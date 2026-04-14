"""
TTSProviderDescriptor — abstract base class for TTS provider descriptors.

Each TTS provider implements this interface in a single descriptor file,
declaring all its configuration and behavior in one place. Consumer code
(service_factory, session, tts_handler, events, chat, voice_cloning, etc.)
dispatches via the registry instead of maintaining hardcoded if/elif chains.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class TTSProviderDescriptor(ABC):
    """Abstract base class that every TTS provider must implement.

    Static configuration is exposed as properties. Behavioral methods
    correspond 1-to-1 with the if/elif branches that previously lived
    in consumer files.
    """

    # ------------------------------------------------------------------
    # Static configuration properties
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def id(self) -> str:
        """Canonical lowercase provider id (e.g. 'kokoro', 'elevenlabs')."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name (e.g. 'Kokoro (Offline)')."""
        ...

    @property
    @abstractmethod
    def type(self) -> str:
        """Provider type: 'offline' or 'online'."""
        ...

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider is enabled and visible in the UI."""
        ...

    @property
    @abstractmethod
    def default_voice(self) -> str:
        """Default voice id for this provider."""
        ...

    @property
    @abstractmethod
    def settings_key(self) -> str:
        """DB settings key that stores the user's chosen voice (e.g. 'kokoro_voice')."""
        ...

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output sample rate in Hz (e.g. 24000 for Kokoro, 44100 for ElevenLabs)."""
        ...

    @property
    @abstractmethod
    def speed_bounds(self) -> tuple[float, float]:
        """(min, max) playback speed bounds for this provider."""
        ...

    @property
    @abstractmethod
    def supports_custom_voices(self) -> bool:
        """Whether this provider supports custom/cloned voices."""
        ...

    @property
    @abstractmethod
    def custom_voice_limit(self) -> int:
        """Max number of custom voices (0 = unlimited, only relevant if supports_custom_voices)."""
        ...

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def create_service(
        self,
        tts_config: dict,
        *,
        settings: dict,
        stt_service: Any,
        is_hands_free: bool,
        models_dir: str,
    ) -> Any:
        """Create and return a TTS service instance.

        Replaces the per-provider branch in ``service_factory.create_tts_service()``
        and ``session._create_services()``.

        Args:
            tts_config: TTS configuration dict (engine, voice_name, voice_id, api_key, …).
            settings: User settings dict from DB.
            stt_service: The current STT service instance (for pipeline wiring).
            is_hands_free: Whether hands-free mode is active.
            models_dir: Path to the models directory.

        Returns:
            A fully initialised TTS service instance.
        """
        ...

    # ------------------------------------------------------------------
    # Audio generation
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        """Generate TTS audio and write it to *out_file*.

        Replaces the per-provider branch in ``tts_handler.generate_tts_audio()``,
        ``generate_voice_sample()``, ``events._telegram_generate_tts()``, and
        ``send_voice_note_to_telegram._run()``.
        """
        ...

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    @abstractmethod
    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve a voice id to a human-readable display name.

        Replaces the per-provider branch in ``service_factory.resolve_voice_to_display_name()``
        and ``tts_handler._resolve_display_name()``.

        Args:
            voice_id: Raw voice identifier (e.g. 'af_heart', 'custom_5', ElevenLabs voice id).
            settings: User settings dict from DB.
            voice_name: Optional pre-resolved display name hint.
        """
        ...

    @abstractmethod
    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid voice id for this provider.

        Replaces the per-provider branch in ``tts_handler._normalize_voice_for_provider()``.
        """
        ...

    @abstractmethod
    def get_voices(self) -> list[dict]:
        """Return the list of available voices for this provider.

        Each entry is a dict with at least ``{"id": ..., "name": ...}``.
        Replaces the per-provider branch in ``voices._get_voices_for_provider()``.
        """
        ...

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    @abstractmethod
    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for switching to this provider.

        The returned dict is consumed by ``session._hot_swap_tts_service()`` and
        should contain the keys needed to update ``self.config['tts']`` before
        creating a new service or performing an in-place voice swap.

        Keys may include:
            - ``engine``: the engine name
            - ``voice_name`` / ``voice_id``: resolved voice
            - ``api_key``: API key if needed
            - ``in_place``: True if the swap can be done without replacing the service
            - Any provider-specific extras
        """
        ...

    # ------------------------------------------------------------------
    # Voice settings entry (for _VOICE_SETTINGS table in session.py)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the ``_VOICE_SETTINGS`` tuple for this provider.

        Returns:
            (engine, settings_key, default_voice, extra_keys) where extra_keys
            maps tts_config keys to settings DB keys (e.g. ``{'api_key': 'elevenlabs_key'}``).
        """
        ...

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    @abstractmethod
    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the voice id from settings for Telegram voice note generation.

        Replaces the per-provider branch in ``events._telegram_resolve_voice_settings()``.
        """
        ...

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    @abstractmethod
    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches this provider and return the canonical id, or None.

        Called by ``constants.normalize_voice_provider()`` which iterates all
        descriptors. Return ``self.id`` if *raw* (lowercased) matches this
        provider's known aliases, otherwise return ``None``.
        """
        ...

    # ------------------------------------------------------------------
    # Voice cloning (optional — default raises NotImplementedError)
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Clone a voice using this provider's cloning mechanism.

        Override in providers that support custom voice cloning (e.g. ElevenLabs IVC,
        Kokoro Kanade, Coqui XTTS v2). The default implementation raises
        ``NotImplementedError``.

        Args:
            voice: The ``CustomVoice`` DB model instance.
            audio_files: List of audio file paths uploaded by the user.
            session: The SQLAlchemy DB session.
        """
        raise NotImplementedError(
            f"Voice cloning is not supported by provider '{self.id}'"
        )
