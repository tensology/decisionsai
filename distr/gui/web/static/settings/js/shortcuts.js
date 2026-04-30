// Shortcut Keys settings

const FALLBACK_SHORTCUT_OPTIONS = {
    modifiers: [
        { value: 'option', label: 'Option (Alt)' },
        { value: 'command', label: 'Command' },
        { value: 'control', label: 'Control' },
        { value: 'shift', label: 'Shift' },
        { value: 'option_command', label: 'Option + Command' },
        { value: 'control_command', label: 'Control + Command' }
    ],
    keys: [
        { value: 'left_arrow', label: 'Left Arrow' },
        { value: 'right_arrow', label: 'Right Arrow' },
        { value: 'up_arrow', label: 'Up Arrow' },
        { value: 'down_arrow', label: 'Down Arrow' },
        { value: 'left_bracket', label: '[' },
        { value: 'right_bracket', label: ']' },
        { value: 'minus', label: '-' },
        { value: 'plus', label: '+' },
        { value: 'equal', label: '=' },
        { value: 'grave', label: "~ / `" },
        { value: 'a', label: 'A' },
        { value: 's', label: 'S' },
        { value: 'c', label: 'C' },
        { value: 'j', label: 'J' },
        { value: 'n', label: 'N' },
        { value: 'w', label: 'W' },
        { value: '1', label: '1' },
        { value: '2', label: '2' },
        { value: '3', label: '3' },
        { value: '4', label: '4' }
    ]
};

let SHORTCUT_OPTIONS = FALLBACK_SHORTCUT_OPTIONS;

function _populateSelect(selectId, options, selectedValue) {
    const el = document.getElementById(selectId);
    if (!el) return;
    const html = (options || []).map(opt => `<option value="${opt.value}">${opt.label}</option>`).join('');
    el.innerHTML = html;
    if (selectedValue && (options || []).some(o => o.value === selectedValue)) {
        el.value = selectedValue;
    }
}

function _modifierLabel(modifier) {
    const option = (SHORTCUT_OPTIONS.modifiers || []).find(opt => opt.value === modifier);
    return option ? option.label : modifier;
}

function _keyLabel(key) {
    const option = (SHORTCUT_OPTIONS.keys || []).find(opt => opt.value === key);
    return option ? option.label : key;
}

function _comboLabel(modifier, key) {
    return _modifierLabel(modifier) + ' + ' + _keyLabel(key);
}

async function loadShortcutSettings() {
    try {
        const response = await fetch('/api/shortcuts');
        if (!response.ok) {
            throw new Error('Failed to load shortcut settings');
        }
        const settings = await response.json();
        SHORTCUT_OPTIONS = settings.shortcut_options || FALLBACK_SHORTCUT_OPTIONS;

        _populateSelect('shortcuts_global_ptt_hotkey_primary', SHORTCUT_OPTIONS.ptt_modifiers || SHORTCUT_OPTIONS.modifiers, settings.global_ptt_hotkey_primary || 'option');
        _populateSelect('shortcuts_global_ptt_hotkey_secondary', SHORTCUT_OPTIONS.ptt_modifiers || SHORTCUT_OPTIONS.modifiers, settings.global_ptt_hotkey_secondary || 'command');
        _populateSelect('shortcuts_oracle_size_hotkey_decrease_modifier', SHORTCUT_OPTIONS.modifiers, settings.oracle_size_hotkey_decrease_modifier || 'control_command');
        _populateSelect('shortcuts_oracle_size_hotkey_decrease_key', SHORTCUT_OPTIONS.keys, settings.oracle_size_hotkey_decrease_key || 'down_arrow');
        _populateSelect('shortcuts_oracle_size_hotkey_increase_modifier', SHORTCUT_OPTIONS.modifiers, settings.oracle_size_hotkey_increase_modifier || 'control_command');
        _populateSelect('shortcuts_oracle_size_hotkey_increase_key', SHORTCUT_OPTIONS.keys, settings.oracle_size_hotkey_increase_key || 'up_arrow');
        _populateSelect('shortcuts_recording_hotkey_modifier', SHORTCUT_OPTIONS.modifiers, settings.recording_hotkey_modifier || 'option_command');
        _populateSelect('shortcuts_recording_hotkey_key', SHORTCUT_OPTIONS.keys, settings.recording_hotkey_key || 's');
        _populateSelect('shortcuts_skin_nav_hotkey_previous_modifier', SHORTCUT_OPTIONS.modifiers, settings.skin_nav_hotkey_previous_modifier || 'control_command');
        _populateSelect('shortcuts_skin_nav_hotkey_previous_key', SHORTCUT_OPTIONS.keys, settings.skin_nav_hotkey_previous_key || 'left_arrow');
        _populateSelect('shortcuts_skin_nav_hotkey_next_modifier', SHORTCUT_OPTIONS.modifiers, settings.skin_nav_hotkey_next_modifier || 'control_command');
        _populateSelect('shortcuts_skin_nav_hotkey_next_key', SHORTCUT_OPTIONS.keys, settings.skin_nav_hotkey_next_key || 'right_arrow');
        _populateSelect('shortcuts_skin_select_hotkey_modifier', SHORTCUT_OPTIONS.modifiers, settings.skin_select_hotkey_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_chat_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_chat_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_chat_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_chat_key || 'c');
        _populateSelect('shortcuts_web_hotkey_projects_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_projects_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_projects_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_projects_key || 'j');
        _populateSelect('shortcuts_web_hotkey_actions_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_actions_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_actions_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_actions_key || 'a');
        _populateSelect('shortcuts_web_hotkey_snippets_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_snippets_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_snippets_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_snippets_key || 'n');
        _populateSelect('shortcuts_web_hotkey_workflows_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_workflows_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_workflows_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_workflows_key || 'w');
        _populateSelect('shortcuts_web_hotkey_preferences_modifier', SHORTCUT_OPTIONS.modifiers, settings.web_hotkey_preferences_modifier || 'option_command');
        _populateSelect('shortcuts_web_hotkey_preferences_key', SHORTCUT_OPTIONS.keys, settings.web_hotkey_preferences_key || 'grave');

        document.getElementById('shortcuts_global_ptt_hotkey_enabled').checked = settings.global_ptt_hotkey_enabled !== undefined ? settings.global_ptt_hotkey_enabled : true;
        document.getElementById('shortcuts_recording_hotkey_enabled').checked = settings.recording_hotkey_enabled !== undefined ? settings.recording_hotkey_enabled : true;
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
