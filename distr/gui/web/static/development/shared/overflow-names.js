// Shared overflow-name helper.
// Project names are never truncated (see overflow-names.css).
// Long thread titles and remaining breadcrumb segments (not the project name)
// scroll in a fixed-width box with a seamless, looping marquee when they overflow.

export const OVERFLOW_NAME_DEFAULTS = Object.freeze({
    // lists / cards: scroll on hover and keyboard focus
    listMode: 'hover',
    // active thread header and remaining breadcrumbs: gentle auto-scroll with a pause
    headerMode: 'auto',
    breadcrumbMode: 'auto',
    // portion of the cycle spent still at the start (duplicate makes the restart invisible)
    pausePortion: 0.12,
    gap: '2.5em',
    pxPerSecond: 36,
    minDurationSec: 6,
    maxDurationSec: 28,
    overflowTolerancePx: 1,
    // prefers-reduced-motion: no animation; full name stays in title / tooltip / aria
    reducedMotion: 'title-only',
});

export const PROJECT_NAME_SELECTORS = Object.freeze([
    '#header-project',
    '.project-select span',
    '.plan-project',
    '.terminal-project-titlebar strong',
    '#terminal-project-name',
    '.proposal-project',
    '.reports-chip',
    '.reports-block > b',
    '.reports-pie li span',
    '.reports-project-legend-row > span',
    '.reports-project-option > span',
    '.reports-detail-project',
    '.reports-day-project > h3',
    '#development-kanban-board',
]);

// Thread / breadcrumb (non-project) titles that receive the shared marquee.
export const MARQUEE_TARGETS = Object.freeze([
    { selector: '#header-task', mode: 'auto', kind: 'breadcrumb-thread' },
    { selector: '#development-task-header .task-breadcrumb strong[data-plan-crumb]', mode: 'auto', kind: 'breadcrumb' },
    { selector: '#development-kanban-view-label', mode: 'auto', kind: 'breadcrumb' },
    { selector: '.task-title', mode: 'hover', kind: 'thread-list', wrapText: true },
    { selector: '.development-kanban-card-title > strong', mode: 'hover', kind: 'kanban-card' },
    { selector: '.plan-chat-title', mode: 'auto', kind: 'plan-header' },
    { selector: '.plan-chat-menu-title', mode: 'hover', kind: 'plan-menu' },
    { selector: '.incoming-row-title strong', mode: 'hover', kind: 'incoming' },
    { selector: '.incoming-proposal-main strong', mode: 'hover', kind: 'incoming' },
    { selector: '.reports-day-copy', mode: 'hover', kind: 'reports' },
    { selector: '.reports-block span', mode: 'hover', kind: 'reports' },
]);

const SKIP_CHILD = '.pin-mark, .task-run-state, .name-marquee, [data-laya-badge], .laya-thread-badge';

export function marqueeDistancePx(textWidth, gapPx) {
    const width = Number(textWidth) || 0;
    const gap = Number(gapPx) || 0;
    return width > 0 ? width + gap : 0;
}

export function marqueeDurationSec(distancePx, options = OVERFLOW_NAME_DEFAULTS) {
    const distance = Math.max(0, Number(distancePx) || 0);
    const speed = Number(options.pxPerSecond) || OVERFLOW_NAME_DEFAULTS.pxPerSecond;
    const min = Number(options.minDurationSec) || OVERFLOW_NAME_DEFAULTS.minDurationSec;
    const max = Number(options.maxDurationSec) || OVERFLOW_NAME_DEFAULTS.maxDurationSec;
    if (distance <= 0 || speed <= 0) return min;
    return Math.min(max, Math.max(min, distance / speed));
}

export function isOverflowing(scrollWidth, clientWidth, tolerance = OVERFLOW_NAME_DEFAULTS.overflowTolerancePx) {
    return (Number(scrollWidth) || 0) > (Number(clientWidth) || 0) + (Number(tolerance) || 0);
}

