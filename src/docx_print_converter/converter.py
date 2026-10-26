"""Select a renderer by OOXML structure and publish only a verified PDF."""
import hashlib
import os
import posixpath
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import tempfile
import time
from urllib.parse import unquote, urlsplit

from .archive import _bounded_archive, _xml

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
COMPLEX_TAGS = {
    '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblpPr': 'floating_table',
    '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}framePr': 'text_frame',
    '{urn:schemas-microsoft-com:vml}shape': 'legacy_shape',
    '{urn:schemas-microsoft-com:vml}group': 'legacy_shape_group',
    '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor': 'anchored_drawing',
}

class ConversionError(RuntimeError):
    """A short diagnostic code safe to return without document contents."""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def select_engine(source):
    """Route the document and its related XML parts, independent of OPC paths."""
    from .docx_compat import _relationship_target
    features = set()
    try:
        with _bounded_archive(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ConversionError('ambiguous_package_parts')
            pending = [_relationship_target(archive, '', 'officeDocument')]
            visited = set()
            total = 0
            while pending:
                part = pending.pop()
                if part in visited:
                    continue
                visited.add(part)
                entry = archive.getinfo(part)
                total += entry.file_size
                if entry.file_size > MAX_XML_BYTES or total > 16 * MAX_XML_BYTES:
                    features.add('inspection_limited')
                    break
                root = _xml(archive.read(entry))
                features.update(COMPLEX_TAGS[e.tag] for e in root.iter() if e.tag in COMPLEX_TAGS)
                relationships = posixpath.join(posixpath.dirname(part), '_rels',
                                                posixpath.basename(part) + '.rels')
                if relationships not in names:
                    continue
                rel_entry = archive.getinfo(relationships)
                total += rel_entry.file_size
                if rel_entry.file_size > MAX_XML_BYTES or total > 16 * MAX_XML_BYTES:
                    features.add('inspection_limited')
                    break
                rel_root = _xml(archive.read(rel_entry))
                for relation in rel_root:
                    if relation.tag != '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship':
                        continue
                    if relation.get('TargetMode', 'Internal') != 'Internal':
                        continue
                    target = unquote(relation.get('Target', ''))
                    url = urlsplit(target)
                    if not target or url.scheme or url.netloc or url.query or url.fragment or '\\' in target:
                        features.add('inspection_limited')
                        continue
                    name = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                    if target.startswith('/'):
                        name = name.lstrip('/')
                    if name.startswith('../') or name in ('.', '..') or name not in names:
                        features.add('inspection_limited')
                        continue
                    if name.lower().endswith(('.xml', '.vml')) and name not in visited:
                        pending.append(name)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError('invalid_or_oversized_docx') from exc
    return ('onlyoffice' if features else 'docx4j'), sorted(features)


# The isolated launcher avoids running Python/ctypes in a forked preexec_fn,
# which is unsafe when this library is called from a multithreaded service.
# exec preserves Linux PDEATHSIG for ordinary JVM/x2t executables. This kills
# the direct renderer if an outer supervisor SIGKILLs the converter itself.
_RENDERER_EXEC = """import os,signal,sys
if sys.platform.startswith("linux"):
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:
        sys.exit(125)
    if os.getppid()!=int(sys.argv[1]):
        os.kill(os.getpid(),signal.SIGKILL)
signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGTERM,signal.SIGINT})
os.execvpe(sys.argv[2],sys.argv[2:],os.environ)
"""


def _run(command, work, timeout, env=None, cwd=None):
    """Bound execution and clean its process group on timeout or cancellation.

    Linux additionally kills the direct renderer after abrupt parent death.
    Descendants of a renderer require normal group cancellation or a container
    supervisor; PDEATHSIG itself is not inherited by grandchildren.
    """
    process = None
    main_thread = threading.current_thread() is threading.main_thread()
    previous_term = signal.getsignal(signal.SIGTERM) if main_thread else None

    def cancelled(signum, frame):
        raise ConversionError('renderer_cancelled')

    if main_thread:
        signal.signal(signal.SIGTERM, cancelled)
    argv = [sys.executable, '-I', '-c', _RENDERER_EXEC, str(os.getpid())] + list(command)
    try:
        with open(work / 'engine.log', 'wb') as log:
            # Delay Python signal callbacks until the Popen result is assigned.
            # The isolated launcher unblocks these signals before exec.
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}) if main_thread else None
            try:
                process = subprocess.Popen(argv, stdout=log, stderr=log, env=env, cwd=cwd,
                                           start_new_session=True)
            except OSError as exc:
                raise ConversionError('renderer_unavailable') from exc
            finally:
                if mask is not None:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise ConversionError('renderer_timeout') from exc
        if code != 0:
            raise ConversionError('renderer_exit_%d' % code)
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if main_thread:
            signal.signal(signal.SIGTERM, previous_term)


