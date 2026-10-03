import { createPlanConversation } from './conversation.js?v=20261003-page-stencils-3';

export function createPlanning(host, root = document.getElementById('plan-root')) {
    const state = { boards: [], projects: [], workspaces: [], workspace: null, item: null, search: '', showUnlinked: false, navigation: 0, view: 'home' };
    const conversation = createPlanConversation(host, root);
    const escapeHtml = host.escapeHtml;
    const projectFor = (board) => state.projects.find((project) => Number(project.id) === Number(board?.project_id || board?.default_project_id)) || null;
    const workspaceFor = (board) => state.workspaces.find((workspace) => workspace.board_key === board.key) || null;
    const providerName = (board) => board?.provider === 'jira' ? 'Jira' : board?.provider === 'trello' ? 'Trello' : 'Local';
    const api = (path, options) => host.api(path, options);
    async function loadWorkspaces() {
        state.workspaces = (await api('/workflows/studio/plan-workspaces')).items || [];
    }

    function homeResultsHtml() {
        const query = state.search.trim().toLowerCase();
        const boards = state.boards.filter(
            (board) =>
                !query ||
                String(board.name || '')
                    .toLowerCase()
                    .includes(query)
        );
        const workingBoards = boards.filter((board) => Boolean(projectFor(board)));
        const unlinkedBoards = boards.filter((board) => !projectFor(board));
        const emptyMessage = query ? 'No linked boards match this search.' : 'No linked boards are ready for planning.';
        return `<div class="plan-board-grid">${
            workingBoards
                .map((board) => {
                    const workspace = workspaceFor(board);
                    const project = projectFor(board);
                    const count = Number(workspace?.item_count || 0);
                    const stateLabel = count
                        ? `${count} section${count === 1 ? '' : 's'}`
                        : 'No plan yet';
                    return `<article class="plan-board-card" data-plan-board="${escapeHtml(board.key)}">
                    <button type="button" class="plan-board-open" data-plan-board-open="${escapeHtml(board.key)}">
                        <span class="plan-board-copy"><strong>${escapeHtml(board.name || 'Untitled board')}</strong><small>${escapeHtml(project?.folder_location || 'No project folder')}</small></span>
                        <span class="plan-board-state">${stateLabel}<i aria-hidden="true">›</i></span>
                    </button>
                </article>`;
                })
                .join('') || `<div class="plan-empty plan-home-empty">${emptyMessage}</div>`
        }</div>
            ${
                unlinkedBoards.length
                    ? `<details class="plan-unlinked"${state.showUnlinked || query ? ' open' : ''}>
                <summary><span>Unlinked boards</span><small>${unlinkedBoards.length}</small></summary>
                <div class="plan-unlinked-list">
                    <p>Link a project to make planning available.</p>
                    ${unlinkedBoards.map((board) => `<div class="plan-unlinked-row" data-plan-unlinked="${escapeHtml(board.key)}"><strong>${escapeHtml(board.name || 'Untitled board')}</strong><small>${providerName(board)}</small></div>`).join('')}
                </div>
            </details>`
                    : ''
            }`;
    }

    function homeHtml() {
        return `<div class="plan-home">
            <header class="plan-home-header"><div><h1>Plans</h1><p>Open a linked board to load or build its plan under the project <code>planning/</code> folder.</p></div></header>
            <label class="plan-search"><span aria-hidden="true">⌕</span><input id="plan-search" type="search" value="${escapeHtml(state.search)}" placeholder="Search boards" aria-label="Search plan boards"></label>
            <div id="plan-home-results">${homeResultsHtml()}</div>
        </div>`;
    }

    function bindHomeResults() {
        root.querySelector('.plan-unlinked')?.addEventListener('toggle', (event) => {
            state.showUnlinked = event.target.open;
        });
        root.querySelectorAll('[data-plan-board-open]').forEach((button) =>
            button.addEventListener('click', () => {
                const board = state.boards.find((row) => row.key === button.dataset.planBoardOpen);
                if (board) host.openBoardPlan(board.key);
            })
        );
    }

    function bindHome() {
        root.querySelector('#plan-search')?.addEventListener('input', (event) => {
            state.search = event.target.value;
            if (state.search.trim()) state.showUnlinked = true;
            root.querySelector('#plan-home-results').innerHTML = homeResultsHtml();
            bindHomeResults();
        });
        bindHomeResults();
    }

    async function openHome(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        conversation.leave();
        const navigation = ++state.navigation;
        state.view = 'home';
        state.workspace = null;
        state.item = null;
        try {
            await loadWorkspaces();
        } catch (error) {
            if (navigation !== state.navigation) return;
            root.innerHTML = `<div class="plan-empty error">${escapeHtml(error.message || 'Could not load plans.')}</div>`;
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
        root.innerHTML = '<div class="plan-empty" role="status">Loading plan...</div>';
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
            if (navigation === state.navigation)
                root.innerHTML = `<div class="plan-empty error">${escapeHtml(error.message || 'Could not open this plan.')}</div>`;
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
