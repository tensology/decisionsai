from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = ROOT.parent / "www.decisionsai.net" / "remote-app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_shared_list_keyboard_module_exists():
    path = ROOT / "distr/gui/web/static/shared/js/list_keyboard.js"
    js = read(path)
    assert "DecisionsListKeyboard" in js
    assert "decisions-confirm-modal" in js
    assert "ArrowDown" in js
    assert "ArrowUp" in js


def test_primary_left_panels_use_shared_list_keyboard():
    shared_marker = "DecisionsListKeyboard.bind"
    files = {
        "chat": ROOT / "distr/gui/web/static/chat/js/chat.js",
        "projects": ROOT / "distr/gui/web/static/projects/js/projects.js",
        "actions": ROOT / "distr/gui/web/static/actions/js/actions.js",
        "snippets": ROOT / "distr/gui/web/static/snippets/js/snippets.js",
        "automations": ROOT / "distr/gui/web/static/automations/js/automations.js",
        "workflows": ROOT / "distr/gui/web/static/workflows/js/workflows.js",
        "settings": ROOT / "distr/gui/web/static/settings/js/settings.js",
        "kanban": ROOT / "distr/gui/web/static/kanban/js/kanban.js",
        "whatsapp": ROOT / "distr/gui/web/static/kanban/js/kanban_whatsapp.js",
    }

    for name, path in files.items():
        js = read(path)
        if name in {"kanban", "whatsapp", "settings"}:
            assert "ArrowDown" in js, name
            assert "ArrowUp" in js, name
        else:
            assert shared_marker in js, name
        assert "tabindex" in js or ".tabIndex" in js or "setAttribute('tabindex'" in js, name

    assert "deleteChat(" in read(files["chat"])
    assert "deleteProjectFromList(id)" in read(files["projects"])
    assert "deleteActionFromList(id)" in read(files["actions"])
    assert "deleteSnippet(id)" in read(files["snippets"])
    assert "deleteSelected(id)" in read(files["automations"])
    assert "deleteWorkflowById(id)" in read(files["workflows"])
    assert "confirmDeleteCurrentLocalBoard" in read(files["kanban"])
    assert "waRunDeleteChatConfirm" in read(files["whatsapp"])


def test_remote_chat_no_longer_embeds_whatsapp_reply_form():
    chat_tab = read(REMOTE_ROOT / "src/components/tabs/ChatTab.jsx")

    assert "WhatsApp Reply" not in chat_tab
    assert "sendWhatsAppText" not in chat_tab
    assert "startWhatsAppRecording" not in chat_tab
    assert "/api/tickets/whatsapp/send" not in chat_tab
