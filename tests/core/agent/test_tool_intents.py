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


def test_forces_codex_thread_context_for_codex_conversation_requests():
    assert "codex_thread_context" in forced_tool_names_for_text(
        "If I ask you to work with one of my conversations inside of codecs, can you do that?"
    )
    assert "codex_thread_context" in forced_tool_names_for_text(
        "Bring in the Codex thread and turn it into a ticket"
    )
    assert "codex_thread_context" in forced_tool_names_for_text(
        "What am I doing inside Codex right now?"
    )


def test_forces_proactive_orchestrator_for_workload_and_source_triage():
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "Check Gmail, Slack, WhatsApp, Trello and Jira and tell me what is important."
    )
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "Where am I with the workload in Cursor and Codex?"
    )
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "What is my daily plan from emails, WhatsApp, tickets, boards and projects?"
    )


def test_forces_ticket_tool_for_local_and_remote_ticket_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    assert "create_ticket" in forced_tool_names_for_text("create a ticket for this bug")
    assert "create_ticket" in forced_tool_names_for_text("create a Jira ticket for the login bug")
    assert "create_ticket" in forced_tool_names_for_text("make a Trello card for the UI bug")
    assert "create_ticket" in forced_tool_names_for_text("move ticket 12 from the wrong board to DecisionsAI")
    assert "create_ticket" in forced_tool_names_for_text("transfer this card to the ThatShirtShow board")


def test_forces_ticket_tool_for_whatsapp_agent_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    assert "create_ticket" in forced_tool_names_for_text("list WhatsApp messages from the client")
    assert "create_ticket" in forced_tool_names_for_text("create a ticket from those WhatsApp messages")
    assert "create_ticket" in forced_tool_names_for_text("show WhatsApp context for this thread")


def test_forces_file_operations_for_ticket_file_destinations(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    forced = forced_tool_names_for_text("create a ticket in Downloads for the onboarding bug")

    assert "file_operations" in forced
    assert "create_ticket" not in forced


def test_forces_type_text_for_type_out_ticket_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    forced = forced_tool_names_for_text("type out a ticket for the login bug")

    assert "type_text" in forced
    assert "create_ticket" not in forced


def test_forces_cursor_ticket_for_decisionsai_ticket_only_in_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "True")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert forced[0] == "create_cursor_ticket"
    assert "create_ticket" not in forced

    monkeypatch.setenv("DEBUG", "False")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert "create_ticket" in forced
    assert "create_cursor_ticket" not in forced
