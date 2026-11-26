/**
 * The chat box's two tabs: Chat first, Library second.
 *
 * The library's contents are screen-aware, so the tab has to know *where the
 * operator is*. That is derived from the plan conversation's own state rather
 * than guessed:
 *
 *   Interfaces tab, a page selected   -> 'page'      (widgets)
 *   Interfaces tab, nothing selected  -> 'sitemap'   (page templates)
 *   any other tab                     -> 'page'      (widgets)
 *
 * The DOM wiring is deliberately small; the decisions are pure functions so they
 * can be tested without a browser.
 */

export const PANEL_TABS = [
    { id: 'chat', label: 'Chat', help: 'Talk about the plan' },
    { id: 'library', label: 'Library', help: 'Widgets and page templates' },
];

const TAB_IDS = PANEL_TABS.map((tab) => tab.id);

/** Which library screen the operator is on. Never throws. */
export function libraryScreenFor(session) {
    try {
        const tab = String((session && session.tab) || '').toLowerCase();
        if (tab !== 'wireframes') return 'page';
        const selection = (session && session.selection) || {};
        const chosen = selection.wireframes;
        // DISCOVERED / empty means the sitemap view, not a single page.
        if (chosen === undefined || chosen === null || chosen === '' || chosen === 'discovered') return 'sitemap';
        return 'page';
    } catch {
        return 'page';
    }
}

export function normalizePanelTab(tab) {
    const key = String(tab || '').toLowerCase();
    return TAB_IDS.includes(key) ? key : 'chat';
}

/** The tab strip. Chat is first, Library second, and the active one is marked. */
export function panelTabsHtml(active) {
    const current = normalizePanelTab(active);
    return PANEL_TABS.map((tab) => (
        `<button type="button" role="tab" data-panel-tab="${tab.id}"`
        + ` title="${tab.help}"`
        + ` aria-selected="${tab.id === current ? 'true' : 'false'}"`
        + ` class="plan-panel-tab${tab.id === current ? ' active' : ''}">${tab.label}</button>`
    )).join('');
}

/**
 * Show the active tab and hide the other.
 *
 * The chat body is not rebuilt - it is only hidden - so switching tabs never
 * loses a half-typed message or scroll position.
 */
export function applyPanelTab(root, tab) {
    const current = normalizePanelTab(tab);
    if (!root || !root.querySelector) return current;
    const chat = root.querySelector('#plan-chat-body');
    const library = root.querySelector('#plan-library');
    if (chat) chat.hidden = current !== 'chat';
    if (library) library.hidden = current !== 'library';
    for (const button of root.querySelectorAll('[data-panel-tab]')) {
        const isActive = button.dataset.panelTab === current;
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        button.classList.toggle('active', isActive);
    }
    return current;
}

/**
 * Wire the tabs. Returns {setTab, getTab, refreshScreen}.
 *
 * onLibrary(screen) is called whenever the Library tab is shown, so the caller
 * loads the right entries for the current screen.
 */
export function mountPlanPanelTabs(root, { session, onLibrary = () => {}, initial = 'chat' } = {}) {
    let current = normalizePanelTab(initial);

    function setTab(tab) {
        current = applyPanelTab(root, tab);
        if (current === 'library') onLibrary(libraryScreenFor(session));
        return current;
    }

    function refreshScreen() {
        if (current === 'library') onLibrary(libraryScreenFor(session));
    }

    for (const button of (root && root.querySelectorAll ? root.querySelectorAll('[data-panel-tab]') : [])) {
        button.addEventListener('click', () => setTab(button.dataset.panelTab));
    }
    applyPanelTab(root, current);

    return { setTab, getTab: () => current, refreshScreen };
}
