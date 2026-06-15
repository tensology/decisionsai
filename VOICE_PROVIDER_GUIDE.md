# Voice Provider Integration Guide

This document is the definitive reference for adding, removing, or updating a TTS (Text-to-Speech) voice provider in the DecisionsAI system.

---

## Architecture Overview

The voice provider system uses a **descriptor-based architecture** with automatic discovery. Each provider is defined in a single descriptor file that implements the `TTSProviderDescriptor` abstract base class. A central `TTSProviderRegistry` auto-discovers all descriptors at runtime and provides dynamic dispatch to every consumer in the stack — service factory, session management, TTS handler, Telegram integration, chat routes, voice cloning, and the web UI.

**Data flow:**

```
TTSProviderDescriptor (one file per provider)
    │
    ▼
TTSProviderRegistry (auto-discovers descriptors)
    ├── Web UI (general.js fetches /api/tts/providers)
    ├── Service Factory (service_factory.py → registry.get(engine).create_service())
    ├── TTS Handler (tts_handler.py → registry.get(prov).generate_audio())
    ├── Session (session.py → registry.get(engine).create_service() / get_hot_swap_config())
    ├── Telegram (events.py + manager.py → registry.get(prov).get_telegram_voice_id())
    ├── Chat Routes (chat.py → registry.provider_ids() for validation)
    ├── Voice Cloning (voice_cloning.py → registry.get(prov).clone_voice())
    └── Constants (constants.py → registry-based normalize_voice_provider())
```

**Active providers:** Kokoro (offline), ElevenLabs (online), OpenAI (online), Coqui TTS (offline), Supertonic (offline).

**Retired providers** (descriptor stubs only — not in the UI, no live TTS): F5-TTS, VoxCPM, Chatterbox. Legacy DB values still normalize to their ids; runtime falls back to Kokoro.

---

## Adding a New Voice Provider

Adding a new provider requires **one file**: a descriptor module in `distr/core/agent/services/tts/`. No other files need to be modified — the registry auto-discovers it.

### Step 1: Create the Descriptor File

Create `distr/core/agent/services/tts/<yourprovider>_descriptor.py`:

```python
"""
YourProviderDescriptor — TTSProviderDescriptor for the YourProvider TTS provider.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)


class YourProviderDescriptor(TTSProviderDescriptor):
    """Provider descriptor for YourProvider."""

    # ------------------------------------------------------------------
    # Static configuration (required properties)
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "yourprovider"  # Canonical lowercase ID

    @property
    def name(self) -> str:
        return "YourProvider (Online)"  # Human-readable display name

    @property
    def type(self) -> str:
        return "online"  # "online" or "offline"

    @property
    def enabled(self) -> bool:
        return True  # False to hide from UI

    @property
    def default_voice(self) -> str:
        return "voice_1"  # Default voice ID

    @property
    def settings_key(self) -> str:
        return "yourprovider_voice"  # DB settings key for saved voice

    @property
    def sample_rate(self) -> int:
        return 24000  # Output sample rate in Hz

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)  # (min, max) playback speed

    @property
    def supports_custom_voices(self) -> bool:
        return False  # True if voice cloning is supported

    @property
    def custom_voice_limit(self) -> int:
        return 0  # 0 = unlimited (only relevant if supports_custom_voices)

    # ------------------------------------------------------------------
    # Required methods
    # ------------------------------------------------------------------

    def create_service(self, tts_config, *, settings, stt_service, is_hands_free, models_dir):
        # Create and return a TTS service instance
        ...

    def generate_audio(self, text, voice, speed, out_file):
        # Generate TTS audio and write WAV to out_file
        ...

    def resolve_display_name(self, voice_id, settings, voice_name=None):
        # Return human-readable name for a voice ID
        ...

    def normalize_voice(self, raw_voice, settings):
        # Normalize a raw voice string into a valid voice ID
        ...

    def get_voices(self):
        # Return list of available voices: [{"id": ..., "name": ...}, ...]
        ...

    def get_hot_swap_config(self, voice_model, settings):
        # Return config dict for live voice switching
        ...

    def get_voice_settings_entry(self):
        # Return (engine, settings_key, default_voice, extra_keys_dict)
        ...

    def get_telegram_voice_id(self, settings):
        # Resolve voice ID from settings for Telegram voice notes
        ...

    def normalize_provider_name(self, raw):
        # Return self.id if raw matches this provider, else None
        ...

    # ------------------------------------------------------------------
    # Optional: Voice cloning (only if supports_custom_voices = True)
    # ------------------------------------------------------------------

    # def clone_voice(self, voice, audio_files, session):
    #     # Default raises NotImplementedError — override if needed
    #     ...


# IMPORTANT: Export the singleton descriptor instance at module level
DESCRIPTOR = YourProviderDescriptor()
```

### Step 2: That's It

The `TTSProviderRegistry` automatically discovers your descriptor on first access. No other files need changes.

---

## TTSProviderDescriptor Reference

### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `id` | `str` | Canonical lowercase provider ID (e.g. `"kokoro"`, `"elevenlabs"`) |
| `name` | `str` | Human-readable display name (e.g. `"Kokoro (Offline)"`) |
| `type` | `str` | `"online"` or `"offline"` |
| `enabled` | `bool` | Whether the provider is visible in the UI |
| `default_voice` | `str` | Default voice ID for this provider |
| `settings_key` | `str` | DB settings key for the user's chosen voice (e.g. `"kokoro_voice"`) |
| `sample_rate` | `int` | Output sample rate in Hz (e.g. `24000`, `44100`) |
| `speed_bounds` | `tuple[float, float]` | `(min, max)` playback speed bounds |
| `supports_custom_voices` | `bool` | Whether voice cloning is supported |
| `custom_voice_limit` | `int` | Max custom voices (`0` = unlimited) |

