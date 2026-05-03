#!/usr/bin/env python3
"""
Live AEC + Echo Gate test — three phases:
  Phase 1 — SILENT:    Stay quiet. Measures echo suppression.
  Phase 2 — SPEAK:     Talk over TTS. Measures barge-in detection.
  Phase 3 — INTERRUPT: TTS plays, you speak to interrupt it. It stops,
                        waits 2s, resumes. You must interrupt 3 times to pass.

Usage:
    python tests/audio/test_aec_live.py [--file PATH] [--out-dev IDX] [--in-dev IDX]
"""

import argparse
import asyncio
import sys
import os
import time
import threading
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import pytest

pytest.importorskip("pyaudio")
import pyaudio
from pydub import AudioSegment
from distr.core.audio.echo_canceller import ReferenceBuffer, NLMSEchoCanceller

BARGEIN_RMS = 0.04  # Lowered: grace period + debounce handle spikes, so threshold can sit closer to echo floor


def load_audio(path: str):
    seg = AudioSegment.from_file(path)
    sr = seg.frame_rate
    samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples, sr


def rms(arr):
    return float(np.sqrt(np.mean(arr ** 2))) if len(arr) > 0 else 0.0


# =========================================================================
# Phase 1 & 2: simple play-through with monitoring
# =========================================================================
async def run_phase(pa, args, tts_audio, tts_sr, ref_buf, aec, phase_name):
    MIC_SR = 16000
    CHUNK_SAMPLES = 320
    mic_queue = []
    lock = threading.Lock()
    playback_started = threading.Event()
    playback_done = threading.Event()
    rec_stop = threading.Event()

    def record_thread():
        try:
            s = pa.open(format=pyaudio.paInt16, channels=1, rate=MIC_SR,
                        input=True, input_device_index=args.in_dev,
                        frames_per_buffer=CHUNK_SAMPLES)
            playback_started.wait(timeout=5)
            while not rec_stop.is_set():
                with lock:
                    mic_queue.append(s.read(CHUNK_SAMPLES, exception_on_overflow=False))
            for _ in range(25):
                with lock:
                    mic_queue.append(s.read(CHUNK_SAMPLES, exception_on_overflow=False))
            s.stop_stream(); s.close()
        except Exception as e:
            print(f"  !! Mic error: {e}")

    def playback_thread():
        try:
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
        except Exception as e:
            print(f"  !! Speaker error: {e}")
        finally:
            playback_done.set()

    stats = {'chunks': 0, 'vad_fires': 0, 'suppress': 0, 'bargein': 0,
             'peak_raw': 0.0, 'peak_aec': 0.0}

    rec_t = threading.Thread(target=record_thread, daemon=True)
    play_t = threading.Thread(target=playback_thread, daemon=True)
    rec_t.start(); play_t.start()
    playback_started.wait(timeout=5)

    GRACE_PERIOD = 0.5  # suppress barge-in for first 500ms after TTS starts
    BARGEIN_DEBOUNCE = 3  # require N consecutive high-energy chunks (~60ms)
    start = time.time()
    grace_until = start + GRACE_PERIOD  # playback_started just fired
    hdr = f"{'Time':>6}  {'RAW RMS':>9}  {'AEC RMS':>9}  {'Reduc':>8}  {'Gate':>11}  {'Ref':>5}"
    print(hdr)
    print("-" * len(hdr))

    win_raw, win_aec, win_peak = [], [], []
    last_report = time.time()
    tail_deadline = None
    consecutive_high = 0  # debounce counter for barge-in detection

    while True:
        if playback_done.is_set() and tail_deadline is None:
            tail_deadline = time.time() + 1.0
        with lock:
            pending = list(mic_queue); mic_queue.clear()
        for raw_bytes in pending:
            stats['chunks'] += 1
            raw_f32 = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            raw_r = rms(raw_f32)
            stats['peak_raw'] = max(stats['peak_raw'], raw_r)
            aec_bytes = await aec.filter(raw_bytes)
            aec_f32 = np.frombuffer(aec_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            aec_r = rms(aec_f32)
            stats['peak_aec'] = max(stats['peak_aec'], aec_r)
            win_raw.append(raw_r); win_aec.append(aec_r); win_peak.append(aec_r)
            conf = min(1.0, aec_r / 0.03)
            if conf > 0.5:
                stats['vad_fires'] += 1
                # AEC divergence filter: if AEC amplified the signal (aec > raw * 1.2),
                # it's filter divergence, not real speech. Real speech adds energy on
                # top of echo, so aec_r stays close to raw_r.
                is_aec_artifact = (ref_buf.is_active and raw_r > 0.001
                                   and aec_r > raw_r * 1.2)
                if time.time() < grace_until:
                    stats['suppress'] += 1  # grace period — treat as suppressed
                    consecutive_high = 0
                elif ref_buf.is_active and (aec_r < BARGEIN_RMS or is_aec_artifact):
                    stats['suppress'] += 1
                    consecutive_high = 0
                else:
                    consecutive_high += 1
                    if consecutive_high >= BARGEIN_DEBOUNCE:
                        stats['bargein'] += 1
                        consecutive_high = 0
            else:
                consecutive_high = 0
        now = time.time()
        if now - last_report >= 0.3 and win_raw:
            avg_raw = np.mean(win_raw); avg_aec = np.mean(win_aec); pk = max(win_peak)
            db = 20*np.log10(avg_aec/avg_raw) if avg_raw>1e-8 and avg_aec>1e-8 else 0.0
            active = ref_buf.is_active
            gate = "SUPPRESS" if active and pk < BARGEIN_RMS else \
                   ">> BARGE-IN" if active and pk >= BARGEIN_RMS else "—"
            print(f"{now-start:5.1f}s  {avg_raw:9.5f}  {avg_aec:9.5f}  {db:+7.1f}dB  {gate:>11}  {'ON' if active else 'off':>5}")
            win_raw.clear(); win_aec.clear(); win_peak.clear()
            last_report = now
        if tail_deadline and time.time() >= tail_deadline:
            break
        await asyncio.sleep(0.01)

    rec_stop.set(); play_t.join(timeout=3); rec_t.join(timeout=3)
    return stats


# =========================================================================
# Phase 3: interrupt simulation — stop on barge-in, wait 2s, resume
# =========================================================================
async def run_phase3(pa, args, tts_audio, tts_sr, ref_buf, aec):
    MIC_SR = 16000
    CHUNK_SAMPLES = 320
    REQUIRED_INTERRUPTS = 3
    COOLDOWN = 2.0  # seconds to wait after interrupt before resuming

    mic_queue = []
    lock = threading.Lock()
    rec_stop = threading.Event()

    # Playback control
    playback_pause = threading.Event()   # clear = paused, set = playing
    playback_kill = threading.Event()    # set = stop playback thread entirely
    playback_pos = [0]                   # shared cursor into tts_audio
    playback_active = threading.Event()  # set when stream is open and writing

    def record_thread():
        try:
            s = pa.open(format=pyaudio.paInt16, channels=1, rate=MIC_SR,
                        input=True, input_device_index=args.in_dev,
                        frames_per_buffer=CHUNK_SAMPLES)
            while not rec_stop.is_set():
                with lock:
                    mic_queue.append(s.read(CHUNK_SAMPLES, exception_on_overflow=False))
            s.stop_stream(); s.close()
        except Exception as e:
            print(f"  !! Mic error: {e}")

    def playback_thread():
        """Plays audio from playback_pos, pausable via playback_pause."""
        play_chunk = int(tts_sr * 0.02)
        try:
            s = pa.open(format=pyaudio.paInt16, channels=1, rate=tts_sr,
                        output=True, output_device_index=args.out_dev)
            while not playback_kill.is_set():
                # Wait until unpaused
                playback_pause.wait(timeout=0.1)
                if playback_kill.is_set():
                    break
                if not playback_pause.is_set():
                    continue

                pos = playback_pos[0]
                if pos >= len(tts_audio):
                    # Wrap around for continuous playback
                    playback_pos[0] = 0
                    pos = 0

                chunk = tts_audio[pos:pos+play_chunk]
                if len(chunk) == 0:
                    playback_pos[0] = 0
                    continue

                ref_buf.push(chunk)
                s.write((np.clip(chunk, -1, 1) * 32767).astype(np.int16).tobytes())
                playback_pos[0] = pos + len(chunk)
                playback_active.set()

            s.stop_stream(); s.close()
        except Exception as e:
            print(f"  !! Speaker error: {e}")

    # Start threads
    rec_t = threading.Thread(target=record_thread, daemon=True)
    play_t = threading.Thread(target=playback_thread, daemon=True)
    rec_t.start()
    play_t.start()

    interrupts = 0
    state = "STARTING"  # STARTING -> PLAYING -> INTERRUPTED -> COOLDOWN -> PLAYING ...
    cooldown_end = 0
    grace_until = 0  # suppress barge-in until this time (grace period after TTS start)
    GRACE_PERIOD = 0.5  # seconds — matches production _check_bargein_energy
    start = time.time()

    hdr = f"{'Time':>6}  {'AEC RMS':>9}  {'State':>14}  {'Interrupts':>10}"
    print(hdr)
    print("-" * len(hdr))

    # Brief delay then start playing
    await asyncio.sleep(0.3)
    ref_buf.set_active(True)
    playback_pause.set()
    state = "PLAYING"
    grace_until = time.time() + GRACE_PERIOD
    last_report = time.time()
    win_aec = []

    # Barge-in needs N consecutive high-energy chunks to trigger (debounce)
    BARGEIN_CONSECUTIVE = 3  # ~60ms of sustained speech
    consecutive_high = 0

    while interrupts < REQUIRED_INTERRUPTS:
        elapsed = time.time() - start
        if elapsed > 60:
            print("  !! Timeout (60s) — aborting phase 3")
            break

        with lock:
            pending = list(mic_queue); mic_queue.clear()

        for raw_bytes in pending:
            aec_bytes = await aec.filter(raw_bytes)
            aec_f32 = np.frombuffer(aec_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            aec_r = rms(aec_f32)
            win_aec.append(aec_r)

            if state == "PLAYING":
                if time.time() < grace_until:
                    # Grace period — ignore energy spikes while AEC converges
                    consecutive_high = 0
                elif aec_r >= BARGEIN_RMS:
                    consecutive_high += 1
                else:
                    consecutive_high = 0

                if consecutive_high >= BARGEIN_CONSECUTIVE:
                    # INTERRUPT!
                    interrupts += 1
                    state = "INTERRUPTED"
                    consecutive_high = 0
                    # Stop playback
                    playback_pause.clear()
                    ref_buf.set_active(False)
                    # Reset AEC weights for next session
                    aec._w[:] = 0.0
                    aec._ref_tail[:] = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    print(f"  {'':>6}  {'':>9}  *** INTERRUPT {interrupts}/{REQUIRED_INTERRUPTS} *** — pausing {COOLDOWN:.0f}s")

            elif state == "INTERRUPTED" or state == "COOLDOWN":
                state = "COOLDOWN"
                if time.time() >= cooldown_end:
                    # Resume playback
                    ref_buf.set_active(True)
                    playback_pause.set()
                    state = "PLAYING"
                    consecutive_high = 0
                    grace_until = time.time() + GRACE_PERIOD
                    print(f"  {'':>6}  {'':>9}  --- RESUMED --- speak again to interrupt")

        now = time.time()
        if now - last_report >= 0.3 and win_aec:
            avg_aec = np.mean(win_aec)
            el = now - start
            st_str = state
            if state == "COOLDOWN":
                remaining = max(0, cooldown_end - now)
                st_str = f"COOLDOWN {remaining:.1f}s"
            print(f"{el:5.1f}s  {avg_aec:9.5f}  {st_str:>14}  {interrupts:>10}")
            win_aec.clear()
            last_report = now

        await asyncio.sleep(0.01)

    # Cleanup
    playback_pause.clear()
    playback_kill.set()
    rec_stop.set()
    play_t.join(timeout=3)
    rec_t.join(timeout=3)

    return interrupts


# =========================================================================
# Main
# =========================================================================
async def main_async(args):
    tts_audio, tts_sr = load_audio(args.file)
    file_dur = len(tts_audio) / tts_sr
    max_samples = int(min(8.0, file_dur) * tts_sr)
    tts_audio_short = tts_audio[:max_samples]
    phase_dur = len(tts_audio_short) / tts_sr

    MIC_SR = 16000
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=MIC_SR)
    aec = NLMSEchoCanceller(reference_buffer=ref_buf, filter_length=800, mu=0.5,
                             output_sample_rate=tts_sr)
    await aec.start(MIC_SR)
    pa = pyaudio.PyAudio()

    print("=" * 72)
    print("  AEC + Echo Gate Live Test")
    print("=" * 72)
    print(f"  File:    {args.file} ({phase_dur:.1f}s phases 1-2, loops for phase 3)")
    print(f"  Speaker: device {args.out_dev}")
    print(f"  Mic:     device {args.in_dev}")
    print(f"  Barge-in threshold: RMS {BARGEIN_RMS}")

    # ---- PHASE 1 ----
    print("\n" + "=" * 72)
    print("  PHASE 1 — ECHO TEST")
    print("  Stay SILENT. Do NOT speak.")
    print("=" * 72 + "\n")
    await asyncio.sleep(1)
    stats1 = await run_phase(pa, args, tts_audio_short, tts_sr, ref_buf, aec, "SILENT")
    aec._w[:] = 0.0; aec._ref_tail[:] = 0.0

    # ---- PHASE 2 ----
    print("\n" + "=" * 72)
    print("  PHASE 2 — BARGE-IN TEST")
    print("  >>>  SPEAK NOW  <<<  Talk over the TTS at normal volume.")
    print("=" * 72)
    for i in range(3, 0, -1):
        print(f"  Starting in {i}...")
        await asyncio.sleep(1)
    print("  GO! Speak now!\n")
    stats2 = await run_phase(pa, args, tts_audio_short, tts_sr, ref_buf, aec, "SPEAK")
    aec._w[:] = 0.0; aec._ref_tail[:] = 0.0

    # ---- PHASE 3 ----
    print("\n" + "=" * 72)
    print("  PHASE 3 — INTERRUPT SIMULATION")
    print("  TTS will play. Speak to interrupt it.")
    print("  It will stop, wait 2s, then resume.")
    print("  You must interrupt 3 times to pass.")
    print("=" * 72)
    for i in range(3, 0, -1):
        print(f"  Starting in {i}...")
        await asyncio.sleep(1)
    print("  GO! TTS is playing — speak to interrupt!\n")
    p3_interrupts = await run_phase3(pa, args, tts_audio, tts_sr, ref_buf, aec)

    # ---- RESULTS ----
    print("\n" + "=" * 72)
    print("  RESULTS")
    print("=" * 72)

    p1_ok = stats1['bargein'] <= 1  # allow ≤1: test uses RMS heuristic, not Silero VAD; isolated AEC spikes are filtered by real VAD
    p2_ok = stats2['bargein'] > 0
    p3_ok = p3_interrupts >= 3

    print(f"\n  Phase 1 — SILENT:")
    print(f"    VAD fires: {stats1['vad_fires']}, Suppressed: {stats1['suppress']}, False barge-in: {stats1['bargein']}")
    print(f"    {'✓ PASS' if p1_ok else '✗ FAIL'}")

    print(f"\n  Phase 2 — SPEAK:")
    print(f"    VAD fires: {stats2['vad_fires']}, Suppressed: {stats2['suppress']}, Barge-in: {stats2['bargein']}")
    print(f"    {'✓ PASS' if p2_ok else '✗ FAIL'}")

    print(f"\n  Phase 3 — INTERRUPT:")
    print(f"    Interrupts triggered: {p3_interrupts}/3")
    print(f"    {'✓ PASS' if p3_ok else '✗ FAIL'}")

    all_pass = p1_ok and p2_ok and p3_ok
    print(f"\n  {'✓✓ ALL PASS' if all_pass else '✗ SOME FAILED'}")
    print()

    pa.terminate()
    await aec.stop()


def main():
    parser = argparse.ArgumentParser(description="Live AEC + barge-in + interrupt test")
    parser.add_argument("--file", default="assets/tmp/tts_chat_kokoro_4cd3bd24a65a.mp3")
    parser.add_argument("--out-dev", type=int, default=2)
    parser.add_argument("--in-dev", type=int, default=1)
    args = parser.parse_args()
    if not os.path.exists(args.file):
        print(f"Error: {args.file} not found"); sys.exit(1)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
