"""Derive planning documents from an approved Plan wire design (New Work workflow).

Deterministic, no LLM: screens (label, route, id, ``#|`` descriptions), their controls,
and ``bind=`` attributes become PRD, FRAC and an Architecture ERD. FR keys are stable
across regeneration because each FR heading carries ``<!-- screen:<key> -->``.
Written through ``service.apply_agent_edits`` with source ``design`` (origin ``new``),
so the usual validation and ownership rules apply. A section whose latest revision is a
manual edit (origin ``user``) is never overwritten; it is reported as skipped.
"""
from __future__ import annotations

import re
from typing import Any

from distr.core.planning.interface_conventions import screens as wire_screens
from distr.core.planning.provenance import origin_of

_CONTROL = re.compile(r'^\s*(button|link|input|select|checkbox|radio|toggle|textarea|form|table|list)\b\s*(?:"((?:[^"\\]|\\.)*)")?(.*)$')
_BIND = re.compile(r'\bbind(?:ing)?=(?:"([^"]+)"|(\S+))')
_FR_HEAD = re.compile(r"^##\s+(FR-\d{3,})\b.*<!--\s*screen:([^\s>]+)\s*-->", re.M)
_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _screen_blocks(content: str) -> list[dict[str, Any]]:
    lines = str(content or "").split("\n")
    found = wire_screens(content)
    out = []
    for position, screen in enumerate(found):
        start = screen["index"] + 1
        end = found[position + 1]["index"] if position + 1 < len(found) else len(lines)
        controls, binds = [], []
        for line in lines[start:end]:
            if line.lstrip().startswith("#"):
                continue
            m = _CONTROL.match(line)
            if m and m.group(2):
                controls.append((m.group(1), m.group(2)))
            for b in _BIND.finditer(line):
                binds.append(b.group(1) or b.group(2))
        key = screen.get("id") or screen.get("route") or screen.get("label") or f"screen-{position + 1}"
        out.append({"key": str(key), "label": screen.get("label") or str(key), "route": screen.get("route") or "",
                    "desc": " ".join(screen.get("desc") or []).strip(), "controls": controls, "binds": binds})
    return out


