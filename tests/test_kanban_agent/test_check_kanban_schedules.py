# Feature: kanban-cli-settings-restructure
"""
Unit tests for check_kanban_schedules() reading from global settings.

Validates: Requirements 8.9 — Scheduler reads check-in configuration from
global settings instead of per-board columns.
"""
import contextlib
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.kanban.scheduler import check_kanban_schedules


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


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


def _seed_board_with_workflow(factory, board_name="Board", has_workflow=True,
                               last_run_hours_ago=None):
    """Create a board with an optional workflow and optional last run.

    Returns (board_id, workflow_id).
    """
    session = factory()
    board = KanbanBoard(name=board_name, created_date=datetime(2024, 1, 1, 0, 0, 0))
    session.add(board)
    session.flush()

    wf_id = None
    if has_workflow:
        wf = AutoWorkflow(name="Test Workflow")
        session.add(wf)
        session.flush()
        wf_id = wf.id
        board.default_workflow_id = wf_id

        if last_run_hours_ago is not None:
            run = AutoWorkflowRun(
                workflow_id=wf_id,
                board_id=board.id,
                started_at=datetime.utcnow() - timedelta(hours=last_run_hours_ago),
                status="completed",
            )
            session.add(run)

    board_id = board.id
    session.commit()
    session.close()
    return board_id, wf_id


# Scheduler imports start_agent_checkin inside the function — patch that binding.
_PATCH_DB = "distr.core.db.get_session"
_PATCH_SETTINGS = "distr.core.settings.load_settings_from_db"
_PATCH_START_AGENT = "distr.core.kanban.agent.start_agent_checkin"


class TestCheckKanbanSchedulesGlobalSettings:
    """Validates: Requirements 8.9 — Scheduler reads from global settings."""

    def test_disabled_agent_does_not_fire(self):
        """When kanban_agent_enabled is False, no agents are fired."""
        factory = _make_session_factory()

        settings = {
            "kanban_agent_enabled": False,
            "kanban_agent_frequency": "daily",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": "[]",
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        board_id, wf_id = _seed_board_with_workflow(factory, last_run_hours_ago=48)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            mock_start.assert_not_called()

    def test_enabled_agent_fires_for_due_board(self):
        """When enabled and schedule is due, fires agent for board with workflow."""
        factory = _make_session_factory()
        board_id, wf_id = _seed_board_with_workflow(factory, last_run_hours_ago=48)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_frequency": "daily",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": "[]",
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            mock_start.assert_called_once_with(board_id)

    def test_board_without_workflow_is_skipped(self):
        """Boards without a default_workflow_id are not considered."""
        factory = _make_session_factory()
        _seed_board_with_workflow(factory, has_workflow=False)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_frequency": "daily",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": "[]",
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            mock_start.assert_not_called()

    def test_uses_global_frequency_not_board_frequency(self):
        """Global frequency setting is used, not the board's agent_frequency column."""
        factory = _make_session_factory()

        # Board has agent_frequency='monthly' but global says 'daily'
        session = factory()
        board = KanbanBoard(
            name="Board",
            agent_frequency="monthly",
            created_date=datetime(2024, 1, 1, 0, 0, 0),
        )
        session.add(board)
        session.flush()
        wf = AutoWorkflow(name="WF")
        session.add(wf)
        session.flush()
        board.default_workflow_id = wf.id
        # Last run 2 days ago — daily would be due, monthly would not
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            board_id=board.id,
            started_at=datetime.utcnow() - timedelta(days=2),
            status="completed",
        )
        session.add(run)
        board_id = board.id
        session.commit()
        session.close()

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_frequency": "daily",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": "[]",
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            # Should fire because global says daily and last run was 2 days ago
            mock_start.assert_called_once_with(board_id)

    def test_hourly_uses_global_hours(self):
        """Hourly frequency uses kanban_agent_hours from global settings."""
        factory = _make_session_factory()
        board_id, wf_id = _seed_board_with_workflow(factory, last_run_hours_ago=3)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_frequency": "hourly",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": json.dumps(list(range(24))),
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            # With all 24 hours selected and last run 3 hours ago, should be due
            mock_start.assert_called_once_with(board_id)

    def test_multiple_boards_with_workflows_all_checked(self):
        """All boards with default_workflow_id are checked, not just agent_enabled ones."""
        factory = _make_session_factory()
        board_id1, _ = _seed_board_with_workflow(factory, board_name="Board1", last_run_hours_ago=48)
        board_id2, _ = _seed_board_with_workflow(factory, board_name="Board2", last_run_hours_ago=48)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_frequency": "daily",
            "kanban_agent_time": "09:00",
            "kanban_agent_hours": "[]",
            "kanban_agent_days": "[]",
            "kanban_agent_monthly_day": 1,
        }

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch(_PATCH_START_AGENT) as mock_start:
            check_kanban_schedules()
            fired_board_ids = [call.args[0] for call in mock_start.call_args_list]

        assert board_id1 in fired_board_ids
        assert board_id2 in fired_board_ids
