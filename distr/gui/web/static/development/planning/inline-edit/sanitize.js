// Allowlist sanitiser for the inline HTML kept inside plan markdown (pure, no DOM).
// Tags: markdown inline output (strong b em i s del strike code br) + u, mark, span(style colour only), a(safe href).
const SIMPLE = new Set(['strong', 'b', 'em', 'i', 's', 'del', 'strike', 'code', 'u', 'mark']);
const VOID = new Set(['br']);
const DROP_CONTENT = new Set(['script', 'style', 'iframe', 'object', 'embed', 'template', 'noscript', 'textarea', 'title', 'svg', 'math']);
const NAMED_COLOURS = new Set(['black', 'white', 'gray', 'grey', 'silver', 'red', 'orange', 'yellow', 'green', 'teal', 'blue', 'navy', 'purple', 'pink', 'brown', 'gold', 'crimson', 'tomato', 'coral', 'orchid', 'violet', 'cyan', 'magenta', 'lime', 'olive', 'maroon', 'transparent', 'inherit']);
const NUM = String.raw`[+-]?(?:\d+\.?\d*|\.\d+)(?:%|deg|turn|rad)?`;
const FUNC_RE = new RegExp(String.raw`^(rgba?|hsla?)\(\s*${NUM}(?:\s*[,\s/]\s*${NUM}){2,3}\s*\)$`, 'i');

export const escapeText = (s) => String(s ?? '').replace(/&(?!(?:[a-z]+|#\d+|#x[0-9a-f]+);)/gi, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
export const escapeAttr = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function decodeEntities(s) {
    return String(s ?? '')
        .replace(/&#x([0-9a-f]+);?/gi, (_, h) => String.fromCodePoint(parseInt(h, 16) || 32))
        .replace(/&#(\d+);?/g, (_, d) => String.fromCodePoint(Number(d) || 32))
        .replace(/&colon;/gi, ':').replace(/&tab;/gi, '\t').replace(/&newline;/gi, '\n')
        .replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

export function safeColour(value) {
    const v = decodeEntities(value).trim().toLowerCase();
    if (/^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/.test(v)) return v;
    if (NAMED_COLOURS.has(v)) return v;
    if (FUNC_RE.test(v)) return v.replace(/\s+/g, ' ');
    return '';
}

// Only http(s) and mailto. Bare domains get https://. Returns '' when unsafe.
export function safeHref(value) {
    let v = decodeEntities(value).replace(/[\u0000-\u0020\u007f]+/g, (m, i) => (i === 0 ? '' : m)).trim();
    v = v.replace(/[\u0000-\u001f\u007f]/g, '');
    if (!v) return '';
    if (/^mailto:/i.test(v)) return /^mailto:[^\s<>"]+@[^\s<>"]+$/i.test(v) ? v : '';
    if (/^https?:\/\//i.test(v)) { try { const u = new URL(v); return /^https?:$/.test(u.protocol) ? u.href : ''; } catch { return ''; } }
    if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return '';                 // any other scheme (javascript:, data:, vbscript:, file: …)
    if (/^[\w.-]+@[\w-]+\.[\w.-]+$/.test(v)) return `mailto:${v}`;
    if (/^(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:[/?#][^\s]*)?$/i.test(v)) { try { return new URL(`https://${v}`).href; } catch { return ''; } }
    return '';
}

export function safeStyle(value) {
    const out = [];
    for (const part of decodeEntities(value).split(';')) {
        const m = /^\s*(color|background-color)\s*:\s*(.+?)\s*$/i.exec(part);
        if (!m) continue;
        const colour = safeColour(m[2]);
        if (colour) out.push(`${m[1].toLowerCase()}: ${colour}`);
    }
    return out.join('; ');
}

function parseAttrs(src) {
    const attrs = {};
    const re = /([^\s"'<>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let m;
    while ((m = re.exec(src))) attrs[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? '';
    return attrs;
}

// Returns a canonical opening tag, or '' to drop it.
export function sanitizeOpenTag(name, attrSrc) {
    const tag = name.toLowerCase();
    const attrs = parseAttrs(attrSrc || '');
    if (SIMPLE.has(tag) || VOID.has(tag)) return `<${tag}>`;
    if (tag === 'span') { const style = safeStyle(attrs.style || ''); return style ? `<span style="${escapeAttr(style)}">` : '<span>'; }
    if (tag === 'a') {
        const href = safeHref(attrs.href || '');
        return href ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer">` : '<a>';
    }
    return '';
}

const TOKEN_RE = /<!--[\s\S]*?(?:-->|$)|<\/?([a-zA-Z][a-zA-Z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|<[!?][^>]*>?/g;

export function sanitizeHtml(html) {
    const src = String(html ?? '');
    const out = [];
    const stack = [];
    let last = 0, dropping = null, m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(src))) {
        const text = src.slice(last, m.index);
        if (!dropping && text) out.push(escapeText(text));
        last = TOKEN_RE.lastIndex;
        const raw = m[0];
        const name = (m[1] || '').toLowerCase();
        if (!name) continue;                                   // comments, doctype, processing instructions
        const closing = raw.startsWith('</');
        if (dropping) { if (closing && name === dropping) dropping = null; continue; }
        if (DROP_CONTENT.has(name)) { if (!closing && !/\/\s*>$/.test(raw)) dropping = name; continue; }
        if (closing) {
            const at = stack.lastIndexOf(name);
            if (at < 0) continue;                              // unmatched closer
            while (stack.length > at) out.push(`</${stack.pop()}>`);
            continue;
        }
        const open = sanitizeOpenTag(name, m[2]);
        if (!open) continue;
        out.push(open);
        if (!VOID.has(name)) stack.push(name);
    }
    if (!dropping) out.push(escapeText(src.slice(last)));
    while (stack.length) out.push(`</${stack.pop()}>`);
    return out.join('');
}

// Raw markdown (Documents "Edit markdown" mode): the same tag allowlist as the inline editor, applied to the
// HTML tags inside markdown text. Markdown characters are left alone (no escaping of > & * …); fenced and
// inline code are kept verbatim. Disallowed tags are dropped (script/style/… with their content).
export function sanitizeMarkdownHtml(md) {
    const parts = String(md ?? '').split(/(^```[^\n]*\n[\s\S]*?^```[ \t]*$|`[^`\n]+`)/m);
    return parts.map((part, i) => (i % 2 ? part : sanitizeTagsOnly(part))).join('');
}
function sanitizeTagsOnly(src) {
    const out = [];
    let last = 0, dropping = null, m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(src))) {
        if (!dropping) out.push(src.slice(last, m.index));
        last = TOKEN_RE.lastIndex;
        const raw = m[0];
        const name = (m[1] || '').toLowerCase();
        if (!name) { if (!raw.startsWith('<!--') && !/^<[!?]/.test(raw) && !dropping) out.push(raw); continue; }
        const closing = raw.startsWith('</');
        if (dropping) { if (closing && name === dropping) dropping = null; continue; }
        if (DROP_CONTENT.has(name)) { if (!closing && !/\/\s*>$/.test(raw)) dropping = name; continue; }
        if (closing) { if (SIMPLE.has(name) || name === 'span' || name === 'a') out.push(`</${name}>`); continue; }
        const open = sanitizeOpenTag(name, m[2]);
        if (open) out.push(open);
    }
    if (!dropping) out.push(src.slice(last));
    return out.join('');
}
