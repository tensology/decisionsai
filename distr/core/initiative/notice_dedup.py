"""Post the same Initiative notice at most once a day.

"Player1Sport (jira) has 76 fetched items available for review" was posted 16
times into one chat between 15 and 19 Nov, every scan cycle, because nothing
changed and nothing remembered it had already been said. The key ignores the
situational suffix ("[last cycle 1m ago; desktop: …]") so only a real change in
the notice (e.g. a new item count) is posted again inside the window.
Survives restarts via a small JSON file next to the database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WINDOW_SEC = 24 * 60 * 60
_FILE_ENV = "DECISIONS_NOTICE_DEDUP_FILE"
_LOCK = threading.Lock()


def _path() -> Path:
    override = os.environ.get(_FILE_ENV)
    if override:
        return Path(override)
    from distr.core.paths import DB_DIR

    return Path(DB_DIR) / "initiative_notice_dedup.json"


def notice_key(message: str, fingerprint: str | None = None) -> str:
    text = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(message or ""))  # situational suffix
    text = re.sub(r"\s+", " ", text).strip().lower()
    raw = f"{text}|{fingerprint or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def should_post(message: str, *, chat_id: int | None = None, fingerprint: str | None = None,
                now: float | None = None, window_sec: int = WINDOW_SEC) -> bool:
    """True when this notice may be posted; records it when it may."""
    now = time.time() if now is None else now
    key = f"{chat_id or 0}:{notice_key(message, fingerprint)}"
    path = _path()
    with _LOCK:
        seen = _load(path)
        last = seen.get(key)
        if last is not None and now - last < window_sec:
            logger.info("Initiative notice suppressed (already posted %.1fh ago)", (now - last) / 3600)
            return False
        seen = {k: v for k, v in seen.items() if now - v < window_sec}
        seen[key] = now
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(seen), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.debug("Could not persist notice dedup state", exc_info=True)
    return True
