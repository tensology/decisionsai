"""Automation CRUD routes backed by the AutoWorkflow scheduler."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.core.automation_orchestrator import dispatch_automation_to_current_chat
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep


AUTOMATION_SURFACE = "automation"


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


def _utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _notify_automation_data_changed() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


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


def _automation_id(workflow_id: int | str) -> str:
    return f"wf_{int(workflow_id)}"


def _workflow_id(automation_id: str | int) -> int:
    raw = str(automation_id or "").strip()
    if raw.startswith("wf_"):
        raw = raw[3:]
    if not raw.isdigit():
        raise HTTPException(404, "Automation not found")
    return int(raw)


def _automation_marker(
    schedule: dict[str, Any] | None = None,
    automation_type: str = "scheduled_instruction",
    *,
    preset_id: str = "",
    action_config: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "decisions_surface": AUTOMATION_SURFACE,
            "automation_type": automation_type or "scheduled_instruction",
            "schedule": schedule or {"kind": "daily", "time": "09:00"},
            "preset_id": str(preset_id or "").strip(),
            "action_config": action_config if isinstance(action_config, dict) else {},
        },
        ensure_ascii=False,
        default=str,
    )


def _workflow_schedule(schedule: dict[str, Any] | None, *, strict: bool = False) -> dict[str, Any]:
    from distr.core.workflow.scheduler import normalize_schedule_time

    schedule = schedule if isinstance(schedule, dict) else {}
    kind = str(schedule.get("kind") or schedule.get("frequency") or "daily").strip().lower()
    if kind in {"15m", "15min"}:
        kind = "interval"
        schedule = {
            **schedule,
            "kind": "interval",
            "interval": schedule.get("interval") or 15,
            "interval_unit": schedule.get("interval_unit") or "minutes",
        }
    if kind in {"30m", "30min"}:
        kind = "interval"
        schedule = {
            **schedule,
            "kind": "interval",
            "interval": schedule.get("interval") or 30,
            "interval_unit": schedule.get("interval_unit") or "minutes",
        }
    if kind not in {"once", "interval", "15min", "30min", "hourly", "daily", "weekly", "monthly"}:
        kind = "daily"
    time_value = str(schedule.get("time") or "09:00")
    if kind in {"daily", "weekly", "monthly"}:
        try:
            time_value = normalize_schedule_time(time_value, default="09:00")
        except ValueError as exc:
            if strict:
                raise HTTPException(422, str(exc)) from exc
            time_value = "09:00"
    run_at = str(schedule.get("run_at") or "").strip()
    if kind == "once" and strict:
        if not run_at:
            raise HTTPException(422, "Run-at time is required for one-time automations")
        try:
            from distr.core.workflow.scheduler import normalize_once_run_at_storage, parse_once_run_at_as_utc

            parsed = parse_once_run_at_as_utc(run_at)
            if not parsed:
                raise ValueError(f"Invalid run-at time: {run_at!r}")
            run_at = normalize_once_run_at_storage(run_at)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    interval_value = 15
    interval_unit = "minutes"
    if kind == "interval":
        try:
            interval_value = max(1, int(schedule.get("interval") or schedule.get("interval_value") or 15))
        except (TypeError, ValueError):
            if strict:
                raise HTTPException(422, "Interval must be a positive number") from None
            interval_value = 15
        interval_unit = str(schedule.get("interval_unit") or "minutes").strip().lower()
        if interval_unit.startswith("sec"):
            interval_unit = "seconds"
        else:
            interval_unit = "minutes"
        if strict and interval_unit == "seconds" and interval_value > 86400:
            raise HTTPException(422, "Interval cannot exceed 86400 seconds")
        if strict and interval_unit == "minutes" and interval_value > 1440:
            raise HTTPException(422, "Interval cannot exceed 1440 minutes")
    return {
        "kind": kind,
        "time": time_value,
        "run_at": run_at,
        "interval": interval_value,
        "interval_unit": interval_unit,
        "days": str(schedule.get("days") or schedule.get("schedule_days") or "1"),
        "timezone": str(schedule.get("timezone") or ""),
    }


def _compute_next_run(schedule: dict[str, Any]) -> datetime | None:
    try:
        from distr.core.workflow.scheduler import _next_run_from_cron, next_run_for_interval, schedule_to_cron

        if schedule.get("kind") == "interval":
            return next_run_for_interval(
                int(schedule.get("interval") or 15),
                str(schedule.get("interval_unit") or "minutes"),
                _utcnow(),
                allow_current_window=True,
            )
        schedule_time = schedule.get("time")
        if schedule.get("kind") == "once":
            schedule_time = schedule.get("run_at")
        elif schedule.get("kind") == "interval":
            schedule_time = f"{schedule.get('interval')}:{schedule.get('interval_unit')}"
        cron = schedule_to_cron(
            schedule.get("kind"),
            schedule_time,
            schedule.get("timezone"),
            schedule.get("days"),
        )
        return _next_run_from_cron(cron or "", _utcnow(), schedule.get("timezone"), allow_current_minute=True)
    except Exception:
        return None


def _apply_schedule(workflow: AutoWorkflow, schedule: dict[str, Any]) -> None:
    schedule = _workflow_schedule(schedule, strict=True)
    workflow.schedule_enabled = workflow.status == "active"
    workflow.schedule_preset = schedule["kind"]
    if schedule["kind"] == "once":
        workflow.schedule_time = schedule.get("run_at")
    elif schedule["kind"] == "interval":
        workflow.schedule_time = f"{schedule.get('interval')}:{schedule.get('interval_unit')}"
    else:
        workflow.schedule_time = schedule.get("time")
    workflow.schedule_days = schedule.get("days") or "1"
    workflow.schedule_timezone = schedule.get("timezone") or None
    workflow.schedule_cron = None
    workflow.next_run_at = _compute_next_run(schedule) if workflow.schedule_enabled else None
    if workflow.schedule_enabled and workflow.next_run_at is None:
        raise HTTPException(422, "Schedule could not produce a next run time")


def _first_instruction_step(workflow: AutoWorkflow) -> AutoWorkflowStep | None:
    steps = sorted(list(workflow.steps or []), key=lambda s: s.position or 0)
    return steps[0] if steps else None


def _serialize_automation(workflow: AutoWorkflow) -> dict[str, Any]:
    from distr.core.workflow.scheduler import once_run_at_for_datetime_local_input

    marker = _json_config(workflow.context_rules)
    schedule = _workflow_schedule(marker.get("schedule") if isinstance(marker.get("schedule"), dict) else {})
    step = _first_instruction_step(workflow)
    run_at_display = schedule.get("run_at") or ""
    if schedule.get("kind") == "once" and run_at_display:
        run_at_display = once_run_at_for_datetime_local_input(run_at_display)
    action_config = marker.get("action_config") if isinstance(marker.get("action_config"), dict) else {}
    return {
        "id": _automation_id(workflow.id),
        "workflow_id": workflow.id,
        "step_id": step.id if step else None,
        "name": workflow.name or "Untitled Automation",
        "automation_type": marker.get("automation_type") or "scheduled_instruction",
        "preset_id": str(marker.get("preset_id") or "").strip(),
        "action_config": dict(action_config),
        "status": workflow.status or "active",
        "instruction": (step.instruction if step else "") or "",
        "schedule": {
            "kind": schedule["kind"],
            "time": schedule.get("time") or "09:00",
            "run_at": run_at_display,
            "interval": schedule.get("interval") or 15,
            "interval_unit": schedule.get("interval_unit") or "minutes",
            "days": schedule.get("days") or "1",
            "timezone": schedule.get("timezone") or "",
        },
        "created_at": _iso(workflow.created_date),
        "updated_at": _iso(workflow.modified_date),
        "last_run_at": _iso(workflow.last_run_at),
        "next_run_at": _iso(workflow.next_run_at),
        "run_health": "healthy",
        "health": "healthy",
    }


def _is_automation_workflow(workflow: AutoWorkflow) -> bool:
    marker = _json_config(workflow.context_rules)
    return (
        workflow.workflow_type == "scheduled"
        and str(marker.get("decisions_surface") or "").strip().lower() == AUTOMATION_SURFACE
    )


def _automation_prompt(automation: Dict[str, Any]) -> str:
    instruction = str(automation.get("instruction") or "").strip()
    return (
        "This is a DecisionsAI automation run. Treat the automation instruction "
        "as the user's requested outcome, decide the natural next orchestration "
        "step, and respond organically. If sub-agents or tools are needed, route "
        "the work through the orchestrator instead of pretending it is complete.\n\n"
        f"Automation: {automation.get('name') or 'Untitled Automation'}\n"
        f"Instruction:\n{instruction}"
    )


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
                "is_workflow_attached": True,
                **(payload or {}),
            },
        )
    except Exception:
        return None


def _get_automation_workflow(db, automation_id: str) -> AutoWorkflow:
    workflow_id = _workflow_id(automation_id)
    workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
    if not workflow or not _is_automation_workflow(workflow):
        raise HTTPException(404, "Automation not found")
    return workflow


def _serialize_run(run: AutoWorkflowRun, automation_id: str) -> dict[str, Any]:
    data = _json_config(run.run_data)
    return {
        "id": f"run_{run.id}",
        "workflow_run_id": run.id,
        "workflow_id": run.workflow_id,
        "automation_id": automation_id,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "status": run.status,
        "summary": (
            data.get("message")
            or data.get("summary")
            or (data.get("result_packet") or {}).get("summary")
            or "Automation run recorded."
        ),
        "orchestration_event_ids": data.get("orchestration_event_ids") or [],
        "retry_count": int(data.get("retry_count") or 0),
        "manual": bool(data.get("manual")),
    }


def _dispatch_to_orchestrator(automation: Dict[str, Any]) -> Dict[str, Any]:
    return dispatch_automation_to_current_chat(
        automation,
        manual=True,
        emit_event=_emit_automation_event,
    )


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/automations/presets")
    async def list_automation_presets():
        from distr.core.automation_presets import list_automation_presets as _list_presets

        return JSONResponse({"presets": _list_presets()})

    @router.get("/automations")
    async def list_automations():
        with get_session() as db:
            rows = (
                db.query(AutoWorkflow)
                .filter(AutoWorkflow.workflow_type == "scheduled")
                .order_by(AutoWorkflow.modified_date.desc())
                .all()
            )
            automations = [_serialize_automation(row) for row in rows if _is_automation_workflow(row)]
        return JSONResponse({"automations": automations})

    @router.post("/automations")
    async def create_automation(payload: AutomationPayload):
        from distr.core.automation_presets import get_automation_preset

        schedule = _workflow_schedule(payload.schedule, strict=True)
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
                schedule = _workflow_schedule(preset.get("schedule") or schedule, strict=True)
        now = _utcnow()
        with get_session() as db:
            workflow = AutoWorkflow(
                name=payload.name or (preset.get("name") if preset else None) or "New Automation",
                description="Itemized DecisionsAI automation.",
                status=payload.status or "active",
                workflow_type="scheduled",
                context_rules=_automation_marker(
                    schedule,
                    automation_type,
                    preset_id=preset_id,
                    action_config=action_config,
                ),
                created_date=now,
                modified_date=now,
            )
            _apply_schedule(workflow, schedule)
            db.add(workflow)
            db.flush()
            step = AutoWorkflowStep(
                workflow_id=workflow.id,
                position=0,
                name="Automation Instruction",
                action_type="agent_instruction",
                step_type="agent_instruction",
                instruction=payload.instruction or instruction or "",
                config=json.dumps({"source": "automation"}, ensure_ascii=False),
                timeout_seconds=300,
            )
            db.add(step)
            db.commit()
            db.refresh(workflow)
            _notify_automation_data_changed()
            return JSONResponse({"success": True, "automation": _serialize_automation(workflow)})

    @router.get("/automations/due")
    async def list_due_automations():
        now = _utcnow()
        with get_session() as db:
            rows = (
                db.query(AutoWorkflow)
                .filter(
                    AutoWorkflow.workflow_type == "scheduled",
                    AutoWorkflow.schedule_enabled == True,  # noqa: E712
                    AutoWorkflow.next_run_at.isnot(None),
                    AutoWorkflow.next_run_at <= now,
                )
                .all()
            )
            automations = [_serialize_automation(row) for row in rows if _is_automation_workflow(row)]
        return JSONResponse({"automations": automations})

    @router.get("/automations/{automation_id}")
    async def get_automation(automation_id: str):
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            automation = _serialize_automation(workflow)
            runs = [
                _serialize_run(row, automation["id"])
                for row in (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.workflow_id == workflow.id)
                    .order_by(AutoWorkflowRun.started_at.desc())
                    .limit(50)
                    .all()
                )
            ]
        return JSONResponse({"automation": automation, "runs": runs})

    @router.put("/automations/{automation_id}")
    async def update_automation(automation_id: str, payload: AutomationUpdate):
        data = payload.model_dump(exclude_unset=True)
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            if "name" in data:
                workflow.name = data["name"] or workflow.name
            if "status" in data:
                workflow.status = data["status"] or workflow.status
            if "instruction" in data:
                step = _first_instruction_step(workflow)
                if step:
                    step.instruction = data["instruction"] or ""
            schedule = _workflow_schedule(
                data.get("schedule") or _json_config(workflow.context_rules).get("schedule") or {},
                strict="schedule" in data,
            )
            if "schedule" in data or "status" in data:
                _apply_schedule(workflow, schedule)
            marker = _json_config(workflow.context_rules)
            preset_id = str(data.get("preset_id") if "preset_id" in data else marker.get("preset_id") or "").strip()
            action_config = data.get("action_config") if "action_config" in data else marker.get("action_config")
            if not isinstance(action_config, dict):
                action_config = {}
            workflow.context_rules = _automation_marker(
                schedule,
                marker.get("automation_type") or "scheduled_instruction",
                preset_id=preset_id,
                action_config=action_config,
            )
            workflow.modified_date = _utcnow()
            db.commit()
            db.refresh(workflow)
            _notify_automation_data_changed()
            return JSONResponse({"success": True, "automation": _serialize_automation(workflow)})

    @router.post("/automations/{automation_id}/pause")
    async def pause_automation(automation_id: str):
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            workflow.status = "paused"
            _apply_schedule(workflow, _json_config(workflow.context_rules).get("schedule") or {})
            workflow.modified_date = _utcnow()
            db.commit()
            db.refresh(workflow)
            _notify_automation_data_changed()
            return JSONResponse({"success": True, "automation": _serialize_automation(workflow)})

    @router.post("/automations/{automation_id}/resume")
    async def resume_automation(automation_id: str):
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            workflow.status = "active"
            _apply_schedule(workflow, _json_config(workflow.context_rules).get("schedule") or {})
            workflow.modified_date = _utcnow()
            db.commit()
            db.refresh(workflow)
            _notify_automation_data_changed()
            return JSONResponse({"success": True, "automation": _serialize_automation(workflow)})

    @router.delete("/automations/{automation_id}")
    async def delete_automation(automation_id: str):
        from distr.core.db.workflow import AutoWorkflowStepResult

        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            step_ids = [step.id for step in list(workflow.steps or []) if step.id is not None]
            run_ids = [run.id for run in list(workflow.runs or []) if run.id is not None]
            if step_ids:
                db.query(AutoWorkflowStepResult).filter(
                    AutoWorkflowStepResult.step_id.in_(step_ids)
                ).delete(synchronize_session=False)
            if run_ids:
                db.query(AutoWorkflowStepResult).filter(
                    AutoWorkflowStepResult.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
            db.delete(workflow)
            db.commit()
        _notify_automation_data_changed()
        return JSONResponse({"success": True})

    @router.post("/automations/{automation_id}/run")
    async def run_automation(automation_id: str):
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            automation = _serialize_automation(workflow)
        dispatch = _dispatch_to_orchestrator(automation)
        run = {
            "id": f"run_{dispatch.get('workflow_run_id') or 'dispatch'}",
            "workflow_run_id": dispatch.get("workflow_run_id"),
            "workflow_id": automation["workflow_id"],
            "automation_id": automation["id"],
            "started_at": _iso(_utcnow()),
            "completed_at": None,
            "status": dispatch["status"],
            "summary": dispatch["summary"],
            "orchestration_event_ids": dispatch.get("event_ids") or [],
            "retry_count": 0,
            "manual": True,
        }
        return JSONResponse({"success": True, "run": run, "automation": automation})

    @router.get("/automations/{automation_id}/runs")
    async def list_automation_runs(automation_id: str):
        with get_session() as db:
            workflow = _get_automation_workflow(db, automation_id)
            public_id = _automation_id(workflow.id)
            rows = (
                db.query(AutoWorkflowRun)
                .filter(AutoWorkflowRun.workflow_id == workflow.id)
                .order_by(AutoWorkflowRun.started_at.desc())
                .limit(50)
                .all()
            )
            runs = [_serialize_run(row, public_id) for row in rows]
        return JSONResponse({"runs": runs})

    return router
