"""Seeding must never resurrect managed class or Development pipelines."""

from __future__ import annotations

from distr.core.db import seed_workflows as module


def test_seeding_does_not_create_managed_class_workflows(monkeypatch):
    monkeypatch.setattr(module, "retire_legacy_workflows", lambda: {"retired": 1})
    result = module.seed_workflows()

    assert result["seeded_count"] == 0
    assert result["seeded_names"] == []
    assert result["managed"] == {}
    assert result["retired_legacy"] == 1
    assert result["converted_legacy_request"] is False


def test_a_legacy_only_request_is_flagged_but_creates_nothing(monkeypatch):
    monkeypatch.setattr(module, "retire_legacy_workflows", lambda: {"retired": 0})
    result = module.seed_workflows(workflow_names=["Development"])

    assert result["converted_legacy_request"] is True
    assert result["seeded_names"] == []


def test_a_mixed_request_is_not_treated_as_legacy_only(monkeypatch):
    monkeypatch.setattr(module, "retire_legacy_workflows", lambda: {"retired": 0})
    result = module.seed_workflows(workflow_names=["Development", "Ticket: standard (one session)"])

    assert result["converted_legacy_request"] is False
    assert result["seeded_count"] == 0


def test_force_reset_is_a_noop(monkeypatch):
    monkeypatch.setattr(module, "retire_legacy_workflows", lambda: {"retired": 0})
    result = module.seed_workflows(force_reset=True)
    assert result["force_reset"] is True
    assert result["managed"] == {}


def test_managed_names_are_empty_after_retirement():
    assert module.managed_workflow_names() == []


def test_retired_names_cover_both_development_aliases():
    assert "development" in module.RETIRED_WORKFLOW_NAMES
    assert "development: ticket to implementation" in module.RETIRED_WORKFLOW_NAMES
