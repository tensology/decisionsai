// ChatGPT-like Chat Interface JavaScript

const API_BASE = '/api';
let currentChatId = null;   // chat currently displayed (view)
let loadedChatId = null;   // chat that is "loaded" - messages go here, input shown (matches native chat.py)
let agentCurrentChatId = null;   // chat currently loaded in the voice agent (from desktop); used to show "In agent" in sidebar
let currentChatSettings = null;
let isStreaming = false;
let streamAbortController = null;   // abort controller for current stream (cancel button)
let contextMenuChatId = null;
let chatWs = null;
let chatWsSubscribedId = null;
let chatWsReconnectTimer = null;
let chatWsReconnectDelay = 1000; // ms, doubles on each failure up to 30s
/** When non-null, we are showing a streamed assistant message for this chat (PTT/voice). */
let streamingChatId = null;
/** True when this chat was loaded with skipLoadInAgent (new chat from modal). First send must call load-in-agent so agent gets full TTS/identity setup. */
let loadedChatSkippedLoadInAgent = false;
/** Monotonically increasing token; each poll/stream registers its token and checks it's still current before resolving. Prevents stale polls from old chats firing on new chats. */
let _streamToken = 0;
/** Sequence counter for selectChat — only the latest call renders its result. */
let _selectSeq = 0;
/** Guard against double-clicking "+" creating two chats simultaneously. */
let _createChatGuard = false;

// DOM Elements
const sidebar = document.getElementById('sidebar');
const chatList = document.getElementById('chatList');
const mainContent = document.getElementById('mainContent');
const emptyState = document.getElementById('emptyState');
const chatMessages = document.getElementById('chatMessages');
const inputContainer = document.getElementById('inputContainer');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const speakerToggle = document.getElementById('speakerToggle');
let ttsEnabled = true;
let transcriptionStatusTimer = null;
const newChatBtn = document.getElementById('newChatBtn');
const chatSettingsHeader = document.getElementById('chatSettingsHeader');

// Disable input until setup form is loaded
messageInput.disabled = true;
messageInput.placeholder = 'Loading...';
sendButton.disabled = true;

// Modal Elements
const newChatModal = document.getElementById('newChatModal');
const modalClose = document.getElementById('modalClose');
const chatContextMenu = document.getElementById('chatContextMenu');
const modalCancel = document.getElementById('modalCancel');
const modalCreate = document.getElementById('modalCreate');
const llmProviderSelect = document.getElementById('llmProvider');
const llmModelSelect = document.getElementById('llmModel');
const voiceProviderSelect = document.getElementById('voiceProvider');
const voiceModelSelect = document.getElementById('voiceModel');
const startingQuestionInput = document.getElementById('startingQuestion');

// TTS Player (bottom of sidebar)
const ttsPlayerCard = document.getElementById('ttsPlayerCard');
const ttsPlayerClose = document.getElementById('ttsPlayerClose');
const ttsPlayerAudio = document.getElementById('ttsPlayerAudio');
const ttsPlayerSeek = document.getElementById('ttsPlayerSeek');
const ttsPlayerTime = document.getElementById('ttsPlayerTime');
const ttsPlayerPlay = document.getElementById('ttsPlayerPlay');
const ttsPlayerPause = document.getElementById('ttsPlayerPause');
const ttsPlayerStop = document.getElementById('ttsPlayerStop');
const ttsPlayerSpeed = document.getElementById('ttsPlayerSpeed');
const ttsPlayerDownload = document.getElementById('ttsPlayerDownload');
let ttsPlayerBlob = null;
let ttsPlayerObjectURL = null;
/** Filename for download: tts_<timestamp>.mp3, set when loading blob */
let ttsPlayerDownloadFilename = 'tts.wav';

// Cached TTS providers from backend (populated on first use)
let _ttsProviders = null;

async function _ensureTTSProviders() {
    if (_ttsProviders) return _ttsProviders;
    try {
        const resp = await fetch('/api/tts/providers');
        if (resp.ok) _ttsProviders = await resp.json();
    } catch (e) {
        console.error('Failed to load TTS providers:', e);
    }
    if (!_ttsProviders) _ttsProviders = [];
    return _ttsProviders;
}

function getVoiceSettingsKey(voiceProvider) {
    if (_ttsProviders) {
        const provider = _ttsProviders.find(p => p.id === voiceProvider);
        if (provider && provider.settings_key) return provider.settings_key;
    }
    return voiceProvider + '_voice';  // fallback
}

// Listen for provider changes from the settings page (cross-tab via BroadcastChannel)
try {
    const _providersBc = new BroadcastChannel('providers-changed');
    _providersBc.onmessage = function(ev) {
        if (ev.data && ev.data.type === 'thirdparty-providers-changed') {
            console.log('Third-party providers changed (cross-tab) — refreshing chat provider dropdowns');
            // Refresh the empty-state dropdowns if visible
            if (typeof loadEmptyStateDropdowns === 'function') loadEmptyStateDropdowns();
        }
    };
} catch (_) { /* BroadcastChannel not supported — no-op */ }

// Create one chat with default config when there are no chats. Uses Settings API (LLM + voice from Settings).
// We do not pass starting_question so the backend adds the welcome message "How can I help you today?"
async function createDefaultChat() {
    let provider = 'ollama';
    let modelName = null;
    let voiceProvider = 'kokoro';
    let voiceModel = null;
    try {
        await _ensureTTSProviders();
        const [llmsRes, generalRes] = await Promise.all([
            fetch('/api/llms'),
            fetch('/api/general')
        ]);
        if (llmsRes.ok) {
            const llms = await llmsRes.json();
            const p = (llms.conversational_provider || 'ollama').toString().toLowerCase();
            const m = (llms.conversational_model || '').trim();
            if (p) provider = p;
            if (m && m !== '—') modelName = m;
        }
        if (generalRes.ok) {
            const general = await generalRes.json();
            const vp = (general.voice_provider || 'kokoro').toString().toLowerCase();
            if (vp) voiceProvider = vp;
            const key = getVoiceSettingsKey(voiceProvider);
            const v = (general[key] || '').trim();
            if (v && v !== '—') voiceModel = v;
        }
    } catch (e) {
        console.warn('Settings fetch failed, using defaults:', e);
    }
    const body = {
        title: 'New Chat',
        provider,
        voice_provider: voiceProvider,
        voice_model: voiceModel
    };
    if (modelName) body.model_name = modelName;
    try {
        const response = await fetch(`${API_BASE}/chats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!response.ok) {
            const err = await response.text();
            console.error('Create default chat failed:', response.status, err);
            return null;
        }
        const data = await response.json();
        return data.id || null;
    } catch (e) {
        console.error('Error creating default chat:', e);
        return null;
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    initTTSPlayer();
    _ensureTTSProviders();
    loadDefaultSettings();

    // Llama click → show about window + play splash sound
    const llamaEl = document.getElementById('llamaClickTarget');
    if (llamaEl) {
        llamaEl.addEventListener('click', () => {
            fetch('/api/about/show', { method: 'POST' }).catch(() => {});
        });
    }

    loadChats().then(async (data) => {
        const params = new URLSearchParams(window.location.search);
        const idParam = params.get('id');
        if (idParam) {
            const chatId = parseInt(idParam, 10);
            if (!isNaN(chatId)) await loadChat(chatId);
        } else if (data && data.chats && data.chats.length === 0) {
            // Show empty state so user can select provider/model/voice before first chat
            showEmptyState();
        } else if (data && data.last_chat_id != null && data.chats && data.chats.length) {
            const lastId = Number(data.last_chat_id);
            if (!isNaN(lastId) && data.chats.some(c => c.id === lastId)) {
                // Use selectChat on startup — agent already has this chat loaded from app init, no need to hot-swap
                await selectChat(lastId);
            }
        }
        if (!currentChatId) loadEmptyStateDropdowns();
    });
});

// Event Listeners
function setupEventListeners() {
    newChatBtn.addEventListener('click', () => showNewChatModal());
    sendButton.addEventListener('click', () => { if (isStreaming) cancelStream(); else sendMessage(); });
    if (speakerToggle) {
        speakerToggle.addEventListener('click', () => {
            ttsEnabled = !ttsEnabled;
            speakerToggle.setAttribute('aria-pressed', ttsEnabled ? 'true' : 'false');
            speakerToggle.title = ttsEnabled ? 'Read response aloud (TTS)' : 'TTS off – text only';
        });
    }
    messageInput.addEventListener('keydown', handleInputKeydown);
    messageInput.addEventListener('input', handleInputChange);
    // Modal event listeners
    modalClose.addEventListener('click', hideNewChatModal);
    modalCancel.addEventListener('click', hideNewChatModal);
    modalCreate.addEventListener('click', createNewChatWithSettings);
    llmProviderSelect.addEventListener('change', () => {
        loadLlmModels(llmProviderSelect.value);
        toggleModalOllamaPullBtn();
    });
    voiceProviderSelect.addEventListener('change', () => loadVoiceModels(voiceProviderSelect.value));

    const emptyStateLlmProvider = document.getElementById('emptyStateLlmProvider');
    const emptyStateLlmModel = document.getElementById('emptyStateLlmModel');
    const emptyStateVoiceProvider = document.getElementById('emptyStateVoiceProvider');
    const emptyStateVoiceModel = document.getElementById('emptyStateVoiceModel');
    if (emptyStateLlmProvider) emptyStateLlmProvider.addEventListener('change', () => {
        loadEmptyStateLlmModels(emptyStateLlmProvider.value);
        toggleEmptyStateOllamaPullBtn();
    });
    if (emptyStateVoiceProvider) emptyStateVoiceProvider.addEventListener('change', () => loadEmptyStateVoiceModels(emptyStateVoiceProvider.value));

    const modalPlayVoiceBtn = document.getElementById('modalPlayVoiceBtn');
    if (modalPlayVoiceBtn) modalPlayVoiceBtn.addEventListener('click', playVoiceModal);
    const emptyStatePlayVoiceBtn = document.getElementById('emptyStatePlayVoiceBtn');
    if (emptyStatePlayVoiceBtn) emptyStatePlayVoiceBtn.addEventListener('click', playEmptyStateVoice);

    // Modal should only close via Cancel, Close button, or Escape — NOT on overlay click
    // (removed: newChatModal background click handler)

    // Update active nav tab based on current page
    document.querySelectorAll('.nav-tab').forEach(tab => {
        if (tab.href === window.location.href ||
            (tab.href.includes('/chat') && window.location.pathname.startsWith('/chat'))) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Header Load button + Load bar button (view → load, matches native chat.py)
    const headerLoadBtn = document.getElementById('headerLoadButton');
    if (headerLoadBtn) headerLoadBtn.addEventListener('click', () => { if (currentChatId) loadChat(currentChatId); });
    const loadBarBtn = document.getElementById('loadChatBarButton');
    if (loadBarBtn) loadBarBtn.addEventListener('click', () => { if (currentChatId) loadChat(currentChatId); });

    // Chat context menu: close on click outside
    if (chatContextMenu) {
        document.addEventListener('click', hideChatContextMenu);
        document.addEventListener('scroll', hideChatContextMenu, true);
        chatContextMenu.addEventListener('click', (e) => e.stopPropagation());
        chatContextMenu.querySelectorAll('.chat-context-menu-item').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const action = btn.getAttribute('data-action');
                const idStr = chatContextMenu.getAttribute('data-chat-id');
                const id = idStr ? parseInt(idStr, 10) : null;
                hideChatContextMenu();
                if (id == null || isNaN(id)) return;
                if (action === 'view') {
                    setTimeout(() => selectChat(id), 0);
                }
                if (action === 'load') {
                    setTimeout(() => loadChat(id), 0);
                }
                if (action === 'rename') renameChat(id);
                if (action === 'delete') deleteChat(id);
            });
        });
    }
}

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        if (!messageInput.disabled && !sendButton.disabled && !isStreaming) {
            sendMessage();
        }
    }
}

function handleInputChange() {
    // Auto-resize textarea
    messageInput.style.height = 'auto';
    messageInput.style.height = messageInput.scrollHeight + 'px';
    
    // Enable/disable send button
    sendButton.disabled = !messageInput.value.trim() || isStreaming;
}

function getChatWsUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const base = `${protocol}//${window.location.host}/api/ws`;
    const token = (window.DECISIONSAI_INTERNAL_API_TOKEN || '').trim();
    if (!token) return base;
    const separator = base.includes('?') ? '&' : '?';
    return `${base}${separator}internal_token=${encodeURIComponent(token)}`;
}

function stopChatWebSocket() {
    if (chatWsReconnectTimer) { clearTimeout(chatWsReconnectTimer); chatWsReconnectTimer = null; }
    chatWsReconnectDelay = 1000;
    if (chatWs) {
        chatWs.onclose = null; // prevent reconnect on intentional close
        chatWs.close();
        chatWs = null;
        chatWsSubscribedId = null;
    }
}

/**
 * Start or ensure WebSocket is connected and subscribed to the current chat.
 * @param {boolean} force - If true, bypass isStreaming guard (use when ensuring connection before send).
 */
function startChatWebSocket(force) {
    if (currentChatId == null) return;
    if (!force && isStreaming) return;
    const chatId = currentChatId;
    if (chatWs && chatWs.readyState === WebSocket.OPEN) {
        if (chatWsSubscribedId === chatId) return;
        chatWsSubscribedId = chatId;
        chatWs.send(JSON.stringify({ subscribe: chatId }));
        return;
    }
    // Clear any pending reconnect before opening a fresh connection
    if (chatWsReconnectTimer) { clearTimeout(chatWsReconnectTimer); chatWsReconnectTimer = null; }
    if (chatWs) {
        chatWs.onclose = null;
        chatWs.close();
        chatWs = null;
    }
    try {
        const wsBase = getChatWsUrl();
        const separator = wsBase.includes('?') ? '&' : '?';
        const url = wsBase + (chatId ? `${separator}chat_id=${chatId}` : '');
        chatWs = new WebSocket(url);
        chatWsSubscribedId = chatId;
        chatWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.event === 'message_added') {
                    handleChatEventMessageAdded(msg);
                    return;
                }
                if (msg.event === 'stream_started') {
                    handleChatEventStreamStarted(msg);
                    return;
                }
                if (msg.event === 'stream_token') {
                    handleChatEventStreamToken(msg);
                    return;
                }
                if (msg.event === 'stream_finished') {
                    handleChatEventStreamFinished(msg);
                    return;
                }
                if (msg.event === 'stream_error') {
                    handleChatEventStreamError(msg);
                    return;
                }
                if (msg.event === 'transcription_progress') {
                    if (msg.chat_id === currentChatId) {
                        showTranscriptionStatus(msg.status || 'Transcription in progress...', Boolean(msg.done));
                    }
                    return;
                }
                if (msg.type === 'chat_updated' && msg.chat_id === currentChatId) {
                    if (streamingChatId === currentChatId && window._agentStreamResolve) {
                        // Fallback: stream_finished never fired (e.g. legacy provider). Fetch and render, then resolve.
                        fetch(`${API_BASE}/chats/${currentChatId}`).then(r => r.json()).then(data => {
                            const wrap = document.getElementById('streamingAssistantMessage');
                            if (wrap) {
                                const textEl = document.getElementById('streamingAssistantText');
                                const raw = textEl ? textEl.textContent : '';
                                const messages = data.messages || [];
                                const lastAssistant = messages.filter(m => m.role === 'assistant').pop();
                                const content = (lastAssistant && lastAssistant.content) || raw;
                                wrap.id = '';
                                wrap.innerHTML = `
                                    <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
                                    <div class="message-content">
                                        <div class="message-text">${formatMessage(content)}</div>
                                        <div class="message-actions">
                                            <button class="message-action-btn" onclick="copyMessage(this)">Copy</button>
                                            <button class="message-action-btn message-action-play" onclick="playTTS(this)" title="Play with TTS">Play</button>
                                        </div>
                                    </div>
                                `;
                            }
                            streamingChatId = null;
                            removeTypingIndicator();
                            if (window._agentStreamResolve) {
                                window._agentStreamResolve();
                                window._agentStreamResolve = null;
                            }
                            updateChatSettingsDisplay({
                                title: data.title || 'New Chat',
                                provider: data.provider || '-',
                                model_name: data.model_name || '-',
                                voice_provider: data.voice_provider,
                                voice_model: data.voice_model, voice_model_display: data.voice_model_display
                            });
                            scrollToBottom();
                        }).catch(() => {});
                        return;
                    }
                    if (streamingChatId === currentChatId) return;
                    fetch(`${API_BASE}/chats/${currentChatId}`).then(r => r.json()).then(data => {
                        renderMessages(data.messages || [], true);
                        updateChatSettingsDisplay({
                            title: data.title || 'New Chat',
                            provider: data.provider || '-',
                            model_name: data.model_name || '-',
                            voice_provider: data.voice_provider,
                            voice_model: data.voice_model, voice_model_display: data.voice_model_display
                        });
                        scrollToBottom();
                    }).catch(() => {});
                }
            } catch (e) {}
        };
        chatWs.onclose = () => {
            chatWs = null;
            chatWsSubscribedId = null;
            // Auto-reconnect if we still have an active chat, with exponential backoff
            if (currentChatId != null) {
                chatWsReconnectTimer = setTimeout(() => {
                    chatWsReconnectTimer = null;
                    chatWsReconnectDelay = Math.min(chatWsReconnectDelay * 2, 30000);
                    startChatWebSocket();
                }, chatWsReconnectDelay);
            }
        };
        chatWs.onopen = () => {
            chatWsReconnectDelay = 1000; // reset backoff on successful connect
            if (chatWsSubscribedId != null && chatWs.readyState === WebSocket.OPEN) {
                chatWs.send(JSON.stringify({ subscribe: chatWsSubscribedId }));
            }
        };
    } catch (e) {
        console.debug('Chat WebSocket error:', e);
    }
}

