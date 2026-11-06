// Plan chat part 2: tool-call cards (N1) and memory panel (N2). Pure helpers; wiring is in conversation.js.

const VERBS = { create: 'Created', update: 'Updated' };
const NOUNS = { interface: 'interface', erd: 'ERD', diagram: 'diagram', document: 'document' };

export function toolLabel(tool) {
    const [verb, noun] = String(tool || '').split('_');
    return `${VERBS[verb] || 'Changed'} ${NOUNS[noun] || 'item'}`;
}

// Unsaved inline editors -> [{id, base_revision}] (keys look like "12" or "12:desc"); one entry per item.
export function draftsPayload(inlineDrafts) {
    const out = new Map();
    for (const [key, value] of Object.entries(inlineDrafts || {})) {
        const id = Number.parseInt(String(key), 10);
        if (Number.isInteger(id) && id > 0 && !out.has(id)) out.set(id, { id, base_revision: value?.baseRevision ?? null });
    }
    return [...out.values()];
}

// Undo is offered only while the item is still at the revision the AI wrote (else it changed since).
export function cardState(card, item) {
    if (card.status === 'conflict') return item ? 'conflict' : 'gone';
    if (!item) return card.from_revision === 0 ? 'undone' : 'gone';
    return Number(item.revision_count) === Number(card.to_revision) ? 'undoable' : 'changed';
}

export function toolCardsHtml(message, items, esc, dismissed = new Set()) {
    const cards = (message.tool_calls || []).map((card, index) => ({ card, index })).filter(({ index }) => !dismissed.has(`${message.id}:${index}`));
    if (!cards.length) return '';
    return `<div class="plan-tool-cards">${cards.map(({ card, index }) => {
        const item = (items || []).find((row) => String(row.id) === String(card.id));
        const state = cardState(card, item);
        const ref = `data-turn="${esc(message.id)}" data-card="${esc(index)}"`;
        const revs = card.status === 'conflict' ? 'not applied' : (card.from_revision ? `r${esc(card.from_revision)} → r${esc(card.to_revision)}` : `new · r${esc(card.to_revision)}`);
        const note = state === 'conflict' ? '<p class="plan-tool-note">You have unsaved changes in this item, so the AI version was not written.</p>'
            : state === 'changed' ? '<p class="plan-tool-note">Edited since — undo from the item history instead.</p>'
            : state === 'undone' ? '<p class="plan-tool-note">Undone (archived).</p>' : '';
        const warn = (card.warnings || []).length ? `<p class="plan-tool-note plan-tool-warn">${esc(card.warnings[0])}</p>` : '';
        const actions = state === 'conflict'
            ? `<button type="button" data-tool-apply ${ref}>Apply AI version</button><button type="button" data-tool-keep ${ref}>Keep mine</button>`
            : `${item ? `<button type="button" data-tool-open="${esc(card.id)}">Open</button>` : ''}${state === 'undoable' ? `<button type="button" data-tool-undo ${ref}>Undo</button>` : ''}`;
        return `<div class="plan-tool-card" data-state="${esc(state)}" role="group" aria-label="${esc(toolLabel(card.tool))}: ${esc(card.title || '')}">
            <div class="plan-tool-head"><span class="plan-tool-name">${esc(toolLabel(card.tool))}</span><strong class="plan-tool-title">${esc(card.title || 'Untitled')}</strong><small class="plan-tool-rev">${revs}</small></div>
            ${note}${warn}<div class="plan-tool-actions">${actions}</div></div>`;
    }).join('')}</div>`;
}

export function memoryPanelHtml(view, esc) {
    const rows = (view.records || []).slice().reverse().map((row) => `<li><p>${esc(row.text)}</p>${row.at ? `<small>${esc(new Date(row.at).toLocaleString())}</small>` : ''}</li>`).join('');
    return `<h2>Chat memory</h2>
        <p class="plan-memory-state">${view.enabled ? 'On: finished turns are saved (redacted) and other chats of this project can recall them.' : 'Off: nothing from this chat is saved or recalled.'}</p>
        <label class="plan-memory-toggle"><input type="checkbox" id="plan-memory-enabled"${view.enabled ? ' checked' : ''}> Remember this chat</label>
        <ol class="plan-memory-list">${rows || '<li class="plan-memory-empty">No saved memory for this chat.</li>'}</ol>
        <footer><button type="button" data-memory-forget="chat"${view.count ? '' : ' disabled'}>Forget this chat</button><button type="button" data-memory-forget="project">Forget whole project</button><button type="button" data-memory-close>Close</button></footer>`;
}
