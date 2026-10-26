"""Run explicitly with DOCX_PRINT_INTEGRATION=1 inside the built image/runtime."""
import os
from pathlib import Path
import subprocess

import pytest

from docx_print_converter.converter import convert
from make_samples import make_samples

pytestmark = pytest.mark.skipif(os.environ.get('DOCX_PRINT_INTEGRATION') != '1',
                                reason='Requires the built renderer image')


def test_public_layout_corpus(tmp_path):
    expected = make_samples(tmp_path)
    for name, pages in expected.items():
        result = convert(tmp_path / (name + '.docx'), tmp_path / (name + '.pdf'))
        assert result['pages'] == pages, (name, result)
        text = subprocess.check_output(['pdftotext', str(tmp_path / (name + '.pdf')), '-'], text=True)
        assert {'simple': 'FIRST', 'intentional-blank': 'LAST', 'landscape': 'LANDSCAPE', 'table': 'CELL-A'}[name] in text
        if name == 'landscape':
            numbers = result['page_size'].split()
            assert float(numbers[0]) > float(numbers[2])
        if name == 'intentional-blank':
            middle = subprocess.check_output(['pdftotext', '-f', '2', '-l', '2', str(tmp_path / (name + '.pdf')), '-'], text=True)
            assert not middle.strip()
