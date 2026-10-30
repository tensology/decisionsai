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

    function incomingConversationRowHtml(conversation) {
        const latest = conversation.latest || {};
        const selected = state.selectedIncomingConversationKey === conversation.key;
        const direction = latest.from_me ? 'You: ' : '';
        return `<button type="button" class="incoming-conversation-row${conversation.pendingCount ? ' pending' : ''}${selected ? ' selected' : ''}" data-incoming-conversation="${actions.escapeHtml(conversation.key)}" aria-pressed="${selected}">${incomingAvatarHtml(conversation)}<span class="incoming-row-copy"><span class="incoming-row-title"><strong>${actions.escapeHtml(conversation.label)}</strong><time>${actions.escapeHtml(actions.formatDateTime(latest.created_at))}</time></span><span class="incoming-row-preview">${actions.escapeHtml(direction + (latest.text || (latest.media_type ? `${latest.media_type} attachment` : 'No message text')))}</span><span class="incoming-row-meta">${incomingLinkPillsHtml(conversation.links)}${conversation.pendingCount ? `<b>${conversation.pendingCount} new</b>` : ''}</span></span><span class="incoming-row-more" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}" title="View information or link to a board">•••</span></button>`;
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
                    `<div class="incoming-message-bubble${item.from_me ? ' outgoing' : ' incoming'}" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}"><small>${actions.escapeHtml(item.from_me ? 'You' : item.sender || conversation.label)}</small>${incomingMediaHtml(item)}${item.text ? `<p>${actions.escapeHtml(item.text)}</p>` : ''}<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></div>`
            )
            .join('');
        const snapshotActions = conversation.links
            .map(
                (link) =>
                    `<button type="button" data-incoming-snapshot="${Number(link.id)}" data-board-id="${Number(link.board_id)}"${conversation.canSnapshot ? '' : ' disabled'}>Snapshot to ${actions.escapeHtml(link.board_name || 'board')}</button>`
            )
            .join('');
        const snapshotHelp = conversation.hasSnapshot ? 'Includes messages since the previous snapshot.' : 'The first snapshot includes the last 2 days.';
        return `<header class="incoming-whatsapp-reader-header">${incomingAvatarHtml(conversation, true)}<div><h3>${actions.escapeHtml(conversation.label)}</h3><p>${conversation.chatType === 'group' ? 'WhatsApp group' : 'WhatsApp contact'} · ${conversation.messages.length} recent messages</p><div class="incoming-reader-links">${incomingLinkPillsHtml(conversation.links)}</div></div><button type="button" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}">•••</button></header><div class="incoming-whatsapp-messages">${messages}</div><footer><small>${conversation.links.length ? actions.escapeHtml(snapshotHelp) : 'This conversation is not linked to work.'}</small><span>${snapshotActions}</span></footer>`;
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
                return `<section class="incoming-gmail-message" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}"><header><div><strong>${actions.escapeHtml(item.sender || 'Unknown sender')}</strong><small>${actions.escapeHtml(item.recipient ? `To ${item.recipient}` : '')}</small></div><time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></header><div class="incoming-gmail-body">${actions.escapeHtml(item.text || item.snippet || 'No message text')}</div>${attachments}</section>`;
            })
            .join('');
        return `<header class="incoming-gmail-reader-header"><div><h3>${actions.escapeHtml(conversation.label || '(No subject)')}</h3><p>${actions.escapeHtml(latest.sender || 'Unknown sender')} · ${conversation.messages.length} message${conversation.messages.length === 1 ? '' : 's'}</p></div></header><div class="incoming-gmail-messages">${messages}</div><footer>${actionHtml}</footer>`;
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
        const messageCount = (proposal.evidence || []).length;
        const mediaCount = (proposal.attachments || []).length;
        const push = proposal.project_id
            ? `<button type="button" class="proposal-btn primary" data-incoming-proposal-thread="${proposalId}" title="Creates the thread and hands it to Decisions, like Send to Decisions.">Create Thread</button>`
            : `<span class="proposal-btn muted" title="Link this conversation to a project first">No project</span>`;
        const dismiss = `<button type="button" class="proposal-btn ghost" data-incoming-proposal-dismiss="${proposalId}" title="Dismiss this proposal">Dismiss</button>`;
        const meta = [
            messageCount ? `${messageCount} message${messageCount === 1 ? '' : 's'}` : '',
            mediaCount ? `${mediaCount} attachment${mediaCount === 1 ? '' : 's'}` : ''
        ].filter(Boolean).join(' · ');
        return `<article class="proposal-card" data-incoming-proposal-open="${proposalId}" tabindex="0" role="button" aria-label="Open evidence for ${actions.escapeHtml(proposal.title || 'proposal')}">
            <div class="proposal-head">
                <p class="proposal-project${project ? '' : ' none'}">${actions.escapeHtml(project || 'Not linked to a project')}</p>
                <h3 class="proposal-title">${actions.escapeHtml(proposal.title || 'Proposal')}</h3>
                <span class="proposal-source" title="${actions.escapeHtml(tooltip)}" data-incoming-proposal-open="${proposalId}">${incomingSourceGlyph(proposal.source)}${actions.escapeHtml(source)}</span>
            </div>
            ${description ? `<p class="proposal-desc">${actions.escapeHtml(description)}</p>` : ''}
            ${meta ? `<p class="proposal-meta">${actions.escapeHtml(meta)}</p>` : ''}
            <div class="proposal-actions">${push}${dismiss}</div>
        </article>`;
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

    function proposalFilesHtml(proposal) {
        const items = proposal.attachments || [];
        if (!items.length) return '<p class="proposal-empty">No attachments on this proposal.</p>';
        const token = incomingInternalToken();
        const media = items.map((a) => {
            if (!a.api_path) return '';
            const url = `/api/workflows/studio/incoming/proposals/attachment?path=${encodeURIComponent(a.api_path)}&internal_token=${encodeURIComponent(token)}`;
            const isImage = String(a.mime_type || '').startsWith('image/');
            const caption = a.caption || a.transcript || '';
            return isImage
                ? `<a class="proposal-media" href="${url}" target="_blank" rel="noopener" title="${actions.escapeHtml(caption || a.name)}"><img src="${url}" alt="${actions.escapeHtml(a.name)}" /></a>`
                : `<a class="proposal-file" href="${url}" target="_blank" rel="noopener">${actions.escapeHtml(a.name)}${a.kind ? ` · ${actions.escapeHtml(a.kind)}` : ''}</a>`;
        }).join('');
        return `<div class="proposal-media-grid">${media}</div>`;
    }

    function closeProposalDetail() {
        el('incoming-proposal-modal')?.remove();
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
            ['Project', project],
            ['Participant', proposal.participant || conversation.participant_name || conversation.participant_address || ''],
            ['Channel', conversation.channel || source],
            ['Priority', proposal.priority || ''],
            ['Source thread', conversation.thread_id || ''],
        ].filter((row) => row[1]);
        const details = `<dl class="proposal-modal-meta">${meta.map(([k, v]) => `<div><dt>${actions.escapeHtml(k)}</dt><dd>${actions.escapeHtml(v)}</dd></div>`).join('')}</dl>
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
                ${proposal.project_id ? `<button type="button" class="proposal-btn primary" data-proposal-thread="${actions.escapeHtml(proposal.id || '')}">Create Thread</button>` : ''}
            </footer>
        </div>`;
        document.body.appendChild(overlay);
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
            proposalsList.innerHTML = state.incomingProposalsError
                ? `<div class="development-empty">${actions.escapeHtml(state.incomingProposalsError)}</div>`
                : (state.incomingProposals || []).length
                  ? (state.incomingProposals || []).map(incomingProposalRowHtml).join('')
                  : '<div class="development-empty">No proposals right now.</div>';
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
              ? `${linkedConversations.length ? `<section class="incoming-channel-section"><header><h2>Linked work</h2><small>${linkedConversations.length}</small></header><div>${linkedConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}${otherConversations.length ? `<section class="incoming-channel-section other"><header><h2>Other conversations</h2><small>${otherConversations.length}</small></header><div>${otherConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}`
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
        const attachments =
            Array.isArray(item.attachments) && item.attachments.length
                ? `<small class="incoming-gmail-attachments">${item.attachments.length} attachment${item.attachments.length === 1 ? '' : 's'}: ${actions.escapeHtml(item.attachments.map((attachment) => attachment.filename || 'Attachment').join(', '))}</small>`
                : '';
        return `<section class="incoming-gmail-message" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}"><header><div><strong>${actions.escapeHtml(item.sender || 'Unknown sender')}</strong><small>${actions.escapeHtml(item.recipient ? `To ${item.recipient}` : '')}</small></div><time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></header><div class="incoming-gmail-body">${actions.escapeHtml(item.text || item.snippet || 'No message text')}</div>${attachments}</section>`;
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

    function handleIncomingPush(message) {
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
        setResyncBusy(true);
        const load = (async () => {
            let data = null;
            let failed = false;
            // Fetch the channel list and the proposals in parallel so Proposals
            // is not gated on the slower channel load.
            const [incomingResult, proposalResult] = await Promise.allSettled([
                actions.api(`/workflows/studio/incoming?limit=150&_=${Date.now()}`, { cache: 'no-store' }),
                actions.api(`/workflows/studio/incoming/proposals?limit=150&_=${Date.now()}`, { cache: 'no-store' })
            ]);
            if (seq !== incomingLoadSeq) return;
            if (incomingResult.status === 'fulfilled') {
                data = incomingResult.value;
            } else {
                failed = true;
            }
            if (proposalResult.status === 'fulfilled') {
                state.incomingProposals = proposalResult.value.proposals || [];
                state.incomingProposalsError = proposalResult.value.error || '';
            } else {
                state.incomingProposals = [];
                state.incomingProposalsError = 'Proposals are temporarily unavailable.';
            }
            if (failed) {
                state.incoming = [];
                state.incomingLinks = [];
                state.incomingChannels = {};
                state.incomingError = 'Incoming is temporarily unavailable. Restart DecisionsAI and try again.';
            } else {
                state.incoming = data.items || [];
                state.incomingLinks = data.links || [];
                state.incomingChannels = data.channels || {};
                state.incomingError = '';
            }
            state.incomingLoadedAt = Date.now();
            state.incomingLoad = null;
            setResyncBusy(false);
            actions.renderSidebar();
            if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
        })();
        state.incomingLoad = load;
        return load;
    }

    // 2026-10-22 resync: auto-refresh every 90s while Incoming is visible, and on tab focus/visibility.
    const INCOMING_AUTO_REFRESH_MS = 90_000;
    function incomingVisible() {
        return context.workspaceMode === 'incoming' && document.visibilityState === 'visible';
    }
    function startIncomingAutoRefresh() {
        setInterval(() => {
            if (incomingVisible() && !state.incomingLoad) refreshIncomingData({ force: true });
        }, INCOMING_AUTO_REFRESH_MS);
        const onReturn = () => {
            if (incomingVisible() && !state.incomingLoad && (!state.incomingLoadedAt || Date.now() - state.incomingLoadedAt > 5_000)) {
                refreshIncomingData({ force: true });
            }
        };
        document.addEventListener('visibilitychange', onReturn);
        window.addEventListener('focus', onReturn);
    }

    function bindEvents() {
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
        startIncomingAutoRefresh();
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
