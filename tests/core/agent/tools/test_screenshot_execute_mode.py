from distr.core.agent.services.vision.intent_classifier import VisionIntent
from distr.core.agent.services.vision.action_mode import resolve_execute_action


def test_execute_action_defaults_false_for_locate_intent():
    locate_intents = {VisionIntent.LOCATE}
    result = resolve_execute_action(None, VisionIntent.LOCATE, locate_intents)
    assert result is False


def test_execute_action_defaults_true_for_non_locate_intent():
    locate_intents = {VisionIntent.LOCATE}
    result = resolve_execute_action(None, VisionIntent.CLICK_ELEMENT, locate_intents)
    assert result is True


def test_execute_action_explicit_override_true():
    locate_intents = {VisionIntent.LOCATE}
    result = resolve_execute_action(True, VisionIntent.LOCATE, locate_intents)
    assert result is True
