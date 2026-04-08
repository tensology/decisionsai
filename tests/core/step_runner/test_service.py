"""Unit tests for Step Runner service — Task 7.2 changes.

Tests that:
- create_scheduled_session auto-creates workflow_input with source_type="scheduled"
- get_session_with_steps includes context_rules and workflow_input
- duplicate_session copies context_rules but NOT workflow_input
- plan_session stores workflow_input when provided
- create_workflow_input helper produces correct dict structure
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from distr.core.workflow.service import (
    create_workflow_input,
    plan_session,
    get_session_with_steps,
    duplicate_session,
    create_scheduled_session,
)


class TestCreateWorkflowInput:
    """Tests for the create_workflow_input helper."""

    def test_basic_instruction_source(self):
        result = create_workflow_input(source_type="instruction", text="do something")
        assert result["source_type"] == "instruction"
        assert result["text"] == "do something"
        assert result["title"] == ""
        assert result["images"] == []
        assert result["attachments"] == []
        assert result["metadata"] == {}

    def test_scheduled_source(self):
        result = create_workflow_input(source_type="scheduled", text="check email")
        assert result["source_type"] == "scheduled"
        assert result["text"] == "check email"

    def test_kanban_ticket_source_with_metadata(self):
        result = create_workflow_input(
            source_type="kanban_ticket",
            text="Fix the bug",
            title="BUG-123",
            images=["/img/screenshot.png"],
            metadata={"ticket_id": "BUG-123"},
        )
        assert result["source_type"] == "kanban_ticket"
        assert result["title"] == "BUG-123"
        assert result["images"] == ["/img/screenshot.png"]
        assert result["metadata"]["ticket_id"] == "BUG-123"

    def test_api_source(self):
        result = create_workflow_input(source_type="api", text="triggered via API")
        assert result["source_type"] == "api"

    def test_all_four_source_types_accepted(self):
        for src in ("instruction", "kanban_ticket", "api", "scheduled"):
            result = create_workflow_input(source_type=src)
            assert result["source_type"] == src


class TestPlanSessionWorkflowInput:
    """Tests that plan_session stores workflow_input on the session."""

    @patch("distr.core.workflow.service._legacy_call_llm_for_plan")
    @patch("distr.core.workflow.service.get_session")
    def test_plan_session_stores_workflow_input(self, mock_get_session, mock_llm):
        """When workflow_input dict is provided, it should be serialized to JSON."""
        mock_llm.return_value = [{"title": "Step 1", "instruction": "do it"}]

        # Set up mock DB session
        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = mock_db

        # Capture what gets added
        added_objects = []
        def capture_add(obj):
            added_objects.append(obj)
            # Simulate flush setting the id
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = 42
        mock_db.add.side_effect = capture_add
        mock_db.flush.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        wf_input = create_workflow_input(source_type="instruction", text="hello")
        plan_session("hello", workflow_input=wf_input)

        # The first added object should be the session
        from distr.core.db.step_runner import StepRunnerSession
        sessions = [o for o in added_objects if isinstance(o, StepRunnerSession)]
        assert len(sessions) == 1
        stored_json = sessions[0].workflow_input
        assert stored_json is not None
        parsed = json.loads(stored_json)
        assert parsed["source_type"] == "instruction"
        assert parsed["text"] == "hello"

    @patch("distr.core.workflow.service._legacy_call_llm_for_plan")
    @patch("distr.core.workflow.service.get_session")
    def test_plan_session_no_workflow_input(self, mock_get_session, mock_llm):
        """When workflow_input is None, the column should be None."""
        mock_llm.return_value = [{"title": "Step 1", "instruction": "do it"}]

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = mock_db

        added_objects = []
        def capture_add(obj):
            added_objects.append(obj)
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = 1
        mock_db.add.side_effect = capture_add
        mock_db.flush.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        plan_session("hello")

        from distr.core.db.step_runner import StepRunnerSession
        sessions = [o for o in added_objects if isinstance(o, StepRunnerSession)]
        assert len(sessions) == 1
        assert sessions[0].workflow_input is None


class TestCreateScheduledSessionWorkflowInput:
    """Tests that create_scheduled_session auto-creates workflow_input."""

    @patch("distr.core.workflow.service.plan_session")
    @patch("distr.core.workflow.scheduler.schedule_to_cron")
    @patch("distr.core.workflow.service.get_session")
    def test_scheduled_session_passes_workflow_input(self, mock_get_session, mock_cron, mock_plan):
        """create_scheduled_session should call plan_session with a scheduled workflow_input."""
        mock_plan.return_value = 99
        mock_cron.return_value = None  # Skip cron setup

        create_scheduled_session("check email", "daily")

        # Verify plan_session was called with workflow_input
        mock_plan.assert_called_once()
        call_args = mock_plan.call_args
        assert call_args[0][0] == "check email"  # instruction
        wf_input = call_args[1].get("workflow_input") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("workflow_input")
        assert wf_input is not None
        assert wf_input["source_type"] == "scheduled"
        assert wf_input["text"] == "check email"


class TestGetSessionWithStepsIncludesNewFields:
    """Tests that get_session_with_steps includes context_rules and workflow_input."""

    @patch("distr.core.workflow.service.get_session")
    @patch("distr.core.workflow.service._get_session_run_history")
    def test_includes_context_rules_and_workflow_input(self, mock_run_history, mock_get_session):
        """Response dict should contain context_rules and workflow_input keys."""
        mock_run_history.return_value = []

        # Build a mock session object
        mock_session_obj = MagicMock()
        mock_session_obj.id = 1
        mock_session_obj.instruction = "test"
        mock_session_obj.status = "planned"
        mock_session_obj.chat_id = None
        mock_session_obj.session_type = "instruction"
        mock_session_obj.schedule = None
        mock_session_obj.next_run_at = None
        mock_session_obj.last_run_at = None
        mock_session_obj.schedule_time = None
        mock_session_obj.schedule_days = None
        mock_session_obj.timezone = None
        mock_session_obj.enabled = True
        mock_session_obj.created_date = None
        mock_session_obj.context_rules = "Always be polite"
        mock_session_obj.workflow_input = json.dumps({"source_type": "instruction", "text": "test"})
        mock_session_obj.steps = []

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_session_obj
        mock_get_session.return_value = mock_db

        result = get_session_with_steps(1)

        assert result is not None
        assert "context_rules" in result
        assert result["context_rules"] == "Always be polite"
        assert "workflow_input" in result
        parsed_wi = json.loads(result["workflow_input"])
        assert parsed_wi["source_type"] == "instruction"

    @patch("distr.core.workflow.service.get_session")
    @patch("distr.core.workflow.service._get_session_run_history")
    def test_includes_none_when_fields_empty(self, mock_run_history, mock_get_session):
        """When context_rules and workflow_input are None, they should still be in the dict."""
        mock_run_history.return_value = []

        mock_session_obj = MagicMock()
        mock_session_obj.id = 2
        mock_session_obj.instruction = "test"
        mock_session_obj.status = "planned"
        mock_session_obj.chat_id = None
        mock_session_obj.session_type = "instruction"
        mock_session_obj.schedule = None
        mock_session_obj.next_run_at = None
        mock_session_obj.last_run_at = None
        mock_session_obj.schedule_time = None
        mock_session_obj.schedule_days = None
        mock_session_obj.timezone = None
        mock_session_obj.enabled = True
        mock_session_obj.created_date = None
        mock_session_obj.context_rules = None
        mock_session_obj.workflow_input = None
        mock_session_obj.steps = []

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_session_obj
        mock_get_session.return_value = mock_db

        result = get_session_with_steps(2)

        assert result is not None
        assert "context_rules" in result
        assert result["context_rules"] is None
        assert "workflow_input" in result
        assert result["workflow_input"] is None


class TestDuplicateSessionCopiesContextRules:
    """Tests that duplicate_session copies context_rules but NOT workflow_input."""

    @patch("distr.core.workflow.service.get_session")
    def test_copies_context_rules(self, mock_get_session):
        """Duplicated session should have the original's context_rules."""
        mock_orig = MagicMock()
        mock_orig.id = 10
        mock_orig.instruction = "original instruction"
        mock_orig.chat_id = 5
        mock_orig.context_rules = "Important rules here"
        mock_orig.workflow_input = json.dumps({"source_type": "instruction", "text": "orig"})
        mock_orig.steps = []

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_orig
        mock_get_session.return_value = mock_db

        added_objects = []
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)
        mock_db.flush.return_value = None
        mock_db.commit.return_value = None

        new_session_mock = MagicMock()
        new_session_mock.id = 20
        mock_db.refresh.side_effect = lambda obj: setattr(obj, 'id', 20)

        duplicate_session(10)

        from distr.core.db.step_runner import StepRunnerSession
        sessions = [o for o in added_objects if isinstance(o, StepRunnerSession)]
        assert len(sessions) == 1
        new_session = sessions[0]
        assert new_session.context_rules == "Important rules here"
        # workflow_input should NOT be copied
        assert not hasattr(new_session, 'workflow_input') or new_session.workflow_input is None
