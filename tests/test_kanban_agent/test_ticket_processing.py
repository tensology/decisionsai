# Feature: kanban-agent-workflow
"""
Property tests for Kanban Agent ticket processing.

Property 4: Workflow invocation uses board's default workflow
Property 5: Failed run leaves ticket in source lane
Property 8: Successful run moves ticket to done lane
Property 9: Moved ticket position is max + 1
Property 10: All source lane tickets are processed
"""
import contextlib
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket  # noqa: F401
from distr.core.db.workflow import (  # noqa: F401
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)


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


def _seed_board(factory, num_tickets=1, done_ticket_positions=None):
    """Create a board with source/done lanes, a workflow, and tickets.

    Returns (board_id, source_lane_id, done_lane_id, workflow_id, ticket_ids).
    """
    session = factory()
    board = KanbanBoard(
        name="Test Board",
        agent_enabled=True,
        agent_source_lane="Source",
        agent_done_lane="Done",
    )
    session.add(board)
    session.flush()

    source_lane = KanbanLane(board_id=board.id, name="Source", position=0)
    done_lane = KanbanLane(board_id=board.id, name="Done", position=1)
    session.add_all([source_lane, done_lane])
    session.flush()

    wf = AutoWorkflow(name="Test Workflow")
    session.add(wf)
    session.flush()
    wf_id = wf.id

    board.default_workflow_id = wf_id

    ticket_ids = []
    for i in range(num_tickets):
        t = KanbanTicket(lane_id=source_lane.id, title=f"Ticket-{i}", position=i)
        session.add(t)
        session.flush()
        ticket_ids.append(t.id)

    # Add existing tickets in done lane if specified
    if done_ticket_positions:
        for pos in done_ticket_positions:
            session.add(KanbanTicket(lane_id=done_lane.id, title=f"Done-{pos}", position=pos))

    board_id = board.id
    source_lane_id = source_lane.id
    done_lane_id = done_lane.id
    session.commit()
    session.close()
    return board_id, source_lane_id, done_lane_id, wf_id, ticket_ids


class TestWorkflowInvocation:
    """Property 4: Workflow invocation uses board's default workflow.

    *For any* Kanban board with a configured `default_workflow_id` and tickets
    in the source lane, the Agent_Check_In should invoke `start_workflow_run`
    with the board's `default_workflow_id` for each ticket.

    **Validates: Requirements 2.3**
    """

    @given(
        num_tickets=st.integers(min_value=1, max_value=5),
        workflow_id_offset=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50, deadline=None)
    def test_start_workflow_called_with_default_id(self, num_tickets, workflow_id_offset):
        """start_workflow_run is called with the board's default_workflow_id for each ticket."""
        from distr.core.kanban.agent import KanbanAgentCheckIn, _active_agents

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, source_lane_id, done_lane_id, wf_id, ticket_ids = _seed_board(
            factory, num_tickets=num_tickets
        )

        call_args = []

        def mock_start_workflow_run(workflow_id):
            call_args.append(workflow_id)
            # Create a completed run record so _wait_for_run terminates
            session = factory()
            run = AutoWorkflowRun(workflow_id=workflow_id, status="completed")
            session.add(run)
            session.commit()
            run_id = run.id
            session.close()
            return {"run_id": run_id}

        mock_settings = {
            'kanban_agent_enabled': True,
            'kanban_agent_source_lane': 'Source',
            'kanban_agent_done_lane': 'Done',
            'kanban_agent_orchestrator_provider': '',
            'kanban_agent_orchestrator_model': '',
            'kanban_agent_coder_provider': '',
            'kanban_agent_coder_model': '',
            'kanban_agent_sub_provider': '',
            'kanban_agent_sub_model': '',
        }

        with patch("distr.core.kanban.agent.get_session", patched_get_session), \
             patch("distr.core.kanban.agent.load_settings_from_db", return_value=mock_settings), \
             patch("distr.core.kanban.agent.start_workflow_run", side_effect=mock_start_workflow_run), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)
            agent.run()

        assert len(call_args) == num_tickets
        for called_wf_id in call_args:
            assert called_wf_id == wf_id


