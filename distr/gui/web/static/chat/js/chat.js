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
let chatWsHeartbeatTimer = null;
let chatWsLastPongAt = 0;
const CHAT_WS_HEARTBEAT_MS = 15000;
const CHAT_WS_STALE_MS = 45000;
const _agentStreamWaiters = new Map();

function _setAgentStreamWaiter(chatId, token, resolve) {
    _agentStreamWaiters.set(Number(chatId), { token, resolve });
}

function _clearAgentStreamWaiter(chatId, resolve) {
    const key = Number(chatId);
    const current = _agentStreamWaiters.get(key);
    if (current && (!resolve || current.resolve === resolve)) _agentStreamWaiters.delete(key);
}

function _resolveAgentStreamWaiter(chatId) {
    const key = Number(chatId);
    const current = _agentStreamWaiters.get(key);
    if (!current || current.token !== _streamToken) return false;
    _agentStreamWaiters.delete(key);
    current.resolve();
    return true;
}
let queuedTranscriptionStatusUpdate = null;
let transcriptionStatusFlushScheduled = false;
/** When non-null, we are showing a streamed assistant message for this chat (PTT/voice). */
let streamingChatId = null;
/** Plain text of the last reply finalized in stream_finished — suppress duplicate assistant message_added / poll rows. */
let _lastStreamFinalizedPlain = null;
/** True when this chat was loaded with skipLoadInAgent (new chat from modal). POST /chats already hot-swapped the agent; first send must NOT call load-in-agent (would interrupt_tts and kill in-flight first reply). */
let loadedChatSkippedLoadInAgent = false;
/** Monotonically increasing token; each poll/stream registers its token and checks it's still current before resolving. Prevents stale polls from old chats firing on new chats. */
let _streamToken = 0;
/** Sequence counter for selectChat — only the latest call renders its result. */
let _selectSeq = 0;
/** Guard against double-clicking "+" creating two chats simultaneously. */
let _createChatGuard = false;

/** Persist active chat for Kanban ticket creation / lane-move notices (cross-tab). */
const KANBAN_SOURCE_CHAT_STORAGE_KEY = 'decisions_source_chat_id';
const LIVE_CHAT_STATE_STORAGE_KEY = 'decisions_live_chat_state_v1';
const LIVE_CHAT_STATE_MAX_AGE_MS = 10 * 60 * 1000;

function syncKanbanSourceChatContext() {
    try {
        const id = loadedChatId != null ? loadedChatId : currentChatId;
        if (id != null && Number(id) >= 1) {
            sessionStorage.setItem(KANBAN_SOURCE_CHAT_STORAGE_KEY, String(Number(id)));
        } else {
            sessionStorage.removeItem(KANBAN_SOURCE_CHAT_STORAGE_KEY);
        }
    } catch (e) { /* ignore private mode / quota */ }
}

window.DecisionsWebChat = window.DecisionsWebChat || {};
window.DecisionsWebChat.getSourceChatIdForTickets = function () {
    try {
        const id = loadedChatId != null ? loadedChatId : currentChatId;
        if (id != null && Number(id) >= 1) return Number(id);
        const raw = sessionStorage.getItem(KANBAN_SOURCE_CHAT_STORAGE_KEY);
        if (raw) {
            const n = parseInt(raw, 10);
            if (!isNaN(n) && n >= 1) return n;
        }
    } catch (e) { /* ignore */ }
    return null;
};

function _loadLiveChatStateMap() {
    try {
        const raw = sessionStorage.getItem(LIVE_CHAT_STATE_STORAGE_KEY);
        if (!raw) return {};
        const data = JSON.parse(raw);
        return data && typeof data === 'object' ? data : {};
    } catch (e) {
        return {};
    }
}

function _saveLiveChatStateMap(map) {
    try {
        sessionStorage.setItem(LIVE_CHAT_STATE_STORAGE_KEY, JSON.stringify(map || {}));
    } catch (e) { /* ignore private mode / quota */ }
}

function _getLiveChatState(chatId) {
    if (chatId == null) return null;
    const key = String(chatId);
    const state = _loadLiveChatStateMap()[key];
    if (!state || typeof state !== 'object') return null;
    const updatedAt = Number(state.updatedAt || 0);
    if (!updatedAt || (Date.now() - updatedAt) > LIVE_CHAT_STATE_MAX_AGE_MS) {
        _clearLiveChatState(chatId);
        return null;
    }
    return state;
}

function _updateLiveChatState(chatId, patch) {
    if (chatId == null) return;
    const key = String(chatId);
    const map = _loadLiveChatStateMap();
    const previous = map[key];
    map[key] = {
        ...(previous && typeof previous === 'object' ? previous : {}),
        ...(patch && typeof patch === 'object' ? patch : {}),
        updatedAt: Date.now()
    };
    _saveLiveChatStateMap(map);
}

function _clearLiveChatState(chatId) {
    if (chatId == null) return;
    const key = String(chatId);
    const map = _loadLiveChatStateMap();
    if (!(key in map)) return;
    delete map[key];
    _saveLiveChatStateMap(map);
}

function _setLivePreviewState(text) {
    if (currentChatId == null) return;
    const plain = _normalizeMsgPlain(text);
    if (!plain) return;
    _updateLiveChatState(currentChatId, {
        pendingUserText: plain,
        pendingUserPhase: 'preview'
    });
}

function _setCommittedLiveUserState(text) {
    if (currentChatId == null) return;
    const plain = _normalizeMsgPlain(text);
    if (!plain) return;
    _updateLiveChatState(currentChatId, {
        pendingUserText: plain,
        pendingUserPhase: 'committed'
    });
}

function _acknowledgePersistedLiveUser(chatId, text) {
    const plain = _normalizeMsgPlain(text);
    const state = _getLiveChatState(chatId);
    if (!state || !plain || _normalizeMsgPlain(state.pendingUserText || '') !== plain) return;
    if (state.assistantStreaming) {
        _updateLiveChatState(chatId, {
            pendingUserText: '',
            pendingUserPhase: null
        });
        return;
    }
    _clearLiveChatState(chatId);
}

function _setLiveAssistantStreaming(chatId, text = '') {
    if (chatId == null) return;
    _updateLiveChatState(chatId, {
        assistantStreaming: true,
        assistantText: String(text || '')
    });
}

function _appendLiveAssistantStreamText(chatId, text) {
    if (chatId == null || !text) return;
    const state = _getLiveChatState(chatId) || {};
    _updateLiveChatState(chatId, {
        assistantStreaming: true,
        assistantText: String(state.assistantText || '') + String(text)
    });
}

function _clearLiveAssistantStreaming(chatId) {
    const state = _getLiveChatState(chatId);
    if (!state) return;
    const pendingUserText = _normalizeMsgPlain(state.pendingUserText || '');
    if (pendingUserText) {
        _updateLiveChatState(chatId, {
            assistantStreaming: false,
            assistantText: ''
        });
        return;
    }
    _clearLiveChatState(chatId);
}

function _messagesEndWithAssistant(messages) {
    if (!Array.isArray(messages) || !messages.length) return false;
    for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i] || {};
        if (message.role === 'tool') continue;
        return message.role === 'assistant' && Boolean(_normalizeMsgPlain(message.content || ''));
    }
    return false;
}

function _hasActiveAssistantStreamForCurrentChat() {
    if (currentChatId == null) return false;
    if (isStreaming && streamingChatId === currentChatId) return true;
    const state = _getLiveChatState(currentChatId);
    return Boolean(state && state.assistantStreaming);
}

function _hasLiveTranscriptionStateForCurrentChat() {
    if (currentChatId == null) return false;
    const state = _getLiveChatState(currentChatId);
    return Boolean(
        state
        && state.pendingUserPhase === 'preview'
        && _normalizeMsgPlain(state.pendingUserText || '')
    );
}

function _pendingLiveTranscriptionPlainForCurrentChat() {
    if (currentChatId == null) return '';
    const state = _getLiveChatState(currentChatId);
    if (!state || state.pendingUserPhase !== 'preview') return '';
    return _normalizeMsgPlain(state.pendingUserText || '');
}

function _messagesContainUserPlain(messages, plain) {
    if (!plain || !Array.isArray(messages)) return false;
    return messages.some(msg => (
        msg
        && msg.role === 'user'
        && _normalizeMsgPlain(msg.content || '') === plain
    ));
}

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
const inputLoadButton = document.getElementById('inputLoadButton');
let ttsEnabled = true;
let transcriptionStatusTimer = null;
/** Updates the clock in the live STT preview row every second (same styling as message timestamps). */
let _sttPreviewClockInterval = null;
const newChatBtn = document.getElementById('newChatBtn');
const chatSettingsHeader = document.getElementById('chatSettingsHeader');

// Disable input until empty state is shown or a chat is selected/loaded
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
const configureChatButton = document.getElementById('configureChatButton');
const compactChatButton = document.getElementById('compactChatButton');
const benchmarkInfoButton = document.getElementById('benchmarkInfoButton');
const forkChatButton = document.getElementById('forkChatButton');
const chatConfigModal = document.getElementById('chatConfigModal');
const chatConfigClose = document.getElementById('chatConfigClose');
const chatConfigCancel = document.getElementById('chatConfigCancel');
const chatConfigSave = document.getElementById('chatConfigSave');
const chatConfigLlmProvider = document.getElementById('chatConfigLlmProvider');
const chatConfigLlmModel = document.getElementById('chatConfigLlmModel');
const chatConfigVoiceProvider = document.getElementById('chatConfigVoiceProvider');
const chatConfigVoiceModel = document.getElementById('chatConfigVoiceModel');
const chatConfigStatus = document.getElementById('chatConfigStatus');
const headerLlmSummary = document.getElementById('headerLlmSummary');
const headerVoiceSummary = document.getElementById('headerVoiceSummary');
const voiceUsageMeter = document.getElementById('voiceUsageMeter');
const voiceUsageFill = document.getElementById('voiceUsageFill');
const voiceUsageText = document.getElementById('voiceUsageText');
const contextRing = document.getElementById('contextRing');

let _autoCompactInFlight = false;
let _lastAutoCompactKey = null;
let _headerStatsTimer = null;
let _headerUsageCacheKey = null;
let _headerUsageFetchedAt = 0;
let _llmUsageTooltip = '';

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

function invalidateTTSProvidersCache() {
    _ttsProviders = null;
}

/** Voice provider options from GET /api/tts/providers — same registry as Settings → General. */
function populateChatVoiceProviderSelect(selectEl) {
    if (!selectEl) return;
    if (!_ttsProviders || !_ttsProviders.length) {
        selectEl.innerHTML = '<option value="">No voice providers available</option>';
        return;
    }
    selectEl.innerHTML = _ttsProviders.map(p =>
        `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`
    ).join('');
}

/** Voice rows from cached provider payload (avoids extra /api/voices fetch when registry has voices). */
function chatVoiceOptionHtml(voice) {
    const id = voice && typeof voice === 'object' ? (voice.id || '') : voice;
    const name = voice && typeof voice === 'object' ? (voice.name || voice.id || '') : voice;
    const attrs = [`value="${escapeHtml(id || '')}"`];
    if (voice && typeof voice === 'object') {
        if (voice.custom) attrs.push('data-custom="1"');
        if (voice.custom_voice_id) attrs.push(`data-custom-voice-id="${escapeHtml(String(voice.custom_voice_id))}"`);
        if (voice.provider_voice_id) attrs.push(`data-provider-voice-id="${escapeHtml(voice.provider_voice_id)}"`);
        if (voice.custom_source) attrs.push(`data-custom-source="${escapeHtml(voice.custom_source)}"`);
    }
    return `<option ${attrs.join(' ')}>${escapeHtml(name || id || '')}</option>`;
}

function populateChatVoiceModelSelectForEl(modelSelectEl, providerId) {
    if (!modelSelectEl) return;
    const provider = _ttsProviders && _ttsProviders.find(p => p.id === providerId);
    const voices = provider && Array.isArray(provider.voices) ? provider.voices : [];
    if (voices.length) {
        modelSelectEl.innerHTML = voices.map(chatVoiceOptionHtml).join('');
    } else {
        modelSelectEl.innerHTML = '<option value="">No voices available</option>';
    }
}

/** Saved voice_provider only if present in verified registry list; else first available. */
function resolvedVoiceProviderForChat(generalData) {
    const preferred = canonicalVoiceProviderForSelect(generalData);
    if (_ttsProviders && _ttsProviders.length) {
        if (_ttsProviders.some(p => p.id === preferred)) return preferred;
        return _ttsProviders[0].id;
    }
    return preferred;
}

function getVoiceSettingsKey(voiceProvider) {
    if (_ttsProviders) {
        const provider = _ttsProviders.find(p => p.id === voiceProvider);
        if (provider && provider.settings_key) return provider.settings_key;
    }
    return voiceProvider + '_voice';  // fallback
}

/** Sync chat configure TTS choice to global Settings (General) so GET /api/general restores it. */
async function persistGlobalVoiceToSettings(voiceProvider, voiceModel) {
    const vp = (voiceProvider || '').trim();
    const vm = (voiceModel || '').trim();
    if (!vp || !vm) return;
    try {
        const r = await fetch('/api/general/voice-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice_provider: vp, voice_model: vm }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            console.warn('persistGlobalVoiceToSettings failed:', r.status, err);
        }
    } catch (e) {
        console.warn('persistGlobalVoiceToSettings failed:', e);
    }
}

/** Align saved Settings voice fields with modal <option value="id"> (handles legacy display strings). */
function canonicalVoiceProviderForSelect(generalData) {
    const raw = generalData && (generalData.tts_provider || generalData.voice_provider);
    if (!raw || raw === '—') return 'kokoro';
    const s = String(raw).toLowerCase().trim();
    if (_ttsProviders && _ttsProviders.length) {
        const byId = _ttsProviders.find(p => (p.id || '').toLowerCase() === s);
        if (byId && byId.id) return byId.id;
        const bySub = _ttsProviders.find(p => s.includes((p.id || '').toLowerCase()));
        if (bySub && bySub.id) return bySub.id;
    }
    return s;
}

// Listen for provider changes from the settings page (cross-tab via BroadcastChannel)
try {
    const _providersBc = new BroadcastChannel('providers-changed');
    _providersBc.onmessage = function(ev) {
        if (ev.data && ev.data.type === 'thirdparty-providers-changed') {
            console.log('Third-party providers changed (cross-tab) — refreshing configure forms');
            invalidateTTSProvidersCache();
            if (typeof loadDefaultSettings === 'function') loadDefaultSettings();
            if (typeof loadEmptyStateDropdowns === 'function') loadEmptyStateDropdowns();
        }
    };
} catch (_) { /* BroadcastChannel not supported — no-op */ }

/**
 * Resolve LLM + voice from Settings (same sources as createDefaultChat).
 * Fills first available model/voice when settings omit them so first-send works without the modal.
 */
async function fetchDefaultsForNewChat() {
    let provider = 'ollama';
    let model_name = null;
    let voice_provider = 'kokoro';
    let voice_model = null;
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
            if (m && m !== '—') model_name = m;
        }
        if (generalRes.ok) {
            const general = await generalRes.json();
            voice_provider = resolvedVoiceProviderForChat(general);
            const key = getVoiceSettingsKey(voice_provider);
            const v = (general[key] || '').trim();
            if (v && v !== '—') voice_model = v;
        }
    } catch (e) {
        console.warn('fetchDefaultsForNewChat settings fetch failed:', e);
    }
    if (!model_name && provider) {
        try {
            const providerId = providerIdFromDisplay(provider);
            const response = await fetch(`/api/llms/models?type=conversational&provider=${encodeURIComponent(providerId)}`);
            const data = await response.json();
            const models = data.models || [];
            if (models.length) {
                const first = models[0];
                model_name = String(first.id || first || '').trim();
            }
        } catch (_) {}
    }
    if (!voice_model && voice_provider) {
        try {
            const response = await fetch(`/api/voices/${encodeURIComponent(voice_provider)}`);
            const data = await response.json();
            const voices = Array.isArray(data) ? data : (data.voices || data.chats || []);
            if (voices.length) {
                const first = voices[0];
                voice_model = String(first.id || first || '').trim();
            }
        } catch (_) {}
    }
    return { provider, model_name, voice_provider, voice_model };
}

