"""
Workflow Service — CRUD operations for workflows, steps, variables, and runs.
Each module handles one concern; this file is the data layer.
"""
import json
import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

from distr.core.db import get_session
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep,
    AutoWorkflowVariable, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.gui.web.workflow_events import increment_workflow_updated

logger = logging.getLogger(__name__)


# ── Workflow type validation ──

VALID_WORKFLOW_TYPES = {"manual", "instruction", "scheduled", "audit"}


def validate_workflow_type(workflow_type: str) -> bool:
    """Return True if *workflow_type* is one of the allowed values, False otherwise."""
    return workflow_type in VALID_WORKFLOW_TYPES


def _safe_json_loads(text: Optional[str]) -> Any:
    """Parse a JSON string, returning an empty dict on None or invalid JSON."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Re-exports for backward compatibility ──
# These functions were extracted to dedicated modules but are re-exported
# here so existing callers don't break.

# Migration (moved to distr.core.workflow.migration)
from distr.core.workflow.migration import (  # noqa: F401, E402
    MIGRATION_MARKER_KEY,
    is_migration_degraded,
    _migration_degraded_mode,
    _SESSION_STATUS_MAP,
    _SESSION_TYPE_MAP,
    _RUN_STATUS_MAP,
    _check_migration_marker,
    _write_migration_marker,
    _parse_dt,
    _resequence_positions,
    migrate_step_runner_data,
)

# Planning (moved to distr.core.workflow.planning)
from distr.core.workflow.planning import (  # noqa: F401, E402
    PLAN_PROMPT,
    _is_simple_instruction,
    _litellm_model,
    _call_llm_for_plan,
    plan_workflow,
    build_step_context_prompt,
    generate_steps,
    generate_step_code,
    test_step_code,
)

# Audit trail (moved to distr.core.workflow.audit)
from distr.core.workflow.audit import (  # noqa: F401, E402
    get_or_create_audit_workflow,
    append_audit_step,
)

# Verification (moved to distr.core.workflow.verification)
from distr.core.workflow.verification import (  # noqa: F401, E402
    _run_verification,
    _verify_text_match,
    _verify_rule_based,
    _verify_llm_judgment,
    _verify_screenshot,
    _verify_playwright,
)

# Import/Export (moved to distr.core.workflow.import_export)
from distr.core.workflow.import_export import (  # noqa: F401, E402
    export_workflow,
    export_workflow_bundle,
    import_workflow,
    import_workflow_bundle,
    list_presets,
    load_preset,
    save_preset,
    _convert_legacy_to_unified,
    _is_legacy_format,
    _serialize_workflow,
    _serialize_step,
    _step_id_to_position,
    _position_to_step_id,
)


# ── Step config validation ──

def validate_step_config(step_type: str, config: dict) -> List[Dict[str, str]]:
    """Validate step configuration by delegating to ``StepValidator``.

    Returns an empty list when the configuration is valid, or a list of
    ``{"field": ..., "message": ...}`` dicts describing validation errors.

    **Validates: Requirements 2.5**
    """
    from distr.core.step_runner.validation import StepValidator

    errors = StepValidator().validate(step_type, config)
    return [{"field": e.field, "message": e.message} for e in errors]


# ── Workflow CRUD ──

def create_workflow(name: str = "Untitled Workflow", description: str = "", workflow_type: str = "manual") -> int:
    if not validate_workflow_type(workflow_type):
        raise ValueError(
            f"Invalid workflow_type '{workflow_type}'. Must be one of: {', '.join(sorted(VALID_WORKFLOW_TYPES))}"
        )
    with get_session() as db:
        wf = AutoWorkflow(name=name, description=description, status="draft", workflow_type=workflow_type)
        db.add(wf)
        db.commit()
        db.refresh(wf)
        return wf.id


def get_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        return _serialize_workflow(wf)


def get_workflow_type(workflow_id: int) -> Optional[str]:
    """Return the workflow_type for a workflow, or None if not found."""
    with get_session() as db:
        wf = db.query(AutoWorkflow.workflow_type).filter(AutoWorkflow.id == workflow_id).first()
        return wf[0] if wf else None


def list_workflows(limit: int = 50, search: Optional[str] = None, status: Optional[str] = None, workflow_type: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_session() as db:
        q = db.query(AutoWorkflow)
        if workflow_type:
            q = q.filter(AutoWorkflow.workflow_type == workflow_type)
        else:
            q = q.filter(AutoWorkflow.workflow_type != 'audit')
        if status:
            q = q.filter(AutoWorkflow.status == status)
        if search and search.strip():
            q = q.filter(AutoWorkflow.name.ilike(f"%{search.strip()}%"))
        rows = q.order_by(AutoWorkflow.modified_date.desc()).limit(limit).all()
        return [
            {
                "id": w.id, "name": w.name,
                "description": (w.description or "")[:200],
                "status": w.status,
                "schedule_enabled": w.schedule_enabled,
                "schedule_preset": w.schedule_preset,
                "schedule_time": w.schedule_time,
                "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
                "step_count": len(w.steps),
                "created_date": w.created_date.isoformat() if w.created_date else None,
                "modified_date": w.modified_date.isoformat() if w.modified_date else None,
            }
            for w in rows
        ]


def update_workflow(workflow_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "status", "schedule_enabled",
        "schedule_preset", "schedule_cron", "schedule_time",
        "schedule_days", "schedule_timezone", "next_run_at",
        "start_step_position", "workflow_type", "context_rules",
    }
    if "workflow_type" in kwargs and not validate_workflow_type(kwargs["workflow_type"]):
        raise ValueError(
            f"Invalid workflow_type '{kwargs['workflow_type']}'. Must be one of: {', '.join(sorted(VALID_WORKFLOW_TYPES))}"
        )
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(wf, k, v)
        db.commit()
        return True


def delete_workflow(workflow_id: int) -> bool:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        db.delete(wf)
        db.commit()
        return True


def duplicate_workflow(workflow_id: int) -> Optional[int]:
    with get_session() as db:
        orig = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not orig:
            return None
        new_wf = AutoWorkflow(
            name=f"{orig.name} (copy)", description=orig.description,
            status="draft", start_step_position=orig.start_step_position,
        )
        db.add(new_wf)
        db.flush()
        for step in sorted(orig.steps, key=lambda s: s.position):
            db.add(AutoWorkflowStep(
                workflow_id=new_wf.id, position=step.position,
                name=step.name, description=step.description,
                action_type=step.action_type, instruction=step.instruction,
                validation_type=step.validation_type,
                validation_prompt=step.validation_prompt,
                screenshot_path=step.screenshot_path,
                routing_mode=step.routing_mode,
                routing_prompt=step.routing_prompt,
                on_pass_goto=step.on_pass_goto, on_fail_goto=step.on_fail_goto,
                wait_before_next=step.wait_before_next,
                max_retries=step.max_retries,
                timeout_seconds=step.timeout_seconds,
                require_approval=step.require_approval,
                code=step.code,
                validation_code=step.validation_code,
                linked_project_id=step.linked_project_id,
                wait_for_continue=step.wait_for_continue,
            ))
        for var in orig.variables:
            db.add(AutoWorkflowVariable(
                workflow_id=new_wf.id, name=var.name,
                default_value=var.default_value, description=var.description,
            ))
        db.commit()
        return new_wf.id


# ── Step CRUD ──

def add_step(workflow_id: int, name: str = "New Step", action_type: str = "agent_instruction",
             position: Optional[int] = None) -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        if position is None:
            position = max((s.position for s in wf.steps), default=-1) + 1
        step = AutoWorkflowStep(workflow_id=workflow_id, position=position, name=name, action_type=action_type)
        db.add(step)
        db.commit()
        db.refresh(step)
        return step.id


def update_step(step_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "position", "action_type", "instruction",
        "validation_type", "validation_prompt", "screenshot_path",
        "routing_mode", "routing_prompt",
        "on_pass_goto", "on_fail_goto", "wait_before_next",
        "max_retries", "timeout_seconds", "require_approval",
        "status", "result", "recording_filename", "action_id",
        "code", "validation_code", "linked_project_id", "wait_for_continue",
    }
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(step, k, v)
        db.commit()
        return True


def delete_step(step_id: int) -> bool:
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        db.delete(step)
        db.commit()
        return True


def reorder_steps(workflow_id: int, step_ids: List[int]) -> bool:
    with get_session() as db:
        for pos, step_id in enumerate(step_ids):
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id, AutoWorkflowStep.workflow_id == workflow_id,
            ).first()
            if step:
                step.position = pos
        db.commit()
        return True


# ── Variable CRUD ──

def add_variable(workflow_id: int, name: str, default_value: str = "", description: str = "") -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        var = AutoWorkflowVariable(workflow_id=workflow_id, name=name, default_value=default_value, description=description)
        db.add(var)
        db.commit()
        db.refresh(var)
        return var.id


def update_variable(variable_id: int, **kwargs) -> bool:
    allowed = {"name", "default_value", "description"}
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(var, k, v)
        db.commit()
        return True


def delete_variable(variable_id: int) -> bool:
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        db.delete(var)
        db.commit()
        return True


# ── Run & step result queries ──

def get_active_run(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Get the currently active run for a workflow, if any."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status == "running",
        ).first()
        if not run:
            return None
        return {
            "id": run.id,
            "current_step_id": run.current_step_id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        }


def get_run_history(workflow_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .order_by(AutoWorkflowRun.started_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in rows
        ]


def get_step_results(step_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Get execution result history for a step."""
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.step_id == step_id)
            .order_by(AutoWorkflowStepResult.created_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "step_id": r.step_id,
                "run_id": r.run_id,
                "agent_response": r.agent_response or "",
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# ── Screenshot management ──

def save_screenshot(step_id: int, file_data: bytes, filename: str) -> Optional[str]:
    """Save a reference screenshot for screenshot_compare validation."""
    from distr.core.paths import DB_DIR
    screenshots_dir = os.path.join(DB_DIR, "workflow_screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".png"
    save_path = os.path.join(screenshots_dir, f"step_{step_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(file_data)
    update_step(step_id, screenshot_path=save_path)
    return save_path


# ── Workflow reset & history management ──

def reset_workflow_steps(workflow_id: int) -> Dict[str, Any]:
    """Cancel any active run and reset all step statuses to pending.

    Use when the user wants to stop everything and start fresh.
    """
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs
        cancelled_runs = 0
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
            cancelled_runs += 1

        # Reset all steps to pending
        for step in wf.steps:
            step.status = "pending"
            step.result = None
        db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "cancelled_runs": cancelled_runs,
        "steps_reset": len(wf.steps) if wf else 0,
    }


def clear_workflow_history(workflow_id: int) -> Dict[str, Any]:
    """Delete all run history and step results for a workflow."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs first
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
        db.commit()

    with get_session() as db:
        # Delete all step results for this workflow's runs
        run_ids = [
            r.id for r in
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .all()
        ]
        deleted_results = 0
        if run_ids:
            deleted_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
        # Delete all runs
        deleted_runs = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .delete(synchronize_session=False)
        )
        # Reset step statuses
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if wf:
            for step in wf.steps:
                step.status = "pending"
                step.result = None
        db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "deleted_runs": deleted_runs,
        "deleted_results": deleted_results,
    }


# ── Execution re-exports ──
# These functions now live in dispatcher.py but are re-exported here
# so existing callers don't break during the transition.

from distr.core.workflow.dispatcher import (  # noqa: F401, E402
    _RunContext,
    _active_runs,
    _runs_lock,
    _cleanup_run,
    _finalize_terminal_run,
    _clear_workflow_env,
    start_workflow_run,
    execute_step,
    cancel_run,
    cancel_step,
    complete_run,
    StepDispatcher,
)

# Re-export WorkflowAgent and WorkflowAgentBridge for backward compatibility
from distr.core.workflow_agent import WorkflowAgent  # noqa: F401, E402
from distr.core.step_runner.agent_bridge import WorkflowAgentBridge  # noqa: F401, E402

