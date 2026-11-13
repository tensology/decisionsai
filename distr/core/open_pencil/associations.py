"""Associate an OpenPencil file with a planning page (Interfaces section).

Stored as a small JSON sidecar (``~/.decisions/open_pencil/associations.json``,
override with ``DECISIONS_OPEN_PENCIL_STORE``) keyed by plan workspace id and
page key, so the planning DB schema and in-flight planning files are untouched.
Only the local path and its last seen hash are stored; the design file is
never copied or modified.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from distr.core.open_pencil.reader import validate_document_path

STORE_ENV = "DECISIONS_OPEN_PENCIL_STORE"
_LOCK = threading.Lock()


def store_path() -> Path:
    override = os.environ.get(STORE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".decisions" / "open_pencil" / "associations.json"


def _key(workspace_id: int, page_key: str) -> str:
    page = str(page_key or "").strip()
    if not page or len(page) > 300 or "\n" in page:
        raise ValueError("A planning page key is required (max 300 chars, single line).")
    return f"{int(workspace_id)}::{page}"


def _load() -> dict:
    path = store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "links": {}}
    except ValueError as exc:
        raise ValueError(f"OpenPencil association store is unreadable: {path}") from exc
    data.setdefault("links", {})
    return data


def _save(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".associations.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def associate(workspace_id: int, page_key: str, file_path: str, *, note: str | None = None) -> dict:
    doc = validate_document_path(file_path)
    key = _key(workspace_id, page_key)
    record = {
        "workspace_id": int(workspace_id),
        "page_key": str(page_key).strip(),
        "path": str(doc),
        "format": doc.suffix.lower().lstrip("."),
        "note": (note or "").strip()[:500] or None,
        "linked_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        data = _load()
        data["links"][key] = record
        _save(data)
    return record


def get_association(workspace_id: int, page_key: str) -> dict | None:
    with _LOCK:
        return _load()["links"].get(_key(workspace_id, page_key))


def list_associations(workspace_id: int) -> list[dict]:
    prefix = f"{int(workspace_id)}::"
    with _LOCK:
        links = _load()["links"]
    return [v for k, v in sorted(links.items()) if k.startswith(prefix)]


def remove_association(workspace_id: int, page_key: str) -> bool:
    key = _key(workspace_id, page_key)
    with _LOCK:
        data = _load()
        removed = data["links"].pop(key, None) is not None
        if removed:
            _save(data)
    return removed
