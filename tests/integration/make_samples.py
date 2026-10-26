"""Generate public layout probes; no customer documents are stored in the repository."""
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
CT = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>'''
RELS = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''

def paragraph(text):
    return '<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'


def make_samples(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    page_break = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    table = ('<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid><w:tr>'
             '<w:tc>' + paragraph('CELL-A') + '</w:tc><w:tc>' + paragraph('CELL-B') + '</w:tc></w:tr></w:tbl>')
    cases = [('simple', paragraph('FIRST - ordinary document'), 1, False),
             ('intentional-blank', paragraph('FIRST') + page_break + page_break + paragraph('LAST'), 3, False),
             ('landscape', paragraph('LANDSCAPE'), 1, True),
             ('table', table + paragraph('AFTER-TABLE'), 1, False)]
    for name, content, pages, landscape in cases:
        size = 'w:w="16838" w:h="11906" w:orient="landscape"' if landscape else 'w:w="11906" w:h="16838"'
        body = (f'<w:document xmlns:w="{W}"><w:body>{content}<w:sectPr><w:pgSz {size}/>'
                '<w:pgMar w:top="1440" w:bottom="1440" w:left="1440" w:right="1440"/></w:sectPr></w:body></w:document>')
        with ZipFile(directory / (name + '.docx'), 'w', ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', CT)
            archive.writestr('_rels/.rels', RELS)
            archive.writestr('word/document.xml', body)
            archive.writestr('docProps/app.xml', '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Pages>%d</Pages></Properties>' % pages)
    return {name: pages for name, _, pages, _ in cases}

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(make_samples(args.directory))
