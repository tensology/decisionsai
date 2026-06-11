from distr.core.agent.services.llm.bulk_instruction import (
    augment_bulk_instruction,
    profile_bulk_instruction,
    should_bypass_fast_action_detection,
)
from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action
from distr.core.automation_orchestrator import automation_prompt


def test_bulk_instruction_profile_detects_large_multi_action_request():
    text = "\n".join(
        [f"Step {i}: execute this by opening the file, updating the row, then sending the result." for i in range(60)]
    )

    profile = profile_bulk_instruction(text)

    assert profile.is_bulk is True
    assert profile.line_count == 60
    assert profile.asks_execution is True


def test_bulk_instruction_augmentation_preserves_original_and_adds_tool_queue_rules():
    text = "\n".join([f"{i}. create the thing and verify it" for i in range(1, 60)])

    augmented = augment_bulk_instruction(text, source="chat")

    assert augmented.startswith("[Multi-Action Intake]")
    assert "ordered action queue" in augmented
    assert "Verify each material tool result" in augmented
    assert "[Original User Packet]" in augmented
    assert text in augmented


def test_short_single_action_request_is_not_augmented():
    text = "Open Chrome."

    assert augment_bulk_instruction(text) == text


def test_automation_run_action_instruction_bypasses_fast_action_detection():
    automation = {"name": "Morning macro", "instruction": "run action fuzzy"}
    prompt = augment_bulk_instruction(automation_prompt(automation), source="automation")

    assert should_bypass_fast_action_detection(prompt)

    class _StubMixin:
        _messages = [{"role": "user", "content": prompt}]
        _processed_fast_actions = set()
        _bypass_fast_actions_for_turn = False

        def _check_fast_actions(self):
            from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

            return LLMSharedMixin._check_fast_actions(self)

    assert _StubMixin()._check_fast_actions() is None


def test_direct_run_action_still_detects_fast_action():
    text = "run action fuzzy"

    assert not should_bypass_fast_action_detection(text)
    detected = detect_fast_action(text)
    assert detected.action_type == ActionType.ACTION_PLAY
    assert detected.tool_args.get("action_name") == "fuzzy"


def test_multi_action_packets_are_not_re_augmented():
    text = (
        'say "Hello"\n'
        "the move the mouse to the center of the screen on screen 1.\n"
        "the move the mouse to the center of the screen on screen 2."
    )
    wrapped = augment_bulk_instruction(text, source="automation")

    assert wrapped.startswith("[Multi-Action Intake]")
    assert augment_bulk_instruction(wrapped, source="chat") == wrapped


def test_short_ordered_desktop_actions_are_augmented():
    text = (
        'say "Hey babe, you are a sexy beautiful dude!"\n'
        "the move the mouse to the center of the screen on screen 1.\n"
        "the move the mouse to the center of the screen on screen 2.\n"
        "the move the mouse to the center of the screen on screen 3."
    )

    augmented = augment_bulk_instruction(text, source="automation")

    assert augmented.startswith("[Multi-Action Intake]")
    assert "ordered action queue" in augmented
    assert "one physical desktop action at a time" in augmented
    assert "Do not summarize repeated desktop actions into one best-effort action" in augmented
    assert text in augmented
