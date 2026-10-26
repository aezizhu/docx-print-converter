import json
from pathlib import Path

from docx_print_converter import cli


def test_report_cannot_overwrite_source_before_conversion(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'in.docx'; source.write_bytes(b'original')
    monkeypatch.setattr(cli, 'convert', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not run')))
    assert cli.main([str(source), str(tmp_path / 'out.pdf'), '--report', str(source)]) == 1
    assert source.read_bytes() == b'original'
    assert json.loads(capsys.readouterr().err)['error'] == 'report_path_conflict'


def test_success_emits_one_json_report(tmp_path, monkeypatch, capsys):
    result = {'pages': 1, 'engine': {'name': 'docx4j', 'version': 'test'}}
    monkeypatch.setattr(cli, 'convert', lambda *a, **k: result)
    report = tmp_path / 'report.json'
    assert cli.main([str(tmp_path / 'in.docx'), str(tmp_path / 'out.pdf'), '--report', str(report)]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert json.loads(report.read_text()) == result
