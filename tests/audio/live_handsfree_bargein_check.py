#!/usr/bin/env python
"""LIVE hands-free echo + barge-in check (needs Paul at the mic).

Runs OUTSIDE the Decisions app with the same classes as the pipeline:
ReferenceBuffer -> WebRTCAECFilter -> SileroVADAnalyzer (hands-free VADParams)
-> BaseSTTService barge-in gate.  Nothing is sent to the running app.

Phase 1 (echo only, no cue): plays the TTS clip through the speakers.
  PASS if no VAD user turn / barge-in fires.
Phase 2 (barge-in over echo): prints + plays "Paul, start talking now"
  (rendered locally with macOS `say`, played through the same output stream
  and fed to the AEC reference so the cue is cancelled, and detection is armed
  only after the cue ends), then loops the TTS clip. Paul talks over it.
  PASS if a barge-in fires within --timeout seconds (default 20).

Before running: pause listening / hands-free in Decisions so the app does not
react to the speaker audio. Use speakers (not headphones) to test echo.
Recordings are written to /tmp/vadecho_live/.
"""
import argparse
import asyncio
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import vad_echo_harness as H  # noqa: E402

CUE = "Paul, start talking now"
OUT_DIR = "/tmp/vadecho_live"


def say_cue():
    tmp = tempfile.mkdtemp()
    wav = os.path.join(tmp, "cue.wav")
    H._say_to_wav(CUE, "Samantha", wav, H.OUT_SR)
    x, sr = H._read_wav(wav)
    return H.resample(x, sr, H.OUT_SR) * 0.7


