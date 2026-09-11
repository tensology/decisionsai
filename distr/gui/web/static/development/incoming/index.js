// Owns incoming rendering, interactions, and private view state.
export function createIncoming({ context, actions, el }) {
    const state = {
        incoming: [],
        incomingLinks: [],
        incomingChannels: {},
        incomingError: '',
        incomingLoad: null,
        incomingLoadedAt: 0,
        incomingFilter: 'whatsapp',
        selectedIncomingConversationKey: '',
        incomingLinkConversationKey: '',
        selectedGmailThreadKey: '',
        selectedMailshotThreadKey: ''
    };
    function incomingBoardOptions(selectedId) {
        const localBoards = context.boards.filter((board) => board.provider === 'decisions');
        return (
            '<option value="">Choose board</option>' +
            localBoards
                .map(
                    (board) =>
                        `<option value="${Number(board.local_id || board.id)}"${Number(selectedId) === Number(board.local_id || board.id) ? ' selected' : ''}>${actions.escapeHtml(board.name || 'Board')}</option>`
                )
                .join('')
        );
    }

    function incomingConversations(sourceFilter = state.incomingFilter) {
        const grouped = new Map();
        state.incoming.forEach((item) => {
            const source = String(item.source || 'unknown').toLowerCase();
            if (!['whatsapp', 'gmail', 'mailshot'].includes(source)) return;
            const threadId = String(item.source_thread_id || item.sender || item.key || 'unknown');
            const key = `${source}:${threadId}`;
            if (!grouped.has(key)) grouped.set(key, { key, source, threadId, messages: [] });
            grouped.get(key).messages.push(item);
        });
        if (!sourceFilter || sourceFilter === 'whatsapp') {
            state.incomingLinks
                .filter((link) => link.source === 'whatsapp')
                .forEach((link) => {
                    const threadId = String(link.source_thread_id || link.source_address || 'unknown');
                    const key = `whatsapp:${threadId}`;
                    if (!grouped.has(key))
                        grouped.set(key, {
                            key,
                            source: 'whatsapp',
                            threadId,
                            messages: [],
                            syntheticLinks: []
                        });
                    const conversation = grouped.get(key);
                    conversation.syntheticLinks = conversation.syntheticLinks || [];
                    conversation.syntheticLinks.push({
                        id: link.id,
                        board_id: link.board_id,
                        board_name: link.board_name,
                        auto_snapshot: link.auto_snapshot,
                        label: link.label
                    });
                });
        }
        return Array.from(grouped.values())
            .map((conversation) => {
                conversation.messages.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
                const latest = conversation.messages[0] || {};
                const link = conversation.messages.find((item) => item.link_id || item.board_id || item.links?.length) || latest;
                conversation.latest = latest;
                conversation.label =
                    link.conversation_label ||
                    latest.conversation_label ||
                    conversation.syntheticLinks?.[0]?.label ||
                    latest.thread_label ||
                    latest.sender ||
                    conversation.threadId;
                conversation.chatType = link.chat_type || latest.chat_type || 'thread';
                const allLinks = (conversation.syntheticLinks || []).concat(
                    conversation.messages.flatMap((item) =>
                        Array.isArray(item.links)
                            ? item.links
                            : item.link_id
                              ? [
                                    {
                                        id: item.link_id,
                                        board_id: item.board_id,
                                        board_name: item.board_name,
                                        auto_snapshot: item.auto_snapshot
                                    }
                                ]
                              : []
                    )
                );
                conversation.links = Array.from(new Map(allLinks.filter((item) => item?.id).map((item) => [Number(item.id), item])).values());
                conversation.boardId = Number(conversation.links[0]?.board_id || link.board_id || 0);
                conversation.boardName = conversation.links[0]?.board_name || link.board_name || '';
                conversation.linkId = Number(conversation.links[0]?.id || link.link_id || 0);
                conversation.pendingCount = conversation.messages.filter((item) => !item.processed && !item.from_me).length;
                conversation.canSnapshot = conversation.messages.some((item) => !item.processed && !item.from_me);
                conversation.hasSnapshot = conversation.messages.some((item) => item.snapshotted);
                return conversation;
            })
            .filter((conversation) => !sourceFilter || conversation.source === sourceFilter);
    }

    function incomingInitials(label) {
        return (
            String(label || '?')
                .split(/\s+/)
                .filter(Boolean)
                .slice(0, 2)
                .map((part) => part[0])
                .join('')
                .toUpperCase() || '?'
        );
    }

    function incomingLinkPillsHtml(links) {
        return (links || []).map((link) => `<span class="incoming-board-pill">${actions.escapeHtml(link.board_name || 'Board')}</span>`).join('');
    }

    function incomingConversationRowHtml(conversation) {
        const latest = conversation.latest || {};
        const selected = state.selectedIncomingConversationKey === conversation.key;
        const direction = latest.from_me ? 'You: ' : '';
        return `<button type="button" class="incoming-conversation-row${conversation.pendingCount ? ' pending' : ''}${selected ? ' selected' : ''}" data-incoming-conversation="${actions.escapeHtml(conversation.key)}" aria-pressed="${selected}"><span class="incoming-avatar" aria-hidden="true">${actions.escapeHtml(incomingInitials(conversation.label))}</span><span class="incoming-row-copy"><span class="incoming-row-title"><strong>${actions.escapeHtml(conversation.label)}</strong><time>${actions.escapeHtml(actions.formatDateTime(latest.created_at))}</time></span><span class="incoming-row-preview">${actions.escapeHtml(direction + (latest.text || (latest.media_type ? `${latest.media_type} attachment` : 'No message text')))}</span><span class="incoming-row-meta">${incomingLinkPillsHtml(conversation.links)}${conversation.pendingCount ? `<b>${conversation.pendingCount} new</b>` : ''}</span></span><span class="incoming-row-more" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}" title="View information or link to a board">•••</span></button>`;
    }

    function incomingMediaHtml(item) {
        if (!item.media_type || !item.database_id) return '';
        const url = `/api/tickets/whatsapp/relay-media/${Number(item.database_id)}?wa_key=${encodeURIComponent(item.source_message_id || '')}`;
        const type = String(item.media_type || '').toLowerCase();
        const mime = String(item.media_mime_type || '').toLowerCase();
        if (type === 'image' || type === 'photo' || mime.startsWith('image/'))
            return `<a href="${url}" target="_blank" rel="noopener"><img class="incoming-media-image" src="${url}" alt="${actions.escapeHtml(item.media_filename || 'WhatsApp image')}" loading="lazy"></a>`;
        if (['audio', 'voice', 'ptt'].includes(type) || mime.startsWith('audio/'))
            return `<audio class="incoming-media-audio" controls preload="none" src="${url}&format=m4a"></audio>`;
        if (type === 'video' || mime.startsWith('video/')) return `<video class="incoming-media-video" controls preload="metadata" src="${url}"></video>`;
        return `<a class="incoming-media-file" href="${url}" target="_blank" rel="noopener">Open ${actions.escapeHtml(item.media_filename || `${type || 'file'} attachment`)}</a>`;
    }

    function incomingWhatsAppReaderHtml(conversation) {
        if (!conversation)
            return '<div class="incoming-reader-empty"><strong>Select a conversation</strong><p>Linked work channels are kept at the top.</p></div>';
        const messages = conversation.messages
            .slice()
            .reverse()
            .slice(-100)
            .map(
                (item) =>
                    `<div class="incoming-message-bubble${item.from_me ? ' outgoing' : ' incoming'}"><small>${actions.escapeHtml(item.from_me ? 'You' : item.sender || conversation.label)}</small>${incomingMediaHtml(item)}${item.text ? `<p>${actions.escapeHtml(item.text)}</p>` : ''}<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></div>`
            )
            .join('');
        const snapshotActions = conversation.links
            .map(
                (link) =>
                    `<button type="button" data-incoming-snapshot="${Number(link.id)}" data-board-id="${Number(link.board_id)}"${conversation.canSnapshot ? '' : ' disabled'}>Snapshot to ${actions.escapeHtml(link.board_name || 'board')}</button>`
            )
            .join('');
        const snapshotHelp = conversation.hasSnapshot ? 'Includes messages since the previous snapshot.' : 'The first snapshot includes the last 2 days.';
        return `<header class="incoming-whatsapp-reader-header"><span class="incoming-avatar large" aria-hidden="true">${actions.escapeHtml(incomingInitials(conversation.label))}</span><div><h3>${actions.escapeHtml(conversation.label)}</h3><p>${conversation.chatType === 'group' ? 'WhatsApp group' : 'WhatsApp contact'} · ${conversation.messages.length} recent messages</p><div class="incoming-reader-links">${incomingLinkPillsHtml(conversation.links)}</div></div><button type="button" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}">•••</button></header><div class="incoming-whatsapp-messages">${messages}</div><footer><small>${conversation.links.length ? actions.escapeHtml(snapshotHelp) : 'This conversation is not linked to work.'}</small><span>${snapshotActions}</span></footer>`;
    }

    function scrollIncomingWhatsAppToLatest() {
        const messages = el('incoming-whatsapp-reader')?.querySelector('.incoming-whatsapp-messages');
        if (!messages) return;
        const scroll = () => {
            messages.scrollTop = messages.scrollHeight;
        };
        window.requestAnimationFrame(scroll);
        messages.querySelectorAll('img, video').forEach((media) => {
            media.addEventListener(media.tagName === 'VIDEO' ? 'loadedmetadata' : 'load', scroll, { once: true });
        });
    }

    function mailThreadButtonHtml(conversation, selectedKey, dataAttribute) {
        const latest = conversation.latest || {};
        const preview = latest.snippet || latest.text || 'No message text';
        return `<button type="button" class="incoming-gmail-thread${conversation.pendingCount ? ' unread' : ''}${selectedKey === conversation.key ? ' selected' : ''}" ${dataAttribute}="${actions.escapeHtml(conversation.key)}"><span><strong>${actions.escapeHtml(conversation.label || '(No subject)')}</strong><time>${actions.escapeHtml(actions.formatDateTime(latest.created_at))}</time></span><small>${actions.escapeHtml(latest.sender || 'Unknown sender')}</small><p>${actions.escapeHtml(preview)}</p></button>`;
    }

    function gmailReaderHtml(conversation) {
        if (!conversation)
            return '<div class="incoming-reader-empty"><strong>Select a Gmail thread</strong><p>Choose an inbox thread on the left to read it here.</p></div>';
        const latest = conversation.latest || {};
        let actionHtml = '';
        if (latest.ticket_id) {
            actionHtml = `<button type="button" data-incoming-open-ticket="${Number(latest.ticket_id)}" data-board-id="${Number(latest.board_id || 0)}">Open ticket</button>`;
        } else {
            actionHtml = `<div class="incoming-link-controls"><select data-incoming-board aria-label="Choose ticket board">${incomingBoardOptions('')}</select><button type="button" data-incoming-create-ticket data-incoming-key="${actions.escapeHtml(latest.key || '')}">Create ticket</button></div>`;
        }
        const messages = conversation.messages
            .slice()
            .reverse()
            .map((item) => {
                const attachments =
                    Array.isArray(item.attachments) && item.attachments.length
                        ? `<small class="incoming-gmail-attachments">${item.attachments.length} attachment${item.attachments.length === 1 ? '' : 's'}: ${actions.escapeHtml(item.attachments.map((attachment) => attachment.filename || 'Attachment').join(', '))}</small>`
                        : '';
                return `<section class="incoming-gmail-message"><header><div><strong>${actions.escapeHtml(item.sender || 'Unknown sender')}</strong><small>${actions.escapeHtml(item.recipient ? `To ${item.recipient}` : '')}</small></div><time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></header><div class="incoming-gmail-body">${actions.escapeHtml(item.text || item.snippet || 'No message text')}</div>${attachments}</section>`;
            })
            .join('');
        return `<header class="incoming-gmail-reader-header"><div><h3>${actions.escapeHtml(conversation.label || '(No subject)')}</h3><p>${actions.escapeHtml(latest.sender || 'Unknown sender')} · ${conversation.messages.length} message${conversation.messages.length === 1 ? '' : 's'}</p></div></header><div class="incoming-gmail-messages">${messages}</div><footer>${actionHtml}</footer>`;
    }

    function bindIncomingActions(root) {
        if (!root) return;
        root.querySelectorAll('[data-incoming-snapshot]').forEach((button) =>
            button.addEventListener('click', () => snapshotIncomingWhatsApp(Number(button.dataset.boardId), Number(button.dataset.incomingSnapshot), button))
        );
        root.querySelectorAll('[data-incoming-manage]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                openIncomingLinkDialog(state.selectedIncomingConversationKey || button.closest('[data-incoming-conversation]')?.dataset.incomingConversation);
            })
        );
        root.querySelectorAll('[data-incoming-create-ticket]').forEach((button) => button.addEventListener('click', () => createIncomingTicket(button)));
        root.querySelectorAll('[data-incoming-dismiss]').forEach((button) =>
            button.addEventListener('click', () => resolveIncomingEvent(Number(button.dataset.incomingDismiss), 'dismiss'))
        );
        root.querySelectorAll('[data-incoming-open-ticket]').forEach((button) =>
            button.addEventListener('click', () => openIncomingTicket(Number(button.dataset.boardId), Number(button.dataset.incomingOpenTicket)))
        );
    }

    function renderIncomingWorkspace() {
        const list = el('incoming-list');
        if (!list) return;
        document.querySelectorAll('[data-incoming-filter]').forEach((button) => {
            const selected = button.dataset.incomingFilter === state.incomingFilter;
            button.classList.toggle('active', selected);
            button.setAttribute('aria-selected', String(selected));
            button.setAttribute('tabindex', selected ? '0' : '-1');
        });
        const whatsappConversations = incomingConversations('whatsapp');
        const gmailConversations = incomingConversations('gmail');
        const mailshotConversations = incomingConversations('mailshot');
        el('incoming-whatsapp-count').textContent = String(incomingConversations('whatsapp').length);
        el('incoming-gmail-count').textContent = String(gmailConversations.length);
        el('incoming-mailshot-count').textContent = String(mailshotConversations.length);
        el('incoming-panel-whatsapp').classList.toggle('hidden', state.incomingFilter !== 'whatsapp');
        el('incoming-panel-gmail').classList.toggle('hidden', state.incomingFilter !== 'gmail');
        el('incoming-panel-mailshot').classList.toggle('hidden', state.incomingFilter !== 'mailshot');
        if (!whatsappConversations.some((conversation) => conversation.key === state.selectedIncomingConversationKey)) {
            state.selectedIncomingConversationKey =
                whatsappConversations.find((conversation) => conversation.links.length)?.key || whatsappConversations[0]?.key || '';
        }
        const linkedConversations = whatsappConversations.filter((conversation) => conversation.links.length);
        const otherConversations = whatsappConversations.filter((conversation) => !conversation.links.length);
        list.innerHTML = state.incomingError
            ? `<div class="development-empty">${actions.escapeHtml(state.incomingError)}</div>`
            : whatsappConversations.length
              ? `${linkedConversations.length ? `<section class="incoming-channel-section"><header><h2>Linked work</h2><small>${linkedConversations.length}</small></header><div>${linkedConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}${otherConversations.length ? `<section class="incoming-channel-section other"><header><h2>Other conversations</h2><small>${otherConversations.length}</small></header><div>${otherConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}`
              : '<div class="development-empty">No WhatsApp conversations are available.</div>';
        list.querySelectorAll('[data-incoming-conversation]').forEach((button) => {
            button.addEventListener('click', () => {
                state.selectedIncomingConversationKey = button.dataset.incomingConversation || '';
                renderIncomingWorkspace();
            });
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                openIncomingLinkDialog(button.dataset.incomingConversation || '');
            });
        });
        bindIncomingActions(list);
        const selectedWhatsApp = whatsappConversations.find((conversation) => conversation.key === state.selectedIncomingConversationKey) || null;
        el('incoming-whatsapp-reader').innerHTML = incomingWhatsAppReaderHtml(selectedWhatsApp);
        bindIncomingActions(el('incoming-whatsapp-reader'));
        scrollIncomingWhatsAppToLatest();

        if (!gmailConversations.some((conversation) => conversation.key === state.selectedGmailThreadKey)) {
            state.selectedGmailThreadKey = gmailConversations[0]?.key || '';
        }
        const gmailList = el('incoming-gmail-list');
        const gmailStatus = state.incomingChannels.gmail || {};
        gmailList.innerHTML = state.incomingError
            ? `<div class="development-empty">${actions.escapeHtml(state.incomingError)}</div>`
            : gmailStatus.error
              ? `<div class="incoming-channel-error"><strong>${gmailStatus.connected ? 'Reconnect Gmail' : 'Connect Gmail'}</strong><p>${actions.escapeHtml(gmailStatus.error)}</p><button type="button" data-google-reconnect>${gmailStatus.connected ? 'Reconnect Google' : 'Connect Google'}</button></div>`
              : gmailConversations.length
                ? gmailConversations
                      .map((conversation) => mailThreadButtonHtml(conversation, state.selectedGmailThreadKey, 'data-incoming-gmail-thread'))
                      .join('')
                : '<div class="development-empty">No Gmail inbox threads are available.</div>';
        gmailList.querySelectorAll('[data-incoming-gmail-thread]').forEach((button) =>
            button.addEventListener('click', () => {
                state.selectedGmailThreadKey = button.dataset.incomingGmailThread || '';
                renderIncomingWorkspace();
            })
        );
        gmailList.querySelectorAll('[data-google-reconnect]').forEach((button) => button.addEventListener('click', reconnectGoogleFromIncoming));
        const selectedGmailThread = gmailConversations.find((conversation) => conversation.key === state.selectedGmailThreadKey) || null;
        el('incoming-gmail-reader').innerHTML = gmailStatus.error
            ? '<div class="incoming-reader-empty"><strong>Gmail is not available</strong><p>Reconnect the account to open and read inbox threads here.</p></div>'
            : gmailReaderHtml(selectedGmailThread);
        bindIncomingActions(el('incoming-gmail-reader'));

        const mailshotStatus = state.incomingChannels.mailshot || {};
        const mailshotTab = el('incoming-folder-mailshot');
        mailshotTab.classList.remove('hidden');
        if (!mailshotConversations.some((conversation) => conversation.key === state.selectedMailshotThreadKey)) {
            state.selectedMailshotThreadKey = mailshotConversations[0]?.key || '';
        }
        const mailshotList = el('incoming-mailshot-list');
        mailshotList.innerHTML = mailshotStatus.error
            ? `<div class="incoming-channel-error"><strong>Mailshot needs attention</strong><p>${actions.escapeHtml(mailshotStatus.error)}</p><a href="/settings#advanced">Open Settings</a></div>`
            : mailshotConversations.length
              ? mailshotConversations
                    .map((conversation) => mailThreadButtonHtml(conversation, state.selectedMailshotThreadKey, 'data-incoming-mailshot-thread'))
                    .join('')
              : '<div class="development-empty">No Tensology Mailshot messages are available.</div>';
        mailshotList.querySelectorAll('[data-incoming-mailshot-thread]').forEach((button) =>
            button.addEventListener('click', () => {
                state.selectedMailshotThreadKey = button.dataset.incomingMailshotThread || '';
                renderIncomingWorkspace();
            })
        );
        const selectedMailshotThread = mailshotConversations.find((conversation) => conversation.key === state.selectedMailshotThreadKey) || null;
        el('incoming-mailshot-reader').innerHTML = mailshotStatus.error
            ? '<div class="incoming-reader-empty"><strong>Mailshot is not available</strong><p>Reconnect Tensology to open and read Mailshot messages here.</p></div>'
            : gmailReaderHtml(selectedMailshotThread);
        bindIncomingActions(el('incoming-mailshot-reader'));
    }

    function openIncomingLinkDialog(conversationKey) {
        const conversation = incomingConversations('whatsapp').find((row) => row.key === conversationKey);
        if (!conversation) return;
        state.incomingLinkConversationKey = conversation.key;
        el('incoming-link-title').textContent = conversation.label;
        el('incoming-link-summary').textContent = conversation.links.length
            ? 'Linked to these boards. You can add another board without removing the existing links.'
            : 'Link this person or group when the conversation belongs to active work.';
        el('incoming-current-links').innerHTML = conversation.links.length
            ? conversation.links
                  .map(
                      (link) =>
                          `<div><span>${actions.escapeHtml(link.board_name || 'Board')}</span><button type="button" data-incoming-unlink="${Number(link.id)}" data-board-id="${Number(link.board_id)}" aria-label="Unlink ${actions.escapeHtml(link.board_name || 'board')}">Remove</button></div>`
                  )
                  .join('')
            : '<small>Not linked to a board.</small>';
        el('incoming-current-links')
            .querySelectorAll('[data-incoming-unlink]')
            .forEach((button) =>
                button.addEventListener('click', async () => {
                    await unlinkIncomingChannel(Number(button.dataset.boardId), Number(button.dataset.incomingUnlink));
                    el('incoming-link-dialog').close();
                })
            );
        const linkedBoardIds = new Set(conversation.links.map((link) => Number(link.board_id)));
        const options = context.boards.filter((board) => board.provider === 'decisions' && !linkedBoardIds.has(Number(board.local_id || board.id)));
        el('incoming-link-board').innerHTML =
            '<option value="">Choose board</option>' +
            options.map((board) => `<option value="${Number(board.local_id || board.id)}">${actions.escapeHtml(board.name || 'Board')}</option>`).join('');
        el('incoming-link-dialog').showModal();
    }

    async function linkIncomingConversation() {
        const conversation = incomingConversations('whatsapp').find((row) => row.key === state.incomingLinkConversationKey);
        const boardId = Number(el('incoming-link-board')?.value || 0);
        if (!conversation || !boardId) {
            actions.toast('Choose a board first.', 'error');
            return;
        }
        try {
            await actions.api(`/tickets/boards/${boardId}/whatsapp-links`, {
                method: 'POST',
                body: {
                    phone_jid: conversation.threadId,
                    contact_name: conversation.label,
                    auto_snapshot: false
                }
            });
            el('incoming-link-dialog').close('saved');
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(`${conversation.label} linked to the board.`);
        } catch (error) {
            actions.toast(error.message || 'Could not link the WhatsApp conversation.', 'error');
        }
    }

    async function reconnectGoogleFromIncoming(event) {
        const button = event?.currentTarget;
        if (button) button.disabled = true;
        try {
            const returnTo = '/development/incoming/';
            const result = await actions.api(`/advanced/google/oauth-url?return_to=${encodeURIComponent(returnTo)}`);
            if (result.needs_config) {
                window.location.href = `/settings?subtab=connect&provider=google&return_to=${encodeURIComponent(returnTo)}#thirdparty`;
                return;
            }
            if (!result.url) throw new Error(result.error || 'Could not start Google reconnect.');
            window.location.href = result.url;
        } catch (error) {
            if (button) button.disabled = false;
            actions.toast(error.message || 'Could not start Google reconnect.', 'error');
        }
    }

    async function snapshotIncomingWhatsApp(boardId, linkId, button) {
        if (button?.disabled) return;
        const originalLabel = button?.textContent || 'Create snapshot';
        const card = button?.closest('.incoming-whatsapp-reader');
        if (button) {
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.textContent = 'Creating snapshot...';
        }
        card?.classList.add('snapshot-creating');
        const progressTimer = window.setTimeout(() => {
            if (button?.isConnected) button.textContent = 'Copying messages and attachments...';
        }, 6000);
        try {
            const result = await actions.api(`/tickets/boards/${boardId}/whatsapp-snapshot-ticket`, {
                method: 'POST',
                body: { link_id: linkId, since_hours: 48 }
            });
            const messageCount = Number(result.message_count || 0);
            if (result.thread_pending) {
                actions.toast(
                    `Snapshot ticket created from ${messageCount} message${messageCount === 1 ? '' : 's'}, but its thread could not be opened. The ticket is safe on the board.`,
                    'error'
                );
            } else {
                actions.toast(`Snapshot ticket created from ${messageCount} message${messageCount === 1 ? '' : 's'}.`);
            }
            if (result.chat_id) {
                await actions.loadChat(Number(result.chat_id));
                context.attachments = (result.attachments || []).map((item) => ({
                    key: `file:${item.path}`,
                    label: item.name || 'Attachment',
                    text: `Attached local file: ${item.path}\nMIME type: ${item.mime_type || 'application/octet-stream'}\nUse this file as input to the requested work.`,
                    reference: item.path,
                    source: 'file',
                    kind: 'file',
                    mime_type: item.mime_type || 'application/octet-stream',
                    size: Number(item.size || 0),
                    sourceLabel: String(item.mime_type || '').startsWith('image/') ? 'Image' : 'File'
                }));
                el('task-prompt').value = result.prepared_prompt || '';
                actions.resizePrompt();
                actions.renderAttachments();
                el('task-prompt').focus();
                actions.refreshShell({ preserveConversation: true }).catch(() => {});
            } else {
                await actions.refreshShell({ preserveConversation: true });
            }
        } catch (error) {
            actions.toast(error.message || 'Could not create the WhatsApp snapshot ticket.', 'error');
        } finally {
            window.clearTimeout(progressTimer);
            if (button?.isConnected) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
                button.textContent = originalLabel;
            }
            card?.classList.remove('snapshot-creating');
        }
    }

    async function createIncomingTicket(button) {
        const item = state.incoming.find((row) => row.key === button?.dataset.incomingKey);
        if (!item) return;
        const actionScope = button.closest('[data-incoming-conversation], .incoming-gmail-reader');
        const boardId = Number(item.board_id || actionScope?.querySelector('[data-incoming-board]')?.value || 0);
        const board = context.boards.find((row) => Number(row.local_id || row.id) === boardId);
        try {
            const result = await actions.api('/workflows/intake/ingest', {
                method: 'POST',
                body: {
                    source: item.source,
                    user_text: `Create a ticket: ${item.text || 'Incoming request'}`,
                    source_thread_id: item.source_thread_id || '',
                    source_message_id: item.source_message_id || '',
                    board_hint: board?.name || '',
                    project_hint: board?.name || '',
                    metadata: {
                        board_id: boardId || undefined,
                        project_id: board?.project_id || undefined
                    }
                }
            });
            actions.toast(result.decision?.response_text || 'Incoming request was sent to the orchestrator.');
            await actions.refreshShell({ preserveConversation: true });
        } catch (error) {
            actions.toast(error.message || 'Could not create a ticket from the incoming message.', 'error');
        }
    }

    async function resolveIncomingEvent(eventId, action) {
        try {
            await actions.api(`/workflows/intake/inbox/${eventId}/action`, {
                method: 'POST',
                body: { action }
            });
            await actions.refreshShell({ preserveConversation: true });
        } catch (error) {
            actions.toast(error.message || 'Could not update the incoming item.', 'error');
        }
    }

    async function openIncomingTicket(boardId, ticketId) {
        const board = context.boards.find((row) => Number(row.local_id || row.id) === Number(boardId));
        if (!board) return;
        await actions.openBoardKanban(board.key);
        await actions.loadBoardTickets(board.key);
        const ticket = (context.boardTickets[board.key] || []).find((row) => Number(row.id) === Number(ticketId));
        if (ticket) actions.openKanbanTicketDialog(ticket.key);
    }

    async function unlinkIncomingChannel(boardId, linkId) {
        if (
            !(await actions.confirmAction({
                title: 'Unlink incoming channel',
                message: 'Stop showing this WhatsApp person or group as linked to the board? Stored messages and existing tickets are preserved.',
                confirmLabel: 'Unlink',
                danger: true
            }))
        )
            return;
        try {
            await actions.api(`/tickets/boards/${boardId}/whatsapp-links/${linkId}`, {
                method: 'DELETE'
            });
            await refreshIncomingData({ force: true });
            actions.toast('Incoming channel unlinked.');
        } catch (error) {
            actions.toast(error.message || 'Could not unlink the channel.', 'error');
        }
    }

    function refreshIncomingData(options) {
        if (state.incomingLoad) return state.incomingLoad;
        if (!options?.force && state.incomingLoadedAt && Date.now() - state.incomingLoadedAt < 15_000) return Promise.resolve();
        state.incomingLoad = (async () => {
            try {
                const data = await actions.api('/workflows/studio/incoming?limit=150');
                state.incoming = data.items || [];
                state.incomingLinks = data.links || [];
                state.incomingChannels = data.channels || {};
                state.incomingError = '';
            } catch (_error) {
                state.incoming = [];
                state.incomingLinks = [];
                state.incomingChannels = {};
                state.incomingError = 'Incoming is temporarily unavailable. Restart DecisionsAI and try again.';
            } finally {
                state.incomingLoadedAt = Date.now();
                state.incomingLoad = null;
            }
            actions.renderSidebar();
            if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
        })();
        return state.incomingLoad;
    }

    function bindEvents() {
        document.querySelectorAll('[data-incoming-filter]').forEach((button) =>
            button.addEventListener('click', () => {
                state.incomingFilter = button.dataset.incomingFilter || 'all';
                renderIncomingWorkspace();
            })
        );
        el('incoming-link-form').addEventListener('submit', (event) => {
            event.preventDefault();
            linkIncomingConversation();
        });
    }
    return {
        bindEvents,
        incomingConversations,
        refreshIncomingData,
        renderIncomingWorkspace,
        unlinkIncomingChannel
    };
}
