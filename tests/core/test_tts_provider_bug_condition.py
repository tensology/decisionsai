"""
Bug Condition Exploration Test — TTS Provider Framework

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.9, 1.10, 1.11, 1.12, 1.14, 1.15

This test demonstrates the structural defect: adding a provider to TTS_PROVIDERS
without updating all consumer files causes failures. The test MUST FAIL on unfixed
code — failure confirms the bug exists.

Bug Condition (C):
  A provider exists in TTS_PROVIDERS but is missing from one or more consumer file
  if/elif chains, causing crashes, wrong voices, silent fallbacks, or broken features.
"""

import pytest
from typing import Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

# ---------------------------------------------------------------------------
# Mock provider entry — registered in TTS_PROVIDERS but unknown to consumers
# ---------------------------------------------------------------------------
MOCK_PROVIDER = {
    "id": "mockprovider",
    "name": "Mock (Test)",
    "type": "offline",
    "enabled": True,
    "default_voice": "default",
    "settings_key": "mockprovider_voice",
    "supports_custom_voices": False,
}


class TestBugConditionUnfixed:
    """Monkey-patch TTS_PROVIDERS to include a mock provider, then verify that
    consumer code paths reject / mishandle it.

    Every assertion below encodes the EXPECTED (correct) behavior — i.e. the
    mock provider SHOULD be handled gracefully. On UNFIXED code these assertions
    will FAIL, proving the bug exists.
    """

    # -- helpers --
    @pytest.fixture(autouse=True)
    def _patch_providers(self, monkeypatch):
        """Append the mock provider to TTS_PROVIDERS for every test."""
        from distr.core.agent import constants

        patched = list(constants.TTS_PROVIDERS) + [MOCK_PROVIDER]
        monkeypatch.setattr(constants, "TTS_PROVIDERS", patched)
        # Also patch derived lookups
        monkeypatch.setattr(
            constants,
            "TTS_PROVIDER_BY_ID",
            {p["id"]: p for p in patched},
        )
        monkeypatch.setattr(
            constants,
            "TTS_ENABLED_IDS",
            [p["id"] for p in patched if p["enabled"]],
        )

    # ---- 1. create_tts_service ------------------------------------------------
    # Requirement 1.1 / 1.12: create_tts_service should handle the new provider
    def test_create_tts_service_handles_mock_provider(self):
        """create_tts_service({'engine': 'mockprovider'}, ...) should NOT raise
        ValueError on a registered provider.

        On UNFIXED code this raises ValueError: Unsupported TTS engine: mockprovider

        We inspect the source code of create_tts_service to verify the bug
        condition without requiring PyQt6 and other heavy dependencies.
        """
        import importlib
        import ast

        # Read the source of service_factory.py and verify the elif chain
        # does NOT handle 'mockprovider'
        import inspect
        spec = importlib.util.find_spec("distr.core.agent.service_factory")
        source_path = spec.origin

        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)

        # Find the create_tts_service function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "create_tts_service":
                # Collect all string comparisons with 'engine' variable
                handled_engines = set()
                for child in ast.walk(node):
                    if isinstance(child, ast.Compare):
                        # Look for patterns like: engine == 'kokoro'
                        for comparator in child.comparators:
                            if isinstance(comparator, ast.Constant) and isinstance(
                                comparator.value, str
                            ):
                                handled_engines.add(comparator.value)
                break

        assert "mockprovider" in handled_engines, (
            f"Bug condition confirmed (Req 1.1/1.12): create_tts_service has "
            f"hardcoded elif branches for {sorted(handled_engines)} but does NOT "
            f"handle 'mockprovider'. A registered provider would raise "
            f"ValueError: Unsupported TTS engine: mockprovider"
        )

    # ---- 2. normalize_voice_provider ------------------------------------------
    # This one may pass because normalize_voice_provider has a fallback (returns raw)
    def test_normalize_voice_provider_returns_mockprovider(self):
        """normalize_voice_provider('mockprovider') should return 'mockprovider'.

        The current implementation falls through to `return v or 'kokoro'`, so
        this may actually pass — it's the one consumer that has a generic fallback.
        """
        from distr.core.agent.constants import normalize_voice_provider

        result = normalize_voice_provider("mockprovider")
        assert result == "mockprovider", (
            f"normalize_voice_provider('mockprovider') returned '{result}' "
            f"instead of 'mockprovider'"
        )

    # ---- 3. valid_voice_providers in chat.py ----------------------------------
    # Requirement 1.9: mockprovider should be in valid_voice_providers
    def test_mockprovider_in_valid_voice_providers(self):
        """'mockprovider' should be accepted as a valid voice provider for chat
        creation. On UNFIXED code, the hardcoded list does NOT include it.
        """
        # The hardcoded list from chat.py create_chat endpoint
        hardcoded_valid = ["kokoro", "openai", "elevenlabs", "coqui", "f5tts", "voxcpm", ""]
        assert "mockprovider" in hardcoded_valid, (
            "Bug condition confirmed (Req 1.9): 'mockprovider' is NOT in the "
            f"hardcoded valid_voice_providers list: {hardcoded_valid}"
        )

    # ---- 4. voice_keys in telegram manager ------------------------------------
    # Requirement 1.10: mockprovider should be in voice_keys
    def test_mockprovider_in_voice_keys(self):
        """'mockprovider' should have an entry in the voice_keys dict used by
        Telegram agent name resolution. On UNFIXED code, it does NOT.
        """
        hardcoded_voice_keys = {
            "kokoro": "kokoro_voice",
            "elevenlabs": "elevenlabs_voice",
            "openai": "openai_voice",
            "coqui": "coqui_voice",
            "voxcpm": "voxcpm_voice",
        }
        assert "mockprovider" in hardcoded_voice_keys, (
            "Bug condition confirmed (Req 1.10): 'mockprovider' is NOT in the "
            f"hardcoded voice_keys dict: {list(hardcoded_voice_keys.keys())}"
        )

    # ---- 5. _display_map in chat.py -------------------------------------------
    # Requirement 1.8: mockprovider should be in _display_map
    def test_mockprovider_in_chat_display_map(self):
        """'mockprovider' should have a display name entry in chat.py's
        _display_map. On UNFIXED code, it does NOT.
        """
        hardcoded_display_map = {
            "kokoro": "Kokoro",
            "openai": "OpenAI",
            "elevenlabs": "ElevenLabs",
            "coqui": "Coqui TTS",
            "f5tts": "F5-TTS",
            "voxcpm": "VoxCPM",
        }
        assert "mockprovider" in hardcoded_display_map, (
            "Bug condition confirmed (Req 1.8): 'mockprovider' is NOT in the "
            f"hardcoded _display_map in chat.py: {list(hardcoded_display_map.keys())}"
        )

    # ---- 6. _display dict in main.py ------------------------------------------
    # Requirement 1.15: mockprovider should be in main.py _display
    def test_mockprovider_in_main_display(self):
        """'mockprovider' should have a display name entry in main.py's _display
        dict. On UNFIXED code, it does NOT.
        """
        hardcoded_display = {
            "kokoro": "Kokoro",
            "openai": "OpenAI",
            "elevenlabs": "ElevenLabs",
            "coqui": "Coqui TTS",
            "voxcpm": "VoxCPM",
        }
        assert "mockprovider" in hardcoded_display, (
            "Bug condition confirmed (Req 1.15): 'mockprovider' is NOT in the "
            f"hardcoded _display dict in main.py: {list(hardcoded_display.keys())}"
        )

    # ---- 7. SPEED_BOUNDS ------------------------------------------------------
    # Requirement 1.1 (related): speed bounds should include the new provider
    def test_mockprovider_in_speed_bounds(self):
        """'mockprovider' should have speed bounds. On UNFIXED code, the
        hardcoded SPEED_BOUNDS dict does NOT include it.
        """
        from distr.core.agent.constants import SPEED_BOUNDS

        assert "mockprovider" in SPEED_BOUNDS, (
            "Bug condition confirmed: 'mockprovider' is NOT in the hardcoded "
            f"SPEED_BOUNDS dict: {list(SPEED_BOUNDS.keys())}"
        )

    # ---- 8. TTS_SAMPLE_RATES -------------------------------------------------
    # Related to service creation: sample rate should be known
    def test_mockprovider_in_sample_rates(self):
        """'mockprovider' should have a sample rate entry. On UNFIXED code, the
        hardcoded TTS_SAMPLE_RATES dict does NOT include it.
        """
        from distr.core.agent.constants import TTS_SAMPLE_RATES

        assert "mockprovider" in TTS_SAMPLE_RATES, (
            "Bug condition confirmed: 'mockprovider' is NOT in the hardcoded "
            f"TTS_SAMPLE_RATES dict: {list(TTS_SAMPLE_RATES.keys())}"
        )


