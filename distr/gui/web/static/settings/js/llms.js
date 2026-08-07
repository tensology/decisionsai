// LLMs Settings JavaScript

function _getLLMActionButtons() {
    return {
        saveBtn: document.getElementById('llms_inline_save') || document.getElementById('btn_save'),
        reloadBtn: document.getElementById('btn_cancel'),
    };
}

function _setActionBusy(button, busy, busyLabel, idleLabel) {
    if (!button) return;
    if (busy) {
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = idleLabel || button.textContent || '';
        }
        button.dataset.busy = '1';
        button.disabled = true;
        button.textContent = busyLabel;
        return;
    }
    delete button.dataset.busy;
    button.disabled = false;
    button.textContent = idleLabel || button.dataset.defaultLabel || button.textContent;
}

const LLM_TYPES = ['conversational', 'coding', 'vision', 'image', 'video', 'workflow', 'computer_use'];
const PROJECT_CLI_LEVELS = ['low', 'medium', 'high'];
let llmProviderStatusById = {};
let projectCliBackendsById = {};
let projectCliModelStateByLevel = {};
let benchmarkModalState = null;
const llmModelRequestCache = new Map();
const projectCliModelRequestCache = new Map();
let projectCliBackendsRequest = null;
let activeLlmSubtab = 'speech';

// Populate provider dropdowns from available providers (Third-Party configured only)
async function populateProviderDropdowns() {
    try {
        const [llmRes, mediaRes] = await Promise.all([
            fetch('/api/llms/available-providers'),
            fetch('/api/llms/available-media-providers'),
        ]);
        const llmData = llmRes.ok ? await llmRes.json() : { providers: [] };
        const mediaData = mediaRes.ok ? await mediaRes.json() : { providers: [] };
        const baseProviders = llmData.providers || [];
        const mediaProviders = mediaData.providers || [];
        const llmTypes = LLM_TYPES;
        const optionalTypes = ['workflow', 'computer_use', 'video'];
        const emptyLabels = {
            'workflow': 'Inherit from Conversational',
            'computer_use': 'Disabled (accessibility tree only)',
            'video': 'Disabled',
        };
        for (const type of llmTypes) {
            const sel = document.getElementById(`${type}_provider`);
            if (!sel) continue;
            let providers = baseProviders;
            if (type === 'image') {
                const seen = new Set(baseProviders.map(p => p.id));
                providers = baseProviders.concat(mediaProviders.filter(p => !seen.has(p.id)));
            } else if (type === 'video') {
                providers = mediaProviders.length ? mediaProviders : [];
            }
            let html = '';
            if (optionalTypes.includes(type)) {
                html += `<option value="">${escapeHtml(emptyLabels[type] || 'Inherit')}</option>`;
            }
            if (providers.length) {
                html += providers.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('');
            } else if (!optionalTypes.includes(type)) {
                html = '<option value="ollama">Ollama</option>';
            }
            sel.innerHTML = html;
        }
    } catch (e) {
        console.error('Error loading available providers:', e);
    }
}

function ensureProviderStatusPills() {
    // Provider pills on the main LLM rows turned out to be visually noisy and
    // competed with the restored info icon. Keep the richer status badges for
    // project routing instead.
}

function _providerStatusClassName(row) {
    if (!row) return 'llm-provider-pill llm-provider-pill--idle';
    if (row.state === 'local') return 'llm-provider-pill llm-provider-pill--local';
    if (row.ready) return 'llm-provider-pill llm-provider-pill--ready';
    return 'llm-provider-pill llm-provider-pill--missing';
}

function updateProviderStatusPill(type) {
    const select = document.getElementById(`${type}_provider`);
    const pill = document.getElementById(`${type}_provider_status`);
    if (!select || !pill) return;
    const provider = (select.value || '').toLowerCase();
    if (!provider) {
        pill.className = 'llm-provider-pill llm-provider-pill--idle';
        pill.textContent = type === 'workflow' ? 'Inherit' : 'Disabled';
        pill.title = '';
        return;
    }
    const row = llmProviderStatusById[provider];
    pill.className = _providerStatusClassName(row);
    pill.textContent = row ? (row.balance_label || row.name || provider) : 'Unknown';
    pill.title = row && row.detail ? row.detail : '';
}