def design_screens(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for item in items:
        if item.get("item_type") in ("wireframe", "flows") and item.get("content_format") == "wire":
            for block in _screen_blocks(item.get("content") or ""):
                if block["key"] in seen:
                    continue
                seen.add(block["key"])
                out.append(block)
    return out


def _stable_keys(existing_frac: str, blocks: list[dict[str, Any]]) -> dict[str, str]:
    keys = {screen: fr for fr, screen in _FR_HEAD.findall(existing_frac or "")}
    used = set(keys.values())
    number = max([int(k[3:]) for k in used] or [0])
    for block in blocks:
        if block["key"] not in keys:
            number += 1
            while f"FR-{number:03d}" in used:
                number += 1
            keys[block["key"]] = f"FR-{number:03d}"
            used.add(keys[block["key"]])
    return keys


def build_design_frac(blocks: list[dict[str, Any]], keys: dict[str, str]) -> str:
    lines = ["# Functional requirements and acceptance criteria", "",
             "Generated from the approved Plan design (origin: new). Completion source of truth: global `completion-protocol` skill.", ""]
    for block in blocks:
        fr = keys[block["key"]]
        route = f" (`{block['route']}`)" if block["route"] else ""
        lines += [f"## {fr} {block['label']}{route} <!-- screen:{block['key']} -->", "",
                  block["desc"] or f"The {block['label']} page is available and does what its design shows.", ""]
        n = 1
        lines.append(f"- AC-{fr[3:]}.{n}: Given a user opens {block['route'] or block['label']}, when the page loads, then it shows the "
                     f"{block['label']} content as designed, including empty, loading and error states.")
        for kind, label in block["controls"][:12]:
            n += 1
            verb = {"button": "activates", "link": "follows", "input": "fills", "textarea": "fills", "select": "chooses",
                    "checkbox": "toggles", "radio": "chooses", "toggle": "toggles", "form": "submits", "table": "reviews",
                    "list": "reviews"}.get(kind, "uses")
            lines.append(f"- AC-{fr[3:]}.{n}: Given the {block['label']} page, when the user {verb} \"{label}\", then the "
                         f"expected result is shown and invalid input is explained.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_design_prd(blocks: list[dict[str, Any]], keys: dict[str, str], project_name: str) -> str:
    lines = ["# Product requirements", "", "## Outcome", "",
             f"Deliver the pages designed in Plan for **{project_name}** (origin: new).", "", "## Requirements", ""]
    for block in blocks:
        route = f" — `{block['route']}`" if block["route"] else ""
        lines.append(f"- {keys[block['key']]}: {block['label']}{route}. {block['desc']}".rstrip())
    lines += ["", "## Open questions", "", "- Confirm users, permissions and data retention for each page.", ""]
    return "\n".join(lines)


def build_design_erd(blocks: list[dict[str, Any]]) -> str | None:
    entities: dict[str, set[str]] = {}
    for block in blocks:
        for bind in block["binds"]:
            entity, _, field = str(bind).partition(".")
            entity = _SAFE.sub("", entity)  # Mermaid identifiers are case-sensitive; binds must resolve
            field = _SAFE.sub("", field) or "id"
            if entity:
                entities.setdefault(entity, {"id"}).add(field)
    if not entities:
        return None
    lines = ["erDiagram"]
    for entity in sorted(entities):
        lines.append(f"    {entity} {{")
        for field in sorted(entities[entity]):
            lines.append(f"        string {field}{' PK' if field == 'id' else ''}")
        lines.append("    }")
    return "\n".join(lines) + "\n"


def derive_from_design(workspace_id: int) -> dict[str, Any]:
    from distr.core.planning import service

    detail = service.get_workspace(int(workspace_id))
    if detail is None:
        raise LookupError("Plan workspace not found.")
    items = detail.get("items") or []
    blocks = design_screens(items)
    if not blocks:
        raise ValueError("No designed screens found. Design the pages in Plan Interfaces first.")
    by_type: dict[str, dict[str, Any]] = {}
    for item in items:
        by_type.setdefault(str(item.get("item_type")), item)
    keys = _stable_keys((by_type.get("frac") or {}).get("content") or "", blocks)
    project_name = str(detail.get("board_name") or "the project")
    wanted = {"frac": ("Functional requirements", build_design_frac(blocks, keys), "markdown"),
              "prd": ("Product requirements", build_design_prd(blocks, keys, project_name), "markdown")}
    erd = build_design_erd(blocks)
    # Only create a starter ERD when the plan has none; never replace a richer data model.
    if erd and not by_type.get("architecture") and not by_type.get("erd"):
        wanted["architecture"] = ("Architecture and ERD", erd, "mermaid")
    edits, skipped, revisions = [], [], {}
    for item in items:
        revisions[str(item["id"])] = int(item.get("revision_count") or 0)
    for item_type, (title, content, fmt) in wanted.items():
        current = by_type.get(item_type)
        if current and origin_of(current.get("last_source")) == "user":
            skipped.append(item_type)
            continue
        if current and (current.get("content") or "") == content:
            continue
        edit = {"item_type": item_type, "title": title, "content": content, "content_format": fmt}
        if current:
            if current.get("content_format") != fmt:
                skipped.append(item_type)
                continue
            edit["id"] = int(current["id"])
        edits.append(edit)
    results = service.apply_agent_edits(int(workspace_id), edits=edits, expected_revisions=revisions,
                                        instruction="Derived from the approved Plan design", source="design") if edits else []
    return {"screens": len(blocks), "requirement_keys": [keys[b["key"]] for b in blocks],
            "updated": [r.get("item_type") for r in results], "skipped_manual": skipped}
