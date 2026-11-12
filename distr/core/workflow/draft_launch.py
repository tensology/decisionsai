"""Explicit approval of the exact reviewed proactive workflow draft."""
import hashlib
import json
from pathlib import Path

from distr.core.db import Action, Chat, get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, DevelopmentWorkItem

WORKFLOW_DEFINITION_CHANGED = "The workflow or a linked Action changed after approval. Scan this ticket again and review before launching."
WORKFLOW_RECEIPT_UNCONFIRMED = "A workflow was approved, but its run could not be confirmed. Check launch status before approving again."
WORKFLOW_APPROVAL_SCOPE_CHANGED = "The approved ticket, board or Development thread changed. The workflow stopped. Review the links before starting again."
WORKFLOW_APPROVAL_FOLDER_CHANGED = "The project folder changed after approval. The workflow stopped. Scan this ticket again and review before launching."


def approved_workflow_stop_reason(status, metadata):
    """Use fixed public copy for a recorded approval invalidation."""
    if status != "failed":
        return None
    if metadata.get("approval_project_folder_changed") is True:
        return WORKFLOW_APPROVAL_FOLDER_CHANGED
    if metadata.get("approval_scope_changed") is True:
        return WORKFLOW_APPROVAL_SCOPE_CHANGED
    if metadata.get("approval_definition_changed") is True:
        return WORKFLOW_DEFINITION_CHANGED
    return None


def project_folder_revision(folder, *, include_directory=True):
    """Bind approval to the resolved path and, when present, its directory."""
    prefix = ""
    try:
        path = Path(folder).expanduser().resolve() if folder else None
        identity = str(path) if path else ""
        if path is not None and include_directory:
            try:
                entry = path.stat()
            except FileNotFoundError:
                pass  # An approved new-project workflow can create its checkout.
            else:
                prefix = "directory:"
                identity = json.dumps((identity, entry.st_dev, entry.st_ino))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return prefix + hashlib.sha256(identity.encode("utf-8", errors="surrogateescape")).hexdigest()


def approved_draft_folder_matches(db, event, metadata):
    project = db.get(Project, event.project_id)
    if "approved_project_folder_revision" in metadata:
        return project_folder_matches(project.folder_location, metadata["approved_project_folder_revision"])
    try:
        candidate = json.loads(event.payload)["candidate"]
    except (KeyError, TypeError, ValueError):
        return True
    # Older approvals saved the configured path, without its resolved target.
    current = project_folder_revision(project.folder_location, include_directory=False)
    return (not isinstance(candidate, dict) or "project_folder" not in candidate
            or (current is not None and current == project_folder_revision(candidate["project_folder"], include_directory=False)))


def project_folder_matches(folder, expected):
    if not isinstance(expected, str):
        return False
    return project_folder_revision(folder, include_directory=expected.startswith("directory:")) == expected


def workflow_step_action(db, step):
    if step.action_type not in {"decision_action", "play_recording"}:
        return None
    try:
        config = json.loads(step.config or "{}")
        if not isinstance(config, dict):
            return None
        action_id = ((config.get("action_id") if step.action_type == "decision_action" else None)
                     or config.get("recording_id") or step.action_id)
        action = db.get(Action, int(action_id)) if action_id else None
    except (TypeError, ValueError):
        return None
    if action is None:
        return None
    return {"id": action.id, "title": action.title, "is_instruction": bool(action.is_instruction),
            "instruction_text": action.instruction_text, "recording_filename": action.recording_filename}


def workflow_definition_revision(db, workflow):
    """Identify executable definition data while excluding run telemetry."""
    from distr.core.workflow.standards_memory import STANDARDS_CONTEXT_TITLE, UNIVERSAL_WORKFLOW_STANDARDS

    definition = {column.key: getattr(workflow, column.key) for column in workflow.__table__.columns
                  if column.key not in {"created_date", "modified_date", "last_run_at", "next_run_at"}}
    steps = sorted(workflow.steps, key=lambda step: (step.position or 0, step.id))
    step_definitions = [
        {column.key: getattr(step, column.key) for column in step.__table__.columns
         if column.key not in {"created_date", "modified_date", "status", "result", "tool_used", "routing_path"}}
        for step in steps
    ]
    variables = sorted(
        (var.name, var.default_value or "") for var in workflow.variables
        if not (var.name == STANDARDS_CONTEXT_TITLE
                and (var.default_value or "").strip() == UNIVERSAL_WORKFLOW_STANDARDS.strip())
    )
    actions = [workflow_step_action(db, step) for step in steps]
    return hashlib.sha256(json.dumps(
        [definition, step_definitions, variables, actions], sort_keys=True, default=str,
    ).encode()).hexdigest()


