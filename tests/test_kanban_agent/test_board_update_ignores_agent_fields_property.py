# Feature: kanban-cli-settings-restructure, Property 9: BoardUpdate model does not accept agent fields
"""
Property 9: BoardUpdate model does not accept agent fields

For any PUT request to /api/kanban/boards/{board_id} containing agent-related fields
(agent_enabled, agent_frequency, agent_time, agent_days, agent_monthly_day,
agent_orchestrator_provider, agent_orchestrator_model, agent_coder_provider,
agent_coder_model, agent_sub_provider, agent_sub_model, agent_source_lane,
agent_done_lane), those fields should be ignored and the board's stored agent column
values should remain unchanged.

**Validates: Requirements 10.3**
"""
import asyncio
import contextlib
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane
from distr.gui.web.routes.kanban import create_routes

from unittest.mock import patch

# Agent fields that BoardUpdate should NOT accept
AGENT_FIELDS = [
    "agent_enabled",
    "agent_frequency",
    "agent_time",
    "agent_days",
    "agent_monthly_day",
    "agent_orchestrator_provider",
    "agent_orchestrator_model",
    "agent_coder_provider",
    "agent_coder_model",
    "agent_sub_provider",
    "agent_sub_model",
    "agent_source_lane",
    "agent_done_lane",
]

# Strategies for generating agent field values to send in PUT body
agent_enabled_st = st.booleans()
agent_frequency_st = st.sampled_from(
    ["hourly", "daily", "weekly", "fortnightly", "monthly"]
)
agent_time_st = st.sampled_from(["00:00", "09:00", "12:30", "18:45", "23:59"])
agent_days_st = st.sampled_from(["[]", "[0]", "[1,3,5]", "[0,1,2,3,4,5,6]"])
agent_monthly_day_st = st.integers(min_value=1, max_value=28)
agent_provider_st = st.sampled_from(["openai", "anthropic", "google", "local"])
agent_model_st = st.sampled_from(["gpt-4", "claude-3", "gemini-pro", "llama-3"])
agent_lane_st = st.sampled_from(["Backlog", "Current", "Done", "QA / Assess"])
board_name_st = st.sampled_from(["Updated Board", "New Name", "My Board"])


