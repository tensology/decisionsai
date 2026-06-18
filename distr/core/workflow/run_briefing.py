"""Human briefing and checkpoint gates before and between ticket workflow steps."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from distr.core.db import get_session
from distr.core.db.kanban import KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

logger = logging.getLogger(__name__)

HumanWorkflowAction = Literal["confirm", "continue", "steer", "stop", "unclear"]

_STOP_RE = re.compile(
    r"\b("
    r"stop|cancel|abort|hold off|don't run|do not run|not now|pause the run|"
    r"leave it|forget it|never mind|nevermind"
    r")\b",
    re.IGNORECASE,
)
_CONTINUE_RE = re.compile(
    r"^(?:"
    r"yes(?:\s*,\s*go ahead)?|yep|yeah|ok(?:ay)?|sure|go(?: ahead)?|proceed|continue|start|"
    r"sounds good|looks good|that(?:'s| is) fine|good to go|do it|"
    r"let(?:'s| us) go|approved?|confirm(?:ed)?"
    r")(?:[.!?,;\s]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RunBriefingContext:
    run_id: int
    workflow_id: int
    workflow_name: str
    ticket_id: int | None
    ticket_title: str
    ticket_summary: str
    project_name: str
    loop_goal: str
    loop_exit: str
    first_step_name: str
    first_step_instruction: str
    step_count: int
    step_outline: str
    steering_notes: str = ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _one_line(text: str, *, limit: int = 220) -> str:
    clean = re.sub(r"\s+", " ", _strip_html(text or "")).strip()
    if len(clean) > limit:
        return clean[: limit - 3].rstrip() + "..."
    return clean


def _ticket_summary(ticket: KanbanTicket | None) -> str:
    if not ticket:
        return ""
    desc = _one_line(ticket.description or "", limit=320)
    if desc:
        return desc
    return _one_line(ticket.title or "", limit=120)


def gather_run_briefing_context(run_id: int) -> RunBriefingContext | None:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return None
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(run.workflow_id)).first()
        if not wf:
            return None
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        loop_contract = run_data.get("loop_contract") or {}
        if not loop_contract and wf.workflow_input:
            try:
                loop_contract = json.loads(wf.workflow_input or "{}") or {}
            except Exception:
                loop_contract = {}

        ticket = None
        if run.ticket_id:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
        ticket_title = (run_data.get("ticket_title") or (ticket.title if ticket else "") or "").strip()
        if not ticket_title and ticket:
            ticket_title = (ticket.title or "").strip()

        steps = sorted(wf.steps or [], key=lambda s: int(s.position or 0))
        first_step = steps[0] if steps else None
        outline_bits: list[str] = []
        for idx, step in enumerate(steps[:6], start=1):
            name = (step.name or f"Step {idx}").strip()
            outline_bits.append(f"{idx}. {name}")
        if len(steps) > 6:
            outline_bits.append(f"...and {len(steps) - 6} more checks")

        steering = (run_data.get("run_briefing_steering") or "").strip()
        return RunBriefingContext(
            run_id=int(run.id),
            workflow_id=int(run.workflow_id),
            workflow_name=(wf.name or "Workflow").strip(),
            ticket_id=int(run.ticket_id) if run.ticket_id else None,
            ticket_title=ticket_title or "Untitled ticket",
            ticket_summary=_ticket_summary(ticket),
            project_name=str(run_data.get("project_name") or "").strip(),
            loop_goal=str(loop_contract.get("goal") or wf.description or "").strip(),
            loop_exit=str(loop_contract.get("exit_when") or "").strip(),
            first_step_name=(first_step.name if first_step else "Step 1").strip(),
            first_step_instruction=_one_line(first_step.instruction if first_step else "", limit=180),
            step_count=len(steps),
            step_outline="; ".join(outline_bits),
            steering_notes=steering,
        )


def build_run_briefing_message(ctx: RunBriefingContext) -> str:
    """Plain-English pre-run briefing — one decision with context."""
    from distr.core.workflow.approval_decision import (
        approval_loop_diagnostics,
        build_run_briefing_decision,
        format_approval_decision_text,
    )

    try:
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(ctx.run_id)).first()
            if run:
                run_data = json.loads(run.run_data or "{}") or {}
                loop_msg = approval_loop_diagnostics(run_data, waiting_kind="run_briefing")
                if loop_msg:
                    return loop_msg
    except Exception:
        pass

    decision = build_run_briefing_decision(ctx)
    return format_approval_decision_text(decision)


def build_step_review_message(
    *,
    run_id: int | None = None,
    ticket_title: str,
    step_name: str,
    step_index: int | None,
    passed: bool,
    result_summary: str,
    next_step_name: str = "",
) -> str:
    from distr.core.workflow.approval_decision import (
        approval_loop_diagnostics,
        build_step_review_decision,
        format_approval_decision_text,
    )

    if run_id is not None:
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if run and run.run_data:
                    run_data = json.loads(run.run_data or "{}") or {}
                    loop_msg = approval_loop_diagnostics(run_data, waiting_kind="step_review")
                    if loop_msg:
                        return loop_msg
        except Exception:
            pass

    summary = _one_line(result_summary, limit=200)
    try:
        if run_id is not None:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if run and run.run_data:
                    run_data = json.loads(run.run_data or "{}") or {}
                    latest = run_data.get("latest_step_report") or {}
                    fields = latest.get("fields") if isinstance(latest.get("fields"), dict) else {}
                    harness_summary = _one_line(str(fields.get("summary") or ""), limit=180)
                    if harness_summary and harness_summary not in (summary or ""):
                        summary = f"{summary} Harness reported: {harness_summary}".strip()
    except Exception:
        pass

    decision = build_step_review_decision(
        ticket_title=ticket_title,
        step_name=step_name,
        step_index=step_index,
        passed=passed,
        result_summary=summary,
        next_step_name=next_step_name,
    )
    return format_approval_decision_text(decision)


def classify_human_workflow_response(
    text: str,
    *,
    waiting_kind: str = "",
) -> HumanWorkflowAction:
    """Interpret natural language as confirm/continue, steer, or stop."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        kind = (waiting_kind or "").strip().lower()
        return "confirm" if kind == "run_briefing" else "continue"
    if _STOP_RE.search(clean):
        return "stop"
    if re.search(r"\b(safe option|do the safe|recommended path)\b", clean, re.IGNORECASE):
        return "confirm" if (waiting_kind or "").strip().lower() == "run_briefing" else "continue"
    if re.search(r"\b(approve|sign off|sign-off)\b", clean, re.IGNORECASE) and len(clean) < 60:
        return "continue"
    if _CONTINUE_RE.match(clean) and len(clean) < 80:
        return "confirm" if (waiting_kind or "").strip().lower() == "run_briefing" else "continue"
    if len(clean) < 12 and _CONTINUE_RE.search(clean):
        return "confirm" if (waiting_kind or "").strip().lower() == "run_briefing" else "continue"
    return "steer"


