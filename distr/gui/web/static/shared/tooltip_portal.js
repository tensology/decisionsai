// Fixed-position tooltips for the composer/selector area, so no container overflow can clip them.
// [data-tooltip] shows its text; [data-tooltip-if-truncated] shows its own text only when it is cut off.
// Inside an open <dialog> the tip is appended to that dialog (top layer); otherwise to <body>.
const SCOPE = '.studio-composer, .model-dialog, .complexity-dialog';

export function installTooltipPortal(root = document) {
    if (root.__tooltipPortal) return;
    root.__tooltipPortal = true;
    let tip = null;
    let owner = null;
    const hide = () => {
        tip?.remove();
        tip = null;
        owner = null;
    };
    const textFor = (target) => {
        if (target.hasAttribute('data-tooltip')) return target.getAttribute('data-tooltip') || '';
        const truncated = target.scrollWidth > target.clientWidth + 1;
        return truncated ? (target.textContent || '').trim() : '';
    };
    root.addEventListener('pointerover', (event) => {
        const target = event.target instanceof Element ? event.target.closest('[data-tooltip], [data-tooltip-if-truncated]') : null;
        if (!target || !target.closest(SCOPE)) return;
        if (target === owner) return;
        hide();
        const text = textFor(target);
        if (!text || target.getAttribute('aria-expanded') === 'true') return;
        // While a selector dialog is open, only its own controls get tips.
        const openDialog = document.querySelector('dialog.model-dialog[open], dialog.complexity-dialog[open]');
        if (openDialog && !openDialog.contains(target)) return;
        owner = target;
        tip = document.createElement('div');
        tip.className = 'studio-tooltip-portal';
        tip.setAttribute('role', 'tooltip');
        tip.textContent = text;
        (target.closest('dialog[open]') || document.body).appendChild(tip);
        const rect = target.getBoundingClientRect();
        const box = tip.getBoundingClientRect();
        const margin = 8;
        let left = rect.left + rect.width / 2 - box.width / 2;
        let top = rect.top - box.height - 6;
        // Rows inside the selector pop-up: put the tip beside the pop-up so it never covers other rows.
        const panel = target.closest('.model-dialog');
        if (panel && target.closest('.model-menu-row, .model-choice')) {
            const outer = panel.getBoundingClientRect();
            top = rect.top + rect.height / 2 - box.height / 2;
            left = outer.left - box.width - 8;
            if (left < margin) left = outer.right + 8;
        }
        left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
        if (top < margin) top = rect.bottom + 6;
        tip.style.left = `${Math.round(left)}px`;
        tip.style.top = `${Math.round(top)}px`;
    });
    root.addEventListener('pointerout', (event) => {
        if (!owner) return;
        const next = event.relatedTarget;
        if (next instanceof Node && owner.contains(next)) return;
        hide();
    });
    ['pointerdown', 'scroll', 'keydown'].forEach((name) => root.addEventListener(name, hide, true));
    window.addEventListener('blur', hide);
}
