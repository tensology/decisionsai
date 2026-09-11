// Owns boards rendering, interactions, and private view state.
export function createBoards({ context, actions, el }) {
    const state = {
        kanbanViewMode:
            new URLSearchParams(window.location.search).get('view') === 'list' || window.localStorage.getItem('decisions.developmentKanbanView') === 'list'
                ? 'list'
                : 'board',
        collapsedKanbanLanes: new Set(),
        expandedKanbanLanes: new Set(),
        contextTicketKey: ''
    };
    function kanbanLanes() {
        return context.boardDetails[context.selectedBoardKey]?.lanes || [];
    }

    function getKanbanViewMode() {
        return state.kanbanViewMode;
    }

    function kanbanTicketByKey(ticketKey) {
        return (context.boardTickets[context.selectedBoardKey] || []).find((ticket) => ticket.key === ticketKey) || null;
    }

    function kanbanTicketStatusHtml(ticket) {
        const status = String(ticket.workflow_status || '').toLowerCase();
        if (!status) return '<span class="kanban-run-status-placeholder" aria-hidden="true"></span>';
        const active = ['running', 'waiting', 'paused', 'queued', 'initializing'].includes(status);
        const tone = ['failed', 'error'].includes(status)
            ? 'red'
            : ['waiting', 'paused', 'queued', 'initializing'].includes(status)
              ? 'amber'
              : ['running', 'completed', 'complete', 'success'].includes(status)
                ? 'green'
                : 'grey';
        const label = actions.statusLabel(status);
        const dot = `<span aria-hidden="true"></span>`;
        return active
            ? `<button type="button" class="kanban-run-status active ${actions.escapeHtml(status)} ${tone}" data-ticket-run-status aria-label="Open ${actions.escapeHtml(label.toLowerCase())} execution details" title="${actions.escapeHtml(label)}">${dot}</button>`
            : `<span class="kanban-run-status ${actions.escapeHtml(status)} ${tone}" role="img" aria-label="${actions.escapeHtml(label)}" title="${actions.escapeHtml(label)}">${dot}</span>`;
    }

    function kanbanTicketMetaHtml(ticket) {
        const todos = Array.isArray(ticket.todos) ? ticket.todos : [];
        const done = todos.filter((item) => item.done).length;
        const bits = [];
        if (ticket.time_estimate) bits.push(`<span>${actions.escapeHtml(ticket.time_estimate)} estimate</span>`);
        if (ticket.time_spent) bits.push(`<span>${actions.escapeHtml(ticket.time_spent)} logged</span>`);
        if (todos.length) bits.push(`<span>${done}/${todos.length} to-do</span>`);
        if (Array.isArray(ticket.labels) && ticket.labels.length) bits.push(`<span>${ticket.labels.length} labels</span>`);
        return bits.length ? `<div class="development-kanban-card-meta">${bits.join('')}</div>` : '';
    }

    function kanbanDurationSeconds(value) {
        if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, Math.round(value));
        const text = String(value || '')
            .trim()
            .toLowerCase();
        if (!text) return 0;
        const clock = text.match(/^(\d+):([0-5]?\d):([0-5]?\d)$/);
        if (clock) return Number(clock[1]) * 3600 + Number(clock[2]) * 60 + Number(clock[3]);
        if (/^\d+$/.test(text)) return Number(text);
        let seconds = 0;
        let matched = false;
        for (const part of text.matchAll(/(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b/g)) {
            matched = true;
            const amount = Number(part[1]);
            const unit = part[2][0];
            seconds += unit === 'h' ? amount * 3600 : unit === 'm' ? amount * 60 : amount;
        }
        return matched ? Math.max(0, Math.round(seconds)) : 0;
    }

    function formatKanbanDuration(value) {
        const total = kanbanDurationSeconds(value);
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':');
    }

    function kanbanListDescriptionHtml(description) {
        if (!description) return '';
        return `<span class="development-kanban-list-description" title="${actions.escapeHtml(description)}"><span class="development-kanban-list-description-track"><span>${actions.escapeHtml(description)}</span></span></span>`;
    }

    function initializeKanbanListMarquees(root) {
        root.querySelectorAll('.development-kanban-list-description').forEach((description) => {
            const track = description.querySelector('.development-kanban-list-description-track');
            const first = track?.querySelector('span');
            if (!track || !first) return;
            window.requestAnimationFrame(() => {
                if (!description.isConnected || track.scrollWidth <= description.clientWidth + 1) return;
                const clone = first.cloneNode(true);
                clone.setAttribute('aria-hidden', 'true');
                track.appendChild(clone);
                description.classList.add('marquee');
                track.style.setProperty('--kanban-list-marquee-duration', `${Math.max(10, Math.round(first.scrollWidth / 40))}s`);
            });
        });
    }

    function kanbanCardHtml(board, lane, ticket, layout) {
        const id = ticket.id || ticket.key;
        const key = `${board.provider}:${id}`;
        const title = ticket.title || ticket.name || ticket.key || 'Untitled ticket';
        const description = ticket.description || ticket.desc || ticket.summary || '';
        const priority = ticket.priority || '';
        const complexity = ticket.complexity || '';
        const source = ticket.source_provider || ticket.external_source || (board.provider !== 'decisions' ? board.provider : '');
        const showSource = source && String(source).toLowerCase() !== String(board.provider || '').toLowerCase();
        const listDescription = layout === 'list' ? kanbanListDescriptionHtml(description) : '';
        const listTime =
            layout === 'list'
                ? `<span class="kanban-list-time" title="Time logged" aria-label="Time logged ${formatKanbanDuration(ticket.time_spent)}">${formatKanbanDuration(ticket.time_spent)}</span>`
                : '';
        return `<article class="development-kanban-card${layout === 'list' ? ' list-row' : ''}" draggable="true" tabindex="0" role="button" data-kanban-ticket="${actions.escapeHtml(key)}" data-ticket-id="${actions.escapeHtml(id)}" aria-label="Open ${actions.escapeHtml(title)}">
            <div class="development-kanban-card-head"><span class="development-kanban-card-badges">${showSource ? `<span class="kanban-source">${actions.escapeHtml(actions.providerName(source))}</span>` : ''}${complexity ? `<span class="kanban-complexity">${actions.escapeHtml(String(complexity).replaceAll('_', ' '))}</span>` : ''}${priority ? `<span class="kanban-priority ${actions.escapeHtml(String(priority).toLowerCase())}">${actions.escapeHtml(priority)}</span>` : ''}${listTime}</span><button type="button" class="development-kanban-ticket-menu" data-kanban-menu aria-label="Ticket actions" title="Ticket actions">•••</button></div>
            <div class="development-kanban-card-copy"><span class="development-kanban-card-title">${kanbanTicketStatusHtml(ticket)}<strong title="${actions.escapeHtml(title)}">${actions.escapeHtml(title)}</strong></span>${layout === 'list' ? listDescription : description ? `<p>${actions.escapeHtml(description)}</p>` : ''}${layout === 'list' ? '' : kanbanTicketMetaHtml(ticket)}</div>
            <footer>${ticket.source_chat_id ? '<span class="kanban-thread-link">Thread linked</span>' : '<span></span>'}<span>${Array.isArray(ticket.files) && ticket.files.length ? `${ticket.files.length} file${ticket.files.length === 1 ? '' : 's'}` : ''}</span></footer>
        </article>`;
    }

    function renderKanbanBoardLanes(board, lanes) {
        return lanes
            .map((lane) => {
                const tickets = lane.tickets || lane.cards || [];
                const laneId = lane.id || lane.key || lane.name;
                return `<section class="development-kanban-lane" data-kanban-lane="${actions.escapeHtml(laneId)}">
                <header><strong>${actions.escapeHtml(lane.name || lane.title || 'Untitled lane')}</strong><span>${tickets.length}</span></header>
                <div class="development-kanban-card-list" data-kanban-dropzone="${actions.escapeHtml(laneId)}">${tickets.map((ticket) => kanbanCardHtml(board, lane, ticket, 'board')).join('')}</div>
            </section>`;
            })
            .join('');
    }

    function kanbanListRank(value, ranks, fallback = 2) {
        const normalized = String(value || '')
            .toLowerCase()
            .trim()
            .replaceAll('-', '_')
            .replaceAll(' ', '_');
        return ranks[normalized] ?? fallback;
    }

    function compareKanbanListTickets(a, b) {
        const complexity = {
            very_complex: 5,
            extra_high: 5,
            complex: 4,
            high: 4,
            moderate: 3,
            medium: 3,
            simple: 2,
            low: 1,
            trivial: 1
        };
        const priority = {
            highest: 6,
            urgent: 5,
            critical: 5,
            high: 4,
            medium: 3,
            normal: 3,
            low: 1,
            lowest: 0
        };
        const priorityDiff = kanbanListRank(b.priority, priority) - kanbanListRank(a.priority, priority);
        if (priorityDiff) return priorityDiff;
        const complexityDiff = kanbanListRank(b.complexity, complexity) - kanbanListRank(a.complexity, complexity);
        if (complexityDiff) return complexityDiff;
        const positionDiff = Number(a.position || 0) - Number(b.position || 0);
        return (
            positionDiff ||
            String(a.title || '').localeCompare(String(b.title || ''), undefined, {
                sensitivity: 'base'
            })
        );
    }

    function renderKanbanListLanes(board, lanes) {
        return `<div class="development-kanban-list">${lanes
            .map((lane) => {
                const tickets = [...(lane.tickets || lane.cards || [])].sort(compareKanbanListTickets);
                const laneId = String(lane.id || lane.key || lane.name);
                const laneKey = `${board.key}:${laneId}`;
                const laneName = String(lane.name || lane.title || '')
                    .trim()
                    .toLowerCase();
                const collapsedByDefault = ['done', 'complete', 'completed'].includes(laneName);
                const collapsed = state.collapsedKanbanLanes.has(laneKey) || (collapsedByDefault && !state.expandedKanbanLanes.has(laneKey));
                return `<section class="development-kanban-list-section${collapsed ? ' collapsed' : ''}" data-kanban-lane="${actions.escapeHtml(laneId)}">
                <button type="button" class="development-kanban-list-heading" data-kanban-lane-toggle="${actions.escapeHtml(laneId)}" aria-expanded="${String(!collapsed)}"><span aria-hidden="true">›</span><strong>${actions.escapeHtml(lane.name || lane.title || 'Untitled lane')}</strong><small>${tickets.length}</small></button>
                <div class="development-kanban-list-body" data-kanban-dropzone="${actions.escapeHtml(laneId)}">${tickets.map((ticket) => kanbanCardHtml(board, lane, ticket, 'list')).join('')}</div>
            </section>`;
            })
            .join('')}</div>`;
    }

    function bindKanbanWorkspaceInteractions(board, lanesNode) {
        lanesNode.querySelectorAll('[data-kanban-ticket]').forEach((card) => {
            card.addEventListener('click', (event) => {
                if (event.target.closest('[data-kanban-menu], [data-ticket-run-status]')) return;
                openKanbanTicketDialog(card.dataset.kanbanTicket);
            });
            card.addEventListener('contextmenu', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openTicketContextMenu(event, card.dataset.kanbanTicket);
            });
            card.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    openKanbanTicketDialog(card.dataset.kanbanTicket);
                }
            });
            card.addEventListener('dragstart', (event) => {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/decisions-ticket-key', card.dataset.kanbanTicket);
                card.classList.add('dragging');
            });
            card.addEventListener('dragend', () => card.classList.remove('dragging'));
            card.querySelector('[data-kanban-menu]')?.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openTicketContextMenu(event, card.dataset.kanbanTicket, event.currentTarget);
            });
            card.querySelector('[data-ticket-run-status]')?.addEventListener('click', (event) => {
                event.stopPropagation();
                openTicketRunPopover(event.currentTarget, card.dataset.kanbanTicket);
            });
        });
        lanesNode.querySelectorAll('[data-kanban-lane-toggle]').forEach((button) =>
            button.addEventListener('click', () => {
                const key = `${board.key}:${button.dataset.kanbanLaneToggle}`;
                if (button.getAttribute('aria-expanded') === 'true') {
                    state.expandedKanbanLanes.delete(key);
                    state.collapsedKanbanLanes.add(key);
                } else {
                    state.collapsedKanbanLanes.delete(key);
                    state.expandedKanbanLanes.add(key);
                }
                renderKanbanWorkspace();
            })
        );
        lanesNode.querySelectorAll('[data-kanban-dropzone]').forEach((zone) => {
            zone.addEventListener('dragover', (event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                zone.classList.add('drag-over');
            });
            zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
            zone.addEventListener('drop', (event) => {
                event.preventDefault();
                zone.classList.remove('drag-over');
                moveKanbanTicket(event.dataTransfer.getData('text/decisions-ticket-key'), zone.dataset.kanbanDropzone, zone.children.length);
            });
        });
        initializeKanbanListMarquees(lanesNode);
    }

    function syncKanbanHeader(board) {
        const boardMode = state.kanbanViewMode === 'board';
        el('development-kanban-view-board').classList.toggle('active', boardMode);
        el('development-kanban-view-board').setAttribute('aria-pressed', String(boardMode));
        el('development-kanban-view-list').classList.toggle('active', !boardMode);
        el('development-kanban-view-list').setAttribute('aria-pressed', String(!boardMode));
        el('development-kanban-view-label').textContent = boardMode ? 'Kanban' : 'List';
        const refreshButton = el('development-kanban-refresh');
        const external = board && (board.provider === 'jira' || board.provider === 'trello');
        refreshButton.classList.toggle('hidden', !board);
        refreshButton.setAttribute('aria-label', external ? `Re-sync ${actions.providerName(board.provider)} board` : 'Refresh board');
        refreshButton.title = refreshButton.getAttribute('aria-label');
        el('development-kanban-edit-board').classList.toggle('hidden', !board || board.provider !== 'decisions');
        el('development-kanban-more').classList.toggle('hidden', !board);
    }

    function setKanbanViewMode(mode) {
        state.kanbanViewMode = mode === 'list' ? 'list' : 'board';
        try {
            window.localStorage.setItem('decisions.developmentKanbanView', state.kanbanViewMode);
        } catch (_) {
            /* local preference only */
        }
        actions.closeBoardContextMenu();
        renderKanbanWorkspace();
    }

    function renderKanbanWorkspace() {
        const board = actions.boardByKey(context.selectedBoardKey);
        const lanesNode = el('development-kanban-lanes');
        syncKanbanHeader(board);
        if (!board) {
            el('development-kanban-board').textContent = 'No board selected';
            lanesNode.innerHTML = '<div class="development-kanban-empty">Select a board from the Development sidebar.</div>';
            return;
        }
        el('development-kanban-board').textContent = board.name || 'Untitled board';
        const detail = context.boardDetails[board.key];
        if (!detail) {
            lanesNode.innerHTML = '<div class="development-kanban-empty">Loading board...</div>';
            return;
        }
        const lanes = detail.lanes || [];
        lanesNode.classList.toggle('list-view', state.kanbanViewMode === 'list');
        lanesNode.innerHTML = lanes.length
            ? state.kanbanViewMode === 'list'
                ? renderKanbanListLanes(board, lanes)
                : renderKanbanBoardLanes(board, lanes)
            : '<div class="development-kanban-empty">This board has no lanes yet. Edit the board to configure it.</div>';
        bindKanbanWorkspaceInteractions(board, lanesNode);
    }

    function setKanbanStatus(message, type) {
        const node = el('development-kanban-status');
        node.textContent = message || '';
        node.classList.toggle('hidden', !message);
        node.classList.toggle('error', type === 'error');
    }

    function moveCachedKanbanTicket(board, ticket, laneId, position) {
        const detail = context.boardDetails[board.key];
        if (!detail) return false;
        let rawTicket = null;
        (detail.lanes || []).forEach((lane) => {
            const collection = Array.isArray(lane.tickets) ? lane.tickets : Array.isArray(lane.cards) ? lane.cards : null;
            if (!collection) return;
            const index = collection.findIndex((item) => String(item.id || item.key) === String(ticket.id));
            if (index >= 0) rawTicket = collection.splice(index, 1)[0];
        });
        const target = (detail.lanes || []).find((lane) => [lane.id, lane.key, lane.name].some((value) => String(value) === String(laneId)));
        if (!rawTicket || !target) return false;
        const collection = Array.isArray(target.tickets) ? target.tickets : Array.isArray(target.cards) ? target.cards : (target.tickets = []);
        collection.splice(Math.max(0, Math.min(Number(position) || 0, collection.length)), 0, rawTicket);
        actions.cacheBoardDetail(board, detail);
        renderKanbanWorkspace();
        return true;
    }

    async function forceRefreshKanbanBoard() {
        const board = actions.boardByKey(context.selectedBoardKey);
        if (!board) return;
        const button = el('development-kanban-refresh');
        button.disabled = true;
        button.classList.add('refreshing');
        const external = board.provider === 'jira' || board.provider === 'trello';
        setKanbanStatus(external ? `Re-syncing ${actions.providerName(board.provider)}...` : 'Refreshing board...');
        try {
            await actions.loadBoardTickets(board.key, {
                force: true,
                remote: external,
                throwOnError: true
            });
            setKanbanStatus('');
        } catch (error) {
            setKanbanStatus(error.message || `Could not refresh ${actions.providerName(board.provider)}.`, 'error');
        } finally {
            button.disabled = false;
            button.classList.remove('refreshing');
        }
    }

    function closeTicketContextMenu() {
        el('ticket-context-menu').classList.add('hidden');
        state.contextTicketKey = '';
    }

    function positionContextMenu(menu, event, anchor) {
        menu.classList.remove('hidden');
        const rect = anchor?.getBoundingClientRect();
        const x = event?.clientX || rect?.right || 12;
        const y = event?.clientY || rect?.bottom || 12;
        menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
        menu.focus({ preventScroll: true });
    }

    function openTicketContextMenu(event, ticketKey, anchor) {
        const board = actions.boardByKey(context.selectedBoardKey);
        const ticket = kanbanTicketByKey(ticketKey);
        if (!board || !ticket) return;
        actions.closeBoardContextMenu();
        state.contextTicketKey = ticketKey;
        const menu = el('ticket-context-menu');
        const local = board.provider === 'decisions';
        const hasProject = Boolean(ticket.raw?.linked_project_id || context.boardDetails[board.key]?.default_project_id || board.project_id);
        const hasWorkflow = Boolean(ticket.raw?.linked_workflow_id || context.boardDetails[board.key]?.default_workflow_id);
        const threadButton = menu.querySelector('[data-ticket-menu-action="thread"]');
        threadButton.textContent = ticket.source_chat_id ? 'Open Thread' : 'Create Thread';
        menu.querySelector('[data-ticket-menu-action="workflow"]').classList.toggle('hidden', !local || !hasWorkflow);
        menu.querySelector('[data-ticket-menu-action="agent"]').classList.toggle('hidden', !local || !hasProject);
        menu.querySelector('[data-ticket-menu-action="project"]').classList.toggle('hidden', !local || !hasProject);
        menu.querySelector('[data-ticket-menu-action="delete"]').classList.toggle('hidden', !local);
        positionContextMenu(menu, event, anchor);
    }

    async function copyKanbanTicketDetails(ticket) {
        const files = Array.isArray(ticket.raw?.files) ? ticket.raw.files : [];
        const links = Array.isArray(ticket.raw?.links) ? ticket.raw.links : [];
        const extras = files
            .map((item) => item.url || item.download_url)
            .concat(links.map((item) => item.url))
            .filter(Boolean);
        const text = [ticket.ticket_title, ticket.text, extras.length ? `References:\n${extras.map((item) => `- ${item}`).join('\n')}` : '']
            .filter(Boolean)
            .join('\n\n');
        try {
            await navigator.clipboard.writeText(text);
            actions.toast('Ticket details copied.');
        } catch (_) {
            const input = document.createElement('textarea');
            input.value = text;
            input.style.position = 'fixed';
            input.style.opacity = '0';
            document.body.appendChild(input);
            input.select();
            document.execCommand('copy');
            input.remove();
            actions.toast('Ticket details copied.');
        }
    }

    async function runKanbanTicketAction(ticket, action) {
        if (!ticket?.ticket_id) return;
        const endpoints = {
            workflow: `/tickets/tickets/${ticket.ticket_id}/send-to-workflow`,
            agent: `/tickets/tickets/${ticket.ticket_id}/send-to-cli`,
            project: `/tickets/tickets/${ticket.ticket_id}/send-to-project`
        };
        const labels = { workflow: 'workflow', agent: 'Codex', project: 'project' };
        const endpoint = endpoints[action];
        if (!endpoint) return;
        setKanbanStatus(`Sending ticket to ${labels[action]}...`);
        try {
            const result = await actions.api(endpoint, {
                method: 'POST',
                body: action === 'agent' ? { backend_id: 'codex' } : {}
            });
            setKanbanStatus('');
            actions.toast(result.message || `Ticket sent to ${labels[action]}.`);
            await actions.loadBoardTickets(context.selectedBoardKey, { force: true });
        } catch (error) {
            setKanbanStatus(error.message || `Could not send ticket to ${labels[action]}.`, 'error');
        }
    }

    async function runTicketContextMenuAction(event) {
        const button = event.target.closest('[data-ticket-menu-action]');
        if (!button) return;
        const action = button.dataset.ticketMenuAction;
        const ticket = kanbanTicketByKey(state.contextTicketKey);
        closeTicketContextMenu();
        if (!ticket) return;
        if (action === 'open') openKanbanTicketDialog(ticket.key);
        else if (action === 'copy') await copyKanbanTicketDetails(ticket);
        else if (action === 'thread') {
            openKanbanTicketDialog(ticket.key);
            await createThreadFromKanbanTicket();
        } else if (action === 'delete') {
            openKanbanTicketDialog(ticket.key);
            await deleteKanbanTicket();
        } else await runKanbanTicketAction(ticket, action);
    }

    async function openTicketRunPopover(anchor, ticketKey) {
        document.querySelector('.development-run-popover')?.remove();
        const ticket = kanbanTicketByKey(ticketKey);
        if (!ticket?.ticket_id) return;
        try {
            const data = await actions.api(`/tickets/tickets/${ticket.ticket_id}/active-run`);
            if (!data.active) {
                actions.toast('No active run found for this ticket.');
                return;
            }
            const popover = document.createElement('div');
            popover.className = 'development-run-popover';
            popover.innerHTML = `<header><strong>${actions.escapeHtml(data.workflow_name || data.ticket_title || ticket.ticket_title)}</strong><span>${actions.escapeHtml(data.status || 'running')}</span></header>${data.current_step_name ? `<p>Step: ${actions.escapeHtml(data.current_step_name)}</p>` : ''}${data.phase ? `<p>Phase: ${actions.escapeHtml(data.phase)}</p>` : ''}<footer><a href="${actions.escapeHtml(data.open_url || '/development/')}">Open run</a>${data.status === 'waiting' ? `<a href="${actions.escapeHtml(data.open_url || '/development/')}">Respond</a>` : ''}<button type="button" class="danger">Cancel run</button></footer>`;
            document.body.appendChild(popover);
            const rect = anchor.getBoundingClientRect();
            popover.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - popover.offsetWidth - 8))}px`;
            popover.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - popover.offsetHeight - 8))}px`;
            popover.querySelector('button').addEventListener('click', async () => {
                const target = data.cancellation_target?.url;
                if (
                    !target ||
                    !(await actions.confirmAction({
                        title: 'Cancel run',
                        message: `Cancel the active run for “${ticket.ticket_title}”?`,
                        confirmLabel: 'Cancel run',
                        danger: true
                    }))
                )
                    return;
                try {
                    await actions.api(target, { method: 'POST', body: {} });
                    popover.remove();
                    await actions.loadBoardTickets(context.selectedBoardKey, {
                        force: true
                    });
                    actions.toast('Run cancelled.');
                } catch (error) {
                    actions.toast(error.message || 'Could not cancel the run.', 'error');
                }
            });
        } catch (error) {
            actions.toast(error.message || 'Could not load the active run.', 'error');
        }
    }

    function setKanbanTicketTab(tabName) {
        ['details', 'notes', 'attachments', 'todos'].forEach((name) => {
            el(`kanban-ticket-${name}-tab`).setAttribute('aria-selected', String(name === tabName));
            el(`kanban-ticket-${name}-panel`).hidden = name !== tabName;
        });
    }

    function ticketFileUrl(ticket, file) {
        return (
            file.url ||
            file.download_url ||
            file.preview_url ||
            (ticket?.ticket_id && file.id ? `/api/tickets/tickets/${ticket.ticket_id}/files/${file.id}/content` : '')
        );
    }

    function renderKanbanTicketRelations(ticket, editable) {
        const raw = ticket?.raw || {};
        const links = Array.isArray(raw.links) ? raw.links : [];
        const files = Array.isArray(raw.files) ? raw.files : [];
        const todos = Array.isArray(raw.todos) ? raw.todos : [];
        const comments = Array.isArray(raw.comments) ? raw.comments : [];
        el('kanban-ticket-attachment-count').textContent = links.length + files.length ? String(links.length + files.length) : '';
        el('kanban-ticket-todo-count').textContent = todos.length ? String(todos.length) : '';
        el('kanban-ticket-comment-count').textContent = comments.length ? `${comments.length} comment${comments.length === 1 ? '' : 's'}` : '';
        el('kanban-ticket-links').innerHTML = links.length
            ? links
                  .map(
                      (item) =>
                          `<div class="kanban-ticket-relation-row"><a href="${actions.escapeHtml(item.url || '#')}" target="_blank" rel="noopener"><strong>${actions.escapeHtml(item.title || item.url || 'Link')}</strong><small>${actions.escapeHtml(item.url || '')}</small></a>${editable ? `<button type="button" data-ticket-link-delete="${Number(item.id)}" aria-label="Delete link">×</button>` : ''}</div>`
                  )
                  .join('')
            : '<p class="kanban-ticket-no-attachments">No links added</p>';
        el('kanban-ticket-attachments').innerHTML = files.length
            ? files
                  .map((item) => {
                      const name = item.filename || item.name || 'Attachment';
                      const mime = item.mime_type || item.type || '';
                      const url = ticketFileUrl(ticket, item);
                      const isImage = String(mime).startsWith('image/') || /\.(png|jpe?g|gif|webp|avif|svg)$/i.test(name);
                      return `<article class="kanban-ticket-attachment">${isImage && url ? `<img src="${actions.escapeHtml(url)}" alt="${actions.escapeHtml(name)}">` : '<span class="kanban-ticket-file-icon" aria-hidden="true">+</span>'}<span>${url ? `<a href="${actions.escapeHtml(url)}" target="_blank" rel="noopener"><strong>${actions.escapeHtml(name)}</strong></a>` : `<strong>${actions.escapeHtml(name)}</strong>`}<small>${actions.escapeHtml(mime || 'File')}</small></span>${editable ? `<button type="button" data-ticket-file-delete="${Number(item.id)}" aria-label="Delete file">×</button>` : ''}</article>`;
                  })
                  .join('')
            : '<p class="kanban-ticket-no-attachments">No files attached</p>';
        el('kanban-ticket-todos').innerHTML = todos.length
            ? todos
                  .map(
                      (item) =>
                          `<div class="kanban-ticket-todo-row${item.done ? ' done' : ''}"><label><input type="checkbox" data-ticket-todo-toggle="${Number(item.id)}"${item.done ? ' checked' : ''}${editable ? '' : ' disabled'}><span>${actions.escapeHtml(item.text || '')}</span></label>${editable ? `<button type="button" data-ticket-todo-delete="${Number(item.id)}" aria-label="Delete to-do">×</button>` : ''}</div>`
                  )
                  .join('')
            : '<p class="kanban-ticket-no-attachments">No to-do items</p>';
        [
            'kanban-ticket-link-title',
            'kanban-ticket-link-url',
            'kanban-ticket-add-link',
            'kanban-ticket-upload',
            'kanban-ticket-todo-input',
            'kanban-ticket-add-todo'
        ].forEach((id) => {
            el(id).disabled = !editable;
        });
        el('kanban-ticket-link-form').classList.toggle('hidden', !editable);
        el('kanban-ticket-upload').classList.toggle('hidden', !editable);
        el('kanban-ticket-todo-input').parentElement.classList.toggle('hidden', !editable);
    }

    function openKanbanTicketDialog(ticketKey) {
        const board = actions.boardByKey(context.selectedBoardKey);
        if (!board) return;
        const ticket = ticketKey ? kanbanTicketByKey(ticketKey) : null;
        const creating = !ticket;
        const externalExisting = board.provider !== 'decisions' && !creating;
        const lanes = kanbanLanes();
        const form = el('kanban-ticket-form');
        form.dataset.ticketKey = ticket?.key || '';
        form.dataset.creating = String(creating);
        el('kanban-ticket-id').value = ticket?.id || '';
        const providerLabel = el('kanban-ticket-provider');
        providerLabel.textContent = board.provider === 'decisions' ? '' : `${actions.providerName(board.provider)} ticket`;
        providerLabel.classList.toggle('hidden', board.provider === 'decisions');
        el('kanban-ticket-heading').textContent = creating ? 'New ticket' : ticket.ticket_title;
        el('kanban-ticket-title').value = ticket?.ticket_title || '';
        el('kanban-ticket-description').value = ticket?.text || '';
        el('kanban-ticket-priority').value = ticket?.priority || 'medium';
        el('kanban-ticket-complexity').value = ticket?.complexity || 'moderate';
        el('kanban-ticket-lane').innerHTML = lanes
            .map(
                (lane) =>
                    `<option value="${actions.escapeHtml(lane.id || lane.key || lane.name)}">${actions.escapeHtml(lane.name || lane.title || 'Untitled lane')}</option>`
            )
            .join('');
        el('kanban-ticket-lane').value = String(ticket?.lane_id || lanes[0]?.id || lanes[0]?.key || lanes[0]?.name || '');
        ['kanban-ticket-title', 'kanban-ticket-description', 'kanban-ticket-priority', 'kanban-ticket-lane'].forEach((id) => {
            el(id).disabled = externalExisting;
        });
        el('kanban-ticket-complexity').disabled = externalExisting;
        el('kanban-ticket-complexity-field').classList.toggle('hidden', board.provider !== 'decisions');
        el('kanban-ticket-readonly').classList.toggle('hidden', !externalExisting);
        el('kanban-ticket-save').classList.toggle('hidden', externalExisting);
        el('kanban-ticket-delete').classList.toggle('hidden', creating || board.provider !== 'decisions');
        const createThreadButton = el('kanban-ticket-create-thread');
        createThreadButton.classList.toggle('hidden', creating);
        createThreadButton.textContent = ticket?.source_chat_id ? 'Open thread' : 'Create thread';
        createThreadButton.disabled = false;
        const raw = ticket?.raw || {};
        const materialRows = Array.isArray(raw.attachments) && raw.attachments.length ? raw.attachments : raw.files || [];
        setKanbanTicketTab('details');
        el('kanban-ticket-tabs').classList.toggle('hidden', creating);
        el('kanban-ticket-notes-tab').classList.toggle('hidden', externalExisting);
        el('kanban-ticket-todos-tab').classList.toggle('hidden', externalExisting);
        el('kanban-ticket-context-notes').value = raw.context_notes || '';
        el('kanban-ticket-context-notes').readOnly = externalExisting;
        if (Array.isArray(raw.attachments) && raw.attachments.length && (!Array.isArray(raw.files) || !raw.files.length)) raw.files = materialRows;
        renderKanbanTicketRelations(ticket, Boolean(ticket && board.provider === 'decisions'));
        const sourceLink = el('kanban-ticket-source-link');
        sourceLink.classList.toggle('hidden', !ticket?.external_url);
        sourceLink.href = ticket?.external_url || '#';
        const localExisting = Boolean(ticket && board.provider === 'decisions');
        const detail = context.boardDetails[board.key] || {};
        el('kanban-ticket-run-workflow').classList.toggle('hidden', !localExisting || !(raw.linked_workflow_id || detail.default_workflow_id));
        el('kanban-ticket-run-agent').classList.toggle('hidden', !localExisting || !(raw.linked_project_id || detail.default_project_id || board.project_id));
        el('kanban-ticket-dialog').showModal();
        if (!externalExisting) window.setTimeout(() => el('kanban-ticket-title').focus(), 0);
    }

    async function saveKanbanTicket(event) {
        event.preventDefault();
        const board = actions.boardByKey(context.selectedBoardKey);
        if (!board) return;
        const form = el('kanban-ticket-form');
        const creating = form.dataset.creating === 'true';
        const title = el('kanban-ticket-title').value.trim();
        if (!title) return;
        const body = {
            title,
            description: el('kanban-ticket-description').value.trim(),
            lane_id: board.provider === 'decisions' ? Number(el('kanban-ticket-lane').value) : el('kanban-ticket-lane').value,
            priority: el('kanban-ticket-priority').value
        };
        if (board.provider === 'decisions') {
            body.complexity = el('kanban-ticket-complexity').value;
            body.context_notes = el('kanban-ticket-context-notes').value.trim();
        }
        try {
            if (board.provider === 'decisions') {
                const ticketId = Number(el('kanban-ticket-id').value || 0);
                await actions.api(creating ? '/tickets/tickets' : `/tickets/tickets/${ticketId}`, { method: creating ? 'POST' : 'PUT', body });
            } else {
                await actions.api(
                    `/tickets/external-boards/${encodeURIComponent(board.provider)}/${encodeURIComponent(board.external_id || board.id)}/create-ticket`,
                    { method: 'POST', body }
                );
            }
            el('kanban-ticket-dialog').close();
            await actions.loadBoardTickets(board.key, {
                force: true,
                remote: board.provider !== 'decisions'
            });
            actions.toast(creating ? 'Ticket created.' : 'Ticket updated.');
        } catch (error) {
            actions.toast(error.message || 'Could not save the ticket.', 'error');
        }
    }

    function currentKanbanDialogTicket() {
        return kanbanTicketByKey(el('kanban-ticket-form').dataset.ticketKey);
    }

    async function refreshKanbanTicketRelations(ticketKey) {
        await actions.loadBoardTickets(context.selectedBoardKey, { force: true });
        const ticket = kanbanTicketByKey(ticketKey);
        if (ticket) renderKanbanTicketRelations(ticket, actions.boardByKey(context.selectedBoardKey)?.provider === 'decisions');
    }

    async function addKanbanTicketLink() {
        const ticket = currentKanbanDialogTicket();
        const title = el('kanban-ticket-link-title').value.trim();
        const url = el('kanban-ticket-link-url').value.trim();
        if (!ticket?.ticket_id || !title || !url) {
            actions.toast('Enter a link title and URL.', 'error');
            return;
        }
        try {
            await actions.api(`/tickets/tickets/${ticket.ticket_id}/links`, {
                method: 'POST',
                body: { title, url }
            });
            el('kanban-ticket-link-title').value = '';
            el('kanban-ticket-link-url').value = '';
            await refreshKanbanTicketRelations(ticket.key);
            actions.toast('Link added.');
        } catch (error) {
            actions.toast(error.message || 'Could not add the link.', 'error');
        }
    }

    async function uploadKanbanTicketFiles(files) {
        const ticket = currentKanbanDialogTicket();
        if (!ticket?.ticket_id || !files?.length) return;
        try {
            for (const file of files) {
                const body = new FormData();
                body.append('file', file);
                await actions.api(`/tickets/tickets/${ticket.ticket_id}/files`, {
                    method: 'POST',
                    body
                });
            }
            el('kanban-ticket-file-input').value = '';
            await refreshKanbanTicketRelations(ticket.key);
            actions.toast(`${files.length} file${files.length === 1 ? '' : 's'} uploaded.`);
        } catch (error) {
            actions.toast(error.message || 'Could not upload the file.', 'error');
        }
    }

    async function addKanbanTicketTodo() {
        const ticket = currentKanbanDialogTicket();
        const text = el('kanban-ticket-todo-input').value.trim();
        if (!ticket?.ticket_id || !text) return;
        try {
            await actions.api(`/tickets/tickets/${ticket.ticket_id}/todos`, {
                method: 'POST',
                body: { text }
            });
            el('kanban-ticket-todo-input').value = '';
            await refreshKanbanTicketRelations(ticket.key);
        } catch (error) {
            actions.toast(error.message || 'Could not add the to-do item.', 'error');
        }
    }

    async function handleKanbanTicketRelationClick(event) {
        const ticket = currentKanbanDialogTicket();
        if (!ticket?.ticket_id) return;
        const linkDelete = event.target.closest('[data-ticket-link-delete]');
        const fileDelete = event.target.closest('[data-ticket-file-delete]');
        const todoDelete = event.target.closest('[data-ticket-todo-delete]');
        try {
            if (linkDelete)
                await actions.api(`/tickets/tickets/${ticket.ticket_id}/links/${Number(linkDelete.dataset.ticketLinkDelete)}`, { method: 'DELETE' });
            else if (fileDelete)
                await actions.api(`/tickets/tickets/${ticket.ticket_id}/files/${Number(fileDelete.dataset.ticketFileDelete)}`, { method: 'DELETE' });
            else if (todoDelete)
                await actions.api(`/tickets/tickets/${ticket.ticket_id}/todos/${Number(todoDelete.dataset.ticketTodoDelete)}`, { method: 'DELETE' });
            else return;
            await refreshKanbanTicketRelations(ticket.key);
        } catch (error) {
            actions.toast(error.message || 'Could not remove the item.', 'error');
        }
    }

    async function handleKanbanTicketTodoChange(event) {
        const checkbox = event.target.closest('[data-ticket-todo-toggle]');
        const ticket = currentKanbanDialogTicket();
        if (!checkbox || !ticket?.ticket_id) return;
        try {
            await actions.api(`/tickets/tickets/${ticket.ticket_id}/todos/${Number(checkbox.dataset.ticketTodoToggle)}`, {
                method: 'PUT',
                body: { done: checkbox.checked }
            });
            await refreshKanbanTicketRelations(ticket.key);
        } catch (error) {
            checkbox.checked = !checkbox.checked;
            actions.toast(error.message || 'Could not update the to-do item.', 'error');
        }
    }

    async function deleteKanbanTicket() {
        const board = actions.boardByKey(context.selectedBoardKey);
        const ticket = kanbanTicketByKey(el('kanban-ticket-form').dataset.ticketKey);
        if (!board || board.provider !== 'decisions' || !ticket) return;
        el('kanban-ticket-dialog').close();
        const linkedChatId = Number(ticket.source_chat_id || 0) || null;
        const decision = await actions.confirmAction({
            title: 'Delete ticket',
            message: `Delete “${ticket.ticket_title}” from this board? Its incoming messages will become available for another snapshot.`,
            confirmLabel: 'Delete',
            danger: true,
            checkbox: linkedChatId ? { label: 'Also delete linked thread', checked: true } : null
        });
        const confirmed = typeof decision === 'object' ? decision.confirmed : decision;
        const deleteThread = Boolean(linkedChatId && typeof decision === 'object' && decision.checked);
        if (!confirmed) {
            openKanbanTicketDialog(ticket.key);
            return;
        }
        try {
            const result = await actions.api(`/tickets/tickets/${Number(ticket.id)}${deleteThread ? '?delete_thread=true' : ''}`, { method: 'DELETE' });
            const deletedThreadId = Number(result?.deleted_thread_id || (deleteThread ? linkedChatId : 0)) || null;
            if (deletedThreadId) {
                context.chats = context.chats.filter((chat) => Number(chat.id) !== deletedThreadId);
                context.archivedChats = context.archivedChats.filter((chat) => Number(chat.id) !== deletedThreadId);
                if (Number(context.currentChat?.id) === deletedThreadId) {
                    context.currentChat = null;
                    context.currentWorkflow = null;
                    context.currentRun = null;
                }
                actions.renderSidebar();
            }
            await actions.loadBoardTickets(board.key, { force: true, remote: false });
            actions.toast(deleteThread ? 'Ticket and linked thread deleted.' : 'Ticket deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the ticket.', 'error');
        }
    }

    async function moveKanbanTicket(ticketKey, laneId, position) {
        const board = actions.boardByKey(context.selectedBoardKey);
        const ticket = kanbanTicketByKey(ticketKey);
        if (!board || !ticket || String(ticket.lane_id) === String(laneId)) return;
        const previousDetail = JSON.parse(JSON.stringify(context.boardDetails[board.key] || {}));
        moveCachedKanbanTicket(board, ticket, laneId, position);
        setKanbanStatus('Moving ticket...');
        try {
            if (board.provider === 'decisions') {
                await actions.api(`/tickets/tickets/${Number(ticket.id)}/move`, {
                    method: 'PUT',
                    body: { lane_id: Number(laneId), position }
                });
            } else {
                await actions.api(
                    `/tickets/external-boards/${encodeURIComponent(board.provider)}/${encodeURIComponent(board.external_id || board.id)}/move-ticket`,
                    {
                        method: 'PUT',
                        body: {
                            ticket_id: String(ticket.id),
                            target_lane_id: String(laneId),
                            position
                        }
                    }
                );
            }
            setKanbanStatus('');
        } catch (error) {
            actions.cacheBoardDetail(board, previousDetail);
            renderKanbanWorkspace();
            setKanbanStatus(error.message || 'Could not move the ticket.', 'error');
            window.setTimeout(() => setKanbanStatus(''), 3500);
        }
    }

    async function createThreadFromKanbanTicket() {
        const ticket = kanbanTicketByKey(el('kanban-ticket-form').dataset.ticketKey);
        const boardKey = context.selectedBoardKey;
        const board = actions.boardByKey(boardKey);
        if (!ticket) return;
        if (ticket.source_chat_id && context.chats.some((chat) => Number(chat.id) === Number(ticket.source_chat_id))) {
            el('kanban-ticket-dialog').close();
            await actions.loadChat(Number(ticket.source_chat_id));
            return;
        }
        const button = el('kanban-ticket-create-thread');
        button.disabled = true;
        button.textContent = 'Preparing...';
        try {
            const draft = await actions.api('/tickets/thread-draft', {
                method: 'POST',
                body: {
                    provider: board.provider,
                    board_id: String(board.external_id || board.local_id || board.id),
                    ticket_id: String(ticket.id),
                    project_id: board.project_id || undefined
                }
            });
            context.attachments = [];
            el('kanban-ticket-dialog').close();
            actions.startBoardDevelopment(boardKey);
            context.attachments.unshift({
                ...ticket,
                label: ticket.ticket_title,
                board_ticket_key: ticket.key,
                sourceLabel: `${actions.providerName(ticket.source)} · ${ticket.lane_name || 'No lane'}`,
                suppress_prompt_context: true,
                hidden_chip: true
            });
            (draft.attachments || []).forEach((item) =>
                context.attachments.push({
                    key: `file:${item.path}`,
                    label: item.name || 'Attachment',
                    text: `Attached local file: ${item.path}\nMIME type: ${item.mime_type || 'application/octet-stream'}\nUse this file as input to the requested work.`,
                    reference: item.path,
                    source: 'file',
                    kind: 'file',
                    mime_type: item.mime_type || 'application/octet-stream',
                    size: Number(item.size || 0),
                    sourceLabel: String(item.mime_type || '').startsWith('image/') ? 'Image' : 'File'
                })
            );
            el('task-prompt').value = draft.prompt || ticket.ticket_title || '';
            actions.resizePrompt();
            actions.syncTicketSelect();
            actions.renderAttachments();
            el('task-prompt').focus();
            if ((draft.warnings || []).length) actions.toast(draft.warnings[0], 'error');
        } catch (error) {
            button.disabled = false;
            button.textContent = 'Create thread';
            actions.toast(error.message || 'Could not prepare the ticket thread.', 'error');
        }
    }

    function bindEvents() {
        el('development-kanban-new-ticket').addEventListener('click', () => openKanbanTicketDialog(''));
        el('development-kanban-refresh').addEventListener('click', forceRefreshKanbanBoard);
        el('development-kanban-view-board').addEventListener('click', () => setKanbanViewMode('board'));
        el('development-kanban-view-list').addEventListener('click', () => setKanbanViewMode('list'));
        el('development-kanban-edit-board').addEventListener('click', () => actions.openBoardLinkDialog(context.selectedBoardKey));
        el('development-kanban-more').addEventListener('click', (event) => {
            event.stopPropagation();
            actions.openBoardContextMenu(event, context.selectedBoardKey);
        });
        el('kanban-workspace').addEventListener('contextmenu', (event) => {
            if (event.target.closest('[data-kanban-ticket]')) return;
            event.preventDefault();
            actions.openBoardContextMenu(event, context.selectedBoardKey);
        });
        el('ticket-context-menu').addEventListener('click', runTicketContextMenuAction);
        el('kanban-ticket-form').addEventListener('submit', saveKanbanTicket);
        el('kanban-ticket-details-tab').addEventListener('click', () => setKanbanTicketTab('details'));
        el('kanban-ticket-notes-tab').addEventListener('click', () => setKanbanTicketTab('notes'));
        el('kanban-ticket-attachments-tab').addEventListener('click', () => setKanbanTicketTab('attachments'));
        el('kanban-ticket-todos-tab').addEventListener('click', () => setKanbanTicketTab('todos'));
        el('kanban-ticket-create-thread').addEventListener('click', createThreadFromKanbanTicket);
        el('kanban-ticket-run-workflow').addEventListener('click', () => runKanbanTicketAction(currentKanbanDialogTicket(), 'workflow'));
        el('kanban-ticket-run-agent').addEventListener('click', () => runKanbanTicketAction(currentKanbanDialogTicket(), 'agent'));
        el('kanban-ticket-delete').addEventListener('click', deleteKanbanTicket);
        el('kanban-ticket-add-link').addEventListener('click', addKanbanTicketLink);
        el('kanban-ticket-upload').addEventListener('click', () => el('kanban-ticket-file-input').click());
        el('kanban-ticket-file-input').addEventListener('change', (event) => uploadKanbanTicketFiles(Array.from(event.target.files || [])));
        el('kanban-ticket-add-todo').addEventListener('click', addKanbanTicketTodo);
        el('kanban-ticket-links').addEventListener('click', handleKanbanTicketRelationClick);
        el('kanban-ticket-attachments').addEventListener('click', handleKanbanTicketRelationClick);
        el('kanban-ticket-todos').addEventListener('click', handleKanbanTicketRelationClick);
        el('kanban-ticket-todos').addEventListener('change', handleKanbanTicketTodoChange);
    }
    return {
        bindEvents,
        closeTicketContextMenu,
        forceRefreshKanbanBoard,
        getKanbanViewMode,
        openKanbanTicketDialog,
        renderKanbanWorkspace,
        setKanbanViewMode
    };
}
