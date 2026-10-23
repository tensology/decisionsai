// Shared circular "Loop" ring for workflow steps (Workflows page + thread runner card).
export const WORKFLOW_VIEW_KEY = 'decisions.workflowViewMode';

let loopSeq = 0;
// Page-wide unique suffix so SVG marker ids never collide between cards.
export function nextWorkflowLoopId(prefix = 'workflow-loop') {
    loopSeq += 1;
    return `${prefix}-${loopSeq}`;
}

export function readWorkflowViewMode() {
    try {
        return window.localStorage.getItem(WORKFLOW_VIEW_KEY) === 'list' ? 'list' : 'loop';
    } catch (_error) {
        return 'loop';
    }
}

export function writeWorkflowViewMode(mode) {
    const value = mode === 'list' ? 'list' : 'loop';
    try {
        window.localStorage.setItem(WORKFLOW_VIEW_KEY, value);
    } catch (_error) {
        /* storage unavailable */
    }
    return value;
}

const fallbackEscape = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

export function workflowViewSwitchHtml(mode, { attr = 'data-workflow-view', className = 'workflow-view-switch', label = 'Workflow view' } = {}) {
    const current = mode === 'list' ? 'list' : 'loop';
    return `<div class="${className}" role="group" aria-label="${label}"><button type="button" ${attr}="loop" aria-pressed="${current === 'loop'}"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M15.5 7A6 6 0 1 0 16 12"></path><path d="m13 4 3 3-3 3"></path></svg>Loop</button><button type="button" ${attr}="list" aria-pressed="${current === 'list'}"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M6 5h10M6 10h10M6 15h10"></path><circle cx="3" cy="5" r=".7"></circle><circle cx="3" cy="10" r=".7"></circle><circle cx="3" cy="15" r=".7"></circle></svg>List</button></div>`;
}

/**
 * options: { mode ('design' hides status pills + runtime highlight; adds .selected for selectedStepId), compact, activeStepId, idPrefix, escapeHtml, statusLabel, coreLabel, coreCaption,
 *            emptyHtml, ariaLabel, mapRole, buttonAttrs(step, index), nodeExtraHtml(step, index) }
 */
export function workflowLoopHtml(steps, options = {}) {
    const esc = options.escapeHtml || fallbackEscape;
    const statusLabel = options.statusLabel || ((status) => status);
    const compact = Boolean(options.compact);
    const list = Array.isArray(steps) ? steps : [];
    if (!list.length) return options.emptyHtml ?? '<div class="workflow-empty-steps">No steps yet.</div>';
    const count = list.length;
    const prefix = String(options.idPrefix || 'workflow-loop').replace(/[^a-zA-Z0-9_-]/g, '-');
    const markerId = `${prefix}-arrow`;
    const activeId = options.activeStepId == null || options.activeStepId === '' ? null : String(options.activeStepId);
    const nodes = list
        .map((step, index) => {
            const angle = ((-90 + (360 * index) / count) * Math.PI) / 180;
            const x = 50 + 40 * Math.cos(angle);
            const y = 50 + 37 * Math.sin(angle);
            const mobileX = 50 + 24 * Math.cos(angle);
            const mobileY = 50 + 38 * Math.sin(angle);
            const design = options.mode === 'design';
            const status = design ? 'design' : String(step.status || 'pending').toLowerCase();
            const name = step.name || `Step ${index + 1}`;
            const current = !design && activeId !== null && String(step.id) === activeId;
            const selected = options.selectedStepId != null && String(step.id) === String(options.selectedStepId);
            const attrs = options.buttonAttrs ? options.buttonAttrs(step, index) : '';
            const extra = !compact && options.nodeExtraHtml ? options.nodeExtraHtml(step, index) : '';
            const inner = compact
                ? `<b>${String(index + 1).padStart(2, '0')}</b><strong>${esc(name)}</strong>`
                : `<span class="workflow-loop-node-top"><b>${String(index + 1).padStart(2, '0')}</b>${design ? '' : `<i>${esc(statusLabel(status))}</i>`}</span><strong>${esc(name)}</strong>${extra}`;
            const tip = design ? '' : ` title="${esc(`${index + 1}. ${name} — ${statusLabel(status)}`)}"`;
            return `<article class="workflow-loop-node ${esc(status)}${current ? ' current' : ''}${selected ? ' selected' : ''}" role="${compact ? 'presentation' : 'listitem'}" style="--loop-x:${x.toFixed(2)}%;--loop-y:${y.toFixed(2)}%;--loop-mobile-x:${mobileX.toFixed(2)}%;--loop-mobile-y:${mobileY.toFixed(2)}%">
                <button type="button" ${attrs}${tip}>${inner}</button>
            </article>`;
        })
        .join('');
    const coreLabel = options.coreLabel ?? 'Ready';
    const coreCaption = options.coreCaption ?? 'step loop';
    const role = options.mapRole || 'list';
    const ariaLabel = options.ariaLabel || `Workflow loop with ${count} steps`;
    return `<div class="workflow-loop-viewport${compact ? ' compact' : ''}"><div class="workflow-loop-map${count > 8 ? ' dense' : ''}${compact ? ' compact' : ''}" role="${role}" aria-label="${esc(ariaLabel)}">
            <svg class="workflow-loop-path" aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none"><defs><marker id="${markerId}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z"></path></marker></defs><path d="M50 6 A44 44 0 0 1 94 50" marker-end="url(#${markerId})"></path><path d="M94 50 A44 44 0 0 1 50 94" marker-end="url(#${markerId})"></path><path d="M50 94 A44 44 0 0 1 6 50" marker-end="url(#${markerId})"></path><path d="M6 50 A44 44 0 0 1 50 6" marker-end="url(#${markerId})"></path></svg>
            <div class="workflow-loop-core"><span>${esc(coreLabel)}</span><strong>${count}</strong><small>${esc(coreCaption)}</small></div>${nodes}
        </div></div>`;
}

