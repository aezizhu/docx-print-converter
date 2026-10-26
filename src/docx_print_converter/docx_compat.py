"""Materialize declared East Asian theme fonts, without reformatting a DOCX.

ONLYOFFICE currently loses supplemental theme script fonts (upstream issue 1415).
This workaround resolves only that indirection in a temporary conversion copy.
The parser locates attributes by namespace; byte-span replacement preserves all
unrelated XML, including prefix declarations used inside mc:Ignorable values.
"""
import copy
import os
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit
from xml.parsers import expat
from xml.sax.saxutils import quoteattr
from zipfile import ZipFile

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_XML_TOTAL = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
SEP = '\x1f'
SCRIPT = {'zh-cn': 'Hans', 'zh-sg': 'Hans', 'zh-hans': 'Hans',
          'zh-tw': 'Hant', 'zh-hk': 'Hant', 'zh-mo': 'Hant', 'zh-hant': 'Hant',
          'ja-jp': 'Jpan', 'ko-kr': 'Hang'}


class _Skip(ValueError):
    pass


def _read_xml(archive, name):
    from .archive import _xml
    entry = archive.getinfo(name)
    if entry.file_size > MAX_XML_BYTES:
        raise _Skip('inspection_limit')
    raw = archive.read(entry)
    return raw, _xml(raw)


def _relationship_target(archive, base, kind):
    rels = (posixpath.join(posixpath.dirname(base), '_rels', posixpath.basename(base) + '.rels')
            if base else '_rels/.rels')
    _, root = _read_xml(archive, rels)
    matches = [e for e in root.findall('{%s}Relationship' % R) if e.get('Type') == REL + kind]
    if len(matches) != 1 or matches[0].get('TargetMode', 'Internal') != 'Internal':
        raise _Skip('missing_or_ambiguous_relationship')
    target = unquote(matches[0].get('Target', ''))
    url = urlsplit(target)
    if not target or url.scheme or url.netloc or url.query or url.fragment or '\\' in target:
        raise _Skip('unsafe_relationship')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(base), target))
    name = name.lstrip('/') if target.startswith('/') else name
    if name in ('.', '..') or name.startswith('../') or name not in archive.namelist():
        raise _Skip('unsafe_relationship')
    return name


def _resolved_fonts(theme, language):
    schemes = theme.findall('./{%s}themeElements/{%s}fontScheme' % (A, A))
    if len(schemes) != 1:
        raise _Skip('missing_or_ambiguous_theme')
    result = {}
    script = SCRIPT.get(language.lower())
    for family in ('major', 'minor'):
        collections = schemes[0].findall('{%s}%sFont' % (A, family))
        if len(collections) != 1:
            continue
        faces = collections[0].findall('{%s}ea' % A)
        if len(faces) != 1:
            continue
        face = faces[0].get('typeface', '')
        if not face and script:
            names = {e.get('typeface', '') for e in collections[0].findall('{%s}font' % A)
                     if e.get('script') == script}
            if len(names) == 1:
                face = names.pop()
        if face and len(face) <= 256:
            result[family + 'EastAsia'] = face
    return result


def _expanded(name):
    pieces = name.split(SEP)
    return pieces[0:2], (pieces[2] + ':' + pieces[1] if len(pieces) == 3 else pieces[-1])


def _tag_end(raw, start):
    quote = None
    for i in range(start, len(raw)):
        c = raw[i]
        if quote:
            if c == quote:
                quote = None
        elif c in (34, 39):
            quote = c
        elif c == 62:
            return i + 1
    raise _Skip('malformed_xml')


def _attribute_pattern(name):
    return re.compile(rb'\s+' + re.escape(name.encode('utf-8')) + rb'\s*=\s*([\'"])(.*?)\1', re.S)


