// One selected source, one editable draft, one explicit send approval.
export async function openSourceReply({ chatId, responseId, api, opener }) {
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-dialog source-reply-dialog';
    dialog.setAttribute('aria-labelledby', 'source-reply-title');
    dialog.innerHTML = `<form method="dialog">
      <header><h2 id="source-reply-title">Reply to source</h2><button type="button" data-close aria-label="Close reply">×</button></header>
      <div class="source-reply-content">
        <label for="source-reply-source">Source</label><select id="source-reply-source"></select>
        <section data-draft-fields hidden><div class="source-reply-destination" aria-live="polite"></div>
        <label for="source-reply-text">Reply</label><textarea id="source-reply-text" rows="9" disabled></textarea>
        <label for="source-reply-instruction">Rewrite guidance (optional)</label><input id="source-reply-instruction" type="text" placeholder="For example: shorter, or explain the next step"></section>
        <p class="source-reply-status" role="status" aria-live="polite"></p>
      </div>
      <footer><button type="button" data-cancel class="secondary-button">Cancel</button><button type="button" data-rewrite class="secondary-button" disabled>Rewrite</button><button type="button" data-approve class="primary-button" disabled>Approve</button></footer>
    </form>`;
    document.body.append(dialog);
    const sourceSelect = dialog.querySelector('select');
    const text = dialog.querySelector('textarea');
    const instruction = dialog.querySelector('input');
    const status = dialog.querySelector('[role="status"]');
    const destination = dialog.querySelector('.source-reply-destination');
    const fields = dialog.querySelector('[data-draft-fields]');
    const approve = dialog.querySelector('[data-approve]');
    const rewrite = dialog.querySelector('[data-rewrite]');
    const cancel = dialog.querySelector('[data-cancel]');
    const close = dialog.querySelector('[data-close]');
    const endpoint = `/workflows/studio/tasks/${chatId}/source-replies`;
    let draft = null;
    let busy = false;
    let sending = false;
    let closed = false;
    function setBusy(value, message = '') {
        busy = value;
        status.textContent = message;
        sourceSelect.disabled = value;
        text.disabled = value || !draft || draft.status !== 'draft';
        instruction.disabled = value;
        approve.disabled = value || !draft || draft.status !== 'draft' || !text.value.trim();
        rewrite.disabled = value || !sourceSelect.value || (draft && draft.status !== 'draft');
        cancel.disabled = value && sending;
        close.disabled = value && sending;
    }
    function showDestination(target) {
        destination.replaceChildren();
        const lines = target.channel === 'whatsapp'
            ? [`WhatsApp: ${target.label || target.jid}`, `Conversation: ${target.jid}`]
            : [`${target.channel === 'gmail' ? 'Gmail' : 'Mailshot'}: Reply all`, ...(target.from ? [`From: ${target.from}`] : []), `To: ${target.to.join(', ')}`,
               ...(target.cc.length ? [`Cc: ${target.cc.join(', ')}`] : []), `Subject: ${target.subject}`];
        for (const line of lines) {
            const paragraph = document.createElement('p');
            paragraph.textContent = line;
            destination.append(paragraph);
        }
    }
    async function generate() {
        if (busy || !sourceSelect.value) return;
        fields.hidden = false;
        const previous = draft;
        setBusy(true, previous ? 'Rewriting reply...' : 'Drafting reply...');
        try {
            const nextDraft = await api(endpoint, { method: 'POST', body: {
                response_id: responseId, source_key: sourceSelect.value,
                previous: text.value, instruction: instruction.value, replaces: previous?.id || ''
            }});
            if (closed) {
                await api(`${endpoint}/${nextDraft.id}/cancel`, { method: 'POST', body: {} });
                return;
            }
            draft = nextDraft;
            text.value = draft.text;
            showDestination(draft.target);
            setBusy(false, 'Review the destination and message. Approve sends this exact text.');
            text.focus();
        } catch (error) {
            if (closed) return;
            draft = previous;
            setBusy(false, error.message || 'Could not draft the reply. Please retry.');
        }
    }
    async function dismiss() {
        if (sending) return;
        if (draft?.status === 'draft') {
            setBusy(true, 'Cancelling reply...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) { setBusy(false, error.message || 'Could not cancel. Please retry.'); return; }
        }
        closed = true;
        dialog.close();
    }
    dialog.addEventListener('close', () => { closed = true; dialog.remove(); opener?.focus(); }, { once: true });
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); dismiss(); });
    dialog.addEventListener('click', (event) => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dismiss();
    });
    close.addEventListener('click', dismiss);
    cancel.addEventListener('click', dismiss);
    dialog.querySelector('form').addEventListener('submit', (event) => event.preventDefault());
    sourceSelect.addEventListener('change', async () => {
        if (draft?.status === 'draft') {
            setBusy(true, 'Changing source...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) { sourceSelect.value = draft.source_key; setBusy(false, error.message); return; }
        }
        draft = null; text.value = ''; destination.replaceChildren(); setBusy(false);
        fields.hidden = !sourceSelect.value;
        await generate();
    });
    text.addEventListener('input', () => { approve.disabled = busy || !text.value.trim() || draft?.status !== 'draft'; });
    rewrite.addEventListener('click', generate);
    approve.addEventListener('click', async () => {
        if (busy || !draft || !text.value.trim()) return;
        sending = true;
        setBusy(true, 'Sending reply...');
        try {
            draft = await api(`${endpoint}/${draft.id}/approve`, { method: 'POST', body: { approved: true, text: text.value } });
            sending = false;
            setBusy(false, 'Reply sent.');
            sourceSelect.disabled = true;
            cancel.textContent = 'Close';
        } catch (error) {
            // A lost response might follow a successful send. Never invite a blind resend.
            draft.status = 'unconfirmed';
            sending = false;
            setBusy(false, error.message || 'Send was not confirmed. Check the original conversation before sending again.');
            sourceSelect.disabled = true;
            cancel.textContent = 'Close';
        }
    });
    dialog.showModal();
    setBusy(true, 'Loading proposal sources...');
    try {
        const result = await api(endpoint);
        if (closed) return;
        const sources = result.sources || [];
        if (!sources.length) throw new Error('No original source is available for this proposal.');
        sourceSelect.replaceChildren(new Option(sources.length > 1 ? 'Choose a source...' : 'Select source', ''));
        for (const source of sources) sourceSelect.add(new Option(`${source.channel}: ${source.label}`, source.key));
        setBusy(false, sources.length > 1 ? 'Choose the conversation or email to reply to.' : '');
        if (sources.length === 1) { sourceSelect.value = sources[0].key; await generate(); }
    } catch (error) { setBusy(false, error.message); }
}