async function loadProviderStatusPills() {
    ensureProviderStatusPills();
    try {
        const response = await fetch('/api/llms/provider-status');
        const data = response.ok ? await response.json() : { providers: [] };
        llmProviderStatusById = {};
        (data.providers || []).forEach(row => {
            llmProviderStatusById[(row.id || '').toLowerCase()] = row;
        });
    } catch (e) {
        console.error('Error loading provider status pills:', e);
    }
    LLM_TYPES.forEach(updateProviderStatusPill);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function populateSttOptions(settings) {
    const select = document.getElementById('stt_model');
    if (!select) return;
    const options = settings.stt_options || [
        { id: 'vosk', name: 'Vosk (Local & Offline)' },
        { id: 'whisper', name: 'Whisper.cpp (Local & Offline)' }
    ];
    select.innerHTML = '';
    options.forEach(option => {
        const opt = document.createElement('option');
        opt.value = option.id;
        opt.textContent = option.name;
        select.appendChild(opt);
    });
    select.value = settings.stt_model || (options[0] && options[0].id) || 'whisper';

    if (settings.stt_unavailable && settings.stt_unavailable.reason && typeof window.showNotification === 'function') {
        window.showNotification(settings.stt_unavailable.reason, 'warning');
    }
}

async function populateProjectCliBackends() {
    try {
        if (!projectCliBackendsRequest) {
            projectCliBackendsRequest = fetch('/api/projects/cli-backends')
                .then(async response => (response.ok ? response.json() : { backends: [] }))
                .catch(() => ({ backends: [] }));
        }
        const data = await projectCliBackendsRequest;
        const backends = (data.backends || []).slice().sort((a, b) => {
            return String(a.name || '').localeCompare(String(b.name || ''));
        });
        projectCliBackendsById = {};
        backends.forEach(row => {
            projectCliBackendsById[(row.id || '').toLowerCase()] = row;
        });
        PROJECT_CLI_LEVELS.forEach(level => {
            const sel = document.getElementById(`project_cli_${level}_backend`);
            if (!sel || !backends.length) return;
            sel.innerHTML = backends.map(b => {
                const displayName = String(b.name || '').replace(/\s+CLI$/i, '').trim() || b.id;
                return `<option value="${escapeHtml(b.id)}">${escapeHtml(displayName)}</option>`;
            }).join('');
        });
    } catch (e) {
        console.error('Error loading project CLI backends:', e);
    }
}

async function loadProjectCliRouteModels(level, backend, selectedModel) {
    const sel = document.getElementById(`project_cli_${level}_model`);
    if (!sel) return;
    backend = (backend || 'pi').trim();
    selectedModel = (selectedModel || '').trim();
    try {
        sel.innerHTML = '<option value="">Loading models...</option>';
        sel.disabled = true;
        const cacheKey = backend.toLowerCase();
        let request = projectCliModelRequestCache.get(cacheKey);
        if (!request) {
            request = fetch(`/api/projects/cli-models?backend_id=${encodeURIComponent(backend)}`)
                .then(async response => (response.ok ? response.json() : { models: [], recommended_model: { id: 'auto' } }))
                .catch(() => ({ models: [], recommended_model: { id: 'auto' } }));
            projectCliModelRequestCache.set(cacheKey, request);
        }
        const data = await request;
        const models = data.models || [];
        projectCliModelStateByLevel[level] = data;
        sel.innerHTML = '';
        if (data.supports_model_picker === false || data.backend_kind === 'ide') {
            const opt = document.createElement('option');
            opt.value = 'auto';
            opt.textContent = 'Model chosen inside the IDE';
            sel.appendChild(opt);
            sel.value = 'auto';
            sel.disabled = true;
        } else {
            const auto = document.createElement('option');
            auto.value = 'auto';
            auto.textContent = 'Auto';
            sel.appendChild(auto);

            const recommendedId = ((data.recommended_model || {}).id || '').trim();
            const orderedModels = models.slice().sort((a, b) => {
                const aId = (a && typeof a === 'object') ? (a.id || '') : (a || '');
                const bId = (b && typeof b === 'object') ? (b.id || '') : (b || '');
                if (aId === recommendedId) return -1;
                if (bId === recommendedId) return 1;
                return String((a && typeof a === 'object') ? (a.name || aId) : aId)
                    .localeCompare(String((b && typeof b === 'object') ? (b.name || bId) : bId));
            });
            orderedModels.forEach(m => {
                const opt = document.createElement('option');
                const rawId = (m && typeof m === 'object') ? (m.id || '') : m;
                const rawName = (m && typeof m === 'object') ? (m.name || m.id || '') : (m || '');
                opt.value = rawId;
                opt.textContent = rawId === recommendedId ? `${rawName} (Recommended)` : rawName;
                sel.appendChild(opt);
            });
            if (selectedModel && !Array.prototype.some.call(sel.options, o => o.value === selectedModel)) {
                const opt = document.createElement('option');
                opt.value = selectedModel;
                opt.textContent = selectedModel;
                sel.appendChild(opt);
            }
            sel.value = selectedModel || (recommendedId || 'auto');
            sel.disabled = false;
        }
        updateProjectCliRouteStatus(level);
    } catch (e) {
        console.error(`Error loading project CLI route models for ${level}:`, e);
        projectCliModelStateByLevel[level] = null;
        sel.innerHTML = '<option value="auto">Auto</option>';
        sel.value = selectedModel || 'auto';
        sel.disabled = false;
        updateProjectCliRouteStatus(level);
    } finally {
        if (!sel.disabled) {
            sel.disabled = false;
        }
    }
}

async function populateProjectCliRoutes(settings) {
    await populateProjectCliBackends();
    await Promise.all(PROJECT_CLI_LEVELS.map(async level => {
        const backend = settings[`project_cli_${level}_backend`] || (level === 'low' ? 'cursor' : 'codex');
        const model = settings[`project_cli_${level}_model`] || (level === 'high' ? 'gpt-5.3-codex' : 'auto');
        const backendSel = document.getElementById(`project_cli_${level}_backend`);
        if (backendSel) backendSel.value = backend;
        await loadProjectCliRouteModels(level, backend, model);
        updateProjectCliRouteStatus(level);
    }));
}

function bindProjectCliRouteControls() {
    PROJECT_CLI_LEVELS.forEach(level => {
        const backendSel = document.getElementById(`project_cli_${level}_backend`);
        if (!backendSel || backendSel.dataset.bound === '1') return;
        backendSel.dataset.bound = '1';
        backendSel.addEventListener('change', function() {
            loadProjectCliRouteModels(level, this.value, 'auto');
            updateProjectCliRouteStatus(level);
        });
    });
}

function ensureProjectCliStatusPills() {
    // Status dots are rendered directly in the template beside each backend select.
}

function updateProjectCliRouteStatus(level) {
    const backendSel = document.getElementById(`project_cli_${level}_backend`);
    const backendDot = document.getElementById(`project_cli_${level}_backend_status`);
    if (!backendSel || !backendDot) return;
    const backend = projectCliBackendsById[(backendSel.value || '').toLowerCase()];
    if (!backend) {
        backendDot.className = 'project-cli-status-dot project-cli-status-dot--idle';
        backendDot.title = 'Loading backend status';
    } else if (backend.ready) {
        backendDot.className = 'project-cli-status-dot project-cli-status-dot--ready';
        backendDot.title = (backend.message || 'CLI ready');
    } else {
        backendDot.className = 'project-cli-status-dot project-cli-status-dot--warning';
        backendDot.title = (backend.message || 'Needs setup');
    }
}

// Load LLMs settings from backend
async function loadLLMsSettings(opts) {
    opts = opts || {};
    try {
        if (opts.forceModelReload) {
            llmModelRequestCache.clear();
            projectCliModelRequestCache.clear();
            projectCliBackendsRequest = null;
            const { reloadBtn } = _getLLMActionButtons();
            _setActionBusy(reloadBtn, true, 'Reloading...', 'Reload');
            if (typeof window.showNotification === 'function') {
                window.showNotification('Reloading model catalogs...', 'info');
            }
            const reloadResponse = await fetch('/api/llms/models/reload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (!reloadResponse.ok) {
                throw new Error('Failed to flush model cache');
            }
        }

        await populateProviderDropdowns();

        const response = await fetch('/api/llms');
        if (!response.ok) {
            throw new Error('Failed to load LLMs settings');
        }
        const settings = await response.json();

        populateSttOptions(settings);
        const instantDictation = document.getElementById('instant_dictation');
        if (instantDictation) {
            instantDictation.checked = settings.instant_dictation !== undefined ? settings.instant_dictation : true;
        }

        const llmTypes = LLM_TYPES;
        const optionalTypes = ['workflow', 'computer_use', 'video'];
        const modelLoadTasks = llmTypes.map(async type => {
            const provider = (settings[`${type}_provider`] || '').toLowerCase();
            const model = (settings[`${type}_model`] || '').trim();

            const providerSelect = document.getElementById(`${type}_provider`);
            let effectiveProvider = provider;
            if (providerSelect) {
                providerSelect.value = provider;
                if (providerSelect.value !== provider && providerSelect.options.length) {
                    // For optional types, empty string is valid (inherit/disabled)
                    if (optionalTypes.includes(type) && !provider) {
                        providerSelect.value = '';
                    } else {
                        providerSelect.selectedIndex = 0;
                    }
                    effectiveProvider = providerSelect.value;
                }
            }

            if (effectiveProvider) {
                await loadLLMModels(type, effectiveProvider, { notifyOnMissingProviderKey: false });
            }

            const modelSelect = document.getElementById(`${type}_model`);
            if (modelSelect) {
                let found = false;
                for (let i = 0; i < modelSelect.options.length; i++) {
                    if (modelSelect.options[i].value === model) {
                        modelSelect.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found && model) {
                    const option = document.createElement('option');
                    option.value = model;
                    option.textContent = model;
                    modelSelect.appendChild(option);
                    modelSelect.value = model;
                }
            }
        });
        await Promise.all(modelLoadTasks);

        await populateProjectCliRoutes(settings);
        updateDownloadButtonVisibility();
        await refreshS2sLocksUi();
        if (opts.forceModelReload && typeof window.showNotification === 'function') {
            window.showNotification('Model catalogs reloaded', 'success');
        }
        console.log('LLMs settings loaded');
    } catch (error) {
        console.error('Error loading LLMs settings:', error);
        showNotification('Failed to load LLMs settings: ' + error.message, 'error');
    } finally {
        if (opts.forceModelReload) {
            const { reloadBtn } = _getLLMActionButtons();
            _setActionBusy(reloadBtn, false, '', 'Reload');
        }
    }
}

async function refreshS2sLocksUi(explicitModel) {
    if (!window.DecisionsS2S || typeof window.DecisionsS2S.applyS2sLocks !== 'function') {
        return;
    }
    const modelEl = document.getElementById('conversational_model');
    const model = explicitModel !== undefined && explicitModel !== null
        ? explicitModel
        : (modelEl ? modelEl.value : '');
    await window.DecisionsS2S.applyS2sLocks({
        model: model,
        sttSelect: document.getElementById('stt_model'),
        conversationalProvider: document.getElementById('conversational_provider'),
        conversationalModel: modelEl,
        ttsProvider: document.getElementById('tts_provider'),
        openaiTtsModel: document.getElementById('openai_tts_model'),
        voiceProvider: document.getElementById('tts_provider'),
        voiceModel: document.getElementById('tts_voice'),
    });
    // If dropdown is Completions but loaded chat is S2S, re-query without model (chat-scoped)
    if (model && window.DecisionsS2S.fetchS2sLocks) {
        const byModel = await window.DecisionsS2S.fetchS2sLocks(model);
        if (!byModel.s2s_active) {
            await window.DecisionsS2S.applyS2sLocks({
                model: '',
                sttSelect: document.getElementById('stt_model'),
                conversationalProvider: document.getElementById('conversational_provider'),
                conversationalModel: modelEl,
                ttsProvider: document.getElementById('tts_provider'),
                openaiTtsModel: document.getElementById('openai_tts_model'),
                voiceProvider: document.getElementById('tts_provider'),
                voiceModel: document.getElementById('tts_voice'),
            });
        }
    }
}

// Save LLMs settings to backend
async function saveLLMsSettings() {
    const { saveBtn } = _getLLMActionButtons();
    try {
        _setActionBusy(saveBtn, true, 'Saving...', 'Save');
        if (typeof window.showNotification === 'function') {
            window.showNotification('Saving LLM settings...', 'info');
        }
        const settings = {
            stt_model: document.getElementById('stt_model').value,
            conversational_provider: document.getElementById('conversational_provider').value,
            conversational_model: document.getElementById('conversational_model').value,
            coding_provider: document.getElementById('coding_provider').value,
            coding_model: document.getElementById('coding_model').value,
            vision_provider: document.getElementById('vision_provider').value,
            vision_model: document.getElementById('vision_model').value,
            image_provider: document.getElementById('image_provider').value,
            image_model: document.getElementById('image_model').value,
            video_provider: document.getElementById('video_provider') ? document.getElementById('video_provider').value : '',
            video_model: document.getElementById('video_model') ? document.getElementById('video_model').value : '',
            workflow_provider: document.getElementById('workflow_provider').value,
            workflow_model: document.getElementById('workflow_model').value,
            computer_use_provider: document.getElementById('computer_use_provider').value,
            computer_use_model: document.getElementById('computer_use_model').value,
            project_cli_low_backend: document.getElementById('project_cli_low_backend')?.value || 'cursor',
            project_cli_low_model: document.getElementById('project_cli_low_model')?.value || 'auto',
            project_cli_medium_backend: document.getElementById('project_cli_medium_backend')?.value || 'codex',
            project_cli_medium_model: document.getElementById('project_cli_medium_model')?.value || 'auto',
            project_cli_high_backend: document.getElementById('project_cli_high_backend')?.value || 'codex',
            project_cli_high_model: document.getElementById('project_cli_high_model')?.value || 'gpt-5.3-codex',
            instant_dictation: document.getElementById('instant_dictation') ? document.getElementById('instant_dictation').checked : true
        };

        const response = await fetch('/api/llms', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save LLMs settings');
        }

        const result = await response.json();
        if (typeof window.showNotification === 'function') {
            window.showNotification('LLMs settings saved', 'success');
        }
        console.log('LLMs settings saved:', result);
    } catch (error) {
        console.error('Error saving LLMs settings:', error);
        if (typeof window.showNotification === 'function') {
            window.showNotification('Failed to save LLMs settings: ' + error.message, 'error');
        } else {
            console.error('Failed to save LLMs settings:', error.message);
        }
    } finally {
        _setActionBusy(saveBtn, false, '', 'Save');
    }
}

async function reloadLLMsSettings() {
    return loadLLMsSettings({ forceModelReload: true });
}

// Load available models for a specific LLM type and provider (API returns [{id, name}])
async function loadLLMModels(type, provider, opts) {
    opts = opts || {};
    var notifyOnMissingProviderKey = !!opts.notifyOnMissingProviderKey;
    const modelSelect = document.getElementById(`${type}_model`);
    if (!modelSelect) return;

    try {
        modelSelect.innerHTML = '<option value="">Loading models...</option>';
        modelSelect.disabled = true;

        const cacheKey = `${String(type || '').toLowerCase()}::${String(provider || '').toLowerCase()}`;
        let request = llmModelRequestCache.get(cacheKey);
        if (!request) {
            request = fetch(`/api/llms/models?type=${encodeURIComponent(type)}&provider=${encodeURIComponent(provider)}`)
                .then(async response => {
                    if (!response.ok) {
                        throw new Error('Failed to load models');
                    }
                    return response.json();
                });
            llmModelRequestCache.set(cacheKey, request);
        }
        const data = await request;
        const models = data.models || [];

        modelSelect.innerHTML = '<option value="">Select model...</option>';

        if (models.length === 0) {
            if (provider !== 'ollama') {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = `Configure ${provider} API key in API Keys`;
                option.disabled = true;
                modelSelect.appendChild(option);
                if (notifyOnMissingProviderKey) {
                    showNotification(`${provider} API key not configured. Go to API Keys.`, 'warning');
                }
            } else {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = 'No models found - check Ollama is running';
                option.disabled = true;
                modelSelect.appendChild(option);
            }
        } else {
            models.forEach(m => {
                const option = document.createElement('option');
                option.value = (typeof m === 'object' && m != null && m.id != null) ? m.id : m;
                option.textContent = (typeof m === 'object' && m != null && m.name != null) ? m.name : (m || '');
                modelSelect.appendChild(option);
            });
        }

        console.log(`Loaded ${models.length} models for ${type} (${provider})`);
    } catch (error) {
        console.error(`Error loading models for ${type}:`, error);
        modelSelect.innerHTML = '<option value="">Error loading models</option>';
        showNotification(`Failed to load models: ${error.message}`, 'error');
    } finally {
        modelSelect.disabled = false;
    }
}

// Open Ollama model browser modal (like native OllamaModelBrowserDialog)
let ollamaBrowserCurrentType = 'conversational';
let ollamaBrowserModels = [];

function openOllamaModelBrowser(type) {
    ollamaBrowserCurrentType = type;
    const modal = document.getElementById('ollama_browser_modal');
    if (!modal) return;
    // Update modal title based on type
    const titleEl = document.getElementById('ollama_browser_modal_title');
    if (titleEl) {
        const typeLabels = { conversational: 'Conversational', coding: 'Coding', vision: 'Vision', image: 'Image', workflow: 'Workflow', computer_use: 'Computer Use' };
        titleEl.textContent = 'Download Ollama models — ' + (typeLabels[type] || type);
    }
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.getElementById('ollama_browser_search').value = '';
    document.getElementById('ollama_browser_list').innerHTML = '<p class="text-[#9ca3af] text-sm">Loading...</p>';
    var pullStatus = document.getElementById('ollama_browser_pull_status');
    if (pullStatus) pullStatus.classList.add('hidden');
    fetchOllamaLibraryAndRender();
}

function closeOllamaBrowserModal() {
    const modal = document.getElementById('ollama_browser_modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
}

async function fetchOllamaLibraryAndRender() {
    try {
        const response = await fetch('/api/ollama/library');
        const data = await response.json();
        ollamaBrowserModels = data.models || [];
        renderOllamaBrowserList(filterModelsByType(ollamaBrowserModels));
    } catch (e) {
        console.error('Failed to load Ollama library:', e);
        document.getElementById('ollama_browser_list').innerHTML = '<p class="text-red-400 text-sm">Failed to load library.</p>';
    }
}

/**
 * Filter models by ollamaBrowserCurrentType based on capabilities from the library scrape.
 * conversational → tools, coding → tools or code, vision → vision, image → show all (no good local option)
 */
function filterModelsByType(models) {
    const type = ollamaBrowserCurrentType;
    if (!type || type === 'image') return models; // image: no good filter, show all
    return models.filter(function (m) {
        const caps = m.capabilities || [];
        if (caps.length === 0) return false; // no capability info — hide from filtered views
        if (type === 'conversational') return caps.indexOf('tools') >= 0;
        if (type === 'coding') return caps.indexOf('tools') >= 0 || caps.indexOf('code') >= 0;
        if (type === 'vision') return caps.indexOf('vision') >= 0;
        return true;
    });
}

function renderOllamaBrowserList(models) {
    const container = document.getElementById('ollama_browser_list');
    if (!container) return;
    if (!models || models.length === 0) {
        container.innerHTML = '<p class="text-[#9ca3af] text-sm">No matching models found. Try Refresh library or a different search.</p>';
        return;
    }
    const capColors = { tools: '#2d5a27', vision: '#4a3080', code: '#806a20', thinking: '#206080', cloud: '#606060', embedding: '#605020' };
    const rows = models.map(function (m) {
        const name = escapeHtml(m.name || '');
        const caps = (m.capabilities || []).map(function (c) {
            var bg = capColors[c] || '#565869';
            return '<span class="px-1.5 py-0.5 rounded text-[10px] text-white" style="background:' + bg + '">' + escapeHtml(c) + '</span>';
        }).join(' ');
        const sizes = m.sizes || [];
        const installedSizes = m.installed_sizes || [];
        const sizeButtons = sizes.map(function (size) {
            const installed = installedSizes.indexOf(size) >= 0;
            const label = installed ? '✓ ' + size : size;
            const cls = installed
                ? 'px-2 py-1 rounded text-xs bg-[#565869] text-[#9ca3af] cursor-not-allowed ollama-size-btn-installed'
                : 'px-2 py-1 rounded text-xs bg-[#2d5a27] hover:bg-[#3d7a37] text-white cursor-pointer ollama-size-btn';
            return '<button type="button" class="' + cls + '" data-model="' + escapeHtml(m.name || '') + '" data-size="' + escapeHtml(size) + '">' + label + '</button>';
        }).join(' ');
        return '<tr class="ollama-model-row"><td class="ollama-model-name">' + name + (caps ? ' ' + caps : '') + '</td><td class="ollama-model-sizes"><div class="flex flex-wrap gap-2">' + sizeButtons + '</div></td></tr>';
    }).join('');
    container.innerHTML = '<div class="ollama-browser-table-wrap"><table class="ollama-browser-table"><thead><tr><th class="text-left py-2 px-3 text-[#9ca3af] font-medium">Model</th><th class="text-left py-2 px-3 text-[#9ca3af] font-medium">Sizes / Download</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function openOllamaDownloadModal(modelName, size) {
    const modal = document.getElementById('ollama_download_modal');
    if (!modal) return;
    document.getElementById('ollama_download_modal_title').textContent = 'Downloading model';
    document.getElementById('ollama_download_modal_model').textContent = modelName + (size ? ' : ' + size : '');
    document.getElementById('ollama_download_modal_progress').classList.remove('hidden');
    document.getElementById('ollama_download_modal_status').textContent = 'Starting download...';
    document.getElementById('ollama_download_modal_done').classList.add('hidden');
    document.getElementById('ollama_download_modal_close').classList.add('hidden');
    document.getElementById('ollama_download_modal_result').textContent = '';
    document.getElementById('ollama_download_modal_result').className = 'text-sm';
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
}

function closeOllamaDownloadModal() {
    const modal = document.getElementById('ollama_download_modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
}

async function ollamaBrowserPull(modelName, size) {
    openOllamaDownloadModal(modelName, size);
    const statusEl = document.getElementById('ollama_download_modal_status');
    const progressEl = document.getElementById('ollama_download_modal_progress');
    const doneEl = document.getElementById('ollama_download_modal_done');
    const resultEl = document.getElementById('ollama_download_modal_result');
    const closeBtn = document.getElementById('ollama_download_modal_close');
    if (statusEl) statusEl.textContent = 'Downloading ' + modelName + (size ? ':' + size : '') + '...';
    try {
        const body = { model: modelName, size: size || null };
        const response = await fetch('/api/ollama/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json();
        if (progressEl) progressEl.classList.add('hidden');
        if (doneEl) doneEl.classList.remove('hidden');
        if (closeBtn) closeBtn.classList.remove('hidden');
        if (data.success) {
            if (resultEl) {
                resultEl.textContent = data.message || 'Download complete.';
                resultEl.classList.remove('text-red-400');
                resultEl.classList.add('text-[#10a37f]');
            }
            document.getElementById('ollama_download_modal_title').textContent = 'Download complete';
            await fetch('/api/llms/models/reload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider: 'ollama' })
            });
            await fetchOllamaLibraryAndRender();
            if (ollamaBrowserCurrentType) {
                await loadLLMModels(ollamaBrowserCurrentType, 'ollama');
            }
            setTimeout(closeOllamaDownloadModal, 2000);
        } else {
            if (resultEl) {
                resultEl.textContent = data.message || 'Download failed.';
                resultEl.classList.remove('text-[#10a37f]');
                resultEl.classList.add('text-red-400');
            }
            document.getElementById('ollama_download_modal_title').textContent = 'Download failed';
        }
    } catch (e) {
        if (progressEl) progressEl.classList.add('hidden');
        if (doneEl) doneEl.classList.remove('hidden');
        if (closeBtn) closeBtn.classList.remove('hidden');
        if (resultEl) {
            resultEl.textContent = 'Error: ' + (e.message || 'Request failed');
            resultEl.classList.remove('text-[#10a37f]');
            resultEl.classList.add('text-red-400');
        }
        document.getElementById('ollama_download_modal_title').textContent = 'Download failed';
    }
}

function filterOllamaBrowserList() {
    const q = (document.getElementById('ollama_browser_search') || {}).value || '';
    const term = q.trim().toLowerCase();
    var filtered = filterModelsByType(ollamaBrowserModels);
    if (term) {
        filtered = filtered.filter(function (m) {
            return (m.name || '').toLowerCase().indexOf(term) >= 0;
        });
    }
    renderOllamaBrowserList(filtered);
}

function updateDownloadButtonVisibility() {
    ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use'].forEach(function (type) {
        const providerSelect = document.getElementById(type + '_provider');
        const downloadBtn = document.getElementById(type + '_download');
        if (!providerSelect || !downloadBtn) return;
        const provider = (providerSelect.value || '').toLowerCase();
        downloadBtn.style.display = provider === 'ollama' ? '' : 'none';
    });
}

function ensureBenchmarkButtons() {
    // Comparison is launched from the info icon beside the provider select.
}

function ensureBenchmarkModal() {
    if (document.getElementById('llm_benchmark_modal')) return;
    const overlay = document.createElement('div');
    overlay.id = 'llm_benchmark_modal';
    overlay.className = 'llm-benchmark-modal hidden';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<div class="llm-benchmark-modal__backdrop" data-benchmark-close="1"></div>' +
        '<div class="llm-benchmark-modal__dialog">' +
        '<div class="llm-benchmark-modal__content" id="llm_benchmark_modal_content"></div>' +
        '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (event) {
        if (event.target && event.target.getAttribute('data-benchmark-close') === '1') {
            closeBenchmarkModal();
        }
        const tabBtn = event.target.closest('[data-benchmark-tab]');
        if (tabBtn && benchmarkModalState) {
            benchmarkModalState.tab = tabBtn.getAttribute('data-benchmark-tab') || 'leaderboard';
            const currentPayload = benchmarkModalState.payload;
            if (currentPayload) renderBenchmarkModal(currentPayload);
            return;
        }
        const sortBtn = event.target.closest('[data-benchmark-sort]');
        if (sortBtn && benchmarkModalState) {
            openBenchmarkModal(benchmarkModalState.type, {
                model: benchmarkModalState.model,
                compareModel: benchmarkModalState.compareModel,
                sort: sortBtn.getAttribute('data-benchmark-sort'),
                tab: benchmarkModalState.tab,
            });
        }
        const leaderboardRow = event.target.closest('[data-benchmark-compare]');
        if (leaderboardRow && benchmarkModalState) {
            const compareProvider = leaderboardRow.getAttribute('data-benchmark-provider') || benchmarkModalState.compareProvider || benchmarkModalState.provider;
            const compareModel = leaderboardRow.getAttribute('data-benchmark-compare') || '';
            setBenchmarkCompareSelection(compareProvider, compareModel);
        }
    });
}

