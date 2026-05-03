# Feature: kanban-agent-workflow
"""
Property tests for Kanban Agent lane resolution and ticket collection.

Property 1: Lane resolution by name
Property 2: Ticket collection ordering
"""
import contextlib
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket  # noqa: F401
from distr.core.db.workflow import (  # noqa: F401 — ensure models registered
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)


def _make_session_factory():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    """SessionContext-compatible context manager for patching get_session."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Strategies
_lane_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=30,
)


class TestLaneResolutionByName:
    """Property 1: Lane resolution by name.

    *For any* Kanban board with a set of lanes and a configured `agent_source_lane`
    string, the lane resolution function should return the lane whose name matches
    the string, or None if no lane matches. The match should be exact (case-sensitive).

    **Validates: Requirements 1.1**
    """

    @given(
        lane_names=st.lists(_lane_name_st, min_size=1, max_size=5, unique=True),
        pick_index=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50, deadline=None)
    def test_resolve_existing_lane(self, lane_names, pick_index):
        """Resolving a lane name that exists returns the matching lane."""
        from distr.core.kanban.agent import _resolve_lane

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.kanban.agent.get_session", patched_get_session):
            # Seed board and lanes
            session = factory()
            board = KanbanBoard(name="Test Board", agent_enabled=True)
            session.add(board)
            session.flush()
            board_id = board.id

            for i, name in enumerate(lane_names):
                session.add(KanbanLane(board_id=board_id, name=name, position=i))
            session.commit()
            session.close()

            # Pick one lane name
            target_name = lane_names[pick_index % len(lane_names)]
            result = _resolve_lane(board_id, target_name)

            assert result is not None
            assert result.name == target_name
            assert result.board_id == board_id

    @given(
        lane_names=st.lists(_lane_name_st, min_size=1, max_size=5, unique=True),
        query_name=_lane_name_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_resolve_nonexistent_lane_returns_none(self, lane_names, query_name):
        """Resolving a lane name that doesn't exist returns None."""
        assume(query_name not in lane_names)

        from distr.core.kanban.agent import _resolve_lane

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.kanban.agent.get_session", patched_get_session):
            session = factory()
            board = KanbanBoard(name="Test Board", agent_enabled=True)
            session.add(board)
            session.flush()
            board_id = board.id

            for i, name in enumerate(lane_names):
                session.add(KanbanLane(board_id=board_id, name=name, position=i))
            session.commit()
            session.close()

            result = _resolve_lane(board_id, query_name)
            assert result is None

    @given(
        lane_names=st.lists(_lane_name_st, min_size=2, max_size=5, unique=True),
    )
    @settings(max_examples=50, deadline=None)
    def test_resolve_is_case_sensitive(self, lane_names):
        """Lane resolution is case-sensitive — swapping case should not match."""
        from distr.core.kanban.agent import _resolve_lane

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.kanban.agent.get_session", patched_get_session):
            session = factory()
            board = KanbanBoard(name="Test Board", agent_enabled=True)
            session.add(board)
            session.flush()
            board_id = board.id

            for i, name in enumerate(lane_names):
                session.add(KanbanLane(board_id=board_id, name=name, position=i))
            session.commit()
            session.close()

            target = lane_names[0]
            swapped = target.swapcase()
            if swapped != target:
                result = _resolve_lane(board_id, swapped)
                assert result is None


class TestTicketCollectionOrdering:
    """Property 2: Ticket collection ordering.

    *For any* set of Kanban tickets in a lane with arbitrary position values,
    collecting tickets from that lane should return them ordered by position
    ascending. The resulting list length should equal the number of tickets
    in the lane.

    **Validates: Requirements 1.2, 2.1**
    """

    @given(
        positions=st.lists(st.integers(min_value=0, max_value=10000), min_size=1, max_size=20),
    )
    @settings(max_examples=50, deadline=None)
    def test_tickets_ordered_by_position_ascending(self, positions):
        """Collected tickets are ordered by position ascending with correct count."""
        from distr.core.kanban.agent import KanbanAgentCheckIn, _BoardInfo

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.kanban.agent.get_session", patched_get_session):
            session = factory()
            board = KanbanBoard(
                name="Test Board",
                agent_enabled=True,
                agent_source_lane="Source",
                agent_done_lane="Done",
                default_workflow_id=1,
            )
            session.add(board)
            session.flush()
            board_id = board.id

            source_lane = KanbanLane(board_id=board_id, name="Source", position=0)
            session.add(source_lane)
            session.flush()
            source_lane_id = source_lane.id

            for i, pos in enumerate(positions):
                session.add(KanbanTicket(
                    lane_id=source_lane_id,
                    title=f"Ticket-{i}",
                    position=pos,
                ))
            session.commit()
            session.close()

            board_info = _BoardInfo(
                id=board_id,
                name="Test Board",
                agent_enabled=True,
                agent_source_lane="Source",
                agent_done_lane="Done",
                default_workflow_id=1,
            )

            agent = KanbanAgentCheckIn(board_id)
            tickets = agent._collect_tickets(board_info)

            # Length matches
            assert len(tickets) == len(positions)

            # Ordered by position ascending
            ticket_positions = [t["position"] for t in tickets]
            assert ticket_positions == sorted(positions)
