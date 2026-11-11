// Always-on inline editor for a plan item: markdown -> rich contenteditable (with the floating
// toolbar). Only used for markdown documents (Requirements / Interfaces text); never for DSL. Footer Save.
import { markdownToEditableHtml } from './markdown.js';
import { serializeEditor } from './serialize.js?v=20261103-ifaces3';
import { sanitizeMarkdownHtml } from './sanitize.js?v=20261103-ifaces3';
import { createFormatToolbar } from './toolbar.js';

const live = new Set();
const extraChecks = new Set();   // other unsaved surfaces (ERD canvas, stored drafts) register here
export const addUnsavedCheck = (check) => { extraChecks.add(check); return () => extraChecks.delete(check); };
export const hasUnsavedInlineEdits = () => { prune(); return [...live].some((e) => e.dirty); };
export const hasUnsavedEdits = () => hasUnsavedInlineEdits() || [...extraChecks].some((check) => { try { return Boolean(check()); } catch { return false; } });
function prune() { for (const e of live) if (!e.el.isConnected) { e.destroy(); } }
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', (event) => { if (hasUnsavedEdits()) { event.preventDefault(); event.returnValue = ''; } });
}
export const isRevisionConflict = (error) => /newer revision/i.test(error?.message || '');

export function createInlineEditor({ value, format = 'markdown', label = 'Document', save, toast = () => {}, initialDraft = null, onDraft = () => {}, onSaved = () => {}, conflict = false, onReloadLatest = null }) {
    prune();
    // format: 'markdown' (rich contenteditable + floating toolbar), 'markdown-raw' (plain textarea of the
    // markdown; tags are run through the same allowlist on save), anything else: plain contenteditable text.
    const rich = format === 'markdown';
    const raw = format === 'markdown-raw';
    let saved = String(value ?? '');
    const el = document.createElement('div');
    el.className = `plan-inline-edit${rich ? '' : raw ? ' plan-inline-raw-mode' : ' plan-inline-source'}`;
    el.innerHTML = `<span class="plan-inline-tick" aria-hidden="true" hidden>✓</span>
        <div class="plan-inline-conflict" role="alert" hidden><span>A newer version of this document was saved elsewhere. Your unsaved version is kept here.</span><button type="button" class="header-button plan-inline-reload">Reload latest (your version is copied)</button></div>
        <div class="plan-inline-body">
        ${raw ? `<textarea class="plan-inline-raw" spellcheck="false" aria-label="${label.replace(/"/g, '&quot;')} (markdown)"></textarea>`
        : `<div class="plan-inline-text${rich ? ' message-markdown' : ''}" contenteditable="true" role="textbox" aria-multiline="true" spellcheck="${rich}" aria-label="${label.replace(/"/g, '&quot;')}"></div>`}
        </div>
        <footer class="plan-inline-footer"><span class="plan-inline-state" role="status" data-state="saved">Saved</span><button type="button" class="header-button primary plan-inline-save" disabled>Save</button></footer>`;
    const text = el.querySelector(raw ? '.plan-inline-raw' : '.plan-inline-text');
    const button = el.querySelector('.plan-inline-save');
    const state = el.querySelector('.plan-inline-state');
    const tick = el.querySelector('.plan-inline-tick');
    const banner = el.querySelector('.plan-inline-conflict');
    let blocks = [], initialHtml = [], saving = false, toolbar = null, tickTimer = 0, conflicted = Boolean(conflict);
    const showConflict = () => { conflicted = true; banner.hidden = false; refresh(); };
    banner.querySelector('button').addEventListener('click', () => onReloadLatest?.(current()));

    function load(md) {
        if (rich) {
            const doc = markdownToEditableHtml(md);
            blocks = doc.blocks;
            text.innerHTML = doc.html || '<div class="md-e-p"><br></div>';
            initialHtml = [...text.children].map((c) => c.innerHTML);
        } else if (raw) text.value = md;
        else text.textContent = md;
    }
    const current = (onListMarker) => (rich ? serializeEditor(text, blocks, initialHtml, onListMarker) : raw ? text.value : text.innerText.replace(/\n$/, ''));
    const baseline = () => (rich || raw ? saved : saved.replace(/\n$/, ''));
    function refresh() {
        const draft = current((item, marker) => {
            const number = /^\s*(\d+[.)])/.exec(marker)?.[1];
            if (number) item.dataset.orderedMarker = number;
            else delete item.dataset.orderedMarker;
        });
        const dirty = !saving && draft !== baseline();
        api.dirty = dirty || saving;
        state.dataset.state = saving ? 'saving' : dirty ? 'unsaved' : 'saved';
        state.textContent = saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'Saved';
        button.disabled = saving || !dirty || conflicted;   // a stale draft never overwrites a newer revision
        if (api.dirty) tick.hidden = true;
        // A pending request is still an unsaved draft. Removing it here loses
        // the text if the person switches tabs before the request settles.
        onDraft(draft !== baseline() ? draft : null);
    }
    load(initialDraft ?? saved);
    if (rich) toolbar = createFormatToolbar(text);
    text.addEventListener('input', refresh);
    if (!raw) text.addEventListener('paste', (e) => {   // plain text only: nothing unsafe enters the document
        e.preventDefault();
        document.execCommand('insertText', false, e.clipboardData?.getData('text/plain') || '');
    });
    button.addEventListener('click', async () => {
        if (button.disabled) return;
        const submitted = current();
        const content = rich ? submitted : raw ? sanitizeMarkdownHtml(submitted) : submitted + (saved.endsWith('\n') ? '\n' : '');
        saving = true; refresh();
        try {
            const result = await save(content);
            saved = content;
            if (raw && text.value === submitted && submitted !== content) text.value = content;
            saving = false;
            refresh();
            onSaved(content);
            const pendingSync = Boolean(result?.file_sync_error);
            if (!api.dirty) {
                tick.textContent = pendingSync ? '⟳' : '✓';
                tick.dataset.state = pendingSync ? 'sync-pending' : 'saved';
                tick.hidden = false; clearTimeout(tickTimer); tickTimer = setTimeout(() => { tick.hidden = true; }, 2400);
            }
            if (pendingSync) toast('Saved. The file sync is pending and will retry.', 'info');
            else if (!api.dirty) toast('Saved');
        } catch (error) {
            saving = false;
            if (isRevisionConflict(error)) showConflict();
            else { refresh(); state.dataset.state = 'error'; state.textContent = `Not saved: ${error?.message || 'unknown error'}`; }   // edits stay in the editor
            toast(`Could not save: ${error?.message || 'unknown error'}`, 'error');
        }
    });
    const api = {
        el, dirty: false,
        get value() { return current(); },
        destroy() { toolbar?.destroy(); live.delete(api); },
    };
    live.add(api);
    if (conflicted) showConflict(); else refresh();
    return api;
}
