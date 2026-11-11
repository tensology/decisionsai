"""Explicit approval of the exact reviewed proactive workflow draft."""
import json

from distr.core.db import get_session
from distr.core.db.kanban import KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, DevelopmentWorkItem


def approved_draft_receipt(db, event):
    run = db.get(AutoWorkflowRun, event.run_id) if event.run_id is not None else None
    item = db.query(DevelopmentWorkItem).filter_by(chat_id=run.chat_id).first() if run and run.chat_id else None
    if (not run or not item or run.workflow_id != event.workflow_id
            or run.board_id != event.board_id or run.ticket_id != event.ticket_id
            or item.local_ticket_id != event.ticket_id or item.project_id != event.project_id):
        return {"status": "unconfirmed"}
    try:
        metadata = json.loads(run.run_data or "{}")
    except (TypeError, ValueError):
        return {"status": "unconfirmed"}
    if (not isinstance(metadata, dict) or metadata.get("source_type") != "proactive_workflow_draft"
            or metadata.get("source_ref") != str(event.id) or metadata.get("project_id") != event.project_id):
        return {"status": "unconfirmed"}
    return {
        "status": "launched", "candidate_id": event.id, "workflow_id": run.workflow_id,
        "run_id": run.id, "run_status": run.status, "phase": metadata.get("phase"), "chat_id": run.chat_id,
        "development_url": f"/development/threads/{run.chat_id}/",
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
        if review.get("candidate_id") != candidate_id:
            raise DevelopmentThreadConflict("This draft is out of date. Refresh it before approving.")
        candidate = json.loads(event.payload)["candidate"]
        draft = candidate["workflow_draft"]
        if draft["checklist_revision"] != revision:
            raise DevelopmentThreadConflict("This draft is out of date. Refresh it before approving.")
        if review["status"] == "launched":
            return review
        if review["status"] != "awaiting_review" or draft["omitted_steps"]:
            raise DevelopmentThreadConflict("Review the complete current draft before launching.")

        project_id = event.project_id
        require_plan_prerequisites(ticket_id, project_id=project_id, board_id=board_id, session=db)
        ticket = db.get(KanbanTicket, ticket_id)
        lane = db.get(KanbanLane, ticket.lane_id)
        root = bind_ticket_development_thread(db, ticket, project_id=project_id, board_id=board_id, lane_name=lane.name)
        metadata = development_thread_metadata(root)
        board_key = f"decisions:{board_id}"
        if metadata.get("board_key") not in (None, "", board_key):
            raise DevelopmentThreadConflict("The Development thread belongs to another board.")
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
            "orchestration": {
                "owner_type": "development_thread", "owner_chat_id": root.id,
                "agent_role": "workflow_orchestrator", "workflow_id": workflow.id,
                "specialist_workers_are_children": True, "report_to_parent_thread": True,
            },
        }
        if route:
            run_metadata["thread_route"] = route
        result = start_workflow_run(
            workflow.id, context=context, board_id=board_id, ticket_id=ticket_id, run_metadata=run_metadata,
            dispatch_async=True, _approval_session=db, _approval_event=event,
        )
        if not result.get("run_id"):
            db.rollback()
            raise DevelopmentThreadConflict(result.get("error") or "The workflow could not be started.")
        return approved_draft_receipt(db, event)
