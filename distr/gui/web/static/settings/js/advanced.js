// Advanced Settings JavaScript - Directory tree like native CheckableDirModel (expand ~/, checkboxes = indexed_folders)

var advancedCheckedPaths = new Set();
var advancedHermesSettings = {
    hermes_enabled: true,
    hermes_memory_export_enabled: false,
    hermes_orchestrator_provider: '',
    hermes_orchestrator_model: '',
    hermes_validator_provider: '',
    hermes_validator_model: '',
    hermes_correction_provider: '',
    hermes_correction_model: ''
};

function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function isPathChecked(path) {
    return advancedCheckedPaths.has(path);
}

function setPathChecked(path, checked) {
    if (checked) {
        advancedCheckedPaths.add(path);
    } else {
        advancedCheckedPaths.delete(path);
    }
}

/** True if any path in advancedCheckedPaths is a strict descendant of folderPath. */
function isAnyDescendantChecked(folderPath) {
    var found = false;
    advancedCheckedPaths.forEach(function (p) {
        if (p === folderPath || p.length <= folderPath.length) return;
        if (p.substring(0, folderPath.length) !== folderPath) return;
        var next = p[folderPath.length];
        if (next === '/' || next === '\\') found = true;
    });
    return found;
}

/** Walk the tree and set indeterminate on checkboxes where node is unchecked but some descendant is checked. */
function refreshIndeterminateState() {
    var treeEl = document.getElementById('directory_tree');
    if (!treeEl) return;
    treeEl.querySelectorAll('.dir-node').forEach(function (nodeEl) {
        var path = nodeEl.getAttribute('data-path');
        var cb = nodeEl.querySelector('.dir-tree-cb');
        if (!path || !cb) return;
        cb.indeterminate = !isPathChecked(path) && isAnyDescendantChecked(path);
    });
}

function checkDescendants(nodeEl, checked) {
    var children = nodeEl.querySelector('.dir-children');
    if (!children) return;
    children.querySelectorAll('.dir-node').forEach(function (childNode) {
        var path = childNode.getAttribute('data-path');
        if (path) {
            setPathChecked(path, checked);
            var cb = childNode.querySelector('.dir-tree-cb');
            if (cb) cb.checked = checked;
        }
        checkDescendants(childNode, checked);
    });
}

function renderTreeNode(container, path, name, depth) {
    var depthPx = (depth || 0) * 16;
    var isChecked = isPathChecked(path);
    var row = document.createElement('div');
    row.className = 'dir-node';
    row.setAttribute('data-path', path);

    var rowLine = document.createElement('div');
    rowLine.className = 'dir-row-line flex items-center gap-1 py-0.5 pr-2 hover:bg-[#565869] rounded cursor-pointer select-none';
    rowLine.style.paddingLeft = (depthPx + 4) + 'px';
    rowLine.innerHTML =
        '<span class="dir-toggle w-4 shrink-0 text-[#9ca3af] cursor-pointer inline-flex items-center justify-center" title="Expand">&#9654;</span>' +
        '<input type="checkbox" class="dir-tree-cb w-4 h-4 shrink-0 rounded border-[#565869] bg-[#1a1f3a] text-[#10a37f] focus:ring-[#10a37f] cursor-pointer" ' + (isChecked ? 'checked' : '') + '>' +
        '<span class="dir-name truncate flex-1" title="' + escapeHtml(path) + '">' + escapeHtml(name) + '</span>';

    var childrenWrap = document.createElement('div');
    childrenWrap.className = 'dir-children hidden pl-0';

    row.appendChild(rowLine);
    row.appendChild(childrenWrap);

    var toggle = row.querySelector('.dir-toggle');
    var cb = row.querySelector('.dir-tree-cb');
    cb.indeterminate = !isChecked && isAnyDescendantChecked(path);

    toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        var expanded = row.getAttribute('data-expanded') === '1';
        if (!expanded) {
            if (!row.getAttribute('data-children-loaded')) {
                loadTreeChildren(path, childrenWrap, (depth || 0) + 1);
                row.setAttribute('data-children-loaded', '1');
            }
            childrenWrap.classList.remove('hidden');
            toggle.classList.add('dir-expanded');
            row.setAttribute('data-expanded', '1');
        } else {
            childrenWrap.classList.add('hidden');
            toggle.classList.remove('dir-expanded');
            row.setAttribute('data-expanded', '0');
        }
    });

    cb.addEventListener('change', function (e) {
        e.stopPropagation();
        var checked = cb.checked;
        setPathChecked(path, checked);
        checkDescendants(row, checked);
        refreshIndeterminateState();
    });

    cb.addEventListener('click', function (e) {
        e.stopPropagation();
    });

    container.appendChild(row);
    return row;
}

function loadTreeChildren(path, container, depth) {
    container.innerHTML = '<div class="py-1 px-4 text-[#9ca3af] text-sm">Loading...</div>';
    fetch('/api/advanced/directories/children?path=' + encodeURIComponent(path))
        .then(function (r) { return r.json(); })
        .then(function (data) {
            container.innerHTML = '';
            var children = data.children || [];
            children.forEach(function (child) {
                renderTreeNode(container, child.path, child.name, depth || 0);
            });
            refreshIndeterminateState();
        })
        .catch(function () {
            container.innerHTML = '<div class="py-1 px-4 text-red-400 text-sm">Failed to load</div>';
        });
}

function loadTreeRoot() {
    var treeEl = document.getElementById('directory_tree');
    if (!treeEl) return;
    treeEl.innerHTML = '<p class="text-[#9ca3af] py-2">Loading...</p>';
    fetch('/api/advanced/directories/root')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            treeEl.innerHTML = '';
            var children = data.children || [];
            children.forEach(function (child) {
                renderTreeNode(treeEl, child.path, child.name, 0);
            });
            refreshIndeterminateState();
            if (children.length === 0) {
                treeEl.innerHTML = '<p class="text-[#9ca3af] py-2">No directories in home</p>';
            }
        })
        .catch(function () {
            treeEl.innerHTML = '<p class="text-red-400 py-2">Failed to load directory tree</p>';
        });
}

