// Editable DOM -> markdown (browser). Unchanged blocks emit their original source verbatim.
import { safeHref, safeStyle } from './sanitize.js';

function inline(node) {
    let out = '';
    node.childNodes.forEach((child, i) => {
        if (child.nodeType === 3) { out += child.nodeValue.replace(/\u00a0/g, ' ').replace(/\u200b/g, ''); return; }
        if (child.nodeType !== 1) return;
        const tag = child.tagName.toLowerCase();
        if (tag === 'br') { if (i < node.childNodes.length - 1) out += '<br>'; return; }
        const body = inline(child);
        if (!body && tag !== 'code') return;
        if (tag === 'strong' || tag === 'b') out += `**${body}**`;
        else if (tag === 'em' || tag === 'i') out += `*${body}*`;
        else if (tag === 's' || tag === 'del' || tag === 'strike') out += `~~${body}~~`;
        else if (tag === 'code') out += `\`${child.textContent}\``;
        else if (tag === 'u') out += `<u>${body}</u>`;
        else if (tag === 'mark') out += `<mark>${body}</mark>`;
        else if (tag === 'a') { const href = safeHref(child.getAttribute('href') || ''); out += href ? `[${body}](${href})` : body; }
        else if (tag === 'span' || tag === 'font') {
            let style = safeStyle(child.getAttribute('style') || '');
            if (tag === 'font' && child.getAttribute('color')) style = safeStyle(`color: ${child.getAttribute('color')}`);
            const weight = child.style?.fontWeight, deco = child.style?.textDecorationLine || child.style?.textDecoration || '';
            let wrapped = style ? `<span style="${style}">${body}</span>` : body;
            if (weight === 'bold' || Number(weight) >= 600) wrapped = `**${wrapped}**`;
            if (/underline/.test(deco)) wrapped = `<u>${wrapped}</u>`;
            if (/line-through/.test(deco)) wrapped = `~~${wrapped}~~`;
            out += wrapped;
        } else if (tag === 'div' || tag === 'p') out += (out ? '\n' : '') + body;
        else out += body;
    });
    return out;
}

export function serializeEditor(root, blocks, initialHtml) {
    const lines = [];
    for (const el of root.children) {
        const index = el.dataset.b === undefined ? -1 : Number(el.dataset.b);
        if (index >= 0 && initialHtml[index] === el.innerHTML && blocks[index]) { lines.push(blocks[index].source); continue; }
        if (el.classList.contains('md-e-raw') || el.tagName === 'PRE') { lines.push(el.textContent.replace(/\n$/, '')); continue; }
        const text = inline(el);
        if (el.classList.contains('md-e-gap') && !text.trim()) { lines.push(''); continue; }
        let marker = el.dataset.marker || '';
        // A new / edited numbered item continues the list above it (Enter in "2. …" gives "3. …", not a second "2.").
        const num = /^(\s*)(\d+)([.)]\s+)$/.exec(marker);
        const prev = num && /^(\s*)(\d+)[.)]\s/.exec(lines[lines.length - 1] || '');
        if (num && prev && prev[1] === num[1]) marker = `${num[1]}${Number(prev[2]) + 1}${num[3]}`;
        lines.push(...`${marker}${text}`.split('\n').map((l, i) => (i ? l : l)));
    }
    return lines.join('\n');
}
