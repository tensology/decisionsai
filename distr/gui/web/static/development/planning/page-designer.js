import { renderWireframe } from './wireframe.js?v=20261125-canvas-workspace';
import { addWidget, designPage, editWidgetLabel, editWidgetLayout, insertionLayout, moveWidget, removeWidget, WIDGETS } from './page-designer-source.js?v=20261125-canvas-workspace';
import { openSourceDrawer } from './canvas-chrome.js?v=20261125-canvas-workspace';

const PALETTE = { row: 'Row', stack: 'Stack', heading: 'Heading', text: 'Text', button: 'Button', input: 'Field' };
const WIDGET_DRAG = 'application/x-decisions-widget';
const labelOf = node => node.label || node.attrs.label || '';
const SPACING = { width: [80, 1600], height: [0, 20000], gap: [0, 80], padding: [0, 80] };
const CONTAINERS = new Set(['screen', 'content', 'main', 'section', 'row', 'stack', 'card', 'form', 'grid', 'header', 'footer', 'hero']);
const GLYPHS = { row: '▥', stack: '▤', heading: 'H', text: 'T', button: '⊡', input: '▭' };

export function mountPageDesigner(surface, { item, pageIndex, draft, assets, onDraft, onSave, onDiscard }) {
    const source = () => draft.source;
    let selection = draft.line;
    let model;
    let busy = false;
    let scroll = 0;
    surface.innerHTML = `<section class="plan-designer" aria-label="Page designer">
        <div class="plan-design-top">
            <div class="plan-design-template" hidden>
                <span>Inherits <strong data-template-name></strong></span>
                <button type="button" class="header-button" data-template-edit>Edit base template</button>
            </div>
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
        <div class="plan-design-workspace">
            <aside class="plan-design-layer" aria-label="Layers">
                <h3>Layers</h3>
                <div class="plan-design-tree" role="tree" aria-label="Page layers"></div>
            </aside>
            <div class="plan-design-board">
                <div class="plan-design-artboard-name"></div>
                <iframe title="Design canvas" sandbox="allow-same-origin allow-scripts" referrerpolicy="no-referrer"></iframe>
                <div class="plan-design-palette" role="toolbar" aria-label="Add widgets">
                    ${Object.keys(WIDGETS).map(type => `<button type="button" class="header-button" data-widget="${type}" draggable="true" aria-label="Add ${PALETTE[type].toLowerCase()}" title="Add ${PALETTE[type].toLowerCase()}"><strong aria-hidden="true">${GLYPHS[type]}</strong><span>${PALETTE[type]}</span></button>`).join('')}
                </div>
            </div>
            <aside class="plan-design-controls" aria-label="Layer inspector">
                <h3>Properties</h3>
                <div class="plan-design-selection"><strong></strong><small></small></div>
                <form class="plan-design-properties">
                    <label>Label<input maxlength="160" aria-label="Widget label" autocomplete="off"></label>
                    <button type="submit" class="header-button">Apply label</button>
                </form>
                <form class="plan-design-layout">
                    <h4>Layout</h4>
                    <div class="plan-design-dimensions">
                        ${Object.entries(SPACING).map(([key, [min, max]]) => `<label>${key}<input name="${key}" type="number" min="${min}" max="${max}" step="any" placeholder="Auto"></label>`).join('')}
                    </div>
                    <button type="submit" class="header-button" disabled>Apply layout</button>
                </form>
                <div class="plan-design-arrange" role="group" aria-label="Arrange layer">
                    <button type="button" class="header-button" data-move="-1" aria-label="Move up" title="Move up">↑</button>
                    <button type="button" class="header-button" data-move="1" aria-label="Move down" title="Move down">↓</button>
                    <button type="button" class="header-button" data-design-delete aria-label="Delete widget" title="Delete widget (Delete)">Delete</button>
                </div>
                <div class="plan-design-status" role="status"></div>
            </aside>
        </div>
    </section>`;
    const root = surface.querySelector('.plan-designer');
    const frame = root.querySelector('iframe');
    const inheritance = root.querySelector('.plan-design-template');
    const templateName = inheritance.querySelector('[data-template-name]');
    const editTemplate = inheritance.querySelector('button');
    const layers = root.querySelector('[role=tree]');
    const field = root.querySelector('input');
    const form = root.querySelector('form');
    const applyLabel = form.querySelector('[type=submit]');
    const layout = root.querySelector('.plan-design-layout');
    const layoutFields = [...layout.querySelectorAll('input')];
    const applyLayout = layout.querySelector('button');
    const selectionName = root.querySelector('.plan-design-selection strong');
    const selectionType = root.querySelector('.plan-design-selection small');
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
        layers.querySelectorAll('[role=treeitem]').forEach(row => {
            const active = Number(row.dataset.line) === selection;
            row.setAttribute('aria-selected', String(active));
            row.tabIndex = active ? 0 : -1;
        });
        selectionName.textContent = node ? labelOf(node) || node.type : 'No layer selected';
        selectionType.textContent = node?.type || '';
        layout.hidden = !entry?.parent;
        for (const input of layoutFields) {
            input.value = node?.attrs[input.name] ?? '';
            input.disabled = busy || !model.valid || !entry?.parent || (input.name === 'gap' && !CONTAINERS.has(node.type));
            input.parentElement.hidden = input.name === 'gap' && input.disabled;
        }
        applyLayout.disabled = true;
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
        editTemplate.disabled = busy;
        status.textContent = message || (dirty() ? 'Unsaved changes' : model.valid ? '' : 'Open the page source to fix the stencil before editing.');
        highlight(frame.contentDocument);
    }

    function highlight(doc, reveal = false) {
        doc?.querySelector('.design-selected')?.classList.remove('design-selected');
        doc?.querySelector('.design-selection-controls')?.remove();
        const element = doc?.querySelector(`.node[data-line="${selection}"]`);
        if (!element) return;
        element.classList.add('design-selected');
        if (!busy && model.valid && selected()?.parent) {
            const handles = doc.createElement('span');
            handles.className = 'design-selection-controls';
            handles.setAttribute('aria-hidden', 'true');
            handles.innerHTML = '<i></i><i></i><i></i><i data-design-resize></i>';
            element.append(handles);
        }
        if (!reveal) return;
        const { top, bottom } = element.getBoundingClientRect();
        const frameHeight = doc.documentElement.clientHeight;
        if (top < 0) doc.scrollingElement.scrollTop += top - 8;
        else if (bottom > frameHeight) doc.scrollingElement.scrollTop += Math.min(top, bottom - frameHeight + 8);
    }

    function render() {
        const restoreLayerFocus = layers.contains(document.activeElement);
        model = designPage(source(), pageIndex);
        inheritance.hidden = !model.template;
        templateName.textContent = model.template?.label || '';
        if (!model.layers.has(selection)) selection = model.root?.line ?? 0;
        layers.replaceChildren(...Array.from(model.layers, ([line, { node, depth }]) => {
            const option = document.createElement('button');
            option.type = 'button';
            option.setAttribute('role', 'treeitem');
            option.setAttribute('aria-level', String(depth + 1));
            option.dataset.line = line;
            option.style.setProperty('--layer-depth', Math.min(depth, 7));
            const title = labelOf(node) || PALETTE[node.type] || node.type;
            option.title = title;
            const glyph = document.createElement('i');
            glyph.setAttribute('aria-hidden', 'true');
            glyph.textContent = GLYPHS[node.type] || (CONTAINERS.has(node.type) ? '□' : '·');
            const name = document.createElement('span');
            name.textContent = title;
            option.append(glyph, name);
            return option;
        }));
        root.querySelector('.plan-design-artboard-name').textContent = labelOf(model.layers.values().next().value?.node || { attrs: {} }) || item.title;
        sync();
        if (restoreLayerFocus) layers.querySelector('[aria-selected="true"]')?.focus({ preventScroll: true });
        // WebKit needs scripting enabled for the host's artboard event handlers.
        // This renderer emits validated markup with script-src 'none' in Design.
        frame.srcdoc = renderWireframe(source(), item.title, model.id, assets).html;
    }

    editTemplate.onclick = () => {
        if (!model.template) return;
        const stencil = source();
        const { drawer } = openSourceDrawer({
            title: `Edit base template · ${model.template.label}`, text: stencil, opener: editTemplate,
            onApply: (nextSource) => {
                const next = designPage(nextSource, pageIndex);
                if (!next.valid || next.id !== model.id) throw new Error('Keep this page in the stencil and fix any invalid DSL before applying.');
                change({ source: nextSource, line: next.root.line });
            },
        });
        const box = drawer.querySelector('textarea');
        const before = box.value.split('\n').slice(0, model.template.line - 1).join('\n');
        const start = before ? before.length + 1 : 0;
        box.focus();
        box.setSelectionRange(start, box.value.indexOf('\n', start) < 0 ? box.value.length : box.value.indexOf('\n', start));
    };

    function change(edit) {
        if (busy || !edit || edit.source === source()) return;
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
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target?.tagName || '');
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
        style.textContent = `body,.sheet{background:#fff;color:#222630}
            .sheet{padding:clamp(12px,3.3vw,28px)!important}
            .canvas{border:0;border-radius:0;box-shadow:none;overflow:visible;background:transparent}
            .node:not(.plain),.form,.hero{background:#f6f7f9!important;border-radius:3px;box-shadow:none;outline:1px solid #dde0e6}
            .node[data-design-layer]:is(.section,.row,.stack,.grid,.card,.form){border-radius:2px;outline:1px dashed #bac1cc;outline-offset:3px}
            .node :is(input,textarea,select,button,.button-link){border:1px solid #969ea9;border-radius:2px;background:#fff;color:#25272b}
            .button[data-wf-variant=primary] :is(button,.button-link),.button[data-wf-primary=true] button{background:#e7ecf5;color:#1f3053;border-color:#9aaac1}
            .node img{filter:grayscale(1)}
            .node[data-design-layer]{cursor:grab;user-select:none}
            .node[data-design-layer]:hover{outline:1px dashed #4774d0;outline-offset:2px}
            .node.design-selected,.node[data-design-layer].design-selected{outline:1px solid #2866e9;outline-offset:2px}
            .design-selection-controls{position:absolute;inset:-3px;z-index:9;pointer-events:none}
            .design-selection-controls i{position:absolute;width:8px;height:8px;border:1px solid #2866e9;background:white}
            .design-selection-controls i:nth-child(1){top:-4px;left:-4px}
            .design-selection-controls i:nth-child(2){top:-4px;right:-4px}
            .design-selection-controls i:nth-child(3){bottom:-4px;left:-4px}
            .design-selection-controls i:nth-child(4){bottom:-4px;right:-4px;pointer-events:auto;cursor:ew-resize;touch-action:none}
            .node[data-design-layer].design-target{outline:2px dashed #315fc0;outline-offset:3px}
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
        let resize = null;
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
            const handle = event.target.closest?.('[data-design-resize]');
            if (handle && !busy && event.button === 0 && entry?.parent) {
                event.preventDefault();
                handle.setPointerCapture(event.pointerId);
                const element = handle.closest('.node');
                resize = { node: entry.node, x: event.clientX, width: element.getBoundingClientRect().width, handle };
                return;
            }
            if (busy || event.pointerType === 'touch' || event.button !== 0 || !entry?.parent || entry.node.type === 'content') return;
            event.preventDefault();
            pointerStart = { node: entry.node, x: event.clientX, y: event.clientY };
        });
        doc.addEventListener('pointermove', event => {
            if (resize) {
                resize.handle.parentElement.style.right = `${resize.x - event.clientX}px`;
                return;
            }
            if (!pointerStart || !(event.buttons & 1)) return;
            if (Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 6) moving = true;
            if (moving) markTarget(event);
        });
        doc.addEventListener('pointerup', event => {
            if (resize) {
                const { node, x, width, handle } = resize;
                resize = null;
                handle.parentElement.style.right = '';
                ignoreClick = true;
                if (Math.abs(event.clientX - x) >= 3) {
                    const [min, max] = SPACING.width;
                    change(editWidgetLayout(source(), node, { width: String(Math.round(Math.max(min, Math.min(max, width + event.clientX - x)))) }));
                }
                return;
            }
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
        doc.addEventListener('pointercancel', () => {
            if (resize) resize.handle.parentElement.style.right = '';
            resize = null;
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
    layers.onclick = event => {
        const row = event.target.closest('[role=treeitem]');
        if (!row) return;
        choose(Number(row.dataset.line));
        highlight(frame.contentDocument, true);
    };
    layers.onkeydown = event => {
        const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const rows = [...layers.children];
        const next = rows[rows.indexOf(event.target) + step];
        if (next) { next.click(); next.focus(); }
    };
    const layoutValues = () => Object.fromEntries(layoutFields.filter(input => !input.disabled).map(input => [input.name, input.value]));
    layout.oninput = () => {
        applyLayout.disabled = !layout.checkValidity() || Object.entries(layoutValues()).every(([key, value]) => value === (selected().node.attrs[key] ?? ''));
    };
    layout.onsubmit = event => {
        event.preventDefault();
        if (!applyLayout.disabled) change(editWidgetLayout(source(), selected().node, layoutValues()));
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
