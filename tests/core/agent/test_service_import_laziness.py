import subprocess
import sys


def _loaded_modules_after(statement: str) -> set[str]:
    script = (
        "import sys; "
        f"{statement}; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return set(result.stdout.splitlines())


def test_whisper_import_does_not_eagerly_load_llm_or_tts_providers():
    loaded = _loaded_modules_after(
        "from distr.core.agent.services import WhisperSTTService"
    )

    assert "distr.core.agent.services.stt.whisper" in loaded
    assert "distr.core.agent.services.tts.kokoro" not in loaded
    assert "distr.core.agent.services.llm.providers.openai" not in loaded
    assert "distr.core.agent.services.llm.providers.ollama" not in loaded


def test_service_factory_import_does_not_eagerly_load_llm_providers():
    loaded = _loaded_modules_after("import distr.core.agent.service_factory")

    assert "distr.core.agent.services.llm.providers.openai" not in loaded
    assert "distr.core.agent.services.llm.providers.ollama" not in loaded
