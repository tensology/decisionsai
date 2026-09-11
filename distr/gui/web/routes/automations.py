"""Automation CRUD routes — first-class automations table, not workflow rows."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.core.automation.store import (
    AutomationStoreError,
    create_automation,
    delete_all_automations,
    delete_automation,
    get_automation,
    list_automation_runs,
    list_automations,
    list_due_automations,
    normalize_schedule,
    notify_automation_data_changed,
    update_automation,
    utc_now,
)
from distr.core.automation_orchestrator import dispatch_automation_to_current_chat


class AutomationPayload(BaseModel):
    name: str = Field(default="New Automation")
    automation_type: str = Field(default="scheduled_instruction")
    status: Optional[str] = None
    instruction: str = Field(default="")
    preset_id: str = Field(default="")
    schedule: Dict[str, Any] = Field(default_factory=lambda: {"kind": "daily", "time": "09:00"})
    source_config: Dict[str, Any] = Field(default_factory=dict)
    linked_project_id: Optional[int] = None
    linked_board_id: Optional[int] = None
    optional_workflow_id: Optional[int] = None
    optional_snippet_id: Optional[int] = None
    action_config: Dict[str, Any] = Field(default_factory=dict)
    approval_policy: Dict[str, Any] = Field(default_factory=dict)
    notification_policy: Dict[str, Any] = Field(default_factory=dict)
    ticket_creation: Dict[str, Any] = Field(default_factory=dict)
    validation: Dict[str, Any] = Field(default_factory=dict)


class AutomationUpdate(BaseModel):
    name: Optional[str] = None
    automation_type: Optional[str] = None
    status: Optional[str] = None
    instruction: Optional[str] = None
    schedule: Optional[Dict[str, Any]] = None
    preset_id: Optional[str] = None
    action_config: Optional[Dict[str, Any]] = None
    source_config: Optional[Dict[str, Any]] = None
    linked_project_id: Optional[int] = None
    linked_board_id: Optional[int] = None
    optional_workflow_id: Optional[int] = None
    optional_snippet_id: Optional[int] = None
    approval_policy: Optional[Dict[str, Any]] = None
    notification_policy: Optional[Dict[str, Any]] = None
    ticket_creation: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None


class AutomationDraftRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    current_thread_title: Optional[str] = Field(default=None, max_length=500)


class AutomationImportRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["codex", "cursor", "claude"])
    routing: Dict[str, Any] = Field(default_factory=dict)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def _store_error(exc: AutomationStoreError) -> HTTPException:
    message = str(exc) or "Automation request failed"
    status = 404 if "not found" in message.lower() else 422
    return HTTPException(status, message)


def _emit_automation_event(
    *,
    automation: Dict[str, Any],
    event_type: str,
    status: str,
    summary: str,
    payload: Dict[str, Any] | None = None,
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
                "surface": "automation",
                "subtype": event_type,
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "automation_type": automation.get("automation_type"),
                "is_workflow_attached": bool(automation.get("is_workflow_attached")),
                **(payload or {}),
            },
        )
    except Exception:
        return None


def _dispatch_to_orchestrator(automation: Dict[str, Any]) -> Dict[str, Any]:
    return dispatch_automation_to_current_chat(
        automation,
        manual=True,
        emit_event=_emit_automation_event,
    )


def _require_automation(automation_id: str) -> Dict[str, Any]:
    automation = get_automation(automation_id)
    if not automation:
        raise HTTPException(404, "Automation not found")
    return automation


def _schedule_time_from_text(text: str) -> str:
    matches = re.finditer(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    for match in matches:
        raw = match.group(0).strip().lower()
        suffix = (match.group(3) or "").lower()
        if ":" not in raw and not suffix and not raw.startswith("at "):
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return "09:00"


def _fallback_automation_draft(instruction: str) -> Dict[str, Any]:
    text = " ".join(str(instruction or "").split()).strip()
    lowered = text.lower()
    interval = re.search(r"\bevery\s+(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", lowered)
    one_time_day = re.search(r"\b(today|tomorrow)\b", lowered)
    explicit_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", lowered)
    if one_time_day or explicit_date:
        if explicit_date:
            run_date = datetime.strptime(explicit_date.group(1), "%Y-%m-%d").date()
        else:
            run_date = (datetime.now() + timedelta(days=1 if one_time_day.group(1) == "tomorrow" else 0)).date()
        run_time = _schedule_time_from_text(text)
        schedule = {"kind": "once", "run_at": f"{run_date.isoformat()}T{run_time}"}
    elif interval:
        value = int(interval.group(1))
        unit_text = interval.group(2)
        if unit_text.startswith(("hour", "hr")):
            schedule = {"kind": "interval", "interval": min(value * 60, 1440), "interval_unit": "minutes"}
        else:
            schedule = {
                "kind": "interval",
                "interval": min(value, 86400 if unit_text.startswith("sec") else 1440),
                "interval_unit": "seconds" if unit_text.startswith("sec") else "minutes",
            }
    elif "hourly" in lowered or "every hour" in lowered:
        schedule = {"kind": "hourly"}
    elif "weekday" in lowered or "monday to friday" in lowered:
        schedule = {"kind": "weekly", "time": _schedule_time_from_text(text), "days": "1,2,3,4,5"}
    elif "weekly" in lowered or "every week" in lowered or re.search(
        r"\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
    ):
        day_map = {
            "monday": "1", "tuesday": "2", "wednesday": "3", "thursday": "4",
            "friday": "5", "saturday": "6", "sunday": "0",
        }
        day = next((number for label, number in day_map.items() if label in lowered), "1")
        schedule = {"kind": "weekly", "time": _schedule_time_from_text(text), "days": day}
    elif "monthly" in lowered or "every month" in lowered:
        schedule = {"kind": "monthly", "time": _schedule_time_from_text(text), "days": "1"}
    else:
        schedule = {"kind": "daily", "time": _schedule_time_from_text(text)}
    name_source = re.split(r"\b(?:every|daily|hourly|weekly|monthly|on weekdays|at \d)\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    name = (name_source or text or "Scheduled action").strip(" .,:;-")[:80]
    if name:
        name = name[0].upper() + name[1:]
    source = next((value for value in ("whatsapp", "telegram", "gmail") if value in lowered), "")
    event_triggered = bool(source and re.search(r"\b(?:when|whenever|incoming|arrives?|received?|new message|new email)\b", lowered))
    return {
        "name": name or "Scheduled action",
        "instruction": text,
        "schedule": normalize_schedule(schedule, strict=True),
        "automation_type": "channel_intake" if event_triggered else "scheduled_instruction",
        "source_config": {"source": source, "trigger": "incoming_message"} if event_triggered else {},
        "link_current_thread": bool(re.search(r"\b(this|current)\s+(task|thread|chat)\b", lowered)),
        "drafted_by": "fallback",
    }


def _draft_automation(instruction: str, current_thread_title: str | None = None) -> Dict[str, Any]:
    fallback = _fallback_automation_draft(instruction)
    try:
        from distr.core.settings import load_settings_from_db
        from distr.core.workflow.planning import _planning_model_tiers, _strip_json_fence, call_planning_llm

        settings = load_settings_from_db()
        prompt = f"""Convert the user's request into one agent automation.
