"""A clean, machine-readable plan an agent can use to build a page or a ticket.

No UI. The same document is returned by GET /plan-workspaces/{id}/blueprint
and by the plan_blueprint agent tool.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_LAYOUT_WIRE_CHARS = 24_000


_ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".json",
}
_ASSET_KIND = {
    ".csv": "csv", ".tsv": "csv", ".xlsx": "spreadsheet", ".xls": "spreadsheet",
    ".pdf": "pdf", ".json": "json", ".svg": "image", ".ico": "image",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
}

# Material-icon tokens sometimes leak into captured button/link labels.
_ICON_TOKEN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"(.*)$')
_ATTR = re.compile(r"""(\w+)=("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\S+)""")
_PUBLIC_LEAVES = {"login", "register", "activate", "forgot-password", "reset-password"}
QUOTE_MARKS = set('"') | set("'")


def _norm_route(route: str) -> str:
    path = str(route or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    return path if path.startswith("/") else f"/{path}"


def _clean_label(label: str) -> str:
    text = html.unescape(str(label or "")).strip()
    words = [word for word in text.split() if not _ICON_TOKEN.fullmatch(word)]
    return " ".join(words).strip() or text


def parse_wire_item(raw: str) -> tuple[str, dict[str, str]]:
    """Split `input "password" type=password` into a clean label and attributes."""
    rest = str(raw or "").strip()
    if " " in rest:
        kind, rest = rest.split(" ", 1)
        if "=" in kind and not kind.startswith(("input", "field", "button", "link", "section", "heading", "screen")):
            rest = str(raw or "").strip()
        rest = rest.strip()
    label = ""
    attrs: dict[str, str] = {}
    quoted = _QUOTED.match(rest)
    if quoted:
        label = _clean_label(quoted.group(1).replace('\\"', '"'))
        rest = quoted.group(2).strip()
    elif rest:
        taken: list[str] = []
        for part in rest.split():
            if "=" in part and part.split("=", 1)[0].isidentifier():
                break
            taken.append(part)
        label = _clean_label(" ".join(taken))
        rest = rest[len(" ".join(taken)):].strip() if taken else rest
    for match in _ATTR.finditer(rest):
        value = match.group(2)
        if len(value) >= 2 and value[0] in QUOTE_MARKS and value[-1] == value[0]:
            value = value[1:-1]
        attrs[match.group(1)] = value
    if not label and attrs.get("name"):
        label = _clean_label(attrs["name"])
    return label, attrs


def _page_access(route: str, auth: bool = False) -> str:
    path = _norm_route(route)
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if path.startswith("/admin") or path.startswith("/account"):
        return "public" if leaf in _PUBLIC_LEAVES else "signed-in"
    return "signed-in" if auth else "public"



def meaningful_sections(page: dict | None) -> list[str]:
    """Drop headings that are just the page title or route — they are not a stencil."""
    page = page or {}
    label = html.unescape(str(page.get("label") or "")).strip()
    route = str(page.get("route") or "")
    skip = {label, route, route + "/", (label + "/") if label else ""}
    out: list[str] = []
    for section in page.get("sections") or []:
        name = html.unescape(str(section)).strip()
        if name and name not in skip and name not in out:
            out.append(name)
    return out


def _cue_fields(cue: dict | None) -> dict[str, list]:
    cue = cue if isinstance(cue, dict) else {}

    def as_field(row) -> dict[str, str] | None:
        if isinstance(row, dict):
            name = _clean_label(row.get("name") or row.get("label") or "")
            if not name:
                return None
            item = {"name": name}
            if row.get("type"):
                item["type"] = str(row["type"])
            return item
        name, attrs = parse_wire_item(f"input {row}" if not str(row).strip().startswith(("input ", "field ")) else str(row))
        if not name:
            name, attrs = parse_wire_item(f'input "{row}"')
        if not name:
            return None
        item = {"name": name}
        if attrs.get("type"):
            item["type"] = attrs["type"]
        return item

    def as_link(row) -> dict[str, str] | None:
        if isinstance(row, dict):
            label = _clean_label(row.get("label") or row.get("name") or "")
            href = row.get("route") or row.get("href") or ""
            route = _norm_route(href) if href else ""
            if not (label or route):
                return None
            return {"label": label or route, "route": route}
        raw = str(row).strip()
        if not raw.startswith("link "):
            raw = f"link {raw if raw.startswith(chr(34)) else chr(34) + raw + chr(34)}"
        label, attrs = parse_wire_item(raw)
        route = _norm_route(attrs["route"]) if attrs.get("route") else ""
        if not (label or route):
            return None
        return {"label": label or route, "route": route}

    def as_label(row) -> str:
        if isinstance(row, dict):
            return _clean_label(row.get("label") or row.get("name") or "")
        text = str(row).strip()
        if text.startswith(("button ", "section ", "heading ", "label ")):
            name, _ = parse_wire_item(text)
            return name
        name, _ = parse_wire_item(f'button "{text}"') if " variant=" in text or text.startswith('"') else (_clean_label(text.split(" variant=")[0].strip().strip('"')), {})
        return name

    fields: list[dict[str, str]] = []
    for row in cue.get("fields") or cue.get("inputs") or []:
        item = as_field(row)
        if item and item not in fields:
            fields.append(item)
    sections: list[str] = []
    for row in cue.get("sections") or cue.get("headings") or cue.get("regions") or []:
        label = as_label(row)
        if label and label not in sections:
            sections.append(label)
    buttons: list[str] = []
    for row in cue.get("buttons") or []:
        label = as_label(row)
        if label and label not in buttons:
            buttons.append(label)
    links: list[dict[str, str]] = []
    for row in cue.get("links") or []:
        item = as_link(row)
        if item and item not in links:
            links.append(item)
    return {"fields": fields, "sections": sections, "buttons": buttons, "links": links}


def project_file_assets(project_root: Path | str | None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Images, CSV, spreadsheets and PDFs in the project, skipping bundled and build trees."""
    if not project_root:
        return []
    root = Path(project_root).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []
    from distr.core.planning.project_scan import _inside_bundled_tree

    skip_dirs = {
        ".git", "node_modules", "__pycache__", ".pytest_cache", ".next",
        "dist", "build", "vendors", "migrations", "planning", "staticfiles",
        "tmp", "venv", ".venv", "coverage", "installer",
        "artifacts", "editor-qa", "screenshots", "playwright-report",
        "test-results", "recordings",
    }

    import os
    found: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if len(found) >= limit:
            break
        here = Path(dirpath)
        try:
            rel_dir = here.relative_to(root)
        except ValueError:
            dirnames[:] = []
            continue
        parts = rel_dir.parts
        if any(part in skip_dirs or part.startswith(".") for part in parts):
            dirnames[:] = []
            continue
        if parts and parts[0] == "planning":
            dirnames[:] = []
            continue
        if _inside_bundled_tree(root, here / "x"):
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and not name.startswith(".")]
        for name in filenames:
            if len(found) >= limit:
                break
            suffix = Path(name).suffix.lower()
            if suffix not in _ASSET_SUFFIXES:
                continue
            path = here / name
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.append({
                "path": rel,
                "kind": _ASSET_KIND.get(suffix, "file"),
                "suffix": suffix,
                "bytes": size,
            })
    found.sort(key=lambda row: row["path"])
    return found[:limit]


def _shots_by_route(status: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, meta in (status.get("shots") or {}).items():
        if not isinstance(meta, dict):
            continue
        route = _norm_route(meta.get("route") or urlparse(str(meta.get("url") or "")).path or "")
        if route and route not in out:
            out[route] = meta
    return out


def _wire_cues(items: list[dict]) -> dict[str, dict]:
    """Best-effort fields/sections already written into the wire stencil for a route."""
    item = next((row for row in items if row.get("content_format") == "wire"), None)
    if not item:
        return {}
    text = str(item.get("content") or "")
    cues: dict[str, dict] = {}
    current_route = ""
    current_label = ""
    fields: list[dict[str, str]] = []
    sections: list[str] = []
    buttons: list[str] = []
    links: list[dict[str, str]] = []
    block: list[str] = []
    layout_meta: dict[str, Any] = {}

    def flush():
        if current_route:
            cues[current_route] = {
                "label": current_label,
                "fields": fields[:],
                "sections": sections[:],
                "buttons": buttons[:],
                "links": links[:],
            }
            if layout_meta:
                # The page's layout rules, so Build can regenerate this page from the stencil alone.
                cues[current_route]["layout"] = {**layout_meta, "wire": "\n".join(block)[:_LAYOUT_WIRE_CHARS]}

    for line in text.splitlines():
        raw = line.strip()
        if raw.startswith("screen "):
            flush()
            fields, sections, buttons, links = [], [], [], []
            block, layout_meta = [line], {}
            if " layout=v2" in raw:
                layout_meta = {"version": "v2"}
                for key in ("vw", "ph"):
                    found = re.search(rf"\b{key}=(\d+)", raw)
                    if found:
                        layout_meta[key] = int(found.group(1))
            current_route, current_label = "", ""
            if raw.startswith('screen "') and raw.count('"') >= 2:
                current_label = raw.split('"', 2)[1]
            if "route=" in raw:
                current_route = _norm_route(raw.split("route=", 1)[1].split()[0].strip().strip('"').strip("'"))
            if current_label in {current_route, current_route + "/", ""}:
                current_label = current_route
            continue
        if block and raw and not raw.startswith("#"):
            block.append(line)
        if raw.startswith(("input ", "field ")):
            name, attrs = parse_wire_item(raw)
            if name:
                item = {"name": name}
                if attrs.get("type"):
                    item["type"] = attrs["type"]
                fields.append(item)
        elif raw.startswith(("section ", "heading ")):
            label, _ = parse_wire_item(raw)
            route_like = label in {"", current_route, current_route + "/", f"{current_route}/"}
            if label and not route_like:
                sections.append(label)
                if current_label in {"", current_route, current_route + "/"}:
                    current_label = label
        elif raw.startswith("button "):
            label, _ = parse_wire_item(raw)
            if label:
                buttons.append(label)
        elif raw.startswith("link "):
            label, attrs = parse_wire_item(raw)
            route = _norm_route(attrs["route"]) if attrs.get("route") else ""
            if label or route:
                links.append({"label": label or route, "route": route})
    flush()
    return cues


def _erd_from_items(items: list[dict]) -> dict[str, Any]:
    item = next((row for row in items if row.get("item_type") in {"architecture", "erd", "diagram"}
                 and row.get("content_format") == "mermaid"), None)
    if not item:
        return {"tables": [], "relations": [], "title": ""}
    from distr.core.planning.db_erd import erd_read_view

    model = erd_read_view(item.get("content") or "") or {"tables": [], "relations": []}
    tables = []
    for table in model.get("tables") or []:
        tables.append({
            "name": table.get("name"),
            "fields": [{"name": f.get("name"), "type": f.get("type"), "keys": f.get("keys") or []}
                       for f in table.get("fields") or []],
        })
    relations = []
    for rel in model.get("relations") or []:
        relations.append({
            "from": rel.get("from"), "to": rel.get("to"),
            "label": rel.get("label") or "", "left": rel.get("left"), "right": rel.get("right"),
        })
    return {"tables": tables, "relations": relations, "title": item.get("title") or "Architecture and ERD",
            "item_id": item.get("id")}


def _page_id(node_id: str, route: str) -> str:
    ident = str(node_id or "").strip()
    if ident and not ident.startswith("missing-"):
        return ident
    slug = route.strip("/").replace("/", "-") or "home"
    return slug


def _shot_payload(shot: dict, route: str) -> dict[str, str] | None:
    if not shot:
        return None
    return {
        "name": shot.get("name") or "",
        "captured_at": shot.get("captured_at") or "",
        "image": shot.get("image") or "",
        "route": shot.get("route") or route,
    }


def plan_blueprint(workspace_id: int) -> dict[str, Any]:
    """Pages, stencils, links, the real ERD and project assets — enough to build from."""
    from distr.core.planning import iface_shots, service

    detail = service.get_workspace(int(workspace_id), include_data_model=True)
    if detail is None:
        raise LookupError("Plan workspace not found.")
    try:
        status = iface_shots.interface_shot_status(int(workspace_id))
    except Exception:
        status = {"shots": {}, "saved": []}
    sitemap = detail.get("sitemap") or {}
    items = detail.get("items") or []
    wire_cues = _wire_cues(items)
    shots_by_route = _shots_by_route(status)
    pages = []
    for node in sitemap.get("nodes") or []:
        if node.get("kind") not in {None, "page"}:
            continue
        route = _norm_route(node.get("route") or "")
        if not route:
            continue
        shot = shots_by_route.get(route) or {}
        raw_cue = wire_cues.get(route) or {}
        cue = _cue_fields(raw_cue)
        access = _page_access(route, bool(node.get("auth")))
        status_name = node.get("status") or ""
        if status_name in {"missing", "incomplete", ""} and raw_cue:
            status_name = "wired"
        label = raw_cue.get("label") or node.get("label") or route
        if label in {route, route + "/"}:
            label = route
        pages.append({
            "id": _page_id(node.get("id"), route),
            "label": label,
            "route": route,
            "access": access,
            "status": status_name,
            "template": node.get("template") or "",
            "view": node.get("view") or "",
            "auth": access == "signed-in",
            "fields": cue["fields"],
            "sections": cue["sections"],
            "buttons": cue["buttons"],
            "links": cue["links"],
            "layout": raw_cue.get("layout"),
            "shot": _shot_payload(shot, route),
        })
    known = {p["route"] for p in pages}
    for route, cue in wire_cues.items():
        if route in known:
            continue
        shot = shots_by_route.get(route) or {}
        access = _page_access(route, False)
        label = cue.get("label") or route
        if label in {route, route + "/"}:
            label = route
        pages.append({
            "id": _page_id("", route),
            "label": label,
            "route": route,
            "access": access,
            "status": "wired",
            "template": "",
            "view": "",
            "auth": access == "signed-in",
            "fields": cue.get("fields") or [],
            "sections": cue.get("sections") or [],
            "buttons": cue.get("buttons") or [],
            "links": cue.get("links") or [],
            "layout": cue.get("layout"),
            "shot": _shot_payload(shot, route),
        })
    links = []
    for edge in sitemap.get("edges") or []:
        links.append({
            "from": edge.get("from"), "to": edge.get("to"),
            "kind": edge.get("kind") or "", "label": edge.get("label") or "",
        })
    erd = _erd_from_items(items)
    project_folder = detail.get("project_folder") or ""
    assets = project_file_assets(project_folder)
    coverage = {
        "sitemap": sitemap.get("coverage") or "missing",
        "note": sitemap.get("note") or "",
        "page_count": len(pages),
        "pages_with_fields": sum(1 for p in pages if p.get("fields") or p.get("buttons") or meaningful_sections(p)),
        "pages_with_shots": sum(1 for p in pages if p.get("shot")),
        "table_count": len(erd["tables"]),
        "asset_count": len(assets),
    }
    complete = coverage["sitemap"] in {"verified", "inferred"} and coverage["page_count"] > 0
    coverage["state"] = "complete" if complete else ("none" if not coverage["page_count"] else "partial")
    return {
        "workspace_id": int(workspace_id),
        "project_id": detail.get("project_id"),
        "board": detail.get("board_name"),
        "project_folder": project_folder,
        "root_path": detail.get("root_path") or "",
        "coverage": coverage,
        "pages": pages,
        "links": links,
        "erd": erd,
        "assets": assets,
        "endpoints": sitemap.get("endpoints") or [],
    }


def _field_line(field) -> str:
    if isinstance(field, dict):
        name = field.get("name") or ""
        typ = field.get("type") or ""
        return f"{name} ({typ})" if typ and typ != "text" else name
    return str(field)


def _link_line(link) -> str:
    if isinstance(link, dict):
        label = link.get("label") or link.get("route") or ""
        route = link.get("route") or ""
        return f"{label} → `{route}`" if route and label != route else (f"`{route}`" if route else label)
    return str(link)


def page_ticket_draft(workspace_id: int, route: str) -> dict[str, Any]:
    """A non-mutating, actionable ticket body for one page, built from the blueprint."""
    route = _norm_route(route)
    blueprint = plan_blueprint(workspace_id)
    page = next((p for p in blueprint["pages"] if p["route"] == route), None)
    if page is None:
        page = next((p for p in blueprint["pages"] if p["route"].startswith(route) or route.startswith(p["route"])), None)
    if page is None:
        raise LookupError(f"No page in this plan matches {route}.")
    erd = blueprint.get("erd") or {}
    table_count = len(erd.get("tables") or [])
    relation_count = len(erd.get("relations") or [])
    fields = page.get("fields") or []
    sections = meaningful_sections(page)
    buttons = page.get("buttons") or []
    links = page.get("links") or []
    stencil_ready = bool(fields or sections or buttons)
    lines = [
        f"# {page['label']}",
        "",
        f"Route: `{page['route']}`",
        f"Access: {page.get('access') or 'public'}",
    ]
    if page.get("template"):
        lines.append(f"Template: `{page['template']}`")
    if page.get("view"):
        lines.append(f"View: `{page['view']}`")
    if page.get("status"):
        lines.append(f"Status: {page['status']}")
    lines += ["", "## Stencil"]
    if sections:
        lines.append("Sections: " + ", ".join(sections))
    if fields:
        lines.append("Fields:")
        lines.extend(f"- {_field_line(name)}" for name in fields)
    if buttons:
        lines.append("Actions: " + ", ".join(buttons))
    if links:
        lines.append("Links: " + ", ".join(_link_line(link) for link in links))
    if not stencil_ready:
        lines.append("No fields or sections are recorded for this page yet. Recapture or rescan before building.")
    layout = page.get("layout") or {}
    if layout.get("wire"):
        lines += ["", "## Layout (build to these rules)",
                  f"Layout rules read from the real page at {layout.get('vw') or 1440}px wide, with phone overrides in `at768`. "
                  "Containers carry flow, columns, gaps, widths and alignment; text and images are grey blocks, so the "
                  "base template supplies colours and typography.", "", "```", layout["wire"][:6000], "```"]
    if page.get("shot"):
        lines += ["", f"Screenshot: {page['shot'].get('name') or ''} captured {page['shot'].get('captured_at') or 'unknown'}."]
    if table_count:
        lines += [
            "",
            "## Data model",
            f"This project has {table_count} tables and {relation_count} relations. "
            "Use the plan blueprint `erd` object for field names, types, and keys.",
        ]
    access = page.get("access") or "public"
    who = "a signed-in user" if access == "signed-in" else "a visitor"
    acceptance = ["", "## Acceptance"]
    if stencil_ready:
        acceptance.append(
            f"- Given {who} lands on `{page['route']}`, when the page loads, then the recorded stencil fields and actions are present."
        )
        if fields:
            acceptance.append(
                "- Given a required field is left empty, when the form is submitted, then the page explains what to fix and does not save."
            )
        if links:
            acceptance.append(
                "- Given the linked pages above, when a user follows them, then each opens the matching route in this plan."
            )
    else:
        acceptance.append(
            f"- Recapture `{page['route']}` (or rebuild the plan wire) so the stencil lists the real fields before this ticket is built."
        )
    lines += acceptance
    if stencil_ready:
        ready_reason = "stencil recorded"
    elif page.get("shot"):
        ready_reason = "screenshot only; recapture the stencil before building"
    else:
        ready_reason = "no stencil or screenshot yet"
    return {
        "workspace_id": int(workspace_id),
        "board": blueprint.get("board"),
        "route": page["route"],
        "title": f"{page['label']} — build from the plan stencil",
        "description": "\n".join(lines),
        "page": page,
        "ready": stencil_ready,
        "ready_reason": ready_reason,
        "action": "preview",
    }
