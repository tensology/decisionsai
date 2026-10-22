"""Oracle chat image attachments: validation, storage and allowlisted lookup.

Images are stored under ~/.decisions/chat_images/ with a random name. Only
jpeg/png/gif/webp up to 8 MB are accepted (the limit every provider adapter in
llm_factory supports). The format is checked from magic bytes, not the client
supplied content type.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional

MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024
_EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|gif|webp)$")
REJECT_MESSAGE = "Only JPEG, PNG, GIF or WebP images up to 8 MB can be attached."


class ChatImageError(ValueError):
    pass


def chat_images_dir() -> Path:
    return Path.home() / ".decisions" / "chat_images"


def sniff_image_mime(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def store_chat_image(data: bytes, root: Optional[Path] = None) -> dict:
    if not data or len(data) > MAX_CHAT_IMAGE_BYTES:
        raise ChatImageError(REJECT_MESSAGE)
    mime = sniff_image_mime(data)
    if not mime:
        raise ChatImageError(REJECT_MESSAGE)
    base = root or chat_images_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{_EXT_BY_MIME[mime]}"
    path = base / name
    path.write_bytes(data)
    return {"name": name, "path": str(path), "mime_type": mime, "size": len(data), "url": chat_image_url(name)}


def chat_image_url(name: str) -> str:
    return f"/api/chat-images/{name}"


def resolve_chat_image(value: str, root: Optional[Path] = None) -> Optional[Path]:
    """Return the stored image path for a name or path inside the image dir, else None."""
    if not value:
        return None
    name = Path(str(value)).name
    if not _NAME_RE.match(name):
        return None
    base = (root or chat_images_dir()).resolve(strict=False)
    if str(value) != name and Path(str(value)).expanduser().resolve(strict=False).parent != base:
        return None
    path = base / name
    return path if path.is_file() else None
