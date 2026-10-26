#!/usr/bin/env python3
"""Add explicit open-font aliases to pinned ONLYOFFICE v2 indexes.

Source format: ONLYOFFICE/core DesktopEditor/fontengine/ApplicationFonts.cpp
and ApplicationFontsWorker.cpp. Trailing ranges use NUL-terminated UTF-8 names
(CByteBuilder/CByteReader in core ed63e94de814ff1d4ea28cddfaba6939bc9df9ed,
DocumentServer v10.0.0). Used at image build time against the pinned converter bundle.
Existing .before-alias backups cause refusal, making accidental reapplication safe.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile

ALIASES = {
    'AR PL UMing CN': ('宋体', 'SimSun'),
    'WenQuanYi Zen Hei': ('黑体', 'SimHei', '微软雅黑', 'Microsoft YaHei'),
}
MAX_BYTES = 32 * 1024 * 1024
VERSION_RE = re.compile(rb'window\["__all_fonts_js_version__"\]\s*=\s*(\d+)\s*;')
BLOB_RE = re.compile(rb'(window\["g_fonts_selection_bin"\]\s*=\s*")([A-Za-z0-9+/=]+)("\s*;)')


def u32(data, pos, end):
    if pos < 0 or pos + 4 > end:
        raise ValueError('Truncated integer in font index')
    return struct.unpack_from('<I', data, pos)[0], pos + 4


def string(data, pos, end, limit=16384):
    size, pos = u32(data, pos, end)
    if size > limit or pos + size > end:
        raise ValueError('Invalid or oversized string in font index')
    raw = data[pos:pos + size]
    value = raw.decode('utf-8', 'strict')
    if '\x00' in value:
        raise ValueError('Unexpected NUL in v2 font name/path')
    return value, pos + size


def pack_string(value):
    value = value.encode('utf-8')
    return struct.pack('<I', len(value)) + value


def parse_index(data):
    if not 4 <= len(data) <= MAX_BYTES:
        raise ValueError('Font index size is unsupported')
    count, pos = u32(data, 0, len(data))
    if not 1 <= count <= 10000:
        raise ValueError('Font record count is unsupported')
    records = []
    for _ in range(count):
        start = pos
        size, pos = u32(data, pos, len(data))
        end = start + size
        if size < 96 or end > len(data):
            raise ValueError('Invalid v2 record length')
        name, pos = string(data, pos, end)
        count_pos = pos
        n_aliases, pos = u32(data, pos, end)
        if n_aliases > 256:
            raise ValueError('Too many alternate names')
        names = []
        for _ in range(n_aliases):
            value, pos = string(data, pos, end)
            names.append(value)
        remainder_pos = pos
        path, pos = string(data, pos, end)
        # v2: face index + three flags + panose length/data + ranges + metrics/type.
        if not name or not path or pos + 76 != end:
            raise ValueError('Record does not match supported v2 field layout')
        panose_len, _ = u32(data, pos + 16, end)
        if panose_len != 10:
            raise ValueError('Unsupported PANOSE field length')
        records.append({'name': name, 'aliases': names, 'start': start, 'end': end,
                        'count_pos': count_pos, 'remainder_pos': remainder_pos})
        pos = end
    records_end = pos
    if pos != len(data):
        ranges, pos = u32(data, pos, len(data))
        if ranges > 100000:
            raise ValueError('Range count is unsupported')
        for _ in range(ranges):
            # Unlike record strings, CByteBuilder::WriteStringUTF8 writes a
            # NUL-terminated string here, not a length-prefixed string.
            zero = data.find(b'\x00', pos, min(len(data), pos + 16385))
            if zero < 0 or zero == pos:
                raise ValueError('Invalid NUL-terminated range font name')
            data[pos:zero].decode('utf-8', 'strict')
            pos = zero + 1
            first, pos = u32(data, pos, len(data))
            last, pos = u32(data, pos, len(data))
            if first > last or last > 0x10ffff:
                raise ValueError('Invalid Unicode range')
        if pos != len(data):
            raise ValueError('Unrecognized trailing index data')
    return records, records_end


def patch_index(data):
    records, end = parse_index(data)
    present = {r['name'] for r in records}
    missing = set(ALIASES) - present
    if missing:
        raise ValueError('Required font families missing: ' + ', '.join(sorted(missing)))
    for target, aliases in ALIASES.items():
        for record in records:
            if record['name'] != target and set(aliases) & ({record['name']} | set(record['aliases'])):
                raise ValueError('Requested alias already belongs to a different font family')
    result = [data[:4]]
    changes = {}
    for record in records:
        name = record['name']
        additions = [a for a in ALIASES.get(name, ()) if a not in record['aliases']]
        if additions:
            aliases = record['aliases'] + additions
            body = (data[record['start'] + 4:record['count_pos']]
                    + struct.pack('<I', len(aliases))
                    + b''.join(pack_string(a) for a in aliases)
                    + data[record['remainder_pos']:record['end']])
            result.append(struct.pack('<I', len(body) + 4) + body)
            changes[name] = changes.get(name, 0) + 1
        else:
            result.append(data[record['start']:record['end']])
    result.append(data[end:])
    patched = b''.join(result)
    readback, patched_end = parse_index(patched)
    assert data[end:] == patched[patched_end:], 'Trailing range data changed'
    for old, new in zip(records, readback):
        assert old['name'] == new['name']
        assert data[old['remainder_pos']:old['end']] == patched[new['remainder_pos']:new['end']], 'Font fields changed'
        assert set(ALIASES.get(new['name'], ())) <= set(new['aliases']), 'Alias readback failed'
    return patched, changes


def atomic_write(path, data, mode):
    fd, temp = tempfile.mkstemp(prefix=path.name + '.alias-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def patch_directory(directory):
    paths = [directory / 'font_selection.bin', directory / 'AllFonts.js']
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('Expected regular, bounded index file: ' + str(path))
    originals = [p.read_bytes() for p in paths]
    versions = VERSION_RE.findall(originals[1])
    matches = list(BLOB_RE.finditer(originals[1]))
    if versions != [b'2'] or len(matches) != 1:
        raise ValueError('Expected exactly one v2 declaration and one base64 font index')
    match = matches[0]
    js_index = base64.b64decode(match.group(2), validate=True)
    _, native_end = parse_index(originals[0])
    _, js_end = parse_index(js_index)
    if js_end != len(js_index) or originals[0][:native_end] != js_index:
        raise ValueError('Native and JavaScript font records do not match exactly')
    native_new, changes = patch_index(originals[0])
    js_new, js_changes = patch_index(js_index)
    if changes != js_changes or not changes:
        raise ValueError('No matching new aliases to add, or indexes disagree')
    patched_js = originals[1][:match.start(2)] + base64.b64encode(js_new) + originals[1][match.end(2):]
    replacements = [native_new, patched_js]
    backups = [p.with_name(p.name + '.before-alias') for p in paths]
    if any(p.exists() for p in backups):
        raise ValueError('Backup already exists; restore both indexes before re-running')
    modes = [stat.S_IMODE(p.stat().st_mode) for p in paths]
    for path, backup, original in zip(paths, backups, originals):
        with backup.open('xb') as output:
            output.write(original)
        shutil.copystat(path, backup)
    if [p.read_bytes() for p in paths] != originals:
        raise ValueError('Index changed concurrently; nothing replaced')
    try:
        for path, data, mode in zip(paths, replacements, modes):
            atomic_write(path, data, mode)
        if [p.read_bytes() for p in paths] != replacements:
            raise ValueError('Written index readback differs')
    except Exception:
        for path, original, mode in zip(paths, originals, modes):
            atomic_write(path, original, mode)
        raise
    for family, count in changes.items():
        print(json.dumps({'family': family, 'records_updated': count,
                          'aliases': ALIASES[family]}, ensure_ascii=False))
    print('Verified: both indexes patched; original font fields and trailing ranges preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin_dir', type=Path)
    args = parser.parse_args()
    patch_directory(args.bin_dir.resolve())


if __name__ == '__main__':
    main()
