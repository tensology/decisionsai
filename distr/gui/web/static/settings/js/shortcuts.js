// Shortcut Keys settings — badge-style hotkey capture

// ---------------------------------------------------------------------------
// Styles injected once
// ---------------------------------------------------------------------------
(function _injectHotkeyStyles() {
    if (document.getElementById('_hotkey_capture_styles')) return;
    const style = document.createElement('style');
    style.id = '_hotkey_capture_styles';
    style.textContent = `
        .hotkey-capture {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .hotkey-display {
            flex: 1;
            min-height: 38px;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
            padding: 6px 10px;
            background: #0d1117;
            border: 1px solid #565869;
            border-radius: 6px;
            cursor: pointer;
            outline: none;
            transition: border-color 0.15s;
            user-select: none;
        }
        .hotkey-display:hover {
            border-color: #7a7c8c;
        }
        .hotkey-display:focus,
        .hotkey-display.capturing {
            border-color: #10a37f;
            box-shadow: 0 0 0 2px rgba(16,163,127,0.25);
        }
        .hotkey-display.capturing {
            animation: hk-pulse 1s ease-in-out infinite;
        }
        @keyframes hk-pulse {
            0%, 100% { box-shadow: 0 0 0 2px rgba(16,163,127,0.25); }
            50%       { box-shadow: 0 0 0 4px rgba(16,163,127,0.45); }
        }
        .hotkey-placeholder {
            color: #6b7280;
            font-size: 13px;
        }
        .hotkey-capturing-text {
            color: #10a37f;
            font-size: 13px;
            font-style: italic;
        }
        .key-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 2px 7px;
            background: #1e2430;
            border: 1px solid #565869;
            border-radius: 4px;
            color: #ececf1;
            font-size: 12px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            line-height: 1.5;
            white-space: nowrap;
        }
        .hotkey-clear {
            flex-shrink: 0;
            width: 26px;
            height: 26px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: transparent;
            border: 1px solid #565869;
            border-radius: 4px;
            color: #7a7c8c;
            font-size: 12px;
            cursor: pointer;
            transition: color 0.15s, border-color 0.15s;
        }
        .hotkey-clear:hover {
            color: #ececf1;
            border-color: #9ca3af;
        }
    `;
    document.head.appendChild(style);
})();

// ---------------------------------------------------------------------------
// Modifier / key display maps
// ---------------------------------------------------------------------------
const _MOD_SYMBOLS = {
    control: '⌃',   // ⌃
    command: '⌘',   // ⌘
    option:  '⌥',   // ⌥
    shift:   '⇧',   // ⇧
};

// Ordered for display: control, option, shift, command
const _MOD_ORDER = ['control', 'option', 'shift', 'command'];

const _KEY_DISPLAY = {
    left_arrow:    '←',   // ←
    right_arrow:   '→',   // →
    up_arrow:      '↑',   // ↑
    down_arrow:    '↓',   // ↓
    left_bracket:  '[',
    right_bracket: ']',
    minus:         '-',
    plus:          '+',
    equal:         '=',
    grave:         '`',
    comma:         ',',
    period:        '.',
    slash:         '/',
    semicolon:     ';',
    quote:         "'",
    backslash:     '\\',
};

// pynput key name → internal name
const _PYNPUT_TO_INTERNAL = {
    // modifiers
    ctrl_l: 'control', ctrl_r: 'control', ctrl: 'control',
    alt_l: 'option',   alt_r: 'option',   alt: 'option',
    alt_gr: 'option',
    cmd_l: 'command',  cmd_r: 'command',  cmd: 'command',
    shift_l: 'shift',  shift_r: 'shift',  shift: 'shift',
    // arrows
    left: 'left_arrow',  right: 'right_arrow',
    up: 'up_arrow',      down: 'down_arrow',
};

const _MODIFIER_VALUES = new Set(['control', 'option', 'shift', 'command']);

