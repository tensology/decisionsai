"""Storage for library-built plans, in the PROJECT'S PLANNING FOLDER.

A plan IS the workspace's existing plan items - one ``wireframe`` item per page,
plus the ``flows`` item - exactly as the canvas already reads them. The library
does not keep a parallel plan: dropping a page template creates a ``wireframe``
item, and dropping a widget appends to that item's wire.

    <project>/planning/            the plan items' files (<id>-<slug>.wire, ...)
        library/                   entries specific to THIS project (product:<slug>)
        tickets/                   component-level tickets built from the plan

The files are the source of truth (``planning/files.py``); writes are atomic and
confined to the planning root.

The internal agent's own improvement notes are a DIFFERENT destination - the
platform notes folder used in debug mode - and never belong here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SITEMAP_JSON = "sitemap.json"
SITEMAP_WIRE = "sitemap.wire"
LIBRARY_DIR = "library"
TICKETS_DIR = "tickets"
MAX_FILE_BYTES = 4_000_000


def planning_root(root: str | Path) -> Path:
    """The project's planning folder, created if needed."""
    path = Path(str(root or "")).expanduser()
    if not str(root or "").strip():
        raise ValueError("A planning folder is required.")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _contained(root: Path, target: Path) -> bool:
    """Refuse to write outside the planning folder."""
    try:
        resolved = target.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _atomic_write(target: Path, text: str) -> bool:
    """Write via a temp file + replace, so a crash cannot leave a half file."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(target.parent), prefix=".plan-", delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, target)
        return True
    except OSError:
        logger.warning("plan-store: could not write %s", target, exc_info=True)
        try:
            if "staged" in locals() and staged.exists():
                staged.unlink()
        except OSError:
            pass
        return False


def _read_text(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("plan-store: could not read %s", path, exc_info=True)
        return ""


# ------------------------------------------------------------------ pages

FLOWS_ITEM = "flows"
MAX_FLOWS_BYTES = 200_000


def _flows_item(workspace_id: int) -> dict[str, Any] | None:
    """The workspace's flows item - the sitemap the canvas actually reads."""
    try:
        from distr.core.planning.service import get_workspace

        for item in (get_workspace(int(workspace_id)) or {}).get("items") or []:
            if str(item.get("item_type") or "").lower() == FLOWS_ITEM:
                return item
    except Exception:
        logger.debug("plan-store: could not read the flows item", exc_info=True)
    return None


def list_pages(workspace_id: int) -> list[dict[str, Any]]:
    """Every page in the plan, parsed from the flows wire - the canvas's own view."""
    item = _flows_item(workspace_id)
    if not item:
        return []
    pages: list[dict[str, Any]] = []
    for name, title, route, _ in _screens(str(item.get("content") or "")):
        pages.append({"name": name, "title": title, "route": route})
    return pages


def place_page(workspace_id: int, entry: Any, *, title: str = "") -> dict[str, Any]:
    """Drop a page template: append its screen to the plan's flows wire."""
    wire = str(getattr(entry, "wire", "") or "").strip()
    if not wire:
        return {"placed": False, "error": "the library entry has no wire"}
    if not wire.lstrip().startswith("screen"):
        wire = f'screen "{_title_of(title, entry)}"\n  ' + wire.replace("\n", "\n  ")

    item = _flows_item(workspace_id)
    if not item:
        return {"placed": False, "error": "this plan has no flows item to add pages to"}

    name = _screen_name(wire)
    existing = {page["name"] for page in list_pages(workspace_id)}
    if name and name in existing:
        return {"placed": False, "error": f"a page called {name!r} is already in the plan"}

    content = str(item.get("content") or "").rstrip("\n")
    merged = f"{content}\n\n{wire}\n" if content else f"{wire}\n"
    if len(merged) > MAX_FLOWS_BYTES:
        return {"placed": False, "error": "the plan is at its size limit"}

    saved = _save_flows(item, merged)
    return {**saved, "created_page": name, "page": name}


def place_widget(workspace_id: int, page_name: str, entry: Any) -> dict[str, Any]:
    """Drop a widget into a page: insert it as children of that page's screen."""
    body = str(getattr(entry, "wire", "") or "").strip()
    if not body:
        return {"placed": False, "error": "the library entry has no wire"}
    wanted = _slug(page_name)
    if not wanted:
        return {"placed": False, "error": "no page was named"}

    item = _flows_item(workspace_id)
    if not item:
        return {"placed": False, "error": "this plan has no flows item"}

    content = str(item.get("content") or "")
    lines = content.splitlines()
    bounds = _screen_bounds(lines, wanted)
    if bounds is None:
        return {"placed": False, "error": f"no page called {wanted!r} in the plan"}
    first = body.splitlines()[0].strip()
    start, stop = bounds
    if any(first and first in line for line in lines[start:stop]):
        return {"placed": False, "error": f"{getattr(entry, 'name', 'that')!r} is already on this page"}

    indent = _screen_indent(lines[start]) + "  "
    inserted = [f"{indent}{line.rstrip()}" for line in body.splitlines() if line.strip()]
    merged = "\n".join(lines[:stop] + inserted + lines[stop:]) + "\n"
    if len(merged) > MAX_FLOWS_BYTES:
        return {"placed": False, "error": "the plan is at its size limit"}

    saved = _save_flows(item, merged)
    return {**saved, "page": wanted}


