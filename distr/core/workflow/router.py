"""
StepRouter — step routing after completion.

Verify result → store result → determine next step → advance or end run.
Extracted from complete_step() in service.py and _advance_workflow_orchestration() in workflow.py.

**Validates: Requirements 3, 4, 7**
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowRun,
    AutoWorkflowStep,
    AutoWorkflowStepResult,
)
from distr.core.workflow.verification import _run_verification
from distr.gui.web.workflow_events import increment_workflow_updated

logger = logging.getLogger(__name__)


class StepRouter:
    """Decide what happens after a step completes.

    Two public entry points:

    - ``route(step_id, result, passed, run_id)`` — verify → store → route.
    - ``resume_from_feedback(step_id, run_id, feedback)`` — resume a waiting step.
    """

    # ── Public API ──────────────────────────────────────────────────

    def route(
        self,
        step_id: int,
        result: str,
        passed: bool,
        run_id: int,
        *,
        skip_wait: bool = False,
    ) -> Dict[str, Any]:
        """After a step completes: verify → store result → determine next step.

        Returns one of:
            {"action": "next_step", "step_id": <id>}
            {"action": "end_run", "status": "completed"}
            {"action": "waiting", "notify_main_agent": True}

        Set ``skip_wait=True`` when resuming from feedback to avoid re-entering
        the wait state for a ``wait_for_continue`` step that has already waited.
        """
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id,
            ).first()
            if not step:
                return {"action": "end_run", "status": "failed", "error": "Step not found"}

            # ── wait_for_continue gate ──
            # Skip when resuming from feedback (the step has already waited)
            if step.wait_for_continue and not skip_wait:
                return self._enter_wait_state(db, step, run_id, result, passed)

            # ── verify ──
            verified_passed = _run_verification(step, result, passed)
            status = "passed" if verified_passed else "failed"

            # ── store result ──
            step.status = status
            step.result = result
            db.add(AutoWorkflowStepResult(
                step_id=step_id,
                run_id=run_id,
                agent_response=result,
                status=status,
            ))

            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if not run:
                db.commit()
                increment_workflow_updated()
                return {"action": "end_run", "status": status}

            # ── determine next step ──
            decision = self._determine_next(db, step, run, verified_passed, result)
            db.commit()

        increment_workflow_updated()
        return decision

    def resume_from_feedback(
        self,
        step_id: int,
        run_id: int,
        feedback: str,
    ) -> Dict[str, Any]:
        """Resume a waiting step with user/main-agent feedback."""
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if not run:
                return {"action": "end_run", "status": "failed", "error": "Run not found"}
            if run.status != "waiting":
                return {"action": "end_run", "status": "failed",
                        "error": f"Run is not waiting (status: {run.status})"}

            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id,
            ).first()
            if not step or step.status != "waiting":
                return {"action": "end_run", "status": "failed",
                        "error": "No waiting step found"}

            # Recover stored result/passed from run_data
            run_data = json.loads(run.run_data or "{}")
            stored_result = run_data.get("waiting_result", "")
            stored_passed = run_data.get("waiting_passed", True)

            # Append feedback to the result
            if feedback.strip():
                stored_result = f"{stored_result}\n\n[FEEDBACK]: {feedback.strip()}"
                # Persist feedback in run_data for downstream steps
                run_data["feedback"] = feedback.strip()
                run.run_data = json.dumps(run_data)

            # Transition back to running
            run.status = "running"
            step.status = "running"
            db.commit()

        increment_workflow_updated()

        # Re-route with the enriched result, skipping the wait gate
        # since the step has already waited for and received feedback.
        return self.route(step_id, stored_result, stored_passed, run_id, skip_wait=True)

    # ── Internal: determine next step ───────────────────────────────

    def _determine_next(
        self,
        db,
        step: AutoWorkflowStep,
        run: AutoWorkflowRun,
        verified_passed: bool,
        result: str,
    ) -> Dict[str, Any]:
        """Pick the next step based on routing_mode. Mutates run in-place."""
        routing_mode = (step.routing_mode or "static").strip().lower()

        if routing_mode == "agent_decision":
            next_step_id = self._agent_route(db, step, result, verified_passed)
        else:
            next_step_id = self._static_route(step, verified_passed)

        # null / -1 → end run
        if next_step_id is None or next_step_id == -1:
            return self._end_run(run)

        next_step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == next_step_id,
        ).first()
        if not next_step:
            return self._end_run(run)

        # Self-routing guard
        if next_step.id == step.id:
            logger.warning(
                "Step %d routes to itself — ending run to prevent infinite loop.",
                step.id,
            )
            return self._end_run(run, warning="Infinite loop prevented")

        # Advance
        run.current_step_id = next_step.id
        return {
            "action": "next_step",
            "step_id": next_step.id,
            "wait_before_next": step.wait_before_next or 0,
        }

    # ── Static routing ──────────────────────────────────────────────

    @staticmethod
    def _static_route(step: AutoWorkflowStep, passed: bool) -> Optional[int]:
        """Follow on_pass_goto / on_fail_goto. None or -1 means end."""
        goto = step.on_pass_goto if passed else step.on_fail_goto
        return goto

    # ── Agent routing ───────────────────────────────────────────────

    def _agent_route(
        self,
        db,
        step: AutoWorkflowStep,
        result: str,
        passed: bool,
    ) -> Optional[int]:
        """Ask the LLM which step to go to next."""
        wf = db.query(AutoWorkflow).filter(
            AutoWorkflow.id == step.workflow_id,
        ).first()
        all_steps = sorted(wf.steps, key=lambda s: s.position) if wf else []
        return self._agent_route_decision(step, result, passed, all_steps)

    @staticmethod
    def _agent_route_decision(
        step: AutoWorkflowStep,
        result: str,
        passed: bool,
        all_steps: List[AutoWorkflowStep],
    ) -> Optional[int]:
        """Build an LLM prompt and parse the response into a step ID or None."""
        step_descriptions = []
        for s in all_steps:
            if s.id == step.id:
                continue
            desc = f'  - Step ID {s.id}: "{s.name}" (position #{s.position})'
            if s.description:
                desc += f" — {s.description}"
            step_descriptions.append(desc)

        if not step_descriptions:
            return None

        steps_list = "\n".join(step_descriptions)
        routing_prompt = (step.routing_prompt or "").strip()
        status_word = "PASSED" if passed else "FAILED"

        prompt = (
            "You are a workflow routing agent. A step just completed and you "
            "need to decide what happens next.\n\n"
            f'COMPLETED STEP: "{step.name}" (ID {step.id})\n'
            f"STATUS: {status_word}\n"
            f"RESULT:\n{result}\n\n"
        )
        if routing_prompt:
            prompt += f"ROUTING INSTRUCTIONS:\n{routing_prompt}\n\n"
        prompt += (
            f"AVAILABLE NEXT STEPS:\n{steps_list}\n\n"
            'Respond with ONLY one of the following:\n'
            '- A step ID number (e.g. "42") to go to that step\n'
            '- "END" to finish the workflow\n\n'
            "Your decision:"
        )

        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(prompt)
            if response:
                return StepRouter._parse_routing_response(response, all_steps, step.id)
        except ImportError:
            pass
        except Exception as e:
            logger.error("Agent routing decision failed: %s", e, exc_info=True)

        logger.warning("Agent routing: no LLM available, defaulting to END")
        return None

    @staticmethod
    def _parse_routing_response(
        response: str,
        all_steps: List[AutoWorkflowStep],
        current_step_id: int,
    ) -> Optional[int]:
        """Parse the LLM routing response into a step ID or None (end)."""
        text = response.strip().upper()
        if text == "END" or text.startswith("END"):
            return None

        numbers = re.findall(r"\d+", text)
        if numbers:
            candidate_id = int(numbers[0])
            valid_ids = {s.id for s in all_steps if s.id != current_step_id}
            if candidate_id in valid_ids:
                return candidate_id
            # Try matching by position
            for s in all_steps:
                if s.position == candidate_id and s.id != current_step_id:
                    return s.id

        logger.warning(
            "Could not parse agent routing response: '%s', defaulting to END",
            response,
        )
        return None

    # ── Wait state ──────────────────────────────────────────────────

    def _enter_wait_state(
        self,
        db,
        step: AutoWorkflowStep,
        run_id: int,
        result: str,
        passed: bool,
    ) -> Dict[str, Any]:
        """Put step + run into waiting state and notify the main agent."""
        step.status = "waiting"
        step_id = step.id
        step_name = step.name
        workflow_id = step.workflow_id

        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == run_id,
        ).first()
        if run:
            run.status = "waiting"
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = result
            run_data["waiting_passed"] = passed
            run.run_data = json.dumps(run_data)
        db.commit()

        increment_workflow_updated()
        self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
        self._notify_main_agent(workflow_id, run_id, step_name, result)

        return {"action": "waiting", "notify_main_agent": True, "run_id": run_id}

    # ── Event emission ─────────────────────────────────────────────────

    @staticmethod
    def _emit_waiting_for_feedback(
        step_id: int,
        workflow_id: int,
        run_id: int,
        result: str,
    ) -> None:
        """Emit ``step_waiting_for_feedback`` signal so the main agent can react."""
        try:
            from distr.core.signals import signal_manager
            signal_manager.step_waiting_for_feedback.emit(
                step_id, workflow_id, run_id, result,
            )
        except Exception as e:
            logger.debug("Could not emit step_waiting_for_feedback: %s", e)

    # ── End run helper ──────────────────────────────────────────────

    @staticmethod
    def _end_run(
        run: AutoWorkflowRun,
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark run as completed and return end_run decision."""
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        decision: Dict[str, Any] = {
            "action": "end_run",
            "status": "completed",
            "run_id": run.id,
        }
        if warning:
            decision["warning"] = warning
        return decision

    # ── Notifications ───────────────────────────────────────────────

    @staticmethod
    def _notify_main_agent(
        workflow_id: int,
        run_id: Optional[int],
        step_name: str,
        result: str,
    ) -> None:
        """Speak result via TTS and queue a report for the main agent."""
        # TTS notification
        try:
            from distr.core.signals import signal_manager
            speak_text = result.strip()[:400]
            if len(result.strip()) > 400:
                speak_text += "..."
            signal_manager.speak_text_directly.emit(
                f"{speak_text}. Step '{step_name}' is now waiting for your input.",
            )
        except Exception as e:
            logger.debug("Could not speak wait notification: %s", e)

        # Queue report for the main agent
        try:
            from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
            WorkflowAgentBridge().queue_report_to_agent(
                workflow_id,
                f"Workflow step '{step_name}' completed and is now WAITING "
                f"for your input. Run ID: {run_id}. "
                f"Result: {result[:500]}",
            )
        except Exception as e:
            logger.debug("Could not queue wait report: %s", e)
