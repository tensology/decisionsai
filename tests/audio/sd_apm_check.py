#!/usr/bin/env python3
"""sounddevice + LiveKit APM echo-cancel probe (no app, no MediaDevices).

Same coherent timing as `rtc.MediaDevices` (capture and render in device
callbacks through ONE `rtc.AudioProcessingModule`), but without the track /
player layer that SIGABRTs in the app. If this cancels echo here, we wire it
into the transport.

Run:
    workon decisions
    python tests/audio/sd_apm_check.py           # AEC on
    python tests/audio/sd_apm_check.py --no-aec  # baseline
"""

from __future__ import annotations

import argparse
import math
import threading
import time

import numpy as np
import sounddevice as sd
from livekit import rtc

SR = 48000
FRAME = 480  # 10 ms
BLOCK = 4800  # 100 ms device buffer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-aec", action="store_true")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--in-device", type=int, default=None)
    ap.add_argument("--out-device", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("default (in,out):", sd.default.device)
        for i, d in enumerate(sd.query_devices()):
            print(f"[{i}] in={d.get('max_input_channels')} out={d.get('max_output_channels')} name={d.get('name')}")
        return

    apm = rtc.AudioProcessingModule(
        echo_cancellation=not args.no_aec,
        noise_suppression=True,
        high_pass_filter=True,
        auto_gain_control=False,
    )

    state = {"play": False}
    rows: list[tuple[bool, float]] = []
    lock = threading.Lock()
    out_delay = [0.0]
    tone_phase = [0]

    def process_near(arr: np.ndarray) -> np.ndarray:
        x = arr[:, 0].copy()
        n = len(x)
        for i in range(0, n - FRAME + 1, FRAME):
            chunk = x[i:i + FRAME]
            f = rtc.AudioFrame(
                data=chunk.tobytes(), sample_rate=SR, num_channels=1, samples_per_channel=FRAME
            )
            apm.process_stream(f)
            x[i:i + FRAME] = np.frombuffer(f.data, dtype=np.int16)
        return x

    def in_cb(indata, frames, time_info, status):
        try:
            d = (time_info.currentTime - time_info.inputBufferAdcTime + out_delay[0]) * 1000.0
            apm.set_stream_delay_ms(int(max(d, 0.0)))
        except Exception:
            pass
        cleaned = process_near(indata)
        rms = float(np.sqrt(np.mean((cleaned.astype(np.float32) / 32768.0) ** 2)))
        with lock:
            rows.append((state["play"], rms))

    def out_cb(outdata, frames, time_info, status):
        n = frames
        idx = np.arange(tone_phase[0], tone_phase[0] + n)
        tone_phase[0] = (tone_phase[0] + n) % SR
        if state["play"]:
            s = 0.3 * np.sin(2 * math.pi * 220.0 * idx / SR)
        else:
            s = np.zeros(n)
        pcm = np.clip(s * 32767.0, -32768, 32767).astype(np.int16)
        outdata[:, 0] = pcm
        try:
            out_delay[0] = float(time_info.outputBufferDacTime - time_info.currentTime)
        except Exception:
            pass
        for i in range(0, n - FRAME + 1, FRAME):
            chunk = pcm[i:i + FRAME]
            f = rtc.AudioFrame(
                data=chunk.tobytes(), sample_rate=SR, num_channels=1, samples_per_channel=FRAME
            )
            try:
                apm.process_reverse_stream(f)
            except Exception:
                pass

    print(f"AEC {'OFF' if args.no_aec else 'ON'} @ {SR} Hz — 1s silence, {args.seconds:.0f}s tone, 1s silence")
    with sd.InputStream(callback=in_cb, channels=1, samplerate=SR, dtype="int16", blocksize=BLOCK, device=args.in_device):
        with sd.OutputStream(callback=out_cb, channels=1, samplerate=SR, dtype="int16", blocksize=BLOCK, device=args.out_device):
            time.sleep(1.0)
            state["play"] = True
            time.sleep(args.seconds)
            state["play"] = False
            time.sleep(1.0)

    play = [r for p, r in rows if p]
    sil = [r for p, r in rows if not p]
    print(f"  during tone playback : median={np.median(play):.4f} p90={np.percentile(play,90):.4f} max={max(play):.4f} n={len(play)}")
    print(f"  silence (baseline)   : median={np.median(sil):.4f} max={max(sil):.4f} n={len(sil)}")


if __name__ == "__main__":
    main()