def _save_flows(item: dict[str, Any], content: str) -> dict[str, Any]:
    try:
        from distr.core.planning.service import update_item

        updated = update_item(
            int(item.get("id")),
            content=content,
            expected_revision=int(item.get("revision_count") or 1),
            source="library",
        )
    except Exception as exc:
        logger.warning("plan-store: could not update the flows item", exc_info=True)
        return {"placed": False, "error": str(exc)}
    return {"placed": True, "page": updated}


def _screen_name(wire: str) -> str:
    import re

    match = re.search(r'^\s*screen\s+"([^"]*)"', wire or "", re.MULTILINE)
    return _slug(match.group(1)) if match else ""


def _screen_indent(line: str) -> str:
    return (line or "")[: len(line or "") - len((line or "").lstrip())]


def _screens(content: str) -> list[tuple[str, str, str, int]]:
    """(name, title, route, line_index) for every screen in the wire."""
    import re

    out: list[tuple[str, str, str, int]] = []
    for index, line in enumerate(content.splitlines()):
        match = re.match(r'^(\s*)screen\s+"([^"]*)"(.*)$', line)
        if not match:
            continue
        title = match.group(2)
        rest = match.group(3) or ""
        route = ""
        route_match = re.search(r'route=(?:"([^"]*)"|(\S+))', rest)
        if route_match:
            route = route_match.group(1) or route_match.group(2) or ""
        out.append((_slug(title), title, route, index))
    return out


def _screen_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """Start and end line of a screen block (end exclusive)."""
    start = None
    indent = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("screen ") and _slug(_screen_name(line)) == name:
            start = index
            indent = _screen_indent(line)
            continue
        if start is not None:
            if not stripped:
                continue
            if _screen_indent(line) <= indent and stripped.startswith("screen "):
                return start, index
    return (start, len(lines)) if start is not None else None


def _title_of(title: str, entry: Any) -> str:
    return " ".join(str(title or getattr(entry, "title", "") or getattr(entry, "name", "") or "Page").split())[:120]


def _slug(value: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(value or "").lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:60]


# ------------------------------------------------------------------ library

def project_library_dir(root: str | Path) -> Path | None:
    """``<planning>/library/`` - entries specific to this project."""
    try:
        return planning_root(root) / LIBRARY_DIR
    except (ValueError, OSError):
        return None


def save_project_entry(root: str | Path, entry: Any) -> dict[str, Any]:
    """Write one library entry for this project. Returns where it went."""
    folder = project_library_dir(root)
    if folder is None:
        return {"saved": False, "error": "no planning folder"}

    payload = entry.as_dict() if hasattr(entry, "as_dict") else entry
    if not isinstance(payload, dict):
        return {"saved": False, "error": "not an entry"}
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"saved": False, "error": "no name"}

    target = folder / name / "manifest.json"
    if not _contained(folder, target):
        return {"saved": False, "error": "outside the library folder"}
    if not _atomic_write(target, json.dumps(payload, indent=2) + "\n"):
        return {"saved": False, "error": "write failed"}
    return {"saved": True, "path": str(target)}


# ------------------------------------------------------------------ tickets

def tickets_dir(root: str | Path) -> Path | None:
    """``<planning>/tickets/`` - tickets built from the plan."""
    try:
        return planning_root(root) / TICKETS_DIR
    except (ValueError, OSError):
        return None


def save_tickets(root: str | Path, tickets: Any, *, heading: str = "Plan tickets") -> dict[str, Any]:
    """Write the plan's component-level tickets into the planning folder.

    One markdown file per page, so a ticket can be handed to a developer on its
    own, plus an index. Tickets from a plan belong HERE - the internal agent's
    notes about the Decisions platform itself are a different destination.
    """
    from distr.core.planning.plan_tickets import tickets_markdown

    folder = tickets_dir(root)
    if folder is None:
        return {"saved": False, "error": "no planning folder"}

    items = [t for t in (tickets or []) if isinstance(t, dict)]
    if not _atomic_write(folder / "index.md", tickets_markdown(items, heading=heading)):
        return {"saved": False, "error": "write failed"}

    by_page: dict[str, list[dict[str, Any]]] = {}
    for ticket in items:
        by_page.setdefault(str(ticket.get("page") or "page"), []).append(ticket)

    written: list[str] = []
    for page, page_tickets in sorted(by_page.items()):
        safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in page.lower()).strip("-") or "page"
        target = folder / f"{safe}.md"
        if not _contained(folder, target):
            continue
        body = tickets_markdown(page_tickets, heading=f"Page: {page}")
        if _atomic_write(target, body):
            written.append(target.name)

    return {"saved": True, "dir": str(folder), "pages": written}


def load_tickets_index(root: str | Path) -> str:
    """The tickets index, or "" when there is none."""
    folder = tickets_dir(root)
    if folder is None:
        return ""
    return _read_text(folder / "index.md")
