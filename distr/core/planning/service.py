"""Board-scoped planning workspaces with safe project-folder persistence."""

from __future__ import annotations

import hashlib
from distr.core.planning.files import stage_write, finish_write, managed_target, disk_hash
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func

from distr.core.db import get_session
from distr.core.db.projects import Project
from distr.core.db.workflow import PlanItem, PlanItemRevision, PlanWorkspace
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


ITEM_TYPES = {
    "brief": ("Outcome brief", "markdown", "# Outcome brief\n\n## Outcome\n\nDescribe the result this board should deliver.\n\n## Boundaries\n\n- In scope\n- Out of scope\n"),
    "prd": ("Product requirements", "markdown", "# Product requirements\n\n## Outcome\n\n## Users\n\n## Requirements\n\n- FR-001: \n\n## Open questions\n\n"),
    "frac": ("Functional requirements", "markdown", "# Functional requirements and acceptance criteria\n\n## FR-001\n\nRequirement.\n\n- AC-001.1: Given ..., when ..., then ...\n"),
    "wireframe": ("Wireframes", "html", "# Wireframes\n\nDescribe the primary screen, its actions, and its loading and error states. The preview turns this into a vector storyboard.\n"),
    "erd": ("Entity relationship diagram", "mermaid", "erDiagram\n    PROJECT ||--o{ ITEM : contains\n    ITEM ||--o{ REVISION : versions\n"),
    "diagram": ("System diagram", "mermaid", "flowchart LR\n    User --> DecisionsAI\n    DecisionsAI --> Project\n"),
    "decision": ("Decision", "markdown", "# Decision\n\n## Context\n\n## Decision\n\n## Consequences\n\n"),
    "architecture": ("Architecture and ERD", "mermaid", "erDiagram\n    PROJECT ||--o{ ITEM : contains\n    ITEM ||--o{ REVISION : versions\n"),
    "flows": ("Flows and wireframes", "html", "# Flows and wireframes\n\nDescribe the primary user journey and the screen states that need to be designed. The preview turns this into a vector storyboard.\n"),
    "skills": ("Skills and instructions", "markdown", "# Skills and instructions\n\n## Required skills\n\n- Identify the skills needed to deliver this plan.\n\n## Working instructions\n\n- Keep implementation aligned with the approved requirements.\n"),
    "handover": ("Handover", "markdown", "# Handover\n\n## What is ready\n\n## Open items\n\n## Next actions\n\n- Confirm the unresolved decisions before execution.\n"),
    "file_structure": ("File structure", "markdown", "# File structure\n\nThis is the bounded file tree observed in the linked project. It is regenerated only when you explicitly ask Plan to inspect the project.\n"),
}

SCAFFOLD = ("brief", "prd", "frac", "architecture", "flows", "skills", "file_structure", "handover")

ITEM_META = {
    "brief": ("The outcome, users, and boundaries", "Outcome card"),
    "prd": ("What the product must do and for whom", "Requirement cards"),
    "frac": ("Testable behaviour and acceptance criteria", "Acceptance cards"),
    "architecture": ("How the system is shaped and connected", "Rendered ERD"),
    "flows": ("The user journey and screen states", "SVG storyboard"),
    "skills": ("The tools, skills, and working rules needed", "Capability cards"),
    "handover": ("What is ready, unresolved, and next", "Handover checklist"),
    "file_structure": ("What files and folders own the work", "Bounded file tree"),
    "wireframe": ("A screen layout and its interaction states", "SVG storyboard"),
    "erd": ("The project data relationships", "Rendered ERD"),
    "diagram": ("A system or process relationship", "Rendered diagram"),
    "decision": ("A decision and its consequences", "Decision card"),
}


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(value or "item").lower()).strip("-")
    return clean[:64] or "item"


def _workspace_payload(row: PlanWorkspace, item_count: int = 0) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "board_key": row.board_key,
        "board_provider": row.board_provider,
        "board_name": row.board_name,
        "project_id": int(row.project_id) if row.project_id is not None else None,
        "root_path": row.root_path or "",
        "status": row.status or "draft",
        "item_count": int(item_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None,
    }


def _workspace_summary(items: list[PlanItem]) -> dict[str, Any]:
    total = len(items)
    approved = sum(1 for item in items if item.status == "approved")
    open_items = [item.title for item in items if item.status != "approved"]
    return {
        "total": total,
        "approved": approved,
        "progress_percent": round((approved / total) * 100) if total else 0,
        "open_items": open_items,
        "next_action": "Review the open plan sections before handover." if open_items else "Plan is ready for handover.",
    }