def _render(engine, source, output, work, timeout):
    env = dict(os.environ, HOME=str(work), TMPDIR=str(work), LANG='C.UTF-8', LC_ALL='C.UTF-8')
    if engine == 'docx4j':
        java = os.environ.get('DOCX_PRINT_JAVA', 'java')
        jar = Path(os.environ.get('DOCX_PRINT_JAR', '/opt/docx-print-converter/java/docx-print-converter.jar'))
        if not jar.is_file():
            raise ConversionError('docx4j_jar_unavailable')
        _run([java, '-Djava.awt.headless=true', '-Xmx1024m', '-jar', str(jar), str(source), str(output)],
             work, timeout, env=env)
        return {'name': 'docx4j', 'version': '17.3.3'}
    directory = Path(os.environ.get('DOCX_PRINT_ONLYOFFICE_DIR',
                                   '/var/www/onlyoffice/documentserver/server/FileConverter/bin')).resolve()
    executable = directory / 'x2t'
    if not executable.is_file() or not (directory / 'font_selection.bin').is_file():
        raise ConversionError('onlyoffice_runtime_unavailable')
    env['LD_LIBRARY_PATH'] = str(directory)
    _run([str(executable), str(source), str(output), str(directory)], work, timeout, env=env, cwd=directory)
    return {'name': 'onlyoffice', 'version': '10.0.0'}


def _pdf_info(path):
    if not path.is_file() or not path.stat().st_size:
        raise ConversionError('missing_pdf')
    try:
        result = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True,
                                timeout=30, env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError('pdf_validation_unavailable') from exc
    fields = dict(line.split(':', 1) for line in result.stdout.splitlines() if ':' in line)
    try:
        pages = int(fields.get('Pages', '').strip())
    except ValueError:
        pages = 0
    if result.returncode or pages < 1:
        raise ConversionError('invalid_pdf')
    return pages, fields.get('Page size', '').strip()


def convert(source, output, *, engine='auto', timeout=180):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ConversionError('same_path')
    if source.suffix.lower() != '.docx' or not source.is_file():
        raise ConversionError('input_must_be_docx')
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ConversionError('input_too_large')
    if engine not in ('auto', 'docx4j', 'onlyoffice') or not 1 <= timeout <= 3600:
        raise ConversionError('invalid_options')
    started = time.monotonic()
    original_hash = _sha256(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.docx-print-', dir=output.parent) as temporary:
        work = Path(temporary)
        staged = work / 'input.docx'
        shutil.copyfile(source, staged)
        if _sha256(staged) != original_hash:
            raise ConversionError('source_changed')
        chosen, features = select_engine(staged)
        chosen = chosen if engine == 'auto' else engine
        normalizations = []
        if chosen == 'onlyoffice':
            from .docx_compat import materialize_east_asia_theme
            from .docx_grid import normalize_empty_grid_paragraphs
            for transform in (materialize_east_asia_theme, normalize_empty_grid_paragraphs):
                staged, info = transform(staged, work)
                normalizations.append(info)
        staged_hash = _sha256(staged)
        rendered = work / 'rendered.pdf'
        renderer = _render(chosen, staged, rendered, work, timeout)
        if _sha256(staged) != staged_hash or _sha256(source) != original_hash:
            raise ConversionError('source_changed')
        pages, size = _pdf_info(rendered)
        result = {'engine': renderer, 'features': features, 'normalizations': normalizations,
                  'pages': pages, 'page_size': size, 'source_sha256': original_hash,
                  'pdf_sha256': _sha256(rendered), 'elapsed_seconds': round(time.monotonic() - started, 3)}
        os.replace(rendered, output)
    return result