function reducedMotion(doc) {
    const view = doc?.defaultView;
    return Boolean(view?.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
}

function fullTextOf(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.textContent || '';
    const clone = node.cloneNode(true);
    clone.querySelectorAll?.('.name-marquee__text[aria-hidden="true"], .pin-mark, .task-run-state, [data-laya-badge], .laya-thread-badge')?.forEach((el) => el.remove());
    // Prefer live visible text. data-full-name is only a fallback: a later
    // host.textContent write leaves the stale attribute in place and would
    // otherwise restore "No thread selected" after the title was set.
    const first = clone.querySelector?.('.name-marquee__text');
    const live = first ? (first.textContent || '').trim() : '';
    if (live) return live;
    const direct = (clone.textContent || '').replace(/\s+/g, ' ').trim();
    if (direct) return direct;
    return String(node.getAttribute?.('data-full-name') || '').trim();
}

export function setOverflowLabel(host, text) {
    if (!host) return;
    const label = String(text ?? '');
    const first = host.querySelector?.(':scope .name-marquee__text:not([aria-hidden])')
        || host.querySelector?.(':scope .name-marquee__text');
    if (first) { if (first.textContent !== label) first.textContent = label; }
    else host.textContent = label;
    const trimmed = label.replace(/\s+/g, ' ').trim();
    if (trimmed) {
        host.setAttribute('data-full-name', trimmed);
        host.setAttribute('title', trimmed);
    } else {
        host.removeAttribute('data-full-name');
        host.removeAttribute('title');
    }
    refreshOverflowNames(host);
}

function setNameAttrs(el, text) {
    const name = String(text || '').trim();
    if (!name) return;
    if (el.getAttribute('data-full-name') !== name) el.setAttribute('data-full-name', name);
    if (el.getAttribute('title') !== name) el.setAttribute('title', name);
    // Don't override an explicit control label on a button / link host.
    const host = el.closest('button, a, [role="button"], [role="link"]') || el;
    if (host === el && el.getAttribute('aria-label') !== name && (!host.hasAttribute('aria-label') || host.dataset.nameMarqueeAria === '1')) {
        el.dataset.nameMarqueeAria = '1';
        el.setAttribute('aria-label', name);
    }
}

function ensureStructure(host, text, { wrapText = false } = {}) {
    let shell = host.classList.contains('name-marquee')
        ? host
        : host.querySelector(':scope > .name-marquee');
    // textContent on an already-enhanced host wipes the track; recover the new label.
    if (shell === host && !host.querySelector(':scope > .name-marquee__viewport')) {
        const label = text || fullTextOf(host);
        host.textContent = '';
        text = label;
    } else if (wrapText && !shell) {
        shell = host.ownerDocument.createElement('span');
        const keep = [];
        const move = [];
        for (const child of [...host.childNodes]) {
            if (child.nodeType === 1 && child.matches?.(SKIP_CHILD)) keep.push(child);
            else move.push(child);
        }
        const fromMove = move.map((node) => fullTextOf(node)).join(' ').replace(/\s+/g, ' ').trim();
        const label = text || fromMove;
        move.forEach((node) => node.remove());
        host.append(shell);
        // pins / spinners already in `keep` stay as siblings
        void keep;
        text = label;
    } else if (!shell) {
        shell = host;
        const label = text || fullTextOf(host);
        host.textContent = '';
        text = label;
    }
    shell.classList.add('name-marquee');
    let viewport = shell.querySelector(':scope > .name-marquee__viewport');
    if (!viewport) {
        viewport = host.ownerDocument.createElement('span');
        viewport.className = 'name-marquee__viewport';
        const track = host.ownerDocument.createElement('span');
        track.className = 'name-marquee__track';
        const copy = host.ownerDocument.createElement('span');
        copy.className = 'name-marquee__text';
        track.append(copy);
        viewport.append(track);
        shell.append(viewport);
    }
    const track = viewport.querySelector(':scope > .name-marquee__track');
    const first = track.querySelector(':scope > .name-marquee__text:not([aria-hidden])')
        || track.querySelector(':scope > .name-marquee__text');
    const label = text || first?.textContent || '';
    if (first && first.textContent !== label) first.textContent = label;
    setNameAttrs(shell, label);
    if (shell !== host) setNameAttrs(host, label);
    return { shell, viewport, track, first, text: label };
}

function gapPxOf(track, doc) {
    const style = doc.defaultView?.getComputedStyle?.(track);
    const raw = style?.columnGap || style?.gap || '0px';
    const parsed = Number.parseFloat(raw);
    if (Number.isFinite(parsed) && String(raw).endsWith('px')) return parsed;
    // 2.5em fallback in px
    const font = Number.parseFloat(style?.fontSize || '12') || 12;
    return font * 2.5;
}

function syncShell(shell, viewport, track, first, options) {
    if (!shell || !viewport || !track || !first) return { overflowing: false, distance: 0 };
    const doc = shell.ownerDocument;
    const overflowing = isOverflowing(first.scrollWidth, viewport.clientWidth, options.overflowTolerancePx);
    if (shell.classList.contains('is-overflowing') !== overflowing) shell.classList.toggle('is-overflowing', overflowing);
    if (!overflowing || reducedMotion(doc)) {
        track.querySelectorAll('.name-marquee__text[aria-hidden="true"]').forEach((node) => node.remove());
        track.style.removeProperty('--name-marquee-distance');
        track.style.removeProperty('--name-marquee-duration');
        return { overflowing, distance: 0 };
    }
    let clone = track.querySelector(':scope > .name-marquee__text[aria-hidden="true"]');
    if (!clone) {
        clone = first.cloneNode(true);
        clone.setAttribute('aria-hidden', 'true');
        track.append(clone);
    } else if (clone.textContent !== first.textContent) {
        clone.textContent = first.textContent;
    }
    const distance = marqueeDistancePx(first.scrollWidth, gapPxOf(track, doc));
    const duration = marqueeDurationSec(distance, options);
    const nextDistance = `${Math.round(distance * 100) / 100}px`;
    const nextDuration = `${duration}s`;
    const prevDistance = track.style.getPropertyValue('--name-marquee-distance');
    const prevDuration = track.style.getPropertyValue('--name-marquee-duration');
    // Re-setting animation properties restarts the loop and looks like a snap.
    if (prevDistance !== nextDistance) track.style.setProperty('--name-marquee-distance', nextDistance);
    if (prevDuration !== nextDuration) track.style.setProperty('--name-marquee-duration', nextDuration);
    return { overflowing, distance, duration };
}

function enhance(host, target, options) {
    if (!host || host.closest?.('.name-marquee__viewport')) return null;
    const current = fullTextOf(host);
    const parts = ensureStructure(host, current, { wrapText: Boolean(target.wrapText) });
    // Only touch classes / data when they change: a remove + re-add of the
    // animation class on every refresh (once per second via the elapsed-time
    // tick) can restart the marquee.
    const auto = target.mode === 'auto';
    parts.shell.classList.toggle('name-marquee--auto', auto);
    parts.shell.classList.toggle('name-marquee--hover', !auto);
    const kind = target.kind || 'thread';
    if (parts.shell.dataset.nameMarquee !== kind) parts.shell.dataset.nameMarquee = kind;
    return syncShell(parts.shell, parts.viewport, parts.track, parts.first, options);
}

export function refreshOverflowNames(root = (typeof document !== 'undefined' ? document : null), options = OVERFLOW_NAME_DEFAULTS) {
    if (!root?.querySelectorAll) return [];
    const opts = { ...OVERFLOW_NAME_DEFAULTS, ...options };
    const results = [];
    for (const target of MARQUEE_TARGETS) {
        const scope = root.querySelectorAll ? root : root.ownerDocument;
        const base = root.querySelectorAll ? root : scope;
        const nodes = typeof root.matches === 'function' && root.matches(target.selector)
            ? [root]
            : [...base.querySelectorAll(target.selector)];
        for (const node of nodes) {
            results.push({ el: node, kind: target.kind, ...enhance(node, target, opts) });
        }
    }
    return results;
}

export function installOverflowNames(root = (typeof document !== 'undefined' ? document : null), options = OVERFLOW_NAME_DEFAULTS) {
    if (!root?.querySelectorAll) return { refresh() { return []; }, disconnect() {} };
    const doc = root.ownerDocument || root;
    const refresh = () => refreshOverflowNames(root, options);
    refresh();
    const view = doc.defaultView;
    let timer = 0;
    const schedule = () => {
        if (timer) view?.clearTimeout?.(timer);
        timer = view?.setTimeout?.(() => { timer = 0; refresh(); }, 40) || 0;
    };
    const observer = typeof MutationObserver === 'function'
        ? new MutationObserver((records) => {
            for (const record of records) {
                if (record.type === 'characterData' || record.addedNodes?.length || record.removedNodes?.length) {
                    // Ignore our own duplicate-text mutations.
                    const t = record.target;
                    if (t?.closest?.('.name-marquee__track') || t?.classList?.contains?.('name-marquee__text')) continue;
                    schedule();
                    return;
                }
            }
        })
        : null;
    observer?.observe(root.body || root, { subtree: true, childList: true, characterData: true });
    const ro = typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => schedule())
        : null;
    const shell = root.querySelector?.('#studio-shell') || root.body || root;
    ro?.observe?.(shell);
    view?.addEventListener?.('resize', schedule);
    doc.fonts?.ready?.then?.(refresh).catch?.(() => {});
    return {
        refresh,
        disconnect() {
            observer?.disconnect();
            ro?.disconnect?.();
            view?.removeEventListener?.('resize', schedule);
        },
    };
}

function autoInstall() {
    if (typeof document === 'undefined') return;
    const start = () => {
        if (!document.getElementById('development-task-header') && !document.getElementById('studio-shell')) return;
        if (document.documentElement.dataset.overflowNamesInstalled === '1') return;
        document.documentElement.dataset.overflowNamesInstalled = '1';
        installOverflowNames(document);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
}

autoInstall();
