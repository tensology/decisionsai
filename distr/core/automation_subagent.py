"""Background automation subagent — runs automations off the orchestrator hot path."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable

from distr.core.db import get_session
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

logger = logging.getLogger(__name__)

_active_threads: dict[int, threading.Thread] = {}
_active_run_lock_keys: set[int] = set()
_thread_lock = threading.Lock()


def _run_lock_key_from_automation(automation: dict[str, Any]) -> int | None:
    record_id = automation.get("record_id")
    if record_id is not None:
        try:
            return int(record_id)
        except (TypeError, ValueError):
            pass
    raw = automation.get("workflow_id")
    if raw is None:
        raw = automation.get("id")
    if raw is None:
        return None
    try:
        return int(str(raw).replace("wf_", "").replace("auto_", "").strip())
    except (TypeError, ValueError):
        return None


def _workflow_id_from_automation(automation: dict[str, Any]) -> int | None:
    return _run_lock_key_from_automation(automation)


def try_acquire_workflow_run(workflow_id: int) -> bool:
    """Return False when this automation already has a live subagent thread."""
    with _thread_lock:
        if int(workflow_id) in _active_run_lock_keys:
            return False
        _active_run_lock_keys.add(int(workflow_id))
        return True


def release_workflow_run(workflow_id: int | None) -> None:
    if workflow_id is None:
        return
    with _thread_lock:
        _active_run_lock_keys.discard(int(workflow_id))


def workflow_run_in_progress(workflow_id: int) -> bool:
    with _thread_lock:
        return int(workflow_id) in _active_run_lock_keys


def _json_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(str(raw))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _notify_automation_data_changed() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


def _put_main_event(event: str, data: dict[str, Any]) -> bool:
    """Enqueue an event for the main Qt event loop (TTS, Telegram, etc.)."""
    try:
        from distr.core.signals import get_agent_event_queue

        queue = get_agent_event_queue()
        if queue is not None:
            queue.put((event, data), block=False)
            return True
    except Exception:
        logger.debug("Automation subagent: could not enqueue %s", event, exc_info=True)
    return False


def _speak_orchestrator(text: str) -> bool:
    message = (text or "").strip()
    if not message:
        return False
    try:
        from distr.core.signals import speak_text_directly_event_queue

        speak_text_directly_event_queue(message)
        return True
    except Exception:
        logger.debug("Automation subagent: orchestrator speak failed", exc_info=True)
        return False


def _telegram_connected() -> bool:
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        manager = getattr(app, "telegram_manager", None) if app else None
        if manager is None:
            return False
        if hasattr(manager, "is_connected"):
            return bool(manager.is_connected())
        return True
    except Exception:
        return False


def _deliver_automation_speech(
    speech_text: str,
    *,
    automation_name: str,
    manual: bool,
) -> tuple[str, str]:
    """Deliver the automation body to the user. Returns (channel, status_detail)."""
    body = (speech_text or "").strip()
    if not body:
        return "none", "nothing to deliver"

    if _telegram_connected():
        queued = _put_main_event(
            "send_to_telegram",
            {
                "text": body[:3500],
                "is_done": True,
                "provider": "tool",
                "skip_screenshot": True,
                "explicit_artifact_intent": False,
                "input_type": "automation",
                # Automations must reach the Telegram chat, not the remote web UI.
                "force_telegram_delivery": True,
                "explicit_notification_intent": True,
                "engagement_source": "automation",
            },
        )
        if queued:
            return "telegram", "queued for Telegram"
        return "telegram", "Telegram is connected but the send queue is unavailable"

    queued = _speak_orchestrator(body[:650])
    if queued:
        return "desktop_tts", "queued for voice"
    return "none", "voice queue unavailable"


def _orchestrator_delivery_ack(
    *,
    automation_name: str,
    success: bool,
    channel: str,
    channel_detail: str,
) -> None:
    """Brief spoken status — never recap where the summary was routed."""
    if success:
        if channel == "desktop_tts":
            return
        if channel == "telegram":
            _speak_orchestrator("Done. I put it on Telegram.")
            return
        _speak_orchestrator("Done.")
        return
    label = (automation_name or "That").strip()
    detail = (channel_detail or "").strip()
    if not detail or "run history" in detail.lower() or "queue" in detail.lower():
        _speak_orchestrator(f"{label} didn't work.")
        return
    _speak_orchestrator(f"{label} didn't work. {detail}")


def update_automation_run(
    run_id: int,
    *,
    status: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> None:
    now = datetime.utcnow().replace(microsecond=0)
    with get_session() as session:
        run = session.query(AutomationRun).filter(AutomationRun.id == int(run_id)).first()
        if run:
            run.status = status
            data = _json_config(run.run_data)
            data["summary"] = summary
            data["message"] = summary
            if extra:
                data.update(extra)
            run.run_data = json.dumps(data, ensure_ascii=False, default=str)
            if status in {"completed", "failed", "skipped", "dispatched"}:
                if status != "dispatched":
                    run.completed_at = now
            auto_row = session.query(Automation).filter(Automation.id == run.automation_id).first()
            if auto_row:
                auto_row.last_run_at = now
                auto_row.modified_date = now
            session.commit()
            _notify_automation_data_changed()
            return
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return
        run.status = status
        data = _json_config(run.run_data)
        data["summary"] = summary
        data["message"] = summary
        if extra:
            data.update(extra)
        run.run_data = json.dumps(data, ensure_ascii=False, default=str)
        if status in {"completed", "failed", "skipped", "dispatched"}:
            if status != "dispatched":
                run.completed_at = now
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == run.workflow_id).first()
        if workflow:
            workflow.last_run_at = now
            workflow.modified_date = now
        session.commit()
    _notify_automation_data_changed()


def finalize_automation_subagent_from_agent(
    run_id: int,
    *,
    automation_name: str,
    success: bool,
    summary: str,
    speech_text: str = "",
) -> None:
    """Called when an instruction automation finishes inside the live agent."""
    channel, channel_detail = ("none", "saved in run history")
    if success and speech_text:
        channel, channel_detail = _deliver_automation_speech(
            speech_text,
            automation_name=automation_name,
            manual=True,
        )
    status = "completed" if success else "failed"
    update_automation_run(
        int(run_id),
        status=status,
        summary=summary,
        extra={
            "delivery_channel": channel,
            "delivery_detail": channel_detail,
            "execution_mode": "agent_chat_subagent",
        },
    )
    _orchestrator_delivery_ack(
        automation_name=automation_name,
        success=success,
        channel=channel,
        channel_detail=channel_detail,
    )


def _execute_tool_automation(
    automation: dict[str, Any],
    *,
    manual: bool,
) -> tuple[bool, str, str]:
    from distr.core.automation_tool_runner import run_automation_tool
    from distr.core.engagement_gates import proactive_delivery_blocked

    action_config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
    tool_name = str(action_config.get("tool") or "").strip()
    tool_args = dict(action_config.get("args") or {})
    tool_args["from_automation_run"] = True

    blocked, reason = proactive_delivery_blocked(
        delivery_kind="automation_tool",
        body=str(automation.get("name") or ""),
        manual=manual,
        preset_id=str(automation.get("preset_id") or ""),
    )
    if blocked:
        summary = {
            "daily_plan_opt_out": "Skipped — you asked not to receive scheduled daily plans.",
            "user_likely_asleep": "Skipped — you do not look awake yet. I will try again on the next schedule.",
        }.get(reason, "Skipped by engagement policy.")
        return False, summary, ""

    tool_result = run_automation_tool(tool_name, tool_args)
    output = str(tool_result.get("output") or "").strip()
    spoken = str(tool_result.get("spoken_summary") or output).strip()
    success = bool(tool_result.get("success"))
    summary = spoken or output or ("Tool run finished." if success else "Tool run failed.")
    return success, summary, spoken or summary


def _execute_instruction_automation(
    automation: dict[str, Any],
    *,
    chat_id: int | None,
    speak: bool,
    run_id: int,
) -> tuple[bool, str, str]:
    from distr.core.agent.services.llm.bulk_instruction import augment_bulk_instruction
    from distr.core.automation_orchestrator import automation_prompt, emit_to_agent_chat

    prompt = augment_bulk_instruction(automation_prompt(automation), source="automation")
    if not chat_id:
        return False, "Automation needs an active agent chat before it can run.", ""

    try:
        from distr.core.signals import signal_manager

        options = {
            "skip_user_persist": True,
            "automation_run_id": int(run_id),
            "automation_name": str(automation.get("name") or "Automation"),
        }
        signal_manager.web_send_to_agent_requested.emit(
            int(chat_id),
            prompt,
            False,
            None,
            None,
            options,
        )
        return True, "Automation is running in a background agent thread.", prompt
    except Exception as exc:
        logger.debug("Instruction automation dispatch failed", exc_info=True)
        try:
            emit_to_agent_chat(int(chat_id), prompt, bool(speak), skip_user_persist=True)
            return True, "Automation is running in a background agent thread.", prompt
        except Exception:
            return False, f"Automation could not reach the orchestrator: {exc}", ""


def _automation_worker(
    *,
    automation: dict[str, Any],
    run_id: int,
    manual: bool,
    chat_id: int | None,
    speak: bool,
    schedule_metadata: dict[str, Any] | None,
    emit_event: Callable[..., int | None] | None,
) -> None:
    name = str(automation.get("name") or "Automation")
    workflow_id = _workflow_id_from_automation(automation)
    tool_name = ""
    action_config = automation.get("action_config")
    if isinstance(action_config, dict):
        tool_name = str(action_config.get("tool") or "").strip()

    try:
        if tool_name:
            success, summary, speech_text = _execute_tool_automation(automation, manual=manual)
            if not success and summary.startswith("Skipped"):
                update_automation_run(
                    run_id,
                    status="skipped",
                    summary=summary,
                    extra={"execution_mode": "automation_subagent_tool"},
                )
                _orchestrator_delivery_ack(
                    automation_name=name,
                    success=False,
                    channel="none",
                    channel_detail=summary,
                )
                return

            channel, channel_detail = ("none", "saved in run history")
            if success and speech_text and speak:
                channel, channel_detail = _deliver_automation_speech(
                    speech_text,
                    automation_name=name,
                    manual=manual,
                )

            status = "completed" if success else "failed"
            update_automation_run(
                run_id,
                status=status,
                summary=summary,
                extra={
                    "delivery_channel": channel,
                    "delivery_detail": channel_detail,
                    "execution_mode": "automation_subagent_tool",
                    "tool": tool_name,
                    **(schedule_metadata or {}),
                },
            )

            if emit_event:
                emit_event(
                    automation=automation,
                    event_type="worker_completed" if success else "worker_failed",
                    status=status,
                    summary=summary,
                    payload={
                        "workflow_run_id": run_id,
                        "manual": manual,
                        "delivery_channel": channel,
                        **(schedule_metadata or {}),
                    },
                )

            _orchestrator_delivery_ack(
                automation_name=name,
                success=success,
                channel=channel,
                channel_detail=channel_detail,
            )
            return

        # Instruction automations: hand off to the agent subprocess and let it finalize.
        success, summary, _prompt = _execute_instruction_automation(
            automation,
            chat_id=chat_id,
            speak=speak,
            run_id=run_id,
        )
        if not success:
            update_automation_run(
                run_id,
                status="failed",
                summary=summary,
                extra={"execution_mode": "automation_subagent_instruction"},
            )
            _orchestrator_delivery_ack(
                automation_name=name,
                success=False,
                channel="none",
                channel_detail=summary,
            )
            return

        update_automation_run(
            run_id,
            status="running",
            summary=summary,
            extra={"execution_mode": "automation_subagent_instruction"},
        )
        _speak_orchestrator("On it.")
    except Exception as exc:
        logger.error("Automation subagent failed for run %s: %s", run_id, exc, exc_info=True)
        update_automation_run(
            run_id,
            status="failed",
            summary=f"Automation subagent failed: {exc}",
            extra={"execution_mode": "automation_subagent_error"},
        )
        _orchestrator_delivery_ack(
            automation_name=name,
            success=False,
            channel="none",
            channel_detail=str(exc),
        )
    finally:
        release_workflow_run(workflow_id)
        with _thread_lock:
            _active_threads.pop(int(run_id), None)


def start_automation_subagent(
    *,
    automation: dict[str, Any],
    run_id: int,
    manual: bool = True,
    chat_id: int | None = None,
    speak: bool = True,
    schedule_metadata: dict[str, Any] | None = None,
    emit_event: Callable[..., int | None] | None = None,
) -> None:
    """Launch automation work on a daemon thread and return immediately."""
    workflow_id = _workflow_id_from_automation(automation)
    if workflow_id is not None and not try_acquire_workflow_run(workflow_id):
        logger.warning(
            "Automation subagent: workflow %s already running; skipping duplicate run %s",
            workflow_id,
            run_id,
        )
        update_automation_run(
            int(run_id),
            status="skipped",
            summary="Automation is already running.",
            extra={"execution_mode": "automation_subagent_duplicate"},
        )
        _orchestrator_delivery_ack(
            automation_name=str(automation.get("name") or "Automation"),
            success=False,
            channel="none",
            channel_detail="Automation is already running.",
        )
        return

    thread = threading.Thread(
        target=_automation_worker,
        kwargs={
            "automation": automation,
            "run_id": int(run_id),
            "manual": bool(manual),
            "chat_id": chat_id,
            "speak": bool(speak),
            "schedule_metadata": schedule_metadata,
            "emit_event": emit_event,
        },
        name=f"automation-subagent-{run_id}",
        daemon=True,
    )
    with _thread_lock:
        _active_threads[int(run_id)] = thread
    thread.start()
