"""Empty paragraph document-grid compatibility must not reformat content."""
import importlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

import pytest

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


@pytest.fixture
def grid():
    return importlib.import_module("docx_print_converter.docx_grid")


def mark(font='SimHei', size='28'):
    return f'<w:rPr><w:rFonts w:ascii="{font}"/><w:sz w:val="{size}"/></w:rPr>'


def sect(pitch='312', kind='lines'):
    return f'<w:sectPr><w:docGrid w:type="{kind}" w:linePitch="{pitch}"/></w:sectPr>'


def package(tmp_path, body=None, styles=None, final=None):
    if body is None:
        body = '<w:p><w:pPr>' + mark() + '</w:pPr></w:p>'
    if styles is None:
        styles = f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault>{mark()}</w:rPrDefault></w:docDefaults></w:styles>'
    xml = (f'<w:document xmlns:w="{W}" xmlns:v="urn:schemas-microsoft-com:vml" '
           'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
           'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
           'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">'
           '<w:body>'+body+(sect() if final is None else final)+'</w:body></w:document>')
    path = tmp_path / 'input.docx'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.comment = b'keep comment'
        z.writestr('_rels/.rels', f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels', f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="{REL}styles" Target="styles.xml"/></Relationships>')
        z.writestr('word/document.xml', xml)
        z.writestr('word/styles.xml', styles)
        z.writestr('word/media/image.png', b'exact picture bytes')
    return path


def xml(path):
    with ZipFile(path) as z:
        return z.read('word/document.xml')


def lines(path):
    root = ET.fromstring(xml(path))
    return [p.find(f'{{{W}}}pPr/{{{W}}}spacing').get(f'{{{W}}}line')
            if p.find(f'{{{W}}}pPr/{{{W}}}spacing') is not None else None
            for p in root.find(f'{{{W}}}body') if p.tag == f'{{{W}}}p']


def test_grid_uses_word_metrics_not_nominal_font_size(grid, tmp_path):
    src = package(tmp_path)
    original = src.read_bytes()
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out != src and meta['replacements'] == 1
    assert lines(out) == ['624']
    assert src.read_bytes() == original
    with ZipFile(src) as a, ZipFile(out) as b:
        assert a.comment == b.comment
        assert all(a.read(n) == b.read(n) for n in a.namelist() if n != 'word/document.xml')
    added = xml(out).replace(b'<w:spacing w:line="624" w:lineRule="exact"/>', b'')
    assert added == xml(src), 'Only inserted spacing bytes may change'


def test_empty_floating_vml_image_allowed_and_shape_bytes_preserved(grid, tmp_path):
    pic = '<w:r><w:pict><v:shape style="position:absolute;margin-top:656pt;height:41.25pt"><v:imagedata/></v:shape></w:pict></w:r>'
    src = package(tmp_path, '<w:p><w:pPr>'+mark()+'</w:pPr>'+pic+'</w:p>')
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert meta['changed'] and lines(out) == ['624']
    assert pic.encode() in xml(out)


@pytest.mark.parametrize('content', [
    '<w:r><w:t>text</w:t></w:r>', '<w:r><w:t> </w:t></w:r>',
    '<w:r><w:br/></w:r>', '<w:r><w:tab/></w:r>',
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
    '<w:fldSimple w:instr="PAGE"/>', '<w:r><w:instrText>PAGE</w:instrText></w:r>',
    '<w:r><w:drawing><wp:inline/></w:drawing></w:r>',
    '<w:r><w:pict><v:shape style="position:relative"/></w:pict></w:r>',
])
def test_content_or_unknown_image_layout_is_untouched(grid, tmp_path, content):
    src = package(tmp_path, '<w:p><w:pPr>'+mark()+'</w:pPr>'+content+'</w:p>')
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out == src and not meta['changed']


@pytest.mark.parametrize('rule', ['exact', 'atLeast'])
def test_explicit_spacing_is_not_overridden(grid, tmp_path, rule):
    src = package(tmp_path, '<w:p><w:pPr><w:spacing w:line="500" w:lineRule="'+rule+'"/>'+mark()+'</w:pPr></w:p>')
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out == src and not meta['changed'] and lines(out) == ['500']


def test_style_chain_and_direct_override(grid, tmp_path):
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:default="1" w:styleId="N">'
              '<w:pPr><w:snapToGrid w:val="0"/></w:pPr>'+mark('Calibri','24')+'</w:style>'
              '<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="N"/>'+mark('SimHei','28')+'</w:style></w:styles>')
    body = '<w:p><w:pPr><w:pStyle w:val="B"/></w:pPr></w:p><w:p><w:pPr><w:pStyle w:val="B"/><w:snapToGrid/></w:pPr></w:p>'
    src = package(tmp_path, body, styles)
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert meta['replacements'] == 1 and lines(out) == [None, '624']


def test_ascii_mark_font_wins_over_east_asia_slot(grid, tmp_path):
    body = '<w:p><w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="SimHei"/><w:sz w:val="24"/></w:rPr></w:pPr></w:p>'
    out, meta = grid.normalize_empty_grid_paragraphs(package(tmp_path, body), tmp_path)
    assert meta['changed'] and lines(out) == ['312']


def test_auto_spacing_and_spacing_attributes_preserved(grid, tmp_path):
    body = '<w:p><w:pPr><w:spacing w:before="33" w:line="480" w:after="44"/>'+mark('Calibri','24')+'</w:pPr></w:p>'
    out, _ = grid.normalize_empty_grid_paragraphs(package(tmp_path, body), tmp_path)
    assert lines(out) == ['624']
    assert b'w:before="33"' in xml(out) and b'w:after="44"' in xml(out)


