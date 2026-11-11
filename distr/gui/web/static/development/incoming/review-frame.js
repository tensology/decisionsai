// Runs inside the sandboxed proposal document. All desktop access is mediated by App View.
const el = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const formatDateTime = value => {
    const date = new Date(value);
    return value && !Number.isNaN(date.getTime())
        ? date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
};

window.addEventListener('message', function connect(event) {
    if (event.source !== parent || event.data?.type !== 'decisions-proposal-review' || event.ports.length !== 1) return;
    window.removeEventListener('message', connect);
    const port = event.ports[0];
    const pending = new Map();
    let requestId = 0;
    const request = (type, data) => new Promise((resolve, reject) => {
        const id = ++requestId;
        pending.set(id, { resolve, reject });
        port.postMessage({ type, id, ...data });
    });
    port.onmessage = ({ data }) => {
        const task = pending.get(data?.id);
        if (!task) return;
        pending.delete(data.id);
        if (data.error) task.reject(Object.assign(new Error(data.error), { data: { error: data.error } }));
        else task.resolve(data.result);
    };
    const incoming = createIncoming({
        context: { workspaceMode: 'incoming', embeddedProposalReview: true },
        shared: { fileDropZoneHtml, proposalThreadToast },
        el,
        actions: {
            escapeHtml,
            formatDateTime,
            api: (path, options) => request('api', { path, options }),
            readProposalAttachment: path => request('attachment', { path }),
            openProposalSource: sourceKey => request('source', { sourceKey }),
            closeProposalReview: () => port.postMessage({ type: 'close' }),
            loadChat: chatId => port.postMessage({ type: 'thread', chatId }),
            refreshShell: () => {},
            toast: (message, kind) => { if (kind === 'error') port.postMessage({ type: 'error', message }); },
        },
    });
    incoming.mountProposalReview(event.data.proposal);
    if (event.data.initialTab === 'source') {
        const tab = document.querySelector('[data-proposal-tab="source"]');
        tab?.click();
        tab?.focus();
    }
    port.postMessage({ type: 'ready' });
});
