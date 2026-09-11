// Owns the selected thread socket, reconnect delay and coalesced refresh timer.
export function createThreadLiveUpdates({ token, isActive, refresh }) {
    let socket = null,
        reconnect = null,
        refreshTimer = null;
    function disconnect() {
        clearTimeout(reconnect);
        clearTimeout(refreshTimer);
        reconnect = refreshTimer = null;
        if (socket) {
            socket.onclose = null;
            socket.close();
            socket = null;
        }
    }
    function connect(chatId) {
        const id = Number(chatId);
        if (!id || !isActive(id)) return;
        if (socket?.chatId === id && socket.readyState <= WebSocket.OPEN) return;
        disconnect();
        const query = new URLSearchParams({ chat_id: String(id) });
        if (token) query.set('internal_token', token);
        const current = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/ws?${query}`);
        current.chatId = id;
        socket = current;
        current.onopen = () => {
            if (socket === current && isActive(id)) current.send(JSON.stringify({ subscribe: id }));
        };
        current.onmessage = (event) => {
            if (socket !== current || !isActive(id)) return;
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'pong' || Number(message.chat_id || id) !== id) return;
                clearTimeout(refreshTimer);
                refreshTimer = setTimeout(() => {
                    if (isActive(id)) refresh();
                }, 120);
            } catch (_) {
                /* Recover malformed transport messages through the next durable refresh. */
            }
        };
        current.onclose = () => {
            if (socket !== current) return;
            socket = null;
            if (isActive(id)) reconnect = setTimeout(() => connect(id), 1800);
        };
    }
    return { connect, disconnect };
}
