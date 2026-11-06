// Floating formatting toolbar for a contenteditable root. Never takes focus from the text:
// buttons cancel mousedown, only the bar itself receives pointer events, position follows the Range.
import { safeHref } from './sanitize.js';

export const SWATCHES = ['#f97316', '#ef4444', '#eab308', '#22c55e', '#3b82f6', '#a855f7', '#94a3b8'];
const ICON = { bold: '<b>B</b>', underline: '<u>U</u>', strike: '<s>S</s>', colour: '<span class="md-tb-a">A</span>', highlight: '<span class="md-tb-hl">H</span>', link: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6.5 9.5l3-3"/><path d="M7 4.5l1-1a2.8 2.8 0 014 4l-1 1"/><path d="M9 11.5l-1 1a2.8 2.8 0 01-4-4l1-1"/></svg>' };   // SVG, not an emoji (emoji fallback crashed headless Chromium)

export function createFormatToolbar(root) {
    const bar = document.createElement('div');
    bar.className = 'md-toolbar hidden';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Text formatting');
    bar.innerHTML = `
        <button type="button" data-fmt="bold" aria-label="Bold (Ctrl/Cmd+B)" title="Bold" aria-pressed="false">${ICON.bold}</button>
        <button type="button" data-fmt="underline" aria-label="Underline (Ctrl/Cmd+U)" title="Underline" aria-pressed="false">${ICON.underline}</button>
        <button type="button" data-fmt="strike" aria-label="Strikethrough" title="Strikethrough" aria-pressed="false">${ICON.strike}</button>
        <button type="button" data-fmt="colour" aria-label="Font colour" title="Font colour" aria-haspopup="true" aria-expanded="false" aria-pressed="false">${ICON.colour}</button>
        <button type="button" data-fmt="highlight" aria-label="Highlight" title="Highlight" aria-pressed="false">${ICON.highlight}</button>
        <button type="button" data-fmt="link" aria-label="Link (Ctrl/Cmd+K)" title="Link" aria-pressed="false">${ICON.link}</button>
        <div class="md-tb-pop md-tb-colours hidden" role="group" aria-label="Font colours">
            ${SWATCHES.map((c) => `<button type="button" data-colour="${c}" aria-label="Colour ${c}" style="background:${c}"></button>`).join('')}
            <button type="button" data-colour="" class="md-tb-clear" aria-label="Clear colour">Clear</button>
        </div>
        <form class="md-tb-pop md-tb-link hidden" aria-label="Link">
            <input type="text" name="url" placeholder="https://…" aria-label="Link URL" autocomplete="off">
            <button type="submit" data-link="apply">Apply</button>
            <button type="button" data-link="remove">Remove</button>
            <span class="md-tb-error" role="alert"></span>
        </form>`;
    document.body.append(bar);
    let savedRange = null;
    const linkForm = bar.querySelector('.md-tb-link');
    const colours = bar.querySelector('.md-tb-colours');

    const selectionRange = () => {
        const sel = document.getSelection();
        if (!sel || !sel.rangeCount) return null;
        const range = sel.getRangeAt(0);
        return root.contains(range.commonAncestorContainer) ? range : null;
    };
    const closestIn = (node, selector) => { const el = node?.nodeType === 1 ? node : node?.parentElement; const hit = el?.closest(selector); return hit && root.contains(hit) ? hit : null; };
    const restore = () => { if (!savedRange) return; const sel = document.getSelection(); sel.removeAllRanges(); sel.addRange(savedRange); };
    const exec = (cmd, value = null, css = false) => { document.execCommand('styleWithCSS', false, css); document.execCommand(cmd, false, value); document.execCommand('styleWithCSS', false, false); };
    const hide = () => { bar.classList.add('hidden'); colours.classList.add('hidden'); linkForm.classList.add('hidden'); bar.querySelector('[data-fmt="colour"]').setAttribute('aria-expanded', 'false'); };
    const popOpen = () => !colours.classList.contains('hidden') || !linkForm.classList.contains('hidden');

    function hasColour(node) {
        for (let el = node?.nodeType === 1 ? node : node?.parentElement; el && el !== root && root.contains(el); el = el.parentElement) {
            if ((el.tagName === 'SPAN' && el.style.color) || (el.tagName === 'FONT' && el.getAttribute('color'))) return true;
        }
        return false;
    }
    function syncState(range) {
        const node = range.startContainer;
        const states = {
            // Active state from the DOM around the caret (deterministic, cheap).
            bold: Boolean(closestIn(node, 'b, strong')), underline: Boolean(closestIn(node, 'u')), strike: Boolean(closestIn(node, 's, strike, del')),
            highlight: Boolean(closestIn(node, 'mark')), link: Boolean(closestIn(node, 'a')),
            colour: hasColour(node),
        };
        for (const [k, v] of Object.entries(states)) bar.querySelector(`[data-fmt="${k}"]`).setAttribute('aria-pressed', String(v));
    }
    function position(range) {
        let rect = range.getBoundingClientRect();
        if (!rect.width && !rect.height) rect = range.startContainer.parentElement?.getBoundingClientRect() || rect;
        const box = root.getBoundingClientRect();
        if (rect.bottom < box.top || rect.top > box.bottom || rect.bottom < 0 || rect.top > window.innerHeight) { hide(); return false; }
        bar.classList.remove('hidden');
        const w = bar.offsetWidth || 220, h = bar.offsetHeight || 34;
        const left = Math.max(8, Math.min(window.innerWidth - w - 8, rect.left + rect.width / 2 - w / 2));
        const top = rect.top - h - 8 >= 8 ? rect.top - h - 8 : rect.bottom + 8;
        bar.style.left = `${Math.round(left)}px`;
        bar.style.top = `${Math.round(top)}px`;
        return true;
    }
    function update() {
        if (popOpen()) return;
        const range = selectionRange();
        if (!range) return hide();
        const inLink = closestIn(range.startContainer, 'a');
        if (range.collapsed && !inLink) return hide();
        savedRange = range.cloneRange();
        if (position(range)) syncState(range);
    }
    function openLink() {
        const range = selectionRange() || savedRange;
        if (!range) return;
        savedRange = range.cloneRange();
        if (!position(range)) bar.classList.remove('hidden');
        colours.classList.add('hidden');
        linkForm.classList.remove('hidden');
        const a = closestIn(range.startContainer, 'a');
        linkForm.url.value = a?.getAttribute('href') || '';
        linkForm.querySelector('.md-tb-error').textContent = '';
        linkForm.url.focus();
    }
    function applyLink() {
        const href = safeHref(linkForm.url.value);
        if (!href) { linkForm.querySelector('.md-tb-error').textContent = 'Use an http(s) or mailto link.'; return; }
        restore();
        const a = closestIn(savedRange?.startContainer, 'a');
        if (a && savedRange.collapsed) a.setAttribute('href', href);
        else if (savedRange?.collapsed) exec('insertHTML', `<a href="${href.replace(/"/g, '&quot;')}">${href.replace(/</g, '&lt;')}</a>`);
        else exec('createLink', href);
        root.querySelectorAll('a').forEach((link) => { link.target = '_blank'; link.rel = 'noopener noreferrer'; });
        hide(); root.focus(); root.dispatchEvent(new Event('input', { bubbles: true }));
    }
    function toggleHighlight() {
        const range = selectionRange();
        if (!range) return;
        const mark = closestIn(range.startContainer, 'mark');
        if (mark) {
            const r = document.createRange(); r.selectNodeContents(mark);
            const sel = document.getSelection(); sel.removeAllRanges(); sel.addRange(r);
            const parentRange = document.createRange(); parentRange.selectNode(mark); sel.removeAllRanges(); sel.addRange(parentRange);
            exec('insertHTML', mark.innerHTML);
        } else if (!range.collapsed) {
            const holder = document.createElement('div'); holder.append(range.cloneContents());
            exec('insertHTML', `<mark>${holder.innerHTML}</mark>`);
        }
    }
    function setColour(colour) {
        restore();
        if (colour) exec('foreColor', colour, true);
        else {
            exec('removeFormat');   // clears inline colour (and bold etc. on the selection, like most editors' "clear")
        }
        colours.classList.add('hidden');
        root.dispatchEvent(new Event('input', { bubbles: true }));
        update();
    }
    function run(fmt) {
        if (fmt === 'bold') exec('bold');
        else if (fmt === 'underline') exec('underline');
        else if (fmt === 'strike') exec('strikeThrough');
        else if (fmt === 'highlight') toggleHighlight();
        else if (fmt === 'link') return openLink();
        else if (fmt === 'colour') { linkForm.classList.add('hidden'); const open = colours.classList.toggle('hidden'); bar.querySelector('[data-fmt="colour"]').setAttribute('aria-expanded', String(!open)); return; }
        root.dispatchEvent(new Event('input', { bubbles: true }));
        update();
    }

    bar.addEventListener('mousedown', (e) => { if (!e.target.closest('input')) e.preventDefault(); });   // keep the text selection
    bar.addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.fmt) run(b.dataset.fmt);
        else if ('colour' in b.dataset) setColour(b.dataset.colour);
        else if (b.dataset.link === 'remove') { restore(); const a = closestIn(savedRange?.startContainer, 'a'); if (a) { const r = document.createRange(); r.selectNode(a); const s = document.getSelection(); s.removeAllRanges(); s.addRange(r); } exec('unlink'); hide(); root.dispatchEvent(new Event('input', { bubbles: true })); }
    });
    linkForm.addEventListener('submit', (e) => { e.preventDefault(); applyLink(); });
    linkForm.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); hide(); restore(); root.focus(); } });
    root.addEventListener('keydown', (e) => {
        const mod = e.metaKey || e.ctrlKey;
        if (e.key === 'Escape') { hide(); return; }
        if (!mod || e.altKey) return;
        const k = e.key.toLowerCase();
        if (k === 'b' || k === 'u') { e.preventDefault(); run(k === 'b' ? 'bold' : 'underline'); }
        else if (k === 'k') { e.preventDefault(); openLink(); }
    });
    const onSelection = () => { if (document.activeElement === root || root.contains(document.activeElement)) update(); else if (!bar.contains(document.activeElement)) hide(); };
    const onMove = () => { if (!bar.classList.contains('hidden') && savedRange) position(savedRange); };
    document.addEventListener('selectionchange', onSelection);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    root.addEventListener('blur', () => setTimeout(() => { if (!bar.contains(document.activeElement) && document.activeElement !== root) hide(); }, 0));
    return {
        el: bar, hide, update,
        destroy() { document.removeEventListener('selectionchange', onSelection); window.removeEventListener('scroll', onMove, true); window.removeEventListener('resize', onMove); bar.remove(); },
    };
}
