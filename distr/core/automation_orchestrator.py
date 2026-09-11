"""Automation dispatch with strict surface ownership."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from distr.core.agent.services.llm.bulk_instruction import augment_bulk_instruction
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, DevelopmentWorkItem

logger = logging.getLogger(__name__)

AUTOMATION_SURFACE = "automation"
_UNTRUSTED_MESSAGE_MAX_CHARS = 100_000
_UNTRUSTED_ATTACHMENT_MAX_FILES = 100
_UNTRUSTED_ATTACHMENT_MAX_AGE_SECONDS = 24 * 60 * 60


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


def automation_id(workflow_id: int | str) -> str:
    from distr.core.automation.store import legacy_public_id, public_id

    try:
        return public_id(int(workflow_id))
    except (TypeError, ValueError):
        return legacy_public_id(workflow_id)


def is_automation_workflow(workflow: AutoWorkflow | None) -> bool:
    from distr.core.automation.store import is_automation_workflow as _is_auto

    return _is_auto(workflow)


def serialize_automation_workflow(workflow: AutoWorkflow) -> dict[str, Any]:
    from distr.core.automation.store import get_automation, serialize_legacy_workflow

    migrated = get_automation(f"wf_{workflow.id}")
    if migrated and migrated.get("record_id"):
        return migrated
    return serialize_legacy_workflow(workflow)


def automation_prompt(automation: dict[str, Any]) -> str:
    instruction = str(automation.get("instruction") or "").strip()
    return (
        "This is a DecisionsAI automation run. Treat the automation instruction "
        "as the user's requested outcome, decide the natural next orchestration "
        "step, and respond organically. If sub-agents or tools are needed, route "
        "the work through the orchestrator instead of pretending it is complete. "
        "If the instruction contains multiple requested actions, execute them as "
        "an ordered queue and report each unavailable screen, blocker, or failed "
        "step honestly.\n\n"
        f"Automation: {automation.get('name') or 'Untitled Automation'}\n"
        f"Instruction:\n{instruction}"
    )


def dispatch_matching_channel_automations(
    *,
    source: str,
    message_text: str,
    source_thread_id: str = "",
    source_message_id: str = "",
) -> list[dict[str, Any]]:
    """Dispatch active event automations matching one incoming channel item."""
    from distr.core.automation.store import list_automations

    normalized = str(source or "").strip().lower()
    if normalized not in {"whatsapp", "telegram", "gmail"}:
        return []
    results: list[dict[str, Any]] = []
    for automation in list_automations():
        if str(automation.get("status") or "active").lower() != "active":
            continue
        if str(automation.get("automation_type") or "") != "channel_intake":
            continue
        config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
        source_config = config.get("source_config") if isinstance(config.get("source_config"), dict) else {}
        if str(source_config.get("source") or "").strip().lower() != normalized:
            continue
        run = dict(automation)
        run["_invocation_context"] = {
            "origin_surface": normalized,
            "origin_thread_id": str(source_thread_id or "").strip(),
            "origin_message_id": str(source_message_id or "").strip(),
            "reply_surface": "telegram" if normalized == "telegram" else normalized,
            "untrusted_message_text": str(message_text or "").strip(),
        }
        results.append(dispatch_automation_to_current_chat(run, manual=False))
    return results


def _untrusted_channel_attachment(automation: dict[str, Any]) -> dict[str, Any] | None:
    invocation = automation.get("_invocation_context")
    if not isinstance(invocation, dict):
        return None
    message = str(invocation.get("untrusted_message_text") or "")[:_UNTRUSTED_MESSAGE_MAX_CHARS]
    if not message:
        return None
    folder = Path.home() / ".decisions" / "workspaces" / "projects" / "untrusted-automation-inputs"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    existing = sorted(
        (item for item in folder.glob("channel-message-*.txt") if item.is_file()),
        key=lambda item: item.stat().st_mtime,
    )
    for stale in existing:
        if now - stale.stat().st_mtime > _UNTRUSTED_ATTACHMENT_MAX_AGE_SECONDS:
            stale.unlink(missing_ok=True)
    remaining = [item for item in existing if item.exists()]
    for overflow in remaining[: max(0, len(remaining) - _UNTRUSTED_ATTACHMENT_MAX_FILES + 1)]:
        overflow.unlink(missing_ok=True)
    path = folder / f"channel-message-{uuid.uuid4().hex}.txt"
    payload = {
        "trust": "untrusted_external_data",
        "source": str(invocation.get("origin_surface") or "external"),
        "source_thread_id": str(invocation.get("origin_thread_id") or ""),
        "source_message_id": str(invocation.get("origin_message_id") or ""),
        "message": message,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return {
        "name": "Untrusted external channel message",
        "path": str(path),
        "mime_type": "application/json",
        "size": path.stat().st_size,
        "trust": "untrusted_external_data",
    }


def emit_automation_event(
    *,
    automation: dict[str, Any],
    event_type: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> int | None:
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        return emit_orchestration_event(
            source="automation",
            event_type=event_type,
            status=status,
            workflow_id=automation.get("workflow_id"),
            summary=summary,
            payload={
                "surface": AUTOMATION_SURFACE,
                "subtype": event_type,
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "automation_type": automation.get("automation_type"),
                "preset_id": automation.get("preset_id") or "",
                "is_workflow_attached": True,
                **(payload or {}),
            },
        )
    except Exception:
        logger.debug("Automation orchestration event failed", exc_info=True)
        return None


def resolve_current_agent_chat_id(settings: dict[str, Any] | None = None) -> int | None:
    try:
        from distr.core.chat import ChatService

        return ChatService.get_current_chat_id()
    except Exception:
        return None


def emit_to_agent_chat(
    chat_id: int,
    prompt: str,
    speak: bool = True,
    *,
    skip_user_persist: bool = False,
) -> None:
    from distr.core.db import get_session
    from distr.core.workflow.development_threads import is_development_thread

    with get_session() as db:
        if is_development_thread(db, int(chat_id)):
            from distr.core.workflow.development_control import dispatch_command, enqueue_command

            command = enqueue_command(
                int(chat_id),
                prompt,
                source="orchestrator",
                command_type="automation_instruction",
                metadata={"speak": bool(speak)},
            )
            dispatch_command(int(command["id"]))
            return
    from distr.core.signals import signal_manager

    options = {"skip_user_persist": True} if skip_user_persist else None
    signal_manager.web_send_to_agent_requested.emit(
        int(chat_id),
        prompt,
        bool(speak),
        None,
        None,
        options,
    )


def _record_dispatch_run(
    *,
    automation: dict[str, Any],
    status: str,
    summary: str,
    event_ids: list[int],
    manual: bool,
    prompt: str,
    chat_id: int | None,
    schedule_metadata: dict[str, Any] | None,
    execution_mode: str = "agent_chat_orchestrator",
    tool_result: dict[str, Any] | None = None,
) -> int | None:
    from distr.core.automation.store import record_automation_run

    return record_automation_run(
        automation=automation,
        status=status,
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=prompt,
        chat_id=chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode=execution_mode,
        tool_result=tool_result,
    )


def _bound_tool_name(automation: dict[str, Any]) -> str:
    action_config = automation.get("action_config")
    if not isinstance(action_config, dict):
        return ""
    return str(action_config.get("tool") or "").strip()


def _development_chat_id(automation: dict[str, Any]) -> int | None:
    try:
        value = int(automation.get("thread_chat_id") or 0)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    action_config = automation.get("action_config")
    if not isinstance(action_config, dict):
        return None
    try:
        value = int(action_config.get("development_chat_id") or 0)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def ensure_automation_thread(automation: dict[str, Any]) -> int:
    """Create or refresh the one durable Development thread owned by an automation."""
    from distr.core.chat import _chat_params
    from distr.core.db import Chat
    from distr.core.db.automation import Automation
    from distr.core.db.kanban import KanbanBoard
    from distr.core.workflow.development_threads import ensure_development_thread, mark_development_thread

    record_id = automation.get("record_id")
    if not record_id:
        raise ValueError("A first-class automation record is required.")
    board_id = automation.get("linked_board_id")
    project_id = automation.get("linked_project_id")
    workflow_id = automation.get("optional_workflow_id")
    board_key = None
    board_provider = None
    with get_session() as session:
        board = session.get(KanbanBoard, int(board_id)) if board_id is not None else None
        if board_id is not None and board is None:
            raise ValueError("The linked board no longer exists.")
        if board is not None:
            source = str(board.source or "database").strip().lower()
            if source in {"jira", "trello"} and str(board.external_board_id or "").strip():
                board_provider = source
                board_key = f"{source}:{str(board.external_board_id).strip()}"
            else:
                board_provider = "local"
                board_key = f"decisions:{int(board.id)}"
            project_id = board.default_project_id
        existing_id = _development_chat_id(automation)
        existing = session.get(Chat, int(existing_id)) if existing_id else None
        expected_identity = f"source:automation:{str(automation.get('id') or '').strip().lower()}"
        owned_item = (
            session.query(DevelopmentWorkItem)
            .filter(
                DevelopmentWorkItem.chat_id == int(existing_id),
                DevelopmentWorkItem.source_type == "automation",
                DevelopmentWorkItem.identity_key == expected_identity,
            )
            .first()
            if existing_id
            else None
        )
        if existing is None or existing.parent_id is not None or owned_item is None:
            existing = None
        existing_chat_id = int(existing.id) if existing is not None else None

    title = str(automation.get("name") or "Automation").strip()
    if existing_chat_id is None:
        chat_id = ensure_development_thread(
            workflow_id=int(workflow_id) if workflow_id is not None else None,
            title=title,
            project_id=int(project_id) if project_id is not None else None,
            source_type="automation",
            source_ref=str(automation.get("id")),
            board_key=board_key,
            board_provider=board_provider,
        )
    else:
        chat_id = existing_chat_id
        with get_session() as session:
            root = session.get(Chat, chat_id)
            if root is not None:
                root.title = title
                root.project_id = int(project_id) if project_id is not None else None
                config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
                provider = str(config.get("model_provider") or config.get("provider") or "").strip().lower()
                model = str(config.get("model") or config.get("model_name") or "").strip()
                root.provider = provider or None
                root.model_name = model or None
                root.route_mode = (
                    "manual"
                    if provider and model and not bool(config.get("adaptive_model_routing", False))
                    else "auto"
                )
                session.commit()
        mark_development_thread(
            chat_id,
            workflow_id=int(workflow_id) if workflow_id is not None else None,
            source_type="automation",
            source_ref=str(automation.get("id")),
            board_key=board_key,
            board_provider=board_provider,
        )

    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
        provider = str(config.get("model_provider") or config.get("provider") or "").strip().lower()
        model = str(config.get("model") or config.get("model_name") or "").strip()
        if root is not None:
            root.provider = provider or None
            root.model_name = model or None
            root.route_mode = (
                "manual"
                if provider and model and not bool(config.get("adaptive_model_routing", False))
                else "auto"
            )
            params = _chat_params(root.params)
            params["automation_run"] = {
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "provider": provider,
                "model": model,
                "reasoning_effort": str(config.get("reasoning_effort") or "medium"),
                "fallback_backend": str(config.get("fallback_backend") or ""),
                "fallback_model_provider": str(config.get("fallback_model_provider") or ""),
                "fallback_model": str(config.get("fallback_model") or ""),
            }
            root.params = json.dumps(params, ensure_ascii=False, default=str)
        row = session.get(Automation, int(record_id))
        if row is None:
            raise ValueError("Automation no longer exists.")
        row.thread_chat_id = int(chat_id)
        row.project_id = int(project_id) if project_id is not None else None
        config = _json_config(row.action_config)
        config["development_chat_id"] = int(chat_id)
        if project_id is None:
            config.pop("linked_project_id", None)
        else:
            config["linked_project_id"] = int(project_id)
        row.action_config = json.dumps(config, ensure_ascii=False, default=str)
        session.commit()
    return int(chat_id)


def _dispatch_development_automation(
    automation: dict[str, Any],
    *,
    manual: bool,
    schedule_metadata: dict[str, Any] | None,
    emit_event: Callable[..., int | None],
    execute: bool = True,
) -> dict[str, Any]:
    """Run a linked automation through Development, never the chat/TTS agent."""
    from distr.core.chat import ChatService
    from distr.core.db import Chat
    from distr.core.workflow.development_threads import (
        development_thread_metadata,
        workflow_id_for_development_chat,
    )
    from distr.core.workflow.work_dispatch import dispatch_work_item

    chat_id = _development_chat_id(automation)
    instruction = str(automation.get("instruction") or "").strip()
    external_attachment = _untrusted_channel_attachment(automation)
    if not chat_id or not instruction:
        return {
            "status": "failed",
            "summary": "Development automation is missing its thread or instruction.",
            "workflow_run_id": None,
            "automation_run_id": None,
            "event_ids": [],
            "chat_id": chat_id,
        }

    with get_session() as session:
        chat = session.get(Chat, int(chat_id))
        if chat is None:
            return {
                "status": "failed",
                "summary": "The linked Development thread no longer exists.",
                "workflow_run_id": None,
                "automation_run_id": None,
                "event_ids": [],
                "chat_id": chat_id,
            }
        workflow_id = workflow_id_for_development_chat(chat)
        metadata = development_thread_metadata(chat)
        project_id = chat.project_id
    config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
    complexity = str(config.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    route = {
        "backend": str(config.get("backend") or "").strip().lower(),
        "model_provider": str(config.get("model_provider") or "").strip().lower(),
        "model": str(config.get("model") or "").strip(),
        "fallback_backend": str(config.get("fallback_backend") or "").strip().lower(),
        "fallback_model_provider": str(config.get("fallback_model_provider") or "").strip().lower(),
        "fallback_model": str(config.get("fallback_model") or "").strip(),
    }
    route = {key: value for key, value in route.items() if value}
    routing_assessment = {
        "complexity": complexity,
        "operational_state": "external_untrusted" if external_attachment else "neutral",
        "route": route,
        "source": "automation_import_configuration",
        "adaptive_model_routing": bool(config.get("adaptive_model_routing", True)),
    }
    event_ids: list[int] = []
    started_event_id = emit_event(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Development automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "development_chat_id": chat_id,
            "development_workflow_id": workflow_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    if not execute:
        run = {"status": "running", "id": None}
    elif workflow_id is not None and external_attachment is None:
        ChatService.add_user_message(int(chat_id), instruction)
        run = dispatch_work_item(
            workflow_id=int(workflow_id),
            chat_id=int(chat_id),
            context=instruction,
            project_id=int(project_id) if project_id is not None else None,
            ticket_id=int(metadata.get("ticket_id")) if metadata.get("ticket_id") else None,
            source_type="automation",
            source_ref=str(automation.get("id") or "").strip() or None,
            run_metadata={
                "project_id": project_id,
                "automation_id": automation.get("id"),
                "automation": True,
                "routing_assessment": routing_assessment,
            },
            dispatch_async=True,
            _session_provider=get_session,
        )
    else:
        from distr.core.workflow.development_control import resume_thread_time
        from distr.core.workflow.development_harness import dispatch_development_prompt

        ChatService.add_user_message(int(chat_id), instruction)
        resume_thread_time(int(chat_id))
        run = dispatch_development_prompt(
            int(chat_id),
            instruction,
            routing_assessment=routing_assessment,
            attachments=[external_attachment] if external_attachment else None,
            autonomy_override="plan" if external_attachment else None,
            dispatch_async=True,
        )
    if isinstance(run, dict) and run.get("error"):
        status = "failed"
        summary = str(run.get("error"))
        development_run_id = None
    else:
        raw_status = str((run or {}).get("status") or "running") if isinstance(run, dict) else "running"
        status = "running" if raw_status in {"initializing", "queued", "dispatched"} else raw_status
        summary = "Development automation dispatched to its linked thread."
        development_run_id = (
            (run or {}).get("run_id") or (run or {}).get("id")
            if isinstance(run, dict)
            else None
        )

    automation_run_id = _record_dispatch_run(
        automation=automation,
        status=status,
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=instruction,
        chat_id=chat_id,
        schedule_metadata={
            **(schedule_metadata or {}),
            "development_workflow_id": workflow_id,
            "development_run_id": development_run_id,
        },
        execution_mode="development_harness",
    )
    dispatched_event_id = emit_event(
        automation=automation,
        event_type="worker_dispatched" if status != "failed" else "worker_failed",
        status=status,
        summary=summary,
        payload={
            "development_chat_id": chat_id,
            "development_workflow_id": workflow_id,
            "development_run_id": development_run_id,
            "automation_run_id": automation_run_id,
            "manual": bool(manual),
        },
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)
    return {
        "status": status,
        "summary": summary,
        "workflow_run_id": development_run_id,
        "automation_run_id": automation_run_id,
        "event_ids": event_ids,
        "chat_id": chat_id,
        "development_workflow_id": workflow_id,
    }


def _automation_already_running(automation: dict[str, Any]) -> bool:
    from distr.core.automation_subagent import _workflow_id_from_automation, workflow_run_in_progress

    workflow_id = _workflow_id_from_automation(automation)
    return workflow_id is not None and workflow_run_in_progress(workflow_id)


def _skip_duplicate_running_response(
    automation: dict[str, Any],
    *,
    manual: bool,
    chat_id: int | None,
    schedule_metadata: dict[str, Any] | None,
    execution_mode: str,
    emit_event: Callable[..., int | None],
) -> dict[str, Any]:
    summary = "Automation is already running."
    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="skipped",
        summary=summary,
        event_ids=[],
        manual=manual,
        prompt="",
        chat_id=chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode=execution_mode,
    )
    emit_event(
        automation=automation,
        event_type="worker_skipped",
        status="skipped",
        summary=summary,
        payload={"skip_reason": "already_running", "workflow_run_id": workflow_run_id, "manual": manual},
    )
    return {
        "status": "skipped",
        "summary": summary,
        "workflow_run_id": workflow_run_id,
        "event_ids": [],
        "chat_id": chat_id,
        "skip_reason": "already_running",
    }


def _dispatch_tool_bound_automation(
    automation: dict[str, Any],
    *,
    manual: bool,
    schedule_metadata: dict[str, Any] | None,
    chat_id: int | None,
    speak: bool,
    emit_event: Callable[..., int | None],
) -> dict[str, Any]:
    from distr.core.automation_subagent import start_automation_subagent
    from distr.core.engagement_gates import proactive_delivery_blocked

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
        workflow_run_id = _record_dispatch_run(
            automation=automation,
            status="skipped",
            summary=summary,
            event_ids=[],
            manual=manual,
            prompt="",
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_tool",
        )
        emit_event(
            automation=automation,
            event_type="worker_skipped",
            status="skipped",
            summary=summary,
            payload={"skip_reason": reason, "workflow_run_id": workflow_run_id, "manual": manual},
        )
        return {
            "status": "skipped",
            "summary": summary,
            "workflow_run_id": workflow_run_id,
            "event_ids": [],
            "chat_id": chat_id,
            "skip_reason": reason,
        }

    if _automation_already_running(automation):
        return _skip_duplicate_running_response(
            automation,
            manual=manual,
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_tool",
            emit_event=emit_event,
        )

    event_ids: list[int] = []
    started_event_id = emit_event(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "tool": _bound_tool_name(automation),
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    # Tool runs have their own run ledger and do not borrow the current Chat.
    target_chat_id = chat_id
    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="running",
        summary="Automation subagent started.",
        event_ids=event_ids,
        manual=manual,
        prompt="",
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode="automation_subagent_tool",
    )

    dispatched_event_id = emit_event(
        automation=automation,
        event_type="worker_dispatched",
        status="running",
        summary="Automation subagent started.",
        payload={
            "workflow_run_id": workflow_run_id,
            "chat_id": target_chat_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)

    if workflow_run_id:
        start_automation_subagent(
            automation=automation,
            run_id=int(workflow_run_id),
            manual=manual,
            chat_id=target_chat_id,
            speak=speak,
            schedule_metadata=schedule_metadata,
            emit_event=emit_event,
        )

    return {
        "status": "running",
        "summary": "Automation subagent started.",
        "workflow_run_id": workflow_run_id,
        "event_ids": event_ids,
        "chat_id": target_chat_id,
    }


def dispatch_automation_to_current_chat(
    automation: dict[str, Any],
    *,
    manual: bool = True,
    schedule_metadata: dict[str, Any] | None = None,
    chat_id: int | None = None,
    speak: bool = False,
    emit_signal: bool = True,
    emit_event: Callable[..., int | None] | None = None,
) -> dict[str, Any]:
    instruction = str(automation.get("instruction") or "").strip()
    tool_name = _bound_tool_name(automation)
    if not instruction and not tool_name:
        return {
            "status": "failed",
            "summary": "Automation has no instruction or tool to run.",
            "workflow_run_id": None,
            "event_ids": [],
        }

    event_fn = emit_event or emit_automation_event
    invocation = automation.get("_invocation_context")
    has_untrusted_external_input = bool(
        isinstance(invocation, dict) and invocation.get("untrusted_message_text")
    )
    if automation.get("record_id"):
        try:
            chat_id = ensure_automation_thread(automation)
            automation = {**automation, "thread_chat_id": chat_id}
            from distr.core.workflow.development_control import resume_thread_time

            resume_thread_time(int(chat_id))
        except Exception as exc:
            return {
                "status": "failed",
                "summary": str(exc) or "Automation thread could not be prepared.",
                "workflow_run_id": None,
                "automation_run_id": None,
                "event_ids": [],
            }
    if _development_chat_id(automation) and (
        has_untrusted_external_input
        or not tool_name
        or automation.get("linked_board_id") is not None
        or automation.get("linked_project_id") is not None
        or automation.get("optional_workflow_id") is not None
    ):
        return _dispatch_development_automation(
            automation,
            manual=manual,
            schedule_metadata=schedule_metadata,
            emit_event=event_fn,
            execute=emit_signal,
        )
    if tool_name:
        return _dispatch_tool_bound_automation(
            automation,
            manual=manual,
            schedule_metadata=schedule_metadata,
            chat_id=chat_id,
            speak=speak,
            emit_event=event_fn,
        )

    if _automation_already_running(automation):
        return _skip_duplicate_running_response(
            automation,
            manual=manual,
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_instruction",
            emit_event=event_fn,
        )

    event_ids: list[int] = []
    started_event_id = event_fn(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "instruction": instruction,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    prompt = augment_bulk_instruction(automation_prompt(automation), source="automation")
    # Instruction automations must own an execution thread. Falling back to the
    # current conversational Chat contaminates both its transcript and TTS.
    target_chat_id = _development_chat_id(automation) or chat_id
    status = "running"
    summary = "Automation subagent started."

    if not target_chat_id:
        status = "failed"
        summary = "Automation could not create an independent run thread."
        workflow_run_id = _record_dispatch_run(
            automation=automation,
            status=status,
            summary=summary,
            event_ids=event_ids,
            manual=manual,
            prompt=prompt,
            chat_id=target_chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_instruction",
        )
        return {
            "status": status,
            "summary": summary,
            "workflow_run_id": workflow_run_id,
            "event_ids": event_ids,
            "chat_id": target_chat_id,
        }

    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="running",
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=prompt,
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode="automation_subagent_instruction",
    )

    dispatched_event_id = event_fn(
        automation=automation,
        event_type="worker_dispatched",
        status="running",
        summary=summary,
        payload={
            "instruction": instruction,
            "workflow_run_id": workflow_run_id,
            "chat_id": target_chat_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)

    if workflow_run_id and emit_signal:
        from distr.core.automation_subagent import start_automation_subagent

        start_automation_subagent(
            automation=automation,
            run_id=int(workflow_run_id),
            manual=manual,
            chat_id=target_chat_id,
            speak=speak,
            schedule_metadata=schedule_metadata,
            emit_event=event_fn,
        )

    if workflow_run_id:
        try:
            from distr.core.db.automation import AutomationRun

            with get_session() as session:
                run = session.query(AutomationRun).filter(AutomationRun.id == workflow_run_id).first()
                if run:
                    data = _json_config(run.run_data)
                    data["orchestration_event_ids"] = event_ids
                    run.run_data = json.dumps(data, ensure_ascii=False, default=str)
                    session.commit()
                else:
                    run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == workflow_run_id).first()
                    if run:
                        data = _json_config(run.run_data)
                        data["orchestration_event_ids"] = event_ids
                        run.run_data = json.dumps(data, ensure_ascii=False, default=str)
                        session.commit()
        except Exception:
            logger.debug("Automation run event id update failed", exc_info=True)

    return {
        "status": status,
        "summary": summary,
        "workflow_run_id": workflow_run_id,
        "event_ids": event_ids,
        "chat_id": target_chat_id,
    }
