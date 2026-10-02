"""human_checkpoints must survive _workflow_run_settings (not stripped)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from distr.core.workflow.dispatcher import DEFAULT_RUN_SETTINGS, _workflow_run_settings


def test_default_run_settings_enable_human_checkpoints():
    assert DEFAULT_RUN_SETTINGS.get("human_checkpoints") is True


def test_workflow_run_settings_preserves_and_defaults_human_checkpoints():
    empty = _workflow_run_settings(SimpleNamespace(run_settings=None))
    assert empty["human_checkpoints"] is True

    enabled = _workflow_run_settings(
        SimpleNamespace(run_settings=json.dumps({"human_checkpoints": True, "execution_mode": "parallel"}))
    )
    assert enabled["human_checkpoints"] is True
    assert enabled["execution_mode"] == "parallel"

    disabled = _workflow_run_settings(
        SimpleNamespace(run_settings=json.dumps({"human_checkpoints": False}))
    )
    assert disabled["human_checkpoints"] is False

    # Keys outside the allow-list still must not wipe human_checkpoints default.
    mixed = _workflow_run_settings(
        SimpleNamespace(run_settings=json.dumps({"free_only": True, "prefer_local": True}))
    )
    assert mixed["human_checkpoints"] is True
    assert "free_only" not in mixed


def test_spawn_and_kanban_skip_human_checkpoints_default_false():
    """Spawn + kanban API must not skip checkpoints unless callers opt in."""
    import inspect

    from distr.core.workflow.spawn_workflow import spawn_workflow_for_ticket
    from distr.gui.web.routes.kanban import SpawnWorkflowForTicketRequest

    spawn_default = inspect.signature(spawn_workflow_for_ticket).parameters[
        "skip_human_checkpoints"
    ].default
    assert spawn_default is False

    req = SpawnWorkflowForTicketRequest()
    assert req.skip_human_checkpoints is False
    assert (
        SpawnWorkflowForTicketRequest.model_fields["skip_human_checkpoints"].default
        is False
    )

    # Agent workflow tool must call spawn with skip=False (not hard-coded True).
    from distr.core.agent.tools.step_runner import workflow_tools as wt

    assert "skip_human_checkpoints=False" in inspect.getsource(wt)
