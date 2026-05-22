"""
Workflow routes — /workflows/*
"""
from fastapi import Request, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import re
import time
import threading

from ._shared import logger


_isolated_step_exec_lock = threading.Lock()
_isolated_step_exec_started_at = {}


def _workflow_feedback_message(action: str, result: dict | None = None) -> dict:
    """Return human-readable workflow feedback for API/UI callers."""
    result = result or {}
    if action == "run_started":
        return {
            "message": "Workflow run started.",
            "next_action": "Watch Active Runs for the current step and final result.",
        }
    if action == "cancelled":
        return {
            "message": "Workflow run cancelled.",
            "next_action": "Review the run history before starting it again.",
        }
    if action == "reset":
        return {
            "message": "Workflow stopped and reset.",
            "next_action": "Run it again when the steps look right.",
        }
    if action == "clear_audit":
        return {
            "message": "Workflow run history cleared.",
            "next_action": "Executor sessions and orchestration events were left intact.",
        }
    if action == "clear_events":
        return {
            "message": "Workflow events cleared.",
            "next_action": "New orchestration events will appear when the workflow runs again.",
        }
    if action == "clear_executor":
        return {
            "message": "Executor log cleared.",
            "next_action": "New CLI or IDE sessions will appear when a run reaches an executor step.",
        }
    if action == "continued":
        decision = result.get("action") or ""
        if decision == "next_step":
            return {
                "message": f"Workflow continued to step #{result.get('step_id')}.",
                "next_action": "Watch Active Runs for the next step outcome.",
            }
        if decision == "end_run":
            return {
                "message": f"Workflow finished with status: {result.get('status', 'completed')}.",
                "next_action": "Open run history for the final evidence packet.",
            }
        if decision == "waiting":
            return {
                "message": "Workflow is still waiting for input.",
                "next_action": "Provide the missing decision or continue instruction.",
            }
        return {
            "message": "Workflow continued.",
            "next_action": "Refresh Active Runs to see the latest state.",
        }
    return {"message": "Workflow updated.", "next_action": "Refresh the workflow status."}


def _workflow_error_payload(error: str, action: str = "workflow") -> dict:
    """Normalize workflow errors into useful, non-noisy API payloads."""
    raw = str(error or "Workflow request failed.").strip()
    lower = raw.lower()
    detail = raw
    next_action = "Refresh the workflow and check the current run state."

    if "already in progress" in lower:
        detail = "A run is already active for this workflow scope."
        next_action = "Open Active Runs, then continue, cancel, or wait for that run."
    elif "not waiting" in lower:
        detail = "This workflow is not currently waiting for input."
        next_action = "Refresh Active Runs; continue only applies to waiting runs."
    elif "no waiting step" in lower:
        detail = "There is no waiting step to continue."
        next_action = "Refresh the workflow status and inspect the current step."
    elif "has no steps" in lower:
        detail = "This workflow has no steps to run."
        next_action = "Add at least one workflow step, then run it again."
    elif "no instruction" in lower or "no command configured" in lower or "no url configured" in lower:
        detail = "A workflow step is missing required configuration."
        next_action = "Open the highlighted step and fill in the missing action details."
    elif "audit workflows are read-only" in lower:
        detail = "Audit workflows are read-only."
        next_action = "Duplicate or create a non-audit workflow before editing or running it."
    elif "run not found" in lower:
        detail = "That workflow run no longer exists."
        next_action = "Refresh Active Runs and use the latest run id."
    elif "workflow not found" in lower:
        detail = "That workflow no longer exists."
        next_action = "Refresh the workflow list."
    elif "step not found" in lower:
        detail = "That workflow step no longer exists."
        next_action = "Refresh the workflow details."

    return {
        "detail": detail,
        "raw_detail": raw,
        "action": action,
        "next_action": next_action,
    }


# ---- Pydantic models (only used in this module) ----

class WorkflowCreateRequest(BaseModel):
    name: str = "Untitled Workflow"
    description: str = ""
    workflow_type: Optional[str] = None


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_preset: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    schedule_timezone: Optional[str] = None
    start_step_position: Optional[int] = None
    workflow_type: Optional[str] = None
    context_rules: Optional[str] = None
    run_settings: Optional[dict] = None


class CodexBridgeEventRequest(BaseModel):
    event_type: str = "codex_event"
    status: Optional[str] = None
    message: str = ""
    input: str = ""
    output: str = ""
    execution_session_id: Optional[int] = None
    step_id: Optional[int] = None
    ticket_id: Optional[int] = None
    project_id: Optional[int] = None
    payload: Optional[dict] = None
    evidence: Optional[dict] = None


class StepCreateRequest(BaseModel):
    name: str = "New Step"
    action_type: str = "agent_instruction"
    position: Optional[int] = None


class StepReorderRequest(BaseModel):
    step_ids: List[int]


class WorkflowGenerateRequest(BaseModel):
    description: str


class WorkflowPlanRequest(BaseModel):
    instruction: str
    chat_id: Optional[int] = None


class WorkflowGenerateStepsRequest(BaseModel):
    instruction: str


class WorkflowGenerateCodeRequest(BaseModel):
    instruction: str
    step_type: str


class WorkflowTestCodeRequest(BaseModel):
    code: str
    step_type: str
    headless: bool = True


class WorkflowValidateStepRequest(BaseModel):
    step_type: str
    config: dict


class WorkflowScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    timezone: Optional[str] = None


