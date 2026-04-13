"""Unit tests for assemble_step_context wiring in WorkflowOrchestrationMixin._send_workflow_instruction."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from distr.app.workflow import WorkflowOrchestrationMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orch(workflow_id=1, steps_data=None, prior_results=None, workflow_description="Do stuff"):
    """Build a minimal orchestration dict matching what _start_workflow_orchestration creates."""
    mock_agent = MagicMock()
    mock_loop = MagicMock()
    if steps_data is None:
        steps_data = [{"id": 10, "title": "Step 1", "instruction": "Run something"}]
    return {
        "workflow_id": workflow_id,
        "steps_data": steps_data,
        "prior_results": prior_results or [],
        "workflow_description": workflow_description,
        "workflow_agent": mock_agent,
        "agent_loop": mock_loop,
    }


def _make_db_session(context_rules=None, workflow_input=None):
    """Build a mock AutoWorkflow DB object."""
    return SimpleNamespace(
        id=1,
        context_rules=context_rules,
        workflow_input=(
            json.dumps(workflow_input) if isinstance(workflow_input, dict) else workflow_input
        ),
    )


def _make_db_step(step_id=10, step_type="run_command", config=None):
    """Build a mock AutoWorkflowStep DB object."""
    return SimpleNamespace(
        id=step_id,
        step_type=step_type,
        config=json.dumps(config) if isinstance(config, dict) else config,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSendWorkflowInstructionWiring:
    """Verify that _send_workflow_instruction uses assemble_step_context."""

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.workflow.service.build_step_context_prompt", return_value="built prompt")
    @patch("distr.core.db.get_session")
    def test_calls_assemble_step_context(self, mock_get_session, mock_build, mock_assemble, mock_run_coro):
        """assemble_step_context is called with session, step, and prior_results."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session(context_rules="Be concise.")
        db_step = _make_db_step(step_id=10, step_type="agent_instruction")

        mock_db = MagicMock()
        # New query pattern: 1st call = step (for step_type check),
        # 2nd call = workflow (for context assembly), 3rd call = step (for context assembly)
        mock_db.query.return_value.filter.return_value.first.side_effect = [db_step, db_session, db_step]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = StepInputContext(workflow_rules="Be concise.")
        mock_assemble.return_value = mock_ctx

        mixin = WorkflowOrchestrationMixin()
        orch = _make_orch(prior_results=[{"title": "S1", "result": "ok"}])
        mixin._send_workflow_instruction(orch, 0)

        mock_assemble.assert_called_once()
        call_kwargs = mock_assemble.call_args
        assert call_kwargs.kwargs["session"] is db_session
        assert call_kwargs.kwargs["step"] is db_step
        assert call_kwargs.kwargs["prior_results"] == [{"title": "S1", "result": "ok"}]

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.workflow.service.build_step_context_prompt", return_value="built prompt")
    @patch("distr.core.db.get_session")
    def test_uses_workflow_rules_from_assembled_context(self, mock_get_session, mock_build, mock_assemble, mock_run_coro):
        """build_step_context_prompt receives workflow_rules from the assembled context."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session(context_rules="My rules")
        db_step = _make_db_step(step_id=10, step_type="agent_instruction")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [db_step, db_session, db_step]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = StepInputContext(workflow_rules="My rules")
        mock_assemble.return_value = mock_ctx

        mixin = WorkflowOrchestrationMixin()
        orch = _make_orch()
        mixin._send_workflow_instruction(orch, 0)

        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["context_rules"] == "My rules"

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.workflow.service.build_step_context_prompt", return_value="built prompt")
    @patch("distr.core.db.get_session")
    def test_stores_step_input_context_on_orch(self, mock_get_session, mock_build, mock_assemble, mock_run_coro):
        """The assembled StepInputContext is stored on orch['step_input_context']."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="agent_instruction")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [db_step, db_session, db_step]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = StepInputContext(workflow_rules="rules", step_config={"cmd": "ls"})
        mock_assemble.return_value = mock_ctx

        mixin = WorkflowOrchestrationMixin()
        orch = _make_orch()
        mixin._send_workflow_instruction(orch, 0)

        assert orch["step_input_context"] is mock_ctx

    @patch("asyncio.run_coroutine_threadsafe")
    def test_prompt_override_skips_assembly(self, mock_run_coro):
        """When prompt is provided, assemble_step_context is NOT called."""
        mixin = WorkflowOrchestrationMixin()
        orch = _make_orch()
        mixin._send_workflow_instruction(orch, 0, prompt="custom prompt")

        # WorkflowAgent.execute was scheduled via run_coroutine_threadsafe
        mock_run_coro.assert_called_once()
        # step_input_context should not be set (no assembly happened)
        assert "step_input_context" not in orch

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.workflow.service.build_step_context_prompt", return_value="fallback prompt")
    @patch("distr.core.db.get_session")
    def test_fallback_on_db_error(self, mock_get_session, mock_build, mock_assemble, mock_run_coro):
        """If DB access fails, context_rules falls back to empty string."""
        mock_get_session.side_effect = Exception("DB down")

        mixin = WorkflowOrchestrationMixin()
        orch = _make_orch()
        mixin._send_workflow_instruction(orch, 0)

        mock_assemble.assert_not_called()
        assert orch["step_input_context"] is None
        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["context_rules"] == ""
