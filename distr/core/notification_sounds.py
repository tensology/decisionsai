"""Notification sounds a user can choose between.

The full Crossroads bell catalogue is retained in currency-trader commit
768e0ed0c3e47f3c09a63733e8e47c48847b291b. ``catalog.json`` records its original
labels, source filenames and SHA256 hashes. Previously saved aliases keep working.
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from distr.core.paths import ASSETS_DIR

DEFAULT_NOTIFICATION_SOUND = "bell"

_CATALOG = json.loads((Path(ASSETS_DIR) / "sounds/notifications/catalog.json").read_text())
NOTIFICATION_SOUNDS: tuple[dict[str, str], ...] = tuple(_CATALOG["sounds"])
_ALIASES = _CATALOG["aliases"]
_SLUGS = {item["slug"] for item in NOTIFICATION_SOUNDS} | set(_ALIASES)


def sounds_dir() -> Path:
    return Path(ASSETS_DIR) / "sounds" / "notifications"


def normalize(value: Any) -> str:
    """Return a known slug, falling back to the default."""
    slug = str(value or "").strip().lower()
    return slug if slug in _SLUGS else DEFAULT_NOTIFICATION_SOUND


def resolve_slug(value: Any) -> str:
    """Resolve a saved legacy name to the canonical catalogue choice."""
    slug = normalize(value)
    return _ALIASES.get(slug, slug)


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
    slug = resolve_slug(value)
    for item in NOTIFICATION_SOUNDS:
        if item["slug"] != slug:
            continue
        if not item["file"]:
            return ""
        if (sounds_dir() / item["file"]).is_file():
            return f"/assets/sounds/notifications/{item['file']}"
        return ""
    return ""
