// Interfaces node shapes, images, notes and links (N6): canvas decoration + the node popover.
// All metadata lives in the item's trailing sidecar line (flow-meta.js); saves go through ctx.save(content),
// which is the existing PATCH /plan-items/{id} with expected_revision (409 / 422 handled here like the inline editor).
import { SHAPES, NOTE_LIMIT, LINK_LABEL_LIMIT, readSidecar, updateNodeMeta, renameNodeId } from './flow-meta.js?v=20261103-ifaces3';
import { createInlineEditor, isRevisionConflict } from './inline-edit/editor.js?v=20261103-ifaces3';

const SHAPE_LABEL = { page: 'Page', rectangle: 'Rectangle', square: 'Square', circle: 'Circle', oval: 'Oval', diamond: 'Diamond (decision)' };
const h = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text != null) el.textContent = text; return el; };

// Small, self-contained shape glyph (SVG) for a node; "page" is the default document glyph.
export function shapeGlyph(shape = 'page') {
    const paths = {
        page: '<path d="M4 2.5h6l3 3v8H4z" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10 2.5v3h3" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        rectangle: '<rect x="1.5" y="4" width="13" height="8" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        square: '<rect x="3" y="3" width="10" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        circle: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        oval: '<ellipse cx="8" cy="8" rx="6.5" ry="4.2" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        diamond: '<path d="M8 1.8 14.2 8 8 14.2 1.8 8z" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    };
    return `<svg class="plan-node-glyph" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">${paths[shape] || paths.page}</svg>`;
}

// Decorate an existing node element (sitemap card / page strip) with its metadata. No metadata: untouched.
export function decorateNode(el, meta, ctx) {
    if (!meta || !Object.keys(meta).length) return;
    el.classList.add('plan-node-meta');
    if (meta.shape) el.dataset.shape = meta.shape;
    const badges = h('span', 'plan-node-badges');
    if (meta.shape) badges.insertAdjacentHTML('beforeend', `<span class="plan-node-badge" title="${SHAPE_LABEL[meta.shape]}">${shapeGlyph(meta.shape)}</span>`);
    if (meta.image) badges.append(thumb(meta.image, ctx, 'plan-node-thumb-mini'));
    if (meta.note) { const n = h('span', 'plan-node-badge plan-node-note-badge', 'Note'); n.title = meta.note; badges.append(n); }
    for (const [target, label] of Object.entries(meta.links || {})) badges.append(linkChip(target, label, ctx));
    el.append(badges);
}

function thumb(id, ctx, cls = 'plan-node-thumb') {
    const asset = ctx.asset(id);
    if (!asset) {
        const missing = h('span', `${cls} plan-node-thumb-missing`, 'Missing image');
        missing.setAttribute('role', 'img');
        missing.setAttribute('aria-label', `Image ${id} is missing`);
        missing.title = `Image ${id} is no longer attached to this project.`;
        return missing;
    }
    const button = h('button', `${cls} plan-node-thumb-button`);
    button.type = 'button';
    button.setAttribute('aria-label', `Enlarge image ${asset.name || id}`);
    const img = document.createElement('img');
    img.alt = ''; img.decoding = 'async'; img.src = ctx.assetUrl(id);
    button.append(img);
    button.addEventListener('click', (event) => { event.stopPropagation(); ctx.viewAsset(id); });
    return button;
}

function linkChip(target, label, ctx) {
    const known = ctx.targets().find((t) => t.id === target);
    const chip = h('button', `plan-node-link${known ? '' : ' plan-node-link-dangling'}`);
    chip.type = 'button';
    chip.textContent = `${label ? `${label} → ` : '→ '}${known ? known.label : target}`;
    if (known) {
        chip.setAttribute('aria-label', `Open linked page ${known.label}${label ? ` (${label})` : ''}`);
        chip.addEventListener('click', (event) => { event.stopPropagation(); ctx.follow(target); });
    } else {
        chip.disabled = true;
        chip.title = `"${target}" is not a page in this plan any more.`;
        chip.insertAdjacentHTML('afterbegin', '<span class="plan-node-warn" aria-hidden="true">!</span>');
        chip.setAttribute('aria-label', `Link target ${target} is missing`);
    }
    return chip;
}

// "Node" opener button used on the page toolbar, the Mermaid dock and sitemap cards.
export function nodeButton(label, onOpen, cls = '') {
    const button = h('button', `plan-node-open ${cls}`.trim());
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', `Node details: ${label}`);
    button.innerHTML = `${shapeGlyph('page')}<span>Node</span>`;
    button.addEventListener('click', (event) => { event.stopPropagation(); onOpen(button); });
    return button;
}

let openPopover = null;
export const closeNodePopover = () => openPopover?.close();

// Popover for one node. node = { key, screenLine, label }. ctx = { item(), format, save, upload, asset, assetUrl,
// viewAsset, targets, follow, reloadLatest, toast }.
export function openNodePopover(opener, node, ctx) {
    closeNodePopover();
    const pop = h('div', 'plan-node-popover');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'false');
    pop.setAttribute('aria-label', `Node: ${node.label}`);
    let editor = null;
    let current = { ...node };
    const meta = () => (current.key ? readSidecar(ctx.item().content, ctx.format).nodes[current.key] || {} : {});

    const close = () => {
        editor?.destroy();
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onOutside, true);
        pop.remove();
        if (openPopover?.pop === pop) openPopover = null;
        if (opener?.isConnected) opener.focus();
    };
    const onKey = (event) => {
        if (event.key === 'Escape') { event.preventDefault(); close(); return; }
        if (event.key !== 'Tab') return;
        const focusable = [...pop.querySelectorAll('button:not(:disabled), input:not(:disabled), select, textarea, [contenteditable="true"], [tabindex="0"]')].filter((e) => e.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    const onOutside = (event) => { if (!pop.contains(event.target) && !opener?.contains(event.target) && !event.target.closest?.('.md-toolbar')) close(); };

    const status = h('p', 'plan-node-status');
    status.setAttribute('role', 'status');
    const alert = h('div', 'plan-node-error');
    alert.setAttribute('role', 'alert');
    alert.hidden = true;
    const fail = (error, mine) => {
        alert.hidden = false;
        alert.textContent = '';
        if (error?.status === 409 || isRevisionConflict(error)) {
            alert.append(h('span', '', 'This page has a newer revision. Your change was not saved.'));
            const reload = h('button', 'header-button plan-node-reload', 'Reload latest (your version is copied)');
            reload.type = 'button';
            reload.addEventListener('click', () => { close(); ctx.reloadLatest(mine); });
            alert.append(reload);
            return;
        }
        alert.textContent = `Not saved: ${error?.message || 'unknown error'}`;
    };
    // Apply one patch: fresh content, auto id, orphan prune, PATCH with expected_revision.
    async function apply(patch, what) {
        alert.hidden = true;
        let next;
        try { next = updateNodeMeta(ctx.item().content, ctx.format, { key: current.key, screenLine: current.screenLine, patch }); }
        catch (error) { fail(error); throw error; }
        status.textContent = 'Saving…';
        try {
            await ctx.save(next.content);
        } catch (error) { status.textContent = ''; fail(error, next.content); throw error; }
        current = { ...current, key: next.id };
        status.textContent = `${what} saved${next.pruned.length ? ` · removed metadata for ${next.pruned.length} deleted node${next.pruned.length === 1 ? '' : 's'}` : ''}.`;
        render();
        return next;
    }

    function render() {
        const m = meta();
        editor?.destroy();
        pop.textContent = '';
        const head = h('div', 'plan-node-head');
        head.insertAdjacentHTML('beforeend', shapeGlyph(m.shape || 'page'));
        head.append(h('h4', '', node.label));
        if (current.key) head.append(h('code', 'plan-node-key', current.key));
        const x = h('button', 'plan-node-close');
        x.type = 'button'; x.setAttribute('aria-label', 'Close'); x.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
        x.addEventListener('click', close);
        head.append(x);
        pop.append(head);

        if (ctx.format === 'wire' && current.key) {
            // Rename id: the screen's id= and its metadata key change together (one revision).
            const row = h('div', 'plan-node-rename');
            const input = document.createElement('input');
            input.type = 'text'; input.value = current.key; input.maxLength = 64; input.spellcheck = false;
            input.setAttribute('aria-label', 'Page id');
            const go = h('button', 'header-button plan-node-rename-save', 'Rename id');
            go.type = 'button';
            const run = async () => {
                const to = input.value.trim();
                if (!to || to === current.key) return;
                alert.hidden = true;
                let next;
                try { next = renameNodeId(ctx.item().content, 'wire', current.key, to); } catch (error) { fail(error); return; }
                status.textContent = 'Saving…';
                try { await ctx.save(next.content); } catch (error) { status.textContent = ''; fail(error, next.content); return; }
                current = { ...current, key: next.id };
                render();
                status.textContent = `Id renamed to ${next.id}.`;
                pop.querySelector('.plan-node-rename input')?.focus();
            };
            go.addEventListener('click', run);
            input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); run(); } });
            row.append(h('span', 'plan-node-rename-label', 'Id'), input, go);
            pop.append(row);
        }

        if (ctx.format === 'wire') {
            const set = h('fieldset', 'plan-node-shapes');
            set.append(h('legend', '', 'Shape'));
            for (const shape of SHAPES) {
                const label = h('label', 'plan-node-shape');
                const input = document.createElement('input');
                input.type = 'radio'; input.name = `plan-node-shape-${Math.random().toString(36).slice(2)}`; input.value = shape;
                input.checked = (m.shape || 'page') === shape;
                input.addEventListener('change', () => apply({ shape: shape === 'page' ? null : shape }, 'Shape').then(() => pop.querySelector(`input[value="${shape}"]`)?.focus(), () => {}));
                label.append(input);
                label.insertAdjacentHTML('beforeend', `${shapeGlyph(shape)}<span>${SHAPE_LABEL[shape]}</span>`);
                set.append(label);
            }
            pop.append(set);
        } else pop.append(h('p', 'plan-node-hint', 'Shapes for Mermaid flows are set in the diagram itself.'));

        // Note: shared inline editor (plain text), 500-character cap.
        const noteBox = h('section', 'plan-node-section plan-node-note');
        noteBox.append(h('h5', '', 'Note'));
        const count = h('small', 'plan-node-count', `${(m.note || '').length} / ${NOTE_LIMIT}`);
        editor = createInlineEditor({
            value: m.note || '', format: 'text', label: `Note for ${node.label}`, toast: ctx.toast,
            save: async (text) => {
                const note = String(text || '').replace(/\n+$/, '');
                if (note.length > NOTE_LIMIT) throw new Error(`A node note can be at most ${NOTE_LIMIT} characters (now ${note.length}).`);
                return apply({ note: note || null }, 'Note');
            },
        });
        editor.el.addEventListener('input', () => { const n = editor.value.replace(/\n+$/, '').length; count.textContent = `${n} / ${NOTE_LIMIT}`; count.dataset.over = String(n > NOTE_LIMIT); });
        noteBox.append(editor.el, count);
        pop.append(noteBox);

        // Image: existing attachment upload; the sidecar keeps only the asset id.
        const imageBox = h('section', 'plan-node-section plan-node-image');
        imageBox.append(h('h5', '', 'Image'));
        if (m.image) imageBox.append(thumb(m.image, ctx));
        const file = document.createElement('input');
        file.type = 'file'; file.accept = 'image/png,image/jpeg,image/webp'; file.className = 'plan-node-file'; file.setAttribute('aria-label', `Upload an image for ${node.label}`);
        file.addEventListener('change', async () => {
            const chosen = file.files?.[0];
            if (!chosen) return;
            status.textContent = 'Uploading…';
            let asset;
            try { asset = await ctx.upload(chosen); } catch (error) { status.textContent = ''; fail(error); return; }
            apply({ image: String(asset.id) }, 'Image').catch(() => {});
        });
        const pick = h('button', 'header-button plan-node-image-pick', m.image ? 'Replace image' : 'Add image');
        pick.type = 'button'; pick.addEventListener('click', () => file.click());
        imageBox.append(file, pick);
        if (m.image) { const rm = h('button', 'header-button plan-node-image-remove', 'Remove image'); rm.type = 'button'; rm.addEventListener('click', () => apply({ image: null }, 'Image').catch(() => {})); imageBox.append(rm); }
        pop.append(imageBox);

        // Links: label + target page; click-through; dangling targets flagged.
        const linkBox = h('section', 'plan-node-section plan-node-links');
        linkBox.append(h('h5', '', 'Links'));
        const list = h('ul', 'plan-node-link-list');
        for (const [target, label] of Object.entries(m.links || {})) {
            const li = h('li');
            li.append(linkChip(target, label, { ...ctx, follow: (id) => { close(); ctx.follow(id); } }));
            const rm = h('button', 'plan-node-link-remove', 'Remove');
            rm.type = 'button'; rm.setAttribute('aria-label', `Remove link to ${target}`);
            rm.addEventListener('click', () => { const links = { ...(meta().links || {}) }; delete links[target]; apply({ links: Object.keys(links).length ? links : null }, 'Link').catch(() => {}); });
            li.append(rm);
            list.append(li);
        }
        if (list.childNodes.length) linkBox.append(list);
        const targets = ctx.targets().filter((t) => t.id !== current.key);
        if (targets.length) {
            const row = h('div', 'plan-node-link-add');
            const select = document.createElement('select');
            select.setAttribute('aria-label', 'Link target page');
            select.innerHTML = targets.map((t) => `<option value="${t.id.replace(/"/g, '&quot;')}"></option>`).join('');
            [...select.options].forEach((o, i) => { o.textContent = targets[i].label; });
            const label = document.createElement('input');
            label.type = 'text'; label.maxLength = LINK_LABEL_LIMIT; label.placeholder = 'Label (optional)'; label.setAttribute('aria-label', 'Link label');
            const add = h('button', 'header-button plan-node-link-save', 'Add link');
            add.type = 'button';
            add.addEventListener('click', () => apply({ links: { ...(meta().links || {}), [select.value]: label.value.trim() } }, 'Link').catch(() => {}));
            row.append(select, label, add);
            linkBox.append(row);
        } else linkBox.append(h('p', 'plan-node-hint', 'No other pages to link to yet.'));
        pop.append(linkBox, alert, status);
    }

    render();
    document.body.append(pop);
    place(pop, opener);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onOutside, true);
    openPopover = { pop, close };
    (pop.querySelector('input[type="radio"]:checked') || pop.querySelector('.plan-inline-text') || pop.querySelector('button'))?.focus();
    return openPopover;
}

