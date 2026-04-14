"""
TTSProviderRegistry — central registry with auto-discovery for TTS provider descriptors.

Provides dynamic dispatch for all consumer code paths, eliminating the need
for hardcoded if/elif chains when adding or removing providers.

Usage:
    from distr.core.agent.services.tts.registry import tts_registry

    descriptor = tts_registry.get("kokoro")
    enabled = tts_registry.enabled_providers()
    all_ids = tts_registry.provider_ids()
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)


class TTSProviderRegistry:
    """Central registry for TTS provider descriptors.

    Supports both manual registration via ``register()`` and automatic
    discovery of modules in ``distr/core/agent/services/tts/`` that export
    a module-level ``DESCRIPTOR`` attribute which is a
    ``TTSProviderDescriptor`` instance.
    """

    def __init__(self) -> None:
        self._providers: dict[str, TTSProviderDescriptor] = {}
        self._discovered = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, descriptor: TTSProviderDescriptor) -> None:
        """Register a provider descriptor.

        Args:
            descriptor: A ``TTSProviderDescriptor`` instance.  Its ``id``
                property is used as the lookup key.

        Raises:
            ValueError: If a descriptor with the same id is already registered.
        """
        pid = descriptor.id
        if pid in self._providers:
            raise ValueError(
                f"Duplicate TTS provider descriptor id: '{pid}' "
                f"(already registered by {type(self._providers[pid]).__name__})"
            )
        self._providers[pid] = descriptor
        logger.debug("Registered TTS provider descriptor: %s", pid)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, provider_id: str) -> TTSProviderDescriptor:
        """Return the descriptor for *provider_id*.

        Auto-discovery is triggered on first access if it hasn't run yet.

        Raises:
            KeyError: If no descriptor is registered for *provider_id*.
        """
        self._ensure_discovered()
        if provider_id not in self._providers:
            raise KeyError(
                f"No TTS provider descriptor registered for '{provider_id}'. "
                f"Known providers: {list(self._providers.keys())}"
            )
        return self._providers[provider_id]

    # ------------------------------------------------------------------
    # Iteration helpers
    # ------------------------------------------------------------------

    def enabled_providers(self) -> list[TTSProviderDescriptor]:
        """Return all registered descriptors where ``enabled`` is ``True``."""
        self._ensure_discovered()
        return [d for d in self._providers.values() if d.enabled]

    def all_providers(self) -> list[TTSProviderDescriptor]:
        """Return all registered descriptors (enabled and disabled)."""
        self._ensure_discovered()
        return list(self._providers.values())

    def provider_ids(self) -> list[str]:
        """Return the ids of all *enabled* providers."""
        return [d.id for d in self.enabled_providers()]

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def _ensure_discovered(self) -> None:
        """Run auto-discovery once, on first access."""
        if not self._discovered:
            self._discovered = True
            self._auto_discover()

    def _auto_discover(self) -> None:
        """Scan ``distr.core.agent.services.tts`` for modules exporting ``DESCRIPTOR``."""
        from distr.core.agent.services.tts.provider_descriptor import (
            TTSProviderDescriptor,
        )

        package_dir = os.path.dirname(__file__)
        package_name = "distr.core.agent.services.tts"

        for module_info in pkgutil.iter_modules([package_dir]):
            module_name = module_info.name
            # Skip non-descriptor helper modules
            if module_name in ("__init__", "registry", "provider_descriptor"):
                continue

            fqn = f"{package_name}.{module_name}"
            try:
                mod = importlib.import_module(fqn)
            except Exception:
                logger.debug("Skipping TTS module %s (import failed)", fqn, exc_info=True)
                continue

            descriptor = getattr(mod, "DESCRIPTOR", None)
            if descriptor is not None and isinstance(descriptor, TTSProviderDescriptor):
                if descriptor.id not in self._providers:
                    self.register(descriptor)
                    logger.debug(
                        "Auto-discovered TTS provider: %s from %s",
                        descriptor.id,
                        fqn,
                    )

    # ------------------------------------------------------------------
    # Testing / reset support
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Clear all registrations and reset discovery flag.

        Intended for use in tests only.
        """
        self._providers.clear()
        self._discovered = False

    def __contains__(self, provider_id: str) -> bool:
        self._ensure_discovered()
        return provider_id in self._providers

    def __len__(self) -> int:
        self._ensure_discovered()
        return len(self._providers)

    def __repr__(self) -> str:
        return (
            f"<TTSProviderRegistry providers={list(self._providers.keys())} "
            f"discovered={self._discovered}>"
        )


# Module-level singleton
tts_registry = TTSProviderRegistry()
