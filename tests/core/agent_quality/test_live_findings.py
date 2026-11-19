"""Regressions for what the isolated live-like run exposed on 19 Nov 2026.

1. Chat never failed over: BaseLLMService._surface_model_error shadowed the
   mixin method that tries failover.
2. With every route on OpenRouter, failover found no backup even though
   OpenAI, Kilo and NVIDIA keys were set (no model configured for them).
3. The provider-switch notice closed the turn before the backup answered.
4. Fast actions that only speak (clipboard read-aloud) never closed their turn;
   that is why the 11 Nov "read from the clipboard" turn stayed open for hours
   and later workflow updates attached to it.
5. Preferences told in one chat were never remembered in another.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from distr.core import provider_failover as pf

from .conftest import run_isolated

ONLY_OPENROUTER = {
    "openrouter_key": "sk-test-openrouter",
    "openai_key": "sk-test-openai",
    "kilo_key": "kilo-test",
    "nvidia_key": "nv-test",
    "llm_provider": "OpenRouter",
    "llm_model": "deepseek/deepseek-v4.1-flash",
    "step_runner_llm_provider": "openrouter",
    "step_runner_llm_model": "deepseek/deepseek-v4.1-flash",
}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from distr.core.services import model_catalog_cache

    (tmp_path / "openai_abc.json").write_text(json.dumps({"models": [
        {"id": "gpt-5.4-mini"}, {"id": "gpt-5.4"}, {"id": "text-embedding-3-small"}]}))
    (tmp_path / "kilocode_x.json").write_text(json.dumps({"models": [
        {"id": "deepseek/deepseek-v4.1-flash"}, {"id": "deepseek/deepseek-v4.1-flash:batch"}]}))
    (tmp_path / "nvidia_y.json").write_text(json.dumps({"models": [{"id": "deepseek-ai/deepseek-v4.1-flash"}]}))
    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "load_failover_order", lambda settings=None: [dict(i) for i in pf.DEFAULT_FAILOVER_ORDER])
    return tmp_path


def test_openrouter_down_fails_over_to_keyed_providers_without_configured_models(catalog):
    got = [(t.provider, t.model) for t in pf.failover_candidates(
        "openrouter", "deepseek/deepseek-v4.1-flash", ONLY_OPENROUTER)]
    assert got == [
        ("openai", "gpt-5.4-mini"),
        ("kilocode", "deepseek/deepseek-v4.1-flash"),
        ("nvidia", "deepseek-ai/deepseek-v4.1-flash"),
    ]


def test_catalog_backup_never_picks_batch_or_embedding_models(catalog):
    assert all(pf.is_chat_capable_model(m) for m in pf.catalog_fallback_models("kilocode", "deepseek/deepseek-v4.1-flash:batch"))
    assert "text-embedding-3-small" not in pf.catalog_fallback_models("openai", "text-embedding-3-small")


def test_unkeyed_provider_is_still_skipped(catalog):
    settings = dict(ONLY_OPENROUTER, openai_key="")
    got = [t.provider for t in pf.failover_candidates("openrouter", "deepseek/deepseek-v4.1-flash", settings)]
    assert "openai" not in got


def test_base_service_surface_error_tries_failover_first():
    from distr.core.agent.services.llm.base_service import BaseLLMService

    posted = []
    fake = SimpleNamespace(
        _try_provider_failover=lambda exc: (True, "OpenRouter is not responding, so I switched to OpenAI."),
        _post_provider_notice=posted.append,
    )
    out = run_isolated(BaseLLMService._surface_model_error(fake, RuntimeError("Connection error.")))
    assert out == ""
    assert posted == ["OpenRouter is not responding, so I switched to OpenAI."]


def test_provider_notice_does_not_finalize_the_turn(monkeypatch):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
    from distr.core.agent import tool_audit

    pf.reset_notice_state()
    calls = []
    manager = SimpleNamespace(
        get_current_chat=lambda: 42,
        add_assistant_message=lambda chat_id, text, **kw: calls.append((chat_id, text, kw)),
    )
    monkeypatch.setattr(tool_audit, "record_tool_execution", lambda *a, **k: calls.append(("activity", a[1])))
    fake = SimpleNamespace(chat_manager=manager, event_queue=None, _get_provider_name=lambda: "OpenRouter")
    LLMSharedMixin._post_provider_notice(fake, "OpenRouter is not responding, so I switched to OpenAI.")
    assert calls[0][2].get("is_final") is False
    assert ("activity", "provider_failover") in calls


def test_speak_only_fast_action_closes_its_own_turn(monkeypatch):
    from distr.core.agent.services.llm.mixins import fast_actions as fa
    from distr.core import chat_turns

    closed = []
    monkeypatch.setattr(chat_turns, "complete_turn", lambda chat_id, **kw: closed.append((chat_id, kw)))
    fake = SimpleNamespace()
    action = SimpleNamespace(response_type="tts_clipboard")
    fa.FastActionMixin._fa_close_turn(fake, 7, 1030, action, "CLIPBOARD CONTENT: hello")
    assert closed == [(7, {"turn_id": 1030, "display_text": "Read the clipboard aloud."})]
    assert {"tts", "tts_clipboard", "action_playback"} <= fa._SELF_CONTAINED_RESPONSE_TYPES
    # LLM-backed fast actions finish through the normal reply path instead.
    assert "llm_response" not in fa._SELF_CONTAINED_RESPONSE_TYPES


def test_fast_action_closes_the_turn_captured_at_start(monkeypatch):
    """A newer message sent during a long read-aloud must not be closed instead."""
    from distr.core.agent.services.llm.mixins import fast_actions as fa

    turns = iter([1030, 1031])  # active turn at start, then the user's next message
    monkeypatch.setattr(fa, "_open_turn_id", lambda chat_id: next(turns))
    monkeypatch.setattr(fa, "tool_execution_block_reason", lambda *a, **k: "")
    monkeypatch.setattr(fa, "remember_successful_tool_call", lambda *a, **k: None)
    from distr.core.agent import tool_audit
    monkeypatch.setattr(tool_audit, "record_tool_execution", lambda *a, **k: None)
    monkeypatch.setattr(tool_audit, "record_tool_start", lambda *a, **k: None)
    closed = []

    class Tool:
        name = "clipboard_action"

        def _run(self, **kw):
            return "CLIPBOARD CONTENT: hi"

    class Svc(fa.FastActionMixin):
        _tools_dict = {"clipboard_action": Tool()}
        _tools = []
        event_queue = None

        def _normalize_tool_kwargs(self, tool, args):
            return dict(args)

        def _last_user_message_text(self):
            return "read from the clipboard"

        async def _fa_handle_tts_clipboard(self, *a):
            return True

        def _fa_close_turn(self, chat_id, turn_id, action, result):
            closed.append((chat_id, turn_id))

    action = SimpleNamespace(action_type=SimpleNamespace(value="clipboard"), tool_name="clipboard_action",
                             tool_args={"action": "read"}, response_type="tts_clipboard", original_text="read from the clipboard")
    assert run_isolated(Svc()._execute_fast_action(action, 7)) is True
    assert closed == [(7, 1030)]


def test_memory_captures_preferences_and_decisions_but_not_secrets():
    from distr.core.agent.memory_capture import extract_memories

    assert extract_memories("Something for you to remember: I always want time estimates in agent minutes.") == [
        ("user", "I always want time estimates in agent minutes.")]
    assert extract_memories("We decided to go with Kilo as the backup provider.") == [
        ("memory", "We decided to go with Kilo as the backup provider.")]
    assert extract_memories("Remember my password is hunter2 and the key sk-abcdefghijkl") == []
    assert extract_memories("Do I always want estimates in minutes?") == []
    assert extract_memories("read from the clipboard") == []


def test_memory_capture_dedupes_and_feeds_the_prompt(tmp_path):
    from distr.core.agent import memory_capture as mc

    assert mc.capture("Remember that I always want estimates in agent minutes.", chat_id=5, root=tmp_path)
    assert mc.capture("remember: I always want estimates in agent minutes", root=tmp_path) == []
    block = mc.memory_prompt_block(mc.remembered_entries(root=tmp_path))
    assert "What you remember from earlier chats" in block
    assert "agent minutes" in block
    assert "You told me earlier" in block  # the agent is asked to say which memory it used
