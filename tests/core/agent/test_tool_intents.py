from distr.core.agent.tool_intents import (
    forced_tool_names_for_text,
    is_explicit_create_action_request,
    is_saved_action_run_or_list_request,
    is_workflow_follow_up,
    pair_saved_action_tools,
)


def test_screenshot_intent_forces_screenshot_analyzer() -> None:
    assert "screenshot_analyzer" in forced_tool_names_for_text(
        "I'm trying to delete projects. Take a screenshot and assist me."
    )
    assert "screenshot_analyzer" in forced_tool_names_for_text("what do you see on screen")


def test_gmail_attachment_intent_forces_google_workspace() -> None:
    assert "google_workspace" in forced_tool_names_for_text(
        "Get Julie's Gmail attachment and put it in my downloads folder."
    )


def test_google_connection_status_forces_google_workspace() -> None:
    for request in (
        "Are you connected to my Gmail?",
        "Can you check whether Google Calendar is working?",
        "Try to access Google Workspace again.",
    ):
        assert "google_workspace" in forced_tool_names_for_text(request)


def test_location_specific_business_lookup_forces_web_search() -> None:
    request = (
        "Can you find me in Claremont, Cape Town, near Cavendish Square, "
        "a print shop and get me a list of them?"
    )

    assert "web_search" in forced_tool_names_for_text(request)


def test_forces_clipboard_for_read_and_write_requests():
    assert "clipboard_action" in forced_tool_names_for_text("read my clipboard")
    assert "clipboard_action" in forced_tool_names_for_text("read the clipboard and let's talk about it")
    assert "clipboard_action" in forced_tool_names_for_text("set clipboard to hello")
    assert "clipboard_action" in forced_tool_names_for_text("write hello into my clipboard")


def test_clipboard_to_calendar_instruction_forces_both_tools():
    forced = forced_tool_names_for_text(
        "Go and ingest my clipboard and then please create the event in Google Calendar."
    )

    assert forced == ["clipboard_action", "google_workspace"]


def test_calendar_event_follow_ups_are_recognized():
    assert is_workflow_follow_up("Yes.")
    assert is_workflow_follow_up("Yeah, can you?")
    assert is_workflow_follow_up("Can you?")
    assert is_workflow_follow_up("Try again.")
    assert is_workflow_follow_up("Create the fucking event.")
    assert is_workflow_follow_up("do it, read or ingest the clipboard and do it.")
    assert is_workflow_follow_up("First option.")
    assert is_workflow_follow_up("I want you to perform the second option")
    assert not is_workflow_follow_up("Create a ticket for this bug")


def test_action_follow_ups_allow_leading_so_then_okay_yes():
    assert is_workflow_follow_up("run it")
    assert is_workflow_follow_up("do that")
    assert is_workflow_follow_up("Okay, can you run it?")
    assert is_workflow_follow_up("So do that.")
    assert is_workflow_follow_up("yes run it")
    assert is_workflow_follow_up("then do that")
    assert is_workflow_follow_up("okay, do that")


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
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "Turn on Jira morning intake"
    )
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "Enable the Jira intake automation from email"
    )
    assert "proactive_orchestrator" in forced_tool_names_for_text(
        "Run Jira intake now"
    )


def test_forces_work_ops_for_intake_run_and_client_send():
    assert "work_ops" in forced_tool_names_for_text("What's coming in from work intake?")
    assert "work_ops" in forced_tool_names_for_text("Run ticket 42")
    assert "work_ops" in forced_tool_names_for_text("Send it to the client")
    assert "work_ops" in forced_tool_names_for_text("work status of the ticket")


def test_forces_ticket_tool_for_local_and_remote_ticket_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    assert "create_ticket" in forced_tool_names_for_text("create a ticket for this bug")
    assert "create_ticket" in forced_tool_names_for_text("create a Jira ticket for the login bug")
    assert "create_ticket" in forced_tool_names_for_text("make a Trello card for the UI bug")
    assert "create_ticket" in forced_tool_names_for_text("move ticket 12 from the wrong board to DecisionsAI")
    assert "create_ticket" in forced_tool_names_for_text("transfer this card to the ThatShirtShow board")
    assert "create_ticket" in forced_tool_names_for_text("merge those two tickets")
    assert "create_ticket" in forced_tool_names_for_text("Merge tickets 290 and 328 on Merrypak")
    assert "create_ticket" in forced_tool_names_for_text("move the backlog tickets into Current")
    assert "create_ticket" in forced_tool_names_for_text(
        "create_ticket tool for Kanban board operations (list_tickets, move_ticket, delete_ticket)"
    )
    assert "create_ticket" in forced_tool_names_for_text(
        "Break this project down into tickets and then run the work."
    )
    assert "create_ticket" in forced_tool_names_for_text(
        "Convert the request into separate work items before implementation."
    )
    assert "create_ticket" in forced_tool_names_for_text(
        "Scope this into clear, independently executable tickets linked to the workflow."
    )


