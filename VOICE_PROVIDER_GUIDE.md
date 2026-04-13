# Voice Provider Integration Guide

This document is the definitive reference for adding, removing, or updating a TTS (Text-to-Speech) voice provider in the DecisionsAI system. It covers every touch point — from the backend registry and service implementation to the web UI, chat pipeline, Telegram voice notes, and voice cloning.

---

## Architecture Overview

The voice provider system follows a registry-driven pattern. A single source of truth — `TTS_PROVIDERS` in `constants.py` — drives the entire stack. The UI, service factory, TTS handler, Telegram integration, and chat routes all read from this registry or use the canonical provider IDs it defines.

**Data flow:**

```
TTS_PROVIDERS (constants.py)
    ├── Web UI (general.js fetches /api/tts/providers)
    ├── Service Factory (service_factory.py creates TTS service instances)
    ├── TTS Handler (tts_handler.py generates audio for previews & chat)
    ├── Telegram (events.py + manager.py generate voice notes)
    ├── Chat Routes (chat.py validates provider on chat creation/update)
    └── Voice Cloning (voice_cloning.py processes custom voices)
```

**Current providers:** Kokoro (offline), ElevenLabs (online), OpenAI (online), Coqui TTS (offline, disabled), F5-TTS (offline, disabled), VoxCPM (offline).

---

## Touch Point Checklist

When adding or removing a provider, you must update ALL of the following. Each section below explains the details.

| # | Touch Point | File(s) | Action |
|---|-------------|---------|--------|
| 1 | Provider Registry | `distr/core/agent/constants.py` | Add/remove entry in `TTS_PROVIDERS` |
| 2 | Provider Normalization | `distr/core/agent/constants.py` | Update `normalize_voice_provider()` |
| 3 | Sample Rates | `distr/core/agent/constants.py` | Add/remove `SAMPLE_RATE_*` and `TTS_SAMPLE_RATES` entry |
| 4 | Speed Bounds | `distr/core/agent/constants.py` | Add/remove `SPEED_BOUNDS` entry |
| 5 | Voice List (if static) | `distr/core/agent/constants.py` | Add voice dict (like `KOKORO_VOICES`) |
| 6 | TTS Service Class | `distr/core/agent/services/tts/<provider>.py` | Create/remove service class extending `TTSService` |
| 7 | Services `__init__.py` | `distr/core/agent/services/__init__.py` | Import/export the new service class |
| 8 | TTS Services `__init__.py` | `distr/core/agent/services/tts/__init__.py` | Import/export the new service class |
| 9 | Service Factory — Import | `distr/core/agent/service_factory.py` | Import the service class |
| 10 | Service Factory — `create_tts_service()` | `distr/core/agent/service_factory.py` | Add/remove `elif engine == '<id>':` block |
| 11 | Service Factory — `resolve_voice_to_display_name()` | `distr/core/agent/service_factory.py` | Add/remove provider display name resolution |
| 12 | Service Factory — `resolve_agent_name_from_tts_config()` | `distr/core/agent/service_factory.py` | Add/remove `if engine == '<id>':` block |
| 13 | Session — `_VOICE_SETTINGS` | `distr/core/agent/session.py` | Add/remove entry in `_VOICE_SETTINGS` dict |
| 14 | Session — `_hot_swap_tts_service()` | `distr/core/agent/session.py` | Add/remove hot-swap `elif vp == '<id>':` block |
| 14b | Session — `_create_services()` | `distr/core/agent/session.py` | Add/remove `elif tts_config['engine'] == '<id>':` block in the TTS creation chain (this is the STARTUP path — separate from service_factory) |
| 15 | TTS Handler — `_tts_provider_to_internal()` | `distr/core/audio/tts_handler.py` | Add/remove mapping |
| 16 | TTS Handler — `_normalize_voice_for_provider()` | `distr/core/audio/tts_handler.py` | Add/remove provider voice validation |
| 17 | TTS Handler — `generate_tts_audio()` | `distr/core/audio/tts_handler.py` | Add/remove voice default resolution + generation call |
| 18 | TTS Handler — `generate_voice_sample()` | `distr/core/audio/tts_handler.py` | Add/remove provider generation call |
| 19 | TTS Handler — `_resolve_display_name()` | `distr/core/audio/tts_handler.py` | Add/remove display name resolution |
| 20 | TTS Handler — `_generate_<provider>()` | `distr/core/audio/tts_handler.py` | Create/remove generation function |
| 21 | Database — Settings Model | `distr/core/db/__init__.py` | Add/remove `<provider>_voice` column on `Settings` |
| 22 | Database — Provider-specific columns | `distr/core/db/__init__.py` | Add any provider-specific settings columns |
| 23 | Pydantic Model — GeneralSettings | `distr/gui/web/routes/settings/_shared.py` | Add/remove `<provider>_voice` field |
| 24 | API Route — Voice List | `distr/gui/web/routes/settings/voices.py` | Add/remove `/voices/<provider>` endpoint |
| 25 | API Route — `_get_voices_for_provider()` | `distr/gui/web/routes/settings/voices.py` | Add/remove `elif provider_id == '<id>':` block |
| 26 | API Route — General Settings GET | `distr/gui/web/routes/settings/general.py` | Add/remove `<provider>_voice` in response dict |
| 27 | Chat Route — Validation | `distr/gui/web/routes/chat.py` | Add provider ID to `valid_voice_providers` lists |
| 28 | Telegram — Voice Resolution | `distr/app/events.py` (`_telegram_resolve_voice_settings`) | Add/remove `elif '<provider>' in vp_lower:` |
| 29 | Telegram — TTS Generation | `distr/app/events.py` (`_telegram_generate_tts`) | Add/remove `elif '<provider>' in tts_lower:` |
| 30 | Telegram — Manager Agent Name | `distr/core/integrations/telegram/manager.py` | Add provider to `voice_keys` dict |
| 31 | Telegram — Voice Note Tool | `distr/core/agent/tools/integrations/send_voice_note_to_telegram.py` | Add/remove provider TTS generation |
| 32 | Voice Cloning (if supported) | `distr/core/audio/voice_cloning.py` | Add/remove `_clone_<provider>()` function |
| 33 | Custom Voice Route Validation | `distr/gui/web/routes/settings/voices.py` | Add provider to allowed list in `create_custom_voice()` |
| 34 | Settings Service | `distr/core/services/settings_service.py` | Add provider-specific cache clearing if needed |
| 35 | API Docs | `distr/gui/web/routes/docs.py` | Update API documentation |

