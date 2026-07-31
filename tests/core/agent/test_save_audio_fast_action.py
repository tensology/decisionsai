import pytest

from distr.core.agent.services.llm.fast_action_detector import (
    ActionType,
    detect_fast_action,
)


@pytest.mark.parametrize(
    "command",
    [
        "Save clipboard to audio.",
        "Can you save the clipboard to an audio file?",
        "Save the entire clipboard to audio as an MP3 in Downloads.",
        "Save the clipboard as audio as a WAV to Desktop.",
    ],
)
def test_explicit_clipboard_audio_commands_bypass_the_llm(command: str) -> None:
    action = detect_fast_action(command)

    assert action.action_type is ActionType.SAVE_AUDIO
    assert action.tool_name == "save_audio"
    assert action.tool_args["text"] == command
