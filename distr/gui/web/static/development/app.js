import { createReports } from './reports/index.js';
import { createThreadLiveUpdates } from './threads/live.js';
import { createThreadSession } from './threads/session.js';
import { createCatalogLoader } from './shell/catalog.js';
import { installNavigationGuard } from './shell/navigation-guard.js';
import { catalogModels, providerLabel } from './shared/models.js';
import { createPlanning } from './planning/index.js';

import { createSidebar } from './sidebar/index.js';

import { createAutomations } from './automations/index.js';

import { createIncoming } from './incoming/index.js';

import { createWorkflows } from './workflows/index.js';

import { createBoards } from './boards/index.js';

import { createTerminals } from './terminals/index.js';

import { createThreadsTranscript } from './threads/transcript/index.js?v=20260906-1';

import { createThreadsComposer } from './threads/composer/index.js';

import { createThreadsInspector } from './threads/inspector/index.js?v=20260906-1';

// Composition root: shared catalog and thread context, feature interfaces, navigation.

const state = {
    projects: [],
    boards: [],
    selectedBoardKey: '',
    boardScopedDraft: false,
    boardTickets: {},
    boardDetails: {},
    boardTicketLoads: {},
    chats: [],
    archivedChats: [],
    workflows: [],
    runs: [],
    inbox: [],
    automations: [],
    artifacts: [],
    plans: [],
    doctor: null,
    activeArtifactIndex: null,
    currentChat: null,
    currentWorkflow: null,
    currentRun: null,
    chatLoadToken: 0,
    threadTime: null,
    controlState: { channels: {}, interactions: [], commands: [], controls: {} },
    currentTab: 'run',
    selectedProjectId: null,
    draft: {
        project_id: null,
        title: '',
        route_mode: 'auto',
        provider: '',
        model_name: '',
        execution_profile: 'code',
        autonomy_level: 'full',
        permission_mode: 'standard',
        reasoning_effort: 'medium',
        service_tier: 'standard',
        ticket_id: null,
        workflow_id: null
    },
    attachments: [],
    contextSource: 'project',
    projectContext: null,
    providers: [],
    modelCatalogs: {},
    skills: [],
    modelMenuPane: '',
    polling: null,
    busy: false,
    editingCommandId: null,
    workspaceMode: 'chat',
    routingAssessment: null,
    elapsedTimer: null,
    projectTickets: {},
    projectTicketLoads: {},
    ready: false,
    routeLoading: false,
    changeReview: null,
    changeReviewLoading: false,
    selectedChangePath: '',
    usePlaywright: false
};

const el = (id) => document.getElementById(id);

const token = document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';

const developmentModePaths = {
    plan: '/development/plan/',
    incoming: '/development/incoming/',
    automations: '/development/automations/',
    automation_rules: '/development/automation-rules/',
    workflows: '/development/workflows/',
    terminals_home: '/development/terminals/',
    reports: '/development/reports/'
};

function developmentBoardPath(boardKey, view) {
    const [provider, ...identityParts] = String(boardKey || '').split(':');
    const identity = identityParts.join(':');
    if (!provider || !identity) return '/development/';
    return `/development/boards/${encodeURIComponent(provider)}/${encodeURIComponent(identity)}/${view ? `${encodeURIComponent(view)}/` : ''}`;
}

function developmentRoute() {
    const url = new URL(window.location.href);
    const query = url.searchParams;
    const ticketId = Number(query.get('ticket_id') || 0) || null;
    const legacyBoard = String(query.get('board') || '');
    if (query.get('chat')) {
        const chatId = Number(query.get('chat') || 0);
        return { kind: 'thread', chatId, path: `/development/threads/${chatId}/` };
    }
    if (query.get('new') === '1') return { kind: 'new', path: '/development/new/' };
    if (legacyBoard) {
        const legacyView = query.get('edit_board') === '1' ? 'settings' : String(query.get('view') || '');
        return {
            kind: 'board',
            boardKey: legacyBoard,
            view: legacyView,
            path: developmentBoardPath(legacyBoard, legacyView)
        };
    }
    const legacyView = String(query.get('view') || '');
    if (legacyView) {
        const mode = legacyView === 'automations' ? 'automations' : legacyView === 'automation_rules' ? 'automation_rules' : legacyView;
        return {
            kind: 'workspace',
            mode,
            path: developmentModePaths[mode] || '/development/'
        };
    }

    const parts = url.pathname
        .split('/')
        .filter(Boolean)
        .map((part) => decodeURIComponent(part));
    if (parts[0] !== 'development' || parts.length === 1) return { kind: 'home', path: '/development/' };
    if (parts[1] === 'new') return { kind: 'new', path: '/development/new/' };
    if (parts[1] === 'threads' && Number(parts[2])) {
        const chatId = Number(parts[2]);
        return { kind: 'thread', chatId, path: `/development/threads/${chatId}/` };
    }
    if (parts[1] === 'terminals' && Number(parts[2])) {
        const projectId = Number(parts[2]);
        return {
            kind: 'project_terminals',
            projectId,
            path: `/development/terminals/${projectId}/`
        };
    }
    if (parts[1] === 'boards' && parts[2] && parts[3]) {
        const boardKey = `${parts[2]}:${parts[3]}`;
        const view = parts[4] || '';
        const boardPath = developmentBoardPath(boardKey, view);
        return {
            kind: 'board',
            boardKey,
            view,
            ticketId,
            path: ticketId ? `${boardPath}?ticket_id=${ticketId}` : boardPath
        };
    }
    const mode =
        parts[1] === 'scheduled-actions' || parts[1] === 'automations'
            ? 'automations'
            : parts[1] === 'automation-rules'
              ? 'automation_rules'
              : parts[1] === 'terminals'
                ? 'terminals_home'
                : parts[1];
    return developmentModePaths[mode] ? { kind: 'workspace', mode, path: developmentModePaths[mode] } : { kind: 'home', path: '/development/' };
}

let acceptedLocation = window.location.pathname + window.location.search + window.location.hash;
function setDevelopmentLocation(path, options) {
    const target = new URL(path, window.location.origin);
    const location = target.pathname + target.search + target.hash;
    acceptedLocation = location;
    if (`${window.location.pathname}${window.location.search}${window.location.hash}` === location) return;
    window.history[options?.replace ? 'replaceState' : 'pushState']({}, '', location);
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function safeArtifactUri(value) {
    const uri = String(value || '').trim();
    if (/^https?:\/\//i.test(uri) || /^\/(?:api|static)\//.test(uri) || /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(uri)) return uri;
    return '';
}

function apiErrorText(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(apiErrorText).filter(Boolean).join('; ');
    if (value && typeof value === 'object') {
        const location = Array.isArray(value.loc) ? value.loc.slice(1).join('.') : '';
        const message = apiErrorText(value.msg || value.message || value.detail);
        if (message) return location ? `${location}: ${message}` : message;
        try {
            return JSON.stringify(value);
        } catch (_) {
            return 'Request failed.';
        }
    }
    return value == null ? '' : String(value);
}

async function api(path, options) {
    const config = Object.assign({}, options || {});
    config.headers = Object.assign({}, config.headers || {});
    if (token) config.headers['X-DecisionsAI-Internal-Token'] = token;
    if (config.body && typeof config.body !== 'string' && !(config.body instanceof FormData)) {
        config.headers['Content-Type'] = 'application/json';
        config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path.startsWith('/api') ? path : `/api${path}`, config);
    const text = await response.text();
    let data = {};
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (_) {
            data = { detail: text };
        }
    }
    if (!response.ok) {
        const error = new Error(apiErrorText(data.detail) || apiErrorText(data.message) || `Request failed (${response.status})`);
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

async function terminalActionApi(path, options, timeoutMs) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs || 8000);
    try {
        return await api(path, Object.assign({}, options || {}, { signal: controller.signal }));
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw new Error('The terminal action timed out. Its status has been refreshed.');
        }
        throw error;
    } finally {
        window.clearTimeout(timeout);
    }
}

function toast(message, type) {
    const node = el('studio-toast');
    node.textContent = message;
    node.className = `studio-toast${type === 'error' ? ' error' : ''}`;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.add('hidden'), 3200);
}

async function confirmAction(options) {
    if (!window.DecisionsAPI || typeof window.DecisionsAPI.confirm !== 'function') {
        toast('Confirmation controls are unavailable. Nothing was changed.', 'error');
        return false;
    }
    return window.DecisionsAPI.confirm(options);
}

function projectById(projectId) {
    const id = Number(projectId || 0);
    return state.projects.find((project) => Number(project.id) === id) || null;
}

function workflowForChat(chatId) {
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === Number(chatId));
    const metadataWorkflowId = Number(chat?.development_workflow_id || 0);
    if (metadataWorkflowId) {
        return state.workflows.find((workflow) => Number(workflow.id) === metadataWorkflowId) || null;
    }
    const matches = state.workflows.filter((workflow) => Number(workflow.chat_id) === Number(chatId));
    return matches.sort((a, b) => Number(b.id) - Number(a.id))[0] || null;
}

function directExecutionForChat(chat) {
    const execution = chat?.direct ? chat : chat?.development?.execution || chat?.development_execution || null;
    if (!execution || typeof execution !== 'object' || (!execution.job_id && !execution.execution_session_id && !execution.status)) return null;
    return {
        ...execution,
        id: execution.execution_session_id || execution.id || execution.job_id,
        direct: true,
        __clientReceivedAt: Date.now()
    };
}

function runForWorkflow(workflowId) {
    return state.runs.find((run) => Number(run.workflow_id) === Number(workflowId)) || null;
}