function getCheckedPaths() {
    // Filter out paths whose parent/ancestor is already checked —
    // if /Users/paul/dev is checked, don't also send /Users/paul/dev/src
    var all = Array.from(advancedCheckedPaths).sort();
    var result = [];
    for (var i = 0; i < all.length; i++) {
        var dominated = false;
        for (var j = 0; j < result.length; j++) {
            var ancestor = result[j];
            if (all[i].length > ancestor.length &&
                all[i].substring(0, ancestor.length) === ancestor &&
                (all[i][ancestor.length] === '/' || all[i][ancestor.length] === '\\')) {
                dominated = true;
                break;
            }
        }
        if (!dominated) {
            result.push(all[i]);
        }
    }
    return result;
}

function setAdvancedFieldValue(id, value) {
    var el = document.getElementById(id);
    if (el) el.value = value || '';
}

function setAdvancedCheckbox(id, checked) {
    var el = document.getElementById(id);
    if (el) el.checked = !!checked;
}

function getAdvancedFieldValue(id) {
    var el = document.getElementById(id);
    return el ? el.value : '';
}

function getAdvancedCheckbox(id) {
    var el = document.getElementById(id);
    return el ? !!el.checked : false;
}

function rememberAdvancedHermesSettings(settings) {
    advancedHermesSettings.hermes_enabled = settings.hermes_enabled !== false;
    advancedHermesSettings.hermes_memory_export_enabled = !!settings.hermes_memory_export_enabled;
    advancedHermesSettings.hermes_orchestrator_provider = settings.hermes_orchestrator_provider || '';
    advancedHermesSettings.hermes_orchestrator_model = settings.hermes_orchestrator_model || '';
    advancedHermesSettings.hermes_validator_provider = settings.hermes_validator_provider || '';
    advancedHermesSettings.hermes_validator_model = settings.hermes_validator_model || '';
    advancedHermesSettings.hermes_correction_provider = settings.hermes_correction_provider || '';
    advancedHermesSettings.hermes_correction_model = settings.hermes_correction_model || '';
}

// Load advanced settings from backend
async function loadAdvancedSettings() {
    try {
        var response = await fetch('/api/advanced');
        if (!response.ok) throw new Error('Failed to load advanced settings');
        var settings = await response.json();

        document.getElementById('exclude_types').value = settings.exclude_types || '';
        rememberAdvancedHermesSettings(settings);
        advancedCheckedPaths = new Set(settings.indexed_folders || []);
        loadTreeRoot();

        console.log('Advanced settings loaded');
        updateConnectionStatus();
        var params = new URLSearchParams(window.location.search);
        if (params.get('google') === 'connected') {
            if (typeof window.showNotification === 'function') window.showNotification('Google connected successfully', 'success');
            if (window.history && window.history.replaceState) window.history.replaceState({}, '', window.location.pathname + window.location.hash);
        } else if (params.get('google') === 'error') {
            if (typeof window.showNotification === 'function') window.showNotification('Google connection failed', 'error');
            if (window.history && window.history.replaceState) window.history.replaceState({}, '', window.location.pathname + window.location.hash);
        }
    } catch (error) {
        console.error('Error loading advanced settings:', error);
        if (typeof window.showNotification === 'function') window.showNotification('Failed to load advanced settings: ' + error.message, 'error');
    }
}

// Save advanced settings (indexed_folders = checked paths in tree, exclude_types -> excluded_files)
async function saveAdvancedSettings() {
    try {
        var settings = {
            indexed_folders: getCheckedPaths(),
            exclude_types: document.getElementById('exclude_types').value
        };
        Object.assign(settings, advancedHermesSettings);

        var response = await fetch('/api/advanced', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            var err = await response.json();
            throw new Error(err.detail || 'Failed to save advanced settings');
        }

        var result = await response.json();
        if (typeof window.showNotification === 'function') window.showNotification('Advanced settings saved', 'success');
        console.log('Advanced settings saved:', result);
    } catch (error) {
        console.error('Error saving advanced settings:', error);
        if (typeof window.showNotification === 'function') window.showNotification('Failed to save advanced settings: ' + error.message, 'error');
    }
}

async function reindexModels() {
    try {
        if (typeof window.showNotification === 'function') window.showNotification('Reindexing models... This may take a while.', 'info');
        var response = await fetch('/api/advanced/reindex', { method: 'POST' });
        if (!response.ok) {
            var err = await response.json();
            throw new Error(err.detail || 'Failed to reindex');
        }
        var result = await response.json();
        var msg = result.message || 'Models reindexed successfully';
        if (typeof window.showNotification === 'function') window.showNotification(msg, 'success');
        console.log('Reindex result:', result);
    } catch (error) {
        console.error('Error reindexing:', error);
        if (typeof window.showNotification === 'function') window.showNotification('Failed to reindex: ' + error.message, 'error');
    }
}

var settingsBase = '';

