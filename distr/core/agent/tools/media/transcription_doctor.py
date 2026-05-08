"""
Transcription Doctor Tool

Preflight/health check tool for transcription backends and dependencies.
Reports which backends are available, which models are installed,
whether ffmpeg is available, and where output will be written.
"""

import logging
from typing import Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from distr.core.agent.tools.media.video_transcriber import check_transcription_backends

logger = logging.getLogger(__name__)

# Spoken first; technical checklist only after REFERENCE: (system prompt — never read aloud below REFERENCE).
_REFERENCE_MARKER = "\n\nREFERENCE:\n"


def _engine_to_plain(engine: Optional[str]) -> str:
    if engine == "vibevoice_asr":
        return "VibeVoice for speech to text"
    if engine == "whisper":
        return "Whisper on your machine for speech to text"
    if engine == "vosk":
        return "Vosk on your machine for speech to text"
    if engine == "assemblyai":
        return "Assembly A I in the cloud for speech to text"
    if engine == "openai_whisper":
        return "Open A I Whisper in the cloud for speech to text"
    if not engine:
        return "speech to text is not fully matched to a known engine in settings"
    return f"engine {engine} for speech to text"


def _build_voice_friendly_summary(
    *,
    transcription_model: str,
    resolved_engine: Optional[str],
    preflight: dict,
    ffmpeg_state: Optional[bool],
    check_backends: bool,
) -> str:
    """Two to four sentences, no markdown, no emoji, safe for TTS."""
    from distr.core.agent.services.tts.vibevoice_runtime import vibevoice_asr_runtime_ready

    want = _engine_to_plain(resolved_engine)
    vv_ready = False
    try:
        vv_ready = bool(vibevoice_asr_runtime_ready())
    except Exception:
        vv_ready = False

    lines = []
    if resolved_engine == "vibevoice_asr":
        if vv_ready:
            lines.append(
                f"In Settings you chose {transcription_model or 'VibeVoice'}. "
                "The VibeVoice package is present, so push to talk should use VibeVoice for speech to text."
            )
        else:
            lines.append(
                f"In Settings you chose {transcription_model or 'VibeVoice'}, but the VibeVoice package is not installed in this Python environment. "
                "The live microphone falls back to local Whisper or Vosk until you run the install VibeVoice script in your app virtual environment."
            )
    else:
        lines.append(
            f"Your saved speech to text choice is {transcription_model or 'not set'}, which resolves to {want}."
        )

    # None = ffmpeg check skipped (do not claim broken — the model often omits check_ffmpeg).
    if ffmpeg_state is True:
        lines.append("FFmpeg is available for audio and video transcoding.")
    elif ffmpeg_state is False:
        lines.append("FFmpeg is missing or broken; fix that before relying on file transcription.")

    if check_backends:
        backends = preflight.get("backends") or []
        ok_names = [b["name"] for b in backends if b.get("available")]
        if ok_names:
            lines.append(
                "For transcribing files and video, at least one backend is ready: "
                + ", ".join(ok_names[:5])
                + ("." if len(ok_names) <= 5 else ", and others.")
            )
        else:
            lines.append("No file transcription backend is fully ready yet; see the details on screen.")

    return " ".join(lines)


class TranscriptionDoctorInput(BaseModel):
    """Input schema for transcription doctor tool."""
    check_ffmpeg: bool = Field(default=True, description="Check if ffmpeg is available")
    check_backends: bool = Field(default=True, description="Check transcription backends")


