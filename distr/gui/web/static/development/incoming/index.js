const QUICK_REPLY_PATH = '/workflows/studio/incoming/mail/reply';
const QUICK_REPLY_UNKNOWN = 'Send was not confirmed. Check the original conversation before sending again.';
const quickReplyStorageKey = key => `decisions.incoming.quick-reply.v1:${key}`;

export function compareIncomingMessagesNewestFirst(left, right) {
    const time = (item) => Date.parse(item.created_at || '') || 0;
    const difference = time(right) - time(left);
    if (difference) return difference;
    // A batch arrives newest first, so local database ids may run backwards.
    // Use the relay's original sequence when both messages retain it.
    const leftSequence = Number(left.source_sequence);
    const rightSequence = Number(right.source_sequence);
    if (left.source === 'whatsapp' && right.source === 'whatsapp'
        && leftSequence > 0 && rightSequence > 0) return rightSequence - leftSequence;
    return Number(right.database_id || 0) - Number(left.database_id || 0);
}

export function incomingConversationLabel(conversation) {
    const messages = conversation.messages || [];
    const latest = messages[0] || {};
    const linked = messages.find((item) => item.link_id || item.board_id || item.links?.length);
    const synthetic = conversation.syntheticLinks || [];
    const group = (linked?.chat_type || latest.chat_type) === 'group' || String(conversation.threadId || '').endsWith('@g.us');
    // An outbound private message names us, not the person we are talking to.
    const contact = conversation.source === 'whatsapp' && !group && !linked && !synthetic.length
        ? messages.find((item) => !item.from_me)
        : null;
    return linked?.conversation_label || synthetic[0]?.label
        || contact?.conversation_label || contact?.sender
        || latest.conversation_label || latest.thread_label || latest.sender || conversation.threadId;
}

export function proposalSourceStartTime(proposal) {
    const supplied = Date.parse(proposal.source_started_at || '');
    if (Number.isFinite(supplied)) return supplied;
    const values = proposal.message_ids?.length ? proposal.message_ids : proposal.source_message_ids;
    const ids = values?.length ? new Set(values.map(String)) : null;
    const dates = (proposal.evidence || []).filter(row => ids === null || ids.has(String(row.id)))
        .map(row => Date.parse(row.sent_at || '')).filter(Number.isFinite);
    return dates.length ? Math.min(...dates) : null;
}

export function compareIncomingProposalsNewestFirst(left, right) {
    const first = proposalSourceStartTime(left);
    const second = proposalSourceStartTime(right);
    if (first === null && second !== null) return 1;
    if (first !== null && second === null) return -1;
    if (first !== null && second !== null && first !== second) return second - first;
    const leftId = String(left.id || left.dedupe_key || '');
    const rightId = String(right.id || right.dedupe_key || '');
    return leftId < rightId ? -1 : leftId > rightId ? 1 : 0;
}