function place(pop, opener) {
    if (window.matchMedia('(max-width: 600px)').matches) { pop.classList.add('plan-node-sheet'); return; }   // bottom sheet
    const r = opener?.getBoundingClientRect?.() || { left: 16, bottom: 16, right: 16 };
    const width = Math.min(360, window.innerWidth - 24);
    pop.style.width = `${width}px`;
    pop.style.left = `${Math.max(12, Math.min(r.left, window.innerWidth - width - 12))}px`;
    const below = window.innerHeight - r.bottom - 18;
    const above = (r.top ?? r.bottom) - 18;
    if (below < 320 && above > below) {
        // Opener near the bottom (e.g. the Interfaces dock): open upwards, anchored to the opener's top edge.
        pop.style.top = 'auto';
        pop.style.bottom = `${Math.round(window.innerHeight - (r.top ?? r.bottom) + 6)}px`;
        pop.style.maxHeight = `${Math.max(160, Math.round(above))}px`;
        return;
    }
    const top = r.bottom + 6;
    pop.style.bottom = '';
    pop.style.top = `${Math.max(12, Math.min(top, window.innerHeight - 120))}px`;
    pop.style.maxHeight = `${Math.max(200, window.innerHeight - Math.max(12, Math.min(top, window.innerHeight - 120)) - 12)}px`;
}

