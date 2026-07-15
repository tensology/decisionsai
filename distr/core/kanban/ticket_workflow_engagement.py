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


def _step_label(step_index: int | None, step_name: str) -> str:
    name = (step_name or "").strip()
    if step_index and step_index > 0:
        if name:
            return f"Step {step_index}: {name}"
        return f"Step {step_index}"
    return name or "Step"


def _brief_failure(result_text: str) -> str:
    clean = re.sub(r"\s+", " ", (result_text or "").strip())
    low = clean.lower()
    if "no linked project" in low:
        return "Link the ticket to a project first."
    if "llm judgment not available" in low:
        return "LLM validation is unavailable."
    if "usage limit" in low or "hit your usage" in low:
        return "Cursor usage limit reached — switch backend or wait for reset."
    if "bypassed" in low:
        return "No project was linked for this step."
    sentence = re.split(r"[.!?\n]", clean, maxsplit=1)[0].strip()
    if len(sentence) > 100:
        sentence = sentence[:97] + "..."
    return sentence or "Check the activity feed for details."


def _ticket_subject(ticket_title: str, fallback: str = "the ticket") -> str:
    clean = re.sub(r"\s+", " ", (ticket_title or "").strip())
    return clean or fallback


def build_run_start_message(
    *,
    ticket_title: str,
    step_name: str,
    step_index: int | None = None,
) -> str:
    return f"{_ticket_subject(ticket_title)}: {_step_label(step_index, step_name)} has started."


def build_step_start_message(
    *,
    ticket_title: str,
    step_name: str,
    step_index: int | None = None,
) -> str:
    return build_run_start_message(
        ticket_title=ticket_title,
        step_name=step_name,
        step_index=step_index,
    )


def build_step_done_message(
    *,
    ticket_title: str,
    step_name: str,
    passed: bool,
    result_text: str,
    action_type: str = "",
    step_index: int | None = None,
) -> str:
    label = _step_label(step_index, step_name)
    if passed:
        return f"{_ticket_subject(ticket_title)}: {label} passed."
    return f"{_ticket_subject(ticket_title)}: {label} failed. {_brief_failure(result_text)}"


def build_run_done_message(
    *,
    ticket_title: str,
    status: str,
    warning: str = "",
    elapsed_label: str = "",
) -> str:
    normalized = (status or "").strip().lower()
    if warning and "loop" in warning.lower():
        base = "Run stopped in a loop."
    elif normalized == "completed":
        base = f"{_ticket_subject(ticket_title)}: Run finished."
    elif normalized == "cancelled":
        base = f"{_ticket_subject(ticket_title)}: Run stopped."
    else:
        base = f"{_ticket_subject(ticket_title)}: Run failed."
    if elapsed_label:
        base = f"{base} Elapsed {elapsed_label}."
    return base


def build_ide_handoff_waiting_message(
    *,
    ticket_title: str = "",
    project_name: str = "",
    backend_label: str = "Cursor",
) -> str:
    subject = (ticket_title or project_name or "your ticket").strip()
    return (
        f"I opened {backend_label} for {subject}. "
        "Work there, then let me know when you're ready to continue."
    )


def build_ide_handoff_waiting_voice(
    *,
    ticket_title: str = "",
    project_name: str = "",
    backend_label: str = "Cursor",
) -> str:
    subject = (ticket_title or project_name or "").strip()
    if subject:
        return f"I opened {backend_label} for {subject}."
    return f"I opened {backend_label}. Take a look when you're ready."


def build_workflow_waiting_message(
    *,
    run_id: int,
    step_id: int,
    result_text: str,
    ticket_title: str = "",
) -> str:
    clean = (result_text or "").strip()
    low = clean.lower()
    if "waiting in ide" in low or "ide opened" in low or "work packet" in low:
        backend = "Cursor"
        if "codex" in low:
            backend = "Codex"
        return build_ide_handoff_waiting_message(
            ticket_title=ticket_title,
            backend_label=backend,
        )
    summary = _brief_failure(clean) if "failed" in low else clean
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."
    label = (ticket_title or "Workflow").strip()
    return (
        f"{label} needs your input. "
        f"{summary or 'It paused for a decision.'} "
        "Tell me if you want to adjust direction, or I'm ready to continue."
    )


def build_workflow_waiting_voice(
    *,
    step_id: int,
    result_text: str,
    ticket_title: str = "",
    step_name: str = "",
) -> str:
    from distr.core.workflow.wait_handoff import wait_handoff_voice_text

    return wait_handoff_voice_text(
        step_name=step_name or ticket_title,
        result_text=result_text,
        ticket_title=ticket_title,
    )


