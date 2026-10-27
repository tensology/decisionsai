"""Seeding must never resurrect the retired Development pipeline."""

from __future__ import annotations

from distr.core.db import seed_workflows as module


def _stub(monkeypatch, *, classes, retired=0):
    monkeypatch.setattr(module, "ensure_class_workflows", lambda: dict(classes))
    monkeypatch.setattr(module, "retire_legacy_workflows", lambda: {"retired": retired})


def test_seeding_creates_managed_class_workflows(monkeypatch):
    _stub(monkeypatch, classes={"light": 1, "standard": 2}, retired=1)
    result = module.seed_workflows()

    assert sorted(result["seeded_names"]) == ["light", "standard"]
    assert result["retired_legacy"] == 1
    assert result["converted_legacy_request"] is False


def test_a_legacy_only_request_converts_to_the_managed_workflows(monkeypatch):
    _stub(monkeypatch, classes={"standard": 2})
    result = module.seed_workflows(workflow_names=["Development"])

    assert result["converted_legacy_request"] is True
    assert result["seeded_names"] == ["standard"]


def test_a_mixed_request_is_not_treated_as_legacy_only(monkeypatch):
    _stub(monkeypatch, classes={"standard": 2})
    result = module.seed_workflows(workflow_names=["Development", "Ticket: standard (one session)"])

    assert result["converted_legacy_request"] is False


def test_force_reset_rebuilds_each_managed_workflow(monkeypatch):
    built: list[tuple[int, str]] = []
    _stub(monkeypatch, classes={"heavy": 3, "critical": 4})
    monkeypatch.setattr(
        module,
        "rebuild_class_workflow",
        lambda workflow_id, class_key: built.append((workflow_id, class_key)),
    )

    module.seed_workflows(force_reset=True)

    assert sorted(built) == [(3, "heavy"), (4, "critical")]


def test_managed_names_are_the_class_workflow_names():
    from distr.core.workflow.phases import class_workflow_name

    assert module.managed_workflow_names() == [class_workflow_name(key) for key in ("light", "standard", "heavy", "critical")]


def test_retired_names_cover_both_development_aliases():
    assert "development" in module.RETIRED_WORKFLOW_NAMES
    assert "development: ticket to implementation" in module.RETIRED_WORKFLOW_NAMES