---

## Detailed Instructions Per Touch Point

### 1. Provider Registry (`distr/core/agent/constants.py`)

Add an entry to the `TTS_PROVIDERS` list. This is the single source of truth — the UI reads it via `/api/tts/providers`.

```python
TTS_PROVIDERS = [
    # ... existing providers ...
    {
        "id": "newprovider",              # Internal key — lowercase, no spaces
        "name": "NewProvider (Online)",    # Human-readable label for UI dropdown
        "type": "online",                 # "online" or "offline"
        "enabled": True,                  # False to hide from UI
        "default_voice": "voice_1",       # Default voice ID
        "settings_key": "newprovider_voice",  # DB column name for saved voice
        "supports_custom_voices": False,  # True if voice cloning is supported
        "custom_voice_limit": 0,          # 0 = unlimited, >0 = max custom voices
    },
]
```

Also add display name constant:
```python
TTS_NEWPROVIDER = "NewProvider (Online)"
```

### 2. Provider Normalization (`distr/core/agent/constants.py`)

Update `normalize_voice_provider()` to recognize the new provider's various name forms:

```python
def normalize_voice_provider(raw: str) -> str:
    v = (raw or '').strip().lower()
    # ... existing checks ...
    if 'newprovider' in v:
        return 'newprovider'
    return v or 'kokoro'
```

### 3. Sample Rates (`distr/core/agent/constants.py`)

Add the output sample rate constant and register it:

```python
SAMPLE_RATE_NEWPROVIDER = 24000  # or whatever the provider outputs

TTS_SAMPLE_RATES = {
    # ... existing entries ...
    'newprovider': SAMPLE_RATE_NEWPROVIDER,
}
```

### 4. Speed Bounds (`distr/core/agent/constants.py`)