function updateConnectionStatus() {
    fetch(settingsBase + '/api/advanced/connection-status').then(function (r) { return r.json(); }).then(function (data) {
        /* Shared shell matches advanced.html: flex chip, min width, no shrink (Discord/Slack stay full buttons after fetch). */
        var shell = 'advanced-integration-btn px-6 py-2.5 rounded-md text-sm font-medium transition-colors';
        var filledBorder = ' border border-white/15';
        var connectedClass = shell + ' bg-[#4CAF50] hover:bg-[#45a049] text-white' + filledBorder;
        var googleBtn = document.getElementById('google_connect_btn');
        var telegramBtn = document.getElementById('telegram_connect_btn');
        var trelloBtn = document.getElementById('trello_connect_btn');
        var jiraBtn = document.getElementById('jira_connect_btn');
        var clickupBtn = document.getElementById('clickup_connect_btn');
        var mondayBtn = document.getElementById('monday_connect_btn');
        if (googleBtn) {
            googleBtn.className = data.google_connected ? connectedClass : shell + ' bg-[#007bff] hover:bg-[#0069d9] text-white' + filledBorder;
            googleBtn.innerHTML = data.google_connected ? '✓ Google' : 'Google';
        }
        var whatsappBtn = document.getElementById('whatsapp_connect_btn');
        if (whatsappBtn) {
            whatsappBtn.className = data.whatsapp_connected ? connectedClass : shell + ' border-2 border-[#25D366] text-[#25D366] hover:bg-[#25D366] hover:text-white';
            whatsappBtn.innerHTML = data.whatsapp_connected ? '\u2713 WhatsApp' : 'WhatsApp';
        }
        if (telegramBtn) {
            telegramBtn.className = data.telegram_connected ? connectedClass : shell + ' bg-[#0088cc] hover:bg-[#0077b3] text-white' + filledBorder;
            telegramBtn.innerHTML = data.telegram_connected ? '✓ Telegram' : 'Telegram';
        }
        if (trelloBtn) {
            trelloBtn.className = data.trello_has_valid ? connectedClass : shell + ' bg-[#0079BF] hover:bg-[#026aa7] text-white' + filledBorder;
            trelloBtn.innerHTML = data.trello_has_valid ? '✓ Trello' : 'Trello';
        }
        if (jiraBtn) {
            jiraBtn.className = data.jira_has_valid ? connectedClass : shell + ' bg-[#0052CC] hover:bg-[#0043a8] text-white' + filledBorder;
            jiraBtn.innerHTML = data.jira_has_valid ? '✓ Jira' : 'Jira';
        }
        var discordBtn = document.getElementById('discord_connect_btn');
        if (discordBtn) {
            discordBtn.className = data.discord_bot_configured ? connectedClass : shell + ' bg-[#5865F2] hover:bg-[#4752c4] text-white' + filledBorder;
            discordBtn.innerHTML = data.discord_bot_configured ? '\u2713 Discord' : 'Discord';
        }
        var slackBtn = document.getElementById('slack_connect_btn');
        if (slackBtn) {
            var slackOk = !!(data.slack_bot_configured || data.slack_signing_configured);
            slackBtn.className = slackOk ? connectedClass : shell + ' bg-[#4A154B] hover:bg-[#611f69] text-white' + filledBorder;
            slackBtn.innerHTML = slackOk ? '\u2713 Slack' : 'Slack';
        }
        if (clickupBtn) {
            clickupBtn.className = data.clickup_configured ? connectedClass : shell + ' bg-[#7B68EE] hover:bg-[#6754d8] text-white' + filledBorder;
            clickupBtn.innerHTML = data.clickup_configured ? '\u2713 ClickUp' : 'ClickUp';
        }
        if (mondayBtn) {
            mondayBtn.className = data.monday_configured ? connectedClass : shell + ' bg-[#00C875] hover:bg-[#00a862] text-white' + filledBorder;
            mondayBtn.innerHTML = data.monday_configured ? '\u2713 Monday' : 'Monday';
        }
    }).catch(function () {});
}

function loadIntegrationConnectorModal() {
    var modal = document.getElementById('integration_connectors_modal');
    if (!modal) return;
    var statusEl = document.getElementById('integration_connectors_status');
    if (statusEl) {
        statusEl.textContent = '';
        statusEl.className = 'text-sm min-h-[1.25rem]';
    }
    fetch(settingsBase + '/api/advanced/integration-connectors').then(function (r) { return r.json(); }).then(function (data) {
        var dIn = document.getElementById('integration_discord_token');
        var sbIn = document.getElementById('integration_slack_bot_token');
        var sgIn = document.getElementById('integration_slack_signing_secret');
        var cuIn = document.getElementById('integration_clickup_token');
        var moIn = document.getElementById('integration_monday_token');
        var hint = document.getElementById('integration_connectors_env_hint');
        if (dIn) {
            dIn.value = data.discord_bot_token || '';
            dIn.disabled = !!data.discord_from_env;
        }
        if (sbIn) {
            sbIn.value = data.slack_bot_token || '';
            sbIn.disabled = !!data.slack_bot_from_env;
        }
        if (sgIn) {
            sgIn.value = data.slack_signing_secret || '';
            sgIn.disabled = !!data.slack_signing_from_env;
        }
        if (cuIn) {
            cuIn.value = data.clickup_api_token || '';
            cuIn.disabled = false;
        }
        if (moIn) {
            moIn.value = data.monday_api_token || '';
            moIn.disabled = false;
        }
        if (hint) {
            var parts = [];
            if (data.discord_from_env) parts.push('Discord: env DECISIONSAI_DISCORD_BOT_TOKEN is set.');
            if (data.slack_bot_from_env) parts.push('Slack bot: env DECISIONSAI_SLACK_BOT_TOKEN is set.');
            if (data.slack_signing_from_env) parts.push('Slack signing: env DECISIONSAI_SLACK_SIGNING_SECRET is set.');
            if (parts.length) {
                hint.textContent = parts.join(' ');
                hint.classList.remove('hidden');
            } else {
                hint.textContent = '';
                hint.classList.add('hidden');
            }
        }
    }).catch(function () {
        if (statusEl) {
            statusEl.textContent = 'Could not load saved credentials.';
            statusEl.className = 'text-sm text-red-400 min-h-[1.25rem]';
        }
    });
}

function openIntegrationConnectorsModal() {
    var modal = document.getElementById('integration_connectors_modal');
    if (!modal) return;
    loadIntegrationConnectorModal();
    modal.classList.remove('hidden');
    modal.querySelectorAll('.integration_connectors_modal_close').forEach(function (btn) {
        btn.onclick = function () { modal.classList.add('hidden'); };
    });
    var bd = modal.querySelector('.integration_connectors_modal_backdrop');
    if (bd) bd.onclick = function () { modal.classList.add('hidden'); };
    var saveBtn = document.getElementById('integration_connectors_save_btn');
    if (saveBtn) {
        saveBtn.onclick = function () {
            var statusEl = document.getElementById('integration_connectors_status');
            var payload = {
                discord_bot_token: (document.getElementById('integration_discord_token') || {}).value || '',
                slack_bot_token: (document.getElementById('integration_slack_bot_token') || {}).value || '',
                slack_signing_secret: (document.getElementById('integration_slack_signing_secret') || {}).value || '',
                clickup_api_token: (document.getElementById('integration_clickup_token') || {}).value || '',
                monday_api_token: (document.getElementById('integration_monday_token') || {}).value || ''
            };
            if (statusEl) {
                statusEl.textContent = 'Saving...';
                statusEl.className = 'text-sm text-[#9ca3af] min-h-[1.25rem]';
            }
            fetch(settingsBase + '/api/advanced/integration-connectors', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function (r) { return r.json(); }).then(function (res) {
                if (res.success) {
                    if (statusEl) {
                        statusEl.textContent = res.restart_note || 'Saved.';
                        statusEl.className = 'text-sm text-green-400 min-h-[1.25rem]';
                    }
                    if (typeof window.showNotification === 'function') window.showNotification('Integration settings saved', 'success');
                    updateConnectionStatus();
                    loadIntegrationConnectorModal();
                } else {
                    if (statusEl) {
                        statusEl.textContent = res.error || 'Save failed';
                        statusEl.className = 'text-sm text-red-400 min-h-[1.25rem]';
                    }
                }
            }).catch(function () {
                if (statusEl) {
                    statusEl.textContent = 'Save failed';
                    statusEl.className = 'text-sm text-red-400 min-h-[1.25rem]';
                }
            });
        };
    }
}

