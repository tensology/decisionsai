// Development automation list/calendar. Full edits use the shared Automations
// editor so execution bindings and schedule metadata have one save path.
import { displayModelName, shortModelName } from '../../shared/model_variant.js?v=20261022-automodel';
import { SNAP_MINUTES, snapMinutes, timeToMinutes, minutesToTime, topForMinutes, shiftSchedule, groupCalendarEvents } from './calendar_drag.js?v=20261030-calendar-groups';
import { loadInstalledHarnesses, harnessSelectOptions, harnessLabel } from '../../shared/harnesses.js?v=20261022-harnesssrc';

export function createAutomations({ context, actions, el }) {
    // autofilter 2026-10-22: view + status filter remembered for the session (sessionStorage, as chat.js does).
    const VIEW_KEY = 'decisions_automations_view';
    const STATUS_KEY = 'decisions_automations_status_filter';
    const readSession = (key, fallback) => { try { return sessionStorage.getItem(key) || fallback; } catch (_) { return fallback; } };
    const writeSession = (key, value) => { try { sessionStorage.setItem(key, value); } catch (_) {} };
    const state = {
        selectedAutomationId: '',
        automationRuns: {},
        view: readSession(VIEW_KEY, 'list') === 'calendar' ? 'calendar' : 'list',
        statusFilter: readSession(STATUS_KEY, 'all'),
        calendarMode: 'week',
        calendarAnchor: new Date()
    };

    const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const esc = (value) => actions.escapeHtml(value);

    function isMessageTriggered(automation) {
        return String(automation?.automation_type || '') === 'channel_intake';
    }

    function scheduleTimes(schedule) {
        // "Several times a day" may be stored as a comma-separated time list.
        return String(schedule?.time || '09:00')
            .split(',')
            .map((item) => item.trim())
            .filter((item) => /^\d{1,2}:\d{2}$/.test(item))
            .map((item) => item.padStart(5, '0'))
            .sort();
    }

    function scheduleDays(schedule) {
        return String(schedule?.days || '1')
            .split(',')
            .map((day) => Number(day.trim()))
            .filter((day) => Number.isFinite(day));
    }

    function automationScheduleLabel(automation) {
        if (isMessageTriggered(automation)) {
            const source = String(automation?.source_config?.source || 'channel');
            return `On incoming ${source.charAt(0).toUpperCase()}${source.slice(1)} message`;
        }
        const value = automation?.schedule || {};
        const times = scheduleTimes(value);
        const at = times.length ? times.join(', ') : '09:00';
        if (value.kind === 'cron') return `Cron: ${value.cron || value.expression || 'custom schedule'}`;
        if (value.kind === 'interval') return `Every ${value.interval || 30} ${value.interval_unit || 'minutes'}`;
        if (value.kind === 'hourly') return 'Every hour';
        if (value.kind === 'once') return value.run_at ? `Once, ${actions.formatDateTime(value.run_at)}` : 'Once';
        if (value.kind === 'weekly') {
            const days = scheduleDays(value);
            if (days.join(',') === '1,2,3,4,5') return `Weekdays at ${at}`;
            const names = days.map((day) => WEEKDAYS[day]).filter(Boolean);
            return names.length ? `${names.join(', ')} at ${at}` : `Weekly at ${at}`;
        }
        if (value.kind === 'yearly') return `Yearly on ${Number(scheduleDays(value)[0] || 1)} ${MONTHS[Number(value.month || 1) - 1] || 'Jan'} at ${at}`;
        if (value.kind === 'monthly') return `Monthly on day ${scheduleDays(value).join(', ') || 1} at ${at}`;
        return times.length > 1 ? `Daily at ${at}` : `Daily at ${at}`;
    }

    function whatItDoes(automation, max = 110) {
        const text = String(automation?.instruction || automation?.description || '')
            .replace(/\s+/g, ' ')
            .trim();
        if (!text) return 'No description provided.';
        if (text.length <= max) return text;
        const cut = text.slice(0, max);
        const space = cut.lastIndexOf(' ');
        return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,.;:–-]+$/, '')}…`;
    }

    function automationGroup(automation) {
        const status = String(automation.status || 'active').toLowerCase();
        const completed =
            status === 'completed' || (String(automation.schedule?.kind || '') === 'once' && automation.last_run_at && !automation.next_run_at);
        if (completed) return 'completed';
        if (status === 'paused') return 'paused';
        return 'active';
    }

    function automationProject(automation) {
        const config = automation.action_config || {};
        const board = (context.boards || []).find(
            (item) => Number(item.local_id || item.id) === Number(automation.linked_board_id || config.linked_board_id)
        );
        const projectId = Number(config.linked_project_id || board?.project_id || board?.default_project_id || 0);
        const project = projectId ? (context.projects || []).find((item) => Number(item.id) === projectId) : null;
        return project?.name || board?.name || 'No project';
    }

    const STATUS_ORDER = ['active', 'paused', 'completed'];
    const STATUS_LABELS = { active: 'Active', paused: 'Paused', completed: 'Completed' };
    // Statuses actually present in the listing (the same groups the list renders).
    function availableStatuses() {
        const present = new Set(typeAutomations().map(automationGroup));
        return STATUS_ORDER.filter((key) => present.has(key));
    }
    function renderStatusFilter() {
        const root = el('scheduled-status-filter');
        const list = el('scheduled-status-filter-list');
        if (!root || !list) return;
        const statuses = availableStatuses();
        if (state.statusFilter !== 'all' && !statuses.includes(state.statusFilter)) statuses.push(state.statusFilter);
        const all = typeAutomations();
        const countFor = (key) => (key === 'all' ? all.length : all.filter((automation) => automationGroup(automation) === key).length);
        const labelFor = (key) => (key === 'all' ? 'All' : STATUS_LABELS[key] || esc(key));
        list.innerHTML = ['all', ...statuses]
            .map((key) => `<li role="option" id="scheduled-status-option-${esc(key)}" class="studio-dropdown-option" data-value="${esc(key)}" tabindex="-1" aria-selected="${key === state.statusFilter}"><span>${labelFor(key)}</span><span class="studio-count-pill">${countFor(key)}</span></li>`)
            .join('');
        root.dataset.value = state.statusFilter;
        root.querySelector('.studio-dropdown-value').textContent = key2text(labelFor(state.statusFilter));
        root.querySelector('.studio-dropdown-button .studio-count-pill').textContent = String(countFor(state.statusFilter));
    }
    function key2text(html) {
        const node = document.createElement('span');
        node.innerHTML = html;
        return node.textContent;
    }
    // Custom listbox dropdown (a native <select> cannot render count pills).
    function bindStatusDropdown() {
        const root = el('scheduled-status-filter');
        const button = el('scheduled-status-filter-button');
        const list = el('scheduled-status-filter-list');
        if (!root || !button || !list) return;
        const options = () => Array.from(list.querySelectorAll('[role="option"]'));
        const isOpen = () => button.getAttribute('aria-expanded') === 'true';
        const close = (focusButton) => {
            button.setAttribute('aria-expanded', 'false');
            list.classList.add('hidden');
            list.removeAttribute('aria-activedescendant');
            if (focusButton) button.focus();
        };
        const focusOption = (option) => {
            if (!option) return;
            options().forEach((item) => item.classList.toggle('active', item === option));
            list.setAttribute('aria-activedescendant', option.id);
            option.focus();
        };
        const open = () => {
            button.setAttribute('aria-expanded', 'true');
            list.classList.remove('hidden');
            focusOption(options().find((item) => item.getAttribute('aria-selected') === 'true') || options()[0]);
        };
        const choose = (option) => {
            if (!option) return;
            state.statusFilter = option.dataset.value || 'all';
            writeSession(STATUS_KEY, state.statusFilter);
            close(true);
            renderScheduledWorkspace();
        };
        button.addEventListener('click', () => (isOpen() ? close(false) : open()));
        button.addEventListener('keydown', (event) => {
            if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) {
                event.preventDefault();
                open();
            }
        });
        list.addEventListener('click', (event) => choose(event.target.closest('[role="option"]')));
        list.addEventListener('keydown', (event) => {
            const items = options();
            const index = items.indexOf(document.activeElement);
            if (event.key === 'ArrowDown') { event.preventDefault(); focusOption(items[Math.min(items.length - 1, index + 1)]); }
            else if (event.key === 'ArrowUp') { event.preventDefault(); focusOption(items[Math.max(0, index - 1)]); }
            else if (event.key === 'Home') { event.preventDefault(); focusOption(items[0]); }
            else if (event.key === 'End') { event.preventDefault(); focusOption(items[items.length - 1]); }
            else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(document.activeElement.closest('[role="option"]')); }
            else if (event.key === 'Escape') { event.preventDefault(); close(true); }
            else if (event.key === 'Tab') close(false);
        });
        document.addEventListener('click', (event) => { if (isOpen() && !root.contains(event.target)) close(false); });
    }
    function visibleAutomations() {
        return typeAutomations().filter((automation) => state.statusFilter === 'all' || automationGroup(automation) === state.statusFilter);
    }
    function typeAutomations() {
        return (context.automations || []).filter((automation) => {
            const type = String(automation.automation_type || 'scheduled_instruction');
            if (context.workspaceMode === 'automation_rules') return type !== 'scheduled_instruction';
            return true;
        });
    }

    // ---------- list ----------
    function scheduledRowHtml(automation) {
        const id = esc(automation.id);
        const group = automationGroup(automation);
        const selected = String(state.selectedAutomationId) === String(automation.id);
        const nextCell = isMessageTriggered(automation)
            ? { text: 'On message', title: '' }
            : automation.next_run_at
                ? { text: relativeWhen(automation.next_run_at), title: whenTitle(automation.next_run_at) }
                : { text: '—', title: '' };
        const lastCell = automation.last_run_at
            ? { text: relativeWhen(automation.last_run_at), title: whenTitle(automation.last_run_at) }
            : { text: 'Not run yet', title: '' };
        const created = automation.created_at ? new Date(automation.created_at) : null;
        // automodel: the old "#auto_1" tag was just the internal record id prefix; show only the created date.
        const ident = created && !Number.isNaN(created.getTime()) ? `Created ${created.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric', timeZone: SAST })}` : '';
        const statusLabel = group === 'completed' ? 'Completed' : group === 'paused' ? 'Paused' : 'Active';
        return `<div class="scheduled-row readonly${selected ? ' active' : ''}" data-scheduled-row="${id}" role="row">
            <div class="scheduled-row-main" role="cell">
                <span class="scheduled-row-title"><strong>${esc(automation.name || 'Scheduled task')}</strong></span>
                <span class="scheduled-row-what" title="${esc(whatItDoes(automation, 400))}">${esc(whatItDoes(automation))}</span>
                ${ident ? `<span class="scheduled-row-ident">${esc(ident)}</span>` : ''}
            </div>
            <span class="scheduled-cell" role="cell" data-label="Schedule">${esc(automationScheduleLabel(automation))}</span>
            <span class="scheduled-cell scheduled-cell-next" role="cell" data-label="Next run" title="${esc(nextCell.title)}"><span class="scheduled-pill ${group}">${statusLabel}</span><span class="scheduled-next-when">${esc(nextCell.text)}</span></span>
            <span class="scheduled-cell" role="cell" data-label="Last run" title="${esc(lastCell.title)}">${esc(lastCell.text)}</span>
            <span class="scheduled-cell" role="cell" data-label="Project">${esc(automationProject(automation))}</span>
            <span class="scheduled-cell actions" role="cell" data-label="Actions">${rowActionsHtml(automation, group)}</span>
        </div>`;
    }

    function renderList() {
        const list = el('scheduled-list');
        const order = { active: 0, paused: 1, completed: 2 };
        const automations = visibleAutomations().slice().sort((a, b) => {
            const ga = order[automationGroup(a)] ?? 3;
            const gb = order[automationGroup(b)] ?? 3;
            if (ga !== gb) return ga - gb;
            const na = a.next_run_at ? new Date(a.next_run_at).getTime() : Infinity;
            const nb = b.next_run_at ? new Date(b.next_run_at).getTime() : Infinity;
            return na - nb;
        });
        const head = '<div class="scheduled-row scheduled-row-head" role="row">'
            + '<span role="columnheader">What it does</span>'
            + '<span role="columnheader">Schedule</span>'
            + '<span role="columnheader">Next run</span>'
            + '<span role="columnheader">Last run</span>'
            + '<span role="columnheader">Project</span>'
            + '<span role="columnheader" class="actions"></span>'
            + '</div>';
        list.innerHTML = automations.length
            ? `<div class="scheduled-table" role="table">${head}${automations.map(scheduledRowHtml).join('')}</div>`
            : (state.statusFilter !== 'all' && typeAutomations().length
                ? `<div class="development-empty">No ${esc((STATUS_LABELS[state.statusFilter] || state.statusFilter).toLowerCase())} automations.</div>`
                : '<div class="development-empty">No scheduled actions yet. Ask Laya in a thread to create one.</div>');
        list.querySelectorAll('[data-row-action]').forEach((button) =>
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                const automationId = button.dataset.automationId;
                const action = button.dataset.rowAction;
                if (action === 'delete') deleteAutomation(automationId);
                else if (action === 'run') runAutomation(automationId);
                else if (action === 'pause' || action === 'resume') toggleAutomation(automationId, action);
                else if (action === 'history') openHistoryModal(automationId);
                else if (action === 'edit') openAutomationEditor(automationId);
                else if (action === 'settings') openRouteModal(automationId);
                else if (action === 'schedule') openScheduleModal(automationId);
            })
        );
        list.querySelectorAll('[data-scheduled-row]').forEach((row) => {
            row.addEventListener('dblclick', (event) => {
                if (!event.target.closest('button, a, input, select')) openAutomationEditor(row.dataset.scheduledRow);
            });
            row.addEventListener('contextmenu', (event) => {
                if (event.target.closest('button, a, input, select')) return;
                event.preventDefault();
                openScheduledRowMenu(row.dataset.scheduledRow, event.clientX, event.clientY);
            });
        });
        ensureRouteNames();
    }

    // automodel: readable harness/model column + SAST times.
    const SAST = 'Africa/Johannesburg';
    function whenTitle(value) {
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return '';
        return `${d.toLocaleString('en-ZA', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: SAST })} SAST`;
    }
    function relativeWhen(value) {
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return '—';
        const diff = d.getTime() - Date.now();
        const abs = Math.abs(diff);
        const units = [['day', 86400000], ['hour', 3600000], ['minute', 60000]];
        const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
        for (const [unit, ms] of units) {
            if (abs >= ms) return rtf.format(Math.round(diff / ms), unit);
        }
        return diff >= 0 ? 'in under a minute' : 'just now';
    }
    const routeNames = { backends: {}, models: {}, defaults: null, loading: false };
    function backendLabel(id) {
        const b = routeNames.backends[String(id || '').toLowerCase()];
        return (b && b.name) || harnessLabel(id);
    }
    function modelText(backend, model) {
        if (!model || model === 'auto') return 'Auto';
        const row = (routeNames.models[String(backend || '').toLowerCase()] || []).find((m) => m && m.id === model);
        return row ? modelLabel(row) : shortModelName(model) || model;
    }
    function layerText(backend, model) {
        return `${backendLabel(backend)} · ${modelText(backend, model)}`;
    }
    function routeSummary(automation) {
        const c = automation.action_config || {};
        const mode = String(c.cli_route_mode || '');
        if (mode === 'custom' && c.cli_backend) {
            return { text: layerText(c.cli_backend, c.cli_model), title: 'Set on this schedule', inherit: false };
        }
        if (mode !== 'inherit' && c.backend) {
            return { text: layerText(c.backend, c.model), title: 'Imported with this schedule', inherit: false };
        }
        const g = routeNames.defaults || {};
        const inner = g.backend ? layerText(g.backend, g.model) : 'Complexity routing';
        return { text: `Default: ${inner}`, title: 'Inherits the global automation default', inherit: true };
    }
    async function ensureRouteNames() {
        if (routeNames.loading || routeNames.defaults) return;
        routeNames.loading = true;
        try {
            const [backends, defaults] = await Promise.all([cliBackends(), globalDefault()]);
            backends.forEach((b) => { routeNames.backends[String(b.id || '').toLowerCase()] = b; });
            routeNames.defaults = defaults && typeof defaults === 'object' && 'backend' in defaults ? defaults : { backend: '' };
            const wanted = new Set();
            (context.automations || []).forEach((a) => {
                const c = a.action_config || {};
                if (c.cli_backend) wanted.add(String(c.cli_backend).toLowerCase());
                if (c.backend) wanted.add(String(c.backend).toLowerCase());
            });
            if (routeNames.defaults.backend) wanted.add(routeNames.defaults.backend);
            await Promise.all([...wanted].map(async (b) => { routeNames.models[b] = (await cliModels(b)).models || []; }));
        } finally {
            routeNames.loading = false;
        }
        if (['automations', 'automation_rules'].includes(context.workspaceMode)) {
            el('scheduled-list')?.querySelectorAll('[data-row-action="model"]').forEach((button) => {
                const automation = (context.automations || []).find((a) => String(a.id) === String(button.dataset.automationId));
                if (!automation) return;
                const route = routeSummary(automation);
                button.textContent = route.text;
                button.title = `${route.title} — click to change`;
                button.classList.toggle('inherit', route.inherit);
            });
        }
    }

    // ---------- calendar ----------
    // ---------- calendar 2026-10-22: time grid (Day/Week), drag to reschedule, ignore ----------
    const HOUR_PX = 64;
    function occurrenceKey(dayKey, time) {
        return `${dayKey}T${time}`;
    }
    function isIgnored(automation, dayKey, time) {
        return (automation.schedule?.ignored_occurrences || []).includes(occurrenceKey(dayKey, time));
    }
    function isTimedKind(automation) {
        return ['daily', 'weekly', 'monthly', 'yearly', 'once'].includes(String(automation.schedule?.kind || 'daily'));
    }
    function timeGridHtml(days) {
        const todayKey = dateKey(new Date());
        const cols = days.length;
        const head = days
            .map((date) => `<div class="cal-tg-dayhead ${dayCellClass(date, eventsOn(date), null)}" data-cal-day="${dateKey(date)}"><span>${date.toLocaleDateString([], { weekday: 'short' })}</span><strong>${date.getDate()}</strong></div>`)
            .join('');
        let allDay = '';
        let columns = '';
        days.forEach((date) => {
            const key = dateKey(date);
            const events = eventsOn(date);
            state.calendarDays[key] = events;
            const untimed = events.filter((e) => !isTimedKind(e.automation));
            const timed = events.filter((e) => isTimedKind(e.automation));
            const recurringKey = `${key}:recurring`;
            state.calendarClusters[recurringKey] = untimed;
            allDay += `<div class="cal-tg-allday-cell" data-cal-day="${key}">${untimed.length ? `<button type="button" class="cal-recurring-group" data-cal-cluster="${recurringKey}" aria-label="${untimed.length} recurring automations on ${key}">${untimed.length} recurring</button>` : ''}</div>`;
            const blocks = groupCalendarEvents(timed, HOUR_PX, 44)
                .map((group, index) => {
                    const style = `top:${group.top}px;height:44px;left:2px;width:calc(100% - 4px)`;
                    if (group.events.length === 1) return eventHtml(group.events[0], false, key, style);
                    const groupKey = `${key}:${index}`;
                    state.calendarClusters[groupKey] = group.events;
                    const first = group.events[0].time;
                    const last = group.events[group.events.length - 1].time;
                    const range = first === last ? first : `${first} - ${last}`;
                    return `<button type="button" class="cal-event-group" style="${style}" data-cal-cluster="${groupKey}" aria-label="${group.events.length} automations, ${range}, ${key}"><time>${esc(range)}</time><strong>${group.events.length} automations</strong></button>`;
                })
                .join('');
            const now = key === todayKey ? `<div class="cal-now-line" data-cal-now style="top:${topForMinutes(new Date().getHours() * 60 + new Date().getMinutes(), HOUR_PX)}px"><span></span></div>` : '';
            columns += `<div class="cal-tg-col${key === todayKey ? ' today' : ''}" data-cal-day="${key}" data-cal-drop="time">${blocks}${now}</div>`;
        });
        const labels = Array.from({ length: 24 }, (_, h) => `<span class="cal-tg-time" style="top:${h * HOUR_PX}px">${h === 0 ? '' : `${String(h).padStart(2, '0')}:00`}</span>`).join('');
        const lines = Array.from({ length: 48 }, (_, i) => `<div class="cal-tg-line${i % 2 ? ' half' : ''}" style="top:${(i * HOUR_PX) / 2}px"></div>`).join('');
        return `<div class="cal-timegrid ${cols === 1 ? 'day' : 'week'}" style="--cal-cols:${cols};--hour-px:${HOUR_PX}px">
            <div class="cal-tg-head"><div class="cal-tg-gutter"></div>${head}</div>
            <div class="cal-tg-allday"><div class="cal-tg-gutter">Recurring</div>${allDay}</div>
            <div class="cal-tg-scroll" data-cal-scroll><div class="cal-tg-body" style="height:${24 * HOUR_PX}px">
                <div class="cal-tg-times">${labels}</div>
                <div class="cal-tg-lines">${lines}</div>
                <div class="cal-tg-cols">${columns}</div>
            </div></div>
        </div>`;
    }
    function updateNowLine() {
        const line = document.querySelector('[data-cal-now]');
        if (!line) return;
        const now = new Date();
        if (line.closest('[data-cal-day]')?.dataset.calDay !== dateKey(now)) { renderCalendar(); return; }
        line.style.top = `${topForMinutes(now.getHours() * 60 + now.getMinutes(), HOUR_PX)}px`;
    }
    function ensureNowTimer() {
        if (state.nowTimer) return;
        state.nowTimer = window.setInterval(() => {
            if (state.view === 'calendar' && state.calendarMode !== 'month') updateNowLine();
        }, 60000);
    }

    function showUndoToast(message, onUndo) {
        document.getElementById('scheduled-undo-toast')?.remove();
        const toast = document.createElement('div');
        toast.id = 'scheduled-undo-toast';
        toast.className = 'scheduled-undo-toast';
        toast.setAttribute('role', 'status');
        toast.innerHTML = `<span>${esc(message)}</span><button type="button" data-undo>Undo</button>`;
        document.body.appendChild(toast);
        const timer = window.setTimeout(() => toast.remove(), 8000);
        toast.querySelector('[data-undo]').addEventListener('click', async () => {
            window.clearTimeout(timer);
            toast.remove();
            await onUndo();
        });
    }
    async function applySchedule(automation, schedule) {
        const result = await saveAutomationPatch(automation, { schedule });
        if (!result?.automation) automation.schedule = schedule;
        return result;
    }
    async function dropEvent(automationId, fromDay, fromTime, toDay, toTime) {
        const automation = (context.automations || []).find((item) => String(item.id) === String(automationId));
        if (!automation || (fromDay === toDay && fromTime === toTime)) { renderCalendar(); return; }
        const before = JSON.parse(JSON.stringify(automation.schedule || {}));
        const next = shiftSchedule(before, fromDay, fromTime, toDay, toTime);
        if (!next) { actions.toast('This schedule can’t be moved by dragging.', 'error'); renderCalendar(); return; }
        try {
            await applySchedule(automation, next);
            renderCalendar();
            showUndoToast(`Moved “${automation.name || 'automation'}” to ${automationScheduleLabel(automation)}.`, async () => {
                try { await applySchedule(automation, before); renderCalendar(); actions.toast('Move undone.'); }
                catch (error) { actions.toast(error.message || 'Could not undo.', 'error'); }
            });
        } catch (error) {
            actions.toast(error.message || 'Could not move the automation.', 'error');
            renderCalendar();
        }
    }
    function bindDrag(button) {
        const automationId = button.dataset.scheduledCalendarEvent;
        const fromDay = button.dataset.calOccDay;
        const fromTime = button.dataset.calOccTime;
        if (!fromDay || !fromTime) return;
        button.addEventListener('pointerdown', (down) => {
            if (down.button !== 0) return;
            const startX = down.clientX;
            const startY = down.clientY;
            const grabOffset = down.clientY - button.getBoundingClientRect().top;
            let dragging = false;
            let target = null;
            const move = (event) => {
                if (!dragging && Math.hypot(event.clientX - startX, event.clientY - startY) < 5) return;
                if (!dragging) { dragging = true; state.dragging = true; button.classList.add('dragging'); button.setPointerCapture?.(down.pointerId); }
                button.style.pointerEvents = 'none';
                const hit = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-cal-day]');
                button.style.pointerEvents = '';
                if (!hit) return;
                if (hit.dataset.calDrop === 'time') {
                    const rect = hit.getBoundingClientRect();
                    const minutes = snapMinutes(((event.clientY - grabOffset - rect.top) * 60) / HOUR_PX);
                    target = { day: hit.dataset.calDay, time: minutesToTime(minutes) };
                    if (button.parentElement !== hit) hit.appendChild(button);
                    button.style.top = `${topForMinutes(minutes, HOUR_PX)}px`;
                    button.style.left = '2px';
                    button.style.width = 'calc(100% - 4px)';
                    const label = button.querySelector('time');
                    if (label) label.textContent = target.time;
                } else if (hit.matches('.scheduled-calendar-day')) {
                    target = { day: hit.dataset.calDay, time: fromTime };
                    const box = hit.querySelector('.cal-entries');
                    if (box && button.parentElement !== box) box.appendChild(button);
                }
            };
            const up = () => {
                window.removeEventListener('pointermove', move);
                window.removeEventListener('pointerup', up);
                button.classList.remove('dragging');
                state.dragging = false;
                if (!dragging) return;
                state.renderPending = false;
                state.suppressClick = true;
                window.setTimeout(() => { state.suppressClick = false; }, 0);
                if (target) dropEvent(automationId, fromDay, fromTime, target.day, target.time);
                else renderCalendar();
            };
            window.addEventListener('pointermove', move);
            window.addEventListener('pointerup', up);
        });
    }
    function closeCalendarMenu() {
        document.getElementById('scheduled-cal-menu')?.remove();
    }
    function openCalendarMenu(button, x, y) {
        closeCalendarMenu();
        const automation = (context.automations || []).find((item) => String(item.id) === String(button.dataset.scheduledCalendarEvent));
        const day = button.dataset.calOccDay;
        const time = button.dataset.calOccTime;
        if (!automation) return false;
        const canIgnore = day && time && ['daily', 'weekly', 'monthly', 'yearly'].includes(String(automation.schedule?.kind || ''));
        const ignored = isIgnored(automation, day, time);
        const menu = document.createElement('div');
        menu.id = 'scheduled-cal-menu';
        menu.className = 'scheduled-cal-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = '<button type="button" role="menuitem" data-cal-edit>Edit automation</button>'
            + (canIgnore ? `<button type="button" role="menuitem" data-cal-ignore="${ignored ? '0' : '1'}">${ignored ? 'Un-ignore' : 'Ignore'}</button>` : '');
        document.body.appendChild(menu);
        menu.style.left = `${Math.min(x, window.innerWidth - menu.offsetWidth - 8)}px`;
        menu.style.top = `${Math.min(y, window.innerHeight - menu.offsetHeight - 8)}px`;
        menu.querySelector('[data-cal-edit]').addEventListener('click', () => openAutomationEditor(automation.id));
        menu.querySelector('[data-cal-ignore]')?.addEventListener('click', async (event) => {
            const ignore = event.currentTarget.dataset.calIgnore === '1';
            closeCalendarMenu();
            const occurrence = occurrenceKey(day, time);
            try {
                const result = await actions.api(`/automations/${encodeURIComponent(automation.id)}/occurrences/ignore`, { method: 'POST', body: { occurrence, ignored: ignore } });
                if (result?.automation) {
                    Object.assign(automation, result.automation);
                } else {
                    const list = new Set(automation.schedule?.ignored_occurrences || []);
                    if (ignore) list.add(occurrence); else list.delete(occurrence);
                    automation.schedule = { ...(automation.schedule || {}), ignored_occurrences: [...list].sort() };
                }
                renderCalendar();
                actions.toast(ignore ? `Skipping the ${time} run on ${day}.` : `The ${time} run on ${day} will run again.`);
            } catch (error) {
                actions.toast(error.message || 'Could not update that occurrence.', 'error');
            }
        });
        return true;
    }
    // Calendar views put Status first, then the date navigation; List keeps its own layout.
    function placeStatusFilter(calendarView) {
        const filter = document.querySelector('.scheduled-status-filter');
        const toolbar = el('scheduled-calendar-toolbar');
        const controls = el('scheduled-view-toggle');
        if (!filter || !toolbar || !controls) return;
        if (calendarView) {
            if (toolbar.firstElementChild !== filter) toolbar.insertBefore(filter, toolbar.firstElementChild);
        } else if (filter.parentElement !== controls) {
            controls.insertBefore(filter, controls.firstElementChild);
        }
    }

    function startOfDay(value) {
        const date = new Date(value);
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    }
    function startOfWeek(value) {
        const date = startOfDay(value);
        date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
        return date;
    }
    function addDays(value, amount) {
        const date = startOfDay(value);
        date.setDate(date.getDate() + amount);
        return date;
    }
    function dateKey(value) {
        const date = startOfDay(value);
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }

    // Returns the occurrences ({time, label}) of an automation on a given day.
    function occurrencesOn(automation, day) {
        if (isMessageTriggered(automation)) return [];
        const schedule = automation.schedule || {};
        const kind = String(schedule.kind || 'daily');
        const date = startOfDay(day);
        if (automationGroup(automation) === 'completed' && kind !== 'once') return [];
        const created = automation.created_at ? startOfDay(automation.created_at) : null;
        if (created && date < created && kind !== 'once') return [];
        const atTimes = () => (scheduleTimes(schedule).length ? scheduleTimes(schedule) : ['09:00']).map((time) => ({ time, label: time }));
        if (kind === 'once') {
            if (!schedule.run_at) return [];
            const at = new Date(schedule.run_at);
            if (Number.isNaN(at.getTime()) || dateKey(at) !== dateKey(date)) return [];
            const time = at.toTimeString().slice(0, 5);
            return [{ time, label: time }];
        }
        if (kind === 'weekly') return scheduleDays(schedule).includes(date.getDay()) ? atTimes() : [];
        if (kind === 'monthly') return scheduleDays(schedule).includes(date.getDate()) ? atTimes() : [];
        if (kind === 'yearly') return date.getMonth() + 1 === Number(schedule.month || 1) && scheduleDays(schedule).includes(date.getDate()) ? atTimes() : [];
        if (kind === 'hourly') return [{ time: '00:00', label: 'Hourly' }];
        if (kind === 'interval') return [{ time: '00:00', label: `Every ${schedule.interval || 30} ${String(schedule.interval_unit || 'minutes').replace(/s$/, '')}s` }];
        if (kind === 'cron') return [{ time: '', label: automationScheduleLabel(automation) }];
        return atTimes();
    }

    function eventsOn(day) {
        const events = [];
        visibleAutomations().forEach((automation) =>
            occurrencesOn(automation, day).forEach((occurrence) => events.push({ automation, ...occurrence }))
        );
        return events.sort((a, b) => `${a.time} ${a.automation.name || ''}`.localeCompare(`${b.time} ${b.automation.name || ''}`));
    }

    function eventHtml(event, compact, dayKey = '', style = '') {
        const automation = event.automation;
        const paused = automationGroup(automation) !== 'active';
        const selected = String(state.selectedAutomationId) === String(automation.id);
        const name = automation.name || 'Scheduled task';
        const timed = dayKey && /^\d{2}:\d{2}$/.test(String(event.time || '')) && isTimedKind(automation);
        const ignored = timed && isIgnored(automation, dayKey, event.time);
        const occ = timed ? ` data-cal-occ-day="${esc(dayKey)}" data-cal-occ-time="${esc(event.time)}" data-cal-draggable="1"` : '';
        const tip = `${event.label} · ${name}${ignored ? ' (ignored — this run is skipped)' : ''} — ${whatItDoes(automation, 200)}`;
        const showTime = !(compact && dayKey && !isTimedKind(automation));
        return `<button type="button" class="scheduled-calendar-event${paused ? ' paused' : ''}${compact ? ' compact' : ''}${selected ? ' selected' : ''}${ignored ? ' ignored' : ''}${style ? ' cal-tg-event' : ''}"${style ? ` style="${style}"` : ''}${occ} data-scheduled-calendar-event="${esc(automation.id)}" title="${esc(tip)}">${showTime ? `<time>${esc(event.label)}</time>` : ''}<span class="cal-title"><span class="cal-title-inner">${esc(name)}</span></span></button>`;
    }

    function dayCellClass(date, events, anchorMonth) {
        const key = dateKey(date);
        const todayKey = dateKey(new Date());
        const classes = [];
        if (key === todayKey) classes.push('today');
        else if (startOfDay(date) < startOfDay(new Date()) || (anchorMonth !== null && date.getMonth() !== anchorMonth) || !events.length) classes.push('muted');
        return classes.join(' ');
    }

    function renderCalendar() {
        // calendar: the 6s background refresh must not rebuild the grid under an active drag.
        if (state.dragging) { state.renderPending = true; return; }
        const calendar = el('scheduled-calendar');
        const title = el('scheduled-calendar-title');
        if (!calendar || !title) return;
        closeDayPopover();
        const anchor = startOfDay(state.calendarAnchor || new Date());
        let html = '';
        state.calendarDays = {};
        state.calendarClusters = {};
        if (state.calendarMode === 'month') {
            title.textContent = anchor.toLocaleDateString([], { month: 'long', year: 'numeric' });
            const start = startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1));
            html = `<div class="scheduled-calendar-weekdays">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => `<span>${d}</span>`).join('')}</div><div class="scheduled-calendar-month cal-fixed">`;
            for (let index = 0; index < 42; index += 1) {
                const date = addDays(start, index);
                const events = eventsOn(date);
                state.calendarDays[dateKey(date)] = events;
                html += `<section class="scheduled-calendar-day ${dayCellClass(date, events, anchor.getMonth())}" data-cal-day="${dateKey(date)}"><header><time datetime="${dateKey(date)}">${date.getDate()}</time></header><div class="cal-entries">${events
                    .map((event) => eventHtml(event, true, dateKey(date)))
                    .join('')}</div></section>`;
            }
            html += '</div>';
        } else if (state.calendarMode === 'day') {
            title.textContent = anchor.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
            html = timeGridHtml([anchor]);
        } else {
            const start = startOfWeek(anchor);
            const days = Array.from({ length: 7 }, (_, index) => addDays(start, index));
            title.textContent = `${start.toLocaleDateString([], { day: 'numeric', month: 'short' })} to ${days[6].toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}`;
            html = timeGridHtml(days);
        }
        const previousScroll = calendar.querySelector('[data-cal-scroll]')?.scrollTop;
        calendar.innerHTML = html;
        calendar.querySelectorAll('[data-cal-cluster]').forEach((button) => button.addEventListener('click', () => {
            const group = state.calendarClusters[button.dataset.calCluster] || [];
            openDayPopover(button.closest('[data-cal-day]').dataset.calDay, button, group);
        }));
        calendar.classList.toggle('cal-has-timegrid', state.calendarMode !== 'month');
        const scroller = calendar.querySelector('[data-cal-scroll]');
        if (scroller) scroller.scrollTop = previousScroll ?? Math.max(0, (new Date().getHours() - 2) * HOUR_PX);
        ensureNowTimer();
        closeCalendarMenu();
        const triggers = visibleAutomations().filter(isMessageTriggered);
        el('scheduled-message-triggers').innerHTML = triggers.length
            ? `<span>On incoming message:</span>${triggers.map((automation) => eventHtml({ automation, label: automationScheduleLabel(automation).replace('On incoming ', '') }, true)).join('')}`
            : '';
        bindCalendarEvents(el('scheduled-calendar-wrap'));
        fitCalendarCells(calendar);
        applyMarquee(el('scheduled-calendar-wrap'));
        document.querySelectorAll('[data-scheduled-calendar-mode]').forEach((button) => {
            const active = button.dataset.scheduledCalendarMode === state.calendarMode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', String(active));
        });
    }

    function bindCalendarEvents(root) {
        root?.querySelectorAll('[data-scheduled-calendar-event]').forEach((button) => {
            if (button.dataset.bound) return;
            button.dataset.bound = '1';
            bindCalendarOpen(button);
            if (button.dataset.calDraggable) bindDrag(button);
            button.addEventListener('contextmenu', (event) => {
                if (openCalendarMenu(button, event.clientX, event.clientY)) event.preventDefault();
            });
        });
    }

    function bindCalendarOpen(button, fromPopover = false) {
        let clickTimer;
        button.addEventListener('click', (event) => {
            if (state.suppressClick) return;
            window.clearTimeout(clickTimer);
            const open = () => {
                if (fromPopover) closeDayPopover();
                openHistoryModal(button.dataset.scheduledCalendarEvent);
            };
            if (event.detail === 0) open();
            else if (event.detail === 1) clickTimer = window.setTimeout(open, 300);
        });
        button.addEventListener('dblclick', () => {
            window.clearTimeout(clickTimer);
            openAutomationEditor(button.dataset.scheduledCalendarEvent);
        });
    }

    // Show as many entries as fit in each fixed-size cell, then "+N more".
    function fitCalendarCells(calendar) {
        calendar.querySelectorAll('.cal-entries').forEach((box) => {
            const entries = Array.from(box.children);
            if (!entries.length) return;
            const limit = box.clientHeight;
            if (box.scrollHeight <= limit + 1) return;
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'cal-more';
            box.appendChild(more);
            let visible = entries.length;
            const fits = () => box.scrollHeight <= limit + 1;
            more.textContent = '+0 more';
            while (visible > 0 && !fits()) {
                visible -= 1;
                entries[visible].hidden = true;
                more.textContent = `+${entries.length - visible} more`;
            }
            const dayKey = box.closest('[data-cal-day]')?.dataset.calDay || '';
            more.addEventListener('click', (event) => {
                event.stopPropagation();
                openDayPopover(dayKey, more);
            });
        });
    }

    // Keep calendar labels still; expanded groups show the full wrapped names.
    function applyMarquee(root) {
        root?.querySelectorAll('.cal-title').forEach((outer) => outer.classList.remove('marquee'));
    }

    function closeDayPopover() {
        const pop = document.getElementById('scheduled-day-popover');
        const returnFocus = pop?.returnFocus;
        pop?.remove();
        if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    }

    function openDayPopover(dayKey, anchorEl, group = null) {
        closeDayPopover();
        const events = group || (state.calendarDays || {})[dayKey] || [];
        const pop = document.createElement('div');
        pop.id = 'scheduled-day-popover';
        pop.className = 'scheduled-day-popover';
        pop.setAttribute('role', 'dialog');
        pop.returnFocus = anchorEl;
        const label = new Date(`${dayKey}T00:00:00`).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
        pop.setAttribute('aria-label', `Automations for ${label}`);
        pop.innerHTML = `<header><strong>${esc(label)}</strong><button type="button" aria-label="Close" data-pop-close>×</button></header><div>${events.map((event) => eventHtml(event, true)).join('')}</div>`;
        document.body.appendChild(pop);
        const rect = anchorEl.getBoundingClientRect();
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - pop.offsetWidth - 8));
        const top = rect.bottom + 6 + pop.offsetHeight < window.innerHeight ? rect.bottom + 6 : Math.max(8, rect.top - pop.offsetHeight - 6);
        pop.style.left = `${left}px`;
        pop.style.top = `${top}px`;
        pop.querySelector('[data-pop-close]').addEventListener('click', closeDayPopover);
        bindCalendarEvents(pop);
        applyMarquee(pop);
        pop.querySelector('[data-pop-close]').focus({ preventScroll: true });
    }

    // ---------- page ----------
    function renderScheduledWorkspace() {
        const list = el('scheduled-list');
        if (!list) return;
        const rules = context.workspaceMode === 'automation_rules';
        if (state.selectedAutomationId && !visibleAutomations().some((item) => String(item.id) === String(state.selectedAutomationId))) {
            state.selectedAutomationId = '';
        }
        const calendarView = state.view === 'calendar';
        placeStatusFilter(calendarView);
        list.classList.toggle('hidden', calendarView);
        el('scheduled-calendar-wrap')?.classList.toggle('hidden', !calendarView);
        const toolbar = el('scheduled-calendar-toolbar');
        if (toolbar) {
            toolbar.classList.remove('hidden');
            toolbar.classList.toggle('list-mode', !calendarView);
            const toggleButton = el('scheduled-view-toggle');
            if (toggleButton && toggleButton.parentElement !== toolbar) toolbar.appendChild(toggleButton);
        }
        document.querySelectorAll('[data-scheduled-view]').forEach((button) => {
            const active = button.dataset.scheduledView === state.view;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
        renderStatusFilter();
        if (calendarView) renderCalendar();
        else renderList();
        renderScheduledDetail();
    }

    const ICONS = {
        edit: '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12.5 3.5 4 4M3 17l4.5-1L17 6.5 13.5 3 4 12.5 3 17z"/></svg>',
        pause: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="4" width="3.2" height="12" rx="1"/><rect x="11.8" y="4" width="3.2" height="12" rx="1"/></svg>',
        resume: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 2 4 11.5h5L8 18l8-10h-5l1-6z"/></svg>',
        // autocog: Run now and Resume swapped icons (run = play triangle, resume = bolt); history = list, schedule = clock, settings = cog.
        run: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4.5v11a.8.8 0 0 0 1.2.7l9-5.5a.8.8 0 0 0 0-1.4l-9-5.5A.8.8 0 0 0 6 4.5z"/></svg>',
        history: '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M7.5 5.5H16M7.5 10H16M7.5 14.5H16"/><circle cx="4.2" cy="5.5" r=".6" fill="currentColor"/><circle cx="4.2" cy="10" r=".6" fill="currentColor"/><circle cx="4.2" cy="14.5" r=".6" fill="currentColor"/></svg>',
        schedule: '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="10" cy="10" r="7"/><path d="M10 6v4.2l2.8 1.8"/></svg>',
        settings: '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/><circle cx="10" cy="10" r="5.3"/></svg>',
        delete: '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 6h12M8 6V4h4v2M6 6l.8 10h6.4L14 6"/></svg>'
    };

    function rowActionsHtml(automation, group) {
        const id = esc(automation.id);
        const name = esc(automation.name || 'scheduled task');
        const button = (action, label, extra = '') =>
            `<button type="button" class="scheduled-icon-button ${action}${extra}" data-row-action="${action}" data-automation-id="${id}" title="${label}" aria-label="${label} ${name}">${ICONS[action]}</button>`;
        const toggle = group === 'completed' ? '' : group === 'paused' ? button('resume', 'Resume') : button('pause', 'Pause');
        return `<span class="scheduled-row-icons">${button('edit', 'Edit automation')}${button('run', 'Run now')}${toggle}${button('history', 'Run history')}${isMessageTriggered(automation) ? '' : button('schedule', 'Edit schedule')}${button('settings', 'Harness and model')}${button('delete', 'Delete', ' danger')}</span>`;
    }

    async function loadRuns(automationId, force) {
        const key = String(automationId);
        if (!force && state.automationRuns[key]) return state.automationRuns[key];
        try {
            const data = await actions.api(`/automations/${encodeURIComponent(key)}/runs`);
            state.automationRuns[key] = Array.isArray(data) ? data : data.runs || [];
        } catch (_) {
            state.automationRuns[key] = [];
        }
        return state.automationRuns[key];
    }

    function closeHistoryModal() {
        document.getElementById('scheduled-history-modal')?.remove();
        state.historyFor = '';
    }

    function renderHistoryModal(automation, runs) {
        const modal = document.getElementById('scheduled-history-modal');
        if (!modal) return;
        const shown = runs ? (state.historyExpanded ? runs.slice(0, 50) : runs.slice(0, 5)) : [];
        modal.querySelector('.scheduled-history-body').innerHTML = runs === null
            ? '<p class="scheduled-history-empty">Loading runs…</p>'
            : runs.length
              ? shown
                    .map(
                        (run) =>
                            `<div class="scheduled-run"><div><strong>${esc(actions.statusLabel(run.status))}</strong><span>${esc(actions.formatDateTime(run.started_at || run.created_at))}</span></div>${run.summary ? `<p class="scheduled-run-summary">${esc(run.summary)}</p><button type="button" class="scheduled-run-expand" data-run-expand aria-expanded="false">Show full</button>` : ''}</div>`
                    )
                    .join('') +
                (runs.length > 5 ? `<button type="button" class="scheduled-runs-more" data-runs-more>${state.historyExpanded ? 'Show fewer' : `Show more (${Math.min(runs.length, 50) - 5})`}</button>` : '')
              : '<p class="scheduled-history-empty">No runs recorded yet.</p>';
        modal.querySelectorAll('[data-run-expand]').forEach((button) => {
            const summary = button.previousElementSibling;
            if (summary && summary.scrollHeight <= summary.clientHeight + 2) button.hidden = true;
            button.addEventListener('click', () => {
                const open = summary.classList.toggle('expanded');
                button.textContent = open ? 'Show less' : 'Show full';
                button.setAttribute('aria-expanded', String(open));
            });
        });
        modal.querySelector('[data-runs-more]')?.addEventListener('click', () => {
            state.historyExpanded = !state.historyExpanded;
            renderHistoryModal(automation, runs);
        });
    }

    async function openHistoryModal(automationId) {
        const automation = (context.automations || []).find((item) => String(item.id) === String(automationId));
        if (!automation) return;
        closeHistoryModal();
        closeDayPopover();
        state.historyFor = String(automation.id);
        state.historyExpanded = false;
        const modal = document.createElement('div');
        modal.id = 'scheduled-history-modal';
        modal.className = 'scheduled-history-backdrop';
        modal.innerHTML = `<div class="scheduled-history-dialog" role="dialog" aria-modal="true" aria-labelledby="scheduled-history-title">
            <header><div><h2 id="scheduled-history-title">${esc(automation.name || 'Scheduled task')}</h2><p>${esc(automationScheduleLabel(automation))} · ${esc(automationProject(automation))}</p></div><button type="button" class="scheduled-history-close" aria-label="Close run history" title="Close">×</button></header>
            <h3>Previous runs</h3>
            <div class="scheduled-history-body"></div>
        </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => {
            if (event.target === modal) closeHistoryModal();
        });
        modal.querySelector('.scheduled-history-close').addEventListener('click', closeHistoryModal);
        modal.querySelector('.scheduled-history-close').focus();
        renderHistoryModal(automation, null);
        const runs = await loadRuns(automation.id);
        if (state.historyFor === String(automation.id)) renderHistoryModal(automation, runs);
    }

    // autocog 2026-10-22: two separate edit modals (cog = harness/model, clock = schedule),
    // built on the same backdrop/dialog pattern as the run-history modal.
    function closeEditModal() {
        document.getElementById('scheduled-edit-modal')?.remove();
    }
    function openEditDialog(automation, { title, subtitle, body, saveLabel = 'Save' }) {
        closeHistoryModal();
        closeEditModal();
        closeDayPopover();
        const modal = document.createElement('div');
        modal.id = 'scheduled-edit-modal';
        modal.className = 'scheduled-history-backdrop';
        modal.innerHTML = `<div class="scheduled-history-dialog scheduled-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="scheduled-edit-title">
            <header><div><h2 id="scheduled-edit-title">${esc(title)}</h2><p>${esc(automation.name || 'Scheduled task')} · ${esc(subtitle)}</p></div><button type="button" class="scheduled-history-close" aria-label="Close" title="Close">×</button></header>
            ${body}
            <p class="scheduled-model-hint scheduled-edit-error" data-edit-error role="alert"></p>
            <footer class="scheduled-edit-actions"><button type="button" class="scheduled-edit-cancel" data-edit-cancel>Cancel</button><button type="button" class="scheduled-model-save" data-edit-save>${esc(saveLabel)}</button></footer>
        </div>`;
        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => { if (event.target === modal) closeEditModal(); });
        modal.querySelector('.scheduled-history-close').addEventListener('click', closeEditModal);
        modal.querySelector('[data-edit-cancel]').addEventListener('click', closeEditModal);
        return modal;
    }
    async function saveAutomationPatch(automation, body) {
        const result = await actions.api(`/automations/${encodeURIComponent(automation.id)}`, { method: 'PUT', body });
        if (result?.automation) {
            const list = context.automations || [];
            const index = list.findIndex((item) => String(item.id) === String(automation.id));
            if (index >= 0) list[index] = result.automation;
            Object.assign(automation, result.automation);
        }
        return result;
    }

    function openRouteModal(automationId) {
        const automation = (context.automations || []).find((item) => String(item.id) === String(automationId));
        if (!automation) return;
        const modal = openEditDialog(automation, {
            title: 'Harness and model',
            subtitle: routeSummary(automation).text,
            body: `<section class="scheduled-model-route" aria-label="Harness and model">
                <div class="scheduled-model-row">
                    <label class="scheduled-edit-field"><span>Harness</span><select data-automation-cli-backend aria-label="Harness"><option value="">Inherit default</option></select></label>
                    <label class="scheduled-edit-field"><span>Provider</span><select data-automation-cli-provider aria-label="Provider" disabled><option value="">Any provider</option></select></label>
                    <label class="scheduled-edit-field"><span>Model</span><select data-automation-cli-model aria-label="Model" disabled><option value="">—</option></select></label>
                </div>
                <p class="scheduled-model-hint" data-automation-cli-hint></p>
                <p class="scheduled-edit-note">Applies to future runs of this automation. Past runs in its history keep the harness and model they ran with.</p>
            </section>`
        });
        bindModelRoute(modal, automation, {
            save: modal.querySelector('[data-edit-save]'),
            onSaved: () => { closeEditModal(); renderScheduledWorkspace(); }
        });
    }

    const CAL_WEEKDAYS = [[1, 'Mon'], [2, 'Tue'], [3, 'Wed'], [4, 'Thu'], [5, 'Fri'], [6, 'Sat'], [0, 'Sun']];
    function scheduleFormState(schedule) {
        const s = schedule || {};
        const kind = ['daily', 'weekly', 'monthly', 'yearly'].includes(s.kind) ? s.kind : 'daily';
        const days = scheduleDays(s);
        return {
            kind,
            time: scheduleTimes(s)[0] || '09:00',
            weekdays: kind === 'weekly' ? days.map((d) => (d === 7 ? 0 : d)) : [1],
            dom: kind === 'monthly' || kind === 'yearly' ? Number(days[0] || 1) : 1,
            month: kind === 'yearly' ? Number(s.month || 1) : 1,
            converted: !['daily', 'weekly', 'monthly', 'yearly'].includes(s.kind),
            multiTime: scheduleTimes(s).length > 1
        };
    }
    // Pure: form values -> schedule payload or {error}. Mirrors the server's validation.
    function buildSchedulePayload(form, timezone) {
        const kind = form.kind;
        if (!['daily', 'weekly', 'monthly', 'yearly'].includes(kind)) return { error: 'Pick how often it runs.' };
        const m = /^(\d{1,2}):(\d{2})$/.exec(String(form.time || ''));
        if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return { error: 'Enter a time as HH:MM.' };
        const out = { kind, time: `${m[1].padStart(2, '0')}:${m[2]}`, timezone: timezone || '' };
        if (kind === 'weekly') {
            const days = [...new Set(form.weekdays || [])].sort((a, b) => a - b);
            if (!days.length) return { error: 'Pick at least one weekday.' };
            out.days = days.join(',');
        }
        if (kind === 'monthly' || kind === 'yearly') {
            const dom = Number(form.dom);
            if (!Number.isInteger(dom) || dom < 1 || dom > 31) return { error: 'Day of month must be 1–31.' };
            out.days = String(dom);
        }
        if (kind === 'yearly') {
            const month = Number(form.month);
            if (!Number.isInteger(month) || month < 1 || month > 12) return { error: 'Pick a month.' };
            const limit = { 2: 29, 4: 30, 6: 30, 9: 30, 11: 30 }[month] || 31;
            if (Number(out.days) > limit) return { error: `${MONTHS[month - 1]} has no day ${out.days}.` };
            out.month = String(month);
        }
        return { schedule: out };
    }

    function openScheduleModal(automationId) {
        const automation = (context.automations || []).find((item) => String(item.id) === String(automationId));
        if (!automation || isMessageTriggered(automation)) return;
        const form = scheduleFormState(automation.schedule);
        const kinds = [['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['yearly', 'Yearly']];
        const note = form.converted
            ? `Currently “${automationScheduleLabel(automation)}”. Saving replaces it with the calendar schedule below.`
            : form.multiTime ? `Currently runs at several times (${scheduleTimes(automation.schedule).join(', ')}). Saving keeps one time.` : '';
        const modal = openEditDialog(automation, {
            title: 'Schedule',
            subtitle: automationScheduleLabel(automation),
            body: `<section class="scheduled-schedule-form" aria-label="Schedule">
                <div class="scheduled-kind-tabs" role="radiogroup" aria-label="Repeats">${kinds.map(([k, l]) => `<button type="button" role="radio" class="scheduled-kind-tab" data-sched-kind="${k}" aria-checked="${form.kind === k}">${l}</button>`).join('')}</div>
                <div class="scheduled-sched-fields">
                    <label class="scheduled-edit-field" data-sched-show="yearly"><span>Month</span><select data-sched-month>${MONTHS.map((n, i) => `<option value="${i + 1}"${form.month === i + 1 ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
                    <label class="scheduled-edit-field" data-sched-show="monthly yearly"><span>Day of month</span><input type="number" min="1" max="31" step="1" data-sched-dom value="${form.dom}"></label>
                    <label class="scheduled-edit-field"><span>Time</span><input type="time" data-sched-time value="${esc(form.time)}" required></label>
                </div>
                <div class="scheduled-weekday-picker" data-sched-show="weekly" role="group" aria-label="Weekdays">${CAL_WEEKDAYS.map(([d, l]) => `<button type="button" class="scheduled-weekday" data-sched-day="${d}" aria-pressed="${form.weekdays.includes(d)}">${l}</button>`).join('')}</div>
                <p class="scheduled-edit-note">${note ? esc(note) + ' ' : ''}Times are ${esc(automation.schedule?.timezone || 'local (SAST)')}. The next run is recalculated when you save.</p>
            </section>`
        });
        const sync = () => {
            modal.querySelectorAll('[data-sched-kind]').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.schedKind === form.kind)));
            modal.querySelectorAll('[data-sched-show]').forEach((node) => { node.hidden = !node.dataset.schedShow.split(' ').includes(form.kind); });
        };
        modal.querySelectorAll('[data-sched-kind]').forEach((b) => b.addEventListener('click', () => { form.kind = b.dataset.schedKind; sync(); }));
        modal.querySelectorAll('[data-sched-day]').forEach((b) => b.addEventListener('click', () => {
            const d = Number(b.dataset.schedDay);
            form.weekdays = form.weekdays.includes(d) ? form.weekdays.filter((x) => x !== d) : [...form.weekdays, d];
            b.setAttribute('aria-pressed', String(form.weekdays.includes(d)));
        }));
        sync();
        const error = modal.querySelector('[data-edit-error]');
        modal.querySelector('[data-edit-save]').addEventListener('click', async () => {
            form.time = modal.querySelector('[data-sched-time]').value;
            form.dom = modal.querySelector('[data-sched-dom]').value;
            form.month = modal.querySelector('[data-sched-month]').value;
            const built = buildSchedulePayload(form, automation.schedule?.timezone);
            if (built.error) { error.textContent = built.error; return; }
            error.textContent = '';
            try {
                await saveAutomationPatch(automation, { schedule: built.schedule });
                actions.toast('Schedule saved.');
                closeEditModal();
                await actions.refreshShell({ preserveConversation: true });
                if (['automations', 'automation_rules'].includes(context.workspaceMode)) renderScheduledWorkspace();
            } catch (err) {
                error.textContent = err.message || 'Could not save the schedule.';
            }
        });
        modal.querySelector('[data-sched-time]').focus();
    }

    // automodel 2026-10-22: per-automation harness + model (Inherit global | specific route).
    const modelRouteCache = {
 backends: null, models: new Map(), defaults: null };
    function modelLabel(row) {
        return shortModelName(row.id || '', displayModelName(row.name || '')) || row.name || row.id;
    }
    async function cliBackends() {
        // harnesssrc: installed harnesses only, from the shared source.
        return loadInstalledHarnesses();
    }
    async function cliModels(backend) {
        if (!modelRouteCache.models.has(backend)) {
            modelRouteCache.models.set(backend, fetch(`/api/projects/cli-models?backend_id=${encodeURIComponent(backend)}`).then((r) => (r.ok ? r.json() : { models: [] })).catch(() => ({ models: [] })));
        }
        return modelRouteCache.models.get(backend);
    }
    async function globalDefault() {
        try {
            const res = await fetch('/api/automations/model-defaults');
            return res.ok ? await res.json() : {};
        } catch (_) {
            return {};
        }
    }
    async function fillModels(select, backend, selected, provider, onlyProvider) {
        if (!backend) {
            select.innerHTML = '<option value="">—</option>';
            select.disabled = true;
            return;
        }
        select.disabled = true;
        select.innerHTML = '<option value="">Loading…</option>';
        const data = await cliModels(backend);
        const ide = data.supports_model_picker === false || data.backend_kind === 'ide';
        const options = [`<option value="auto">${ide ? 'Model chosen inside the IDE' : 'Auto'}</option>`];
        (data.models || []).forEach((m) => {
            const row = m && typeof m === 'object' ? m : { id: String(m || ''), name: String(m || '') };
            if (!row.id) return;
            // schedmodel: provider picker narrows the model list.
            if (onlyProvider && String(row.provider || '').toLowerCase() !== onlyProvider && row.id !== selected) return;
            options.push(`<option value="${esc(row.id)}" data-provider="${esc(String(row.provider || '').toLowerCase())}" title="${esc(row.id)}">${esc(modelLabel(row))}${row.provider ? ` · ${esc(row.provider)}` : ''}</option>`);
        });
        if (selected && selected !== 'auto' && !(data.models || []).some((m) => (m && m.id) === selected)) {
            options.push(`<option value="${esc(selected)}" data-provider="${esc(provider || '')}">${esc(selected)}</option>`);
        }
        select.innerHTML = options.join('');
        select.value = selected || 'auto';
        select.disabled = false;
    }
    // schedmodel 2026-10-22: provider picker (distinct providers of the harness's models).
    async function fillProviders(select, backend, selected) {
        if (!select) return '';
        if (!backend) {
            select.innerHTML = '<option value="">—</option>';
            select.disabled = true;
            return '';
        }
        const data = await cliModels(backend);
        const providers = [...new Set((data.models || []).map((m) => String((m && m.provider) || '').toLowerCase()).filter(Boolean))].sort();
        const want = String(selected || '').toLowerCase();
        if (want && !providers.includes(want)) providers.push(want);
        select.innerHTML = ['<option value="">Any provider</option>', ...providers.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`)].join('');
        select.value = want;
        select.disabled = providers.length === 0;
        return select.value;
    }
    async function bindModelRoute(modal, automation, opts = {}) {
        const backendSel = modal.querySelector('[data-automation-cli-backend]');
        const modelSel = modal.querySelector('[data-automation-cli-model]');
        const providerSel = modal.querySelector('[data-automation-cli-provider]');
        const hint = modal.querySelector('[data-automation-cli-hint]');
        const save = opts.save || modal.querySelector('[data-automation-cli-save]');
        if (!backendSel || !modelSel) return;
        const config = automation.action_config || {};
        const legacyRoute = !config.cli_route_mode && config.backend;
        const custom = (String(config.cli_route_mode || '') === 'custom' && config.cli_backend) || legacyRoute;
        const [backends, defaults] = await Promise.all([cliBackends(), globalDefault()]);
        const inheritLabel = defaults.backend
            ? `Inherit default — ${layerText(defaults.backend, defaults.model)}`
            : 'Inherit default — Complexity routing';
        const savedBackend = custom ? String(legacyRoute ? config.backend : config.cli_backend).toLowerCase() : '';
        backendSel.innerHTML = harnessSelectOptions(backends, savedBackend, { leading: `<option value="">${esc(inheritLabel)}</option>`, escape: esc });
        backendSel.value = savedBackend;
        const savedProvider = custom ? String((legacyRoute ? config.model_provider : config.cli_model_provider) || '').toLowerCase() : '';
        const savedModel = custom ? String((legacyRoute ? config.model : config.cli_model) || 'auto') : '';
        const prov = await fillProviders(providerSel, backendSel.value, savedProvider);
        await fillModels(modelSel, backendSel.value, savedModel, savedProvider, prov);
        const legacy = !config.cli_route_mode && config.backend
            ? `Imported route in use: ${config.backend}${config.model ? ' / ' + config.model : ''}. Choose “Inherit default” and Save to drop it.`
            : '';
        if (hint) hint.textContent = legacy;
        backendSel.addEventListener('change', async () => {
            await fillProviders(providerSel, backendSel.value, '');
            await fillModels(modelSel, backendSel.value, 'auto', '', '');
        });
        providerSel?.addEventListener('change', () => fillModels(modelSel, backendSel.value, 'auto', providerSel.value, providerSel.value));
        save?.addEventListener('click', async () => {
            const backend = backendSel.value;
            const option = modelSel.selectedOptions ? modelSel.selectedOptions[0] : null;
            const actionConfig = backend
                ? { cli_route_mode: 'custom', cli_backend: backend, cli_model: modelSel.value || 'auto', cli_model_provider: option?.dataset.provider || providerSel?.value || '' }
                : { cli_route_mode: 'inherit', cli_backend: '', cli_model: '', cli_model_provider: '' };
            try {
                await saveAutomationPatch(automation, { action_config: actionConfig });
                if (hint) hint.textContent = backend ? 'Saved. This automation uses its own harness and model.' : 'Saved. This automation inherits the global default.';
                actions.toast('Automation model saved.');
                routeNames.defaults = null;
                ensureRouteNames();
                if (opts.onSaved) opts.onSaved();
            } catch (error) {
                actions.toast(error.message || 'Could not save the automation model.', 'error');
            }
        });
    }

    // The side detail panel was removed; keep the container hidden.
    function renderScheduledDetail() {
        el('scheduled-detail')?.classList.add('hidden');
        el('scheduled-layout')?.classList.remove('detail-open');
    }

    // Kept under the old name for callers: shows the automation read-only on this page.
    function openAutomationDialog(automation) {
        if (!automation) return;
        if (!['automations', 'automation_rules'].includes(context.workspaceMode) && actions.setWorkspaceMode) {
            actions.setWorkspaceMode('automations');
        }
        openHistoryModal(automation.id);
    }

    async function runAutomation(automationId) {
        try {
            const result = await actions.api(`/automations/${encodeURIComponent(automationId)}/run`, { method: 'POST', body: {} });
            actions.toast(result.run?.summary || 'Automation started.');
            delete state.automationRuns[String(automationId)];
            await actions.refreshShell({ preserveConversation: ['automations', 'automation_rules'].includes(context.workspaceMode) });
            if (['automations', 'automation_rules'].includes(context.workspaceMode)) renderScheduledWorkspace();
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

    async function deleteAutomation(automationId) {
        const automation = (context.automations || []).find((item) => String(item.id) === String(automationId));
        if (!automation) return;
        if (
            !(await actions.confirmAction({
                title: 'Delete automation',
                message: `Delete “${automation.name || 'this automation'}” and its run history?`,
                confirmLabel: 'Delete',
                danger: true
            }))
        )
            return;
        try {
            await actions.api(`/automations/${encodeURIComponent(automationId)}`, { method: 'DELETE' });
            if (String(state.selectedAutomationId) === String(automationId)) state.selectedAutomationId = '';
            delete state.automationRuns[String(automationId)];
            await actions.refreshShell({ preserveConversation: true });
            renderScheduledWorkspace();
            actions.toast('Automation deleted.');
        } catch (error) {
            actions.toast(error.message || 'Could not delete the automation.', 'error');
        }
    }

    function moveCalendar(amount) {
        const anchor = startOfDay(state.calendarAnchor);
        if (state.calendarMode === 'month') state.calendarAnchor = new Date(anchor.getFullYear(), anchor.getMonth() + amount, 1);
        else if (state.calendarMode === 'day') state.calendarAnchor = addDays(anchor, amount);
        else state.calendarAnchor = addDays(anchor, amount * 7);
        renderCalendar();
    }

    function bindEvents() {
        let resizeTimer = 0;
        window.addEventListener('resize', () => {
            window.clearTimeout(resizeTimer);
            resizeTimer = window.setTimeout(() => {
                if (state.view === 'calendar' && el('scheduled-calendar')?.offsetParent) renderCalendar();
            }, 150);
        });
        document.addEventListener('click', (event) => {
            const pop = document.getElementById('scheduled-day-popover');
            if (pop && !pop.contains(event.target) && !event.target.closest('.cal-more, [data-cal-cluster]')) closeDayPopover();
            const menu = document.getElementById('scheduled-cal-menu');
            if (menu && !menu.contains(event.target)) closeCalendarMenu();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeScheduledRowMenu();
                closeDayPopover();
                closeHistoryModal();
                closeEditModal();
                closeCalendarMenu();
            }
        });
        const toolbar = el('scheduled-calendar-toolbar');
        const toggleButton = el('scheduled-view-toggle');
        if (toolbar && toggleButton && toggleButton.parentElement !== toolbar) toolbar.appendChild(toggleButton);
        document.querySelectorAll('[data-scheduled-view]').forEach((button) =>
            button.addEventListener('click', () => {
                state.view = button.dataset.scheduledView === 'calendar' ? 'calendar' : 'list';
                writeSession(VIEW_KEY, state.view);
                renderScheduledWorkspace();
            })
        );
        bindStatusDropdown();
        el('scheduled-calendar-previous')?.addEventListener('click', () => moveCalendar(-1));
        el('scheduled-calendar-next')?.addEventListener('click', () => moveCalendar(1));
        el('scheduled-calendar-today')?.addEventListener('click', () => {
            state.calendarAnchor = new Date();
            renderCalendar();
        });
        document.querySelectorAll('[data-scheduled-calendar-mode]').forEach((button) =>
            button.addEventListener('click', () => {
                const mode = button.dataset.scheduledCalendarMode;
                state.calendarMode = ['day', 'week', 'month'].includes(mode) ? mode : 'week';
                renderCalendar();
            })
        );
    }

    function openAutomationEditor(automationId) {
        closeScheduledRowMenu();
        window.location.assign(`/automations/?automation=${encodeURIComponent(automationId)}`);
    }

    function closeScheduledRowMenu() {
        const menu = document.getElementById('scheduled-row-menu');
        const returnFocus = menu?.returnFocus;
        menu?.remove();
        returnFocus?.focus();
    }

    function openScheduledRowMenu(automationId, x, y) {
        closeScheduledRowMenu();
        const menu = document.createElement('div');
        menu.id = 'scheduled-row-menu';
        menu.returnFocus = [...(el('scheduled-list')?.querySelectorAll('[data-row-action="edit"]') || [])]
            .find((button) => button.dataset.automationId === String(automationId));
        menu.className = 'scheduled-cal-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = '<button type="button" role="menuitem">Edit automation</button>';
        document.body.appendChild(menu);
        menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
        menu.querySelector('button').addEventListener('click', () => openAutomationEditor(automationId));
        menu.querySelector('button').addEventListener('keydown', (event) => {
            if (event.key === 'Tab') closeScheduledRowMenu();
        });
        menu.querySelector('button').focus();
    }
    function closeAutomationImportMenu() {}

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
