"""Resolve automations by preset or natural-language intent."""

from __future__ import annotations

import logging
from typing import Any

from distr.core.automation_orchestrator import is_automation_workflow, serialize_automation_workflow
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow

logger = logging.getLogger(__name__)


def _json_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        import json

        loaded = json.loads(str(raw))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


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
        with get_session() as session:
            candidates = (
                session.query(AutoWorkflow)
                .filter(AutoWorkflow.workflow_type == "scheduled")
                .order_by(AutoWorkflow.modified_date.desc())
                .all()
            )
            for workflow in candidates:
                if not is_automation_workflow(workflow):
                    continue
                marker = _json_config(workflow.context_rules)
                if str(marker.get("preset_id") or "").strip().lower() != key:
                    continue
                if active_only and (workflow.status or "").strip().lower() != "active":
                    continue
                rows.append(serialize_automation_workflow(workflow))
    except Exception:
        logger.debug("find_automations_by_preset failed", exc_info=True)
    return rows


def find_automation_for_daily_plan(*, active_only: bool = True) -> dict[str, Any] | None:
    rows = find_automations_by_preset("daily_plan", active_only=active_only)
    return rows[0] if rows else None


def has_active_daily_plan_automation() -> bool:
    return find_automation_for_daily_plan(active_only=True) is not None