// Create one chat with default config when there are no chats. Uses Settings API (LLM + voice from Settings).
// We do not pass starting_question so the backend adds the welcome message "How can I help you today?"
async function createDefaultChat() {
    const d = await fetchDefaultsForNewChat();
    const body = {
        title: 'New Chat',
        provider: d.provider,
        voice_provider: d.voice_provider,
        voice_model: d.voice_model
    };
    if (d.model_name) body.model_name = d.model_name;
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
            const fromCreate = params.get('from_create') === '1';
            if (!isNaN(chatId)) {
                if (fromCreate) await loadChat(chatId, { skipLoadInAgent: true });
                else await selectChat(chatId);
            }
        } else if (data && data.chats && data.chats.length === 0) {
            // Show empty state so user can select provider/model/voice before first chat
            showEmptyState();
        } else if (data && data.last_chat_id != null && data.chats && data.chats.length) {
            const lastId = Number(data.last_chat_id);
            if (!isNaN(lastId) && data.chats.some(c => c.id === lastId)) {
                await selectChat(lastId);
            }
        }
        syncKanbanSourceChatContext();
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
    if (voiceProviderSelect) {
        voiceProviderSelect.addEventListener('change', async () => {
            await loadVoiceModels(voiceProviderSelect.value);
            await persistGlobalVoiceToSettings(voiceProviderSelect.value, voiceModelSelect ? voiceModelSelect.value : '');
        });
    }
    if (voiceModelSelect) {
        voiceModelSelect.addEventListener('change', () => {
            persistGlobalVoiceToSettings(voiceProviderSelect ? voiceProviderSelect.value : '', voiceModelSelect.value);
        });
    }
    if (configureChatButton) configureChatButton.addEventListener('click', () => openChatConfigModal());
    if (compactChatButton) compactChatButton.addEventListener('click', () => compactCurrentChat());
    if (benchmarkInfoButton) benchmarkInfoButton.addEventListener('click', () => openChatBenchmarkInfoModal());
    if (forkChatButton) forkChatButton.addEventListener('click', () => forkCurrentChat());
    if (chatMessages) {
        chatMessages.addEventListener('contextmenu', (e) => {
            if (!currentChatId) return;
            if (e.target && e.target.closest && e.target.closest('.message')) {
                e.preventDefault();
                showChatContextMenu(e, currentChatId);
            }
        });
        chatMessages.addEventListener('click', (e) => {
            const btn = e.target && e.target.closest ? e.target.closest('[data-mermaid-open]') : null;
            if (!btn) return;
            const block = btn.closest('[data-mermaid-code]');
            if (!block || !window.DecisionsMermaid) return;
            const raw = block.getAttribute('data-mermaid-code') || '';
            let code = '';
            try { code = decodeURIComponent(raw); } catch (_) { code = raw; }
            const labelEl = block.querySelector('.mermaid-inline-header span');
            const title = labelEl ? labelEl.textContent : 'Diagram';
            window.DecisionsMermaid.open({ code, title });
        });
        bindActivityToggleHandlers();
    }
    if (chatConfigClose) chatConfigClose.addEventListener('click', hideChatConfigModal);
    if (chatConfigCancel) chatConfigCancel.addEventListener('click', hideChatConfigModal);
    if (chatConfigSave) chatConfigSave.addEventListener('click', saveChatConfig);
    if (chatConfigLlmProvider) {
        chatConfigLlmProvider.addEventListener('change', () => {
            loadLlmModelsInto(chatConfigLlmModel, chatConfigLlmProvider.value);
        });
    }
    if (chatConfigVoiceProvider) {
        chatConfigVoiceProvider.addEventListener('change', async () => {
            await reloadChatVoiceModels('chatConfig', chatConfigVoiceProvider.value);
            updateChatVoiceButtons('chatConfig');
        });
    }

    const emptyStateLlmProvider = document.getElementById('emptyStateLlmProvider');
    const emptyStateVoiceProvider = document.getElementById('emptyStateVoiceProvider');
    const emptyStateVoiceModel = document.getElementById('emptyStateVoiceModel');
    if (emptyStateLlmProvider) emptyStateLlmProvider.addEventListener('change', () => {
        loadEmptyStateLlmModels(emptyStateLlmProvider.value);
        toggleEmptyStateOllamaPullBtn();
    });
    if (emptyStateVoiceProvider) {
        emptyStateVoiceProvider.addEventListener('change', async () => {
            await loadEmptyStateVoiceModels(emptyStateVoiceProvider.value);
            const vmEl = document.getElementById('emptyStateVoiceModel');
            await persistGlobalVoiceToSettings(emptyStateVoiceProvider.value, vmEl ? vmEl.value : '');
        });
    }
    if (emptyStateVoiceModel) {
        emptyStateVoiceModel.addEventListener('change', () => {
            const vpEl = document.getElementById('emptyStateVoiceProvider');
            persistGlobalVoiceToSettings(vpEl ? vpEl.value : '', emptyStateVoiceModel.value);
        });
    }

    const modalPlayVoiceBtn = document.getElementById('modalPlayVoiceBtn');
    if (modalPlayVoiceBtn) modalPlayVoiceBtn.addEventListener('click', () => playVoiceForContext('modal'));
    const emptyStatePlayVoiceBtn = document.getElementById('emptyStatePlayVoiceBtn');
    if (emptyStatePlayVoiceBtn) emptyStatePlayVoiceBtn.addEventListener('click', () => playVoiceForContext('emptyState'));
    const chatConfigPlayVoiceBtn = document.getElementById('chatConfigPlayVoiceBtn');
    if (chatConfigPlayVoiceBtn) chatConfigPlayVoiceBtn.addEventListener('click', () => playVoiceForContext('chatConfig'));

    // Modal should only close via Cancel, Close button, or Escape — NOT on overlay click
    // (removed: newChatModal background click handler)

    // Single view → load action, placed beside the composer.
    if (inputLoadButton) inputLoadButton.addEventListener('click', () => { if (currentChatId) loadChat(currentChatId); });

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
                if (action === 'configure') {
                    setTimeout(async () => {
                        if (currentChatId !== id) await selectChat(id);
                        openChatConfigModal(id);
                    }, 0);
                }
                if (action === 'compact') {
                    setTimeout(async () => {
                        if (currentChatId !== id) await selectChat(id);
                        compactCurrentChat(id);
                    }, 0);
                }
                if (action === 'fork') {
                    setTimeout(async () => {
                        if (currentChatId !== id) await selectChat(id);
                        forkCurrentChat(id);
                    }, 0);
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
    const canReply = currentChatId == null || loadedChatId === currentChatId;
    sendButton.disabled = !canReply || !messageInput.value.trim() || isStreaming;
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
    if (chatWsHeartbeatTimer) { clearInterval(chatWsHeartbeatTimer); chatWsHeartbeatTimer = null; }
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
                if (msg.type === 'pong') {
                    chatWsLastPongAt = Date.now();
                    return;
                }
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
                if (msg.event === 'tool_executed') {
                    handleChatEventToolExecuted(msg);
                    return;
                }
                if (msg.event === 'workflow_event') {
                    handleChatEventWorkflow(msg);
                    return;
                }
                if (msg.event === 'transcription_progress') {
                    if (msg.chat_id === currentChatId) {
                        queueTranscriptionStatusUpdate(
                            msg.status != null ? msg.status : '',
                            Boolean(msg.done),
                            Boolean(msg.clear_live_preview),
                            Boolean(msg.discard_live_preview)
                        );
                    }
                    return;
                }
                if (msg.type === 'chat_updated' && msg.chat_id === currentChatId) {
                    if (streamingChatId === currentChatId && _agentStreamWaiters.has(Number(currentChatId))) {
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
                                _lastStreamFinalizedPlain = _normalizeMsgPlain(content);
                            }
                            streamingChatId = null;
                            removeTypingIndicator();
                            _resolveAgentStreamWaiter(currentChatId);
                            updateChatSettingsDisplay({
                                title: data.title || 'New Chat',
                                provider: data.provider || '-',
                                model_name: data.model_name || '-',
                                voice_provider: data.voice_provider,
                                voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
                            });
                            scrollToBottom();
                            syncRenderedMessageCountFromDom();
                            _suppressChatUpdatedFetchUntil = Date.now() + 2500;
                        }).catch(() => {});
                        return;
                    }
                    if (streamingChatId === currentChatId) {
                        fetch(`${API_BASE}/chats/${currentChatId}`).then(r => r.json()).then(data => {
                            mergeChatUpdatedDuringStream(data.messages || []);
                            updateChatSettingsDisplay({
                                title: data.title || 'New Chat',
                                provider: data.provider || '-',
                                model_name: data.model_name || '-',
                                voice_provider: data.voice_provider,
                                voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
                            });
                            scrollToBottom();
                        }).catch(() => {});
                        return;
                    }
                    if (Date.now() < _suppressChatUpdatedFetchUntil) {
                        return;
                    }
                    if (isStreaming && streamingChatId !== currentChatId) {
                        fetch(`${API_BASE}/chats/${currentChatId}`).then(r => r.json()).then(data => {
                            mergeChatUpdatedDuringStream(data.messages || []);
                            updateChatSettingsDisplay({
                                title: data.title || 'New Chat',
                                provider: data.provider || '-',
                                model_name: data.model_name || '-',
                                voice_provider: data.voice_provider,
                                voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
                            });
                            scrollToBottom();
                        }).catch(() => {});
                        return;
                    }
                    fetch(`${API_BASE}/chats/${currentChatId}`).then(r => r.json()).then(data => {
                        renderMessages(data.messages || [], true);
                        updateChatSettingsDisplay({
                            title: data.title || 'New Chat',
                            provider: data.provider || '-',
                            model_name: data.model_name || '-',
                            voice_provider: data.voice_provider,
                            voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
                        });
                        scrollToBottom();
                    }).catch(() => {});
                }
            } catch (e) {}
        };
        chatWs.onclose = () => {
            if (chatWsHeartbeatTimer) { clearInterval(chatWsHeartbeatTimer); chatWsHeartbeatTimer = null; }
            chatWs = null;
            chatWsSubscribedId = null;
            // Auto-reconnect if we still have an active chat, with exponential backoff
            if (currentChatId != null) {
                chatWsReconnectTimer = setTimeout(() => {
                    chatWsReconnectTimer = null;
                    chatWsReconnectDelay = Math.min(chatWsReconnectDelay * 2, 30000);
                    startChatWebSocket(true);
                }, chatWsReconnectDelay);
            }
        };
        chatWs.onopen = () => {
            chatWsReconnectDelay = 1000; // reset backoff on successful connect
            chatWsLastPongAt = Date.now();
            if (chatWsSubscribedId != null && chatWs.readyState === WebSocket.OPEN) {
                chatWs.send(JSON.stringify({ subscribe: chatWsSubscribedId }));
            }
            if (chatWsHeartbeatTimer) clearInterval(chatWsHeartbeatTimer);
            chatWsHeartbeatTimer = setInterval(() => {
                if (!chatWs || chatWs.readyState !== WebSocket.OPEN) return;
                if (Date.now() - chatWsLastPongAt > CHAT_WS_STALE_MS) {
                    console.warn('Chat WebSocket heartbeat stale; reconnecting');
                    chatWs.close();
                    return;
                }
                chatWs.send(JSON.stringify({ type: 'ping', sent_at: Date.now() }));
            }, CHAT_WS_HEARTBEAT_MS);
        };
    } catch (e) {
        console.debug('Chat WebSocket error:', e);
    }
}

/** Track user message content already rendered optimistically by sendMessage()
    so handleChatEventMessageAdded can skip duplicates but render voice/PTT messages. */
let _optimisticUserMessages = new Map();  // first-100-chars -> timestamp for recently rendered user messages
const OPTIMISTIC_USER_MESSAGE_TTL_MS = 15000;

function _addOptimisticUserMessage(key) {
    if (!key) return;
    _optimisticUserMessages.set(String(key), Date.now());
}

function _hasRecentOptimisticUserMessage(key) {
    if (!key) return false;
    const now = Date.now();
    for (const [storedKey, storedAt] of _optimisticUserMessages.entries()) {
        if (!storedAt || now - Number(storedAt) > OPTIMISTIC_USER_MESSAGE_TTL_MS) {
            _optimisticUserMessages.delete(storedKey);
        }
    }
    return _optimisticUserMessages.has(String(key));
}

function _normalizeMsgPlain(s) {
    return (s != null ? String(s) : '').replace(/\s+/g, ' ').trim();
}

/** Drop consecutive assistant messages with identical body (pipeline/WS double-write). */
function dedupeConsecutiveDuplicateAssistants(msgs) {
    if (!msgs || msgs.length < 2) return msgs;
    const out = [msgs[0]];
    for (let i = 1; i < msgs.length; i++) {
        const m = msgs[i];
        const prev = out[out.length - 1];
        if (m.role === 'assistant' && prev.role === 'assistant'
            && _normalizeMsgPlain(m.content) === _normalizeMsgPlain(prev.content)) {
            continue;
        }
        out.push(m);
    }
    return out;
}

function _lastRenderedUserBubblePlain() {
    const users = [...chatMessages.querySelectorAll('.message.user')].filter(
        el => el.id !== 'transcriptionStatus'
    );
    if (!users.length) return '';
    const te = users[users.length - 1].querySelector('.message-text');
    return te ? _normalizeMsgPlain(te.textContent) : '';
}

function hasOpenUserTurnPlain(plain) {
    if (!plain || !chatMessages) return false;
    const nodes = [...chatMessages.querySelectorAll('.message')]
        .filter(el => {
            const id = el.id || '';
            return id !== 'transcriptionStatus'
                && id !== 'streamingAssistantMessage'
                && id !== 'streamingAssistantActivity'
                && id !== 'typingIndicator';
        });
    const last = nodes[nodes.length - 1];
    if (!last || !last.classList.contains('user')) return false;
    const textEl = last.querySelector('.message-text');
    return !!(textEl && _normalizeMsgPlain(textEl.textContent) === plain);
}

function _stopSttPreviewClock() {
    if (_sttPreviewClockInterval) {
        clearInterval(_sttPreviewClockInterval);
        _sttPreviewClockInterval = null;
    }
}

function hasRenderedUserMessagePlain(plain) {
    return hasRenderedMessagePlain('user', plain);
}

/** Live STT strip uses the same slot as timestamps — show the current time, not the word “Listening”. */
function _tickSttPreviewClock() {
    const lab = document.querySelector('#transcriptionStatus .stt-preview-label');
    if (!lab) return;
    const clock = _formatTimestamp(Date.now());
    lab.textContent = clock || '—';
}

function _ensureSttPreviewClockRunning() {
    _tickSttPreviewClock();
    if (!_sttPreviewClockInterval) {
        _sttPreviewClockInterval = setInterval(_tickSttPreviewClock, 1000);
    }
}

/** Keep `#transcriptionStatus` directly above `#streamingAssistantMessage` (appendChild order is wrong once the stream mounts first). */
function _ensureTranscriptionPreviewPlacement() {
    const preview = document.getElementById('transcriptionStatus');
    const streamEl = document.getElementById('streamingAssistantMessage');
    if (!preview || !chatMessages || preview.parentNode !== chatMessages) return;
    if (streamEl && streamEl.parentNode === chatMessages) {
        chatMessages.insertBefore(preview, streamEl);
    }
}

/** Always remove live STT preview + reset composer — must run even when we skip duplicate message_added. */
function _discardLiveTranscriptionUi() {
    _stopSttPreviewClock();
    const liveStt = document.getElementById('transcriptionStatus');
    if (liveStt) liveStt.remove();
    if (transcriptionStatusTimer) {
        clearTimeout(transcriptionStatusTimer);
        transcriptionStatusTimer = null;
    }
    if (messageInput) {
        messageInput.value = '';
        messageInput.placeholder = 'Send message...';
    }
}

function isLoadedChatView() {
    return currentChatId != null && loadedChatId === currentChatId;
}

function canPromoteVoiceTranscriptInChat() {
    if (currentChatId == null) return false;
    return loadedChatId === currentChatId || agentCurrentChatId === currentChatId;
}

function workflowEventDomKey(event) {
    const ev = event || {};
    const runId = ev.run_id;
    const type = String(ev.type || 'event').toLowerCase();
    if (runId != null && runId !== '') {
        return `workflow-run:${runId}:${type}`;
    }
    if (ev.id) return `workflow-id:${ev.id}`;
    return null;
}

function hasRenderedToolEvent(msg) {
    if (!chatMessages || !msg) return false;
    const eventId = msg.id != null ? String(msg.id) : '';
    if (eventId) {
        const existing = chatMessages.querySelector(`[data-tool-event-id="${CSS.escape(eventId)}"]`);
        if (existing) return true;
    }
    if ((msg.tool_name || '') !== 'chat_settings') return false;
    const summary = String(msg.result_summary || msg.title || '').trim();
    if (!summary) return false;
    const labels = chatMessages.querySelectorAll('.activity-step-label');
    for (const label of labels) {
        if ((label.textContent || '').trim() === summary) return true;
    }
    return false;
}

function hasRenderedWorkflowEvent(event) {
    const key = workflowEventDomKey(event);
    if (!key || !chatMessages) return false;
    return !!chatMessages.querySelector(`[data-workflow-event-key="${key}"]`);
}

function isHiddenWorkflowEvent(event) {
    if (!event) return true;
    if (event.chat_suppressed === true) return true;
    if (event.chat_visible === false) return true;
    const type = String(event.type || '').trim().toLowerCase();
    // Scheduled/manual automations dispatch through the orchestrator; Automations hub owns run history.
    return type === 'automation_run';
}

function hasLiveUserTranscriptionPreview() {
    const liveStt = document.getElementById('transcriptionStatus');
    return !!(liveStt && liveStt.classList && liveStt.classList.contains('user'));
}

function promoteTranscriptionPreviewToUserMessage(committedText) {
    if (!chatMessages || !canPromoteVoiceTranscriptInChat()) return false;
    const liveStt = document.getElementById('transcriptionStatus');
    const previewTextEl = liveStt ? liveStt.querySelector('#transcriptionStatusText') : null;
    const plain = _normalizeMsgPlain(committedText != null ? committedText : (previewTextEl ? previewTextEl.textContent : ''));
    if (!plain || hasOpenUserTurnPlain(plain)) {
        _discardLiveTranscriptionUi();
        return false;
    }
    _stopSttPreviewClock();
    if (liveStt) liveStt.remove();
    const div = createMessageElement({ role: 'user', content: plain, timestamp: Date.now() });
    insertMessageElementInOrder(div, { role: 'user', content: plain, timestamp: Date.now() });
    _addOptimisticUserMessage(plain.substring(0, 100));
    _setCommittedLiveUserState(plain);
    syncRenderedMessageCountFromDom();
    scheduleRefreshChatHeaderStats();
    scrollToBottom();
    return true;
}

function ensureStreamingAssistantMessageElement() {
    const existing = document.getElementById('streamingAssistantMessage');
    if (existing) return existing;
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
    insertMessageElementInOrder(div, { role: 'assistant', timestamp: Date.now() });
    flushPendingAssistantTools(div, null);
    return div;
}

function restoreLiveChatState(chatId, messages = []) {
    if (!chatMessages || chatId == null || currentChatId !== chatId) return;
    const state = _getLiveChatState(chatId);
    if (!state) return;

    if (state.assistantStreaming && _messagesEndWithAssistant(messages)) {
        _clearLiveChatState(chatId);
        streamingChatId = null;
        isStreaming = false;
        setSendButtonStreaming(false);
        removeTypingIndicator();
        const staleStream = document.getElementById('streamingAssistantMessage');
        if (staleStream) staleStream.remove();
        const staleActivity = document.getElementById('streamingAssistantActivity');
        if (staleActivity) staleActivity.remove();
        return;
    }

    const pendingUserText = _normalizeMsgPlain(state.pendingUserText || '');
    if (pendingUserText && hasOpenUserTurnPlain(pendingUserText)) {
        if (state.assistantStreaming) {
            _updateLiveChatState(chatId, {
                pendingUserText: '',
                pendingUserPhase: null
            });
        } else {
            _clearLiveChatState(chatId);
            return;
        }
    } else if (pendingUserText) {
        if (state.pendingUserPhase === 'preview' && !document.getElementById('transcriptionStatus')) {
            showTranscriptionStatus(pendingUserText, false, false, false);
        } else if (state.pendingUserPhase === 'committed' && !hasOpenUserTurnPlain(pendingUserText)) {
            const div = createMessageElement({ role: 'user', content: pendingUserText, timestamp: Date.now() });
            insertMessageElementInOrder(div, { role: 'user', content: pendingUserText, timestamp: Date.now() });
            _addOptimisticUserMessage(pendingUserText.substring(0, 100));
            syncRenderedMessageCountFromDom();
        }
    }

    if (state.assistantStreaming) {
        streamingChatId = chatId;
        isStreaming = true;
        setSendButtonStreaming(true);
        removeTypingIndicator();
        const wrap = ensureStreamingAssistantMessageElement();
        const textEl = wrap.querySelector('#streamingAssistantText');
        const typingEl = wrap.querySelector('#streamingTypingIndicator');
        const assistantText = String(state.assistantText || '');
        if (textEl) textEl.textContent = assistantText;
        if (typingEl) typingEl.style.display = assistantText ? 'none' : '';
        _ensureTranscriptionPreviewPlacement();
    }
}

/** DB/API timestamps are naive UTC; normalize before parse so local display/order stay correct. */
function normalizeUtcTimestampInput(ts) {
    if (ts === undefined || ts === null || ts === '') return null;
    if (typeof ts === 'number') return ts;
    let value = String(ts).trim();
    if (!value) return null;
    if (/[zZ]$/.test(value) || /[+-]\d{2}:?\d{2}$/.test(value)) return value;
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value)) {
        return value.replace(' ', 'T') + 'Z';
    }
    return value;
}

function messageTimestampMs(message) {
    if (!message) return 0;
    const normalized = normalizeUtcTimestampInput(message.timestamp);
    if (normalized === null) return 0;
    if (typeof normalized === 'number') return normalized;
    const parsed = Date.parse(normalized);
    return Number.isFinite(parsed) ? parsed : 0;
}

function messageDomTimestampMs(node) {
    if (!node) return 0;
    const raw = node.dataset.messageTs;
    if (raw != null && raw !== '') {
        const parsed = Number(raw);
        if (Number.isFinite(parsed)) return parsed;
    }
    return 0;
}

function messageDomRowId(node) {
    if (!node || node.dataset.turnChatId == null || node.dataset.turnChatId === '') return null;
    const parsed = Number(node.dataset.turnChatId);
    return Number.isFinite(parsed) ? parsed : null;
}

function messageDomRole(node) {
    if (!node || !node.classList) return '';
    if (node.classList.contains('user')) return 'user';
    if (node.classList.contains('assistant')) return 'assistant';
    if (node.classList.contains('activity')) return 'tool';
    if (node.classList.contains('chat-divider')) return 'divider';
    return '';
}

function messageRoleRank(role) {
    const roleRank = { user: 0, tool: 1, tool_group: 1, workflow: 1, assistant: 2, divider: 3 };
    return roleRank[role] != null ? roleRank[role] : 9;
}

function previousMessageSibling(node) {
    let prev = node ? node.previousElementSibling : null;
    while (prev && !prev.classList.contains('message') && !prev.classList.contains('chat-divider')) {
        prev = prev.previousElementSibling;
    }
    return prev;
}

/** Live path: assistant can finalize before the user bubble — pull the user row above it. */
function repairOrphanAssistantBeforeUser() {
    if (!chatMessages) return;
    const assistants = [...chatMessages.querySelectorAll('.message.assistant')]
        .filter((el) => el.id !== 'streamingAssistantMessage');
    for (const asst of assistants) {
        const prev = previousMessageSibling(asst);
        if (prev && prev.classList.contains('user')) continue;
        let next = asst.nextElementSibling;
        while (next && !next.classList.contains('message')) next = next.nextElementSibling;
        if (!next || !next.classList.contains('user')) continue;

        const asstRow = messageDomRowId(asst);
        const userRow = messageDomRowId(next);
        if (asstRow != null && userRow != null) {
            // Different turns — keep chronological order (e.g. clipboard reply, then a later user message).
            if (asstRow !== userRow) continue;
        } else {
            const asstTs = messageDomTimestampMs(asst);
            const userTs = messageDomTimestampMs(next);
            if (asstTs && userTs && userTs > asstTs) continue;
        }

        chatMessages.insertBefore(next, asst);
    }
}

function compareMessageInsertOrder(message, node) {
    const msgTs = messageTimestampMs(message);
    const nodeTs = messageDomTimestampMs(node);
    if (msgTs !== nodeTs) return msgTs - nodeTs;
    const msgRow = message.chat_row_id != null ? Number(message.chat_row_id) : null;
    const nodeRow = messageDomRowId(node);
    if (msgRow != null && nodeRow != null && msgRow !== nodeRow) return msgRow - nodeRow;
    return messageRoleRank(message.role) - messageRoleRank(messageDomRole(node));
}

