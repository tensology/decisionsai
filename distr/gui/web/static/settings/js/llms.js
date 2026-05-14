// LLMs Settings JavaScript

// Populate provider dropdowns from available providers (Third-Party configured only)
async function populateProviderDropdowns() {
    try {
        const response = await fetch('/api/llms/available-providers');
        const data = response.ok ? await response.json() : { providers: [] };
        const providers = data.providers || [];
        const llmTypes = ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use', 'kanban'];
        const optionalTypes = ['workflow', 'computer_use', 'kanban'];
        const emptyLabels = {
            'workflow': 'Inherit from Conversational',
            'computer_use': 'Disabled (accessibility tree only)',
            'kanban': 'Inherit from Conversational',
        };
        for (const type of llmTypes) {
            const sel = document.getElementById(`${type}_provider`);
            if (!sel) continue;
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

// Load LLMs settings from backend
async function loadLLMsSettings() {
    try {
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

        const llmTypes = ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use', 'kanban'];
        const optionalTypes = ['workflow', 'computer_use', 'kanban'];
        for (const type of llmTypes) {
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
        }

        updateDownloadButtonVisibility();
        console.log('LLMs settings loaded');
    } catch (error) {
        console.error('Error loading LLMs settings:', error);
        showNotification('Failed to load LLMs settings: ' + error.message, 'error');
    }
}

// Save LLMs settings to backend
async function saveLLMsSettings() {
    try {
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
            workflow_provider: document.getElementById('workflow_provider').value,
            workflow_model: document.getElementById('workflow_model').value,
            computer_use_provider: document.getElementById('computer_use_provider').value,
            computer_use_model: document.getElementById('computer_use_model').value,
            kanban_provider: document.getElementById('kanban_provider').value,
            kanban_model: document.getElementById('kanban_model').value,
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
    }
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

        const response = await fetch(`/api/llms/models?type=${encodeURIComponent(type)}&provider=${encodeURIComponent(provider)}`);
        if (!response.ok) {
            throw new Error('Failed to load models');
        }
        const data = await response.json();
        const models = data.models || [];

        modelSelect.innerHTML = '<option value="">Select model...</option>';

        if (models.length === 0) {
            if (provider !== 'ollama') {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = `Configure ${provider} API key in Third Party tab`;
                option.disabled = true;
                modelSelect.appendChild(option);
                if (notifyOnMissingProviderKey) {
                    showNotification(`${provider} API key not configured. Go to Third Party tab.`, 'warning');
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
        const typeLabels = { conversational: 'Conversational', coding: 'Coding', vision: 'Vision', image: 'Image', workflow: 'Workflow', computer_use: 'Computer Use', kanban: 'Ticket Board Sub-agent' };
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
    ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use', 'kanban'].forEach(function (type) {
        const providerSelect = document.getElementById(type + '_provider');
        const downloadBtn = document.getElementById(type + '_download');
        if (!providerSelect || !downloadBtn) return;
        const provider = (providerSelect.value || '').toLowerCase();
        downloadBtn.style.display = provider === 'ollama' ? '' : 'none';
    });
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

// Initialize LLMs settings when DOM is loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        if (document.getElementById('tab-llms')) {
            loadLLMsSettings();
            updateDownloadButtonVisibility();
            initOllamaBrowserModal();

            const llmTypes = ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use', 'kanban'];
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

                const downloadBtn = document.getElementById(`${type}_download`);
                if (downloadBtn) {
                    downloadBtn.addEventListener('click', function() {
                        openOllamaModelBrowser(type);
                    });
                }
            });
        }
    });
} else {
    if (document.getElementById('tab-llms')) {
        loadLLMsSettings();
        updateDownloadButtonVisibility();
        initOllamaBrowserModal();

        const llmTypes = ['conversational', 'coding', 'vision', 'image', 'workflow', 'computer_use', 'kanban'];
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

            const downloadBtn = document.getElementById(`${type}_download`);
            if (downloadBtn) {
                downloadBtn.addEventListener('click', function() {
                    openOllamaModelBrowser(type);
                });
            }
        });
    }
}

// Export functions for use in other files
window.loadLLMsSettings = loadLLMsSettings;

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

window.saveLLMsSettings = saveLLMsSettings;
