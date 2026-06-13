from types import SimpleNamespace
from unittest.mock import patch

from distr.core.agent.tools.system.system_info import SystemInfoTool


class _ChatManager:
    def __init__(self, chat_id=42, provider="openai", model="gpt-4.1", voice_provider="kokoro", voice_model="af_heart"):
        self._chat_id = chat_id
        self.current_provider = provider
        self.current_model = model
        self.current_voice_provider = voice_provider
        self.current_voice_model = voice_model

    def get_current_chat(self):
        return self._chat_id


def test_system_info_reports_runtime_llm_and_voice():
    tool = SystemInfoTool(chat_manager=_ChatManager())
    fake_settings = {
        "conversational_llm_provider": "ollama",
        "conversational_llm_model": "llama3",
        "voice_provider": "elevenlabs",
        "kokoro_voice": "bf_emma",
        "hands_free_mode": True,
        "last_listening_state": True,
    }

    with patch("distr.core.settings.load_settings_from_db", return_value=fake_settings):
        with patch.object(
            SystemInfoTool,
            "_get_voice_display_name",
            return_value="Heart",
        ):
            spoken, _, reference = tool._get_system_info().partition("\n\nREFERENCE:\n")

    assert "gpt-4.1" in spoken or "gpt 4.1" in spoken.lower()
    assert "Heart" in spoken
    assert "conversational_provider" not in spoken.lower()
    assert "openai" in reference.lower() or "OpenAI" in reference
    assert "Heart" in reference


def test_system_info_falls_back_to_chat_voice_when_runtime_voice_missing():
    manager = _ChatManager(voice_provider=None, voice_model=None)
    manager.current_voice_provider = None
    manager.current_voice_model = None
    tool = SystemInfoTool(chat_manager=manager)

    fake_chat = SimpleNamespace(voice_provider="kokoro", voice_model="am_adam")
    fake_settings = {
        "conversational_llm_provider": "ollama",
        "conversational_llm_model": "llama3",
        "voice_provider": "kokoro",
        "kokoro_voice": "af_heart",
        "hands_free_mode": True,
        "last_listening_state": True,
    }

    with patch("distr.core.settings.load_settings_from_db", return_value=fake_settings):
        with patch("distr.core.db.get_session") as mock_session:
            mock_session.return_value.__enter__.return_value.get.return_value = fake_chat
            with patch.object(SystemInfoTool, "_get_voice_display_name", return_value="Adam"):
                spoken = tool._get_system_info().split("\n\nREFERENCE:\n", 1)[0]

    assert "Adam" in spoken
