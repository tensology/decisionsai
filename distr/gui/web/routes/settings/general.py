"""
General routes — /general, /voice/*, /oracle/*, /play-voice
"""
import asyncio

from fastapi.responses import JSONResponse

from distr.core.hotkeys import DEFAULTS as HOTKEY_DEFAULTS

from ._shared import (
    logger,
    GeneralSettings,
    VoiceSelectionUpdate,
    PlayVoiceRequest,
    PlaybackSpeedUpdate,
    SpeechVolumeUpdate,
    VadThresholdUpdate,
    ElevenLabsVoiceSettingsUpdate,
    OraclePositionUpdate,
    route_handler,
)

from pydantic import BaseModel, Field


# Local model for oracle size — endpoint stays in general.py
class OracleSizeUpdate(BaseModel):
    sphere_size: int = Field(ge=4, le=10)


def register_routes(router, templates):

    @router.get("/general")
    @route_handler("load general settings")
    async def get_general_settings():
        """Get current general settings"""
        from distr.core.agent.constants import normalize_voice_provider
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()

        # Prefer tts_provider (canonical id from chat create / agent) then voice_provider (may be display text)
        _raw_vp = settings.get("tts_provider") or settings.get("voice_provider") or "kokoro"
        _voice_provider_id = normalize_voice_provider(str(_raw_vp))

        return JSONResponse({
            "load_splash_sound": settings.get("load_splash_sound", False),
            "show_about": settings.get("show_about", False),
            "welcome_greet_me": settings.get("welcome_greet_me", False),
            "load_on_startup": settings.get("load_on_startup", True),
            "listening_state": settings.get("listening_state", "remember"),
            # Canonical id for UI selects (normalized). DB may store descriptor display name in tts_provider.
            "tts_provider": _voice_provider_id,
            "voice_provider": _voice_provider_id,
            # Legacy single column: active voice model id for the current provider path (see also per-provider *_voice).
            "tts_voice": settings.get("tts_voice", ""),
            "kokoro_voice": settings.get("kokoro_voice", "af_heart"),
            "elevenlabs_voice": settings.get("elevenlabs_voice", "default"),
            "openai_voice": settings.get("openai_voice", "alloy"),
            "coqui_voice": settings.get("coqui_voice", "p225"),
            "qwen3_voice": settings.get("qwen3_voice", "aiden"),
            "f5tts_voice": settings.get("f5tts_voice", "default"),
            "voxcpm_voice": settings.get("voxcpm_voice", "default"),
            "supertonic_voice": settings.get("supertonic_voice", "M1"),
            "chatterbox_voice": settings.get("chatterbox_voice", "default"),
            "playback_speed": settings.get("playback_speed", 1.0),
            "speech_volume": settings.get("speech_volume", 100),
            "vad_threshold": settings.get("vad_threshold", 50),
            "elevenlabs_stability": settings.get("elevenlabs_stability", 0.5),
            "elevenlabs_similarity_boost": settings.get("elevenlabs_similarity_boost", 0.6),
            "elevenlabs_style": settings.get("elevenlabs_style", 0.25),
            "elevenlabs_use_speaker_boost": settings.get("elevenlabs_use_speaker_boost", True),
            "restore_position": settings.get("restore_position", True),
            "oracle_position": settings.get("oracle_position", "custom"),
            "global_ptt_hotkey_enabled": settings.get("global_ptt_hotkey_enabled", True),
            "global_ptt_hotkey_combo": settings.get("global_ptt_hotkey_combo", HOTKEY_DEFAULTS["global_ptt_hotkey_combo"]),
        })

    @router.post("/general")
    @route_handler("save general settings")
    async def save_general_settings_route(settings_data: GeneralSettings):
        """Save general settings and update oracle"""
        from distr.core.services.settings_service import save_general_settings
        save_general_settings(settings_data)
        return JSONResponse({"success": True, "message": "Settings saved and oracle updated"})

    @router.post("/general/voice-selection")
    @route_handler("save voice selection from chat UI")
    async def save_voice_selection_route(body: VoiceSelectionUpdate):
        """Persist TTS provider and voice model to global settings (same as General tab)."""
        from distr.core.services.settings_service import save_voice_selection

        save_voice_selection(body.voice_provider, body.voice_model)
        return JSONResponse({"success": True, "message": "Voice selection saved"})

    @router.post("/about/show")
    @route_handler("show about window")
    async def show_about_window():
        """Show the about window and play the splash sound via the desktop app."""
        from distr.core.signals import signal_manager
        signal_manager.show_about_window.emit()
        return JSONResponse({"success": True})

    @router.post("/voice/playback-speed")
    @route_handler("update playback speed")
    async def update_playback_speed_route(data: PlaybackSpeedUpdate):
        """Update playback speed, persist to DB, and emit signal."""
        from distr.core.services.settings_service import update_playback_speed
        speed = update_playback_speed(data.playback_speed)
        return JSONResponse({"success": True, "playback_speed": speed})

    @router.post("/voice/speech-volume")
    @route_handler("update speech volume")
    async def update_speech_volume_route(data: SpeechVolumeUpdate):
        """Update speech volume, persist to DB, and emit signal."""
        from distr.core.services.settings_service import update_speech_volume
        volume = update_speech_volume(data.speech_volume)
        return JSONResponse({"success": True, "speech_volume": volume})

    @router.post("/voice/vad-threshold")
    @route_handler("update VAD threshold")
    async def update_vad_threshold_route(data: VadThresholdUpdate):
        """Update VAD threshold, persist to DB, and emit signal."""
        from distr.core.services.settings_service import update_vad_threshold
        threshold = update_vad_threshold(data.vad_threshold)
        return JSONResponse({"success": True, "vad_threshold": threshold})

    @router.post("/voice/elevenlabs-settings")
    @route_handler("update ElevenLabs voice settings")
    async def update_elevenlabs_voice_settings_route(data: ElevenLabsVoiceSettingsUpdate):
        """Update ElevenLabs voice settings, persist to DB, emit signal, clear cache."""
        from distr.core.services.settings_service import update_elevenlabs_settings
        stability, similarity_boost, style, use_speaker_boost = update_elevenlabs_settings(
            data.stability, data.similarity_boost, data.style, data.use_speaker_boost
        )
        return JSONResponse({
            "success": True,
            "elevenlabs_stability": stability,
            "elevenlabs_similarity_boost": similarity_boost,
            "elevenlabs_style": style,
            "elevenlabs_use_speaker_boost": use_speaker_boost,
        })

    @router.post("/oracle/position")
    @route_handler("update oracle position")
    async def update_oracle_position_route(data: OraclePositionUpdate):
        """Update oracle position, persist to DB, and emit signal."""
        from distr.core.services.settings_service import update_oracle_position
        pos = update_oracle_position(data.oracle_position)
        return JSONResponse({"success": True, "oracle_position": pos})

    @router.post("/oracle/size")
    @route_handler("update oracle size")
    async def update_oracle_size_route(data: OracleSizeUpdate):
        """Update oracle size, persist to DB, and emit signal."""
        from distr.core.services.settings_service import update_oracle_size
        actual_size = update_oracle_size(data.sphere_size)
        return JSONResponse({"success": True, "sphere_size": data.sphere_size, "pixels": actual_size})

    @router.post("/play-voice")
    @route_handler("generate voice sample")
    async def play_voice_endpoint(request: PlayVoiceRequest):
        """Generate a voice sample and serve it as WAV for browser playback."""
        from pathlib import Path

        from distr.core.audio.tts_handler import generate_voice_sample
        from starlette.responses import Response
        from distr.core.settings import load_settings_from_db

        from distr.core.agent.constants import normalize_voice_provider

        provider_raw = (request.provider or "").strip()
        provider = normalize_voice_provider(provider_raw)
        voice = (request.voice or "").strip()
        voice_name = (request.voice_name or "").strip() or voice

        # Defensively resolve ElevenLabs voice IDs
        if provider == "elevenlabs":
            try:
                settings = load_settings_from_db()
                api_key = (settings.get("elevenlabs_key", "") or "").strip()
                if api_key:
                    from elevenlabs import ElevenLabs
                    client = ElevenLabs(api_key=api_key)
                    available_voices = client.voices.get_all().voices or []
                    by_id = {v.voice_id: v.voice_id for v in available_voices if getattr(v, "voice_id", None)}
                    by_name = {
                        (v.name or "").strip().lower(): v.voice_id
                        for v in available_voices
                        if getattr(v, "voice_id", None) and getattr(v, "name", None)
                    }

                    requested = (voice or "").strip()
                    requested_l = requested.lower()
                    configured_voice = (settings.get("elevenlabs_voice", "") or "").strip()
                    fallback_voice = available_voices[0].voice_id if available_voices else ""

                    if requested in by_id:
                        resolved_voice = requested
                    elif requested_l and requested_l in by_name:
                        resolved_voice = by_name[requested_l]
                    elif voice_name and voice_name.lower() in by_name:
                        resolved_voice = by_name[voice_name.lower()]
                    elif configured_voice in by_id:
                        resolved_voice = configured_voice
                    else:
                        resolved_voice = fallback_voice

                    if resolved_voice and resolved_voice != voice:
                        logger.info(
                            "play-voice normalized ElevenLabs voice from '%s' to '%s'",
                            voice, resolved_voice,
                        )
                        voice = resolved_voice
            except Exception as resolve_err:
                logger.warning("play-voice ElevenLabs voice normalization failed: %s", resolve_err)

        loop = asyncio.get_event_loop()
        try:
            wav_path = await loop.run_in_executor(
                None,
                lambda: generate_voice_sample(provider, voice, request.speed, voice_name),
            )
        except ValueError as gen_err:
            return JSONResponse({"error": str(gen_err)}, status_code=400)
        logger.info(
            "play-voice served: provider_raw=%r resolved_provider=%r voice=%r speed=%s file=%s",
            request.provider,
            provider,
            voice,
            request.speed,
            wav_path,
        )

        # Read into memory so the response body always matches what was just written (avoids
        # sendfile / inode reuse edge cases with FileResponse on repeated previews).
        payload = Path(wav_path).read_bytes()
        return Response(
            content=payload,
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Content-Length": str(len(payload)),
            },
        )