def test_section_end_applies_to_preceding_paragraphs(grid, tmp_path):
    body = '<w:p/>'+'<w:p><w:pPr>'+sect('400')+'</w:pPr></w:p>'+'<w:p/>'
    out, meta = grid.normalize_empty_grid_paragraphs(package(tmp_path, body, final=sect('200')), tmp_path)
    assert meta['replacements'] == 3 and lines(out) == ['400', '400', '400']


def test_table_paragraphs_are_not_changed(grid, tmp_path):
    table = '<w:tbl><w:tr><w:tc><w:p><w:pPr>'+mark()+'</w:pPr></w:p></w:tc></w:tr></w:tbl>'
    src = package(tmp_path, table+'<w:p/>')
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert meta['replacements'] == 1 and table.encode() in xml(out)


@pytest.mark.parametrize('kind,pitch,font', [('default','312','SimHei'),('snapToChars','312','SimHei'),('lines','0','SimHei'),('lines','312','Unknown Font')])
def test_unsupported_grid_or_font_is_not_guessed(grid, tmp_path, kind, pitch, font):
    src = package(tmp_path,'<w:p><w:pPr>'+mark(font)+'</w:pPr></w:p>',final=sect(pitch,kind))
    out, meta = grid.normalize_empty_grid_paragraphs(src,tmp_path)
    assert out == src and not meta['changed']


def test_prefix_and_self_closing_paragraph_preserved(grid,tmp_path):
    src=package(tmp_path,'<w:p w14:paraId="1234"/>')
    before=xml(src)
    out,meta=grid.normalize_empty_grid_paragraphs(src,tmp_path)
    assert meta['changed'] and lines(out)==['624']
    assert b'mc:Ignorable="w14"' in xml(out) and b'xmlns:w14=' in xml(out)
    assert xml(out).replace(b'<w:p w14:paraId="1234"><w:pPr><w:spacing w:line="624" w:lineRule="exact"/></w:pPr></w:p>',b'<w:p w14:paraId="1234"/>')==before


def test_rerun_is_noop(grid,tmp_path):
    out, _=grid.normalize_empty_grid_paragraphs(package(tmp_path),tmp_path)
    again,meta=grid.normalize_empty_grid_paragraphs(out,tmp_path)
    assert again==out and not meta['changed']


def test_direct_ascii_clears_inherited_theme_slot(grid, tmp_path):
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:default="1" w:styleId="N">'
              '<w:rPr><w:rFonts w:asciiTheme="minorHAnsi"/><w:sz w:val="28"/></w:rPr></w:style></w:styles>')
    src = package(tmp_path, '<w:p><w:pPr><w:rPr><w:rFonts w:ascii="SimHei"/></w:rPr></w:pPr></w:p>', styles)
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert meta['changed'] and lines(out) == ['624']


def test_inherited_exact_spacing_remains_exact(grid, tmp_path):
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:default="1" w:styleId="N">'
              '<w:pPr><w:spacing w:line="500" w:lineRule="exact"/></w:pPr>'+mark()+'</w:style></w:styles>')
    src = package(tmp_path, '<w:p><w:pPr><w:spacing w:before="20"/></w:pPr></w:p>', styles)
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out == src and not meta['changed']


def test_unknown_style_and_cycle_are_not_guessed(grid, tmp_path):
    styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="A"><w:basedOn w:val="A"/>{mark()}</w:style></w:styles>'
    src = package(tmp_path, '<w:p><w:pPr><w:pStyle w:val="A"/></w:pPr></w:p>', styles)
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out == src and meta['skipped']['unresolved_style'] == 1


def test_optional_exact_family_metric_provider(grid, tmp_path):
    src = package(tmp_path, '<w:p><w:pPr>'+mark('Special face','24')+'</w:pPr></w:p>')
    calls=[]
    def metric(font):
        calls.append(font)
        return 1.4
    out, meta = grid.normalize_empty_grid_paragraphs(src,tmp_path,metrics_provider=metric)
    assert calls == ['Special face'] and meta['changed'] and lines(out) == ['624']


def test_alternate_xml_prefix_is_preserved(grid,tmp_path):
    src=package(tmp_path)
    with ZipFile(src) as z: parts={n:z.read(n)for n in z.namelist()}
    original=parts['word/document.xml'].replace(b'xmlns:w=',b'xmlns:q=').replace(b'<w:',b'<q:').replace(b'</w:',b'</q:').replace(b' w:',b' q:')
    parts['word/document.xml']=original
    with ZipFile(src,'w')as z:
        for n,b in parts.items():z.writestr(n,b)
    out,meta=grid.normalize_empty_grid_paragraphs(src,tmp_path)
    assert meta['changed']
    assert xml(out).replace(b'<q:spacing q:line="624" q:lineRule="exact"/>',b'') == original


def test_bad_or_oversized_package_returns_original(grid,tmp_path,monkeypatch):
    src=package(tmp_path)
    monkeypatch.setattr(grid,'MAX_EXPANDED_BYTES',1)
    out,meta=grid.normalize_empty_grid_paragraphs(src,tmp_path)
    assert out == src and meta['reason']=='inspection_limit'
    src.write_bytes(b'not zip')
    out,meta=grid.normalize_empty_grid_paragraphs(src,tmp_path)
    assert out==src and not meta['changed']


def test_numbered_empty_paragraph_is_not_treated_as_blank(grid, tmp_path):
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:default="1" w:styleId="N">'
              '<w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr>'+mark()+'</w:style></w:styles>')
    src = package(tmp_path, '<w:p/>', styles)
    out, meta = grid.normalize_empty_grid_paragraphs(src, tmp_path)
    assert out == src and not meta['changed']
