from enum import Enum


class PolicyDecision(Enum):
    EXECUTE = "execute"
    DRAFT_AND_ASK = "draft_and_ask"
    SUGGEST_ONLY = "suggest_only"
    SKIP = "skip"


def evaluate(action, level: str, boundaries: dict) -> PolicyDecision:
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
