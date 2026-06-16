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
    """Plain-English pre-run briefing for desktop/Telegram."""
    project = ctx.project_name or "the linked project"
    parts = [
        f"Before I start, here's the plan for {ctx.ticket_title}.",
        f"It's on {project}.",
    ]
    if ctx.ticket_summary:
        parts.append(f"In short: {ctx.ticket_summary}")
    if ctx.loop_goal:
        parts.append(f"The loop will work toward: {ctx.loop_goal}.")
    if ctx.loop_exit:
        parts.append(f"We're done when: {_one_line(ctx.loop_exit, limit=200)}")
    if ctx.step_count > 1:
        parts.append(
            f"I'll run {ctx.step_count} steps, starting with {ctx.first_step_name}."
        )
        if ctx.first_step_instruction:
            parts.append(f"First up: {ctx.first_step_instruction}")
    else:
        parts.append(f"I'll start with {ctx.first_step_name}.")
    if ctx.steering_notes:
        parts.append(f"I'll also keep in mind: {_one_line(ctx.steering_notes, limit=180)}")
    parts.append(
        "Tell me if you want to change anything, or say you're happy for me to begin. "
        "When you confirm, I'll write the work packet and start the Cursor harness on step one."
    )
    return " ".join(parts)


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
    label = step_name.strip() or "the step"
    if step_index and step_index > 0:
        label = f"Step {step_index}: {label}"
    status = "finished" if passed else "didn't get through"
    summary = _one_line(result_summary, limit=200)
    parts = [f"{label} {status}."]
    if summary and summary.lower() not in {"step completed.", "completed.", "passed."}:
        parts.append(summary)
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun

        if run_id is not None:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if run and run.run_data:
                    run_data = json.loads(run.run_data or "{}") or {}
                    latest = run_data.get("latest_step_report") or {}
                    fields = latest.get("fields") if isinstance(latest.get("fields"), dict) else {}
                    harness_summary = _one_line(str(fields.get("summary") or ""), limit=180)
                    if harness_summary and harness_summary not in (summary or ""):
                        parts.append(f"Harness reported: {harness_summary}")
    except Exception:
        pass
    if next_step_name:
        parts.append(f"Next would be {next_step_name}.")
    parts.append("Tell me if you want to adjust direction, or I'm ready to continue.")
    return " ".join(parts)


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
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return ""
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
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
            voice_body=message,
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
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
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
            voice_body=message,
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
