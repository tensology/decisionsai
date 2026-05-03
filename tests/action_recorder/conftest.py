"""Legacy desktop ``ActionWindow`` tests — GUI removed in favor of web Actions UI."""

from __future__ import annotations

import pytest


_LEGACY_ACTION_WINDOW_FILES = frozenset(
    {
        "test_action_recording.py",
        "test_action_recording_crash.py",
        "test_action_recording_delayed.py",
        "test_action_recording_worker.py",
        "test_action_window_gradual.py",
    }
)


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    return collection_path.name in _LEGACY_ACTION_WINDOW_FILES
