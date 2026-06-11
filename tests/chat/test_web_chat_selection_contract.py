from pathlib import Path


CHAT_JS = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "gui"
    / "web"
    / "static"
    / "chat"
    / "js"
    / "chat.js"
)


def _chat_js_source() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def test_sidebar_click_views_chat_without_loading_agent():
    src = _chat_js_source()
    click_block = src.split("div.addEventListener('click'", 1)[1].split(
        "div.addEventListener('dblclick'", 1
    )[0]

    assert "selectChat(chat.id)" in click_block
    assert "loadChat(chat.id)" not in click_block


def test_keyboard_selection_views_chat_without_loading_agent():
    src = _chat_js_source()
    bind_block = src.split("window.DecisionsListKeyboard.bind({", 1)[1].split(
        "});", 1
    )[0]

    assert "onSelect: (id) => { selectChat(id); }" in bind_block
    assert "onSelect: (id) => { loadChat(id); }" not in bind_block


def test_refresh_restore_views_last_chat_without_loading_agent():
    src = _chat_js_source()
    restore_block = src.split("data.last_chat_id != null", 1)[1].split(
        "syncKanbanSourceChatContext();", 1
    )[0]

    assert "await selectChat(lastId);" in restore_block
    assert "await loadChat(lastId);" not in restore_block


def test_view_only_chat_keeps_composer_visible_with_load_affordance():
    src = _chat_js_source()
    show_block = src.split("function showChatView(isLoaded) {", 1)[1].split(
        "function updateLoadButtonVisibility()", 1
    )[0]

    assert "inputContainer.style.display = 'block';" in show_block
    assert "loadBar.style.display = isLoaded ? 'none' : 'flex';" in show_block
    assert "messageInput.disabled = !isLoaded;" in show_block
    assert "Load this chat to reply" in show_block
