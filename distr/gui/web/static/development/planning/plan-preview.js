/**
 * Plan preview: what the plan actually looks like once something is placed.
 *
 * Placing a library entry persists the plan and returns the rendered wire; this
 * turns that into something visible. Crucially it renders through the SAME
 * wireframe renderer the canvas uses and passes the base template's CSS in, so
 * the preview is governed by the real tokens, contracts and grid - not a
 * lookalike that drifts from what the pages will be.
 *
 * Pure helpers are exported for tests; the DOM wiring is a thin layer.
 */

/**
 * The plan's pages, as the canvas holds them.
 *
 * The plan IS the workspace's flows wire, so the pages come straight from there -
 * there is no second model to reconcile.
 */
export function pageRows(plan) {
    const rows = [];
    const list = (plan && plan.pages) || [];
    for (const page of list) {
        if (!page || !page.name) continue;
        rows.push({
            name: String(page.name),
            title: String(page.title || page.name),
            route: String(page.route || ''),
            depth: 0,
            components: Array.isArray(page.components) ? page.components.length : 0,
        });
    }
    return rows;
}

export function planSummary(plan) {
    const rows = pageRows(plan);
    return {
        pages: rows.length,
        routes: rows.filter((r) => r.route).length,
        components: rows.reduce((total, row) => total + row.components, 0),
        missing: Array.isArray(plan && plan.missing) ? plan.missing.length : 0,
    };
}

/** Render the plan's wire with the base template's CSS applied. */
export function previewHtml(plan, renderWireframe) {
    const wire = String((plan && plan.wire) || '').trim();
    if (!wire || typeof renderWireframe !== 'function') return '';
    try {
        const rendered = renderWireframe(
            wire,
            (plan && plan.baseName) || 'Plan',
            'plan-preview',
            [],
            { standalone: true, baseCss: String((plan && plan.baseCss) || '') },
        );
        return (rendered && rendered.html) || '';
    } catch {
        return '';
    }
}

export function mountPlanPreview(container, { renderWireframe, onOpenPage = () => {} } = {}) {
    const root = container;
    root.innerHTML = '';
    root.classList.add('plan-preview');

    const summary = document.createElement('p');
    summary.className = 'plan-preview-summary';
    summary.setAttribute('role', 'status');

    const list = document.createElement('ul');
    list.className = 'plan-preview-pages';
    list.setAttribute('aria-label', 'Pages in this plan');

    const frame = document.createElement('iframe');
    frame.className = 'plan-preview-frame';
    frame.setAttribute('title', 'Plan preview');
    frame.setAttribute('sandbox', '');
    frame.hidden = true;

    const empty = document.createElement('p');
    empty.className = 'plan-preview-empty';
    empty.textContent = 'Nothing in the plan yet. Drop a page template from the Library to start.';

    root.append(summary, empty, list, frame);

    function paint(plan) {
        const stats = planSummary(plan);
        const rows = pageRows(plan);

        summary.textContent = stats.pages
            ? `${stats.pages} page${stats.pages === 1 ? '' : 's'} in this plan`
              + (stats.missing ? ` · ${stats.missing} missing from the library` : '')
            : '';

        list.innerHTML = '';
        for (const row of rows) {
            const item = document.createElement('li');
            item.className = 'plan-preview-page';
            item.dataset.page = row.name;
            item.style.paddingInlineStart = '8px';
            const route = row.route ? ` <code>${row.route}</code>` : '';
            item.innerHTML = `<strong>${row.title}</strong>${route}`;
            item.addEventListener('click', () => onOpenPage(row));
            list.append(item);
        }

        const html = previewHtml(plan, renderWireframe);
        empty.hidden = Boolean(html);
        list.hidden = !rows.length;
        if (html) {
            frame.hidden = false;
            frame.srcdoc = html;
        } else {
            frame.hidden = true;
            frame.removeAttribute('srcdoc');
        }
    }

    return { setPlan: paint, destroy() { root.innerHTML = ''; } };
}
