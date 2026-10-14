from distr.core.chat import resolve_usable_voice_selection, voice_provider_is_usable


def _settings(**overrides):
    settings = {
        "voice_provider": "fishaudio",
        "tts_provider": "Fish Audio (Online)",
        "fishaudio_enabled": True,
        "fishaudio_key": "fish-secret",
        "fishaudio_voice": "fish-voice-id",
        "elevenlabs_enabled": False,
        "elevenlabs_key": "",
        "elevenlabs_voice": "old-eleven-voice",
        "kokoro_voice": "af_heart",
    }
    settings.update(overrides)
    return settings


def test_disabled_chat_voice_falls_back_to_configured_fish_audio():
    provider, model = resolve_usable_voice_selection(
        "elevenlabs", "old-eleven-voice", _settings()
    )

    assert provider == "fishaudio"
    assert model == "fish-voice-id"


def test_enabled_fish_audio_chat_voice_is_preserved():
    provider, model = resolve_usable_voice_selection(
        "fishaudio", "selected-fish-voice", _settings()
    )

    assert provider == "fishaudio"
    assert model == "selected-fish-voice"


def test_online_provider_requires_enabled_flag_and_secret():
    assert voice_provider_is_usable("fishaudio", _settings())
    assert not voice_provider_is_usable(
        "fishaudio", _settings(fishaudio_enabled=False)
    )
    assert not voice_provider_is_usable("fishaudio", _settings(fishaudio_key=""))
