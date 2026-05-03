"""Misc scripts that are not CI-safe unit tests."""

from __future__ import annotations

_IGNORE_NAMES = frozenset(
    {
        "test_playback.py",
        "test_slugify_check.py",
        "test_vosk_download_real.py",
    }
)


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    return collection_path.name in _IGNORE_NAMES
