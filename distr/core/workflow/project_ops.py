"""Project operations harness — classify outcome instructions and build execution plans."""

from __future__ import annotations

import re
from typing import Any, Optional


_AUTOMATION_TERMS = re.compile(
    r"\b("
    r"every\s+(morning|day|week|hour|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"daily|weekly|hourly|schedule|scheduled|recurring|repeat|automation|"
    r"open\s+chrome|open\s+safari|open\s+app|launch\s+app|"
    r"type\s+(a\s+)?snippet|paste\s+snippet|desktop\s+action|saved\s+action|"
    r"remind\s+me\s+to|at\s+\d{1,2}(:\d{2})?\s*(am|pm)?"
    r")\b",
    re.IGNORECASE,
)
_REVIEW_TERMS = re.compile(
    r"\b(review|inspect|audit|check\s+the\s+diff|look\s+at\s+the\s+diff|before\s+implementation|code\s+review)\b",
    re.IGNORECASE,
)
_INVESTIGATE_TERMS = re.compile(
    r"\b(investigate|debug|diagnose|find\s+out\s+why|failing\s+build|root\s+cause|figure\s+out)\b",
    re.IGNORECASE,
)
_QA_TERMS = re.compile(
    r"\b(qa|quality\s+check|run\s+tests|test\s+the\s+latest|validation|regression)\b",
    re.IGNORECASE,
)
_QUEUE_TERMS = re.compile(
    r"\b(review\s+the\s+ticket\s+queue|ticket\s+queue|current\s+queue|queued\s+tickets|backlog)\b",
    re.IGNORECASE,
)
_SEND_CURSOR_TERMS = re.compile(
    r"\b(send\s+(this\s+)?ticket\s+to\s+cursor|cursor\s+implement|implement\s+this\s+ticket)\b",
    re.IGNORECASE,
)
_SEND_CODEX_TERMS = re.compile(
    r"\b(ask\s+codex|send\s+to\s+codex|codex\s+inspect|codex\s+review)\b",
    re.IGNORECASE,
)
_FIX_TERMS = re.compile(
    r"\b(fix|resolve|repair|patch|address)\b",
    re.IGNORECASE,
)

_HUMAN_STATUS_MAP = {
    "planning": "Planning",
    "waiting": "Waiting for approval",
    "waiting_for_approval": "Waiting for approval",
    "investigating": "Investigating",
    "implementing": "Implementing",
    "testing": "Testing",
    "reviewing": "Reviewing",
    "blocked": "Blocked",
    "ready_for_qa": "Ready for QA",
    "done": "Done",
    "completed": "Done",
    "failed": "Blocked",
    "running": "Implementing",
    "queued": "Planning",
    "cancelled": "Done",
}


def human_status_label(status: str) -> str:
    """Map technical run/event status to plain language."""
    key = str(status or "").strip().lower().replace(" ", "_")
    if not key:
        return "Planning"
    if key in _HUMAN_STATUS_MAP:
        return _HUMAN_STATUS_MAP[key]
    if "retry" in key or "correction" in key:
        return "Testing"
    if "approval" in key:
        return "Waiting for approval"
    return key.replace("_", " ").capitalize()


def human_event_summary(event_type: str, summary: str = "") -> str:
    """Prefer human phrasing for orchestration timeline entries."""
    text = (summary or "").strip()
    if text:
        return text
    event = str(event_type or "").strip().lower()
    mapping = {
        "cursor_handoff": "Cursor is implementing the ticket",
        "codex_handoff": "Codex is reviewing the work",
        "validation_failed": "QA checks failed and a retry is queued",
        "validation_passed": "QA checks passed",
        "route_approval_requested": "Waiting for approval before changing route",
        "correction_dispatched": "Retrying because checks failed",
        "ticket_created": "Created a project ticket",
        "workflow_run_started": "Started project execution",
        "harness_steer": "Updated direction for the active run",
    }
    for prefix, label in mapping.items():
        if event.startswith(prefix) or prefix in event:
            return label
    return event.replace("_", " ").capitalize() if event else "Project update"


