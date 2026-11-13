// One selected source, one editable draft, one explicit send approval.
// With `mount`, the same flow renders as the call-out under an Incoming thread's final reply;
// without it, Message Client opens as a modal.
export async function openSourceReply({ chatId, responseId, api, opener, onClose, toast, mount }) {
    const inline = Boolean(mount);
    const titleId = inline ? `client-reply-callout-title-${responseId}` : 'source-reply-title';
    const idSuffix = inline ? `-callout-${responseId}` : '';
    const dialog = document.createElement(inline ? 'section' : 'dialog');
    dialog.className = inline ? 'client-reply-callout source-reply-dialog' : 'studio-dialog source-reply-dialog';
    dialog.setAttribute('aria-labelledby', titleId);
    dialog.innerHTML = `<form method="dialog">
      <header><h2 id="${titleId}">${inline ? 'Ready to send this?' : 'Message Client'}</h2><button type="button" data-close aria-label="${inline ? 'Not now' : 'Close message'}">${inline ? 'Not now' : '×'}</button></header>
      <div class="source-reply-content">
        <label for="source-reply-source${idSuffix}">To:</label><div class="source-reply-recipient"><select id="source-reply-source${idSuffix}"></select></div>
        <section data-draft-fields hidden><div class="source-reply-destination" aria-live="polite"></div>
        <label for="source-reply-text${idSuffix}">Reply</label><textarea id="source-reply-text${idSuffix}" rows="9" disabled></textarea>
        <details data-unsent-edits hidden><summary>Your unsent edits (not sent)</summary><textarea aria-label="Your unsent edits" rows="3" readonly></textarea></details>
        <label for="source-reply-instruction${idSuffix}">Rewrite guidance (optional)</label><input id="source-reply-instruction${idSuffix}" type="text" placeholder="For example: shorter, or explain the next step"></section>
        <p class="source-reply-status" role="status" aria-live="polite" tabindex="-1"></p>
        <button type="button" data-check-status class="secondary-button source-reply-check-status" hidden>Check send status</button>
      </div>
      <footer><button type="button" data-rewrite class="secondary-button" disabled>Rewrite</button><button type="button" data-approve class="primary-button" disabled>${inline ? 'Approve and send' : 'Approve'}</button></footer>
    </form>`;
    if (inline) mount.replaceChildren(dialog);
    else document.body.append(dialog);
    const finish = () => {
        if (!inline) return dialog.close();
        closed = true; dialog.remove(); onClose?.();
    };
    const sourceSelect = dialog.querySelector('select');
    const text = dialog.querySelector('textarea');
    const unsentEdits = dialog.querySelector('[data-unsent-edits]');
    const unsentText = unsentEdits.querySelector('textarea');
    const instruction = dialog.querySelector('input');
    const status = dialog.querySelector('[role="status"]');
    const destination = dialog.querySelector('.source-reply-destination');
    const fields = dialog.querySelector('[data-draft-fields]');
    const approve = dialog.querySelector('[data-approve]');
    const rewrite = dialog.querySelector('[data-rewrite]');
    const close = dialog.querySelector('[data-close]');
    const checkStatus = dialog.querySelector('[data-check-status]');
    const endpoint = `/workflows/studio/tasks/${chatId}/source-replies`;
    // Wording the agent drafted for the client seeds the first Message Client draft.
    const agentDraft = (opener || mount)?.closest?.('[data-client-draft]')?.dataset.clientDraft || '';
    let draft = null;
    let busy = false;
    let sending = false;
    let closed = false;
    let sourceChanged = false;
    const retained = new Map();
    const historyBlocks = new Map();
    const selectedBlock = () => historyBlocks.get(sourceSelect.value);
    function historyUnavailable(error) {
        if (error.data?.code !== 'reply_history_unavailable') return false;
        if (error.data.source_key) {
            historyBlocks.set(error.data.source_key, error.message);
            return true;
        }
        sourceChanged = true;
        if (!sourceSelect.options.length) {
            sourceSelect.hidden = true;
            sourceSelect.labels[0].hidden = true;
        }
        return true;
    }
    function setBusy(value, message = '') {
        busy = value;
        status.textContent = message;
        const blocked = sourceChanged || Boolean(selectedBlock());
        sourceSelect.disabled = value || sourceChanged;
        text.disabled = value || !draft;
        text.readOnly = blocked || !draft || draft.status !== 'draft';
        instruction.disabled = value || blocked || (draft && draft.status !== 'draft');
        approve.disabled = value || text.readOnly || draft?.rewrite_required || !text.value.trim();
        rewrite.disabled = value || blocked || !sourceSelect.value || (draft && draft.status !== 'draft');
        close.disabled = value && sending;
        checkStatus.hidden = blocked || !['sending', 'unconfirmed'].includes(draft?.status);
        checkStatus.disabled = value;
    }
    function showUnsentEdits(value = '') {
        unsentText.value = value;
        unsentEdits.open = Boolean(value);
        unsentEdits.hidden = !value;
    }
    function showDraft(nextDraft) {
        draft = nextDraft;
        retained.set(draft.source_key, draft);
        fields.hidden = false;
        text.value = draft.text;
        showUnsentEdits();
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
        const lines = target.channel === 'telegram' ? [target.topic_label
            ? `Telegram topic: ${target.topic_label}` : `Telegram group: ${target.label || target.thread_id}`]
            : [`${target.channel === 'gmail' ? 'Gmail' : 'Mailshot'}: Reply all`, ...(target.from ? [`From: ${target.from}`] : []), `To: ${target.to.join(', ')}`,
               ...(target.cc.length ? [`Cc: ${target.cc.join(', ')}`] : []), `Subject: ${target.subject}`];
        for (const line of lines) {
            const paragraph = document.createElement('p');
            paragraph.textContent = line;
            destination.append(paragraph);
        }
    }
    async function generate() {
        if (busy || sourceChanged || selectedBlock() || !sourceSelect.value) return;
        fields.hidden = false;
        const previous = draft;
        setBusy(true, previous ? 'Rewriting reply...' : 'Drafting reply...');
        try {
            const nextDraft = await api(endpoint, { method: 'POST', body: {
                response_id: responseId, source_key: sourceSelect.value,
                previous: text.value || (previous ? '' : agentDraft), instruction: instruction.value, replaces: previous?.id || ''
            }});
            // A late response can refer to a draft already open in another window.
            if (closed) return;
            showDraft(nextDraft);
            text.focus();
        } catch (error) {
            if (closed) return;
            draft = previous;
            const blocked = historyUnavailable(error);
            setBusy(false, error.message || 'Could not draft the reply. Please retry.');
            if (blocked) status.focus();
        }
    }
    async function dismiss() {
        if (sending) return;
        // The call-out only steps aside; its draft stays for Message Client.
        if (inline) return finish();
        if (!sourceChanged && !selectedBlock() && draft?.status === 'draft') {
            setBusy(true, 'Cancelling reply...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) {
                if (!historyUnavailable(error)) {
                    setBusy(false, error.message || 'Could not cancel. Please retry.');
                    return;
                }
            }
        }
        closed = true;
        finish();
    }
    if (!inline) dialog.addEventListener('close', () => { closed = true; dialog.remove(); opener?.focus(); onClose?.(); }, { once: true });
    if (!inline) dialog.addEventListener('cancel', (event) => { event.preventDefault(); dismiss(); });
    if (!inline) dialog.addEventListener('click', (event) => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dismiss();
    });
    close.addEventListener('click', dismiss);
    dialog.querySelector('form').addEventListener('submit', (event) => event.preventDefault());
    function openSelection() {
        const key = sourceSelect.value;
        const reason = selectedBlock();
        fields.hidden = !key || (Boolean(reason) && !retained.has(key));
        if (retained.has(key)) {
            showDraft(retained.get(key));
            if (!reason) return;
        }
        if (reason) {
            setBusy(false, reason);
            status.focus();
        } else {
            return generate();
        }
    }
    sourceSelect.addEventListener('change', async () => {
        if (draft?.status === 'draft' && !historyBlocks.has(draft.source_key)) {
            setBusy(true, 'Changing source...');
            try { await api(`${endpoint}/${draft.id}/cancel`, { method: 'POST', body: {} }); }
            catch (error) {
                sourceSelect.value = draft.source_key;
                const blocked = historyUnavailable(error);
                setBusy(false, error.message);
                if (blocked) status.focus();
                return;
            }
            retained.delete(draft.source_key);
        }
        draft = null; text.value = ''; showUnsentEdits(); destination.replaceChildren(); setBusy(false);
        await openSelection();
    });
    text.addEventListener('input', () => { approve.disabled = busy || text.readOnly || draft?.rewrite_required || !text.value.trim(); });
    rewrite.addEventListener('click', generate);
    checkStatus.addEventListener('click', async () => {
        if (busy || checkStatus.hidden || !draft) return;
        const pending = draft;
        const reviewedText = unsentEdits.hidden ? text.value : unsentText.value;
        setBusy(true, 'Checking send status...');
        try {
            const result = await api(endpoint + '?response_id=' + encodeURIComponent(responseId));
            if (closed) return;
            const reason = (result.sources || []).find(source => source.key === pending.source_key)?.reply_blocked_reason;
            if (reason) {
                historyBlocks.set(pending.source_key, reason);
                setBusy(false, reason);
                status.focus();
                return;
            }
            const latest = (result.drafts || []).find(item =>
                item.id === pending.id && item.response_id === responseId && item.source_key === pending.source_key);
            if (!latest || !['draft', 'sending', 'unconfirmed', 'sent'].includes(latest.status)) {
                setBusy(false, 'This draft is no longer available. Reopen Message Client to review the current sources.');
                checkStatus.focus();
                return;
            }
            showDraft(latest.status === 'draft' ? { ...latest, text: reviewedText } : latest);
            if (latest.status !== 'draft' && reviewedText !== latest.text) {
                showUnsentEdits(reviewedText);
                if (latest.status === 'sent') status.textContent = 'This reply was already sent. Your newer wording is under Your unsent edits.';
            } else if (latest.status === 'draft' && !latest.rewrite_required) {
                status.textContent = 'The desktop has no send recorded for this draft. Review the message before approving again.';
            }
            if (latest.status === 'sent') status.focus();
            else if (checkStatus.hidden) text.focus();
            else checkStatus.focus();
        } catch (error) {
            if (closed) return;
            const blocked = historyUnavailable(error);
            setBusy(false, error.message || 'Could not check send status. Try again.');
            if (blocked) status.focus();
            else checkStatus.focus();
        }
    });
    approve.addEventListener('click', async () => {
        if (busy || text.readOnly || !draft || draft.rewrite_required || !text.value.trim()) return;
        sending = true;
        setBusy(true, 'Sending reply...');
        try {
            const pendingDraft = draft;
            const reviewedText = text.value;
            const result = await api(`${endpoint}/${draft.id}/approve`, { method: 'POST', body: { approved: true, text: reviewedText } });
            if (result.status === 'sending') {
                sending = false;
                showDraft(result);
                checkStatus.focus();
                return;
            }
            if (result.status !== 'sent') throw new Error('Send was not confirmed. Check the original conversation before sending again.');
            if (toast && (!result.already_sent || result.text === reviewedText)) {
                finish();
                toast('Reply sent', 'success');
                return;
            }
            draft = { ...pendingDraft, ...result, text: result.text ?? reviewedText };
            text.value = draft.text;
            showDestination(draft.target);
            if (result.already_sent && reviewedText !== draft.text) {
                showUnsentEdits(reviewedText);
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
            const blocked = historyUnavailable(error);
            sourceChanged ||= notSent && error.data.reason === 'source_changed';
            if (notSent && error.data.reason === 'rewrite_required') draft.rewrite_required = true;
            draft.status = notSent ? 'draft' : 'unconfirmed';
            draft.text = text.value;
            retained.set(draft.source_key, draft);
            sending = false;
            setBusy(false, error.message || 'Send was not confirmed. Check the original conversation before sending again.');
            sourceSelect.disabled = sourceChanged || (!notSent && !selectedBlock());
            if (blocked) status.focus();
            else if (sourceChanged) close.focus();
        }
    });
    if (!inline) dialog.showModal();
    setBusy(true, 'Loading proposal sources...');
    try {
        const result = await api(`${endpoint}?response_id=${encodeURIComponent(responseId)}`);
        if (closed) return;
        const sources = result.sources || [];
        for (const item of result.drafts || []) retained.set(item.source_key, item);
        if (!sources.length) throw new Error('No original source is available for this proposal.');
        sourceSelect.replaceChildren(new Option('Choose a recipient…', ''));
        const channels = { whatsapp: 'WhatsApp', gmail: 'Gmail', mailshot: 'Mailshot', telegram: 'Telegram' };
        const labels = sources.map(source => source.label.trim().toLowerCase());
        for (const [index, source] of sources.entries()) {
            let label = source.label;
            if (source.label === source.thread_id) {
                label = sources.length === 1 ? 'Original conversation' : `${channels[source.channel]} · ${source.thread_id}`;
            } else if (sources.some((_, other) => other !== index && labels[other] === labels[index])) {
                label = `${channels[source.channel]} · ${source.thread_id} · ${source.label}`;
            }
            if (source.reply_blocked_reason) historyBlocks.set(source.key, source.reply_blocked_reason);
            sourceSelect.add(new Option(source.reply_blocked_reason ? `${label} (blocked)` : label, source.key));
        }
        setBusy(false);
        if (sources.length === 1) {
            sourceSelect.value = sources[0].key;
            await openSelection();
        }
    } catch (error) {
        if (closed) return;
        const blocked = historyUnavailable(error);
        setBusy(false, error.message);
        if (blocked) status.focus();
    }
}
