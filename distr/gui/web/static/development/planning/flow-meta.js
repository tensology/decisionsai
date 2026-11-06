// Interfaces node metadata ("sidecar"), one per item: the LAST non-empty line of the item.
//   wire:    # decisions-flow: {"nodes":{"<screen id>":{"image":"17","links":{"saved":"on save"},"note":"…","shape":"diamond"}},"v":1}
//   mermaid: %% decisions-flow: {…}          (Mermaid shapes are native: the sidecar holds image / note / links only)
// Both are comment lines: the wire parser, renderer, server validator and Mermaid ignore them. This module only
// ever rewrites that last line (and, when metadata is first attached to a wire screen without id=, that screen's
// `screen` line); the "#|" page-description lines are never touched. Pure functions, no DOM (node-testable).
export const SHAPES = ['page', 'rectangle', 'square', 'circle', 'oval', 'diamond'];
export const NOTE_LIMIT = 500;
export const LINK_LABEL_LIMIT = 120;
export const META_LIMIT_BYTES = 16 * 1024;
const PREFIX = { wire: '# decisions-flow:', mermaid: '%% decisions-flow:' };
const ID = /^[A-Za-z][\w-]{0,63}$/;
const FIELDS = ['shape', 'image', 'note', 'links'];

export const sidecarPrefix = (format) => PREFIX[format] || null;
const isSidecar = (line, format) => Boolean(PREFIX[format]) && line.trim().startsWith(PREFIX[format]);

// Stable, compact JSON: keys sorted at every level (diffs stay small, writes are deterministic).
export function stableJson(value) {
    if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
    if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${stableJson(value[k])}`).join(',')}}`;
    return JSON.stringify(value);
}

// -> { nodes, body, error }. body = the content without the sidecar line and its trailing blank lines.
// A malformed sidecar is ignored (defaults render) and reported in `error`; it is never thrown.
export function readSidecar(content, format) {
    const text = String(content ?? '');
    const empty = { nodes: {}, body: text, error: '' };
    if (!PREFIX[format] || !text.includes(PREFIX[format])) return empty;
    const lines = text.split('\n');
    const hits = lines.map((line, i) => (isSidecar(line, format) ? i : -1)).filter((i) => i >= 0);
    if (!hits.length) return empty;
    let last = lines.length - 1;
    while (last >= 0 && !lines[last].trim()) last -= 1;
    if (hits.length > 1 || hits[0] !== last) return { ...empty, error: hits.length > 1 ? 'Only one flow metadata line is allowed.' : 'The flow metadata line must be the last line of the item.' };
    const body = trimEnd(lines.slice(0, last)).join('\n');
    try {
        const data = JSON.parse(lines[last].trim().slice(PREFIX[format].length).trim());
        if (!data || typeof data !== 'object' || data.v !== 1 || (data.nodes && typeof data.nodes !== 'object')) throw new Error('bad');
        return { nodes: cleanNodes(data.nodes || {}, format), body: body ? `${body}\n` : '', error: '' };
    } catch { return { ...empty, error: 'Flow metadata is not valid JSON.' }; }
}

function trimEnd(lines) { const out = lines.slice(); while (out.length && !out[out.length - 1].trim()) out.pop(); return out; }

function cleanNodes(nodes, format) {
    const out = {};
    for (const [key, meta] of Object.entries(nodes || {})) {
        if (!ID.test(key) || !meta || typeof meta !== 'object') continue;
        const node = cleanNode(meta, format);
        if (Object.keys(node).length) out[key] = node;
    }
    return out;
}

function cleanNode(meta, format) {
    const node = {};
    if (format === 'wire' && SHAPES.includes(meta.shape) && meta.shape !== 'page') node.shape = meta.shape;
    if (meta.image != null && /^[1-9]\d{0,15}$/.test(String(meta.image))) node.image = String(meta.image);
    if (typeof meta.note === 'string' && meta.note.trim()) node.note = meta.note.slice(0, NOTE_LIMIT);
    if (meta.links && typeof meta.links === 'object') {
        const links = {};
        for (const [target, label] of Object.entries(meta.links)) if (ID.test(target)) links[target] = String(label ?? '').slice(0, LINK_LABEL_LIMIT);
        if (Object.keys(links).length) node.links = links;
    }
    return node;
}

// Replace (or remove, when `nodes` is empty) the sidecar line. Keys sorted; always the last non-empty line.
export function writeSidecar(content, format, nodes) {
    if (!PREFIX[format]) throw new Error('Flow metadata is only supported for page outlines and Mermaid flows.');
    const lines = trimEnd(String(content ?? '').split('\n').filter((line) => !isSidecar(line, format)));
    const clean = cleanNodes(nodes, format);
    if (!Object.keys(clean).length) return lines.length ? `${lines.join('\n')}\n` : '';
    const json = stableJson({ v: 1, nodes: clean });
    if (new TextEncoder().encode(json).length > META_LIMIT_BYTES) throw new Error('Flow metadata is larger than 16 KB.');
    return `${lines.join('\n')}\n${PREFIX[format]} ${json}\n`;
}

