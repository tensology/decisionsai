import pytest
from distr.core.initiative.policy import evaluate, migrate_initiative_level, PolicyDecision
from distr.core.initiative.proposed_action import ProposedAction


def make_action(action_type):
    return ProposedAction(action_type=action_type, description="test")


def full_boundaries(allow_telegram=True, allow_routine=True, ask_external=False, ask_file=False, ask_sensitive=False):
    return {
        "initiative_allow_telegram": allow_telegram,
        "initiative_allow_routine_tasks": allow_routine,
        "initiative_ask_external_comms": ask_external,
        "initiative_ask_file_changes": ask_file,
        "initiative_ask_sensitive": ask_sensitive,
    }


# --- observe level ---
class TestObserveLevel:
    def test_observe_any_action_is_skip(self):
        for at in ["suggestion", "routine_task", "external_comms", "file_change", "sensitive", "none"]:
            assert evaluate(make_action(at), "observe", full_boundaries()) == PolicyDecision.SKIP


# --- assist level ---
class TestAssistLevel:
    def test_assist_any_action_is_suggest_only(self):
        for at in ["suggestion", "routine_task", "external_comms", "file_change", "sensitive", "none"]:
            assert evaluate(make_action(at), "assist", full_boundaries()) == PolicyDecision.SUGGEST_ONLY


# --- operate level ---
class TestOperateLevel:
    def test_routine_task_allowed(self):
        assert evaluate(make_action("routine_task"), "operate", full_boundaries(allow_routine=True)) == PolicyDecision.EXECUTE

    def test_routine_task_blocked(self):
        assert evaluate(make_action("routine_task"), "operate", full_boundaries(allow_routine=False)) == PolicyDecision.SUGGEST_ONLY

    def test_suggestion_execute(self):
        assert evaluate(make_action("suggestion"), "operate", full_boundaries()) == PolicyDecision.EXECUTE

    def test_external_comms_ask_true(self):
        assert evaluate(make_action("external_comms"), "operate", full_boundaries(ask_external=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_external_comms_ask_false(self):
        assert evaluate(make_action("external_comms"), "operate", full_boundaries(ask_external=False)) == PolicyDecision.EXECUTE

    def test_file_change_ask_true(self):
        assert evaluate(make_action("file_change"), "operate", full_boundaries(ask_file=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_file_change_ask_false(self):
        assert evaluate(make_action("file_change"), "operate", full_boundaries(ask_file=False)) == PolicyDecision.EXECUTE

    def test_sensitive_ask_true(self):
        assert evaluate(make_action("sensitive"), "operate", full_boundaries(ask_sensitive=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_sensitive_ask_false(self):
        assert evaluate(make_action("sensitive"), "operate", full_boundaries(ask_sensitive=False)) == PolicyDecision.EXECUTE

    def test_none_is_skip(self):
        assert evaluate(make_action("none"), "operate", full_boundaries()) == PolicyDecision.SKIP


# --- own level ---
class TestOwnLevel:
    def test_routine_task_allowed(self):
        assert evaluate(make_action("routine_task"), "own", full_boundaries(allow_routine=True)) == PolicyDecision.EXECUTE

    def test_routine_task_blocked(self):
        assert evaluate(make_action("routine_task"), "own", full_boundaries(allow_routine=False)) == PolicyDecision.SUGGEST_ONLY

    def test_suggestion_execute(self):
        assert evaluate(make_action("suggestion"), "own", full_boundaries()) == PolicyDecision.EXECUTE

    def test_external_comms_ask_true(self):
        assert evaluate(make_action("external_comms"), "own", full_boundaries(ask_external=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_external_comms_ask_false(self):
        assert evaluate(make_action("external_comms"), "own", full_boundaries(ask_external=False)) == PolicyDecision.EXECUTE

    def test_file_change_ask_true(self):
        assert evaluate(make_action("file_change"), "own", full_boundaries(ask_file=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_file_change_ask_false(self):
        assert evaluate(make_action("file_change"), "own", full_boundaries(ask_file=False)) == PolicyDecision.EXECUTE

    def test_sensitive_ask_true(self):
        assert evaluate(make_action("sensitive"), "own", full_boundaries(ask_sensitive=True)) == PolicyDecision.DRAFT_AND_ASK

    def test_sensitive_ask_false(self):
        assert evaluate(make_action("sensitive"), "own", full_boundaries(ask_sensitive=False)) == PolicyDecision.EXECUTE

    def test_none_is_skip(self):
        assert evaluate(make_action("none"), "own", full_boundaries()) == PolicyDecision.SKIP


# --- migration ---
class TestMigrateInitiativeLevel:
    def test_passive_to_observe(self):
        assert migrate_initiative_level("passive") == "observe"

    def test_assistive_to_assist(self):
        assert migrate_initiative_level("assistive") == "assist"

    def test_proactive_to_operate(self):
        assert migrate_initiative_level("proactive") == "operate"

    def test_autonomous_to_own(self):
        assert migrate_initiative_level("autonomous") == "own"

    def test_current_values_unchanged(self):
        for v in ["observe", "assist", "operate", "own"]:
            assert migrate_initiative_level(v) == v

    def test_unknown_value_unchanged(self):
        assert migrate_initiative_level("unknown_value") == "unknown_value"


class TestEnhancedPolicyGate:
    def test_blocked_action_type_skips(self):
        action = make_action("external_comms")
        decision = evaluate(
            action,
            "own",
            full_boundaries(ask_external=False),
            policy_context={"always_block_action_types": ["external_comms"]},
        )
        assert decision == PolicyDecision.SKIP

    def test_duplicate_or_cooldown_skips(self):
        action = make_action("routine_task")
        decision = evaluate(
            action,
            "operate",
            full_boundaries(allow_routine=True),
            policy_context={"duplicate_recent": True},
        )
        assert decision == PolicyDecision.SKIP

    def test_low_confidence_downgrades_to_suggestion(self):
        action = make_action("routine_task")
        action.payload = {"confidence": 0.2}
        decision = evaluate(
            action,
            "own",
            full_boundaries(allow_routine=True),
            policy_context={"minimum_confidence_to_execute": 0.5},
        )
        assert decision == PolicyDecision.SUGGEST_ONLY

    def test_high_risk_forces_draft_even_when_boundary_allows_execute(self):
        action = make_action("file_change")
        action.payload = {"risk_level": "high"}
        decision = evaluate(
            action,
            "operate",
            full_boundaries(ask_file=False),
            policy_context={"minimum_risk_to_require_ask": "high"},
        )
        assert decision == PolicyDecision.DRAFT_AND_ASK

    def test_scope_policy_overrides_from_payload(self):
        action = make_action("file_change")
        action.payload = {
            "scope_policy": {
                "always_require_ask_for": ["file_change"],
            }
        }
        decision = evaluate(
            action,
            "own",
            full_boundaries(ask_file=False),
        )
        assert decision == PolicyDecision.DRAFT_AND_ASK
