// Shared pieces of the ticket and proposal modals: readable JSON notes and the Files drop zone.

// ---- Notes: JSON <-> readable labeled fields --------------------------------

// Returns { kind: 'json', value } for a JSON object/array note, otherwise { kind: 'text' }.
export function parseNoteJson(text) {
    const trimmed = String(text ?? '').trim();
    if (!trimmed || !'{['.includes(trimmed[0])) return { kind: 'text' };
    try {
        const value = JSON.parse(trimmed);
        return value && typeof value === 'object' ? { kind: 'json', value } : { kind: 'text' };
    } catch (_) {
        return { kind: 'text' };
    }
}

function leafType(value) {
    if (value === null) return 'null';
    if (Array.isArray(value)) return value.length ? 'array' : 'empty-array';
    if (typeof value === 'object') return Object.keys(value).length ? 'object' : 'empty-object';
    return typeof value; // string | number | boolean
}

export function leafText(value) {
    if (value === null) return '';
    if (typeof value === 'string') return value;
    return String(value);
}

export function humanizeKey(key) {
    const text = String(key).replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim();
    return text ? text[0].toUpperCase() + text.slice(1) : String(key);
}

// Flattens a JSON value into rows in original key order. Containers become
// group rows; scalars become editable leaf rows keyed by a stable path key.
export function flattenNote(value, path = [], rows = []) {
    const type = leafType(value);
    if (type === 'object' || type === 'array') {
        if (path.length) rows.push({ kind: 'group', path, depth: path.length - 1, label: rowLabel(path), type });
        const entries = type === 'array' ? value.map((item, index) => [index, item]) : Object.entries(value);
        entries.forEach(([key, item]) => flattenNote(item, [...path, key], rows));
        return rows;
    }
    rows.push({
        kind: 'leaf', path, key: JSON.stringify(path), depth: Math.max(0, path.length - 1),
        label: path.length ? rowLabel(path) : 'Value', type, text: leafText(value),
        editable: !type.startsWith('empty')
    });
    return rows;
}

function rowLabel(path) {
    const last = path[path.length - 1];
    return typeof last === 'number' ? `Item ${last + 1}` : humanizeKey(last);
}

function coerce(original, text) {
    if (typeof original === 'string') return text;
    if (typeof original === 'number') {
        const trimmed = text.trim();
        return trimmed !== '' && Number.isFinite(Number(trimmed)) ? Number(trimmed) : text;
    }
    if (typeof original === 'boolean') {
        const lower = text.trim().toLowerCase();
        return lower === 'true' ? true : lower === 'false' ? false : text;
    }
    if (original === null) return text.trim() === '' || text.trim() === 'null' ? null : text;
    return original;
}

// Applies edited leaf texts (Map pathKey -> text) to a deep copy, keeping key
// order, unknown keys and each leaf's original type where the text allows it.
export function applyNoteEdits(value, edits, path = []) {
    const type = leafType(value);
    if (type === 'array') return value.map((item, index) => applyNoteEdits(item, edits, [...path, index]));
    if (type === 'object') {
        const out = {};
        Object.entries(value).forEach(([key, item]) => { out[key] = applyNoteEdits(item, edits, [...path, key]); });
        return out;
    }
    const key = JSON.stringify(path);
    if (!edits || !edits.has(key)) return value;
    const text = edits.get(key);
    return text === leafText(value) ? value : coerce(value, text);
}

function detectIndent(text) {
    const match = String(text).match(/\n([ \t]+)\S/);
    return match ? match[1] : (String(text).includes('\n') ? 2 : 0);
}

// Serializes edited JSON. Returns the original text untouched when nothing changed.
export function serializeNote(originalText, value, edits) {
    const parsed = parseNoteJson(originalText);
    if (parsed.kind !== 'json') return String(originalText ?? '');
    const next = applyNoteEdits(value, edits);
    if (JSON.stringify(next) === JSON.stringify(parsed.value)) return String(originalText ?? '');
    return JSON.stringify(next, null, detectIndent(originalText));
}

// Only send a note when it differs from what was loaded.
export function noteChanged(originalText, nextText) {
    return String(nextText ?? '').trim() !== String(originalText ?? '').trim();
}

export function noteFieldsHtml(rows, escapeHtml, { readOnly = false } = {}) {
    return rows.map((row) => {
        const indent = ` style="--note-depth:${row.depth}"`;
        if (row.kind === 'group') {
            return `<div class="ticket-note-group"${indent}>${escapeHtml(row.label)}</div>`;
        }
        if (!row.editable) {
            return `<div class="ticket-note-field"${indent}><span>${escapeHtml(row.label)}</span><em>${row.type === 'empty-array' ? 'No items' : 'Empty'}</em></div>`;
        }
        const multiline = row.text.length > 60 || row.text.includes('\n');
        const control = multiline
            ? `<textarea data-note-path="${escapeHtml(row.key)}" rows="${Math.min(8, Math.max(2, row.text.split('\n').length))}"${readOnly ? ' readonly' : ''}>${escapeHtml(row.text)}</textarea>`
            : `<input type="text" data-note-path="${escapeHtml(row.key)}" value="${escapeHtml(row.text)}"${readOnly ? ' readonly' : ''}>`;
        return `<label class="ticket-note-field"${indent}><span>${escapeHtml(row.label)}</span>${control}</label>`;
    }).join('');
}

export function collectNoteEdits(root) {
    const edits = new Map();
    root?.querySelectorAll('[data-note-path]').forEach((input) => edits.set(input.dataset.notePath, input.value));
    return edits;
}

// ---- Files tab: drop zone ---------------------------------------------------

export function fileDropZoneHtml(id, { disabled = false, message = 'Drop files here' } = {}) {
    return `<div class="ticket-file-drop${disabled ? ' disabled' : ''}" id="${id}" data-file-drop>`
        + `<span>${message}</span>`
        + (disabled ? '' : `<button type="button" class="secondary-button" data-file-pick>Choose files</button>`)
        + '</div>';
}

// Wires drag-and-drop (with hover state) and an optional picker input to onFiles(files[]).
export function bindFileDropZone(zone, onFiles, input) {
    if (!zone || zone.dataset.dropBound === 'true') return;
    zone.dataset.dropBound = 'true';
    let depth = 0;
    const active = () => !zone.classList.contains('disabled');
    const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files');
    zone.addEventListener('dragenter', (event) => {
        if (!active() || !hasFiles(event)) return;
        event.preventDefault();
        depth += 1;
        zone.classList.add('is-dragover');
    });
    zone.addEventListener('dragover', (event) => {
        if (!active() || !hasFiles(event)) return;
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });
    zone.addEventListener('dragleave', () => {
        depth = Math.max(0, depth - 1);
        if (!depth) zone.classList.remove('is-dragover');
    });
    zone.addEventListener('drop', (event) => {
        event.preventDefault();
        depth = 0;
        zone.classList.remove('is-dragover');
        if (!active()) return;
        const files = Array.from(event.dataTransfer?.files || []);
        if (files.length) onFiles(files);
    });
    const pick = zone.querySelector('[data-file-pick]');
    if (pick && input) pick.addEventListener('click', () => input.click());
}

// Toast after accepting a proposal: lists files that could not be fetched.
export function proposalThreadToast(result) {
    const failed = (result?.failed_attachments || []).map((row) => row?.name || 'file');
    if (!failed.length) return 'Thread created.';
    return `Thread created. ${failed.length} file${failed.length === 1 ? '' : 's'} could not be fetched: ${failed.join(', ')}`;
}