function setBenchmarkCompareSelection(provider, model) {
    if (!benchmarkModalState) return;
    const modalType = benchmarkModalState.type;
    const selectedModel = (model || '').toString();
    const selectedProvider = (provider || '').toString();
    benchmarkModalState.compareProvider = selectedProvider;
    benchmarkModalState.compareModel = selectedModel;
    benchmarkModalState.tab = 'compare';

    const providerSelect = document.getElementById('llm_benchmark_compare_provider');
    const modelSelect = document.getElementById('llm_benchmark_compare_model');
    if (providerSelect) {
        const canSetProvider = setSelectValueIfAvailable(providerSelect, selectedProvider);
        if (canSetProvider) {
            refreshBenchmarkCardProvider('compare', modalType, selectedProvider, selectedModel);
            if (selectedModel) setActiveLeaderboardCompareRow(selectedModel);
            return;
        }
    }
    if (modelSelect && setSelectValueIfAvailable(modelSelect, selectedModel)) {
        refreshBenchmarkCardProfile('compare', modalType, selectedProvider, selectedModel);
        setActiveLeaderboardCompareRow(selectedModel);
        return;
    }

    if (selectedModel) {
        refreshBenchmarkCardProfile('compare', modalType, selectedProvider, selectedModel);
        setActiveLeaderboardCompareRow(selectedModel);
    }
}

