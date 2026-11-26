/**
 * The planning side panel: one panel that swaps between the chat and the library.
 *
 * Its contents are screen-aware - page templates at sitemap/flow level, widgets
 * inside a page - and entries are draggable onto the canvas. The drag itself is
 * the canvas's job: this module only announces what was picked, so the library
 * and the canvas stay independent.
 */

import { renderWireframe } from './wireframe.js';
import { mountPlanPreview } from './plan-preview.js';

export const MODES = ['chat', 'library'];

/** Which library kind a screen should show. Unknown screens behave like a page. */
const SCREEN_KIND = {
    sitemap: 'page-template',
    flow: 'page-template',
    flows: 'page-template',
    page: 'widget',
    wireframe: 'widget',
};

export function screenKind(screen) {
    return SCREEN_KIND[String(screen || '').toLowerCase()] || 'widget';
}

export function normalizeScreen(screen) {
    const key = String(screen || '').toLowerCase();
    return key in SCREEN_KIND ? key : 'page';
}

/** Swap the panel's mode. Returns the new mode; an unknown mode is refused. */
export function toggleMode(current, wanted) {
    return MODES.includes(wanted) ? wanted : (MODES.includes(current) ? current : 'chat');
}

export function libraryUrl(baseUrl, workspaceId, screen, kind) {
    const root = String(baseUrl || '').replace(/\/+$/, '');
    const params = new URLSearchParams({ screen: normalizeScreen(screen) });
    if (kind) params.set('kind', kind);
    return `${root}/workflows/studio/plan-workspaces/${encodeURIComponent(workspaceId)}/library?${params}`;
}

/**
 * Load the panel's entries for a screen. Never throws: a failed load is an
 * empty library plus a message, so the panel can say so instead of breaking.
 */
export async function loadLibrary(baseUrl, workspaceId, screen, { fetchImpl, kind } = {}) {
    const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
    if (!doFetch) return { entries: [], screen: normalizeScreen(screen), error: 'no fetch' };
    try {
        const response = await doFetch(libraryUrl(baseUrl, workspaceId, screen, kind));
        if (!response || !response.ok) {
            return { entries: [], screen: normalizeScreen(screen), error: `HTTP ${response ? response.status : '?'}` };
        }
        const body = await response.json();
        const entries = Array.isArray(body && body.entries) ? body.entries : [];
        return {
            entries,
            screen: (body && body.screen) || normalizeScreen(screen),
            product: (body && body.product) || '',
            error: '',
        };
    } catch (err) {
        return { entries: [], screen: normalizeScreen(screen), error: String((err && err.message) || err) };
    }
}

/** Group entries by kind, then by their first tag, for a scannable list. */
export function groupEntries(entries) {
    const byKind = new Map();
    for (const entry of Array.isArray(entries) ? entries : []) {
        if (!entry || !entry.name) continue;
        const kind = entry.kind || 'widget';
        if (!byKind.has(kind)) byKind.set(kind, new Map());
        const tags = Array.isArray(entry.tags) ? entry.tags : [];
        const group = tags.length ? tags[0] : 'general';
        const byTag = byKind.get(kind);
        if (!byTag.has(group)) byTag.set(group, []);
        byTag.get(group).push(entry);
    }
    return [...byKind.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([kind, byTag]) => ({
            kind,
            groups: [...byTag.entries()]
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([tag, items]) => ({
                    tag,
                    items: [...items].sort((a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name))),
                })),
        }));
}

export const DRAG_MIME = 'application/x-decisions-plan-entry';

/**
 * Mount the panel. Returns {setMode, setScreen, reload, destroy}.
 *
 * Drag: the entry is put on the dataTransfer under DRAG_MIME *and* announced via
 * onPick, so a canvas can accept it either way.
 */
export function mountLibraryPanel(container, options = {}) {
    const {
        workspaceId,
        baseUrl = '',
        screen = 'page',
        mode = 'chat',
        onPick = () => {},
        fetchImpl,
    } = options;

    let currentMode = toggleMode('chat', mode);
    let currentScreen = normalizeScreen(screen);
    let last = { entries: [], error: '' };

    const root = container;
    root.innerHTML = '';
    root.classList.add('plan-library-panel');

    const status = document.createElement('p');
    status.className = 'plan-library-status';
    status.setAttribute('role', 'status');

    const preview = document.createElement('div');
    preview.className = 'plan-library-preview';

    const list = document.createElement('ul');
    list.className = 'plan-library-list';
    list.setAttribute('aria-label', 'Library');

    root.append(status, preview, list);

    // The plan itself, rendered through the same renderer the canvas uses and
    // with the base template's CSS, so what you see is governed by the real
    // tokens, contracts and grid.
    let planPreview = null;
    try {
        planPreview = mountPlanPreview(preview, { renderWireframe });
    } catch (err) {
        console.warn('[plan] preview unavailable', err);
    }

    function paintTabs() {
        // The tab strip lives in the chat header (see renderChatHeader); the panel
        // only records which one is active.
        root.dataset.mode = currentMode;
    }

    function paintList() {
        list.innerHTML = '';
        if (last.error) {
            status.textContent = `Library unavailable (${last.error})`;
            return;
        }
        if (!last.entries.length) {
            status.textContent = currentScreen === 'sitemap' || currentScreen === 'flow'
                ? 'No page templates in the library.'
                : 'No widgets in the library.';
            return;
        }
        status.textContent = `${last.entries.length} entr${last.entries.length === 1 ? 'y' : 'ies'}`;
        for (const { kind, groups } of groupEntries(last.entries)) {
            const kindHeading = document.createElement('li');
            kindHeading.className = 'plan-library-kind';
            kindHeading.textContent = kind;
            list.append(kindHeading);
            for (const group of groups) {
                for (const entry of group.items) {
                    const item = document.createElement('li');
                    item.className = 'plan-library-entry';
                    item.draggable = true;
                    item.dataset.name = entry.name;
                    item.dataset.kind = entry.kind || kind;
                    item.title = entry.description || entry.title || entry.name;
                    item.textContent = entry.title || entry.name;
                    item.addEventListener('dragstart', (event) => {
                        try {
                            event.dataTransfer.setData(DRAG_MIME, JSON.stringify(entry));
                            event.dataTransfer.effectAllowed = 'copy';
                        } catch { /* a canvas may still use onPick */ }
                        onPick(entry);
                    });
                    list.append(item);
                }
            }
        }
    }

    async function reload() {
        if (currentMode !== 'library') return last;
        status.textContent = 'Loading…';
        last = await loadLibrary(baseUrl, workspaceId, currentScreen, { fetchImpl });
        paintList();
        return last;
    }

    function setMode(next) {
        currentMode = toggleMode(currentMode, next);
        paintTabs();
        if (currentMode === 'library') reload();
        else { status.textContent = ''; }
    }

    function setScreen(next) {
        currentScreen = normalizeScreen(next);
        if (currentMode === 'library') reload();
    }

    paintTabs();
    if (currentMode === 'library') reload();

    return {
        setMode,
        setScreen,
        reload,
        setPlan: (plan) => { try { planPreview?.setPlan(plan); } catch (err) { console.warn('[plan] preview failed', err); } },
        getMode: () => currentMode,
        getScreen: () => currentScreen,
        getEntries: () => last.entries,
        destroy() { root.innerHTML = ''; },
    };
}
