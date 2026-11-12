import { parseWireframe, renderWireframe, explainWireframeDiagnostics } from './wireframe.js';
import { createPlanPolling } from './polling.js';
import { buildResultsHtml } from './build-results.js';
import { layoutConnectedSitemap, pageAccessGroup, SITEMAP_ACCESS_GROUPS } from './sitemap.js?v=20261028-responsive';
import { pageCaptureFor } from './page-captures.js?v=20261003-sitemap-groups';
import { planningProviderLabel } from './routes.js';
import { createInlineEditor, addUnsavedCheck, isRevisionConflict } from './inline-edit/editor.js?v=20261103-ifaces3';
import { markdownToEditableHtml } from './inline-edit/markdown.js';
import { DOCUMENT_ORDER, orderDocuments, documentLabel, openDocument, moveTab, normaliseMode, isUserDocument } from './documents.js?v=20261103-ifaces3';
import { userDocumentsEnabled, USER_DOCUMENT_TYPE } from './documents-config.js?v=20261103-ifaces3';
import { mountErdItem, isErdMermaid, erdIsDirty } from './erd/mount.js?v=20261112-readable-data';
import { availableBottom, fillToWindow, createCallout, advancedButton, pinDock, openDrawer } from './canvas-chrome.js?v=20261103-ifaces3';
import { readSidecar, nodeIds, writeSidecar, slugId } from './flow-meta.js?v=20261103-ifaces3';
import { buildIfaceModel, newScreenSource, SHAPE_KIND, LANES } from './iface-model.js?v=20261112-readable-map';
import { createIfaceCanvas } from './iface-canvas.js?v=20261112-map-pan';
import { mountPagePicker } from './page-picker.js?v=20261112-route-details';
import { decorateNode, nodeButton, openNodePopover, openNodePicker, closeNodePopover, warningsNotice } from './flow-nodes.js?v=20261103-ifaces3';
import { rememberedRoute, rememberRoute, routeStore } from './route-memory.js?v=20261102-verify';
import { chatHeaderHtml, chatStore, rememberChat, rememberedChat, pickActiveChat, validateImageFiles, rejectionMessage, isImageAsset, imageLabel } from './chat-header.js?v=20261103-chat2';
import { toolCardsHtml, draftsPayload, memoryPanelHtml } from './chat-tools.js?v=20261103-chat2';

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

    function monitor(session) {
        session.polling?.stop();
        if (!current(session) || session.pending || !['running', 'pending', 'processing'].includes(session.conversation.status)) return;
        session.polling = createPlanPolling({
            active: () => current(session) && !session.pending,
            read: () => api(conversationPath(session)),
            apply: async (conversation, valid) => {
                const running = ['running', 'pending', 'processing'].includes(conversation.status);
                if (!running) {
                    const workspace = await api(path(session));
                    if (!valid()) return false;
                    session.workspace = workspace;
                }
                const changed = JSON.stringify(session.conversation) !== JSON.stringify(conversation);
                session.conversation = conversation;
                reconcileMessage(session);
                session.pollError = '';
                if (changed || !running) refresh(session);
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
    }

    // Interfaces actions live on the canvas (not the header). Held here so a preview re-render cannot drop them.
    let ifaceActions = null;

    // ⋯ menu (same .board-context-menu popover as the thread header menu). Fixed under its button.
    function closePlanMenu() {
        const menu = ifaceActions?.querySelector('#plan-header-menu');
        if (!menu || menu.classList.contains('hidden')) return;
        menu.classList.add('hidden');
        ifaceActions.querySelector('#plan-more-button')?.setAttribute('aria-expanded', 'false');
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
            if (!event.target.closest?.('#plan-chat-menu') && !event.target.closest?.('#plan-chat-switch')) {
                root.querySelector('#plan-chat-menu')?.classList.add('hidden');
                root.querySelector('#plan-chat-switch')?.setAttribute('aria-expanded', 'false');
            }
        });
        document.addEventListener('keydown', (event) => {
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
    function interfacesSurface(preview, stats, hint) {
        preview.classList.add('plan-canvas-surface');
        const callout = createCallout({ key: 'interfaces', label: 'Interfaces summary', stats, hint });
        preview.prepend(callout);   // first child: the sticky, floated call-out pins to the surface's top right
        const keep = new MutationObserver(() => { if (preview.firstElementChild !== callout) preview.prepend(callout); });
        keep.observe(preview, { childList: true });
        const unfill = fillToWindow(preview);
        return () => { keep.disconnect(); unfill?.(); };
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
        const userActions = userDocs ? `<button type="button" class="plan-doc-add" id="plan-doc-add" aria-label="Add document" title="Add document">+</button>${open && isUserDocument(open) ? `<button type="button" class="plan-doc-rename" id="plan-doc-rename" aria-label="Rename ${esc(open.title || 'document')}" title="Rename">Rename</button><button type="button" class="plan-doc-delete" id="plan-doc-delete" aria-label="Delete ${esc(open.title || 'document')}" title="Delete">Delete</button>` : ''}` : '';
        return `<div class="plan-docs-bar">
            <div class="plan-doc-tabs" role="tablist" aria-label="Documents">${tabs}</div>${userActions}
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
        if (add) add.onclick = async () => {
            add.disabled = true;
            try {
                const created = await api(path(session, '/items'), { method: 'POST', body: { item_type: USER_DOCUMENT_TYPE, title: 'Untitled document', content: '# Untitled document\n\n', content_format: 'markdown' } });
                session.workspace.items = [...(session.workspace.items || []), created];
                session.selection.requirements = created.id;
                renderArtifacts(session);
                renameDocument(session, created);
            } catch (error) { host.toast?.(`Could not add the document: ${error?.message || 'unknown error'}`, 'error'); add.disabled = false; }
        };
        const rename = panel.querySelector('#plan-doc-rename');
        if (rename && open) rename.onclick = () => renameDocument(session, open);
        const remove = panel.querySelector('#plan-doc-delete');
        if (remove && open) remove.onclick = () => deleteDocument(session, open);
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
    function renameDocument(session, doc) {
        if (!userDocumentsEnabled() || !isUserDocument(doc)) return;
        if (session.inlineDrafts?.[doc.id]) { host.toast?.('Save or undo your changes to this document before renaming it.', 'error'); return; }
        const tab = root.querySelector(`.plan-doc-tab[data-doc="${CSS.escape(String(doc.id))}"]`);
        if (!tab) return;
        const input = document.createElement('input');
        input.className = 'plan-doc-rename-input';
        input.value = doc.title || '';
        input.setAttribute('aria-label', 'Document name');
        tab.replaceWith(input);
        input.focus(); input.select();
        let done = false;
        const finish = async (commit) => {
            if (done) return; done = true;
            const title = input.value.trim();
            if (commit && title && title !== doc.title) {
                try {
                    const updated = await api(`/workflows/studio/plan-items/${encodeURIComponent(doc.id)}`, { method: 'PATCH', body: { title, expected_revision: revisionOf(doc) } });
                    Object.assign(doc, updated);
                } catch (error) { host.toast?.(`Could not rename: ${error?.message || 'unknown error'}`, 'error'); }
            }
            renderArtifacts(session);
            root.querySelector(`.plan-doc-tab[data-doc="${CSS.escape(String(doc.id))}"]`)?.focus();
        };
        input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } else if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
        input.addEventListener('blur', () => finish(true));
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
        root.innerHTML = `<div class="plan-detail">
            <header class="plan-detail-header">
                <strong class="plan-project" title="${esc(session.workspace.board_name)}">${esc(session.workspace.board_name)}</strong>
                <nav class="plan-tabs" aria-label="Plan sections">${Object.entries(TABS).map(([tab, info]) => `<button type="button" data-plan-tab="${tab}" title="${esc(info.help || info.label)}" aria-pressed="${session.tab === tab}">${info.label}</button>`).join('')}</nav>
            </header>
            <div class="plan-detail-body">
                <section class="plan-artifacts" id="plan-artifacts" aria-label="Artifacts"></section>
                <section class="plan-conversation" aria-label="Plan conversation">
                    <header class="plan-chat-header" id="plan-chat-header" aria-label="Chat"></header>
                    <div class="composer-drop-overlay plan-drop-overlay" id="plan-drop-overlay" hidden><strong>Drop images to attach</strong><span>PNG, JPEG or WebP, up to 10 MB each</span></div>
                    <div class="plan-messages" id="plan-messages" role="log" aria-label="Messages" aria-live="polite"></div>
                    <div class="plan-composer-wrap">
                        <div id="plan-operation-status" role="status" aria-live="polite"></div>
                        <div class="plan-context-hint" id="plan-context-hint" hidden></div>
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
                </section>
            </div>
        </div>`;
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
        ifaceActions = document.createElement('div');
        ifaceActions.className = 'plan-iface-actions';
        ifaceActions.id = 'plan-header-actions';
        ifaceActions.setAttribute('role', 'group');
        ifaceActions.setAttribute('aria-label', 'Interface actions');
        ifaceActions.innerHTML = `<button type="button" class="header-button" id="plan-rebuild-sitemap">Rebuild</button>`;
        ifaceActions.querySelector('#plan-rebuild-sitemap').onclick = () => { closePlanMenu(); rebuildSitemap(session); };
        // Build tickets was retired from Plan (2026-11-11); the build route remains for existing tooling.
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
        const rebuild = ifaceActions?.querySelector('#plan-rebuild-sitemap');
        if (!rebuild) return;
        rebuild.textContent = session.rebuildingInterfaces ? 'Rebuilding' : 'Rebuild';
        rebuild.disabled = blocked(session) || session.rebuildingInterfaces || !modelAvailable(session);
    }

    function controls(session) {
        if (!current(session)) return;
        if (session.loaded) saveDraft(session);
        if (!Object.keys(session.inlineDrafts || {}).length && !erdIsDirty(session)) session.planSaveWarning = '';
        const busy = blocked(session);
        root.querySelector('#plan-send').disabled = busy || !modelAvailable(session) || !session.draft.trim();
        const buildButton = planBar().querySelector('#plan-build');
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
        const status = root.querySelector('#plan-operation-status');
        status.replaceChildren();
        if (session.scanHint && session.scanHint.kind !== 'scanning') {
            const hint = document.createElement('p');
            hint.className = 'plan-scan-hint';
            hint.dataset.kind = session.scanHint.kind;
            hint.textContent = session.scanHint.text;
            status.append(hint);
        }
        if (session.error || session.planSaveWarning) {
            const error = document.createElement('p');
            error.className = 'plan-error';
            error.setAttribute('role', 'alert');
            error.textContent = session.error || session.planSaveWarning;
            status.append(error);
        }
        const persistedStatus = ['running', 'pending', 'processing'].includes(session.conversation.status) ? 'Assistant is working...' : '';
        const note = session.pending || session.pollError || (!session.loaded && !session.error ? 'Loading conversation...' : '') || session.outcome || persistedStatus;
        if (note) {
            const text = document.createElement('p');
            text.textContent = String(note);
            status.append(text);
        }
        const log = root.querySelector('#plan-messages');
        const latestBuild = Array.from(log.querySelectorAll('.plan-build-results')).at(-1);
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
        if (session.undoClear && !session.pending) {
            const undo = document.createElement('button');
            undo.type = 'button';
            undo.id = 'plan-chat-undo-clear';
            undo.textContent = 'Undo clear';
            undo.onclick = () => undoClear(session);
            status.append(undo);
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
        }).join('') || (session.loaded
            ? `<div class="plan-conversation-empty">${allStarters(session)
                ? `<p><strong>Start from the project</strong></p><ol class="plan-empty-steps"><li>Pages map themselves from HTML templates, views, and URLs</li><li>Read the sitemap in Public, Account, and Signed in</li><li>Tighten requirements, then ask for build tickets</li></ol><p class="plan-empty-hint">Planning is the rapid-sense hub: pages → production.</p>`
                : `<p><strong>No messages yet</strong></p><p class="plan-empty-hint">Start with the pages, then define requirements and data before building tasks.</p>`}</div>`
            : '');
        wireToolCards(session, log);
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
        if (nearBottom) log.scrollTop = log.scrollHeight;
    }

    function followSubmittedTurn(session) {
        if (!current(session)) return;
        const log = root.querySelector('#plan-messages');
        log.scrollTop = log.scrollHeight;
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
                if (depth < 1) walk(child, depth + 1);
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

    function renderArtifacts(session) {
        if (!current(session)) return;
        ifaceActions?.remove();
        session.previewCleanup?.();
        session.previewCleanup = null;
        const panel = root.querySelector('#plan-artifacts');
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
        const hint = root.querySelector('#plan-context-hint');
        if (hint) { hint.hidden = !(docsTab && item); hint.textContent = docsTab && item ? `Context: ${documentLabel(item, choices)}` : ''; }
        panel.innerHTML = `${docsTab ? documentsBarHtml(session, choices, item) : choices.length > 1 || showDiscoveryChoice ? `<label class="plan-artifact-picker"><select id="plan-artifact-select" aria-label="Select artifact">${choices.map((choice) => `<option value="${esc(choice.id)}"${String(choice.id) === String(item?.id) ? ' selected' : ''}>${esc(choice.title)}${choice.is_starter ? ' · needs content' : ''}</option>`).join('')}${discoveryOption}</select></label>` : ''}
            ${item && !session.viewedAsset && (item.is_starter || item.origin_label) ? `<div class="plan-artifact-revision" data-plan-provenance="${esc(item.origin || '')}">${item.is_starter ? 'Not generated yet · Plan fills this from the project scan or the approved design' : esc(`${item.origin_label}${item.last_generated_at ? ` · ${new Date(`${item.last_generated_at}Z`).toLocaleString()}` : ''}${item.db_only ? ' · stored in Decisions only' : ''}`)}</div>` : ''}
            ${item?.file_sync_error ? `<div class="plan-file-status" role="alert">${esc(item.file_sync_error)}<button type="button" id="plan-review-file">Review file changes</button></div>` : ''}
            <div class="plan-preview" id="plan-preview"></div>
            ${assets.length ? `<div class="plan-assets" aria-label="Assets">${assetsHtml(assets)}</div>` : ''}`;
        if (docsTab) wireDocumentsBar(session, panel, choices, item);
        panel.querySelector('#plan-artifact-select')?.addEventListener('change', (event) => { session.viewedAsset = null; session.selection[session.tab] = event.target.value; renderArtifacts(session); });
        panel.querySelectorAll('[data-view-asset]').forEach(button => {
            button.onclick = () => { session.viewedAsset = button.dataset.viewAsset; renderArtifacts(session); };
        });
        const review = panel.querySelector('#plan-review-file');
        if (review) { review.disabled = Boolean(session.pending); review.onclick = () => reviewFile(session, item); }
        const preview = panel.querySelector('#plan-preview');
        if (session.tab === 'wireframes' && session.rebuildingInterfaces) {
            preview.innerHTML = rebuildStatusHtml(session.rebuildPercent || 0);
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
            preview.innerHTML = `<div class="plan-canvas-empty"><p><strong>No ${esc(TABS[session.tab].label.toLowerCase())} yet</strong></p><p class="plan-empty-hint">${esc(tips[session.tab] || 'Ask Plan to fill this section.')}</p></div>`;
            if (session.tab === 'wireframes') session.previewCleanup = interfacesSurface(preview, '0 pages, 0 documents', 'Ask Plan to draft a page outline, or rescan the project to fill the sitemap.');
            parkIfaceActions(session, preview);
            return;
        }
        session.previewCleanup?.();
        session.previewCleanup = null;
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
                    divider,
                    mk('erd-dock-button', 'Undo', 'Undo', ico('M6 4 3 7l3 3M3.5 7H10a3 3 0 0 1 0 6H8'), () => ifaceStep(session, item, 'undo'), !h.undo.length),
                    mk('erd-dock-button', 'Redo', 'Redo', ico('M10 4l3 3-3 3M12.5 7H6a3 3 0 0 0 0 6h2'), () => ifaceStep(session, item, 'redo'), !h.redo.length));
            }
            ifaceTools.append(advancedButton({ title: 'Advanced · page source', text: () => item.content || '', onApply: (text) => applyPageSource(session, item, text) }));
            panel.append(ifaceTools);
        }
        if (session.tab === 'wireframes') {
            let pages = 0, guidance = 0;
            try {
                const screens = item.content_format === 'wire' ? (parseWireframe(item.content).screens || []) : [];
                guidance = screens.filter(screen => String(screen.attrs.layer || '').toLowerCase() === 'guidance').length;
                pages = screens.length - guidance;
            } catch { pages = 0; }
            const stats = `${pages} ${pages === 1 ? 'page' : 'pages'}${guidance ? ` · ${guidance} guidance ${guidance === 1 ? 'item' : 'items'}` : ''}, ${choices.length} ${choices.length === 1 ? 'document' : 'documents'}`;
            const opened = Boolean(session.openPage?.[item.id]);
            const surfaceCleanup = interfacesSurface(preview, opened ? 'Page outline' : stats, opened ? 'Switch perspective with Preview / Planning / Development, or go back with All pages.' : 'Switch perspective with Preview / Planning / Development above the page.');
            const dockCleanup = ifaceTools ? pinDock(ifaceTools, preview) : null;
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
                if (page) {
                    const toolbar = document.createElement('div');
                    toolbar.className = 'plan-preview-toolbar';
                    const layerButtons = `<div class="plan-page-layers" role="tablist" aria-label="Page perspective">
                        <button type="button" data-layer="preview" aria-pressed="${layer === 'preview'}">Preview</button>
                        <button type="button" data-layer="planning" aria-pressed="${layer === 'planning'}">Planning</button>
                        <button type="button" data-layer="development" aria-pressed="${layer === 'development'}">Development</button>
                       </div>`;
                    toolbar.innerHTML = `${openId ? '<button type="button" class="iface-back" data-iface-back aria-label="Back to all pages">← All pages</button>' : ''}
                        ${openId && session.ifacePicker?.[item.id]?.hasSelection ? '<button type="button" data-iface-page-results>Back to results</button>' : ''}
                        ${layer === 'preview' ? `<button type="button" aria-pressed="${session.previewModes[item.id] === 'html'}">${session.previewModes[item.id] === 'html' ? 'Interactive' : 'Stencil'}</button>` : ''}${layerButtons}`;
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
                        return panel.querySelector('.plan-find-page');
                    }
                    toolbar.querySelector('[data-iface-back]')?.addEventListener('click', () => {
                        returnToMap()?.focus({ preventScroll: true });
                    });
                    toolbar.querySelector('[data-iface-page-results]')?.addEventListener('click', () => returnToMap()?.click());
                    toolbar.querySelectorAll('[data-layer]').forEach(button => {
                        button.onclick = () => { session.pageLayers[item.id] = button.dataset.layer; renderArtifacts(session); };
                    });
                    const htmlToggle = toolbar.querySelector('button[aria-pressed]:not([data-layer])');
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

                const result = renderWireframe(item.content, item.title, page?.id, previewAssets);
                preview.innerHTML = `<div class="plan-wireframe-preview">${result.svg}</div>${diagnosticsHtml(result.diagnostics)}`;
                if (session.previewModes[item.id] === 'html') {
                    const frame = document.createElement('iframe');
                    frame.title = page?.label || item.title || 'Page preview';
                    frame.setAttribute('sandbox', '');
                    frame.referrerPolicy = 'no-referrer';
                    frame.srcdoc = result.html;
                    preview.querySelector('.plan-wireframe-preview').replaceWith(frame);
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
            preview.append(inlineEditorFor(session, item, raw ? 'markdown-raw' : 'markdown', item.title || 'Document', () => markDocTabs(session)).el);
        }
        parkIfaceActions(session, preview);
    }

    // Top-right of the interfaces container, same right inset as the zoom dock. Hidden on other plan sections.
    function parkIfaceActions(session, preview) {
        if (!ifaceActions) return;
        if (session.tab !== 'wireframes' || !preview?.isConnected) { ifaceActions.remove(); return; }
        const anchor = preview.querySelector('.iface-navigation') || preview.querySelector('.erd-body');
        if (!anchor) {
            const callout = preview.querySelector(':scope > .plan-canvas-callout');
            if (callout) callout.after(ifaceActions);
            else preview.prepend(ifaceActions);
        } else anchor.append(ifaceActions);
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
            if (error?.status === 409 || isRevisionConflict(error)) throw new Error('This page has a newer revision. Your text is kept here; copy it, then reload the page to see the latest version.');
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
    function addPageDrawer(session, item, { label = '', route = '', opener = document.activeElement } = {}) {
        const { drawer, close } = openDrawer({ title: label ? 'Create page outline' : 'Add page', opener, className: 'iface-add-drawer', html: `
            <form class="iface-add-form">
                <label>Page name<input name="label" required maxlength="80" value="${esc(label)}" autocomplete="off"></label>
                <label>Route<input name="route" maxlength="200" placeholder="/example" value="${esc(route)}" autocomplete="off"></label>
                <p class="plan-empty-hint">Adds a page outline to this Interfaces item (a new revision; the server validates it).</p>
                <footer class="plan-drawer-foot"><p class="plan-drawer-error" role="alert" hidden></p>
                    <button type="button" class="header-button plan-drawer-cancel">Cancel</button>
                    <button type="submit" class="header-button primary">${label ? 'Create outline' : 'Add page'}</button></footer>
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
                try { taken = (parseWireframe(item.content || '').screens || []).map((s) => s.attrs?.id).filter(Boolean); } catch { taken = []; }
                const id = slugId(name, taken);
                const sidecar = readSidecar(item.content || '', 'wire');
                const body = (sidecar.error ? (item.content || '') : sidecar.body).replace(/\s+$/, '');
                const added = `${body ? `${body}\n` : ''}${newScreenSource({ label: name, route: form.elements.route.value, id })}`;
                const content = !sidecar.error && Object.keys(sidecar.nodes).length ? writeSidecar(added, 'wire', sidecar.nodes) : added;
                let opened = false;
                await applyPageSource(session, item, content, () => {
                    if (!label || !current(session) || session.tab !== 'wireframes' || selected(session) !== item
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
                error.textContent = e?.message || 'Could not add the page.';
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
            ${hasOutlineItem || fromPicker ? `<div class="erd-panel-actions iface-panel-actions">
                ${hasOutlineItem ? (node.screenId ? '<button type="button" class="header-button primary" data-iface-open>Open page</button><button type="button" class="header-button" data-iface-node>Name, id, shape…</button>'
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
    function openIfacePage(session, item, node) {
        if (!node?.screenId) return false;
        session.openPage ||= {};
        session.openPage[item.id] = node.screenId;
        session.pageLayers ||= {};
        session.pageLayers[item.id] = 'preview';
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
        const model = buildIfaceModel({ sitemap, screens: mapScreens, captures: pageCaptureFor(session.workspace), meta: readSidecar(content, 'wire').nodes, source: content });
        if (!model.nodes.length) return false;
        if (screens.length === 1 && mapScreens.length === 1 && model.nodes.length === 1) return false;
        if (item) (session.pages ||= {})[item.id] = null;
        const history = item ? ifaceHistory(session, item) : null;
        session.ifaceView ||= {}; session.ifaceSel ||= {};
        const width = Math.max(320, (preview.clientWidth || 900) - 24);
        const height = Math.max(240, Math.round(availableBottom(preview) - Math.max(0, preview.getBoundingClientRect().top) - 40));
        const byScreen = new Map(screens.map((s) => [String(s.id), s]));
        const ctx = item ? nodeCtx(session, item) : null;
        const pickerState = (session.ifacePicker ||= {})[viewKey] ||= {};
        const allLinks = session.ifaceAllLinks ||= {};
        const panelSource = item ? advancedButton({ title: 'Advanced · page source', text: () => item.content || '', onApply: (text) => applyPageSource(session, item, text) }) : null;
        const canvas = createIfaceCanvas({
            model, aspect: Math.min(2.4, Math.max(0.5, width / height)),
            view: session.ifaceView[viewKey] || null, selectedKey: session.ifaceSel[viewKey] || null,
            showAllLinks: allLinks[viewKey],
            ...(item && {
                canUndo: history.undo.length > 0, canRedo: history.redo.length > 0,
                onAddPage: (opener) => addPageDrawer(session, item, { opener }),
                onUndo: () => ifaceStep(session, item, 'undo'), onRedo: () => ifaceStep(session, item, 'redo'),
            }),
            onViewChange: (view) => { session.ifaceView[viewKey] = view; },
            onSelect: (node) => { session.ifaceSel[viewKey] = node?.key || null; },
            onOpen: (node) => { if (!openIfacePage(session, item, node)) canvas.select(node?.key, { focus: true }); },
            panelHtml: (node) => ifacePanelHtml(node, model, pickerState.hasSelection, Boolean(item)),
            onPanel: (box, node) => {
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
        if (panelSource) canvas.dock.append(panelSource);
        else canvas.dock.remove();
        const docs = choices.length;
        const guidance = model.nodes.filter(node => node.lane === 'guidance').length;
        const pages = model.nodes.length - guidance;
        const callout = createCallout({ key: 'interfaces', label: 'Interfaces summary',
            stats: `${pages} ${pages === 1 ? 'page' : 'pages'}${guidance ? ` · ${guidance} guidance ${guidance === 1 ? 'item' : 'items'}` : ''} · ${model.outlined} with outlines · ${docs} ${docs === 1 ? 'document' : 'documents'}`,
            hint: `Use Find page to search by name, route or template.${model.note ? ' Route discovery is partial.' : ''}` });
        if (model.note) callout.title = model.note;
        const header = document.createElement('div');
        header.className = 'iface-map-header';
        const navigation = document.createElement('div');
        navigation.className = 'iface-navigation';
        header.append(callout, navigation);
        canvas.el.prepend(header);
        const openPicker = mountPagePicker(navigation, model.nodes, (node) => {
            pickerState.hasSelection = true;
            if (openIfacePage(session, item, node)) {
                panel.querySelector('[data-iface-back]')?.focus({ preventScroll: true });
            } else {
                canvas.select(node.key, { focus: true });
                canvas.reveal(node.key, { readable: true });
            }
        }, pickerState);
        if (canvas.canFilterLinks) {
            const links = document.createElement('button');
            links.type = 'button';
            links.className = 'header-button iface-all-links';
            links.textContent = 'Show all links';
            links.setAttribute('aria-pressed', String(Boolean(allLinks[viewKey])));
            links.addEventListener('click', () => {
                const show = !allLinks[viewKey];
                allLinks[viewKey] = show;
                canvas.setAllLinks(show);
                links.setAttribute('aria-pressed', String(show));
            });
            navigation.append(links);
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
        session.undoClear = null;
        session.pending = 'Waiting for assistant...';
        session.error = '';
        session.outcome = '';
        controls(session);
        followSubmittedTurn(session);
        try {
            let recovered = false;
            try {
                session.conversation = await api(path(session, '/messages'), { method: 'POST', body: { ...body, request_id: request.id } });
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
            try { await reload(session); }
            catch (error) { session.error = `Response received, but artifacts could not refresh: ${error.message}`; }
        } finally { session.pending = ''; refresh(session); monitor(session); }
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
        // A server without multi-chat (Python not restarted yet) returns no `chats`: keep the single-chat UI.
        header.hidden = !Array.isArray(session.conversation.chats);
        if (header.hidden) { header.replaceChildren(); return; }
        header.innerHTML = chatHeaderHtml(session, esc);
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
        menu.querySelectorAll('[data-chat-restore]').forEach((button) => { button.onclick = () => { close(); chatAction(session, Number(button.dataset.chatRestore), 'restore', 'Chat restored.', true); }; });
        header.querySelector('#plan-chat-rename').onclick = () => { close(); renameChat(session); };
        header.querySelector('#plan-chat-archive').onclick = () => { close(); archiveChat(session); };
        header.querySelector('#plan-chat-memory').onclick = () => { close(); openMemory(session); };
        header.querySelector('#plan-chat-new').onclick = () => newChat(session);
        header.querySelector('#plan-chat-compact').onclick = () => chatAction(session, session.chatId, 'compact', 'Compacted: earlier turns are now a summary for the assistant.');
        header.querySelector('#plan-chat-clear').onclick = () => clearChat(session);
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
            session.undoClear = null;
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
        await chatRequest(session, 'Starting a new chat...', async () => {
            const created = await api(path(session, '/chats'), { method: 'POST', body: {} });
            session.undoClear = null;
            await openChat(session, created.chat_id);
            session.outcome = 'New chat started.';
        });
        refresh(session);
    }
    async function chatAction(session, chatId, action, done, open = false) {
        const chat = (session.conversation.chats || []).find((row) => row.id === chatId);
        if (!chatId) return;
        await chatRequest(session, 'Updating chat...', async () => {
            await api(path(session, `/chats/${encodeURIComponent(chatId)}/${action}`), { method: 'POST', body: { expected_revision: chat?.revision ?? null } });
            await openChat(session, open ? chatId : session.chatId);
            session.outcome = done;
        });
        refresh(session);
    }
    async function clearChat(session) {
        const chat = currentChat(session);
        if (!chat) return;
        await chatRequest(session, 'Clearing chat...', async () => {
            const result = await api(path(session, `/chats/${encodeURIComponent(chat.id)}/clear`), { method: 'POST', body: { expected_revision: chat.revision ?? null } });
            await openChat(session, result.chat_id);
            session.undoClear = { archived: result.archived_chat_id, fresh: result.chat_id };
            session.outcome = `"${chat.title}" was archived; this is a fresh chat.`;
        });
        refresh(session);
    }
    async function undoClear(session) {
        const undo = session.undoClear;
        if (!undo) return;
        await chatRequest(session, 'Restoring chat...', async () => {
            await api(path(session, `/chats/${encodeURIComponent(undo.archived)}/restore`), { method: 'POST', body: {} });
            const fresh = (session.conversation.chats || []).find((row) => row.id === undo.fresh);
            // The fresh chat is archived again only while it is still empty (never loses a message).
            if (fresh && !fresh.messages) await api(path(session, `/chats/${encodeURIComponent(undo.fresh)}/archive`), { method: 'POST', body: { expected_revision: fresh.revision } });
            session.undoClear = null;
            await openChat(session, undo.archived);
            session.outcome = 'Chat restored.';
        });
        refresh(session);
    }
    async function archiveChat(session) {
        const chat = currentChat(session);
        if (!chat) return;
        const confirm = window.DecisionsAPI?.confirm;
        const ok = confirm ? Boolean(await confirm({ title: 'Archive chat', message: `Archive "${chat.title}"? It moves to Archived in the chat list and can be restored.`, confirmLabel: 'Archive' })) : true;
        if (!ok) return;
        await chatRequest(session, 'Archiving chat...', async () => {
            await api(path(session, `/chats/${encodeURIComponent(chat.id)}/archive`), { method: 'POST', body: { expected_revision: chat.revision ?? null } });
            await openChat(session, null);
            session.undoClear = null;
            session.outcome = `"${chat.title}" archived.`;
        });
        refresh(session);
    }
    function renameChat(session) {
        const chat = currentChat(session);
        const button = root.querySelector('#plan-chat-switch');
        if (!chat || !button || blocked(session)) return;
        const input = document.createElement('input');
        input.className = 'plan-chat-rename-input';
        input.value = chat.title || '';
        input.maxLength = 120;
        input.setAttribute('aria-label', 'Chat name');
        button.replaceWith(input);
        input.focus(); input.select();
        let finished = false;
        const finish = async (commit) => {
            if (finished) return; finished = true;
            const title = input.value.trim();
            if (commit && title && title !== chat.title) {
                await chatRequest(session, 'Renaming chat...', async () => {
                    await api(path(session, `/chats/${encodeURIComponent(chat.id)}`), { method: 'PATCH', body: { title, expected_revision: chat.revision ?? null } });
                    await openChat(session, chat.id);
                });
            }
            refresh(session);
            root.querySelector('#plan-chat-switch')?.focus();
        };
        input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } else if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
        input.addEventListener('blur', () => finish(true));
    }

    // ---- Item 7: tool-call cards (Open / Undo, or Apply AI version / Keep mine for unsaved items) -------------
    const cardOf = (session, el) => (session.conversation.messages || []).find((m) => m.role === 'assistant' && String(m.id) === el.dataset.turn)?.tool_calls?.[Number(el.dataset.card)];
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
    async function openMemory(session) {
        const chat = currentChat(session);
        if (!chat) return;
        const base = path(session, `/chats/${encodeURIComponent(chat.id)}/memory`);
        let view;
        try { view = await api(base); } catch (error) { session.error = error.message || 'Could not load chat memory.'; controls(session); return; }
        const dialog = document.createElement('dialog');
        dialog.className = 'plan-memory-dialog';
        const paint = () => {
            dialog.innerHTML = memoryPanelHtml(view, esc);
            dialog.querySelector('[data-memory-close]').onclick = () => dialog.close();
            dialog.querySelector('#plan-memory-enabled').onchange = async (event) => {
                try { view = await api(base, { method: 'PUT', body: { enabled: event.target.checked } }); }
                catch (error) { host.toast?.(error.message || 'Could not change memory.', 'error'); }
                paint();
            };
            dialog.querySelectorAll('[data-memory-forget]').forEach((button) => {
                button.onclick = async () => {
                    const scope = button.dataset.memoryForget;
                    const confirm = window.DecisionsAPI?.confirm;
                    const ok = confirm ? Boolean(await confirm({ title: 'Forget memory', message: scope === 'project' ? 'Forget the saved memory of every plan chat in this project? Messages stay visible.' : 'Forget this chat\'s saved memory? Messages stay visible.', confirmLabel: 'Forget', danger: true })) : true;
                    if (!ok) return;
                    try { view = await api(`${base}?scope=${scope}`, { method: 'DELETE' }); host.toast?.(`Forgot ${view.removed} saved note${view.removed === 1 ? '' : 's'}.`); }
                    catch (error) { host.toast?.(error.message || 'Could not forget memory.', 'error'); }
                    paint();
                };
            });
        };
        paint();
        root.append(dialog);
        dialog.addEventListener('close', async () => {
            dialog.remove();
            try { await openChat(session, session.chatId); } catch { /* keep the current view */ }
            refresh(session);
        }, { once: true });
        dialog.showModal();
    }

    // "<item_type>: message" (service.materialize_project_scan) -> { [item id]: [message, …] }, merged.
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

    function rebuildStatusHtml(percent) {
        const n = Math.max(0, Math.min(100, Math.round(percent)));
        return `<div class="plan-rebuild-status" role="status">
            <p>Rebuilding interface</p>
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
            if (!session.rebuildingInterfaces) { clearInterval(session.rebuildTimer); session.rebuildTimer = 0; return; }
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
        if (canvas.el.querySelector('.iface-shot-eye')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'iface-shot-eye';
        button.title = text;
        button.setAttribute('aria-label', text);
        button.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg><span></span>';
        button.querySelector('span').textContent = text;
        canvas.el.querySelector('.iface-navigation')?.append(button);
    }
    async function loadIfaceShots(session, model, canvas, generation) {
        let status;
        try { status = await api(path(session, '/interface-shots')); }
        catch { return; }
        if (session.ifaceShots !== generation || !canvas.el.isConnected) return;
        const saved = new Set(status.saved || []);
        const imageUrl = (screenKey) => `/api${path(session, `/interface-shots/${encodeURIComponent(screenKey)}`)}`;
        const pages = model.nodes.filter((node) => (node.lane === 'frontend' || node.lane === 'backend') && node.screenKey && concretePageRoute(node.route) && !node.shot?.image);
        for (const node of pages) {
            const hit = ['.png', '.jpg', '.jpeg', '.webp'].some((ext) => saved.has(`${node.screenKey}${ext}`));
            if (hit) canvas.setShot(node.key, { image: imageUrl(node.screenKey) });
        }
        const reason = status.reason || 'Images cannot be fetched because the terminals are down. If they were up, this could show the frontend and the backend.';
        if (!status.terminals_up || (!status.frontend_url && !status.backend_url)) {
            mountShotEye(canvas, reason);
            return;
        }
        for (const node of pages) {
            if (session.ifaceShots !== generation || !canvas.el.isConnected) return;
            if (node.shot?.image) continue;
            const base = node.lane === 'backend' ? status.backend_url : status.frontend_url;
            if (!base) {
                canvas.setShot(node.key, { reason: node.lane === 'backend' ? 'No backend address is published in the running terminals.' : 'No frontend address is published in the running terminals.' });
                continue;
            }
            try {
                const result = await api(path(session, '/interface-shots'), { method: 'POST', body: { screen_id: node.screenKey, route: node.route, lane: node.lane } });
                if (session.ifaceShots !== generation || !canvas.el.isConnected) return;
                if (result.image) canvas.setShot(node.key, { image: result.image });
                else if (result.reason) canvas.setShot(node.key, { reason: result.reason });
            } catch { /* leave the card placeholder */ }
        }
    }

    function requireSavedPlan(session, action) {
        const ids = Object.keys(session.inlineDrafts || {}).map((key) => String(key).split('#')[0]);
        const names = [...new Set((session.workspace.items || []).filter((item) => ids.includes(String(item.id))).map((item) => item.title || 'Document'))];
        if (erdIsDirty(session)) names.push('Data');
        if (!ids.length && !names.length) return true;
        session.planSaveWarning = `Save your changes${names.length ? ` in ${names.join(', ')}` : ''} before ${action}.`;
        session.outcome = '';
        controls(session);
        root.querySelector('#plan-operation-status')?.scrollIntoView({ block: 'nearest' });
        return false;
    }

    async function rebuildSitemap(session) {
        if (blocked(session) || session.rebuildingInterfaces || !modelAvailable(session)) return;
        if (!requireSavedPlan(session, 'rebuilding interfaces')) return;
        closePlanMenu();
        session.rebuildingInterfaces = true;
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
    function setScanHint(hint) {
        const bar = planBar().querySelector('.plan-detail-header') || planBar();
        let node = bar.querySelector?.('.plan-scan-hint');
        if (active) active.scanHint = hint;
        if (hint?.kind !== 'scanning') {
            node?.remove();
            if (active) controls(active);
            return;
        }
        if (active) controls(active);
        if (!node) {
            node = document.createElement('span');
            node.className = 'plan-scan-hint';
            node.setAttribute('role', 'status');
            bar.append(node);
        }
        node.dataset.kind = hint.kind;
        node.textContent = hint.text;
    }
    function refreshWorkspace(workspace) {
        const session = sessions.get(workspaceScope(workspace));
        if (!session || session.pending) return;
        session.workspace = workspace;
        if (current(session)) renderArtifacts(session);
    }
    function leave() {
        root.querySelector('.plan-file-dialog')?.close();
        bar?.remove();
        bar = null;
        active?.polling?.stop();
        active = null;
    }
    // Unsaved = chat draft/attachments/pending turn, a stored inline draft, or a dirty ERD canvas.
    const hasDraft = () => [...sessions.values()].some((session) => Boolean(session.draft || session.attachments.length || session.pending
        || [...(session.chatStates?.values() || [])].some(chat => chat.draft || chat.attachments.length)
        || Object.keys(session.inlineDrafts || {}).length || erdIsDirty(session)));
    addUnsavedCheck(() => [...sessions.values()].some((session) => Object.keys(session.inlineDrafts || {}).length || erdIsDirty(session)));
    return { open, refreshWorkspace, setScanHint, leave, hasDraft };
}
