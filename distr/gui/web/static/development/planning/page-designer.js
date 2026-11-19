import { renderWireframe } from './wireframe.js?v=20261119-page-designer';
import { addWidget, designPage, editWidgetLabel, insertionLayout, moveWidget, WIDGETS } from './page-designer-source.js?v=20261119-page-designer';

const PALETTE = { row: 'Row', stack: 'Stack', heading: 'Heading', text: 'Text', button: 'Button', input: 'Field' };
const WIDGET_DRAG = 'application/x-decisions-widget';
const labelOf = node => node.label || node.attrs.label || '';

export function mountPageDesigner(surface, { item, pageIndex, draft, assets, onDraft, onSave, onDiscard }) {
    const source = () => draft.source;
    let selection = draft.line;
    let model;
    let busy = false;
    let scroll = 0;
    surface.innerHTML = `<section class="plan-designer" aria-label="Page designer">
        <div class="plan-design-top">
            <div class="plan-design-viewport" role="group" aria-label="Canvas width">
                <button type="button" data-width="wide" aria-pressed="true">Full width</button>
                <button type="button" data-width="phone" aria-pressed="false">Phone</button>
            </div>
            <div class="plan-design-save">
                <button type="button" class="header-button" data-design-discard hidden>Discard</button>
                <button type="button" class="header-button primary" data-design-save disabled>Save design</button>
            </div>
        </div>
        <div class="plan-design-board">
            <iframe title="Design canvas" sandbox="allow-same-origin" referrerpolicy="no-referrer"></iframe>
        </div>
        <div class="plan-design-controls">
            <div class="plan-design-palette" role="group" aria-label="Add widgets">
                ${Object.keys(WIDGETS).map(type => `<button type="button" class="header-button" data-widget="${type}" draggable="true" aria-label="Add ${PALETTE[type].toLowerCase()}"><span aria-hidden="true">+</span> ${PALETTE[type]}</button>`).join('')}
            </div>
            <label class="plan-design-layer">Layer<select aria-label="Layer"></select></label>
            <form class="plan-design-properties">
                <label>Label<input maxlength="160" aria-label="Widget label" autocomplete="off"></label>
                <button type="submit" class="header-button">Apply label</button>
                <button type="button" class="header-button" data-move="-1" aria-label="Move up" title="Move up">↑</button>
                <button type="button" class="header-button" data-move="1" aria-label="Move down" title="Move down">↓</button>
            </form>
            <div class="plan-design-status" role="status"></div>
        </div>
    </section>`;
    const root = surface.querySelector('.plan-designer');
    const frame = root.querySelector('iframe');
    const layers = root.querySelector('select');
    const field = root.querySelector('input');
    const form = root.querySelector('form');
    const applyLabel = form.querySelector('[type=submit]');
    const save = root.querySelector('[data-design-save]');
    const discard = root.querySelector('[data-design-discard]');
    const status = root.querySelector('[role=status]');
    const palette = [...root.querySelectorAll('[data-widget]')];
    const movers = [...root.querySelectorAll('[data-move]')];
    const selected = () => model.layers.get(selection);
    const dirty = () => draft.source !== draft.baseSource;

    function sync(message = '') {
        const entry = selected();
        const node = entry?.node;
        layers.value = String(selection);
        const siblings = entry?.parent?.children || [];
        const position = siblings.indexOf(node);
        field.value = node ? labelOf(node) : '';
        field.disabled = busy || !model.valid || !entry?.parent || node?.type === 'content';
        applyLabel.disabled = true;
        movers[0].disabled = busy || !model.valid || position <= 0 || node?.type === 'content';
        movers[1].disabled = busy || !model.valid || position < 0 || position >= siblings.length - 1 || node?.type === 'content';
        palette.forEach(button => {
            button.disabled = busy || !model.valid;
            button.draggable = !button.disabled;
        });
        save.disabled = busy || !dirty() || !model.valid;
        discard.hidden = !dirty();
        discard.disabled = busy;
        status.textContent = message || (dirty() ? 'Unsaved changes' : model.valid ? '' : 'Open the page source to fix the stencil before editing.');
        highlight(frame.contentDocument);
    }

    function highlight(doc, reveal = false) {
        doc?.querySelector('.design-selected')?.classList.remove('design-selected');
        const element = doc?.querySelector(`.node[data-line="${selection}"]`);
        if (!element) return;
        element.classList.add('design-selected');
        if (!reveal) return;
        const { top, bottom } = element.getBoundingClientRect();
        const frameHeight = doc.documentElement.clientHeight;
        if (top < 0) doc.scrollingElement.scrollTop += top - 8;
        else if (bottom > frameHeight) doc.scrollingElement.scrollTop += Math.min(top, bottom - frameHeight + 8);
    }

    function render() {
        model = designPage(source(), pageIndex);
        if (!model.layers.has(selection)) selection = model.root?.line ?? 0;
        layers.replaceChildren(...Array.from(model.layers, ([line, { node, depth }]) => {
            const option = document.createElement('option');
            option.value = line;
            option.textContent = '  '.repeat(depth) + (labelOf(node) || PALETTE[node.type] || node.type);
            return option;
        }));
        sync();
        frame.srcdoc = renderWireframe(source(), item.title, model.id, assets).html;
    }

    function change(edit) {
        if (busy || !edit) return;
        if (!designPage(edit.source, pageIndex).valid) {
            sync('That change would make the stencil invalid.');
            return;
        }
        scroll = frame.contentDocument?.scrollingElement?.scrollTop || 0;
        selection = edit.line;
        draft.source = edit.source;
        draft.line = selection;
        onDraft(draft);
        render();
    }

    function choose(line) {
        selection = line;
        draft.line = line;
        sync();
    }

    frame.addEventListener('load', () => {
        const doc = frame.contentDocument;
        if (!doc || doc.URL !== 'about:srcdoc') return;
        const style = doc.createElement('style');
        style.textContent = `.node[data-design-layer]{cursor:grab;user-select:none}
            .node[data-design-layer]:hover{outline:1px dashed #4774d0;outline-offset:2px}
            .node.design-selected{outline:2px solid #315fc0;outline-offset:3px}
            .node.design-target{outline:2px dashed #315fc0;outline-offset:3px}
            .node[data-design-empty]{min-height:40px;outline:1px dashed #8298c9;border-radius:4px}
            .node[data-design-empty]::after{content:"Drop widgets here";padding:8px;color:#405a84;font:12px system-ui}`;
        doc.head.append(style);
        doc.scrollingElement.scrollTop = scroll;
        doc.querySelectorAll('a, button, input, select, textarea').forEach(control => { control.tabIndex = -1; });
        function lineAt(event) {
            const target = event.target.closest?.('[data-design-layer]');
            return Number(target?.dataset.line);
        }
        for (const element of doc.querySelectorAll('.node[data-line]')) {
            const entry = model.layers.get(Number(element.dataset.line));
            if (!entry) continue;
            element.dataset.designLayer = '';
            if (['row', 'stack'].includes(entry.node.type) && !entry.node.children.length) element.dataset.designEmpty = '';
        }
        let pointerStart = null;
        let moving = false;
        let ignoreClick = false;
        const clearTarget = () => doc.querySelector('.design-target')?.classList.remove('design-target');
        function markTarget(event) {
            clearTarget();
            const target = insertionLayout(model, lineAt(event));
            doc.querySelector(`.node[data-line="${target?.line}"]`)?.classList.add('design-target');
        }
        doc.addEventListener('click', event => {
            event.preventDefault();
            if (ignoreClick) { ignoreClick = false; return; }
            const line = lineAt(event);
            if (!busy && line) choose(line);
        });
        doc.addEventListener('pointerdown', event => {
            ignoreClick = false;
            const entry = model.layers.get(lineAt(event));
            if (busy || event.pointerType === 'touch' || event.button !== 0 || !entry?.parent || entry.node.type === 'content') return;
            event.preventDefault();
            pointerStart = { node: entry.node, x: event.clientX, y: event.clientY };
        });
        doc.addEventListener('pointermove', event => {
            if (!pointerStart || !(event.buttons & 1)) return;
            if (Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 6) moving = true;
            if (moving) markTarget(event);
        });
        doc.addEventListener('pointerup', event => {
            const start = pointerStart;
            pointerStart = null;
            clearTarget();
            if (!moving) return;
            moving = false;
            ignoreClick = true;
            const layout = insertionLayout(model, lineAt(event));
            if (start && layout) change(moveWidget(source(), start.node, layout, layout.children.length));
        });
        doc.addEventListener('pointerleave', () => {
            pointerStart = null;
            moving = false;
            clearTarget();
        });
        doc.addEventListener('dragstart', event => event.preventDefault());
        doc.addEventListener('dragover', event => {
            if (busy || !event.dataTransfer.types.includes(WIDGET_DRAG)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
            markTarget(event);
        });
        doc.addEventListener('dragleave', event => {
            if (!event.relatedTarget) clearTarget();
        });
        doc.addEventListener('drop', event => {
            event.preventDefault();
            clearTarget();
            const layout = insertionLayout(model, lineAt(event));
            const widget = event.dataTransfer.getData(WIDGET_DRAG);
            if (widget) change(addWidget(source(), layout, widget));
        });
        highlight(doc, true);
    });

    root.querySelectorAll('[data-width]').forEach(button => {
        button.onclick = () => {
            root.dataset.width = button.dataset.width;
            root.querySelectorAll('[data-width]').forEach(toggle => toggle.setAttribute('aria-pressed', String(toggle === button)));
        };
    });
    palette.forEach(button => {
        button.onclick = () => change(addWidget(source(), insertionLayout(model, selection), button.dataset.widget));
        button.ondragstart = event => {
            event.dataTransfer.setData(WIDGET_DRAG, button.dataset.widget);
            event.dataTransfer.effectAllowed = 'copy';
        };
    });
    layers.onchange = () => {
        choose(Number(layers.value));
        highlight(frame.contentDocument, true);
    };
    field.oninput = () => { applyLabel.disabled = !field.value.trim() || field.value.trim() === labelOf(selected().node); };
    form.onsubmit = event => {
        event.preventDefault();
        if (!applyLabel.disabled) change(editWidgetLabel(source(), selected().node, field.value));
    };
    movers.forEach(button => {
        button.onclick = () => {
            const { node, parent } = selected();
            const index = parent.children.indexOf(node) + (Number(button.dataset.move) < 0 ? -1 : 2);
            change(moveWidget(source(), node, parent, index));
        };
    });
    discard.onclick = async () => {
        busy = true;
        sync('Loading saved design…');
        try { await onDiscard(); }
        catch (error) {
            busy = false;
            sync(error.message || 'Could not load the saved design.');
        }
    };
    save.onclick = async () => {
        busy = true;
        sync('Saving…');
        try { await onSave(source(), model.id); }
        catch (error) {
            busy = false;
            sync(error.status === 409
                ? 'This stencil has a newer revision. Your draft is still open. Discard to load the latest version.'
                : error.message || 'Could not save the design.');
        }
    };
    render();
}
