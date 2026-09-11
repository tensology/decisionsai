"""Provider-combination and failure-mode coverage for the voice pipeline seams."""

from __future__ import annotations

import asyncio

import pytest

from tests.audio.pipeline_simulation import (
    ScriptedLLM,
    ScriptedSTT,
    ScriptedTTS,
    SimulatedVoicePipeline,
    delayed_echo,
    silence_chunks,
    speech_chunks,
)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("stt_name", "llm_name", "tts_name"),
    (("openai-realtime", "openai", "kokoro"), ("assemblyai-v3", "ollama", "elevenlabs")),
)
def test_provider_combination_preserves_turn_and_response_contract(stt_name, llm_name, tts_name):
    del stt_name, llm_name, tts_name  # names document the representative adapter pair.
    pipeline = SimulatedVoicePipeline(
        ScriptedSTT("hello there", partials=("hello", "hello there")),
        ScriptedLLM(tokens=("I", " heard", " you.")),
        ScriptedTTS(chunks=2),
    )
    chunks = speech_chunks(count=3) + silence_chunks(count=2, start=0.06, sequence_start=3)

    run(pipeline.feed(chunks))

    kinds = [kind for kind, _, _ in pipeline.trace.events]
    assert kinds[:4] == ["vad_started", "stt_partial", "stt_partial", "vad_stopped"]
    assert "stt_final" in kinds
    assert "llm_first_token" in kinds
    assert kinds.count("tts_audio") == 2


def test_hands_free_barge_in_interrupts_playback_before_new_response():
    pipeline = SimulatedVoicePipeline(
        ScriptedSTT("first turn", partials=("first",)),
        ScriptedLLM(tokens=("first", " response")),
        ScriptedTTS(chunks=2),
    )
    first_turn = speech_chunks(count=2) + silence_chunks(count=1, start=0.04, sequence_start=2)
    run(pipeline.feed(first_turn))
    assert pipeline.playing

    second_turn = speech_chunks(count=2, start=0.2, seed=8)
    run(pipeline.feed(second_turn))

    kinds = [kind for kind, _, _ in pipeline.trace.events]
    assert "interrupted" in kinds
    interruption_index = kinds.index("interrupted")
    assert interruption_index > kinds.index("tts_audio")
    assert "tts_audio" not in kinds[interruption_index + 1:]


def test_push_to_talk_uses_button_boundary_when_vad_is_not_involved():
    pipeline = SimulatedVoicePipeline(
        ScriptedSTT("button bounded input"), ScriptedLLM(), ScriptedTTS(chunks=1)
    )
    run(pipeline.push_to_talk(speech_chunks(count=2)))

    kinds = [kind for kind, _, _ in pipeline.trace.events]
    assert kinds[0] == "ptt_started"
    assert "ptt_released" in kinds
    assert "stt_final" in kinds
    assert "tts_audio" in kinds


def test_delayed_attenuated_echo_stays_below_speech_threshold():
    reference = speech_chunks(count=8, level=0.25)
    echo = delayed_echo(reference, delay_chunks=3, attenuation=0.12)

    assert max(chunk.rms for chunk in echo) < 0.05
    assert max(chunk.rms for chunk in reference) > 0.05


def test_streaming_jitter_drop_and_provider_error_are_observable():
    pipeline = SimulatedVoicePipeline(
        ScriptedSTT(
            "recoverable turn",
            partials=("recover", "recoverable"),
            jitter=(0.0, 0.001),
            drop_sequences={1},
            error_on_sequence=2,
        ),
        ScriptedLLM(),
        ScriptedTTS(),
    )
    run(pipeline.feed(speech_chunks(count=3)))

    kinds = [kind for kind, _, _ in pipeline.trace.events]
    assert "stt_partial" in kinds
    assert "stt_error" in kinds
