"""WhatsApp media path helpers — relative storage under DB_DIR (Stage 0 BUG-8)."""

from __future__ import annotations

import os

from distr.core.paths import DB_DIR


def media_path_for_database(abs_path: str) -> str:
    """Return path to persist in DB: relative to DB_DIR when under DB_DIR."""
    if not abs_path:
        return ""
    try:
        abs_norm = os.path.realpath(abs_path)
        db_norm = os.path.realpath(DB_DIR)
        sep = os.sep
        if abs_norm.startswith(db_norm + sep) or abs_norm == db_norm:
            rel = os.path.relpath(abs_norm, db_norm)
            return rel.replace("\\", "/")
    except Exception:
        pass
    return abs_path


def resolve_media_local_path(stored: str) -> str:
    """Resolve stored DB path to an absolute path for filesystem access."""
    if not stored:
        return ""
    s = stored.strip()
    if os.path.isabs(s):
        return os.path.normpath(s)
    return os.path.normpath(os.path.join(DB_DIR, s.replace("/", os.sep)))


def resolve_whatsapp_media_disk_path(stored: str) -> str:
    """Resolve DB-relative paths and legacy basename-only rows for filesystem access."""
    if not stored or not str(stored).strip():
        return ""
    stored_s = str(stored).strip()
    resolved = resolve_media_local_path(stored_s)
    if resolved:
        rp = os.path.realpath(resolved)
        if os.path.exists(rp):
            return rp
    media_dir = os.path.realpath(os.path.join(DB_DIR, "whatsapp_media"))
    basename = os.path.basename(stored_s.replace("\\", "/"))
    if not basename:
        return ""
    return os.path.realpath(os.path.join(media_dir, basename))
