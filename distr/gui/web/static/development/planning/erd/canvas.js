// ERD editor view: HTML table nodes + one SVG relation layer inside a transformed stage.
// Zoom/pan are a single CSS transform; dragging updates only the moved node and its relations (rAF).
import { parseErd, emitErd, CARDINALITY, RELATION_PRESETS } from './parser.js?v=20261103-rows1';
import { autoLayout, needsLayout, placeMissing, bounds, TABLE_WIDTH } from './layout.js?v=20261102-verify';
import { relationGeometry, markerPath, markerTransform } from './geometry.js?v=20261102-verify';
import { createStore } from './model.js?v=20261102-verify';
import { FIELD_TYPE_GROUPS, PARAM_HINT, PARAM_TYPES, splitType, joinType } from './field-types.js?v=20261102-verify';
import { availableBottom } from '../canvas-chrome.js?v=20261103-ifaces3';

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SVG = 'http://www.w3.org/2000/svg';
const MIN_ZOOM = 0.1, MAX_ZOOM = 2.5;
const DENSE_DIAGRAM_TABLES = 20, MIN_READABLE_FIT = 0.65;
const PAN_KEYS = { ArrowLeft: [64, 0], ArrowRight: [-64, 0], ArrowUp: [0, 64], ArrowDown: [0, -64] };
const KEYS = ['PK', 'FK', 'UK'];
const DOTS = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3.5" cy="8" r="1.3" fill="currentColor"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/><circle cx="12.5" cy="8" r="1.3" fill="currentColor"/></svg>';
const MENU_ICONS = { edit: 'M3 13h3l7-7-3-3-7 7v3z', view: 'M2.5 3.5h11v9h-11zM2.5 6.5h11M6 6.5v6', duplicate: 'M5 5h8v8H5zM3 11V3h8', delete: 'M3 4.5h10M6.5 4.5V3h3v1.5M5 4.5l.6 8.5h4.8l.6-8.5' };

// TYPE control for one field: dropdown of FIELD_TYPE_GROUPS + a parameter input (varchar(255) ->
// "varchar" + "255"). A type the list doesn't know stays exactly as stored, shown as "Custom: <value>".
function typeControlHtml(field, index) {
    const t = splitType(field.type);
    const groups = FIELD_TYPE_GROUPS.map(([label, types]) => `<optgroup label="${esc(label)}">${types.map((v) => `<option value="${v}"${t.known && t.base === v ? ' selected' : ''}>${v}</option>`).join('')}</optgroup>`).join('');
    const custom = t.known ? '' : `<option value="__keep" selected>Custom: ${esc(field.type)}</option>`;
    const showParam = t.known && PARAM_TYPES.has(t.base);
    return `<span class="erd-type-control">
        <select data-f="type-base" aria-label="Field type" id="erd-type-${index}">${custom}${groups}<option value="__custom">Other…</option></select>
        <input data-f="type-param" value="${esc(t.param)}" placeholder="${esc(PARAM_HINT[t.base] || '')}" aria-label="Type parameters"${showParam ? '' : ' hidden'}>
        <input data-f="type" value="${esc(field.type)}" placeholder="custom type" aria-label="Custom field type"${t.known ? ' hidden' : ''}>
    </span>`;
}
// One popover menu (panel ⋯ menu and the canvas right-click menu): role=menu, arrows/Home/End, Escape,
// outside click and scroll close it, kept inside the viewport (flipped / clamped), focus returns to the opener.
let openMenuApi = null;
function openMenu({ items, x, y, anchor = null, opener = null, label = 'Table actions' }) {
    openMenuApi?.close(false);
    const menu = document.createElement('div');
    menu.className = 'board-context-menu erd-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', label);
    menu.innerHTML = items.map((it, i) => `<button type="button" role="menuitem" tabindex="-1" data-i="${i}"${it.danger ? ' class="danger"' : ''}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="${MENU_ICONS[it.icon] || ''}"/></svg>${esc(it.label)}</button>`).join('');
    document.body.append(menu);
    const r = menu.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight, pad = 8;
    let left = x, top = y;
    if (anchor) { left = anchor.right - r.width; top = anchor.bottom + 4; if (top + r.height > vh - pad) top = anchor.top - r.height - 4; }
    else { if (left + r.width > vw - pad) left = x - r.width; if (top + r.height > vh - pad) top = y - r.height; }
    menu.style.left = `${Math.round(Math.max(pad, Math.min(left, vw - r.width - pad)))}px`;
    menu.style.top = `${Math.round(Math.max(pad, Math.min(top, vh - r.height - pad)))}px`;
    const buttons = [...menu.querySelectorAll('[role="menuitem"]')];
    const focusAt = (i) => buttons[(i + buttons.length) % buttons.length]?.focus();
    const onDoc = (e) => { if (!menu.contains(e.target) && e.target !== opener && !opener?.contains?.(e.target)) close(false); };
    const onScroll = (e) => { if (!menu.contains(e.target)) close(false); };
    const onResize = () => close(false);
    function close(returnFocus = true) {
        if (!menu.isConnected) return;
        menu.remove();
        document.removeEventListener('pointerdown', onDoc, true);
        document.removeEventListener('scroll', onScroll, true);
        document.removeEventListener('wheel', onScroll, true);
        window.removeEventListener('resize', onResize);
        if (openMenuApi?.menu === menu) openMenuApi = null;
        opener?.setAttribute?.('aria-expanded', 'false');
        if (returnFocus && opener?.isConnected) opener.focus({ preventScroll: true });
    }
    menu.addEventListener('keydown', (e) => {
        const i = buttons.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') focusAt(i + 1);
        else if (e.key === 'ArrowUp') focusAt(i - 1);
        else if (e.key === 'Home') focusAt(0);
        else if (e.key === 'End') focusAt(buttons.length - 1);
        else if (e.key === 'Escape') close(true);
        else if (e.key === 'Tab') close(false);
        else return;
        e.preventDefault(); e.stopPropagation();
    });
    menu.addEventListener('click', (e) => {
        const b = e.target.closest('[role="menuitem"]');
        if (!b) return;
        close(true);
        items[Number(b.dataset.i)].run();
    });
    document.addEventListener('pointerdown', onDoc, true);
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('wheel', onScroll, true);
    window.addEventListener('resize', onResize);
    opener?.setAttribute?.('aria-expanded', 'true');
    focusAt(0);
    openMenuApi = { menu, close };
    return openMenuApi;
}

