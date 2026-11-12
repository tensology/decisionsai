export function createSubmissionQueue({ context, actions, el, edit }) {
    let working = false;
    let errorText = '';
    let lastMarkup = '';
    const icon = path => `<svg aria-hidden="true" viewBox="0 0 16 16"><path d="${path}"/></svg>`;
    const icons = { steer: 'M2 8h11M9 4l4 4-4 4', fork: 'M4 13V3M4 8h5a3 3 0 0 0 3-3V3M2 5l2-2 2 2M10 5l2-2 2 2', edit: 'm3 11 .5-2L10 3l3 3-6.5 6.5H3z', delete: 'M3 4h10M6 4V2h4v2M4 4l1 10h6l1-10M7 7v4M9 7v4' };
    const labels = { steer: 'Steer current thread', fork: 'Start in a new thread', edit: 'Edit queued item', delete: 'Delete queued item' };
    const needsReview = command => command.status === 'queued' && command.metadata?.review_after_startup_failure === true;
    function scopeStatus(command) {
        if (command.scope_status) return command.scope_status;
        const scope = context.currentChat?.development_scope;
        if (!Array.isArray(scope)) return '';
        const saved = command.metadata?.queued_scope
            ?? (command.source === 'app_view' ? command.metadata?.app_view_scope : null);
        if (!Array.isArray(saved)) return 'unknown';
        return JSON.stringify(saved) === JSON.stringify(scope) ? 'current' : 'earlier';
    }
    const scopeNotice = command => ({
        earlier: 'Queued for an earlier project, board, or ticket.',
        unknown: 'Original project could not be verified. Queue it again from the intended thread.',
    }[scopeStatus(command)] || '');
    const awaitingApprovedWork = () => context.currentRun?.origin === 'proactive_orchestrator';
    function replace(command) {
        const rows = context.controlState.commands || [];
        const index = rows.findIndex(row => row.id === command.id);
        if (index >= 0) rows[index] = command;
        else rows.push(command);
    }
    function render() {
        const root = el('thread-command-queue');
        if (!root) return;
        const rows = (context.controlState.commands || []).filter(row => ['queued', 'editing', 'dispatching'].includes(row.status));
        root.classList.toggle('hidden', !context.currentChat || (!rows.length && !errorText));
        root.setAttribute('aria-busy', String(working));
        const waitingForApprovedWork = awaitingApprovedWork();
        const commandActions = Object.entries(labels);
        const note = waitingForApprovedWork && rows.length ? '<span class="queued-wait-note">Follow-ups wait for the approved run.</span>' : '';
        const markup = `<header><span>${working ? 'Updating queue...' : 'Queued'}${rows.length ? ` (${rows.length})` : ''}</span>${note}</header>${errorText ? `<p class="queued-error" role="alert">${actions.escapeHtml(errorText)}</p>` : ''}<div class="queued-items">${rows.map(command => {
            const draft = command.metadata?.composer;
            const files = command.metadata?.attachments || [];
            const text = draft?.text || command.content || '';
            const review = needsReview(command);
            const held = scopeNotice(command);
            const reservedFork = review && command.metadata.retry_new_thread === true;
            const available = commandActions.filter(([action]) => held ? ['edit', 'delete'].includes(action) : reservedFork
                ? action !== 'fork' : !waitingForApprovedWork || action !== 'steer');
            return `<article class="queued-item" data-queued-id="${Number(command.id)}" aria-label="Queued item: ${actions.escapeHtml(text.slice(0, 100))}"><div class="queued-copy"><p title="${actions.escapeHtml(text)}">${actions.escapeHtml(text)}</p>${files.length ? `<div class="queued-files">${files.map((file, index) => `<span title="${actions.escapeHtml(file.name)}" data-tooltip="${actions.escapeHtml(file.name)}">${!held && String(file.mime_type).startsWith('image/') ? `<img src="/api/workflows/studio/commands/${Number(command.id)}/attachments/${index}" alt="${actions.escapeHtml(file.name)}">` : icon(String(file.mime_type).startsWith('audio/') ? 'M6 11V3l7-1v8M6 11c0 2-4 3-4 1s4-3 4-1M13 10c0 2-4 3-4 1s4-3 4-1' : String(file.mime_type).startsWith('video/') ? 'M2 3h12v10H2zM6 5l5 3-5 3z' : 'M4 2h5l3 3v9H4zM9 2v4h3')}<small>${actions.escapeHtml(file.name)}</small></span>`).join('')}</div>` : ''}${command.status !== 'queued' ? `<small>${command.status === 'editing' ? 'Editing in composer' : 'Starting...'}</small>` : ''}${held || review ? `<small>${actions.escapeHtml(held || command.result_summary)}</small>` : ''}</div><div class="queued-actions">${available.map(([action, label]) => {
                const retry = review && action === 'steer';
                if (retry) label = 'Retry follow-up';
                return `<button type="button" data-queued-action="${retry ? 'retry' : action}" data-command-id="${Number(command.id)}" aria-label="${label}" data-tooltip="${label}"${working || context.editingCommandId || (command.status !== 'queued' && !(command.status === 'editing' && ['edit', 'delete'].includes(action))) ? ' disabled' : ''}>${retry ? 'Retry' : icon(icons[action])}</button>`;
            }).join('')}</div></article>`;
        }).join('')}</div>`;
        if (markup === lastMarkup) return;
        lastMarkup = markup;
        root.innerHTML = markup;
        root.querySelectorAll('[data-queued-action]').forEach(button => button.addEventListener('click', () => act(button.dataset.queuedAction, Number(button.dataset.commandId))));
    }
    async function act(action, id) {
        if (working || context.editingCommandId || (action === 'steer' && awaitingApprovedWork())) return;
        const command = (context.controlState.commands || []).find(row => Number(row.id) === id);
        if (!command || (command.status !== 'queued' && !(command.status === 'editing' && ['edit', 'delete'].includes(action)))) return;
        if (scopeNotice(command) && !['edit', 'delete'].includes(action)) return;
        if (action === 'delete' && !await actions.confirmAction({ title: 'Delete queued item?', message: String(command.metadata?.composer?.text || command.content).slice(0, 160), confirmLabel: 'Delete item', cancelLabel: 'Keep item' })) return;
        working = true; errorText = ''; render();
        try {
            if (action === 'edit') {
                const updated = await actions.api(`/workflows/studio/commands/${id}`, { method: 'PATCH', body: { editing: true, expected_modified_at: command.modified_at } });
                replace(updated);
                await edit(updated);
            } else if (action === 'retry') {
                if (!needsReview(command)) return;
                const manual = typeof command.metadata?.retry_new_thread === 'boolean';
                const path = manual ? `/workflows/studio/commands/${id}/run`
                    : `/workflows/studio/tasks/${context.currentChat.id}/followups/${id}/start`;
                const result = await actions.api(path, {
                    method: 'POST',
                    body: {
                        expected_modified_at: command.modified_at,
                        ...(manual && {
                            new_thread: command.metadata.retry_new_thread,
                            ...(!command.metadata.retry_new_thread && { expected_stop: context.currentRun?.exact_stop ?? null })
                        })
                    }
                });
                command.status = result.starting ? 'dispatching' : 'delivered';
                if (manual && command.metadata.retry_new_thread && result.chat_id) await actions.loadChat(Number(result.chat_id));
            } else if (action === 'delete') {
                replace(await actions.api(`/workflows/studio/commands/${id}`, { method: 'PATCH', body: { cancel: true, expected_modified_at: command.modified_at } }));
            } else {
                const result = await actions.api(`/workflows/studio/commands/${id}/run`, { method: 'POST', body: {
                    new_thread: action === 'fork', expected_modified_at: command.modified_at,
                    ...(action === 'steer' && { expected_stop: context.currentRun?.exact_stop ?? null })
                } });
                command.status = result.starting ? 'dispatching' : 'delivered';
                if (action === 'fork' && result.chat_id) await actions.loadChat(Number(result.chat_id));
            }
            await actions.refreshShell({ preserveConversation: true });
        } catch (error) {
            errorText = error.data?.detail || error.message || 'Could not update this queued item. Try again.';
            if (action === 'steer' || action === 'retry') {
                await actions.refreshShell({ preserveConversation: true }).catch(() => {});
            }
        } finally { working = false; render(); }
    }
    const layer = el('development-composer-layer');
    if (layer && typeof ResizeObserver !== 'undefined') new ResizeObserver(() => {
        const height = Math.ceil(layer.getBoundingClientRect().height);
        const inner = document.querySelector('#conversation .conversation-inner');
        if (inner) inner.style.paddingBottom = `${height + 16}px`;
        el('studio-toast')?.style.setProperty('--studio-composer-height', `${height}px`);
    }).observe(layer);
    return { render, replace };
}
