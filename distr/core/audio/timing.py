"""Low-overhead timing seam for voice pipeline diagnostics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AudioTiming:
    """Monotonic stage timestamps for one voice turn.

    The class is deliberately provider-agnostic. Consumers can attach the
    same instance to capture, STT, LLM, TTS, and transport adapters without
    changing their public frame contracts.
    """

    stages: dict[str, float] = field(default_factory=dict)

    def mark(self, stage: str, *, at: float | None = None) -> float:
        value = time.monotonic() if at is None else float(at)
        self.stages[stage] = value
        return value

    def elapsed(self, start: str, end: str) -> float | None:
        if start not in self.stages or end not in self.stages:
            return None
        return max(0.0, self.stages[end] - self.stages[start])

    def snapshot(self) -> dict[str, float | None]:
        return {
            "capture_to_vad": self.elapsed("capture", "vad_started"),
            "vad_to_commit": self.elapsed("vad_stopped", "stt_commit"),
            "commit_to_transcript": self.elapsed("stt_commit", "stt_final"),
            "transcript_to_llm": self.elapsed("stt_final", "llm_first_token"),
            "llm_to_tts": self.elapsed("llm_first_token", "tts_first_audio"),
            "capture_to_first_audio": self.elapsed("capture", "tts_first_audio"),
            "interruption_ack": self.elapsed("interruption_requested", "interruption_acknowledged"),
        }

