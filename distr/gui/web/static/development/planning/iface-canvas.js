// Planning board. The host supplies the graph and persists edits and viewport state.
import { SHAPE_KIND, layoutIface, NODE } from './iface-model.js?v=20261125-canvas-workspace';
import { shapeGlyph } from './flow-nodes.js?v=20261103-ifaces3';
import { esc, availableBottom } from './canvas-chrome.js?v=20261103-ifaces3';

const MIN_ZOOM = 0.05, MAX_ZOOM = 2;
const icon = (d) => `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

// opts: { model, aspect, view, selectedKey, pinned, canUndo, canRedo, onAddPage, onUndo, onRedo, onRebuild, onOpen(node),
//         onSelect(node|null), onViewChange(view), onPin(nodes), panelHtml(node) -> trusted html, onPanel(panelEl, node) }
export function createIfaceCanvas(opts) {
    const { model } = opts;
    const pins = { ...(opts.pinned || {}) };
    let layout = layoutIface(model, { aspect: opts.aspect || 1.6, pinned: pins });
    let view = opts.view || null;
    let selectedKey = opts.selectedKey && model.byKey.get(opts.selectedKey) ? opts.selectedKey : null;
    const el = document.createElement('div');
    el.className = 'erd-editor iface-canvas';
    el.innerHTML = `<div class="erd-body">
        <div class="erd-viewport iface-viewport" tabindex="0" role="application" aria-label="Interfaces canvas: arrow keys pan, plus and minus zoom, Tab to the pages${layout.large ? ', then use arrow keys to browse' : ''}">
            <div class="erd-stage"><svg class="erd-lines iface-lines" aria-hidden="true"></svg><div class="iface-lanes"></div><div class="erd-nodes iface-nodes" role="list" aria-label="Pages"></div></div>
        </div>
        <div class="erd-dock erd-dock-left iface-dock" role="toolbar" aria-label="Edit pages">
            <span data-iface="finder-slot"></span>
            <button type="button" class="erd-dock-button erd-dock-wide" data-iface="add-page" aria-label="Add page" title="Add page">${icon('M8 3v10M3 8h10')}<span class="erd-dock-label">Add page</span></button>
            <button type="button" class="erd-dock-button" data-iface="add-assets" aria-label="Attach project files" title="Attach project files">${icon('M8 10V2m-3 3 3-3 3 3M3 10v4h10v-4')}</button>
            <button type="button" class="erd-dock-button" data-iface="add-endpoint" aria-label="Add endpoint" title="Add endpoint">${icon('M5.5 4 2 8l3.5 4m5-8L14 8l-3.5 4M9 3 7 13')}</button>
            <span class="erd-dock-divider" aria-hidden="true"></span>
            <button type="button" class="erd-dock-button" data-iface="undo" aria-label="Undo" title="Undo (⌘Z)">${icon('M6 4 3 7l3 3M3.5 7H10a3 3 0 0 1 0 6H8')}</button>
            <button type="button" class="erd-dock-button" data-iface="redo" aria-label="Redo" title="Redo (⇧⌘Z)">${icon('M10 4l3 3-3 3M12.5 7H6a3 3 0 0 0 0 6h2')}</button>
            <span data-iface="advanced-slot"></span>
            <span class="erd-dock-divider" data-iface="count-divider" hidden aria-hidden="true"></span>
            <span class="iface-map-status" data-iface="page-count" hidden></span>
        </div>
        <div class="erd-dock erd-dock-right" role="toolbar" aria-label="Zoom">
            <button type="button" class="erd-dock-button erd-zoom-100" data-iface="reset-zoom" aria-label="Reset zoom" title="Reset zoom to 100%"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><circle cx="6.5" cy="6.5" r="4.1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M9.7 9.7 13 13" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><text x="6.5" y="7.7" text-anchor="middle" font-size="3.2" font-weight="700" fill="currentColor" stroke="none">100</text></svg></button>
            <button type="button" class="erd-dock-button" data-iface="zoom-out" aria-label="Zoom out" title="Zoom out">${icon('M3.5 8h9')}</button>
            <output class="erd-zoom" aria-live="polite" title="Zoom level">100%</output>
            <button type="button" class="erd-dock-button" data-iface="zoom-in" aria-label="Zoom in" title="Zoom in">${icon('M8 3.5v9M3.5 8h9')}</button>
            <span class="erd-dock-divider" aria-hidden="true"></span>
            <button type="button" class="erd-dock-button erd-dock-wide" data-iface="fit" aria-label="Fit to view" title="Fit to view"><span class="erd-dock-label erd-dock-label-keep">Fit</span></button>
        </div>
        <aside class="erd-panel iface-panel" aria-label="Page details" hidden></aside>
    </div>`;
    const $ = (s) => el.querySelector(s);
    const viewport = $('.erd-viewport'), stage = $('.erd-stage'), nodesEl = $('.iface-nodes'), lanesEl = $('.iface-lanes'), lines = $('.iface-lines'), panel = $('.iface-panel');
    const dock = $('.iface-dock');

    // ---- nodes, lanes, edges ---------------------------------------------------------
    lanesEl.innerHTML = layout.lanes.map((l) => `<section class="iface-lane" data-lane="${esc(l.id)}" style="transform: translate(${l.x}px, ${l.y - 4}px); width: ${l.w}px; height: ${l.h}px"><h3>${esc(l.label)} <small>${l.count}</small></h3></section>`).join('');
    const orderedNodes = layout.large
        ? [...model.nodes].sort((a, b) => a.y - b.y || a.x - b.x)
        : model.nodes;
    const lastCardBottom = orderedNodes.reduce((bottom, node) => Math.max(bottom, node.y + node.h), 0);
    let activeKey = selectedKey || orderedNodes[0]?.key;
    nodesEl.innerHTML = orderedNodes.map((n) => {
        const part = n.lane === 'ajax' || n.kind === 'endpoint';
        const comment = n.note && n.kind !== 'endpoint' ? `<small class="iface-note" title="${esc(n.note)}">${esc(n.note)}</small>` : '';
        const thumb = part || n.quiet || n.lane === 'guidance' ? '' : (n.shot?.image ? `<img class="iface-shot" alt="" decoding="async" loading="lazy" draggable="false" src="${esc(n.shot.image)}"${n.shot?.captured_at ? ` title="Captured ${esc(n.shot.captured_at)}"` : ''}>`
            : `<div class="iface-card-content">${n.summary?.length ? n.summary.map(value => `<span>${esc(value)}</span>`).join('') : `<span>${n.screenId ? 'Page stencil' : 'Outline needed'}</span>`}</div>`);
        const method = part && n.method ? `<span class="iface-part-method">${esc(n.method)}</span>` : '';
        const route = esc(n.route || (n.lane === 'guidance' ? 'guidance' : 'no route'));
        const endpoint = n.kind === 'endpoint';
        const kind = part ? (n.kind === 'endpoint' ? 'Endpoint' : 'Part') : (SHAPE_KIND[n.shape] || 'Page');
        const access = n.access || 'public';
        const accessOpen = access === 'public';
        const accessMark = `<span class="plan-sitemap-access" data-open="${accessOpen ? 'true' : 'false'}" title="${esc(accessOpen ? 'Open' : 'Needs a login')}">${accessOpen
            ? '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.25" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'
            : '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.25" y="7" width="9.5" height="6.25" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 7V5.1a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'}</span>`;
        const shotTitle = n.shot?.captured_at ? ` title="Captured ${esc(n.shot.captured_at)}"` : '';
        return `<div class="erd-table iface-node${part ? ' iface-part' : ''}${n.kind === 'endpoint' ? ' iface-endpoint' : ''}${n.quiet ? ' iface-node-quiet' : ''}${n.key === selectedKey ? ' selected' : ''}${n.pinned ? ' iface-pinned' : ''}" role="listitem" tabindex="${layout.large && n.key !== activeKey ? -1 : 0}" data-node="${esc(n.key)}" data-shape="${esc(n.shape)}" data-shape-source="${n.shapeSource}" data-lane="${esc(n.lane)}" data-kind="${esc(n.kind || '')}" data-screen-key="${esc(n.screenKey || '')}" data-access="${esc(access)}" data-outline="${n.screenId ? 'yes' : 'no'}" data-page-state="${esc(n.state || 'normal')}"
            aria-label="${esc(`${n.label}${n.method ? `, ${n.method}` : ''}${n.route ? `, ${n.route}` : ''}${n.note ? `. ${n.note}` : ''}. ${kind}${accessOpen ? ', open' : ', needs a login'}${n.screenId ? '' : ', no page outline yet'}. Enter opens.`)}"
            style="transform: translate(${n.x}px, ${n.y}px); width: ${n.w}px; height: ${n.h}px">
            <header class="erd-table-head iface-node-head"><span class="iface-glyph" title="${esc(kind)}${n.shapeSource === 'default' ? ' (default)' : ''}">${shapeGlyph(n.shape)}</span><strong title="${esc(n.label)}">${esc(n.label)}</strong>${accessMark}</header>
            ${endpoint ? '' : method}${thumb}${comment}<small class="iface-route" title="${esc(n.route)}">${endpoint ? `${method}<span class="iface-endpoint-path">${route}</span>` : route}</small>
            ${opts.onConnect && n.screenId ? `<button type="button" class="iface-port" data-wire-source aria-label="Connect from ${esc(n.label)}" title="Drag to another card to connect"></button>` : ''}
        </div>`;
    }).join('');
    const byKeyEl = new Map([...nodesEl.querySelectorAll('[data-node]')].map((n) => [n.dataset.node, n]));
    function setActivePage(key) {
        if (!layout.large || key === activeKey) return;
        const previous = byKeyEl.get(activeKey);
        if (previous) previous.tabIndex = -1;
        byKeyEl.get(key).tabIndex = 0;
        activeKey = key;
    }
    const port = (n, tx, ty) => {
        const cx = n.x + n.w / 2, cy = n.y + n.h / 2;
        const dx = tx - cx, dy = ty - cy;
        if (Math.abs(dx) >= Math.abs(dy)) return [dx >= 0 ? n.x + n.w : n.x, cy];
        return [cx, dy >= 0 ? n.y + n.h : n.y];
    };
    const hits = (x1, y1, x2, y2, skip) => {
        const left = Math.min(x1, x2), right = Math.max(x1, x2), top = Math.min(y1, y2), bottom = Math.max(y1, y2);
        const pad = 6;
        for (const node of model.nodes) {
            if (skip.has(node.key)) continue;
            const nx = node.x - pad, ny = node.y - pad, nw = node.w + pad * 2, nh = node.h + pad * 2;
            if (x1 === x2) {
                if (x1 >= nx && x1 <= nx + nw && bottom >= ny && top <= ny + nh) return true;
            } else if (y1 === y2) {
                if (y1 >= ny && y1 <= ny + nh && right >= nx && left <= nx + nw) return true;
            }
        }
        return false;
    };
    const orthogonal = (x1, y1, x2, y2, skip) => {
        const midX = (x1 + x2) / 2, midY = (y1 + y2) / 2;
        const candidates = [];
        const offsets = [0, 28, -28, 56, -56, 84, -84, 120, -120, 160, -160];
        for (const off of offsets) {
            const mx = midX + off;
            if (!hits(x1, y1, mx, y1, skip) && !hits(mx, y1, mx, y2, skip) && !hits(mx, y2, x2, y2, skip)) {
                candidates.push(`M${x1},${y1} H${mx} V${y2} H${x2}`);
                break;
            }
        }
        if (candidates.length) return candidates[0];
        for (const off of offsets) {
            const my = midY + off;
            if (!hits(x1, y1, x1, my, skip) && !hits(x1, my, x2, my, skip) && !hits(x2, my, x2, y2, skip)) {
                return `M${x1},${y1} V${my} H${x2} V${y2}`;
            }
        }
        return `M${x1},${y1} L${x2},${y2}`;
    };
    function drawEdges() {
        const directions = new Set(model.edges.filter(edge => edge.kind !== 'parent').map(({ from, to }) => JSON.stringify([from, to])));
        lines.setAttribute('width', String(layout.width + 40));
        lines.setAttribute('height', String(layout.height + 40));
        lines.innerHTML = `<defs><marker id="iface-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>` +
            model.edges.map((e) => {
                const a = model.byKey.get(e.from), b = model.byKey.get(e.to);
                if (!a || !b) return '';
                let [x1, y1] = port(a, b.x + b.w / 2, b.y + b.h / 2);
                let [x2, y2] = port(b, a.x + a.w / 2, a.y + a.h / 2);
                const horizontal = x1 === a.x || x1 === a.x + a.w;
                if (e.kind !== 'parent' && directions.has(JSON.stringify([e.to, e.from]))) {
                    if (horizontal) { const offset = Math.sign(x2 - x1) * 13; y1 += offset; y2 += offset; }
                    else { const offset = Math.sign(y2 - y1) * 13; x1 += offset; x2 += offset; }
                }
                const skip = new Set([a.key, b.key]);
                const blocked = (x1 === x2 || y1 === y2) && hits(x1, y1, x2, y2, skip);
                const d = e.kind === 'parent' || blocked
                    ? orthogonal(x1, y1, x2, y2, skip)
                    : curve(x1, y1, x2, y2, horizontal);
                return `<g class="iface-edge${e.chrome ? ' iface-edge-chrome' : ''}" data-kind="${esc(e.kind)}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" data-chrome="${e.chrome ? 'yes' : 'no'}"><path class="erd-line" d="${d}" marker-end="url(#iface-arrow)"/>${e.label ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}">${esc(e.label)}</text>` : ''}</g>`;
            }).join('');
        labelsPlaced = false;
        return [...lines.querySelectorAll('.iface-edge')];
    }
    let labelsPlaced = false;
    let edges = drawEdges();
    const canFilterLinks = edges.length > 0;
    function setAllLinks(show) {
        lines.classList.toggle('selected-links-only', canFilterLinks && !show);
    }
    setAllLinks(opts.showAllLinks !== false);

    // Keep reciprocal and dense-hub captions distinct without changing navigation paths.
    function placeEdgeLabels() {
        if (labelsPlaced || !el.isConnected) return;
        const occupied = model.nodes.map((n) => ({ x: n.x - 4, y: n.y - 4, width: n.w + 8, height: n.h + 8 }));
        const overlaps = (a, b) => a.x < b.x + b.width + 4 && a.x + a.width + 4 > b.x && a.y < b.y + b.height + 4 && a.y + a.height + 4 > b.y;
        for (const text of lines.querySelectorAll('.iface-edge text')) {
            const path = text.parentElement.querySelector('path');
            const length = path.getTotalLength();
            const start = path.getPointAtLength(0), end = path.getPointAtLength(length);
            const normal = { x: -(end.y - start.y) / (length || 1), y: (end.x - start.x) / (length || 1) };
            const original = text.getBBox();
            const dx = original.x - Number(text.getAttribute('x')), dy = original.y - Number(text.getAttribute('y'));
            let placed = false;
            for (const offset of [0, 16, -16, 32, -32, 48, -48, 64, -64, 80, -80]) {
                for (const fraction of [.5, .4, .6, .3, .7, .2, .8]) {
                    const point = path.getPointAtLength(length * fraction);
                    const x = point.x + normal.x * offset, y = point.y + normal.y * offset - 6;
                    const box = { x: x + dx, y: y + dy, width: original.width, height: original.height };
                    if (occupied.some((other) => overlaps(box, other))) continue;
                    text.setAttribute('x', String(x)); text.setAttribute('y', String(y));
                    occupied.push(box); placed = true; break;
                }
                if (placed) break;
            }
            // No clear spot: leave the line unlabelled rather than stack text on a card or another label.
            if (placed) text.removeAttribute('visibility');
            else text.setAttribute('visibility', 'hidden');
        }
        labelsPlaced = true;
    }

    // ---- view ---------------------------------------------------------------------------
    function applyView() {
        stage.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`;
        const grid = 22 * view.zoom;
        viewport.style.backgroundSize = `${grid}px ${grid}px`;
        viewport.style.backgroundPosition = `${view.x % grid}px ${view.y % grid}px`;
        viewport.classList.toggle('iface-grid-quiet', view.zoom < 0.4);
        $('.erd-zoom').textContent = `${Math.round(view.zoom * 100)}%`;
        opts.onViewChange?.(view);
    }
    let fitted = null;
    function fit({ floor = 0 } = {}) {
        const vw = viewport.clientWidth || 800, vh = readableHeight();
        const b = { x: -14, y: -4, w: layout.width + 28, h: layout.height + 18 };
        const fittedZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.min((vw - 40) / b.w, (vh - 40) / b.h, 1)));
        const zoom = Math.min(MAX_ZOOM, Math.max(fittedZoom, floor));
        view = { zoom, x: (vw - b.w * zoom) / 2 - b.x * zoom, y: Math.max(16, (vh - b.h * zoom) / 2) - b.y * zoom };
        fitted = floor ? null : view;
        applyView();
        return zoom;
    }
    // Use card bounds so a small board can open at readable size.
    function contentBounds() {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        const include = (x, y, w, h) => {
            if (!Number.isFinite(x) || !Number.isFinite(y)) return;
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + (Number(w) || 0));
            maxY = Math.max(maxY, y + (Number(h) || 0));
        };
        for (const node of model.nodes || []) include(node.x, node.y, node.w, node.h);
        if (!Number.isFinite(minX)) return { x: -14, y: -4, w: (layout.width || 0) + 28, h: (layout.height || 0) + 18 };
        return { x: minX, y: minY, w: Math.max(1, maxX - minX), h: Math.max(1, maxY - minY) };
    }
    function initialView() {
        if (!el.isConnected) return;
        const vw = viewport.clientWidth || 0;
        if (!vw) {
            requestAnimationFrame(() => { if (!view) initialView(); });
            return;
        }
        const b = contentBounds();
        const vh = readableHeight();
        const pad = 24;
        const zoom = Math.min(1, Math.max(layout.large ? 0.82 : 0.55, Math.min((vw - pad * 2) / b.w, (vh - pad * 2) / b.h)));
        const x = (b.w * zoom + pad * 2 <= vw)
            ? (vw - b.w * zoom) / 2 - b.x * zoom
            : pad - b.x * zoom;
        const y = Math.max(64, (vh - b.h * zoom) / 2) - b.y * zoom;
        view = { zoom, x, y };
        fitted = null;
        applyView();
    }
    function zoomAt(factor, cx = (viewport.clientWidth || 800) / 2, cy = (viewport.clientHeight || 500) / 2) {
        if (!view) return;
        const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.zoom * factor));
        const k = zoom / view.zoom;
        view = { zoom, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k };
        applyView();
    }
    function readableHeight() {
        const dockHeight = Math.max(...[...el.querySelectorAll('.erd-dock')].map((dock) =>
            dock.offsetHeight + (parseFloat(getComputedStyle(dock).bottom) || 0)), 0);
        return Math.max(1, viewport.clientHeight - dockHeight - 12);
    }
    function panTo(x, y) {
        if (layout.large) {
            const height = readableHeight();
            const margin = 16;
            const clamp = (position, extent, available) => {
                const trailing = available - extent - margin;
                return Math.max(Math.min(margin, trailing), Math.min(Math.max(margin, trailing), position));
            };
            const mapHeight = lastCardBottom * view.zoom;
            y = clamp(y, mapHeight, height);
            const atBottom = mapHeight > height - margin * 2 && y === height - mapHeight - margin;
            const lastRow = orderedNodes.at(-1)?.y;
            // A short lane at the bottom may have far fewer columns than the one above.
            const rowWidth = orderedNodes.reduce((right, node) => {
                const top = node.y * view.zoom + y;
                if (atBottom && node.y !== lastRow) return right;
                if (top >= height || top + node.h * view.zoom <= 0) return right;
                return Math.max(right, (node.x + node.w) * view.zoom);
            }, 0);
            x = clamp(x, rowWidth || layout.width * view.zoom, viewport.clientWidth);
        }
        if (x === view.x && y === view.y) return;
        fitted = null;
        view = { ...view, x, y };
        applyView();
    }
    // 100% is the default zoom. Keep the point under the viewport centre fixed.
    function resetZoom() {
        if (!view) view = { zoom: 1, x: 0, y: 0 };
        const cx = (viewport.clientWidth || 800) / 2, cy = (viewport.clientHeight || 500) / 2;
        const k = 1 / view.zoom;
        view = { zoom: 1, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k };
        fitted = null;
        applyView();
    }
    function sizeToWindow() {
        if (!el.isConnected) return;
        const top = viewport.getBoundingClientRect().top;
        placeEdgeLabels();
        const height = Math.max(240, Math.round(availableBottom(el) - Math.max(0, top) - 16));
        if (viewport.style.height !== `${height}px`) viewport.style.height = `${height}px`;
    }
    let frame = 0;
    const onResize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { const refit = fitted && fitted === view; sizeToWindow(); if (refit) fit(); }); };
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(onResize) : null;
    resizeObserver?.observe(el);
    window.addEventListener('resize', onResize);

    // ---- selection + panel -----------------------------------------------------------
    function select(key, { focus = false } = {}) {
        selectedKey = key && model.byKey.has(key) ? key : null;
        byKeyEl.forEach((node, k) => node.classList.toggle('selected', k === selectedKey));
        lines.classList.toggle('has-selected-page', Boolean(selectedKey));
        edges.forEach(edge => edge.classList.toggle('related',
            edge.dataset.from === selectedKey || edge.dataset.to === selectedKey));
        const node = selectedKey ? model.byKey.get(selectedKey) : null;
        panel.hidden = !node;
        panel.dataset.mode = node ? 'edit' : '';
        if (node) {
            setActivePage(node.key);
            panel.innerHTML = `<button type="button" class="erd-callout-close iface-panel-close" aria-label="Close page details" title="Close (Esc)">${icon('M4.5 4.5l7 7M11.5 4.5l-7 7')}</button>${opts.panelHtml ? opts.panelHtml(node) : `<h4>${esc(node.label)}</h4>`}`;
            panel.querySelector('.iface-panel-close').onclick = () => { select(null); byKeyEl.get(node.key)?.focus({ preventScroll: true }); };
            opts.onPanel?.(panel, node);
            if (focus) panel.querySelector('button:not(.iface-panel-close)')?.focus({ preventScroll: true });
        } else panel.innerHTML = '';
        opts.onSelect?.(node);
    }
    function reveal(key, { readable = false } = {}) {
        const n = model.byKey.get(key);
        if (!n || !view) return;
        let vw = viewport.clientWidth;
        if (readable && key === selectedKey && !panel.hidden && getComputedStyle(panel).position === 'absolute') {
            const unobstructed = panel.getBoundingClientRect().left - viewport.getBoundingClientRect().left - 12;
            if (unobstructed >= n.w) vw = unobstructed;
        }
        const vh = readableHeight();
        // Fit remains an overview. Keyboard navigation makes the focused page
        // readable, while preserving a user's deliberate magnification above 100%.
        const zoom = readable ? Math.max(1, view.zoom) : view.zoom;
        const sx = n.x * zoom + view.x, sy = n.y * zoom + view.y;
        if (zoom !== view.zoom || sx < 0 || sy < 0 || sx + n.w * zoom > vw || sy + n.h * zoom > vh) {
            view = { zoom, x: vw / 2 - (n.x + n.w / 2) * zoom, y: vh / 2 - (n.y + n.h / 2) * zoom };
            fitted = null;
            applyView();
        }
    }

    function placeCard(node) {
        const card = byKeyEl.get(node.key);
        if (card) card.style.transform = `translate(${node.x}px, ${node.y}px)`;
    }
    function neighborsOf(key) {
        const found = [];
        for (const edge of model.edges) {
            if (edge.chrome) continue;
            if (edge.from === key) found.push(edge.to);
            else if (edge.to === key) found.push(edge.from);
        }
        return found.map((id) => model.byKey.get(id)).filter(Boolean);
    }
    function applyDrag(g, clientX, clientY) {
        const node = model.byKey.get(g.node);
        if (!node) return;
        node.x = g.nodeOrigin[0] + (clientX - g.start[0]) / view.zoom;
        node.y = g.nodeOrigin[1] + (clientY - g.start[1]) / view.zoom;
        placeCard(node);
        const rest = NODE.w + NODE.gapX + NODE.linkGap;
        for (const other of g.springs || []) {
            const restPos = g.rest.get(other.key);
            if (!restPos) continue;
            const cx = node.x + node.w / 2, cy = node.y + node.h / 2;
            const ox = restPos[0] + other.w / 2, oy = restPos[1] + other.h / 2;
            const dx = ox - cx, dy = oy - cy;
            const dist = Math.hypot(dx, dy) || 1;
            const shift = (dist - rest) * 0.18;
            other.x = restPos[0] + (dx / dist) * shift;
            other.y = restPos[1] + (dy / dist) * shift;
            placeCard(other);
        }
        edges = drawEdges();
        markRelated();
    }
    function markRelated() {
        lines.classList.toggle('has-selected-page', Boolean(selectedKey));
        edges.forEach((edge) => edge.classList.toggle('related',
            Boolean(selectedKey) && (edge.dataset.from === selectedKey || edge.dataset.to === selectedKey)));
    }
    function relayoutPinned() {
        layout = layoutIface(model, { aspect: opts.aspect || 1.6, pinned: pins });
        for (const lane of lanesEl.querySelectorAll('.iface-lane')) {
            const row = layout.lanes.find((item) => item.id === lane.dataset.lane);
            if (!row) continue;
            lane.style.transform = `translate(${row.x}px, ${row.y - 4}px)`;
            lane.style.width = `${row.w}px`;
            lane.style.height = `${row.h}px`;
        }
        for (const node of model.nodes) placeCard(node);
        edges = drawEdges();
        markRelated();
    }

    // ---- pointer: drag a card to move it, drag empty canvas to pan, click selects, double-click opens
    let gesture = null, raf = 0, clickTimer = 0, wire = null, wireKey = null, connecting = false;
    const draftWire = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    draftWire.classList.add('iface-wire-draft');
    draftWire.innerHTML = '<path/>';
    stage.append(draftWire);
    function curve(x1, y1, x2, y2, horizontal = true) {
        const bend = Math.max(28, Math.abs(horizontal ? x2 - x1 : y2 - y1) * 0.45);
        const direction = Math.sign(horizontal ? x2 - x1 : y2 - y1) || 1;
        const dx = horizontal ? bend * direction : 0, dy = horizontal ? 0 : bend * direction;
        return `M${x1},${y1} C${x1 + dx},${y1 + dy} ${x2 - dx},${y2 - dy} ${x2},${y2}`;
    }
    function markWire(key) {
        byKeyEl.get(wireKey)?.classList.remove('iface-wire-source');
        wireKey = key;
        byKeyEl.get(key)?.classList.add('iface-wire-source');
        viewport.classList.toggle('iface-wiring', Boolean(key));
        draftWire.firstElementChild.removeAttribute('d');
    }
    async function connectTo(key) {
        const from = model.byKey.get(wireKey), to = model.byKey.get(key);
        markWire(null);
        if (!from?.screenId || !to?.screenId || from === to || connecting) return;
        connecting = true;
        try { await opts.onConnect?.(from, to); }
        finally { connecting = false; }
    }
    viewport.addEventListener('click', event => {
        const source = event.target.closest('[data-wire-source]');
        if (source && event.detail === 0) markWire(source.closest('[data-node]').dataset.node);
    });
    function panGesture() {
        if (gesture?.last && !gesture.dragging) panTo(gesture.origin[0] + gesture.last[0], gesture.origin[1] + gesture.last[1]);
    }
    viewport.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || !view) return;
        clearTimeout(clickTimer);
        const node = e.target.closest('[data-node]');
        const target = node ? model.byKey.get(node.dataset.node) : null;
        if (e.target.closest('[data-wire-source]') && target) {
            e.preventDefault();
            markWire(target.key);
            wire = { id: e.pointerId, from: target };
            viewport.setPointerCapture(e.pointerId);
            return;
        }
        if (wireKey && target) { connectTo(target.key); return; }
        gesture = {
            start: [e.clientX, e.clientY], origin: [view.x, view.y], moved: false,
            node: target?.key || null, id: e.pointerId, dragging: false,
            nodeOrigin: target ? [target.x, target.y] : null,
        };
    });
    viewport.addEventListener('pointermove', (e) => {
        if (wire?.id === e.pointerId) {
            const bounds = viewport.getBoundingClientRect();
            const x = (e.clientX - bounds.left - view.x) / view.zoom;
            const y = (e.clientY - bounds.top - view.y) / view.zoom;
            draftWire.firstElementChild.setAttribute('d', curve(wire.from.x + wire.from.w, wire.from.y + 18, x, y));
            return;
        }
        if (!gesture) return;
        const dx = e.clientX - gesture.start[0], dy = e.clientY - gesture.start[1];
        if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
        if (!gesture.moved) {
            try { viewport.setPointerCapture(gesture.id); } catch { /* synthetic pointer */ }
            if (gesture.node) {
                gesture.dragging = true;
                const node = model.byKey.get(gesture.node);
                gesture.rest = new Map();
                gesture.springs = neighborsOf(gesture.node).filter((other) => !other.pinned || other.key === gesture.node);
                for (const other of gesture.springs) gesture.rest.set(other.key, [other.x, other.y]);
                if (node) {
                    const card = byKeyEl.get(node.key);
                    if (card) card.classList.add('iface-dragging');
                }
            }
        }
        gesture.moved = true;
        gesture.last = [dx, dy];
        if (raf) return;
        raf = requestAnimationFrame(() => {
            raf = 0;
            if (gesture?.dragging) applyDrag(gesture, gesture.start[0] + gesture.last[0], gesture.start[1] + gesture.last[1]);
            else panGesture();
        });
    });
    const end = (event) => {
        if (wire?.id === event.pointerId) {
            const key = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-node]')?.dataset.node;
            wire = null;
            if (key && key !== wireKey) connectTo(key);
            else draftWire.firstElementChild.removeAttribute('d');
            return;
        }
        const g = gesture;
        if (!g || g.id !== event.pointerId) return;
        if (g.moved) g.last = [event.clientX - g.start[0], event.clientY - g.start[1]];
        if (g.dragging) applyDrag(g, event.clientX, event.clientY);
        else panGesture();
        gesture = null;
        if (g.dragging) {
            const node = model.byKey.get(g.node);
            byKeyEl.get(g.node)?.classList.remove('iface-dragging');
            if (node) {
                if (g.rest) for (const other of g.springs || []) {
                    const rest = g.rest.get(other.key);
                    if (!rest || other.pinned) continue;
                    other.x = rest[0];
                    other.y = rest[1];
                }
                pins[node.screenKey || node.key] = { x: node.x, y: node.y, pinned: true };
                node.pinned = true;
                relayoutPinned();
                opts.onPin?.({ ...(pins) });
            }
            return;
        }
        if (g.moved) return;
        if (g.node) clickTimer = setTimeout(() => select(g.node), 270);
        else select(null);
    };
    viewport.addEventListener('pointerup', end);
    viewport.addEventListener('pointercancel', () => { gesture = null; wire = null; markWire(null); });
    viewport.addEventListener('dblclick', (e) => {
        const node = e.target.closest('[data-node]');
        if (node) {
            clearTimeout(clickTimer);
            e.preventDefault();
            const entry = model.byKey.get(node.dataset.node);
            opts.onSelect?.(entry);
            opts.onOpen?.(entry, 'design');
        }
    });
    viewport.addEventListener('wheel', (e) => {
        if (!view) return;
        e.preventDefault();
        const r = viewport.getBoundingClientRect();
        // Command+scroll zooms (up in, down out). Control and a plain scroll pan. Chrome also sets ctrlKey for Command+scroll, so the check is Meta only.
        if (e.metaKey || e.getModifierState?.('Meta')) zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
        else panTo(view.x - e.deltaX, view.y - e.deltaY);
    }, { passive: false });
    viewport.addEventListener('focusin', (e) => {
        const node = e.target.closest?.('[data-node]');
        if (node) {
            setActivePage(node.dataset.node);
            if (node.matches(':focus-visible')) reveal(node.dataset.node, { readable: true });
        }
    });
    viewport.addEventListener('keydown', (e) => {
        const node = e.target.closest?.('[data-node]');
        if (e.key === 'Escape' && wireKey) { e.preventDefault(); markWire(null); return; }
        if (e.target.closest?.('[data-wire-source]')) return;
        if (node && wireKey && e.key === 'Enter') { e.preventDefault(); connectTo(node.dataset.node); return; }
        if (node && e.key === 'Enter') { e.preventDefault(); opts.onOpen?.(model.byKey.get(node.dataset.node)); return; }
        if (node && e.key === ' ') { e.preventDefault(); select(node.dataset.node, { focus: true }); return; }
        if (node && layout.large) {
            const index = orderedNodes.findIndex((page) => page.key === node.dataset.node);
            const next = {
                ArrowLeft: index - 1, ArrowUp: index - 1,
                ArrowRight: index + 1, ArrowDown: index + 1,
                Home: 0, End: orderedNodes.length - 1,
            }[e.key];
            if (next !== undefined) {
                e.preventDefault();
                const page = orderedNodes[Math.max(0, Math.min(next, orderedNodes.length - 1))];
                byKeyEl.get(page.key).focus({ preventScroll: true });
                return;
            }
        }
        const step = e.shiftKey ? 160 : 60;
        const pan = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
        if (pan && !node && view) { e.preventDefault(); panTo(view.x + pan[0], view.y + pan[1]); return; }
        if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAt(1.2); }
        else if (e.key === '-') { e.preventDefault(); zoomAt(1 / 1.2); }
        else if (e.key === '0') { e.preventDefault(); fit(); }
    });
    el.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && selectedKey && !e.defaultPrevented) {
            e.preventDefault();
            const key = selectedKey;
            select(null);
            byKeyEl.get(key)?.focus({ preventScroll: true });
            return;
        }
        if (e.target.matches('input, select, textarea')) return;
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); (e.shiftKey ? opts.onRedo : opts.onUndo)?.(); }
    });

    // ---- docks ------------------------------------------------------------------------
    const act = {
        'add-page': () => opts.onAddPage?.($('[data-iface="add-page"]')),
        'add-assets': () => opts.onAddAssets?.(),
        'add-endpoint': () => opts.onAddEndpoint?.($('[data-iface="add-endpoint"]')),
        undo: () => opts.onUndo?.(), redo: () => opts.onRedo?.(),
        'zoom-in': () => zoomAt(1.2), 'zoom-out': () => zoomAt(1 / 1.2), 'reset-zoom': () => resetZoom(), fit: () => fit(),
    };
    el.querySelectorAll('[data-iface]').forEach((b) => b.addEventListener('click', () => act[b.dataset.iface]?.()));
    const setHistory = (canUndo, canRedo) => { $('[data-iface="undo"]').disabled = !canUndo; $('[data-iface="redo"]').disabled = !canRedo; };
    setHistory(Boolean(opts.canUndo), Boolean(opts.canRedo));
    if (!opts.onAddPage) $('[data-iface="add-page"]').disabled = true;
    if (!opts.onAddAssets) $('[data-iface="add-assets"]').hidden = true;
    if (!opts.onAddEndpoint) $('[data-iface="add-endpoint"]').hidden = true;

    function setShot(key, shot) {
        const node = model.byKey.get(key);
        if (!node || node.lane === 'ajax' || node.lane === 'guidance' || node.kind === 'endpoint') return;
        node.shot = { ...(node.shot || {}), ...shot };
        const card = byKeyEl.get(key);
        if (!card) return;
        if (shot?.image) {
            const existing = card.querySelector('img.iface-shot');
            if (existing && (existing.getAttribute('src') === shot.image || existing.dataset.shotSrc === shot.image)) {
                if (shot.current !== false) existing.classList.add('iface-shot-current');
                if (shot.captured_at) existing.title = `Captured ${shot.captured_at}`;
                return;
            }
            const img = document.createElement('img');
            img.className = 'iface-shot';
            img.alt = '';
            img.draggable = false;
            img.decoding = 'async';
            img.loading = 'eager';
            img.dataset.shotSrc = shot.image;
            img.src = shot.image;
            if (shot.current !== false) img.classList.add('iface-shot-current');
            if (shot.captured_at) img.title = `Captured ${shot.captured_at}`;
            const slot = card.querySelector('.iface-shot');
            if (slot) slot.replaceWith(img);
            else card.querySelector('.iface-node-head')?.insertAdjacentElement('afterend', img);
        } else if (shot?.reason) {
            const missing = card.querySelector('.iface-shot-missing');
            if (missing) missing.title = shot.reason;
        }
    }

    function setPageCount(text, title) {
        const count = $('[data-iface="page-count"]');
        const divider = $('[data-iface="count-divider"]');
        if (!count) return;
        const label = String(text || '');
        count.textContent = label;
        count.title = title || label;
        count.hidden = !label;
        if (divider) divider.hidden = !label;
    }

    function setAdvanced(node) {
        const slot = $('[data-iface="advanced-slot"]');
        if (!slot) return;
        slot.replaceChildren();
        if (node) slot.append(node);
    }

    return {
        el, dock, get layout() { return layout; }, fit, zoomAt, resetZoom, reveal, select, setHistory, setShot, setPageCount, setAdvanced, canFilterLinks, setAllLinks,
        get view() { return view; },
        get selectedKey() { return selectedKey; },
        mounted() {
            sizeToWindow();
            if (view) applyView(); else initialView();
            if (selectedKey) select(selectedKey);
        },
        destroy() { resizeObserver?.disconnect(); window.removeEventListener('resize', onResize); cancelAnimationFrame(frame); cancelAnimationFrame(raf); clearTimeout(clickTimer); el.remove(); },
    };
}
