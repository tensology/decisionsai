// Owns automations rendering, interactions, and private view state.
export function createAutomations({ context, actions, el }) {
    const state = {
        selectedAutomationId: '',
        contextAutomationId: '',
        automationFilter: 'all',
        automationSearch: '',
        scheduledView: 'list',
        scheduledCalendarMode: 'month',
        scheduledCalendarAnchor: new Date(),
        automationRuns: {},
        automationDrafting: false,
        automationImportSources: [],
        automationImportDefaults: {}
    };
    function automationScheduleLabel(schedule) {
        const value = schedule || {};
        const time = value.time || '09:00';
        if (value.kind === 'interval') return `Every ${value.interval || 30} ${value.interval_unit || 'minutes'}`;
        if (value.kind === 'hourly') return 'Every hour';
        if (value.kind === 'once') return value.run_at ? `Once, ${actions.formatDateTime(value.run_at)}` : 'Once';
        if (value.kind === 'weekly' && String(value.days || '') === '1,2,3,4,5') return `Weekdays at ${time}`;
        if (value.kind === 'weekly') return `Weekly at ${time}`;
        if (value.kind === 'monthly') return `Monthly at ${time}`;
        return `Daily at ${time}`;
    }

    function calendarStartOfDay(value) {
        const date = new Date(value);
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    }

    function calendarStartOfWeek(value) {
        const date = calendarStartOfDay(value);
        const offset = (date.getDay() + 6) % 7;
        date.setDate(date.getDate() - offset);
        return date;
    }

    function calendarAddDays(value, amount) {
        const date = calendarStartOfDay(value);
        date.setDate(date.getDate() + amount);
        return date;
    }

    function calendarDateKey(value) {
        const date = calendarStartOfDay(value);
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }

    function automationOccursOnDate(automation, value) {
        const schedule = automation.schedule || {};
        const date = calendarStartOfDay(value);
        if (schedule.kind === 'once') {
            if (!schedule.run_at) return false;
            return calendarDateKey(new Date(schedule.run_at)) === calendarDateKey(date);
        }
        if (schedule.kind === 'weekly') {
            const days = String(schedule.days || '1')
                .split(',')
                .map((day) => Number(day.trim()));
            return days.includes(date.getDay());
        }
        if (schedule.kind === 'monthly') {
            const days = String(schedule.days || '1')
                .split(',')
                .map((day) => Number(day.trim()));
            return days.includes(date.getDate());
        }
        return ['daily', 'hourly', 'interval'].includes(String(schedule.kind || 'daily'));
    }

    function automationCalendarTime(automation) {
        const schedule = automation.schedule || {};
        if (schedule.kind === 'once' && schedule.run_at) {
            return new Date(schedule.run_at).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            });
        }
        if (schedule.kind === 'hourly' || schedule.kind === 'interval') return automationScheduleLabel(schedule);
        return schedule.time || '09:00';
    }

    function scheduledCalendarEventsForDate(date) {
        return context.automations
            .filter((automation) => String(automation.automation_type || 'scheduled_instruction') === 'scheduled_instruction')
            .filter((automation) => automationOccursOnDate(automation, date))
            .sort((left, right) => `${automationCalendarTime(left)} ${left.name || ''}`.localeCompare(`${automationCalendarTime(right)} ${right.name || ''}`));
    }

    function scheduledCalendarEventHtml(automation, compact) {
        const paused = String(automation.status || '').toLowerCase() === 'paused';
        const label = `${automationCalendarTime(automation)} ${automation.name || 'Scheduled task'}`;
        return `<button type="button" class="scheduled-calendar-event${paused ? ' paused' : ''}${compact ? ' compact' : ''}" data-scheduled-calendar-event="${actions.escapeHtml(automation.id)}" title="${actions.escapeHtml(label)}"><time>${actions.escapeHtml(automationCalendarTime(automation))}</time><span>${actions.escapeHtml(automation.name || 'Scheduled task')}</span></button>`;
    }

    function renderScheduledCalendar() {
        const calendar = el('scheduled-calendar');
        const title = el('scheduled-calendar-title');
        if (!calendar || !title) return;
        const anchor = calendarStartOfDay(state.scheduledCalendarAnchor || new Date());
        const mode = state.scheduledCalendarMode;
        let html = '';
        if (mode === 'month') {
            title.textContent = anchor.toLocaleDateString([], {
                month: 'long',
                year: 'numeric'
            });
            const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
            const start = calendarStartOfWeek(first);
            html =
                '<div class="scheduled-calendar-weekdays">' +
                ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => `<span>${day}</span>`).join('') +
                '</div><div class="scheduled-calendar-month">';
            for (let index = 0; index < 42; index += 1) {
                const date = calendarAddDays(start, index);
                const events = scheduledCalendarEventsForDate(date);
                const outside = date.getMonth() !== anchor.getMonth();
                const today = calendarDateKey(date) === calendarDateKey(new Date());
                html += `<section class="scheduled-calendar-day${outside ? ' outside' : ''}${today ? ' today' : ''}"><header><time datetime="${calendarDateKey(date)}">${date.getDate()}</time></header><div>${events
                    .slice(0, 3)
                    .map((item) => scheduledCalendarEventHtml(item, true))
                    .join('')}${events.length > 3 ? `<small>+${events.length - 3} more</small>` : ''}</div></section>`;
            }
            html += '</div>';
        } else {
            const start = mode === 'week' ? calendarStartOfWeek(anchor) : anchor;
            const days = mode === 'week' ? Array.from({ length: 7 }, (_, index) => calendarAddDays(start, index)) : [start];
            const end = days[days.length - 1];
            title.textContent =
                mode === 'day'
                    ? anchor.toLocaleDateString([], {
                          weekday: 'long',
                          day: 'numeric',
                          month: 'long',
                          year: 'numeric'
                      })
                    : `${start.toLocaleDateString([], { day: 'numeric', month: 'short' })} to ${end.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}`;
            html =
                `<div class="scheduled-calendar-agenda ${mode}">` +
                days
                    .map((date) => {
                        const events = scheduledCalendarEventsForDate(date);
                        const today = calendarDateKey(date) === calendarDateKey(new Date());
                        return `<section class="scheduled-calendar-agenda-day${today ? ' today' : ''}"><header><span>${date.toLocaleDateString([], { weekday: 'short' })}</span><strong>${date.getDate()}</strong></header><div>${events.length ? events.map((item) => scheduledCalendarEventHtml(item, false)).join('') : '<p>No scheduled runs</p>'}</div></section>`;
                    })
                    .join('') +
                '</div>';
        }
        calendar.innerHTML = html;
        calendar.querySelectorAll('[data-scheduled-calendar-event]').forEach((button) =>
            button.addEventListener('click', () => {
                const automation = context.automations.find((item) => String(item.id) === String(button.dataset.scheduledCalendarEvent));
                if (automation) openAutomationDialog(automation);
            })
        );
    }

    function setScheduledView(view) {
        state.scheduledView = view === 'calendar' ? 'calendar' : 'list';
        renderScheduledWorkspace();
    }

    function setScheduledCalendarMode(mode) {
        state.scheduledCalendarMode = ['month', 'week', 'day'].includes(mode) ? mode : 'month';
        document.querySelectorAll('[data-scheduled-calendar-mode]').forEach((button) => {
            const active = button.dataset.scheduledCalendarMode === state.scheduledCalendarMode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', String(active));
        });
        renderScheduledCalendar();
    }

    function moveScheduledCalendar(amount) {
        const anchor = calendarStartOfDay(state.scheduledCalendarAnchor || new Date());
        if (state.scheduledCalendarMode === 'month') anchor.setMonth(anchor.getMonth() + amount, 1);
        else anchor.setDate(anchor.getDate() + amount * (state.scheduledCalendarMode === 'week' ? 7 : 1));
        state.scheduledCalendarAnchor = anchor;
        renderScheduledCalendar();
    }

    function automationLinkedChat(automation) {
        const chatId = Number(automation?.thread_chat_id || automation?.action_config?.development_chat_id || 0);
        if (!chatId) return null;
        return context.chats.concat(context.archivedChats).find((chat) => Number(chat.id) === chatId) || null;
    }

    function filteredAutomations() {
        const query = state.automationSearch.trim().toLowerCase();
        return context.automations.filter((automation) => {
            const type = String(automation.automation_type || 'scheduled_instruction');
            if (context.workspaceMode === 'automations' && type !== 'scheduled_instruction') return false;
            if (context.workspaceMode === 'automation_rules' && type === 'scheduled_instruction') return false;
            const status = String(automation.status || 'active').toLowerCase();
            const completed =
                status === 'completed' || (String(automation.schedule?.kind || '') === 'once' && automation.last_run_at && !automation.next_run_at);
            if (state.automationFilter === 'active' && (status !== 'active' || completed)) return false;
            if (state.automationFilter === 'paused' && status !== 'paused') return false;
            if (state.automationFilter === 'completed' && !completed) return false;
            if (state.automationFilter !== 'completed' && completed && state.automationFilter !== 'all') return false;
            if (!query) return true;
            return `${automation.name || ''} ${automation.instruction || ''}`.toLowerCase().includes(query);
        });
    }

    function scheduledRowHtml(automation) {
        const status = String(automation.status || 'active').toLowerCase();
        const linkedChat = automationLinkedChat(automation);
        const selected = String(state.selectedAutomationId) === String(automation.id);
        const timing =
            status === 'paused'
                ? 'Paused'
                : automation.next_run_at
                  ? `Next ${actions.formatDateTime(automation.next_run_at)}`
                  : automationScheduleLabel(automation.schedule);
        const config = automation.action_config || {};
        const route = config.model ? `${config.model_provider || 'auto'} · ${config.model}` : 'Automatic model';
        const linkedBoard = context.boards.find((board) => Number(board.local_id || board.id) === Number(automation.linked_board_id || config.linked_board_id));
        const scopeLabel = linkedBoard ? `${linkedBoard.name || 'Board'} · ${route}` : `Automation thread · ${route}`;
        return `<div class="scheduled-row${selected ? ' active' : ''}" data-scheduled-row="${actions.escapeHtml(automation.id)}">
            <span class="scheduled-row-state${status === 'running' || status === 'active' ? ' running' : ''}" aria-hidden="true"></span>
            <button type="button" class="scheduled-row-main" data-scheduled-select="${actions.escapeHtml(automation.id)}">
                <span class="scheduled-row-copy"><strong>${actions.escapeHtml(automation.name || 'Scheduled task')}</strong><small>${actions.escapeHtml(timing)} · ${actions.escapeHtml(scopeLabel)}</small></span>
            </button>
            <span class="scheduled-row-actions">
                <button type="button" data-scheduled-menu="${actions.escapeHtml(automation.id)}" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for ${actions.escapeHtml(automation.name || 'scheduled task')}"><svg viewBox="0 0 18 18" aria-hidden="true"><circle cx="4" cy="9" r="1.25"></circle><circle cx="9" cy="9" r="1.25"></circle><circle cx="14" cy="9" r="1.25"></circle></svg></button>
            </span>
        </div>`;
    }

    function closeScheduledRowMenu() {
        const menu = el('scheduled-row-menu');
        if (!menu) return;
        menu.classList.add('hidden');
        document.querySelectorAll('[data-scheduled-menu][aria-expanded="true"]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
        state.contextAutomationId = '';
    }

    function openScheduledRowMenu(event, automationId) {
        event.preventDefault();
        event.stopPropagation();
        const automation = context.automations.find((item) => String(item.id) === String(automationId));
        if (!automation) return;
        closeScheduledRowMenu();
        state.contextAutomationId = String(automation.id);
        const menu = el('scheduled-row-menu');
        const trigger = event.currentTarget;
        const status = String(automation.status || 'active').toLowerCase();
        const toggle = menu.querySelector('[data-scheduled-menu-action="toggle"]');
        toggle.textContent = status === 'paused' ? 'Resume' : 'Pause';
        toggle.dataset.nextAction = status === 'paused' ? 'resume' : 'pause';
        menu.classList.remove('hidden');
        trigger.setAttribute('aria-expanded', 'true');
        const rect = trigger.getBoundingClientRect();
        const left = Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8));
        const below = rect.bottom + 5;
        const top = below + menu.offsetHeight <= window.innerHeight - 8 ? below : Math.max(8, rect.top - menu.offsetHeight - 5);
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
        menu.focus({ preventScroll: true });
    }

    function runScheduledRowMenuAction(event) {
        const button = event.target.closest('[data-scheduled-menu-action]');
        if (!button) return;
        const automationId = state.contextAutomationId;
        const automation = context.automations.find((item) => String(item.id) === String(automationId));
        const action = button.dataset.scheduledMenuAction;
        const nextAction = button.dataset.nextAction;
        closeScheduledRowMenu();
        if (!automation) return;
        if (action === 'run') runAutomation(automation.id);
        else if (action === 'edit') openAutomationDialog(automation);
        else if (action === 'toggle') toggleAutomation(automation.id, nextAction || 'pause');
        else if (action === 'delete') deleteScheduledAutomation(automation.id);
    }

    function renderScheduledWorkspace() {
        const list = el('scheduled-list');
        if (!list) return;
        const rules = context.workspaceMode === 'automation_rules';
        if (rules) state.scheduledView = 'list';
        const calendarView = !rules && state.scheduledView === 'calendar';
        const viewToggle = el('scheduled-view-toggle');
        const viewToggleLabel = el('scheduled-view-toggle-label');
        viewToggle?.classList.toggle('hidden', rules);
        viewToggle?.setAttribute('aria-pressed', String(calendarView));
        if (viewToggleLabel) viewToggleLabel.textContent = calendarView ? 'List' : 'Calendar';
        el('scheduled-prompt-form')?.classList.toggle('hidden', calendarView);
        el('scheduled-workspace')?.querySelector('.scheduled-toolbar')?.classList.toggle('hidden', calendarView);
        el('scheduled-layout')?.classList.toggle('hidden', calendarView);
        el('scheduled-calendar-toolbar')?.classList.toggle('hidden', !calendarView);
        el('scheduled-calendar')?.classList.toggle('hidden', !calendarView);
        el('scheduled-prompt-input').placeholder = rules
            ? 'For example: when a WhatsApp message arrives, create a ticket for the linked board'
            : 'For example: run the Player1Sport test suite every weekday at 08:00';
        el('scheduled-prompt-submit').textContent = rules ? 'Automate' : 'Schedule';
        const deleteAllButton = el('scheduled-delete-all');
        if (deleteAllButton) deleteAllButton.disabled = context.automations.length === 0;
        document.querySelectorAll('[data-automation-filter]').forEach((button) => {
            const selected = button.dataset.automationFilter === state.automationFilter;
            button.classList.toggle('active', selected);
            button.setAttribute('aria-selected', String(selected));
        });
        const automations = filteredAutomations();
        list.innerHTML = automations.length
            ? automations.map(scheduledRowHtml).join('')
            : `<div class="development-empty">No ${rules ? 'automations' : 'scheduled tasks'} match this view.</div>`;
        list.querySelectorAll('[data-scheduled-select]').forEach((button) => {
            button.addEventListener('click', () => selectScheduledAutomation(button.dataset.scheduledSelect));
        });
        list.querySelectorAll('[data-scheduled-menu]').forEach((button) => {
            button.addEventListener('click', (event) => openScheduledRowMenu(event, button.dataset.scheduledMenu));
        });
        renderScheduledDetail();
        if (calendarView) {
            setScheduledCalendarMode(state.scheduledCalendarMode);
            renderScheduledCalendar();
        }
    }

    async function selectScheduledAutomation(automationId) {
        state.selectedAutomationId = String(automationId || '');
        const selection = state.selectedAutomationId;
        renderScheduledWorkspace();
        try {
            await actions.ensureProviderCatalogs();
            if (String(state.selectedAutomationId) === String(automationId)) renderScheduledDetail();
        } catch (_) {
            /* keep current route selectable when provider discovery is unavailable */
        }
        if (state.selectedAutomationId !== selection || !selection || state.automationRuns[selection]) return;
        try {
            const data = await actions.api(`/automations/${encodeURIComponent(selection)}/runs`);
            state.automationRuns[selection] = Array.isArray(data) ? data : data.runs || [];
        } catch (_) {
            state.automationRuns[selection] = [];
        }
        if (state.selectedAutomationId === selection) renderScheduledDetail();
    }

    function renderScheduledDetail() {
        const detail = el('scheduled-detail');
        const layout = el('scheduled-layout');
        if (!detail || !layout) return;
        const automation = context.automations.find((item) => String(item.id) === String(state.selectedAutomationId));
        detail.classList.toggle('hidden', !automation);
        layout.classList.toggle('detail-open', Boolean(automation));
        if (!automation) {
            detail.innerHTML = '';
            return;
        }
        const status = String(automation.status || 'active').toLowerCase();
        const linkedChat = automationLinkedChat(automation);
        const linkedBoard = context.boards.find(
            (board) => Number(board.local_id || board.id) === Number(automation.linked_board_id || automation.action_config?.linked_board_id)
        );
        const linkedWorkflow = context.workflows.find(
            (workflow) => Number(workflow.id) === Number(automation.optional_workflow_id || automation.action_config?.development_workflow_id)
        );
        const runs = state.automationRuns[String(automation.id)] || [];
        const latestRunThreadId = Number(runs.find((run) => Number(run.chat_id || 0))?.chat_id || 0);
        const config = automation.action_config || {};
        const routeProvider = String(config.model_provider || config.provider || '').toLowerCase();
        const routeModel = String(config.model || config.model_name || '');
        const fallbackRoute = config.fallback_model ? `${config.fallback_model_provider || 'Automatic'} / ${config.fallback_model}` : 'None';
        const importSource = config.import_source ? String(config.import_source) : '';
        detail.innerHTML = `<div class="scheduled-detail-head"><div><h2>${actions.escapeHtml(automation.name || 'Scheduled task')}</h2><div class="scheduled-detail-status">${actions.escapeHtml(status)}</div></div><button type="button" class="scheduled-detail-close" aria-label="Close details">×</button></div>
            <p class="scheduled-detail-instruction">${actions.escapeHtml(automation.instruction || 'No instruction provided.')}</p>
            <div class="scheduled-detail-facts">
                <div><span>Board</span><strong>${actions.escapeHtml(linkedBoard?.name || 'No board')}</strong></div>
                <div><span>Thread</span><strong>${actions.escapeHtml(linkedChat?.title || automation.name || 'Automation')}</strong></div>
                ${linkedWorkflow ? `<div><span>Workflow</span><strong>${actions.escapeHtml(linkedWorkflow.name || 'Workflow')}</strong></div>` : ''}
                <div class="scheduled-detail-control"><span>Provider</span><select data-detail-provider aria-label="Automation provider">${automationProviderOptions(routeProvider, 'Automatic')}</select></div>
                <div class="scheduled-detail-control"><span>Model</span><select data-detail-model aria-label="Automation model">${automationModelOptions(routeProvider, routeModel, 'Automatic')}</select></div>
                <div class="scheduled-detail-control"><span>Reasoning</span><select data-detail-reasoning aria-label="Automation reasoning"><option value="low"${String(config.reasoning_effort || 'medium') === 'low' ? ' selected' : ''}>Low</option><option value="medium"${String(config.reasoning_effort || 'medium') === 'medium' ? ' selected' : ''}>Medium</option><option value="high"${String(config.reasoning_effort || 'medium') === 'high' ? ' selected' : ''}>High</option><option value="xhigh"${String(config.reasoning_effort || 'medium') === 'xhigh' ? ' selected' : ''}>Extra high</option></select></div>
                <div><span>Fallback</span><strong>${actions.escapeHtml(fallbackRoute)}</strong></div>
                <div><span>Schedule</span><strong>${actions.escapeHtml(automationScheduleLabel(automation.schedule))}</strong></div>
                <div><span>Next run</span><strong>${actions.escapeHtml(automation.next_run_at ? actions.formatDateTime(automation.next_run_at) : status === 'paused' ? 'Paused' : 'Pending')}</strong></div>
                <div><span>Last run</span><strong>${actions.escapeHtml(automation.last_run_at ? actions.formatDateTime(automation.last_run_at) : 'Not run yet')}</strong></div>
            </div>
            ${importSource ? `<div class="scheduled-import-origin">Imported from ${actions.escapeHtml(importSource)}</div>` : ''}
            ${linkedChat ? `<button type="button" class="scheduled-thread-link" data-scheduled-thread="${Number(linkedChat.id)}">Open thread</button>` : latestRunThreadId ? `<button type="button" class="scheduled-thread-link" data-scheduled-run-thread="${latestRunThreadId}">Open thread</button>` : '<div class="scheduled-unlinked">The automation thread is created when this automation is saved.</div>'}
            <div class="scheduled-detail-actions">
                <button type="button" data-detail-run>Run now</button>
                <button type="button" data-detail-toggle data-action="${status === 'paused' ? 'resume' : 'pause'}">${status === 'paused' ? 'Resume' : 'Pause'}</button>
                <button type="button" data-detail-edit>Edit</button>
                <button type="button" class="danger" data-detail-delete>Delete</button>
            </div>
            <div class="scheduled-runs"><h3>Previous runs</h3>${
                runs.length
                    ? runs
                          .slice(0, 6)
                          .map(
                              (run) =>
                                  `<div class="scheduled-run"><div><strong>${actions.escapeHtml(actions.statusLabel(run.status))}</strong><span>${actions.escapeHtml(actions.formatDateTime(run.started_at || run.created_at))}</span></div>${run.summary ? `<p>${actions.escapeHtml(run.summary)}</p>` : ''}${run.chat_id ? `<button type="button" data-run-thread="${Number(run.chat_id)}">Open thread</button>` : ''}</div>`
                          )
                          .join('')
                    : '<div class="scheduled-run"><p>No runs recorded yet.</p></div>'
            }</div>`;
        detail.querySelector('.scheduled-detail-close').addEventListener('click', () => {
            state.selectedAutomationId = '';
            renderScheduledWorkspace();
        });
        detail.querySelector('[data-detail-run]').addEventListener('click', () => runAutomation(automation.id));
        detail.querySelector('[data-detail-toggle]').addEventListener('click', (event) => toggleAutomation(automation.id, event.currentTarget.dataset.action));
        detail.querySelector('[data-detail-edit]').addEventListener('click', () => openAutomationDialog(automation));
        detail.querySelector('[data-detail-delete]').addEventListener('click', () => deleteScheduledAutomation(automation.id));
        detail
            .querySelector('[data-detail-provider]')
            .addEventListener('change', (event) => updateScheduledRoute(automation, 'provider', event.currentTarget.value));
        detail.querySelector('[data-detail-model]').addEventListener('change', (event) => updateScheduledRoute(automation, 'model', event.currentTarget.value));
        detail
            .querySelector('[data-detail-reasoning]')
            .addEventListener('change', (event) => updateScheduledRoute(automation, 'reasoning_effort', event.currentTarget.value));
        detail
            .querySelector('[data-scheduled-thread]')
            ?.addEventListener('click', (event) => actions.loadChat(Number(event.currentTarget.dataset.scheduledThread)));
        detail
            .querySelector('[data-scheduled-run-thread]')
            ?.addEventListener('click', (event) => actions.loadChat(Number(event.currentTarget.dataset.scheduledRunThread)));
        detail
            .querySelectorAll('[data-run-thread]')
            .forEach((button) => button.addEventListener('click', () => actions.loadChat(Number(button.dataset.runThread))));
    }

    function syncAutomationScheduleFields() {
        const kind = el('automation-schedule-kind').value;
        const interval = kind === 'interval';
        const once = kind === 'once';
        el('automation-dialog')
            .querySelectorAll('.automation-interval-field')
            .forEach((field) => field.classList.toggle('hidden', !interval));
        el('automation-dialog').querySelector('.automation-once-field').classList.toggle('hidden', !once);
        el('automation-dialog')
            .querySelector('.automation-time-field')
            .classList.toggle('hidden', interval || once || kind === 'hourly');
        el('automation-dialog')
            .querySelector('.automation-weekly-days-field')
            .classList.toggle('hidden', kind !== 'weekly');
        el('automation-dialog')
            .querySelector('.automation-monthly-days-field')
            .classList.toggle('hidden', kind !== 'monthly');
    }

    function setAutomationWeekdays(days) {
        const selected = new Set(
            String(days || '1')
                .split(',')
                .map((value) => value.trim())
                .filter(Boolean)
        );
        el('automation-dialog')
            .querySelectorAll('.automation-weekday-options input')
            .forEach((input) => {
                input.checked = selected.has(input.value);
            });
    }

    function automationWeekdays() {
        const values = Array.from(el('automation-dialog').querySelectorAll('.automation-weekday-options input:checked')).map((input) => input.value);
        return values.length ? values.join(',') : '1';
    }

    function automationProviderOptions(selected, emptyLabel) {
        const options = context.providers.map((provider) => {
            const id = String(provider.id || provider).toLowerCase();
            return `<option value="${actions.escapeHtml(id)}"${String(selected || '').toLowerCase() === id ? ' selected' : ''}>${actions.escapeHtml(provider.name || provider.id || provider)}</option>`;
        });
        if (selected && !context.providers.some((provider) => String(provider.id || provider).toLowerCase() === String(selected).toLowerCase())) {
            options.push(`<option value="${actions.escapeHtml(selected)}" selected>${actions.escapeHtml(actions.providerLabel(selected))}</option>`);
        }
        return `<option value="">${actions.escapeHtml(emptyLabel || 'Automatic')}</option>` + options.join('');
    }

    function automationModelOptions(providerId, selected, emptyLabel) {
        const options = actions.catalogModels(providerId).map((model) => {
            const id = String(model.id || model.name || model);
            const label = String(model.name || model.id || model);
            return `<option value="${actions.escapeHtml(id)}"${String(selected || '') === id ? ' selected' : ''}>${actions.escapeHtml(label)}</option>`;
        });
        if (selected && !actions.catalogModels(providerId).some((model) => String(model.id || model.name || model) === String(selected))) {
            options.push(`<option value="${actions.escapeHtml(selected)}" selected>${actions.escapeHtml(selected)}</option>`);
        }
        return `<option value="">${actions.escapeHtml(emptyLabel || 'Automatic')}</option>` + options.join('');
    }

    function syncAutomationModelSelect(providerId, selectId, selected, emptyLabel) {
        const select = el(selectId);
        select.innerHTML = automationModelOptions(providerId, selected, emptyLabel);
    }

    function automationBackendForProvider(provider) {
        return provider === 'openai' ? 'codex' : provider === 'anthropic' ? 'claude' : provider ? 'pi' : '';
    }

    async function updateScheduledRoute(automation, field, value) {
        const config = { ...(automation.action_config || {}) };
        if (field === 'provider') {
            config.model_provider = String(value || '').toLowerCase();
            config.backend = automationBackendForProvider(config.model_provider);
            config.model = '';
        } else if (field === 'model') {
            config.model = String(value || '');
        } else if (field === 'reasoning_effort') {
            config.reasoning_effort = String(value || 'medium');
        }
        try {
            const response = await actions.api(`/automations/${encodeURIComponent(automation.id)}`, { method: 'PUT', body: { action_config: config } });
            const updated = response.automation || response;
            context.automations = context.automations.map((item) => (String(item.id) === String(automation.id) ? { ...item, ...updated } : item));
            renderScheduledWorkspace();
            const label = field === 'provider' ? 'Provider' : field === 'model' ? 'Model' : 'Reasoning';
            actions.toast(`${label} updated.`);
        } catch (error) {
            actions.toast(error.message || 'Could not update the automation model.', 'error');
            renderScheduledDetail();
        }
    }

    async function openAutomationDialog(automation) {
        const editing = Boolean(automation);
        const schedule = automation?.schedule || {};
        const config = automation?.action_config || {};
        const weekdaySchedule = schedule.kind === 'weekly' && String(schedule.days || '') === '1,2,3,4,5';
        el('automation-id').value = editing ? automation.id : '';
        el('automation-dialog-title').textContent = editing ? 'Edit scheduled task' : 'Create scheduled task';
        el('automation-name').value = editing ? automation.name || '' : context.currentChat ? `${context.currentChat.title || 'Development'} automation` : '';
        el('automation-instruction').value = editing ? automation.instruction || '' : context.currentChat ? actions.developmentInstruction() : '';
        const currentBoard = actions.threadBoard(context.currentChat);
        const selectedBoardId = Number(automation?.linked_board_id || config.linked_board_id || currentBoard?.local_id || currentBoard?.id || 0);
        const registeredBoards = context.boards.filter((board) => Number(board.local_id || 0) || board.provider === 'decisions');
        el('automation-board').innerHTML =
            '<option value="">No board</option>' +
            registeredBoards
                .map((board) => {
                    const id = Number(board.local_id || board.id);
                    return `<option value="${id}"${selectedBoardId === id ? ' selected' : ''}>${actions.escapeHtml(board.name || 'Board')}</option>`;
                })
                .join('');
        const selectedWorkflowId = Number(automation?.optional_workflow_id || config.development_workflow_id || 0);
        el('automation-workflow').innerHTML =
            '<option value="">No workflow</option>' +
            context.workflows
                .map((workflow) => {
                    const id = Number(workflow.id);
                    return `<option value="${id}"${selectedWorkflowId === id ? ' selected' : ''}>${actions.escapeHtml(workflow.name || 'Workflow')}</option>`;
                })
                .join('');
        const source = String(automation?.source_config?.source || automation?.action_config?.source_config?.source || '');
        el('automation-trigger').value =
            String(automation?.automation_type || 'scheduled_instruction') === 'channel_intake' ? source || 'whatsapp' : 'scheduled_instruction';
        el('automation-schedule-kind').value = weekdaySchedule ? 'weekdays' : schedule.kind || 'daily';
        el('automation-time').value = schedule.time || '09:00';
        el('automation-once-at').value = String(schedule.run_at || '').slice(0, 16);
        el('automation-interval').value = schedule.interval || 30;
        el('automation-interval-unit').value = schedule.interval_unit === 'seconds' ? 'seconds' : 'minutes';
        el('automation-monthly-days').value = schedule.kind === 'monthly' ? String(schedule.days || '1') : '1';
        el('automation-timezone').value = schedule.timezone || '';
        setAutomationWeekdays(weekdaySchedule ? '1,2,3,4,5' : schedule.days || '1');
        try {
            await actions.ensureProviderCatalogs();
        } catch (_) {
            /* automatic routing remains available */
        }
        const provider = String(config.model_provider || config.provider || '').toLowerCase();
        const fallbackProvider = String(config.fallback_model_provider || config.fallback_provider || '').toLowerCase();
        el('automation-provider').innerHTML = automationProviderOptions(provider, 'Automatic');
        el('automation-fallback-provider').innerHTML = automationProviderOptions(fallbackProvider, 'None');
        syncAutomationModelSelect(provider, 'automation-model', config.model || config.model_name || '', 'Automatic');
        syncAutomationModelSelect(fallbackProvider, 'automation-fallback-model', config.fallback_model || '', 'None');
        el('automation-reasoning').value = ['low', 'medium', 'high', 'xhigh'].includes(String(config.reasoning_effort || 'medium'))
            ? String(config.reasoning_effort || 'medium')
            : 'medium';
        el('delete-automation-button').classList.toggle('hidden', !editing);
        el('save-automation-button').textContent = editing ? 'Save automation' : 'Create automation';
        syncAutomationScheduleFields();
        el('automation-dialog').showModal();
        window.setTimeout(() => el('automation-name').focus(), 0);
    }

    function automationSchedulePayload() {
        const selected = el('automation-schedule-kind').value;
        const timezone = el('automation-timezone').value.trim();
        if (selected === 'once') {
            return { kind: 'once', run_at: el('automation-once-at').value, timezone };
        }
        if (selected === 'interval') {
            return {
                kind: 'interval',
                interval: Number(el('automation-interval').value || 30),
                interval_unit: el('automation-interval-unit').value || 'minutes',
                timezone
            };
        }
        if (selected === 'weekdays') {
            return {
                kind: 'weekly',
                time: el('automation-time').value || '09:00',
                days: '1,2,3,4,5',
                timezone
            };
        }
        if (selected === 'weekly') {
            return {
                kind: 'weekly',
                time: el('automation-time').value || '09:00',
                days: automationWeekdays(),
                timezone
            };
        }
        if (selected === 'monthly') {
            return {
                kind: 'monthly',
                time: el('automation-time').value || '09:00',
                days: el('automation-monthly-days').value.trim() || '1',
                timezone
            };
        }
        return {
            kind: selected,
            time: el('automation-time').value || '09:00',
            days: '1',
            timezone
        };
    }

    async function saveAutomation(event) {
        event.preventDefault();
        const automationId = el('automation-id').value;
        const existing = context.automations.find((item) => String(item.id) === String(automationId));
        const provider = el('automation-provider').value;
        const fallbackProvider = el('automation-fallback-provider').value;
        const base = {
            name: el('automation-name').value.trim(),
            instruction: el('automation-instruction').value.trim(),
            schedule: automationSchedulePayload(),
            automation_type: el('automation-trigger').value === 'scheduled_instruction' ? 'scheduled_instruction' : 'channel_intake',
            source_config:
                el('automation-trigger').value === 'scheduled_instruction'
                    ? {}
                    : {
                          source: el('automation-trigger').value,
                          trigger: 'incoming_message'
                      },
            linked_board_id: Number(el('automation-board').value || 0) || null,
            optional_workflow_id: Number(el('automation-workflow').value || 0) || null,
            action_config: {
                ...(existing?.action_config || {}),
                run_in_new_thread: true,
                backend: automationBackendForProvider(provider),
                model_provider: provider,
                model: el('automation-model').value,
                reasoning_effort: el('automation-reasoning').value || 'medium',
                fallback_backend: automationBackendForProvider(fallbackProvider),
                fallback_model_provider: fallbackProvider,
                fallback_model: el('automation-fallback-model').value
            }
        };
        if (!base.name || !base.instruction) return;
        try {
            let savedAutomation = null;
            if (automationId) {
                const response = await actions.api(`/automations/${encodeURIComponent(automationId)}`, { method: 'PUT', body: base });
                savedAutomation = response.automation || response;
                actions.toast('Automation updated.');
            } else {
                const response = await actions.api('/automations', {
                    method: 'POST',
                    body: {
                        ...base,
                        automation_type: base.automation_type,
                        source_config: base.source_config,
                        linked_board_id: base.linked_board_id,
                        optional_workflow_id: base.optional_workflow_id,
                        action_config: base.action_config,
                        notification_policy: {
                            telegram: true,
                            waiting: true,
                            completed: true,
                            failed: true
                        }
                    }
                });
                savedAutomation = response.automation || response;
                actions.toast('Automation created.');
            }
            el('automation-dialog').close();
            await actions.refreshShell({ preserveConversation: true });
            state.selectedAutomationId = String(savedAutomation?.id || automationId || '');
            const threadId = Number(savedAutomation?.thread_chat_id || savedAutomation?.action_config?.development_chat_id || 0);
            const ownedThread = context.chats.concat(context.archivedChats).find((chat) => Number(chat.id) === threadId);
            if (!automationId && ownedThread) {
                await actions.loadChat(threadId);
                return;
            }
            if (['automations', 'automation_rules'].includes(context.workspaceMode)) renderScheduledWorkspace();
        } catch (error) {
            actions.toast(error.message || 'Could not save the automation.', 'error');
        }
    }

    function closeAutomationImportMenu() {
        el('scheduled-import-menu')?.classList.add('hidden');
        el('scheduled-import-open')?.setAttribute('aria-expanded', 'false');
    }

    function renderAutomationImportMenu() {
        const menu = el('scheduled-import-menu');
        const available = state.automationImportSources.filter((source) => source.available);
        if (!available.length) {
            menu.innerHTML = '<div class="scheduled-import-empty">No local schedules available</div>';
            return;
        }
        menu.innerHTML = available
            .map(
                (source) =>
                    `<button type="button" role="menuitem" data-automation-import-source="${actions.escapeHtml(source.id)}"><span>${actions.escapeHtml(source.label || source.id)}</span><small>${Number(source.new || 0)} new · ${Number(source.existing || 0)} imported${source.errors?.length ? ` · ${source.errors.length} blocked` : ''}</small></button>`
            )
            .join('');
        menu.querySelectorAll('[data-automation-import-source]').forEach((button) => {
            button.addEventListener('click', () => openAutomationImportDialog(button.dataset.automationImportSource));
        });
    }

    async function toggleAutomationImportMenu() {
        const menu = el('scheduled-import-menu');
        const opening = menu.classList.contains('hidden');
        if (!opening) {
            closeAutomationImportMenu();
            return;
        }
        menu.innerHTML = '<div class="scheduled-import-empty">Looking for schedules…</div>';
        menu.classList.remove('hidden');
        el('scheduled-import-open').setAttribute('aria-expanded', 'true');
        try {
            const result = await actions.api('/automations/imports');
            state.automationImportSources = result.sources || [];
            state.automationImportDefaults = result.routing_defaults || {};
            renderAutomationImportMenu();
        } catch (error) {
            menu.innerHTML = `<div class="scheduled-import-empty error">${actions.escapeHtml(error.message || 'Could not inspect local schedules.')}</div>`;
        }
    }

    function syncAutomationImportRouteNote() {
        const provider = el('automation-import-provider').value;
        const model = el('automation-import-model').value;
        const environment = el('automation-import-environment').value;
        const adaptive = el('automation-import-adaptive').checked;
        const note = el('automation-import-route-note');
        const route =
            provider && model
                ? `${actions.providerLabel(provider)} / ${el('automation-import-model').selectedOptions[0]?.textContent || model}`
                : 'Automatic model selection';
        note.textContent = `${environment === 'local' ? 'Local-first' : environment === 'hosted' ? 'Hosted' : 'Automatic location'} · ${route}${adaptive ? ' · adaptive review and recovery enabled' : ' · fixed route'}`;
        note.classList.toggle('is-remote', environment === 'hosted');
    }

    async function openAutomationImportDialog(source) {
        if (!source) return;
        const sourceRow = state.automationImportSources.find((item) => item.id === source);
        closeAutomationImportMenu();
        try {
            await actions.ensureProviderCatalogs();
        } catch (error) {
            actions.toast(error.message || 'Could not load model choices.', 'error');
            return;
        }
        const defaults = state.automationImportDefaults || {};
        const preferredProvider = String(defaults.model_provider || 'ollama').toLowerCase();
        const preferredModel = String(defaults.model || 'muse-glimmer:30b-mlx');
        const preferredAvailable = actions.catalogModels(preferredProvider).some((model) => String(model.id || model.name || model) === preferredModel);
        const provider = preferredAvailable ? preferredProvider : '';
        const model = preferredAvailable ? preferredModel : '';
        el('automation-import-source').value = source;
        el('automation-import-title').textContent = `Import ${sourceRow?.label || source} schedules`;
        el('automation-import-summary').textContent =
            `${Number(sourceRow?.new || 0)} new and ${Number(sourceRow?.existing || 0)} already imported. Confirm one starting route for this source.${sourceRow?.errors?.length ? ' Cannot import: ' + sourceRow.errors.map((item) => `${item.name}: ${item.message}`).join('; ') : ''}`;
        el('automation-import-provider').innerHTML = automationProviderOptions(provider, 'Automatic');
        syncAutomationModelSelect(provider, 'automation-import-model', model, 'Automatic');
        el('automation-import-complexity').value = defaults.complexity || 'medium';
        el('automation-import-reasoning').value = defaults.reasoning_effort || 'medium';
        el('automation-import-environment').value = defaults.execution_environment || 'local';
        el('automation-import-adaptive').checked = defaults.adaptive_model_routing !== false;
        el('automation-import-update-existing').checked = defaults.update_existing !== false;
        el('automation-import-submit').disabled = false;
        el('automation-import-submit').textContent = 'Import schedules';
        syncAutomationImportRouteNote();
        el('automation-import-dialog').showModal();
    }

    async function importAutomationSource(event) {
        event.preventDefault();
        const source = el('automation-import-source').value;
        if (!source) return;
        const sourceRow = state.automationImportSources.find((item) => item.id === source);
        const label = sourceRow?.label || source;
        const button = el('automation-import-submit');
        button.disabled = true;
        button.textContent = 'Importing…';
        try {
            const result = await actions.api('/automations/imports', {
                method: 'POST',
                body: {
                    sources: [source],
                    routing: {
                        model_provider: el('automation-import-provider').value,
                        model: el('automation-import-model').value,
                        complexity: el('automation-import-complexity').value,
                        reasoning_effort: el('automation-import-reasoning').value,
                        execution_environment: el('automation-import-environment').value,
                        adaptive_model_routing: el('automation-import-adaptive').checked,
                        allow_provider_failover: true,
                        update_existing: el('automation-import-update-existing').checked
                    }
                }
            });
            const message = `${Number(result.imported_count || 0)} imported · ${Number(result.updated_count || 0)} updated · ${Number(result.skipped_count || 0)} unchanged`;
            if (result.errors?.length) {
                el('automation-import-summary').textContent =
                    `${message}. Not imported: ${result.errors.map((item) => `${item.name}: ${item.message}`).join('; ')}`;
                button.disabled = false;
                button.textContent = 'Retry supported schedules';
            } else el('automation-import-dialog').close('imported');
            await actions.refreshShell({ preserveConversation: true });
            renderScheduledWorkspace();
            actions.toast(`${label}: ${message}`);
        } catch (error) {
            actions.toast(error.message || 'Could not import scheduled tasks.', 'error');
            button.disabled = false;
            button.textContent = 'Try again';
        }
    }

    async function createScheduledFromPrompt(event) {
        event.preventDefault();
        const input = el('scheduled-prompt-input');
        const instruction = input.value.trim();
        if (!instruction || state.automationDrafting) return;
        state.automationDrafting = true;
        el('scheduled-prompt-submit').disabled = true;
        el('scheduled-prompt-submit').textContent = 'Scheduling…';
        try {
            const draft = await actions.api('/automations/draft', {
                method: 'POST',
                body: {
                    instruction,
                    current_thread_title: context.currentChat?.title || null
                }
            });
            const linkCurrentThread = Boolean(draft.link_current_thread && context.currentChat);
            const actionConfig = {
                run_in_new_thread: true,
                linked_project_id: linkCurrentThread ? context.currentChat?.project_id || null : null,
                model_provider: '',
                model: '',
                reasoning_effort: 'medium',
                fallback_model_provider: '',
                fallback_model: ''
            };
            const created = await actions.api('/automations', {
                method: 'POST',
                body: {
                    name: draft.name,
                    instruction: draft.instruction || instruction,
                    schedule: draft.schedule,
                    automation_type: draft.automation_type || (context.workspaceMode === 'automation_rules' ? 'channel_intake' : 'scheduled_instruction'),
                    source_config: draft.source_config || {},
                    linked_project_id: linkCurrentThread ? context.currentChat.project_id || undefined : undefined,
                    action_config: actionConfig,
                    notification_policy: {
                        telegram: true,
                        waiting: true,
                        completed: true,
                        failed: true
                    }
                }
            });
            input.value = '';
            const createdAutomation = created.automation || created;
            state.selectedAutomationId = String(createdAutomation.id || '');
            await actions.refreshShell({ preserveConversation: true });
            const threadId = Number(createdAutomation.thread_chat_id || createdAutomation.action_config?.development_chat_id || 0);
            const ownedThread = context.chats.concat(context.archivedChats).find((chat) => Number(chat.id) === threadId);
            if (ownedThread) await actions.loadChat(threadId);
            else renderScheduledWorkspace();
            actions.toast(context.workspaceMode === 'automation_rules' ? 'Automation created.' : 'Scheduled task created.');
        } catch (error) {
            actions.toast(error.message || 'Could not create the scheduled task.', 'error');
        } finally {
            state.automationDrafting = false;
            el('scheduled-prompt-submit').disabled = false;
            el('scheduled-prompt-submit').textContent = context.workspaceMode === 'automation_rules' ? 'Automate' : 'Schedule';
            input.focus();
        }
    }

    async function runAutomation(automationId) {
        try {
            const result = await actions.api(`/automations/${encodeURIComponent(automationId)}/run`, { method: 'POST', body: {} });
            actions.toast(result.run?.summary || 'Automation started.');
            delete state.automationRuns[String(automationId)];
            await actions.refreshShell({
                preserveConversation: ['automations', 'automation_rules'].includes(context.workspaceMode)
            });
            if (['automations', 'automation_rules'].includes(context.workspaceMode)) await selectScheduledAutomation(automationId);
        } catch (error) {
            actions.toast(error.message || 'Could not run the automation.', 'error');
        }
    }

    async function toggleAutomation(automationId, action) {
        try {
            await actions.api(`/automations/${encodeURIComponent(automationId)}/${action}`, { method: 'POST', body: {} });
            actions.toast(action === 'pause' ? 'Automation paused.' : 'Automation resumed.');
            await actions.refreshShell({ preserveConversation: true });
            if (['automations', 'automation_rules'].includes(context.workspaceMode)) renderScheduledWorkspace();
        } catch (error) {
            actions.toast(error.message || 'Could not update the automation.', 'error');
        }
    }

    async function deleteAutomation() {
        const automationId = el('automation-id').value;
        if (!automationId) return;
        const automation = context.automations.find((item) => String(item.id) === String(automationId)) || null;
        el('automation-dialog').close();
        if (
            !(await actions.confirmAction({
                title: 'Delete Development automation',
                message: 'Delete this Development automation? Its run history will also be removed.',
                confirmLabel: 'Delete',
                danger: true
            }))
        ) {
            if (automation) openAutomationDialog(automation);
            return;
        }
        try {
            await actions.api(`/automations/${encodeURIComponent(automationId)}`, {
                method: 'DELETE'
            });
            el('automation-dialog').close();
            await actions.refreshShell({ preserveConversation: true });
            actions.toast('Automation deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the automation.', 'error');
        }
    }

    async function deleteScheduledAutomation(automationId) {
        const automation = context.automations.find((item) => String(item.id) === String(automationId));
        if (!automation) return;
        if (
            !(await actions.confirmAction({
                title: 'Delete scheduled task',
                message: `Delete “${automation.name || 'this scheduled task'}”? Its run history will also be removed.`,
                confirmLabel: 'Delete',
                danger: true
            }))
        )
            return;
        try {
            await actions.api(`/automations/${encodeURIComponent(automationId)}`, {
                method: 'DELETE'
            });
            state.selectedAutomationId = '';
            delete state.automationRuns[String(automationId)];
            await actions.refreshShell({ preserveConversation: true });
            actions.toast('Scheduled task deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the scheduled task.', 'error');
        }
    }

    async function deleteAllScheduledAutomations() {
        const total = context.automations.length;
        if (!total) return;
        if (
            !(await actions.confirmAction({
                title: 'Delete all automations',
                message: `Delete all ${total} automations, their run history, and their automation threads? This cannot be undone.`,
                confirmLabel: 'Delete all',
                danger: true
            }))
        )
            return;
        const button = el('scheduled-delete-all');
        if (button) button.disabled = true;
        try {
            const result = await actions.api('/automations?confirm=true', {
                method: 'DELETE'
            });
            state.selectedAutomationId = '';
            state.contextAutomationId = '';
            state.automationRuns = {};
            state.automationSearch = '';
            el('scheduled-search-input').value = '';
            await actions.refreshShell({ preserveConversation: true });
            const deleted = Number(result.deleted || total);
            actions.toast(`${deleted} automation${deleted === 1 ? '' : 's'} deleted.`);
        } catch (error) {
            if (button) button.disabled = context.automations.length === 0;
            actions.toast(error.message || 'Could not delete all automations.', 'error');
        }
    }

    function bindEvents() {
        el('scheduled-view-toggle').addEventListener('click', () => setScheduledView(state.scheduledView === 'calendar' ? 'list' : 'calendar'));
        el('scheduled-calendar-previous').addEventListener('click', () => moveScheduledCalendar(-1));
        el('scheduled-calendar-next').addEventListener('click', () => moveScheduledCalendar(1));
        el('scheduled-calendar-today').addEventListener('click', () => {
            state.scheduledCalendarAnchor = new Date();
            renderScheduledCalendar();
        });
        document
            .querySelectorAll('[data-scheduled-calendar-mode]')
            .forEach((button) => button.addEventListener('click', () => setScheduledCalendarMode(button.dataset.scheduledCalendarMode)));
        el('scheduled-row-menu').addEventListener('click', runScheduledRowMenuAction);
        el('scheduled-import-open').addEventListener('click', toggleAutomationImportMenu);
        el('automation-import-form').addEventListener('submit', importAutomationSource);
        el('automation-import-provider').addEventListener('change', (event) => {
            syncAutomationModelSelect(event.target.value, 'automation-import-model', '', 'Automatic');
            syncAutomationImportRouteNote();
        });
        el('automation-import-model').addEventListener('change', syncAutomationImportRouteNote);
        el('automation-import-environment').addEventListener('change', syncAutomationImportRouteNote);
        el('automation-import-adaptive').addEventListener('change', syncAutomationImportRouteNote);
        el('automation-provider').addEventListener('change', (event) => syncAutomationModelSelect(event.target.value, 'automation-model', '', 'Automatic'));
        el('automation-fallback-provider').addEventListener('change', (event) =>
            syncAutomationModelSelect(event.target.value, 'automation-fallback-model', '', 'None')
        );
        el('scheduled-prompt-form').addEventListener('submit', createScheduledFromPrompt);
        el('scheduled-search-input').addEventListener('input', (event) => {
            state.automationSearch = event.target.value;
            renderScheduledWorkspace();
        });
        el('scheduled-delete-all').addEventListener('click', deleteAllScheduledAutomations);
        document.querySelectorAll('[data-automation-filter]').forEach((button) =>
            button.addEventListener('click', () => {
                state.automationFilter = button.dataset.automationFilter || 'all';
                renderScheduledWorkspace();
            })
        );
        el('automation-form').addEventListener('submit', saveAutomation);
        el('automation-schedule-kind').addEventListener('change', syncAutomationScheduleFields);
        el('delete-automation-button').addEventListener('click', deleteAutomation);
    }
    return {
        bindEvents,
        closeAutomationImportMenu,
        closeScheduledRowMenu,
        openAutomationDialog,
        renderScheduledWorkspace,
        runAutomation,
        toggleAutomation
    };
}
