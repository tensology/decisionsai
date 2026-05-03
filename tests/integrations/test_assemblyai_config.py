"""Lightweight AssemblyAI STT service checks (no SDK method spy assertions).

Pipecat must appear available so ``AssemblyAISTTService`` can construct.
Requires ``pip install assemblyai`` for imports to succeed.
"""

import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import distr.core.agent.libs as _agent_libs  # noqa: E402

_agent_libs.PIPECAT_AVAILABLE = True

try:
    import distr.core.agent.services.stt.assemblyai as _asm_mod  # noqa: E402

    if not _asm_mod.ASSEMBLYAI_AVAILABLE:
        pytest.skip(
            "assemblyai PyPI package not installed (pip install assemblyai)",
            allow_module_level=True,
        )
    from distr.core.agent.services.stt.assemblyai import AssemblyAISTTService  # noqa: E402
except Exception as exc:  # noqa: BLE001
    pytest.skip(
        f"AssemblyAISTTService import failed (install assemblyai / pipecat): {exc}",
        allow_module_level=True,
    )


class TestAssemblyAIConfig(unittest.TestCase):
    """Assert constructor wiring only — batch transcribe uses live ``assemblyai`` APIs."""

    def test_init_default_model(self):
        service = AssemblyAISTTService(api_key="test_key")
        self.assertEqual(service.api_key, "test_key")
        self.assertEqual(service.speech_model, "universal")

    def test_init_specific_model(self):
        service = AssemblyAISTTService(api_key="test_key", model="slam-1")
        self.assertEqual(service.api_key, "test_key")
        self.assertEqual(service.speech_model, "slam-1")


if __name__ == "__main__":
    unittest.main()
