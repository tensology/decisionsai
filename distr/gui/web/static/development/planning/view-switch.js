// Per-board view switcher shown inside the shared .task-breadcrumb: Kanban / Plan / last thread / terminals.
// The last thread per board is browser UI state (localStorage), never a server setting.

export const LAST_THREAD_STORE = 'development_board_last_thread';
export const LAST_SECTION_STORE = 'development_board_last_section';
const SECTIONS = new Set(['kanban', 'plan', 'thread']);

function readAll(storage, key = LAST_THREAD_STORE) {
    try { return JSON.parse(storage?.getItem(key) || '{}') || {}; } catch { return {}; }
}

export function rememberBoardThread(storage, boardKey, chatId) {
    const id = Number(chatId || 0);
    if (!storage || !boardKey || !id) return;
    const all = readAll(storage);
    if (Number(all[boardKey]) === id) return;
    all[boardKey] = id;
    try { storage.setItem(LAST_THREAD_STORE, JSON.stringify(all)); } catch { /* storage full or blocked */ }
}

// Last main section per board: kanban, plan, or thread. Browser UI state only.
export function rememberBoardSection(storage, boardKey, section) {
    if (!storage || !boardKey || !SECTIONS.has(section)) return;
    const all = readAll(storage, LAST_SECTION_STORE);
    if (all[boardKey] === section) return;
    all[boardKey] = section;
    try { storage.setItem(LAST_SECTION_STORE, JSON.stringify(all)); } catch { /* storage full or blocked */ }
}

export function boardLastSection(storage, boardKey) {
    if (!boardKey) return '';
    const section = readAll(storage, LAST_SECTION_STORE)[boardKey];
    return SECTIONS.has(section) ? section : '';
}

// chats: active (non-archived) threads, newest first. boardOf(chat) -> board key.
// Stored thread if still active on this board, else the newest active thread on it, else 0.
export function boardLastThread(storage, boardKey, chats, boardOf) {
    if (!boardKey) return 0;
    const onBoard = (chats || []).filter((chat) => !chat.archived && boardOf(chat) === boardKey);
    const stored = Number(readAll(storage)[boardKey] || 0);
    if (stored && onBoard.some((chat) => Number(chat.id) === stored)) return stored;
    return Number(onBoard[0]?.id || 0);
}

// Same 16px stroke icon style as .icon-button; the Plan glyph is the old Kanban-header Plan icon.
// Terminals uses the same glyph as the board context-menu "Open" terminals item.
export const VIEW_ICONS = {
    kanban: '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="3" height="10" rx=".8"/><rect x="6.5" y="3" width="3" height="7" rx=".8"/><rect x="10.5" y="3" width="3" height="5" rx=".8"/></svg>',
    plan: '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M4.5 4h7M4.5 8h7M4.5 12h4.5"/><path d="M3 4h.5M3 8h.5M3 12h.5"/></svg>',
    thread: '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/></svg>',
    terminals: '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3.5" width="11" height="9" rx="1"/><path d="M5 7l2 1.5L5 10M8.5 10.5h2.5"/></svg>',
};

export function boardViewOptions(current, threadId, options = {}) {
    const canOpenTerminals = options.canOpenTerminals !== false;
    return [
        { view: 'kanban', label: 'Kanban', active: current === 'kanban', disabled: false, title: 'Kanban' },
        { view: 'plan', label: 'Plan', active: current === 'plan', disabled: false, title: 'Planning' },
        { view: 'thread', label: 'Last thread', active: current === 'thread', disabled: !threadId,
          title: threadId ? 'Thread' : 'No threads on this board yet' },
        { view: 'terminals', label: 'Terminals', active: current === 'terminals', disabled: !canOpenTerminals,
          title: canOpenTerminals ? 'Terminal' : 'Add a project folder to this board first' },
    ];
}

// aria-disabled (not disabled) so the tooltip still shows on hover.
export function boardViewSwitchHtml(options) {
    return options.map((option) => `<button type="button" data-board-view="${option.view}" title="${option.title}" aria-label="${option.title}" aria-pressed="${option.active}"${option.disabled ? ' aria-disabled="true"' : ''} class="icon-button">${VIEW_ICONS[option.view]}</button>`).join('');
}

// Sidebar project click.
// Terminals stay on terminals. CLI priority applies only while a thread is open,
// so Kanban and Plan keep that section. Otherwise the current section, or the
// section last viewed on the clicked board.
// pickingNewThread: New Thread in the sidebar is waiting for a project; that
// click starts a scoped draft instead of falling through to Kanban.
export function projectClickTarget({
    workspaceMode = '',
    planBoardView = false,
    hasCurrentChat = false,
    cliOn = false,
    boardHasTerminalProject = false,
    boardSupportsKanban = false,
    boardHasPlanProject = false,
    lastSection = '',
    lastThreadId = 0,
    pickingNewThread = false
} = {}) {
    if (pickingNewThread) return { action: 'draft' };
    const current = workspaceMode === 'kanban'
        ? 'kanban'
        : workspaceMode === 'plan' && planBoardView
            ? 'plan'
            : workspaceMode === 'chat' && hasCurrentChat
                ? 'thread'
                : '';
    const threadId = Number(lastThreadId || 0);
    if ((workspaceMode === 'terminals' || workspaceMode === 'terminals_home') && boardHasTerminalProject) {
        return { action: 'terminals' };
    }
    if (cliOn && current === 'thread' && threadId) {
        return { action: 'cli-thread', chatId: threadId };
    }
    let section = current || lastSection;
    if (section === 'plan' && !boardHasPlanProject) section = '';
    if (section === 'kanban' && !boardSupportsKanban) section = '';
    if (section === 'thread') {
        if (threadId) return { action: 'thread', chatId: threadId };
        section = '';
    }
    if (section === 'plan') return { action: 'plan' };
    if ((section === 'kanban' || !section) && boardSupportsKanban) return { action: 'kanban' };
    return { action: 'draft' };
}
