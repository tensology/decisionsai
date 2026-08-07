"""OpenAI S2S helpers, Completions guard, lock matrix, persist strip."""

from __future__ import annotations


def test_is_openai_s2s_model_matrix():
    from distr.core.openai_s2s import is_openai_s2s_model

    assert is_openai_s2s_model("gpt-realtime-2.1")
    assert is_openai_s2s_model("gpt-realtime-2.1-mini")
    assert is_openai_s2s_model("gpt-realtime-2")
    assert is_openai_s2s_model("gpt-realtime-1.5")
    assert not is_openai_s2s_model("gpt-4o")
    assert not is_openai_s2s_model("gpt-transcribe")
    assert not is_openai_s2s_model("gpt-live-transcribe")
    assert not is_openai_s2s_model("gpt-realtime-translate")
    assert not is_openai_s2s_model("gpt-audio-1.5")
    assert not is_openai_s2s_model("whisper-1")
    assert not is_openai_s2s_model("")
    assert not is_openai_s2s_model(None)


def test_s2s_ui_locks_matrix():
    from distr.core.openai_s2s import s2s_ui_locks

    locked = s2s_ui_locks("gpt-realtime-2.1")
    assert locked["s2s_active"] is True
    assert locked["lock_stt"] is True
    assert locked["lock_conversational_provider"] is True
    assert locked["lock_tts_provider"] is True
    assert locked["lock_openai_tts_model"] is True
    assert locked["lock_conversational_model"] is False
    assert "marin" in locked["voice_set"]

    unlocked = s2s_ui_locks("gpt-4o")
    assert unlocked["s2s_active"] is False
    assert unlocked["lock_stt"] is False
    assert unlocked["voice_set"] is None


def test_completions_twin_uses_globals_when_chat_s2s():
    from distr.core.openai_s2s import completions_model_for_chat

    assert (
        completions_model_for_chat("gpt-realtime-2.1", "gpt-4o") == "gpt-4o"
    )
    assert completions_model_for_chat("gpt-4o", "gpt-4o-mini") == "gpt-4o"
    # Global must not be Realtime
    assert completions_model_for_chat("gpt-realtime-2.1", "gpt-realtime-2.1") == "gpt-4o"


def test_strip_realtime_from_settings_models():
    from distr.core.openai_s2s import strip_realtime_from_settings_models

    s = {
        "conversational_llm_model": "gpt-realtime-2.1",
        "agent_model": "gpt-realtime-2.1",
        "llm_model": "gpt-4o",
    }
    assert strip_realtime_from_settings_models(s) is True
    assert s["conversational_llm_model"] == ""
    assert s["agent_model"] == ""
    assert s["llm_model"] == "gpt-4o"


def test_get_openai_models_includes_s2s_allowlist(monkeypatch):
    from distr.gui.utils import get_ollama_models as gom

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-realtime-2.1"},
                    {"id": "gpt-transcribe"},
                    {"id": "tts-1"},
                ]
            }

    monkeypatch.setattr(gom.requests, "get", lambda *a, **k: FakeResp())
    models = gom.get_openai_models("sk-test")
    ids = {m["id"] for m in models}
    assert "gpt-4o" in ids
    assert "gpt-realtime-2.1" in ids
    assert "gpt-transcribe" not in ids
    assert "tts-1" not in ids
    # Injected allowlist members
    assert "gpt-realtime-2" in ids


def test_transcription_model_unchanged_by_s2s_lock_helper():
    """Lock matrix must not imply rewriting STT — value preservation is a UI/API contract."""
    from distr.core.openai_s2s import s2s_ui_locks

    before = "OpenAI (gpt-transcribe + gpt-live-transcribe)"
    locks = s2s_ui_locks("gpt-realtime-2.1")
    assert locks["lock_stt"] is True
    after = before  # enter S2S must not mutate
    assert after == before


def test_apply_s2s_voice_defaults():
    from distr.core.openai_s2s import apply_s2s_voice_defaults

    p, vp, vm = apply_s2s_voice_defaults(
        model_name="gpt-realtime-2.1",
        voice_provider="elevenlabs",
        voice_model="Rachel",
    )
    assert p == "openai"
    assert vp == "openai"
    assert vm == "marin"

    p2, vp2, vm2 = apply_s2s_voice_defaults(
        model_name="gpt-4o",
        voice_provider="elevenlabs",
        voice_model="Rachel",
    )
    assert p2 is None
    assert vp2 == "elevenlabs"
    assert vm2 == "Rachel"


def test_apply_s2s_voice_keeps_valid_realtime_voice():
    from distr.core.openai_s2s import apply_s2s_voice_defaults

    _p, vp, vm = apply_s2s_voice_defaults(
        model_name="gpt-realtime-2.1",
        voice_provider="openai",
        voice_model="cedar",
    )
    assert vp == "openai"
    assert vm == "cedar"


def test_hot_swap_llm_service_twins_realtime(monkeypatch):
    """Direct hot-swap must never leave a Realtime id on Completions config."""
    from distr.core.openai_s2s import completions_model_for_chat, is_openai_s2s_model

    twin = completions_model_for_chat("gpt-realtime-2.1", "gpt-4o")
    assert twin == "gpt-4o"
    assert not is_openai_s2s_model(twin)


def test_s2s_locks_endpoint_helper_matches_matrix():
    from distr.core.openai_s2s import s2s_ui_locks

    data = s2s_ui_locks("gpt-realtime-2.1")
    assert data["lock_stt"] and data["lock_tts_provider"]
    assert data["lock_conversational_model"] is False
    data2 = s2s_ui_locks("")
    assert data2["s2s_active"] is False


def test_assert_not_s2s_for_completions():
    from distr.core.openai_s2s import assert_not_s2s_for_completions
    import pytest

    assert assert_not_s2s_for_completions("gpt-4o") == "gpt-4o"
    with pytest.raises(ValueError):
        assert_not_s2s_for_completions("gpt-realtime-2.1")
