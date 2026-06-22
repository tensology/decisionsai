"""
Initiative policy gate: map (action, level, boundaries, optional rubric) → PolicyDecision.

Precedence (documented for TASK 3–4):
  1. Hard guards: blocked action types, cooldown / duplicate proposals.
  2. ``always_require_ask_for`` → DRAFT_AND_ASK.
  3. Low confidence → SUGGEST_ONLY (payload / scope_policy).
  4. High-risk file/external/sensitive → DRAFT_AND_ASK when thresholds say so.
  5. Boundary flags (initiative_ask_*) encoded in level/action branches — they
     produce DRAFT_AND_ASK / EXECUTE / SUGGEST_ONLY in the legacy paths below.
  6. Rubric (when present): merged with legacy operate/own outcomes by taking the
     *more restrictive* decision (SKIP > SUGGEST_ONLY > DRAFT_AND_ASK > EXECUTE).
     Rubric never upgrades past a blocked capability (e.g. routine_task without allow).
  7. observe / assist without rubric: legacy passive behaviour (SKIP / SUGGEST_ONLY).
  8. observe / assist with rubric: rubric thresholds only (DESIGN §2.1).
"""

from enum import Enum

from distr.core.initiative.rubric import RubricScore


class PolicyDecision(Enum):
    EXECUTE = "execute"
    DRAFT_AND_ASK = "draft_and_ask"
    SUGGEST_ONLY = "suggest_only"
    SKIP = "skip"


_RUBRIC_MERGE_RANK = {
    PolicyDecision.EXECUTE: 0,
    PolicyDecision.DRAFT_AND_ASK: 1,
    PolicyDecision.SUGGEST_ONLY: 2,
    PolicyDecision.SKIP: 3,
}


def _more_restrictive(a: PolicyDecision, b: PolicyDecision) -> PolicyDecision:
    """Prefer the safer / more user-gated outcome."""
    return a if _RUBRIC_MERGE_RANK[a] >= _RUBRIC_MERGE_RANK[b] else b


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


def _extract_rubric(action) -> RubricScore | None:
    r = getattr(action, "rubric", None)
    if isinstance(r, RubricScore):
        return r
    payload = getattr(action, "payload", None)
    if isinstance(payload, dict):
        return RubricScore.from_payload(payload.get("rubric"))
    return None


def _evaluate_operate(action, boundaries: dict) -> PolicyDecision:
    action_type = action.action_type

    if action_type == "none":
        return PolicyDecision.SKIP

    if action_type == "routine_task":
        if boundaries.get("initiative_allow_routine_tasks", False):
            return PolicyDecision.EXECUTE
        return PolicyDecision.SUGGEST_ONLY

    if action_type == "suggestion":
        return PolicyDecision.EXECUTE

    if action_type in ("board_triage", "message_triage", "email_triage"):
        return PolicyDecision.EXECUTE

    if action_type == "ticket_lane_move":
        if boundaries.get("initiative_allow_ticket_lane_moves", False):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

    if action_type == "workflow_start":
        if (
            boundaries.get("initiative_allow_routine_tasks", False)
            and boundaries.get("initiative_allow_workflow_start", False)
        ):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

    if action_type == "project_cli_task":
        if (
            boundaries.get("initiative_allow_routine_tasks", False)
            and boundaries.get("initiative_allow_project_cli", False)
        ):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

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

    return PolicyDecision.SKIP


def _evaluate_own(action, boundaries: dict) -> PolicyDecision:
    action_type = action.action_type

    if action_type == "none":
        return PolicyDecision.SKIP

    if action_type == "routine_task":
        if boundaries.get("initiative_allow_routine_tasks", False):
            return PolicyDecision.EXECUTE
        return PolicyDecision.SUGGEST_ONLY

    if action_type == "suggestion":
        return PolicyDecision.EXECUTE

    if action_type in ("board_triage", "message_triage", "email_triage"):
        return PolicyDecision.EXECUTE

    if action_type == "ticket_lane_move":
        if boundaries.get("initiative_allow_ticket_lane_moves", False):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

    if action_type == "workflow_start":
        if (
            boundaries.get("initiative_allow_routine_tasks", False)
            and boundaries.get("initiative_allow_workflow_start", False)
        ):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

    if action_type == "project_cli_task":
        if (
            boundaries.get("initiative_allow_routine_tasks", False)
            and boundaries.get("initiative_allow_project_cli", False)
        ):
            return PolicyDecision.EXECUTE
        return PolicyDecision.DRAFT_AND_ASK

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

    return PolicyDecision.SKIP


def evaluate(action, level: str, boundaries: dict, policy_context: dict | None = None) -> PolicyDecision:
    """
    Map (action, level, boundaries) to a PolicyDecision.

    action      — object with ``action_type``, optional ``payload``, optional ``rubric``
    level       — observe | assist | operate | own
    boundaries  — dict with initiative_allow_* / initiative_ask_* keys
    """
    action_type = action.action_type
    policy = _read_policy_context(action, boundaries, policy_context)

    if action_type == "automation_recommendation":
        return PolicyDecision.SKIP if level == "observe" else PolicyDecision.DRAFT_AND_ASK

    if action_type in policy["always_block_action_types"]:
        return PolicyDecision.SKIP
    if policy["cooldown_active"] or policy["duplicate_recent"]:
        return PolicyDecision.SKIP
    if action_type in policy["always_require_ask_for"]:
        return PolicyDecision.DRAFT_AND_ASK
    if policy["confidence"] < policy["minimum_confidence_to_execute"]:
        return PolicyDecision.SUGGEST_ONLY
    if (
        action_type in (
            "external_comms",
            "file_change",
            "sensitive",
            "ticket_lane_move",
            "workflow_start",
            "project_cli_task",
        )
        and _risk_at_least(policy["risk_level"], policy["minimum_risk_to_require_ask"])
    ):
        return PolicyDecision.DRAFT_AND_ASK

    rubric_score = _extract_rubric(action)

    if level == "observe":
        if rubric_score is None:
            return PolicyDecision.SKIP
        return rubric_score.policy_decision("observe")

    if level == "assist":
        if rubric_score is None:
            return PolicyDecision.SUGGEST_ONLY
        return rubric_score.policy_decision("assist")

    if level == "operate":
        classic = _evaluate_operate(action, boundaries)
        if rubric_score is None:
            return classic
        return _more_restrictive(classic, rubric_score.policy_decision("operate"))

    if level == "own":
        classic = _evaluate_own(action, boundaries)
        if rubric_score is None:
            return classic
        return _more_restrictive(classic, rubric_score.policy_decision("own"))

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
