"""Workflow execution HTTP boundary. Existing URLs remain compatible."""
from fastapi import Request
from fastapi.responses import JSONResponse
from typing import Optional
import json
import time
import asyncio
import logging

logger = logging.getLogger(__name__)
from .models import CodexBridgeEventRequest, UiFeedbackRequest, WorkflowTicketGroupRunRequest
from ._workflow_support import _is_audit_workflow, _needs_input_context_and_spoken, _workflow_error_payload, _workflow_feedback_message


def register_routes(router, templates):
    @router.get("/workflows/orchestrator-setup")
    async def get_orchestrator_setup():
        """Return Orchestrator readiness and ticket complexity routing for workflow onboarding."""
        from distr.core.settings import load_settings_from_db
        from distr.core.orchestrator import (
            ensure_orchestrator_tables,
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
            ensure_orchestrator_tables()
        except Exception as exc:
            ledger_ready = False
            ledger_error = str(exc)

        routing = {}
        from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed

        for level, default_backend, default_model in [
            ("low", "cursor", "auto"),
            ("medium", "codex", "auto"),
            ("high", "codex", "gpt-5.3-codex"),
        ]:
            backend = (settings.get(f"project_cli_{level}_backend") or default_backend).strip().lower()
            routing[level] = {
                "backend": backend,
                "model": (settings.get(f"project_cli_{level}_model") or default_model).strip(),
                "model_provider": (settings.get(f"project_cli_{level}_model_provider") or "").strip().lower(),
                "codex_intelligence": normalize_codex_intelligence(
                    settings.get(f"project_cli_{level}_codex_intelligence")
                ),
                "codex_speed": normalize_codex_speed(settings.get(f"project_cli_{level}_codex_speed")),
                "fallback_backend": (settings.get(f"project_cli_{level}_fallback_backend") or "").strip().lower(),
                "fallback_model": (settings.get(f"project_cli_{level}_fallback_model") or "").strip(),
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

        hermes_agent = next(
            (item for item in (backends.get("backends") or []) if item.get("id") == "hermes_agent"),
            None,
        )

        return JSONResponse({
            "enabled": bool(settings.get("orchestrator_enabled", True)),
            "memory_export_enabled": bool(settings.get("orchestrator_memory_export_enabled", False)),
            "routing": routing,
            "readiness": readiness,
            "counts": counts,
            "backends": backends,
            "connected_sources": connected_sources,
            "optional_backends": {
                "hermes_agent": {
                    "installed": bool(hermes_agent and hermes_agent.get("installed")),
                    "ready": bool(hermes_agent and hermes_agent.get("ready")),
                    "message": (hermes_agent or {}).get("message") or "",
                    "setup_command": "NONINTERACTIVE=1 bash scripts/setup_project_clis.sh hermes-agent",
                    "docs": "docs/nous-hermes-agent.md",
                    "required_for_orchestrator": False,
                }
            },
        })


    @router.post("/workflows/orchestrator-setup")
    async def save_orchestrator_setup(request: Request):
        """Save Orchestrator workflow onboarding settings."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        from distr.core.project_cli_backends import normalize_backend_id
        from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed

        data = await request.json()
        settings = load_settings_from_db()
        settings["orchestrator_enabled"] = bool(data.get("enabled", True))
        if "memory_export_enabled" in data:
            settings["orchestrator_memory_export_enabled"] = bool(data.get("memory_export_enabled", False))

        models = data.get("models") or {}
        if "models" in data:
            from distr.core.orchestrator import ORCHESTRATOR_ROLE_SETTINGS_KEYS

            for role in ["orchestrator", "validator", "correction"]:
                row = models.get(role) or {}
                provider_key, model_key = ORCHESTRATOR_ROLE_SETTINGS_KEYS[role]
                settings[provider_key] = (row.get("provider") or "").strip()
                settings[model_key] = (row.get("model") or "").strip()

        routing = data.get("routing") or {}
        if "routing" in data:
            from distr.core.project_cli_backends.ide_handoff import is_ide_backend

            for level, default_backend, default_model in [
                ("low", "cursor", "auto"),
                ("medium", "codex", "auto"),
                ("high", "codex", "gpt-5.3-codex"),
            ]:
                row = routing.get(level) or {}
                settings[f"project_cli_{level}_backend"] = normalize_backend_id(row.get("backend") or default_backend)
                backend_id = settings[f"project_cli_{level}_backend"]
                if "model_provider" in row or "provider" in row:
                    settings[f"project_cli_{level}_model_provider"] = (
                        row.get("model_provider") or row.get("provider") or ""
                    ).strip().lower()
                fallback_backend = normalize_backend_id(row.get("fallback_backend") or "")
                fallback_model = (row.get("fallback_model") or "").strip()
                if is_ide_backend(backend_id):
                    settings[f"project_cli_{level}_model"] = ""
                    settings[f"project_cli_{level}_model_provider"] = ""
                    settings[f"project_cli_{level}_fallback_backend"] = (
                        fallback_backend if fallback_backend and not is_ide_backend(fallback_backend) else ""
                    )
                    settings[f"project_cli_{level}_fallback_model"] = (
                        fallback_model if settings[f"project_cli_{level}_fallback_backend"] else ""
                    )
                    settings.pop(f"project_cli_{level}_codex_intelligence", None)
                    settings.pop(f"project_cli_{level}_codex_speed", None)
                    continue
                settings[f"project_cli_{level}_model"] = (row.get("model") or default_model).strip()
                if backend_id == "codex":
                    settings[f"project_cli_{level}_model_provider"] = "openai"
                elif backend_id == "claude_code":
                    settings[f"project_cli_{level}_model_provider"] = "anthropic"
                settings[f"project_cli_{level}_fallback_backend"] = ""
                settings[f"project_cli_{level}_fallback_model"] = ""
                if backend_id == "codex":
                    settings[f"project_cli_{level}_codex_intelligence"] = normalize_codex_intelligence(
                        row.get("codex_intelligence") or row.get("codex_reasoning_effort")
                    )
                    settings[f"project_cli_{level}_codex_speed"] = normalize_codex_speed(
                        row.get("codex_speed") or row.get("codex_service_tier")
                    )
                else:
                    settings.pop(f"project_cli_{level}_codex_intelligence", None)
                    settings.pop(f"project_cli_{level}_codex_speed", None)

        # Keep the existing workflow LLM fallback aligned with orchestrator routing
        # so older code paths still resolve to the same brain.
        orchestrator = models.get("orchestrator") or {}
        if "models" in data and (orchestrator.get("provider") or orchestrator.get("model")):
            settings["workflow_llm_provider"] = (orchestrator.get("provider") or "").strip()
            settings["workflow_llm_model"] = (orchestrator.get("model") or "").strip()

        save_settings_to_db(settings)
        return JSONResponse({"success": True})


    @router.get("/workflows/active-runs")
    async def workflow_active_runs(limit: int = 50, workflow_id: Optional[int] = None):
        try:
            from distr.core.workflow.service import get_active_runs
            rows = await asyncio.to_thread(get_active_runs, limit=limit, workflow_id=workflow_id)
            return JSONResponse(rows)
        except Exception as e:
            logger.error("Workflow active runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/{workflow_id}/runs")
    async def workflow_runs(workflow_id: int, limit: int = 10):
        try:
            from distr.core.workflow.service import get_run_history
            return JSONResponse(get_run_history(workflow_id, limit=limit))
        except Exception as e:
            logger.error("Workflow runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/{workflow_id}/orchestrator-events")
    async def workflow_orchestrator_events(
        workflow_id: int,
        limit: int = 100,
        ticket_id: Optional[int] = None,
        run_id: Optional[int] = None,
        board_id: Optional[int] = None,
    ):
        try:
            from distr.core.orchestrator import list_events

            return JSONResponse(list_events(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                board_id=board_id,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow orchestrator events failed: %s", e, exc_info=True)
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


    @router.delete("/workflows/{workflow_id}/runs/{run_id}")
    async def workflow_delete_run(workflow_id: int, run_id: int):
        """Delete one inactive workflow run and the logs scoped to that run."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
            from distr.core.db.orchestrator import (
                OrchestratorCorrectionAttempt,
                OrchestratorEvent,
                OrchestratorValidationRecord,
            )
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStepResult
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "delete_run"), status_code=404)
                status = str(run.status or "").strip().lower()
                if status in {"queued", "running", "waiting"}:
                    return JSONResponse(
                        {
                            "detail": "Active workflow runs cannot be deleted.",
                            "action": "delete_run",
                            "next_action": "Cancel or complete the run before deleting its history.",
                            "workflow_id": workflow_id,
                            "run_id": run_id,
                            "status": status,
                        },
                        status_code=409,
                    )

                session_rows = (
                    db.query(ProjectExecutionSession.id)
                    .filter(ProjectExecutionSession.workflow_id == int(workflow_id))
                    .filter(ProjectExecutionSession.run_id == int(run_id))
                    .all()
                )
                session_ids = [int(row[0]) for row in session_rows]
                deleted_executor_events = 0
                deleted_executor_sessions = 0
                if session_ids:
                    deleted_executor_events = (
                        db.query(ProjectExecutionEvent)
                        .filter(ProjectExecutionEvent.session_id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                    deleted_executor_sessions = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                deleted_corrections = (
                    db.query(OrchestratorCorrectionAttempt)
                    .filter(OrchestratorCorrectionAttempt.workflow_id == int(workflow_id))
                    .filter(OrchestratorCorrectionAttempt.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_validations = (
                    db.query(OrchestratorValidationRecord)
                    .filter(OrchestratorValidationRecord.workflow_id == int(workflow_id))
                    .filter(OrchestratorValidationRecord.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_orchestrator_events = (
                    db.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.workflow_id == int(workflow_id))
                    .filter(OrchestratorEvent.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_step_results = (
                    db.query(AutoWorkflowStepResult)
                    .filter(AutoWorkflowStepResult.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                db.delete(run)
                db.commit()

            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_run": run_id,
                "deleted_executor_sessions": deleted_executor_sessions,
                "deleted_executor_events": deleted_executor_events,
                "deleted_orchestrator_events": deleted_orchestrator_events,
                "deleted_validations": deleted_validations,
                "deleted_corrections": deleted_corrections,
                "deleted_step_results": deleted_step_results,
                "message": "Workflow run history deleted.",
                "next_action": "Open Active Runs or run history to continue with remaining runs.",
            })
        except Exception as e:
            logger.error("Workflow delete run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "delete_run"), status_code=500)


    @router.post("/workflows/{workflow_id}/run-ticket-group")
    async def workflow_run_ticket_group(workflow_id: int, data: WorkflowTicketGroupRunRequest):
        """Start the explicit queued-ticket selection using sequential/parallel policy."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            ticket_ids = list(dict.fromkeys(int(ticket_id) for ticket_id in data.ticket_ids))
            if not ticket_ids:
                return JSONResponse({"detail": "Select at least one ticket"}, status_code=422)
            if len(ticket_ids) > 100:
                return JSONResponse({"detail": "A ticket group may contain at most 100 tickets"}, status_code=422)

            from distr.core.workflow.dispatcher import start_workflow_ticket_group
            from distr.core.workflow.ticket_dispatch import compact_ticket_run_ref

            ticket_items = [compact_ticket_run_ref({"ticket_id": ticket_id}) for ticket_id in ticket_ids]
            # WorkflowAgent construction may lazily import and warm many optional
            # tools. Keep that cold-start work off uvicorn's event loop so status,
            # heartbeat, and cancellation requests remain responsive.
            result = await asyncio.to_thread(
                start_workflow_ticket_group,
                workflow_id,
                ticket_items,
                dispatch_async=True,
            )
            if result.get("error"):
                return JSONResponse(_workflow_error_payload(result["error"], "run_group"), status_code=400)
            result.update({
                "message": (
                    f"Started {len(result.get('started') or [])} ticket run(s); "
                    f"{int(result.get('queued_count') or 0)} queued in the selected group."
                ),
                "next_action": "Watch the existing Runs or Loop view for step progress.",
            })
            return JSONResponse(result)
        except ValueError as exc:
            return JSONResponse(_workflow_error_payload(str(exc), "run_group"), status_code=422)
        except Exception as exc:
            logger.error("Workflow ticket-group run failed: %s", exc, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(exc), "run_group"), status_code=500)


    @router.post("/workflows/{workflow_id}/run")
    async def workflow_run(workflow_id: int, request: Request):
        """Start a workflow inside a durable Development thread."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.work_dispatch import dispatch_work_item
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            start_step_id = body.get("start_step_id") if isinstance(body, dict) else None
            chat_id = body.get("chat_id") if isinstance(body, dict) else None
            result = await asyncio.to_thread(
                dispatch_work_item,
                workflow_id=workflow_id,
                chat_id=int(chat_id) if chat_id is not None else None,
                start_step_id=start_step_id,
                source_type="workflow_manual_run",
                dispatch_async=True,
            )
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "run"), status_code=400)
            result.update(_workflow_feedback_message("run_started", result))
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse(_workflow_error_payload(str(e), "run"), status_code=422)
        except Exception as e:
            logger.error("Workflow run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "run"), status_code=500)


    @router.post("/workflows/{workflow_id}/cancel-run/{run_id}")
    async def workflow_cancel_run(workflow_id: int, run_id: int):
        try:
            from distr.core.workflow.dispatcher import cancel_run
            if not cancel_run(run_id, workflow_id=workflow_id):
                return JSONResponse(_workflow_error_payload("Run not found", "cancel"), status_code=404)
            try:
                from distr.core.signals import signal_manager

                signal_manager.interrupt_tts.emit()
                signal_manager.player_stop.emit()
            except Exception:
                pass
            return JSONResponse({"success": True, **_workflow_feedback_message("cancelled")})
        except Exception as e:
            logger.error("Workflow cancel run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "cancel"), status_code=500)


    @router.post("/workflows/{workflow_id}/runs/{run_id}/route-approval")
    async def workflow_route_approval(workflow_id: int, run_id: int, request: Request):
        """Approve or reject a pending orchestrator route override for a waiting run."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        approved = bool(body.get("approved"))
        try:
            from distr.core.workflow.service import apply_run_route_approval

            result = apply_run_route_approval(run_id, approved=approved)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow route approval failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/runs/{run_id}/steer")
    async def workflow_harness_steer(workflow_id: int, run_id: int, request: Request):
        """Steer the active harness mid-flight without restarting the workflow step."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("input") or body.get("instruction") or "")
        try:
            from distr.core.workflow.service import apply_run_harness_steer

            result = apply_run_harness_steer(run_id, message)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow harness steer failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/runs/{run_id}/ui-feedback")
    async def workflow_ui_feedback(workflow_id: int, run_id: int, payload: UiFeedbackRequest):
        """Record the user's UI approval/rejection label for a run outcome."""
        try:
            from distr.core.orchestrator import (
                get_visual_baseline_set,
                inspect_visual_baseline_readiness,
                record_ui_feedback_label,
                upsert_visual_baseline_screens,
            )

            event_id = record_ui_feedback_label(
                label=payload.label,
                reason=payload.reason,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=payload.step_id,
                ticket_id=payload.ticket_id,
                board_id=payload.board_id,
                project_id=payload.project_id,
                execution_session_id=payload.execution_session_id,
                screenshot_paths=payload.screenshot_paths or [],
            )
            response = {
                "success": True,
                "event_id": event_id,
                "message": "UI feedback recorded.",
                "next_action": "Refresh the workflow timeline or learned rules to see the new signal.",
            }
            if payload.save_as_visual_baseline:
                if str(payload.label or "").strip().lower() != "approved":
                    return JSONResponse(
                        {"detail": "Only approved UI feedback can be accepted as a visual baseline."},
                        status_code=422,
                    )
                screenshot_path = next((str(path or "").strip() for path in (payload.screenshot_paths or []) if str(path or "").strip()), "")
                if not screenshot_path:
                    return JSONResponse({"detail": "At least one screenshot path is required to save a visual baseline."}, status_code=422)
                baseline_name = (payload.visual_baseline_name or "").strip() or f"Approved workflow {workflow_id}"
                screen_name = (payload.baseline_screen_name or "").strip() or f"Run {run_id}"
                baseline_id = upsert_visual_baseline_screens(
                    name=baseline_name,
                    board_id=payload.board_id,
                    project_id=payload.project_id,
                    description=f"Accepted from workflow {workflow_id} run {run_id} approval.",
                    screens=[{
                        "screen_name": screen_name,
                        "screenshot_path": screenshot_path,
                        "notes": payload.reason or "Approved UI screenshot.",
                        "metadata": {
                            "workflow_id": workflow_id,
                            "run_id": run_id,
                            "feedback_event_id": event_id,
                        },
                    }],
                    copy_screenshots=True,
                )
                response["visual_baseline"] = get_visual_baseline_set(baseline_set_id=baseline_id)
                response["message"] = "UI feedback recorded and visual baseline saved."
                readiness = inspect_visual_baseline_readiness(baseline_set_id=baseline_id)
                response["visual_baseline_readiness"] = readiness
                response["next_action"] = (
                    "Visual baseline is ready for UI validation."
                    if readiness.get("ready")
                    else "Visual baseline saved, but reference screenshot readiness failed."
                )
            return JSONResponse(response)
        except Exception as e:
            logger.error("Workflow UI feedback failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


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


    @router.post("/workflows/{workflow_id}/runs/{run_id}/reissue-interaction")
    async def workflow_reissue_interaction(workflow_id: int, run_id: int):
        """Re-send the same durable Telegram question and controls for a waiting run."""
        try:
            from distr.core.workflow.interactions import reissue_workflow_interaction

            result = reissue_workflow_interaction(run_id, workflow_id=workflow_id)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow interaction reissue failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/runs/{run_id}/codex-events")
    async def workflow_codex_bridge_event(workflow_id: int, run_id: int, event: CodexBridgeEventRequest):
        """Record Codex IDE/plugin steering and execution events into Decisions/orchestrator.

        This endpoint is intentionally not limited to waiting runs. Codex may report
        mid-run steering, interruption, progress, or completion while the workflow is
        still running, waiting, or already terminal.
        """
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
            from distr.core.kanban.project_execution import append_execution_event
            from distr.core.orchestrator import record_human_intervention_memory
            from distr.core.orchestration_events import (
                emit_orchestration_event,
                normalize_orchestration_event_type,
            )
            from distr.gui.web.workflow_events import increment_workflow_updated

            event_type = (event.event_type or "codex_event").strip() or "codex_event"
            status = (event.status or "").strip() or None
            message = (event.message or event.input or event.output or "").strip()
            payload = dict(event.payload or {})
            if event.input:
                payload["input"] = event.input
            if event.output:
                payload["output"] = event.output
            if event.mistake_label:
                payload["mistake_label"] = event.mistake_label
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
                latest_handoff = (
                    run_data.get("latest_backend_handoff")
                    if isinstance(run_data.get("latest_backend_handoff"), dict)
                    else {}
                )
                lower_event_type = event_type.lower().replace("-", "_").replace(" ", "_")
                bridge_suffix = lower_event_type
                for prefix in ("cursor_", "codex_", "worker_"):
                    if bridge_suffix.startswith(prefix):
                        bridge_suffix = bridge_suffix[len(prefix):]
                        break
                run_status_before = run.status
                waiting_kind_before = str(run_data.get("waiting_kind") or "").strip()
                needs_human = bridge_suffix in {
                    "needs_input",
                    "waiting",
                    "interrupted",
                } or lower_event_type in {
                    "needs_input",
                    "worker_needs_input",
                    "codex_needs_input",
                    "cursor_needs_input",
                    "codex_waiting",
                    "cursor_waiting",
                    "human_takeover",
                    "manual_fix",
                    "changes_requested",
                }
                worker_terminal = bridge_suffix in {
                    "completed",
                    "failed",
                } or lower_event_type in {
                    "completed",
                    "worker_completed",
                    "codex_completed",
                    "cursor_completed",
                    "failed",
                    "worker_failed",
                    "codex_failed",
                    "cursor_failed",
                }
                needs_input_context = {}
                worker_question_spoken = ""
                if needs_human:
                    needs_input_context, worker_question_spoken = _needs_input_context_and_spoken(
                        db,
                        workflow_id=workflow_id,
                        run=run,
                        step_id=int(step_id) if step_id else None,
                        project_id=int(project_id) if str(project_id or "").isdigit() else None,
                        message=message,
                        payload=payload,
                    )

                history = run_data.get("codex_bridge_events") or []
                history.append({
                    "event_type": event_type,
                    "status": status,
                    "message": message,
                    "input": event.input or "",
                    "output": event.output or "",
                    "step_id": step_id,
                    "ticket_id": ticket_id,
                    "project_id": project_id,
                    "execution_session_id": execution_session_id,
                    "human_intervention_state": "needs_human_input" if needs_human else run_data.get("human_intervention_state", "none"),
                    "ts": time.time(),
                })
                run_data["codex_bridge_events"] = history[-50:]
                live_context = run_data.get("live_agent_context") if isinstance(run_data.get("live_agent_context"), dict) else {}
                live_context.update({
                    "last_event_type": event_type,
                    "last_status": status,
                    "last_message": message,
                    "last_input": event.input or live_context.get("last_input", ""),
                    "last_output": event.output or live_context.get("last_output", ""),
                    "step_id": step_id,
                    "ticket_id": ticket_id,
                    "project_id": project_id,
                    "execution_session_id": execution_session_id,
                    "updated_at": history[-1]["ts"],
                    "recent_events": history[-10:],
                })
                if event_type == "user_steer" and message:
                    live_context["latest_user_steer"] = message
                if event_type in {"codex_completed", "codex_failed", "cursor_completed", "cursor_failed"} and message:
                    live_context["latest_terminal_summary"] = message
                run_data["live_agent_context"] = live_context
                if event_type in {"user_steer", "codex_interrupted", "codex_waiting", "codex_needs_input", "cursor_interrupted", "cursor_waiting", "cursor_needs_input"}:
                    run_data["last_codex_bridge_state"] = {
                        "event_type": event_type,
                        "status": status,
                        "message": message,
                        "step_id": step_id,
                        "execution_session_id": execution_session_id,
                    }
                if needs_human:
                    run.status = "waiting"
                    if step_id:
                        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                        if step:
                            step.status = "waiting"
                    run_data["waiting_kind"] = "needs_human_input"
                    run_data["human_intervention_state"] = "needs_human_input"
                    run_data["next_action"] = "needs_human_input"
                    run_data["worker_question"] = message
                    run_data["worker_question_spoken"] = worker_question_spoken or message
                    run_data["needs_input_context"] = needs_input_context
                    if latest_handoff:
                        latest_handoff["human_intervention"] = {
                            **(
                                latest_handoff.get("human_intervention")
                                if isinstance(latest_handoff.get("human_intervention"), dict)
                                else {}
                            ),
                            "state": "needs_human_input",
                            "latest_message": message,
                            "latest_label": event.mistake_label or "",
                        }
                        run_data["latest_backend_handoff"] = latest_handoff
                elif worker_terminal:
                    if run_data.get("ticket_dispatch"):
                        run.status = "failed" if "failed" in lower_event_type else "completed"
                        if step_id:
                            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                            if step:
                                step.status = "failed" if "failed" in lower_event_type else "completed"
                        run_data.pop("waiting_kind", None)
                        run_data.pop("ide_handoff_pending", None)
                        run_data["human_intervention_state"] = "resolved"
                        run_data["worker_status"] = "failed" if "failed" in lower_event_type else "completed"
                    else:
                        if run.status == "waiting":
                            run.status = "running"
                        if step_id:
                            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                            if step and step.status == "waiting":
                                step.status = "running"
                        if run_data.get("waiting_kind") in {"needs_human_input", "worker_needs_input"}:
                            run_data.pop("waiting_kind", None)
                        if run_data.get("next_action") == "needs_human_input":
                            run_data.pop("next_action", None)
                        run_data["human_intervention_state"] = "resolved"
                        run_data["worker_status"] = "failed" if "failed" in lower_event_type else "completed"
                    run_data["last_codex_bridge_state"] = {
                        "event_type": event_type,
                        "status": status,
                        "message": message,
                        "step_id": step_id,
                        "execution_session_id": execution_session_id,
                    }
                    if latest_handoff:
                        latest_handoff["state"] = "failed" if "failed" in lower_event_type else "completed"
                        latest_handoff["human_intervention"] = {
                            **(
                                latest_handoff.get("human_intervention")
                                if isinstance(latest_handoff.get("human_intervention"), dict)
                                else {}
                            ),
                            "state": "resolved",
                            "latest_message": message,
                        }
                        run_data["latest_backend_handoff"] = latest_handoff
                run.run_data = json.dumps(run_data)
                db.commit()

            append_execution_event(
                int(execution_session_id) if execution_session_id else None,
                event_type,
                status=status,
                message=message,
                payload=payload,
            )

            standard_event_type = normalize_orchestration_event_type(
                event_type,
                source="codex",
                status=status,
            )
            captured_standard = False
            if event_type in {
                "user_steer",
                "manual_fix",
                "changes_requested",
                "codex_interrupted",
                "cursor_interrupted",
            } and message:
                try:
                    from distr.core.workflow.control_policy import classify_learning_signal
                    from distr.core.workflow.steering_memory import record_run_steering_feedback

                    learning = classify_learning_signal(message, event_type=event_type)
                    record_run_steering_feedback(
                        run_id=run_id,
                        source="cursor" if "cursor" in lower_event_type else "codex",
                        event_type=event_type,
                        message=message,
                        step_id=int(step_id) if step_id else None,
                        workflow_id=workflow_id,
                        board_id=int(board_id) if board_id else None,
                        ticket_id=int(ticket_id) if ticket_id else None,
                        project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    )
                    captured_standard = bool(learning.enabled)
                except Exception:
                    logger.debug("Could not persist bridge steering", exc_info=True)

            mistake_event_id = None
            if message and (
                event.mistake_label
                or event_type in {"manual_fix", "changes_requested"}
            ):
                mistake_event_id = record_human_intervention_memory(
                    label=event.mistake_label or ("manual_fix_applied" if event_type == "manual_fix" else "missed_requirement"),
                    message=message,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=int(step_id) if step_id else None,
                    ticket_id=int(ticket_id) if ticket_id else None,
                    board_id=int(board_id) if board_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    handoff_event_id=(
                        latest_handoff.get("handoff_event_id")
                        if isinstance(latest_handoff, dict)
                        else None
                    ),
                )
            orchestrator_event_id = emit_orchestration_event(
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
            try:
                from distr.core.agent_activity import emit_agent_activity_step

                activity_result = emit_agent_activity_step(
                    source="codex",
                    surface="workflow",
                    status="waiting" if needs_human else (status or ("completed" if worker_terminal else "running")),
                    title=(
                        "Needs input"
                        if needs_human
                        else ("Worker completed" if worker_terminal else "Worker progress")
                    ),
                    summary=worker_question_spoken if needs_human and worker_question_spoken else (message or f"Codex bridge event: {event_type}"),
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=int(step_id) if step_id else None,
                    ticket_id=int(ticket_id) if ticket_id else None,
                    board_id=int(board_id) if board_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    parent_event_id=orchestrator_event_id,
                    thread_key="codex",
                    step_key=event_type,
                    step_type="needs_input" if needs_human else "cli_bridge",
                    context=needs_input_context if needs_human else {},
                    question=message if needs_human else "",
                    spoken_text=worker_question_spoken if needs_human else "",
                    payload={"bridge_event_type": event_type},
                    evidence=event.evidence or {},
                )
                orchestrator_event_id = activity_result.get("event_id") or orchestrator_event_id
            except Exception:
                logger.debug("Could not emit codex bridge agent activity", exc_info=True)

            increment_workflow_updated()

            if worker_terminal and (event.output or event.message):
                try:
                    from distr.core.workflow.step_iteration import record_harness_step_report

                    record_harness_step_report(
                        run_id=int(run_id),
                        step_id=int(step_id) if step_id else None,
                        report_text=(event.output or event.message or "").strip(),
                        source="cursor" if "cursor" in lower_event_type else "codex",
                        event_type=event_type,
                    )
                except Exception:
                    logger.debug("Could not record harness step report", exc_info=True)

            try:
                from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

                persist_worker_feedback(
                    message=message,
                    output=event.output or "",
                    input_text=event.input or "",
                    event_type=event_type,
                    source="cursor" if "cursor" in lower_event_type else "codex",
                    ticket_id=int(ticket_id) if ticket_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    board_id=int(board_id) if board_id else None,
                    workflow_id=int(workflow_id),
                    run_id=int(run_id),
                    step_id=int(step_id) if step_id else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    mistake_label=event.mistake_label or "",
                    skip_steering_log=True,
                    skip_human_intervention=True,
                )
            except Exception:
                logger.debug("Could not persist bridge feedback to workspace memory", exc_info=True)

            auto_continue_result = None
            if (
                bridge_suffix == "completed"
                and run_status_before == "waiting"
                and waiting_kind_before in {"ide_handoff", "needs_human_input"}
                and not bool(run_data.get("ticket_dispatch"))
            ):
                try:
                    from distr.core.workflow.dispatcher import continue_waiting_step

                    resume_text = (event.output or event.message or "IDE work completed.").strip()
                    auto_continue_result = continue_waiting_step(int(run_id), resume_text)
                except Exception:
                    logger.debug("IDE bridge auto-continue failed", exc_info=True)

            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "event_type": standard_event_type,
                "legacy_event_type": event_type if event_type != standard_event_type else "",
                "event_id": orchestrator_event_id,
                "orchestrator_event_id": orchestrator_event_id,
                "human_intervention_event_id": mistake_event_id,
                "captured_standard": captured_standard,
                "auto_continue": auto_continue_result,
            })
        except Exception as e:
            logger.error("Workflow Codex bridge event failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "codex_event"), status_code=500)


    @router.get("/workflows/{workflow_id}/runs/{run_id}/steering-memory")
    async def workflow_run_steering_memory(workflow_id: int, run_id: int):
        """Return run steering log and board learned rules for the Runs memory panel."""
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.workflow.steering_memory import get_run_steering_snapshot

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "steering_memory"), status_code=404)
            snapshot = get_run_steering_snapshot(int(run_id))
            if not snapshot:
                return JSONResponse(_workflow_error_payload("Run not found", "steering_memory"), status_code=404)
            return JSONResponse({"success": True, **snapshot})
        except Exception as e:
            logger.error("Workflow steering memory failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "steering_memory"), status_code=500)


    @router.get("/workflows/{workflow_id}/runs/{run_id}/timeline")
    async def workflow_run_timeline(
        workflow_id: int,
        run_id: int,
        limit: int = 100,
        mission_control: bool = False,
        detail: bool = False,
    ):
        """Return the normalized orchestration conversation timeline for a run."""
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.orchestration_events import list_orchestration_timeline

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "timeline"), status_code=404)
                # Copy scalar state before the session closes. Workflow polling
                # can race with runner commits, which expires ORM attributes;
                # reading the detached row below used to turn that race into a
                # stream of 500s in the Mission Control side panel.
                current_step_id = int(run.current_step_id) if run.current_step_id else None
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
            blueprint = {}
            try:
                from distr.core.workflow.blueprint_adherence import build_run_blueprint_snapshot

                blueprint = build_run_blueprint_snapshot(run_data if isinstance(run_data, dict) else {})
            except Exception:
                blueprint = {}
            if detail:
                from distr.core.workflow.runtime_contract import detailed_execution_timeline

                events = detailed_execution_timeline(list_orchestration_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    limit=min(max(int(limit or 500), 1), 500),
                ))
            elif mission_control:
                from distr.core.orchestration_events import list_mission_control_timeline

                events = list_mission_control_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    current_step_id=current_step_id,
                )
            else:
                events = list_orchestration_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    limit=limit,
                )
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "events": events,
                "blueprint": blueprint,
                "mission_control": bool(mission_control),
                "detail": bool(detail),
            })
        except Exception as e:
            logger.error("Workflow timeline failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "timeline"), status_code=500)


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


