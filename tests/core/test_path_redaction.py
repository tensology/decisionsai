"""Tests for filesystem path redaction in conversational text."""

from distr.core.agent.services.llm.text_utils import (
    _PATH_REDACT_PLACEHOLDER,
    clean_model_text_for_chat,
    redact_filesystem_paths_for_conversation,
)


def test_redact_unix_downloads_path():
    raw = (
        "File created:\n"
        "/Users/paul/Downloads/workflow_failure_and_workflow_step_type_fix_ticket.md"
    )
    out = redact_filesystem_paths_for_conversation(raw)
    assert "/Users/" not in out
    assert _PATH_REDACT_PLACEHOLDER in out


def test_redact_preserves_url_hosts():
    text = "See https://example.com/docs/api for details."
    out = redact_filesystem_paths_for_conversation(text)
    assert "example.com" in out


def test_redact_windows_path():
    text = r"Saved to C:\Users\paul\Downloads\report.pdf"
    out = redact_filesystem_paths_for_conversation(text)
    assert r"C:\Users" not in out
    assert _PATH_REDACT_PLACEHOLDER in out


def test_clean_model_text_for_chat_strips_markdown_noise():
    raw = "**Fix:** use `dictation` mode.\n\n### Next\n- Do the thing"
    out = clean_model_text_for_chat(raw)
    assert "*" not in out
    assert "`" not in out
    assert "#" not in out
    assert out == "Fix: use dictation mode. Next Do the thing"