// Non-blocking notice for update_item's validation_warnings.
export function warningsNotice(warnings, onDismiss) {
    const box = h('div', 'plan-flow-warnings');
    box.setAttribute('role', 'status');
    box.append(h('strong', '', 'Saved with notes'));
    const ul = h('ul');
    for (const w of warnings.slice(0, 5)) ul.append(h('li', '', w));
    box.append(ul);
    const x = h('button', 'plan-flow-warnings-close', 'Dismiss');
    x.type = 'button'; x.addEventListener('click', () => { box.remove(); onDismiss?.(); });
    box.append(x);
    return box;
}

// Mermaid: choose which node to describe (the diagram itself stays native and read-only).
export function openNodePicker(opener, nodes, onPick) {
    closeNodePopover();
    const pop = h('div', 'plan-node-popover plan-node-picker');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', 'Choose a node');
    pop.append(h('h4', '', 'Choose a node'));
    const list = h('div', 'plan-node-picker-list');
    for (const node of nodes) {
        const b = h('button', 'plan-node-pick');
        b.type = 'button';
        b.innerHTML = `${shapeGlyph('page')}<span></span>${node.meta && Object.keys(node.meta).length ? '<small>has notes</small>' : ''}`;
        b.querySelector('span').textContent = node.label;
        b.addEventListener('click', () => { close(); onPick(node, opener); });
        list.append(b);
    }
    if (!nodes.length) list.append(h('p', 'plan-node-hint', 'This diagram has no nodes yet.'));
    pop.append(list);
    const onKey = (event) => {
        if (event.key === 'Escape') { event.preventDefault(); close(); }
        if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
            const items = [...pop.querySelectorAll('.plan-node-pick')];
            const at = items.indexOf(document.activeElement);
            if (items.length) { event.preventDefault(); items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus(); }
        }
    };
    const onOutside = (event) => { if (!pop.contains(event.target) && !opener?.contains(event.target)) close(); };
    function close() {
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onOutside, true);
        pop.remove();
        if (openPopover?.pop === pop) openPopover = null;
    }
    document.body.append(pop);
    place(pop, opener);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onOutside, true);
    openPopover = { pop, close: () => { close(); opener?.focus(); } };
    pop.querySelector('button')?.focus();
}
