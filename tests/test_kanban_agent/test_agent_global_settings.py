# Feature: kanban-cli-settings-restructure
"""
Unit tests for agent global settings integration.

Validates: Requirements 6.3, 6.4 — Agent reads LLM configuration from global
settings instead of board columns, and falls back to empty strings when global
settings have empty provider/model.
"""
import contextlib
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard
from distr.core.db.workflow import AutoWorkflow
from distr.core.kanban.agent import KanbanAgentCheckIn, _BoardInfo


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


def _seed_board(factory, workflow=True):
    """Create a board with an optional default workflow. Returns board_id."""
    session = factory()
    board = KanbanBoard(
        name="Test Board",
        agent_orchestrator_provider="board-orch-prov",
        agent_orchestrator_model="board-orch-model",
        agent_coder_provider="board-coder-prov",
        agent_coder_model="board-coder-model",
        agent_sub_provider="board-sub-prov",
        agent_sub_model="board-sub-model",
        agent_source_lane="Board Source",
        agent_done_lane="Board Done",
        agent_enabled=True,
    )
    session.add(board)
    session.flush()

    if workflow:
        wf = AutoWorkflow(name="WF")
        session.add(wf)
        session.flush()
        board.default_workflow_id = wf.id

    board_id = board.id
    session.commit()
    session.close()
    return board_id


_PATCH_SETTINGS = "distr.core.kanban.agent.load_settings_from_db"
_PATCH_DB = "distr.core.kanban.agent.get_session"