function connectGoogle() {
    // Check if already connected — offer disconnect instead
    var btn = document.getElementById('google_connect_btn');
    if (btn && btn.textContent.indexOf('✓') !== -1) {
        window.DecisionsAPI.confirm({
            title: "Disconnect Google",
            message: "Disconnect Google? This will remove your tokens and OAuth config.",
            confirmLabel: "Disconnect",
            danger: true,
            onConfirm: function() {
                fetch(settingsBase + '/api/advanced/google/disconnect', { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            if (typeof window.showNotification === 'function') window.showNotification('Google disconnected', 'success');
                            updateConnectionStatus();
                        } else {
                            if (typeof window.showNotification === 'function') window.showNotification(data.error || 'Disconnect failed', 'error');
                        }
                    })
                    .catch(function () {
                        if (typeof window.showNotification === 'function') window.showNotification('Disconnect failed', 'error');
                    });
            }
        });
        return;
    }
    fetch(settingsBase + '/api/advanced/google/oauth-url').then(function (r) { return r.json(); }).then(function (data) {
        if (data.needs_config) {
            openGoogleSetupModal(data.javascript_origin, data.redirect_uri);
            return;
        }
        if (data.error) {
            if (typeof window.showNotification === 'function') window.showNotification(data.error, 'error');
            return;
        }
        if (data.url) window.location.href = data.url;
    }).catch(function (e) {
        if (typeof window.showNotification === 'function') window.showNotification('Failed to get OAuth URL', 'error');
    });
}

function openGoogleSetupModal(jsOrigin, redirectUri) {
    var modal = document.getElementById('google_setup_modal');
    if (!modal) return;
    document.getElementById('google_js_origin').textContent = jsOrigin || '';
    document.getElementById('google_redirect_uri').textContent = redirectUri || '';
    var statusEl = document.getElementById('google_upload_status');
    statusEl.className = 'text-sm mt-2 hidden';
    statusEl.textContent = '';
    modal.classList.remove('hidden');

    modal.querySelectorAll('.google_modal_close').forEach(function (btn) { btn.onclick = function () { modal.classList.add('hidden'); }; });

    var dropZone = document.getElementById('google_drop_zone');
    var fileInput = document.getElementById('google_file_input');

    dropZone.onclick = function () { fileInput.click(); };
    dropZone.ondragover = function (e) { e.preventDefault(); dropZone.style.borderColor = '#4a9eff'; };
    dropZone.ondragleave = function () { dropZone.style.borderColor = ''; };
    dropZone.ondrop = function (e) {
        e.preventDefault();
        dropZone.style.borderColor = '';
        if (e.dataTransfer.files.length) handleGoogleConfigFile(e.dataTransfer.files[0]);
    };
    fileInput.onchange = function () { if (fileInput.files.length) handleGoogleConfigFile(fileInput.files[0]); };
}

function handleGoogleConfigFile(file) {
    var statusEl = document.getElementById('google_upload_status');
    if (!file.name.endsWith('.json')) {
        statusEl.textContent = 'File must be a .json file';
        statusEl.className = 'text-sm mt-2 text-red-400';
        return;
    }
    statusEl.textContent = 'Uploading...';
    statusEl.className = 'text-sm mt-2 text-[#9ca3af]';
    var reader = new FileReader();
    reader.onload = function (e) {
        try {
            var content = e.target.result;
            JSON.parse(content); // validate
            fetch(settingsBase + '/api/advanced/google/upload-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content })
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data.success) {
                    statusEl.textContent = 'Configuration saved. Connecting to Google...';
                    statusEl.className = 'text-sm mt-2 text-green-400';
                    setTimeout(function () {
                        document.getElementById('google_setup_modal').classList.add('hidden');
                        // Fetch OAuth URL and redirect directly
                        fetch(settingsBase + '/api/advanced/google/oauth-url').then(function (r) { return r.json(); }).then(function (d) {
                            if (d.url) window.location.href = d.url;
                            else if (typeof window.showNotification === 'function') window.showNotification(d.error || 'Could not get OAuth URL', 'error');
                        });
                    }, 1000);
                    if (typeof window.showNotification === 'function') window.showNotification('Google OAuth config saved', 'success');
                } else {
                    statusEl.textContent = data.error || 'Upload failed';
                    statusEl.className = 'text-sm mt-2 text-red-400';
                }
            }).catch(function () {
                statusEl.textContent = 'Upload failed';
                statusEl.className = 'text-sm mt-2 text-red-400';
            });
        } catch (err) {
            statusEl.textContent = 'Invalid JSON file';
            statusEl.className = 'text-sm mt-2 text-red-400';
        }
    };
    reader.readAsText(file);
}

function connectJira() {
    var listModal = document.getElementById('jira_modal');
    var formModal = document.getElementById('jira_form_modal');
    if (!listModal || !formModal) return;
    listModal.classList.remove('hidden');
    loadJiraAccounts();
    listModal.querySelectorAll('.jira_modal_close').forEach(function (btn) { btn.onclick = function () { listModal.classList.add('hidden'); }; });
    document.getElementById('jira_add_btn').onclick = function () {
        window._jira_editing_name = null;
        document.getElementById('jira_form_title').textContent = 'Add Jira Account';
        document.getElementById('jira_account_name').value = 'Jira Account';
        document.getElementById('jira_server_url').value = '';
        document.getElementById('jira_email').value = '';
        document.getElementById('jira_api_token').value = '';
        document.getElementById('jira_form_status').textContent = '';
        formModal.classList.remove('hidden');
    };
    formModal.querySelectorAll('.jira_form_modal_close').forEach(function (btn) { btn.onclick = function () { formModal.classList.add('hidden'); }; });
    document.getElementById('jira_form_cancel').onclick = function () { formModal.classList.add('hidden'); };
    document.getElementById('jira_validate_save_btn').onclick = function () { jiraValidateAndSave(); };
}

