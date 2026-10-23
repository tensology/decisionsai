"""Offline hands-free echo / barge-in harness.

Drives the SAME classes the live pipeline uses, frame by frame, without audio
devices:  ReferenceBuffer -> WebRTCAECFilter (audio_in_filter) ->
SileroVADAnalyzer (VADParams from session code) -> BaseSTTService
``_handle_speaking_started`` barge-in logic.

Timeline per 20 ms tick (mirrors the transport):
  * output side: 20 ms of TTS audio is "written" then pushed to the reference
    buffer (transport.py: push after write);
  * input side: the mic frame for the same tick (echo of the reference
    delayed by an acoustic path, plus optional user speech) goes through the
    AEC filter and the VAD.
The reference buffer is active from TTS start until TTS stop (plus whatever
hang-over the code under test applies) exactly as the transport does.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field

import numpy as np

IN_SR = 16000
OUT_SR = 24000
TICK_MS = 20
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vad_echo")
ECHO_TEXT = ("Here is a summary of your calendar for today. You have three meetings, "
             "the first one starts at nine thirty with the design team, and then "
             "lunch with Sarah at one o'clock.")
USER_TEXT = "Stop, wait a second, cancel that please."


# ---------------------------------------------------------------- audio utils
def _read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels())[:, 0]
    return data.astype(np.float32) / 32768.0, sr


def resample(x, sr_from, sr_to):
    if sr_from == sr_to:
        return x.astype(np.float32)
    n = int(round(len(x) * sr_to / sr_from))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _say_to_wav(text, voice, path, sr):
    """Local macOS TTS (stand-in for kokoro; no network, no Fish/oracle)."""
    tmp = tempfile.mkdtemp()
    try:
        aiff = os.path.join(tmp, "o.aiff")
        subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{sr}", "-c", "1", aiff, path], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_clips():
    """Return (tts_out@OUT_SR, user@IN_SR). Generates fixtures with `say` once."""
    tts_p = os.path.join(FIXTURE_DIR, "tts_samantha_24k.wav")
    usr_p = os.path.join(FIXTURE_DIR, "user_daniel_16k.wav")
    if not (os.path.exists(tts_p) and os.path.exists(usr_p)):
        if not shutil.which("say"):
            raise FileNotFoundError("vad_echo fixtures missing and macOS `say` unavailable")
        os.makedirs(FIXTURE_DIR, exist_ok=True)
        _say_to_wav(ECHO_TEXT, "Samantha", tts_p, OUT_SR)
        _say_to_wav(USER_TEXT, "Daniel", usr_p, IN_SR)
    tts, sr1 = _read_wav(tts_p)
    usr, sr2 = _read_wav(usr_p)
    tts = resample(tts, sr1, OUT_SR) * 0.7  # typical TTS output level
    return tts, resample(usr, sr2, IN_SR)


def make_echo(tts_out, delay_ms, gain=0.45, rt60_ms=180, noise_db=-55, seed=0):
    """Speaker->mic path: delay, attenuation, exponential-decay reverb, noise."""
    rng = np.random.default_rng(seed)
    x = resample(tts_out, OUT_SR, IN_SR)
    ir_len = int(IN_SR * rt60_ms / 1000)
    t = np.arange(ir_len) / IN_SR
    ir = rng.standard_normal(ir_len) * np.exp(-6.9 * t / (rt60_ms / 1000)) * 0.08
    ir[0] = 1.0
    y = np.convolve(x, ir)[: len(x) + ir_len]
    y = y / (np.max(np.abs(y)) + 1e-9) * np.max(np.abs(x)) * gain
    d = int(IN_SR * delay_ms / 1000)
    y = np.concatenate([np.zeros(d, np.float32), y]).astype(np.float32)
    return y, (10 ** (noise_db / 20.0)), rng


# ------------------------------------------------------------------ results
@dataclass
class RunResult:
    vad_starts: list = field(default_factory=list)      # times (s) VAD -> SPEAKING
    bargeins: list = field(default_factory=list)        # times (s) STT barge-in fired
    max_conf_active: float = 0.0                        # max Silero conf while AEC active
    max_conf_after: float = 0.0                         # max conf after TTS stop (tail)
    erle_db: list = field(default_factory=list)
    aec_active_frames: int = 0
    aec_passthrough_frames: int = 0
    tts_stop_s: float = 0.0
    aec_off_s: float = 0.0
    cleaned: np.ndarray | None = None
    vad_params: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)

    def summary(self):
        e = np.array(self.erle_db) if self.erle_db else np.zeros(1)
        return (f"vad_params={self.vad_params} vad_starts={[round(v,2) for v in self.vad_starts]} "
                f"bargeins={[round(v,2) for v in self.bargeins]} max_conf_active={self.max_conf_active:.2f} "
                f"max_conf_after_stop={self.max_conf_after:.2f} erle_db(median/p10)={np.median(e):.1f}/"
                f"{np.percentile(e,10):.1f} aec_active={self.aec_active_frames} passthrough={self.aec_passthrough_frames} "
                f"tts_stop={self.tts_stop_s:.2f}s aec_off={self.aec_off_s:.2f}s")


def _make_stt(ref_buf, aec_filter):
    from distr.core.agent.services.stt.base import BaseSTTService

    class _HarnessSTT(BaseSTTService):
        async def run_stt(self, audio):  # pragma: no cover - not used
            if False:
                yield None

        async def _push_bargein_interruption(self):
            self.harness_bargeins += 1

        async def push_frame(self, frame, direction=None):  # not in a pipeline
            return None

        async def _on_speaking_stopped(self, frame, direction):
            return None

    stt = _HarnessSTT(is_hands_free=True, aec_ref_buf=ref_buf)
    stt._aec_filter = aec_filter
    stt.harness_bargeins = 0
    return stt


def build_vad(hands_free=True):
    """Build the Silero VAD exactly as session.py does (incl. any hands-free params)."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from distr.core.agent import constants as C
    try:
        from distr.core.agent.vad_params import build_vad_params  # added by fix, if present
    except Exception:
        build_vad_params = None
    vad = SileroVADAnalyzer()
    conf = C.VAD_DEFAULT_THRESHOLD / 100.0
    if build_vad_params is not None:
        params = build_vad_params(C.VAD_DEFAULT_THRESHOLD, hands_free=hands_free)
    else:
        from pipecat.audio.vad.vad_analyzer import VADParams
        params = VADParams(start_secs=C.VAD_START_SECS, confidence=conf)
    vad.set_sample_rate(IN_SR)
    vad.set_params(params)
    return vad, params


