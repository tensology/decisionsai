// Per-board view switcher shown inside the shared .task-breadcrumb: Kanban / Plan / last thread.
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
export const VIEW_ICONS = {
    kanban: '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="3" height="10" rx=".8"/><rect x="6.5" y="3" width="3" height="7" rx=".8"/><rect x="10.5" y="3" width="3" height="5" rx=".8"/></svg>',
    plan: '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M4.5 4h7M4.5 8h7M4.5 12h4.5"/><path d="M3 4h.5M3 8h.5M3 12h.5"/></svg>',
    thread: '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/></svg>',
};

export function boardViewOptions(current, threadId) {
    return [
        { view: 'kanban', label: 'Kanban', active: current === 'kanban', disabled: false, title: 'Kanban' },
        { view: 'plan', label: 'Plan', active: current === 'plan', disabled: false, title: 'Plan' },
        { view: 'thread', label: 'Last thread', active: current === 'thread', disabled: !threadId,
          title: threadId ? 'Last thread' : 'No threads on this board yet' },
    ];
}

// aria-disabled (not disabled) so the tooltip still shows on hover.
export function boardViewSwitchHtml(options) {
    return options.map((option) => `<button type="button" data-board-view="${option.view}" title="${option.title}" aria-label="${option.title}" aria-pressed="${option.active}"${option.disabled ? ' aria-disabled="true"' : ''} class="icon-button">${VIEW_ICONS[option.view]}</button>`).join('');
}