function setSelectValueIfAvailable(selectEl, value) {
    if (!selectEl || value === undefined || value === null) return false;
    const stringValue = String(value);
    const matchingOption = Array.prototype.find.call(selectEl.options || [], function (option) {
        return String(option.value) === stringValue;
    });
    if (!matchingOption) return false;
    selectEl.value = stringValue;
    return true;
}

function setActiveLeaderboardCompareRow(compareModel) {
    if (!benchmarkModalState) return;
    const rows = document.querySelectorAll('[data-benchmark-compare]');
    rows.forEach(function (row) {
        row.classList.toggle('is-active', row.getAttribute('data-benchmark-compare') === compareModel);
    });
}

function closeBenchmarkModal() {
    const modal = document.getElementById('llm_benchmark_modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
}

function benchmarkSelectOptions(payload, selectedId, fallbackRow) {
    const seen = new Set();
    const items = [];
    (payload.model_options || []).forEach(function (row) {
        if (!row || !row.id || seen.has(row.id)) return;
        seen.add(row.id);
        items.push(row);
    });
    if (fallbackRow && fallbackRow.id && !seen.has(fallbackRow.id)) {
        items.push(fallbackRow);
    }
    return items.map(function (row) {
        const selected = row.id === selectedId ? ' selected' : '';
        return '<option value="' + escapeHtml(row.id) + '"' + selected + '>' + escapeHtml(row.label || row.id) + '</option>';
    }).join('');
}

function benchmarkProviderOptions(type, selectedProvider, payload) {
    const sourceSelect = document.getElementById(type + '_provider');
    const seen = new Set();
    const items = [];
    if (sourceSelect) {
        Array.prototype.forEach.call(sourceSelect.options || [], function (option) {
            const value = option.value || '';
            if (seen.has(value)) return;
            seen.add(value);
            items.push({ id: value, name: option.textContent || value || 'Default' });
        });
    }
    ((payload && payload.provider_options) || []).forEach(function (provider) {
        const value = provider && (provider.id || provider.value || '');
        if (!value || seen.has(value)) return;
        seen.add(value);
        items.push({ id: value, name: provider.name || provider.label || value });
    });
    if (selectedProvider && !seen.has(selectedProvider)) {
        items.push({ id: selectedProvider, name: selectedProvider });
    }
    return items.map(function (provider) {
        const selected = provider.id === selectedProvider ? ' selected' : '';
        return '<option value="' + escapeHtml(provider.id || '') + '"' + selected + '>' + escapeHtml(provider.name || provider.id || '') + '</option>';
    }).join('');
}

async function loadBenchmarkProviderOptions(type) {
    try {
        const needsMedia = type === 'image' || type === 'video';
        const [llmRes, mediaRes] = await Promise.all([
            fetch('/api/llms/available-providers'),
            needsMedia ? fetch('/api/llms/available-media-providers') : Promise.resolve(null)
        ]);
        const llmData = llmRes && llmRes.ok ? await llmRes.json() : { providers: [] };
        const mediaData = mediaRes && mediaRes.ok ? await mediaRes.json() : { providers: [] };
        const baseProviders = llmData.providers || [];
        const mediaProviders = mediaData.providers || [];
        if (type === 'video') return mediaProviders;
        if (type === 'image') {
            const seen = new Set(baseProviders.map(function (provider) { return provider.id; }));
            return baseProviders.concat(mediaProviders.filter(function (provider) { return !seen.has(provider.id); }));
        }
        return baseProviders;
    } catch (error) {
        console.error('Error loading benchmark providers:', error);
        return [];
    }
}

function benchmarkProfileCapabilities(profile) {
    const items = (profile && profile.capabilities) || [];
    if (!items.length) return '<span class="llm-benchmark-card__muted">No extra capability metadata</span>';
    return items.map(function (item) {
        return '<span class="llm-benchmark-card__capability">' + escapeHtml(item) + '</span>';
    }).join('');
}

function benchmarkMetricDisplay(value, suffix) {
    if (value === null || value === undefined || value === '' || value === 0 || value === '0') return '';
    return escapeHtml(String(value)) + (suffix ? escapeHtml(suffix) : '');
}

function benchmarkMetricRows(profile) {
    profile = profile || {};
    const metrics = profile.benchmark_metrics || {};
    const pricing = profile.pricing || {};
    const rows = [
        { label: 'Performance score', value: profile.performance_score },
        { label: 'Value score', value: profile.value_score },
        { label: 'Context window', value: metrics.context_window_label || profile.context_window },
        { label: 'Blended price', value: metrics.blended_price_per_1m || pricing.blended_per_1m, suffix: '/1M' },
        { label: 'Input price', value: metrics.input_price_per_1m || pricing.input_per_1m, suffix: '/1M' },
        { label: 'Output price', value: metrics.output_price_per_1m || pricing.output_per_1m, suffix: '/1M' },
        { label: 'Output speed', value: metrics.output_speed_tps, suffix: ' t/s' },
        { label: 'Latency', value: metrics.latency_first_chunk_s, suffix: ' s' },
        { label: 'Response time', value: metrics.end_to_end_response_s, suffix: ' s' },
        { label: 'Kilo completion', value: metrics.completion_percent, suffix: '%' },
        { label: 'Cost per attempt', value: metrics.cost_per_attempt_usd, suffix: ' USD' },
        { label: 'Intelligence index', value: metrics.intelligence_index },
        { label: 'Benchmark count', value: profile.benchmark_count }
    ];
    return rows.filter(function (row) {
        return !(row.value === null || row.value === undefined || row.value === '' || row.value === 0 || row.value === '0');
    }).map(function (row) {
        return '<div><dt>' + escapeHtml(row.label) + '</dt><dd>' + benchmarkMetricDisplay(row.value, row.suffix || '') + '</dd></div>';
    }).join('');
}

function benchmarkSourcesMarkup(profile) {
    const sources = (profile && profile.benchmark_sources) || [];
    if (!sources.length) return '';
    return '<div class="llm-benchmark-card__sources">' + sources.map(function (source) {
        const href = source.detail_url || source.url || '#';
        const rank = source.rank ? ' · #' + source.rank : '';
        return '<a class="llm-benchmark-card__source" href="' + escapeHtml(href) + '" target="_blank" rel="noreferrer">' +
            escapeHtml(source.label || source.id || 'Source') + escapeHtml(rank) +
            '</a>';
    }).join('') + '</div>';
}

function benchmarkModelOptionsFromCatalog(payload) {
    return ((payload && payload.models) || []).map(function (row) {
        return {
            id: (typeof row === 'object' && row != null && row.id != null) ? row.id : row,
            label: (typeof row === 'object' && row != null && row.name != null) ? row.name : (row || '')
        };
    });
}

function renderBenchmarkCardBody(profile) {
    profile = profile || {};
    const metricRows = benchmarkMetricRows(profile);
    return '<p class="llm-benchmark-card__model-name">' + escapeHtml(profile.model_label || profile.model_id || 'Select a model') + '</p>' +
        '<p class="llm-benchmark-card__provider">' + escapeHtml((profile.provider_label || profile.provider || '').toString()) + '</p>' +
        '<p class="llm-benchmark-card__summary">' + escapeHtml(profile.summary || '') + '</p>' +
        (metricRows ? '<dl class="llm-benchmark-card__stats">' + metricRows + '</dl>' : '') +
        benchmarkSourcesMarkup(profile) +
        '<p class="llm-benchmark-card__use-case"><strong>Best for:</strong> ' + escapeHtml(profile.best_for || 'No recommendation available yet') + '</p>' +
        '<div class="llm-benchmark-card__capabilities"><strong>Tooling:</strong> ' + benchmarkProfileCapabilities(profile) + '</div>' +
        '<p class="llm-benchmark-card__footnote">Released: ' + escapeHtml(profile.released || 'n/a') + ' · Last benchmark: ' + escapeHtml(profile.last_benchmark_date || 'n/a') + '</p>';
}

function renderBenchmarkCard(profile, accentClass, cardKey, type, modelOptionsHtml, providerOptionsHtml) {
    profile = profile || {};
    const providerId = cardKey === 'primary' ? 'llm_benchmark_primary_provider' : 'llm_benchmark_compare_provider';
    const modelId = cardKey === 'primary' ? 'llm_benchmark_primary_model' : 'llm_benchmark_compare_model';
    return '<article class="llm-benchmark-card ' + accentClass + '">' +
        '<div class="llm-benchmark-card__row">' +
        '<div class="llm-benchmark-card__select-stack">' +
        '<label class="llm-benchmark-card__select-label">' +
        '<span>Provider</span>' +
        '<select class="llm-benchmark-card__select" id="' + providerId + '" data-benchmark-card="' + escapeHtml(cardKey) + '" data-benchmark-field="provider">' +
        providerOptionsHtml +
        '</select>' +
        '</label>' +
        '<label class="llm-benchmark-card__select-label">' +
        '<span>Model</span>' +
        '<select class="llm-benchmark-card__select" id="' + modelId + '" data-benchmark-card="' + escapeHtml(cardKey) + '" data-benchmark-field="model">' +
        modelOptionsHtml +
        '</select>' +
        '</label>' +
        '</div>' +
        '</div>' +
        '<div class="llm-benchmark-card__body" data-benchmark-profile-body="' + escapeHtml(cardKey) + '">' +
        renderBenchmarkCardBody(profile) +
        '</div>' +
        '</article>';
}

async function loadBenchmarkModelProfile(type, provider, model) {
    const response = await fetch('/api/llms/model-profile?type=' + encodeURIComponent(type) +
        '&provider=' + encodeURIComponent(provider || '') +
        '&model=' + encodeURIComponent(model || ''));
    const payload = response.ok ? await response.json() : null;
    if (!payload || !payload.profile) throw new Error('Failed to load model profile');
    return payload.profile;
}

async function loadBenchmarkProviderModels(type, provider) {
    const response = await fetch('/api/llms/models?type=' + encodeURIComponent(type) +
        '&provider=' + encodeURIComponent(provider || ''));
    const payload = response.ok ? await response.json() : { models: [] };
    return benchmarkModelOptionsFromCatalog(payload);
}

async function refreshBenchmarkCardProfile(cardKey, type, provider, model) {
    const body = document.querySelector('[data-benchmark-profile-body="' + cardKey + '"]');
    if (!body) return;
    body.classList.add('is-loading');
    try {
        const profile = await loadBenchmarkModelProfile(type, provider, model);
        body.innerHTML = renderBenchmarkCardBody(profile);
        if (benchmarkModalState) {
            if (cardKey === 'primary') {
                benchmarkModalState.primaryProvider = provider;
                benchmarkModalState.provider = provider;
                benchmarkModalState.model = model;
            } else {
                benchmarkModalState.compareProvider = provider;
                benchmarkModalState.compareModel = model;
            }
        }
    } catch (error) {
        console.error('Error loading benchmark profile:', error);
        body.innerHTML = '<p class="llm-benchmark-modal__loading llm-benchmark-modal__loading--error">Failed to load model details.</p>';
    } finally {
        body.classList.remove('is-loading');
    }
}

async function refreshBenchmarkCardProvider(cardKey, type, provider, preferredModel) {
    const modelSelect = document.getElementById(cardKey === 'primary' ? 'llm_benchmark_primary_model' : 'llm_benchmark_compare_model');
    if (!modelSelect) return;
    modelSelect.disabled = true;
    try {
        const normalizedModel = (preferredModel || '').toString();
        const modelOptions = await loadBenchmarkProviderModels(type, provider);
        const fallbackModel = normalizedModel ? { id: normalizedModel, label: normalizedModel } : null;
        modelSelect.innerHTML = benchmarkSelectOptions({ model_options: modelOptions }, normalizedModel, fallbackModel);
        const nextModel = normalizedModel && setSelectValueIfAvailable(modelSelect, normalizedModel)
            ? normalizedModel
            : (modelOptions[0] ? modelOptions[0].id : '');
        if (nextModel) modelSelect.value = nextModel;
        await refreshBenchmarkCardProfile(cardKey, type, provider, nextModel);
        if (cardKey === 'compare') setActiveLeaderboardCompareRow(nextModel);
    } catch (error) {
        console.error('Error loading benchmark models:', error);
        const body = document.querySelector('[data-benchmark-profile-body="' + cardKey + '"]');
        if (body) body.innerHTML = '<p class="llm-benchmark-modal__loading llm-benchmark-modal__loading--error">Failed to load provider models.</p>';
    } finally {
        modelSelect.disabled = false;
    }
}

function renderBenchmarkModal(payload) {
    ensureBenchmarkModal();
    const content = document.getElementById('llm_benchmark_modal_content');
    if (!content) return;
    const selected = payload.selected_model || {};
    const comparison = payload.comparison_model || {};
    const primaryProvider = (benchmarkModalState && benchmarkModalState.primaryProvider) || payload.primary_provider || '';
    const compareProvider = (benchmarkModalState && benchmarkModalState.compareProvider) || payload.compare_provider || '';
    const primaryProfile = payload.primary_profile || {};
    const compareProfile = payload.compare_profile || {};
    const sort = payload.sort || 'performance';
    const activeTab = (benchmarkModalState && benchmarkModalState.tab) || 'leaderboard';
    const optionsHtml = benchmarkSelectOptions(payload, selected.id, { id: primaryProfile.model_id || selected.id, label: primaryProfile.model_label || selected.label });
    const comparePayload = Object.assign({}, payload, { model_options: payload.compare_model_options || [] });
    const compareOptionsHtml = benchmarkSelectOptions(comparePayload, comparison.id, { id: compareProfile.model_id || comparison.id, label: compareProfile.model_label || comparison.label });
    const primaryProviderOptions = benchmarkProviderOptions(payload.type, primaryProvider, payload);
    const compareProviderOptions = benchmarkProviderOptions(payload.type, compareProvider, payload);
    if (payload.refreshing && !(payload.leaderboard || []).length) {
        content.innerHTML = '<div class="llm-benchmark-modal__loading">' +
            '<div class="llm-benchmark-modal__spinner" aria-hidden="true"></div>' +
            '<div>Fetching data from sources, you can close this window and come back in a bit.</div>' +
            '</div>';
        return;
    }
    const leaderboard = (payload.leaderboard || []).map(function (row, index) {
        const active = benchmarkModalState && benchmarkModalState.compareModel === row.id ? ' is-active' : '';
        return '<tr class="llm-benchmark-leaderboard__row' + active + '" data-benchmark-compare="' + escapeHtml(row.id || '') + '" data-benchmark-provider="' + escapeHtml(row.provider || '') + '">' +
            '<td class="llm-benchmark-leaderboard__rank">#' + (index + 1) + '</td>' +
            '<td class="llm-benchmark-leaderboard__model">' + escapeHtml(row.label || row.id || '') + '</td>' +
            '<td class="llm-benchmark-leaderboard__score">' + escapeHtml(row.performance_score != null ? String(row.performance_score) : '—') + '</td>' +
            '<td class="llm-benchmark-leaderboard__score">' + escapeHtml(row.value_score != null ? String(row.value_score) : '—') + '</td>' +
            '</tr>';
    }).join('');
    const refreshBanner = payload.refreshing ? '<div class="llm-benchmark-modal__refreshing">Fetching fresh benchmark data from sources in the background. You can close this window and come back in a bit.</div>' : '';
    content.innerHTML = '<header class="llm-benchmark-modal__header">' +
        '<h3 class="llm-benchmark-modal__title">Benchmark information</h3>' +
        '<button type="button" class="llm-benchmark-modal__close" data-benchmark-close="1">×</button>' +
        '</header>' +
        '<div class="llm-benchmark-modal__tabs">' +
        '<button type="button" class="llm-benchmark-modal__tab' + (activeTab === 'leaderboard' ? ' is-active' : '') + '" data-benchmark-tab="leaderboard">Leaderboard</button>' +
        '<button type="button" class="llm-benchmark-modal__tab' + (activeTab === 'compare' ? ' is-active' : '') + '" data-benchmark-tab="compare">Compare</button>' +
        '</div>' +
        refreshBanner +
        '<section class="llm-benchmark-modal__panel' + (activeTab === 'leaderboard' ? ' is-active' : '') + '">' +
        '<p class="llm-benchmark-modal__helper">Performance is the benchmark score. Value is the score adjusted for cost. Click a row to compare it.</p>' +
        '<aside class="llm-benchmark-modal__leaderboard llm-benchmark-modal__leaderboard--full">' +
        '<table class="llm-benchmark-leaderboard__table">' +
        '<thead><tr>' +
        '<th>#</th>' +
        '<th>Model</th>' +
        '<th><button type="button" class="llm-benchmark-leaderboard__sort-button' + (sort === 'performance' ? ' is-active' : '') + '" data-benchmark-sort="performance">Performance</button></th>' +
        '<th><button type="button" class="llm-benchmark-leaderboard__sort-button' + (sort === 'value' ? ' is-active' : '') + '" data-benchmark-sort="value">Value</button></th>' +
        '</tr></thead>' +
        '<tbody>' + leaderboard + '</tbody>' +
        '</table>' +
        '</aside>' +
        '</section>' +
        '<section class="llm-benchmark-modal__panel' + (activeTab === 'compare' ? ' is-active' : '') + '">' +
        '<div class="llm-benchmark-modal__grid">' +
        '<section class="llm-benchmark-modal__compare-column">' +
        renderBenchmarkCard(primaryProfile, 'llm-benchmark-card--primary', 'primary', payload.type, optionsHtml, primaryProviderOptions) +
        '</section>' +
        '<section class="llm-benchmark-modal__compare-column">' +
        renderBenchmarkCard(compareProfile, 'llm-benchmark-card--comparison', 'compare', payload.type, compareOptionsHtml, compareProviderOptions) +
        '</section>' +
        '</div>' +
        '</section>';
    const modal = document.getElementById('llm_benchmark_modal');
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    const primaryModelSelect = document.getElementById('llm_benchmark_primary_model');
    const compareModelSelect = document.getElementById('llm_benchmark_compare_model');
    const primaryProviderSelect = document.getElementById('llm_benchmark_primary_provider');
    const compareProviderSelect = document.getElementById('llm_benchmark_compare_provider');
    if (primaryModelSelect) {
        primaryModelSelect.addEventListener('change', function () {
            refreshBenchmarkCardProfile(
                'primary',
                payload.type,
                primaryProviderSelect ? primaryProviderSelect.value : primaryProvider,
                this.value
            );
        });
    }
    if (compareModelSelect) {
        compareModelSelect.addEventListener('change', function () {
            refreshBenchmarkCardProfile(
                'compare',
                payload.type,
                compareProviderSelect ? compareProviderSelect.value : compareProvider,
                this.value
            );
        });
    }
    if (primaryProviderSelect) {
        primaryProviderSelect.addEventListener('change', function () {
            refreshBenchmarkCardProvider('primary', payload.type, this.value);
        });
    }
    if (compareProviderSelect) {
        compareProviderSelect.addEventListener('change', function () {
            refreshBenchmarkCardProvider('compare', payload.type, this.value);
        });
    }
}

async function openBenchmarkModal(type, opts) {
    opts = opts || {};
    ensureBenchmarkModal();
    const modal = document.getElementById('llm_benchmark_modal');
    const content = document.getElementById('llm_benchmark_modal_content');
    const providerSelect = document.getElementById(type + '_provider');
    const modelSelect = document.getElementById(type + '_model');
    const model = opts.model || (modelSelect ? modelSelect.value : '');
    const provider = opts.provider || (providerSelect ? providerSelect.value : '');
    const compareProvider = opts.compareProvider || provider;
    const compareModel = opts.compareModel || '';
    const sort = opts.sort || (benchmarkModalState && benchmarkModalState.type === type ? benchmarkModalState.sort : 'performance');
    const tab = opts.tab || (benchmarkModalState && benchmarkModalState.type === type ? benchmarkModalState.tab : 'leaderboard');
    benchmarkModalState = {
        type: type,
        provider: provider,
        primaryProvider: provider,
        compareProvider: compareProvider,
        model: model,
        compareModel: compareModel,
        sort: sort,
        tab: tab,
        payload: null
    };
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    content.innerHTML = '<div class="llm-benchmark-modal__loading">' +
        '<div class="llm-benchmark-modal__spinner" aria-hidden="true"></div>' +
        '<div>Loading benchmark data...</div>' +
        '</div>';
    try {
        const [benchmarkResponse, primaryModelsResponse, compareModelsResponse, primaryProfileResponse, compareProfileResponse, providerOptions] = await Promise.all([
            fetch('/api/llms/benchmark?type=' + encodeURIComponent(type) +
                '&provider=' + encodeURIComponent(provider || '') +
                '&model=' + encodeURIComponent(model || '') +
                '&compare_model=' + encodeURIComponent(compareModel || '') +
                '&sort=' + encodeURIComponent(sort) +
                '&limit=40'),
            fetch('/api/llms/models?type=' + encodeURIComponent(type) +
                '&provider=' + encodeURIComponent(provider || '')),
            fetch('/api/llms/models?type=' + encodeURIComponent(type) +
                '&provider=' + encodeURIComponent(compareProvider || '')),
            fetch('/api/llms/model-profile?type=' + encodeURIComponent(type) +
                '&provider=' + encodeURIComponent(provider || '') +
                '&model=' + encodeURIComponent(model || '')),
            fetch('/api/llms/model-profile?type=' + encodeURIComponent(type) +
                '&provider=' + encodeURIComponent(compareProvider || '') +
                '&model=' + encodeURIComponent(compareModel || '')),
            loadBenchmarkProviderOptions(type)
        ]);
        const payload = benchmarkResponse.ok ? await benchmarkResponse.json() : null;
        const primaryCatalogPayload = primaryModelsResponse.ok ? await primaryModelsResponse.json() : { models: [] };
        const compareCatalogPayload = compareModelsResponse.ok ? await compareModelsResponse.json() : { models: [] };
        const primaryProfilePayload = primaryProfileResponse.ok ? await primaryProfileResponse.json() : { profile: null };
        const compareProfilePayload = compareProfileResponse.ok ? await compareProfileResponse.json() : { profile: null };
        if (!payload) throw new Error('Failed to load benchmark data');
        payload.model_options = benchmarkModelOptionsFromCatalog(primaryCatalogPayload);
        payload.compare_model_options = benchmarkModelOptionsFromCatalog(compareCatalogPayload);
        payload.primary_profile = primaryProfilePayload.profile || payload.selected_model || {};
        payload.compare_profile = compareProfilePayload.profile || payload.comparison_model || {};
        payload.provider_options = providerOptions || [];
        payload.primary_provider = provider;
        payload.compare_provider = compareProvider;
        benchmarkModalState.model = (payload.selected_model || {}).id || model;
        benchmarkModalState.compareModel = (payload.comparison_model || {}).id || compareModel;
        benchmarkModalState.primaryProvider = provider;
        benchmarkModalState.compareProvider = compareProvider;
        benchmarkModalState.sort = payload.sort || sort;
        benchmarkModalState.payload = payload;
        renderBenchmarkModal(payload);
    } catch (error) {
        console.error('Error loading benchmark modal:', error);
        content.innerHTML = '<div class="llm-benchmark-modal__loading llm-benchmark-modal__loading--error">Failed to load benchmark data.</div>';
    }
}

function initOllamaBrowserModal() {
    const modal = document.getElementById('ollama_browser_modal');
    if (!modal) return;
    document.getElementById('ollama_browser_modal_close').addEventListener('click', closeOllamaBrowserModal);
    const downloadModalClose = document.getElementById('ollama_download_modal_close');
    const downloadModalBackdrop = document.querySelector('.ollama_download_modal_backdrop');
    if (downloadModalClose) downloadModalClose.addEventListener('click', closeOllamaDownloadModal);
    document.getElementById('ollama_browser_refresh').addEventListener('click', function () {
        fetch('/api/ollama/refresh-library', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.success) {
                showNotification('Library refreshed', 'success');
                fetchOllamaLibraryAndRender();
            } else {
                showNotification(data.message || 'Refresh failed', 'error');
            }
        }).catch(function (e) {
            showNotification('Refresh failed: ' + (e.message || ''), 'error');
        });
    });
    document.getElementById('ollama_browser_search').addEventListener('input', filterOllamaBrowserList);
    document.getElementById('ollama_browser_list').addEventListener('click', function (e) {
        const btn = e.target.closest('.ollama-size-btn');
        if (!btn) return;
        const model = btn.getAttribute('data-model');
        const size = btn.getAttribute('data-size');
        if (model) ollamaBrowserPull(model, size || null);
    });
}