function workflowRunId(run) {
    if (!run || run.direct) return null;
    const value = run.run_id ?? run.id;
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function isActiveAgentRun(run) {
    return Boolean(run && ['initializing', 'queued', 'running', 'waiting', 'paused'].includes(String(run.status || '').toLowerCase()));
}

function isProcessingAgentRun(run) {
    return Boolean(run && ['initializing', 'queued', 'running'].includes(String(run.status || '').toLowerCase()));
}

function threadIsProcessing(chat) {
    const isCurrent = Number(state.currentChat?.id) === Number(chat?.id);
    if (isCurrent && state.busy) return true;
    if (isCurrent && state.currentChat?.active_turn?.active) return true;
    if (isCurrent && state.currentRun && !isProcessingAgentRun(state.currentRun)) return false;
    if (isProcessingAgentRun(directExecutionForChat(chat))) return true;
    const workflow = workflowForChat(chat?.id);
    return Boolean(workflow && isProcessingAgentRun(runForWorkflow(workflow.id)));
}

function threadRowContentHtml(chat, pinned = false) {
    const title = escapeHtml(chat?.title || 'New thread');
    const pin = pinned ? '<span class="pin-mark" aria-label="Pinned">●</span>' : '';
    const spinner = threadIsProcessing(chat)
        ? '<span class="task-run-state" role="status" aria-label="Working on this thread" title="Working on this thread"></span>'
        : '';
    return `<span class="task-title">${pin}${title}</span>${spinner}`;
}

function statusLabel(status) {
    const value = String(status || 'ready').toLowerCase();
    const labels = {
        initializing: 'Working',
        queued: 'Queued',
        running: 'Implementing',
        waiting: 'Waiting for input',
        paused: 'Paused',
        failed: 'Needs attention',
        completed: 'Complete',
        cancelled: 'Stopped',
        ready: 'Ready'
    };
    return labels[value] || value.replace(/_/g, ' ').replace(/^./, (char) => char.toUpperCase());
}

function setTaskState(status) {
    const node = el('task-state');
    const value = String(status || 'ready').toLowerCase();
    node.className = `task-state ${value}`;
    node.querySelector('b').textContent = statusLabel(value);
    const stoppable = isActiveAgentRun(state.currentRun);
    el('stop-run-button').classList.toggle('hidden', !stoppable);
    updateElapsedTime();
}

function updateElapsedTime() {
    const node = el('run-elapsed');
    if (!node) return;
    node.textContent = state.threadTime ? threadTimeLabel(state.threadTime, true) : '00:00:00';
    const toggle = el('composer-time-toggle');
    if (!toggle) return;
    const running = Boolean(state.threadTime && !state.threadTime.paused);
    toggle.disabled = !state.currentChat;
    toggle.querySelector('span').textContent = running ? '■' : '▶';
    toggle.setAttribute('aria-label', running ? 'Pause time tracking' : 'Start time tracking');
    toggle.title = running ? 'Pause time tracking' : 'Start time tracking';
    toggle.setAttribute('aria-pressed', running ? 'true' : 'false');
}

function formatSeconds(value, alwaysHours) {
    const seconds = Math.max(0, Math.floor(Number(value || 0)));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    if (alwaysHours || hours) return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
    return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function threadTimeSeconds(time) {
    let seconds = Number(time?.seconds ?? time?.accumulated_seconds ?? 0);
    if (time && !time.paused && time.__clientReceivedAt) {
        seconds += Math.max(0, Math.floor((Date.now() - time.__clientReceivedAt) / 1000));
    }
    return seconds;
}

function timerSnapshot(time) {
    return time ? { ...time, __clientReceivedAt: Date.now() } : null;
}

function threadTimeLabel(time, alwaysHours) {
    return formatSeconds(threadTimeSeconds(time), alwaysHours);
}

function elapsedLabel(run) {
    const serverSeconds = Number(run?.elapsed_seconds);
    const receivedAt = run.__clientReceivedAt || (run.__clientReceivedAt = Date.now());
    const seconds = Number.isFinite(serverSeconds) ? Math.max(0, Math.floor(serverSeconds + (Date.now() - receivedAt) / 1000)) : 0;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    return hours
        ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function syncHeader() {
    const chat = state.currentChat;
    const board = boardByKey(state.selectedBoardKey) || boardForProject(chat?.project_id);
    const runStatus = String(state.currentRun?.status || 'ready').toLowerCase();
    el('header-project').textContent = board?.name || 'Development';
    el('header-task').textContent = chat?.title || state.draft.title || 'No thread selected';
    setTaskState(['completed', 'cancelled'].includes(runStatus) ? 'ready' : runStatus);
    el('thread-more-button').classList.toggle('hidden', !chat);
    el('task-state').classList.toggle('hidden', !chat);
    syncProjectSelect();
}

function projectOptions(selected) {
    return (
        '<option value="">No board</option>' +
        state.projects
            .map(
                (project) =>
                    `<option value="${Number(project.id)}"${Number(selected) === Number(project.id) ? ' selected' : ''}>${escapeHtml(project.name)}</option>`
            )
            .join('')
    );
}

function syncProjectSelect() {
    if (!state.selectedBoardKey && state.currentChat?.project_id) state.selectedBoardKey = boardForProject(state.currentChat.project_id)?.key || '';
    renderBoardPicker();
    if (state.selectedBoardKey) loadBoardTickets(state.selectedBoardKey);
    syncTicketSelect();
    renderComposerWorkflowPicker();
    syncPermissionSelect();
}

function ticketLabel(item) {
    const title = item.ticket_title || item.text || item.response_text || `Ticket #${item.ticket_id || item.id}`;
    const lane = item.lane || item.lane_name || item.column_name || item.status || '';
    return lane ? `${title} · ${lane}` : title;
}

function ticketItemsForProject(projectId) {
    if (projectId && Array.isArray(state.projectTickets[Number(projectId)])) return state.projectTickets[Number(projectId)];
    return state.inbox.filter((item) => {
        if (!(item.ticket_id || /jira|trello|ticket|board/i.test(String(item.source || '')))) return false;
        return !item.project_id || !projectId || Number(item.project_id) === Number(projectId);
    });
}

async function loadProjectTickets(projectId) {
    const id = Number(projectId || 0);
    if (!id || state.projectTicketLoads[id]) return;
    const project = projectById(id);
    if (!project?.kanban_board_id) return;
    state.projectTicketLoads[id] = true;
    try {
        const board = await api(`/tickets/boards/${Number(project.kanban_board_id)}`);
        state.projectTickets[id] = (board.lanes || []).flatMap((lane) =>
            (lane.tickets || [])
                .filter((ticket) => !ticket.linked_project_id || Number(ticket.linked_project_id) === id)
                .map((ticket) => ({
                    id: ticket.id,
                    ticket_id: ticket.id,
                    ticket_title: ticket.title,
                    text: ticket.description || ticket.title || '',
                    project_id: ticket.linked_project_id || id,
                    lane_name: lane.name || '',
                    source: board.source || 'Ticket board'
                }))
        );
        syncTicketSelect();
    } catch (_) {
        state.projectTickets[id] = [];
    }
}

function normalizeBoards(localBoards, externalBoards) {
    const rows = new Map();
    localBoards.forEach((board) => {
        const provider = String(board.source || 'decisions').toLowerCase();
        const external = ['jira', 'trello'].includes(provider) && board.external_board_id;
        const key = external ? `${provider}:${board.external_board_id}` : `decisions:${board.id}`;
        rows.set(key, {
            ...board,
            key,
            aliases: external ? [`decisions:${board.id}`] : [],
            provider: external ? provider : 'decisions',
            local_id: board.id,
            project_id: board.default_project_id || null
        });
    });
    ['jira', 'trello'].forEach((provider) =>
        (externalBoards[provider] || []).forEach((board) => {
            const key = `${provider}:${board.id}`;
            rows.set(key, {
                ...(rows.get(key) || {}),
                ...board,
                key,
                provider,
                external_id: String(board.id),
                project_id: board.default_project_id || rows.get(key)?.project_id || null
            });
        })
    );
    // A project can own several distinct boards. Merge only identical provider IDs.
    return Array.from(rows.values());
}

function boardByKey(key) {
    return state.boards.find((board) => board.key === key || board.aliases?.includes(key)) || null;
}

function boardForProject(projectId) {
    const id = Number(projectId || 0);
    if (!id) return null;
    return state.boards.find((board) => Number(board.project_id || board.default_project_id) === id) || null;
}

function providerName(provider) {
    return provider === 'jira' ? 'Jira' : provider === 'trello' ? 'Trello' : 'Local';
}

function systemFileManagerLabel() {
    const platform = String(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || '').toLowerCase();
    if (/mac|iphone|ipad|ipod/.test(platform)) return 'Finder';
    if (/win/.test(platform)) return 'Explorer';
    return 'File Manager';
}

function pickerOptionHtml({ key, label, provider, meta, selected }) {
    return `<button type="button" role="option" data-picker-key="${escapeHtml(key)}" aria-selected="${selected}"><span class="picker-option-label">${escapeHtml(label)}</span>${meta ? `<small>${escapeHtml(meta)}</small>` : ''}</button>`;
}

function renderBoardPicker() {
    const board = boardByKey(state.selectedBoardKey);
    const locked = Boolean(state.currentChat || state.boardScopedDraft);
    const picker = el('composer-board-picker');
    const button = el('composer-board-button');
    el('composer-board-label').textContent = board?.name || 'No board';
    picker.classList.toggle('hidden', locked);
    picker.classList.toggle('locked', locked);
    button.disabled = locked;
    button.title = locked ? 'Board is fixed for this thread' : 'Choose ticket board';
    const menu = el('composer-board-menu');
    if (locked) menu.classList.add('hidden');
    const groups = ['jira', 'trello', 'decisions']
        .map((provider) => ({
            provider,
            boards: state.boards.filter((item) => item.provider === provider)
        }))
        .filter((group) => group.boards.length);
    menu.innerHTML =
        pickerOptionHtml({ key: '', label: 'No board', selected: !board }) +
        groups
            .map(
                (group) =>
                    `<div class="picker-group-label" role="presentation">${escapeHtml(providerName(group.provider))}</div>${group.boards.map((item) => pickerOptionHtml({ key: item.key, label: item.name || 'Untitled board', meta: item.project_id ? 'Linked' : 'Not linked', selected: item.key === state.selectedBoardKey })).join('')}`
            )
            .join('');
    menu.querySelectorAll('[data-picker-key]').forEach((button) => button.addEventListener('click', () => selectBoard(button.dataset.pickerKey)));
}

function renderComposerWorkflowPicker() {
    const picker = el('composer-workflow-picker');
    if (!picker) return;
    const selectedId = Number(state.currentWorkflow?.id || state.draft.workflow_id || 0);
    const selected = state.workflows.find((workflow) => Number(workflow.id) === selectedId) || null;
    const locked = Boolean(state.currentChat && isActiveAgentRun(state.currentRun));
    const button = el('composer-workflow-button');
    el('composer-workflow-label').textContent = selected?.name || 'Direct agent';
    picker.classList.toggle('locked', locked);
    button.disabled = locked;
    button.title = locked ? 'Workflow cannot change during an active run' : 'Choose how this thread should execute';
    const menu = el('composer-workflow-menu');
    if (locked) menu.classList.add('hidden');
    const projectId = Number(state.currentChat?.project_id || state.draft.project_id || 0);
    const relevant = state.workflows.filter(
        (workflow) => !projectId || !workflows.workflowProjectId(workflow) || workflows.workflowProjectId(workflow) === projectId
    );
    menu.innerHTML =
        pickerOptionHtml({
            key: '',
            label: 'Direct agent',
            meta: 'One agent turn without a reusable workflow',
            selected: !selected
        }) +
        relevant
            .map((workflow) =>
                pickerOptionHtml({
                    key: String(workflow.id),
                    label: workflow.name || 'Untitled workflow',
                    meta: `${Number(workflow.step_count || workflows.workflowStepList(workflow).length || 0)} steps`,
                    selected: Number(workflow.id) === selectedId
                })
            )
            .join('');
    menu.querySelectorAll('[data-picker-key]').forEach((option) =>
        option.addEventListener('click', () => selectComposerWorkflow(Number(option.dataset.pickerKey || 0)))
    );
}

async function selectComposerWorkflow(workflowId) {
    closePickerMenus();
    const id = Number(workflowId || 0) || null;
    if (state.currentChat) {
        try {
            await api(`/workflows/studio/tasks/${state.currentChat.id}/controls`, {
                method: 'PATCH',
                body: { workflow_id: id }
            });
            state.currentWorkflow = id ? await api(`/workflows/${id}`) : null;
            state.draft.workflow_id = id;
            state.currentChat.development_workflow_id = id;
            toast(id ? 'Workflow linked to this thread.' : 'Thread set to direct agent execution.');
        } catch (error) {
            toast(error.message || 'Could not change the thread workflow.', 'error');
        }
    } else {
        state.draft.workflow_id = id;
    }
    renderComposerWorkflowPicker();
    threads_inspector.renderInspector();
}

function closePickerMenus() {
    ['composer-board-menu', 'composer-ticket-menu', 'composer-workflow-menu'].forEach((id) => el(id)?.classList.add('hidden'));
    ['composer-board-button', 'composer-ticket-button', 'composer-workflow-button'].forEach((id) => el(id)?.setAttribute('aria-expanded', 'false'));
}

function togglePicker(buttonId, menuId) {
    const menu = el(menuId);
    const opening = menu.classList.contains('hidden');
    closePickerMenus();
    menu.classList.toggle('hidden', !opening);
    el(buttonId).setAttribute('aria-expanded', String(opening));
}

const pendingBoardLoads = new Map();
function loadBoardTickets(boardKey, options = {}) {
    const pending = pendingBoardLoads.get(boardKey);
    if (pending) return options.force ? pending.then(() => loadBoardTickets(boardKey, options)) : pending;
    const request = performBoardLoad(boardKey, options).finally(() => pendingBoardLoads.delete(boardKey));
    pendingBoardLoads.set(boardKey, request);
    return request;
}

async function performBoardLoad(boardKey, options) {
    if (!boardKey || (state.boardTicketLoads[boardKey] && !options?.force)) return state.boardDetails[boardKey] || null;
    const board = boardByKey(boardKey);
    if (!board) return;
    state.boardTicketLoads[boardKey] = true;
    try {
        const externalPath = `/tickets/external-boards/${encodeURIComponent(board.provider)}/${encodeURIComponent(board.external_id || board.id)}${options?.remote ? '?force_refresh=true' : ''}`;
        const detail = board.provider === 'decisions' ? await api(`/tickets/boards/${Number(board.local_id || board.id)}`) : await api(externalPath);
        cacheBoardDetail(board, detail);
        if (board.provider !== 'decisions' && detail.cache_ready === false) {
            delete state.boardTicketLoads[boardKey];
            window.setTimeout(() => {
                if (state.selectedBoardKey === boardKey) loadBoardTickets(boardKey);
            }, 1500);
        }
    } catch (error) {
        state.boardTickets[boardKey] = [];
        delete state.boardDetails[boardKey];
        delete state.boardTicketLoads[boardKey];
        if (!options?.throwOnError) toast(`Could not load ${providerName(board.provider)} tickets.`, 'error');
        if (options?.throwOnError) throw error;
    }
    syncTicketSelect();
    if (state.workspaceMode === 'kanban' && state.selectedBoardKey === boardKey) boards.renderKanbanWorkspace();
    return state.boardDetails[boardKey] || null;
}

function cacheBoardDetail(board, detail) {
    state.boardDetails[board.key] = detail;
    state.boardTickets[board.key] = (detail.lanes || []).flatMap((lane) =>
        (lane.tickets || lane.cards || []).map((ticket) => ({
            key: `${board.provider}:${ticket.id || ticket.key}`,
            id: ticket.id || ticket.key,
            ticket_id: board.provider === 'decisions' ? Number(ticket.id || 0) || null : null,
            ticket_title: ticket.title || ticket.name || ticket.key || 'Untitled ticket',
            text: ticket.description || ticket.desc || ticket.summary || ticket.title || ticket.name || '',
            lane_id: lane.id || lane.key || lane.name,
            lane_name: lane.name || lane.title || '',
            source: board.provider,
            external_url: ticket.url || ticket.external_url || '',
            source_chat_id: ticket.source_chat_id || null,
            priority: ticket.priority || 'medium',
            complexity: ticket.complexity || 'moderate',
            raw: ticket
        }))
    );
}

function syncTicketSelect() {
    const tickets = state.boardTickets[state.selectedBoardKey] || [];
    const locked = Boolean(state.currentChat);
    const selected = state.currentChat?.development_board_ticket_key || state.attachments.find((item) => item.board_ticket_key)?.board_ticket_key || '';
    const ticket = tickets.find((item) => item.key === selected) || null;
    const linkedTicketLabel = state.currentChat?.development_board_ticket_title || (state.currentChat?.development_ticket_id ? state.currentChat.title : '');
    const picker = el('composer-ticket-picker');
    const button = el('composer-ticket-button');
    const visible = !locked && Boolean(ticket || tickets.length);
    picker.classList.toggle('hidden', !visible);
    picker.classList.toggle('locked', locked);
    button.disabled = locked;
    button.title = locked ? 'Ticket is fixed for this thread' : 'Choose board ticket';
    el('composer-ticket-label').textContent = ticket ? ticketLabel(ticket) : linkedTicketLabel || 'No ticket';
    const menu = el('composer-ticket-menu');
    if (locked) menu.classList.add('hidden');
    const board = boardByKey(state.selectedBoardKey);
    menu.innerHTML =
        pickerOptionHtml({ key: '', label: 'No ticket', selected: !ticket }) +
        tickets
            .map((item) =>
                pickerOptionHtml({
                    key: item.key,
                    label: item.ticket_title,
                    meta: item.lane_name || '',
                    selected: item.key === selected
                })
            )
            .join('');
    menu.querySelectorAll('[data-picker-key]').forEach((button) => button.addEventListener('click', () => selectBoardTicket(button.dataset.pickerKey)));
}

function syncPermissionSelect() {
    const permission =
        typeof state.currentChat?.permission_profile === 'object' ? state.currentChat.permission_profile.mode : state.currentChat?.permission_profile;
    el('composer-permission-select').value = permission || state.draft.permission_mode || 'standard';
}

function closeThreadHeaderMenu() {
    el('thread-header-menu').classList.add('hidden');
    el('thread-more-button').setAttribute('aria-expanded', 'false');
}

function toggleThreadHeaderMenu() {
    if (!state.currentChat) return;
    const menu = el('thread-header-menu');
    const opening = menu.classList.contains('hidden');
    menu.classList.toggle('hidden', !opening);
    el('thread-more-button').setAttribute('aria-expanded', String(opening));
    const active = isActiveAgentRun(state.currentRun);
    el('thread-menu-clear-context').disabled = Boolean(active);
}

async function loadThreadTime() {
    if (!state.currentChat) {
        state.threadTime = null;
        updateElapsedTime();
        return;
    }
    const chatId = Number(state.currentChat.id);
    try {
        const time = await api(`/workflows/studio/tasks/${chatId}/time`);
        if (Number(state.currentChat?.id) !== chatId) return;
        state.threadTime = timerSnapshot(time);
        updateElapsedTime();
    } catch (_) {
        if (Number(state.currentChat?.id) === chatId) state.threadTime = null;
    }
}

function openTimeDialog() {
    if (!state.currentChat) return;
    closeThreadHeaderMenu();
    el('time-editor-value').textContent = threadTimeLabel(state.threadTime, true);
    el('time-play-button').disabled = Boolean(state.threadTime && !state.threadTime.paused);
    el('time-pause-button').disabled = Boolean(!state.threadTime || state.threadTime.paused);
    el('time-dialog').showModal();
}

async function updateThreadTime(action) {
    if (!state.currentChat) return;
    try {
        if (action === 'reset')
            state.threadTime = timerSnapshot(
                await api(`/workflows/studio/tasks/${state.currentChat.id}/time`, {
                    method: 'PATCH',
                    body: { seconds: 0 }
                })
            );
        else state.threadTime = timerSnapshot(await api(`/workflows/studio/tasks/${state.currentChat.id}/time/${action}`, { method: 'POST', body: {} }));
        el('time-editor-value').textContent = threadTimeLabel(state.threadTime, true);
        el('time-play-button').disabled = !state.threadTime.paused;
        el('time-pause-button').disabled = state.threadTime.paused;
        updateElapsedTime();
        toast(action === 'reset' ? 'Thread time reset.' : action === 'play' ? 'Time logging resumed.' : 'Time logging paused.');
    } catch (error) {
        toast(error.message || 'Could not update thread time.', 'error');
    }
}

async function clearThreadContext() {
    if (!state.currentChat) return;
    closeThreadHeaderMenu();
    try {
        await api(`/workflows/studio/tasks/${state.currentChat.id}/clear-context`, {
            method: 'POST',
            body: {}
        });
        toast('Context cleared. The transcript and time were preserved.');
    } catch (error) {
        toast(error.message || 'Could not clear context.', 'error');
    }
}

function setWorkspaceMode(mode, options) {
    if (state.workspaceMode === 'reports' && mode !== 'reports') reports.leave();
    if (mode !== 'chat') {
        threadSession.cancel();
        disconnectLiveUpdates();
        state.chatLoadToken++;
    }
    if (state.workspaceMode === 'plan' && mode !== 'plan') planning.leave();
    if (['terminals', 'terminals_home'].includes(state.workspaceMode) && !['terminals', 'terminals_home'].includes(mode)) terminals.leave();
    const automationMode = mode === 'automations' || mode === 'automation_rules';
    const ruleMode = mode === 'automation_rules';
    const workflowMode = mode === 'workflows';
    const kanbanMode = mode === 'kanban';
    const incomingMode = mode === 'incoming';
    const terminalMode = mode === 'terminals';
    const planMode = mode === 'plan';
    const terminalsHomeMode = mode === 'terminals_home';
    const reportsMode = mode === 'reports';
    const placeholderMode = planMode || terminalsHomeMode || reportsMode;
    const alternateMode = automationMode || workflowMode || kanbanMode || incomingMode || terminalMode || placeholderMode;
    if (!terminalMode && state.workspaceMode === 'terminals') terminals.closeTerminalViews();
    state.workspaceMode = ruleMode
        ? 'automation_rules'
        : automationMode
          ? 'automations'
          : workflowMode
            ? 'workflows'
            : kanbanMode
              ? 'kanban'
              : incomingMode
                ? 'incoming'
                : terminalMode
                  ? 'terminals'
                  : planMode
                    ? 'plan'
                    : terminalsHomeMode
                      ? 'terminals_home'
                      : reportsMode
                        ? 'reports'
                        : 'chat';
    el('development-task-header').classList.toggle('hidden', alternateMode);
    el('conversation').classList.toggle('hidden', alternateMode);
    el('development-composer-layer').classList.toggle('hidden', alternateMode);
    el('scheduled-workspace').classList.toggle('hidden', !automationMode);
    el('workflow-workspace').classList.toggle('hidden', !workflowMode);
    el('kanban-workspace').classList.toggle('hidden', !kanbanMode);
    el('incoming-workspace').classList.toggle('hidden', !incomingMode);
    el('terminal-workspace').classList.toggle('hidden', !terminalMode);
    el('plan-workspace').classList.toggle('hidden', !planMode);
    el('terminals-home-workspace').classList.toggle('hidden', !terminalsHomeMode);
    el('reports-workspace').classList.toggle('hidden', !reportsMode);
    if (alternateMode) {
        closePickerMenus();
        closeInspector();
        if (automationMode) automations.renderScheduledWorkspace();
        if (workflowMode) workflows.renderWorkflowWorkspace();
        if (kanbanMode) boards.renderKanbanWorkspace();
        if (incomingMode) {
            incoming.renderIncomingWorkspace();
            incoming.refreshIncomingData({ force: true });
        }
        if (terminalMode) {
            terminals.renderTerminalWorkspace();
            terminals.startTerminalPolling();
        }
        if (terminalsHomeMode) {
            terminals.renderTerminalsHome();
            terminals.startTerminalPolling();
        }
        if (reportsMode) reports.enter();
        if (planMode && !options?.boardPlan) planning.openHome(state.boards, state.projects);
    }
    sidebar.renderSidebar();
    if (window.innerWidth <= 680) closeDrawers();
    const focusTarget = automationMode
        ? el('scheduled-prompt-input')
        : workflowMode
          ? el('workflow-prompt-input')
          : incomingMode || kanbanMode || terminalMode
            ? null
            : el('task-prompt');
    if (options?.focus !== false) window.setTimeout(() => focusTarget?.focus(), 0);
    if (options?.updateUrl !== false && developmentModePaths[state.workspaceMode]) setDevelopmentLocation(developmentModePaths[state.workspaceMode], options);
}

async function ensureProviderCatalogs() {
    if (state.providers.length && Object.keys(state.modelCatalogs).length) return;
    const providersData = await api('/llms/available-providers');
    state.providers = providersData.providers || [];
    const catalogs = await Promise.all(
        state.providers.map(async (provider) => {
            const id = String(provider.id || provider);
            try {
                const data = await api(`/llms/models?type=conversational&provider=${encodeURIComponent(id)}`);
                return [id.toLowerCase(), data.models || []];
            } catch (_) {
                return [id.toLowerCase(), []];
            }
        })
    );
    state.modelCatalogs = Object.fromEntries(catalogs);
}

function boardSupportsKanban(board) {
    if (!board) return false;
    if (board.provider === 'decisions') return Boolean(board.local_id || board.id);
    return ['jira', 'trello'].includes(board.provider) && Boolean(board.external_id || board.id);
}

async function waitForRouteReady() {
    while (state.routeLoading) await new Promise((resolve) => window.setTimeout(resolve, 20));
}

async function openBoardFromSidebar(boardKey) {
    await waitForRouteReady();
    const selection = ++state.chatLoadToken;
    const board = boardByKey(boardKey);
    if (boardSupportsKanban(board)) {
        const detail = state.boardDetails[boardKey] || (await loadBoardTickets(boardKey));
        if (selection !== state.chatLoadToken) return;
        if (detail) {
            await openBoardKanban(boardKey);
            return;
        }
    }
    startBoardDevelopment(boardKey, { skipTicketLoad: true });
}

function startBoardDevelopment(boardKey, options) {
    selectBoard(boardKey, {
        boardScoped: true,
        skipTicketLoad: Boolean(options?.skipTicketLoad)
    });
    toast('New board thread ready.');
}

async function startNewDevelopmentFromSidebar() {
    await waitForRouteReady();
    startNewDevelopment();
}

async function openBoardKanban(boardKey, options) {
    const board = boardByKey(boardKey);
    if (!board) return;
    state.selectedBoardKey = board.key;
    setWorkspaceMode('kanban', { focus: false, updateUrl: false });
    boards.renderKanbanWorkspace();
    await loadBoardTickets(board.key);
    sidebar.closeBoardContextMenu();
    if (options?.updateUrl !== false) setDevelopmentLocation(developmentBoardPath(board.key, 'kanban'), options);
}

async function openBoardPlan(boardKey, options) {
    if (planning.hasUnsavedChanges() && !(await planning.beforeLeave())) return;
    const board = boardByKey(boardKey);
    if (!board) return;
    if (!board.project_id || !projectById(board.project_id)) {
        sidebar.closeBoardContextMenu();
        setWorkspaceMode('plan', { focus: false, updateUrl: false });
        toast('Link this board to a project before opening Plan.', 'error');
        setDevelopmentLocation(developmentModePaths.plan, { replace: true });
        return;
    }
    state.selectedBoardKey = board.key;
    setWorkspaceMode('plan', { focus: false, updateUrl: false, boardPlan: true });
    sidebar.closeBoardContextMenu();
    await planning.openBoard(board, state.projects);
    if (options?.updateUrl !== false) setDevelopmentLocation(developmentBoardPath(board.key, 'plan'), options);
}

async function openBoardTerminals(boardKey, options) {
    const board = boardByKey(boardKey);
    sidebar.closeBoardContextMenu();
    if (!board?.project_id) {
        toast('Add a project folder to this board first.', 'error');
        return;
    }
    state.selectedBoardKey = board.key;
    terminals.primeTerminalSelection({ boardKey: board.key });
    await terminals.openProjectTerminals(Number(board.project_id), {
        ...options,
        path: developmentBoardPath(board.key, 'terminals')
    });
}

function selectBoard(boardKey, options) {
    state.boardScopedDraft = Boolean(options?.boardScoped);
    state.selectedBoardKey = boardKey || '';
    const board = boardByKey(state.selectedBoardKey);
    const projectId = board?.project_id ? Number(board.project_id) : null;
    state.selectedProjectId = projectId;
    state.draft.project_id = projectId;
    state.attachments = state.attachments.filter((item) => !item.board_ticket_key);
    closePickerMenus();
    renderBoardPicker();
    syncTicketSelect();
    if (board && !options?.skipTicketLoad) loadBoardTickets(board.key);
    threads_composer.renderAttachments();
    sidebar.renderSidebar();
    showEmptyTask({ preserveProject: true });
    setWorkspaceMode('chat', { focus: false, updateUrl: false });
    el('task-prompt').focus();
    if (window.innerWidth <= 680) closeDrawers();
    if (board && options?.updateUrl !== false) setDevelopmentLocation(developmentBoardPath(board.key), options);
}

function selectBoardTicket(ticketKey) {
    if (state.currentChat) return;
    const tickets = state.boardTickets[state.selectedBoardKey] || [];
    const ticket = tickets.find((item) => item.key === ticketKey) || null;
    state.attachments = state.attachments.filter((item) => !item.board_ticket_key);
    if (ticket)
        state.attachments.unshift({
            ...ticket,
            label: ticket.ticket_title,
            board_ticket_key: ticket.key,
            sourceLabel: `${providerName(ticket.source)} · ${ticket.lane_name || 'No lane'}`
        });
    closePickerMenus();
    syncTicketSelect();
    threads_composer.renderAttachments();
}

const threadSession = createThreadSession({ api, workflowForChat, runForWorkflow, directExecutionForChat, workflowRunId, isActiveAgentRun, timerSnapshot });

const catalogLoader = createCatalogLoader(api);
let shellRefresh = null;
let catalogErrors = '';
function refreshShell(options = {}) {
    if (shellRefresh) {
        if (options.background) return shellRefresh;
        return shellRefresh.then(() => refreshShell({ ...options, background: true }));
    }
    shellRefresh = performShellRefresh(options).finally(() => {
        shellRefresh = null;
    });
    return shellRefresh;
}

async function performShellRefresh(options) {
    const preserveConversation = Boolean(options?.preserveConversation);
    try {
        const {
            projects,
            boards: boardRows,
            externalBoards,
            chatsData,
            workflows: workflowSummaries,
            runs,
            inboxData,
            automationData,
            skillsData,
            errors
        } = await catalogLoader.load();
        const errorKey = errors.join(',');
        if (errorKey && errorKey !== catalogErrors) toast('Some Development sources are unavailable. Available data remains usable.', 'error');
        catalogErrors = errorKey;
        const autoRoute = state.routingAssessment;
        const workflowRows = Array.isArray(workflowSummaries) ? workflowSummaries : [];
        const detailedWorkflows = new Map(
            state.workflows.filter((workflow) => Array.isArray(workflow.steps)).map((workflow) => [Number(workflow.id), workflow])
        );
        state.workflows = workflowRows.map((workflow) => {
            const detail = detailedWorkflows.get(Number(workflow.id));
            return detail
                ? {
                      ...detail,
                      ...workflow,
                      step_count: workflow.step_count ?? detail.steps.length
                  }
                : workflow;
        });
        state.projects = Array.isArray(projects) ? projects : projects.projects || [];
        state.boards = normalizeBoards(Array.isArray(boardRows) ? boardRows : [], externalBoards || {});
        const developmentChats = (chatsData.chats || []).filter((chat) => chat.development);
        state.chats = developmentChats.filter((chat) => !chat.archived);
        state.archivedChats = developmentChats.filter((chat) => chat.archived);
        state.runs = (Array.isArray(runs) ? runs : runs.runs || []).map((run) => ({
            ...run,
            __clientReceivedAt: Date.now()
        }));
        state.inbox = inboxData.items || [];
        state.automations = automationData.automations || [];
        state.skills = Array.isArray(skillsData.skills) ? skillsData.skills : [];
        if (autoRoute?.route) state.routingAssessment = autoRoute;
        threads_composer.renderComposerSkillMenu();
        threads_composer.updateModelLabel();
        sidebar.renderSidebar();
        if (['automations', 'automation_rules'].includes(state.workspaceMode)) automations.renderScheduledWorkspace();
        if (state.workspaceMode === 'workflows') workflows.renderWorkflowWorkspace();
        if (state.workspaceMode === 'incoming') incoming.renderIncomingWorkspace();
        if (state.workspaceMode === 'terminals') terminals.renderTerminalWorkspace();
        if (state.workspaceMode === 'plan') planning.syncCatalog(state.boards, state.projects);
        if (state.currentChat && state.workspaceMode === 'chat') {
            const selection = state.chatLoadToken;
            const chatId = Number(state.currentChat.id);
            const runWasActive = isActiveAgentRun(state.currentRun);
            const current = () => selection === state.chatLoadToken && chatId === Number(state.currentChat?.id) && state.workspaceMode === 'chat';
            const snapshot = await threadSession.read(chatId, { isCurrent: current });
            if (!snapshot || !current()) return chatsData;
            Object.assign(state, snapshot);
            updateElapsedTime();
            const runFinished = runWasActive && !isActiveAgentRun(state.currentRun);
            if (preserveConversation && runFinished && Number(state.currentChat?.id) === chatId) {
                const completedChat = await api(`/chats/${chatId}?surface=development`);
                if (!current()) return chatsData;
                state.currentChat = completedChat;
                state.chats = state.chats.map((chat) => (Number(chat.id) === chatId ? state.currentChat : chat));
                threads_transcript.renderConversation(state.currentChat);
            }
            if (runFinished) sidebar.renderSidebar();
            syncHeader();
            if (preserveConversation && !runFinished) threads_transcript.syncDurableTurnActivity();
            threads_inspector.renderInspector();
            if (!preserveConversation && state.workspaceMode === 'chat') {
                await loadChat(state.currentChat.id, { quiet: true });
            }
        }
        const route = developmentRoute();
        const incomingRoute =
            state.workspaceMode === 'incoming' ||
            (route.kind === 'workspace' && route.mode === 'incoming') ||
            (route.kind === 'board' && route.view === 'incoming');
        if (incomingRoute) await incoming.refreshIncomingData({ force: true });
        return chatsData;
    } catch (error) {
        toast(error.message || 'Could not load Development threads.', 'error');
        return { chats: [] };
    }
}

async function copyText(value, successMessage) {
    const text = String(value || '');
    try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        toast(successMessage || 'Copied.');
    } catch (_error) {
        toast('Could not copy to the clipboard.', 'error');
    }
}

async function forkCurrentThread() {
    const id = Number(state.currentChat?.id || 0);
    if (!id) return;
    try {
        const result = await api(`/workflows/studio/tasks/${id}/fork`, {
            method: 'POST',
            body: {}
        });
        await refreshShell({ preserveConversation: true });
        await loadChat(result.id);
        toast('Opened an independent thread from this transcript.');
    } catch (error) {
        toast(error.message || 'Could not fork the thread.', 'error');
    }
}

async function reviewTurnChanges() {
    if (!state.currentChat) return;
    state.currentTab = 'changes';
    state.selectedChangePath = '';
    openInspector();
    await threads_inspector.loadInspectorChanges();
}

async function undoTurnChanges() {
    if (!state.currentChat || !state.currentRun?.changes?.reversible) return;
    const files = state.currentRun.changes.files || [];
    if (
        !(await confirmAction({
            title: 'Undo this turn',
            message: `Restore the ${files.length} ${files.length === 1 ? 'file' : 'files'} changed only by this turn? Later edits are protected and will stop the undo.`,
            confirmLabel: 'Undo changes',
            danger: true
        }))
    )
        return;
    try {
        const result = await api(`/workflows/studio/tasks/${state.currentChat.id}/execution/changes/undo`, { method: 'POST', body: {} });
        state.currentRun.changes = result.changes || {
            ...state.currentRun.changes,
            undone: true
        };
        state.changeReview = null;
        state.selectedChangePath = '';
        threads_transcript.renderConversation(state.currentChat);
        threads_inspector.renderInspector();
        toast('Turn changes restored. The conversation was kept.');
    } catch (error) {
        toast(error.message || 'Could not undo these changes.', 'error');
    }
}

function bindConversationActions(list, messages) {
    list.querySelectorAll('[data-copy-message]').forEach((button) =>
        button.addEventListener('click', () => {
            const message = messages[Number(button.dataset.copyMessage)];
            copyText(message?.content || '', 'Response copied.');
        })
    );
    list.querySelector('[data-fork-current]')?.addEventListener('click', forkCurrentThread);
    list.querySelector('[data-turn-review]')?.addEventListener('click', reviewTurnChanges);
    list.querySelector('[data-turn-undo]')?.addEventListener('click', undoTurnChanges);
    list.querySelector('[data-turn-changes-toggle]')?.addEventListener('click', (event) => {
        const button = event.currentTarget;
        const card = button.closest('.turn-changes');
        const content = card?.querySelector('.turn-change-list');
        const expanded = button.getAttribute('aria-expanded') === 'true';
        button.setAttribute('aria-expanded', String(!expanded));
        content?.classList.toggle('hidden', expanded);
    });
}

async function loadChat(chatId, options) {
    if (planning.hasUnsavedChanges() && !(await planning.beforeLeave())) return;
    if (!chatId) return;
    const loadToken = ++state.chatLoadToken;
    const stale = () => loadToken !== state.chatLoadToken;
    try {
        const snapshot = await threadSession.read(chatId, { includeChat: true, isCurrent: () => !stale() });
        if (!snapshot || stale()) return;
        Object.assign(state, snapshot);
        const chat = snapshot.currentChat;
        state.doctor = null;
        state.changeReview = null;
        state.changeReviewLoading = false;
        state.selectedChangePath = '';
        updateElapsedTime();
        state.draft.project_id = chat.project_id || null;
        state.draft.workflow_id = Number(state.currentWorkflow?.id || chat.development_workflow_id || 0) || null;
        state.selectedProjectId = chat.project_id || null;
        state.selectedBoardKey = chat.development_board_key || boardForProject(chat.project_id)?.key || state.selectedBoardKey || '';
        state.draft.title = chat.title || '';
        state.draft.route_mode = chat.route_mode || 'auto';
        state.draft.provider = chat.provider || '';
        state.draft.model_name = chat.model_name || '';
        state.draft.reasoning_effort = chat.reasoning_effort || state.draft.reasoning_effort || 'medium';
        state.draft.service_tier = chat.service_tier || state.draft.service_tier || 'standard';
        state.draft.execution_profile = chat.execution_profile || 'code';
        state.draft.autonomy_level = chat.autonomy_level || 'full';
        threads_composer.updateModelLabel();
        threads_composer.updateModeLabel();
        syncHeader();
        sidebar.renderSidebar();
        threads_transcript.renderConversation(chat);
        threads_inspector.renderInspector();
        setWorkspaceMode('chat', { focus: false, updateUrl: false });
        connectLiveUpdates(chat.id);
        if (options?.updateUrl !== false)
            setDevelopmentLocation(`/development/threads/${Number(chat.id)}/`, {
                replace: options?.replaceUrl
            });
        if (window.innerWidth <= 680) closeDrawers();
    } catch (error) {
        if (!options?.quiet) toast(error.message || 'Could not open task.', 'error');
    }
}

function formatDateTime(value) {
    if (!value) return 'Not scheduled';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function showEmptyTask(options) {
    state.chatLoadToken += 1;
    const preserveProject = Boolean(options?.preserveProject);
    if (!preserveProject) {
        state.boardScopedDraft = false;
        state.selectedProjectId = null;
        state.draft.project_id = null;
        state.selectedBoardKey = '';
    }
    state.currentChat = null;
    state.currentWorkflow = null;
    state.currentRun = null;
    state.threadTime = null;
    state.plans = [];
    state.artifacts = [];
    state.controlState = {
        channels: {},
        interactions: [],
        commands: [],
        controls: {}
    };
    state.doctor = null;
    state.draft.title = '';
    state.draft.workflow_id = null;
    disconnectLiveUpdates();
    el('message-list').classList.add('hidden');
    el('studio-empty').classList.remove('hidden');
    syncHeader();
    sidebar.renderSidebar();
    threads_inspector.renderInspector();
    if (window.innerWidth > 980) closeInspector();
}

function startNewDevelopment(options) {
    state.boardScopedDraft = false;
    state.attachments = [];
    state.draft.ticket_id = null;
    threads_composer.renderAttachments();
    showEmptyTask();
    setWorkspaceMode('chat', { updateUrl: false });
    if (options?.updateUrl !== false) setDevelopmentLocation('/development/new/', options);
    if (window.innerWidth <= 680) closeDrawers();
}

function openRenameThreadDialog(chatId) {
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === Number(chatId));
    if (!chat) return;
    el('thread-rename-id').value = String(chat.id);
    el('thread-rename-name').value = chat.title || '';
    el('thread-rename-dialog').showModal();
    window.setTimeout(() => el('thread-rename-name').select(), 0);
}

async function renameThread(event) {
    event.preventDefault();
    const id = Number(el('thread-rename-id').value || 0);
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === id);
    const title = el('thread-rename-name').value.trim();
    if (!id || !chat || !title) return;
    try {
        const updated = await api(`/workflows/studio/tasks/${id}`, {
            method: 'PATCH',
            body: { title }
        });
        const projected = {
            ...updated,
            development_board_key: updated.board_key,
            development_board_provider: updated.board_provider,
            development_ticket_id: updated.ticket_id,
            development_board_ticket_key: updated.board_ticket_key
        };
        Object.assign(chat, projected);
        if (Number(state.currentChat?.id) === id) Object.assign(state.currentChat, projected);
        el('thread-rename-dialog').close();
        sidebar.renderSidebar();
        syncHeader();
        toast('Thread renamed.');
    } catch (error) {
        toast(error.message || 'Could not rename the thread.', 'error');
    }
}

function parseThreadTime(value) {
    const match = String(value || '')
        .trim()
        .match(/^(\d+):([0-5]\d):([0-5]\d)$/);
    if (!match) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

async function openThreadDialog(chatId) {
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === Number(chatId));
    if (!chat) return;
    el('thread-dialog-id').value = String(chat.id);
    const selectedBoard = boardByKey(chat.development_board_key) || boardForProject(chat.project_id);
    el('thread-board-name').value = selectedBoard ? `${selectedBoard.name} · ${providerName(selectedBoard.provider)}` : 'No board';
    if (selectedBoard) await loadBoardTickets(selectedBoard.key);
    const tickets = selectedBoard ? state.boardTickets[selectedBoard.key] || [] : [];
    const currentTicketId = Number(chat.development_ticket_id || 0) || null;
    const currentTicketKey = String(chat.development_board_ticket_key || '');
    const ticketSelect = el('thread-ticket');
    ticketSelect.innerHTML =
        `<option value="">${currentTicketId || currentTicketKey ? 'Keep current ticket' : 'No ticket'}</option>` +
        tickets
            .map((ticket) => {
                const ownedElsewhere = ticket.source_chat_id && Number(ticket.source_chat_id) !== Number(chat.id);
                return `<option value="${escapeHtml(ticket.key)}"${ownedElsewhere ? ' disabled' : ''}>${escapeHtml(ticket.ticket_title)}${ticket.lane_name ? ` · ${escapeHtml(ticket.lane_name)}` : ''}${ownedElsewhere ? ' · linked to another thread' : ''}</option>`;
            })
            .join('');
    const selectedTicket = tickets.find((ticket) => (currentTicketId && Number(ticket.ticket_id) === currentTicketId) || ticket.key === currentTicketKey);
    ticketSelect.value = selectedTicket?.key || '';
    ticketSelect.disabled = !selectedBoard || !tickets.length;
    let time = null;
    try {
        time = timerSnapshot(await api(`/workflows/studio/tasks/${chat.id}/time`));
    } catch (_) {
        time = null;
    }
    const seconds = Math.max(0, Math.floor(threadTimeSeconds(time)));
    el('thread-dialog').dataset.timeSeconds = String(seconds);
    el('thread-time-value').value = formatSeconds(seconds, true);
    el('thread-dialog').showModal();
    window.setTimeout(() => (ticketSelect.disabled ? el('thread-time-value') : ticketSelect).focus(), 0);
}

async function saveThread(event) {
    event.preventDefault();
    const id = Number(el('thread-dialog-id').value || 0);
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === id);
    if (!id || !chat) return;
    const seconds = parseThreadTime(el('thread-time-value').value);
    if (seconds === null) {
        el('thread-time-value').setCustomValidity('Enter time as hours:minutes:seconds.');
        el('thread-time-value').reportValidity();
        return;
    }
    el('thread-time-value').setCustomValidity('');
    const selectedBoard = boardByKey(chat.development_board_key) || boardForProject(chat.project_id);
    const tickets = selectedBoard ? state.boardTickets[selectedBoard.key] || [] : [];
    const ticket = tickets.find((item) => item.key === el('thread-ticket').value) || null;
    try {
        const updated = await api(`/workflows/studio/tasks/${id}`, {
            method: 'PATCH',
            body: {
                title: chat.title,
                project_id: chat.project_id || selectedBoard?.project_id || null,
                board_key: selectedBoard?.key || null,
                board_provider: selectedBoard?.provider || null,
                ticket_id: ticket?.ticket_id || chat.development_ticket_id || null,
                board_ticket_key: ticket?.key || chat.development_board_ticket_key || null,
                board_ticket_title: ticket?.ticket_title || chat.development_board_ticket_title || chat.title,
                board_ticket_lane: ticket?.lane_name || null
            }
        });
        if (seconds !== Number(el('thread-dialog').dataset.timeSeconds || 0)) {
            const time = timerSnapshot(
                await api(`/workflows/studio/tasks/${id}/time`, {
                    method: 'PATCH',
                    body: { seconds }
                })
            );
            if (Number(state.currentChat?.id) === id) state.threadTime = time;
        }
        const item = state.chats.find((chat) => Number(chat.id) === id);
        const projected = {
            ...updated,
            development_board_key: updated.board_key,
            development_board_provider: updated.board_provider,
            development_ticket_id: updated.ticket_id,
            development_board_ticket_key: updated.board_ticket_key
        };
        if (item) Object.assign(item, projected);
        if (Number(state.currentChat?.id) === id) Object.assign(state.currentChat, projected);
        el('thread-dialog').close();
        sidebar.renderSidebar();
        syncHeader();
        toast('Thread updated.');
    } catch (error) {
        toast(error.message || 'Could not update the thread.', 'error');
    }
}