class TranscriptionDoctorTool(BaseTool):
    """Tool for checking transcription system health and availability."""
    
    name: str = "transcription_doctor"
    description: str = (
        "Check speech-to-text health: saved Settings choice, whether VibeVoice can run, ffmpeg, and file backends.\n"
        "Returns a short voice-safe paragraph FIRST, then a line REFERENCE: and technical details below it.\n"
        "You MUST speak only the paragraph above REFERENCE to the user over TTS; never read the block below REFERENCE aloud.\n"
        "Use when the user asks which STT is active, to verify setup, or after changing speech to text in Settings.\n"
    )
    args_schema: type[BaseModel] = TranscriptionDoctorInput
    
    def _run(self, check_ffmpeg: bool = True, check_backends: bool = True, **kwargs) -> str:
        """Run transcription system health check."""
        try:
            from distr.core.settings import load_settings_from_db
            from distr.core.agent.config_loader import resolve_stt_config

            settings = load_settings_from_db()
            assemblyai_key = settings.get('assemblyai_key', '') if settings.get('assemblyai_enabled', False) else None
            openai_key = None
            if settings and settings.get('openai_enabled'):
                k = (settings.get('openai_key') or '').strip()
                openai_key = k or None

            tm = (settings.get('transcription_model') or '').strip() or '(not set)'
            stt_parsed = resolve_stt_config(tm)
            engine = stt_parsed.get('engine') or 'unknown'

            ffmpeg_state: Optional[bool] = None
            ffmpeg_lines: list[str] = []
            if check_ffmpeg:
                import subprocess
                try:
                    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
                    if result.returncode == 0:
                        ffmpeg_state = True
                        version_line = result.stdout.decode('utf-8').split('\n')[0]
                        ffmpeg_lines.append(f"✅ ffmpeg: Available\n   {version_line}\n")
                    else:
                        ffmpeg_state = False
                        ffmpeg_lines.append("❌ ffmpeg: Not working\n")
                except FileNotFoundError:
                    ffmpeg_state = False
                    ffmpeg_lines.append(
                        "❌ ffmpeg: Not found\n   Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)\n"
                    )
                except Exception as e:
                    ffmpeg_state = False
                    ffmpeg_lines.append(f"❌ ffmpeg: Check failed - {str(e)}\n")

            preflight = check_transcription_backends(assemblyai_key=assemblyai_key, openai_key=openai_key) if check_backends else {
                'backends': [],
                'has_any_backend': False,
            }

            spoken = _build_voice_friendly_summary(
                transcription_model=tm,
                resolved_engine=engine if engine != 'unknown' else None,
                preflight=preflight,
                ffmpeg_state=ffmpeg_state,
                check_backends=check_backends,
            )

            tech: list[str] = []
            tech.append("=== Live agent STT (saved settings) ===\n")
            tech.append(f"Settings value: {tm}\n")
            tech.append(f"Resolved engine id: {engine}\n\n")
            tech.append("=== Transcription System Health Check ===\n")
            tech.extend(ffmpeg_lines)

            if check_backends:
                tech.append("\n--- Transcription Backends ---\n")
                for backend in preflight['backends']:
                    status = "✅" if backend['available'] else "❌"
                    tech.append(f"{status} {backend['name']}: {backend['reason']}\n")

                if preflight['has_any_backend']:
                    tech.append("\n✅ At least one backend is available\n")
                else:
                    tech.append("\n❌ No backends available. Please install and configure at least one:\n")
                    tech.append("   - AssemblyAI: pip install assemblyai and set API key\n")
                    tech.append("   - Whisper.cpp: pywhispercpp (see requirements.txt)\n")
                    tech.append("   - OpenAI Whisper: openai + API key\n")
                    tech.append("   - Vosk: python bin/setup_vosk.py\n")
                    tech.append("   - VibeVoice ASR: ./scripts/install_vibevoice.sh\n")

            return spoken + _REFERENCE_MARKER + "".join(tech)
            
        except Exception as e:
            logger.error(f"Transcription doctor error: {e}", exc_info=True)
            return f"Error running health check: {str(e)}"
    
    async def _arun(self, check_ffmpeg: bool = True, check_backends: bool = True, **kwargs) -> str:
        """Async version of _run."""
        return self._run(check_ffmpeg, check_backends)

