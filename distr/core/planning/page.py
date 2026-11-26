"""Pages: grid placement plus component references. Nothing else.

A page does **not** decide what a form or a table looks like - it places
components and lets the base template govern their appearance and behaviour. A
page may override the base, but only by exception: whatever it does not name, it
inherits.

Composition is deliberately plain: a page is an ordered list of blocks, each a
reference to a library entry (plus an optional grid span and per-block
overrides). ``compose_wire`` turns that into the existing wireframe DSL, so the
renderer is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MAX_BLOCKS = 60
MAX_TITLE = 120


@dataclass(frozen=True)
class Block:
    """One placed component: a library reference plus placement."""

    ref: str
    span: int | None = None          # grid columns to span; None = default
    overrides: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "span": self.span, "overrides": dict(self.overrides)}


@dataclass(frozen=True)
class Page:
    """Grid placement + component references + base overrides."""

    name: str
    title: str
    route: str = ""
    base: str | None = None           # base template name; None = the default
    blocks: tuple[Block, ...] = ()
    overrides: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "route": self.route,
            "base": self.base,
            "blocks": [b.as_dict() for b in self.blocks],
            "overrides": dict(self.overrides),
        }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _slug(value: Any) -> str:
    raw = _text(value, 64).lower()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _parse_span(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        span = int(value)
    except (TypeError, ValueError):
        return None
    return span if 1 <= span <= 24 else None


def parse_block(raw: Any) -> Block | None:
    """A block from a dict or a bare ref string. None when unusable."""
    if isinstance(raw, str):
        ref = _slug(raw)
        return Block(ref=ref) if ref else None
    if not isinstance(raw, dict):
        return None
    ref = _slug(raw.get("ref") or raw.get("name") or raw.get("widget"))
    if not ref:
        return None
    overrides = raw.get("overrides")
    return Block(
        ref=ref,
        span=_parse_span(raw.get("span")),
        overrides=dict(overrides) if isinstance(overrides, dict) else {},
    )


def parse_page(raw: Any) -> Page | None:
    """Build a page from a dict, or None when unusable. Never raises."""
    if not isinstance(raw, dict):
        return None

    name = _slug(raw.get("name") or raw.get("id"))
    title = _text(raw.get("title"), MAX_TITLE)
    if not name and not title:
        return None

    blocks: list[Block] = []
    for item in (raw.get("blocks") or [])[:MAX_BLOCKS]:
        block = parse_block(item)
        if block:
            blocks.append(block)
        else:
            logger.warning("page: skipped an unusable block in %r", name or title)

    overrides = raw.get("overrides")
    base = _slug(raw.get("base")) or None

    return Page(
        name=name or _slug(title),
        title=title or name,
        route=_text(raw.get("route"), 200),
        base=base,
        blocks=tuple(blocks),
        overrides=dict(overrides) if isinstance(overrides, dict) else {},
    )


def page_from_template(entry: Any, *, title: str = "", route: str = "", name: str = "") -> Page:
    """A new page from a page-template library entry.

    The template's ``wire`` is carried as a single block referencing the entry,
    so the page inherits the template's structure and can still be overridden.
    """
    entry_name = _slug(getattr(entry, "name", "") or "")
    entry_title = _text(getattr(entry, "title", "") or "", MAX_TITLE)
    page_title = _text(title, MAX_TITLE) or entry_title or entry_name or "Page"
    block = Block(ref=entry_name) if entry_name else None
    return Page(
        name=_slug(name) or _slug(page_title),
        title=page_title,
        route=_text(route, 200),
        base=None,
        blocks=(block,) if block else (),
    )


def resolve_blocks(page: Page, *, library_module: Any = None) -> tuple[list[tuple[Block, Any]], list[str]]:
    """Pair each block with its library entry.

    Returns ``(resolved, missing_refs)``. A block whose entry is missing is
    reported rather than silently dropped, so the caller can surface it.
    """
    if library_module is None:
        from distr.core.planning import library as library_module  # type: ignore[no-redef]

    resolved: list[tuple[Block, Any]] = []
    missing: list[str] = []
    for block in page.blocks:
        entry = None
        try:
            entry = library_module.get(block.ref)
        except Exception:
            logger.debug("page: library lookup failed for %r", block.ref, exc_info=True)
        if entry is None:
            missing.append(block.ref)
            continue
        resolved.append((block, entry))
    return resolved, missing


def compose_wire(page: Page, *, library_module: Any = None, indent: str = "  ") -> tuple[str, list[str]]:
    """Render a page to wireframe DSL text.

    Returns ``(wire, missing_refs)``. The screen header comes from the page; each
    block contributes its library entry's ``wire`` body.
    """
    resolved, missing = resolve_blocks(page, library_module=library_module)

    head = f'screen "{page.title}"'
    if page.name:
        head += f" id={page.name}"
    if page.route:
        head += f" route={page.route}"

    lines = [head]
    for block, entry in resolved:
        body = str(getattr(entry, "wire", "") or "").strip()
        if not body:
            continue
        body_lines = body.splitlines()
        # A page-template carries its own `screen` header; the page supplies the
        # screen, so the template contributes only its body. Without this a
        # template composed into a page nests screen inside screen.
        if str(getattr(entry, "kind", "")).strip().lower() == "page-template" and body_lines:
            if body_lines[0].lstrip().startswith("screen"):
                body_lines = body_lines[1:]
        for line in body_lines:
            stripped = line.rstrip()
            if not stripped:
                continue
            lines.append(f"{indent}{stripped}")
    return "\n".join(lines) + "\n", missing


def effective_base(page: Page) -> Any:
    """The base template this page actually renders with (base + overrides)."""
    from distr.core.planning import base_template

    return base_template.for_page(page.base, page.overrides)