def _transform_xml(raw, fonts):
    from .archive import _xml
    # The byte replacement intentionally supports UTF-8 only. Other encodings
    # remain untouched rather than risking offsets or changing the XML encoding.
    if b'\x00' in raw or raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise _Skip('unsupported_xml_encoding')
    declaration = re.match(rb'\xef\xbb\xbf?\s*<\?xml[^?]*\?>|\s*<\?xml[^?]*\?>', raw)
    if declaration:
        encoding = re.search(rb'encoding\s*=\s*[\'"]([^\'"]+)', declaration.group(), re.I)
        if encoding and encoding.group(1).lower() not in (b'utf-8', b'utf8', b'us-ascii'):
            raise _Skip('unsupported_xml_encoding')
    # Entity/declaration safety is checked before Expat callbacks are installed.
    _xml(raw)
    parser = expat.ParserCreate(namespace_separator=SEP)
    parser.namespace_prefixes = True
    patches = []

    def start(name, attrs):
        if _expanded(name)[0] != [W, 'rFonts']:
            return
        relevant = {_expanded(k)[0][1]: (_expanded(k)[1], value)
                    for k, value in attrs.items() if _expanded(k)[0][0] == W}
        theme = relevant.get('eastAsiaTheme')
        if not theme or theme[1] not in fonts:
            return
        begin = parser.CurrentByteIndex
        end = _tag_end(raw, begin)
        tag = raw[begin:end]
        if 'eastAsia' in relevant:
            tag, count = _attribute_pattern(relevant['eastAsia'][0]).subn(b'', tag)
            if count != 1:
                raise _Skip('attribute_span_mismatch')
        qname = theme[0].rsplit(':', 1)[0] + ':eastAsia'
        replacement = (' ' + qname + '=' + quoteattr(fonts[theme[1]])).encode('utf-8')
        tag, count = _attribute_pattern(theme[0]).subn(lambda _: replacement, tag)
        if count != 1:
            raise _Skip('attribute_span_mismatch')
        patches.append((begin, end, tag))

    parser.StartElementHandler = start
    parser.Parse(raw, True)
    pieces = []
    previous = 0
    for begin, end, tag in patches:
        pieces.extend((raw[previous:begin], tag))
        previous = end
    pieces.append(raw[previous:])
    return b''.join(pieces), len(patches)


def materialize_east_asia_theme(src, workdir):
    """Return ``(path, metadata)``; caller owns cleanup of a returned temp copy.

    Unresolved, malformed or oversized packages return the original path. No
    changed copy is emitted unless every relevant part can be inspected safely.
    No paragraph, image, layout, font file or page-count modification is made.
    """
    # Lazy import allows office_conversion to import this helper without a cycle.
    from .archive import _bounded_archive
    src = Path(src)
    metadata = {'changed': False, 'replacements': 0, 'parts': [], 'reason': 'no_changes'}
    if src.suffix.lower() != '.docx':
        metadata['reason'] = 'not_docx'
        return src, metadata
    output = None
    try:
        with _bounded_archive(src) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if (len(names) != len(set(names)) or any(len(n) > 1024 for n in names)
                    or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES):
                raise _Skip('inspection_limit')
            main = _relationship_target(archive, '', 'officeDocument')
            theme = _relationship_target(archive, main, 'theme')
            _, theme_root = _read_xml(archive, theme)
            language = ''
            try:
                settings = _relationship_target(archive, main, 'settings')
            except _Skip:
                settings = None
            if settings:
                _, settings_root = _read_xml(archive, settings)
                langs = settings_root.findall('{%s}themeFontLang' % W)
                if len(langs) == 1:
                    language = langs[0].get('{%s}eastAsia' % W, '')
            fonts = _resolved_fonts(theme_root, language)
            if not fonts:
                metadata['reason'] = 'unresolved_theme_fonts'
                return src, metadata
            candidates = [e for e in entries if posixpath.dirname(e.filename) == posixpath.dirname(main)
                          and e.filename.endswith('.xml') and e.filename != theme]
            if sum(e.file_size for e in candidates) > MAX_XML_TOTAL:
                raise _Skip('inspection_limit')
            changes = {}
            for entry in candidates:
                raw, _ = _read_xml(archive, entry.filename)
                changed, count = _transform_xml(raw, fonts)
                if count:
                    changes[entry.filename] = changed
                    metadata['parts'].append({'part': entry.filename, 'replacements': count})
                    metadata['replacements'] += count
            if not changes:
                return src, metadata
            fd, name = tempfile.mkstemp(prefix='docx-theme-', suffix='.docx', dir=workdir)
            os.close(fd)
            output = Path(name)
            with ZipFile(output, 'w') as target:
                target.comment = archive.comment
                for entry in entries:
                    if entry.filename in changes:
                        target.writestr(copy.copy(entry), changes[entry.filename])
                    else:
                        with archive.open(entry) as reader, target.open(copy.copy(entry), 'w') as writer:
                            shutil.copyfileobj(reader, writer, 1024 * 1024)
            metadata.update(changed=True, reason='resolved_east_asia_theme')
            return output, metadata
    except Exception as exc:
        if output is not None:
            output.unlink(missing_ok=True)
        metadata = {'changed': False, 'replacements': 0, 'parts': [],
                    'reason': str(exc) if isinstance(exc, _Skip) else 'invalid_or_unreadable_package'}
        return src, metadata
