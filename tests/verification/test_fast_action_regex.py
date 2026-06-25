
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.agent.services.llm.fast_action_detector import (
    FastActionDetector,
    ActionType,
    detect_fast_action,
)


def test_screenshot_intent_forces_screenshot_analyzer() -> None:
    cases = [
        "Take a screenshot. I'm looking at the projects. Can you help me delete them?",
        "I want you to take a screenshot so you can see what I'm looking at.",
        "you take a screenshot and assist me",
        "what do you see on my screen",
    ]
    for text in cases:
        assert "screenshot_analyzer" in forced_tool_names_for_text(text), text


def test_direct_screenshot_with_extra_context_fast_actions() -> None:
    text = (
        "Take a screenshot. I'm looking at the projects. "
        "I want to be able to delete these projects. Can you just help me?"
    )
    result = detect_fast_action(text)
    assert result.action_type == ActionType.SCREENSHOT_ANALYZE
    assert result.tool_name == "screenshot_analyzer"
    assert result.confidence >= 0.9


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


def test_open_mermaid_viewer_fast_action() -> None:
    cases = [
        "open the mermaid viewer",
        "open mermaid viewer",
        "open diagram viewer",
        "you open the mermaid JS viewer",
    ]
    for text in cases:
        result = detect_fast_action(text)
        assert result.action_type == ActionType.OPEN_WINDOW, text
        assert result.tool_name == "open_page", text
        assert result.tool_args.get("page") == "diagram viewer", text


def test_document_convert_with_explicit_path_stays_fast_action() -> None:
    result = detect_fast_action("convert README.md to pdf")
    assert result.action_type == ActionType.DOCUMENT_CONVERT
    assert result.tool_name == "convert_document"
    assert result.tool_args.get("input_path") == "README.md"
    assert result.tool_args.get("output_format") == "pdf"


def test_document_convert_without_explicit_path_defers_to_llm(monkeypatch, tmp_path) -> None:
    vague_cases = [
        "convert this to pdf",
        "make a pdf of this",
        "export as pdf",
        "turn that into a word document",
    ]
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    for text in vague_cases:
        result = detect_fast_action(text)
        assert result.action_type == ActionType.UNKNOWN, text
        assert result.tool_name == "", text
