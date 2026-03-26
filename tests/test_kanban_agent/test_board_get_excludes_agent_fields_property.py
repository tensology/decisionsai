# Feature: kanban-cli-settings-restructure, Property 7: Board GET response excludes agent configuration fields
"""
Property 7: Board GET response excludes agent configuration fields

For any board, the JSON response from GET /api/kanban/boards/{board_id} should not
contain the keys: agent_orchestrator_provider, agent_orchestrator_model,
agent_coder_provider, agent_coder_model, agent_sub_provider, agent_sub_model,
agent_enabled, agent_frequency, agent_time, agent_days, agent_monthly_day,
agent_source_lane, agent_done_lane.

**Validates: Requirements 7.3, 10.4**
"""
import contextlib
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane

from distr.gui.web.routes.kanban import create_routes


# Agent fields that must NOT appear in the board GET response
EXCLUDED_AGENT_KEYS = {
    "agent_orchestrator_provider",
    "agent_orchestrator_model",
    "agent_coder_provider",
    "agent_coder_model",
    "agent_sub_provider",
    "agent_sub_model",
    "agent_enabled",
    "agent_frequency",
    "agent_time",
    "agent_days",
    "agent_monthly_day",
    "agent_source_lane",
    "agent_done_lane",
}


def _make_session_factory():
    """Create a fresh in-memory SQLite DB with all tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ── Strategies ──

agent_bool_st = st.booleans()
agent_frequency_st = st.sampled_from(["hourly", "daily", "weekly", "fortnightly", "monthly"])
agent_time_st = st.sampled_from(["00:00", "09:00", "12:30", "18:45", "23:59"])
agent_days_st = st.sampled_from(["[]", "[0]", "[1,3,5]", "[0,1,2,3,4,5,6]"])
agent_monthly_day_st = st.integers(min_value=1, max_value=28)
agent_provider_st = st.sampled_from(["", "openai", "anthropic", "google", "local"])
agent_model_st = st.sampled_from(["", "gpt-4", "claude-3", "gemini-pro"])
agent_lane_st = st.sampled_from(["", "Backlog", "Current", "Done", "QA / Assess"])


class TestBoardGetExcludesAgentFieldsProperty:
    """Property 7: Board GET response excludes agent configuration fields."""

    @given(
        agent_enabled=agent_bool_st,
        agent_frequency=agent_frequency_st,
        agent_time=agent_time_st,
        agent_days=agent_days_st,
        agent_monthly_day=agent_monthly_day_st,
        agent_orchestrator_provider=agent_provider_st,
        agent_orchestrator_model=agent_model_st,
        agent_coder_provider=agent_provider_st,
        agent_coder_model=agent_model_st,
        agent_sub_provider=agent_provider_st,
        agent_sub_model=agent_model_st,
        agent_source_lane=agent_lane_st,
        agent_done_lane=agent_lane_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_board_get_response_excludes_agent_keys(
        self,
        agent_enabled,
        agent_frequency,
        agent_time,
        agent_days,
        agent_monthly_day,
        agent_orchestrator_provider,
        agent_orchestrator_model,
        agent_coder_provider,
        agent_coder_model,
        agent_sub_provider,
        agent_sub_model,
        agent_source_lane,
        agent_done_lane,
    ):
        """
        **Validates: Requirements 7.3, 10.4**

        For any board with arbitrary agent column values, the GET response
        should not contain any agent configuration keys.
        """
        factory = _make_session_factory()

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

        # Create a board with agent columns populated
        with patched_get_session() as session:
            board = KanbanBoard(
                name="Test Board",
                description="test",
                source="database",
                agent_enabled=agent_enabled,
                agent_frequency=agent_frequency,
                agent_time=agent_time,
                agent_days=agent_days,
                agent_monthly_day=agent_monthly_day,
                agent_orchestrator_provider=agent_orchestrator_provider,
                agent_orchestrator_model=agent_orchestrator_model,
                agent_coder_provider=agent_coder_provider,
                agent_coder_model=agent_coder_model,
                agent_sub_provider=agent_sub_provider,
                agent_sub_model=agent_sub_model,
                agent_source_lane=agent_source_lane,
                agent_done_lane=agent_done_lane,
            )
            session.add(board)
            session.flush()
            session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
            session.flush()
            board_id = board.id

        # Build app with patched get_session
        app = FastAPI()
        with patch("distr.gui.web.routes.kanban.get_session", patched_get_session):
            router = create_routes()
        app.include_router(router, prefix="/api")

        with patch("distr.gui.web.routes.kanban.get_session", patched_get_session):
            client = TestClient(app)
            resp = client.get(f"/api/kanban/boards/{board_id}")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        data = resp.json()

        # Verify no agent keys are present in the response
        found_keys = EXCLUDED_AGENT_KEYS & set(data.keys())
        assert not found_keys, (
            f"Board GET response should not contain agent keys, but found: {found_keys}"
        )
