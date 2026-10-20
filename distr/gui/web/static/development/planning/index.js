import { createPlanConversation } from './conversation.js?v=20261003-sitemap-groups';

export function createPlanning(host, root = document.getElementById('plan-root')) {
    const state = { boards: [], projects: [], workspaces: [], workspace: null, item: null, navigation: 0, view: 'home' };
    const conversation = createPlanConversation(host, root);
    const escapeHtml = host.escapeHtml;
    const projectFor = (board) => state.projects.find((project) => Number(project.id) === Number(board?.project_id || board?.default_project_id)) || null;
    const workspaceFor = (board) => state.workspaces.find((workspace) => workspace.board_key === board.key) || null;
    const api = (path, options) => host.api(path, options);
    async function loadWorkspaces() {
        state.workspaces = (await api('/workflows/studio/plan-workspaces')).items || [];
    }

    function homeResultsHtml() {
        // Development sidebar (sidebar/index.js renderSidebar) lists every catalog board.
        // This page keeps that set and drops boards with no valid project.
        const boards = state.boards.filter((board) => {
            if (!projectFor(board)) return false;
            return true;
        });
        return `<div class="plan-board-grid" role="list">${
            boards
                .map((board) => {
                    const workspace = workspaceFor(board);
                    const project = projectFor(board);
                    const count = Number(workspace?.item_count || 0);
                    const actionLabel = count ? 'Edit' : 'Build plan';
                    const folder = project?.folder_location || 'No project folder';
                    const boardName = board.name || 'Ticket board';
                    const key = escapeHtml(board.key);
                    const searchText = escapeHtml(`${boardName} ${folder}`.toLowerCase());
                    return `<article class="plan-board-card" role="listitem" data-plan-board="${key}" data-plan-search="${searchText}">
                    <div class="plan-board-open">
                        <span class="plan-board-copy"><strong>${escapeHtml(boardName)}</strong><small>${escapeHtml(folder)}</small></span>
                        <span class="plan-board-state"><button type="button" class="plan-board-action" data-plan-board-open="${key}">${actionLabel}</button></span>
                    </div>
                </article>`;
                })
                .join('') || '<div class="plan-home-empty">No boards are ready for planning.</div>'
        }</div>`;
    }

    function homeHtml() {
        return `<div class="plan-home">
            <header class="plan-home-header">
                <h1>Plans</h1>
                <label class="plan-search"><span class="sr-only">Search plans</span><input type="search" id="plan-search" placeholder="Search plans" autocomplete="off"></label>
            </header>
            <div id="plan-home-results">${homeResultsHtml()}</div>
            <div class="plan-home-empty" id="plan-search-empty" hidden>No plans match this search.</div>
        </div>`;
    }

    function openListedBoard(key) {
        const board = state.boards.find((row) => row.key === key);
        if (board) host.openBoardPlan(board.key);
    }

    function bindHomeResults() {
        root.querySelectorAll('[data-plan-board]').forEach((card) =>
            card.addEventListener('click', () => openListedBoard(card.dataset.planBoard))
        );
        root.querySelectorAll('[data-plan-board-open]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                openListedBoard(button.dataset.planBoardOpen);
            })
        );
    }

    function bindHome() {
        bindHomeResults();
        const search = root.querySelector('#plan-search');
        const empty = root.querySelector('#plan-search-empty');
        search?.addEventListener('input', () => {
            const query = search.value.trim().toLowerCase();
            let visible = 0;
            root.querySelectorAll('[data-plan-search]').forEach((card) => {
                const matches = !query || card.dataset.planSearch.includes(query);
                card.hidden = !matches;
                if (matches) visible += 1;
            });
            if (empty) empty.hidden = visible !== 0;
        });
    }

    function retryHtml(message, action) {
        return `<div class="plan-empty error" role="alert"><strong>${escapeHtml(message)}</strong><button type="button" class="plan-retry" data-plan-retry="${action}">Retry plans</button></div>`;
    }

    async function openHome(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        conversation.leave();
        const navigation = ++state.navigation;
        state.view = 'home';
        state.workspace = null;
        state.item = null;
        root.innerHTML = '<div class="plan-loading" role="status" aria-label="Loading plans"><span></span><span></span><span></span></div>';
        try {
            await loadWorkspaces();
        } catch (error) {
            if (navigation !== state.navigation) return;
            root.innerHTML = retryHtml(error.message || 'Could not load plans.', 'home');
            root.querySelector('[data-plan-retry="home"]')?.addEventListener('click', () => openHome(state.boards, state.projects));
            return;
        }
        if (navigation !== state.navigation) return;
        root.innerHTML = homeHtml();
        bindHome();
    }


    async function openBoard(board, projects) {
        conversation.leave();
        const navigation = ++state.navigation;
        state.view = 'board';
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (!projectFor(board)) {
            host.toast('Link this board to a project before opening Plan.', 'error');
            host.openHome();
            return false;
        }
        root.innerHTML = '<div class="plan-loading" role="status" aria-label="Loading plan"><span></span><span></span><span></span></div>';
        try {
            const detail = await api('/workflows/studio/plan-workspaces', {
                method: 'POST',
                body: {
                    board_key: board.key,
                    board_provider: board.provider,
                    board_name: board.name || 'Untitled board',
                    project_id: board.project_id || board.default_project_id || null
                }
            });
            if (navigation !== state.navigation) return false;
            conversation.open(detail);
            return true;
        } catch (error) {
            if (navigation === state.navigation) {
                root.innerHTML = retryHtml(error.message || 'Could not open this plan.', 'board');
                root.querySelector('[data-plan-retry="board"]')?.addEventListener('click', () => openBoard(board, state.projects));
            }
            return false;
        }
    }

    function syncCatalog(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (state.view === 'home' && !root.querySelector('.plan-empty.error')) openHome(state.boards, state.projects);
    }
    function leave() {
        ++state.navigation;
        conversation.leave();
    }
    return { openHome, openBoard, syncCatalog, beforeLeave: async () => true, leave,
        hasUnsavedChanges: () => conversation.hasDraft() };
}
