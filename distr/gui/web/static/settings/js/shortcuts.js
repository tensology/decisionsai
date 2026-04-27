// Shortcut Keys settings

function _shortcutLabelFromModifier(modifier) {
    var map = {
        option: 'Option',
        option_command: 'Option + Command',
        command: 'Command',
        control: 'Control',
        shift: 'Shift'
    };
    return map[modifier] || 'Option';
}

function _comboLabel(modifier, key) {
    var keyLabel = _shortcutLabelFromKey(key);
    return _shortcutLabelFromModifier(modifier) + ' + ' + keyLabel;
}

function _shortcutLabelFromKey(key) {
    var map = {
        left_bracket: '[',
        right_bracket: ']',
        minus: '-',
        equal: '=',
        left_arrow: 'Left Arrow',
        right_arrow: 'Right Arrow',
        a: 'A',
        c: 'C',
        j: 'J',
        n: 'N',
        s: 'S',
        w: 'W',
        grave: '~'
    };
    return map[key] || '[';
}

async function loadShortcutSettings() {
    try {
        const response = await fetch('/api/shortcuts');
        if (!response.ok) {
            throw new Error('Failed to load shortcut settings');
        }
        const settings = await response.json();
        document.getElementById('shortcuts_global_ptt_hotkey_enabled').checked = settings.global_ptt_hotkey_enabled !== undefined ? settings.global_ptt_hotkey_enabled : true;
        document.getElementById('shortcuts_global_ptt_hotkey_primary').value = settings.global_ptt_hotkey_primary || 'option';
        document.getElementById('shortcuts_global_ptt_hotkey_secondary').value = settings.global_ptt_hotkey_secondary || 'command';
        document.getElementById('shortcuts_oracle_size_hotkey_decrease_modifier').value = settings.oracle_size_hotkey_decrease_modifier || 'option_command';
        document.getElementById('shortcuts_oracle_size_hotkey_decrease_key').value = settings.oracle_size_hotkey_decrease_key || 'left_bracket';
        document.getElementById('shortcuts_oracle_size_hotkey_increase_modifier').value = settings.oracle_size_hotkey_increase_modifier || 'option_command';
        document.getElementById('shortcuts_oracle_size_hotkey_increase_key').value = settings.oracle_size_hotkey_increase_key || 'right_bracket';
        document.getElementById('shortcuts_recording_hotkey_enabled').checked = settings.recording_hotkey_enabled !== undefined ? settings.recording_hotkey_enabled : true;
        document.getElementById('shortcuts_recording_hotkey_modifier').value = settings.recording_hotkey_modifier || 'option_command';
        document.getElementById('shortcuts_recording_hotkey_key').value = settings.recording_hotkey_key || 's';
        document.getElementById('shortcuts_skin_nav_hotkey_previous_modifier').value = settings.skin_nav_hotkey_previous_modifier || 'option_command';
        document.getElementById('shortcuts_skin_nav_hotkey_previous_key').value = settings.skin_nav_hotkey_previous_key || 'left_arrow';
        document.getElementById('shortcuts_skin_nav_hotkey_next_modifier').value = settings.skin_nav_hotkey_next_modifier || 'option_command';
        document.getElementById('shortcuts_skin_nav_hotkey_next_key').value = settings.skin_nav_hotkey_next_key || 'right_arrow';
        document.getElementById('shortcuts_skin_select_hotkey_modifier').value = settings.skin_select_hotkey_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_chat_modifier').value = settings.web_hotkey_chat_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_chat_key').value = settings.web_hotkey_chat_key || 'c';
        document.getElementById('shortcuts_web_hotkey_projects_modifier').value = settings.web_hotkey_projects_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_projects_key').value = settings.web_hotkey_projects_key || 'j';
        document.getElementById('shortcuts_web_hotkey_actions_modifier').value = settings.web_hotkey_actions_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_actions_key').value = settings.web_hotkey_actions_key || 'a';
        document.getElementById('shortcuts_web_hotkey_snippets_modifier').value = settings.web_hotkey_snippets_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_snippets_key').value = settings.web_hotkey_snippets_key || 'n';
        document.getElementById('shortcuts_web_hotkey_workflows_modifier').value = settings.web_hotkey_workflows_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_workflows_key').value = settings.web_hotkey_workflows_key || 'w';
        document.getElementById('shortcuts_web_hotkey_preferences_modifier').value = settings.web_hotkey_preferences_modifier || 'option_command';
        document.getElementById('shortcuts_web_hotkey_preferences_key').value = settings.web_hotkey_preferences_key || 'grave';
    } catch (error) {
        console.error('Error loading shortcut settings:', error);
        showNotification('Failed to load shortcut settings: ' + error.message, 'error');
    }
}

