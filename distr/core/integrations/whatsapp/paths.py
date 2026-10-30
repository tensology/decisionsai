"""WhatsApp media path helpers — relative storage under DB_DIR (Stage 0 BUG-8)."""

from __future__ import annotations

import os
import hashlib
import tempfile
from pathlib import Path

from distr.core.paths import DB_DIR


def store_whatsapp_media(content: bytes, *, message_id: str, media_type: str, mime_type: str) -> str:
    """Keep distinct messages and content in distinct, atomically written files."""
    if not content:
        raise ValueError('WhatsApp media is empty.')
    mime = str(mime_type).split(';', 1)[0].strip().lower()
    ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
           'audio/ogg': '.ogg', 'audio/opus': '.opus', 'audio/mpeg': '.mp3',
           'audio/mp4': '.m4a', 'audio/webm': '.webm', 'video/mp4': '.mp4',
           'video/3gpp': '.3gp', 'video/webm': '.webm', 'application/pdf': '.pdf'}.get(mime, '.bin')
    kind = media_type if media_type in {'photo', 'image', 'voice', 'audio', 'ptt', 'video', 'document', 'sticker'} else 'media'
    identity = hashlib.sha256(str(message_id).encode() + b'\0' + content).hexdigest()[:32]
    directory = Path(DB_DIR) / 'whatsapp_media'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'whatsapp_{kind}_{identity}{ext}'
    partial = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.part', delete=False) as output:
            partial = Path(output.name)
            output.write(content)
        partial.replace(destination)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return str(destination)


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