def aec_hangover_secs():
    try:
        from distr.core.agent import constants as C
        return float(getattr(C, "HANDS_FREE_AEC_HANGOVER_SECS", 0.0))
    except Exception:
        return 0.0


async def _run(tts_out, mic, tts_start_s, tts_end_s, reference_delay_ms=40.0):
    from distr.core.audio.echo_canceller import ReferenceBuffer, WebRTCAECFilter
    from pipecat.audio.vad.vad_analyzer import VADState
    from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=OUT_SR)
    aec = WebRTCAECFilter(ref_buf, output_sample_rate=OUT_SR, reference_delay_ms=reference_delay_ms)
    await aec.start(IN_SR)
    vad, params = build_vad()
    stt = _make_stt(ref_buf, aec)
    hang = aec_hangover_secs()
    res = RunResult(vad_params=dict(confidence=params.confidence, start_secs=params.start_secs,
                                    stop_secs=params.stop_secs, min_volume=params.min_volume,
                                    aec_hangover_s=hang))
    res.tts_stop_s = tts_end_s
    res.aec_off_s = tts_end_s + hang

    in_n = IN_SR * TICK_MS // 1000
    out_n = OUT_SR * TICK_MS // 1000
    n_ticks = len(mic) // in_n
    prev_state = VADState.QUIET
    cleaned = []
    for i in range(n_ticks):
        t = i * TICK_MS / 1000.0
        # output transport side
        if tts_start_s <= t < tts_end_s:
            if not ref_buf.is_active and t < tts_start_s + 0.001 + TICK_MS / 1000:
                ref_buf.set_active(True)          # TTSStartedFrame
            k = int((t - tts_start_s) * OUT_SR)
            chunk = tts_out[k:k + out_n]
            if len(chunk) < out_n:
                chunk = np.pad(chunk, (0, out_n - len(chunk)))
            ref_buf.push(chunk.astype(np.float32))  # pushed after device write
        elif t >= tts_end_s + hang and ref_buf.is_active:
            ref_buf.set_active(False)             # playback drained (+hang-over)
        # input transport side
        frame = mic[i * in_n:(i + 1) * in_n]
        pcm = np.clip(np.round(frame * 32767), -32768, 32767).astype(np.int16).tobytes()
        active = ref_buf.is_active
        out = await aec.filter(pcm)
        if active:
            res.aec_active_frames += 1
            res.erle_db.append(aec.last_metrics.get("erle_db", 0.0))
        else:
            res.aec_passthrough_frames += 1
        cleaned.append(np.frombuffer(out, np.int16))
        state = await vad.analyze_audio(out)
        conf = float(getattr(vad, "_harness_last_conf", 0.0))
        if active:
            res.max_conf_active = max(res.max_conf_active, conf)
        elif t >= tts_end_s:
            res.max_conf_after = max(res.max_conf_after, conf)
        m = aec.last_metrics if active else {}
        res.trace.append((t, active, m.get("input_rms", 0.0), m.get("residual_rms", 0.0),
                          m.get("reference_rms", 0.0), conf))
        before = stt.harness_bargeins
        # Same order as the transport: VAD frame first, then the audio frame
        # reaches the STT (vosk.py process_frame).
        stt._record_echo_frame_metrics()
        D = FrameDirection.DOWNSTREAM
        if state == VADState.SPEAKING and prev_state != VADState.SPEAKING:
            res.vad_starts.append(t)
            await stt._handle_speaking_started(VADUserStartedSpeakingFrame(), D)
        elif state == VADState.QUIET and prev_state != VADState.QUIET:
            await stt._handle_speaking_stopped(VADUserStoppedSpeakingFrame(), D)
        if state in (VADState.SPEAKING, VADState.QUIET):
            prev_state = state
        if not stt._user_speaking:
            stt._pre_buffer.append(out)
            await stt._check_pending_bargein(out, D)
        if stt.harness_bargeins > before:
            res.bargeins.append(t)
    res.cleaned = np.concatenate(cleaned).astype(np.float32) / 32768.0
    return res