export function createErdEditor({ text, onSave, onReloadLatest = null, onViewRows = null, aspect = 1.6, debounceMs = 1200, askConfirm = async (message) => Boolean(await window.DecisionsAPI?.confirm?.({ title: 'Delete', message, confirmLabel: 'Delete', danger: true })) }) {
    const parsed = parseErd(text);
    const fresh = needsLayout(parsed.model);
    if (fresh && !parsed.hasLayout) autoLayout(parsed.model, { aspect }); else placeMissing(parsed.model, { aspect });
    const store = createStore(parsed.model);
    let view = parsed.model.view || null;
    let selected = null;   // { kind: 'table', name } | { kind: 'relation', index }
    let savedText = text;
    let status = 'saved', statusMessage = '';
    let saveTimer = null, saving = null, conflict = false;

    const el = document.createElement('div');
    el.className = 'erd-editor';
    el.dataset.saveState = 'saved';
    const icon = (d) => `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    // Chrome overlays the canvas grid (inside .erd-body): bottom-left edit dock, bottom-right zoom dock,
    // top-right dismissable call-out, a brief save tick, and an alert only for errors / conflicts.
    el.innerHTML = `
        <details class="erd-warnings" hidden></details>
        <div class="erd-body">
            <div class="erd-viewport" tabindex="0" aria-label="ERD canvas" aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown">
                <div class="erd-stage"><svg class="erd-lines" aria-hidden="true"></svg><div class="erd-nodes"></div><svg class="erd-draft" aria-hidden="true"><path/></svg></div>
            </div>
            <div class="erd-dock erd-dock-left" role="toolbar" aria-label="Edit diagram">
                <button type="button" class="erd-dock-button erd-dock-wide" data-erd="add-table" aria-label="Add table" title="Add table">${icon('M8 3v10M3 8h10')}<span class="erd-dock-label">Add table</span></button>
                <span class="erd-dock-divider" aria-hidden="true"></span>
                <button type="button" class="erd-dock-button" data-erd="undo" aria-label="Undo" title="Undo (⌘Z)">${icon('M6 4 3 7l3 3M3.5 7H10a3 3 0 0 1 0 6H8')}</button>
                <button type="button" class="erd-dock-button" data-erd="redo" aria-label="Redo" title="Redo (⇧⌘Z)">${icon('M10 4l3 3-3 3M12.5 7H6a3 3 0 0 0 0 6h2')}</button>
            </div>
            <div class="erd-dock erd-dock-right" role="toolbar" aria-label="Zoom">
                <button type="button" class="erd-dock-button" data-erd="zoom-out" aria-label="Zoom out" title="Zoom out">${icon('M3.5 8h9')}</button>
                <output class="erd-zoom" aria-live="polite" title="Zoom level">100%</output>
                <button type="button" class="erd-dock-button" data-erd="zoom-in" aria-label="Zoom in" title="Zoom in">${icon('M8 3.5v9M3.5 8h9')}</button>
                <span class="erd-dock-divider" aria-hidden="true"></span>
                <button type="button" class="erd-dock-button erd-dock-wide" data-erd="fit" aria-label="Fit to view" title="Fit to view"><span class="erd-dock-label erd-dock-label-keep">Fit</span></button>
            </div>
            <section class="erd-callout" aria-label="Diagram summary" hidden>
                <button type="button" class="erd-callout-close" data-erd="callout-close" aria-label="Dismiss summary" title="Dismiss">${icon('M4.5 4.5l7 7M11.5 4.5l-7 7')}</button>
                <p class="erd-callout-stats"></p>
                <p class="erd-callout-hint">Select a table or relation to edit it. Drag a table's ● onto another to relate them.</p>
            </section>
            <span class="erd-tick" aria-hidden="true" hidden>✓</span>
            <div class="erd-alert" role="alert" hidden>
                <span class="erd-status" data-state="saved"></span>
                <button type="button" class="erd-dock-button erd-dock-wide erd-reload" data-erd="reload-latest" hidden><span class="erd-dock-label erd-dock-label-keep">Reload latest (your version is copied)</span></button>
            </div>
            <aside class="erd-panel" aria-label="Selection details"></aside>
            <section class="erd-data" hidden aria-label="Table rows"></section>
        </div>`;
    const $ = (s) => el.querySelector(s);
    const viewport = $('.erd-viewport'), stage = $('.erd-stage'), nodesEl = $('.erd-nodes'), lines = $('.erd-lines'), panel = $('.erd-panel');
    const draft = $('.erd-draft path');

    if (parsed.warnings.length) {
        const w = $('.erd-warnings');
        w.hidden = false;
        // one compact line by default (never remembered open); the list is there on demand
        w.innerHTML = `<summary><strong>${parsed.warnings.length} line(s) kept as raw Mermaid text</strong> · not editable here, preserved on save</summary><ul>${parsed.warnings.slice(0, 8).map((m) => `<li>${esc(m)}</li>`).join('')}</ul>`;
    }

    // ---- view transform -------------------------------------------------------------
    function applyView() {
        stage.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`;
        $('.erd-zoom').textContent = `${Math.round(view.zoom * 100)}%`;
    }
    function fit({ initial = false } = {}) {
        const b = bounds(store.state);
        const vw = viewport.clientWidth || 800, vh = viewport.clientHeight || 500;
        const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.min((vw - 40) / b.w, (vh - 40) / b.h, 1)));
        if (initial && store.state.tables.length > DENSE_DIAGRAM_TABLES && zoom < MIN_READABLE_FIT) {
            const first = store.state.tables.reduce((best, table) =>
                table.y < best.y || (table.y === best.y && table.x < best.x) ? table : best);
            view = { zoom: 1, x: 20 - first.x, y: 20 - first.y };
        } else {
            view = { zoom, x: (vw - b.w * zoom) / 2 - b.x * zoom, y: (vh - b.h * zoom) / 2 - b.y * zoom };
            fittedView = view;
        }
        store.setView(view);
        applyView();
        return view.zoom;
    }
    // The canvas fills the space left in the window below it (header, tabs, chat panel and footer are
    // whatever sits above / beside it), never a fixed 458px; re-fits on resize if the user hasn't moved the view.
    let fittedView = null, resizeFrame = 0;
    let layoutLive = fresh && !parsed.hasLayout, layoutAspect = aspect;
    function sizeToWindow() {
        if (!el.isConnected) return;
        const top = viewport.getBoundingClientRect().top;
        // leave room for whatever follows the editor in its host (e.g. the Advanced source toggle)
        const host = el.closest('.erd-host');
        let after = 0;
        for (let n = host?.nextElementSibling; n; n = n.nextElementSibling) after += n.getBoundingClientRect().height + 8;
        const height = Math.max(220, Math.round(availableBottom(el) - Math.max(0, top) - 16 - after));
        if (viewport.style.height !== `${height}px`) viewport.style.height = `${height}px`;
    }
    function onResize() {
        cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => {
            const refit = fittedView && fittedView === view;
            sizeToWindow();
            // A diagram whose layout was never saved or edited is re-packed for the new canvas shape;
            // saved / moved positions are never touched.
            const aspectNow = (viewport.clientWidth || 1) / (viewport.clientHeight || 1);
            if (layoutLive && refit && Math.abs(aspectNow - layoutAspect) / layoutAspect > 0.15) {
                layoutAspect = aspectNow;
                autoLayout(store.state, { aspect: aspectNow });
                render();
            }
            if (refit) fit();
        });
    }
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(onResize) : null;
    resizeObserver?.observe(el);
    window.addEventListener('resize', onResize);
    function zoomAt(factor, cx, cy) {
        const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.zoom * factor));
        const k = zoom / view.zoom;
        view = { zoom, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k };
        store.setView(view);
        applyView();
    }
    const toWorld = (clientX, clientY) => {
        const r = viewport.getBoundingClientRect();
        return [(clientX - r.left - view.x) / view.zoom, (clientY - r.top - view.y) / view.zoom];
    };

    // ---- rendering --------------------------------------------------------------------
    function tableHtml(t) {
        const sel = selected?.kind === 'table' && selected.name === t.name;
        return `<div class="erd-table${sel ? ' selected' : ''}" data-table="${esc(t.name)}" tabindex="0" role="group" aria-label="Table ${esc(t.name)}" aria-keyshortcuts="Shift+F10 ContextMenu Delete Enter" style="transform: translate(${t.x}px, ${t.y}px); width: ${TABLE_WIDTH}px">
            <header class="erd-table-head" data-drag="${esc(t.name)}"><strong title="${esc(t.alias || t.name)}">${esc(t.name)}</strong>
                <span class="erd-handle" data-handle="${esc(t.name)}" title="Drag to another table to relate" aria-label="Draw relation from ${esc(t.name)}"></span></header>
            <ul>${t.fields.map((f) => `<li><span class="erd-keys">${esc((f.keys || []).join(' '))}</span><span class="erd-field">${esc(f.name)}${f.nullable ? '?' : ''}</span><span class="erd-type">${esc(f.type)}</span></li>`).join('')}
            ${t.raw.length ? `<li class="erd-raw" title="${esc(t.raw.join('\n'))}">+${t.raw.length} raw line(s)</li>` : ''}</ul>
            <button type="button" class="erd-add-field" data-add-field="${esc(t.name)}">+ field</button>
        </div>`;
    }
    function relationEl(r, i, byName) {
        const a = byName.get(r.from), b = byName.get(r.to);
        if (!a || !b) return '';
        const g = relationGeometry(a, b);
        const m1 = markerPath(r.left), m2 = markerPath(r.right);
        const marker = (m, p, d) => `<g transform="${markerTransform(p, d)}"><path d="${m.d}"/>${m.circle ? `<circle cx="${m.circle.cx}" cy="0" r="${m.circle.r}"/>` : ''}</g>`;
        const mid = [(g.p1[0] + g.p2[0]) / 2, (g.p1[1] + g.p2[1]) / 2];
        const sel = selected?.kind === 'relation' && selected.index === i;
        return `<g class="erd-rel${sel ? ' selected' : ''}${r.identifying === false ? ' dashed' : ''}" data-rel="${i}" data-from="${esc(r.from)}" data-to="${esc(r.to)}">
            <path class="erd-hit" d="${g.path}"/><path class="erd-line" d="${g.path}"/>${marker(m1, g.p1, g.d1)}${marker(m2, g.p2, g.d2)}
            ${r.label ? `<text x="${mid[0]}" y="${mid[1] - 6}">${esc(r.label)}</text>` : ''}</g>`;
    }
    function renderLines(only) {
        const s = store.state;
        const byName = new Map(s.tables.map((t) => [t.name, t]));
        if (!only) { lines.innerHTML = s.relations.map((r, i) => relationEl(r, i, byName)).join(''); return; }
        lines.querySelectorAll('[data-rel]').forEach((g) => {
            if (g.dataset.from !== only && g.dataset.to !== only) return;
            const i = Number(g.dataset.rel);
            g.outerHTML = relationEl(s.relations[i], i, byName);
        });
    }
    function sizeLayers() {
        const b = bounds(store.state);
        for (const svg of [lines, $('.erd-draft')]) {
            const w = Math.max(b.x + b.w + 600, 2000), h = Math.max(b.y + b.h + 600, 1400);
            const x = Math.min(0, b.x - 300), y = Math.min(0, b.y - 300);
            svg.setAttribute('viewBox', `${x} ${y} ${w - x} ${h - y}`);
            Object.assign(svg.style, { left: `${x}px`, top: `${y}px`, width: `${w - x}px`, height: `${h - y}px` });
        }
    }
    function render() {
        nodesEl.innerHTML = store.state.tables.map(tableHtml).join('');
        sizeLayers();
        renderLines();
        renderPanel();
        renderCallout();
        $('[data-erd="undo"]').disabled = !store.canUndo();
        $('[data-erd="redo"]').disabled = !store.canRedo();
    }

    // ---- call-out (top right): stats + hint, dismissed for this browser session -------
    const CALLOUT_KEY = 'decisions.erd.callout.dismissed';
    const calloutDismissed = () => { try { return sessionStorage.getItem(CALLOUT_KEY) === '1'; } catch { return false; } };
    function renderCallout() {
        const box = $('.erd-callout');
        const s = store.state;
        box.querySelector('.erd-callout-stats').textContent = `${s.tables.length} ${s.tables.length === 1 ? 'table' : 'tables'}, ${s.relations.length} ${s.relations.length === 1 ? 'relation' : 'relations'}`;
        box.hidden = calloutDismissed() || Boolean(selected);
    }
    function dismissCallout() {
        try { sessionStorage.setItem(CALLOUT_KEY, '1'); } catch { /* private mode: closes for this view only */ }
        $('.erd-callout').hidden = true;
        viewport.focus({ preventScroll: true });
    }
    function flashTick() {
        const tick = $('.erd-tick');
        tick.hidden = false;
        clearTimeout(flashTick.timer);
        flashTick.timer = setTimeout(() => { tick.hidden = true; }, 1600);
    }

    // ---- side panel (table / relation details) -------------------------------------
    const closeSelection = `<button type="button" class="icon-button erd-selection-close" data-p="close" aria-label="Close selection details" title="Close (Esc)">${icon('M4.5 4.5l7 7M11.5 4.5l-7 7')}</button>`;
    let renderedSelection = null;
    function clearSelection() {
        selected = null;
        render();
        viewport.focus({ preventScroll: true });
    }
    function renderPanel() {
        const s = store.state;
        panel.dataset.mode = selected ? 'edit' : 'hint';   // no selection: the top-right call-out carries the hint
        if (selected !== renderedSelection) {
            panel.scrollTop = 0;
            renderedSelection = selected;
        }
        renderCallout();
        if (selected?.kind === 'table') {
            const t = s.tables.find((x) => x.name === selected.name);
            if (!t) { selected = null; return renderPanel(); }
            panel.innerHTML = `<div class="erd-panel-head"><h4>Table</h4><div><button type="button" class="icon-button erd-panel-more" data-p="more" aria-haspopup="menu" aria-expanded="false" aria-label="Table actions" title="Table actions">${DOTS}</button>${closeSelection}</div></div>
                <label>Name <input data-p="rename" value="${esc(t.name)}" aria-label="Table name"></label>
                <h4>Fields</h4>
                <ol class="erd-fields">${t.fields.map((f, i) => `<li data-field="${i}">
                    <div class="erd-field-row"><input data-f="name" value="${esc(f.name)}" aria-label="Field name">${typeControlHtml(f, i)}</div>
                    <div class="erd-field-row">${KEYS.map((k) => `<label class="erd-check"><input type="checkbox" data-key="${k}"${(f.keys || []).includes(k) ? ' checked' : ''}>${k}</label>`).join('')}
                        <label class="erd-check" title="Editor-only, kept in the layout sidecar"><input type="checkbox" data-f="nullable"${f.nullable ? ' checked' : ''}>null</label></div>
                    <div class="erd-field-row"><input data-f="default" value="${esc(f.default ?? '')}" placeholder="default" aria-label="Default"><input data-f="comment" value="${esc(f.comment)}" placeholder="comment" aria-label="Comment"></div>
                    <div class="erd-field-row erd-field-tools"><button type="button" class="icon-button" data-move="-1" aria-label="Move up">↑</button><button type="button" class="icon-button" data-move="1" aria-label="Move down">↓</button><button type="button" class="icon-button" data-del-field aria-label="Delete field">×</button></div>
                </li>`).join('')}</ol>
                <button type="button" class="header-button" data-p="add-field">Add field</button>
                ${t.raw.length ? `<p class="plan-empty-hint">Raw lines kept: <code>${t.raw.map(esc).join('<br>')}</code></p>` : ''}`;
            return;
        }
        if (selected?.kind === 'relation') {
            const r = s.relations[selected.index];
            if (!r) { selected = null; return renderPanel(); }
            const opts = (side, cur) => Object.entries(CARDINALITY[side]).map(([k, v]) => `<option value="${esc(k)}"${k === cur ? ' selected' : ''}>${v}</option>`).join('');
            panel.innerHTML = `<div class="erd-panel-head"><h4>Relation</h4>${closeSelection}</div><p><strong>${esc(r.from)}</strong> → <strong>${esc(r.to)}</strong></p>
                <label>Preset <select data-r="preset" aria-label="Cardinality preset"><option value="">Custom</option>${RELATION_PRESETS.map(([n, l, rr]) => `<option value="${l}|${rr}"${l === r.left && rr === r.right ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
                <label>${esc(r.from)} side <select data-r="left">${opts('left', r.left)}</select></label>
                <label>${esc(r.to)} side <select data-r="right">${opts('right', r.right)}</select></label>
                <label class="erd-check"><input type="checkbox" data-r="identifying"${r.identifying !== false ? ' checked' : ''}>Identifying (solid line)</label>
                <label>Label <input data-r="label" value="${esc(r.label)}" aria-label="Relation label"></label>
                <button type="button" class="header-button danger" data-r="delete">Delete relation</button>`;
            return;
        }
        panel.innerHTML = `<p class="plan-empty-hint">${s.tables.length} tables · ${s.relations.length} relations. Select a table or relation to edit it. Drag the ● on a table onto another table to relate them. Hold Command and scroll to zoom; drag empty space to pan.</p>`;
    }

    panel.addEventListener('change', (e) => {
        const target = e.target;
        if (selected?.kind === 'table') {
            const name = selected.name;
            if (target.dataset.p === 'rename') { if (store.renameTable(name, target.value)) selected = { kind: 'table', name: store.state.tables.find((t) => t.name === target.value.trim().replace(/\s+/g, '_').replace(/[^\w-]/g, ''))?.name || name }; else target.value = name; return; }
            const li = target.closest('[data-field]');
            if (!li) return;
            const index = Number(li.dataset.field);
            const field = store.state.tables.find((t) => t.name === name).fields[index];
            if (target.dataset.key) {
                const keys = new Set(field.keys || []);
                target.checked ? keys.add(target.dataset.key) : keys.delete(target.dataset.key);
                store.updateField(name, index, { keys: KEYS.filter((k) => keys.has(k)) });
            }
            else if (target.dataset.f === 'nullable') store.updateField(name, index, { nullable: target.checked || undefined });
            else if (target.dataset.f === 'type-base') {
                if (target.value === '__keep') return;
                if (target.value === '__custom') { const input = li.querySelector('[data-f="type"]'); input.hidden = false; li.querySelector('[data-f="type-param"]').hidden = true; input.focus(); input.select(); return; }
                const prev = splitType(field.type);
                store.updateField(name, index, { type: joinType(target.value, PARAM_TYPES.has(target.value) ? prev.param : '') });
                panel.querySelector(`#erd-type-${index}`)?.focus();
            } else if (target.dataset.f === 'type-param') {
                const t = splitType(field.type);
                if (t.known) store.updateField(name, index, { type: joinType(t.base, target.value) });
            } else if (target.dataset.f === 'type') { if (target.value.trim()) store.updateField(name, index, { type: target.value.trim() }); }
            else if (target.dataset.f) store.updateField(name, index, { [target.dataset.f]: target.value });
        } else if (selected?.kind === 'relation') {
            const i = selected.index;
            const k = target.dataset.r;
            if (k === 'preset' && target.value) { const [left, right] = target.value.split('|'); store.updateRelation(i, { left, right }); }
            else if (k === 'left' || k === 'right' || k === 'label') store.updateRelation(i, { [k]: target.value });
            else if (k === 'identifying') store.updateRelation(i, { identifying: target.checked });
        }
    });
    panel.addEventListener('click', async (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.p === 'close') { clearSelection(); return; }
        if (selected?.kind === 'table') {
            const name = selected.name;
            const li = b.closest('[data-field]');
            if (b.dataset.p === 'more') {
                if (openMenuApi && b.getAttribute('aria-expanded') === 'true') { openMenuApi.close(true); return; }
                openMenu({ anchor: b.getBoundingClientRect(), opener: b, items: tableMenuItems(name, { edit: false }) });
            }
            else if (b.dataset.p === 'add-field') store.addField(name);
            else if (li && b.dataset.move) store.moveField(name, Number(li.dataset.field), Number(b.dataset.move));
            else if (li && 'delField' in b.dataset) store.deleteField(name, Number(li.dataset.field));
        } else if (selected?.kind === 'relation' && b.dataset.r === 'delete') {
            if (await askConfirm('Delete this relation?')) { const i = selected.index; selected = null; store.deleteRelation(i); }
        }
    });
    panel.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        e.preventDefault();
        clearSelection();
    });

    // ---- table actions (panel ⋯ menu, canvas context menu, keyboard) -------------------
    function editTable(name) {
        selected = { kind: 'table', name };
        render();
        panel.querySelector('[data-p="rename"]')?.focus();
    }
    function duplicateTableAction(name) { const n = store.duplicateTable(name); if (n) { selected = { kind: 'table', name: n }; render(); } }
    async function deleteTableAction(name, { returnTo = null } = {}) {
        if (await askConfirm(`Delete table ${name} and its relations?`)) { selected = null; store.deleteTable(name); viewport.focus({ preventScroll: true }); return true; }
        const back = returnTo?.isConnected ? returnTo : nodesEl.querySelector(`[data-table="${CSS.escape(name)}"]`);
        back?.focus({ preventScroll: true });
        return false;
    }
    function tableMenuItems(name, { edit = true } = {}) {
        return [
            ...(edit ? [{ label: 'Edit', icon: 'edit', run: () => editTable(name) }] : []),
            { label: 'View data', icon: 'view', run: () => openData(name) },
            { label: 'Duplicate', icon: 'duplicate', run: () => duplicateTableAction(name) },
            { label: 'Delete', icon: 'delete', danger: true, run: () => deleteTableAction(name) },
        ];
    }
    // ---- view data: one read-only page of the linked table --------------------------
    const dataEl = $('.erd-data');
    const PAGE_SIZE = 50;
    let dataReq = 0;
    let dataState = null;
    let dataSkel = 0;
    const onDataKey = (e) => {
        if (e.key !== 'Escape' || dataEl.hidden || document.querySelector('.erd-menu')) return;
        e.preventDefault();
        closeData();
    };
    function closeData() {
        dataReq += 1;
        clearTimeout(dataSkel);
        dataState = null;
        dataEl.hidden = true;
        dataEl.innerHTML = '';
        document.removeEventListener('keydown', onDataKey, true);
        viewport.focus({ preventScroll: true });
    }
    function dbLink(name) {
        const table = store.state.tables.find((item) => item.name === name);
        const db = table?.sidecarExtra?.db;
        if (!db?.link || !db.schema || !db.table) return null;
        return db;
    }
    function rangeLabel(state) {
        if (!state.total) return '0 rows';
        const start = (state.page - 1) * state.pageSize + 1;
        const end = Math.min(state.total, state.page * state.pageSize);
        return `${start}-${end} of ${state.total}`;
    }
    function numericColumn(rows, index) {
        const values = rows.map((row) => row[index]).filter((value) => value !== null && value !== '');
        return values.length > 0 && values.every((value) => typeof value === 'number' || /^-?\d+(\.\d+)?$/.test(String(value)));
    }
    function renderData() {
        const state = dataState;
        if (!state) { dataEl.hidden = true; return; }
        dataEl.hidden = false;
        const title = state.db ? `${state.db.schema}.${state.db.table}` : state.name;
        const busy = state.status === 'loading';
        let body = '';
        if (state.unlinked) {
            body = `<div class="erd-data-state"><p>${esc(state.error)}</p></div>`;
        } else if (state.status === 'error') {
            body = `<div class="erd-data-state" role="alert"><p>Couldn't load ${esc(state.db?.table || state.name)}. ${esc(state.error || 'Check the database link and try again.')}</p><button type="button" class="header-button" data-retry>Retry</button></div>`;
        } else if (busy && state.skeleton && !state.rows.length) {
            body = `<div class="erd-data-skel" aria-hidden="true"></div><div class="erd-data-skel"></div><div class="erd-data-skel"></div><div class="erd-data-skel"></div><div class="erd-data-skel"></div><div class="erd-data-skel"></div><p class="erd-sr">Loading rows…</p>`;
        } else if (state.status === 'ready' && !state.total) {
            body = `<div class="erd-data-state"><p>No rows in ${esc(state.db.table)}.</p></div>`;
        } else if (state.rows.length) {
            const nums = state.columns.map((_, index) => numericColumn(state.rows, index));
            body = `<div class="erd-data-scroll"><table><thead><tr>${state.columns.map((name) => `<th scope="col">${esc(name)}</th>`).join('')}</tr></thead><tbody>${state.rows.map((row) => `<tr>${row.map((value, index) => `<td class="${nums[index] ? 'num' : ''}"${value != null && String(value).length > 40 ? ` title="${esc(value)}"` : ''}>${value == null ? '<span class="erd-data-null">null</span>' : esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${busy ? '<p class="erd-sr">Loading rows…</p>' : ''}`;
        } else if (busy) {
            body = '<p class="erd-sr">Loading rows…</p>';
        }
        const foot = state.status === 'ready' || (busy && state.rows.length) ? `<footer class="erd-data-foot"><span>${rangeLabel(state)}</span><button type="button" class="header-button" data-page="prev"${state.page <= 1 || busy ? ' disabled' : ''}>Previous</button><button type="button" class="header-button" data-page="next"${busy || state.page * state.pageSize >= state.total ? ' disabled' : ''}>Next</button></footer>` : '';
        const inside = dataEl.contains(document.activeElement) ? (document.activeElement.dataset.page || 'close') : '';
        dataEl.innerHTML = `<header class="erd-data-head"><h2>${esc(title)}</h2><button type="button" class="erd-callout-close" data-close aria-label="Close rows" title="Close">${icon('M4.5 4.5l7 7M11.5 4.5l-7 7')}</button></header><div class="erd-data-body"${busy ? ' aria-busy="true"' : ''}>${body}</div>${foot}`;
        dataEl.setAttribute('aria-busy', busy ? 'true' : 'false');
        const focus = state.autofocus ? 'close' : inside;
        state.autofocus = false;
        if (focus) (focus === 'close' ? dataEl.querySelector('[data-close]') : dataEl.querySelector(`[data-page="${focus}"]`))?.focus();
    }
    async function fetchData() {
        const state = dataState;
        if (!state?.db) return;
        const token = ++dataReq;
        state.status = 'loading';
        state.error = '';
        state.skeleton = false;
        clearTimeout(dataSkel);
        if (!state.rows.length) dataSkel = setTimeout(() => { if (token === dataReq && dataState?.status === 'loading') { dataState.skeleton = true; renderData(); } }, 300);
        renderData();
        try {
            if (!onViewRows) throw new Error('The request failed.');
            const data = await onViewRows({ link_id: state.db.link, schema: state.db.schema, table: state.db.table, page: state.page, page_size: state.pageSize });
            if (token !== dataReq) return;
            state.columns = Array.isArray(data.columns) ? data.columns : [];
            state.rows = Array.isArray(data.rows) ? data.rows : [];
            state.total = Number(data.total) || 0;
            state.page = Number(data.page) || state.page;
            state.pageSize = Number(data.page_size) || state.pageSize;
            state.status = 'ready';
        } catch (error) {
            if (token !== dataReq) return;
            state.status = 'error';
            state.error = error?.message || 'The request failed.';
            state.rows = [];
            state.columns = [];
        } finally {
            clearTimeout(dataSkel);
            if (token === dataReq) renderData();
        }
    }
    function openData(name) {
        const db = dbLink(name);
        dataReq += 1;
        dataState = { name, db, page: 1, pageSize: PAGE_SIZE, status: db ? 'loading' : 'error', error: '', columns: [], rows: [], total: 0, skeleton: false, unlinked: !db, autofocus: true };
        document.removeEventListener('keydown', onDataKey, true);
        document.addEventListener('keydown', onDataKey, true);
        if (!db) {
            dataState.error = 'This table is only on the diagram, so there are no database rows to show.';
            renderData();
            return;
        }
        fetchData();
    }
    dataEl.addEventListener('click', (e) => {
        const button = e.target.closest('button');
        if (!button || !dataState) return;
        if ('close' in button.dataset) closeData();
        else if ('retry' in button.dataset) fetchData();
        else if (button.dataset.page === 'prev' && dataState.page > 1) { dataState.page -= 1; fetchData(); }
        else if (button.dataset.page === 'next' && dataState.page * dataState.pageSize < dataState.total) { dataState.page += 1; fetchData(); }
    });

    const selectNode = (name) => {
        if (selected?.kind === 'table' && selected.name === name) return;
        selected = { kind: 'table', name };
        nodesEl.querySelectorAll('.erd-table.selected').forEach((n) => n.classList.remove('selected'));
        nodesEl.querySelector(`[data-table="${CSS.escape(name)}"]`)?.classList.add('selected');
        renderPanel();
    };
    viewport.addEventListener('contextmenu', (e) => {
        const node = e.target.closest('[data-table]');
        if (!node || !nodesEl.contains(node)) return;
        e.preventDefault();
        const name = node.dataset.table;
        selectNode(name);
        openMenu({ x: e.clientX, y: e.clientY, opener: nodesEl.querySelector(`[data-table="${CSS.escape(name)}"]`), items: tableMenuItems(name) });
    });
    viewport.addEventListener('keydown', (e) => {
        if (e.target.matches('input, select, textarea')) return;
        if (e.target === viewport && Object.hasOwn(PAN_KEYS, e.key)) {
            e.preventDefault();
            const [dx, dy] = PAN_KEYS[e.key];
            view = { ...view, x: view.x + dx, y: view.y + dy };
            store.setView(view);
            applyView();
            return;
        }
        const focused = e.target.closest?.('[data-table]');
        const name = focused?.dataset.table || (selected?.kind === 'table' ? selected.name : null);
        if (!name) return;
        if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
            e.preventDefault();
            selectNode(name);
            const node = nodesEl.querySelector(`[data-table="${CSS.escape(name)}"]`);
            const r = node.getBoundingClientRect();
            openMenu({ x: r.left + Math.min(24, r.width / 2), y: r.top + Math.min(24, r.height / 2), opener: node, items: tableMenuItems(name) });
        } else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteTableAction(name, { returnTo: focused }); }
        else if (e.key === 'Enter' && focused) { e.preventDefault(); editTable(name); }
    });

    // ---- toolbar ------------------------------------------------------------------------
    el.querySelector('.erd-body').addEventListener('click', (e) => {
        const button = e.target.closest('[data-erd]');
        if (!button || !el.contains(button) || button.closest('.erd-panel')) return;
        const action = button.dataset.erd;
        if (action === 'callout-close') { dismissCallout(); return; }
        const r = viewport.getBoundingClientRect();
        if (action === 'zoom-in') zoomAt(1.2, r.width / 2, r.height / 2);
        else if (action === 'zoom-out') zoomAt(1 / 1.2, r.width / 2, r.height / 2);
        else if (action === 'fit') fit();
        else if (action === 'undo') store.undo();
        else if (action === 'redo') store.redo();
        else if (action === 'add-table') {
            const [x, y] = toWorld(r.left + r.width / 2 - 110 * view.zoom, r.top + r.height / 2 - 40 * view.zoom);
            const name = store.addTable('NEW_TABLE', x, y);
            selected = { kind: 'table', name };
            render();
        }
    });
    el.addEventListener('keydown', (e) => {
        if (e.target.matches('input, select, textarea')) return;
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? store.redo() : store.undo(); }
    });

    // ---- pointer: pan, drag tables, draw relations, select ---------------------------
    let gesture = null, frame = 0;
    viewport.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        const handle = e.target.closest('[data-handle]');
        const head = e.target.closest('[data-drag]');
        const addField = e.target.closest('[data-add-field]');
        const rel = e.target.closest('[data-rel]');
        const node = e.target.closest('[data-table]');
        if (addField) { const name = addField.dataset.addField; selected = { kind: 'table', name }; store.addField(name); return; }
        if (handle) {
            gesture = { kind: 'link', from: handle.dataset.handle, start: toWorld(e.clientX, e.clientY) };
        } else if (head || node) {
            const name = (head || node).dataset.drag || node.dataset.table;
            const t = store.state.tables.find((x) => x.name === name);
            gesture = { kind: 'move', name, node: nodesEl.querySelector(`[data-table="${CSS.escape(name)}"]`), start: [e.clientX, e.clientY], origin: [t.x, t.y], moved: false };
            if (selected?.kind !== 'table' || selected.name !== name) { selected = { kind: 'table', name }; nodesEl.querySelectorAll('.erd-table.selected').forEach((n) => n.classList.remove('selected')); gesture.node.classList.add('selected'); renderPanel(); }
        } else if (rel) {
            selected = { kind: 'relation', index: Number(rel.dataset.rel) };
            renderLines(); renderPanel();
            return;
        } else {
            gesture = { kind: 'pan', start: [e.clientX, e.clientY], origin: [view.x, view.y], moved: false };
        }
        viewport.setPointerCapture(e.pointerId);
        e.preventDefault();
    });
    viewport.addEventListener('pointermove', (e) => {
        if (!gesture) return;
        gesture.last = [e.clientX, e.clientY];
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            const g = gesture;
            if (!g?.last) return;
            if (g.kind === 'pan') {
                view = { ...view, x: g.origin[0] + g.last[0] - g.start[0], y: g.origin[1] + g.last[1] - g.start[1] };
                g.moved = true;
                applyView();
            } else if (g.kind === 'move') {
                const t = store.state.tables.find((x) => x.name === g.name);
                t.x = g.origin[0] + (g.last[0] - g.start[0]) / view.zoom;   // live preview; committed on pointerup
                t.y = g.origin[1] + (g.last[1] - g.start[1]) / view.zoom;
                g.moved = true;
                g.node.style.transform = `translate(${t.x}px, ${t.y}px)`;
                renderLines(g.name);
            } else if (g.kind === 'link') {
                const [x, y] = toWorld(g.last[0], g.last[1]);
                draft.setAttribute('d', `M${g.start} L${x},${y}`);
            }
        });
    });
    const endGesture = (e) => {
        const g = gesture;
        gesture = null;
        if (frame) { cancelAnimationFrame(frame); frame = 0; }
        if (!g) return;
        if (g.kind === 'pan') { store.setView(view); if (!g.moved && selected) { selected = null; render(); } }
        else if (g.kind === 'move') {
            const t = store.state.tables.find((x) => x.name === g.name);
            if (g.moved) { const [x, y] = [t.x, t.y]; t.x = g.origin[0]; t.y = g.origin[1]; store.moveTable(g.name, x, y); }
        } else if (g.kind === 'link') {
            draft.setAttribute('d', '');
            // Look through the floating panel (it can cover part of the canvas) for the table under the pointer.
            const target = document.elementsFromPoint(e.clientX, e.clientY).map((n) => n.closest?.('[data-table]')).find((n) => n && nodesEl.contains(n));
            if (target && el.contains(target)) {
                const index = store.addRelation(g.from, target.dataset.table);
                if (index !== false) { selected = { kind: 'relation', index }; render(); }
            }
        }
    };
    viewport.addEventListener('pointerup', endGesture);
    viewport.addEventListener('pointercancel', endGesture);
    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const r = viewport.getBoundingClientRect();
        // Command+scroll zooms (up in, down out). Control and a plain scroll pan. Chrome also sets ctrlKey for Command+scroll, so the check is Meta only.
        if (e.metaKey || e.getModifierState?.('Meta')) zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
        else { view = { ...view, x: view.x - e.deltaX, y: view.y - e.deltaY }; store.setView(view); applyView(); }
    }, { passive: false });

    // ---- autosave (existing plan item PATCH via onSave) -----------------------------
    function setStatus(next, message = '') {
        status = next; statusMessage = message;
        const node = $('.erd-status');
        node.dataset.state = next;
        node.textContent = { saved: 'Saved', unsaved: 'Unsaved changes', saving: 'Saving…', error: `Not saved: ${message}`, conflict: `Not saved: ${message}` }[next];
        node.title = node.textContent;
        $('.erd-reload').hidden = next !== 'conflict';
        el.dataset.saveState = next;                         // exposed for tests / styling; no persistent badge
        $('.erd-alert').hidden = next !== 'error' && next !== 'conflict';
    }
    // Only a stale-revision answer locks autosave (until "Reload latest"); a validation error (422) or a
    // network error stays visible and the next edit retries.
    const isRevisionConflict = (error) => /newer revision/i.test(error?.message || '') || (error?.status === 409 && !error?.message);
    async function flush() {
        clearTimeout(saveTimer); saveTimer = null;
        if (conflict || !onSave) return;
        if (saving) { await saving; if (status !== 'unsaved' || conflict) return; }
        const content = emitErd(store.state);
        if (content === savedText) { setStatus('saved'); return; }
        setStatus('saving');
        saving = (async () => {
            try { await onSave(content); savedText = content; flashTick(); setStatus(store.state && emitErd(store.state) === savedText ? 'saved' : 'unsaved'); }
            catch (error) {
                if (isRevisionConflict(error)) { conflict = true; setStatus('conflict', 'a newer version was saved elsewhere.'); }
                else setStatus('error', error?.message || 'save failed');
            }
            finally { saving = null; }
        })();
        await saving;
        if (status === 'unsaved' && !conflict) schedule();
    }
    function schedule() { if (conflict) return; setStatus('unsaved'); clearTimeout(saveTimer); saveTimer = setTimeout(() => { if (!saving) flush(); else schedule(); }, debounceMs); }
    store.subscribe(() => { layoutLive = false; render(); schedule(); });
    $('.erd-reload').addEventListener('click', () => onReloadLatest?.(emitErd(store.state)));

    view = view || { zoom: 1, x: 20, y: 20 };
    render();
    applyView();
    const api = {
        el, store,
        get dirty() { return status === 'unsaved' || status === 'saving' || status === 'conflict' || (status === 'error' && emitErd(store.state) !== savedText); },
        get conflict() { return conflict; },
        get savedText() { return savedText; },
        get status() { return status; },
        fit, flush, zoomAt,
        mounted() {
            sizeToWindow();
            const aspectNow = (viewport.clientWidth || 1) / (viewport.clientHeight || 1);
            if (layoutLive && Math.abs(aspectNow - layoutAspect) / layoutAspect > 0.05) { layoutAspect = aspectNow; autoLayout(store.state, { aspect: aspectNow }); render(); }
            if (!parsed.model.view) fit({ initial: true }); else applyView();
        },
        destroy() { openMenuApi?.close(false); clearTimeout(saveTimer); clearTimeout(dataSkel); document.removeEventListener('keydown', onDataKey, true); resizeObserver?.disconnect(); window.removeEventListener('resize', onResize); el.remove(); },
    };
    return api;
}
