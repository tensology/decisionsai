// Mermaid erDiagram <-> editor model. Mermaid stays the stored/synced source; the editor's
// layout (positions, zoom) and extras live in one trailing comment line, which Mermaid ignores:
//   %% decisions-erd: {"v":1,"view":{...},"tables":{"NAME":{"x":0,"y":0,"interfaces":[],"fields":{"f":{"nullable":true,"default":"x"}}}}}
// Sidecar keys this editor does not use (e.g. N5 `db` link tags, top level and per table) are kept verbatim.
// Lines the parser cannot represent are kept verbatim (model.preserved / table.raw) and re-emitted.

import { mermaidType } from './field-types.js?v=20261102-verify';

export const SIDECAR_PREFIX = '%% decisions-erd:';
const LEFT = ['|o', '||', '}o', '}|'];
const RIGHT = ['o|', '||', 'o{', '|{'];
const NAME = String.raw`(?:"[^"]+"|[A-Za-z_][\w-]*)`;
const REL_RE = new RegExp(String.raw`^(${NAME})\s*(\|o|\|\||\}o|\}\|)(--|\.\.)(o\||\|\||o\{|\|\{)\s*(${NAME})\s*:\s*(.*)$`);
const BLOCK_RE = new RegExp(String.raw`^(${NAME})(?:\s*\[\s*"([^"]*)"\s*\])?\s*\{\s*(\})?\s*$`);
const ATTR_RE = /^([A-Za-z_][\w\-()[\],.*]*?)\s+([A-Za-z_*][\w\-*]*)((?:\s+(?:PK|FK|UK)(?:\s*,\s*(?:PK|FK|UK))*)?)(?:\s+"([^"]*)")?\s*$/;