def ticket_preview(workspace_id: int) -> dict[str, Any]:
    """Return an idempotent, non-mutating ticket proposal for a plan workspace."""
    with get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        items = db.query(PlanItem).filter(PlanItem.workspace_id == workspace.id).order_by(PlanItem.sort_order, PlanItem.id).all()
        proposals = []
        for position, item in enumerate(items, start=1):
            proposals.append({
                "position": position,
                "stable_key": _hash(f"plan:{workspace.id}:item:{item.id}")[:16],
                "title": item.title,
                "source_item_id": int(item.id),
                "status": item.status or "draft",
                "action": "update-or-create",
                "ready": item.status == "approved",
            })
        return {
            "workspace_id": int(workspace.id),
            "board_name": workspace.board_name,
            "idempotent": True,
            "creates": sum(1 for item in proposals if item["ready"]),
            "needs_review": sum(1 for item in proposals if not item["ready"]),
            "items": proposals,
        }


def build_approved_tickets(workspace_id: int) -> dict[str, Any]:
    """Create approved plan tickets once, keyed by the plan item's stable identity."""
    with get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        if workspace.board_provider != "decisions":
            raise ValueError("Ticket building currently supports local Decisions boards only.")
        try:
            board_id = int(str(workspace.board_key).split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("Plan is not linked to a local Decisions board.") from exc
        board = db.get(KanbanBoard, board_id)
        if board is None:
            raise LookupError("Linked ticket board not found.")
        lane = next((row for row in board.lanes if row.name.lower() == "backlog"), None) or next(iter(board.lanes), None)
        if lane is None:
            raise ValueError("Linked ticket board has no lane.")
        items = db.query(PlanItem).filter(PlanItem.workspace_id == workspace.id, PlanItem.status == "approved").order_by(PlanItem.sort_order, PlanItem.id).all()
        existing = {ticket.source_external_id: ticket for ticket in lane.tickets if ticket.source_provider == "plan"}
        created, reused = [], []
        for item in items:
            stable_key = _hash(f"plan:{workspace.id}:item:{item.id}")[:16]
            ticket = existing.get(stable_key)
            if ticket is None:
                ticket = KanbanTicket(
                    lane_id=lane.id,
                    title=item.title,
                    description=item.content or "",
                    priority="medium",
                    complexity="medium",
                    position=len(lane.tickets),
                    linked_project_id=board.default_project_id,
                    linked_workflow_id=board.default_workflow_id,
                    source_provider="plan",
                    source_external_id=stable_key,
                    source_label=workspace.board_name,
                )
                db.add(ticket)
                created.append(item.title)
            else:
                reused.append(item.title)
        db.commit()
        return {"workspace_id": int(workspace.id), "board_name": workspace.board_name, "created": created, "reused": reused, "idempotent": True}


def _item_payload(row: PlanItem, revision_count: int = 0) -> dict[str, Any]:
    purpose, visual_mode = ITEM_META.get(row.item_type, ("A structured plan artifact", "Review card"))
    return {
        "id": int(row.id),
        "workspace_id": int(row.workspace_id),
        "item_type": row.item_type,
        "title": row.title,
        "content": row.content or "",
        "content_format": row.content_format or "markdown",
        "status": row.status or "draft",
        "sort_order": int(row.sort_order or 0),
        "file_path": row.file_path or "",
        "revision_count": int(revision_count or 0),
        "purpose": purpose,
        "visual_mode": visual_mode,
        "file_sync_error": getattr(row, "file_sync_error", ""),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None,
    }


def list_workspaces() -> list[dict[str, Any]]:
    with get_session() as db:
        rows = db.query(PlanWorkspace).order_by(PlanWorkspace.modified_at.desc()).all()
        counts = dict(db.query(PlanItem.workspace_id, func.count(PlanItem.id)).group_by(PlanItem.workspace_id).all())
        return [_workspace_payload(row, counts.get(row.id, 0)) for row in rows]


def ensure_workspace(*, board_key: str, board_provider: str, board_name: str, project_id: int | None = None) -> dict[str, Any]:
    clean_key = str(board_key or "").strip()
    if ":" not in clean_key:
        raise ValueError("A valid board key is required.")
    with get_session() as db:
        row = db.query(PlanWorkspace).filter(PlanWorkspace.board_key == clean_key).one_or_none()
        project = db.get(Project, int(project_id)) if project_id else None
        root_path = str(Path(project.folder_location).expanduser() / "planning") if project and project.folder_location else ""
        if row is None:
            row = PlanWorkspace(
                board_key=clean_key,
                board_provider=str(board_provider or "decisions").strip().lower(),
                board_name=str(board_name or "Untitled board").strip(),
                project_id=int(project_id) if project_id else None,
                root_path=root_path,
            )
            db.add(row)
        else:
            row.board_name = str(board_name or row.board_name).strip()
            row.project_id = int(project_id) if project_id else row.project_id
            row.root_path = root_path or row.root_path
        db.commit()
        db.refresh(row)
        count = db.query(func.count(PlanItem.id)).filter(PlanItem.workspace_id == row.id).scalar() or 0
        payload = _workspace_payload(row, count)
    ensure_plan_scaffold(payload["id"])
    return get_workspace(payload["id"]) or payload


def get_workspace(workspace_id: int) -> dict[str, Any] | None:
    with get_session() as db:
        row = db.get(PlanWorkspace, int(workspace_id))
        if row is None:
            return None
        count = db.query(func.count(PlanItem.id)).filter(PlanItem.workspace_id == row.id).scalar() or 0
        payload = _workspace_payload(row, count)
        items = db.query(PlanItem).filter(PlanItem.workspace_id == row.id).order_by(PlanItem.sort_order, PlanItem.id).all()
        payload["items"] = [_item_payload(item, _revision_count(db, item.id)) for item in items]
        payload["summary"] = _workspace_summary(items)
    return payload


def reconcile_workspace(workspace_id: int) -> dict[str, Any] | None:
    """Retry pending file projections for a workspace.

    Reconciliation is deliberately separate from ``get_workspace``.  Loading
    the Development UI must not write, replace, or delete project files as a
    side effect of a GET request.
    """
    workspace = get_workspace(workspace_id)
    if workspace is None:
        return None
    errors = {
        int(item["id"]): _finish_file_projection(item["id"])
        for item in workspace["items"]
    }
    refreshed = get_workspace(workspace_id)
    if refreshed is None:
        return None
    for item in refreshed["items"]:
        item["file_sync_error"] = errors.get(int(item["id"]), "")
    return refreshed


def ensure_plan_scaffold(workspace_id: int) -> list[dict[str, Any]]:
    """Create only missing first-class plan sections, preserving all user content."""
    with get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        existing = {item.item_type for item in db.query(PlanItem).filter(PlanItem.workspace_id == workspace.id).all()}
    created = []
    for item_type in SCAFFOLD:
        if item_type not in existing:
            created.append(create_item(workspace_id=workspace_id, item_type=item_type, source="scaffold"))
    # Keep the canonical handover sequence even when a new section is added to
    # an older workspace. User-created artifacts remain after the scaffold.
    with get_session() as db:
        items = db.query(PlanItem).filter(PlanItem.workspace_id == int(workspace_id)).order_by(PlanItem.sort_order, PlanItem.id).all()
        order = {item_type: position for position, item_type in enumerate(SCAFFOLD)}
        ordered = sorted(items, key=lambda item: (order.get(item.item_type, len(SCAFFOLD)), item.sort_order, item.id))
        for position, item in enumerate(ordered):
            item.sort_order = position
        db.commit()
    return created


def _revision_count(db, item_id: int) -> int:
    return int(db.query(func.count(PlanItemRevision.id)).filter(PlanItemRevision.item_id == int(item_id)).scalar() or 0)


def _write_managed_file(db, workspace: PlanWorkspace, item: PlanItem, previous_hash: str | None = None) -> None:
    if not workspace.root_path:
        return
    if not item.file_path:
        suffix = {"markdown": ".md", "mermaid": ".mmd", "html": ".html"}.get(item.content_format, ".txt")
        item.file_path = str(Path(workspace.root_path).expanduser().resolve() / f"{item.id}-{_slug(item.title)}{suffix}")
    stage_write(db, workspace, item, previous_hash)


def _finish_file_projection(item_id):
    # Serialize recovery with edits so an older projection cannot overtake a newer revision.
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text
            db.execute(text("BEGIN IMMEDIATE"))
        item = db.get(PlanItem, int(item_id), with_for_update=True)
        if item is None:
            return ""
        workspace = db.get(PlanWorkspace, item.workspace_id)
        try:
            error = finish_write(db, workspace, item)
            db.commit()
            return error
        except (OSError, ValueError) as exc:
            db.rollback()
            return f"Saved in Decisions; file synchronization is pending: {exc}"


def create_item(*, workspace_id: int, item_type: str, title: str = "", content: str = "", content_format: str = "", source: str = "user", instruction: str = "") -> dict[str, Any]:
    clean_type = str(item_type or "brief").strip().lower()
    default_title, default_format, default_content = ITEM_TYPES.get(clean_type, ("Document", "markdown", "# Document\n\n"))
    with get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        current_max = db.query(func.coalesce(func.max(PlanItem.sort_order), -1)).filter(PlanItem.workspace_id == workspace.id).scalar()
        position = int(current_max if current_max is not None else -1) + 1
        if clean_type == "file_structure" and not content and workspace.project_id:
            project = db.get(Project, int(workspace.project_id))
            if project and project.folder_location:
                content = _file_tree_content(Path(project.folder_location).expanduser().resolve(), workspace.root_path)
        item = PlanItem(
            workspace_id=workspace.id,
            item_type=clean_type,
            title=str(title or default_title).strip(),
            content=str(content or default_content),
            content_format=str(content_format or default_format).strip().lower(),
            sort_order=position,
        )
        db.add(item)
        db.flush()
        _write_managed_file(db, workspace, item)
        db.add(PlanItemRevision(item_id=item.id, revision=1, title=item.title, content=item.content, status=item.status, source=source, instruction=instruction))
        db.commit()
        db.refresh(item)
        payload = _item_payload(item, 1)
    payload["file_sync_error"] = _finish_file_projection(payload["id"])
    return payload


def _file_tree_content(project_root: Path, plan_root: str = "") -> str:
    """Build a small, deterministic tree without ingesting generated or secret data."""
    ignored = {".git", "node_modules", "__pycache__", ".pytest_cache", ".next", "dist", "build"}
    plan_path = Path(plan_root).expanduser().resolve() if plan_root else None
    paths: list[Path] = []
    for path in project_root.rglob("*"):
        if any(part in ignored or part.startswith(".") for part in path.relative_to(project_root).parts):
            continue
        if plan_path and (path == plan_path or plan_path in path.parents):
            continue
        paths.append(path)
        if len(paths) >= 120:
            break
    lines = ["# File structure", "", f"Observed from `{project_root}`.", ""]
    if not paths:
        lines.append("- No visible project files found")
    else:
        for path in sorted(paths, key=lambda value: (len(value.relative_to(project_root).parts), str(value).lower())):
            relative = path.relative_to(project_root)
            depth = len(relative.parts) - 1
            marker = "▸" if path.is_dir() else "•"
            lines.append(f"{'  ' * depth}- {marker} `{relative}`")
    lines.extend(["", "## Reading guide", "", "- Directories are shown before deeper paths.", "- Hidden, generated, dependency, and plan-sync folders are omitted."])
    return "\n".join(lines) + "\n"


def update_item(item_id: int, *, title: str | None = None, content: str | None = None, status: str | None = None, instruction: str = "", source: str = "user", expected_revision: int | None = None, workspace_id: int | None = None) -> dict[str, Any] | None:
    with get_session() as db:
        # Serialize revision checks and writes, including SQLite's deferred transactions.
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text
            db.execute(text("BEGIN IMMEDIATE"))
        item = db.get(PlanItem, int(item_id), with_for_update=True)
        if item is None:
            return None
        if workspace_id is not None and item.workspace_id != int(workspace_id):
            raise LookupError("Plan item not found in this workspace.")
        current_revision = _revision_count(db, item.id)
        if expected_revision is not None and current_revision != expected_revision:
            raise ValueError("This plan item has a newer revision. Your draft has been kept. Review the newer version before saving.")
        workspace = db.get(PlanWorkspace, item.workspace_id)
        previous_hash = item.content_hash
        changed = (title is not None and (str(title).strip() or item.title) != item.title) or (content is not None and str(content) != item.content)
        if title is not None:
            item.title = str(title).strip() or item.title
        if content is not None:
            item.content = str(content)
        if status is not None:
            clean_status = str(status).strip().lower()
            if clean_status not in {"draft", "review", "approved"}:
                raise ValueError("Unknown plan item status.")
            item.status = clean_status
        if changed:
            item.status = "draft"
        _write_managed_file(db, workspace, item, previous_hash=previous_hash)
        revision = current_revision + 1
        db.add(PlanItemRevision(item_id=item.id, revision=revision, title=item.title, content=item.content, status=item.status, source=source, instruction=instruction))
        db.commit()
        db.refresh(item)
        payload = _item_payload(item, revision)
    payload["file_sync_error"] = _finish_file_projection(payload["id"])
    return payload


def list_revisions(item_id: int) -> list[dict[str, Any]]:
    with get_session() as db:
        rows = db.query(PlanItemRevision).filter(PlanItemRevision.item_id == int(item_id)).order_by(PlanItemRevision.revision.desc()).all()
        return [{
            "id": int(row.id), "item_id": int(row.item_id), "revision": int(row.revision),
            "title": row.title, "content": row.content, "status": row.status,
            "source": row.source, "instruction": row.instruction or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]


def discover_project(workspace_id: int, *, instruction: str) -> dict[str, Any]:
    with get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        project = db.get(Project, int(workspace.project_id)) if workspace and workspace.project_id else None
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        if project is None or not project.folder_location:
            raise ValueError("Link a project folder to this board first.")
        project_root = Path(project.folder_location).expanduser().resolve()
        if not project_root.is_dir():
            raise ValueError("The linked project folder is unavailable.")
        names = sorted(path.name for path in project_root.iterdir() if not path.name.startswith(".") and path.resolve() != Path(workspace.root_path).resolve())[:60]
        readme = next((project_root / name for name in ("README.md", "README", "readme.md") if (project_root / name).is_file()), None)
        readme_excerpt = readme.read_text(encoding="utf-8", errors="replace")[:6000].strip() if readme else ""
        existing = db.query(PlanItem).filter(
            PlanItem.workspace_id == workspace.id,
            PlanItem.item_type == "project_overview",
        ).one_or_none()
        existing_revision = _revision_count(db, existing.id) if existing else 0
    content = "# Project overview\n\n"
    content += f"Observed in `{project_root}`.\n\n## Top-level contents\n\n"
    content += "\n".join(f"- {name}" for name in names) or "- No visible files found"
    if readme_excerpt:
        content += f"\n\n## README excerpt\n\n{readme_excerpt}\n"
    content += "\n\n## Next questions\n\n- Which behavior is current, and which behavior is proposed?\n- Which requirements need acceptance criteria?\n"
    if existing and existing.content == content:
        return {"action": "discovered", "item": _item_payload(existing, existing_revision), "message": "The project overview is unchanged."}
    if existing:
        item = create_item(workspace_id=workspace_id, item_type="discovery_proposal", title="Project overview proposal", content=content, content_format="markdown", source="discovery", instruction=instruction)
        return {"action": "proposed", "item": item, "message": "Created a discovery proposal. Your maintained overview is unchanged."}
    else:
        item = create_item(
            workspace_id=workspace_id,
            item_type="project_overview",
            title="Project overview",
            content=content,
            content_format="markdown",
            source="discovery",
            instruction=instruction,
        )
    return {"action": "discovered", "item": item}


def apply_instruction(workspace_id: int, *, instruction: str, item_id: int | None = None) -> dict[str, Any]:
    text = str(instruction or "").strip()
    if not text:
        raise ValueError("Describe what to create or change.")
    if item_id:
        with get_session() as db:
            scoped_item = db.get(PlanItem, int(item_id))
            if scoped_item is None or int(scoped_item.workspace_id) != int(workspace_id):
                raise LookupError("Plan item not found in this workspace.")
            expected_revision = _revision_count(db, item_id)
        lower = text.lower()
        append_prefix = next((prefix for prefix in ("append ", "add ", "include ") if lower.startswith(prefix)), "")
        if append_prefix:
            with get_session() as db:
                item = db.get(PlanItem, int(item_id))
                if item is None or int(item.workspace_id) != int(workspace_id):
                    raise LookupError("Plan item not found.")
                content = f"{item.content.rstrip()}\n\n{text[len(append_prefix):].strip()}\n"
            return {"action": "updated", "item": update_item(item_id, content=content, source="language", instruction=text, expected_revision=expected_revision, workspace_id=workspace_id)}
        if lower.startswith("replace with "):
            return {"action": "updated", "item": update_item(item_id, content=text[13:].strip(), source="language", instruction=text, expected_revision=expected_revision, workspace_id=workspace_id)}
        if lower.startswith("rename to "):
            return {"action": "updated", "item": update_item(item_id, title=text[10:].strip(), source="language", instruction=text, expected_revision=expected_revision, workspace_id=workspace_id)}
        return {"action": "needs_detail", "message": "Use append, replace with, or rename to for this item."}
    lower = text.lower()
    if any(phrase in lower for phrase in ("generate from project", "scan project", "discover project", "analyze project")):
        return discover_project(workspace_id, instruction=text)
    aliases = (
        ("wireframe", ("wireframe",)),
        ("flows", ("flow", "flows", "user journey")),
        ("frac", ("frac", "frack", "functional requirements", "acceptance criteria")),
        ("prd", ("prd", "product requirements")),
        ("erd", ("erd", "entity relationship")),
        ("architecture", ("architecture", "system design")),
        ("skills", ("skills", "instructions")),
        ("handover", ("handover", "hand over", "handoff")),
        ("file_structure", ("file structure", "file tree", "folder structure", "project structure")),
        ("diagram", ("diagram",)),
        ("decision", ("decision",)),
        ("brief", ("brief",)),
    )
    requested_type = next((kind for kind, phrases in aliases if any(phrase in lower for phrase in phrases)), None)
    if not requested_type:
        return {"action": "needs_detail", "message": "Name the item to create, such as a brief, PRD, FRAC, wireframe, ERD, diagram, or decision."}
    title_match = re.search(r"(?:called|named|titled)\s+[\"']?(.+?)[\"']?$", text, re.IGNORECASE)
    item = create_item(workspace_id=workspace_id, item_type=requested_type, title=title_match.group(1).strip() if title_match else "", source="language", instruction=text)
    return {"action": "created", "item": item, "message": "Created a template. Use the editor or literal append, replace with, and rename to commands to develop it."}


def review_file(item_id: int) -> dict[str, Any]:
    with get_session() as db:
        item = db.get(PlanItem, int(item_id))
        if item is None:
            raise LookupError("Plan item not found.")
        workspace = db.get(PlanWorkspace, item.workspace_id)
        if not item.file_path or not workspace.root_path:
            raise ValueError("This item has no managed project file.")
        target = managed_target(workspace, item)
        content = target.read_text(encoding="utf-8") if target.exists() else None
        return {"item_id": item.id, "title": item.title, "saved_content": item.content or "", "file_content": content, "file_hash": _hash(content) if content is not None else None, "expected_revision": _revision_count(db, item.id)}


def reconcile_file(item_id: int, *, expected_revision: int, file_hash: str | None, action: str) -> dict[str, Any]:
    from distr.core.db.workflow import PlanFileWrite
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text
            db.execute(text("BEGIN IMMEDIATE"))
        item = db.get(PlanItem, int(item_id), with_for_update=True)
        if item is None:
            raise LookupError("Plan item not found.")
        if _revision_count(db, item.id) != expected_revision:
            raise ValueError("The saved item changed. Review both versions again.")
        workspace = db.get(PlanWorkspace, item.workspace_id)
        target = managed_target(workspace, item)
        external = target.read_text(encoding="utf-8") if target.exists() else None
        if (_hash(external) if external is not None else None) != file_hash:
            raise ValueError("The file changed after your review. Review both versions again.")
        pending = db.get(PlanFileWrite, item.id)
        if action == "import" and external is not None:
            if pending:
                db.delete(pending)
            item.content = external
            item.content_hash = _hash(external)
            item.status = "draft"
            revision = expected_revision + 1
            db.add(PlanItemRevision(item_id=item.id, revision=revision, title=item.title, content=item.content, status=item.status, source="external_file", instruction="Imported the reviewed external file version."))
        elif action == "restore" and external is None:
            if pending:
                db.delete(pending)
                db.flush()
            stage_write(db, workspace, item, None)
            revision = expected_revision
        else:
            raise ValueError("Import an existing file or restore a missing file; existing files are never replaced by reconciliation.")
        db.commit()
        payload = _item_payload(item, revision)
    payload["file_sync_error"] = _finish_file_projection(item_id)
    return payload
