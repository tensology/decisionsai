"""Human-facing progress updates and time tracking for ticket workflow runs."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from distr.core.db import get_session
from distr.core.db.kanban import KanbanTicket
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
from distr.core.human_engagement import EngagementIntent, HumanEngagementService

logger = logging.getLogger(__name__)

from distr.core.kanban.ticket_time_tracking import (
    add_time_spent_seconds,
    format_time_tracking_seconds,
    parse_time_tracking_seconds,
)


def _human_result_snippet(
    result_text: str,
    *,
    passed: bool,
    step_name: str,
    action_type: str = "",
) -> str:
    clean = re.sub(r"\s+", " ", (result_text or "").strip())
    for prefix in (
        "Cursor reported status: failed",
        "Codex reported status: failed",
        "failed.",
        "Error:",
    ):
        if clean.lower().startswith(prefix.lower()):
            clean = clean[len(prefix) :].strip(" .:-")
    if not clean:
        if passed:
            return "That step finished cleanly."
        return "That step didn't come back with anything useful."

    sentence = re.split(r"[.!?\n]", clean, maxsplit=1)[0].strip()
    if len(sentence) > 180:
        sentence = sentence[:177].rsplit(" ", 1)[0] + "..."

    action = (action_type or "").strip().lower()
    if "cursor" in action or "cursor" in clean.lower():
        worker = "Cursor"
    elif action == "send_to_project_cli" or "codex" in clean.lower():
        worker = "the project CLI"
    else:
        worker = (step_name or "the step").strip() or "the step"

    if passed:
        return f"{worker} came back — {sentence}."
    return f"{worker} hit a snag — {sentence}."


def build_run_start_message(*, ticket_title: str, step_name: str) -> str:
    title = (ticket_title or "this ticket").strip()
    step = (step_name or "the first step").strip()
    return f"Picking up {title}. Starting with {step}."


def build_step_start_message(*, ticket_title: str, step_name: str) -> str:
    title = (ticket_title or "the ticket").strip()
    step = (step_name or "the next step").strip()
    return f"Working on {step} for {title}."


def build_step_done_message(
    *,
    ticket_title: str,
    step_name: str,
    passed: bool,
    result_text: str,
    action_type: str = "",
) -> str:
    title = (ticket_title or "the ticket").strip()
    step = (step_name or "that step").strip()
    detail = _human_result_snippet(
        result_text,
        passed=passed,
        step_name=step,
        action_type=action_type,
    )
    if passed:
        return f"Done with {step} on {title}. {detail}"
    return f"{step} on {title} didn't clear. {detail}"


def build_run_done_message(
    *,
    ticket_title: str,
    status: str,
    warning: str = "",
    elapsed_label: str = "",
) -> str:
    title = (ticket_title or "the ticket").strip()
    normalized = (status or "").strip().lower()
    if warning and "loop" in warning.lower():
        base = f"Got stuck in a loop on {title}, so I stopped the run."
    elif normalized == "completed":
        base = f"Finished working on {title}."
    elif normalized == "cancelled":
        base = f"Stopped work on {title}."
    else:
        base = f"Couldn't get {title} all the way through."
    if elapsed_label:
        base = f"{base} That took about {elapsed_label}."
    return base


def _run_context(run_id: int) -> dict[str, Any]:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        ticket_title = (run_data.get("ticket_title") or "").strip()
        if run.ticket_id and not ticket_title:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
            if ticket:
                ticket_title = (ticket.title or "").strip()
        return {
            "run": run,
            "run_data": run_data,
            "ticket_id": run.ticket_id,
            "board_id": run.board_id,
            "workflow_id": run.workflow_id,
            "ticket_title": ticket_title,
        }


def notify_ticket_workflow_progress(
    *,
    run_id: int,
    body: str,
    state_fingerprint: str,
    step_id: Optional[int] = None,
    priority: str = "normal",
    requires_response: bool = False,
) -> None:
    """Deliver a plain-English workflow update via TTS and/or Telegram."""
    text = (body or "").strip()
    if not text:
        return

    ctx = _run_context(run_id)
    service = HumanEngagementService(allow_telegram=True)
    decision = service.decide(
        EngagementIntent(
            source="workflow",
            surface="proactive",
            kind="workflow_progress",
            priority=priority,  # type: ignore[arg-type]
            subject_type="ticket_workflow_run",
            subject_id=str(ctx.get("ticket_id") or run_id),
            state_fingerprint=state_fingerprint,
            body=text,
            voice_body=text,
            explicit_notification_intent=True,
            workflow_id=ctx.get("workflow_id"),
            run_id=run_id,
            step_id=step_id,
            requires_response=requires_response,
        )
    )
    if not decision.should_send:
        logger.debug(
            "ticket workflow engagement suppressed (%s): %s",
            decision.suppress_reason,
            text[:120],
        )
        return

    outbound = decision.final_voice_text or decision.final_text or text
    try:
        if decision.channel == "desktop":
            from distr.core.signals import speak_text_directly_event_queue

            speak_text_directly_event_queue(outbound)
        elif decision.channel == "telegram":
            from distr.core.signals import get_agent_event_queue

            queue = get_agent_event_queue()
            if queue:
                queue.put(
                    (
                        "send_to_telegram",
                        {
                            "text": outbound,
                            "is_done": False,
                            "explicit_notification_intent": True,
                            "engagement_kind": "workflow_progress",
                            "engagement_source": "workflow",
                            "engagement_priority": priority,
                            "run_id": run_id,
                            "step_id": step_id,
                            "state_fingerprint": state_fingerprint,
                            "allow_voice": decision.format in {"voice", "desktop_tts", "remote_audio"},
                        },
                    ),
                    block=False,
                )
            else:
                from distr.core.signals import speak_text_directly_event_queue

                speak_text_directly_event_queue(outbound)
        else:
            from distr.core.signals import speak_text_directly_event_queue

            speak_text_directly_event_queue(outbound)
    except Exception:
        logger.debug("ticket workflow engagement delivery failed", exc_info=True)

    try:
        from distr.core.orchestration_events import emit_user_notification

        emit_user_notification(
            channel=decision.channel or "chat",
            text=outbound,
            workflow_id=ctx.get("workflow_id"),
            run_id=run_id,
            step_id=step_id,
            ticket_id=ctx.get("ticket_id"),
            board_id=ctx.get("board_id"),
            payload={"engagement_kind": "workflow_progress"},
        )
    except Exception:
        logger.debug("ticket workflow engagement ledger write failed", exc_info=True)


def record_ticket_workflow_elapsed(
    *,
    ticket_id: int,
    run_id: int,
    status: str = "",
    warning: str = "",
) -> Optional[str]:
    """Add elapsed run time to the ticket's time_spent field."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
        if not run or not ticket:
            return None
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        if run_data.get("time_recorded_to_ticket"):
            return ticket.time_spent

        started = run.started_at
        ended = run.completed_at or utc_now_naive()
        if not started or not ended:
            return ticket.time_spent
        if isinstance(started, datetime) and started.tzinfo is not None:
            started = started.replace(tzinfo=None)
        if isinstance(ended, datetime) and ended.tzinfo is not None:
            ended = ended.replace(tzinfo=None)
        elapsed_seconds = max(0, int((ended - started).total_seconds()))
        if elapsed_seconds <= 0:
            return ticket.time_spent

        ticket.time_spent = add_time_spent_seconds(ticket.time_spent, elapsed_seconds)
        run_data["time_recorded_to_ticket"] = True
        run_data["elapsed_seconds_recorded"] = elapsed_seconds
        run.run_data = json.dumps(run_data)
        db.commit()

        elapsed_label = format_time_tracking_seconds(
            parse_time_tracking_seconds(ticket.time_spent)
        ) or format_time_tracking_seconds(elapsed_seconds)
        try:
            notify_ticket_workflow_progress(
                run_id=run_id,
                body=build_run_done_message(
                    ticket_title=(ticket.title or run_data.get("ticket_title") or ""),
                    status=status or (run.status or ""),
                    warning=warning,
                    elapsed_label=elapsed_label,
                ),
                state_fingerprint=f"run_done:{run_id}:{status}:{warning}",
                priority="high" if (status or "").strip().lower() == "failed" else "normal",
            )
        except Exception:
            logger.debug("run-done engagement failed", exc_info=True)
        return ticket.time_spent


