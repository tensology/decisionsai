"""Time-to-first-audio benchmark for the remote-app TTS path.

Compares the legacy path (full synthesis + ffmpeg Ogg + chunked send, where the
browser can only start after the last chunk) against the streaming path
(first PCM chunk leaves the desktop as soon as the provider produces it).

Usage: python scripts/bench_remote_tts.py [provider ...] [--runs N]
Never prints keys. Needs the provider's key/model in the DecisionsAI settings DB.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEXT = (
    "Both invoices are corrected and the reply drafts are waiting in Mailshot. "
    "Michelle's total is now nineteen thousand two hundred and twenty eight rand, "
    "and Ursula's is six hundred and fifty three rand forty. "
    "The rate fix is live on tensology.com, so the next run on the twenty fifth uses each client's agreed rate."
)


def _descriptor(provider_id: str):
    from distr.core.agent.services.tts.registry import tts_registry

    return tts_registry.get(provider_id)


def _voice(desc, provider_id: str) -> str:
    from distr.core.agent.services.tts.outbound_voice import resolve_outbound_voice_settings

    pid, voice, _ = resolve_outbound_voice_settings()
    if str(pid).lower().startswith(provider_id[:4]) and voice:
        return voice
    return desc.default_voice


def legacy(provider_id: str):
    """Text -> first byte the browser could play (it waits for the last chunk)."""
    import subprocess

    desc = _descriptor(provider_id)
    voice = _voice(desc, provider_id)
    out = Path(tempfile.mkdtemp()) / "bench.wav"
    t0 = time.perf_counter()
    desc.generate_audio(TEXT, voice, 1.0, str(out))
    t_synth = time.perf_counter() - t0
    ogg = out.with_suffix(".ogg")
    subprocess.run(["ffmpeg", "-y", "-i", str(out), "-c:a", "libopus", "-b:a", "64k", str(ogg)],
                   capture_output=True, timeout=60)
    t_conv = time.perf_counter() - t0 - t_synth
    return {"synth_s": t_synth, "ogg_s": t_conv, "first_audio_s": time.perf_counter() - t0,
            "bytes": ogg.stat().st_size if ogg.exists() else 0}


def streaming(provider_id: str):
    from distr.core.agent.services.tts.remote_stream import open_pcm_stream

    desc = _descriptor(provider_id)
    voice = _voice(desc, provider_id)
    t0 = time.perf_counter()
    stream = open_pcm_stream(provider_id, TEXT, voice, 1.0)
    first = None
    total = 0
    n = 0
    for chunk in stream.chunks:
        if first is None:
            first = time.perf_counter() - t0
        total += len(chunk)
        n += 1
    return {"first_audio_s": first, "total_s": time.perf_counter() - t0, "bytes": total,
            "chunks": n, "rate": stream.sample_rate, "mode": stream.mode,
            "audio_s": total / 2 / stream.sample_rate}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("providers", nargs="*", default=["fishaudio", "openai", "kokoro"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mode", choices=["both", "legacy", "streaming", "e2e"], default="both")
    args = ap.parse_args()
    if args.mode == "e2e":
        run_e2e(args.providers, args.runs)
        return
    for pid in args.providers:
        for label, fn in (("legacy", legacy), ("streaming", streaming)):
            if args.mode not in ("both", label):
                continue
            rows = []
            for _ in range(args.runs):
                try:
                    rows.append(fn(pid))
                except Exception as exc:  # noqa: BLE001
                    print(f"{pid:10s} {label:9s} FAILED: {type(exc).__name__}: {str(exc)[:120]}")
                    break
            if rows:
                firsts = [r["first_audio_s"] for r in rows]
                print(f"{pid:10s} {label:9s} first-audio median={statistics.median(firsts):.2f}s "
                      f"min={min(firsts):.2f}s max={max(firsts):.2f}s  detail={ {k: (round(v, 2) if isinstance(v, float) else v) for k, v in rows[-1].items()} }")



def e2e(provider_id: str, live: bool):
    """deliver_remote_tts with a recording manager: seconds until the first audio chunk is sent."""
    import tempfile
    from unittest.mock import MagicMock

    from distr.core.agent.constants import normalize_voice_provider  # noqa: F401
    from distr.core.agent.services.tts.remote_stream import open_pcm_stream
    from distr.core.integrations.telegram.remote_tts_delivery import build_synthetic_remote_context, deliver_remote_tts

    desc = _descriptor(provider_id)
    voice = _voice(desc, provider_id)

    class M:
        def __init__(self):
            self.t0 = time.perf_counter(); self.first = None; self.last = None; self.n = 0
            self._cancelled_remote_audio_requests = set()
        def is_connected(self): return True
        def _press_key(self, *a, **k): return True
        def _send_websocket_message(self, msg):
            if msg["type"] == "remote_agent_audio_chunk":
                self.n += 1
                self.last = time.perf_counter() - self.t0
                if self.first is None:
                    self.first = self.last
            return True

    def generate_tts(text):
        import subprocess
        out = Path(tempfile.mkdtemp()) / "b.wav"
        desc.generate_audio(text, voice, 1.0, str(out))
        return out

    def to_ogg(p):
        import subprocess
        ogg = p.with_suffix(".ogg")
        subprocess.run(["ffmpeg", "-y", "-i", str(p), "-c:a", "libopus", "-b:a", "64k", str(ogg)], capture_output=True, timeout=60)
        return ogg

    m = M()
    deliver_remote_tts(
        m, TEXT, build_synthetic_remote_context({"mode": "command", "request_id": "bench"}),
        generate_tts=generate_tts, convert_wav_to_ogg=to_ogg, cleanup_files=MagicMock(),
        open_pcm_stream=(lambda t: open_pcm_stream(provider_id, t, voice, 1.0)) if live else None,
    )
    return {"first_audio_s": m.first, "last_chunk_s": m.last, "chunks": m.n}


def run_e2e(providers, runs):
    for pid in providers:
        for label, live in (("file", False), ("live", True)):
            rows = []
            for _ in range(runs):
                try:
                    rows.append(e2e(pid, live))
                except Exception as exc:  # noqa: BLE001
                    print(f"{pid} {label} FAILED {exc}")
                    break
            if rows:
                f = [r["first_audio_s"] for r in rows]
                print(f"E2E {pid:10s} {label:5s} first chunk sent median={statistics.median(f):.2f}s "
                      f"min={min(f):.2f}s max={max(f):.2f}s last_chunk={rows[-1]['last_chunk_s']:.2f}s chunks={rows[-1]['chunks']}")


if __name__ == "__main__":
    main()