def classify_project_instruction(instruction: str) -> dict[str, Any]:
    """Classify a natural-language project instruction into an execution route."""
    text = (instruction or "").strip()
    lowered = text.lower()
    if not text:
        return {
            "route": "clarification",
            "confidence": 0.2,
            "rationale": "Add what you want done for this project.",
        }
    if _AUTOMATION_TERMS.search(text) and not _FIX_TERMS.search(text):
        return {
            "route": "automation",
            "confidence": 0.9,
            "rationale": "This sounds like a quick repeatable action better suited to Automations.",
        }
    if _QUEUE_TERMS.search(text):
        return {
            "route": "queue_review",
            "confidence": 0.84,
            "rationale": "I will review the linked board queue and report what needs attention.",
        }
    if _SEND_CODEX_TERMS.search(text) or (_REVIEW_TERMS.search(text) and "codex" in lowered):
        return {
            "route": "codex_review",
            "confidence": 0.86,
            "rationale": "I will route this to Codex for investigation or review before implementation.",
        }
    if _SEND_CURSOR_TERMS.search(text):
        return {
            "route": "cursor_implement",
            "confidence": 0.88,
            "rationale": "I will send implementation work to Cursor with project context attached.",
        }
    if _QA_TERMS.search(text):
        return {
            "route": "qa_validation",
            "confidence": 0.83,
            "rationale": "I will run QA checks on the latest project change.",
        }
    if _INVESTIGATE_TERMS.search(text):
        return {
            "route": "investigate",
            "confidence": 0.8,
            "rationale": "I will investigate the issue and report findings before changing code.",
        }
    if _REVIEW_TERMS.search(text):
        return {
            "route": "codex_review",
            "confidence": 0.78,
            "rationale": "I will ask Codex to review the current work before proceeding.",
        }
    if _FIX_TERMS.search(text) or len(text.split()) >= 3:
        return {
            "route": "implement_ticket",
            "confidence": 0.75,
            "rationale": "I will create a durable ticket, attach project context, and start workflow execution.",
        }
    return {
        "route": "clarification",
        "confidence": 0.45,
        "rationale": "I need a clearer outcome before starting project work.",
    }


def _plan_steps(route: str, instruction: str) -> list[dict[str, str]]:
    steps_by_route = {
        "automation": [
            {"phase": "planning", "label": "Recognize this as a lightweight automation request"},
            {"phase": "planning", "label": "Open Automations so you can save or schedule it"},
        ],
        "queue_review": [
            {"phase": "investigating", "label": "Load the linked board and ticket queue"},
            {"phase": "reviewing", "label": "Summarize blockers, active runs, and next tickets"},
        ],
        "codex_review": [
            {"phase": "planning", "label": "Create or select a ticket with the requested review scope"},
            {"phase": "reviewing", "label": "Send the work to Codex for inspection or review"},
            {"phase": "reviewing", "label": "Report findings back to the workflow timeline"},
        ],
        "cursor_implement": [
            {"phase": "planning", "label": "Attach the active project and ticket context"},
            {"phase": "implementing", "label": "Send implementation to Cursor"},
            {"phase": "testing", "label": "Wait for results and run validation checks"},
        ],
        "qa_validation": [
            {"phase": "testing", "label": "Run QA or validation checks on the latest change"},
            {"phase": "reviewing", "label": "Report pass/fail status and any retry needs"},
        ],
        "investigate": [
            {"phase": "investigating", "label": "Create a ticket for the investigation scope"},
            {"phase": "investigating", "label": "Route investigation to the configured executor"},
            {"phase": "reviewing", "label": "Report findings before implementation starts"},
        ],
        "implement_ticket": [
            {"phase": "planning", "label": "Create a ticket with the active project context"},
            {"phase": "implementing", "label": "Start the linked workflow and route to the executor"},
            {"phase": "testing", "label": "Run checks and report progress in the run timeline"},
        ],
        "clarification": [
            {"phase": "blocked", "label": "Ask for the missing project outcome or target ticket"},
        ],
    }
    return steps_by_route.get(route, steps_by_route["clarification"])


