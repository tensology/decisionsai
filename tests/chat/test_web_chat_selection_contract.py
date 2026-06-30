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
CHAT_ROUTES = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "gui"
    / "web"
    / "routes"
    / "chat.py"
)


def _chat_js_source() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _chat_html_source() -> str:
    return CHAT_HTML.read_text(encoding="utf-8")


def _chat_css_source() -> str:
    return CHAT_CSS.read_text(encoding="utf-8")


def _chat_routes_source() -> str:
    return CHAT_ROUTES.read_text(encoding="utf-8")


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

    assert "configureChatButton" in helper_block
    assert "speakerToggle.disabled = Boolean(isViewOnly);" in helper_block
    assert "inputLoadButton.disabled = !isViewOnly || currentChatId == null;" in helper_block


def test_send_guard_refuses_viewed_unloaded_chat():
    src = _chat_js_source()
    send_block = src.split("async function sendMessage() {", 1)[1].split(
        "// Ensure WebSocket is connected and subscribed BEFORE sending", 1
    )[0]

    assert "if (!loadedChatId || loadedChatId !== currentChatId)" in send_block
    assert "Please load a chat to reply." in send_block
    assert "setViewOnlyChrome(true);" in send_block


def test_view_only_visual_state_is_on_header_and_input_band():
    css = _chat_css_source()

    assert ".chat-shell .input-container.view-only" in css
    assert "rgba(249, 115, 22, 0.15)" in css
    assert ".chat-shell .chat-settings-header.view-only" in css


def test_input_band_stacks_above_llama_click_target():
    css = _chat_css_source()
    input_block = css.split(".input-container {", 1)[1].split("}", 1)[0]
    llama_block = css.split(".llama-click-target {", 1)[1].split("}", 1)[0]

    assert "position: relative;" in input_block
    assert "z-index: 10;" in input_block
    assert "z-index: 5;" in llama_block


def test_chat_loading_copy_is_chat_focused_not_agent_setup():
    html = _chat_html_source()
    js = _chat_js_source()
    combined = html + js

    assert "Chat settings" in html
    assert "Loading chat settings..." in html
    assert "Starting chat..." in combined
    assert "Setup your Agent" not in combined
    assert "Agent is Loading" not in combined
    assert "Agent is loading" not in combined


def test_chat_sidebar_orders_by_latest_activity_not_creation_time():
    routes = _chat_routes_source()
    chats_block = routes.split('@router.get("/chats")', 1)[1].split(
        'settings = load_settings_from_db()',
        1,
    )[0]

    assert ".order_by(Chat.modified_date.desc(), Chat.id.desc())" in chats_block
    assert ".order_by(Chat.created_date.desc(), Chat.id.desc())" not in chats_block


def test_header_icon_buttons_have_visible_tooltip_contract():
    html = _chat_html_source()
    css = _chat_css_source()

    for control_id, label in [
        ("compactChatButton", "Compact context"),
        ("forkChatButton", "Fork chat"),
        ("contextRing", "Estimated context used"),
        ("configureChatButton", "Configure chat"),
    ]:
        assert f'id="{control_id}"' in html
        assert f'aria-label="{label}"' in html
        assert f'data-tooltip="{label}"' in html

    assert 'class="header-icon-btn chat-header-tooltip" id="compactChatButton"' in html
    assert 'class="header-icon-btn chat-header-tooltip" id="forkChatButton"' in html
    assert 'class="context-ring chat-header-tooltip" id="contextRing"' in html
    assert 'class="header-icon-btn chat-header-tooltip" id="configureChatButton"' in html

    assert ".chat-header-tooltip::after" in css
    assert "content: attr(data-tooltip);" in css
    assert ".chat-header-tooltip:hover::after" in css
    assert ".chat-header-tooltip:focus-visible::after" in css
    assert "top: calc(100% + 8px);" in css
    assert ".chat-header-actions .chat-header-tooltip::after" in css
    assert ".chat-header-tooltip::before" not in css


def test_context_ring_keeps_progress_circle_pseudo_element():
    css = _chat_css_source()
    context_block = css.split(".context-ring::before {", 1)[1].split(
        ".context-ring span",
        1,
    )[0]

    assert "inset: 5px;" in context_block
    assert "border-radius: 50%;" in context_block
    assert "background: #1a1f3a;" in context_block


