import { isStartupTerminal } from '../terminals/session-policy.js';
import { createReadySeen } from './ready_seen.js';

// Owns sidebar rendering, interactions, and private view state.
// Filled folder = board has threads underneath; outline = none.
export function boardFolderIconHtml(hasThreads) {
    return `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" class="${hasThreads ? 'folder-filled' : 'folder-outline'}"><path d="M1.5 3.5h4l1.5 1.5h7.5v8h-13z" fill="${hasThreads ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
}

export function createSidebar({ context, actions, el }) {
    const state = {
        pinnedBoardKeys: [],
        collapsedBoardKeys: new Set(),
        expandedThreadBoards: new Set(),
        // Folders opened only to show a thread: they go back to collapsed once the user moves on.
        revealedBoardKeys: new Set(),
        // Collapsed folders the user opened by hand; opening a thread from one makes it a reveal.
        peekBoardKeys: new Set(),
        // Thread being revealed while the app is still switching into the thread view.
        revealPending: null,
        boardsCollapsed: false,
        boardsExpanded: false,
        // "No project" is its own folder (threads with no board or project), never a project entry.
        noProjectExpanded: false,
        // Thread the No project folder last auto-opened for. Auto-open happens once per thread, so a
        // user collapse while that thread stays open sticks (also across reloads).
        noProjectRevealedFor: 0,
        // Whole Pinned section collapsed (its header toggle). Pinned folders keep their own collapse.
        pinnedSectionCollapsed: false,
        recentsExpanded: new URLSearchParams(window.location.search).get('recent') === '1',
        showAllRecents: new URLSearchParams(window.location.search).get('recent') === '1',
        contextBoardKey: '',
        contextThreadId: null,
        pendingTerminalBoards: new Set(),
        primedTerminalProjects: new Set()
    };
    // Finished threads this browser has shown; the folder's ready colour ignores them.
    const readySeen = createReadySeen(window.localStorage);
    try {
        const pinned = JSON.parse(window.localStorage.getItem('decisions-development-pinned-boards') || '[]');
        const collapsed = JSON.parse(window.localStorage.getItem('decisions-development-collapsed-boards') || '[]');
        state.pinnedBoardKeys = Array.isArray(pinned) ? pinned : [];
        state.collapsedBoardKeys = new Set(Array.isArray(collapsed) ? collapsed : []);
        state.boardsCollapsed = window.localStorage.getItem('decisions-development-boards-collapsed') === 'true';
        state.noProjectExpanded = window.localStorage.getItem('decisions-development-no-project-expanded') === 'true';
        state.noProjectRevealedFor = Number(window.localStorage.getItem('decisions-development-no-project-revealed') || 0);
        state.pinnedSectionCollapsed = window.localStorage.getItem('decisions-development-pinned-section-collapsed') === 'true';
    } catch (_) {
        /* Keep default preferences if storage is unavailable. */
    }
    function threadRowHtml(chat, active, pinned = false) {
        // Unread (finished, not yet opened) threads get an orange pin in the row's reserved left
        // gutter (.task-row padding-left). It sits inside the row button, so clicking it opens the
        // thread through the same handler, and the re-render after opening drops it.
        const unread = threadNeedsReview(chat);
        const unreadPin = unread ? '<span class="task-row-unread-pin" aria-hidden="true"></span>' : '';
        return `<div class="task-row-wrap${active ? ' active' : ''}${unread ? ' unread' : ''}"><button class="task-row" data-chat-id="${Number(chat.id)}"${active ? ' aria-current="page"' : ''}${unread ? ' aria-description="Unread"' : ''}>${unreadPin}${actions.threadRowContentHtml(chat, pinned)}</button></div>`;
    }
    let pinRevision = 0;
    let pinWritePending = false;

    function storePinnedBoards(keys) {
        state.pinnedBoardKeys = keys;
        try {
            window.localStorage.setItem('decisions-development-pinned-boards', JSON.stringify(state.pinnedBoardKeys));
        } catch (_) { /* The server remains authoritative without browser storage. */ }
    }

    async function savePinnedBoards(method, body) {
        pinWritePending = true;
        pinRevision += 1;
        el('board-context-pin').disabled = true;
        try {
            const payload = await actions.api('/workflows/studio/sidebar/pins', { method, body });
            if (!Array.isArray(payload?.board_keys)) throw new Error('Board pins could not be confirmed.');
            storePinnedBoards(payload.board_keys);
        } finally {
            pinWritePending = false;
            pinRevision += 1;
            el('board-context-pin').disabled = false;
        }
    }

    async function applyPinnedBoards(payload, readRevision) {
        if (pinWritePending || readRevision !== pinRevision) return;
        if (payload?.board_keys === null) {
            // Server already migrated hover-panel project pins; seed only this browser's old local pins.
            const keys = Array.from(new Set(state.pinnedBoardKeys));
            if (keys.length) await savePinnedBoards('PUT', { board_keys: keys, only_if_unset: true });
            else storePinnedBoards([]);
        } else if (Array.isArray(payload?.board_keys)) {
            storePinnedBoards(payload.board_keys);
        }
    }
    function isThreadActive(chat) {
        const currentId = Number(context.currentChat?.id || 0);
        const chatId = Number(chat?.id || 0);
        if (!currentId || !chatId || currentId !== chatId) return false;
        if (context.workspaceMode === 'chat') return true;
        // Keep the thread selected while viewing that project's terminals.
        if (context.workspaceMode === 'terminals') {
            const projectId = Number(context.currentChat?.project_id || 0);
            return Boolean(projectId);
        }
        return false;
    }
    function storeCollapsedBoards() {
        // Revealed folders are stored collapsed, so a reload also comes back to the normal state.
        const keys = new Set([...state.collapsedBoardKeys, ...state.revealedBoardKeys]);
        try {
            window.localStorage.setItem('decisions-development-collapsed-boards', JSON.stringify([...keys]));
        } catch (_) {
            /* local preference only */
        }
    }
    function activeThreadBoardKey() {
        const chat = context.currentChat;
        if (!chat) return '';
        const pending = state.revealPending;
        // openChat reveals before it switches to the chat view; keep the folder until that lands.
        if (pending && pending.mode !== context.workspaceMode) state.revealPending = null;
        const landing = state.revealPending && Number(state.revealPending.chatId) === Number(chat.id);
        return landing || isThreadActive(chat) ? threadBoard(chat)?.key || '' : '';
    }
    function settleRevealedBoards() {
        // 2026-11-12: a folder opened for a thread returns to normal once that thread is no longer open.
        const keep = activeThreadBoardKey();
        state.revealedBoardKeys.forEach((key) => {
            if (key === keep) return;
            state.revealedBoardKeys.delete(key);
            state.collapsedBoardKeys.add(key);
            state.expandedThreadBoards.delete(key);
        });
    }
    function isBoardViewSelected(boardKey) {
        // Highlight a project row only while the board itself is the view (board, plan, or the
        // board's new-thread page). An open thread highlights its own row instead of the folder.
        if (!boardKey || context.selectedBoardKey !== boardKey) return false;
        if (['kanban', 'plan'].includes(context.workspaceMode)) return true;
        return context.workspaceMode === 'chat' && !context.currentChat;
    }
    function threadNeedsReview(chat) {
        return Boolean(actions.threadIsReadyUnaddressed?.(readySeen.withSeen(chat)));
    }

    // A thread open in the chat view is being read: once its replies are on screen it no
    // longer counts toward its folder's ready (orange) state.
    function noteOpenThreadSeen() {
        const chat = context.currentChat;
        if (!chat?.id) return;
        // openChat renders the sidebar just before it switches into the chat view.
        const landing = state.revealPending && Number(state.revealPending.chatId) === Number(chat.id);
        if (context.workspaceMode !== 'chat' && !landing) return;
        // The sidebar row (list payload) is what paints the folder; judge both copies.
        const row = context.chats.find((item) => Number(item.id) === Number(chat.id));
        if (threadNeedsReview(chat) || (row && threadNeedsReview(row))) readySeen.markSeen(chat);
    }

    // 2026-11-13: two sections above Pinned/Boards that are not projects.
    // - Running automations: every automation thread that is processing right now (project-linked
    //   ones also stay in their project folder). Hidden when nothing runs; refreshed by the ~6s shell poll.
    // - No project: threads with no board/project. Its own folder, outside the projects list.
    function ensureExtraSections() {
        const anchor = el('pinned-board-section');
        if (!anchor || document.getElementById('running-automations-section')) return;
        anchor.insertAdjacentHTML('beforebegin',
            '<section class="sidebar-bucket sidebar-running-automations hidden" id="running-automations-section" aria-label="Running automations">'
            + '<div class="sidebar-section-label"><span>Running automations</span><small class="sidebar-section-count" id="running-automations-count"></small></div>'
            + '<div class="task-list" id="running-automations-group" aria-live="polite"></div></section>'
            + '<section class="sidebar-bucket sidebar-no-project hidden" id="no-project-section" aria-label="Threads with no project">'
            + '<div class="sidebar-section-label"><button class="sidebar-section-toggle" type="button" id="no-project-toggle" aria-expanded="false" aria-controls="no-project-content">'
            + '<span class="chevron" aria-hidden="true">▶</span><span>No project</span><small class="sidebar-section-count" id="no-project-count"></small></button></div>'
            + '<div class="task-list hidden" id="no-project-content"></div></section>');
        document.getElementById('no-project-toggle').addEventListener('click', () => {
            state.noProjectExpanded = !state.noProjectExpanded;
            // The open thread's one-time auto-open is used up: the user's choice wins from here.
            const open = context.currentChat?.id ? Number(context.currentChat.id) : 0;
            if (open) state.noProjectRevealedFor = open;
            try {
                window.localStorage.setItem('decisions-development-no-project-expanded', String(state.noProjectExpanded));
                window.localStorage.setItem('decisions-development-no-project-revealed', String(state.noProjectRevealedFor || 0));
            } catch (_) { /* keep the in-memory choice */ }
            renderSidebar();
            document.getElementById('no-project-toggle')?.focus({ preventScroll: true });
        });
    }
    // Pinned gets its own ▶/▼ header toggle (the template ships a plain label). Built once.
    function ensurePinnedToggle() {
        const section = el('pinned-board-section');
        const label = section?.querySelector(':scope > .sidebar-section-label');
        if (!label || document.getElementById('pinned-section-toggle')) return;
        label.innerHTML = '<button class="sidebar-section-toggle" type="button" id="pinned-section-toggle" aria-expanded="true" aria-controls="pinned-board-tree">'
            + '<span class="chevron" aria-hidden="true">▼</span><span>Pinned</span></button>';
        document.getElementById('pinned-section-toggle').addEventListener('click', () => {
            state.pinnedSectionCollapsed = !state.pinnedSectionCollapsed;
            try {
                window.localStorage.setItem('decisions-development-pinned-section-collapsed', String(state.pinnedSectionCollapsed));
            } catch (_) { /* keep the in-memory choice */ }
            renderSidebar();
            document.getElementById('pinned-section-toggle')?.focus({ preventScroll: true });
        });
    }
    function paintPinnedToggle() {
        ensurePinnedToggle();
        const toggle = document.getElementById('pinned-section-toggle');
        if (!toggle) return;
        const collapsed = state.pinnedSectionCollapsed;
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.querySelector('.chevron').textContent = collapsed ? '▶' : '▼';
        el('pinned-board-tree').classList.toggle('hidden', collapsed);
    }
    function renderExtraSections() {
        paintPinnedToggle();
        ensureExtraSections();
        const runningSection = document.getElementById('running-automations-section');
        const noProjectSection = document.getElementById('no-project-section');
        if (!runningSection || !noProjectSection) return;
        const byNewest = (a, b) => String(b.modified_date || '').localeCompare(String(a.modified_date || '')) || Number(b.id) - Number(a.id);
        const running = context.chats
            .filter((chat) => chat.development_source_type === 'automation' && actions.threadIsProcessing?.(chat))
            .sort(byNewest);
        runningSection.classList.toggle('hidden', !running.length);
        document.getElementById('running-automations-count').textContent = running.length ? String(running.length) : '';
        document.getElementById('running-automations-group').innerHTML = running.map((chat) => threadRowHtml(chat, isThreadActive(chat))).join('');
        const loose = context.chats.filter((chat) => !chat.pinned && !threadBoard(chat)).sort(byNewest);
        // Auto-open once when the user lands on one of its threads; never override an explicit collapse.
        const activeLoose = loose.find((chat) => isThreadActive(chat));
        if (activeLoose && Number(activeLoose.id) !== Number(state.noProjectRevealedFor)) {
            state.noProjectRevealedFor = Number(activeLoose.id);
            state.noProjectExpanded = true;
            try {
                window.localStorage.setItem('decisions-development-no-project-expanded', 'true');
                window.localStorage.setItem('decisions-development-no-project-revealed', String(state.noProjectRevealedFor));
            } catch (_) { /* keep the in-memory choice */ }
        }
        const expanded = state.noProjectExpanded;
        noProjectSection.classList.toggle('hidden', !loose.length);
        noProjectSection.classList.toggle('has-running-thread', loose.some((chat) => actions.threadIsProcessing?.(chat)));
        const toggle = document.getElementById('no-project-toggle');
        toggle.setAttribute('aria-expanded', String(expanded));
        toggle.querySelector('.chevron').textContent = expanded ? '▼' : '▶';
        document.getElementById('no-project-count').textContent = loose.length ? String(loose.length) : '';
        const content = document.getElementById('no-project-content');
        content.classList.toggle('hidden', !expanded);
        content.innerHTML = loose.map((chat) => threadRowHtml(chat, isThreadActive(chat))).join('');
    }
    function renderSidebar() {
        settleRevealedBoards();
        readySeen.prune(context.chats);
        noteOpenThreadSeen();
        const pinnedBoards = context.boards.filter((board) => state.pinnedBoardKeys.includes(board.key));
        const pinnedThreads = context.chats.filter((chat) => chat.pinned);
        const unpinned = context.boards.filter((board) => !state.pinnedBoardKeys.includes(board.key));
        const visible = state.boardsExpanded ? unpinned : unpinned.slice(0, 6);
        const pinnedRows = pinnedThreads
            .map((chat) => {
                const active = isThreadActive(chat);
                return threadRowHtml(chat, active, true);
            })
            .join('');
        el('pinned-board-tree').innerHTML = pinnedRows + pinnedBoards.map(boardGroupHtml).join('');
        el('pinned-board-section').classList.toggle('hidden', !pinnedThreads.length && !pinnedBoards.length);
        const boardsCollapsible = pinnedBoards.length > 0;
        const boardsCollapsed = boardsCollapsible && state.boardsCollapsed;
        el('boards-toggle').classList.toggle('hidden', !boardsCollapsible);
        el('boards-label').classList.toggle('hidden', boardsCollapsible);
        el('boards-toggle').setAttribute('aria-expanded', String(!boardsCollapsed));
        el('boards-toggle').querySelector('.chevron').textContent = boardsCollapsed ? '▶' : '▼';
        el('boards-content').classList.toggle('hidden', boardsCollapsed);
        const groupedBoards = ['jira', 'trello', 'decisions']
            .map((provider) => ({
                provider,
                boards: visible.filter((board) => board.provider === provider)
            }))
            .filter((group) => group.boards.length);
        el('board-task-tree').innerHTML =
            groupedBoards
                .map(
                    (group) =>
                        `<div class="board-provider-label">${actions.escapeHtml(actions.providerName(group.provider))}</div>${group.boards.map(boardGroupHtml).join('')}`
                )
                .join('') || '<div class="sidebar-empty">No ticket boards connected.</div>';
        el('boards-show-more').classList.toggle('hidden', unpinned.length <= 6);
        el('boards-show-more').textContent = state.boardsExpanded ? 'Show less' : `Show ${unpinned.length - 6} more`;
        const ruleMode = ['automations', 'automation_rules'].includes(context.workspaceMode);
        const newThreadActive = context.workspaceMode === 'chat' && !context.currentChat && !context.selectedBoardKey;
        el('new-thread-button').classList.toggle('active', newThreadActive);
        el('new-thread-button').setAttribute('aria-current', newThreadActive ? 'page' : 'false');
        [
            ['sidebar-terminals-toggle', ['terminals_home', 'terminals'].includes(context.workspaceMode)],
            ['sidebar-reports-toggle', context.workspaceMode === 'reports']
        ].forEach(([id, active]) => {
            const button = el(id);
            button.classList.toggle('active', active);
            button.setAttribute('aria-current', active ? 'page' : 'false');
        });
        el('sidebar-rules-toggle').classList.toggle('active', ruleMode);
        el('sidebar-rules-toggle').setAttribute('aria-current', ruleMode ? 'page' : 'false');
        el('sidebar-rule-count').textContent = String(context.automations.length);
        const incomingMode = context.workspaceMode === 'incoming';
        el('sidebar-incoming-toggle').classList.toggle('active', incomingMode);
        el('sidebar-incoming-toggle').setAttribute('aria-current', incomingMode ? 'page' : 'false');
        el('sidebar-incoming-count').textContent = String(
            actions
                .incomingConversations(null)
                .filter((conversation) => ['whatsapp', 'gmail', 'mailshot'].includes(conversation.source) && conversation.pendingCount).length
        );
        const workflowMode = context.workspaceMode === 'workflows';
        el('sidebar-workflows-toggle').classList.toggle('active', workflowMode);
        el('sidebar-workflows-toggle').setAttribute('aria-current', workflowMode ? 'page' : 'false');
        el('sidebar-workflow-count').textContent = String(context.workflows.length);
        const archived = context.archivedChats.length
            ? `<details class="archived-threads"><summary>Archived <span>${context.archivedChats.length}</span></summary>${context.archivedChats.map((chat) => `<div class="task-row-wrap"><button class="task-row" data-chat-id="${Number(chat.id)}"><span class="task-title">${actions.escapeHtml(chat.title || 'Archived thread')}</span></button></div>`).join('')}</details>`
            : '';

        const activeStatuses = new Set(['initializing', 'queued', 'running', 'waiting']);
        const recentThreads = context.chats
            .filter((chat) => chat.development_source_type !== 'automation')
            .slice()
            .sort((a, b) => {
                const activeA = Number(activeStatuses.has(String(a.development_execution?.status || '').toLowerCase()));
                const activeB = Number(activeStatuses.has(String(b.development_execution?.status || '').toLowerCase()));
                return activeB - activeA || String(b.modified_date || '').localeCompare(String(a.modified_date || '')) || Number(b.id) - Number(a.id);
            });
        const visibleRecent = state.showAllRecents ? recentThreads : recentThreads.slice(0, 5);
        el('recents-section').classList.toggle('hidden', !recentThreads.length);
        el('recents-content').classList.toggle('hidden', !state.recentsExpanded);
        el('recents-toggle').setAttribute('aria-expanded', String(state.recentsExpanded));
        el('recents-toggle').querySelector('.chevron').textContent = state.recentsExpanded ? '▼' : '▶';
        el('recent-task-group').innerHTML = visibleRecent
            .map((chat) => {
                const active = isThreadActive(chat);
                return threadRowHtml(chat, active);
            })
            .join('');
        el('archived-task-group').innerHTML = archived;
        el('recents-show-more').classList.toggle('hidden', state.showAllRecents || recentThreads.length <= 5);
        renderExtraSections();

        document.querySelectorAll('[data-board-toggle]').forEach(bindProjectToggle);
        document.querySelectorAll('[data-project-threads-more]').forEach(button => {
            button.addEventListener('click', () => {
                const key = button.dataset.projectThreadsMore;
                if (state.expandedThreadBoards.has(key)) state.expandedThreadBoards.delete(key);
                else state.expandedThreadBoards.add(key);
                renderSidebar();
                document.querySelector(`[data-project-threads-more="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
            });
        });
        document.querySelectorAll('[data-board-select]').forEach((button) => {
            button.addEventListener('click', () => actions.openBoardFromSidebar(button.dataset.boardSelect));
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                openBoardContextMenu(event, button.dataset.boardSelect);
            });
        });
        document.querySelectorAll('[data-board-kanban]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                actions.openBoardKanban(button.dataset.boardKanban);
            });
        });
        document.querySelectorAll('[data-board-new-chat]').forEach((button) => {
            button.addEventListener('click', async (event) => {
                event.stopPropagation();
                await actions.waitForRouteReady();
                actions.startBoardDevelopment(button.dataset.boardNewChat);
            });
        });
        document.querySelectorAll('[data-board-terminal]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleBoardTerminal(button);
            });
        });
        document.querySelectorAll('[data-board-edit-commands]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                openBoardLinkDialog(button.dataset.boardEditCommands);
            });
        });
        primeBoardTerminalSessions();
        document.querySelectorAll('[data-chat-id]').forEach((button) => {
            button.addEventListener('click', () => actions.loadChat(Number(button.dataset.chatId)));
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                openThreadContextMenu(event, Number(button.dataset.chatId));
            });
        });
    }

    function openBoardContextMenu(event, boardKey) {
        const menu = el('board-context-menu');
        const pinButton = el('board-context-pin');
        const board = actions.boardByKey(boardKey);
        state.contextBoardKey = boardKey;
        pinButton.querySelector('.context-menu-label').textContent = state.pinnedBoardKeys.includes(boardKey) ? 'Unpin Board' : 'Pin Board';
        pinButton.disabled = pinWritePending;
        el('board-context-plan').disabled = !board?.project_id || !actions.projectById(board.project_id);
        el('board-context-list').disabled =
            context.workspaceMode === 'kanban' && actions.getKanbanViewMode() === 'list' && context.selectedBoardKey === boardKey;
        el('board-context-kanban').disabled =
            context.workspaceMode === 'kanban' && actions.getKanbanViewMode() === 'board' && context.selectedBoardKey === boardKey;
        el('board-context-refresh').querySelector('.context-menu-label').textContent =
            board?.provider === 'decisions' ? 'Refresh Board' : `Re-sync ${actions.providerName(board?.provider)} Board`;
        el('board-context-activate').classList.toggle('hidden', board?.provider !== 'decisions');
        el('board-context-activate').disabled = Boolean(board?.in_use);
        el('board-context-archive').classList.toggle('hidden', board?.provider !== 'decisions');
        el('board-context-delete').classList.toggle('hidden', board?.provider !== 'decisions');
        const project = actions.projectById(board?.project_id);
        const openFolderButton = el('board-context-open-folder');
        openFolderButton.querySelector('.context-menu-label').textContent = `Show in ${actions.systemFileManagerLabel()}`;
        openFolderButton.disabled = !project?.folder_location;
        openFolderButton.title = project?.folder_location || 'No project folder configured';
        menu.classList.remove('hidden');
        const width = menu.offsetWidth;
        const height = menu.offsetHeight;
        menu.style.left = `${Math.min(event.clientX, window.innerWidth - width - 8)}px`;
        menu.style.top = `${Math.min(event.clientY, window.innerHeight - height - 8)}px`;
        menu.focus({ preventScroll: true });
        refreshBoardContextTerminalAction(boardKey);
    }

    function paintTerminalToggle(button, board, sessions) {
        const running = (sessions || []).some(isStartupTerminal);
        button.disabled = !board?.project_id;
        button.dataset.running = String(running);
        button.querySelector('.context-menu-label').textContent = running ? 'Stop Terminals' : 'Start Terminals';
        button.querySelector('svg').innerHTML = running ? '<rect x="5" y="5" width="6" height="6" rx=".5"/>' : '<path d="M5 3.5v9l7-4.5z"/>';
    }

    function paintBoardContextTerminalAction(board, sessions) {
        paintTerminalToggle(el('board-context-terminal-toggle'), board, sessions);
    }

    async function refreshBoardContextTerminalAction(boardKey) {
        const board = actions.boardByKey(boardKey);
        paintBoardContextTerminalAction(board, actions.getTerminalSessions(board?.project_id));
        if (!board?.project_id) return;
        try {
            const sessions = await actions.loadProjectTerminalSessions(board.project_id);
            if (state.contextBoardKey === boardKey) paintBoardContextTerminalAction(board, sessions);
        } catch (_) {
            /* keep the cached action state */
        }
    }

    async function toggleBoardContextTerminals() {
        const board = actions.boardByKey(state.contextBoardKey);
        if (!board?.project_id) return;
        const projectId = Number(board.project_id);
        const running = el('board-context-terminal-toggle').dataset.running === 'true';
        closeBoardContextMenu();
        try {
            const result = await actions.api(`/projects/${projectId}/startup-terminals/${running ? 'stop' : 'start'}`, { method: 'POST', body: {} });
            actions.toast(result.message || (running ? 'Project terminals stopped.' : 'Project terminals started.'));
            await actions.loadProjectTerminalSessions(projectId);
            if (context.workspaceMode === 'terminals_home') actions.paintTerminalProjectGrid();
        } catch (error) {
            actions.toast(error.message || 'Could not update project terminals.', 'error');
        }
    }

    function closeBoardContextMenu() {
        el('board-context-menu').classList.add('hidden');
        state.contextBoardKey = '';
    }

    function openThreadContextMenu(event, chatId) {
        const chat = context.chats.concat(context.archivedChats).find((item) => Number(item.id) === Number(chatId));
        if (!chat) return;
        state.contextThreadId = Number(chatId);
        const menu = el('thread-context-menu');
        el('thread-context-pin').querySelector('.context-menu-label').textContent = chat.pinned ? 'Unpin thread' : 'Pin thread';
        menu.querySelectorAll('[data-timer-menu-item]').forEach((button) => {
            const stateName = chat.development_time_running ? 'running' : 'stopped';
            button.querySelector('.context-menu-label').textContent = button.dataset[`${stateName}Label`];
            button.dataset.timerAction = button.dataset[`${stateName}Action`];
        });
        el('thread-context-archive').querySelector('.context-menu-label').textContent = chat.archived ? 'Restore thread' : 'Archive thread';
        menu.classList.remove('hidden');
        menu.style.left = `${Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8)}px`;
        menu.style.top = `${Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8)}px`;
        menu.focus({ preventScroll: true });
        refreshThreadContextTerminalAction(chat);
    }

    function threadBoard(chat) {
        return actions.boardByKey(chat?.development_board_key) || actions.boardForProject(chat?.project_id) || null;
    }

    async function refreshThreadContextTerminalAction(chat) {
        const board = threadBoard(chat);
        const button = el('thread-context-terminal-toggle');
        paintTerminalToggle(button, board, actions.getTerminalSessions(board?.project_id));
        if (!board?.project_id) return;
        try {
            const sessions = await actions.loadProjectTerminalSessions(board.project_id);
            if (Number(state.contextThreadId) === Number(chat.id)) paintTerminalToggle(button, board, sessions);
        } catch (_) {
            /* keep the cached action state */
        }
    }

    async function toggleThreadContextTerminals() {
        const chat = context.chats.concat(context.archivedChats).find((item) => Number(item.id) === Number(state.contextThreadId));
        const board = threadBoard(chat);
        if (!board?.project_id) return;
        const projectId = Number(board.project_id);
        const running = el('thread-context-terminal-toggle').dataset.running === 'true';
        closeThreadContextMenu();
        try {
            const result = await actions.api(`/projects/${projectId}/startup-terminals/${running ? 'stop' : 'start'}`, { method: 'POST', body: {} });
            actions.toast(result.message || (running ? 'Project terminals stopped.' : 'Project terminals started.'));
            await actions.loadProjectTerminalSessions(projectId);
            if (context.workspaceMode === 'terminals_home') actions.paintTerminalProjectGrid();
        } catch (error) {
            actions.toast(error.message || 'Could not update project terminals.', 'error');
        }
    }

    function closeThreadContextMenu() {
        el('thread-context-menu').classList.add('hidden');
        state.contextThreadId = null;
    }

    async function contextToggleThreadPin() {
        const chat = context.chats.concat(context.archivedChats).find((item) => Number(item.id) === Number(state.contextThreadId));
        if (!chat) return;
        closeThreadContextMenu();
        try {
            await actions.api(`/workflows/studio/tasks/${chat.id}/controls`, {
                method: 'PATCH',
                body: { pinned: !chat.pinned }
            });
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(chat.pinned ? 'Thread unpinned.' : 'Thread pinned.');
        } catch (error) {
            actions.toast(error.message || 'Could not update the thread.', 'error');
        }
    }

    async function contextArchiveThread() {
        const chat = context.chats.concat(context.archivedChats).find((item) => Number(item.id) === Number(state.contextThreadId));
        if (!chat) return;
        closeThreadContextMenu();
        try {
            await actions.api(`/workflows/studio/tasks/${chat.id}/archive`, {
                method: 'POST',
                body: { archived: !chat.archived }
            });
            if (Number(context.currentChat?.id) === Number(chat.id)) actions.showEmptyTask();
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(chat.archived ? 'Thread restored.' : 'Thread archived.');
        } catch (error) {
            actions.toast(error.message || 'Could not update the thread.', 'error');
        }
    }

    async function openBoardLinkDialog(boardKey) {
        const board = boardKey ? actions.boardByKey(boardKey) : null;
        if (boardKey && !board) return;
        state.contextBoardKey = board?.key || '';
        el('board-link-dialog').dataset.boardKey = board?.key || '';
        el('board-edit-id').value = board ? String(board.local_id || board.id || '') : '';
        el('board-link-title').textContent = board ? `Edit ${board.name || 'board'}` : 'Create board';
        el('board-edit-name').value = board?.name || '';
        el('board-edit-folder').value = board?.folder_location || board?.default_project_folder || '';
        el('board-edit-terminals').value = board?.startup_instructions || '';
        el('delete-board-button').classList.toggle('hidden', !board || board.provider !== 'decisions');
        closeBoardContextMenu();
        state.contextBoardKey = board?.key || '';
        el('board-link-dialog').showModal();
        window.setTimeout(() => el('board-edit-name').focus(), 0);
    }

    async function saveBoardLink(event) {
        event.preventDefault();
        const board = actions.boardByKey(el('board-link-dialog').dataset.boardKey);
        const name = el('board-edit-name').value.trim();
        if (!name) return;
        const body = {
            name,
            folder_location: el('board-edit-folder').value.trim(),
            startup_instructions: el('board-edit-terminals').value
        };
        try {
            if (!board) {
                const created = await actions.api('/tickets/boards', {
                    method: 'POST',
                    body
                });
                context.selectedBoardKey = `decisions:${Number(created.id)}`;
            } else if (board.provider === 'decisions') {
                await actions.api(`/tickets/boards/${Number(board.local_id || board.id)}`, { method: 'PUT', body });
            } else {
                await actions.api(
                    `/tickets/external-boards/${encodeURIComponent(board.provider)}/${encodeURIComponent(board.external_id || board.id)}/register`,
                    {
                        method: 'POST',
                        body: {
                            name,
                            default_project_id: board.project_id || 0
                        }
                    }
                );
            }
            el('board-link-dialog').close();
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(board ? 'Board updated.' : 'Board created.');
        } catch (error) {
            actions.toast(error.message || 'Could not save the board.', 'error');
        }
    }

    async function browseBoardFolder() {
        const input = el('board-edit-folder');
        const button = el('board-folder-browse');
        button.disabled = true;
        try {
            const query = input.value.trim() ? `?initial_dir=${encodeURIComponent(input.value.trim())}` : '';
            const result = await actions.api(`/browse-folder${query}`);
            if (result.path) {
                input.value = result.path;
                if (!el('board-edit-name').value.trim())
                    el('board-edit-name').value =
                        result.path
                            .replace(/[\\/]+$/, '')
                            .split(/[\\/]/)
                            .pop() || '';
            } else if (result.error && result.error !== 'No folder selected') actions.toast(result.error, 'error');
        } catch (error) {
            actions.toast(error.message || 'Could not open the repository folder picker.', 'error');
        } finally {
            button.disabled = false;
        }
    }

    async function deleteBoard(board) {
        if (!board || board.provider !== 'decisions') return;
        const path = board.folder_location || board.default_project_folder || '';
        const decision = await actions.confirmAction({
            title: 'Delete project?',
            message: 'This removes the project, board, tickets, Development threads, time relationships, and stored snapshot files.',
            confirmLabel: 'Delete',
            danger: true,
            checkbox: path ? { label: `Also delete repository folder (${path})`, checked: false } : null
        });
        const confirmed = decision === true || Boolean(decision?.confirmed);
        if (!confirmed) return;
        try {
            const removeRepository = Boolean(decision?.checked);
            await actions.api(`/tickets/boards/${Number(board.local_id || board.id)}?delete_repository=${removeRepository}`, { method: 'DELETE' });
            if (context.selectedBoardKey === board.key) actions.showEmptyTask();
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(removeRepository ? 'Project, linked data, and repository deleted.' : 'Project and linked data deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the project.', 'error');
        }
    }

    // Same rule as the thread header: a project with startup terminal commands
    // (hasStartupTerminalCommands, or the board's own copy of the project's
    // startup_instructions) or live startup sessions has a terminal.
    function hasCommandLines(text) {
        return String(text || '').split('\n').some((line) => line.trim() && !line.trim().startsWith('#'));
    }
    function boardTerminalState(board) {
        const projectId = Number(board?.project_id || 0);
        if (!projectId) return { linked: false, running: false };
        const sessions = actions.getTerminalSessions?.(projectId) || [];
        const running = sessions.some(isStartupTerminal);
        const linked = running || Boolean(actions.hasStartupTerminalCommands?.(projectId)) || hasCommandLines(board.startup_instructions);
        return { linked, running };
    }
    // Board edit dialog (openBoardLinkDialog) saves startup_instructions only for
    // Decisions boards; it creates and links a project when the board has none.
    function boardCanEditTerminalCommands(board) {
        return board?.provider === 'decisions';
    }
    function boardTerminalActionHtml(board) {
        const { linked, running } = boardTerminalState(board);
        if (!linked) {
            if (!boardCanEditTerminalCommands(board)) return '';
            const name = actions.escapeHtml(board.name || 'board');
            return `<button class="board-row-action board-edit-commands" type="button" data-board-edit-commands="${actions.escapeHtml(board.key)}" aria-label="Add terminal commands for ${name}" title="Add terminal commands"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M9.5 4.2 11.8 6.5 6 12.3H3.7V10z"/><path d="M8.4 5.3l2.3 2.3"/></svg></button>`;
        }
        const label = running ? 'Stop terminal' : 'Start terminal';
        const name = actions.escapeHtml(board.name || 'board');
        return `<button class="board-row-action board-terminal terminal-project-action ${running ? 'stop' : 'start'}" type="button" data-board-terminal="${actions.escapeHtml(board.key)}" data-running="${String(running)}" aria-label="${label} for ${name}" title="${label}">${actions.terminalProjectActionIcon(running ? 'stop' : 'start', state.pendingTerminalBoards.has(board.key))}</button>`;
    }
    async function toggleBoardTerminal(button) {
        const boardKey = String(button.dataset.boardTerminal || '');
        const board = actions.boardByKey(boardKey);
        const projectId = Number(board?.project_id || 0);
        if (!projectId || state.pendingTerminalBoards.has(boardKey)) return;
        const running = button.dataset.running === 'true';
        state.pendingTerminalBoards.add(boardKey);
        try {
            const changed = await actions.setTerminalProjectRunning(projectId, !running, button);
            await actions.loadProjectTerminalSessions(projectId);
            // Global "On startup, go to terminals." (default on): starting a terminal takes
            // you straight into that project's terminals; off = start in place.
            if (!running && changed && actions.playOpensTerminals?.() !== false) await actions.openBoardTerminals?.(boardKey);
        } catch (error) {
            console.warn(`Board terminal ${running ? 'stop' : 'start'} failed:`, error?.message || error);
        } finally {
            state.pendingTerminalBoards.delete(boardKey);
            renderSidebar();
        }
    }
    async function primeBoardTerminalSessions() {
        // Load each linked project's sessions once so the icon reflects the real state.
        const ids = new Set(context.boards.map(board => Number(board.project_id || 0))
            .filter(id => id && !state.primedTerminalProjects.has(id) && actions.hasStartupTerminalCommands?.(id)));
        if (!ids.size || !actions.loadProjectTerminalSessions) return;
        ids.forEach(id => state.primedTerminalProjects.add(id));
        await Promise.all([...ids].map(id => actions.loadProjectTerminalSessions(id).catch(() => [])));
        renderSidebar();
    }
    function boardGroupHtml(board) {
        const chats = context.chats.filter((chat) => {
            if (chat.pinned) return false;
            return threadBoard(chat)?.key === board.key;
        });
        const hasTasks = chats.length > 0;
        const collapsed = hasTasks && state.collapsedBoardKeys.has(board.key);
        const orderedChats = chats.slice().sort((a, b) => {
            const first = Date.parse(a.created_date), second = Date.parse(b.created_date);
            return (Number.isFinite(first) && Number.isFinite(second) ? second - first : 0) || Number(b.id) - Number(a.id);
        });
        const expanded = state.expandedThreadBoards.has(board.key);
        const visibleChats = expanded ? orderedChats : orderedChats.slice(0, 4);
        const rows = visibleChats.map(chat => threadRowHtml(chat, isThreadActive(chat), Boolean(chat.pinned))).join('');
        const showMore = orderedChats.length > 4
            ? `<button type="button" class="project-threads-more" data-project-threads-more="${actions.escapeHtml(board.key)}" aria-expanded="${expanded}">${expanded ? 'Show less' : 'Show more'}</button>` : '';
        const running = context.chats.some(chat => threadBoard(chat)?.key === board.key && actions.threadIsProcessing?.(chat));
        const ready = !running && context.chats.some(chat => threadBoard(chat)?.key === board.key && threadNeedsReview(chat));
        const busyIcon = running
            ? `<span class="folder-running-glow" aria-hidden="true">${boardFolderIconHtml(true)}</span>`
            : ready
                ? `<span class="folder-ready-alert" aria-hidden="true">${boardFolderIconHtml(true)}</span>`
                : '';
        const selected = isBoardViewSelected(board.key);
        const kanbanButton = actions.boardSupportsKanban?.(board)
            ? `<button class="board-row-action board-kanban" type="button" data-board-kanban="${actions.escapeHtml(board.key)}" aria-label="Open board for ${actions.escapeHtml(board.name || 'board')}" title="Open board"><svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="3" height="10" rx=".8"/><rect x="6.5" y="3" width="3" height="7" rx=".8"/><rect x="10.5" y="3" width="3" height="5" rx=".8"/></svg></button>`
            : '';
        const projectRow = `<div class="project-row${selected ? ' active' : ''}">
            ${hasTasks ? `<button class="project-toggle project-folder${collapsed ? '' : ' expanded'}" type="button" data-board-toggle="${actions.escapeHtml(board.key)}" aria-expanded="${String(!collapsed)}" aria-label="${collapsed ? 'Expand' : 'Collapse'} ${actions.escapeHtml(board.name || 'board')} threads"><span class="folder-icon">${boardFolderIconHtml(true)}${busyIcon}</span></button>` : `<span class="project-toggle project-folder empty" aria-hidden="true"><span class="folder-icon">${boardFolderIconHtml(false)}</span></span>`}
            <button class="project-select" type="button" data-board-select="${actions.escapeHtml(board.key)}"${selected ? ' aria-current="true"' : ''}><span>${actions.escapeHtml(board.name || 'Ticket board')}</span></button>
            <div class="board-row-tail${hasTasks ? ' has-threads' : ''}">${kanbanButton}${boardTerminalActionHtml(board)}<button class="board-row-action board-new-chat" type="button" data-board-new-chat="${actions.escapeHtml(board.key)}" aria-label="New thread in ${actions.escapeHtml(board.name || 'board')}" title="New thread in board"><span aria-hidden="true">+</span></button></div>
        </div>`;
        return `<section class="project-group${running ? ' has-running-thread' : ''}${ready ? ' has-ready-thread' : ''}" data-board-group="${actions.escapeHtml(board.key)}">
            ${projectRow}
            <div class="task-list${hasTasks && !collapsed ? '' : ' hidden'}">${rows}${showMore}</div>
        </section>`;
    }

    function bindProjectToggle(button) {
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            const group = button.closest('.project-group');
            const list = group?.querySelector('.task-list');
            if (!list) return;
            const closing = !list.classList.contains('hidden');
            const boardKey = String(button.dataset.boardToggle || '');
            const boardName = group.querySelector('[data-board-select] span')?.textContent?.trim() || 'board';
            state.revealedBoardKeys.delete(boardKey);
            if (closing) {
                state.collapsedBoardKeys.add(boardKey);
                state.expandedThreadBoards.delete(boardKey);
                state.peekBoardKeys.delete(boardKey);
            } else {
                if (state.collapsedBoardKeys.has(boardKey)) state.peekBoardKeys.add(boardKey);
                state.collapsedBoardKeys.delete(boardKey);
            }
            storeCollapsedBoards();
            renderSidebar();
            document.querySelector(`[data-board-toggle="${CSS.escape(boardKey)}"]`)?.focus({ preventScroll: true });
        });
    }

    async function toggleBoardPin(boardKey) {
        if (pinWritePending) return;
        const pinning = !state.pinnedBoardKeys.includes(boardKey);
        try {
            await savePinnedBoards('PATCH', { board_key: boardKey, pinned: pinning });
        } catch (error) {
            actions.toast(error.message || 'Board pins could not be saved.', 'error');
            return;
        }
        renderSidebar();
        actions.toast(pinning ? 'Board pinned.' : 'Board unpinned.');
        if (state.contextBoardKey === boardKey) closeBoardContextMenu();
    }

    // Desktop sidebar collapse: one toggle in the New Thread row; when collapsed only that
    // toggle remains (thin rail). Persisted globally in localStorage; default expanded.
    const SIDEBAR_COLLAPSED_KEY = 'decisions.development.sidebarCollapsed';
    const SIDEBAR_ICON_COLLAPSE = '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M6 3v10"/><path d="M10.5 6 8.5 8l2 2"/></svg>';
    const SIDEBAR_ICON_EXPAND = '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M6 3v10"/><path d="M8.5 6l2 2-2 2"/></svg>';

    function readSidebarCollapsed() {
        try {
            return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
        } catch (_) {
            return false;
        }
    }

    function applySidebarCollapsed(collapsed) {
        const on = Boolean(collapsed);
        el('studio-shell')?.classList.toggle('sidebar-collapsed', on);
        el('studio-sidebar')?.classList.toggle('collapsed', on);
        const button = el('sidebar-collapse-toggle');
        if (!button) return;
        const label = on ? 'Expand sidebar' : 'Collapse sidebar';
        button.innerHTML = on ? SIDEBAR_ICON_EXPAND : SIDEBAR_ICON_COLLAPSE;
        button.setAttribute('aria-label', label);
        button.setAttribute('aria-expanded', on ? 'false' : 'true');
        button.title = label;
    }

    function setSidebarCollapsed(collapsed) {
        try {
            window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
        } catch (_) {
            /* ignore */
        }
        applySidebarCollapsed(collapsed);
        // Let layout-sensitive panes (xterm) refit to the new width.
        window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }

    applySidebarCollapsed(readSidebarCollapsed());

    function bindEvents() {
        el('sidebar-collapse-toggle')?.addEventListener('click', (event) => {
            event.stopPropagation();
            setSidebarCollapsed(!el('studio-shell')?.classList.contains('sidebar-collapsed'));
        });
        el('new-thread-button').addEventListener('click', actions.startNewDevelopmentFromSidebar);
        el('sidebar-incoming-toggle').addEventListener('click', () => actions.setWorkspaceMode('incoming'));
        el('sidebar-rules-toggle').addEventListener('click', () => actions.setWorkspaceMode('automations'));
        el('sidebar-rule-add').addEventListener('click', () => {
            actions.setWorkspaceMode('automations');
            window.setTimeout(() => el('scheduled-prompt-input')?.focus(), 0);
        });
        el('sidebar-workflows-toggle').addEventListener('click', () => actions.setWorkspaceMode('workflows'));
        el('sidebar-workflow-add').addEventListener('click', () => {
            actions.setWorkspaceMode('workflows');
            if (typeof actions.openWorkflowComposer === 'function') actions.openWorkflowComposer();
            else el('workflow-add-toggle')?.click();
        });
        el('sidebar-terminals-toggle').addEventListener('click', () => actions.setWorkspaceMode('terminals_home'));
        el('sidebar-reports-toggle').addEventListener('click', () => actions.setWorkspaceMode('reports'));
        el('board-context-pin').addEventListener('click', () => {
            if (state.contextBoardKey) toggleBoardPin(state.contextBoardKey);
        });
        el('board-context-new-chat').addEventListener('click', () => {
            if (state.contextBoardKey) actions.startBoardDevelopment(state.contextBoardKey);
            closeBoardContextMenu();
        });
        el('board-context-new-ticket').addEventListener('click', async () => {
            const boardKey = state.contextBoardKey;
            if (!boardKey) return;
            await actions.openBoardKanban(boardKey);
            actions.openKanbanTicketDialog('');
        });
        el('board-context-refresh').addEventListener('click', async () => {
            const boardKey = state.contextBoardKey;
            if (!boardKey) return;
            if (context.selectedBoardKey !== boardKey || context.workspaceMode !== 'kanban') await actions.openBoardKanban(boardKey);
            closeBoardContextMenu();
            await actions.forceRefreshKanbanBoard();
        });
        el('board-context-list').addEventListener('click', async () => {
            const boardKey = state.contextBoardKey;
            if (!boardKey) return;
            if (context.selectedBoardKey !== boardKey || context.workspaceMode !== 'kanban') await actions.openBoardKanban(boardKey);
            actions.setKanbanViewMode('list');
        });
        el('board-context-edit').addEventListener('click', () => {
            if (state.contextBoardKey) openBoardLinkDialog(state.contextBoardKey);
        });
        el('board-context-open-folder').addEventListener('click', async () => {
            const board = actions.boardByKey(state.contextBoardKey);
            const project = actions.projectById(board?.project_id);
            closeBoardContextMenu();
            if (!project?.id || !project.folder_location) {
                actions.toast('Add a project folder to this board first.', 'error');
                return;
            }
            try {
                const result = await actions.api(`/projects/${Number(project.id)}/open-folder`, { method: 'POST', body: {} });
                actions.toast(`Opened in ${result.file_manager || actions.systemFileManagerLabel()}.`);
            } catch (error) {
                actions.toast(error.message || `Could not open the project folder in ${actions.systemFileManagerLabel()}.`, 'error');
            }
        });
        el('board-context-kanban').addEventListener('click', () => {
            const boardKey = state.contextBoardKey;
            if (!boardKey) return;
            actions.openBoardKanban(boardKey).then(() => actions.setKanbanViewMode('board'));
        });
        el('board-context-plan').addEventListener('click', () => {
            if (state.contextBoardKey) actions.openBoardPlan(state.contextBoardKey);
        });
        el('board-context-activate').addEventListener('click', async () => {
            const board = actions.boardByKey(state.contextBoardKey);
            closeBoardContextMenu();
            if (!board?.local_id) return;
            try {
                await actions.api(`/tickets/boards/${Number(board.local_id)}/use`, {
                    method: 'POST',
                    body: {}
                });
                await actions.refreshShell({ preserveConversation: true });
                actions.toast('Board set as active.');
            } catch (error) {
                actions.toast(error.message || 'Could not activate the board.', 'error');
            }
        });
        el('board-context-archive').addEventListener('click', async () => {
            const board = actions.boardByKey(state.contextBoardKey);
            closeBoardContextMenu();
            if (
                !board?.local_id ||
                !(await actions.confirmAction({
                    title: 'Archive board',
                    message: `Archive “${board.name || 'this board'}”? Its tickets remain stored.`,
                    confirmLabel: 'Archive'
                }))
            )
                return;
            try {
                await actions.api(`/tickets/boards/${Number(board.local_id)}/archive`, {
                    method: 'POST',
                    body: {}
                });
                context.selectedBoardKey = '';
                actions.setWorkspaceMode('chat', { focus: false });
                await actions.refreshShell({ preserveConversation: true });
                actions.toast('Board archived.');
            } catch (error) {
                actions.toast(error.message || 'Could not archive the board.', 'error');
            }
        });
        el('board-context-delete').addEventListener('click', async () => {
            const board = actions.boardByKey(state.contextBoardKey);
            closeBoardContextMenu();
            await deleteBoard(board);
        });
        el('board-context-terminals').addEventListener('click', () => {
            if (state.contextBoardKey) actions.openBoardTerminals(state.contextBoardKey);
        });
        el('board-context-terminal-toggle').addEventListener('click', toggleBoardContextTerminals);
        el('new-board-button').addEventListener('click', () => openBoardLinkDialog(''));
        el('thread-context-pin').addEventListener('click', contextToggleThreadPin);
        el('thread-context-rename').addEventListener('click', () => {
            const chatId = state.contextThreadId;
            closeThreadContextMenu();
            if (chatId) actions.openRenameThreadDialog(chatId);
        });
        el('thread-context-edit').addEventListener('click', () => {
            const chatId = state.contextThreadId;
            closeThreadContextMenu();
            if (chatId) actions.openThreadDialog(chatId);
        });
        el('thread-context-menu').querySelectorAll('[data-timer-menu-item]').forEach((button) => {
            button.addEventListener('click', () => {
                const chatId = state.contextThreadId;
                const timerAction = button.dataset.timerAction;
                closeThreadContextMenu();
                if (chatId && timerAction) actions.updateThreadTimer(chatId, timerAction);
            });
        });
        el('thread-context-terminal-toggle').addEventListener('click', toggleThreadContextTerminals);
        el('thread-context-archive').addEventListener('click', contextArchiveThread);
        el('thread-context-delete').addEventListener('click', () => {
            const chatId = state.contextThreadId;
            closeThreadContextMenu();
            if (chatId) actions.deleteThread(chatId);
        });
        el('board-link-form').addEventListener('submit', saveBoardLink);
        el('board-folder-browse').addEventListener('click', browseBoardFolder);
        el('delete-board-button').addEventListener('click', () => {
            const board = actions.boardByKey(el('board-link-dialog').dataset.boardKey);
            el('board-link-dialog').close();
            deleteBoard(board);
        });
        el('boards-show-more').addEventListener('click', () => {
            state.boardsExpanded = !state.boardsExpanded;
            renderSidebar();
        });
        el('boards-toggle').addEventListener('click', () => {
            state.boardsCollapsed = !state.boardsCollapsed;
            window.localStorage.setItem('decisions-development-boards-collapsed', String(state.boardsCollapsed));
            renderSidebar();
        });
        el('recents-toggle').addEventListener('click', () => {
            state.recentsExpanded = !state.recentsExpanded;
            renderSidebar();
        });
        el('recents-show-more').addEventListener('click', actions.openRecentThreads);
        el('sidebar-open').addEventListener('click', actions.openSidebar);
        el('sidebar-close').addEventListener('click', actions.closeDrawers);
    }
    return {
        async runDesktopBoardAction(payload) {
            const allowed = ['new-ticket', 'new-chat', 'plan', 'kanban', 'list', 'refresh', 'edit',
                'activate', 'open-folder', 'terminals', 'terminal-toggle', 'archive', 'delete'];
            if (!allowed.includes(payload?.action) || !actions.boardByKey(payload.board_key)) return;
            openBoardContextMenu({ clientX: 8, clientY: 8 }, payload.board_key);
            if (payload.action === 'terminal-toggle') await refreshBoardContextTerminalAction(payload.board_key);
            const button = el(`board-context-${payload.action}`);
            el('board-context-menu').classList.add('hidden');
            if (button && !button.disabled && !button.classList.contains('hidden')) button.click();
            else actions.toast('This action is unavailable for this project.', 'error');
        },
        applyPinnedBoards,
        boardPinRevision: () => pinRevision,
        pinnedProjectIds() {
            return [...new Set(context.boards
                .filter((board) => [board.key, ...(board.aliases || [])].some((key) => state.pinnedBoardKeys.includes(key)))
                .map((board) => Number(board.project_id))
                .filter((id) => id > 0))];
        },
        bindEvents,
        boardTerminalActionHtml,
        closeBoardContextMenu,
        closeThreadContextMenu,
        openBoardContextMenu,
        openBoardLinkDialog,
        openRecents() {
            state.recentsExpanded = true;
            state.showAllRecents = true;
            renderSidebar();
        },
        revealThread(chat) {
            const board = threadBoard(chat);
            if (!board) return;
            // Open a collapsed folder only while its thread is being viewed (see settleRevealedBoards).
            if (state.collapsedBoardKeys.has(board.key) || state.peekBoardKeys.has(board.key) || state.revealedBoardKeys.has(board.key)) {
                state.revealedBoardKeys.add(board.key);
            }
            state.revealPending = { chatId: Number(chat.id), mode: context.workspaceMode };
            state.peekBoardKeys.delete(board.key);
            state.collapsedBoardKeys.delete(board.key);
            state.expandedThreadBoards.delete(board.key);
            state.boardsCollapsed = false;
            const unpinned = context.boards.filter(row => !state.pinnedBoardKeys.includes(row.key));
            if (unpinned.findIndex(row => row.key === board.key) >= 6) state.boardsExpanded = true;
            storeCollapsedBoards();
            try {
                window.localStorage.setItem('decisions-development-boards-collapsed', 'false');
            } catch (_) {}
        },
        renderSidebar,
        threadBoard
    };
}
