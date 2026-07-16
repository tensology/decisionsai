"""Unit tests for StepRouter — routing logic after step completion."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from distr.core.workflow.router import StepRouter


# ── Helpers ─────────────────────────────────────────────────────────

def _make_step(**overrides):
    """Create a mock AutoWorkflowStep with sensible defaults."""
    step = MagicMock()
    step.id = overrides.get("id", 1)
    step.name = overrides.get("name", "Test Step")
    step.description = overrides.get("description", "")
    step.workflow_id = overrides.get("workflow_id", 100)
    step.position = overrides.get("position", 0)
    step.routing_mode = overrides.get("routing_mode", "static")
    step.routing_prompt = overrides.get("routing_prompt", None)
    step.on_pass_goto = overrides.get("on_pass_goto", None)
    step.on_fail_goto = overrides.get("on_fail_goto", None)
    step.wait_before_next = overrides.get("wait_before_next", 0)
    step.wait_for_continue = overrides.get("wait_for_continue", False)
    step.require_approval = overrides.get("require_approval", False)
    step.validation_type = overrides.get("validation_type", "none")
    step.validation_prompt = overrides.get("validation_prompt", None)
    step.status = overrides.get("status", "running")
    step.result = overrides.get("result", None)
    return step


def _make_run(**overrides):
    """Create a mock AutoWorkflowRun."""
    run = MagicMock()
    run.id = overrides.get("id", 10)
    run.workflow_id = overrides.get("workflow_id", 100)
    run.status = overrides.get("status", "running")
    run.current_step_id = overrides.get("current_step_id", 1)
    run.completed_at = overrides.get("completed_at", None)
    run.run_data = overrides.get("run_data", None)
    run.ticket_id = overrides.get("ticket_id", None)
    run.board_id = overrides.get("board_id", None)
    run.workflow = overrides.get("workflow", None)
    return run


# ── Static routing tests ───────────────────────────────────────────

class TestStaticRoute:
    def test_pass_follows_on_pass_goto(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=5, on_fail_goto=9)
        result = StepRouter._static_route(db, step, passed=True)
        assert result == 5

    def test_fail_follows_on_fail_goto(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=5, on_fail_goto=9)
        result = StepRouter._static_route(db, step, passed=False)
        assert result == 9

    def test_null_goto_ends_run_on_pass(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=None, on_fail_goto=None)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        assert StepRouter._static_route(db, step, passed=True) is None

    def test_fail_without_goto_ends_run_even_when_next_step_exists(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=None, on_fail_goto=None)
        next_step = _make_step(id=2, position=1)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = next_step
        assert StepRouter._static_route(db, step, passed=False) == -1

    def test_pass_without_goto_advances_to_next_step(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=None, on_fail_goto=None)
        next_step = _make_step(id=2, position=1)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = next_step
        assert StepRouter._static_route(db, step, passed=True) == 2

    def test_minus_one_ends_run(self):
        db = MagicMock()
        step = _make_step(on_pass_goto=-1, on_fail_goto=-1)
        assert StepRouter._static_route(db, step, passed=True) == -1
        assert StepRouter._static_route(db, step, passed=False) == -1


# ── Parse routing response tests ───────────────────────────────────

class TestParseRoutingResponse:
    def _steps(self):
        return [
            _make_step(id=1, position=0),
            _make_step(id=2, position=1),
            _make_step(id=3, position=2),
        ]

    def test_end_response(self):
        assert StepRouter._parse_routing_response("END", self._steps(), 1) is None

    def test_end_with_explanation(self):
        assert StepRouter._parse_routing_response("END - workflow done", self._steps(), 1) is None

    def test_valid_step_id(self):
        assert StepRouter._parse_routing_response("2", self._steps(), 1) == 2

    def test_step_id_in_text(self):
        assert StepRouter._parse_routing_response("Go to step 3", self._steps(), 1) == 3

    def test_position_fallback(self):
        # If the number doesn't match an ID but matches a position
        steps = [_make_step(id=10, position=0), _make_step(id=20, position=1)]
        result = StepRouter._parse_routing_response("1", steps, 10)
        assert result == 20  # position 1 → id 20

    def test_invalid_response_returns_none(self):
        assert StepRouter._parse_routing_response("I don't know", self._steps(), 1) is None

    def test_self_reference_excluded(self):
        # Step 1 is current, so "1" should not match step id 1
        steps = [_make_step(id=1, position=0), _make_step(id=2, position=1)]
        result = StepRouter._parse_routing_response("1", steps, 1)
        # Should try position fallback: position 1 → id 2
        assert result == 2


# ── Self-routing guard tests ───────────────────────────────────────

class TestSelfRoutingGuard:
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    def test_self_route_ends_run(self, mock_verify, mock_get_session, mock_ws):
        step = _make_step(id=5, on_pass_goto=5)
        run = _make_run(id=10)
        next_step = _make_step(id=5)  # same step

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, next_step]

        router = StepRouter()
        decision = router.route(5, "result", True, 10)

        assert decision["action"] == "end_run"
        assert "warning" in decision

    def test_failed_self_route_retries_when_loop_contract_is_bounded(self):
        step = _make_step(id=5, position=2, on_fail_goto=5)
        run = _make_run(run_data=json.dumps({
            "loop_contract": {"max_iterations": 3},
            "loop_iteration": 0,
        }))
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = step

        target = StepRouter()._apply_loop_iteration_routing(
            db,
            run,
            step,
            next_step_id=5,
            verified_passed=False,
        )

        assert target == 5
        assert json.loads(run.run_data)["loop_iteration"] == 1

    def test_determine_next_allows_a_counted_bounded_self_retry(self):
        step = _make_step(id=5, position=2, on_fail_goto=5)
        run = _make_run(run_data=json.dumps({
            "loop_contract": {"max_iterations": 3},
            "loop_iteration": 1,
        }))
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = step

        with patch.object(StepRouter, "_apply_loop_iteration_routing", return_value=5):
            decision = StepRouter()._determine_next(db, step, run, False, "timed out")

        assert decision["action"] == "next_step"
        assert decision["step_id"] == 5


# ── Wait state tests ───────────────────────────────────────────────

class TestWaitState:
    @patch("distr.core.workflow.router.StepRouter._emit_waiting_for_feedback")
    @patch("distr.core.workflow.router.StepRouter._notify_main_agent")
    @patch("distr.core.workflow.router.should_pause_after_step", return_value=True)
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    def test_wait_for_continue_enters_waiting(self, mock_get_session, mock_ws, mock_pause, mock_notify, mock_emit):
        step = _make_step(id=1, wait_for_continue=True)
        run = _make_run(id=10, run_data="{}")

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run]

        router = StepRouter()
        decision = router.route(1, "done", True, 10)

        assert decision["action"] == "waiting"
        assert decision["notify_main_agent"] is True
        assert step.status == "waiting"
        assert run.status == "waiting"
        mock_pause.assert_called_once()

    @patch("distr.core.workflow.router.should_pause_after_step", return_value=False)
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    def test_wait_for_continue_is_ignored_when_checkpoints_are_not_enabled(
        self,
        mock_verify,
        mock_get_session,
        mock_ws,
        mock_pause,
    ):
        step = _make_step(id=1, wait_for_continue=True)
        run = _make_run(id=10, run_data="{}")
        next_step = _make_step(id=2, position=1)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, next_step]
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = next_step

        router = StepRouter()
        decision = router.route(1, "done", True, 10)

        assert decision["action"] == "next_step"
        assert decision["step_id"] == 2
        assert step.status == "passed"
        mock_pause.assert_called_once()

    @patch("distr.core.workflow.router.StepRouter._notify_main_agent")
    @patch("distr.core.workflow.router.should_pause_after_step", return_value=True)
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    def test_wait_emits_step_waiting_for_feedback(self, mock_get_session, mock_ws, mock_pause, mock_notify):
        step = _make_step(id=3, workflow_id=200, wait_for_continue=True, name="Review")
        run = _make_run(id=15, run_data="{}")

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run]

        with patch("distr.core.workflow.router.StepRouter._emit_waiting_for_feedback") as mock_emit:
            router = StepRouter()
            router.route(3, "analysis complete", True, 15)

            mock_emit.assert_called_once_with(3, 200, 15, "analysis complete")


# ── End run helper tests ───────────────────────────────────────────

class TestEndRun:
    def test_end_run_sets_completed(self):
        run = _make_run(id=10)
        decision = StepRouter._end_run(run)
        assert decision["action"] == "end_run"
        assert decision["status"] == "completed"
        assert run.status == "completed"
        assert run.completed_at is not None

    def test_end_run_with_warning(self):
        run = _make_run(id=10)
        decision = StepRouter._end_run(run, warning="loop detected")
        assert decision["warning"] == "loop detected"


# ── Resume from feedback tests ─────────────────────────────────────

class TestResumeFromFeedback:
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch.object(StepRouter, "route")
    def test_resume_appends_feedback_and_reroutes(self, mock_route, mock_get_session, mock_ws):
        run = _make_run(id=10, status="waiting", run_data=json.dumps({
            "waiting_result": "step output",
            "waiting_passed": True,
        }))
        step = _make_step(id=1, status="waiting")

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [run, step]

        mock_route.return_value = {"action": "next_step", "step_id": 2}

        router = StepRouter()
        decision = router.resume_from_feedback(1, 10, "looks good")

        assert decision["action"] == "next_step"
        assert run.status == "running"
        assert step.status == "running"
        # Verify route was called with enriched result
        call_args = mock_route.call_args
        assert "[FEEDBACK]: looks good" in call_args[0][1]

    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    def test_resume_non_waiting_run_fails(self, mock_get_session, mock_ws):
        run = _make_run(id=10, status="running")

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.return_value = run

        router = StepRouter()
        decision = router.resume_from_feedback(1, 10, "feedback")

        assert decision["action"] == "end_run"
        assert "error" in decision


# ── Full route integration tests ───────────────────────────────────

class TestRouteIntegration:
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    def test_route_to_next_step(self, mock_verify, mock_get_session, mock_ws):
        step = _make_step(id=1, on_pass_goto=2)
        run = _make_run(id=10)
        next_step = _make_step(id=2)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, next_step]
        db.add = MagicMock()

        router = StepRouter()
        decision = router.route(1, "output", True, 10)

        assert decision["action"] == "next_step"
        assert decision["step_id"] == 2

    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    def test_route_null_goto_ends_run(self, mock_verify, mock_get_session, mock_ws):
        step = _make_step(id=1, on_pass_goto=None)
        run = _make_run(id=10)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, None]
        db.add = MagicMock()

        router = StepRouter()
        decision = router.route(1, "output", True, 10)

        assert decision["action"] == "end_run"
        assert decision["status"] == "completed"

    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=False)
    def test_route_failed_verification_follows_fail_goto(self, mock_verify, mock_get_session, mock_ws):
        step = _make_step(id=1, on_pass_goto=2, on_fail_goto=3)
        run = _make_run(id=10)
        next_step = _make_step(id=3)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, next_step]
        db.add = MagicMock()

        router = StepRouter()
        decision = router.route(1, "output", True, 10)

        assert decision["action"] == "next_step"
        assert decision["step_id"] == 3
        assert step.status == "failed"

    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    def test_route_step_not_found(self, mock_get_session, mock_ws):
        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.return_value = None

        router = StepRouter()
        decision = router.route(999, "output", True, 10)

        assert decision["action"] == "end_run"
        assert "error" in decision

    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    @patch(
        "distr.core.workflow.standards_memory.build_standards_context",
        return_value="[VISUAL TASTE MEMORY]\n- approved: Compact controls.",
    )
    def test_route_updates_result_packet_in_run_data(
        self,
        _mock_standards,
        mock_verify,
        mock_get_session,
        mock_ws,
    ):
        step = _make_step(
            id=1,
            name="Analyze",
            on_pass_goto=2,
            validation_type="text_match",
            validation_prompt="analysis passed",
        )
        run = _make_run(
            id=10,
            board_id=7,
            workflow=MagicMock(context_rules="Workflow context."),
            run_data=json.dumps(
                {
                    "result_packet": {
                        "status": "running",
                        "summary": "Workflow run started.",
                        "changes": {"change_summary": []},
                        "audit": {"final_verdict": "cannot_determine"},
                    },
                },
            ),
        )
        next_step = _make_step(id=2)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, next_step]
        db.add = MagicMock()

        router = StepRouter()
        decision = router.route(1, "analysis passed", True, 10)

        assert decision["action"] == "next_step"
        payload = json.loads(run.run_data or "{}")
        packet = payload.get("result_packet") or {}
        assert packet.get("status") == "running"
        changes = (packet.get("changes") or {}).get("change_summary") or []
        assert any("Analyze: passed" in line for line in changes)
        snapshots = ((packet.get("execution") or {}).get("validation_snapshots") or [])
        assert snapshots[-1]["step_name"] == "Analyze"
        assert snapshots[-1]["validation_type"] == "text_match"
        assert snapshots[-1]["expected"] == "analysis passed"
        assert snapshots[-1]["verdict"] == "pass"
        assert "[VISUAL TASTE MEMORY]" in snapshots[-1]["standards_context"]

    @patch("distr.core.workflow.router.append_ticket_audit_entry")
    @patch("distr.core.workflow.router.increment_workflow_updated")
    @patch("distr.core.workflow.router.get_session")
    @patch("distr.core.workflow.router._run_verification", return_value=True)
    def test_route_writes_ticket_audit_entry(
        self,
        mock_verify,
        mock_get_session,
        mock_ws,
        mock_append_ticket_audit,
    ):
        step = _make_step(id=1, name="Audit Step", on_pass_goto=2)
        run = _make_run(id=10, ticket_id=42, run_data=json.dumps({"result_packet": {}}))
        next_step = _make_step(id=2)

        db = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        db.query.return_value.filter.return_value.first.side_effect = [step, run, run, run, next_step]
        db.add = MagicMock()
        db.flush = MagicMock()

        router = StepRouter()
        decision = router.route(1, "step output", True, 10)

        assert decision["action"] == "next_step"
        mock_append_ticket_audit.assert_called_once()
        _, kwargs = mock_append_ticket_audit.call_args
        assert kwargs["ticket_id"] == 42
        assert kwargs["run_id"] == 10
        assert kwargs["step_id"] == 1


# ── Event emission tests ───────────────────────────────────────────

class TestEmitWaitingForFeedback:
    @patch("distr.core.signals.signal_manager")
    def test_emit_calls_signal_manager(self, mock_signals):
        StepRouter._emit_waiting_for_feedback(5, 100, 20, "step result text")

        mock_signals.step_waiting_for_feedback.emit.assert_called_once_with(
            5, 100, 20, "step result text",
        )

    @patch("distr.core.signals.signal_manager")
    def test_emit_handles_signal_error_gracefully(self, mock_signals):
        mock_signals.step_waiting_for_feedback.emit.side_effect = RuntimeError("no Qt")

        # Should not raise
        StepRouter._emit_waiting_for_feedback(1, 2, 3, "text")
