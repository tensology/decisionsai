#!/usr/bin/env python3
"""
Check that all critical DecisionsAI dependencies are importable.
Runs in a single process to avoid spawning 37+ subprocesses, which can
trigger macOS jetsam (SIGKILL) under memory pressure and cause false
"missing package" reports.
Exit 0 if all OK, 1 if any missing. Missing **critical** package names are printed to **stdout**
(one per line) so launch scripts can parse them without mixing in library warnings on stderr.
"""
import os
import sys

# Suppress noisy import banners (pipecat, etc.) before any heavy imports
os.environ.setdefault("LOGURU_LEVEL", "ERROR")

# package_name: import_name (for packages where they differ)
CRITICAL = [
    ("numpy", "numpy"),
    ("PyQt6", "PyQt6"),
    ("torch", "torch"),
    ("transformers", "transformers"),
    ("langchain", "langchain"),
    ("ollama", "ollama"),
    ("scipy", "scipy"),
    ("litellm", "litellm"),
    ("vosk", "vosk"),
    ("pipecat", "pipecat"),
    ("kokoro_onnx", "kokoro_onnx"),
    ("supertonic", "supertonic"),
    ("kanade_tokenizer", "kanade_tokenizer"),
    ("sqlalchemy", "sqlalchemy"),
    ("beautifulsoup4", "bs4"),
    ("lxml", "lxml"),
    ("elevenlabs", "elevenlabs"),
    ("sounddevice", "sounddevice"),
    ("soundfile", "soundfile"),
    ("pynput", "pynput"),
    ("pyautogui", "pyautogui"),
    ("pyaudio", "pyaudio"),
    ("fuzzywuzzy", "fuzzywuzzy"),
    ("resampy", "resampy"),
    ("syntok", "syntok"),
    ("colorama", "colorama"),
]

OPTIONAL = [
    ("pywhispercpp", "pywhispercpp"),
    ("chatterbox-mlx", "chatterbox"),
]

# Packages checked via pip metadata rather than import (heavy deps that may
# fail to import at check time even when correctly installed).
METADATA_ONLY = [
]

def main():
    missing = []
    for pkg_name, import_name in CRITICAL:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg_name)

    # Check metadata-only packages via pip (avoids importing heavy deps)
    try:
        import importlib.metadata as _meta
        for pkg_name in METADATA_ONLY:
            try:
                _meta.version(pkg_name)
            except _meta.PackageNotFoundError:
                missing.append(pkg_name)
    except Exception:
        pass  # importlib.metadata unavailable — skip these checks

    if sys.platform == "darwin":
        try:
            __import__("AppKit")
        except ImportError:
            missing.append("pyobjc-framework-Cocoa")

    if missing:
        for name in missing:
            print(name)
        return 1

    # Check optional packages — warn but don't fail
    for pkg_name, import_name in OPTIONAL:
        try:
            __import__(import_name)
        except ImportError:
            print(f"optional: {pkg_name} not available", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())