function loadJiraAccounts() {
    fetch(settingsBase + '/api/advanced/accounts?provider=jira').then(function (r) { return r.json(); }).then(function (data) {
        var listEl = document.getElementById('jira_accounts_list');
        var accounts = data.accounts || [];
        if (accounts.length === 0) {
            listEl.innerHTML = '<p class="text-[#9ca3af]">No Jira accounts. Click Add Jira Account.</p>';
            return;
        }
        listEl.innerHTML = '<table class="w-full text-sm"><thead><tr class="border-b border-[#565869]"><th class="text-left py-2 text-[#9ca3af]">Name</th><th class="text-left py-2 text-[#9ca3af]">Server URL</th><th class="text-left py-2 text-[#9ca3af]">Status</th><th></th></tr></thead><tbody id="jira_accounts_tbody"></tbody></table>';
        var tbody = document.getElementById('jira_accounts_tbody');
        accounts.forEach(function (acc) {
            var tr = document.createElement('tr');
            tr.className = 'border-b border-[#565869]';
            tr.innerHTML = '<td class="py-2">' + escapeHtml(acc.name || '') + '</td><td class="py-2 text-[#9ca3af]">' + escapeHtml(acc.server_url || '') + '</td><td class="py-2">' + (acc.is_valid ? 'Valid' : 'Invalid') + '</td><td class="py-2"><button type="button" class="jira_edit_btn px-2 py-1 bg-[#0088cc] hover:bg-[#0077b3] text-white rounded text-xs mr-1" data-name="' + escapeHtml(acc.name || '') + '">Edit</button><button type="button" class="jira_delete_btn px-2 py-1 bg-[#ff6b6b] hover:bg-[#ff5252] text-white rounded text-xs" data-name="' + escapeHtml(acc.name || '') + '">Delete</button></td>';
            tbody.appendChild(tr);
        });
        listEl.querySelectorAll('.jira_edit_btn').forEach(function (btn) {
            btn.onclick = function () {
                var name = btn.getAttribute('data-name');
                var acc = accounts.find(function (a) { return a.name === name; });
                if (!acc) return;
                window._jira_editing_name = name;
                document.getElementById('jira_form_title').textContent = 'Edit Jira Account';
                document.getElementById('jira_account_name').value = acc.name || '';
                document.getElementById('jira_server_url').value = acc.server_url || '';
                document.getElementById('jira_email').value = '';
                document.getElementById('jira_api_token').value = '';
                document.getElementById('jira_form_status').textContent = 'Re-enter email and API token to update this account.';
                document.getElementById('jira_form_modal').classList.remove('hidden');
            };
        });
        listEl.querySelectorAll('.jira_delete_btn').forEach(function (btn) {
            btn.onclick = function () {
                var name = btn.getAttribute('data-name');
                window.DecisionsAPI.confirm({
                    title: "Delete Jira account",
                    message: 'Delete Jira account "' + name + '"?',
                    confirmLabel: "Delete",
                    danger: true,
                    onConfirm: function() {
                        fetch(settingsBase + '/api/advanced/accounts', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'jira', name: name }) }).then(function (r) { return r.json(); }).then(function () { loadJiraAccounts(); updateConnectionStatus(); }).catch(function () {});
                    }
                });
            };
        });
    });
}

function jiraValidateAndSave() {
    var name = (document.getElementById('jira_account_name').value || '').trim();
    var server_url = (document.getElementById('jira_server_url').value || '').trim();
    var email = (document.getElementById('jira_email').value || '').trim();
    var api_token = (document.getElementById('jira_api_token').value || '').trim();
    var statusEl = document.getElementById('jira_form_status');
    if (!name || !server_url || !email || !api_token) {
        statusEl.textContent = 'Please fill in all fields.';
        statusEl.className = 'text-sm text-red-400';
        return;
    }
    statusEl.textContent = 'Validating...';
    statusEl.className = 'text-sm text-[#9ca3af]';
    fetch(settingsBase + '/api/advanced/validate/jira', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_url: server_url, email: email, api_token: api_token }) }).then(function (r) { return r.json(); }).then(function (res) {
        if (!res.valid) {
            statusEl.textContent = res.error || 'Validation failed';
            statusEl.className = 'text-sm text-red-400';
            return;
        }
        var body = { provider: 'jira', name: name, server_url: server_url, email: email, api_token: api_token };
        if (window._jira_editing_name) body.original_name = window._jira_editing_name;
        fetch(settingsBase + '/api/advanced/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) { return r.json(); }).then(function (result) {
            if (result.success) {
                statusEl.textContent = 'Saved.';
                statusEl.className = 'text-sm text-green-400';
                var formModal = document.getElementById('jira_form_modal');
                if (formModal) formModal.classList.add('hidden');
                loadJiraAccounts();
                updateConnectionStatus();
                if (typeof window.showNotification === 'function') window.showNotification('Jira account saved', 'success');
            } else {
                statusEl.textContent = result.error || 'Save failed';
                statusEl.className = 'text-sm text-red-400';
            }
        });
    }).catch(function () { statusEl.textContent = 'Request failed'; statusEl.className = 'text-sm text-red-400'; });
}

