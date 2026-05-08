"""Unit tests for STT label → engine resolution (config_loader)."""

import pytest

from distr.core.agent.config_loader import resolve_stt_config


@pytest.mark.parametrize(
    "label,expected_engine",
    [
        ("VibeVoice ASR (Local)", "vibevoice_asr"),
        ("vibevoice asr (local)", "vibevoice_asr"),
        ("VibeVoice ASR", "vibevoice_asr"),
        ("Whisper.cpp (Local & Offline)", "whisper"),
        ("Vosk (Local & Offline)", "vosk"),
        ("AssemblyAI (universal-2)", "assemblyai"),
        ("OpenAI Whisper (whisper-1)", "openai_whisper"),
    ],
)
def test_resolve_stt_config_known_labels(label: str, expected_engine: str) -> None:
    cfg = resolve_stt_config(label)
    assert cfg.get("engine") == expected_engine


def test_resolve_stt_config_unknown_returns_none_engine() -> None:
    cfg = resolve_stt_config("Some Unknown Transcription Label")
    assert cfg.get("engine") is None
