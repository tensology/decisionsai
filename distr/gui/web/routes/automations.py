"""Automation CRUD routes backed by the AutoWorkflow scheduler."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.core.agent.services.llm.bulk_instruction import augment_bulk_instruction
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep


AUTOMATION_SURFACE = "automation"


class AutomationPayload(BaseModel):
    name: str = Field(default="New Automation")
    automation_type: str = Field(default="scheduled_instruction")
    status: Optional[str] = None
    instruction: str = Field(default="")
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


def _automation_marker(schedule: dict[str, Any] | None = None, automation_type: str = "scheduled_instruction") -> str:
    return json.dumps(
        {
            "decisions_surface": AUTOMATION_SURFACE,
            "automation_type": automation_type or "scheduled_instruction",
            "schedule": schedule or {"kind": "daily", "time": "09:00"},
        },
        ensure_ascii=False,
        default=str,
    )


def _workflow_schedule(schedule: dict[str, Any] | None, *, strict: bool = False) -> dict[str, Any]:
    from distr.core.workflow.scheduler import normalize_schedule_time

    schedule = schedule if isinstance(schedule, dict) else {}
    kind = str(schedule.get("kind") or schedule.get("frequency") or "daily").strip().lower()
    if kind in {"15m", "15min"}:
        kind = "15min"
    if kind in {"30m", "30min"}:
        kind = "30min"
    if kind not in {"once", "15min", "30min", "hourly", "daily", "weekly"}:
        kind = "daily"
    time_value = str(schedule.get("time") or "09:00")
    if kind in {"daily", "weekly"}:
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
            datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(422, f"Invalid run-at time: {run_at!r}") from exc
    return {
        "kind": kind,
        "time": time_value,
        "run_at": run_at,
        "days": str(schedule.get("days") or schedule.get("schedule_days") or "1"),
        "timezone": str(schedule.get("timezone") or ""),
    }


def _compute_next_run(schedule: dict[str, Any]) -> datetime | None:
    try:
        from distr.core.workflow.scheduler import _next_run_from_cron, schedule_to_cron

        cron = schedule_to_cron(
            schedule.get("kind"),
            schedule.get("run_at") if schedule.get("kind") == "once" else schedule.get("time"),
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
    workflow.schedule_time = schedule.get("run_at") if schedule["kind"] == "once" else schedule.get("time")
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
    marker = _json_config(workflow.context_rules)
    schedule = _workflow_schedule(marker.get("schedule") if isinstance(marker.get("schedule"), dict) else {})
    step = _first_instruction_step(workflow)
    return {
        "id": _automation_id(workflow.id),
        "workflow_id": workflow.id,
        "name": workflow.name or "Untitled Automation",
        "automation_type": marker.get("automation_type") or "scheduled_instruction",
        "status": workflow.status or "active",
        "instruction": (step.instruction if step else "") or "",
        "schedule": {
            "kind": schedule["kind"],
            "time": schedule.get("time") or "09:00",
            "run_at": schedule.get("run_at") or "",
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
    instruction = str(automation.get("instruction") or "").strip()
    if not instruction:
        return {
            "status": "failed",
            "summary": "Automation has no instruction to run.",
            "workflow_run_id": None,
            "event_ids": [],
        }

    event_ids: list[int] = []
    started_event_id = _emit_automation_event(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={"instruction": instruction},
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    prompt = augment_bulk_instruction(_automation_prompt(automation), source="automation")
    try:
        from distr.core.workflow.dispatcher import start_workflow_run

        result = start_workflow_run(
            int(automation["workflow_id"]),
            context=prompt,
            run_metadata={
                "source_type": "automation",
                "source_label": "Automation",
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "instruction": instruction,
                "manual": True,
                "is_workflow_attached": True,
                "orchestration_event_ids": event_ids,
            },
        )
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        status = "dispatched"
        summary = "Automation instruction sent to the orchestrator."
        workflow_run_id = result.get("run_id")
    except Exception as exc:
        status = "failed"
        summary = f"Automation could not reach the orchestrator: {exc}"
        workflow_run_id = None

    dispatched_event_id = _emit_automation_event(
        automation=automation,
        event_type="worker_dispatched" if status == "dispatched" else "worker_failed",
        status=status,
        summary=summary,
        payload={"instruction": instruction, "workflow_run_id": workflow_run_id},
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)

    return {
        "status": status,
        "summary": summary,
        "workflow_run_id": workflow_run_id,
        "event_ids": event_ids,
    }


def create_routes() -> APIRouter:
    router = APIRouter()

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
        schedule = _workflow_schedule(payload.schedule, strict=True)
        now = _utcnow()
        with get_session() as db:
            workflow = AutoWorkflow(
                name=payload.name or "New Automation",
                description="Itemized DecisionsAI automation.",
                status=payload.status or "active",
                workflow_type="scheduled",
                context_rules=_automation_marker(schedule, payload.automation_type),
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
                instruction=payload.instruction or "",
                config=json.dumps({"source": "automation"}, ensure_ascii=False),
                timeout_seconds=300,
            )
            db.add(step)
            db.commit()
            db.refresh(workflow)
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
            workflow.context_rules = _automation_marker(schedule, _json_config(workflow.context_rules).get("automation_type") or "scheduled_instruction")
            workflow.modified_date = _utcnow()
            db.commit()
            db.refresh(workflow)
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