async function deleteThread(id) {
    const chat = state.chats.concat(state.archivedChats).find((item) => Number(item.id) === id);
    if (!id || !chat) return;
    const linkedTicketId = Number(chat.development_ticket_id || chat.development?.ticket_id || 0) || null;
    const decision = await confirmAction({
        title: 'Delete thread',
        message: `Delete “${chat.title}” and its thread data? This cannot be undone.`,
        confirmLabel: 'Delete',
        danger: true,
        checkbox: linkedTicketId ? { label: 'Also delete linked ticket', checked: true } : null
    });
    const confirmed = typeof decision === 'object' ? decision.confirmed : decision;
    if (!confirmed) return;
    const deleteLinkedTicket = !linkedTicketId || (typeof decision === 'object' && decision.checked);
    try {
        await api(`/workflows/studio/tasks/${id}?delete_linked_ticket=${deleteLinkedTicket ? 'true' : 'false'}`, { method: 'DELETE' });
        if (Number(state.currentChat?.id) === id) showEmptyTask({ preserveProject: true });
        await refreshShell({ preserveConversation: true });
        toast(deleteLinkedTicket && linkedTicketId ? 'Thread and linked ticket deleted.' : 'Thread deleted.');
    } catch (error) {
        toast(error.message || 'Could not delete the thread.', 'error');
    }
}

