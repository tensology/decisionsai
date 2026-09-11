"""Startup resilience for optional online TTS providers."""

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_elevenlabs_startup_failure_falls_back_to_kokoro(monkeypatch):
    from distr.core.agent import service_factory
    from distr.core.agent.session import AgentSession

    calls = []

    def create_tts(config, **kwargs):
        calls.append(config.copy())
        if config["engine"] == "elevenlabs":
            raise OSError("DNS lookup failed")
        return SimpleNamespace(provider="kokoro")

    monkeypatch.setattr(service_factory, "create_tts_service", create_tts)
    session = object.__new__(AgentSession)
    session.config = {
        "tts": {"engine": "elevenlabs", "voice_id": "voice-id"},
    }
    session.settings = {"kokoro_voice": "af_heart"}
    session.stt_service = MagicMock()
    session.is_hands_free = True
    session.event_queue = MagicMock()
    session.logger = MagicMock()

    result = AgentSession._create_tts_service_only(session)

    assert result.provider == "kokoro"
    assert [call["engine"] for call in calls] == ["elevenlabs", "kokoro"]
    assert calls[-1]["voice_name"] == "af_heart"
    assert session.config["tts"]["engine"] == "elevenlabs"
    session.logger.warning.assert_called_once()


def test_current_chat_voice_setting_wins_over_legacy_alias(monkeypatch):
    from distr.core.agent import service_factory
    from distr.core.agent.session import AgentSession

    captured = {}

    def create_llm(_config, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(service_factory, "create_llm_service", create_llm)
    session = object.__new__(AgentSession)
    session.config = {"llm": {"engine": "ollama", "model_name": "test"}}
    session.settings = {"chat_voice_enabled": True, "voice_enabled": False}
    session.role = "assistant"
    session.agent_name = "Heart"
    session.event_queue = MagicMock()
    session.is_listening = True
    session.chat_manager = MagicMock()
    session.command_queue = MagicMock()
    session.confirmation_results_dict = {}
    session.is_hands_free = False
    session.tts_service = MagicMock()

    AgentSession._create_llm_service_only(session)

    assert captured["voice_enabled"] is True
