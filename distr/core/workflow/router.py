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
from distr.core.kanban.result_packet import append_workflow_step_to_packet
from distr.core.kanban.ticket_audit import append_ticket_audit_entry
from distr.gui.web.workflow_events import increment_workflow_updated
from distr.gui.web.kanban_events import increment_kanban_updated

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
            step_result_row = AutoWorkflowStepResult(
                step_id=step_id,
                run_id=run_id,
                agent_response=result,
                status=status,
            )
            db.add(step_result_row)
            db.flush()

            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if not run:
                db.commit()
                increment_workflow_updated()
                return {"action": "end_run", "status": status}

            # ── determine next step ──
            decision = self._determine_next(db, step, run, verified_passed, result)
            # ── update canonical result packet in run_data ──
            try:
                run_data = json.loads(run.run_data or "{}")
            except Exception:
                run_data = {}
            packet = run_data.get("result_packet") or {}
            packet = append_workflow_step_to_packet(
                packet,
                step_name=step.name or f"Step {step.id}",
                step_status=status,
                step_result=result or "",
                run_status=(
                    decision.get("status")
                    if decision.get("action") == "end_run"
                    else "running"
                ),
            )
            run_data["result_packet"] = packet

            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=getattr(step_result_row, "id", None),
                    execution_lane="cursor",
                    status=status,
                    final_verdict=((packet.get("audit") or {}).get("final_verdict")),
                    summary=f"{step.name or f'Step {step_id}'}: {status}",
                    details=(result or "")[:3000],
                )
            run.run_data = json.dumps(run_data)
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
            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=None,
                    execution_lane="cursor",
                    status="running",
                    final_verdict="cannot_determine",
                    summary=f"{step.name or f'Step {step_id}'} resumed",
                    details=(feedback or "").strip()[:3000] or "Run resumed after waiting state.",
                )
                increment_kanban_updated(
                    board_id=getattr(run, "board_id", None),
                    event_type="ticket_workflow_status",
                    payload={
                        "ticket_id": int(run.ticket_id),
                        "run_id": run_id,
                        "status": "running",
                        "step_id": step_id,
                    },
                )
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
            next_step_id = self._static_route(db, step, verified_passed)

        # null / -1 → end run
        if next_step_id is None or next_step_id == -1:
            return self._end_run(run, status="completed" if verified_passed else "failed")

        next_step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == next_step_id,
        ).first()
        if not next_step:
            return self._end_run(run, status="completed" if verified_passed else "failed")

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
    def _static_route(db, step: AutoWorkflowStep, passed: bool) -> Optional[int]:
        """Follow explicit goto, otherwise default to next step by position."""
        goto = step.on_pass_goto if passed else step.on_fail_goto
        if goto is not None:
            return goto

        # Backward-compatible default: if no explicit route is configured,
        # advance to the next step in sequence.
        next_step = (
            db.query(AutoWorkflowStep)
            .filter(
                AutoWorkflowStep.workflow_id == step.workflow_id,
                AutoWorkflowStep.position > step.position,
            )
            .order_by(AutoWorkflowStep.position.asc())
            .first()
        )
        if next_step:
            return next_step.id
        return None

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
        handoff = self._build_wait_handoff_text(step_name=step_name, result_text=result, run_id=run_id)

        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == run_id,
        ).first()
        if run:
            run.status = "waiting"
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = result
            run_data["waiting_passed"] = passed
            run_data["waiting_prompt"] = handoff["prompt"]
            run.run_data = json.dumps(run_data)
        db.add(AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run_id,
            agent_response=handoff["history_entry"],
            status="waiting",
        ))
        if run and getattr(run, "ticket_id", None):
            append_ticket_audit_entry(
                db,
                ticket_id=int(run.ticket_id),
                run_id=run_id,
                step_id=step_id,
                step_result_id=None,
                execution_lane="cursor",
                status="waiting",
                final_verdict="cannot_determine",
                summary=f"{step_name or f'Step {step_id}'} waiting for input",
                details=(result or "")[:3000],
            )
            increment_kanban_updated(
                board_id=getattr(run, "board_id", None),
                event_type="ticket_workflow_status",
                payload={
                    "ticket_id": int(run.ticket_id),
                    "run_id": run_id,
                    "status": "waiting",
                    "step_id": step_id,
                },
            )
        db.commit()

        increment_workflow_updated()
        self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
        self._notify_main_agent(workflow_id, run_id, handoff)

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
        status: str = "completed",
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark run as completed and return end_run decision."""
        run.status = status
        run.completed_at = datetime.utcnow()
        decision: Dict[str, Any] = {
            "action": "end_run",
            "status": status,
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
        handoff: Dict[str, str],
    ) -> None:
        """Speak result via TTS and queue a report for the main agent."""
        # TTS notification
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
                handoff["report"].replace("__RUN_ID__", str(run_id)),
            )
        except Exception as e:
            logger.debug("Could not queue wait report: %s", e)

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
            f"[WAITING FOR INPUT]\n{prompt}\n"
            f"Run ID: {run_id if run_id is not None else 'unknown'}"
        )
        return {
            "prompt": prompt,
            "tts": tts,
            "report": report,
            "history_entry": history_entry,
        }