def build_workflow_waiting_nudge(
    *,
    workflow_name: str,
    ticket_title: str = "",
    step_name: str = "",
    waiting_kind: str = "",
) -> tuple[str, str]:
    """Actionable text + voice for initiative / background workflow-waiting alerts."""
    subject = (ticket_title or workflow_name or "A workflow").strip()
    step = (step_name or "").strip()
    kind = (waiting_kind or "").strip().lower()

    if kind == "run_briefing":
        text = (
            f"{subject} is ready to start but waiting for your go-ahead. "
            "Open Workflows → Active Runs, or tell me **go ahead** or **continue** to start."
        )
        voice = f"{subject} is ready to start. Say go ahead or continue to proceed."
    elif kind == "step_review":
        step_bit = f" after {step}" if step else ""
        text = (
            f"{subject} paused{step_bit} for a quick checkpoint. "
            "Open Workflows → Active Runs, or tell me **continue** to proceed, **stop** to cancel."
        )
        voice = f"{subject} paused for a checkpoint. Say continue to proceed, or stop to cancel."
    elif kind == "approval":
        step_bit = f" ({step})" if step else ""
        text = (
            f"{subject} needs your approval{step_bit}. "
            "Open Workflows → Active Runs and approve the step, or tell me **approve** or **continue**."
        )
        voice = f"{subject} needs your approval. Open Workflows or say approve to continue."
    elif kind == "route_approval":
        text = (
            f"{subject} wants to change execution route. "
            "Open Workflows → Active Runs to approve or reject, or tell me what to do."
        )
        voice = f"{subject} needs route approval. Open Workflows or tell me how to proceed."
    elif kind in {"ide_handoff", "needs_human_input", "worker_needs_input"}:
        text = (
            f"{subject} is waiting on you in the linked IDE or harness. "
            "Finish there, then tell me **continue**, or open Workflows → Active Runs."
        )
        voice = f"{subject} is waiting in your IDE. Say continue when you're ready."
    else:
        step_bit = f" at {step}" if step else ""
        text = (
            f"{subject} is paused{step_bit} and needs a decision. "
            "Open Workflows → Active Runs, or tell me **continue** to proceed, **stop** to cancel."
        )
        voice = f"{subject} is paused. Say continue to proceed, or stop to cancel."

    return text, voice


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


def _telegram_manager_from_app():
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        return getattr(app, "telegram_manager", None) if app else None
    except Exception:
        return None


def notify_ticket_workflow_progress(
    *,
    run_id: int,
    body: str,
    state_fingerprint: str,
    step_id: Optional[int] = None,
    priority: str = "normal",
    requires_response: bool = False,
    voice_body: Optional[str] = None,
) -> None:
    """Deliver a plain-English workflow update via TTS and/or Telegram."""
    text = (body or "").strip()
    if not text:
        return
    spoken = (voice_body or text).strip()

    ctx = _run_context(run_id)
    interaction = None
    reply_markup = None
    if requires_response and ctx.get("workflow_id"):
        try:
            from distr.core.workflow.interactions import (
                create_workflow_interaction,
                telegram_reply_markup,
            )

            waiting_kind = str((ctx.get("run_data") or {}).get("waiting_kind") or "feedback")
            manager = _telegram_manager_from_app()
            telegram_chat_id = getattr(manager, "telegram_user_id", None) if manager else None
            interaction = create_workflow_interaction(
                workflow_id=int(ctx["workflow_id"]),
                run_id=run_id,
                step_id=step_id,
                kind=waiting_kind,
                telegram_chat_id=telegram_chat_id,
            )
            reply_markup = telegram_reply_markup(interaction)
        except Exception:
            logger.warning("Could not create durable workflow interaction for run %s", run_id, exc_info=True)
    service = HumanEngagementService(
        allow_telegram=True,
        telegram_manager=_telegram_manager_from_app(),
    )
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
            voice_body=spoken,
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
        elif decision.channel == "remote":
            from distr.core.integrations.telegram.remote_tts_delivery import enqueue_remote_tts_delivery

            if not enqueue_remote_tts_delivery(
                outbound,
                data={
                    "mode": "proactive",
                    "source_command": "workflow_progress",
                    "engagement_kind": "workflow_progress",
                    "engagement_source": "workflow",
                    "engagement_priority": priority,
                    "run_id": run_id,
                    "step_id": step_id,
                    "state_fingerprint": state_fingerprint,
                    "workflow_id": ctx.get("workflow_id"),
                    "ticket_id": ctx.get("ticket_id"),
                    "board_id": ctx.get("board_id"),
                    "ticket_title": ctx.get("ticket_title"),
                    "explicit_notification_intent": True,
                    "requires_response": requires_response,
                    "interaction_token": interaction.get("token") if interaction else None,
                },
            ):
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
                            "workflow_id": ctx.get("workflow_id"),
                            "ticket_id": ctx.get("ticket_id"),
                            "board_id": ctx.get("board_id"),
                            "ticket_title": ctx.get("ticket_title"),
                            "requires_response": requires_response,
                            "interaction_token": interaction.get("token") if interaction else None,
                            "reply_markup": reply_markup,
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
            payload={
                "engagement_kind": "workflow_progress",
                "ticket_title": ctx.get("ticket_title"),
                "state_fingerprint": state_fingerprint,
            },
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
        step_index = int(step.position) + 1
        body = build_step_start_message(
            ticket_title=ticket_title,
            step_name=step_name,
            step_index=step_index,
        )
        if int(run.current_step_id or 0) == int(step_id) and not run_data.get("run_start_announced"):
            body = build_run_start_message(
                ticket_title=ticket_title,
                step_name=step_name,
                step_index=step_index,
            )
            run_data["run_start_announced"] = True
            run.run_data = json.dumps(run_data)
            db.commit()
    notify_ticket_workflow_progress(
        run_id=run_id,
        step_id=step_id,
        body=body,
        state_fingerprint=f"step_start:{step_id}",
        priority="high",
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
        step_index = int(step.position) + 1
    notify_ticket_workflow_progress(
        run_id=run_id,
        step_id=step_id,
        body=build_step_done_message(
            ticket_title=ticket_title,
            step_name=step_name,
            passed=passed,
            result_text=result_text,
            action_type=action_type,
            step_index=step_index,
        ),
        state_fingerprint=f"step_done:{step_id}:{'pass' if passed else 'fail'}",
        priority="normal" if passed else "high",
    )