### Required Methods

| Method | Signature | Replaces |
|--------|-----------|----------|
| `create_service` | `(tts_config, *, settings, stt_service, is_hands_free, models_dir) -> Any` | `service_factory.create_tts_service()` and `session._create_services()` if/elif chains |
| `generate_audio` | `(text, voice, speed, out_file) -> None` | `tts_handler.generate_tts_audio()`, `generate_voice_sample()`, `events._telegram_generate_tts()`, `send_voice_note_to_telegram._run()` |
| `resolve_display_name` | `(voice_id, settings, voice_name=None) -> str` | `service_factory.resolve_voice_to_display_name()`, `tts_handler._resolve_display_name()` |
| `normalize_voice` | `(raw_voice, settings) -> str` | `tts_handler._normalize_voice_for_provider()` |
| `get_voices` | `() -> list[dict]` | `voices._get_voices_for_provider()` |
| `get_hot_swap_config` | `(voice_model, settings) -> dict` | `session._hot_swap_tts_service()` |
| `get_voice_settings_entry` | `() -> tuple[str, str, str, dict]` | `session._VOICE_SETTINGS` entries |
| `get_telegram_voice_id` | `(settings) -> str` | `events._telegram_resolve_voice_settings()` |
| `normalize_provider_name` | `(raw) -> Optional[str]` | `constants.normalize_voice_provider()` |

### Optional Methods

| Method | Default Behavior | When to Override |
|--------|-----------------|-----------------|
| `clone_voice(voice, audio_files, session)` | Raises `NotImplementedError` | When `supports_custom_voices = True` |

---

## Auto-Discovery Mechanism

The `TTSProviderRegistry` (in `distr/core/agent/services/tts/registry.py`) automatically discovers provider descriptors using the following process:

1. On first access (any call to `get()`, `enabled_providers()`, `all_providers()`, or `provider_ids()`), the registry scans `distr/core/agent/services/tts/` for Python modules.
2. Modules named `__init__`, `registry`, or `provider_descriptor` are skipped.
3. Each remaining module is imported and checked for a module-level `DESCRIPTOR` attribute.
4. If `DESCRIPTOR` is an instance of `TTSProviderDescriptor`, it is registered automatically.
5. Discovery runs once (lazily) and the results are cached for the lifetime of the process.

**Import the registry singleton:**

```python
from distr.core.agent.services.tts.registry import tts_registry

# Lookup by ID
descriptor = tts_registry.get("kokoro")

# All enabled providers
for d in tts_registry.enabled_providers():
    print(d.id, d.name)

# All provider IDs (enabled only)
ids = tts_registry.provider_ids()
```

---

## Removing or Disabling a Provider

**Soft disable (recommended):** Set `enabled` to `False` in your descriptor's property. The provider will be excluded from all consumer code paths automatically — no other files need changes.

**Retire fully:** Set `enabled` to `False`, delete the `*_descriptor.py` service implementation file (e.g. `kokoro.py`), and replace the descriptor with a thin stub extending `RetiredTTSProviderDescriptor` (see `f5tts_descriptor.py`, `voxcpm_descriptor.py`, `chatterbox_descriptor.py`). Keep the descriptor module so `normalize_voice_provider()` still recognizes legacy settings.

**Hard remove:** Delete the descriptor file from `distr/core/agent/services/tts/`. The registry will no longer discover it. No stale if/elif branches to clean up.

---

## Voice Cloning

Voice cloning is opt-in per provider. To enable it:

1. Set `supports_custom_voices = True` and `custom_voice_limit` in your descriptor
2. Override the `clone_voice(voice, audio_files, session)` method

The system automatically dispatches cloning requests to your descriptor's `clone_voice()` method.

**Cloning patterns by type:**

| Type | Example | How It Works |
|------|---------|-------------|
| API-based | ElevenLabs | Upload audio to provider API, get back a `voice_id`, store in `provider_voice_id` |
| Reference-based | VoxCPM, F5-TTS | Store reference audio locally, pass path at inference time (zero-shot cloning) |
| Voice conversion | Kokoro/Kanade | Synthesize with base voice, then apply voice conversion using reference audio |

---

## Web UI — No Code Changes Needed

The web UI is fully dynamic. The JavaScript in `distr/gui/web/static/settings/js/general.js`:

1. Fetches `/api/tts/providers` on page load (built from the registry)
2. Populates the provider dropdown from the response
3. Populates the voice dropdown from each provider's `voices` array
4. Shows/hides the "+ Custom" button based on `supports_custom_voices`
5. Saves the selected voice using the provider's `settings_key`

The chat UI (`chat.js`) resolves voice keys from the `/api/tts/providers` response `settings_key` field — no hardcoded ternary chains.

---

## Additional Setup (Outside the Descriptor)

While the descriptor handles all dispatch logic automatically, some providers may still need:

- **TTS Service Class**: A Pipecat `TTSService` implementation in `distr/core/agent/services/tts/<provider>.py` (the descriptor's `create_service()` instantiates it)
- **Database columns**: A `<provider>_voice` column on the `Settings` model if the provider needs persistent voice selection
- **Pydantic model field**: A corresponding field in `GeneralSettings` for the settings API
- **Voice API endpoint**: A `/voices/<provider>` route if the provider has a dynamic voice list
- **Provider-specific UI controls**: Custom HTML/JS if the provider needs settings beyond voice selection (e.g. ElevenLabs stability/similarity sliders)

These are provider infrastructure concerns, not dispatch logic — they don't involve if/elif chains and don't need to be updated when other providers change.

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
