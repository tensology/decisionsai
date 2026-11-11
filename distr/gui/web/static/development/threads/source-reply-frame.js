window.addEventListener('message', function connectSourceReply(event) {
    if (event.source !== parent || event.data?.type !== 'decisions-source-reply' || event.ports.length !== 1) return;
    window.removeEventListener('message', connectSourceReply);
    const port = event.ports[0];
    const pending = new Map();
    let sequence = 0;
    port.onmessage = ({ data }) => {
        const task = pending.get(data?.id);
        if (!task) return;
        pending.delete(data.id);
        if (data.error) task.reject(Object.assign(new Error(data.error.message), { data: data.error.data }));
        else task.resolve(data.result);
    };
    const api = (path, options) => new Promise((resolve, reject) => {
        const id = ++sequence;
        pending.set(id, { resolve, reject });
        port.postMessage({ id, path, options });
    });
    openSourceReply({
        chatId: event.data.chatId, responseId: event.data.responseId, api,
        onClose: () => port.postMessage({ type: 'close' }),
        toast: (_, kind) => { if (kind === 'success') port.postMessage({ type: 'sent' }); },
    });
    port.postMessage({ type: 'ready' });
});
