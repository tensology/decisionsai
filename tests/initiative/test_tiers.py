"""Tests for permission tier helpers (R2)."""

from distr.core.initiative.tiers import (
    PermissionTier,
    boundary_minimum_tier,
    configured_tier_for_action,
    effective_permission_tier,
)


def test_boundary_minimum_escalates_when_ask_file():
    b = {"initiative_ask_file_changes": True}
    assert boundary_minimum_tier("file_change", b) == PermissionTier.ESCALATE


def test_boundary_minimum_zero_when_no_ask():
    b = {"initiative_ask_file_changes": False}
    assert boundary_minimum_tier("file_change", b) == PermissionTier.SILENT


def test_effective_maxes_configured_and_boundary():
    boundaries = {
        "initiative_allow_routine_tasks": False,
        "initiative_ask_file_changes": True,
    }
    settings = {"initiative_tier_file_change": PermissionTier.NOTIFY}
    eff = effective_permission_tier("file_change", boundaries, settings)
    assert eff == PermissionTier.ESCALATE


def test_routine_silent_when_allowed():
    boundaries = {"initiative_allow_routine_tasks": True}
    eff = effective_permission_tier("routine_task", boundaries, {})
    assert eff == PermissionTier.SILENT


def test_default_notify_floor_does_not_lift_silent_routine():
    boundaries = {"initiative_allow_routine_tasks": True}
    settings = {"initiative_default_tier": PermissionTier.NOTIFY}
    eff = effective_permission_tier("routine_task", boundaries, settings)
    assert eff == PermissionTier.SILENT


def test_external_comm_stays_approve_with_default_notify_floor():
    boundaries = {"initiative_ask_external_comms": False}
    settings = {"initiative_default_tier": PermissionTier.NOTIFY}
    eff = effective_permission_tier("external_comms", boundaries, settings)
    assert eff == PermissionTier.APPROVE
