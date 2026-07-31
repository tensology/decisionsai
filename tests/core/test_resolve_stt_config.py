"""Unit tests for STT label → engine resolution (config_loader)."""

import pytest

from distr.core.agent.config_loader import resolve_stt_config


@pytest.mark.parametrize(
    "label,expected_engine,expected_model",
    [
        ("Whisper.cpp (Local & Offline)", "whisper", None),
        ("Vosk (Local & Offline)", "vosk", None),
        ("AssemblyAI (universal-3-5-pro)", "assemblyai", "universal-3-5-pro"),
        ("AssemblyAI (universal-2)", "assemblyai", "universal-2"),
        ("OpenAI (gpt-transcribe + gpt-live-transcribe)", "openai_whisper", "gpt-transcribe"),
        ("OpenAI Whisper (whisper-1)", "openai_whisper", "whisper-1"),
    ],
)
def test_resolve_stt_config_known_labels(label: str, expected_engine: str, expected_model: str | None) -> None:
    cfg = resolve_stt_config(label)
    assert cfg.get("engine") == expected_engine
    if expected_model is not None:
        assert cfg.get("model") == expected_model


def test_resolve_stt_config_unknown_returns_none_engine() -> None:
    cfg = resolve_stt_config("Some Unknown Transcription Label")
    assert cfg.get("engine") is None