def human_checkpoint_enabled(run_data: dict[str, Any] | None) -> bool:
    data = run_data if isinstance(run_data, dict) else {}
    if data.get("skip_human_checkpoints"):
        return False
    settings = data.get("run_settings") if isinstance(data.get("run_settings"), dict) else {}
    if settings.get("human_checkpoints") is False:
        return False
    if data.get("ticket_id") or data.get("loop_contract"):
        return True
    return bool(settings.get("human_checkpoints"))


def enter_run_briefing_wait(run_id: int, first_step_id: int) -> str:
    """Pause a new ticket run for human confirmation before step 1."""
    ctx = gather_run_briefing_context(run_id)
    if not ctx:
        return ""
    message = build_run_briefing_message(ctx)
    from distr.core.workflow.approval_decision import (
        build_run_briefing_decision,
        format_approval_decision_voice,
        increment_checkpoint_counter,
    )

    decision = build_run_briefing_decision(ctx)
    voice = format_approval_decision_voice(decision)
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return ""
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        run_data = increment_checkpoint_counter(run_data, gate="run_briefing")
        run_data["approval_decision"] = decision.to_dict()
        run.status = "waiting"
        run.current_step_id = int(first_step_id)
        run_data["waiting_kind"] = "run_briefing"
        run_data["run_briefing_text"] = message
        run_data["pending_first_step_id"] = int(first_step_id)
        run.run_data = json.dumps(run_data)
        db.commit()
    try:
        from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

        notify_ticket_workflow_progress(
            run_id=run_id,
            body=message,
            voice_body=voice,
            state_fingerprint=f"run_briefing:{run_id}",
            step_id=first_step_id,
            priority="high",
            requires_response=True,
        )
    except Exception:
        logger.debug("run briefing notification failed", exc_info=True)
    return message


