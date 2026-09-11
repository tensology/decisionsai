// Owns sidebar rendering, interactions, and private view state.
export function createSidebar({ context, actions, el }) {
    const state = {
        pinnedBoardKeys: [],
        collapsedBoardKeys: new Set(),
        boardsExpanded: false,
        contextBoardKey: '',
        contextThreadId: null
    };
    try {
        const pinned = JSON.parse(window.localStorage.getItem('decisions-development-pinned-boards') || '[]');
        const collapsed = JSON.parse(window.localStorage.getItem('decisions-development-collapsed-boards') || '[]');
        state.pinnedBoardKeys = Array.isArray(pinned) ? pinned : [];
        state.collapsedBoardKeys = new Set(Array.isArray(collapsed) ? collapsed : []);
    } catch (_) {
        /* Keep default preferences if storage is unavailable. */
    }
    function renderSidebar() {
        const pinnedBoards = context.boards.filter((board) => state.pinnedBoardKeys.includes(board.key));
        const pinnedThreads = context.chats.filter((chat) => chat.pinned);
        const unpinned = context.boards.filter((board) => !state.pinnedBoardKeys.includes(board.key));
        const visible = state.boardsExpanded ? unpinned : unpinned.slice(0, 6);
        const pinnedRows = pinnedThreads
            .map((chat) => {
                const active = context.workspaceMode === 'chat' && Number(context.currentChat?.id) === Number(chat.id);
                return `<div class="task-row-wrap${active ? ' active' : ''}"><button class="task-row" data-chat-id="${Number(chat.id)}"${active ? ' aria-current="page"' : ''}>${actions.threadRowContentHtml(chat, true)}</button></div>`;
            })
            .join('');
        el('pinned-board-tree').innerHTML = pinnedRows + pinnedBoards.map(boardGroupHtml).join('');
        el('pinned-board-section').classList.toggle('hidden', !pinnedThreads.length && !pinnedBoards.length);
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
        ['plan', 'terminals_home', 'reports'].forEach((mode) => {
            const button = el(`sidebar-${mode === 'terminals_home' ? 'terminals' : mode}-toggle`);
            const active = mode === 'terminals_home' ? ['terminals_home', 'terminals'].includes(context.workspaceMode) : context.workspaceMode === mode;
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

        const unassigned = context.chats.filter(
            (chat) => !chat.project_id && !chat.development_board_key && !chat.pinned && chat.development_source_type !== 'automation'
        );
        el('recents-section').classList.toggle('hidden', !unassigned.length && !context.archivedChats.length);
        el('recents-label').classList.toggle('hidden', !unassigned.length);
        el('unassigned-task-group').innerHTML = unassigned.length
            ? unassigned
                  .slice(0, 8)
                  .map((chat) => {
                      const active = context.workspaceMode === 'chat' && Number(context.currentChat?.id) === Number(chat.id);
                      return `<div class="task-row-wrap${active ? ' active' : ''}"><button class="task-row" data-chat-id="${Number(chat.id)}"${active ? ' aria-current="page"' : ''}>${actions.threadRowContentHtml(chat)}</button></div>`;
                  })
                  .join('') + archived
            : archived;

        document.querySelectorAll('[data-board-toggle]').forEach(bindProjectToggle);
        document.querySelectorAll('[data-board-select]').forEach((button) => {
            button.addEventListener('click', () => actions.openBoardFromSidebar(button.dataset.boardSelect));
            button.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                openBoardContextMenu(event, button.dataset.boardSelect);
            });
        });
        document.querySelectorAll('[data-board-pin]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleBoardPin(button.dataset.boardPin);
            });
        });
        document.querySelectorAll('[data-board-new-chat]').forEach((button) => {
            button.addEventListener('click', async (event) => {
                event.stopPropagation();
                await actions.waitForRouteReady();
                actions.startBoardDevelopment(button.dataset.boardNewChat);
            });
        });
        document.querySelectorAll('[data-board-kanban]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                actions.openBoardFromSidebar(button.dataset.boardKanban);
            });
        });
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
        const running = Boolean((sessions || []).length);
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
        el('thread-context-pin').textContent = chat.pinned ? 'Unpin thread' : 'Pin thread';
        el('thread-context-archive').textContent = chat.archived ? 'Restore thread' : 'Archive thread';
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
        el('board-link-workflow').innerHTML =
            '<option value="">No default workflow</option>' +
            context.workflows
                .map(
                    (workflow) =>
                        `<option value="${Number(workflow.id)}"${Number(board?.default_workflow_id) === Number(workflow.id) ? ' selected' : ''}>${actions.escapeHtml(workflow.name || 'Untitled workflow')}</option>`
                )
                .join('');
        el('board-whatsapp-jid').value = '';
        el('board-whatsapp-name').value = '';
        el('board-whatsapp-auto').checked = false;
        el('board-whatsapp-add').disabled = !board || board.provider !== 'decisions';
        if (board?.provider === 'decisions') await loadBoardWhatsAppLinks(board);
        else el('board-whatsapp-links').innerHTML = '<p>Save the local board before linking WhatsApp.</p>';
        closeBoardContextMenu();
        state.contextBoardKey = board?.key || '';
        el('board-link-dialog').showModal();
        window.setTimeout(() => el('board-edit-name').focus(), 0);
    }

    async function loadBoardWhatsAppLinks(board) {
        try {
            const links = await actions.api(`/tickets/boards/${Number(board.local_id || board.id)}/whatsapp-links`);
            el('board-whatsapp-links').innerHTML = links.length
                ? links
                      .map(
                          (link) =>
                              `<div class="channel-link-row"><div><strong>${actions.escapeHtml(link.contact_name || link.phone_number || link.phone_jid)}</strong><small>${actions.escapeHtml(link.phone_jid)}${link.auto_snapshot ? ' · automatic snapshots' : ' · manual snapshots'}</small></div><button type="button" data-board-link-remove="${Number(link.id)}" aria-label="Remove WhatsApp link">×</button></div>`
                      )
                      .join('')
                : '<p>No WhatsApp people or groups linked.</p>';
            el('board-whatsapp-links')
                .querySelectorAll('[data-board-link-remove]')
                .forEach((button) =>
                    button.addEventListener('click', async () => {
                        await actions.unlinkIncomingChannel(Number(board.local_id || board.id), Number(button.dataset.boardLinkRemove));
                        await loadBoardWhatsAppLinks(board);
                    })
                );
        } catch (error) {
            el('board-whatsapp-links').innerHTML = `<p>${actions.escapeHtml(error.message || 'Could not load WhatsApp links.')}</p>`;
        }
    }

    async function addBoardWhatsAppLink() {
        const board = actions.boardByKey(el('board-link-dialog').dataset.boardKey);
        const jid = el('board-whatsapp-jid').value.trim();
        if (!board || !jid) return;
        try {
            await actions.api(`/tickets/boards/${Number(board.local_id || board.id)}/whatsapp-links`, {
                method: 'POST',
                body: {
                    phone_jid: jid,
                    contact_name: el('board-whatsapp-name').value.trim(),
                    auto_snapshot: el('board-whatsapp-auto').checked
                }
            });
            el('board-whatsapp-jid').value = '';
            el('board-whatsapp-name').value = '';
            el('board-whatsapp-auto').checked = false;
            await loadBoardWhatsAppLinks(board);
            await actions.refreshIncomingData();
            actions.toast('WhatsApp channel linked.');
        } catch (error) {
            actions.toast(error.message || 'Could not link WhatsApp.', 'error');
        }
    }

    async function saveBoardLink(event) {
        event.preventDefault();
        const board = actions.boardByKey(el('board-link-dialog').dataset.boardKey);
        const name = el('board-edit-name').value.trim();
        if (!name) return;
        const workflowId = Number(el('board-link-workflow').value || 0) || null;
        const body = {
            name,
            folder_location: el('board-edit-folder').value.trim(),
            startup_instructions: el('board-edit-terminals').value,
            default_workflow_id: workflowId || 0
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
                            default_project_id: board.project_id || 0,
                            default_workflow_id: workflowId || 0
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

    function openBoardDeleteDialog() {
        const board = actions.boardByKey(el('board-link-dialog').dataset.boardKey);
        if (!board || board.provider !== 'decisions') return;
        el('board-delete-dialog').dataset.boardKey = board.key;
        el('delete-board-repository').checked = false;
        const path = board.folder_location || board.default_project_folder || '';
        el('delete-board-repository').disabled = !path;
        el('delete-board-repository-path').textContent = path || 'No project folder configured';
        el('board-link-dialog').close();
        el('board-delete-dialog').showModal();
    }

    async function deleteBoard(event) {
        event.preventDefault();
        const board = actions.boardByKey(el('board-delete-dialog').dataset.boardKey);
        if (!board) return;
        try {
            const removeRepository = el('delete-board-repository').checked;
            await actions.api(`/tickets/boards/${Number(board.local_id || board.id)}?delete_repository=${removeRepository}`, { method: 'DELETE' });
            el('board-delete-dialog').close();
            if (context.selectedBoardKey === board.key) actions.showEmptyTask();
            await actions.refreshShell({ preserveConversation: true });
            actions.toast(removeRepository ? 'Board, linked data, and repository deleted.' : 'Board and linked data deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the board.', 'error');
        }
    }

    function boardGroupHtml(board) {
        const ownsProjectChats = board.project_id && actions.boardForProject(board.project_id)?.key === board.key;
        const chats = context.chats.filter((chat) => {
            if (chat.pinned) return false;
            if (chat.development_board_key) return actions.boardByKey(chat.development_board_key)?.key === board.key;
            return ownsProjectChats && Number(chat.project_id) === Number(board.project_id);
        });
        const hasTasks = chats.length > 0;
        const collapsed = hasTasks && state.collapsedBoardKeys.has(board.key);
        const orderedChats = chats
            .slice()
            .sort(
                (a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || String(b.modified_date || '').localeCompare(String(a.modified_date || ''))
            );
        const rows = orderedChats
            .map((chat) => {
                const active = context.workspaceMode === 'chat' && Number(context.currentChat?.id) === Number(chat.id);
                return `<div class="task-row-wrap${active ? ' active' : ''}">
                <button class="task-row" data-chat-id="${Number(chat.id)}"${active ? ' aria-current="page"' : ''}>
                    ${actions.threadRowContentHtml(chat, Boolean(chat.pinned))}
                </button>
            </div>`;
            })
            .join('');
        const selected = !['terminals', 'terminals_home'].includes(context.workspaceMode) && context.selectedBoardKey === board.key;
        const kanbanAction = actions.boardSupportsKanban(board)
            ? `<button class="board-row-action board-kanban" type="button" data-board-kanban="${actions.escapeHtml(board.key)}" aria-label="Open ${actions.escapeHtml(board.name || 'board')} Kanban" title="Open Kanban"><span aria-hidden="true">▦</span></button>`
            : '';
        const projectRow = `<div class="project-row${selected ? ' active' : ''}">
            <span class="board-link-bead${board.project_id ? ' linked' : ''}" title="${board.project_id ? 'Linked to a project' : 'Not linked to a project'}" aria-label="${board.project_id ? 'Linked to a project' : 'Not linked to a project'}"></span>
            <button class="project-select" type="button" data-board-select="${actions.escapeHtml(board.key)}"${selected ? ' aria-current="true"' : ''}><span>${actions.escapeHtml(board.name || 'Ticket board')}</span></button>
            <div class="board-row-tail${hasTasks ? ' has-threads' : ''}">${hasTasks ? `<button class="project-toggle" type="button" data-board-toggle="${actions.escapeHtml(board.key)}" aria-expanded="${String(!collapsed)}" aria-label="${collapsed ? 'Expand' : 'Collapse'} ${actions.escapeHtml(board.name || 'board')} threads"><span class="chevron">${collapsed ? '▶' : '▼'}</span></button>` : ''}${kanbanAction}<button class="board-row-action board-new-chat" type="button" data-board-new-chat="${actions.escapeHtml(board.key)}" aria-label="New thread in ${actions.escapeHtml(board.name || 'board')}" title="New thread in board"><span aria-hidden="true">+</span></button></div>
        </div>`;
        return `<section class="project-group" data-board-group="${actions.escapeHtml(board.key)}">
            ${projectRow}
            <div class="task-list${hasTasks && !collapsed ? '' : ' hidden'}">${rows}</div>
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
            if (closing) state.collapsedBoardKeys.add(boardKey);
            else state.collapsedBoardKeys.delete(boardKey);
            try {
                window.localStorage.setItem('decisions-development-collapsed-boards', JSON.stringify(Array.from(state.collapsedBoardKeys)));
            } catch (_) {
                /* local preference only */
            }
            list.classList.toggle('hidden', closing);
            button.querySelector('.chevron').textContent = closing ? '▶' : '▼';
            button.setAttribute('aria-expanded', String(!closing));
            button.setAttribute('aria-label', `${closing ? 'Expand' : 'Collapse'} ${boardName} threads`);
        });
    }

    function toggleBoardPin(boardKey) {
        const set = new Set(state.pinnedBoardKeys);
        const pinning = !set.has(boardKey);
        if (pinning) set.add(boardKey);
        else set.delete(boardKey);
        state.pinnedBoardKeys = Array.from(set);
        try {
            window.localStorage.setItem('decisions-development-pinned-boards', JSON.stringify(state.pinnedBoardKeys));
        } catch (_) {
            /* local preference only */
        }
        renderSidebar();
        actions.toast(pinning ? 'Board pinned.' : 'Board unpinned.');
        closeBoardContextMenu();
    }

    function bindEvents() {
        el('new-thread-button').addEventListener('click', actions.startNewDevelopmentFromSidebar);
        el('sidebar-plan-toggle').addEventListener('click', () => actions.setWorkspaceMode('plan'));
        el('sidebar-incoming-toggle').addEventListener('click', () => actions.setWorkspaceMode('incoming'));
        el('sidebar-rules-toggle').addEventListener('click', () => actions.setWorkspaceMode('automations'));
        el('sidebar-rule-add').addEventListener('click', () => {
            actions.setWorkspaceMode('automations');
            window.setTimeout(() => el('scheduled-prompt-input')?.focus(), 0);
        });
        el('sidebar-workflows-toggle').addEventListener('click', () => actions.setWorkspaceMode('workflows'));
        el('sidebar-workflow-add').addEventListener('click', () => actions.setWorkspaceMode('workflows'));
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
        el('thread-context-terminal-toggle').addEventListener('click', toggleThreadContextTerminals);
        el('thread-context-archive').addEventListener('click', contextArchiveThread);
        el('thread-context-delete').addEventListener('click', () => {
            const chatId = state.contextThreadId;
            closeThreadContextMenu();
            if (chatId) actions.deleteThread(chatId);
        });
        el('board-link-form').addEventListener('submit', saveBoardLink);
        el('board-folder-browse').addEventListener('click', browseBoardFolder);
        el('board-whatsapp-add').addEventListener('click', addBoardWhatsAppLink);
        el('delete-board-button').addEventListener('click', openBoardDeleteDialog);
        el('board-delete-form').addEventListener('submit', deleteBoard);
        el('boards-show-more').addEventListener('click', () => {
            state.boardsExpanded = !state.boardsExpanded;
            renderSidebar();
        });
        el('sidebar-open').addEventListener('click', actions.openSidebar);
        el('sidebar-close').addEventListener('click', actions.closeDrawers);
    }
    return {
        bindEvents,
        closeBoardContextMenu,
        closeThreadContextMenu,
        openBoardContextMenu,
        openBoardLinkDialog,
        renderSidebar,
        threadBoard
    };
}
