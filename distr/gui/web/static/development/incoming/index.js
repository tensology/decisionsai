// Owns incoming rendering, interactions, and private view state.
export function createIncoming({ context, actions, el }) {
    const state = {
        incoming: [],
        incomingLinks: [],
        incomingChannels: {},
        incomingProposals: [],
        incomingProposalsError: '',
        incomingError: '',
        incomingLoad: null,
        incomingLoadedAt: 0,
        incomingFilter: 'proposals',
        incomingMobileDetail: false,
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
            if (!['whatsapp', 'gmail', 'mailshot', 'telegram'].includes(source)) return;
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

    function incomingAvatarHtml(conversation, large = false) {
        const initials = actions.escapeHtml(incomingInitials(conversation.label));
        const jid = String(conversation.threadId || '');
        const valid = /^(?:\d+(?::\d+)?@(?:s\.whatsapp\.net|lid)|\d+(?:-\d+)?@g\.us)$/.test(jid);
        const photo = conversation.source === 'whatsapp' && valid
            ? `<img data-incoming-avatar src="/api/tickets/whatsapp/profile-picture?jid=${encodeURIComponent(jid)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
            : '';
        return `<span class="incoming-avatar${large ? ' large' : ''}" aria-hidden="true"><span>${initials}</span>${photo}</span>`;
    }

    function incomingLinkPillsHtml(links) {
        return (links || []).map((link) => `<span class="incoming-board-pill">${actions.escapeHtml(link.board_name || 'Board')}</span>`).join('');
    }


    const INCOMING_SEEN_KEY = 'decisions.incoming.conversationSeenAt';

    function incomingSeenMap() {
        try {
            const raw = JSON.parse(localStorage.getItem(INCOMING_SEEN_KEY) || '{}');
            return raw && typeof raw === 'object' ? raw : {};
        } catch (_) {
            return {};
        }
    }

    function conversationLatestIncomingTs(conversation) {
        return Math.max(
            0,
            ...(conversation.messages || [])
                .filter((item) => !item.from_me)
                .map((item) => Date.parse(item.created_at) || 0)
        );
    }

    function conversationShowsNewDot(conversation) {
        if (!conversation?.pendingCount) return false;
        const latest = conversationLatestIncomingTs(conversation);
        if (!latest) return false;
        const seenAt = Number(incomingSeenMap()[conversation.key] || 0);
        return latest > seenAt;
    }

    function markConversationSeen(conversationKey, conversation) {
        if (!conversationKey) return;
        const map = incomingSeenMap();
        const latest = conversation ? conversationLatestIncomingTs(conversation) : 0;
        map[conversationKey] = Math.max(Date.now(), latest || 0);
        localStorage.setItem(INCOMING_SEEN_KEY, JSON.stringify(map));
    }

    function proposalsForWhatsAppThread(conversation) {
        const thread = String(conversation?.threadId || conversation?.key || '');
        if (!thread) return [];
        return (state.incomingProposals || []).filter((proposal) => {
            const sourceThread = String(proposal.source_thread_id || '');
            const id = String(proposal.id || '');
            return sourceThread === thread || id.endsWith(`:${thread}`) || id.includes(thread);
        });
    }

    function whatsappMessageBubbleHtml(item, conversation) {
        return `<div class="incoming-message-bubble${item.from_me ? ' outgoing' : ' incoming'}" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}" data-wa-mid="${actions.escapeHtml(item.source_message_id || '')}"><small>${actions.escapeHtml(item.from_me ? 'You' : item.sender || conversation.label)}</small>${incomingMediaHtml(item)}${item.text ? `<p>${actions.escapeHtml(item.text)}</p>` : ''}<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></div>`;
    }

    function whatsappProposalMarkerHtml(kind, proposal) {
        const title = String(proposal.title || 'Proposal').trim() || 'Proposal';
        const label = kind === 'start' ? `Proposal: ${title} start` : `Proposal: ${title} end`;
        return `<div class="incoming-proposal-marker" data-proposal-marker="${actions.escapeHtml(kind)}" data-proposal-id="${actions.escapeHtml(proposal.id || '')}" role="separator"><span>${actions.escapeHtml(label)}</span></div>`;
    }

    function whatsappMessagesWithProposalMarkers(conversation) {
        const ordered = conversation.messages.slice().reverse().slice(-100);
        const proposals = proposalsForWhatsAppThread(conversation);
        const startAt = new Map();
        const endAt = new Map();
        proposals.forEach((proposal) => {
            const ids = new Set((proposal.message_ids || []).map((value) => String(value)));
            if (!ids.size) return;
            let first = -1;
            let last = -1;
            ordered.forEach((item, index) => {
                const mid = String(item.source_message_id || '');
                if (!mid || !ids.has(mid)) return;
                if (first < 0) first = index;
                last = index;
            });
            if (first < 0) return;
            const starts = startAt.get(first) || [];
            starts.push(proposal);
            startAt.set(first, starts);
            const ends = endAt.get(last) || [];
            ends.push(proposal);
            endAt.set(last, ends);
        });
        return ordered
            .map((item, index) => {
                const before = (startAt.get(index) || []).map((proposal) => whatsappProposalMarkerHtml('start', proposal)).join('');
                const bubble = whatsappMessageBubbleHtml(item, conversation);
                const after = (endAt.get(index) || []).map((proposal) => whatsappProposalMarkerHtml('end', proposal)).join('');
                return `${before}${bubble}${after}`;
            })
            .join('');
    }

    function mailMessageCardHtml(item) {
        const attachments =
            Array.isArray(item.attachments) && item.attachments.length
                ? `<small class="incoming-gmail-attachments">${item.attachments.length} attachment${item.attachments.length === 1 ? '' : 's'}: ${actions.escapeHtml(item.attachments.map((attachment) => attachment.filename || 'Attachment').join(', '))}</small>`
                : '';
        const from = item.sender || 'Unknown sender';
        const to = item.recipient ? `To ${item.recipient}` : '';
        const when = actions.formatDateTime(item.created_at);
        return `<section class="incoming-gmail-message" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}" data-mail-key="${actions.escapeHtml(item.key || '')}" data-mail-id="${actions.escapeHtml(item.source_message_id || '')}" data-mail-sender="${actions.escapeHtml(from)}" data-mail-subject="${actions.escapeHtml(item.subject || '')}"><header class="incoming-mail-headers"><div class="incoming-mail-header-row"><span class="incoming-mail-header-label">From</span><strong>${actions.escapeHtml(from)}</strong></div>${to ? `<div class="incoming-mail-header-row"><span class="incoming-mail-header-label">To</span><span>${actions.escapeHtml(item.recipient)}</span></div>` : ''}<div class="incoming-mail-header-row"><span class="incoming-mail-header-label">Date</span><time>${actions.escapeHtml(when)}</time></div></header><div class="incoming-gmail-body">${actions.escapeHtml(item.text || item.snippet || 'No message text')}</div>${attachments}</section>`;
    }

    function mailReplySubject(conversation) {
        const label = String(conversation?.label || '').trim();
        if (!label) return 'Re:';
        return /^re:/i.test(label) ? label : `Re: ${label}`;
    }

    function mailChannelFromReader(root) {
        if (root?.id === 'incoming-mailshot-reader' || root?.closest?.('#incoming-panel-mailshot')) return 'mailshot';
        return 'gmail';
    }

    function incomingConversationRowHtml(conversation) {
        const latest = conversation.latest || {};
        const selected = state.selectedIncomingConversationKey === conversation.key;
        const direction = latest.from_me ? 'You: ' : '';
        const showDot = conversationShowsNewDot(conversation);
        return `<button type="button" class="incoming-conversation-row${showDot ? ' pending' : ''}${selected ? ' selected' : ''}" data-incoming-conversation="${actions.escapeHtml(conversation.key)}" aria-pressed="${selected}">${incomingAvatarHtml(conversation)}<span class="incoming-row-copy"><span class="incoming-row-title"><strong>${actions.escapeHtml(conversation.label)}</strong><time>${actions.escapeHtml(actions.formatDateTime(latest.created_at))}</time></span><span class="incoming-row-preview">${actions.escapeHtml(direction + (latest.text || (latest.media_type ? `${latest.media_type} attachment` : 'No message text')))}</span><span class="incoming-row-meta">${incomingLinkPillsHtml(conversation.links)}${showDot ? `<span class="incoming-new-dot" data-incoming-mark-seen="${actions.escapeHtml(conversation.key)}" role="button" tabindex="0" title="Mark as seen" aria-label="Mark conversation as seen"></span>` : ''}</span></span><span class="incoming-row-more" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}" title="View information or link to a board">•••</span></button>`;
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
            return '<div class="incoming-reader-empty"><strong>Select a conversation</strong><p>Linked conversations are kept at the top.</p></div>';
        const messages = whatsappMessagesWithProposalMarkers(conversation);
        const linkedNote = conversation.links.length ? '' : 'This conversation is not linked.';
        return `<header class="incoming-whatsapp-reader-header">${incomingAvatarHtml(conversation, true)}<div><h3>${actions.escapeHtml(conversation.label)}</h3><p>${conversation.chatType === 'group' ? 'WhatsApp group' : 'WhatsApp contact'} · ${conversation.messages.length} recent messages</p></div><button type="button" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}">•••</button></header><div class="incoming-whatsapp-messages">${messages}</div><footer>${linkedNote ? `<small>${actions.escapeHtml(linkedNote)}</small>` : '<span></span>'}<span></span></footer>`;
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
            return '<div class="incoming-reader-empty"><strong>Select a conversation</strong><p>Choose a conversation on the left to read it here.</p></div>';
        const latest = conversation.latest || {};
        let rightActions = '';
        if (latest.ticket_id) {
            rightActions = `<button type="button" data-incoming-open-ticket="${Number(latest.ticket_id)}" data-board-id="${Number(latest.board_id || 0)}">Open ticket</button>`;
        } else {
            rightActions = `<div class="incoming-link-controls"><select data-incoming-board aria-label="Choose ticket board">${incomingBoardOptions('')}</select><button type="button" data-incoming-create-ticket data-incoming-key="${actions.escapeHtml(latest.key || '')}">Create ticket</button></div>`;
        }
        const replyTo = actions.escapeHtml(latest.sender || '');
        const replySubject = actions.escapeHtml(mailReplySubject(conversation));
        const replyMessageId = actions.escapeHtml(latest.source_message_id || '');
        const messages = conversation.messages.slice().reverse().map(mailMessageCardHtml).join('');
        return `<header class="incoming-gmail-reader-header"><div><h3>${actions.escapeHtml(conversation.label || '(No subject)')}</h3><p>${conversation.messages.length} message${conversation.messages.length === 1 ? '' : 's'}</p></div></header><div class="incoming-gmail-messages">${messages}</div><div class="incoming-quick-reply hidden" data-incoming-quick-reply><div class="incoming-quick-reply-fields"><label>To<input type="email" data-quick-reply-to value="${replyTo}" autocomplete="email"></label><label>Subject<input type="text" data-quick-reply-subject value="${replySubject}"></label><label>Message<textarea data-quick-reply-body rows="6" placeholder="Write your reply"></textarea></label><input type="hidden" data-quick-reply-message-id value="${replyMessageId}"></div><div class="incoming-quick-reply-actions"><button type="button" class="proposal-btn ghost" data-quick-reply-cancel>Cancel</button><button type="button" class="proposal-btn primary" data-quick-reply-send>Send</button></div></div><footer class="incoming-mail-footer"><button type="button" class="proposal-btn ghost" data-incoming-quick-reply-open>Quick Reply</button><div class="incoming-mail-footer-right">${rightActions}</div></footer>`;
    }

    function bindIncomingActions(root) {
        root.querySelectorAll('[data-incoming-avatar]').forEach((photo) => {
            const show = () => photo.classList.toggle('loaded', photo.naturalWidth > 0);
            photo.addEventListener('load', show, { once: true });
            photo.addEventListener('error', () => photo.remove(), { once: true });
            if (photo.complete) show();
        });
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
        root.querySelectorAll('[data-incoming-mark-seen]').forEach((dot) => {
            const clear = (event) => {
                event.preventDefault();
                event.stopPropagation();
                const key = dot.getAttribute('data-incoming-mark-seen') || '';
                const conversation = incomingConversations('whatsapp').find((row) => row.key === key);
                markConversationSeen(key, conversation);
                renderIncomingWorkspace();
            };
            dot.addEventListener('click', clear);
            dot.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') clear(event);
            });
        });
        root.querySelectorAll('[data-incoming-quick-reply-open]').forEach((button) =>
            button.addEventListener('click', () => {
                const reader = button.closest('.incoming-gmail-reader');
                reader?.querySelector('[data-incoming-quick-reply]')?.classList.remove('hidden');
                reader?.querySelector('[data-quick-reply-body]')?.focus();
            })
        );
        root.querySelectorAll('[data-quick-reply-cancel]').forEach((button) =>
            button.addEventListener('click', () => {
                const panel = button.closest('[data-incoming-quick-reply]');
                if (!panel) return;
                panel.classList.add('hidden');
                const body = panel.querySelector('[data-quick-reply-body]');
                if (body) body.value = '';
            })
        );
        root.querySelectorAll('[data-quick-reply-send]').forEach((button) =>
            button.addEventListener('click', () => sendIncomingQuickReply(button))
        );
    }

    async function sendIncomingQuickReply(button) {
        const reader = button.closest('.incoming-gmail-reader');
        const panel = button.closest('[data-incoming-quick-reply]');
        if (!reader || !panel) return;
        const channel = mailChannelFromReader(reader);
        const to = panel.querySelector('[data-quick-reply-to]')?.value?.trim() || '';
        const subject = panel.querySelector('[data-quick-reply-subject]')?.value?.trim() || '';
        const body = panel.querySelector('[data-quick-reply-body]')?.value || '';
        const messageId = panel.querySelector('[data-quick-reply-message-id]')?.value || '';
        if (!to || !body.trim()) {
            actions.toast('Add a recipient and reply text before sending.', 'error');
            return;
        }
        const original = button.textContent;
        button.disabled = true;
        button.textContent = 'Sending…';
        try {
            const result = await actions.api('/workflows/studio/incoming/mail/reply', {
                method: 'POST',
                body: {
                    channel,
                    to,
                    subject,
                    body,
                    message_id: messageId,
                    dry_run: false,
                },
            });
            if (!result?.success) throw new Error(result?.error || 'Could not send the reply.');
            actions.toast(result.dry_run ? 'Dry-run only — reply was not sent.' : 'Reply sent.');
            panel.classList.add('hidden');
            const bodyField = panel.querySelector('[data-quick-reply-body]');
            if (bodyField) bodyField.value = '';
        } catch (error) {
            actions.toast(error.message || 'Could not send the reply.', 'error');
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    }

    function incomingSourceGlyph(source) {
        const glyphs = { whatsapp: '◫', gmail: '✉', mailshot: '✉', telegram: '✈' };
        return `<span class="proposal-source-icon" aria-hidden="true">${glyphs[String(source || '').toLowerCase()] || '•'}</span>`;
    }

    function incomingInternalToken() {
        return document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';
    }

    function proposalFirstMessage(proposal) {
        const evidence = proposal.evidence || [];
        const ev = evidence[evidence.length - 1] || evidence[0];
        if (!ev) return '';
        const who = ev.sender_name || ev.sender_address || '';
        return `${who ? who + ': ' : ''}${String(ev.body || '').replace(/\s+/g, ' ').slice(0, 180)}`.trim();
    }

    // Card mirrors Tensology's proposal card: project name top-left, title, source pill
    // top-right (hover shows the exact message), description, then the actions. Clicking
    // the card opens the evidence view with the retained messages and attachments.
    function incomingProposalRowHtml(proposal) {
        const source = String(proposal.source_label || proposal.source || '').trim();
        const project = String(proposal.project_name || (proposal.project_id ? `Project ${proposal.project_id}` : '')).trim();
        const origin = proposal.origin === 'tensology' ? 'Synced from Tensology' : 'Detected here';
        const description = String(proposal.description || proposal.rationale || '').trim();
        const proposalId = actions.escapeHtml(proposal.id || '');
        const tooltip = proposalFirstMessage(proposal) || `${origin} · ${source}`;
        const push = proposal.project_id
            ? `<button type="button" class="proposal-btn primary" data-incoming-proposal-thread="${proposalId}" title="Creates the thread and hands it to Decisions, like Send to Decisions.">Create Thread</button>`
            : `<span class="proposal-btn muted" title="Link this conversation to a project first">No project</span>`;
        const dismiss = `<button type="button" class="proposal-btn ghost" data-incoming-proposal-dismiss="${proposalId}" title="Dismiss this proposal">Dismiss</button>`;
        const projectKey = actions.escapeHtml(String(proposal.project_id || ''));
        return `<article class="proposal-card" data-incoming-proposal-open="${proposalId}" data-proposal-project="${projectKey}" draggable="true" tabindex="0" role="button" aria-label="Open evidence for ${actions.escapeHtml(proposal.title || 'proposal')}">
            <div class="proposal-head">
                <p class="proposal-project${project ? '' : ' none'}">${actions.escapeHtml(project || 'Not linked to a project')}</p>
                <h3 class="proposal-title">${actions.escapeHtml(proposal.title || 'Proposal')}</h3>
                <span class="proposal-source" title="${actions.escapeHtml(tooltip)}" data-incoming-proposal-open="${proposalId}">${incomingSourceGlyph(proposal.source)}${actions.escapeHtml(source)}</span>
            </div>
            ${description ? `<p class="proposal-desc">${actions.escapeHtml(description)}</p>` : ''}
            <div class="proposal-actions">${push}${dismiss}<span class="proposal-drag" title="Drag onto another proposal on the same project to merge them into one ticket" aria-hidden="true">⠿</span></div>
        </article>`;
    }

    let dragProposalId = '';

    function proposalById(proposalId) {
        return (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId)) || null;
    }

    async function mergeProposalCards(targetId, sourceId) {
        const first = proposalById(sourceId);
        const second = proposalById(targetId);
        if (!first || !second || String(first.id) === String(second.id)) return;
        if (!first.project_id || String(first.project_id) !== String(second.project_id)) {
            actions.toast('Only proposals on the same project can be merged.', 'error');
            return;
        }
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/merge', {
                method: 'POST',
                body: { first, second }
            });
            if (result && result.chat_id) {
                actions.toast('Merged into one ticket.', 'success');
                state.incomingProposals = (state.incomingProposals || []).filter(
                    (row) => String(row.id) !== String(sourceId) && String(row.id) !== String(targetId)
                );
                renderIncomingWorkspace();
                if (actions.openThreadDialog) actions.openThreadDialog(result.chat_id);
            }
        } catch (error) {
            actions.toast(error.message || 'Could not merge the proposals.', 'error');
        }
    }

    function proposalEvidenceHtml(proposal) {
        const evidence = proposal.evidence || [];
        if (!evidence.length) {
            return '<p class="proposal-empty">No retained source messages for this proposal.</p>';
        }
        return evidence.map((ev) => `<article class="proposal-message">
            <header><strong>${actions.escapeHtml(ev.sender_name || ev.sender_address || 'Unknown')}</strong><time>${actions.escapeHtml(actions.formatDateTime(ev.sent_at))}</time></header>
            ${ev.body ? `<p>${actions.escapeHtml(ev.body)}</p>` : '<p class="muted">(no text)</p>'}
        </article>`).join('');
    }

    function proposalAttachmentUrl(proposal, item) {
        const token = incomingInternalToken();
        if (item.download_error || item.error) return '';
        if (item.url) {
            const join = item.url.includes('?') ? '&' : '?';
            return `${item.url}${join}internal_token=${encodeURIComponent(token)}`;
        }
        if (item.id && proposal?.id) {
            return `/api/workflows/studio/incoming/proposals/files/${encodeURIComponent(item.id)}?proposal_id=${encodeURIComponent(proposal.id)}&internal_token=${encodeURIComponent(token)}`;
        }
        if (item.api_path) {
            return `/api/workflows/studio/incoming/proposals/attachment?path=${encodeURIComponent(item.api_path)}&internal_token=${encodeURIComponent(token)}`;
        }
        return '';
    }

    function proposalFilesHtml(proposal) {
        const items = proposal.attachments || [];
        if (!items.length) return '<p class="proposal-empty">No attachments on this proposal.</p>';
        const media = items.map((a, index) => {
            const err = a.download_error || a.error || '';
            if (err) {
                return `<div class="proposal-file proposal-file-failed" role="status" title="${actions.escapeHtml(err)}"><strong>${actions.escapeHtml(a.name || 'Attachment')}</strong><span>${actions.escapeHtml(err)}</span></div>`;
            }
            const url = proposalAttachmentUrl(proposal, a);
            if (!url) {
                return `<div class="proposal-file proposal-file-failed" role="status"><strong>${actions.escapeHtml(a.name || 'Attachment')}</strong><span>No download link yet. Open Files again after sync.</span></div>`;
            }
            const isImage = String(a.mime_type || '').startsWith('image/');
            const caption = a.caption || a.transcript || '';
            const payload = actions.escapeHtml(JSON.stringify({
                url,
                name: a.name || 'Attachment',
                mime_type: a.mime_type || '',
                caption,
            }));
            return isImage
                ? `<button type="button" class="proposal-media" data-file-preview="${payload}" title="${actions.escapeHtml(caption || a.name || '')}"><img src="${url}" alt="${actions.escapeHtml(a.name || 'Attachment')}" loading="lazy" /></button>`
                : `<button type="button" class="proposal-file" data-file-preview="${payload}">${actions.escapeHtml(a.name || 'Attachment')}${a.kind ? ` · ${actions.escapeHtml(a.kind)}` : ''}</button>`;
        }).join('');
        return `<div class="proposal-media-grid">${media || '<p class="proposal-empty">No attachments on this proposal.</p>'}</div>`;
    }

    function closeFilePreview() {
        const overlay = document.getElementById('incoming-file-preview-modal');
        if (!overlay) return;
        overlay.remove();
        document.body.style.overflow = overlay._previousOverflow || '';
        document.removeEventListener('keydown', onFilePreviewKey);
    }

    function onFilePreviewKey(event) {
        if (event.key === 'Escape') closeFilePreview();
    }

    function openFilePreview(meta) {
        if (!meta || !meta.url) return;
        closeFilePreview();
        const mime = String(meta.mime_type || '');
        const name = meta.name || 'Attachment';
        let body;
        if (mime.startsWith('image/')) {
            body = `<img class="file-preview-image" src="${actions.escapeHtml(meta.url)}" alt="${actions.escapeHtml(name)}" />`;
        } else if (mime === 'application/pdf' || String(name).toLowerCase().endsWith('.pdf')) {
            body = `<iframe class="file-preview-frame" title="${actions.escapeHtml(name)}" src="${actions.escapeHtml(meta.url)}"></iframe>`;
        } else if (mime.startsWith('text/') || mime === 'application/json' || /\.(txt|md|csv|json|log)$/i.test(name)) {
            body = `<pre class="file-preview-text" data-file-preview-text>Loading…</pre>`;
        } else {
            body = `<div class="file-preview-other"><p>Preview is not available for this file type.</p><a class="proposal-btn primary" href="${actions.escapeHtml(meta.url)}" download="${actions.escapeHtml(name)}">Download ${actions.escapeHtml(name)}</a></div>`;
        }
        const overlay = document.createElement('div');
        overlay.className = 'proposal-modal-overlay file-preview-overlay';
        overlay.id = 'incoming-file-preview-modal';
        overlay._previousOverflow = document.body.style.overflow;
        overlay.innerHTML = `<div class="proposal-modal file-preview-modal" role="dialog" aria-modal="true" aria-label="${actions.escapeHtml(name)}">
            <header>
                <div class="proposal-modal-title"><h3>${actions.escapeHtml(name)}</h3>${meta.caption ? `<p class="proposal-modal-desc">${actions.escapeHtml(meta.caption)}</p>` : ''}</div>
                <button type="button" class="proposal-modal-close" aria-label="Close" data-file-preview-close>×</button>
            </header>
            <div class="proposal-modal-body file-preview-body">${body}</div>
            <footer>
                <a class="proposal-btn ghost" href="${actions.escapeHtml(meta.url)}" download="${actions.escapeHtml(name)}">Download</a>
                <button type="button" class="proposal-btn primary" data-file-preview-close>Close</button>
            </footer>
        </div>`;
        document.body.style.overflow = 'hidden';
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (event) => { if (event.target === overlay) closeFilePreview(); });
        overlay.querySelectorAll('[data-file-preview-close]').forEach((btn) => btn.addEventListener('click', closeFilePreview));
        document.addEventListener('keydown', onFilePreviewKey);
        const textNode = overlay.querySelector('[data-file-preview-text]');
        if (textNode) {
            fetch(meta.url).then(async (response) => {
                if (!response.ok) {
                    let message = `Could not load file (${response.status}).`;
                    try {
                        const payload = await response.json();
                        if (payload?.error) message = payload.error;
                    } catch (_) {}
                    textNode.textContent = message;
                    return;
                }
                textNode.textContent = await response.text();
            }).catch((error) => {
                textNode.textContent = error?.message || 'Could not load file.';
            });
        }
    }

    function bindProposalFilePreview(root) {
        root?.querySelectorAll('[data-file-preview]').forEach((node) => {
            node.addEventListener('click', () => {
                try {
                    openFilePreview(JSON.parse(node.getAttribute('data-file-preview') || '{}'));
                } catch (_) {}
            });
        });
    }

    async function ensureProposalFilesForUi(proposal) {
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/files/ensure', {
                method: 'POST',
                body: { proposal },
            });
            const attachments = [...(result.attachments || []), ...(result.failures || [])];
            if (attachments.length) {
                proposal.attachments = attachments;
                const listed = (state.incomingProposals || []).find((row) => String(row.id) === String(proposal.id));
                if (listed) listed.attachments = attachments;
            }
            return attachments;
        } catch (error) {
            const message = error?.message || String(error);
            proposal.attachments = [{ name: 'Attachments', download_error: message }];
            return proposal.attachments;
        }
    }

    function closeProposalDetail() {
        const overlay = el('incoming-proposal-modal');
        if (overlay) {
            overlay.remove();
            document.body.style.overflow = overlay._previousOverflow || '';
            if (overlay._returnFocus?.isConnected) overlay._returnFocus.focus();
        }
        document.removeEventListener('keydown', onProposalDetailKey);
    }

    function onProposalDetailKey(event) {
        if (event.key === 'Escape') closeProposalDetail();
    }

    // Tabbed detail, mirroring Tensology's ticket view (Details / Tasks / Files ...).
    function openProposalDetail(proposalId) {
        const proposal = (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId));
        if (!proposal) return;
        closeProposalDetail();
        const source = String(proposal.source_label || proposal.source || '').trim();
        const project = String(proposal.project_name || 'Not linked to a project').trim();
        const conversation = proposal.source_conversation || {};
        const tasks = proposal.tasks || [];
        const files = proposal.attachments || [];
        const evidence = proposal.evidence || [];
        const meta = [
            ['Participant', proposal.participant || conversation.participant_name || conversation.participant_address || ''],
            ['Channel', conversation.channel || source],
            ['Priority', proposal.priority || ''],
            ['Source thread', conversation.thread_id || ''],
        ].filter((row) => row[1]);
        const projects = [...(context.projects || [])];
        if (proposal.project_id && !projects.some((p) => Number(p.id) === Number(proposal.project_id))) {
            projects.push({ id: proposal.project_id, name: project });
        }
        const projectOptions = '<option value="">Choose project</option>' + projects
            .sort((a, b) => String(a.name).localeCompare(String(b.name)))
            .map((p) => `<option value="${Number(p.id)}"${Number(p.id) === Number(proposal.project_id) ? ' selected' : ''}>${actions.escapeHtml(p.name)}</option>`).join('');
        const details = `<div class="proposal-project-field"><label for="proposal-project-select">Project</label>
            <select id="proposal-project-select" aria-describedby="proposal-project-status">${projectOptions}</select>
            <span id="proposal-project-status" role="status" aria-live="polite"></span></div>
            <dl class="proposal-modal-meta">${meta.map(([k, v]) => `<div><dt>${actions.escapeHtml(k)}</dt><dd>${actions.escapeHtml(v)}</dd></div>`).join('')}</dl>
            ${proposal.description ? `<p class="proposal-modal-desc">${actions.escapeHtml(proposal.description)}</p>` : ''}
            ${proposal.rationale ? `<p class="proposal-modal-rationale"><span>Why</span> ${actions.escapeHtml(proposal.rationale)}</p>` : ''}`;
        const tasksPane = tasks.length ? `<ul class="proposal-tasks">${tasks.map((t) => `<li>${actions.escapeHtml(t)}</li>`).join('')}</ul>` : '<p class="proposal-empty">No tasks listed.</p>';
        const tabs = [['details', 'Details', 0], ['tasks', 'Tasks', tasks.length], ['files', 'Files', files.length], ['source', 'Source', evidence.length]];
        const tabBar = tabs.map(([id, label, count], i) => `<button type="button" role="tab" class="proposal-tab${i === 0 ? ' active' : ''}" aria-selected="${i === 0}" data-proposal-tab="${id}">${label}${count ? `<span class="proposal-tab-count">${count}</span>` : ''}</button>`).join('');
        const pane = (id, html, active) => `<div class="proposal-pane${active ? '' : ' hidden'}" role="tabpanel" data-proposal-pane="${id}">${html}</div>`;
        const overlay = document.createElement('div');
        overlay.className = 'proposal-modal-overlay';
        overlay.id = 'incoming-proposal-modal';
        overlay.innerHTML = `<div class="proposal-modal" role="dialog" aria-modal="true" aria-label="${actions.escapeHtml(proposal.title || 'Proposal')}">
            <header>
                <div class="proposal-modal-title"><span class="proposal-source">${incomingSourceGlyph(proposal.source)}${actions.escapeHtml(source)}</span><h3>${actions.escapeHtml(proposal.title || 'Proposal')}</h3></div>
                <button type="button" class="proposal-modal-close" aria-label="Close" data-proposal-close>×</button>
            </header>
            <nav class="proposal-tabs" role="tablist">${tabBar}</nav>
            <div class="proposal-modal-body">
                ${pane('details', details, true)}
                ${pane('tasks', tasksPane, false)}
                ${pane('files', proposalFilesHtml(proposal), false)}
                ${pane('source', proposalEvidenceHtml(proposal), false)}
            </div>
            <footer>
                <button type="button" class="proposal-btn ghost" data-proposal-close>Close</button>
                <button type="button" class="proposal-btn primary" data-proposal-thread="${actions.escapeHtml(proposal.id || '')}"${proposal.project_id ? '' : ' disabled'}>Create Thread</button>
            </footer>
        </div>`;
        document.body.appendChild(overlay);
        bindProposalFilePreview(overlay);
        const filesPane = overlay.querySelector('[data-proposal-pane="files"]');
        const filesTab = overlay.querySelector('[data-proposal-tab="files"]');
        if (filesPane && !(proposal.attachments || []).some((item) => item.url || item.id || item.download_error || item.error)) {
            filesPane.innerHTML = '<p class="proposal-empty">Loading attachments…</p>';
        }
        ensureProposalFilesForUi(proposal).then((attachments) => {
            if (!filesPane?.isConnected) return;
            filesPane.innerHTML = proposalFilesHtml({ ...proposal, attachments });
            bindProposalFilePreview(filesPane);
            if (filesTab) {
                const count = attachments.length;
                filesTab.innerHTML = `Files${count ? `<span class="proposal-tab-count">${count}</span>` : ''}`;
            }
        });
        const projectSelect = overlay.querySelector('#proposal-project-select');
        const projectStatus = overlay.querySelector('#proposal-project-status');
        const threadButton = overlay.querySelector('[data-proposal-thread]');
        projectSelect.addEventListener('change', async () => {
            const current = proposalById(proposalId);
            if (!current) return;
            const selectedId = Number(projectSelect.value);
            if (!selectedId) { projectSelect.value = String(current.project_id || ''); return; }
            projectSelect.disabled = true;
            threadButton.disabled = true;
            projectStatus.textContent = 'Saving...';
            projectStatus.classList.remove('error');
            try {
                const result = await actions.api('/workflows/studio/incoming/proposals/project', {
                    method: 'PUT', body: { proposal_id: proposalId, project_id: selectedId, message_ids: current.message_ids || [] }
                });
                if (!result?.success || !result.proposal) throw new Error(result?.error || 'Could not save the project.');
                state.incomingProposals = state.incomingProposals.map((row) => String(row.id) === String(proposalId) ? result.proposal : row);
                projectSelect.value = String(result.proposal.project_id);
                projectStatus.textContent = 'Saved';
                renderIncomingWorkspace();
                overlay._returnFocus = [...(el('incoming-proposals-list')?.querySelectorAll('.proposal-card') || [])]
                    .find((node) => node.dataset.incomingProposalOpen === String(proposalId)) || overlay._returnFocus;
            } catch (error) {
                projectSelect.value = String(current.project_id || '');
                projectStatus.textContent = error.data?.error || error.message || 'Could not save the project. Try again.';
                projectStatus.classList.add('error');
            } finally {
                projectSelect.disabled = false;
                threadButton.disabled = !proposalById(proposalId)?.project_id;
                projectSelect.focus();
            }
        });
        const previousFocus = document.activeElement;
        overlay.addEventListener('keydown', (event) => {
            if (event.key !== 'Tab') return;
            const controls = [...overlay.querySelectorAll('button:not(:disabled), select:not(:disabled)')].filter((n) => n.getClientRects().length);
            const first = controls[0], last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        overlay._returnFocus = previousFocus;
        overlay._previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        projectSelect.focus();
        overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) closeProposalDetail(); });
        overlay.querySelectorAll('[data-proposal-close]').forEach((b) => b.addEventListener('click', closeProposalDetail));
        overlay.querySelectorAll('[data-proposal-tab]').forEach((button) => button.addEventListener('click', () => {
            overlay.querySelectorAll('[data-proposal-tab]').forEach((b) => { b.classList.toggle('active', b === button); b.setAttribute('aria-selected', String(b === button)); });
            overlay.querySelectorAll('[data-proposal-pane]').forEach((p) => p.classList.toggle('hidden', p.dataset.proposalPane !== button.dataset.proposalTab));
        }));
        overlay.querySelectorAll('[data-proposal-thread]').forEach((b) => b.addEventListener('click', () => {
            closeProposalDetail();
            pushProposalToThread(b.dataset.proposalThread, b);
        }));
        document.addEventListener('keydown', onProposalDetailKey);
    }

    async function dismissProposal(proposalId, button) {
        const proposal = (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId));
        if (!proposal) return;
        button.disabled = true;
        try {
            await actions.api('/workflows/studio/incoming/proposals/dismiss', { method: 'POST', body: { proposal } });
            state.incomingProposals = (state.incomingProposals || []).filter((row) => String(row.id) !== String(proposalId));
            renderIncomingWorkspace();
        } catch (error) {
            actions.toast(error.message || 'Could not dismiss the proposal.', 'error');
            button.disabled = false;
        }
    }

    async function pushProposalToThread(proposalId, button) {
        if (button?.disabled) return;
        const proposal = (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId));
        if (!proposal) return;
        const original = button.textContent;
        button.disabled = true;
        button.textContent = 'Creating…';
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/thread', {
                method: 'POST',
                body: { proposal }
            });
            if (!result?.chat_id) throw new Error(result?.error || 'The proposal thread could not be created.');
            if (result && result.chat_id) {
                actions.toast('Thread created.', 'success');
                // The proposal is actioned (and cleared on Tensology) — drop it from the list.
                state.incomingProposals = (state.incomingProposals || []).filter((row) => String(row.id) !== String(proposalId));
                renderIncomingWorkspace();
                await actions.loadChat(Number(result.chat_id));
                await actions.refreshShell({ preserveConversation: true });
            }
        } catch (error) {
            actions.toast(error.message || 'Could not create the thread.', 'error');
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    }

    function renderIncomingWorkspace() {
        const list = el('incoming-list');
        if (!list) return;
        el('incoming-workspace').classList.toggle('incoming-detail-open', state.incomingMobileDetail);
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
        const proposalsPanel = el('incoming-panel-proposals');
        if (proposalsPanel) proposalsPanel.classList.toggle('hidden', state.incomingFilter !== 'proposals');
        const proposalsCount = el('incoming-proposals-count');
        if (proposalsCount) proposalsCount.textContent = String((state.incomingProposals || []).length);
        const proposalsList = el('incoming-proposals-list');
        if (proposalsList) {
            const hasProposals = (state.incomingProposals || []).length > 0;
            const loading = state.incomingProposalsLoading;
            proposalsList.classList.toggle('proposal-cards-status', !hasProposals || !!state.incomingProposalsError);
            proposalsList.setAttribute('aria-busy', loading ? 'true' : 'false');
            proposalsList.innerHTML = loading && !hasProposals
                ? '<div class="proposal-list-status" role="status"><span class="proposal-loading-spinner" aria-hidden="true"></span><span>Loading proposals...</span></div>'
                : state.incomingProposalsError
                  ? `<div class="proposal-list-status" role="status">${actions.escapeHtml(state.incomingProposalsError)}</div>`
                  : hasProposals
                    ? (state.incomingProposals || []).map(incomingProposalRowHtml).join('')
                    : '<div class="proposal-list-status" role="status">No proposals right now.</div>';
            proposalsList.querySelectorAll('[data-incoming-proposal-thread]').forEach((button) =>
                button.addEventListener('click', () => pushProposalToThread(button.dataset.incomingProposalThread, button))
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-dismiss]').forEach((button) =>
                button.addEventListener('click', () => dismissProposal(button.dataset.incomingProposalDismiss, button))
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-open]').forEach((node) =>
                node.addEventListener('click', (event) => {
                    if (event.target.closest('[data-incoming-proposal-thread],[data-incoming-proposal-dismiss]')) return;
                    openProposalDetail(node.dataset.incomingProposalOpen);
                })
            );
            proposalsList.querySelectorAll('.proposal-card[data-incoming-proposal-open]').forEach((node) =>
                node.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openProposalDetail(node.dataset.incomingProposalOpen);
                    }
                })
            );
            // Drag one proposal onto another on the same project to merge them.
            proposalsList.querySelectorAll('.proposal-card[data-proposal-project]').forEach((node) => {
                node.addEventListener('dragstart', (event) => {
                    dragProposalId = node.dataset.incomingProposalOpen || '';
                    if (event.dataTransfer) {
                        event.dataTransfer.effectAllowed = 'move';
                        try { event.dataTransfer.setData('text/plain', dragProposalId); } catch (_) { /* ignore */ }
                    }
                    node.classList.add('dragging');
                });
                node.addEventListener('dragend', () => {
                    node.classList.remove('dragging');
                    proposalsList.querySelectorAll('.proposal-card.drop-target').forEach((n) => n.classList.remove('drop-target'));
                });
                node.addEventListener('dragover', (event) => {
                    const source = proposalById(dragProposalId);
                    if (!source || !source.project_id || String(source.project_id) !== String(node.dataset.proposalProject)) return;
                    event.preventDefault();
                    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
                    node.classList.add('drop-target');
                });
                node.addEventListener('dragleave', () => node.classList.remove('drop-target'));
                node.addEventListener('drop', (event) => {
                    event.preventDefault();
                    node.classList.remove('drop-target');
                    const sourceId = dragProposalId || (event.dataTransfer ? event.dataTransfer.getData('text/plain') : '');
                    if (sourceId && sourceId !== node.dataset.incomingProposalOpen) {
                        mergeProposalCards(node.dataset.incomingProposalOpen, sourceId);
                    }
                });
            });
        }
        if (!whatsappConversations.some((conversation) => conversation.key === state.selectedIncomingConversationKey)) {
            state.selectedIncomingConversationKey =
                whatsappConversations.find((conversation) => conversation.links.length)?.key || whatsappConversations[0]?.key || '';
        }
        const linkedConversations = whatsappConversations.filter((conversation) => conversation.links.length);
        const otherConversations = whatsappConversations.filter((conversation) => !conversation.links.length);
        list.innerHTML = state.incomingError
            ? `<div class="development-empty">${actions.escapeHtml(state.incomingError)}</div>`
            : whatsappConversations.length
              ? `${linkedConversations.length ? `<section class="incoming-channel-section"><header><h2>Linked conversations</h2><small>${linkedConversations.length}</small></header><div>${linkedConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}${otherConversations.length ? `<section class="incoming-channel-section other"><header><h2>Other conversations</h2><small>${otherConversations.length}</small></header><div>${otherConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}`
              : '<div class="development-empty">No WhatsApp conversations are available.</div>';
        list.querySelectorAll('[data-incoming-conversation]').forEach((button) => {
            button.addEventListener('click', () => {
                state.selectedIncomingConversationKey = button.dataset.incomingConversation || '';
                state.incomingMobileDetail = true;
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
                state.incomingMobileDetail = true;
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
                state.incomingMobileDetail = true;
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
        el('incoming-link-error').textContent = '';
        el('incoming-link-error').classList.add('hidden');
        el('incoming-link-title').textContent = conversation.label;
        el('incoming-link-summary').textContent = conversation.links.length
            ? 'Linked to these boards. You can add another board without removing the existing links.'
            : 'Link this person or group when the conversation belongs to active work.';
        el('incoming-current-links').innerHTML = conversation.links.length
            ? conversation.links
                  .map(
                      (link) =>
                          `<div><span>${actions.escapeHtml(link.board_name || 'Board')}</span><label class="incoming-link-auto"><input type="checkbox" data-incoming-auto="${Number(link.id)}" data-board-id="${Number(link.board_id)}"${link.auto_snapshot ? ' checked' : ''}><span>Auto snapshot</span></label><button type="button" data-incoming-unlink="${Number(link.id)}" data-board-id="${Number(link.board_id)}" aria-label="Unlink ${actions.escapeHtml(link.board_name || 'board')}">Remove</button></div>`
                  )
                  .join('')
            : '<small>Not linked to a board.</small>';
        el('incoming-current-links')
            .querySelectorAll('[data-incoming-unlink]')
            .forEach((button) =>
                button.addEventListener('click', async () => {
                    const removed = await unlinkIncomingChannel(Number(button.dataset.boardId), Number(button.dataset.incomingUnlink));
                    if (removed) {
                        openIncomingLinkDialog(conversation.key);
                        (el('incoming-current-links').querySelector('button') || el('incoming-link-board')).focus();
                    }
                })
            );
        el('incoming-current-links')
            .querySelectorAll('[data-incoming-auto]')
            .forEach((input) =>
                input.addEventListener('change', async () => {
                    await toggleIncomingAutoSnapshot(Number(input.dataset.boardId), Number(input.dataset.incomingAuto), input.checked);
                })
            );
        el('incoming-link-auto').checked = false;
        const linkedBoardIds = new Set(conversation.links.map((link) => Number(link.board_id)));
        const options = context.boards.filter((board) => board.provider === 'decisions' && !linkedBoardIds.has(Number(board.local_id || board.id)));
        el('incoming-link-board').innerHTML =
            '<option value="">Choose board</option>' +
            options.map((board) => `<option value="${Number(board.local_id || board.id)}">${actions.escapeHtml(board.name || 'Board')}</option>`).join('');
        if (!el('incoming-link-dialog').open) el('incoming-link-dialog').showModal();
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
                    auto_snapshot: el('incoming-link-auto').checked
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
        if (button?.disabled) return;
        const item = state.incoming.find((row) => row.key === button?.dataset.incomingKey);
        if (!item) return;
        const actionScope = button.closest('[data-incoming-conversation], .incoming-gmail-reader');
        const boardId = Number(item.board_id || actionScope?.querySelector('[data-incoming-board]')?.value || 0);
        const board = context.boards.find((row) => Number(row.local_id || row.id) === boardId);
        if (!boardId || !board) {
            actions.toast('Choose a board before creating a ticket.', 'error');
            actionScope?.querySelector('[data-incoming-board]')?.focus();
            return;
        }
        const originalLabel = button.textContent;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Creating ticket...';
        try {
            const isGmail = String(item.source || '').toLowerCase() === 'gmail';
            const result = await actions.api(
                isGmail ? '/workflows/incoming/gmail/ingest' : '/workflows/intake/ingest',
                {
                method: 'POST',
                body: isGmail
                    ? {
                        subject: item.subject || item.conversation_label || '',
                        text: item.text || item.snippet || 'Incoming Gmail message',
                        source_thread_id: item.source_thread_id || '',
                        source_message_id: item.source_message_id || '',
                        sender: item.sender || '',
                        board_hint: board?.name || '',
                        project_hint: board?.name || '',
                        force_ticket: true
                    }
                    : {
                    source: item.source,
                    user_text: `Create a ticket: ${item.subject || item.conversation_label || ''}\n\n${item.text || item.snippet || 'Incoming request'}`,
                    source_user_id: item.sender || '',
                    source_thread_id: item.source_thread_id || '',
                    source_message_id: item.source_message_id || '',
                    board_hint: board?.name || '',
                    project_hint: board?.name || '',
                    metadata: {
                        board_id: boardId || undefined,
                        project_id: board?.project_id || undefined,
                        subject: item.subject || item.conversation_label || '',
                        email_provider: item.source,
                        email_sender: item.sender || ''
                    }
                }
            });
            if (!result.decision?.ticket_id) throw new Error(result.decision?.response_text || 'The ticket could not be created.');
            item.ticket_id = result.decision.ticket_id;
            item.board_id = result.decision.board_id || boardId;
            actions.toast(result.decision?.response_text || 'Incoming request was sent to the orchestrator.');
            renderIncomingWorkspace();
            await actions.refreshShell({ preserveConversation: true });
        } catch (error) {
            actions.toast(error.message || 'Could not create a ticket from the incoming message.', 'error');
        } finally {
            button.disabled = false;
            button.removeAttribute('aria-busy');
            button.textContent = originalLabel;
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

    async function toggleIncomingAutoSnapshot(boardId, linkId, enabled) {
        try {
            await actions.api(`/tickets/boards/${boardId}/whatsapp-links/${linkId}`, {
                method: 'PATCH',
                body: { auto_snapshot: enabled }
            });
            await refreshIncomingData({ force: true });
            actions.toast(enabled ? 'Automatic snapshots enabled.' : 'Automatic snapshots turned off.');
        } catch (error) {
            actions.toast(error.message || 'Could not update automatic snapshots.', 'error');
            const conversation = incomingConversations('whatsapp').find((row) => row.key === state.incomingLinkConversationKey);
            if (conversation) openIncomingLinkDialog(conversation.key);
        }
    }

    async function unlinkIncomingChannel(boardId, linkId) {
        const errorNode = el('incoming-link-error');
        errorNode.textContent = '';
        errorNode.classList.add('hidden');
        if (
            !(await actions.confirmAction({
                title: 'Unlink incoming channel',
                message: 'Stop showing this WhatsApp person or group as linked to the board? Stored messages and existing tickets are preserved.',
                confirmLabel: 'Unlink',
                danger: true
            }))
        )
            return false;
        try {
            await actions.api(`/tickets/boards/${boardId}/whatsapp-links/${linkId}`, {
                method: 'DELETE'
            });
            await refreshIncomingData({ force: true });
            actions.toast('Incoming channel unlinked.');
            return true;
        } catch (error) {
            const message = error.message || 'Could not unlink the channel.';
            if (el('incoming-link-dialog').open) {
                errorNode.textContent = message;
                errorNode.classList.remove('hidden');
            } else {
                actions.toast(message, 'error');
            }
            return false;
        }
    }

    // Push updates: /api/ws topic "incoming" sends {type: "incoming_message", channel, message_id}. No polling.
    const seenPushedMessages = new Set();
    let incomingSocket = null;
    let incomingSocketRetry = null;
    let incomingPushTimer = null;
    let incomingSocketDelay = 2000;

    function incomingItemKey(item) {
        return String(item?.key || `${item?.source || ''}:${item?.database_id || item?.source_message_id || item?.id || ''}`);
    }

    // req 2026-10-22: pushed messages are patched into the existing DOM (rows replaced or prepended,
    // reader bubbles appended, de-duplicated by id). Nothing re-renders the workspace and scroll is kept.
    const MAIL_CHANNELS = {
        gmail: { list: 'incoming-gmail-list', reader: 'incoming-gmail-reader', attr: 'data-incoming-gmail-thread', dataKey: 'incomingGmailThread', stateKey: 'selectedGmailThreadKey' },
        mailshot: { list: 'incoming-mailshot-list', reader: 'incoming-mailshot-reader', attr: 'data-incoming-mailshot-thread', dataKey: 'incomingMailshotThread', stateKey: 'selectedMailshotThreadKey' }
    };

    function htmlToNode(html) {
        const template = document.createElement('template');
        template.innerHTML = html.trim();
        return template.content.firstElementChild;
    }

    function whatsappBubbleHtml(item, conversation) {
        return `<div class="incoming-message-bubble${item.from_me ? ' outgoing' : ' incoming'}" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}"><small>${actions.escapeHtml(item.from_me ? 'You' : item.sender || conversation.label)}</small>${incomingMediaHtml(item)}${item.text ? `<p>${actions.escapeHtml(item.text)}</p>` : ''}<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></div>`;
    }

    function mailMessageHtml(item) {
        return mailMessageCardHtml(item);
    }

    function appendMessagesInPlace(container, items, toHtml) {
        if (!container || !items.length) return;
        const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
        const top = container.scrollTop;
        items.forEach((item) => {
            const key = incomingItemKey(item);
            if (container.querySelector(`[data-incoming-msg="${CSS.escape(key)}"]`)) return;
            const node = htmlToNode(toHtml(item));
            if (node) container.appendChild(node);
        });
        container.scrollTop = nearBottom ? container.scrollHeight : top;
    }

    function upsertRow(list, selector, html, bind) {
        if (!list) return;
        const listTop = list.scrollTop;
        const node = htmlToNode(html);
        if (!node) return;
        const existing = list.querySelector(selector);
        if (existing) existing.replaceWith(node);
        else {
            const firstRow = list.querySelector('[data-incoming-conversation], .incoming-gmail-thread');
            if (firstRow) firstRow.parentElement.insertBefore(node, firstRow);
            else {
                list.querySelectorAll('.development-empty, .incoming-reader-empty').forEach((empty) => empty.remove());
                list.appendChild(node);
            }
        }
        bind(node);
        list.scrollTop = listTop;
    }

    function patchIncomingInPlace(fresh) {
        const freshKeys = new Set(fresh.map(incomingItemKey));
        const isFresh = (item) => freshKeys.has(incomingItemKey(item));
        const oldestFirst = (items) => items.filter(isFresh).slice().reverse();
        ['whatsapp', 'gmail', 'mailshot'].forEach((source) => {
            const conversations = incomingConversations(source);
            const count = el(`incoming-${source}-count`);
            if (count) count.textContent = String(conversations.length);
            conversations
                .filter((conversation) => conversation.messages.some(isFresh))
                .forEach((conversation) => {
                    if (source === 'whatsapp') {
                        upsertRow(el('incoming-list'), `[data-incoming-conversation="${CSS.escape(conversation.key)}"]`, incomingConversationRowHtml(conversation), (row) => {
                            row.addEventListener('click', () => {
                                state.selectedIncomingConversationKey = row.dataset.incomingConversation || '';
                                state.incomingMobileDetail = true;
                                renderIncomingWorkspace();
                            });
                            row.addEventListener('contextmenu', (event) => {
                                event.preventDefault();
                                openIncomingLinkDialog(row.dataset.incomingConversation || '');
                            });
                            bindIncomingActions(row);
                        });
                        if (conversation.key === state.selectedIncomingConversationKey) {
                            appendMessagesInPlace(el('incoming-whatsapp-reader')?.querySelector('.incoming-whatsapp-messages'), oldestFirst(conversation.messages), (item) => whatsappBubbleHtml(item, conversation));
                        }
                        return;
                    }
                    const config = MAIL_CHANNELS[source];
                    upsertRow(el(config.list), `[${config.attr}="${CSS.escape(conversation.key)}"]`, mailThreadButtonHtml(conversation, state[config.stateKey], config.attr), (row) => {
                        row.addEventListener('click', () => {
                            state[config.stateKey] = row.dataset[config.dataKey] || '';
                            state.incomingMobileDetail = true;
                            renderIncomingWorkspace();
                        });
                    });
                    if (conversation.key === state[config.stateKey]) {
                        appendMessagesInPlace(el(config.reader)?.querySelector('.incoming-gmail-messages'), oldestFirst(conversation.messages), mailMessageHtml);
                    }
                });
        });
    }

    async function applyPushedIncoming() {
        try {
            const data = await actions.api('/workflows/studio/incoming?limit=150');
            const known = new Set((state.incoming || []).map(incomingItemKey));
            const seen = new Set();
            const fresh = (data.items || []).filter((item) => {
                const key = incomingItemKey(item);
                if (known.has(key) || seen.has(key)) return false;
                seen.add(key);
                return true;
            });
            if (!fresh.length) return;
            state.incoming = [...fresh, ...(state.incoming || [])];
            state.incomingLinks = data.links || state.incomingLinks;
            state.incomingChannels = data.channels || state.incomingChannels;
            state.incomingLoadedAt = Date.now();
            if (context.workspaceMode === 'incoming') patchIncomingInPlace(fresh);
        } catch (_error) {
            /* Keep the current view; the next pushed message retries. */
        }
    }

    function removeProposalByRequest(requestId) {
        if (requestId == null || requestId === '') return false;
        const before = state.incomingProposals || [];
        const next = before.filter((row) => {
            const id = String(row.id || '');
            if (id === String(requestId)) return false;
            if (id === `tensology:${requestId}`) return false;
            return true;
        });
        if (next.length === before.length) return false;
        state.incomingProposals = next;
        if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
        return true;
    }

    function handleIncomingPush(message) {
        if (message?.type === 'proposal_changed' || message?.type === 'development_changed') {
            const status = String(message.status || '').toLowerCase();
            // Dismiss / thread creation already updated server cache surgically —
            // drop the card locally and skip a full Tensology refetch.
            if (['dismissed', 'created', 'started', 'sent_to_decisions', 'approved'].includes(status)) {
                removeProposalByRequest(message.request_id);
                if (message.type === 'development_changed') {
                    actions.refreshShell({ preserveConversation: true, background: true });
                }
                return;
            }
            clearTimeout(incomingPushTimer);
            incomingPushTimer = setTimeout(() => {
                state.incomingLoadedAt = 0;
                actions.refreshShell({ preserveConversation: true, background: true });
                // New proposals invalidate the server cache; force rebuild once.
                if (context.workspaceMode === 'incoming') refreshIncomingData({ force: true });
            }, 250);
            return;
        }
        if (message?.type !== 'incoming_message') return;
        const id = `${message.channel || ''}:${message.message_id ?? ''}`;
        if (message.message_id != null && seenPushedMessages.has(id)) return;
        seenPushedMessages.add(id);
        clearTimeout(incomingPushTimer);
        incomingPushTimer = setTimeout(applyPushedIncoming, 250);
    }

    function connectIncomingPush() {
        if (incomingSocket && incomingSocket.readyState <= WebSocket.OPEN) return;
        const token = document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';
        const query = new URLSearchParams();
        if (token) query.set('internal_token', token);
        const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/ws?${query}`);
        incomingSocket = socket;
        socket.onopen = () => {
            incomingSocketDelay = 2000;
            socket.send(JSON.stringify({ subscribe_topic: 'incoming' }));
            // Reconcile from the server list cache; Resync remains the forced rebuild.
            state.incomingLoadedAt = 0;
            if (context.workspaceMode === 'incoming') refreshIncomingData();
            actions.refreshShell({ preserveConversation: true, background: true });
        };
        socket.onmessage = (event) => {
            try {
                handleIncomingPush(JSON.parse(event.data));
            } catch (_) {
                /* ignore malformed frames */
            }
        };
        socket.onclose = () => {
            if (incomingSocket !== socket) return;
            incomingSocket = null;
            clearTimeout(incomingSocketRetry);
            incomingSocketRetry = setTimeout(connectIncomingPush, incomingSocketDelay);
            incomingSocketDelay = Math.min(incomingSocketDelay * 2, 60000);
        };
    }

    // 2026-10-22 resync: latest-wins loader. A forced refresh supersedes an in-flight one; stale responses are dropped.
    let incomingLoadSeq = 0;
    function setResyncBusy(busy) {
        state.incomingProposalsLoading = busy;
        el('incoming-proposals-list')?.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (busy && !(state.incomingProposals || []).length && context.workspaceMode === 'incoming') renderIncomingWorkspace();
        const button = el('incoming-resync');
        if (!button) return;
        button.disabled = busy;
        button.classList.toggle('loading', busy);
        button.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
    function refreshIncomingData(options) {
        if (state.incomingLoad && !options?.force) return state.incomingLoad;
        if (!options?.force && state.incomingLoadedAt && Date.now() - state.incomingLoadedAt < 15_000) return Promise.resolve();
        connectIncomingPush();
        const seq = ++incomingLoadSeq;
        const force = Boolean(options?.force);
        setResyncBusy(true);
        const load = (async () => {
            // Proposals and channels load in parallel; paint proposals as soon as
            // their own response returns. Soft load never sends refresh=1 / cache-bust.
            const proposalsUrl = force
                ? `/workflows/studio/incoming/proposals?limit=150&refresh=1`
                : `/workflows/studio/incoming/proposals?limit=150`;
            const channelsUrl = `/workflows/studio/incoming?limit=150`;
            const proposalsReq = actions.api(proposalsUrl, force ? { cache: 'no-store' } : undefined);
            const channelsReq = actions.api(channelsUrl, force ? { cache: 'no-store' } : undefined);

            const paintProposals = proposalsReq.then((result) => {
                if (seq !== incomingLoadSeq) return;
                state.incomingProposals = result.proposals || [];
                state.incomingProposalsError = result.error || '';
                const autoAccept = el('incoming-auto-accept');
                if (autoAccept && !autoAccept.disabled) autoAccept.checked = result.auto_accept === true;
                setResyncBusy(false);
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            }, () => {
                if (seq !== incomingLoadSeq) return;
                state.incomingProposals = [];
                state.incomingProposalsError = 'Proposals are temporarily unavailable.';
                setResyncBusy(false);
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            });

            const paintChannels = channelsReq.then((data) => {
                if (seq !== incomingLoadSeq) return;
                state.incoming = data.items || [];
                state.incomingLinks = data.links || [];
                state.incomingChannels = data.channels || {};
                state.incomingError = '';
                actions.renderSidebar();
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            }, () => {
                if (seq !== incomingLoadSeq) return;
                state.incoming = [];
                state.incomingLinks = [];
                state.incomingChannels = {};
                state.incomingError = 'Incoming is temporarily unavailable. Restart DecisionsAI and try again.';
                actions.renderSidebar();
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            });

            await Promise.allSettled([paintProposals, paintChannels]);
            if (seq !== incomingLoadSeq) return;
            state.incomingLoadedAt = Date.now();
            state.incomingLoad = null;
            setResyncBusy(false);
        })();
        state.incomingLoad = load;
        return load;
    }

    // Event-driven Incoming: WS covers dismiss/create/new proposals. Soft refresh on
    // tab focus only (hits the server list cache). Resync button stays force=true.
    function incomingVisible() {
        return context.workspaceMode === 'incoming' && document.visibilityState === 'visible';
    }
    function startIncomingAutoRefresh() {
        const onReturn = () => {
            if (incomingVisible() && !state.incomingLoad && (!state.incomingLoadedAt || Date.now() - state.incomingLoadedAt > 5_000)) {
                refreshIncomingData();
            }
        };
        document.addEventListener('visibilitychange', onReturn);
        window.addEventListener('focus', onReturn);
    }

    function bindEvents() {
        connectIncomingPush();
        document.querySelectorAll('[data-incoming-filter]').forEach((button) =>
            button.addEventListener('click', () => {
                state.incomingFilter = button.dataset.incomingFilter || 'all';
                state.incomingMobileDetail = false;
                renderIncomingWorkspace();
            })
        );
        document.querySelectorAll('[data-incoming-back]').forEach((button) => button.addEventListener('click', () => {
            state.incomingMobileDetail = false;
            el('incoming-workspace').classList.remove('incoming-detail-open');
            el(`incoming-panel-${state.incomingFilter}`)?.querySelector('.selected')?.focus();
        }));
        el('incoming-resync')?.addEventListener('click', () => refreshIncomingData({ force: true }));
        el('incoming-auto-accept')?.addEventListener('change', async (event) => {
            const control = event.target;
            const enabled = control.checked;
            control.disabled = true;
            try {
                const result = await actions.api('/workflows/studio/incoming/proposals/auto-accept', {
                    method: 'PUT', body: { enabled }
                });
                control.checked = result.auto_accept === true;
                actions.toast(enabled ? 'Auto-accept enabled for all projects.' : 'Auto-accept disabled.');
            } catch (error) {
                control.checked = !enabled;
                actions.toast(error.message || 'Could not save auto-accept.', 'error');
            } finally {
                control.disabled = false;
            }
            refreshIncomingData({ force: true });
        });
        startIncomingAutoRefresh();
        el('incoming-link-form').addEventListener('submit', (event) => {
            event.preventDefault();
            linkIncomingConversation();
        });
    }
    if (typeof window !== 'undefined') {
        window.DecisionsIncoming = Object.assign(window.DecisionsIncoming || {}, { openFilePreview });
    }
    return {
        bindEvents,
        incomingConversations,
        openFilePreview,
        refreshIncomingData,
        renderIncomingWorkspace,
        unlinkIncomingChannel
    };
}

// 2026-10-22 height: size the visible Incoming channel panel in JS so its bottom is flush with the
// viewport bottom at any window height (height = innerHeight - panel top). Inner lists scroll internally.
function fitIncomingPanels() {
    const ws = document.getElementById('incoming-workspace');
    if (!ws) return;
    ws.querySelectorAll('.incoming-folder-panel').forEach(panel => {
        if (panel.classList.contains('hidden') || !panel.getClientRects().length) { panel.style.removeProperty('height'); return; }
        const top = panel.getBoundingClientRect().top;
        // 2026-10-22 incomingpad: stop at the workspace's bottom gutter (CSS padding-bottom on
        // .scheduled-workspace-inner) and inside the frame's bottom border, instead of the raw viewport bottom.
        const frame = panel.parentElement;
        const inner = frame && frame.closest('.scheduled-workspace-inner');
        const gutter = inner ? (parseFloat(getComputedStyle(inner).paddingBottom) || 0) : 0;
        const frameBorder = frame ? (parseFloat(getComputedStyle(frame).borderBottomWidth) || 0) : 0;
        const limit = Math.min(window.innerHeight - gutter, frame.getBoundingClientRect().bottom) - frameBorder;
        let h = Math.max(0, Math.floor(limit - top));
        panel.style.setProperty('height', `${h}px`);
        panel.style.setProperty('flex', '0 0 auto');
        // Trim anything (padding/border/margin of the panel or its wrappers up to the shell) still past the viewport.
        let over = 0;
        for (let node = panel; node && node.id !== 'studio-shell'; node = node.parentElement) {
            const mb = parseFloat(getComputedStyle(node).marginBottom) || 0;
            over = Math.max(over, node.getBoundingClientRect().bottom + mb - (node === panel ? limit : window.innerHeight));
        }
        if (over > 0.5) panel.style.setProperty('height', `${Math.max(0, h - Math.ceil(over))}px`);
    });
}
let fitIncomingFrame = 0;
function scheduleFitIncomingPanels() {
    if (fitIncomingFrame) return;
    fitIncomingFrame = requestAnimationFrame(() => { fitIncomingFrame = 0; fitIncomingPanels(); });
}
if (typeof window !== 'undefined') {
    window.addEventListener('resize', scheduleFitIncomingPanels);
    const startFitIncoming = () => {
        scheduleFitIncomingPanels();
        const ws = document.getElementById('incoming-workspace');
        if (!ws) return;
        new MutationObserver(scheduleFitIncomingPanels).observe(ws, { subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] });
        if (ws.parentElement) new MutationObserver(scheduleFitIncomingPanels).observe(ws.parentElement, { attributes: true, subtree: false, childList: true });
        new MutationObserver(scheduleFitIncomingPanels).observe(ws, { attributes: true, attributeFilter: ['class', 'hidden', 'style'] });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startFitIncoming, { once: true });
    else startFitIncoming();
}