function connectTrello() {
    var listModal = document.getElementById('trello_modal');
    var formModal = document.getElementById('trello_form_modal');
    if (!listModal || !formModal) return;
    listModal.classList.remove('hidden');
    loadTrelloAccounts();
    listModal.querySelectorAll('.trello_modal_close').forEach(function (btn) { btn.onclick = function () { listModal.classList.add('hidden'); }; });
    document.getElementById('trello_add_btn').onclick = function () {
        window._trello_editing_name = null;
        document.getElementById('trello_form_title').textContent = 'Add Trello Account';
        document.getElementById('trello_account_name').value = 'Trello Account';
        document.getElementById('trello_api_key').value = '';
        document.getElementById('trello_api_token').value = '';
        document.getElementById('trello_form_status').textContent = '';
        formModal.classList.remove('hidden');
    };
    formModal.querySelectorAll('.trello_form_modal_close').forEach(function (btn) { btn.onclick = function () { formModal.classList.add('hidden'); }; });
    document.getElementById('trello_form_cancel').onclick = function () { formModal.classList.add('hidden'); };
    document.getElementById('trello_generate_token_btn').onclick = function () {
        var apiKey = (document.getElementById('trello_api_key').value || '').trim();
        if (!apiKey) { if (typeof window.showNotification === 'function') window.showNotification('Enter API key first', 'warning'); return; }
        fetch(settingsBase + '/api/advanced/trello/auth-url?api_key=' + encodeURIComponent(apiKey)).then(function (r) { return r.json(); }).then(function (data) { if (data.url) window.open(data.url); if (typeof window.showNotification === 'function') window.showNotification('Approve in Trello, then paste the token here.', 'info'); });
    };
    document.getElementById('trello_validate_save_btn').onclick = function () { trelloValidateAndSave(); };
}

function loadTrelloAccounts() {
    fetch(settingsBase + '/api/advanced/accounts?provider=trello').then(function (r) { return r.json(); }).then(function (data) {
        var listEl = document.getElementById('trello_accounts_list');
        var accounts = data.accounts || [];
        if (accounts.length === 0) {
            listEl.innerHTML = '<p class="text-[#9ca3af]">No Trello accounts. Click Add Trello Account.</p>';
            return;
        }
        listEl.innerHTML = '<table class="w-full text-sm"><thead><tr class="border-b border-[#565869]"><th class="text-left py-2 text-[#9ca3af]">Name</th><th class="text-left py-2 text-[#9ca3af]">API Key</th><th class="text-left py-2 text-[#9ca3af]">Status</th><th></th></tr></thead><tbody id="trello_accounts_tbody"></tbody></table>';
        var tbody = document.getElementById('trello_accounts_tbody');
        accounts.forEach(function (acc) {
            var tr = document.createElement('tr');
            tr.className = 'border-b border-[#565869]';
            tr.innerHTML = '<td class="py-2">' + escapeHtml(acc.name || '') + '</td><td class="py-2 text-[#9ca3af]">' + escapeHtml(acc.api_key_masked || '') + '</td><td class="py-2">' + (acc.is_valid ? 'Valid' : 'Invalid') + '</td><td class="py-2"><button type="button" class="trello_edit_btn px-2 py-1 bg-[#0088cc] hover:bg-[#0077b3] text-white rounded text-xs mr-1" data-name="' + escapeHtml(acc.name || '') + '">Edit</button><button type="button" class="trello_delete_btn px-2 py-1 bg-[#ff6b6b] hover:bg-[#ff5252] text-white rounded text-xs" data-name="' + escapeHtml(acc.name || '') + '">Delete</button></td>';
            tbody.appendChild(tr);
        });
        listEl.querySelectorAll('.trello_edit_btn').forEach(function (btn) {
            btn.onclick = function () {
                var name = btn.getAttribute('data-name');
                var acc = accounts.find(function (a) { return a.name === name; });
                if (!acc) return;
                window._trello_editing_name = name;
                document.getElementById('trello_form_title').textContent = 'Edit Trello Account';
                document.getElementById('trello_account_name').value = acc.name || '';
                document.getElementById('trello_api_key').value = '';
                document.getElementById('trello_api_token').value = '';
                document.getElementById('trello_form_status').textContent = 'Re-enter API key and token to update this account.';
                document.getElementById('trello_form_modal').classList.remove('hidden');
            };
        });
        listEl.querySelectorAll('.trello_delete_btn').forEach(function (btn) {
            btn.onclick = function () {
                var name = btn.getAttribute('data-name');
                window.DecisionsAPI.confirm({
                    title: "Delete Trello account",
                    message: 'Delete Trello account "' + name + '"?',
                    confirmLabel: "Delete",
                    danger: true,
                    onConfirm: function() {
                        fetch(settingsBase + '/api/advanced/accounts', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'trello', name: name }) }).then(function (r) { return r.json(); }).then(function () { loadTrelloAccounts(); updateConnectionStatus(); }).catch(function () {});
                    }
                });
            };
        });
    });
}

function trelloValidateAndSave() {
    var name = (document.getElementById('trello_account_name').value || '').trim();
    var api_key = (document.getElementById('trello_api_key').value || '').trim();
    var api_token = (document.getElementById('trello_api_token').value || '').trim();
    var statusEl = document.getElementById('trello_form_status');
    if (!name || !api_key || !api_token) {
        statusEl.textContent = 'Please fill in all fields.';
        statusEl.className = 'text-sm text-red-400';
        return;
    }
    statusEl.textContent = 'Validating...';
    statusEl.className = 'text-sm text-[#9ca3af]';
    fetch(settingsBase + '/api/advanced/validate/trello', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: api_key, api_token: api_token }) }).then(function (r) { return r.json(); }).then(function (res) {
        if (!res.valid) {
            statusEl.textContent = res.error || 'Validation failed';
            statusEl.className = 'text-sm text-red-400';
            return;
        }
        var body = { provider: 'trello', name: name, api_key: api_key, api_token: api_token };
        if (window._trello_editing_name) body.original_name = window._trello_editing_name;
        fetch(settingsBase + '/api/advanced/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) { return r.json(); }).then(function (result) {
            if (result.success) {
                statusEl.textContent = 'Saved.';
                statusEl.className = 'text-sm text-green-400';
                var formModal = document.getElementById('trello_form_modal');
                if (formModal) formModal.classList.add('hidden');
                loadTrelloAccounts();
                updateConnectionStatus();
                if (typeof window.showNotification === 'function') window.showNotification('Trello account saved', 'success');
            } else {
                statusEl.textContent = result.error || 'Save failed';
                statusEl.className = 'text-sm text-red-400';
            }
        });
    }).catch(function () { statusEl.textContent = 'Request failed'; statusEl.className = 'text-sm text-red-400'; });
}

