import { parseWireframe, renderWireframe, explainWireframeDiagnostics } from './wireframe.js?v=20261125-artboard-continuity';
import { artboardWidthControls, bindArtboardWidth, mountPageDesigner, standalonePageHTML } from './page-designer.js?v=20261125-artboard-continuity';
import { endpointExample, HTTP_METHODS, openEndpointEditor, saveEndpointExample } from './endpoint-editor.js?v=20261125-canvas-workspace';
import { createPlanPolling } from './polling.js';
import { mountLibraryPanel } from './library-panel.js?v=20261126-library';
import { mountPlanPanelTabs, panelTabsHtml } from './panel-tabs.js?v=20261126-library';
import { buildResultsHtml } from './build-results.js';
import { layoutConnectedSitemap, pageAccessGroup, sitemapRouteKey, SITEMAP_ACCESS_GROUPS } from './sitemap.js?v=20261119-preview-routes';
import { pageCaptureFor } from './page-captures.js?v=20261003-sitemap-groups';
import { planningProviderLabel } from './routes.js';
import { createInlineEditor, addUnsavedCheck, isRevisionConflict } from './inline-edit/editor.js?v=20261103-ifaces3';
import { markdownToEditableHtml } from './inline-edit/markdown.js';
import { DOCUMENT_ORDER, orderDocuments, documentLabel, openDocument, moveTab, normaliseMode, isUserDocument } from './documents.js?v=20261103-ifaces3';
import { userDocumentsEnabled, USER_DOCUMENT_TYPE } from './documents-config.js?v=20261103-ifaces3';
import { mountErdItem, isErdMermaid, erdIsDirty } from './erd/mount.js?v=20261112-readable-data';
import { availableBottom, fillToWindow, createCallout, advancedButton, pinDock, openDrawer } from './canvas-chrome.js?v=20261103-ifaces3';
import { readSidecar, nodeIds, writeSidecar, slugId, ensureScreenId, updateNodeMeta } from './flow-meta.js?v=20261113-rebuild-map15';
import { buildIfaceModel, focusIfaceJourney, newScreenSource, SHAPE_KIND, LANES } from './iface-model.js?v=20261125-canvas';
import { createIfaceCanvas } from './iface-canvas.js?v=20261125-canvas-workspace';
import { mountPagePicker } from './page-picker.js';
import { decorateNode, nodeButton, openNodePopover, openNodePicker, closeNodePopover, warningsNotice } from './flow-nodes.js?v=20261113-rebuild-map15';
import { rememberedRoute, rememberRoute, routeStore } from './route-memory.js?v=20261102-verify';
import { chatHeaderHtml, chatStore, rememberChat, rememberedChat, pickActiveChat, validateImageFiles, rejectionMessage, isImageAsset, imageLabel } from './chat-header.js?v=20261113-rebuild-map15';
import { toolCardsHtml, draftsPayload } from './chat-tools.js?v=20261113-rebuild-map15';
import { animateDocumentEdit } from './doc-edit-anim.js?v=20261114-plan-docs9';

// Planning providers are the same for every board: fetch once per page, not per plan open.
let planningProviders = null;

const BASE = '/workflows/studio/plan-workspaces';
const DISCOVERED_PAGES = 'discovered';
const workspaceScope = (workspace) => JSON.stringify([workspace.id, workspace.project_id, workspace.board_key]);
const draftKey = (session, chatId) => `decisions.plan.draft.${session.scope}.${chatId || 'new'}`;
const TABS = {
    wireframes: { label: 'Interfaces', help: 'Page outlines, sitemap, and how screens fit together', types: ['wireframe', 'flows'] },
    erd: { label: 'Data', help: 'Entities and relationships (ERD / architecture)', types: ['erd', 'architecture', 'diagram'] },
    // Internal id stays 'requirements' (session.tab, data-plan-tab, data-hub-tab, saved selections); the UI says Documents.
    requirements: { label: 'Documents', help: 'FRAC, brief, PRD and other project documents', types: DOCUMENT_ORDER }
};


