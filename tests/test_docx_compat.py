"""Theme-font resolution must preserve every unrelated DOCX byte and property."""
import importlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

import pytest

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


@pytest.fixture
def compat():
    return importlib.import_module("docx_print_converter.docx_compat")


def package(tmp_path, lang='zh-CN', ea='', extra=None, theme_target='theme/theme1.xml'):
    body = (f'<w:document xmlns:w="{W}"><w:body><w:p><w:pPr><w:spacing w:line="276" '
            'w:lineRule="auto"/></w:pPr><w:r><w:t>Original text</w:t></w:r></w:p>'
            '<w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    styles = (f'<w:styles xmlns:w="{W}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
              'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">'
              '<w:style w:styleId="Normal"><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" '
              'w:eastAsiaTheme="minorEastAsia"/><w:sz w:val="24"/></w:rPr></w:style></w:styles>')
    settings = f'<w:settings xmlns:w="{W}"><w:themeFontLang w:eastAsia="{lang}"/></w:settings>'
    theme = (f'<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme><a:majorFont>'
             '<a:ea typeface="Major Face"/></a:majorFont><a:minorFont>'
             f'<a:ea typeface="{ea}"/><a:font script="Hans" typeface="SimSun"/>'
             '<a:font script="Hant" typeface="MingLiU"/><a:font script="Jpan" typeface="Yu Mincho"/>'
             '<a:font script="Hang" typeface="Malgun Gothic"/></a:minorFont>'
             '</a:fontScheme></a:themeElements></a:theme>')
    entries = {
        '_rels/.rels': f'<Relationships xmlns="{R}"><Relationship Id="r0" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="{REL}theme" Target="{theme_target}"/><Relationship Id="r2" Type="{REL}settings" Target="settings.xml"/></Relationships>',
        'word/document.xml': body, 'word/styles.xml': styles,
        'word/settings.xml': settings, 'word/theme/theme1.xml': theme,
        'word/media/seal.png': b'unchanged image bytes',
        'docProps/app.xml': b'<Properties><Pages>1</Pages></Properties>',
        '[Content_Types].xml': b'<Types/>',
    }
    if extra:
        entries.update(extra)
    path = tmp_path / 'source.docx'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.comment = b'preserve archive comment'
        for name, value in entries.items():
            z.writestr(name, value)
    return path


def read_parts(path):
    with ZipFile(path) as z:
        return {name: z.read(name) for name in z.namelist()}


def test_resolves_theme_with_only_attribute_bytes_changed(compat, tmp_path):
    src = package(tmp_path)
    original = src.read_bytes()
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert output != src and output.is_file()
    assert src.read_bytes() == original
    before, after = read_parts(src), read_parts(output)
    assert after['word/styles.xml'] == before['word/styles.xml'].replace(
        b'w:eastAsiaTheme="minorEastAsia"', b'w:eastAsia="SimSun"')
    assert {k: v for k, v in before.items() if k != 'word/styles.xml'} == {
        k: v for k, v in after.items() if k != 'word/styles.xml'}
    assert meta['changed'] is True and meta['replacements'] == 1
    with ZipFile(output) as z:
        assert z.comment == b'preserve archive comment'
    ET.fromstring(after['word/styles.xml'])


@pytest.mark.parametrize('lang,font', [('zh-CN', 'SimSun'), ('zh-SG', 'SimSun'),
    ('zh-TW', 'MingLiU'), ('zh-HK', 'MingLiU'), ('zh-MO', 'MingLiU'),
    ('ja-JP', 'Yu Mincho'), ('ko-KR', 'Malgun Gothic')])
def test_declared_language_selects_supplemental_script(compat, tmp_path, lang, font):
    output, meta = compat.materialize_east_asia_theme(package(tmp_path, lang=lang), tmp_path)
    assert meta['changed']
    assert ('w:eastAsia="' + font + '"').encode() in read_parts(output)['word/styles.xml']


@pytest.mark.parametrize('lang', ['', 'en-US', 'zh', 'und'])
def test_ambiguous_language_is_unchanged(compat, tmp_path, lang):
    src = package(tmp_path, lang=lang)
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert output == src and not meta['changed']
    assert list(tmp_path.iterdir()) == [src]


def test_explicit_theme_east_asian_face_does_not_need_language(compat, tmp_path):
    output, meta = compat.materialize_east_asia_theme(package(tmp_path, lang='', ea='Theme Face'), tmp_path)
    assert meta['changed']
    assert b'w:eastAsia="Theme Face"' in read_parts(output)['word/styles.xml']


def test_resolves_actual_theme_relationship(compat, tmp_path):
    src = package(tmp_path, theme_target='../assets/custom.xml')
    parts = read_parts(src)
    with ZipFile(src, 'w', ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr('assets/custom.xml' if name == 'word/theme/theme1.xml' else name, data)
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert meta['changed'] and b'w:eastAsia="SimSun"' in read_parts(output)['word/styles.xml']


def test_header_with_different_namespace_prefix_and_existing_face(compat, tmp_path):
    header = f"<q:hdr xmlns:q='{W}'><q:p><q:r><q:rPr><q:rFonts q:eastAsia='Old' q:ascii='Keep' q:eastAsiaTheme='majorEastAsia'/></q:rPr><q:t>HEADER</q:t></q:r></q:p></q:hdr>"
    src = package(tmp_path, extra={'word/header1.xml': header})
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    result = ET.fromstring(read_parts(output)['word/header1.xml'])
    attrs = result.find('.//{%s}rFonts' % W).attrib
    assert attrs == {'{%s}eastAsia' % W: 'Major Face', '{%s}ascii' % W: 'Keep'}
    assert meta['replacements'] == 2


@pytest.mark.parametrize('part,value', [
    ('word/theme/theme1.xml', b'<broken'),
    ('word/settings.xml', b'<?xml version="1.0" encoding="made-up"?><x/>'),
    ('word/styles.xml', b'<!DOCTYPE x [<!ENTITY foo "bar">]><x/>'),
    ('word/document.xml', b'<broken'),
])
def test_malformed_xml_does_not_break_conversion_or_emit_copy(compat, tmp_path, part, value):
    src = package(tmp_path, extra={part: value})
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert output == src and not meta['changed']
    assert list(tmp_path.iterdir()) == [src]


def test_external_theme_is_not_followed(compat, tmp_path):
    rels = f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="{REL}theme" Target="https://example.test/font.xml" TargetMode="External"/></Relationships>'
    src = package(tmp_path, extra={'word/_rels/document.xml.rels': rels})
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert output == src and not meta['changed']


def test_large_xml_is_not_parsed(compat, tmp_path, monkeypatch):
    src = package(tmp_path)
    monkeypatch.setattr(compat, 'MAX_XML_BYTES', 32)
    output, meta = compat.materialize_east_asia_theme(src, tmp_path)
    assert output == src and not meta['changed']


def test_no_second_rewrite(compat, tmp_path):
    first, meta = compat.materialize_east_asia_theme(package(tmp_path), tmp_path)
    assert meta['changed']
    second, meta = compat.materialize_east_asia_theme(first, tmp_path)
    assert second == first and not meta['changed']
