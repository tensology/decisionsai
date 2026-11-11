export function mountPagePicker(container, nodes, onChoose) {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'header-button plan-find-page';
    trigger.textContent = 'Find page';
    trigger.setAttribute('aria-haspopup', 'dialog');

    const dialog = document.createElement('dialog');
    dialog.className = 'plan-memory-dialog plan-page-picker';
    dialog.setAttribute('aria-labelledby', 'plan-page-picker-title');
    dialog.innerHTML = `<header><h2 id="plan-page-picker-title">Find page</h2>
        <button type="button" class="header-button" aria-label="Close page picker">Close</button></header>
        <input type="search" aria-label="Search pages" placeholder="Search by page name or route" autocomplete="off" autofocus>
        <p role="status" class="plan-page-picker-status"></p><ul aria-label="Matching pages"></ul>`;
    const search = dialog.querySelector('input');
    const status = dialog.querySelector('[role="status"]');
    const list = dialog.querySelector('ul');
    const close = dialog.querySelector('header button');
    const sorted = [...nodes].sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }) || a.route.localeCompare(b.route));
    let chosePage = false;

    function choose(node) {
        chosePage = true;
        dialog.close();
        onChoose(node);
    }

    function filter() {
        const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const matches = sorted.filter((node) => {
            const text = `${node.label} ${node.route}`.toLowerCase();
            return terms.every((term) => text.includes(term));
        });
        list.replaceChildren(...matches.map((node) => {
            const row = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            const title = document.createElement('strong');
            title.textContent = node.label;
            button.append(title);
            if (node.route) {
                const route = document.createElement('small');
                route.textContent = node.route;
                button.append(route);
            }
            if (!node.screenId) {
                const note = document.createElement('small');
                note.textContent = 'No outline yet';
                button.append(note);
            }
            button.onclick = () => choose(node);
            row.append(button);
            return row;
        }));
        status.textContent = matches.length ? `${matches.length} ${matches.length === 1 ? 'page' : 'pages'}` : 'No matching pages';
    }

    search.addEventListener('input', filter);
    dialog.addEventListener('keydown', (event) => {
        if (event.isComposing) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            dialog.close();
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
            buttons[Math.min(index + 1, buttons.length - 1)].focus();
        } else if (event.key === 'ArrowUp' && index >= 0) {
            event.preventDefault();
            (buttons[index - 1] || search).focus();
        }
    });
    close.onclick = () => dialog.close();
    dialog.addEventListener('click', (event) => {
        if (event.target !== dialog) return;
        const { left, top, right, bottom } = dialog.getBoundingClientRect();
        if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) dialog.close();
    });
    dialog.addEventListener('close', () => { if (!chosePage) trigger.focus({ preventScroll: true }); });
    trigger.onclick = () => {
        chosePage = false;
        search.value = '';
        filter();
        dialog.showModal();
        search.focus();
    };
    container.append(trigger, dialog);
}
