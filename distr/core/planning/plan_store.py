"""File-backed storage for library-built plans, inside the PROJECT'S PLANNING FOLDER.

Everything a plan is made of lives in ``<project>/planning/`` and is read back
from there - not from the database and not from a downloads folder:

    <project>/planning/
        sitemap.json        the structured plan: pages, blocks, base overrides
        sitemap.wire        the rendered wireframe document (human-readable)
        library/            entries specific to THIS project (scope product:<slug>)
        tickets/            component-level tickets built from the plan

This mirrors the existing planning convention (plan items are files such as
``174-flows-and-wireframes.wire``, with the database holding only metadata), and
follows ``planning/files.py``: writes are atomic and confined to the planning
root.

The internal agent's own improvement notes are a DIFFERENT destination - see
``docs``/debug mode - and never belong here.
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


# ------------------------------------------------------------------ sitemap

def save_sitemap(root: str | Path, sitemap: Any, *, wire: str = "") -> dict[str, Any]:
    """Persist a sitemap (and its rendered wire) into the planning folder."""
    try:
        base = planning_root(root)
    except (ValueError, OSError) as exc:
        return {"saved": False, "error": str(exc)}

    target = base / SITEMAP_JSON
    if not _contained(base, target):
        return {"saved": False, "error": "outside the planning folder"}

    payload = sitemap.as_dict() if hasattr(sitemap, "as_dict") else sitemap
    if not _atomic_write(target, json.dumps(payload, indent=2) + "\n"):
        return {"saved": False, "error": "write failed"}

    if wire:
        _atomic_write(base / SITEMAP_WIRE, wire)
    return {"saved": True, "path": str(target)}


def load_sitemap(root: str | Path) -> Any:
    """Read the sitemap back. An absent or broken file is an empty sitemap."""
    from distr.core.planning import sitemap as sitemap_module

    try:
        base = planning_root(root)
    except (ValueError, OSError):
        return sitemap_module.Sitemap()

    text = _read_text(base / SITEMAP_JSON)
    if not text:
        return sitemap_module.Sitemap()
    try:
        payload = json.loads(text)
    except ValueError:
        logger.warning("plan-store: %s is not valid JSON; treating it as empty", base / SITEMAP_JSON)
        return sitemap_module.Sitemap()
    return sitemap_module.parse_sitemap(payload)


def sitemap_wire_path(root: str | Path) -> Path | None:
    """Where the rendered wire lives, or None when there is no planning folder."""
    try:
        return planning_root(root) / SITEMAP_WIRE
    except (ValueError, OSError):
        return None


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