/** Track user message content already rendered optimistically by sendMessage()
    so handleChatEventMessageAdded can skip duplicates but render voice/PTT messages. */
let _optimisticUserMessages = new Set();  // first-100-chars of user messages we already rendered

function handleChatEventMessageAdded(msg) {
    if (msg.chat_id !== currentChatId) return;
    const role = msg.role || 'user';
    const content = msg.content || '';
    if (role === 'user') {
        // Voice/PTT messages come via WS without sendMessage() — render them.
        // Skip only if sendMessage() already rendered this exact text optimistically.
        const key = content.substring(0, 100);
        if (_optimisticUserMessages.has(key)) return;
        const div = createMessageElement({ role, content });
        // Insert BEFORE the streaming assistant message or typing indicator
        // so the user message appears chronologically first.
        const streamingMsg = document.getElementById('streamingAssistantMessage');
        const typingInd = document.getElementById('typingIndicator');
        const insertBefore = streamingMsg || typingInd;
        if (insertBefore) {
            chatMessages.insertBefore(div, insertBefore);
        } else {
            chatMessages.appendChild(div);
        }
        scrollToBottom();
        return;
    }
    if (role === 'assistant' && streamingChatId === currentChatId) return;
    const div = createMessageElement({ role, content });
    chatMessages.appendChild(div);
    scrollToBottom();
}

function handleChatEventStreamStarted(msg) {
    if (msg.chat_id !== currentChatId) return;
    streamingChatId = msg.chat_id;
    _streamTextBuffer = '';
    _streamRafPending = false;
    removeTypingIndicator();
    const existing = document.getElementById('streamingAssistantMessage');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = 'streamingAssistantMessage';
    div.innerHTML = `
        <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
        <div class="message-content">
            <div class="message-text" id="streamingAssistantText"></div>
            <div class="typing-indicator" id="streamingTypingIndicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
            <div class="message-actions" style="display: none;"></div>
        </div>
    `;
    chatMessages.appendChild(div);
    scrollToBottom();
}

/** rAF-debounced stream token renderer: bundles rapid token events into one DOM update per frame. */
let _streamTextBuffer = '';
let _streamRafPending = false;

function handleChatEventStreamToken(msg) {
    if (msg.chat_id !== currentChatId) return;
    if (msg.token) {
        _streamTextBuffer += msg.token;
        if (!_streamRafPending) {
            _streamRafPending = true;
            requestAnimationFrame(() => {
                _streamRafPending = false;
                const el = document.getElementById('streamingAssistantText');
                if (el) {
                    el.textContent += _streamTextBuffer;
                    const typingEl = document.getElementById('streamingTypingIndicator');
                    if (typingEl) typingEl.style.display = 'none';
                }
                _streamTextBuffer = '';
                scrollToBottom();
            });
        }
    }
}

function handleChatEventStreamFinished(msg) {
    if (msg.chat_id !== currentChatId) return;
    // Flush any buffered stream tokens before finalizing
    _streamTextBuffer = '';
    _streamRafPending = false;
    removeTypingIndicator();
    const wrap = document.getElementById('streamingAssistantMessage');
    if (wrap) {
        const textEl = document.getElementById('streamingAssistantText');
        // Prefer response_text from event (clean, tool_call tags stripped) over raw streamed DOM text
        const raw = (msg.response_text != null && msg.response_text !== '') ? msg.response_text : (textEl ? textEl.textContent : '');
        wrap.id = '';
        wrap.innerHTML = `
            <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
            <div class="message-content">
                <div class="message-text">${formatMessage(raw)}</div>
                <div class="message-actions">
                    <button class="message-action-btn" onclick="copyMessage(this)">Copy</button>
                    <button class="message-action-btn message-action-play" onclick="playTTS(this)" title="Play with TTS">Play</button>
                </div>
            </div>
        `;
        scrollToBottom();
    } else {
        // Streaming element was destroyed (e.g. by polling race) - fetch final state from DB
        const chatId = currentChatId;
        fetch(`${API_BASE}/chats/${chatId}`).then(r => r.json()).then(data => {
            if (currentChatId === chatId) {
                renderMessages(data.messages || [], false);
                updateChatSettingsDisplay({
                    title: data.title || 'New Chat',
                    provider: data.provider || '-',
                    model_name: data.model_name || '-',
                    voice_provider: data.voice_provider,
                    voice_model: data.voice_model, voice_model_display: data.voice_model_display
                });
                scrollToBottom();
            }
        }).catch(() => {});
    }
    // Null streamingChatId AFTER resolving so the poll guard (streamingChatId !== currentChatId)
    // still holds for any in-flight poll tick that fires before the microtask queue drains.
    // Resolve pending WebSocket-based wait (sendToAgentAndPoll / pollUntilAgentResponse)
    // Only fire if this stream_finished is for the currently active stream token (guards against stale events from old chats)
    if (window._agentStreamResolve) {
        window._agentStreamResolve();
        window._agentStreamResolve = null;
    }
    streamingChatId = null;
    _optimisticUserMessages.clear();
    // Reset input state in case the message came from voice/PTT (not web sendMessage)
    isStreaming = false;
    messageInput.disabled = false;
    messageInput.placeholder = 'Send message...';
    sendButton.disabled = !messageInput.value.trim();
    setSendButtonStreaming(false);
}

function handleChatEventStreamError(msg) {
    if (msg.chat_id != null && msg.chat_id !== currentChatId) return;
    _streamTextBuffer = '';
    _streamRafPending = false;
    removeTypingIndicator();
    // Remove any in-progress streaming bubble
    const wrap = document.getElementById('streamingAssistantMessage');
    if (wrap) wrap.remove();
    // Show the error as an assistant message in the chat
    const errorText = msg.error || 'An error occurred while generating a response.';
    // Clean up verbose API error details — show just the key message
    let displayError = errorText;
    if (errorText.includes('exceeded your current quota')) {
        displayError = '⚠️ API quota exceeded. Check your plan and billing, or switch to a different model.';
    } else if (errorText.includes('Rate Limit')) {
        displayError = '⚠️ Rate limit hit. Please wait a moment and try again.';
    } else if (errorText.includes('401') || errorText.includes('authentication')) {
        displayError = '⚠️ Authentication failed. Check your API key in Settings → Third Party Providers.';
    } else if (errorText.length > 200) {
        // Truncate very long error messages
        displayError = '⚠️ ' + errorText.substring(0, 200) + '…';
    } else {
        displayError = '⚠️ ' + errorText;
    }
    if (chatMessages) {
        const errorEl = document.createElement('div');
        errorEl.className = 'message assistant';
        errorEl.innerHTML = `
            <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
            <div class="message-content">
                <div class="message-text" style="color:#f87171">${escapeHtml(displayError)}</div>
            </div>
        `;
        chatMessages.appendChild(errorEl);
        scrollToBottom();
    }
    if (window._agentStreamResolve) {
        window._agentStreamResolve();
        window._agentStreamResolve = null;
    }
    streamingChatId = null;
}

function showTranscriptionStatus(text, done) {
    if (!chatMessages) return;
    let wrap = document.getElementById('transcriptionStatus');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'message assistant';
        wrap.id = 'transcriptionStatus';
        wrap.innerHTML = `
            <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
            <div class="message-content">
                <div class="message-text" id="transcriptionStatusText"></div>
            </div>
        `;
        chatMessages.appendChild(wrap);
    }

    const textEl = document.getElementById('transcriptionStatusText');
    if (textEl) textEl.textContent = text || 'Transcription in progress...';
    scrollToBottom();

    if (transcriptionStatusTimer) {
        clearTimeout(transcriptionStatusTimer);
        transcriptionStatusTimer = null;
    }
    if (done) {
        transcriptionStatusTimer = setTimeout(() => {
            const el = document.getElementById('transcriptionStatus');
            if (el) el.remove();
        }, 5000);
    }
}

// Load Chats (from database via settings API; returns { chats, last_chat_id, agent_current_chat_id } for restore on refresh)
async function loadChats() {
    try {
        const response = await fetch(`${API_BASE}/chats`);
        if (!response.ok) throw new Error('Failed to load chats');
        const data = await response.json();
        const chats = data.chats !== undefined ? data.chats : (Array.isArray(data) ? data : []);
        renderChatList(chats);
        applyChatsData(data);
        return data;
    } catch (error) {
        console.error('Error loading chats:', error);
        renderChatList([]);
        return { chats: [], last_chat_id: null, agent_current_chat_id: null };
    }
}

// Persist loaded chat ID to DB (Settings.last_chat_id) so refresh restores it (same as native)
async function saveLastChatId(chatId) {
    try {
        await fetch('/api/last-chat-id', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ last_chat_id: chatId })
        });
    } catch (error) {
        console.error('Error saving last_chat_id:', error);
    }
}

function renderChatList(chats) {
    chatList.innerHTML = '';
    
    if (chats.length === 0) {
        chatList.innerHTML = '<div style="padding: 12px; color: #8e8ea0; text-align: center; font-size: 14px;">No chats yet</div>';
        return;
    }
    
    chats.forEach(chat => {
        const chatItem = createChatItem(chat);
        chatList.appendChild(chatItem);
    });
    updateActiveChat();
}

function createChatItem(chat) {
    const div = document.createElement('div');
    div.className = 'chat-item';
    div.setAttribute('data-chat-id', String(chat.id));
    div.setAttribute('title', 'Double-click to load into agent');
    if (chat.id === currentChatId) {
        div.classList.add('active');
    }
    
    div.innerHTML = `
        <span class="chat-item-in-agent-star" style="display: none;" title="Loaded in voice agent">★</span>
        <span class="chat-item-title-wrap">
            <span class="chat-item-title">${escapeHtml(chat.title)}</span>
        </span>
        <span class="chat-item-agent-badge" style="display: none;" title="Loaded in voice agent">In agent</span>
        <div class="chat-item-actions">
            <button type="button" class="chat-item-btn chat-item-rename-btn" data-chat-id="${chat.id}" title="Rename" aria-label="Rename chat">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                </svg>
            </button>
            <button class="chat-item-btn" onclick="event.stopPropagation(); deleteChat(${chat.id})" title="Delete">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
            </button>
        </div>
    `;
    
    div.addEventListener('click', (e) => {
        selectChat(chat.id);
    });
    div.addEventListener('dblclick', (e) => {
        loadChat(chat.id);
    });
    div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showChatContextMenu(e, chat.id);
    });
    const renameBtn = div.querySelector('.chat-item-rename-btn');
    if (renameBtn) {
        renameBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            renameChat(chat.id);
        });
    }

    return div;
}

function showChatContextMenu(e, chatId) {
    contextMenuChatId = chatId;
    if (!chatContextMenu) return;
    chatContextMenu.setAttribute('data-chat-id', String(chatId));
    chatContextMenu.style.display = 'block';
    chatContextMenu.style.left = Math.min(e.clientX, window.innerWidth - 130) + 'px';
    chatContextMenu.style.top = e.clientY + 'px';
}

