"""Tests for filesystem path redaction in conversational text."""

from distr.core.agent.services.llm.text_utils import (
    _PATH_REDACT_PLACEHOLDER,
    clean_model_text_for_chat,
    clean_text_for_tts,
    ensure_line_sentence_boundaries_for_tts,
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
    assert out == "Fix: use dictation mode. Next. Do the thing."


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


def test_ensure_line_sentence_boundaries_adds_period_for_soft_breaks():
    raw = "Dear team\nPlease review the budget\nThanks"
    out = ensure_line_sentence_boundaries_for_tts(raw)
    assert out == "Dear team.\nPlease review the budget.\nThanks."


def test_ensure_line_sentence_boundaries_keeps_existing_stops():
    raw = "First line.\nSecond line!\nThird line?\nAlready done..."
    out = ensure_line_sentence_boundaries_for_tts(raw)
    assert out == raw


def test_clean_text_for_tts_email_lines_become_distinct_sentences():
    raw = "Subject: Quarterly update\nWe met with the client\nNo issues found"
    out = clean_text_for_tts(raw, spoken_prose=True)
    assert out == "Subject: Quarterly update. We met with the client. No issues found."


def test_clean_text_for_tts_strips_inline_markdown_headings():
    raw = "It was working on: # Pick up brief ## Handoff Tightened the now-playing bar."
    out = clean_text_for_tts(raw, spoken_prose=True)
    assert "#" not in out
    assert "Pick up brief Handoff Tightened the now-playing bar" in out


def test_spoken_task_summary_prefers_ticket_title():
    from distr.core.agent.services.llm.text_utils import spoken_task_summary

    instruction = "# Pick up brief\n\n## Handoff\n\nSome notes\n\n--- PRIMARY TASK ---\nTighten the now-playing bar"
    out = spoken_task_summary(instruction, ticket_title="Tighten the now-playing bar")
    assert out == "Tighten the now-playing bar"


def test_spoken_task_summary_uses_primary_task_without_title():
    from distr.core.agent.services.llm.text_utils import spoken_task_summary

    instruction = (
        "# Pick up brief\n\n## Handoff\n\nNotes\n\n---\n\n"
        "[KANBAN TICKET CONTEXT]\n\n--- PRIMARY TASK ---\n"
        "Tighten the now-playing bar\n\nMake the scrubber easier to grab."
    )
    out = spoken_task_summary(instruction)
    assert out == "Tighten the now-playing bar"


def test_spoken_result_summary_shortens_model_errors():
    from distr.core.agent.services.llm.text_utils import spoken_result_summary

    raw = "Cannot use this model: gpt-5-codex. Available models: gpt-4o, auto."
    out = spoken_result_summary(raw)
    assert "Available models" not in out
    assert "isn't available" in out
