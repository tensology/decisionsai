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


# ── Backward-compatible shims ──
# These functions were removed during the workflow-execution-engine refactoring
# but are re-created here as thin wrappers so existing tests and callers
# continue to work during the transition.


def _speak_result(result: str):
    """Speak the agent result via TTS if it's meaningful."""
    if not result or not result.strip():
        return
    text = result.strip()
    if len(text) > 500:
        text = text[:500] + "..."
    try:
        from distr.core.signals import signal_manager
        signal_manager.speak_text_directly.emit(text)
    except Exception:
        pass


def _check_and_enter_wait(step_id: int, action_result: str, passed: bool):
    """Check if a step has wait_for_continue and enter waiting state.

    Backward-compatible shim. Returns a wait response dict if the step
    should wait, or None to proceed normally.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step or not step.wait_for_continue:
            return None
        step.status = "waiting"
        step_name = step.name
        workflow_id = step.workflow_id
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.current_step_id == step_id,
            AutoWorkflowRun.status == "running",
        ).first()
        run_id = None
        if run:
            run.status = "waiting"
            run_id = run.id
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = action_result
            run_data["waiting_passed"] = passed
            run.run_data = json.dumps(run_data)
        db.commit()

    increment_workflow_updated()

    # Notify via TTS
    try:
        from distr.core.signals import signal_manager
        speak = action_result.strip()[:400]
        if len(action_result.strip()) > 400:
            speak += "..."
        signal_manager.speak_text_directly.emit(
            f"Step '{step_name}' is done and waiting for your input. Here's what happened: {speak}")
    except Exception:
        pass

    # Queue report for main agent
    try:
        from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
        WorkflowAgentBridge().queue_report_to_agent(
            workflow_id,
            f"Workflow step '{step_name}' completed and is now WAITING for your input. "
            f"Run ID: {run_id}. Result: {action_result[:500]}")
    except Exception:
        pass

    return {"success": True, "waiting": True, "message": "Step waiting for continue signal."}


def complete_step(step_id: int, result: str, passed: bool, _from_continue: bool = False):
    """Mark a step as complete, run verification, store result, route to next.

    Backward-compatible shim with inline routing logic so it works with
    mocked get_session at the service module level.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return {"error": "Step not found"}

        # Check wait_for_continue (skip if resuming from continue)
        if step.wait_for_continue and not _from_continue:
            wait_result = _check_and_enter_wait(step_id, result, passed)
            if wait_result:
                return wait_result

        # Run verification
        verified_passed = _run_verification(step, result, passed)
        status = "passed" if verified_passed else "failed"
        step.status = status
        step.result = result

        # Find active run
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.status == "running",
            AutoWorkflowRun.current_step_id == step_id,
        ).first()

        # Store result in history
        step_result = AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run.id if run else None,
            agent_response=result,
            status=status,
        )
        db.add(step_result)

        if not run:
            db.commit()
            increment_workflow_updated()
            _speak_result(result)
            return {"done": True, "status": status}

        # Inline routing logic (avoids StepRouter's separate get_session)
        goto = step.on_pass_goto if verified_passed else step.on_fail_goto

        if goto is None or goto == -1:
            # END workflow
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            _run_id, _wf_id = run.id, step.workflow_id
            db.commit()
            increment_workflow_updated()
            _speak_result(result)
            _finalize_terminal_run(_run_id, _wf_id, "completed")
            return {"done": True, "status": "completed", "run_id": _run_id}

        # Safety: prevent routing to self
        if goto == step_id:
            logger.warning("Step %d routes to itself. Ending workflow.", step_id)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            _run_id, _wf_id = run.id, step.workflow_id
            db.commit()
            increment_workflow_updated()
            _finalize_terminal_run(_run_id, _wf_id, "completed")
            return {"done": True, "status": "completed", "run_id": _run_id,
                    "warning": "Infinite loop prevented"}

        # Route to next step
        next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == goto).first()
        if not next_step:
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            _run_id, _wf_id = run.id, step.workflow_id
            db.commit()
            increment_workflow_updated()
            _finalize_terminal_run(_run_id, _wf_id, "completed")
            return {"done": True, "status": "completed", "run_id": _run_id}

        next_step.status = "running"
        run.current_step_id = next_step.id
        run_id = run.id
        db.commit()

    increment_workflow_updated()
    _speak_result(result)

    # Dispatch next step
    _dispatch_step(
        next_step.id,
        next_step.name,
        next_step.action_type or "agent_instruction",
        next_step.instruction or "",
        next_step.recording_filename or "",
        action_id=next_step.action_id,
        code=next_step.code or "",
    )
    return {"done": False, "status": status, "next_step_id": next_step.id, "run_id": run_id}


