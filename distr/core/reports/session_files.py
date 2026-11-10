"""Read retained provider sessions without extracting or changing their archives."""
from contextlib import contextmanager
from io import TextIOWrapper
from pathlib import Path
import re

_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def session_variant(path):
    """A retained continuation can share its parent UUID; address it explicitly."""
    name = Path(path).name.removesuffix('.zst').removesuffix('.jsonl')
    match = re.search(rf'({_UUID})_({_UUID})$', name, re.I)
    return match.group(0) if match else None


def session_paths(root):
    root = Path(root)
    yield from root.rglob('*.jsonl')
    yield from root.rglob('*.jsonl.zst')


def session_roots(root, kind):
    roots = [Path(root)]
    if kind == 'codex_local' and Path(root) == Path.home()/'.codex'/'sessions':
        roots.append(Path.home()/'.codex'/'archived_sessions')
    return [path for path in roots if path.is_dir() and not path.is_symlink()]


@contextmanager
def open_session(path):
    path = Path(path)
    if path.name.endswith('.jsonl.zst'):
        import zstandard
        try:
            with path.open('rb') as raw:
                with zstandard.ZstdDecompressor().stream_reader(raw) as decoded:
                    with TextIOWrapper(decoded, encoding='utf-8', errors='replace') as text:
                        yield text
        except zstandard.ZstdError as exc:
            raise OSError(f'Cannot read compressed session {path.name}.') from exc
    else:
        with path.open(encoding='utf-8', errors='replace') as text:
            yield text
