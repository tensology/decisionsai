"""Legacy desktop chat tests or modules that patch ``sys.modules`` at import time."""

from __future__ import annotations

_IGNORE = frozenset(
    {
        "test_chat_click_crash.py",
        "test_chat_feed_loading.py",
        "test_chat_list_loading.py",
        "test_chat_window_from_oracle.py",
    }
)


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    return collection_path.name in _IGNORE
