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

function _shortcutLabelFromKey(key) {
    var map = {
        left_bracket: '[',
        right_bracket: ']',
        minus: '-',
        equal: '='
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
            oracle_size_hotkey_increase_key: document.getElementById('shortcuts_oracle_size_hotkey_increase_key').value
        };

        if (settings.global_ptt_hotkey_primary === settings.global_ptt_hotkey_secondary) {
            showNotification('Pick two different keys for the global PTT hotkey', 'error');
            return;
        }

        const downCombo = _shortcutLabelFromModifier(settings.oracle_size_hotkey_decrease_modifier) + ' + ' + _shortcutLabelFromKey(settings.oracle_size_hotkey_decrease_key);
        const upCombo = _shortcutLabelFromModifier(settings.oracle_size_hotkey_increase_modifier) + ' + ' + _shortcutLabelFromKey(settings.oracle_size_hotkey_increase_key);
        if (downCombo === upCombo) {
            showNotification('Increase and decrease oracle size shortcuts must be different', 'error');
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
