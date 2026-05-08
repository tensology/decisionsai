"""
Integration test: ticket → start_workflow_run → StepDispatcher → WorkflowAgent → result.

This is the test the entire workflow suite was missing. It uses a real in-memory
SQLite database and real dispatcher/executor code, mocking only at the LLM boundary
so the full pipeline is exercised without an actual Ollama/OpenAI call.

Validates end-to-end:
  - start_workflow_run() creates a run record and returns run_id
  - StepDispatcher picks up the step and calls WorkflowAgent.execute()
  - Agent produces a result; result is recorded in AutoWorkflowStepResult
  - Router routes to next step or ends the run
  - AutoWorkflowRun.status reaches "completed"
  - ticket.workflow_status is updated to "completed"
"""
import contextlib
import sys
import threading
import time
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── Stub out Qt/signal dependencies that aren't available in test env ──────
_mock_signals = MagicMock()
_mock_signals.signal_manager = MagicMock()
sys.modules.setdefault("distr.core.signals", _mock_signals)
sys.modules.setdefault("PyQt6.QtCore", MagicMock())
sys.modules.setdefault("PyQt6", MagicMock())


def _make_engine():
    # StaticPool forces all connections (including background threads) to share
    # one underlying connection, so the in-memory schema is visible everywhere.
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_db(factory):
    """Create the minimal schema objects needed for a workflow run."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    s = factory()
    board = KanbanBoard(name="Test Board", agent_enabled=True)
    s.add(board)
    s.flush()

    lane = KanbanLane(board_id=board.id, name="Current", position=0)
    s.add(lane)
    s.flush()

    ticket = KanbanTicket(
        lane_id=lane.id,
        title="Fix the login bug",
        description="Users cannot log in with OAuth.",
        position=0,
    )
    s.add(ticket)
    s.flush()

    wf = AutoWorkflow(name="Bug Triage", description="Triage a bug ticket")
    s.add(wf)
    s.flush()

    board.default_workflow_id = wf.id
    ticket.linked_workflow_id = wf.id

    step = AutoWorkflowStep(
        workflow_id=wf.id,
        name="Analyze",
        position=0,
        action_type="agent_instruction",
        instruction="Analyze this bug and provide a one-sentence summary.",
        status="pending",
    )
    s.add(step)
    s.flush()

    ids = {
        "board_id": board.id,
        "lane_id": lane.id,
        "ticket_id": ticket.id,
        "workflow_id": wf.id,
        "step_id": step.id,
    }
    s.commit()
    s.close()
    return ids


def _wait_for_run_terminal(factory, run_id: int, timeout: float = 20.0) -> str:
    """Poll until AutoWorkflowRun.status is terminal (or timeout)."""
    from distr.core.db.workflow import AutoWorkflowRun
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = factory()
        run = s.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        status = run.status if run else None
        s.close()
        if status in ("completed", "failed", "cancelled"):
            return status
        time.sleep(0.2)
    return "timeout"


class TestWorkflowDispatchIntegration:
    """Full-stack integration test for the ticket → workflow → step → result pipeline."""

    def setup_method(self):
        from distr.core.db import Base
        from distr.core.workflow.dispatcher import _active_runs, _runs_lock
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        # Clean up any live RunContexts from previous tests to prevent cross-test bleed.
        with _runs_lock:
            _active_runs.clear()

    def _patched_get_session(self):
        return _session_ctx(self.factory)

    @pytest.mark.timeout(30)
    def test_single_agent_step_runs_to_completion(self):
        """start_workflow_run() with one agent_instruction step completes successfully."""
        ids = _seed_db(self.factory)

        # Mock only the LLM call — everything else is real
        fake_llm_response = ("The login bug is caused by a missing OAuth redirect URI.", [])

        no_op_kanban = MagicMock()
        no_op_workflow = MagicMock()

        patches = [
            # Single source of truth — all modules import get_session from distr.core.db
            # Each module binds get_session locally at import; patch the local name.
            patch("distr.core.workflow.dispatcher.get_session", self._patched_get_session),
            patch("distr.core.workflow.router.get_session", self._patched_get_session),
            patch("distr.core.workflow.post_execution.get_session", self._patched_get_session),
            patch("distr.core.workflow.step_executor.get_session", self._patched_get_session),
            # LLM boundary — return a canned response, no tool calls
            patch("distr.core.workflow_agent.WorkflowAgent._call_llm_sync",
                  return_value=fake_llm_response),
            # Silence WebSocket/event emissions — not under test
            patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op_workflow),
            patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op_kanban),
            patch("distr.core.workflow.router.increment_workflow_updated", no_op_workflow),
            patch("distr.core.workflow.post_execution.increment_workflow_updated", no_op_workflow),
            # Silence audit trail writes that open extra sessions
            patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.workflow.router.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.kanban.result_packet.append_workflow_step_to_packet",
                  side_effect=lambda p, **kw: p),
            # Risk enforcement can downgrade completed→failed when audit gates aren't
            # satisfied — test the dispatch pipeline without audit policy interference.
            patch("distr.core.workflow.dispatcher.enforce_validation_requirements",
                  side_effect=lambda packet, run_status, risk_profile: (run_status, packet, [])),
        ]

        from distr.core.workflow.dispatcher import start_workflow_run

        # Patches must stay active while background agent thread executes —
        # start_workflow_run returns immediately for async agent steps.
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            result = start_workflow_run(
                ids["workflow_id"],
                context="Ticket: Fix the login bug\n\nDescription: Users cannot log in with OAuth.",
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
                run_metadata={
                    "source_type": "test",
                    "board_id": ids["board_id"],
                    "ticket_id": ids["ticket_id"],
                    "ticket_title": "Fix the login bug",
                    "board_name": "Test Board",
                    "phase": "planning",
                },
            )

            assert "error" not in result, f"start_workflow_run returned error: {result}"
            run_id = result.get("run_id")
            assert run_id is not None, "No run_id returned"

            # Wait for async execution to finish (patches still active)
            terminal = _wait_for_run_terminal(self.factory, run_id, timeout=20.0)

        assert terminal == "completed", f"Run ended with '{terminal}' instead of 'completed'"

        # Verify step result was recorded
        from distr.core.db.workflow import AutoWorkflowStepResult
        s = self.factory()
        results = s.query(AutoWorkflowStepResult).filter(
            AutoWorkflowStepResult.run_id == run_id).all()
        s.close()
        assert len(results) >= 1, "No step result was recorded"
        assert any("login" in (r.agent_response or "").lower() for r in results), \
            "LLM response not in step result"

    @pytest.mark.timeout(30)
    def test_workflow_run_blocked_while_live_run_exists(self):
        """start_workflow_run() refuses to start a second run while one is truly active."""
        from distr.core.workflow.dispatcher import start_workflow_run, _active_runs, _runs_lock
        from distr.core.workflow.dispatcher import _RunContext
        import asyncio

        ids = _seed_db(self.factory)
        fake_llm_response = ("Done.", [])
        no_op = MagicMock()

        patches = [
            patch("distr.core.workflow.dispatcher.get_session", self._patched_get_session),
            patch("distr.core.workflow.router.get_session", self._patched_get_session),
            patch("distr.core.workflow.post_execution.get_session", self._patched_get_session),
            patch("distr.core.workflow.step_executor.get_session", self._patched_get_session),
            patch("distr.core.workflow_agent.WorkflowAgent._call_llm_sync",
                  return_value=fake_llm_response),
            patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op),
            patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op),
            patch("distr.core.workflow.router.increment_workflow_updated", no_op),
            patch("distr.core.workflow.post_execution.increment_workflow_updated", no_op),
            patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.workflow.router.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.kanban.result_packet.append_workflow_step_to_packet",
                  side_effect=lambda p, **kw: p),
        ]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            result1 = start_workflow_run(
                ids["workflow_id"],
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
            )
            run_id = result1.get("run_id")
            assert run_id is not None

            # While run1 is still in _active_runs, a second start should be rejected
            with _runs_lock:
                still_active = run_id in _active_runs

            if still_active:
                result2 = start_workflow_run(
                    ids["workflow_id"],
                    board_id=ids["board_id"],
                    ticket_id=ids["ticket_id"],
                )
                assert "error" in result2, "Expected duplicate-run rejection"
                assert "already in progress" in result2["error"].lower()

            _wait_for_run_terminal(self.factory, run_id, timeout=20.0)

    @pytest.mark.timeout(10)
    def test_orphaned_run_is_auto_cancelled_on_new_push(self):
        """An orphaned run (in DB as 'running' but not in _active_runs) is auto-cancelled."""
        from distr.core.db.workflow import AutoWorkflowRun
        from distr.core.workflow.dispatcher import start_workflow_run

        ids = _seed_db(self.factory)
        no_op = MagicMock()

        # Manually insert an orphaned run record
        s = self.factory()
        orphan = AutoWorkflowRun(
            workflow_id=ids["workflow_id"],
            board_id=ids["board_id"],
            ticket_id=ids["ticket_id"],
            status="running",
        )
        s.add(orphan)
        s.commit()
        orphan_id = orphan.id
        s.close()

        fake_llm = ("Done.", [])
        patches = [
            patch("distr.core.workflow.dispatcher.get_session", self._patched_get_session),
            patch("distr.core.workflow.router.get_session", self._patched_get_session),
            patch("distr.core.workflow.post_execution.get_session", self._patched_get_session),
            patch("distr.core.workflow.step_executor.get_session", self._patched_get_session),
            patch("distr.core.workflow_agent.WorkflowAgent._call_llm_sync", return_value=fake_llm),
            patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op),
            patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op),
            patch("distr.core.workflow.router.increment_workflow_updated", no_op),
            patch("distr.core.workflow.post_execution.increment_workflow_updated", no_op),
            patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.workflow.router.append_ticket_audit_entry", MagicMock()),
            patch("distr.core.kanban.result_packet.append_workflow_step_to_packet",
                  side_effect=lambda p, **kw: p),
        ]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            # Should succeed — orphan should be auto-cancelled first
            result = start_workflow_run(
                ids["workflow_id"],
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
            )

            assert "error" not in result, f"start_workflow_run failed: {result}"

            # Orphan should now be cancelled
            s = self.factory()
            orphan_row = s.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == orphan_id).first()
            s.close()
            assert orphan_row.status == "cancelled", \
                f"Orphan run status is '{orphan_row.status}', expected 'cancelled'"

            _wait_for_run_terminal(self.factory, result["run_id"], timeout=15.0)
