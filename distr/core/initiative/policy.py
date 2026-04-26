from enum import Enum


class PolicyDecision(Enum):
    EXECUTE = "execute"
    DRAFT_AND_ASK = "draft_and_ask"
    SUGGEST_ONLY = "suggest_only"
    SKIP = "skip"


RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def _normalize_risk(value) -> str:
    raw = str(value or "medium").strip().lower()
    return raw if raw in RISK_ORDER else "medium"


def _risk_at_least(current: str, minimum: str) -> bool:
    return RISK_ORDER[_normalize_risk(current)] >= RISK_ORDER[_normalize_risk(minimum)]


def _read_policy_context(action, boundaries: dict, policy_context: dict | None = None) -> dict:
    payload = getattr(action, "payload", None)
    action_payload = payload if isinstance(payload, dict) else {}
    scope_policy = action_payload.get("scope_policy") or {}
    if not isinstance(scope_policy, dict):
        scope_policy = {}
    merged = {}
    if isinstance(policy_context, dict):
        merged.update(policy_context)
    if scope_policy:
        merged.update(scope_policy)

    # Optional policy-level flags; defaults preserve current behavior.
    merged.setdefault("cooldown_active", False)
    merged.setdefault("duplicate_recent", False)
    merged.setdefault("minimum_confidence_to_execute", 0.0)
    merged.setdefault("minimum_risk_to_require_ask", "critical")
    merged.setdefault("always_require_ask_for", [])
    merged.setdefault("always_block_action_types", [])

    return {
        "cooldown_active": bool(merged.get("cooldown_active", False)),
        "duplicate_recent": bool(merged.get("duplicate_recent", False)),
        "minimum_confidence_to_execute": float(merged.get("minimum_confidence_to_execute", 0.0)),
        "minimum_risk_to_require_ask": _normalize_risk(merged.get("minimum_risk_to_require_ask", "critical")),
        "always_require_ask_for": {
            str(v).strip().lower()
            for v in (merged.get("always_require_ask_for") or [])
            if str(v).strip()
        },
        "always_block_action_types": {
            str(v).strip().lower()
            for v in (merged.get("always_block_action_types") or [])
            if str(v).strip()
        },
        "risk_level": _normalize_risk(action_payload.get("risk_level", "medium")),
        "confidence": float(action_payload.get("confidence", 1.0)),
    }


def evaluate(action, level: str, boundaries: dict, policy_context: dict | None = None) -> PolicyDecision:
    """
    Map (action, level, boundaries) to a PolicyDecision.

    action      — object with an `action_type` attribute (str)
    level       — initiative level: observe | assist | operate | own
    boundaries  — dict with keys:
                    initiative_allow_telegram
                    initiative_allow_routine_tasks
                    initiative_ask_external_comms
                    initiative_ask_file_changes
                    initiative_ask_sensitive
    """
    action_type = action.action_type
    policy = _read_policy_context(action, boundaries, policy_context)

    # Guard rails applied before level-specific logic.
    if action_type in policy["always_block_action_types"]:
        return PolicyDecision.SKIP
    if policy["cooldown_active"] or policy["duplicate_recent"]:
        return PolicyDecision.SKIP
    if action_type in policy["always_require_ask_for"]:
        return PolicyDecision.DRAFT_AND_ASK
    if policy["confidence"] < policy["minimum_confidence_to_execute"]:
        return PolicyDecision.SUGGEST_ONLY
    if (
        action_type in ("external_comms", "file_change", "sensitive")
        and _risk_at_least(policy["risk_level"], policy["minimum_risk_to_require_ask"])
    ):
        return PolicyDecision.DRAFT_AND_ASK

    if level == "observe":
        return PolicyDecision.SKIP

    if level == "assist":
        return PolicyDecision.SUGGEST_ONLY

    if level == "operate":
        if action_type == "none":
            return PolicyDecision.SKIP

        if action_type == "routine_task":
            if boundaries.get("initiative_allow_routine_tasks", False):
                return PolicyDecision.EXECUTE
            return PolicyDecision.SUGGEST_ONLY

        if action_type == "suggestion":
            # Deliver via Telegram if allowed, otherwise queue as draft
            # Either way the decision from the gate perspective is EXECUTE
            return PolicyDecision.EXECUTE

        if action_type == "external_comms":
            if boundaries.get("initiative_ask_external_comms", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        if action_type == "file_change":
            if boundaries.get("initiative_ask_file_changes", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        if action_type == "sensitive":
            if boundaries.get("initiative_ask_sensitive", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        # Unknown action type at operate level — skip
        return PolicyDecision.SKIP

    if level == "own":
        if action_type == "none":
            return PolicyDecision.SKIP

        if action_type == "routine_task":
            if boundaries.get("initiative_allow_routine_tasks", False):
                return PolicyDecision.EXECUTE
            return PolicyDecision.SUGGEST_ONLY

        if action_type == "suggestion":
            return PolicyDecision.EXECUTE

        if action_type == "external_comms":
            if boundaries.get("initiative_ask_external_comms", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        if action_type == "file_change":
            if boundaries.get("initiative_ask_file_changes", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        if action_type == "sensitive":
            if boundaries.get("initiative_ask_sensitive", False):
                return PolicyDecision.DRAFT_AND_ASK
            return PolicyDecision.EXECUTE

        # Unknown action type at own level — skip
        return PolicyDecision.SKIP

    # Unknown level — skip
    return PolicyDecision.SKIP


_MIGRATION_MAP = {
    "passive": "observe",
    "assistive": "assist",
    "proactive": "operate",
    "autonomous": "own",
}

_CURRENT_VALUES = {"observe", "assist", "operate", "own"}


def migrate_initiative_level(old_value: str) -> str:
    """
    Migrate a legacy initiative_level string to the current vocabulary.

    passive    → observe
    assistive  → assist
    proactive  → operate
    autonomous → own

    Already-current values (observe/assist/operate/own) are returned unchanged.
    Unknown values are returned unchanged.
    """
    if old_value in _CURRENT_VALUES:
        return old_value
    return _MIGRATION_MAP.get(old_value, old_value)
