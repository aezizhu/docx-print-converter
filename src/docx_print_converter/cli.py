"""Command-line interface; stdout contains one JSON result."""
import argparse
import json
from pathlib import Path
import sys

from .converter import ConversionError, convert


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert DOCX to PDF using local open-source renderers.')
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--engine', choices=['auto', 'docx4j', 'onlyoffice'], default='auto')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.report and args.report.resolve() in (args.input.resolve(), args.output.resolve()):
            raise ConversionError('report_path_conflict')
        result = convert(args.input, args.output, engine=args.engine, timeout=args.timeout)
        text = json.dumps(result, ensure_ascii=True, sort_keys=True)
        if args.report:
            args.report.write_text(text + '\n', encoding='utf-8')
        print(text)
        return 0
    except (ConversionError, OSError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, ConversionError) else 'io_error'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