class TestFailedRunLeavesTicket:
    """Property 5: Failed run leaves ticket in source lane.

    *For any* ticket whose associated AutoWorkflow_Run completes with status
    "failed" or "cancelled", the ticket's `lane_id` should remain unchanged
    (still in the source lane).

    **Validates: Requirements 2.4**
    """

    @given(
        fail_status=st.sampled_from(["failed", "cancelled"]),
    )
    @settings(max_examples=50, deadline=None)
    def test_failed_run_keeps_ticket_in_source(self, fail_status):
        """Ticket stays in source lane when run ends with failed/cancelled."""
        from distr.core.kanban.agent import KanbanAgentCheckIn

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, source_lane_id, done_lane_id, wf_id, ticket_ids = _seed_board(
            factory, num_tickets=1
        )

        def mock_start_workflow_run(workflow_id):
            session = factory()
            run = AutoWorkflowRun(workflow_id=workflow_id, status=fail_status)
            session.add(run)
            session.commit()
            run_id = run.id
            session.close()
            return {"run_id": run_id}

        mock_settings = {
            'kanban_agent_enabled': True,
            'kanban_agent_source_lane': 'Source',
            'kanban_agent_done_lane': 'Done',
            'kanban_agent_orchestrator_provider': '',
            'kanban_agent_orchestrator_model': '',
            'kanban_agent_coder_provider': '',
            'kanban_agent_coder_model': '',
            'kanban_agent_sub_provider': '',
            'kanban_agent_sub_model': '',
        }

        with patch("distr.core.kanban.agent.get_session", patched_get_session), \
             patch("distr.core.kanban.agent.load_settings_from_db", return_value=mock_settings), \
             patch("distr.core.kanban.agent.start_workflow_run", side_effect=mock_start_workflow_run), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)
            agent.run()

        # Verify ticket is still in source lane
        session = factory()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_ids[0]).first()
        assert ticket.lane_id == source_lane_id
        session.close()


class TestSuccessfulRunMovesTicket:
    """Property 8: Successful run moves ticket to done lane.

    *For any* ticket whose associated AutoWorkflow_Run completes with status
    "completed", the ticket's `lane_id` should be updated to the done lane's ID.

    **Validates: Requirements 3.1**
    """

    @given(
        num_tickets=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_completed_run_moves_ticket_to_done(self, num_tickets):
        """Tickets are moved to done lane when run completes successfully."""
        from distr.core.kanban.agent import KanbanAgentCheckIn

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, source_lane_id, done_lane_id, wf_id, ticket_ids = _seed_board(
            factory, num_tickets=num_tickets
        )

        def mock_start_workflow_run(workflow_id):
            session = factory()
            run = AutoWorkflowRun(workflow_id=workflow_id, status="completed")
            session.add(run)
            session.commit()
            run_id = run.id
            session.close()
            return {"run_id": run_id}

        mock_settings = {
            'kanban_agent_enabled': True,
            'kanban_agent_source_lane': 'Source',
            'kanban_agent_done_lane': 'Done',
            'kanban_agent_orchestrator_provider': '',
            'kanban_agent_orchestrator_model': '',
            'kanban_agent_coder_provider': '',
            'kanban_agent_coder_model': '',
            'kanban_agent_sub_provider': '',
            'kanban_agent_sub_model': '',
        }

        with patch("distr.core.kanban.agent.get_session", patched_get_session), \
             patch("distr.core.kanban.agent.load_settings_from_db", return_value=mock_settings), \
             patch("distr.core.kanban.agent.start_workflow_run", side_effect=mock_start_workflow_run), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)
            agent.run()

        # All tickets should be in done lane
        session = factory()
        for tid in ticket_ids:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == tid).first()
            assert ticket.lane_id == done_lane_id
        session.close()


