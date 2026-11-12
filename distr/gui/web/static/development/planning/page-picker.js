function pastedPath(value) {
    if (/^https?:\/\//i.test(value)) {
        try {
            return new URL(value).pathname;
        } catch {
            return '';
        }
    }
    return value.startsWith('/') ? value.split(/[?#]/, 1)[0] : '';
}

function routePatternSpecificity(route, segments) {
    const parts = route.split('/');
    if (parts.length !== segments.length
        || !parts.every((part, index) => part === segments[index] || (part.startsWith(':') && segments[index]))) return -1;
    const literals = parts.filter(part => !part.startsWith(':')).length;
    return literals < parts.length ? literals : -1;
}

export function mountPagePicker(container, nodes, onChoose, state) {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'header-button primary plan-find-page';
    trigger.textContent = 'Find page';
    trigger.setAttribute('aria-haspopup', 'dialog');

    const dialog = document.createElement('dialog');
    dialog.className = 'plan-memory-dialog plan-page-picker';
    dialog.setAttribute('aria-labelledby', 'plan-page-picker-title');
    dialog.innerHTML = `<header><h2 id="plan-page-picker-title">Find page</h2>
        <button type="button" class="header-button" aria-label="Close page picker">Close</button></header>
        <input type="search" aria-label="Search pages" placeholder="Search by name, route or template" autocomplete="off" autofocus>
        <p role="status" class="plan-page-picker-status"></p><ul aria-label="Matching pages"></ul>`;
    const search = dialog.querySelector('input');
    const status = dialog.querySelector('[role="status"]');
    const list = dialog.querySelector('ul');
    const close = dialog.querySelector('header button');
    const sorted = [...nodes]
        .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }) || a.route.localeCompare(b.route))
        .map(node => ({
            node,
            label: node.label.toLowerCase(),
            route: node.route.toLowerCase(),
            template: (node.template || '').toLowerCase(),
        }));
    let chosePage = false;
    let opener = trigger;
    let activeResult = null;

    function dismiss() {
        state.scrollTop = list.scrollTop;
        dialog.close();
    }

    function choose(node) {
        state.activeKey = node.key;
        chosePage = true;
        dismiss();
        onChoose(node);
    }

    function filter() {
        const value = search.value.trim();
        const path = pastedPath(value).toLowerCase();
        const routeQuery = path.replace(/\/+$/, '') || path;
        const query = routeQuery || value.toLowerCase().replace(/\s+/g, ' ');
        const segments = routeQuery ? routeQuery.split('/') : [];
        const terms = routeQuery ? [routeQuery] : query.split(' ').filter(Boolean);
        const childPrefix = routeQuery === '/' ? '/' : `${routeQuery}/`;
        const matches = sorted.flatMap(({ node, label, route, template }) => {
            const path = route.replace(/\/+$/, '') || route;
            const exact = routeQuery ? path === routeQuery : label === query || template === query;
            const specificity = routeQuery ? routePatternSpecificity(path, segments) : -1;
            const text = `${label} ${route} ${template}`;
            if (!exact && specificity < 0 && !terms.every(term => text.includes(term))) return [];
            const prefix = routeQuery ? path.startsWith(childPrefix) : label.startsWith(query);
            return [{ node, specificity, rank: exact ? 0 : prefix ? 1 : specificity >= 0 ? 2 : 3 }];
        }).sort((a, b) => a.rank - b.rank || b.specificity - a.specificity);
        activeResult = null;
        list.replaceChildren(...matches.map(({ node }) => {
            const row = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.tabIndex = -1;
            button.dataset.pageKey = node.key;
            if (node.key === state.activeKey) activeResult = button;
            const title = document.createElement('strong');
            title.textContent = node.label;
            button.append(title);
            const metadata = document.createElement('span');
            metadata.className = 'plan-page-picker-meta';
            if (node.route) {
                const route = document.createElement('small');
                route.textContent = node.route;
                metadata.append(route);
            }
            if (node.template && !node.screenId) {
                const template = document.createElement('small');
                template.textContent = node.template;
                metadata.append(template);
            }
            if (!node.screenId) {
                const note = document.createElement('small');
                note.textContent = 'No outline yet';
                metadata.append(note);
            }
            if (metadata.childElementCount) button.append(metadata);
            button.onclick = () => choose(node);
            row.append(button);
            return row;
        }));
        activeResult ||= list.querySelector('button');
        if (activeResult) activeResult.tabIndex = 0;
        status.textContent = matches.length ? `${matches.length} ${matches.length === 1 ? 'page' : 'pages'}` : 'No matching pages';
    }

    list.addEventListener('focusin', (event) => {
        if (!event.target.matches('button')) return;
        state.activeKey = event.target.dataset.pageKey;
        if (event.target === activeResult) return;
        if (activeResult) activeResult.tabIndex = -1;
        activeResult = event.target;
        activeResult.tabIndex = 0;
    });
    search.addEventListener('input', () => {
        state.query = search.value;
        state.scrollTop = 0;
        state.activeKey = null;
        filter();
        list.scrollTop = 0;
    });
    dialog.addEventListener('keydown', (event) => {
        if (event.isComposing) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            dismiss();
            return;
        }
        const buttons = [...list.querySelectorAll('button')];
        const index = buttons.indexOf(event.target);
        if (event.target !== search && index < 0) return;
        if (event.key === 'Enter' && event.target === search) {
            event.preventDefault();
            buttons[0]?.click();
        } else if (event.key === 'ArrowDown' && buttons.length) {
            event.preventDefault();
            (index < 0 ? activeResult : buttons[Math.min(index + 1, buttons.length - 1)]).focus();
        } else if (event.key === 'ArrowUp' && index >= 0) {
            event.preventDefault();
            (buttons[index - 1] || search).focus();
        }
    });
    close.onclick = dismiss;
    dialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        dismiss();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target !== dialog) return;
        const { left, top, right, bottom } = dialog.getBoundingClientRect();
        if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) dismiss();
    });
    dialog.addEventListener('close', () => {
        if (!chosePage) (opener.isConnected ? opener : trigger).focus({ preventScroll: true });
    });
    function open(from = trigger) {
        opener = from;
        chosePage = false;
        search.value = state.query || '';
        filter();
        dialog.showModal();
        search.focus();
        list.scrollTop = state.scrollTop || 0;
    }
    trigger.onclick = () => open();
    container.append(trigger, dialog);
    return open;
}
