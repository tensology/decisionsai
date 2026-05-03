"""Integration folder mixes pytest targets with manual smoke scripts."""

from __future__ import annotations


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    return collection_path.name == "trello_boards_manual.py"