function compareMessageOrder(a, b) {
    const rowA = a && a.chat_row_id != null ? Number(a.chat_row_id) : null;
    const rowB = b && b.chat_row_id != null ? Number(b.chat_row_id) : null;
    const turnA = a && a.turn_chat_id != null ? Number(a.turn_chat_id) : rowA;
    const turnB = b && b.turn_chat_id != null ? Number(b.turn_chat_id) : rowB;
    if (turnA != null && turnB != null && turnA !== turnB) return turnA - turnB;
    const tsA = messageTimestampMs(a);
    const tsB = messageTimestampMs(b);
    if (tsA !== tsB) return tsA - tsB;
    if (rowA != null && rowB != null && rowA !== rowB) return rowA - rowB;
    return messageRoleRank(a && a.role) - messageRoleRank(b && b.role);
}

function sortMessagesForDisplay(messages) {
    if (!Array.isArray(messages) || messages.length < 2) return messages || [];
    return [...messages].sort(compareMessageOrder);
}

function isLiveChatMessageNode(node) {
    if (!node) return false;
    const id = node.id || '';
    return id !== 'transcriptionStatus'
        && id !== 'streamingAssistantMessage'
        && id !== 'streamingAssistantActivity'
        && id !== 'typingIndicator';
}

function insertMessageElementInOrder(div, message) {
    if (!chatMessages || !div) return;
    const ts = messageTimestampMs(message);
    const rowId = message.chat_row_id != null ? Number(message.chat_row_id) : null;
    div.dataset.messageTs = String(ts || Date.now());
    if (rowId != null && !Number.isNaN(rowId)) {
        div.dataset.turnChatId = String(rowId);
    }
    const nodes = [...chatMessages.querySelectorAll('.message, .chat-divider')]
        .filter(isLiveChatMessageNode)
        .filter((node) => node !== div);
    for (const node of nodes) {
        if (compareMessageInsertOrder(message, node) < 0) {
            chatMessages.insertBefore(div, node);
            finalizeMessageElementMount(div, message);
            syncRenderedMessageCountFromDom();
            if (message.role === 'user') repairOrphanAssistantBeforeUser();
            return;
        }
    }
    const streamingMsg = document.getElementById('streamingAssistantMessage');
    const streamingActivity = document.getElementById('streamingAssistantActivity');
    const typingInd = document.getElementById('typingIndicator');
    const insertBefore = [streamingActivity, streamingMsg, typingInd]
        .find((node) => node && node !== div);
    if (insertBefore) {
        chatMessages.insertBefore(div, insertBefore);
    } else if (div.parentNode !== chatMessages) {
        chatMessages.appendChild(div);
    }
    finalizeMessageElementMount(div, message);
    syncRenderedMessageCountFromDom();
    if (message.role === 'user') repairOrphanAssistantBeforeUser();
}

function handleChatEventMessageAdded(msg) {
    if (msg.chat_id !== currentChatId) return;
    const role = msg.role || 'user';
    const content = msg.content || '';
    if (role === 'user') {
        // Voice/PTT messages come via WS without sendMessage() — render them.
        // Skip only if sendMessage() already rendered this exact text optimistically.
        const key = content.substring(0, 100);
        const np = _normalizeMsgPlain(content);
        if (_hasRecentOptimisticUserMessage(key) && (hasOpenUserTurnPlain(np) || hasRenderedUserMessagePlain(np))) {
            _acknowledgePersistedLiveUser(currentChatId, content);
            _discardLiveTranscriptionUi();
            return;
        }
        if (np && hasOpenUserTurnPlain(np)) {
            _acknowledgePersistedLiveUser(currentChatId, content);
            _discardLiveTranscriptionUi();
            return;
        }
        _lastStreamFinalizedPlain = null;
        _discardLiveTranscriptionUi();
        const div = createMessageElement({ role, content, timestamp: msg.timestamp });
        insertMessageElementInOrder(div, { role, content, timestamp: msg.timestamp, chat_row_id: msg.chat_row_id });
        _addOptimisticUserMessage(key);
        _acknowledgePersistedLiveUser(currentChatId, content);
        repairOrphanAssistantBeforeUser();
        scrollToBottom();
        scheduleRefreshChatHeaderStats();
        return;
    }
    if (role === 'assistant' && streamingChatId === currentChatId) {
        const wrap = document.getElementById('streamingAssistantMessage');
        const textEl = document.getElementById('streamingAssistantText');
        const streamedPlain = _normalizeMsgPlain(textEl ? textEl.textContent : '');
        const incomingPlain = _normalizeMsgPlain(content);
        if (!incomingPlain) return;
        if (wrap && !streamedPlain) {
            wrap.remove();
            streamingChatId = null;
        } else {
            return;
        }
    }
    if (role === 'assistant') {
        const plainA = _normalizeMsgPlain(content);
        if (plainA && hasRenderedMessagePlain('assistant', plainA)) {
            return;
        }
        if (plainA && _lastStreamFinalizedPlain && plainA === _lastStreamFinalizedPlain) {
            return;
        }
        // DB/pipeline may append the full reply after stream_finished finalized a prefix (interrupt/TTS race).
        if (
            plainA && _lastStreamFinalizedPlain
            && plainA.length > _lastStreamFinalizedPlain.length
            && plainA.startsWith(_lastStreamFinalizedPlain)
        ) {
            const nodes = [...chatMessages.querySelectorAll('.message.assistant')].filter(
                el => el.id !== 'streamingAssistantMessage'
            );
            const lastAsst = nodes[nodes.length - 1];
            if (lastAsst) {
                const textEl = lastAsst.querySelector('.message-text');
                if (textEl) {
                    textEl.innerHTML = formatMessage(content);
                    _lastStreamFinalizedPlain = plainA;
                    scrollToBottom();
                    return;
                }
            }
        }
    }
    const div = createMessageElement({ role, content, timestamp: msg.timestamp, chat_row_id: msg.chat_row_id });
    insertMessageElementInOrder(div, { role, content, timestamp: msg.timestamp, chat_row_id: msg.chat_row_id });
    if (role === 'assistant') flushPendingAssistantTools(div, msg.chat_row_id);
    scrollToBottom();
    scheduleRefreshChatHeaderStats({ checkTitle: role === 'assistant' });
}

function hasRenderedMessagePlain(role, plain) {
    if (!plain || !chatMessages) return false;
    return [...chatMessages.querySelectorAll(`.message.${role}`)]
        .filter(el => el.id !== 'transcriptionStatus' && el.id !== 'streamingAssistantMessage')
        .some(el => {
            const textEl = el.querySelector('.message-text');
            return textEl && _normalizeMsgPlain(textEl.textContent) === plain;
        });
}

function findLiveTurnAnchor(chatRowId) {
    if (!chatMessages) return null;
    if (chatRowId != null && chatRowId !== '') {
        const matches = [...chatMessages.querySelectorAll(`[data-turn-chat-id="${chatRowId}"]`)]
            .filter(el => el.id !== 'transcriptionStatus');
        const activity = matches.find(el => el.classList.contains('activity'));
        if (activity) return activity;
        if (matches.length) return matches[0];
    }
    return document.getElementById('streamingAssistantActivity')
        || document.getElementById('streamingAssistantMessage')
        || document.getElementById('typingIndicator');
}

function mergeChatUpdatedDuringStream(messages) {
    if (!chatMessages || !Array.isArray(messages)) return;
    let inserted = false;
    messages.forEach(message => {
        if (!message || message.role !== 'user') return;
        const plain = _normalizeMsgPlain(message.content || '');
        if (!plain) return;
        if (message.chat_row_id != null && findLiveTurnAnchor(message.chat_row_id)) return;
        if (hasOpenUserTurnPlain(plain)) return;
        _discardLiveTranscriptionUi();
        const div = createMessageElement(message);
        insertMessageElementInOrder(div, message);
        inserted = true;
    });
    if (inserted) {
        repairOrphanAssistantBeforeUser();
        syncRenderedMessageCountFromDom();
        scheduleRefreshChatHeaderStats();
    }
}

function repairMissingUserMessageForStream(chatId) {
    if (!chatMessages || chatId !== currentChatId) return;
    fetch(`${API_BASE}/chats/${chatId}`)
        .then(r => r.json())
        .then(data => {
            if (chatId !== currentChatId) return;
            mergeChatUpdatedDuringStream(data.messages || []);
        })
        .catch(() => {});
}

function handleChatEventStreamStarted(msg) {
    if (msg.chat_id !== currentChatId) return;
    const preview = document.getElementById('transcriptionStatus');
    const previewTextEl = preview ? preview.querySelector('#transcriptionStatusText') : null;
    const previewPlain = _normalizeMsgPlain(previewTextEl ? previewTextEl.textContent : '');
    if (!previewPlain || !hasOpenUserTurnPlain(previewPlain)) {
        promoteTranscriptionPreviewToUserMessage();
    } else {
        _discardLiveTranscriptionUi();
    }
    streamingChatId = msg.chat_id;
    _streamTextBuffer = '';
    _streamRafPending = false;
    removeTypingIndicator();
    const existingActivity = document.getElementById('streamingAssistantActivity');
    if (existingActivity) existingActivity.remove();
    const existing = document.getElementById('streamingAssistantMessage');
    if (existing) existing.remove();
    const div = ensureStreamingAssistantMessageElement();
    _setLiveAssistantStreaming(msg.chat_id, '');
    _ensureTranscriptionPreviewPlacement();
    repairMissingUserMessageForStream(msg.chat_id);
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
                _appendLiveAssistantStreamText(currentChatId, _streamTextBuffer);
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
        const existingActivityHtml = extractAssistantActivityForPreserve(wrap);
        const textEl = document.getElementById('streamingAssistantText');
        // Prefer response_text from event (clean, tool_call tags stripped) over raw streamed DOM text
        const raw = (msg.response_text != null && msg.response_text !== '') ? msg.response_text : (textEl ? textEl.textContent : '');
        if (!_normalizeMsgPlain(raw)) {
            wrap.remove();
            _lastStreamFinalizedPlain = null;
            _suppressChatUpdatedFetchUntil = 0;
        } else {
        wrap.id = '';
        const finishedAt = Date.now();
        wrap.dataset.messageTs = String(finishedAt);
        const clock = _formatTimestamp(finishedAt);
        const headerTs = clock ? `<span class="message-header-timestamp">${clock}</span>` : '';
        wrap.innerHTML = `
            <div class="message-avatar">${AVATAR_SVG_ASSISTANT}</div>
            <div class="message-content">
                ${headerTs}
                <div class="message-text">${formatMessage(raw)}</div>
                <div class="message-actions">
                    <button class="message-action-btn" onclick="copyMessage(this)">Copy</button>
                    <button class="message-action-btn message-action-play" onclick="playTTS(this)" title="Play with TTS">Play</button>
                </div>
            </div>
        `;
        restoreAssistantEmbeddedActivity(wrap, existingActivityHtml);
        insertMessageElementInOrder(wrap, { role: 'assistant', timestamp: finishedAt, chat_row_id: msg.chat_row_id });
        repairOrphanAssistantBeforeUser();
        syncRenderedMessageCountFromDom();
        _lastStreamFinalizedPlain = _normalizeMsgPlain(raw);
        _suppressChatUpdatedFetchUntil = Date.now() + 2500;
        scrollToBottom();
        }
    } else {
        // Streaming element was destroyed (e.g. by polling race) - fetch final state from DB
        const chatId = currentChatId;
        fetch(`${API_BASE}/chats/${chatId}`).then(r => r.json()).then(data => {
            if (currentChatId === chatId) {
                const messages = data.messages || [];
                const lastAssistant = messages.filter(m => m.role === 'assistant').pop();
                const lastPlain = lastAssistant ? _normalizeMsgPlain(lastAssistant.content) : '';
                if (!lastPlain || !hasRenderedMessagePlain('assistant', lastPlain)) {
                    renderMessages(messages, false);
                }
                syncRenderedMessageCountFromDom();
                _suppressChatUpdatedFetchUntil = Date.now() + 2500;
                updateChatSettingsDisplay({
                    title: data.title || 'New Chat',
                    provider: data.provider || '-',
                    model_name: data.model_name || '-',
                    voice_provider: data.voice_provider,
                    voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
                });
                scrollToBottom();
            }
        }).catch(() => {});
    }
    _clearLiveAssistantStreaming(msg.chat_id);
    // Null streamingChatId AFTER resolving so the poll guard (streamingChatId !== currentChatId)
    // still holds for any in-flight poll tick that fires before the microtask queue drains.
    // Resolve pending WebSocket-based wait (sendToAgentAndPoll / pollUntilAgentResponse)
    // Only fire if this stream_finished is for the currently active stream token (guards against stale events from old chats)
    _resolveAgentStreamWaiter(msg.chat_id);
    streamingChatId = null;
    scheduleRefreshChatHeaderStats({ checkTitle: true });
    // Reset input state in case the message came from voice/PTT (not web sendMessage)
    isStreaming = false;
    const isLoadedView = loadedChatId === currentChatId;
    messageInput.disabled = !isLoadedView;
    messageInput.placeholder = isLoadedView ? 'Send message...' : 'Load this chat to reply...';
    sendButton.disabled = !isLoadedView || !messageInput.value.trim();
    setViewOnlyChrome(!isLoadedView);
    setSendButtonStreaming(false);
}

function handleChatEventStreamError(msg) {
    if (msg.chat_id != null && msg.chat_id !== currentChatId) return;
    _streamTextBuffer = '';
    _streamRafPending = false;
    _lastStreamFinalizedPlain = null;
    removeTypingIndicator();
    const existingActivity = document.getElementById('streamingAssistantActivity');
    if (existingActivity) existingActivity.remove();
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
        displayError = '⚠️ Authentication failed. Check your API key in Settings → API Keys.';
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
    _resolveAgentStreamWaiter(msg.chat_id != null ? msg.chat_id : currentChatId);
    _clearLiveChatState(msg.chat_id != null ? msg.chat_id : currentChatId);
    streamingChatId = null;
}

function isProactivePlannerToolEvent(msg) {
    const haystack = [
        msg && msg.title,
        msg && msg.tool_name,
        msg && msg.routing_path
    ].filter(Boolean).join(' ').toLowerCase();
    return /\b(day|daily|week|month)\s+planner\b/.test(haystack)
        || haystack.includes('planner_orchestration')
        || haystack.includes('proactive_orchestrator');
}

function isHiddenLiveToolEvent(msg) {
    if (!msg) return true;
    if (msg.chat_suppressed === true) return true;
    if (msg.chat_visible === false) return true;
    return isProactivePlannerToolEvent(msg);
}

function handleChatEventToolExecuted(msg) {
    if (msg.chat_id !== currentChatId) return;
    if (!chatMessages) return;
    if (isHiddenLiveToolEvent(msg)) return;
    if (hasRenderedToolEvent(msg)) return;
    const toolMessage = {
        role: 'tool',
        timestamp: msg.timestamp || Date.now(),
        content: msg.result_summary || '',
        turn_chat_id: msg.turn_chat_id,
        tool_event_id: msg.id != null ? String(msg.id) : null,
        tool_event: {
            tool_name: msg.tool_name || 'tool',
            title: msg.title || `Executed ${msg.tool_name || 'tool'}`,
            status: msg.status || 'completed',
            result_summary: msg.result_summary || '',
            result_detail: msg.result_detail || '',
            routing_path: msg.routing_path || '',
            user_text: msg.user_text || '',
            compact: Boolean(msg.chat_compact),
            activity_style: msg.activity_style || 'active'
        }
    };
    appendToolToAssistantTurn(toolMessage);
    syncRenderedMessageCountFromDom();
    scrollToBottom();
}

function pendingAssistantToolKey(turnChatId) {
    if (turnChatId != null && turnChatId !== '') return String(turnChatId);
    return '__active_assistant_turn__';
}

function findAssistantMessageForTurn(turnChatId) {
    if (!chatMessages || turnChatId == null || turnChatId === '') return null;
    const matches = [...chatMessages.querySelectorAll(`[data-turn-chat-id="${String(turnChatId)}"]`)];
    return matches.reverse().find(el => el.classList.contains('assistant')) || null;
}

function queuePendingAssistantTool(message) {
    const key = pendingAssistantToolKey(message && message.turn_chat_id);
    const existing = _pendingAssistantToolMessages.get(key) || [];
    existing.push(message);
    _pendingAssistantToolMessages.set(key, existing);
}

function flushPendingAssistantTools(target, turnChatId) {
    if (!target) return;
    const keys = [pendingAssistantToolKey(turnChatId)];
    if (turnChatId != null && turnChatId !== '') keys.push(pendingAssistantToolKey(null));
    keys.forEach(key => {
        const tools = _pendingAssistantToolMessages.get(key);
        if (tools && tools.length) {
            appendAssistantActivity(target, tools);
            _pendingAssistantToolMessages.delete(key);
        }
    });
}

function toolEventName(message) {
    return String((message && message.tool_event && message.tool_event.tool_name) || '').trim();
}

function isStandaloneSystemActivity(message) {
    const name = toolEventName(message);
    return name === 'chat_settings' || name === 'read_aloud';
}

function shouldEmbedToolInAssistantTurn(toolMessage) {
    return !isStandaloneSystemActivity(toolMessage);
}

function appendStandaloneToolActivity(message) {
    if (!chatMessages) return null;
    const group = appendStandaloneToolToActivityGroup(message);
    if (group && !group.isConnected) {
        insertMessageElementInOrder(group, {
            role: 'tool_group',
            timestamp: message.timestamp,
            chat_row_id: message.chat_row_id
        });
        scrollToBottom();
    }
    return group;
}

function findAppendableStandaloneActivityGroup() {
    if (!chatMessages) return null;
    const last = chatMessages.lastElementChild;
    if (!last || !last.classList.contains('message') || !last.classList.contains('activity')) return null;
    if (last.classList.contains('assistant-turn-activity')) return null;
    if (!last.classList.contains('tool-execution-group')) return null;
    if (last.querySelectorAll('.activity-step').length >= MAX_ACTIVITY_STEPS_PER_GROUP) return null;
    return last;
}

function appendStandaloneToolToActivityGroup(message) {
    let group = isStandaloneSystemActivity(message) ? null : findAppendableStandaloneActivityGroup();
    if (!group) {
        group = createToolExecutionGroupElement({
            role: 'tool_group',
            timestamp: message.timestamp,
            turn_chat_id: null,
            tools: []
        });
    }
    const list = group.querySelector('.activity-steps');
    if (list) list.insertAdjacentHTML('beforeend', toolExecutionItemHtml(message));
    group.querySelectorAll('.activity-step').forEach(syncActivityMessageAlignment);
    return group;
}

function appendToolToAssistantTurn(message) {
    if (isStandaloneSystemActivity(message)) {
        return appendStandaloneToolActivity(message);
    }
    const turnChatId = message && message.turn_chat_id != null ? String(message.turn_chat_id) : null;
    const streamingMsg = document.getElementById('streamingAssistantMessage');
    const target = findAssistantMessageForTurn(turnChatId) || streamingMsg;
    if (!target) {
        queuePendingAssistantTool(message);
        return null;
    }
    appendAssistantActivity(target, [message]);
    return target;
}

function activityGroupMatchesTurn(group, turnChatId) {
    if (!group) return false;
    const groupTurn = group.dataset.turnChatId;
    if (turnChatId == null || turnChatId === '') return groupTurn == null || groupTurn === '';
    return groupTurn === String(turnChatId);
}

function appendToolToActivityGroup(message) {
    const turnChatId = message.turn_chat_id != null ? String(message.turn_chat_id) : null;
    const anchor = getLateToolInsertAnchor();
    let group = null;
    if (anchor && anchor.previousElementSibling && anchor.previousElementSibling.classList.contains('tool-execution-group')) {
        const candidate = anchor.previousElementSibling;
        if (activityGroupMatchesTurn(candidate, turnChatId)) group = candidate;
    }
    if (!group) {
        const last = chatMessages ? chatMessages.lastElementChild : null;
        if (last && last.classList.contains('tool-execution-group') && activityGroupMatchesTurn(last, turnChatId)) {
            group = last;
        }
    }
    if (!group) {
        group = createToolExecutionGroupElement({
            role: 'tool_group',
            timestamp: message.timestamp,
            turn_chat_id: message.turn_chat_id,
            tools: []
        });
    }
    const list = group.querySelector('.activity-steps');
    if (list) list.insertAdjacentHTML('beforeend', toolExecutionItemHtml(message));
    group.querySelectorAll('.activity-step').forEach(syncActivityMessageAlignment);
    return group;
}

