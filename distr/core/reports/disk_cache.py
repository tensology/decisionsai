"""On-disk memo for Reports session parses and period snapshots.

Lives under ~/.decisions/cache/reports (outside the repo). Writes are temp+rename.
A corrupt or old-version file is ignored and rebuilt. Session entries are keyed by
path/mtime/size and evicted when the directory grows past a size cap.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

CACHE_VERSION = 1
SESSION_MAX_BYTES = 128 * 1024 * 1024
PERIOD_MAX_ENTRIES = 8
_CACHE_DIR_OVERRIDE: Path | None = None
_LOCK = threading.Lock()
_period_preloaded: dict[tuple, dict] = {}
_loaded_startup = False


def set_report_cache_dir(path: Path | None) -> None:
    """Tests point this at a temporary directory. None restores the default."""
    global _CACHE_DIR_OVERRIDE, _period_preloaded, _loaded_startup
    _CACHE_DIR_OVERRIDE = Path(path) if path is not None else None
    _period_preloaded = {}
    _loaded_startup = False


def report_cache_dir() -> Path:
    if _CACHE_DIR_OVERRIDE is not None:
        return _CACHE_DIR_OVERRIDE
    override = os.environ.get("DECISIONS_REPORTS_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".decisions" / "cache" / "reports"


def _disk_cache_allowed() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST") and _CACHE_DIR_OVERRIDE is None and not os.environ.get("DECISIONS_REPORTS_CACHE_DIR"):
        return False
    return True


def _json_default(obj):
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, date):
        return {"__d__": obj.isoformat()}
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Cannot cache {type(obj).__name__}")


def _json_hook(obj):
    if set(obj.keys()) == {"__dt__"}:
        return datetime.fromisoformat(obj["__dt__"])
    if set(obj.keys()) == {"__d__"}:
        return date.fromisoformat(obj["__d__"])
    return obj


def _dumps(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload, default=_json_default, separators=(",", ":")).encode("utf-8"), compresslevel=6)


def _loads(data: bytes) -> dict | None:
    try:
        payload = json.loads(gzip.decompress(data).decode("utf-8"), object_hook=_json_hook)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    return payload


def _canonical_key(key) -> bytes:
    return json.dumps(key, default=_json_default, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _key_hash(key) -> str:
    return hashlib.sha256(_canonical_key(key)).hexdigest()[:32]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_envelope(path: Path) -> dict | None:
    try:
        return _loads(path.read_bytes())
    except Exception:
        return None


def _dir_bytes(folder: Path) -> int:
    total = 0
    if not folder.is_dir():
        return 0
    for item in folder.glob("*"):
        if item.is_file() and not item.name.startswith(".tmp-"):
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _evict_oldest(folder: Path, *, max_bytes: int | None = None, max_entries: int | None = None, keep: Path | None = None) -> None:
    if not folder.is_dir():
        return
    files = []
    for item in folder.glob("*"):
        if not item.is_file() or item.name.startswith(".tmp-") or item == keep:
            continue
        try:
            st = item.stat()
        except OSError:
            continue
        files.append((st.st_mtime_ns, st.st_size, item))
    files.sort()
    total = sum(size for _, size, _ in files) + (keep.stat().st_size if keep and keep.is_file() else 0)
    while files and (
        (max_bytes is not None and total > max_bytes)
        or (max_entries is not None and (len(files) + (1 if keep else 0)) > max_entries)
    ):
        _, size, item = files.pop(0)
        try:
            item.unlink()
        except OSError:
            continue
        total -= size


def session_cache_path(key: tuple) -> Path:
    return report_cache_dir() / "sessions" / f"{_key_hash(key)}.json.gz"


def period_cache_path(key: tuple) -> Path:
    return report_cache_dir() / "periods" / f"{_key_hash(key)}.json.gz"


def load_session_activity(key: tuple):
    """Return (meta, stamps) if a valid disk entry matches this path/mtime/size/kind key."""
    if not _disk_cache_allowed():
        return None
    path = session_cache_path(key)
    payload = _read_envelope(path)
    if not payload or payload.get("kind") != "session":
        return None
    stored = payload.get("key")
    if _canonical_key(stored) != _canonical_key(key):
        return None
    meta = payload.get("meta")
    stamps = payload.get("stamps")
    if not isinstance(meta, dict) or not isinstance(stamps, list):
        return None
    return meta, stamps


def _plain_key(key):
    if isinstance(key, tuple):
        return [_plain_key(part) for part in key]
    return key


def save_session_activity(key: tuple, meta: dict, stamps: list) -> None:
    if not _disk_cache_allowed():
        return
    path = session_cache_path(key)
    payload = {"version": CACHE_VERSION, "kind": "session", "key": _plain_key(key), "meta": meta, "stamps": stamps}
    try:
        data = _dumps(payload)
    except TypeError:
        return
    with _LOCK:
        try:
            _atomic_write(path, data)
            _evict_oldest(path.parent, max_bytes=SESSION_MAX_BYTES, keep=path)
        except OSError:
            path.unlink(missing_ok=True)


def load_period_snapshot(key: tuple) -> dict | None:
    if not _disk_cache_allowed():
        return None
    preloaded = _period_preloaded.get(key)
    if preloaded is not None:
        return preloaded
    # Hash lookup first, then scan (keys may have been stored with equivalent list form).
    path = period_cache_path(key)
    payload = _read_envelope(path) if path.is_file() else None
    if payload is None:
        payload = _find_period_payload(key)
    if not payload or payload.get("kind") != "period":
        return None
    if _canonical_key(payload.get("key")) != _canonical_key(key):
        return None
    snapshot = payload.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def _find_period_payload(key: tuple) -> dict | None:
    folder = report_cache_dir() / "periods"
    if not folder.is_dir():
        return None
    want = _canonical_key(key)
    for item in folder.glob("*.json.gz"):
        payload = _read_envelope(item)
        if payload and payload.get("kind") == "period" and _canonical_key(payload.get("key")) == want:
            return payload
    return None


def save_period_snapshot(key: tuple, snapshot: dict) -> None:
    if not _disk_cache_allowed():
        return
    path = period_cache_path(key)
    payload = {"version": CACHE_VERSION, "kind": "period", "key": _plain_key(key), "snapshot": snapshot}
    try:
        data = _dumps(payload)
    except TypeError:
        return
    with _LOCK:
        try:
            _atomic_write(path, data)
            _period_preloaded[key] = snapshot
            _evict_oldest(path.parent, max_entries=PERIOD_MAX_ENTRIES, keep=path)
        except OSError:
            path.unlink(missing_ok=True)


def load_report_disk_cache() -> int:
    """Load valid period snapshots into memory so the first Reports open can reuse them.

    Session parses stay lazy (they can be large). Corrupt or old-version files are skipped.
    """
    global _loaded_startup
    if not _disk_cache_allowed():
        _loaded_startup = True
        return 0
    folder = report_cache_dir() / "periods"
    loaded = 0
    if folder.is_dir():
        for item in folder.glob("*.json.gz"):
            payload = _read_envelope(item)
            if not payload or payload.get("kind") != "period":
                continue
            snapshot = payload.get("snapshot")
            stored = payload.get("key")
            if not isinstance(snapshot, dict) or stored is None:
                continue
            try:
                key = _restore_key(stored)
            except Exception:
                continue
            _period_preloaded[key] = snapshot
            loaded += 1
    _loaded_startup = True
    return loaded


def _restore_key(stored):
    if isinstance(stored, list):
        return tuple(_restore_key(part) for part in stored)
    return stored


def cache_dir_size_bytes() -> int:
    root = report_cache_dir()
    return _dir_bytes(root / "sessions") + _dir_bytes(root / "periods")
