"""Automation persistence — first-class rows, not disguised workflows."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from distr.core.db import get_session
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

logger = logging.getLogger(__name__)

AUTOMATION_SURFACE = "automation"
LEGACY_ID_PREFIX = "wf_"
PUBLIC_ID_PREFIX = "auto_"


class AutomationStoreError(ValueError):
    """Domain validation error for automation CRUD."""


def utc_now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def json_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(str(raw))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def public_id(record_id: int) -> str:
    return f"{PUBLIC_ID_PREFIX}{int(record_id)}"


def legacy_public_id(workflow_id: int) -> str:
    return f"{LEGACY_ID_PREFIX}{int(workflow_id)}"


def parse_public_id(raw: str | int) -> tuple[str, int]:
    """Return (kind, id) where kind is 'auto' or 'wf' (legacy workflow id)."""
    text = str(raw or "").strip()
    if text.startswith(PUBLIC_ID_PREFIX):
        suffix = text[len(PUBLIC_ID_PREFIX) :]
        if suffix.isdigit():
            return "auto", int(suffix)
    if text.startswith(LEGACY_ID_PREFIX):
        suffix = text[len(LEGACY_ID_PREFIX) :]
        if suffix.isdigit():
            return "wf", int(suffix)
    if text.isdigit():
        return "auto", int(text)
    raise AutomationStoreError("Automation not found")


def notify_automation_data_changed() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


def iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def normalize_schedule(schedule: dict[str, Any] | None, *, strict: bool = False) -> dict[str, Any]:
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
                raise AutomationStoreError(str(exc)) from exc
            time_value = "09:00"
    run_at = str(schedule.get("run_at") or "").strip()
    if kind == "once" and strict:
        if not run_at:
            raise AutomationStoreError("Run-at time is required for one-time automations")
        try:
            from distr.core.workflow.scheduler import normalize_once_run_at_storage, parse_once_run_at_as_utc

            parsed = parse_once_run_at_as_utc(run_at)
            if not parsed:
                raise ValueError(f"Invalid run-at time: {run_at!r}")
            run_at = normalize_once_run_at_storage(run_at)
        except ValueError as exc:
            raise AutomationStoreError(str(exc)) from exc
    interval_value = 15
    interval_unit = "minutes"
    if kind == "interval":
        try:
            interval_value = max(1, int(schedule.get("interval") or schedule.get("interval_value") or 15))
        except (TypeError, ValueError):
            if strict:
                raise AutomationStoreError("Interval must be a positive number") from None
            interval_value = 15
        interval_unit = str(schedule.get("interval_unit") or "minutes").strip().lower()
        if interval_unit.startswith("sec"):
            interval_unit = "seconds"
        else:
            interval_unit = "minutes"
        if strict and interval_unit == "seconds" and interval_value > 86400:
            raise AutomationStoreError("Interval cannot exceed 86400 seconds")
        if strict and interval_unit == "minutes" and interval_value > 1440:
            raise AutomationStoreError("Interval cannot exceed 1440 minutes")
    return {
        "kind": kind,
        "time": time_value,
        "run_at": run_at,
        "interval": interval_value,
        "interval_unit": interval_unit,
        "days": str(schedule.get("days") or schedule.get("schedule_days") or "1"),
        "timezone": str(schedule.get("timezone") or ""),
    }


def compute_next_run(schedule: dict[str, Any]) -> datetime | None:
    try:
        from distr.core.workflow.scheduler import _next_run_from_cron, next_run_for_interval, schedule_to_cron

        if schedule.get("kind") == "interval":
            return next_run_for_interval(
                int(schedule.get("interval") or 15),
                str(schedule.get("interval_unit") or "minutes"),
                utc_now(),
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
        return _next_run_from_cron(cron or "", utc_now(), schedule.get("timezone"), allow_current_minute=True)
    except Exception:
        return None


def apply_schedule_to_row(row: Automation, schedule: dict[str, Any]) -> None:
    schedule = normalize_schedule(schedule, strict=True)
    row.schedule_enabled = (row.status or "active").strip().lower() == "active"
    row.schedule_preset = schedule["kind"]
    if schedule["kind"] == "once":
        row.schedule_time = schedule.get("run_at")
    elif schedule["kind"] == "interval":
        row.schedule_time = f"{schedule.get('interval')}:{schedule.get('interval_unit')}"
    else:
        row.schedule_time = schedule.get("time")
    row.schedule_days = schedule.get("days") or "1"
    row.schedule_timezone = schedule.get("timezone") or None
    row.schedule_config = json.dumps(schedule, ensure_ascii=False, default=str)
    row.next_run_at = compute_next_run(schedule) if row.schedule_enabled else None
    if row.schedule_enabled and row.next_run_at is None:
        raise AutomationStoreError("Schedule could not produce a next run time")


def schedule_dict_from_row(row: Automation) -> dict[str, Any]:
    stored = json_config(row.schedule_config)
    if stored:
        return normalize_schedule(stored)
    return normalize_schedule(
        {
            "kind": row.schedule_preset or "daily",
            "time": row.schedule_time or "09:00",
            "days": row.schedule_days or "1",
            "timezone": row.schedule_timezone or "",
        }
    )


def serialize_automation(row: Automation) -> dict[str, Any]:
    from distr.core.workflow.scheduler import once_run_at_for_datetime_local_input

    schedule = schedule_dict_from_row(row)
    run_at_display = schedule.get("run_at") or ""
    if schedule.get("kind") == "once" and run_at_display:
        run_at_display = once_run_at_for_datetime_local_input(run_at_display)
    action_config = json_config(row.action_config)
    return {
        "id": public_id(row.id),
        "record_id": row.id,
        "workflow_id": row.legacy_workflow_id,
        "step_id": None,
        "name": row.name or "Untitled Automation",
        "automation_type": row.automation_type or "scheduled_instruction",
        "preset_id": str(row.preset_id or "").strip(),
        "action_config": dict(action_config),
        "status": row.status or "active",
        "instruction": (row.instruction or "") or "",
        "schedule": {
            "kind": schedule["kind"],
            "time": schedule.get("time") or "09:00",
            "run_at": run_at_display,
            "interval": schedule.get("interval") or 15,
            "interval_unit": schedule.get("interval_unit") or "minutes",
            "days": schedule.get("days") or "1",
            "timezone": schedule.get("timezone") or "",
        },
        "created_at": iso(row.created_date),
        "updated_at": iso(row.modified_date),
        "last_run_at": iso(row.last_run_at),
        "next_run_at": iso(row.next_run_at),
        "run_health": "healthy",
        "health": "healthy",
        "is_workflow_attached": row.legacy_workflow_id is not None,
    }


def is_automation_workflow(workflow: AutoWorkflow | None) -> bool:
    if workflow is None:
        return False
    marker = json_config(workflow.context_rules)
    return (
        workflow.workflow_type == "scheduled"
        and str(marker.get("decisions_surface") or "").strip().lower() == AUTOMATION_SURFACE
    )


def _first_instruction_step(workflow: AutoWorkflow) -> AutoWorkflowStep | None:
    steps = sorted(list(workflow.steps or []), key=lambda s: s.position or 0)
    return steps[0] if steps else None


def serialize_legacy_workflow(workflow: AutoWorkflow) -> dict[str, Any]:
    """Serialize unmigrated automation workflow (fallback during transition)."""
    marker = json_config(workflow.context_rules)
    schedule = normalize_schedule(marker.get("schedule") if isinstance(marker.get("schedule"), dict) else {})
    step = _first_instruction_step(workflow)
    action_config = marker.get("action_config") if isinstance(marker.get("action_config"), dict) else {}
    from distr.core.workflow.scheduler import once_run_at_for_datetime_local_input

    run_at_display = schedule.get("run_at") or ""
    if schedule.get("kind") == "once" and run_at_display:
        run_at_display = once_run_at_for_datetime_local_input(run_at_display)
    return {
        "id": legacy_public_id(workflow.id),
        "record_id": None,
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
        "created_at": iso(workflow.created_date),
        "updated_at": iso(workflow.modified_date),
        "last_run_at": iso(workflow.last_run_at),
        "next_run_at": iso(workflow.next_run_at),
        "run_health": "healthy",
        "health": "healthy",
        "is_workflow_attached": True,
    }


def get_automation_row(public: str | int) -> Automation | None:
    kind, raw_id = parse_public_id(public)
    with get_session() as session:
        if kind == "auto":
            return session.query(Automation).filter(Automation.id == raw_id).first()
        return (
            session.query(Automation)
            .filter(Automation.legacy_workflow_id == raw_id)
            .first()
        )


def get_automation(public: str | int) -> dict[str, Any] | None:
    kind, raw_id = parse_public_id(public)
    with get_session() as session:
        if kind == "auto":
            row = session.query(Automation).filter(Automation.id == raw_id).first()
            if row:
                return serialize_automation(row)
        else:
            row = (
                session.query(Automation)
                .filter(Automation.legacy_workflow_id == raw_id)
                .first()
            )
            if row:
                return serialize_automation(row)
            workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == raw_id).first()
            if workflow and is_automation_workflow(workflow):
                return serialize_legacy_workflow(workflow)
    return None


def list_automations() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.query(Automation).order_by(Automation.modified_date.desc()).all()
        if rows:
            return [serialize_automation(row) for row in rows]
        legacy = (
            session.query(AutoWorkflow)
            .filter(AutoWorkflow.workflow_type == "scheduled")
            .order_by(AutoWorkflow.modified_date.desc())
            .all()
        )
        return [serialize_legacy_workflow(row) for row in legacy if is_automation_workflow(row)]


def list_due_automations() -> list[dict[str, Any]]:
    now = utc_now()
    with get_session() as session:
        rows = (
            session.query(Automation)
            .filter(
                Automation.schedule_enabled == True,  # noqa: E712
                Automation.next_run_at.isnot(None),
                Automation.next_run_at <= now,
            )
            .all()
        )
        due: list[dict[str, Any]] = []
        for row in rows:
            active = (
                session.query(AutomationRun)
                .filter(
                    AutomationRun.automation_id == row.id,
                    AutomationRun.status.in_(["running", "waiting", "dispatched"]),
                )
                .first()
            )
            if active:
                continue
            due.append(serialize_automation(row))
        if due:
            return due
        legacy_rows = (
            session.query(AutoWorkflow)
            .filter(
                AutoWorkflow.workflow_type == "scheduled",
                AutoWorkflow.schedule_enabled == True,  # noqa: E712
                AutoWorkflow.next_run_at.isnot(None),
                AutoWorkflow.next_run_at <= now,
            )
            .all()
        )
        for workflow in legacy_rows:
            if not is_automation_workflow(workflow):
                continue
            if session.query(Automation).filter(Automation.legacy_workflow_id == workflow.id).first():
                continue
            due.append(serialize_legacy_workflow(workflow))
        return due


def create_automation(
    *,
    name: str,
    automation_type: str,
    status: str,
    instruction: str,
    preset_id: str,
    schedule: dict[str, Any],
    action_config: dict[str, Any],
) -> dict[str, Any]:
    schedule = normalize_schedule(schedule, strict=True)
    now = utc_now()
    with get_session() as session:
        row = Automation(
            name=name or "New Automation",
            description="DecisionsAI automation.",
            status=status or "active",
            automation_type=automation_type or "scheduled_instruction",
            preset_id=str(preset_id or "").strip(),
            instruction=instruction or "",
            action_config=json.dumps(action_config or {}, ensure_ascii=False, default=str),
            created_date=now,
            modified_date=now,
        )
        apply_schedule_to_row(row, schedule)
        session.add(row)
        session.commit()
        session.refresh(row)
        notify_automation_data_changed()
        return serialize_automation(row)


def update_automation(public: str | int, **fields: Any) -> dict[str, Any]:
    kind, raw_id = parse_public_id(public)
    if kind != "auto":
        raise AutomationStoreError("Automation not found")
    with get_session() as session:
        db_row = session.query(Automation).filter(Automation.id == raw_id).first()
        if not db_row:
            raise AutomationStoreError("Automation not found")
        if "name" in fields and fields["name"] is not None:
            db_row.name = fields["name"] or db_row.name
        if "status" in fields and fields["status"] is not None:
            db_row.status = fields["status"] or db_row.status
        if "instruction" in fields and fields["instruction"] is not None:
            db_row.instruction = fields["instruction"] or ""
        if "preset_id" in fields and fields["preset_id"] is not None:
            db_row.preset_id = str(fields["preset_id"] or "").strip()
        if "action_config" in fields and isinstance(fields["action_config"], dict):
            db_row.action_config = json.dumps(fields["action_config"], ensure_ascii=False, default=str)
        if "automation_type" in fields and fields["automation_type"]:
            db_row.automation_type = fields["automation_type"]
        schedule = schedule_dict_from_row(db_row)
        if "schedule" in fields and isinstance(fields["schedule"], dict):
            schedule = normalize_schedule(fields["schedule"], strict=True)
        if "schedule" in fields or "status" in fields:
            apply_schedule_to_row(db_row, schedule)
        db_row.modified_date = utc_now()
        session.commit()
        session.refresh(db_row)
        notify_automation_data_changed()
        return serialize_automation(db_row)


def delete_automation(public: str | int) -> bool:
    try:
        kind, raw_id = parse_public_id(public)
    except AutomationStoreError:
        return False
    if kind != "auto":
        return False
    with get_session() as session:
        db_row = session.query(Automation).filter(Automation.id == raw_id).first()
        if not db_row:
            return False
        session.delete(db_row)
        session.commit()
        notify_automation_data_changed()
        return True


def advance_automation_next_run(record_id: int) -> None:
    from distr.core.automation.scheduler_advance import advance_next_run_for_automation

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == int(record_id)).first()
        if not row:
            return
        advance_next_run_for_automation(row)
        row.modified_date = utc_now()
        session.commit()


def record_automation_run(
    *,
    automation: dict[str, Any],
    status: str,
    summary: str,
    event_ids: list[int] | None = None,
    manual: bool = False,
    prompt: str = "",
    chat_id: int | None = None,
    schedule_metadata: dict[str, Any] | None = None,
    execution_mode: str = "agent_chat_orchestrator",
    tool_result: dict[str, Any] | None = None,
) -> int | None:
    record_id = automation.get("record_id")
    now = utc_now()
    run_data = {
        "source_type": "automation",
        "source_label": "Automation",
        "execution_mode": execution_mode,
        "automation_id": automation.get("id"),
        "automation_name": automation.get("name"),
        "preset_id": automation.get("preset_id") or "",
        "instruction": automation.get("instruction"),
        "action_config": automation.get("action_config") or {},
        "manual": bool(manual),
        "message": summary,
        "summary": summary,
        "chat_id": chat_id,
        "is_workflow_attached": bool(automation.get("is_workflow_attached")),
        "orchestration_event_ids": event_ids or [],
        "prompt_preview": (prompt or "")[:1500],
        **(schedule_metadata or {}),
    }
    if tool_result:
        run_data["tool_result"] = tool_result

    if record_id:
        with get_session() as session:
            run = AutomationRun(
                automation_id=int(record_id),
                status=status,
                started_at=now,
                completed_at=now if status in {"failed", "skipped", "completed"} else None,
                run_data=json.dumps(run_data, ensure_ascii=False, default=str),
            )
            session.add(run)
            auto_row = session.query(Automation).filter(Automation.id == int(record_id)).first()
            if auto_row:
                auto_row.last_run_at = now
                auto_row.modified_date = now
            session.commit()
            return run.id

    workflow_id = automation.get("workflow_id")
    if not workflow_id:
        return None
    with get_session() as session:
        run = AutoWorkflowRun(
            workflow_id=int(workflow_id),
            status=status,
            started_at=now,
            completed_at=now if status in {"failed", "skipped", "completed"} else None,
            current_step_id=automation.get("step_id"),
            run_data=json.dumps(run_data, ensure_ascii=False, default=str),
        )
        session.add(run)
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if workflow:
            workflow.last_run_at = now
            workflow.modified_date = now
        session.commit()
        return run.id


def list_automation_runs(public: str | int, *, limit: int = 50) -> list[dict[str, Any]]:
    automation = get_automation(public)
    if not automation:
        raise AutomationStoreError("Automation not found")
    runs: list[dict[str, Any]] = []
    record_id = automation.get("record_id")
    if record_id:
        with get_session() as session:
            rows = (
                session.query(AutomationRun)
                .filter(AutomationRun.automation_id == int(record_id))
                .order_by(AutomationRun.started_at.desc())
                .limit(limit)
                .all()
            )
            for row in rows:
                data = json_config(row.run_data)
                runs.append(
                    {
                        "id": f"run_{row.id}",
                        "automation_run_id": row.id,
                        "automation_id": automation["id"],
                        "started_at": iso(row.started_at),
                        "completed_at": iso(row.completed_at),
                        "status": row.status,
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
                )
        return runs
    workflow_id = automation.get("workflow_id")
    if not workflow_id:
        return runs
    with get_session() as session:
        rows = (
            session.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowRun.started_at.desc())
            .limit(limit)
            .all()
        )
        for row in rows:
            data = json_config(row.run_data)
            runs.append(
                {
                    "id": f"run_{row.id}",
                    "workflow_run_id": row.id,
                    "workflow_id": row.workflow_id,
                    "automation_id": automation["id"],
                    "started_at": iso(row.started_at),
                    "completed_at": iso(row.completed_at),
                    "status": row.status,
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
            )
    return runs


def migrate_legacy_automation_workflows() -> int:
    """Copy legacy AutoWorkflow automations into automations table; disable legacy schedules."""
    migrated = 0
    with get_session() as session:
        candidates = (
            session.query(AutoWorkflow)
            .filter(AutoWorkflow.workflow_type == "scheduled")
            .order_by(AutoWorkflow.id.asc())
            .all()
        )
        for workflow in candidates:
            if not is_automation_workflow(workflow):
                continue
            existing = (
                session.query(Automation)
                .filter(Automation.legacy_workflow_id == workflow.id)
                .first()
            )
            if existing:
                if workflow.schedule_enabled:
                    workflow.schedule_enabled = False
                    workflow.next_run_at = None
                continue
            marker = json_config(workflow.context_rules)
            schedule = normalize_schedule(marker.get("schedule") if isinstance(marker.get("schedule"), dict) else {})
            step = _first_instruction_step(workflow)
            now = utc_now()
            row = Automation(
                name=workflow.name or "Untitled Automation",
                description=workflow.description or "",
                status=workflow.status or "active",
                automation_type=marker.get("automation_type") or "scheduled_instruction",
                preset_id=str(marker.get("preset_id") or "").strip(),
                instruction=(step.instruction if step else "") or "",
                action_config=json.dumps(
                    marker.get("action_config") if isinstance(marker.get("action_config"), dict) else {},
                    ensure_ascii=False,
                    default=str,
                ),
                schedule_enabled=bool(workflow.schedule_enabled),
                schedule_preset=workflow.schedule_preset,
                schedule_time=workflow.schedule_time,
                schedule_days=workflow.schedule_days,
                schedule_timezone=workflow.schedule_timezone,
                schedule_config=json.dumps(schedule, ensure_ascii=False, default=str),
                next_run_at=workflow.next_run_at,
                last_run_at=workflow.last_run_at,
                legacy_workflow_id=workflow.id,
                created_date=workflow.created_date or now,
                modified_date=workflow.modified_date or now,
            )
            session.add(row)
            workflow.schedule_enabled = False
            workflow.next_run_at = None
            workflow.modified_date = now
            migrated += 1
        session.commit()
    if migrated:
        logger.info("Migrated %d legacy automation workflow(s) to automations table", migrated)
    return migrated