def enter_step_review_wait(
    *,
    run_id: int,
    step_id: int,
    passed: bool,
    result_text: str,
    next_step_id: int,
    next_step_name: str,
) -> str:
    """Pause after a step completes and before the next step dispatches."""
    ctx = gather_run_briefing_context(run_id)
    ticket_title = ctx.ticket_title if ctx else ""
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not step or not run:
            return ""
        step_index = int(step.position or 0) + 1
        message = build_step_review_message(
            run_id=run_id,
            ticket_title=ticket_title,
            step_name=(step.name or "").strip(),
            step_index=step_index,
            passed=passed,
            result_summary=result_text,
            next_step_name=next_step_name,
        )
        from distr.core.workflow.approval_decision import (
            build_step_review_decision,
            format_approval_decision_voice,
            increment_checkpoint_counter,
        )

        summary = _one_line(result_text, limit=200)
        decision = build_step_review_decision(
            ticket_title=ticket_title,
            step_name=(step.name or "").strip(),
            step_index=step_index,
            passed=passed,
            result_summary=summary,
            next_step_name=next_step_name,
        )
        voice = format_approval_decision_voice(decision)
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        run_data = increment_checkpoint_counter(run_data, gate="step_review")
        run_data["approval_decision"] = decision.to_dict()
        run.status = "waiting"
        run.current_step_id = int(step_id)
        step.status = "waiting"
        run_data["waiting_kind"] = "step_review"
        run_data["step_review_text"] = message
        run_data["step_review_passed"] = bool(passed)
        run_data["step_review_result"] = (result_text or "")[:4000]
        run_data["pending_next_step_id"] = int(next_step_id)
        run_data.pop("pending_first_step_id", None)
        run.run_data = json.dumps(run_data)
        db.commit()
    try:
        from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

        notify_ticket_workflow_progress(
            run_id=run_id,
            body=message,
            voice_body=voice,
            state_fingerprint=f"step_review:{run_id}:{step_id}",
            step_id=step_id,
            priority="normal" if passed else "high",
            requires_response=True,
        )
    except Exception:
        logger.debug("step review notification failed", exc_info=True)
    return message


def maybe_pause_before_next_step(
    *,
    run_id: int,
    completed_step_id: int,
    passed: bool,
    result_text: str,
    next_step_id: int,
) -> bool:
    """Return True when execution should pause for human review before the next step."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return False
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        if not human_checkpoint_enabled(run_data):
            return False
        if str(run_data.get("waiting_kind") or "").strip().lower() == "step_review":
            return False
        next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(next_step_id)).first()
        next_name = (next_step.name if next_step else "").strip()

    enter_step_review_wait(
        run_id=run_id,
        step_id=completed_step_id,
        passed=passed,
        result_text=result_text,
        next_step_id=next_step_id,
        next_step_name=next_name,
    )
    return True