Return JSON only with these keys:
- name: concise action name, maximum 80 characters
- instruction: complete executable instruction, without losing operational detail
- schedule: one DecisionsAI schedule object. Supported kinds are once, hourly, daily, weekly, monthly, or interval. Use HH:MM 24-hour time. Once uses run_at as a local ISO date and time. Weekdays use kind weekly and days \"1,2,3,4,5\". Interval uses interval and interval_unit seconds or minutes. Preserve an explicitly requested IANA timezone.
- automation_type: channel_intake when an incoming WhatsApp, Telegram, or Gmail message triggers the action; otherwise scheduled_instruction
- source_config: for channel_intake return a source and incoming_message trigger; otherwise return an empty object
- link_current_thread: true only if the user explicitly refers to this task, thread, or chat

Current task title: {current_thread_title or 'none'}
User request: {instruction}
"""
        for _tier, provider, model in _planning_model_tiers(settings)[:3]:
            try:
                raw = call_planning_llm(prompt, provider, model, settings)
                parsed = json.loads(_strip_json_fence(raw))
                if not isinstance(parsed, dict):
                    continue
                name = str(parsed.get("name") or fallback["name"]).strip()[:80]
                executable = str(parsed.get("instruction") or instruction).strip()
                schedule = normalize_schedule(parsed.get("schedule"), strict=True)
                if not name or not executable:
                    continue
                return {
                    "name": name,
                    "instruction": executable,
                    "schedule": schedule,
                    "automation_type": str(parsed.get("automation_type") or fallback["automation_type"]),
                    "source_config": parsed.get("source_config") if isinstance(parsed.get("source_config"), dict) else fallback["source_config"],
                    "link_current_thread": bool(parsed.get("link_current_thread")),
                    "drafted_by": "ai",
                }
            except Exception:
                continue
    except Exception:
        pass
    return fallback


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/automations/presets")
    async def list_automation_presets():
        from distr.core.automation_presets import list_automation_presets as _list_presets

        return JSONResponse({"presets": _list_presets()})

    @router.get("/automations")
    async def list_automations_route():
        return JSONResponse({"automations": list_automations()})

    @router.delete("/automations")
    async def delete_all_automations_route(confirm: bool = False):
        if not confirm:
            raise HTTPException(400, "Bulk automation deletion requires confirm=true")
        result = await asyncio.to_thread(delete_all_automations)
        return JSONResponse({"success": True, **result})

    @router.get("/automations/imports")
    async def automation_import_status_route():
        from distr.core.automation.imports import import_status

        return JSONResponse(await asyncio.to_thread(import_status))

    @router.post("/automations/imports")
    async def import_automations_route(payload: AutomationImportRequest):
        from distr.core.automation.imports import import_automations

        try:
            result = await asyncio.to_thread(
                import_automations,
                payload.sources,
                routing=payload.routing,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        prepared = []
        for automation in [*(result.get("imported") or []), *(result.get("updated") or [])]:
            try:
                from distr.core.automation_orchestrator import ensure_automation_thread

                chat_id = await asyncio.to_thread(ensure_automation_thread, automation)
                prepared.append({"automation_id": automation.get("id"), "chat_id": chat_id})
            except Exception as exc:
                prepared.append({
                    "automation_id": automation.get("id"),
                    "error": str(exc) or "Automation thread could not be prepared.",
                })
        result["prepared_threads"] = prepared
        return JSONResponse(result)

    @router.post("/automations/draft")
    async def draft_automation_route(payload: AutomationDraftRequest):
        instruction = payload.instruction.strip()
        if not instruction:
            raise HTTPException(422, "Describe what the scheduled action should do")
        draft = await asyncio.to_thread(_draft_automation, instruction, payload.current_thread_title)
        return JSONResponse(draft)

    @router.post("/automations")
    async def create_automation_route(payload: AutomationPayload):
        from distr.core.automation_presets import get_automation_preset

        try:
            schedule = normalize_schedule(payload.schedule, strict=True)
            preset_id = str(payload.preset_id or "").strip()
            preset = get_automation_preset(preset_id) if preset_id else None
            automation_type = payload.automation_type or "scheduled_instruction"
            instruction = payload.instruction or ""
            action_config = dict(payload.action_config or {})
            # Thread ownership is assigned by the server after the automation
            # exists. Never let a client claim an unrelated Development chat.
            action_config.pop("development_chat_id", None)
            if payload.source_config:
                action_config["source_config"] = dict(payload.source_config)
            if payload.linked_project_id is not None:
                action_config.setdefault("linked_project_id", int(payload.linked_project_id))
            if payload.optional_workflow_id is not None:
                action_config.setdefault("development_workflow_id", int(payload.optional_workflow_id))
            if payload.linked_board_id is not None:
                action_config.setdefault("linked_board_id", int(payload.linked_board_id))
            if preset:
                automation_type = preset.get("automation_type") or automation_type
                if not instruction:
                    instruction = preset.get("instruction") or ""
                if not action_config:
                    action_config = dict(preset.get("action_config") or {})
                if not payload.schedule or payload.schedule == {"kind": "daily", "time": "09:00"}:
                    schedule = normalize_schedule(preset.get("schedule") or schedule, strict=True)
            automation = create_automation(
                name=payload.name or (preset.get("name") if preset else None) or "New Automation",
                automation_type=automation_type,
                status=payload.status or "active",
                instruction=instruction,
                preset_id=preset_id,
                schedule=schedule,
                action_config=action_config,
                board_id=payload.linked_board_id or action_config.get("linked_board_id"),
                project_id=payload.linked_project_id or action_config.get("linked_project_id"),
                thread_chat_id=None,
                linked_workflow_id=payload.optional_workflow_id or action_config.get("development_workflow_id"),
            )
            from distr.core.automation_orchestrator import ensure_automation_thread

            try:
                ensure_automation_thread(automation)
            except Exception:
                # Creation is one product operation. Do not leave an active,
                # invisible scheduled row behind when thread preparation fails.
                delete_automation(automation["id"])
                raise
            automation = _require_automation(automation["id"])
        except (AutomationStoreError, ValueError) as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"success": True, "automation": automation})

    @router.get("/automations/due")
    async def list_due_automations_route():
        return JSONResponse({"automations": list_due_automations()})

    @router.get("/automations/{automation_id}")
    async def get_automation_route(automation_id: str):
        automation = _require_automation(automation_id)
        try:
            runs = list_automation_runs(automation_id)
        except (AutomationStoreError, ValueError) as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"automation": automation, "runs": runs})

    @router.put("/automations/{automation_id}")
    async def update_automation_route(automation_id: str, payload: AutomationUpdate):
        data = payload.model_dump(exclude_unset=True)
        if not data:
            automation = _require_automation(automation_id)
            return JSONResponse({"success": True, "automation": automation})
        if automation_id.startswith("wf_"):
            raise HTTPException(
                409,
                "This automation is still on the legacy workflow record. Restart the app to migrate it, then edit again.",
            )
        existing = _require_automation(automation_id)
        incoming_action_config = data.get("action_config")
        if isinstance(incoming_action_config, dict):
            action_config = {
                **dict(existing.get("action_config") or {}),
                **incoming_action_config,
            }
        else:
            action_config = dict(existing.get("action_config") or {})
        if isinstance(data.get("source_config"), dict):
            action_config["source_config"] = data.pop("source_config")
        # The persisted first-class relation is authoritative. A PUT cannot
        # rebind an automation to a client-selected chat.
        owned_chat_id = existing.get("thread_chat_id")
        if owned_chat_id:
            action_config["development_chat_id"] = int(owned_chat_id)
        else:
            action_config.pop("development_chat_id", None)
        if isinstance(incoming_action_config, dict) or "source_config" in payload.model_fields_set:
            data["action_config"] = action_config
        # These are first-class ownership fields, not incidental JSON options.
        if "linked_board_id" in data:
            if data.get("linked_board_id") is None:
                data["linked_project_id"] = None
            else:
                from distr.core.db import get_session
                from distr.core.db.kanban import KanbanBoard

                with get_session() as session:
                    board = session.get(KanbanBoard, int(data["linked_board_id"]))
                    if board is None:
                        raise HTTPException(422, "The linked board does not exist")
                    data["linked_project_id"] = board.default_project_id
        try:
            automation = update_automation(automation_id, **data)
            from distr.core.automation_orchestrator import ensure_automation_thread

            ensure_automation_thread(automation)
            automation = _require_automation(automation_id)
        except (AutomationStoreError, ValueError) as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"success": True, "automation": automation})

    @router.post("/automations/{automation_id}/pause")
    async def pause_automation_route(automation_id: str):
        if automation_id.startswith("wf_"):
            raise HTTPException(409, "Restart the app to migrate this automation before pausing.")
        try:
            automation = update_automation(automation_id, status="paused")
        except AutomationStoreError as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"success": True, "automation": automation})

    @router.post("/automations/{automation_id}/resume")
    async def resume_automation_route(automation_id: str):
        if automation_id.startswith("wf_"):
            raise HTTPException(409, "Restart the app to migrate this automation before resuming.")
        try:
            automation = update_automation(automation_id, status="active")
        except AutomationStoreError as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"success": True, "automation": automation})

    @router.delete("/automations/{automation_id}")
    async def delete_automation_route(automation_id: str):
        if automation_id.startswith("wf_"):
            raise HTTPException(409, "Restart the app to migrate this automation before deleting.")
        if not delete_automation(automation_id):
            raise HTTPException(404, "Automation not found")
        return JSONResponse({"success": True})

    @router.post("/automations/{automation_id}/run")
    async def run_automation_route(automation_id: str):
        automation = _require_automation(automation_id)
        dispatch = _dispatch_to_orchestrator(automation)
        run_id = dispatch.get("workflow_run_id") or dispatch.get("automation_run_id")
        run = {
            "id": f"run_{run_id or 'dispatch'}",
            "workflow_run_id": dispatch.get("workflow_run_id"),
            "automation_run_id": dispatch.get("automation_run_id") or run_id,
            "workflow_id": automation.get("workflow_id"),
            "automation_id": automation["id"],
            "started_at": _iso(utc_now()),
            "completed_at": None,
            "status": dispatch["status"],
            "summary": dispatch["summary"],
            "orchestration_event_ids": dispatch.get("event_ids") or [],
            "retry_count": 0,
            "manual": True,
        }
        return JSONResponse({"success": True, "run": run, "automation": automation})

    @router.get("/automations/{automation_id}/runs")
    async def list_automation_runs_route(automation_id: str):
        try:
            runs = list_automation_runs(automation_id)
        except AutomationStoreError as exc:
            raise _store_error(exc) from exc
        return JSONResponse({"runs": runs})

    return router
