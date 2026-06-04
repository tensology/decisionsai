"""Tests for filesystem path redaction in conversational text."""

from distr.core.agent.services.llm.text_utils import (
    _PATH_REDACT_PLACEHOLDER,
    clean_model_text_for_chat,
    clean_text_for_tts,
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


def test_clean_text_for_tts_turns_residual_slashes_into_spacing():
    raw = r"Use app/src/audio.ts or alpha\beta for copied text from 05/12."
    out = clean_text_for_tts(raw, spoken_prose=True)

    assert "/" not in out
    assert "\\" not in out
    assert "app src audio.ts" in out
    assert "alpha beta" in out
    assert "05 12" in out


def test_clean_text_for_tts_replaces_urls_before_slash_spacing():
    out = clean_text_for_tts("Open https://example.com/docs/api now.", spoken_prose=True)

    assert out == "Open a web link now."


def test_path_redaction_does_not_claim_a_file_was_saved():
    raw = "Active project: Tensology (/Users/paul/development/TENSOLOGY/www.tensology.com)."

    out = clean_text_for_tts(raw, spoken_prose=True)

    assert "saved a file" not in out.lower()
    assert "a local path" in out