// Owns incoming rendering, interactions, and private view state.
// 2026-11-11 attachment icons: one shared file-type icon for the Gmail/Mailshot attachment bar and the preview title.
const FILE_TYPE_STYLES = {
    pdf: ['PDF', '#e5484d'],
    word: ['DOC', '#2b6fd6'],
    sheet: ['XLS', '#1f9d55'],
    slides: ['PPT', '#e8702a'],
    image: ['IMG', '#8b5cf6'],
    audio: ['AUD', '#d946ef'],
    video: ['VID', '#0ea5e9'],
    archive: ['ZIP', '#b7791f'],
    code: ['CODE', '#0d9488'],
    text: ['TXT', '#64748b'],
    file: ['FILE', '#6b7280'],
};
const FILE_TYPE_EXTENSIONS = {
    word: ['doc', 'docx', 'docm', 'dot', 'dotx', 'odt', 'rtf', 'pages'],
    sheet: ['xls', 'xlsx', 'xlsm', 'xlsb', 'ods', 'csv', 'tsv', 'numbers'],
    slides: ['ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'odp', 'key'],
    image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'heic', 'heif', 'bmp', 'tif', 'tiff', 'svg', 'ico'],
    audio: ['mp3', 'm4a', 'wav', 'ogg', 'oga', 'opus', 'aac', 'flac', 'amr', 'weba'],
    video: ['mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi', '3gp', 'wmv'],
    archive: ['zip', '7z', 'rar', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst'],
    code: ['js', 'mjs', 'ts', 'tsx', 'jsx', 'py', 'rb', 'php', 'java', 'kt', 'swift', 'go', 'rs', 'c', 'h', 'cpp', 'cs', 'sh', 'sql', 'html', 'htm', 'css', 'xml', 'json', 'yaml', 'yml', 'toml', 'ini'],
    text: ['txt', 'md', 'log', 'eml', 'vcf', 'ics'],
};

export function attachmentFileKind(item) {
    const mime = String(item?.mime_type || item?.type || '').toLowerCase().split(';', 1)[0].trim();
    const name = String(item?.filename || item?.name || '').toLowerCase().trim();
    const ext = (name.match(/\.([a-z0-9]+)$/) || ['', ''])[1];
    const byExt = (kind) => FILE_TYPE_EXTENSIONS[kind].includes(ext);
    if (mime === 'application/pdf' || ext === 'pdf') return 'pdf';
    if (/msword|wordprocessingml|opendocument\.text|\/rtf$/.test(mime) || byExt('word')) return 'word';
    if (/ms-excel|spreadsheetml|opendocument\.spreadsheet|^text\/(csv|tab-separated-values)$/.test(mime) || byExt('sheet')) return 'sheet';
    if (/ms-powerpoint|presentationml|opendocument\.presentation/.test(mime) || byExt('slides')) return 'slides';
    if (mime.startsWith('image/') || byExt('image')) return 'image';
    if (mime.startsWith('audio/') || byExt('audio')) return 'audio';
    if (mime.startsWith('video/') || byExt('video')) return 'video';
    if (/zip|x-7z|x-rar|vnd\.rar|x-tar|gzip|x-bzip|x-xz/.test(mime) || byExt('archive')) return 'archive';
    if (/javascript|json|xml|x-python|x-sh|typescript|^text\/(html|css|x-)/.test(mime) || byExt('code')) return 'code';
    if (mime.startsWith('text/') || byExt('text')) return 'text';
    return 'file';
}

export function fileTypeIconSvg(kind, size = 28) {
    const key = Object.prototype.hasOwnProperty.call(FILE_TYPE_STYLES, kind) ? kind : 'file';
    const [label, color] = FILE_TYPE_STYLES[key];
    const height = Number(size) || 28;
    const width = Math.round(height * 24 / 28);
    return `<svg class="file-type-icon file-type-${key}" data-file-kind="${key}" width="${width}" height="${height}" viewBox="0 0 24 28" aria-hidden="true" focusable="false">`
        + '<path d="M3.5 1h11.5l6.5 6.5V25.5a1.5 1.5 0 0 1-1.5 1.5h-16.5A1.5 1.5 0 0 1 2 25.5v-23A1.5 1.5 0 0 1 3.5 1z" fill="#f8fafc" stroke="#cbd5e1"/>'
        + '<path d="M15 1v5a1.5 1.5 0 0 0 1.5 1.5h5" fill="#e2e8f0" stroke="#cbd5e1"/>'
        + `<rect x="0.5" y="14.5" width="20" height="8.5" rx="1.75" fill="${color}"/>`
        + `<text x="10.5" y="20.6" text-anchor="middle" font-family="Quicksand, system-ui, sans-serif" font-size="${label.length > 3 ? 5.2 : 6}" font-weight="700" fill="#fff">${label}</text></svg>`;
}

export function createIncoming({ context, actions, el, shared: { fileDropZoneHtml, proposalThreadToast } }) {
    let dismissExpiredBusy = false;
    const proposalMutations = new Set();
    const proposalSpokenText = new Map();
    const quickReplyDrafts = new Map();
    const whatsappReaders = new Map();
    let proposalReadRequest = null;
    let incomingOperation = '';
    let manualCheckBusy = false;
    let checkStatusTimer = null;
    let checkStatusGeneration = 0;
    let incomingChecks = null;
    let incomingCheckError = '';

    function checksAreRunning() {
        return Object.values(incomingChecks || {}).some(row => ['queued', 'running'].includes(row.source_state)
            || ['queued', 'running'].includes(row.recalibration?.state));
    }

    function renderCheckStatus() {
        const node = el('incoming-check-status');
        if (!node) return;
        const rows = Object.values(incomingChecks || {});
        const failed = rows.some(row => row.source_state === 'failed' || ['failed', 'partial'].includes(row.recalibration?.state));
        const checking = checksAreRunning();
        const updated = rows.reduce((sum, row) => sum + Number(row.recalibration?.updated || 0), 0);
        node.classList.toggle('hidden', !incomingChecks && !incomingCheckError);
        node.classList.toggle('error', Boolean(incomingCheckError) || failed);
        node.textContent = incomingCheckError || (checking ? 'Checking sources and proposal project assignments in the background...'
            : failed ? 'Incoming check incomplete. Some sources or proposals could not be checked. Try Check incoming again.'
            : `Incoming checked. ${updated} proposal${updated === 1 ? '' : 's'} updated.`);
    }

    function watchIncomingCheck(generation, remaining = 240) {
        if (checkStatusTimer) clearTimeout(checkStatusTimer);
        checkStatusTimer = setTimeout(async () => {
            checkStatusTimer = null;
            if (generation !== checkStatusGeneration || context.workspaceMode !== 'incoming' || !remaining) return;
            if (document.hidden) { watchIncomingCheck(generation, remaining - 1); return; }
            try {
                const result = await actions.api('/workflows/studio/incoming/check');
                if (generation !== checkStatusGeneration) return;
                incomingChecks = result.checks || null;
                incomingCheckError = '';
                renderCheckStatus();
                if (checksAreRunning()) watchIncomingCheck(generation, remaining - 1);
                else await refreshIncomingData({ force: true });
            } catch (error) {
                incomingCheckError = error.data?.error || 'Could not check background progress. Try Check incoming again.';
                renderCheckStatus();
            }
        }, 5000);
    }

    async function checkIncoming() {
        if (manualCheckBusy || incomingOperation) return;
        manualCheckBusy = true;
        const generation = ++checkStatusGeneration;
        setResyncBusy(true);
        incomingCheckError = '';
        try {
            const result = await actions.api('/workflows/studio/incoming/check', { method: 'POST', body: {} });
            if (result?.success === false) throw new Error(result.error || 'Incoming checking could not be started.');
            incomingChecks = result.checks || null;
            await refreshIncomingData({ force: true });
            if (checksAreRunning()) watchIncomingCheck(generation);
        } catch (error) {
            incomingCheckError = error.data?.error || error.message || 'Incoming checking could not be started.';
            await refreshIncomingData({ force: true });
        } finally {
            manualCheckBusy = false;
            setResyncBusy(false);
            renderCheckStatus();
        }
    }
    const state = {
        incoming: [],
        incomingLinks: [],
        incomingChannels: {},
        incomingProposals: [],
        incomingPendingMerges: [],
        incomingProposalsError: '',
        incomingProposalSync: null,
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
                conversation.messages.sort(compareIncomingMessagesNewestFirst);
                const latest = conversation.messages[0] || {};
                const link = conversation.messages.find((item) => item.link_id || item.board_id || item.links?.length) || latest;
                conversation.latest = latest;
                conversation.label = incomingConversationLabel(conversation);
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
    const seenConversations = {};

    function incomingSeenMap() {
        try {
            const raw = JSON.parse(localStorage.getItem(INCOMING_SEEN_KEY) || '{}');
            return { ...(raw && typeof raw === 'object' ? raw : {}), ...seenConversations };
        } catch (_) {
            return { ...seenConversations };
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
        map[conversationKey] = latest || Date.now();
        seenConversations[conversationKey] = map[conversationKey];
        try { localStorage.setItem(INCOMING_SEEN_KEY, JSON.stringify(map)); } catch (_) { /* In-memory acknowledgement still works. */ }
    }

    function selectIncomingConversation(key) {
        state.selectedIncomingConversationKey = key || '';
        const conversation = incomingConversations('whatsapp').find(row => row.key === key);
        if (conversation) markConversationSeen(key, conversation);
        state.incomingMobileDetail = true;
        renderIncomingWorkspace();
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

    // 2026-11-12 WhatsApp voice-note Transcribe: same endpoint, labels and states as the Kanban
    // WhatsApp Transcribe action and the phone remote app. POST .../analyze-media (no body) saves
    // "[Transcription] text" into the message caption, so later loads show the text without a button.
    const WA_VOICE_PLACEHOLDER = /^(?:voice|audio|ptt) attachment$/i;
    const WA_TRANSCRIPT_PREFIX = /^\[(?:Transcription|Transcript)\]\s*/i;
    const whatsappTranscribeJobs = new Map();

    function isWhatsAppVoiceItem(item) {
        const type = String(item?.media_type || '').toLowerCase();
        const mime = String(item?.media_mime_type || '').toLowerCase();
        return ['audio', 'voice', 'ptt'].includes(type) || mime.startsWith('audio/');
    }

    function splitWhatsAppTranscript(item) {
        const raw = String(item?.text || '').trim();
        const lines = raw ? raw.split(/\r?\n/) : [];
        const at = lines.findIndex(line => WA_TRANSCRIPT_PREFIX.test(line.trim()));
        let body = raw;
        let transcript = '';
        if (at >= 0) {
            transcript = [lines[at].trim().replace(WA_TRANSCRIPT_PREFIX, ''), ...lines.slice(at + 1)].join('\n').trim();
            body = lines.slice(0, at).join('\n').trim();
        }
        const job = whatsappTranscribeJobs.get(Number(item?.database_id));
        if (!transcript && job?.text) transcript = job.text;
        return { body, transcript };
    }

    function whatsappTranscribeErrorText(error) {
        const status = Number(error?.status || 0);
        if (status === 409) return 'The audio isn\'t downloaded yet. Play the voice note once, then tap Retry.';
        if (status === 404) return 'The voice note file couldn\'t be found on this computer.';
        if (status === 422) return 'No speech was found in this voice note.';
        if (status >= 500) return 'Transcription failed. Try again.';
        return error?.message || 'Transcription failed. Try again.';
    }

    function whatsappVoiceTranscriptHtml(item, transcript) {
        const id = Number(item.database_id);
        return `<div class="incoming-wa-transcript" data-wa-transcript="${id}"><div class="incoming-wa-transcript-head"><span>Transcription</span><button type="button" class="incoming-wa-transcribe-btn" data-wa-transcript-copy="${id}" aria-label="Copy transcription">Copy</button></div><p>${actions.escapeHtml(transcript)}</p></div>`;
    }

    function whatsappVoiceTranscribeHtml(item) {
        const id = Number(item.database_id);
        const job = whatsappTranscribeJobs.get(id) || {};
        const label = job.pending ? 'Transcribing\u2026' : job.error ? 'Retry' : 'Transcribe';
        const error = job.error && !job.pending ? `<p class="incoming-wa-transcribe-error" role="status">${actions.escapeHtml(job.error)}</p>` : '';
        return `<div class="incoming-wa-transcribe"><button type="button" class="incoming-wa-transcribe-btn${job.pending ? ' is-loading' : ''}" data-wa-transcribe="${id}" aria-label="${job.error ? 'Retry transcription' : 'Transcribe voice note'}"${job.pending ? ' disabled aria-busy="true"' : ''}>${label}</button>${error}</div>`;
    }

    function whatsappMessageBubbleHtml(item, conversation) {
        let textHtml = item.text ? `<p>${actions.escapeHtml(item.text)}</p>` : '';
        let voiceHtml = '';
        if (isWhatsAppVoiceItem(item) && Number(item.database_id) > 0) {
            const { body, transcript } = splitWhatsAppTranscript(item);
            if (transcript) {
                textHtml = body && !WA_VOICE_PLACEHOLDER.test(body) ? `<p>${actions.escapeHtml(body)}</p>` : '';
                voiceHtml = whatsappVoiceTranscriptHtml(item, transcript);
            } else if (!body || WA_VOICE_PLACEHOLDER.test(body)) {
                voiceHtml = whatsappVoiceTranscribeHtml(item);
            }
        }
        return `<div class="incoming-message-bubble${item.from_me ? ' outgoing' : ' incoming'}" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}" data-wa-mid="${actions.escapeHtml(item.source_message_id || '')}"><small>${actions.escapeHtml(item.from_me ? 'You' : item.sender || conversation.label)}</small>${incomingMediaHtml(item)}${textHtml}${voiceHtml}<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time></div>`;
    }

    function applyWhatsAppTranscript(conversation, id, text) {
        const block = `[Transcription] ${text}`;
        const patch = (item) => {
            if (!item || Number(item.database_id) !== id) return;
            const current = String(item.text || '').trim();
            const kept = WA_VOICE_PLACEHOLDER.test(current) ? '' : current.split(/\r?\n/).filter(line => !WA_TRANSCRIPT_PREFIX.test(line.trim())).join('\n').trim();
            item.text = kept ? `${kept}\n${block}` : block;
        };
        (state.incoming || []).forEach(patch);
        whatsappReaderState(conversation.key).items.forEach(patch);
        (conversation.messages || []).forEach(patch);
    }

    async function transcribeWhatsAppVoice(conversation, id) {
        if (!(id > 0)) return;
        const job = whatsappTranscribeJobs.get(id) || {};
        if (job.pending) return;
        whatsappTranscribeJobs.set(id, { pending: true, error: '', text: '' });
        repaintSelectedWhatsApp(conversation.key);
        try {
            const result = await actions.api(`/tickets/whatsapp/messages/${encodeURIComponent(id)}/analyze-media`, { method: 'POST' });
            const text = String(result?.text || '').trim();
            if (!result || result.success !== true || !text) throw new Error(result?.error || result?.detail || 'Transcription failed. Try again.');
            whatsappTranscribeJobs.set(id, { pending: false, error: '', text });
            applyWhatsAppTranscript(conversation, id, text);
        } catch (error) {
            whatsappTranscribeJobs.set(id, { pending: false, error: whatsappTranscribeErrorText(error), text: '' });
        } finally {
            repaintSelectedWhatsApp(conversation.key);
        }
    }

    async function copyWhatsAppTranscript(conversation, id) {
        const view = whatsappReaderState(conversation.key);
        const item = [...view.items.values(), ...(conversation.messages || [])].find(row => Number(row.database_id) === id);
        const text = item ? splitWhatsAppTranscript(item).transcript : (whatsappTranscribeJobs.get(id)?.text || '');
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            actions.toast('Transcription copied.');
        } catch (_) {
            actions.toast('Could not copy the transcription. Check clipboard permissions and try again.', 'error');
        }
    }

    function whatsappProposalMarkerHtml(kind, proposal) {
        const title = String(proposal.title || 'Proposal').trim() || 'Proposal';
        const label = kind === 'start' ? `Proposal: ${title} start` : `Proposal: ${title} end`;
        return `<div class="incoming-proposal-marker" data-proposal-marker="${actions.escapeHtml(kind)}" data-proposal-id="${actions.escapeHtml(proposal.id || '')}" role="separator"><span>${actions.escapeHtml(label)}</span></div>`;
    }

    function whatsappMessagesWithProposalMarkers(conversation) {
        const ordered = conversation.messages.slice().reverse();
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

    function parseCssPx(value) {
        const raw = String(value || '').trim();
        if (!raw) return NaN;
        const match = raw.match(/^(\d+(?:\.\d+)?)\s*px$/i) || raw.match(/^(\d+(?:\.\d+)?)$/);
        return match ? Number(match[1]) : NaN;
    }

    function isDeclaredTrackingPixel(img) {
        const dims = [];
        for (const attr of ['width', 'height']) {
            const n = parseCssPx(img.getAttribute(attr));
            if (Number.isFinite(n)) dims.push(n);
        }
        const style = String(img.getAttribute('style') || '');
        for (const prop of ['width', 'height']) {
            const match = style.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, 'i'));
            const n = match ? parseCssPx(match[1]) : NaN;
            if (Number.isFinite(n)) dims.push(n);
        }
        return dims.some((n) => n <= 3);
    }

    function sanitizeEmailHtml(html) {
        const template = document.createElement('template');
        template.innerHTML = String(html || '');
        const blocked = new Set(['script', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form']);
        const dropNodes = [];
        // 2026-11-12 nov11-audit-4: the preview frame inherits the app CSP (font-src/style-src 'self'), so
        // remote web fonts and remote @import sheets in email <style> blocks can never load there and only
        // log CSP errors. Drop just those rules; the text already falls back to the next font in the stack.
        const remoteUrl = /url\(\s*['"]?\s*(?:https?:)?\/\//i;
        template.content.querySelectorAll('style').forEach((style) => {
            const css = String(style.textContent || '');
            const cleaned = css
                .replace(/@font-face\s*\{[^}]*\}/gi, (rule) => (remoteUrl.test(rule) ? '' : rule))
                .replace(/@import\s+(?:url\(\s*)?['"]?\s*(?:https?:)?\/\/[^;]*;?/gi, '');
            if (cleaned !== css) style.textContent = cleaned;
        });
        template.content.querySelectorAll('*').forEach((node) => {
            const tag = node.tagName.toLowerCase();
            if (blocked.has(tag)) {
                node.remove();
                return;
            }
            if (tag === 'img' && isDeclaredTrackingPixel(node)) {
                dropNodes.push(node);
                return;
            }
            [...node.attributes].forEach((attr) => {
                const name = attr.name.toLowerCase();
                const value = String(attr.value || '');
                if (name.startsWith('on') || name === 'srcdoc') {
                    node.removeAttribute(attr.name);
                    return;
                }
                if ((name === 'href' || name === 'xlink:href') && /^\s*javascript:/i.test(value)) {
                    node.removeAttribute(attr.name);
                    return;
                }
                if (name === 'src' || name === 'poster' || name === 'background') {
                    const remote = /^\s*https?:/i.test(value) || /^\s*\/\//.test(value);
                    if (!remote) return;
                    // Allow real remote <img src="https://...">; strip other remote media attrs.
                    // Tracking pixels are removed by declared size (above) or natural size on load.
                    if (tag === 'img' && name === 'src') return;
                    node.removeAttribute(attr.name);
                }
            });
            if (tag === 'a') {
                node.setAttribute('target', '_blank');
                node.setAttribute('rel', 'noopener noreferrer');
            }
        });
        dropNodes.forEach((node) => node.remove());
        return template.innerHTML;
    }

    function mailBodyHtml(item) {
        const htmlBody = String(item.html_body || '').trim();
        const plain = String(item.text || item.snippet || '').trim() || 'No message text';
        if (htmlBody) {
            const cidMap = {};
            (item.attachments || []).forEach((attachment) => {
                const cid = String(attachment.content_id || attachment.cid || '').replace(/^<|>$/g, '');
                const url = attachment.preview_url || attachment.url || '';
                if (cid && url) cidMap[cid.toLowerCase()] = url;
            });
            let rewritten = htmlBody.replace(/cid:([^"'\s>]+)/gi, (_, raw) => {
                const key = String(raw || '').replace(/^<|>$/g, '').toLowerCase();
                return cidMap[key] || 'about:blank';
            });
            rewritten = sanitizeEmailHtml(rewritten);
            // Same app font as the rest of the UI (Quicksand); the iframe does not inherit it.
            const doc = `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><link rel="stylesheet" href="/static/vendor/quicksand.css"><style>
                html,body{margin:0;padding:0;background:#fff;color:#111827;}
                body{font:14px/1.55 Quicksand,sans-serif;word-break:break-word;}
                img{max-width:100%;height:auto;}
                a{color:#1d4ed8;}
                pre,code{white-space:pre-wrap;word-break:break-word;}
            </style></head><body>${rewritten}</body></html>`;
            return `<iframe class="incoming-mail-html-frame" title="Email body" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" srcdoc="${actions.escapeHtml(doc)}"></iframe>`;
        }
        return `<div class="incoming-gmail-body incoming-gmail-body-plain">${actions.escapeHtml(plain)}</div>`;
    }

    function mailMessageCardHtml(item) {
        const from = item.sender || 'Unknown sender';
        const when = actions.formatDateTime(item.created_at);
        const to = item.recipient
            ? `<div class="incoming-mail-header-row"><span class="incoming-mail-header-label">To</span><span>${actions.escapeHtml(item.recipient)}</span></div>`
            : '';
        return `<section class="incoming-gmail-message" data-incoming-msg="${actions.escapeHtml(incomingItemKey(item))}" data-mail-key="${actions.escapeHtml(item.key || '')}" data-mail-id="${actions.escapeHtml(item.source_message_id || '')}" data-mail-sender="${actions.escapeHtml(from)}" data-mail-subject="${actions.escapeHtml(item.subject || '')}"><header class="incoming-mail-headers"><div class="incoming-mail-header-row"><span class="incoming-mail-header-label">From</span><strong>${actions.escapeHtml(from)}</strong></div>${to}<div class="incoming-mail-header-row"><span class="incoming-mail-header-label">Date</span><time>${actions.escapeHtml(when)}</time></div></header>${mailBodyHtml(item)}</section>`;
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

    function whatsappReaderState(key) {
        if (!whatsappReaders.has(key)) {
            whatsappReaders.set(key, { text: '', items: new Map(), beforeId: null, hasOlder: true });
        }
        return whatsappReaders.get(key);
    }

    function whatsappReplyJid(conversation) {
        const jid = conversation.threadId;
        if (/^[^\s@]+@(?:g\.us|s\.whatsapp\.net|lid)$/.test(jid)) return jid;
        return conversation.chatType === 'private' && /^\d+$/.test(jid) ? `${jid}@s.whatsapp.net` : '';
    }

    function incomingWhatsAppReaderHtml(conversation) {
        if (!conversation)
            return '<div class="incoming-reader-empty"><strong>Select a conversation</strong><p>Linked conversations are kept at the top.</p></div>';
        const view = whatsappReaderState(conversation.key);
        const allMessages = new Map(view.items);
        for (const item of conversation.messages) allMessages.set(incomingItemKey(item), item);
        const ordered = [...allMessages.values()].sort(compareIncomingMessagesNewestFirst);
        const messages = whatsappMessagesWithProposalMarkers({ ...conversation, messages: ordered });
        const older = view.hasOlder ? `<button type="button" class="proposal-btn ghost" data-wa-older${view.loadingOlder ? ' disabled' : ''}>${view.loadingOlder ? 'Loading...' : 'Load older messages'}</button>` : '';
        const reply = whatsappReplyJid(conversation) ? `
            <form class="incoming-whatsapp-compose" data-wa-compose>
                <div class="incoming-whatsapp-compose-controls">
                    <textarea rows="1" aria-label="WhatsApp message" placeholder="Type a message"${view.sending ? ' readonly' : ''}>${actions.escapeHtml(view.text)}</textarea>
                    <button type="submit" aria-label="Send WhatsApp message"${view.sending || !view.text.trim() ? ' disabled' : ''}>
                        <svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3 20.5 22 12 3 3.5v6.6l11 1.9-11 1.9z"/></svg>
                    </button>
                </div>
                <p role="status" class="${view.sendError ? 'error' : ''}">${actions.escapeHtml(view.sending ? 'Sending...' : view.sendStatus || '')}</p>
            </form>` : '';
        return `<header class="incoming-whatsapp-reader-header">${incomingAvatarHtml(conversation, true)}<div><h3>${actions.escapeHtml(conversation.label)}</h3><p>${conversation.chatType === 'group' ? 'WhatsApp group' : 'WhatsApp contact'}</p></div><button type="button" data-incoming-manage aria-label="Manage ${actions.escapeHtml(conversation.label)}">•••</button></header><div class="incoming-whatsapp-messages"><div class="incoming-whatsapp-history">${older}${view.historyError ? `<p class="error" role="status">${actions.escapeHtml(view.historyError)}</p>` : ''}</div>${messages}</div>${reply}`;
    }

    function paintWhatsAppReader(conversation, scroll = 'preserve') {
        const reader = el('incoming-whatsapp-reader');
        const previousMessages = reader.querySelector('.incoming-whatsapp-messages');
        const previousInput = reader.querySelector('[data-wa-compose] textarea');
        const sameThread = conversation && reader.dataset.thread === conversation.key;
        const keepFocus = sameThread && document.activeElement === previousInput && previousInput;
        const top = previousMessages?.scrollTop || 0;
        const height = previousMessages?.scrollHeight || 0;
        const atBottom = whatsappMessagesAtBottom(previousMessages);
        reader.dataset.thread = conversation?.key || '';
        reader.innerHTML = incomingWhatsAppReaderHtml(conversation);
        bindIncomingActions(reader);
        const messages = reader.querySelector('.incoming-whatsapp-messages');
        if (!messages) return;
        messages.addEventListener('scroll', () => maybeClearWhatsAppUnreadFromVisibility(), { passive: true });
        if (sameThread && scroll === 'older') messages.scrollTop = top + messages.scrollHeight - height;
        else if (sameThread && scroll === 'preserve' && !atBottom) messages.scrollTop = top;
        else scrollIncomingWhatsAppToLatest();
        const form = reader.querySelector('[data-wa-compose]');
        if (form) {
            const input = form.querySelector('textarea');
            const view = whatsappReaderState(conversation.key);
            input.addEventListener('input', () => {
                view.text = input.value;
                form.querySelector('button').disabled = !view.text.trim() || view.sending;
            });
            input.addEventListener('keydown', event => {
                if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
                event.preventDefault();
                form.requestSubmit();
            });
            form.addEventListener('submit', event => {
                event.preventDefault();
                sendIncomingWhatsApp(conversation);
            });
            if (keepFocus) {
                input.focus({ preventScroll: true });
                input.setSelectionRange(previousInput.selectionStart, previousInput.selectionEnd);
            }
        }
        reader.querySelector('[data-wa-older]')?.addEventListener('click', () => loadOlderWhatsApp(conversation));
        bindWhatsAppVoiceButtons(reader, conversation);
    }

    // Also used for voice notes appended live (patchIncomingInPlace), which skip paintWhatsAppReader.
    function bindWhatsAppVoiceButtons(root, conversation) {
        root?.querySelectorAll('[data-wa-transcribe]:not([data-wa-bound]), [data-wa-transcript-copy]:not([data-wa-bound])').forEach(button => {
            button.dataset.waBound = '1';
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                if (button.dataset.waTranscribe) transcribeWhatsAppVoice(conversation, Number(button.dataset.waTranscribe));
                else copyWhatsAppTranscript(conversation, Number(button.dataset.waTranscriptCopy));
            });
        });
    }

    function repaintSelectedWhatsApp(key, scroll) {
        if (context.workspaceMode !== 'incoming' || state.incomingFilter !== 'whatsapp'
            || state.selectedIncomingConversationKey !== key) return;
        const selected = incomingConversations('whatsapp').find(row => row.key === key);
        if (selected) paintWhatsAppReader(selected, scroll);
    }

    async function fetchWhatsAppHistory(conversation, beforeId = 0) {
        const query = new URLSearchParams({ thread_id: conversation.threadId, before_id: String(beforeId) });
        return actions.api(`/workflows/studio/incoming/whatsapp/history?${query}`, { cache: 'no-store' });
    }

    async function loadOlderWhatsApp(conversation) {
        const view = whatsappReaderState(conversation.key);
        if (view.loadingOlder || !view.hasOlder) return;
        view.loadingOlder = true;
        view.historyError = '';
        repaintSelectedWhatsApp(conversation.key);
        try {
            const first = [...conversation.messages].sort((a, b) =>
                (Date.parse(a.source_sent_at) || 0) - (Date.parse(b.source_sent_at) || 0)
                || Number(a.database_id || 0) - Number(b.database_id || 0)).find(item => item.database_id);
            const data = await fetchWhatsAppHistory(conversation, view.beforeId ?? first?.database_id ?? 0);
            for (const item of data.items || []) view.items.set(incomingItemKey(item), item);
            view.beforeId = data.items?.at(-1)?.database_id ?? view.beforeId;
            view.hasOlder = Boolean(data.has_older);
        } catch (error) {
            view.historyError = error.message || 'Could not load older messages. Try again.';
        } finally {
            view.loadingOlder = false;
            repaintSelectedWhatsApp(conversation.key, 'older');
        }
    }

    async function sendIncomingWhatsApp(conversation) {
        const view = whatsappReaderState(conversation.key);
        const text = view.text.trim();
        const jid = whatsappReplyJid(conversation);
        if (!jid || !text || view.sending) return;
        const sentAt = Math.floor(Date.now() / 1000) * 1000;
        const known = new Set([...view.items.keys(), ...conversation.messages.map(incomingItemKey)]);
        view.sending = true;
        view.sendError = false;
        view.sendStatus = '';
        repaintSelectedWhatsApp(conversation.key);
        try {
            const result = await actions.api('/tickets/whatsapp/send', { method: 'POST', body: { jid, text } });
            if (!result.success) throw new Error(result.error || 'WhatsApp did not accept the message.');
            view.text = '';
            view.sendStatus = 'Submitted to WhatsApp.';
            refreshSentWhatsApp(conversation, text, sentAt, known);
        } catch (error) {
            view.sendError = true;
            view.sendStatus = error.message || 'Could not send the message. Try again.';
        } finally {
            view.sending = false;
            repaintSelectedWhatsApp(conversation.key);
        }
    }

    async function refreshSentWhatsApp(conversation, text, sentAt, known) {
        const view = whatsappReaderState(conversation.key);
        for (const delay of [500, 1500, 3000]) {
            await new Promise(resolve => setTimeout(resolve, delay));
            try {
                const data = await fetchWhatsAppHistory(conversation);
                const echo = (data.items || []).find(item => item.from_me && item.text === text
                    && !known.has(incomingItemKey(item)) && Date.parse(item.created_at) >= sentAt);
                if (!echo) continue;
                const key = incomingItemKey(echo);
                view.items.set(key, echo);
                if (!state.incoming.some(item => incomingItemKey(item) === key)) {
                    state.incoming.unshift(echo);
                    refreshWhatsAppConversationRow(conversation.key);
                }
                repaintSelectedWhatsApp(conversation.key);
                return;
            } catch (_) {
                // The relay accepted the request. Retrying the read cannot resend it.
            }
        }
    }

    function whatsappMessagesAtBottom(messages) {
        if (!messages) return false;
        return messages.scrollHeight - messages.scrollTop - messages.clientHeight <= 24;
    }

    function refreshWhatsAppConversationRow(conversationKey) {
        const list = el('incoming-whatsapp-list-content');
        if (!list || !conversationKey) return;
        const conversation = incomingConversations('whatsapp').find((row) => row.key === conversationKey);
        if (!conversation) return;
        const existing = list.querySelector(`[data-incoming-conversation="${CSS.escape(conversationKey)}"]`);
        if (!existing) return;
        const wrap = document.createElement('div');
        wrap.innerHTML = incomingConversationRowHtml(conversation);
        const next = wrap.firstElementChild;
        if (!next) return;
        existing.replaceWith(next);
        next.addEventListener('click', () => {
            selectIncomingConversation(next.dataset.incomingConversation);
        });
        next.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            openIncomingLinkDialog(next.dataset.incomingConversation || '');
        });
        bindIncomingActions(next);
    }

    function maybeClearWhatsAppUnreadFromVisibility() {
        const key = state.selectedIncomingConversationKey;
        if (!key) return;
        const messages = el('incoming-whatsapp-reader')?.querySelector('.incoming-whatsapp-messages');
        if (!whatsappMessagesAtBottom(messages)) return;
        const conversation = incomingConversations('whatsapp').find((row) => row.key === key);
        if (!conversationShowsNewDot(conversation)) return;
        markConversationSeen(key, conversation);
        refreshWhatsAppConversationRow(key);
    }

    function scrollIncomingWhatsAppToLatest() {
        const messages = el('incoming-whatsapp-reader')?.querySelector('.incoming-whatsapp-messages');
        if (!messages) return;
        const scroll = () => {
            messages.scrollTop = messages.scrollHeight;
            maybeClearWhatsAppUnreadFromVisibility();
        };
        window.requestAnimationFrame(scroll);
        messages.querySelectorAll('img, video').forEach((media) => {
            media.addEventListener(media.tagName === 'VIDEO' ? 'loadedmetadata' : 'load', scroll, { once: true });
        });
        // Short threads that already fit still count as "latest visible".
        window.requestAnimationFrame(() => maybeClearWhatsAppUnreadFromVisibility());
    }

    function mailThreadButtonHtml(conversation, selectedKey, dataAttribute) {
        const latest = conversation.latest || {};
        const preview = latest.snippet || latest.text || 'No message text';
        return `<button type="button" class="incoming-gmail-thread${conversation.pendingCount ? ' unread' : ''}${selectedKey === conversation.key ? ' selected' : ''}" ${dataAttribute}="${actions.escapeHtml(conversation.key)}"><span><strong>${actions.escapeHtml(conversation.label || '(No subject)')}</strong><time>${actions.escapeHtml(actions.formatDateTime(latest.created_at))}</time></span><small>${actions.escapeHtml(latest.sender || 'Unknown sender')}</small><p>${actions.escapeHtml(preview)}</p></button>`;
    }


    function collectConversationMailAttachments(conversation) {
        const seen = new Set();
        const out = [];
        for (const message of (conversation?.messages || [])) {
            for (const attachment of (message.attachments || [])) {
                if (!attachment || typeof attachment !== 'object') continue;
                const key = [
                    attachment.attachment_id || '',
                    attachment.content_id || '',
                    attachment.filename || attachment.name || '',
                    message.source_message_id || message.id || '',
                ].join('|');
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({
                    ...attachment,
                    message_id: attachment.message_id || message.source_message_id || '',
                    preview_url: attachment.preview_url || '',
                });
            }
        }
        return out;
    }

    function mailAttachmentPreviewUrl(attachment) {
        if (attachment?.preview_url) return String(attachment.preview_url);
        const mid = String(attachment?.message_id || '').trim();
        const aid = String(attachment?.attachment_id || '').trim();
        const cid = String(attachment?.content_id || '').replace(/^<|>$/g, '').trim();
        if (!mid || (!aid && !cid)) return '';
        const params = new URLSearchParams({ message_id: mid });
        if (aid) params.set('attachment_id', aid);
        if (cid) params.set('content_id', cid);
        const provider = String(attachment?.provider || '').trim().toLowerCase();
        if (provider) params.set('provider', provider);
        else if (mid.startsWith('email-')) params.set('provider', 'mailshot');
        return `/api/workflows/studio/incoming/mail/attachment?${params.toString()}`;
    }

    function mailAttachmentBarHtml(conversation) {
        const attachments = collectConversationMailAttachments(conversation);
        if (!attachments.length) return '';
        const items = attachments.map((attachment) => {
            const name = String(attachment.filename || attachment.name || 'Attachment');
            const mime = String(attachment.mime_type || attachment.type || '');
            const url = mailAttachmentPreviewUrl(attachment);
            const label = proposalAttachmentLabel({ name, mime_type: mime, kind: attachment.kind || '' });
            const isImage = mime.toLowerCase().startsWith('image/') || /\.(png|jpe?g|gif|webp)$/i.test(name);
            const payload = actions.escapeHtml(JSON.stringify({
                url,
                name: label,
                filename: name,
                mime_type: mime,
                caption: '',
            }));
            const thumb = (url && isImage)
                ? `<img class="incoming-mail-attach-thumb" src="${actions.escapeHtml(url)}" alt="" loading="lazy">`
                : `<span class="incoming-mail-attach-icon" aria-hidden="true">${fileTypeIconSvg(attachmentFileKind({ mime_type: mime, filename: name }))}</span>`;
            const disabled = url ? '' : ' disabled';
            return `<button type="button" class="incoming-mail-attach-chip"${disabled} data-file-preview="${payload}" title="${actions.escapeHtml(name)}">${thumb}<span class="incoming-mail-attach-name">${actions.escapeHtml(name)}</span></button>`;
        }).join('');
        // Up to three rows of full-name chips; more columns scroll horizontally.
        return `<div class="incoming-mail-attachment-bar" aria-label="Attachments"><div class="incoming-mail-attachment-bar-track" style="--attach-rows:${Math.min(attachments.length, 3)}">${items}</div></div>`;
    }

    function restoreQuickReply(key) {
        if (quickReplyDrafts.has(key)) return quickReplyDrafts.get(key);
        try {
            const draft = JSON.parse(window.sessionStorage.getItem(quickReplyStorageKey(key)));
            if (draft && ['idempotencyKey', 'body', 'to', 'subject', 'messageId'].every(field => typeof draft[field] === 'string')
                && draft.idempotencyKey && typeof draft.open === 'boolean') {
                quickReplyDrafts.set(key, draft);
                return draft;
            }
        } catch { /* Storage is optional in this tab. */ }
        return null;
    }

    function persistQuickReply(key, draft) {
        try {
            if (!draft?.body || draft.sent) {
                window.sessionStorage.removeItem(quickReplyStorageKey(key));
                return;
            }
            const { idempotencyKey, body, to, subject, messageId, open } = draft;
            window.sessionStorage.setItem(quickReplyStorageKey(key), JSON.stringify({
                idempotencyKey, body, to, subject, messageId, open,
                receiptStatus: draft.sending ? 'unconfirmed' : draft.receiptStatus,
                error: draft.sending ? QUICK_REPLY_UNKNOWN : draft.error,
            }));
        } catch { /* The current editor still holds its draft. */ }
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
        const replySource = conversation.messages.find(item => !item.from_me && !(item.labels || []).includes('SENT')) || latest;
        const draft = restoreQuickReply(conversation.key);
        const statusButton = draft?.receiptStatus
            ? `<button type="button" class="proposal-btn ghost" data-quick-reply-check${draft.checking ? ' disabled' : ''}>${draft.checking ? 'Checking...' : 'Check status'}</button>`
            : '';
        const replyTo = actions.escapeHtml(draft?.to ?? (replySource.reply_to || replySource.sender || ''));
        const replySubject = actions.escapeHtml(draft?.subject ?? mailReplySubject(conversation));
        const replyMessageId = actions.escapeHtml(draft?.messageId ?? replySource.source_message_id ?? '');
        const messages = conversation.messages.slice().reverse().map(mailMessageCardHtml).join('');
        const attachmentBar = mailAttachmentBarHtml(conversation);
        return `<div class="incoming-gmail-messages">${messages}</div>${attachmentBar}<div class="incoming-quick-reply${draft?.open ? '' : ' hidden'}" data-incoming-quick-reply data-quick-reply-key="${actions.escapeHtml(conversation.key)}"><div class="incoming-quick-reply-fields"><label>To<input type="text" data-quick-reply-to value="${replyTo}" autocomplete="email"${draft?.sending ? ' disabled' : ''}></label><label>Subject<input type="text" data-quick-reply-subject value="${replySubject}"${draft?.sending ? ' disabled' : ''}></label><label>Message<textarea data-quick-reply-body rows="6" placeholder="Write your reply"${draft?.sending ? ' disabled' : ''}>${actions.escapeHtml(draft?.body || '')}</textarea></label><input type="hidden" data-quick-reply-message-id value="${replyMessageId}"></div><p role="status" data-quick-reply-status>${actions.escapeHtml(draft?.error || '')}</p><div class="incoming-quick-reply-actions"><button type="button" class="proposal-btn ghost" data-quick-reply-cancel${draft?.sending ? ' disabled' : ''}>Cancel</button>${statusButton}<button type="button" class="proposal-btn primary" data-quick-reply-send${draft?.sending || draft?.receiptStatus ? ' disabled' : ''}>${draft?.sending ? 'Sending...' : 'Send'}</button></div></div><footer class="incoming-mail-footer"><button type="button" class="proposal-btn ghost" data-incoming-quick-reply-open>Quick Reply</button><div class="incoming-mail-footer-right">${rightActions}</div></footer>`;
    }

    function rememberQuickReply(panel) {
        const key = panel?.dataset.quickReplyKey;
        if (!key) return null;
        const draft = quickReplyDrafts.get(key) || { idempotencyKey: crypto.randomUUID() };
        if (draft.sent) return draft;
        Object.assign(draft, {
            open: !panel.classList.contains('hidden'),
            to: panel.querySelector('[data-quick-reply-to]')?.value || '',
            subject: panel.querySelector('[data-quick-reply-subject]')?.value || '',
            body: panel.querySelector('[data-quick-reply-body]')?.value || '',
            messageId: panel.querySelector('[data-quick-reply-message-id]')?.value || '',
        });
        quickReplyDrafts.set(key, draft);
        persistQuickReply(key, draft);
        return draft;
    }

    function bindIncomingActions(root) {
        root?.querySelectorAll('[data-incoming-quick-reply]').forEach(panel => {
            panel.addEventListener('input', () => rememberQuickReply(panel));
            if (quickReplyDrafts.get(panel.dataset.quickReplyKey)?.receiptStatus) {
                panel.querySelectorAll('input,textarea').forEach(control => { control.readOnly = true; });
                panel.querySelector('[data-quick-reply-send]').disabled = true;
                panel.querySelector('[data-quick-reply-cancel]').textContent = 'Close';
            }
        });
        root.querySelectorAll('[data-incoming-avatar]').forEach((photo) => {
            const show = () => photo.classList.toggle('loaded', photo.naturalWidth > 0);
            photo.addEventListener('load', show, { once: true });
            photo.addEventListener('error', () => photo.remove(), { once: true });
            if (photo.complete) show();
        });
        if (!root) return;
        bindProposalFilePreview(root);
        root.querySelectorAll('[data-incoming-snapshot]').forEach((button) =>
            button.addEventListener('click', () => snapshotIncomingWhatsApp(Number(button.dataset.boardId), Number(button.dataset.incomingSnapshot), button))
        );
        root.querySelectorAll('[data-incoming-manage]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                openIncomingLinkDialog(button.closest('[data-incoming-conversation]')?.dataset.incomingConversation || state.selectedIncomingConversationKey);
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
                const panel = reader?.querySelector('[data-incoming-quick-reply]');
                if (quickReplyDrafts.get(panel?.dataset.quickReplyKey)?.sent) quickReplyDrafts.delete(panel.dataset.quickReplyKey);
                rememberQuickReply(panel);
                reader?.querySelector('[data-quick-reply-body]')?.focus();
            })
        );
        root.querySelectorAll('[data-quick-reply-cancel]').forEach((button) =>
            button.addEventListener('click', () => {
                const panel = button.closest('[data-incoming-quick-reply]');
                if (!panel) return;
                panel.classList.add('hidden');
                panel.closest('.incoming-gmail-reader')?.querySelector('[data-incoming-quick-reply-open]')?.focus();
                const draft = quickReplyDrafts.get(panel.dataset.quickReplyKey);
                if (draft?.receiptStatus) {
                    draft.open = false;
                    persistQuickReply(panel.dataset.quickReplyKey, draft);
                    return;
                }
                const body = panel.querySelector('[data-quick-reply-body]');
                if (body) body.value = '';
                quickReplyDrafts.delete(panel.dataset.quickReplyKey);
                persistQuickReply(panel.dataset.quickReplyKey, null);
            })
        );
        root.querySelectorAll('[data-quick-reply-send]').forEach((button) =>
            button.addEventListener('click', () => sendIncomingQuickReply(button))
        );
        root.querySelectorAll('[data-quick-reply-check]').forEach((button) =>
            button.addEventListener('click', () => checkQuickReplyStatus(button))
        );
        bindProposalFilePreview(root);
        root.querySelectorAll('iframe.incoming-mail-html-frame').forEach((frame) => {
            const fit = () => {
                try {
                    const doc = frame.contentDocument;
                    const height = Math.max(doc?.body?.scrollHeight || 0, doc?.documentElement?.scrollHeight || 0, 120);
                    frame.style.height = `${Math.min(Math.max(height + 8, 120), 1200)}px`;
                } catch (_) {
                    frame.style.height = '320px';
                }
            };
            const scrubTrackingPixels = () => {
                try {
                    const doc = frame.contentDocument;
                    if (!doc) return;
                    [...doc.querySelectorAll('img')].forEach((img) => {
                        const dropIfTiny = () => {
                            const w = Number(img.naturalWidth || 0);
                            const h = Number(img.naturalHeight || 0);
                            if ((w > 0 && w <= 3) || (h > 0 && h <= 3)) {
                                img.remove();
                                fit();
                            }
                        };
                        if (img.complete) dropIfTiny();
                        else img.addEventListener('load', dropIfTiny, { once: true });
                    });
                } catch (_) { /* ignore cross-document races */ }
            };
            const onReady = () => {
                scrubTrackingPixels();
                fit();
            };
            frame.addEventListener('load', onReady);
            setTimeout(onReady, 50);
        });
    }

    function confirmQuickReply(panel, draft) {
        Object.assign(draft, { sent: true, open: false, body: '', receiptStatus: '', error: '' });
        persistQuickReply(panel.dataset.quickReplyKey, draft);
        panel.classList.add('hidden');
        const field = panel.querySelector('[data-quick-reply-body]');
        if (field) field.value = '';
        actions.toast('Reply sent.');
    }

    async function checkQuickReplyStatus(button) {
        const panel = button.closest('[data-incoming-quick-reply]');
        const key = panel?.dataset.quickReplyKey;
        const draft = quickReplyDrafts.get(key);
        if (!draft?.receiptStatus || draft.checking) return;
        draft.checking = true;
        button.disabled = true;
        button.textContent = 'Checking...';
        try {
            const result = await actions.api(`${QUICK_REPLY_PATH}/status?idempotency_key=${encodeURIComponent(draft.idempotencyKey)}`);
            if (quickReplyDrafts.get(key) !== draft) return;
            if (result.receipt_status === 'sent') {
                confirmQuickReply(panel, draft);
            } else {
                draft.error = result.receipt_status === 'missing'
                    ? 'No send receipt was found. The outcome is still unknown. Check the original conversation.'
                    : QUICK_REPLY_UNKNOWN;
            }
        } catch {
            draft.error = 'Could not check the saved reply status. Try checking again.';
        } finally {
            draft.checking = false;
            if (quickReplyDrafts.get(key) === draft) {
                persistQuickReply(key, draft);
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            }
        }
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
        const draft = rememberQuickReply(panel);
        if (draft?.sending || draft?.receiptStatus) return;
        if (!to || !body.trim()) {
            actions.toast('Add a recipient and reply text before sending.', 'error');
            return;
        }
        const original = button.textContent;
        button.disabled = true;
        button.textContent = 'Sending...';
        draft.sending = true;
        draft.error = '';
        persistQuickReply(panel.dataset.quickReplyKey, draft);
        panel.querySelectorAll('input,textarea,[data-quick-reply-cancel]').forEach(control => { control.disabled = true; });
        try {
            const result = await actions.api(QUICK_REPLY_PATH, {
                method: 'POST',
                body: {
                    channel,
                    to,
                    subject,
                    body,
                    message_id: messageId,
                    idempotency_key: draft.idempotencyKey,
                    dry_run: false,
                },
            });
            if (!result?.success) throw new Error(result?.error || 'Could not send the reply.');
            if (result.dry_run) {
                draft.error = 'Reply was not sent. The provider returned a dry run.';
                return;
            }
            confirmQuickReply(panel, draft);
        } catch (error) {
            draft.error = error.message || 'Could not send the reply.';
            const receiptStatus = error.data?.receipt_status || error.receipt_status;
            if (['unconfirmed', 'sending'].includes(receiptStatus)) draft.receiptStatus = receiptStatus;
            else if (error.data?.send_attempted !== false && error.status !== 422) {
                draft.receiptStatus = 'unconfirmed';
                draft.error = QUICK_REPLY_UNKNOWN;
            }
            const inlineVisible = context.workspaceMode === 'incoming'
                && Array.from(document.querySelectorAll('[data-incoming-quick-reply]:not(.hidden)')).some(
                    editor => editor.dataset.quickReplyKey === panel.dataset.quickReplyKey && editor.getClientRects().length > 0
                );
            if (!inlineVisible) actions.toast(draft.error, 'error');
        } finally {
            draft.sending = false;
            persistQuickReply(panel.dataset.quickReplyKey, draft);
            button.disabled = false;
            button.textContent = original;
            panel.querySelectorAll('input,textarea,[data-quick-reply-cancel]').forEach(control => { control.disabled = false; });
            // A background refresh may have replaced this exact editor while
            // the send was pending. Render only from retained delivery state.
            if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
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
        const evidence = proposal.source === 'whatsapp' ? proposalSourceChunk(proposal).messages : (proposal.evidence || []);
        const ev = evidence[evidence.length - 1] || evidence[0];
        if (!ev) return '';
        const who = ev.sender_name || ev.sender_address || '';
        return `${who ? who + ': ' : ''}${String(ev.body || '').replace(/\s+/g, ' ').slice(0, 180)}`.trim();
    }

    // Card mirrors Tensology's proposal card: project name top-left, title, source pill
    // top-right (hover shows the exact message), description, then the actions. Clicking
    // the card opens the evidence view with the retained messages and attachments.
    function proposalIsExpired(proposal) {
        const status = String(proposal.stored_status || proposal.status || 'pending').toLowerCase();
        if (!['pending', 'candidate', 'expired'].includes(status)) return false;
        const deadline = Date.parse(proposal.expires_at || '');
        return proposal.is_expired === true || (Number.isFinite(deadline) && Date.now() >= deadline);
    }

    let proposalExpiryTimer = null;
    function scheduleProposalExpiry() {
        clearTimeout(proposalExpiryTimer);
        const deadlines = (state.incomingProposals || []).filter((row) => !proposalIsExpired(row))
            .map((row) => Date.parse(row.expires_at || '')).filter((value) => Number.isFinite(value) && value > Date.now());
        if (deadlines.length) proposalExpiryTimer = setTimeout(() => {
            renderIncomingWorkspace();
        }, Math.min(2_147_483_647, Math.max(1, Math.min(...deadlines) - Date.now() + 1)));
    }

    function proposalNeedsProject(proposal) {
        return proposal?.needs_project === true || !proposal?.project_id;
    }

    const mergeReservationNotice = 'Reserved for a merge. Retry with its original partner.';

    function incomingProposalRowHtml(proposal) {
        const expired = proposalIsExpired(proposal);
        const needsProject = proposalNeedsProject(proposal);
        const reserved = proposal.merge_source_reserved === true;
        const source = String(proposal.source_label || proposal.source || '').trim();
        const project = String(proposal.project_name || (proposal.project_id ? `Project ${proposal.project_id}` : '')).trim();
        const origin = proposal.origin === 'tensology' ? 'Synced from Tensology' : 'Detected here';
        const description = String(proposal.description || proposal.rationale || '').trim();
        const proposalId = actions.escapeHtml(proposal.id || '');
        const tooltip = proposalFirstMessage(proposal) || `${origin} · ${source}`;
        const retry = reserved && proposal.merge_retry;
        const push = retry
            ? `<button type="button" class="proposal-btn primary" data-incoming-retry-merge="${proposalId}"${incomingOperation || proposalMutations.size || dismissExpiredBusy ? ' disabled' : ''}>Retry merge</button>`
            : reserved ? '' : !needsProject
            ? `<button type="button" class="proposal-btn primary" data-incoming-proposal-thread="${proposalId}" title="Creates the thread and hands it to Decisions, like Send to Decisions.">Create Thread</button>`
            : `<button type="button" class="proposal-btn primary" data-incoming-proposal-confirm="${proposalId}">Confirm project</button>`;
        const dismiss = reserved ? '' : `<button type="button" class="proposal-btn ghost" data-incoming-proposal-dismiss="${proposalId}" title="Dismiss this proposal">Dismiss</button>`;
        const preparing = proposalReadRequest?.key === String(proposal.id);
        const read = `<button type="button" class="proposal-btn ghost${preparing ? ' is-loading' : ''}" data-incoming-proposal-read="${proposalId}" title="Read this proposal aloud"${proposalReadRequest ? ' disabled' : ''}${preparing ? ' aria-busy="true"' : ''}>${preparing ? 'Preparing...' : 'Read to me'}</button>`;
        const projectKey = actions.escapeHtml(String(proposal.project_id || ''));
        return `<article class="proposal-card${expired ? ' proposal-expired' : ''}" data-incoming-proposal-open="${proposalId}" data-proposal-project="${needsProject ? '' : projectKey}" draggable="${!needsProject}" tabindex="0" role="button" aria-label="Open evidence for ${actions.escapeHtml(proposal.title || 'proposal')}">
            ${expired ? '<span class="proposal-expired-ribbon">Expired</span>' : ''}
            <div class="proposal-head">
                <p class="proposal-project${needsProject ? ' none' : ''}">${actions.escapeHtml(needsProject ? 'Confirm project' : project)}</p>
                <h3 class="proposal-title">${actions.escapeHtml(proposal.title || 'Proposal')}</h3>
                <span class="proposal-source" title="${actions.escapeHtml(tooltip)}" data-incoming-proposal-open="${proposalId}">${incomingSourceGlyph(proposal.source)}${actions.escapeHtml(source)}</span>
            </div>
            ${description ? `<p class="proposal-desc">${actions.escapeHtml(description)}</p>` : ''}
            ${reserved ? `<p class="proposal-desc">${mergeReservationNotice}</p>` : ''}
            <div class="proposal-actions">${push}${dismiss}${read}<span class="proposal-tools"><button type="button" class="proposal-copy" data-incoming-proposal-copy="${proposalId}" title="Copy title and description" aria-label="Copy title and description"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2 2v1"></path></svg></button>${needsProject ? '' : '<span class="proposal-drag" title="Drag onto another proposal on the same project to merge them into one ticket" aria-hidden="true">⠿</span>'}</span></div>
        </article>`;
    }

    let dragProposalId = '';

    function proposalById(proposalId) {
        const modal = el('incoming-proposal-modal');
        const requiredKey = modal?.dataset.proposalId === String(proposalId) ? modal.dataset.reviewKey : '';
        return (state.incomingProposals || []).find(row => String(row.id) === String(proposalId)
            && (!requiredKey || row.review_key === requiredKey)) || null;
    }

    let proposalReviewRequest = 0;

    function clearProposalReview() {
        proposalReviewRequest++;
        closeProposalDetail();
        const status = el('incoming-proposal-review-status');
        status.textContent = '';
        status.classList.add('hidden');
    }

    async function openProposalReview(reviewKey) {
        const request = ++proposalReviewRequest;
        const status = el('incoming-proposal-review-status');
        const current = () => request === proposalReviewRequest && context.workspaceMode === 'incoming'
            && new URLSearchParams(window.location.search).get('proposal_key') === reviewKey;
        const show = (message, focus = false) => {
            status.textContent = message;
            status.classList.toggle('hidden', !message);
            if (focus) status.focus();
        };
        state.incomingFilter = 'proposals';
        state.incomingMobileDetail = false;
        renderIncomingWorkspace();
        if (!/^[a-f0-9]{64}$/.test(reviewKey)) {
            show('This proposal link is invalid.', true);
            return;
        }
        show('Opening linked proposal…');
        try {
            const result = await actions.api(`/workflows/studio/incoming/proposals/review?${new URLSearchParams({ review_key: reviewKey })}`);
            if (!current() || el('incoming-proposal-modal')) return;
            if (result.status === 'thread' && Number.isSafeInteger(result.chat_id) && result.chat_id > 0) {
                show('');
                await actions.loadChat(result.chat_id, { replaceUrl: true });
                return;
            }
            if (result.status === 'proposal' && result.proposal?.review_key === reviewKey) {
                // Wait for the initial list so a late response cannot replace the reviewed snapshot.
                await refreshIncomingData();
                if (!current() || el('incoming-proposal-modal')) return;
                const proposal = result.proposal;
                state.incomingProposals = [proposal, ...state.incomingProposals.filter(row => String(row.id) !== String(proposal.id))];
                renderIncomingWorkspace();
                show('');
                openProposalDetail(proposal.id, { reviewKey });
                return;
            }
            show(result.status === 'unconfirmed'
                ? 'Could not confirm this proposal. Reload the link to try again.'
                : 'This proposal link is no longer available.', true);
        } catch (_) {
            if (current()) show('Could not open this proposal. Reload the link to try again.', true);
        }
    }

    async function copyProposal(proposalId) {
        const proposal = proposalById(proposalId);
        if (!proposal) return;
        const text = [proposal.title || 'Proposal', proposal.description || proposal.rationale || '']
            .map((part) => String(part).trim()).filter(Boolean).join('\n\n');
        try {
            await navigator.clipboard.writeText(text);
            actions.toast('Proposal title and description copied.');
        } catch (_) {
            actions.toast('Could not copy the proposal. Check clipboard permissions and try again.', 'error');
        }
    }

    function syncProposalReadButtons() {
        el('incoming-proposals-list')?.querySelectorAll('[data-incoming-proposal-read]').forEach((button) => {
            const preparing = proposalReadRequest?.key === button.dataset.incomingProposalRead;
            button.disabled = Boolean(proposalReadRequest);
            button.classList.toggle('is-loading', preparing);
            button.setAttribute('aria-busy', String(preparing));
            button.textContent = preparing ? 'Preparing...' : 'Read to me';
        });
    }

    async function readProposalAloud(proposalId, button) {
        const proposal = proposalById(proposalId);
        if (!proposal || proposalReadRequest || proposalMutations.has(String(proposalId))) return;
        const key = String(proposalId);
        const text = [proposal.title, proposal.description || proposal.rationale]
            .map((part) => String(part || '').trim()).filter(Boolean).join('\n\n');
        if (!text) {
            actions.toast('There is nothing to read in this proposal.', 'error');
            return;
        }
        const cached = proposalSpokenText.get(key);
        const controller = new AbortController();
        proposalReadRequest = { key, controller };
        syncProposalReadButtons();
        let timedOut = false;
        const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 65_000);
        try {
            if (cached?.source === text) {
                const result = await actions.api('/chats/speak-spoken', { method: 'POST', body: { text: cached.spoken }, signal: controller.signal });
                if (result?.ok !== true) throw new Error('Speech is unavailable. Check that the Oracle is running and try again.');
                return;
            }
            const result = await actions.api('/workflows/studio/incoming/proposals/read', {
                method: 'POST',
                signal: controller.signal,
                body: { text, proposal_id: proposal.id || proposalId, title: proposal.title || '' }
            });
            const spoken = String(result?.spoken_text || '').trim();
            if (result?.success !== true || !spoken) throw new Error(result?.error || 'Could not prepare the spoken version. Please try again.');
            proposalSpokenText.set(key, { source: text, spoken });
        } catch (error) {
            if (controller.signal.aborted && !timedOut) return;
            actions.toast(timedOut ? 'Preparing speech timed out. Please try again.' : error.data?.error || error.message || 'Could not read the proposal.', 'error');
        } finally {
            clearTimeout(timeout);
            proposalReadRequest = null;
            syncProposalReadButtons();
        }
    }

    function showIncomingProgress(title, description) {
        const previousFocus = document.activeElement;
        const previousOverflow = document.body.style.overflow;
        const overlay = document.createElement('div');
        overlay.className = 'proposal-modal-overlay incoming-progress-overlay';
        overlay.innerHTML = `<section class="proposal-modal incoming-progress-modal" role="dialog" aria-modal="true" aria-labelledby="incoming-progress-title" aria-describedby="incoming-progress-description"><header><h3 id="incoming-progress-title"></h3><button type="button" class="proposal-btn ghost" aria-label="Close progress">×</button></header><div class="incoming-progress-body"><p id="incoming-progress-description"></p><progress aria-label="Processing proposals"></progress></div></section>`;
        const closeButton = overlay.querySelector('button');
        const status = el('incoming-operation-status');
        const update = (nextTitle, nextDescription) => {
            incomingOperation = nextTitle;
            overlay.querySelector('h3').textContent = nextTitle;
            overlay.querySelector('p').textContent = nextDescription;
            if (status) { status.textContent = nextTitle; status.classList.remove('hidden'); }
        };
        let closed = false;
        const close = () => {
            if (closed) return;
            closed = true;
            overlay.remove();
            document.body.style.overflow = previousOverflow;
            if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
            else el('incoming-resync')?.focus({ preventScroll: true });
        };
        update(title, description);
        const dismiss = () => { close(); actions.closeProposalReview?.(); };
        if (context.embeddedProposalReview) closeButton.setAttribute('aria-label', 'Return to proposals');
        closeButton.addEventListener('click', dismiss);
        overlay.addEventListener('click', event => { if (event.target === overlay) dismiss(); });
        overlay.addEventListener('keydown', event => {
            if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss(); }
            if (event.key === 'Tab') { event.preventDefault(); closeButton.focus(); }
        });
        document.body.style.overflow = 'hidden';
        document.body.appendChild(overlay);
        closeButton.focus();
        return { update, finish() { close(); incomingOperation = ''; status?.classList.add('hidden'); } };
    }

    async function mergeProposalCards(targetId, sourceId, retry = null) {
        if (dismissExpiredBusy || proposalMutations.size || incomingOperation) return;
        const first = proposalById(sourceId);
        const second = proposalById(targetId);
        if (!retry && (!first || !second || String(first.id) === String(second.id))) return;
        if (!retry && (proposalNeedsProject(first) || proposalNeedsProject(second) || String(first.project_id) !== String(second.project_id))) {
            actions.toast('Only proposals on the same project can be merged.', 'error');
            return;
        }
        proposalMutations.add(String(sourceId));
        proposalMutations.add(String(targetId));
        const progress = retry
            ? showIncomingProgress('Retrying merge...', 'Recovering the saved proposal and its original sources.')
            : showIncomingProgress('Merging proposals...', 'Combining the requests, source files and transcripts.');
        renderIncomingWorkspace();
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/merge', {
                method: 'POST',
                body: retry ? { retry } : { first, second }
            });
            if (!result?.chat_id) throw new Error(result?.error || 'The proposals could not be merged.');
            if (result && result.chat_id) {
                const missingFiles = Boolean(result.failed_attachments?.length);
                actions.toast(missingFiles ? proposalThreadToast(result) : 'Merged into one thread.', missingFiles ? 'error' : 'success');
                state.incomingProposals = (state.incomingProposals || []).filter(
                    (row) => String(row.id) !== String(sourceId) && String(row.id) !== String(targetId)
                );
                renderIncomingWorkspace();
                progress.update('Opening thread...', 'The merged request is ready.');
                if (actions.loadChat) await actions.loadChat(Number(result.chat_id));
                else if (actions.openThreadDialog) actions.openThreadDialog(result.chat_id);
            }
        } catch (error) {
            actions.toast(error.data?.error || error.message || 'Could not merge the proposals.', 'error');
        } finally {
            proposalMutations.delete(String(sourceId));
            proposalMutations.delete(String(targetId));
            progress.finish();
            renderIncomingWorkspace();
            if (retry && context.workspaceMode === 'incoming') {
                el('incoming-proposals-list')?.querySelector(`[data-incoming-retry-merge="${CSS.escape(sourceId)}"]`)?.focus();
            }
        }
    }

    async function resumeIncomingMerge(key) {
        const saved = state.incomingPendingMerges.find(row => row.merge_key === key);
        if (!saved || dismissExpiredBusy || proposalMutations.size || incomingOperation) return;
        const progress = showIncomingProgress('Resuming merge...', saved.local_recovery
            ? 'Recovering the saved proposals.' : 'Confirming dismissal of the remaining originals.');
        renderIncomingWorkspace();
        try {
            const endpoint = '/workflows/studio/incoming/proposals/merge';
            const result = await actions.api(saved.local_recovery ? endpoint : `${endpoint}/${Number(saved.chat_id)}/resume`, {
                method: 'POST', body: saved.local_recovery ? { local_recovery: key } : { merge_key: key }
            });
            const chatId = Number(result?.chat_id);
            if (!Number.isSafeInteger(chatId) || chatId <= 0 || (!saved.local_recovery && chatId !== Number(saved.chat_id))) {
                throw new Error('The saved merge could not be confirmed.');
            }
            state.incomingPendingMerges = state.incomingPendingMerges.filter(row => row.merge_key !== key);
            const missingFiles = Boolean(result.failed_attachments?.length);
            actions.toast(missingFiles ? proposalThreadToast(result) : 'Merged into one thread.', missingFiles ? 'error' : 'success');
            if (context.workspaceMode === 'incoming') {
                if (actions.loadChat) await actions.loadChat(Number(result.chat_id));
                else if (actions.openThreadDialog) actions.openThreadDialog(result.chat_id);
            }
        } catch (error) {
            actions.toast(error.data?.error || error.message || 'The merge could not resume.', 'error');
        } finally {
            progress.finish();
            renderIncomingWorkspace();
            if (context.workspaceMode === 'incoming') {
                el('incoming-proposals-list')?.querySelector(`[data-incoming-resume-merge="${CSS.escape(key)}"]`)?.focus();
            }
        }
    }

    function proposalSourceThreadId(proposal, source) {
        const explicit = String(proposal.source_thread_id || proposal.source_conversation?.thread_id || '').trim();
        if (explicit) return explicit;
        const prefix = `local:${source}:`;
        const id = String(proposal.id || '');
        return id.startsWith(prefix) ? id.slice(prefix.length) : '';
    }

    function findProposalConversation(proposal, source, messageIds) {
        const conversations = incomingConversations(source);
        const threadId = proposalSourceThreadId(proposal, source);
        if (threadId) return conversations.find(row => row.threadId === threadId) || null;
        if (!messageIds.length) return null;

        // Legacy proposals may have message IDs without a recorded thread.
        const wanted = new Set(messageIds);
        const matches = conversations.filter(row => row.messages.some(item => wanted.has(String(item.source_message_id || ''))));
        return matches.length === 1 ? matches[0] : null;
    }

    function proposalSourceChunk(proposal) {
        const messageIds = Array.isArray(proposal.message_ids) ? proposal.message_ids.map(String).filter(Boolean) : [];
        const scopedMessages = new Set();
        for (const ref of Array.isArray(proposal.source_scope) ? proposal.source_scope : []) {
            const channel = String(ref?.source || ref?.channel || ref?.source_conversation?.channel || '').toLowerCase();
            const thread = String(ref?.source_thread_id || ref?.source_conversation?.thread_id || '');
            if (!channel || !thread || thread.startsWith('merged:') || !Array.isArray(ref.message_ids)) continue;
            for (const id of ref.message_ids) {
                if (id) scopedMessages.add(JSON.stringify([channel, thread, String(id)]));
            }
        }
        const wanted = new Set(messageIds);
        const source = String(proposal.source || '').toLowerCase();
        const whatsapp = source === 'whatsapp';
        const contextIds = new Set((proposal.source_context_message_ids || []).map(String));
        const evidence = (Array.isArray(proposal.evidence) ? proposal.evidence : []).filter(ev => ev && (ev.id || ev.body || ev.sent_at)
            && (!whatsapp || (wanted.size ? wanted.has(String(ev.id)) : !ev.context_only && !contextIds.has(String(ev.id)))));
        if (evidence.length) {
            const ordered = evidence.slice().sort((a, b) => String(a.sent_at || '').localeCompare(String(b.sent_at || '')));
            return {
                messages: ordered,
                count: ordered.length,
                sourceCount: scopedMessages.size || ordered.length,
                start: ordered[0],
                end: ordered[ordered.length - 1],
                messageIds: ordered.map((ev) => String(ev.id || '')).filter(Boolean),
            };
        }
        const conversation = source && !proposal.source_scope?.length && !proposal.merged_from?.length
            ? findProposalConversation(proposal, source, messageIds) : null;
        const pool = (conversation?.messages || []).filter(item =>
            wanted.size ? wanted.has(String(item.source_message_id || '')) : !whatsapp);

        const matched = pool.map((item) => ({
            id: String(item.source_message_id || ''),
            body: item.text || item.snippet || '',
            sent_at: item.created_at || '',
            sender_name: item.sender || item.conversation_label || '',
            sender_address: item.sender || '',
            direction: item.direction || '',
        }));
        // Keep recorded id order when possible, else chronological.
        if (wanted.size) {
            const rank = new Map(messageIds.map((id, i) => [id, i]));
            matched.sort((a, b) => {
                const ra = rank.has(a.id) ? rank.get(a.id) : 1e9;
                const rb = rank.has(b.id) ? rank.get(b.id) : 1e9;
                if (ra !== rb) return ra - rb;
                return String(a.sent_at || '').localeCompare(String(b.sent_at || ''));
            });
        } else {
            matched.sort((a, b) => String(a.sent_at || '').localeCompare(String(b.sent_at || '')));
        }

        if (!matched.length && (scopedMessages.size || messageIds.length)) {
            return {
                messages: [],
                count: scopedMessages.size || messageIds.length,
                start: null,
                end: null,
                messageIds,
                idsOnly: true,
            };
        }
        return {
            messages: matched,
            count: matched.length || messageIds.length,
            start: matched[0] || null,
            end: matched[matched.length - 1] || null,
            messageIds: matched.map((ev) => ev.id).filter(Boolean).length
                ? matched.map((ev) => ev.id).filter(Boolean)
                : messageIds,
            idsOnly: false,
        };
    }

    function proposalEvidenceHtml(proposal) {
        const chunk = proposalSourceChunk(proposal);
        const merged = Boolean(proposal.source_scope?.length || proposal.merged_from?.length);
        if (!chunk.count && !merged) {
            return '<p class="proposal-empty">No retained source messages for this proposal.</p>';
        }
        const startLabel = chunk.start
            ? `${actions.formatDateTime(chunk.start.sent_at)}${chunk.start.sender_name ? `, ${chunk.start.sender_name}` : ''}`
            : 'start';
        const endLabel = chunk.end
            ? `${actions.formatDateTime(chunk.end.sent_at)}${chunk.end.sender_name ? `, ${chunk.end.sender_name}` : ''}`
            : 'end';
        let summary = '';
        if (chunk.idsOnly) {
            const messages = chunk.count === 1 ? 'the source message' : `the ${chunk.count} source messages`;
            summary = merged
                ? `Open an original conversation to look up ${messages}.`
                : `The text of ${messages} is unavailable.`;
        } else if (chunk.count > 1) {
            summary = `From <strong>${actions.escapeHtml(startLabel)}</strong> to <strong>${actions.escapeHtml(endLabel)}</strong>, ${chunk.count} messages`;
        }
        const jump = proposalSourceControlsHtml('proposal-source-picker', proposal.id);
        const messages = (chunk.messages || []).map((ev) => `<article class="proposal-message" data-source-message-id="${actions.escapeHtml(ev.id || '')}">
            <header><strong>${actions.escapeHtml(ev.sender_name || ev.sender_address || 'Unknown')}</strong><time>${actions.escapeHtml(actions.formatDateTime(ev.sent_at))}</time></header>
            ${ev.body ? `<p>${actions.escapeHtml(ev.body)}</p>` : '<p class="muted">(no text)</p>'}
        </article>`).join('');
        return `<div data-proposal-source-evidence>${summary ? `<p class="proposal-source-summary">${summary}</p>` : ''}${jump}${messages}</div>
            <div data-proposal-source-reader hidden></div>`;
    }

    function proposalSourceControlsHtml(pickerId, proposalId = '') {
        return `<div class="proposal-source-jump"><div class="proposal-source-choice hidden">
            <label for="${pickerId}">${proposalId ? 'Original conversation' : 'Conversation'}</label>
            <select id="${pickerId}" data-proposal-source-choice></select></div>
            <button type="button" class="proposal-btn ghost" data-proposal-jump-source="${actions.escapeHtml(proposalId)}">${proposalId ? 'View in conversation' : 'Open conversation'}</button>
            <p class="proposal-source-status hidden" data-proposal-source-status role="status"></p></div>`;
    }

    function proposalAttachmentUrl(proposal, item) {
        const token = incomingInternalToken();
        if (item.download_error || item.error) return '';
        if (context.embeddedProposalReview) return item.url || '';
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

    function formatAttachmentBytes(value) {
        const size = Number(value) || 0;
        if (size <= 0) return '';
        if (size < 1024) return `${size} B`;
        if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatAttachmentDuration(seconds) {
        const total = Math.round(Number(seconds) || 0);
        if (!total || total < 0) return '';
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return mins > 0 ? `${mins}:${String(secs).padStart(2, '0')}` : `${secs}s`;
    }

    function attachmentMimeBase(value) {
        return String(value || '').split(';', 1)[0].trim().toLowerCase();
    }

    function isAudioAttachment(item) {
        const mime = attachmentMimeBase(item?.mime_type);
        const kind = String(item?.kind || '').toLowerCase();
        const name = String(item?.name || '');
        return mime.startsWith('audio/') || ['audio', 'voice', 'ptt'].includes(kind) || /\.(oga|ogg|opus|mp3|m4a|wav|aac)$/i.test(name);
    }

    function isImageAttachment(item) {
        const mime = attachmentMimeBase(item?.mime_type);
        const kind = String(item?.kind || '').toLowerCase();
        return mime.startsWith('image/') || ['image', 'photo', 'sticker'].includes(kind);
    }

    function proposalAttachmentLabel(item) {
        const kind = String(item?.kind || '').toLowerCase();
        const mime = attachmentMimeBase(item?.mime_type);
        const name = String(item?.name || '').trim();
        if (['voice', 'ptt'].includes(kind) || (mime.startsWith('audio/') && /voice|ptt|whatsapp_voice/i.test(name))) return 'Voice note';
        if (mime.startsWith('audio/') || kind === 'audio') return 'Audio';
        if (isImageAttachment(item)) return 'Image';
        if (mime === 'application/pdf' || /\.pdf$/i.test(name)) return 'PDF';
        if (name) {
            const base = name.replace(/^whatsapp[_-]/i, '').replace(/[_-]+/g, ' ').replace(/\.[a-z0-9]+$/i, '');
            if (base && base.length <= 48) return base.replace(/\b\w/g, (ch) => ch.toUpperCase());
        }
        return name || 'Attachment';
    }

    function proposalAttachmentMetaLine(item) {
        const parts = [];
        const duration = formatAttachmentDuration(item?.duration ?? item?.media_duration);
        const size = formatAttachmentBytes(item?.size);
        if (duration) parts.push(duration);
        if (size) parts.push(size);
        const mime = attachmentMimeBase(item?.mime_type);
        if (mime && !mime.startsWith('audio/')) parts.push(mime);
        else if (mime.startsWith('audio/') && !duration) parts.push(mime.split('/')[1] || 'audio');
        return parts.join(' · ');
    }

    function normalizeProposalTask(item, index) {
        if (item && typeof item === 'object' && !Array.isArray(item)) {
            const title = String(item.title || item.name || item.task || item.summary || '').trim();
            const description = String(item.description || item.details || item.detail || item.body || '').trim();
            const acceptance = item.acceptance_criteria || item.acceptance || item.done_when || item.criteria || '';
            const acceptanceText = Array.isArray(acceptance)
                ? acceptance.map((row) => String(row || '').trim()).filter(Boolean).join('\n')
                : String(acceptance || '').trim();
            const status = String(item.status || item.state || '').trim() || 'To do';
            const estimate = item.estimate || item.estimated_minutes || item.minutes || '';
            return {
                number: index + 1,
                title: title || `Task ${index + 1}`,
                description,
                acceptance: acceptanceText,
                status,
                estimate: estimate === '' || estimate == null ? '' : String(estimate),
                rich: Boolean(title || description || acceptanceText || item.status),
            };
        }
        const title = String(item || '').trim();
        return {
            number: index + 1,
            title: title || `Task ${index + 1}`,
            description: '',
            acceptance: '',
            status: 'To do',
            estimate: '',
            rich: false,
        };
    }

    function stripTranscriptionPrefix(text) {
        return String(text || '').replace(/^\[(?:Transcription|Transcript)\]\s*/i, '').trim();
    }

    function proposalAttachmentTranscript(proposal, attachment) {
        const direct = stripTranscriptionPrefix(attachment?.transcript || attachment?.caption || '');
        if (direct) return direct;
        const mid = String(attachment?.source_message_id || attachment?.message_id || '').trim();
        const name = String(attachment?.name || '').trim();
        const rows = state.incoming || [];
        const hit = rows.find((item) => {
            if (mid && String(item.source_message_id || '') === mid) return true;
            if (name && String(item.media_filename || '') === name) return true;
            return false;
        });
        if (!hit) return '';
        return stripTranscriptionPrefix(hit.text || hit.snippet || '');
    }

    function loadProposalTaskDone(proposal) {
        const indexes = proposal?.task_completed_indexes;
        return new Set(Array.isArray(indexes) ? indexes.filter((n) => Number.isInteger(n) && n >= 0 && n < (proposal.tasks || []).length) : []);
    }

    function proposalTasksHtml(proposal) {
        const items = Array.isArray(proposal.tasks) ? proposal.tasks : [];
        if (!items.length) return '<p class="proposal-empty">No tasks listed.</p>';
        const done = loadProposalTaskDone(proposal);
        const cards = items.map((raw, index) => {
            const task = normalizeProposalTask(raw, index);
            const isDone = done.has(index);
            const details = [];
            if (task.description) {
                details.push(`<p class="proposal-task-description">${actions.escapeHtml(task.description)}</p>`);
            }
            if (task.acceptance) {
                details.push(`<p class="proposal-task-acceptance"><span>Acceptance</span> ${actions.escapeHtml(task.acceptance)}</p>`);
            }
            if (task.estimate) {
                details.push(`<p class="proposal-task-estimate">${actions.escapeHtml(task.estimate)}${/^\d+$/.test(task.estimate) ? ' min' : ''}</p>`);
            }
            const body = details.length
                ? `<div class="proposal-task-body">${details.join('')}</div>`
                : '';
            return `<li class="proposal-task-card${isDone ? ' is-done' : ''}" data-proposal-task-index="${index}">
                <div class="proposal-task-head">
                    <button type="button" class="proposal-task-check" role="checkbox" aria-checked="${isDone ? 'true' : 'false'}" aria-label="${isDone ? 'Mark task not done' : 'Mark task done'}" data-proposal-task-toggle="${index}">
                        <span class="proposal-task-number" aria-hidden="true">${isDone ? '✓' : task.number}</span>
                    </button>
                    <div class="proposal-task-copy">
                        <strong class="proposal-task-title">${actions.escapeHtml(task.title)}</strong>
                    </div>
                </div>
                ${body}
            </li>`;
        }).join('');
        return `<p class="proposal-task-save-status" role="status" aria-live="polite" data-proposal-task-save-status></p><ol class="proposal-task-list" data-proposal-tasks-for="${actions.escapeHtml(String(proposal.id || ''))}">${cards}</ol>`;
    }

    // Proposals have no local file storage yet, so the shared drop zone is shown read-only.
    const PROPOSAL_DROP_ZONE = fileDropZoneHtml('proposal-file-drop', { disabled: true, message: 'Files from the source conversation. They are attached to the thread and its ticket when you accept.' });

    function proposalFilesHtml(proposal) {
        return PROPOSAL_DROP_ZONE + proposalFileListHtml(proposal);
    }

    function proposalFileListHtml(proposal) {
        const items = proposal.attachments || [];
        if (!items.length) return '<p class="proposal-empty">No attachments on this proposal.</p>';
        const images = [];
        const files = [];
        items.forEach((a) => {
            const err = a.download_error || a.error || '';
            if (err) {
                files.push(`<div class="proposal-file-card proposal-file-failed" role="status" title="${actions.escapeHtml(err)}"><div class="proposal-file-card-head"><span class="proposal-file-icon" aria-hidden="true">!</span><div class="proposal-file-meta"><strong>${actions.escapeHtml(proposalAttachmentLabel(a))}</strong><span>${actions.escapeHtml(err)}</span></div></div></div>`);
                return;
            }
            const url = proposalAttachmentUrl(proposal, a);
            const label = proposalAttachmentLabel(a);
            const meta = proposalAttachmentMetaLine(a);
            const caption = a.caption || a.transcript || '';
            const payload = actions.escapeHtml(JSON.stringify({
                url,
                name: label,
                filename: a.name || label,
                mime_type: a.mime_type || '',
                caption,
            }));
            if (!url) {
                files.push(`<div class="proposal-file-card proposal-file-failed" role="status"><div class="proposal-file-card-head"><span class="proposal-file-icon" aria-hidden="true">?</span><div class="proposal-file-meta"><strong title="${actions.escapeHtml(a.name || label)}">${actions.escapeHtml(label)}</strong><span>No download link yet. Open Files again after sync.</span></div></div></div>`);
                return;
            }
            if (isImageAttachment(a) && !context.embeddedProposalReview) {
                images.push(`<button type="button" class="proposal-media" data-file-preview="${payload}" title="${actions.escapeHtml(caption || label)}"><img src="${url}" alt="${actions.escapeHtml(label)}" loading="lazy" /></button>`);
                return;
            }
            if (isAudioAttachment(a) && !context.embeddedProposalReview) {
                const fileName = String(a.name || label || 'audio').trim() || 'audio';
                const sizeOnly = formatAttachmentBytes(a.size);
                const transcript = proposalAttachmentTranscript(proposal, a);
                const transcriptBlock = transcript
                    ? `<details class="proposal-file-transcript"><summary>Transcription</summary><p>${actions.escapeHtml(transcript)}</p></details>`
                    : '';
                files.push(`<div class="proposal-file-card proposal-file-audio">
                    <div class="proposal-file-audio-meta">
                        <strong class="proposal-file-name" title="${actions.escapeHtml(fileName)}">${actions.escapeHtml(fileName)}</strong>
                        ${sizeOnly ? `<span class="proposal-file-size">${actions.escapeHtml(sizeOnly)}</span>` : ''}
                    </div>
                    <audio class="proposal-file-audio-player" controls preload="metadata" src="${actions.escapeHtml(url)}"></audio>
                    ${transcriptBlock}
                </div>`);
                return;
            }
            files.push(`<button type="button" class="proposal-file-card" data-file-preview="${payload}">
                <div class="proposal-file-card-head">
                    <span class="proposal-file-icon" aria-hidden="true">▣</span>
                    <div class="proposal-file-meta">
                        <strong title="${actions.escapeHtml(a.name || label)}">${actions.escapeHtml(label)}</strong>
                        <span>${actions.escapeHtml(meta || 'File')}</span>
                    </div>
                </div>
            </button>`);
        });
        const imageBlock = images.length ? `<div class="proposal-media-grid">${images.join('')}</div>` : '';
        const fileBlock = files.length ? `<div class="proposal-file-list">${files.join('')}</div>` : '';
        return `${imageBlock}${fileBlock}` || '<p class="proposal-empty">No attachments on this proposal.</p>';
    }

    const mediaTranscriptions = new Map();

    function closeFilePreview() {
        const overlay = document.getElementById('incoming-file-preview-modal');
        if (!overlay) return;
        overlay.querySelectorAll('audio, video').forEach(player => {
            player.pause();
            player.removeAttribute('src');
            player.load();
        });
        overlay.remove();
        if (overlay._releaseFileUrl) overlay._releaseFileUrl();
        document.body.style.overflow = overlay._previousOverflow || '';
        document.removeEventListener('keydown', onFilePreviewKey, true);
        if (overlay._returnFocus?.isConnected) overlay._returnFocus.focus({ preventScroll: true });
    }

    function onFilePreviewKey(event) {
        const overlay = document.getElementById('incoming-file-preview-modal');
        if (!overlay) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            closeFilePreview();
        }
        if (event.key === 'Tab') {
            const nodes = [...overlay.querySelectorAll('button, a[href], audio, video, iframe, [tabindex="0"]')]
                .filter(node => !node.hidden && !node.disabled && node.getClientRects().length);
            const first = nodes[0], last = nodes[nodes.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
    }

    function openFilePreview(meta) {
        if (!meta?.url) return;
        const returnFocus = document.activeElement;
        closeFilePreview();
        const mime = String(meta.mime_type || '').toLowerCase();
        const filename = meta.filename || meta.name || 'Attachment';
        const name = meta.display_name || meta.name || 'Attachment';
        let body, playable = false, isPdf = false;
        if (mime.startsWith('image/') || /\.(png|jpe?g|gif|webp|avif)$/i.test(filename) || isImageAttachment({ mime_type: mime, name: filename })) {
            body = `<img class="file-preview-image" src="${actions.escapeHtml(meta.url)}" alt="${actions.escapeHtml(filename)}">`;
        } else if (mime.startsWith('audio/') || isAudioAttachment({ mime_type: mime, name: filename, kind: meta.kind })) {
            playable = true;
            body = `<audio class="file-preview-audio" controls autoplay preload="metadata" src="${actions.escapeHtml(meta.url)}"></audio>`;
        } else if (mime.startsWith('video/') || /\.(mp4|webm|mov|3gp)$/i.test(filename)) {
            playable = true;
            body = `<video class="file-preview-image" controls autoplay playsinline preload="metadata" src="${actions.escapeHtml(meta.url)}"></video>`;
        } else if (mime === 'application/pdf' || /\.pdf$/i.test(filename)) {
            // 2026-11-11: the embedded PDF viewer has its own download, so no footer button or generic "PDF" title.
            isPdf = true;
            body = `<iframe class="file-preview-frame" title="${actions.escapeHtml(filename)}" src="${actions.escapeHtml(meta.url)}"></iframe>`;
        } else if (mime.startsWith('text/') || mime === 'application/json' || /\.(txt|md|csv|json|log)$/i.test(filename)) {
            body = '<pre class="file-preview-text" data-file-preview-text>Loading...</pre>';
        } else {
            body = '<p class="file-preview-other">Preview is not available for this file type. Download the original to open it.</p>';
        }
        const overlay = document.createElement('div');
        overlay.className = 'proposal-modal-overlay file-preview-overlay';
        overlay.id = 'incoming-file-preview-modal';
        overlay._previousOverflow = document.body.style.overflow;
        overlay._returnFocus = returnFocus;
        overlay._releaseFileUrl = meta.releaseUrl;
        // Title: file-type icon plus the complete file name (wraps, never truncated).
        const titleIcon = fileTypeIconSvg(attachmentFileKind({ mime_type: mime, filename }), 24);
        overlay.innerHTML = `<div class="proposal-modal file-preview-modal${playable ? ' has-transcript' : ''}" role="dialog" aria-modal="true" aria-label="${actions.escapeHtml(name)}">
            <header><div class="proposal-modal-title"><h3 class="file-preview-title" title="${actions.escapeHtml(filename)}">${titleIcon}<span class="file-preview-title-name">${actions.escapeHtml(filename)}</span></h3>${meta.caption ? `<p class="proposal-modal-desc">${actions.escapeHtml(meta.caption)}</p>` : ''}</div><button type="button" class="proposal-modal-close" aria-label="Close" data-file-preview-close>×</button></header>
            <div class="proposal-modal-body file-preview-body">${body}
                <p class="file-preview-error hidden" data-media-error role="alert">This media could not be played. Download the original to open it in another player.</p>
                ${playable ? '<section class="file-preview-transcript" aria-label="Transcript"><h4>Transcript</h4><div data-media-transcript role="status" aria-live="polite"></div><button class="proposal-btn ghost hidden" type="button" data-transcript-retry>Retry transcription</button></section>' : ''}
            </div>
            ${isPdf ? '' : `<footer><a class="proposal-btn ghost" href="${actions.escapeHtml(meta.url)}" download="${actions.escapeHtml(filename)}">Download</a></footer>`}
        </div>`;
        document.body.style.overflow = 'hidden';
        document.body.appendChild(overlay);
        overlay.addEventListener('click', event => { if (event.target === overlay) closeFilePreview(); });
        overlay.querySelector('[data-file-preview-close]').addEventListener('click', closeFilePreview);
        document.addEventListener('keydown', onFilePreviewKey, true);
        overlay.querySelector('[data-file-preview-close]').focus({ preventScroll: true });
        const player = overlay.querySelector('audio, video');
        overlay.querySelector('img')?.addEventListener('error', () => { const notice = overlay.querySelector('[data-media-error]'); notice.textContent = 'The image could not be loaded. Download the original to open it.'; notice.classList.remove('hidden'); });
        player?.addEventListener('error', () => overlay.querySelector('[data-media-error]').classList.remove('hidden'));
        if (player) player.play().catch(() => { /* Native controls remain available if autoplay is blocked. */ });
        const transcriptNode = overlay.querySelector('[data-media-transcript]');
        const retry = overlay.querySelector('[data-transcript-retry]');
        const showTranscript = data => {
            if (!overlay.isConnected) return;
            transcriptNode.textContent = data.transcript || 'No speech detected.';
            retry.classList.add('hidden');
        };
        const generateTranscript = async () => {
            transcriptNode.textContent = 'Transcribing...';
            retry.classList.add('hidden');
            try {
                if (!mediaTranscriptions.has(meta.url)) {
                    mediaTranscriptions.set(meta.url, actions.api(meta.transcription_url.replace(/^\/api/, ''), { method: 'POST', body: {} })
                        .catch(error => { mediaTranscriptions.delete(meta.url); throw error; }));
                }
                const data = await mediaTranscriptions.get(meta.url);
                document.querySelectorAll('[data-thread-file-preview]').forEach(button => {
                    const item = JSON.parse(button.dataset.threadFilePreview);
                    if (item.url !== meta.url) return;
                    Object.assign(item, { transcript: data.transcript, transcript_status: data.status });
                    button.dataset.threadFilePreview = JSON.stringify(item);
                    const tip = `${item.name}\n\nTranscript:\n${data.transcript || 'No speech detected.'}`;
                    button.title = tip;
                    button.dataset.tooltip = tip;
                });
                showTranscript(data);
            } catch (_) {
                if (!overlay.isConnected) return;
                transcriptNode.textContent = 'Transcription could not be completed.';
                retry.classList.remove('hidden');
            }
        };
        if (playable) {
            if (meta.transcript || meta.transcript_status === 'empty') showTranscript(meta);
            else if (meta.transcription_url) generateTranscript();
            else transcriptNode.textContent = 'No transcript available.';
            retry.addEventListener('click', generateTranscript);
        }
        const textNode = overlay.querySelector('[data-file-preview-text]');
        if (textNode) fetch(meta.url).then(response => {
            if (!response.ok) {
                const url = new URL(meta.url, window.location.href);
                const linkedProposal = url.pathname.startsWith('/api/workflows/studio/incoming/proposals/files/')
                    && url.searchParams.has('review_key');
                if (linkedProposal && response.status === 404) {
                    overlay.querySelector('footer')?.classList.add('hidden');
                    textNode.setAttribute('role', 'status');
                    return 'This attachment is no longer available on the linked proposal. Reopen the proposal to check its files.';
                }
                return 'Could not load the file.';
            }
            return response.text();
        }).then(text => { textNode.textContent = text; })
            .catch(() => { textNode.textContent = 'Could not load the file.'; });
    }

    function bindProposalTaskToggles(root, getReviewed, onSaved) {
        if (!root) return;
        const list = root.querySelector('[data-proposal-tasks-for]');
        if (!list) return;
        renderCheckStatus();
        let saving = false;
        const status = root.querySelector('[data-proposal-task-save-status]');
        const apply = (card, index, isDone) => {
            card.classList.toggle('is-done', isDone);
            const button = card.querySelector('[data-proposal-task-toggle]');
            const number = button?.querySelector('.proposal-task-number');
            if (button) {
                button.setAttribute('aria-checked', isDone ? 'true' : 'false');
                button.setAttribute('aria-label', isDone ? 'Mark task not done' : 'Mark task done');
            }
            if (number) number.textContent = isDone ? '✓' : String(index + 1);
        };
        list.querySelectorAll('[data-proposal-task-toggle]').forEach((button) => {
            const activate = async () => {
                if (saving) return;
                const index = Number(button.getAttribute('data-proposal-task-toggle'));
                if (!Number.isFinite(index)) return;
                const card = button.closest('.proposal-task-card');
                if (!card) return;
                const current = getReviewed();
                const restoreFocus = document.activeElement === button;
                const done = loadProposalTaskDone(current);
                if (done.has(index)) done.delete(index);
                else done.add(index);
                saving = true;
                list.setAttribute('aria-busy', 'true');
                list.querySelectorAll('[data-proposal-task-toggle]').forEach((control) => { control.disabled = true; });
                if (status) { status.textContent = 'Saving...'; status.classList.remove('error'); }
                try {
                    const result = await actions.api('/workflows/studio/incoming/proposals/details', {
                        method: 'PUT', body: {
                            proposal_id: current.id,
                            message_ids: current.message_ids || [],
                            expected_updated_at: current.updated_at || '',
                            ...(current.review_key ? { reviewed_proposal: current } : {}),
                            task_completed_indexes: [...done].sort((a, b) => a - b),
                        },
                    });
                    if (!result?.success || !result.proposal) throw new Error(result?.error || 'Could not save task completion.');
                    state.incomingProposals = state.incomingProposals.map((row) => String(row.id) === String(current.id) ? result.proposal : row);
                    onSaved?.(result.proposal);
                    const saved = loadProposalTaskDone(getReviewed());
                    list.querySelectorAll('[data-proposal-task-index]').forEach((node) => {
                        const taskIndex = Number(node.getAttribute('data-proposal-task-index'));
                        apply(node, taskIndex, saved.has(taskIndex));
                    });
                    if (status) status.textContent = 'Saved';
                } catch (error) {
                    if (status) {
                        status.textContent = error.data?.error || error.message || 'Could not save task completion. Try again.';
                        status.classList.add('error');
                    }
                } finally {
                    saving = false;
                    list.setAttribute('aria-busy', 'false');
                    list.querySelectorAll('[data-proposal-task-toggle]').forEach((control) => { control.disabled = false; });
                    if (restoreFocus && button.isConnected && document.activeElement === document.body) button.focus();
                }
            };
            button.addEventListener('click', activate);
            button.addEventListener('keydown', (event) => {
                if (event.key === ' ' || event.key === 'Enter') {
                    event.preventDefault();
                    activate();
                }
            });
        });
    }

    function bindProposalFilePreview(root) {
        root?.querySelectorAll('[data-file-preview]').forEach((node) => {
            node.addEventListener('click', async () => {
                try {
                    const meta = JSON.parse(node.getAttribute('data-file-preview') || '{}');
                    if (context.embeddedProposalReview) {
                        node.disabled = true;
                        const file = await actions.readProposalAttachment(meta.url);
                        if (!node.isConnected) return;
                        node.disabled = false;
                        node.focus({ preventScroll: true });
                        const url = URL.createObjectURL(file);
                        openFilePreview({ ...meta, url, releaseUrl: () => URL.revokeObjectURL(url) });
                    } else openFilePreview(meta);
                } catch (error) {
                    if (context.embeddedProposalReview && node.isConnected) {
                        node.disabled = false;
                        actions.toast(error.message || 'Could not load the attachment.', 'error');
                    }
                }
            });
        });
    }

    async function ensureProposalFilesForUi(proposal) {
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/files/ensure', {
                method: 'POST',
                body: proposal.review_key ? { review_key: proposal.review_key } : { proposal },
            });
            const attachments = [...(result.attachments || []), ...(result.failures || [])];
            // Keep the source manifest intact for reopening, retrying, and thread handoff.
            // Managed preview records do not contain the original scoped download links.
            return attachments;
        } catch (error) {
            const message = error?.message || String(error);
            return [{ name: 'Attachments', download_error: message }];
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
        if (event.key === 'Escape') dismissProposalDetail();
    }

    function dismissProposalDetail() {
        closeProposalDetail();
        actions.closeProposalReview?.();
    }



    async function ensureIncomingRowsLoaded() {
        if ((state.incoming || []).length) return state.incoming;
        try {
            await refreshIncomingData({ force: false });
        } catch (_) { /* soft */ }
        return state.incoming || [];
    }

    function refreshProposalSourcePane(overlay, proposal) {
        if (!overlay || !proposal) return null;
        const sourcePane = overlay.querySelector('[data-proposal-pane="source"]');
        const sourceTab = overlay.querySelector('[data-proposal-tab="source"]');
        if (!sourcePane) return null;
        const chunk = proposalSourceChunk(proposal);
        sourcePane.innerHTML = proposalEvidenceHtml(proposal);
        sourcePane.querySelectorAll('[data-proposal-jump-source]').forEach((button) => {
            button.addEventListener('click', () => jumpProposalSource(proposal));
        });
        if (sourceTab) {
            const count = chunk.count || 0;
            sourceTab.innerHTML = `Source${count ? `<span class="proposal-tab-count">${count}</span>` : ''}`;
        }
        return chunk;
    }

    function showProposalSourceReader(button, choice, result) {
        const pane = button.closest('[data-proposal-pane="source"]');
        const reader = pane.querySelector('[data-proposal-source-reader]');
        const evidence = pane.querySelector('[data-proposal-source-evidence]');
        const original = Array.isArray(result?.items)
            ? result.items.filter(item => item?.source_message_id === result.anchor_id) : [];
        if (!result?.source || !result?.thread_id || !result?.anchor_id || original.length !== 1
            || (choice && (result.source_key !== choice.value || result.source !== choice.dataset.source
                || result.thread_id !== choice.dataset.threadId))
            || result.items.some(item => item?.source !== result.source || item?.source_thread_id !== result.thread_id)) {
            throw new Error('The original message could not be confirmed in this conversation.');
        }
        const label = original[0].conversation_label || choice?.textContent || '';
        reader.innerHTML = `<div class="proposal-source-reader-header">
            ${choice ? `<button type="button" class="proposal-btn ghost" data-source-back>${pane.closest('dialog') ? 'Back to conversations' : 'Back to source evidence'}</button>` : ''}
            ${label ? `<h4>${actions.escapeHtml(label)}</h4>` : ''}
        </div>${result.items.map(item => {
            const anchor = item === original[0];
            const text = item.text || item.snippet || item.transcript || '';
            const attachments = (item.attachments || []).map(file => file.filename || file.name).filter(Boolean);
            return `<article class="proposal-message${anchor ? ' proposal-message-original' : ''}"${anchor ? ' tabindex="-1" data-original-message' : ''}>
                ${anchor ? '<strong class="proposal-original-label">Original message</strong>' : ''}
                <header><strong>${actions.escapeHtml(item.from_me ? 'You' : item.sender || 'Unknown sender')}</strong>
                    ${item.created_at ? `<time>${actions.escapeHtml(actions.formatDateTime(item.created_at))}</time>` : ''}</header>
                ${text ? `<p>${actions.escapeHtml(text)}</p>` : '<p class="muted">No message text</p>'}
                ${attachments.length ? `<p>${actions.escapeHtml(attachments.join(', '))}</p>` : ''}
            </article>`;
        }).join('')}`;
        const top = pane.scrollTop;
        reader.querySelector('[data-source-back]')?.addEventListener('click', () => {
            reader.hidden = true;
            reader.replaceChildren();
            evidence.hidden = false;
            pane.scrollTop = top;
            button.focus({ preventScroll: true });
        });
        evidence.hidden = true;
        reader.hidden = false;
        const anchor = reader.querySelector('[data-original-message]');
        anchor.focus({ preventScroll: true });
        anchor.scrollIntoView({ block: 'nearest' });
    }

    async function loadProposalSource(button, read, { embedded = false, active = () => true } = {}) {
        const controls = button?.closest('.proposal-source-jump');
        const picker = controls?.querySelector('[data-proposal-source-choice]');
        const notice = controls?.querySelector('[data-proposal-source-status]');
        if (!button || !picker || !notice || button.disabled) return;
        button.disabled = true;
        picker.disabled = true;
        notice.classList.remove('hidden');
        notice.textContent = 'Opening the original conversation…';
        const isCurrent = () => button.isConnected && active();
        try {
            const result = await read(picker.value);
            if (!isCurrent()) return;
            if (!result?.choices) {
                if (!embedded) {
                    showProposalSourceReader(button, picker.value ? picker.selectedOptions[0] : null, result);
                    notice.classList.add('hidden');
                    picker.disabled = false;
                    button.disabled = false;
                }
                return;
            }
            picker.replaceChildren(new Option('Choose a conversation', ''), ...result.choices.map(choice => {
                const option = new Option(choice.label, choice.key);
                option.dataset.source = choice.source || '';
                option.dataset.threadId = choice.thread_id || '';
                return option;
            }));
            picker.closest('.proposal-source-choice').classList.remove('hidden');
            picker.disabled = false;
            picker.onchange = () => {
                button.disabled = !picker.value;
                notice.classList.add('hidden');
            };
            notice.classList.add('hidden');
            picker.focus();
        } catch (error) {
            if (!isCurrent()) return;
            notice.textContent = error.message || 'Could not open the original conversation.';
            button.disabled = false;
            picker.disabled = false;
            button.focus({ preventScroll: true });
        }
    }

    let submittedSourceDialog;
    function closeSubmittedProposalSource() {
        submittedSourceDialog?.close();
    }

    function openSubmittedProposalSource({ chatId, reviewKey, opener }) {
        closeSubmittedProposalSource();
        const dialog = document.createElement('dialog');
        submittedSourceDialog = dialog;
        dialog.className = 'studio-dialog submitted-proposal-source-dialog';
        dialog.setAttribute('aria-labelledby', 'submitted-proposal-source-title');
        dialog.innerHTML = `<form method="dialog">
            <header><div><h2 id="submitted-proposal-source-title">Original conversation</h2></div>
                <button type="button" class="proposal-btn ghost" data-submitted-source-close>Close</button></header>
            <div class="submitted-proposal-source-body" data-proposal-pane="source">
                <div data-proposal-source-evidence>${proposalSourceControlsHtml('submitted-proposal-source-picker')}</div>
                <div data-proposal-source-reader hidden></div>
            </div>
        </form>`;
        const controller = new AbortController();
        dialog.addEventListener('close', () => {
            controller.abort();
            dialog.remove();
            if (submittedSourceDialog !== dialog) return;
            submittedSourceDialog = null;
            if (opener?.getClientRects().length) opener.focus({ preventScroll: true });
        }, { once: true });
        document.body.appendChild(dialog);
        dialog.querySelector('[data-submitted-source-close]').addEventListener('click', () => dialog.close());
        dialog.showModal();
        const button = dialog.querySelector('[data-proposal-jump-source]');
        const read = () => loadProposalSource(button, sourceKey => {
            const params = new URLSearchParams({ review_key: reviewKey, chat_id: chatId });
            if (sourceKey) params.set('source_key', sourceKey);
            return actions.api(`/workflows/studio/incoming/proposals/source?${params}`, { signal: controller.signal });
        }, { active: () => dialog.open && submittedSourceDialog === dialog });
        button.addEventListener('click', read);
        void read();
    }

    async function jumpProposalSource(proposal) {
        if (context.embeddedProposalReview || proposal.source_scope?.length || proposal.merged_from?.length) {
            const overlay = el('incoming-proposal-modal');
            const button = overlay?.querySelector('[data-proposal-jump-source]');
            const read = async sourceKey => {
                if (context.embeddedProposalReview) return actions.openProposalSource(sourceKey);
                const reviewKey = overlay.dataset.reviewKey;
                if (!reviewKey) throw new Error('Reopen the proposal to check its original conversations.');
                const params = new URLSearchParams({ review_key: reviewKey });
                if (sourceKey) params.set('source_key', sourceKey);
                const result = await actions.api(`/workflows/studio/incoming/proposals/source?${params}`);
                if (overlay.dataset.reviewKey !== reviewKey) {
                    throw new Error('The proposal changed. Reopen it to check its original conversations.');
                }
                return result;
            };
            return loadProposalSource(button, read, { embedded: context.embeddedProposalReview });
        }
        const chunk = proposalSourceChunk(proposal);
        const source = String(proposal.source || '').toLowerCase();
        const selectionKey = source === 'whatsapp' ? 'selectedIncomingConversationKey' : MAIL_CHANNELS[source]?.stateKey;
        const match = selectionKey && findProposalConversation(proposal, source, chunk.messageIds);
        if (!match || state.incomingChannels[source]?.error) {
            const notice = el('incoming-proposal-modal')?.querySelector('[data-proposal-source-status]');
            if (notice) {
                notice.textContent = 'The original conversation is not available in Incoming.';
                notice.classList.remove('hidden');
            }
            return;
        }
        closeProposalDetail();
        state.incomingFilter = source;
        if (source === 'whatsapp') {
            selectIncomingConversation(match.key);
        } else {
            state[selectionKey] = match.key;
            state.incomingMobileDetail = true;
            renderIncomingWorkspace();
        }
        requestAnimationFrame(() => {
            if (context.workspaceMode !== 'incoming' || state.incomingFilter !== source || state[selectionKey] !== match.key) return;
            const reader = el(`incoming-${source}-reader`);
            if (!reader) return;
            const attr = source === 'whatsapp' ? 'data-wa-mid' : 'data-mail-id';
            const exactMessage = chunk.messageIds.map(id => reader.querySelector(`[${attr}="${CSS.escape(id)}"]`)).find(Boolean);
            const target = exactMessage || reader;
            if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
            target.focus({ preventScroll: true });
            if (exactMessage) exactMessage.scrollIntoView({ block: 'center' });
        });
    }

    // Tabbed detail, mirroring Tensology's ticket view (Details / Tasks / Files ...).
    function openProposalDetail(proposalId, { reviewKey = '' } = {}) {
        const proposal = (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId));
        if (!proposal) return;
        let reviewed = proposal;
        closeProposalDetail();
        const source = String(proposal.source_label || proposal.source || '').trim();
        const project = String(proposal.project_name || 'Confirm project').trim();
        const reserved = proposal.merge_source_reserved === true;
        const conversation = proposal.source_conversation || {};
        const tasks = proposal.tasks || [];
        const files = proposal.attachments || [];
        const meta = [
            ['Participant', proposal.participant || conversation.participant_name || conversation.participant_address || ''],
            ['Channel', conversation.channel || source],
            ['Priority', proposal.priority || ''],
            ['Source thread', conversation.thread_id || ''],
        ].filter((row) => row[1]);
        const linkedCandidates = Array.isArray(proposal.candidate_projects);
        const projects = [...(linkedCandidates ? proposal.candidate_projects : (context.projects || []))];
        if (!linkedCandidates && proposal.project_id && !projects.some((p) => Number(p.id) === Number(proposal.project_id))) {
            projects.push({ id: proposal.project_id, name: project });
        }
        const projectOptions = '<option value="">Choose linked project</option>' + projects
            .sort((a, b) => String(a.name).localeCompare(String(b.name)))
            .map((p) => `<option value="${Number(p.id)}"${Number(p.id) === Number(proposal.project_id) ? ' selected' : ''}>${actions.escapeHtml(p.name)}</option>`).join('');
        const details = `${reserved ? `<p class="proposal-modal-desc">${mergeReservationNotice}</p>` : ''}
            <div class="proposal-project-field"><label for="proposal-project-select">Project</label>
            <select id="proposal-project-select" aria-describedby="proposal-project-status"${reserved ? ' disabled' : ''}>${projectOptions}</select>
            <span id="proposal-project-status" role="status" aria-live="polite"></span></div>
            <dl class="proposal-modal-meta">${meta.map(([k, v]) => `<div><dt>${actions.escapeHtml(k)}</dt><dd>${actions.escapeHtml(v)}</dd></div>`).join('')}</dl>
            ${proposal.description ? `<p class="proposal-modal-desc">${actions.escapeHtml(proposal.description)}</p>` : ''}
            ${proposal.rationale ? `<p class="proposal-modal-rationale"><span>Why</span> ${actions.escapeHtml(proposal.rationale)}</p>` : ''}`;
        const tasksPane = proposalTasksHtml(proposal);
        const sourceChunk = proposalSourceChunk(proposal);
        const tabs = [['details', 'Details', 0], ['tasks', 'Tasks', tasks.length], ['files', 'Files', files.length], ['source', 'Source', sourceChunk.sourceCount ?? sourceChunk.count]];
        const tabBar = tabs.map(([id, label, count], i) => `<button type="button" role="tab" class="proposal-tab${i === 0 ? ' active' : ''}" aria-selected="${i === 0}" data-proposal-tab="${id}">${label}${count ? `<span class="proposal-tab-count">${count}</span>` : ''}</button>`).join('');
        const pane = (id, html, active) => `<div class="proposal-pane${active ? '' : ' hidden'}" role="tabpanel" data-proposal-pane="${id}">${html}</div>`;
        const overlay = document.createElement('div');
        overlay.className = 'proposal-modal-overlay';
        overlay.id = 'incoming-proposal-modal';
        overlay.dataset.proposalId = String(proposalId);
        overlay.dataset.reviewKey = proposal.review_key || reviewKey;
        overlay.innerHTML = `<div class="proposal-modal" role="dialog" aria-modal="true" aria-label="${actions.escapeHtml(proposal.title || 'Proposal')}">
            <header>
                <div class="proposal-modal-title"><span class="proposal-source">${incomingSourceGlyph(proposal.source)}${actions.escapeHtml(source)}</span><h3>${actions.escapeHtml(proposal.title || 'Proposal')}</h3></div>
                <button type="button" class="proposal-modal-close" aria-label="Close" data-proposal-close>×</button>
            </header>
            <nav class="proposal-tabs" role="tablist">${tabBar}</nav>
            <div class="proposal-modal-body">
                ${pane('details', details, true)}
                ${pane('tasks', tasksPane, false)}
                ${pane('files', proposal.review_key
                    ? PROPOSAL_DROP_ZONE + '<p class="proposal-empty" role="status">Loading attachments…</p>'
                    : proposalFilesHtml(proposal), false)}
                ${pane('source', proposalEvidenceHtml(proposal), false)}
            </div>
            <footer>
                ${proposal.review_key && !context.embeddedProposalReview ? '<p class="proposal-review-copy-status hidden" data-proposal-copy-status role="status"></p><button type="button" class="proposal-btn ghost proposal-copy-review" data-proposal-copy-review>Copy review link</button>' : ''}
                <button type="button" class="proposal-btn ghost" data-proposal-close>Close</button>
                <button type="button" class="proposal-btn primary" data-proposal-thread="${actions.escapeHtml(proposal.id || '')}"${reserved || proposalNeedsProject(proposal) ? ' disabled' : ''}>Create Thread</button>
            </footer>
        </div>`;
        document.body.appendChild(overlay);
        bindProposalFilePreview(overlay);
        bindProposalTaskToggles(overlay, () => reviewed, (updated) => {
            reviewed = { ...reviewed, id: updated.id, review_key: updated.review_key || reviewed.review_key,
                updated_at: updated.updated_at, task_completed_indexes: updated.task_completed_indexes };
            proposalId = updated.id;
            overlay.dataset.proposalId = String(proposalId);
            if (overlay.dataset.reviewKey && updated.review_key) overlay.dataset.reviewKey = updated.review_key;
            renderIncomingWorkspace();
            overlay._returnFocus = [...(el('incoming-proposals-list')?.querySelectorAll('.proposal-card') || [])]
                .find((node) => node.dataset.incomingProposalOpen === String(proposalId)) || overlay._returnFocus;
        });
        overlay.querySelectorAll('[data-proposal-jump-source]').forEach((button) => {
            button.addEventListener('click', () => jumpProposalSource(reviewed));
        });
        const filesPane = overlay.querySelector('[data-proposal-pane="files"]');
        const filesTab = overlay.querySelector('[data-proposal-tab="files"]');
        if (filesPane && !proposal.review_key && !(proposal.attachments || []).some((item) => item.url || item.id || item.download_error || item.error)) {
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
        if (!context.embeddedProposalReview) ensureIncomingRowsLoaded().then(() => {
            if (!overlay.isConnected) return;
            refreshProposalSourcePane(overlay, reviewed);
        });
        const projectSelect = overlay.querySelector('#proposal-project-select');
        const projectStatus = overlay.querySelector('#proposal-project-status');
        const threadButton = overlay.querySelector('[data-proposal-thread]');
        projectSelect.addEventListener('change', async () => {
            const current = reviewed;
            const selectedId = Number(projectSelect.value);
            if (!selectedId) { projectSelect.value = String(current.project_id || ''); return; }
            if (Array.isArray(current.candidate_projects) && !current.candidate_projects.some((item) => Number(item.id) === selectedId)) {
                projectSelect.value = String(current.project_id || '');
                projectStatus.textContent = 'Choose one of this source\'s linked projects.';
                projectStatus.classList.add('error');
                return;
            }
            projectSelect.disabled = true;
            threadButton.disabled = true;
            projectStatus.textContent = 'Saving...';
            projectStatus.classList.remove('error');
            try {
                const result = await actions.api('/workflows/studio/incoming/proposals/project', {
                    method: 'PUT', body: { proposal_id: proposalId, project_id: selectedId,
                        message_ids: current.message_ids || [], expected_updated_at: current.updated_at || '',
                        ...(current.review_key ? { reviewed_proposal: current } : {}) }
                });
                if (!result?.success || !result.proposal) throw new Error(result?.error || 'Could not save the project.');
                const updated = result.proposal;
                reviewed = { ...reviewed, id: updated.id, review_key: updated.review_key || reviewed.review_key,
                    updated_at: updated.updated_at, project_id: updated.project_id,
                    project_name: updated.project_name, needs_project: updated.needs_project,
                    can_push_to_thread: updated.can_push_to_thread };
                state.incomingProposals = state.incomingProposals.map((row) => String(row.id) === String(proposalId) ? result.proposal : row);
                proposalId = result.proposal.id;
                overlay.dataset.proposalId = String(proposalId);
                if (overlay.dataset.reviewKey && result.proposal.review_key) overlay.dataset.reviewKey = result.proposal.review_key;
                threadButton.dataset.proposalThread = String(proposalId);
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
                threadButton.disabled = proposalNeedsProject(reviewed);
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
        overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) dismissProposalDetail(); });
        overlay.querySelectorAll('[data-proposal-close]').forEach((b) => b.addEventListener('click', dismissProposalDetail));
        overlay.querySelector('[data-proposal-copy-review]')?.addEventListener('click', async () => {
            const notice = overlay.querySelector('[data-proposal-copy-status]');
            const show = (message) => {
                notice.textContent = message;
                notice.classList.remove('hidden');
            };
            const current = reviewed;
            if (!current?.review_key) {
                show('The proposal changed. Reopen it to copy its review link.');
                return;
            }
            const url = new URL('/development/incoming/', window.location.origin);
            url.searchParams.set('proposal_key', current.review_key);
            try {
                await navigator.clipboard.writeText(url.href);
                show('Development review link copied.');
            } catch (_) {
                show('Could not copy the review link. Check clipboard permissions and try again.');
            }
        });
        overlay.querySelectorAll('[data-proposal-tab]').forEach((button) => button.addEventListener('click', () => {
            overlay.querySelectorAll('[data-proposal-tab]').forEach((b) => { b.classList.toggle('active', b === button); b.setAttribute('aria-selected', String(b === button)); });
            overlay.querySelectorAll('[data-proposal-pane]').forEach((p) => p.classList.toggle('hidden', p.dataset.proposalPane !== button.dataset.proposalTab));
            if (button.dataset.proposalTab === 'source' && !context.embeddedProposalReview) {
                const current = reviewed;
                ensureIncomingRowsLoaded().then(() => {
                    if (!overlay.isConnected) return;
                    refreshProposalSourcePane(overlay, current);
                });
            }
        }));
        overlay.querySelectorAll('[data-proposal-thread]').forEach((b) => b.addEventListener('click', () => {
            closeProposalDetail();
            pushProposalToThread(proposalId, b, reviewed);
        }));
        document.addEventListener('keydown', onProposalDetailKey);
    }

    async function dismissProposal(proposalId, button, { bulk = false } = {}) {
        if ((!bulk && dismissExpiredBusy) || proposalMutations.has(String(proposalId))) return false;
        const proposal = (state.incomingProposals || []).find((row) => String(row.id) === String(proposalId));
        if (!proposal) return false;
        proposalMutations.add(String(proposalId));
        button.disabled = true;
        const index = (state.incomingProposals || []).indexOf(proposal);
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/dismiss', { method: 'POST', body: { proposal } });
            if (result?.success === false || result?.dismissed === false) throw new Error(result?.error || 'Could not dismiss the proposal.');
            ++incomingLoadSeq;
            state.incomingLoad = null;
            setResyncBusy(false);
            state.incomingProposals = (state.incomingProposals || []).filter((row) => String(row.id) !== String(proposalId));
            renderIncomingWorkspace();
            const key = result?.key || (result?.receipt_keys || [])[0];
            if (key) {
                undoDismissed.set(key, { proposal, index });
                if (!bulk) showUndoSnackbar({ key, proposal, index, upstream: Boolean(result?.upstream_dismissed) });
            }
            if (!el('incoming-dismissed-panel')?.classList.contains('hidden')) loadDismissedList();
            return true;
        } catch (error) {
            if (!bulk) actions.toast(error.message || 'Could not dismiss the proposal.', 'error');
            button.disabled = false;
            return false;
        } finally {
            proposalMutations.delete(String(proposalId));
            if (!bulk) renderIncomingWorkspace();
        }
    }

    async function dismissExpiredProposals() {
        if (dismissExpiredBusy || proposalMutations.size) return;
        const targets = (state.incomingProposals || []).filter(row => !row.merge_source_reserved && proposalIsExpired(row)).map(row => String(row.id));
        if (!targets.length) return;
        dismissExpiredBusy = true;
        ++incomingLoadSeq;
        state.incomingLoad = null;
        setResyncBusy(false);
        renderIncomingWorkspace();
        let dismissed = 0;
        try {
            for (const id of targets) {
                if (await dismissProposal(id, el('incoming-dismiss-expired'), { bulk: true })) dismissed++;
            }
            const failed = targets.length - dismissed;
            actions.toast(`Dismissed ${dismissed} expired proposal${dismissed === 1 ? '' : 's'}.${failed ? ` ${failed} could not be dismissed.` : ''}`, failed ? 'error' : 'success');
        } finally {
            dismissExpiredBusy = false;
            renderIncomingWorkspace();
            if (!el('incoming-dismissed-panel')?.classList.contains('hidden')) loadDismissedList();
        }
    }

    // Undo for dismissals: one snackbar at a time; older dismissals stay reachable
    // through "Recently dismissed". Rows kept here so Undo restores position.
    const UNDO_MS = 10_000;
    const undoDismissed = new Map();
    const locallyRestored = new Set();
    let undoTimer = null;
    let undoCurrent = null;
    function undoSnackbarNode() {
        let node = document.getElementById('incoming-undo-snackbar');
        if (node) return node;
        node = document.createElement('div');
        node.id = 'incoming-undo-snackbar';
        node.className = 'incoming-undo-snackbar hidden';
        node.setAttribute('role', 'status');
        node.setAttribute('aria-live', 'polite');
        node.innerHTML = '<span class="incoming-undo-message" data-incoming-undo-message></span>'
            + '<button type="button" class="proposal-btn primary" data-incoming-undo>Undo</button>'
            + '<button type="button" class="incoming-undo-close" data-incoming-undo-close aria-label="Close">&times;</button>';
        node.querySelector('[data-incoming-undo]').addEventListener('click', () => {
            if (undoCurrent) restoreDismissed(undoCurrent.key);
        });
        node.querySelector('[data-incoming-undo-close]').addEventListener('click', hideUndoSnackbar);
        // Do not yank the snackbar from under keyboard / pointer users.
        node.addEventListener('focusin', () => clearTimeout(undoTimer));
        node.addEventListener('mouseenter', () => clearTimeout(undoTimer));
        node.addEventListener('focusout', armUndoTimer);
        node.addEventListener('mouseleave', armUndoTimer);
        node.addEventListener('keydown', (event) => { if (event.key === 'Escape') hideUndoSnackbar(); });
        document.body.appendChild(node);
        return node;
    }
    function armUndoTimer() {
        clearTimeout(undoTimer);
        undoTimer = setTimeout(hideUndoSnackbar, UNDO_MS);
    }
    function showUndoSnackbar(entry) {
        const node = undoSnackbarNode();
        undoCurrent = entry;
        const title = entry.proposal?.title || 'Proposal';
        node.querySelector('[data-incoming-undo-message]').textContent = `Dismissed “${title}”`;
        node.querySelector('[data-incoming-undo]').disabled = false;
        node.classList.remove('hidden');
        armUndoTimer();
    }
    function hideUndoSnackbar() {
        clearTimeout(undoTimer);
        undoCurrent = null;
        document.getElementById('incoming-undo-snackbar')?.classList.add('hidden');
    }
    async function restoreDismissed(key, button) {
        if (dismissExpiredBusy) return;
        const restoreHadFocus = button === document.activeElement;
        if (button) button.disabled = true;
        const undoButton = document.querySelector('#incoming-undo-snackbar [data-incoming-undo]');
        if (undoCurrent?.key === key && undoButton) undoButton.disabled = true;
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/restore', { method: 'POST', body: { key } });
            locallyRestored.add(key);
            setTimeout(() => locallyRestored.delete(key), 5_000);
            const kept = undoDismissed.get(key);
            undoDismissed.delete(key);
            if (undoCurrent?.key === key) hideUndoSnackbar();
            const restoredProposal = result?.proposal || kept?.proposal;
            if (restoredProposal) {
                const rows = (state.incomingProposals || []).filter((row) => String(row.id) !== String(restoredProposal.id));
                const at = kept?.index >= 0 ? Math.min(kept.index, rows.length) : 0;
                rows.splice(at, 0, restoredProposal);
                state.incomingProposals = rows;
                renderIncomingWorkspace();
            } else {
                // Not in this page's memory: reconcile from the (invalidated) server list.
                state.incomingLoadedAt = 0;
                refreshIncomingData();
            }
            if (result?.pending_retriage) {
                actions.toast('The running server could not return the restored proposal. The restore update needs to be loaded.', 'error');
            } else if (result?.upstream_dismissed) {
                actions.toast('Restored here. It may still be dismissed on Tensology.');
            } else if (result?.restored === false) {
                actions.toast('That proposal was already restored.');
            } else {
                actions.toast('Proposal restored.');
            }
            if (!el('incoming-dismissed-panel')?.classList.contains('hidden')) await loadDismissedList();
            if (restoreHadFocus) el('incoming-dismissed-toggle')?.focus();
        } catch (error) {
            actions.toast(error.message || 'Could not restore the proposal.', 'error');
            if (button) button.disabled = false;
            if (undoButton) undoButton.disabled = false;
        }
    }
    function dismissedRowHtml(row, canDelete = false) {
        const when = row.created_at ? new Date(row.created_at) : null;
        const label = row.title || `${row.source || 'Proposal'} · ${row.conversation_key || row.key}`;
        return `<li class="incoming-dismissed-row">
            <div class="incoming-dismissed-text"${row.summary ? ` title="${actions.escapeHtml(row.summary)}"` : ''}><strong>${actions.escapeHtml(label)}</strong>
            <small>${when && !isNaN(when) ? actions.escapeHtml(when.toLocaleString()) : ''}${row.title_recovered ? '' : ' · Title not stored (dismissed before titles were saved)'}${row.upstream_dismissed ? ' · Tensology' : ''}</small></div>
            <div class="incoming-dismissed-actions"><button type="button" class="proposal-btn ghost" data-incoming-restore="${actions.escapeHtml(row.key)}" aria-label="Restore ${actions.escapeHtml(label)}">Restore</button>
            ${canDelete ? `<button type="button" class="proposal-btn ghost" data-incoming-hide-dismissed="${actions.escapeHtml(row.key)}" aria-label="Delete ${actions.escapeHtml(label)} from history" title="Remove from history and keep this proposal dismissed">Delete</button>` : ''}</div>
        </li>`;
    }
    async function hideDismissed(key, button) {
        const hadFocus = button === document.activeElement;
        button.disabled = true;
        const row = button.closest('.incoming-dismissed-row');
        const next = row?.nextElementSibling?.querySelector('button') || row?.previousElementSibling?.querySelector('button');
        try {
            await actions.api('/workflows/studio/incoming/proposals/dismissed', { method: 'DELETE', body: { key } });
            row?.remove();
            if (hadFocus) (next || el('incoming-dismissed-toggle'))?.focus();
            if (!el('incoming-dismissed-panel')?.querySelector('.incoming-dismissed-row')) {
                await loadDismissedList();
            }
            actions.toast('Removed from history. The proposal remains dismissed.');
        } catch (error) {
            button.disabled = false;
            button.focus();
            actions.toast(error.message || 'Could not remove the entry from history.', 'error');
        }
    }
    async function loadDismissedList() {
        const panel = el('incoming-dismissed-panel');
        if (!panel) return;
        panel.innerHTML = '<p class="incoming-dismissed-status" role="status">Loading…</p>';
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/dismissed?limit=20');
            const rows = result?.dismissed || [];
            panel.innerHTML = rows.length
                ? `<ul class="incoming-dismissed-list" aria-label="Recently dismissed proposals">${rows.map((row) => dismissedRowHtml(row, result.history_delete === true)).join('')}</ul>`
                : '<p class="incoming-dismissed-status" role="status">Nothing dismissed recently.</p>';
            panel.querySelectorAll('[data-incoming-restore]').forEach((b) =>
                b.addEventListener('click', () => restoreDismissed(b.dataset.incomingRestore, b))
            );
            panel.querySelectorAll('[data-incoming-hide-dismissed]').forEach((b) =>
                b.addEventListener('click', () => hideDismissed(b.dataset.incomingHideDismissed, b))
            );
        } catch (error) {
            panel.innerHTML = `<p class="incoming-dismissed-status" role="status">${actions.escapeHtml(error.message || 'Could not load dismissed proposals.')}</p>`;
        }
    }
    function toggleDismissedPanel(force) {
        const panel = el('incoming-dismissed-panel');
        const toggle = el('incoming-dismissed-toggle');
        if (!panel || !toggle) return;
        const open = typeof force === 'boolean' ? force : panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) loadDismissedList();
    }

    async function pushProposalToThread(proposalId, button, reviewed = null) {
        if (button?.disabled || dismissExpiredBusy || proposalMutations.size) return;
        const proposal = reviewed || proposalById(proposalId);
        if (!proposal) return;
        const reviewKey = proposal.review_key || '';
        if (proposalNeedsProject(proposal)) {
            openProposalDetail(proposalId);
            return;
        }
        const original = button.textContent;
        proposalMutations.add(String(proposalId));
        button.disabled = true;
        button.textContent = 'Preparing...';
        const progress = showIncomingProgress('Preparing thread...', 'Preparing source files and transcribing audio or video before the first run. If transcription fails, the thread will still be submitted.');
        let receivedResponse = false;
        try {
            const result = await actions.api('/workflows/studio/incoming/proposals/thread', {
                method: 'POST',
                body: reviewKey ? { proposal, review_key: reviewKey } : { proposal }
            });
            receivedResponse = true;
            if (!result?.chat_id) throw new Error(result?.error || 'The proposal thread could not be created.');
            if (result && result.chat_id) {
                ++incomingLoadSeq;
                state.incomingLoad = null;
                setResyncBusy(false);
                // The proposal is actioned (and cleared on Tensology) — drop it from the list.
                state.incomingProposals = (state.incomingProposals || []).filter(row => String(row.id) !== String(proposalId)
                    || (reviewKey && row.review_key !== reviewKey));
                renderIncomingWorkspace();
                progress.update('Opening thread...', 'Loading Development.');
                await actions.loadChat(Number(result.chat_id));
                actions.toast(proposalThreadToast(result), (result.failed_attachments || []).length ? 'error' : 'success');
                await actions.refreshShell({ preserveConversation: true });
            }
        } catch (error) {
            const message = !receivedResponse && error instanceof TypeError
                ? 'Connection interrupted. Retry to open the proposal thread.'
                : error.data?.error || error.message || 'Could not open the proposal thread.';
            actions.toast(message, 'error');
            if (error.data?.code === 'proposal_review_changed' && !context.embeddedProposalReview) {
                state.incomingLoadedAt = 0;
                void refreshIncomingData();
            }
        } finally {
            proposalMutations.delete(String(proposalId));
            progress.finish();
            button.disabled = false;
            button.textContent = original;
            renderIncomingWorkspace();
        }
    }

    function renderIncomingWorkspace() {
        document.querySelectorAll('[data-incoming-quick-reply]').forEach(rememberQuickReply);
        scheduleProposalExpiry();
        const list = el('incoming-whatsapp-list-content');
        if (!list) return;
        const operationStatus = el('incoming-operation-status');
        if (operationStatus) { operationStatus.textContent = incomingOperation; operationStatus.classList.toggle('hidden', !incomingOperation); }
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
        if (proposalsCount) proposalsCount.textContent = String(state.incomingProposals.length + state.incomingPendingMerges.length);
        const syncStatus = el('incoming-proposal-sync-status');
        if (syncStatus) {
            const sync = state.incomingProposalSync;
            const conflicts = Array.isArray(sync?.conflicts) ? sync.conflicts : [];
            const incomplete = sync?.state === 'partial' || conflicts.length > 0;
            const changes = sync?.state === 'complete' ? [
                sync.uploaded > 0 ? `${sync.uploaded} proposal${sync.uploaded === 1 ? '' : 's'} sent to Tensology.` : '',
                sync.updated > 0 ? `${sync.updated} proposal${sync.updated === 1 ? '' : 's'} updated in Tensology.` : '',
            ].filter(Boolean).join(' ') : '';
            syncStatus.classList.toggle('error', incomplete);
            syncStatus.textContent = !sync?.state ? '' : sync.state === 'pending'
                ? 'Checking proposal synchronization...'
                : incomplete
                  ? ['Synchronization incomplete.', sync.error || '', conflicts.length ? `${conflicts.length} proposal${conflicts.length === 1 ? '' : 's'} could not be synchronized.` : ''].filter(Boolean).join(' ')
                  : changes;
            syncStatus.classList.toggle('hidden', !syncStatus.textContent);
        }
        const dismissExpiredButton = el('incoming-dismiss-expired');
        if (dismissExpiredButton) {
            dismissExpiredButton.disabled = dismissExpiredBusy || proposalMutations.size > 0
                || !(state.incomingProposals || []).some(row => !row.merge_source_reserved && proposalIsExpired(row));
            dismissExpiredButton.textContent = dismissExpiredBusy ? 'Dismissing...' : 'Dismiss expired';
            dismissExpiredButton.setAttribute('aria-busy', String(dismissExpiredBusy));
        }
        const proposalsList = el('incoming-proposals-list');
        if (proposalsList) {
            const merges = state.incomingPendingMerges;
            const hasProposals = state.incomingProposals.length + merges.length > 0;
            const loading = state.incomingProposalsLoading;
            proposalsList.classList.toggle('proposal-cards-status', !hasProposals || !!state.incomingProposalsError);
            proposalsList.setAttribute('aria-busy', loading ? 'true' : 'false');
            proposalsList.innerHTML = loading && !hasProposals
                ? '<div class="proposal-list-status" role="status"><span class="proposal-loading-spinner" aria-hidden="true"></span><span>Loading proposals...</span></div>'
                : state.incomingProposalsError
                  ? `<div class="proposal-list-status" role="status">${actions.escapeHtml(state.incomingProposalsError)}</div>`
                  : hasProposals
                    ? [...(state.incomingProposals || [])].sort(compareIncomingProposalsNewestFirst).map(incomingProposalRowHtml).join('')
                    : '<div class="proposal-list-status" role="status">No proposals waiting for review.</div>';
            proposalsList.insertAdjacentHTML('afterbegin', merges.map(row => `<article class="proposal-card">
                <div class="proposal-head">
                    <p class="proposal-project">${actions.escapeHtml(row.project_name || '')}</p>
                    <h3 class="proposal-title">${actions.escapeHtml(row.title || 'Merged proposal')}</h3>
                </div>
                <p class="proposal-desc">${row.local_recovery ? 'Merge interrupted before the thread was saved.' : 'Waiting for dismissal confirmation.'}</p>
                <div class="proposal-actions">
                    <button type="button" class="proposal-btn primary" data-incoming-resume-merge="${actions.escapeHtml(row.merge_key)}"
                        ${dismissExpiredBusy || incomingOperation || proposalMutations.size ? 'disabled' : ''}>Resume merge</button>
                </div>
            </article>`).join(''));
            proposalsList.querySelectorAll('[data-incoming-resume-merge]').forEach(button =>
                button.addEventListener('click', () => resumeIncomingMerge(button.dataset.incomingResumeMerge)));
            proposalsList.querySelectorAll('[data-incoming-retry-merge]').forEach(button =>
                button.addEventListener('click', () => {
                    const sourceId = button.dataset.incomingRetryMerge;
                    const retry = proposalById(sourceId)?.merge_retry;
                    const partnerId = retry?.original_ids.find(id => `tensology:${id}` !== sourceId);
                    if (partnerId) mergeProposalCards(`tensology:${partnerId}`, sourceId, retry);
                }));
            proposalsList.querySelectorAll('[data-incoming-proposal-thread]').forEach((button) =>
                button.addEventListener('click', () => pushProposalToThread(button.dataset.incomingProposalThread, button))
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-confirm]').forEach((button) =>
                button.addEventListener('click', (event) => {
                    event.stopPropagation();
                    openProposalDetail(button.dataset.incomingProposalConfirm);
                })
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-dismiss]').forEach((button) =>
                button.addEventListener('click', () => dismissProposal(button.dataset.incomingProposalDismiss, button))
            );
            if (dismissExpiredBusy || incomingOperation) proposalsList.querySelectorAll('[data-incoming-proposal-thread],[data-incoming-proposal-dismiss]').forEach(button => { button.disabled = true; });
            proposalsList.querySelectorAll('[data-incoming-proposal-copy]').forEach((button) =>
                button.addEventListener('click', () => copyProposal(button.dataset.incomingProposalCopy))
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-read]').forEach((button) =>
                button.addEventListener('click', () => readProposalAloud(button.dataset.incomingProposalRead, button))
            );
            proposalsList.querySelectorAll('[data-incoming-proposal-open]').forEach((node) =>
                node.addEventListener('click', (event) => {
                    if (event.target.closest('[data-incoming-retry-merge],[data-incoming-proposal-thread],[data-incoming-proposal-confirm],[data-incoming-proposal-dismiss],[data-incoming-proposal-copy],[data-incoming-proposal-read]')) return;
                    openProposalDetail(node.dataset.incomingProposalOpen);
                })
            );
            proposalsList.querySelectorAll('.proposal-card[data-incoming-proposal-open]').forEach((node) =>
                node.addEventListener('keydown', (event) => {
                    if (event.target !== node) return;
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openProposalDetail(node.dataset.incomingProposalOpen);
                    }
                })
            );
            // Drag one proposal onto another on the same project to merge them.
            proposalsList.querySelectorAll('.proposal-card[data-proposal-project]').forEach((node) => {
                node.addEventListener('dragstart', (event) => {
                    if (proposalMutations.size || incomingOperation || proposalNeedsProject(proposalById(node.dataset.incomingProposalOpen))) { event.preventDefault(); return; }
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
        const refreshWarning = state.incomingError
            ? `<div class="incoming-channel-error" role="status"><p>${actions.escapeHtml(state.incomingError)}</p></div>`
            : '';
        list.innerHTML = refreshWarning + (whatsappConversations.length
              ? `${linkedConversations.map(incomingConversationRowHtml).join('')}${otherConversations.length ? `<section class="incoming-channel-section other"><header><h2>Other conversations</h2><small>${otherConversations.length}</small></header><div>${otherConversations.map(incomingConversationRowHtml).join('')}</div></section>` : ''}`
              : refreshWarning ? '' : '<div class="development-empty">No WhatsApp conversations are available.</div>');
        list.querySelectorAll('[data-incoming-conversation]').forEach((button) => {
            button.addEventListener('click', () => {
                selectIncomingConversation(button.dataset.incomingConversation);
            });
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                openIncomingLinkDialog(button.dataset.incomingConversation || '');
            });
        });
        bindIncomingActions(list);
        const selectedWhatsApp = whatsappConversations.find((conversation) => conversation.key === state.selectedIncomingConversationKey) || null;
        paintWhatsAppReader(selectedWhatsApp);

        if (!gmailConversations.some((conversation) => conversation.key === state.selectedGmailThreadKey)) {
            state.selectedGmailThreadKey = gmailConversations[0]?.key || '';
        }
        const gmailList = el('incoming-gmail-list-content');
        const gmailStatus = state.incomingChannels.gmail || {};
        gmailList.innerHTML = refreshWarning + (gmailStatus.error
              ? `<div class="incoming-channel-error"><strong>${gmailStatus.connected ? 'Reconnect Gmail' : 'Connect Gmail'}</strong><p>${actions.escapeHtml(gmailStatus.error)}</p><button type="button" data-google-reconnect>${gmailStatus.connected ? 'Reconnect Google' : 'Connect Google'}</button></div>`
              : gmailConversations.length
                ? gmailConversations
                      .map((conversation) => mailThreadButtonHtml(conversation, state.selectedGmailThreadKey, 'data-incoming-gmail-thread'))
                      .join('')
                : refreshWarning ? '' : '<div class="development-empty">No Gmail inbox threads are available.</div>');
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
        const mailshotList = el('incoming-mailshot-list-content');
        const mailshotSettings = mailshotStatus.error_code !== 'refresh_failed'
            ? '<a href="/settings#advanced">Open Settings</a>' : '';
        mailshotList.innerHTML = refreshWarning + (mailshotStatus.error
            ? `<div class="incoming-channel-error"><strong>Mailshot needs attention</strong><p>${actions.escapeHtml(mailshotStatus.error)}</p>${mailshotSettings}</div>`
            : mailshotConversations.length
              ? mailshotConversations
                    .map((conversation) => mailThreadButtonHtml(conversation, state.selectedMailshotThreadKey, 'data-incoming-mailshot-thread'))
                    .join('')
              : refreshWarning ? '' : '<div class="development-empty">No Tensology Mailshot messages are available.</div>');
        mailshotList.querySelectorAll('[data-incoming-mailshot-thread]').forEach((button) =>
            button.addEventListener('click', () => {
                state.selectedMailshotThreadKey = button.dataset.incomingMailshotThread || '';
                state.incomingMobileDetail = true;
                renderIncomingWorkspace();
            })
        );
        const selectedMailshotThread = mailshotConversations.find((conversation) => conversation.key === state.selectedMailshotThreadKey) || null;
        el('incoming-mailshot-reader').innerHTML = selectedMailshotThread && !mailshotStatus.error
            ? gmailReaderHtml(selectedMailshotThread) : '';
        bindIncomingActions(el('incoming-mailshot-reader'));

    }

    const pendingUnlinks = new Set();

    function updateWhatsAppLink(linkId, replacement) {
        channelLoadSeq++;
        state.incomingLinks = state.incomingLinks.filter(row => row.source !== 'whatsapp' || Number(row.id) !== linkId);
        if (replacement) state.incomingLinks.push(replacement);
        for (const item of state.incoming) {
            if (item.source !== 'whatsapp') continue;
            const matches = replacement && item.source_thread_id === replacement.source_thread_id;
            const previous = item.links ?? (item.link_id ? [{
                id: item.link_id, board_id: item.board_id, board_name: item.board_name, auto_snapshot: item.auto_snapshot,
            }] : []);
            const links = previous.filter(row => Number(row.id) !== linkId);
            if (!matches && links.length === previous.length && Number(item.link_id) !== linkId) continue;
            if (matches) links.unshift(replacement);
            item.links = links;
            item.link_id = links[0]?.id || null;
            item.board_id = links[0]?.board_id || null;
            item.board_name = links[0]?.board_name || '';
            item.auto_snapshot = Boolean(links[0]?.auto_snapshot);
            item.can_snapshot = Boolean(links.length && !item.processed);
        }
    }

    function openIncomingLinkDialog(conversationKey) {
        const conversation = incomingConversations('whatsapp').find((row) => row.key === conversationKey);
        if (!conversation) return;
        state.incomingLinkConversationKey = conversation.key;
        el('incoming-link-error').textContent = '';
        el('incoming-link-error').classList.add('hidden');
        el('incoming-link-title').textContent = conversation.label;
        el('incoming-link-summary').textContent = conversation.links.length
            ? 'Choose another board to move this conversation.'
            : 'Link this person or group when the conversation belongs to active work.';
        el('incoming-current-links').innerHTML = conversation.links.length
            ? conversation.links
                  .map(
                      (link) =>
                          `<div><span>${actions.escapeHtml(link.board_name || 'Board')}</span><label class="incoming-link-auto"><input type="checkbox" data-incoming-auto="${Number(link.id)}" data-board-id="${Number(link.board_id)}"${link.auto_snapshot ? ' checked' : ''}><span>Auto snapshot</span></label><button type="button" data-incoming-unlink="${Number(link.id)}" data-board-id="${Number(link.board_id)}" aria-label="Unlink ${actions.escapeHtml(link.board_name || 'board')}"${pendingUnlinks.has(Number(link.id)) ? ' disabled' : ''}>Remove</button></div>`
                  )
                  .join('')
            : '<small>Not linked to a board.</small>';
        el('incoming-current-links')
            .querySelectorAll('[data-incoming-unlink]')
            .forEach((button) =>
                button.addEventListener('click', () => {
                    unlinkIncomingChannel(Number(button.dataset.boardId), Number(button.dataset.incomingUnlink), button);
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
        const board = context.boards.find(row => row.provider === 'decisions' && Number(row.local_id || row.id) === boardId);
        if (!conversation || !board) {
            actions.toast('Choose a board first.', 'error');
            return;
        }
        const submit = el('incoming-link-form').querySelector('[type="submit"]');
        if (submit.disabled) return;
        submit.disabled = true;
        const autoSnapshot = el('incoming-link-auto').checked;
        try {
            const result = await actions.api(`/tickets/boards/${boardId}/whatsapp-links`, {
                method: 'POST',
                body: {
                    phone_jid: conversation.threadId,
                    contact_name: conversation.label,
                    auto_snapshot: autoSnapshot
                }
            });
            const id = Number(result.id);
            if (!result.success || !id) throw new Error(result.error || 'The board link could not be confirmed.');
            updateWhatsAppLink(id, {
                id, board_id: boardId, board_name: board.name, auto_snapshot: autoSnapshot,
                source: 'whatsapp', source_thread_id: conversation.threadId, label: conversation.label,
            });
            el('incoming-link-dialog').close('saved');
            renderIncomingWorkspace();
            el('incoming-whatsapp-list-content').querySelector(
                `[data-incoming-conversation="${CSS.escape(conversation.key)}"]`
            )?.focus({ preventScroll: true });
        } catch (error) {
            const status = el('incoming-link-error');
            status.textContent = error.message || 'Could not link the WhatsApp conversation.';
            status.classList.remove('hidden');
        } finally {
            submit.disabled = false;
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

    async function unlinkIncomingChannel(boardId, linkId, button) {
        if (pendingUnlinks.has(linkId)) return false;
        const key = state.incomingLinkConversationKey;
        const dialog = el('incoming-link-dialog');
        const isCurrentDialog = () => dialog.open && state.incomingLinkConversationKey === key;
        const errorNode = el('incoming-link-error');
        errorNode.textContent = '';
        errorNode.classList.add('hidden');
        pendingUnlinks.add(linkId);
        if (button) button.disabled = true;
        try {
            const result = await actions.api(`/tickets/boards/${boardId}/whatsapp-links/${linkId}`, {
                method: 'DELETE'
            });
            if (!result.success) throw new Error(result.error || 'The unlink could not be confirmed.');
        } catch (error) {
            if (error.status !== 404 || error.data?.detail !== 'Link not found') {
                const message = error.message || 'Could not unlink the channel.';
                if (isCurrentDialog()) {
                    errorNode.textContent = message;
                    errorNode.classList.remove('hidden');
                    const retry = dialog.querySelector(`[data-incoming-unlink="${linkId}"]`);
                    if (retry) {
                        retry.disabled = false;
                        retry.focus();
                    }
                } else {
                    actions.toast(message, 'error');
                }
                return false;
            }
        } finally {
            pendingUnlinks.delete(linkId);
        }
        updateWhatsAppLink(linkId);
        renderIncomingWorkspace();
        if (isCurrentDialog()) {
            const conversation = incomingConversations('whatsapp').find(row => row.key === key);
            if (conversation?.links.length) {
                openIncomingLinkDialog(key);
                (el('incoming-current-links').querySelector('button:not(:disabled)') || el('incoming-link-board')).focus();
            } else {
                dialog.close('unlinked');
                const row = el('incoming-whatsapp-list-content').querySelector(
                    `[data-incoming-conversation="${CSS.escape(key)}"]`
                );
                (row || el('incoming-folder-whatsapp')).focus({ preventScroll: true });
            }
        }
        return true;
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
                (list.querySelector('[data-incoming-list-content]') || list).appendChild(node);
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
                                selectIncomingConversation(row.dataset.incomingConversation);
                            });
                            row.addEventListener('contextmenu', (event) => {
                                event.preventDefault();
                                openIncomingLinkDialog(row.dataset.incomingConversation || '');
                            });
                            bindIncomingActions(row);
                        });
                        if (conversation.key === state.selectedIncomingConversationKey) {
                            appendMessagesInPlace(el('incoming-whatsapp-reader')?.querySelector('.incoming-whatsapp-messages'), oldestFirst(conversation.messages), (item) => whatsappMessageBubbleHtml(item, conversation));
                            bindWhatsAppVoiceButtons(el('incoming-whatsapp-reader'), conversation);
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
            const channelSeq = channelLoadSeq;
            const data = await actions.api('/workflows/studio/incoming?limit=150');
            if (channelSeq !== channelLoadSeq) return;
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
            if (status === 'restored') {
                // Our own Undo already re-inserted the row; others reconcile softly.
                if (locallyRestored.size) return;
                state.incomingLoadedAt = 0;
                if (context.workspaceMode === 'incoming') refreshIncomingData();
                return;
            }
            if (['dismissed', 'created', 'started', 'sent_to_decisions', 'approved'].includes(status)) {
                removeProposalByRequest(message.request_id);
                if (message.type === 'development_changed') {
                    actions.refreshShell({ preserveConversation: true, background: true });
                }
                return;
            }
            if (['proposal_sync', 'reconciled', 'proposal_updated'].includes(status)) {
                clearTimeout(incomingPushTimer);
                incomingPushTimer = setTimeout(() => {
                    state.incomingLoadedAt = 0;
                    // A reconciliation result is already in the server cache.
                    // Forcing triage here would start another reconciliation.
                    if (context.workspaceMode === 'incoming') refreshIncomingData();
                }, 250);
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
    let channelLoadSeq = 0;
    let channelResyncBusy = false;

    function paintIncomingChannels(data) {
        state.incoming = data.items || [];
        state.incomingLinks = data.links || [];
        state.incomingChannels = data.channels || {};
        state.incomingError = '';
        // 2026-11-11 stale-error: a channel that now loads cleanly drops its earlier Resync error line.
        ['gmail', 'mailshot'].forEach((source) => {
            const line = el(`incoming-${source}-resync-status`);
            if (line?.classList.contains('error') && !state.incomingChannels[source]?.error) {
                line.classList.remove('error');
                line.textContent = '';
            }
        });
        actions.renderSidebar();
        if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
    }

    async function resyncIncomingChannel(button) {
        if (channelResyncBusy) return;
        channelResyncBusy = true;
        channelLoadSeq++;
        const source = button.dataset.incomingChannelResync;
        const whatsapp = source === 'whatsapp';
        const name = whatsapp ? 'WhatsApp' : source === 'mailshot' ? 'Mailshot' : 'Gmail';
        const label = button.querySelector('strong');
        const status = el(`incoming-${source}-resync-status`);
        status.textContent = '';
        status.classList.remove('error');
        const controls = document.querySelectorAll('[data-incoming-channel-resync]');
        controls.forEach(control => { control.disabled = true; });
        button.classList.add('loading');
        button.setAttribute('aria-busy', 'true');
        label.textContent = 'Syncing...';
        try {
            const sync = whatsapp ? await actions.api('/tickets/whatsapp/sync', { method: 'POST' }) : null;
            const channelSeq = channelLoadSeq;
            const data = await actions.api('/workflows/studio/incoming?limit=150', { cache: 'no-store' });
            if (channelSeq !== channelLoadSeq) return;
            // Paint first so the lists reflect this response even when Gmail reports an error.
            paintIncomingChannels(data);
            if (source === 'gmail' && data.channels?.gmail?.error) throw new Error(data.channels.gmail.error);
            state.incomingLoadedAt = Date.now();
            if (source === 'mailshot' && data.channels?.mailshot?.error) return;
            const issue = sync?.warning || sync?.error;
            status.classList.toggle('error', Boolean(issue));
            status.textContent = issue || `${name} conversations refreshed.`;
        } catch (error) {
            status.classList.add('error');
            status.textContent = error.data?.warning || error.message || `Could not resync ${name}. Try again.`;
        } finally {
            channelResyncBusy = false;
            controls.forEach(control => { control.disabled = false; });
            button.classList.remove('loading');
            button.removeAttribute('aria-busy');
            label.textContent = 'Resync';
        }
    }

    function setResyncBusy(busy) {
        state.incomingProposalsLoading = busy;
        el('incoming-proposals-list')?.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (busy && !(state.incomingProposals || []).length && context.workspaceMode === 'incoming') renderIncomingWorkspace();
        const button = el('incoming-resync');
        if (!button) return;
        button.disabled = busy || manualCheckBusy;
        button.classList.toggle('loading', busy);
        button.setAttribute('aria-busy', busy ? 'true' : 'false');
        const label = button.querySelector('strong');
        if (label) label.textContent = busy ? 'Checking...' : 'Check incoming';
    }
    function refreshIncomingData(options) {
        if (dismissExpiredBusy) return Promise.resolve();
        if (state.incomingLoad && !options?.force) return state.incomingLoad;
        if (!options?.force && state.incomingLoadedAt && Date.now() - state.incomingLoadedAt < 15_000) return Promise.resolve();
        connectIncomingPush();
        const seq = ++incomingLoadSeq;
        const force = Boolean(options?.force);
        if (force) state.incomingProposalSync = { state: 'pending' };
        setResyncBusy(true);
        if (force && context.workspaceMode === 'incoming') renderIncomingWorkspace();
        const load = (async () => {
            // Proposals and channels load in parallel; paint proposals as soon as
            // their own response returns. Soft load never sends refresh=1 / cache-bust.
            const proposalsUrl = force
                ? `/workflows/studio/incoming/proposals?limit=150&refresh=1`
                : `/workflows/studio/incoming/proposals?limit=150`;
            const channelsUrl = `/workflows/studio/incoming?limit=150`;
            const proposalsReq = actions.api(proposalsUrl, force ? { cache: 'no-store' } : undefined);
            const channelSeq = channelLoadSeq;
            const channelsReq = channelResyncBusy ? Promise.resolve(null)
                : actions.api(channelsUrl, force ? { cache: 'no-store' } : undefined);

            const paintProposals = proposalsReq.then((result) => {
                if (seq !== incomingLoadSeq) return;
                state.incomingProposals = result.proposals || [];
                state.incomingPendingMerges = result.pending_merges || [];
                state.incomingProposalsError = result.error || '';
                state.incomingProposalSync = result.sync || null;
                const autoAccept = el('incoming-auto-accept');
                if (autoAccept && !autoAccept.disabled) autoAccept.checked = result.auto_accept === true;
                setResyncBusy(false);
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            }, () => {
                if (seq !== incomingLoadSeq) return;
                state.incomingProposalsError = state.incomingProposals.length ? '' : 'Proposals are temporarily unavailable.';
                state.incomingProposalSync = { state: 'partial', error: 'Proposals are temporarily unavailable. Try Check incoming again.' };
                setResyncBusy(false);
                if (context.workspaceMode === 'incoming') renderIncomingWorkspace();
            });

            const paintChannels = channelsReq.then((data) => {
                if (data && seq === incomingLoadSeq && channelSeq === channelLoadSeq) paintIncomingChannels(data);
            }, () => {
                if (seq !== incomingLoadSeq || channelSeq !== channelLoadSeq) return;
                const retained = state.incoming.length ? ' Showing previously loaded conversations.' : '';
                state.incomingError = `Could not refresh conversations.${retained}`;
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
        el('incoming-resync')?.addEventListener('click', checkIncoming);
        document.querySelectorAll('[data-incoming-channel-resync]').forEach((button) =>
            button.addEventListener('click', () => resyncIncomingChannel(button))
        );
        el('incoming-dismissed-toggle')?.addEventListener('click', () => toggleDismissedPanel());
        el('incoming-dismiss-expired')?.addEventListener('click', dismissExpiredProposals);
        el('incoming-dismissed-panel')?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') { toggleDismissedPanel(false); el('incoming-dismissed-toggle')?.focus(); }
        });
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
        leave() {
            proposalReadRequest?.controller.abort();
            clearProposalReview();
        },
        bindEvents,
        incomingConversations,
        openFilePreview,
        clearProposalReview,
        openProposalReview,
        openSubmittedProposalSource,
        closeSubmittedProposalSource,
        mountProposalReview(proposal) {
            state.incomingProposals = [proposal];
            openProposalDetail(proposal.id, { reviewKey: proposal.review_key });
        },
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
