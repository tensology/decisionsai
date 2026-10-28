"""Notification sounds a user can choose between.

Samples come from the currency-trader notification library and live under
``assets/sounds/notifications``. Keeping a fixed catalogue means the web UI
offers known-good choices instead of arbitrary filenames.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from distr.core.paths import ASSETS_DIR

DEFAULT_NOTIFICATION_SOUND = "bell"

NOTIFICATION_SOUNDS: tuple[dict[str, str], ...] = (
    {"slug": "bell", "label": "Bell", "file": "bell.wav"},
    {"slug": "chime", "label": "Chime", "file": "chime.mp3"},
    {"slug": "dong", "label": "Dong", "file": "dong.mp3"},
    {"slug": "gentle", "label": "Gentle alarm", "file": "gentle.mp3"},
    {"slug": "tone", "label": "Notification tone", "file": "tone.mp3"},
    {"slug": "elegant", "label": "Elegant", "file": "elegant.mp3"},
    {"slug": "achievement", "label": "Achievement", "file": "achievement.mp3"},
    {"slug": "none", "label": "Silent", "file": ""},
)

_SLUGS = {item["slug"] for item in NOTIFICATION_SOUNDS}


def sounds_dir() -> Path:
    return Path(ASSETS_DIR) / "sounds" / "notifications"


def normalize(value: Any) -> str:
    """Return a known slug, falling back to the default."""
    slug = str(value or "").strip().lower()
    return slug if slug in _SLUGS else DEFAULT_NOTIFICATION_SOUND


def catalog() -> list[dict[str, str]]:
    """Return the catalogue with browser-usable URLs."""
    return [
        {
            "slug": item["slug"],
            "label": item["label"],
            "url": f"/assets/sounds/notifications/{item['file']}" if item["file"] else "",
        }
        for item in NOTIFICATION_SOUNDS
    ]


def sound_url(value: Any) -> str:
    """Return the browser URL for a saved sound, or '' for silent/missing."""
    slug = normalize(value)
    for item in NOTIFICATION_SOUNDS:
        if item["slug"] != slug:
            continue
        if not item["file"]:
            return ""
        if (sounds_dir() / item["file"]).is_file():
            return f"/assets/sounds/notifications/{item['file']}"
        return ""
    return ""