def approved_draft_owns_scope(db, run, event, metadata):
    if (not event or event.source != "proactive_orchestrator" or event.event_type != "proactive_work_candidate"
            or not run or not run.chat_id or run.id != event.run_id or run.workflow_id != event.workflow_id
            or run.board_id != event.board_id or run.ticket_id != event.ticket_id
            or not isinstance(metadata, dict) or metadata.get("source_type") != "proactive_workflow_draft"
            or metadata.get("source_ref") != str(event.id) or metadata.get("project_id") != event.project_id):
        return False
    root = db.get(Chat, run.chat_id)
    ticket = db.get(KanbanTicket, event.ticket_id)
    item = db.query(DevelopmentWorkItem).filter_by(chat_id=run.chat_id).first()
    lane = db.get(KanbanLane, ticket.lane_id) if ticket else None
    board = db.get(KanbanBoard, event.board_id)
    project = db.get(Project, event.project_id)
    return bool(
        board and not board.archived and lane and lane.board_id == board.id
        and project and project.deleted_at is None
        and (ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id) == project.id
        and root and root.parent_id is None and root.project_id == project.id
        and ticket.source_chat_id == root.id and ticket.linked_workflow_id == run.workflow_id
        and item and item.local_ticket_id == ticket.id and item.project_id == project.id
        and item.workflow_id == run.workflow_id and item.board_key == f"decisions:{board.id}"
    )


def approved_draft_receipt(db, event):
    run = db.get(AutoWorkflowRun, event.run_id) if event.run_id is not None else None
    if not run:
        return {"status": "unconfirmed"}
    try:
        metadata = json.loads(run.run_data or "{}")
    except (TypeError, ValueError):
        return {"status": "unconfirmed"}
    if not approved_draft_owns_scope(db, run, event, metadata):
        return {"status": "unconfirmed"}
    warning = approved_workflow_stop_reason(run.status, metadata)
    return {
        "status": "launched", "candidate_id": event.id, "workflow_id": run.workflow_id,
        "run_id": run.id, "run_status": run.status, "phase": metadata.get("phase"), "chat_id": run.chat_id,
        "development_url": f"/development/threads/{run.chat_id}/",
        **({"warning": warning} if warning else {}),
    }


