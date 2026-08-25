// Shortcut Keys settings — badge-style hotkey capture

const _hiddenTicketDictationSettings = {
    ticket_dictation_hotkey_enabled: false,
    ticket_dictation_hotkey_modifier: 'control_shift',
    ticket_dictation_hotkey_key: '',
    dictation_ticket_use_llm: true,
    dictation_ticket_model: 'qwen2.5:0.5b',
    dictation_ticket_timeout: '1.2',
    dictation_ticket_prompt: ''
};

function _rememberHiddenTicketDictationSettings(s) {
    _hiddenTicketDictationSettings.ticket_dictation_hotkey_enabled = false;
    _hiddenTicketDictationSettings.ticket_dictation_hotkey_modifier = s.ticket_dictation_hotkey_modifier || 'control_shift';
    _hiddenTicketDictationSettings.ticket_dictation_hotkey_key = s.ticket_dictation_hotkey_key || '';
    _hiddenTicketDictationSettings.dictation_ticket_use_llm = s.dictation_ticket_use_llm !== undefined ? !!s.dictation_ticket_use_llm : true;
    _hiddenTicketDictationSettings.dictation_ticket_model = s.dictation_ticket_model || 'qwen2.5:0.5b';
    _hiddenTicketDictationSettings.dictation_ticket_timeout = s.dictation_ticket_timeout || '1.2';
    _hiddenTicketDictationSettings.dictation_ticket_prompt = s.dictation_ticket_prompt || '';
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
(function _injectHotkeyStyles() {
    if (document.getElementById('_hotkey_capture_styles')) return;
    const style = document.createElement('style');
    style.id = '_hotkey_capture_styles';
    style.textContent = `
        .hotkey-capture {
            display: flex;
            align-items: center;
            gap: 0;
            position: relative;
        }
        .hotkey-display {
            flex: 1;
            min-height: 38px;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
            padding: 6px 34px 6px 10px;
            background: #0d1117;
            border: 1px solid #565869;
            border-radius: 6px;
            cursor: pointer;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
            user-select: none;
        }
        .hotkey-display:hover {
            border-color: #7a7c8c;
        }
        .hotkey-display.capturing {
            border-color: #10a37f;
            box-shadow: 0 0 0 2px rgba(16,163,127,0.30);
            animation: hk-pulse 1s ease-in-out infinite;
        }
        /* Green = changed but not yet saved */
        .hotkey-display.changed {
            border-color: #10a37f;
            box-shadow: 0 0 0 1px rgba(16,163,127,0.20);
        }
        @keyframes hk-pulse {
            0%, 100% { box-shadow: 0 0 0 2px rgba(16,163,127,0.25); }
            50%       { box-shadow: 0 0 0 4px rgba(16,163,127,0.50); }
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
            padding: 2px 8px;
            background: #1e2430;
            border: 1px solid #565869;
            border-radius: 4px;
            color: #ececf1;
            font-size: 12px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            line-height: 1.6;
            white-space: nowrap;
        }
        .hotkey-clear {
            position: absolute;
            top: 50%;
            right: 9px;
            transform: translateY(-50%);
            display: flex;
            align-items: center;
            justify-content: center;
            width: auto;
            height: auto;
            border: none;
            color: #ffffff;
            font-size: 14px;
            line-height: 1;
            background: transparent;
            cursor: pointer;
            transition: color 0.15s ease, opacity 0.15s ease, transform 0.15s ease;
            padding: 0;
            margin: 0;
            opacity: 1;
            width: 20px;
            height: 20px;
            border: 1px solid rgba(120, 129, 141, 0.65);
            border-radius: 9999px;
            background: rgba(120, 129, 141, 0.14);
        }
        .hotkey-clear:hover {
            color: #ffffff;
            background: rgba(120, 129, 141, 0.2);
            border-color: rgba(148, 163, 184, 0.85);
            opacity: 1;
            transform: translateY(-50%) scale(1.04);
        }
        .hotkey-clear svg {
            width: 12px;
            height: 12px;
            display: block;
        }
    `;
    document.head.appendChild(style);
})();

// ---------------------------------------------------------------------------
// Key maps
// ---------------------------------------------------------------------------
const _MOD_SYMBOLS = { control: '⌃', command: '⌘', option: '⌥', shift: '⇧' };
const _MOD_ORDER   = ['control', 'option', 'shift', 'command'];
const _MODIFIER_VALUES = new Set(['control', 'option', 'shift', 'command']);

const _KEY_DISPLAY = {
    left_arrow: '←', right_arrow: '→', up_arrow: '↑', down_arrow: '↓',
    left_bracket: '[', right_bracket: ']',
    minus: '-', plus: '+', equal: '=', grave: '`',
    comma: ',', period: '.', slash: '/', semicolon: ';', quote: "'", backslash: '\\',
};

function _normKey(e) {
    const k = e.key;
    if (k === 'Control')    return 'control';
    if (k === 'Alt')        return 'option';
    if (k === 'Meta')       return 'command';
    if (k === 'Shift')      return 'shift';
    if (k === 'ArrowLeft')  return 'left_arrow';
    if (k === 'ArrowRight') return 'right_arrow';
    if (k === 'ArrowUp')    return 'up_arrow';
    if (k === 'ArrowDown')  return 'down_arrow';
    if (k === '[' || k === '{') return 'left_bracket';
    if (k === ']' || k === '}') return 'right_bracket';
    if (k === '-' || k === '_') return 'minus';
    if (k === '=')          return 'equal';
    if (k === '`' || k === '~') return 'grave';
    if (k === ',')          return 'comma';
    if (k === '.')          return 'period';
    if (k === '/')          return 'slash';
    if (k === ';')          return 'semicolon';
    if (k === "'")          return 'quote';
    if (k === '\\')         return 'backslash';
    if (k.length === 1 && /[a-z0-9]/i.test(k)) return k.toLowerCase();
    return null;
}

function _isModifier(k) { return _MODIFIER_VALUES.has(k); }

function _keyLabel(k) {
    if (!k) return '';
    return _KEY_DISPLAY[k] || k.toUpperCase();
}

function _modLabel(m) { return _MOD_SYMBOLS[m] || m.toUpperCase(); }

function _renderBadges(mods, key) {
    const ordered = _MOD_ORDER.filter(m => (mods instanceof Set ? mods.has(m) : mods.includes(m)));
    const parts = ordered.map(_modLabel);
    if (key && !_isModifier(key)) parts.push(_keyLabel(key));
    return parts.map(p => `<span class="key-badge">${p}</span>`).join('');
}

function _decodeModifierValue(val) {
    const mods = new Set();
    if (!val) return mods;
    for (const p of val.split('_')) {
        if (_MODIFIER_VALUES.has(p)) mods.add(p);
    }
    return mods;
}

function _encodeModifiers(mods) {
    return _MOD_ORDER.filter(m => mods.has(m)).join('_');
}

function _sameModifierSet(a, b) {
    const aa = _decodeModifierValue(a);
    const bb = _decodeModifierValue(b);
    if (aa.size !== bb.size) return false;
    for (const mod of aa) {
        if (!bb.has(mod)) return false;
    }
    return aa.size > 0;
}

function _shortcutLabel(modifierValue, keyValue = null) {
    const mods = _decodeModifierValue(modifierValue);
    const badges = _MOD_ORDER.filter(m => mods.has(m)).map(_modLabel);
    if (keyValue) badges.push(_keyLabel(keyValue));
    return badges.join(' + ');
}

function _valueOrDefault(value, fallback) {
    return value === undefined || value === null ? fallback : value;
}

function _shortcutSignature(modifierValue, keyValue = '') {
    const mods = _MOD_ORDER.filter(m => _decodeModifierValue(modifierValue).has(m));
    if (mods.length === 0) return null;
    return `${mods.join('_')}::${keyValue || ''}`;
}

function _validateShortcutCollisions(shortcuts) {
    const seen = new Map();
    const seenChanged = new Map();
    for (const shortcut of shortcuts) {
        if (!shortcut.enabled) continue;
        const signature = _shortcutSignature(shortcut.modifier, shortcut.key);
        if (!signature) {
            return `${shortcut.name} requires at least one modifier key`;
        }
        const enabledChanged = shortcut.enabledField
            ? String(shortcut.enabled) !== (_savedValues[shortcut.enabledField] || '')
            : false;
        const modifierChanged = shortcut.modifierField
            ? (shortcut.modifier || '') !== (_savedValues[shortcut.modifierField] || '')
            : false;
        const keyChanged = shortcut.keyField
            ? (shortcut.key || '') !== (_savedValues[shortcut.keyField] || '')
            : false;
        const changed = enabledChanged || modifierChanged || keyChanged;
        if (seen.has(signature)) {
            const other = seen.get(signature);
            if (changed || seenChanged.get(signature)) {
                return `${shortcut.name} overlaps ${other.name} (${_shortcutLabel(shortcut.modifier, shortcut.key)}). Choose a different shortcut combo.`;
            }
        }
        seen.set(signature, shortcut);
        seenChanged.set(signature, changed);
    }
    return null;
}

// ---------------------------------------------------------------------------
// "Saved" baseline — used to detect unsaved changes
// ---------------------------------------------------------------------------
const _savedValues = {}; // field-id → string value
let _shortcutAutosaveTimer = null;
let _shortcutAutosaveInFlight = false;

function _recordSaved(id, val) { _savedValues[id] = val || ''; }

function _shortcutsHavePendingChanges() {
    if (String(_cb('shortcuts_global_ptt_hotkey_enabled', true)) !== (_savedValues.shortcuts_global_ptt_hotkey_enabled || '')) return true;
    if (String(_cb('shortcuts_recording_hotkey_enabled', true)) !== (_savedValues.shortcuts_recording_hotkey_enabled || '')) return true;
    if (String(_cb('shortcuts_dictation_hotkey_enabled', false)) !== (_savedValues.shortcuts_dictation_hotkey_enabled || '')) return true;
    return Array.from(document.querySelectorAll('.hotkey-capture')).some(_isChanged);
}

function _queueShortcutAutosave() {
    if (_shortcutAutosaveTimer) clearTimeout(_shortcutAutosaveTimer);
    _shortcutAutosaveTimer = setTimeout(async () => {
        _shortcutAutosaveTimer = null;
        if (_shortcutAutosaveInFlight || !_shortcutsHavePendingChanges()) return;
        _shortcutAutosaveInFlight = true;
        try {
            await saveShortcutSettings({ silentSuccess: true });
        } finally {
            _shortcutAutosaveInFlight = false;
            if (_shortcutsHavePendingChanges()) _queueShortcutAutosave();
        }
    }, 250);
}

function _isChanged(captureEl) {
    const type = captureEl.dataset.type;
    if (type === 'ptt-combo') {
        const p = document.getElementById(captureEl.dataset.primaryField);
        const s = document.getElementById(captureEl.dataset.secondaryField);
        const pid = captureEl.dataset.primaryField;
        const sid = captureEl.dataset.secondaryField;
        return (p && p.value !== (_savedValues[pid] || '')) ||
               (s && s.value !== (_savedValues[sid] || ''));
    }
    if (type === 'ptt-single') {
        const fid = captureEl.dataset.field;
        const inp = document.getElementById(fid);
        return inp && inp.value !== (_savedValues[fid] || '');
    }
    if (type === 'modifier-key') {
        const mid = captureEl.dataset.modifierField;
        const kid = captureEl.dataset.keyField;
        const mi  = document.getElementById(mid);
        const ki  = document.getElementById(kid);
        return (mi && mi.value !== (_savedValues[mid] || '')) ||
               (ki && ki.value !== (_savedValues[kid] || ''));
    }
    if (type === 'modifier-only') {
        const fid = captureEl.dataset.field;
        const inp = document.getElementById(fid);
        return inp && inp.value !== (_savedValues[fid] || '');
    }
    return false;
}

function _updateChangedState(captureEl) {
    const display = captureEl.querySelector('.hotkey-display');
    if (!display) return;
    if (_isChanged(captureEl)) {
        display.classList.add('changed');
    } else {
        display.classList.remove('changed');
    }
}

function _markAllSaved() {
    // After a successful save, update baseline and clear green indicators
    document.querySelectorAll('.hotkey-capture').forEach(el => {
        const type = el.dataset.type;
        if (type === 'ptt-combo') {
            const p = document.getElementById(el.dataset.primaryField);
            const s = document.getElementById(el.dataset.secondaryField);
            if (p) _recordSaved(el.dataset.primaryField,   p.value);
            if (s) _recordSaved(el.dataset.secondaryField, s.value);
        } else if (type === 'ptt-single') {
            const inp = document.getElementById(el.dataset.field);
            if (inp) _recordSaved(el.dataset.field, inp.value);
        } else if (type === 'modifier-key') {
            const mi = document.getElementById(el.dataset.modifierField);
            const ki = document.getElementById(el.dataset.keyField);
            if (mi) _recordSaved(el.dataset.modifierField, mi.value);
            if (ki) _recordSaved(el.dataset.keyField,      ki.value);
        } else if (type === 'modifier-only') {
            const inp = document.getElementById(el.dataset.field);
            if (inp) _recordSaved(el.dataset.field, inp.value);
        }
        el.querySelector('.hotkey-display')?.classList.remove('changed');
    });
}

// ---------------------------------------------------------------------------
// Active capture state — document-level to reliably catch all key events
// ---------------------------------------------------------------------------
let _activeCapture = null; // { el, type, pressedMods, pressedKeys, peakMods, peakActionKey }

document.addEventListener('keydown', e => {
    if (!_activeCapture) return;
    e.preventDefault();
    e.stopPropagation();
    const k = _normKey(e);
    if (!k) return;
    if (_isModifier(k)) _activeCapture.pressedMods.add(k);
    else                _activeCapture.pressedKeys.add(k);

    // Remember the largest simultaneous modifier chord. On release order matters:
    // if user lifts ⌘ then ⌃, the final keyup snapshot only had ⌃ — we'd lose ⌘ otherwise.
    const hm = new Set([..._activeCapture.pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
    const peakSz = _activeCapture.peakMods?.size ?? 0;
    if (hm.size > peakSz) {
        _activeCapture.peakMods = new Set(hm);
    }

    const rawActions = [..._activeCapture.pressedKeys].filter(x => !_isModifier(x));
    if (rawActions.length > 0) {
        _activeCapture.peakActionKey = rawActions[rawActions.length - 1];
    }

    _livePreview();
}, true);

document.addEventListener('keyup', e => {
    if (!_activeCapture) return;
    e.preventDefault();
    e.stopPropagation();
    const k = _normKey(e);
    if (!k) return;

    // If we delete `k` from tracking sets, would nothing remain held?
    const modsAfter = new Set(_activeCapture.pressedMods);
    const keysAfter = new Set(_activeCapture.pressedKeys);
    if (_isModifier(k)) modsAfter.delete(k);
    else keysAfter.delete(k);

    const finishing = modsAfter.size === 0 && keysAfter.size === 0;

    if (finishing) {
        // Snapshot chord BEFORE clearing sets — commit ran after delete previously,
        // so pressedMods/pressedKeys were always empty ("Click to set" loop).
        const { snapMods, snapKeys } = _snapChordFromActiveCapture();
        _finalizeChordCapture(_activeCapture.el, snapMods, snapKeys);
        _activeCapture = null;
        return;
    }

    if (_isModifier(k)) _activeCapture.pressedMods.delete(k);
    else _activeCapture.pressedKeys.delete(k);
}, true);

// Click outside → commit chord if user pressed anything; otherwise abandon (restore prior)
document.addEventListener('click', e => {
    if (!_activeCapture) return;
    if (!_activeCapture.el.contains(e.target)) {
        if (_captureHasProgress()) {
            const el = _activeCapture.el;
            const { snapMods, snapKeys } = _snapChordFromActiveCapture();
            _activeCapture = null;
            _finalizeChordCapture(el, snapMods, snapKeys);
        } else {
            _cancelCapture();
        }
    }
}, true);

// Escape → cancel
document.addEventListener('keydown', e => {
    if (!_activeCapture) return;
    if (e.key === 'Escape') {
        e.preventDefault();
        _cancelCapture();
    }
});

/** Merge simultaneous-peak + current held state for commit (keyup finish or click-outside). */
function _snapChordFromActiveCapture() {
    if (!_activeCapture) return { snapMods: new Set(), snapKeys: new Set() };
    let snapMods = new Set(_activeCapture.pressedMods);
    let snapKeys = new Set(_activeCapture.pressedKeys);
    if (_activeCapture.peakMods && _activeCapture.peakMods.size > 0) {
        snapMods = new Set(_activeCapture.peakMods);
    }
    if (_activeCapture.peakActionKey) {
        snapKeys = new Set([_activeCapture.peakActionKey]);
    }
    return { snapMods, snapKeys };
}

function _captureHasProgress() {
    if (!_activeCapture) return false;
    const ac = _activeCapture;
    const heldNow = new Set([...ac.pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
    const actionsNow = [...ac.pressedKeys].filter(k => !_isModifier(k));
    return (ac.peakMods && ac.peakMods.size > 0)
        || !!ac.peakActionKey
        || heldNow.size > 0
        || actionsNow.length > 0;
}

function _livePreview() {
    if (!_activeCapture) return;
    const { el, pressedMods, pressedKeys } = _activeCapture;
    const type = el.dataset.type;
    const display = el.querySelector('.hotkey-display');
    if (!display) return;

    const heldMods = new Set([...pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
    const actionKey = [...pressedKeys].filter(k => !_isModifier(k)).slice(-1)[0] || null;

    if (type === 'ptt-combo' || type === 'modifier-only') {
        display.innerHTML = heldMods.size > 0
            ? _renderBadges(heldMods, null)
            : '<span class="hotkey-capturing-text">Press modifier keys…</span>';
    } else if (type === 'ptt-single') {
        const first = _MOD_ORDER.find(m => heldMods.has(m));
        display.innerHTML = first
            ? `<span class="key-badge">${_modLabel(first)}</span>`
            : '<span class="hotkey-capturing-text">Press a modifier…</span>';
    } else {
        // modifier-key
        display.innerHTML = (heldMods.size > 0 || actionKey)
            ? _renderBadges(heldMods, actionKey)
            : '<span class="hotkey-capturing-text">Press keys…</span>';
    }
}

/**
 * Apply captured chord to hidden inputs and display. `pressedMods` / `pressedKeys`
 * must be the chord from immediately before the last keyup (not after clears).
 */
function _finalizeChordCapture(el, pressedMods, pressedKeys) {
    const type = el.dataset.type;
    const heldMods = new Set([...pressedMods].filter(m => _MODIFIER_VALUES.has(m)));
    const actionKeys = [...pressedKeys].filter(k => !_isModifier(k));
    const actionKey  = actionKeys.slice(-1)[0] || null;
    const display    = el.querySelector('.hotkey-display');

    if (type === 'ptt-combo') {
        // Split the held modifiers into primary and secondary
        const ordered = _MOD_ORDER.filter(m => heldMods.has(m));
        const primary   = ordered[0] || null;
        const secondary = ordered[1] || primary; // fall back to same if only one held
        const pi = document.getElementById(el.dataset.primaryField);
        const si = document.getElementById(el.dataset.secondaryField);
        if (pi) pi.value = primary   || '';
        if (si) si.value = secondary || '';
        display && (display.innerHTML = primary
            ? _renderBadges(heldMods.size > 0 ? heldMods : new Set([primary, secondary].filter(Boolean)), null)
            : '<span class="hotkey-placeholder">Click to set</span>');

    } else if (type === 'ptt-single') {
        const first = _MOD_ORDER.find(m => heldMods.has(m)) || null;
        const inp = document.getElementById(el.dataset.field);
        if (inp) inp.value = first || '';
        display && (display.innerHTML = first
            ? `<span class="key-badge">${_modLabel(first)}</span>`
            : '<span class="hotkey-placeholder">Click to set</span>');

    } else if (type === 'modifier-key') {
        const modVal = _encodeModifiers(heldMods);
        const mi = document.getElementById(el.dataset.modifierField);
        const ki = document.getElementById(el.dataset.keyField);
        if (mi) mi.value = modVal    || '';
        if (ki) ki.value = actionKey || '';
        display && (display.innerHTML = (heldMods.size > 0 || actionKey)
            ? _renderBadges(heldMods, actionKey)
            : '<span class="hotkey-placeholder">Click to set</span>');

    } else if (type === 'modifier-only') {
        const modVal = _encodeModifiers(heldMods);
        const inp = document.getElementById(el.dataset.field);
        if (inp) inp.value = modVal || '';
        display && (display.innerHTML = heldMods.size > 0
            ? _renderBadges(heldMods, null)
            : '<span class="hotkey-placeholder">Click to set</span>');
    }

    display && display.classList.remove('capturing');
    _updateChangedState(el);
    _queueShortcutAutosave();
}

function _cancelCapture() {
    if (!_activeCapture) return;
    const { el } = _activeCapture;
    _activeCapture = null;
    const display = el.querySelector('.hotkey-display');
    if (display) display.classList.remove('capturing');
    _restoreDisplayFromInputs(el);
}

function _restoreDisplayFromInputs(el) {
    const type = el.dataset.type;
    if (type === 'ptt-combo') {
        const p = document.getElementById(el.dataset.primaryField);
        const s = document.getElementById(el.dataset.secondaryField);
        const mods = new Set([p?.value, s?.value].filter(v => v && _MODIFIER_VALUES.has(v)));
        const display = el.querySelector('.hotkey-display');
        if (display) display.innerHTML = mods.size > 0
            ? _renderBadges(mods, null)
            : '<span class="hotkey-placeholder">Click to set</span>';
    } else if (type === 'ptt-single') {
        const inp = document.getElementById(el.dataset.field);
        _setHotkeyDisplay(el, inp?.value || '', null);
    } else if (type === 'modifier-key') {
        const mi = document.getElementById(el.dataset.modifierField);
        const ki = document.getElementById(el.dataset.keyField);
        _setHotkeyDisplay(el, mi?.value || '', ki?.value || '');
    } else if (type === 'modifier-only') {
        const inp = document.getElementById(el.dataset.field);
        _setHotkeyDisplay(el, inp?.value || '', null);
    }
}

// ---------------------------------------------------------------------------
// Render stored value into display
// ---------------------------------------------------------------------------
function _setHotkeyDisplay(captureEl, modifierValue, keyValue) {
    const display = captureEl.querySelector('.hotkey-display');
    if (!display) return;
    const mods = _decodeModifierValue(modifierValue);
    const key  = keyValue && !_isModifier(keyValue) ? keyValue : null;
    display.innerHTML = (mods.size === 0 && !key)
        ? '<span class="hotkey-placeholder">Click to set</span>'
        : _renderBadges(mods, key);
}


// ---------------------------------------------------------------------------
// Init all .hotkey-capture widgets
// ---------------------------------------------------------------------------
function initHotkeyCapture() {
    document.querySelectorAll('.hotkey-capture').forEach(captureEl => {
        const display  = captureEl.querySelector('.hotkey-display');
        const clearBtn = captureEl.querySelector('.hotkey-clear');
        if (!display) return;

        // Click on display → start capturing
        display.addEventListener('click', () => {
            if (_activeCapture) _cancelCapture();
            _activeCapture = {
                el: captureEl,
                type: captureEl.dataset.type,
                pressedMods: new Set(),
                pressedKeys: new Set(),
                peakMods: new Set(),
                peakActionKey: null,
            };
            display.classList.add('capturing');
            display.innerHTML = '<span class="hotkey-capturing-text">Press keys…</span>';
        });

        // Clear button
        if (clearBtn) {
            clearBtn.addEventListener('click', e => {
                e.stopPropagation();
                if (_activeCapture?.el === captureEl) { _activeCapture = null; }
                display.classList.remove('capturing');
                display.innerHTML = '<span class="hotkey-placeholder">Click to set</span>';
                const type = captureEl.dataset.type;
                if (type === 'ptt-combo') {
                    const pi = document.getElementById(captureEl.dataset.primaryField);
                    const si = document.getElementById(captureEl.dataset.secondaryField);
                    if (pi) pi.value = '';
                    if (si) si.value = '';
                } else if (type === 'ptt-single' || type === 'modifier-only') {
                    const inp = document.getElementById(captureEl.dataset.field);
                    if (inp) inp.value = '';
                } else if (type === 'modifier-key') {
                    const mi = document.getElementById(captureEl.dataset.modifierField);
                    const ki = document.getElementById(captureEl.dataset.keyField);
                    if (mi) mi.value = '';
                    if (ki) ki.value = '';
                }
                _updateChangedState(captureEl);
                _queueShortcutAutosave();
            });
        }
    });
}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------
function _applyShortcutSettings(s) {
    function _setMK(mid, kid, mVal, kVal) {
        const mi = document.getElementById(mid);
        const ki = document.getElementById(kid);
        if (mi) { mi.value = mVal || ''; _recordSaved(mid, mVal || ''); }
        if (ki) { ki.value = kVal || ''; _recordSaved(kid, kVal || ''); }
        const el = document.querySelector(`.hotkey-capture[data-modifier-field="${mid}"][data-key-field="${kid}"]`);
        if (el) {
            _setHotkeyDisplay(el, mVal || '', kVal || '');
            el.querySelector('.hotkey-display')?.classList.remove('changed');
        }
    }
    function _setMO(fid, val) {
        const inp = document.getElementById(fid);
        if (inp) { inp.value = val || ''; _recordSaved(fid, val || ''); }
        const el = document.querySelector(`.hotkey-capture[data-field="${fid}"]`);
        if (el) {
            _setHotkeyDisplay(el, val || '', null);
            el.querySelector('.hotkey-display')?.classList.remove('changed');
        }
    }

    // PTT — single modifier-only field
    _setMO('shortcuts_global_ptt_hotkey_combo', _valueOrDefault(s.global_ptt_hotkey_combo, 'option_command'));

    // Dictation
    _setMK('shortcuts_dictation_hotkey_modifier', 'shortcuts_dictation_hotkey_key',
           _valueOrDefault(s.dictation_hotkey_modifier, 'control_command'), _valueOrDefault(s.dictation_hotkey_key, ''));

    _rememberHiddenTicketDictationSettings(s);

    // Recording
    _setMK('shortcuts_recording_hotkey_modifier', 'shortcuts_recording_hotkey_key',
           _valueOrDefault(s.recording_hotkey_modifier, 'option_command'), _valueOrDefault(s.recording_hotkey_key, 's'));

    // Skin nav
    _setMK('shortcuts_skin_nav_hotkey_previous_modifier', 'shortcuts_skin_nav_hotkey_previous_key',
           _valueOrDefault(s.skin_nav_hotkey_previous_modifier, 'control_command'), _valueOrDefault(s.skin_nav_hotkey_previous_key, 'left_arrow'));
    _setMK('shortcuts_skin_nav_hotkey_next_modifier', 'shortcuts_skin_nav_hotkey_next_key',
           _valueOrDefault(s.skin_nav_hotkey_next_modifier, 'control_command'), _valueOrDefault(s.skin_nav_hotkey_next_key, 'right_arrow'));
    _setMO('shortcuts_skin_select_hotkey_modifier', _valueOrDefault(s.skin_select_hotkey_modifier, 'option_command'));

    // Web hotkeys
    _setMK('shortcuts_web_hotkey_chat_modifier',        'shortcuts_web_hotkey_chat_key',
           _valueOrDefault(s.web_hotkey_chat_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_chat_key, 'c'));
    _setMK('shortcuts_web_hotkey_projects_modifier',    'shortcuts_web_hotkey_projects_key',
           _valueOrDefault(s.web_hotkey_projects_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_projects_key, 'j'));
    _setMK('shortcuts_web_hotkey_actions_modifier',     'shortcuts_web_hotkey_actions_key',
           _valueOrDefault(s.web_hotkey_actions_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_actions_key, 'a'));
    _setMK('shortcuts_web_hotkey_snippets_modifier',    'shortcuts_web_hotkey_snippets_key',
           _valueOrDefault(s.web_hotkey_snippets_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_snippets_key, 'n'));
    _setMK('shortcuts_web_hotkey_workflows_modifier',   'shortcuts_web_hotkey_workflows_key',
           _valueOrDefault(s.web_hotkey_workflows_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_workflows_key, 'w'));
    _setMK('shortcuts_web_hotkey_automations_modifier', 'shortcuts_web_hotkey_automations_key',
           _valueOrDefault(s.web_hotkey_automations_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_automations_key, 'o'));
    _setMK('shortcuts_web_hotkey_ticket_board_modifier', 'shortcuts_web_hotkey_ticket_board_key',
           _valueOrDefault(s.web_hotkey_ticket_board_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_ticket_board_key, 't'));
    _setMK('shortcuts_web_hotkey_preferences_modifier', 'shortcuts_web_hotkey_preferences_key',
           _valueOrDefault(s.web_hotkey_preferences_modifier, 'control_shift'), _valueOrDefault(s.web_hotkey_preferences_key, 'p'));

    // Oracle size
    _setMK('shortcuts_oracle_size_hotkey_decrease_modifier', 'shortcuts_oracle_size_hotkey_decrease_key',
           _valueOrDefault(s.oracle_size_hotkey_decrease_modifier, 'control_command'), _valueOrDefault(s.oracle_size_hotkey_decrease_key, 'down_arrow'));
    _setMK('shortcuts_oracle_size_hotkey_increase_modifier', 'shortcuts_oracle_size_hotkey_increase_key',
           _valueOrDefault(s.oracle_size_hotkey_increase_modifier, 'control_command'), _valueOrDefault(s.oracle_size_hotkey_increase_key, 'up_arrow'));

    // Checkboxes
    const pttCb  = document.getElementById('shortcuts_global_ptt_hotkey_enabled');
    const recCb  = document.getElementById('shortcuts_recording_hotkey_enabled');
    const dictCb = document.getElementById('shortcuts_dictation_hotkey_enabled');
    const pttEnabled = s.global_ptt_hotkey_enabled !== undefined ? s.global_ptt_hotkey_enabled : true;
    const recEnabled = s.recording_hotkey_enabled !== undefined ? s.recording_hotkey_enabled : true;
    const dictEnabled = s.dictation_hotkey_enabled !== undefined ? s.dictation_hotkey_enabled : false;
    if (pttCb)  { pttCb.checked = pttEnabled; _recordSaved('shortcuts_global_ptt_hotkey_enabled', String(pttEnabled)); }
    if (recCb)  { recCb.checked = recEnabled; _recordSaved('shortcuts_recording_hotkey_enabled', String(recEnabled)); }
    if (dictCb) { dictCb.checked = dictEnabled; _recordSaved('shortcuts_dictation_hotkey_enabled', String(dictEnabled)); }
}

async function loadShortcutSettings() {
    try {
        const r = await fetch('/api/shortcuts');
        if (!r.ok) throw new Error('Failed to load shortcut settings');
        const s = await r.json();
        _applyShortcutSettings(s);

    } catch (err) {
        console.error('Error loading shortcut settings:', err);
        if (typeof showNotification === 'function')
            showNotification('Failed to load shortcut settings: ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------
function _v(id)  { return (document.getElementById(id) || {}).value || ''; }
function _cb(id, def) { const el = document.getElementById(id); return el ? el.checked : def; }

async function saveShortcutSettings(options) {
    options = options || {};
    try {
        const settings = {
            global_ptt_hotkey_enabled: _cb('shortcuts_global_ptt_hotkey_enabled', true),
            global_ptt_hotkey_combo:   _v('shortcuts_global_ptt_hotkey_combo'),

            recording_hotkey_enabled:  _cb('shortcuts_recording_hotkey_enabled', true),
            recording_hotkey_modifier: _v('shortcuts_recording_hotkey_modifier'),
            recording_hotkey_key:      _v('shortcuts_recording_hotkey_key'),

            dictation_hotkey_enabled:  _cb('shortcuts_dictation_hotkey_enabled', false),
            dictation_hotkey_modifier: _v('shortcuts_dictation_hotkey_modifier'),
            dictation_hotkey_key:      _v('shortcuts_dictation_hotkey_key'),
            ticket_dictation_hotkey_enabled: false,
            ticket_dictation_hotkey_modifier: _hiddenTicketDictationSettings.ticket_dictation_hotkey_modifier,
            ticket_dictation_hotkey_key: _hiddenTicketDictationSettings.ticket_dictation_hotkey_key,
            dictation_ticket_use_llm: _hiddenTicketDictationSettings.dictation_ticket_use_llm,
            dictation_ticket_model: _hiddenTicketDictationSettings.dictation_ticket_model,
            dictation_ticket_timeout: _hiddenTicketDictationSettings.dictation_ticket_timeout,
            dictation_ticket_prompt: _hiddenTicketDictationSettings.dictation_ticket_prompt,

            oracle_size_hotkey_decrease_modifier: _v('shortcuts_oracle_size_hotkey_decrease_modifier'),
            oracle_size_hotkey_decrease_key:      _v('shortcuts_oracle_size_hotkey_decrease_key'),
            oracle_size_hotkey_increase_modifier: _v('shortcuts_oracle_size_hotkey_increase_modifier'),
            oracle_size_hotkey_increase_key:      _v('shortcuts_oracle_size_hotkey_increase_key'),

            skin_nav_hotkey_previous_modifier: _v('shortcuts_skin_nav_hotkey_previous_modifier'),
            skin_nav_hotkey_previous_key:      _v('shortcuts_skin_nav_hotkey_previous_key'),
            skin_nav_hotkey_next_modifier:     _v('shortcuts_skin_nav_hotkey_next_modifier'),
            skin_nav_hotkey_next_key:          _v('shortcuts_skin_nav_hotkey_next_key'),
            skin_select_hotkey_modifier:       _v('shortcuts_skin_select_hotkey_modifier'),

            web_hotkey_chat_modifier:        _v('shortcuts_web_hotkey_chat_modifier'),
            web_hotkey_chat_key:             _v('shortcuts_web_hotkey_chat_key'),
            web_hotkey_projects_modifier:    _v('shortcuts_web_hotkey_projects_modifier'),
            web_hotkey_projects_key:         _v('shortcuts_web_hotkey_projects_key'),
            web_hotkey_actions_modifier:     _v('shortcuts_web_hotkey_actions_modifier'),
            web_hotkey_actions_key:          _v('shortcuts_web_hotkey_actions_key'),
            web_hotkey_snippets_modifier:    _v('shortcuts_web_hotkey_snippets_modifier'),
            web_hotkey_snippets_key:         _v('shortcuts_web_hotkey_snippets_key'),
            web_hotkey_workflows_modifier:   _v('shortcuts_web_hotkey_workflows_modifier'),
            web_hotkey_workflows_key:        _v('shortcuts_web_hotkey_workflows_key'),
            web_hotkey_automations_modifier: _v('shortcuts_web_hotkey_automations_modifier'),
            web_hotkey_automations_key:      _v('shortcuts_web_hotkey_automations_key'),
            web_hotkey_ticket_board_modifier: _v('shortcuts_web_hotkey_ticket_board_modifier'),
            web_hotkey_ticket_board_key:      _v('shortcuts_web_hotkey_ticket_board_key'),
            web_hotkey_preferences_modifier: _v('shortcuts_web_hotkey_preferences_modifier'),
            web_hotkey_preferences_key:      _v('shortcuts_web_hotkey_preferences_key'),
        };

        const collisionMessage = _validateShortcutCollisions([
            { name: 'Push-to-Talk', enabled: settings.global_ptt_hotkey_enabled, modifier: settings.global_ptt_hotkey_combo, key: '', enabledField: 'shortcuts_global_ptt_hotkey_enabled', modifierField: 'shortcuts_global_ptt_hotkey_combo' },
            { name: 'Dictation', enabled: settings.dictation_hotkey_enabled, modifier: settings.dictation_hotkey_modifier, key: settings.dictation_hotkey_key, enabledField: 'shortcuts_dictation_hotkey_enabled', modifierField: 'shortcuts_dictation_hotkey_modifier', keyField: 'shortcuts_dictation_hotkey_key' },
            { name: 'Recording', enabled: settings.recording_hotkey_enabled, modifier: settings.recording_hotkey_modifier, key: settings.recording_hotkey_key, enabledField: 'shortcuts_recording_hotkey_enabled', modifierField: 'shortcuts_recording_hotkey_modifier', keyField: 'shortcuts_recording_hotkey_key' },
            { name: 'Oracle size decrease', enabled: true, modifier: settings.oracle_size_hotkey_decrease_modifier, key: settings.oracle_size_hotkey_decrease_key, modifierField: 'shortcuts_oracle_size_hotkey_decrease_modifier', keyField: 'shortcuts_oracle_size_hotkey_decrease_key' },
            { name: 'Oracle size increase', enabled: true, modifier: settings.oracle_size_hotkey_increase_modifier, key: settings.oracle_size_hotkey_increase_key, modifierField: 'shortcuts_oracle_size_hotkey_increase_modifier', keyField: 'shortcuts_oracle_size_hotkey_increase_key' },
            { name: 'Previous skin', enabled: true, modifier: settings.skin_nav_hotkey_previous_modifier, key: settings.skin_nav_hotkey_previous_key, modifierField: 'shortcuts_skin_nav_hotkey_previous_modifier', keyField: 'shortcuts_skin_nav_hotkey_previous_key' },
            { name: 'Next skin', enabled: true, modifier: settings.skin_nav_hotkey_next_modifier, key: settings.skin_nav_hotkey_next_key, modifierField: 'shortcuts_skin_nav_hotkey_next_modifier', keyField: 'shortcuts_skin_nav_hotkey_next_key' },
            { name: 'Skin number modifier', enabled: true, modifier: settings.skin_select_hotkey_modifier, key: '', modifierField: 'shortcuts_skin_select_hotkey_modifier' },
            { name: 'Chat launcher', enabled: true, modifier: settings.web_hotkey_chat_modifier, key: settings.web_hotkey_chat_key, modifierField: 'shortcuts_web_hotkey_chat_modifier', keyField: 'shortcuts_web_hotkey_chat_key' },
            { name: 'Projects launcher', enabled: true, modifier: settings.web_hotkey_projects_modifier, key: settings.web_hotkey_projects_key, modifierField: 'shortcuts_web_hotkey_projects_modifier', keyField: 'shortcuts_web_hotkey_projects_key' },
            { name: 'Actions launcher', enabled: true, modifier: settings.web_hotkey_actions_modifier, key: settings.web_hotkey_actions_key, modifierField: 'shortcuts_web_hotkey_actions_modifier', keyField: 'shortcuts_web_hotkey_actions_key' },
            { name: 'Snippets launcher', enabled: true, modifier: settings.web_hotkey_snippets_modifier, key: settings.web_hotkey_snippets_key, modifierField: 'shortcuts_web_hotkey_snippets_modifier', keyField: 'shortcuts_web_hotkey_snippets_key' },
            { name: 'Workflows launcher', enabled: true, modifier: settings.web_hotkey_workflows_modifier, key: settings.web_hotkey_workflows_key, modifierField: 'shortcuts_web_hotkey_workflows_modifier', keyField: 'shortcuts_web_hotkey_workflows_key' },
            { name: 'Automations launcher', enabled: true, modifier: settings.web_hotkey_automations_modifier, key: settings.web_hotkey_automations_key, modifierField: 'shortcuts_web_hotkey_automations_modifier', keyField: 'shortcuts_web_hotkey_automations_key' },
            { name: 'Ticket board launcher', enabled: true, modifier: settings.web_hotkey_ticket_board_modifier, key: settings.web_hotkey_ticket_board_key, modifierField: 'shortcuts_web_hotkey_ticket_board_modifier', keyField: 'shortcuts_web_hotkey_ticket_board_key' },
            { name: 'Preferences launcher', enabled: true, modifier: settings.web_hotkey_preferences_modifier, key: settings.web_hotkey_preferences_key, modifierField: 'shortcuts_web_hotkey_preferences_modifier', keyField: 'shortcuts_web_hotkey_preferences_key' },
        ]);
        if (collisionMessage) {
            if (typeof showNotification === 'function')
                showNotification(collisionMessage, 'error');
            return;
        }

        const resp = await fetch('/api/shortcuts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to save shortcut settings');
        }

        const payload = await resp.json();
        if (payload && payload.settings) {
            _applyShortcutSettings(payload.settings);
        } else {
            await loadShortcutSettings();
        }
        if (!options.silentSuccess && typeof showNotification === 'function')
            showNotification('Shortcut settings saved', 'success');
    } catch (err) {
        console.error('Error saving shortcut settings:', err);
        if (typeof showNotification === 'function')
            showNotification('Failed to save shortcut settings: ' + err.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function _initShortcuts() {
    if (!document.getElementById('tab-shortcuts')) return;
    initHotkeyCapture();
    ['shortcuts_global_ptt_hotkey_enabled', 'shortcuts_recording_hotkey_enabled', 'shortcuts_dictation_hotkey_enabled'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.autosaveBound) {
            el.addEventListener('change', _queueShortcutAutosave);
            el.dataset.autosaveBound = 'true';
        }
    });
    loadShortcutSettings();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initShortcuts);
} else {
    _initShortcuts();
}

window.loadShortcutSettings  = loadShortcutSettings;
window.saveShortcutSettings  = saveShortcutSettings;
window.initHotkeyCapture     = initHotkeyCapture;
