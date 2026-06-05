"""Legacy Kanban agent tests.

The Ticket Board check-in agent was removed in favor of the Automations hub.
These tests target deleted modules/endpoints and are intentionally ignored.
"""


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    name = getattr(collection_path, "name", "")
    return name.startswith("test_") and name.endswith(".py") and name != "test_legacy_removed.py"