class WorkflowSeedFixturesRequest(BaseModel):
    force_reset: bool = False
    workflow_names: Optional[List[str]] = None


class WorkflowPurgeAllRequest(BaseModel):
    confirm: bool = False
    include_audit: bool = False


class ContextItemCreateRequest(BaseModel):
    title: str
    content: str = ""
    notes: str = ""


class ContextItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    notes: Optional[str] = None


def register_routes(router, templates):

    def _is_audit_workflow(workflow_id: int) -> bool:
        """Return True if the workflow exists and has workflow_type='audit'."""
        from distr.core.workflow.service import get_workflow_type
        return get_workflow_type(workflow_id) == "audit"

    @router.get("/workflows")
    async def workflow_list(limit: int = 50, search: Optional[str] = None, type: Optional[str] = None):
        try:
            from distr.core.workflow.service import list_workflows
            return JSONResponse(list_workflows(limit=limit, search=search, workflow_type=type))
        except Exception as e:
            logger.error("Workflow list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows")
    async def workflow_create(data: WorkflowCreateRequest):
        try:
            from distr.core.workflow.service import create_workflow, get_workflow
            kwargs = {"name": data.name, "description": data.description}
            if data.workflow_type is not None:
                kwargs["workflow_type"] = data.workflow_type
            wf_id = create_workflow(**kwargs)
            return JSONResponse(get_workflow(wf_id))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/purge-all")
    async def workflow_purge_all(data: WorkflowPurgeAllRequest):
        """Remove every workflow (except audit workflows unless include_audit is True)."""
        if not data.confirm:
            return JSONResponse(
                {"detail": "Set confirm=true in the JSON body to delete all workflows."},
                status_code=400,
            )
        try:
            from distr.core.workflow.service import purge_all_workflows
            from distr.gui.web.workflow_events import increment_workflow_updated

            removed = purge_all_workflows(include_audit=data.include_audit)
            increment_workflow_updated()
            return JSONResponse({"success": True, "removed": removed})
        except Exception as e:
            logger.error("Workflow purge-all failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Plan / Version / Step-level endpoints (must be before {workflow_id} routes) ──

    @router.post("/workflows/plan")
    async def workflow_plan(data: WorkflowPlanRequest):
        """Plan a workflow from a natural-language instruction."""
        try:
            from distr.core.workflow.service import plan_workflow, get_workflow
            from distr.gui.web.workflow_events import increment_workflow_updated
            wf_id = plan_workflow(data.instruction, chat_id=data.chat_id)
            if not wf_id:
                return JSONResponse({"detail": "Failed to plan workflow"}, status_code=500)
            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/version")
    async def workflow_version():
        """Return a version counter that increments when workflow data changes. UI polls to refresh."""
        try:
            from distr.gui.web.workflow_events import get_workflow_update_counter
            return JSONResponse({"version": get_workflow_update_counter()})
        except Exception:
            return JSONResponse({"version": 0})

    @router.get("/workflows/events")
    async def get_workflow_events_since(since: int = 0):
        """Return workflow events logged after *since* version.

        Clients reconnecting after a WebSocket gap call this to check whether
        they missed updates, then refresh their state if the returned list
        is non-empty.
        """
        from distr.gui.web.workflow_events import get_events_since, get_workflow_update_counter
        events = get_events_since(since)
        return JSONResponse({
            "current_version": get_workflow_update_counter(),
            "missed": len(events),
            "events": events,
        })

    @router.get("/workflows/llm-settings")
    async def get_workflow_llm_settings():
        """Return the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        return JSONResponse({
            "provider": settings.get("workflow_llm_provider") or "",
            "model": settings.get("workflow_llm_model") or "",
        })

    @router.post("/workflows/llm-settings")
    async def save_workflow_llm_settings(request: Request):
        """Save the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        data = await request.json()
        settings = load_settings_from_db()
        provider = (data.get("provider") or "").strip()
        model = (data.get("model") or "").strip()
        settings["workflow_llm_provider"] = provider
        settings["workflow_llm_model"] = model
        save_settings_to_db(settings)
        return JSONResponse({"success": True})

    @router.get("/workflows/hermes-setup")
    async def get_hermes_setup():
        """Return Hermes readiness and ticket complexity routing for workflow onboarding."""
        from distr.core.settings import load_settings_from_db
        from distr.core.hermes import (
            ensure_hermes_tables,
            list_correction_attempts,
            list_events,
            list_project_runtime_sessions,
            list_validation_records,
        )
        from distr.core.project_cli_backends import get_backend_statuses

        settings = load_settings_from_db()
        ledger_ready = True
        ledger_error = ""
        try:
            ensure_hermes_tables()
        except Exception as exc:
            ledger_ready = False
            ledger_error = str(exc)

        routing = {}
        for level, default_backend, default_model in [
            ("low", "cursor", "auto"),
            ("medium", "codex", "auto"),
            ("high", "codex", "gpt-5.3-codex"),
        ]:
            routing[level] = {
                "backend": (settings.get(f"project_cli_{level}_backend") or default_backend).strip().lower(),
                "model": (settings.get(f"project_cli_{level}_model") or default_model).strip(),
            }

        try:
            accounts = json.loads(settings.get("connected_accounts") or "[]")
        except Exception:
            accounts = []
        connected_sources = sorted({
            (account.get("provider") or account.get("type") or account.get("service") or "").strip().lower()
            for account in accounts
            if isinstance(account, dict) and (account.get("provider") or account.get("type") or account.get("service"))
        })

        backends = get_backend_statuses(routing["medium"]["backend"])
        backend_rows = backends.get("backends") or []
        ready_backends = [
            row for row in backend_rows
            if row.get("id") in {routing[level]["backend"] for level in routing}
            and row.get("available", row.get("ready", False))
        ]

        readiness = [
            {
                "id": "ledger",
                "label": "Run ledger",
                "status": "ready" if ledger_ready else "blocked",
                "detail": "Event, runtime, validation, and correction tables are available." if ledger_ready else ledger_error,
            },
            {
                "id": "executor_routing",
                "label": "Complexity routing",
                "status": "ready" if all(routing[level]["backend"] and routing[level]["model"] for level in routing) else "needs_setup",
                "detail": "Low, medium, and high tickets have executor/model routes.",
            },
            {
                "id": "executors",
                "label": "Executor backends",
                "status": "ready" if ready_backends else "needs_setup",
                "detail": "Codex, Cursor, IDE, or other executors need to be installed and available.",
            },
        ]

        counts = {"events": 0, "runtime_sessions": 0, "validations": 0, "corrections": 0}
        if ledger_ready:
            try:
                counts = {
                    "events": len(list_events(limit=500)),
                    "runtime_sessions": len(list_project_runtime_sessions(limit=200)),
                    "validations": len(list_validation_records(limit=500)),
                    "corrections": len(list_correction_attempts(limit=500)),
                }
            except Exception:
                pass

        return JSONResponse({
            "enabled": bool(settings.get("hermes_enabled", True)),
            "memory_export_enabled": bool(settings.get("hermes_memory_export_enabled", False)),
            "routing": routing,
            "readiness": readiness,
            "counts": counts,
            "backends": backends,
            "connected_sources": connected_sources,
        })

    @router.post("/workflows/hermes-setup")
    async def save_hermes_setup(request: Request):
        """Save Hermes workflow onboarding settings."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        from distr.core.project_cli_backends import normalize_backend_id

        data = await request.json()
        settings = load_settings_from_db()
        settings["hermes_enabled"] = bool(data.get("enabled", True))
        if "memory_export_enabled" in data:
            settings["hermes_memory_export_enabled"] = bool(data.get("memory_export_enabled", False))

        models = data.get("models") or {}
        if "models" in data:
            for role in ["orchestrator", "validator", "correction"]:
                row = models.get(role) or {}
                settings[f"hermes_{role}_provider"] = (row.get("provider") or "").strip()
                settings[f"hermes_{role}_model"] = (row.get("model") or "").strip()

        routing = data.get("routing") or {}
        if "routing" in data:
            for level, default_backend, default_model in [
                ("low", "cursor", "auto"),
                ("medium", "codex", "auto"),
                ("high", "codex", "gpt-5.3-codex"),
            ]:
                row = routing.get(level) or {}
                settings[f"project_cli_{level}_backend"] = normalize_backend_id(row.get("backend") or default_backend)
                settings[f"project_cli_{level}_model"] = (row.get("model") or default_model).strip()

        # Keep the existing workflow LLM fallback aligned with Hermes orchestration
        # so older code paths still resolve to the same brain.
        orchestrator = models.get("orchestrator") or {}
        if "models" in data and (orchestrator.get("provider") or orchestrator.get("model")):
            settings["workflow_llm_provider"] = (orchestrator.get("provider") or "").strip()
            settings["workflow_llm_model"] = (orchestrator.get("model") or "").strip()

        save_settings_to_db(settings)
        return JSONResponse({"success": True})

    @router.get("/workflows/actions/catalog")
    async def get_workflow_actions_catalog():
        """Return saved Decisions Actions usable by workflow/Hermes steps."""
        from distr.core.db import get_session, Action
        from sqlalchemy import desc, nulls_last
        with get_session() as session:
            actions = session.query(Action).order_by(
                nulls_last(desc(Action.last_run_date)),
                desc(Action.modified_date),
            ).all()
            rows = []
            for action in actions:
                is_instruction = bool(action.is_instruction) if action.is_instruction is not None else False
                recording_filename = action.recording_filename or ""
                instruction_text = action.instruction_text or ""
                mode = "instruction" if is_instruction else "recording"
                usable = bool(instruction_text.strip()) if is_instruction else bool(recording_filename.strip())
                rows.append({
                    "id": action.id,
                    "title": action.title or f"Action #{action.id}",
                    "description": action.description or "",
                    "mode": mode,
                    "is_instruction": is_instruction,
                    "recording_filename": recording_filename,
                    "has_recording": bool(recording_filename.strip()),
                    "has_instruction": bool(instruction_text.strip()),
                    "usable": usable,
                    "last_run_date": action.last_run_date.isoformat() if action.last_run_date else None,
                })
            return JSONResponse(rows)

    @router.post("/workflows/seed-fixtures")
    async def seed_workflow_fixtures(data: WorkflowSeedFixturesRequest):
        """Seed workflow fixtures. Optional force reset for dynamic template updates."""
        try:
            from distr.core.db.seed_workflows import seed_workflows
            result = seed_workflows(force_reset=data.force_reset, workflow_names=data.workflow_names)
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Workflow fixture seed failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/generate-code")
    async def workflow_generate_step_code(step_id: int, data: WorkflowGenerateCodeRequest):
        """Generate code for a step from a natural-language instruction."""
        try:
            from distr.core.workflow.service import generate_step_code
            code = generate_step_code(step_id, data.instruction, data.step_type)
            return JSONResponse({"code": code})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        except RuntimeError as e:
            logger.error("Workflow generate-code LLM error: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)
        except Exception as e:
            logger.error("Workflow generate-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/test-code")
    async def workflow_test_step_code(step_id: int, data: WorkflowTestCodeRequest):
        """Execute code in an isolated subprocess with auto-fix loop."""
        try:
            from distr.core.workflow.service import test_step_code
            result = test_step_code(step_id, data.code, data.step_type, headless=data.headless)
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        except Exception as e:
            logger.error("Workflow test-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/validate")
    async def workflow_validate_step(step_id: int, data: WorkflowValidateStepRequest):
        """Validate step configuration against type-specific rules."""
        try:
            from distr.core.workflow.service import validate_step_config
            errors = validate_step_config(data.step_type, data.config)
            if errors:
                return JSONResponse({"errors": errors}, status_code=422)
            return JSONResponse({"valid": True})
        except Exception as e:
            logger.error("Workflow validate step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Presets / Export / Import (must be before {workflow_id} routes) ──

    @router.get("/workflows/presets")
    async def workflow_list_presets():
        try:
            from distr.core.workflow.service import list_presets
            return JSONResponse(list_presets())
        except Exception as e:
            logger.error("List presets failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/presets/{filename}/load")
    async def workflow_load_preset(filename: str):
        try:
            from distr.core.workflow.service import load_preset
            wf_id = load_preset(filename)
            if not wf_id:
                return JSONResponse({"detail": "Preset not found"}, status_code=404)
            from distr.core.workflow.service import get_workflow
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Load preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/import")
    async def workflow_import(file: UploadFile = File(...)):
        """Import a .dwf bundle or .json file."""
        try:
            raw = await file.read()
            fname = file.filename or ""
            if fname.endswith(".dwf"):
                from distr.core.workflow.service import import_workflow_bundle, get_workflow
                wf_id = import_workflow_bundle(raw)
            else:
                from distr.core.workflow.service import import_workflow, get_workflow
                data = json.loads(raw)
                wf_id = import_workflow(data)
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow import failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/generate")
    async def workflow_generate(data: WorkflowGenerateRequest):
        """Generate a workflow from a natural-language description using the coding LLM."""
        try:
            from distr.core.workflow_engine.code_generator import CodeGeneratorService
            from distr.core.workflow.service import import_workflow

            prompt = (
                "You are a workflow generator. Given the user's description, produce a JSON object "
                "representing a workflow compatible with the following schema:\n"
                "{\n"
                '  "name": "Workflow Name",\n'
                '  "description": "...",\n'
                '  "steps": [\n'
                "    {\n"
                '      "position": 0,\n'
                '      "name": "Step 1",\n'
                '      "action_type": "agent_instruction",\n'
                '      "instruction": "...",\n'
                '      "validation_type": "none",\n'
                '      "validation_prompt": "",\n'
                '      "routing_mode": "static",\n'
                '      "on_pass_goto_position": 1,\n'
                '      "on_fail_goto_position": null,\n'
                '      "wait_for_continue": false\n'
                "    }\n"
                "  ],\n"
                '  "context_rules": ""\n'
                "}\n\n"
                "Valid action_type values and when to use them:\n"
                '- "agent_instruction" — general-purpose desktop/UI automation (default for most tasks)\n'
                '- "playwright" — browser automation: navigate, login, fill forms, click, scrape, screenshot\n'
                '- "computer_use" — local vision-action loop for mechanical GUI tasks when browser automation is unavailable\n'
                '- "execute_code" — run a Python script (data processing, file I/O, computation)\n'
                '- "run_command" — execute a shell command (mkdir, cp, ls, app launch)\n'
                '- "http_request" — make an HTTP request (GET, POST, PUT, DELETE)\n'
                '- "play_recording" — replay a previously recorded macro\n\n'
                "Rules:\n"
                "- Use \"playwright\" for all web browser tasks.\n"
                "- Use \"computer_use\" for repetitive local GUI/screen-control tasks that need screenshots and sidecar actions.\n"
                "- Use \"agent_instruction\" for desktop app tasks and general automation.\n"
                "- Use \"execute_code\" for data/file processing.\n"
                "- The last step's on_pass_goto_position should be null (end workflow).\n"
                "- Return ONLY valid JSON, no markdown fences or explanations.\n\n"
                f"User description:\n{data.description}"
            )

            svc = CodeGeneratorService()
            raw_response = svc._call_coding_llm(prompt)

            # Strip markdown fences if present
            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[\w]*\s*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)
                cleaned = cleaned.strip()

            try:
                workflow_data = json.loads(cleaned)
            except json.JSONDecodeError as je:
                return JSONResponse(
                    {"detail": f"Failed to parse generated workflow JSON: {je}"},
                    status_code=422,
                )

            wf_id = import_workflow(workflow_data)
            return JSONResponse({"id": wf_id})
        except Exception as e:
            logger.error("Workflow generation failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/active-runs")
    async def workflow_active_runs(limit: int = 50, workflow_id: Optional[int] = None):
        try:
            from distr.core.workflow.service import get_active_runs
            return JSONResponse(get_active_runs(limit=limit, workflow_id=workflow_id))
        except Exception as e:
            logger.error("Workflow active runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}")
    async def workflow_get(workflow_id: int):
        try:
            from distr.core.workflow.service import get_workflow
            data = get_workflow(workflow_id)
            if not data:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(data)
        except Exception as e:
            logger.error("Workflow get failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}")
    async def workflow_update(workflow_id: int, data: WorkflowUpdateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import update_workflow
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if "run_settings" in updates:
                updates["run_settings"] = json.dumps(updates["run_settings"])
            if not update_workflow(workflow_id, **updates):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}")
    async def workflow_delete(workflow_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import delete_workflow
            if not delete_workflow(workflow_id):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/duplicate")
    async def workflow_duplicate(workflow_id: int):
        try:
            from distr.core.workflow.service import duplicate_workflow, get_workflow
            new_id = duplicate_workflow(workflow_id)
            if not new_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(get_workflow(new_id))
        except Exception as e:
            logger.error("Workflow duplicate failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/generate-steps")
    async def workflow_generate_steps(workflow_id: int, data: WorkflowGenerateStepsRequest):
        """Generate steps for an existing workflow using the LLM planner."""
        try:
            from distr.core.workflow.service import generate_steps
            from distr.gui.web.workflow_events import increment_workflow_updated
            steps = generate_steps(workflow_id, data.instruction)
            increment_workflow_updated()
            return JSONResponse({"steps": steps})
        except Exception as e:
            logger.error("Workflow generate-steps failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/schedule")
    async def workflow_update_schedule(workflow_id: int, data: WorkflowScheduleUpdate):
        """Update a workflow's schedule configuration."""
        try:
            from distr.core.workflow.service import update_workflow
            updates = {}
            if data.enabled is not None:
                updates["schedule_enabled"] = data.enabled
            if data.schedule is not None:
                updates["schedule_preset"] = data.schedule
            if data.schedule_time is not None:
                updates["schedule_time"] = data.schedule_time
            if data.schedule_days is not None:
                updates["schedule_days"] = data.schedule_days
            if data.timezone is not None:
                updates["schedule_timezone"] = data.timezone
            if not update_workflow(workflow_id, **updates):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update schedule failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/runs")
    async def workflow_runs(workflow_id: int, limit: int = 10):
        try:
            from distr.core.workflow.service import get_run_history
            return JSONResponse(get_run_history(workflow_id, limit=limit))
        except Exception as e:
            logger.error("Workflow runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/hermes-events")
    async def workflow_hermes_events(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None):
        try:
            from distr.core.hermes import list_events

            return JSONResponse(list_events(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow Hermes events failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/validations")
    async def workflow_validations(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, verdict: Optional[str] = None):
        try:
            from distr.core.hermes import list_validation_records

            return JSONResponse(list_validation_records(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                verdict=verdict,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow validation records failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/corrections")
    async def workflow_corrections(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, validation_record_id: Optional[int] = None):
        try:
            from distr.core.hermes import list_correction_attempts

            return JSONResponse(list_correction_attempts(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                validation_record_id=validation_record_id,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow correction attempts failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/runs")
    async def workflow_clear_runs(workflow_id: int):
        """Clear this workflow's completed run history without touching other logs."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import clear_workflow_history
            result = clear_workflow_history(workflow_id)
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "clear_audit"), status_code=404)
            result.update(_workflow_feedback_message("clear_audit", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow clear runs failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_audit"), status_code=500)

    @router.delete("/workflows/{workflow_id}/events")
    async def workflow_clear_events(workflow_id: int):
        """Clear orchestration events for this workflow only."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.hermes import HermesEvent
            from distr.core.db.workflow import AutoWorkflow
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                wf = db.query(AutoWorkflow.id).filter(AutoWorkflow.id == workflow_id).first()
                if not wf:
                    return JSONResponse(_workflow_error_payload("Workflow not found", "clear_events"), status_code=404)
                deleted_events = (
                    db.query(HermesEvent)
                    .filter(HermesEvent.workflow_id == workflow_id)
                    .delete(synchronize_session=False)
                )
                db.commit()
            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_events": deleted_events,
                **_workflow_feedback_message("clear_events", {"deleted_events": deleted_events}),
            })
        except Exception as e:
            logger.error("Workflow clear events failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_events"), status_code=500)

    @router.delete("/workflows/{workflow_id}/executor-sessions")
    async def workflow_clear_executor_sessions(workflow_id: int):
        """Clear CLI/IDE execution sessions for this workflow only."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
            from distr.core.db.workflow import AutoWorkflow
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                wf = db.query(AutoWorkflow.id).filter(AutoWorkflow.id == workflow_id).first()
                if not wf:
                    return JSONResponse(_workflow_error_payload("Workflow not found", "clear_executor"), status_code=404)
                session_ids = [
                    row[0]
                    for row in (
                        db.query(ProjectExecutionSession.id)
                        .filter(ProjectExecutionSession.workflow_id == workflow_id)
                        .all()
                    )
                ]
                deleted_events = 0
                deleted_sessions = 0
                if session_ids:
                    deleted_events = (
                        db.query(ProjectExecutionEvent)
                        .filter(ProjectExecutionEvent.session_id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                    deleted_sessions = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                db.commit()
            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_sessions": deleted_sessions,
                "deleted_events": deleted_events,
                **_workflow_feedback_message("clear_executor", {"deleted_sessions": deleted_sessions}),
            })
        except Exception as e:
            logger.error("Workflow clear executor sessions failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_executor"), status_code=500)

    # Execution
    @router.post("/workflows/{workflow_id}/run")
    async def workflow_run(workflow_id: int, request: Request):
        """Start a workflow run. Accepts optional { "start_step_id": int }."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.dispatcher import start_workflow_run
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            start_step_id = body.get("start_step_id") if isinstance(body, dict) else None
            result = start_workflow_run(workflow_id, start_step_id=start_step_id)
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "run"), status_code=400)
            result.update(_workflow_feedback_message("run_started", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "run"), status_code=500)

    @router.post("/workflows/{workflow_id}/cancel-run/{run_id}")
    async def workflow_cancel_run(workflow_id: int, run_id: int):
        try:
            from distr.core.workflow.dispatcher import cancel_run
            if not cancel_run(run_id):
                return JSONResponse(_workflow_error_payload("Run not found", "cancel"), status_code=404)
            return JSONResponse({"success": True, **_workflow_feedback_message("cancelled")})
        except Exception as e:
            logger.error("Workflow cancel run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "cancel"), status_code=500)

    @router.post("/workflows/{workflow_id}/stop-reset")
    async def workflow_stop_reset(workflow_id: int):
        """Cancel active run (if any) and reset all step statuses/results."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import reset_workflow_steps
            result = reset_workflow_steps(workflow_id)
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "reset"), status_code=404)
            result.update(_workflow_feedback_message("reset", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow stop-reset failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "reset"), status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/continue")
    async def workflow_continue_run(workflow_id: int, run_id: int, request: Request):
        """Resume a waiting workflow run.

        Accepts optional JSON body. Preferred field is ``input``, but callback-style
        payloads using ``response``, ``message``, ``result``, or ``output`` are
        also accepted for compatibility.
        """
        try:
            from distr.core.workflow.dispatcher import continue_waiting_step
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            optional_input = ""
            if isinstance(body, dict):
                optional_input = (
                    body.get("input")
                    or body.get("response")
                    or body.get("message")
                    or body.get("result")
                    or body.get("output")
                    or ""
                )
            optional_input = str(optional_input or "")
            result = continue_waiting_step(run_id, optional_input)
            if "error" in result:
                status_code = result.get("status_code", 400)
                return JSONResponse(_workflow_error_payload(result["error"], "continue"), status_code=status_code)
            result.update(_workflow_feedback_message("continued", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow continue run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "continue"), status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/codex-events")
    async def workflow_codex_bridge_event(workflow_id: int, run_id: int, event: CodexBridgeEventRequest):
        """Record Codex IDE/plugin steering and execution events into Decisions/Hermes.

        This endpoint is intentionally not limited to waiting runs. Codex may report
        mid-run steering, interruption, progress, or completion while the workflow is
        still running, waiting, or already terminal.
        """
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.kanban.project_execution import append_execution_event
            from distr.core.workflow.standards_memory import capture_feedback_as_standard
            from distr.core.hermes import emit_event
            from distr.gui.web.workflow_events import increment_workflow_updated

            event_type = (event.event_type or "codex_event").strip() or "codex_event"
            status = (event.status or "").strip() or None
            message = (event.message or event.input or event.output or "").strip()
            payload = dict(event.payload or {})
            if event.input:
                payload["input"] = event.input
            if event.output:
                payload["output"] = event.output
            payload.setdefault("bridge", "codex")

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "codex_event"), status_code=404)
                run_data = {}
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                step_id = event.step_id or run.current_step_id
                ticket_id = event.ticket_id or run.ticket_id
                board_id = run.board_id
                project_id = event.project_id or run_data.get("project_id")
                execution_session_id = event.execution_session_id or payload.get("execution_session_id")

                history = run_data.get("codex_bridge_events") or []
                history.append({
                    "event_type": event_type,
                    "status": status,
                    "message": message,
                    "step_id": step_id,
                    "ticket_id": ticket_id,
                    "project_id": project_id,
                    "execution_session_id": execution_session_id,
                    "ts": time.time(),
                })
                run_data["codex_bridge_events"] = history[-50:]
                if event_type in {"user_steer", "codex_interrupted", "codex_waiting", "codex_needs_input"}:
                    run_data["last_codex_bridge_state"] = {
                        "event_type": event_type,
                        "status": status,
                        "message": message,
                        "step_id": step_id,
                        "execution_session_id": execution_session_id,
                    }
                run.run_data = json.dumps(run_data)
                db.commit()

            append_execution_event(
                int(execution_session_id) if execution_session_id else None,
                event_type,
                status=status,
                message=message,
                payload=payload,
            )

            hermes_event_id = emit_event(
                source="codex",
                event_type=event_type,
                status=status,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=int(step_id) if step_id else None,
                ticket_id=int(ticket_id) if ticket_id else None,
                board_id=int(board_id) if board_id else None,
                project_id=int(project_id) if str(project_id or "").isdigit() else None,
                execution_session_id=int(execution_session_id) if execution_session_id else None,
                summary=message or f"Codex bridge event: {event_type}",
                payload=payload,
                evidence=event.evidence or {},
            )

            captured_standard = False
            if event_type in {"user_steer", "codex_interrupted", "codex_needs_input"} and message:
                captured_standard = capture_feedback_as_standard(workflow_id, message)

            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "event_type": event_type,
                "hermes_event_id": hermes_event_id,
                "captured_standard": captured_standard,
            })
        except Exception as e:
            logger.error("Workflow Codex bridge event failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "codex_event"), status_code=500)

    @router.get("/workflows/{workflow_id}/active-run")
    async def workflow_active_run(workflow_id: int):
        try:
            from distr.core.workflow.service import get_active_run
            run = get_active_run(workflow_id)
            return JSONResponse(run or {
                "active": False,
                "message": "No active run for this workflow.",
                "next_action": "Start the workflow or open run history for previous results.",
            })
        except Exception as e:
            logger.error("Workflow active run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "status"), status_code=500)

    # Context items (structured agent context snippets)
    @router.get("/workflows/{workflow_id}/context-items")
    async def workflow_context_items(workflow_id: int):
        try:
            from distr.core.workflow.service import get_context_items
            return JSONResponse(get_context_items(workflow_id))
        except Exception as e:
            logger.error("Workflow context items failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/context-items")
    async def workflow_add_context_item(workflow_id: int, data: ContextItemCreateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import add_context_item
            item_id = add_context_item(workflow_id, title=data.title, content=data.content, notes=data.notes)
            if not item_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"id": item_id, "success": True})
        except Exception as e:
            logger.error("Workflow add context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/context-items/{context_item_id}")
    async def workflow_update_context_item(workflow_id: int, context_item_id: int, data: ContextItemUpdateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import update_context_item
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if not update_context_item(context_item_id, workflow_id=workflow_id, **updates):
                return JSONResponse({"detail": "Context item not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/context-items/{context_item_id}")
    async def workflow_delete_context_item(workflow_id: int, context_item_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import delete_context_item
            if not delete_context_item(context_item_id, workflow_id=workflow_id):
                return JSONResponse({"detail": "Context item not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Steps
    @router.post("/workflows/{workflow_id}/steps")
    async def workflow_add_step(workflow_id: int, data: StepCreateRequest):
        try:
            from distr.core.workflow.service import add_step, get_workflow
            step_id = add_step(workflow_id, name=data.name, action_type=data.action_type, position=data.position)
            if not step_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(get_workflow(workflow_id))
        except Exception as e:
            logger.error("Workflow add step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/steps/reorder")
    async def workflow_reorder_steps(workflow_id: int, data: StepReorderRequest):
        try:
            from distr.core.workflow.service import reorder_steps
            reorder_steps(workflow_id, data.step_ids)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow reorder steps failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/steps/{step_id}")
    async def workflow_update_step(workflow_id: int, step_id: int, request: Request):
        try:
            from distr.core.workflow.service import update_step
            body = await request.json()
            if not update_step(step_id, **body):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/steps/{step_id}")
    async def workflow_delete_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.service import delete_step
            if not delete_step(step_id, workflow_id=workflow_id):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step execution (path-param route)
    @router.post("/workflows/{workflow_id}/steps/{step_id}/execute")
    async def workflow_execute_step(workflow_id: int, step_id: int):
        """Execute a single step in isolation.

        Runs the step in a background thread so the LLM call doesn't block
        the uvicorn event loop (which would hang the entire server). Returns
        immediately; the UI polls / soft-refreshes to see the result.
        """
        import asyncio

        # Server-side debounce/idempotency guard for double-clicks or duplicate listeners.
        now = time.time()
        with _isolated_step_exec_lock:
            last_started = _isolated_step_exec_started_at.get(step_id, 0.0)
            if now - last_started < 1.5:
                logger.info(
                    "Workflow step execute deduped: workflow_id=%s step_id=%s delta=%.3fs",
                    workflow_id,
                    step_id,
                    now - last_started,
                )
                return JSONResponse({"success": True, "message": "Step execution already in progress."})
            _isolated_step_exec_started_at[step_id] = now

        def _run():
            try:
                from distr.core.workflow.dispatcher import StepDispatcher
                dispatcher = StepDispatcher()
                logger.info(
                    "Workflow step execute started: workflow_id=%s step_id=%s",
                    workflow_id,
                    step_id,
                )
                dispatcher.run_isolated(step_id)
                logger.info(
                    "Workflow step execute finished: workflow_id=%s step_id=%s",
                    workflow_id,
                    step_id,
                )
            except Exception as exc:
                logger.error("Background step execution failed for step %s: %s", step_id, exc, exc_info=True)
            finally:
                # Keep timestamp for a short debounce window only.
                try:
                    with _isolated_step_exec_lock:
                        started = _isolated_step_exec_started_at.get(step_id, 0.0)
                        if started and (time.time() - started) > 10.0:
                            _isolated_step_exec_started_at.pop(step_id, None)
                except Exception:
                    pass

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run)
        return JSONResponse({"success": True, "message": "Step execution started."})

    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop")
    async def workflow_stop_step(workflow_id: int, step_id: int):
        """Stop a running/waiting step without cancelling the full run."""
        try:
            from distr.core.workflow.dispatcher import cancel_step

            # Stop any recording playback via the action playback service
            try:
                from distr.core.signals import signal_manager
                svc = getattr(signal_manager, 'action_playback_service', None)
                if svc is not None:
                    svc.stop_action()
            except Exception:
                pass

            # Stop TTS and player if a step is being stopped
            try:
                from distr.core.signals import signal_manager
                signal_manager.interrupt_tts.emit()
                signal_manager.player_stop.emit()
            except Exception:
                pass

            # Cancel the step itself
            cancel_step(step_id)

            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow stop step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/cancel")
    async def workflow_cancel_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.dispatcher import cancel_step
            if not cancel_step(step_id):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow cancel step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/complete")
    async def workflow_complete_step(workflow_id: int, step_id: int, request: Request):
        """Mark step complete with result. Body: {result: str, passed: bool}"""
        try:
            from distr.core.workflow.router import StepRouter
            from distr.core.db import get_session as _get_session
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep as _Step
            body = await request.json()
            result_text = body.get("result", "")
            passed = body.get("passed", True)
            # Find the active run for this step (if any)
            run_id = None
            with _get_session() as db:
                step = db.query(_Step).filter(_Step.id == step_id).first()
                if not step:
                    return JSONResponse({"detail": "Step not found"}, status_code=404)
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    run_id = run.id
            if run_id is not None:
                router = StepRouter()
                res = router.route(step_id, result_text, passed, run_id)
            else:
                # Isolated step — just record the result
                from distr.core.workflow.service import update_step
                update_step(step_id, status="passed" if passed else "failed", result=result_text)
                res = {"done": True, "status": "passed" if passed else "failed"}
            return JSONResponse(res)
        except Exception as e:
            logger.error("Workflow complete step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Screenshot upload for validation
    @router.post("/workflows/{workflow_id}/steps/{step_id}/screenshot")
    async def workflow_upload_screenshot(workflow_id: int, step_id: int, file: UploadFile = File(...)):
        try:
            from distr.core.workflow.service import save_screenshot
            data = await file.read()
            path = save_screenshot(step_id, data, file.filename or "screenshot.png")
            if not path:
                return JSONResponse({"detail": "Failed to save screenshot"}, status_code=500)
            return JSONResponse({"success": True, "path": path})
        except Exception as e:
            logger.error("Workflow screenshot upload failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step result history
    @router.get("/workflows/{workflow_id}/steps/{step_id}/results")
    async def workflow_step_results(workflow_id: int, step_id: int, limit: int = 20):
        try:
            from distr.core.workflow.service import get_step_results
            return JSONResponse(get_step_results(step_id, limit=limit))
        except Exception as e:
            logger.error("Workflow step results failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/steps/{step_id}/results")
    async def workflow_clear_step_results(workflow_id: int, step_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import clear_step_results
            result = clear_step_results(step_id)
            if "error" in result:
                return JSONResponse({"detail": result["error"]}, status_code=404)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow clear step results failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step recording
    @router.post("/workflows/{workflow_id}/steps/{step_id}/start-recording")
    async def workflow_start_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.core.signals import signal_manager
            signal_manager.start_step_recording.emit(step_id)
            return JSONResponse({"success": True, "message": "Recording countdown started"})
        except Exception as e:
            logger.error("Start step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop-recording")
    async def workflow_stop_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.core.signals import signal_manager
            signal_manager.stop_step_recording.emit()

            # Poll briefly for the recording to be saved to the database.
            # The signal handler saves asynchronously, so we wait up to ~2s.
            import asyncio
            from distr.core.workflow.service import get_workflow
            for _ in range(10):
                await asyncio.sleep(0.2)
                wf = get_workflow(workflow_id)
                if wf:
                    s = next((s for s in wf.get("steps", []) if s["id"] == step_id), None)
                    if s and s.get("recording_filename"):
                        return JSONResponse({"success": True, "message": "Recording stopped", "recording_filename": s["recording_filename"]})

            # Timed out waiting, but the signal was sent — return success anyway
            return JSONResponse({"success": True, "message": "Recording stopped"})
        except Exception as e:
            logger.error("Stop step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/play-recording")
    async def workflow_play_step_recording(workflow_id: int, step_id: int):
        """Play the recorded action for a step."""
        try:
            from distr.core.workflow.service import get_workflow
            from distr.core.signals import signal_manager
            wf = get_workflow(workflow_id)
            if not wf:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            step = next((s for s in wf.get("steps", []) if s["id"] == step_id), None)
            if not step:
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            rec = step.get("recording_filename", "")
            if not rec:
                return JSONResponse({"detail": "No recording for this step"}, status_code=400)
            signal_manager.play_recording_file.emit(rec)
            return JSONResponse({"success": True, "message": "Playing recording"})
        except Exception as e:
            logger.error("Play step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Export ──

    @router.get("/workflows/{workflow_id}/export")
    async def workflow_export(workflow_id: int):
        """Download a .dwf bundle (ZIP with recordings + screenshots)."""
        try:
            from distr.core.workflow.service import export_workflow_bundle, export_workflow
            bundle = export_workflow_bundle(workflow_id)
            if not bundle:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            # Get workflow name for the filename
            data = export_workflow(workflow_id)
            import re
            safe_name = re.sub(r'[^a-z0-9_]', '', (data.get("name", "workflow") or "workflow").lower().replace(" ", "_"))
            from starlette.responses import Response
            return Response(
                content=bundle,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}.dwf"'}
            )
        except Exception as e:
            logger.error("Workflow export failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/export-preset")
    async def workflow_export_preset(workflow_id: int):
        try:
            from distr.core.workflow.service import save_preset
            filename = save_preset(workflow_id)
            if not filename:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True, "filename": filename})
        except Exception as e:
            logger.error("Workflow export preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)
