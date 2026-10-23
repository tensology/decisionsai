import { bindWorkflowStepNumbers, workflowStepDataAttrs, readWorkflowViewMode, workflowLoopHtml as sharedWorkflowLoopHtml, workflowStepNumbersHtml, workflowViewSwitchHtml, writeWorkflowViewMode } from '../shared/workflow-loop.js?v=20261022-threadring2';
// Owns workflows rendering, interactions, and private view state.
export function createWorkflows({ context, actions, el }) {
    const state = {
        selectedWorkflowId: '',
        workflowViewMode: readWorkflowViewMode(),
        workflowRuns: {},
        workflowBusy: {},
        workflowDetailsLoading: new Set(),
        workflowMemory: {},
        inlineStepId: 0,
        selectedStepIndex: -1,
        drawerAnimate: false,
        settingsOpen: false,
        workflowDetailSteps: {}
    };
    function filteredWorkflows() {
        return context.workflows.slice();
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

    function workflowCardHtml(workflow) {
        const { run, status, active } = workflowRunState(workflow);
        const busy = Boolean(state.workflowBusy[String(workflow.id)]);
        const steps = workflowStepList(workflow);
        const stepCount = Number(workflow.step_count || workflow.steps_count || steps.length || 0);
        const action = active ? 'stop' : 'start';
        const issue = definitionIssue(workflow);
        const currentStepIndex = (() => {
            if (!steps.length || !run) return 0;
            const sid = run.current_step_id ?? run.current_step?.id ?? run.step_id;
            let idx = sid != null ? steps.findIndex((step) => String(step.id) === String(sid)) : -1;
            if (idx < 0 && run.current_step_name) idx = steps.findIndex((step) => String(step.name || '') === String(run.current_step_name));
            if (idx < 0 && Number.isInteger(run.current_step_index)) idx = run.current_step_index;
            return idx >= 0 && idx < steps.length ? idx : 0;
        })();
        const stepRow = (label, index) =>
            `<li class="workflow-card-step-${label.toLowerCase()}"><span class="workflow-card-index">${index + 1}</span><span class="workflow-card-step"><small>${label}</small><strong>${actions.escapeHtml(steps[index].name || `Step ${index + 1}`)}</strong></span></li>`;
        const preview = steps.length
            ? `<ol class="workflow-card-steps workflow-card-current-next">${stepRow('Current', currentStepIndex)}${
                  currentStepIndex + 1 < steps.length
                      ? stepRow('Next', currentStepIndex + 1)
                      : '<li class="workflow-card-step-next workflow-card-last"><span class="workflow-card-index">✓</span><span class="workflow-card-step"><small>Next</small><strong>Last step</strong></span></li>'
              }</ol>`
            : stepCount
              ? '<p class="workflow-card-pending">Loading steps</p>'
              : '<p class="workflow-card-pending">No steps yet</p>';
        const summary = `${stepCount} step${stepCount === 1 ? '' : 's'} · ${run ? actions.statusLabel(status) : issue || 'Ready'}`;
        const stateClass = status === 'waiting' || status === 'paused' ? 'waiting' : status === 'failed' ? 'failed' : active ? 'running' : '';
        return `<article class="workflow-card" data-workflow-card="${Number(workflow.id)}">
            <header class="workflow-card-head">
                <button type="button" class="workflow-card-open" data-workflow-select="${Number(workflow.id)}"><strong>${actions.escapeHtml(workflow.name || 'Untitled workflow')}</strong><small>${actions.escapeHtml(summary)}</small></button>
                <span class="workflow-card-controls">${stateClass ? `<i class="workflow-state ${stateClass}" role="img" aria-label="${actions.escapeHtml(actions.statusLabel(status))}" title="${actions.escapeHtml(actions.statusLabel(status))}"></i>` : ''}<span class="scheduled-row-icons"><button type="button" class="scheduled-icon-button edit" data-workflow-card-action="open" data-workflow-id="${Number(workflow.id)}" title="Edit" aria-label="Edit ${actions.escapeHtml(workflow.name || 'workflow')}"><svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m4 14-.5 3 3-.5L15 8l-2.5-2.5L4 14Z"/><path d="m11.5 6.5 2.5 2.5"/></svg></button><button type="button" class="scheduled-icon-button delete danger" data-workflow-card-action="delete" data-workflow-id="${Number(workflow.id)}" title="Delete" aria-label="Delete ${actions.escapeHtml(workflow.name || 'workflow')}"><svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 6h12M8 6V4h4v2M6 6l.8 10h6.4L14 6"/></svg></button></span></span>
            </header>
            ${preview ? `<button type="button" class="workflow-card-body" data-workflow-select="${Number(workflow.id)}">${preview}</button>` : ''}
        </article>`;
    }

    function workflowGroupHtml(project, workflows, groupKind) {
        const name = groupKind === 'imported' ? 'Imported workflows' : groupKind === 'test' ? 'Test workflows' : project?.name || 'Reusable workflows';
        const help =
            groupKind === 'project'
                ? `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} linked to ${project?.name || 'this project'} and available to its ticket threads`
                : groupKind === 'imported'
                  ? `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} from messages`
                  : groupKind === 'test'
                    ? `${workflows.length} test workflow${workflows.length === 1 ? '' : 's'}`
                    : `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} you can run again`;
        return `<section class="workflow-project-group workflow-group-${actions.escapeHtml(groupKind)}" data-workflow-project="${Number(project?.id || 0)}"><header><div><span class="workflow-project-mark" aria-hidden="true">${project ? '◆' : groupKind === 'test' ? 'T' : groupKind === 'imported' ? 'I' : '◇'}</span><div><h2>${actions.escapeHtml(name)}</h2><p>${actions.escapeHtml(help)}</p></div></div></header><div class="workflow-card-grid">${sortedWorkflowLibraryRows(
            workflows
        )
            .map((workflow) => workflowCardHtml(workflow))
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
                <details class="workflow-run-menu"><summary aria-label="What you can do with this run">•••</summary><div>
                    ${run.open_url ? `<a href="${actions.escapeHtml(run.open_url)}">Open</a>` : ''}
                    ${run.related_ticket_url ? `<a href="${actions.escapeHtml(run.related_ticket_url)}">Related ticket</a>` : ''}
                    ${status === 'waiting' && run.open_url ? `<a class="waiting" href="${actions.escapeHtml(run.open_url)}">Continue / Respond</a>` : ''}
                    ${cancelUrl ? `<button type="button" data-active-execution-cancel="${actions.escapeHtml(cancelUrl)}">Cancel</button>` : ''}
                </div></details>
            </article>`;
            })
            .join('');
        return `<section class="workflow-project-group workflow-active-executions"><header><div><span class="workflow-project-mark" aria-hidden="true">●</span><div><h2>Running now</h2><p>${runs.length} running</p></div></div></header><div class="workflow-active-execution-list">${rows}</div></section>`;
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
        const activeExecutions = activeExecutionRowsHtml();
        const groupsHtml = workflows.length
            ? `<div class="workflow-card-grid workflow-card-grid-flat">${sortedWorkflowLibraryRows(workflows)
                  .map((workflow) => workflowCardHtml(workflow))
                  .join('')}</div>`
            : '<div class="development-empty">No workflows yet.</div>';
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
        list.querySelectorAll('[data-workflow-card-action]').forEach((button) =>
            button.addEventListener('click', () => {
                const workflowId = Number(button.dataset.workflowId);
                if (button.dataset.workflowCardAction === 'open') selectWorkflow(workflowId);
                else if (button.dataset.workflowCardAction === 'delete') deleteManagedWorkflow(workflowId);
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
        if (String(workflowId || '') !== state.selectedWorkflowId) {
            state.selectedStepIndex = -1;
            state.inlineStepId = 0;
        }
        state.selectedWorkflowId = String(workflowId || '');
        try {
            const detail = await actions.api(`/workflows/${Number(workflowId)}`);
            const index = context.workflows.findIndex((item) => Number(item.id) === Number(workflowId));
            if (index >= 0) context.workflows[index] = detail;
            if (detail && Array.isArray(detail.steps)) state.workflowDetailSteps[String(workflowId)] = detail.steps;
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

    function workflowSkillLabel(skillId) {
        const skill = (context.skills || []).find((item) => String(item.id || item.slug || item.name) === String(skillId));
        return String(skill?.name || skill?.label || skillId || 'Skill');
    }

    function workflowSkillIcon() {
        return '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.25 2.5h3.5v2.25H12v3.5H9.75v2.25h-3.5V8.25H4v-3.5h2.25z"></path><path d="M6.25 4.75h3.5v3.5h-3.5z"></path></svg>';
    }

    function workflowEditorIcon(name) {
        const paths = {
            up: '<path d="M5 12 10 7l5 5"></path><path d="M10 7v8"></path>',
            down: '<path d="m5 8 5 5 5-5"></path><path d="M10 13V5"></path>',
            edit: '<path d="m4 14-.5 3 3-.5L15 8l-2.5-2.5L4 14Z"></path><path d="m11.5 6.5 2.5 2.5"></path>',
            duplicate: '<rect x="7" y="7" width="9" height="9" rx="1.5"></rect><path d="M13 7V4H4v9h3"></path>',
            continue: '<path d="M4 6v5h9"></path><path d="m10 8 3 3-3 3"></path>',
            delete: '<path d="m6 6 8 8M14 6l-8 8"></path>',
            settings: '<circle cx="10" cy="10" r="2.6"></circle><path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6"></path>'
        };
        return `<svg aria-hidden="true" viewBox="0 0 20 20">${paths[name] || ''}</svg>`;
    }

    function workflowLoopHtml(steps, active, issue = '') {
        const count = steps.length;
        return sharedWorkflowLoopHtml(steps, {
            idPrefix: `workflow-loop-${Number(state.selectedWorkflowId || 0)}`,
            escapeHtml: actions.escapeHtml,
            statusLabel: actions.statusLabel,
            mode: 'design',
            selectedStepId: steps[selectedStepIndex(steps)]?.id,
            coreLabel: !count ? 'Add a step' : selectedStepIndex(steps) >= 0 ? `Step ${selectedStepIndex(steps) + 1}` : 'Steps',
            coreCaption: selectedStepIndex(steps) >= 0 ? 'selected' : 'select a step',
            emptyHtml: '<div class="workflow-empty-steps">No steps yet. Add one manually or describe the loop you want above.</div>',
            buttonAttrs: (step, index) => `${workflowStepDataAttrs(step, index, { escapeHtml: actions.escapeHtml, selected: index === selectedStepIndex(steps) })} data-workflow-step-id="${Number(step.id)}" aria-label="Edit step ${index + 1}: ${actions.escapeHtml(step.name || `Step ${index + 1}`)}"`,
            nodeExtraHtml: (step) => {
                const description = String(step.description || step.instruction || '').trim();
                const icons = workflowStepSkills(step)
                    .map((skill) => {
                        const label = workflowSkillLabel(skill);
                        return `<span class="workflow-loop-skill" data-wf-tip="${actions.escapeHtml(label)}" aria-label="${actions.escapeHtml(label)}">${workflowSkillIcon()}</span>`;
                    })
                    .join('');
                return `${description ? `<small class="workflow-loop-desc">${actions.escapeHtml(description)}</small>` : ''}${icons ? `<span class="workflow-loop-skills">${icons}</span>` : ''}`;
            }
        });
    }

    function selectedStepIndex(steps) {
        const index = Number(state.selectedStepIndex ?? -1);
        return index >= 0 && index < steps.length ? index : -1;
    }

    // Slide-out step panel: one form. The loop card already shows the description.
    function workflowStepDrawerHtml(workflow, steps) {
        if (!state.inlineStepId || !steps.some((item) => Number(item.id) === Number(state.inlineStepId))) return '';
        state.selectedStepIndex = steps.findIndex((item) => Number(item.id) === Number(state.inlineStepId));
        const entering = state.drawerAnimate ? ' entering' : '';
        state.drawerAnimate = false;
        return `<aside class="workflow-step-drawer${entering}" aria-label="Edit step"><header><span>Step ${selectedStepIndex(steps) + 1}</span><button type="button" class="workflow-icon-action" data-workflow-drawer-close aria-label="Close step panel" data-wf-tip="Close panel">${workflowEditorIcon('delete')}</button></header>${workflowInlineStepEditorHtml(workflow, steps)}</aside>`;
    }

    function workflowStepNumbersSectionHtml(workflow, steps) {
        if (!steps.length) return '';
        const html = workflowStepNumbersHtml(steps, {
            selectedStepId: steps[selectedStepIndex(steps)]?.id,
            mode: 'design',
            panel: false,
            idPrefix: `workflow-steps-${Number(workflow.id || 0)}`,
            escapeHtml: actions.escapeHtml,
            routeFor: (step) => {
                const config = workflowStepConfig(step);
                return String(config.model || config.route || step.model || '');
            },
            outputFor: (step) => String(step.last_output || step.output || step.result || ''),
            ariaLabel: `${workflow.name || 'Workflow'} steps`
        });
        return `<div class="workflow-step-numbers-host">${html}</div>`;
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
                return `<article class="workflow-editor-step" data-workflow-step="${Number(step.id)}" data-workflow-step-row="${Number(step.id)}"><span class="workflow-step-number">${String(index + 1).padStart(2, '0')}</span><div class="workflow-step-copy"><strong>${actions.escapeHtml(step.name || `Step ${index + 1}`)}</strong><p class="workflow-step-desc">${actions.escapeHtml(step.description || step.instruction || '')}</p><div class="workflow-step-support">${outcome ? `<span><b>Outcome</b>${actions.escapeHtml(outcome)}</span>` : ''}${skills.length ? `<span><b>Skills</b>${actions.escapeHtml(skills.join(', '))}</span>` : ''}</div>${routing ? `<small>${actions.escapeHtml(routing)}</small>` : ''}</div><div class="workflow-step-tools"><button type="button" class="workflow-icon-action" data-workflow-step-move="${Number(step.id)}" data-direction="up" aria-label="Move step up" data-wf-tip="Move step up"${index === 0 ? ' disabled' : ''}>${workflowEditorIcon('up')}</button><button type="button" class="workflow-icon-action" data-workflow-step-move="${Number(step.id)}" data-direction="down" aria-label="Move step down" data-wf-tip="Move step down"${index === steps.length - 1 ? ' disabled' : ''}>${workflowEditorIcon('down')}</button><button type="button" class="workflow-icon-action" data-workflow-step-edit="${Number(step.id)}" aria-label="Edit step" data-wf-tip="Edit step">${workflowEditorIcon('edit')}</button><button type="button" class="workflow-icon-action" data-workflow-step-duplicate="${Number(step.id)}" aria-label="Duplicate step" data-wf-tip="Duplicate step">${workflowEditorIcon('duplicate')}</button><button type="button" class="workflow-icon-action danger" data-workflow-step-delete="${Number(step.id)}" aria-label="Delete step" data-wf-tip="Delete step">${workflowEditorIcon('delete')}</button></div></article>`;
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
        // Catalog refreshes can replace detailed steps with summary steps; fill them back from the last detail load.
        const detailSteps = new Map((state.workflowDetailSteps[String(workflow.id)] || []).map((item) => [Number(item.id), item]));
        const steps = workflowStepList(workflow).map((item) => ({ ...item, ...(detailSteps.get(Number(item.id)) || {}) }));
        const { run, status, active } = workflowRunState(workflow);
        const busy = Boolean(state.workflowBusy[String(workflow.id)]);
        const metadata = workflowInputObject(workflow);
        const projectId = workflowProjectId(workflow);
        const memory = state.workflowMemory[String(workflow.id)] || null;
        const viewMode = state.workflowViewMode === 'list' ? 'list' : 'loop';
        detail.innerHTML = `<header class="workflow-editor-header"><nav><button type="button" class="workflow-editor-back" aria-label="Back to workflows"><svg aria-hidden="true" viewBox="0 0 20 20"><path d="M12.5 4.5 7 10l5.5 5.5"></path></svg><span>Workflows</span></button><i>/</i><strong>${actions.escapeHtml(workflow.name || 'Workflow')}</strong></nav><div class="workflow-editor-actions"><button type="button" class="workflow-icon-action" data-workflow-duplicate aria-label="Duplicate workflow" data-wf-tip="Duplicate workflow">${workflowEditorIcon('duplicate')}</button><button type="button" class="workflow-icon-action" data-workflow-settings-open aria-label="Workflow settings" data-wf-tip="Workflow settings">${workflowEditorIcon('edit')}</button></div></header>
            <div class="workflow-editor-scroll"><div class="workflow-builder-layout"><section class="workflow-step-section"><div class="workflow-canvas-meta"><div class="workflow-toolbar-left"><strong class="workflow-toolbar-count">${steps.length} step${steps.length === 1 ? '' : 's'}</strong>${workflowViewSwitchHtml(viewMode)}</div>${viewMode === 'loop' ? workflowStepNumbersSectionHtml(workflow, steps) : ''}<div class="workflow-step-header-actions"><button type="button" class="primary-button workflow-add-step-bar" data-workflow-add-step>Add step</button></div></div><div class="workflow-step-stage" data-view="${viewMode}">${viewMode === 'loop' ? workflowLoopHtml(steps, active, definitionIssue(workflow)) : `<div class="workflow-step-stack">${workflowStepListHtml(steps)}</div>`}</div>${workflowStepDrawerHtml(workflow, steps)}</section></div></div><dialog class="studio-dialog workflow-settings-modal" data-workflow-settings-modal aria-label="Workflow settings"><header class="workflow-settings-modal-header"><strong>Workflow settings</strong><button type="button" class="workflow-icon-action" data-workflow-settings-close aria-label="Close workflow settings" data-wf-tip="Close">${workflowEditorIcon('delete')}</button></header><details class="workflow-advanced-section"><summary><span><strong>Workflow settings</strong><small>Name, objective, and run configuration</small></span><svg aria-hidden="true" viewBox="0 0 20 20"><path d="m6 8 4 4 4-4"></path></svg></summary><form class="workflow-settings-form"><div class="workflow-settings-grid"><label>Name<input data-workflow-name value="${actions.escapeHtml(workflow.name || '')}" required></label><label>Project<select data-workflow-project><option value="">Shared workflow</option>${context.projects.map((project) => `<option value="${Number(project.id)}"${Number(project.id) === projectId ? ' selected' : ''}>${actions.escapeHtml(project.name || 'Untitled project')}</option>`).join('')}</select></label></div><label>Description<textarea data-workflow-description rows="3">${actions.escapeHtml(workflow.description || '')}</textarea></label><label>Workflow objective and input<textarea data-workflow-objective rows="4">${actions.escapeHtml(metadata.objective || metadata.instruction || (typeof workflow.workflow_input === 'string' && !Object.keys(metadata).length ? workflow.workflow_input : ''))}</textarea></label><label>Context and memory rules<textarea data-workflow-context rows="4" placeholder="What should every step remember, preserve, and learn?">${actions.escapeHtml(typeof workflow.context_rules === 'string' ? workflow.context_rules : JSON.stringify(workflow.context_rules || '', null, 2).replace(/^"|"$/g, ''))}</textarea></label><details class="workflow-technical-settings"><summary>Advanced run settings</summary><label>Run settings<textarea data-workflow-run-settings rows="6" placeholder='{"max_parallel": 1}'>${actions.escapeHtml(JSON.stringify(workflow.run_settings || {}, null, 2))}</textarea></label></details><div class="workflow-settings-actions"><button type="submit" class="primary-button">Save settings</button><button type="button" class="danger-text-button" data-workflow-delete>Delete workflow</button></div></form></details>
            <details class="workflow-advanced-section workflow-memory-section"><summary><span><strong>Memory</strong><small>Handoffs and learned context</small></span><svg aria-hidden="true" viewBox="0 0 20 20"><path d="m6 8 4 4 4-4"></path></svg></summary><div class="workflow-memory-panel"><button type="button" class="secondary-button" data-workflow-load-memory>${memory ? 'Refresh memory' : 'Load memory'}</button><div class="workflow-memory-content">${memory ? workflowMemoryHtml(memory) : '<p>Load the workflow memory to inspect handoffs, routing, and learned context.</p>'}</div></div></details></dialog>`;
        detail.querySelector('.workflow-editor-back').addEventListener('click', () => {
            state.selectedWorkflowId = '';
            renderWorkflowWorkspace();
        });
        detail.querySelector('[data-workflow-duplicate]').addEventListener('click', () => duplicateManagedWorkflow(Number(workflow.id)));
        detail.querySelectorAll('[data-workflow-view]').forEach((button) =>
            button.addEventListener('click', () => {
                state.workflowViewMode = writeWorkflowViewMode(button.dataset.workflowView);
                renderWorkflowDetail();
            })
        );
        const stepSection = detail.querySelector('.workflow-step-section');
        // Clicking a step (ring node or centre number) selects it and opens the slide-out panel.
        stepSection?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-wf-step]');
            if (!button) return;
            event.stopPropagation();
            openInlineStepEditor(Number(steps[Number(button.dataset.stepIndex)]?.id));
        }, true);
        bindWorkflowStepNumbers(stepSection, { escapeHtml: actions.escapeHtml });
        const settingsModal = detail.querySelector('[data-workflow-settings-modal]');
        detail.querySelector('[data-workflow-settings-open]').addEventListener('click', () => {
            state.settingsOpen = true;
            settingsModal.showModal();
        });
        settingsModal.querySelector('[data-workflow-settings-close]').addEventListener('click', () => settingsModal.close());
        settingsModal.addEventListener('close', () => (state.settingsOpen = false));
        if (state.settingsOpen && !settingsModal.open) settingsModal.showModal();
        detail.querySelector('.workflow-settings-form').addEventListener('submit', (event) => saveWorkflowSettings(event, Number(workflow.id)));
        detail.querySelector('[data-workflow-delete]').addEventListener('click', () => deleteManagedWorkflow(Number(workflow.id)));
        detail.querySelector('[data-workflow-add-step]').addEventListener('click', () => addInlineWorkflowStep(Number(workflow.id)));
        bindInlineStepEditor(detail, Number(workflow.id));
        detail.querySelector('[data-workflow-load-memory]').addEventListener('click', () => loadWorkflowMemory(Number(workflow.id)));
        detail
            .querySelectorAll('[data-workflow-step-edit]')
            .forEach((button) => button.addEventListener('click', () => openInlineStepEditor(Number(button.dataset.workflowStepEdit))));
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
        detail.querySelectorAll('[data-workflow-step-row]').forEach((row) =>
            row.addEventListener('click', (event) => {
                if (event.target.closest('button')) return;
                openInlineStepEditor(Number(row.dataset.workflowStepRow));
            })
        );
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
            setWorkflowComposerOpen(false);
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

    function setWorkflowComposerOpen(open) {
        const modal = el('workflow-add-modal');
        if (!modal) return;
        modal.classList.toggle('hidden', !open);
        el('workflow-add-toggle')?.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) el('workflow-prompt-input')?.focus();
    }

    // The composer lives inside the workflow home panel, so drop any open workflow detail first.
    function openWorkflowComposer() {
        if (state.selectedWorkflowId) {
            state.selectedWorkflowId = '';
            state.selectedStepIndex = -1;
            state.inlineStepId = 0;
            renderWorkflowWorkspace();
        }
        setWorkflowComposerOpen(true);
    }

    // Inline step editor (instruction, skills, outcome) saved through PATCH /workflows/:id/steps/:stepId.
    function workflowInlineStepEditorHtml(workflow, steps) {
        const stepId = Number(state.inlineStepId || 0);
        const index = steps.findIndex((item) => Number(item.id) === stepId);
        if (index < 0) return '';
        const step = steps[index];
        const esc = actions.escapeHtml;
        const skillOptions = workflowStepOptionHtml(context.skills, workflowStepSkills(step), 'inline-skill');
        return `<form class="workflow-inline-step-editor" data-workflow-inline-step="${stepId}"><label>Name<input data-inline-name value="${esc(step.name || '')}" required></label><label>Description<textarea data-inline-description rows="3">${esc(step.description || '')}</textarea></label><label>Instruction<textarea data-inline-instruction rows="4">${esc(step.instruction || '')}</textarea></label><label>Skills<div class="workflow-option-grid">${skillOptions || '<p class="workflow-inline-empty">No skills installed.</p>'}</div></label><label>Outcome<textarea data-inline-outcome rows="2">${esc(workflowStepOutcome(step))}</textarea></label><div class="workflow-inline-step-actions"><button type="submit" class="primary-button">Save step</button></div></form>`;
    }

    function openInlineStepEditor(stepId) {
        const workflow = context.workflows.find((item) => String(item.id) === String(state.selectedWorkflowId));
        const index = workflowStepList(workflow).findIndex((item) => Number(item.id) === Number(stepId));
        if (index >= 0) state.selectedStepIndex = index;
        state.drawerAnimate = !state.inlineStepId && Boolean(stepId);
        state.inlineStepId = Number(stepId) || 0;
        renderWorkflowDetail();
        if (state.inlineStepId) refreshWorkflowDetailForEditor(workflow?.id);
    }

    // The list payload can carry summary steps; load the full workflow (GET /workflows/:id) so the editor shows real values.
    async function refreshWorkflowDetailForEditor(workflowId) {
        if (!workflowId) return null;
        try {
            const fresh = await actions.api(`/workflows/${Number(workflowId)}`);
            const index = context.workflows.findIndex((item) => Number(item.id) === Number(workflowId));
            if (index >= 0 && fresh && Array.isArray(fresh.steps)) context.workflows[index] = fresh;
            if (fresh && Array.isArray(fresh.steps)) state.workflowDetailSteps[String(workflowId)] = fresh.steps;
            if (state.inlineStepId && !el('workflow-detail')?.querySelector('.workflow-inline-step-editor :focus')) renderWorkflowDetail();
            return fresh;
        } catch (_) {
            return null;
        }
    }

    function closeStepDrawer() {
        const drawer = el('workflow-detail')?.querySelector('.workflow-step-drawer');
        if (!drawer) return openInlineStepEditor(0);
        drawer.classList.add('leaving');
        drawer.addEventListener('animationend', () => openInlineStepEditor(0), { once: true });
    }

    function bindInlineStepEditor(detail, workflowId) {
        const form = detail.querySelector('[data-workflow-inline-step]');
        if (!form) return;
        detail.querySelector('[data-workflow-drawer-close]')?.addEventListener('click', closeStepDrawer);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const stepId = Number(form.dataset.workflowInlineStep);
            // Merge into the freshly loaded step so unrelated config keys are never dropped.
            await refreshWorkflowDetailForEditor(workflowId);
            const step = (state.workflowDetailSteps[String(workflowId)] || []).find((item) => Number(item.id) === stepId);
            if (!step) return actions.toast('Could not load this step; nothing was saved.', 'error');
            const split = (value) => value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
            const config = workflowStepConfig(step);
            config.skills = Array.from(form.querySelectorAll('[data-workflow-inline-skill-option]:checked')).map((input) => input.value);
            config.expected_outputs = split(form.querySelector('[data-inline-outcome]').value);
            try {
                await actions.api(`/workflows/${workflowId}/steps/${stepId}`, {
                    method: 'PATCH',
                    body: {
                        name: form.querySelector('[data-inline-name]').value.trim(),
                        description: form.querySelector('[data-inline-description]').value.trim(),
                        instruction: form.querySelector('[data-inline-instruction]').value.trim(),
                        config
                    }
                });
                state.inlineStepId = 0;
                actions.toast('Workflow step updated.');
                await selectWorkflow(workflowId);
            } catch (error) {
                actions.toast(error.message || 'Could not update the workflow step.', 'error');
            }
        });
    }

    // Add step: POST /workflows/:id/steps, then open the new step in the inline editor.
    async function addInlineWorkflowStep(workflowId) {
        const workflow = context.workflows.find((item) => Number(item.id) === workflowId);
        if (!workflow) return;
        const position = workflowStepList(workflow).length;
        try {
            const created = await actions.api(`/workflows/${workflowId}/steps`, {
                method: 'POST',
                body: { name: `Step ${position + 1}`, action_type: 'agent_instruction', instruction: '', position, config: {} }
            });
            const createdSteps = workflowStepList(created);
            state.inlineStepId = Number(createdSteps[createdSteps.length - 1]?.id || 0);
            actions.toast('Workflow step added.');
            await selectWorkflow(workflowId);
        } catch (error) {
            actions.toast(error.message || 'Could not add the workflow step.', 'error');
        }
    }

    // One fixed-position tooltip for the workflows area: native titles move to data-wf-tip so only one tip shows.
    function installWorkflowTooltips() {
        if (document.__wfTips) return;
        document.__wfTips = true;
        let tip = null;
        let owner = null;
        const hide = () => {
            tip?.remove();
            tip = null;
            owner = null;
        };
        const inScope = (node) => node.closest('#workflow-home, #workflow-list, #workflow-detail');
        document.addEventListener('pointerover', (event) => {
            const target = event.target instanceof Element ? event.target.closest('[title], [data-wf-tip]') : null;
            if (!target || !inScope(target) || target === owner) return;
            if (target.hasAttribute('title')) {
                if (!target.hasAttribute('data-wf-tip')) target.setAttribute('data-wf-tip', target.getAttribute('title'));
                target.removeAttribute('title');
            }
            hide();
            const text = target.getAttribute('data-wf-tip') || '';
            if (!text) return;
            owner = target;
            tip = document.createElement('div');
            tip.className = 'workflow-tooltip-portal';
            tip.setAttribute('role', 'tooltip');
            tip.textContent = text;
            document.body.appendChild(tip);
            const rect = target.getBoundingClientRect();
            const box = tip.getBoundingClientRect();
            const margin = 8;
            let left = rect.left + rect.width / 2 - box.width / 2;
            let top = rect.top - box.height - 6;
            left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
            if (top < margin) top = rect.bottom + 6;
            top = Math.min(top, window.innerHeight - box.height - margin);
            tip.style.left = `${Math.round(left)}px`;
            tip.style.top = `${Math.round(top)}px`;
        });
        document.addEventListener('pointerout', (event) => {
            if (!owner) return;
            if (event.relatedTarget instanceof Node && owner.contains(event.relatedTarget)) return;
            hide();
        });
        ['pointerdown', 'scroll', 'keydown'].forEach((name) => document.addEventListener(name, hide, true));
        window.addEventListener('blur', hide);
    }

    function bindEvents() {
        installWorkflowTooltips();
        el('workflow-prompt-form').addEventListener('submit', createWorkflowFromPrompt);
        el('workflow-add-toggle')?.addEventListener('click', () => setWorkflowComposerOpen(true));
        el('workflow-add-modal')?.addEventListener('click', (event) => {
            if (event.target === event.currentTarget) setWorkflowComposerOpen(false);
        });
        el('workflow-prompt-cancel')?.addEventListener('click', () => setWorkflowComposerOpen(false));
        el('workflow-prompt-input')?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') setWorkflowComposerOpen(false);
        });
        el('workflow-step-form').addEventListener('submit', saveWorkflowStep);
        el('workflow-step-provider').addEventListener('change', () => syncWorkflowStepModels(''));
    }
    return {
        bindEvents,
        openWorkflowComposer,
        renderWorkflowWorkspace,
        selectWorkflow,
        workflowProjectId,
        workflowStepList
    };
}
