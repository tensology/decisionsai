from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KANBAN_JS = ROOT / "distr/gui/web/static/kanban/js/kanban.js"
ENGAGEMENT_PY = ROOT / "distr/core/kanban/ticket_orchestrator_engagement.py"
KANBAN_PY = ROOT / "distr/gui/web/routes/kanban.py"
SIGNALS_PY = ROOT / "distr/core/signals.py"
APP_SIGNALS_PY = ROOT / "distr/app/signals.py"


def test_ticket_discuss_stays_on_board_and_uses_engage_endpoint():
    js = KANBAN_JS.read_text(encoding="utf-8")
    assert 'apiFetch("/api/tickets/tickets/engage-orchestrator"' in js
    assert "local_board_id: localBoardId" in js
    assert 'window.location.href = "/chat/?id="' not in js
    assert "res.display_message" in js


def test_ticket_engagement_records_activity_not_user_message():
    py = ENGAGEMENT_PY.read_text(encoding="utf-8")
    assert "record_ticket_engagement_activity" in py
    assert "record_tool_execution" in py
    assert "ChatService.add_user_message" not in py
    assert "web_load_chat_and_process_requested" in py
    assert "web_load_chat_in_agent_requested" not in py


def test_engage_route_activates_board_and_project_before_dispatch():
    py = KANBAN_PY.read_text(encoding="utf-8")
    assert "activate_engagement_context" in py
    assert "emit_ticket_engagement_memory_event" in py
    assert "local_board_id" in py
    assert "_import_attachments_for_orchestrator_engagement" in py


def test_orchestrator_engagement_imports_jira_attachments_into_prompt():
    engagement = ENGAGEMENT_PY.read_text(encoding="utf-8")
    kanban = KANBAN_PY.read_text(encoding="utf-8")
    assert "attachment_markdown" in engagement
    assert "Attachments (imported to project folder)" in engagement
    assert "document_extractor" in engagement
    assert "_import_attachments_for_orchestrator_engagement" in kanban
    assert "_resolve_ticket_external_link_for_import" in kanban


def test_load_chat_and_process_signal_exists():
    assert "web_load_chat_and_process_requested = pyqtSignal(int, str, bool, bool)" in SIGNALS_PY.read_text(
        encoding="utf-8"
    )
    app = APP_SIGNALS_PY.read_text(encoding="utf-8")
    assert "web_load_chat_and_process_requested.connect" in app
    assert '"skip_user_persist": bool(skip_user_persist)' in app