def test_chat_updated_during_stream_fetches_committed_voice_turns():
    src = _chat_js_source()
    ws_block = src.split("if (msg.type === 'chat_updated' && msg.chat_id === currentChatId) {", 1)[1].split(
        "            } catch (e) {}",
        1,
    )[0]

    assert "if (streamingChatId === currentChatId) return;" not in ws_block
    assert "mergeChatUpdatedDuringStream(data.messages || []);" in ws_block


def test_workflow_events_dedupe_by_run_id_in_chat_timeline():
    src = _chat_js_source()
    assert "function workflowEventDomKey" in src
    assert "function hasRenderedWorkflowEvent" in src
    assert "if (hasRenderedWorkflowEvent(workflowEvent)) return;" in src
    assert "el.dataset.workflowEventKey = domKey" in src


def test_automation_run_workflow_cards_hidden_in_chat():
    js = _chat_js_source()
    routes = _chat_routes_source()
    assert "function isHiddenWorkflowEvent" in js
    assert "return type === 'automation_run';" in js
    assert "if (isHiddenWorkflowEvent(workflowEvent)) return;" in js
    assert "def _is_visible_workflow_event" in routes
    assert '== "automation_run"' in routes


def test_chat_workflow_events_preserve_agent_activity_payload():
    routes = _chat_routes_source()
    workflow_block = routes.split('"workflow_event": {', 1)[1].split(
        "_message_sort_key",
        1,
    )[0]

    assert '"agent_activity": event.get("agent_activity") or {}' in workflow_block


def test_live_workflow_events_preserve_agent_activity_payload():
    src = _chat_js_source()
    handler_block = src.split("function handleChatEventWorkflow(msg) {", 1)[1].split(
        "if (isHiddenWorkflowEvent(workflowEvent)) return;",
        1,
    )[0]

    assert "agent_activity: msg.agent_activity || {}" in handler_block


def test_committed_voice_transcription_promotes_preview_without_waiting_for_database():
    src = _chat_js_source()
    promote_block = src.split("function promoteTranscriptionPreviewToUserMessage", 1)[1].split(
        "function handleChatEventMessageAdded",
        1,
    )[0]
    status_block = src.split("function showTranscriptionStatus", 1)[1].split(
        "// Live speech-to-text",
        1,
    )[0]

    assert "canPromoteVoiceTranscriptInChat()" in promote_block
    assert "hasOpenUserTurnPlain(plain)" in promote_block
    assert "hasRenderedMessagePlain('user', plain)" not in promote_block
    assert "createMessageElement({ role: 'user', content: plain" in promote_block
    assert "insertMessageElementInOrder(div, { role: 'user', content: plain" in promote_block
    assert "_addOptimisticUserMessage(plain.substring(0, 100));" in promote_block
    assert "if (clearLivePreview)" in status_block
    assert "promoteTranscriptionPreviewToUserMessage();" in status_block
    assert "if (done && trimmed && canPromoteVoiceTranscriptInChat() && hasLiveUserTranscriptionPreview())" in status_block
    assert "promoteTranscriptionPreviewToUserMessage(trimmed);" in status_block


def test_live_transcription_websocket_updates_are_coalesced_before_dom_work():
    src = _chat_js_source()
    ws_block = src.split("chatWs.onmessage = (event) => {", 1)[1].split(
        "if (msg.type === 'chat_updated'",
        1,
    )[0]

    assert "queueTranscriptionStatusUpdate(" in ws_block
    assert "requestAnimationFrame(flushQueuedTranscriptionStatusUpdate)" in src
    assert "setTimeout(flushQueuedTranscriptionStatusUpdate, 80)" in src


def test_repeated_voice_transcript_text_is_not_deduped_against_older_turns():
    src = _chat_js_source()
    open_turn_block = src.split("function hasOpenUserTurnPlain", 1)[1].split(
        "function _stopSttPreviewClock",
        1,
    )[0]
    stream_start_block = src.split("function handleChatEventStreamStarted", 1)[1].split(
        "streamingChatId = msg.chat_id;",
        1,
    )[0]
    merge_block = src.split("function mergeChatUpdatedDuringStream", 1)[1].split(
        "function repairMissingUserMessageForStream",
        1,
    )[0]

    assert "const last = nodes[nodes.length - 1];" in open_turn_block
    assert "last.classList.contains('user')" in open_turn_block
    assert "hasOpenUserTurnPlain(previewPlain)" in stream_start_block
    assert "hasRenderedMessagePlain('user', previewPlain)" not in stream_start_block
    assert "message.chat_row_id != null && findLiveTurnAnchor(message.chat_row_id)" in merge_block
    assert "hasOpenUserTurnPlain(plain)" in merge_block
    assert "hasRenderedMessagePlain('user', plain)" not in merge_block


