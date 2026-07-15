"""Cross-process coordination between live SQLite users and offline restore."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


class DatabaseInUseError(RuntimeError):
    pass


_runtime_handles: list[IO[str]] = []


def _lock_path(database_path: str | Path) -> Path:
    path = Path(database_path).expanduser().resolve()
    return path.with_name(path.name + ".runtime.lock")


def acquire_runtime_database_lock(database_path: str | Path) -> IO[str] | None:
    """Hold a shared lock for the lifetime of an app/worker process."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows fallback has no flock
        return None
    lock_path = _lock_path(database_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    _runtime_handles.append(handle)
    return handle


@contextmanager
def exclusive_database_maintenance_lock(
    database_path: str | Path,
) -> Iterator[None]:
    """Reject an offline restore while any DecisionsAI process uses the DB."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows has atomic replace fallback
        yield
        return
    lock_path = _lock_path(database_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatabaseInUseError(
                "The DecisionsAI database is in use; quit the app before restoring"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
