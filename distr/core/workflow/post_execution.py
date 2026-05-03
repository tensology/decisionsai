"""PostExecutionMixin — post-execution: result recording, routing, notifications.

Extracted from StepDispatcher for clarity. All methods remain accessible
via self because StepDispatcher inherits from this mixin.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun, AutoWorkflowStepResult
from distr.core.workflow.verification import _run_verification
from distr.gui.web.workflow_events import increment_workflow_updated

logger = logging.getLogger(__name__)


class PostExecutionMixin:
    """Provides post-execution logic: recording results, routing, waiting, audit."""

    def _record_result(
        self,
        step_id: int,
        run_id: Optional[int],
        result_text: str,
        passed: bool,
        skip_wait: bool = False,
    ) -> None:
        """Run verification, store result, update step status, push websocket."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step:
                return
            # Check wait_for_continue before finalizing
            if step.wait_for_continue and not skip_wait:
                if self._enter_wait_state(step_id, result_text, passed, run_id=run_id):
                    return
            verified_passed = _run_verification(step, result_text, passed)
            status = "passed" if verified_passed else "failed"
            step.status = status
            step.result = result_text
            db.add(AutoWorkflowStepResult(
                step_id=step_id, run_id=run_id,
                agent_response=result_text, status=status,
            ))
            db.commit()
        increment_workflow_updated()

    # ── Post-execution: routing, recording, notifications ────────────

    def _record_result_and_route(
        self,
        step_id: int,
        run_id: Optional[int],
        result_text: str,
        passed: bool,
        skip_wait: bool = False,
    ) -> None:
        """Record result AND route to the next step (for async step completion).

        This is the critical fix: when an async step (agent_instruction, recording)
        completes via callback, we need to not only store the result but also
        advance the workflow to the next step. Without this, the workflow stalls
        after every async step.
        """
        # Guard against double-routing the same step
        with self._routed_lock:
            if step_id in self._routed_steps:
                logger.warning("_record_result_and_route: step %s already routed, skipping", step_id)
                return
            self._routed_steps.add(step_id)

        # Route to the next step if we're in a workflow run.
        # NOTE: StepRouter.route() is the single writer for step results/status
        # in workflow mode to avoid duplicate result rows and duplicated context.
        if run_id is not None:
            try:
                from distr.core.workflow.router import StepRouter
                from distr.core.workflow.dispatcher import StepDispatcher, complete_run, _update_workflow_thread_step
                router = StepRouter()
                decision = router.route(step_id, result_text, passed, run_id, skip_wait=skip_wait)
                self._append_workflow_step_audit(step_id, run_id, result_text, passed)

                if decision.get("action") == "next_step":
                    next_step_id = decision["step_id"]
                    wait_before = decision.get("wait_before_next", 0)
                    if wait_before > 0:
                        import time
                        time.sleep(wait_before)

                    # Update env vars for the next step
                    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(next_step_id)
                    _update_workflow_thread_step(next_step_id)

                    # Dispatch the next step — StepDispatcher.run_in_workflow sets status to
                    # "running". Pre-marking "running" here tripped the run_in_workflow
                    # idempotency guard (same pattern as start_workflow_run's first step).
                    increment_workflow_updated()

                    dispatcher = StepDispatcher()
                    dispatcher.run_in_workflow(next_step_id, run_id)

                elif decision.get("action") == "end_run":
                    status = decision.get("status", "completed")
                    complete_run(run_id, status)

                elif decision.get("action") == "waiting":
                    pass  # Step entered wait state — nothing more to do

            except Exception as e:
                logger.error("Routing failed after step %s: %s", step_id, e, exc_info=True)
                try:
                    from distr.core.workflow.dispatcher import complete_run
                    complete_run(run_id, "failed")
                except Exception:
                    pass

    def _append_workflow_step_audit(
        self,
        step_id: int,
        run_id: int,
        result_text: str,
        passed: bool,
    ) -> None:
        """Mirror workflow step outputs to the workflow audit trail for the UI."""
        try:
            from distr.core.workflow.audit import append_audit_step

            with get_session() as db:
                step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                if not step or not run:
                    return
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == step.workflow_id).first()
                chat_id = wf.chat_id if wf else None
                if not chat_id:
                    return

                status = "passed" if passed else "failed"
                instruction = (step.instruction or "").strip() or step.name or f"Step {step_id}"
                label = f"{step.name or 'Step'} (workflow step)"
                append_audit_step(
                    chat_id=chat_id,
                    tool_name=label,
                    instruction=instruction,
                    result=(result_text or "")[:4000],
                    status=status,
                )
        except Exception:
            logger.debug("Failed to append workflow step audit", exc_info=True)

    def _notify_isolated_step_result(self, step_data: Dict[str, Any], passed: bool, result_text: str) -> None:
        """Notify bridge/voice agent when an isolated step finishes.

        Isolated runs (triggered from the Workflows UI "Run step") do not create
        workflow run records, so they never hit the normal completion bridge path.
        This keeps isolated-step outcomes visible to the orchestrator and spoken
        feedback loop, including success cases.
        """
        try:
            from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge

            workflow_id = step_data.get("workflow_id")
            if workflow_id is None:
                return
            from distr.core.workflow.context_limits import truncate_step_result

            step_title = (step_data.get("name") or "").strip() or f"Step {step_data.get('id')}"
            safe_result = truncate_step_result(
                result_text or ("Step completed." if passed else "Step failed with no details.")
            )

            run_result = {
                "session_id": workflow_id,
                "run_id": None,
                "success": passed,
                "cancelled": False,
                "steps_summary": [{
                    "title": step_title,
                    "status": "passed" if passed else "failed",
                    "result": safe_result,
                }],
            }
            WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
        except Exception:
            logger.debug("Could not notify isolated step result", exc_info=True)

    def _enter_wait_state(
        self,
        step_id: int,
        result_text: str,
        passed: bool,
        run_id: Optional[int] = None,
    ) -> bool:
        """Put step into waiting state if wait_for_continue is set. Returns True if entered."""
        handoff = self._build_wait_handoff_text(step_name="", result_text=result_text, run_id=run_id)
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step or not step.wait_for_continue:
                return False
            step.status = "waiting"
            step_name, workflow_id = step.name, step.workflow_id
            handoff = self._build_wait_handoff_text(step_name=step_name, result_text=result_text, run_id=run_id)
            if run_id is not None:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == run_id,
                ).first()
            else:
                # Legacy fallback path for isolated callers that do not pass run_id.
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
            resolved_run_id = None
            if run:
                run.status = "waiting"
                resolved_run_id = run.id
                run_data = json.loads(run.run_data or "{}")
                run_data["waiting_result"] = result_text
                run_data["waiting_passed"] = passed
                run_data["waiting_prompt"] = handoff["prompt"]
                run.run_data = json.dumps(run_data)
            # Persist a readable wait-state handoff in step history so users can
            # see exactly what the step asked for (not just raw output).
            db.add(AutoWorkflowStepResult(
                step_id=step_id,
                run_id=resolved_run_id,
                agent_response=handoff["history_entry"],
                status="waiting",
            ))
            db.commit()
        increment_workflow_updated()
        # Notify main agent via TTS
        try:
            from distr.core.signals import speak_text_directly_event_queue
            speak_text_directly_event_queue(handoff["tts"])
        except Exception as e:
            logger.debug("Could not speak wait notification: %s", e)
        # Queue report for the main agent
        try:
            from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge
            WorkflowAgentBridge().queue_report_to_agent(
                workflow_id,
                handoff["report"].replace("__RUN_ID__", str(resolved_run_id)),
            )
        except Exception as e:
            logger.debug("Could not queue wait report: %s", e)
        return True

    @staticmethod
    def _build_wait_handoff_text(step_name: str, result_text: str, run_id: Optional[int]) -> Dict[str, str]:
        """Build curated wait-state text for TTS, history, and agent report."""
        clean_result = (result_text or "").strip()
        if not clean_result:
            clean_result = "Step completed with no detailed output."
        summary = clean_result[:280]
        if len(clean_result) > 280:
            summary += "..."
        step_label = step_name or "workflow step"
        prompt = (
            f"{step_label} is waiting for your decision. "
            "Reply with what should happen next, for example: continue, retry, skip, or provide extra instructions."
        )
        tts = f"{summary}. {prompt}"
        report = (
            f"[WORKFLOW_WAIT_HANDOFF]\n"
            f"step_name: {step_label}\n"
            f"run_id: __RUN_ID__\n"
            f"status: waiting_for_user_input\n"
            f"step_result_summary: {summary}\n"
            f"step_result_full: {clean_result[:1500]}\n\n"
            "Orchestrator instructions:\n"
            "1) Relay the step result faithfully; do not re-style or expand scope.\n"
            "2) Ask one clear follow-up question for user input.\n"
            "3) After user reply, call continue_workflow with that reply."
        )
        history_entry = (
            f"{clean_result}\n\n"
            f"[WAITING FOR INPUT]\n{prompt}"
        )
        if run_id is not None:
            history_entry = f"{history_entry}\nRun ID: {run_id}"
        return {
            "prompt": prompt,
            "tts": tts,
            "report": report,
            "history_entry": history_entry,
        }
