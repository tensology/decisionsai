#!/usr/bin/env python3
"""LiveKit WebRTC APM echo-cancellation probe (no app, no server).

Runs capture + playback through ONE `rtc.AudioProcessingModule`: the output
player feeds the real rendered audio back as the AEC reverse stream, with the
delay measured from PortAudio timestamps (no guessed delay, no ring buffer).

This is the engine that could replace the app's custom WebRTC AEC. It prints
mean mic RMS while a known signal plays, so echo cancellation is measurable.

Run:
    workon decisions
    python tests/audio/livekit_apm_check.py --output 2          # AEC on
    python tests/audio/livekit_apm_check.py --output 2 --no-aec # baseline
    python tests/audio/livekit_apm_check.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import numpy as np

try:
    from livekit import rtc
except ImportError:
    print("livekit not installed. Run: pip install livekit")
    sys.exit(1)

SR = 48000
FRAME_SAMPLES = 480  # 10 ms at 48 kHz (APM requires 10 ms frames)


def _rms(frame: rtc.AudioFrame) -> float:
    data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(data * data)))


def _list_devices() -> None:
    devices = rtc.MediaDevices()
    print("Input devices:")
    for d in devices.list_input_devices():
        print(f"  [{d['index']}] {d.get('name')}")
    print("Output devices:")
    for d in devices.list_output_devices():
        print(f"  [{d['index']}] {d.get('name')}")


class _Stats:
    def __init__(self) -> None:
        self.play: list[float] = []
        self.idle: list[float] = []
        self.playing = False


async def _play_noise(source: rtc.AudioSource, seconds: float) -> None:
    """Feed broadband noise (speech-like, non-stationary) to the output."""
    rng = np.random.default_rng(1234)
    total = int(seconds * SR)
    sent = 0
    t0 = time.monotonic()
    # Pink-ish noise via a simple low-passed white noise.
    state = 0.0
    while sent < total:
        white = rng.standard_normal(FRAME_SAMPLES).astype(np.float32)
        # One-pole low-pass for a speech-band spectrum.
        out = np.empty_like(white)
        for i, w in enumerate(white):
            state = 0.92 * state + 0.08 * w
            out[i] = state
        # Normalize to a fixed RMS so the echo level is stable.
        rms = float(np.sqrt(np.mean(out * out))) or 1.0
        samples = out / rms * 0.20
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        frame = rtc.AudioFrame(
            data=pcm.tobytes(),
            sample_rate=SR,
            num_channels=1,
            samples_per_channel=FRAME_SAMPLES,
        )
        await source.capture_frame(frame)
        sent += FRAME_SAMPLES
        delay = (t0 + sent / SR) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


async def _monitor(mic_track: rtc.Track, stats: _Stats) -> None:
    stream = rtc.AudioStream(mic_track, sample_rate=SR, num_channels=1)
    try:
        async for event in stream:
            rms = _rms(event.frame)
            if stats.playing:
                stats.play.append(rms)
            else:
                stats.idle.append(rms)
    except asyncio.CancelledError:
        pass
    finally:
        await stream.aclose()


def _summ(values: list[float]) -> str:
    if not values:
        return "n/a"
    arr = np.asarray(values, dtype=np.float32)
    return f"median={np.median(arr):.3f} p90={np.percentile(arr, 90):.3f} max={arr.max():.3f}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-aec", action="store_true", help="Disable echo cancellation")
    parser.add_argument("--list", action="store_true", help="List devices and exit")
    parser.add_argument("--input", type=int, default=None, help="Input device index")
    parser.add_argument("--output", type=int, default=None, help="Output device index")
    parser.add_argument("--seconds", type=float, default=8.0, help="Playback duration")
    args = parser.parse_args()

    if args.list:
        _list_devices()
        return

    loop = asyncio.get_running_loop()
    devices = rtc.MediaDevices(
        loop=loop,
        input_sample_rate=SR,
        output_sample_rate=SR,
        num_channels=1,
        blocksize=4800,
    )

    aec = not args.no_aec
    mic = devices.open_input(
        enable_aec=aec,
        noise_suppression=True,
        high_pass_filter=True,
        auto_gain_control=False,
        input_device=args.input,
    )
    player = devices.open_output(output_device=args.output)

    mic_track = rtc.LocalAudioTrack.create_audio_track("mic-monitor", mic.source)
    tts_source = rtc.AudioSource(SR, 1, loop=loop)
    tts_track = rtc.LocalAudioTrack.create_audio_track("tts", tts_source)
    await player.add_track(tts_track)
    await player.start()

    stats = _Stats()
    monitor_task = asyncio.create_task(_monitor(mic_track, stats))

    label = f"input={args.input or 'default'} output={args.output or 'default'} aec={aec}"
    print(f"[{label}] settling 1.5s ...")
    await asyncio.sleep(1.5)

    print("playing speech-band noise ...")
    stats.playing = True
    await _play_noise(tts_source, args.seconds)
    stats.playing = False

    print("idle 1.5s ...")
    await asyncio.sleep(1.5)

    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    await mic.aclose()
    await player.aclose()

    print(f"RESULT {label}")
    print(f"  during playback echo : {_summ(stats.play)}")
    print(f"  while idle (baseline): {_summ(stats.idle)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