var telegramPollInterval = null;
function connectTelegram() {
    var modal = document.getElementById('telegram_modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    document.getElementById('telegram_qr_container').innerHTML = '<p class="text-[#565869]">Loading QR...</p>';
    document.getElementById('telegram_status').textContent = 'Requesting QR code...';
    document.getElementById('telegram_link').classList.add('hidden');
    modal.querySelectorAll('.telegram_modal_close').forEach(function (btn) { btn.onclick = function () { clearInterval(telegramPollInterval); modal.classList.add('hidden'); }; });
    fetch(settingsBase + '/api/advanced/telegram/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error) {
            document.getElementById('telegram_status').textContent = data.error;
            document.getElementById('telegram_status').className = 'text-sm text-red-400 text-center';
            return;
        }
        var qr = data.qr_code;
        var token = data.token;
        var link = data.link;
        var app_user_id = data.app_user_id;
        if (qr) {
            var img = document.createElement('img');
            img.alt = 'QR';
            img.className = 'max-w-[200px] max-h-[200px]';
            if (qr.indexOf('data:image') === 0 && qr.indexOf(',') >= 0) img.src = qr;
            else img.src = 'data:image/png;base64,' + qr;
            document.getElementById('telegram_qr_container').innerHTML = '';
            document.getElementById('telegram_qr_container').appendChild(img);
        }
        document.getElementById('telegram_status').textContent = 'Scan the QR code with your Telegram app.';
        document.getElementById('telegram_status').className = 'text-sm text-green-500 text-center';
        if (link) {
            var a = document.getElementById('telegram_link');
            a.href = link;
            a.textContent = 'Or open link in Telegram';
            a.classList.remove('hidden');
        }
        telegramPollInterval = setInterval(function () {
            fetch(settingsBase + '/api/advanced/telegram/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: token }) }).then(function (r) { return r.json(); }).then(function (st) {
                if (st.status === 'connected') {
                    clearInterval(telegramPollInterval);
                    fetch(settingsBase + '/api/advanced/telegram/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: token, app_user_id: app_user_id, user_id: st.user_id }) }).then(function () {
                        document.getElementById('telegram_status').textContent = 'Connected successfully.';
                        updateConnectionStatus();
                        if (typeof window.showNotification === 'function') window.showNotification('Telegram connected', 'success');
                        setTimeout(function () { modal.classList.add('hidden'); }, 1500);
                    });
                }
            });
        }, 2000);
    }).catch(function () {
        document.getElementById('telegram_status').textContent = 'Failed to get QR code.';
        document.getElementById('telegram_status').className = 'text-sm text-red-400 text-center';
    });
}