def test_recent_optimistic_user_message_is_not_rendered_again_after_assistant_reply():
    src = _chat_js_source()
    message_added_block = src.split("function handleChatEventMessageAdded", 1)[1].split(
        "if (role === 'assistant'",
        1,
    )[0]
    render_incremental_block = src.split("// Fast path: append only new messages", 1)[1].split(
        "// Duplicate assistant rows",
        1,
    )[0]

    assert "hasRenderedUserMessagePlain(np)" in message_added_block
    assert "hasRenderedUserMessagePlain(userPlain)" in render_incremental_block
    assert "_hasRecentOptimisticUserMessage(key)" in message_added_block
    assert "_hasRecentOptimisticUserMessage(msg.content.substring(0, 100))" in render_incremental_block


def test_voice_stream_start_repairs_missing_user_transcript_from_chat_state():
    src = _chat_js_source()
    repair_block = src.split("function repairMissingUserMessageForStream", 1)[1].split(
        "function handleChatEventStreamStarted",
        1,
    )[0]
    start_block = src.split("function handleChatEventStreamStarted", 1)[1].split(
        "function handleChatEventStreamToken",
        1,
    )[0]

    assert "fetch(`${API_BASE}/chats/${chatId}`)" in repair_block
    assert "mergeChatUpdatedDuringStream(data.messages || []);" in repair_block
    assert "repairMissingUserMessageForStream(msg.chat_id);" in start_block


def test_live_tool_activity_keeps_turn_anchor_for_voice_transcript_merge():
    src = _chat_js_source()
    tool_block = src.split("function handleChatEventToolExecuted(msg) {", 1)[1].split(
        "function appendToolToActivityGroup(message) {",
        1,
    )[0]
    group_block = src.split("function createToolExecutionGroupElement(message) {", 1)[1].split(
        "function createToolExecutionElement(message) {",
        1,
    )[0]

    assert "turn_chat_id: msg.turn_chat_id" in tool_block
    assert "el.dataset.turnChatId = String(turnChatId);" in group_block


def test_reload_tool_activity_embeds_in_matching_assistant_turn():
    src = _chat_js_source()
    normalize_block = src.split("function normalizeTraceMessages(messages) {", 1)[1].split(
        "function renderMessages(messages, preserveOnEmpty)",
        1,
    )[0]
    create_block = src.split("function createMessageElement(message) {", 1)[1].split(
        "function finalizeMessageElementMount(div, message) {",
        1,
    )[0]
    finalize_block = src.split("function finalizeMessageElementMount(div, message) {", 1)[1].split(
        "function bindActivityToggleHandlers()",
        1,
    )[0]

    assert "embedded_tools" in normalize_block
    assert "toolsByAssistantTurn" in normalize_block
    assert "shouldEmbedToolInAssistantTurn" in normalize_block
    assert "buildStandaloneToolGroups" in normalize_block
    assert "sortMessagesForDisplay" in normalize_block
    assert "appendAssistantActivity(div, message.embedded_tools);" in finalize_block
    assert "reordered.push(...trace, current)" not in normalize_block


def test_live_tool_activity_embeds_into_streaming_or_matching_assistant():
    src = _chat_js_source()
    tool_block = src.split("function handleChatEventToolExecuted(msg) {", 1)[1].split(
        "function appendToolToAssistantTurn(message) {",
        1,
    )[0]
    append_block = src.split("function appendToolToAssistantTurn(message) {", 1)[1].split(
        "function activityGroupMatchesTurn(group, turnChatId) {",
        1,
    )[0]

    assert "appendToolToAssistantTurn(toolMessage);" in tool_block
    assert "appendToolToActivityGroup(toolMessage)" not in tool_block
    assert "isStandaloneSystemActivity(message)" in append_block
    assert "appendStandaloneToolActivity(message)" in append_block
    assert "document.getElementById('streamingAssistantMessage')" in append_block
    assert "findAssistantMessageForTurn(turnChatId)" in append_block
    assert "appendAssistantActivity(target, [message]);" in append_block


