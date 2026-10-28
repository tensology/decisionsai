"""Playback speed must be applied exactly once.

Fish Audio synthesises at the requested speed (speed= goes out with the request)
and the transport ALSO time-stretched the audio, so a 1.25x setting came out
nearer 1.38x and carried stretch artefacts on top of a clean resynthesis.
"""

from __future__ import annotations

from types import SimpleNamespace

from distr.core.agent.command_handler import _cmd_set_playback_speed
from distr.core.agent.services.tts.fishaudio import FishAudioTTSService


class _Output:
    def __init__(self) -> None:
        self.speed = None

    def set_speed(self, speed: float) -> None:
        self.speed = speed


class _Service:
    def __init__(self, applies_own_speed: bool) -> None:
        self.applies_own_playback_speed = applies_own_speed
        self.speed = None

    def set_playback_speed(self, speed: float) -> None:
        self.speed = speed


def _session(service) -> SimpleNamespace:
    output = _Output()
    return SimpleNamespace(
        tts_service=service,
        transport=SimpleNamespace(output=lambda: output),
        logger=SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None),
        settings={},
        _output=output,
    )


def test_fish_audio_declares_it_applies_its_own_speed():
    assert FishAudioTTSService.applies_own_playback_speed is True


def test_a_provider_that_synthesises_at_speed_is_not_stretched_again():
    service = _Service(applies_own_speed=True)
    session = _session(service)

    _cmd_set_playback_speed(session, {"speed": 1.25})

    assert service.speed == 1.25
    # The provider already produced 1.25x audio; the transport must stay neutral.
    assert session._output.speed == 1.0


def test_a_provider_without_its_own_speed_still_uses_the_transport():
    service = _Service(applies_own_speed=False)
    session = _session(service)

    _cmd_set_playback_speed(session, {"speed": 1.25})

    assert service.speed == 1.25
    assert session._output.speed == 1.25


# --- Provider matrix -------------------------------------------------------
# Every TTS provider must be consistent about WHO applies playback speed, and
# whether it can play pre-synthesised audio. These pin the audit so a new
# provider cannot silently double-apply speed or lose the prefetch win.

SYNTHESISES_AT_SPEED = [
    "FishAudioTTSService",
    "CoquiTTSService",
    "KokoroTTSService",
    "SupertonicTTSService",
]
NEUTRAL_SYNTHESIS = ["OpenAITTSService", "ElevenLabsTTSService", "PixazoTTSService"]


def _tts_classes():
    from distr.core.agent.services.tts import (
        ElevenLabsTTSService,
        OpenAITTSService,
        SupertonicTTSService,
    )
    from distr.core.agent.services.tts.coqui import CoquiTTSService
    from distr.core.agent.services.tts.kokoro import KokoroTTSService
    from distr.core.agent.services.tts.pixazo import PixazoTTSService

    return {
        "FishAudioTTSService": FishAudioTTSService,
        "CoquiTTSService": CoquiTTSService,
        "KokoroTTSService": KokoroTTSService,
        "SupertonicTTSService": SupertonicTTSService,
        "OpenAITTSService": OpenAITTSService,
        "ElevenLabsTTSService": ElevenLabsTTSService,
        "PixazoTTSService": PixazoTTSService,
    }


def test_providers_that_synthesise_at_speed_declare_it():
    classes = _tts_classes()
    missing = [n for n in SYNTHESISES_AT_SPEED if not getattr(classes[n], "applies_own_playback_speed", False)]
    assert missing == [], f"these would have speed applied twice: {missing}"


def test_providers_with_neutral_synthesis_leave_speed_to_the_transport():
    classes = _tts_classes()
    wrong = [n for n in NEUTRAL_SYNTHESIS if getattr(classes[n], "applies_own_playback_speed", False)]
    assert wrong == [], f"these would lose playback speed entirely: {wrong}"


def test_prefetch_covers_every_streaming_provider():
    classes = _tts_classes()
    for name in SYNTHESISES_AT_SPEED + NEUTRAL_SYNTHESIS:
        if name == "KokoroTTSService":
            continue  # local provider with its own playback path
        cls = classes[name]
        assert cls._can_prefetch_synthesis(cls.__new__(cls)) is True, name


def test_kokoro_keeps_its_own_playback_path():
    from distr.core.agent.services.tts.kokoro import KokoroTTSService

    assert KokoroTTSService._can_prefetch_synthesis(KokoroTTSService.__new__(KokoroTTSService)) is False
