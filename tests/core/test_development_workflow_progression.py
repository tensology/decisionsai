"""Integration-style workflow progression test with visible step trace output."""

import threading

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
from distr.core.workflow.dispatcher import StepDispatcher, _RunContext, _active_runs, _runs_lock


def test_development_workflow_step_progression_trace(capfd):
    """Show Planning -> Execution -> Validation progression with context/result chaining."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import patch, MagicMock

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    class _SessionCtx:
        def __enter__(self):
            self.s = SessionLocal()
            return self.s

        def __exit__(self, exc_type, exc, tb):
            if exc_type:
                self.s.rollback()
            else:
                self.s.commit()
            self.s.close()
            return False

    def _get_session():
        return _SessionCtx()

    with patch("distr.core.workflow.dispatcher.get_session", _get_session), patch(
        "distr.core.workflow.router.get_session", _get_session
    ), patch("distr.core.workflow.service.get_session", _get_session):
        with _get_session() as db:
            wf = AutoWorkflow(
                name="Development",
                description="Development workflow for ticket execution.",
                status="draft",
            )
            db.add(wf)
            db.flush()
            workflow_id = wf.id

            steps = [
                AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="Planning",
                    action_type="agent_instruction",
                    instruction="Create a concrete implementation plan with success criteria.",
                    status="pending",
                ),
                AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=1,
                    name="Execution",
                    action_type="agent_instruction",
                    instruction="Implement the plan and summarize concrete code changes.",
                    status="pending",
                ),
                AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=2,
                    name="Validation",
                    action_type="agent_instruction",
                    instruction="Validate implementation and report pass/fail per requirement.",
                    status="pending",
                ),
            ]
            db.add_all(steps)
            db.flush()
            steps[0].on_pass_goto = steps[1].id
            steps[0].on_fail_goto = -1
            steps[1].on_pass_goto = steps[2].id
            steps[1].on_fail_goto = -1
            steps[2].on_pass_goto = -1
            steps[2].on_fail_goto = -1
            first_step_id = steps[0].id

            run = AutoWorkflowRun(workflow_id=wf.id, status="running", current_step_id=steps[0].id)
            db.add(run)
            db.flush()
            run_id = run.id

        fake_agent = MagicMock()
        fake_loop = MagicMock()
        fake_thread = threading.Thread(target=lambda: None)
        with _runs_lock:
            _active_runs[run_id] = _RunContext(
                run_id=run_id,
                workflow_agent=fake_agent,
                event_loop=fake_loop,
                thread=fake_thread,
                context_prefix=(
                    "Ticket: Fix check-in pipeline for board Alpha\n\n"
                    "Description: Plan, implement, and validate board->workflow->ticket progression."
                ),
            )

        captured_prompts = {}

        def _fake_run_agent(self, step_data, run_id_arg):
            prompt = self._build_agent_prompt(step_data, run_id_arg)
            step_name = step_data["name"]
            captured_prompts[step_name] = prompt

            if step_name == "Planning":
                print("\n[TRACE] STEP 1: Planning")
                print(prompt[:600])
                assert "Ticket: Fix check-in pipeline for board Alpha" in prompt
                return {
                    "output": "Plan complete. Success criteria: context flows to all steps; routing advances to execution.",
                    "passed": True,
                }
            if step_name == "Execution":
                print("\n[TRACE] STEP 2: Execution")
                print(prompt[:600])
                assert "Plan complete. Success criteria" in prompt
                return {
                    "output": "Implemented changes. Updated dispatcher context injection and workflow progression tracing.",
                    "passed": True,
                }

            print("\n[TRACE] STEP 3: Validation")
            print(prompt[:600])
            assert "Implemented changes. Updated dispatcher context injection" in prompt
            return {
                "output": "Validation pass. Requirement context-flow satisfied and step chaining confirmed.",
                "passed": True,
            }

        with patch.object(StepDispatcher, "_run_agent", _fake_run_agent):
            dispatcher = StepDispatcher()
            result = dispatcher.run_in_workflow(first_step_id, run_id)
            assert result["success"] is True

        with _get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            all_steps = (
                db.query(AutoWorkflowStep)
                .filter(AutoWorkflowStep.workflow_id == workflow_id)
                .order_by(AutoWorkflowStep.position.asc())
                .all()
            )
            results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id == run_id)
                .order_by(AutoWorkflowStepResult.created_at.asc())
                .all()
            )

            print("\n[TRACE] FINAL RUN STATUS:", run.status)
            for st in all_steps:
                print(f"[TRACE] STEP RESULT: {st.name} -> {st.status}")

            assert run.status == "completed"
            assert [s.status for s in all_steps] == ["passed", "passed", "passed"]
            assert len(results) == 3

            # Guardrails proving chaining and initial ticket context propagation
            assert "Ticket: Fix check-in pipeline for board Alpha" in captured_prompts["Planning"]
            assert "Plan complete. Success criteria" in captured_prompts["Execution"]
            assert "Implemented changes. Updated dispatcher context injection" in captured_prompts["Validation"]

        out = capfd.readouterr().out
        assert "[TRACE] STEP 1: Planning" in out
        assert "[TRACE] STEP 2: Execution" in out
        assert "[TRACE] STEP 3: Validation" in out
        assert "[TRACE] FINAL RUN STATUS: completed" in out

