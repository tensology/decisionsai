"""Custom voice personality must apply for all TTS providers, not only Kokoro."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distr.core.agent.command_handler import _update_agent_identity_from_chat_voice


def test_update_agent_identity_loads_pixazo_custom_personality():
    session = SimpleNamespace(
        agent_name="VoxCPM 2",
        settings={},
        logger=MagicMock(),
        _custom_voice_personality="",
    )
    session._load_custom_voice_personality = MagicMock(
        side_effect=lambda _p, _v: setattr(
            session, "_custom_voice_personality", "Speak like a witty surfer."
        )
    )
    session._load_agent_role = MagicMock(return_value="DEFAULT\n\nSpeak like a witty surfer.")
    session.llm_service = MagicMock()

    with patch(
        "distr.core.agent.service_factory.resolve_voice_to_display_name",
        return_value="Hayley",
    ), patch(
        "distr.core.agent.service_factory.update_agent_name_on_llm",
    ) as mock_update:
        _update_agent_identity_from_chat_voice(session, "pixazo", "custom_11")

    session._load_custom_voice_personality.assert_called_once_with("pixazo", "custom_11")
    assert session.agent_name == "Hayley"
    mock_update.assert_called_once_with(session.llm_service, "Hayley", session.role)