class TestBoardUpdateIgnoresAgentFieldsProperty:
    """Property 9: BoardUpdate model does not accept agent fields."""

    @given(
        new_name=board_name_st,
        put_agent_enabled=agent_enabled_st,
        put_agent_frequency=agent_frequency_st,
        put_agent_time=agent_time_st,
        put_agent_days=agent_days_st,
        put_agent_monthly_day=agent_monthly_day_st,
        put_agent_orchestrator_provider=agent_provider_st,
        put_agent_orchestrator_model=agent_model_st,
        put_agent_coder_provider=agent_provider_st,
        put_agent_coder_model=agent_model_st,
        put_agent_sub_provider=agent_provider_st,
        put_agent_sub_model=agent_model_st,
        put_agent_source_lane=agent_lane_st,
        put_agent_done_lane=agent_lane_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_board_update_ignores_agent_fields(
        self,
        new_name,
        put_agent_enabled,
        put_agent_frequency,
        put_agent_time,
        put_agent_days,
        put_agent_monthly_day,
        put_agent_orchestrator_provider,
        put_agent_orchestrator_model,
        put_agent_coder_provider,
        put_agent_coder_model,
        put_agent_sub_provider,
        put_agent_sub_model,
        put_agent_source_lane,
        put_agent_done_lane,
    ):
        """
        **Validates: Requirements 10.3**

        For any PUT to /api/kanban/boards/{board_id} containing agent fields,
        those fields should be ignored and the board's agent columns remain unchanged.
        """
        # Fixed initial agent column values on the board
        initial_agent_enabled = False
        initial_agent_frequency = "daily"
        initial_agent_time = "09:00"
        initial_agent_days = "[]"
        initial_agent_monthly_day = 1
        initial_agent_orchestrator_provider = ""
        initial_agent_orchestrator_model = ""
        initial_agent_coder_provider = ""
        initial_agent_coder_model = ""
        initial_agent_sub_provider = ""
        initial_agent_sub_model = ""
        initial_agent_source_lane = ""
        initial_agent_done_lane = ""

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        @contextlib.contextmanager
        def patched_get_session():
            session = factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        # Create a board with known initial agent column values
        with patched_get_session() as session:
            board = KanbanBoard(
                name="Original Board",
                description="original desc",
                source="database",
                agent_enabled=initial_agent_enabled,
                agent_frequency=initial_agent_frequency,
                agent_time=initial_agent_time,
                agent_days=initial_agent_days,
                agent_monthly_day=initial_agent_monthly_day,
                agent_orchestrator_provider=initial_agent_orchestrator_provider,
                agent_orchestrator_model=initial_agent_orchestrator_model,
                agent_coder_provider=initial_agent_coder_provider,
                agent_coder_model=initial_agent_coder_model,
                agent_sub_provider=initial_agent_sub_provider,
                agent_sub_model=initial_agent_sub_model,
                agent_source_lane=initial_agent_source_lane,
                agent_done_lane=initial_agent_done_lane,
            )
            session.add(board)
            session.flush()
            session.add(
                KanbanLane(board_id=board.id, name="Backlog", position=0)
            )
            session.flush()
            board_id = board.id

        # Build a BoardUpdate payload that includes agent fields
        # Pydantic should ignore the agent fields since they're not in BoardUpdate
        from distr.gui.web.routes.kanban import BoardUpdate

        payload_dict = {
            "name": new_name,
            "agent_enabled": put_agent_enabled,
            "agent_frequency": put_agent_frequency,
            "agent_time": put_agent_time,
            "agent_days": put_agent_days,
            "agent_monthly_day": put_agent_monthly_day,
            "agent_orchestrator_provider": put_agent_orchestrator_provider,
            "agent_orchestrator_model": put_agent_orchestrator_model,
            "agent_coder_provider": put_agent_coder_provider,
            "agent_coder_model": put_agent_coder_model,
            "agent_sub_provider": put_agent_sub_provider,
            "agent_sub_model": put_agent_sub_model,
            "agent_source_lane": put_agent_source_lane,
            "agent_done_lane": put_agent_done_lane,
        }

        # Construct the Pydantic model — agent fields should be silently dropped
        board_update = BoardUpdate(**payload_dict)

        # Call the update_board endpoint
        with patch(
            "distr.gui.web.routes.kanban.get_session",
            patched_get_session,
        ):
            router = create_routes()
            update_board_fn = None
            for route in router.routes:
                if hasattr(route, "endpoint"):
                    name = getattr(route.endpoint, "__name__", "")
                    if name == "update_board":
                        update_board_fn = route.endpoint
                        break

            assert update_board_fn is not None, "update_board endpoint not found"

            loop = asyncio.new_event_loop()
            try:
                response = loop.run_until_complete(
                    update_board_fn(board_id, board_update)
                )
            finally:
                loop.close()

        resp_data = json.loads(response.body.decode())
        assert resp_data.get("success") is True

        # Verify agent columns remain unchanged in the database
        with patched_get_session() as session:
            board = session.query(KanbanBoard).get(board_id)
            assert board is not None

            # The board name should have been updated
            assert board.name == new_name

            # All agent columns should remain at their initial values
            assert board.agent_enabled == initial_agent_enabled
            assert board.agent_frequency == initial_agent_frequency
            assert board.agent_time == initial_agent_time
            assert board.agent_days == initial_agent_days
            assert board.agent_monthly_day == initial_agent_monthly_day
            assert board.agent_orchestrator_provider == initial_agent_orchestrator_provider
            assert board.agent_orchestrator_model == initial_agent_orchestrator_model
            assert board.agent_coder_provider == initial_agent_coder_provider
            assert board.agent_coder_model == initial_agent_coder_model
            assert board.agent_sub_provider == initial_agent_sub_provider
            assert board.agent_sub_model == initial_agent_sub_model
            assert board.agent_source_lane == initial_agent_source_lane
            assert board.agent_done_lane == initial_agent_done_lane

        engine.dispose()
