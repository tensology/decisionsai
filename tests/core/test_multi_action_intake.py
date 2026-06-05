from distr.core.agent.services.llm.bulk_instruction import (
    augment_bulk_instruction,
    profile_bulk_instruction,
)


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
