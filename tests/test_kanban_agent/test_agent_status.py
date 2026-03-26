# Feature: kanban-agent-workflow, Property 15: Agent status reflects processing state
"""
Property 15: Agent status reflects processing state

*For any* Agent_Check_In processing ticket K of N total tickets, the agent
status should report `state="running"`, `current_ticket_id` matching ticket
K's ID, `total_tickets=N`, and `processed_count` equal to the number of
tickets already completed.

**Validates: Requirements 10.1**
"""
import contextlib
from unittest.mock import patch, MagicMock

from hypothesis import given, settings
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


def _seed_board(factory, num_tickets):
    """Create a board with source/done lanes, a workflow, and tickets."""
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
    board.default_workflow_id = wf.id

    ticket_ids = []
    for i in range(num_tickets):
        t = KanbanTicket(lane_id=source_lane.id, title=f"Ticket-{i}", position=i)
        session.add(t)
        session.flush()
        ticket_ids.append(t.id)

    board_id = board.id
    session.commit()
    session.close()
    return board_id, ticket_ids


class TestAgentStatusReflectsProcessingState:
    """Property 15: Agent status reflects processing state.

    **Validates: Requirements 10.1**
    """

    @given(
        num_tickets=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=50, deadline=None)
    def test_status_during_processing(self, num_tickets):
        """While processing ticket K of N, status reports running state with correct fields."""
        from distr.core.kanban.agent import KanbanAgentCheckIn, _active_agents

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, ticket_ids = _seed_board(factory, num_tickets)

        # Track status snapshots captured during each ticket's processing
        status_snapshots = []

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
             patch("distr.core.kanban.agent.cancel_run", return_value=True), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)

            original_process = agent._process_ticket

            def capturing_process(board, ticket):
                # Snapshot status BEFORE processing (after _process_ticket sets current_ticket_id)
                # We need to capture after the ticket fields are set but before completion.
                # The _process_ticket method sets current_ticket_id at the start, so we
                # wrap start_workflow_run to capture at that point instead.
                status_snapshots.append({
                    "state": agent.status.state,
                    "current_ticket_id": agent.status.current_ticket_id,
                    "current_ticket_title": agent.status.current_ticket_title,
                    "total_tickets": agent.status.total_tickets,
                    "processed_count": agent.status.processed_count,
                })
                return original_process(board, ticket)

            agent._process_ticket = capturing_process
            agent.run()

            # After run completes, agent should be idle
            assert agent.status.state == "idle"

            # Verify we captured a snapshot for each ticket
            assert len(status_snapshots) == num_tickets

            for k, snap in enumerate(status_snapshots):
                # State should be "running" during processing
                assert snap["state"] == "running", (
                    f"Ticket {k}: expected state='running', got '{snap['state']}'"
                )
                # current_ticket_id should match ticket K's ID
                # Note: for k=0 the snapshot is taken before _process_ticket sets
                # current_ticket_id, so we check it matches the previous ticket or None
                # Actually, our snapshot is taken at the START of _process_ticket wrapper,
                # before the original _process_ticket sets the id. For k>0, the previous
                # ticket's id may still be set. Let's verify total_tickets and processed_count.
                assert snap["total_tickets"] == num_tickets, (
                    f"Ticket {k}: expected total_tickets={num_tickets}, got {snap['total_tickets']}"
                )
                assert snap["processed_count"] == k, (
                    f"Ticket {k}: expected processed_count={k}, got {snap['processed_count']}"
                )

            # Also verify the agent was registered in _active_agents during run
            # (it's removed after run completes)
            assert board_id not in _active_agents

    @given(
        num_tickets=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=50, deadline=None)
    def test_status_current_ticket_id_during_processing(self, num_tickets):
        """current_ticket_id matches the ticket being processed."""
        from distr.core.kanban.agent import KanbanAgentCheckIn

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        board_id, ticket_ids = _seed_board(factory, num_tickets)

        # Capture status inside _process_ticket after it sets current_ticket_id
        mid_process_snapshots = []

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
             patch("distr.core.kanban.agent.cancel_run", return_value=True), \
             patch("distr.core.kanban.agent.set_llm_override", return_value=MagicMock()), \
             patch("distr.core.kanban.agent.clear_llm_override"):
            agent = KanbanAgentCheckIn(board_id)

            original_process = agent._process_ticket

            def capturing_mid_process(board, ticket):
                # Call original first so it sets current_ticket_id
                result = original_process(board, ticket)
                # But we need the snapshot DURING processing, not after.
                # Let's capture it differently — intercept start_workflow_run
                return result

            # Better approach: patch start_workflow_run to capture status mid-flight
            real_mock = mock_start_workflow_run

            def capturing_start_run(workflow_id):
                mid_process_snapshots.append({
                    "state": agent.status.state,
                    "current_ticket_id": agent.status.current_ticket_id,
                    "current_ticket_title": agent.status.current_ticket_title,
                    "total_tickets": agent.status.total_tickets,
                    "processed_count": agent.status.processed_count,
                })
                return real_mock(workflow_id)

            with patch("distr.core.kanban.agent.start_workflow_run", side_effect=capturing_start_run):
                agent.run()

            assert len(mid_process_snapshots) == num_tickets

            for k, snap in enumerate(mid_process_snapshots):
                assert snap["state"] == "running"
                assert snap["current_ticket_id"] == ticket_ids[k], (
                    f"Ticket {k}: expected current_ticket_id={ticket_ids[k]}, "
                    f"got {snap['current_ticket_id']}"
                )
                assert snap["current_ticket_title"] == f"Ticket-{k}"
                assert snap["total_tickets"] == num_tickets
                assert snap["processed_count"] == k