def test_forces_ticket_tool_for_whatsapp_agent_requests(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    assert "create_ticket" in forced_tool_names_for_text("list WhatsApp messages from the client")
    assert "create_ticket" in forced_tool_names_for_text("create a ticket from those WhatsApp messages")
    assert "create_ticket" in forced_tool_names_for_text("show WhatsApp context for this thread")
    assert "create_ticket" in forced_tool_names_for_text(
        "No, the MP Web Dev Group, there is a message from Carmen and has a screenshot on it."
    )


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


def test_forces_special_key_for_keypress_requests():
    assert "special_key" in forced_tool_names_for_text("press the space bar")
    assert "special_key" in forced_tool_names_for_text("press enter")
    assert "special_key" in forced_tool_names_for_text("spacebar")


def test_forces_desktop_control_tools_for_audited_phrases():
    window_requests = (
        "Can you minimize this window?",
        "Can you maximize Codex?",
        "Move the terminal to the left screen",
        "Move the terminal to my second desktop",
        "Open up my second desktop",
    )
    for request in window_requests:
        assert "window_management" in forced_tool_names_for_text(request), request

    assert "launch_app" in forced_tool_names_for_text("Can you bring up Spotify?")
    assert "launch_app" in forced_tool_names_for_text("Switch to Chrome")
    assert "open_page" in forced_tool_names_for_text("Bring up the web UI")
    assert "open_project" in forced_tool_names_for_text("Bring up the project")
    assert "create_ticket" in forced_tool_names_for_text("Bring up the ticket board")
    assert "smart_open" in forced_tool_names_for_text("Can you open up my Downloads folder?")
    assert "media_control" in forced_tool_names_for_text("Can you reload Brave?")

    mouse_tools = forced_tool_names_for_text("Move the mouse to the Save button")
    assert "find_element" in mouse_tools
    assert "move_to_element" in mouse_tools
    assert "screenshot_analyzer" in mouse_tools


def test_forces_cursor_ticket_for_decisionsai_ticket_only_in_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "True")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert forced[0] == "create_cursor_ticket"
    assert "create_ticket" not in forced

    monkeypatch.setenv("DEBUG", "False")

    forced = forced_tool_names_for_text("make a ticket for DecisionsAI to fix Telegram")

    assert "create_ticket" in forced
    assert "create_cursor_ticket" not in forced


_TONIGHT_LIST_OR_RUN = (
    "What's the list of actions that I can actually run?",
    "I want to list the list of actions that you have and that I've created, you know, custom actions.",
    "Okay, can you run it?",
)


def test_tonight_action_utterances_force_list_and_play_action():
    for request in _TONIGHT_LIST_OR_RUN:
        forced = forced_tool_names_for_text(request)
        assert "list_actions" in forced, request
        assert "play_action" in forced, request


def test_run_it_follow_ups_force_list_and_play_action():
    for request in ("Okay, can you run it?", "yes run it", "run it"):
        forced = forced_tool_names_for_text(request)
        assert "list_actions" in forced, request
        assert "play_action" in forced, request


def test_so_do_that_is_a_run_or_list_follow_up_not_a_create():
    assert is_workflow_follow_up("So do that.")
    assert is_saved_action_run_or_list_request("So do that.")
    assert not is_explicit_create_action_request("So do that.")
    assert "list_actions" not in forced_tool_names_for_text("So do that.")


def test_pair_saved_action_tools_includes_both():
    assert pair_saved_action_tools(["list_actions"]) == ["list_actions", "play_action"]
    assert pair_saved_action_tools(["play_action"]) == ["play_action", "list_actions"]
    assert pair_saved_action_tools(["run_workflow"]) == ["run_workflow"]


def test_saved_action_rules_do_not_steal_unrelated_runs_or_creates():
    assert "play_action" not in forced_tool_names_for_text("Run ticket 42")
    assert "list_actions" not in forced_tool_names_for_text("list scheduled actions")
    assert "play_action" not in forced_tool_names_for_text("list scheduled actions")
    assert "list_actions" not in forced_tool_names_for_text("Create an action named Morning setup")
    assert "play_action" not in forced_tool_names_for_text("Create an action named Morning setup")
    assert is_explicit_create_action_request("Create an action named Morning setup")


def test_tonight_list_and_run_requests_are_saved_action_run_or_list():
    for request in _TONIGHT_LIST_OR_RUN + ("So do that.",):
        assert is_saved_action_run_or_list_request(request), request
        assert not is_explicit_create_action_request(request), request
