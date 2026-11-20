"""Select application dependencies from the shared, full requirements list."""

from pathlib import Path
import re


# These capabilities remain available to full developer installs. The terminal
# installer selects only the extensions needed for the user's first-run profile.
_EXTENSIONS = {
    "whisper": frozenset({"pywhispercpp"}),
    "vosk": frozenset({"vosk"}),
    "assemblyai": frozenset({"assemblyai"}),
    "elevenlabs": frozenset({"elevenlabs"}),
    "kokoro": frozenset({"kokoro-onnx"}),
    "supertonic": frozenset({"supertonic"}),
}
_DEFERRED = frozenset({
    "browser-use",
    "coqui-tts",
    "headroom-ai",
    "hypothesis",
    "kanade-tokenizer",
    "llama-index",
    "llama-index-embeddings-ollama",
    "llama-index-embeddings-openai",
    "llama-index-llms-ollama",
    "llama-index-llms-openai",
    "playwright",
    "pytest",
    "pytest-playwright",
    "pyqt6-webengine",
    "sentence-transformers",
    "torch",
    "torchaudio",
    "transformers",
}).union(*_EXTENSIONS.values())


def selected_requirements(path: Path, *, stt: str, tts: str) -> list[str]:
    """Keep the tested pins for core packages and the selected speech providers."""
    selected = _EXTENSIONS.get(stt, frozenset()) | _EXTENSIONS.get(tts, frozenset())
    requirements = []
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = line.partition(" #")[0].strip()
        if not requirement or requirement.startswith("#"):
            continue
        name = re.match(r"[A-Za-z0-9_.-]+", requirement)
        if name is None:
            raise ValueError(f"Unsupported requirement syntax in {path.name}")
        package = re.sub(r"[-_.]+", "-", name[0]).lower()
        if package not in _DEFERRED or package in selected:
            if package == "pywhispercpp":
                # Official CPython 3.12 wheels avoid native toolchain installs.
                requirements.extend(("--only-binary=pywhispercpp", "pywhispercpp==1.5.1"))
            else:
                requirements.append(requirement)
    return requirements