// Normalise a browser KeyboardEvent to an internal key name
function _normKey(e) {
    const k = e.key;
    // Modifiers
    if (k === 'Control')  return 'control';
    if (k === 'Alt')      return 'option';
    if (k === 'Meta')     return 'command';
    if (k === 'Shift')    return 'shift';
    // Arrows
    if (k === 'ArrowLeft')  return 'left_arrow';
    if (k === 'ArrowRight') return 'right_arrow';
    if (k === 'ArrowUp')    return 'up_arrow';
    if (k === 'ArrowDown')  return 'down_arrow';
    // Special
    if (k === '[' || k === '{') return 'left_bracket';
    if (k === ']' || k === '}') return 'right_bracket';
    if (k === '-' || k === '_') return 'minus';
    if (k === '+') return 'plus';
    if (k === '=' || k === '+') return 'equal';
    if (k === '`' || k === '~') return 'grave';
    if (k === ',') return 'comma';
    if (k === '.') return 'period';
    if (k === '/') return 'slash';
    if (k === ';') return 'semicolon';
    if (k === "'") return 'quote';
    if (k === '\\') return 'backslash';
    if (k.length === 1 && /[a-z0-9]/i.test(k)) return k.toLowerCase();
    return null;
}

function _isModifier(k) {
    return _MODIFIER_VALUES.has(k);
}

function _keyLabel(internalKey) {
    if (!internalKey) return '';
    if (_KEY_DISPLAY[internalKey]) return _KEY_DISPLAY[internalKey];
    return internalKey.toUpperCase();
}

function _modLabel(mod) {
    return _MOD_SYMBOLS[mod] || mod.toUpperCase();
}

function _renderBadges(modifiers, key) {
    // modifiers: Set or Array of strings; key: string or null
    const mods = _MOD_ORDER.filter(m => (modifiers instanceof Set ? modifiers.has(m) : modifiers.includes(m)));
    const parts = [...mods.map(_modLabel)];
    if (key && !_isModifier(key)) parts.push(_keyLabel(key));
    return parts.map(p => `<span class="key-badge">${p}</span>`).join('');
}

// ---------------------------------------------------------------------------
// Decode stored value string → { modifiers: Set, key: string|null }
// For modifier-key: modifier="control_command", key="d"
// For ptt-single: field="option" (single modifier)
// For modifier-only: field="option_command"
// ---------------------------------------------------------------------------
function _decodeModifierValue(val) {
    if (!val) return new Set();
    const parts = val.split('_').filter(Boolean);
    const mods = new Set();
    for (const p of parts) {
        if (_MODIFIER_VALUES.has(p)) mods.add(p);
    }
    return mods;
}

function _encodeModifiers(mods) {
    const ordered = _MOD_ORDER.filter(m => mods.has(m));
    return ordered.join('_');
}

// ---------------------------------------------------------------------------
// Render current value into the display div
// ---------------------------------------------------------------------------
function _setHotkeyDisplay(captureEl, modifierValue, keyValue) {
    const display = captureEl.querySelector('.hotkey-display');
    if (!display) return;
    const mods = _decodeModifierValue(modifierValue);
    const key = (keyValue && !_isModifier(keyValue)) ? keyValue : null;
    if (mods.size === 0 && !key) {
        display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
    } else {
        display.innerHTML = _renderBadges(mods, key);
    }
}