function hideChatContextMenu() {
    contextMenuChatId = null;
    if (chatContextMenu) {
        chatContextMenu.style.display = 'none';
        chatContextMenu.removeAttribute('data-chat-id');
    }
}

// Create New Chat
async function createNewChat() {
    if (_createChatGuard) return;
    _createChatGuard = true;
    newChatBtn.disabled = true;
    try {
        // Inherit provider/model/voice from current chat settings if loaded, else empty-state dropdowns
        let provider = null, modelName = null, voiceProvider = null, voiceModel = null;
        if (currentChatSettings?.provider) {
            provider = currentChatSettings.provider;
            modelName = currentChatSettings.model_name || null;
            voiceProvider = currentChatSettings.voice_provider || null;
            voiceModel = currentChatSettings.voice_model || null;
        } else {
            const emptyLlmProvider = document.getElementById('emptyStateLlmProvider');
            const emptyLlmModel = document.getElementById('emptyStateLlmModel');
            const emptyVoiceProvider = document.getElementById('emptyStateVoiceProvider');
            const emptyVoiceModel = document.getElementById('emptyStateVoiceModel');
            provider = emptyLlmProvider?.value?.trim() || null;
            modelName = emptyLlmModel?.value?.trim() || null;
            voiceProvider = emptyVoiceProvider?.value?.trim() || null;
            voiceModel = emptyVoiceModel?.value?.trim() || null;
        }
        const response = await fetch(`${API_BASE}/chats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: null, provider, model_name: modelName, voice_provider: voiceProvider, voice_model: voiceModel })
        });
        if (!response.ok) throw new Error('Failed to create chat');
        const data = await response.json();
        await loadChats();
        await loadChat(data.id);
    } catch (error) {
        console.error('Error creating chat:', error);
    } finally {
        _createChatGuard = false;
        newChatBtn.disabled = false;
    }
}

// Select Chat: show messages and UI for this chat, but do NOT load into agent.
// Called on single click. Use loadChat() to also hot-swap the agent.
async function selectChat(chatId) {
    const seq = ++_selectSeq;
    currentChatId = chatId;
    updateActiveChat();
    try {
        const response = await fetch(`${API_BASE}/chats/${chatId}`);
        const data = await response.json();
        // Discard if a newer selectChat/loadChat has already taken over
        if (seq !== _selectSeq) return;
        renderMessages(data.messages);
        updateChatSettingsDisplay({
            title: data.title || 'New Chat',
            provider: data.provider || '-',
            model_name: data.model_name || '-',
            voice_provider: data.voice_provider,
            voice_model: data.voice_model, voice_model_display: data.voice_model_display
        });
        showChatView(loadedChatId === chatId);
        updateLoadButtonVisibility();
        startChatWebSocket();
        setTimeout(() => { chatMessages.scrollTop = chatMessages.scrollHeight; }, 300);
        await saveLastChatId(chatId);
    } catch (error) {
        console.error('Error selecting chat:', error);
    }
}

// Load Chat: select + load into agent (hot-swap LLM/voice/history). Called on double click or Load button.
// Options: { skipLoadInAgent: true } - skip POST load-in-agent (use when chat was just created and agent already has it)
async function loadChat(chatId, options = {}) {
    // Abort any in-flight stream from a previous chat so its poll doesn't stomp this one
    if (streamAbortController) { streamAbortController.abort(); streamAbortController = null; }
    _streamToken++;       // invalidate any pending poll/stream resolve from old chat
    _selectSeq++;         // invalidate any pending selectChat render
    const seq = _selectSeq;
    currentChatId = chatId;
    loadedChatId = chatId;
    streamingChatId = null;
    isStreaming = false;
    setSendButtonStreaming(false);
    updateActiveChat();
    // Close TTS player when switching chats — agent TTS is interrupted server-side, player should clear too
    if (ttsPlayerCard) { ttsPlayerCard.style.display = 'none'; }
    if (ttsPlayerAudio) { ttsPlayerAudio.pause(); ttsPlayerAudio.removeAttribute('src'); }
    // Lock sidebar so user can't click another chat while this one is loading into agent
    chatList.classList.add('chat-list--locked');
    try {
        const response = await fetch(`${API_BASE}/chats/${chatId}`);
        const data = await response.json();
        if (seq !== _selectSeq) return; // superseded by another navigation
        renderMessages(data.messages);
        updateChatSettingsDisplay({
            title: data.title || 'New Chat',
            provider: data.provider || '-',
            model_name: data.model_name || '-',
            voice_provider: data.voice_provider,
            voice_model: data.voice_model, voice_model_display: data.voice_model_display
        });
        showChatView(true);
        updateLoadButtonVisibility();
        startChatWebSocket();
        setTimeout(() => { chatMessages.scrollTop = chatMessages.scrollHeight; }, 300);
        await saveLastChatId(chatId);
        if (options.skipLoadInAgent) {
            loadedChatSkippedLoadInAgent = true;
            agentCurrentChatId = chatId;
            updateActiveChat();
        } else {
            loadedChatSkippedLoadInAgent = false;
            try {
                await fetch(`${API_BASE}/chats/${chatId}/load-in-agent`, { method: 'POST' });
                agentCurrentChatId = chatId;
                updateActiveChat();
            } catch (e) {
                console.warn('Load-in-agent failed:', e);
            }
        }
    } catch (error) {
        console.error('Error loading chat:', error);
    } finally {
        chatList.classList.remove('chat-list--locked');
    }
}

function applyChatsData(data) {
    if (!data) return;
    agentCurrentChatId = data.agent_current_chat_id != null ? Number(data.agent_current_chat_id) : null;
    if (isNaN(agentCurrentChatId)) agentCurrentChatId = null;
    // If the agent already has a chat loaded AND that chat still exists in the list,
    // the web UI should treat it as loaded too.
    const chats = data.chats || [];
    if (agentCurrentChatId != null && loadedChatId == null && chats.some(c => c.id === agentCurrentChatId)) {
        loadedChatId = agentCurrentChatId;
    }
    updateActiveChat();
}

function updateActiveChat() {
    document.querySelectorAll('.chat-item').forEach(item => {
        const id = parseInt(item.getAttribute('data-chat-id'), 10);
        item.classList.toggle('active', id === currentChatId);
        item.classList.toggle('loaded', id === loadedChatId);
        const inAgent = (id === agentCurrentChatId);
        item.classList.toggle('in-agent', inAgent);
        const star = item.querySelector('.chat-item-in-agent-star');
        if (star) star.style.display = inAgent ? 'inline-block' : 'none';
    });
}

function showChatView(isLoaded) {
    emptyState.style.display = 'none';
    chatMessages.style.display = 'flex';
    if (chatSettingsHeader) chatSettingsHeader.style.display = 'flex';
    inputContainer.style.display = isLoaded ? 'block' : 'none';
    const loadBar = document.getElementById('loadChatBar');
    if (loadBar) loadBar.style.display = isLoaded ? 'none' : 'flex';
    // Always re-enable the input when showing a chat — it may still be disabled
    // from the initial 'Loading...' state set before DOMContentLoaded completes.
    if (!isStreaming) {
        messageInput.disabled = false;
        if (messageInput.placeholder === 'Loading...') messageInput.placeholder = 'Send message...';
        sendButton.disabled = !messageInput.value.trim();
    }
}

function updateLoadButtonVisibility() {
    const headerLoadBtn = document.getElementById('headerLoadButton');
    if (!headerLoadBtn) return;
    const show = currentChatId != null && loadedChatId !== currentChatId;
    headerLoadBtn.style.display = show ? 'inline-flex' : 'none';
}

function showEmptyState() {
    stopChatWebSocket();
    emptyState.style.display = 'flex';
    chatMessages.style.display = 'none';
    chatSettingsHeader.style.display = 'none';
    inputContainer.style.display = 'block';
    const loadBar = document.getElementById('loadChatBar');
    if (loadBar) loadBar.style.display = 'none';
    const headerLoadBtn = document.getElementById('headerLoadButton');
    if (headerLoadBtn) headerLoadBtn.style.display = 'none';
    // Ensure input is usable (may have been disabled during a stream)
    messageInput.disabled = false;
    sendButton.disabled = !messageInput.value.trim();
    isStreaming = false;
    setSendButtonStreaming(false);
    hideEmptyStateAgentLoading();
    loadEmptyStateDropdowns();
}

function showEmptyStateAgentLoading() {
    const setup = document.getElementById('emptyStateSetup');
    const loading = document.getElementById('emptyStateAgentLoading');
    if (setup) setup.style.display = 'none';
    if (loading) loading.style.display = 'block';
}

function hideEmptyStateAgentLoading() {
    const setup = document.getElementById('emptyStateSetup');
    const loading = document.getElementById('emptyStateAgentLoading');
    if (setup) setup.style.display = 'block';
    if (loading) loading.style.display = 'none';
}

// Populate empty-state dropdowns (same sources as create-chat modal)
async function loadEmptyStateDropdowns() {
    // Show loader, hide form while loading
    const loader = document.getElementById('emptyStateLoader');
    const form = document.getElementById('emptyStateForm');
    const prompt = document.getElementById('emptyStatePrompt');
    if (loader) loader.style.display = '';
    if (form) form.style.display = 'none';
    if (prompt) prompt.style.display = 'none';

    const llmProviderEl = document.getElementById('emptyStateLlmProvider');
    const llmModelEl = document.getElementById('emptyStateLlmModel');
    const voiceProviderEl = document.getElementById('emptyStateVoiceProvider');
    const voiceModelEl = document.getElementById('emptyStateVoiceModel');
    if (!llmProviderEl || !llmModelEl || !voiceProviderEl || !voiceModelEl) return;
    try {
        await _ensureTTSProviders();
        const [providersRes, modelsRes, generalRes] = await Promise.all([
            fetch('/api/llms/available-providers'),
            fetch(`${API_BASE}/models`),
            fetch('/api/general')
        ]);
        const providersData = providersRes.ok ? await providersRes.json() : { providers: [] };
        const modelsData = modelsRes.ok ? await modelsRes.json() : {};
        const generalData = generalRes.ok ? await generalRes.json() : {};
        const providers = providersData.providers || [];
        llmProviderEl.innerHTML = providers.length ? providers.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('') : '<option value="">No providers</option>';
        const preferredProvider = (modelsData.provider || 'ollama').toLowerCase();
        const hasPreferred = providers.some(p => p.id === preferredProvider);
        const llmProvider = hasPreferred ? preferredProvider : (providers[0] ? providers[0].id : '');
        if (llmProvider) {
            llmProviderEl.value = llmProvider;
            await loadEmptyStateLlmModels(llmProvider);
            const defaultModel = (modelsData.model || '').trim();
            if (defaultModel && llmModelEl.options.length) {
                const opt = Array.from(llmModelEl.options).find(o => o.value === defaultModel);
                if (opt) llmModelEl.value = defaultModel;
                else if (llmModelEl.options[0] && llmModelEl.options[0].value) llmModelEl.selectedIndex = 0;
            } else if (llmModelEl.options[0] && llmModelEl.options[0].value) llmModelEl.selectedIndex = 0;
        }
        const voiceProvider = generalData.voice_provider || 'kokoro';
        voiceProviderEl.value = voiceProvider;
        await loadEmptyStateVoiceModels(voiceProvider);
        const voiceKey = getVoiceSettingsKey(voiceProvider);
        const defaultVoice = (generalData[voiceKey] || '').trim();
        if (defaultVoice && voiceModelEl.options.length) {
            const opt = Array.from(voiceModelEl.options).find(o => o.value === defaultVoice);
            if (opt) voiceModelEl.value = defaultVoice;
            else if (voiceModelEl.options[0] && voiceModelEl.options[0].value) voiceModelEl.selectedIndex = 0;
        } else if (voiceModelEl.options[0] && voiceModelEl.options[0].value) voiceModelEl.selectedIndex = 0;
        updateChatVoiceButtons('emptyState');
    } catch (e) {
        console.error('Error loading empty-state dropdowns:', e);
    }
    // Load skin picker
    await loadEmptyStateSkins();
    // Show/hide Ollama download button
    toggleEmptyStateOllamaPullBtn();
    // Hide Kilo promo if KiloCode is already a provider
    toggleKiloPromo('emptyStateLlmProvider');
    // Reveal the form, hide the loader, enable input
    revealEmptyStateForm();
}

function revealEmptyStateForm() {
    const loader = document.getElementById('emptyStateLoader');
    const form = document.getElementById('emptyStateForm');
    const prompt = document.getElementById('emptyStatePrompt');
    if (loader) loader.style.display = 'none';
    if (form) form.style.display = '';
    if (prompt) prompt.style.display = '';
    // Enable the message input now that everything is ready
    if (!loadedChatId && !currentChatId) {
        messageInput.disabled = false;
        messageInput.placeholder = 'Send message...';
        sendButton.disabled = !messageInput.value.trim();
    }
}

// ── Skin picker in empty state ──
let _emptyStateSkinSelection = null;

async function loadEmptyStateSkins() {
    const grid = document.getElementById('emptyStateSkinGrid');
    if (!grid) return;
    try {
        const res = await fetch('/api/skins');
        if (!res.ok) return;
        const data = await res.json();
        const skins = data.skins || [];
        const selected = data.selected_skin || 'oracle';
        _emptyStateSkinSelection = selected;
        grid.innerHTML = '';
        skins.forEach(function(skin) {
            const isSel = skin.folder_name === selected;
            const card = document.createElement('div');
            card.className = 'empty-state-skin-card' + (isSel ? ' selected' : '');
            card.dataset.folder = skin.folder_name;

            const idleFile = skin.idle_animation || 'idle.webm';
            const previewUrl = '/api/skins/' + encodeURIComponent(skin.folder_name) + '/preview/' + encodeURIComponent(idleFile);
            const ext = idleFile.split('.').pop().toLowerCase();
            const isOracle = skin.type === 'oracle';

            const previewWrap = document.createElement('div');
            previewWrap.className = 'empty-state-skin-preview' + (isOracle ? '' : ' avatar-preview');

            let previewEl;
            if (ext === 'webm') {
                previewEl = document.createElement('video');
                previewEl.src = previewUrl;
                previewEl.autoplay = true;
                previewEl.muted = true;
                previewEl.loop = true;
                previewEl.setAttribute('playsinline', '');
            } else {
                previewEl = document.createElement('img');
                previewEl.src = previewUrl;
                previewEl.onerror = function() { this.style.display = 'none'; };
            }
            previewWrap.appendChild(previewEl);
            card.appendChild(previewWrap);

            const nameEl = document.createElement('span');
            nameEl.className = 'empty-state-skin-name';
            nameEl.textContent = skin.name || skin.folder_name;
            card.appendChild(nameEl);

            card.addEventListener('click', function() {
                _emptyStateSkinSelection = skin.folder_name;
                grid.querySelectorAll('.empty-state-skin-card').forEach(function(c) {
                    c.classList.toggle('selected', c.dataset.folder === skin.folder_name);
                });
            });

            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Error loading skins for empty state:', e);
    }
}

function applySelectedSkin() {
    if (!_emptyStateSkinSelection) return;
    fetch('/api/skins/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skin_name: _emptyStateSkinSelection })
    }).catch(function(e) { console.error('Failed to apply skin:', e); });
}

// ── Ollama model download button in empty state ──
function toggleEmptyStateOllamaPullBtn() {
    const btn = document.getElementById('emptyStateOllamaPullBtn');
    const provider = document.getElementById('emptyStateLlmProvider');
    if (!btn || !provider) return;
    btn.style.display = (provider.value === 'ollama') ? '' : 'none';
}

function toggleKiloPromo(providerSelectId, containerSelector) {
    const providerEl = document.getElementById(providerSelectId);
    if (!providerEl) return;
    const hasKilo = Array.from(providerEl.options).some(o => o.value.toLowerCase() === 'kilocode');
    const container = providerEl.closest(containerSelector || '.empty-state-form, .modal-body');
    if (!container) return;
    const kiloWrap = container.querySelector('.empty-state-kilo-wrap');
    const keyHint = container.querySelector('.empty-state-key-hint-wrap');
    if (kiloWrap) kiloWrap.style.display = hasKilo ? 'none' : '';
    if (keyHint) keyHint.style.display = hasKilo ? 'none' : '';
}

async function pullOllamaModelFromEmptyState() {
    window.open('/settings#llms', '_blank');
}

// ── Modal: Ollama download + skin picker ──
let _modalSkinSelection = null;

function toggleModalOllamaPullBtn() {
    const btn = document.getElementById('modalOllamaPullBtn');
    const provider = document.getElementById('llmProvider');
    if (!btn || !provider) return;
    btn.style.display = (provider.value === 'ollama') ? '' : 'none';
}

async function pullOllamaModelFromModal() {
    window.open('/settings#llms', '_blank');
}

async function loadModalSkins() {
    const grid = document.getElementById('modalSkinGrid');
    if (!grid) return;
    try {
        const res = await fetch('/api/skins');
        if (!res.ok) return;
        const data = await res.json();
        const skins = data.skins || [];
        const selected = data.selected_skin || 'oracle';
        _modalSkinSelection = selected;
        grid.innerHTML = '';
        skins.forEach(function(skin) {
            const isSel = skin.folder_name === selected;
            const card = document.createElement('div');
            card.className = 'empty-state-skin-card' + (isSel ? ' selected' : '');
            card.dataset.folder = skin.folder_name;
            const idleFile = skin.idle_animation || 'idle.webm';
            const previewUrl = '/api/skins/' + encodeURIComponent(skin.folder_name) + '/preview/' + encodeURIComponent(idleFile);
            const ext = idleFile.split('.').pop().toLowerCase();
            const isOracle = skin.type === 'oracle';
            const previewWrap = document.createElement('div');
            previewWrap.className = 'empty-state-skin-preview' + (isOracle ? '' : ' avatar-preview');
            let previewEl;
            if (ext === 'webm') {
                previewEl = document.createElement('video');
                previewEl.src = previewUrl;
                previewEl.autoplay = true; previewEl.muted = true; previewEl.loop = true;
                previewEl.setAttribute('playsinline', '');
            } else {
                previewEl = document.createElement('img');
                previewEl.src = previewUrl;
            }
            previewWrap.appendChild(previewEl);
            card.appendChild(previewWrap);
            const nameEl = document.createElement('span');
            nameEl.className = 'empty-state-skin-name';
            nameEl.textContent = skin.name || skin.folder_name;
            card.appendChild(nameEl);
            card.addEventListener('click', function() {
                _modalSkinSelection = skin.folder_name;
                grid.querySelectorAll('.empty-state-skin-card').forEach(function(c) {
                    c.classList.toggle('selected', c.dataset.folder === skin.folder_name);
                });
            });
            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Error loading modal skins:', e);
    }
}

function applyModalSelectedSkin() {
    if (!_modalSkinSelection) return;
    fetch('/api/skins/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skin_name: _modalSkinSelection })
    }).catch(function(e) { console.error('Failed to apply skin:', e); });
}

async function loadEmptyStateLlmModels(provider) {
    const el = document.getElementById('emptyStateLlmModel');
    if (!el) return;
    el.innerHTML = '<option value="">Loading…</option>';
    try {
        const response = await fetch(`/api/llms/models?type=conversational&provider=${encodeURIComponent(provider)}`);
        const data = await response.json();
        const models = data.models || [];
        el.innerHTML = models.length ? models.map(m => `<option value="${escapeHtml(m.id || m)}">${escapeHtml(m.name || m.id || m)}</option>`).join('') : '<option value="">No models</option>';
    } catch (e) {
        el.innerHTML = '<option value="">Error loading</option>';
    }
}

async function loadEmptyStateVoiceModels(provider) {
    const el = document.getElementById('emptyStateVoiceModel');
    if (!el) return;
    el.innerHTML = '<option value="">Loading…</option>';
    if (!provider) {
        el.innerHTML = '<option value="">Select provider</option>';
        return;
    }
    try {
        const response = await fetch(`/api/voices/${encodeURIComponent(provider)}`);
        const data = await response.json();
        const voices = Array.isArray(data) ? data : (data.voices || data.chats || []);
        el.innerHTML = voices.length ? voices.map(v => `<option value="${escapeHtml(v.id || v)}">${escapeHtml(v.name || v.id || v)}</option>`).join('') : '<option value="">No voices</option>';
    } catch (e) {
        el.innerHTML = '<option value="">Error loading</option>';
    }
}

// Render Messages. When preserveOnEmpty is true, don't replace with empty (avoids clearing during send flow).
/** Track how many child message elements are currently rendered to enable incremental append. */
let _renderedMessageCount = 0;

function renderMessages(messages, preserveOnEmpty) {
    // Always clean up streaming/typing state when rendering messages
    removeTypingIndicator();
    const streamingEl = document.getElementById('streamingAssistantMessage');
    if (streamingEl) streamingEl.remove();

    if (messages.length === 0) {
        if (preserveOnEmpty && chatMessages.children.length > 0) return;
        chatMessages.innerHTML = '';
        _renderedMessageCount = 0;
        _optimisticUserMessages.clear();
        return;
    }
    // Incremental render: only append new messages instead of wiping the whole DOM.
    // When messages.length < _renderedMessageCount, the chat was reloaded (e.g. switching chats),
    // so we do a full rebuild.
    if (messages.length > _renderedMessageCount && messages.length - _renderedMessageCount <= 10) {
        // Fast path: append only new messages, skipping ones already rendered optimistically
        for (let i = _renderedMessageCount; i < messages.length; i++) {
            const msg = messages[i];
            // Skip user messages we already rendered via sendMessage()
            if (msg.role === 'user' && msg.content && _optimisticUserMessages.has(msg.content.substring(0, 100))) {
                continue;
            }
            const messageDiv = createMessageElement(msg);
            chatMessages.appendChild(messageDiv);
        }
        _renderedMessageCount = messages.length;
        scrollToBottom();
        return;
    }
    // Full rebuild (chat switch, refresh, etc.)
    chatMessages.innerHTML = '';
    _optimisticUserMessages.clear();
    messages.forEach(message => {
        const messageDiv = createMessageElement(message);
        chatMessages.appendChild(messageDiv);
    });
    _renderedMessageCount = messages.length;
    scrollToBottomImmediate();
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function initTTSPlayer() {
    if (!ttsPlayerAudio || !ttsPlayerCard) return;
    const audio = ttsPlayerAudio;

    function updateTimeDisplay() {
        const current = audio.currentTime;
        const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
        if (ttsPlayerTime) ttsPlayerTime.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
        if (ttsPlayerSeek) {
            const pct = duration > 0 ? (current / duration) * 100 : 0;
            ttsPlayerSeek.value = String(pct);
        }
    }

    function showPlayPause(playing) {
        if (ttsPlayerPlay) ttsPlayerPlay.style.display = playing ? 'none' : 'flex';
        if (ttsPlayerPause) ttsPlayerPause.style.display = playing ? 'flex' : 'none';
    }

    audio.addEventListener('loadedmetadata', () => {
        updateTimeDisplay();
        if (ttsPlayerSeek) ttsPlayerSeek.max = 100;
    });
    audio.addEventListener('timeupdate', updateTimeDisplay);
    audio.addEventListener('ended', () => {
        showPlayPause(false);
        audio.currentTime = 0;
        updateTimeDisplay();
    });
    audio.addEventListener('error', () => showPlayPause(false));

    if (ttsPlayerPlay) {
        ttsPlayerPlay.addEventListener('click', () => {
            if (!audio.src) return;
            audio.playbackRate = ttsPlayerSpeed ? parseFloat(ttsPlayerSpeed.value) : 1;
            audio.play().catch(() => {});
            showPlayPause(true);
        });
    }
    if (ttsPlayerPause) {
        ttsPlayerPause.addEventListener('click', () => {
            audio.pause();
            showPlayPause(false);
        });
    }
    if (ttsPlayerStop) {
        ttsPlayerStop.addEventListener('click', () => {
            audio.pause();
            audio.currentTime = 0;
            showPlayPause(false);
            updateTimeDisplay();
            // Also interrupt agent TTS (PTT/welcome message playback)
            if (loadedChatId) {
                fetch(`${API_BASE}/chats/${loadedChatId}/cancel`, { method: 'POST' }).catch(() => {});
            }
        });
    }
    if (ttsPlayerSeek) {
        ttsPlayerSeek.addEventListener('input', () => {
            const pct = parseFloat(ttsPlayerSeek.value);
            const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
            audio.currentTime = (pct / 100) * duration;
        });
    }
    if (ttsPlayerSpeed) {
        ttsPlayerSpeed.addEventListener('change', () => {
            audio.playbackRate = parseFloat(ttsPlayerSpeed.value);
        });
    }
    if (ttsPlayerDownload) {
        ttsPlayerDownload.addEventListener('click', () => {
            if (!ttsPlayerBlob) return;
            const a = document.createElement('a');
            a.href = URL.createObjectURL(ttsPlayerBlob);
            a.download = ttsPlayerDownloadFilename;
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }
    if (ttsPlayerClose && ttsPlayerCard) {
        ttsPlayerClose.addEventListener('click', () => {
            if (ttsPlayerAudio) {
                ttsPlayerAudio.pause();
                ttsPlayerAudio.currentTime = 0;
            }
            ttsPlayerCard.style.display = 'none';
            // Also interrupt agent TTS
            if (loadedChatId) {
                fetch(`${API_BASE}/chats/${loadedChatId}/cancel`, { method: 'POST' }).catch(() => {});
            }
        });
    }
}

function getTTSDownloadFilename() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const h = String(now.getHours()).padStart(2, '0');
    const min = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    return `tts_${y}${m}${d}${h}${min}${s}.mp3`;
}

/** Load TTS blob into the sidebar player; show and focus the player, then auto-play when ready. */
function loadTTSIntoPlayer(blob) {
    if (!ttsPlayerCard || !ttsPlayerAudio) return;
    if (ttsPlayerObjectURL) {
        ttsPlayerAudio.pause();
        ttsPlayerAudio.removeAttribute('src');
        ttsPlayerAudio.load();
        URL.revokeObjectURL(ttsPlayerObjectURL);
        ttsPlayerObjectURL = null;
    }
    ttsPlayerBlob = blob;
    ttsPlayerDownloadFilename = getTTSDownloadFilename();
    ttsPlayerObjectURL = URL.createObjectURL(blob);
    ttsPlayerAudio.src = ttsPlayerObjectURL;
    ttsPlayerAudio.playbackRate = ttsPlayerSpeed ? parseFloat(ttsPlayerSpeed.value) : 1;
    if (ttsPlayerPlay) ttsPlayerPlay.style.display = 'flex';
    if (ttsPlayerPause) ttsPlayerPause.style.display = 'none';
    ttsPlayerTime.textContent = '0:00 / 0:00';
    ttsPlayerSeek.value = '0';
    ttsPlayerCard.style.display = 'block';
    ttsPlayerCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    ttsPlayerCard.setAttribute('tabindex', '-1');
    ttsPlayerCard.focus();
    function onReady() {
        ttsPlayerAudio.removeEventListener('canplaythrough', onReady);
        ttsPlayerAudio.play().then(() => {
            if (ttsPlayerPlay) ttsPlayerPlay.style.display = 'none';
            if (ttsPlayerPause) ttsPlayerPause.style.display = 'flex';
        }).catch(() => {});
    }
    ttsPlayerAudio.addEventListener('canplaythrough', onReady);
    ttsPlayerAudio.load();
}

/** Fetch TTS for text and load into sidebar player; then bring player into focus (no auto-play). */
async function playTTS(button) {
    const messageEl = button.closest('.message');
    const textEl = messageEl ? messageEl.querySelector('.message-text') : null;
    const text = textEl ? textEl.textContent.trim() : '';
    if (!text) return;
    const originalLabel = button.textContent;
    button.textContent = '…';
    button.disabled = true;
    try {
        const payload = { text, speed: 1.0, format: 'mp3' };
        if (currentChatId != null) payload.chat_id = currentChatId;
        const response = await fetch(`${API_BASE}/chats/tts/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || err.error || response.statusText);
        }
        const blob = await response.blob();
        loadTTSIntoPlayer(blob);
    } catch (e) {
        console.error('TTS error:', e);
        alert('Could not generate TTS: ' + (e.message || 'Please try again.'));
    } finally {
        button.textContent = originalLabel;
        button.disabled = false;
    }
}

