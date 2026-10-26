"""Resolve document-grid height for empty body paragraphs in a conversion copy.

Word/GDI ratios are numeric metrics from the open-source docx4j conversion
catalog (word-line-metrics.properties), not redistributed proprietary fonts.
Only empty, auto-spaced paragraphs with known mark-font metrics are changed.
"""
import copy
from fractions import Fraction
import math
import os
from pathlib import Path
import shutil
import tempfile
from xml.parsers import expat
from zipfile import ZipFile

from .docx_compat import (W, SEP, MAX_EXPANDED_BYTES, _Skip, _read_xml,
                         _relationship_target, _tag_end, _attribute_pattern)

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
V = 'urn:schemas-microsoft-com:vml'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
Q = '{' + W + '}'
# East Asian families use 1.3 * the Win ascent/descent box; others include
# external leading. Do not infer unknown families from a substituted font.
METRICS = {
    'simhei': Fraction(13, 10), '黑体': Fraction(13, 10),
    'simsun': Fraction(13, 10), '宋体': Fraction(13, 10),
    'calibri': Fraction(2500, 2048), 'calibri light': Fraction(2500, 2048),
    'arial': Fraction(2355, 2048), 'times new roman': Fraction(2355, 2048),
    'microsoft yahei': Fraction(13 * 2703, 10 * 2048),
    '微软雅黑': Fraction(13 * 2703, 10 * 2048),
}
CONTENT = {Q + x for x in ('br', 'cr', 'tab', 'fldChar', 'fldSimple', 'instrText',
    'delText', 'sym', 'object', 'footnoteReference', 'endnoteReference',
    'noBreakHyphen', 'softHyphen', 'lastRenderedPageBreak', 'txbxContent')}
AFTER_SPACING = {'ind', 'contextualSpacing', 'mirrorIndents', 'suppressOverlap',
    'jc', 'textDirection', 'textAlignment', 'textboxTightWrap', 'outlineLvl',
    'divId', 'cnfStyle', 'rPr', 'sectPr', 'pPrChange'}


def _empty(paragraph):
    for node in paragraph.iter():
        if node.tag in CONTENT or node.tag.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/math}'):
            return False
        if node.tag == Q + 't' and node.text:
            return False
        if node.tag == '{' + WP + '}inline':
            return False
        if node.tag == Q + 'drawing' and node.find('{' + WP + '}anchor') is None:
            return False
        if node.tag == Q + 'pict':
            shapes = list(node.iter('{' + V + '}shape')) + list(node.iter('{' + V + '}group'))
            if not shapes or any('position:absolute' not in x.get('style', '').replace(' ', '').lower() for x in shapes):
                return False
    return True


def _bool(element):
    value = element.get(Q + 'val', '1').lower()
    if value in ('0', 'false', 'off'): return False
    if value in ('1', 'true', 'on'): return True
    raise _Skip('unknown_boolean')


def _effective(paragraph, styles_root):
    styles = {s.get(Q + 'styleId'): s for s in styles_root.findall(Q + 'style')}
    defaults = [s.get(Q + 'styleId') for s in styles_root.findall(Q + 'style')
                if s.get(Q + 'type') == 'paragraph' and s.get(Q + 'default') in ('1', 'true', 'on')]
    pr = paragraph.find(Q + 'pPr')
    sid = pr.find(Q + 'pStyle') if pr is not None else None
    name = sid.get(Q + 'val') if sid is not None else (defaults[0] if len(defaults) == 1 else None)
    chain, seen = [], set()
    while name:
        if name in seen or name not in styles or len(chain) >= 32:
            raise _Skip('unresolved_style')
        seen.add(name); style = styles[name]; chain.append(style)
        parent = style.find(Q + 'basedOn')
        name = parent.get(Q + 'val') if parent is not None else None
    result = {'spacing': {}, 'snap': True, 'font': None, 'theme': None, 'size': None, 'numbered': False}

    def run(rpr):
        if rpr is None: return
        if rpr.find(Q + 'rStyle') is not None:
            raise _Skip('character_style_on_mark')
        for tag in ('rtl', 'cs'):
            flag = rpr.find(Q + tag)
            if flag is not None and _bool(flag): raise _Skip('complex_script_mark')
        fonts = rpr.find(Q + 'rFonts')
        if fonts is not None:
            if fonts.get(Q + 'asciiTheme'):
                result['theme'], result['font'] = fonts.get(Q + 'asciiTheme'), None
            elif fonts.get(Q + 'ascii'):
                result['font'], result['theme'] = fonts.get(Q + 'ascii'), None
        size = rpr.find(Q + 'sz')
        if size is not None: result['size'] = size.get(Q + 'val')

    def props(ppr):
        if ppr is None: return
        snap = ppr.find(Q + 'snapToGrid')
        if snap is not None: result['snap'] = _bool(snap)
        numbering = ppr.find(Q + 'numPr')
        if numbering is not None:
            number_id = numbering.find(Q + 'numId')
            result['numbered'] = number_id is None or number_id.get(Q + 'val') != '0'
        spacing = ppr.find(Q + 'spacing')
        if spacing is not None: result['spacing'].update(spacing.attrib)

    dd = styles_root.find(Q + 'docDefaults')
    if dd is not None:
        props(dd.find(Q + 'pPrDefault/' + Q + 'pPr'))
        run(dd.find(Q + 'rPrDefault/' + Q + 'rPr'))
    for style in reversed(chain):
        props(style.find(Q + 'pPr')); run(style.find(Q + 'rPr'))
    props(pr)
    if pr is not None: run(pr.find(Q + 'rPr'))
    return result