class TestMovedTicketPosition:
    """Property 9: Moved ticket position is max + 1.

    *For any* done lane with N existing tickets at various positions, when a
    ticket is moved to the done lane, its position should be set to
    `max(existing_positions) + 1`. If the done lane is empty, position should be 0.

    **Validates: Requirements 3.2**
    """

    @given(
        existing_positions=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=0,
            max_size=10,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_moved_ticket_gets_correct_position(self, existing_positions):
        """Moved ticket position is max(existing) + 1, or 0 if done lane is empty."""
        from distr.core.kanban.agent import KanbanAgentCheckIn

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, source_lane_id, done_lane_id, wf_id, ticket_ids = _seed_board(
            factory, num_tickets=1, done_ticket_positions=existing_positions
        )

        def mock_start_workflow_run(workflow_id):
            session = factory()
            run = AutoWorkflowRun(workflow_id=workflow_id, status="completed")
            session.add(run)
            session.commit()
            run_id = run.id
            session.close()
            return {"run_id": run_id}

        mock_settings = {
            'kanban_agent_enabled': True,
            'kanban_agent_source_lane': 'Source',
            'kanban_agent_done_lane': 'Done',
            'kanban_agent_orchestrator_provider': '',
            'kanban_agent_orchestrator_model': '',
            'kanban_agent_coder_provider': '',
            'kanban_agent_coder_model': '',
            'kanban_agent_sub_provider': '',
            'kanban_agent_sub_model': '',
        }

        with patch("distr.core.kanban.agent.get_session", patched_get_session), \
             patch("distr.core.kanban.agent.load_settings_from_db", return_value=mock_settings), \
             patch("distr.core.kanban.agent.start_workflow_run", side_effect=mock_start_workflow_run), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)
            agent.run()

        # Check the moved ticket's position
        session = factory()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_ids[0]).first()
        expected_pos = 0 if not existing_positions else max(existing_positions) + 1
        assert ticket.position == expected_pos
        assert ticket.lane_id == done_lane_id
        session.close()


class TestAllTicketsProcessed:
    """Property 10: All source lane tickets are processed.

    *For any* source lane with N tickets and a valid workflow, after the
    Agent_Check_In completes (without cancellation), the processed count
    should equal N.

    **Validates: Requirements 3.4**
    """

    @given(
        num_tickets=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=50, deadline=None)
    def test_processed_count_equals_ticket_count(self, num_tickets):
        """After completion, processed_count equals the number of source tickets."""
        from distr.core.kanban.agent import KanbanAgentCheckIn

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, source_lane_id, done_lane_id, wf_id, ticket_ids = _seed_board(
            factory, num_tickets=num_tickets
        )

        def mock_start_workflow_run(workflow_id):
            session = factory()
            run = AutoWorkflowRun(workflow_id=workflow_id, status="completed")
            session.add(run)
            session.commit()
            run_id = run.id
            session.close()
            return {"run_id": run_id}

        mock_settings = {
            'kanban_agent_enabled': True,
            'kanban_agent_source_lane': 'Source',
            'kanban_agent_done_lane': 'Done',
            'kanban_agent_orchestrator_provider': '',
            'kanban_agent_orchestrator_model': '',
            'kanban_agent_coder_provider': '',
            'kanban_agent_coder_model': '',
            'kanban_agent_sub_provider': '',
            'kanban_agent_sub_model': '',
        }

        with patch("distr.core.kanban.agent.get_session", patched_get_session), \
             patch("distr.core.kanban.agent.load_settings_from_db", return_value=mock_settings), \
             patch("distr.core.kanban.agent.start_workflow_run", side_effect=mock_start_workflow_run), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)
            agent.run()

            assert agent.status.processed_count == num_tickets
