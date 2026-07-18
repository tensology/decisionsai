"""StepExecutorMixin — step-type execution methods.

Extracted from StepDispatcher for clarity. All methods remain accessible
via self because StepDispatcher inherits from this mixin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun, AutoWorkflowStepResult
from distr.core.kanban.result_packet import summarize_packet_for_step_context

logger = logging.getLogger(__name__)


def capture_ui_screenshot(*, step_id: int, run_id: Optional[int], label: str) -> Optional[str]:
    """Capture a workflow UI screenshot to the local workflow screenshot folder."""
    # ponytail: macOS screencapture triggers Screen Recording permission spam (Cursor/Codex);
    # headless Playwright and E2E harnesses use browser screenshots instead.
    if os.environ.get("DECISIONS_SKIP_UI_SCREEN_CAPTURE", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        from distr.core.paths import DB_DIR

        screenshots_dir = Path(DB_DIR) / "workflow_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^a-z0-9_-]+", "_", (label or "screen").strip().lower()).strip("_") or "screen"
        run_part = f"run_{run_id}_" if run_id is not None else ""
        path = screenshots_dir / f"{run_part}step_{step_id}_{safe_label}.png"
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["screencapture", "-x", str(path)], timeout=5, check=True)
        else:
            from PIL import ImageGrab

            image = ImageGrab.grab()
            image.save(path)
        return str(path) if path.exists() else None
    except Exception:
        logger.debug("Could not capture workflow UI screenshot", exc_info=True)
        return None


def _agent_result_passed(result_text: str) -> bool:
    """Fail closed for known orchestration/model failures returned as text."""
    text = (result_text or "").strip().lower()
    if not text:
        return False
    failure_markers = (
        "model quota or billing failed",
        "you exceeded your current quota",
        "credit balance is too low",
        "insufficient credits",
        "unsupported parameter",
        "model does not support this request",
        "llm call failed",
        "agent dispatch failed",
        "error code: 400",
        "error code: 429",
        "rate limit",
        "insufficient_quota",
    )
    return not any(marker in text for marker in failure_markers)


class StepExecutorMixin:
    """Provides step execution logic: code, command, HTTP, agent, recording."""

    def _execute(self, step_data: Dict[str, Any], run_id: Optional[int]) -> Dict[str, Any]:
        """Route to the correct step-type handler."""
        step_data = self._apply_pending_correction_context(step_data, run_id)
        action_type = step_data["action_type"]
        config = self._build_config(step_data)
        handlers = {
            "execute_code": lambda: self._run_code(step_data, config, run_id=run_id),
            "playwright": lambda: self._run_playwright(step_data, config, run_id=run_id),
            "browser_use": lambda: self._run_browser_use(step_data, config, run_id=run_id),
            "ytdlp": lambda: self._run_ytdlp(config, run_id=run_id),
            "run_command": lambda: self._run_command(config, run_id=run_id),
            "http_request": lambda: self._run_http(config),
            "play_recording": lambda: self._run_recording(step_data, config, run_id=run_id),
            "decision_action": lambda: self._run_decisions_action(step_data, config, run_id=run_id),
            "send_to_project_cli": lambda: self._run_send_to_project_cli(step_data, config, run_id=run_id),
            "agent_instruction": lambda: self._run_agent(step_data, run_id),
            "computer_use": lambda: self._run_computer_use(step_data, config, run_id=run_id),
        }
        handler = handlers.get(action_type)
        if handler is None:
            return {"output": f"Unknown action type: {action_type}", "passed": False}
        wants_ui_capture = self._should_capture_ui_evidence(
            step_data, action_type, config=config, run_id=run_id,
        )
        before_path = None
        if wants_ui_capture:
            before_path = capture_ui_screenshot(
                step_id=int(step_data.get("id") or 0),
                run_id=run_id,
                label="before",
            )
        result = handler()
        if wants_ui_capture and not result.get("async"):
            after_path = capture_ui_screenshot(
                step_id=int(step_data.get("id") or 0),
                run_id=run_id,
                label="after",
            )
            result = self._with_ui_screenshot_evidence(
                result=result,
                before_path=before_path,
                after_path=after_path,
                config=config,
            )
        return result

    def _should_capture_ui_evidence(
        self,
        step_data: Dict[str, Any],
        action_type: str,
        config: Optional[dict] = None,
        run_id: Optional[int] = None,
    ) -> bool:
        config = config if isinstance(config, dict) else {}
        if config.get("skip_ui_screen_capture"):
            return False
        if run_id and self._run_is_e2e_smoke(run_id):
            return False
        if action_type in {"playwright", "browser_use"} and config.get("headless", True):
            return False
        if action_type in {"computer_use", "playwright", "browser_use"}:
            return True
        if action_type == "agent_instruction":
            return self._is_computer_use_instruction(step_data.get("instruction") or "")
        return bool(config.get("ui_quality_capture") or config.get("capture_ui_evidence"))

    @staticmethod
    def _run_is_e2e_smoke(run_id: int) -> bool:
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if not run:
                    return False
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == run.workflow_id).first()
                if not wf:
                    return False
                try:
                    wf_input = json.loads(wf.workflow_input or "{}") or {}
                except Exception:
                    wf_input = {}
                slug = str(wf_input.get("slug") or "").strip().lower()
                if slug in (
                    "dogfood-e2e-smoke",
                    "dogfood-spawn-e2e",
                    "spotify-e2e-ideation",
                    "spotify-e2e-dev",
                    "spotify-e2e-polish",
                ) or wf_input.get("e2e_smoke"):
                    return True
        except Exception:
            return False
        return False

    def _with_ui_screenshot_evidence(
        self,
        *,
        result: Dict[str, Any],
        before_path: Optional[str],
        after_path: Optional[str],
        config: Optional[dict] = None,
    ) -> Dict[str, Any]:
        config = config or {}
        output = (result.get("output") or "").strip()
        evidence_lines: List[str] = []
        if before_path:
            evidence_lines.append(f"Before screenshot: {before_path}")
        else:
            evidence_lines.append("Before screenshot unavailable: automatic capture failed before step execution.")
        if after_path:
            evidence_lines.append(f"After screenshot: {after_path}")
        elif before_path:
            evidence_lines.append("After screenshot unavailable: automatic capture failed after step execution.")
        baseline_name = (config.get("visual_baseline_name") or config.get("baseline_name") or "").strip()
        baseline_id = config.get("visual_baseline_id") or config.get("baseline_set_id")
        baseline_screen = (config.get("baseline_screen_name") or config.get("visual_baseline_screen") or "").strip()
        threshold = config.get("visual_diff_threshold")
        if baseline_name:
            evidence_lines.append(f"Visual baseline: {baseline_name}")
        if baseline_id not in (None, ""):
            evidence_lines.append(f"Visual baseline id: {baseline_id}")
        if baseline_screen:
            evidence_lines.append(f"Baseline screen: {baseline_screen}")
        if threshold not in (None, ""):
            evidence_lines.append(f"Visual diff threshold: {threshold}")
        if evidence_lines:
            result = dict(result)
            result["output"] = "\n".join(evidence_lines + ([output] if output else [])).strip()
        return result

    def _apply_pending_correction_context(
        self,
        step_data: Dict[str, Any],
        run_id: Optional[int],
    ) -> Dict[str, Any]:
        """Prepend auto-dispatched correction instructions to the step when present."""
        if run_id is None:
            return step_data
        try:
            from distr.core.orchestrator import format_correction_instruction

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if not run or not run.run_data:
                    return step_data
                run_data = json.loads(run.run_data or "{}")
                pending = run_data.get("pending_correction") or {}
                if int(pending.get("step_id") or 0) != int(step_data.get("id") or 0):
                    return step_data
                packet = pending.get("packet") or {}
                correction_text = format_correction_instruction(packet)
                if not correction_text:
                    return step_data
                existing = (step_data.get("instruction") or "").strip()
                step_data = dict(step_data)
                step_data["instruction"] = f"{correction_text}\n\n{existing}".strip()
                run_data.pop("pending_correction", None)
                run.run_data = json.dumps(run_data)
                db.commit()
                return step_data
        except Exception:
            logger.debug("Could not apply pending correction context", exc_info=True)
        return step_data

    # ── Step type handlers ──────────────────────────────────────────

    def _run_code(self, step_data: Dict[str, Any], config: dict,
                   run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute Python code. Generate from instruction if no code provided."""
        return self._run_code_type(step_data, config, "execute_code", run_id=run_id)

    def _run_playwright(self, step_data: Dict[str, Any], config: dict,
                        run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute Playwright browser automation code."""
        return self._run_code_type(step_data, config, "playwright", run_id=run_id)

    def _run_browser_use(self, step_data: Dict[str, Any], config: dict,
                         run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute a Browser Use step through the deterministic browser adapter.

        Browser Use remains the explicit workflow action/tool identity while its
        local execution adapter uses Playwright. This keeps runs deterministic
        and usable without requiring a separately configured agentic-browser LLM.
        """
        result = self._run_code_type(step_data, config, "playwright", run_id=run_id)
        output = str(result.get("output") or "")
        result["output"] = (
            "Browser Use executed via the local Playwright adapter."
            + ("\n" + output if output else "")
        )
        result["browser_surface"] = "browser_use"
        result["browser_adapter"] = "playwright"
        return result

    def _run_ytdlp(self, config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Fetch YouTube/video metadata, subtitles, or search via yt-dlp."""
        from distr.core.yt_dlp_support import run_ytdlp_step

        return run_ytdlp_step(config)

    def _run_code_type(self, step_data: Dict[str, Any], config: dict,
                       action_type: str, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Shared logic for execute_code and playwright steps."""
        exec_code = (config.get("code") or step_data.get("code") or "").strip()
        # Regenerate if stored code is JavaScript (wrong language for the Python runner).
        if exec_code and ("require(" in exec_code or "const {" in exec_code or "const " in exec_code[:80]):
            logger.warning("_run_code_type: stored code appears to be JavaScript, regenerating for step %s", step_data.get("id"))
            exec_code = ""
        if not exec_code and step_data["instruction"].strip():
            exec_code = self._generate_code(step_data["instruction"], action_type, run_id=run_id)
            if exec_code is None:
                return {"output": f"Code generation failed for {action_type}", "passed": False}
        if not exec_code:
            return {"output": "No code or instruction provided", "passed": False}
        try:
            from distr.core.workflow_engine.test_loop import TestLoopService
            svc = TestLoopService()
            # Resolve project working directory if step has a linked project
            cwd = None
            linked_project_id = step_data.get("linked_project_id")
            if not linked_project_id and step_data.get("workflow_id"):
                with get_session() as db:
                    step_obj = db.query(AutoWorkflowStep).filter(
                        AutoWorkflowStep.id == step_data["id"]).first()
                    if step_obj and step_obj.linked_project_id:
                        linked_project_id = step_obj.linked_project_id
            if linked_project_id:
                try:
                    from distr.core.db.projects import Project
                    with get_session() as db:
                        proj = db.query(Project).filter(Project.id == linked_project_id).first()
                        if proj and proj.folder_location:
                            cwd = proj.folder_location
                except Exception:
                    pass
            if action_type == "playwright":
                timeout_seconds = max(1, int(config.get("timeout_seconds", 120) or 120))
                exec_result = svc._execute_playwright(
                    exec_code,
                    headless=config.get("headless", True),
                    timeout=timeout_seconds,
                )
            else:
                timeout_seconds = max(1, int(config.get("timeout_seconds", 60) or 60))
                exec_result = svc._execute_python(
                    exec_code,
                    timeout=timeout_seconds,
                    cwd=cwd,
                )
            stdout = getattr(exec_result, "stdout", "") or (exec_result.get("stdout", "") if isinstance(exec_result, dict) else "")
            stderr = getattr(exec_result, "stderr", "") or (exec_result.get("stderr", "") if isinstance(exec_result, dict) else "")
            exit_code = getattr(exec_result, "exit_code", None)
            if exit_code is None:
                exit_code = exec_result.get("exit_code", 1) if isinstance(exec_result, dict) else 1
            return {"output": (stdout + "\n" + stderr).strip()[:2000], "passed": exit_code == 0}
        except Exception as e:
            return {"output": f"{action_type} execution error: {e}", "passed": False}

    @staticmethod
    def _apply_step_harness_overrides(route: dict, config: dict) -> dict:
        """Merge per-step harness fields from step config onto the execution route."""
        from distr.core.project_cli_backends import normalize_backend_id

        merged = dict(route or {})
        execution_route = config.get("execution_route") if isinstance(config.get("execution_route"), dict) else {}
        snapshot = execution_route.get("route_snapshot") if isinstance(execution_route.get("route_snapshot"), dict) else {}
        if execution_route.get("enabled") and execution_route.get("mode") == "scoped" and snapshot:
            backend_id = str(snapshot.get("backend_id") or "").strip()
            if backend_id:
                merged["backend"] = normalize_backend_id(backend_id)
            model = str(snapshot.get("model") or "").strip()
            if model:
                merged["model"] = model
            provider = str(snapshot.get("provider") or "").strip()
            if provider:
                merged["model_provider"] = provider
            if snapshot.get("intelligence_hint"):
                merged["intelligence_hint"] = str(snapshot.get("intelligence_hint") or "").strip()
            if snapshot.get("speed_hint"):
                merged["speed_hint"] = str(snapshot.get("speed_hint") or "").strip()
            if snapshot.get("tier"):
                merged["tier"] = str(snapshot.get("tier") or "").strip()
            merged["source"] = "step_execution_route"
            merged["rationale"] = (
                f"Workflow step selected scoped route "
                f"{str(snapshot.get('name') or snapshot.get('model') or 'route').strip()}."
            )
        backend_id = str(config.get("backend_id") or "").strip()
        if backend_id:
            merged["backend"] = normalize_backend_id(backend_id)
        model = str(config.get("model") or "").strip()
        # "auto" means inherit the resolved board/run route. Treating it as a
        # literal override discards an explicit board model and causes a second,
        # potentially unrelated catalog selection.
        if model and (model != "auto" or str(merged.get("model") or "").strip() in {"", "auto"}):
            merged["model"] = model
        provider = str(config.get("model_provider") or config.get("provider") or "").strip()
        if provider:
            merged["model_provider"] = provider
        complexity = str(config.get("complexity") or "").strip().lower()
        if complexity in {"low", "medium", "high"}:
            merged["complexity"] = complexity
        if config.get("codex_reasoning_effort"):
            merged["codex_reasoning_effort"] = str(config.get("codex_reasoning_effort") or "").strip()
        if config.get("codex_service_tier"):
            merged["codex_service_tier"] = str(config.get("codex_service_tier") or "").strip()
        return merged

    @staticmethod
    def _step_execution_route_enabled(config: dict) -> bool:
        execution_route = config.get("execution_route") if isinstance(config.get("execution_route"), dict) else {}
        return bool(
            execution_route.get("enabled")
            and execution_route.get("mode") == "scoped"
            and isinstance(execution_route.get("route_snapshot"), dict)
            and str(execution_route.get("scoped_model_key") or "").strip()
        )

    @staticmethod
    def _active_run_execution_route(run_data: dict | None) -> dict[str, Any]:
        route = run_data.get("execution_route") if isinstance(run_data, dict) and isinstance(run_data.get("execution_route"), dict) else {}
        backend = str(route.get("backend") or "").strip()
        model = str(route.get("model") or "").strip()
        if not backend and not model:
            return {}
        return dict(route)

    def _run_command(self, config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute a shell command."""
        import subprocess

        from distr.core.rtk_support import run_shell_command

        cmd = config.get("command", "")
        cwd = config.get("working_directory") or self._project_cwd_for_run(run_id)
        timeout = config.get("timeout_seconds", 60)
        try:
            proc = run_shell_command(cmd, timeout=timeout, cwd=cwd)
            return {"output": (proc.stdout + proc.stderr).strip()[:2000],
                    "passed": proc.returncode == 0}
        except subprocess.TimeoutExpired:
            return {"output": f"Command timed out after {timeout}s", "passed": False}
        except Exception as e:
            return {"output": f"Command execution error: {e}", "passed": False}

    def _project_cwd_for_run(self, run_id: Optional[int]) -> Optional[str]:
        """Resolve the linked project folder for run-scoped command steps."""
        if run_id is None:
            return None
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if not run:
                    return None
                run_data: dict[str, Any] = {}
                if run.run_data:
                    routing_settings = {}
                    try:
                        run_data = json.loads(run.run_data or "{}") or {}
                    except Exception:
                        run_data = {}
                folder = str(run_data.get("project_folder") or "").strip()
                if not folder and run_data.get("project_id"):
                    try:
                        from distr.core.db.projects import Project

                        project = db.query(Project).filter(Project.id == int(run_data["project_id"])).first()
                        folder = str(getattr(project, "folder_location", "") or "").strip()
                    except Exception:
                        folder = ""
                if not folder:
                    return None
                path = Path(folder).expanduser()
                return str(path) if path.is_dir() else None
        except Exception:
            logger.debug("_project_cwd_for_run: failed to resolve project folder", exc_info=True)
            return None

    def _run_http(self, config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Make an HTTP request."""
        url = config.get("url", "")
        method = config.get("method", "GET")
        headers = config.get("headers", {})
        body = config.get("body")
        timeout = config.get("timeout_seconds", 30)
        # Auto-serialize dict bodies as JSON
        json_body = None
        if isinstance(body, dict):
            json_body = body
            body = None
            headers = dict(headers) if headers else {}
            headers.setdefault("Content-Type", "application/json")
        try:
            import requests
            resp = requests.request(method, url, headers=headers, data=body, json=json_body, timeout=timeout)
            return {"output": f"HTTP {resp.status_code}\n{resp.text[:1500]}",
                    "passed": 200 <= resp.status_code < 400}
        except Exception as e:
            return {"output": f"HTTP request failed: {e}", "passed": False}

    def _run_send_to_project_cli(
        self,
        step_data: Dict[str, Any],
        config: dict,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send step instruction to a project's CLI session.

        Project resolution order:
        1) run/ticket project context
        2) ticket linked project or board default project
        3) step.linked_project_id fallback for advanced/debug workflows
        """
        instruction = (config.get("instruction") or step_data.get("instruction") or "").strip()
        if not instruction:
            return {"output": "No instruction provided for Send to Project CLI", "passed": False}

        # Full agent prompt is built inside _run_task once the execution route (and
        # IDE vs CLI backend) is known. IDE handoffs stay slim and orchestrator-driven.

        step_project_id = step_data.get("linked_project_id")
        project_id = None
        project_folder = None
        workflow_id = step_data.get("workflow_id")
        run_data: dict[str, Any] = {}

        def _coerce_int(value: Any) -> Optional[int]:
            if value is None:
                return None
            try:
                text = str(value).strip()
                if not text:
                    return None
                return int(text)
            except (TypeError, ValueError):
                return None

        try:
            from distr.core.db.projects import Project
            with get_session() as db:
                step_obj = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_data["id"]).first()
                if step_obj and step_obj.linked_project_id:
                    step_project_id = step_obj.linked_project_id
                if step_obj and step_obj.workflow_id:
                    workflow_id = step_obj.workflow_id

                if run_id:
                    run_obj = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                    if run_obj and run_obj.run_data:
                        try:
                            run_data = json.loads(run_obj.run_data or "{}")
                            if run_data.get("ticket_id") is not None:
                                instruction = instruction.replace("{{ticket_id}}", str(run_data.get("ticket_id")))
                            if run_data.get("project_id") is not None:
                                instruction = instruction.replace("{{project_id}}", str(run_data.get("project_id")))
                            run_project_id = run_data.get("project_id")
                            resolved_run_project_id = _coerce_int(run_project_id)
                            if resolved_run_project_id:
                                project_id = resolved_run_project_id
                        except (ValueError, TypeError, json.JSONDecodeError):
                            pass

                if not project_id:
                    ticket_id = _coerce_int(run_data.get("ticket_id"))
                    if ticket_id:
                        try:
                            from distr.core.db.kanban import KanbanTicket

                            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
                            if ticket and ticket.linked_project_id:
                                project_id = ticket.linked_project_id
                            elif ticket and ticket.lane and ticket.lane.board and ticket.lane.board.default_project_id:
                                project_id = ticket.lane.board.default_project_id
                        except Exception:
                            pass

                if not project_id:
                    project_id = _coerce_int(step_project_id)

                if project_id:
                    project = db.query(Project).filter(Project.id == int(project_id)).first()
                    if project and project.folder_location:
                        project_folder = project.folder_location
        except Exception as e:
            return {"output": f"Failed resolving project context: {e}", "passed": False}

        if not project_id or not project_folder:
            return {
                "output": (
                    "No linked project for this ticket. "
                    "Link the ticket to a project with a folder on the board, then run again."
                ),
                "passed": False,
            }

        try:
            from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
            from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, load_decisions_json

            ticket_id_brief = _coerce_int(run_data.get("ticket_id"))
            brief = ""
            if ticket_id_brief:
                hook_ensure_workspace("tickets", ticket_id_brief, reason="send_to_project_cli")
                brief = build_pickup_brief(
                    entity_type="tickets",
                    entity_id=ticket_id_brief,
                    decisions=load_decisions_json("tickets", ticket_id_brief),
                )
            elif project_id:
                hook_ensure_workspace("projects", project_id, reason="send_to_project_cli")
                brief = build_pickup_brief(
                    entity_type="projects",
                    entity_id=project_id,
                    decisions=load_decisions_json("projects", project_id),
                )
            if brief.strip():
                instruction = brief.strip() + "\n\n---\n\n" + instruction
        except Exception:
            logger.debug("send_to_project_cli: pickup brief injection failed", exc_info=True)

        try:
            from distr.core.project_cli_backends.harness import HarnessContext
            import concurrent.futures

            async def _run_task():
                with get_session() as db:
                    project = db.query(Project).filter(Project.id == int(project_id)).first()
                    if not project:
                        raise ValueError(f"Project #{project_id} not found")
                    route = {}
                    decision = None
                    ticket = None
                    board = None
                    ticket_id = run_data.get("ticket_id")
                    run_row = (
                        db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                        if run_id
                        else None
                    )
                    run_data_local = json.loads(run_row.run_data or "{}") or {} if run_row else {}
                    from distr.core.work_intake.execution_policy import apply_requested_step_policy

                    config_local, requested_step_role, requested_step_route = apply_requested_step_policy(
                        config,
                        step=step_data,
                        run_data=run_data_local,
                    )
                    wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first() if workflow_id else None
                    from distr.core.project_cli_backends.model_policy import _workflow_run_settings

                    workflow_model_policy = _workflow_run_settings(wf)
                    workflow_model_policy.update(config_local.get("model_policy") or {})
                    auto_step_routing = bool(workflow_model_policy.get("auto_route_models", False))
                    stored_step_routes = run_data_local.get("step_routes")
                    stored_step_routes = stored_step_routes if isinstance(stored_step_routes, dict) else {}
                    stored_step_route = stored_step_routes.get(str(step_data.get("id")))
                    stored_step_route = stored_step_route if isinstance(stored_step_route, dict) else {}
                    if ticket_id is not None:
                        try:
                            from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
                            from distr.core.orchestrator_routing import resolve_execution_route

                            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                            if ticket and getattr(ticket, "lane_id", None):
                                lane = db.query(KanbanLane).filter(KanbanLane.id == int(ticket.lane_id)).first()
                                if lane and getattr(lane, "board_id", None):
                                    board = db.query(KanbanBoard).filter(KanbanBoard.id == int(lane.board_id)).first()
                            if ticket:
                                approved_override = run_data_local.get("approved_route_override")
                                if self._step_execution_route_enabled(config_local):
                                    route = self._apply_step_harness_overrides({}, config_local)
                                    route["complexity"] = str(
                                        route.get("complexity")
                                        or run_data_local.get("execution_route", {}).get("complexity")
                                        or getattr(ticket, "complexity", None)
                                        or "medium"
                                    ).strip().lower() or "medium"
                                    route.setdefault("source", "step_execution_route")
                                elif approved_override and isinstance(approved_override, dict):
                                    from distr.core.project_cli_backends import normalize_backend_id

                                    route = {
                                        "backend": normalize_backend_id(
                                            str(approved_override.get("backend") or "").strip() or "pi"
                                        ),
                                        "model": str(approved_override.get("model") or "auto").strip(),
                                        "complexity": str(
                                            approved_override.get("complexity")
                                            or run_data_local.get("execution_route", {}).get("complexity")
                                            or "medium"
                                        ),
                                        "source": "orchestrator_override",
                                        "rationale": str(approved_override.get("rationale") or "").strip(),
                                        "requires_approval": False,
                                    }
                                elif stored_step_route:
                                    route = dict(stored_step_route)
                                    route.setdefault("source", "active_step_route")
                                    route.setdefault("rationale", "Continuing this step with its recorded execution route.")
                                elif not auto_step_routing and self._active_run_execution_route(run_data_local):
                                    route = self._active_run_execution_route(run_data_local)
                                    route.setdefault("source", "active_run_route")
                                    route.setdefault("rationale", "Continuing with the workflow run's active execution route.")
                                    route["complexity"] = str(
                                        route.get("complexity")
                                        or getattr(ticket, "complexity", None)
                                        or "medium"
                                    ).strip().lower() or "medium"
                                else:
                                    decision = resolve_execution_route(
                                        project=project,
                                        ticket=ticket,
                                        board=board,
                                        run_id=run_id,
                                        step_id=step_data.get("id"),
                                        workflow_id=workflow_id,
                                        allow_orchestrator_override=not bool(
                                            run_data_local.get("suppress_orchestrator_override")
                                        ),
                                        # Auto mode makes a second, role-aware decision below.
                                        # Emitting the generic baseline here produces two
                                        # contradictory model announcements for one step.
                                        emit_event=not auto_step_routing,
                                    )
                                    route = decision.to_route_dict()
                                if run_row:
                                    run_data_local["execution_route"] = route
                                    if decision and decision.requires_approval:
                                        run_data_local["pending_route_approval"] = decision.override_route or {}
                                        run_data_local["route_approval_pending"] = True
                                        run_row.run_data = json.dumps(run_data_local)
                                        db.commit()
                                        override = decision.override_route or {}
                                        backend_hint = override.get("backend") or route.get("backend") or "auto"
                                        return {
                                            "output": (
                                                f"Route override suggested: {backend_hint}. "
                                                "Waiting for human approval before dispatching."
                                            ),
                                            "passed": True,
                                            "skip_wait": False,
                                            "route_approval_pending": True,
                                        }
                                    run_row.run_data = json.dumps(run_data_local)
                                    db.commit()
                        except Exception:
                            # Route resolution and optional skill provisioning share
                            # this guarded block. Never discard a route that was
                            # already resolved just because enrichment failed; doing
                            # so silently reselects a different model downstream.
                            logger.warning(
                                "send_to_project_cli: route enrichment failed; preserving resolved route=%s",
                                route,
                                exc_info=True,
                            )
                    route = self._apply_step_harness_overrides(route, config_local)
                    try:
                        from distr.core.project_cli_backends.model_policy import (
                            apply_auto_step_role_policy,
                            apply_workflow_model_policy,
                        )
                        from distr.core.settings import load_settings_from_db

                        routing_settings = load_settings_from_db()
                        if not self._step_execution_route_enabled(config_local):
                            route = apply_workflow_model_policy(
                                route,
                                workflow=wf,
                                config=config_local,
                                settings=routing_settings,
                            )
                            route = apply_auto_step_role_policy(
                                route,
                                workflow=wf,
                                config=config_local,
                                settings=routing_settings,
                                step_role=requested_step_role,
                                prior_role_routes=run_data_local.get("step_role_routes") or {},
                            )
                    except Exception:
                        logger.debug("send_to_project_cli: model policy resolution failed", exc_info=True)
                    try:
                        from distr.core.project_cli_backends.provider_preflight import preflight_provider_route

                        provider_preflight = preflight_provider_route(
                            route,
                            settings=routing_settings,
                            complexity=str(route.get("complexity") or "medium"),
                        )
                    except Exception:
                        logger.warning("Provider preflight failed unexpectedly; leaving route unverified", exc_info=True)
                        provider_preflight = None
                    provider_model_readiness = None
                    if (
                        provider_preflight is not None
                        and provider_preflight.ready is True
                        and str(route.get("model_provider") or "").strip().lower() == "openrouter"
                        and str(route.get("model") or "").strip().lower().endswith(":free")
                        and not bool(route.get("provider_preflight_override"))
                    ):
                        try:
                            from distr.core.project_cli_backends.provider_preflight import (
                                probe_openrouter_model_readiness,
                            )

                            provider_model_readiness = probe_openrouter_model_readiness(
                                model=str(route.get("model") or ""),
                                api_key=str((routing_settings or {}).get("openrouter_key") or ""),
                            )
                            if provider_model_readiness.ready is False:
                                provider_preflight = provider_model_readiness
                        except Exception:
                            logger.warning(
                                "OpenRouter free-model readiness probe failed unexpectedly; leaving route unverified",
                                exc_info=True,
                            )
                    if run_row and provider_model_readiness is not None:
                        latest = json.loads(run_row.run_data or "{}") or {}
                        latest["provider_model_readiness"] = provider_model_readiness.to_dict()
                        run_row.run_data = json.dumps(latest)
                        db.commit()
                    if (
                        provider_preflight is not None
                        and provider_preflight.ready is False
                        and not bool(route.get("provider_preflight_override"))
                    ):
                        free_candidates = []
                        if str(route.get("model_provider") or "").strip().lower() == "openrouter":
                            try:
                                from distr.core.project_cli_backends.provider_preflight import rank_openrouter_free_models

                                free_candidates = rank_openrouter_free_models(
                                    api_key=str((routing_settings or {}).get("openrouter_key") or ""),
                                    complexity=str(route.get("complexity") or "medium"),
                                    required_capabilities=list(config.get("required_capabilities") or ["tools"]),
                                    limit=3,
                                )
                            except Exception:
                                logger.warning("Could not fetch ranked OpenRouter free candidates", exc_info=True)
                        fallback = self._runtime_provider_fallback_route(route, config_local)
                        proposed = dict((free_candidates[0] if free_candidates else None) or fallback or route)
                        proposed["provider_preflight_override"] = not bool(fallback)
                        if free_candidates:
                            proposed["provider_preflight_override"] = False
                            proposed["source"] = "provider_preflight_free_recommendation"
                        proposed["rationale"] = (
                            f"{provider_preflight.message} "
                            + (
                                "Try the recommended current free model after a readiness probe."
                                if free_candidates
                                else ("Use this alternative route instead." if fallback else
                                "Proceed with the selected route only if you explicitly accept this risk.")
                            )
                        ).strip()
                        current_label = " / ".join(
                            part for part in (
                                str(route.get("backend") or "pi"),
                                str(route.get("model_provider") or ""),
                                str(route.get("model") or "auto"),
                            ) if part
                        )
                        proposed_label = " / ".join(
                            part for part in (
                                str(proposed.get("backend") or "pi"),
                                str(proposed.get("model_provider") or ""),
                                str(proposed.get("model") or "auto"),
                            ) if part
                        )
                        question = (
                            f"I checked {current_label} before starting and cannot safely dispatch it. "
                            f"{provider_preflight.message}"
                        )
                        if free_candidates:
                            choices = " ".join(
                                f"{item['rank']}. {item.get('name') or item.get('model')} — {item.get('reason')}"
                                for item in free_candidates
                            )
                            question += (
                                f" I found these current free coding candidates: {choices} "
                                f"I recommend option 1. Which one would you like me to readiness-check and try?"
                            )
                        elif fallback:
                            question += f" I can switch to {proposed_label}. Would you like me to proceed?"
                        else:
                            question += " Would you like me to proceed anyway?"
                        if run_row:
                            latest = json.loads(run_row.run_data or "{}") or {}
                            latest["pending_route_approval"] = proposed
                            latest["provider_preflight_pending"] = True
                            latest["provider_preflight"] = provider_preflight.to_dict()
                            latest["provider_free_candidates"] = free_candidates
                            latest["provider_fallback_route"] = dict(fallback or {})
                            latest["provider_preflight_prompt"] = question
                            latest["waiting_prompt"] = question
                            run_row.run_data = json.dumps(latest)
                            db.commit()
                        return {
                            "output": question,
                            "passed": True,
                            "skip_wait": False,
                            "provider_preflight_pending": True,
                        }
                    try:
                        from distr.core.orchestration_events import emit_orchestration_event

                        route_backend = str(route.get("backend") or "pi")
                        route_model = str(route.get("model") or "auto")
                        route_provider = str(route.get("model_provider") or "")
                        route_label = " / ".join(
                            part for part in (route_backend, route_provider, route_model) if part
                        )
                        emit_orchestration_event(
                            source="orchestrator",
                            event_type="route_decided",
                            status="ready",
                            workflow_id=workflow_id,
                            run_id=run_id,
                            step_id=step_data.get("id"),
                            ticket_id=int(ticket_id) if ticket_id is not None else None,
                            board_id=getattr(board, "id", None) if board else None,
                            project_id=int(project_id),
                            summary=(
                                f"Using {route_label} for {requested_step_role}. "
                                f"{str(route.get('policy_reason') or route.get('rationale') or '').strip()}"
                            ).strip(),
                            payload={
                                "decision": route,
                                "step_role": requested_step_role,
                                "auto_detected": bool(route.get("auto_detected")),
                            },
                        )
                        if run_id:
                            from distr.core.kanban.ticket_workflow_engagement import (
                                build_route_selection_message,
                                notify_ticket_workflow_progress,
                            )

                            notify_ticket_workflow_progress(
                                run_id=int(run_id),
                                step_id=int(step_data.get("id")) if step_data.get("id") else None,
                                body=build_route_selection_message(
                                    ticket_title=str((run_data_local or {}).get("ticket_title") or ""),
                                    step_name=str(step_data.get("name") or ""),
                                    step_role=requested_step_role,
                                    backend=route_backend,
                                    model=route_model,
                                    provider=route_provider,
                                    reason=str(
                                        route.get("policy_reason")
                                        or route.get("rationale")
                                        or ""
                                    ),
                                ),
                                state_fingerprint=(
                                    f"route_selected:{step_data.get('id')}:"
                                    f"{route_backend}:{route_provider}:{route_model}"
                                ),
                                priority="normal",
                            )
                    except Exception:
                        logger.debug("Could not emit final step route", exc_info=True)
                    backend_for_skills = route.get("backend") or "pi"
                    if wf and project.folder_location and ticket_id is not None:
                        from distr.core.workflow.skill_provision import provision_workflow_skills

                        provision_workflow_skills(
                            workflow=wf,
                            project_folder=project.folder_location,
                            backend_id=backend_for_skills,
                            chain_type="pre_chain",
                            run_id=run_id,
                            workflow_id=workflow_id,
                            ticket_id=int(ticket_id),
                            board_id=getattr(board, "id", None) if board else None,
                            project_id=int(project_id),
                        )
                        extra_skills = list(decision.skills or []) if decision else []
                        step_skills = config_local.get("skills") if isinstance(config_local.get("skills"), list) else []
                        for skill_id in step_skills:
                            if skill_id and skill_id not in extra_skills:
                                extra_skills.append(skill_id)
                        if extra_skills:
                            from distr.core.workflow.skill_provision import push_skill_to_project

                            for skill_id in extra_skills:
                                push_skill_to_project(
                                    skill_id=skill_id,
                                    project_folder=project.folder_location,
                                    backend_id=backend_for_skills,
                                )
                    required_capabilities = [
                        str(item).strip()
                        for item in (config.get("required_capabilities") or [])
                        if str(item).strip()
                    ]
                    backend_id = route.get("backend") or "pi"
                    independent_from = str(requested_step_route.get("independent_from") or "").strip()
                    if independent_from and run_row:
                        latest = json.loads(run_row.run_data or "{}") or {}
                        prior_routes = latest.get("step_role_routes")
                        prior_routes = prior_routes if isinstance(prior_routes, dict) else {}
                        prior = prior_routes.get(independent_from)
                        prior = prior if isinstance(prior, dict) else {}
                        prior_backend = str(prior.get("backend") or "").strip()
                        prior_model = str(prior.get("model") or "").strip()
                        if not prior_backend and not prior_model:
                            return {
                                "output": (
                                    f"Independent {requested_step_role} was requested, but the "
                                    f"{independent_from} route was not recorded. Independence cannot be proven."
                                ),
                                "passed": False,
                            }
                        same_route = (
                            prior_backend
                            and prior_backend == str(backend_id)
                            and (not prior_model or prior_model == str(route.get("model") or "auto"))
                        )
                        if same_route:
                            from distr.core.project_cli_backends import list_backends
                            from distr.core.project_cli_backends.ide_handoff import is_ide_backend

                            alternative = next(
                                (
                                    candidate
                                    for candidate in list_backends()
                                    if candidate.id != prior_backend
                                    and not is_ide_backend(candidate.id)
                                    and candidate.setup_status().ready
                                    and candidate.supports(required_capabilities)
                                ),
                                None,
                            )
                            if alternative is None:
                                return {
                                    "output": (
                                        f"Independent {requested_step_role} was requested, but no ready "
                                        f"backend differs from the {independent_from} route {prior_backend}."
                                    ),
                                    "passed": False,
                                }
                            backend_id = alternative.id
                            route["backend"] = backend_id
                            route["model"] = "auto"
                            route.pop("model_provider", None)
                            route["independent_from_role"] = independent_from
                            route["independent_from_backend"] = prior_backend
                    if required_capabilities:
                        from distr.core.project_cli_backends import resolve_backend_for_capabilities

                        capable_backend = resolve_backend_for_capabilities(
                            required_capabilities,
                            preferred_backend_id=backend_id,
                        )
                        if capable_backend is None:
                            return {
                                "output": (
                                    "No ready execution backend provides required capabilities: "
                                    + ", ".join(required_capabilities)
                                ),
                                "passed": False,
                            }
                        backend_id = capable_backend.id
                        route["backend"] = backend_id
                        route["required_capabilities"] = required_capabilities
                        route["capability_routed"] = True
                    if requested_step_route:
                        route["request_policy_role"] = requested_step_role
                        route["request_policy_applied"] = True
                        route["source"] = "explicit_request_policy"
                        if run_row:
                            latest = json.loads(run_row.run_data or "{}") or {}
                            role_routes = latest.get("step_role_routes")
                            if not isinstance(role_routes, dict):
                                role_routes = {}
                            role_routes[requested_step_role] = {
                                "backend": backend_id,
                                "model": str(route.get("model") or "auto"),
                                "model_provider": str(route.get("model_provider") or ""),
                                "step_id": step_data.get("id"),
                            }
                            latest["step_role_routes"] = role_routes
                            latest["execution_route"] = route
                            run_row.run_data = json.dumps(latest)
                            db.commit()
                    if run_row and auto_step_routing:
                        latest = json.loads(run_row.run_data or "{}") or {}
                        step_routes = latest.get("step_routes")
                        step_routes = step_routes if isinstance(step_routes, dict) else {}
                        step_routes[str(step_data.get("id"))] = dict(route)
                        role_routes = latest.get("step_role_routes")
                        role_routes = role_routes if isinstance(role_routes, dict) else {}
                        role_routes[requested_step_role] = {
                            "backend": backend_id,
                            "model": str(route.get("model") or "auto"),
                            "model_provider": str(route.get("model_provider") or ""),
                            "step_id": step_data.get("id"),
                            "auto_detected": bool(route.get("auto_detected")),
                        }
                        latest["step_routes"] = step_routes
                        latest["step_role_routes"] = role_routes
                        latest["execution_route"] = route
                        run_row.run_data = json.dumps(latest)
                        db.commit()
                    from distr.core.project_cli_backends.ide_handoff import is_ide_backend
                    from distr.core.workflow.step_iteration import build_ide_step_instruction

                    if is_ide_backend(backend_id):
                        final_instruction = build_ide_step_instruction(
                            step_data, run_id, config=config
                        )
                    elif run_id is not None:
                        try:
                            final_instruction = self._build_agent_prompt(step_data, run_id)
                        except Exception as exc:
                            logger.warning(
                                "_run_send_to_project_cli: enriched prompt failed: %s", exc
                            )
                            final_instruction = instruction
                    else:
                        final_instruction = instruction
                    if backend_id == "pi":
                        # Pi also loads project skills and repo guidance into its
                        # own context window. Keep the workflow packet compact so
                        # free/local routes do not silently exhaust their usable
                        # generation budget before reaching the current task.
                        final_instruction = self._bound_project_cli_prompt(
                            final_instruction,
                            max_chars=12000,
                        )
                    def _harness_context(
                        selected_backend: str,
                        selected_model: str,
                        selected_provider: str = "",
                    ) -> HarnessContext:
                        return HarnessContext(
                            project=project,
                            instruction=final_instruction,
                            backend_id=selected_backend,
                            model=selected_model,
                            ticket_id=int(ticket_id) if ticket_id is not None else None,
                            board_id=getattr(board, "id", None) if board else None,
                            run_id=run_id,
                            workflow_id=workflow_id,
                            step_id=step_data.get("id"),
                            ticket_complexity=route.get("complexity", "medium"),
                            codex_reasoning_effort=route.get("codex_reasoning_effort") or "",
                            codex_service_tier=route.get("codex_service_tier") or "",
                            origin="workflow",
                            required_capabilities=required_capabilities,
                            adapter_options={
                                key: value for key, value in {
                                    "reasoning_effort": route.get("codex_reasoning_effort"),
                                    "service_tier": route.get("codex_service_tier"),
                                    "model_provider": selected_provider,
                                    "quality_tier": config.get("quality_tier"),
                                    "latency_tier": config.get("latency_tier"),
                                    "timeout_seconds": config.get("timeout_seconds"),
                                    "provider_preflight_override": route.get("provider_preflight_override"),
                                }.items() if value not in (None, "")
                            },
                        )

                    handle = await dispatch_harness_async(
                        _harness_context(
                            backend_id,
                            str(route.get("model") or ""),
                            str(route.get("model_provider") or ""),
                        )
                    )
                    if handle.result is not None and not bool(handle.result.success):
                        free_retry_candidates = route.get("free_model_retry_candidates")
                        free_retry_candidates = (
                            list(free_retry_candidates)
                            if isinstance(free_retry_candidates, list)
                            else []
                        )
                        if free_retry_candidates and run_row:
                            current_model = str(route.get("model") or "")
                            failed_text = str(
                                getattr(handle.result, "error", "")
                                or getattr(handle.result, "output", "")
                                or "the model did not complete the work contract"
                            ).strip()
                            remaining = []
                            for index, item in enumerate(free_retry_candidates):
                                candidate = dict(item or {})
                                if str(candidate.get("model") or "") == current_model:
                                    candidate["readiness_failed"] = True
                                    candidate["execution_failed"] = True
                                    candidate["execution_failure"] = failed_text[:1000]
                                    free_retry_candidates[index] = candidate
                                elif not candidate.get("readiness_failed") and not candidate.get("execution_failed"):
                                    remaining.append((index, candidate))
                            paid_fallback = dict(route.get("paid_fallback_route") or {})
                            if remaining:
                                next_index, recommended = remaining[0]
                                proposed_retry = dict(recommended)
                                question = (
                                    f"{current_model} passed readiness but failed the actual work: {failed_text[:500]} "
                                    f"I recommend option {next_index + 1}, "
                                    f"{recommended.get('name') or recommended.get('model')}. "
                                    "Would you like me to readiness-check and try it?"
                                )
                            elif paid_fallback:
                                proposed_retry = paid_fallback
                                question = (
                                    f"{current_model} failed the actual work: {failed_text[:500]} "
                                    "No ranked free candidates remain. "
                                    f"I recommend {paid_fallback.get('backend') or 'the fallback'} / "
                                    f"{paid_fallback.get('model') or 'auto'}. Would you like to proceed?"
                                )
                            else:
                                proposed_retry = {}
                                question = (
                                    f"{current_model} failed the actual work: {failed_text[:500]} "
                                    "No ready candidate remains. Stop the run or change the route."
                                )
                            latest = json.loads(run_row.run_data or "{}") or {}
                            latest["provider_free_candidates"] = free_retry_candidates
                            latest["provider_preflight_prompt"] = question
                            latest["waiting_prompt"] = question
                            latest["provider_preflight_pending"] = True
                            if proposed_retry:
                                latest["pending_route_approval"] = proposed_retry
                            run_row.run_data = json.dumps(latest)
                            db.commit()
                            return {
                                "output": question,
                                "passed": True,
                                "skip_wait": False,
                                "provider_preflight_pending": True,
                            }
                        fallback = self._runtime_provider_fallback_route(route, config)
                        fallback_backend = str(fallback.get("backend") or "")
                        if fallback_backend:
                            from distr.core.project_cli_backends import get_backend
                            from distr.core.project_cli_backends.ide_handoff import is_ide_backend

                            fallback_adapter = get_backend(fallback_backend)
                            if (
                                fallback_backend != backend_id
                                and not is_ide_backend(fallback_backend)
                                and fallback_adapter.setup_status().ready
                                and fallback_adapter.supports(required_capabilities)
                            ):
                                try:
                                    from distr.core.orchestration_events import emit_orchestration_event

                                    emit_orchestration_event(
                                        source="orchestrator",
                                        event_type="provider_failover",
                                        status="running",
                                        workflow_id=workflow_id,
                                        run_id=run_id,
                                        step_id=step_data.get("id"),
                                        ticket_id=int(ticket_id) if ticket_id is not None else None,
                                        board_id=getattr(board, "id", None) if board else None,
                                        project_id=int(project_id),
                                        summary=(
                                            f"{backend_id} failed; retrying this step with "
                                            f"{fallback_backend}."
                                        ),
                                        payload={
                                            "failed_backend": backend_id,
                                            "fallback_backend": fallback_backend,
                                            "fallback_model": fallback.get("model") or "auto",
                                            "reason": str(handle.result.error or "")[:1000],
                                        },
                                    )
                                except Exception:
                                    logger.debug("Could not emit provider failover event", exc_info=True)
                                try:
                                    from distr.core.kanban.ticket_workflow_engagement import (
                                        build_provider_failover_message,
                                        notify_ticket_workflow_progress,
                                    )

                                    notify_ticket_workflow_progress(
                                        run_id=int(run_id),
                                        step_id=int(step_data.get("id")) if step_data.get("id") else None,
                                        body=build_provider_failover_message(
                                            ticket_title=str(
                                                (json.loads(run_row.run_data or "{}") or {}).get("ticket_title")
                                                if run_row else ""
                                            ),
                                            step_name=str(step_data.get("name") or ""),
                                            failed_backend=backend_id,
                                            fallback_backend=fallback_backend,
                                        ),
                                        state_fingerprint=(
                                            f"provider_failover:{step_data.get('id')}:"
                                            f"{backend_id}:{fallback_backend}"
                                        ),
                                        priority="normal",
                                    )
                                except Exception:
                                    logger.debug(
                                        "Could not send provider failover engagement",
                                        exc_info=True,
                                    )
                                route.update(fallback)
                                if not fallback.get("model_provider"):
                                    route.pop("model_provider", None)
                                try:
                                    from distr.core.project_cli_backends.model_policy import build_auto_fallback_chain
                                    from distr.core.settings import load_settings_from_db

                                    route["fallback_chain"] = build_auto_fallback_chain(
                                        route,
                                        settings=load_settings_from_db(),
                                    )
                                except Exception:
                                    pass
                                route["source"] = "runtime_provider_failover"
                                route["policy_source"] = "runtime_provider_failover"
                                route["policy_reason"] = (
                                    f"The {backend_id} execution failed its completion contract."
                                )
                                route["fallback_from"] = backend_id
                                if run_row:
                                    run_data_local = json.loads(run_row.run_data or "{}") or {}
                                    run_data_local["execution_route"] = route
                                    step_routes = run_data_local.get("step_routes")
                                    step_routes = step_routes if isinstance(step_routes, dict) else {}
                                    step_routes[str(step_data.get("id"))] = dict(route)
                                    run_data_local["step_routes"] = step_routes
                                    run_row.run_data = json.dumps(run_data_local)
                                    db.commit()
                                handle = await dispatch_harness_async(
                                    _harness_context(
                                        fallback_backend,
                                        str(fallback.get("model") or "auto"),
                                        str(fallback.get("model_provider") or ""),
                                    )
                                )
                    if run_id and handle.execution_session_id:
                        if run_row:
                            run_data_local = json.loads(run_row.run_data or "{}") or {}
                            run_data_local["execution_session_id"] = handle.execution_session_id
                            run_data_local["execution_route"] = route
                            run_data_local.pop("approved_route_override", None)
                            run_data_local.pop("suppress_orchestrator_override", None)
                            run_row.run_data = json.dumps(run_data_local)
                            db.commit()
                    if handle.result is None:
                        raise ValueError("Harness dispatch returned no result")
                    return handle.result

            async def dispatch_harness_async(context):
                from distr.core.project_cli_backends.base import BackendTaskResult
                from distr.core.project_cli_backends.harness import (
                    HarnessHandle,
                    HarnessStatus,
                    dispatch_harness,
                )
                from distr.core.project_cli_backends.timing import resolve_worker_timing

                configured_timeout = int(config.get("timeout_seconds", 900) or 900)
                timing = resolve_worker_timing(
                    backend_id=context.backend_id,
                    model=context.model or "auto",
                    provider=str((context.adapter_options or {}).get("model_provider") or ""),
                    complexity=context.ticket_complexity,
                    configured_timeout_seconds=configured_timeout,
                    # The backend performs the live Ollama residency probe. The
                    # outer guard uses the conservative cold/unknown ceiling.
                    model_loaded=None,
                )
                timeout_seconds = timing.timeout_seconds + 30
                try:
                    return await asyncio.wait_for(
                        dispatch_harness(context),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    # Convert the timeout into the same failed completion
                    # contract returned by providers. Auto routing can then
                    # advance to the next evidence-backed provider instead of
                    # bypassing failover and repeating the identical model.
                    error = f"Project CLI timed out after {timeout_seconds}s"
                    result = BackendTaskResult(
                        False,
                        context.backend_id,
                        context.backend_id,
                        error=error,
                    )
                    return HarnessHandle(
                        backend_id=context.backend_id,
                        result=result,
                        status=HarnessStatus.FAILED,
                        evidence={"error": error},
                    )

            def _threaded_run_task():
                def _thread_runner():
                    return asyncio.run(_run_task())
                timeout_seconds = int(config.get("timeout_seconds", 900) or 900)
                max_attempts = 1 if config.get("allow_provider_failover", True) is False else 2
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(_thread_runner).result(
                        timeout=(timeout_seconds * max_attempts) + 30
                    )

            # asyncio.run() cannot be called from within a running event loop
            # (e.g. FastAPI/tests). Detect that case before constructing the
            # coroutine so Python does not warn about an un-awaited _run_task().
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                result = asyncio.run(_run_task())
            else:
                result = _threaded_run_task()

            if isinstance(result, dict) and (
                result.get("route_approval_pending") or result.get("provider_preflight_pending")
            ):
                if run_id:
                    try:
                        with get_session() as db:
                            run_row = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                            if run_row:
                                payload = json.loads(run_row.run_data or "{}") or {}
                                if result.get("provider_preflight_pending"):
                                    payload["provider_preflight_pending"] = True
                                else:
                                    payload["route_approval_pending"] = True
                                run_row.run_data = json.dumps(payload)
                                db.commit()
                    except Exception:
                        pass
                return {
                    "output": result.get("output", "Route override pending approval."),
                    "passed": bool(result.get("passed", True)),
                    "skip_wait": False,
                }

            backend_name = getattr(result, "backend_id", "") or "project_cli"
            output = (getattr(result, "output", "") or "").strip()
            error = (getattr(result, "error", "") or "").strip()
            passed = bool(getattr(result, "success", False))
            waits_for_human = bool(getattr(result, "waits_for_human", False))
            text = output or error or f"Sent to {backend_name}."
            return {
                "output": (
                    f"Project CLI backend: {backend_name}\n"
                    f"Project: {project_id}\n"
                    f"Status: {'waiting in IDE' if waits_for_human else ('completed' if passed else 'failed')}\n\n"
                    f"{text}"
                )[:6000],
                "passed": passed,
                "skip_wait": not waits_for_human,
            }
        except Exception as e:
            return {"output": f"Failed sending to project CLI: {e}", "passed": False}

    def _run_recording(self, step_data: Dict[str, Any], config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Play a recorded action. Async — completes via signal callback.

        Connects to action_playback_finished, action_playback_stopped, AND
        playback_failed (for file-not-found / startup errors) to ensure the
        workflow always advances. Includes a timeout watchdog as a safety net.
        """
        recording_name = self._resolve_recording_name(step_data, config)
        if not recording_name:
            return {"output": "No recording attached to this step", "passed": False}
        try:
            from distr.core.signals import signal_manager
            step_id = step_data["id"]
            # Use the run_id passed from the dispatcher rather than a fragile DB lookup.
            # If run_id is None (isolated step), we still record the result but skip routing.
            _run_id = run_id
            # Fallback: try to look up run_id from DB if not provided (backward compat)
            if _run_id is None:
                with get_session() as db:
                    step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                    if step:
                        run = db.query(AutoWorkflowRun).filter(
                            AutoWorkflowRun.workflow_id == step.workflow_id,
                            AutoWorkflowRun.current_step_id == step_id,
                            AutoWorkflowRun.status == "running",
                        ).first()
                        if run:
                            _run_id = run.id
            if _run_id is None:
                logger.warning("_run_recording: run_id is None for step %s — workflow routing will be skipped", step_id)
            _done = [False]
            _timeout_seconds = config.get("timeout_seconds", 300)

            def _cleanup():
                """Disconnect all signal handlers."""
                for sig, handler in _handlers:
                    try:
                        sig.disconnect(handler)
                    except Exception:
                        pass

            def _on_playback_done(result_text: str, passed: bool):
                if _done[0]:
                    return
                _done[0] = True
                _cleanup()
                action_id = config.get("action_id") or config.get("recording_id") or step_data.get("action_id")
                if action_id:
                    self._emit_decisions_action_event(
                        step_data,
                        _run_id,
                        "decisions_action_completed",
                        "completed" if passed else "failed",
                        result_text,
                        {
                            "action_id": action_id,
                            "mode": "recording",
                            "recording_filename": recording_name,
                        },
                    )
                self._record_result_and_route(step_id, run_id=_run_id,
                                               result_text=result_text, passed=passed)

            def _on_finished():
                _on_playback_done("Recording completed.", True)

            def _on_stopped(reason: str):
                # "stopped" usually indicates an interruption/cancel path rather
                # than a successful recording completion.
                _on_playback_done(
                    f"Recording stopped: {reason}" if reason else "Recording stopped.",
                    False,
                )

            def _on_failed(error_msg: str):
                _on_playback_done(f"Recording failed: {error_msg}", False)

            # Register handlers on both signal_manager (global) and the
            # ActionPlaybackService instance (for file-not-found errors)
            signal_manager.action_playback_finished.connect(_on_finished)
            signal_manager.action_playback_stopped.connect(_on_stopped)
            _handlers = [
                (signal_manager.action_playback_finished, _on_finished),
                (signal_manager.action_playback_stopped, _on_stopped),
            ]

            # Connect to the playback service's playback_failed signal for
            # early errors (file not found, startup failure) that don't
            # emit action_playback_stopped.
            try:
                svc = getattr(signal_manager, 'action_playback_service', None)
                if svc is not None:
                    svc.playback_failed.connect(_on_failed)
                    _handlers.append((svc.playback_failed, _on_failed))
            except Exception:
                pass  # Playback service not available — skip

            # Timeout watchdog — ensure the workflow can't hang forever
            def _timeout_watchdog():
                import time
                time.sleep(_timeout_seconds)
                if not _done[0]:
                    logger.warning("Recording playback timed out for step %s after %ds",
                                   step_id, _timeout_seconds)
                    _cleanup()
                    self._record_result_and_route(
                        step_id, run_id=_run_id,
                        result_text=f"Recording playback timed out after {_timeout_seconds}s",
                        passed=False)

            watchdog = threading.Thread(target=_timeout_watchdog, daemon=True)
            watchdog.start()

            signal_manager.play_recording_file.emit(recording_name)
            return {"async": True, "message": "Playing recording."}
        except Exception as e:
            return {"output": f"Recording playback error: {e}", "passed": False}

    def _run_decisions_action(self, step_data: Dict[str, Any], config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Run a saved Decisions Action as a workflow step.

        Recorded actions replay through the desktop playback service. Instruction
        actions are executed through the workflow agent with the saved instruction
        text, keeping both kinds reusable in orchestrator-driven workflows.
        """
        action_id = config.get("action_id") or config.get("recording_id") or step_data.get("action_id")
        if not action_id:
            return {"output": "No Decisions Action selected for this step", "passed": False}
        try:
            from distr.core.db import Action
            with get_session() as db:
                action = db.query(Action).filter(Action.id == int(action_id)).first()
                if not action:
                    return {"output": f"Decisions Action #{action_id} was not found", "passed": False}
                title = action.title or f"Action #{action.id}"
                is_instruction = bool(action.is_instruction)
                instruction_text = action.instruction_text or ""
                recording_filename = action.recording_filename or ""

            self._emit_decisions_action_event(
                step_data,
                run_id,
                "decisions_action_started",
                "running",
                f"Started Decisions Action: {title}",
                {
                    "action_id": int(action_id),
                    "title": title,
                    "mode": "instruction" if is_instruction else "recording",
                    "recording_filename": recording_filename,
                },
            )

            if is_instruction:
                if not instruction_text.strip():
                    return {"output": f"Instruction action '{title}' has no instruction text", "passed": False}
                agent_step = dict(step_data)
                agent_step["instruction"] = (
                    f"Run saved Decisions Action: {title}\n\n"
                    f"{instruction_text.strip()}"
                )
                result = self._run_agent(agent_step, run_id)
                passed = bool(result.get("passed"))
                self._emit_decisions_action_event(
                    step_data,
                    run_id,
                    "decisions_action_completed",
                    "completed" if passed else "failed",
                    f"Instruction action {'completed' if passed else 'failed'}: {title}",
                    {"action_id": int(action_id), "title": title, "mode": "instruction", "result": result.get("output", "")[:2000]},
                )
                return result

            if not recording_filename:
                self._emit_decisions_action_event(
                    step_data,
                    run_id,
                    "decisions_action_completed",
                    "failed",
                    f"Recording action has no recording file: {title}",
                    {"action_id": int(action_id), "title": title, "mode": "recording"},
                )
                return {"output": f"Recorded action '{title}' has no recording file", "passed": False}

            rec_config = dict(config)
            rec_config["recording_id"] = int(action_id)
            rec_config["recording_name"] = recording_filename
            return self._run_recording(step_data, rec_config, run_id=run_id)
        except Exception as exc:
            self._emit_decisions_action_event(
                step_data,
                run_id,
                "decisions_action_completed",
                "failed",
                f"Decisions Action failed: {exc}",
                {"action_id": action_id},
            )
            return {"output": f"Decisions Action error: {exc}", "passed": False}

    def _emit_decisions_action_event(
        self,
        step_data: Dict[str, Any],
        run_id: Optional[int],
        event_type: str,
        status: str,
        summary: str,
        payload: Optional[dict] = None,
    ) -> None:
        try:
            from distr.core.orchestrator import emit_event
            workflow_id = step_data.get("workflow_id")
            ticket_id = board_id = project_id = None
            if run_id:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                    if run:
                        workflow_id = run.workflow_id
                        ticket_id = run.ticket_id
                        board_id = run.board_id
                        project_id = run.project_id
            emit_event(
                source="workflow",
                event_type=event_type,
                status=status,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_data.get("id"),
                ticket_id=ticket_id,
                board_id=board_id,
                project_id=project_id,
                summary=summary,
                payload=payload or {},
            )
        except Exception:
            logger.debug("Could not emit Decisions Action orchestrator event", exc_info=True)

    # ── Keywords that signal a step wants computer/screen control ──────
    _CU_KEYWORDS = (
        "click", "type ", "type in", "press ", "scroll", "drag",
        "open app", "launch app", "navigate to", "go to url",
        "fill in", "fill out", "submit form", "log in", "login",
        "screenshot", "screen", "window", "button", "checkbox",
        "on screen", "on my screen", "in the app", "in the browser",
    )

    @classmethod
    def _is_computer_use_instruction(cls, instruction: str) -> bool:
        """Heuristic: does this instruction sound like computer/screen control?"""
        tl = instruction.lower()
        return any(kw in tl for kw in cls._CU_KEYWORDS)

    def _run_agent(self, step_data: Dict[str, Any], run_id: Optional[int]) -> Dict[str, Any]:
        """Send instruction to the workflow agent (or main agent as fallback)."""
        step_id = step_data["id"]
        timeout_seconds = step_data.get("timeout_seconds", 300)
        prompt = self._build_agent_prompt(step_data, run_id)
        run_ctx = self._get_run_context(step_id, run_id)

        # Detect computer-use intent from instruction or explicit flag
        raw_instruction = (step_data.get("instruction") or "").strip()
        computer_use = (
            step_data.get("computer_use_mode", False)
            or self._is_computer_use_instruction(raw_instruction)
        )

        if run_ctx is not None:
            logger.info("_run_agent: using WorkflowAgent for step %s (run_id=%s, computer_use=%s)",
                        step_id, run_id, computer_use)
            if computer_use:
                run_ctx.workflow_agent.enable_computer_use(goal=raw_instruction)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_ctx.workflow_agent.execute(prompt), run_ctx.event_loop)

                def _on_agent_done(fut):
                    from distr.core.workflow.dispatcher import workflow_run_context_is_current

                    if not workflow_run_context_is_current(run_id, run_ctx):
                        logger.info(
                            "Ignoring stale WorkflowAgent callback for run_id=%s step_id=%s",
                            run_id,
                            step_id,
                        )
                        return
                    try:
                        result_text = fut.result(timeout=0)
                        result_text = self._augment_agent_result_with_tool_evidence(
                            result_text, run_ctx.workflow_agent,
                        )
                        passed = _agent_result_passed(result_text)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=result_text, passed=passed)
                    except asyncio.CancelledError:
                        # Agent was cancelled by timeout watchdog — already handled
                        pass
                    except Exception as exc:
                        logger.error("WorkflowAgent failed for step %s: %s", step_id, exc)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=str(exc), passed=False)

                future.add_done_callback(_on_agent_done)

                # Timeout watchdog — if the agent doesn't finish in time, fail the step
                _timed_out = [False]

                def _timeout_watchdog():
                    import time
                    from distr.core.workflow.dispatcher import workflow_run_context_is_current

                    time.sleep(timeout_seconds)
                    if not workflow_run_context_is_current(run_id, run_ctx):
                        logger.info(
                            "Ignoring stale WorkflowAgent timeout for run_id=%s step_id=%s",
                            run_id,
                            step_id,
                        )
                        return
                    if not future.done():
                        _timed_out[0] = True
                        logger.warning("WorkflowAgent timed out for step %s after %ds", step_id, timeout_seconds)
                        future.cancel()
                        self._record_result_and_route(
                            step_id, run_id=run_id,
                            result_text=f"Step timed out after {timeout_seconds}s",
                            passed=False)

                watchdog = threading.Thread(target=_timeout_watchdog, daemon=True)
                watchdog.start()

                return {"async": True, "message": "Step dispatched to WorkflowAgent."}
            except Exception as e:
                logger.error("Failed to dispatch step %s to WorkflowAgent: %s", step_id, e)
                return {"output": str(e), "passed": False}

        # Fallback: no per-run event loop / shared WorkflowAgent — execute synchronously
        # in a worker thread. WorkflowAgent still loads the standard tool set (via
        # load_tools + optional cache warmup); prefer start_workflow_run() for async
        # dispatch and a persistent agent loop when available.
        logger.info(
            "_run_agent: no RunContext for step %s (run_id=%s) — using threaded WorkflowAgent fallback.",
            step_id,
            run_id,
        )
        # Must run in a background thread since we may be inside an existing
        # event loop (e.g. FastAPI uvicorn loop). We can't call
        # loop.run_until_complete() when another loop is running.
        try:
            from distr.core.workflow_agent import WorkflowAgent
            import concurrent.futures
            agent = WorkflowAgent()
            if computer_use:
                agent.enable_computer_use(goal=raw_instruction)

            def _run_agent_sync():
                """Run agent.execute() in an isolated thread with its own loop."""
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(agent.execute(prompt))
                finally:
                    loop.close()
                    agent.shutdown()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_agent_sync)
                result_text = future.result(timeout=timeout_seconds)

            if not result_text or not result_text.strip():
                result_text = "Step completed with no output."
            passed = _agent_result_passed(result_text)
            self._record_result_and_route(step_id, run_id=run_id,
                                          result_text=result_text, passed=passed)
            return {"output": result_text, "passed": passed}
        except Exception as e:
            logger.error("WorkflowAgent fallback failed for step %s: %s", step_id, e)
            # Last resort: record failure so the workflow doesn't stall forever
            self._record_result_and_route(step_id, run_id=run_id,
                                          result_text=f"Agent dispatch failed: {e}", passed=False)
            return {"output": f"Agent dispatch error: {e}", "passed": False}

    @staticmethod
    def _runtime_provider_fallback_route(
        route: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one explicit provider failover route for a failed CLI step."""
        if config.get("allow_provider_failover", True) is False:
            return {}
        current_backend = str(route.get("backend") or "pi").strip()
        explicit_fallback = bool(config.get("fallback_backend") or route.get("fallback_backend"))
        fallback_backend = str(config.get("fallback_backend") or route.get("fallback_backend") or "").strip()
        fallback_model = str(config.get("fallback_model") or route.get("fallback_model") or "auto").strip() or "auto"
        fallback_provider = str(
            config.get("fallback_model_provider")
            or route.get("fallback_model_provider")
            or ""
        ).strip()
        if not explicit_fallback:
            chain = route.get("fallback_chain")
            chain = chain if isinstance(chain, list) else []
            candidate = next(
                (
                    item for item in chain
                    if isinstance(item, dict)
                    and item.get("automatic", True) is not False
                    and str(item.get("backend") or "").strip()
                    and str(item.get("backend") or "").strip() != current_backend
                ),
                None,
            )
            if candidate:
                fallback_backend = str(candidate.get("backend") or "").strip()
                fallback_model = str(candidate.get("model") or "auto").strip() or "auto"
                fallback_provider = str(candidate.get("model_provider") or "").strip()
            elif current_backend == "pi":
                fallback_backend = "codex"
            elif current_backend == "codex":
                try:
                    from distr.core.project_cli_backends.model_policy import _openrouter_hy3_route
                    from distr.core.settings import load_settings_from_db

                    hy3 = _openrouter_hy3_route(load_settings_from_db())
                except Exception:
                    hy3 = None
                if hy3:
                    fallback_backend = "pi"
                    fallback_model = str(hy3.get("model") or "tencent/hy3-preview")
                    fallback_provider = "openrouter"
                else:
                    fallback_backend = "claude_code"
            elif current_backend != "claude_code":
                fallback_backend = "claude_code"
        if not fallback_backend or fallback_backend == current_backend:
            return {}
        fallback: dict[str, Any] = {
            "backend": fallback_backend,
            "model": fallback_model,
        }
        if fallback_provider:
            fallback["model_provider"] = fallback_provider
        return fallback

    # ── Helpers ──────────────────────────────────────────────────────

    def _build_workflow_execution_packet_context(
        self,
        step_data: Dict[str, Any],
        run_id: Optional[int],
        workflow_id: Optional[int],
    ) -> dict[str, Any]:
        """Resolve execution identity and durable memory references for a step."""
        if run_id is None:
            return {}

        try:
            from distr.core.workspace_memory.paths import (
                AGENTS_FILE,
                ACTIVE_FILE,
                CONTEXT_FILE,
                HANDOFF_FILE,
                REFERENCES_DIRNAME,
                ROUTER_FILE,
                companion_memory_file,
                companion_root,
                projection_memory_file,
                projection_root,
            )
            from distr.core.workspace_memory.reader import load_workspace_context
            step_id = step_data.get("id")
            step_name = (step_data.get("name") or f"Step {step_id or ''}").strip()
            action_type = (step_data.get("action_type") or step_data.get("step_type") or "").strip()
            project_id: Optional[int] = None
            board_id: Optional[int] = None
            ticket_id: Optional[int] = None
            project_folder = ""
            project_name = ""
            run_data: dict[str, Any] = {}
            workflow_name = ""
            ticket_title = ""

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if run:
                    workflow_id = int(run.workflow_id) if run.workflow_id else workflow_id
                    ticket_id = int(run.ticket_id) if run.ticket_id else None
                    board_id = int(run.board_id) if getattr(run, "board_id", None) else None
                    if run.run_data:
                        try:
                            run_data = json.loads(run.run_data or "{}") or {}
                        except Exception:
                            run_data = {}
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first() if workflow_id else None
                if wf:
                    workflow_name = (wf.name or "").strip()
                if not ticket_id and run_data.get("ticket_id") is not None:
                    try:
                        ticket_id = int(run_data.get("ticket_id"))
                    except Exception:
                        ticket_id = None
                if run_data.get("project_id") is not None:
                    try:
                        project_id = int(run_data.get("project_id"))
                    except Exception:
                        project_id = None
                if ticket_id:
                    try:
                        from distr.core.db.kanban import KanbanLane, KanbanTicket

                        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                        if ticket:
                            ticket_title = (ticket.title or "").strip()
                            if ticket.linked_project_id and not project_id:
                                project_id = int(ticket.linked_project_id)
                            if ticket.lane_id:
                                lane = db.query(KanbanLane).filter(KanbanLane.id == int(ticket.lane_id)).first()
                                if lane:
                                    board_id = board_id or int(lane.board_id)
                                    if lane.board and lane.board.default_project_id and not project_id:
                                        project_id = int(lane.board.default_project_id)
                    except Exception:
                        logger.debug("_build_workflow_execution_packet_context: ticket lookup failed", exc_info=True)
                if project_id:
                    try:
                        from distr.core.db.projects import Project

                        project = db.query(Project).filter(Project.id == int(project_id)).first()
                        if project:
                            project_name = (project.name or "").strip()
                            project_folder = (project.folder_location or "").strip()
                    except Exception:
                        logger.debug("_build_workflow_execution_packet_context: project lookup failed", exc_info=True)

            ctx = load_workspace_context(
                project_id=project_id,
                board_id=board_id,
                workflow_id=workflow_id,
                run_id=run_id,
                ticket_id=ticket_id,
                folder_location=project_folder,
                ensure=True,
                include_pickup_brief=True,
            )

            def _path_line(label: str, value: str) -> str:
                return f"{label}: {value}" if value else ""

            context_paths: list[str] = []
            if workflow_id:
                root = companion_root("workflows", int(workflow_id))
                context_paths.extend([
                    _path_line("Workflow agents", str(root / AGENTS_FILE)),
                    _path_line("Workflow router", str(root / ROUTER_FILE)),
                    _path_line("Workflow context", str(root / CONTEXT_FILE)),
                    _path_line("Workflow handoff", str(companion_memory_file("workflows", int(workflow_id), HANDOFF_FILE))),
                    _path_line("Workflow active memory", str(companion_memory_file("workflows", int(workflow_id), ACTIVE_FILE))),
                    _path_line("Workflow learned references", str(root / REFERENCES_DIRNAME)),
                ])
            if project_id:
                root = companion_root("projects", int(project_id))
                context_paths.extend([
                    _path_line("Project agents", str(root / AGENTS_FILE)),
                    _path_line("Project handoff", str(companion_memory_file("projects", int(project_id), HANDOFF_FILE))),
                    _path_line("Project active memory", str(companion_memory_file("projects", int(project_id), ACTIVE_FILE))),
                    _path_line("Project learned references", str(root / REFERENCES_DIRNAME)),
                ])
            if ticket_id:
                root = companion_root("tickets", int(ticket_id))
                context_paths.extend([
                    _path_line("Ticket agents", str(root / AGENTS_FILE)),
                    _path_line("Ticket handoff", str(companion_memory_file("tickets", int(ticket_id), HANDOFF_FILE))),
                    _path_line("Ticket active memory", str(companion_memory_file("tickets", int(ticket_id), ACTIVE_FILE))),
                    _path_line("Ticket learned references", str(root / REFERENCES_DIRNAME)),
                ])
            if run_id:
                root = companion_root("runs", int(run_id))
                context_paths.extend([
                    _path_line("Run agents", str(root / AGENTS_FILE)),
                    _path_line("Run handoff", str(companion_memory_file("runs", int(run_id), HANDOFF_FILE))),
                    _path_line("Run active memory", str(companion_memory_file("runs", int(run_id), ACTIVE_FILE))),
                ])
            if project_folder:
                repo_root = Path(project_folder).expanduser()
                context_paths.extend([
                    _path_line("Projected DecisionsAI agents", str(projection_root(project_folder) / AGENTS_FILE)),
                    _path_line("Projected DecisionsAI handoff", str(projection_memory_file(project_folder, HANDOFF_FILE))),
                    _path_line("Repo AGENTS.md", str(repo_root / "AGENTS.md")),
                ])

            context_paths = [line for line in context_paths if line]
            router_chain: list[str] = []
            for row in ctx.router_chain or []:
                label = row.get("label") or row.get("entity_type") or "router"
                path = row.get("path") or ""
                if path:
                    router_chain.append(f"{label}: {path}")

            return {
                "identity": {
                    "workflow": workflow_name or workflow_id or "unknown",
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "step": step_name,
                    "step_id": step_id,
                    "action": action_type or "unknown",
                    "ticket": ticket_title or ticket_id or "none",
                    "ticket_id": ticket_id,
                    "project": project_name or project_id or "unknown",
                    "project_id": project_id,
                    "project_folder": project_folder or "not set",
                },
                "memory_refs": [*context_paths, *router_chain, *list(ctx.references_index or [])[:20]],
                "memory_candidates": [
                    ctx.pickup_brief or "",
                    ctx.handoff_preview or "",
                    ctx.active_notes or "",
                ],
            }
        except Exception:
            logger.debug("_build_workflow_execution_packet_context failed", exc_info=True)
            return {}

    @staticmethod
    def _bound_project_cli_prompt(prompt: str, *, max_chars: int = 16000) -> str:
        """Bound local-worker context while preserving identity and current task.

        Execution identity and memory paths live at the start; the current step,
        guardrails, and return contract live at the end. Historical run reports
        accumulate in the middle and are the safest material to compact.
        """
        value = str(prompt or "")
        if len(value) <= max_chars:
            return value
        marker = "\n\n[Historical context compacted for worker latency]\n\n"
        head_chars = int(max_chars * 0.55)
        tail_chars = max_chars - head_chars - len(marker)
        return value[:head_chars].rstrip() + marker + value[-tail_chars:].lstrip()

    @staticmethod
    def _record_context_telemetry(run_id: Optional[int], step_id: Any, telemetry: dict[str, Any]) -> None:
        """Persist bounded prompt diagnostics so Mission Control can explain context cost."""
        if run_id is None:
            return
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if not run:
                    return
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                entry = {"step_id": step_id, **dict(telemetry or {})}
                history = [item for item in (run_data.get("context_telemetry") or []) if isinstance(item, dict)]
                history.append(entry)
                run_data["context_telemetry"] = history[-20:]
                run_data["latest_context_telemetry"] = entry
                run.run_data = json.dumps(run_data, default=str)
                db.commit()
        except Exception:
            logger.debug("Could not persist workflow context telemetry", exc_info=True)

    def _build_agent_prompt(self, step_data: Dict[str, Any], run_id: Optional[int]) -> str:
        """Build the prompt for the WorkflowAgent, enriched with full context.

        Uses the shared ``assemble_step_context`` and ``build_step_context_prompt``
        infrastructure so the dispatcher path (web UI) gets the same context
        awareness as the orchestration path (Qt app).

        Includes: workflow description, context rules, workflow input, prior step
        results, user feedback from wait_for_continue steps, step position,
        variable resolution, and step description.
        """
        from distr.core.workflow.context_limits import (
            extract_artifact_paths_from_result,
            truncate_step_result,
        )
        step_id = step_data["id"]
        workflow_id = step_data.get("workflow_id")

        # ── Load workflow-level context ──
        workflow_description = ""
        context_rules = ""
        workflow_input_context = ""
        prior_results: List[Dict[str, str]] = []
        total_steps = 1
        step_index = 0
        continuation_input = ""
        coordination_map = ""
        wf = None
        step_obj = None
        try:
            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(
                    AutoWorkflow.id == workflow_id).first()
                step_obj = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == step_id).first()

                if wf:
                    workflow_description = wf.description or ""
                    context_rules = getattr(wf, 'context_rules', None) or ""
                    try:
                        from distr.core.workflow.service import build_combined_context_rules
                        context_rules = build_combined_context_rules(workflow_id, context_rules)
                    except Exception as ce:
                        logger.debug("_build_agent_prompt: failed to combine context items: %s", ce)
                    try:
                        from distr.core.workflow.standards_memory import build_standards_context

                        board_id_for_standards = None
                        if run_id is not None:
                            run_row = db.query(AutoWorkflowRun).filter(
                                AutoWorkflowRun.id == int(run_id)
                            ).first()
                            if run_row and run_row.board_id:
                                board_id_for_standards = int(run_row.board_id)
                        context_rules = build_standards_context(
                            context_rules,
                            board_id=board_id_for_standards,
                        )
                    except Exception as ce:
                        logger.debug("_build_agent_prompt: failed to add standards memory: %s", ce)
                    all_steps = sorted(wf.steps, key=lambda s: s.position)
                    total_steps = len(all_steps)
                    for i, s in enumerate(all_steps):
                        if s.id == step_id:
                            step_index = i
                            break

        except Exception as e:
            logger.debug("_build_agent_prompt: failed to load workflow/step objects: %s", e)

        # ── Build prior results from step result history ──
        if run_id is not None:
            try:
                with get_session() as db:
                    results = (
                        db.query(AutoWorkflowStepResult)
                        .filter(AutoWorkflowStepResult.run_id == run_id)
                        .order_by(AutoWorkflowStepResult.created_at)
                        .all()
                    )
                    for sr in results:
                        if sr.step_id == step_id:
                            continue  # Skip the current step
                        s = sr.step
                        title = s.name if s else f"Step {sr.step_id}"
                        result = (sr.agent_response or "").strip()
                        if result:
                            truncated = truncate_step_result(result)
                            paths = extract_artifact_paths_from_result(result)
                            entry: Dict[str, Any] = {
                                "title": title,
                                "result": truncated,
                                "step_type": s.action_type if s else "agent_instruction",
                            }
                            if paths:
                                entry["artifact_paths"] = paths
                            prior_results.append(entry)
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load prior results: %s", e)

            # ── Load user feedback from a preceding wait step ──
            try:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(
                        AutoWorkflowRun.id == run_id).first()
                    if run and run.run_data:
                        run_data = json.loads(run.run_data or "{}")
                        try:
                            from distr.core.workflow.coordination_plan import render_coordination_map

                            coordination_map = render_coordination_map(
                                run_data.get("coordination_plan") or {},
                                current_step_id=step_id,
                            )
                        except Exception:
                            coordination_map = ""
                        feedback = run_data.get("feedback", "")
                        if feedback and feedback.strip():
                            continuation_input = feedback.strip()
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load feedback: %s", e)
            # ── Load structured ticket context (if run metadata has ticket_id) ──
            try:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(
                        AutoWorkflowRun.id == run_id).first()
                    if run and run.run_data:
                        run_data = json.loads(run.run_data or "{}")
                        # Prefer direct column (always set) over JSON metadata field
                        ticket_id = run.ticket_id or run_data.get("ticket_id")
                        if ticket_id:
                            try:
                                from distr.core.kanban.ticket_cli_context import (
                                    build_kanban_ticket_cli_instruction,
                                )
                                ticket_ctx = build_kanban_ticket_cli_instruction(
                                    db,
                                    int(ticket_id),
                                    project_name=(run_data.get("project_name") or ""),
                                    project_folder=(run_data.get("project_folder") or ""),
                                    project_id=run_data.get("project_id"),
                                    max_total_chars=4500,
                                )
                                if ticket_ctx and ticket_ctx.strip():
                                    if workflow_input_context:
                                        workflow_input_context = (
                                            workflow_input_context
                                            + "\n\n"
                                            + ticket_ctx.strip()
                                        )
                                    else:
                                        workflow_input_context = ticket_ctx.strip()
                            except Exception as ce:
                                logger.debug("_build_agent_prompt: ticket context assembly failed: %s", ce)
                        packet_context = summarize_packet_for_step_context(
                            run_data.get("result_packet") or {},
                        )
                        if packet_context:
                            if workflow_input_context:
                                workflow_input_context = (
                                    workflow_input_context + "\n\n" + packet_context
                                )
                            else:
                                workflow_input_context = packet_context
                        developer_context = run_data.get("developer_context")
                        if developer_context:
                            try:
                                from distr.core.developer_context import (
                                    format_developer_context_dict_for_prompt,
                                )

                                developer_context_text = format_developer_context_dict_for_prompt(
                                    developer_context,
                                    max_chars=2200,
                                )
                                if developer_context_text:
                                    if workflow_input_context:
                                        workflow_input_context = (
                                            workflow_input_context
                                            + "\n\n"
                                            + developer_context_text
                                        )
                                    else:
                                        workflow_input_context = developer_context_text
                            except Exception as ce:
                                logger.debug("_build_agent_prompt: developer context render failed: %s", ce)
            except Exception as e:
                logger.debug("_build_agent_prompt: failed loading ticket metadata context: %s", e)

        # ── Inject run context (ticket/board/project context) from start_workflow_run(context=...) ──
        if run_id is not None:
            try:
                from distr.core.workflow.dispatcher import _runs_lock, _active_runs
                with _runs_lock:
                    run_ctx_obj = _active_runs.get(run_id)
                if run_ctx_obj:
                    # Legacy free-form context string — only use as fallback when no rich
                    # ticket context was already assembled from the DB (ticket_cli_context).
                    # Previously this overwrote the richer context, causing the agent to see
                    # only "Ticket: title\nDescription: ..." instead of the full structured data.
                    if (run_ctx_obj.context_prefix or "").strip() and not workflow_input_context:
                        workflow_input_context = run_ctx_obj.context_prefix.strip()
                    # Structured WorkflowRunContext — inject parent session data
                    if run_ctx_obj.run_ctx is not None:
                        structured = run_ctx_obj.run_ctx.as_context_string()
                        if structured:
                            if workflow_input_context:
                                workflow_input_context = (
                                    workflow_input_context + "\n\n" + structured
                                )
                            else:
                                workflow_input_context = structured
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load run context prefix: %s", e)

        # ── Build the compact handoff packet ──
        step_instruction = step_data["instruction"].strip()
        step_title = step_data.get("name", f"Step {step_index + 1}")
        step_description = step_data.get("description", "")
        from distr.core.workflow.ticket_contract import step_scope_overlay

        applicability = step_scope_overlay(step_title, workflow_input_context)
        if applicability:
            step_instruction = f"{step_instruction}\n\n[Ticket-specific applicability]\n{applicability}"
        step_config = step_data.get("config") or {}
        if isinstance(step_config, str):
            try:
                step_config = json.loads(step_config) or {}
            except Exception:
                step_config = {}
        guardrail_text = str(step_config.get("guardrail") or "").strip()

        # Prepend step description if it exists (adds "why" context)
        if step_description:
            step_instruction = f"{step_description}\n\n{step_instruction}"
        if guardrail_text:
            step_instruction = f"{step_instruction}\n\n[STEP GUARDRAILS]\n{guardrail_text}"
        failure_checklist = step_config.get("failure_checklist")
        if failure_checklist:
            if isinstance(failure_checklist, list):
                checklist_text = "\n".join(
                    f"- {str(item).strip().lstrip('-').strip()}"
                    for item in failure_checklist
                    if str(item).strip()
                )
            else:
                checklist_text = str(failure_checklist).strip()
            if checklist_text:
                step_instruction = (
                    f"{step_instruction}\n\n[VALIDATION FAILURE CHECKLIST]\n{checklist_text}"
                )
        tools = step_config.get("tools") if isinstance(step_config.get("tools"), list) else []
        other_tool = str(step_config.get("other_tool") or "").strip()
        if other_tool and "other" in [str(t).lower() for t in tools]:
            step_instruction = f"{step_instruction}\n\n[ADDITIONAL TOOL]\n{other_tool}"

        loop_context_summary = ""
        if run_id is not None:
            try:
                from distr.core.workflow.planning import build_loop_context_summary

                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                    run_data = json.loads(run.run_data or "{}") if run and run.run_data else {}
                    loop_contract = run_data.get("loop_contract") or {}
                    if not loop_contract and wf and getattr(wf, "workflow_input", None):
                        try:
                            loop_contract = json.loads(wf.workflow_input or "{}") or {}
                        except Exception:
                            loop_contract = {}
                    iteration = int(run_data.get("loop_iteration") or 0)
                    ticket_title = str(run_data.get("ticket_title") or "").strip()
                    if not ticket_title and getattr(run, "ticket_id", None):
                        try:
                            from distr.core.db.kanban import KanbanTicket

                            ticket_row = (
                                db.query(KanbanTicket)
                                .filter(KanbanTicket.id == int(run.ticket_id))
                                .first()
                            )
                            if ticket_row and ticket_row.title:
                                ticket_title = str(ticket_row.title).strip()
                        except Exception:
                            pass
                    loop_context_summary = build_loop_context_summary(
                        loop_contract,
                        iteration,
                        ticket_title=ticket_title,
                    )
            except Exception as e:
                logger.debug("_build_agent_prompt: loop context failed: %s", e)

        steering_context = ""
        if run_id is not None:
            try:
                from distr.core.workflow.steering_memory import build_steering_context_for_run_id

                steering_context = build_steering_context_for_run_id(run_id)
            except Exception as e:
                logger.debug("_build_agent_prompt: steering context failed: %s", e)

        execution_packet_context = self._build_workflow_execution_packet_context(
            step_data,
            run_id,
            workflow_id,
        )
        from distr.core.workflow.handoff_packet import StepHandoffPacket, select_relevant_memory
        from distr.core.workflow.step_iteration import HARNESS_REPORT_TEMPLATE

        objective_context = (
            f"{workflow_description}\n\nWorkflow input:\n{workflow_input_context}".strip()
            if workflow_input_context
            else (workflow_description or "Complete the requested workflow.")
        )
        constraints = [
            context_rules,
            loop_context_summary,
            steering_context,
            "Stay on the linked ticket and current workflow step. Use project-local rules before generic assumptions. "
            "If required files or attachments are missing, report needs_input. Keep changes minimal, evidence-backed, and reversible.",
        ]
        memory_candidates = [
            *list(execution_packet_context.get("memory_candidates") or []),
            context_rules,
            steering_context,
        ]
        memory_facts = select_relevant_memory(
            memory_candidates,
            query=f"{step_title}\n{step_instruction}\n{objective_context[:2200]}",
        )
        artifact_refs: list[str] = []
        prior_outcomes: list[dict[str, Any]] = []
        for item in prior_results:
            artifact_refs.extend(item.get("artifact_paths") or [])
            prior_outcomes.append({
                "title": item.get("title") or "Prior step",
                "status": item.get("status") or "completed",
                "summary": item.get("result") or "",
            })
        packet = StepHandoffPacket(
            identity=dict(execution_packet_context.get("identity") or {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "step_id": step_id,
                "step": step_title,
                "position": f"{step_index + 1}/{total_steps}",
            }),
            objective=objective_context,
            current_step={"title": step_title, "instruction": step_instruction},
            workflow_map=coordination_map,
            constraints=[item for item in constraints if str(item or "").strip()],
            prior_outcomes=prior_outcomes,
            artifact_refs=artifact_refs,
            memory_refs=list(execution_packet_context.get("memory_refs") or []),
            memory_facts=memory_facts,
            continuation=continuation_input,
            return_contract=HARNESS_REPORT_TEMPLATE,
        )
        prompt, telemetry = packet.render(max_chars=8_000)
        self._record_context_telemetry(run_id, step_id, telemetry)

        logger.info(
            "_build_agent_prompt: step_id=%s run_id=%s prior_results=%d context_rules_len=%d workflow_input_len=%d feedback_len=%d packet_chars=%d",
            step_id,
            run_id,
            len(prior_results),
            len(context_rules or ""),
            len(workflow_input_context or ""),
            len(continuation_input or ""),
            len(prompt),
        )

        return prompt

    def _resolve_recording_name(self, step_data: Dict[str, Any], config: dict) -> Optional[str]:
        """Resolve the recording filename from config, step data, or linked action."""
        name = config.get("recording_name", "") or step_data.get("recording_filename", "")
        if name:
            return name
        action_id = config.get("recording_id") or step_data.get("action_id")
        if not action_id:
            return None
        try:
            from distr.core.db import Action
            with get_session() as db:
                action = db.query(Action).filter(Action.id == action_id).first()
                if action and action.recording_filename:
                    return action.recording_filename
        except Exception as e:
            logger.warning("Could not load linked action %s: %s", action_id, e)
        return None

    def _get_run_context(self, step_id: int, run_id: Optional[int]):
        """Look up the active _RunContext for a workflow run, if any."""
        from distr.core.workflow.dispatcher import _runs_lock, _active_runs
        if run_id is not None:
            with _runs_lock:
                ctx = _active_runs.get(run_id)
            if ctx:
                logger.debug("_get_run_context: found run_id=%s in _active_runs", run_id)
                return ctx
            else:
                logger.warning("_get_run_context: run_id=%s not found in _active_runs (keys=%s)",
                               run_id, list(_active_runs.keys()))

        # Fallback: look up run from step's workflow
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    with _runs_lock:
                        return _active_runs.get(run.id)
        return None

    def _generate_code(self, instruction: str, action_type: str,
                        run_id: Optional[int] = None) -> Optional[str]:
        """Generate code from instruction using the coding LLM.

        If run_id is provided, includes workflow context (prior results,
        feedback, description) so the generated code is contextually aware.
        """
        try:
            from distr.core.workflow_engine.code_generator import CodeGeneratorService
            from distr.core.workflow_engine.step_types import StepType
            step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE

            # Build context string from workflow run history
            context = None
            if run_id is not None:
                context_parts = []
                try:
                    with get_session() as db:
                        results = (
                            db.query(AutoWorkflowStepResult)
                            .filter(AutoWorkflowStepResult.run_id == run_id)
                            .order_by(AutoWorkflowStepResult.created_at)
                            .all()
                        )
                        for sr in results:
                            s = sr.step
                            title = s.name if s else f"Step {sr.step_id}"
                            result = (sr.agent_response or "").strip()
                            if result:
                                context_parts.append(f"{title}: {result[:500]}")

                        run = db.query(AutoWorkflowRun).filter(
                            AutoWorkflowRun.id == run_id).first()
                        if run and run.run_data:
                            run_data = json.loads(run.run_data or "{}")
                            feedback = run_data.get("feedback", "")
                            if feedback and feedback.strip():
                                context_parts.append(f"User feedback: {feedback.strip()[:500]}")
                except Exception as e:
                    logger.debug("_generate_code: failed to load context: %s", e)

                if context_parts:
                    context = "\n".join(context_parts)

            return CodeGeneratorService().generate_code(instruction, step_type, context=context)
        except Exception as e:
            logger.error("Code generation failed: %s", e)
            return None

    @staticmethod
    def _extract_attachment_paths(text: str) -> List[str]:
        if not text:
            return []
        # Capture absolute file paths that end in common media extensions.
        pattern = r"(/[^ \n\t\"']+\.(?:png|jpg|jpeg|webp|gif|mp4|mov|m4a|wav|mp3))"
        hits = re.findall(pattern, text, flags=re.IGNORECASE)
        deduped: List[str] = []
        for p in hits:
            if p not in deduped:
                deduped.append(p)
        return deduped

    # ── computer_use step type ────────────────────────────────────────────────

    def _run_computer_use(
        self,
        step_data: Dict[str, Any],
        config: dict,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Autonomous vision-action loop for the computer_use step type.

        Unlike agent_instruction (which burns orchestration LLM tokens on every
        micro-decision), this handler owns the loop itself:

          screenshot → qwen3-vl decides action → sidecar executes → repeat

        The orchestration model is only called when the loop escalates.
        """
        goal = (config.get("goal") or step_data.get("instruction") or "").strip()
        if not goal:
            return {"output": "No goal provided for computer_use step.", "passed": False}

        max_iter = min(int(config.get("max_iterations", 15)), 25)
        stuck_threshold = int(config.get("stuck_threshold", 3))
        escalate = bool(config.get("escalate_on_ambiguity", True))
        resize_w = int(config.get("screenshot_resize_width", 1280))

        iteration_log: List[Dict[str, Any]] = []
        consecutive_stuck = 0

        logger.info("ComputerUse[%s]: starting — goal=%r max_iter=%d", run_id, goal[:80], max_iter)

        for i in range(max_iter):
            # ── 1. Capture + resize screenshot ──────────────────────────────
            screenshot_b64 = self._cu_capture_screenshot(resize_w)
            if not screenshot_b64:
                return {"output": "Failed to capture screenshot — is the sidecar running?", "passed": False}

            # ── 2. Ask vision model what to do next ─────────────────────────
            action = self._cu_decide_action(goal, screenshot_b64, iteration_log, i)

            action_type = action.get("type", "unknown")
            logger.info("ComputerUse[%s]: iter %d action=%s desc=%r",
                        run_id, i + 1, action_type, action.get("description", "")[:60])

            # ── 3. Check terminal states ─────────────────────────────────────
            if action_type == "finished":
                summary = self._cu_format_summary(goal, iteration_log, action.get("reason", "Goal achieved."))
                return {"output": summary, "passed": True}

            if action_type == "stuck":
                consecutive_stuck += 1
                iteration_log.append({"i": i + 1, "type": "stuck", "description": action.get("reason", "")})
                if consecutive_stuck >= stuck_threshold:
                    if escalate:
                        escalation = self._cu_escalate(goal, screenshot_b64, iteration_log, step_data, run_id)
                        if escalation:
                            return escalation
                    return {
                        "output": self._cu_format_summary(
                            goal, iteration_log,
                            f"Stuck after {i + 1} iterations: {action.get('reason', '')}",
                        ),
                        "passed": False,
                    }
                continue
            else:
                consecutive_stuck = 0

            # ── 4. Execute the action via sidecar ────────────────────────────
            exec_result = self._cu_execute_action(action)
            iteration_log.append({
                "i": i + 1,
                "type": action_type,
                "description": action.get("description", ""),
                "result": exec_result[:200],
            })

            import time as _time
            _time.sleep(0.4)  # Let the UI settle before next screenshot

        return {
            "output": self._cu_format_summary(
                goal, iteration_log,
                f"Reached max iterations ({max_iter}) without completing goal.",
            ),
            "passed": False,
        }

    # ── ComputerUse helpers ───────────────────────────────────────────────────

    def _cu_capture_screenshot(self, max_width: int = 1280) -> Optional[str]:
        """Capture the screen and return a base64 JPEG resized to max_width."""
        try:
            from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool
            result = call_sidecar_tool("capture_screen", {}, timeout=15)
            raw_b64 = result.get("data", "")
            if not raw_b64:
                return None

            import base64, io
            from PIL import Image
            raw = base64.b64decode(raw_b64)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = img.size
            if w > max_width:
                scale = max_width / w
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:
            logger.error("ComputerUse: screenshot failed: %s", exc)
            return None

    _CU_DECIDE_PROMPT = """\
You are a GUI automation agent. Your goal is: {goal}

Previous actions ({n} so far):
{history}

Look at the current screenshot and decide the single next action.

Respond with ONLY a JSON object — no text before or after it:

If you need to click something:
  {{"type":"click","description":"what you're clicking","norm_x":0.5,"norm_y":0.3}}

If you need to type text:
  {{"type":"type","text":"the text to type","description":"typing into X"}}

If you need to press keys:
  {{"type":"keys","keys":"cmd,s","description":"save the file"}}

If you need to scroll:
  {{"type":"scroll","direction":"down","norm_x":0.5,"norm_y":0.5,"description":"scrolling the list"}}

If the goal is fully achieved:
  {{"type":"finished","reason":"brief description of what was accomplished"}}

If you genuinely cannot proceed (error, wrong app, blocked):
  {{"type":"stuck","reason":"what is wrong and why you cannot continue"}}

IMPORTANT: norm_x and norm_y are 0.0–1.0 fractions of screen width/height.\
"""

    def _cu_decide_action(
        self,
        goal: str,
        screenshot_b64: str,
        iteration_log: List[Dict[str, Any]],
        iteration: int,
    ) -> Dict[str, Any]:
        """Ask the vision model to decide the next action. Returns a dict."""
        history = "\n".join(
            f"  {e['i']}. {e['type']}: {e.get('description', '')} → {e.get('result', '')[:80]}"
            for e in iteration_log[-6:]  # last 6 entries to stay within context
        ) or "  (none yet)"

        prompt = self._CU_DECIDE_PROMPT.format(
            goal=goal, n=iteration, history=history,
        )

        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            ollama_url = settings.get("ollama_url", "http://localhost:11434/")
            vision_model = settings.get("vision_llm_model") or "qwen3-vl:2b"

            import requests as _req
            resp = _req.post(
                ollama_url.rstrip("/") + "/api/chat",
                json={
                    "model": vision_model,
                    "messages": [{"role": "user", "content": prompt, "images": [screenshot_b64]}],
                    "stream": False,
                },
                timeout=60,
            )
            content = resp.json()["message"]["content"].strip()

            # Extract JSON from response (model may wrap it in markdown)
            import re
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                return json.loads(m.group())
            return {"type": "stuck", "reason": f"Vision model returned non-JSON: {content[:200]}"}
        except Exception as exc:
            logger.error("ComputerUse: vision decision failed: %s", exc)
            return {"type": "stuck", "reason": f"Vision API error: {exc}"}

    def _cu_execute_action(self, action: Dict[str, Any]) -> str:
        """Execute a single computer-use action via the sidecar HTTP API."""
        try:
            from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool
            action_type = action.get("type", "")

            if action_type == "click":
                result = call_sidecar_tool("click_at", {
                    "norm_x": action.get("norm_x", 0.5),
                    "norm_y": action.get("norm_y", 0.5),
                    "action": "click",
                }, timeout=10)
                return f"Clicked at ({action.get('norm_x'):.2f}, {action.get('norm_y'):.2f}): {result.get('success', False)}"

            elif action_type == "type":
                result = call_sidecar_tool("type_clipboard", {
                    "text": action.get("text", ""),
                }, timeout=15)
                return f"Typed {len(action.get('text',''))} chars: {result.get('success', False)}"

            elif action_type == "keys":
                result = call_sidecar_tool("press_keys", {
                    "keys": action.get("keys", ""),
                }, timeout=10)
                return f"Pressed {action.get('keys')}: {result.get('success', False)}"

            elif action_type == "scroll":
                params: Dict[str, Any] = {
                    "direction": action.get("direction", "down"),
                    "amount": int(action.get("amount", 3)),
                }
                if "norm_x" in action:
                    params["norm_x"] = action["norm_x"]
                    params["norm_y"] = action.get("norm_y", 0.5)
                result = call_sidecar_tool("scroll", params, timeout=10)
                return f"Scrolled {action.get('direction')}: {result.get('success', False)}"

            else:
                return f"Unknown action type: {action_type}"
        except Exception as exc:
            logger.error("ComputerUse: action execution failed: %s", exc)
            return f"Execution error: {exc}"

    def _cu_escalate(
        self,
        goal: str,
        screenshot_b64: str,
        iteration_log: List[Dict[str, Any]],
        step_data: Dict[str, Any],
        run_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Escalate to the orchestration LLM when the vision loop is stuck.

        Builds a rich prompt summarising what has been tried and asks the main
        model for guidance or a corrective action, then re-enters the loop.
        """
        logger.info("ComputerUse[%s]: escalating to orchestration model", run_id)
        history_text = "\n".join(
            f"  {e['i']}. {e['type']}: {e.get('description', '')} → {e.get('result', '')[:120]}"
            for e in iteration_log
        )
        escalation_instruction = (
            f"A computer-use automation step is stuck trying to achieve this goal:\n"
            f"  {goal}\n\n"
            f"Actions taken so far:\n{history_text}\n\n"
            f"Look at the current screen state and tell me: what should happen next? "
            f"Either provide a specific action instruction or explain what is blocking progress."
        )
        try:
            from distr.core.workflow_agent import WorkflowAgent
            import concurrent.futures
            agent = WorkflowAgent()

            def _run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(agent.execute(escalation_instruction))
                finally:
                    loop.close()
                    agent.shutdown()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                guidance = pool.submit(_run).result(timeout=120)

            if guidance:
                iteration_log.append({"i": "ESC", "type": "escalation", "description": guidance[:300]})
                # Don't return yet — let the caller decide based on guidance content
                logger.info("ComputerUse: orchestration guidance: %s", guidance[:200])
        except Exception as exc:
            logger.error("ComputerUse: escalation failed: %s", exc)

        return None  # Caller will handle the stuck return

    @staticmethod
    def _cu_format_summary(goal: str, iteration_log: List[Dict[str, Any]], final_status: str) -> str:
        """Format a human-readable run summary for the step result."""
        lines = [f"Goal: {goal}", f"Status: {final_status}", "", "Steps taken:"]
        for e in iteration_log:
            desc = e.get("description", "")
            result = e.get("result", "")
            lines.append(f"  {e['i']}. [{e['type']}] {desc}" + (f" → {result}" if result else ""))
        return "\n".join(lines)

    def _augment_agent_result_with_tool_evidence(self, result_text: str, workflow_agent) -> str:
        """Append tool evidence + attachments so workflow history/audit is actionable."""
        base = (result_text or "").strip() or "Step completed."
        try:
            msgs = list(workflow_agent.messages or [])
        except Exception:
            return base

        # Collect tool outputs from this step only (messages since last user prompt).
        recent_tool_outputs: List[str] = []
        for msg in reversed(msgs):
            role = (msg or {}).get("role")
            if role == "user":
                break
            if role == "tool":
                content = str((msg or {}).get("content") or "").strip()
                if content:
                    recent_tool_outputs.append(content[:600])
        recent_tool_outputs.reverse()

        attachment_paths = self._extract_attachment_paths(base)
        for chunk in recent_tool_outputs:
            for path in self._extract_attachment_paths(chunk):
                if path not in attachment_paths:
                    attachment_paths.append(path)

        out = base
        if recent_tool_outputs:
            preview = "\n".join(f"- {c}" for c in recent_tool_outputs[:4])
            out = f"{out}\n\nTool evidence:\n{preview}"
        if attachment_paths:
            out = f"{out}\n\nAttachments:\n" + "\n".join(f"- {p}" for p in attachment_paths)
        return out[:8000]
