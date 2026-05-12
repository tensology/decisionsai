from distr.core.agent.tool_intents import forced_tool_names_for_text


def test_forces_clipboard_for_read_and_write_requests():
    assert "clipboard_action" in forced_tool_names_for_text("read my clipboard")
    assert "clipboard_action" in forced_tool_names_for_text("read the clipboard and let's talk about it")
    assert "clipboard_action" in forced_tool_names_for_text("set clipboard to hello")
    assert "clipboard_action" in forced_tool_names_for_text("write hello into my clipboard")


def test_forces_file_convert_automation_and_exit_tools():
    assert "file_operations" in forced_tool_names_for_text("rename that file to report.md")
    assert "convert_document" in forced_tool_names_for_text("turn this into a word document")
    assert "create_step_runner" in forced_tool_names_for_text("create an automation")
    assert "exit_app" in forced_tool_names_for_text("exit the app")


def test_forces_ticket_tool_for_local_and_remote_ticket_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    assert "create_ticket" in forced_tool_names_for_text("create a ticket for this bug")
    assert "create_ticket" in forced_tool_names_for_text("create a Jira ticket for the login bug")
    assert "create_ticket" in forced_tool_names_for_text("make a Trello card for the UI bug")


def test_forces_cursor_ticket_for_decisionsai_ticket_only_in_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "True")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert forced[0] == "create_cursor_ticket"
    assert "create_ticket" not in forced

    monkeypatch.setenv("DEBUG", "False")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert "create_ticket" in forced
    assert "create_cursor_ticket" not in forced
