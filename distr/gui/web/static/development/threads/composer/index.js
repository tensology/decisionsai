import { providerLabel as lookupProviderLabel, catalogModels as lookupCatalogModels } from '../../shared/models.js';
// Owns threads composer rendering, interactions, and private view state.
export function createThreadsComposer({ context, actions, el, token }) {
    function updateModelLabel() {
        const mode = context.draft.route_mode || 'auto';
        const effort = String(context.draft.reasoning_effort || 'medium')
            .replace(/^./, (char) => char.toUpperCase())
            .replace('Xhigh', 'Extra high');
        const autoRoute = context.routingAssessment?.route || {};
        const autoModel = String(autoRoute.model || '').replace(/:free$/i, '');
        const assessed = autoModel ? `${autoModel} · Free` : 'Selecting free model';
        const manualEffort = modelOptionValues('effort').length ? ` · ${effort}` : '';
        const speed = context.draft.service_tier === 'priority' && modelOptionValues('speed').length ? ' · Fast' : '';
        const fullLabel = mode === 'auto' ? `Auto · ${assessed}${speed}` : `Pinned · ${context.draft.model_name || 'Choose model'}${manualEffort}${speed}`;
        el('model-label').textContent = fullLabel;
        const fallback = autoRoute.fallback_model ? ` Fallback: ${autoRoute.fallback_model}.` : '';
        el('model-button').title =
            mode === 'auto' ? `${fullLabel}.${fallback}` : context.routingAssessment?.reason ? `${fullLabel}. ${context.routingAssessment.reason}` : fullLabel;
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
            route_mode: context.draft.route_mode,
            execution_profile: context.draft.execution_profile,
            autonomy_level: context.draft.autonomy_level,
            permission_mode: context.draft.permission_mode,
            reasoning_effort: context.draft.reasoning_effort,
            service_tier: context.draft.service_tier,
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
        const hasImages = context.attachments.some((item) => item.sourceLabel === 'Image' || String(item.text || '').includes('MIME type: image/'));
        if (hasImages && !modelSupportsImages()) {
            actions.toast('This pinned model is not image-capable. Choose Auto or an image-capable model before sending.', 'error');
            return;
        }
        if (context.draft.route_mode === 'auto' && !context.editingCommandId) {
            try {
                const ticket = context.attachments.find((item) => item.ticket_id || item.board_ticket_key) || {};
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
            } catch (_) {
                context.routingAssessment = null;
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
            } else if ((context.controlState.interactions || [])[0]?.allowed_actions?.includes('feedback')) {
                const interaction = context.controlState.interactions[0];
                await resolveInteraction(interaction.token, 'feedback', fullPrompt);
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
            actions.toast(`${actionLabel(action)} queued for the agent turn.`);
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
        return catalogModels(context.draft.provider).find((model) => String(model.id || model.name || model).toLowerCase() === wanted) || null;
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
        const automatic = context.draft.route_mode !== 'manual';
        const effortOptions = modelOptionValues('effort');
        const speedOptions = modelOptionValues('speed');
        if (effortOptions.length && !effortOptions.includes(context.draft.reasoning_effort)) context.draft.reasoning_effort = effortOptions[0];
        if (speedOptions.length && !speedOptions.includes(context.draft.service_tier)) context.draft.service_tier = speedOptions[0];
        el('model-provider-value').textContent = automatic ? 'Auto' : providerLabel(context.draft.provider);
        el('model-catalog-value').textContent = automatic ? 'Automatic' : context.draft.model_name || 'Choose model';
        el('model-effort-value').textContent = optionLabel(context.draft.reasoning_effort || 'medium');
        el('model-speed-value').textContent = optionLabel(context.draft.service_tier || 'standard');
        el('model-effort-row').classList.toggle('hidden', !effortOptions.length);
        el('model-speed-row').classList.toggle('hidden', !speedOptions.length);
        updateModelLabel();
        updateModeLabel();
    }

    function modelChoiceHtml(label, value, selected, meta) {
        return `<button type="button" class="model-choice${selected ? ' selected' : ''}" role="menuitemradio" aria-checked="${selected ? 'true' : 'false'}" data-model-choice="${actions.escapeHtml(value)}"><span>${actions.escapeHtml(label)}</span><small>${selected ? '✓' : actions.escapeHtml(meta || '')}</small></button>`;
    }

    function renderModelPaneChoices(pane, query = '') {
        const list = el('model-submenu-list');
        if (pane === 'provider') {
            list.innerHTML =
                modelChoiceHtml('Auto', 'auto', context.draft.route_mode !== 'manual', 'Best route for each step') +
                context.providers
                    .map((provider) => {
                        const id = String(provider.id || provider);
                        return modelChoiceHtml(
                            String(provider.name || provider.id || provider),
                            id,
                            context.draft.route_mode === 'manual' && id.toLowerCase() === String(context.draft.provider || '').toLowerCase(),
                            `${catalogModels(id).length} models`
                        );
                    })
                    .join('');
        } else if (pane === 'model') {
            if (context.draft.route_mode !== 'manual' || !context.draft.provider) {
                list.innerHTML = '<div class="model-menu-note">Choose a provider first.</div>';
            } else {
                const models = catalogModels(context.draft.provider);
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
                list.innerHTML = filteredModels.length
                    ? filteredModels
                          .map((model) => {
                              const id = String(model.id || model.name || model);
                              const name = String(model.name || model.id || model);
                              return modelChoiceHtml(
                                  name,
                                  id,
                                  id.toLowerCase() === String(context.draft.model_name || '').toLowerCase(),
                                  model.context_window ? `${Math.round(Number(model.context_window) / 1000)}k context` : ''
                              );
                          })
                          .join('')
                    : `<div class="model-menu-note">${models.length ? 'No matching models.' : 'No available models were returned by this provider.'}</div>`;
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

    function openModelPane(pane) {
        context.modelMenuPane = pane;
        const search = el('model-submenu-search');
        el('model-submenu-title').textContent = pane === 'provider' ? 'Provider' : pane === 'model' ? 'Model' : pane === 'effort' ? 'Effort' : 'Speed';
        search.classList.toggle('hidden', pane !== 'model');
        el('model-submenu-header').classList.toggle('model-search-visible', pane === 'model');
        search.value = '';
        renderModelPaneChoices(pane);
        el('model-menu-main').classList.add('hidden');
        el('model-submenu').classList.remove('hidden');
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
        if (pane === 'provider') {
            if (value === 'auto') {
                context.draft.route_mode = 'auto';
                context.draft.provider = '';
                context.draft.model_name = '';
                closeModelPane();
                await persistModelRoute();
                return;
            }
            context.draft.route_mode = 'manual';
            context.draft.provider = value;
            context.draft.model_name = '';
            openModelPane('model');
            return;
        }
        if (pane === 'model') context.draft.model_name = value;
        if (pane === 'effort') context.draft.reasoning_effort = value;
        if (pane === 'speed') context.draft.service_tier = value;
        closeModelPane();
        await persistModelRoute();
    }

    async function openModelDialog() {
        const dialog = el('model-dialog');
        if (!dialog.open) dialog.show();
        closeModelPane();
        positionModelDialog();
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
            syncModelMenu();
            positionModelDialog();
        } catch (error) {
            actions.toast(`Model catalog unavailable: ${error.message}`, 'error');
        }
    }

    function positionModelDialog() {
        const dialog = el('model-dialog');
        const anchor = el('model-button').getBoundingClientRect();
        const width = Math.min(440, window.innerWidth - 24);
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
        const routeMode = context.draft.route_mode || 'auto';
        if (routeMode === 'manual' && !context.draft.model_name) return;
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
                    provider: routeMode === 'manual' ? context.draft.provider : null,
                    model_name: routeMode === 'manual' ? context.draft.model_name : null,
                    reasoning_effort: modelOptionValues('effort').length ? context.draft.reasoning_effort : null,
                    service_tier: modelOptionValues('speed').length ? context.draft.service_tier : null
                };
                const updated = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/model-route`, { method: 'PATCH', body: payload });
                Object.assign(context.currentChat, updated);
                const listItem = context.chats.find((chat) => Number(chat.id) === Number(updated.id));
                if (listItem) Object.assign(listItem, updated);
                actions.toast(routeMode === 'auto' ? 'Automatic routing enabled.' : `${providerLabel(context.draft.provider)} · ${context.draft.model_name}`);
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
        renderComposerSkillMenu(el('composer-skill-search').value);
    }

    function renderComposerSkillMenu(query) {
        const list = el('composer-skill-list');
        if (!list) return;
        const needle = String(query || '')
            .trim()
            .toLowerCase();
        const rows = context.skills
            .filter((skill) => !['browser-qa', 'decisions-playwright'].includes(String(skill.id || '').toLowerCase()))
            .filter((skill) => !needle || `${skill.name || ''} ${skill.id || ''} ${skill.description || ''}`.toLowerCase().includes(needle));
        list.innerHTML = rows.length
            ? rows
                  .map((skill) => {
                      const id = String(skill.id || '');
                      const selected = selectedPromptSkill(id);
                      return `<button type="button" class="composer-skill-option${selected ? ' selected' : ''}" role="menuitemcheckbox" aria-checked="${selected}" data-prompt-skill="${actions.escapeHtml(id)}"><span>${actions.escapeHtml(skill.name || id)}</span><small>${selected ? 'Added' : actions.escapeHtml(skill.description || skill.source || '')}</small></button>`;
                  })
                  .join('')
            : '<div class="model-menu-note">No matching skills.</div>';
        list.querySelectorAll('[data-prompt-skill]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                togglePromptSkill(button.dataset.promptSkill);
            })
        );
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
        el('attach-button').setAttribute('aria-expanded', 'false');
    }

    function toggleComposerActionMenu() {
        const menu = el('composer-action-menu');
        const opening = menu.classList.contains('hidden');
        menu.classList.toggle('hidden', !opening);
        el('attach-button').setAttribute('aria-expanded', String(opening));
        if (opening) {
            el('composer-skill-search').value = '';
            renderComposerSkillMenu();
            window.setTimeout(() => menu.querySelector('[role="menuitem"]')?.focus(), 0);
        }
    }

    async function runComposerAttachmentAction(action) {
        closeComposerActionMenu();
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

    async function uploadFiles(files, input) {
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
        await uploadFiles(event.target.files, event.target);
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
            await uploadFiles(event.dataTransfer?.files || []);
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

    function renderAttachments() {
        const wrap = el('composer-context');
        const skillWrap = el('composer-skill-badges');
        const regular = context.attachments.map((item, index) => ({ item, index })).filter(({ item }) => item.kind !== 'skill' && !item.hidden_chip);
        const skills = context.attachments.map((item, index) => ({ item, index })).filter(({ item }) => item.kind === 'skill');
        wrap.innerHTML = regular
            .map(
                ({ item, index }) =>
                    `<span class="context-chip${item.kind === 'file' ? ' context-chip-file' : ''}">${item.preview_url ? `<img class="context-chip-preview" src="${actions.escapeHtml(item.preview_url)}" alt="">` : ''}<span>${actions.escapeHtml(item.label)}</span><button type="button" data-remove-attachment="${index}" aria-label="Remove ${actions.escapeHtml(item.label)}">×</button></span>`
            )
            .join('');
        skillWrap.innerHTML = skills
            .map(
                ({ item, index }) =>
                    `<span class="composer-skill-badge" title="${actions.escapeHtml(item.label)}" aria-label="${actions.escapeHtml(item.label)} skill">${actions.skillIconSvg()}<button type="button" data-remove-attachment="${index}" aria-label="Remove ${actions.escapeHtml(item.label)} skill">×</button></span>`
            )
            .join('');
        wrap.classList.toggle('hidden', !regular.length);
        skillWrap.classList.toggle('hidden', !skills.length);
        [wrap, skillWrap].forEach((container) =>
            container.querySelectorAll('[data-remove-attachment]').forEach((button) =>
                button.addEventListener('click', () => {
                    const removed = context.attachments.splice(Number(button.dataset.removeAttachment), 1)[0];
                    if (removed?.preview_url?.startsWith('blob:')) URL.revokeObjectURL(removed.preview_url);
                    renderAttachments();
                    if (el('attach-dialog').open) renderContextSource();
                })
            )
        );
        syncPlaywrightSkillToggle();
        if (el('attach-dialog').open)
            el('context-count').textContent = context.attachments.length
                ? `${context.attachments.length} item${context.attachments.length === 1 ? '' : 's'} selected`
                : 'Nothing selected';
    }
    return {
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
        providerLabel,
        renderAttachments,
        renderComposerSkillMenu,
        renderContextSource,
        renderModelPaneChoices,
        resolveInteraction,
        runComposerAttachmentAction,
        sendPrompt,
        setRunMode,
        startWorkflow,
        stopRun,
        toggleComposerActionMenu,
        togglePromptPlaywright,
        updateModeLabel,
        updateModelLabel,
        uploadContextFiles
    };
}
