"""Kanban route tests need ``cryptography`` (imported by ``routes/kanban.py``)."""

from __future__ import annotations

import importlib.util


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    if importlib.util.find_spec("cryptography") is None:
        name = getattr(collection_path, "name", "")
        return name.startswith("test_") and name.endswith(".py")
    return False