var whatsappPollInterval = null;
function connectWhatsApp() {
    var modal = document.getElementById('whatsapp_modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    document.getElementById('whatsapp_qr_container').innerHTML = '<p class="text-[#565869]">Loading QR code...</p>';
    document.getElementById('whatsapp_status').textContent = 'Requesting QR code...';
    document.getElementById('whatsapp_status').className = 'text-sm text-[#9ca3af] text-center';
    document.getElementById('whatsapp_connected_info').classList.add('hidden');

    modal.querySelectorAll('.whatsapp_modal_close').forEach(function (btn) { btn.onclick = function () { clearInterval(whatsappPollInterval); modal.classList.add('hidden'); }; });
    document.getElementById('whatsapp_disconnect_btn').onclick = function () {
        fetch(settingsBase + '/api/advanced/whatsapp/disconnect', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.success) {
                document.getElementById('whatsapp_status').textContent = 'Disconnected. Requesting new QR code...';
                document.getElementById('whatsapp_connected_info').classList.add('hidden');
                updateConnectionStatus();
                setTimeout(function () { connectWhatsApp(); }, 2000);
            } else {
                if (typeof window.showNotification === 'function') window.showNotification(data.error || 'Disconnect failed', 'error');
            }
        }).catch(function () { if (typeof window.showNotification === 'function') window.showNotification('Disconnect failed', 'error'); });
    };

    fetch(settingsBase + '/api/advanced/whatsapp/qr').then(function (r) { return r.json(); }).then(function (data) {
        if (data.error && data.status === 'service_down') {
            document.getElementById('whatsapp_status').textContent = 'WhatsApp service is not running on the server.';
            document.getElementById('whatsapp_status').className = 'text-sm text-yellow-400 text-center';
            document.getElementById('whatsapp_qr_container').innerHTML = '<p class="text-yellow-600">Service Unavailable</p><p class="text-sm text-[#565869] mt-2">The WhatsApp service needs to be running on the server. Contact your admin.</p>';
            return;
        }
        var qr = data.qr_code;
        var status = data.status;

        if (status === 'connected' && data.phone) {
            // Already connected — save to DB
            fetch(settingsBase + '/api/advanced/whatsapp/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jid: data.phone.jid || '', name: data.phone.name || '', push_name: data.phone.pushName || data.phone.notify || '' }) }).then(function () { updateConnectionStatus(); });
            document.getElementById('whatsapp_qr_container').innerHTML = '\u003cp class="text-green-500 text-lg"\u003e✓ Connected\u003c/p\u003e';
            document.getElementById('whatsapp_status').textContent = 'WhatsApp is connected: ' + (data.phone.name || data.phone.jid || '');
            document.getElementById('whatsapp_status').className = 'text-sm text-green-400 text-center';
            document.getElementById('whatsapp_connected_info').classList.remove('hidden');
            document.getElementById('whatsapp_phone_info').textContent = data.phone.name || data.phone.jid || 'Unknown';
            updateConnectionStatus();
            return;
        }

        if (qr) {
            // The server provides a base64 PNG QR image (qr_image)
            var qrContainer = document.getElementById('whatsapp_qr_container');
            qrContainer.innerHTML = '';
            if (data.qr_image) {
                // qr_image is a data URL like "data:image/png;base64,..."
                var img = document.createElement('img');
                img.src = data.qr_image;
                img.alt = 'WhatsApp QR Code';
                img.className = 'max-w-[256px] max-h-[256px] rounded';
                qrContainer.appendChild(img);
            } else {
                // Fallback: show raw pairing text
                var textDiv = document.createElement('div');
                textDiv.className = 'text-center';
                textDiv.innerHTML = '<p class="text-sm text-[#ececf1] mb-2">Enter this code in WhatsApp:</p>' +
                    '<div class="bg-white p-3 rounded inline-block"><p class="text-xs text-black break-all font-mono" style="max-width:250px;word-break:break-all;">' + escapeHtml(qr) + '</p></div>';
                qrContainer.appendChild(textDiv);
            }
            document.getElementById('whatsapp_status').textContent = 'Scan with WhatsApp: Settings \u2192 Linked Devices \u2192 Link a Device';
            document.getElementById('whatsapp_status').className = 'text-sm text-green-500 text-center';
        } else if (status === 'qr_ready') {
            document.getElementById('whatsapp_status').textContent = 'Waiting for QR code...';
            document.getElementById('whatsapp_status').className = 'text-sm text-[#9ca3af] text-center';
        } else {
            document.getElementById('whatsapp_status').textContent = data.error || 'Could not get QR code.';
            document.getElementById('whatsapp_status').className = 'text-sm text-red-400 text-center';
        }

        // Poll for connection status
        whatsappPollInterval = setInterval(function () {
            fetch(settingsBase + '/api/advanced/whatsapp/status').then(function (r) { return r.json(); }).then(function (st) {
                if (st.status === 'connected' && st.phone) {
                    clearInterval(whatsappPollInterval);
                    // Save to DB
                    fetch(settingsBase + '/api/advanced/whatsapp/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jid: st.phone.jid || '', name: st.phone.name || '', push_name: st.phone.pushName || st.phone.notify || '' }) });
                    document.getElementById('whatsapp_qr_container').innerHTML = '\u003cp class="text-green-500 text-lg"\u003e✓ Connected\u003c/p\u003e';
                    document.getElementById('whatsapp_status').textContent = 'WhatsApp connected: ' + (st.phone.name || st.phone.jid || '');
                    document.getElementById('whatsapp_status').className = 'text-sm text-green-400 text-center';
                    document.getElementById('whatsapp_connected_info').classList.remove('hidden');
                    document.getElementById('whatsapp_phone_info').textContent = st.phone.name || st.phone.jid || 'Unknown';
                    updateConnectionStatus();
                    if (typeof window.showNotification === 'function') window.showNotification('WhatsApp connected', 'success');
                } else if (st.status === 'qr_ready' && st.qr_code && !data.qr_code) {
                    // New QR available, refresh the display
                    data.qr_code = st.qr_code;
                    connectWhatsApp(); // restart with new QR
                    clearInterval(whatsappPollInterval);
                }
            });
        }, 3000);
    }).catch(function () {
        document.getElementById('whatsapp_status').textContent = 'Failed to connect to WhatsApp service.';
        document.getElementById('whatsapp_status').className = 'text-sm text-red-400 text-center';
    });
}

function initAdvancedTab() {
    var treeEl = document.getElementById('directory_tree');
    if (treeEl) {
        treeEl.addEventListener('click', function (e) {
            if (e.target.closest('.dir-toggle') || e.target.closest('.dir-tree-cb')) return;
            var node = e.target.closest('.dir-node');
            if (node) {
                var toggle = node.querySelector('.dir-toggle');
                if (toggle) toggle.click();
            }
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('tab-advanced')) {
            loadAdvancedSettings();
            initAdvancedTab();
            var googleBtn = document.getElementById('google_connect_btn');
            if (googleBtn) googleBtn.addEventListener('click', connectGoogle);
            var telegramBtn = document.getElementById('telegram_connect_btn');
            if (telegramBtn) telegramBtn.addEventListener('click', connectTelegram);
            var whatsappBtn = document.getElementById('whatsapp_connect_btn');
            if (whatsappBtn) whatsappBtn.addEventListener('click', connectWhatsApp);
            var trelloBtn = document.getElementById('trello_connect_btn');
            if (trelloBtn) trelloBtn.addEventListener('click', connectTrello);
            var jiraBtn = document.getElementById('jira_connect_btn');
            if (jiraBtn) jiraBtn.addEventListener('click', connectJira);
            var discordBtn = document.getElementById('discord_connect_btn');
            if (discordBtn) discordBtn.addEventListener('click', openIntegrationConnectorsModal);
            var slackBtn = document.getElementById('slack_connect_btn');
            if (slackBtn) slackBtn.addEventListener('click', openIntegrationConnectorsModal);
            var clickupBtn = document.getElementById('clickup_connect_btn');
            if (clickupBtn) clickupBtn.addEventListener('click', openIntegrationConnectorsModal);
            var mondayBtn = document.getElementById('monday_connect_btn');
            if (mondayBtn) mondayBtn.addEventListener('click', openIntegrationConnectorsModal);
            var reindexBtn = document.getElementById('reindex_button');
            if (reindexBtn) reindexBtn.addEventListener('click', reindexModels);
        }
    });
} else {
    if (document.getElementById('tab-advanced')) {
        loadAdvancedSettings();
        initAdvancedTab();
        var googleBtn = document.getElementById('google_connect_btn');
        if (googleBtn) googleBtn.addEventListener('click', connectGoogle);
        var telegramBtn = document.getElementById('telegram_connect_btn');
        if (telegramBtn) telegramBtn.addEventListener('click', connectTelegram);
        var whatsappBtn = document.getElementById('whatsapp_connect_btn');
        if (whatsappBtn) whatsappBtn.addEventListener('click', connectWhatsApp);
        var trelloBtn = document.getElementById('trello_connect_btn');
        if (trelloBtn) trelloBtn.addEventListener('click', connectTrello);
        var jiraBtn = document.getElementById('jira_connect_btn');
        if (jiraBtn) jiraBtn.addEventListener('click', connectJira);
        var discordBtn = document.getElementById('discord_connect_btn');
        if (discordBtn) discordBtn.addEventListener('click', openIntegrationConnectorsModal);
        var slackBtn = document.getElementById('slack_connect_btn');
        if (slackBtn) slackBtn.addEventListener('click', openIntegrationConnectorsModal);
        var clickupBtn = document.getElementById('clickup_connect_btn');
        if (clickupBtn) clickupBtn.addEventListener('click', openIntegrationConnectorsModal);
        var mondayBtn = document.getElementById('monday_connect_btn');
        if (mondayBtn) mondayBtn.addEventListener('click', openIntegrationConnectorsModal);
        var reindexBtn = document.getElementById('reindex_button');
        if (reindexBtn) reindexBtn.addEventListener('click', reindexModels);
    }
}

window.loadAdvancedSettings = loadAdvancedSettings;
window.saveAdvancedSettings = saveAdvancedSettings;
