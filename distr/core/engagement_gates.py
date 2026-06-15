"""Engagement gates for proactive automations and initiative delivery."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

_DAILY_PLAN_OPT_OUT_RE = re.compile(
    r"(?i)\b("
    r"don'?t\s+(?:want|need|send|give)\s+(?:me\s+)?(?:a\s+)?(?:daily|morning|day)\s+plan|"
    r"no\s+(?:daily|morning|day)\s+plan|"
    r"stop\s+(?:sending|giving)\s+(?:me\s+)?(?:daily|morning|day)\s+plan|"
    r"don'?t\s+send\s+(?:me\s+)?(?:a\s+)?morning\s+brief|"
    r"don'?t\s+pester\s+me\s+(?:with\s+)?(?:a\s+)?(?:daily|morning)\s+plan"
    r")\b"
)

_PLANNER_KINDS = frozenset(
    {
        "daily_plan",
        "morning_brief",
        "day_planner",
        "planner",
        "initiative_update",
    }
)


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        import json

        return json.loads(value)
    except Exception:
        return fallback


def _maintenance_value(key: str) -> dict[str, Any]:
    try:
        from distr.core.db import get_session
        from distr.core.db.orchestrator import OrchestratorMaintenanceState

        with get_session() as session:
            row = (
                session.query(OrchestratorMaintenanceState)
                .filter(OrchestratorMaintenanceState.key == key)
                .first()
            )
            if not row:
                return {}
            loaded = _json_loads(row.value_json, {})
            return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def set_maintenance_value(key: str, value: dict[str, Any]) -> None:
    try:
        import json

        from distr.core.db import get_session
        from distr.core.db.orchestrator import OrchestratorMaintenanceState
        from distr.core.db.time import utc_now_naive

        with get_session() as session:
            row = (
                session.query(OrchestratorMaintenanceState)
                .filter(OrchestratorMaintenanceState.key == key)
                .first()
            )
            payload = json.dumps(value or {}, ensure_ascii=False, default=str)
            if row:
                row.value_json = payload
                row.updated_at = utc_now_naive()
            else:
                session.add(
                    OrchestratorMaintenanceState(
                        key=key,
                        value_json=payload,
                    )
                )
            session.commit()
    except Exception:
        return


def user_opted_out_of_daily_plans() -> bool:
    """True when durable memory says the user does not want scheduled plans."""
    try:
        from distr.core.orchestrator_memory import list_user_memories

        memories = list_user_memories(category="engagement_guardrail", limit=50)
        memories += list_user_memories(category="communication_preference", limit=50)
        for row in memories:
            content = str(row.get("content") or "")
            if _DAILY_PLAN_OPT_OUT_RE.search(content):
                return True
            lower = content.lower()
            if "daily plan" in lower and any(word in lower for word in ("does not want", "don't want", "stop sending")):
                return True
    except Exception:
        pass
    if _maintenance_value("daily_plan_automation_opt_out").get("opted_out"):
        return True
    return False


def record_daily_plan_opt_out(*, source: str = "user") -> None:
    set_maintenance_value(
        "daily_plan_automation_opt_out",
        {"opted_out": True, "source": source},
    )
    try:
        from distr.core.orchestrator_memory import record_user_memory

        record_user_memory(
            "Does not want scheduled daily or morning plans sent proactively.",
            category="engagement_guardrail",
            tags=["guardrail", "daily_plan", "automation"],
            confidence=0.9,
        )
    except Exception:
        pass


def _local_now() -> datetime:
    try:
        from distr.core.initiative.scheduler import default_local_tz

        return datetime.now(default_local_tz())
    except Exception:
        return datetime.now()


def _recent_activity_within(hours: float) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        from distr.core.orchestrator_memory import list_machine_activity

        for row in list_machine_activity(limit=5):
            stamp = str(row.get("last_seen_at") or row.get("captured_at") or "").strip()
            if not stamp:
                continue
            try:
                seen = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                if seen >= cutoff:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        raw = settings.get("agent_last_activity_at") or settings.get("last_user_message_at")
        if raw:
            seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            if seen >= cutoff:
                return True
    except Exception:
        pass
    return False


def user_likely_awake(*, manual: bool = False) -> bool:
    """Best-effort awake check for scheduled proactive delivery."""
    if manual:
        return True
    if _recent_activity_within(3.0):
        return True
    local = _local_now()
    hour = local.hour
    # Conservative default: avoid early-morning pushes before 8:00 local.
    if hour < 8:
        return False
    # Late night without recent activity.
    if hour >= 23 and not _recent_activity_within(1.5):
        return False
    return True


def proactive_delivery_blocked(
    *,
    delivery_kind: str = "",
    body: str = "",
    manual: bool = False,
    preset_id: str = "",
) -> tuple[bool, str]:
    """
    Return (blocked, reason).

    Manual runs and explicit user requests bypass awake-hour gating but still
    respect opt-outs for scheduled automations only when ``manual`` is False.
    """
    kind = str(delivery_kind or "").strip().lower()
    preset = str(preset_id or "").strip().lower()
    text = f"{body} {kind} {preset}".lower()
    is_plan_delivery = preset == "daily_plan" or any(
        token in text for token in ("daily plan", "morning brief", "day planner", "morning plan")
    ) or kind in _PLANNER_KINDS

    if is_plan_delivery and user_opted_out_of_daily_plans() and not manual:
        return True, "daily_plan_opt_out"

    if not manual and is_plan_delivery and not user_likely_awake(manual=False):
        return True, "user_likely_asleep"

    return False, ""


def daily_plan_prompt_due() -> bool:
    """Whether Initiative may ask once about setting up a daily-plan automation."""
    if user_opted_out_of_daily_plans():
        return False
    state = _maintenance_value("daily_plan_automation_prompt")
    if state.get("declined"):
        return False
    if state.get("prompted_at"):
        try:
            prompted = datetime.fromisoformat(str(state["prompted_at"]).replace("Z", "+00:00"))
            if prompted.tzinfo is None:
                prompted = prompted.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - prompted < timedelta(days=30):
                return False
        except Exception:
            pass
    return True


def mark_daily_plan_prompt_sent() -> None:
    set_maintenance_value(
        "daily_plan_automation_prompt",
        {"prompted_at": datetime.now(timezone.utc).isoformat()},
    )


def mark_daily_plan_prompt_declined() -> None:
    set_maintenance_value(
        "daily_plan_automation_prompt",
        {
            "declined": True,
            "prompted_at": datetime.now(timezone.utc).isoformat(),
        },
    )
