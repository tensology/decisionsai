from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("distr.core.initiative.service")


@dataclass
class DraftEntry:
    id: str
    action_type: str
    description: str
    draft: str
    reason: str
    created_at: str
    expires_at: str


class DraftQueue:
    def __init__(self, path: str = "db/initiative_drafts.json") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: list[DraftEntry] = []
        self._load()
        self.expire_old()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, entry: DraftEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            self._save()

    def get_all(self) -> list[DraftEntry]:
        with self._lock:
            return list(self._entries)

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            for i, e in enumerate(self._entries):
                if e.id == entry_id:
                    del self._entries[i]
                    self._save()
                    return True
            return False

    def expire_old(self) -> int:
        now = datetime.now(tz=timezone.utc)
        expired: list[DraftEntry] = []

        with self._lock:
            remaining: list[DraftEntry] = []
            for entry in self._entries:
                expires_at = datetime.fromisoformat(entry.expires_at)
                # Ensure timezone-aware comparison
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < now:
                    expired.append(entry)
                else:
                    remaining.append(entry)

            if expired:
                self._entries = remaining
                self._save()

        for entry in expired:
            logger.info(
                "Draft entry %s expired (action_type=%s)",
                entry.id,
                entry.action_type,
            )

        return len(expired)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self._entries = [DraftEntry(**item) for item in raw]
        except Exception:
            logger.warning("Failed to load draft queue from %s; starting empty.", self._path)
            self._entries = []

    def _save(self) -> None:
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        data = json.dumps(
            [dataclasses.asdict(e) for e in self._entries],
            indent=2,
            ensure_ascii=False,
        )

        dir_for_tmp = parent if parent else "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_for_tmp, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