function initLLMEnhancements() {
    initLLMSubtabs();
    ensureProviderStatusPills();
    ensureProjectCliStatusPills();
    ensureBenchmarkButtons();
    ensureBenchmarkModal();
    const { saveBtn } = _getLLMActionButtons();
    if (saveBtn && saveBtn.id === 'llms_inline_save' && saveBtn.dataset.bound !== '1') {
        saveBtn.dataset.bound = '1';
        saveBtn.addEventListener('click', function () {
            saveLLMsSettings();
        });
    }
}

function setActiveLLMSubtab(tabName) {
    activeLlmSubtab = tabName || 'speech';
    document.querySelectorAll('[data-llm-subtab]').forEach(function (button) {
        const isActive = button.getAttribute('data-llm-subtab') === activeLlmSubtab;
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    document.querySelectorAll('[data-llm-panel]').forEach(function (panel) {
        panel.classList.toggle('is-active', panel.getAttribute('data-llm-panel') === activeLlmSubtab);
    });
}

function initLLMSubtabs() {
    const buttons = document.querySelectorAll('[data-llm-subtab]');
    if (!buttons.length) return;
    buttons.forEach(function (button) {
        if (button.dataset.llmTabBound === '1') return;
        button.dataset.llmTabBound = '1';
        button.addEventListener('click', function () {
            setActiveLLMSubtab(button.getAttribute('data-llm-subtab') || 'speech');
        });
    });
    setActiveLLMSubtab(activeLlmSubtab);
}

// Initialize LLMs settings when DOM is loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        if (document.getElementById('tab-llms')) {
            initLLMEnhancements();
            loadLLMsSettings();
            updateDownloadButtonVisibility();
            initOllamaBrowserModal();

            const llmTypes = LLM_TYPES;
            llmTypes.forEach(type => {
                const providerSelect = document.getElementById(`${type}_provider`);
                if (providerSelect) {
                    providerSelect.addEventListener('change', function() {
                        if (this.value) {
                            loadLLMModels(type, this.value, { notifyOnMissingProviderKey: true });
                        } else {
                            // Empty provider — clear model dropdown
                            const modelSelect = document.getElementById(`${type}_model`);
                            if (modelSelect) modelSelect.innerHTML = '<option value="">Select model...</option>';
                        }
                        updateDownloadButtonVisibility();
                    });
                }

                const modelSelect = document.getElementById(`${type}_model`);
                if (modelSelect && type === 'conversational') {
                    modelSelect.addEventListener('change', function() {
                        refreshS2sLocksUi(this.value);
                    });
                }

                const downloadBtn = document.getElementById(`${type}_download`);
                if (downloadBtn) {
                    downloadBtn.addEventListener('click', function() {
                        openOllamaModelBrowser(type);
                    });
                }
            });
            bindProjectCliRouteControls();
        }
    });
} else {
    if (document.getElementById('tab-llms')) {
        initLLMEnhancements();
        loadLLMsSettings();
        updateDownloadButtonVisibility();
        initOllamaBrowserModal();

        const llmTypes = LLM_TYPES;
        llmTypes.forEach(type => {
            const providerSelect = document.getElementById(`${type}_provider`);
            if (providerSelect) {
                providerSelect.addEventListener('change', function() {
                    if (this.value) {
                        loadLLMModels(type, this.value, { notifyOnMissingProviderKey: true });
                    } else {
                        const modelSelect = document.getElementById(`${type}_model`);
                        if (modelSelect) modelSelect.innerHTML = '<option value="">Select model...</option>';
                    }
                    updateDownloadButtonVisibility();
                });
            }

            const modelSelect = document.getElementById(`${type}_model`);
            if (modelSelect && type === 'conversational') {
                modelSelect.addEventListener('change', function() {
                    refreshS2sLocksUi(this.value);
                });
            }

            const downloadBtn = document.getElementById(`${type}_download`);
            if (downloadBtn) {
                downloadBtn.addEventListener('click', function() {
                    openOllamaModelBrowser(type);
                });
            }
        });
        bindProjectCliRouteControls();
    }
}

