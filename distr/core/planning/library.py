"""Planning widget / layout / page-template library.

Mirrors ``skin_discovery``: scan a directory for folders containing a valid
``manifest.json``, skip invalid ones with a logged warning, and never fail the
caller. Drop a folder in and it appears - no rebuild, no code change.

Two roots are merged:

- ``assets/planning-library/`` - the built-in spine shipped with the app;
- ``~/.decisions/planning/library/`` - the operator's own entries, shareable
  between installs and editable without touching the repo.

A user entry with the same ``name`` overrides the built-in.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
KINDS = ("widget", "layout", "page-template", "base-template")
SCOPE_GLOBAL = "global"
PRODUCT_PREFIX = "product:"
MAX_NAME = 64
MAX_WIRE = 20_000


@dataclass(frozen=True)
class LibraryEntry:
    """One library folder. ``wire`` uses the existing wireframe DSL."""

    name: str
    kind: str
    title: str
    scope: str
    wire: str
    tags: tuple[str, ...] = ()
    description: str = ""
    slots: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    source: str = "builtin"  # builtin | user
    folder: str = ""

    @property
    def product(self) -> str | None:
        """The product slug when this entry is product-scoped, else None."""
        if self.scope.startswith(PRODUCT_PREFIX):
            return self.scope[len(PRODUCT_PREFIX):].strip().lower() or None
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "title": self.title,
            "scope": self.scope,
            "product": self.product,
            "wire": self.wire,
            "tags": list(self.tags),
            "description": self.description,
            "slots": list(self.slots),
            "config": dict(self.config),
            "version": self.version,
            "source": self.source,
        }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalise_name(value: Any) -> str:
    raw = _text(value, MAX_NAME).lower()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._")


def _normalise_scope(value: Any) -> str:
    raw = _text(value, 80).lower()
    if not raw or raw == SCOPE_GLOBAL:
        return SCOPE_GLOBAL
    if raw.startswith(PRODUCT_PREFIX):
        slug = _normalise_name(raw[len(PRODUCT_PREFIX):])
        return f"{PRODUCT_PREFIX}{slug}" if slug else SCOPE_GLOBAL
    # A bare product name is accepted as product:<name>.
    slug = _normalise_name(raw)
    return f"{PRODUCT_PREFIX}{slug}" if slug else SCOPE_GLOBAL


def parse_manifest(raw: Any, *, source: str = "builtin", folder: str = "") -> LibraryEntry | None:
    """Build an entry from a manifest dict, or None when it is unusable.

    Unusable means: not a dict, no usable name, an unknown ``kind``, or no
    ``wire`` body. Every failure is a skip with a logged warning, never a raise.
    """
    if not isinstance(raw, dict):
        logger.warning("planning-library: manifest in %r is not an object; skipped", folder or "?")
        return None

    name = _normalise_name(raw.get("name") or folder)
    if not name:
        logger.warning("planning-library: manifest in %r has no usable name; skipped", folder or "?")
        return None

    kind = _text(raw.get("kind"), 32).lower() or "widget"
    if kind not in KINDS:
        logger.warning(
            "planning-library: %r has unknown kind %r (expected one of %s); skipped",
            name, kind, ", ".join(KINDS),
        )
        return None

    wire = str(raw.get("wire") or "").strip()
    if not wire:
        logger.warning("planning-library: %r has no wire body; skipped", name)
        return None
    if len(wire) > MAX_WIRE:
        logger.warning("planning-library: %r wire body is %d chars (max %d); skipped", name, len(wire), MAX_WIRE)
        return None

    tags_raw = raw.get("tags") or []
    tags = tuple(
        t for t in (_normalise_name(item) for item in (tags_raw if isinstance(tags_raw, (list, tuple)) else [])) if t
    )[:12]

    slots_raw = raw.get("slots") or []
    slots = tuple(
        s for s in (_normalise_name(item) for item in (slots_raw if isinstance(slots_raw, (list, tuple)) else [])) if s
    )[:12]

    try:
        version = int(raw.get("version") or 1)
    except (TypeError, ValueError):
        version = 1

    config_raw = raw.get("config")
    config = dict(config_raw) if isinstance(config_raw, dict) else {}

    return LibraryEntry(
        name=name,
        kind=kind,
        title=_text(raw.get("title"), 120) or name.replace("-", " ").title(),
        scope=_normalise_scope(raw.get("scope")),
        wire=wire,
        tags=tags,
        description=_text(raw.get("description"), 400),
        slots=slots,
        config=config,
        version=max(1, version),
        source=source,
        folder=folder,
    )


def _scan_root(root: Path, *, source: str) -> dict[str, LibraryEntry]:
    found: dict[str, LibraryEntry] = {}
    try:
        if not root.is_dir():
            return found
        # Recursive: entries may be grouped in subfolders (widgets/, layouts/).
        manifests = sorted(
            p for p in root.rglob(MANIFEST_NAME)
            if not any(part.startswith(".") for part in p.relative_to(root).parts)
        )
    except OSError:
        logger.debug("planning-library: could not scan %s", root, exc_info=True)
        return found

    for manifest in manifests:
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("planning-library: %s has an unreadable manifest (%s); skipped", manifest.parent.name, exc)
            continue
        entry = parse_manifest(raw, source=source, folder=manifest.parent.name)
        if entry:
            found[entry.name] = entry
    return found


def builtin_root() -> Path:
    """The shipped spine. Resolved from the repo/assets layout."""
    return Path(__file__).resolve().parents[3] / "assets" / "planning-library"


def library_root() -> Path:
    """The operator's library (user data, shareable between installs)."""
    import os

    override = (os.environ.get("DECISIONS_PLANNING_LIBRARY_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    from distr.core.paths import DATA_DIR

    return Path(DATA_DIR) / "planning" / "library"


def discover(*, user_root: Path | None = None, builtin: Path | None = None) -> list[LibraryEntry]:
    """All entries, built-ins first, user entries overriding by name.

    Never raises: an unreadable root or a broken manifest is skipped.
    """
    merged: dict[str, LibraryEntry] = {}
    merged.update(_scan_root(builtin if builtin is not None else builtin_root(), source="builtin"))
    merged.update(_scan_root(user_root if user_root is not None else library_root(), source="user"))
    return sorted(merged.values(), key=lambda e: (e.kind, e.name))


def _matches(entry: LibraryEntry, *, kind: str | None, product: str | None) -> bool:
    if kind and entry.kind != kind:
        return False
    entry_product = entry.product
    if entry_product is None:
        return True
    # A product-scoped entry is only offered for that product.
    return bool(product) and entry_product == product.strip().lower()


def entries(
    *,
    kind: str | None = None,
    product: str | None = None,
    tag: str | None = None,
    user_root: Path | None = None,
    builtin: Path | None = None,
) -> list[LibraryEntry]:
    """Library entries filtered by kind, product scope and tag.

    Global entries are always offered; a product-scoped entry only appears when
    ``product`` names that product, so a bespoke widget never pollutes the
    general library.
    """
    want_tag = _normalise_name(tag) if tag else None
    out: list[LibraryEntry] = []
    for entry in discover(user_root=user_root, builtin=builtin):
        if not _matches(entry, kind=kind, product=product):
            continue
        if want_tag and want_tag not in entry.tags:
            continue
        out.append(entry)
    return out


def get(name: str, *, user_root: Path | None = None, builtin: Path | None = None) -> LibraryEntry | None:
    wanted = _normalise_name(name)
    if not wanted:
        return None
    for entry in discover(user_root=user_root, builtin=builtin):
        if entry.name == wanted:
            return entry
    return None


def panel_entries(screen: str, *, product: str | None = None, **kwargs: Any) -> list[LibraryEntry]:
    """What the side panel offers for a screen.

    sitemap / flow  -> page templates
    page / wireframe -> widgets
    """
    key = (screen or "").strip().lower()
    if key in ("sitemap", "flow", "flows"):
        return entries(kind="page-template", product=product, **kwargs)
    return entries(kind="widget", product=product, **kwargs)
