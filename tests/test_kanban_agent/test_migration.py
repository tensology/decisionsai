# Feature: kanban-cli-settings-restructure
"""
Unit tests for migrate_board_agent_settings_to_global().

Validates: Requirements 9.1, 9.2, 9.3 — One-time migration of per-board agent
settings to global settings, default initialization when no agent-enabled boards
exist, and skip behavior when migration has already completed.
"""
import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard
from distr.core.kanban.migration import migrate_board_agent_settings_to_global, _BOARD_TO_GLOBAL


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


def _make_settings_store():
    """Return a mutable dict and load/save helpers that operate on it."""
    store = {}

    def load():
        return store.copy()

    def save(settings):
        store.clear()
        store.update(settings)

    return store, load, save


_PATCH_SETTINGS_LOAD = "distr.core.kanban.migration.load_settings_from_db"
_PATCH_SETTINGS_SAVE = "distr.core.kanban.migration.save_settings_to_db"
_PATCH_DB = "distr.core.kanban.migration.get_session"


class TestMigrationFromAgentEnabledBoard:
    """Validates: Requirement 9.1 — Migration copies settings from first agent-enabled board."""

    def test_copies_agent_fields_from_enabled_board(self):
        """Settings are copied from the first agent_enabled board to global settings."""
        factory = _make_session_factory()

        # Seed a board with agent_enabled=True and specific values
        session = factory()
        board = KanbanBoard(
            name="My Board",
            agent_enabled=True,
            agent_frequency="weekly",
            agent_time="14:30",
            agent_days='[1,3,5]',
            agent_monthly_day=15,
            agent_orchestrator_provider="openai",
            agent_orchestrator_model="gpt-4",
            agent_coder_provider="anthropic",
            agent_coder_model="claude-3",
            agent_sub_provider="groq",
            agent_sub_model="llama-3",
            agent_source_lane="To Do",
            agent_done_lane="Done",
        )
        session.add(board)
        session.commit()
        session.close()

        store, load_fn, save_fn = _make_settings_store()

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=save_fn), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        # Verify all mapped fields were copied
        assert store["kanban_agent_enabled"] is True
        assert store["kanban_agent_frequency"] == "weekly"
        assert store["kanban_agent_time"] == "14:30"
        assert store["kanban_agent_days"] == "[1,3,5]"
        assert store["kanban_agent_monthly_day"] == 15
        assert store["kanban_agent_orchestrator_provider"] == "openai"
        assert store["kanban_agent_orchestrator_model"] == "gpt-4"
        assert store["kanban_agent_coder_provider"] == "anthropic"
        assert store["kanban_agent_coder_model"] == "claude-3"
        assert store["kanban_agent_sub_provider"] == "groq"
        assert store["kanban_agent_sub_model"] == "llama-3"
        assert store["kanban_agent_source_lane"] == "To Do"
        assert store["kanban_agent_done_lane"] == "Done"
        assert store["_kanban_migration_done"] is True

    def test_picks_first_enabled_board_by_id(self):
        """When multiple boards are agent-enabled, the one with the lowest ID is used."""
        factory = _make_session_factory()

        session = factory()
        board1 = KanbanBoard(name="Board A", agent_enabled=True, agent_frequency="daily",
                             agent_source_lane="Lane A")
        board2 = KanbanBoard(name="Board B", agent_enabled=True, agent_frequency="monthly",
                             agent_source_lane="Lane B")
        session.add_all([board1, board2])
        session.commit()
        session.close()

        store, load_fn, save_fn = _make_settings_store()

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=save_fn), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        # Board A (lower ID) should be the source
        assert store["kanban_agent_frequency"] == "daily"
        assert store["kanban_agent_source_lane"] == "Lane A"


class TestMigrationNoAgentEnabledBoards:
    """Validates: Requirement 9.2 — Defaults initialized when no agent-enabled boards exist."""

    def test_initializes_defaults_when_no_enabled_boards(self):
        """When no boards have agent_enabled=True, global settings get default values."""
        factory = _make_session_factory()

        # Seed a board with agent_enabled=False
        session = factory()
        board = KanbanBoard(name="Disabled Board", agent_enabled=False)
        session.add(board)
        session.commit()
        session.close()

        store, load_fn, save_fn = _make_settings_store()

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=save_fn), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        assert store["kanban_agent_enabled"] is False
        assert store["kanban_agent_frequency"] == "daily"
        assert store["kanban_agent_time"] == "09:00"
        assert store["kanban_agent_hours"] == "[]"
        assert store["kanban_agent_days"] == "[]"
        assert store["kanban_agent_monthly_day"] == 1
        assert store["kanban_agent_source_lane"] == ""
        assert store["kanban_agent_done_lane"] == ""
        assert store["kanban_agent_orchestrator_provider"] == ""
        assert store["kanban_agent_orchestrator_model"] == ""
        assert store["kanban_agent_coder_provider"] == ""
        assert store["kanban_agent_coder_model"] == ""
        assert store["kanban_agent_sub_provider"] == ""
        assert store["kanban_agent_sub_model"] == ""
        assert store["_kanban_migration_done"] is True

    def test_initializes_defaults_when_no_boards_at_all(self):
        """When the kanban_boards table is empty, global settings get default values."""
        factory = _make_session_factory()
        store, load_fn, save_fn = _make_settings_store()

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=save_fn), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        assert store["kanban_agent_enabled"] is False
        assert store["kanban_agent_frequency"] == "daily"
        assert store["_kanban_migration_done"] is True


class TestMigrationSkipsWhenAlreadyDone:
    """Validates: Requirement 9.3 — Migration skips when global settings already exist."""

    def test_skips_when_sentinel_flag_is_set(self):
        """When _kanban_migration_done is True, migration does not overwrite settings."""
        factory = _make_session_factory()

        # Seed a board that would be picked up if migration ran
        session = factory()
        board = KanbanBoard(name="Board", agent_enabled=True, agent_frequency="monthly")
        session.add(board)
        session.commit()
        session.close()

        store, load_fn, save_fn = _make_settings_store()
        # Pre-populate with sentinel and a custom value
        store["_kanban_migration_done"] = True
        store["kanban_agent_frequency"] = "hourly"

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=save_fn), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        # The existing value should be untouched — migration was skipped
        assert store["kanban_agent_frequency"] == "hourly"

    def test_save_not_called_when_skipped(self):
        """save_settings_to_db is never called when migration is skipped."""
        factory = _make_session_factory()
        store, load_fn, save_fn = _make_settings_store()
        store["_kanban_migration_done"] = True

        save_calls = []

        def tracking_save(settings):
            save_calls.append(settings)
            save_fn(settings)

        with patch(_PATCH_SETTINGS_LOAD, side_effect=load_fn), \
             patch(_PATCH_SETTINGS_SAVE, side_effect=tracking_save), \
             patch(_PATCH_DB, side_effect=lambda: _session_ctx(factory)):
            migrate_board_agent_settings_to_global()

        assert len(save_calls) == 0
