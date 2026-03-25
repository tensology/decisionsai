#!/usr/bin/env python3
"""
Quick RMS monitor — plays TTS through speaker while recording mic,
runs AEC, and prints the post-AEC RMS every 300ms.

This shows exactly what the STT service's pre_buffer would contain,
so we can see what threshold is needed for barge-in.

Usage:
    python tests/audio/test_rms_monitor.py [--file PATH] [--out-dev IDX] [--in-dev IDX]

Speak during playback and watch the AEC RMS values.
Echo (no speech) should be low; speech should be noticeably higher.
"""

import argparse
import asyncio
import sys
import os
import time
import threading
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import pyaudio
from pydub import AudioSegment
from distr.core.audio.echo_canceller import ReferenceBuffer, NLMSEchoCanceller


def load_audio(path: str):
    seg = AudioSegment.from_file(path)
    sr = seg.frame_rate
    samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples, sr


def rms(arr):
    return float(np.sqrt(np.mean(arr ** 2))) if len(arr) > 0 else 0.0


async def main_async(args):
    tts_audio, tts_sr = load_audio(args.file)
    max_samples = int(min(15.0, len(tts_audio) / tts_sr) * tts_sr)
    tts_audio = tts_audio[:max_samples]

    MIC_SR = 16000
    CHUNK_SAMPLES = 320  # 20ms at 16kHz

    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=tts_sr)
    aec = NLMSEchoCanceller(reference_buffer=ref_buf, filter_length=800, mu=0.5,
                             output_sample_rate=tts_sr)
    await aec.start(MIC_SR)
    pa = pyaudio.PyAudio()

    mic_queue = []
    lock = threading.Lock()
    playback_done = threading.Event()
    playback_started = threading.Event()
    rec_stop = threading.Event()

    def record_thread():
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=MIC_SR,
                    input=True, input_device_index=args.in_dev,
                    frames_per_buffer=CHUNK_SAMPLES)
        playback_started.wait(timeout=5)
        while not rec_stop.is_set():
            with lock:
                mic_queue.append(s.read(CHUNK_SAMPLES, exception_on_overflow=False))
        s.stop_stream(); s.close()

    def playback_thread():
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=tts_sr,
                    output=True, output_device_index=args.out_dev)
        ref_buf.set_active(True)
        playback_started.set()
        ch = int(tts_sr * 0.02)
        for i in range(0, len(tts_audio), ch):
            c = tts_audio[i:i+ch]
            ref_buf.push(c)
            s.write((np.clip(c, -1, 1) * 32767).astype(np.int16).tobytes())
        ref_buf.set_active(False)
        s.stop_stream(); s.close()
        playback_done.set()

    rec_t = threading.Thread(target=record_thread, daemon=True)
    play_t = threading.Thread(target=playback_thread, daemon=True)
    rec_t.start(); play_t.start()
    playback_started.wait(timeout=5)

    start = time.time()
    print(f"\n{'Time':>6}  {'RAW RMS':>9}  {'AEC RMS':>9}  {'Reduc':>8}  {'> 0.04?':>8}  {'> 0.025?':>9}  {'Consec':>6}")
    print("-" * 70)

    win_raw = []
    win_aec = []
    last_report = time.time()
    consecutive_04 = 0
    consecutive_025 = 0
    tail_deadline = None

    while True:
        if playback_done.is_set() and tail_deadline is None:
            tail_deadline = time.time() + 1.0
            print("\n  --- TTS ended, recording 1s tail ---\n")
        with lock:
            pending = list(mic_queue); mic_queue.clear()
        for raw_bytes in pending:
            raw_f32 = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            raw_r = rms(raw_f32)
            aec_bytes = await aec.filter(raw_bytes)
            aec_f32 = np.frombuffer(aec_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            aec_r = rms(aec_f32)
            win_raw.append(raw_r)
            win_aec.append(aec_r)

            if aec_r >= 0.04:
                consecutive_04 += 1
            else:
                consecutive_04 = 0
            if aec_r >= 0.025:
                consecutive_025 += 1
            else:
                consecutive_025 = 0

        now = time.time()
        if now - last_report >= 0.3 and win_raw:
            avg_raw = np.mean(win_raw)
            avg_aec = np.mean(win_aec)
            peak_aec = max(win_aec)
            db = 20*np.log10(avg_aec/avg_raw) if avg_raw>1e-8 and avg_aec>1e-8 else 0.0
            above_04 = "YES" if peak_aec >= 0.04 else "no"
            above_025 = "YES" if peak_aec >= 0.025 else "no"
            print(f"{now-start:5.1f}s  {avg_raw:9.5f}  {avg_aec:9.5f}  {db:+7.1f}dB  {above_04:>8}  {above_025:>9}  {consecutive_04:>6}")
            win_raw.clear(); win_aec.clear()
            last_report = now

        if tail_deadline and time.time() >= tail_deadline:
            break
        await asyncio.sleep(0.01)

    rec_stop.set()
    play_t.join(timeout=3)
    rec_t.join(timeout=3)
    pa.terminate()
    await aec.stop()

    print("\nDone. Compare 'AEC RMS' values:")
    print("  - Silent periods (echo only) should be < 0.02")
    print("  - Speaking periods should be > 0.04 for barge-in to work")
    print("  - If speaking is between 0.025-0.04, threshold needs lowering")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="assets/tmp/tts_chat_kokoro_4cd3bd24a65a.mp3")
    parser.add_argument("--out-dev", type=int, default=2)
    parser.add_argument("--in-dev", type=int, default=1)
    args = parser.parse_args()
    if not os.path.exists(args.file):
        print(f"Error: {args.file} not found"); sys.exit(1)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
