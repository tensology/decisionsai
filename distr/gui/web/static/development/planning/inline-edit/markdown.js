// Markdown -> editable blocks (pure, no DOM). One block per line for headings, paragraphs and
// list items; fences, tables, quotes, HTML blocks, rules stay "raw" (edited as plain source).
// Untouched blocks are written back from their original source, so a save never rewrites them.
import { sanitizeHtml, escapeText, escapeAttr, safeHref } from './sanitize.js';

export function parseBlocks(md) {
    const lines = String(md ?? '').replace(/\r\n?/g, '\n').split('\n');
    const blocks = [];
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const fence = /^\s*(`{3,}|~{3,})/.exec(line);
        if (fence) {
            let j = i + 1;
            while (j < lines.length && !new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[j])) j++;
            j = Math.min(j, lines.length - 1);
            blocks.push({ kind: 'raw', source: lines.slice(i, j + 1).join('\n') });
            i = j; continue;
        }
        if (/^\s*\|.*\|\s*$/.test(line)) {
            let j = i;
            while (j + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[j + 1])) j++;
            blocks.push({ kind: 'raw', source: lines.slice(i, j + 1).join('\n') });
            i = j; continue;
        }
        if (!line.trim()) { blocks.push({ kind: 'gap', source: line }); continue; }
        const heading = /^(#{1,6})\s+(.*)$/.exec(line);
        if (heading) { blocks.push({ kind: 'heading', level: heading[1].length, marker: `${heading[1]} `, text: heading[2], source: line }); continue; }
        const item = /^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$/.exec(line);
        if (item) { blocks.push({ kind: 'item', marker: item[1], text: item[2], source: line }); continue; }
        if (/^\s*(?:>|<\/?[a-zA-Z]+[^>]*>\s*$|(?:-{3,}|\*{3,}|_{3,})\s*$)/.test(line) && !/^\s*<(u|mark|span|a|strong|em|b|i|s|del|code)\b/i.test(line)) {
            blocks.push({ kind: 'raw', source: line }); continue;
        }
        blocks.push({ kind: 'para', text: line, source: line });
    }
    return blocks;
}

// Inline markdown + allowed inline HTML -> sanitised HTML.
export function inlineToHtml(src) {
    const held = [];
    const hold = (html) => `\u0000${held.push(html) - 1}\u0000`;
    let s = String(src ?? '');
    s = s.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${escapeText(code)}</code>`));
    s = s.replace(/<\/?[a-zA-Z][a-zA-Z0-9-]*(?:[^>"']|"[^"]*"|'[^']*')*>/g, (tag) => hold(tag));   // raw HTML: sanitised below
    s = escapeText(s);
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, text, href) => {
        const safe = safeHref(href.replace(/&amp;/g, '&'));
        // The opening tag is held so emphasis rules never touch the URL (e.g. `*` / `_` inside it).
        return safe ? `${hold(`<a href="${escapeAttr(safe)}" target="_blank" rel="noopener noreferrer">`)}${text}${hold('</a>')}` : m;
    });
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
        .replace(/~~([^~\n]+?)~~/g, '<s>$1</s>')
        .replace(/(^|[^\w*])\*([^*\n]+?)\*(?!\w)/g, '$1<em>$2</em>')
        .replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, '$1<em>$2</em>');
    s = s.replace(/\u0000(\d+)\u0000/g, (_, n) => held[Number(n)]);
    return sanitizeHtml(s);
}

export function blockHtml(block, index) {
    const at = `data-b="${index}"`;
    if (block.kind === 'gap') return `<div class="md-e-gap" ${at}><br></div>`;
    if (block.kind === 'raw') return `<pre class="md-e-raw" ${at} spellcheck="false">${escapeText(block.source)}</pre>`;
    if (block.kind === 'heading') return `<div class="md-e-h md-e-h${block.level}" ${at} data-marker="${escapeAttr(block.marker)}">${inlineToHtml(block.text) || '<br>'}</div>`;
    if (block.kind === 'item') return `<div class="md-e-item" ${at} data-marker="${escapeAttr(block.marker)}">${inlineToHtml(block.text) || '<br>'}</div>`;
    return `<div class="md-e-p" ${at}>${inlineToHtml(block.text) || '<br>'}</div>`;
}

export function markdownToEditableHtml(md) {
    const blocks = parseBlocks(md);
    return { blocks, html: blocks.map(blockHtml).join('') };
}
