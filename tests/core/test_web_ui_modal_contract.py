from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_shared_api_exposes_consistent_confirm_modal_contract():
    base = read("distr/gui/web/templates/base.html")
    api_js = read("distr/gui/web/static/shared/js/api.js")
    css = read("distr/gui/web/static/shared/css/base.css")

    assert base.index('/static/shared/css/base.css') < base.index("{% block head_styles %}")
    assert base.index('/static/shared/css/decisions_datetime.css') < base.index("{% block head_styles %}")
    assert '/static/shared/js/api.js' in base
    assert "function showConfirm(opts)" in api_js
    assert "decisions-confirm-modal" in api_js
    assert "decisions-confirm-ok" in api_js
    assert "decisions-confirm-cancel" in api_js
    assert "decisions-confirm-option" in api_js
    assert "checkboxInput.checked" in api_js
    assert 'evt.key === "Escape"' in api_js
    assert 'evt.key === "Enter"' in api_js
    assert "previousActive = document.activeElement" in api_js
    assert "previousActive.focus()" in api_js
    assert "window.DecisionsAPI" in api_js
    assert "confirm: showConfirm" in api_js
    assert ".decisions-confirm-overlay" in css
    assert ".decisions-confirm-hotkeys" in css
    assert ".decisions-confirm-ok.is-danger" in css
    assert ".decisions-confirm-option" in css


def test_chat_page_cannot_override_shared_modal_styles():
    chat_html = read("distr/gui/web/templates/chat/chat.html")
    base = read("distr/gui/web/templates/base.html")
    chat_js = read("distr/gui/web/static/chat/js/chat.js")

    assert "{% block extra_head %}" in chat_html
    assert "static/css/chat.css" in chat_html
    assert '/static/shared/css/base.css' in base
    assert base.index('/static/shared/css/base.css') < base.index("{% block head_styles %}")
    assert base.index("{% block head_styles %}") < base.index("{% block extra_head %}")
    assert 'title: "Delete chat"' in chat_js
    assert 'window.DecisionsAPI.confirm({' in chat_js
    assert "chat-item-delete-btn" in chat_js
    assert "deleteBtn.addEventListener('click'" in chat_js
    assert "onclick=\"event.stopPropagation(); deleteChat" not in chat_js


def test_delete_and_reset_flows_use_shared_modal_instead_of_native_confirm():
    snippets_js = read("distr/gui/web/static/snippets/js/snippets.js")
    automations_js = read("distr/gui/web/static/automations/js/automations.js")
    settings_js = read("distr/gui/web/static/settings/js/settings.js")

    assert 'window.DecisionsAPI.confirm({' in snippets_js
    assert 'title: "Delete snippet"' in snippets_js
    assert 'confirmLabel: "Delete"' in snippets_js
    assert 'if (!confirm("Delete this snippet?")) return;' not in snippets_js

    assert 'window.DecisionsAPI.confirm({' in automations_js
    assert 'title: "Remove automation"' in automations_js
    assert 'confirmLabel: "Remove"' in automations_js
    assert "Remove automation" in automations_js
    assert "confirm('Remove automation" not in automations_js
    assert 'automation-delete").addEventListener("click", function()' in automations_js
    assert 'addEventListener("click", deleteSelected)' not in automations_js

    assert 'window.DecisionsAPI.confirm({' in settings_js
    assert 'title: "Reset settings"' in settings_js
    assert 'confirmLabel: "Reset"' in settings_js
    assert "confirm('Reset all settings to defaults?')" not in settings_js


def test_kanban_and_workflows_delegate_to_shared_confirm_modal():
    kanban_js = read("distr/gui/web/static/kanban/js/kanban.js")
    workflows_js = read("distr/gui/web/static/workflows/js/workflows.js")
    kanban_html = read("distr/gui/web/templates/kanban/kanban.html")

    assert "modalHelpers.showConfirm(opts);" in kanban_js
    assert "window.DecisionsAPI.confirm(opts);" in kanban_js
    assert 'id="kb-confirm-modal"' not in kanban_html

    assert "function showConfirmModal(opts)" in workflows_js
    assert "window.DecisionsAPI.confirm(opts);" in workflows_js
    assert 'id="wf-confirm-modal"' not in workflows_js