Add playback speed limits:

```python
SPEED_BOUNDS = {
    # ... existing entries ...
    "newprovider": (0.5, 2.0),  # (min, max) — provider-specific limits
}
```

### 5. Voice List (`distr/core/agent/constants.py`)

If the provider has a static voice list (like Kokoro), define it:

```python
NEWPROVIDER_VOICES = {
    "voice_1": "Alice",
    "voice_2": "Bob",
}
```

For API-driven providers (like ElevenLabs), voices are fetched dynamically — skip this step.

### 6. TTS Service Class (`distr/core/agent/services/tts/<provider>.py`)

Create a new file implementing the Pipecat `TTSService` interface. The service must:

- Extend `TTSService` from `distr.core.agent.libs`
- Implement `process_frame()` to handle the Pipecat frame pipeline
- Handle these frame types: `TextFrame`, `LLMFullResponseStartFrame`, `LLMFullResponseEndFrame`, `StartFrame`, `EndFrame`, `CancelFrame`, `InterruptionFrame`, `UserStartedSpeakingFrame`, `UserStoppedSpeakingFrame`
- Emit `TTSStartedFrame` / `TTSStoppedFrame` and `OutputAudioRawFrame`
- Implement `set_hands_free()`, `set_ptt_active()`, `set_playback_speed()`, `set_speech_volume()`
- Buffer text into complete sentences before synthesizing
- Support the `event_queue` for sending events back to the main process
- Handle Telegram voice note generation via `_current_telegram_request` flag

Use an existing service (e.g., `openai.py`) as a template. Key constructor parameters:

```python
class NewProviderTTSService(TTSService):
    def __init__(
        self,
        api_key: str = None,          # If online provider
        voice_id: str = "voice_1",
        voice_name: str = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        **kwargs,
    ):
```

### 7–8. Services Package Imports

**`distr/core/agent/services/__init__.py`** — Add import with try/except:

```python
try:
    from .tts.newprovider import NewProviderTTSService
except ImportError:
    NewProviderTTSService = None

# In __all__:
if NewProviderTTSService:
    __all__.append("NewProviderTTSService")
```

**`distr/core/agent/services/tts/__init__.py`** — Add import (if you want it re-exported from the tts subpackage).

### 9–12. Service Factory (`distr/core/agent/service_factory.py`)

**Import:**
```python
try:
    from .services import NewProviderTTSService
except ImportError:
    NewProviderTTSService = None
```

**`create_tts_service()`** — Add an `elif engine == 'newprovider':` block:

```python
elif engine == 'newprovider':
    if not NewProviderTTSService:
        raise ImportError("NewProviderTTSService is not available")
    api_key = tts_config.get('api_key', '')
    voice_id = tts_config.get('voice_id', 'voice_1')
    lo, hi = SPEED_BOUNDS['newprovider']
    playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))
    service = NewProviderTTSService(
        api_key=api_key,
        voice_id=voice_id,
        voice_name=voice_id,
        stt_service=stt_service,
        playback_speed=playback_speed,
        event_queue=settings.get('_event_queue'),
        speech_volume=100,
    )
```

**`resolve_voice_to_display_name()`** — Add resolution logic:

```python
if 'newprovider' in vp:
    return vm.capitalize() if vm else "NewProvider"
```

**`resolve_agent_name_from_tts_config()`** — Add:

```python
if engine == 'newprovider':
    vm = tts_config.get('voice_id', 'voice_1')
    return resolve_voice_to_display_name('newprovider', vm, settings)
```

### 13–14. Session (`distr/core/agent/session.py`)

**`_VOICE_SETTINGS` dict** (in `_load_config`):

```python
_VOICE_SETTINGS = {
    # ... existing entries ...
    'newprovider': ('newprovider', 'newprovider_voice', 'voice_1', {'api_key': 'newprovider_key'}),
}
```

The tuple format is: `(engine_name, settings_db_key, default_voice, {extra_config_keys})`.

**`_hot_swap_tts_service()`** — Add an `elif vp == 'newprovider':` block for live voice switching:

```python
elif vp == 'newprovider':
    self.config['tts']['engine'] = 'newprovider'
    self.config['tts']['voice_id'] = voice_model or 'voice_1'
    self.config['tts']['api_key'] = (self.settings.get('newprovider_key') or '').strip()
```

**`_create_services()`** — Add an `elif tts_config['engine'] == 'newprovider':` block in the TTS creation if/elif chain. This is the STARTUP path used when the agent session first initializes — it's separate from `service_factory.create_tts_service()` and MUST be updated independently:

```python
elif tts_config['engine'] == 'newprovider':
    from .services.tts.newprovider import NewProviderTTSService
    voice_name = tts_config.get('voice_name', 'default')
    playback_speed = self.settings.get('playback_speed', 1.0)
    self.tts_service = NewProviderTTSService(
        voice_name=voice_name,
        stt_service=self.stt_service,
        playback_speed=playback_speed,
        event_queue=self.event_queue,
        speech_volume=100,
    )
    self.tts_service.set_hands_free(self.is_hands_free)
    if hasattr(self, 'llm_service') and self.llm_service:
        self.llm_service.set_tts_service(self.tts_service)
```

**CRITICAL**: This is the most commonly missed touch point. The `_create_services()` method has its own if/elif chain for TTS engines that is completely separate from `service_factory.create_tts_service()`. If you add a provider to the factory but not here, the app will crash on startup with `ValueError: Unsupported TTS engine`.

### 15–20. TTS Handler (`distr/core/audio/tts_handler.py`)

This file handles audio generation for web previews, chat playback, and Telegram.

**`_tts_provider_to_internal()`:**
```python
if p in ("newprovider", "newprovider (online)"):
    return "newprovider"
```

**`_normalize_voice_for_provider()`:**
```python
if prov == "newprovider":
    # Validate voice ID against known voices or pass through
    return raw if raw else "voice_1"
```

**`generate_tts_audio()`** — Add voice default resolution:
```python
elif prov == "newprovider":
    voice = (settings.get("newprovider_voice") or "voice_1").strip()
```

And add generation call:
```python
elif prov == "newprovider":
    _generate_newprovider(text, voice, speed, out_file)
```

**`generate_voice_sample()`** — Add:
```python
elif provider == 'newprovider':
    _generate_newprovider(test_text, voice, speed, out_file)
```

**`_resolve_display_name()`** — Add:
```python
if provider == 'newprovider':
    return voice.capitalize() if voice else "NewProvider"
```

**Create `_generate_newprovider()`:**
```python
def _generate_newprovider(text: str, voice: str, speed: float, out_file: str):
    """Generate NewProvider voice sample to WAV file."""
    import numpy as np
    # ... provider-specific synthesis logic ...
    # Must write a WAV file to out_file at 48kHz (resample if needed)
    audio, sample_rate = _resample_audio(audio, src_rate, 48000)
    sf.write(out_file, audio, sample_rate)
    logger.info("Wrote NewProvider sample to %s", out_file)
```

### 21–22. Database (`distr/core/db/__init__.py`)

Add a column to the `Settings` model:

```python
class Settings(Base):
    # ... existing columns ...
    newprovider_voice = Column(String, default='voice_1')
    # If the provider needs an API key:
    # newprovider_key is typically stored alongside other keys
```

