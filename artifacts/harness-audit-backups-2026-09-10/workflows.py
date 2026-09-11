"""Workflows HTTP boundary. Existing URLs remain compatible."""
from fastapi import Request, File, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from typing import Optional
import json
import re
import time
import asyncio
import logging

logger = logging.getLogger(__name__)
from .models import ContextItemCreateRequest, ContextItemUpdateRequest, LoopPresetApplyRequest, LoopPresetSaveRequest, ProjectOpsExecuteRequest, ProjectOpsPlanRequest, StepCreateRequest, StepReorderRequest, VisualBaselineRequest, WorkflowCreateRequest, WorkflowGenerateRequest, WorkflowGenerateStepsRequest, WorkflowOrderRequest, WorkflowPlanRequest, WorkflowPurgeAllRequest, WorkflowScheduleUpdate, WorkflowSeedFixturesRequest, WorkflowUpdateRequest, WorkflowValidateStepRequest
from ._workflow_support import _is_audit_workflow, _isolated_step_exec_lock, _isolated_step_exec_started_at, _workflow_error_payload, _workflow_feedback_message, _workflows_websocket_handler


def register_routes(router, templates):
    @router.get("/workflows")
    async def workflow_list(limit: int = 50, search: Optional[str] = None, type: Optional[str] = None):
        try:
            from distr.core.workflow.service import list_workflows
            rows = await asyncio.to_thread(list_workflows, limit=limit, search=search, workflow_type=type)
            return JSONResponse(rows)
        except Exception as e:
            logger.error("Workflow list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/skills")
    async def workflow_skills_catalog(source: Optional[str] = None, limit: int = 200):
        """Bundled skills registry for workflow skill chains and orchestrator transfer."""
        try:
            from distr.core.skills.catalog import load_registry

            rows = load_registry()
            if source:
                src = source.strip().lower()
                rows = tuple(r for r in rows if str(r.get("source") or "").lower() == src)
            out = []
            for row in rows[: max(1, min(int(limit or 200), 500))]:
                out.append(
                    {
                        "id": row.get("id"),
                        "name": row.get("name") or row.get("id"),
                        "description": row.get("description") or "",
                        "source": row.get("source") or "bundled",
                        "tags": row.get("tags") or [],
                    }
                )
            return JSONResponse({"skills": out, "count": len(out)})
        except Exception as e:
            logger.error("Workflow skills catalog failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows")
    async def workflow_create(data: WorkflowCreateRequest):
        try:
            from distr.core.workflow.service import create_workflow, get_workflow
            kwargs = {"name": data.name, "description": data.description}
            if data.workflow_type is not None:
                kwargs["workflow_type"] = data.workflow_type
            wf_id = create_workflow(**kwargs)
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/order")
    async def workflow_order_update(data: WorkflowOrderRequest):
        try:
            from distr.core.workflow.service import update_workflow_order

            ok = update_workflow_order(data.workflow_ids)
            if not ok:
                return JSONResponse({"detail": "Workflow order update failed."}, status_code=400)
            return JSONResponse({"success": True, "workflow_ids": data.workflow_ids})
        except Exception as e:
            logger.error("Workflow order update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/visual-baselines")
    async def workflow_visual_baseline_create(data: VisualBaselineRequest):
        """Create a named visual baseline set for UI quality validation."""
        try:
            from distr.core.orchestrator import create_visual_baseline_set, get_visual_baseline_set

            baseline_id = create_visual_baseline_set(
                name=data.name,
                board_id=data.board_id,
                project_id=data.project_id,
                description=data.description,
                version=data.version,
                screens=[screen.model_dump() for screen in data.screens],
                copy_screenshots=data.store_copy,
            )
            baseline = get_visual_baseline_set(baseline_set_id=baseline_id)
            return JSONResponse({"success": True, "visual_baseline": baseline})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow visual baseline create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/visual-baselines")
    async def workflow_visual_baseline_list(
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        include_global: bool = False,
        limit: int = 50,
    ):
        """List named visual baselines for board/project UI validation."""
        try:
            from distr.core.orchestrator import list_visual_baseline_sets

            baselines = list_visual_baseline_sets(
                board_id=board_id,
                project_id=project_id,
                include_global=include_global,
                limit=limit,
            )
            return JSONResponse({"success": True, "visual_baselines": baselines})
        except Exception as e:
            logger.error("Workflow visual baseline list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/visual-baselines/readiness")
    async def workflow_visual_baseline_readiness(
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        baseline_set_id: Optional[int] = None,
        name: Optional[str] = None,
        include_global: bool = False,
        limit: int = 50,
    ):
        """Check whether visual baseline screenshot files are present on disk."""
        try:
            from distr.core.orchestrator import inspect_visual_baseline_readiness

            readiness = inspect_visual_baseline_readiness(
                board_id=board_id,
                project_id=project_id,
                baseline_set_id=baseline_set_id,
                name=name,
                include_global=include_global,
                limit=limit,
            )
            return JSONResponse({"success": True, "visual_baseline_readiness": readiness})
        except Exception as e:
            logger.error("Workflow visual baseline readiness failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/visual-baselines/{baseline_set_id}")
    async def workflow_visual_baseline_get(baseline_set_id: int):
        """Return one named visual baseline set and its reference screens."""
        try:
            from distr.core.orchestrator import get_visual_baseline_set

            baseline = get_visual_baseline_set(baseline_set_id=baseline_set_id)
            if not baseline:
                return JSONResponse({"detail": "Visual baseline not found"}, status_code=404)
            return JSONResponse({"success": True, "visual_baseline": baseline})
        except Exception as e:
            logger.error("Workflow visual baseline get failed: %s", e, exc_info=True)
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


    @router.post("/workflows/plan")
    async def workflow_plan(data: WorkflowPlanRequest):
        """Plan a workflow from a natural-language instruction."""
        try:
            from distr.core.workflow.service import plan_workflow, get_workflow
            from distr.gui.web.workflow_events import increment_workflow_updated
            wf_id = plan_workflow(data.instruction, chat_id=data.chat_id, name=data.name)
            if not wf_id:
                return JSONResponse({"detail": "Failed to plan workflow"}, status_code=500)
            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/{workflow_id}/project-context")
    async def workflow_project_context(workflow_id: int, board_id: Optional[int] = None):
        """Return active project, board, queue, and execution status for the ops harness."""
        try:
            from distr.core.workflow.project_ops import gather_project_ops_context

            return JSONResponse(gather_project_ops_context(workflow_id=workflow_id, board_id=board_id))
        except Exception as e:
            logger.error("Workflow project context failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/ops/plan")
    async def workflow_project_ops_plan(workflow_id: int, data: ProjectOpsPlanRequest):
        """Classify a project outcome instruction and return a short execution plan."""
        try:
            from distr.core.workflow.project_ops import (
                build_execution_plan,
                classify_project_instruction,
                gather_project_ops_context,
                suggest_skills_for_route,
            )

            context = gather_project_ops_context(workflow_id=workflow_id, board_id=data.board_id)
            classification = classify_project_instruction(data.instruction)
            route = classification.get("route") or "clarification"
            context["skills_hint"] = suggest_skills_for_route(route, data.instruction)
            plan = build_execution_plan(
                data.instruction,
                route=route,
                classification=classification,
                context=context,
            )
            return JSONResponse(plan)
        except Exception as e:
            logger.error("Workflow project ops plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/ops/execute")
    async def workflow_project_ops_execute(workflow_id: int, data: ProjectOpsExecuteRequest):
        """Execute an approved project operations plan."""
        if not data.approved:
            return JSONResponse({"detail": "Approval is required before execution."}, status_code=400)
        try:
            from distr.core.workflow.project_ops import execute_project_ops_plan

            result = execute_project_ops_plan(
                workflow_id=workflow_id,
                instruction=data.instruction,
                route=data.route,
                board_id=data.board_id,
                ticket_id=data.ticket_id,
            )
            status_code = 200
            if result.get("status") == "failed":
                status_code = 400
            elif result.get("status") == "needs_input":
                status_code = 422
            return JSONResponse(result, status_code=status_code)
        except Exception as e:
            logger.error("Workflow project ops execute failed: %s", e, exc_info=True)
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


    @router.get("/workflows/actions/catalog")
    async def get_workflow_actions_catalog():
        """Return saved Decisions Actions usable by workflow/orchestrator steps."""
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


    @router.get("/workflows/presets")
    async def workflow_list_presets():
        try:
            from distr.core.workflow.service import list_presets
            return JSONResponse(list_presets())
        except Exception as e:
            logger.error("List presets failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/loop-presets")
    async def workflow_loop_presets():
        from distr.core.workflow.loop_presets import list_loop_presets

        return JSONResponse({"presets": list_loop_presets()})


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


    @router.get("/workflows/blueprint/checklist")
    async def workflow_blueprint_checklist():
        """Return the durable agent-system blueprint adherence checklist."""
        try:
            from distr.core.workflow.blueprint_adherence import checklist_snapshot

            return JSONResponse({"success": True, **checklist_snapshot()})
        except Exception as e:
            logger.error("Workflow blueprint checklist failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/blueprint/evals")
    async def workflow_blueprint_evals():
        """Run the standing outcome eval pack for Development."""
        try:
            from distr.core.workflow.blueprint_eval_pack import run_blueprint_eval_pack

            return JSONResponse({"success": True, **await asyncio.to_thread(run_blueprint_eval_pack)})
        except Exception as e:
            logger.error("Workflow blueprint evals failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/{workflow_id}")
    async def workflow_get(workflow_id: int):
        try:
            from distr.core.workflow.service import get_workflow
            data = await asyncio.to_thread(get_workflow, workflow_id)
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
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
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


    @router.get("/workflows/{workflow_id}/validations")
    async def workflow_validations(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, verdict: Optional[str] = None):
        try:
            from distr.core.orchestrator import list_validation_records

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
    async def workflow_corrections(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, validation_record_id: Optional[int] = None, status: Optional[str] = None):
        try:
            from distr.core.orchestrator import list_correction_attempts

            return JSONResponse(list_correction_attempts(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                validation_record_id=validation_record_id,
                status=status,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow correction attempts failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.delete("/workflows/{workflow_id}/events")
    async def workflow_clear_events(workflow_id: int):
        """Clear orchestration events for this workflow only."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.orchestrator import OrchestratorEvent
            from distr.core.db.workflow import AutoWorkflow
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                wf = db.query(AutoWorkflow.id).filter(AutoWorkflow.id == workflow_id).first()
                if not wf:
                    return JSONResponse(_workflow_error_payload("Workflow not found", "clear_events"), status_code=404)
                deleted_events = (
                    db.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.workflow_id == workflow_id)
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


    @router.post("/workflows/{workflow_id}/runs/{run_id}/provider-model-selection")
    async def workflow_provider_model_selection(workflow_id: int, run_id: int, request: Request):
        """Readiness-check a selected free model before retrying a waiting step."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            candidate_index = int(body.get("candidate_index", 0))
        except (TypeError, ValueError):
            return JSONResponse({"detail": "candidate_index must be an integer"}, status_code=400)
        try:
            from distr.core.workflow.service import apply_run_provider_model_selection

            result = apply_run_provider_model_selection(run_id, candidate_index)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow provider model selection failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


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


    @router.post("/workflows/{workflow_id}/apply-loop-preset")
    async def workflow_apply_loop_preset(workflow_id: int, data: LoopPresetApplyRequest):
        try:
            from distr.core.workflow.loop_presets import apply_loop_preset

            result = apply_loop_preset(workflow_id, data.preset_name, mode=data.mode)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            from distr.core.workflow.service import get_workflow

            return JSONResponse({"success": True, **result, "workflow": get_workflow(workflow_id)})
        except Exception as e:
            logger.error("Workflow apply loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/{workflow_id}/export-loop-preset")
    async def workflow_export_loop_preset(workflow_id: int):
        """Download current loop steps as a loop preset JSON bundle."""
        try:
            import re

            from distr.core.workflow.loop_presets import export_loop_preset_json
            from starlette.responses import Response

            bundle = export_loop_preset_json(workflow_id)
            if not bundle:
                return JSONResponse(
                    {"detail": "Workflow not found or has no steps to export"},
                    status_code=404,
                )
            safe_name = re.sub(
                r"[^a-z0-9_-]+",
                "-",
                str(bundle.get("slug") or bundle.get("name") or "loop").lower(),
            ).strip("-") or "loop"
            payload = json.dumps(bundle, indent=2, ensure_ascii=False)
            return Response(
                content=payload.encode("utf-8"),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}.loop-preset.json"'
                },
            )
        except Exception as e:
            logger.error("Workflow export loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/import-loop-preset")
    async def workflow_import_loop_preset(
        workflow_id: int,
        file: UploadFile = File(...),
        mode: str = "replace",
    ):
        """Import a loop preset JSON file into the current workflow."""
        try:
            from distr.core.workflow.loop_presets import import_loop_preset_json
            from distr.core.workflow.service import get_workflow

            raw = await file.read()
            try:
                bundle_data = json.loads(raw.decode("utf-8"))
            except Exception:
                return JSONResponse({"detail": "Invalid JSON file"}, status_code=400)

            result = import_loop_preset_json(workflow_id, bundle_data, mode=mode)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Import failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse({"success": True, **result, "workflow": get_workflow(workflow_id)})
        except Exception as e:
            logger.error("Workflow import loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/save-loop-preset")
    async def workflow_save_loop_preset(workflow_id: int, data: LoopPresetSaveRequest):
        """Save current workflow steps as a reusable user loop preset."""
        try:
            from distr.core.workflow.loop_presets import save_loop_preset_from_workflow

            result = save_loop_preset_from_workflow(workflow_id, data.name)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Save failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Workflow save loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/steps")
    async def workflow_add_step(workflow_id: int, data: StepCreateRequest):
        try:
            from distr.core.workflow.service import add_step, get_workflow
            step_id = add_step(
                workflow_id,
                name=data.name,
                action_type=data.action_type,
                position=data.position,
                instruction=data.instruction,
                config=data.config,
                validation_type=data.validation_type,
                validation_prompt=data.validation_prompt,
                wait_for_continue=data.wait_for_continue,
            )
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


    @router.websocket("/workflows/ws")
    async def workflows_websocket(websocket: WebSocket):
        await _workflows_websocket_handler(websocket)


    @router.websocket("/ws/workflows")
    async def workflows_websocket_legacy(websocket: WebSocket):
        await _workflows_websocket_handler(websocket)