// Export functions for use in other files
window.loadLLMsSettings = loadLLMsSettings;
window.reloadLLMsSettings = reloadLLMsSettings;
window.refreshS2sLocksUi = refreshS2sLocksUi;
window.openLLMBenchmarkModal = openBenchmarkModal;

// Auto-refresh provider dropdowns when third-party keys are saved (same page)
window.addEventListener('thirdparty-providers-changed', function() {
    console.log('Third-party providers changed — refreshing LLM provider dropdowns');
    loadLLMsSettings();
});

// Same-origin tabs: Ticket Boards global save POSTs /api/llms — reload this tab when that happens
(function initLlmsBroadcastSync() {
    if (typeof BroadcastChannel === 'undefined') return;
    try {
        var bc = new BroadcastChannel('decisions_llms_settings_sync');
        bc.onmessage = function () {
            if (document.getElementById('tab-llms')) loadLLMsSettings();
        };
        window._decisionsLlmsSyncBc = bc;
    } catch (e) {}
})();

window.addEventListener('pageshow', function (ev) {
    if (!ev.persisted || !document.getElementById('tab-llms')) return;
    var panel = document.getElementById('tab-llms');
    if (panel && panel.classList.contains('active-tab-panel')) loadLLMsSettings();
});

window.addEventListener('open-llm-benchmark', function (event) {
    const type = event && event.detail ? event.detail.type : '';
    if (!type) return;
    openBenchmarkModal(type, {
        provider: event.detail.provider || '',
        model: event.detail.model || '',
        compareProvider: event.detail.compareProvider || '',
        compareModel: event.detail.compareModel || ''
    });
});

window.saveLLMsSettings = saveLLMsSettings;
