from distr.core.agent.services.tts.outbound_voice import (
    is_voice_delivery_provider,
    resolve_outbound_voice_settings,
    voice_delivery_provider_for_event,
)


def test_is_voice_delivery_provider_accepts_registered_providers():
    assert is_voice_delivery_provider("kokoro")
    assert is_voice_delivery_provider("elevenlabs")
    assert is_voice_delivery_provider("ElevenLabs (Online)")
    assert is_voice_delivery_provider("openai")
    assert is_voice_delivery_provider("tool")
    assert not is_voice_delivery_provider("discord")


def test_voice_delivery_provider_for_event_uses_resolver(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.services.tts.outbound_voice.resolve_outbound_voice_settings",
        lambda settings=None: ("elevenlabs", "voice123", "ElevenLabs (Online)"),
    )
    assert voice_delivery_provider_for_event() == "elevenlabs"
