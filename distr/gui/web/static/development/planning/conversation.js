import { parseWireframe, renderWireframe } from './wireframe.js';
import { createThreadsTranscript } from '../threads/transcript/index.js';
import { createPlanPolling } from './polling.js';
import { buildResultsHtml } from './build-results.js';
import { wireframeSitemap } from './sitemap.js';
import { planningProviderLabel } from './routes.js';

const BASE = '/workflows/studio/plan-workspaces';
const TABS = {
    wireframes: { label: 'Wireframes', types: ['wireframe', 'flows'] },
    erd: { label: 'ERD', types: ['erd', 'architecture', 'diagram'] },
    requirements: { label: 'Requirements', types: ['brief', 'prd', 'frac', 'skills', 'file_structure', 'handover', 'decision', 'project_overview', 'discovery_proposal'] }
};

export function createPlanConversation(host, root) {
    const sessions = new Map();
    const esc = (value) => host.escapeHtml(String(value ?? ''));
    const { assistantMarkdown } = createThreadsTranscript({ actions: { escapeHtml: esc } });
    const api = (path, options) => host.api(path, options);
    let active = null;
    let mermaidReady;
    let diagramId = 0;
    const path = (session, suffix = '') => `${BASE}/${encodeURIComponent(session.id)}${suffix}`;
    const current = (session) => active === session && Boolean(root.querySelector('.plan-detail'));
    const items = (session) => (session.workspace.items || []).filter((item) => TABS[session.tab].types.includes(item.item_type));
    const selected = (session) => items(session).find((item) => String(item.id) === String(session.selection[session.tab])) || items(session)[0] || null;
    const blocked = (session) => !session.loaded || Boolean(session.pending) || ['running', 'pending', 'processing'].includes(session.conversation.status);
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
            read: () => api(path(session, '/conversation')),
            apply: async (conversation, valid) => {
                const running = ['running', 'pending', 'processing'].includes(conversation.status);
                if (!running) {
                    const workspace = await api(path(session));
                    if (!valid()) return false;
                    session.workspace = workspace;
                }
                const changed = JSON.stringify(session.conversation) !== JSON.stringify(conversation);
                session.conversation = conversation;
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

    function shell(session) {
        root.innerHTML = `<div class="plan-detail">
            <header class="plan-detail-header">
                <button type="button" id="plan-back" aria-label="Back to plans"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg></button>
                <strong class="plan-project" title="${esc(session.workspace.board_name)}">${esc(session.workspace.board_name)}</strong>
                <nav class="plan-tabs" aria-label="Plan artifacts">${Object.entries(TABS).map(([tab, info]) => `<button type="button" data-plan-tab="${tab}" aria-pressed="${session.tab === tab}">${info.label}</button>`).join('')}</nav>
                <button type="button" id="plan-scan">Scan project</button>
                <button type="button" id="plan-build">Build tasks</button>
            </header>
            <div class="plan-detail-body">
                <section class="plan-artifacts" id="plan-artifacts" aria-label="Artifacts"></section>
                <section class="plan-conversation" aria-label="Plan conversation">
                    <div class="plan-messages" id="plan-messages" role="log" aria-label="Messages" aria-live="polite"></div>
                    <div class="plan-composer-wrap">
                        <div id="plan-operation-status" role="status" aria-live="polite"></div>
                        <form class="studio-composer plan-language" id="plan-language">
                            <div id="plan-draft-assets" class="composer-context"></div>
                            <textarea id="plan-language-input" rows="2" placeholder="Describe what you want to build or change" aria-label="Message"></textarea>
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
        root.querySelector('#plan-back').onclick = () => host.openHome();
        root.querySelector('#plan-scan').onclick = () => scan(session);
        root.querySelector('#plan-build').onclick = () => build(session);
        root.querySelector('#plan-provider').onchange = async (event) => {
            session.provider = event.target.value;
            session.model = '';
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
            controls(session);
        };
        root.querySelector('#plan-attach').onclick = () => root.querySelector('#plan-attachment').click();
        root.querySelector('#plan-attachment').onchange = (event) => {
            const files = [...event.target.files];
            event.target.value = '';
            upload(session, files);
        };
        root.querySelectorAll('[data-plan-tab]').forEach((button) => {
            button.onclick = () => {
                session.tab = button.dataset.planTab;
                session.canvasClosed = false;
                session.viewedAsset = null;
                root.querySelectorAll('[data-plan-tab]').forEach((tab) => tab.setAttribute('aria-pressed', String(tab === button)));
                renderArtifacts(session);
            };
        });
        refresh(session);
    }

    function controls(session) {
        if (!current(session)) return;
        const busy = blocked(session);
        root.querySelector('#plan-send').disabled = busy || !modelAvailable(session) || !session.draft.trim();
        root.querySelector('#plan-scan').disabled = busy;
        root.querySelector('#plan-build').disabled = busy || !modelAvailable(session);
        root.querySelector('#plan-model').disabled = busy;
        root.querySelector('#plan-provider').disabled = busy;
        root.querySelector('#plan-attach').disabled = busy;
        const status = root.querySelector('#plan-operation-status');
        status.replaceChildren();
        if (session.error) {
            const error = document.createElement('p');
            error.className = 'plan-error';
            error.setAttribute('role', 'alert');
            error.textContent = session.error;
            status.append(error);
        }
        const persistedStatus = ['running', 'pending', 'processing'].includes(session.conversation.status) ? 'Assistant is working...' : '';
        const note = session.pending || session.pollError || (!session.loaded && !session.error ? 'Loading conversation...' : '') || session.outcome || persistedStatus;
        if (note) {
            const text = document.createElement('p');
            text.textContent = String(note);
            status.append(text);
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
        log.innerHTML = (session.conversation.messages || []).map((message) => `<article class="plan-message${message.role === 'user' ? ' plan-message-user' : ''}"><strong>${esc(message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Assistant' : message.role)}</strong><div class="plan-message-content">${esc(message.content)}</div>${(message.artifacts || []).map((id) => {
            const item = (session.workspace.items || []).find((item) => String(item.id) === String(id));
            return item ? `<button type="button" data-message-artifact="${esc(item.id)}">${esc(item.title)}</button>` : '';
        }).join('')}${message.role === 'assistant' ? buildResultsHtml(message.build_result, esc) : ''}</article>`).join('') || (session.loaded
            ? `<p class="plan-conversation-empty">${allStarters(session)
                ? 'Plan sections are scaffolded in the project planning/ folder. Ask Plan to scan the project and fill brief, requirements, architecture, and flows.'
                : 'No messages yet.'}</p>`
            : '');
        log.querySelectorAll('[data-message-artifact]').forEach((button) => {
            button.onclick = () => {
                const item = session.workspace.items.find((item) => String(item.id) === button.dataset.messageArtifact);
                const tab = Object.keys(TABS).find((tab) => TABS[tab].types.includes(item.item_type));
                if (!tab) return;
                session.selection[tab] = item.id;
                root.querySelector(`[data-plan-tab="${tab}"]`).click();
            };
        });
        const drafts = root.querySelector('#plan-draft-assets');
        drafts.innerHTML = session.attachments.map((asset) => `<span class="context-chip"><span>${esc(asset.name)}</span><button type="button" data-remove-asset="${esc(asset.id)}" aria-label="Remove ${esc(asset.name)}">Remove</button></span>`).join('');
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

    function renderArtifacts(session) {
        if (!current(session)) return;
        const panel = root.querySelector('#plan-artifacts');
        root.querySelector('.plan-detail-body').classList.toggle('plan-chat-only', Boolean(session.canvasClosed));
        panel.hidden = Boolean(session.canvasClosed);
        root.querySelectorAll('[data-plan-tab]').forEach(button => button.setAttribute('aria-pressed', String(!session.canvasClosed && session.tab === button.dataset.planTab)));
        if (session.canvasClosed) return;
        const item = selected(session);
        if (item) session.selection[session.tab] = item.id;
        const choices = items(session);
        const assets = [...new Map([...(session.conversation.attachments || []), ...session.attachments].map((asset) => [String(asset.id), asset])).values()];
        const previewAssets = assets.filter(asset => asset.url === `/api/workflows/studio/plan-workspaces/${session.id}/attachments/${asset.id}`)
            .map(asset => ({...asset, url: new URL(asset.url, window.location.origin).href}));
        panel.innerHTML = `${choices.length > 1 ? `<label class="plan-artifact-picker"><select id="plan-artifact-select" aria-label="Select artifact">${choices.map((choice) => `<option value="${esc(choice.id)}"${String(choice.id) === String(item?.id) ? ' selected' : ''}>${esc(choice.title)}${choice.is_starter ? ' · needs content' : ''}</option>`).join('')}</select></label>` : ''}
            ${item?.is_starter && !session.viewedAsset ? `<div class="plan-artifact-revision">Starter section · ask Plan to fill this from the project</div>` : ''}
            ${item?.revision_count && !session.viewedAsset && !item?.is_starter ? `<div class="plan-artifact-revision">Revision ${esc(item.revision_count)}</div>` : ''}
            ${item?.file_sync_error ? `<div class="plan-file-status" role="alert">${esc(item.file_sync_error)}<button type="button" id="plan-review-file">Review file changes</button></div>` : ''}
            <div class="plan-preview" id="plan-preview"></div>
            ${assets.length ? `<div class="plan-assets" aria-label="Assets">${assetsHtml(assets)}</div>` : ''}`;
        panel.querySelector('#plan-artifact-select')?.addEventListener('change', (event) => { session.viewedAsset = null; session.selection[session.tab] = event.target.value; renderArtifacts(session); });
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'plan-close-canvas';
        close.setAttribute('aria-label', 'Close artifact pane');
        close.title = 'Close artifact pane';
        close.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
        close.onclick = () => { session.canvasClosed = true; renderArtifacts(session); root.querySelector('#plan-language-input').focus(); };
        panel.prepend(close);
        panel.querySelectorAll('[data-view-asset]').forEach(button => {
            button.onclick = () => { session.viewedAsset = button.dataset.viewAsset; renderArtifacts(session); };
        });
        const review = panel.querySelector('#plan-review-file');
        if (review) { review.disabled = Boolean(session.pending); review.onclick = () => reviewFile(session, item); }
        const preview = panel.querySelector('#plan-preview');
        const sourceAsset = assets.find(asset => String(asset.id) === String(session.viewedAsset));
        if (sourceAsset) { renderSourceAsset(session, sourceAsset, preview); return; }
        if (!item) { preview.innerHTML = `<div class="plan-canvas-empty">No ${TABS[session.tab].label.toLowerCase()} yet.</div>`; return; }
        if (item.content_format === 'wire') {
            try {
                const screens = parseWireframe(item.content).screens || [];
                session.pages ||= {};
                session.previewModes ||= {};
                // Page inspection uses readable, responsive controls. SVG remains
                // useful for thumbnails and an explicitly selected schematic view.
                session.previewModes[item.id] ||= 'html';
                const page = screens.find(screen => screen.id === session.pages[item.id]) || (screens.length === 1 ? screens[0] : null);
                session.pages[item.id] = session.sitemap ? undefined : page?.id;
                const toolbar = document.createElement('div');
                toolbar.className = 'plan-preview-toolbar';
                toolbar.innerHTML = `<select aria-label="Wireframe page">${screens.length > 1 ? `<option value=""${!page && !session.sitemap ? ' selected' : ''}>All pages</option>` : ''}<option value="sitemap"${session.sitemap ? ' selected' : ''}>Sitemap</option>${screens.map(screen => `<option value="page:${esc(screen.id)}"${screen.id === page?.id && !session.sitemap ? ' selected' : ''}>${esc(screen.label || screen.id)}</option>`).join('')}</select>${page && !session.sitemap ? `<button type="button" aria-pressed="${session.previewModes[item.id] === 'html'}">HTML preview</button>` : ''}`;
                panel.insertBefore(toolbar, preview);
                toolbar.querySelector('select').addEventListener('change', event => {
                    session.sitemap = event.target.value === 'sitemap';
                    session.pages[item.id] = event.target.value.startsWith('page:') ? event.target.value.slice(5) : undefined;
                    renderArtifacts(session);
                });
                const htmlToggle = toolbar.querySelector('button');
                if (htmlToggle) htmlToggle.onclick = () => { session.previewModes[item.id] = session.previewModes[item.id] === 'html' ? 'svg' : 'html'; renderArtifacts(session); };
                if (session.sitemap) {
                    const map = wireframeSitemap(items(session).filter(entry => entry.content_format === 'wire').map(entry => ({
                        id: entry.id, screens: parseWireframe(entry.content).screens || [],
                    })));
                    const diagram = document.createElement('div');
                    diagram.className = 'plan-mermaid';
                    preview.append(diagram);
                    renderDiagram(diagram, map.source);
                    if (map.diagnostics.length) preview.insertAdjacentHTML('beforeend', `<ul class="plan-wireframe-diagnostics">${map.diagnostics.map(message => `<li>${esc(message)}</li>`).join('')}</ul>`);
                    return;
                }
                if (!page && screens.length > 1) {
                    const overview = document.createElement('div');
                    overview.className = 'plan-page-overview';
                    for (const screen of screens) {
                        const thumbnail = document.createElement('button');
                        thumbnail.type = 'button';
                        thumbnail.className = 'plan-page-thumbnail';
                        const result = renderWireframe(item.content, item.title, screen.id, previewAssets);
                        thumbnail.innerHTML = `<span class="plan-page-image" aria-hidden="true">${result.svg}</span><span class="plan-page-name">${esc(screen.label || screen.id)}</span>${screen.attrs.route ? `<span class="plan-page-route">${esc(screen.attrs.route)}</span>` : ''}`;
                        thumbnail.onclick = () => { session.pages[item.id] = screen.id; renderArtifacts(session); panel.querySelector('[aria-label="Wireframe page"]')?.focus(); };
                        overview.append(thumbnail);
                    }
                    preview.append(overview);
                    return;
                }
                const result = renderWireframe(item.content, item.title, page?.id, previewAssets);
                preview.innerHTML = `<div class="plan-wireframe-preview">${result.svg}</div>${result.diagnostics?.length ? `<ul class="plan-wireframe-diagnostics">${result.diagnostics.map((entry) => `<li>Line ${esc(entry.line)}: ${esc(entry.message)}</li>`).join('')}</ul>` : ''}`;
                if (session.previewModes[item.id] === 'html') {
                    const frame = document.createElement('iframe');
                    frame.title = page?.label || item.title;
                    frame.setAttribute('sandbox', '');
                    frame.referrerPolicy = 'no-referrer';
                    frame.srcdoc = result.html;
                    preview.querySelector('.plan-wireframe-preview').replaceWith(frame);
                }
            } catch (error) { preview.textContent = error.message || 'Could not render wireframe.'; }
        } else if (item.content_format === 'html') {
            const frame = document.createElement('iframe');
            frame.title = item.title || 'Wireframe';
            frame.setAttribute('sandbox', '');
            frame.referrerPolicy = 'no-referrer';
            frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; form-action 'none'; base-uri 'none'">${item.content || ''}`;
            preview.append(frame);
        } else if (item.content_format === 'mermaid') {
            preview.classList.add('plan-mermaid');
            renderDiagram(preview, item.content);
        } else {
            const document = window.document.createElement('div');
            document.className = 'plan-document message-markdown';
            document.innerHTML = assistantMarkdown(item.content || '');
            preview.append(document);
        }
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

    async function load(session) {
        if (session.pending) return;
        session.polling?.stop();
        const refreshWorkspace = session.loaded;
        session.pending = 'Loading conversation...';
        session.error = '';
        controls(session);
        try {
            const conversation = await api(path(session, '/conversation'));
            session.conversation = conversation;
            session.provider = session.provider || conversation.provider || '';
            session.model = session.model || conversation.model_name || '';
            session.loaded = true;
            if (current(session)) {
                root.querySelector('#plan-provider').innerHTML = providerOptions(session);
                root.querySelector('#plan-model').innerHTML = modelOptions(session);
            }
            if (refreshWorkspace) await reload(session);
            session.providers = (await api('/llms/available-providers?type=planning')).providers || [];
            if (current(session)) root.querySelector('#plan-provider').innerHTML = providerOptions(session);
            await loadModels(session);
        } catch (error) { session.error = error.message || 'Could not load conversation. Refresh to retry.'; }
        finally { session.pending = ''; session.pollError = ''; refresh(session); monitor(session); }
    }

    async function send(session) {
        if (blocked(session) || !session.draft.trim() || !modelAvailable(session)) return;
        const version = session.draftVersion;
        const attachments = [...new Set([...session.attachments.map((asset) => asset.id), ...(!session.canvasClosed && session.viewedAsset ? [Number(session.viewedAsset)] : [])])];
        const selectedItem = session.canvasClosed || session.viewedAsset ? null : selected(session);
        const body = { message: session.draft.trim(), provider: session.provider, model_name: session.model, tab: session.tab, item_id: selectedItem?.id ?? null, page_id: selectedItem?.content_format === 'wire' ? session.pages?.[selectedItem.id] ?? null : null, attachments };
        session.pending = 'Waiting for assistant...';
        session.error = '';
        session.outcome = '';
        controls(session);
        followSubmittedTurn(session);
        try {
            session.conversation = await api(path(session, '/messages'), { method: 'POST', body });
            if (session.conversation.status === 'error') {
                session.error = 'The assistant could not complete this request. Your draft is preserved.';
                return;
            }
            // Only clear exactly the submitted draft. A user can compose the next message while waiting.
            if (session.draftVersion === version) {
                session.draft = '';
                if (current(session)) root.querySelector('#plan-language-input').value = '';
            }
            session.attachments = session.attachments.filter((asset) => !attachments.includes(asset.id));
            try { await reload(session); }
            catch (error) { session.error = `Response received, but artifacts could not refresh: ${error.message}`; }
        } catch (error) {
            session.error = error.message || 'Message failed. Your draft is preserved.';
            // The server may have persisted an assistant error even when the HTTP request failed.
            try { session.conversation = await api(path(session, '/conversation')); } catch { /* Keep last persisted messages and the draft. */ }
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

    async function scan(session) {
        if (blocked(session)) return;
        session.pending = 'Scanning project...';
        session.error = '';
        session.outcome = '';
        controls(session);
        followSubmittedTurn(session);
        try {
            const result = await api(path(session, '/scan'), { method: 'POST', body: {} });
            session.outcome = result.message || `Updated ${(result.updated || []).length} plan sections from the project.`;
            if (result.workspace) session.workspace = result.workspace;
            else await reload(session);
            try { session.conversation = await api(path(session, '/conversation')); }
            catch (error) { session.error = session.error || `Could not refresh conversation: ${error.message}`; }
        } catch (error) { session.error = error.message || 'Could not scan project.'; }
        finally {
            session.pending = '';
            refresh(session);
            monitor(session);
        }
    }

    async function build(session) {
        if (blocked(session) || !modelAvailable(session)) return;
        session.pending = 'Checking plan...';
        session.error = '';
        session.outcome = '';
        controls(session);
        followSubmittedTurn(session);
        try {
            const result = await api(path(session, '/build'), { method: 'POST', body: { provider: session.provider, model_name: session.model } });
            if (result.status === 'error') throw new Error(result.error || 'Could not build tasks.');
            session.outcome = result.status === 'needs_input' ? '' : `Tasks: ${result.created} created, ${result.updated || 0} updated, ${result.reused} reused.`;
        } catch (error) { session.error = error.message || 'Could not build tasks.'; }
        finally {
            try { session.conversation = await api(path(session, '/conversation')); }
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
        let session = sessions.get(id);
        if (!session) {
            session = { id, workspace, conversation: { messages: [], attachments: [] }, models: null, loaded: false, draft: '', draftVersion: 0, attachments: [], provider: '', model: '', tab: 'wireframes', selection: {}, pending: '', error: '', outcome: '' };
            session.canvasClosed = !(workspace.items || []).some(item =>
                ['wireframe', 'flows'].includes(item.item_type) && String(item.content || '').trim());
            sessions.set(id, session);
        } else if (!session.pending) session.workspace = workspace;
        active?.polling?.stop();
        active = session;
        shell(session);
        load(session);
    }
    function leave() {
        root.querySelector('.plan-file-dialog')?.close();
        active?.polling?.stop();
        active = null;
    }
    return { open, leave, hasDraft: () => [...sessions.values()].some((session) => Boolean(session.draft || session.attachments.length || session.pending)) };
}