function _formatTimestamp(ts) {
    if (!ts) return '';
    try {
        const d = new Date(ts);
        if (isNaN(d.getTime())) return '';
        const h = d.getHours();
        const m = d.getMinutes().toString().padStart(2, '0');
        const ampm = h >= 12 ? 'pm' : 'am';
        return `${h % 12 || 12}:${m} ${ampm}`;
    } catch (_) { return ''; }
}

function createMessageElement(message) {
    const div = document.createElement('div');
    div.className = `message ${message.role}`;
    const avatarSvg = message.role === 'user' ? AVATAR_SVG_USER : AVATAR_SVG_ASSISTANT;
    const actionsHtml = message.role === 'assistant'
        ? `<button class="message-action-btn" onclick="copyMessage(this)">Copy</button>
           <button class="message-action-btn message-action-play" onclick="playTTS(this)" title="Play with TTS">Play</button>`
        : `<button class="message-action-btn" onclick="copyMessage(this)">Copy</button>`;

    const ts = _formatTimestamp(message.timestamp);
    const headerTimestamp = ts ? `<span class="message-header-timestamp">${ts}</span>` : '';

    div.innerHTML = `
        <div class="message-avatar">${avatarSvg}</div>
        <div class="message-content">
            ${headerTimestamp}
            <div class="message-text">${formatMessage(message.content)}</div>
            <div class="message-actions">
                ${actionsHtml}
            </div>
        </div>
    `;
    return div;
}

