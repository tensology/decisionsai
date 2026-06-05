// Settings page JavaScript: shared glue (notification, tab switching, save/reload routing)

function showNotification(message, type) {
    type = type || 'info';
    var existing = document.getElementById('notification');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.id = 'notification';
    el.className = 'fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999] px-5 py-3 rounded-lg shadow-lg transition-opacity duration-300 text-white font-medium text-sm ' +
        (type === 'success' ? 'bg-green-600' : type === 'error' ? 'bg-red-600' : type === 'warning' ? 'bg-yellow-600' : 'bg-[#1a237e]');
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(function() {
        el.style.opacity = '0';
        setTimeout(function() { el.remove(); }, 300);
    }, 3000);
}
window.showNotification = showNotification;

var SETTINGS_TABS = ['general', 'initiative', 'audio', 'thirdparty', 'llms', 'mcp', 'skins', 'shortcuts', 'advanced', 'logs'];

function getTabFromHash() {
    var hash = (window.location.hash || '').replace(/^#/, '').toLowerCase();
    return SETTINGS_TABS.indexOf(hash) >= 0 ? hash : 'general';
}

function switchTab(tabName) {
    var tab = SETTINGS_TABS.indexOf(tabName) >= 0 ? tabName : 'general';
    var container = document.querySelector('.settings-tab-inner');
    var contents = container ? Array.prototype.filter.call(container.children, function(el) { return el.classList && el.classList.contains('tab-content'); }) : document.querySelectorAll('.tab-content');
    for (var i = 0; i < contents.length; i++) {
        contents[i].classList.remove('active-tab-panel');
        contents[i].style.setProperty('display', 'none', 'important');
    }
    var buttons = document.querySelectorAll('.tab-button');
    for (var j = 0; j < buttons.length; j++) buttons[j].classList.remove('active');
    var panel = document.getElementById('tab-' + tab);
    if (panel) {
        panel.classList.add('active-tab-panel');
        panel.style.setProperty('display', 'block', 'important');
    }
    var btn = document.querySelector('.tab-button[data-tab="' + tab + '"]');
    if (btn) btn.classList.add('active');
    var wrapper = document.querySelector('.settings-tab-inner') && document.querySelector('.settings-tab-inner').parentElement;
    if (wrapper) {
        if (tab === 'logs') wrapper.classList.add('logs-tab-active');
        else wrapper.classList.remove('logs-tab-active');
    }
    window.location.hash = tab;
    // Avoid double-fetch on first paint: llms.js loads the LLMs tab on DOMContentLoaded.
    if (window._settingsUiReady) {
        if (tab === 'logs' && typeof window.loadLogs === 'function') setTimeout(window.loadLogs, 0);
        if (tab === 'llms' && typeof window.loadLLMsSettings === 'function') setTimeout(window.loadLLMsSettings, 0);
        if (tab === 'mcp' && typeof window.loadMCPSettings === 'function') setTimeout(window.loadMCPSettings, 0);
    }
}
window.switchTab = switchTab;

function initSettingsPage() {
    if (!document.querySelector('.tab-content')) return;
    
    console.log('Settings JS initialized, attaching click handlers');
    
    document.querySelectorAll('.tab-button').forEach(function(btn) {
        console.log('Attaching handler to:', btn.getAttribute('data-tab'));
        btn.setAttribute('tabindex', '0');
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            var tab = btn.getAttribute('data-tab');
            console.log('Tab clicked:', tab);
            switchTab(tab);
        });
    });
    var settingsNav = document.getElementById('settings-nav') || document.querySelector('.settings-sidebar') || document.querySelector('#settings-sidebar');
    if (settingsNav && settingsNav.dataset.keyboardBound !== '1') {
        settingsNav.dataset.keyboardBound = '1';
        settingsNav.addEventListener('keydown', function(e) {
            var target = e.target && e.target.closest ? e.target.closest('.tab-button') : null;
            if (!target) return;
            if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            var buttons = Array.prototype.slice.call(settingsNav.querySelectorAll('.tab-button'));
            if (!buttons.length) return;
            if (e.key === 'Enter' || e.key === ' ') {
                target.click();
                return;
            }
            var idx = buttons.indexOf(target);
            var next = buttons[Math.max(0, Math.min(buttons.length - 1, idx + (e.key === 'ArrowDown' ? 1 : -1)))];
            if (next) {
                next.focus();
                switchTab(next.getAttribute('data-tab'));
            }
        });
    }
    
    window._settingsUiReady = false;
    switchTab(getTabFromHash());
    window._settingsUiReady = true;
    window.addEventListener('hashchange', function() { switchTab(getTabFromHash()); });
    setupActionButtons();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSettingsPage);
} else {
    initSettingsPage();
}

