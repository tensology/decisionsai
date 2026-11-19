import { createPlanConversation } from './conversation.js?v=20261120-wfplan';
import { scanHint, watchPlanScan } from './scan-watch.js?v=20261112-scan-refresh';
import { createLoadGuard, isAbort } from './load-guard.js?v=20261102-verify';

export function createPlanning(host, root = document.getElementById('plan-root')) {
    const state = { boards: [], projects: [], workspaces: [], workspace: null, item: null, navigation: 0, view: 'home', details: new Map() };
    const loads = createLoadGuard();
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
        // This page keeps that set, including boards with no project folder, so they can be set up here.
        const boards = state.boards.filter((board) => board && board.key);
        return `<div class="plan-board-grid" role="list">${
            boards
                .map((board) => {
                    const workspace = workspaceFor(board);
                    const project = projectFor(board);
                    const count = Number(workspace?.item_count || 0);
                    const actionLabel = !project ? 'Set up' : count ? 'Edit' : 'Build plan';
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

    function retryHtml(message, action, label = 'Retry plans') {
        return `<div class="plan-empty error" role="alert"><strong>${escapeHtml(message)}</strong><button type="button" class="plan-retry" data-plan-retry="${action}">${label}</button></div>`;
    }

    async function openHome(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        conversation.leave();
        const navigation = ++state.navigation;
        state.view = 'home';
        state.workspace = null;
        host.setPlanCrumbs?.();
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
        const project = projectFor(board);
        if (!project) {
            renderUnlinked(board);
            return false;
        }
        // Reopening a board plan shows the cached plan at once and refreshes it in the
        // background; only the first open waits on the (project-scanning) POST.
        const load = loads.begin();
        const previous = state.details.get(board.key);
        const cached = Number(previous?.project_id) === Number(project.id) ? previous : null;
        if (cached) conversation.open(cached);
        else root.innerHTML = '<div class="plan-loading" role="status" aria-label="Loading plan"><span></span><span></span><span></span></div>';
        try {
            const detail = await api('/workflows/studio/plan-workspaces', {
                method: 'POST',
                signal: load.signal,
                body: {
                    board_key: board.key,
                    board_provider: board.provider,
                    board_name: board.name || 'Untitled board',
                    project_id: project.id
                }
            });
            if (navigation !== state.navigation || !load.isCurrent()) return false;
            state.details.set(board.key, detail);
            if (cached) conversation.refreshWorkspace(detail);
            else conversation.open(detail);
            conversation.setScanHint(scanHint(detail.scan));
            if (detail.scan?.state === 'scanning') {
                watchPlanScan({
                    api,
                    workspaceId: detail.id,
                    isCurrent: () => navigation === state.navigation && load.isCurrent(),
                    onSettled: (status, workspace) => {
                        if (workspace) {
                            state.details.set(board.key, workspace);
                            conversation.refreshWorkspace(workspace);
                        }
                        conversation.setScanHint(scanHint(status));
                    }
                });
            }
            return true;
        } catch (error) {
            if (isAbort(error) || !load.isCurrent()) return false;
            if (error.status === 409 && navigation === state.navigation) {
                conversation.leave();
                state.details.delete(board.key);
                root.innerHTML = retryHtml(error.message, 'home', 'Back to Plans');
                root.querySelector('[data-plan-retry="home"]')?.addEventListener('click', () => openHome(state.boards, state.projects));
                return false;
            }
            if (cached) return true;
            if (navigation === state.navigation) {
                root.innerHTML = retryHtml(error.message || 'Could not open this plan.', 'board');
                root.querySelector('[data-plan-retry="board"]')?.addEventListener('click', () => openBoard(board, state.projects));
            }
            return false;
        }
    }

    // A board with no project folder (e.g. My Board) still gets a Plan screen that says
    // what is missing and links the folder, instead of bouncing back with a toast.
    function renderUnlinked(board) {
        state.workspace = null;
        state.item = null;
        host.setPlanCrumbs?.();
        const name = escapeHtml(board?.name || 'This board');
        root.innerHTML = `<div class="plan-empty plan-unlinked" data-plan-unlinked="${escapeHtml(board?.key || '')}">
            <strong>${name} has no project folder yet</strong>
            <p class="plan-empty-hint">A plan is built from a project folder: its pages, data and flows. Link a folder to this board and its plan opens here.</p>
            <div class="plan-empty-actions">
                <button type="button" class="plan-retry primary" data-plan-link-folder>Link a project folder</button>
                <button type="button" class="plan-retry" data-plan-retry="home">Back to Plans</button>
            </div>
        </div>`;
        root.querySelector('[data-plan-link-folder]')?.addEventListener('click', () => host.openBoardLink?.(board.key));
        root.querySelector('[data-plan-retry="home"]')?.addEventListener('click', () => host.openHome());
    }

    // Unsaved page-designer, document or ERD edits: ask before leaving Plan. The draft stays in
    // memory for this tab, but a reload or close loses it, so leaving must be a choice.
    async function beforeLeave() {
        if (!conversation.hasUnsavedEdits?.()) return true;
        return window.confirm('You have unsaved plan edits. Leave Plan without saving? The draft stays until you reload or close Decisions.');
    }

    function syncCatalog(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (state.view === 'home' && !root.querySelector('.plan-empty.error')) openHome(state.boards, state.projects);
    }
    function leave() {
        ++state.navigation;
        loads.cancel(); // abort the in-flight plan POST; scan polling stops via isCurrent
        conversation.leave();
    }
    return { openHome, openBoard, currentView: () => state.view, syncCatalog, beforeLeave, leave,
        hasUnsavedChanges: () => conversation.hasDraft() };
}