def _instrument(vad_cls):
    """Record the raw Silero confidence (no behaviour change)."""
    if getattr(vad_cls, "_harness_patched", False):
        return
    orig = vad_cls.voice_confidence

    def wrapped(self, buffer):
        c = orig(self, buffer)
        self._harness_last_conf = float(np.asarray(c).reshape(-1)[0])
        return c
    vad_cls.voice_confidence = wrapped
    vad_cls._harness_patched = True


def run_case(case, delay_ms=90.0, user_at_s=2.5, user_gain=0.9, echo_gain=0.45, seed=0):
    """Case 1: echo only. Case 2: echo + user speech; on the first barge-in the
    TTS is cut there (as the pipeline interruption does) and the run repeated
    so the echo genuinely stops at the interruption point."""
    res = _run_case(case, delay_ms, user_at_s, user_gain, echo_gain, seed, None)
    if case == 2 and res.bargeins:
        res = _run_case(case, delay_ms, user_at_s, user_gain, echo_gain, seed, res.bargeins[0])
    return res


def _run_case(case, delay_ms, user_at_s, user_gain, echo_gain, seed, cut_at_s):
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    _instrument(SileroVADAnalyzer)
    tts, user = load_clips()
    if cut_at_s is not None:
        tts = tts[: max(0, int((cut_at_s - 0.5 + TICK_MS / 1000) * OUT_SR))]
    lead = 0.5
    tts_start, tts_end = lead, lead + len(tts) / OUT_SR
    echo, noise_amp, rng = make_echo(tts, delay_ms, gain=echo_gain, seed=seed)
    total = int((tts_end + 1.5) * IN_SR)
    mic = np.zeros(total, np.float32)
    s = int(lead * IN_SR)
    mic[s:s + len(echo)] += echo[: total - s]
    user_onset = None
    if case == 2:
        u = int((lead + user_at_s) * IN_SR)
        seg = user[: total - u] * user_gain
        mic[u:u + len(seg)] += seg
        nz = np.nonzero(np.abs(user) > 0.02)[0]
        user_onset = lead + user_at_s + (nz[0] / IN_SR if len(nz) else 0)
    mic += rng.standard_normal(total).astype(np.float32) * noise_amp
    res = asyncio.run(_run(tts, mic, tts_start, tts_end))
    res.user_onset = user_onset
    res.interrupted_at = cut_at_s
    starts = res.bargeins or res.vad_starts
    if case == 2 and starts:
        # Transcript proxy: after the detected user turn, the cleaned mic must follow the
        # user's voice, not the echo (envelope correlation over 1.2 s).
        b = int(starts[0] * IN_SR)
        w = slice(b, b + int(1.2 * IN_SR))
        def env(x):
            x = np.abs(x[w]); k = 320
            return x[: len(x) // k * k].reshape(-1, k).mean(1)
        uref = np.zeros(total, np.float32); uref[u:u + len(seg)] = seg
        eref = np.zeros(total, np.float32); eref[s:s + len(echo)] = echo[: total - s]
        c = env(res.cleaned)
        res.corr_user = float(np.corrcoef(c, env(uref))[0, 1])
        res.corr_echo = float(np.corrcoef(c, env(eref))[0, 1])
    return res