async function changeProjectScope(event) {
    const projectId = event.target.value ? Number(event.target.value) : null;
    state.selectedProjectId = projectId;
    state.draft.project_id = projectId;
    state.draft.ticket_id = null;
    state.projectContext = null;
    state.attachments = state.attachments.filter((item) => !item.ticket_id);
    threads_composer.renderAttachments();
    syncTicketSelect();
    if (!state.currentChat) {
        syncHeader();
        sidebar.renderSidebar();
        return;
    }
    try {
        const updated = await api(`/workflows/studio/tasks/${state.currentChat.id}/controls`, { method: 'PATCH', body: { project_id: projectId } });
        Object.assign(state.currentChat, updated);
        const item = state.chats.find((chat) => Number(chat.id) === Number(updated.id));
        if (item) Object.assign(item, updated);
        sidebar.renderSidebar();
        syncHeader();
        toast('Thread moved to the selected project.');
    } catch (error) {
        toast(error.message || 'Could not change the project.', 'error');
        syncProjectSelect();
    }
}

function changeTicketScope(event) {
    const ticketId = Number(event.target.value || 0);
    state.draft.ticket_id = ticketId || null;
    state.attachments = state.attachments.filter((item) => !item.ticket_id);
    if (ticketId) {
        const item = ticketItemsForProject(state.currentChat?.project_id || state.selectedProjectId || state.draft.project_id).find(
            (entry) => Number(entry.ticket_id || entry.id) === ticketId
        );
        if (item)
            state.attachments.unshift({
                key: `ticket:${ticketId}`,
                label: item.ticket_title || item.text || `Ticket #${ticketId}`,
                text: item.text || item.response_text || item.ticket_title || '',
                ticket_id: ticketId,
                source: item.source || 'ticket',
                sourceLabel: `Ticket${item.lane || item.lane_name || item.column_name ? ` · ${item.lane || item.lane_name || item.column_name}` : ''}`
            });
    }
    threads_composer.renderAttachments();
}

