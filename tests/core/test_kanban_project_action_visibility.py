from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_project_bound_card_actions_are_hidden_without_project():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")

    assert "if (config.hidden) return \"\";" in js
    assert 'keyClass: "kb-act-copy"' in js
    assert 'keyClass: "kb-act-agent"' in js
    assert 'keyClass: "kb-act-cli"' in js
    assert 'keyClass: "kb-act-project"' in js
    assert 'keyClass: "kb-act-workflow"' in js
    assert 'keyClass: "kb-act-transfer"' in js
    assert 'keyClass: "kb-act-delete"' in js
    assert 'tooltip: "Send to Orchestrator"' in js
    assert 'tooltip: "Copy title and description"' in js
    assert 'tooltip: "Copy to local board"' in js
    assert "hidden: !opts.hasProject" in js
    assert "hidden: !opts.canTransfer" in js
    assert "hidden: !opts.canDelete" in js
    assert "link ticket/board to project" not in js


def test_kanban_modal_project_actions_toggle_hidden_not_disabled_state():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert 'cliBtn.classList.toggle("hidden", !canPush)' in js
    assert 'projectBtn.classList.toggle("hidden", !canPush)' in js
    assert 'cliBtn.disabled = false' in js
    assert 'projectBtn.disabled = false' in js
    assert 'cliBtn.title = "Run with Cursor/Codex"' in js
    assert 'projectBtn.title = "Send to Project"' in js


def test_kanban_ticket_orchestrator_handoff_opens_chat_thread():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert 'showSnackbar("Sending ticket to the orchestrator…"' in js
    assert 'apiFetch("/api/chats/" + chatId + "/load-in-agent"' in js
    assert 'apiFetch("/api/chats/" + chatId + "/send-to-agent"' in js
    assert 'body: JSON.stringify({ message: starting, speak: true })' in js
    assert 'window.location.href = "/chat/?id=" + encodeURIComponent(String(targetChatId));' in js


def test_kanban_modal_footer_uses_current_ticket_action_labels():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")

    assert 'title="Copy title and description" aria-label="Copy title and description"' in html
    assert 'title="Send to Orchestrator" aria-label="Send to Orchestrator"' in html
    assert 'title="Run with Cursor/Codex" aria-label="Run with Cursor/Codex"' in html
    assert 'title="Send to Project" aria-label="Send to Project"' in html
    assert 'title="Send to Workflow" aria-label="Send to Workflow"' in html
    assert 'title="Copy to local board" aria-label="Copy to local board"' in html


def test_kanban_orchestrator_prompt_is_active_and_project_aware():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert "[Ticket Board — orchestrator engage this ticket]" in js
    assert "start of a real, spoken conversation about this exact ticket and its linked project" in js
    assert "use the available project/local-context tools to resolve the project" in js
    assert "confirm whether a local folder exists" in js
    assert "If Cursor/Codex/IDE session tools are available" in js
    assert "Do not ask 3-5 generic questions" in js
    assert "buildTicketDiscussionStartingQuestion(ticket, !!isLocal, boardLabel, src, currentBoardData || {})" in js
    assert "linked_project_id: linkedProjectId" in js
    assert "linked_project_name: linkedProjectId ? linkedProjectName : \"\"" in js


def test_kanban_api_payloads_expose_project_context_for_orchestrator():
    py = (ROOT / "distr/gui/web/routes/kanban.py").read_text(encoding="utf-8")

    assert "def _project_context_payload" in py
    assert "default_project_name" in py
    assert "default_project_folder" in py
    assert '**_project_context_payload(linked_project, "linked")' in py
