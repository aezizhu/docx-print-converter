"""Exercise the actual image as an unprivileged, offline process."""
import hashlib
from pathlib import Path
import subprocess
import tempfile

from docx_print_converter.converter import convert
from make_samples import make_samples

with tempfile.TemporaryDirectory(prefix='docx-smoke-') as folder:
    root = Path(folder)
    expected = make_samples(root)
    for name, pages in expected.items():
        source = root / (name + '.docx')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        output = root / (name + '.pdf')
        result = convert(source, output)
        assert result['pages'] == pages, (name, result)
        assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
        text = subprocess.check_output(['pdftotext', str(output), '-'], text=True)
        assert {'simple': 'FIRST', 'intentional-blank': 'LAST', 'landscape': 'LANDSCAPE', 'table': 'CELL-A'}[name] in text
        if name == 'intentional-blank':
            assert not subprocess.check_output(['pdftotext', '-f', '2', '-l', '2', str(output), '-'], text=True).strip()
        if name == 'landscape':
            size = result['page_size'].split()
            assert float(size[0]) > float(size[2])
        print(name, result['engine']['name'], pages, 'PASS', flush=True)
    # Exercise the separate native shape engine as well as automatic flow routing.
    result = convert(root / 'simple.docx', root / 'simple-onlyoffice.pdf', engine='onlyoffice')
    assert result['pages'] == 1 and result['engine']['name'] == 'onlyoffice'
    assert 'FIRST' in subprocess.check_output(['pdftotext', str(root / 'simple-onlyoffice.pdf'), '-'], text=True)
    print('onlyoffice explicit smoke PASS', flush=True)
