"""Persistent memory of Telegram button taps (callback query ids) already acted on.

The relay can replay a tap after a reconnect, and a restart empties the in-memory
caches, so ids are kept on disk for an hour. A repeated id is never acted on twice.
"""

import json
import os
import threading
import time
from pathlib import Path

SEEN_TTL_S = 3600
SEEN_CAP = 200
_LOCK = threading.Lock()


def _path() -> Path:
    base = os.environ.get("DECISIONS_DB_DIR") or os.path.expanduser("~/.decisions")
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base) / "telegram_seen_callbacks.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def claim_callback(callback_id) -> bool:
    """True the first time this callback id is seen (and remember it); False for a repeat."""
    key = str(callback_id or "").strip()
    if not key:
        return True
    with _LOCK:
        now = time.time()
        seen = {k: float(v) for k, v in _load().items() if now - float(v or 0) < SEEN_TTL_S}
        if key in seen:
            return False
        seen[key] = now
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-SEEN_CAP:])
        try:
            tmp = _path().with_suffix(".tmp")
            tmp.write_text(json.dumps(seen), encoding="utf-8")
            tmp.replace(_path())
        except Exception:
            pass  # in-flight handling must never fail because the memory could not be saved
        return True