async function saveShortcutSettings() {
    try {
        const settings = {
            global_ptt_hotkey_enabled: document.getElementById('shortcuts_global_ptt_hotkey_enabled').checked,
            global_ptt_hotkey_primary: document.getElementById('shortcuts_global_ptt_hotkey_primary').value,
            global_ptt_hotkey_secondary: document.getElementById('shortcuts_global_ptt_hotkey_secondary').value,
            oracle_size_hotkey_decrease_modifier: document.getElementById('shortcuts_oracle_size_hotkey_decrease_modifier').value,
            oracle_size_hotkey_decrease_key: document.getElementById('shortcuts_oracle_size_hotkey_decrease_key').value,
            oracle_size_hotkey_increase_modifier: document.getElementById('shortcuts_oracle_size_hotkey_increase_modifier').value,
            oracle_size_hotkey_increase_key: document.getElementById('shortcuts_oracle_size_hotkey_increase_key').value,
            recording_hotkey_enabled: document.getElementById('shortcuts_recording_hotkey_enabled').checked,
            recording_hotkey_modifier: document.getElementById('shortcuts_recording_hotkey_modifier').value,
            recording_hotkey_key: document.getElementById('shortcuts_recording_hotkey_key').value,
            skin_nav_hotkey_previous_modifier: document.getElementById('shortcuts_skin_nav_hotkey_previous_modifier').value,
            skin_nav_hotkey_previous_key: document.getElementById('shortcuts_skin_nav_hotkey_previous_key').value,
            skin_nav_hotkey_next_modifier: document.getElementById('shortcuts_skin_nav_hotkey_next_modifier').value,
            skin_nav_hotkey_next_key: document.getElementById('shortcuts_skin_nav_hotkey_next_key').value,
            skin_select_hotkey_modifier: document.getElementById('shortcuts_skin_select_hotkey_modifier').value,
            web_hotkey_chat_modifier: document.getElementById('shortcuts_web_hotkey_chat_modifier').value,
            web_hotkey_chat_key: document.getElementById('shortcuts_web_hotkey_chat_key').value,
            web_hotkey_projects_modifier: document.getElementById('shortcuts_web_hotkey_projects_modifier').value,
            web_hotkey_projects_key: document.getElementById('shortcuts_web_hotkey_projects_key').value,
            web_hotkey_actions_modifier: document.getElementById('shortcuts_web_hotkey_actions_modifier').value,
            web_hotkey_actions_key: document.getElementById('shortcuts_web_hotkey_actions_key').value,
            web_hotkey_snippets_modifier: document.getElementById('shortcuts_web_hotkey_snippets_modifier').value,
            web_hotkey_snippets_key: document.getElementById('shortcuts_web_hotkey_snippets_key').value,
            web_hotkey_workflows_modifier: document.getElementById('shortcuts_web_hotkey_workflows_modifier').value,
            web_hotkey_workflows_key: document.getElementById('shortcuts_web_hotkey_workflows_key').value,
            web_hotkey_preferences_modifier: document.getElementById('shortcuts_web_hotkey_preferences_modifier').value,
            web_hotkey_preferences_key: document.getElementById('shortcuts_web_hotkey_preferences_key').value
        };

        if (settings.global_ptt_hotkey_primary === settings.global_ptt_hotkey_secondary) {
            showNotification('Pick two different keys for the global PTT hotkey', 'error');
            return;
        }

        const downCombo = _comboLabel(settings.oracle_size_hotkey_decrease_modifier, settings.oracle_size_hotkey_decrease_key);
        const upCombo = _comboLabel(settings.oracle_size_hotkey_increase_modifier, settings.oracle_size_hotkey_increase_key);
        if (downCombo === upCombo) {
            showNotification('Increase and decrease oracle size shortcuts must be different', 'error');
            return;
        }

        const recordingCombo = _comboLabel(settings.recording_hotkey_modifier, settings.recording_hotkey_key);
        if (recordingCombo === downCombo || recordingCombo === upCombo) {
            showNotification('Recording shortcut must differ from oracle size shortcuts', 'error');
            return;
        }

        const response = await fetch('/api/shortcuts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save shortcut settings');
        }

        showNotification('Shortcut settings saved', 'success');
    } catch (error) {
        console.error('Error saving shortcut settings:', error);
        showNotification('Failed to save shortcut settings: ' + error.message, 'error');
    }
}

function _initShortcuts() {
    if (!document.getElementById('tab-shortcuts')) return;
    loadShortcutSettings();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initShortcuts);
} else {
    _initShortcuts();
}

window.loadShortcutSettings = loadShortcutSettings;
window.saveShortcutSettings = saveShortcutSettings;
