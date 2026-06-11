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
CHAT_HTML = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "gui"
    / "web"
    / "templates"
    / "chat"
    / "chat.html"
)
CHAT_CSS = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "gui"
    / "web"
    / "static"
    / "chat"
    / "css"
    / "chat.css"
)


def _chat_js_source() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _chat_html_source() -> str:
    return CHAT_HTML.read_text(encoding="utf-8")


def _chat_css_source() -> str:
    return CHAT_CSS.read_text(encoding="utf-8")


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
    assert "messageInput.disabled = !isLoaded;" in show_block
    assert "Load this chat to reply" in show_block
    assert "setViewOnlyChrome(!isLoaded);" in show_block


def test_view_only_load_button_lives_next_to_composer_only():
    html = _chat_html_source()
    js = _chat_js_source()

    assert 'id="inputLoadButton"' in html
    assert 'id="headerLoadButton"' not in html
    assert 'id="loadChatBarButton"' not in html
    assert "inputLoadButton.addEventListener('click'" in js
    assert "getElementById('headerLoadButton')" not in js
    assert "getElementById('loadChatBarButton')" not in js


def test_view_only_disables_header_and_composer_except_load_button():
    src = _chat_js_source()
    helper_block = src.split("function setViewOnlyChrome(isViewOnly) {", 1)[1].split(
        "function showEmptyState()", 1
    )[0]

    assert "headerLlmProvider" in helper_block
    assert "headerLlmModel" in helper_block
    assert "headerVoiceProvider" in helper_block
    assert "headerVoiceModel" in helper_block
    assert "speakerToggle.disabled = Boolean(isViewOnly);" in helper_block
    assert "inputLoadButton.disabled = !isViewOnly || currentChatId == null;" in helper_block


def test_view_only_visual_state_is_on_header_and_input_band():
    css = _chat_css_source()

    assert ".chat-shell .input-container.view-only" in css
    assert "rgba(249, 115, 22, 0.15)" in css
    assert ".chat-shell .chat-settings-header.view-only" in css
