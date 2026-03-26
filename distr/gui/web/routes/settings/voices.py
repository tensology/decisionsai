"""
Voices routes — /tts/providers, /voices/*, /custom-voices/*, /elevenlabs-voices/*
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
import os

from ._shared import logger


# OpenAI TTS supported voices (single source of truth in backend)
OPENAI_TTS_VOICES = [
    {"id": "alloy", "name": "Alloy"},
    {"id": "echo", "name": "Echo"},
    {"id": "fable", "name": "Fable"},
    {"id": "onyx", "name": "Onyx"},
    {"id": "nova", "name": "Nova"},
    {"id": "shimmer", "name": "Shimmer"},
]

CUSTOM_VOICE_AUDIO_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'db', 'custom_voices'))


def register_routes(router, templates):

    async def _get_voices_for_provider(provider_id: str):
        """Fetch voice list for a provider. Includes custom voices from DB."""
        voices = []
        try:
            if provider_id == "kokoro":
                from distr.core.agent.constants import KOKORO_VOICES
                voices = [{"id": vid, "name": name} for vid, name in KOKORO_VOICES.items()]
            elif provider_id == "elevenlabs":
                from distr.core.settings import load_settings_from_db
                settings = load_settings_from_db()
                api_key = settings.get("elevenlabs_key", "").strip()
                if api_key:
                    from elevenlabs import ElevenLabs
                    client = ElevenLabs(api_key=api_key)
                    el_voices = client.voices.get_all().voices
                    voices = []
                    for v in el_voices:
                        cat = getattr(v, "category", "premade")
                        if cat == "cloned":
                            voices.append({"id": v.voice_id, "name": f"⭐ {v.name}", "custom": True, "api_cloned": True})
                        else:
                            voices.append({"id": v.voice_id, "name": v.name})
            elif provider_id == "openai":
                voices = list(OPENAI_TTS_VOICES)
            elif provider_id == "coqui":
                from distr.core.agent.constants import COQUI_VOICES
                voices = [{"id": vid, "name": name} for vid, name in COQUI_VOICES.items()]
        except Exception as e:
            logger.warning("Could not load voices for %s: %s", provider_id, e)

        # Append custom voices from DB (status=ready)
        # For ElevenLabs: API cloned voices are already marked custom above.
        # DB entries take precedence — replace API-cloned entries that match DB records.
        if provider_id in ("kokoro", "elevenlabs"):
            try:
                from distr.core.db import get_session
                from sqlalchemy import text
                session = get_session()
                try:
                    rows = session.execute(text(
                        "SELECT id, name, provider_voice_id, status FROM custom_voices "
                        "WHERE provider = :p"
                    ), {"p": provider_id}).fetchall()
                    logger.info("Custom voices for %s: %s", provider_id, [(r[0], r[1], r[2], r[3]) for r in rows])
                    custom_provider_ids = set()
                    custom_names = set()
                    for row in rows:
                        if row[3] != "ready":
                            continue
                        vid = row[2] or f"custom_{row[0]}"
                        custom_provider_ids.add(vid)
                        custom_names.add(row[1].strip().lower())
                        voices.append({"id": vid, "name": f"⭐ {row[1]}", "custom": True})
                    logger.info("Custom IDs: %s, Custom names: %s", custom_provider_ids, custom_names)
                    # Remove duplicates: drop non-DB entries whose id or name matches a DB custom voice
                    # This covers both plain API entries and api_cloned entries that have a DB counterpart
                    if custom_provider_ids or custom_names:
                        before = len(voices)
                        voices = [v for v in voices if
                            (v.get("custom") and not v.get("api_cloned")) or (
                                v["id"] not in custom_provider_ids and
                                v.get("name", "").replace("⭐ ", "").strip().lower() not in custom_names
                            )]
                        logger.info("Dedup: %d -> %d voices", before, len(voices))
                finally:
                    session.close()
            except Exception as e:
                logger.warning("Could not load custom voices for %s: %s", provider_id, e)

        return voices

    @router.get("/tts/providers")
    async def get_tts_providers():
        """Return all enabled TTS providers with their voices. Single source of truth for the UI."""
        from distr.core.agent.constants import TTS_PROVIDERS
        result = []
        for p in TTS_PROVIDERS:
            if not p["enabled"]:
                continue
            voices = await _get_voices_for_provider(p["id"])
            entry = {
                "id": p["id"],
                "name": p["name"],
                "type": p["type"],
                "default_voice": p["default_voice"],
                "settings_key": p["settings_key"],
                "voices": voices,
                "supports_custom_voices": p.get("supports_custom_voices", False),
            }
            limit = p.get("custom_voice_limit", 0)
            if limit:
                entry["custom_voice_limit"] = limit
            result.append(entry)
        return result

    @router.get("/voices/kokoro")
    async def get_kokoro_voices():
        """Return canonical Kokoro voice list plus any custom cloned voices."""
        try:
            from distr.core.agent.session import KOKORO_VOICES
            voices = [{"id": vid, "name": name} for vid, name in KOKORO_VOICES.items()]
        except Exception as e:
            logger.warning(f"Could not load KOKORO_VOICES: {e}")
            voices = [{"id": "af_heart", "name": "Heart"}]

        # Append custom voices (Kanade voice cloning)
        try:
            from distr.core.db import get_session, CustomVoice
            session = get_session()
            try:
                customs = session.query(CustomVoice).filter(
                    CustomVoice.provider == 'kokoro', CustomVoice.status == 'ready'
                ).all()
                for cv in customs:
                    voices.append({
                        "id": f"custom_{cv.id}",
                        "name": f"⭐ {cv.name}",
                        "custom": True,
                    })
            finally:
                session.close()
        except Exception as e:
            logger.debug("Could not load Kokoro custom voices: %s", e)

        return voices

    @router.get("/voices/elevenlabs")
    async def get_elevenlabs_voices():
        """Return ElevenLabs voices from API using saved key (dynamic)."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            api_key = settings.get("elevenlabs_key", "").strip()
            if not api_key:
                return []
            from elevenlabs import ElevenLabs
            client = ElevenLabs(api_key=api_key)
            voices = client.voices.get_all().voices
            result = []
            for v in voices:
                cat = getattr(v, "category", "premade")
                if cat == "cloned":
                    result.append({"id": v.voice_id, "name": f"⭐ {v.name}", "custom": True, "api_cloned": True})
                else:
                    result.append({"id": v.voice_id, "name": v.name})
            return result
        except Exception as e:
            logger.warning(f"Could not load ElevenLabs voices: {e}")
            return []

    @router.get("/voices/openai")
    async def get_openai_voices():
        """Return OpenAI TTS voice list (backend-defined)."""
        return OPENAI_TTS_VOICES

    @router.get("/voices/coqui")
    async def get_coqui_voices():
        """Return Coqui VCTK voice list (from coqui-ai-voices.json via constants)."""
        try:
            from distr.core.agent.constants import COQUI_VOICES
            return [{"id": vid, "name": name} for vid, name in COQUI_VOICES.items()]
        except Exception as e:
            logger.warning("Could not load COQUI_VOICES: %s", e)
            return [{"id": "p225", "name": "Sarah"}]

    # ── Custom Voices CRUD ──────────────────────────────────────────────

    @router.get("/custom-voices")
    async def list_custom_voices(provider: str = None):
        """List custom voices, optionally filtered by provider."""
        from distr.core.db import get_session, CustomVoice
        session = get_session()
        try:
            q = session.query(CustomVoice)
            if provider:
                q = q.filter(CustomVoice.provider == provider)
            voices = q.order_by(CustomVoice.created_date.desc()).all()
            return [
                {
                    "id": v.id,
                    "name": v.name,
                    "provider": v.provider,
                    "system_prompt": v.system_prompt or "",
                    "personality": v.personality or "",
                    "status": v.status,
                    "provider_voice_id": v.provider_voice_id or "",
                    "error_message": v.error_message or "",
                }
                for v in voices
            ]
        finally:
            session.close()

    @router.post("/custom-voices")
    async def create_custom_voice(request: Request):
        """Create a custom voice. Accepts multipart form: name, provider, system_prompt, audio files."""
        from distr.core.db import get_session, CustomVoice
        form = await request.form()
        name = (form.get("name") or "").strip()
        provider = (form.get("provider") or "").strip().lower()
        system_prompt = (form.get("system_prompt") or "").strip()
        personality = (form.get("personality") or "").strip()
        gender = (form.get("gender") or "female").strip().lower()
        if gender not in ("male", "female"):
            gender = "female"

        if not name:
            return JSONResponse({"error": "Name is required"}, status_code=400)
        if provider not in ("elevenlabs", "kokoro"):
            return JSONResponse({"error": "Provider must be elevenlabs or kokoro"}, status_code=400)

        # Check ElevenLabs limit (max 5 custom voices)
        session = get_session()
        try:
            if provider == "elevenlabs":
                count = session.query(CustomVoice).filter(
                    CustomVoice.provider == "elevenlabs",
                    CustomVoice.status != "failed",
                ).count()
                if count >= 5:
                    return JSONResponse({"error": "ElevenLabs allows a maximum of 5 custom voices"}, status_code=400)

            # Create DB record
            voice = CustomVoice(name=name, provider=provider, system_prompt=system_prompt, personality=personality, gender=gender, status="pending")
            session.add(voice)
            session.commit()
            voice_id = voice.id

            # Save uploaded audio files
            audio_dir = os.path.join(CUSTOM_VOICE_AUDIO_DIR, str(voice_id))
            os.makedirs(audio_dir, exist_ok=True)
            voice.audio_dir = audio_dir

            saved_files = []
            for key in form:
                item = form[key]
                if hasattr(item, 'filename') and item.filename:
                    # It's an uploaded file
                    safe_name = os.path.basename(item.filename)
                    dest = os.path.join(audio_dir, safe_name)
                    content = await item.read()
                    with open(dest, 'wb') as f:
                        f.write(content)
                    saved_files.append(dest)

            if not saved_files:
                voice.status = "failed"
                voice.error_message = "At least one audio file is required"
                session.commit()
                return JSONResponse({"error": "At least one audio file is required"}, status_code=400)

            session.commit()
        finally:
            session.close()

        # Kick off async processing in background
        import threading
        threading.Thread(
            target=_process_voice_bg,
            args=(voice_id,),
            daemon=True,
        ).start()

        return JSONResponse({"id": voice_id, "status": "processing", "name": name})

    @router.delete("/custom-voices/{voice_id}")
    async def delete_custom_voice(voice_id: int):
        """Delete a custom voice and its audio files."""
        from distr.core.db import get_session, CustomVoice
        import shutil
        session = get_session()
        try:
            voice = session.query(CustomVoice).filter(CustomVoice.id == voice_id).first()
            if not voice:
                return JSONResponse({"error": "Not found"}, status_code=404)

            # Delete ElevenLabs voice from their API if applicable
            if voice.provider == "elevenlabs" and voice.provider_voice_id:
                try:
                    from distr.core.settings import load_settings_from_db
                    settings = load_settings_from_db()
                    api_key = (settings.get("elevenlabs_key") or "").strip()
                    if api_key:
                        from elevenlabs import ElevenLabs
                        client = ElevenLabs(api_key=api_key)
                        client.voices.delete(voice.provider_voice_id)
                except Exception as e:
                    logger.warning("Could not delete ElevenLabs voice %s: %s", voice.provider_voice_id, e)

            # Delete audio files
            if voice.audio_dir and os.path.isdir(voice.audio_dir):
                shutil.rmtree(voice.audio_dir, ignore_errors=True)

            session.delete(voice)
            session.commit()
            return JSONResponse({"success": True})
        finally:
            session.close()

    @router.delete("/elevenlabs-voices/{voice_id}")
    async def delete_elevenlabs_voice_by_provider_id(voice_id: str):
        """Delete a cloned ElevenLabs voice directly via the API (not in local DB)."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            api_key = (settings.get("elevenlabs_key") or "").strip()
            if not api_key:
                return JSONResponse({"error": "No ElevenLabs API key configured"}, status_code=400)
            from elevenlabs import ElevenLabs
            client = ElevenLabs(api_key=api_key)
            client.voices.delete(voice_id)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.warning("Could not delete ElevenLabs voice %s: %s", voice_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/custom-voices/{voice_id}/status")
    async def get_custom_voice_status(voice_id: int):
        """Poll processing status of a custom voice."""
        from distr.core.db import get_session, CustomVoice
        session = get_session()
        try:
            voice = session.query(CustomVoice).filter(CustomVoice.id == voice_id).first()
            if not voice:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return {"id": voice.id, "status": voice.status, "error_message": voice.error_message or ""}
        finally:
            session.close()

    @router.patch("/custom-voices/{voice_id}")
    async def update_custom_voice(voice_id: int, request: Request):
        """Update a custom voice (currently only personality is editable)."""
        from distr.core.db import get_session, CustomVoice
        from datetime import datetime
        session = get_session()
        try:
            voice = session.query(CustomVoice).filter(CustomVoice.id == voice_id).first()
            if not voice:
                return JSONResponse({"error": "Not found"}, status_code=404)
            body = await request.json()
            if "personality" in body:
                voice.personality = (body["personality"] or "").strip()
            voice.modified_date = datetime.utcnow()
            session.commit()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Failed to update custom voice %s: %s", voice_id, e, exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            session.close()

    @router.post("/custom-voices/transcribe")
    async def transcribe_audio_for_custom_voice(request: Request):
        """Transcribe an uploaded audio file and return the text. Used by the custom voice modal."""
        form = await request.form()
        audio_file = None
        for key in form:
            item = form[key]
            if hasattr(item, 'filename') and item.filename:
                audio_file = item
                break
        if not audio_file:
            return JSONResponse({"error": "No audio file provided"}, status_code=400)

        import tempfile
        content = await audio_file.read()
        suffix = os.path.splitext(audio_file.filename or "audio.wav")[1] or ".wav"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(content)
            tmp.close()
            transcript = await _transcribe_audio(tmp.name)
            return {"transcript": transcript}
        except Exception as e:
            logger.error("Transcription failed: %s", e, exc_info=True)
            return JSONResponse({"error": str(e)[:200]}, status_code=500)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    async def _transcribe_audio(file_path: str) -> str:
        """Delegate to voice_cloning service."""
        from distr.core.audio.voice_cloning import transcribe_audio_file
        return await transcribe_audio_file(file_path)

    def _process_voice_bg(voice_id: int):
        """Delegate to voice_cloning service."""
        from distr.core.audio.voice_cloning import process_custom_voice
        process_custom_voice(voice_id)
