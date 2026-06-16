"""Resolve automations by preset or natural-language intent."""

from __future__ import annotations

import logging
from typing import Any

from distr.core.automation.store import list_automations

logger = logging.getLogger(__name__)


def find_automations_by_preset(
    preset_id: str,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    key = str(preset_id or "").strip().lower()
    if not key:
        return []
    rows: list[dict[str, Any]] = []
    try:
        for automation in list_automations():
            if str(automation.get("preset_id") or "").strip().lower() != key:
                continue
            if active_only and (automation.get("status") or "").strip().lower() != "active":
                continue
            rows.append(automation)
    except Exception:
        logger.debug("find_automations_by_preset failed", exc_info=True)
    return rows


def find_automation_for_daily_plan(*, active_only: bool = True) -> dict[str, Any] | None:
    rows = find_automations_by_preset("daily_plan", active_only=active_only)
    return rows[0] if rows else None


def has_active_daily_plan_automation() -> bool:
    return find_automation_for_daily_plan(active_only=True) is not None
