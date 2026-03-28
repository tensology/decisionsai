import pytest
from distr.core.initiative.policy import evaluate, migrate_initiative_level, PolicyDecision
from distr.core.initiative.service import ProposedAction


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
