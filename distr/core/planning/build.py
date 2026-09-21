"""Materialize a validated Plan graph; never schedule or dispatch execution.

Revision keys are decimal PlanItem IDs and values are revision numbers (zero
for an item without history). Every referenced source needs an expected revision.
Results contain created/updated/reused counts and dependency ticket IDs. Stable identities
are scoped to the workspace. Context notes hold the versioned ``plan_build``
contract; consumers must explicitly interpret it before executing any work.
"""

from __future__ import annotations

import json
import hashlib
from graphlib import CycleError, TopologicalSorter

from sqlalchemy import func, text

from distr.core.db import Chat, get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem, PlanFileWrite, PlanItem, PlanItemRevision, PlanWorkspace
from distr.core.kanban.ticket_policy import _global_complexity_route
from distr.core.project_cli_backends.model_policy import apply_workflow_model_policy


class PlanBuildConflict(ValueError):
    """A source revision or an already materialized ticket has changed."""


def _notes(metadata):
    """Detect edits to generated context as well as to visible ticket fields."""
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return json.dumps({"plan_build": metadata, "original_digest": digest}, sort_keys=True)


def _graph(tasks):
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    if len(tasks) > 200:
        raise ValueError("At most 200 tasks are allowed")
    graph = {}
    required = {"key", "title", "description", "parent_key", "depends_on",
                "source_item_ids", "acceptance_criteria", "skills", "complexity", "role"}
    for task in tasks:
        if not isinstance(task, dict) or not required <= task.keys():
            raise ValueError("Each task must contain all required graph fields")
        if task.keys() - required - {"provider", "model"}:
            raise ValueError("Unknown task fields")
        for field in ("key", "title", "description"):
            if not isinstance(task[field], str) or (field != "description" and not task[field].strip()):
                raise ValueError(f"Invalid {field}")
            if len(task[field]) > {"key": 128, "title": 300, "description": 20000}[field]:
                raise ValueError(f"Too long: {field}")
        key = task["key"]
        if key != key.strip() or key in graph:
            raise ValueError(f"Invalid or duplicate key: {key}")
        if task["parent_key"] is not None and (not isinstance(task["parent_key"], str) or len(task["parent_key"]) > 128):
            raise ValueError(f"Invalid parent_key: {key}")
        for field in ("depends_on", "acceptance_criteria", "skills", "source_item_ids"):
            values = task[field]
            valid = (lambda v: type(v) is int and v > 0) if field == "source_item_ids" else (lambda v: isinstance(v, str) and bool(v.strip()))
            if (not isinstance(values, list) or len(values) > 200 or not all(valid(v) for v in values)
                    or any(isinstance(v, str) and len(v) > (128 if field in ("depends_on", "skills") else 2000) for v in values)):
                raise ValueError(f"Invalid {field}: {key}")
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {field}: {key}")
        if not task["source_item_ids"]:
            raise ValueError(f"Source items are required: {key}")
        if not task["acceptance_criteria"]:
            raise ValueError(f"Acceptance criteria are required: {key}")
        if task["complexity"] not in ("low", "medium", "high"):
            raise ValueError(f"Invalid complexity: {key}")
        if task["role"] not in ("implementation", "review", "testing", "polish"):
            raise ValueError(f"Invalid role: {key}")
        for field in ("provider", "model"):
            if field in task and (not isinstance(task[field], str) or not task[field].strip() or len(task[field]) > 300):
                raise ValueError(f"Invalid {field}: {key}")
        graph[key] = dict(task)
        for field in ("depends_on", "source_item_ids", "skills"):
            graph[key][field] = sorted(task[field])
    edges = {}
    for key, task in graph.items():
        refs = set(task["depends_on"])
        if task["parent_key"] is not None:
            refs.add(task["parent_key"])
        if refs - graph.keys():
            raise ValueError(f"Unknown parent or dependency: {key}")
        edges[key] = refs
    try:
        order = list(TopologicalSorter(edges).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic parent/dependency graph") from exc
    return graph, order


def _route(task, board):
    # Use the existing settings policy without resolve_ticket_cli_route's
    # setup_status probes or availability-based fallback substitution.
    route = dict(_global_complexity_route(task["complexity"]))
    policy = json.loads(board.orchestrator_policy or "{}")
    if not isinstance(policy, dict):
        raise ValueError("Invalid board routing policy")
    routes = policy.get("complexity_routing") or {}
    if not isinstance(routes, dict):
        raise ValueError("Invalid board complexity routing")
    override = routes.get(task["complexity"]) or {}
    if not isinstance(override, dict):
        raise ValueError("Invalid board complexity route")
    if override.get("backend") and override["backend"] != route.get("backend"):
        route = {"backend": override["backend"], "model": "auto"}
    for field in ("backend", "model", "model_provider", "codex_reasoning_effort", "codex_service_tier"):
        if override.get(field):
            route[field] = override[field]
    if override.get("provider"):
        route["model_provider"] = override["provider"]
    if "provider" in task:
        # Provider is a model provider (e.g. ollama), not a CLI backend.
        route = {"backend": "pi", "model_provider": task["provider"], "model": task.get("model", "auto")}
    if "model" in task:
        route["model"] = task["model"]
    route.update(complexity=task["complexity"], step_role=task["role"],
                 source="plan_explicit" if "provider" in task or "model" in task else "plan_policy")
    route = apply_workflow_model_policy(route, config={"backend_id": route["backend"], "model": route.get("model")}, settings={})
    route["task_profile"] = {"intent": task["role"], "complexity": task["complexity"], "skills": task["skills"]}
    return route


def build_tasks(workspace_id: int, *, tasks: list[dict], expected_revisions: dict[str, int]) -> dict:
    """Atomically create, reuse, or update untouched, unstarted plan tickets.

    Raises ValueError for invalid input, LookupError for a missing workspace,
    and PlanBuildConflict for stale sources or conflicting authored content.
    Reuse preserves the original route even when configured policy has changed.
    Updates preserve the selected backend/model unless explicitly changed by the
    proposal. Completed or active work raises a needs_replanning conflict; no
    ticket is replaced. Legacy contracts without an integrity digest can be
    reused but cannot safely be updated.
    """
    graph, order = _graph(tasks)
    if type(workspace_id) is not int or workspace_id <= 0:
        raise ValueError("Invalid workspace_id")
    if not isinstance(expected_revisions, dict) or any(
        not isinstance(k, str) or not k.isascii() or not k.isdecimal() or str(int(k)) != k
        or int(k) <= 0 or type(v) is not int or v < 0
        for k, v in expected_revisions.items()
    ):
        raise ValueError("expected_revisions must map decimal item IDs to revision numbers")
    source_ids = {i for task in graph.values() for i in task["source_item_ids"]}
    if not source_ids <= {int(k) for k in expected_revisions}:
        raise ValueError("Missing expected revisions for source items")

    with get_session() as db:
        try:
            # SQLite ignores FOR UPDATE. Acquire its write reservation before
            # reading so concurrent builds cannot both observe a missing key.
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            workspace = db.get(PlanWorkspace, workspace_id, with_for_update=True)
            if workspace is None:
                raise LookupError("Plan workspace not found")
            prefix, _, raw_id = workspace.board_key.partition(":")
            if workspace.board_provider != "decisions" or prefix != "decisions" or not raw_id.isdecimal():
                raise ValueError("Workspace must reference a local Decisions board")
            board = db.get(KanbanBoard, int(raw_id), with_for_update=True)
            if (board is None or board.source != "database" or not workspace.project_id
                    or board.default_project_id != workspace.project_id
                    or db.get(Project, workspace.project_id) is None):
                raise ValueError("Workspace project/board consistency failure")
            # Workspace IDs and conversation IDs are separate namespaces. Only
            # publish a chat reference when the canonical scoped root exists.
            conversation = db.query(Chat.id).join(
                DevelopmentWorkItem, DevelopmentWorkItem.chat_id == Chat.id
            ).filter(
                DevelopmentWorkItem.identity_key == f"source:plan_workspace:{workspace_id}",
                Chat.parent_id.is_(None), Chat.project_id == workspace.project_id,
            ).one_or_none()
            source_thread_id = str(conversation.id) if conversation else None
            lanes = db.query(KanbanLane).filter_by(board_id=board.id).order_by(KanbanLane.position, KanbanLane.id).all()
            if not lanes:
                raise ValueError("Board has no lane")
            lane = next((row for row in lanes if row.name.lower() == "backlog"), lanes[0])
            current_items = db.query(PlanItem).filter_by(workspace_id=workspace_id).all()
            pending_files = db.query(PlanFileWrite.item_id).join(
                PlanItem, PlanItem.id == PlanFileWrite.item_id
            ).filter(PlanItem.workspace_id == workspace_id).all()
            if pending_files:
                raise PlanBuildConflict(
                    "Project file synchronization is pending. Review file changes before building tasks."
                )
            current_revisions = {
                str(item.id): db.query(func.max(PlanItemRevision.revision)).filter_by(item_id=item.id).scalar() or 0
                for item in current_items
            }
            if not set(expected_revisions) <= set(current_revisions):
                raise ValueError("Source item outside workspace")
            if current_revisions != expected_revisions:
                raise PlanBuildConflict("Stale plan revision or source membership. Regenerate tasks from the current plan.")
            from distr.core.planning.validation import validate_plan_references, project_image_ids
            validate_plan_references([
                {"title": item.title, "item_type": item.item_type,
                 "content_format": item.content_format, "content": item.content or ""}
                for item in current_items
            ], asset_ids=project_image_ids(db, workspace.project_id))
            for item_id, expected in sorted(expected_revisions.items(), key=lambda pair: int(pair[0])):
                item = db.get(PlanItem, int(item_id), with_for_update=True)
                if item is None or item.workspace_id != workspace_id:
                    raise ValueError(f"Source item outside workspace: {item_id}")
                revision = db.query(func.max(PlanItemRevision.revision)).filter_by(item_id=item.id).scalar() or 0
                if revision != expected:
                    raise PlanBuildConflict(f"Stale plan revision: {item_id}")

            identities = {key: f"plan:{workspace_id}:task:{key}" for key in graph}
            existing = db.query(KanbanTicket).filter(
                KanbanTicket.source_provider == "plan",
                KanbanTicket.source_external_id.startswith(f"plan:{workspace_id}:task:", autoescape=True),
            ).with_for_update().all()
            by_identity = {}
            omitted = [ticket.id for ticket in existing if ticket.source_external_id not in set(identities.values())]
            if omitted:
                raise PlanBuildConflict(f"needs_replanning: existing tasks omitted from proposal: {omitted}")
            for ticket in existing:
                if ticket.source_external_id in by_identity:
                    raise PlanBuildConflict("Duplicate materialized task identity")
                by_identity[ticket.source_external_id] = ticket
            rows, metadata, updates = {}, {}, set()
            for key in order:
                task = graph[key]
                contract = {"version": 1, "workspace_id": workspace_id, "project_id": workspace.project_id,
                            "task": task, "expected_revisions": {str(i): expected_revisions[str(i)] for i in task["source_item_ids"]}}
                ticket = by_identity.get(identities[key])
                if ticket is not None:
                    try:
                        stored = json.loads(ticket.context_notes or "{}")["plan_build"]
                        original = stored["task"]
                        original_parent = (by_identity.get(f"plan:{workspace_id}:task:{original['parent_key']}")
                                           if original["parent_key"] is not None else None)
                        parent = original_parent.id if original_parent else None
                        dependencies = [by_identity[f"plan:{workspace_id}:task:{k}"].id for k in original["depends_on"]]
                        sealed = ticket.context_notes == _notes(stored)
                        legacy = ticket.context_notes == json.dumps({"plan_build": stored}, sort_keys=True)
                        matches = ((sealed or legacy)
                                   and ticket.title == original["title"] and ticket.description == original["description"]
                                   and ticket.complexity == original["complexity"]
                                   and ticket.linked_project_id == workspace.project_id
                                   and ticket.lane_id in {row.id for row in lanes}
                                   and ticket.parent_ticket_id == parent
                                   and stored["depends_on"] == dependencies
                                   and isinstance(stored["route"], dict))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        matches = False
                    if not matches:
                        raise PlanBuildConflict(f"Existing authored task conflicts: {key}")
                    changed = any(stored.get(k) != v for k, v in contract.items())
                    if changed:
                        ticket_lane = next(row.name.strip().lower() for row in lanes if row.id == ticket.lane_id)
                        protected_lanes = {"done", "completed", "in progress", "in_progress", "running"}
                        if board.agent_done_lane:
                            protected_lanes.add(board.agent_done_lane.strip().lower())
                        executed = db.query(ProjectExecutionSession.id).filter(
                            ProjectExecutionSession.ticket_id == ticket.id,
                            ProjectExecutionSession.status.in_(("queued", "running", "waiting", "completed")),
                        ).first()
                        if (not sealed or ticket.workflow_status not in (None, "", "failed", "cancelled")
                                or ticket_lane in protected_lanes or executed):
                            raise PlanBuildConflict(f"needs_replanning: task cannot be updated safely: {key}")
                        route = dict(stored["route"])
                        if any(task.get(f) != original.get(f) for f in ("provider", "model")):
                            route = _route(task, board)
                        route.update(complexity=task["complexity"], step_role=task["role"],
                                     task_profile={"intent": task["role"], "complexity": task["complexity"], "skills": task["skills"]})
                        metadata[key] = {**contract, "route": route}
                        updates.add(key)
                    else:
                        metadata[key] = stored
                    rows[key] = ticket
                else:
                    metadata[key] = {**contract, "route": _route(task, board)}
                    # No rows are added or flushed until all conflicts and
                    # routing decisions have been checked.
                    rows[key] = KanbanTicket(title=task["title"], description=task["description"],
                        complexity=task["complexity"], priority="medium", lane_id=lane.id,
                        linked_project_id=workspace.project_id, source_provider="plan",
                        source_external_id=identities[key], source_thread_id=source_thread_id,
                        source_url=f"/development/boards/decisions/{board.id}/plan/",
                        source_label=workspace.board_name, send_to_cli=False)
            created = 0
            position = db.query(func.max(KanbanTicket.position)).filter_by(lane_id=lane.id).scalar()
            position = (position + 1) if position is not None else 0
            for key in order:
                ticket, task = rows[key], graph[key]
                is_new = ticket.id is None
                ticket.source_thread_id = source_thread_id
                ticket.source_url = f"/development/boards/decisions/{board.id}/plan/"
                if not is_new and key not in updates:
                    continue
                if is_new:
                    ticket.position = position + created
                ticket.title = task["title"]
                ticket.description = task["description"]
                ticket.complexity = task["complexity"]
                ticket.parent_ticket_id = rows[task["parent_key"]].id if task["parent_key"] else None
                metadata[key]["depends_on"] = [rows[k].id for k in task["depends_on"]]
                ticket.context_notes = _notes(metadata[key])
                db.add(ticket)
                db.flush()
                created += int(is_new)
            result = {"created": created, "updated": len(updates), "reused": len(graph) - created - len(updates), "tasks": [
                {"id": rows[k].id, "key": k, "title": rows[k].title,
                 "parent_id": rows[k].parent_ticket_id, "depends_on": metadata[k]["depends_on"],
                 "route": metadata[k]["route"]} for k in graph
            ]}
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
