"""Tickets from a plan: component-level operations, not prose.

This is the payoff of the library. Because a plan is built from named library
entries, a ticket can say exactly what to change:

    Add a KPI row to the Dashboard page (library: kpi-row)

instead of a paragraph of description that a developer has to interpret. The
ticket is a **diff against real components**: it names the action (add / update /
remove), the page, and the library entry.

Tickets are produced by comparing the plan against what already exists, so the
same call handles "new page", "new component on an existing page" and "component
removed".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from distr.core.planning.page import Block  # noqa: E402  (kept after the logger for readability)

ACTION_ADD = "add"
ACTION_UPDATE = "update"
ACTION_REMOVE = "remove"
ACTIONS = (ACTION_ADD, ACTION_UPDATE, ACTION_REMOVE)

MAX_TICKETS = 200


@dataclass(frozen=True)
class PlanTicket:
    """One component-level change."""

    action: str
    page: str
    component: str
    title: str
    body: str
    kind: str = "widget"
    scope: str = "global"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "page": self.page,
            "component": self.component,
            "title": self.title,
            "body": self.body,
            "kind": self.kind,
            "scope": self.scope,
        }

    def to_markdown(self) -> str:
        return f"## {self.title}\n\n{self.body}\n"


def _blocks_of(page: Any, *, library_module: Any = None) -> list[str]:
    """The component refs a page needs.

    A page-template block expands to the template's declared ``components``, so a
    plan is diffed at component level rather than against the template's name.
    """
    refs: list[str] = []
    for block in (getattr(page, "blocks", ()) or []):
        ref = str(getattr(block, "ref", "") or "").strip()
        if not ref:
            continue
        entry = _safe_get(library_module, ref) if library_module is not None else None
        components = tuple(getattr(entry, "components", ()) or ()) if entry is not None else ()
        for component in components or (ref,):
            component = str(component or "").strip()
            if component and component not in refs:
                refs.append(component)
    return refs


def _component_title(entry: Any, ref: str) -> str:
    title = str(getattr(entry, "title", "") or "").strip()
    return title or ref.replace("-", " ").title()


def _ticket_body(*, action: str, page: Any, component: str, entry: Any, extra: str = "") -> str:
    page_title = str(getattr(page, "title", "") or getattr(page, "name", "") or "the page")
    lines = [f"- **Action:** {action}", f"- **Page:** {page_title}"]
    route = str(getattr(page, "route", "") or "").strip()
    if route:
        lines.append(f"- **Route:** `{route}`")
    lines.append(f"- **Library entry:** `{component}`")
    kind = str(getattr(entry, "kind", "") or "").strip()
    if kind:
        lines.append(f"- **Kind:** {kind}")
    scope = str(getattr(entry, "scope", "") or "").strip()
    if scope and scope != "global":
        lines.append(f"- **Scope:** {scope}")
    description = str(getattr(entry, "description", "") or "").strip()
    if description:
        lines.append(f"- **What it is:** {description}")
    wire = str(getattr(entry, "wire", "") or "").strip()
    if wire:
        lines.append("")
        lines.append("Library wire:")
        lines.append("")
        lines.append("```")
        lines.append(wire)
        lines.append("```")
    if extra:
        lines.append("")
        lines.append(extra)
    return "\n".join(lines)


def tickets_for_sitemap(
    sitemap: Any,
    *,
    library_module: Any = None,
    current: dict[str, list[str]] | None = None,
    changed: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Component-level tickets for a plan.

    ``current`` maps a page name to the component refs that page already has, so
    the same call produces add / remove. Omitted means "nothing exists yet", so
    every component is an add.

    A component that is already present is **not** a ticket - there is nothing to
    do. ``changed`` names components whose spec actually changed, and only those
    become updates. Without it, "present" and "unchanged" are indistinguishable,
    and emitting an update for every component would bury the real work.
    """
    if library_module is None:
        from distr.core.planning import library as library_module  # type: ignore[no-redef]

    existing: dict[str, list[str]] = {}
    if isinstance(current, dict):
        for key, value in current.items():
            name = str(key or "").strip().lower()
            if not name:
                continue
            existing[name] = [str(v or "").strip() for v in (value or []) if str(v or "").strip()]

    changed_refs = {str(c or "").strip() for c in (changed or []) if str(c or "").strip()}
    tickets: list[PlanTicket] = []
    seen_pages: set[str] = set()

    for node in getattr(sitemap, "all_nodes", lambda: [])():
        page = getattr(node, "page", None)
        if page is None:
            continue
        name = str(getattr(page, "name", "") or "").strip().lower()
        if not name or name in seen_pages:
            continue
        seen_pages.add(name)

        wanted = _blocks_of(page, library_module=library_module)
        have = existing.get(name, [])
        page_is_new = name not in existing

        # Removals first: what the page no longer has.
        for ref in have:
            if ref not in wanted:
                entry = _safe_get(library_module, ref)
                tickets.append(PlanTicket(
                    action=ACTION_REMOVE,
                    page=name,
                    component=ref,
                    title=f"Remove {_component_title(entry, ref)} from {getattr(page, 'title', name)}",
                    body=_ticket_body(action=ACTION_REMOVE, page=page, component=ref, entry=entry),
                    kind=str(getattr(entry, "kind", "") or "widget"),
                    scope=str(getattr(entry, "scope", "") or "global"),
                ))

        for ref in wanted:
            entry = _safe_get(library_module, ref)
            if entry is None:
                # A plan referencing something the library does not have is a gap
                # worth a ticket of its own, not a silent omission.
                tickets.append(PlanTicket(
                    action=ACTION_ADD,
                    page=name,
                    component=ref,
                    title=f"Add {ref} to {getattr(page, 'title', name)} (missing from the library)",
                    body=(
                        f"- **Action:** {ACTION_ADD}\n- **Page:** {getattr(page, 'title', name)}\n"
                        f"- **Library entry:** `{ref}` (NOT FOUND)\n\n"
                        "This component is referenced by the plan but is not in the library. "
                        "Add it to the library first, or correct the plan."
                    ),
                    kind="widget",
                ))
                continue

            if page_is_new or ref not in have:
                action = ACTION_ADD
            elif ref in changed_refs:
                action = ACTION_UPDATE
            else:
                continue  # already present and unchanged: nothing to do

            tickets.append(PlanTicket(
                action=action,
                page=name,
                component=ref,
                title=f"{action.capitalize()} {_component_title(entry, ref)} on {getattr(page, 'title', name)}",
                body=_ticket_body(action=action, page=page, component=ref, entry=entry),
                kind=str(getattr(entry, "kind", "") or "widget"),
                scope=str(getattr(entry, "scope", "") or "global"),
            ))

        if len(tickets) >= MAX_TICKETS:
            logger.warning("plan-tickets: hit the %d ticket cap", MAX_TICKETS)
            break

    return [t.as_dict() for t in tickets[:MAX_TICKETS]]