function formatMessage(text) {
    if (!text) return '';
    
    // Escape HTML
    let html = escapeHtml(text);
    
    // Format code blocks
    html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
        return `<pre><code>${escapeHtml(code.trim())}</code></pre>`;
    });
    
    // Format inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // Format links
    html = html.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" style="color: #19c37d;">$1</a>');
    
    // Format line breaks
    html = html.replace(/\n/g, '<br>');
    
    return html;
}

// Send Message (goes to loaded chat only - matches native chat.py add_to_chat_thread)
// Guard: prevent double submission (e.g. Enter + click or double-tap)
async function sendMessage() {
    const message = messageInput.value.trim();
    if (!message || isStreaming) return;
    isStreaming = true;
    sendButton.disabled = true;
    if (!loadedChatId) {
        if (currentChatId) {
            isStreaming = false;
            sendButton.disabled = !messageInput.value.trim();
            alert('Please load a chat to reply.');
            return;
        }
        const emptyLlmProvider = document.getElementById('emptyStateLlmProvider');
        const emptyLlmModel = document.getElementById('emptyStateLlmModel');
        const emptyVoiceProvider = document.getElementById('emptyStateVoiceProvider');
        const emptyVoiceModel = document.getElementById('emptyStateVoiceModel');
        const provider = emptyLlmProvider?.value?.trim();
        const modelName = emptyLlmModel?.value?.trim();
        const voiceProvider = emptyVoiceProvider?.value?.trim();
        const voiceModel = emptyVoiceModel?.value?.trim();
        if (!provider || !modelName) {
            isStreaming = false;
            sendButton.disabled = !messageInput.value.trim();
            alert('Please select LLM provider and model in Setup your Agent.');
            return;
        }
        if (!voiceProvider || !voiceModel) {
            isStreaming = false;
            sendButton.disabled = !messageInput.value.trim();
            alert('Please select Voice provider and voice in Setup your Agent.');
            return;
        }
        showEmptyStateAgentLoading();
        const savedPlaceholder = messageInput.placeholder;
        messageInput.placeholder = 'Agent is loading…';
        messageInput.disabled = true;
        streamAbortController = new AbortController();
        setSendButtonStreaming(true);
        let newChatId = null;
        try {
            const response = await fetch(`${API_BASE}/chats`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: null,
                    provider,
                    model_name: modelName,
                    voice_provider: voiceProvider,
                    voice_model: voiceModel,
                    starting_question: message,
                    speak: Boolean(ttsEnabled)
                })
            });
            const data = await response.json();
            if (!response.ok) {
                hideEmptyStateAgentLoading();
                messageInput.disabled = false;
                messageInput.placeholder = savedPlaceholder;
                isStreaming = false;
                streamAbortController = null;
                sendButton.disabled = !messageInput.value.trim();
                setSendButtonStreaming(false);
                alert('Could not create chat: ' + (data.detail || response.statusText || 'Please try again.'));
                return;
            }
            newChatId = data.id;
            loadedChatId = data.id;
            currentChatId = data.id;
            // Apply selected skin when initializing chat
            if (typeof applySelectedSkin === 'function') applySelectedSkin();
            updateActiveChat();
            // Clear input immediately so user sees it sent
            messageInput.value = '';
            messageInput.style.height = 'auto';
            showChatView(true);
            // Add user message to UI while waiting for agent
            const userMsg = createMessageElement({ role: 'user', content: message });
            chatMessages.appendChild(userMsg);
            _optimisticUserMessages.add(message.substring(0, 100));
            const typing = createTypingIndicator();
            chatMessages.appendChild(typing);
            scrollToBottom();
            // Ensure WebSocket is connected for new chat (force=true bypasses isStreaming)
            startChatWebSocket(true);
            // Wait for agent response (WebSocket stream_finished or polling fallback)
            await pollUntilAgentResponse(streamAbortController.signal);
            hideEmptyStateAgentLoading();
            await loadChats();
            if (!ttsEnabled) {
                fetch(`${API_BASE}/chats/restore-speaker`, { method: 'POST' }).catch(() => {});
            }
            return;
        } catch (error) {
            hideEmptyStateAgentLoading();
            messageInput.disabled = false;
            messageInput.placeholder = savedPlaceholder;
            if (error.name !== 'AbortError') {
                alert(error.message || 'Failed to send message. Please try again.');
            }
            if (newChatId != null) {
                try {
                    await loadChats();
                    await loadChat(newChatId);
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
                } catch (_) {}
            }
            return;
        } finally {
            messageInput.disabled = false;
            messageInput.placeholder = savedPlaceholder;
            isStreaming = false;
            streamAbortController = null;
            sendButton.disabled = !messageInput.value.trim();
            setSendButtonStreaming(false);
        }
    }
    // Ensure WebSocket is connected and subscribed BEFORE sending (bypass isStreaming guard)
    startChatWebSocket(true);
    // Clear input immediately on send (Enter or button) so user sees it cleared
    messageInput.value = '';
    messageInput.style.height = 'auto';
    handleInputChange();
    chatMessages.appendChild(createMessageElement({ role: 'user', content: message }));
    _optimisticUserMessages.add(message.substring(0, 100));
    chatMessages.appendChild(createTypingIndicator());
    scrollToBottom();
    setSendButtonStreaming(true);
    streamAbortController = new AbortController();
    try {
        await sendToAgentAndPoll(message, streamAbortController.signal);
    } catch (error) {
        removeTypingIndicator();
        if (error.name !== 'AbortError') {
            addErrorMessage(error.message || 'Failed to send message. Please try again.');
        }
    } finally {
        isStreaming = false;
        streamAbortController = null;
        sendButton.disabled = !messageInput.value.trim();
        setSendButtonStreaming(false);
    }
}

// Try to send to agent; on failure (agent not ready) retry with capped backoff until success or max attempts.
async function sendToAgentWhenReady(message, abortSignal) {
    // Capture chat ID once — loadedChatId may change if user switches chats during retry delays
    const targetChatId = loadedChatId;
    // If this chat was loaded with skipLoadInAgent (new chat from modal), ensure agent has full setup before first send.
    if (loadedChatSkippedLoadInAgent && targetChatId) {
        loadedChatSkippedLoadInAgent = false;
        try {
            await fetch(`${API_BASE}/chats/${targetChatId}/load-in-agent`, { method: 'POST' });
            agentCurrentChatId = targetChatId;
            updateActiveChat();
        } catch (e) {
            console.warn('Load-in-agent before send failed:', e);
        }
    }

    const maxAttempts = 20;
    const baseDelayMs = 800;
    const maxDelayMs = 3000;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (abortSignal && abortSignal.aborted) throw new DOMException('Aborted', 'AbortError');
        const body = { message, chat_id: targetChatId, speak: Boolean(ttsEnabled) };
        if (currentChatSettings?.provider && currentChatSettings?.model_name) {
            body.provider = currentChatSettings.provider;
            body.model_name = currentChatSettings.model_name;
        }
        if (currentChatSettings?.voice_provider || currentChatSettings?.voice_model) {
            body.voice_provider = currentChatSettings.voice_provider || null;
            body.voice_model = currentChatSettings.voice_model || null;
        }
        const res = await fetch(`${API_BASE}/chats/${targetChatId}/send-to-agent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (res.ok) return;
        const errBody = await res.text();
        const isRetryable = res.status >= 500 || /unavailable|connection|ECONNREFUSED|not available/i.test(errBody);
        if (!isRetryable || attempt === maxAttempts) {
            let errMsg = 'Failed to send to agent';
            try {
                const j = JSON.parse(errBody);
                if (j.detail) errMsg = typeof j.detail === 'string' ? j.detail : (j.detail[0]?.msg || errMsg);
            } catch (_) {
                if (errBody) errMsg = errBody.slice(0, 200);
            }
            if (attempt === maxAttempts && isRetryable) errMsg = 'Agent did not become ready in time. Please try again.';
            throw new Error(errMsg);
        }
        // Show attempt count in placeholder so user knows it's retrying, not hung
        if (messageInput) messageInput.placeholder = `Waiting for agent… (${attempt}/${maxAttempts})`;
        const delayMs = Math.min(baseDelayMs * attempt, maxDelayMs);
        await new Promise(r => setTimeout(r, delayMs));
    }
}

// Wait for agent response: WebSocket stream_finished OR polling. Always polls in parallel.
async function pollUntilAgentResponse(abortSignal) {
    if (!loadedChatId) throw new Error('No chat loaded');
    const myToken = _streamToken;
    const myChatId = loadedChatId;
    // Use client-side count instead of fetching the entire chat just to snapshot initialCount.
    // This removes a ~100-200ms HTTP roundtrip + full JSON serialization before we even poll.
    const adjustedInitialCount = _renderedMessageCount + 1; // +1 for the user message just appended
    // Poll at a slower rate since WebSocket is our primary delivery path.
    // Polling is purely a fallback for when WebSocket events are lost.
    const pollMs = 3000;   // was 600ms — WebSocket handles real-time; poll is just insurance
    const timeoutMs = 120000;

    let pollDone = false;
    let pollInterval = null;
    let pollTimeout = null;

    const done = () => {
        if (pollDone) return;
        pollDone = true;
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        if (pollTimeout) { clearTimeout(pollTimeout); pollTimeout = null; }
        if (window._agentStreamResolve === resolveStream) window._agentStreamResolve = null;
        removeTypingIndicator();
        if (streamingChatId === myChatId) streamingChatId = null;
    };

    let resolveStream;
    return new Promise((resolve, reject) => {
        const finish = () => { done(); resolve(); };
        resolveStream = finish;
        window._agentStreamResolve = finish;

        pollInterval = setInterval(async () => {
            if (pollDone || (abortSignal && abortSignal.aborted)) return;
            // Self-cancel if chat has changed
            if (_streamToken !== myToken) { done(); reject(new DOMException('Aborted', 'AbortError')); return; }
            const r = await fetch(`${API_BASE}/chats/${myChatId}`).catch(() => null);
            if (!r || !r.ok) return;
            const data = await r.json();
            const messages = (data.messages || []);
            const has_new = messages.length > adjustedInitialCount;
            const last_is_assistant = messages.length && messages[messages.length - 1].role === 'assistant';
            if (has_new && last_is_assistant) {
                finish();
                if (currentChatId === myChatId) {
                    renderMessages(messages, false);
                    updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display });
                    scrollToBottom();
                }
            } else if (has_new && streamingChatId !== myChatId && currentChatId === myChatId) {
                renderMessages(messages, false);
                updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display });
                scrollToBottom();
            }
        }, pollMs);

        pollTimeout = setTimeout(async () => {
            if (pollDone) return;
            finish();
            if (currentChatId !== myChatId) return;
            const r = await fetch(`${API_BASE}/chats/${myChatId}`).catch(() => null);
            if (r && r.ok) {
                const d = await r.json();
                const msgs = (d.messages || []);
                if (msgs.some(m => m.role === 'assistant')) {
                    renderMessages(msgs, false);
                    updateChatSettingsDisplay({ title: d.title || 'New Chat', provider: d.provider || '-', model_name: d.model_name || '-', voice_provider: d.voice_provider, voice_model: d.voice_model, voice_model_display: d.voice_model_display });
                    scrollToBottom();
                }
            }
        }, timeoutMs);

        if (abortSignal) {
            abortSignal.addEventListener('abort', () => { done(); reject(new DOMException('Aborted', 'AbortError')); });
        }
    });
}

// Send message to the agent (same path as voice). Waits for agent to accept, then waits for
// WebSocket stream_finished event OR polling finds the message. Always polls in parallel so
// the message appears even when WebSocket events never arrive.
async function sendToAgentAndPoll(message, abortSignal) {
    const myToken = _streamToken;
    const myChatId = loadedChatId;
    // Use client-side rendered count instead of fetching the entire chat via HTTP.
    // This removes a full HTTP roundtrip + JSON serialization + DB query before every send.
    const initialCount = _renderedMessageCount;
    // Account for the user message + typing indicator we just appended
    const adjustedInitialCount = initialCount + 1; // +1 for the user message just appended
    await sendToAgentWhenReady(message, abortSignal);
    if (_streamToken !== myToken) throw new DOMException('Aborted', 'AbortError');
    // Poll at a slower rate since WebSocket is our primary delivery path.
    // Polling is purely a fallback for when WebSocket events are lost.
    const pollMs = 3000;  // was 600ms — WebSocket handles real-time; poll is just insurance
    const timeoutMs = 120000;

    let pollDone = false;
    let pollInterval = null;
    let pollTimeout = null;

    const done = () => {
        if (pollDone) return;
        pollDone = true;
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        if (pollTimeout) { clearTimeout(pollTimeout); pollTimeout = null; }
        if (window._agentStreamResolve === resolveStream) window._agentStreamResolve = null;
        removeTypingIndicator();
        if (streamingChatId === myChatId) streamingChatId = null;
    };

    let resolveStream;
    return new Promise((resolve, reject) => {
        const finish = () => { done(); resolve(); };
        resolveStream = finish;
        window._agentStreamResolve = finish;

        pollInterval = setInterval(async () => {
            if (pollDone || (abortSignal && abortSignal.aborted)) return;
            if (_streamToken !== myToken) { done(); reject(new DOMException('Aborted', 'AbortError')); return; }
            const r = await fetch(`${API_BASE}/chats/${myChatId}`).catch(() => null);
            if (!r || !r.ok) return;
            const data = await r.json();
            const messages = (data.messages || []);
            const has_new = messages.length > adjustedInitialCount;
            const last_is_assistant = messages.length && messages[messages.length - 1].role === 'assistant';
            if (has_new && last_is_assistant) {
                finish();
                if (currentChatId === myChatId) {
                    renderMessages(messages, false);
                    updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display });
                    scrollToBottom();
                }
            } else if (has_new && streamingChatId !== myChatId && currentChatId === myChatId) {
                renderMessages(messages, false);
                updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display });
                scrollToBottom();
            }
        }, pollMs);

        pollTimeout = setTimeout(async () => {
            if (pollDone) return;
            finish();
            if (currentChatId !== myChatId) return;
            const r = await fetch(`${API_BASE}/chats/${myChatId}`).catch(() => null);
            if (r && r.ok) {
                const d = await r.json();
                const msgs = (d.messages || []);
                if (msgs.some(m => m.role === 'assistant')) {
                    renderMessages(msgs, false);
                    updateChatSettingsDisplay({ title: d.title || 'New Chat', provider: d.provider || '-', model_name: d.model_name || '-', voice_provider: d.voice_provider, voice_model: d.voice_model, voice_model_display: d.voice_model_display });
                    scrollToBottom();
                }
            }
        }, timeoutMs);

        if (abortSignal) {
            abortSignal.addEventListener('abort', () => { done(); reject(new DOMException('Aborted', 'AbortError')); });
        }
    });
}

function cancelStream() {
    if (!loadedChatId) return;
    fetch(`${API_BASE}/chats/${loadedChatId}/cancel`, { method: 'POST' }).catch(() => {});
    if (streamAbortController) {
        streamAbortController.abort();
    }
}

function setSendButtonStreaming(streaming) {
    if (!sendButton) return;
    // Keep modal create button in sync — can't create a new chat while agent is responding
    if (modalCreate) modalCreate.disabled = streaming;
    if (streaming) {
        if (!sendButton.dataset.originalHtml) {
            sendButton.dataset.originalHtml = sendButton.innerHTML;
            sendButton.dataset.originalTitle = sendButton.title || 'Send';
        }
        sendButton.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
        sendButton.title = 'Stop';
    } else {
        if (sendButton.dataset.originalHtml) {
            sendButton.innerHTML = sendButton.dataset.originalHtml;
            sendButton.title = sendButton.dataset.originalTitle || 'Send';
        }
    }
}

function createTypingIndicator() {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = 'typingIndicator';
    div.innerHTML = `
        <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
        <div class="message-content">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    return div;
}

function removeTypingIndicator() {
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
        indicator.remove();
    }
}