def build_execution_plan(
    instruction: str,
    *,
    route: Optional[str] = None,
    classification: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a short human-readable execution plan for project operations."""
    context = context or {}
    classification = classification or classify_project_instruction(instruction)
    route = route or classification.get("route") or "clarification"
    steps = _plan_steps(route, instruction)
    summary_parts = [step["label"] for step in steps[:4]]
    summary = " → ".join(summary_parts) if summary_parts else "Clarify the requested outcome."
    risky = route in {"implement_ticket", "cursor_implement", "qa_validation"}
    return {
        "instruction": instruction,
        "route": route,
        "confidence": classification.get("confidence", 0.5),
        "rationale": classification.get("rationale", ""),
        "summary": summary,
        "steps": steps,
        "requires_approval": risky or route == "clarification",
        "human_status": human_status_label(steps[0]["phase"] if steps else "planning"),
        "redirect_to": "/automations/" if route == "automation" else "",
        "context": {
            "project_id": context.get("project_id"),
            "project_name": context.get("project_name"),
            "board_id": context.get("board_id"),
            "board_name": context.get("board_name"),
            "workflow_id": context.get("workflow_id"),
            "queue_count": context.get("queue_count", 0),
        },
        "skills_hint": context.get("skills_hint") or [],
    }


def suggest_skills_for_route(route: str, instruction: str = "") -> list[str]:
    """Return subtle skill hints for advanced details — not primary UI."""
    text = f"{route} {instruction}".lower()
    hints: list[str] = []
    if route in {"implement_ticket", "cursor_implement"}:
        hints.append("ticket execution")
    if route in {"codex_review", "investigate"}:
        hints.append("code review")
    if route == "qa_validation" or "ui" in text or "qa" in text:
        hints.append("UI QA")
    return hints


def _ticket_title_from_instruction(instruction: str) -> str:
    line = (instruction or "").strip().splitlines()[0].strip()
    if len(line) > 120:
        return line[:117] + "..."
    return line or "Project work item"


def _route_ticket_description(route: str, instruction: str) -> str:
    prefix = {
        "queue_review": "Queue review request",
        "codex_review": "Codex review request",
        "cursor_implement": "Cursor implementation request",
        "qa_validation": "QA validation request",
        "investigate": "Investigation request",
    }.get(route, "Project operations request")
    return f"{prefix}\n\n{instruction.strip()}"


def gather_project_ops_context(
    *,
    workflow_id: int,
    board_id: Optional[int] = None,
) -> dict[str, Any]:
    """Load project, board, queue, and execution status for the workflow harness."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.service import get_workflow

    workflow = get_workflow(workflow_id) or {}
    context: dict[str, Any] = {
        "workflow_id": workflow_id,
        "workflow_name": workflow.get("name"),
        "board_id": board_id,
        "board_name": None,
        "project_id": None,
        "project_name": None,
        "project_folder": None,
        "queue_count": 0,
        "active_run_count": 0,
        "human_status": "Planning",
        "execution_summary": "No active project run",
    }
    with get_session() as session:
        board = None
        if board_id:
            board = session.query(KanbanBoard).filter_by(id=board_id).first()
        if board:
            context["board_name"] = board.name
            if board.default_project_id:
                project = session.query(Project).filter_by(id=board.default_project_id).first()
                if project:
                    context["project_id"] = project.id
                    context["project_name"] = project.name
                    context["project_folder"] = project.folder_location
        queue_count = (
            session.query(KanbanTicket)
            .filter(KanbanTicket.linked_workflow_id == workflow_id)
            .count()
        )
        context["queue_count"] = queue_count
        active_runs = (
            session.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .count()
        )
        context["active_run_count"] = active_runs
        if active_runs:
            latest = (
                session.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.workflow_id == workflow_id,
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .order_by(AutoWorkflowRun.started_at.desc())
                .first()
            )
            if latest:
                context["human_status"] = human_status_label(latest.status)
                context["execution_summary"] = f"Run #{latest.id} is {human_status_label(latest.status).lower()}"
    return context


def _resolve_intake_lane(session, board_id: int):
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.utils import load_settings_from_db

    board = session.query(KanbanBoard).filter_by(id=board_id).first()
    if not board:
        return None, None
    source_lane_name = (getattr(board, "agent_source_lane", None) or "").strip()
    if not source_lane_name:
        try:
            source_lane_name = (load_settings_from_db().get("kanban_agent_source_lane") or "").strip()
        except Exception:
            source_lane_name = ""
    lane = None
    if source_lane_name:
        lane = session.query(KanbanLane).filter_by(board_id=board_id, name=source_lane_name).first()
    if not lane:
        lane = session.query(KanbanLane).filter(
            KanbanLane.board_id == board_id,
            KanbanLane.name.ilike("%backlog%"),
        ).first()
    if not lane:
        lane = session.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position.asc()).first()
    return board, lane