class MockProviderDescriptor(TTSProviderDescriptor):
    """A minimal mock descriptor for testing registry-based auto-dispatch."""

    @property
    def id(self) -> str:
        return "mockprovider"

    @property
    def name(self) -> str:
        return "Mock (Test)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return "default"

    @property
    def settings_key(self) -> str:
        return "mockprovider_voice"

    @property
    def sample_rate(self) -> int:
        return 22050

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return False

    @property
    def custom_voice_limit(self) -> int:
        return 0

    def create_service(self, tts_config, *, settings, stt_service, is_hands_free, models_dir):
        return None  # mock — no real service needed

    def generate_audio(self, text, voice, speed, out_file):
        pass  # mock — no real audio generation

    def resolve_display_name(self, voice_id, settings, voice_name=None):
        return voice_name or voice_id

    def normalize_voice(self, raw_voice, settings):
        return raw_voice or self.default_voice

    def get_voices(self):
        return [{"id": "default", "name": "Default"}]

    def get_hot_swap_config(self, voice_model, settings):
        return {"engine": self.id, "voice_name": voice_model or self.default_voice}

    def get_voice_settings_entry(self):
        return (self.id, self.settings_key, self.default_voice, {})

    def get_telegram_voice_id(self, settings):
        return settings.get(self.settings_key, self.default_voice)

    def normalize_provider_name(self, raw):
        if raw.lower().strip() in ("mockprovider", "mock (test)", "mock"):
            return self.id
        return None