class TestLoadBoardReadsGlobalSettings:
    """Validates: Requirements 6.3 — Agent reads LLM config from global settings."""

    def test_llm_config_comes_from_global_settings(self):
        """_load_board() populates LLM fields from global settings, not board columns."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Global Source",
            "kanban_agent_done_lane": "Global Done",
            "kanban_agent_orchestrator_provider": "global-orch-prov",
            "kanban_agent_orchestrator_model": "global-orch-model",
            "kanban_agent_coder_provider": "global-coder-prov",
            "kanban_agent_coder_model": "global-coder-model",
            "kanban_agent_sub_provider": "global-sub-prov",
            "kanban_agent_sub_model": "global-sub-model",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is not None
        assert info.agent_orchestrator_provider == "global-orch-prov"
        assert info.agent_orchestrator_model == "global-orch-model"
        assert info.agent_coder_provider == "global-coder-prov"
        assert info.agent_coder_model == "global-coder-model"
        assert info.agent_sub_provider == "global-sub-prov"
        assert info.agent_sub_model == "global-sub-model"

    def test_lane_config_comes_from_global_settings(self):
        """_load_board() populates lane fields from global settings, not board columns."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Global Source",
            "kanban_agent_done_lane": "Global Done",
            "kanban_agent_orchestrator_provider": "",
            "kanban_agent_orchestrator_model": "",
            "kanban_agent_coder_provider": "",
            "kanban_agent_coder_model": "",
            "kanban_agent_sub_provider": "",
            "kanban_agent_sub_model": "",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is not None
        assert info.agent_source_lane == "Global Source"
        assert info.agent_done_lane == "Global Done"
        # Board columns had "Board Source" / "Board Done" — those should NOT appear
        assert info.agent_source_lane != "Board Source"
        assert info.agent_done_lane != "Board Done"

    def test_workflow_id_still_comes_from_board(self):
        """_load_board() reads default_workflow_id from the board record."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "Done",
            "kanban_agent_orchestrator_provider": "",
            "kanban_agent_orchestrator_model": "",
            "kanban_agent_coder_provider": "",
            "kanban_agent_coder_model": "",
            "kanban_agent_sub_provider": "",
            "kanban_agent_sub_model": "",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is not None
        assert info.default_workflow_id is not None


class TestLoadBoardFallbackEmptySettings:
    """Validates: Requirements 6.4 — Fallback when global settings have empty provider/model."""

    def test_empty_provider_model_returns_empty_strings(self):
        """When global settings have empty provider/model, _BoardInfo fields are empty strings."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "Done",
            "kanban_agent_orchestrator_provider": "",
            "kanban_agent_orchestrator_model": "",
            "kanban_agent_coder_provider": "",
            "kanban_agent_coder_model": "",
            "kanban_agent_sub_provider": "",
            "kanban_agent_sub_model": "",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is not None
        assert info.agent_orchestrator_provider == ""
        assert info.agent_orchestrator_model == ""
        assert info.agent_coder_provider == ""
        assert info.agent_coder_model == ""
        assert info.agent_sub_provider == ""
        assert info.agent_sub_model == ""

    def test_missing_keys_default_to_empty_strings(self):
        """When global settings dict is missing LLM keys, _BoardInfo defaults to empty strings."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        # Minimal settings — no LLM keys at all
        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "Done",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is not None
        assert info.agent_orchestrator_provider == ""
        assert info.agent_orchestrator_model == ""
        assert info.agent_coder_provider == ""
        assert info.agent_coder_model == ""
        assert info.agent_sub_provider == ""
        assert info.agent_sub_model == ""


class TestLLMOverrideFromGlobalSettings:
    """Validates: Requirements 6.3 — LLM override is set from global settings values."""

    def test_run_sets_llm_override_from_global_settings(self):
        """run() sets LLM override using values from _load_board (global settings)."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "Done",
            "kanban_agent_orchestrator_provider": "test-orch",
            "kanban_agent_orchestrator_model": "test-orch-model",
            "kanban_agent_coder_provider": "test-coder",
            "kanban_agent_coder_model": "test-coder-model",
            "kanban_agent_sub_provider": "test-sub",
            "kanban_agent_sub_model": "test-sub-model",
        }

        captured_override = {}

        original_set = __import__("distr.core.llm_override", fromlist=["set_llm_override"]).set_llm_override

        def mock_set_override(override):
            captured_override["orchestrator_provider"] = override.orchestrator_provider
            captured_override["orchestrator_model"] = override.orchestrator_model
            captured_override["coder_provider"] = override.coder_provider
            captured_override["coder_model"] = override.coder_model
            captured_override["sub_provider"] = override.sub_provider
            captured_override["sub_model"] = override.sub_model
            return original_set(override)

        agent = KanbanAgentCheckIn(board_id)

        # Provide a dummy ticket so run() reaches set_llm_override
        dummy_tickets = [{"id": 1, "title": "T1", "lane_id": 1, "position": 0}]

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)), \
             patch("distr.core.kanban.agent.set_llm_override", side_effect=mock_set_override), \
             patch("distr.core.kanban.agent.clear_llm_override"), \
             patch.object(agent, "_collect_tickets", return_value=dummy_tickets), \
             patch.object(agent, "_process_ticket", return_value="completed"):
            agent.run()

        assert captured_override["orchestrator_provider"] == "test-orch"
        assert captured_override["orchestrator_model"] == "test-orch-model"
        assert captured_override["coder_provider"] == "test-coder"
        assert captured_override["coder_model"] == "test-coder-model"
        assert captured_override["sub_provider"] == "test-sub"
        assert captured_override["sub_model"] == "test-sub-model"


class TestLoadBoardValidation:
    """Edge cases for _load_board() validation."""

    def test_disabled_agent_returns_none(self):
        """When kanban_agent_enabled is False, _load_board returns None."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": False,
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is None

    def test_missing_source_lane_returns_none(self):
        """When source lane is empty, _load_board returns None."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "",
            "kanban_agent_done_lane": "Done",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is None

    def test_missing_done_lane_returns_none(self):
        """When done lane is empty, _load_board returns None."""
        factory = _make_session_factory()
        board_id = _seed_board(factory)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is None

    def test_board_without_workflow_returns_none(self):
        """When board has no default_workflow_id, _load_board returns None."""
        factory = _make_session_factory()
        board_id = _seed_board(factory, workflow=False)

        settings = {
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Source",
            "kanban_agent_done_lane": "Done",
        }

        agent = KanbanAgentCheckIn(board_id)

        with patch(_PATCH_SETTINGS, return_value=settings), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            info = agent._load_board()

        assert info is None
