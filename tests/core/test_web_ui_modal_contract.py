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
    assert '<script src="/static/shared/js/api.js"></script>' in base
    assert "function showConfirm(opts)" in api_js
    assert "decisions-confirm-modal" in api_js
    assert "decisions-confirm-ok" in api_js
    assert "decisions-confirm-cancel" in api_js
    assert 'evt.key === "Escape"' in api_js
    assert 'evt.key === "Enter"' in api_js
    assert "previousActive = document.activeElement" in api_js
    assert "previousActive.focus()" in api_js
    assert "window.DecisionsAPI" in api_js
    assert "confirm: showConfirm" in api_js
    assert ".decisions-confirm-overlay" in css
    assert ".decisions-confirm-hotkeys" in css
    assert ".decisions-confirm-ok.is-danger" in css


def test_chat_page_cannot_override_shared_modal_styles():
    chat_html = read("distr/gui/web/templates/chat/chat.html")
    base = read("distr/gui/web/templates/base.html")
    chat_js = read("distr/gui/web/static/chat/js/chat.js")

    assert "{% block head_styles %}" in chat_html
    assert "static/css/chat.css" in chat_html
    assert '/static/shared/css/base.css' in base
    assert base.index('/static/shared/css/base.css') < base.index("{% block head_styles %}")
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