def test_first_party_web_ui_does_not_use_native_confirm_dialogs():
    static_root = ROOT / "distr/gui/web/static"
    offenders = []
    for path in static_root.rglob("*.js"):
        rel = path.relative_to(ROOT)
        rel_text = str(rel)
        if "/vendor/" in rel_text or rel_text.endswith(".tmp"):
            continue
        text = path.read_text(encoding="utf-8")
        if "window.confirm(" in text or re.search(r"(?<![A-Za-z0-9_$.])confirm\(", text):
            offenders.append(rel_text)

    assert offenders == []


def test_development_board_dialog_is_clean_and_whatsapp_lives_on_incoming():
    board = read("distr/gui/web/templates/development/sidebar/board-dialog.html")
    incoming = read("distr/gui/web/templates/development/incoming/link-dialog.html")
    index = read("distr/gui/web/templates/development/index.html")
    css = read("distr/gui/web/static/development/shell.css")
    sidebar_js = read("distr/gui/web/static/development/sidebar/index.js")
    incoming_js = read("distr/gui/web/static/development/incoming/index.js")

    assert "Board project" not in board
    assert "Incoming WhatsApp" not in board
    assert "board-whatsapp" not in board
    assert "board-link-workflow" not in board
    assert "Default workflow" not in board
    assert 'class="danger-button hidden" id="delete-board-button"' in board
    assert 'class="primary-button">Save board' in board
    assert 'class="secondary-button">Cancel' in board
    assert "delete-dialog.html" not in index
    assert "board-delete-dialog" not in sidebar_js
    assert "incoming-link-auto" in incoming
    assert "Create snapshot tickets automatically" in incoming
    assert "el('incoming-link-auto').checked" in incoming_js
    assert "auto_snapshot: enabled" in incoming_js
    assert ".danger-button" in css
    assert ".decisions-confirm-ok" in read("distr/gui/web/static/shared/css/base.css")


def test_development_workflows_live_in_the_thread_not_board_or_composer():
    board = read("distr/gui/web/templates/development/sidebar/board-dialog.html")
    ticket = read("distr/gui/web/templates/development/boards/ticket-dialog.html")
    ticket_menu = read("distr/gui/web/templates/development/boards/ticket-menu.html")
    composer = read("distr/gui/web/templates/development/threads/composer.html")
    transcript = read("distr/gui/web/static/development/threads/transcript/index.js")
    styles = read("distr/gui/web/static/development/threads/styles.css")
    kanban = read("distr/gui/web/routes/kanban.py")
    run_tool = read("distr/core/agent/tools/step_runner/workflow_tools.py")

    assert "board-link-workflow" not in board
    assert "Run workflow" not in ticket
    assert "Run workflow" not in ticket_menu
    assert "composer-workflow" not in composer
    assert "thread-workflow-runner" in transcript
    assert "liveWorkflowRunnerHtml" in transcript
    assert ".thread-workflow-ring" in styles
    assert "linked_workflow_id=board.default_workflow_id" not in kanban
    assert "no open Development thread" in run_tool
    assert "chat_id=int(host_chat_id)" in run_tool


def test_development_model_menu_is_harness_first():
    dialog = read("distr/gui/web/templates/development/threads/model-dialog.html")
    composer = read("distr/gui/web/static/development/threads/composer/index.js")
    threads = read("distr/core/workflow/development_threads.py")
    harness = read("distr/core/workflow/development_harness.py")
    assessment = read("distr/gui/web/routes/development/threads.py")

    assert 'data-model-pane="harness"' in dialog
    assert 'data-model-pane="auto"' in dialog
    assert 'id="model-route-preview"' in dialog
    assert "data-model-pane=\"provider\"" not in dialog
    assert "HARNESS_OPTIONS" in composer
    assert "scheduleRoutePreview" in composer
    assert 'backend: clean_backend if mode == "manual" else ""' in threads or '"backend": clean_backend' in threads
    assert 'pinned_backend in {"pi", "cursor", "codex", "claude_code"}' in harness
    assert "resolve_ticket_cli_route" in assessment
    assert "plan_workflow" in harness
