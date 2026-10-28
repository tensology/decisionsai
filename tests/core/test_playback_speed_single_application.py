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