class Player(threading.Thread):
    """Blocking 20 ms writes; reference pushed AFTER each write (transport.py)."""

    def __init__(self, sd, ref_buf, device=None):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.ref = ref_buf
        self.stop_flag = threading.Event()
        self.stream = sd.OutputStream(samplerate=H.OUT_SR, channels=1, dtype="float32",
                                      blocksize=H.OUT_SR * H.TICK_MS // 1000, device=device)

    def play(self, audio):
        n = H.OUT_SR * H.TICK_MS // 1000
        for i in range(0, len(audio), n):
            c = audio[i:i + n]
            self.q.put(np.pad(c, (0, n - len(c))).astype(np.float32))

    def flush(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    @property
    def idle(self):
        return self.q.empty()

    def run(self):
        self.stream.start()
        n = H.OUT_SR * H.TICK_MS // 1000
        while not self.stop_flag.is_set():
            try:
                c = self.q.get(timeout=0.02)
            except queue.Empty:
                c = None
            if c is None:
                continue
            self.stream.write(c.reshape(-1, 1))
            self.ref.push(c)
        self.stream.stop(); self.stream.close()


async def phase(sd, args, with_cue):
    from distr.core.audio.echo_canceller import ReferenceBuffer, WebRTCAECFilter
    from pipecat.audio.vad.vad_analyzer import VADState
    from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    tts, _ = H.load_clips()
    ref = ReferenceBuffer(max_duration_secs=2.0, sample_rate=H.OUT_SR)
    aec = WebRTCAECFilter(ref, output_sample_rate=H.OUT_SR, reference_delay_ms=args.aec_delay_ms)
    await aec.start(H.IN_SR)
    vad, params = H.build_vad(hands_free=True)
    stt = H._make_stt(ref, aec)
    mic_q = queue.Queue()
    in_n = H.IN_SR * H.TICK_MS // 1000

    def cb(indata, frames, t, status):
        mic_q.put(np.clip(indata[:, 0] * 32767, -32768, 32767).astype(np.int16).tobytes())

    player = Player(sd, ref, args.output_device)
    instream = sd.InputStream(samplerate=H.IN_SR, channels=1, dtype="float32", blocksize=in_n,
                              callback=cb, device=args.input_device)
    raw, cleaned, events = [], [], []
    t0 = time.monotonic()
    armed_at = None
    tts_start = None
    ref.set_active(True)  # TTSStartedFrame
    player.start(); instream.start()
    if with_cue:
        cue = say_cue()
        print(f"\n>>> {CUE}\n", flush=True)
        player.play(cue)
        cue_end = time.monotonic() + len(cue) / H.OUT_SR + 0.3
    else:
        cue_end = t0
    clip_queued = False
    deadline = None
    prev = VADState.QUIET
    result = None
    D = FrameDirection.DOWNSTREAM
    try:
        while True:
            now = time.monotonic()
            if now >= cue_end and not clip_queued:
                tts_start = now
                reps = max(1, int(np.ceil(args.timeout * H.OUT_SR / len(tts)))) if with_cue else 1
                for _ in range(reps):
                    player.play(tts)
                clip_queued = True
                armed_at = now
                deadline = now + (args.timeout if with_cue else len(tts) / H.OUT_SR + 1.0)
            try:
                pcm = mic_q.get(timeout=0.1)
            except queue.Empty:
                continue
            out = await aec.filter(pcm)
            raw.append(np.frombuffer(pcm, np.int16)); cleaned.append(np.frombuffer(out, np.int16))
            state = await vad.analyze_audio(out)
            before = stt.harness_bargeins
            stt._record_echo_frame_metrics()
            if state == VADState.SPEAKING and prev != VADState.SPEAKING:
                tag = "armed" if armed_at else "during-cue(ignored)"
                events.append((round(now - t0, 2), "vad_start", tag))
                if armed_at:
                    await stt._handle_speaking_started(VADUserStartedSpeakingFrame(), D)
            elif state == VADState.QUIET and prev != VADState.QUIET:
                await stt._handle_speaking_stopped(VADUserStoppedSpeakingFrame(), D)
            if state in (VADState.SPEAKING, VADState.QUIET):
                prev = state
            if not stt._user_speaking:
                stt._pre_buffer.append(out)
                await stt._check_pending_bargein(out, D)
            if stt.harness_bargeins > before and armed_at:
                lat = now - armed_at
                events.append((round(now - t0, 2), "barge_in", f"{lat:.2f}s after armed"))
                print(f"barge-in detected {lat:.2f}s after detection armed "
                      f"(erle={aec.last_metrics.get('erle_db', 0):.1f} dB)", flush=True)
                player.flush()  # pipeline interruption stops TTS
                result = ("barge_in", lat)
                if with_cue:
                    break
            if deadline and now >= deadline:
                break
    finally:
        player.flush(); time.sleep(0.2)
        ref.set_active(False)
        player.stop_flag.set(); instream.stop(); instream.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    name = "phase2_bargein" if with_cue else "phase1_echo_only"
    for tag, data in (("mic", raw), ("aec", cleaned)):
        with wave.open(os.path.join(OUT_DIR, f"{name}_{tag}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(H.IN_SR)
            w.writeframes(np.concatenate(data).tobytes() if data else b"")
    return result, events, params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--aec-delay-ms", type=float, default=40.0)
    ap.add_argument("--input-device", default=None)
    ap.add_argument("--output-device", default=None)
    ap.add_argument("--skip-echo-phase", action="store_true")
    args = ap.parse_args()
    import sounddevice as sd
    print("in:", sd.query_devices(args.input_device, "input")["name"],
          "| out:", sd.query_devices(args.output_device, "output")["name"])
    ok = True
    if not args.skip_echo_phase:
        print("\nPhase 1: echo only - please stay SILENT.")
        res, ev, params = asyncio.run(phase(sd, args, with_cue=False))
        p1 = res is None
        ok &= p1
        print("VADParams:", dict(confidence=params.confidence, start_secs=params.start_secs,
                                 min_volume=params.min_volume))
        print("Phase 1 (echo only):", "PASS" if p1 else "FAIL (echo triggered barge-in)", ev)
        time.sleep(1.0)
    print("\nPhase 2: wait for the spoken cue, then talk over the assistant.")
    res, ev, _ = asyncio.run(phase(sd, args, with_cue=True))
    p2 = res is not None
    ok &= p2
    if p2:
        print(f"Phase 2 (barge-in over echo): PASS - {res[1]:.2f}s after cue", ev)
    else:
        print(f"Phase 2 (barge-in over echo): FAIL - no user speech detected within "
              f"{args.timeout:.0f}s of the cue", ev)
    print(f"Recordings: {OUT_DIR}/")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
