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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import pytest

pytest.importorskip("pyaudio")
pytest.importorskip("pipecat.audio.filters.base_audio_filter")

import pyaudio
from pydub import AudioSegment
from distr.core.audio.echo_canceller import ReferenceBuffer, NLMSEchoCanceller
from distr.core.agent.services.stt.base import BaseSTTService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState


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
    double_talk = None
    if args.double_talk_file:
        double_talk, double_talk_sr = load_audio(args.double_talk_file)
        if double_talk_sr != tts_sr:
            target_len = round(len(double_talk) * tts_sr / double_talk_sr)
            source_positions = np.linspace(0, len(double_talk) - 1, target_len)
            double_talk = np.interp(source_positions, np.arange(len(double_talk)), double_talk)
        double_talk = double_talk[:int(2.5 * tts_sr)]
    input_double_talk = None
    if double_talk is not None and args.double_talk_source == "input":
        input_len = round(len(double_talk) * 16000 / tts_sr)
        input_positions = np.linspace(0, len(double_talk) - 1, input_len)
        input_double_talk = np.interp(
            input_positions, np.arange(len(double_talk)), double_talk
        ).astype(np.float32)

    MIC_SR = 16000
    CHUNK_SAMPLES = 320  # 20ms at 16kHz

    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=tts_sr)
    aec = NLMSEchoCanceller(reference_buffer=ref_buf, filter_length=args.filter_length, mu=args.mu,
                             output_sample_rate=tts_sr,
                             reference_delay_ms=args.delay_ms)
    await aec.start(MIC_SR)
    vad = SileroVADAnalyzer(
        sample_rate=MIC_SR,
        params=VADParams(confidence=0.5, start_secs=0.2, stop_secs=0.8),
    )
    vad.set_sample_rate(MIC_SR)
    pa = pyaudio.PyAudio()

    processed_queue = []
    probe_queue = []
    lock = threading.Lock()
    playback_done = threading.Event()
    playback_started = threading.Event()
    rec_stop = threading.Event()
    vad_state = {"state": VADState.QUIET, "starts": 0, "stops": 0}
    vad_trigger_metrics = []
    gate_state = {"history": [], "suppressed_starts": 0, "allowed_starts": 0}

    def record_thread():
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=MIC_SR,
                    input=True, input_device_index=args.in_dev,
                    frames_per_buffer=CHUNK_SAMPLES)
        playback_started.wait(timeout=5)
        input_cursor = 0
        while not rec_stop.is_set():
            raw_bytes = s.read(CHUNK_SAMPLES, exception_on_overflow=False)
            if input_double_talk is not None:
                raw_f32 = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                talk_start = int(args.double_talk_start * MIC_SR)
                talk_offset = input_cursor - talk_start
                if talk_offset < len(input_double_talk) and talk_offset + len(raw_f32) > 0:
                    src_start = max(0, -talk_offset)
                    dst_start = max(0, talk_offset)
                    count = min(len(raw_f32) - dst_start, len(input_double_talk) - src_start)
                    if count > 0:
                        raw_f32[dst_start:dst_start + count] += (
                            input_double_talk[src_start:src_start + count] * args.double_talk_gain
                        )
                        raw_bytes = (np.clip(raw_f32, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                input_cursor += len(raw_f32)
            if args.probe_delay:
                mic_probe = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                if time.monotonic() - probe_started >= 1.0:
                    ref_len = int(len(mic_probe) * tts_sr / MIC_SR)
                    scores = []
                    for delay_ms in range(0, 251, 10):
                        ref = ref_buf.pull(ref_len, round(delay_ms * tts_sr / 1000.0))
                        ref = NLMSEchoCanceller._resample(ref, len(mic_probe))
                        mic_centered = mic_probe - np.mean(mic_probe)
                        ref_centered = ref - np.mean(ref)
                        score = abs(float(np.dot(mic_centered, ref_centered))) / max(
                            float(np.linalg.norm(mic_centered) * np.linalg.norm(ref_centered)), 1e-8
                        )
                        scores.append((score, delay_ms))
                    with lock:
                        probe_queue.append(max(scores))
            aec_bytes = asyncio.run(aec.filter(raw_bytes))
            metrics = dict(aec.last_metrics)
            new_vad_state = asyncio.run(vad.analyze_audio(aec_bytes))
            if new_vad_state == VADState.SPEAKING and vad_state["state"] != VADState.SPEAKING:
                vad_state["starts"] += 1
                vad_trigger_metrics.append(metrics)
                current_classification = BaseSTTService._reference_dominance_from_metrics(metrics)
                sequence = gate_state["history"] + [current_classification]
                gate_suppresses = not (
                    len(sequence) >= 3
                    and sequence[-3:] == [False, False, False]
                ) and bool(gate_state["history"])
                if gate_suppresses:
                    gate_state["suppressed_starts"] += 1
                else:
                    gate_state["allowed_starts"] += 1
            if new_vad_state == VADState.QUIET and vad_state["state"] == VADState.SPEAKING:
                vad_state["stops"] += 1
            if new_vad_state in (VADState.QUIET, VADState.SPEAKING):
                vad_state["state"] = new_vad_state
            gate_state["history"].append(
                BaseSTTService._reference_dominance_from_metrics(metrics)
            )
            if len(gate_state["history"]) > 15:
                del gate_state["history"][:-15]
            with lock:
                processed_queue.append((raw_bytes, aec_bytes, metrics))
        s.stop_stream(); s.close()

    def playback_thread():
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=tts_sr,
                    output=True, output_device_index=args.out_dev)
        ref_buf.set_active(True)
        playback_started.set()
        ch = int(tts_sr * 0.02)
        for i in range(0, len(tts_audio), ch):
            c = tts_audio[i:i+ch]
            speaker_chunk = c.copy()
            if double_talk is not None:
                talk_start = int(args.double_talk_start * tts_sr)
                talk_end = talk_start + len(double_talk)
                if talk_start <= i < talk_end:
                    talk_offset = i - talk_start
                    talk_chunk = double_talk[talk_offset:talk_offset + len(c)]
                    speaker_chunk[:len(talk_chunk)] += talk_chunk * args.double_talk_gain
            s.write((np.clip(speaker_chunk, -1, 1) * 32767).astype(np.int16).tobytes())
            # Match the production transport: timestamp the reference after
            # the device accepts the audio frame, not before the write.
            ref_buf.push(c)
        ref_buf.set_active(False)
        s.stop_stream(); s.close()
        playback_done.set()

    probe_started = time.monotonic()
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
    dominated_frames = 0
    strongly_dominated_frames = 0
    measured_dominated_frames = 0
    measured_double_talk_frames = 0
    correlated_frames = 0
    tail_deadline = None

    while True:
        if playback_done.is_set() and tail_deadline is None:
            tail_deadline = time.time() + 1.0
            print("\n  --- TTS ended, recording 1s tail ---\n")
        with lock:
            pending = list(processed_queue)
            processed_queue.clear()
        for raw_bytes, aec_bytes, metrics in pending:
            raw_f32 = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            raw_r = rms(raw_f32)
            aec_f32 = np.frombuffer(aec_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            aec_r = rms(aec_f32)
            correlation = float(metrics.get("reference_correlation", 0.0))
            correlated_frames += int(correlation >= 0.35)
            dominated_frames += int(correlation >= 0.35)
            strongly_dominated_frames += int(correlation >= 0.7)
            ref_rms = float(metrics.get("reference_rms", 0.0) or 0.0)
            input_rms = float(metrics.get("input_rms", 0.0) or 0.0)
            residual_rms = float(metrics.get("residual_rms", 0.0) or 0.0)
            measured_dominated = (
                correlation >= 0.45
                and ref_rms >= residual_rms * 1.15
                and ref_rms >= input_rms * 0.55
            )
            measured_dominated_frames += int(measured_dominated)
            measured_double_talk_frames += int(
                not measured_dominated and aec_r >= 0.04
            )
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

    if args.probe_delay:
        with lock:
            probes = list(probe_queue)
        print(f"\nProbe best delay: {max(probes) if probes else 'none'}")
    print(f"AEC calibrated delay: {aec._estimated_delay_ms}")
    print(f"Silero VAD transitions on post-AEC mic: starts={vad_state['starts']}; stops={vad_state['stops']}")
    print(f"Turn gate result for VAD starts: suppressed={gate_state['suppressed_starts']}; allowed={gate_state['allowed_starts']}")
    if vad_trigger_metrics:
        print(f"First Silero start metrics: {vad_trigger_metrics[0]}")
    print(f"Reference-correlated frames: {correlated_frames}; correlation-only dominated: {dominated_frames}; measured dominated: {measured_dominated_frames}; strongly correlated: {strongly_dominated_frames}; measured double-talk candidates: {measured_double_talk_frames}")

    print("\nDone. Compare 'AEC RMS' values:")
    print("  - Silent periods (echo only) should be < 0.02")
    print("  - Speaking periods should be > 0.04 for barge-in to work")
    print("  - If speaking is between 0.025-0.04, threshold needs lowering")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="assets/tmp/tts_chat_kokoro_4cd3bd24a65a.mp3")
    parser.add_argument("--out-dev", type=int, default=2)
    parser.add_argument("--in-dev", type=int, default=1)
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--filter-length", type=int, default=800)
    parser.add_argument("--mu", type=float, default=0.5)
    parser.add_argument("--probe-delay", action="store_true")
    parser.add_argument("--double-talk-file")
    parser.add_argument(
        "--double-talk-source",
        choices=("speaker", "input"),
        default="speaker",
        help="Route the voice signal through the TTS speaker or inject it into the mic track",
    )
    parser.add_argument("--double-talk-out-dev", type=int, default=0)
    parser.add_argument("--double-talk-gain", type=float, default=1.0)
    parser.add_argument(
        "--double-talk-start",
        type=float,
        default=2.0,
        help="Seconds after playback start to mix the independent voice signal",
    )
    args = parser.parse_args()
    if not os.path.exists(args.file):
        print(f"Error: {args.file} not found"); sys.exit(1)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