export const isErdMermaid = (text) => /^\s*(?:%%[^\n]*\n\s*)*erDiagram\b/.test(String(text || ''));
const unquote = (s) => (s.length >= 2 && s.startsWith('"') && s.endsWith('"') ? s.slice(1, -1) : s);
// One emitted value can never break out of its line: newlines / line separators become spaces.
const oneLine = (s) => String(s ?? '').replace(/[\r\n\u2028\u2029]+/g, ' ');
const quoteName = (s) => { const v = oneLine(s); return /^[A-Za-z_][\w-]*$/.test(v) ? v : `"${v.replace(/"/g, "'")}"`; };
const quoteText = (s) => oneLine(s).replace(/"/g, "'");
const quoteLabel = (s) => `"${quoteText(s)}"`;
// Split a trailing `%% comment` that sits outside double quotes.
function splitComment(line) {
    let quoted = false;
    for (let i = 0; i < line.length - 1; i++) {
        if (line[i] === '"') quoted = !quoted;
        else if (!quoted && line[i] === '%' && line[i + 1] === '%' && (i === 0 || /\s/.test(line[i - 1]))) return [line.slice(0, i).trimEnd(), line.slice(i).trim()];
    }
    return [line, ''];
}

// preserved[i] is a verbatim line; preservedAt[i] says where it sat: 'head' (before erDiagram),
// 'top' (before any statement), 'T:<table>' (after that table) or 'R:<from>|<to>|<label>' (after that relation).
export function emptyModel() { return { tables: [], relations: [], preserved: [], preservedAt: [], view: null }; }

export function parseErd(text) {
    const model = emptyModel();
    const warnings = [];
    let sidecar = null;
    const byName = new Map();
    const table = (name) => {
        if (!byName.has(name)) {
            const t = { name, alias: '', fields: [], raw: [], x: null, y: null, interfaces: [] };
            byName.set(name, t);
            model.tables.push(t);
        }
        return byName.get(name);
    };
    let seenHeader = false;
    let block = null;
    let anchor = 'top';
    const keep = (line, at = anchor) => { model.preserved.push(line); model.preservedAt.push(at); };
    String(text || '').split(/\r?\n/).forEach((original, index) => {
        let line = original.trim();
        if (line.startsWith(SIDECAR_PREFIX)) {
            try { sidecar = JSON.parse(line.slice(SIDECAR_PREFIX.length)); } catch { warnings.push(`Line ${index + 1}: unreadable editor layout ignored.`); }
            return;
        }
        if (block) {
            if (line === '}') { block = null; return; }
            if (!line) return;
            if (REL_RE.test(splitComment(line)[0]) || BLOCK_RE.test(line)) {
                // An unclosed `{`: close it here instead of swallowing the rest of the diagram.
                warnings.push(`Line ${index + 1}: ${block.name} block was not closed; closed it before this line.`);
                block = null;
            } else {
            const m = ATTR_RE.exec(line);
            if (m) {
                const keys = (m[3] || '').split(/[\s,]+/).filter(Boolean);
                block.fields.push({ type: m[1], name: m[2], keys, comment: m[4] ?? '' });
            } else {
                block.raw.push(line);
                warnings.push(`Line ${index + 1} in ${block.name}: kept as raw text ("${line}").`);
            }
            return;
            }
        }
        if (!line) return;
        if (!seenHeader && /^erDiagram\b/.test(line)) { seenHeader = true; return; }
        if (!seenHeader && line.startsWith('%%')) { keep(line, 'head'); return; }
        const [body, trailing] = line.startsWith('%%') ? [line, ''] : splitComment(line);
        const rel = REL_RE.exec(body);
        if (rel) {
            const from = table(unquote(rel[1])).name;
            const to = table(unquote(rel[5])).name;
            const r = { from, to, left: rel[2], right: rel[4], identifying: rel[3] === '--', label: unquote(rel[6].trim()) };
            model.relations.push(r);
            anchor = relKey(r);
            if (trailing) keep(trailing);
            return;
        }
        if (trailing && BLOCK_RE.test(body)) line = body;
        const blk = BLOCK_RE.exec(line);
        if (blk) {
            const t = table(unquote(blk[1]));
            if (blk[2] !== undefined) t.alias = blk[2];
            if (!blk[3]) block = t;
            anchor = `T:${t.name}`;
            if (trailing) keep(trailing);
            return;
        }
        keep(line);
        if (!line.startsWith('%%')) warnings.push(`Line ${index + 1}: kept as raw text ("${line}").`);
    });
    if (block) warnings.push(`${block.name}: block was not closed.`);
    applySidecar(model, sidecar);
    return { model, warnings, hasLayout: Boolean(sidecar) };
}

const KNOWN_TOP = new Set(['v', 'view', 'tables']);
const KNOWN_TABLE = new Set(['x', 'y', 'interfaces', 'fields']);
const extras = (obj, known) => {
    const out = {};
    for (const [k, v] of Object.entries(obj || {})) if (!known.has(k)) out[k] = v;
    return Object.keys(out).length ? out : null;
};

function applySidecar(model, sidecar) {
    if (!sidecar || typeof sidecar !== 'object') return;
    const top = extras(sidecar, KNOWN_TOP);
    if (top) model.sidecarExtra = top;
    model.view = sidecar.view && typeof sidecar.view === 'object' ? { zoom: +sidecar.view.zoom || 1, x: +sidecar.view.x || 0, y: +sidecar.view.y || 0 } : null;
    for (const t of model.tables) {
        const meta = sidecar.tables?.[t.name];
        if (!meta) continue;
        if (Number.isFinite(meta.x)) t.x = meta.x;
        if (Number.isFinite(meta.y)) t.y = meta.y;
        if (Array.isArray(meta.interfaces)) t.interfaces = meta.interfaces.map(String);
        const more = extras(meta, KNOWN_TABLE);
        if (more) t.sidecarExtra = more;
        for (const f of t.fields) {
            const extra = meta.fields?.[f.name];
            if (extra && 'nullable' in extra) f.nullable = Boolean(extra.nullable);
            if (extra && 'default' in extra) f.default = String(extra.default);
            // A type Mermaid cannot carry (e.g. decimal(12,2)) is emitted as its base; the exact text lives here.
            if (extra && typeof extra.type === 'string' && mermaidType(extra.type) === f.type) f.type = extra.type;
        }
    }
}

const relKey = (r) => `R:${r.from}|${r.to}|${r.label}`;

export function sidecarOf(model) {
    const tables = {};
    for (const t of model.tables) {
        const fields = {};
        for (const f of t.fields) {
            const extra = {};
            if (f.nullable !== undefined) extra.nullable = f.nullable;
            if (f.default !== undefined && f.default !== '') extra.default = f.default;
            if (f.type && mermaidType(f.type) !== f.type) extra.type = f.type;
            if (Object.keys(extra).length) fields[f.name] = extra;
        }
        tables[t.name] = { ...(t.sidecarExtra || {}), x: Math.round(t.x ?? 0), y: Math.round(t.y ?? 0), interfaces: [...(t.interfaces || [])], ...(Object.keys(fields).length ? { fields } : {}) };
    }
    return { ...(model.sidecarExtra || {}), v: 1, ...(model.view ? { view: model.view } : {}), tables };
}

// Fields added on a linked table since the last save. A rename or delete in the same save is skipped:
// that would look like a new column and the old column is never dropped.
// ponytail: one save that both adds a field and renames another field on the same table does not create the column.
export function addedDbColumns(beforeText, afterText) {
    const before = parseErd(beforeText).model;
    const after = parseErd(afterText).model;
    const out = [];
    for (const table of after.tables) {
        const db = table.sidecarExtra?.db;
        if (!db || !db.link || !db.schema || !db.table) continue;
        const prev = before.tables.find((item) => item.name === table.name);
        if (!prev) continue;
        const removed = prev.fields.some((field) => !table.fields.some((next) => next.name === field.name));
        if (removed) continue;
        const prevNames = new Set(prev.fields.map((field) => field.name));
        for (const field of table.fields) {
            if (!prevNames.has(field.name)) {
                out.push({ link_id: db.link, schema: db.schema, table: db.table, column: field.name, field_type: field.type || 'string' });
            }
        }
    }
    return out;
}

export function emitErd(model, { sidecar = true } = {}) {
    // Preserved lines go back where they were (anchored to the statement before them); a line whose
    // anchor was deleted falls back to the end, in original order.
    const at = model.preservedAt || [];
    const pending = model.preserved.map((line, i) => ({ line, at: at[i] ?? 'end' }));
    const take = (key) => { const lines = []; for (const p of pending) if (!p.done && p.at === key) { p.done = true; lines.push(`    ${p.line}`); } return lines; };
    const out = [...take('head').map((l) => l.trim()), 'erDiagram', ...take('top')];
    for (const t of model.tables) {
        const head = `${quoteName(t.name)}${t.alias ? `["${quoteText(t.alias)}"]` : ''}`;
        if (!t.fields.length && !t.raw.length) out.push(`    ${head} {`, '    }');
        else {
            out.push(`    ${head} {`);
            for (const f of t.fields) {
                const keys = (f.keys || []).length ? ` ${f.keys.join(', ')}` : '';
                const comment = f.comment ? ` "${quoteText(f.comment)}"` : '';
                out.push(`        ${mermaidType(oneLine(f.type))} ${oneLine(f.name).replace(/\s+/g, '_')}${keys}${comment}`);
            }
            t.raw.forEach((l) => out.push(`        ${oneLine(l)}`));
            out.push('    }');
        }
        out.push(...take(`T:${t.name}`));
    }
    for (const r of model.relations) {
        out.push(`    ${quoteName(r.from)} ${r.left}${r.identifying === false ? '..' : '--'}${r.right} ${quoteName(r.to)} : ${quoteLabel(r.label)}`);
        out.push(...take(relKey(r)));
    }
    pending.forEach((p) => { if (!p.done) out.push(`    ${oneLine(p.line)}`); });
    if (sidecar) out.push(`${SIDECAR_PREFIX} ${JSON.stringify(sidecarOf(model))}`);
    return `${out.join('\n')}\n`;
}

export const CARDINALITY = {
    left: { '|o': 'Zero or one', '||': 'Exactly one', '}o': 'Zero or many', '}|': 'One or many' },
    right: { 'o|': 'Zero or one', '||': 'Exactly one', 'o{': 'Zero or many', '|{': 'One or many' },
};
export const RELATION_PRESETS = [
    ['One to one', '||', '||'], ['One to many', '||', 'o{'], ['One to one-or-many', '||', '|{'],
    ['Zero-or-one to one', '|o', '||'], ['Zero-or-one to many', '|o', 'o{'], ['Many to many', '}o', 'o{'],
];
export function endKind(token) {
    const t = String(token);
    return { min: t.includes('o') ? 0 : 1, many: t.includes('{') || t.includes('}') };
}
export { LEFT, RIGHT };