function setupActionButtons() {
    var saveBtn = document.getElementById('btn_save');
    var cancelBtn = document.getElementById('btn_cancel');
    if (saveBtn) {
        saveBtn.addEventListener('click', function() {
            var active = document.querySelector('.tab-content.active-tab-panel');
            if (active) {
                if (active.id === 'tab-general' && typeof window.saveGeneralSettings === 'function') window.saveGeneralSettings();
                else if (active.id === 'tab-audio' && typeof window.saveAudioSettings === 'function') window.saveAudioSettings();
                else if (active.id === 'tab-thirdparty' && typeof window.saveThirdPartySettings === 'function') window.saveThirdPartySettings();
                else if (active.id === 'tab-llms' && typeof window.saveLLMsSettings === 'function') window.saveLLMsSettings();
                else if (active.id === 'tab-skins' && typeof window.saveSkinsSettings === 'function') window.saveSkinsSettings();
                else if (active.id === 'tab-skins') showNotification('Use the Save button in the Skin Editor', 'info');
                else if (active.id === 'tab-shortcuts' && typeof window.saveShortcutSettings === 'function') window.saveShortcutSettings();
                else if (active.id === 'tab-advanced' && typeof window.saveAdvancedSettings === 'function') window.saveAdvancedSettings();
                else if (active.id === 'tab-initiative' && typeof window.saveInitiativeSettings === 'function') window.saveInitiativeSettings();
                else if (active.id === 'tab-mcp' && typeof window.saveMCPSettings === 'function') window.saveMCPSettings();
                else if (active.id === 'tab-logs') showNotification('Logs tab has no save action', 'info');
                else showNotification('Settings saved (UI only)', 'info');
            }
        });
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            var logsPanel = document.getElementById('tab-logs');
            var onLogsTab = logsPanel && logsPanel.classList.contains('active-tab-panel');
            if (onLogsTab) {
                if (typeof window.loadLogs === 'function') window.loadLogs();
                showNotification('Log refreshed', 'info');
                return false;
            }
            var active = document.querySelector('.settings-tab-inner > .tab-content.active-tab-panel');
            if (active) {
                if (active.id === 'tab-general' && typeof window.loadGeneralSettings === 'function') window.loadGeneralSettings();
                else if (active.id === 'tab-audio' && typeof window.loadAudioSettings === 'function') window.loadAudioSettings();
                else if (active.id === 'tab-thirdparty' && typeof window.loadThirdPartySettings === 'function') window.loadThirdPartySettings();
                else if (active.id === 'tab-llms' && typeof window.loadLLMsSettings === 'function') window.loadLLMsSettings();
                else if (active.id === 'tab-skins' && typeof window.loadSkinsSettings === 'function') window.loadSkinsSettings();
                else if (active.id === 'tab-shortcuts' && typeof window.loadShortcutSettings === 'function') window.loadShortcutSettings();
                else if (active.id === 'tab-advanced' && typeof window.loadAdvancedSettings === 'function') window.loadAdvancedSettings();
                else if (active.id === 'tab-initiative' && typeof window.loadInitiativeSettings === 'function') window.loadInitiativeSettings();
                else if (active.id === 'tab-mcp' && typeof window.loadMCPSettings === 'function') window.loadMCPSettings();
            }
            showNotification('Settings reloaded', 'info');
        }, true);
    }
    var clearCache = document.getElementById('clear_cache');
    if (clearCache) clearCache.addEventListener('click', function() { showNotification('Cache cleared', 'info'); });
    var resetDefaults = document.getElementById('reset_defaults');
    if (resetDefaults) resetDefaults.addEventListener('click', function() {
        window.DecisionsAPI.confirm({
            title: "Reset settings",
            message: "Reset all settings to defaults?",
            confirmLabel: "Reset",
            danger: true,
            onConfirm: function() {
                showNotification('Settings reset', 'info');
            }
        });
    });
    var googleConnect = document.getElementById('google_connect');
    if (googleConnect) googleConnect.addEventListener('click', function() { showNotification('Google Workspace (not yet implemented)', 'info'); });
}

function togglePasswordVisibility(inputId) {
    var input = document.getElementById(inputId);
    if (!input) return;
    var container = input.parentElement;
    var toggleBtn = container.querySelector('.toggle-password-btn');
    if (!toggleBtn) return;
    var eyeClosed = toggleBtn.querySelector('.eye-closed');
    var eyeOpen = toggleBtn.querySelector('.eye-open');
    if (input.type === 'password') {
        input.type = 'text';
        if (eyeClosed) eyeClosed.classList.add('hidden');
        if (eyeOpen) eyeOpen.classList.remove('hidden');
        toggleBtn.title = 'Hide password';
    } else {
        input.type = 'password';
        if (eyeClosed) eyeClosed.classList.remove('hidden');
        if (eyeOpen) eyeOpen.classList.add('hidden');
        toggleBtn.title = 'Show password';
    }
}
window.togglePasswordVisibility = togglePasswordVisibility;