// ---- Numbered step strip (1, 2, 3...) with a reveal panel. Shared by the thread card and Workflows page. ----
export function workflowStepStatusText(status) {
    const value = String(status || 'pending').toLowerCase();
    if (['done', 'completed', 'success', 'succeeded'].includes(value)) return 'Done';
    if (['running', 'active', 'in_progress'].includes(value)) return 'Running';
    if (['failed', 'error'].includes(value)) return 'Failed';
    if (['waiting', 'paused', 'gate', 'awaiting_gate', 'blocked', 'needs_attention'].includes(value)) return 'Awaiting gate';
    return 'Pending';
}

/** Data attributes carried by every clickable step control (ring node or number). */
export function workflowStepDataAttrs(step, index, { escapeHtml, route = '', output = '', selected = false } = {}) {
    const esc = escapeHtml || fallbackEscape;
    const status = String(step.status || 'pending').toLowerCase();
    const detail = step.description || step.instruction || '';
    return `role="tab" aria-selected="${selected}" tabindex="${selected ? '0' : '-1'}" data-wf-step data-step-index="${index}" data-step-name="${esc(step.name || `Step ${index + 1}`)}" data-step-status="${esc(status)}" data-step-route="${esc(route)}" data-step-detail="${esc(detail)}" data-step-output="${esc(String(output || '').slice(0, 280))}"`;
}

export function workflowStepPanelInner({ name, status, detail, route, output }, escapeHtml) {
    const esc = escapeHtml || fallbackEscape;
    const stateClass = String(status || 'pending').toLowerCase().replace(/[^a-z_-]/g, '');
    return `<div><strong>${esc(name || 'Workflow step')}</strong><em class="workflow-step-pill ${stateClass}">${esc(workflowStepStatusText(status))}</em>${route ? `<span>${esc(route)}</span>` : ''}</div><p>${esc(detail || 'No step description is available yet.')}</p>${output ? `<p class="workflow-step-output"><b>Latest output</b>${esc(output)}</p>` : ''}`;
}

/**
 * steps: [{id, name, status, description|instruction}]
 * options: { activeStepId, selectedStepId, idPrefix, escapeHtml, routeFor(step), outputFor(step), ariaLabel, panel (bool, default true) }
 */
