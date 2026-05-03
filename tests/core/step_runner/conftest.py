"""Workflow orchestration modules patch ``sys.modules`` at import time.

Those patches break collection of unrelated suites (e.g. ``distr.core.db`` becomes a
mock). When running the broad ``pytest tests/`` tree, skip importing them here.
Run them explicitly::

    pytest tests/core/step_runner/

"""

from __future__ import annotations

_WORKFLOW_MOCK_MODULES = frozenset(
    {
        "test_pbt_workflow_agent.py",
        "test_integration_workflow.py",
        "test_concurrent_orchestration.py",
    }
)


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    if collection_path.name not in _WORKFLOW_MOCK_MODULES:
        return False
    args = [str(a) for a in config.args]
    if any("step_runner" in a for a in args):
        return False
    return True
