"""Plan tools for workflow steps (Decisions MCP / dev_agent_tools), 2026-11-11.

Workflow CLI steps run inside the project folder; these tools resolve that folder to its
Decisions project and board Plan workspace and act only through the Plan services
(validated edits, revision checks, ownership rules). They never write repository files
directly; Plan's own managed projection rules still apply to ordinary plan sections, and
the Requirements Pack is DB-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from distr.core.planning.provenance import origin_of

CONTENT_LIMIT = 20000
WRITE_SOURCES = {"derived": "workflow_derived", "new": "workflow_new"}


def _ok(tool: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "summary": summary, "artifacts": [], **extra}


def _fail(tool: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "summary": summary, "artifacts": [], **extra}


def resolve_workspace(project_folder: Optional[str]) -> int:
    """Project folder -> Plan workspace id (creating the board plan workspace when the board exists)."""
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.db.workflow import PlanWorkspace
    from distr.core.planning import service

    if not project_folder:
        raise LookupError("No project folder for this step.")
    target = Path(project_folder).expanduser().resolve()
    with service.get_session() as db:
        best = None
        for project in db.query(Project).filter(Project.folder_location.isnot(None)).all():
            if getattr(project, "deleted_at", None) is not None:
                continue
            try:
                root = Path(project.folder_location).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if target == root or root in target.parents:
                if best is None or len(str(root)) > len(str(best[1])):
                    best = (project, root)
        if best is None:
            raise LookupError("This folder is not linked to a Decisions project.")
        project = best[0]
        workspace = (db.query(PlanWorkspace).filter(PlanWorkspace.project_id == project.id, PlanWorkspace.status != "archived")
                     .order_by(PlanWorkspace.modified_at.desc(), PlanWorkspace.id.desc()).first())
        if workspace is not None:
            return int(workspace.id)
        board = db.query(KanbanBoard).filter(KanbanBoard.default_project_id == project.id).order_by(KanbanBoard.id).first()
        if board is None:
            raise LookupError("The project has no board yet, so it has no Plan. Link a board to the project first.")
        board_key, board_name, project_id = f"decisions:{board.id}", board.name or project.name, int(project.id)
    return int(service.ensure_workspace(board_key=board_key, board_provider="decisions", board_name=board_name,
                                        project_id=project_id)["id"])


def _revisions(detail: dict[str, Any]) -> dict[str, int]:
    return {str(item["id"]): int(item.get("revision_count") or 0) for item in detail.get("items") or []}


def plan_snapshot(*, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning import service
    from distr.core.planning.requirements_pack import ledger_from_items

    try:
        workspace_id = resolve_workspace(project_folder)
    except LookupError as exc:
        return _fail("plan_snapshot", str(exc))
    detail = service.get_workspace(workspace_id) or {}
    items = [{"id": i["id"], "item_type": i["item_type"], "title": i["title"], "content_format": i["content_format"],
              "revision": i.get("revision_count"), "origin": i.get("origin") or origin_of(i.get("last_source")),
              "last_generated_at": i.get("last_generated_at"), "content": (i.get("content") or "")[:CONTENT_LIMIT],
              "content_truncated": len(i.get("content") or "") > CONTENT_LIMIT} for i in detail.get("items") or []]
    ledger = ledger_from_items(detail.get("items") or [])
    return _ok("plan_snapshot", f"Plan #{workspace_id} has {len(items)} sections; requirements {ledger['counts']}.",
               workspace_id=workspace_id, board=detail.get("board_name"), items=items, requirements=ledger)


def plan_scan_project(force: bool = False, *, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning import service

    try:
        workspace_id = resolve_workspace(project_folder)
        service.ensure_plan_scaffold(workspace_id)
        result = service.materialize_project_scan(workspace_id, force=bool(force), instruction="Existing Project workflow scan")
    except (LookupError, ValueError) as exc:
        return _fail("plan_scan_project", str(exc))
    updated = result.get("updated") or []
    return _ok("plan_scan_project", f"Scanned the project; updated {', '.join(map(str, updated)) or 'nothing'}.",
               workspace_id=workspace_id, updated=updated, skipped=result.get("skipped") or [],
               validation_warnings=result.get("validation_warnings") or [], summary_detail=result.get("summary"))


def plan_write_artifacts(edits: list, origin: str = "new", *, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning import service

    source = WRITE_SOURCES.get(str(origin or "new").strip().lower())
    if source is None:
        return _fail("plan_write_artifacts", "origin must be 'derived' or 'new'.")
    if not isinstance(edits, list) or not edits:
        return _fail("plan_write_artifacts", "Provide a non-empty list of edits.")
    try:
        workspace_id = resolve_workspace(project_folder)
        detail = service.get_workspace(workspace_id) or {}
        manual = {int(i["id"]) for i in detail.get("items") or [] if (i.get("origin") or origin_of(i.get("last_source"))) == "user"}
        blocked = [e.get("id") for e in edits if isinstance(e, dict) and e.get("id") in manual]
        if blocked:
            return _fail("plan_write_artifacts", "These sections were edited by Paul and are kept as overrides: "
                         + ", ".join(f"#{i}" for i in blocked) + ". Leave them or ask Paul.")
        clean = [dict(e) for e in edits]
        for edit in clean:
            if isinstance(edit, dict) and edit.get("id") is None and not edit.get("content_format"):
                edit["content_format"] = "markdown"
        results = service.apply_agent_edits(workspace_id, edits=clean, expected_revisions=_revisions(detail),
                                            instruction="Workflow step", source=source)
    except (LookupError, ValueError, PermissionError) as exc:
        return _fail("plan_write_artifacts", f"Not saved: {exc}")
    warnings = [f"{r.get('item_type')} #{r.get('id')}: {w}" for r in results for w in r.get("validation_warnings") or []]
    return _ok("plan_write_artifacts", f"Saved {len(results)} plan artifact(s).", workspace_id=workspace_id,
               saved=[{"id": r["id"], "item_type": r["item_type"], "revision": r.get("revision_count")} for r in results],
               validation_warnings=warnings)


def plan_derive_from_design(*, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning.derive import derive_from_design

    try:
        workspace_id = resolve_workspace(project_folder)
        result = derive_from_design(workspace_id)
    except (LookupError, ValueError) as exc:
        return _fail("plan_derive_from_design", str(exc))
    return _ok("plan_derive_from_design", f"Derived {', '.join(result['updated']) or 'no changes'} from {result['screens']} screens.",
               workspace_id=workspace_id, **result)


def plan_requirements_pack(new_instructions: str = "", *, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning.requirements_pack import generate_requirements_pack

    try:
        workspace_id = resolve_workspace(project_folder)
        result = generate_requirements_pack(workspace_id, new_instructions=str(new_instructions or ""))
    except (LookupError, ValueError) as exc:
        return _fail("plan_requirements_pack", str(exc))
    counts = result["ledger"]["counts"]
    return _ok("plan_requirements_pack",
               f"Requirements Pack saved in Decisions (derived {counts['derived']}, new {counts['new']}, conflicts {counts['conflicts']}); "
               f"not written to the repository.", workspace_id=workspace_id, item_id=result["item"].get("id"),
               ledger=result["ledger"], proposed_path=result["proposed_path"], written_to_repo=False)


def plan_build_tickets(tasks: list, *, project_folder: Optional[str] = None, **_: Any) -> dict[str, Any]:
    from distr.core.planning import service
    from distr.core.planning.build import PlanBuildConflict, build_tasks
    from distr.core.planning.requirements_pack import ledger_from_items

    try:
        workspace_id = resolve_workspace(project_folder)
        detail = service.get_workspace(workspace_id) or {}
        conflicts = set(ledger_from_items(detail.get("items") or [])["conflict_keys"])
        if conflicts and isinstance(tasks, list):
            blocked = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                cited = _keys_in(" ".join(map(str, task.get("acceptance_criteria") or [])) + " " + str(task.get("description") or ""))
                if cited and cited <= conflicts:
                    blocked.append(str(task.get("key")))
            if blocked:
                return _fail("plan_build_tickets", "Resolve requirement conflicts first: "
                             + ", ".join(sorted(conflicts)) + f" (tasks {', '.join(blocked)}).")
        result = build_tasks(workspace_id, tasks=tasks, expected_revisions=_revisions(detail))
    except (LookupError, ValueError, PlanBuildConflict) as exc:
        return _fail("plan_build_tickets", f"No tickets built: {exc}")
    return _ok("plan_build_tickets", f"Built tickets: {result}", workspace_id=workspace_id, result=result)


def _keys_in(text: str) -> set[str]:
    import re
    return set(re.findall(r"\b(?:FR|NFR|INF|NEW)-\d{3,}\b", text))


_EDIT_SCHEMA = {"type": "object", "properties": {
    "id": {"type": "integer", "description": "Existing Plan item id to update (omit to create)"},
    "item_type": {"type": "string", "description": "brief, prd, frac, decision, document, wireframe, flows, architecture, erd, diagram"},
    "title": {"type": "string"}, "content": {"type": "string"},
    "content_format": {"type": "string", "description": "markdown, wire, or mermaid"}}, "required": ["content"]}

_REASON = {"type": "string", "description": "One line: why this step calls the tool (kept in the run log)"}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "plan_snapshot": {"description": "Read this project's Decisions Plan: every section with its origin (derived/new/user), "
                      "content, revision, and the requirements ledger.",
                      "parameters": {"type": "object", "properties": {"reason": _REASON}, "required": ["reason"]}},
    "plan_scan_project": {"description": "Deterministically scan the linked repository (routes, pages, models, sitemap, files) and "
                          "refresh the Plan sections the scanner owns. Never overwrites Paul's edits.",
                          "parameters": {"type": "object", "properties": {"reason": _REASON, "force": {"type": "boolean", "default": False}}, "required": ["reason"]}},
    "plan_write_artifacts": {"description": "Create or update Plan sections through the validated Plan editor (wire DSL is validated "
                             "by the Plan renderer). origin='derived' for content reverse-engineered from the project, 'new' for "
                             "forward-looking design. Sections Paul edited are refused.",
                             "parameters": {"type": "object", "properties": {"edits": {"type": "array", "items": _EDIT_SCHEMA},
                                            "origin": {"type": "string", "enum": ["derived", "new"]}}, "required": ["edits"]}},
    "plan_derive_from_design": {"description": "Generate PRD, FRAC (stable FR keys) and ERD from the Plan's designed screens.",
                                "parameters": {"type": "object", "properties": {"reason": _REASON}, "required": ["reason"]}},
    "plan_requirements_pack": {"description": "Regenerate the Requirements Pack (derived history vs new instructions, conflicts) "
                               "in Decisions. Never writes to the repository.",
                               "parameters": {"type": "object", "properties": {"new_instructions": {"type": "string",
                                              "description": "Optional '- NEW-001: …' lines to add as forward-looking requirements"}, "reason": _REASON}, "required": ["reason"]}},
    "plan_build_tickets": {"description": "Create Backlog tickets from a dependency-ordered task graph (key, title, description, "
                           "parent_key, depends_on, source_item_ids, acceptance_criteria citing FR keys, skills, complexity, role).",
                           "parameters": {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object"}}},
                                          "required": ["tasks"]}},
}

DISPATCH = {
    "plan_snapshot": plan_snapshot,
    "plan_scan_project": plan_scan_project,
    "plan_write_artifacts": plan_write_artifacts,
    "plan_derive_from_design": plan_derive_from_design,
    "plan_requirements_pack": plan_requirements_pack,
    "plan_build_tickets": plan_build_tickets,
}
