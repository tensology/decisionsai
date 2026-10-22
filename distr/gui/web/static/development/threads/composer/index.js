import { encodedVariant, displayModelName, shortModelName } from '../../../shared/model_variant.js?v=20261022-composer6';
import { providerLabel as lookupProviderLabel, catalogModels as lookupCatalogModels } from '../../shared/models.js';
import { loadInstalledHarnesses } from '../../../shared/harnesses.js?v=20261022-harnesssrc';
// Owns threads composer rendering, interactions, and private view state.
export function createThreadsComposer({ context, actions, el, token }) {
    const HARNESS_OPTIONS = [
        { id: 'pi', label: 'Pi' },
        { id: 'cursor', label: 'Cursor' },
        { id: 'codex', label: 'Codex' },
        { id: 'claude_code', label: 'Claude Code' },
        { id: 'kilocode', label: 'Kilo' }
    ];
    const BASE_HARNESS_IDS = new Set(HARNESS_OPTIONS.map((item) => item.id));
    // harnesssrc 2026-10-22: offer only installed harnesses (shared source). Known ids stay
    // resolvable so a saved pin to an uninstalled harness still shows its name.
    loadInstalledHarnesses().then((installed) => {
        if (!Array.isArray(installed) || !installed.length) return;
        const rows = installed.map((h) => ({ id: h.id, label: (HARNESS_OPTIONS.find((o) => o.id === h.id) || {}).label || h.name }));
        HARNESS_OPTIONS.splice(0, HARNESS_OPTIONS.length, ...rows);
        rows.forEach((row) => BASE_HARNESS_IDS.add(row.id));
    });
    let routePreviewTimer = null;

    function harnessOptions() {
        return HARNESS_OPTIONS;
    }

    function knownHarnessId(id) {
        const wanted = String(id || '').trim().toLowerCase();
        return BASE_HARNESS_IDS.has(wanted) ? wanted : '';
    }

    function harnessLabel(id) {
        const wanted = knownHarnessId(id);
        const match = harnessOptions().find((item) => item.id === wanted || item.id.toLowerCase() === String(wanted || '').toLowerCase());
        if (match?.label) return match.label;
        return wanted ? wanted.replace(/_/g, ' ') : 'Auto';
    }

    function selectedHarnessId() {
        if (String(context.draft.backend || '').toLowerCase() === 'pi' && String(context.draft.provider || '').toLowerCase() === 'kilocode') {
            return 'kilocode';
        }
        return knownHarnessId(context.draft.backend);
    }

    function apiBackendForHarness(id) {
        return id === 'kilocode' ? 'pi' : id;
    }

    function ensureManualBackend() {
        if (context.draft.route_mode !== 'manual') return;
        const current = String(context.draft.backend || '').trim();
        if (knownHarnessId(current)) return;
        context.draft.backend = 'pi';
    }

    function harnessDefaultProvider(backend) {
        if (backend === 'codex') return 'openai';
        if (backend === 'claude_code') return 'anthropic';
        if (backend === 'cursor') return 'cursor';
        if (backend === 'pi') return String(context.draft.provider || 'ollama').toLowerCase();
        return String(backend || '').toLowerCase();
    }

    function cliModels(backend) {
        const id = String(backend || '').toLowerCase();
        const catalogs = context.cliModelCatalogs || {};
        return Array.isArray(catalogs[id]) ? catalogs[id] : null;
    }

    async function ensureCliModels(backend) {
        const id = knownHarnessId(backend);
        if (!id) return [];
        const cached = cliModels(id);
        if (cached) return cached;
        if (!context.cliModelCatalogs) context.cliModelCatalogs = {};
        if (!context.cliModelMessages) context.cliModelMessages = {};
        const projectId = Number(context.currentChat?.project_id || context.selectedProjectId || context.draft.project_id || 0);
        let path = `/projects/cli-models?backend_id=${encodeURIComponent(apiBackendForHarness(id))}`;
        if (projectId) path += `&project_id=${encodeURIComponent(String(projectId))}`;
        try {
            const data = await actions.api(path);
            const models = (Array.isArray(data.models) ? data.models : []).filter(
                (model) => id !== 'kilocode' || String(model?.provider || '').toLowerCase() === 'kilocode'
            );
            context.cliModelCatalogs[id] = models;
            context.cliModelMessages[id] = String(data.message || '').trim();
            return models;
        } catch (error) {
            context.cliModelCatalogs[id] = [];
            context.cliModelMessages[id] = error?.message || 'Could not load models for this harness.';
            return [];
        }
    }

    function modelRowsForHarness(backend, query = '') {
        const id = knownHarnessId(backend);
        let models = cliModels(id) || [];
        const selected = String(context.draft.model_name || '').trim();
        if (
            selected &&
            selected.toLowerCase() !== 'auto' &&
            !models.some((model) => String(model.id || model.name || model).toLowerCase() === selected.toLowerCase())
        ) {
            // A saved pin that the current catalog no longer lists stays selected and is marked unavailable (never reset to Auto).
            models = [{ id: selected, name: selected, unavailable: true }, ...models];
        }
        const normalizedQuery = String(query || '')
            .trim()
            .toLowerCase();
        const withoutAuto = freeFirst(models.filter((model) => String(model.id || model.name || model).toLowerCase() !== 'auto'));
        if (!normalizedQuery) return withoutAuto;
        return withoutAuto.filter((model) => {
            const modelId = String(model.id || model.name || model);
            const name = String(model.name || model.id || model);
            return `${name} ${modelId}`.toLowerCase().includes(normalizedQuery);
        });
    }

    // Free models first, then the rest in their usual (provider) order. One plain list, nothing pinned.
    function isFreeModel(model) {
        if (!model || typeof model !== 'object') return /(:free|-free|\/free)$/i.test(String(model || ''));
        if (model.free === true || model.is_free === true) return true;
        const pricing = model.pricing || {};
        if (pricing.prompt != null && Number(pricing.prompt) === 0 && Number(pricing.completion || 0) === 0) return true;
        return /(:free|-free|\/free)$/i.test(String(model.id || model.name || ''));
    }

    // Local = runs on this Mac (Ollama without :cloud, MLX, LM Studio, local workers); never shown as Free.
    function isLocalModel(model) {
        const id = String(model?.id || model?.name || model || '').toLowerCase();
        const provider = String(model?.provider || '').toLowerCase();
        if (model?.local === true || model?.scope === 'local') return true;
        if (/(^|[-_:/.\s])mlx($|[-_:/.\s])/.test(id) || provider === 'mlx' || provider === 'lmstudio' || provider === 'lm_studio') return true;
        if (id.startsWith('local_ollama:')) return true;
        return provider === 'ollama' && !/(:|-)cloud$/.test(id);
    }

    function costClass(model) {
        if (isLocalModel(model)) return 'local';
        return isFreeModel(model) ? 'free' : 'paid';
    }

    // One plain list: Free cloud models first, then Local, then Paid; usual order kept inside each group.
    function freeFirst(models) {
        const rank = { free: 0, local: 1, paid: 2 };
        return models.map((model, index) => ({ model, index, rank: rank[costClass(model)] }))
            .sort((a, b) => (a.rank === b.rank ? a.index - b.index : a.rank - b.rank))
            .map((row) => row.model);
    }

    function costBadge(model) {
        return { free: 'Free', local: 'Local', paid: 'Paid' }[costClass(model)];
    }

    function updateModelLabel() {
        ensureManualBackend();
        const mode = context.draft.route_mode || 'auto';
        const effort = String(context.draft.reasoning_effort || 'medium')
            .replace(/^./, (char) => char.toUpperCase())
            .replace('Xhigh', 'Extra high');
        const autoRoute = context.routingAssessment?.route || {};
        const assessedHarness = knownHarnessId(autoRoute.backend);
        const complexity = String(context.routingAssessment?.complexity || '').toLowerCase();
        const autoModel = String(autoRoute.model || 'auto').replace(/:free$/i, '');
        const manualEffort = modelOptionValues('effort').length && !encodedVariant(context.draft.model_name).effort ? ` · ${effort}` : '';
        const speed = context.draft.service_tier === 'priority' && modelOptionValues('speed').length ? ' · Fast' : '';
        // 2026-10-22 selector: Auto and a fixed harness/model are mutually exclusive. Auto shows only
        // "Auto"; the last automatic decision goes to the tooltip in plain words.
        let fullLabel;
        let tooltip;
        if (mode === 'auto') {
            fullLabel = `Auto${speed}`;
            const decision = autoDecisionText();
            tooltip = `Auto${decision ? ` · last: ${decision}` : ''}`;
        } else {
            const pinnedModel = String(context.draft.model_name || 'auto').replace(/:free$/i, '');
            const harness = harnessLabel(selectedHarnessId() || 'pi');
            const provider =
                selectedHarnessId() === 'pi' && context.draft.provider ? lookupProviderLabel(context.draft.provider, context.providers) : '';
            const pretty = readableModel(selectedHarnessId() || 'pi', context.draft.model_name) || pinnedModel;
            fullLabel = `${harness} · ${pretty}${speed}`;
            tooltip = `${harness} · ${pretty}${manualEffort}${speed}`;
        }
        // Auto's pick for the message being typed, visible before send: "Auto: Medium, Codex gpt-x".
        const chip = el('model-auto-chip');
        if (chip) {
            const route = context.routingAssessment?.route || {};
            const tierHarness = knownHarnessId(route.backend);
            const tierModel = String(route.model || '').replace(/:free$/i, '');
            const tier = String(context.routingAssessment?.complexity || '');
            const show = mode === 'auto' && Boolean(context.routingAssessment) && Boolean(tierHarness);
            chip.hidden = !show;
            const catalogRow = (cliModels(tierHarness) || []).find((m) => String(m.id || '').toLowerCase() === String(route.model || '').toLowerCase());
            const shortModel = tierModel && tierModel.toLowerCase() !== 'auto' ? shortModelName(route.model, catalogRow?.name || '') : '';
            const tierLabel = tier ? tier[0].toUpperCase() + tier.slice(1) : 'Medium';
            chip.textContent = show ? `${tierLabel}, ${harnessLabel(tierHarness)}${shortModel ? `, ${shortModel}` : ''}` : '';
            chip.removeAttribute('title');
            delete chip.dataset.tooltip;
            chip.dataset.tooltipIfTruncated = '';
        }
        el('model-label').textContent = fullLabel;
        el('model-label').title = '';
        el('model-button').dataset.tooltip = tooltip;
        el('model-button').setAttribute('aria-label', `Harness and model: ${fullLabel}`);
        syncRoutePreview();
    }

    // Readable model name for labels: catalog display name if known, else a prettified slug.
    function readableModel(backend, id) {
        const raw = String(id || '').trim();
        if (!raw || raw.toLowerCase() === 'auto') return '';
        const row = (cliModels(backend) || []).find((m) => String(m.id || '').toLowerCase() === raw.toLowerCase());
        return shortModelName(raw, row?.name || '');
    }

    function autoDecisionText() {
        const assessment = context.routingAssessment;
        if (!assessment) return '';
        const route = assessment.route || {};
        const assessedHarness = knownHarnessId(route.backend);
        const model = String(route.model || '').replace(/:free$/i, '');
        const name = readableModel(assessedHarness, route.model || model);
        const complexity = String(assessment.complexity || '').toLowerCase();
        const tier = complexity ? complexity[0].toUpperCase() + complexity.slice(1) : '';
        const pick = [assessedHarness ? harnessLabel(assessedHarness) : '', name].filter(Boolean).join(' · ');
        return [tier, pick].filter(Boolean).join(', ');
    }

    function syncRoutePreview() {
        const preview = el('model-route-preview');
        const text = el('model-route-preview-text');
        if (!preview || !text) return;
        const automatic = context.draft.route_mode !== 'manual';
        preview.hidden = !automatic;
        if (!automatic) return;
        const assessment = context.routingAssessment;
        if (!assessment) {
            text.textContent = '';
            preview.hidden = true;
            return;
        }
        text.textContent = `Would use: ${autoDecisionText()}`;
    }

    async function refreshRoutingAssessment(instruction, options) {
        const prompt = String(instruction || el('task-prompt')?.value || '').trim();
        if (context.draft.route_mode !== 'auto' && !options?.force) return null;
        if (!prompt) {
            context.routingAssessment = null;
            updateModelLabel();
            return null;
        }
        try {
            const ticket = context.attachments.find((item) => item.ticket_id || item.board_ticket_key) || {};
            const hasImages = context.attachments.some(
                (item) => item.sourceLabel === 'Image' || String(item.text || '').includes('MIME type: image/')
            );
            context.routingAssessment = await actions.api('/workflows/studio/routing-assessment', {
                method: 'POST',
                body: {
                    instruction: prompt,
                    ticket_title: ticket.ticket_title || ticket.label || '',
                    ticket_description: ticket.text || '',
                    has_images: hasImages,
                    recent_messages: (context.currentChat?.messages || []).slice(-6).map((message) => message.content || message.text || '')
                }
            });
            updateModelLabel();
            return context.routingAssessment;
        } catch (_) {
            return context.routingAssessment;
        }
    }

    function scheduleRoutePreview() {
        window.clearTimeout(routePreviewTimer);
        if (context.draft.route_mode !== 'auto') {
            syncRoutePreview();
            return;
        }
        routePreviewTimer = window.setTimeout(() => {
            refreshRoutingAssessment().catch(() => {});
        }, 450);
    }

    function updateModeLabel() {
        const mode = context.draft.autonomy_level || 'full';
        el('mode-button').classList.toggle('hidden', mode === 'full');
        el('mode-label').textContent = mode === 'goal' ? 'Goal' : mode === 'approval' ? 'Approval' : 'Plan';
        document.querySelectorAll('[data-prompt-mode]').forEach((button) => {
            button.setAttribute('aria-pressed', button.dataset.promptMode === mode ? 'true' : 'false');
        });
    }

    function promptWithContext(prompt) {
        const promptContext = context.attachments.filter((item) => item.kind !== 'file' && item.kind !== 'skill' && !item.suppress_prompt_context);
        if (!promptContext.length) return prompt;
        let remaining = 48000;
        const blocks = [];
        promptContext.forEach((item) => {
            if (remaining <= 0) return;
            const raw = String(item.text || item.reference || '').trim();
            const content = raw.slice(0, Math.min(12000, remaining));
            remaining -= content.length;
            blocks.push(`[${item.sourceLabel || item.source || 'Context'}: ${item.label}]${content ? `\n${content}` : ''}`);
        });
        return `${prompt}\n\nFocused context for this turn:\n\n${blocks.join('\n\n')}`;
    }

    function attachedFilesPayload() {
        return context.attachments
            .filter((item) => item.kind === 'file')
            .map((item) => ({
                name: item.label || '',
                path: item.reference || '',
                mime_type: item.mime_type || 'application/octet-stream',
                size: Number(item.size || 0)
            }));
    }

    function selectedSkillIds() {
        return context.attachments.filter((item) => item.kind === 'skill' && item.skill_id).map((item) => String(item.skill_id));
    }

    async function createTask(prompt) {
        const ticket = context.attachments.find((item) => item.ticket_id) || null;
        const boardTicket = context.attachments.find((item) => item.board_ticket_key) || null;
        const board = actions.boardByKey(context.selectedBoardKey);
        const payload = {
            prompt,
            title: context.draft.title || boardTicket?.ticket_title || undefined,
            project_id: context.draft.project_id,
            workflow_id: context.draft.workflow_id || undefined,
            ticket_id: ticket?.ticket_id || undefined,
            board_key: board?.key || undefined,
            board_provider: board?.provider || undefined,
            board_ticket_key: boardTicket?.board_ticket_key || undefined,
            board_ticket_title: boardTicket?.ticket_title || undefined,
            board_ticket_lane: boardTicket?.lane_name || undefined,
            provider: context.draft.provider || undefined,
            model_name: context.draft.model_name || undefined,
            backend: context.draft.route_mode === 'manual' ? context.draft.backend || undefined : undefined,
            route_mode: context.draft.route_mode,
            execution_profile: context.draft.execution_profile,
            autonomy_level: context.draft.autonomy_level,
            permission_mode: context.draft.permission_mode,
            reasoning_effort: context.draft.route_mode === 'manual' && encodedVariant(context.draft.model_name).effort ? undefined : context.draft.reasoning_effort,
            service_tier: context.draft.route_mode === 'manual' && encodedVariant(context.draft.model_name).speed ? undefined : context.draft.service_tier,
            routing_assessment: context.routingAssessment || undefined,
            attachments: attachedFilesPayload(),
            skill_ids: selectedSkillIds(),
            use_playwright: Boolean(context.usePlaywright)
        };
        const result = await actions.api('/workflows/studio/tasks', {
            method: 'POST',
            body: payload
        });
        context.attachments = [];
        context.usePlaywright = false;
        renderAttachments();
        await actions.refreshShell({ preserveConversation: true });
        await actions.loadChat(result.task.id);
        actions.toast(result.message || 'Task created.');
    }

    async function shouldConfirmWorkflowPush() {
        if (context.editingCommandId) return false;
        if ((context.controlState.interactions || []).length) return false;
        if (actions.isActiveAgentRun(context.currentRun)) return false;
        const autonomy = String(context.currentChat?.autonomy_level || context.draft.autonomy_level || 'full').toLowerCase();
        if (autonomy === 'plan' || autonomy === 'approval') return false;
        if (context.draft.workflow_id || context.currentWorkflow) return false;
        if (Number(context.currentChat?.development_workflow_id || 0)) return false;
        return String(context.routingAssessment?.execution_mode || '') === 'workflow';
    }

    async function sendPrompt(event) {
        event.preventDefault();
        if (context.busy || !context.ready || context.routeLoading) return;
        const route = actions.developmentRoute();
        if (route.kind === 'thread' && Number(context.currentChat?.id || 0) !== Number(route.chatId)) {
            actions.toast('This thread is still loading. Try again in a moment.', 'error');
            return;
        }
        const prompt = el('task-prompt').value.trim();
        if (!prompt) return;
        if (context.submittingPrompt) return; // double submit (held Enter / repeat click) during routing checks
        context.submittingPrompt = true;
        try {
            await sendPromptOnce(prompt);
        } finally {
            context.submittingPrompt = false;
        }
    }

    async function sendPromptOnce(prompt) {
        const hasImages = context.attachments.some((item) => item.sourceLabel === 'Image' || String(item.text || '').includes('MIME type: image/'));
        if (hasImages && !modelSupportsImages()) {
            actions.toast('This pinned model is not image-capable. Choose Auto or an image-capable model before sending.', 'error');
            return;
        }
        if (!context.editingCommandId) {
            try {
                await refreshRoutingAssessment(prompt, { force: true });
            } catch (_) {
                context.routingAssessment = null;
            }
        }
        if ((context.controlState.interactions || []).length) {
            actions.toast('This workflow is waiting for a checkpoint decision. Open Details to approve, revise, or stop it.', 'error');
            actions.openInspector();
            return;
        }
        if (await shouldConfirmWorkflowPush()) {
            const accepted = await actions.confirmAction({
                title: 'Multi-stage workflow suggested',
                message: 'This request appears to need coordinated planning, implementation, review, and end-to-end verification. Use a workflow, or send it directly as one agent turn?',
                confirmLabel: 'Use workflow',
                cancelLabel: 'Send directly'
            });
            if (!accepted) {
                context.routingAssessment = {
                    ...(context.routingAssessment || {}),
                    execution_mode: 'direct',
                    execution_mode_override: 'direct'
                };
            }
        }
        const fullPrompt = promptWithContext(prompt);
        context.busy = true;
        actions.renderSidebar();
        el('send-button').disabled = true;
        el('task-prompt').value = '';
        actions.resizePrompt();
        try {
            if (context.editingCommandId) {
                await actions.api(`/workflows/studio/commands/${context.editingCommandId}`, { method: 'PATCH', body: { content: fullPrompt } });
                context.editingCommandId = null;
                el('task-prompt').placeholder = 'Describe what DecisionsAI should build or change...';
                actions.toast('Queued guidance updated.');
                await actions.refreshShell({ preserveConversation: true });
            } else if (!context.currentChat) {
                await createTask(fullPrompt);
            } else if (actions.isActiveAgentRun(context.currentRun)) {
                const result = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/commands`, {
                    method: 'POST',
                    body: {
                        content: fullPrompt,
                        source: 'web',
                        metadata: { skill_ids: selectedSkillIds() }
                    }
                });
                actions.toast(result.summary || 'Guidance queued.');
                await actions.refreshShell({ preserveConversation: true });
            } else {
                await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/messages`, {
                    method: 'POST',
                    body: {
                        message: fullPrompt,
                        routing_assessment: context.routingAssessment || null,
                        attachments: attachedFilesPayload(),
                        skill_ids: selectedSkillIds(),
                        use_playwright: Boolean(context.usePlaywright)
                    }
                });
                await actions.refreshShell({ preserveConversation: false });
            }
            context.attachments = [];
            context.usePlaywright = false;
            renderAttachments();
        } catch (error) {
            el('task-prompt').value = prompt;
            actions.resizePrompt();
            actions.toast(error.message || 'Could not submit the task.', 'error');
        } finally {
            context.busy = false;
            actions.renderSidebar();
            actions.syncComposerAvailability();
        }
    }

    async function startWorkflow() {
        if (!context.currentWorkflow) return;
        try {
            if (context.currentChat?.autonomy_level === 'plan') {
                const updated = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/controls`, {
                    method: 'PATCH',
                    body: { autonomy_level: 'full' }
                });
                Object.assign(context.currentChat, updated);
                context.draft.autonomy_level = 'full';
                updateModeLabel();
            }
            await actions.api(`/workflows/${context.currentWorkflow.id}/run`, {
                method: 'POST',
                body: { chat_id: context.currentChat?.id || null }
            });
            actions.toast('Execution started.');
            await actions.refreshShell({
                preserveConversation: context.workspaceMode !== 'chat'
            });
        } catch (error) {
            actions.toast(error.message, 'error');
        }
    }

    async function continueRun() {
        if (!context.currentRun || !context.currentWorkflow) return;
        try {
            await actions.api(`/workflows/${context.currentWorkflow.id}/runs/${context.currentRun.id}/continue`, { method: 'POST', body: {} });
            actions.toast('Run continued.');
            await actions.refreshShell({ preserveConversation: false });
        } catch (error) {
            actions.toast(error.message, 'error');
        }
    }

    function actionLabel(action) {
        const labels = {
            approve: 'Approve',
            continue: 'Continue',
            stop: 'Stop',
            reject: 'Reject',
            feedback: 'Revise'
        };
        return labels[String(action || '').toLowerCase()] || String(action || 'Continue');
    }

    async function resolveInteraction(interactionToken, action, responseText) {
        if (!interactionToken || !action) return;
        try {
            await actions.api(`/workflows/studio/interactions/${encodeURIComponent(interactionToken)}/resolve`, {
                method: 'POST',
                body: { action, response_text: responseText || '' }
            });
            actions.toast(`${actionLabel(action)} submitted to the workflow checkpoint.`);
            await actions.refreshShell({ preserveConversation: false });
        } catch (error) {
            actions.toast(error.message || 'Could not resolve the checkpoint.', 'error');
        }
    }

    async function stopRun() {
        if (!context.currentRun || !context.currentChat) return;
        const direct = Boolean(context.currentRun.direct);
        if (
            !(await actions.confirmAction({
                title: direct ? 'Stop development agent' : 'Stop workflow run',
                message: direct
                    ? 'Stop the current agent turn? The thread and its evidence will be preserved.'
                    : 'Stop the current workflow run? The thread and its evidence will be preserved.',
                confirmLabel: direct ? 'Stop agent' : 'Stop run',
                danger: true
            }))
        )
            return;
        try {
            if (direct) {
                await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/execution/stop`, { method: 'POST', body: {} });
            } else if (context.currentWorkflow) {
                await actions.api(`/workflows/${context.currentWorkflow.id}/cancel-run/${actions.workflowRunId(context.currentRun)}`, { method: 'POST' });
            }
            actions.toast('Agent stopped.');
            await actions.refreshShell({ preserveConversation: false });
        } catch (error) {
            actions.toast(error.message, 'error');
        }
    }

    async function cancelChatTurn() {
        if (!context.currentChat) return;
        try {
            await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/execution/stop`, { method: 'POST', body: {} });
            actions.toast('Turn stopped.');
            await actions.loadChat(context.currentChat.id, { quiet: true });
        } catch (error) {
            actions.toast(error.message, 'error');
        }
    }

    const providerLabel = (id) => lookupProviderLabel(id, context.providers);
    const catalogModels = (id) => lookupCatalogModels(id, context.modelCatalogs);

    function selectedCatalogModel() {
        const wanted = String(context.draft.model_name || '').toLowerCase();
        return (
            catalogModels(context.draft.provider).find((model) => String(model.id || model.name || model).toLowerCase() === wanted) ||
            modelRowsForHarness(selectedHarnessId()).find((model) => String(model.id || model.name || model).toLowerCase() === wanted) ||
            null
        );
    }

    function declaredCapability(model, keys) {
        const sources = [model, model?.capabilities, model?.metadata].filter((value) => value && typeof value === 'object');
        for (const source of sources) {
            for (const key of keys) {
                const value = source[key];
                if (Array.isArray(value)) return value.map(String);
                if (value === true) return true;
                if (value === false) return [];
            }
        }
        return null;
    }

    function modelOptionValues(kind) {
        if (context.draft.route_mode === 'auto') return kind === 'effort' ? ['low', 'medium', 'high', 'xhigh'] : ['standard', 'priority'];
        const provider = String(context.draft.provider || '').toLowerCase();
        const model = selectedCatalogModel();
        if (!model) return [];
        const modelId = String(model.id || model.name || model).toLowerCase();
        const declared =
            kind === 'effort'
                ? declaredCapability(model, ['reasoning_efforts', 'supported_reasoning_efforts', 'reasoning_effort'])
                : declaredCapability(model, ['service_tiers', 'speed_options', 'speed']);
        if (Array.isArray(declared)) return declared;
        if (declared === true) return kind === 'effort' ? ['low', 'medium', 'high', 'xhigh'] : ['standard', 'priority'];
        if (provider === 'openai' && /(^|[/_-])(gpt-5|o[1-9]|codex)/i.test(modelId)) {
            return kind === 'effort' ? ['low', 'medium', 'high', 'xhigh'] : ['standard', 'priority'];
        }
        return [];
    }

    function optionLabel(value) {
        const labels = {
            low: 'Low',
            medium: 'Medium',
            high: 'High',
            xhigh: 'Extra high',
            standard: 'Standard',
            priority: 'Fast'
        };
        return labels[value] || String(value || '').replace(/^./, (char) => char.toUpperCase());
    }

    function syncModelMenu() {
        ensureManualBackend();
        const automatic = context.draft.route_mode !== 'manual';
        const backend = selectedHarnessId();
        const effortOptions = modelOptionValues('effort');
        const speedOptions = modelOptionValues('speed');
        if (effortOptions.length && !effortOptions.includes(context.draft.reasoning_effort)) context.draft.reasoning_effort = effortOptions[0];
        if (speedOptions.length && !speedOptions.includes(context.draft.service_tier)) context.draft.service_tier = speedOptions[0];
        const autoRoute = context.routingAssessment?.route || {};
        const assessedHarness = knownHarnessId(autoRoute.backend);
        // (Auto is now the first choice in the Harness list; no separate Auto row.)
        el('model-harness-value').textContent = automatic ? 'Auto' : harnessLabel(backend || 'pi');
        el('model-provider-value').textContent = automatic ? 'Auto' : providerLabel(context.draft.provider || harnessDefaultProvider(context.draft.backend));
        el('model-provider-row').classList.add('hidden');
        el('model-catalog-value').textContent = automatic
            ? readableModel(assessedHarness, autoRoute.model) || 'Automatic'
            : readableModel(backend, context.draft.model_name) || 'Choose model';
        ['model-harness-value', 'model-provider-value', 'model-catalog-value'].forEach((id) => { el(id).removeAttribute('title'); el(id).dataset.tooltipIfTruncated = ''; });
        if (!automatic && context.draft.model_name && cliModels(backend) && modelRowsForHarness(backend).find((m) => m.unavailable && String(m.id).toLowerCase() === String(context.draft.model_name).toLowerCase())) {
            el('model-catalog-value').textContent = `${readableModel(backend, context.draft.model_name)} (unavailable)`;
        }
        el('model-effort-value').textContent = optionLabel(context.draft.reasoning_effort || 'medium');
        el('model-speed-value').textContent = optionLabel(context.draft.service_tier || 'standard');
        // 2026-10-22 selector: Auto is one top-level choice; in Auto the other rows are picked automatically.
        // In fixed mode Model needs a harness, and Effort/Speed need a model and show only what it supports.
        const lastPick = automatic ? autoDecisionText() : '';
        const setRow = (rowId, valueId, { disabled, text, hint, hidden }) => {
            const row = rowId.startsWith('[') ? document.querySelector(rowId) : el(rowId);
            if (!row) return;
            row.classList.toggle('hidden', Boolean(hidden));
            row.disabled = Boolean(disabled);
            row.classList.toggle('is-inactive', Boolean(disabled));
            if (text != null) {
                el(valueId).textContent = text;
                el(valueId).removeAttribute('title');
            }
        };
        const harnessRow = document.querySelector('[data-model-pane="harness"]');
        if (automatic) {
            const lastHarness = knownHarnessId(context.routingAssessment?.route?.backend);
            const lastModel = String(context.routingAssessment?.route?.model || '').replace(/:free$/i, '');
            el('model-harness-value').textContent = 'Auto';
            el('model-harness-value').removeAttribute('title');
            harnessRow?.classList.add('is-auto');
            // Linear flow: in Auto only Auto | Harness are shown. Model appears once a harness is picked.
            setRow('[data-model-pane="model"]', 'model-catalog-value', { hidden: true });
            setRow('model-effort-row', 'model-effort-value', { hidden: true });
            setRow('model-speed-row', 'model-speed-value', { hidden: true });
        } else {
            harnessRow?.classList.remove('is-auto');
            const hasModel = Boolean(String(context.draft.model_name || '').trim());
            setRow('[data-model-pane="model"]', 'model-catalog-value', { disabled: !backend, hidden: false });
            if (!hasModel) {
                setRow('model-effort-row', 'model-effort-value', { hidden: true });
                setRow('model-speed-row', 'model-speed-value', { hidden: true });
                if (backend) el('model-catalog-value').textContent = cliModels(backend) === null ? 'Loading models…' : 'Choose model';
            } else {
                const variant = encodedVariant(context.draft.model_name);
                setRow('model-effort-row', 'model-effort-value', { disabled: false, hidden: !effortOptions.length || Boolean(variant.effort) });
                setRow('model-speed-row', 'model-speed-value', { disabled: false, hidden: !speedOptions.length || Boolean(variant.speed) });
            }
        }
        updateModelLabel();
        updateModeLabel();
    }

    function modelChoiceHtml(label, value, selected, meta) {
        return `<button type="button" class="model-choice${selected ? ' selected' : ''}" role="menuitemradio" aria-checked="${selected ? 'true' : 'false'}" data-model-choice="${actions.escapeHtml(value)}"><span data-tooltip-if-truncated>${actions.escapeHtml(label)}</span><small>${selected ? '✓' : actions.escapeHtml(meta || '')}</small></button>`;
    }

    function renderPiModelChoices(query = '') {
        const provider = String(context.draft.provider || '').trim();
        if (!provider) {
            return '<div class="model-menu-note">Choose a provider first.</div>';
        }
        const models = catalogModels(provider);
        const normalizedQuery = String(query || '')
            .trim()
            .toLowerCase();
        const filteredModels = normalizedQuery
            ? models.filter((model) => {
                  const id = String(model.id || model.name || model);
                  const name = String(model.name || model.id || model);
                  return `${name} ${id}`.toLowerCase().includes(normalizedQuery);
              })
            : models;
        if (!filteredModels.length) {
            return `<div class="model-menu-note">${models.length ? 'No matching models.' : 'No available models were returned by this provider.'}</div>`;
        }
        return filteredModels
            .map((model) => {
                const id = String(model.id || model.name || model);
                const name = String(model.name || model.id || model);
                return modelChoiceHtml(
                    displayModelName(name),
                    id,
                    id.toLowerCase() === String(context.draft.model_name || '').toLowerCase(),
                    model.context_window ? `${Math.round(Number(model.context_window) / 1000)}k context` : ''
                );
            })
            .join('');
    }

    function renderCliModelChoices(query = '') {
        const backend = selectedHarnessId();
        const loaded = cliModels(backend);
        if (loaded === null) return '<div class="model-menu-note">Loading models…</div>';
        const filteredModels = modelRowsForHarness(backend, query);
        const hint = String((context.cliModelMessages || {})[backend] || '').trim();
        const selected = String(context.draft.model_name || 'auto').toLowerCase();
        return (
            (['pi', 'kilocode'].includes(backend) ? '' : modelChoiceHtml('Auto', 'auto', selected === 'auto', 'Harness default')) +
            (filteredModels.length
                ? filteredModels
                      .map((model) => {
                          const id = String(model.id || model.name || model);
                          const name = String(model.name || model.id || model);
                          const meta = model.unavailable ? 'unavailable' : costBadge(model);
                          return modelChoiceHtml(String(name).toLowerCase() === String(id).toLowerCase() ? shortModelName(id) : displayModelName(name), id, id.toLowerCase() === selected, meta);
                      })
                      .join('')
                : `<div class="model-menu-note">${hint || (query ? 'No matching models.' : 'No models returned for this harness.')}</div>`)
        );
    }

    function renderModelPaneChoices(pane, query = '') {
        const list = el('model-submenu-list');
        if (pane === 'auto') {
            list.innerHTML = modelChoiceHtml(
                'Auto',
                'auto',
                context.draft.route_mode !== 'manual',
                'Picks a tier per message'
            );
        } else if (pane === 'harness') {
            const selectedBackend = selectedHarnessId();
            list.innerHTML = modelChoiceHtml('Auto', 'auto', context.draft.route_mode !== 'manual', 'Picks a tier per message') + harnessOptions().map((item) =>
                modelChoiceHtml(
                    item.label,
                    item.id,
                    context.draft.route_mode === 'manual' && item.id.toLowerCase() === selectedBackend.toLowerCase(),
                    ''
                )
            ).join('');
        } else if (pane === 'provider') {
            if (context.draft.route_mode !== 'manual' || context.draft.backend !== 'pi') {
                list.innerHTML = '<div class="model-menu-note">Provider applies to the Pi harness only.</div>';
            } else {
                const selected = String(context.draft.provider || '').toLowerCase();
                list.innerHTML = (context.providers || [])
                    .map((row) => {
                        const id = String(row.id || row);
                        return modelChoiceHtml(
                            String(row.name || id),
                            id,
                            id.toLowerCase() === selected,
                            `${catalogModels(id).length} models`
                        );
                    })
                    .join('') || '<div class="model-menu-note">No providers available.</div>';
            }
        } else if (pane === 'model') {
            if (context.draft.route_mode !== 'manual' || !selectedHarnessId()) {
                list.innerHTML = '<div class="model-menu-note">Choose a harness first.</div>';
            } else {
                list.innerHTML = renderCliModelChoices(query);
            }
        } else {
            const values = modelOptionValues(pane);
            const selected = pane === 'effort' ? context.draft.reasoning_effort : context.draft.service_tier;
            list.innerHTML = values.map((value) => modelChoiceHtml(optionLabel(value), value, value === selected, '')).join('');
        }
        list.querySelectorAll('[data-model-choice]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                selectModelChoice(pane, button.dataset.modelChoice);
            })
        );
    }

    async function openModelPane(pane) {
        if (pane === 'auto') {
            // Auto is a direct choice, not a submenu.
            if (context.draft.route_mode !== 'auto') await selectModelChoice('auto', 'auto');
            syncModelMenu();
            return;
        }
        const row = document.querySelector(`[data-model-pane="${pane}"]`);
        if (row?.disabled) return;
        if (pane === 'provider' && (context.draft.route_mode !== 'manual' || context.draft.backend !== 'pi')) {
            pane = 'harness';
        }
        if (pane === 'harness' || pane === 'auto') {
            ensureManualBackend();
            syncModelMenu();
        }
        context.modelMenuPane = pane;
        const search = el('model-submenu-search');
        const titles = { auto: 'Auto', harness: 'Harness', provider: 'Provider', model: 'Model', effort: 'Effort', speed: 'Speed' };
        el('model-submenu-title').textContent = titles[pane] || 'Models';
        search.classList.toggle('hidden', pane !== 'model');
        el('model-submenu-header').classList.toggle('model-search-visible', pane === 'model');
        search.value = '';
        el('model-menu-main').classList.add('hidden');
        el('model-submenu').classList.remove('hidden');
        if (pane === 'model' && context.draft.route_mode === 'manual' && selectedHarnessId()) {
            renderModelPaneChoices(pane);
            positionModelDialog();
            await ensureCliModels(selectedHarnessId());
            if (context.modelMenuPane !== 'model') return;
        }
        renderModelPaneChoices(pane);
        positionModelDialog();
        if (pane === 'model') window.setTimeout(() => search.focus(), 0);
    }

    function closeModelPane() {
        context.modelMenuPane = '';
        el('model-submenu').classList.add('hidden');
        el('model-menu-main').classList.remove('hidden');
        syncModelMenu();
        positionModelDialog();
    }

    async function selectModelChoice(pane, value) {
        if (pane === 'auto') {
            context.draft.route_mode = 'auto';
            context.draft.backend = '';
            context.draft.provider = '';
            context.draft.model_name = '';
            closeModelPane();
            await persistModelRoute();
            scheduleRoutePreview();
            return;
        }
        if (pane === 'harness' && value === 'auto') return selectModelChoice('auto', 'auto');
        if (pane === 'harness') {
            context.draft.route_mode = 'manual';
            context.routingAssessment = null;
            context.draft.backend = apiBackendForHarness(value);
            context.draft.provider = value === 'kilocode' ? 'kilocode' : (value === 'pi' ? '' : harnessDefaultProvider(value));
            context.draft.model_name = '';
            await openModelPane('model');
            return;
        }
        if (pane === 'provider') {
            context.draft.route_mode = 'manual';
            context.draft.backend = 'pi';
            context.draft.provider = value;
            context.draft.model_name = '';
            await openModelPane('model');
            return;
        }
        if (pane === 'model') {
            context.draft.model_name = value;
            const selectedModel = modelRowsForHarness(selectedHarnessId()).find(
                (model) => String(model.id || model.name || model).toLowerCase() === String(value || '').toLowerCase()
            );
            if (selectedModel?.provider) context.draft.provider = String(selectedModel.provider).toLowerCase();
            if (!context.draft.provider) context.draft.provider = harnessDefaultProvider(context.draft.backend);
        }
        if (pane === 'effort') context.draft.reasoning_effort = value;
        if (pane === 'speed') context.draft.service_tier = value;
        closeModelPane();
        await persistModelRoute();
    }

    // A harness picked without choosing a model is never saved; when the menu closes, show the saved route again
    // instead of leaving a half-made pin that a later reload silently turns back into Auto.
    function restoreSavedRoute() {
        const chat = context.currentChat;
        if (!chat || context.draft.route_mode !== 'manual') return;
        const unsaved = context.draft.backend === 'pi' ? !context.draft.model_name : !context.draft.backend;
        if (!unsaved) return;
        context.draft.route_mode = String(chat.route_mode || 'auto');
        context.draft.backend = chat.backend || '';
        context.draft.provider = chat.provider || '';
        context.draft.model_name = chat.model_name || '';
        updateModelLabel();
    }

    function isRouteMenuOpen() {
        return Boolean(el('model-dialog')?.open || el('complexity-dialog')?.open);
    }

    // ---- Auto complexity routing (Low / Medium / High) -------------------------------------------------
    // Reads and saves the same global per-complexity route Auto uses (settings project_cli_{level}_*)
    // through GET/POST /workflows/orchestrator-setup. Only `enabled` (echoed unchanged) and `routing` are sent.
    const COMPLEXITY_LEVELS = [
        { id: 'low', label: 'Low', hint: 'Small, quick changes' },
        { id: 'medium', label: 'Medium', hint: 'Typical feature or fix' },
        { id: 'high', label: 'High', hint: 'Large or risky work' }
    ];
    let complexitySetup = null;

    function tierHarnessId(row) {
        const backend = String(row?.backend || '').toLowerCase();
        const provider = String(row?.model_provider || '').toLowerCase();
        if (backend === 'kilocode' || (backend === 'pi' && provider === 'kilocode')) return 'kilocode';
        return knownHarnessId(backend) || 'pi';
    }

    function tierModelOptionsHtml(harness, row) {
        const loaded = cliModels(harness);
        const saved = String(row?.model || '').trim();
        if (loaded === null) return '<option value="">Loading models…</option>';
        const models = freeFirst(loaded).map((model) => ({ id: String(model.id || model.name || model), name: `${displayModelName(String(model.name || model.id || model))} · ${costBadge(model)}`, provider: model.provider || '' }));
        const options = [];
        if (!['pi', 'kilocode'].includes(harness)) options.push({ id: 'auto', name: 'Harness default' });
        if (saved && saved.toLowerCase() !== 'auto' && !models.some((m) => m.id.toLowerCase() === saved.toLowerCase())) {
            options.push({ id: saved, name: `${saved} (unavailable)` });
        }
        options.push(...models);
        if (!options.length) {
            const hint = String((context.cliModelMessages || {})[harness] || '').trim();
            return `<option value="">${actions.escapeHtml(hint || 'No models returned for this harness')}</option>`;
        }
        const wanted = (saved || (options[0]?.id ?? '')).toLowerCase();
        return options
            .map((m) => `<option value="${actions.escapeHtml(m.id)}" data-provider="${actions.escapeHtml(String(m.provider || ''))}" title="${actions.escapeHtml(m.name)}"${m.id.toLowerCase() === wanted ? ' selected' : ''}>${actions.escapeHtml(m.name)}</option>`)
            .join('');
    }

    function renderComplexityDialog() {
        const body = el('complexity-dialog-rows');
        if (!complexitySetup) {
            body.innerHTML = '<p class="complexity-note">Loading Auto settings…</p>';
            return;
        }
        body.innerHTML = COMPLEXITY_LEVELS.map((level) => {
            const row = complexitySetup.routing?.[level.id] || {};
            const harness = tierHarnessId(row);
            const harnessOptionsHtml = harnessOptions()
                .map((item) => `<option value="${item.id}"${item.id === harness ? ' selected' : ''}>${actions.escapeHtml(item.label)}</option>`)
                .join('');
            return `<div class="complexity-row" data-complexity-level="${level.id}"><div class="complexity-row-title"><strong>${level.label}</strong><small>${level.hint}</small></div><label><span>Harness</span><select data-complexity-harness aria-label="${level.label} harness">${harnessOptionsHtml}</select></label><label><span>Model</span><select data-complexity-model aria-label="${level.label} model">${tierModelOptionsHtml(harness, row)}</select></label></div>`;
        }).join('');
        body.querySelectorAll('[data-complexity-level]').forEach((rowEl) => {
            const level = rowEl.dataset.complexityLevel;
            const modelSelect = rowEl.querySelector('[data-complexity-model]');
            modelSelect.title = modelSelect.selectedOptions[0]?.textContent || '';
            rowEl.querySelector('[data-complexity-harness]').addEventListener('change', async (event) => {
                const harness = event.target.value;
                complexitySetup.routing[level] = { ...(complexitySetup.routing[level] || {}), backend: apiBackendForHarness(harness), model_provider: harness === 'kilocode' ? 'kilocode' : '', model: '' };
                modelSelect.innerHTML = '<option value="">Loading models…</option>';
                await ensureCliModels(harness);
                if (!el('complexity-dialog').open) return;
                modelSelect.innerHTML = tierModelOptionsHtml(harness, complexitySetup.routing[level]);
                modelSelect.title = modelSelect.selectedOptions[0]?.textContent || '';
                if (modelSelect.value) await saveComplexityTier(level, harness, modelSelect);
            });
            modelSelect.addEventListener('change', async () => {
                modelSelect.title = modelSelect.selectedOptions[0]?.textContent || '';
                await saveComplexityTier(level, rowEl.querySelector('[data-complexity-harness]').value, modelSelect);
            });
        });
    }

    async function saveComplexityTier(level, harness, modelSelect) {
        const model = modelSelect.value;
        if (!model) return;
        const option = modelSelect.selectedOptions[0];
        const current = complexitySetup.routing[level] || {};
        const provider = harness === 'kilocode' ? 'kilocode' : String(option?.dataset.provider || '').toLowerCase() || (harness === tierHarnessId(current) ? String(current.model_provider || '') : '');
        complexitySetup.routing[level] = { ...current, backend: apiBackendForHarness(harness), model, model_provider: provider };
        const routing = {};
        COMPLEXITY_LEVELS.forEach(({ id }) => {
            const row = complexitySetup.routing[id] || {};
            routing[id] = {
                backend: row.backend,
                model: row.model,
                model_provider: row.model_provider || '',
                codex_intelligence: row.codex_intelligence || '',
                codex_speed: row.codex_speed || ''
            };
        });
        const status = el('complexity-dialog-status');
        status.textContent = 'Saving…';
        try {
            await actions.api('/workflows/orchestrator-setup', { method: 'POST', body: { enabled: complexitySetup.enabled !== false, routing } });
            status.textContent = `${level[0].toUpperCase()}${level.slice(1)} now uses ${harnessLabel(harness)} with ${model}.`;
            context.routingAssessment = null;
            scheduleRoutePreview();
        } catch (error) {
            status.textContent = `Could not save: ${error.message}`;
        }
    }

    async function openComplexityDialog() {
        const dialog = el('complexity-dialog');
        if (el('model-dialog').open) el('model-dialog').close('cancel');
        complexitySetup = null;
        el('complexity-dialog-status').textContent = '';
        renderComplexityDialog();
        if (!dialog.open) dialog.showModal();
        try {
            const setup = await actions.api('/workflows/orchestrator-setup');
            complexitySetup = { enabled: setup.enabled, routing: JSON.parse(JSON.stringify(setup.routing || {})) };
            renderComplexityDialog();
            const harnesses = [...new Set(COMPLEXITY_LEVELS.map(({ id }) => tierHarnessId(complexitySetup.routing[id])))];
            await Promise.all(harnesses.map((h) => ensureCliModels(h)));
            if (dialog.open) renderComplexityDialog();
        } catch (error) {
            el('complexity-dialog-rows').innerHTML = `<p class="complexity-note">Auto settings are unavailable: ${actions.escapeHtml(error.message || 'error')}</p>`;
        }
    }

    async function openModelDialog() {
        const dialog = el('model-dialog');
        if (!dialog.dataset.restoreBound) {
            dialog.dataset.restoreBound = '1';
            dialog.addEventListener('close', restoreSavedRoute);
            // Keep the draft visible and focused after changing the harness/model.
            dialog.addEventListener('close', () => {
                const input = el('task-prompt');
                if (!input || input.disabled) return;
                const end = input.value.length;
                window.requestAnimationFrame(() => { input.focus({ preventScroll: true }); input.setSelectionRange(end, end); });
            });
        }
        if (!dialog.open) dialog.show();
        closeModelPane();
        positionModelDialog();
        scheduleRoutePreview();
        try {
            const providersData = await actions.api('/llms/available-providers');
            context.providers = providersData.providers || [];
            const catalogs = await Promise.all(
                context.providers.map(async (provider) => {
                    const id = String(provider.id || provider);
                    try {
                        const data = await actions.api(`/llms/models?type=conversational&provider=${encodeURIComponent(id)}`);
                        return [id.toLowerCase(), data.models || []];
                    } catch (_) {
                        return [id.toLowerCase(), []];
                    }
                })
            );
            context.modelCatalogs = Object.fromEntries(catalogs);
            const backend = selectedHarnessId();
            if (context.draft.route_mode === 'manual' && backend) {
                if (context.cliModelCatalogs) delete context.cliModelCatalogs[backend];
                await ensureCliModels(backend);
            }
            syncModelMenu();
            positionModelDialog();
        } catch (error) {
            actions.toast(`Model catalog unavailable: ${error.message}`, 'error');
        }
    }

    function positionModelDialog() {
        const dialog = el('model-dialog');
        const anchor = el('model-button').getBoundingClientRect();
        const width = Math.min(328, window.innerWidth - 24);
        dialog.style.width = `${width}px`;
        dialog.style.left = `${Math.max(12, Math.min(window.innerWidth - width - 12, anchor.right - width))}px`;
        dialog.style.top = `${Math.max(12, anchor.top - dialog.offsetHeight - 8)}px`;
    }

    function openModeDialog() {
        const mode = context.currentChat?.autonomy_level || context.draft.autonomy_level || 'full';
        const radio = el('mode-dialog').querySelector(`input[name="run-mode"][value="${['approval', 'plan', 'goal'].includes(mode) ? mode : 'full'}"]`);
        if (radio) radio.checked = true;
        el('mode-dialog').showModal();
    }

    async function applyRunMode(event) {
        event.preventDefault();
        const autonomyLevel = el('mode-dialog').querySelector('input[name="run-mode"]:checked')?.value || 'full';
        const changed = await setRunMode(autonomyLevel);
        if (!changed) return;
        el('mode-dialog').close();
    }

    async function setRunMode(autonomyLevel) {
        if (!['full', 'approval', 'plan', 'goal'].includes(autonomyLevel)) return false;
        context.draft.autonomy_level = autonomyLevel;
        if (context.currentChat) {
            try {
                const updated = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/controls`, {
                    method: 'PATCH',
                    body: { autonomy_level: autonomyLevel }
                });
                Object.assign(context.currentChat, updated);
                const item = context.chats.find((chat) => Number(chat.id) === Number(updated.id));
                if (item) Object.assign(item, updated);
                const labels = {
                    full: 'Work mode enabled.',
                    approval: 'Approval mode enabled.',
                    plan: 'Plan mode enabled.',
                    goal: 'Goal mode enabled.'
                };
                actions.toast(labels[autonomyLevel]);
            } catch (error) {
                actions.toast(error.message || 'Could not change the run mode.', 'error');
                return false;
            }
        }
        updateModeLabel();
        actions.renderInspector();
        positionModelDialog();
        return true;
    }

    async function persistModelRoute() {
        ensureManualBackend();
        const routeMode = context.draft.route_mode || 'auto';
        if (routeMode === 'manual' && !context.draft.backend) return;
        if (routeMode === 'manual' && context.draft.backend === 'pi' && !context.draft.model_name) return;
        if (routeMode === 'manual') context.routingAssessment = null;
        try {
            window.localStorage.setItem('decisions-development-reasoning-effort', context.draft.reasoning_effort);
        } catch (_) {
            /* local preference only */
        }
        try {
            window.localStorage.setItem('decisions-development-service-tier', context.draft.service_tier);
        } catch (_) {
            /* local preference only */
        }
        if (context.currentChat) {
            try {
                const payload = {
                    route_mode: routeMode,
                    backend: routeMode === 'manual' ? context.draft.backend : null,
                    provider: routeMode === 'manual' ? context.draft.provider || harnessDefaultProvider(context.draft.backend) : null,
                    model_name: routeMode === 'manual' ? context.draft.model_name || 'auto' : null,
                    // A model id that already encodes effort/speed (e.g. grok-4.7-medium, *-fast) gets no separate param.
                    reasoning_effort: modelOptionValues('effort').length && !(routeMode === 'manual' && encodedVariant(context.draft.model_name).effort) ? context.draft.reasoning_effort : null,
                    service_tier: modelOptionValues('speed').length && !(routeMode === 'manual' && encodedVariant(context.draft.model_name).speed) ? context.draft.service_tier : null
                };
                const updated = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/model-route`, { method: 'PATCH', body: payload });
                Object.assign(context.currentChat, updated);
                const listItem = context.chats.find((chat) => Number(chat.id) === Number(updated.id));
                if (listItem) Object.assign(listItem, updated);
                actions.toast(
                    routeMode === 'auto'
                        ? 'Automatic harness routing enabled.'
                        : `${harnessLabel(context.draft.backend)} · ${readableModel(context.draft.backend, context.draft.model_name) || 'auto'}`
                );
            } catch (error) {
                actions.toast(error.message, 'error');
                return;
            }
        }
        updateModelLabel();
        actions.renderInspector();
    }

    function contextKey(item) {
        return String(item.key || `${item.source || 'context'}:${item.label || ''}`);
    }

    function promptSkillAttachment(skill) {
        const id = String(skill?.id || '').trim();
        return {
            key: `skill:${id}`,
            kind: 'skill',
            skill_id: id,
            label: String(skill?.name || id),
            source: 'skill',
            sourceLabel: 'Skill'
        };
    }

    function selectedPromptSkill(skillId) {
        return context.attachments.some((item) => item.kind === 'skill' && item.skill_id === skillId);
    }

    function syncPlaywrightSkillToggle() {
        el('prompt-playwright-toggle').setAttribute('aria-pressed', String(context.usePlaywright));
        el('prompt-playwright-toggle').title = context.usePlaywright ? 'Do not use the browser for this prompt' : 'Use the browser for this prompt';
    }

    function togglePromptPlaywright() {
        context.usePlaywright = !context.usePlaywright;
        syncPlaywrightSkillToggle();
    }

    function togglePromptSkill(skillId) {
        const skill = context.skills.find((item) => String(item.id) === String(skillId));
        if (!skill) return;
        const key = `skill:${skill.id}`;
        const index = context.attachments.findIndex((item) => contextKey(item) === key);
        if (index >= 0) context.attachments.splice(index, 1);
        else context.attachments.push(promptSkillAttachment(skill));
        renderAttachments();
        renderComposerSkillMenu(el('composer-skill-search')?.value);
    }

    async function prepareVisualPlan() {
        const prompt = el('task-prompt').value.trim();
        if (!prompt) {
            actions.toast('Describe the screen or flow first.', 'error');
            el('task-prompt').focus();
            return;
        }
        const skill = context.skills.find((item) => String(item.id) === 'decisions-visual-plan');
        if (skill && !selectedPromptSkill(String(skill.id))) context.attachments.push(promptSkillAttachment(skill));
        context.usePlaywright = true;
        if (context.currentChat) {
            const artifact = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/artifacts`, {
                method: 'POST',
                body: {
                    artifact_type: 'visual_plan',
                    title: 'Visual plan',
                    workflow_id: context.currentWorkflow?.id || null,
                    run_id: actions.workflowRunId?.(context.currentRun) || null,
                    summary: prompt,
                    status: 'planned',
                    metadata: { requested_formats: ['wireframe', 'mermaid', 'desktop_screenshot', 'mobile_screenshot'] }
                }
            });
            context.artifacts = [...(context.artifacts || []), artifact];
            actions.renderInspector();
        }
        renderAttachments();
        syncPlaywrightSkillToggle();
        actions.toast('Visual plan attached. Send the prompt to generate and validate it.');
    }

    // Skills browser: cards, two per row, search + source filters. Clicking a card attaches the skill and closes.
    let skillSourceFilter = '';
    const SOURCE_TAGS = new Set(['ecc', 'vendor', 'community', 'skills-pack', 'local', 'google-cloud', 'reference-adapted', 'external']);
    const SKILL_SOURCE_LABELS = { local: 'Local', ecc_vendor: 'ECC', community_vendor: 'Community', 'google/skills': 'Google', 'reference-adapted': 'Adapted', external: 'External' };
    function skillSourceLabel(source) {
        const key = String(source || 'other');
        return SKILL_SOURCE_LABELS[key] || key;
    }

    function renderSkillFilters() {
        const box = el('skills-browser-filters');
        if (!box) return;
        const counts = new Map();
        context.skills.forEach((skill) => counts.set(String(skill.source || 'other'), (counts.get(String(skill.source || 'other')) || 0) + 1));
        const chips = [['', `All ${context.skills.length}`], ...[...counts.entries()].sort((x, y) => y[1] - x[1]).map(([key, n]) => [key, `${skillSourceLabel(key)} ${n}`])];
        box.innerHTML = chips
            .map(([key, label]) => `<button type="button" class="skills-filter-chip" data-skill-source="${actions.escapeHtml(key)}" aria-pressed="${key === skillSourceFilter}">${actions.escapeHtml(label)}</button>`)
            .join('');
        box.querySelectorAll('[data-skill-source]').forEach((chip) =>
            chip.addEventListener('click', () => {
                skillSourceFilter = chip.dataset.skillSource;
                renderSkillFilters();
                renderComposerSkillMenu(el('composer-skill-search')?.value);
            })
        );
    }

    // Pending multi-selection in the skills browser; attached on 'Add', discarded on close.
    const MAX_PROMPT_SKILLS = 5;
    const pendingSkills = new Set();
    function attachedSkillCount() {
        return context.attachments.filter((item) => item.kind === 'skill').length;
    }

    function syncSkillBrowserFooter() {
        const count = el('skills-browser-count');
        const add = el('skills-browser-add');
        const full = attachedSkillCount() + pendingSkills.size >= MAX_PROMPT_SKILLS;
        if (count) count.textContent = `${pendingSkills.size} selected${full ? ' · Max 5 skills' : ''}`;
        if (add) add.disabled = !pendingSkills.size;
    }

    function togglePendingSkill(skillId) {
        const id = String(skillId);
        if (selectedPromptSkill(id)) return;
        if (pendingSkills.has(id)) pendingSkills.delete(id);
        else if (attachedSkillCount() + pendingSkills.size < MAX_PROMPT_SKILLS) pendingSkills.add(id);
        renderComposerSkillMenu(el('composer-skill-search')?.value);
    }

    function addPendingSkills() {
        [...pendingSkills].forEach((id) => {
            if (!selectedPromptSkill(id) && attachedSkillCount() < MAX_PROMPT_SKILLS) togglePromptSkill(id);
        });
        closeSkillsBrowser();
        el('task-prompt')?.focus();
    }

    function closeSkillsBrowser() {
        pendingSkills.clear();
        const dialog = el('skills-browser-dialog');
        if (dialog?.open) dialog.close();
    }

    function renderComposerSkillMenu(query) {
        const list = el('composer-skill-list');
        if (!list) return;
        const needle = String(query || '')
            .trim()
            .toLowerCase();
        const full = attachedSkillCount() + pendingSkills.size >= MAX_PROMPT_SKILLS;
        const rows = context.skills
            .filter((skill) => !['browser-qa', 'decisions-playwright'].includes(String(skill.id || '').toLowerCase()))
            .filter((skill) => !skillSourceFilter || String(skill.source || 'other') === skillSourceFilter)
            .filter((skill) => !needle || `${skill.name || ''} ${skill.id || ''} ${skill.description || ''} ${(skill.tags || []).join(' ')}`.toLowerCase().includes(needle));
        list.innerHTML = rows.length
            ? rows
                  .map((skill) => {
                      const id = String(skill.id || '');
                      const attached = selectedPromptSkill(id);
                      const pending = pendingSkills.has(id);
                      const blocked = !attached && !pending && full;
                      // Tags mostly repeat the source (ecc/vendor/community/google-cloud); show only real topic tags.
                      const tags = (Array.isArray(skill.tags) ? skill.tags : []).filter((tag) => !SOURCE_TAGS.has(String(tag).toLowerCase())).slice(0, 3);
                      const badge = attached ? 'Attached' : String(skill.source || 'local') === 'local' ? '' : skillSourceLabel(skill.source);
                      return `<button type="button" class="skill-card${attached ? ' attached' : ''}${pending ? ' selected' : ''}" role="checkbox" aria-checked="${attached || pending}" ${attached || blocked ? 'disabled' : ''} data-prompt-skill="${actions.escapeHtml(id)}" aria-label="${actions.escapeHtml(skill.name || id)}"><header><span class="skill-card-check" aria-hidden="true"></span><strong>${actions.escapeHtml(skill.name || id)}</strong>${badge ? `<span class="skill-card-source">${actions.escapeHtml(badge)}</span>` : ''}</header><p>${actions.escapeHtml(skill.description || 'No description.')}</p>${tags.length ? `<footer>${tags.map((tag) => `<span>${actions.escapeHtml(tag)}</span>`).join('')}</footer>` : ''}</button>`;
                  })
                  .join('')
            : '<div class="model-menu-note">No matching skills.</div>';
        list.querySelectorAll('[data-prompt-skill]').forEach((card) => card.addEventListener('click', () => togglePendingSkill(card.dataset.promptSkill)));
        syncSkillBrowserFooter();
    }

    function openSkillsBrowser() {
        const dialog = el('skills-browser-dialog');
        if (!dialog) return;
        if (!dialog.__bound) {
            dialog.__bound = true;
            // Delegated: X / backdrop close and discard; Escape ('cancel') does the same.
            dialog.addEventListener('click', (event) => {
                if (event.target === dialog || event.target.closest('[data-skills-browser-close]')) closeSkillsBrowser();
                else if (event.target.closest('#skills-browser-add')) addPendingSkills();
            });
            dialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                closeSkillsBrowser();
            });
        }
        pendingSkills.clear();
        renderSkillFilters();
        renderComposerSkillMenu(el('composer-skill-search').value);
        dialog.showModal();
        el('composer-skill-search').focus();
    }

    // Visual plan sketch pad: draw, then attach the PNG through uploadFiles (origin 'visual-plan').
    const visualPlan = { tool: 'pen', ops: [], current: null };
    function drawVisualPlan() {
        const canvas = el('visual-plan-canvas');
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        [...visualPlan.ops, ...(visualPlan.current ? [visualPlan.current] : [])].forEach((op) => {
            if (op.type === 'pen' || op.type === 'eraser') {
                ctx.strokeStyle = op.type === 'eraser' ? '#ffffff' : '#1b2140';
                ctx.lineWidth = op.type === 'eraser' ? 22 : 3;
                ctx.beginPath();
                op.points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
                ctx.stroke();
            } else if (op.type === 'rect' || op.type === 'frame') {
                const x = Math.min(op.x, op.x2), y = Math.min(op.y, op.y2), w = Math.abs(op.x2 - op.x), h = Math.abs(op.y2 - op.y);
                ctx.strokeStyle = op.type === 'frame' ? '#f97316' : '#1b2140';
                ctx.lineWidth = op.type === 'frame' ? 3 : 2;
                ctx.strokeRect(x, y, w, h);
                if (op.type === 'frame') {
                    ctx.fillStyle = 'rgba(249,115,22,.12)';
                    ctx.fillRect(x, y, w, 26);
                    ctx.fillStyle = '#c2410c';
                    ctx.font = '600 14px -apple-system, sans-serif';
                    ctx.fillText(op.label || 'Page', x + 8, y + 18);
                }
            } else if (op.type === 'text') {
                ctx.fillStyle = '#1b2140';
                ctx.font = '18px -apple-system, sans-serif';
                ctx.fillText(op.text, op.x, op.y);
            }
        });
    }

    function visualPlanPoint(event) {
        const canvas = el('visual-plan-canvas');
        const rect = canvas.getBoundingClientRect();
        return [((event.clientX - rect.left) * canvas.width) / rect.width, ((event.clientY - rect.top) * canvas.height) / rect.height];
    }

    function bindVisualPlan(dialog) {
        const canvas = el('visual-plan-canvas');
        dialog.querySelectorAll('[data-vp-tool]').forEach((button) =>
            button.addEventListener('click', () => {
                visualPlan.tool = button.dataset.vpTool;
                dialog.querySelectorAll('[data-vp-tool]').forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
            })
        );
        dialog.querySelector('[data-vp-undo]').addEventListener('click', () => {
            visualPlan.ops.pop();
            drawVisualPlan();
        });
        dialog.querySelector('[data-vp-clear]').addEventListener('click', () => {
            visualPlan.ops = [];
            drawVisualPlan();
        });
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog || event.target.closest('[data-visual-plan-close]')) dialog.close();
        });
        canvas.addEventListener('pointerdown', (event) => {
            const [x, y] = visualPlanPoint(event);
            const tool = visualPlan.tool;
            if (tool === 'text') {
                const text = window.prompt('Text');
                if (text) visualPlan.ops.push({ type: 'text', text, x, y });
                drawVisualPlan();
                return;
            }
            canvas.setPointerCapture?.(event.pointerId);
            visualPlan.current = tool === 'pen' || tool === 'eraser' ? { type: tool, points: [[x, y]] } : { type: tool, x, y, x2: x, y2: y };
        });
        canvas.addEventListener('pointermove', (event) => {
            const op = visualPlan.current;
            if (!op) return;
            const [x, y] = visualPlanPoint(event);
            if (op.points) op.points.push([x, y]);
            else Object.assign(op, { x2: x, y2: y });
            drawVisualPlan();
        });
        const finish = () => {
            const op = visualPlan.current;
            visualPlan.current = null;
            if (!op) return;
            if (op.type === 'frame') op.label = `Page ${visualPlan.ops.filter((item) => item.type === 'frame').length + 1}`;
            visualPlan.ops.push(op);
            drawVisualPlan();
        };
        canvas.addEventListener('pointerup', finish);
        canvas.addEventListener('pointercancel', finish);
        el('visual-plan-done').addEventListener('click', () => {
            canvas.toBlob(async (blob) => {
                if (!blob) return actions.toast('Could not export the sketch.', 'error');
                const file = new File([blob], `visual-plan-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.png`, { type: 'image/png' });
                dialog.close();
                try {
                    await uploadFiles([file], null, 'visual-plan');
                    visualPlan.ops = [];
                } catch (error) {
                    actions.toast(error.message || 'Could not attach the sketch.', 'error');
                }
            }, 'image/png');
        });
    }

    function openVisualPlan() {
        const dialog = el('visual-plan-dialog');
        if (!dialog) return;
        if (!dialog.__bound) {
            dialog.__bound = true;
            bindVisualPlan(dialog);
        }
        drawVisualPlan();
        dialog.showModal();
    }

    function addContext(item) {
        const key = contextKey(item);
        const existing = context.attachments.findIndex((entry) => contextKey(entry) === key);
        if (existing >= 0) context.attachments.splice(existing, 1);
        else context.attachments.push(Object.assign({}, item, { key }));
        renderAttachments();
        renderContextSource();
    }

    function contextItemButton(item, dataAttribute, index) {
        const selected = context.attachments.some((entry) => contextKey(entry) === contextKey(item));
        return `<button type="button" class="attachment-item${selected ? ' selected' : ''}" ${dataAttribute}="${index}" aria-pressed="${selected}"><strong>${actions.escapeHtml(item.label)}</strong><span>${actions.escapeHtml(item.meta || item.sourceLabel || item.source || 'Context')}${selected ? ' · Added' : ''}</span></button>`;
    }

    async function openAttachDialog() {
        const dialog = el('attach-dialog');
        context.contextSource = 'files';
        dialog.showModal();
        renderContextSource(true);
    }

    function closeComposerActionMenu() {
        el('composer-action-menu').classList.add('hidden');
        el('composer-plan-button')?.setAttribute('aria-expanded', 'false');
    }

    function toggleComposerActionMenu() {
        const menu = el('composer-action-menu');
        const opening = menu.classList.contains('hidden');
        menu.classList.toggle('hidden', !opening);
        el('composer-plan-button')?.setAttribute('aria-expanded', String(opening));
        if (opening) {
            window.setTimeout(() => menu.querySelector('[role="menuitem"]')?.focus(), 0);
        }
    }

    async function runComposerAttachmentAction(action) {
        closeComposerActionMenu();
        if (action === 'visual') return openVisualPlan();
        if (action === 'skills') return openSkillsBrowser();
        if (action === 'files') {
            el('context-file-input').click();
            return;
        }
    }

    function modelSupportsImages() {
        if (context.draft.route_mode === 'auto') return true;
        const catalogModel = selectedCatalogModel();
        const capabilityList = Array.isArray(catalogModel?.capabilities) ? catalogModel.capabilities.map((value) => String(value).toLowerCase()) : [];
        if (capabilityList.length) return capabilityList.some((value) => ['vision', 'image', 'images', 'image_input'].includes(value));
        const declared = declaredCapability(catalogModel, ['supports_vision', 'vision', 'image_input', 'images']);
        if (declared === true) return true;
        if (Array.isArray(declared)) return declared.length > 0;
        const model = String(context.draft.model_name || '').toLowerCase();
        const provider = String(context.draft.provider || '').toLowerCase();
        if (provider === 'openai' && /gpt|o1|o3|o4/.test(model)) return true;
        if (provider === 'anthropic' && /claude-(3|4)/.test(model)) return true;
        return /(gemini|gemma-3|llama-4|qwen.*vl|vision|pixtral)/.test(model);
    }

    function transferHasFiles(dataTransfer) {
        return Array.from(dataTransfer?.types || []).includes('Files');
    }

    function insideDevelopmentDropZone(target) {
        return target instanceof Element && Boolean(target.closest('#studio-composer, #studio-sidebar'));
    }

    function transferItems(dataTransfer) {
        return Array.from(dataTransfer?.items || []).filter((item) => item.kind === 'file');
    }

    function transferItemEntry(item) {
        try {
            return typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
        } catch (_) {
            return null;
        }
    }

    function transferContainsDirectory(dataTransfer) {
        return transferItems(dataTransfer).some((item) => Boolean(transferItemEntry(item)?.isDirectory));
    }

    function transferContainsUnsupportedImage(dataTransfer) {
        if (modelSupportsImages()) return false;
        return transferItems(dataTransfer).some((item) => String(item.type || '').startsWith('image/'));
    }

    function setDropOverlay(overlay, visible, invalid, title, detail) {
        if (!overlay) return;
        overlay.classList.toggle('hidden', !visible);
        overlay.classList.toggle('is-invalid', Boolean(invalid));
        if (title) overlay.querySelector('strong').textContent = title;
        if (detail) overlay.querySelector('span').textContent = detail;
    }

    function droppedFileUri(dataTransfer) {
        const value =
            String(dataTransfer?.getData('text/uri-list') || '')
                .split(/\r?\n/)
                .find((line) => line && !line.startsWith('#')) || '';
        if (!value.startsWith('file://')) return '';
        try {
            const parsed = new URL(value);
            let path = decodeURIComponent(parsed.pathname || '');
            if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
            return path;
        } catch (_) {
            return '';
        }
    }

    function droppedFolder(dataTransfer) {
        const items = transferItems(dataTransfer);
        const directoryItems = items.filter((item) => Boolean(transferItemEntry(item)?.isDirectory));
        const fileItems = items.filter((item) => transferItemEntry(item)?.isFile);
        if (directoryItems.length !== 1 || fileItems.length) return null;
        const item = directoryItems[0];
        const entry = transferItemEntry(item);
        const file = item.getAsFile?.();
        const path = String(file?.path || file?.mozFullPath || droppedFileUri(dataTransfer) || '');
        return { name: String(entry?.name || file?.name || '').trim(), path };
    }

    async function uploadFiles(files, input, origin = 'picker') {
        files = Array.from(files || []);
        if (!files.length) return;
        if (input) input.disabled = true;
        try {
            for (const file of files) {
                const isImage = String(file.type || '').startsWith('image/');
                const previewUrl =
                    isImage && file.size <= 10 * 1024 * 1024
                        ? await new Promise((resolve) => {
                              const reader = new FileReader();
                              reader.onload = () => resolve(String(reader.result || ''));
                              reader.onerror = () => resolve('');
                              reader.readAsDataURL(file);
                          })
                        : '';
                const form = new FormData();
                form.append('file', file, file.name);
                if (context.draft.project_id) form.append('project_id', String(context.draft.project_id));
                const headers = token ? { 'X-DecisionsAI-Internal-Token': token } : {};
                const response = await fetch('/api/workflows/studio/attachments', {
                    method: 'POST',
                    headers,
                    body: form
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.detail || `Could not attach ${file.name}`);
                }
                addContext({
                    key: `file:${result.path}`,
                    label: result.name || file.name,
                    text: `Attached local file: ${result.path}\nMIME type: ${result.mime_type || file.type || 'unknown'}\nUse this file as input to the requested work.`,
                    reference: result.path,
                    source: 'file',
                    kind: 'file',
                    mime_type: result.mime_type || file.type || 'application/octet-stream',
                    size: Number(result.size || file.size || 0),
                    sourceLabel: isImage ? 'Image' : 'File',
                    origin,
                    preview_url: previewUrl
                });
            }
            actions.toast(`${files.length} file${files.length === 1 ? '' : 's'} attached.`);
            if (files.some((file) => String(file.type || '').startsWith('image/')) && !modelSupportsImages()) {
                actions.toast('This pinned model is not image-capable. Choose Auto or an image-capable model before sending.', 'error');
            }
        } catch (error) {
            actions.toast(error.message || 'Could not attach the selected files.', 'error');
        } finally {
            if (input) {
                input.value = '';
                input.disabled = false;
            }
        }
    }

    async function uploadContextFiles(event) {
        await uploadFiles(event.target.files, event.target, 'picker');
        event.target.value = '';
    }

    async function createBoardFromDroppedFolder(dataTransfer) {
        let folder = droppedFolder(dataTransfer);
        if (!folder) {
            actions.toast('Drop one folder on the Development sidebar.', 'error');
            return;
        }
        if (!folder.path) {
            const picked = await actions.api('/browse-folder');
            if (!picked.path) {
                actions.toast('The browser hid the folder path. Select the same folder to finish adding it.', 'error');
                return;
            }
            folder = {
                name: folder.name || String(picked.path).split(/[\\/]/).filter(Boolean).pop(),
                path: picked.path
            };
        }
        const comparablePath = (value) =>
            String(value || '')
                .replace(/[\\/]+$/, '')
                .toLowerCase();
        const existing = context.boards.find((board) => comparablePath(board.folder_location || board.default_project_folder) === comparablePath(folder.path));
        if (existing) {
            context.selectedBoardKey = existing.key;
            await actions.openBoardFromSidebar(existing.key);
            actions.toast(`${existing.name || folder.name} is already available.`);
            return;
        }
        const result = await actions.api('/tickets/boards', {
            method: 'POST',
            body: { name: folder.name, folder_location: folder.path }
        });
        context.selectedBoardKey = `decisions:${Number(result.id)}`;
        await actions.refreshShell({ preserveConversation: true });
        const board = actions.boardByKey(context.selectedBoardKey);
        if (board) await actions.openBoardFromSidebar(board.key);
        actions.toast(`${folder.name} added as a board and project.`);
    }

    function bindComposerDrop() {
        const composer = el('studio-composer');
        const sidebar = el('studio-sidebar');
        const composerOverlay = el('composer-drop-overlay');
        const sidebarOverlay = el('sidebar-drop-overlay');
        const depths = new Map([
            [composer, 0],
            [sidebar, 0]
        ]);

        const reset = (target, overlay) => {
            depths.set(target, 0);
            setDropOverlay(overlay, false, false);
        };

        composer.addEventListener('dragenter', (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            depths.set(composer, (depths.get(composer) || 0) + 1);
            const directory = transferContainsDirectory(event.dataTransfer);
            const unsupportedImage = transferContainsUnsupportedImage(event.dataTransfer);
            setDropOverlay(
                composerOverlay,
                true,
                directory || unsupportedImage,
                directory ? 'Folders cannot be attached here' : unsupportedImage ? 'This model cannot read images' : 'Drop files into this prompt',
                directory
                    ? 'Drop project folders on the left sidebar'
                    : unsupportedImage
                      ? 'Choose Auto or an image-capable model'
                      : 'Images and files become visible prompt context'
            );
        });
        composer.addEventListener('dragover', (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            const invalid = transferContainsDirectory(event.dataTransfer) || transferContainsUnsupportedImage(event.dataTransfer);
            if (event.dataTransfer) event.dataTransfer.dropEffect = invalid ? 'none' : 'copy';
        });
        composer.addEventListener('dragleave', (event) => {
            event.stopPropagation();
            depths.set(composer, Math.max(0, (depths.get(composer) || 0) - 1));
            if (!depths.get(composer)) reset(composer, composerOverlay);
        });
        composer.addEventListener('drop', async (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            const invalid = transferContainsDirectory(event.dataTransfer) || transferContainsUnsupportedImage(event.dataTransfer);
            reset(composer, composerOverlay);
            if (invalid) {
                actions.toast(
                    transferContainsDirectory(event.dataTransfer) ? 'Drop folders on the Development sidebar.' : 'This pinned model does not accept images.',
                    'error'
                );
                return;
            }
            await uploadFiles(event.dataTransfer?.files || [], null, 'drop');
        });
        // Pasted screenshots/images upload through the same attachment path as drop.
        composer.addEventListener('paste', async (event) => {
            const files = Array.from(event.clipboardData?.files || []).filter((file) => String(file.type || '').startsWith('image/'));
            if (!files.length) return;
            event.preventDefault();
            await uploadFiles(files, null, 'paste');
        });

        sidebar.addEventListener('dragenter', (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            depths.set(sidebar, (depths.get(sidebar) || 0) + 1);
            const valid = Boolean(droppedFolder(event.dataTransfer));
            setDropOverlay(
                sidebarOverlay,
                true,
                !valid,
                valid ? 'Drop a project folder' : 'Only one folder can be added',
                valid ? 'Adds the folder as a board and project' : 'Files belong in the prompt area'
            );
        });
        sidebar.addEventListener('dragover', (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = droppedFolder(event.dataTransfer) ? 'link' : 'none';
        });
        sidebar.addEventListener('dragleave', (event) => {
            event.stopPropagation();
            depths.set(sidebar, Math.max(0, (depths.get(sidebar) || 0) - 1));
            if (!depths.get(sidebar)) reset(sidebar, sidebarOverlay);
        });
        sidebar.addEventListener('drop', async (event) => {
            if (!transferHasFiles(event.dataTransfer)) return;
            event.preventDefault();
            event.stopPropagation();
            reset(sidebar, sidebarOverlay);
            try {
                await createBoardFromDroppedFolder(event.dataTransfer);
            } catch (error) {
                actions.toast(error.message || 'Could not add the dropped project folder.', 'error');
            }
        });

        document.addEventListener('dragover', (event) => {
            if (!transferHasFiles(event.dataTransfer) || insideDevelopmentDropZone(event.target)) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'none';
        });
        document.addEventListener('drop', (event) => {
            if (!transferHasFiles(event.dataTransfer) || insideDevelopmentDropZone(event.target)) return;
            event.preventDefault();
            reset(composer, composerOverlay);
            reset(sidebar, sidebarOverlay);
            actions.toast('Drop files on the prompt, or a project folder on the left sidebar.', 'error');
        });
    }

    async function loadProjectContext() {
        const projectId = Number(context.currentChat?.project_id || context.selectedProjectId || context.draft.project_id || 0);
        if (!projectId) {
            context.projectContext = null;
            return;
        }
        if (Number(context.projectContext?.id) === projectId) return;
        try {
            context.projectContext = await actions.api(`/projects/${projectId}`);
        } catch (error) {
            context.projectContext = {
                id: projectId,
                context_items: [],
                files: [],
                error: error.message
            };
        }
    }

    async function renderContextSource(loadProject) {
        const source = context.contextSource;
        const list = el('attachment-list');
        el('context-manual').classList.toggle('hidden', source !== 'manual');
        el('context-files').classList.toggle('hidden', source !== 'files');
        list.classList.toggle('hidden', source === 'manual' || source === 'files');
        document.querySelectorAll('[data-context-source]').forEach((button) => {
            button.classList.toggle('active', button.dataset.contextSource === source);
        });
        el('context-count').textContent = context.attachments.length
            ? `${context.attachments.length} item${context.attachments.length === 1 ? '' : 's'} selected`
            : 'Nothing selected';
        if (source === 'files') {
            window.setTimeout(() => el('context-file-input').focus(), 0);
            return;
        }
        if (source === 'manual') {
            window.setTimeout(() => el('context-manual-label').focus(), 0);
            return;
        }
        if (source === 'project' && loadProject) {
            list.innerHTML = '<div class="empty-inspector">Loading project context...</div>';
            await loadProjectContext();
        }
        if (source === 'project') {
            if (!context.projectContext) {
                list.innerHTML =
                    '<div class="empty-inspector"><strong>Choose a project first.</strong><span>Its repository is the working context. Saved project notes and files appear here.</span></div>';
                return;
            }
            const detail = context.projectContext;
            const projectItems = (detail.context_items || []).map((item) => ({
                key: `project-note:${item.id}`,
                label: item.title || 'Project note',
                text: item.content || '',
                source: 'project',
                sourceLabel: 'Project note',
                meta: 'Project note'
            }));
            const projectFiles = (detail.files || []).map((item) => ({
                key: `project-file:${item.id}`,
                label: item.filename || item.file_path || 'Project file',
                text: item.file_path ? `Project file reference: ${item.file_path}${item.description ? `\n${item.description}` : ''}` : item.description || '',
                source: 'project-file',
                sourceLabel: 'Project file',
                meta: item.description || 'Project file'
            }));
            const items = projectItems.concat(projectFiles);
            list.innerHTML = items.length
                ? items.map((item, index) => contextItemButton(item, 'data-project-context-index', index)).join('')
                : '<div class="empty-inspector"><strong>The repository is already linked.</strong><span>No additional project notes or uploaded files are saved.</span></div>';
            list.querySelectorAll('[data-project-context-index]').forEach((button) =>
                button.addEventListener('click', () => addContext(items[Number(button.dataset.projectContextIndex)]))
            );
            return;
        }
        if (source === 'tickets') {
            const tickets = context.inbox
                .filter((item) => item.ticket_id || /jira|trello|ticket|board/i.test(String(item.source || '')))
                .map((item) => ({
                    key: `ticket:${item.ticket_id || item.id}`,
                    label: item.ticket_title || item.text || item.response_text || `Ticket #${item.ticket_id}`,
                    text: item.text || item.response_text || item.ticket_title || '',
                    ticket_id: item.ticket_id || null,
                    source: item.source || 'ticket',
                    sourceLabel: 'Ticket',
                    meta: item.source || 'Ticket board'
                }));
            list.innerHTML = tickets.length
                ? tickets
                      .slice(0, 80)
                      .map((item, index) => contextItemButton(item, 'data-ticket-context-index', index))
                      .join('')
                : '<div class="empty-inspector">No ticket is waiting in Development intake.</div>';
            list.querySelectorAll('[data-ticket-context-index]').forEach((button) =>
                button.addEventListener('click', () => addContext(tickets[Number(button.dataset.ticketContextIndex)]))
            );
            return;
        }
        const threads = context.chats
            .filter((chat) => Number(chat.id) !== Number(context.currentChat?.id))
            .map((chat) => ({
                key: `thread:${chat.id}`,
                label: chat.title || `Thread ${chat.id}`,
                text: `Related thread #${chat.id}: ${chat.title || 'Untitled'}`,
                source: 'thread',
                sourceLabel: 'Thread',
                meta: actions.projectById(chat.project_id)?.name || 'Thread'
            }));
        list.innerHTML = threads.length
            ? threads
                  .slice(0, 80)
                  .map((item, index) => contextItemButton(item, 'data-thread-context-index', index))
                  .join('')
            : '<div class="empty-inspector">No other thread is available.</div>';
        list.querySelectorAll('[data-thread-context-index]').forEach((button) =>
            button.addEventListener('click', () => addContext(threads[Number(button.dataset.threadContextIndex)]))
        );
    }

    function addManualContext() {
        const label = el('context-manual-label').value.trim();
        const content = el('context-manual-content').value.trim();
        if (!content) {
            el('context-manual-content').focus();
            actions.toast('Paste a link or note first.', 'error');
            return;
        }
        const isUrl = /^https?:\/\//i.test(content);
        let defaultLabel = 'Run note';
        if (isUrl) {
            try {
                defaultLabel = new URL(content).hostname || 'Reference link';
            } catch (_) {
                defaultLabel = 'Reference link';
            }
        }
        addContext({
            key: `manual:${content}`,
            label: label || defaultLabel,
            text: content,
            source: isUrl ? 'link' : 'note',
            sourceLabel: isUrl ? 'Reference link' : 'Run note'
        });
        el('context-manual-label').value = '';
        el('context-manual-content').value = '';
        actions.toast('Context added.');
    }

    function developmentInstruction() {
        const messages = context.currentChat?.messages || [];
        const firstUser = messages.find((message) => message.role === 'user' && String(message.content || '').trim());
        return String(firstUser?.content || context.currentChat?.title || '').trim();
    }

    function attachmentFileKind(item) {
        const name = String(item.label || item.reference || '').toLowerCase();
        const mime = String(item.mime_type || '').toLowerCase();
        if (mime === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
        if (/word|officedocument|opendocument\.text|rtf/.test(mime) || /\.(docx?|odt|rtf|pages)$/.test(name)) return 'doc';
        return 'file';
    }

    function attachmentIconSvg(kind) {
        const page = '<path d="M4.5 2.5h4.75L12 5.25v8.25h-7.5z"></path><path d="M9.25 2.5v2.75H12"></path>';
        if (kind === 'pdf') return `<svg viewBox="0 0 16 16" aria-hidden="true">${page}</svg><b>PDF</b>`;
        if (kind === 'doc') return `<svg viewBox="0 0 16 16" aria-hidden="true">${page}<path d="M6.5 8h3.5M6.5 10h3.5"></path></svg><b>DOC</b>`;
        return `<svg viewBox="0 0 16 16" aria-hidden="true">${page}</svg>`;
    }

    function attachmentTooltip(item) {
        if (item.kind === 'skill') return `Skill: ${item.label}`;
        const image = String(item.mime_type || '').startsWith('image/') || Boolean(item.preview_url);
        if (item.origin === 'visual-plan') return `Visual plan: ${item.label}`;
        const origin = item.origin === 'paste' ? 'Pasted' : item.origin === 'drop' ? 'Dropped' : '';
        if (image) return `${origin ? `${origin} image` : 'Image'}: ${item.label}`;
        if (item.kind === 'file') return `${origin ? `${origin} file` : 'File'}: ${item.label}`;
        return `${item.sourceLabel || 'Context'}: ${item.label}`;
    }

    function renderAttachments() {
        // Attachments and skills render as small icons beside the access dropdown (legacy chip rows stay empty).
        const wrap = el('composer-context');
        const skillWrap = el('composer-skill-badges');
        const strip = el('composer-attachment-strip');
        if (wrap) wrap.innerHTML = '';
        if (skillWrap) skillWrap.innerHTML = '';
        wrap?.classList.add('hidden');
        skillWrap?.classList.add('hidden');
        const visible = context.attachments.map((item, index) => ({ item, index })).filter(({ item }) => item.kind === 'skill' || !item.hidden_chip);
        if (strip) {
            strip.innerHTML = visible
                .map(({ item, index }) => {
                    const tip = attachmentTooltip(item);
                    const body =
                        item.kind === 'skill'
                            ? actions.skillIconSvg()
                            : item.preview_url
                              ? `<img src="${actions.escapeHtml(item.preview_url)}" alt="">`
                              : attachmentIconSvg(attachmentFileKind(item));
                    const kindClass = item.kind === 'skill' ? ' skill' : item.preview_url ? ' image' : ` file ${attachmentFileKind(item)}`;
                    return `<span class="composer-attachment-icon${kindClass}" data-tooltip="${actions.escapeHtml(tip)}" aria-label="${actions.escapeHtml(tip)}" role="img">${body}<button type="button" data-remove-attachment="${index}" aria-label="Remove ${actions.escapeHtml(tip)}" data-tooltip="Remove ${actions.escapeHtml(item.label)}">×</button></span>`;
                })
                .join('');
            strip.classList.toggle('hidden', !visible.length);
            strip.querySelectorAll('[data-remove-attachment]').forEach((button) =>
                button.addEventListener('click', () => {
                    const removed = context.attachments.splice(Number(button.dataset.removeAttachment), 1)[0];
                    if (removed?.preview_url?.startsWith('blob:')) URL.revokeObjectURL(removed.preview_url);
                    renderAttachments();
                    if (el('attach-dialog').open) renderContextSource();
                })
            );
        }
        syncPlaywrightSkillToggle();
        if (el('attach-dialog').open)
            el('context-count').textContent = context.attachments.length
                ? `${context.attachments.length} item${context.attachments.length === 1 ? '' : 's'} selected`
                : 'Nothing selected';
    }
    return {
        isRouteMenuOpen,
        openComplexityDialog,
        actionLabel,
        addManualContext,
        applyRunMode,
        bindComposerDrop,
        catalogModels,
        closeComposerActionMenu,
        closeModelPane,
        continueRun,
        developmentInstruction,
        openModeDialog,
        openModelDialog,
        openModelPane,
        positionModelDialog,
        prepareVisualPlan,
        providerLabel,
        renderAttachments,
        renderComposerSkillMenu,
        renderContextSource,
        renderModelPaneChoices,
        resolveInteraction,
        runComposerAttachmentAction,
        sendPrompt,
        setRunMode,
        scheduleRoutePreview,
        startWorkflow,
        stopRun,
        toggleComposerActionMenu,
        togglePromptPlaywright,
        updateModeLabel,
        updateModelLabel,
        uploadContextFiles
    };
}
