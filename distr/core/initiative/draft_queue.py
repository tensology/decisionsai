from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from distr.core.initiative.tiers import PermissionTier
from distr.core.paths import DB_DIR

logger = logging.getLogger("distr.core.initiative.service")

_DEFAULT_DRAFT_PATH = os.path.join(DB_DIR, "initiative_drafts.json")


@dataclass
class DraftEntry:
    id: str
    action_type: str
    description: str
    draft: str
    reason: str
    created_at: str
    expires_at: str
    permission_tier: int = PermissionTier.APPROVE
    #: When set, ``InitiativeService.approve_draft`` runs this before removing the row (R11).
    execute_payload: dict[str, Any] | None = None


_DRAFT_ENTRY_FIELDS = {f.name for f in dataclasses.fields(DraftEntry)}


class DraftQueue:
    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_DRAFT_PATH
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

    def get_by_id(self, entry_id: str) -> DraftEntry | None:
        """Return the pending entry with this id, or ``None``."""
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
            return None

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
            loaded: list[DraftEntry] = []
            for item in raw:
                row = {k: item[k] for k in item if k in _DRAFT_ENTRY_FIELDS}
                if "permission_tier" not in row and "tier" in item:
                    row["permission_tier"] = int(item["tier"])
                loaded.append(DraftEntry(**row))
            self._entries = loaded
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
