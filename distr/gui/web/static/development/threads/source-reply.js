// One selected source, one editable draft, one explicit send approval.
export async function openSourceReply({ chatId, responseId, api, opener, onClose, toast }) {
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-dialog source-reply-dialog';
    dialog.setAttribute('aria-labelledby', 'source-reply-title');
    dialog.innerHTML = `<form method="dialog">
      <header><h2 id="source-reply-title">Message Client</h2><button type="button" data-close aria-label="Close message">×</button></header>
      <div class="source-reply-content">
        <label for="source-reply-source">To:</label><select id="source-reply-source"></select>
        <section data-draft-fields hidden><div class="source-reply-destination" aria-live="polite"></div>
        <label for="source-reply-text">Reply</label><textarea id="source-reply-text" rows="9" disabled></textarea>
        <details data-unsent-edits hidden><summary>Your unsent edits (not sent)</summary><textarea aria-label="Your unsent edits" rows="3" readonly></textarea></details>
        <label for="source-reply-instruction">Rewrite guidance (optional)</label><input id="source-reply-instruction" type="text" placeholder="For example: shorter, or explain the next step"></section>
        <p class="source-reply-status" role="status" aria-live="polite"></p>
      </div>
      <footer><button type="button" data-rewrite class="secondary-button" disabled>Rewrite</button><button type="button" data-approve class="primary-button" disabled>Approve</button></footer>
    </form>`;
    document.body.append(dialog);
    const sourceSelect = dialog.querySelector('select');
    const text = dialog.querySelector('textarea');
    const unsentEdits = dialog.querySelector('[data-unsent-edits]');
    const instruction = dialog.querySelector('input');
    const status = dialog.querySelector('[role="status"]');
    const destination = dialog.querySelector('.source-reply-destination');
    const fields = dialog.querySelector('[data-draft-fields]');
    const approve = dialog.querySelector('[data-approve]');
    const rewrite = dialog.querySelector('[data-rewrite]');
    const close = dialog.querySelector('[data-close]');
    const endpoint = `/workflows/studio/tasks/${chatId}/source-replies`;
    let draft = null;
    let busy = false;
    let sending = false;
    let closed = false;
    let sourceChanged = false;
    const retained = new Map();
    function setBusy(value, message = '') {
        busy = value;
        status.textContent = message;
        sourceSelect.disabled = value || sourceChanged;
        text.disabled = value || !draft;
        text.readOnly = sourceChanged || !draft || draft.status !== 'draft';
        instruction.disabled = value || sourceChanged || (draft && draft.status !== 'draft');
        approve.disabled = value || text.readOnly || draft?.rewrite_required || !text.value.trim();
        rewrite.disabled = value || sourceChanged || !sourceSelect.value || (draft && draft.status !== 'draft');
        close.disabled = value && sending;
    }
    function showDraft(nextDraft) {
        draft = nextDraft;
        retained.set(draft.source_key, draft);
        fields.hidden = false;
        text.value = draft.text;
        showDestination(draft.target);
        const messages = {
            draft: 'Review the destination and message. Approve sends this exact text.',
            sent: 'Previously sent.',
            sending: 'This reply is sending or awaiting confirmation. Check the original conversation before sending again.',
            unconfirmed: 'Send was not confirmed. Check the original conversation before sending again. Your text is retained.'
        };
        setBusy(false, draft.rewrite_required && draft.status === 'draft'
            ? draft.rewrite_notice || 'The source messages changed. Rewrite and review a new draft.'
            : messages[draft.status] || 'This reply has already been handled.');
    }
    function showDestination(target) {
        destination.replaceChildren();
        destination.hidden = target.channel === 'whatsapp';
        if (destination.hidden) return;
        const lines = [`${target.channel === 'gmail' ? 'Gmail' : 'Mailshot'}: Reply all`, ...(target.from ? [`From: ${target.from}`] : []), `To: ${target.to.join(', ')}`,
               ...(target.cc.length ? [`Cc: ${target.cc.join(', ')}`] : []), `Subject: ${target.subject}`];
        for (const line of lines) {
            const paragraph = document.createElement('p');
            paragraph.textContent = line;
            destination.append(paragraph);
        }
    }
    async function generate() {
        if (busy || sourceChanged || !sourceSelect.value) return;
        fields.hidden = false;
        const previous = draft;
        setBusy(true, previous ? 'Rewriting reply...' : 'Drafting reply...');
        try {
            const nextDraft = await api(endpoint, { method: 'POST', body: {
                response_id: responseId, source_key: sourceSelect.value,
                previous: text.value, instruction: instruction.value, replaces: previous?.id || ''
            }});
            // A late response can refer to a draft already open in another window.
            if (closed) return;
            showDraft(nextDraft);
            text.focus();
        } catch (error) {
            if (closed) return;
            draft = previous;
            setBusy(false, error.message || 'Could not draft the reply. Please retry.');
        }
    }
    async function dismiss() {
        if (sending) return;
        if (!sourceChanged && draft?.status === 'draft') {
            setBusy(true, 'Cancelling reply...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) { setBusy(false, error.message || 'Could not cancel. Please retry.'); return; }
        }
        closed = true;
        dialog.close();
    }
    dialog.addEventListener('close', () => { closed = true; dialog.remove(); opener?.focus(); onClose?.(); }, { once: true });
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); dismiss(); });
    dialog.addEventListener('click', (event) => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dismiss();
    });
    close.addEventListener('click', dismiss);
    dialog.querySelector('form').addEventListener('submit', (event) => event.preventDefault());
    sourceSelect.addEventListener('change', async () => {
        if (draft?.status === 'draft') {
            setBusy(true, 'Changing source...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) { sourceSelect.value = draft.source_key; setBusy(false, error.message); return; }
            retained.delete(draft.source_key);
        }
        draft = null; text.value = ''; destination.replaceChildren(); setBusy(false);
        fields.hidden = !sourceSelect.value;
        if (retained.has(sourceSelect.value)) showDraft(retained.get(sourceSelect.value));
        else await generate();
    });
    text.addEventListener('input', () => { approve.disabled = busy || text.readOnly || draft?.rewrite_required || !text.value.trim(); });
    rewrite.addEventListener('click', generate);
    approve.addEventListener('click', async () => {
        if (busy || text.readOnly || !draft || draft.rewrite_required || !text.value.trim()) return;
        sending = true;
        setBusy(true, 'Sending reply...');
        try {
            const pendingDraft = draft;
            const reviewedText = text.value;
            const result = await api(`${endpoint}/${draft.id}/approve`, { method: 'POST', body: { approved: true, text: reviewedText } });
            if (result.status !== 'sent') throw new Error('Send was not confirmed. Check the original conversation before sending again.');
            if (toast && (!result.already_sent || result.text === reviewedText)) {
                dialog.close();
                toast('Reply sent', 'success');
                return;
            }
            draft = { ...pendingDraft, ...result, text: result.text ?? reviewedText };
            text.value = draft.text;
            showDestination(draft.target);
            if (result.already_sent && reviewedText !== draft.text) {
                unsentEdits.hidden = false;
                unsentEdits.open = true;
                unsentEdits.querySelector('textarea').value = reviewedText;
            }
            retained.set(draft.source_key, draft);
            sending = false;
            setBusy(false, result.already_sent && reviewedText !== draft.text
                ? 'This reply was already sent. Your newer wording is under Your unsent edits.'
                : 'Previously sent.');
            sourceSelect.disabled = true;
        } catch (error) {
            // A lost response might follow a successful send. Never invite a blind resend.
            const notSent = error.data?.code === 'reply_not_sent' && error.data?.send_attempted === false
                && error.data?.draft_status === 'draft';
            sourceChanged = notSent && error.data.reason === 'source_changed';
            if (notSent && error.data.reason === 'rewrite_required') draft.rewrite_required = true;
            draft.status = notSent ? 'draft' : 'unconfirmed';
            draft.text = text.value;
            retained.set(draft.source_key, draft);
            sending = false;
            setBusy(false, error.message || 'Send was not confirmed. Check the original conversation before sending again.');
            sourceSelect.disabled = !notSent || sourceChanged;
            if (sourceChanged) close.focus();
        }
    });
    dialog.showModal();
    setBusy(true, 'Loading proposal sources...');
    try {
        const result = await api(`${endpoint}?response_id=${encodeURIComponent(responseId)}`);
        if (closed) return;
        const sources = result.sources || [];
        for (const item of result.drafts || []) retained.set(item.source_key, item);
        if (!sources.length) throw new Error('No original source is available for this proposal.');
        sourceSelect.replaceChildren(new Option('Choose a recipient…', ''));
        for (const source of sources) {
            const label = source.label === source.thread_id ? 'Original conversation' : source.label;
            sourceSelect.add(new Option(label, source.key));
        }
        setBusy(false);
        if (sources.length === 1) {
            sourceSelect.value = sources[0].key;
            if (retained.has(sourceSelect.value)) showDraft(retained.get(sourceSelect.value));
            else await generate();
        }
    } catch (error) { setBusy(false, error.message); }
}
