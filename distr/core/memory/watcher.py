"""Optional QFileSystemWatcher for memory markdown dir (R8)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MemoryFilesWatcher:
    """
    Notify when AGENT/USER/MEMORY/EVENTS change on disk.

    Falls back to no-op when Qt or QFileSystemWatcher is unavailable (CI / headless).
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        on_changed: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._root = root
        self._on_changed = on_changed
        self._watcher = None
        self._started = False

    def start(self) -> bool:
        from distr.core.memory.files import default_memory_dir

        if self._started:
            return True

        base = self._root if self._root is not None else default_memory_dir()
        try:
            from PyQt6.QtCore import QFileSystemWatcher
        except ImportError:
            logger.debug("MemoryFilesWatcher: PyQt6.QtCore unavailable — skip")
            return False

        watcher = QFileSystemWatcher()
        path_str = str(base.resolve())
        if not watcher.addPath(path_str):
            logger.warning("MemoryFilesWatcher: could not watch %s", path_str)
            return False

        if self._on_changed:

            def _relay(_path: str) -> None:
                try:
                    self._on_changed(path_str)
                except Exception:
                    logger.warning("MemoryFilesWatcher callback failed", exc_info=True)

            watcher.directoryChanged.connect(_relay)

        self._watcher = watcher
        self._started = True
        return True

    def stop(self) -> None:
        if self._watcher is not None:
            try:
                paths = list(self._watcher.directories()) + list(self._watcher.files())
                if paths:
                    self._watcher.removePaths(paths)
            except Exception:
                pass
            self._watcher = None
        self._started = False
