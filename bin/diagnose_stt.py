#!/usr/bin/env python3
"""
STT / VibeVoice diagnostic — compares Decisions DB settings to runtime readiness
and optionally runs a file transcription. Scans recent decisions.log for STT errors.

Usage (from repo root, app venv active):
  python bin/diagnose_stt.py
  python bin/diagnose_stt.py --grep-logs --log-lines 800
  python bin/diagnose_stt.py --transcribe-wav /path/to/sample.wav
  python bin/diagnose_stt.py --doctor   # run transcription_doctor tool (ffmpeg + backends)
  python bin/diagnose_stt.py --tts-roundtrip
      # 1) Synthesize WAV via your configured TTS (from settings DB)
      # 2) Transcribe that file via your configured STT — no microphone capture
  python bin/diagnose_stt.py --transcribe-mic [--mic-seconds 5]
      # Record from the default system microphone, then transcribe that WAV
      # (same file STT path the app uses after capture). Speak while it records.
  python bin/diagnose_stt.py --transcribe-mic --stt-engine vibevoice_asr
      # Same mic test but force VibeVoice ASR (use DECISIONSAI_VIBEVOICE_ASR_DEVICE=cpu on Mac if MPS fails)
  # First ASR run can pull TWO huge Hugging Face dirs: microsoft/VibeVoice-ASR and (by default)
  # Qwen/Qwen2.5-7B for the processor — many GB total, several minutes, looks like "more downloading".
  # Prefetch: huggingface-cli download microsoft/VibeVoice-ASR && huggingface-cli download Qwen/Qwen2.5-7B
  # Offline-only loads: DECISIONSAI_VIBEVOICE_ASR_HF_LOCAL_ONLY=1 (cache must already be complete).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Log / DB discovery (aligned with bin/diagnose.py)
def _maybe_prefetch_local_models_for_stt_cli(args: argparse.Namespace) -> None:
    """Warm Vosk / Whisper / VibeVoice HF caches before mic or file transcribe (opt-out via env)."""
    if (os.environ.get("DECISIONS_AI_SKIP_MODEL_PREFETCH") or "").strip() == "1":
        return
    if not (
        args.transcribe_mic
        or args.transcribe_wav
        or args.tts_roundtrip
        or args.doctor
    ):
        return
    script = PROJECT_ROOT / "scripts" / "prefetch_local_models.py"
    if not script.is_file():
        print(f"\n  WARNING: missing {script} — skip prefetch.\n")
        return
    print("\n=== Prefetch local STT/TTS caches (Vosk, Whisper, VibeVoice HF if installed) ===")
    r = subprocess.run(
        [sys.executable, str(script), "--only", "all"],
        cwd=str(PROJECT_ROOT),
    )
    if r.returncode != 0:
        print(f"  Prefetch exited {r.returncode} — continuing; first load may still download.\n")
    else:
        print("  Prefetch done.\n")


def _print_vibevoice_asr_preflight() -> None:
    """One-time console hints so HF multi-repo loads are not mistaken for a runaway downloader."""
    lm = (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_LM") or "Qwen/Qwen2.5-7B").strip()
    mid = (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_MODEL") or "microsoft/VibeVoice-ASR").strip()
    dev = (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_DEVICE") or "(auto)").strip() or "(auto)"
    print(
        "\n  --- VibeVoice ASR (local) ---\n"
        f"  HF repos: {mid!r} + LM {lm!r} (processor ties them together — expect large downloads on first run).\n"
        f"  Device: {dev}  |  cache: ~/.cache/huggingface/hub (or HF_HOME)\n"
        "  If a load crashes mid-way, re-running mostly resumes from cache; the app now keeps the\n"
        "  processor in memory on retry so it should not re-hit the LM download path in the same process.\n"
        "  CPU + 7B LM is RAM-heavy — OOM can look like an instant crash.\n"
        "  Prefetch without this script: huggingface-cli download "
        f"{mid} && huggingface-cli download {lm}\n"
    )


STT_LOG_PATTERNS = re.compile(
    r"vibevoice|VibeVoice|vibevoice_asr|VibeVoiceAsr|ASR|resolve_stt|transcription_model|"
    r"Falling back.*STT|run_stt|get_asr_batch|transcription error|STT:.*[Ee]rror|ImportError.*vibevoice",
    re.IGNORECASE,
)


def _find_db_and_logs() -> tuple[Path, Path]:
    candidates = [
        PROJECT_ROOT / "db",
        PROJECT_ROOT / "distr" / "db",
        Path.home() / ".decisions" / "db",
    ]
    db_path = PROJECT_ROOT / "distr" / "db" / "settings.db"
    log_file = PROJECT_ROOT / "distr" / "db" / "logs" / "decisions.log"
    for d in candidates:
        p = d / "settings.db"
        if p.exists():
            db_path = p
            break
    for d in candidates:
        p = d / "logs" / "decisions.log"
        if p.exists():
            log_file = p
            break
    return db_path, log_file


def _grep_stt_logs(log_path: Path, max_lines: int) -> list[str]:
    if not log_path.is_file():
        return []
    lines: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
        for line in tail:
            if STT_LOG_PATTERNS.search(line):
                lines.append(line.rstrip("\n"))
    except OSError as e:
        return [f"<could not read log: {e}>"]
    return lines


def _models_dir() -> Path:
    return PROJECT_ROOT / "distr" / "core" / "agent" / "models"


def transcribe_wav_for_configured_engine(
    settings: dict,
    stt_cfg: dict,
    wav_path: str,
) -> tuple[str, str]:
    """Return (label, text_or_error) using file-based STT only (no mic)."""
    engine = (stt_cfg or {}).get("engine")

    if engine == "vibevoice_asr":
        from distr.core.agent.services.tts.vibevoice_asr_inference import transcribe_audio_file

        _print_vibevoice_asr_preflight()
        text = transcribe_audio_file(os.path.abspath(wav_path), max_new_tokens=1024)
        return "VibeVoice ASR (file)", (text or "").strip()

    if engine == "whisper":
        from distr.core.agent.libs import WHISPER_AVAILABLE
        from distr.core.agent.services.stt.whisper import WhisperSTTService

        if not WHISPER_AVAILABLE:
            return "Whisper.cpp", "ERROR: pywhispercpp not available"
        model_id = (stt_cfg.get("model") if isinstance(stt_cfg, dict) else None) or "base.en"
        svc = WhisperSTTService(model_path=model_id, event_queue=None, is_hands_free=False)
        try:
            text = svc.transcribe_file(os.path.abspath(wav_path)) or ""
        finally:
            svc.cleanup()
        return f"Whisper.cpp ({model_id}, file)", (text or "").strip()

    if engine == "vosk":
        from distr.core.agent.constants import DEFAULT_VOSK_MODEL_DIR
        from distr.core.agent.services.stt.vosk import VoskSTTService

        vdir = _models_dir() / DEFAULT_VOSK_MODEL_DIR
        if not vdir.is_dir():
            return "Vosk", f"ERROR: Vosk model not found at {vdir}"
        svc = VoskSTTService(model_path=str(vdir), event_queue=None, is_hands_free=False)
        text = svc.transcribe_file(os.path.abspath(wav_path)) or ""
        return "Vosk (file)", (text or "").strip()

    if engine == "assemblyai":
        if not settings.get("assemblyai_enabled") or not (settings.get("assemblyai_key") or "").strip():
            return "AssemblyAI", "ERROR: assemblyai_enabled / assemblyai_key not set in settings"
        from distr.core.agent.services.stt.assemblyai import AssemblyAISTTService

        model = (stt_cfg.get("model") if isinstance(stt_cfg, dict) else None) or "universal-2"
        svc = AssemblyAISTTService(
            api_key=(settings.get("assemblyai_key") or "").strip(),
            model=model,
            event_queue=None,
            is_hands_free=False,
        )
        text = svc.transcribe_file(os.path.abspath(wav_path)) or ""
        return f"AssemblyAI ({model}, file)", (text or "").strip()

    if engine == "openai_whisper":
        if not settings.get("openai_enabled") or not (settings.get("openai_key") or "").strip():
            return "OpenAI Whisper", "ERROR: openai_enabled / openai_key not set in settings"
        from distr.core.agent.services.stt.openai import OpenAIWhisperSTTService

        model = (stt_cfg.get("model") if isinstance(stt_cfg, dict) else None) or "whisper-1"
        svc = OpenAIWhisperSTTService(
            api_key=(settings.get("openai_key") or "").strip(),
            model=model,
            event_queue=None,
            is_hands_free=False,
        )
        text = svc.transcribe_file(os.path.abspath(wav_path)) or ""
        return f"OpenAI Whisper ({model}, file)", (text or "").strip()

    return str(engine or "unknown"), f"ERROR: no automated file transcribe for engine={engine!r}"


def run_tts_then_file_stt(settings: dict, stt_cfg: dict) -> int:
    """Synthesize WAV with configured TTS; transcribe file with configured STT (no microphone)."""
    from distr.core.audio.tts_handler import generate_tts_audio

    phrase = (
        "This is a text to speech test. It was written to a wave file by your TTS provider, "
        "not captured from the microphone."
    )
    print("\n=== TTS → file → STT roundtrip (no microphone) ===")
    print("  Step A: generate_tts_audio(...) using your Settings TTS provider / voice.")
    try:
        wav = generate_tts_audio(phrase)
    except Exception as e:
        print(f"  TTS FAILED: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1
    p = Path(wav)
    print(f"  WAV path: {p.resolve()}")
    if p.is_file():
        print(f"  WAV size: {p.stat().st_size} bytes")
    try:
        import soundfile as sf

        info = sf.info(str(p))
        print(f"  WAV format: {info.samplerate} Hz, {info.channels} ch, ~{info.duration:.2f}s")
    except Exception as ex:
        print(f"  (could not probe WAV: {ex})")

    print("  Step B: transcribe that same file with your configured STT engine (file API only).")
    label, result = transcribe_wav_for_configured_engine(settings, stt_cfg, str(p.resolve()))
    print(f"  Engine: {label}")
    print(f"  Transcription: {result!r}")
    if result.startswith("ERROR:"):
        return 1
    return 0


def run_mic_record_then_stt(settings: dict, stt_cfg: dict, seconds: float) -> int:
    """Record mono 16 kHz PCM from the default microphone, write a temp WAV, run configured file STT."""
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as e:
        print(f"\n=== Microphone STT test ===\n  ERROR: need sounddevice and numpy ({e}). pip install sounddevice")
        return 1

    sr = 16000
    frames = max(1, int(float(seconds) * sr))
    print("\n=== Microphone → WAV → STT (default input device) ===")
    print(f"  Recording {seconds:.1f}s at {sr} Hz mono — speak now…")
    try:
        audio = sd.rec(frames, samplerate=sr, channels=1, dtype="int16")
        sd.wait()
    except Exception as e:
        print(f"  RECORD FAILED: {type(e).__name__}: {e}")
        return 1

    audio = np.squeeze(audio)
    peak = int(np.max(np.abs(audio))) if audio.size else 0
    print(f"  Captured {audio.size} samples, peak amplitude {peak} (32767 = full scale)")
    if peak < 200:
        print("  WARNING: very quiet — check mic permission, input device, or mute.")

    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="mic_diag_")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(audio.astype("int16").tobytes())

        print("  Transcribing with your configured STT (same file API as after PTT capture)…")
        if (stt_cfg or {}).get("engine") == "vibevoice_asr":
            if not (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_DEVICE") or "").strip():
                print("  Hint: export DECISIONSAI_VIBEVOICE_ASR_DEVICE=cpu if MPS crashes or hangs.")

        label, result = transcribe_wav_for_configured_engine(settings, stt_cfg, wav_path)
        print(f"  Engine: {label}")
        print(f"  Transcription: {result!r}")
        if result.startswith("ERROR:"):
            return 1
        return 0
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def _engine_readiness(settings: dict, engine: str | None) -> dict[str, object]:
    out: dict[str, object] = {"engine": engine}
    if engine == "vibevoice_asr":
        try:
            from distr.core.agent.services.tts.vibevoice_runtime import vibevoice_asr_runtime_ready

            out["vibevoice_package"] = bool(vibevoice_asr_runtime_ready())
        except Exception as e:
            out["vibevoice_package"] = False
            out["vibevoice_error"] = str(e)
        try:
            from distr.core.agent.services import VibeVoiceAsrSTTService

            out["VibeVoiceAsrSTTService"] = VibeVoiceAsrSTTService is not None
        except Exception as e:
            out["VibeVoiceAsrSTTService"] = False
            out["service_import_error"] = str(e)
    elif engine == "whisper":
        try:
            from distr.core.agent.libs import WHISPER_AVAILABLE

            out["whisper_cpp"] = bool(WHISPER_AVAILABLE)
        except Exception as e:
            out["whisper_cpp"] = False
            out["whisper_error"] = str(e)
    elif engine == "vosk":
        out["vosk_note"] = "requires model under models/ (see session defaults)"
    elif engine == "assemblyai":
        out["assemblyai_key_configured"] = bool(
            settings.get("assemblyai_enabled") and (settings.get("assemblyai_key") or "").strip()
        )
    elif engine == "openai_whisper":
        out["openai_key_configured"] = bool(
            settings.get("openai_enabled") and (settings.get("openai_key") or "").strip()
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Decisions STT vs settings")
    parser.add_argument("--grep-logs", action="store_true", help="Print recent log lines matching STT/VibeVoice")
    parser.add_argument("--log-lines", type=int, default=6000, help="Tail N lines of log before grep")
    parser.add_argument("--doctor", action="store_true", help="Run transcription_doctor tool (slower)")
    parser.add_argument(
        "--transcribe-wav",
        metavar="PATH",
        help="Run VibeVoice transcribe_audio_file on this wav (loads HF models — slow)",
    )
    parser.add_argument(
        "--tts-roundtrip",
        action="store_true",
        help="Synthesize speech via Settings TTS to a WAV, then transcribe that file via Settings STT (no mic)",
    )
    parser.add_argument(
        "--transcribe-mic",
        action="store_true",
        help="Record from the default microphone to a temp WAV, then transcribe with your configured STT",
    )
    parser.add_argument(
        "--mic-seconds",
        type=float,
        default=4.0,
        help="Recording length for --transcribe-mic (default 4)",
    )
    parser.add_argument(
        "--stt-engine",
        metavar="ENGINE",
        help="Override STT engine for --transcribe-mic / --tts-roundtrip (e.g. vibevoice_asr, whisper, vosk)",
    )
    args = parser.parse_args()

    _maybe_prefetch_local_models_for_stt_cli(args)

    db_path, log_path = _find_db_and_logs()
    print("=== Paths ===")
    print(f"  settings.db: {db_path}  exists={db_path.is_file()}")
    print(f"  decisions.log: {log_path}  exists={log_path.is_file()}")

    os.chdir(PROJECT_ROOT)
    from distr.core.settings import load_settings_from_db
    from distr.core.agent.config_loader import resolve_stt_config

    settings = load_settings_from_db()
    tm = (settings.get("transcription_model") or "").strip() or "(empty)"
    stt_cfg = resolve_stt_config(tm)
    engine = stt_cfg.get("engine")

    if getattr(args, "stt_engine", None) and args.stt_engine.strip():
        ov = args.stt_engine.strip()
        stt_cfg = {"engine": ov}
        engine = ov
        print("\n=== Saved settings vs resolver ===")
        print(f"  transcription_model (DB): {tm!r}")
        print(f"  resolve_stt_config (ignored for transcribe steps): {resolve_stt_config(tm)}")
        print(f"  CLI --stt-engine override: {ov!r}")
    else:
        print("\n=== Saved settings vs resolver ===")
        print(f"  transcription_model (DB): {tm!r}")
        print(f"  resolve_stt_config -> {stt_cfg}")
    if engine is None:
        print("  WARNING: engine is None — string did not match known STT labels.")
        print("  For VibeVoice ASR use exactly e.g. 'VibeVoice ASR (Local)' in Settings → LLMs.")

    print("\n=== Readiness for resolved engine ===")
    ready = _engine_readiness(settings, engine)
    for k, v in ready.items():
        print(f"  {k}: {v}")

    if args.grep_logs:
        hits = _grep_stt_logs(log_path, args.log_lines)
        print(f"\n=== Log grep (last {args.log_lines} lines, STT/VibeVoice related) — {len(hits)} hits ===")
        if not hits:
            print("  (no matches — try increasing --log-lines or confirm log path)")
        for line in hits[-80:]:
            print(line)
        joined = "\n".join(hits)
        if "VibeVoice ASR: loading" in joined and "VibeVoice ASR: loaded" not in joined:
            print(
                "\n  NOTE: Logs show ASR load started but never logged 'VibeVoice ASR: loaded'. "
                "Usually that means the process is still downloading HF weights, is blocked inside "
                "the large model load on MPS, or exited before finishing. Try:\n"
                "    export DECISIONSAI_VIBEVOICE_ASR_DEVICE=cpu\n"
                "    # prefetch: huggingface-cli download microsoft/VibeVoice-ASR Qwen/Qwen2.5-7B\n"
                "  preprocessor_config.json warnings from upstream are often benign (defaults apply)."
            )

    if args.doctor:
        print("\n=== transcription_doctor ===")
        try:
            from distr.core.agent.tools.media.transcription_doctor import TranscriptionDoctorTool

            tool = TranscriptionDoctorTool()
            text = tool._run(check_ffmpeg=True, check_backends=True)
            # Print voice-safe head + marker presence
            ref = "\n\nREFERENCE:\n"
            if ref in text:
                head, _, tail = text.partition(ref)
                print(head.strip())
                print(f"\n... REFERENCE block length: {len(tail)} chars (see UI or full log)")
            else:
                print(text[:2000])
        except Exception as e:
            print(f"  ERROR: {e}")
            return 1

    if args.transcribe_wav:
        wav = Path(args.transcribe_wav).expanduser()
        if not wav.is_file():
            print(f"\nERROR: --transcribe-wav file not found: {wav}")
            return 1
        print(f"\n=== transcribe_audio_file({wav}) — may download weights ===")
        _print_vibevoice_asr_preflight()
        try:
            from distr.core.agent.services.tts.vibevoice_asr_inference import transcribe_audio_file

            out = transcribe_audio_file(str(wav.resolve()), max_new_tokens=512)
            print(f"  result: {out!r}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            return 1

    rc = 0
    if args.tts_roundtrip:
        rc = run_tts_then_file_stt(settings, stt_cfg)
    if args.transcribe_mic:
        mic_rc = run_mic_record_then_stt(settings, stt_cfg, args.mic_seconds)
        rc = mic_rc if mic_rc != 0 else rc

    print("\n=== Session fallback reminder ===")
    print(
        "  If vibevoice package is missing, session.py falls back to Whisper then Vosk "
        "without changing your DB label — UI may still say VibeVoice while mic uses Whisper."
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