async function changeComposerPermission(event) {
    const mode = event.target.value;
    state.draft.permission_mode = mode;
    if (!state.currentChat) return;
    try {
        await api(`/workflows/studio/tasks/${state.currentChat.id}/controls`, {
            method: 'PATCH',
            body: { permission_profile: { mode } }
        });
        state.currentChat.permission_profile = { mode };
        toast(`${event.target.options[event.target.selectedIndex].text} enabled.`);
    } catch (error) {
        toast(error.message || 'Could not change permissions.', 'error');
        syncPermissionSelect();
    }
}

const threadLive = createThreadLiveUpdates({
    token,
    isActive: (id) => state.workspaceMode === 'chat' && Number(state.currentChat?.id) === id,
    refresh: () => {
        if (!state.busy) refreshShell({ preserveConversation: false });
    }
});
const disconnectLiveUpdates = threadLive.disconnect;
const connectLiveUpdates = threadLive.connect;
window.addEventListener('pagehide', () => {
    threadSession.cancel();
    threadLive.disconnect();
});

function openSidebar() {
    el('sidebar-open').setAttribute('aria-expanded', 'true');
    el('studio-sidebar').classList.add('open');
    el('drawer-backdrop').classList.remove('hidden');
}

function openInspector() {
    el('studio-shell').classList.remove('inspector-closed');
    el('task-inspector').classList.add('open');
    if (window.innerWidth <= 980) el('drawer-backdrop').classList.remove('hidden');
    threads_inspector.renderInspector();
}