def _dispatch_step(step_id: int, step_name: str, action_type: str,
                   instruction: str, recording_filename: str,
                   context_prefix: str = "Step Runner",
                   action_id: int = None,
                   code: str = None) -> Dict[str, Any]:
    """Dispatch a step based on its action_type.

    Backward-compatible shim that implements the old _dispatch_step logic
    using the same get_session as service.py so mocks work correctly.
    """
    if action_type in ("execute_code", "playwright"):
        exec_code = (code or "").strip()
        if not exec_code and (instruction or "").strip():
            try:
                from distr.core.step_runner.code_generator import CodeGeneratorService
                from distr.core.step_runner.step_types import StepType
                step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE
                exec_code = CodeGeneratorService().generate_code(instruction, step_type)
            except Exception as e:
                logger.error("Code generation failed for step %s: %s", step_id, e)
                update_step(step_id, status="failed", result=f"Code generation failed: {e}")
                return {"error": f"Code generation failed: {e}"}
        if not exec_code:
            update_step(step_id, status="failed", result="No code or instruction provided.")
            return {"error": "No code or instruction provided"}
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            svc = TestLoopService()
            if action_type == "playwright":
                exec_result = svc._execute_playwright(exec_code)
            else:
                exec_result = svc._execute_python(exec_code)
            stdout = getattr(exec_result, "stdout", "") or ""
            stderr = getattr(exec_result, "stderr", "") or ""
            exit_code = getattr(exec_result, "exit_code", None)
            if exit_code is None:
                exit_code = 1
            output = (stdout + "\n" + stderr).strip()[:2000]
            passed_result = exit_code == 0
            complete_step(step_id, output, passed_result)
            return {"success": True, "output": output, "passed": passed_result}
        except Exception as e:
            error_msg = f"{action_type} execution error: {e}"
            complete_step(step_id, error_msg, False)
            return {"error": error_msg}

    elif action_type == "play_recording":
        rec_name = recording_filename or ""
        if not rec_name and action_id:
            try:
                from distr.core.db import Action
                with get_session() as db:
                    action = db.query(Action).filter(Action.id == action_id).first()
                    if action and action.recording_filename:
                        rec_name = action.recording_filename
            except Exception:
                pass
        if not rec_name:
            update_step(step_id, status="failed", result="No recording attached.")
            return {"error": "No recording attached to this step"}
        try:
            from distr.core.signals import signal_manager
            signal_manager.play_recording_file.emit(rec_name)
            return {"success": True, "message": "Playing recording."}
        except Exception as e:
            return {"error": f"Recording playback error: {e}"}

    else:
        # Agent instruction — send to WorkflowAgent if available
        if not instruction or not instruction.strip():
            update_step(step_id, status="failed", result="No instruction provided.")
            return {"error": "No instruction provided"}

        prompt = f"[{context_prefix} — {step_name}]\n{instruction}"

        # Check for active run with a WorkflowAgent
        run_id = None
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step.workflow_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    run_id = run.id

        if run_id:
            import asyncio
            with _runs_lock:
                ctx = _active_runs.get(run_id)
            if ctx:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        ctx.workflow_agent.execute(prompt), ctx.event_loop)

                    def _on_done(fut):
                        try:
                            resp = fut.result()
                            # Check wait state before completing
                            wait_result = _check_and_enter_wait(step_id, resp, True)
                            if not wait_result:
                                complete_step(step_id, resp, True)
                        except Exception as exc:
                            complete_step(step_id, str(exc), False)

                    future.add_done_callback(_on_done)
                    return {"success": True, "message": "Step dispatched to WorkflowAgent."}
                except Exception as e:
                    return {"error": str(e)}

        # Fallback: send to main agent via signal
        try:
            from distr.core.signals import signal_manager
            signal_manager.send_text_input.emit(prompt, False, None, None)
            return {"success": True, "message": "Step sent to agent."}
        except Exception as e:
            return {"error": f"Agent dispatch error: {e}"}


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status.

    Backward-compatible shim that retrieves stored result and calls complete_step.
    """
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == run.current_step_id,
        ).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}

        step_id = step.id
        run_data = json.loads(run.run_data or "{}")
        stored_result = run_data.get("waiting_result", "")
        stored_passed = run_data.get("waiting_passed", True)

        # Append optional user input to the stored result
        if optional_input and optional_input.strip():
            stored_result = f"{stored_result}\n\n[CONTINUE INPUT]: {optional_input.strip()}"

        # Set run and step back to running
        run.status = "running"
        step.status = "running"
        db.commit()

    increment_workflow_updated()

    # Call complete_step with the stored result (skip wait check via _from_continue)
    return complete_step(step_id, stored_result, stored_passed, _from_continue=True)