function handleChatEventWorkflow(msg) {
    if (msg.chat_id !== currentChatId) return;
    const workflowEvent = {
        run_id: msg.run_id,
        workflow_id: msg.workflow_id,
        workflow_name: msg.workflow_name || 'Workflow',
        type: msg.type || 'event',
        status: msg.status || 'running',
        summary: msg.summary || '',
        phase: msg.phase || '',
        step_id: msg.step_id,
        step_name: msg.step_name || '',
        id: msg.id || null,
        agent_activity: msg.agent_activity || {},
        chat_suppressed: msg.chat_suppressed,
        chat_visible: msg.chat_visible
    };
    if (isHiddenWorkflowEvent(workflowEvent)) return;
    if (hasRenderedWorkflowEvent(workflowEvent)) return;
    const block = createWorkflowEventElement({
        role: 'workflow',
        timestamp: msg.timestamp || Date.now(),
        content: msg.summary || '',
        workflow_event: workflowEvent
    });
    const streamingMsg = document.getElementById('streamingAssistantMessage');
    const streamingActivity = document.getElementById('streamingAssistantActivity');
    const typingInd = document.getElementById('typingIndicator');
    const insertBefore = streamingActivity || streamingMsg || typingInd;
    if (insertBefore) {
        chatMessages.insertBefore(block, insertBefore);
    } else {
        chatMessages.appendChild(block);
    }
    syncRenderedMessageCountFromDom();
    scrollToBottom();
}

function showTranscriptionStatus(text, done, clearLivePreview, discardLivePreview) {
    if (!chatMessages) return;
    const t = text != null ? String(text) : '';
    const trimmed = t.trim();

    function removeStatusBar() {
        _stopSttPreviewClock();
        if (transcriptionStatusTimer) {
            clearTimeout(transcriptionStatusTimer);
            transcriptionStatusTimer = null;
        }
        const el = document.getElementById('transcriptionStatus');
        if (el) el.remove();
    }

    // Dictation finished — drop any live preview without committing a user message.
    if (discardLivePreview) {
        _clearLiveChatState(currentChatId);
        _discardLiveTranscriptionUi();
        return;
    }

    // Committed user message — real bubble arrives via message_added
    if (clearLivePreview) {
        promoteTranscriptionPreviewToUserMessage();
        _discardLiveTranscriptionUi();
        return;
    }
    // Backend sent done + empty (defensive)
    if (done && !trimmed) {
        _clearLiveChatState(currentChatId);
        _discardLiveTranscriptionUi();
        return;
    }
    // File / tool transcription finished — short assistant notice, then hide
    if (done && trimmed && canPromoteVoiceTranscriptInChat() && hasLiveUserTranscriptionPreview()) {
        promoteTranscriptionPreviewToUserMessage(trimmed);
        _discardLiveTranscriptionUi();
        return;
    }
    if (done && trimmed) {
        _clearLiveChatState(currentChatId);
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
        } else {
            wrap.className = 'message assistant';
            const av = wrap.querySelector('.message-avatar');
            if (av) av.innerHTML = AVATAR_SVG_ASSISTANT;
            wrap.querySelectorAll('.message-meta, .stt-preview-label').forEach(el => el.remove());
        }
        const textEl = document.getElementById('transcriptionStatusText');
        if (textEl) textEl.textContent = trimmed;
        scrollToBottom();
        if (transcriptionStatusTimer) {
            clearTimeout(transcriptionStatusTimer);
            transcriptionStatusTimer = null;
        }
        transcriptionStatusTimer = setTimeout(removeStatusBar, 5000);
        return;
    }

    // Live speech-to-text (updates while you talk)
    if (trimmed && hasRenderedUserMessagePlain(_normalizeMsgPlain(trimmed))) {
        _acknowledgePersistedLiveUser(currentChatId, trimmed);
        _discardLiveTranscriptionUi();
        return;
    }
    let wrap = document.getElementById('transcriptionStatus');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'message user';
        wrap.id = 'transcriptionStatus';
        wrap.innerHTML = `
            <div class="message-avatar">${AVATAR_SVG_USER}</div>
            <div class="message-content">
                <span class="message-header-timestamp stt-preview-label" aria-live="polite"></span>
                <div class="message-text" id="transcriptionStatusText" aria-live="polite"></div>
                <div class="message-actions">
                    <button type="button" class="message-action-btn" onclick="copyMessage(this)">Copy</button>
                </div>
            </div>
        `;
        const streamAnchor = document.getElementById('streamingAssistantMessage');
        if (streamAnchor && chatMessages.contains(streamAnchor)) {
            chatMessages.insertBefore(wrap, streamAnchor);
        } else {
            chatMessages.appendChild(wrap);
        }
    } else {
        wrap.className = 'message user';
        const av = wrap.querySelector('.message-avatar');
        if (av) av.innerHTML = AVATAR_SVG_USER;
        if (!wrap.querySelector('.stt-preview-label')) {
            const mc = wrap.querySelector('.message-content');
            if (mc) {
                const meta = document.createElement('span');
                meta.className = 'message-header-timestamp stt-preview-label';
                meta.setAttribute('aria-live', 'polite');
                const first = mc.firstChild;
                if (first) mc.insertBefore(meta, first);
                else mc.appendChild(meta);
            }
        }
        if (!wrap.querySelector('.message-actions')) {
            const mc = wrap.querySelector('.message-content');
            if (mc) {
                const actions = document.createElement('div');
                actions.className = 'message-actions';
                actions.innerHTML = '<button type="button" class="message-action-btn" onclick="copyMessage(this)">Copy</button>';
                mc.appendChild(actions);
            }
        }
    }
    const textEl = document.getElementById('transcriptionStatusText');
    if (textEl) textEl.textContent = trimmed || '…';
    if (trimmed) _setLivePreviewState(trimmed);
    _ensureSttPreviewClockRunning();
    _ensureTranscriptionPreviewPlacement();
    scrollToBottom();
}

function queueTranscriptionStatusUpdate(text, done, clearLivePreview, discardLivePreview) {
    if (done || clearLivePreview || discardLivePreview) {
        queuedTranscriptionStatusUpdate = null;
        transcriptionStatusFlushScheduled = false;
        showTranscriptionStatus(text, done, clearLivePreview, discardLivePreview);
        return;
    }
    queuedTranscriptionStatusUpdate = { text, done, clearLivePreview, discardLivePreview };
    if (transcriptionStatusFlushScheduled) return;
    transcriptionStatusFlushScheduled = true;
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(flushQueuedTranscriptionStatusUpdate);
    } else {
        setTimeout(flushQueuedTranscriptionStatusUpdate, 16);
    }
    setTimeout(flushQueuedTranscriptionStatusUpdate, 80);
}

function flushQueuedTranscriptionStatusUpdate() {
    if (!transcriptionStatusFlushScheduled && !queuedTranscriptionStatusUpdate) return;
    const update = queuedTranscriptionStatusUpdate;
    queuedTranscriptionStatusUpdate = null;
    transcriptionStatusFlushScheduled = false;
    if (!update) return;
    showTranscriptionStatus(
        update.text,
        update.done,
        update.clearLivePreview,
        update.discardLivePreview
    );
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

function focusMessageInput() {
    if (!messageInput || messageInput.disabled) return;
    if (emptyState && emptyState.style.display !== 'none') return;
    messageInput.focus();
}

function measureChatTitleTextWidth(titleEl) {
    if (!titleEl) return 0;
    const style = window.getComputedStyle(titleEl);
    const probe = document.createElement('span');
    probe.textContent = titleEl.textContent || '';
    probe.style.cssText = [
        'position:absolute',
        'visibility:hidden',
        'white-space:nowrap',
        `font:${style.font}`,
        `letter-spacing:${style.letterSpacing}`,
    ].join(';');
    document.body.appendChild(probe);
    const width = probe.offsetWidth;
    probe.remove();
    return width;
}

function applyTitleMarquee(wrapEl, titleEl, { wrapClass, titleClass } = {}) {
    if (!wrapEl || !titleEl) return;
    const wrapMarqueeClass = wrapClass || 'chat-item-title-wrap--marquee';
    const titleMarqueeClass = titleClass || 'chat-item-title--marquee';
    titleEl.classList.remove(titleMarqueeClass);
    wrapEl.classList.remove(wrapMarqueeClass);
    titleEl.style.removeProperty('--marquee-distance');
    const textWidth = measureChatTitleTextWidth(titleEl);
    const available = wrapEl.clientWidth;
    if (textWidth <= available + 1) return;
    titleEl.classList.add(titleMarqueeClass);
    wrapEl.classList.add(wrapMarqueeClass);
    titleEl.style.setProperty('--marquee-distance', `${textWidth - available}px`);
}

function applyChatTitleMarqueeForWrap(wrapEl) {
    applyTitleMarquee(wrapEl, wrapEl ? wrapEl.querySelector('.chat-item-title') : null);
}

function applyHeaderTitleMarquee() {
    const wrap = document.querySelector('.chat-settings-title-wrap');
    const titleEl = document.getElementById('chatTitle');
    applyTitleMarquee(wrap, titleEl, {
        wrapClass: 'chat-settings-title-wrap--marquee',
        titleClass: 'chat-settings-title--marquee',
    });
}

function applySidebarTitleMarquees() {
    document.querySelectorAll('.chat-item-title-wrap').forEach(applyChatTitleMarqueeForWrap);
}

let _titleMarqueeResizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(_titleMarqueeResizeTimer);
    _titleMarqueeResizeTimer = setTimeout(() => {
        applySidebarTitleMarquees();
        applyHeaderTitleMarquee();
    }, 150);
});

function getAdjacentChatIdAfterDelete(chatId) {
    const item = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
    if (!item) return null;
    let sibling = item.previousElementSibling;
    while (sibling && !sibling.classList.contains('chat-item')) {
        sibling = sibling.previousElementSibling;
    }
    if (sibling) return parseInt(sibling.getAttribute('data-chat-id'), 10);
    sibling = item.nextElementSibling;
    while (sibling && !sibling.classList.contains('chat-item')) {
        sibling = sibling.nextElementSibling;
    }
    if (sibling) return parseInt(sibling.getAttribute('data-chat-id'), 10);
    return null;
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
    requestAnimationFrame(applySidebarTitleMarquees);
}

function createChatItem(chat) {
    const div = document.createElement('div');
    div.className = 'chat-item';
    div.setAttribute('data-chat-id', String(chat.id));
    div.setAttribute('title', 'Click to view. Use Load to make it active in the agent.');
    div.setAttribute('tabindex', '0');
    div.setAttribute('role', 'option');
    div.setAttribute('aria-selected', chat.id === currentChatId ? 'true' : 'false');
    if (chat.id === currentChatId) {
        div.classList.add('active');
    }
    
    div.innerHTML = `
        <span class="chat-item-in-agent-star" style="display: none;" title="Loaded in voice agent">★</span>
        <span class="chat-item-title-wrap">
            <span class="chat-item-title">${escapeHtml(chat.title)}</span>
        </span>
        <span class="chat-item-agent-badge" style="display: none;" title="Loaded in voice agent">In agent</span>
        <div class="chat-item-actions flex items-center gap-1 flex-shrink-0">
            <button type="button" class="chat-item-rename-btn p-1.5 rounded text-gray-300 hover:bg-white/10 inline-flex flex-shrink-0" data-chat-id="${chat.id}" title="Rename" aria-label="Rename chat">
                <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
            </button>
            <button type="button" class="chat-item-delete-btn p-1.5 rounded text-gray-400 hover:text-red-400 hover:bg-red-500/20 flex-shrink-0" data-chat-id="${chat.id}" title="Delete" aria-label="Delete chat">
                <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
        </div>
    `;
    
    let clickTimer = null;
    div.addEventListener('click', (e) => {
        if (e.target.closest('.chat-item-rename-btn, .chat-item-delete-btn')) return;
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => {
            clickTimer = null;
            selectChat(chat.id);
        }, 220);
    });
    div.addEventListener('dblclick', (e) => {
        if (e.target.closest('.chat-item-rename-btn, .chat-item-delete-btn')) return;
        e.preventDefault();
        clearTimeout(clickTimer);
        clickTimer = null;
        selectChat(chat.id).then(() => focusMessageInput());
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
    const deleteBtn = div.querySelector('.chat-item-delete-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            deleteChat(chat.id);
        });
    }

    return div;
}

function bindChatListKeyboard() {
    if (!chatList || !window.DecisionsListKeyboard) return;
    window.DecisionsListKeyboard.bind({
        listEl: chatList,
        namespace: 'chat',
        rowSelector: '.chat-item',
        getRowId: (row) => parseInt(row.getAttribute('data-chat-id'), 10),
        getSelectedId: () => currentChatId,
        selectOnNavigate: true,
        onSelect: (id) => { selectChat(id); },
        onEnter: () => { focusMessageInput(); },
        onDelete: (id) => { deleteChat(id); },
        pageGuard: () => !!chatList,
    });
}
bindChatListKeyboard();

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
        // Inherit provider/model/voice from current chat settings if loaded, else Settings defaults
        let provider = null, modelName = null, voiceProvider = null, voiceModel = null;
        if (currentChatSettings?.provider) {
            provider = currentChatSettings.provider;
            modelName = currentChatSettings.model_name || null;
            voiceProvider = currentChatSettings.voice_provider || null;
            voiceModel = currentChatSettings.voice_model || null;
        } else {
            const ep = document.getElementById('emptyStateLlmProvider');
            if (ep) {
                provider = document.getElementById('emptyStateLlmProvider')?.value?.trim() || null;
                modelName = document.getElementById('emptyStateLlmModel')?.value?.trim() || null;
                voiceProvider = document.getElementById('emptyStateVoiceProvider')?.value?.trim() || null;
                voiceModel = document.getElementById('emptyStateVoiceModel')?.value?.trim() || null;
            } else {
                const d = await fetchDefaultsForNewChat();
                provider = d.provider;
                modelName = d.model_name || null;
                voiceProvider = d.voice_provider || null;
                voiceModel = d.voice_model || null;
            }
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
    removeTypingIndicator();
    const staleStream = document.getElementById('streamingAssistantMessage');
    if (staleStream) staleStream.remove();
    const staleActivity = document.getElementById('streamingAssistantActivity');
    if (staleActivity) staleActivity.remove();
    _renderedMessageCount = 0;
    updateActiveChat();
    try {
        const response = await fetch(`${API_BASE}/chats/${chatId}`);
        const data = await response.json();
        // Discard if a newer selectChat/loadChat has already taken over
        if (seq !== _selectSeq) return;
        renderMessages(data.messages);
        restoreLiveChatState(chatId, data.messages);
        updateChatSettingsDisplay({
            title: data.title || 'New Chat',
            provider: data.provider || '-',
            model_name: data.model_name || '-',
            voice_provider: data.voice_provider,
            voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
        });
        showChatView(loadedChatId === chatId);
        updateLoadButtonVisibility();
        startChatWebSocket();
        setTimeout(() => { chatMessages.scrollTop = chatMessages.scrollHeight; }, 300);
        await saveLastChatId(chatId);
        scheduleRefreshChatHeaderStats();
        syncKanbanSourceChatContext();
    } catch (error) {
        console.error('Error selecting chat:', error);
    }
}

// Load Chat: select + load into agent (hot-swap LLM/voice/history). Called only by explicit Load actions.
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
    _lastStreamFinalizedPlain = null;
    isStreaming = false;
    setSendButtonStreaming(false);
    removeTypingIndicator();
    const staleStream = document.getElementById('streamingAssistantMessage');
    if (staleStream) staleStream.remove();
    const staleActivity = document.getElementById('streamingAssistantActivity');
    if (staleActivity) staleActivity.remove();
    _renderedMessageCount = 0;
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
        restoreLiveChatState(chatId, data.messages);
        updateChatSettingsDisplay({
            title: data.title || 'New Chat',
            provider: data.provider || '-',
            model_name: data.model_name || '-',
            voice_provider: data.voice_provider,
            voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint
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
                const loadRes = await fetch(`${API_BASE}/chats/${chatId}/load-in-agent`, { method: 'POST' });
                if (!loadRes.ok) {
                    const err = await loadRes.json().catch(() => ({}));
                    throw new Error(err.detail || err.error || 'Load in agent failed');
                }
                agentCurrentChatId = chatId;
                updateActiveChat();
            } catch (e) {
                console.warn('Load-in-agent failed:', e);
                showChatSnackbar('Agent not loaded', 'error');
            }
        }
        scheduleRefreshChatHeaderStats();
    } catch (error) {
        console.error('Error loading chat:', error);
    } finally {
        syncKanbanSourceChatContext();
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
    syncKanbanSourceChatContext();
}

function updateActiveChat() {
    document.querySelectorAll('.chat-item').forEach(item => {
        const id = parseInt(item.getAttribute('data-chat-id'), 10);
        item.classList.toggle('active', id === currentChatId);
        item.setAttribute('aria-selected', id === currentChatId ? 'true' : 'false');
        item.classList.toggle('loaded', id === loadedChatId);
        const inAgent = (id === agentCurrentChatId);
        item.classList.toggle('in-agent', inAgent);
        const star = item.querySelector('.chat-item-in-agent-star');
        if (star) star.style.display = inAgent ? 'inline-block' : 'none';
    });
}

let _emptyStateAgentWatchTimer = null;

function isEmptyStateVisible() {
    return !!(emptyState && emptyState.style.display !== 'none');
}

function stopEmptyStateAgentWatch() {
    if (_emptyStateAgentWatchTimer) {
        clearInterval(_emptyStateAgentWatchTimer);
        _emptyStateAgentWatchTimer = null;
    }
}

/** Voice/PTT can create the first chat while the welcome screen is still showing — adopt it into the feed. */
async function tryAdoptAgentFirstChat(data) {
    if (!isEmptyStateVisible() || currentChatId != null) return false;
    const chats = Array.isArray(data?.chats) ? data.chats : [];
    const agentId = data?.agent_current_chat_id != null ? Number(data.agent_current_chat_id) : null;
    let chatId = null;
    if (agentId != null && !Number.isNaN(agentId) && chats.some((c) => c.id === agentId)) {
        chatId = agentId;
    } else if (chats.length > 0) {
        chatId = chats[0].id;
    }
    if (chatId == null) return false;
    stopEmptyStateAgentWatch();
    hideEmptyStateAgentLoading();
    await loadChat(chatId, { skipLoadInAgent: true });
    return true;
}

function startEmptyStateAgentWatch() {
    stopEmptyStateAgentWatch();
    if (!isEmptyStateVisible()) return;
    _emptyStateAgentWatchTimer = setInterval(async () => {
        if (!isEmptyStateVisible() || currentChatId != null) {
            stopEmptyStateAgentWatch();
            return;
        }
        try {
            const data = await loadChats();
            await tryAdoptAgentFirstChat(data);
        } catch (e) {
            console.debug('Empty-state agent watch:', e);
        }
    }, 1500);
}

function showChatView(isLoaded) {
    stopEmptyStateAgentWatch();
    emptyState.style.display = 'none';
    chatMessages.style.display = 'flex';
    if (chatSettingsHeader) chatSettingsHeader.style.display = 'flex';
    inputContainer.style.display = 'block';
    setViewOnlyChrome(!isLoaded);
    // Keep the composer visible for viewed chats, but only the loaded chat can receive replies.
    if (!isStreaming) {
        messageInput.disabled = !isLoaded;
        messageInput.placeholder = isLoaded ? 'Send message...' : 'Load this chat to reply...';
        sendButton.disabled = !isLoaded || !messageInput.value.trim();
    }
}

function updateLoadButtonVisibility() {
    const show = currentChatId != null && loadedChatId !== currentChatId;
    if (inputLoadButton) {
        inputLoadButton.style.display = show ? 'inline-flex' : 'none';
        inputLoadButton.disabled = !show;
    }
    setViewOnlyChrome(show);
}

function setViewOnlyChrome(isViewOnly) {
    if (chatSettingsHeader) chatSettingsHeader.classList.toggle('view-only', Boolean(isViewOnly));
    if (inputContainer) inputContainer.classList.toggle('view-only', Boolean(isViewOnly));
    const headerControls = [
        configureChatButton,
        compactChatButton,
        forkChatButton,
        contextRing
    ];
    headerControls.forEach(el => { if (el) el.disabled = Boolean(isViewOnly); });
    if (speakerToggle) speakerToggle.disabled = Boolean(isViewOnly);
    if (inputLoadButton) {
        inputLoadButton.style.display = isViewOnly && currentChatId != null ? 'inline-flex' : 'none';
        inputLoadButton.disabled = !isViewOnly || currentChatId == null;
    }
}