// Node keys present in the source: wire screen id= attributes; Mermaid node ids.
export function nodeIds(content, format) {
    const lines = String(content ?? '').split('\n').filter((line) => !isSidecar(line, format));
    const ids = [];
    const add = (id) => { if (ID.test(id) && !ids.includes(id)) ids.push(id); };
    if (format === 'wire') {
        for (const line of lines) { const m = /^\s*screen\b(.*)$/.exec(line); if (m) { const id = /(?:^|\s)id=([A-Za-z][\w-]{0,63})(?=\s|$)/.exec(m[1]); if (id) add(id[1]); } }
    } else if (format === 'mermaid') {
        const KEYWORDS = new Set(['flowchart', 'graph', 'subgraph', 'end', 'style', 'classDef', 'class', 'click', 'linkStyle', 'direction', 'TD', 'TB', 'BT', 'LR', 'RL']);
        for (const line of lines) {
            const t = line.trim();
            if (!t || t.startsWith('%%') || /^(style|classDef|class|click|linkStyle|direction)\b/.test(t)) continue;
            const stripped = t.replace(/\|[^|]*\|/g, ' ').replace(/(\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|(?<=\w)>[^\]]*\]|"[^"]*")/g, ' ');
            for (const token of stripped.match(/[A-Za-z][\w-]{0,63}/g) || []) if (!KEYWORDS.has(token) && !/^(?:-|o|x)$/.test(token)) add(token);
        }
    }
    return ids;
}

export function slugId(label, taken = []) {
    const base = (String(label || 'page').normalize('NFKD').replace(/[^\w\s-]/g, '').trim().toLowerCase().replace(/[\s_]+/g, '-').replace(/-+/g, '-').replace(/^[^a-z]+/, '') || 'page').slice(0, 56);
    const used = new Set(taken);
    if (!used.has(base)) return base;
    for (let n = 2; ; n += 1) if (!used.has(`${base}-${n}`)) return `${base}-${n}`;
}

// Give the screen on 1-based source `line` an id= (slug of its label, unique) if it has none.
export function ensureScreenId(content, line) {
    const lines = String(content ?? '').split('\n');
    const at = line - 1;
    const m = /^(\s*screen\b)(.*)$/.exec(lines[at] || '');
    if (!m) throw new Error('This node is not a page in the outline.');
    const existing = /(?:^|\s)id=([A-Za-z][\w-]{0,63})(?=\s|$)/.exec(m[2]);
    if (existing) return { content: lines.join('\n'), id: existing[1], added: false };
    const label = /"([^"]*)"/.exec(m[2])?.[1] || /^\s*([^\s=]+)(?=\s|$)/.exec(m[2])?.[1] || 'page';
    const id = slugId(label, nodeIds(content, 'wire'));
    const quoted = /^(\s*"[^"]*")(.*)$/.exec(m[2]);
    lines[at] = quoted ? `${m[1]}${quoted[1]} id=${id}${quoted[2]}` : `${m[1]}${m[2].replace(/\s+$/, '')} id=${id}`;
    return { content: lines.join('\n'), id, added: true };
}

// The one writer used by the UI: apply `patch` to node `key` (null fields remove), prune orphan keys,
// rewrite the sidecar. For wire screens without id=, pass { screenLine } and the id is added in the same text.
export function updateNodeMeta(content, format, { key = null, screenLine = null, patch = {} } = {}) {
    let text = String(content ?? '');
    let id = key;
    if (!id && format === 'wire' && screenLine) ({ content: text, id } = ensureScreenId(text, screenLine));
    if (!id || !ID.test(id)) throw new Error('This node needs an id before it can hold metadata.');
    const { nodes } = readSidecar(text, format);
    const node = { ...(nodes[id] || {}) };
    for (const field of FIELDS) if (Object.hasOwn(patch, field)) { if (patch[field] == null || patch[field] === '') delete node[field]; else node[field] = patch[field]; }
    if (format === 'mermaid' && Object.hasOwn(patch, 'shape')) throw new Error('Node shapes for Mermaid flows are set in the diagram itself, not in flow metadata.');
    if (node.note && node.note.length > NOTE_LIMIT) throw new Error(`A node note can be at most ${NOTE_LIMIT} characters.`);
    if (node.shape && !SHAPES.includes(node.shape)) throw new Error(`Unknown node shape "${node.shape}". Use ${SHAPES.slice(0, -1).join(', ')} or diamond.`);
    const present = new Set(nodeIds(text, format));
    const next = {};
    for (const [k, v] of Object.entries({ ...nodes, [id]: node })) if (present.has(k)) next[k] = v;   // orphan keys pruned
    const pruned = Object.keys(nodes).filter((k) => !present.has(k));
    return { content: writeSidecar(text, format, next), id, pruned };
}

// N6 "rename id" (wire only): rewrite the screen's id= and the sidecar key together, plus any sidecar link
// that targets the old id, in one text (one revision). Mermaid ids live in the diagram itself: not offered.
export function renameNodeId(content, format, from, to) {
    if (format !== 'wire') throw new Error('Node ids can only be renamed on page outlines.');
    if (!ID.test(String(to || ''))) throw new Error('Use a letter first, then letters, digits, - or _ (max 64).');
    if (from === to) return { content: String(content ?? ''), id: to };
    const ids = nodeIds(content, 'wire');
    if (!ids.includes(from)) throw new Error(`No page has id ${from}.`);
    if (ids.includes(to)) throw new Error(`Another page already uses id ${to}.`);
    const re = new RegExp(`(^\\s*screen\\b.*?(?:^|\\s)id=)${from.replace(/[-]/g, '\\-')}(?=\\s|$)`);
    const lines = String(content ?? '').split('\n').map((line) => (/^\s*screen\b/.test(line) && re.test(line) ? line.replace(re, `$1${to}`) : line));
    const { nodes } = readSidecar(content, 'wire');
    const next = {};
    for (const [key, meta] of Object.entries(nodes)) {
        const copy = { ...meta };
        if (copy.links && Object.hasOwn(copy.links, from)) { const links = {}; for (const [t, l] of Object.entries(copy.links)) links[t === from ? to : t] = l; copy.links = links; }
        next[key === from ? to : key] = copy;
    }
    const text = lines.join('\n');
    return { content: Object.keys(nodes).length ? writeSidecar(text, 'wire', next) : text, id: to };
}
