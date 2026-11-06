// Plan chat header (N3 items 9-11): chat switcher, new / compact / clear, and image drop rules.
// Pure helpers are exported for tests; the DOM wiring lives in conversation.js.

export const CHAT_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
export const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024;      // matches assets.MAX_IMAGE_BYTES
export const CHAT_MAX_ATTACHMENTS = 8;                      // matches conversation.MAX_ATTACHMENTS
const KEY = (workspaceId) => `decisions.plan.chat.${workspaceId}`;

export function rememberedChat(store, workspaceId) {
    try { const value = Number(store?.getItem(KEY(workspaceId))); return Number.isInteger(value) && value > 0 ? value : null; }
    catch { return null; }
}
export function rememberChat(store, workspaceId, chatId) {
    try { if (chatId) store?.setItem(KEY(workspaceId), String(chatId)); else store?.removeItem(KEY(workspaceId)); } catch { /* private mode */ }
}
export function chatStore() {
    try { return globalThis.localStorage || null; } catch { return null; }
}

// Remembered chat if it still exists and is live, else the server's default (newest live chat).
export function pickActiveChat(chats, remembered, serverDefault) {
    const live = (chats || []).filter((chat) => !chat.archived);
    if (remembered && live.some((chat) => chat.id === remembered)) return remembered;
    if (serverDefault && (chats || []).some((chat) => chat.id === serverDefault)) return serverDefault;
    return live.length ? live[live.length - 1].id : null;
}

// Images only; the "+" button remains the way to attach PDFs/text. Never trusts the extension alone:
// the server re-checks magic bytes.
export function validateImageFiles(files, existingCount = 0) {
    const accepted = [];
    const rejected = [];
    for (const file of files || []) {
        if (!CHAT_IMAGE_TYPES.includes(String(file.type || '').toLowerCase())) rejected.push({ file, reason: 'type' });
        else if (!(file.size > 0) || file.size > CHAT_IMAGE_MAX_BYTES) rejected.push({ file, reason: 'size' });
        else if (existingCount + accepted.length >= CHAT_MAX_ATTACHMENTS) rejected.push({ file, reason: 'count' });
        else accepted.push(file);
    }
    return { accepted, rejected };
}
export function rejectionMessage(rejected) {
    if (!rejected.length) return '';
    const reasons = new Set(rejected.map((entry) => entry.reason));
    if (reasons.has('type')) return 'Only PNG, JPEG or WebP images can be dropped or pasted here. Use + for other files.';
    if (reasons.has('size')) return 'Images must be smaller than 10 MB.';
    return `Use at most ${CHAT_MAX_ATTACHMENTS} attachments in one message.`;
}

export const isImageAsset = (asset) => CHAT_IMAGE_TYPES.includes(String(asset?.mime_type || '').toLowerCase());
// The label the model sees for the same asset (assets.context_assets): the real asset id, never a renumbered index.
export const imageLabel = (asset) => `Image ${asset.id}`;

export function chatHeaderHtml(session, esc) {
    const chats = session.conversation.chats || [];
    const current = chats.find((chat) => chat.id === session.chatId) || session.conversation.chat || null;
    const title = current?.title || 'New chat';
    const live = chats.filter((chat) => !chat.archived);
    const archived = chats.filter((chat) => chat.archived);
    const row = (chat) => `<button type="button" role="menuitemradio" aria-checked="${chat.id === session.chatId}" data-chat-switch="${esc(chat.id)}"><span class="plan-chat-menu-title">${esc(chat.title || 'Chat')}</span><small>${esc(chat.messages || 0)}</small></button>`;
    return `<button type="button" class="plan-chat-switch" id="plan-chat-switch" aria-haspopup="menu" aria-expanded="false" aria-controls="plan-chat-menu" title="Switch chat"><span class="plan-chat-title">${esc(title)}</span><span aria-hidden="true" class="plan-chat-caret">▾</span></button>
        <div class="board-context-menu plan-chat-menu hidden" id="plan-chat-menu" role="menu" aria-label="Chats">
            ${live.map(row).join('') || '<p class="plan-chat-menu-empty">No chats yet</p>'}
            <hr>
            <button type="button" role="menuitem" id="plan-chat-rename"${current ? '' : ' disabled'}>Rename chat</button>
            <button type="button" role="menuitem" id="plan-chat-memory"${current ? '' : ' disabled'}>Memory: ${current?.memory ? 'on' : 'off'}…</button>
            <button type="button" role="menuitem" id="plan-chat-archive"${current && !current.archived ? '' : ' disabled'}>Archive chat</button>
            ${archived.length ? `<p class="plan-chat-menu-label">Archived</p>${archived.map((chat) => `<button type="button" role="menuitem" data-chat-restore="${esc(chat.id)}"><span class="plan-chat-menu-title">${esc(chat.title || 'Chat')}</span><small>Restore</small></button>`).join('')}` : ''}
        </div>
        <span class="plan-chat-actions" role="group" aria-label="Chat actions">
            <button type="button" class="icon-button" id="plan-chat-new" title="New chat" aria-label="New chat">+</button>
            <button type="button" class="icon-button" id="plan-chat-compact" title="Compact: summarise earlier turns" aria-label="Compact chat"${current ? '' : ' disabled'}>⇣</button>
            <button type="button" class="icon-button" id="plan-chat-clear" title="Clear: archive this chat and start fresh" aria-label="Clear chat"${current ? '' : ' disabled'}>⌫</button>
        </span>`;
}