function closeInspector() {
    el('task-inspector').classList.remove('open');
    if (window.innerWidth > 980) el('studio-shell').classList.add('inspector-closed');
    if (!el('studio-sidebar').classList.contains('open')) el('drawer-backdrop').classList.add('hidden');
}

function closeDrawers() {
    el('sidebar-open').setAttribute('aria-expanded', 'false');
    el('studio-sidebar').classList.remove('open');
    el('task-inspector').classList.remove('open');
    el('drawer-backdrop').classList.add('hidden');
}

function resizePrompt() {
    const input = el('task-prompt');
    input.style.height = 'auto';
    input.style.height = `${Math.min(180, Math.max(55, input.scrollHeight))}px`;
}

function syncComposerAvailability() {
    const available = state.ready && !state.routeLoading && !state.busy;
    el('task-prompt').disabled = !available;
    el('send-button').disabled = !available;
}

function developmentChatById(chatId) {
    const targetId = Number(chatId || 0);
    if (!targetId) return null;
    return [...state.chats, ...state.archivedChats].find((chat) => Number(chat.id) === targetId && chat.development === true) || null;
}

async function restoreDevelopmentHome(options) {
    const requested = developmentChatById(options?.lastChatId);
    const initial = requested || state.chats[0] || null;
    if (initial) {
        await loadChat(initial.id, {
            quiet: true,
            replaceUrl: !options?.fromHistory
        });
        return;
    }
    startNewDevelopment({ updateUrl: false });
    setDevelopmentLocation('/development/new/', { replace: true });
}

function primeInitialWorkspace(route) {
    if (route.kind === 'project_terminals') {
        terminals.primeTerminalSelection({ projectId: route.projectId });
        setWorkspaceMode('terminals', { focus: false, updateUrl: false });
        return;
    }
    if (route.kind === 'board' && route.view === 'terminals') {
        terminals.primeTerminalSelection({ boardKey: route.boardKey });
        state.selectedBoardKey = route.boardKey;
        setWorkspaceMode('terminals', { focus: false, updateUrl: false });
        return;
    }
    if (route.kind === 'board' && route.view === 'plan') {
        state.selectedBoardKey = route.boardKey;
        setWorkspaceMode('plan', {
            focus: false,
            updateUrl: false,
            boardPlan: true
        });
        return;
    }
    if (route.kind === 'workspace' && route.mode === 'terminals_home') {
        setWorkspaceMode('terminals_home', { focus: false, updateUrl: false });
    }
}

async function restoreDevelopmentLocation(options) {
    const requestedLocation = window.location.href;
    if (planning.hasUnsavedChanges() && !(await planning.beforeLeave())) {
        if (options?.fromHistory) window.history.pushState({}, '', acceptedLocation);
        return;
    }
    state.routeLoading = true;
    syncComposerAvailability();
    const route = developmentRoute();
    try {
        document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
        if (route.kind === 'home') {
            await restoreDevelopmentHome(options);
            return;
        }
        if (route.kind === 'thread') {
            const thread = developmentChatById(route.chatId);
            if (!thread) {
                await restoreDevelopmentHome({ fromHistory: options?.fromHistory });
                return;
            }
            await loadChat(thread.id, { quiet: true, updateUrl: false });
        } else if (route.kind === 'new') {
            startNewDevelopment({ updateUrl: false });
            el('task-prompt').focus();
        } else if (route.kind === 'workspace') {
            setWorkspaceMode(route.mode, { focus: false, updateUrl: false });
        } else if (route.kind === 'project_terminals') {
            await terminals.openProjectTerminals(route.projectId, {
                updateUrl: false
            });
        } else if (route.kind === 'board') {
            const board = boardByKey(route.boardKey);
            if (!board) {
                startNewDevelopment({ updateUrl: false });
                setDevelopmentLocation('/development/new/', { replace: true });
                return;
            }
            selectBoard(board.key, { updateUrl: false });
            if (route.view === 'plan') await openBoardPlan(board.key, { updateUrl: false });
            else if (route.view === 'kanban') {
                await openBoardKanban(board.key, { updateUrl: false });
                if (route.ticketId) {
                    const ticket = (state.boardTickets[board.key] || []).find((item) => Number(item.ticket_id || item.id) === Number(route.ticketId));
                    if (ticket) boards.openKanbanTicketDialog(ticket.key);
                }
            } else if (route.view === 'terminals') await openBoardTerminals(board.key, { updateUrl: false });
            else if (route.view === 'incoming') {
                setWorkspaceMode('incoming', { focus: false, updateUrl: false });
            } else if (route.view === 'settings') await sidebar.openBoardLinkDialog(board.key);
        }
        if (!options?.fromHistory && window.location.href === requestedLocation) setDevelopmentLocation(route.path, { replace: true });
    } finally {
        state.routeLoading = false;
        syncComposerAvailability();
    }
}

