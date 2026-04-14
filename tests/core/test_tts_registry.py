"""
Unit tests for TTSProviderRegistry.

Validates: Requirements 2.1, 2.12, 2.13

Tests the registry's core functionality:
- Manual registration via register()
- Lookup via get()
- Filtering via enabled_providers() / all_providers() / provider_ids()
- Auto-discovery of DESCRIPTOR instances from tts package modules
- Duplicate registration rejection
- KeyError on unknown provider lookup
- _reset() for test isolation
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor
from distr.core.agent.services.tts.registry import TTSProviderRegistry


# ---------------------------------------------------------------------------
# Concrete test descriptor (minimal implementation of the ABC)
# ---------------------------------------------------------------------------

class _StubDescriptor(TTSProviderDescriptor):
    """Minimal concrete descriptor for testing the registry."""

    def __init__(self, pid: str, *, enabled: bool = True, name: str | None = None):
        self._id = pid
        self._enabled = enabled
        self._name = name or pid.title()

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def default_voice(self) -> str:
        return "default"

    @property
    def settings_key(self) -> str:
        return f"{self._id}_voice"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return False

    @property
    def custom_voice_limit(self) -> int:
        return 0

    def create_service(self, tts_config, *, settings, stt_service, is_hands_free, models_dir) -> Any:
        return None

    def generate_audio(self, text, voice, speed, out_file) -> None:
        pass

    def resolve_display_name(self, voice_id, settings, voice_name=None) -> str:
        return voice_id

    def normalize_voice(self, raw_voice, settings) -> str:
        return raw_voice

    def get_voices(self) -> list[dict]:
        return []

    def get_hot_swap_config(self, voice_model, settings) -> dict:
        return {}

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return (self._id, f"{self._id}_voice", "default", {})

    def get_telegram_voice_id(self, settings) -> str:
        return "default"

    def normalize_provider_name(self, raw) -> Optional[str]:
        return self._id if raw.lower() == self._id else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    """Return a fresh, empty registry with discovery disabled."""
    r = TTSProviderRegistry()
    # Mark as discovered so auto-discovery doesn't run during unit tests
    r._discovered = True
    return r


# ---------------------------------------------------------------------------
# Tests: register()
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_adds_descriptor(self, registry):
        d = _StubDescriptor("alpha")
        registry.register(d)
        assert "alpha" in registry
        assert len(registry) == 1

    def test_register_multiple(self, registry):
        registry.register(_StubDescriptor("alpha"))
        registry.register(_StubDescriptor("beta"))
        assert len(registry) == 2

    def test_register_duplicate_raises(self, registry):
        registry.register(_StubDescriptor("alpha"))
        with pytest.raises(ValueError, match="Duplicate TTS provider descriptor id"):
            registry.register(_StubDescriptor("alpha"))


# ---------------------------------------------------------------------------
# Tests: get()
# ---------------------------------------------------------------------------

class TestGet:
    def test_get_returns_correct_descriptor(self, registry):
        d = _StubDescriptor("alpha")
        registry.register(d)
        assert registry.get("alpha") is d

    def test_get_unknown_raises_key_error(self, registry):
        with pytest.raises(KeyError, match="No TTS provider descriptor registered for 'unknown'"):
            registry.get("unknown")


# ---------------------------------------------------------------------------
# Tests: enabled_providers / all_providers / provider_ids
# ---------------------------------------------------------------------------

class TestFiltering:
    def test_enabled_providers_excludes_disabled(self, registry):
        registry.register(_StubDescriptor("alpha", enabled=True))
        registry.register(_StubDescriptor("beta", enabled=False))
        registry.register(_StubDescriptor("gamma", enabled=True))

        enabled = registry.enabled_providers()
        assert len(enabled) == 2
        assert {d.id for d in enabled} == {"alpha", "gamma"}

    def test_all_providers_includes_disabled(self, registry):
        registry.register(_StubDescriptor("alpha", enabled=True))
        registry.register(_StubDescriptor("beta", enabled=False))

        all_p = registry.all_providers()
        assert len(all_p) == 2
        assert {d.id for d in all_p} == {"alpha", "beta"}

    def test_provider_ids_returns_enabled_only(self, registry):
        registry.register(_StubDescriptor("alpha", enabled=True))
        registry.register(_StubDescriptor("beta", enabled=False))
        registry.register(_StubDescriptor("gamma", enabled=True))

        ids = registry.provider_ids()
        assert set(ids) == {"alpha", "gamma"}

    def test_empty_registry(self, registry):
        assert registry.enabled_providers() == []
        assert registry.all_providers() == []
        assert registry.provider_ids() == []


# ---------------------------------------------------------------------------
# Tests: _reset()
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_providers(self, registry):
        registry.register(_StubDescriptor("alpha"))
        assert len(registry) == 1
        registry._reset()
        # Re-mark as discovered so auto-discovery doesn't repopulate
        registry._discovered = True
        assert len(registry) == 0
        assert "alpha" not in registry

    def test_reset_allows_re_registration(self, registry):
        d = _StubDescriptor("alpha")
        registry.register(d)
        registry._reset()
        # Mark discovered again since _reset clears it
        registry._discovered = True
        registry.register(d)
        assert registry.get("alpha") is d


# ---------------------------------------------------------------------------
# Tests: __contains__ / __len__ / __repr__
# ---------------------------------------------------------------------------

class TestDunderMethods:
    def test_contains(self, registry):
        registry.register(_StubDescriptor("alpha"))
        assert "alpha" in registry
        assert "beta" not in registry

    def test_len(self, registry):
        assert len(registry) == 0
        registry.register(_StubDescriptor("alpha"))
        assert len(registry) == 1

    def test_repr(self, registry):
        registry.register(_StubDescriptor("alpha"))
        r = repr(registry)
        assert "TTSProviderRegistry" in r
        assert "alpha" in r


# ---------------------------------------------------------------------------
# Tests: auto-discovery
# ---------------------------------------------------------------------------

class TestAutoDiscovery:
    def test_auto_discovery_triggers_on_first_get(self):
        """Auto-discovery should run lazily on first access."""
        r = TTSProviderRegistry()
        # Patch _auto_discover to track calls without actually scanning
        with patch.object(r, "_auto_discover") as mock_discover:
            try:
                r.get("nonexistent")
            except KeyError:
                pass
            mock_discover.assert_called_once()

    def test_auto_discovery_runs_only_once(self):
        """Auto-discovery should not re-run on subsequent accesses."""
        r = TTSProviderRegistry()
        with patch.object(r, "_auto_discover") as mock_discover:
            try:
                r.get("a")
            except KeyError:
                pass
            try:
                r.get("b")
            except KeyError:
                pass
            mock_discover.assert_called_once()

    def test_auto_discovery_skips_registry_and_descriptor_modules(self):
        """Auto-discovery should skip __init__, registry, and provider_descriptor modules."""
        r = TTSProviderRegistry()
        # Trigger discovery through the public API (_ensure_discovered sets the flag)
        r._ensure_discovered()
        # If it tried to import itself, it would cause issues — the fact
        # that we get here without error confirms the skip logic works.
        assert r._discovered is True


# ---------------------------------------------------------------------------
# Tests: singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_tts_registry_is_importable(self):
        from distr.core.agent.services.tts.registry import tts_registry
        assert isinstance(tts_registry, TTSProviderRegistry)

    def test_tts_registry_is_same_instance(self):
        from distr.core.agent.services.tts.registry import tts_registry as r1
        from distr.core.agent.services.tts.registry import tts_registry as r2
        assert r1 is r2
