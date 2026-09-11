// Owns threads inspector rendering, interactions, and private view state.
export function createThreadsInspector({ context, actions, el, token }) {
    function planItemHtml(step, index, currentStepId) {
        const status = String(step.status || 'pending').toLowerCase();
        const active = Number(step.id) === Number(currentStepId) || status === 'running';
        const done = ['completed', 'passed', 'success'].includes(status);
        return `<div class="plan-item"><span class="${done ? 'done' : active ? 'active' : ''}">${done ? '✓' : active ? '●' : '○'}</span><span>${actions.escapeHtml(step.name || `Step ${index + 1}`)}</span><small>${done ? 'done' : active ? 'active' : 'queued'}</small></div>`;
    }

    function currentRoute() {
        const runRoute = context.currentRun?.latest_backend_handoff || {};
        return {
            provider: runRoute.backend_id || context.currentRun?.backend_id || context.currentChat?.provider || context.draft.provider || 'Auto',
            model: runRoute.model || context.currentRun?.model || context.currentChat?.model_name || context.draft.model_name || 'Best available',
            reason:
                runRoute.route_rationale ||
                runRoute.selection_reason ||
                context.currentRun?.selection_reason ||
                ((context.currentChat?.route_mode || context.draft.route_mode) === 'auto'
                    ? 'Selected automatically from the instruction, required tools, cost, and model health.'
                    : 'Pinned manually for this thread.')
        };
    }

    function automationForChat(chatId) {
        return context.automations.find((automation) => Number(automation.action_config?.development_chat_id || 0) === Number(chatId || 0)) || null;
    }

    function automationSectionHtml(chat) {
        const automation = automationForChat(chat?.id);
        if (!automation) {
            return `<section class="inspect-section"><div class="inspect-title">Automation</div><button class="wide-button" data-create-automation="1">Automate this thread</button></section>`;
        }
        const paused = String(automation.status || '').toLowerCase() === 'paused';
        return `<section class="inspect-section">
            <div class="inspect-title">Automation</div>
            <div class="automation-card">
                <div class="automation-card-head"><strong>${actions.escapeHtml(automation.name || 'Development automation')}</strong><span class="automation-status${paused ? ' paused' : ''}">${actions.escapeHtml(automation.status || 'active')}</span></div>
                <div class="automation-meta"><span>Next: ${actions.escapeHtml(actions.formatDateTime(automation.next_run_at))}</span>${automation.last_run_at ? `<span>Last: ${actions.escapeHtml(actions.formatDateTime(automation.last_run_at))}</span>` : ''}</div>
                <div class="automation-actions">
                    <button class="wide-button" data-run-automation="${actions.escapeHtml(automation.id)}">Run now</button>
                    <button class="wide-button" data-toggle-automation="${actions.escapeHtml(automation.id)}" data-automation-action="${paused ? 'resume' : 'pause'}">${paused ? 'Resume' : 'Pause'}</button>
                    <button class="wide-button" data-edit-automation="${actions.escapeHtml(automation.id)}">Edit schedule</button>
                </div>
            </div>
        </section>`;
    }

    function commandQueueHtml() {
        const commands = context.controlState.commands || [];
        if (!commands.length) return '';
        return `<section class="inspect-section queue-section"><div class="inspect-title">Queued guidance <span>${commands.length}</span></div>${commands.map((command) => `<div class="command-card"><div><strong>${actions.escapeHtml(command.source === 'telegram' ? 'Telegram' : 'You')}</strong><span>${actions.escapeHtml(command.content)}</span></div><div class="command-actions"><button data-edit-command="${Number(command.id)}">Edit</button><button data-cancel-command="${Number(command.id)}">Cancel</button></div></div>`).join('')}</section>`;
    }

    function agentPlanHtml(run) {
        const raw = run?.coordination_plan?.assignments || {};
        const assignments = Array.isArray(raw) ? raw : Object.values(raw);
        if (!assignments.length) return '';
        return `<details class="inspect-disclosure"><summary><span>Agents</span><b>${assignments.length} roles</b></summary><div class="disclosure-body">${assignments
            .map((assignment) => {
                const route = assignment.primary_route || {};
                return `<div class="agent-row"><span class="agent-state ${actions.escapeHtml(assignment.status || 'planned')}"></span><div><strong>${actions.escapeHtml(assignment.role || assignment.step_name || 'Worker')}</strong><small>${actions.escapeHtml(assignment.step_name || '')}</small></div><span>${actions.escapeHtml(route.model || route.backend || 'auto')}</span></div>`;
            })
            .join('')}</div></details>`;
    }

    function riskAndCostHtml(run) {
        if (!run) return '';
        const risk = run.risk_profile || {};
        const budget = run.budget || {};
        const drift = run.drift_metrics || {};
        const estimated = Number(budget.estimated_cost_usd || 0);
        const riskLevel = String(risk.level || 'low').toLowerCase();
        const signals = Array.isArray(risk.signals) ? risk.signals : [];
        return `<details class="inspect-disclosure"><summary><span>Safety and cost</span><b class="risk-${actions.escapeHtml(riskLevel)}">${actions.escapeHtml(riskLevel)} risk · $${estimated.toFixed(4)}</b></summary><div class="disclosure-body"><div class="metric-grid"><div><small>Review</small><strong>${actions.escapeHtml(riskLevel === 'high' || riskLevel === 'critical' ? 'Required' : 'Automatic')}</strong></div><div><small>Takeovers</small><strong>${Number(drift.human_takeovers || 0)}</strong></div></div>${signals.length ? `<p class="compact-note">${actions.escapeHtml(signals.join(' · '))}</p>` : ''}</div></details>`;
    }

    function doctorHtml(chat) {
        if (!chat.project_id) return '';
        if (!context.doctor)
            return `<section class="inspect-section contextual-health"><button class="wide-button" data-run-doctor="${Number(chat.project_id)}">Check board harness</button></section>`;
        const summary = context.doctor.summary || {};
        return `<details class="inspect-disclosure"${context.doctor.ok ? '' : ' open'}><summary><span>Board harness</span><b class="${context.doctor.ok ? 'health-ready' : 'health-warning'}">${context.doctor.ok ? 'Ready' : `${Number(summary.missing || 0)} issues`}</b></summary><div class="disclosure-body"><div class="metric-grid"><div><small>Ready</small><strong>${Number(summary.ready || 0)}</strong></div><div><small>Needs setup</small><strong>${Number(summary.missing || 0)}</strong></div></div>${(
            context.doctor.repair_actions || []
        )
            .slice(0, 3)
            .map(
                (action) =>
                    `<div class="health-action"><strong>${actions.escapeHtml(action.name)}</strong><span>${actions.escapeHtml(action.reason)}</span></div>`
            )
            .join('')}</div></details>`;
    }

    function inspectorPreviewsHtml(run) {
        const urls = Array.isArray(run?.runtime_snapshot?.urls) ? run.runtime_snapshot.urls.map(actions.safeArtifactUri).filter(Boolean) : [];
        if (!urls.length) return '';
        return `<section class="inspect-section"><div class="inspect-title">Preview <span>${urls.length}</span></div>${urls.map((url) => `<a class="inspector-preview" href="${actions.escapeHtml(url)}" target="_blank" rel="noopener"><span>${actions.escapeHtml(url)}</span><b>Open</b></a>`).join('')}</section>`;
    }

    function inspectorWorkflowHtml() {
        const workflow = context.currentWorkflow;
        if (!workflow)
            return '<section class="inspect-section"><div class="inspect-title">Execution route</div><div class="surface-row">Mode<span>Direct agent</span></div></section>';
        const steps = actions.workflowStepList(workflow);
        const currentStepId = Number(context.currentRun?.current_step_id || context.currentRun?.step_id || 0);
        return `<section class="inspect-section"><div class="inspect-title">Workflow <span>${actions.escapeHtml(actions.statusLabel(context.currentRun?.status || 'ready'))}</span></div><button type="button" class="workflow-thread-link" data-open-thread-workflow="${Number(workflow.id)}"><strong>${actions.escapeHtml(workflow.name || 'Workflow')}</strong><small>${steps.length} steps · Open workflow</small></button><div class="workflow-thread-steps">${steps.map((step, index) => planItemHtml(step, index, currentStepId)).join('')}</div></section>`;
    }

    function inspectorRunHtml() {
        const run = context.currentRun;
        const chat = context.currentChat;
        if (!chat) return '<div class="empty-inspector">Select a thread.</div>';
        const interaction = (context.controlState.interactions || [])[0] || null;
        const waiting = interaction || (run && (run.worker_question || run.waiting_prompt || Object.keys(run.pending_route_approval || {}).length));
        const interactionActions = interaction ? interaction.allowed_actions || [] : [];
        const rows = inspectorConversationActivity(chat);
        return `
            ${commandQueueHtml()}
            ${inspectorWorkflowHtml()}
            ${waiting ? `<section class="inspect-section"><div class="waiting-card"><strong>Input required${interaction?.telegram_linked ? ' in web or Telegram' : ''}</strong><p>${actions.escapeHtml(run?.worker_question || run?.waiting_prompt || 'Review the pending development checkpoint.')}</p><div class="waiting-actions">${interaction ? interactionActions.map((action) => `<button data-interaction-token="${actions.escapeHtml(interaction.token)}" data-interaction-action="${actions.escapeHtml(action)}">${actions.escapeHtml(actions.actionLabel(action))}</button>`).join('') : '<button data-continue-run="1">Continue</button><button data-focus-steer="1">Add guidance</button>'}</div>${interactionActions.includes('feedback') ? '<small>Or type revision guidance in the composer.</small>' : ''}</div></section>` : ''}
            <section class="inspect-section inspector-activity-section">
                <div class="inspect-title">Conversation <span>${rows.length} recent</span></div>
                <div class="inspector-activity-list">${rows.map(inspectorActivityRowHtml).join('') || '<div class="empty-inspector">No conversation activity yet.</div>'}</div>
            </section>
            ${inspectorPreviewsHtml(run)}`;
    }

    function inspectorActivitySummary(value) {
        const summary = String(value || '')
            .replace(/```[\s\S]*?```/g, ' code ')
            .replace(/[`*_#>\[\]]/g, '')
            .replace(/\s+/g, ' ')
            .trim();
        return summary.length > 160 ? `${summary.slice(0, 157).trimEnd()}...` : summary;
    }

    function inspectorSafeMessage(message) {
        const role = String(message?.role || 'assistant').toLowerCase();
        const text = String(message?.content || '').trim();
        const low = text.toLowerCase();
        if (
            role === 'assistant' && (
                low.includes('this work runs on openrouter') ||
                low.includes('passed readiness but failed the actual work') ||
                low.includes('model request failed while trying to generate')
            )
        ) {
            return 'The previous workflow attempt stopped before completion. Technical details are preserved in the ticket Activity and audit history.';
        }
        return text;
    }

    function isInspectorConversationNoise(message, chat) {
        if (!chat?.project_id || String(message?.role || '').toLowerCase() !== 'user') return false;
        const text = String(message?.content || '').trim().toLowerCase();
        return /^(?:hello(?:,? are you there)?|are you alive\??(?: hello\.)?|how(?: are| you) doing\??|yo,? what'?s up\??)$/.test(text);
    }

    function inspectorConversationActivity(chat) {
        return (chat?.messages || [])
            .filter((message) => !isInspectorConversationNoise(message, chat))
            .map((message) => {
                const role = String(message.role || 'assistant').toLowerCase();
                if (role === 'tool') {
                    const tool = message.tool_event || {};
                    return {
                        kind: 'Action',
                        text: inspectorActivitySummary(tool.title || tool.summary || message.content),
                        status: tool.status || '',
                        timestamp: message.timestamp
                    };
                }
                if (role === 'workflow') {
                    const workflow = message.workflow_event || {};
                    return {
                        kind: 'Progress',
                        text: inspectorActivitySummary(workflow.summary || message.content),
                        status: workflow.status || '',
                        timestamp: message.timestamp
                    };
                }
                return {
                    kind: role === 'user' ? 'You' : 'Response',
                    text: inspectorActivitySummary(inspectorSafeMessage(message)),
                    status: '',
                    timestamp: message.timestamp
                };
            })
            .filter((item) => item.text)
            .slice(-10);
    }

    function inspectorActivityRowHtml(item) {
        const time = actions.messageTime(item.timestamp);
        const meta = time || (item.status ? actions.statusLabel(item.status) : '');
        return `<div class="inspector-activity-row"><span class="inspector-activity-dot ${actions.escapeHtml(String(item.kind || '').toLowerCase())}" aria-hidden="true"></span><div><strong>${actions.escapeHtml(item.kind)}</strong><p>${actions.escapeHtml(item.text)}</p></div>${meta ? `<small>${actions.escapeHtml(meta)}</small>` : ''}</div>`;
    }

    function availableArtifacts() {
        return (context.artifacts || [])
            .map((artifact, index) => ({ artifact, index }))
            .filter(({ artifact }) => artifact.content || artifact.uri || String(artifact.status || '').toLowerCase() === 'ready');
    }

    function inspectorArtifactsHtml() {
        if (!context.currentChat) return '<div class="empty-inspector">Select a thread.</div>';
        const cards = availableArtifacts();
        return `<section class="inspect-section"><div class="inspect-title">Outputs <span>${cards.length}</span></div>${cards.map(({ artifact, index }) => `<button class="artifact-card" data-artifact-index="${index}"><strong>${actions.escapeHtml(artifact.title || artifact.artifact_type)}</strong><span>${actions.escapeHtml(artifact.summary || artifact.content_format)}</span></button>`).join('')}</section>`;
    }

    function inspectorPlanHtml() {
        if (!context.currentChat) return '<div class="empty-inspector">Select a thread.</div>';
        const revisions = context.plans || [];
        if (!revisions.length) return '<div class="empty-inspector">No durable plan has been generated for this thread yet.</div>';
        return `<section class="inspect-section"><div class="inspect-title">Execution outlines <span>${revisions.length} versions</span></div>${revisions
            .map((plan) => {
                const steps = plan.snapshot?.steps || [];
                const action =
                    plan.status === 'draft'
                        ? `<button class="wide-button" data-plan-transition="approved" data-plan-id="${Number(plan.id)}">Approve version ${Number(plan.revision)}</button>`
                        : plan.status === 'approved'
                          ? `<button class="wide-button" data-plan-run="${Number(plan.id)}">Run approved plan</button>`
                          : '';
                return `<article class="plan-revision ${actions.escapeHtml(plan.status)}"><header><strong>Version ${Number(plan.revision)}</strong><span>${actions.escapeHtml(actions.statusLabel(plan.status))}</span></header><p>${actions.escapeHtml(plan.instruction || 'Generated plan')}</p><small>${steps.length} steps · ${actions.escapeHtml(plan.mode || 'develop')}</small>${action}</article>`;
            })
            .join('')}</section>`;
    }

    function inspectorGoalHtml() {
        if (!context.currentChat) return '<div class="empty-inspector">Select a thread.</div>';
        const goal = (context.plans || []).find((plan) => plan.mode === 'goal') || null;
        const run = context.currentRun;
        return `<section class="inspect-section"><div class="inspect-title">Persistent outcome <span>${actions.escapeHtml(goal?.status || 'ready')}</span></div><div class="goal-card"><strong>${actions.escapeHtml(context.currentChat.title || 'Development goal')}</strong><p>${actions.escapeHtml(goal?.instruction || 'Use Goal mode to keep the harness pursuing an outcome across development turns.')}</p><div class="surface-row">Current turn<span>${actions.escapeHtml(actions.statusLabel(run?.status || 'not started'))}</span></div><div class="surface-row">Plan version<span>${goal ? Number(goal.revision) : 'None'}</span></div></div></section>`;
    }

    function inspectorChangesHtml() {
        const run = context.currentRun;
        if (!run) return '<div class="empty-inspector">File changes will appear when implementation starts.</div>';
        const directFiles = Array.isArray(run.changes?.files) ? run.changes.files : [];
        if (directFiles.length) {
            if (context.changeReviewLoading) return '<div class="empty-inspector">Loading changed lines...</div>';
            const review = context.changeReview;
            const reviewFiles = Array.isArray(review?.review_files) ? review.review_files : [];
            const selected = reviewFiles.find((file) => file.path === context.selectedChangePath);
            if (selected) return inspectorFileDiffHtml(selected);
            const files = reviewFiles.length ? reviewFiles : directFiles;
            const changeSet = review || run.changes;
            const canUndo = Boolean(changeSet?.reversible) && !changeSet?.undone;
            return `<section class="change-review-list" aria-label="Changed files">
                <div class="change-review-summary"><span>${files.length} ${files.length === 1 ? 'file' : 'files'} changed</span><b>+${Number(changeSet?.additions || 0)}</b><i>-${Number(changeSet?.deletions || 0)}</i><div class="change-review-actions">${review?.diff ? '<button type="button" data-copy-all-diff aria-label="Copy all changes" title="Copy all changes">Copy</button>' : ''}${canUndo ? '<button type="button" data-inspector-undo class="danger" aria-label="Undo turn file changes" title="Undo turn file changes">Undo</button>' : ''}</div></div>
                <div class="change-file-list">${files
                    .slice(0, 100)
                    .map(
                        (file) =>
                            `<button type="button" class="change-file-row" data-review-file="${actions.escapeHtml(file.path || '')}"><span class="change-file-status" aria-hidden="true">${actions.escapeHtml(String(file.status || 'M').trim() || 'M')}</span><strong>${actions.escapeHtml(file.path || 'Changed file')}</strong><span class="change-counts"><b>+${Number(file.additions || 0)}</b><i>-${Number(file.deletions || 0)}</i></span><span aria-hidden="true">›</span></button>`
                    )
                    .join('')}</div>
            </section>`;
        }
        const before = actions.gitStatusEntries(run.git_status_before);
        const after = actions.gitStatusEntries(run.git_status_after || run.git_status_current);
        const rows = after.length ? after : before;
        return `<section class="inspect-section"><div class="inspect-title">Working tree <span>${rows.length} paths</span></div>${
            rows
                .slice(0, 60)
                .map(
                    (path) =>
                        `<div class="change-card"><strong>${actions.escapeHtml(path)}</strong><span>${after.length ? 'Current change' : 'Present before turn'}</span></div>`
                )
                .join('') || '<div class="empty-inspector">No changed files reported.</div>'
        }</section>`;
    }

    function inspectorFileDiffHtml(file) {
        const hunks = Array.isArray(file.hunks) ? file.hunks : [];
        const rows = hunks
            .map(
                (hunk) =>
                    `<section class="diff-hunk"><div class="diff-hunk-header">${actions.escapeHtml(hunk.header || '')}</div>${(hunk.lines || [])
                        .map((line) => {
                            const marker = line.kind === 'addition' ? '+' : line.kind === 'deletion' ? '-' : line.kind === 'context' ? ' ' : '\\';
                            return `<div class="diff-line ${actions.escapeHtml(line.kind || 'context')}"><span class="diff-line-number">${line.old_line ?? ''}</span><span class="diff-line-number">${line.new_line ?? ''}</span><span class="diff-marker" aria-hidden="true">${marker}</span><code>${actions.escapeHtml(line.content || '')}</code></div>`;
                        })
                        .join('')}</section>`
            )
            .join('');
        const empty = file.binary
            ? '<div class="empty-inspector">Binary file changed. A line diff is not available.</div>'
            : '<div class="empty-inspector">No textual line diff is available for this file.</div>';
        return `<section class="change-file-review" aria-label="Review ${actions.escapeHtml(file.path || 'changed file')}"><header class="change-file-review-header"><button type="button" data-review-back aria-label="Back to changed files" title="Back to changed files">‹</button><div><strong title="${actions.escapeHtml(file.path || '')}">${actions.escapeHtml(file.path || 'Changed file')}</strong><span><b>+${Number(file.additions || 0)}</b> <i>-${Number(file.deletions || 0)}</i></span></div><button type="button" data-copy-file-diff aria-label="Copy file diff" title="Copy file diff">Copy</button></header><div class="diff-viewport">${rows || empty}</div></section>`;
    }

    async function loadInspectorChanges(force) {
        if (!context.currentChat || context.changeReviewLoading || (context.changeReview && !force)) return;
        context.changeReviewLoading = true;
        renderInspector();
        try {
            context.changeReview = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/execution/changes`);
        } catch (error) {
            actions.toast(error.message || 'Could not load the changed lines.', 'error');
        } finally {
            context.changeReviewLoading = false;
            if (context.currentTab === 'changes') renderInspector();
        }
    }

    function evidenceItems(run) {
        if (!run) return [];
        const evidence = [];
        const backend = run.latest_backend_handoff || {};
        if (backend.git_status_after?.length) evidence.push(['Changed files', `${backend.git_status_after.length} paths reported by the executor`]);
        if (run.runtime_snapshot) evidence.push(['Runtime snapshot', 'Terminal sessions and detected URLs captured']);
        if (run.last_codex_bridge_state && Object.keys(run.last_codex_bridge_state).length)
            evidence.push(['IDE activity', 'Latest delegated worker state captured']);
        if (run.provider_preflight && Object.keys(run.provider_preflight).length)
            evidence.push(['Model preflight', 'Provider readiness checked before dispatch']);
        return evidence;
    }

    function inspectorEvidenceHtml() {
        const evidence = evidenceItems(context.currentRun);
        return `<section class="inspect-section"><div class="inspect-title">Evidence packet <span>${evidence.length}</span></div>${evidence.map((row) => `<div class="evidence-card"><strong>${actions.escapeHtml(row[0])}</strong><span>${actions.escapeHtml(row[1])}</span></div>`).join('') || '<div class="empty-inspector">This turn has not reported evidence yet.</div>'}</section>`;
    }

    function renderInspector() {
        const changes = context.currentRun
            ? context.currentRun.changes?.files ||
              context.currentRun.git_status_after ||
              context.currentRun.git_status_current ||
              context.currentRun.git_status_before ||
              []
            : [];
        const hasPlan =
            Boolean(context.currentChat) && (context.currentChat.autonomy_level === 'plan' || (context.plans || []).some((plan) => plan.mode === 'plan'));
        const hasGoal =
            Boolean(context.currentChat) && (context.currentChat.autonomy_level === 'goal' || (context.plans || []).some((plan) => plan.mode === 'goal'));
        const available = {
            run: Boolean(context.currentChat),
            plan: hasPlan,
            goal: hasGoal,
            artifacts: Boolean(availableArtifacts().length),
            changes: Boolean(changes.length)
        };
        document.querySelectorAll('.inspector-tabs button').forEach((button) => {
            button.classList.toggle('hidden', !available[button.dataset.tab]);
        });
        if (!available[context.currentTab]) context.currentTab = 'run';
        el('studio-shell').classList.toggle('files-review-open', context.currentTab === 'changes');
        document.querySelectorAll('.inspector-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.tab === context.currentTab));
        const content = el('inspector-content');
        const html =
            context.currentTab === 'plan'
                ? inspectorPlanHtml()
                : context.currentTab === 'goal'
                  ? inspectorGoalHtml()
                  : context.currentTab === 'artifacts'
                    ? inspectorArtifactsHtml()
                    : context.currentTab === 'changes'
                      ? inspectorChangesHtml()
                      : inspectorRunHtml();
        content.innerHTML = html;
        content.querySelector('[data-open-model]')?.addEventListener('click', actions.openModelDialog);
        content.querySelector('[data-open-thread-workflow]')?.addEventListener('click', async (event) => {
            actions.setWorkspaceMode('workflows', { focus: false });
            await actions.selectWorkflow(Number(event.currentTarget.dataset.openThreadWorkflow));
        });
        content
            .querySelectorAll('[data-plan-transition]')
            .forEach((button) => button.addEventListener('click', () => transitionPlan(Number(button.dataset.planId), button.dataset.planTransition)));
        content
            .querySelectorAll('[data-plan-run]')
            .forEach((button) => button.addEventListener('click', () => runApprovedPlan(Number(button.dataset.planRun))));
        content.querySelector('[data-continue-run]')?.addEventListener('click', actions.continueRun);
        content.querySelector('[data-create-automation]')?.addEventListener('click', () => actions.openAutomationDialog(null));
        content
            .querySelectorAll('[data-edit-automation]')
            .forEach((button) =>
                button.addEventListener('click', () =>
                    actions.openAutomationDialog(context.automations.find((automation) => automation.id === button.dataset.editAutomation) || null)
                )
            );
        content
            .querySelectorAll('[data-run-automation]')
            .forEach((button) => button.addEventListener('click', () => actions.runAutomation(button.dataset.runAutomation)));
        content
            .querySelectorAll('[data-toggle-automation]')
            .forEach((button) =>
                button.addEventListener('click', () => actions.toggleAutomation(button.dataset.toggleAutomation, button.dataset.automationAction))
            );
        content
            .querySelectorAll('[data-interaction-action]')
            .forEach((button) =>
                button.addEventListener('click', () => actions.resolveInteraction(button.dataset.interactionToken, button.dataset.interactionAction))
            );
        content.querySelector('[data-focus-steer]')?.addEventListener('click', () => {
            actions.closeInspector();
            el('task-prompt').focus();
        });
        content
            .querySelectorAll('[data-edit-command]')
            .forEach((button) => button.addEventListener('click', () => editQueuedCommand(Number(button.dataset.editCommand))));
        content
            .querySelectorAll('[data-cancel-command]')
            .forEach((button) => button.addEventListener('click', () => cancelQueuedCommand(Number(button.dataset.cancelCommand))));
        content.querySelector('[data-run-doctor]')?.addEventListener('click', (event) => runProjectDoctor(Number(event.currentTarget.dataset.runDoctor)));
        content.querySelector('[data-capture-skill]')?.addEventListener('click', captureRunSkill);
        content
            .querySelectorAll('[data-artifact-index]')
            .forEach((button) => button.addEventListener('click', () => openArtifact(Number(button.dataset.artifactIndex))));
        content.querySelectorAll('[data-review-file]').forEach((button) =>
            button.addEventListener('click', () => {
                context.selectedChangePath = button.dataset.reviewFile || '';
                renderInspector();
            })
        );
        content.querySelector('[data-review-back]')?.addEventListener('click', () => {
            context.selectedChangePath = '';
            renderInspector();
        });
        content.querySelector('[data-copy-all-diff]')?.addEventListener('click', () => actions.copyText(context.changeReview?.diff || '', 'Changes copied.'));
        content.querySelector('[data-copy-file-diff]')?.addEventListener('click', () => {
            const file = (context.changeReview?.review_files || []).find((item) => item.path === context.selectedChangePath);
            actions.copyText(file?.diff || '', 'File changes copied.');
        });
        content.querySelector('[data-inspector-undo]')?.addEventListener('click', actions.undoTurnChanges);
    }

    function editQueuedCommand(commandId) {
        const command = (context.controlState.commands || []).find((item) => Number(item.id) === Number(commandId));
        if (!command) return;
        context.editingCommandId = Number(command.id);
        el('task-prompt').value = command.content || '';
        el('task-prompt').placeholder = 'Edit queued guidance...';
        actions.resizePrompt();
        actions.closeInspector();
        el('task-prompt').focus();
    }

    async function cancelQueuedCommand(commandId) {
        try {
            await actions.api(`/workflows/studio/commands/${commandId}`, {
                method: 'PATCH',
                body: { cancel: true }
            });
            actions.toast('Queued guidance cancelled.');
            await actions.refreshShell({ preserveConversation: true });
        } catch (error) {
            actions.toast(error.message || 'Could not cancel the guidance.', 'error');
        }
    }

    async function transitionPlan(planId, status) {
        try {
            await actions.api(`/workflows/studio/plans/${planId}`, {
                method: 'PATCH',
                body: { status }
            });
            context.plans = (await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/plans`)).items || [];
            renderInspector();
            actions.toast(status === 'approved' ? 'Plan approved.' : 'Plan updated.');
            return true;
        } catch (error) {
            actions.toast(error.message || 'Could not update the plan.', 'error');
            return false;
        }
    }

    async function runApprovedPlan(planId) {
        try {
            if (!(await transitionPlan(planId, 'executing'))) return;
            await actions.startWorkflow();
        } catch (error) {
            actions.toast(error.message || 'Could not run the approved plan.', 'error');
        }
    }

    async function runProjectDoctor(projectId) {
        try {
            context.doctor = await actions.api(`/workflows/studio/projects/${projectId}/doctor`);
            renderInspector();
            actions.toast(context.doctor.ok ? 'Board harness is ready.' : 'Board harness needs attention.');
        } catch (error) {
            actions.toast(error.message || 'Could not check the board harness.', 'error');
        }
    }

    async function captureRunSkill() {
        if (!context.currentChat || !context.currentRun) return;
        try {
            const result = await actions.api(`/workflows/studio/tasks/${context.currentChat.id}/skills`, {
                method: 'POST',
                body: {
                    execution_session_id: context.currentRun.execution_session_id || context.currentRun.id
                }
            });
            actions.toast(result.summary || 'Reusable skill created.');
        } catch (error) {
            actions.toast(error.message || 'Could not create the board skill.', 'error');
        }
    }

    function openArtifact(index) {
        const artifact = context.artifacts[index];
        if (!artifact) return;
        context.activeArtifactIndex = index;
        el('artifact-viewer-kind').textContent = artifact.artifact_type || artifact.content_format || 'Artifact';
        el('artifact-viewer-title').textContent = artifact.title || 'Planning artifact';
        el('artifact-viewer-meta').textContent =
            `${artifact.content_format || 'document'} · ${artifact.status || 'planned'}${artifact.summary ? ` · ${artifact.summary}` : ''}`;
        el('artifact-viewer-content').textContent =
            artifact.content || 'This artifact is planned and will be populated by the orchestrator before implementation reaches its approval gate.';
        const link = el('artifact-viewer-link');
        const uri = actions.safeArtifactUri(artifact.uri);
        link.classList.toggle('hidden', !uri);
        if (uri) link.href = uri;
        const metadata = artifact.metadata || {};
        const beforeUri = actions.safeArtifactUri(metadata.before_uri);
        const afterUri = actions.safeArtifactUri(metadata.after_uri);
        const imageUri = actions.safeArtifactUri(
            metadata.preview_uri || (artifact.content_format === 'image' || /\.(?:png|jpe?g|webp|gif)(?:\?|$)/i.test(uri) ? uri : '')
        );
        const visual = el('artifact-visual');
        const beforeAfter = el('artifact-before-after');
        const image = el('artifact-image');
        beforeAfter.classList.toggle('hidden', !(beforeUri && afterUri));
        image.classList.toggle('hidden', !imageUri || Boolean(beforeUri && afterUri));
        visual.classList.toggle('hidden', !(imageUri || (beforeUri && afterUri)));
        if (imageUri) image.src = imageUri;
        if (beforeUri && afterUri) {
            el('artifact-before-image').src = beforeUri;
            el('artifact-after-image').src = afterUri;
        }
        const reviewable = String(artifact.status || '').toLowerCase() !== 'planned';
        el('artifact-approve').classList.toggle('hidden', !reviewable || artifact.status === 'approved');
        el('artifact-request-changes').classList.toggle('hidden', !reviewable || artifact.status === 'changes_requested');
        el('artifact-dialog').showModal();
    }

    async function reviewArtifact(status) {
        const artifact = context.artifacts[context.activeArtifactIndex];
        if (!artifact) return;
        try {
            const updated = await actions.api(`/workflows/studio/artifacts/${artifact.id}`, { method: 'PATCH', body: { status } });
            context.artifacts[context.activeArtifactIndex] = updated;
            el('artifact-dialog').close();
            renderInspector();
            actions.toast(status === 'approved' ? 'Artifact approved.' : 'Changes requested. Add the revision guidance in the composer.');
            if (status === 'changes_requested') el('task-prompt').focus();
        } catch (error) {
            actions.toast(error.message || 'Could not review the artifact.', 'error');
        }
    }
    return { loadInspectorChanges, renderInspector, reviewArtifact };
}