async function init() {
    try {
        state.draft.reasoning_effort = window.localStorage.getItem('decisions-development-reasoning-effort') || 'medium';
        state.draft.service_tier = window.localStorage.getItem('decisions-development-service-tier') || 'standard';
    } catch (_) {
        /* Defaults remain usable without local storage. */
    }
    bindEvents();
    threads_composer.renderAttachments();
    threads_inspector.renderInspector();
    primeInitialWorkspace(developmentRoute());
    const chatData = await refreshShell({ preserveConversation: true });
    await restoreDevelopmentLocation({
        lastChatId: Number(chatData.last_chat_id || 0)
    });
    const googleStatus = new URLSearchParams(window.location.search || '').get('google');
    if (googleStatus === 'connected') toast('Google connected. Gmail is ready.');
    else if (googleStatus === 'error') toast('Google could not be connected. Try reconnecting again.', 'error');
    if (googleStatus) {
        const cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete('google');
        window.history.replaceState({}, '', cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
    }
    state.ready = true;
    syncComposerAvailability();
    api('/workflows/studio/routing-assessment', {
        method: 'POST',
        body: {
            instruction: '',
            ticket_title: '',
            ticket_description: '',
            has_images: false,
            recent_messages: []
        }
    })
        .then((assessment) => {
            if (assessment?.route && !state.routingAssessment) {
                state.routingAssessment = assessment;
                threads_composer.updateModelLabel();
            }
        })
        .catch(() => {});
    state.elapsedTimer = window.setInterval(updateElapsedTime, 1000);
    state.polling = window.setInterval(() => {
        if (document.visibilityState !== 'visible' || state.busy) return;
        refreshShell({ preserveConversation: true, background: true });
    }, 6000);
}

function bindEvents() {
    document.addEventListener('click', (event) => {
        if (!event.target.closest('#board-context-menu')) sidebar.closeBoardContextMenu();
        if (!event.target.closest('#ticket-context-menu') && !event.target.closest('[data-kanban-menu]')) boards.closeTicketContextMenu();
        if (!event.target.closest('#thread-context-menu')) sidebar.closeThreadContextMenu();
        if (!event.target.closest('.development-run-popover') && !event.target.closest('[data-ticket-run-status]'))
            document.querySelector('.development-run-popover')?.remove();
        if (!event.target.closest('#scheduled-row-menu') && !event.target.closest('[data-scheduled-menu]')) automations.closeScheduledRowMenu();
    });
    window.addEventListener('resize', () => {
        if (el('model-dialog').open) threads_composer.positionModelDialog();
    });
    el('thread-rename-form').addEventListener('submit', renameThread);
    el('thread-form').addEventListener('submit', saveThread);
    el('composer-board-button').addEventListener('click', () => togglePicker('composer-board-button', 'composer-board-menu'));
    el('composer-ticket-button').addEventListener('click', () => togglePicker('composer-ticket-button', 'composer-ticket-menu'));
    el('composer-workflow-button').addEventListener('click', () => togglePicker('composer-workflow-button', 'composer-workflow-menu'));
    el('composer-permission-select').addEventListener('change', changeComposerPermission);
    el('composer-time-toggle').addEventListener('click', () => updateThreadTime(state.threadTime && !state.threadTime.paused ? 'pause' : 'play'));
    el('studio-composer').addEventListener('submit', threads_composer.sendPrompt);
    el('task-prompt').addEventListener('input', resizePrompt);
    el('task-prompt').addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            el('studio-composer').requestSubmit();
        }
    });
    el('model-button').addEventListener('click', threads_composer.openModelDialog);
    document
        .querySelectorAll('[data-model-pane]')
        .forEach((button) => button.addEventListener('click', () => threads_composer.openModelPane(button.dataset.modelPane)));
    document
        .querySelectorAll('[data-prompt-mode]')
        .forEach((button) => button.addEventListener('click', () => threads_composer.setRunMode(button.dataset.promptMode)));
    el('prompt-playwright-toggle').addEventListener('click', threads_composer.togglePromptPlaywright);
    el('model-submenu-back').addEventListener('click', threads_composer.closeModelPane);
    el('model-submenu-search').addEventListener('input', (event) => threads_composer.renderModelPaneChoices('model', event.target.value));
    el('mode-button').addEventListener('click', threads_composer.openModeDialog);
    el('mode-form').addEventListener('submit', threads_composer.applyRunMode);
    el('model-form').addEventListener('submit', (event) => event.preventDefault());
    el('attach-button').addEventListener('click', threads_composer.toggleComposerActionMenu);
    el('composer-action-menu')
        .querySelectorAll('[data-composer-action]')
        .forEach((button) => button.addEventListener('click', () => threads_composer.runComposerAttachmentAction(button.dataset.composerAction)));
    el('composer-skill-search').addEventListener('input', (event) => threads_composer.renderComposerSkillMenu(event.target.value));
    document.querySelectorAll('[data-context-source]').forEach((button) =>
        button.addEventListener('click', () => {
            state.contextSource = button.dataset.contextSource;
            threads_composer.renderContextSource(state.contextSource === 'project');
        })
    );
    el('context-manual-add').addEventListener('click', threads_composer.addManualContext);
    el('context-file-input').addEventListener('change', threads_composer.uploadContextFiles);
    threads_composer.bindComposerDrop();
    document.querySelectorAll('[data-dialog-close]').forEach((button) =>
        button.addEventListener('click', () => {
            button.closest('dialog')?.close('cancel');
        })
    );
    el('artifact-approve').addEventListener('click', () => threads_inspector.reviewArtifact('approved'));
    el('artifact-request-changes').addEventListener('click', () => threads_inspector.reviewArtifact('changes_requested'));
    el('stop-run-button').addEventListener('click', threads_composer.stopRun);
    el('thread-more-button').addEventListener('click', (event) => {
        event.stopPropagation();
        toggleThreadHeaderMenu();
    });
    el('thread-menu-details').addEventListener('click', () => {
        closeThreadHeaderMenu();
        openInspector();
    });
    el('thread-menu-clear-context').addEventListener('click', clearThreadContext);
    el('thread-menu-edit-time').addEventListener('click', openTimeDialog);
    el('time-play-button').addEventListener('click', () => updateThreadTime('play'));
    el('time-pause-button').addEventListener('click', () => updateThreadTime('pause'));
    el('time-reset-button').addEventListener('click', () => updateThreadTime('reset'));
    el('inspector-close').addEventListener('click', closeInspector);
    el('drawer-backdrop').addEventListener('click', closeDrawers);
    document.querySelectorAll('.inspector-tabs button').forEach((button) =>
        button.addEventListener('click', () => {
            state.currentTab = button.dataset.tab;
            if (state.currentTab !== 'changes') state.selectedChangePath = '';
            document.querySelectorAll('.inspector-tabs button').forEach((item) => item.classList.toggle('active', item === button));
            threads_inspector.renderInspector();
            if (state.currentTab === 'changes') threads_inspector.loadInspectorChanges();
        })
    );
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closePickerMenus();
            automations.closeAutomationImportMenu();
            automations.closeScheduledRowMenu();
            threads_composer.closeComposerActionMenu();
            sidebar.closeBoardContextMenu();
            boards.closeTicketContextMenu();
            document.querySelector('.development-run-popover')?.remove();
            if (el('model-dialog').open) el('model-dialog').close('cancel');
            closeDrawers();
        }
    });
    document.addEventListener('click', (event) => {
        if (!event.target.closest('.composer-picker')) closePickerMenus();
        if (!event.target.closest('#scheduled-import-picker')) automations.closeAutomationImportMenu();
        if (!event.target.closest('#composer-action-menu') && !event.target.closest('#attach-button')) threads_composer.closeComposerActionMenu();
        if (!event.target.closest('#thread-header-menu') && !event.target.closest('#thread-more-button')) closeThreadHeaderMenu();
        if (el('model-dialog').open && !event.target.closest('#model-dialog') && !event.target.closest('#model-button')) el('model-dialog').close('cancel');
    });
    window.addEventListener('popstate', () => {
        if (state.ready) restoreDevelopmentLocation({ fromHistory: true });
    });
}

