import { renderWireframe } from './wireframe.js?v=20261119-page-designer';
import { addWidget, designPage, editWidgetLabel, insertionLayout, moveWidget, removeWidget, WIDGETS } from './page-designer-source.js?v=20261120-wfplan';

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
                <button type="button" class="header-button" data-design-undo aria-label="Undo" title="Undo (Ctrl+Z)" disabled>Undo</button>
                <button type="button" class="header-button" data-design-redo aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled>Redo</button>
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
                <button type="button" class="header-button" data-design-delete aria-label="Delete widget" title="Delete widget (Delete)">Delete</button>
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
    const undo = root.querySelector('[data-design-undo]');
    const redo = root.querySelector('[data-design-redo]');
    const remove = root.querySelector('[data-design-delete]');
    draft.undo ||= [];
    draft.redo ||= [];
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
        undo.disabled = busy || !draft.undo.length;
        redo.disabled = busy || !draft.redo.length;
        remove.disabled = busy || !model.valid || !entry?.parent || ['screen', 'content'].includes(node?.type);
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
        draft.undo.push({ source: draft.source, line: selection });
        if (draft.undo.length > 100) draft.undo.shift();
        draft.redo.length = 0;
        apply(edit);
    }

    function apply(edit) {
        scroll = frame.contentDocument?.scrollingElement?.scrollTop || 0;
        selection = edit.line;
        draft.source = edit.source;
        draft.line = selection;
        onDraft(draft);
        render();
    }

    function step(from, to) {
        if (busy || !from.length) return;
        to.push({ source: draft.source, line: selection });
        apply(from.pop());
    }
    const removeSelected = () => {
        const entry = selected();
        if (!remove.disabled && entry) change(removeWidget(source(), entry.node, entry.parent));
    };
    // Keyboard: Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl+Y redo, Delete/Backspace removes the
    // selected widget unless the person is typing in a field.
    function shortcut(event) {
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target?.tagName || '') && event.target !== layers;
        const mod = event.metaKey || event.ctrlKey;
        const key = event.key.toLowerCase();
        if (mod && key === 'z' && !typing) { event.preventDefault(); step(event.shiftKey ? draft.redo : draft.undo, event.shiftKey ? draft.undo : draft.redo); }
        else if (mod && key === 'y' && !typing) { event.preventDefault(); step(draft.redo, draft.undo); }
        else if ((event.key === 'Delete' || event.key === 'Backspace') && !typing && !mod) { event.preventDefault(); removeSelected(); }
    }
    root.addEventListener('keydown', shortcut);

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
            .node[data-design-empty]::after{content:"Drop widgets here";padding:8px;color:#405a84;font:12px system-ui}
            .node.design-insert-before{box-shadow:0 -3px 0 #315fc0}
            .node.design-insert-after{box-shadow:0 3px 0 #315fc0}
            .row > .node.design-insert-before, .node.design-row-child.design-insert-before{box-shadow:-3px 0 0 #315fc0}
            .row > .node.design-insert-after, .node.design-row-child.design-insert-after{box-shadow:3px 0 0 #315fc0}`;
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
        const clearTarget = () => doc.querySelectorAll('.design-target, .design-insert-before, .design-insert-after')
            .forEach(node => node.classList.remove('design-target', 'design-insert-before', 'design-insert-after', 'design-row-child'));
        // Where a drop lands: the layout under the pointer and the child index the pointer is before,
        // so widgets go where they are dropped instead of always at the end.
        function dropPoint(event) {
            const layout = insertionLayout(model, lineAt(event));
            if (!layout) return { layout: null, index: 0 };
            const horizontal = layout.type === 'row';
            const children = layout.children.filter(child => child.type !== 'content');
            let index = layout.children.length;
            for (const child of children) {
                const box = doc.querySelector(`.node[data-line="${child.line}"]`)?.getBoundingClientRect();
                if (!box) continue;
                const middle = horizontal ? box.left + box.width / 2 : box.top + box.height / 2;
                if ((horizontal ? event.clientX : event.clientY) < middle) { index = layout.children.indexOf(child); break; }
            }
            return { layout, index, horizontal };
        }
        function markTarget(event) {
            clearTarget();
            const { layout, index, horizontal } = dropPoint(event);
            doc.querySelector(`.node[data-line="${layout?.line}"]`)?.classList.add('design-target');
            const before = layout?.children[index];
            const marker = before || layout?.children.at(-1);
            const element = marker && doc.querySelector(`.node[data-line="${marker.line}"]`);
            if (!element) return;
            element.classList.add(before ? 'design-insert-before' : 'design-insert-after');
            if (horizontal) element.classList.add('design-row-child');
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
            const { layout, index } = dropPoint(event);
            if (start && layout) change(moveWidget(source(), start.node, layout, index));
        });
        doc.addEventListener('pointerleave', () => {
            pointerStart = null;
            moving = false;
            clearTarget();
        });
        doc.addEventListener('keydown', shortcut);
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
            const { layout, index } = dropPoint(event);
            const widget = event.dataTransfer.getData(WIDGET_DRAG);
            if (widget) change(addWidget(source(), layout, widget, index));
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
    undo.onclick = () => step(draft.undo, draft.redo);
    redo.onclick = () => step(draft.redo, draft.undo);
    remove.onclick = removeSelected;
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