def tickets_for_pages(
    pages: list[dict[str, Any]],
    *,
    library_module: Any = None,
    current: dict[str, list[str]] | None = None,
    changed: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Component-level tickets for a plan given as pages.

    ``pages`` is the plan as the canvas holds it (name, title, route, components).
    This is the shape the library actually persists, so tickets come from the real
    plan rather than a parallel model.
    """
    class _Node:
        __slots__ = ("page", "children")

        def __init__(self, page: Any) -> None:
            self.page = page
            self.children: list[Any] = []

    class _Page:
        __slots__ = ("name", "title", "route", "blocks", "base", "overrides")

        def __init__(self, row: dict[str, Any]) -> None:
            self.name = str(row.get("name") or "").strip().lower()
            self.title = str(row.get("title") or self.name)
            self.route = str(row.get("route") or "")
            self.base = row.get("base")
            self.overrides = row.get("overrides") or {}
            refs = row.get("components")
            if refs is None:
                refs = [b for b in (row.get("blocks") or [])]
            self.blocks = tuple(Block(ref=str(r)) for r in refs if str(r or "").strip())

    class _Sitemap:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._nodes = [_Node(_Page(r)) for r in rows if isinstance(r, dict)]

        def all_nodes(self) -> list[Any]:
            return self._nodes

    return tickets_for_sitemap(_Sitemap(pages or []), library_module=library_module, current=current, changed=changed)


def _safe_get(library_module: Any, ref: str) -> Any:
    try:
        return library_module.get(ref)
    except Exception:
        logger.debug("plan-tickets: library lookup failed for %r", ref, exc_info=True)
        return None


def tickets_markdown(tickets: Iterable[dict[str, Any]], *, heading: str = "Plan tickets") -> str:
    """Render tickets as one markdown document, grouped by page."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets or []:
        grouped.setdefault(str(ticket.get("page") or "page"), []).append(ticket)

    out = [f"# {heading}", ""]
    for page in sorted(grouped):
        out.append(f"## Page: {page}")
        out.append("")
        for ticket in grouped[page]:
            out.append(f"### {ticket.get('title') or ticket.get('component')}")
            out.append("")
            out.append(str(ticket.get("body") or "").strip())
            out.append("")
    return "\n".join(out).rstrip() + "\n"
