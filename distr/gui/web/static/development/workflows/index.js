// Owns workflows rendering, interactions, and private view state.
export function createWorkflows({ context, actions, el }) {
    const state = {
        selectedWorkflowId: '',
        workflowSearch: '',
        workflowViewMode: window.localStorage.getItem('decisions.workflowViewMode') === 'list' ? 'list' : 'loop',
        workflowRuns: {},
        workflowBusy: {},
        workflowDetailsLoading: new Set(),
        workflowMemory: {}
    };
    function filteredWorkflows() {
        const query = state.workflowSearch.trim().toLowerCase();
        return context.workflows.filter((workflow) => !query || `${workflow.name || ''} ${workflow.description || ''}`.toLowerCase().includes(query));
    }

    function workflowStepList(workflow) {
        return Array.isArray(workflow?.steps) ? workflow.steps.slice().sort((a, b) => Number(a.position || 0) - Number(b.position || 0)) : [];
    }

    function workflowStepConfig(step) {
        if (step?.config && typeof step.config === 'object') return { ...step.config };
        if (typeof step?.config === 'string' && step.config.trim()) {
            try {
                return JSON.parse(step.config);
            } catch (_) {
                return {};
            }
        }
        return {};
    }

    function workflowInputObject(workflow) {
        const value = workflow?.workflow_input;
        if (value && typeof value === 'object' && !Array.isArray(value)) return { ...value };
        if (typeof value === 'string' && value.trim()) {
            try {
                const parsed = JSON.parse(value);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
            } catch (_) {
                /* preserve legacy plain-text workflow inputs */
            }
        }
        return {};
    }

    function workflowProjectId(workflow) {
        const metadataProjectId = Number(workflowInputObject(workflow).linked_project_id || 0);
        if (metadataProjectId) return metadataProjectId;
        const stepProjectId = Number(workflowStepList(workflow).find((step) => Number(step.linked_project_id || 0))?.linked_project_id || 0);
        if (stepProjectId) return stepProjectId;
        const boardProjectId = Number(context.boards.find((board) => Number(board.default_workflow_id || 0) === Number(workflow.id))?.project_id || 0);
        return boardProjectId || 0;
    }

    function workflowRunState(workflow) {
        const run = actions.runForWorkflow(workflow.id) || (state.workflowRuns[String(workflow.id)] || [])[0] || null;
        const status = String(run?.status || workflow.status || 'ready').toLowerCase();
        const active = ['initializing', 'queued', 'running', 'waiting', 'paused'].includes(status);
        return { run, status, active };
    }

    function workflowLibraryCategory(workflow) {
        const input = workflowInputObject(workflow);
        const searchable = `${workflow.name || ''} ${workflow.description || ''} ${workflow.workflow_input || ''}`.toLowerCase();
        if (
            String(input.preset_slug || '').toLowerCase() === 'development-ticket-to-implementation' ||
            String(workflow.name || '')
                .trim()
                .toLowerCase() === 'development'
        )
            return 'reusable';
        if (input.e2e_smoke || searchable.includes('pytest-of-') || searchable.includes('e2e workflow')) return 'test';
        if (String(workflow.workflow_type || '').toLowerCase() === 'instruction' || searchable.includes('whatsapp conversation snapshot')) return 'imported';
        return 'reusable';
    }

    function sortedWorkflowLibraryRows(workflows) {
        return workflows.slice().sort((left, right) => {
            const leftState = workflowRunState(left);
            const rightState = workflowRunState(right);
            if (leftState.active !== rightState.active) return leftState.active ? -1 : 1;
            return String(left.name || '').localeCompare(String(right.name || ''), undefined, { sensitivity: 'base' });
        });
    }

    function workflowActionIcon(action, busy) {
        if (busy) return '<svg class="workflow-spinner" aria-hidden="true" viewBox="0 0 20 20"><circle cx="10" cy="10" r="6"></circle></svg>';
        if (action === 'stop') return '<svg aria-hidden="true" viewBox="0 0 20 20"><rect x="6" y="6" width="8" height="8" rx="1.5"></rect></svg>';
        return '<svg aria-hidden="true" viewBox="0 0 20 20"><path d="M7 5.5v9l7-4.5z"></path></svg>';
    }

    function definitionIssue(workflow) {
        const steps = workflowStepList(workflow);
        const count = Number(workflow?.step_count || workflow?.steps_count || steps.length || 0);
        if (!count) return 'Add a step before running';
        if (steps.some((step) => (step.action_type || 'agent_instruction') === 'agent_instruction' && !String(step.instruction || '').trim()))
            return 'Add an instruction to each agent step';
        return '';
    }

    function workflowCardHtml(workflow, groupKind, project) {
        const { run, status, active } = workflowRunState(workflow);
        const busy = Boolean(state.workflowBusy[String(workflow.id)]);
        const steps = workflowStepList(workflow);
        const stepCount = Number(workflow.step_count || workflow.steps_count || steps.length || 0);
        const action = active ? 'stop' : 'start';
        const issue = definitionIssue(workflow);
        const preview = steps.length
            ? steps
                  .slice(0, 5)
                  .map(
                      (step, index) =>
                          `<li><span>${String(index + 1).padStart(2, '0')}</span><strong>${actions.escapeHtml(step.name || `Step ${index + 1}`)}</strong><small>${actions.escapeHtml(step.action_type || 'agent instruction')}</small></li>`
                  )
                  .join('')
            : `<li class="workflow-card-empty"><span>--</span><strong>${stepCount ? `${stepCount} configured steps` : 'No steps configured'}</strong></li>`;
        const scopeLabel =
            groupKind === 'project'
                ? `Project workflow · ${project?.name || 'Project'}`
                : groupKind === 'imported'
                  ? 'Imported workflow'
                  : groupKind === 'test'
                    ? 'Test workflow'
                    : 'Reusable workflow';
        return `<article class="workflow-card" data-workflow-card="${Number(workflow.id)}">
            <header class="workflow-card-titlebar">
                <button type="button" class="workflow-card-open" data-workflow-select="${Number(workflow.id)}"><span class="workflow-node-icon" aria-hidden="true"><i></i><i></i><i></i></span><strong>${actions.escapeHtml(workflow.name || 'Untitled workflow')}</strong></button>
                <span class="workflow-card-controls"><i class="workflow-state ${status === 'waiting' || status === 'paused' ? 'waiting' : status === 'failed' ? 'failed' : active ? 'running' : ''}" role="img" aria-label="${actions.escapeHtml(actions.statusLabel(status))}" title="${actions.escapeHtml(actions.statusLabel(status))}"></i><button type="button" class="workflow-card-action ${action}" data-workflow-action="${action}" data-workflow-id="${Number(workflow.id)}" aria-label="${active ? 'Stop' : 'Start'} ${actions.escapeHtml(workflow.name || 'workflow')}" title="${actions.escapeHtml(active ? 'Stop workflow' : issue || 'Start workflow')}"${busy || (!active && issue) ? ' disabled' : ''}>${workflowActionIcon(action, busy)}</button><details class="workflow-card-menu"><summary role="button" aria-label="More actions for ${actions.escapeHtml(workflow.name || 'workflow')}">•••</summary><div><button type="button" data-workflow-menu-action="open" data-workflow-id="${Number(workflow.id)}">Open and edit</button><button type="button" data-workflow-menu-action="${action}" data-workflow-id="${Number(workflow.id)}"${!active && issue ? ' disabled' : ''}>${active ? 'Stop workflow' : 'Run workflow'}</button><button type="button" class="danger" data-workflow-menu-action="delete" data-workflow-id="${Number(workflow.id)}">Delete workflow</button></div></details></span>
            </header>
            <button type="button" class="workflow-card-body" data-workflow-select="${Number(workflow.id)}">
                <ol>${preview}</ol>
            </button>
            <footer><span>${actions.escapeHtml(scopeLabel)}</span><span>${stepCount} step${stepCount === 1 ? '' : 's'} · ${run ? actions.escapeHtml(actions.statusLabel(status)) : actions.escapeHtml(issue || 'Ready')}</span></footer>
        </article>`;
    }

    function workflowGroupHtml(project, workflows, groupKind) {
        const name = groupKind === 'imported' ? 'Imported workflows' : groupKind === 'test' ? 'Test workflows' : project?.name || 'Reusable workflows';
        const help =
            groupKind === 'project'
                ? `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} linked to ${project?.name || 'this project'} and available to its ticket threads`
                : groupKind === 'imported'
                  ? `${workflows.length} one-off workflow${workflows.length === 1 ? '' : 's'} created from messages or imported requests`
                  : groupKind === 'test'
                    ? `${workflows.length} development smoke test${workflows.length === 1 ? '' : 's'}, kept separate from production workflows`
                    : `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} available to any project or thread`;
        return `<section class="workflow-project-group workflow-group-${actions.escapeHtml(groupKind)}" data-workflow-project="${Number(project?.id || 0)}"><header><div><span class="workflow-project-mark" aria-hidden="true">${project ? '◆' : groupKind === 'test' ? 'T' : groupKind === 'imported' ? 'I' : '◇'}</span><div><h2>${actions.escapeHtml(name)}</h2><p>${actions.escapeHtml(help)}</p></div></div></header><div class="workflow-card-grid">${sortedWorkflowLibraryRows(
            workflows
        )
            .map((workflow) => workflowCardHtml(workflow, groupKind, project))
            .join('')}</div></section>`;
    }

    function activeExecutionRowsHtml() {
        const runs = (context.runs || []).filter((run) =>
            ['initializing', 'queued', 'running', 'waiting', 'paused'].includes(String(run.status || '').toLowerCase())
        );
        if (!runs.length) return '';
        const rows = runs
            .map((run) => {
                const status = String(run.status || 'running').toLowerCase();
                const kind = run.execution_kind === 'development' ? 'Direct Development' : run.workflow_name || 'Workflow';
                const cancelUrl =
                    run.cancellation_target?.url || (run.workflow_id && run.id ? `/api/workflows/${Number(run.workflow_id)}/cancel-run/${Number(run.id)}` : '');
                return `<article class="workflow-active-execution" data-active-execution="${actions.escapeHtml(run.id)}">
                <div><i class="workflow-state ${status === 'waiting' || status === 'paused' ? 'waiting' : 'running'}"></i><span><strong>${actions.escapeHtml(run.ticket_title || kind)}</strong><small>${actions.escapeHtml(kind)} · ${actions.escapeHtml(actions.statusLabel(status))}${run.project_name ? ` · ${actions.escapeHtml(run.project_name)}` : ''}</small></span></div>
                <details class="workflow-run-menu"><summary aria-label="Execution actions">•••</summary><div>
                    ${run.open_url ? `<a href="${actions.escapeHtml(run.open_url)}">Open</a>` : ''}
                    ${run.related_ticket_url ? `<a href="${actions.escapeHtml(run.related_ticket_url)}">Related ticket</a>` : ''}
                    ${status === 'waiting' && run.open_url ? `<a class="waiting" href="${actions.escapeHtml(run.open_url)}">Continue / Respond</a>` : ''}
                    ${cancelUrl ? `<button type="button" data-active-execution-cancel="${actions.escapeHtml(cancelUrl)}">Cancel</button>` : ''}
                </div></details>
            </article>`;
            })
            .join('');
        return `<section class="workflow-project-group workflow-active-executions"><header><div><span class="workflow-project-mark" aria-hidden="true">●</span><div><h2>Active executions</h2><p>${runs.length} currently active</p></div></div></header><div class="workflow-active-execution-list">${rows}</div></section>`;
    }

    function hydrateWorkflowCards(workflows) {
        workflows
            .filter((workflow) => !Array.isArray(workflow.steps) && !state.workflowDetailsLoading.has(Number(workflow.id)))
            .slice(0, 40)
            .forEach((workflow) => {
                state.workflowDetailsLoading.add(Number(workflow.id));
                actions
                    .api(`/workflows/${Number(workflow.id)}`)
                    .then((detail) => {
                        const index = context.workflows.findIndex((item) => Number(item.id) === Number(workflow.id));
                        if (index >= 0)
                            context.workflows[index] = {
                                ...context.workflows[index],
                                ...detail
                            };
                    })
                    .catch(() => null)
                    .finally(() => {
                        state.workflowDetailsLoading.delete(Number(workflow.id));
                        if (context.workspaceMode === 'workflows' && !state.selectedWorkflowId) renderWorkflowWorkspace();
                    });
            });
    }

    function renderWorkflowWorkspace() {
        const list = el('workflow-list');
        const home = el('workflow-home');
        const detail = el('workflow-detail');
        if (!list || !home || !detail) return;
        const selected = context.workflows.find((item) => String(item.id) === String(state.selectedWorkflowId));
        home.classList.toggle('hidden', Boolean(selected));
        detail.classList.toggle('hidden', !selected);
        if (selected) {
            renderWorkflowDetail();
            return;
        }
        const workflows = filteredWorkflows();
        const productionWorkflows = workflows.filter((workflow) => workflowLibraryCategory(workflow) === 'reusable');
        const projectGroups = context.projects
            .map((project) => ({
                project,
                workflows: productionWorkflows.filter((workflow) => workflowProjectId(workflow) === Number(project.id)),
                kind: 'project'
            }))
            .filter((group) => group.workflows.length)
            .sort((left, right) => String(left.project?.name || '').localeCompare(String(right.project?.name || ''), undefined, { sensitivity: 'base' }));
        const shared = productionWorkflows.filter((workflow) => !workflowProjectId(workflow));
        if (shared.length)
            projectGroups.push({
                project: null,
                workflows: shared,
                kind: 'reusable'
            });
        const imported = workflows.filter((workflow) => workflowLibraryCategory(workflow) === 'imported');
        if (imported.length)
            projectGroups.push({
                project: null,
                workflows: imported,
                kind: 'imported'
            });
        const tests = workflows.filter((workflow) => workflowLibraryCategory(workflow) === 'test');
        if (tests.length) projectGroups.push({ project: null, workflows: tests, kind: 'test' });
        const activeExecutions = activeExecutionRowsHtml();
        const groupsHtml = projectGroups.length
            ? projectGroups.map((group) => workflowGroupHtml(group.project, group.workflows, group.kind)).join('')
            : '<div class="development-empty">No workflows match this view.</div>';
        list.innerHTML = activeExecutions + groupsHtml;
        list.querySelectorAll('[data-workflow-select]').forEach((button) =>
            button.addEventListener('click', () => selectWorkflow(Number(button.dataset.workflowSelect)))
        );
        list.querySelectorAll('[data-workflow-action]').forEach((button) =>
            button.addEventListener('click', () => {
                const workflowId = Number(button.dataset.workflowId);
                if (button.dataset.workflowAction === 'stop')
                    stopManagedWorkflow(workflowId, workflowRunState(context.workflows.find((item) => Number(item.id) === workflowId)).run);
                else runManagedWorkflow(workflowId);
            })
        );
        list.querySelectorAll('[data-workflow-menu-action]').forEach((button) =>
            button.addEventListener('click', () => {
                const workflowId = Number(button.dataset.workflowId);
                const workflow = context.workflows.find((item) => Number(item.id) === workflowId);
                button.closest('details')?.removeAttribute('open');
                if (button.dataset.workflowMenuAction === 'open') selectWorkflow(workflowId);
                else if (button.dataset.workflowMenuAction === 'delete') deleteManagedWorkflow(workflowId);
                else if (button.dataset.workflowMenuAction === 'stop') stopManagedWorkflow(workflowId, workflowRunState(workflow).run);
                else runManagedWorkflow(workflowId);
            })
        );
        list.querySelectorAll('[data-active-execution-cancel]').forEach((button) =>
            button.addEventListener('click', async () => {
                button.disabled = true;
                try {
                    await actions.api(button.dataset.activeExecutionCancel, {
                        method: 'POST',
                        body: {}
                    });
                    actions.toast('Execution cancelled.');
                    await actions.refreshShell({ preserveConversation: true });
                } catch (error) {
                    button.disabled = false;
                    actions.toast(error.message || 'Could not cancel the execution.', 'error');
                }
            })
        );
        hydrateWorkflowCards(workflows);
    }

    async function selectWorkflow(workflowId) {
        state.selectedWorkflowId = String(workflowId || '');
        try {
            const detail = await actions.api(`/workflows/${Number(workflowId)}`);
            const index = context.workflows.findIndex((item) => Number(item.id) === Number(workflowId));
            if (index >= 0) context.workflows[index] = detail;
            if (!state.workflowRuns[String(workflowId)]) {
                const history = await actions.api(`/workflows/${Number(workflowId)}/runs?limit=10`);
                state.workflowRuns[String(workflowId)] = Array.isArray(history) ? history : history.runs || [];
            }
        } catch (error) {
            actions.toast(error.message || 'Could not load the workflow.', 'error');
        }
        renderWorkflowWorkspace();
    }

    function workflowStepOutcome(step) {
        const config = workflowStepConfig(step);
        const outputs = config.expected_outputs || config.expected_output || config.outcome || step?.verification || '';
        if (Array.isArray(outputs))
            return outputs
                .map((item) => String(item || '').trim())
                .filter(Boolean)
                .join(', ');
        return String(outputs || '').trim();
    }

    function workflowStepSkills(step) {
        const config = workflowStepConfig(step);
        const skills = config.skills || config.skill_ids || [];
        return Array.isArray(skills) ? skills.map((item) => String(item || '').trim()).filter(Boolean) : [];
    }

    function workflowEditorIcon(name) {
        const paths = {
            up: '<path d="M5 12 10 7l5 5"></path><path d="M10 7v8"></path>',
            down: '<path d="m5 8 5 5 5-5"></path><path d="M10 13V5"></path>',
            edit: '<path d="m4 14-.5 3 3-.5L15 8l-2.5-2.5L4 14Z"></path><path d="m11.5 6.5 2.5 2.5"></path>',
            duplicate: '<rect x="7" y="7" width="9" height="9" rx="1.5"></rect><path d="M13 7V4H4v9h3"></path>',
            continue: '<path d="M4 6v5h9"></path><path d="m10 8 3 3-3 3"></path>',
            delete: '<path d="m6 6 8 8M14 6l-8 8"></path>'
        };
        return `<svg aria-hidden="true" viewBox="0 0 20 20">${paths[name] || ''}</svg>`;
    }

    function workflowLoopHtml(steps, active, issue = '') {
        if (!steps.length) return '<div class="workflow-empty-steps">No steps yet. Add one manually or describe the loop you want above.</div>';
        const count = steps.length;
        const markerId = `workflow-loop-arrow-${Number(state.selectedWorkflowId || 0)}`;
        const nodes = steps
            .map((step, index) => {
                const angle = ((-90 + (360 * index) / count) * Math.PI) / 180;
                const x = 50 + 40 * Math.cos(angle);
                const y = 50 + 37 * Math.sin(angle);
                const mobileX = 50 + 24 * Math.cos(angle);
                const mobileY = 50 + 38 * Math.sin(angle);
                const status = String(step.status || 'pending').toLowerCase();
                const skills = workflowStepSkills(step).slice(0, 2);
                const outcome = workflowStepOutcome(step);
                return `<article class="workflow-loop-node ${status}" role="listitem" style="--loop-x:${x.toFixed(2)}%;--loop-y:${y.toFixed(2)}%;--loop-mobile-x:${mobileX.toFixed(2)}%;--loop-mobile-y:${mobileY.toFixed(2)}%">
                <button type="button" data-workflow-step-edit="${Number(step.id)}" aria-label="Edit step ${index + 1}: ${actions.escapeHtml(step.name || `Step ${index + 1}`)}">
                    <span class="workflow-loop-node-top"><b>${String(index + 1).padStart(2, '0')}</b><i>${actions.escapeHtml(actions.statusLabel(status))}</i></span>
                    <strong>${actions.escapeHtml(step.name || `Step ${index + 1}`)}</strong>
                    ${outcome ? `<small><span>Outcome</span>${actions.escapeHtml(outcome)}</small>` : ''}
                    ${skills.length ? `<span class="workflow-loop-skills">${skills.map((skill) => `<em>${actions.escapeHtml(skill)}</em>`).join('')}</span>` : ''}
                </button>
            </article>`;
            })
            .join('');
        return `<div class="workflow-loop-viewport"><div class="workflow-loop-map${count > 8 ? ' dense' : ''}" role="list" aria-label="Workflow loop with ${count} steps">
            <svg class="workflow-loop-path" aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none"><defs><marker id="${markerId}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z"></path></marker></defs><path d="M50 6 A44 44 0 0 1 94 50" marker-end="url(#${markerId})"></path><path d="M94 50 A44 44 0 0 1 50 94" marker-end="url(#${markerId})"></path><path d="M50 94 A44 44 0 0 1 6 50" marker-end="url(#${markerId})"></path><path d="M6 50 A44 44 0 0 1 50 6" marker-end="url(#${markerId})"></path></svg>
            <div class="workflow-loop-core"><span>${actions.escapeHtml(active ? 'Running' : issue || (count ? 'Ready' : 'Add a step'))}</span><strong>${count}</strong><small>step loop</small></div>${nodes}
        </div></div>`;
    }

    function workflowStepListHtml(steps) {
        if (!steps.length) return '<div class="workflow-empty-steps">No steps yet. Add one manually or describe the loop you want above.</div>';
        return steps
            .map((step, index) => {
                const stepStatus = String(step.status || 'pending').toLowerCase();
                const activeStep = ['running', 'waiting'].includes(stepStatus);
                const config = workflowStepConfig(step);
                const freshAgent = String(config.agent_context_mode || '').toLowerCase() === 'fresh';
                const routing = [
                    step.validation_type && step.validation_type !== 'none' ? `Validate: ${step.validation_type}` : '',
                    step.routing_mode && step.routing_mode !== 'sequential' ? `Route: ${step.routing_mode}` : '',
                    freshAgent ? 'Fresh context' : ''
                ]
                    .filter(Boolean)
                    .join(' · ');
                const outcome = workflowStepOutcome(step);
                const skills = workflowStepSkills(step);
                return `<article class="workflow-editor-step" data-workflow-step="${Number(step.id)}" draggable="true"><span class="workflow-step-handle" title="Drag to reorder" aria-hidden="true"></span><span class="workflow-step-number">${String(index + 1).padStart(2, '0')}</span><div class="workflow-step-copy"><strong>${actions.escapeHtml(step.name || `Step ${index + 1}`)}</strong><p>${actions.escapeHtml(step.instruction || step.action_type || '')}</p><div class="workflow-step-support">${outcome ? `<span><b>Outcome</b>${actions.escapeHtml(outcome)}</span>` : ''}${skills.length ? `<span><b>Skills</b>${actions.escapeHtml(skills.join(', '))}</span>` : ''}</div>${routing ? `<small>${actions.escapeHtml(routing)}</small>` : ''}</div><em class="workflow-step-status ${actions.escapeHtml(stepStatus)}">${actions.escapeHtml(actions.statusLabel(stepStatus))}</em><div class="workflow-step-tools">${activeStep ? `<button type="button" class="workflow-icon-action stop" data-workflow-step-stop="${Number(step.id)}" aria-label="Stop step" title="Stop step">${workflowActionIcon('stop')}</button>` : `<button type="button" class="workflow-icon-action" data-workflow-step-move="${Number(step.id)}" data-direction="up" aria-label="Move step up" title="Move step up"${index === 0 ? ' disabled' : ''}>${workflowEditorIcon('up')}</button><button type="button" class="workflow-icon-action" data-workflow-step-move="${Number(step.id)}" data-direction="down" aria-label="Move step down" title="Move step down"${index === steps.length - 1 ? ' disabled' : ''}>${workflowEditorIcon('down')}</button><button type="button" class="workflow-icon-action" data-workflow-step-edit="${Number(step.id)}" aria-label="Edit step" title="Edit step">${workflowEditorIcon('edit')}</button><button type="button" class="workflow-icon-action" data-workflow-step-duplicate="${Number(step.id)}" aria-label="Duplicate step" title="Duplicate step">${workflowEditorIcon('duplicate')}</button><button type="button" class="workflow-icon-action start" data-workflow-step-run="${Number(step.id)}" aria-label="Run step" title="Run step">${workflowActionIcon('start')}</button><button type="button" class="workflow-icon-action" data-workflow-step-continue="${Number(step.id)}" aria-label="Run workflow from this step" title="Run from here">${workflowEditorIcon('continue')}</button><button type="button" class="workflow-icon-action danger" data-workflow-step-delete="${Number(step.id)}" aria-label="Delete step" title="Delete step">${workflowEditorIcon('delete')}</button>`}</div></article>`;
            })
            .join('');
    }

    function renderWorkflowDetail() {
        const detail = el('workflow-detail');
        if (!detail) return;
        const workflow = context.workflows.find((item) => String(item.id) === String(state.selectedWorkflowId));
        if (!workflow) {
            detail.innerHTML = '';
            return;
        }
        const steps = workflowStepList(workflow);
        const { run, status, active } = workflowRunState(workflow);
        const busy = Boolean(state.workflowBusy[String(workflow.id)]);
        const metadata = workflowInputObject(workflow);
        const projectId = workflowProjectId(workflow);
        const memory = state.workflowMemory[String(workflow.id)] || null;
        const viewMode = state.workflowViewMode === 'list' ? 'list' : 'loop';
        detail.innerHTML = `<header class="workflow-editor-header"><nav><button type="button" class="workflow-editor-back" aria-label="Back to workflows"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M12.5 4.5 7 10l5.5 5.5"></path></svg><span>Workflows</span></button><i>/</i><strong>${actions.escapeHtml(workflow.name || 'Workflow')}</strong></nav><div class="workflow-editor-actions"><button type="button" class="workflow-icon-action" data-workflow-duplicate aria-label="Duplicate workflow" title="Duplicate workflow">${workflowEditorIcon('duplicate')}</button><button type="button" class="workflow-run-button ${active ? 'stop' : 'start'}" data-workflow-detail-action="${active ? 'stop' : 'start'}" title="${actions.escapeHtml(definitionIssue(workflow) || 'Run workflow')}"${busy || (!active && definitionIssue(workflow)) ? ' disabled' : ''}>${workflowActionIcon(active ? 'stop' : 'start', busy)}<span>${active ? 'Stop run' : 'Run workflow'}</span></button></div></header>
            <div class="workflow-editor-scroll"><div class="workflow-builder-layout"><section class="workflow-step-section"><header class="workflow-canvas-header"><div class="workflow-change-copy"><h1>${actions.escapeHtml(workflow.name || 'Workflow')}</h1><p>${actions.escapeHtml(workflow.description || 'Shape the steps, skills, and outcomes that make this workflow useful.')}</p></div><div class="workflow-step-header-actions"><div class="workflow-view-switch" role="group" aria-label="Workflow view"><button type="button" data-workflow-view="loop" aria-pressed="${viewMode === 'loop'}"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M15.5 7A6 6 0 1 0 16 12"></path><path d="m13 4 3 3-3 3"></path></svg>Loop</button><button type="button" data-workflow-view="list" aria-pressed="${viewMode === 'list'}"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M6 5h10M6 10h10M6 15h10"></path><circle cx="3" cy="5" r=".7"></circle><circle cx="3" cy="10" r=".7"></circle><circle cx="3" cy="15" r=".7"></circle></svg>List</button></div><button type="button" class="secondary-button" data-workflow-add-step>Add step</button></div></header><div class="workflow-canvas-meta"><span><i class="workflow-state ${status === 'waiting' || status === 'paused' ? 'waiting' : status === 'failed' ? 'failed' : active ? 'running' : ''}"></i>${actions.escapeHtml(run ? actions.statusLabel(status) : definitionIssue(workflow) || 'Ready')}</span><strong>${steps.length} step${steps.length === 1 ? '' : 's'}</strong><small>Click a step to edit its instruction, skills, and outcome.</small></div><div class="workflow-step-stage" data-view="${viewMode}">${viewMode === 'loop' ? workflowLoopHtml(steps, active, definitionIssue(workflow)) : `<div class="workflow-step-stack">${workflowStepListHtml(steps)}</div>`}</div></section>
            <aside class="workflow-editor-rail"><section class="workflow-change-panel"><header><h2>Change this workflow</h2><p>Describe what should change. You can review the regenerated steps before running them.</p></header><form data-workflow-revise><label for="workflow-change-instruction">What should work differently?</label><textarea id="workflow-change-instruction" rows="7" placeholder="For example: add a security review after implementation, then route failures back to the build step."></textarea><div class="workflow-change-suggestions"><button type="button" data-workflow-suggestion="Add an independent review step before completion">Add a review step</button><button type="button" data-workflow-suggestion="Strengthen every step with a clear, testable outcome">Clarify outcomes</button></div><button type="submit" class="primary-button">Update workflow</button><small>This changes the step sequence, not the workflow settings.</small></form></section>
            <details class="workflow-advanced-section"><summary><span><strong>Workflow settings</strong><small>Name, objective, and run configuration</small></span><svg aria-hidden="true" viewBox="0 0 20 20"><path d="m6 8 4 4 4-4"></path></svg></summary><form class="workflow-settings-form"><div class="workflow-settings-grid"><label>Name<input data-workflow-name value="${actions.escapeHtml(workflow.name || '')}" required></label><label>Project<select data-workflow-project><option value="">Shared workflow</option>${context.projects.map((project) => `<option value="${Number(project.id)}"${Number(project.id) === projectId ? ' selected' : ''}>${actions.escapeHtml(project.name || 'Untitled project')}</option>`).join('')}</select></label></div><label>Description<textarea data-workflow-description rows="3">${actions.escapeHtml(workflow.description || '')}</textarea></label><label>Workflow objective and input<textarea data-workflow-objective rows="4">${actions.escapeHtml(metadata.objective || metadata.instruction || (typeof workflow.workflow_input === 'string' && !Object.keys(metadata).length ? workflow.workflow_input : ''))}</textarea></label><label>Context and memory rules<textarea data-workflow-context rows="4" placeholder="What should every step remember, preserve, and learn?">${actions.escapeHtml(typeof workflow.context_rules === 'string' ? workflow.context_rules : JSON.stringify(workflow.context_rules || '', null, 2).replace(/^"|"$/g, ''))}</textarea></label><details class="workflow-technical-settings"><summary>Advanced run settings</summary><label>Run settings<textarea data-workflow-run-settings rows="6" placeholder='{"max_parallel": 1}'>${actions.escapeHtml(JSON.stringify(workflow.run_settings || {}, null, 2))}</textarea></label></details><div class="workflow-settings-actions"><button type="submit" class="primary-button">Save settings</button><button type="button" class="danger-text-button" data-workflow-delete>Delete workflow</button></div></form></details>
            <details class="workflow-advanced-section workflow-memory-section"><summary><span><strong>Memory and runs</strong><small>Handoffs and learned context</small></span><svg aria-hidden="true" viewBox="0 0 20 20"><path d="m6 8 4 4 4-4"></path></svg></summary><div class="workflow-memory-panel"><button type="button" class="secondary-button" data-workflow-load-memory>${memory ? 'Refresh memory' : 'Load memory'}</button><div class="workflow-memory-content">${memory ? workflowMemoryHtml(memory) : '<p>Load the workflow memory to inspect handoffs, routing, and learned context.</p>'}</div></div></details></aside></div></div>`;
        detail.querySelector('.workflow-editor-back').addEventListener('click', () => {
            state.selectedWorkflowId = '';
            renderWorkflowWorkspace();
        });
        detail
            .querySelector('[data-workflow-detail-action]')
            .addEventListener('click', () => (active ? stopManagedWorkflow(Number(workflow.id), run) : runManagedWorkflow(Number(workflow.id))));
        detail.querySelector('[data-workflow-duplicate]').addEventListener('click', () => duplicateManagedWorkflow(Number(workflow.id)));
        detail.querySelector('[data-workflow-revise]').addEventListener('submit', (event) => reviseWorkflowFromPrompt(event, Number(workflow.id)));
        detail.querySelectorAll('[data-workflow-suggestion]').forEach((button) =>
            button.addEventListener('click', () => {
                const input = detail.querySelector('#workflow-change-instruction');
                input.value = button.dataset.workflowSuggestion || '';
                input.focus();
            })
        );
        detail.querySelectorAll('[data-workflow-view]').forEach((button) =>
            button.addEventListener('click', () => {
                state.workflowViewMode = button.dataset.workflowView === 'list' ? 'list' : 'loop';
                window.localStorage.setItem('decisions.workflowViewMode', state.workflowViewMode);
                renderWorkflowDetail();
            })
        );
        detail.querySelector('.workflow-settings-form').addEventListener('submit', (event) => saveWorkflowSettings(event, Number(workflow.id)));
        detail.querySelector('[data-workflow-delete]').addEventListener('click', () => deleteManagedWorkflow(Number(workflow.id)));
        detail.querySelector('[data-workflow-add-step]').addEventListener('click', () => openWorkflowStepDialog(Number(workflow.id), 0));
        detail.querySelector('[data-workflow-load-memory]').addEventListener('click', () => loadWorkflowMemory(Number(workflow.id)));
        detail
            .querySelectorAll('[data-workflow-step-edit]')
            .forEach((button) => button.addEventListener('click', () => openWorkflowStepDialog(Number(workflow.id), Number(button.dataset.workflowStepEdit))));
        detail
            .querySelectorAll('[data-workflow-step-move]')
            .forEach((button) =>
                button.addEventListener('click', () => moveWorkflowStep(Number(workflow.id), Number(button.dataset.workflowStepMove), button.dataset.direction))
            );
        detail
            .querySelectorAll('[data-workflow-step-duplicate]')
            .forEach((button) =>
                button.addEventListener('click', () => duplicateWorkflowStep(Number(workflow.id), Number(button.dataset.workflowStepDuplicate)))
            );
        detail
            .querySelectorAll('[data-workflow-step-delete]')
            .forEach((button) => button.addEventListener('click', () => deleteWorkflowStep(Number(workflow.id), Number(button.dataset.workflowStepDelete))));
        detail
            .querySelectorAll('[data-workflow-step-run]')
            .forEach((button) => button.addEventListener('click', () => executeWorkflowStep(Number(workflow.id), Number(button.dataset.workflowStepRun))));
        detail
            .querySelectorAll('[data-workflow-step-continue]')
            .forEach((button) => button.addEventListener('click', () => runWorkflowFromStep(Number(workflow.id), Number(button.dataset.workflowStepContinue))));
        detail
            .querySelectorAll('[data-workflow-step-stop]')
            .forEach((button) => button.addEventListener('click', () => stopWorkflowStep(Number(workflow.id), Number(button.dataset.workflowStepStop))));
        bindWorkflowStepReorder(detail, Number(workflow.id));
        const loopViewport = detail.querySelector('.workflow-loop-viewport');
        if (loopViewport)
            window.requestAnimationFrame(() => {
                if (loopViewport.scrollWidth > loopViewport.clientWidth) {
                    loopViewport.scrollLeft = Math.max(0, (loopViewport.scrollWidth - loopViewport.clientWidth) / 2);
                }
            });
    }

    function workflowMemoryHtml(memory) {
        const summary = memory.summary || memory;
        const routing = Array.isArray(memory.step_routing_table) ? memory.step_routing_table : [];
        const facts = [summary.handoff_preview || memory.handoff_preview, summary.workspace || summary.summary || summary.memory].filter(Boolean);
        return `${facts.length ? facts.map((fact) => `<pre>${actions.escapeHtml(typeof fact === 'string' ? fact : JSON.stringify(fact, null, 2))}</pre>`).join('') : '<p>No learned workflow memory has been recorded yet.</p>'}${routing.length ? `<div class="workflow-routing-table">${routing.map((row) => `<div><strong>${actions.escapeHtml(row.step_name || row.name || `Step ${row.step_id || ''}`)}</strong><span>${actions.escapeHtml(row.route || row.model || row.provider || 'Automatic')}</span></div>`).join('')}</div>` : ''}`;
    }

    async function loadWorkflowMemory(workflowId) {
        try {
            state.workflowMemory[String(workflowId)] = await actions.api(`/workflows/${workflowId}/workspace-memory`);
            renderWorkflowDetail();
        } catch (error) {
            actions.toast(error.message || 'Could not load workflow memory.', 'error');
        }
    }

    async function saveWorkflowSettings(event, workflowId) {
        event.preventDefault();
        const form = event.currentTarget;
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        const workflowInput = workflowInputObject(workflow);
        workflowInput.linked_project_id = Number(form.querySelector('[data-workflow-project]').value || 0) || null;
        workflowInput.objective = form.querySelector('[data-workflow-objective]').value.trim();
        let runSettings = {};
        try {
            runSettings = JSON.parse(form.querySelector('[data-workflow-run-settings]').value.trim() || '{}');
        } catch (_) {
            actions.toast('Run settings must be valid JSON.', 'error');
            return;
        }
        try {
            await actions.api(`/workflows/${workflowId}`, {
                method: 'PATCH',
                body: {
                    name: form.querySelector('[data-workflow-name]').value.trim(),
                    description: form.querySelector('[data-workflow-description]').value.trim(),
                    workflow_input: JSON.stringify(workflowInput),
                    context_rules: form.querySelector('[data-workflow-context]').value.trim(),
                    run_settings: runSettings
                }
            });
            actions.toast('Workflow saved.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not save the workflow.', 'error');
        }
    }

    async function duplicateManagedWorkflow(workflowId) {
        try {
            const result = await actions.api(`/workflows/${workflowId}/duplicate`, {
                method: 'POST',
                body: {}
            });
            const createdId = Number(result.id || result.workflow_id || result.workflow?.id || 0);
            await actions.refreshShell({ preserveConversation: true });
            if (createdId) await selectWorkflow(createdId);
            actions.toast('Workflow duplicated.');
        } catch (error) {
            actions.toast(error.message || 'Could not duplicate the workflow.', 'error');
        }
    }

    function bindWorkflowStepReorder(container, workflowId) {
        let draggedId = 0;
        container.querySelectorAll('.workflow-editor-step').forEach((row) => {
            row.addEventListener('dragstart', () => {
                draggedId = Number(row.dataset.workflowStep);
                row.classList.add('dragging');
            });
            row.addEventListener('dragend', () => {
                row.classList.remove('dragging');
                draggedId = 0;
            });
            row.addEventListener('dragover', (event) => event.preventDefault());
            row.addEventListener('drop', async (event) => {
                event.preventDefault();
                const targetId = Number(row.dataset.workflowStep);
                if (!draggedId || draggedId === targetId) return;
                const ids = workflowStepList(context.workflows.find((item) => Number(item.id) === workflowId)).map((step) => Number(step.id));
                const from = ids.indexOf(draggedId);
                const to = ids.indexOf(targetId);
                ids.splice(to, 0, ids.splice(from, 1)[0]);
                try {
                    await actions.api(`/workflows/${workflowId}/steps/reorder`, {
                        method: 'PATCH',
                        body: { step_ids: ids }
                    });
                    await selectWorkflow(workflowId);
                } catch (error) {
                    actions.toast(error.message || 'Could not reorder workflow steps.', 'error');
                }
            });
        });
    }

    async function moveWorkflowStep(workflowId, stepId, direction) {
        const ids = workflowStepList(context.workflows.find((item) => Number(item.id) === Number(workflowId))).map((step) => Number(step.id));
        const from = ids.indexOf(Number(stepId));
        const to = direction === 'up' ? from - 1 : from + 1;
        if (from < 0 || to < 0 || to >= ids.length) return;
        [ids[from], ids[to]] = [ids[to], ids[from]];
        try {
            await actions.api(`/workflows/${workflowId}/steps/reorder`, {
                method: 'PATCH',
                body: { step_ids: ids }
            });
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not reorder workflow steps.', 'error');
        }
    }

    function syncWorkflowStepModels(selectedModel) {
        const provider = el('workflow-step-provider').value;
        el('workflow-step-model').innerHTML =
            '<option value="">Automatic</option>' +
            actions
                .catalogModels(provider)
                .map((model) => {
                    const id = String(model.id || model.name || model);
                    const label = String(model.name || model.id || model);
                    return `<option value="${actions.escapeHtml(id)}"${String(selectedModel || '') === id ? ' selected' : ''}>${actions.escapeHtml(label)}</option>`;
                })
                .join('');
    }

    function workflowStepOptionHtml(items, selected, kind) {
        const chosen = new Set((selected || []).map(String));
        const optionId = (item) => String(item.id || item.slug || item.name || item);
        const ordered = Array.from(items || []).sort((left, right) => {
            const selectedOrder = Number(chosen.has(optionId(right))) - Number(chosen.has(optionId(left)));
            if (selectedOrder) return selectedOrder;
            return String(left.name || left.label || optionId(left)).localeCompare(String(right.name || right.label || optionId(right)));
        });
        return ordered
            .map((item) => {
                const id = String(item.id || item.slug || item.name || item);
                const label = String(item.name || item.label || item.id || item);
                return `<label class="workflow-option"><input type="checkbox" data-workflow-${kind}-option value="${actions.escapeHtml(id)}"${chosen.has(id) ? ' checked' : ''}><span>${actions.escapeHtml(label)}</span></label>`;
            })
            .join('');
    }

    function selectedWorkflowOptions(selector) {
        return Array.from(document.querySelectorAll(selector))
            .filter((input) => input.checked)
            .map((input) => input.value);
    }

    async function openWorkflowStepDialog(workflowId, stepId) {
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        const step = workflowStepList(workflow).find((item) => Number(item.id) === Number(stepId));
        if (!workflow || (stepId && !step)) return;
        const config = workflowStepConfig(step);
        el('workflow-step-workflow-id').value = String(workflowId);
        el('workflow-step-id').value = step ? String(stepId) : '';
        el('workflow-step-dialog-title').textContent = step ? 'Edit step' : 'Add step';
        el('workflow-step-name').value = step?.name || '';
        el('workflow-step-action').value = step?.action_type || 'agent_instruction';
        el('workflow-step-instruction').value = step?.instruction || '';
        el('workflow-step-outcome').value = workflowStepOutcome(step);
        el('workflow-step-validation').value = step?.validation_type || 'none';
        el('workflow-step-validation-prompt').value = step?.validation_prompt || '';
        el('workflow-step-routing').value = step?.routing_mode || 'sequential';
        el('workflow-step-routing-prompt').value = step?.routing_prompt || '';
        el('workflow-step-retries').value = Number(step?.max_retries || 0);
        el('workflow-step-timeout').value = Number(step?.timeout_seconds || 0);
        el('workflow-step-approval').checked = Boolean(step?.require_approval);
        el('workflow-step-wait').checked = Boolean(step?.wait_for_continue || step?.wait_before_next);
        el('workflow-step-fresh-agent').checked = String(config.agent_context_mode || '').toLowerCase() === 'fresh';
        try {
            await actions.ensureProviderCatalogs();
        } catch (_) {
            /* automatic route remains available */
        }
        el('workflow-step-provider').innerHTML =
            '<option value="">Automatic</option>' +
            context.providers
                .map((provider) => {
                    const id = String(provider.id || provider);
                    return `<option value="${actions.escapeHtml(id)}"${String(config.model_provider || config.provider || '') === id ? ' selected' : ''}>${actions.escapeHtml(provider.name || id)}</option>`;
                })
                .join('');
        syncWorkflowStepModels(config.model || '');
        const targetOptions = workflowStepList(workflow)
            .filter((item) => !step || Number(item.id) !== Number(step.id))
            .map((item) => `<option value="${Number(item.id)}">${actions.escapeHtml(item.name || `Step ${item.position || item.id}`)}</option>`)
            .join('');
        el('workflow-step-on-pass').innerHTML = `<option value="">Next step</option>${targetOptions}`;
        el('workflow-step-on-fail').innerHTML = `<option value="">Stop workflow</option>${targetOptions}`;
        el('workflow-step-on-pass').value = String(step?.on_pass_goto || '');
        el('workflow-step-on-fail').value = String(step?.on_fail_goto || '');
        el('workflow-step-project').innerHTML =
            '<option value="">Workflow project</option>' +
            context.projects
                .map(
                    (project) =>
                        `<option value="${Number(project.id)}"${Number(project.id) === Number(step?.linked_project_id || 0) ? ' selected' : ''}>${actions.escapeHtml(project.name || 'Untitled project')}</option>`
                )
                .join('');
        el('workflow-step-skills').innerHTML = workflowStepOptionHtml(context.skills, config.skills || config.skill_ids || [], 'skill');
        el('workflow-step-tools').innerHTML = workflowStepOptionHtml(
            [
                { id: 'cli', name: 'Project CLI' },
                { id: 'shell', name: 'Shell' },
                { id: 'playwright', name: 'Browser testing' },
                { id: 'http', name: 'HTTP' },
                { id: 'computer', name: 'Computer use' }
            ],
            config.tools || config.ui_tools || [],
            'tool'
        );
        const dialog = el('workflow-step-dialog');
        const advanced = dialog.querySelector('.workflow-step-advanced');
        if (advanced) advanced.open = false;
        dialog.showModal();
        window.setTimeout(() => el('workflow-step-name').focus(), 0);
    }

    async function saveWorkflowStep(event) {
        event.preventDefault();
        const workflowId = Number(el('workflow-step-workflow-id').value || 0);
        let stepId = Number(el('workflow-step-id').value || 0);
        const workflow = context.workflows.find((item) => Number(item.id) === workflowId);
        const step = workflowStepList(workflow).find((item) => Number(item.id) === stepId);
        if (!workflow) return;
        const config = workflowStepConfig(step);
        config.agent_context_mode = el('workflow-step-fresh-agent').checked ? 'fresh' : 'workflow';
        config.model_provider = el('workflow-step-provider').value;
        config.model = el('workflow-step-model').value;
        config.skills = selectedWorkflowOptions('[data-workflow-skill-option]');
        config.tools = selectedWorkflowOptions('[data-workflow-tool-option]');
        config.expected_outputs = el('workflow-step-outcome')
            .value.split(/[\n,]+/)
            .map((item) => item.trim())
            .filter(Boolean);
        const body = {
            name: el('workflow-step-name').value.trim(),
            action_type: el('workflow-step-action').value,
            instruction: el('workflow-step-instruction').value.trim(),
            validation_type: el('workflow-step-validation').value,
            validation_prompt: el('workflow-step-validation-prompt').value.trim(),
            routing_mode: el('workflow-step-routing').value,
            routing_prompt: el('workflow-step-routing-prompt').value.trim(),
            on_pass_goto: Number(el('workflow-step-on-pass').value || 0) || null,
            on_fail_goto: Number(el('workflow-step-on-fail').value || 0) || null,
            max_retries: Number(el('workflow-step-retries').value || 0),
            timeout_seconds: Number(el('workflow-step-timeout').value || 0),
            require_approval: el('workflow-step-approval').checked,
            wait_for_continue: el('workflow-step-wait').checked,
            linked_project_id: Number(el('workflow-step-project').value || 0) || null,
            config
        };
        try {
            if (!stepId) {
                const created = await actions.api(`/workflows/${workflowId}/steps`, {
                    method: 'POST',
                    body: {
                        name: body.name,
                        action_type: body.action_type,
                        instruction: body.instruction,
                        position: workflowStepList(workflow).length,
                        config,
                        validation_type: body.validation_type,
                        validation_prompt: body.validation_prompt,
                        wait_for_continue: body.wait_for_continue
                    }
                });
                const createdSteps = workflowStepList(created);
                stepId = Number(createdSteps[createdSteps.length - 1]?.id || 0);
            }
            if (stepId)
                await actions.api(`/workflows/${workflowId}/steps/${stepId}`, {
                    method: 'PATCH',
                    body
                });
            el('workflow-step-dialog').close('saved');
            actions.toast(step ? 'Workflow step updated.' : 'Workflow step added.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not update the workflow step.', 'error');
        }
    }

    async function duplicateWorkflowStep(workflowId, stepId) {
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        const step = workflowStepList(workflow).find((item) => Number(item.id) === Number(stepId));
        if (!step) return;
        try {
            const created = await actions.api(`/workflows/${workflowId}/steps`, {
                method: 'POST',
                body: {
                    name: `${step.name || 'Step'} copy`,
                    action_type: step.action_type || 'agent_instruction',
                    instruction: step.instruction || '',
                    position: workflowStepList(workflow).length,
                    config: workflowStepConfig(step),
                    validation_type: step.validation_type || 'none',
                    validation_prompt: step.validation_prompt || '',
                    wait_for_continue: Boolean(step.wait_for_continue)
                }
            });
            const newStep = workflowStepList(created).slice(-1)[0];
            if (newStep?.id)
                await actions.api(`/workflows/${workflowId}/steps/${newStep.id}`, {
                    method: 'PATCH',
                    body: {
                        routing_mode: step.routing_mode,
                        routing_prompt: step.routing_prompt,
                        on_pass_goto: step.on_pass_goto,
                        on_fail_goto: step.on_fail_goto,
                        max_retries: step.max_retries,
                        timeout_seconds: step.timeout_seconds,
                        require_approval: step.require_approval,
                        linked_project_id: step.linked_project_id
                    }
                });
            actions.toast('Workflow step duplicated.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not duplicate the workflow step.', 'error');
        }
    }

    async function deleteWorkflowStep(workflowId, stepId) {
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        const step = workflowStepList(workflow).find((item) => Number(item.id) === Number(stepId));
        if (
            !(await actions.confirmAction({
                title: 'Delete workflow step',
                message: `Delete “${step?.name || 'this step'}”?`,
                confirmLabel: 'Delete',
                danger: true
            }))
        )
            return;
        try {
            await actions.api(`/workflows/${workflowId}/steps/${stepId}`, {
                method: 'DELETE'
            });
            actions.toast('Workflow step deleted.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not delete the workflow step.', 'error');
        }
    }

    async function executeWorkflowStep(workflowId, stepId) {
        try {
            await actions.api(`/workflows/${workflowId}/steps/${stepId}/execute`, {
                method: 'POST',
                body: {}
            });
            actions.toast('Step execution started.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not run the step.', 'error');
        }
    }

    async function runWorkflowFromStep(workflowId, stepId) {
        try {
            const result = await actions.api(`/workflows/${workflowId}/run`, {
                method: 'POST',
                body: { start_step_id: stepId }
            });
            actions.toast('Workflow started from this step.');
            await actions.refreshShell({ preserveConversation: true });
            if (result?.chat_id) await actions.loadChat(Number(result.chat_id));
            else await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not start from this step.', 'error');
        }
    }

    async function stopWorkflowStep(workflowId, stepId) {
        try {
            await actions.api(`/workflows/${workflowId}/steps/${stepId}/stop`, {
                method: 'POST',
                body: {}
            });
            actions.toast('Step stopped.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not stop the step.', 'error');
        }
    }

    async function createWorkflowFromPrompt(event) {
        event?.preventDefault();
        const input = el('workflow-prompt-input');
        const instruction = input.value.trim();
        if (!instruction) return;
        el('workflow-prompt-submit').disabled = true;
        try {
            const workflow = await actions.api('/workflows/plan', {
                method: 'POST',
                body: { instruction }
            });
            input.value = '';
            await actions.refreshShell({ preserveConversation: true });
            state.selectedWorkflowId = String(workflow.id);
            await selectWorkflow(workflow.id);
            actions.toast('Workflow planned and ready to review.');
        } catch (error) {
            actions.toast(error.message || 'Could not create the workflow.', 'error');
        } finally {
            el('workflow-prompt-submit').disabled = false;
        }
    }

    async function runManagedWorkflow(workflowId) {
        const issue = definitionIssue(context.workflows.find((row) => Number(row.id) === Number(workflowId)));
        if (issue) {
            actions.toast(issue, 'error');
            return;
        }
        const key = String(workflowId);
        if (state.workflowBusy[key]) return;
        state.workflowBusy[key] = 'start';
        renderWorkflowWorkspace();
        try {
            const result = await actions.api(`/workflows/${workflowId}/run`, {
                method: 'POST',
                body: {}
            });
            actions.toast('Workflow started.');
            await reconcileManagedWorkflow(workflowId);
            if (result?.chat_id) await actions.loadChat(Number(result.chat_id));
        } catch (error) {
            actions.toast(error.message || 'Could not start the workflow.', 'error');
        } finally {
            delete state.workflowBusy[key];
            renderWorkflowWorkspace();
        }
    }

    async function reviseWorkflowFromPrompt(event, workflowId) {
        event.preventDefault();
        const form = event.currentTarget;
        const input = form.querySelector('#workflow-change-instruction');
        const button = form.querySelector('button[type="submit"]');
        const change = input.value.trim();
        if (!change) {
            input.focus();
            return;
        }
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        const currentSteps = workflowStepList(workflow)
            .map((step, index) => `${index + 1}. ${step.name}: ${step.instruction || ''}`)
            .join('\n');
        const instruction = `Revise the existing workflow "${workflow?.name || 'Workflow'}". Preserve useful steps that are not contradicted. Requested change: ${change}\n\nCurrent steps:\n${currentSteps}\n\nReturn the complete ordered workflow, including validation and failure recovery where relevant.`;
        button.disabled = true;
        button.textContent = 'Updating...';
        try {
            await actions.api(`/workflows/${workflowId}/generate-steps`, {
                method: 'POST',
                body: { instruction }
            });
            input.value = '';
            await selectWorkflow(workflowId);
            actions.toast('Workflow loop updated. Review the steps before running it.');
        } catch (error) {
            actions.toast(error.message || 'Could not update the workflow loop.', 'error');
        } finally {
            button.disabled = false;
            button.textContent = 'Update workflow';
        }
    }

    async function regenerateWorkflow(workflowId) {
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        try {
            await actions.api(`/workflows/${workflowId}/generate-steps`, {
                method: 'POST',
                body: {
                    instruction: workflow?.description || workflow?.name || 'Generate a robust development workflow'
                }
            });
            await selectWorkflow(workflowId);
            actions.toast('Workflow steps regenerated.');
        } catch (error) {
            actions.toast(error.message || 'Could not regenerate the steps.', 'error');
        }
    }

    async function deleteManagedWorkflow(workflowId) {
        const workflow = context.workflows.find((item) => Number(item.id) === Number(workflowId));
        if (
            !(await actions.confirmAction({
                title: 'Delete workflow',
                message: `Delete “${workflow?.name || 'this workflow'}” and its run history?`,
                confirmLabel: 'Delete',
                danger: true
            }))
        )
            return;
        try {
            await actions.api(`/workflows/${workflowId}`, { method: 'DELETE' });
            state.selectedWorkflowId = '';
            await actions.refreshShell({ preserveConversation: true });
            renderWorkflowWorkspace();
            actions.toast('Workflow deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the workflow.', 'error');
        }
    }

    async function stopManagedWorkflow(workflowId, run) {
        const key = String(workflowId);
        if (state.workflowBusy[key]) return;
        if (!run) {
            await reconcileManagedWorkflow(workflowId);
            return;
        }
        state.workflowBusy[key] = 'stop';
        renderWorkflowWorkspace();
        try {
            const runId = Number(run.run_id || run.id || 0);
            if (!runId) throw new Error('The active workflow run could not be identified.');
            await actions.api(`/workflows/${workflowId}/cancel-run/${runId}`, {
                method: 'POST',
                body: {}
            });
            actions.toast('Workflow stopped.');
            await reconcileManagedWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not stop the workflow.', 'error');
        } finally {
            delete state.workflowBusy[key];
            renderWorkflowWorkspace();
        }
    }

    async function reconcileManagedWorkflow(workflowId) {
        const [detail, runs, activeRuns] = await Promise.all([
            actions.api(`/workflows/${workflowId}`),
            actions.api(`/workflows/${workflowId}/runs?limit=10`).catch(() => []),
            actions.api(`/workflows/active-runs?limit=100`).catch(() => [])
        ]);
        const index = context.workflows.findIndex((item) => Number(item.id) === Number(workflowId));
        if (index >= 0) context.workflows[index] = { ...context.workflows[index], ...detail };
        state.workflowRuns[String(workflowId)] = Array.isArray(runs) ? runs : runs.runs || [];
        const activeRows = Array.isArray(activeRuns) ? activeRuns : activeRuns.runs || [];
        context.runs = context.runs
            .filter((item) => Number(item.workflow_id) !== Number(workflowId))
            .concat(activeRows.filter((item) => Number(item.workflow_id) === Number(workflowId)).map((item) => ({ ...item, __clientReceivedAt: Date.now() })));
    }

    function bindEvents() {
        el('workflow-create-focus').addEventListener('click', () => el('workflow-prompt-input').focus());
        el('workflow-prompt-form').addEventListener('submit', createWorkflowFromPrompt);
        el('workflow-search-input').addEventListener('input', (event) => {
            state.workflowSearch = event.target.value;
            renderWorkflowWorkspace();
        });
        el('workflow-step-form').addEventListener('submit', saveWorkflowStep);
        el('workflow-step-provider').addEventListener('change', () => syncWorkflowStepModels(''));
    }
    return {
        bindEvents,
        renderWorkflowWorkspace,
        selectWorkflow,
        workflowProjectId,
        workflowStepList
    };
}