const sidebar = createSidebar({
    context: {
        get archivedChats() {
            return state.archivedChats;
        },
        get automations() {
            return state.automations;
        },
        get boards() {
            return state.boards;
        },
        get chats() {
            return state.chats;
        },
        get currentChat() {
            return state.currentChat;
        },
        get selectedBoardKey() {
            return state.selectedBoardKey;
        },
        set selectedBoardKey(value) {
            state.selectedBoardKey = value;
        },
        get workflows() {
            return state.workflows;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        boardByKey: (...args) => boardByKey(...args),
        boardForProject: (...args) => boardForProject(...args),
        boardSupportsKanban: (...args) => boardSupportsKanban(...args),
        closeDrawers: (...args) => closeDrawers(...args),
        confirmAction: (...args) => confirmAction(...args),
        deleteThread: (...args) => deleteThread(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        forceRefreshKanbanBoard: (...args) => boards.forceRefreshKanbanBoard(...args),
        getKanbanViewMode: (...args) => boards.getKanbanViewMode(...args),
        getTerminalSessions: (...args) => terminals.getTerminalSessions(...args),
        incomingConversations: (...args) => incoming.incomingConversations(...args),
        loadChat: (...args) => loadChat(...args),
        loadProjectTerminalSessions: (...args) => terminals.loadProjectTerminalSessions(...args),
        openBoardFromSidebar: (...args) => openBoardFromSidebar(...args),
        openBoardKanban: (...args) => openBoardKanban(...args),
        openBoardPlan: (...args) => openBoardPlan(...args),
        openBoardTerminals: (...args) => openBoardTerminals(...args),
        openKanbanTicketDialog: (...args) => boards.openKanbanTicketDialog(...args),
        openRenameThreadDialog: (...args) => openRenameThreadDialog(...args),
        openSidebar: (...args) => openSidebar(...args),
        openThreadDialog: (...args) => openThreadDialog(...args),
        paintTerminalProjectGrid: (...args) => terminals.paintTerminalProjectGrid(...args),
        projectById: (...args) => projectById(...args),
        providerName: (...args) => providerName(...args),
        refreshIncomingData: (...args) => incoming.refreshIncomingData(...args),
        refreshShell: (...args) => refreshShell(...args),
        setKanbanViewMode: (...args) => boards.setKanbanViewMode(...args),
        setWorkspaceMode: (...args) => setWorkspaceMode(...args),
        showEmptyTask: (...args) => showEmptyTask(...args),
        startBoardDevelopment: (...args) => startBoardDevelopment(...args),
        startNewDevelopmentFromSidebar: (...args) => startNewDevelopmentFromSidebar(...args),
        systemFileManagerLabel: (...args) => systemFileManagerLabel(...args),
        threadRowContentHtml: (...args) => threadRowContentHtml(...args),
        toast: (...args) => toast(...args),
        unlinkIncomingChannel: (...args) => incoming.unlinkIncomingChannel(...args),
        waitForRouteReady: (...args) => waitForRouteReady(...args)
    },
    el
});

const automations = createAutomations({
    context: {
        get archivedChats() {
            return state.archivedChats;
        },
        get automations() {
            return state.automations;
        },
        set automations(value) {
            state.automations = value;
        },
        get boards() {
            return state.boards;
        },
        get chats() {
            return state.chats;
        },
        get currentChat() {
            return state.currentChat;
        },
        get providers() {
            return state.providers;
        },
        get workflows() {
            return state.workflows;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        catalogModels: (...args) => catalogModels(args[0], state.modelCatalogs),
        confirmAction: (...args) => confirmAction(...args),
        developmentInstruction: (...args) => threads_composer.developmentInstruction(...args),
        ensureProviderCatalogs: (...args) => ensureProviderCatalogs(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        formatDateTime: (...args) => formatDateTime(...args),
        loadChat: (...args) => loadChat(...args),
        providerLabel: (...args) => providerLabel(args[0], state.providers),
        refreshShell: (...args) => refreshShell(...args),
        statusLabel: (...args) => statusLabel(...args),
        threadBoard: (...args) => sidebar.threadBoard(...args),
        toast: (...args) => toast(...args)
    },
    el
});

const incoming = createIncoming({
    context: {
        get attachments() {
            return state.attachments;
        },
        set attachments(value) {
            state.attachments = value;
        },
        get boardTickets() {
            return state.boardTickets;
        },
        get boards() {
            return state.boards;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        confirmAction: (...args) => confirmAction(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        formatDateTime: (...args) => formatDateTime(...args),
        loadBoardTickets: (...args) => loadBoardTickets(...args),
        loadChat: (...args) => loadChat(...args),
        openBoardKanban: (...args) => openBoardKanban(...args),
        openKanbanTicketDialog: (...args) => boards.openKanbanTicketDialog(...args),
        refreshShell: (...args) => refreshShell(...args),
        renderAttachments: (...args) => threads_composer.renderAttachments(...args),
        renderSidebar: (...args) => sidebar.renderSidebar(...args),
        resizePrompt: (...args) => resizePrompt(...args),
        toast: (...args) => toast(...args)
    },
    el
});

const workflows = createWorkflows({
    context: {
        get boards() {
            return state.boards;
        },
        get projects() {
            return state.projects;
        },
        get providers() {
            return state.providers;
        },
        get runs() {
            return state.runs;
        },
        set runs(value) {
            state.runs = value;
        },
        get skills() {
            return state.skills;
        },
        get workflows() {
            return state.workflows;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        catalogModels: (...args) => catalogModels(args[0], state.modelCatalogs),
        confirmAction: (...args) => confirmAction(...args),
        ensureProviderCatalogs: (...args) => ensureProviderCatalogs(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        loadChat: (...args) => loadChat(...args),
        refreshShell: (...args) => refreshShell(...args),
        runForWorkflow: (...args) => runForWorkflow(...args),
        statusLabel: (...args) => statusLabel(...args),
        toast: (...args) => toast(...args)
    },
    el
});

const boards = createBoards({
    context: {
        get archivedChats() {
            return state.archivedChats;
        },
        set archivedChats(value) {
            state.archivedChats = value;
        },
        get attachments() {
            return state.attachments;
        },
        set attachments(value) {
            state.attachments = value;
        },
        get boardDetails() {
            return state.boardDetails;
        },
        get boardTickets() {
            return state.boardTickets;
        },
        get chats() {
            return state.chats;
        },
        set chats(value) {
            state.chats = value;
        },
        get currentChat() {
            return state.currentChat;
        },
        set currentChat(value) {
            state.currentChat = value;
        },
        get currentRun() {
            return state.currentRun;
        },
        set currentRun(value) {
            state.currentRun = value;
        },
        get currentWorkflow() {
            return state.currentWorkflow;
        },
        set currentWorkflow(value) {
            state.currentWorkflow = value;
        },
        get selectedBoardKey() {
            return state.selectedBoardKey;
        }
    },
    actions: {
        api: (...args) => api(...args),
        boardByKey: (...args) => boardByKey(...args),
        cacheBoardDetail: (...args) => cacheBoardDetail(...args),
        closeBoardContextMenu: (...args) => sidebar.closeBoardContextMenu(...args),
        confirmAction: (...args) => confirmAction(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        loadBoardTickets: (...args) => loadBoardTickets(...args),
        loadChat: (...args) => loadChat(...args),
        openBoardContextMenu: (...args) => sidebar.openBoardContextMenu(...args),
        openBoardLinkDialog: (...args) => sidebar.openBoardLinkDialog(...args),
        providerName: (...args) => providerName(...args),
        renderAttachments: (...args) => threads_composer.renderAttachments(...args),
        renderSidebar: (...args) => sidebar.renderSidebar(...args),
        resizePrompt: (...args) => resizePrompt(...args),
        startBoardDevelopment: (...args) => startBoardDevelopment(...args),
        statusLabel: (...args) => statusLabel(...args),
        syncTicketSelect: (...args) => syncTicketSelect(...args),
        toast: (...args) => toast(...args)
    },
    el
});

const terminals = createTerminals({
    context: {
        get boards() {
            return state.boards;
        },
        get projects() {
            return state.projects;
        },
        get selectedBoardKey() {
            return state.selectedBoardKey;
        },
        set selectedBoardKey(value) {
            state.selectedBoardKey = value;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        boardByKey: (...args) => boardByKey(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        projectById: (...args) => projectById(...args),
        renderSidebar: (...args) => sidebar.renderSidebar(...args),
        setDevelopmentLocation: (...args) => setDevelopmentLocation(...args),
        setWorkspaceMode: (...args) => setWorkspaceMode(...args),
        terminalActionApi: (...args) => terminalActionApi(...args),
        toast: (...args) => toast(...args)
    },
    el,
    token
});

const threads_transcript = createThreadsTranscript({
    context: {
        get currentChat() {
            return state.currentChat;
        },
        get currentRun() {
            return state.currentRun;
        },
        get skills() {
            return state.skills;
        }
    },
    actions: {
        bindConversationActions: (...args) => bindConversationActions(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        formatSeconds: (...args) => formatSeconds(...args),
        statusLabel: (...args) => statusLabel(...args)
    },
    el,
    token
});

const threads_composer = createThreadsComposer({
    context: {
        get attachments() {
            return state.attachments;
        },
        set attachments(value) {
            state.attachments = value;
        },
        get boards() {
            return state.boards;
        },
        get busy() {
            return state.busy;
        },
        set busy(value) {
            state.busy = value;
        },
        get chats() {
            return state.chats;
        },
        get contextSource() {
            return state.contextSource;
        },
        set contextSource(value) {
            state.contextSource = value;
        },
        get controlState() {
            return state.controlState;
        },
        get currentChat() {
            return state.currentChat;
        },
        get currentRun() {
            return state.currentRun;
        },
        get currentWorkflow() {
            return state.currentWorkflow;
        },
        get draft() {
            return state.draft;
        },
        get editingCommandId() {
            return state.editingCommandId;
        },
        set editingCommandId(value) {
            state.editingCommandId = value;
        },
        get inbox() {
            return state.inbox;
        },
        get modelCatalogs() {
            return state.modelCatalogs;
        },
        set modelCatalogs(value) {
            state.modelCatalogs = value;
        },
        get modelMenuPane() {
            return state.modelMenuPane;
        },
        set modelMenuPane(value) {
            state.modelMenuPane = value;
        },
        get projectContext() {
            return state.projectContext;
        },
        set projectContext(value) {
            state.projectContext = value;
        },
        get providers() {
            return state.providers;
        },
        set providers(value) {
            state.providers = value;
        },
        get ready() {
            return state.ready;
        },
        get routeLoading() {
            return state.routeLoading;
        },
        get routingAssessment() {
            return state.routingAssessment;
        },
        set routingAssessment(value) {
            state.routingAssessment = value;
        },
        get selectedBoardKey() {
            return state.selectedBoardKey;
        },
        set selectedBoardKey(value) {
            state.selectedBoardKey = value;
        },
        get selectedProjectId() {
            return state.selectedProjectId;
        },
        get skills() {
            return state.skills;
        },
        get usePlaywright() {
            return state.usePlaywright;
        },
        set usePlaywright(value) {
            state.usePlaywright = value;
        },
        get workspaceMode() {
            return state.workspaceMode;
        }
    },
    actions: {
        api: (...args) => api(...args),
        boardByKey: (...args) => boardByKey(...args),
        confirmAction: (...args) => confirmAction(...args),
        developmentRoute: (...args) => developmentRoute(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        isActiveAgentRun: (...args) => isActiveAgentRun(...args),
        loadChat: (...args) => loadChat(...args),
        openBoardFromSidebar: (...args) => openBoardFromSidebar(...args),
        projectById: (...args) => projectById(...args),
        refreshShell: (...args) => refreshShell(...args),
        renderInspector: (...args) => threads_inspector.renderInspector(...args),
        renderSidebar: (...args) => sidebar.renderSidebar(...args),
        resizePrompt: (...args) => resizePrompt(...args),
        skillIconSvg: (...args) => threads_transcript.skillIconSvg(...args),
        syncComposerAvailability: (...args) => syncComposerAvailability(...args),
        toast: (...args) => toast(...args),
        workflowRunId: (...args) => workflowRunId(...args)
    },
    el,
    token
});

const threads_inspector = createThreadsInspector({
    context: {
        get activeArtifactIndex() {
            return state.activeArtifactIndex;
        },
        set activeArtifactIndex(value) {
            state.activeArtifactIndex = value;
        },
        get artifacts() {
            return state.artifacts;
        },
        get automations() {
            return state.automations;
        },
        get changeReview() {
            return state.changeReview;
        },
        set changeReview(value) {
            state.changeReview = value;
        },
        get changeReviewLoading() {
            return state.changeReviewLoading;
        },
        set changeReviewLoading(value) {
            state.changeReviewLoading = value;
        },
        get controlState() {
            return state.controlState;
        },
        get currentChat() {
            return state.currentChat;
        },
        get currentRun() {
            return state.currentRun;
        },
        get currentTab() {
            return state.currentTab;
        },
        set currentTab(value) {
            state.currentTab = value;
        },
        get currentWorkflow() {
            return state.currentWorkflow;
        },
        get doctor() {
            return state.doctor;
        },
        set doctor(value) {
            state.doctor = value;
        },
        get draft() {
            return state.draft;
        },
        get editingCommandId() {
            return state.editingCommandId;
        },
        set editingCommandId(value) {
            state.editingCommandId = value;
        },
        get plans() {
            return state.plans;
        },
        set plans(value) {
            state.plans = value;
        },
        get selectedChangePath() {
            return state.selectedChangePath;
        },
        set selectedChangePath(value) {
            state.selectedChangePath = value;
        }
    },
    actions: {
        actionLabel: (...args) => threads_composer.actionLabel(...args),
        api: (...args) => api(...args),
        closeInspector: (...args) => closeInspector(...args),
        continueRun: (...args) => threads_composer.continueRun(...args),
        copyText: (...args) => copyText(...args),
        escapeHtml: (...args) => escapeHtml(...args),
        formatDateTime: (...args) => formatDateTime(...args),
        gitStatusEntries: (...args) => threads_transcript.gitStatusEntries(...args),
        messageTime: (...args) => threads_transcript.messageTime(...args),
        openAutomationDialog: (...args) => automations.openAutomationDialog(...args),
        openModelDialog: (...args) => threads_composer.openModelDialog(...args),
        refreshShell: (...args) => refreshShell(...args),
        resizePrompt: (...args) => resizePrompt(...args),
        resolveInteraction: (...args) => threads_composer.resolveInteraction(...args),
        runAutomation: (...args) => automations.runAutomation(...args),
        safeArtifactUri: (...args) => safeArtifactUri(...args),
        selectWorkflow: (...args) => workflows.selectWorkflow(...args),
        setWorkspaceMode: (...args) => setWorkspaceMode(...args),
        startWorkflow: (...args) => threads_composer.startWorkflow(...args),
        statusLabel: (...args) => statusLabel(...args),
        toast: (...args) => toast(...args),
        toggleAutomation: (...args) => automations.toggleAutomation(...args),
        undoTurnChanges: (...args) => undoTurnChanges(...args),
        workflowStepList: (...args) => workflows.workflowStepList(...args)
    },
    el,
    token
});

const reports = createReports({ api, root: el('reports-workspace'), escapeHtml });

const planning = createPlanning({
    api,
    escapeHtml,
    openBoardPlan,
    openHome: () => setWorkspaceMode('plan'),
    pathForBoard: (boardKey) => developmentBoardPath(boardKey, 'plan'),
    toast
});

installNavigationGuard(planning, el('plan-root'), document, window);
sidebar.bindEvents();
automations.bindEvents();
incoming.bindEvents();
workflows.bindEvents();
boards.bindEvents();
terminals.bindEvents();

init();
