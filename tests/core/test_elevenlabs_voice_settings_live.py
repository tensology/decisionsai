"""ElevenLabs voice_settings live update path."""

from unittest.mock import MagicMock

from distr.core.agent.command_handler import _cmd_set_elevenlabs_voice_settings
from distr.core.agent.services.tts.elevenlabs import ElevenLabsTTSService


def test_set_elevenlabs_voice_settings_updates_runtime_service():
    service = object.__new__(ElevenLabsTTSService)
    service._stability = 0.5
    service._similarity_boost = 0.6
    service._style = 0.25
    service._use_speaker_boost = True

    session = MagicMock()
    session.tts_service = service
    session.settings = {
        "elevenlabs_stability": 0.5,
        "elevenlabs_similarity_boost": 0.6,
        "elevenlabs_style": 0.25,
        "elevenlabs_use_speaker_boost": True,
    }
    session.logger = MagicMock()

    _cmd_set_elevenlabs_voice_settings(session, {
        "stability": 0.2,
        "similarity_boost": 0.9,
        "style": 0.1,
        "use_speaker_boost": False,
    })

    assert service._stability == 0.2
    assert service._similarity_boost == 0.9
    assert service._style == 0.1
    assert service._use_speaker_boost is False
    assert session.settings["elevenlabs_stability"] == 0.2
    assert session.settings["elevenlabs_similarity_boost"] == 0.9
    assert session.settings["elevenlabs_style"] == 0.1
    assert session.settings["elevenlabs_use_speaker_boost"] is False


def test_elevenlabs_service_set_voice_settings_updates_runtime_fields():
    service = object.__new__(ElevenLabsTTSService)
    service._stability = 0.5
    service._similarity_boost = 0.6
    service._style = 0.25
    service._use_speaker_boost = True

    service.set_elevenlabs_voice_settings(
        stability=0.15,
        similarity_boost=0.85,
        style=0.05,
        use_speaker_boost=False,
    )

    assert service._stability == 0.15
    assert service._similarity_boost == 0.85
    assert service._style == 0.05
    assert service._use_speaker_boost is False
