
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from distr.core.agent.services.llm.fast_action_detector import FastActionDetector, ActionType


def test_summarize_regex_clipboard_and_read_variants() -> None:
    detector = FastActionDetector()

    tts_cases = [
        ("summarize from clipboard and read", "tts"),
        ("summarize and read from clipboard", "tts"),
        ("summarize and read this", "tts"),
    ]
    done_cases = [
        ("summarize from clipboard", "done"),
        ("summarize this", "done"),
        ("can you summarize this", "done"),
    ]

    for text, expected_type in tts_cases + done_cases:
        result = detector.detect(text)
        assert result.action_type == ActionType.CLIPBOARD_SUMMARIZE, (
            f"{text!r}: expected CLIPBOARD_SUMMARIZE, got {result.action_type}"
        )
        assert result.response_type == expected_type, (
            f"{text!r}: expected response_type {expected_type!r}, got {result.response_type!r}"
        )