def test_stream_finish_preserves_embedded_assistant_activity():
    src = _chat_js_source()
    stream_finish_block = src.split("function handleChatEventStreamFinished(msg) {", 1)[1].split(
        "function handleChatEventStreamError(msg)",
        1,
    )[0]

    assert "const existingActivityHtml = extractAssistantActivityForPreserve(wrap);" in stream_finish_block
    assert "restoreAssistantEmbeddedActivity(wrap, existingActivityHtml);" in stream_finish_block


def test_assistant_activity_renders_as_system_activity_sibling():
    src = _chat_js_source()
    sibling_block = src.split("function getOrCreateAssistantActivitySibling(assistantEl) {", 1)[1].split(
        "function appendAssistantActivity(assistantEl, tools) {",
        1,
    )[0]
    append_block = src.split("function appendAssistantActivity(assistantEl, tools) {", 1)[1].split(
        "function extractAssistantActivityForPreserve(assistantEl) {",
        1,
    )[0]
    tool_item_block = src.split("function toolExecutionItemHtml(message) {", 1)[1].split(
        "function createToolExecutionGroupElement(message) {",
        1,
    )[0]

    assert "assistant_turn_activity: true" in sibling_block
    assert "findAssistantTurnActivityBlocks(assistantEl)" in sibling_block
    assert "getOrCreateAssistantActivitySibling(assistantEl)" in append_block
    assert "label: activityStepLabel(title, event)" in tool_item_block
    assert "label: activitySystemLabel(title, event)" not in tool_item_block


def test_header_settings_edited_via_configure_modal_only():
    src = _chat_js_source()
    html = _chat_html_source()

    assert 'id="headerLlmSummary"' in html
    assert 'id="headerVoiceSummary"' in html
    assert 'id="headerLlmProvider"' not in html
    assert 'id="headerSettingsSave"' not in html
    assert "renderHeaderSettingsSummary" in src
    assert "openChatConfigModal" in src
    assert "persistHeaderChatSettings" not in src
    assert "markHeaderSettingsDirty" not in src


def test_waiting_indicator_preserved_during_in_flight_send():
    src = _chat_js_source()
    render_block = src.split("function renderMessages(messages, preserveOnEmpty) {", 1)[1].split(
        "function formatTime(seconds)",
        1,
    )[0]
    typing_block = src.split("function createTypingIndicator() {", 1)[1].split(
        "function removeTypingIndicator()",
        1,
    )[0]

    assert "preserveLiveUi" in render_block
    assert "switchingChat" in render_block
    assert "countPersistedDomMessages()" in render_block
    assert "message-waiting" in typing_block
    assert "isStreaming && streamingChatId !== currentChatId" in src


def test_poll_and_render_skip_duplicate_assistant_rows():
    src = _chat_js_source()
    poll_block = src.split("async function pollUntilAgentResponse(abortSignal) {", 1)[1].split(
        "// Send message to the agent",
        1,
    )[0]
    render_block = src.split("function renderMessages(messages, preserveOnEmpty) {", 1)[1].split(
        "function formatTime(seconds)",
        1,
    )[0]

    assert "countPersistedDomMessages()" in poll_block
    assert "hasRenderedMessagePlain('assistant', lastPlain)" in poll_block
    assert "hasRenderedMessagePlain('assistant', assistantPlain)" in render_block
    assert "function countPersistedDomMessages()" in src
    assert ".filter(isLiveChatMessageNode)" in src.split("function countPersistedDomMessages()", 1)[1].split(
        "function syncRenderedMessageCountFromDom", 1
    )[0]


def test_live_tool_activity_uses_same_visibility_contract_as_reload():
    src = _chat_js_source()
    tool_block = src.split("function handleChatEventToolExecuted(msg) {", 1)[1].split(
        "function appendToolToActivityGroup(message) {",
        1,
    )[0]
    visibility_block = src.split("function isHiddenLiveToolEvent(msg) {", 1)[1].split(
        "function handleChatEventToolExecuted(msg) {",
        1,
    )[0]

    assert "if (isHiddenLiveToolEvent(msg)) return;" in tool_block
    assert "msg.chat_suppressed === true" in visibility_block
    assert "msg.chat_visible === false" in visibility_block
    assert "isProactivePlannerToolEvent(msg)" in visibility_block


def test_reload_tool_activity_respects_visible_false_flag():
    src = _chat_routes_source()
    block = src.split("def _is_visible_tool_event", 1)[1].split(
        "def _is_compact_tool_event",
        1,
    )[0]

    assert 'event.get("chat_suppressed") is True' in block
    assert 'event.get("chat_visible") is False' in block