// ---------------------------------------------------------------------------
// Init all .hotkey-capture elements
// ---------------------------------------------------------------------------
function initHotkeyCapture() {
    document.querySelectorAll('.hotkey-capture').forEach(captureEl => {
        const type = captureEl.dataset.type;
        const display = captureEl.querySelector('.hotkey-display');
        const clearBtn = captureEl.querySelector('.hotkey-clear');
        if (!display) return;

        let capturing = false;
        let pressedMods = new Set();
        let pressedKeys = new Set();

        function startCapturing() {
            capturing = true;
            pressedMods.clear();
            pressedKeys.clear();
            display.classList.add('capturing');
            display.innerHTML = '<span class="hotkey-capturing-text">Press keys…</span>';
        }

        function stopCapturing() {
            capturing = false;
            display.classList.remove('capturing');
        }

        function commitValue() {
            // Filter to actual held modifiers only
            const heldMods = new Set([...pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
            // Non-modifier keys
            const actionKeys = [...pressedKeys].filter(k => !_isModifier(k));
            const actionKey = actionKeys[actionKeys.length - 1] || null;

            if (type === 'ptt-single') {
                // Only capture one modifier
                const field = captureEl.dataset.field;
                const input = document.getElementById(field);
                const modArr = _MOD_ORDER.filter(m => heldMods.has(m));
                const chosenMod = modArr[0] || null;
                if (input) input.value = chosenMod || '';
                if (chosenMod) {
                    display.innerHTML = `<span class="key-badge">${_modLabel(chosenMod)}</span>`;
                } else {
                    display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
                }
            } else if (type === 'modifier-key') {
                const modField = captureEl.dataset.modifierField;
                const keyField = captureEl.dataset.keyField;
                const modInput = modField ? document.getElementById(modField) : null;
                const keyInput = keyField ? document.getElementById(keyField) : null;
                const modVal = _encodeModifiers(heldMods);
                if (modInput) modInput.value = modVal;
                if (keyInput) keyInput.value = actionKey || '';
                if (heldMods.size > 0 || actionKey) {
                    display.innerHTML = _renderBadges(heldMods, actionKey);
                } else {
                    display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
                }
            } else if (type === 'modifier-only') {
                const field = captureEl.dataset.field;
                const input = document.getElementById(field);
                const modVal = _encodeModifiers(heldMods);
                if (input) input.value = modVal;
                if (heldMods.size > 0) {
                    display.innerHTML = _renderBadges(heldMods, null);
                } else {
                    display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
                }
            }
        }

        display.addEventListener('click', () => {
            if (!capturing) startCapturing();
        });

        display.addEventListener('focus', () => {
            // do nothing on focus alone — only start when clicked
        });

        display.addEventListener('blur', () => {
            if (capturing) {
                stopCapturing();
                // Restore previous display from hidden inputs
                _restoreDisplayFromInputs(captureEl);
            }
        });

        display.addEventListener('keydown', e => {
            if (!capturing) return;
            e.preventDefault();
            e.stopPropagation();
            const k = _normKey(e);
            if (!k) return;
            if (_isModifier(k)) {
                pressedMods.add(k);
            } else {
                pressedKeys.add(k);
            }
            // Show live preview
            const heldMods = new Set([...pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
            const actionKeys = [...pressedKeys].filter(k2 => !_isModifier(k2));
            const actionKey = actionKeys[actionKeys.length - 1] || null;
            if (type === 'ptt-single') {
                const modArr = _MOD_ORDER.filter(m => heldMods.has(m));
                display.innerHTML = modArr.length
                    ? `<span class="key-badge">${_modLabel(modArr[0])}</span>`
                    : '<span class="hotkey-capturing-text">Press modifier…</span>';
            } else {
                if (heldMods.size > 0 || actionKey) {
                    display.innerHTML = _renderBadges(heldMods, actionKey);
                } else {
                    display.innerHTML = '<span class="hotkey-capturing-text">Press keys…</span>';
                }
            }
        });

        display.addEventListener('keyup', e => {
            if (!capturing) return;
            e.preventDefault();
            e.stopPropagation();
            const k = _normKey(e);
            if (!k) return;
            if (_isModifier(k)) {
                pressedMods.delete(k);
            } else {
                pressedKeys.delete(k);
            }
            // All keys released — commit
            if (pressedMods.size === 0 && pressedKeys.size === 0) {
                stopCapturing();
                commitValue();
            }
        });

        // Clear button
        if (clearBtn) {
            clearBtn.addEventListener('click', e => {
                e.stopPropagation();
                capturing = false;
                display.classList.remove('capturing');
                pressedMods.clear();
                pressedKeys.clear();
                display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
                if (type === 'ptt-single') {
                    const field = captureEl.dataset.field;
                    const input = document.getElementById(field);
                    if (input) input.value = '';
                } else if (type === 'modifier-key') {
                    const modInput = document.getElementById(captureEl.dataset.modifierField);
                    const keyInput = document.getElementById(captureEl.dataset.keyField);
                    if (modInput) modInput.value = '';
                    if (keyInput) keyInput.value = '';
                } else if (type === 'modifier-only') {
                    const input = document.getElementById(captureEl.dataset.field);
                    if (input) input.value = '';
                }
            });
        }
    });
}

function _restoreDisplayFromInputs(captureEl) {
    const type = captureEl.dataset.type;
    if (type === 'ptt-single') {
        const field = captureEl.dataset.field;
        const input = document.getElementById(field);
        _setHotkeyDisplay(captureEl, input ? input.value : '', null);
    } else if (type === 'modifier-key') {
        const modInput = document.getElementById(captureEl.dataset.modifierField);
        const keyInput = document.getElementById(captureEl.dataset.keyField);
        _setHotkeyDisplay(captureEl, modInput ? modInput.value : '', keyInput ? keyInput.value : '');
    } else if (type === 'modifier-only') {
        const field = captureEl.dataset.field;
        const input = document.getElementById(field);
        _setHotkeyDisplay(captureEl, input ? input.value : '', null);
    }
}

// ---------------------------------------------------------------------------
// Load shortcut settings
// ---------------------------------------------------------------------------
async function loadShortcutSettings() {
    try {
        const response = await fetch('/api/shortcuts');
        if (!response.ok) throw new Error('Failed to load shortcut settings');
        const settings = await response.json();

        // Helper to set a modifier-key capture element
        function _setMK(modifierId, keyId, modVal, keyVal) {
            const el = document.querySelector(
                `.hotkey-capture[data-modifier-field="${modifierId}"][data-key-field="${keyId}"]`
            );
            const modInput = document.getElementById(modifierId);
            const keyInput = document.getElementById(keyId);
            if (modInput) modInput.value = modVal || '';
            if (keyInput) keyInput.value = keyVal || '';
            if (el) _setHotkeyDisplay(el, modVal || '', keyVal || '');
        }

        // Helper for ptt-single
        function _setPTT(fieldId, val) {
            const el = document.querySelector(`.hotkey-capture[data-field="${fieldId}"]`);
            const input = document.getElementById(fieldId);
            if (input) input.value = val || '';
            if (el) _setHotkeyDisplay(el, val || '', null);
        }

        // Helper for modifier-only
        function _setMO(fieldId, val) {
            const el = document.querySelector(`.hotkey-capture[data-field="${fieldId}"]`);
            const input = document.getElementById(fieldId);
            if (input) input.value = val || '';
            if (el) _setHotkeyDisplay(el, val || '', null);
        }

        // PTT
        _setPTT('shortcuts_global_ptt_hotkey_primary',   settings.global_ptt_hotkey_primary   || 'option');
        _setPTT('shortcuts_global_ptt_hotkey_secondary', settings.global_ptt_hotkey_secondary || 'command');

        // Recording
        _setMK('shortcuts_recording_hotkey_modifier', 'shortcuts_recording_hotkey_key',
               settings.recording_hotkey_modifier || 'option_command',
               settings.recording_hotkey_key      || 's');

        // Skin nav
        _setMK('shortcuts_skin_nav_hotkey_previous_modifier', 'shortcuts_skin_nav_hotkey_previous_key',
               settings.skin_nav_hotkey_previous_modifier || 'control_command',
               settings.skin_nav_hotkey_previous_key      || 'left_arrow');
        _setMK('shortcuts_skin_nav_hotkey_next_modifier', 'shortcuts_skin_nav_hotkey_next_key',
               settings.skin_nav_hotkey_next_modifier || 'control_command',
               settings.skin_nav_hotkey_next_key      || 'right_arrow');

        // Skin select (modifier-only)
        _setMO('shortcuts_skin_select_hotkey_modifier', settings.skin_select_hotkey_modifier || 'option_command');

        // Web hotkeys
        _setMK('shortcuts_web_hotkey_chat_modifier',        'shortcuts_web_hotkey_chat_key',
               settings.web_hotkey_chat_modifier        || 'option_command', settings.web_hotkey_chat_key        || 'c');
        _setMK('shortcuts_web_hotkey_projects_modifier',    'shortcuts_web_hotkey_projects_key',
               settings.web_hotkey_projects_modifier    || 'option_command', settings.web_hotkey_projects_key    || 'j');
        _setMK('shortcuts_web_hotkey_actions_modifier',     'shortcuts_web_hotkey_actions_key',
               settings.web_hotkey_actions_modifier     || 'option_command', settings.web_hotkey_actions_key     || 'a');
        _setMK('shortcuts_web_hotkey_snippets_modifier',    'shortcuts_web_hotkey_snippets_key',
               settings.web_hotkey_snippets_modifier    || 'option_command', settings.web_hotkey_snippets_key    || 'n');
        _setMK('shortcuts_web_hotkey_workflows_modifier',   'shortcuts_web_hotkey_workflows_key',
               settings.web_hotkey_workflows_modifier   || 'option_command', settings.web_hotkey_workflows_key   || 'w');
        _setMK('shortcuts_web_hotkey_preferences_modifier', 'shortcuts_web_hotkey_preferences_key',
               settings.web_hotkey_preferences_modifier || 'option_command', settings.web_hotkey_preferences_key || 'grave');

        // Dictation
        _setMK('shortcuts_dictation_hotkey_modifier', 'shortcuts_dictation_hotkey_key',
               settings.dictation_hotkey_modifier || 'control_command',
               settings.dictation_hotkey_key      || 'd');

        // Oracle size
        _setMK('shortcuts_oracle_size_hotkey_decrease_modifier', 'shortcuts_oracle_size_hotkey_decrease_key',
               settings.oracle_size_hotkey_decrease_modifier || 'control_command',
               settings.oracle_size_hotkey_decrease_key      || 'down_arrow');
        _setMK('shortcuts_oracle_size_hotkey_increase_modifier', 'shortcuts_oracle_size_hotkey_increase_key',
               settings.oracle_size_hotkey_increase_modifier || 'control_command',
               settings.oracle_size_hotkey_increase_key      || 'up_arrow');

        // Checkboxes
        const pttEnabled = document.getElementById('shortcuts_global_ptt_hotkey_enabled');
        if (pttEnabled) pttEnabled.checked = settings.global_ptt_hotkey_enabled !== undefined ? settings.global_ptt_hotkey_enabled : true;
        const recEnabled = document.getElementById('shortcuts_recording_hotkey_enabled');
        if (recEnabled) recEnabled.checked = settings.recording_hotkey_enabled !== undefined ? settings.recording_hotkey_enabled : true;
        const dictEnabled = document.getElementById('shortcuts_dictation_hotkey_enabled');
        if (dictEnabled) dictEnabled.checked = settings.dictation_hotkey_enabled !== undefined ? settings.dictation_hotkey_enabled : false;

    } catch (error) {
        console.error('Error loading shortcut settings:', error);
        showNotification('Failed to load shortcut settings: ' + error.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Save shortcut settings
// ---------------------------------------------------------------------------
function _getHiddenVal(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
}

function _getCheckVal(id, defaultVal) {
    const el = document.getElementById(id);
    return el ? el.checked : defaultVal;
}

function _comboStr(modifier, key) {
    return (modifier || '') + '+' + (key || '');
}

async function saveShortcutSettings() {
    try {
        const settings = {
            global_ptt_hotkey_enabled:   _getCheckVal('shortcuts_global_ptt_hotkey_enabled', true),
            global_ptt_hotkey_primary:   _getHiddenVal('shortcuts_global_ptt_hotkey_primary'),
            global_ptt_hotkey_secondary: _getHiddenVal('shortcuts_global_ptt_hotkey_secondary'),

            oracle_size_hotkey_decrease_modifier: _getHiddenVal('shortcuts_oracle_size_hotkey_decrease_modifier'),
            oracle_size_hotkey_decrease_key:      _getHiddenVal('shortcuts_oracle_size_hotkey_decrease_key'),
            oracle_size_hotkey_increase_modifier: _getHiddenVal('shortcuts_oracle_size_hotkey_increase_modifier'),
            oracle_size_hotkey_increase_key:      _getHiddenVal('shortcuts_oracle_size_hotkey_increase_key'),

            recording_hotkey_enabled:  _getCheckVal('shortcuts_recording_hotkey_enabled', true),
            recording_hotkey_modifier: _getHiddenVal('shortcuts_recording_hotkey_modifier'),
            recording_hotkey_key:      _getHiddenVal('shortcuts_recording_hotkey_key'),

            skin_nav_hotkey_previous_modifier: _getHiddenVal('shortcuts_skin_nav_hotkey_previous_modifier'),
            skin_nav_hotkey_previous_key:      _getHiddenVal('shortcuts_skin_nav_hotkey_previous_key'),
            skin_nav_hotkey_next_modifier:     _getHiddenVal('shortcuts_skin_nav_hotkey_next_modifier'),
            skin_nav_hotkey_next_key:          _getHiddenVal('shortcuts_skin_nav_hotkey_next_key'),
            skin_select_hotkey_modifier:       _getHiddenVal('shortcuts_skin_select_hotkey_modifier'),

            web_hotkey_chat_modifier:        _getHiddenVal('shortcuts_web_hotkey_chat_modifier'),
            web_hotkey_chat_key:             _getHiddenVal('shortcuts_web_hotkey_chat_key'),
            web_hotkey_projects_modifier:    _getHiddenVal('shortcuts_web_hotkey_projects_modifier'),
            web_hotkey_projects_key:         _getHiddenVal('shortcuts_web_hotkey_projects_key'),
            web_hotkey_actions_modifier:     _getHiddenVal('shortcuts_web_hotkey_actions_modifier'),
            web_hotkey_actions_key:          _getHiddenVal('shortcuts_web_hotkey_actions_key'),
            web_hotkey_snippets_modifier:    _getHiddenVal('shortcuts_web_hotkey_snippets_modifier'),
            web_hotkey_snippets_key:         _getHiddenVal('shortcuts_web_hotkey_snippets_key'),
            web_hotkey_workflows_modifier:   _getHiddenVal('shortcuts_web_hotkey_workflows_modifier'),
            web_hotkey_workflows_key:        _getHiddenVal('shortcuts_web_hotkey_workflows_key'),
            web_hotkey_preferences_modifier: _getHiddenVal('shortcuts_web_hotkey_preferences_modifier'),
            web_hotkey_preferences_key:      _getHiddenVal('shortcuts_web_hotkey_preferences_key'),

            dictation_hotkey_enabled:  _getCheckVal('shortcuts_dictation_hotkey_enabled', false),
            dictation_hotkey_modifier: _getHiddenVal('shortcuts_dictation_hotkey_modifier'),
            dictation_hotkey_key:      _getHiddenVal('shortcuts_dictation_hotkey_key'),
        };

        if (settings.global_ptt_hotkey_primary === settings.global_ptt_hotkey_secondary) {
            showNotification('Pick two different keys for the global PTT hotkey', 'error');
            return;
        }

        const downCombo = _comboStr(settings.oracle_size_hotkey_decrease_modifier, settings.oracle_size_hotkey_decrease_key);
        const upCombo   = _comboStr(settings.oracle_size_hotkey_increase_modifier, settings.oracle_size_hotkey_increase_key);
        if (downCombo === upCombo) {
            showNotification('Increase and decrease oracle size shortcuts must be different', 'error');
            return;
        }

        const recordingCombo = _comboStr(settings.recording_hotkey_modifier, settings.recording_hotkey_key);
        if (recordingCombo === downCombo || recordingCombo === upCombo) {
            showNotification('Recording shortcut must differ from oracle size shortcuts', 'error');
            return;
        }

        const response = await fetch('/api/shortcuts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function _initShortcuts() {
    if (!document.getElementById('tab-shortcuts')) return;
    initHotkeyCapture();
    loadShortcutSettings();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initShortcuts);
} else {
    _initShortcuts();
}

window.loadShortcutSettings = loadShortcutSettings;
window.saveShortcutSettings = saveShortcutSettings;
window.initHotkeyCapture = initHotkeyCapture;
