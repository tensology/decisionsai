from distr.core.workflow.wait_handoff import build_wait_handoff_text, wait_handoff_voice_text


def test_ide_handoff_voice_is_short_not_cli_dump():
    result = (
        "Project CLI backend: cursor_ide\n"
        "Project: 12\n"
        "Status: waiting in IDE\n\n"
        "Opened Cursor IDE with your work packet.\n"
        "Packet: /tmp/decisionsai_cursor_ide.md\n"
        "IDE opened: yes"
    )
    handoff = build_wait_handoff_text("Implement feature", result, run_id=99)
    assert handoff["is_ide_handoff"] is True
    assert handoff["tts"] == "I opened Cursor for Implement feature."
    assert "Packet:" not in handoff["tts"]
    assert "continue, retry, skip" not in handoff["tts"]
    assert "Packet:" in handoff["history_entry"]


def test_generic_wait_voice_skips_raw_output():
    result = "Validation passed. I need approval before deploying."
    assert wait_handoff_voice_text(step_name="Deploy", result_text=result) == "Deploy needs your input."
