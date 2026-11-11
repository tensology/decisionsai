// Shared canvas chrome for the plan tabs (Data ERD canvas, Interfaces surface, other diagrams):
// fill-to-window sizing, a dismissable top-right call-out, and the read-only "Advanced" source drawer.

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Size `node` to the window space below its top edge (header, tabs, chat panel are whatever sits above or
// beside it); keeps doing so on window / container resize. Returns a cleanup function.
// Bottom edge of the space a plan canvas may use: the window, or the artifacts pane when the chat panel
// is stacked below it (narrow layouts).
export function availableBottom(node) {
    const pane = node.closest('.plan-artifacts');
    const preview = node.closest('.plan-preview');
    const paneBottom = preview ? preview.getBoundingClientRect().bottom : pane ? pane.getBoundingClientRect().bottom : Infinity;
    return Math.min(window.innerHeight || 800, paneBottom);
}

export function fillToWindow(node, { min = 320, bottom = 16, onResize = () => {} } = {}) {
    let frame = 0;
    const apply = () => {
        if (!node.isConnected) return;
        const top = Math.max(0, node.getBoundingClientRect().top);
        const height = Math.max(min, Math.round(availableBottom(node) - top - bottom));
        if (node.style.height !== `${height}px`) { node.style.height = `${height}px`; onResize(height); }
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(apply); };
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
    observer?.observe(node.parentElement || node);
    window.addEventListener('resize', schedule);
    apply();
    return () => { observer?.disconnect(); window.removeEventListener('resize', schedule); cancelAnimationFrame(frame); };
}

// Bottom-left dock for a surface whose CONTENT scrolls or is replaced (Interfaces pages): the dock lives
// outside the surface (so innerHTML swaps never wipe it) and is pinned with position:fixed to the surface's
// bottom-left corner, same 10px inset as the Data canvas dock. Returns a cleanup function.
export function pinDock(dock, surface, { inset: fixedInset = null } = {}) {
    dock.classList.add('plan-pinned-dock');
    let frame = 0;
    const apply = () => {
        if (!surface.isConnected || !dock.isConnected) return;
        const r = surface.getBoundingClientRect();
        const inset = fixedInset ?? (r.width <= 460 ? 8 : 10);   // same as the Data dock's @container (max-width: 460px) rule
        const visibleBottom = Math.min(r.bottom, window.innerHeight || r.bottom);
        dock.hidden = r.width === 0 || visibleBottom - r.top < dock.offsetHeight + inset * 2;
        dock.style.left = `${Math.round(r.left + inset)}px`;
        dock.style.top = `${Math.round(visibleBottom - inset - dock.offsetHeight)}px`;
        dock.style.maxWidth = `${Math.max(0, Math.round(r.width - inset * 2))}px`;
        // Scrolled page content must be able to clear the floating controls.
        surface.style.setProperty('--plan-dock-clearance', `${dock.hidden ? 0 : dock.offsetHeight + inset * 2}px`);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(apply); };
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
    observer?.observe(surface);
    observer?.observe(dock);
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    apply();
    return () => { observer?.disconnect(); window.removeEventListener('resize', schedule); window.removeEventListener('scroll', schedule, true); cancelAnimationFrame(frame); surface.style.removeProperty('--plan-dock-clearance'); dock.remove(); };
}

const closeIcon = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

// Top-right call-out (stats + hint) with a close icon; dismissal lasts for the browser session.
export function createCallout({ key, stats = '', hint = '', label = 'Summary' }) {
    const storageKey = `decisions.canvas.callout.${key}`;
    let dismissed = false;
    try { dismissed = sessionStorage.getItem(storageKey) === '1'; } catch { dismissed = false; }
    const box = document.createElement('section');
    box.className = 'erd-callout plan-canvas-callout';
    box.setAttribute('aria-label', label);
    box.hidden = dismissed;
    box.innerHTML = `<button type="button" class="erd-callout-close" aria-label="Dismiss ${esc(label.toLowerCase())}" title="Dismiss">${closeIcon}</button>
        <p class="erd-callout-stats">${esc(stats)}</p>${hint ? `<p class="erd-callout-hint">${esc(hint)}</p>` : ''}`;
    box.querySelector('button').addEventListener('click', () => {
        try { sessionStorage.setItem(storageKey, '1'); } catch { /* private mode */ }
        box.hidden = true;
    });
    return box;
}

// "Advanced · source (read-only)" opener: a compact dock button that opens the source drawer.
// With onApply(text) the drawer shows an editable source box with Apply / Cancel (revision-checked by the
// caller); onApply rejects to keep the drawer open with the error shown inline and the edited text kept.
export function advancedButton({ title = 'Advanced · source (read-only)', text = () => '', onApply = null }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'erd-dock-button erd-dock-wide plan-advanced-open';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', title);
    button.title = title;
    button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M6 4.5 2.5 8 6 11.5M10 4.5 13.5 8 10 11.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="erd-dock-label">Advanced</span>';
    button.addEventListener('click', () => openSourceDrawer({ title, text: text(), opener: button, onApply }));
    return button;
}

// Right-side drawer modal: backdrop, Escape / close button / backdrop click, focus trap, read-only,
// closed by default and never remembered open. Focus returns to the opener on close.
export function openSourceDrawer({ title, text, opener = document.activeElement, onApply = null }) {
    const inner = onApply ? `<textarea class="plan-advanced-source plan-advanced-edit" spellcheck="false" aria-label="Page source"></textarea>
        <footer class="plan-drawer-foot"><p class="plan-drawer-error" role="alert" hidden></p>
            <button type="button" class="header-button plan-drawer-cancel">Cancel</button>
            <button type="button" class="header-button primary plan-drawer-apply" disabled>Apply</button></footer>`
        : `<pre class="plan-advanced-source" tabindex="0" aria-label="Diagram source, read-only">${esc(text)}</pre>`;
    const shell = openDrawer({ title, opener, html: inner });
    const { drawer, close } = shell;
    if (onApply) {
        const box = drawer.querySelector('.plan-advanced-edit');
        const apply = drawer.querySelector('.plan-drawer-apply');
        const error = drawer.querySelector('.plan-drawer-error');
        box.value = String(text ?? '');
        box.addEventListener('input', () => { apply.disabled = box.value === String(text ?? ''); error.hidden = true; });
        drawer.querySelector('.plan-drawer-cancel').addEventListener('click', close);
        apply.addEventListener('click', async () => {
            if (apply.disabled) return;
            apply.disabled = true;
            try { await onApply(box.value); close(); }
            catch (e) { error.textContent = e?.message || 'Could not apply the change.'; error.hidden = false; apply.disabled = false; }
        });
    }
    return { close, el: shell.el };
}

// Generic right-side drawer shell (same chrome as the source drawer): `html` is trusted markup built by the
// caller with esc(); onClose runs once when the drawer closes (Escape, close button, backdrop, close()).
export function openDrawer({ title, opener = document.activeElement, html = '', className = '', onClose = () => {} }) {
    document.querySelector('.plan-drawer-backdrop')?.remove();
    const backdrop = document.createElement('div');
    backdrop.className = 'plan-drawer-backdrop';
    backdrop.innerHTML = `<aside class="plan-drawer${className ? ` ${esc(className)}` : ''}" role="dialog" aria-modal="true" aria-labelledby="plan-drawer-title" tabindex="-1">
        <header class="plan-drawer-head"><h2 id="plan-drawer-title">${esc(title)}</h2>
            <button type="button" class="erd-dock-button plan-drawer-close" aria-label="Close" title="Close (Esc)">${closeIcon}</button></header>
        ${html}
    </aside>`;
    const drawer = backdrop.querySelector('.plan-drawer');
    const focusables = () => [...drawer.querySelectorAll('button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex="0"]')].filter((n) => !n.closest('[hidden]'));
    let closed = false;
    const close = () => {
        if (closed) return;
        closed = true;
        document.removeEventListener('keydown', onKey, true);
        backdrop.classList.remove('open');
        setTimeout(() => backdrop.remove(), 180);
        try { onClose(); } catch { /* caller cleanup must not block closing */ }
        if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
    const onKey = (e) => {
        if (!backdrop.isConnected) { document.removeEventListener('keydown', onKey, true); return; }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
        if (e.key !== 'Tab') return;
        const list = focusables();
        const first = list[0], last = list[list.length - 1];
        if (!drawer.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) close(); });
    drawer.querySelector('.plan-drawer-close').addEventListener('click', close);
    document.addEventListener('keydown', onKey, true);
    document.body.append(backdrop);
    requestAnimationFrame(() => backdrop.classList.add('open'));
    drawer.querySelector('.plan-drawer-close').focus({ preventScroll: true });
    return { close, el: backdrop, drawer };
}