function addErrorMessage(text) {
    const div = document.createElement('div');
    div.className = 'message assistant message-error';
    div.innerHTML = `
        <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
        <div class="message-content">
            <div class="message-text">${escapeHtml(text)}</div>
        </div>
    `;
    chatMessages.appendChild(div);
    scrollToBottom();
}

// Delete Chat — when deleting the loaded or current chat, load the first remaining chat and select it
async function deleteChat(chatId) {
    if (!confirm('Are you sure you want to delete this chat?')) {
        return;
    }
    const wasLoaded = (loadedChatId === chatId);
    // Abort any in-flight stream before tearing down state
    if (wasLoaded && streamAbortController) { streamAbortController.abort(); streamAbortController = null; }
    if (wasLoaded) { _streamToken++; isStreaming = false; setSendButtonStreaming(false); }
    try {
        await fetch(`${API_BASE}/chats/${chatId}`, { method: 'DELETE' });
        currentChatId = null;
        loadedChatId = null;
        chatMessages.innerHTML = '';
        _renderedMessageCount = 0;
        _optimisticUserMessages.clear();
        if (agentCurrentChatId === chatId) agentCurrentChatId = null;
        const data = await loadChats();
        if (data.chats && data.chats.length > 0) {
            await selectChat(data.chats[0].id);
        } else {
            showEmptyState();
            await saveLastChatId(null);
        }
        updateActiveChat();
        updateLoadButtonVisibility();
    } catch (error) {
        console.error('Error deleting chat:', error);
    }
}