def approve_workflow_draft(*, board_id: int, ticket_id: int, candidate_id: int, revision: str):
    from distr.core.chat import _append_user_turn, _chat_params
    from distr.core.orchestrator_proactive import review_workflow_draft
    from distr.core.planning.prerequisites import require_plan_prerequisites
    from distr.core.workflow.development_threads import (
        DevelopmentThreadConflict, _upsert_work_item, bind_ticket_development_thread,
        development_thread_metadata, execution_route_from_model_route,
    )
    from distr.core.workflow.dispatcher import start_workflow_run

    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        event = db.get(OrchestratorEvent, candidate_id, with_for_update=True)
        if not event or (event.source, event.event_type, event.board_id, event.ticket_id) != (
            "proactive_orchestrator", "proactive_work_candidate", board_id, ticket_id,
        ):
            raise LookupError("This workflow draft is unavailable on the selected ticket.")
        review = review_workflow_draft(board_id=board_id, ticket_id=ticket_id, _session=db)
        if review["status"] == "unconfirmed":
            raise DevelopmentThreadConflict(WORKFLOW_RECEIPT_UNCONFIRMED)
        if review["status"] == "blocked":
            raise DevelopmentThreadConflict(review["error"])
        if review.get("candidate_id") != candidate_id:
            raise DevelopmentThreadConflict("This draft is out of date. Refresh it before approving.")
        candidate = json.loads(event.payload)["candidate"]
        if (candidate.get("workflow_draft") or {}).get("checklist_revision") != revision:
            raise DevelopmentThreadConflict("This draft is out of date. Refresh it before approving.")
        if review["status"] == "launched":
            return review
        if review["status"] != "awaiting_review" or review["draft"]["omitted_steps"]:
            raise DevelopmentThreadConflict("Review the complete current draft before launching.")
        draft = review["draft"]

        project_id = event.project_id
        require_plan_prerequisites(ticket_id, project_id=project_id, board_id=board_id, session=db)
        ticket = db.get(KanbanTicket, ticket_id)
        lane = db.get(KanbanLane, ticket.lane_id)
        assigned_workflow_id = draft.get("workflow_id")
        root = bind_ticket_development_thread(db, ticket, project_id=project_id, board_id=board_id,
                                              lane_name=lane.name, approved_workflow_id=assigned_workflow_id)
        metadata = development_thread_metadata(root)
        board_key = f"decisions:{board_id}"
        if metadata.get("board_key") not in (None, "", board_key):
            raise DevelopmentThreadConflict("The Development thread belongs to another board.")
        brief = None
        if assigned_workflow_id:
            from distr.core.kanban.ticket_workflow_brief import build_ticket_workflow_brief, render_ticket_workflow_brief

            workflow = db.get(AutoWorkflow, assigned_workflow_id)
            brief = build_ticket_workflow_brief(db, ticket_id, board_id=board_id, project_id=project_id)
            context = render_ticket_workflow_brief(brief)
        else:
            context = f"Approved workflow draft for {ticket.title}\n\n{ticket.description or ''}".strip()
            workflow = AutoWorkflow(
                name=draft["name"], description=ticket.description or ticket.title,
                status="active", workflow_type="instruction", chat_id=root.id,
            )
            db.add(workflow)
            db.flush()
            for position, step in enumerate(draft["steps"]):
                workflow.steps.append(AutoWorkflowStep(
                    position=position, name=step["instruction"][:120], instruction=step["instruction"],
                    action_type="send_to_project_cli", step_type="send_to_project_cli",
                    linked_project_id=project_id, verification=step["acceptance"],
                ))
            db.flush()
        source_ref = str(candidate_id)
        item = _upsert_work_item(
            db, root, workflow_id=workflow.id, ticket_id=ticket_id,
            source_type=metadata.get("source_type") or "proactive_orchestrator",
            source_ref=metadata.get("source_ref") or source_ref, board_key=board_key, board_provider="local",
            board_ticket_key=str(ticket_id), board_ticket_title=ticket.title, board_ticket_lane=lane.name,
        )
        params = _chat_params(root.params)
        _append_user_turn(db, root, params, context)
        root.params = json.dumps(params, ensure_ascii=False, default=str)
        ticket.linked_workflow_id = workflow.id
        route = execution_route_from_model_route(metadata.get("model_route"))
        run_metadata = {
            "chat_id": root.id, "project_id": project_id, "project_name": review["project_name"],
            "board_key": board_key, "board_provider": "local", "board_ticket_key": str(ticket_id),
            "work_item_identity": item.identity_key, "source_type": "proactive_workflow_draft",
            "source_ref": source_ref, "studio": True, "execution_route": route or candidate.get("route") or {},
            "approved_project_folder_revision": draft["project_folder_revision"],
            "orchestration": {
                "owner_type": "development_thread", "owner_chat_id": root.id,
                "agent_role": "workflow_orchestrator", "workflow_id": workflow.id,
                "specialist_workers_are_children": True, "report_to_parent_thread": True,
            },
        }
        if route:
            run_metadata["thread_route"] = route
        if assigned_workflow_id:
            run_metadata["skip_ticket_queue_advance"] = True
            run_metadata["ticket_workflow_brief"] = brief
        run_metadata["approved_workflow_revision"] = workflow_definition_revision(db, workflow)
        result = start_workflow_run(
            workflow.id, context=context, board_id=board_id, ticket_id=ticket_id, run_metadata=run_metadata,
            dispatch_async=True, _approval_session=db, _approval_event=event,
        )
        if not result.get("run_id"):
            db.rollback()
            raise DevelopmentThreadConflict(result.get("error") or "The workflow could not be started.")
        return approved_draft_receipt(db, event)
