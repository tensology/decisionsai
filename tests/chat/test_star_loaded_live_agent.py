"""Star = loaded = live agent. View-only click stays; the star marks the speaker."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = ROOT / "distr/gui/web/static/chat/js/chat.js"
CHAT_CSS = ROOT / "distr/gui/web/static/chat/css/chat.css"
CHAT_HTML = ROOT / "distr/gui/web/templates/chat/chat.html"


def _js() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _css() -> str:
    return CHAT_CSS.read_text(encoding="utf-8")


def test_click_and_double_click_are_view_only():
    """Least disruption: opening a thread does not move the star or load the agent."""
    src = _js()
    click_block = src.split("div.addEventListener('click'", 1)[1].split(
        "div.addEventListener('dblclick'", 1
    )[0]
    dbl = src.split("div.addEventListener('dblclick'", 1)[1].split(
        "div.addEventListener('contextmenu'", 1
    )[0]
    assert "selectChat(chat.id)" in click_block
    assert "loadChat(chat.id)" not in click_block
    assert "selectChat(chat.id)" in dbl
    assert "loadChat(chat.id)" not in dbl
    assert "loadAgentChat" not in click_block
    assert "loadAgentChat" not in dbl


def test_refresh_restores_live_star_separately_from_viewed_thread():
    src = _js()
    restore = src.split("loadChats().then(async (data) => {", 1)[1].split(
        "syncKanbanSourceChatContext();", 1
    )[0]
    assert "loadedChatId = liveId" in restore
    assert "agentCurrentChatId = liveId" in restore
    assert "await selectChat(lastId);" in restore
    assert "await loadChat(lastId);" not in restore
    assert "data.agent_current_chat_id" in restore


def test_apply_chats_data_always_aligns_loaded_with_live_agent():
    src = _js()
    block = src.split("function applyChatsData(data) {", 1)[1].split(
        "function updateActiveChat()", 1
    )[0]
    assert "loadedChatId = agentCurrentChatId" in block
    assert "loadedChatId == null &&" not in block


def test_update_active_chat_paints_single_star_on_loaded_agent():
    src = _js()
    block = src.split("function updateActiveChat() {", 1)[1].split(
        "function isEmptyStateVisible()", 1
    )[0]
    assert "star.textContent = isLive ? '★' : '☆'" in block
    assert "Active agent" in block
    assert "id === currentChatId" in block  # viewing highlight stays separate
    assert "loadedChatId !== agentCurrentChatId" in block


def test_agent_chat_changed_moves_star_and_loaded_together():
    src = _js()
    block = src.split("if (msg.type === 'agent_chat_changed') {", 1)[1].split(
        "if (msg.type === 'chat_updated'", 1
    )[0]
    assert "loadedChatId = liveId" in block
    assert "agentCurrentChatId = liveId" in block
    assert "updateActiveChat()" in block


def test_voice_transcripts_for_live_agent_are_shown_when_another_thread_is_open():
    src = _js()
    assert "function _ensureViewingLiveChatForVoice(chatId)" in src
    assert "_ensureViewingLiveChatForVoice(msg.chat_id)" in src
    assert "transcription_progress" in src
    assert "handleChatEventStreamStarted" in src
    started = src.split("function handleChatEventStreamStarted(msg) {", 1)[1].split(
        "function handleChatEventStreamToken", 1
    )[0]
    assert "_ensureViewingLiveChatForVoice(msg.chat_id)" in started


def test_empty_state_watch_adopts_only_the_live_agent():
    src = _js()
    block = src.split("async function tryAdoptAgentFirstChat(data) {", 1)[1].split(
        "function startEmptyStateAgentWatch()", 1
    )[0]
    assert "chats[0].id" not in block
    assert "agent_current_chat_id" in block


def test_saved_model_id_stays_visible_and_modal_reloads_defaults():
    src = _js()
    assert "function keepSavedModelOption(modelSelectEl, savedModel)" in src
    defaults = src.split("async function loadDefaultSettings() {", 1)[1].split(
        "async function showNewChatModal()", 1
    )[0]
    assert "keepSavedModelOption(llmModelSelect, defaultModel)" in defaults
    # selectedIndex = 0 is allowed only when Settings has no saved model.
    after = defaults.split("const defaultModel", 1)[1]
    assert "if (defaultModel)" in after
    assert "keepSavedModelOption(llmModelSelect, defaultModel)" in after
    assert "else if (opt) llmModelSelect.selectedIndex = 0" not in after
    modal = src.split("async function showNewChatModal() {", 1)[1].split(
        "function hideNewChatModal()", 1
    )[0]
    assert "_agentModalInitialized = false;" in modal
    assert "if (_agentModalInitialized)" not in modal


def test_no_new_live_agent_badge_was_added():
    js, css, html = _js(), _css(), CHAT_HTML.read_text(encoding="utf-8")
    for blob in (js, css, html):
        assert "Talking to" not in blob
        assert "in-agent-badge" not in blob
        assert "live-agent-badge" not in blob
    assert ".agent-star" in css
    assert "★" in js