function showEmptyState() {
    stopChatWebSocket();
    emptyState.style.display = 'flex';
    chatMessages.style.display = 'none';
    chatSettingsHeader.style.display = 'none';
    inputContainer.style.display = 'block';
    setViewOnlyChrome(false);
    // Ensure input is usable (may have been disabled during a stream)
    messageInput.disabled = false;
    messageInput.placeholder = 'Send message...';
    sendButton.disabled = !messageInput.value.trim();
    isStreaming = false;
    setSendButtonStreaming(false);
    hideEmptyStateAgentLoading();
    loadEmptyStateDropdowns();
    syncKanbanSourceChatContext();
    startEmptyStateAgentWatch();
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

/** Populates empty-state form (shared markup with New chat modal) and reveals it. */
async function loadEmptyStateDropdowns() {
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
        populateChatVoiceProviderSelect(voiceProviderEl);
        const vpResolved = resolvedVoiceProviderForChat(generalData);
        const vpOpts = [...voiceProviderEl.options].map(o => o.value);
        if (vpOpts.includes(vpResolved)) {
            voiceProviderEl.value = vpResolved;
        } else if (voiceProviderEl.options[0] && voiceProviderEl.options[0].value) {
            voiceProviderEl.selectedIndex = 0;
        }
        await loadEmptyStateVoiceModels(voiceProviderEl.value);
        const voiceKey = getVoiceSettingsKey(voiceProviderEl.value);
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
    await loadEmptyStateSkins();
    toggleEmptyStateOllamaPullBtn();
    toggleKiloPromo('emptyStateLlmProvider');
    revealEmptyStateForm();
}

function revealEmptyStateForm() {
    const loader = document.getElementById('emptyStateLoader');
    const form = document.getElementById('emptyStateForm');
    const prompt = document.getElementById('emptyStatePrompt');
    if (loader) loader.style.display = 'none';
    if (form) form.style.display = '';
    if (prompt) prompt.style.display = '';
    if (!loadedChatId && !currentChatId) {
        messageInput.disabled = false;
        messageInput.placeholder = 'Send message...';
        sendButton.disabled = !messageInput.value.trim();
    }
    if (typeof injectInfoIcons === 'function') injectInfoIcons();
}

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

function toggleEmptyStateOllamaPullBtn() {
    const btn = document.getElementById('emptyStateOllamaPullBtn');
    const provider = document.getElementById('emptyStateLlmProvider');
    if (!btn || !provider) return;
    btn.style.display = (provider.value === 'ollama') ? '' : 'none';
}

async function pullOllamaModelFromEmptyState() {
    window.open('/settings#llms', '_blank');
}

async function loadEmptyStateLlmModels(provider) {
    const el = document.getElementById('emptyStateLlmModel');
    if (!el) return;
    const providerId = providerIdFromDisplay(provider || '');
    el.innerHTML = '<option value="">Loading…</option>';
    try {
        const response = await fetch(`/api/llms/models?type=conversational&provider=${encodeURIComponent(providerId)}`);
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
    await _ensureTTSProviders();
    if (!provider) {
        el.innerHTML = '<option value="">Select provider</option>';
        return;
    }
    const prov = _ttsProviders && _ttsProviders.find(p => p.id === provider);
    if (prov && Array.isArray(prov.voices) && prov.voices.length) {
        populateChatVoiceModelSelectForEl(el, provider);
        return;
    }
    el.innerHTML = '<option value="">Loading…</option>';
    try {
        const response = await fetch(`/api/voices/${encodeURIComponent(provider)}`);
        const data = await response.json();
        const voices = Array.isArray(data) ? data : (data.voices || data.chats || []);
        el.innerHTML = voices.length ? voices.map(chatVoiceOptionHtml).join('') : '<option value="">No voices</option>';
    } catch (e) {
        el.innerHTML = '<option value="">Error loading</option>';
    }
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

// Render Messages. When preserveOnEmpty is true, don't replace with empty (avoids clearing during send flow).
/** Track how many child message elements are currently rendered to enable incremental append. */
let _renderedMessageCount = 0;
/** After stream_finished updates the DOM, ignore chat_updated refetches briefly (same write often triggers both). */
let _suppressChatUpdatedFetchUntil = 0;
/** Tool activity can arrive before the assistant DOM exists; hold it briefly by turn id. */
const _pendingAssistantToolMessages = new Map();
const MAX_ACTIVITY_STEPS_PER_GROUP = 3;

/** Persisted rows only — excludes live streaming/typing placeholders. */
function countPersistedDomMessages() {
    if (!chatMessages) return 0;
    return [...chatMessages.querySelectorAll('.message, .chat-divider')].filter(isLiveChatMessageNode).length;
}

/** Keep _renderedMessageCount aligned when WebSocket finalizes #streamingAssistantMessage (no renderMessages() call). */
function syncRenderedMessageCountFromDom() {
    if (!chatMessages) return;
    _renderedMessageCount = countPersistedDomMessages();
}

function isTraceMessage(message) {
    return message && (message.role === 'tool' || message.role === 'workflow');
}

function traceTurnChatId(message) {
    if (!message) return null;
    if (message.turn_chat_id != null && message.turn_chat_id !== '') {
        const parsed = Number(message.turn_chat_id);
        return Number.isFinite(parsed) ? parsed : null;
    }
    if (message.chat_row_id != null && message.chat_row_id !== '') {
        const parsed = Number(message.chat_row_id);
        return Number.isFinite(parsed) ? parsed : null;
    }
    if (message.role === 'tool_group' && Array.isArray(message.tools) && message.tools.length) {
        return traceTurnChatId(message.tools[0]);
    }
    if (message.role === 'tool' && message.tool_event && message.tool_event.turn_chat_id != null) {
        const parsed = Number(message.tool_event.turn_chat_id);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}

function assistantTurnChatId(message) {
    if (!message || message.role !== 'assistant') return null;
    if (message.chat_row_id == null || message.chat_row_id === '') return null;
    const parsed = Number(message.chat_row_id);
    return Number.isFinite(parsed) ? parsed : null;
}

function traceBelongsToAssistantTurn(traceMessage, assistantMessage) {
    const assistantTurn = assistantTurnChatId(assistantMessage);
    const traceTurn = traceTurnChatId(traceMessage);
    if (assistantTurn == null || traceTurn == null) return true;
    return assistantTurn === traceTurn;
}

function toolMessagesFromTrace(message) {
    if (!message) return [];
    if (message.role === 'tool_group' && Array.isArray(message.tools)) {
        return message.tools.filter(tool => tool && tool.role === 'tool');
    }
    if (message.role === 'tool') return [message];
    return [];
}

function addToolsToTurnMap(map, turnId, tools) {
    if (!tools.length) return;
    const key = Number(turnId);
    const existing = map.get(key) || [];
    existing.push(...tools);
    map.set(key, existing);
}

function toolGroupFromTools(tools, turnChatId, options = {}) {
    return {
        role: 'tool_group',
        timestamp: tools[0] && tools[0].timestamp,
        turn_chat_id: turnChatId,
        assistant_turn_activity: Boolean(options.assistant_turn_activity),
        tools
    };
}

function buildStandaloneToolGroups(standaloneTools) {
    const groups = [];
    let batch = [];
    standaloneTools.forEach(tool => {
        if (isStandaloneSystemActivity(tool)) {
            if (batch.length) {
                groups.push(toolGroupFromTools(batch, null));
                batch = [];
            }
            groups.push(toolGroupFromTools([tool], null));
            return;
        }
        batch.push(tool);
        if (batch.length >= MAX_ACTIVITY_STEPS_PER_GROUP) {
            groups.push(toolGroupFromTools(batch, null));
            batch = [];
        }
    });
    if (batch.length) groups.push(toolGroupFromTools(batch, null));
    return groups;
}

function pushStandaloneToolGroups(grouped, standaloneTools) {
    grouped.push(...buildStandaloneToolGroups(standaloneTools));
}

function normalizeReadAloudAssistantMessages(messages) {
    if (!Array.isArray(messages)) return [];
    return messages.flatMap(message => {
        const converted = parseLegacyReadAloudAssistant(message);
        return converted ? [converted] : [message];
    });
}

function parseLegacyReadAloudAssistant(message) {
    if (!message || message.role !== 'assistant') return null;
    const raw = String(message.content || '').trim();
    if (!raw) return null;
    let label = '';
    let detail = '';
    const clipMatch = raw.match(/^\[Read from clipboard:\s*(.+)\]$/s);
    const aloudMatch = raw.match(/^\[Read aloud(?::\s*(.+))?\]$/s);
    if (clipMatch) {
        label = 'Read from clipboard';
        detail = clipMatch[1].replace(/\.\.\.$/, '').trim();
    } else if (aloudMatch) {
        label = 'Read aloud';
        detail = (aloudMatch[1] || '').replace(/\.\.\.$/, '').trim();
    } else if (raw === 'The clipboard is empty.') {
        label = 'Read from clipboard';
        detail = raw;
    } else {
        return null;
    }
    return {
        role: 'tool',
        timestamp: message.timestamp,
        chat_row_id: message.chat_row_id,
        turn_chat_id: message.chat_row_id,
        content: detail,
        tool_event: {
            tool_name: 'read_aloud',
            title: label,
            status: 'completed',
            result_summary: detail,
            result_detail: detail,
            compact: false
        }
    };
}

function normalizeTraceMessages(messages) {
    const toolsByAssistantTurn = new Map();
    const standaloneTools = [];
    messages.forEach(message => {
        if (!message || (message.role !== 'tool' && message.role !== 'tool_group')) return;
        const tools = toolMessagesFromTrace(message);
        tools.forEach(tool => {
            if (!shouldEmbedToolInAssistantTurn(tool)) {
                standaloneTools.push(tool);
                return;
            }
            const turnId = traceTurnChatId(tool) ?? traceTurnChatId(message);
            if (turnId == null) {
                standaloneTools.push(tool);
                return;
            }
            addToolsToTurnMap(toolsByAssistantTurn, turnId, [tool]);
        });
    });

    const grouped = [];
    const embeddedTurns = new Set();
    messages.forEach(message => {
        if (!message) return;
        if (message.role === 'tool' || message.role === 'tool_group') {
            return;
        }
        if (message.role === 'assistant') {
            const turnId = assistantTurnChatId(message);
            if (turnId != null && toolsByAssistantTurn.has(turnId)) {
                grouped.push({
                    ...message,
                    embedded_tools: toolsByAssistantTurn.get(turnId)
                });
                embeddedTurns.add(turnId);
                return;
            }
        }
        grouped.push(message);
    });

    const looseGroups = [];
    toolsByAssistantTurn.forEach((tools, turnId) => {
        if (!embeddedTurns.has(turnId) && tools.length) {
            for (let i = 0; i < tools.length; i += MAX_ACTIVITY_STEPS_PER_GROUP) {
                looseGroups.push(toolGroupFromTools(tools.slice(i, i + MAX_ACTIVITY_STEPS_PER_GROUP), turnId, {
                    assistant_turn_activity: true
                }));
            }
        }
    });
    looseGroups.push(...buildStandaloneToolGroups(standaloneTools));
    return sortMessagesForDisplay([...grouped, ...looseGroups]);
}

function messagesAreChronological(messages) {
    if (!Array.isArray(messages) || messages.length < 2) return true;
    for (let i = 1; i < messages.length; i++) {
        if (compareMessageOrder(messages[i], messages[i - 1]) < 0) return false;
    }
    return true;
}

function renderMessages(messages, preserveOnEmpty) {
    messages = normalizeTraceMessages(sortMessagesForDisplay(normalizeReadAloudAssistantMessages(messages || [])));
    if (_messagesEndWithAssistant(messages)) {
        const liveState = currentChatId != null ? _getLiveChatState(currentChatId) : null;
        if (liveState && liveState.assistantStreaming) {
            _clearLiveChatState(currentChatId);
        }
        if (streamingChatId === currentChatId) streamingChatId = null;
        if (isStreaming && !streamingChatId) {
            isStreaming = false;
            setSendButtonStreaming(false);
        }
    }
    const preserveLiveUi = _hasActiveAssistantStreamForCurrentChat();
    const pendingLiveTranscriptionPlain = _pendingLiveTranscriptionPlainForCurrentChat();
    const persistedLiveTranscriptionArrived = _messagesContainUserPlain(messages, pendingLiveTranscriptionPlain);
    const preserveLiveTranscriptionUi = Boolean(pendingLiveTranscriptionPlain && !persistedLiveTranscriptionArrived);
    const preservedTranscriptionEl = preserveLiveTranscriptionUi
        ? document.getElementById('transcriptionStatus')
        : null;
    if (persistedLiveTranscriptionArrived) {
        _acknowledgePersistedLiveUser(currentChatId, pendingLiveTranscriptionPlain);
    }
    if (!preserveLiveUi) {
        removeTypingIndicator();
        const streamingEl = document.getElementById('streamingAssistantMessage');
        if (streamingEl) streamingEl.remove();
        const streamingActivityEl = document.getElementById('streamingAssistantActivity');
        if (streamingActivityEl) streamingActivityEl.remove();
    }
    // Chat refreshes can race the STT preview event. Keep a real pending
    // transcript visible until message_added / clear_live_preview replaces it.
    if (!preserveLiveTranscriptionUi) {
        _discardLiveTranscriptionUi();
    }

    if (messages.length === 0) {
        if (preserveOnEmpty && chatMessages.children.length > 0) return;
        chatMessages.innerHTML = '';
        _renderedMessageCount = 0;
        _optimisticUserMessages.clear();
        _lastStreamFinalizedPlain = null;
        return;
    }
    // Incremental render: only append new messages instead of wiping the whole DOM.
    // When switching chats, loadChat resets _renderedMessageCount but old rows may still
    // be in the DOM — compare against live DOM count, not only _renderedMessageCount.
    const domMessageCount = countPersistedDomMessages();
    const switchingChat = messages.length < domMessageCount || messages.length < _renderedMessageCount;
    const orderRegressed = !messagesAreChronological(messages);
    if (!switchingChat && messages.length > _renderedMessageCount && messages.length - _renderedMessageCount <= 10 && !orderRegressed) {
        // Fast path: append only new messages, skipping ones already rendered optimistically
        for (let i = _renderedMessageCount; i < messages.length; i++) {
            const msg = messages[i];
            // Skip user messages already on screen (typed, dictated, or optimistic).
            if (msg.role === 'user' && msg.content) {
                const userPlain = _normalizeMsgPlain(msg.content);
                const sameRowRendered = msg.chat_row_id != null && findLiveTurnAnchor(msg.chat_row_id);
                if (
                    sameRowRendered
                    || (_hasRecentOptimisticUserMessage(msg.content.substring(0, 100))
                        && (hasOpenUserTurnPlain(userPlain) || hasRenderedUserMessagePlain(userPlain)))
                ) {
                    continue;
                }
            }
            // Duplicate assistant rows (same body back-to-back) — DB/pipeline race with PTT/voice
            if (msg.role === 'assistant' && i > 0 && messages[i - 1].role === 'assistant') {
                const a = _normalizeMsgPlain(messages[i - 1].content);
                const b = _normalizeMsgPlain(msg.content);
                if (a && a === b) {
                    continue;
                }
            }
            if (msg.role === 'assistant' && msg.content) {
                const assistantPlain = _normalizeMsgPlain(msg.content);
                if (assistantPlain && hasRenderedMessagePlain('assistant', assistantPlain)) {
                    continue;
                }
            }
            const messageDiv = createMessageElement(msg);
            if (messageDiv) {
                chatMessages.appendChild(messageDiv);
                finalizeMessageElementMount(messageDiv, msg);
            }
        }
        _renderedMessageCount = messages.length;
        if (preservedTranscriptionEl && preservedTranscriptionEl.parentNode !== chatMessages) {
            chatMessages.appendChild(preservedTranscriptionEl);
            _ensureTranscriptionPreviewPlacement();
        }
        scrollToBottom();
        return;
    }
    // Full rebuild (chat switch, refresh, etc.)
    if (preserveLiveUi) return;
    chatMessages.innerHTML = '';
    _optimisticUserMessages.clear();
    _lastStreamFinalizedPlain = null;
    const toRender = dedupeConsecutiveDuplicateAssistants(messages);
    toRender.forEach(message => {
        const messageDiv = createMessageElement(message);
        if (messageDiv) {
            chatMessages.appendChild(messageDiv);
            finalizeMessageElementMount(messageDiv, message);
        }
    });
    if (preservedTranscriptionEl) {
        chatMessages.appendChild(preservedTranscriptionEl);
        _ensureTranscriptionPreviewPlacement();
    }
    _renderedMessageCount = toRender.length;
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
        const normalized = normalizeUtcTimestampInput(ts);
        if (normalized === null) return '';
        const d = new Date(typeof normalized === 'number' ? normalized : normalized);
        if (isNaN(d.getTime())) return '';
        const h = d.getHours();
        const m = d.getMinutes().toString().padStart(2, '0');
        const ampm = h >= 12 ? 'pm' : 'am';
        return `${h % 12 || 12}:${m} ${ampm}`;
    } catch (_) { return ''; }
}

function chatDividerParts(divider) {
    const d = divider || {};
    if (d.type === 'fork') {
        const title = (d.source_title || '').trim() || (d.source_chat_id ? `Chat #${d.source_chat_id}` : 'previous chat');
        return {
            label: `Forked from ${title}`,
            note: d.compacted !== false ? 'compacted' : '',
        };
    }
    if (d.reason === 'auto') {
        return { label: 'Context compacted automatically', note: '' };
    }
    return { label: 'Context compacted', note: '' };
}

function createChatDividerElement(message) {
    const divider = (message && message.divider) || {};
    let tsRaw = message.timestamp;
    if (tsRaw === undefined || tsRaw === null || tsRaw === '') tsRaw = Date.now();
    const ts = _formatTimestamp(tsRaw);
    const { label, note } = chatDividerParts(divider);
    const div = document.createElement('div');
    div.className = `chat-divider chat-divider--${escapeHtml(divider.type || 'compact')}`;
    div.setAttribute('role', 'separator');
    div.setAttribute('data-speakable', 'false');
    if (message.chat_row_id != null && message.chat_row_id !== '') {
        div.dataset.turnChatId = String(message.chat_row_id);
    }
    if (divider.type === 'fork' && divider.source_chat_id) {
        div.dataset.sourceChatId = String(divider.source_chat_id);
    }
    div.dataset.messageTs = String(messageTimestampMs({ timestamp: tsRaw }));
    div.innerHTML = `
        <span class="chat-divider-line" aria-hidden="true"></span>
        <div class="chat-divider-body">
            ${ts ? `<span class="chat-divider-meta chat-divider-time">${ts}</span>` : ''}
            <span class="chat-divider-label">${escapeHtml(label)}</span>
            ${note ? `<span class="chat-divider-note">${escapeHtml(note)}</span>` : ''}
        </div>
        <span class="chat-divider-line" aria-hidden="true"></span>
    `;
    return div;
}

function createMessageElement(message) {
    if (message && message.role === 'divider') {
        return createChatDividerElement(message);
    }
    if (message && message.role === 'tool_group') {
        return createToolExecutionGroupElement(message);
    }
    if (message && message.role === 'tool') {
        return createToolExecutionGroupElement({ role: 'tool_group', timestamp: message.timestamp, tools: [message] });
    }
    if (message && message.role === 'workflow') {
        if (isHiddenWorkflowEvent(message.workflow_event)) return null;
        if (hasRenderedWorkflowEvent(message.workflow_event)) return null;
        return createWorkflowEventElement(message);
    }
    const div = document.createElement('div');
    div.className = `message ${message.role}`;
    if (message.chat_row_id != null && message.chat_row_id !== '') {
        div.dataset.turnChatId = String(message.chat_row_id);
    }
    const avatarSvg = message.role === 'user' ? AVATAR_SVG_USER : AVATAR_SVG_ASSISTANT;
    const actionsHtml = message.role === 'assistant'
        ? `<button class="message-action-btn" onclick="copyMessage(this)">Copy</button>
           <button class="message-action-btn message-action-play" onclick="playTTS(this)" title="Play with TTS">Play</button>`
        : `<button class="message-action-btn" onclick="copyMessage(this)">Copy</button>`;

    let tsRaw = message.timestamp;
    if (tsRaw === undefined || tsRaw === null || tsRaw === '') {
        tsRaw = Date.now();
    }
    div.dataset.messageTs = String(messageTimestampMs({ timestamp: tsRaw }));
    const ts = _formatTimestamp(tsRaw);
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

function finalizeMessageElementMount(div, message) {
    if (!div || !message || message.role !== 'assistant') return;
    if (Array.isArray(message.embedded_tools) && message.embedded_tools.length) {
        appendAssistantActivity(div, message.embedded_tools);
    }
}

function bindActivityToggleHandlers() {
    if (!chatMessages || chatMessages.dataset.activityToggleBound === '1') return;
    chatMessages.dataset.activityToggleBound = '1';
    chatMessages.addEventListener('click', (e) => {
        const toggle = e.target.closest('.activity-step-toggle');
        if (!toggle) return;
        e.preventDefault();
        const step = toggle.closest('.activity-step');
        if (!step) return;
        const expanded = !step.classList.contains('activity-step--expanded');
        step.classList.toggle('activity-step--expanded', expanded);
        step.classList.toggle('activity-step--collapsed', !expanded);
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        const body = step.querySelector('.activity-step-body');
        if (body) body.hidden = !expanded;
        syncActivityMessageAlignment(step);
    });
}

function syncActivityMessageAlignment(step) {
    const message = step && step.closest ? step.closest('.message.activity') : null;
    if (!message) return;
    const anyExpanded = Boolean(message.querySelector('.activity-step--expanded'));
    message.classList.toggle('activity--expanded', anyExpanded);
}

function createActivityMessageElement({ innerHtml, extraClass = '' }) {
    const div = document.createElement('div');
    div.className = `message activity${extraClass ? ` ${extraClass}` : ''}`;
    div.setAttribute('data-speakable', 'false');
    div.innerHTML = `
        <div class="message-avatar activity-avatar">${AVATAR_SVG_ACTIVITY}</div>
        <div class="message-content activity-content">
            ${innerHtml}
        </div>
    `;
    return div;
}

function assistantActivityTurnKey(assistantEl) {
    if (!assistantEl) return null;
    const turnId = assistantEl.dataset && assistantEl.dataset.turnChatId;
    return turnId != null && turnId !== '' ? String(turnId) : null;
}

function findAssistantActivitySibling(assistantEl) {
    if (!assistantEl || !chatMessages) return null;
    if (assistantEl.id === 'streamingAssistantMessage') {
        const streaming = document.getElementById('streamingAssistantActivity');
        if (streaming) return streaming;
    }
    const prev = assistantEl.previousElementSibling;
    if (prev && prev.classList.contains('message') && prev.classList.contains('activity') && prev.classList.contains('assistant-turn-activity')) {
        const key = assistantActivityTurnKey(assistantEl);
        if (key == null || prev.dataset.turnChatId === key) return prev;
    }
    const key = assistantActivityTurnKey(assistantEl);
    if (key != null) {
        const match = [...chatMessages.querySelectorAll('.message.activity.assistant-turn-activity')]
            .find(el => el.dataset.turnChatId === key);
        if (match) return match;
    }
    return null;
}

function insertDomNodeAfter(newNode, referenceNode) {
    if (!referenceNode || !referenceNode.parentNode) return;
    referenceNode.parentNode.insertBefore(newNode, referenceNode.nextSibling);
}

function findAssistantTurnActivityBlocks(assistantEl) {
    if (!assistantEl || !chatMessages) return [];
    const key = assistantActivityTurnKey(assistantEl);
    if (key == null) return [];
    return [...chatMessages.querySelectorAll('.message.activity.assistant-turn-activity')]
        .filter(el => el.dataset.turnChatId === key);
}

function getOrCreateAssistantActivitySibling(assistantEl) {
    const blocks = findAssistantTurnActivityBlocks(assistantEl);
    const last = blocks.length ? blocks[blocks.length - 1] : null;
    if (last && last.querySelectorAll('.activity-step').length < MAX_ACTIVITY_STEPS_PER_GROUP) {
        return last;
    }
    const sibling = createToolExecutionGroupElement({
        role: 'tool_group',
        timestamp: Date.now(),
        turn_chat_id: assistantActivityTurnKey(assistantEl),
        tools: [],
        assistant_turn_activity: true
    });
    if (assistantEl.id === 'streamingAssistantMessage' && !blocks.length) {
        sibling.id = 'streamingAssistantActivity';
    }
    if (assistantEl.parentNode === chatMessages) {
        if (!blocks.length) {
            chatMessages.insertBefore(sibling, assistantEl);
        } else {
            insertDomNodeAfter(sibling, blocks[blocks.length - 1]);
        }
    }
    return sibling;
}

function appendAssistantActivity(assistantEl, tools) {
    if (!assistantEl || !Array.isArray(tools) || !tools.length || !chatMessages) return;
    tools.forEach(tool => {
        if (isStandaloneSystemActivity(tool)) {
            appendStandaloneToolActivity(tool);
            return;
        }
        const sibling = getOrCreateAssistantActivitySibling(assistantEl);
        const list = sibling.querySelector('.activity-steps');
        if (list) list.insertAdjacentHTML('beforeend', toolExecutionItemHtml(tool));
        sibling.querySelectorAll('.activity-step').forEach(syncActivityMessageAlignment);
    });
    assistantEl.classList.add('assistant--with-activity');
    scrollToBottom();
}

function extractAssistantActivityForPreserve(assistantEl) {
    const sibling = findAssistantActivitySibling(assistantEl);
    return sibling ? sibling.outerHTML : '';
}

function restoreAssistantEmbeddedActivity(assistantEl, activityHtml) {
    if (!assistantEl || !activityHtml || !chatMessages) return;
    const existing = findAssistantActivitySibling(assistantEl);
    if (existing) existing.remove();
    assistantEl.insertAdjacentHTML('beforebegin', activityHtml);
    const restored = findAssistantActivitySibling(assistantEl);
    if (restored && restored.id === 'streamingAssistantActivity') {
        restored.id = '';
    }
    assistantEl.classList.add('assistant--with-activity');
}

function activityCollapsedActionLabel(title, event) {
    let t = String(title || '').trim();
    if (!t) {
        const tool = String((event && event.tool_name) || 'tool').replace(/_/g, ' ').trim();
        return tool ? tool.charAt(0).toUpperCase() + tool.slice(1) : 'Activity';
    }
    t = t.replace(/^Executed\s+/i, '').trim();
    if (/^sent ticket\b/i.test(t)) return 'Sent ticket';
    if (/^screenshot/i.test(t)) return 'Screenshot Analyzer';
    if (/^type text\b/i.test(t)) return 'Type Text';
    if (/^ran helper code/i.test(t)) return 'Ran helper code';
    if (/^inspected files/i.test(t)) return 'Inspected files';
    if (/^checked mode control/i.test(t)) return 'Checked mode control';
    const bracket = t.match(/^\[([^\]]+)\]/);
    if (bracket) {
        const inner = bracket[1].replace(/_/g, ' ').trim();
        if (inner) return inner.charAt(0).toUpperCase() + inner.slice(1);
    }
    const beforeQuote = t.split(/["']/)[0].trim().replace(/[.:]+$/, '');
    if (beforeQuote && beforeQuote.length < t.length && beforeQuote.length <= 48) {
        return beforeQuote;
    }
    if (t.length <= 48) return t;
    return t.split(/\s+/).slice(0, 4).join(' ');
}

function activityStepLabel(title, event) {
    const toolName = (event && event.tool_name) || '';
    if (toolName === 'chat_settings') {
        const detail = String((event.result_detail || event.result_summary || '')).trim();
        const firstLine = detail.split('\n').map(line => line.trim()).find(Boolean);
        if (firstLine) return firstLine;
    }
    if (toolName === 'read_aloud') {
        return String((event && event.title) || title || 'Read aloud').trim();
    }
    const short = activityCollapsedActionLabel(title, event);
    return String(short || '').replace(/^system activity:\s*/i, '').trim() || 'Activity';
}

function buildActivityCollapsibleStep({ timestamp, label, bodyHtml, safeStatus = 'completed', extraClass = '', toolEventId = '' }) {
    const ts = _formatTimestamp(timestamp || Date.now());
    const eventIdAttr = toolEventId ? ` data-tool-event-id="${escapeHtml(toolEventId)}"` : '';
    return `
        <div class="activity-step activity-step--collapsed activity-step--${safeStatus}${extraClass ? ` ${extraClass}` : ''}"${eventIdAttr}>
            <button type="button" class="activity-step-toggle" aria-expanded="false">
                <span class="activity-step-time">${ts}</span>
                <span class="activity-step-label">${escapeHtml(label)}</span>
                <svg class="activity-step-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            <div class="activity-step-body" hidden>${bodyHtml}</div>
        </div>
    `;
}

function createWorkflowEventElement(message) {
    const event = (message && message.workflow_event) || {};
    const status = String(event.status || 'running').toLowerCase();
    const type = String(event.type || 'event').replace(/_/g, ' ');
    const safeStatus = ['running', 'waiting', 'completed', 'failed', 'cancelled', 'passed'].includes(status) ? status : 'running';
    const workflowName = event.workflow_name || 'Workflow';
    const stepName = event.step_name || '';
    const summary = event.summary || message.content || '';
    const phase = event.phase || '';

    const meta = [];
    if (phase) meta.push(`<span class="activity-meta-chip">${escapeHtml(phase)}</span>`);
    if (stepName) meta.push(`<span class="activity-meta-chip">${escapeHtml(stepName)}</span>`);
    const metaHtml = meta.length ? `<div class="activity-meta">${meta.join('')}</div>` : '';
    const statusHtml = safeStatus !== 'completed' && safeStatus !== 'passed'
        ? `<span class="activity-status activity-status--${safeStatus}">${escapeHtml(safeStatus)}</span>`
        : '';

    const bodyHtml = `
        ${statusHtml ? `<div class="activity-step-meta-row">${statusHtml}</div>` : ''}
        <div class="activity-step-kind">${escapeHtml(type)}</div>
        ${summary ? `<div class="activity-step-detail">${formatMessage(summary)}</div>` : ''}
        ${metaHtml}
    `;
    const innerHtml = `<div class="activity-steps">${buildActivityCollapsibleStep({
        timestamp: message.timestamp,
        label: activityStepLabel(workflowName, { tool_name: 'workflow' }),
        bodyHtml,
        safeStatus,
        extraClass: 'workflow-event'
    })}</div>`;
    const el = createActivityMessageElement({
        innerHtml,
        extraClass: `workflow-event workflow-event--${safeStatus}`
    });
    const domKey = workflowEventDomKey(event);
    if (domKey) el.dataset.workflowEventKey = domKey;
    const step = el.querySelector('.activity-step');
    if (step) syncActivityMessageAlignment(step);
    return el;
}

function getLateToolInsertAnchor() {
    if (!chatMessages) return null;
    const nodes = [...chatMessages.children].filter(el =>
        el.classList && el.classList.contains('message') && el.id !== 'transcriptionStatus'
    );
    const last = nodes[nodes.length - 1];
    if (last && last.classList.contains('assistant')) {
        return last;
    }
    return null;
}

function toolStatusLabel(status) {
    const s = String(status || 'completed').toLowerCase();
    if (s === 'completed' || s === 'passed') return '';
    if (s === 'failed') return 'Failed';
    return s.charAt(0).toUpperCase() + s.slice(1);
}

function toolDisplayTitle(event) {
    const toolName = event.tool_name || 'tool';
    const raw = event.title || toolName.replace(/_/g, ' ');
    return String(raw)
        .replace(/^Executed\s+/i, '')
        .replace(/_/g, ' ')
        .trim();
}

function normalizedTraceText(value) {
    return String(value || '')
        .replace(/\s+/g, ' ')
        .replace(/[.。]+$/g, '')
        .trim()
        .toLowerCase();
}

function toolExecutionItemHtml(message) {
    const event = (message && message.tool_event) || {};
    const status = String(event.status || 'completed').toLowerCase();
    const safeStatus = ['completed', 'failed', 'running', 'cancelled', 'skipped', 'passed'].includes(status) ? status : 'completed';
    const title = toolDisplayTitle(event);
    const detail = (event.result_detail || event.result_summary || message.content || '').trim();
    const compact = Boolean(event.compact);
    const activityStyle = event.activity_style === 'passive' ? 'passive' : 'active';
    const statusLabel = toolStatusLabel(safeStatus);
    const statusHtml = statusLabel ? `<span class="activity-status activity-status--${safeStatus}">${escapeHtml(statusLabel)}</span>` : '';
    const meta = [];
    if (event.routing_path) meta.push(`<span class="activity-meta-chip">${escapeHtml(event.routing_path)}</span>`);
    const collapsedAction = activityCollapsedActionLabel(title, event);
    const fullAction = String(title || '').replace(/^Executed\s+/i, '').trim();
    const titleHtml = fullAction && fullAction !== collapsedAction
        ? `<div class="activity-step-detail activity-step-detail--title">${formatMessage(fullAction)}</div>`
        : '';
    const bodyHtml = `
        ${statusHtml ? `<div class="activity-step-meta-row">${statusHtml}</div>` : ''}
        ${titleHtml}
        <div class="activity-step-detail${compact ? ' activity-step-detail--muted' : ''}">${formatMessage(detail && detail !== fullAction ? detail : (detail || title))}</div>
        ${meta.length ? `<div class="activity-meta">${meta.join('')}</div>` : ''}
    `;
    return buildActivityCollapsibleStep({
        timestamp: message.timestamp,
        label: activityStepLabel(title, event),
        bodyHtml,
        safeStatus,
        extraClass: `${compact ? 'activity-step--compact ' : ''}activity-step--${activityStyle}`.trim(),
        toolEventId: message.tool_event_id || event.event_id || ''
    });
}

function createToolExecutionGroupElement(message) {
    const tools = Array.isArray(message.tools) ? message.tools : [];
    const extraClasses = ['tool-execution-group'];
    if (message.assistant_turn_activity) extraClasses.push('assistant-turn-activity');
    const innerHtml = `<div class="activity-steps">${tools.map(toolExecutionItemHtml).join('')}</div>`;
    const el = createActivityMessageElement({
        innerHtml,
        extraClass: extraClasses.join(' ')
    });
    const turnChatId = message.turn_chat_id || (tools[0] && tools[0].turn_chat_id);
    if (turnChatId != null && turnChatId !== '') {
        el.dataset.turnChatId = String(turnChatId);
    }
    el.querySelectorAll('.activity-step').forEach(syncActivityMessageAlignment);
    return el;
}

function createToolExecutionElement(message) {
    return createToolExecutionGroupElement({
        role: 'tool_group',
        timestamp: message.timestamp,
        tools: [message]
    });
}

function formatMessage(text) {
    if (!text) return '';
    
    // Escape HTML
    let html = escapeHtml(text);
    
    // Mermaid diagrams — preview + open in freestanding viewer
    html = html.replace(/```mermaid\s*\n([\s\S]*?)```/g, (match, code) => {
        const trimmed = code.trim();
        const encoded = encodeURIComponent(trimmed);
        return (
            '<div class="mermaid-inline-block" data-mermaid-code="' + encoded + '">' +
            '<div class="mermaid-inline-header">' +
            '<span>Mermaid diagram</span>' +
            '<button type="button" class="mermaid-inline-open" data-mermaid-open>Open diagram</button>' +
            '</div>' +
            '<div class="mermaid-inline-preview"><pre><code>' + escapeHtml(trimmed) + '</code></pre></div>' +
            '</div>'
        );
    });
    
    // Format code blocks (non-mermaid)
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
    if (!loadedChatId || loadedChatId !== currentChatId) {
        if (currentChatId) {
            isStreaming = false;
            sendButton.disabled = true;
            setViewOnlyChrome(true);
            alert('Please load a chat to reply.');
            return;
        }
        let provider;
        let modelName;
        let voiceProvider;
        let voiceModel;
        const ep = document.getElementById('emptyStateLlmProvider');
        if (ep) {
            provider = document.getElementById('emptyStateLlmProvider')?.value?.trim();
            modelName = document.getElementById('emptyStateLlmModel')?.value?.trim();
            voiceProvider = document.getElementById('emptyStateVoiceProvider')?.value?.trim();
            voiceModel = document.getElementById('emptyStateVoiceModel')?.value?.trim();
        } else {
            const defaults = await fetchDefaultsForNewChat();
            provider = defaults.provider;
            modelName = defaults.model_name;
            voiceProvider = defaults.voice_provider;
            voiceModel = defaults.voice_model;
        }
        if (!provider || !modelName) {
            isStreaming = false;
            sendButton.disabled = !messageInput.value.trim();
            alert('Please select an LLM provider and model in Chat settings, or set defaults in Settings > LLMs.');
            return;
        }
        if (!voiceProvider || !voiceModel) {
            isStreaming = false;
            sendButton.disabled = !messageInput.value.trim();
            alert('Please select a voice provider and voice in Chat settings, or set defaults in Settings > General.');
            return;
        }
        showEmptyStateAgentLoading();
        const savedPlaceholder = messageInput.placeholder;
        messageInput.placeholder = 'Starting chat...';
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
            persistGlobalVoiceToSettings(voiceProvider, voiceModel);
            newChatId = data.id;
            loadedChatId = data.id;
            currentChatId = data.id;
            if (typeof applySelectedSkin === 'function') applySelectedSkin();
            updateActiveChat();
            syncKanbanSourceChatContext();
            // Clear input immediately so user sees it sent
            messageInput.value = '';
            messageInput.style.height = 'auto';
            showChatView(true);
            // Add user message to UI while waiting for agent
            const userMsg = createMessageElement({ role: 'user', content: message });
            chatMessages.appendChild(userMsg);
            _addOptimisticUserMessage(message.substring(0, 100));
            _lastStreamFinalizedPlain = null;
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
    _addOptimisticUserMessage(message.substring(0, 100));
    _lastStreamFinalizedPlain = null;
    chatMessages.appendChild(createTypingIndicator());
    scrollToBottom();
    scheduleRefreshChatHeaderStats();
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
    // New chat from modal: POST /chats already emitted web_create_chat_emits_requested, which runs
    // current_chat_changed(initial_message) on the agent. Do NOT call load-in-agent here — that
    // path sends interrupt_tts first and cancels the in-flight LLM/TTS for the starting question.
    if (loadedChatSkippedLoadInAgent && targetChatId) {
        loadedChatSkippedLoadInAgent = false;
        agentCurrentChatId = targetChatId;
        updateActiveChat();
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
        _clearAgentStreamWaiter(myChatId, resolveStream);
        removeTypingIndicator();
        if (streamingChatId === myChatId) streamingChatId = null;
    };

    let resolveStream;
    return new Promise((resolve, reject) => {
        const finish = () => { done(); resolve(); };
        resolveStream = finish;
        _setAgentStreamWaiter(myChatId, myToken, finish);

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
                // WebSocket stream_finished may have finalized the assistant row already; don't append again.
                const domN = countPersistedDomMessages();
                const lastPlain = _normalizeMsgPlain(messages[messages.length - 1].content || '');
                const assistantRendered = !lastPlain || hasRenderedMessagePlain('assistant', lastPlain);
                if (domN >= messages.length && assistantRendered) {
                    finish();
                    if (currentChatId === myChatId) {
                        syncRenderedMessageCountFromDom();
                        updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
                        scrollToBottom();
                    }
                    return;
                }
                finish();
                if (currentChatId === myChatId) {
                    if (!assistantRendered) {
                        renderMessages(messages, false);
                    } else {
                        syncRenderedMessageCountFromDom();
                    }
                    updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
                    scrollToBottom();
                }
            } else if (has_new && streamingChatId !== myChatId && currentChatId === myChatId) {
                renderMessages(messages, false);
                updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
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
                const lastAssistant = msgs.filter(m => m.role === 'assistant').pop();
                const lastPlain = lastAssistant ? _normalizeMsgPlain(lastAssistant.content) : '';
                if (lastPlain && !hasRenderedMessagePlain('assistant', lastPlain)) {
                    renderMessages(msgs, false);
                    updateChatSettingsDisplay({ title: d.title || 'New Chat', provider: d.provider || '-', model_name: d.model_name || '-', voice_provider: d.voice_provider, voice_model: d.voice_model, voice_model_display: d.voice_model_display, context_stats: d.context_stats, compact_checkpoint: d.compact_checkpoint });
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
        _clearAgentStreamWaiter(myChatId, resolveStream);
        removeTypingIndicator();
        if (streamingChatId === myChatId) streamingChatId = null;
    };

    let resolveStream;
    return new Promise((resolve, reject) => {
        const finish = () => { done(); resolve(); };
        resolveStream = finish;
        _setAgentStreamWaiter(myChatId, myToken, finish);

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
                const domN = countPersistedDomMessages();
                const lastPlain = _normalizeMsgPlain(messages[messages.length - 1].content || '');
                const assistantRendered = !lastPlain || hasRenderedMessagePlain('assistant', lastPlain);
                if (domN >= messages.length && assistantRendered) {
                    finish();
                    if (currentChatId === myChatId) {
                        syncRenderedMessageCountFromDom();
                        updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
                        scrollToBottom();
                    }
                    return;
                }
                finish();
                if (currentChatId === myChatId) {
                    if (!assistantRendered) {
                        renderMessages(messages, false);
                    } else {
                        syncRenderedMessageCountFromDom();
                    }
                    updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
                    scrollToBottom();
                }
            } else if (has_new && streamingChatId !== myChatId && currentChatId === myChatId) {
                renderMessages(messages, false);
                updateChatSettingsDisplay({ title: data.title || 'New Chat', provider: data.provider || '-', model_name: data.model_name || '-', voice_provider: data.voice_provider, voice_model: data.voice_model, voice_model_display: data.voice_model_display, context_stats: data.context_stats, compact_checkpoint: data.compact_checkpoint });
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
                const lastAssistant = msgs.filter(m => m.role === 'assistant').pop();
                const lastPlain = lastAssistant ? _normalizeMsgPlain(lastAssistant.content) : '';
                if (lastPlain && !hasRenderedMessagePlain('assistant', lastPlain)) {
                    renderMessages(msgs, false);
                    updateChatSettingsDisplay({ title: d.title || 'New Chat', provider: d.provider || '-', model_name: d.model_name || '-', voice_provider: d.voice_provider, voice_model: d.voice_model, voice_model_display: d.voice_model_display, context_stats: d.context_stats, compact_checkpoint: d.compact_checkpoint });
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
    div.className = 'message assistant message-waiting';
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
    const confirmed = await window.DecisionsAPI.confirm({
        title: "Delete chat",
        message: "Delete this chat? This cannot be undone.",
        confirmLabel: "Delete",
        danger: true,
    });
    if (!confirmed) {
        return;
    }
    const fallbackChatId = getAdjacentChatIdAfterDelete(chatId);
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
            const remainingIds = data.chats.map((c) => c.id);
            const nextId = (fallbackChatId != null && remainingIds.includes(fallbackChatId))
                ? fallbackChatId
                : data.chats[0].id;
            await selectChat(nextId);
        } else {
            showEmptyState();
            await saveLastChatId(null);
        }
        updateActiveChat();
        updateLoadButtonVisibility();
        syncKanbanSourceChatContext();
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
        if (titleEl) {
            titleEl.textContent = displayTitle;
            applyChatTitleMarqueeForWrap(titleEl.closest('.chat-item-title-wrap'));
        }
        if (currentChatId === chatId) {
            const headerTitle = document.getElementById('chatTitle');
            if (headerTitle) {
                headerTitle.textContent = displayTitle;
                headerTitle.title = displayTitle;
                requestAnimationFrame(applyHeaderTitleMarquee);
            }
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
const AVATAR_SVG_ACTIVITY = '<svg class="avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>';

// Utility Functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Load LLM models for provider (settings API)
async function loadLlmModelsInto(modelSelectEl, provider, preferredModel) {
    if (!modelSelectEl) return;
    const providerId = providerIdFromDisplay(provider || '');
    modelSelectEl.innerHTML = '<option value="">Loading...</option>';
    try {
        const response = await fetch(`/api/llms/models?type=conversational&provider=${encodeURIComponent(providerId)}`);
        const data = await response.json();
        const models = data.models || [];
        const preferred = (preferredModel || '').trim();
        let options = models.map(m => ({
            id: String(m.id || m || '').trim(),
            name: String(m.name || m.id || m || '').trim()
        })).filter(m => m.id);
        if (preferred && !options.some(m => m.id === preferred)) {
            options.unshift({ id: preferred, name: preferred });
        }
        modelSelectEl.innerHTML = options.length
            ? options.map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`).join('')
            : (preferred
                ? `<option value="${escapeHtml(preferred)}">${escapeHtml(preferred)}</option>`
                : '<option value="">No models</option>');
        if (preferred) {
            const opt = Array.from(modelSelectEl.options).find(o => o.value === preferred);
            if (opt) modelSelectEl.value = preferred;
        }
        if (!modelSelectEl.value && modelSelectEl.options[0] && modelSelectEl.options[0].value) {
            modelSelectEl.selectedIndex = 0;
        }
    } catch (e) {
        console.error('Error loading LLM models:', e);
        modelSelectEl.innerHTML = '<option value="">Error loading</option>';
    }
}

async function loadLlmModels(provider) {
    await loadLlmModelsInto(llmModelSelect, provider);
}

// Voice sample playback — shared by new-chat modal, empty state, and Configure Chat modal.
const _CHAT_VOICE_CONTEXTS = {
    emptyState: { prefix: 'emptyState', providerId: 'emptyStateVoiceProvider', modelId: 'emptyStateVoiceModel' },
    modal: { prefix: 'modal', providerId: 'voiceProvider', modelId: 'voiceModel' },
    chatConfig: { prefix: 'chatConfig', providerId: 'chatConfigVoiceProvider', modelId: 'chatConfigVoiceModel' },
};
const _chatVoicePlaybackByContext = {};

function getChatVoiceContextMeta(context) {
    return _CHAT_VOICE_CONTEXTS[context] || null;
}

function getChatVoiceProviderEl(context) {
    const meta = getChatVoiceContextMeta(context);
    return meta ? document.getElementById(meta.providerId) : null;
}

function getChatVoiceModelEl(context) {
    const meta = getChatVoiceContextMeta(context);
    return meta ? document.getElementById(meta.modelId) : null;
}

async function reloadChatVoiceModels(context, provider, preferredVoice) {
    const modelEl = getChatVoiceModelEl(context);
    if (!modelEl) return;
    if (context === 'emptyState') {
        await loadEmptyStateVoiceModels(provider);
        if (preferredVoice) selectVoiceModelOption(modelEl, preferredVoice);
        return;
    }
    if (context === 'modal') {
        await loadVoiceModels(provider);
        if (preferredVoice) selectVoiceModelOption(modelEl, preferredVoice);
        return;
    }
    await loadVoiceModelsInto(modelEl, provider, preferredVoice);
}

function setChatVoicePlayUI(context, state) {
    const meta = getChatVoiceContextMeta(context);
    if (!meta) return;
    const prefix = meta.prefix;
    const playIcon = document.getElementById(prefix + '_play_icon');
    const stopIcon = document.getElementById(prefix + '_stop_icon');
    const spinner = document.getElementById(prefix + '_play_spinner');
    const btn = document.getElementById(prefix + 'PlayVoiceBtn');
    if (playIcon) playIcon.style.display = state === 'idle' ? '' : 'none';
    if (stopIcon) stopIcon.style.display = state === 'playing' ? '' : 'none';
    if (spinner) spinner.style.display = state === 'loading' ? '' : 'none';
    if (btn) btn.disabled = (state === 'loading');
}

function stopVoiceForContext(context) {
    const slot = _chatVoicePlaybackByContext[context] || {};
    slot.loading = false;
    if (slot.audio) {
        slot.audio.pause();
        slot.audio.currentTime = 0;
        slot.audio = null;
    }
    _chatVoicePlaybackByContext[context] = slot;
    setChatVoicePlayUI(context, 'idle');
}

function stopAllChatVoicePreviews(exceptContext) {
    Object.keys(_CHAT_VOICE_CONTEXTS).forEach((ctx) => {
        if (ctx !== exceptContext) stopVoiceForContext(ctx);
    });
}

async function playVoiceForContext(context) {
    const slot = _chatVoicePlaybackByContext[context] || {};
    if (slot.loading) return;
    if (slot.audio && !slot.audio.paused) {
        stopVoiceForContext(context);
        return;
    }
    const providerEl = getChatVoiceProviderEl(context);
    const modelEl = getChatVoiceModelEl(context);
    const provider = (providerEl?.value || '').trim();
    const voice = (modelEl?.value || '').trim();
    if (!provider || !voice) return;
    const voiceName = modelEl?.options[modelEl.selectedIndex] ? modelEl.options[modelEl.selectedIndex].text : voice;

    stopAllChatVoicePreviews(context);
    stopVoiceForContext(context);
    slot.loading = true;
    _chatVoicePlaybackByContext[context] = slot;
    setChatVoicePlayUI(context, 'loading');

    try {
        const headers = { 'Content-Type': 'application/json' };
        const token = typeof window !== 'undefined' && window.DECISIONSAI_INTERNAL_API_TOKEN;
        if (token) headers['X-DecisionsAI-Internal-Token'] = token;
        const response = await fetch('/api/play-voice?_=' + Date.now(), {
            method: 'POST',
            cache: 'no-store',
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
        const audio = new Audio(url);
        slot.audio = audio;
        slot.loading = false;
        _chatVoicePlaybackByContext[context] = slot;
        audio.onended = function() {
            URL.revokeObjectURL(url);
            stopVoiceForContext(context);
        };
        audio.onerror = function() {
            URL.revokeObjectURL(url);
            stopVoiceForContext(context);
            alert('Audio playback failed');
        };
        setChatVoicePlayUI(context, 'playing');
        audio.play().catch((err) => {
            console.error('Voice preview play() failed:', err);
            URL.revokeObjectURL(url);
            stopVoiceForContext(context);
            alert('Audio playback failed: ' + ((err && err.message) || 'Unknown browser error'));
        });
    } catch (e) {
        console.error('Error playing voice:', e);
        alert('Failed to play voice: ' + (e.message || 'Please try again.'));
        stopVoiceForContext(context);
    }
}

// Load voice options — registry voices first (same as Settings → General), then /api/voices fallback.
async function loadVoiceModels(provider) {
    await loadVoiceModelsInto(voiceModelSelect, provider);
}

function selectVoiceModelOption(modelSelectEl, preferredVoice) {
    if (!modelSelectEl) return;
    const preferred = (preferredVoice || modelSelectEl.value || '').trim();
    if (preferred && Array.from(modelSelectEl.options).some(o => o.value === preferred)) {
        modelSelectEl.value = preferred;
        return;
    }
    const first = Array.from(modelSelectEl.options).find(o => (o.value || '').trim());
    if (first) modelSelectEl.value = first.value;
}

async function loadVoiceModelsInto(modelSelectEl, provider, preferredVoice) {
    if (!modelSelectEl) return;
    await _ensureTTSProviders();
    if (!provider) {
        modelSelectEl.innerHTML = '<option value="">Select provider</option>';
        return;
    }
    const prov = _ttsProviders && _ttsProviders.find(p => p.id === provider);
    if (prov && Array.isArray(prov.voices) && prov.voices.length) {
        populateChatVoiceModelSelectForEl(modelSelectEl, provider);
        selectVoiceModelOption(modelSelectEl, preferredVoice);
        return;
    }
    modelSelectEl.innerHTML = '<option value="">Loading…</option>';
    try {
        const response = await fetch(`/api/voices/${encodeURIComponent(provider)}`);
        const data = await response.json();
        const voices = Array.isArray(data) ? data : (data.voices || data.chats || []);
        if (voices.length) {
            modelSelectEl.innerHTML = voices.map(chatVoiceOptionHtml).join('');
            selectVoiceModelOption(modelSelectEl, preferredVoice);
        } else {
            modelSelectEl.innerHTML = '<option value="">No voices available</option>';
        }
    } catch (e) {
        console.error('Error loading voice models:', e);
        modelSelectEl.innerHTML = '<option value="">Error loading</option>';
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

        if (voiceProviderSelect) {
            populateChatVoiceProviderSelect(voiceProviderSelect);
            const vpResolved = resolvedVoiceProviderForChat(generalData);
            const vpOpts = [...voiceProviderSelect.options].map(o => o.value);
            if (vpOpts.includes(vpResolved)) {
                voiceProviderSelect.value = vpResolved;
            } else if (voiceProviderSelect.options[0] && voiceProviderSelect.options[0].value) {
                voiceProviderSelect.selectedIndex = 0;
            }
            await loadVoiceModels(voiceProviderSelect.value);
            const voiceKey = getVoiceSettingsKey(voiceProviderSelect.value);
            const defaultVoice = (generalData[voiceKey] || '').trim();
            if (voiceModelSelect && defaultVoice && voiceModelSelect.options.length) {
                const opt = Array.from(voiceModelSelect.options).find(o => o.value === defaultVoice);
                if (opt) voiceModelSelect.value = defaultVoice;
                else if (voiceModelSelect.options[0] && voiceModelSelect.options[0].value) voiceModelSelect.selectedIndex = 0;
            } else if (voiceModelSelect && voiceModelSelect.options[0] && voiceModelSelect.options[0].value) {
                voiceModelSelect.selectedIndex = 0;
            }
            updateChatVoiceButtons('modal');
        }
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
    stopVoiceForContext('modal');
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

        persistGlobalVoiceToSettings(voiceProvider, voiceModel);

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

function providerIdFromDisplay(value) {
    const raw = (value || '').toString().trim();
    const lower = raw.toLowerCase();
    const map = {
        'ollama': 'ollama',
        'openai': 'openai',
        'anthropic': 'anthropic',
        'groq': 'groq',
        'openrouter': 'openrouter',
        'kilocode': 'kilocode',
        'kilo': 'kilocode',
        'google gemini': 'gemini',
        'gemini': 'gemini',
        'nvidia': 'nvidia'
    };
    return map[lower] || lower;
}

async function openChatConfigModal(chatId) {
    const targetId = chatId || currentChatId;
    if (!targetId || !chatConfigModal) return;
    stopAllChatVoicePreviews();
    if (chatConfigStatus) chatConfigStatus.textContent = '';
    chatConfigModal.style.display = 'flex';
    if (chatConfigSave) chatConfigSave.disabled = true;
    try {
        await _ensureTTSProviders();
        const [providersRes, chatRes] = await Promise.all([
            fetch('/api/llms/available-providers'),
            fetch(`${API_BASE}/chats/${targetId}`)
        ]);
        const providersData = providersRes.ok ? await providersRes.json() : { providers: [] };
        const chatData = chatRes.ok ? await chatRes.json() : currentChatSettings || {};
        const providers = providersData.providers || [];
        if (chatConfigLlmProvider) {
            chatConfigLlmProvider.innerHTML = providers.length
                ? providers.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('')
                : '<option value="">No providers configured</option>';
            const providerId = providerIdFromDisplay(chatData.provider || currentChatSettings?.provider || '');
            if (Array.from(chatConfigLlmProvider.options).some(o => o.value === providerId)) {
                chatConfigLlmProvider.value = providerId;
            }
        }
        await loadLlmModelsInto(chatConfigLlmModel, chatConfigLlmProvider ? chatConfigLlmProvider.value : '', chatData.model_name || currentChatSettings?.model_name || '');

        if (chatConfigVoiceProvider) {
            populateChatVoiceProviderSelect(chatConfigVoiceProvider);
            const voiceProvider = (chatData.voice_provider || currentChatSettings?.voice_provider || '').toString().trim();
            const voiceProviderId = canonicalVoiceProviderForSelect({ tts_provider: voiceProvider, voice_provider: voiceProvider });
            if (Array.from(chatConfigVoiceProvider.options).some(o => o.value === voiceProviderId)) {
                chatConfigVoiceProvider.value = voiceProviderId;
            } else if (chatConfigVoiceProvider.options[0] && chatConfigVoiceProvider.options[0].value) {
                chatConfigVoiceProvider.selectedIndex = 0;
            }
        }
        await loadVoiceModelsInto(chatConfigVoiceModel, chatConfigVoiceProvider ? chatConfigVoiceProvider.value : '');
        const rawVoice = (chatData.voice_model || currentChatSettings?.voice_model || '').toString().trim();
        if (rawVoice && chatConfigVoiceModel && Array.from(chatConfigVoiceModel.options).some(o => o.value === rawVoice)) {
            chatConfigVoiceModel.value = rawVoice;
        }
        updateChatVoiceButtons('chatConfig');
    } catch (e) {
        console.error('Open chat config failed:', e);
        if (chatConfigStatus) chatConfigStatus.textContent = 'Could not load chat configuration.';
    } finally {
        if (chatConfigSave) chatConfigSave.disabled = false;
    }
}

function hideChatConfigModal() {
    stopVoiceForContext('chatConfig');
    if (chatConfigModal) chatConfigModal.style.display = 'none';
}

function showChatSnackbar(message, type) {
    if (window.DecisionsAPI && typeof window.DecisionsAPI.snackbar === 'function') {
        window.DecisionsAPI.snackbar(message, type || 'success', { id: 'chat-snackbar' });
    }
}

function llmProviderDisplayName(value) {
    const id = providerIdFromDisplay(value);
    const labels = {
        ollama: 'Ollama',
        openai: 'OpenAI',
        anthropic: 'Anthropic',
        groq: 'Groq',
        openrouter: 'OpenRouter',
        kilocode: 'KiloCode',
        gemini: 'Google Gemini'
    };
    return labels[id] || (value || '').trim() || '—';
}

function voiceProviderDisplayName(voiceProvider) {
    const providerId = canonicalVoiceProviderForSelect({
        tts_provider: voiceProvider,
        voice_provider: voiceProvider
    });
    const provider = _ttsProviders && _ttsProviders.find(p => p.id === providerId);
    return provider?.name || (voiceProvider || '').trim() || '—';
}

function voiceModelDisplayName(voiceProvider, voiceModel, voiceModelDisplay) {
    const display = (voiceModelDisplay || '').trim();
    if (display) return display;
    const providerId = canonicalVoiceProviderForSelect({
        tts_provider: voiceProvider,
        voice_provider: voiceProvider
    });
    const provider = _ttsProviders && _ttsProviders.find(p => p.id === providerId);
    const voiceId = (voiceModel || '').trim();
    if (provider && Array.isArray(provider.voices) && voiceId) {
        const match = provider.voices.find(v => (v.id || v.name) === voiceId);
        if (match && match.name) return match.name;
    }
    return voiceId || '—';
}

function formatHeaderSettingsPair(providerLabel, modelLabel) {
    const provider = (providerLabel || '').trim() || '—';
    const model = (modelLabel || '').trim() || '—';
    return `${provider} / ${model}`;
}

function headerUsageProviderId(value) {
    return String(value || '').trim().toLowerCase().replace(/\s*\(.*?\)\s*/g, '').replace(/\s+/g, '');
}

function formatHeaderNumber(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return Math.round(n).toLocaleString();
}

function formatHeaderUnixDate(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return '—';
    return new Date(n * 1000).toLocaleString();
}

function setVoiceUsageHidden(hidden) {
    if (voiceUsageMeter) voiceUsageMeter.hidden = Boolean(hidden);
}

function renderVoiceUsageStatus(status) {
    if (!voiceUsageMeter || !voiceUsageFill || !voiceUsageText) return;
    if (!status) {
        setVoiceUsageHidden(true);
        return;
    }
    setVoiceUsageHidden(false);
    const available = Boolean(status.available);
    const percent = available ? Math.max(0, Math.min(100, Number(status.percent_used || 0))) : 0;
    voiceUsageMeter.style.setProperty('--voice-usage-percent', `${percent}%`);
    voiceUsageMeter.classList.toggle('is-warn', available && percent >= 80);
    voiceUsageMeter.classList.toggle('is-error', !available || percent >= 95);
    voiceUsageText.textContent = available ? `${percent}%` : '—';

    const lines = available ? [
        'ElevenLabs usage',
        `Status: ${status.status || '—'}`,
        `Tier: ${status.tier || '—'}`,
        `Used: ${formatHeaderNumber(status.used)} characters`,
        `Limit: ${formatHeaderNumber(status.limit)} characters`,
        `Remaining: ${formatHeaderNumber(status.remaining)} characters`,
        `Billing period: ${status.billing_period || '—'}`,
        `Refresh period: ${status.character_refresh_period || '—'}`,
        `Next reset: ${formatHeaderUnixDate(status.next_reset_unix)}`,
        `Credit extension: ${status.max_credit_limit_extension ?? '—'}`,
        `Current overage: ${JSON.stringify(status.current_overage || {})}`
    ] : [
        'ElevenLabs usage',
        status.error || 'Usage unavailable.'
    ];
    voiceUsageMeter.setAttribute('data-tooltip', lines.join('\n'));
}

function contextTooltipText(stats) {
    const estimated = Number((stats || {}).estimated_tokens || 0);
    const percent = Math.max(0, Math.min(100, Number((stats || {}).percent_used || 0)));
    const base = `${percent}% context used (${estimated}/${(stats || {}).context_window || 'unknown'} token limit)`;
    return _llmUsageTooltip ? `${base}\n\n${_llmUsageTooltip}` : base;
}

function renderLlmUsageStatus(status) {
    if (!status) {
        _llmUsageTooltip = '';
        return;
    }
    if (!status.available) {
        _llmUsageTooltip = `OpenAI billing\n${status.error || 'Usage unavailable.'}`;
        return;
    }
    const cost = Number(status.period_cost || 0);
    const currency = (status.currency || 'usd').toUpperCase();
    _llmUsageTooltip = [
        'OpenAI billing',
        `Current period cost: ${currency} ${cost.toFixed(4)}`,
        `Period start: ${formatHeaderUnixDate(status.period_start_unix)}`,
        `Period end: ${formatHeaderUnixDate(status.period_end_unix)}`,
        status.note || ''
    ].filter(Boolean).join('\n');
}

async function refreshHeaderUsageStatus(settings) {
    const voiceProvider = headerUsageProviderId(settings && settings.voice_provider);
    const llmProvider = headerUsageProviderId(settings && settings.provider);
    const wantsVoice = voiceProvider === 'elevenlabs';
    const wantsLlm = llmProvider === 'openai';
    if (!wantsVoice) renderVoiceUsageStatus(null);
    if (!wantsLlm) {
        _llmUsageTooltip = '';
        if (currentChatSettings && currentChatSettings.context_stats) updateContextRing(currentChatSettings.context_stats);
    }
    if (!wantsVoice && !wantsLlm) return;

    const cacheKey = `${voiceProvider}|${llmProvider}`;
    const now = Date.now();
    if (_headerUsageCacheKey === cacheKey && now - _headerUsageFetchedAt < 60000) return;
    _headerUsageCacheKey = cacheKey;
    _headerUsageFetchedAt = now;

    try {
        const qs = new URLSearchParams({
            voice_provider: wantsVoice ? voiceProvider : '',
            llm_provider: wantsLlm ? llmProvider : ''
        });
        const response = await fetch(`${API_BASE}/chats/usage-status?${qs.toString()}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (wantsVoice) renderVoiceUsageStatus(data.voice);
        if (wantsLlm) renderLlmUsageStatus(data.llm);
        if (currentChatSettings && currentChatSettings.context_stats) updateContextRing(currentChatSettings.context_stats);
    } catch (error) {
        console.warn('Header usage status failed:', error);
        if (wantsVoice) renderVoiceUsageStatus({ available: false, error: 'Usage unavailable.' });
        if (wantsLlm) renderLlmUsageStatus({ available: false, error: 'Billing usage unavailable.' });
    }
}

function getCurrentConversationalBenchmarkContext() {
    const provider = (currentChatSettings && currentChatSettings.provider) ? currentChatSettings.provider : '';
    const model = (currentChatSettings && currentChatSettings.model_name) ? currentChatSettings.model_name : '';
    return {
        provider: provider || '',
        model: model || ''
    };
}

function openChatBenchmarkInfoModal() {
    const context = getCurrentConversationalBenchmarkContext();
    if (window.DecisionsBenchmark && typeof window.DecisionsBenchmark.open === 'function') {
        window.DecisionsBenchmark.open('conversational', {
            provider: context.provider,
            model: context.model,
            compareProvider: context.provider,
            compareModel: context.model
        });
    }
}

async function renderHeaderSettingsSummary(settings) {
    if (!settings) return;
    await _ensureTTSProviders();
    const llmText = formatHeaderSettingsPair(
        llmProviderDisplayName(settings.provider),
        settings.model_name
    );
    if (headerLlmSummary) {
        headerLlmSummary.textContent = llmText;
        headerLlmSummary.title = llmText;
    }
    const voiceGroup = document.getElementById('headerVoiceGroup');
    const hasVoice = Boolean((settings.voice_provider || '').trim() || (settings.voice_model || '').trim());
    if (voiceGroup) voiceGroup.style.display = hasVoice ? '' : 'none';
    if (!headerVoiceSummary) return;
    if (!hasVoice) {
        headerVoiceSummary.textContent = '—';
        headerVoiceSummary.title = '';
        renderVoiceUsageStatus(null);
        _llmUsageTooltip = '';
        return;
    }
    const voiceText = formatHeaderSettingsPair(
        voiceProviderDisplayName(settings.voice_provider),
        voiceModelDisplayName(
            settings.voice_provider,
            settings.voice_model,
            settings.voice_model_display
        )
    );
    headerVoiceSummary.textContent = voiceText;
    headerVoiceSummary.title = voiceText;
    refreshHeaderUsageStatus(settings).catch((e) => {
        console.warn('Header usage status failed:', e);
    });
    setViewOnlyChrome(currentChatId != null && loadedChatId !== currentChatId);
}

async function persistChatSettingsPatch(body, { fromModal = true } = {}) {
    if (!currentChatId) return null;
    if (!body.provider || !body.model_name) {
        const msg = 'Choose an LLM provider and model.';
        if (fromModal && chatConfigStatus) chatConfigStatus.textContent = msg;
        else showChatSnackbar(msg, 'error');
        return null;
    }
    if (fromModal && (!body.voice_provider || !body.voice_model)) {
        const msg = 'Choose a voice provider and voice.';
        if (chatConfigStatus) chatConfigStatus.textContent = msg;
        else showChatSnackbar(msg, 'error');
        return null;
    }
    if (fromModal && chatConfigSave) chatConfigSave.disabled = true;
    if (fromModal && chatConfigStatus) chatConfigStatus.textContent = 'Saving...';
    try {
        const response = await fetch(`${API_BASE}/chats/${currentChatId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || 'Failed to save chat settings');
        if (loadedChatId === currentChatId) {
            await fetch(`${API_BASE}/chats/${currentChatId}/load-in-agent`, { method: 'POST' }).catch(() => {});
        }
        const refreshedRes = await fetch(`${API_BASE}/chats/${currentChatId}`);
        const refreshed = await refreshedRes.json().catch(() => ({}));
        if (!refreshedRes.ok) {
            throw new Error(refreshed.detail || refreshed.error || 'Failed to reload chat settings');
        }
        updateChatSettingsDisplay({
            title: refreshed.title || 'New Chat',
            provider: refreshed.provider || '-',
            model_name: refreshed.model_name || '-',
            voice_provider: refreshed.voice_provider,
            voice_model: refreshed.voice_model,
            voice_model_display: refreshed.voice_model_display,
            context_stats: refreshed.context_stats,
            compact_checkpoint: refreshed.compact_checkpoint
        });
        if (fromModal) hideChatConfigModal();
        showChatSnackbar('Chat settings saved', 'success');
        await loadChats();
        return refreshed;
    } catch (e) {
        console.error('Save chat settings failed:', e);
        const msg = e.message || 'Could not save chat settings.';
        if (fromModal && chatConfigStatus) chatConfigStatus.textContent = msg;
        else showChatSnackbar(msg, 'error');
        return null;
    } finally {
        setViewOnlyChrome(currentChatId != null && loadedChatId !== currentChatId);
        if (fromModal && chatConfigSave) chatConfigSave.disabled = false;
    }
}

async function saveChatConfig() {
    if (!currentChatId || !chatConfigSave) return;
    await persistChatSettingsPatch({
        provider: chatConfigLlmProvider ? chatConfigLlmProvider.value : '',
        model_name: chatConfigLlmModel ? chatConfigLlmModel.value : '',
        voice_provider: chatConfigVoiceProvider ? chatConfigVoiceProvider.value : '',
        voice_model: chatConfigVoiceModel ? chatConfigVoiceModel.value : ''
    }, { fromModal: true });
}

async function maybeAutoCompactChat(settings) {
    const stats = settings?.context_stats || {};
    const threshold = Number(stats.auto_compact_threshold || 75);
    const percent = Number(stats.percent_used || 0);
    const chatId = currentChatId;
    if (!chatId || loadedChatId !== chatId || isStreaming || _autoCompactInFlight) return;
    if (percent < threshold) return;
    const checkpointRowId = settings?.compact_checkpoint?.chat_row_id || 'none';
    const messageCount = Number(stats.message_count || 0);
    const autoCompactKey = `${chatId}:${checkpointRowId}:${messageCount}`;
    if (_lastAutoCompactKey === autoCompactKey) return;
    _autoCompactInFlight = true;
    try {
        const response = await fetch(`${API_BASE}/chats/${chatId}/compact`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: settings.provider || '',
                model_name: settings.model_name || '',
                reason: 'auto'
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || 'Auto-compact failed');
        _lastAutoCompactKey = autoCompactKey;
        if (currentChatId === chatId) {
            await selectChat(chatId);
            if (loadedChatId === chatId) loadedChatId = chatId;
            showChatView(loadedChatId === chatId);
        }
        await loadChats();
    } catch (e) {
        console.warn('Auto-compact skipped:', e);
    } finally {
        _autoCompactInFlight = false;
    }
}

async function compactCurrentChat(chatId) {
    const targetId = chatId || currentChatId;
    if (!targetId || isStreaming) return;
    if (compactChatButton) compactChatButton.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/chats/${targetId}/compact`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: currentChatSettings?.provider || '',
                model_name: currentChatSettings?.model_name || '',
                reason: 'manual'
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || 'Compaction failed');
        if (currentChatId === targetId) {
            await loadChat(targetId);
            await refreshChatHeaderStats({ checkTitle: true });
        }
        await loadChats();
    } catch (e) {
        console.error('Compact chat failed:', e);
        alert(e.message || 'Could not compact chat.');
    } finally {
        if (compactChatButton) compactChatButton.disabled = false;
    }
}

async function forkCurrentChat(chatId) {
    const targetId = chatId || currentChatId;
    if (!targetId || isStreaming) return;
    if (forkChatButton) forkChatButton.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/chats/${targetId}/fork`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: currentChatSettings?.provider || '',
                model_name: currentChatSettings?.model_name || ''
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || 'Fork failed');
        await loadChats();
        await loadChat(data.id, { skipLoadInAgent: true });
    } catch (e) {
        console.error('Fork chat failed:', e);
        alert(e.message || 'Could not fork chat.');
    } finally {
        if (forkChatButton) forkChatButton.disabled = false;
    }
}

function updateContextRing(stats) {
    const estimated = Number((stats || {}).estimated_tokens || 0);
    let percent = Math.max(0, Math.min(100, Number((stats || {}).percent_used || 0)));
    if (estimated > 0 && percent === 0) percent = 1;
    const ring = document.getElementById('contextRing');
    const ringValue = document.getElementById('contextRingValue');
    if (ring) {
        ring.style.setProperty('--context-percent', `${percent}%`);
        ring.title = contextTooltipText(stats);
        ring.setAttribute('data-tooltip', contextTooltipText(stats));
        ring.classList.toggle('context-ring-warn', percent >= (stats.auto_compact_threshold || 75));
    }
    if (ringValue) ringValue.textContent = `${percent}%`;
}

function applyHeaderStatsPayload(data) {
    if (!data || currentChatId == null) return;
    const title = data.title || 'New Chat';
    if (currentChatSettings) {
        currentChatSettings.title = title;
        if (data.context_stats) currentChatSettings.context_stats = data.context_stats;
        if (data.compact_checkpoint !== undefined) currentChatSettings.compact_checkpoint = data.compact_checkpoint;
        if (data.title_auto) currentChatSettings.title_auto = data.title_auto;
    }
    const titleEl = document.getElementById('chatTitle');
    if (titleEl) {
        titleEl.textContent = title;
        titleEl.title = title;
        requestAnimationFrame(applyHeaderTitleMarquee);
    }
    const sidebarTitle = document.querySelector(`.chat-item[data-chat-id="${currentChatId}"] .chat-item-title`);
    if (sidebarTitle) {
        sidebarTitle.textContent = title;
        applyChatTitleMarqueeForWrap(sidebarTitle.closest('.chat-item-title-wrap'));
    }
    if (data.context_stats) updateContextRing(data.context_stats);
}

function scheduleRefreshChatHeaderStats({ checkTitle = false } = {}) {
    if (_headerStatsTimer) clearTimeout(_headerStatsTimer);
    _headerStatsTimer = setTimeout(() => {
        _headerStatsTimer = null;
        refreshChatHeaderStats({ checkTitle });
    }, 250);
}

async function refreshChatHeaderStats({ checkTitle = false } = {}) {
    const chatId = currentChatId;
    if (!chatId) return;
    try {
        if (checkTitle) {
            const titleRes = await fetch(`${API_BASE}/chats/${chatId}/refresh-title`, { method: 'POST' });
            if (titleRes.ok) {
                applyHeaderStatsPayload(await titleRes.json());
                return;
            }
        }
        const response = await fetch(`${API_BASE}/chats/${chatId}/header-stats`);
        if (!response.ok) return;
        const data = await response.json();
        applyHeaderStatsPayload(data);
        const auto = data.title_auto || {};
        if (!auto.manual) {
            const count = Number(data.context_stats?.message_count || 0);
            const last = Number(auto.last_refresh_message_count || 0);
            const interval = Number(auto.interval || 5);
            if (count - last >= interval) {
                const titleRes = await fetch(`${API_BASE}/chats/${chatId}/refresh-title`, { method: 'POST' });
                if (titleRes.ok) applyHeaderStatsPayload(await titleRes.json());
            }
        }
        if (currentChatSettings) {
            currentChatSettings.context_stats = data.context_stats;
            maybeAutoCompactChat(currentChatSettings);
        }
    } catch (e) {
        console.warn('Header stats refresh failed:', e);
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

    applyHeaderStatsPayload({
        title: settings.title || 'New Chat',
        context_stats: settings.context_stats,
        compact_checkpoint: settings.compact_checkpoint,
        title_auto: settings.title_auto
    });

    renderHeaderSettingsSummary(currentChatSettings).catch((e) => {
        console.warn('Header settings summary failed:', e);
    });

    chatSettingsHeader.style.display = 'flex';
    maybeAutoCompactChat(currentChatSettings);
}

// ── Custom Voice Management for Chat UI ──────────────────────────────────

const _CHAT_CV_PROVIDERS = new Set(['kokoro', 'elevenlabs', 'coqui', 'supertonic', 'pixazo']);
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

// Show/hide custom voice buttons (modal, empty-state, and Configure Chat forms)
function updateChatVoiceButtons(context) {
    const providerEl = getChatVoiceProviderEl(context);
    const voiceEl = getChatVoiceModelEl(context);
    const meta = getChatVoiceContextMeta(context);
    const prefix = meta ? meta.prefix : context;
    const customBtn = document.getElementById(prefix + 'CustomVoiceBtn');
    const deleteBtn = document.getElementById(prefix + 'DeleteVoiceBtn');
    const editBtn = document.getElementById(prefix + 'EditVoiceBtn');
    if (!providerEl || !voiceEl) return;

    const provider = providerEl.value;
    const selected = voiceEl.selectedOptions && voiceEl.selectedOptions[0];
    const supportsCustom = _CHAT_CV_PROVIDERS.has(provider);
    const isCustomVoice = selected && selected.dataset.custom === '1';

    if (customBtn) customBtn.style.display = supportsCustom ? '' : 'none';
    if (deleteBtn) deleteBtn.style.display = isCustomVoice ? '' : 'none';
    if (editBtn) editBtn.style.display = isCustomVoice ? '' : 'none';
}

(function() {
    ['emptyState', 'modal', 'chatConfig'].forEach((context) => {
        const meta = getChatVoiceContextMeta(context);
        if (!meta) return;
        const providerEl = document.getElementById(meta.providerId);
        const modelEl = document.getElementById(meta.modelId);
        if (providerEl) providerEl.addEventListener('change', () => updateChatVoiceButtons(context));
        if (modelEl) modelEl.addEventListener('change', () => updateChatVoiceButtons(context));
    });

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

    setTimeout(() => {
        updateChatVoiceButtons('emptyState');
        updateChatVoiceButtons('modal');
        updateChatVoiceButtons('chatConfig');
    }, 500);
})();

// ── Open / Close / Submit Custom Voice Modal ──

function openChatCustomVoiceModal(context) {
    const providerEl = getChatVoiceProviderEl(context);
    const provider = providerEl ? providerEl.value : 'kokoro';
    if (!_CHAT_CV_PROVIDERS.has(provider)) return;

    document.getElementById('chatCv_provider').value = provider;
    document.getElementById('chatCv_context').value = context;
    document.getElementById('chatCv_providerLabel').textContent = provider.charAt(0).toUpperCase() + provider.slice(1);
    document.getElementById('chatCv_name').value = '';
    document.getElementById('chatCv_personality').value = '';
    const promptEl = document.getElementById('chatCv_prompt');
    if (promptEl) promptEl.value = '';
    const transcribingEl = document.getElementById('chatCv_transcribing');
    if (transcribingEl) transcribingEl.classList.add('hidden');
    document.getElementById('chatCv_error').classList.add('hidden');
    document.getElementById('chatCv_form').classList.remove('hidden');
    document.getElementById('chatCv_processing').classList.add('hidden');
    document.getElementById('chatCv_submitBtn').disabled = true;
    setChatCvGender('female');
    setChatCvAudioMode('upload');
    _chatCvRecordedBlob = null;

    // Show/hide gender row (only for kokoro)
    document.getElementById('chatCv_genderRow').style.display = (provider === 'kokoro') ? '' : 'none';
    const audioInput = document.getElementById('chatCv_audio');
    const help = document.getElementById('chatCv_uploadHelp');
    const recordBtn = document.getElementById('chatCv_mode_record');
    if (provider === 'supertonic') {
        audioInput.accept = '.json';
        audioInput.multiple = false;
        if (help) help.textContent = 'Upload a Supertonic Voice Builder .json voice style file.';
        if (recordBtn) recordBtn.classList.add('hidden');
    } else {
        audioInput.accept = '.wav,.mp3,.m4a,.ogg,.flac,.webm';
        audioInput.multiple = true;
        if (help) help.textContent = 'Upload audio clips of the voice to clone';
        if (recordBtn) recordBtn.classList.remove('hidden');
    }

    const modal = document.getElementById('chatCustomVoiceModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.getElementById('chatCv_name').focus();

    audioInput.onchange = function() {
        document.getElementById('chatCv_submitBtn').disabled = !(this.files && this.files.length > 0);
        if (provider !== 'supertonic' && this.files && this.files.length > 0) {
            _autoTranscribeChatCustomVoice(this.files[0], provider);
        }
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

function _autoTranscribeChatCustomVoice(file, provider) {
    const indicator = document.getElementById('chatCv_transcribing');
    const promptEl = document.getElementById('chatCv_prompt');
    if (indicator) indicator.classList.remove('hidden');
    if (promptEl) promptEl.value = '';

    const fd = new FormData();
    fd.append('audio', file);
    fd.append('provider', provider || '');

    fetch('/api/custom-voices/transcribe', { method: 'POST', body: fd })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            if (indicator) indicator.classList.add('hidden');
            if (ok && data.transcript && promptEl) {
                promptEl.value = data.transcript;
            }
        })
        .catch(() => {
            if (indicator) indicator.classList.add('hidden');
        });
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
    if (provider === 'supertonic' && isRecord) { errEl.textContent = 'Supertonic custom voices require a Voice Builder .json file'; errEl.classList.remove('hidden'); return false; }
    if (isRecord && !_chatCvRecordedBlob) { errEl.textContent = 'Please record a voice sample'; errEl.classList.remove('hidden'); return false; }
    if (!isRecord && (!audioInput.files || audioInput.files.length === 0)) { errEl.textContent = 'Audio file required'; errEl.classList.remove('hidden'); return false; }
    if (provider === 'supertonic' && !Array.from(audioInput.files).some(f => f.name.toLowerCase().endsWith('.json'))) {
        errEl.textContent = 'Supertonic custom voices require a Voice Builder .json file';
        errEl.classList.remove('hidden');
        return false;
    }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('provider', provider);
    fd.append('system_prompt', (document.getElementById('chatCv_prompt')?.value || '').trim());
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
                invalidateTTSProvidersCache();
                const voiceSelect = getChatVoiceModelEl(context);
                await reloadChatVoiceModels(context, provider);
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
    const voiceEl = getChatVoiceModelEl(context);
    const providerEl = getChatVoiceProviderEl(context);
    if (!voiceEl || !providerEl) return;
    const voiceId = voiceEl.value;
    const selected = voiceEl.selectedOptions && voiceEl.selectedOptions[0];
    const selectedIsCustom = selected && (selected.dataset.custom === '1' || (voiceId || '').startsWith('custom_'));
    if (!selectedIsCustom) {
        alert('Select a custom voice before deleting.');
        return;
    }
    const dbId = selected.dataset.customVoiceId || (voiceId && voiceId.startsWith('custom_') ? voiceId.split('_')[1] : '');
    const providerVoiceId = selected.dataset.providerVoiceId || voiceId;
    const voiceName = voiceEl.options[voiceEl.selectedIndex]?.text || voiceId;
    const confirmed = await window.DecisionsAPI.confirm({
        title: "Delete custom voice",
        message: 'Delete custom voice "' + voiceName + '"? This cannot be undone.',
        confirmLabel: "Delete",
        danger: true,
    });
    if (!confirmed) return;

    try {
        const url = dbId
            ? '/api/custom-voices/' + encodeURIComponent(dbId)
            : '/api/elevenlabs-voices/' + encodeURIComponent(providerVoiceId);
        const r = await fetch(url, { method: 'DELETE' });
        if (!r.ok) {
            const data = await r.json().catch(() => ({}));
            throw new Error(data.error || data.detail || 'Delete failed');
        }
        const provider = providerEl.value;
        invalidateTTSProvidersCache();
        await reloadChatVoiceModels(context, provider);
        updateChatVoiceButtons(context);
    } catch (e) {
        console.error('Delete failed:', e);
        alert('Delete failed: ' + (e.message || 'Please try again.'));
    }
}

async function editChatCustomVoice(context) {
    const voiceEl = getChatVoiceModelEl(context);
    if (!voiceEl) return;
    const voiceId = voiceEl.value;
    const selected = voiceEl.selectedOptions && voiceEl.selectedOptions[0];
    if (!selected || selected.dataset.custom !== '1') return;
    const dbId = selected.dataset.customVoiceId || (voiceId && voiceId.startsWith('custom_') ? voiceId.split('_')[1] : '');
    if (!dbId) return;
    const voiceName = selected.textContent || voiceId;

    // Fetch current personality
    try {
        const r = await fetch('/api/custom-voices?provider=' + (getChatVoiceProviderEl(context)?.value || ''));
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