export function createPlanConversation(host, root) {
    const sessions = new Map();
    const esc = (value) => host.escapeHtml(String(value ?? ''));
    const api = (path, options) => host.api(path, options);
    let active = null;
    let mermaidReady;
    let diagramId = 0;
    const path = (session, suffix = '') => `${BASE}/${encodeURIComponent(session.id)}${suffix}`;
    // The conversation of the session's active chat (N3 multi-chat); no chat id = the server's newest live chat.
    const conversationPath = (session, chatId = session.chatId) => path(session, '/conversation') + (chatId ? `?chat_id=${encodeURIComponent(chatId)}` : '');
    const current = (session) => active === session && Boolean(root.querySelector('.plan-detail'));
    const tabStore = () => { try { return window.sessionStorage; } catch { return null; } };
    // Provenance ("Generated from the project · date") is metadata: shown as a tooltip, not as a line of content.
    const provenanceText = (item) => (item && !item.is_starter && item.origin_label)
        ? `${item.origin_label}${item.last_generated_at ? ` · ${new Date(`${item.last_generated_at}Z`).toLocaleString()}` : ''}` : '';
    // Collapsible plan chat: starts open; hiding it is remembered while this page stays open (in memory only,
    // plan UI state is never written to browser storage).
    let chatCollapseChoice = null;
    const storedChatCollapse = () => chatCollapseChoice;
    // Horizontally flipped Development sidebar collapse icon (rail on the right).
    const CHAT_ICON_COLLAPSE = '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M10 3v10"/><path d="M5.5 6l2 2-2 2"/></svg>';
    const CHAT_ICON_EXPAND = '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M10 3v10"/><path d="M7.5 6 5.5 8l2 2"/></svg>';

    function setChatCollapsed(session, collapsed, { remember = false } = {}) {
        session.chatCollapsed = Boolean(collapsed);
        if (remember) chatCollapseChoice = Boolean(collapsed);
        const body = root.querySelector('.plan-detail-body');
        const toggle = root.querySelector('#plan-chat-toggle');
        if (!body || !toggle) return;
        const was = body.classList.contains('plan-chat-collapsed');
        body.classList.toggle('plan-chat-collapsed', session.chatCollapsed);
        toggle.setAttribute('aria-expanded', String(!session.chatCollapsed));
        toggle.title = session.chatCollapsed ? 'Show conversation' : 'Hide conversation';
        toggle.setAttribute('aria-label', toggle.title);
        const icon = session.chatCollapsed ? CHAT_ICON_EXPAND : CHAT_ICON_COLLAPSE;
        const label = toggle.querySelector('.plan-chat-toggle-label');
        toggle.querySelectorAll('svg').forEach((node) => node.remove());
        toggle.insertAdjacentHTML('afterbegin', icon);
        if (label) toggle.append(label);
        if (!session.chatCollapsed) toggle.classList.remove('has-activity');
        if (was !== session.chatCollapsed) requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }
    const items = (session) => (session.tab === 'requirements' ? orderDocuments(session.workspace.items) : (session.workspace.items || []).filter((item) => TABS[session.tab].types.includes(item.item_type)));
    const selected = (session) => (session.tab === 'requirements' ? openDocument(items(session), session.selection.requirements)
        : session.tab === 'wireframes' && session.selection.wireframes === DISCOVERED_PAGES ? null
        : items(session).find((item) => String(item.id) === String(session.selection[session.tab])) || items(session)[0] || null);
    const blocked = (session) => !session.loaded || Boolean(session.pending) || ['running', 'pending', 'processing'].includes(session.conversation.status);
    const savedMessageStatuses = ['complete', 'needs_input', 'sync_pending'];
    const messageReceipt = (conversation, request) => request && conversation.messages?.find(message =>
        message.role === 'user' && message.request_id === request.id);
    const modelAvailable = (session) => (session.models || []).some(model => (typeof model === 'string' ? model : model.id || model.name) === session.model);
    const allStarters = (session) => {
        const rows = session.workspace.items || [];
        return rows.length > 0 && rows.every((item) => item.is_starter);
    };
    const isScaffoldPreview = (item) => {
        if (!item) return false;
        const content = String(item.content || item.body || '');
        return Boolean(
            item.is_starter
            || item.origin === 'scaffold'
            || /Primary screen/i.test(content)
            || /Describe the user journey/i.test(content)
        );
    };
    const triggerGeneratePlan = () => {
        const composer = document.getElementById('plan-composer-input')
            || document.querySelector('.plan-composer textarea, .plan-composer input, #plan-prompt, [data-plan-composer-input]');
        if (composer) {
            composer.focus();
            if ('value' in composer && !String(composer.value || '').trim()) {
                composer.value = 'Generate a plan from this project.';
                composer.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }
        document.querySelector('[data-plan-generate], #plan-generate, [data-plan-build], .plan-scan-generate')?.click();
    };

    function monitor(session) {
        session.polling?.stop();
        if (!current(session) || session.pending || !['running', 'pending', 'processing'].includes(session.conversation.status)) return;
        session.polling = createPlanPolling({
            active: () => current(session) && !session.pending,
            read: () => api(conversationPath(session)),
            apply: async (conversation, valid) => {
                const running = ['running', 'pending', 'processing'].includes(conversation.status);
                const changed = JSON.stringify(session.conversation) !== JSON.stringify(conversation);
                session.conversation = conversation;
                reconcileMessage(session);
                session.pollError = '';
                if (!running) {
                    if (!valid()) return false;
                    await applyCompletedTurn(session);
                    return running;
                }
                if (changed) refresh(session);
                else controls(session);
                return running;
            },
            onError: () => {
                session.pollError = 'Connection interrupted. Reconnecting...';
                controls(session);
            },
        });
        session.polling.start();
    }

    function safeUrl(value) {
        if (!value) return null;
        try {
            const url = new URL(value, window.location.href);
            return url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
        } catch { return null; }
    }

    function assetsHtml(assets) {
        return assets.map((asset) => {
            const url = safeUrl(asset.url);
            const preview = url && /^image\/(png|jpeg|gif|webp|avif|bmp|svg\+xml)$/i.test(asset.mime_type || '')
                ? `<img src="${esc(url)}" alt="" loading="lazy">` : '';
            return `<button type="button" class="plan-asset" data-view-asset="${esc(asset.id)}">${preview}<span>${esc(asset.name)}</span></button>`;
        }).join('');
    }

    function providerOptions(session) {
        const providers = [...(session.providers || [])];
        if (session.provider && !providers.some((provider) => provider.id === session.provider)) providers.unshift({ id: session.provider, name: session.provider });
        return '<option value="">Provider</option>' + providers.map((provider) => `<option value="${esc(provider.id)}"${provider.id === session.provider ? ' selected' : ''}>${esc(planningProviderLabel(provider, session.conversation.provider_routes))}</option>`).join('');
    }

    function modelOptions(session) {
        const choices = (session.models || []).map((model) => typeof model === 'string' ? { id: model, name: model } : { id: model.id || model.name, name: model.name || model.id });
        if (session.model && !choices.some((choice) => choice.id === session.model)) choices.unshift({ id: session.model, name: `${session.model} (unavailable)`, disabled: true });
        return '<option value="">Select model</option>' + choices.map((choice) => `<option value="${esc(choice.id)}"${choice.id === session.model ? ' selected' : ''}${choice.disabled ? ' disabled' : ''}>${esc(choice.name)}</option>`).join('');
    }

    async function loadModels(session) {
        session.models = [];
        if (session.provider) session.models = (await api(`/llms/models?type=planning&provider=${encodeURIComponent(session.provider)}`)).models || [];
        if (current(session)) root.querySelector('#plan-model').innerHTML = modelOptions(session);
    }

    // Mount the Plan sections in the shared header above the workspace.
    let bar = null;
    const planBar = () => bar || root;
    function mountBar() {
        const slot = host.planControlsSlot?.();
        const header = root.querySelector('.plan-detail-header');
        bar = null;
        if (!slot || !header) return;
        header.classList.add('plan-context-controls');
        slot.replaceChildren(header);
        bar = header;
        placePlanHeaderEnd();
    }
    function planHeaderEnd() {
        return document.querySelector('#development-task-header > .plan-header-end');
    }
    function placePlanHeaderEnd() {
        const headerEl = document.getElementById('development-task-header');
        const chrome = document.getElementById('board-chrome-unit');
        if (!headerEl || !chrome) return null;
        let end = planHeaderEnd();
        if (!end) {
            end = document.createElement('div');
            end.className = 'plan-header-end';
            chrome.before(end);
        }
        if (chrome.parentElement !== end) end.append(chrome);
        const build = document.getElementById('plan-build')?.closest('.plan-header-actions');
        const hint = document.querySelector('.plan-scan-hint');
        if (hint) end.insertBefore(hint, chrome);
        if (build) end.insertBefore(build, chrome);
        return end;
    }
    function restorePlanHeaderEnd() {
        const end = planHeaderEnd();
        const chrome = document.getElementById('board-chrome-unit');
        if (end && chrome) end.after(chrome);
        end?.remove();
    }

    // Interfaces actions live on the canvas (not the header). Held here so a preview re-render cannot drop them.
    let ifaceActions = null;
    let ifaceRebuild = null;

    // ⋯ menu (same .board-context-menu popover as the thread header menu). Fixed under its button.
    function closePlanMenu() {
        const menu = ifaceActions?.querySelector('#plan-header-menu');
        if (!menu || menu.classList.contains('hidden')) return;
        menu.classList.add('hidden');
        ifaceActions.querySelector('#plan-more-button')?.setAttribute('aria-expanded', 'false');
    }
    function closeRebuildMenu() {
        const menu = ifaceRebuild?.querySelector('#plan-rebuild-menu');
        if (!menu || menu.classList.contains('hidden')) return;
        menu.classList.add('hidden');
    }
    function openRebuildMenu(event) {
        const menu = ifaceRebuild?.querySelector('#plan-rebuild-menu');
        const button = ifaceRebuild?.querySelector('#plan-rebuild-sitemap');
        if (!menu || !button) return;
        menu.classList.remove('hidden');
        const width = menu.offsetWidth || 176;
        const height = menu.offsetHeight || 64;
        const x = event.clientX ?? button.getBoundingClientRect().left;
        const y = event.clientY ?? button.getBoundingClientRect().bottom;
        menu.style.position = 'fixed';
        menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
        menu.style.right = 'auto';
        menu.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
    }
    function togglePlanMenu() {
        const menu = ifaceActions?.querySelector('#plan-header-menu');
        const button = ifaceActions?.querySelector('#plan-more-button');
        if (!menu || !button) return;
        const opening = menu.classList.contains('hidden');
        if (opening) {
            const rect = button.getBoundingClientRect();
            menu.style.position = 'fixed';
            menu.style.left = 'auto';
            menu.style.top = `${Math.round(rect.bottom + 6)}px`;
            menu.style.right = `${Math.max(8, Math.round(window.innerWidth - rect.right))}px`;
        }
        menu.classList.toggle('hidden', !opening);
        button.setAttribute('aria-expanded', String(opening));
        if (opening) menu.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('click', (event) => {
            if (!event.target.closest?.('#plan-header-menu') && !event.target.closest?.('#plan-more-button')) closePlanMenu();
            if (!event.target.closest?.('#plan-rebuild-menu') && !event.target.closest?.('#plan-rebuild-sitemap')) closeRebuildMenu();
            if (!event.target.closest?.('#plan-doc-tab-menu')) closeDocTabMenu();
            if (!event.target.closest?.('#plan-chat-menu') && !event.target.closest?.('#plan-chat-switch')) {
                root.querySelector('#plan-chat-menu')?.classList.add('hidden');
                root.querySelector('#plan-chat-switch')?.setAttribute('aria-expanded', 'false');
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && document.getElementById('plan-doc-tab-menu')) { closeDocTabMenu(); return; }
            const menu = ifaceActions?.querySelector('#plan-header-menu');
            if (!menu || menu.classList.contains('hidden')) return;
            if (event.key === 'Escape') { closePlanMenu(); ifaceActions?.querySelector('#plan-more-button')?.focus(); return; }
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            const items = [...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
            if (!items.length) return;
            event.preventDefault();
            const at = items.indexOf(document.activeElement);
            items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
        });
    }

    // Interfaces: existing page / sitemap content on the gridded, window-filling canvas surface with the
    // summary call-out. The wire renderers below replace the preview's contents (innerHTML), so the call-out
    // is re-attached whenever it is wiped; the observer and fill-height go away with previewCleanup.
    function interfacesSurface(preview) {
        // The old summary call-out banner was removed (it repeated what the map already shows).
        preview.classList.add('plan-canvas-surface');
        const unfill = fillToWindow(preview);
        return () => { unfill?.(); };
    }

    // ---- Documents tab: tab bar (role=tablist), Editor / Markdown, Print PDF ------------------
    const DOC_MODE_KEY = 'decisions.plan.docMode';
    const docMode = () => { try { return normaliseMode(sessionStorage.getItem(DOC_MODE_KEY)); } catch { return 'inline'; } };
    const setDocMode = (mode) => { try { sessionStorage.setItem(DOC_MODE_KEY, normaliseMode(mode)); } catch { /* per view only */ } };
    function documentsBarHtml(session, docs, open) {
        const userDocs = userDocumentsEnabled();
        const mode = docMode();
        const tabs = docs.length ? docs.map((doc) => {
            const on = String(doc.id) === String(open?.id);
            const unsaved = Boolean(session.inlineDrafts?.[doc.id]);
            return `<button type="button" role="tab" class="plan-doc-tab" id="plan-doc-tab-${esc(doc.id)}" data-doc="${esc(doc.id)}" aria-selected="${on}" aria-controls="plan-preview" tabindex="${on ? 0 : -1}"${isUserDocument(doc) ? ' data-user-doc="true"' : ''}${unsaved ? ' data-unsaved="true"' : ''} title="${esc(doc.title || '')}">${esc(documentLabel(doc, docs))}<span class="plan-doc-dot" aria-hidden="true"></span><span class="visually-hidden plan-doc-draft-label">${unsaved ? ' (unsaved)' : ''}</span></button>`;
        }).join('') : '<button type="button" role="tab" class="plan-doc-tab" aria-selected="true" tabindex="0" aria-controls="plan-preview" disabled>FRAC</button>';
        const addTab = userDocs ? `<button type="button" role="tab" class="plan-doc-tab plan-doc-add" id="plan-doc-add" aria-label="Add document" title="Add document">+</button>` : '';
        return `<div class="plan-docs-bar">
            <div class="plan-doc-tabs" role="tablist" aria-label="Documents">${tabs}${addTab}</div>
            <span class="plan-docs-actions">
                <span class="plan-doc-modes" role="group" aria-label="Editor mode">
                    <button type="button" data-doc-mode="inline" aria-pressed="${mode === 'inline'}">Editor</button>
                    <button type="button" data-doc-mode="raw" aria-pressed="${mode === 'raw'}">Markdown</button>
                </span>
                <button type="button" class="header-button plan-doc-print" id="plan-doc-print" aria-label="Print PDF"${open ? '' : ' disabled'}>Print PDF</button>
            </span>
        </div>`;
    }
    function markDocTabs(session) {
        root.querySelectorAll('.plan-doc-tab[data-doc]').forEach((tab) => {
            const unsaved = Boolean(session.inlineDrafts?.[tab.dataset.doc]);
            if (unsaved) tab.dataset.unsaved = 'true'; else delete tab.dataset.unsaved;
            const label = tab.querySelector('.plan-doc-draft-label');
            if (label) label.textContent = unsaved ? ' (unsaved)' : '';
        });
    }
    function wireDocumentsBar(session, panel, docs, open) {
        const tabs = [...panel.querySelectorAll('.plan-doc-tab[data-doc]')];
        const select = (id) => { session.viewedAsset = null; session.selection.requirements = id; renderArtifacts(session); panel.querySelector(`.plan-doc-tab[data-doc="${CSS.escape(String(id))}"]`)?.focus(); };
        tabs.forEach((tab, i) => {
            tab.onclick = () => { if (tab.getAttribute('aria-selected') !== 'true') select(tab.dataset.doc); };
            tab.onkeydown = (event) => {
                const next = moveTab(tabs.length, i, event.key);
                if (next < 0) return;
                event.preventDefault();
                select(tabs[next].dataset.doc);        // automatic activation: drafts are kept per document
            };
        });
        panel.querySelector('.plan-doc-tab[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
        // Typing in the open editor updates the unsaved dot on its tab.
        panel.oninput = () => markDocTabs(session);
        panel.querySelectorAll('[data-doc-mode]').forEach((button) => {
            button.onclick = () => {
                if (button.getAttribute('aria-pressed') === 'true') return;
                setDocMode(button.dataset.docMode);
                renderArtifacts(session);
                root.querySelector(`[data-doc-mode="${button.dataset.docMode}"]`)?.focus();
            };
        });
        const print = panel.querySelector('#plan-doc-print');
        if (print && open) print.onclick = () => printDocument(session, open, documentLabel(open, docs));
        const add = panel.querySelector('#plan-doc-add');
        if (add) add.onclick = () => createNamedDocument(session, add);
        tabs.forEach((tab) => {
            if (!tab.hasAttribute('data-user-doc')) return;
            tab.oncontextmenu = (event) => {
                const doc = docs.find((item) => String(item.id) === String(tab.dataset.doc));
                if (doc) openDocTabMenu(event, session, doc);
            };
        });
    }
    function closeDocTabMenu() {
        document.getElementById('plan-doc-tab-menu')?.remove();
    }
    function openDocTabMenu(event, session, doc) {
        event.preventDefault();
        event.stopPropagation();
        closeDocTabMenu();
        const menu = document.createElement('div');
        menu.id = 'plan-doc-tab-menu';
        menu.className = 'board-context-menu plan-doc-tab-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', 'Document');
        menu.innerHTML = '<button type="button" role="menuitem" data-doc-menu="rename">Rename</button><button type="button" role="menuitem" class="danger-menu-item" data-doc-menu="delete">Delete</button>';
        document.body.append(menu);
        const width = menu.offsetWidth || 176;
        const height = menu.offsetHeight || 64;
        menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8))}px`;
        menu.querySelector('[data-doc-menu="rename"]').onclick = () => { closeDocTabMenu(); renameDocument(session, doc); };
        menu.querySelector('[data-doc-menu="delete"]').onclick = () => { closeDocTabMenu(); deleteDocument(session, doc); };
        menu.querySelector('button')?.focus({ preventScroll: true });
    }
    function promptDocumentName({ title = 'New document', value = '', confirmLabel = 'Create' } = {}) {
        closeDocTabMenu();
        document.getElementById('plan-doc-name-overlay')?.remove();
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.id = 'plan-doc-name-overlay';
            overlay.className = 'plan-doc-name-overlay';
            overlay.setAttribute('role', 'dialog');
            overlay.setAttribute('aria-modal', 'true');
            overlay.setAttribute('aria-labelledby', 'plan-doc-name-title');
            overlay.innerHTML = `<form class="plan-doc-name-modal"><h3 id="plan-doc-name-title">${esc(title)}</h3><input type="text" id="plan-doc-name-input" aria-label="Document name" value="${esc(value)}" autocomplete="off"><div class="plan-doc-name-actions"><button type="button" class="plan-doc-name-cancel">Cancel</button><button type="submit" class="plan-doc-name-ok">${esc(confirmLabel)}</button></div></form>`;
            document.body.append(overlay);
            const input = overlay.querySelector('#plan-doc-name-input');
            const ok = overlay.querySelector('.plan-doc-name-ok');
            const form = overlay.querySelector('form');
            let settled = false;
            const sync = () => { ok.disabled = !input.value.trim(); };
            const finish = (result) => {
                if (settled) return;
                settled = true;
                document.removeEventListener('keydown', onKey, true);
                overlay.remove();
                resolve(result);
            };
            const onKey = (event) => {
                if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(null); }
            };
            form.addEventListener('submit', (event) => {
                event.preventDefault();
                const name = input.value.trim();
                if (name) finish(name);
            });
            overlay.querySelector('.plan-doc-name-cancel').addEventListener('click', () => finish(null));
            overlay.addEventListener('click', (event) => { if (event.target === overlay) finish(null); });
            document.addEventListener('keydown', onKey, true);
            input.addEventListener('input', sync);
            sync();
            input.focus();
            input.select();
        });
    }
    async function createNamedDocument(session, add) {
        const title = await promptDocumentName({ title: 'New document', confirmLabel: 'Create' });
        if (!title) return;
        if (add) add.disabled = true;
        try {
            const created = await api(path(session, '/items'), { method: 'POST', body: { item_type: USER_DOCUMENT_TYPE, title, content: `# ${title}\n\n`, content_format: 'markdown' } });
            session.workspace.items = [...(session.workspace.items || []), created];
            session.selection.requirements = created.id;
            renderArtifacts(session);
        } catch (error) {
            host.toast?.(`Could not add the document: ${error?.message || 'unknown error'}`, 'error');
            if (add) add.disabled = false;
        }
    }
    // Delete = archive on the server (DELETE .../plan-items/{id}?expected_revision=N): only user documents,
    // history and the moved file are kept. Built-in documents never show the button and the route refuses them.
    async function deleteDocument(session, doc) {
        if (!userDocumentsEnabled() || !isUserDocument(doc)) return;
        const name = doc.title || 'this document';
        const unsaved = session.inlineDrafts?.[doc.id] ? ' Your unsaved changes to it will be lost.' : '';
        const ok = Boolean(await window.DecisionsAPI?.confirm?.({ title: 'Delete document', message: `Delete "${name}"? It is removed from Documents; its history is kept.${unsaved}`, confirmLabel: 'Delete', danger: true }));
        if (!ok) return;
        try {
            await api(`${path(session, `/plan-items/${encodeURIComponent(doc.id)}`)}?expected_revision=${encodeURIComponent(revisionOf(doc) ?? '')}`, { method: 'DELETE' });
        } catch (error) {
            const message = error?.status === 409
                ? 'This document has a newer revision. Review the newer version before deleting it.'
                : `Could not delete the document: ${error?.message || 'unknown error'}`;
            host.toast?.(message, 'error');
            return;
        }
        session.workspace.items = (session.workspace.items || []).filter((item) => String(item.id) !== String(doc.id));
        if (session.inlineDrafts) delete session.inlineDrafts[doc.id];
        if (String(session.selection.requirements) === String(doc.id)) {
            const frac = (session.workspace.items || []).find((item) => item.item_type === 'frac');
            session.selection.requirements = frac ? frac.id : null;
        }
        renderArtifacts(session);
        root.querySelector('.plan-doc-tab[aria-selected="true"]')?.focus();
    }
    // Rename = the existing PATCH with title + expected_revision (a title change is a new revision, so it
    // waits until the document has no unsaved draft).
    async function renameDocument(session, doc) {
        if (!userDocumentsEnabled() || !isUserDocument(doc)) return;
        if (session.inlineDrafts?.[doc.id]) { host.toast?.('Save or undo your changes to this document before renaming it.', 'error'); return; }
        const title = await promptDocumentName({ title: 'Rename document', value: doc.title || '', confirmLabel: 'Rename' });
        if (!title || title === doc.title) {
            root.querySelector(`.plan-doc-tab[data-doc="${CSS.escape(String(doc.id))}"]`)?.focus();
            return;
        }
        try {
            const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(doc.id)}`, { method: 'PATCH', body: { title, expected_revision: revisionOf(doc) } });
            Object.assign(doc, updated);
        } catch (error) { host.toast?.(`Could not rename: ${error?.message || 'unknown error'}`, 'error'); }
        renderArtifacts(session);
        root.querySelector(`.plan-doc-tab[data-doc="${CSS.escape(String(doc.id))}"]`)?.focus();
    }
    // Print PDF: a print-only container with the open document (its unsaved draft if any) rendered from
    // markdown; @media print hides all app chrome (styles.css "Documents print"). No PDF export route exists.
    function printDocument(session, doc, label) {
        document.getElementById('plan-print-root')?.remove();
        const content = session.inlineDrafts?.[doc.id]?.draft ?? doc.content ?? '';
        const node = document.createElement('article');
        node.id = 'plan-print-root';
        node.className = 'plan-print-root message-markdown';
        node.setAttribute('aria-hidden', 'true');
        node.innerHTML = `<header class="plan-print-head"><span>${esc(session.workspace.board_name || '')}</span><span>${esc(label)}</span></header>${markdownToEditableHtml(content).html}`;
        node.querySelectorAll('[contenteditable]').forEach((n) => n.removeAttribute('contenteditable'));
        document.body.append(node);
        document.body.classList.add('plan-printing');
        const cleanup = () => { node.remove(); document.body.classList.remove('plan-printing'); window.removeEventListener('afterprint', cleanup); };
        window.addEventListener('afterprint', cleanup);
        window.print();
    }

    function shell(session) {
        bar?.remove();
        const staleEnd = planHeaderEnd();
        if (staleEnd) [...staleEnd.children].forEach((el) => { if (el.id !== 'board-chrome-unit') el.remove(); });
        root.innerHTML = `<div class="plan-detail">
            <header class="plan-detail-header">
                <strong class="plan-project" title="${esc(session.workspace.board_name)}">${esc(session.workspace.board_name)}</strong>
                <nav class="plan-tabs" aria-label="Plan sections">${Object.entries(TABS).map(([tab, info]) => `<button type="button" data-plan-tab="${tab}" title="${esc(info.help || info.label)}" aria-pressed="${session.tab === tab}">${info.label}</button>`).join('')}</nav>
            </header>
            <div class="plan-detail-body">
                <section class="plan-artifacts" id="plan-artifacts" aria-label="Artifacts"></section>
                <section class="plan-conversation" id="plan-conversation" aria-label="Plan conversation">
                    <header class="plan-chat-header" id="plan-chat-header" aria-label="Conversation"><button type="button" class="plan-chat-toggle" id="plan-chat-toggle" aria-controls="plan-conversation" aria-expanded="true" title="Hide conversation" aria-label="Hide conversation">${CHAT_ICON_COLLAPSE}<span class="plan-chat-toggle-label">Conversation</span></button><nav class="plan-panel-tabs" id="plan-panel-tabs" role="tablist" aria-label="Chat or library">${panelTabsHtml('chat')}</nav></header>
                    <div class="plan-chat-body" id="plan-chat-body">
                    <div class="composer-drop-overlay plan-drop-overlay" id="plan-drop-overlay" hidden><strong>Drop images to attach</strong><span>PNG, JPEG or WebP, up to 10 MB each</span></div>
                    <div class="plan-messages" id="plan-messages" role="log" aria-label="Messages" aria-live="polite"></div>
                    <div id="plan-operation-status" role="status" aria-live="polite"></div>
                    <div class="plan-composer-wrap">
                        <form class="studio-composer plan-language" id="plan-language">
                            <div id="plan-draft-assets" class="composer-context"></div>
                            <textarea id="plan-language-input" rows="2" placeholder="Ask about pages, data, or what to build." aria-label="Message"></textarea>
                            <div class="composer-toolbar">
                                <button type="button" class="add-button" id="plan-attach" aria-label="Attach files"><span aria-hidden="true">+</span></button>
                                <input type="file" id="plan-attachment" hidden multiple>
                                <div class="composer-route">
                                    <select id="plan-provider" aria-label="Provider">${providerOptions(session)}</select>
                                    <select id="plan-model" aria-label="Model">${modelOptions(session)}</select>
                                    <button type="submit" class="send-button" id="plan-send" aria-label="Send message">↑</button>
                                </div>
                            </div>
                        </form>
                    </div>
                    </div>
                    <section class="plan-library" id="plan-library" role="tabpanel" aria-label="Library" hidden></section>
                </section>
            </div>
        </div>`;
        root.querySelector('#plan-chat-toggle').addEventListener('click', () => {
            setChatCollapsed(session, !session.chatCollapsed, { remember: true });
            if (!session.chatCollapsed) root.querySelector('#plan-language-input')?.focus({ preventScroll: true });
        });
        setChatCollapsed(session, storedChatCollapse() ?? session.chatCollapsed ?? false);

        // Chat / Library tabs. The library's contents follow the screen: page
        // templates at sitemap level, widgets once a page is open. Mounted once
        // per render; the panel is not rebuilt on a tab switch, only hidden, so a
        // half-typed message survives.
        try {
            const libraryHost = root.querySelector('#plan-library');
            const libraryPanel = libraryHost
                ? mountLibraryPanel(libraryHost, {
                    workspaceId: session.workspace && session.workspace.id,
                    baseUrl: '/api',
                    screen: 'page',
                    onPick: (entry) => {
                        session.pendingLibraryEntry = entry;
                        root.dispatchEvent(new CustomEvent('plan-library-pick', { detail: entry, bubbles: true }));
                    },
                })
                : null;
            if (libraryPanel) {
                session.planPanelTabs = mountPlanPanelTabs(root.querySelector('#plan-panel-tabs') || root, {
                    session,
                    onLibrary: (screen) => { libraryPanel.setScreen(screen); },
                });
                // Consume the pick: place the entry in the plan and persist it into
                // the project's planning folder. At sitemap level a page template
                // creates a page; inside a page the canvas names the target page
                // (session.libraryPage) because only it knows which page is open.
                root.addEventListener('plan-library-pick', async (event) => {
                    const entry = (event && event.detail) || {};
                    if (!entry.name) return;
                    const screen = libraryPanel.getScreen();
                    const body = screen === 'sitemap'
                        ? { entry: entry.name, title: entry.title || '' }
                        : { entry: entry.name, page: session.libraryPage || '' };
                    try {
                        const result = await api(path(session, '/plan/place'), { method: 'POST', body });
                        if (!result || result.placed === false) {
                            console.warn('[plan] could not place the entry:', (result && result.error) || 'unknown');
                            return;
                        }
                        session.planDirty = true;
                        root.dispatchEvent(new CustomEvent('plan-changed', { detail: result, bubbles: true }));
                        controls(session);
                    } catch (err) {
                        console.warn('[plan] place failed', err);
                    }
                });
            }
        } catch (err) {
            console.warn('[plan] library panel unavailable', err);
        }
        const input = root.querySelector('#plan-language-input');
        input.value = session.draft;
        input.addEventListener('input', () => { session.draft = input.value; session.draftVersion++; controls(session); });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                input.form.requestSubmit();
            }
        });
        root.querySelector('#plan-language').addEventListener('submit', (event) => { event.preventDefault(); send(session); });
        mountBar();
        ifaceRebuild = document.createElement('div');
        ifaceRebuild.className = 'plan-iface-rebuild';
        ifaceRebuild.setAttribute('role', 'group');
        ifaceRebuild.setAttribute('aria-label', 'Rebuild interfaces');
        ifaceRebuild.innerHTML = `<button type="button" class="header-button" id="plan-rebuild-sitemap">Rebuild</button><div class="board-context-menu plan-rebuild-menu hidden" id="plan-rebuild-menu" role="menu" aria-label="Rebuild"><button type="button" role="menuitem" data-rebuild-action="rebuild">Rebuild</button><button type="button" role="menuitem" data-rebuild-action="rescan">Rescan images</button></div>`;
        const rebuildBtn = ifaceRebuild.querySelector('#plan-rebuild-sitemap');
        rebuildBtn.onclick = () => { closePlanMenu(); closeRebuildMenu(); rebuildSitemap(session); };
        rebuildBtn.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            closePlanMenu();
            openRebuildMenu(event);
        });
        ifaceRebuild.querySelector('[data-rebuild-action="rebuild"]').onclick = () => { closeRebuildMenu(); rebuildSitemap(session); };
        ifaceRebuild.querySelector('[data-rebuild-action="rescan"]').onclick = () => { closeRebuildMenu(); rescanImages(session); };
        ifaceActions = document.createElement('div');
        ifaceActions.className = 'plan-iface-actions';
        ifaceActions.id = 'plan-header-actions';
        ifaceActions.setAttribute('role', 'group');
        ifaceActions.setAttribute('aria-label', 'Interface link filters');
        ifaceActions.innerHTML = `<button type="button" class="iface-all-links" id="plan-all-links" aria-pressed="false" title="Show all links" aria-label="Show all links"><svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l1.83-1.83a5 5 0 0 0-7.07-7.07L10.7 6.24"/><path d="M14 11a5 5 0 0 0-7.54-.54L4.63 12.3a5 5 0 0 0 7.07 7.07L13.3 17.76"/></svg></button>`;
        // Plan -> gaps -> tickets (restored 12 Nov 2026): the existing build turn reads the plan and the sitemap's
        // outstanding pages, asks when a decision is open, and creates or reuses tickets on this project's board.
        const buildActions = document.createElement('span');
        buildActions.className = 'plan-header-actions';
        buildActions.innerHTML = `<button type="button" class="header-button primary" id="plan-build">Build tickets</button>`;
        (bar || root.querySelector('.plan-detail-header'))?.append(buildActions);
        document.getElementById('plan-build').onclick = () => { closePlanMenu(); build(session); };
        placePlanHeaderEnd();
        root.querySelector('#plan-provider').onchange = async (event) => {
            session.provider = event.target.value;
            session.model = '';
            rememberRoute(routeStore(), session.id, { provider: session.provider, model: '' });
            session.pending = 'Loading models...';
            session.error = '';
            root.querySelector('#plan-model').innerHTML = '<option value="">Select model</option>';
            controls(session);
            try { await loadModels(session); }
            catch (error) { session.error = error.message || 'Could not load models. Select the provider again to retry.'; }
            finally { session.pending = ''; controls(session); }
        };
        root.querySelector('#plan-model').onchange = (event) => {
            session.model = event.target.value;
            rememberRoute(routeStore(), session.id, { provider: session.provider, model: session.model });
            controls(session);
        };
        wireImageDrop(session, root.querySelector('.plan-conversation'), input);
        root.querySelector('#plan-attach').onclick = () => root.querySelector('#plan-attachment').click();
        root.querySelector('#plan-attachment').onchange = (event) => {
            const files = [...event.target.files];
            event.target.value = '';
            if (files.length && root.querySelector('[data-canvas-workspace]:not([data-show-project-panels])')) {
                root.querySelector('[data-project-panels]')?.click();
            }
            upload(session, files);
        };
        planBar().querySelectorAll('[data-plan-tab]').forEach((button) => {
            button.onclick = () => {
                session.tab = button.dataset.planTab;
                session.viewedAsset = null;
                planBar().querySelectorAll('[data-plan-tab]').forEach((tab) => tab.setAttribute('aria-pressed', String(tab === button)));
                button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                renderArtifacts(session);
            };
        });
        refresh(session);
    }

    function syncRebuildButton(session) {
        const rebuild = ifaceRebuild?.querySelector('#plan-rebuild-sitemap') || root.querySelector('#plan-rebuild-sitemap');
        if (!rebuild) return;
        const rescanning = session.rescanningImages;
        rebuild.textContent = rescanning ? 'Rescanning' : (session.rebuildingInterfaces ? 'Rebuilding' : 'Rebuild');
        rebuild.disabled = blocked(session) || session.rebuildingInterfaces || rescanning || !modelAvailable(session);
        ifaceRebuild?.querySelectorAll('#plan-rebuild-menu [role="menuitem"]').forEach((item) => {
            item.disabled = rebuild.disabled;
        });
    }

    function controls(session) {
        if (!current(session)) return;
        if (session.loaded) saveDraft(session);
        if (!Object.keys(session.inlineDrafts || {}).length && !erdIsDirty(session)) session.planSaveWarning = '';
        const busy = blocked(session);
        root.querySelector('#plan-send').disabled = busy || !modelAvailable(session) || !session.draft.trim();
        const buildButton = document.getElementById('plan-build');
        syncRebuildButton(session);
        if (buildButton) buildButton.disabled = busy || !modelAvailable(session);
        const resetZoom = root.querySelector('[data-iface="reset-zoom"]');
        if (resetZoom) resetZoom.disabled = busy;
        root.querySelector('#plan-model').disabled = busy;
        root.querySelector('#plan-provider').disabled = busy;
        root.querySelector('#plan-attach').disabled = busy;
        root.querySelectorAll('#plan-chat-header button').forEach((button) => {
            if (button.dataset.chatSwitch !== undefined || button.id === 'plan-chat-switch') button.disabled = busy;
            else button.disabled = busy || button.dataset.staticDisabled === 'true';
        });
        root.querySelector('#plan-chat-toggle')?.classList.toggle('has-alert', Boolean(session.error || session.planSaveWarning));
        renderOperationStatus(session);
    }

    // Errors, outcomes and the ticket jump are shown in the status line above the composer,
    // so a failed send, rebuild, upload or ticket build is never only a dot on the chat toggle.
    function renderOperationStatus(session) {
        const status = root.querySelector('#plan-operation-status');
        if (!status) return;
        status.replaceChildren();
        if (session.error || session.planSaveWarning) {
            const error = document.createElement('p');
            error.className = 'plan-error';
            error.setAttribute('role', 'alert');
            error.textContent = session.error || session.planSaveWarning;
            status.append(error);
        }
        const working = ['running', 'pending', 'processing'].includes(session.conversation?.status) ? 'Assistant is working...' : '';
        const note = session.pollError || (!session.loaded && !session.error ? 'Loading conversation...' : '') || session.outcome || (session.streamText == null ? working : '');
        if (note) {
            const text = document.createElement('p');
            text.textContent = String(note);
            status.append(text);
        }
        const latestBuild = Array.from(root.querySelectorAll('#plan-messages .plan-build-results')).at(-1);
        const firstTicket = latestBuild?.querySelector('[data-plan-ticket]');
        if (firstTicket && !session.pending) {
            const jump = document.createElement('button');
            jump.type = 'button';
            jump.className = 'plan-ticket-jump';
            jump.textContent = 'View generated tickets';
            jump.onclick = () => {
                firstTicket.focus({ preventScroll: true });
                firstTicket.scrollIntoView({ block: 'center', inline: 'nearest' });
            };
            status.append(jump);
        }
        if (!session.pending && (session.error || !session.loaded)) {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.textContent = 'Refresh conversation';
            retry.onclick = () => load(session);
            status.append(retry);
        }
    }

    function refresh(session) {
        if (!current(session)) return;
        const log = root.querySelector('#plan-messages');
        const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 64;
        let compactedShown = false;
        renderChatHeader(session);
        log.innerHTML = (session.conversation.messages || []).map((message) => {
            const isAssistant = message.role === 'assistant';
            const assistantIdentity = window.DecisionsAssistantIdentity?.html() || '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
            const author = isAssistant
                ? `<strong class="plan-message-author"><span class="plan-message-avatar" data-assistant-identity>${assistantIdentity}</span><span data-assistant-name>${esc(window.DecisionsAssistantIdentity?.name() || 'Assistant')}</span></strong>`
                : `<strong>${esc(message.role === 'user' ? 'You' : message.role)}</strong>`;
            const images = (message.attachment_ids || []).map((id) => sessionAssets(session).find((asset) => String(asset.id) === String(id))).filter(isImageAsset);
            const thumbs = images.length ? `<div class="plan-message-images">${images.map((asset) => {
                const url = safeUrl(asset.url);
                return url ? `<figure class="plan-message-image"><img src="${esc(url)}" alt="${esc(asset.name)}" loading="lazy"><figcaption>${esc(imageLabel(asset))}</figcaption></figure>` : '';
            }).join('')}</div>` : '';
            const divider = message.compacted && !compactedShown ? (compactedShown = true, '<p class="plan-compacted-note">Earlier turns are summarised for the assistant (compacted). They stay visible here.</p>') : '';
            return `${divider}<article class="plan-message${message.role === 'user' ? ' plan-message-user' : ''}${message.compacted ? ' plan-message-compacted' : ''}">${author}<div class="plan-message-content">${esc(message.content)}</div>${thumbs}${(message.artifacts || []).map((id) => {
            const item = (session.workspace.items || []).find((item) => String(item.id) === String(id));
            return item ? `<button type="button" data-message-artifact="${esc(item.id)}">${esc(item.title)}</button>` : '';
        }).join('')}${isAssistant ? toolCardsHtml(message, session.workspace.items, esc, session.dismissedCards || new Set()) : ''}${isAssistant && message.memory_used ? `<p class="plan-memory-hint">Used ${esc(message.memory_used)} note${message.memory_used === 1 ? '' : 's'} from earlier chats</p>` : ''}${isAssistant ? buildResultsHtml(message.build_result, esc, { boardKey: session.workspace.board_key }) : ''}</article>`;
        }).join('');
        wireToolCards(session, log);
        const messageCount = (session.conversation.messages || []).length;
        if (session.pending && session.chatCollapsed) setChatCollapsed(session, false);
        // Chat starts open; it collapses only when the person hides it (remembered per browser).
        if (session.chatCollapsed && messageCount > (session.chatSeenCount ?? messageCount)) root.querySelector('#plan-chat-toggle')?.classList.add('has-activity');
        session.chatSeenCount = messageCount;
        if (host.openPlanTicket) log.querySelectorAll('[data-plan-ticket]').forEach((link) => {
            link.onclick = async (event) => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                if (link.getAttribute('aria-busy') === 'true') return;
                const status = document.createElement('span');
                status.className = 'plan-task-link-status';
                status.setAttribute('role', 'status');
                link.parentElement.querySelector('.plan-task-link-status')?.remove();
                link.after(status);
                status.textContent = 'Opening task...';
                status.scrollIntoView({ block: 'nearest' });
                link.setAttribute('aria-busy', 'true');
                try {
                    await host.openPlanTicket(session.workspace.board_key, Number(link.dataset.planTicket));
                    status.remove();
                } catch (error) {
                    status.setAttribute('role', 'alert');
                    status.textContent = error.message || 'Could not open this task. Try again.';
                    if (status.isConnected) status.scrollIntoView({ block: 'nearest' });
                } finally { link.removeAttribute('aria-busy'); }
            };
        });
        log.querySelectorAll('[data-message-artifact]').forEach((button) => {
            button.onclick = () => {
                const item = session.workspace.items.find((item) => String(item.id) === button.dataset.messageArtifact);
                const tab = Object.keys(TABS).find((tab) => TABS[tab].types.includes(item.item_type));
                if (!tab) return;
                session.selection[tab] = item.id;
                planBar().querySelector(`[data-plan-tab="${tab}"]`).click();
            };
        });
        const drafts = root.querySelector('#plan-draft-assets');
        drafts.innerHTML = session.attachments.map((asset) => `<span class="context-chip">${isImageAsset(asset) && safeUrl(asset.url) ? `<img class="context-chip-preview" src="${esc(safeUrl(asset.url))}" alt="">` : ''}<span title="${esc(asset.name)}">${esc(isImageAsset(asset) ? `${imageLabel(asset)} · ${asset.name}` : asset.name)}</span><button type="button" data-remove-asset="${esc(asset.id)}" aria-label="Remove ${esc(asset.name)}">Remove</button></span>`).join('');
        drafts.querySelectorAll('[data-remove-asset]').forEach((button) => {
            button.disabled = Boolean(session.pending);
            button.onclick = () => { if (session.pending) return; session.attachments = session.attachments.filter((asset) => String(asset.id) !== button.dataset.removeAsset); refresh(session); };
        });
        controls(session);
        renderArtifacts(session);
        // Status/composer height can change during a turn. Follow only after
        // those layout changes, and preserve position when reading older text.
        if (session.streamText != null) mountStreamBubble(session, log, nearBottom);
        if (nearBottom) log.scrollTop = log.scrollHeight;
    }

    function followSubmittedTurn(session) {
        if (!current(session)) return;
        const log = root.querySelector('#plan-messages');
        log.scrollTop = log.scrollHeight;
    }

    function streamMarkdown(text) {
        try {
            return markdownToEditableHtml(text).html || esc(text);
        } catch {
            return esc(text);
        }
    }

    function findStreamBubble(log) {
        return log.querySelector('[data-plan-stream], [data-plan-thinking], .plan-thinking, .plan-message-thinking');
    }

    function atMessageBottom(log) {
        return Boolean(log) && log.scrollHeight - log.scrollTop - log.clientHeight < 64;
    }

    function mountStreamBubble(session, log, nearBottom) {
        if (!log) return null;
        let article = findStreamBubble(log);
        if (!article) {
            const assistantIdentity = window.DecisionsAssistantIdentity?.html() || '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
            const name = esc(window.DecisionsAssistantIdentity?.name() || 'Assistant');
            article = document.createElement('article');
            article.className = 'plan-message plan-message-stream';
            article.dataset.planStream = '1';
            article.innerHTML = `<strong class="plan-message-author"><span class="plan-message-avatar" data-assistant-identity>${assistantIdentity}</span><span data-assistant-name>${name}</span></strong><div class="plan-message-content" data-plan-stream-content></div>`;
            log.append(article);
        } else {
            article.dataset.planStream = '1';
            if (!article.querySelector('[data-plan-stream-content], .plan-message-content')) {
                const content = document.createElement('div');
                content.className = 'plan-message-content';
                content.dataset.planStreamContent = '1';
                article.append(content);
            }
        }
        paintStream(session, article, nearBottom);
        return article;
    }

    function paintStream(session, article, nearBottom) {
        const log = root.querySelector('#plan-messages');
        const content = article.querySelector('[data-plan-stream-content], .plan-message-content') || article;
        const text = session.streamText || '';
        content.innerHTML = text ? streamMarkdown(text) : '<span class="plan-stream-waiting">Thinking</span>';
        if (nearBottom ?? atMessageBottom(log)) log.scrollTop = log.scrollHeight;
    }

    function scheduleStreamPaint(session) {
        if (session.streamPaint) return;
        session.streamPaint = setTimeout(() => {
            session.streamPaint = 0;
            if (!current(session) || session.streamText == null) return;
            const log = root.querySelector('#plan-messages');
            const article = mountStreamBubble(session, log);
            if (article) paintStream(session, article);
        }, 40);
    }

    function stopStreamPaint(session) {
        if (session.streamPaint) {
            clearTimeout(session.streamPaint);
            session.streamPaint = 0;
        }
        session.streamText = null;
    }

    async function readSseEvents(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            const parts = buffer.split('\n\n');
            buffer = done ? '' : parts.pop();
            for (const block of parts) {
                let event = 'message';
                const data = [];
                for (const line of block.split('\n')) {
                    if (line.startsWith('event:')) event = line.slice(6).trim();
                    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
                }
                if (data.length) await onEvent(event, data.join('\n'));
            }
            if (done) break;
        }
    }

    async function postPlanMessage(session, body) {
        const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
        const token = document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';
        if (token) headers['X-DecisionsAI-Internal-Token'] = token;
        const response = await fetch(`/api${path(session, '/messages/stream')}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
        });
        const type = response.headers.get('content-type') || '';
        if (response.ok && type.includes('text/event-stream') && response.body) {
            let conversation = null;
            let streamError = '';
            await readSseEvents(response, async (event, raw) => {
                let payload = {};
                try { payload = raw ? JSON.parse(raw) : {}; } catch { payload = { detail: raw }; }
                if (event === 'delta' && payload.text) {
                    session.streamText = (session.streamText || '') + payload.text;
                    scheduleStreamPaint(session);
                } else if (event === 'done') {
                    conversation = payload;
                    session.conversation = payload;
                    // Reload artifacts as soon as `done` arrives. Do not wait for the
                    // SSE stream to close — a hung reader would otherwise leave the
                    // open document on stale text after the server has already saved.
                    // Do not await here: a slow workspace GET must not stall the reader.
                    session._completedTurn = applyCompletedTurn(session);
                } else if (event === 'error') {
                    streamError = payload.detail || 'Message failed. Your draft is preserved.';
                }
            });
            stopStreamPaint(session);
            if (streamError) throw new Error(typeof streamError === 'string' ? streamError : 'Message failed. Your draft is preserved.');
            if (!conversation) throw new Error('Message failed. Your draft is preserved.');
            return conversation;
        }
        if (response.status === 404 || response.status === 405 || !type.includes('text/event-stream')) {
            return api(path(session, '/messages'), { method: 'POST', body });
        }
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
        throw new Error(data.detail || data.error || `Request failed (${response.status})`);
    }

    async function renderDiagram(target, content) {
        try {
            if (!mermaidReady) mermaidReady = (async () => {
                if (!window.mermaid) await new Promise((resolve, reject) => {
                    const script = document.createElement('script');
                    script.src = '/static/vendor/mermaid/mermaid.min.js';
                    script.onload = resolve;
                    script.onerror = () => { script.remove(); reject(new Error('Could not load diagram renderer.')); };
                    document.head.append(script);
                });
                window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' });
            })().catch((error) => { mermaidReady = null; throw error; });
            await mermaidReady;
            if (!target.isConnected) return;
            const result = await window.mermaid.render(`plan-diagram-${++diagramId}`, content);
            if (target.isConnected) target.innerHTML = result.svg;
        } catch (error) {
            if (target.isConnected) target.textContent = error.message || 'Could not render diagram.';
        }
    }


    function openConnectedPage(session, item, screen, preview) {
        if (!screen) return;
        session.sitemapPositions ||= {};
        session.sitemapPositions[item.id] = { left: preview?.scrollLeft || 0, top: preview?.scrollTop || 0 };
        session.sitemap = false;
        session.pages[item.id] = screen.id;
        session.pageLayers ||= {};
        session.pageLayers[item.id] = 'preview';
        renderArtifacts(session);
    }

    function appendPageCapture(target, shot) {
        if (shot?.image) {
            const img = document.createElement('img');
            img.className = 'plan-sitemap-shot';
            img.alt = '';
            img.decoding = 'async';
            img.src = shot.image;
            target.append(img);
            return;
        }
        const blank = document.createElement('span');
        blank.className = 'plan-sitemap-shot plan-sitemap-shot-missing';
        blank.setAttribute('aria-hidden', 'true');
        if (shot?.reason) blank.title = shot.reason;
        target.append(blank);
    }

    function appendPageStencil(target, screen) {
        const stencil = document.createElement('span');
        stencil.className = 'plan-sitemap-stencil';
        stencil.setAttribute('aria-hidden', 'true');
        const flatten = [];
        const walk = (node, depth = 0) => {
            for (const child of node?.children || []) {
                if (flatten.length >= 12) return;
                flatten.push({ type: child.type || 'text', depth });
                if (depth < 3) walk(child, depth + 1);
            }
        };
        walk(screen);
        if (!flatten.length) flatten.push({ type: 'text', depth: 0 }, { type: 'text', depth: 0 }, { type: 'button', depth: 0 });
        for (const block of flatten) {
            const mark = document.createElement('i');
            mark.dataset.type = block.type;
            mark.dataset.depth = String(block.depth);
            stencil.append(mark);
        }
        target.append(stencil);
    }

    const OPEN_MARK = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.25" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
    const LOCK_MARK = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.25" y="7" width="9.5" height="6.25" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 7V5.1a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';

    function appendAccessMark(target, access) {
        const mark = document.createElement('span');
        mark.className = 'plan-sitemap-access';
        const open = access === 'public';
        mark.dataset.open = open ? 'true' : 'false';
        mark.title = open ? 'Open' : 'Needs a login';
        mark.innerHTML = open ? OPEN_MARK : LOCK_MARK;
        target.append(mark);
    }

    function appendSitemapCard(parent, node, shot, access, quiet, decorate = null) {
        const el = document.createElement('div');
        el.className = quiet ? 'plan-sitemap-node plan-sitemap-node-quiet' : 'plan-sitemap-node';
        el.dataset.kind = node.kind || 'page';
        el.dataset.status = node.status || '';
        el.dataset.access = access;
        appendAccessMark(el, access);
        if (!quiet) {
            if (shot) appendPageCapture(el, shot);
            else appendPageStencil(el, null);
        }
        const name = document.createElement('strong');
        name.textContent = node.label;
        const meta = document.createElement('small');
        meta.textContent = node.route;
        el.append(name, meta);
        decorate?.(el, node);
        parent.append(el);
    }

    function renderAccessGroups(preview, layout, captures, decorate = null) {
        const wrap = document.createElement('div');
        wrap.className = 'plan-sitemap-wrap plan-sitemap-groups';
        const buckets = new Map(SITEMAP_ACCESS_GROUPS.map(group => [group.id, []]));
        for (const node of layout.nodes) {
            const shot = captures.pages[node.id] || null;
            const access = pageAccessGroup(node, shot);
            (buckets.get(access) || buckets.get('public')).push({ node, shot, access });
        }
        for (const group of SITEMAP_ACCESS_GROUPS) {
            const rows = buckets.get(group.id) || [];
            const section = document.createElement('section');
            section.className = 'plan-sitemap-group';
            section.dataset.access = group.id;
            const heading = document.createElement('h3');
            heading.textContent = group.label;
            section.append(heading);
            const cards = document.createElement('div');
            cards.className = 'plan-sitemap-cards';
            const quiet = document.createElement('div');
            quiet.className = 'plan-sitemap-quiet';
            const keepLarge = new Set(['/', '/catalogue', '/register', '/activate', '/forgot-password', '/reset-password', '/change-password', '/change-email']);
            const ordered = rows.slice().sort((a, b) => a.node.label.localeCompare(b.node.label) || a.node.route.localeCompare(b.node.route));
            for (const row of ordered) {
                const hidden = Boolean(row.shot?.notFound) && !keepLarge.has(row.node.route);
                appendSitemapCard(hidden ? quiet : cards, row.node, row.shot, row.access, hidden, hidden ? null : decorate);
            }
            if (cards.childNodes.length) section.append(cards);
            if (quiet.childNodes.length) {
                const note = document.createElement('p');
                note.className = 'plan-sitemap-quiet-label';
                note.textContent = 'These routes open the site\u2019s not-found page.';
                section.append(note, quiet);
            }
            wrap.append(section);
        }
        preview.append(wrap);
    }

    function renderProjectSitemap(preview, session, screens, item) {
        const model = session.workspace?.sitemap || {
            coverage: 'missing',
            note: 'Sitemap cache is not available in this session yet. It is built from HTML templates, views, and URL maps, not from React routes.',
            nodes: [],
            edges: [],
            scaffolding: [],
        };
        const layout = layoutConnectedSitemap(model, screens);
        const captures = pageCaptureFor(session.workspace);
        const byScreen = new Map(screens.map(screen => [String(screen.id), screen]));
        const decorate = (el, node) => decorateScreenNode(session, item, el, byScreen.get(node.screenId));
        if (captures) {
            renderAccessGroups(preview, layout, captures, decorate);
            return;
        }
        const wrap = document.createElement('div');
        wrap.className = 'plan-sitemap-wrap';
        if (layout.scaffolding.length) {
            const layer = document.createElement('div');
            layer.className = 'plan-sitemap-scaffolding';
            const title = document.createElement('span');
            title.textContent = 'Scaffolding';
            layer.append(title);
            for (const entry of layout.scaffolding) {
                const chip = document.createElement('span');
                chip.className = 'plan-sitemap-scaffold';
                chip.textContent = entry.label;
                layer.append(chip);
            }
            wrap.append(layer);
        }
        if (!layout.nodes.length) {
            const empty = document.createElement('div');
            empty.className = 'plan-canvas-empty';
            const strong = document.createElement('p');
            const bold = document.createElement('strong');
            bold.textContent = 'No HTML pages in the sitemap';
            strong.append(bold);
            const hint = document.createElement('p');
            hint.className = 'plan-empty-hint';
            hint.textContent = layout.note || 'No HTML templates, views, or URL maps were found. React routes are not used.';
            empty.append(strong, hint);
            wrap.append(empty);
            preview.append(wrap);
            return;
        }
        const canvas = document.createElement('div');
        canvas.className = 'plan-sitemap';
        canvas.setAttribute('role', 'group');
        canvas.setAttribute('aria-label', 'Sitemap');
        canvas.style.width = `${layout.width}px`;
        canvas.style.height = `${layout.height}px`;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', String(layout.width));
        svg.setAttribute('height', String(layout.height));
        svg.setAttribute('aria-hidden', 'true');
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
        marker.setAttribute('id', `plan-sitemap-arrow-${item.id}`);
        marker.setAttribute('markerWidth', '8');
        marker.setAttribute('markerHeight', '8');
        marker.setAttribute('refX', '7');
        marker.setAttribute('refY', '4');
        marker.setAttribute('orient', 'auto');
        const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        arrow.setAttribute('d', 'M0,0 L8,4 L0,8 z');
        arrow.setAttribute('class', 'plan-sitemap-arrow');
        marker.append(arrow);
        defs.append(marker);
        svg.append(defs);
        for (const edge of layout.edges) {
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', String(edge.x1));
            line.setAttribute('y1', String(edge.y1));
            line.setAttribute('x2', String(edge.x2));
            line.setAttribute('y2', String(edge.y2));
            line.setAttribute('data-kind', edge.kind);
            line.setAttribute('class', `plan-sitemap-edge plan-sitemap-edge-${edge.kind}`);
            line.setAttribute('marker-end', `url(#plan-sitemap-arrow-${item.id})`);
            svg.append(line);
        }
        canvas.append(svg);
        const byId = new Map(screens.map(screen => [String(screen.id), screen]));
        for (const node of layout.nodes) {
            const el = document.createElement('div');
            el.className = 'plan-sitemap-node';
            el.dataset.kind = node.kind;
            el.dataset.status = node.status;
            el.style.left = `${node.x}px`;
            el.style.top = `${node.y}px`;
            const name = document.createElement('strong');
            name.textContent = node.label;
            appendPageStencil(el, byId.get(node.screenId));
            const meta = document.createElement('small');
            meta.textContent = node.route;
            el.append(name, meta);
            decorate(el, node);
            canvas.append(el);
        }
        wrap.append(canvas);
        const legend = document.createElement('div');
        legend.className = 'plan-sitemap-legend';
        for (const kind of ['parent', 'navigation', 'journey', 'auth', 'redirect', 'success', 'failure']) {
            if (!layout.edges.some(edge => edge.kind === kind)) continue;
            const chip = document.createElement('span');
            chip.dataset.kind = kind;
            chip.textContent = kind;
            legend.append(chip);
        }
        if (legend.childNodes.length) wrap.append(legend);
        if (layout.unmappedCount) {
            const note = document.createElement('p');
            note.className = 'plan-sitemap-coverage';
            note.textContent = `${layout.nodes.length} pages. ${layout.unmappedCount} discovered routes are on the sitemap with no page stencil yet.`;
            wrap.append(note);
        }
        preview.append(wrap);
        const saved = session.sitemapPositions?.[item.id];
        if (saved) requestAnimationFrame(() => { preview.scrollLeft = saved.left; preview.scrollTop = saved.top; });
    }

    // ---- Interfaces node metadata (N6): shapes, images, notes, links in the item's trailing sidecar line -----
    const sessionAssets = (session) => [...new Map([...(session.conversation.attachments || []), ...session.attachments].map((asset) => [String(asset.id), asset])).values()];
    function wireTargets(session) {
        const out = [];
        for (const it of (session.workspace.items || []).filter((row) => TABS.wireframes.types.includes(row.item_type) && row.content_format === 'wire')) {
            let screens = [];
            try { screens = parseWireframe(it.content).screens || []; } catch { screens = []; }
            for (const screen of screens) if (screen.attrs?.id && !out.some((t) => t.id === screen.attrs.id)) out.push({ id: screen.attrs.id, label: screen.label || screen.attrs.id, itemId: it.id, screenId: screen.id });
        }
        return out;
    }
    function nodeCtx(session, item) {
        return {
            format: item.content_format,
            item: () => item,
            toast: (message, type) => host.toast?.(message, type),
            asset: (id) => sessionAssets(session).find((asset) => String(asset.id) === String(id) && /^image\//i.test(asset.mime_type || '') && safeUrl(asset.url)) || null,
            assetUrl: (id) => safeUrl(sessionAssets(session).find((asset) => String(asset.id) === String(id))?.url) || '',
            viewAsset: (id) => { closeNodePopover(); session.viewedAsset = String(id); renderArtifacts(session); },
            targets: () => wireTargets(session),
            follow: (id) => {
                const target = wireTargets(session).find((t) => t.id === id);
                if (!target) return;
                closeNodePopover();
                session.viewedAsset = null;
                session.sitemap = false;
                session.selection.wireframes = target.itemId;
                session.pages ||= {}; session.pages[target.itemId] = target.screenId;
                session.pageLayers ||= {}; session.pageLayers[target.itemId] = 'preview';
                renderArtifacts(session);
            },
            upload: async (file) => {
                const body = new FormData();
                body.append('file', file);
                const asset = await api(path(session, '/attachments'), { method: 'POST', body });
                session.conversation.attachments = [...(session.conversation.attachments || []), asset];
                return asset;
            },
            save: async (content) => {
                const before = item.content || '';
                const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { content, expected_revision: revisionOf(item) } });
                Object.assign(item, updated);
                recordIfaceEdit(session, item, before);
                if (!updated?.validation_warnings) delete item.validation_warnings;
                session.flowWarnings ||= {};
                session.flowWarnings[item.id] = updated?.validation_warnings || [];
                renderArtifacts(session);
                return updated;
            },
            reloadLatest: (mine) => reloadLatest(session, item, mine),
        };
    }
    const screenNode = (screen) => ({ key: screen.attrs?.id || null, screenLine: screen.attrs?.id ? null : screen.line, label: screen.label || screen.attrs?.id || 'Page' });
    function decorateScreenNode(session, item, el, screen) {
        if (!screen) return;
        const ctx = nodeCtx(session, item);
        const meta = screen.attrs?.id ? readSidecar(item.content, 'wire').nodes[screen.attrs.id] : null;
        decorateNode(el, meta, ctx);
        el.append(nodeButton(screen.label || 'Page', (opener) => openNodePopover(opener, screenNode(screen), ctx), 'plan-node-open-card'));
    }
    function nodeNotices(session, item, panel, preview) {
        const warnings = [...(session.flowWarnings?.[item.id] || [])];
        const bad = ['wire', 'mermaid'].includes(item.content_format) ? readSidecar(item.content, item.content_format).error : '';
        if (bad) warnings.unshift(`Node metadata was ignored: ${bad}`);
        if (warnings.length) panel.insertBefore(warningsNotice(warnings, () => { if (session.flowWarnings) session.flowWarnings[item.id] = []; }), preview);
    }

    function rememberDocContents(session) {
        const map = new Map();
        for (const row of session.workspace?.items || []) {
            if (row?.id != null) map.set(String(row.id), row.content || '');
        }
        session._docContents = map;
    }
    function rememberDocContent(session, item) {
        if (item?.id == null) return;
        session._docContents ||= new Map();
        session._docContents.set(String(item.id), item.content || '');
    }
    function documentDraft(session, item) {
        if (!item) return null;
        return session.inlineDrafts?.[item.id] ?? session.inlineDrafts?.[String(item.id)] ?? null;
    }
    function takeDocEditAnim(session, item) {
        if (session.tab !== 'requirements' || !item) return null;
        const previous = session._docContents?.get(String(item.id));
        const next = item.content || '';
        if (previous == null || previous === next) return null;
        const stored = documentDraft(session, item);
        const draft = stored?.draft;
        // Skip only when the person has real unsaved edits on the same revision.
        // A leftover draft based on an older revision must not hide an applied agent edit
        // (and must not remount the editor on that stale text).
        if (draft != null && draft !== previous && draft !== next) {
            const base = stored.baseRevision;
            const currentRev = revisionOf(item);
            if (base == null || currentRev == null || Number(base) === Number(currentRev)) return null;
        }
        if (session.inlineDrafts) {
            delete session.inlineDrafts[item.id];
            delete session.inlineDrafts[String(item.id)];
        }
        return { oldText: previous, newText: next };
    }
    function renderArtifacts(session) {
        if (!current(session)) return;
        const itemForAnim = session.tab === 'requirements' ? selected(session) : null;
        const pendingDocAnim = takeDocEditAnim(session, itemForAnim);
        // A later refresh (send finally + monitor, chat title, etc.) must not remount
        // #plan-preview and kill an in-flight agent-edit animation of the same text.
        if (!pendingDocAnim && session._docAnimPlaying
            && itemForAnim && String(session._docAnimPlaying.id) === String(itemForAnim.id)
            && (itemForAnim.content || '') === session._docAnimPlaying.newText) {
            rememberDocContents(session);
            return;
        }
        session.docAnimGen = (session.docAnimGen || 0) + 1;
        const renderGen = session.docAnimGen;
        rememberDocContents(session);
        ifaceActions?.remove();
        session.previewCleanup?.();
        session.previewCleanup = null;
        const panel = root.querySelector('#plan-artifacts');
        const body = panel.closest('.plan-detail-body');
        const panelsKey = `decisions.plan.project-panels.${session.scope}`;
        if (session.showProjectPanels == null) {
            try { session.showProjectPanels = tabStore()?.getItem(panelsKey) === '1'; }
            catch { session.showProjectPanels = false; }
        }
        body.toggleAttribute('data-canvas-workspace', session.tab === 'wireframes');
        body.toggleAttribute('data-show-project-panels', Boolean(session.showProjectPanels));
        if (ifaceActions) {
            let toggle = ifaceActions.querySelector('[data-project-panels]');
            if (!toggle) {
                toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'header-button iface-workspace-toggle';
                toggle.dataset.projectPanels = '';
                ifaceActions.append(toggle);
            }
            toggle.textContent = session.showProjectPanels ? 'Focus canvas' : 'Project panels';
            toggle.setAttribute('aria-pressed', String(Boolean(session.showProjectPanels)));
            toggle.onclick = () => {
                session.showProjectPanels = !session.showProjectPanels;
                try { tabStore()?.setItem(panelsKey, session.showProjectPanels ? '1' : '0'); } catch { /* tab storage unavailable */ }
                body.toggleAttribute('data-show-project-panels', session.showProjectPanels);
                toggle.textContent = session.showProjectPanels ? 'Focus canvas' : 'Project panels';
                toggle.setAttribute('aria-pressed', String(session.showProjectPanels));
                requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
            };
        }
        // Artifacts are always shown (the old show/hide toggle was removed in Round 16).
        panel.hidden = false;
        planBar().querySelectorAll('[data-plan-tab]').forEach(button => button.setAttribute('aria-pressed', String(session.tab === button.dataset.planTab)));
        const choices = items(session);
        const showDiscoveryChoice = session.tab === 'wireframes'
            && choices.length > 0 && choices.every(choice => choice.content_format !== 'wire')
            && session.workspace.sitemap?.nodes?.some(node => node.kind === 'page' && node.route);
        if (session.tab === 'wireframes' && !showDiscoveryChoice && session.selection.wireframes === DISCOVERED_PAGES) {
            delete session.selection.wireframes;
        }
        const item = selected(session);
        if (item) session.selection[session.tab] = item.id;
        const discoveryOption = showDiscoveryChoice
            ? `<option value="${DISCOVERED_PAGES}"${item ? '' : ' selected'}>Discovered pages</option>` : '';
        const assets = [...new Map([...(session.conversation.attachments || []), ...session.attachments].map((asset) => [String(asset.id), asset])).values()];
        const previewAssets = assets.filter(asset => asset.url === `/api/workflows/studio/plan-workspaces/${session.id}/attachments/${asset.id}`)
            .map(asset => ({...asset, url: new URL(asset.url, window.location.origin).href}));
        const docsTab = session.tab === 'requirements';
        panel.innerHTML = `${docsTab ? documentsBarHtml(session, choices, item) : choices.length > 1 || showDiscoveryChoice ? `<label class="plan-artifact-picker"><select id="plan-artifact-select" aria-label="Select artifact">${choices.map((choice) => `<option value="${esc(choice.id)}"${String(choice.id) === String(item?.id) ? ' selected' : ''}>${esc(choice.title)}${choice.is_starter ? ' · needs content' : ''}</option>`).join('')}${discoveryOption}</select></label>` : ''}
            ${item && !session.viewedAsset && (item.is_starter || item.db_only) ? `<div class="plan-artifact-revision" data-plan-provenance="${esc(item.origin || '')}"${provenanceText(item) ? ` title="${esc(provenanceText(item))}"` : ''}>${item.is_starter ? 'Not generated yet · Plan fills this from the project scan or the approved design' : 'Stored in Decisions only'}</div>` : ''}
            ${item?.file_sync_error ? `<div class="plan-file-status" role="alert">${esc(item.file_sync_error)}<button type="button" id="plan-review-file">Review file changes</button></div>` : ''}
            <div class="plan-preview" id="plan-preview"></div>
            ${assets.length ? `<div class="plan-assets" aria-label="Assets">${assetsHtml(assets)}</div>` : ''}`;
        if (docsTab) wireDocumentsBar(session, panel, choices, item);
        document.querySelectorAll('[data-plan-tab]').forEach((tab) => {
            const info = TABS[tab.dataset.planTab];
            if (!info) return;
            const origin = tab.dataset.planTab === session.tab && !session.viewedAsset ? provenanceText(item) : '';
            tab.title = `${info.help || info.label}${origin ? ` — ${origin}` : ''}`;
        });
        panel.querySelector('#plan-artifact-select')?.addEventListener('change', (event) => { session.viewedAsset = null; session.selection[session.tab] = event.target.value; renderArtifacts(session); });
        panel.querySelectorAll('[data-view-asset]').forEach(button => {
            button.onclick = () => { session.viewedAsset = button.dataset.viewAsset; renderArtifacts(session); };
        });
        const review = panel.querySelector('#plan-review-file');
        if (review) { review.disabled = Boolean(session.pending); review.onclick = () => reviewFile(session, item); }
        const preview = panel.querySelector('#plan-preview');
        if (session.tab === 'wireframes' && (session.rebuildingInterfaces || session.rescanningImages)) {
            preview.innerHTML = rebuildStatusHtml(session.rebuildPercent || 0, session.rebuildStatusLabel || (session.rescanningImages ? 'Rescanning images' : 'Rebuilding interface'));
            parkIfaceActions(session, preview);
            return;
        }
        const sourceAsset = assets.find(asset => String(asset.id) === String(session.viewedAsset));
        if (sourceAsset) { renderSourceAsset(session, sourceAsset, preview); parkIfaceActions(session, preview); return; }
        if (!item) {
            if (session.tab === 'wireframes' && mountIfaceCanvas(session, null, panel, preview, choices)) {
                parkIfaceActions(session, preview);
                return;
            }
            const tips = {
                wireframes: 'HTML templates, views, and URL maps fill the sitemap. Ask Plan to draft a page outline for anything still missing.',
                erd: 'Ask Plan for an ERD once the main entities are clear.',
                requirements: 'Capture FRAC / acceptance criteria after the screens make sense.',
            };
            session.previewCleanup?.();
            session.previewCleanup = null;
            preview.innerHTML = `<div class="plan-canvas-empty"><p><strong>No ${esc(TABS[session.tab].label.toLowerCase())} yet</strong></p><p class="plan-empty-hint">${esc(tips[session.tab] || 'Ask Plan to fill this section.')}</p>
                ${session.tab === 'wireframes' ? '<div class="plan-empty-actions"><button type="button" class="header-button primary" data-first-page>Add page</button><button type="button" class="header-button" data-first-endpoint>Add endpoint</button></div>' : ''}</div>`;
            preview.querySelector('[data-first-page]')?.addEventListener('click', e => addPageDrawer(session, null, { opener: e.currentTarget }));
            preview.querySelector('[data-first-endpoint]')?.addEventListener('click', e => addPageDrawer(session, null, { endpoint: true, opener: e.currentTarget }));
            if (session.tab === 'wireframes') session.previewCleanup = interfacesSurface(preview, '0 pages, 0 documents', 'Ask Plan to draft a page outline, or rescan the project to fill the sitemap.');
            parkIfaceActions(session, preview);
            return;
        }
        session.previewCleanup?.();
        session.previewCleanup = null;
        if (item && isScaffoldPreview(item) && !session.viewedAsset) {
            preview.innerHTML = `<div class="plan-canvas-empty" data-plan-empty-starter>
                <p><strong>No preview yet</strong></p>
                <p class="plan-empty-hint">This project does not have a generated plan yet. Generate one from the project, then review the screens here.</p>
                <button type="button" class="header-button primary" data-plan-empty-generate>Generate plan</button>
            </div>`;
            preview.querySelector('[data-plan-empty-generate]')?.addEventListener('click', triggerGeneratePlan);
            parkIfaceActions(session, preview);
            return;
        }
        if (session.tab === 'wireframes') nodeNotices(session, item, panel, preview);
        if (session.tab === 'wireframes' && item.content_format === 'wire' && mountIfaceCanvas(session, item, panel, preview, choices)) { parkIfaceActions(session, preview); return; }
        let ifaceTools = null;
        if (session.tab === 'wireframes' && item.content_format !== 'mermaid') {
            // Interfaces: same chrome as the Data canvas, a bottom-left dock inside the surface holding the
            // Advanced drawer (editable page source, Apply = revision-checked save) and the node opener.
            ifaceTools = document.createElement('div');
            ifaceTools.className = 'erd-dock erd-dock-left plan-interfaces-tools';
            ifaceTools.setAttribute('role', 'toolbar');
            ifaceTools.setAttribute('aria-label', 'Page');
            if (item.content_format === 'wire') {
                const h = ifaceHistory(session, item);
                const mk = (cls, label, title, svg, fn, disabled = false) => { const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.setAttribute('aria-label', label); b.title = title; b.innerHTML = svg; b.disabled = disabled; b.onclick = fn; return b; };
                const ico = (d) => `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
                const divider = document.createElement('span'); divider.className = 'erd-dock-divider'; divider.setAttribute('aria-hidden', 'true');
                ifaceTools.append(
                    mk('erd-dock-button erd-dock-wide', 'Add page', 'Add page', `${ico('M8 3v10M3 8h10')}<span class="erd-dock-label">Add page</span>`, (e) => addPageDrawer(session, item, { opener: e.currentTarget })),
                    mk('erd-dock-button', 'Add endpoint', 'Add endpoint', ico('M5.5 4 2 8l3.5 4m5-8L14 8l-3.5 4M9 3 7 13'), (e) => addPageDrawer(session, item, { endpoint: true, opener: e.currentTarget })),
                    divider,
                    mk('erd-dock-button', 'Undo', 'Undo', ico('M6 4 3 7l3 3M3.5 7H10a3 3 0 0 1 0 6H8'), () => ifaceStep(session, item, 'undo'), !h.undo.length),
                    mk('erd-dock-button', 'Redo', 'Redo', ico('M10 4l3 3-3 3M12.5 7H6a3 3 0 0 0 0 6h2'), () => ifaceStep(session, item, 'redo'), !h.redo.length));
            }
            ifaceTools.append(advancedButton({ title: 'Advanced · page source', text: () => item.content || '', onApply: (text) => applyPageSource(session, item, text) }));
            panel.append(ifaceTools);
        }
        if (session.tab === 'wireframes') {
            const surfaceCleanup = interfacesSurface(preview);
            const pageLayer = session.pageLayers?.[item.id] || 'preview';
            const inlineDock = item.content_format === 'wire'
                && (pageLayer === 'design' || (pageLayer === 'preview' && session.previewModes?.[item.id] !== 'svg'));
            if (inlineDock && ifaceTools) {
                ifaceTools.classList.add('plan-preview-dock');
                preview.after(ifaceTools);
            }
            const dockCleanup = inlineDock ? () => ifaceTools?.remove() : ifaceTools ? pinDock(ifaceTools, preview) : null;
            session.previewCleanup = () => { surfaceCleanup?.(); dockCleanup?.(); };
        }
        if (item.content_format === 'wire') {
            try {
                const parsed = parseWireframe(item.content);
                const screens = parsed.screens || [];
                session.pages ||= {};
                session.previewModes ||= {};
                session.pageLayers ||= {};
                session.previewModes[item.id] ||= 'html';
                session.pageLayers[item.id] ||= 'preview';
                const openId = session.openPage?.[item.id];
                const page = screens.length === 1 ? screens[0] : (screens.find((s) => String(s.id) === String(openId)) || null);
                session.pages[item.id] = page?.id;
                const layer = session.pageLayers[item.id];
                const artboard = ((session.artboards ||= {})[item.id] ||= { width: 'wide' });
                const designDraft = session.designerDrafts?.[item.id];
                if (page) {
                    const toolbar = document.createElement('div');
                    toolbar.className = 'plan-preview-toolbar';
                    const layerButtons = `<div class="plan-page-layers" role="tablist" aria-label="Page perspective">
                        <button type="button" data-layer="design" aria-pressed="${layer === 'design'}">Design</button>
                        <button type="button" data-layer="preview" aria-pressed="${layer === 'preview'}">Preview</button>
                        <button type="button" data-layer="planning" aria-pressed="${layer === 'planning'}">Planning</button>
                        <button type="button" data-layer="development" aria-pressed="${layer === 'development'}">Development</button>
                       </div>`;
                    toolbar.innerHTML = `${openId ? '<button type="button" class="iface-back" data-iface-back aria-label="Back to all pages">← All pages</button>' : ''}
                        ${layer !== 'design' && openId && session.ifacePicker?.[item.id]?.hasSelection ? '<button type="button" data-iface-page-results>Back to results</button>' : ''}
                        ${layerButtons}${layer === 'preview' ? `${designDraft ? '<span class="plan-preview-draft" role="status">Unsaved design preview</span>' : ''}
                        <button type="button" class="plan-preview-mode" data-preview-mode="${session.previewModes[item.id]}" title="Switch the preview rendering">${session.previewModes[item.id] === 'html' ? 'Show schematic' : 'Show page preview'}</button>
                        ${session.previewModes[item.id] === 'html' ? `${artboardWidthControls()}<button type="button" data-export-page>Export HTML</button>` : ''}` : ''}`;
                    panel.insertBefore(toolbar, preview);
                    const ctx = nodeCtx(session, item);
                    (ifaceTools || toolbar).append(nodeButton(page.label || 'Page', (opener) => openNodePopover(opener, screenNode(page), ctx), ifaceTools ? 'erd-dock-button erd-dock-wide' : ''));
                    const pageMeta = page.attrs.id ? readSidecar(item.content, 'wire').nodes[page.attrs.id] : null;
                    if (pageMeta && Object.keys(pageMeta).length) {
                        const strip = document.createElement('div');
                        strip.className = 'plan-node-strip';
                        strip.setAttribute('aria-label', 'Node');
                        const name = document.createElement('span');
                        name.className = 'plan-node-strip-name';
                        name.textContent = page.label || page.attrs.id;
                        strip.append(name);
                        decorateNode(strip, pageMeta, ctx);
                        if (pageMeta.note) { const note = document.createElement('p'); note.className = 'plan-node-strip-note'; note.textContent = pageMeta.note; strip.append(note); }
                        panel.insertBefore(strip, preview);
                    }
                    function returnToMap() {
                        delete session.openPage[item.id];
                        renderArtifacts(session);
                    }
                    toolbar.querySelector('[data-iface-back]')?.addEventListener('click', () => returnToMap());
                    toolbar.querySelector('[data-iface-page-results]')?.addEventListener('click', () => returnToMap());
                    toolbar.querySelectorAll('[data-layer]').forEach(button => {
                        button.onclick = () => { session.pageLayers[item.id] = button.dataset.layer; renderArtifacts(session); };
                    });
                    // Preview is the one selected perspective; the rendering switch is an action, never a second pressed tab.
                    const htmlToggle = toolbar.querySelector('[data-preview-mode]');
                    if (htmlToggle) htmlToggle.onclick = () => { session.previewModes[item.id] = session.previewModes[item.id] === 'html' ? 'svg' : 'html'; renderArtifacts(session); };
                }

                const diagnosticsHtml = (diagnostics) => {
                    const explained = explainWireframeDiagnostics(diagnostics || []);
                    if (!explained.lines.length) return '';
                    const next = explained.steps[0] ? `<p class="plan-diag-next">${esc(explained.steps[0])}</p>` : '';
                    const items = explained.lines.map(line => `<li>${esc(line)}</li>`).join('');
                    return `<div class="plan-wireframe-diagnostics" role="status"><strong>${esc(explained.headline || 'Preview notes')}</strong>${next}<ul>${items}</ul></div>`;
                };

                const collectAnnotations = (node, bag = { binds: [], requirements: [], routes: [] }) => {
                    if (!node) return bag;
                    if (node.attrs?.bind || node.attrs?.binding) bag.binds.push(node.attrs.bind || node.attrs.binding);
                    if (node.attrs?.requirement) bag.requirements.push(node.attrs.requirement);
                    if (node.attrs?.route || node.attrs?.href) {
                        const value = node.attrs.route || node.attrs.href;
                        if (typeof value === 'string' && value.startsWith('/')) bag.routes.push(value);
                    }
                    (node.children || []).forEach(child => collectAnnotations(child, bag));
                    return bag;
                };

                if (!page && screens.length > 1) {
                    renderProjectSitemap(preview, session, screens, item);
                    const docErrors = (parsed.diagnostics || []).filter(d => d.severity === 'error');
                    if (docErrors.length) preview.insertAdjacentHTML('beforeend', diagnosticsHtml(parsed.diagnostics));
                    return;
                }

                if (page && layer === 'design') {
                    const pageToolbar = panel.querySelector('.plan-preview-toolbar');
                    if (ifaceTools && pageToolbar) {
                        const pageOptions = document.createElement('div');
                        pageOptions.className = 'plan-design-page-options';
                        pageOptions.append(...ifaceTools.querySelectorAll('.plan-advanced-open, .plan-node-open'));
                        const projectPanels = ifaceActions?.querySelector('[data-project-panels]');
                        if (projectPanels) {
                            const editorToggle = projectPanels.cloneNode(true);
                            editorToggle.onclick = () => {
                                projectPanels.click();
                                editorToggle.textContent = projectPanels.textContent;
                                editorToggle.setAttribute('aria-pressed', projectPanels.getAttribute('aria-pressed'));
                            };
                            pageOptions.append(editorToggle);
                        }
                        if (pageOptions.childElementCount) pageToolbar.append(pageOptions);
                        ifaceTools.remove();
                    }
                    const drafts = session.designerDrafts ||= {};
                    const draft = drafts[item.id] || { source: item.content, baseSource: item.content, revision: revisionOf(item) };
                    mountPageDesigner(preview, {
                        item, pageIndex: screens.indexOf(page), draft, assets: previewAssets, artboard,
                        onDraft: (changed) => {
                            if (changed.source === changed.baseSource) delete drafts[item.id];
                            else drafts[item.id] = changed;
                        },
                        onSave: async (source, pageId) => {
                            const latest = session.workspace.items.find(row => row.id === item.id);
                            if (latest?.id !== item.id || revisionOf(latest) !== draft.revision || latest.content !== draft.baseSource) {
                                throw new Error('The stencil changed while you were editing. Discard this draft to open the latest version.');
                            }
                            await applyPageSource(session, latest, source, () => {
                                delete drafts[item.id];
                                if (session.openPage?.[item.id] === page.id) session.openPage[item.id] = pageId;
                            });
                        },
                        onDiscard: async () => {
                            await reload(session);
                            delete drafts[item.id];
                            renderArtifacts(session);
                        },
                    });
                    return;
                }

                if (page && layer === 'planning') {
                    const ann = collectAnnotations(page);
                    const kids = (page.children || []).length;
                    const uses = page.attrs.uses ? `<li>Layout template: <strong>${esc(page.attrs.uses)}</strong></li>` : '';
                    preview.innerHTML = `<div class="plan-page-hub">
                        <header><h3>${esc(page.label || 'Untitled page')}</h3>
                        ${page.attrs.route ? `<p class="plan-page-route">${esc(page.attrs.route)}</p>` : '<p class="plan-empty-hint">No route set yet</p>'}
                        </header>
                        <section><h4>Stencil summary</h4>
                          <ul><li>${kids} top-level block${kids === 1 ? '' : 's'} on this page</li>${uses}
                          <li>Device: ${esc(page.attrs.device || 'desktop')}</li></ul>
                          <p class="plan-empty-hint">This is the planning view of the page outline — not production UI yet.</p>
                        </section>
                        <section><h4>Linked plan notes</h4>
                          <ul>
                            <li>Requirements mentions: ${ann.requirements.length ? esc([...new Set(ann.requirements)].join(', ')) : 'none on this page yet'}</li>
                            <li>Planned data bindings: ${ann.binds.length ? esc([...new Set(ann.binds)].join(', ')) : 'none on this page yet'}</li>
                          </ul>
                          <div class="plan-hub-actions">
                            <button type="button" data-hub-tab="requirements">Open Documents</button>
                            <button type="button" data-hub-tab="erd">Open Data</button>
                          </div>
                        </section>
                    </div>`;
                    preview.querySelectorAll('[data-hub-tab]').forEach(btn => {
                        btn.onclick = () => planBar().querySelector(`[data-plan-tab="${btn.dataset.hubTab}"]`)?.click();
                    });
                    mountPageDescription(session, item, page, screens.indexOf(page), preview);
                    return;
                }

                if (page && layer === 'development') {
                    const ann = collectAnnotations(page);
                    const binds = [...new Set(ann.binds)];
                    const reqs = [...new Set(ann.requirements)];
                    const routes = [...new Set([page.attrs.route, ...ann.routes].filter(Boolean))];
                    preview.innerHTML = `<div class="plan-page-hub">
                        <header><h3>${esc(page.label || 'Untitled page')} · development</h3>
                        <p class="plan-empty-hint">How this page likely maps to view, model, and business logic. Full wiring lands later — these are planning links.</p></header>
                        <section><h4>View</h4>
                          <ul><li>Route${routes.length === 1 ? '' : 's'}: ${routes.length ? routes.map(r => `<code>${esc(r)}</code>`).join(', ') : '<em>not set</em>'}</li>
                          <li>Screen id: <code>${esc(page.attrs.id || page.id)}</code></li></ul></section>
                        <section><h4>Model</h4>
                          ${binds.length ? '<p class="plan-empty-hint">These bindings are plan annotations. Confirm name-based matches against the implementation.</p>' : ''}
                          <ul>${binds.length ? binds.map(b => `<li><code>${esc(b)}</code></li>`).join('') : '<li>No bind= annotations on this page yet. Add bind=Entity.field in the outline.</li>'}</ul>
                          <button type="button" data-hub-tab="erd">Jump to Data (ERD)</button></section>
                        <section><h4>Business logic / acceptance</h4>
                          <ul>${reqs.length ? reqs.map(r => `<li><code>${esc(r)}</code></li>`).join('') : '<li>No requirement= tags yet. Link FR-### once FRAC exists.</li>'}</ul>
                          <button type="button" data-hub-tab="requirements">Jump to Documents</button></section>
                    </div>`;
                    preview.querySelectorAll('[data-hub-tab]').forEach(btn => {
                        btn.onclick = () => planBar().querySelector(`[data-plan-tab="${btn.dataset.hubTab}"]`)?.click();
                    });
                    return;
                }

                const interactive = session.previewModes[item.id] === 'html';
                const source = designDraft?.source ?? item.content;
                const previewScreens = designDraft ? parseWireframe(source).screens : screens;
                const previewPageId = previewScreens[screens.indexOf(page)]?.id ?? page?.id;
                const result = renderWireframe(source, item.title, previewPageId, previewAssets, { interactive });
                preview.innerHTML = `<div class="plan-wireframe-preview">${result.svg}</div>${diagnosticsHtml(result.diagnostics)}`;
                if (interactive) {
                    const frame = document.createElement('iframe');
                    frame.title = page?.label || item.title || 'Page preview';
                    // The generated preview gets an opaque origin. Only its hash-pinned
                    // navigation script may send messages back to this host.
                    frame.setAttribute('sandbox', 'allow-scripts');
                    frame.referrerPolicy = 'no-referrer';
                    const listen = event => {
                        if (event.source !== frame.contentWindow || event.origin !== 'null' || event.data?.type !== 'decisions-plan-route'
                            || typeof event.data.route !== 'string') return;
                        const route = sitemapRouteKey(event.data.route);
                        if (!route) return;
                        const matches = screens.filter(screen => sitemapRouteKey(screen.attrs.route) === route);
                        if (matches.length !== 1 || matches[0].id === page?.id) return;
                        openIfacePage(session, item, { screenId: matches[0].id });
                        preview.querySelector('iframe')?.focus();
                    };
                    const cleanup = session.previewCleanup;
                    window.addEventListener('message', listen);
                    session.previewCleanup = () => { window.removeEventListener('message', listen); cleanup?.(); };
                    frame.srcdoc = result.html;
                    const artboardScroll = document.createElement('div');
                    artboardScroll.className = 'plan-artboard-scroll plan-preview-artboard';
                    artboardScroll.append(frame);
                    preview.querySelector('.plan-wireframe-preview').replaceWith(artboardScroll);
                    const widthControls = panel.querySelector('.plan-preview-toolbar .plan-design-viewport');
                    if (widthControls) bindArtboardWidth(widthControls, artboardScroll, artboard);
                    const exportButton = panel.querySelector('[data-export-page]');
                    if (exportButton) {
                        exportButton.disabled = result.diagnostics.some(entry => entry.severity === 'error');
                        exportButton.onclick = async () => {
                            exportButton.disabled = true;
                            try {
                                const html = await standalonePageHTML(source, item.title, previewScreens[screens.indexOf(page)], previewAssets);
                                const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
                                const download = document.createElement('a');
                                download.href = url;
                                download.download = `${slugId(page.label || item.title)}.html`;
                                download.click();
                                setTimeout(() => URL.revokeObjectURL(url), 1000);
                            } catch (error) {
                                host.toast?.(error.message || 'Could not export the page.', 'error');
                            } finally {
                                exportButton.disabled = false;
                            }
                        };
                    }
                }
                if (page) mountPageDescription(session, item, page, screens.indexOf(page), preview);
            } catch (error) {
                preview.innerHTML = `<div class="plan-canvas-empty"><p><strong>Could not show this page outline</strong></p><p class="plan-empty-hint">${esc(error.message || 'Try Sitemap or ask Plan to regenerate the wireframes.')}</p></div>`;
            }
        } else if (item.content_format === 'html') {
            const frame = document.createElement('iframe');
            frame.title = item.title || 'Wireframe';
            frame.setAttribute('sandbox', '');
            frame.referrerPolicy = 'no-referrer';
            frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; form-action 'none'; base-uri 'none'">${item.content || ''}`;
            preview.append(frame);
        } else if (item.content_format === 'mermaid' && isErdMermaid(item.content)) {
            // Data tab ERD: visual canvas is the only editing surface (DSL stays background, read-only Advanced).
            preview.classList.add('plan-erd');
            mountErdItem({ preview, item, session, api, toast: (m, t) => host.toast?.(m, t), isBlocked: () => blocked(session), reloadLatest: (mine) => reloadLatest(session, item, mine), onSchemaApplied: (updated) => schemaApplied(session, updated) });
        } else if (item.content_format === 'mermaid') {
            // The Mermaid DSL is a background representation (agents, rebuild, tickets): show the rendered
            // diagram read-only, plus a collapsed, read-only "Advanced" source view (never remembered open).
            const surface = document.createElement('div');
            surface.className = 'plan-canvas-surface plan-diagram-surface';
            const diagram = document.createElement('div');
            diagram.className = 'plan-mermaid';
            const dock = document.createElement('div');
            dock.className = 'erd-dock erd-dock-left';
            dock.setAttribute('role', 'toolbar');
            dock.setAttribute('aria-label', 'Diagram');
            dock.append(session.tab === 'wireframes'
                ? advancedButton({ title: 'Advanced · page source', text: () => item.content || '', onApply: (text) => applyPageSource(session, item, text) })
                : advancedButton({ text: () => item.content || '' }));
            if (session.tab === 'wireframes') {
                // Mermaid flows: native shapes; the sidecar adds image / note / link per node id.
                const ctx = nodeCtx(session, item);
                const nodes = () => { const meta = readSidecar(item.content, 'mermaid').nodes; return nodeIds(item.content, 'mermaid').map((id) => ({ key: id, label: id, meta: meta[id] })); };
                const opener = nodeButton('Mermaid flow', (button) => openNodePicker(button, nodes(), (node, from) => openNodePopover(from, { key: node.key, label: node.label }, ctx)), 'erd-dock-button erd-dock-wide');
                opener.setAttribute('aria-label', 'Node details');
                dock.append(opener);
                const described = nodes().filter((node) => node.meta && Object.keys(node.meta).length);
                if (described.length) {
                    const strip = document.createElement('div');
                    strip.className = 'plan-node-strip plan-node-strip-flow';
                    for (const node of described) {
                        const chip = document.createElement('span');
                        chip.className = 'plan-node-strip-item';
                        const name = document.createElement('span');
                        name.className = 'plan-node-strip-name';
                        name.textContent = node.label;
                        chip.append(name);
                        decorateNode(chip, node.meta, ctx);
                        strip.append(chip);
                    }
                    panel.insertBefore(strip, preview);
                }
            }
            surface.append(diagram, dock);
            preview.append(surface);
            renderDiagram(diagram, item.content);
            session.previewCleanup = fillToWindow(surface);
        } else {
            // Markdown documents are edited in place: rich inline editor + floating toolbar, or (Documents
            // "Edit markdown") a raw textarea. Both share the per-document draft, so switching mode keeps edits.
            const raw = session.tab === 'requirements' && docMode() === 'raw';
            const mountEditor = ({ afterAnim = false } = {}) => {
                const editor = inlineEditorFor(session, item, raw ? 'markdown-raw' : 'markdown', item.title || 'Document', () => markDocTabs(session)).el;
                if (!afterAnim) {
                    preview.replaceChildren();
                    preview.append(editor);
                    return Promise.resolve();
                }
                // Swap over the finished animation in place: mount the editor hidden
                // (out of flow so the preview does not jump), wait two frames, then
                // drop the anim view and reveal. Never leave #plan-preview empty.
                const anim = preview.querySelector('.plan-doc-anim');
                const prevPos = preview.style.position;
                if (getComputedStyle(preview).position === 'static') preview.style.position = 'relative';
                editor.setAttribute('data-plan-editor-handoff', '');
                editor.style.position = 'absolute';
                editor.style.inset = '0';
                editor.style.pointerEvents = 'none';
                preview.append(editor);
                void editor.offsetHeight;
                return new Promise((resolve) => {
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                        if (preview.contains(editor)) {
                            anim?.remove();
                            editor.removeAttribute('data-plan-editor-handoff');
                            editor.style.position = '';
                            editor.style.inset = '';
                            editor.style.pointerEvents = '';
                            preview.style.position = prevPos;
                        }
                        resolve();
                    }));
                });
            };
            if (pendingDocAnim && session.tab === 'requirements') {
                const alive = () => session.docAnimGen === renderGen && current(session) && preview.isConnected;
                session._docAnimPlaying = { id: item.id, newText: pendingDocAnim.newText, gen: renderGen };
                const run = window.__planDocEditAnim?.animateDocumentEdit || animateDocumentEdit;
                Promise.resolve(run(preview, pendingDocAnim.oldText, pendingDocAnim.newText, { alive })).then(async (finished) => {
                    if (finished && alive()) await mountEditor({ afterAnim: true });
                    if (session._docAnimPlaying?.gen === renderGen) session._docAnimPlaying = null;
                });
            } else mountEditor();
        }
        parkIfaceActions(session, preview);
    }

    // Top-right inside the bordered map (same overlay cell as the zoom dock). Hidden on other plan sections.
    function parkIfaceActions(session, preview) {
        const widgets = [ifaceRebuild, ifaceActions].filter(Boolean);
        if (!widgets.length) return;
        if (session.tab !== 'wireframes' || !preview?.isConnected) {
            for (const el of widgets) el.remove();
            return;
        }
        const anchor = preview.querySelector('.erd-body');
        const callout = preview.querySelector(':scope > .plan-canvas-callout');
        for (const el of widgets) {
            if (anchor) anchor.append(el);
            else if (callout) callout.after(el);
            else preview.prepend(el);
        }
        syncRebuildButton(session);
    }



    // Inline editing: drafts survive re-renders/tab switches per item; saves use the existing
    // PATCH /workflows/studio/plan-items/{id} (new revision + file sync), same content format.
    // A draft remembers the revision it was based on ({ draft, baseRevision }) and always saves against
    // that revision, so a draft restored after refreshWorkspace can never overwrite a newer server version:
    // if the item moved on, the editor opens in conflict mode (draft kept, Save locked, Reload latest offered).
    const revisionOf = (item) => (item?.revision_count == null ? null : Number(item.revision_count));
    function inlineEditorFor(session, item, format, label, onSaved = () => {}) {   // markdown text only (Requirements / Interfaces)
        session.inlineDrafts ||= {};
        const stored = session.inlineDrafts[item.id] ?? null;
        const baseRevision = stored ? stored.baseRevision : revisionOf(item);
        let editor;
        editor = createInlineEditor({
            value: item.content || '', format, label,
            initialDraft: stored?.draft ?? null,
            conflict: Boolean(stored) && stored.baseRevision !== revisionOf(item),
            onReloadLatest: (mine) => reloadLatest(session, item, mine),
            onDraft: (draft) => {
                if (editor && !editor.el.isConnected) return;
                if (draft === null) delete session.inlineDrafts[item.id];
                else session.inlineDrafts[item.id] = { draft, baseRevision: session.inlineDrafts[item.id]?.baseRevision ?? baseRevision };
            },
            toast: (message, type) => host.toast?.(message, type),
            save: async (content) => {
                const base = session.inlineDrafts[item.id]?.baseRevision ?? baseRevision;
                const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, {
                    method: 'PATCH', body: { content, expected_revision: base },
                });
                Object.assign(item, updated);
                rememberDocContent(session, item);
                const remaining = session.inlineDrafts[item.id];
                if (remaining?.draft === content) delete session.inlineDrafts[item.id];
                else if (remaining) remaining.baseRevision = revisionOf(updated);
                return updated;
            },
            onSaved: (content) => {
                onSaved(content);
                // A save may finish while another tab owns the preview. Mount
                // against its new revision when the editor has been replaced.
                if (!editor.el.isConnected && current(session)) renderArtifacts(session);
                controls(session);
            },
        });
        return editor;
    }

    // ---- Interfaces: per-page description + editable Advanced source -------------------------------------
    // A page description is markdown kept inside the page outline as "#|" comment lines directly under the
    // `screen` line (comments are ignored by the wire parser, renderer and server validator), so it travels
    // with the page and saves through the same PATCH + expected_revision path as every other edit.
    const DESC_PREFIX = /^(\s*)#\| ?(.*)$/;
    function pageDescription(content, at) {
        const lines = String(content || '').split(/\r?\n/);
        const out = [];
        for (let i = at + 1; i < lines.length; i += 1) { const m = DESC_PREFIX.exec(lines[i]); if (!m) break; out.push(m[2]); }
        return out.join('\n');
    }
    function withPageDescription(content, at, markdown) {
        const lines = String(content || '').split('\n');
        if (!/^\s*screen\b/.test(lines[at] || '')) throw new Error('This page outline has no screen to describe.');
        let end = at + 1;
        while (end < lines.length && DESC_PREFIX.test(lines[end].replace(/\r$/, ''))) end += 1;
        const indent = `${/^\s*/.exec(lines[at])[0]}  `;
        const body = String(markdown || '').replace(/\s+$/, '');
        const cr = lines[at].endsWith('\r') ? '\r' : '';
        const desc = body ? body.split(/\r?\n/).map((line) => `${indent}#|${line ? ` ${line}` : ''}${cr}`) : [];
        return [...lines.slice(0, at + 1), ...desc, ...lines.slice(end)].join('\n');
    }
    function mountPageDescription(session, item, page, pageIndex, preview) {
        if (!page.line) return;
        session.inlineDrafts ||= {};
        session.descModes ||= {};
        const prefix = `${item.id}#description/`;
        const key = `${prefix}${page.attrs.id ? `id:${page.attrs.id}` : `index:${pageIndex}`}`;
        const mode = session.descModes[key] === 'raw' ? 'raw' : 'inline';
        const sourceIndex = page.line - 1;
        const stored = session.inlineDrafts[key] ?? null;
        const baseRevision = stored ? stored.baseRevision : revisionOf(item);
        const section = document.createElement('section');
        section.className = 'plan-page-description';
        section.setAttribute('aria-label', 'Page description');
        section.innerHTML = `<div class="plan-page-description-head"><h4>Page description</h4>
            <span class="plan-doc-modes" role="group" aria-label="Description editor mode">
                <button type="button" data-desc-mode="raw" aria-pressed="${mode === 'raw'}">Edit markdown</button>
                <button type="button" data-desc-mode="inline" aria-pressed="${mode === 'inline'}">Inline editor</button></span></div>`;
        let editor;
        editor = createInlineEditor({
            value: pageDescription(item.content, sourceIndex), format: mode === 'raw' ? 'markdown-raw' : 'markdown', label: `${page.label || item.title || 'Page'} description`,
            initialDraft: stored?.draft ?? null,
            conflict: Boolean(stored) && stored.baseRevision !== revisionOf(item),
            onReloadLatest: (mine) => reloadLatest(session, item, mine, key),
            onDraft: (draft) => {
                if (editor && !editor.el.isConnected) return;
                if (draft === null) delete session.inlineDrafts[key];
                else session.inlineDrafts[key] = { draft, baseRevision: session.inlineDrafts[key]?.baseRevision ?? baseRevision };
            },
            toast: (message, type) => host.toast?.(message, type),
            save: async (markdown) => {
                const base = session.inlineDrafts[key]?.baseRevision ?? baseRevision;
                const content = withPageDescription(item.content, sourceIndex, markdown);
                const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, {
                    method: 'PATCH', body: { content, expected_revision: base },
                });
                Object.assign(item, updated);
                const remaining = session.inlineDrafts[key];
                if (remaining?.draft === markdown) delete session.inlineDrafts[key];
                // This patch changes only the selected description. Drafts based on the same
                // revision can still save against the new revision.
                if (updated.content === content) {
                    for (const [draftKey, draft] of Object.entries(session.inlineDrafts)) {
                        if (draftKey.startsWith(prefix) && draft.baseRevision === base) draft.baseRevision = revisionOf(updated);
                    }
                }
                return updated;
            },
            onSaved: () => {
                if (!editor.el.isConnected && current(session)) renderArtifacts(session);
                controls(session);
            },
        });
        section.append(editor.el);
        section.querySelectorAll('[data-desc-mode]').forEach((button) => {
            button.onclick = () => {
                if (button.getAttribute('aria-pressed') === 'true') return;
                session.descModes[key] = button.dataset.descMode;
                editor.destroy();
                renderArtifacts(session);
                root.querySelector(`[data-desc-mode="${button.dataset.descMode}"]`)?.focus();
            };
        });
        preview.append(section);
        return editor;
    }
    async function applyPageSource(session, item, text, beforeRender) {
        if (item.content_format === 'wire') {
            let errors = [];
            try { errors = (parseWireframe(text).diagnostics || []).filter((d) => d.severity === 'error'); }
            catch (error) { errors = [{ line: 0, message: error?.message || 'Could not read the page outline.' }]; }
            if (errors.length) throw new Error(`Invalid page outline: ${errors.slice(0, 3).map((d) => (d.line ? `line ${d.line}: ${d.message}` : d.message)).join('; ')}`);
        }
        let updated;
        const before = item.content || '';
        try {
            updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { content: text, expected_revision: revisionOf(item) } });
        } catch (error) {
            if (error?.status === 409 || isRevisionConflict(error)) {
                throw Object.assign(new Error('This page has a newer revision. Your text is kept here; copy it, then reload the page to see the latest version.'), { status: 409 });
            }
            throw error;
        }
        Object.assign(item, updated);
        recordIfaceEdit(session, item, before);
        beforeRender?.();
        renderArtifacts(session);
        host.toast?.(updated?.file_sync_error ? 'Applied. The file sync is pending and will retry.' : 'Applied');
    }

    // ---- Interfaces canvas: undo / redo of this tab's saved edits, Add page, page details ----------------
    // Every step is a normal revision-checked PATCH (new revision, server validation); history lives for the
    // browser session only and is dropped when the item changed elsewhere.
    function ifaceHistory(session, item) {
        session.ifaceHistory ||= {};
        return (session.ifaceHistory[item.id] ||= { undo: [], redo: [] });
    }
    function recordIfaceEdit(session, item, before, { keepRedo = false } = {}) {
        if (session.tab !== 'wireframes' || before === (item.content || '')) return;
        const h = ifaceHistory(session, item);
        h.undo.push({ before, after: item.content || '' });
        if (h.undo.length > 50) h.undo.shift();
        if (!keepRedo) h.redo = [];
    }
    async function ifaceStep(session, item, direction) {
        const h = ifaceHistory(session, item);
        const stack = direction === 'undo' ? h.undo : h.redo;
        const step = stack[stack.length - 1];
        if (!step || blocked(session)) return;
        const expected = direction === 'undo' ? step.after : step.before;
        if ((item.content || '') !== expected) {
            h.undo = []; h.redo = [];
            host.toast?.('This page outline changed elsewhere, so undo history was cleared.', 'info');
            renderArtifacts(session);
            return;
        }
        try {
            const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { content: direction === 'undo' ? step.before : step.after, expected_revision: revisionOf(item) } });
            Object.assign(item, updated);
            stack.pop();
            (direction === 'undo' ? h.redo : h.undo).push(step);
            host.toast?.(direction === 'undo' ? 'Undone (saved as a new revision)' : 'Redone (saved as a new revision)');
        } catch (error) {
            host.toast?.(error?.status === 409 || isRevisionConflict(error) ? 'This page outline has a newer revision; reload to see it.' : `Could not ${direction}: ${error?.message || 'unknown error'}`, 'error');
        }
        renderArtifacts(session);
    }
    function addPageDrawer(session, item, { label = '', route = '', endpoint = false, opener = document.activeElement } = {}) {
        const title = endpoint ? 'Add endpoint' : (label ? 'Create page outline' : 'Add page');
        const { drawer, close } = openDrawer({ title, opener, className: 'iface-add-drawer', html: `
            <form class="iface-add-form">
                <label>${endpoint ? 'Endpoint name' : 'Page name'}<input name="label" required maxlength="80" value="${esc(label)}" autocomplete="off"></label>
                <label>Route<input name="route" ${endpoint ? 'required' : ''} maxlength="200" placeholder="${endpoint ? '/api/items' : '/example'}" value="${esc(route)}" autocomplete="off"></label>
                ${endpoint ? `<label>Method<select name="method">${HTTP_METHODS.map(method => `<option>${method}</option>`).join('')}</select></label>` : ''}
                <footer class="plan-drawer-foot"><p class="plan-drawer-error" role="alert" hidden></p>
                    <button type="button" class="header-button plan-drawer-cancel">Cancel</button>
                    <button type="submit" class="header-button primary">${label && !endpoint ? 'Create outline' : title}</button></footer>
            </form>` });
        const form = drawer.querySelector('form');
        const error = drawer.querySelector('.plan-drawer-error');
        drawer.querySelector('.plan-drawer-cancel').onclick = close;
        setTimeout(() => form.elements.label.focus(), 30);
        form.onsubmit = async (event) => {
            event.preventDefault();
            const name = form.elements.label.value.trim();
            if (!name) return;
            const submit = form.querySelector('[type=submit]');
            submit.disabled = true;
            try {
                let taken = [];
                try { taken = (parseWireframe(item?.content || '').screens || []).map((s) => s.attrs?.id).filter(Boolean); } catch { taken = []; }
                const id = slugId(name, taken);
                const sidecar = readSidecar(item?.content || '', 'wire');
                const body = (sidecar.error ? (item?.content || '') : sidecar.body).replace(/\s+$/, '');
                const route = form.elements.route.value.trim();
                if (endpoint && !/^\/(?!\/)[^\s\\#]*$/.test(route)) throw new Error('Use a local endpoint route, such as /api/items.');
                const source = endpoint
                    ? `screen ${JSON.stringify(name)} id=${id} layer=ajax method=${form.elements.method.value} route=${JSON.stringify(route)}\n`
                    : newScreenSource({ label: name, route, id });
                const added = `${body ? `${body}\n` : ''}${source}`;
                const content = !sidecar.error && Object.keys(sidecar.nodes).length ? writeSidecar(added, 'wire', sidecar.nodes) : added;
                if (!item) {
                    const created = await api(path(session, '/items'), { method: 'POST', body: { item_type: 'wireframe', title: 'Interfaces', content_format: 'wire', content } });
                    session.workspace.items = [...(session.workspace.items || []), created];
                    session.selection.wireframes = created.id;
                    close();
                    if (current(session)) renderArtifacts(session);
                    return;
                }
                let opened = false;
                await applyPageSource(session, item, content, () => {
                    if (endpoint) delete (session.openPage || {})[item.id];
                    if (!label || endpoint || !current(session) || session.tab !== 'wireframes' || selected(session) !== item
                        || !drawer.closest('.plan-drawer-backdrop.open')) return;
                    const screen = parseWireframe(item.content).screens.find((entry) => entry.attrs.id === id);
                    if (!screen) return;
                    (session.openPage ||= {})[item.id] = screen.id;
                    (session.pageLayers ||= {})[item.id] = 'planning';
                    opened = true;
                });
                close();
                if (opened) {
                    root.querySelector('.plan-detail-body').scrollTop = 0;
                    root.querySelector('[data-iface-back]')?.focus({ preventScroll: true });
                }
            } catch (e) {
                error.textContent = e?.message || (endpoint ? 'Could not add the endpoint.' : 'Could not add the page.');
                error.hidden = false;
                submit.disabled = false;
            }
        };
    }
    function ifaceLinks(model, label, keys) {
        const links = keys.map(key => model.byKey.get(key))
            .filter(Boolean)
            .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }) || a.route.localeCompare(b.route));
        if (!links.length) return '';
        return `<div class="iface-page-links" role="group" aria-label="${label}">
            <h5>${label}</h5><ul>${links.map(page => `<li><button type="button" data-iface-follow="${esc(page.key)}">
                <strong>${esc(page.label)}</strong>${page.route ? `<small>${esc(page.route)}</small>` : ''}
            </button></li>`).join('')}</ul></div>`;
    }
    function ifacePanelHtml(node, model, fromPicker, hasOutlineItem) {
        const lane = LANES.find((l) => l.id === node.lane)?.label || 'Public';
        const row = (k, v) => (v ? `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>` : '');
        const outgoing = new Set();
        const incoming = new Set();
        for (const { from, to, kind } of model.edges) {
            if (!['link', 'navigation', 'journey'].includes(kind)) continue;
            if (from === node.key) outgoing.add(to);
            if (to === node.key) incoming.add(from);
        }
        return `<h4 tabindex="-1">${esc(node.label)}</h4>
            ${node.route ? `<p class="iface-selected-route">${esc(node.route)}</p>` : ''}
            ${hasOutlineItem || fromPicker || node.kind === 'endpoint' || outgoing.size || incoming.size ? `<div class="erd-panel-actions iface-panel-actions">
                ${outgoing.size || incoming.size ? '<button type="button" class="header-button" data-iface-journey>Focus journey</button>' : ''}
                ${node.kind === 'endpoint' ? '<button type="button" class="header-button primary" data-iface-open>Open endpoint</button>' : ''}
                ${hasOutlineItem && node.kind !== 'endpoint' ? (node.screenId ? '<button type="button" class="header-button primary" data-iface-open>Open page</button><button type="button" class="header-button" data-iface-node>Name, id, shape…</button>'
                    : '<button type="button" class="header-button primary" data-iface-create>Create page outline</button>') : ''}
                ${fromPicker ? '<button type="button" class="header-button" data-iface-results>Back to results</button>' : ''}
                ${hasOutlineItem ? '<button type="button" class="header-button" data-iface-source>Source</button>' : ''}
            </div>` : ''}
            ${node.template ? `<dl class="iface-facts">${row('Template', node.template)}</dl>` : ''}
            ${ifaceLinks(model, 'Links both ways', [...outgoing].filter(key => incoming.has(key)))}
            ${ifaceLinks(model, 'Links to', [...outgoing].filter(key => !incoming.has(key)))}
            ${ifaceLinks(model, 'Linked from', [...incoming].filter(key => !outgoing.has(key)))}
            ${node.shot?.image ? `<img class="iface-panel-shot" alt="Screenshot of ${esc(node.label)}" src="${esc(node.shot.image)}">` : ''}
            <dl class="iface-facts">${row('Section', lane)}
                ${row('Shape', `${SHAPE_KIND[node.shape] || node.shape}${node.shapeSource === 'default' ? ' (default for this page type)' : ''}`)}
                ${row('Page outline', node.screenId ? (node.screenKey ? `yes · id ${node.screenKey}` : 'yes') : 'not yet')}
                ${row('View', node.view)}${row('Status', node.status)}${row('Method', node.method)}${row('Screenshot', node.lane === 'ajax' || node.lane === 'guidance' ? '' : (node.shot?.image ? '' : node.shot?.reason || (node.shot ? '' : 'none saved')))}</dl>
            ${node.note ? `<p class="iface-note">${esc(node.note)}</p>` : ''}`;
    }
    function openIfacePage(session, item, node, layer = 'preview') {
        if (node?.kind === 'endpoint') {
            const source = item?.content || '';
            const screen = node.screenId && parseWireframe(source).sourceScreens.find(entry => String(entry.id) === node.screenId);
            let revision = item && revisionOf(item);
            openEndpointEditor({
                label: node.label, route: screen?.attrs.route || node.route, method: node.method,
                projectKey: session.workspace.project_id || session.id,
                example: screen ? endpointExample(source, screen.line) : null,
                onSave: screen ? async (example) => {
                    if (revisionOf(item) !== revision) throw new Error('The stencil changed. Reopen the endpoint before saving.');
                    await applyPageSource(session, item, saveEndpointExample(item.content, screen.line, example));
                    revision = revisionOf(item);
                } : null,
            });
            return true;
        }
        if (!item || !node?.screenId) return false;
        session.openPage ||= {};
        session.openPage[item.id] = node.screenId;
        session.pageLayers ||= {};
        session.pageLayers[item.id] = layer;
        renderArtifacts(session);
        return true;
    }
    // The workspace sitemap is browsable before its first outline exists. A single saved
    // outline (or an opened outline) still uses the page view.
    function mountIfaceCanvas(session, item, panel, preview, choices) {
        const viewKey = item?.id ?? DISCOVERED_PAGES;
        const content = item?.content || '';
        let parsed;
        try { parsed = item ? parseWireframe(content) : null; } catch { return false; }
        const screens = parsed?.screens || [];
        const openId = session.openPage?.[viewKey];
        if (openId && screens.some((s) => String(s.id) === String(openId))) return false;
        const sitemap = session.workspace?.sitemap;
        const hasDiscoveredRoutes = sitemap?.nodes?.some(node => node.kind === 'page' && node.route);
        const mapScreens = hasDiscoveredRoutes && content.includes('heading "No route table detected"')
            ? screens.filter(screen => screen.label !== 'No routes found' || screen.attrs?.route)
            : screens;
        const fullModel = buildIfaceModel({ sitemap, screens: mapScreens, captures: pageCaptureFor(session.workspace), meta: readSidecar(content, 'wire').nodes, source: content });
        const journey = (session.ifaceJourney ||= {});
        const pinnedJourney = journey[viewKey];
        const matches = pinnedJourney && !fullModel.byKey.has(pinnedJourney.key)
            ? fullModel.nodes.filter(node => node.route === pinnedJourney.route && node.method === pinnedJourney.method
                && node.kind === pinnedJourney.kind && (node.route || node.label === pinnedJourney.label))
            : [];
        const anchor = matches.length === 1 ? matches[0].key : pinnedJourney?.key;
        const model = focusIfaceJourney(fullModel, anchor);
        const scoped = model !== fullModel;
        const cameraKey = scoped ? `${viewKey}:${anchor}` : viewKey;
        if (!model.nodes.length) return false;
        if (screens.length === 1 && mapScreens.length === 1 && model.nodes.length === 1 && model.nodes[0].kind !== 'endpoint') return false;
        if (item) (session.pages ||= {})[item.id] = null;
        const history = item ? ifaceHistory(session, item) : null;
        session.ifaceView ||= {}; session.ifaceSel ||= {};
        const width = Math.max(320, (preview.clientWidth || 900) - 24);
        const height = Math.max(240, Math.round(availableBottom(preview) - Math.max(0, preview.getBoundingClientRect().top) - 40));
        const byScreen = new Map(screens.map((s) => [String(s.id), s]));
        const ctx = item ? nodeCtx(session, item) : null;
        const pickerState = (session.ifacePicker ||= {})[viewKey] ||= {};
        const allLinks = session.ifaceAllLinks ||= {};
        if (allLinks[viewKey] == null) allLinks[viewKey] = false;
        const panelSource = item ? advancedButton({ title: 'Advanced · page source', text: () => item.content || '', onApply: (text) => applyPageSource(session, item, text) }) : null;
        ensureIfaceLayout(session);
        if (session.ifaceLayoutLoaded) session.ifaceLayoutMounted = true;
        const canvas = createIfaceCanvas({
            model, aspect: Math.min(2.4, Math.max(0.5, width / height)),
            view: session.ifaceView[cameraKey] || null, selectedKey: session.ifaceSel[viewKey] || null,
            pinned: scoped ? {} : session.ifaceLayout || {},
            showAllLinks: allLinks[viewKey],
            onAddAssets: () => root.querySelector('#plan-attachment')?.click(),
            onPin: (nodes) => {
                if (scoped) return;
                session.ifaceLayout = nodes;
                api(path(session, '/interface-layout'), { method: 'PUT', body: { nodes } }).catch(() => {});
            },
            onAddPage: (opener) => addPageDrawer(session, item, { opener }),
            onAddEndpoint: (opener) => addPageDrawer(session, item, { endpoint: true, opener }),
            ...(item && {
                canUndo: history.undo.length > 0, canRedo: history.redo.length > 0,
                onConnect: async (from, to) => {
                    try {
                        const target = byScreen.get(to.screenId), source = byScreen.get(from.screenId);
                        if (!target || !source || item.content !== content) throw new Error('The outline changed. Open the map again before connecting pages.');
                        const next = target.attrs.id ? { content, id: target.attrs.id } : ensureScreenId(content, target.line);
                        const links = { ...from.links, [next.id]: from.links?.[next.id] || '' };
                        const updated = updateNodeMeta(next.content, 'wire', { ...screenNode(source), patch: { links } });
                        await applyPageSource(session, item, updated.content);
                    } catch (error) { host.toast?.(error.message || 'Could not connect pages.', 'error'); }
                },
                onUndo: () => ifaceStep(session, item, 'undo'), onRedo: () => ifaceStep(session, item, 'redo'),
            }),
            onViewChange: (view) => { session.ifaceView[cameraKey] = view; },
            onSelect: (node) => { session.ifaceSel[viewKey] = node?.key || null; },
            onOpen: (node, layer) => { if (!openIfacePage(session, item, node, layer)) canvas.select(node?.key, { focus: true }); },
            panelHtml: (node) => ifacePanelHtml(node, model, pickerState.hasSelection, Boolean(item)),
            onPanel: (box, node) => {
                box.querySelector('[data-iface-journey]')?.addEventListener('click', () => {
                    const { key, route, method, kind, label } = node;
                    journey[viewKey] = { key, route, method, kind, label };
                    delete session.ifaceView[`${viewKey}:${node.key}`];
                    session.ifaceSel[viewKey] = null;
                    renderArtifacts(session);
                });
                box.querySelector('[data-iface-results]')?.addEventListener('click', (e) => openPicker(e.currentTarget));
                box.querySelector('[data-iface-open]')?.addEventListener('click', () => openIfacePage(session, item, node));
                box.querySelector('[data-iface-create]')?.addEventListener('click', (e) => addPageDrawer(session, item, { label: node.label, route: node.route, opener: e.currentTarget }));
                box.querySelector('[data-iface-source]')?.addEventListener('click', () => panelSource.click());
                box.querySelectorAll('[data-iface-follow]').forEach(button => button.addEventListener('click', () => {
                    const key = button.dataset.ifaceFollow;
                    canvas.select(key);
                    canvas.reveal(key, { readable: true });
                    box.scrollTop = 0;
                    box.querySelector('h4').focus({ preventScroll: true });
                }));
                const nodeBtn = box.querySelector('[data-iface-node]');
                if (nodeBtn) {
                    nodeBtn.setAttribute('aria-haspopup', 'dialog');
                    nodeBtn.addEventListener('click', () => { const screen = byScreen.get(node.screenId); if (screen) openNodePopover(nodeBtn, screenNode(screen), ctx); });
                }
            },
        });
        const finderSlot = canvas.dock.querySelector('[data-iface="finder-slot"]');
        const openPicker = mountPagePicker(finderSlot, fullModel.nodes, node => {
            pickerState.hasSelection = true;
            if (!model.byKey.has(node.key)) {
                delete journey[viewKey];
                session.ifaceSel[viewKey] = node.key;
                renderArtifacts(session);
            } else {
                canvas.select(node.key, { focus: true });
                canvas.reveal(node.key, { readable: true });
            }
        }, pickerState);
        if (scoped) {
            const exit = document.createElement('button');
            exit.className = 'header-button iface-exit-journey';
            exit.type = 'button';
            exit.textContent = 'All routes';
            exit.onclick = () => { delete journey[viewKey]; renderArtifacts(session); };
            canvas.el.querySelector('.erd-body').append(exit);
        }
        if (panelSource) canvas.setAdvanced?.(panelSource);
        const docs = choices.length;
        const endpoints = model.nodes.filter((node) => node.kind === 'endpoint').length;
        const guidance = model.nodes.filter((node) => node.lane === 'guidance').length;
        const pages = model.nodes.filter((node) => node.lane !== 'guidance' && node.kind !== 'endpoint').length;
        // Quiet one-line status at the end of the bottom-left dock; the detail lives in its tooltip.
        const countText = `${pages} ${pages === 1 ? 'page' : 'pages'}`;
        const countTitle = [
            `${countText}${endpoints ? ` · ${endpoints} ${endpoints === 1 ? 'endpoint' : 'endpoints'}` : ''}${guidance ? ` · ${guidance} guidance ${guidance === 1 ? 'item' : 'items'}` : ''}${docs ? ` · ${docs} ${docs === 1 ? 'document' : 'documents'}` : ''}`,
            model.hidden?.length ? `${model.hidden.length} placeholder, example, stale or journey-map ${model.hidden.length === 1 ? 'item' : 'items'} hidden` : '',
            provenanceText(item),
        ].filter(Boolean).join('\n');
        canvas.setPageCount?.(countText, countTitle);
        const links = ifaceActions?.querySelector('#plan-all-links');
        if (links) {
            links.hidden = !canvas.canFilterLinks;
            links.setAttribute('aria-pressed', String(Boolean(allLinks[viewKey])));
            links.onclick = () => {
                const show = !allLinks[viewKey];
                allLinks[viewKey] = show;
                canvas.setAllLinks(show);
                links.setAttribute('aria-pressed', String(show));
            };
        }
        preview.classList.add('plan-erd', 'plan-iface');
        const hostEl = document.createElement('div');
        hostEl.className = 'erd-host';
        hostEl.append(canvas.el);
        preview.append(hostEl);
        requestAnimationFrame(() => canvas.mounted());
        session.resetZoom = () => canvas.resetZoom();
        session.ifaceShots = (session.ifaceShots || 0) + 1;
        const shotGeneration = session.ifaceShots;
        session.previewCleanup = () => { session.ifaceShots += 1; session.resetZoom = null; canvas.destroy(); };
        loadIfaceShots(session, model, canvas, shotGeneration);
        const docErrors = (parsed?.diagnostics || []).filter((d) => d.severity === 'error');
        if (docErrors.length) preview.insertAdjacentHTML('beforeend', `<div class="plan-wireframe-diagnostics" role="status"><strong>${docErrors.length} problem(s) in the page outline</strong></div>`);
        return true;
    }

    // "Reload latest (your version is copied)": keep the user's version (clipboard + session copy),
    // drop the stale draft/editor, fetch the workspace and re-render with the newest revision.
    // Databases drawer applied a schema (N5 phase 4): reload, show the ERD it wrote (new per-link ERD or this one).
    async function schemaApplied(session, updated) {
        try { await reload(session); } catch (error) { host.toast?.(`Could not load the updated ERD: ${error?.message || 'unknown error'}`, 'error'); return; }
        if (updated?.id != null) { session.selection ||= {}; session.selection[session.tab] = String(updated.id); }
        renderArtifacts(session);
    }

    async function reloadLatest(session, item, mine, key = item.id) {
        session.conflictCopies ||= {};
        session.conflictCopies[key] = mine;
        let copied = false;
        try { await navigator.clipboard?.writeText(mine); copied = Boolean(navigator.clipboard); } catch { copied = false; }
        delete session.inlineDrafts?.[key];
        const entry = session.erdEditors?.[item.id];
        if (entry) { entry.editor.destroy(); delete session.erdEditors[item.id]; }
        try { await reload(session); } catch (error) { host.toast?.(`Could not load the latest version: ${error?.message || 'unknown error'}`, 'error'); return; }
        renderArtifacts(session);
        host.toast?.(copied ? 'Loaded the latest version. Your version is on the clipboard.' : 'Loaded the latest version. Your version is kept for this session (clipboard unavailable).');
    }

    async function renderSourceAsset(session, asset, preview) {
        const url = safeUrl(asset.url);
        if (!url) { preview.textContent = 'This source is unavailable.'; return; }
        const heading = document.createElement('div');
        heading.className = 'plan-preview-toolbar';
        heading.textContent = asset.name;
        preview.append(heading);
        const original = document.createElement('a');
        original.href = url;
        original.target = '_blank';
        original.rel = 'noopener noreferrer';
        original.textContent = 'Open original';
        heading.append(original);
        if (/^image\//i.test(asset.mime_type || '')) {
            const image = document.createElement('img');
            image.className = 'plan-source-image';
            image.src = url;
            image.alt = asset.name;
            preview.append(image);
        } else if (asset.mime_type === 'application/pdf') {
            try {
                const info = await api(path(session, `/attachments/${encodeURIComponent(asset.id)}/preview-info`));
                if (!preview.isConnected) return;
                const image = document.createElement('img');
                image.className = 'plan-source-image';
                const page = document.createElement('select');
                page.setAttribute('aria-label', 'PDF page');
                page.innerHTML = Array.from({length: info.page_count}, (_, index) => `<option value="${index + 1}">Page ${index + 1} of ${info.page_count}</option>`).join('');
                const show = () => { image.alt = `${asset.name}, page ${page.value}`; image.src = `${url}/preview?page=${page.value}`; };
                page.onchange = show;
                image.onerror = () => { image.alt = 'Preview unavailable. Open the original document.'; };
                if (info.page_count > 1) heading.append(page);
                preview.append(image);
                show();
            } catch (error) {
                if (preview.isConnected) {
                    const message = document.createElement('p');
                    message.textContent = error.message || 'PDF preview is unavailable. Open the original document.';
                    preview.append(message);
                }
            }
        } else {
            const message = document.createElement('p');
            message.textContent = 'This source remains available to the planning agent. Open the original to inspect it.';
            preview.append(message);
        }
    }

    async function reload(session) {
        session.workspace = await api(path(session));
        ensureIfaceLayout(session, { refresh: true });
    }

    // Every completed planning turn (SSE `done`, non-stream POST, send() finally,
    // or the monitor noticing the turn left `running`) must reload the workspace
    // and render artifacts so an open document animates to the applied text.
    async function applyCompletedTurn(session) {
        const prior = session._completedTurn;
        const run = (async () => {
            if (prior) {
                try { await prior; } catch { /* still reload so we are not stuck on a failed first pass */ }
            }
            try { await reload(session); }
            catch (error) { session.error = `Response received, but artifacts could not refresh: ${error.message}`; }
            if (!current(session)) return;
            try { refresh(session); }
            catch (error) {
                console.error(error);
                renderArtifacts(session);
            }
        })();
        session._completedTurn = run;
        return run;
    }

    // SSE `done` can lag behind the server marking the turn complete (buffering, a
    // reader waiting on stream close). Poll independently of `session.pending` so
    // an open document still reloads and animates once the turn is saved.
    function watchTurnCompletion(session, request) {
        session.turnWatch?.stop();
        let stopped = false;
        session.turnWatch = { stop() { stopped = true; } };
        const tick = async () => {
            while (!stopped && current(session) && session.messageRequest?.id === request.id) {
                try {
                    const conversation = await api(conversationPath(session));
                    if (stopped) return;
                    const receipt = messageReceipt(conversation, request);
                    // Only this turn's receipt — conversation.status may already be
                    // complete from another chat and must not stop the watcher early.
                    const status = receipt?.status;
                    if (status && (savedMessageStatuses.includes(status) || status === 'error' || status === 'interrupted')) {
                        session.conversation = conversation;
                        if (conversation.chat_id && !session.chatId) {
                            session.chatId = conversation.chat_id;
                            rememberPlanChat(session);
                        }
                        reconcileMessage(session);
                        await applyCompletedTurn(session);
                        return;
                    }
                } catch { /* retry until the turn settles or send() stops the watch */ }
                await new Promise((resolve) => setTimeout(resolve, 250));
            }
        };
        tick();
    }

    function ensureIfaceLayout(session, { refresh = false } = {}) {
        if (!session?.id) return null;
        if (!refresh && session.ifaceLayoutLoaded) return session.ifaceLayoutPromise || null;
        const previous = session.ifaceLayoutPromise;
        session.ifaceLayoutPromise = api(path(session, '/interface-layout')).then((result) => {
            const incoming = result?.nodes && typeof result.nodes === 'object' ? result.nodes : {};
            session.ifaceLayout = { ...incoming, ...(session.ifaceLayout || {}) };
            session.ifaceLayoutLoaded = true;
            if (!session.ifaceLayoutMounted && Object.keys(incoming).length && current(session) && session.tab === 'wireframes') {
                session.ifaceLayoutMounted = true;
                renderArtifacts(session);
            }
            session.ifaceLayoutMounted = true;
            return session.ifaceLayout;
        }).catch(() => {
            session.ifaceLayout ||= {};
            session.ifaceLayoutLoaded = true;
            session.ifaceLayoutMounted = true;
            return session.ifaceLayout;
        });
        return previous || session.ifaceLayoutPromise;
    }

    function rememberPlanChat(session) {
        rememberChat(tabStore(), session.scope, session.chatId);
        rememberChat(chatStore(), session.id, session.chatId);
    }

    function saveDraft(session) {
        const key = draftKey(session, session.chatId);
        try {
            const store = tabStore();
            if (!store) return false;
            if (!session.draft && !session.attachments.length && !session.messageRequest) {
                store.removeItem(key);
                return true;
            }
            const item = selected(session);
            store.setItem(key, JSON.stringify({
                draft: session.draft, draftVersion: session.draftVersion, messageRequest: session.messageRequest,
                attachmentIds: session.attachments.map(asset => asset.id), viewedAsset: session.viewedAsset,
                provider: session.provider, model: session.model, tab: session.tab, itemId: item?.id,
                pageId: session.pages?.[item?.id],
            }));
            return true;
        } catch { return false; }
    }

    function moveFirstDraft(session) {
        if (!saveDraft(session)) return;
        try { tabStore()?.removeItem(draftKey(session, null)); } catch { /* tab storage unavailable */ }
    }

    function readDraft(session, chatId) {
        try {
            const saved = JSON.parse(tabStore()?.getItem(draftKey(session, chatId)) || 'null');
            if (typeof saved?.draft !== 'string' || !Array.isArray(saved.attachmentIds)) return null;
            const assets = new Map((session.conversation.attachments || []).map(asset => [String(asset.id), asset]));
            saved.attachments = saved.attachmentIds.map(id => assets.get(String(id))).filter(Boolean);
            if (!assets.has(String(saved.viewedAsset))) saved.viewedAsset = null;
            return saved;
        } catch { return null; }
    }

    function applyDraft(session, saved) {
        session.draft = saved?.draft || '';
        session.draftVersion = saved?.draftVersion || 0;
        session.messageRequest = saved?.messageRequest || null;
        session.attachments = [...(saved?.attachments || [])];
        session.viewedAsset = saved?.viewedAsset || null;
        if (current(session)) root.querySelector('#plan-language-input').value = session.draft;
        if (!Object.hasOwn(TABS, saved?.tab)) return;
        session.tab = saved.tab;
        session.selection[saved.tab] = saved.itemId;
        if (saved.itemId && saved.tab === 'wireframes') {
            (session.pages ||= {})[saved.itemId] = saved.pageId || null;
            (session.openPage ||= {})[saved.itemId] = saved.pageId || null;
        }
    }

    async function load(session) {
        if (session.pending) return;
        session.polling?.stop();
        const refreshWorkspace = session.loaded;
        session.pending = 'Loading conversation...';
        session.error = '';
        controls(session);
        try {
            // Conversation and provider list load in parallel (was a sequential waterfall).
            planningProviders ||= api('/llms/available-providers?type=planning').then((data) => data.providers || []).catch((error) => { planningProviders = null; throw error; });
            const previousChat = session.chatId || null;
            let requestedChat = previousChat || rememberedChat(tabStore(), session.scope) || rememberedChat(chatStore(), session.id);
            let [conversation] = await Promise.all([api(conversationPath(session, requestedChat)).catch((error) => {
                if (error?.status !== 404 || !requestedChat) throw error;
                requestedChat = null;                       // remembered chat no longer exists
                return api(conversationPath(session, null));
            }), planningProviders]);
            const active = pickActiveChat(conversation.chats, requestedChat, conversation.chat_id);
            if (active && active !== conversation.chat_id) conversation = await api(conversationPath(session, active));
            const receivedChat = conversation.chat_id || null;
            const currentFirstReceipt = !previousChat && messageReceipt(conversation, session.messageRequest);
            if (session.loaded && previousChat !== receivedChat && !currentFirstReceipt) {
                activateChatConversation(session, conversation);
            } else {
                session.conversation = conversation;
                if (!currentFirstReceipt) session.chatId = receivedChat;
            }
            let firstDraft = false;
            if (!session.loaded && !session.draftVersion) {
                let saved = readDraft(session, session.chatId);
                if (!saved && session.chatId) {
                    const provisional = readDraft(session, null);
                    if (messageReceipt(conversation, provisional?.messageRequest)) {
                        saved = provisional;
                        firstDraft = true;
                    }
                }
                if (saved) {
                    applyDraft(session, saved);
                    session.provider = saved.provider;
                    session.model = saved.model;
                }
            }
            reconcileMessage(session);
            rememberPlanChat(session);
            if (firstDraft) moveFirstDraft(session);
            const remembered = rememberedRoute(routeStore(), session.id);
            // Server default route (settings) only fills a chat that has no route of its own.
            const fallback = conversation.default_route || {};
            session.provider = session.provider || conversation.provider || remembered?.provider || fallback.provider || '';
            session.model = session.model || conversation.model_name || (remembered?.provider === session.provider ? remembered.model : '') || (fallback.provider === session.provider ? fallback.model_name : '') || '';
            session.loaded = true;
            if (current(session)) {
                root.querySelector('#plan-provider').innerHTML = providerOptions(session);
                root.querySelector('#plan-model').innerHTML = modelOptions(session);
            }
            session.providers = await planningProviders;
            if (current(session)) root.querySelector('#plan-provider').innerHTML = providerOptions(session);
            // Models only need the saved provider, so they load alongside the workspace reload.
            await Promise.all([refreshWorkspace ? reload(session) : null, loadModels(session)]);
        } catch (error) { session.error = error.message || 'Could not load conversation. Refresh to retry.'; }
        finally { session.pending = ''; session.pollError = ''; refresh(session); monitor(session); }
    }

    function acknowledgeMessage(session, request) {
        if (session.draftVersion === request.version) {
            session.draft = '';
            if (current(session)) root.querySelector('#plan-language-input').value = '';
        }
        session.attachments = session.attachments.filter((asset) => !request.attachments.includes(asset.id));
        session.messageRequest = null;
        session.error = '';
        saveDraft(session);
    }

    function reconcileMessage(session) {
        const request = session.messageRequest;
        if (!request) return null;
        const receipt = messageReceipt(session.conversation, request);
        if (!receipt) return null;
        const firstChat = !session.chatId && session.conversation.chat_id;
        session.chatId = session.conversation.chat_id || session.chatId;
        rememberPlanChat(session);
        if (savedMessageStatuses.includes(receipt.status)) {
            acknowledgeMessage(session, request);
        } else if (receipt.status === 'error' || receipt.status === 'interrupted') {
            session.messageRequest = null;
            session.error = receipt.status === 'interrupted'
                ? 'The planning turn was interrupted. Your draft is preserved.'
                : 'The assistant could not complete this request. Your draft is preserved.';
        } else {
            session.error = '';
        }
        if (firstChat) moveFirstDraft(session);
        return receipt.status;
    }

    async function send(session) {
        if (blocked(session) || !session.draft.trim() || !modelAvailable(session)) return;
        const attachments = [...new Set([...session.attachments.map((asset) => asset.id), ...(session.viewedAsset ? [Number(session.viewedAsset)] : [])])];
        const selectedItem = session.viewedAsset ? null : selected(session);
        const body = { message: session.draft.trim(), provider: session.provider, model_name: session.model, tab: session.tab, item_id: selectedItem?.id ?? null, page_id: selectedItem?.content_format === 'wire' ? session.pages?.[selectedItem.id] ?? null : null, attachments, chat_id: session.chatId || null, drafts: draftsPayload(session.inlineDrafts) };
        const payload = JSON.stringify(body);
        const previous = session.messageRequest;
        const request = {
            id: previous?.payload === payload ? previous.id
                : (crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-')),
            payload, version: session.draftVersion, attachments,
        };
        session.messageRequest = request;
        session.pending = 'Waiting for assistant...';
        session.streamText = '';
        session.error = '';
        session.outcome = '';
        controls(session);
        refresh(session);
        followSubmittedTurn(session);
        watchTurnCompletion(session, request);
        try {
            let recovered = false;
            try {
                session.conversation = await postPlanMessage(session, { ...body, request_id: request.id });
            } catch (error) {
                session.error = error.message || 'Message failed. Your draft is preserved.';
                recovered = true;
                try {
                    const conversation = await api(conversationPath(session));
                    const sameChat = (conversation.chat_id || null) === (session.chatId || null);
                    if (!sameChat && !messageReceipt(conversation, request)) return;
                    session.conversation = conversation;
                }
                catch { return; }
            }
            const status = reconcileMessage(session);
            if (status) {
                if (!savedMessageStatuses.includes(status)) return;
            } else {
                if (recovered) return;
                const firstChat = !session.chatId && session.conversation.chat_id;
                if (session.conversation.chat_id) {
                    session.chatId = session.conversation.chat_id;
                    rememberPlanChat(session);
                }
                if (session.conversation.status === 'error') {
                    session.messageRequest = null;
                    session.error = 'The assistant could not complete this request. Your draft is preserved.';
                    if (firstChat) moveFirstDraft(session);
                    return;
                }
                acknowledgeMessage(session, request);
                if (firstChat) moveFirstDraft(session);
            }
        } finally {
            session.pending = '';
            stopStreamPaint(session);
            session.turnWatch?.stop();
            session.turnWatch = null;
            try {
                if (session._completedTurn) await session._completedTurn;
                else await applyCompletedTurn(session);
            } catch (error) {
                session.error = session.error || (error.message || 'Artifacts could not refresh.');
            }
            session._completedTurn = null;
            if (current(session)) refresh(session);
            monitor(session);
        }
    }

    async function upload(session, files) {
        if (blocked(session) || !files.length) return;
        session.pending = 'Uploading attachments...';
        session.error = '';
        controls(session);
        try {
            for (const file of files) {
                const body = new FormData();
                body.append('file', file);
                const asset = await api(path(session, '/attachments'), { method: 'POST', body });
                session.attachments.push(asset);
            }
        } catch (error) { session.error = error.message || 'Attachment upload failed. Successfully uploaded files are retained.'; }
        finally { session.pending = ''; refresh(session); }
    }

    // ---- Item 11: drag/drop and paste images into the chat (same upload route as "+") ------------------
    function wireImageDrop(session, zone, input) {
        const overlay = zone.querySelector('#plan-drop-overlay');
        let depth = 0;
        const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes('Files');
        const hide = () => { depth = 0; overlay.hidden = true; overlay.classList.remove('is-invalid'); };
        zone.addEventListener('dragenter', (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            depth += 1;
            const items = [...(event.dataTransfer.items || [])];
            const invalid = blocked(session) || items.some((item) => item.kind === 'file' && item.type && !/^image\/(png|jpeg|webp)$/i.test(item.type));
            overlay.classList.toggle('is-invalid', invalid);
            overlay.querySelector('span').textContent = blocked(session) ? 'Wait for the assistant to finish' : invalid ? 'Only PNG, JPEG or WebP images' : 'PNG, JPEG or WebP, up to 10 MB each';
            overlay.hidden = false;
        });
        zone.addEventListener('dragover', (event) => { if (hasFiles(event)) event.preventDefault(); });
        zone.addEventListener('dragleave', (event) => { if (!hasFiles(event)) return; depth -= 1; if (depth <= 0) hide(); });
        zone.addEventListener('drop', (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            hide();
            attachImages(session, [...event.dataTransfer.files]);
        });
        input.addEventListener('paste', (event) => {
            const files = [...(event.clipboardData?.files || [])];
            if (!files.length) return;                     // plain text paste stays native
            event.preventDefault();
            attachImages(session, files);
        });
    }
    function attachImages(session, files) {
        if (blocked(session)) { session.error = 'Wait for the assistant to finish before attaching images.'; controls(session); return; }
        const { accepted, rejected } = validateImageFiles(files, session.attachments.length);
        session.error = rejectionMessage(rejected);
        if (!accepted.length) { controls(session); return; }
        upload(session, accepted).then(() => { if (rejected.length && !session.error) session.error = rejectionMessage(rejected); controls(session); });
    }

    // ---- Items 9/10: chat switcher, new / rename / archive / restore, clear (undoable) and compact ------------
    function renderChatHeader(session) {
        const header = root.querySelector('#plan-chat-header');
        if (!header) return;
        // Keep the collapse toggle even before the chat list arrives.
        const collapse = header.querySelector('#plan-chat-toggle');
        header.hidden = false;
        header.innerHTML = Array.isArray(session.conversation.chats) ? chatHeaderHtml(session, esc) : '';
        if (collapse) header.prepend(collapse);
        if (!Array.isArray(session.conversation.chats)) return;
        header.querySelectorAll('button:disabled').forEach((button) => { button.dataset.staticDisabled = 'true'; });
        const menu = header.querySelector('#plan-chat-menu');
        const toggle = header.querySelector('#plan-chat-switch');
        const close = () => { menu.classList.add('hidden'); toggle.setAttribute('aria-expanded', 'false'); };
        toggle.onclick = (event) => {
            event.stopPropagation();
            const opening = menu.classList.contains('hidden');
            menu.classList.toggle('hidden', !opening);
            toggle.setAttribute('aria-expanded', String(opening));
            if (opening) {
                const rect = toggle.getBoundingClientRect();
                const viewportPadding = 8;
                const gap = 4;
                const below = window.innerHeight - rect.bottom - gap - viewportPadding;
                const above = rect.top - gap - viewportPadding;
                menu.style.maxHeight = '';
                menu.style.maxWidth = 'calc(100vw - 16px)';
                const openAbove = menu.offsetHeight > below && above > below;
                const available = Math.max(0, openAbove ? above : below);
                menu.style.maxHeight = `min(420px, 70vh, ${Math.floor(available)}px)`;
                menu.style.left = `${Math.max(viewportPadding, Math.min(Math.round(rect.left), window.innerWidth - menu.offsetWidth - viewportPadding))}px`;
                menu.style.top = `${openAbove ? Math.floor(rect.top - gap - menu.offsetHeight) : Math.ceil(rect.bottom + gap)}px`;
                menu.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
            }
        };
        menu.addEventListener('keydown', (event) => { if (event.key === 'Escape') { close(); toggle.focus(); } });
        menu.querySelectorAll('[data-chat-switch]').forEach((button) => { button.onclick = () => { close(); switchChat(session, Number(button.dataset.chatSwitch)); }; });
        menu.querySelectorAll('[data-chat-delete]').forEach((button) => { button.onclick = (event) => { event.stopPropagation(); close(); deleteChat(session, Number(button.dataset.chatDelete)); }; });
        header.querySelector('#plan-chat-new').onclick = () => newChat(session);
    }
    const currentChat = (session) => (session.conversation.chats || []).find((chat) => chat.id === session.chatId) || session.conversation.chat;
    async function chatRequest(session, label, run) {
        if (blocked(session)) return null;
        session.pending = label;
        session.error = '';
        session.outcome = '';
        controls(session);
        try { return await run(); }
        catch (error) {
            session.error = error?.status === 409 ? (error.message || 'This chat changed elsewhere. Reload and try again.') : (error.message || 'The chat action failed.');
            return null;
        } finally { session.pending = ''; }
    }
    async function switchChat(session, chatId) {
        if (!chatId || chatId === session.chatId) return;
        const result = await chatRequest(session, 'Opening chat...', async () => {
            const conversation = await api(`${path(session, '/conversation')}?chat_id=${encodeURIComponent(chatId)}`);
            activateChatConversation(session, conversation);
            await loadModels(session);
            rememberPlanChat(session);
            return conversation;
        });
        refresh(session);
        if (result) { monitor(session); root.querySelector('#plan-language-input')?.focus(); }
    }
    function activateChatConversation(session, conversation) {
        const changed = (conversation.chat_id || null) !== (session.chatId || null);
        if (changed) {
            saveDraft(session);
            session.chatStates ||= new Map();
            session.chatStates.set(String(session.chatId || 'new'), {
                draft: session.draft, draftVersion: session.draftVersion, messageRequest: session.messageRequest,
                attachments: [...session.attachments], viewedAsset: session.viewedAsset || null,
                provider: session.provider, model: session.model,
            });
        }
        session.conversation = conversation;
        session.chatId = conversation.chat_id || null;
        if (!changed) return;
        const saved = session.chatStates.get(String(session.chatId || 'new')) || readDraft(session, session.chatId);
        session.chatStates.delete(String(session.chatId || 'new'));
        const fallback = conversation.default_route || {};
        const remembered = rememberedRoute(routeStore(), session.id);
        applyDraft(session, saved);
        session.provider = saved?.provider || conversation.provider || fallback.provider || remembered?.provider || '';
        session.model = saved?.model || conversation.model_name ||
            (fallback.provider === session.provider ? fallback.model_name : '') ||
            (remembered?.provider === session.provider ? remembered.model : '') || '';
        session.models = [];
        if (current(session)) {
            root.querySelector('#plan-provider').innerHTML = providerOptions(session);
            root.querySelector('#plan-model').innerHTML = modelOptions(session);
            renderArtifacts(session);
        }
        reconcileMessage(session);
    }
    async function openChat(session, chatId) {
        const conversation = await api(chatId ? `${path(session, '/conversation')}?chat_id=${encodeURIComponent(chatId)}` : path(session, '/conversation'));
        const changed = (conversation.chat_id || null) !== (session.chatId || null);
        activateChatConversation(session, conversation);
        if (changed) await loadModels(session);
        rememberPlanChat(session);
    }
    async function newChat(session) {
        await chatRequest(session, 'Starting a new conversation...', async () => {
            const created = await api(path(session, '/conversations'), { method: 'POST', body: {} });
            await openChat(session, created.chat_id);
        });
        refresh(session);
    }
    async function deleteChat(session, chatId) {
        const chat = (session.conversation.chats || []).find((row) => row.id === chatId);
        if (!chatId || !chat) return;
        if (!confirm(`Delete "${chat.title || 'chat'}"?`)) return;
        await chatRequest(session, 'Deleting chat...', async () => {
            const result = await api(path(session, `/conversations/${encodeURIComponent(chatId)}`), { method: 'DELETE' });
            const remaining = (result.chats || []).filter((row) => !row.archived);
            const next = remaining.some((row) => row.id === session.chatId)
                ? session.chatId
                : (remaining.length ? remaining[remaining.length - 1].id : null);
            await openChat(session, next);
        });
        refresh(session);
    }

    function wireToolCards(session, log) {
        log.querySelectorAll('[data-tool-open]').forEach((button) => {
            button.onclick = () => {
                const item = session.workspace.items.find((row) => String(row.id) === button.dataset.toolOpen);
                const tab = item && Object.keys(TABS).find((key) => TABS[key].types.includes(item.item_type));
                if (!tab) return;
                session.selection[tab] = item.id;
                planBar().querySelector(`[data-plan-tab="${tab}"]`).click();
            };
        });
        log.querySelectorAll('[data-tool-undo]').forEach((button) => { button.onclick = () => undoTool(session, cardOf(session, button)); });
        log.querySelectorAll('[data-tool-apply]').forEach((button) => { button.onclick = () => applyAiVersion(session, cardOf(session, button), button); });
        log.querySelectorAll('[data-tool-keep]').forEach((button) => {
            button.onclick = () => { (session.dismissedCards ||= new Set()).add(`${button.dataset.turn}:${button.dataset.card}`); refresh(session); };
        });
    }
    async function undoTool(session, card) {
        if (!card || blocked(session)) return;
        await chatRequest(session, 'Undoing...', async () => {
            await api(path(session, `/plan-items/${encodeURIComponent(card.id)}/restore`), { method: 'POST', body: { revision: card.from_revision, expected_revision: card.to_revision } });
            await reload(session);
            session.outcome = card.from_revision ? `Undone: "${card.title}" is back to revision ${card.from_revision} (saved as a new revision).` : `Undone: "${card.title}" was archived.`;
        });
        refresh(session);
    }
    async function applyAiVersion(session, card, button) {
        if (!card || blocked(session)) return;
        const confirm = window.DecisionsAPI?.confirm;
        const ok = confirm ? Boolean(await confirm({ title: 'Apply AI version', message: `Replace your unsaved changes in "${card.title}" with the AI version? Your draft is discarded; the current saved version stays in history.`, confirmLabel: 'Apply AI version', danger: true })) : true;
        if (!ok) return;
        const item = session.workspace.items.find((row) => String(row.id) === String(card.id));
        await chatRequest(session, 'Applying AI version...', async () => {
            await api(`/workflows/studio/plan-items/${encodeURIComponent(card.id)}`, { method: 'PATCH', body: { content: card.proposed_content, expected_revision: item?.revision_count } });
            for (const key of Object.keys(session.inlineDrafts || {})) if (Number.parseInt(key, 10) === Number(card.id)) delete session.inlineDrafts[key];
            (session.dismissedCards ||= new Set()).add(`${button.dataset.turn}:${button.dataset.card}`);
            await reload(session);
            session.outcome = `Applied the AI version of "${card.title}".`;
        });
        refresh(session);
    }

    // ---- Item 8: memory panel (view, switch on/off, forget chat / project) ------------------------------
    function scanWarnings(items, warnings, previous = {}) {
        const out = { ...(previous || {}) };
        for (const raw of Array.isArray(warnings) ? warnings : []) {
            const m = /^([a-z_]+):\s*(.*)$/s.exec(String(raw));
            const item = m && items.find((i) => i.item_type === m[1]);
            if (!item || !m[2]) continue;
            out[item.id] = [...new Set([...(out[item.id] || []), m[2]])];
        }
        return out;
    }

    function rebuildStatusHtml(percent, label = 'Rebuilding interface') {
        const n = Math.max(0, Math.min(100, Math.round(percent)));
        return `<div class="plan-rebuild-status" role="status">
            <p>${label}</p>
            <div class="plan-rebuild-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${n}" aria-valuetext="${n}%">
                <span class="plan-rebuild-bar" style="width:${n}%"></span>
                <span class="plan-rebuild-pct" aria-hidden="true">${n}%</span>
            </div>
        </div>`;
    }
    function paintRebuildPercent(percent) {
        const n = Math.max(0, Math.min(100, Math.round(percent)));
        const track = root.querySelector('#plan-preview .plan-rebuild-track');
        if (!track) return;
        track.setAttribute('aria-valuenow', String(n));
        track.setAttribute('aria-valuetext', `${n}%`);
        const bar = track.querySelector('.plan-rebuild-bar');
        const label = track.querySelector('.plan-rebuild-pct');
        if (bar) bar.style.width = `${n}%`;
        if (label) label.textContent = `${n}%`;
    }
    // The rebuild route is one POST with no progress events. The bar eases toward 90 while that
    // request is in flight and reaches 100 only when the response arrives.
    function startRebuildProgress(session) {
        clearInterval(session.rebuildTimer);
        session.rebuildPercent = 4;
        session.rebuildTimer = setInterval(() => {
            if (!session.rebuildingInterfaces && !session.rescanningImages) { clearInterval(session.rebuildTimer); session.rebuildTimer = 0; return; }
            const current = session.rebuildPercent || 0;
            if (current >= 90) return;
            session.rebuildPercent = Math.min(90, current + Math.max(0.8, (90 - current) * 0.08));
            paintRebuildPercent(session.rebuildPercent);
        }, 450);
    }
    function concretePageRoute(route) {
        const value = String(route || '');
        if (!/^\/[A-Za-z0-9._~/-]*$/.test(value) || value.includes('..')) return '';
        return value;
    }
    function mountShotEye(canvas, text) {
        if (root.querySelector('.iface-shot-eye') || canvas.el.querySelector('.iface-shot-eye')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'iface-shot-eye';
        button.title = text;
        button.setAttribute('aria-label', text);
        button.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg><span></span>';
        button.querySelector('span').textContent = text;
        const rebuild = root.querySelector('#plan-rebuild-sitemap');
        if (rebuild) rebuild.after(button);
        else (root.querySelector('.plan-iface-actions') || canvas.el.querySelector('.erd-body'))?.append(button);
    }
    async function loadIfaceShots(session, model, canvas, generation) {
        let status;
        const imageUrl = (screenKey) => `/api${path(session, `/interface-shots/${encodeURIComponent(screenKey)}`)}`;
        const shotSlug = (value) => String(value || '').normalize('NFKD').replace(/[^\w\s-]/g, ' ').trim().toLowerCase().replace(/[\s_]+/g, '-').replace(/-+/g, '-').replace(/^[^a-z]+/, '');
        const matchSavedShot = (status, node) => {
            const saved = new Set(status.saved || []);
            const shots = status.shots && typeof status.shots === 'object' ? status.shots : {};
            const route = String(node.route || '').split(/[?#]/)[0].replace(/\/+$/, '');
            const candidates = [node.screenKey, shotSlug(node.label), shotSlug(route), route.replace(/^\/+/, '').replace(/\//g, '-')].filter(Boolean);
            for (const name of candidates) {
                if (shots[name] || ['.png', '.jpg', '.jpeg', '.webp'].some((ext) => saved.has(`${name}${ext}`))) return name;
            }
            return '';
        };
        const shotBlobs = session.ifaceShotBlobs ||= {};
        const shotSrc = async (name) => {
            if (shotBlobs[name]) return shotBlobs[name];
            const headers = {};
            const token = document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';
            if (token) headers['X-DecisionsAI-Internal-Token'] = token;
            try {
                const res = await fetch(imageUrl(name), { headers, credentials: 'same-origin' });
                if (!res.ok) return imageUrl(name);
                const blob = await res.blob();
                if (!blob || !blob.size) return imageUrl(name);
                shotBlobs[name] = URL.createObjectURL(blob);
                return shotBlobs[name];
            } catch {
                return imageUrl(name);
            }
        };
        const paintCardShot = (card, href, capturedAt) => {
            if (!card || !href) return;
            const existing = card.querySelector('img.iface-shot');
            if (existing && (existing.getAttribute('src') === href || existing.dataset.shotSrc === href)) return;
            const img = document.createElement('img');
            img.className = 'iface-shot';
            img.alt = '';
            img.draggable = false;
            img.decoding = 'async';
            img.loading = 'eager';
            img.dataset.shotSrc = href;
            img.src = href;
            if (capturedAt) img.title = `Captured ${capturedAt}`;
            const slot = card.querySelector('.iface-shot');
            if (slot) slot.replaceWith(img);
            else card.querySelector('.iface-node-head')?.insertAdjacentElement('afterend', img);
        };
        let applyingShots = false;
        const applyCachedShots = async (status) => {
            if (applyingShots || !status) return 0;
            applyingShots = true;
            const preview = root.querySelector('#plan-preview');
            const live = preview?.querySelector('.iface-canvas') || (canvas.el.isConnected ? canvas.el : null);
            const shots = status.shots && typeof status.shots === 'object' ? status.shots : {};
            const pages = model.nodes.filter((node) => (node.lane === 'frontend' || node.lane === 'backend') && node.kind !== 'endpoint');
            let applied = 0, skipped = 0, err = '';
            const writeDebug = (extra = '') => {
                const debug = `${applied} applied, ${skipped} unmatched, saved=${(status.saved || []).length}, live=${Boolean(live)}${err ? `, err=${err}` : ''}${extra}`;
                if (live) live.dataset.ifaceShotDebug = debug;
                if (preview) preview.dataset.ifaceShotDebug = debug;
            };
            const cardFor = (node, name) => (live || preview)?.querySelector(`[data-screen-key="${CSS.escape(node.screenKey || name)}"]`)
                || (live || preview)?.querySelector(`[data-screen-key="${CSS.escape(name)}"]`)
                || (live || preview)?.querySelector(`[data-node="${CSS.escape(node.key)}"]`);
            try {
                // Paint API URLs immediately. GET /interface-shots/{id} is a same-origin
                // image/png and does not need a token header, so <img src> works and the
                // browser can decode visible cards first. Waiting to blob-ify every PNG
                // first left cards as placeholders and stalled decode of 2880x1800 shots.
                for (const node of pages) {
                    const name = matchSavedShot(status, node);
                    if (!name) { skipped += 1; continue; }
                    const meta = shots[name];
                    const href = imageUrl(name);
                    if (canvas.el.isConnected) {
                        canvas.setShot(node.key, {
                            image: href,
                            captured_at: meta?.captured_at || '',
                            current: meta ? meta.current !== false : true,
                        });
                    }
                    paintCardShot(cardFor(node, name), href, meta?.captured_at || '');
                    applied += 1;
                }
            } catch (error) {
                err = String(error && error.message || error);
            } finally {
                writeDebug();
                applyingShots = false;
            }
            return applied;
        };
        const watchPreviewShots = () => {
            const preview = root.querySelector('#plan-preview');
            if (!preview || preview.dataset.ifaceShotWatch === '1') return;
            preview.dataset.ifaceShotWatch = '1';
            const remountedCanvas = (mutations) => mutations.some((mutation) => {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1) continue;
                    if (node.matches?.('.iface-canvas, .erd-wrap, .erd-body') || node.querySelector?.('.iface-canvas')) return true;
                }
                return false;
            });
            const obs = new MutationObserver((mutations) => {
                if (applyingShots || !session.ifaceShotStatus) return;
                if (remountedCanvas(mutations)) applyCachedShots(session.ifaceShotStatus);
            });
            obs.observe(preview, { childList: true, subtree: true });
        };
        watchPreviewShots();
        if (session.ifaceShotStatus) applyCachedShots(session.ifaceShotStatus);
        try { status = await api(path(session, '/interface-shots')); }
        catch { return; }
        session.ifaceShotStatus = status;
        await applyCachedShots(status);
        const canFetch = Boolean(status.production_url || (status.terminals_up && (status.frontend_url || status.backend_url)));
        if (!canFetch) {
            const reason = status.reason || 'Images cannot be fetched because the terminals are down. If they were up, this could show the frontend and the backend.';
            mountShotEye(canvas, reason);
            return;
        }
        const pages = model.nodes.filter((node) => (node.lane === 'frontend' || node.lane === 'backend') && node.kind !== 'endpoint');
        for (const node of pages) {
            if (session.ifaceShots !== generation || !canvas.el.isConnected) return;
            if (!node.screenKey || !concretePageRoute(node.route)) continue;
            const name = matchSavedShot(status, node);
            // Reload and Rebuild load cached images only. Recapture happens from "Rescan images".
            if (name || node.shot?.image) continue;
            try {
                const result = await api(path(session, '/interface-shots'), { method: 'POST', body: { screen_id: node.screenKey, route: node.route, lane: node.lane } });
                if (session.ifaceShots !== generation || !canvas.el.isConnected) return;
                if (result.image) canvas.setShot(node.key, {
                    image: result.image,
                    captured_at: result.captured_at || '',
                    current: result.current !== false,
                });
                else if (result.reason) canvas.setShot(node.key, { reason: result.reason });
            } catch { /* leave the card placeholder */ }
        }
    }

    function requireSavedPlan(session, action) {
        const ids = [...Object.keys(session.inlineDrafts || {}).map(key => key.split('#')[0]), ...Object.keys(session.designerDrafts || {})];
        const names = [...new Set((session.workspace.items || []).filter((item) => ids.includes(String(item.id))).map((item) => item.title || 'Document'))];
        if (erdIsDirty(session)) names.push('Data');
        if (!ids.length && !names.length) return true;
        session.planSaveWarning = `Save your changes${names.length ? ` in ${names.join(', ')}` : ''} before ${action}.`;
        session.outcome = '';
        controls(session);
        return false;
    }

    function capturableShotPages(session) {
        const item = (session.workspace?.items || []).find((row) => row.item_type === 'flows' && row.content_format === 'wire')
            || (session.workspace?.items || []).find((row) => row.content_format === 'wire');
        if (!item) return [];
        let parsed;
        try { parsed = parseWireframe(item.content || ''); } catch { return []; }
        const model = buildIfaceModel({
            sitemap: session.workspace?.sitemap,
            screens: parsed?.screens || [],
            captures: pageCaptureFor(session.workspace),
            meta: readSidecar(item.content || '', 'wire').nodes,
            source: item.content || '',
        });
        return model.nodes.filter((node) => (node.lane === 'frontend' || node.lane === 'backend') && node.kind !== 'endpoint' && node.screenKey && concretePageRoute(node.route));
    }

    async function rescanImages(session) {
        if (blocked(session) || session.rebuildingInterfaces || session.rescanningImages || !modelAvailable(session)) return;
        closePlanMenu();
        closeRebuildMenu();
        const pages = capturableShotPages(session);
        session.rescanningImages = true;
        session.rebuildingInterfaces = true;
        session.rebuildStatusLabel = 'Rescanning images';
        session.rebuildPercent = 4;
        session.error = '';
        session.outcome = '';
        controls(session);
        if (current(session)) renderArtifacts(session);
        startRebuildProgress(session);
        try {
            await api(path(session, '/interface-shots/rescan'), { method: 'POST', body: {} });
            for (let i = 0; i < pages.length; i += 1) {
                if (!session.rescanningImages) break;
                const node = pages[i];
                try {
                    await api(path(session, '/interface-shots'), {
                        method: 'POST',
                        body: { screen_id: node.screenKey, route: node.route, lane: node.lane, force: true },
                    });
                } catch { /* leave this page for the next load if capture fails */ }
                session.rebuildPercent = Math.max(session.rebuildPercent || 0, Math.round(((i + 1) / Math.max(pages.length, 1)) * 100));
                paintRebuildPercent(session.rebuildPercent);
            }
            session.rebuildPercent = 100;
            paintRebuildPercent(100);
            session.outcome = pages.length ? `Rescanned ${pages.length} page ${pages.length === 1 ? 'image' : 'images'}.` : 'Cleared cached page images.';
        } catch (error) {
            const message = error.message || 'Could not rescan page images.';
            session.error = /404|405|not found/i.test(message)
                ? 'Rescan images needs a Decisions restart so the new endpoint is available.'
                : message;
        } finally {
            clearInterval(session.rebuildTimer);
            session.rebuildTimer = 0;
            session.rescanningImages = false;
            session.rebuildingInterfaces = false;
            session.rebuildStatusLabel = '';
            refresh(session);
        }
    }

    async function rebuildSitemap(session) {
        if (blocked(session) || session.rebuildingInterfaces || session.rescanningImages || !modelAvailable(session)) return;
        if (!requireSavedPlan(session, 'rebuilding interfaces')) return;
        closePlanMenu();
        closeRebuildMenu();
        session.rebuildingInterfaces = true;
        session.rebuildStatusLabel = 'Rebuilding interface';
        session.rebuildPercent = 4;
        session.error = '';
        session.outcome = '';
        controls(session);
        if (current(session)) renderArtifacts(session);
        startRebuildProgress(session);
        try {
            const result = await api(path(session, '/rebuild-interfaces'), {
                method: 'POST',
                body: { provider: session.provider, model_name: session.model, chat_id: session.chatId || null },
            });
            session.rebuildPercent = 100;
            paintRebuildPercent(100);
            if (result.workspace) session.workspace = result.workspace;
            else await reload(session);
            session.pages = {};
            session.sitemap = true;
            session.outcome = result.message || 'Rebuilt the interfaces and sitemap.';
            // Scan warnings ("flows: …") go to the existing per-item warnings notice of that section.
            session.flowWarnings = scanWarnings(session.workspace?.items || [], result.validation_warnings, session.flowWarnings);
        } catch (error) {
            session.error = error.message || 'Could not rebuild the sitemap.';
        } finally {
            clearInterval(session.rebuildTimer);
            session.rebuildTimer = 0;
            session.rebuildingInterfaces = false;
            session.rebuildStatusLabel = '';
            refresh(session);
        }
    }

    async function build(session) {
        if (blocked(session) || !modelAvailable(session)) return;
        if (!requireSavedPlan(session, 'building tickets')) return;
        session.pending = 'Checking plan...';
        session.error = '';
        session.outcome = '';
        controls(session);
        followSubmittedTurn(session);
        try {
            const result = await api(path(session, '/build'), { method: 'POST', body: { provider: session.provider, model_name: session.model, chat_id: session.chatId || null } });
            if (result.status === 'error') throw new Error(result.error || 'Could not build tickets.');
            session.outcome = result.status === 'needs_input' ? '' : `Tasks: ${result.created} created, ${result.updated || 0} updated, ${result.reused} reused.`;
        } catch (error) { session.error = error.message || 'Could not build tickets.'; }
        finally {
            try { session.conversation = await api(conversationPath(session)); session.chatId = session.conversation.chat_id || session.chatId; }
            catch (error) { session.error = session.error || `Could not refresh build conversation: ${error.message}`; }
            session.pending = '';
            refresh(session);
            monitor(session);
        }
    }

    async function reviewFile(session, item) {
        if (blocked(session)) return;
        session.pending = 'Loading file comparison...';
        controls(session);
        try {
            const endpoint = `/workflows/studio/plan-items/${encodeURIComponent(item.id)}/file-review`;
            const review = await api(endpoint);
            if (!current(session)) return;
            const dialog = document.createElement('dialog');
            dialog.className = 'plan-file-dialog';
            const restoring = review.file_content === null;
            dialog.innerHTML = `<h2>Review file changes</h2><p>${restoring ? 'Restore the saved version to disk.' : 'Import the reviewed project file into this plan.'}</p><div class="plan-file-comparison"><label>Saved in Decisions<pre>${esc(review.saved_content)}</pre></label><label>Project file<pre>${esc(review.file_content ?? 'File is missing.')}</pre></label></div><p role="status"></p><footer><button type="button" data-cancel>Cancel</button><button type="button" data-apply>${restoring ? 'Restore saved file' : 'Import reviewed file'}</button></footer>`;
            root.append(dialog);
            dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
            dialog.addEventListener('close', () => { session.pending = ''; dialog.remove(); refresh(session); }, { once: true });
            dialog.querySelector('[data-apply]').onclick = async () => {
                dialog.querySelector('[data-apply]').disabled = true;
                dialog.querySelector('[data-cancel]').disabled = true;
                const preventClose = (event) => event.preventDefault();
                dialog.addEventListener('cancel', preventClose);
                try {
                    await api(endpoint, { method: 'POST', body: { expected_revision: review.expected_revision, file_hash: review.file_hash, action: restoring ? 'restore' : 'import' } });
                    await reload(session);
                    dialog.close();
                } catch (error) {
                    dialog.querySelector('[role="status"]').textContent = error.message || 'Could not reconcile file.';
                    dialog.querySelector('[data-apply]').disabled = false;
                    dialog.querySelector('[data-cancel]').disabled = false;
                } finally { dialog.removeEventListener('cancel', preventClose); }
            };
            session.pending = 'Review file changes';
            dialog.showModal();
        } catch (error) { session.error = error.message || 'Could not review file.'; session.pending = ''; }
        finally {
            if (!current(session) || !root.querySelector('.plan-file-dialog')) session.pending = '';
            controls(session);
        }
    }

    function open(workspace) {
        const id = String(workspace.id);
        const scope = workspaceScope(workspace);
        let session = sessions.get(scope);
        if (!session) {
            session = { id, scope, workspace, conversation: { messages: [], attachments: [] }, models: null, loaded: false, draft: '', draftVersion: 0, attachments: [], provider: '', model: '', tab: 'wireframes', selection: {}, pending: '', error: '', outcome: '' };
            sessions.set(scope, session);
        } else if (!session.pending) session.workspace = workspace;
        active?.polling?.stop();
        active = session;
        shell(session);
        // Breadcrumb is "<project> / plan"; the active section is shown by the tabs, not repeated here.
        host.setPlanCrumbs?.();
        load(session);
    }
    // Background refresh of an already-open plan: update artifacts without rebuilding the shell.
    // Scan hint lives in the plan toolbar (contextual header area).
    // Red/amber line beside the map status. Redrawn whenever the scan settles, not only on map render.
    function setScanHint(hint) {
        const bar = planBar().querySelector('.plan-detail-header') || planBar();
        let node = bar.querySelector?.('.plan-scan-hint');
        if (active) active.scanHint = hint;
        if (!hint) {
            node?.remove();
            if (active) controls(active);
            return;
        }
        if (active) controls(active);
        if (!node) {
            node = document.createElement('span');
            node.className = 'plan-scan-hint';
            node.setAttribute('role', 'status');
            const end = placePlanHeaderEnd();
            (end || bar).append(node);
            if (end) {
                const chrome = document.getElementById('board-chrome-unit');
                if (chrome && chrome.parentElement === end) end.insertBefore(node, chrome);
            }
        }
        node.dataset.kind = hint.kind || '';
        node.textContent = hint.text || '';
        node.querySelector('.plan-scan-generate')?.remove();
        if (hint.offerGenerate) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'plan-scan-generate';
            button.textContent = 'Generate plan';
            button.addEventListener('click', () => {
                const composer = document.getElementById('plan-composer-input')
                    || document.querySelector('.plan-composer textarea, .plan-composer input, #plan-prompt, [data-plan-composer-input]');
                if (composer) {
                    composer.focus();
                    if ('value' in composer && !String(composer.value || '').trim()) {
                        composer.value = 'Generate a plan from this project.';
                        composer.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }
                document.querySelector('[data-plan-generate], #plan-generate, [data-plan-build]')?.click();
            });
            node.append(' ', button);
        }
    }
    function refreshWorkspace(workspace) {
        const session = sessions.get(workspaceScope(workspace));
        if (!session || session.pending) return;
        session.workspace = workspace;
        if (current(session)) renderArtifacts(session);
    }
    function leave() {
        root.querySelector('.plan-file-dialog')?.close();
        closeDocTabMenu();
        document.getElementById('plan-doc-name-overlay')?.remove();
        bar?.remove();
        bar = null;
        restorePlanHeaderEnd();
        active?.polling?.stop();
        active = null;
    }
    // Unsaved = chat draft/attachments/pending turn, a stored inline draft, or a dirty ERD canvas.
    const hasArtifactDraft = session => Object.keys(session.inlineDrafts || {}).length || Object.keys(session.designerDrafts || {}).length || erdIsDirty(session);
    const hasDraft = () => [...sessions.values()].some((session) => Boolean(session.draft || session.attachments.length || session.pending
        || [...(session.chatStates?.values() || [])].some(chat => chat.draft || chat.attachments.length)
        || hasArtifactDraft(session)));
    addUnsavedCheck(() => [...sessions.values()].some(hasArtifactDraft));
    const animApi = () => window.__planDocEditAnim;
    if (animApi()) {
        animApi().reloadAndRefresh = async () => {
            const session = active;
            if (!session) throw new Error('no active planning session');
            await reload(session);
            refresh(session);
        };
    }

    const hasUnsavedEdits = () => [...sessions.values()].some(hasArtifactDraft);
    return { open, refreshWorkspace, setScanHint, leave, hasDraft, hasUnsavedEdits };
}