export function workflowStepNumbersHtml(steps, options = {}) {
    const esc = options.escapeHtml || fallbackEscape;
    const list = Array.isArray(steps) ? steps : [];
    if (!list.length) return '';
    const activeId = options.activeStepId == null || options.activeStepId === '' ? null : String(options.activeStepId);
    const pickId = options.selectedStepId == null || options.selectedStepId === '' ? activeId : String(options.selectedStepId);
    let activeIndex = pickId === null ? -1 : list.findIndex((step) => String(step.id) === pickId);
    if (activeIndex < 0 && options.mode !== 'design') activeIndex = 0;
    const prefix = String(options.idPrefix || 'workflow-steps').replace(/[^a-zA-Z0-9_-]/g, '-');
    const buttons = list
        .map((step, index) => {
            const design = options.mode === 'design';
            const status = design ? 'design' : String(step.status || 'pending').toLowerCase();
            const current = !design && activeId !== null && String(step.id) === activeId;
            const attrs = workflowStepDataAttrs(step, index, {
                escapeHtml: esc,
                route: options.routeFor ? options.routeFor(step) : '',
                output: options.outputFor ? options.outputFor(step) : '',
                selected: index === activeIndex
            });
            return `<li role="presentation" class="workflow-step-number-item ${esc(status)}${current ? ' current' : ''}"><button type="button" ${attrs} aria-controls="${prefix}-panel" aria-label="Step ${index + 1}: ${esc(step.name || `Step ${index + 1}`)} (${esc(workflowStepStatusText(status))})" title="${esc(step.name || `Step ${index + 1}`)}">${index + 1}</button></li>`;
        })
        .join('');
    const first = list[activeIndex];
    const panel =
        options.panel === false
            ? ''
            : `<div class="workflow-step-panel" id="${prefix}-panel" role="tabpanel" data-wf-step-panel aria-live="polite">${workflowStepPanelInner(
                  {
                      name: first.name || `Step ${activeIndex + 1}`,
                      status: first.status,
                      detail: first.description || first.instruction || '',
                      route: options.routeFor ? options.routeFor(first) : '',
                      output: options.outputFor ? options.outputFor(first) : ''
                  },
                  esc
              )}</div>`;
    return `<div class="workflow-step-numbers"><ol role="tablist" aria-label="${esc(options.ariaLabel || 'Workflow steps')}">${buttons}</ol>${panel}</div>`;
}

/** Wire click + keyboard (arrows/Home/End) for every [data-wf-step] in root, revealing into [data-wf-step-panel]. */
export function bindWorkflowStepNumbers(root, { escapeHtml } = {}) {
    if (!root || root.dataset.wfStepsBound === '1') return;
    root.dataset.wfStepsBound = '1';
    const all = () => Array.from(root.querySelectorAll('[data-wf-step]'));
    const select = (button) => {
        const index = button.dataset.stepIndex;
        all().forEach((item) => {
            const on = item.dataset.stepIndex === index;
            item.setAttribute('aria-selected', String(on));
            item.setAttribute('tabindex', on ? '0' : '-1');
        });
        const panel = root.querySelector('[data-wf-step-panel]');
        if (panel)
            panel.innerHTML = workflowStepPanelInner(
                {
                    name: button.dataset.stepName,
                    status: button.dataset.stepStatus,
                    detail: button.dataset.stepDetail,
                    route: button.dataset.stepRoute,
                    output: button.dataset.stepOutput
                },
                escapeHtml
            );
    };
    root.addEventListener('click', (event) => {
        const button = event.target.closest('[data-wf-step]');
        if (button && root.contains(button)) select(button);
    });
    root.addEventListener('keydown', (event) => {
        const button = event.target.closest('.workflow-step-numbers [data-wf-step]');
        if (!button) return;
        const items = Array.from(button.closest('ol').querySelectorAll('[data-wf-step]'));
        const at = items.indexOf(button);
        let next = -1;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (at + 1) % items.length;
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (at - 1 + items.length) % items.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = items.length - 1;
        if (next < 0) return;
        event.preventDefault();
        items[next].focus();
        select(items[next]);
    });
}
