"""Deterministic provider-agnostic voice pipeline simulation fixtures.

These fixtures model the contracts at the seams between capture/VAD, STT,
LLM, TTS, and playback. They intentionally do not claim to model acoustic
echo cancellation or a real provider's internal timing.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class AudioChunk:
    samples: tuple[float, ...]
    at: float
    sequence: int

    @property
    def rms(self) -> float:
        if not self.samples:
            return 0.0
        return math.sqrt(sum(sample * sample for sample in self.samples) / len(self.samples))


@dataclass(frozen=True)
class STTEvent:
    kind: str
    text: str = ""
    at: float = 0.0


@dataclass(frozen=True)
class TTSChunk:
    text: str
    samples: tuple[float, ...]
    at: float
    sequence: int


class STTAdapter(Protocol):
    async def start_turn(self, at: float) -> AsyncIterator[STTEvent]: ...
    async def push_audio(self, chunk: AudioChunk) -> AsyncIterator[STTEvent]: ...
    async def end_turn(self, at: float) -> AsyncIterator[STTEvent]: ...


class LLMAdapter(Protocol):
    async def stream_response(self, text: str, at: float) -> AsyncIterator[str]: ...


class TTSAdapter(Protocol):
    async def stream_sentence(self, text: str, at: float) -> AsyncIterator[TTSChunk]: ...


@dataclass
class SimulationTrace:
    events: list[tuple[str, float, str]] = field(default_factory=list)

    def record(self, kind: str, at: float, detail: str = "") -> None:
        self.events.append((kind, at, detail))

    def first(self, kind: str) -> tuple[str, float, str]:
        return next(event for event in self.events if event[0] == kind)


class ScriptedSTT:
    """Streaming STT adapter with optional jitter, drops, and provider errors."""

    def __init__(self, text: str, *, partials: tuple[str, ...] = ("hello",), jitter: tuple[float, ...] = (), drop_sequences: set[int] | None = None, error_on_sequence: int | None = None):
        self.text = text
        self.partials = partials
        self.jitter = jitter
        self.drop_sequences = drop_sequences or set()
        self.error_on_sequence = error_on_sequence
        self._started = False

    async def start_turn(self, at: float) -> AsyncIterator[STTEvent]:
        self._started = True
        if False:
            yield STTEvent("noop")

    async def push_audio(self, chunk: AudioChunk) -> AsyncIterator[STTEvent]:
        if not self._started:
            raise RuntimeError("audio received before STT turn start")
        if self.error_on_sequence == chunk.sequence:
            raise RuntimeError("scripted STT provider error")
        if chunk.sequence in self.drop_sequences:
            return
        if chunk.sequence < len(self.partials):
            delay = self.jitter[chunk.sequence] if chunk.sequence < len(self.jitter) else 0.0
            if delay:
                await asyncio.sleep(delay)
            yield STTEvent("partial", self.partials[chunk.sequence], chunk.at + delay)

    async def end_turn(self, at: float) -> AsyncIterator[STTEvent]:
        yield STTEvent("final", self.text, at)
        self._started = False


class ScriptedLLM:
    def __init__(self, tokens: tuple[str, ...] = ("Acknowledged", "."), *, delay: float = 0.0):
        self.tokens = tokens
        self.delay = delay

    async def stream_response(self, text: str, at: float) -> AsyncIterator[str]:
        for token in self.tokens:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield token


class ScriptedTTS:
    def __init__(self, *, chunks: int = 3, delay: float = 0.0):
        self.chunks = chunks
        self.delay = delay

    async def stream_sentence(self, text: str, at: float) -> AsyncIterator[TTSChunk]:
        for sequence in range(self.chunks):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TTSChunk(text, (0.08, -0.08) * 8, at + self.delay, sequence)


def speech_chunks(*, count: int = 12, level: float = 0.2, start: float = 0.0, step: float = 0.02, seed: int = 7) -> list[AudioChunk]:
    rng = random.Random(seed)
    return [
        AudioChunk(tuple(level + rng.uniform(-level * 0.05, level * 0.05) for _ in range(32)), start + index * step, index)
        for index in range(count)
    ]


def silence_chunks(*, count: int, start: float, step: float = 0.02, sequence_start: int = 0) -> list[AudioChunk]:
    return [AudioChunk((0.0,) * 32, start + index * step, sequence_start + index) for index in range(count)]


def delayed_echo(reference: list[AudioChunk], *, delay_chunks: int, attenuation: float) -> list[AudioChunk]:
    result: list[AudioChunk] = []
    for index, chunk in enumerate(reference):
        source = reference[index - delay_chunks] if index >= delay_chunks else None
        level = source.samples[0] * attenuation if source else 0.0
        result.append(AudioChunk((level,) * len(chunk.samples), chunk.at, chunk.sequence))
    return result


class SimulatedVoicePipeline:
    """Small stateful harness for capture, turn detection, and response flow."""

    def __init__(self, stt: STTAdapter, llm: LLMAdapter, tts: TTSAdapter, *, vad_threshold: float = 0.05, trace: SimulationTrace | None = None):
        self.stt, self.llm, self.tts = stt, llm, tts
        self.vad_threshold = vad_threshold
        self.trace = trace or SimulationTrace()
        self.now = 0.0
        self.speaking = False
        self.playing = False
        self.interrupted = False

    async def feed(self, chunks: list[AudioChunk]) -> None:
        for chunk in chunks:
            self.now = max(self.now, chunk.at)
            is_speech = chunk.rms >= self.vad_threshold
            if is_speech and not self.speaking:
                self.speaking = True
                self.trace.record("vad_started", self.now)
                async for _ in self.stt.start_turn(self.now):
                    pass
                if self.playing:
                    self.interrupted = True
                    self.playing = False
                    self.trace.record("interrupted", self.now)
            elif not is_speech and self.speaking:
                self.speaking = False
                self.trace.record("vad_stopped", self.now)
                async for event in self.stt.end_turn(self.now):
                    await self._handle_stt(event)
                continue
            if self.speaking:
                try:
                    async for event in self.stt.push_audio(chunk):
                        await self._handle_stt(event)
                except RuntimeError as error:
                    self.trace.record("stt_error", self.now, str(error))

    async def push_to_talk(self, chunks: list[AudioChunk]) -> None:
        """Feed a button-bounded utterance without relying on VAD boundaries."""
        if not chunks:
            return
        self.now = chunks[0].at
        self.trace.record("ptt_started", self.now)
        async for _ in self.stt.start_turn(self.now):
            pass
        for chunk in chunks:
            self.now = max(self.now, chunk.at)
            try:
                async for event in self.stt.push_audio(chunk):
                    await self._handle_stt(event)
            except RuntimeError as error:
                self.trace.record("stt_error", self.now, str(error))
        self.trace.record("ptt_released", self.now)
        async for event in self.stt.end_turn(self.now):
            await self._handle_stt(event)

    async def _handle_stt(self, event: STTEvent) -> None:
        self.trace.record("stt_" + event.kind, event.at, event.text)
        if event.kind != "final":
            return
        self.trace.record("llm_started", event.at, event.text)
        response = ""
        async for token in self.llm.stream_response(event.text, event.at):
            if self.interrupted:
                return
            if not response:
                self.trace.record("llm_first_token", event.at, token)
            response += token
        async for audio in self.tts.stream_sentence(response, event.at):
            if self.interrupted:
                return
            self.playing = True
            self.trace.record("tts_audio", audio.at, str(audio.sequence))