def notify_ticket_workflow_step_started(run_id: int, step_id: int) -> None:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not run or not step or not run.ticket_id:
            return
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        ticket_title = (run_data.get("ticket_title") or "").strip()
        if not ticket_title:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
            ticket_title = (ticket.title if ticket else "") or ""
        step_name = (step.name or "").strip() or f"step {step.position + 1}"
        body = build_step_start_message(ticket_title=ticket_title, step_name=step_name)
        if int(run.current_step_id or 0) == int(step_id) and not run_data.get("run_start_announced"):
            body = build_run_start_message(ticket_title=ticket_title, step_name=step_name)
            run_data["run_start_announced"] = True
            run.run_data = json.dumps(run_data)
            db.commit()
    notify_ticket_workflow_progress(
        run_id=run_id,
        step_id=step_id,
        body=body,
        state_fingerprint=f"step_start:{step_id}",
    )


def notify_ticket_workflow_step_finished(
    *,
    run_id: int,
    step_id: int,
    passed: bool,
    result_text: str,
) -> None:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not run or not step or not run.ticket_id:
            return
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        ticket_title = (run_data.get("ticket_title") or "").strip()
        if not ticket_title:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
            ticket_title = (ticket.title if ticket else "") or ""
        step_name = (step.name or "").strip() or f"step {step.position + 1}"
        action_type = (step.action_type or "").strip()
    notify_ticket_workflow_progress(
        run_id=run_id,
        step_id=step_id,
        body=build_step_done_message(
            ticket_title=ticket_title,
            step_name=step_name,
            passed=passed,
            result_text=result_text,
            action_type=action_type,
        ),
        state_fingerprint=f"step_done:{step_id}:{'pass' if passed else 'fail'}",
        priority="normal" if passed else "high",
    )
