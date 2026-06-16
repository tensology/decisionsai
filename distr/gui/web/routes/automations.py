"""Automation CRUD routes — first-class automations table, not workflow rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.core.automation.store import (
    AutomationStoreError,
    create_automation,
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


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/automations/presets")
    async def list_automation_presets():
        from distr.core.automation_presets import list_automation_presets as _list_presets

        return JSONResponse({"presets": _list_presets()})

    @router.get("/automations")
    async def list_automations_route():
        return JSONResponse({"automations": list_automations()})

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
            )
        except AutomationStoreError as exc:
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
        except AutomationStoreError as exc:
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
        try:
            automation = update_automation(automation_id, **data)
        except AutomationStoreError as exc:
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