If the provider has provider-specific settings (like ElevenLabs' stability/similarity), add those columns too.

**Important:** After adding columns, the app's auto-migration logic will handle schema updates on next startup. If you need a manual migration, use Alembic or the app's built-in migration system.

### 23. Pydantic Model (`distr/gui/web/routes/settings/_shared.py`)

Add the voice field to `GeneralSettings`:

```python
class GeneralSettings(BaseModel):
    # ... existing fields ...
    newprovider_voice: str = "voice_1"
```

### 24–25. Voice API Routes (`distr/gui/web/routes/settings/voices.py`)

**Add a `/voices/<provider>` endpoint:**

```python
@router.get("/voices/newprovider")
async def get_newprovider_voices():
    """Return NewProvider voice list."""
    # For static voices:
    from distr.core.agent.constants import NEWPROVIDER_VOICES
    return [{"id": vid, "name": name} for vid, name in NEWPROVIDER_VOICES.items()]
    # For API-driven voices:
    # Fetch from provider API using saved API key
```

**Update `_get_voices_for_provider()`:**

```python
elif provider_id == "newprovider":
    from distr.core.agent.constants import NEWPROVIDER_VOICES
    voices = [{"id": vid, "name": name} for vid, name in NEWPROVIDER_VOICES.items()]
```

If the provider supports custom voices, add the custom voice DB query block (same pattern as kokoro/f5tts).

### 26. General Settings GET (`distr/gui/web/routes/settings/general.py`)

Add the voice setting to the response:

```python
return JSONResponse({
    # ... existing fields ...
    "newprovider_voice": settings.get("newprovider_voice", "voice_1"),
})
```

### 27. Chat Route Validation (`distr/gui/web/routes/chat.py`)

Add the provider ID to both `valid_voice_providers` lists (there are two — one in chat creation, one in chat update):

```python
valid_voice_providers = ["kokoro", "openai", "elevenlabs", "f5tts", "newprovider", ""]
```

### 28–29. Telegram Voice Notes (`distr/app/events.py`)

**`_telegram_resolve_voice_settings()`** — Add voice resolution:

```python
elif "newprovider" in vp_lower:
    voice_id = settings.get('newprovider_voice', 'voice_1')
```

**`_telegram_generate_tts()`** — Add TTS generation:

```python
elif 'newprovider' in tts_lower:
    # Generate audio using the provider's API/library
    # Write to temp file, return path
    from distr.core.audio.tts_handler import _generate_newprovider
    audio_file = temp_dir / f"telegram_tts_{timestamp}.wav"
    _generate_newprovider(text, voice_id or 'voice_1', 1.0, str(audio_file))
    return audio_file
```

### 30. Telegram Manager (`distr/core/integrations/telegram/manager.py`)

Update the `voice_keys` dict in `_get_agent_name()`:

```python
voice_keys = {
    "kokoro": "kokoro_voice",
    "elevenlabs": "elevenlabs_voice",
    "openai": "openai_voice",
    "coqui": "coqui_voice",
    "newprovider": "newprovider_voice",
}
```

### 31. Voice Note Tool (`distr/core/agent/tools/integrations/send_voice_note_to_telegram.py`)

Add provider handling in the `_run()` method:

```python
elif 'newprovider' in tts_lower:
    # Generate TTS audio for the voice note
    # Follow the same pattern as kokoro/openai/elevenlabs blocks
```

### 32. Voice Cloning (`distr/core/audio/voice_cloning.py`)

If the provider supports voice cloning, add a `_clone_newprovider()` function:

```python
def _clone_newprovider(voice, audio_files, session) -> None:
    """Clone voice via NewProvider's cloning API/mechanism."""
    # Process audio files, call provider API, update DB record
    voice.provider_voice_id = f"custom_{voice.id}"  # or API-returned ID
    voice.status = "ready"
    session.commit()
```

And add the dispatch in `process_custom_voice()`:

```python
elif voice.provider == "newprovider":
    _clone_newprovider(voice, audio_files, session)
```

### 33. Custom Voice Route Validation (`distr/gui/web/routes/settings/voices.py`)

In `create_custom_voice()`, add the provider to the allowed list:

```python
if provider not in ("elevenlabs", "kokoro", "f5tts", "newprovider"):
    return JSONResponse({"error": "Provider must be ..."}, status_code=400)
```

### 34. Settings Service (`distr/core/services/settings_service.py`)

If the provider needs cache clearing on settings change (like ElevenLabs), add it in `save_general_settings()`:

```python
if data.voice_provider == "newprovider":
    # Clear any cached audio files for this provider
    pass
```

### 35. API Docs (`distr/gui/web/routes/docs.py`)

Add the new voice endpoint to the documentation section.

---

## Web UI — No Code Changes Needed

The web UI is fully dynamic. The JavaScript in `distr/gui/web/static/settings/js/general.js`:

1. Fetches `/api/tts/providers` on page load
2. Populates the provider dropdown from the response
3. Populates the voice dropdown from each provider's `voices` array
4. Shows/hides the "+ Custom" button based on `supports_custom_voices`
5. Shows/hides ElevenLabs-specific sliders when `elevenlabs` is selected
6. Saves the selected voice using the provider's `settings_key`

**If your new provider needs provider-specific UI controls** (like ElevenLabs' stability/similarity sliders), you will need to:

1. Add HTML elements in `distr/gui/web/templates/settings/sections/general.html`
2. Add show/hide logic in `general.js` (toggle visibility based on provider ID)
3. Add a dedicated API endpoint for live updates (like `/api/voice/elevenlabs-settings`)
4. Add the settings service function to persist and emit signals

For providers with no special UI controls, the existing dynamic system handles everything automatically.

---

## Chat UI Voice Selection

The chat UI (`distr/gui/web/static/chat/js/chat.js`) stores `voice_provider` and `voice_model` per chat thread. When creating or updating a chat:

- `voice_provider` is the normalized provider ID (e.g., `"kokoro"`, `"newprovider"`)
- `voice_model` is the voice ID (e.g., `"af_heart"`, `"voice_1"`)

These are stored on the root `Chat` DB record and inherited by child messages. The chat route validates against `valid_voice_providers` — make sure your provider is in that list (Touch Point #27).

The chat header displays the voice provider and resolved voice name via `updateChatSettingsDisplay()`. The display name comes from `resolve_voice_to_display_name()` in the service factory.

---

## Voice Cloning Feature

Voice cloning is opt-in per provider. To enable it:

1. Set `"supports_custom_voices": True` in the `TTS_PROVIDERS` entry
2. Set `"custom_voice_limit": N` (0 = unlimited, N > 0 = max voices)
3. Implement `_clone_<provider>()` in `voice_cloning.py`
4. Add the provider to the allowed list in `create_custom_voice()` route
5. Handle custom voice resolution in `create_tts_service()` (service factory)
6. Handle custom voice resolution in `_generate_<provider>()` (TTS handler)

**Cloning patterns by type:**

| Type | Example | How It Works |
|------|---------|-------------|
| API-based | ElevenLabs | Upload audio to provider API, get back a `voice_id`, store in `provider_voice_id` |
| Reference-based | VoxCPM, F5-TTS | Store reference audio locally, pass path at inference time (zero-shot cloning) |
| Voice conversion | Kokoro/Kanade | Synthesize with base voice, then apply voice conversion using reference audio |

The `CustomVoice` DB model stores:
- `name` — display name
- `provider` — provider ID
- `audio_dir` — path to uploaded audio files
- `provider_voice_id` — API-returned ID or `custom_<db_id>`
- `status` — `pending` → `processing` → `ready` / `failed`
- `system_prompt` — transcription of reference audio (used by F5-TTS as `ref_text`)
- `personality` — agent personality text appended to LLM system prompt
- `gender` — `male` / `female` (used by Kokoro to pick base voice)

---

## Removing a Provider

To remove a provider:

1. Set `"enabled": False` in `TTS_PROVIDERS` (soft disable — keeps DB columns, hides from UI)
2. Or delete the entry entirely and remove all touch points listed above (hard remove)

Soft disable is recommended — it preserves existing user data and avoids migration issues.

---

## Environment & Dependencies

- API keys are stored in the Settings DB, not in `.env`
- Provider-specific Python packages should be imported with `try/except ImportError` guards
- The `enabled` flag in `TTS_PROVIDERS` should be set to `False` if the required package isn't available for the current Python version (e.g., Coqui doesn't support Python 3.12+)

---

## Testing Checklist

After adding a provider, verify:

- [ ] Provider appears in Settings → General → Voice Provider dropdown
- [ ] Voices load in the voice dropdown when provider is selected
- [ ] Voice preview plays correctly (play button in settings)
- [ ] Saving settings persists the provider and voice selection
- [ ] Chat creation with the new provider works
- [ ] Chat voice switching (hot-swap) works without restart
- [ ] Telegram voice notes generate with the correct provider/voice
- [ ] `send_voice_note_to_telegram` tool uses the correct provider
- [ ] Agent display name resolves correctly from the voice
- [ ] Custom voice cloning works (if supported)
- [ ] Custom voices appear with ⭐ prefix in dropdown
- [ ] Deleting custom voices works
- [ ] Voice preview for custom voices works
