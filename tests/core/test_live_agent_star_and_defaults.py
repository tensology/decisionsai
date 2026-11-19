"""Live-agent star, last_chat_id isolation, and Settings-only model defaults."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from distr.core.chat_manager import ChatManagerCore


ROOT = Path(__file__).resolve().parents[2]
CHAT_PY = ROOT / "distr/gui/web/routes/chat.py"
CHAT_AGENTS = ROOT / "distr/core/chat_agents.py"
COMMAND_HANDLER = ROOT / "distr/core/agent/command_handler.py"
SIGNALS = ROOT / "distr/app/signals.py"
ACTIONS = ROOT / "distr/gui/web/routes/settings/actions.py"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_create_and_patch_chat_do_not_write_global_model_defaults():
    src = _src(CHAT_PY)
    create = src.split("async def create_chat(", 1)[1].split("async def", 1)[0]
    update = src.split("async def update_chat(", 1)[1].split("async def", 1)[0]
    for block, label in ((create, "create"), (update, "update")):
        assert '_settings["conversational_llm_model"]' not in block, label
        assert '_sett["conversational_llm_model"]' not in block, label
        assert '_settings["llm_model"]' not in block, label
        assert '_sett["llm_model"]' not in block, label
        assert '_settings["agent_model"]' not in block, label
        assert '_sett["agent_model"]' not in block, label
        assert "Defaults" in block or "defaults change only" in block.lower() or "must not overwrite" in block


def test_put_last_chat_id_is_viewed_only_and_does_not_set_live_agent():
    src = _src(ACTIONS)
    block = src.split("async def set_last_chat_id", 1)[1].split("    @router.", 1)[0]
    assert 'update_setting("last_chat_id"' in block
    assert 'update_setting("agent_current_chat_id"' not in block
    assert "viewed" in block.lower()


def test_load_in_agent_syncs_primary_star():
    src = _src(CHAT_PY)
    block = src.split("async def load_chat_in_agent", 1)[1].split("    @router.", 1)[0]
    assert "sync_primary_to_live_chat" in block
    assert 'settings["agent_current_chat_id"] = chat_id' in block


def test_send_to_agent_and_hot_swap_log_routing_mismatch():
    assert "Chat routing mismatch:" in _src(CHAT_PY)
    assert "Chat routing mismatch:" in _src(COMMAND_HANDLER)
    assert "params['model_name'] = model_name" in _src(SIGNALS)
    assert "params['provider'] = provider" in _src(SIGNALS)


def test_sync_primary_to_live_chat_stars_only_that_agent():
    from distr.core.chat_agents import sync_primary_to_live_chat

    agent = SimpleNamespace(id=7, name="Ada", skin_name="oracle", main_chat_id=11, oracle_background=None)
    primary_row = SimpleNamespace(agent_id=3)
    session = MagicMock()
    session.get.side_effect = lambda model, key: primary_row if key == 1 else None
    session.commit = MagicMock()

    with patch("distr.core.chat_agents.get_session") as gs, patch(
        "distr.core.chat_agents.agent_for_conversation", return_value=agent
    ), patch("distr.core.chat_agents.primary_agent_payload", return_value={"agent_id": 7, "main_conversation_id": 11}):
        gs.return_value.__enter__.return_value = session
        payload = sync_primary_to_live_chat(11)

    assert payload["agent_id"] == 7
    assert primary_row.agent_id == 7
    session.commit.assert_called()


def test_sync_primary_to_live_chat_noop_without_agent():
    from distr.core.chat_agents import sync_primary_to_live_chat

    session = MagicMock()
    with patch("distr.core.chat_agents.get_session") as gs, patch(
        "distr.core.chat_agents.agent_for_conversation", return_value=None
    ):
        gs.return_value.__enter__.return_value = session
        assert sync_primary_to_live_chat(11) is None
    session.commit.assert_not_called()


def test_chat_manager_restart_ignores_viewed_last_chat_id():
    fake_settings = SimpleNamespace(
        last_chat_id=5,
        agent_current_chat_id=None,
        conversational_llm_provider="ollama",
        conversational_llm_model="kimi",
        llm_provider=None,
        agent_provider=None,
        agent_model=None,
    )
    session = MagicMock()
    session.query.return_value.first.return_value = fake_settings
    with patch("distr.core.chat_manager.get_session") as gs:
        gs.return_value.__enter__.return_value = session
        cm = ChatManagerCore()
    assert cm.get_current_chat() is None
    session.get.assert_not_called()
