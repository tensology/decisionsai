"""Small, dependency-free retention helpers for append-only diagnostics."""

from __future__ import annotations

from pathlib import Path


def rotate_oversize_file(
    path: str | Path,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    backups: int = 3,
) -> bool:
    """Rotate ``path`` when it exceeds ``max_bytes``.

    Rotation happens during service setup, before that service starts writing,
    which avoids the cross-process races of a live rotating log handler.
    """
    target = Path(path)
    if max_bytes < 1 or backups < 1 or not target.is_file():
        return False
    try:
        if target.stat().st_size <= max_bytes:
            return False
        oldest = target.with_name(f"{target.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            source = target.with_name(f"{target.name}.{index}")
            if source.exists():
                source.replace(target.with_name(f"{target.name}.{index + 1}"))
        target.replace(target.with_name(f"{target.name}.1"))
        return True
    except OSError:
        return False
