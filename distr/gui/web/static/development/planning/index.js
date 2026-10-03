import { createPlanConversation } from './conversation.js?v=20261003-rebuild-sitemap';

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
        return `<div class="plan-board-grid">${
            boards
                .map((board) => {
                    const workspace = workspaceFor(board);
                    const project = projectFor(board);
                    const count = Number(workspace?.item_count || 0);
                    const actionLabel = count ? 'Edit' : 'Build plan';
                    const folder = project?.folder_location || 'No project folder';
                    const boardName = board.name || 'Ticket board';
                    const key = escapeHtml(board.key);
                    return `<article class="plan-board-card" data-plan-board="${key}">
                    <div class="plan-board-open">
                        <span class="plan-board-copy"><strong>${escapeHtml(boardName)}</strong><small>${escapeHtml(folder)}</small></span>
                        <span class="plan-board-state"><button type="button" class="plan-board-action" data-plan-board-open="${key}">${actionLabel}</button></span>
                    </div>
                </article>`;
                })
                .join('') || '<div class="plan-empty plan-home-empty">No boards are ready for planning.</div>'
        }</div>`;
    }

    function homeHtml() {
        return `<div class="plan-home">
            <div id="plan-home-results">${homeResultsHtml()}</div>
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
