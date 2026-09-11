export function createPlanning(host, root = document.getElementById('plan-root')) {
    const state = {
        boards: [],
        projects: [],
        workspaces: [],
        workspace: null,
        item: null,
        mode: 'edit',
        search: '',
        showUnlinked: false,
        busy: false,
        dirty: false,
        saveTimer: null,
        savePromise: null,
        navigation: 0,
        view: 'home'
    };

    const escapeHtml = host.escapeHtml;
    const projectFor = (board) => state.projects.find((project) => Number(project.id) === Number(board?.project_id || board?.default_project_id)) || null;
    const workspaceFor = (board) => state.workspaces.find((workspace) => workspace.board_key === board.key) || null;
    const providerName = (board) => (board?.provider === 'jira' ? 'Jira' : board?.provider === 'trello' ? 'Trello' : 'Local');
    const itemIcon = (item) => (item.content_format === 'mermaid' ? '⌘' : item.content_format === 'html' ? '◇' : '▤');

    async function api(path, options) {
        return host.api(path, options);
    }

    function setBusy(value) {
        state.busy = Boolean(value);
        root.querySelectorAll('button, input, textarea, select').forEach((control) => {
            if (!control.dataset.allowBusy) control.disabled = state.busy;
        });
    }

    async function loadWorkspaces() {
        state.workspaces = (await api('/workflows/studio/plan-workspaces')).items || [];
    }

    function homeResultsHtml() {
        const query = state.search.trim().toLowerCase();
        const boards = state.boards.filter(
            (board) =>
                !query ||
                String(board.name || '')
                    .toLowerCase()
                    .includes(query)
        );
        const workingBoards = boards.filter((board) => Boolean(projectFor(board)));
        const unlinkedBoards = boards.filter((board) => !projectFor(board));
        const emptyMessage = query ? 'No linked boards match this search.' : 'No linked boards are ready for planning.';
        return `<div class="plan-board-grid">${
            workingBoards
                .map((board) => {
                    const workspace = workspaceFor(board);
                    const project = projectFor(board);
                    const count = Number(workspace?.item_count || 0);
                    return `<article class="plan-board-card" data-plan-board="${escapeHtml(board.key)}">
                    <button type="button" class="plan-board-open" data-plan-board-open="${escapeHtml(board.key)}">
                        <span class="plan-board-icon" aria-hidden="true">≡</span>
                        <span class="plan-board-copy"><strong>${escapeHtml(board.name || 'Untitled board')}</strong><small>${escapeHtml(project?.folder_location || 'No project folder')}</small></span>
                        <span class="plan-board-state">${count ? `${count} item${count === 1 ? '' : 's'}` : 'Start plan'}<i aria-hidden="true">›</i></span>
                    </button>
                </article>`;
                })
                .join('') || `<div class="plan-empty plan-home-empty">${emptyMessage}</div>`
        }</div>
            ${
                unlinkedBoards.length
                    ? `<details class="plan-unlinked"${state.showUnlinked || query ? ' open' : ''}>
                <summary><span>Unlinked boards</span><small>${unlinkedBoards.length}</small></summary>
                <div class="plan-unlinked-list">
                    <p>Link a project to make planning available.</p>
                    ${unlinkedBoards.map((board) => `<div class="plan-unlinked-row" data-plan-unlinked="${escapeHtml(board.key)}"><strong>${escapeHtml(board.name || 'Untitled board')}</strong><small>${providerName(board)}</small></div>`).join('')}
                </div>
            </details>`
                    : ''
            }`;
    }

    function homeHtml() {
        return `<div class="plan-home">
            <header class="plan-home-header"><div><h1>Plans</h1><p>Plan work for boards linked to a project.</p></div></header>
            <label class="plan-search"><span aria-hidden="true">⌕</span><input id="plan-search" type="search" value="${escapeHtml(state.search)}" placeholder="Search boards" aria-label="Search plan boards"></label>
            <div id="plan-home-results">${homeResultsHtml()}</div>
        </div>`;
    }

    function bindHomeResults() {
        root.querySelector('.plan-unlinked')?.addEventListener('toggle', (event) => {
            state.showUnlinked = event.target.open;
        });
        root.querySelectorAll('[data-plan-board-open]').forEach((button) =>
            button.addEventListener('click', () => {
                const board = state.boards.find((row) => row.key === button.dataset.planBoardOpen);
                if (board) host.openBoardPlan(board.key);
            })
        );
    }

    function bindHome() {
        root.querySelector('#plan-search')?.addEventListener('input', (event) => {
            state.search = event.target.value;
            if (state.search.trim()) state.showUnlinked = true;
            root.querySelector('#plan-home-results').innerHTML = homeResultsHtml();
            bindHomeResults();
        });
        bindHomeResults();
    }

    async function openHome(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (!(await beforeLeave())) return false;
        const navigation = ++state.navigation;
        state.view = 'home';
        state.workspace = null;
        state.item = null;
        try {
            await loadWorkspaces();
        } catch (error) {
            root.innerHTML = `<div class="plan-empty error">${escapeHtml(error.message || 'Could not load plans.')}</div>`;
            return;
        }
        if (navigation !== state.navigation) return;
        root.innerHTML = homeHtml();
        bindHome();
    }

    function itemListHtml() {
        const items = state.workspace?.items || [];
        return items.length
            ? items
                  .map(
                      (
                          item
                      ) => `<button type="button" class="plan-item-row${Number(state.item?.id) === Number(item.id) ? ' active' : ''}" data-plan-item="${Number(item.id)}">
            <span aria-hidden="true">${itemIcon(item)}</span><span class="plan-item-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.purpose || 'Plan artifact')}</small></span><small>${item.status === 'approved' ? '✓' : ''}</small>
        </button>`
                  )
                  .join('')
            : '<p class="plan-outline-empty">Use the voice bar to create a brief, PRD, FRAC, ERD, flow, file tree, or handover.</p>';
    }

    function markdownHtml(content) {
        const lines = String(content || '').split('\n');
        const groups = [];
        let current = null;
        lines.forEach((line) => {
            const heading = line.match(/^#{1,3}\s+(.+)$/);
            const bullet = line.match(/^[-*]\s+(.+)$/);
            if (heading) {
                current = { title: heading[1], bullets: [], copy: [] };
                groups.push(current);
            } else if (bullet && current) current.bullets.push(bullet[1]);
            else if (line.trim() && current) current.copy.push(line.trim());
        });
        if (!groups.length) return `<div class="plan-artifact-cards"><article class="plan-artifact-card"><p>${escapeHtml(String(content || ''))}</p></article></div>`;
        return `<div class="plan-artifact-cards">${groups
            .map((group) => `<article class="plan-artifact-card"><h${group.title === groups[0].title ? '2' : '3'}>${escapeHtml(group.title)}</h${group.title === groups[0].title ? '2' : '3'}>${group.copy.map((copy) => `<p>${escapeHtml(copy)}</p>`).join('')}${group.bullets.length ? `<ul>${group.bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}</ul>` : ''}</article>`)
            .join('')}</div>`;
    }

    function fileTreeHtml(content) {
        const rows = String(content || '')
            .split('\n')
            .filter((line) => /^\s*-\s+[▸•]/.test(line))
            .map((line) => {
                const match = line.match(/^(\s*)-\s+[▸•]\s+`?([^`]+)`?$/);
                if (!match) return '';
                const depth = Math.min(5, Math.floor(match[1].length / 2));
                const directory = line.includes('▸');
                return `<li style="--tree-depth:${depth}" class="${directory ? 'directory' : 'file'}"><span aria-hidden="true">${directory ? '▸' : '•'}</span><code>${escapeHtml(match[2])}</code></li>`;
            })
            .join('');
        return `<div class="plan-file-tree" aria-label="Project file structure"><p>Bounded view of the linked project. Generated and hidden folders are omitted.</p><ul>${rows || '<li class="file"><code>No visible project files found.</code></li>'}</ul></div>`;
    }

    function wireframeHtml(item) {
        const title = escapeHtml(item?.title || 'Flow storyboard');
        return `<div class="plan-wireframe-preview" aria-label="${title} storyboard">
            <svg viewBox="0 0 920 520" role="img" aria-labelledby="plan-wireframe-title plan-wireframe-desc">
                <title id="plan-wireframe-title">${title}</title><desc id="plan-wireframe-desc">Annotated screen storyboard with loading, ready, and error states.</desc>
                <rect x="24" y="24" width="872" height="472" rx="14" class="wireframe-shell"/><rect x="24" y="24" width="872" height="54" rx="14" class="wireframe-toolbar"/>
                <circle cx="54" cy="51" r="7" class="wireframe-dot"/><circle cx="78" cy="51" r="7" class="wireframe-dot"/><circle cx="102" cy="51" r="7" class="wireframe-dot"/>
                <rect x="54" y="112" width="190" height="338" rx="8" class="wireframe-panel"/><rect x="272" y="112" width="578" height="70" rx="8" class="wireframe-panel"/>
                <rect x="296" y="136" width="220" height="12" rx="6" class="wireframe-line strong"/><rect x="296" y="204" width="530" height="92" rx="8" class="wireframe-state"/>
                <text x="320" y="232" class="wireframe-label">READY STATE</text><text x="320" y="260" class="wireframe-copy">Primary content is visible and actionable.</text>
                <rect x="296" y="318" width="252" height="108" rx="8" class="wireframe-state muted"/><text x="320" y="346" class="wireframe-label">LOADING</text><rect x="320" y="364" width="176" height="9" rx="4" class="wireframe-line"/>
                <rect x="574" y="318" width="252" height="108" rx="8" class="wireframe-state error"/><text x="598" y="346" class="wireframe-label">ERROR</text><text x="598" y="374" class="wireframe-copy">Explain the problem and recovery.</text>
                <rect x="78" y="140" width="120" height="10" rx="5" class="wireframe-line strong"/><rect x="78" y="176" width="132" height="28" rx="6" class="wireframe-nav active"/><rect x="78" y="220" width="112" height="10" rx="5" class="wireframe-line"/><rect x="78" y="252" width="98" height="10" rx="5" class="wireframe-line"/>
            </svg><p>Screen storyboard with visible ready, loading, and error states. Add annotations through the language bar.</p>
        </div>`;
    }

    async function renderMermaid(target, content) {
        if (!window.mermaid) {
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = '/static/vendor/mermaid/mermaid.min.js';
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
            window.mermaid.initialize({
                startOnLoad: false,
                securityLevel: 'strict',
                theme: 'dark'
            });
        }
        const result = await window.mermaid.render(`plan-diagram-${Date.now()}`, content);
        target.innerHTML = result.svg;
    }

    function canvasHtml() {
        const item = state.item;
        if (!item) return `<div class="plan-canvas-empty"><strong>No plan items yet</strong><p>Use the language bar to create the first useful item.</p></div>`;
        const preview = state.mode === 'preview';
        return `<div class="plan-item-canvas">
            <header class="plan-item-header">
                <input id="plan-item-title" value="${escapeHtml(item.title)}" aria-label="Plan item title">
                <span class="plan-artifact-mode">${escapeHtml(item.visual_mode || 'Review card')}</span><span>v${Number(item.revision_count || 1)}</span>
                <div class="plan-view-toggle" role="group" aria-label="Item view"><button type="button" data-plan-view="edit" class="${preview ? '' : 'active'}">Edit</button><button type="button" data-plan-view="preview" class="${preview ? 'active' : ''}">Preview</button></div>
                <select id="plan-item-status" aria-label="Plan item status"><option value="draft"${item.status === 'draft' ? ' selected' : ''}>Draft</option><option value="review"${item.status === 'review' ? ' selected' : ''}>Review</option><option value="approved"${item.status === 'approved' ? ' selected' : ''}>Approved</option></select>
                <button type="button" class="plan-save" id="plan-save">Save</button>
            </header>
            <div class="plan-file-status">${item.file_path ? `<button type="button" id="plan-review-file">Review file changes</button><span role="status">${escapeHtml(item.file_sync_error || '')}</span>` : ''}</div>
            ${
                preview
                ? `<div class="plan-preview" id="plan-preview">${item.item_type === 'file_structure' ? fileTreeHtml(item.content) : item.content_format === 'markdown' ? markdownHtml(item.content) : item.content_format === 'html' ? wireframeHtml(item) : '<div class="plan-mermaid" id="plan-mermaid"></div>'}</div>`
                    : `<textarea class="plan-editor" id="plan-editor" spellcheck="false" aria-label="Plan item content">${escapeHtml(item.content)}</textarea>`
            }
        </div>`;
    }

    async function showTicketPreview() {
        try {
            const data = await api(`/workflows/studio/plan-workspaces/${Number(state.workspace.id)}/ticket-preview`);
            const rows = data.items?.length
                ? data.items.map((row) => `<li><strong>${row.position}. ${escapeHtml(row.title)}</strong><span>${row.ready ? 'Ready to build' : 'Needs approval'} · ${row.action}</span></li>`).join('')
                : '<li>No plan sections yet.</li>';
            const dialog = document.createElement('dialog');
            dialog.className = 'plan-ticket-dialog';
            dialog.innerHTML = `<form method="dialog"><header><div><span class="plan-kicker">BUILD PREVIEW</span><h2>${escapeHtml(data.board_name)}</h2><p>This preview is non-mutating. Stable identities let reruns update or skip existing tickets instead of duplicating them.</p></div><button aria-label="Close">×</button></header><div class="plan-ticket-summary"><strong>${data.creates} ready</strong><span>${data.needs_review} need review</span></div><ol>${rows}</ol><footer><button type="button" class="plan-build-tickets" id="plan-build-tickets"${data.creates ? '' : ' disabled'}>Build approved tickets</button><button value="close">Close preview</button></footer></form>`;
            document.body.appendChild(dialog);
            dialog.addEventListener('close', () => dialog.remove(), { once: true });
            dialog.querySelector('#plan-build-tickets')?.addEventListener('click', async () => {
                const buildButton = dialog.querySelector('#plan-build-tickets');
                buildButton.disabled = true;
                buildButton.textContent = 'Building…';
                try {
                    const result = await api(`/workflows/studio/plan-workspaces/${Number(state.workspace.id)}/build-tickets`, { method: 'POST' });
                    buildButton.textContent = `${result.created.length} built, ${result.reused.length} reused`;
                    buildButton.classList.add('complete');
                } catch (error) {
                    buildButton.disabled = false;
                    buildButton.textContent = 'Build approved tickets';
                    host.notify?.(error.message || 'Could not build tickets.');
                }
            });
            dialog.showModal();
        } catch (error) {
            host.notify?.(error.message || 'Could not preview tickets.');
        }
    }

    function workspaceHtml() {
        const item = state.item;
        const summary = state.workspace?.summary || { total: 0, approved: 0, progress_percent: 0, open_items: [], next_action: 'Start with the brief.' };
        const openItems = summary.open_items?.length ? `<span>${escapeHtml(summary.open_items.slice(0, 3).join(', '))}${summary.open_items.length > 3 ? ' +' + (summary.open_items.length - 3) : ''}</span>` : '<span>None</span>';
        return `<div class="plan-detail">
            <header class="plan-detail-header"><button type="button" id="plan-back" aria-label="Back to plans" title="Back to plans">‹</button><div><strong>${escapeHtml(state.workspace.board_name)}</strong><small>Voice brief to visual plan to buildable tickets</small></div><button type="button" class="plan-ticket-preview" id="plan-ticket-preview">Preview ticket sequence</button><span>${Number(state.workspace.item_count || 0)} sections</span></header>
            <label class="plan-mobile-picker">Item<select id="plan-mobile-item" aria-label="Select plan item">${(state.workspace.items || []).map((row) => `<option value="${Number(row.id)}"${Number(row.id) === Number(state.item?.id) ? ' selected' : ''}>${escapeHtml(row.title)}</option>`).join('')}</select></label>
            <div class="plan-detail-body"><aside class="plan-outline"><div class="plan-outline-title"><span>Plan sections</span><small>${escapeHtml(state.workspace.root_path || 'Database only')}</small></div><div class="plan-progress" aria-label="Plan progress"><div><strong>${Number(summary.progress_percent)}%</strong><span>${Number(summary.approved)} of ${Number(summary.total)} approved</span></div><div class="plan-progress-track"><i style="width:${Math.min(100, Math.max(0, Number(summary.progress_percent)))}%"></i></div><small>Open: ${openItems}</small><small>Next: ${escapeHtml(summary.next_action || '')}</small></div><div id="plan-item-list">${itemListHtml()}</div></aside><main class="plan-canvas"><div class="plan-workspace-intro"><span class="plan-kicker">PLAN WORKSPACE</span><h1>Shape the work before the build starts.</h1><p>Speak or type what you want. Plan turns it into visible requirements, diagrams, screens, and a handover package. Select a section to refine it.</p><div class="plan-intro-cards"><button type="button" data-plan-item="${Number(state.workspace.items?.[0]?.id || 0)}"><strong>1. Capture</strong><span>Turn the brief into an outcome and boundaries.</span></button><button type="button" data-plan-item="${Number(state.workspace.items?.[1]?.id || 0)}"><strong>2. Make it visual</strong><span>Review requirements, flows, ERD, and SVG screens.</span></button><button type="button" id="plan-intro-tickets"><strong>3. Build in sequence</strong><span>Preview ordered tickets without duplicates.</span></button></div></div>${canvasHtml()}</main></div>
            <form class="plan-language" id="plan-language"><select id="plan-language-scope" aria-label="Language command scope"><option value="board">Board</option>${item ? '<option value="item" selected>This item</option>' : ''}</select><input id="plan-language-input" placeholder="Tell Plan what to capture, for example: add the auth requirement" aria-label="Plan instruction"><button type="button" id="plan-language-mic" aria-label="Capture instruction by voice" aria-pressed="false" title="Capture instruction by voice">●</button><span id="plan-voice-status" class="plan-voice-status" role="status" aria-live="polite">Voice ready</span><button type="submit" aria-label="Apply instruction">↑</button></form>
        </div>`;
    }

    async function paintWorkspace() {
        state.busy = false;
        root.innerHTML = workspaceHtml();
        bindWorkspace();
        if (state.mode === 'preview' && state.item?.content_format === 'mermaid') {
            try {
                await renderMermaid(root.querySelector('#plan-mermaid'), state.item.content);
            } catch (error) {
                root.querySelector('#plan-mermaid').textContent = error.message || 'Could not render diagram.';
            }
        }
    }

    async function selectItem(itemId) {
        if (!(await beforeLeave())) {
            const picker = root.querySelector('#plan-mobile-item');
            if (picker) picker.value = String(state.item?.id || '');
            return;
        }
        state.item = (state.workspace?.items || []).find((item) => Number(item.id) === Number(itemId)) || null;
        state.mode = 'edit';
        paintWorkspace();
    }

    async function reloadWorkspace(itemId) {
        state.workspace = await api(`/workflows/studio/plan-workspaces/${Number(state.workspace.id)}`);
        state.item = (state.workspace.items || []).find((item) => Number(item.id) === Number(itemId || state.item?.id)) || state.workspace.items?.[0] || null;
        await paintWorkspace();
    }

    function bindWorkspace() {
        root.querySelector('#plan-mobile-item')?.addEventListener('change', (event) => selectItem(event.target.value));
        root.querySelector('#plan-back')?.addEventListener('click', async () => {
            if (await beforeLeave()) host.openHome();
        });
        root.querySelectorAll('[data-plan-item]').forEach((button) => button.addEventListener('click', () => selectItem(button.dataset.planItem)));
        root.querySelectorAll('[data-plan-view]').forEach((button) =>
            button.addEventListener('click', async () => {
                if (!(await beforeLeave())) return;
                state.mode = button.dataset.planView;
                paintWorkspace();
            })
        );
        root.querySelector('#plan-review-file')?.addEventListener('click', reviewFile);
        root.querySelector('#plan-ticket-preview')?.addEventListener('click', showTicketPreview);
        root.querySelector('#plan-intro-tickets')?.addEventListener('click', showTicketPreview);
        root.querySelector('#plan-save')?.addEventListener('click', () => saveItem().catch(() => {}));
        root.querySelector('#plan-language')?.addEventListener('submit', applyInstruction);
        root.querySelector('#plan-language-mic')?.addEventListener('click', captureInstructionByVoice);
        ['plan-item-title', 'plan-editor'].forEach((id) => root.querySelector(`#${id}`)?.addEventListener('input', markDirty));
        root.querySelector('#plan-item-status')?.addEventListener('change', markDirty);
    }

    function captureInstructionByVoice() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const input = root.querySelector('#plan-language-input');
        const button = root.querySelector('#plan-language-mic');
        const status = root.querySelector('#plan-voice-status');
        if (!SpeechRecognition || !input || !button) {
            if (status) status.textContent = 'Voice unavailable in this browser';
            host.notify?.('Voice capture is not available in this browser. You can still type the instruction.');
            return;
        }
        const recognition = new SpeechRecognition();
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition.lang = document.documentElement.lang || 'en-ZA';
        button.textContent = '…';
        button.setAttribute('aria-pressed', 'true');
        button.classList.add('listening');
        if (status) status.textContent = 'Listening...';
        recognition.onresult = (event) => {
            input.value = event.results[0][0].transcript;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            if (status) status.textContent = 'Captured. Review, then apply.';
        };
        recognition.onerror = () => {
            if (status) status.textContent = 'Voice stopped. Check microphone permission.';
            host.notify?.('Voice capture stopped. Check microphone permission and try again.');
        };
        recognition.onend = () => {
            button.textContent = '●';
            button.setAttribute('aria-pressed', 'false');
            button.classList.remove('listening');
            if (status && status.textContent === 'Listening...') status.textContent = 'Voice ready';
        };
        recognition.start();
    }

    function markDirty() {
        state.dirty = true;
        const button = root.querySelector('#plan-save');
        if (button) button.textContent = 'Save changes';
        window.clearTimeout(state.saveTimer);
        state.saveTimer = window.setTimeout(() => saveItem({ quiet: true }).catch(() => {}), 800);
    }

    async function flushPendingSave() {
        window.clearTimeout(state.saveTimer);
        state.saveTimer = null;
        if (state.savePromise) await state.savePromise;
        if (state.dirty) await saveItem({ quiet: true });
    }

    async function beforeLeave() {
        try {
            await flushPendingSave();
            return true;
        } catch (_) {
            return false;
        }
    }

    function leave() {
        ++state.navigation;
        window.clearTimeout(state.saveTimer);
    }

    function saveItem(options = {}) {
        if (state.savePromise) return state.savePromise;
        state.savePromise = performSave(options).finally(() => {
            state.savePromise = null;
        });
        return state.savePromise;
    }

    async function performSave(options = {}) {
        if (!state.item || (!state.dirty && options.quiet)) return;
        window.clearTimeout(state.saveTimer);
        state.saveTimer = null;
        setBusy(true);
        try {
            const updated = await api(`/workflows/studio/plan-items/${Number(state.item.id)}`, {
                method: 'PATCH',
                body: {
                    expected_revision: state.item.revision_count,
                    title: root.querySelector('#plan-item-title').value.trim(),
                    content: root.querySelector('#plan-editor')?.value ?? state.item.content,
                    status: root.querySelector('#plan-item-status').value
                }
            });
            state.item = updated;
            const index = (state.workspace?.items || []).findIndex((item) => Number(item.id) === Number(updated.id));
            if (index >= 0) state.workspace.items[index] = updated;
            state.dirty = false;
            setBusy(false);
            const status = root.querySelector('#plan-item-status');
            if (status) status.value = updated.status;
            const button = root.querySelector('#plan-save');
            if (button) button.textContent = 'Saved';
            if (updated.file_sync_error) host.toast(updated.file_sync_error, 'error');
            else if (!options.quiet) host.toast('Plan item saved.');
        } catch (error) {
            host.toast(error.message || 'Could not save the plan item.', 'error');
            setBusy(false);
            throw error;
        }
    }

    async function reviewFile() {
        window.clearTimeout(state.saveTimer);
        try {
            const itemId = state.item.id;
            const review = await api(`/workflows/studio/plan-items/${itemId}/file-review`);
            const dialog = document.createElement('dialog');
            dialog.className = 'plan-file-dialog';
            const restoring = review.file_content === null;
            const reviewDraft = state.dirty && !restoring;
            dialog.innerHTML = `<h2>Review file changes</h2><p>${restoring ? 'Restore the saved version to disk. Any unsaved editor draft will remain in the editor.' : state.dirty ? 'Your editor has an unsaved draft, shown below. Importing the file replaces that draft.' : 'Existing file content will be preserved.'}</p><div class="plan-file-comparison"><label>${reviewDraft ? 'Unsaved editor draft' : 'Saved in Decisions'}<pre>${escapeHtml(reviewDraft ? root.querySelector('#plan-editor')?.value : review.saved_content)}</pre></label><label>Project file<pre>${escapeHtml(review.file_content ?? 'File is missing.')}</pre></label></div><p role="status"></p><footer><button type="button" data-file-cancel>Keep current editor</button><button type="button" data-file-apply>${review.file_content === null ? 'Restore saved file' : 'Import reviewed file'}</button></footer>`;
            root.append(dialog);
            dialog.querySelector('[data-file-cancel]').onclick = () => dialog.close();
            dialog.addEventListener('close', () => dialog.remove(), { once: true });
            dialog.querySelector('[data-file-apply]').onclick = async () => {
                const button = dialog.querySelector('[data-file-apply]');
                button.disabled = true;
                try {
                    await api(`/workflows/studio/plan-items/${itemId}/file-review`, {
                        method: 'POST',
                        body: {
                            expected_revision: review.expected_revision,
                            file_hash: review.file_hash,
                            action: review.file_content === null ? 'restore' : 'import'
                        }
                    });
                    dialog.close();
                    if (!restoring || !state.dirty) {
                        state.dirty = false;
                        await reloadWorkspace(itemId);
                    }
                    host.toast('File reconciliation saved. Previous saved revisions remain in history.');
                } catch (error) {
                    dialog.querySelector('[role="status"]').textContent = error.message;
                    button.disabled = false;
                }
            };
            dialog.showModal();
        } catch (error) {
            host.toast(error.message, 'error');
        }
    }

    async function applyInstruction(event) {
        event.preventDefault();
        const input = root.querySelector('#plan-language-input');
        const instruction = input.value.trim();
        if (!instruction || state.busy) return;
        if (!(await beforeLeave())) return;
        const itemScope = root.querySelector('#plan-language-scope').value === 'item';
        setBusy(true);
        try {
            const result = await api(`/workflows/studio/plan-workspaces/${Number(state.workspace.id)}/instructions`, {
                method: 'POST',
                body: {
                    instruction,
                    item_id: itemScope ? state.item?.id : null
                }
            });
            if (result.action === 'needs_detail') {
                host.toast(result.message, 'error');
                setBusy(false);
                input.focus();
                return;
            }
            await reloadWorkspace(result.item?.id);
            host.toast(result.message || (result.action === 'created' ? 'Plan item created.' : 'Plan item updated.'));
        } catch (error) {
            host.toast(error.message || 'Could not apply the instruction.', 'error');
            setBusy(false);
        }
    }

    async function openBoard(board, projects) {
        if (!(await beforeLeave())) return false;
        const navigation = ++state.navigation;
        state.view = 'board';
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (!projectFor(board)) {
            host.toast('Link this board to a project before opening Plan.', 'error');
            host.openHome();
            return;
        }
        setBusy(true);
        try {
            const workspace = await api('/workflows/studio/plan-workspaces', {
                method: 'POST',
                body: {
                    board_key: board.key,
                    board_provider: board.provider,
                    board_name: board.name || 'Untitled board',
                    project_id: board.project_id || board.default_project_id || null
                }
            });
            const detail = await api(`/workflows/studio/plan-workspaces/${Number(workspace.id)}`);
            if (navigation !== state.navigation) return;
            state.workspace = detail;
            state.item = state.workspace.items?.[0] || null;
            state.mode = 'edit';
            await paintWorkspace();
            return true;
        } catch (error) {
            if (navigation === state.navigation)
                root.innerHTML = `<div class="plan-empty error">${escapeHtml(error.message || 'Could not open this plan.')}</div>`;
            return false;
        }
    }

    function syncCatalog(boards, projects) {
        state.boards = Array.isArray(boards) ? boards : state.boards;
        state.projects = Array.isArray(projects) ? projects : state.projects;
        if (state.view === 'home' && !root.querySelector('.plan-empty.error')) openHome(state.boards, state.projects);
    }

    return {
        openHome,
        openBoard,
        syncCatalog,
        beforeLeave,
        leave,
        hasUnsavedChanges: () => state.dirty || Boolean(state.savePromise)
    };
}