def _section_grids(body):
    groups, pending, index = [], [], 0
    for child in body:
        section = None
        if child.tag == Q + 'p':
            pending.append((index, child)); index += 1
            section = child.find(Q + 'pPr/' + Q + 'sectPr')
        elif child.tag == Q + 'sectPr': section = child
        if section is not None:
            grid = section.find(Q + 'docGrid')
            groups.extend((i, p, grid) for i, p in pending)
            pending = []
    return groups


def _ascii_theme(archive, main, name):
    if name not in ('majorAscii', 'majorHAnsi', 'minorAscii', 'minorHAnsi'):
        raise _Skip('unresolved_mark_theme')
    theme = _relationship_target(archive, main, 'theme')
    _, root = _read_xml(archive, theme)
    try:
        settings = _relationship_target(archive, main, 'settings')
    except _Skip:
        settings = None
    if settings:
        _, sr = _read_xml(archive, settings)
        lang = sr.find(Q + 'themeFontLang')
        value = lang.get(Q + 'val', '') if lang is not None else ''
        if value and not value.lower().startswith('en-'):
            raise _Skip('unresolved_mark_theme_language')
    family = 'major' if name.startswith('major') else 'minor'
    nodes = root.findall('./{%s}themeElements/{%s}fontScheme/{%s}%sFont/{%s}latin' % (A, A, A, family, A))
    if len(nodes) != 1 or not nodes[0].get('typeface'):
        raise _Skip('unresolved_mark_theme')
    return nodes[0].get('typeface')


def _local(name):
    pieces = name.split(SEP)
    return (pieces[0], pieces[1]) if len(pieces) > 1 else ('', name)


def _qname(name):
    parts = name.split(SEP)
    return parts[2] + ':' + parts[1] if len(parts) == 3 else parts[-1]


def _patch_xml(raw, wanted):
    from .archive import _xml
    if b'\x00' in raw:
        raise _Skip('unsupported_xml_encoding')
    _xml(raw)
    parser = expat.ParserCreate(namespace_separator=SEP)
    parser.namespace_prefixes = True
    stack, spans = [], {}
    current, number = None, -1

    def start(name, attrs):
        nonlocal current, number
        tag = _local(name)
        begin = parser.CurrentByteIndex
        if tag == (W, 'p') and len(stack) == 2 and stack[-1] == (W, 'body'):
            number += 1
            current = {'start': begin, 'open_end': _tag_end(raw, begin), 'qname': _qname(name)} if number in wanted else None
            if current is not None: spans[number] = current
        elif current is not None and tag == (W, 'pPr') and len(stack) == 3:
            current['pr'] = {'start': begin, 'open_end': _tag_end(raw, begin), 'qname': _qname(name)}
        elif current is not None and len(stack) == 4 and stack[-1] == (W, 'pPr'):
            if tag == (W, 'spacing'):
                current['spacing'] = (begin, _tag_end(raw, begin), _qname(name), attrs)
            elif tag[0] == W and tag[1] in AFTER_SPACING:
                current.setdefault('insert', begin)
        stack.append(tag)

    def end(name):
        nonlocal current
        if current is not None and len(stack) == 4 and stack[-1] == (W, 'pPr'):
            current['pr']['close'] = parser.CurrentByteIndex
        if len(stack) == 3 and stack[-1] == (W, 'p'):
            current = None
        stack.pop()

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.Parse(raw, True)
    edits = []
    for index, line in wanted.items():
        span = spans[index]
        prefix = span['qname'].split(':')[0] if ':' in span['qname'] else 'grid'
        ns = '' if ':' in span['qname'] else ' xmlns:grid="' + W + '"'
        node = ('<%s:spacing%s %s:line="%d" %s:lineRule="exact"/>' % (prefix, ns, prefix, line, prefix)).encode()
        if 'spacing' in span:
            begin, finish, qname, attrs = span['spacing']; tag = raw[begin:finish]
            for key in attrs:
                if _local(key) in ((W, 'line'), (W, 'lineRule')):
                    tag, count = _attribute_pattern(_qname(key)).subn(b'', tag)
                    if count != 1: raise _Skip('attribute_span_mismatch')
            insertion = len(tag) - (2 if tag.endswith(b'/>') else 1)
            extra = ('%s %s:line="%d" %s:lineRule="exact"' % (ns, prefix, line, prefix)).encode()
            edits.append((begin, finish, tag[:insertion] + extra + tag[insertion:]))
        elif 'pr' in span:
            pr = span['pr']; tag = raw[pr['start']:pr['open_end']]
            if tag.endswith(b'/>'):
                edits.append((pr['start'], pr['open_end'], tag[:-2] + b'>' + node + ('</'+pr['qname']+'>').encode()))
            else:
                point = span.get('insert', pr['close']); edits.append((point, point, node))
        else:
            tag = raw[span['start']:span['open_end']]
            props = ('<%s:pPr%s>' % (prefix, ns)).encode() + node + ('</%s:pPr>' % prefix).encode()
            if tag.endswith(b'/>'):
                edits.append((span['start'], span['open_end'], tag[:-2] + b'>' + props + ('</'+span['qname']+'>').encode()))
            else: edits.append((span['open_end'], span['open_end'], props))
    for begin, finish, replacement in sorted(edits, reverse=True):
        raw = raw[:begin] + replacement + raw[finish:]
    _xml(raw)
    return raw


