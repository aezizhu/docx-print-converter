import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from docx_print_converter.converter import ConversionError, convert, select_engine

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def document(path, content='<w:p><w:r><w:t>Public sample</w:t></w:r></w:p>', main='word/document.xml'):
    with ZipFile(path, 'w') as z:
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="' + main + '"/></Relationships>')
        z.writestr(main, f'<w:document xmlns:w="{W}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"><w:body>{content}</w:body></w:document>')
    return path


@pytest.mark.parametrize('fragment,engine', [
    ('<w:p/>', 'docx4j'),
    ('<w:tbl><w:tblPr/></w:tbl>', 'docx4j'),
    ('<w:tbl><w:tblPr><w:tblpPr/></w:tblPr></w:tbl>', 'onlyoffice'),
    ('<w:p><v:shape/></w:p>', 'onlyoffice'),
    ('<w:p><wp:anchor/></w:p>', 'onlyoffice'),
    ('<w:p><w:pPr><w:framePr/></w:pPr></w:p>', 'onlyoffice'),
])
def test_structural_routing(tmp_path, fragment, engine):
    source = document(tmp_path / 'sample.docx', fragment)
    assert select_engine(source)[0] == engine


def test_routing_ignores_filename_and_cached_page_count(tmp_path):
    source = document(tmp_path / 'telecom-form.docx')
    with ZipFile(source, 'a') as z:
        z.writestr('docProps/app.xml', '<Pages>99999</Pages>')
    assert select_engine(source)[0] == 'docx4j'


def pdf():
    # Valid one-page PDF with an empty content stream; pdfinfo is an independent parser.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>',
               b'<< /Length 0 >>\nstream\n\nendstream']
    data = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(data); data.extend(b'xref\n0 5\n0000000000 65535 f \n')
    for pos in offsets[1:]: data.extend(f'{pos:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(data)


def test_success_preserves_source_and_reports_actual_pdf(tmp_path, monkeypatch):
    from docx_print_converter import converter as c
    source = document(tmp_path / 'source with spaces.docx'); original = source.read_bytes()
    output = tmp_path / 'out.pdf'
    def render(engine, src, dst, work, timeout):
        assert src != source and src.read_bytes() == original
        dst.write_bytes(pdf()); return {'name': engine, 'version': 'test'}
    monkeypatch.setattr(c, '_render', render)
    result = convert(source, output)
    assert source.read_bytes() == original
    assert result['pages'] == 1
    assert result['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['pdf_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result['engine']['name'] == 'docx4j'
    json.dumps(result)


def test_failure_preserves_existing_output(tmp_path, monkeypatch):
    from docx_print_converter import converter as c
    source = document(tmp_path / 'source.docx'); output = tmp_path / 'out.pdf'
    output.write_bytes(b'existing')
    def render(engine, src, dst, work, timeout):
        dst.write_bytes(pdf()); raise ConversionError('converter_failed')
    monkeypatch.setattr(c, '_render', render)
    with pytest.raises(ConversionError): convert(source, output)
    assert output.read_bytes() == b'existing'
    assert not list(tmp_path.glob('.docx-print-*'))


def test_mutating_converter_is_rejected_and_cannot_edit_original(tmp_path, monkeypatch):
    from docx_print_converter import converter as c
    source = document(tmp_path / 'source.docx'); original = source.read_bytes()
    def render(engine, src, dst, work, timeout):
        src.write_bytes(b'changed'); dst.write_bytes(pdf()); return {'name': engine}
    monkeypatch.setattr(c, '_render', render)
    with pytest.raises(ConversionError, match='source_changed'): convert(source, tmp_path / 'out.pdf')
    assert source.read_bytes() == original
    assert not (tmp_path / 'out.pdf').exists()


def test_invalid_pdf_not_published(tmp_path, monkeypatch):
    from docx_print_converter import converter as c
    source = document(tmp_path / 'source.docx')
    def render(engine, src, dst, work, timeout):
        dst.write_bytes(b'not a PDF'); return {'name': engine}
    monkeypatch.setattr(c, '_render', render)
    with pytest.raises(ConversionError, match='invalid_pdf'): convert(source, tmp_path / 'out.pdf')
    assert not (tmp_path / 'out.pdf').exists()


def test_output_cannot_replace_source(tmp_path):
    source = document(tmp_path / 'source.docx')
    with pytest.raises(ConversionError, match='same_path'): convert(source, source)


def test_routing_follows_relocated_main_part(tmp_path):
    source = document(tmp_path / 'source.docx', '<w:tbl><w:tblPr><w:tblpPr/></w:tblPr></w:tbl>',
                      main='documents/content.xml')
    assert select_engine(source) == ('onlyoffice', ['floating_table'])


def _running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # PID1 in minimal containers may retain a killed orphan as a zombie.
    status = Path('/proc') / str(pid) / 'stat'
    if status.exists() and status.read_text().split(') ', 1)[1].startswith('Z'):
        return False
    return True


@pytest.mark.parametrize('how', ['timeout', 'sigterm', 'sigint', 'sigkill'])
def test_renderer_cleanup_on_timeout_and_cancellation(tmp_path, how):
    if how == 'sigkill' and not sys.platform.startswith('linux'):
        pytest.skip('Linux parent-death signal contract')
    pidfile = tmp_path / 'renderer.pid'
    child = ('import os,time;from pathlib import Path;'
             'Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()));time.sleep(30)')
    wrapper = ('from pathlib import Path;import sys;from docx_print_converter.converter import _run;'
               '_run([sys.executable,"-c",' + repr(child) + '],Path(' + repr(str(tmp_path)) + '),'
               + ('0.3' if how == 'timeout' else '30') + ')')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    process = subprocess.Popen([sys.executable, '-c', wrapper], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    renderer = None
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pidfile.exists(), 'Renderer did not start'
        renderer = int(pidfile.read_text())
        if how != 'timeout':
            os.killpg(process.pid, {'sigterm': signal.SIGTERM, 'sigint': signal.SIGINT,
                                   'sigkill': signal.SIGKILL}[how])
        process.communicate(timeout=5)
        deadline = time.monotonic() + 2
        while _running(renderer) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _running(renderer), 'Renderer survived converter cancellation'
        assert not list(tmp_path.glob('*.pdf'))
    finally:
        if renderer and _running(renderer):
            os.killpg(renderer, signal.SIGKILL)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