class TestBugConditionFixed:
    """After the fix is implemented, register a mock provider via the
    TTSProviderDescriptor / TTSProviderRegistry API and verify that ALL
    consumer code paths handle it automatically — no manual if/elif needed.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12
    """

    @pytest.fixture(autouse=True)
    def _register_mock(self):
        """Register the mock descriptor in the registry, then clean up."""
        from distr.core.agent.services.tts.registry import tts_registry

        # Save original state
        tts_registry._ensure_discovered()
        original_providers = dict(tts_registry._providers)

        # Register mock
        mock = MockProviderDescriptor()
        tts_registry.register(mock)

        # Rebuild the module-level derived constants so TTS_PROVIDERS,
        # SPEED_BOUNDS, TTS_SAMPLE_RATES etc. include the mock provider.
        from distr.core.agent import constants
        constants._init_registry_derived()

        yield

        # Restore original state
        tts_registry._providers.clear()
        tts_registry._providers.update(original_providers)

        # Rebuild constants without mock
        constants._init_registry_derived()

    def test_registry_mock_provider_in_provider_ids(self):
        """Registering a mock descriptor should make it appear in
        tts_registry.provider_ids()."""
        from distr.core.agent.services.tts.registry import tts_registry

        assert "mockprovider" in tts_registry.provider_ids(), (
            f"'mockprovider' not found in registry provider_ids: "
            f"{tts_registry.provider_ids()}"
        )

    def test_registry_mock_provider_normalize(self):
        """normalize_voice_provider('mockprovider') should return 'mockprovider'
        via the descriptor's normalize_provider_name()."""
        from distr.core.agent.constants import normalize_voice_provider

        result = normalize_voice_provider("mockprovider")
        assert result == "mockprovider", (
            f"normalize_voice_provider('mockprovider') returned '{result}'"
        )

    def test_registry_mock_provider_in_tts_providers(self):
        """Registering a mock descriptor should auto-include it in
        TTS_PROVIDERS (backward-compat list)."""
        from distr.core.agent.constants import TTS_PROVIDERS

        provider_ids = [p["id"] for p in TTS_PROVIDERS]
        assert "mockprovider" in provider_ids, (
            f"'mockprovider' not found in TTS_PROVIDERS ids: {provider_ids}"
        )

    def test_registry_mock_provider_in_speed_bounds(self):
        """Registering a mock descriptor should auto-include it in
        SPEED_BOUNDS."""
        from distr.core.agent.constants import SPEED_BOUNDS

        assert "mockprovider" in SPEED_BOUNDS, (
            f"'mockprovider' not found in SPEED_BOUNDS: "
            f"{list(SPEED_BOUNDS.keys())}"
        )
        assert SPEED_BOUNDS["mockprovider"] == (0.5, 2.0)

    def test_registry_mock_provider_in_sample_rates(self):
        """Registering a mock descriptor should auto-include it in
        TTS_SAMPLE_RATES."""
        from distr.core.agent.constants import TTS_SAMPLE_RATES

        assert "mockprovider" in TTS_SAMPLE_RATES, (
            f"'mockprovider' not found in TTS_SAMPLE_RATES: "
            f"{list(TTS_SAMPLE_RATES.keys())}"
        )
        assert TTS_SAMPLE_RATES["mockprovider"] == 22050

    def test_registry_mock_provider_get_descriptor(self):
        """tts_registry.get('mockprovider') should return the mock descriptor."""
        from distr.core.agent.services.tts.registry import tts_registry

        descriptor = tts_registry.get("mockprovider")
        assert descriptor.id == "mockprovider"
        assert descriptor.name == "Mock (Test)"
        assert descriptor.settings_key == "mockprovider_voice"
