"""Permission tiers (R2) — user-visible gating layered under policy + rubric (DESIGN §2.2)."""

from __future__ import annotations

from enum import IntEnum


class PermissionTier(IntEnum):
    SILENT = 0
    NOTIFY = 1
    APPROVE = 2
    ESCALATE = 3


def _clamp_tier(value: object, default: int = 1) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(3, n))


def boundary_minimum_tier(action_type: str, boundaries: dict) -> int:
    """Minimum tier enforced by initiative \"ask\" boundaries (floor, never lowers tier)."""
    at = (action_type or "none").strip().lower()
    if at == "sensitive":
        return PermissionTier.ESCALATE
    if at in ("ticket_lane_move", "workflow_start", "project_cli_task"):
        return PermissionTier.APPROVE
    if at == "external_comms" and boundaries.get("initiative_ask_external_comms"):
        return PermissionTier.ESCALATE
    if at == "file_change" and boundaries.get("initiative_ask_file_changes"):
        return PermissionTier.ESCALATE
    return PermissionTier.SILENT


def configured_tier_for_action(
    action_type: str, boundaries: dict, settings: dict
) -> int:
    """Tier from DB overrides or DESIGN defaults (before boundary floor)."""
    at = (action_type or "none").strip().lower()
    key = f"initiative_tier_{at}"
    if settings.get(key) is not None:
        return _clamp_tier(settings.get(key), default=PermissionTier.NOTIFY)

    if at == "suggestion":
        return PermissionTier.NOTIFY
    if at == "routine_task":
        return (
            PermissionTier.SILENT
            if boundaries.get("initiative_allow_routine_tasks")
            else PermissionTier.NOTIFY
        )
    if at in ("board_triage", "message_triage", "email_triage", "jira_intake"):
        return PermissionTier.NOTIFY
    if at == "ticket_lane_move":
        return PermissionTier.APPROVE
    if at == "workflow_start":
        return PermissionTier.APPROVE
    if at == "project_cli_task":
        return PermissionTier.APPROVE
    if at == "automation_recommendation":
        return PermissionTier.APPROVE
    if at == "external_comms":
        return PermissionTier.APPROVE
    if at == "file_change":
        return PermissionTier.APPROVE
    if at == "sensitive":
        return PermissionTier.ESCALATE
    return PermissionTier.SILENT


def effective_permission_tier(
    action_type: str, boundaries: dict, settings: dict | None = None
) -> PermissionTier:
    """
    effective_tier = max(configured_tier, boundary_minimum), then apply
    ``initiative_default_tier`` as an extra floor when the result is not SILENT.

    SILENT is preserved so approved routine work can stay quiet (DESIGN §2.2).
    """
    settings = settings or {}
    cfg = configured_tier_for_action(action_type, boundaries, settings)
    bmin = boundary_minimum_tier(action_type, boundaries)
    floor = max(cfg, bmin)

    default_floor = _clamp_tier(
        settings.get("initiative_default_tier", PermissionTier.NOTIFY),
        default=PermissionTier.NOTIFY,
    )
    if floor == PermissionTier.SILENT:
        return PermissionTier(floor)
    return PermissionTier(max(floor, default_floor))