def normalize_empty_grid_paragraphs(src, workdir, metrics_provider=None):
    """Return (path, metadata); caller removes any returned temporary DOCX.

    Unknown metrics, unresolved styles and nonempty/explicitly spaced paragraphs
    are left untouched. The optional provider returns a natural line-height/size
    ratio for an exact requested family, or None when unknown.
    """
    from .archive import _bounded_archive
    import xml.etree.ElementTree as ET
    src = Path(src)
    meta = {'changed': False, 'replacements': 0, 'paragraphs': [], 'skipped': {}, 'reason': 'no_changes'}
    if src.suffix.lower() != '.docx': return src, meta
    output = None
    try:
        with _bounded_archive(src) as archive:
            entries = archive.infolist(); names = [x.filename for x in entries]
            if len(names) != len(set(names)) or sum(x.file_size for x in entries) > MAX_EXPANDED_BYTES:
                raise _Skip('inspection_limit')
            main = _relationship_target(archive, '', 'officeDocument')
            raw, root = _read_xml(archive, main)
            body = root.find(Q + 'body')
            if body is None: raise _Skip('no_document_body')
            try: styles_name = _relationship_target(archive, main, 'styles')
            except _Skip: styles_name = None
            styles = _read_xml(archive, styles_name)[1] if styles_name else ET.Element(Q+'styles')
            wanted = {}
            for index, paragraph, grid in _section_grids(body):
                if grid is None or grid.get(Q+'type') not in ('lines', 'linesAndChars') or not _empty(paragraph): continue
                try:
                    pitch = int(grid.get(Q+'linePitch', '0'))
                    if not 1 <= pitch <= 31680: raise _Skip('unknown_grid_pitch')
                    effective = _effective(paragraph, styles)
                    if not effective['snap'] or effective['numbered']: continue
                    spacing = effective['spacing']
                    if spacing.get(Q+'lineRule', 'auto') != 'auto': continue
                    font = effective['font']
                    if effective['theme']: font = _ascii_theme(archive, main, effective['theme'])
                    if not font: raise _Skip('unknown_mark_font')
                    ratio = metrics_provider(font) if metrics_provider else METRICS.get(font.strip().lower())
                    if ratio is None: raise _Skip('unknown_mark_metrics')
                    ratio = Fraction(str(ratio))
                    size = int(effective['size'] or '0')
                    auto = int(spacing.get(Q+'line', '240'))
                    if not 1 <= size <= 1638 or not 1 <= auto <= 2400 or not 0 < ratio <= 5:
                        raise _Skip('unknown_line_metrics')
                    raw_height = size * 10 * ratio * Fraction(auto, 240)
                    height = math.ceil(raw_height / pitch) * pitch
                    wanted[index] = height
                    meta['paragraphs'].append({'index': index, 'font': font, 'font_size_half_points': size,
                                               'grid_twips': pitch, 'line_twips': height})
                except (ValueError, TypeError) as exc:
                    reason = str(exc) if isinstance(exc, _Skip) else 'unknown_line_metrics'
                    meta['skipped'][reason] = meta['skipped'].get(reason, 0) + 1
            if not wanted: return src, meta
            changed = _patch_xml(raw, wanted)
            fd, name = tempfile.mkstemp(prefix='docx-grid-', suffix='.docx', dir=workdir)
            os.close(fd); output = Path(name)
            with ZipFile(output, 'w') as target:
                target.comment = archive.comment
                for entry in entries:
                    if entry.filename == main: target.writestr(copy.copy(entry), changed)
                    else:
                        with archive.open(entry) as reader, target.open(copy.copy(entry), 'w') as writer:
                            shutil.copyfileobj(reader, writer, 1024*1024)
            meta.update(changed=True, replacements=len(wanted), reason='resolved_empty_paragraph_grid')
            return output, meta
    except Exception as exc:
        if output is not None: output.unlink(missing_ok=True)
        return src, {'changed': False, 'replacements': 0, 'paragraphs': [], 'skipped': {},
                     'reason': str(exc) if isinstance(exc, _Skip) else 'invalid_or_unreadable_package'}
