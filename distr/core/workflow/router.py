"""
StepRouter — step routing after completion.

Verify result → store result → determine next step → advance or end run.
Extracted from complete_step() in service.py and _advance_workflow_orchestration() in workflow.py.

**Validates: Requirements 3, 4, 7**
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.time import utc_now_naive
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowRun,
    AutoWorkflowStep,
    AutoWorkflowStepResult,
)
from distr.core.workflow.verification import _run_verification, build_validation_snapshot
from distr.core.kanban.result_packet import append_workflow_step_to_packet
from distr.core.kanban.ticket_audit import append_ticket_audit_entry
from distr.core.workflow.chat_trace import record_workflow_chat_event
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
        skip_approval: bool = False,
    ) -> Dict[str, Any]:
        """After a step completes: verify → store result → determine next step.

        Returns one of:
            {"action": "next_step", "step_id": <id>}
            {"action": "end_run", "status": "completed"}
            {"action": "waiting", "notify_main_agent": True}
            {"action": "correction_retry", "step_id": <id>}

        Set ``skip_wait=True`` when resuming from feedback to avoid re-entering
        the wait state for a ``wait_for_continue`` step that has already waited.
        Set ``skip_approval=True`` when resuming after a human approval gate.
        """
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id,
            ).first()
            if not step:
                return {"action": "end_run", "status": "failed", "error": "Step not found"}

            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if run:
                try:
                    run_data = json.loads(run.run_data or "{}")
                except Exception:
                    run_data = {}
                if run_data.pop("ide_handoff_pending", False):
                    run_data["waiting_kind"] = "ide_handoff"
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, result, passed)
                if run_data.pop("route_approval_pending", False):
                    run_data["waiting_kind"] = "route_approval"
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, result, passed)

            # ── wait_for_continue gate ──
            # Skip when resuming from feedback (the step has already waited)
            if step.wait_for_continue and not skip_wait:
                return self._enter_wait_state(db, step, run_id, result, passed)

            # ── verify ──
            verify_project_id = None
            if run:
                try:
                    verify_run_data = json.loads(run.run_data or "{}") or {}
                    raw_project_id = verify_run_data.get("project_id")
                    verify_project_id = int(raw_project_id) if raw_project_id not in (None, "") else None
                except Exception:
                    verify_project_id = None
            ticket_context = ""
            standards_context = ""
            try:
                from distr.core.db.kanban import KanbanTicket
                from distr.core.workflow.standards_memory import build_standards_context

                if run and getattr(run, "ticket_id", None):
                    ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
                    if ticket:
                        ticket_context = f"{getattr(ticket, 'title', '')}\n{getattr(ticket, 'description', '')}".strip()
                if run:
                    try:
                        brief_data = json.loads(run.run_data or "{}") or {}
                        brief = (brief_data.get("ticket_workflow_brief") or "").strip()
                        if brief:
                            ticket_context = (
                                f"{ticket_context}\n\n{brief}".strip()
                                if ticket_context
                                else brief
                            )
                    except Exception:
                        pass
                standards_context = build_standards_context(
                    getattr(run.workflow, "context_rules", None) if run and run.workflow else None,
                    board_id=getattr(run, "board_id", None) if run else None,
                )
            except Exception:
                logger.debug("Could not build verification context", exc_info=True)

            verified_passed = _run_verification(
                step,
                result,
                passed,
                project_id=verify_project_id,
                ticket_context=ticket_context,
                standards_context=standards_context,
            )
            orchestrator_overlay = None
            try:
                from distr.core.orchestrator_validator import apply_orchestrator_validator_overlay
                orchestrator_overlay = apply_orchestrator_validator_overlay(
                    step=step,
                    result=result,
                    caller_passed=passed,
                    mechanical_passed=verified_passed,
                    standards_context=standards_context,
                    ticket_context=ticket_context,
                )
                if orchestrator_overlay is not None:
                    verified_passed = bool(orchestrator_overlay.get("passed"))
            except Exception:
                logger.debug("Hermes validator overlay skipped", exc_info=True)

            validation_snapshot = build_validation_snapshot(
                step, result, passed, verified_passed, project_id=verify_project_id
            )
            if orchestrator_overlay:
                validation_snapshot["orchestrator_validator"] = orchestrator_overlay
                if not verified_passed:
                    hint = (
                        orchestrator_overlay.get("correction_hint")
                        or orchestrator_overlay.get("explanation")
                        or ""
                    ).strip()
                    if hint:
                        validation_snapshot["correction_hint"] = hint
            if standards_context:
                validation_snapshot["standards_context"] = standards_context

            # ── approval gate (after verify, before marking passed) ──
            if step.require_approval and verified_passed and not skip_approval:
                return self._enter_approval_state(db, step, run_id, result, verified_passed)

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
                validation_snapshot=validation_snapshot,
            )
            run_data["result_packet"] = packet
            run.run_data = json.dumps(run_data)
            # Hermes is the cross-cutting ledger and writes through its own
            # session. Persist the canonical step result/run packet first so
            # Hermes validation and event rows cannot be blocked by this write
            # transaction, especially on SQLite-backed local installs.
            db.commit()
            validation_record_id = None
            correction_attempt_id = None
            correction_packet: dict[str, Any] = {}
            try:
                from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
                from distr.core.orchestrator import (
                    build_correction_packet,
                    create_correction_attempt,
                    record_validation,
                )

                latest_execution = (
                    db.query(ProjectExecutionSession)
                    .filter(ProjectExecutionSession.run_id == int(run_id))
                    .filter(ProjectExecutionSession.step_id == int(step_id))
                    .order_by(ProjectExecutionSession.started_at.desc(), ProjectExecutionSession.id.desc())
                    .first()
                )
                validation_record_id = record_validation(
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=getattr(step_result_row, "id", None),
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    project_id=(
                        getattr(latest_execution, "project_id", None)
                        or run_data.get("project_id")
                    ),
                    execution_session_id=getattr(latest_execution, "id", None),
                    validation_snapshot=validation_snapshot,
                    standards_context=standards_context,
                    payload={
                        "step_name": step.name or f"Step {step_id}",
                        "action_type": step.action_type,
                        "decision": decision,
                    },
                )
                if validation_record_id:
                    validation_snapshot["validation_record_id"] = validation_record_id
                if validation_record_id and not verified_passed:
                    ticket = None
                    if getattr(run, "ticket_id", None):
                        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
                    execution_input = {}
                    execution_output = {}
                    runtime_snapshot = {}
                    executor_output = ""
                    target_backend = ""
                    target_model = ""
                    if latest_execution:
                        try:
                            execution_input = json.loads(latest_execution.input_packet or "{}")
                        except Exception:
                            execution_input = {}
                        try:
                            execution_output = json.loads(latest_execution.output_packet or "{}")
                        except Exception:
                            execution_output = {}
                        runtime_snapshot = (
                            execution_input.get("runtime_snapshot")
                            or execution_output.get("runtime_snapshot")
                            or {}
                        )
                        executor_output = (
                            execution_output.get("output")
                            or execution_output.get("error")
                            or ""
                        )
                        target_backend = latest_execution.route_backend or execution_output.get("backend_id") or ""
                        target_model = latest_execution.selected_model or execution_input.get("model") or execution_output.get("model") or ""
                    correction_packet = build_correction_packet(
                        validation_record={
                            "id": validation_record_id,
                            "workflow_id": run.workflow_id,
                            "run_id": run_id,
                            "step_id": step_id,
                            "ticket_id": getattr(run, "ticket_id", None),
                            "validation_type": validation_snapshot.get("validation_type"),
                            "expected": validation_snapshot.get("expected"),
                            "observed": validation_snapshot.get("observed"),
                            "verdict": validation_snapshot.get("verdict"),
                            "correction_hint": validation_snapshot.get("correction_hint"),
                            "payload": {"snapshot": validation_snapshot},
                        },
                        ticket_title=getattr(ticket, "title", None) or run_data.get("ticket_title") or "",
                        step_name=step.name or f"Step {step_id}",
                        runtime_snapshot=runtime_snapshot,
                        executor_output=executor_output,
                    )
                    correction_attempt_id = create_correction_attempt(
                        validation_record_id=validation_record_id,
                        target_backend=target_backend,
                        target_model=target_model,
                        correction_packet=correction_packet,
                        status="queued",
                    )
                    if correction_attempt_id:
                        validation_snapshot["correction_attempt_id"] = correction_attempt_id
            except Exception:
                logger.debug("Could not record Hermes validation record", exc_info=True)

            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=getattr(step_result_row, "id", None),
                    execution_lane="workflow",
                    status=status,
                    final_verdict=((packet.get("audit") or {}).get("final_verdict")),
                    summary=f"{step.name or f'Step {step_id}'}: {status}",
                    details=(result or "")[:3000],
                )
            step_visibility = self._step_visibility_payload(
                step,
                extra_context=[
                    "ticket_workflow_brief",
                    "prior_step_result",
                    "result_packet_summary",
                    "route_decision",
                    "validation_output",
                ],
            )
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="workflow",
                    event_type="workflow_step_completed",
                    status=status,
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    summary=f"{step.name or f'Step {step_id}'}: {status}",
                    payload={
                        "step_name": step.name or f"Step {step_id}",
                        "action_type": step.action_type,
                        "validation_type": step.validation_type,
                        "decision": decision,
                        "validation_record_id": validation_record_id,
                        "correction_attempt_id": correction_attempt_id,
                        **step_visibility,
                    },
                    evidence={
                        "result_preview": (result or "")[:3000],
                        "validation": validation_snapshot,
                    },
                )
            except Exception:
                logger.debug("Could not emit Hermes workflow_step_completed event", exc_info=True)

            if getattr(run, "ticket_id", None):
                try:
                    from distr.core.kanban.ticket_workflow_engagement import (
                        notify_ticket_workflow_step_finished,
                    )

                    notify_ticket_workflow_step_finished(
                        run_id=run_id,
                        step_id=step_id,
                        passed=bool(verified_passed),
                        result_text=result or "",
                    )
                except Exception:
                    logger.debug("Could not send ticket workflow step engagement", exc_info=True)

            correction_decision = self._maybe_auto_dispatch_correction(
                db,
                run=run,
                step=step,
                run_id=run_id,
                verified_passed=verified_passed,
                correction_attempt_id=correction_attempt_id,
                correction_packet=correction_packet,
                run_data=run_data,
            )
            if correction_decision:
                run.run_data = json.dumps(run_data)
                db.commit()
                increment_workflow_updated()
                return correction_decision

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
                try:
                    from distr.core.workflow.standards_memory import capture_feedback_as_standard
                    capture_feedback_as_standard(run.workflow_id, feedback)
                except Exception as exc:
                    logger.debug("Could not capture workflow feedback as standard: %s", exc)

            # Transition back to running
            run.status = "running"
            step.status = "running"
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="workflow",
                    event_type="workflow_step_feedback_received",
                    status="running",
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    summary=f"{step.name or f'Step {step_id}'} received feedback.",
                    payload={"feedback": feedback.strip()},
                )
            except Exception:
                logger.debug("Could not emit Hermes feedback event", exc_info=True)
            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=None,
                    execution_lane="workflow",
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
        return self.route(
            step_id,
            stored_result,
            stored_passed,
            run_id,
            skip_wait=True,
            skip_approval=True,
        )

    def _maybe_auto_dispatch_correction(
        self,
        db,
        *,
        run: AutoWorkflowRun,
        step: AutoWorkflowStep,
        run_id: int,
        verified_passed: bool,
        correction_attempt_id: int | None,
        correction_packet: dict[str, Any],
        run_data: dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Auto-dispatch corrections were removed from workflow run policy."""
        return None

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

        next_step_id = self._apply_loop_iteration_routing(
            db, run, step, next_step_id, verified_passed,
        )

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
            return self._end_run(run, status="failed", warning="Infinite loop prevented")

        # Advance
        run.current_step_id = next_step.id
        return {
            "action": "next_step",
            "step_id": next_step.id,
            "wait_before_next": step.wait_before_next or 0,
        }

    # ── Static routing ──────────────────────────────────────────────

    def _apply_loop_iteration_routing(
        self,
        db,
        run: Optional[AutoWorkflowRun],
        step: AutoWorkflowStep,
        next_step_id: Optional[int],
        verified_passed: bool,
    ) -> Optional[int]:
        """Track loop iterations when routing back to an earlier step."""
        if not run or next_step_id is None or verified_passed:
            return next_step_id
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            return next_step_id
        loop_contract = run_data.get("loop_contract") or {}
        max_iterations = loop_contract.get("max_iterations")
        if max_iterations is None:
            return next_step_id
        target = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == next_step_id).first()
        if not target or target.position >= step.position:
            return next_step_id
        iteration = int(run_data.get("loop_iteration") or 0) + 1
        run_data["loop_iteration"] = iteration
        run.run_data = json.dumps(run_data)
        db.flush()
        if iteration >= int(max_iterations):
            logger.info(
                "Loop max iterations (%s) reached at step %s — ending run",
                max_iterations,
                step.id,
            )
            report_step = (
                db.query(AutoWorkflowStep)
                .filter(AutoWorkflowStep.workflow_id == step.workflow_id)
                .order_by(AutoWorkflowStep.position.desc())
                .first()
            )
            if report_step and report_step.id != step.id:
                return report_step.id
            return -1
        target_visibility = self._step_visibility_payload(
            target,
            extra_context=[
                "ticket_workflow_brief",
                "prior_step_result",
                "result_packet_summary",
                "route_decision",
                "validation_output",
            ],
        )
        payload = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "from_step_id": step.id,
            "from_step_name": step.name or f"Step {step.id}",
            "to_step_id": target.id,
            "to_step_name": target.name or f"Step {target.id}",
            **target_visibility,
        }
        loop_event = OrchestratorEvent(
            event_uid=uuid.uuid4().hex,
            source="workflow",
            event_type="loop_iteration",
            status="running",
            workflow_id=step.workflow_id,
            run_id=run.id,
            step_id=step.id,
            ticket_id=getattr(run, "ticket_id", None),
            board_id=getattr(run, "board_id", None),
            project_id=run_data.get("project_id"),
            summary=f"Loop iteration {iteration} of {max_iterations}",
            payload=json.dumps(payload, default=str),
            evidence=json.dumps({
                "prior_step_result": (getattr(step, "result", None) or "")[:3000],
                "next_step_context": target_visibility.get("context", []),
            }, default=str),
        )
        db.add(loop_event)
        db.flush()
        try:
            from distr.core.events import ORCHESTRATION_EVENT, get_event_bus
            from distr.core.orchestrator import serialize_event

            get_event_bus().publish(ORCHESTRATION_EVENT, serialize_event(loop_event))
        except Exception:
            logger.debug("Could not publish loop_iteration event to EventBus", exc_info=True)
        return next_step_id

    @staticmethod
    def _step_config_dict(step: AutoWorkflowStep) -> Dict[str, Any]:
        try:
            config = json.loads(step.config or "{}")
        except Exception:
            config = {}
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _step_tools_for_action(action_type: str) -> List[str]:
        action = (action_type or "").strip()
        if action == "computer_use":
            return ["computer_use"]
        if action == "playwright":
            return ["playwright", "browser_use"]
        if action == "send_to_project_cli":
            return ["cli"]
        if action in {"run_command", "execute_code", "agent_instruction"}:
            return ["other"]
        return []

    @classmethod
    def _step_visibility_payload(
        cls,
        step: AutoWorkflowStep,
        *,
        extra_context: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        config = cls._step_config_dict(step)
        tools = config.get("tools") if isinstance(config.get("tools"), list) else []
        skills = config.get("skills") if isinstance(config.get("skills"), list) else []
        context = config.get("context") if isinstance(config.get("context"), list) else []
        clean_tools = [str(item).strip() for item in tools if str(item or "").strip()]
        clean_skills = [str(item).strip() for item in skills if str(item or "").strip()]
        clean_context = [str(item).strip() for item in context if str(item or "").strip()]
        for label in extra_context or []:
            label = str(label or "").strip()
            if label and label not in clean_context:
                clean_context.append(label)
        return {
            "skills": clean_skills,
            "tools": clean_tools or cls._step_tools_for_action(step.action_type or step.step_type or ""),
            "context": clean_context,
        }

    @staticmethod
    def _static_route(db, step: AutoWorkflowStep, passed: bool) -> Optional[int]:
        """Follow explicit goto, otherwise advance on pass or end on fail."""
        goto = step.on_pass_goto if passed else step.on_fail_goto
        if goto is not None:
            return goto

        if not passed:
            return -1

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
            if not run_data.get("waiting_kind"):
                run_data["waiting_kind"] = ""
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
                execution_lane="workflow",
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
        record_workflow_chat_event(
            run_id,
            "waiting",
            status="waiting",
            step_id=step_id,
            step_name=step_name,
            summary=result or "Workflow is waiting for input.",
        )
        self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
        self._notify_main_agent(workflow_id, run_id, handoff, result_text=result)

        return {"action": "waiting", "notify_main_agent": True, "run_id": run_id}

    def _enter_approval_state(
        self,
        db,
        step: AutoWorkflowStep,
        run_id: int,
        result: str,
        passed: bool,
    ) -> Dict[str, Any]:
        """Hold a verified step until a human approves it."""
        step.status = "waiting"
        step_id = step.id
        step_name = step.name
        workflow_id = step.workflow_id
        handoff = self._build_approval_handoff_text(step_name=step_name, result_text=result, run_id=run_id)

        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == run_id,
        ).first()
        if run:
            run.status = "waiting"
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = result
            run_data["waiting_passed"] = passed
            run_data["waiting_prompt"] = handoff["prompt"]
            run_data["waiting_kind"] = "approval"
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
                execution_lane="workflow",
                status="waiting",
                final_verdict="cannot_determine",
                summary=f"{step_name or f'Step {step_id}'} waiting for approval",
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
                    "waiting_kind": "approval",
                },
            )
        db.commit()

        increment_workflow_updated()
        record_workflow_chat_event(
            run_id,
            "waiting",
            status="waiting",
            step_id=step_id,
            step_name=step_name,
            summary=result or "Workflow step is waiting for approval.",
        )
        try:
            from distr.core.orchestrator import emit_approval_event

            emit_approval_event(
                event_type="approval_requested",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=getattr(run, "ticket_id", None) if run else None,
                board_id=getattr(run, "board_id", None) if run else None,
                summary=f"{step_name or f'Step {step_id}'} requires manual approval.",
                payload={"step_name": step_name or "", "result_preview": (result or "")[:1500]},
            )
        except Exception:
            logger.debug("Could not emit approval_requested event", exc_info=True)
        self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
        self._notify_main_agent(workflow_id, run_id, handoff, result_text=result)

        return {"action": "waiting", "notify_main_agent": True, "run_id": run_id, "waiting_kind": "approval"}

    @staticmethod
    def _build_approval_handoff_text(step_name: str, result_text: str, run_id: Optional[int]) -> Dict[str, str]:
        clean_result = (result_text or "").strip() or "Step completed with no detailed output."
        summary = clean_result[:280]
        if len(clean_result) > 280:
            summary += "..."
        step_label = step_name or "workflow step"
        prompt = (
            f"{step_label} passed validation and is waiting for your approval. "
            "Reply to approve and continue, or provide correction instructions."
        )
        tts = f"{summary}. {prompt}"
        report = (
            f"[WORKFLOW_APPROVAL_REQUIRED]\n"
            f"step_name: {step_label}\n"
            f"run_id: __RUN_ID__\n"
            f"status: waiting_for_approval\n"
            f"step_result_summary: {summary}\n"
            f"step_result_full: {clean_result[:1500]}\n\n"
            "Orchestrator instructions:\n"
            "1) Summarize what the step accomplished.\n"
            "2) Ask the user to approve or request changes.\n"
            "3) After approval, call continue_workflow with that reply."
        )
        history_entry = f"{clean_result}\n\n[APPROVAL REQUIRED]\n{prompt}"
        if run_id is not None:
            history_entry = f"{history_entry}\nRun ID: {run_id}"
        return {
            "prompt": prompt,
            "tts": tts,
            "report": report,
            "history_entry": history_entry,
        }

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
        run.completed_at = utc_now_naive()
        if warning:
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            run_data["terminal_warning"] = warning
            run.run_data = json.dumps(run_data)
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
        *,
        result_text: str = "",
    ) -> None:
        """Speak result via TTS and queue a report for the main agent."""
        from distr.core.workflow.wait_handoff import is_ide_handoff_result

        # IDE handoffs also notify via ticket_workflow_engagement — avoid double speech.
        if not is_ide_handoff_result(result_text):
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
        from distr.core.workflow.wait_handoff import build_wait_handoff_text

        return build_wait_handoff_text(step_name, result_text, run_id)