def execute_project_ops_plan(
    *,
    workflow_id: int,
    instruction: str,
    route: str,
    board_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
) -> dict[str, Any]:
    """Execute an approved project operations plan."""
    instruction = (instruction or "").strip()
    route = (route or "").strip() or "clarification"
    if route == "automation":
        return {
            "status": "redirect",
            "redirect_to": "/automations/",
            "message": "This belongs in Automations. Open Automations to save or schedule this action.",
            "human_status": "Planning",
        }
    if route == "clarification" or not instruction:
        return {
            "status": "needs_input",
            "message": "Describe the project outcome you want, such as fixing a bug or reviewing the ticket queue.",
            "human_status": "Blocked",
        }
    if not board_id:
        return {
            "status": "needs_input",
            "message": "Select a board linked to this project before starting work.",
            "human_status": "Blocked",
        }

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban.ticket_policy import infer_ticket_complexity, normalize_ticket_complexity
    from distr.core.workflow.dispatcher import start_workflow_run

    with get_session() as session:
        board, lane = _resolve_intake_lane(session, board_id)
        if not board or not lane:
            return {
                "status": "failed",
                "message": "Board has no columns to create a ticket in.",
                "human_status": "Blocked",
            }

        target_ticket_id = ticket_id
        if not target_ticket_id:
            max_pos = max([ticket.position for ticket in lane.tickets], default=-1)
            complexity = infer_ticket_complexity(instruction, _route_ticket_description(route, instruction))
            if route in {"codex_review", "investigate", "queue_review"}:
                complexity = normalize_ticket_complexity("low")
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=_ticket_title_from_instruction(instruction),
                description=_route_ticket_description(route, instruction),
                priority="medium",
                complexity=complexity,
                position=max_pos + 1,
                linked_workflow_id=workflow_id,
                linked_project_id=board.default_project_id,
            )
            session.add(ticket)
            session.flush()
            target_ticket_id = ticket.id
        else:
            ticket = session.query(KanbanTicket).filter_by(id=target_ticket_id).first()
            if not ticket:
                return {"status": "failed", "message": "Ticket not found.", "human_status": "Blocked"}
            ticket.linked_workflow_id = workflow_id
            session.flush()

        context = f"Project ops: {instruction}"
        run_metadata = {
            "source_type": "project_ops",
            "board_id": board_id,
            "board_name": board.name,
            "ticket_id": target_ticket_id,
            "ticket_title": ticket.title if ticket else "",
            "project_id": str(board.default_project_id) if board.default_project_id else None,
            "phase": "implementing" if route in {"implement_ticket", "cursor_implement"} else "investigating",
            "project_ops_route": route,
        }
        run_result = start_workflow_run(
            workflow_id,
            context=context,
            board_id=board_id,
            ticket_id=target_ticket_id,
            run_metadata=run_metadata,
        )
        if "error" in run_result:
            return {
                "status": "failed",
                "message": run_result["error"],
                "human_status": "Blocked",
            }

    phase = run_metadata["phase"]
    return {
        "status": "started",
        "message": f"Started project work on ticket #{target_ticket_id}.",
        "ticket_id": target_ticket_id,
        "run_id": run_result.get("run_id"),
        "workflow_id": workflow_id,
        "human_status": human_status_label(phase),
        "skills_hint": suggest_skills_for_route(route, instruction),
    }