async function renameChat(chatId) {
    const item = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
    const titleEl = item ? item.querySelector('.chat-item-title') : null;
    const currentTitle = (titleEl && titleEl.textContent) ? titleEl.textContent.trim() : '';
    const newTitle = prompt('Rename chat', currentTitle || 'New Chat');
    if (newTitle === null) return;
    const trimmed = newTitle.trim();
    try {
        const response = await fetch(`${API_BASE}/chats/${chatId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: trimmed || null })
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || response.statusText);
        }
        const data = await response.json();
        const displayTitle = data.title || 'New Chat';
        if (titleEl) titleEl.textContent = displayTitle;
        if (currentChatId === chatId) {
            const headerTitle = document.getElementById('chatTitle');
            if (headerTitle) headerTitle.textContent = displayTitle;
        }
        if (currentChatSettings && currentChatId === chatId) {
            currentChatSettings.title = displayTitle;
        }
    } catch (error) {
        console.error('Error renaming chat:', error);
        alert('Could not rename chat: ' + (error.message || 'Please try again.'));
    }
}

// Copy Message
function copyMessage(button) {
    const messageText = button.closest('.message').querySelector('.message-text').textContent;
    navigator.clipboard.writeText(messageText).then(() => {
        button.textContent = 'Copied!';
        setTimeout(() => {
            button.textContent = 'Copy';
        }, 2000);
    });
}

// Scroll to Bottom — rAF-throttled to avoid forced layout reflows on every event
let _scrollRafPending = false;
function scrollToBottom() {
    if (_scrollRafPending) return;
    _scrollRafPending = true;
    requestAnimationFrame(() => {
        _scrollRafPending = false;
        chatMessages.scrollTop = chatMessages.scrollHeight;
    });
}
/** Immediate scroll (not throttled) — only for one-shot events like initial load. */
function scrollToBottomImmediate() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Avatar SVGs matching website Hero (Hero.tsx): user = Volume2, assistant = brain/arcs (animated like Hero)
const AVATAR_SVG_USER = '<svg class="avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
const AVATAR_SVG_ASSISTANT = '<div class="avatar-icon-spin"><svg class="avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 3a9 9 0 0 1 9 9"/><path d="M12 21a9 9 0 0 1-9-9"/></svg></div>';

// Utility Functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Load LLM models for provider (settings API)
async function loadLlmModels(provider) {
    llmModelSelect.innerHTML = '<option value="">Loading...</option>';
    try {
        const response = await fetch(`/api/llms/models?type=conversational&provider=${encodeURIComponent(provider)}`);
        const data = await response.json();
        const models = data.models || [];
        llmModelSelect.innerHTML = models.length ? models.map(m => `<option value="${escapeHtml(m.id || m)}">${escapeHtml(m.name || m.id || m)}</option>`).join('') : '<option value="">No models</option>';
    } catch (e) {
        console.error('Error loading LLM models:', e);
        llmModelSelect.innerHTML = '<option value="">Error loading</option>';
    }
}

// Modal voice sample: same setup as Settings > General (general.js) — fetch WAV, play in browser, loading/playing state
let _modalVoiceAudio = null;
let _modalVoiceLoading = false;

function setModalVoiceUI(state) {
    const playIcon = document.getElementById('modal_play_icon');
    const stopIcon = document.getElementById('modal_stop_icon');
    const spinner = document.getElementById('modal_play_spinner');
    const btn = document.getElementById('modalPlayVoiceBtn');
    if (playIcon) playIcon.style.display = state === 'idle' ? '' : 'none';
    if (stopIcon) stopIcon.style.display = state === 'playing' ? '' : 'none';
    if (spinner) spinner.style.display = state === 'loading' ? '' : 'none';
    if (btn) btn.disabled = (state === 'loading');
}

function stopModalVoice() {
    _modalVoiceLoading = false;
    if (_modalVoiceAudio) {
        _modalVoiceAudio.pause();
        _modalVoiceAudio.currentTime = 0;
        _modalVoiceAudio = null;
    }
    setModalVoiceUI('idle');
}

async function playVoiceModal() {
    if (_modalVoiceLoading) return;
    if (_modalVoiceAudio && !_modalVoiceAudio.paused) {
        stopModalVoice();
        return;
    }
    const provider = (voiceProviderSelect.value || '').trim();
    const voice = (voiceModelSelect.value || '').trim();
    if (!provider || !voice) return;
    const voiceName = voiceModelSelect.options[voiceModelSelect.selectedIndex] ? voiceModelSelect.options[voiceModelSelect.selectedIndex].text : voice;

    stopModalVoice();
    _modalVoiceLoading = true;
    setModalVoiceUI('loading');

    try {
        const headers = { 'Content-Type': 'application/json' };
        const token = typeof window !== 'undefined' && window.DECISIONSAI_INTERNAL_API_TOKEN;
        if (token) headers['X-DecisionsAI-Internal-Token'] = token;
        const response = await fetch('/api/play-voice', {
            method: 'POST',
            headers,
            body: JSON.stringify({ provider, voice, speed: 1.0, voice_name: voiceName })
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || err.detail || 'Failed to generate voice sample');
        }
        const contentType = (response.headers.get('content-type') || '').toLowerCase();
        if (contentType.indexOf('audio') === -1 && contentType.indexOf('mpeg') === -1) {
            const text = await response.text();
            console.error('Play-voice returned non-audio:', contentType, text.slice(0, 200));
            throw new Error('Server returned non-audio. Check console.');
        }
        const blob = await response.blob();
        if (blob.size === 0) {
            throw new Error('Server returned empty audio.');
        }
        const url = URL.createObjectURL(blob);
        _modalVoiceAudio = new Audio(url);
        _modalVoiceAudio.onended = function() {
            setModalVoiceUI('idle');
            URL.revokeObjectURL(url);
            _modalVoiceAudio = null;
        };
        _modalVoiceAudio.onerror = function() {
            setModalVoiceUI('idle');
            URL.revokeObjectURL(url);
            _modalVoiceAudio = null;
            alert('Audio playback failed');
        };
        _modalVoiceLoading = false;
        setModalVoiceUI('playing');
        _modalVoiceAudio.play().catch((err) => {
            console.error('Modal voice play() failed:', err);
            setModalVoiceUI('idle');
            URL.revokeObjectURL(url);
            _modalVoiceAudio = null;
            alert('Audio playback failed: ' + ((err && err.message) || 'Unknown browser error'));
        });
    } catch (e) {
        console.error('Error playing voice:', e);
        alert('Failed to play voice: ' + (e.message || 'Please try again.'));
        _modalVoiceLoading = false;
        setModalVoiceUI('idle');
    }
}

// Empty-state voice sample (same API as modal)
let _emptyStateVoiceAudio = null;
let _emptyStateVoiceLoading = false;

function setEmptyStateVoiceUI(state) {
    const playIcon = document.getElementById('emptyState_play_icon');
    const stopIcon = document.getElementById('emptyState_stop_icon');
    const spinner = document.getElementById('emptyState_play_spinner');
    const btn = document.getElementById('emptyStatePlayVoiceBtn');
    if (playIcon) playIcon.style.display = state === 'idle' ? '' : 'none';
    if (stopIcon) stopIcon.style.display = state === 'playing' ? '' : 'none';
    if (spinner) spinner.style.display = state === 'loading' ? '' : 'none';
    if (btn) btn.disabled = (state === 'loading');
}

function stopEmptyStateVoice() {
    _emptyStateVoiceLoading = false;
    if (_emptyStateVoiceAudio) {
        _emptyStateVoiceAudio.pause();
        _emptyStateVoiceAudio.currentTime = 0;
        _emptyStateVoiceAudio = null;
    }
    setEmptyStateVoiceUI('idle');
}

async function playEmptyStateVoice() {
    if (_emptyStateVoiceLoading) return;
    if (_emptyStateVoiceAudio && !_emptyStateVoiceAudio.paused) {
        stopEmptyStateVoice();
        return;
    }
    const voiceProviderEl = document.getElementById('emptyStateVoiceProvider');
    const voiceModelEl = document.getElementById('emptyStateVoiceModel');
    const provider = (voiceProviderEl?.value || '').trim();
    const voice = (voiceModelEl?.value || '').trim();
    if (!provider || !voice) return;
    const voiceName = voiceModelEl?.options[voiceModelEl.selectedIndex] ? voiceModelEl.options[voiceModelEl.selectedIndex].text : voice;
    stopEmptyStateVoice();
    _emptyStateVoiceLoading = true;
    setEmptyStateVoiceUI('loading');
    try {
        const headers = { 'Content-Type': 'application/json' };
        const token = typeof window !== 'undefined' && window.DECISIONSAI_INTERNAL_API_TOKEN;
        if (token) headers['X-DecisionsAI-Internal-Token'] = token;
        const response = await fetch('/api/play-voice', {
            method: 'POST',
            headers,
            body: JSON.stringify({ provider, voice, speed: 1.0, voice_name: voiceName })
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || err.detail || 'Failed to generate voice sample');
        }
        const contentType = (response.headers.get('content-type') || '').toLowerCase();
        if (contentType.indexOf('audio') === -1 && contentType.indexOf('mpeg') === -1) {
            const text = await response.text();
            console.error('Play-voice returned non-audio:', contentType, text.slice(0, 200));
            throw new Error('Server returned non-audio. Check console.');
        }
        const blob = await response.blob();
        if (blob.size === 0) throw new Error('Server returned empty audio.');
        const url = URL.createObjectURL(blob);
        _emptyStateVoiceAudio = new Audio(url);
        _emptyStateVoiceAudio.onended = function() {
            setEmptyStateVoiceUI('idle');
            URL.revokeObjectURL(url);
            _emptyStateVoiceAudio = null;
        };
        _emptyStateVoiceAudio.onerror = function() {
            setEmptyStateVoiceUI('idle');
            URL.revokeObjectURL(url);
            _emptyStateVoiceAudio = null;
            alert('Audio playback failed');
        };
        _emptyStateVoiceLoading = false;
        setEmptyStateVoiceUI('playing');
        _emptyStateVoiceAudio.play().catch((err) => {
            console.error('Empty-state voice play() failed:', err);
            setEmptyStateVoiceUI('idle');
            URL.revokeObjectURL(url);
            _emptyStateVoiceAudio = null;
            alert('Audio playback failed: ' + ((err && err.message) || 'Unknown browser error'));
        });
    } catch (e) {
        console.error('Error playing empty-state voice:', e);
        alert('Failed to play voice: ' + (e.message || 'Please try again.'));
        _emptyStateVoiceLoading = false;
        setEmptyStateVoiceUI('idle');
    }
}

// Load voice options for provider (settings API – kokoro, openai, elevenlabs)
async function loadVoiceModels(provider) {
    voiceModelSelect.innerHTML = '<option value="">Loading…</option>';
    if (!provider) {
        voiceModelSelect.innerHTML = '<option value="">Select provider</option>';
        return;
    }
    try {
        const response = await fetch(`/api/voices/${encodeURIComponent(provider)}`);
        const data = await response.json();
        const voices = Array.isArray(data) ? data : (data.voices || data.chats || []);
        if (voices.length) {
            voiceModelSelect.innerHTML = voices.map(v => `<option value="${escapeHtml(v.id || v)}">${escapeHtml(v.name || v.id || v)}</option>`).join('');
        } else {
            voiceModelSelect.innerHTML = '<option value="">No voices available</option>';
        }
    } catch (e) {
        console.error('Error loading voice models:', e);
        voiceModelSelect.innerHTML = '<option value="">Error loading</option>';
    }
}

// Load default settings: LLM = conversational (Settings > LLMs), Voice = general (Settings > General)
async function loadDefaultSettings() {
    try {
        await _ensureTTSProviders();
        const [providersRes, modelsRes, generalRes] = await Promise.all([
            fetch('/api/llms/available-providers'),
            fetch(`${API_BASE}/models`),
            fetch('/api/general')
        ]);
        const providersData = providersRes.ok ? await providersRes.json() : { providers: [] };
        const modelsData = modelsRes.ok ? await modelsRes.json() : {};
        const generalData = generalRes.ok ? await generalRes.json() : {};

        const providers = providersData.providers || [];
        llmProviderSelect.innerHTML = providers.length
            ? providers.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('')
            : '<option value="">No providers configured</option>';

        const preferredProvider = (modelsData.provider || 'ollama').toLowerCase();
        const hasPreferred = providers.some(p => p.id === preferredProvider);
        const llmProvider = hasPreferred ? preferredProvider : (providers[0] ? providers[0].id : '');
        if (llmProvider) {
            llmProviderSelect.value = llmProvider;
            await loadLlmModels(llmProvider);
            const defaultModel = (modelsData.model || '').trim();
            if (defaultModel && llmModelSelect.options.length) {
                const opt = Array.from(llmModelSelect.options).find(o => o.value === defaultModel);
                if (opt) llmModelSelect.value = defaultModel;
                else if (llmModelSelect.options[0] && llmModelSelect.options[0].value) llmModelSelect.selectedIndex = 0;
            } else if (llmModelSelect.options[0] && llmModelSelect.options[0].value) {
                llmModelSelect.selectedIndex = 0;
            }
        }

        const voiceProvider = generalData.voice_provider || 'kokoro';
        voiceProviderSelect.value = voiceProvider;
        await loadVoiceModels(voiceProvider);
        const voiceKey = getVoiceSettingsKey(voiceProvider);
        const defaultVoice = (generalData[voiceKey] || '').trim();
        if (defaultVoice && voiceModelSelect.options.length) {
            const opt = Array.from(voiceModelSelect.options).find(o => o.value === defaultVoice);
            if (opt) voiceModelSelect.value = defaultVoice;
            else if (voiceModelSelect.options[0] && voiceModelSelect.options[0].value) voiceModelSelect.selectedIndex = 0;
        } else if (voiceModelSelect.options[0] && voiceModelSelect.options[0].value) {
            voiceModelSelect.selectedIndex = 0;
        }
        updateChatVoiceButtons('modal');
    } catch (error) {
        console.error('Error loading default settings:', error);
    }
}

// Show/Hide Modal
async function showNewChatModal() {
    newChatModal.style.display = 'flex';
    // Show loader, hide form
    const modalLoader = document.getElementById('modalLoader');
    const modalForm = document.getElementById('modalFormContent');
    if (modalLoader) modalLoader.style.display = '';
    if (modalForm) modalForm.style.display = 'none';
    // Disable create button if agent is currently streaming a response
    if (modalCreate) modalCreate.disabled = isStreaming;
    await loadDefaultSettings();
    loadModalSkins();
    toggleModalOllamaPullBtn();
    // Hide Kilo promo if KiloCode is already a provider
    toggleKiloPromo('llmProvider');
    // Reveal form, hide loader
    if (modalLoader) modalLoader.style.display = 'none';
    if (modalForm) modalForm.style.display = '';
    if (typeof injectInfoIcons === 'function') injectInfoIcons();
}

function hideNewChatModal() {
    stopModalVoice();
    newChatModal.style.display = 'none';
    if (startingQuestionInput) startingQuestionInput.value = 'Are you ready to help me?';
}

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && newChatModal.style.display !== 'none') {
        hideNewChatModal();
    }
});

// Create New Chat with Settings
// Flow: create chat (model selected) -> load it into the feed (clear everything else) -> once loaded, send starting question to agent like a normal message.
async function createNewChatWithSettings() {
    if (_createChatGuard) return;
    _createChatGuard = true;
    modalCreate.disabled = true;
    modalCreate.classList.add('btn--loading');
    newChatBtn.disabled = true;
    const llmProvider = llmProviderSelect.value;
    const llmModel = (llmModelSelect.value || '').trim();
    const voiceProvider = (voiceProviderSelect.value || '').trim();
    const voiceModel = (voiceModelSelect.value || '').trim();
    const startingQuestion = startingQuestionInput ? (startingQuestionInput.value || '').trim() : '';

    if (!llmProvider || !llmModel) {
        alert('Please select LLM provider and model.');
        _createChatGuard = false;
        modalCreate.disabled = false;
        modalCreate.classList.remove('btn--loading');
        newChatBtn.disabled = false;
        return;
    }
    if (!voiceProvider || !voiceModel) {
        alert('Please select Voice provider and voice.');
        _createChatGuard = false;
        modalCreate.disabled = false;
        modalCreate.classList.remove('btn--loading');
        newChatBtn.disabled = false;
        return;
    }
    if (!startingQuestion) {
        alert('Please enter a starting question.');
        _createChatGuard = false;
        modalCreate.disabled = false;
        modalCreate.classList.remove('btn--loading');
        newChatBtn.disabled = false;
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/chats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: null,
                provider: llmProvider,
                model_name: llmModel,
                voice_provider: voiceProvider,
                voice_model: voiceModel,
                starting_question: startingQuestion,
                speak: Boolean(ttsEnabled)
            })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || err.error || response.statusText);
        }

        const data = await response.json();

        currentChatSettings = {
            provider: llmProvider,
            model_name: llmModel,
            voice_provider: voiceProvider || null,
            voice_model: voiceModel || null
        };

        hideNewChatModal();
        if (typeof applyModalSelectedSkin === 'function') applyModalSelectedSkin();
        if (startingQuestionInput) startingQuestionInput.value = 'Are you ready to help me?';

        // Clear main message input when switching to the new chat
        if (messageInput) {
            messageInput.value = '';
            messageInput.style.height = 'auto';
            handleInputChange();
        }

        // Refresh sidebar and load the new chat into the feed (clears message area and shows only this chat).
        // Skip load-in-agent so we don't send current_chat_changed and cancel the in-flight response.
        await loadChats();
        await loadChat(data.id, { skipLoadInAgent: true });

        if (data.starting_question && loadedChatId === data.id) {
            // Message already rendered by loadChat() - just add typing indicator for agent response
            const typing = createTypingIndicator();
            chatMessages.appendChild(typing);
            scrollToBottom();
            isStreaming = true;
            setSendButtonStreaming(true);
            streamAbortController = new AbortController();
            // Ensure WebSocket is connected and subscription ACK'd before polling so stream events aren't missed
            startChatWebSocket(true);
            await new Promise(resolve => setTimeout(resolve, 80));
            try {
                // Poll until agent responds (waits for agent reload + response)
                await pollUntilAgentResponse(streamAbortController.signal);
            } catch (e) {
                removeTypingIndicator();
                if (e.name !== 'AbortError') {
                    addErrorMessage(e.message || 'Failed to get response');
                }
            } finally {
                isStreaming = false;
                streamAbortController = null;
                setSendButtonStreaming(false);
            }
        }
    } catch (error) {
        console.error('Error creating chat:', error);
        alert('Failed to create chat: ' + (error.message || 'Please try again.'));
    } finally {
        _createChatGuard = false;
        modalCreate.disabled = false;
        modalCreate.classList.remove('btn--loading');
        newChatBtn.disabled = false;
    }
}
function updateChatSettingsDisplay(settings) {
    const rawVoiceModel = settings.voice_model_raw ?? settings.voice_model ?? null;
    const displayVoiceModel = settings.voice_model_display || rawVoiceModel || null;
    currentChatSettings = {
        ...settings,
        voice_model: rawVoiceModel,
        voice_model_display: displayVoiceModel
    };

    const titleEl = document.getElementById('chatTitle');
    if (titleEl) titleEl.textContent = settings.title || 'New Chat';

    document.getElementById('chatProvider').textContent = settings.provider || '-';
    document.getElementById('chatModel').textContent = settings.model_name || '-';

    if (settings.voice_provider || displayVoiceModel) {
        document.getElementById('voiceSeparator').style.display = 'inline';
        document.getElementById('voiceLabel').style.display = 'inline';
        document.getElementById('chatVoiceProvider').style.display = 'inline';
        document.getElementById('chatVoiceProvider').textContent = settings.voice_provider || '-';
        document.getElementById('voiceModelSeparator').style.display = 'inline';
        document.getElementById('chatVoiceModel').style.display = 'inline';
        document.getElementById('chatVoiceModel').textContent = displayVoiceModel || '-';
    } else {
        document.getElementById('voiceSeparator').style.display = 'none';
        document.getElementById('voiceLabel').style.display = 'none';
        document.getElementById('chatVoiceProvider').style.display = 'none';
        document.getElementById('voiceModelSeparator').style.display = 'none';
        document.getElementById('chatVoiceModel').style.display = 'none';
    }

    chatSettingsHeader.style.display = 'flex';
}

// ── Custom Voice Management for Chat UI ──────────────────────────────────

const _CHAT_CV_PROVIDERS = new Set(['kokoro', 'elevenlabs', 'coqui']);
let _chatCvAudioMode = 'upload';
let _chatCvRecordedBlob = null;
let _chatCvMediaRecorder = null;
let _chatCvRecordStream = null;
let _chatCvRecordChunks = [];
let _chatCvRecordTimer = null;
let _chatCvRecordStartTime = 0;
let _chatCvRecordAnalyser = null;
let _chatCvRecordLevelRAF = null;
let _chatCvPlaybackAudio = null;
const _CHAT_CV_MAX_SECS = 12;

// Show/hide custom voice buttons based on provider and selected voice
function updateChatVoiceButtons(prefix) {
    const providerEl = document.getElementById(prefix === 'emptyState' ? 'emptyStateVoiceProvider' : 'voiceProvider');
    const voiceEl = document.getElementById(prefix === 'emptyState' ? 'emptyStateVoiceModel' : 'voiceModel');
    const customBtn = document.getElementById(prefix + 'CustomVoiceBtn');
    const deleteBtn = document.getElementById(prefix + 'DeleteVoiceBtn');
    const editBtn = document.getElementById(prefix + 'EditVoiceBtn');
    if (!providerEl || !voiceEl) return;

    const provider = providerEl.value;
    const voiceId = voiceEl.value;
    const supportsCustom = _CHAT_CV_PROVIDERS.has(provider);
    const isCustomVoice = voiceId && voiceId.startsWith('custom_');

    if (customBtn) customBtn.style.display = supportsCustom ? '' : 'none';
    if (deleteBtn) deleteBtn.style.display = isCustomVoice ? '' : 'none';
    if (editBtn) editBtn.style.display = isCustomVoice ? '' : 'none';
}

// Hook into provider and voice changes
(function() {
    const esVP = document.getElementById('emptyStateVoiceProvider');
    const esVM = document.getElementById('emptyStateVoiceModel');
    const mVP = document.getElementById('voiceProvider');
    const mVM = document.getElementById('voiceModel');

    function hookChange(el, prefix) {
        if (!el) return;
        el.addEventListener('change', () => updateChatVoiceButtons(prefix));
    }
    hookChange(esVP, 'emptyState');
    hookChange(esVM, 'emptyState');
    hookChange(mVP, 'modal');
    hookChange(mVM, 'modal');

    // Also update after voice lists load (programmatic value changes don't fire 'change')
    const origLoadES = window.loadEmptyStateVoiceModels;
    if (origLoadES) {
        window.loadEmptyStateVoiceModels = async function(p) {
            await origLoadES.call(this, p);
            updateChatVoiceButtons('emptyState');
        };
    }
    const origLoadM = window.loadVoiceModels;
    if (origLoadM) {
        window.loadVoiceModels = async function(p) {
            await origLoadM.call(this, p);
            updateChatVoiceButtons('modal');
        };
    }

    // Initial update after page load
    setTimeout(() => {
        updateChatVoiceButtons('emptyState');
        updateChatVoiceButtons('modal');
    }, 500);
})();

// ── Open / Close / Submit Custom Voice Modal ──

function openChatCustomVoiceModal(context) {
    const providerEl = document.getElementById(context === 'emptyState' ? 'emptyStateVoiceProvider' : 'voiceProvider');
    const provider = providerEl ? providerEl.value : 'kokoro';
    if (!_CHAT_CV_PROVIDERS.has(provider)) return;

    document.getElementById('chatCv_provider').value = provider;
    document.getElementById('chatCv_context').value = context;
    document.getElementById('chatCv_providerLabel').textContent = provider.charAt(0).toUpperCase() + provider.slice(1);
    document.getElementById('chatCv_name').value = '';
    document.getElementById('chatCv_personality').value = '';
    document.getElementById('chatCv_error').classList.add('hidden');
    document.getElementById('chatCv_form').classList.remove('hidden');
    document.getElementById('chatCv_processing').classList.add('hidden');
    document.getElementById('chatCv_submitBtn').disabled = true;
    setChatCvGender('female');
    setChatCvAudioMode('upload');
    _chatCvRecordedBlob = null;

    // Show/hide gender row (only for kokoro)
    document.getElementById('chatCv_genderRow').style.display = (provider === 'kokoro') ? '' : 'none';

    const modal = document.getElementById('chatCustomVoiceModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.getElementById('chatCv_name').focus();

    document.getElementById('chatCv_audio').onchange = function() {
        document.getElementById('chatCv_submitBtn').disabled = !(this.files && this.files.length > 0);
    };
}

function closeChatCustomVoiceModal() {
    _cleanupChatCvRecording();
    _chatCvRecordedBlob = null;
    const modal = document.getElementById('chatCustomVoiceModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

function setChatCvGender(gender) {
    document.getElementById('chatCv_gender').value = gender;
    const f = document.getElementById('chatCv_genderFemale');
    const m = document.getElementById('chatCv_genderMale');
    const active = 'px-4 py-1.5 text-sm rounded transition-colors bg-[#10a37f] text-white';
    const inactive = 'px-4 py-1.5 text-sm rounded transition-colors text-gray-400 hover:text-white';
    f.className = gender === 'female' ? active : inactive;
    m.className = gender === 'male' ? active : inactive;
}

function setChatCvAudioMode(mode) {
    _chatCvAudioMode = mode;
    const active = 'px-4 py-1.5 text-sm rounded transition-colors bg-[#10a37f] text-white';
    const inactive = 'px-4 py-1.5 text-sm rounded transition-colors text-gray-400 hover:text-white';
    document.getElementById('chatCv_mode_upload').className = mode === 'upload' ? active : inactive;
    document.getElementById('chatCv_mode_record').className = mode === 'record' ? active : inactive;
    document.getElementById('chatCv_uploadPanel').classList.toggle('hidden', mode !== 'upload');
    document.getElementById('chatCv_recordPanel').classList.toggle('hidden', mode !== 'record');
    if (mode === 'record') { _resetChatCvRecUI(); } else { _cleanupChatCvRecording(); }
    _updateChatCvSubmit();
}

function _updateChatCvSubmit() {
    const btn = document.getElementById('chatCv_submitBtn');
    if (_chatCvAudioMode === 'record') {
        btn.disabled = !_chatCvRecordedBlob;
    } else {
        const inp = document.getElementById('chatCv_audio');
        btn.disabled = !(inp.files && inp.files.length > 0);
    }
}

function submitChatCustomVoice(e) {
    e.preventDefault();
    const errEl = document.getElementById('chatCv_error');
    errEl.classList.add('hidden');

    const name = document.getElementById('chatCv_name').value.trim();
    const provider = document.getElementById('chatCv_provider').value;
    const context = document.getElementById('chatCv_context').value;
    const audioInput = document.getElementById('chatCv_audio');
    const isRecord = _chatCvAudioMode === 'record';

    if (!name) { errEl.textContent = 'Name is required'; errEl.classList.remove('hidden'); return false; }
    if (isRecord && !_chatCvRecordedBlob) { errEl.textContent = 'Please record a voice sample'; errEl.classList.remove('hidden'); return false; }
    if (!isRecord && (!audioInput.files || audioInput.files.length === 0)) { errEl.textContent = 'Audio file required'; errEl.classList.remove('hidden'); return false; }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('provider', provider);
    fd.append('system_prompt', '');
    fd.append('personality', document.getElementById('chatCv_personality').value.trim());
    fd.append('gender', document.getElementById('chatCv_gender').value || 'female');

    if (isRecord && _chatCvRecordedBlob) {
        const ext = (_chatCvRecordedBlob.type || '').includes('webm') ? 'webm' : 'ogg';
        fd.append('audio_0', _chatCvRecordedBlob, 'recording.' + ext);
    } else {
        for (let i = 0; i < audioInput.files.length; i++) fd.append('audio_' + i, audioInput.files[i]);
    }

    document.getElementById('chatCv_submitBtn').disabled = true;
    document.getElementById('chatCv_form').classList.add('hidden');
    document.getElementById('chatCv_processing').classList.remove('hidden');

    fetch('/api/custom-voices', { method: 'POST', body: fd })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            if (!ok) {
                document.getElementById('chatCv_form').classList.remove('hidden');
                document.getElementById('chatCv_processing').classList.add('hidden');
                document.getElementById('chatCv_submitBtn').disabled = false;
                errEl.textContent = data.error || 'Failed';
                errEl.classList.remove('hidden');
                return;
            }
            _pollChatCv(data.id, context, provider);
        })
        .catch(err => {
            document.getElementById('chatCv_form').classList.remove('hidden');
            document.getElementById('chatCv_processing').classList.add('hidden');
            document.getElementById('chatCv_submitBtn').disabled = false;
            errEl.textContent = 'Network error: ' + err.message;
            errEl.classList.remove('hidden');
        });
    return false;
}

function _pollChatCv(voiceId, context, provider) {
    const interval = setInterval(async () => {
        try {
            const r = await fetch('/api/custom-voices/' + voiceId + '/status');
            const data = await r.json();
            if (data.status === 'ready') {
                clearInterval(interval);
                closeChatCustomVoiceModal();
                // Reload voice list and select the new voice
                const voiceSelect = document.getElementById(context === 'emptyState' ? 'emptyStateVoiceModel' : 'voiceModel');
                if (context === 'emptyState') {
                    await loadEmptyStateVoiceModels(provider);
                } else {
                    await loadVoiceModels(provider);
                }
                if (voiceSelect) {
                    const newId = 'custom_' + voiceId;
                    const opt = Array.from(voiceSelect.options).find(o => o.value === newId);
                    if (opt) voiceSelect.value = newId;
                }
                updateChatVoiceButtons(context);
            } else if (data.status === 'failed') {
                clearInterval(interval);
                document.getElementById('chatCv_processing').classList.add('hidden');
                document.getElementById('chatCv_form').classList.remove('hidden');
                const errEl = document.getElementById('chatCv_error');
                errEl.textContent = data.error_message || 'Voice cloning failed';
                errEl.classList.remove('hidden');
            }
        } catch (e) { /* keep polling */ }
    }, 1500);
}

// ── Delete / Edit Custom Voice ──

async function deleteChatCustomVoice(context) {
    const voiceEl = document.getElementById(context === 'emptyState' ? 'emptyStateVoiceModel' : 'voiceModel');
    const providerEl = document.getElementById(context === 'emptyState' ? 'emptyStateVoiceProvider' : 'voiceProvider');
    if (!voiceEl || !providerEl) return;
    const voiceId = voiceEl.value;
    if (!voiceId || !voiceId.startsWith('custom_')) return;
    const dbId = voiceId.split('_')[1];
    const voiceName = voiceEl.options[voiceEl.selectedIndex]?.text || voiceId;
    if (!confirm('Delete custom voice "' + voiceName + '"?')) return;

    try {
        const r = await fetch('/api/custom-voices/' + dbId, { method: 'DELETE' });
        if (r.ok) {
            const provider = providerEl.value;
            if (context === 'emptyState') await loadEmptyStateVoiceModels(provider);
            else await loadVoiceModels(provider);
            updateChatVoiceButtons(context);
        }
    } catch (e) { console.error('Delete failed:', e); }
}

async function editChatCustomVoice(context) {
    const voiceEl = document.getElementById(context === 'emptyState' ? 'emptyStateVoiceModel' : 'voiceModel');
    if (!voiceEl) return;
    const voiceId = voiceEl.value;
    if (!voiceId || !voiceId.startsWith('custom_')) return;
    const dbId = voiceId.split('_')[1];
    const voiceName = voiceEl.options[voiceEl.selectedIndex]?.text || voiceId;

    // Fetch current personality
    try {
        const r = await fetch('/api/custom-voices?provider=' + (document.getElementById(context === 'emptyState' ? 'emptyStateVoiceProvider' : 'voiceProvider')?.value || ''));
        const voices = await r.json();
        const cv = (Array.isArray(voices) ? voices : []).find(v => String(v.id) === dbId);
        document.getElementById('chatEditVoicePersonality').value = cv?.personality || '';
    } catch (e) { /* ignore */ }

    document.getElementById('chatEditVoiceId').value = dbId;
    document.getElementById('chatEditVoiceName').textContent = voiceName;
    document.getElementById('chatEditVoiceError').classList.add('hidden');
    const modal = document.getElementById('chatEditVoiceModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closeChatEditVoiceModal() {
    const modal = document.getElementById('chatEditVoiceModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

async function submitChatEditVoice(e) {
    e.preventDefault();
    const voiceId = document.getElementById('chatEditVoiceId').value;
    const personality = document.getElementById('chatEditVoicePersonality').value.trim();
    const errEl = document.getElementById('chatEditVoiceError');
    errEl.classList.add('hidden');
    try {
        const r = await fetch('/api/custom-voices/' + voiceId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ personality })
        });
        if (!r.ok) throw new Error((await r.json()).error || 'Failed');
        closeChatEditVoiceModal();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
    }
    return false;
}

// ── Recording Logic (mirrors general.js) ──

function _resetChatCvRecUI() {
    document.getElementById('chatCv_recReady').classList.remove('hidden');
    document.getElementById('chatCv_recActive').classList.add('hidden');
    document.getElementById('chatCv_recDone').classList.add('hidden');
}

function _cleanupChatCvRecording() {
    if (_chatCvMediaRecorder && _chatCvMediaRecorder.state !== 'inactive') _chatCvMediaRecorder.stop();
    if (_chatCvRecordStream) { _chatCvRecordStream.getTracks().forEach(t => t.stop()); _chatCvRecordStream = null; }
    if (_chatCvRecordTimer) { clearInterval(_chatCvRecordTimer); _chatCvRecordTimer = null; }
    if (_chatCvRecordLevelRAF) { cancelAnimationFrame(_chatCvRecordLevelRAF); _chatCvRecordLevelRAF = null; }
    if (_chatCvPlaybackAudio) { _chatCvPlaybackAudio.pause(); _chatCvPlaybackAudio = null; }
    _chatCvMediaRecorder = null;
    _chatCvRecordChunks = [];
    _chatCvRecordAnalyser = null;
}

async function startChatCvRecording() {
    try {
        _cleanupChatCvRecording();
        _chatCvRecordedBlob = null;
        _chatCvRecordChunks = [];
        _chatCvRecordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const ctx = new AudioContext();
        const src = ctx.createMediaStreamSource(_chatCvRecordStream);
        _chatCvRecordAnalyser = ctx.createAnalyser();
        _chatCvRecordAnalyser.fftSize = 256;
        src.connect(_chatCvRecordAnalyser);

        const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
        _chatCvMediaRecorder = mime ? new MediaRecorder(_chatCvRecordStream, { mimeType: mime }) : new MediaRecorder(_chatCvRecordStream);
        _chatCvMediaRecorder.ondataavailable = e => { if (e.data.size > 0) _chatCvRecordChunks.push(e.data); };
        _chatCvMediaRecorder.onstop = () => {
            _chatCvRecordedBlob = new Blob(_chatCvRecordChunks, { type: _chatCvMediaRecorder.mimeType || 'audio/webm' });
            if (_chatCvRecordStream) { _chatCvRecordStream.getTracks().forEach(t => t.stop()); _chatCvRecordStream = null; }
            document.getElementById('chatCv_recActive').classList.add('hidden');
            document.getElementById('chatCv_recDone').classList.remove('hidden');
            document.getElementById('chatCv_recDuration').textContent = ((Date.now() - _chatCvRecordStartTime) / 1000).toFixed(1) + 's';
            _updateChatCvSubmit();
        };
        _chatCvMediaRecorder.start(250);
        _chatCvRecordStartTime = Date.now();
        document.getElementById('chatCv_recReady').classList.add('hidden');
        document.getElementById('chatCv_recActive').classList.remove('hidden');
        document.getElementById('chatCv_recDone').classList.add('hidden');
        _chatCvRecordTimer = setInterval(() => {
            const s = Math.floor((Date.now() - _chatCvRecordStartTime) / 1000);
            document.getElementById('chatCv_recTimer').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
            if ((Date.now() - _chatCvRecordStartTime) / 1000 >= _CHAT_CV_MAX_SECS) stopChatCvRecording();
        }, 250);
        _animateChatCvLevel();
    } catch (err) {
        const errEl = document.getElementById('chatCv_error');
        errEl.textContent = 'Microphone access denied: ' + err.message;
        errEl.classList.remove('hidden');
    }
}

function _animateChatCvLevel() {
    if (!_chatCvRecordAnalyser) return;
    const data = new Uint8Array(_chatCvRecordAnalyser.frequencyBinCount);
    _chatCvRecordAnalyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / data.length;
    const el = document.getElementById('chatCv_recLevel');
    if (el) el.style.width = Math.min(100, (avg / 128) * 100) + '%';
    _chatCvRecordLevelRAF = requestAnimationFrame(_animateChatCvLevel);
}

function stopChatCvRecording() {
    if (_chatCvRecordTimer) { clearInterval(_chatCvRecordTimer); _chatCvRecordTimer = null; }
    if (_chatCvRecordLevelRAF) { cancelAnimationFrame(_chatCvRecordLevelRAF); _chatCvRecordLevelRAF = null; }
    if (_chatCvMediaRecorder && _chatCvMediaRecorder.state !== 'inactive') _chatCvMediaRecorder.stop();
}

function playChatCvRecording() {
    if (!_chatCvRecordedBlob) return;
    if (_chatCvPlaybackAudio) { _chatCvPlaybackAudio.pause(); _chatCvPlaybackAudio = null; document.getElementById('chatCv_recPlayBtn').textContent = '▶ Play'; return; }
    _chatCvPlaybackAudio = new Audio(URL.createObjectURL(_chatCvRecordedBlob));
    document.getElementById('chatCv_recPlayBtn').textContent = '⏹ Stop';
    _chatCvPlaybackAudio.onended = () => { _chatCvPlaybackAudio = null; document.getElementById('chatCv_recPlayBtn').textContent = '▶ Play'; };
    _chatCvPlaybackAudio.play();
}

function retakeChatCvRecording() {
    _cleanupChatCvRecording();
    _chatCvRecordedBlob = null;
    _resetChatCvRecUI();
    _updateChatCvSubmit();
}
