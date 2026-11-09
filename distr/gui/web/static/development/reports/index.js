// Owns report loading, filters and cleanup. No dependencies on other feature state.
// Report + Stats tabs live in ./period.js; History is one work list. Costs stay here.
import { createPeriodReport } from './period.js?v=20261109-invoice-review2';

// Supported list routes accept limit only. They have no offset or cursor.
export const HISTORY_RUNS_URL = '/workflows/studio/reports?limit=100';
export const HISTORY_TIME_URL = '/workflows/studio/reports/time?limit=100';
export const COSTS_TAB_NOTE = 'Latest ledger rows (up to 100), across all dates and projects. Source filters apply only to loaded rows; the total covers all stored rows. The report and Stats do not load this list. Opening Costs does.';

const SAST_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// History and Costs instants are UTC (naive ISO is treated as UTC, ledger epochs are UTC).
// Shown as 7 Nov 2026, 17:50 SAST. A YYYY-MM-DD day label is already a SAST calendar day.
export function formatSastWhen(value) {
    if (value == null || value === '') return 'Unknown';
    const text = String(value).trim();
    const dayOnly = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (dayOnly) {
        const month = SAST_MONTHS[Number(dayOnly[2]) - 1];
        if (!month) return 'Unknown';
        return `${Number(dayOnly[3])} ${month} ${dayOnly[1]}`;
    }
    let date;
    if (typeof value === 'number' || (/^\d+(\.\d+)?$/.test(text) && Number(text) > 1e9)) {
        date = new Date(Number(text) * 1000);
    } else {
        date = new Date(`${text}${/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text) ? '' : 'Z'}`);
    }
    if (Number.isNaN(date.getTime())) return 'Unknown';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Africa/Johannesburg',
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
    }).formatToParts(date).map((part) => [part.type, part.value]));
    if (!parts.day || !parts.month || !parts.year) return 'Unknown';
    return `${Number(parts.day)} ${parts.month} ${parts.year}, ${parts.hour}:${parts.minute} SAST`;
}

export function providerModelLabel(provider, model) {
    const rawProvider = String(provider || '').trim();
    const rawModel = String(model || '').trim();
    const missing = (value) => !value || value === '—' || value === '-' || value === '–';
    const providerText = missing(rawProvider) ? '' : rawProvider;
    let modelText = '';
    if (!missing(rawModel)) modelText = rawModel.toLowerCase() === 'auto' ? 'chosen automatically' : rawModel;
    if (!providerText && !modelText) return 'Provider and model not recorded';
    if (!providerText) return `Provider not recorded, ${modelText}`;
    if (!modelText) return `${providerText}, model not recorded`;
    return `${providerText}, ${modelText}`;
}

export function historyEndLabel(row) {
    if (row?.ended_at) return formatSastWhen(row.ended_at);
    if (String(row?.status || '').toLowerCase() === 'running') return 'In progress';
    return 'Unknown';
}

export function historyProjectLabel(row) {
    const name = String(row?.project_name || '').trim();
    if (name) return name;
    if (!row || !Object.prototype.hasOwnProperty.call(row, 'project_id')) return 'Project unavailable';
    const id = row.project_id;
    if (id == null || id === '' || Number(id) === 0) return 'Unassigned';
    return 'Project unavailable';
}

// The Costs tab total is the full rollup, not the sum of the loaded page.
export function costsDisplayedTotal(rollups) {
    const value = rollups?.total_cost_usd;
    return value == null ? null : Number(value);
}

// Chats and projects already loaded by Development. A negative project id is not a project.
export function historyCatalog(chatsPayload, projectsPayload) {
    const projects = new Map();
    const projectList = Array.isArray(projectsPayload) ? projectsPayload : (projectsPayload?.projects || []);
    for (const project of projectList) {
        const id = Number(project?.id);
        const name = String(project?.name || '').trim();
        if (Number.isInteger(id) && id > 0 && name) projects.set(id, name);
    }
    const byId = new Map();
    const byExecution = new Map();
    const chatList = Array.isArray(chatsPayload) ? chatsPayload : (chatsPayload?.chats || []);
    for (const chat of chatList) {
        const id = Number(chat?.id);
        if (!Number.isInteger(id) || id <= 0) continue;
        const execution = chat.development_execution && typeof chat.development_execution === 'object' ? chat.development_execution : {};
        const session = Number(execution.execution_session_id);
        const rawProject = chat.project_id;
        const projectId = rawProject == null || rawProject === '' ? null : Number(rawProject);
        const record = {
            id,
            title: String(chat.title || '').trim(),
            projectId: Number.isInteger(projectId) && projectId > 0 ? projectId : null
        };
        byId.set(id, record);
        if (Number.isInteger(session) && session > 0 && !byExecution.has(session)) byExecution.set(session, record);
    }
    return { projects, byId, byExecution };
}

function positiveProject(catalog, id) {
    const number = Number(id);
    if (!Number.isInteger(number) || number <= 0 || !catalog.projects.has(number)) return null;
    return { id: number, name: catalog.projects.get(number) };
}

// Fill work and project only from a chat or project that was actually returned.
// A missing chat stays unknown. Null automation durations stay null.
export function applyHistoryContext(items, catalog) {
    if (!catalog) return items || [];
    return (items || []).map((row) => {
        const next = { ...row };
        if (String(row.id).startsWith('time:')) {
            if (row.ended_at && row.duration_seconds != null && Number.isFinite(Number(row.duration_seconds)) && Number(row.duration_seconds) >= 0) {
                next.status = 'Recorded';
            }
            const chat = catalog.byId.get(Number(row.chat_id));
            if (!chat) return next;
            if (!next.description && chat.title) next.description = chat.title;
            const project = positiveProject(catalog, chat.projectId);
            if (project) {
                next.project_id = project.id;
                next.project_name = project.name;
            }
            return next;
        }
        if (String(row.id).startsWith('execution:')) {
            const session = Number(String(row.id).slice('execution:'.length));
            const chat = catalog.byExecution.get(session);
            if (chat) {
                if (chat.title && (!next.description || / execution$/i.test(next.description))) next.description = chat.title;
                if (next.chat_id == null) next.chat_id = chat.id;
                const project = positiveProject(catalog, chat.projectId);
                if (project) {
                    next.project_id = project.id;
                    next.project_name = project.name;
                } else {
                    next.project_id = null;
                    next.project_name = null;
                }
                return next;
            }
            if (Number(next.project_id) < 0) {
                delete next.project_id;
                next.project_name = null;
                return next;
            }
            const project = positiveProject(catalog, next.project_id);
            if (project && !next.project_name) next.project_name = project.name;
            return next;
        }
        if (Number(next.project_id) < 0) {
            next.project_id = null;
            next.project_name = null;
        } else {
            const project = positiveProject(catalog, next.project_id);
            if (project && !next.project_name) next.project_name = project.name;
        }
        return next;
    });
}

export function historyCaption(window) {
    const runLimit = Number(window?.runLimit || 100);
    const timeLimit = Number(window?.timeLimit || 100);
    const runCount = Number(window?.runCount || 0);
    const timeCount = Number(window?.timeCount || 0);
    const runs = runCount >= runLimit
        ? `Runs: the latest ${runLimit} only. Older runs are not in this response.`
        : `Runs: ${runCount} returned, fewer than the ${runLimit} cap.`;
    const timers = timeCount >= timeLimit
        ? `Timers: the latest ${timeLimit} only. Older timers are not in this response.`
        : `Timers: ${timeCount} returned, fewer than the ${timeLimit} cap.`;
    return `${runs} ${timers} These routes accept limit only. They have no offset or cursor. The status filter applies only to the loaded rows, so a match in older work is not shown.`;
}

// Runs and closed timers stay separate rows. A synthetic "… execution" name is not a work description.
export function mergeHistory(runs, times) {
    const items = [];
    const seen = new Set();
    let anon = 0;
    function identity(row, source) {
        if (row?.id == null || row.id === '') return `row-without-id:${source}:${anon++}`;
        return String(row.id);
    }
    for (const row of runs?.items || []) {
        if (!row) continue;
        const id = identity(row, 'run');
        if (seen.has(id)) continue;
        seen.add(id);
        const name = String(row.name || '').trim();
        const synthetic = row.kind === 'execution' && / execution$/i.test(name);
        const runItem = {
            id,
            description: synthetic || !name ? null : name,
            status: row.status ? String(row.status) : null,
            started_at: row.started_at || null,
            ended_at: row.completed_at || null,
            duration_seconds: row.duration_seconds ?? null,
            project_name: row.project_name ? String(row.project_name) : null,
            chat_id: row.chat_id ?? null
        };
        if (Object.prototype.hasOwnProperty.call(row, 'project_id')) runItem.project_id = row.project_id;
        items.push(runItem);
    }
    for (const row of times?.items || []) {
        if (!row) continue;
        const id = identity(row, 'time');
        if (seen.has(id)) continue;
        seen.add(id);
        const fallback = `Thread ${row.chat_id}`;
        const ticket = String(row.ticket_title || '').trim();
        const thread = String(row.thread_title || '').trim();
        const key = String(row.ticket_key || '').trim();
        let description = ticket && ticket !== key ? ticket : thread;
        if (!description || description === fallback) description = ticket && ticket !== fallback ? ticket : null;
        const timeItem = {
            id,
            description,
            status: null,
            started_at: row.started_at || null,
            ended_at: row.ended_at || null,
            duration_seconds: row.seconds ?? null,
            project_name: row.project_name ? String(row.project_name) : null,
            chat_id: row.chat_id ?? null
        };
        if (Object.prototype.hasOwnProperty.call(row, 'project_id')) timeItem.project_id = row.project_id;
        items.push(timeItem);
    }
    items.sort((a, b) => (b.started_at || '').localeCompare(a.started_at || '') || String(b.id).localeCompare(String(a.id)));
    const runItems = runs?.items || [];
    const timeItems = times?.items || [];
    return {
        items,
        window: {
            runCount: runItems.length,
            timeCount: timeItems.length,
            runLimit: Number(runs?.limit || 100),
            timeLimit: Number(times?.limit || 100)
        },
        counting: {
            rows: 'Each row is one recorded run or one closed timer. They are not joined, so a timer and a run that overlap stay two rows.',
            duration: 'Duration is that row only. Adding durations double-counts overlap.',
            wall_clock: 'Global clock on Stats counts an overlapping minute once, including across projects.',
            unknown: 'A missing description, status, start, or end stays blank of invented text.'
        }
    };
}

export function createReports({ api, root, escapeHtml, getPinnedProjectIds }) {
    let historyItems = [],
        historyMeta = null,
        costRows = [],
        costRollups = null,
        costDisplay = 'blended',
        request = null,
        generation = 0,
        visible = false,
        tab = 'report';
    let loading = false,
        failure = '',
        historyFailure = '',
        costFailure = '';
    const result = root.querySelector('#reports-results');
    const runFilters = root.querySelector('#reports-run-filters');
    const costFilters = root.querySelector('#reports-cost-filters');
    const periodView = root.querySelector('#reports-period-view');
    const statsView = root.querySelector('#reports-stats-view');
    const periodControls = root.querySelector('#reports-period-controls');
    const projectsControl = root.querySelector('#reports-projects');
    const buildButton = root.querySelector('#reports-build');
    const period = createPeriodReport({ api, root, escapeHtml, getPinnedProjectIds });

    function formatDuration(seconds) {
        if (seconds == null) return 'Not recorded';
        const value = Number(seconds);
        if (!Number.isFinite(value) || value < 0) return 'Unknown';
        if (value < 60) return `${Math.round(value)} s`;
        const minutes = Math.floor(value / 60);
        const rem = Math.round(value % 60);
        if (minutes < 60) return rem ? `${minutes}m ${rem}s` : `${minutes}m`;
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        return mins ? `${hours}h ${mins}m` : `${hours}h`;
    }

    function formatWhen(value) {
        return formatSastWhen(value);
    }

    function formatUsd(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '$0.00';
        if (Math.abs(n) < 0.0001 && n !== 0) return `$${n.toFixed(6)}`;
        return `$${n.toFixed(4)}`;
    }

    function known(value) {
        const text = value == null ? '' : String(value).trim();
        return text ? escapeHtml(text) : 'Unknown';
    }

    function setTab(next, options = {}) {
        tab = ['stats', 'costs', 'runs'].includes(next) ? next : 'report';
        const isPeriod = tab === 'report' || tab === 'stats';
        root.querySelectorAll('[data-reports-tab]').forEach((button) => {
            const active = button.dataset.reportsTab === tab;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        if (runFilters) runFilters.hidden = tab !== 'runs';
        if (costFilters) costFilters.hidden = tab !== 'costs';
        if (periodView) periodView.hidden = tab !== 'report';
        if (statsView) statsView.hidden = tab !== 'stats';
        if (periodControls) periodControls.hidden = !isPeriod;
        if (projectsControl) projectsControl.hidden = !isPeriod;
        if (buildButton) buildButton.hidden = !isPeriod;
        result.hidden = isPeriod;
        if (isPeriod) {
            if (visible) period.enter(tab);
            return;
        }
        if (options.reload) load();
        else render();
    }

    function renderHistory() {
        if (historyFailure) {
            result.textContent = historyFailure;
            return;
        }
        const status = root.querySelector('#reports-status')?.value || 'all';
        const selected = historyItems.filter((row) => status === 'all' || row.status === status);
        const heading = '<h3>Recent activity</h3>';
        if (!selected.length) {
            result.innerHTML = `${heading}<p>No loaded row matches this filter. Older work is outside this page.</p>`;
            return;
        }
        const body = selected.map((row) => {
            const description = row.description ? escapeHtml(row.description) : 'Unknown';
            const label = row.chat_id != null && row.description
                ? `<a href="/development/threads/${Number(row.chat_id)}/">${description}</a>`
                : description;
            return `<tr><td>${label}</td><td>${escapeHtml(historyProjectLabel(row))}</td><td>${known(row.status)}</td><td>${escapeHtml(formatWhen(row.started_at))}</td><td>${escapeHtml(historyEndLabel(row))}</td><td>${escapeHtml(formatDuration(row.duration_seconds))}</td></tr>`;
        }).join('');
        result.innerHTML = `${heading}<div class="reports-table-scroll"><table><thead><tr><th>Work</th><th>Project</th><th>Status</th><th>Started</th><th>Ended</th><th>Duration</th></tr></thead><tbody>${body}</tbody></table></div>`;
    }

    function renderCosts() {
        if (costFailure) {
            result.textContent = costFailure;
            return;
        }
        const sourceFilter = root.querySelector('#reports-cost-source')?.value || 'all';
        const selected = costRows.filter((row) => sourceFilter === 'all' || row.source === sourceFilter);
        const total = costsDisplayedTotal(costRollups);
        const dayCount = costRollups?.by_day?.length || 0;
        const detailLabel = costDisplay === 'explicit' ? 'Provider and computer shown separately' : 'One combined amount';
        const summary = `<div class="reports-cost-summary"><span>${escapeHtml(detailLabel)}</span><span>Loaded rows: <strong>${selected.length}</strong> of up to 100</span><span>Whole ledger: <strong>${escapeHtml(formatUsd(total))}</strong></span><span>Days in the whole ledger: <strong>${dayCount}</strong></span></div><p class="reports-note">${escapeHtml(COSTS_TAB_NOTE)}</p>`;
        if (!selected.length) {
            result.innerHTML = `${summary}<p>No cost ledger entries yet. Workflow steps record usage when cost_ledger_enabled is on.</p>`;
            return;
        }
        const explicit = costDisplay === 'explicit';
        const head = explicit
            ? '<tr><th>Date</th><th>Run</th><th>Provider and model</th><th>Tokens</th><th>Provider cost</th><th>Computer cost</th><th>Combined cost</th><th>Ledger source</th></tr>'
            : '<tr><th>Date</th><th>Run</th><th>Provider and model</th><th>Cost</th><th>Ledger source</th></tr>';
        const body = selected
            .map((row) => {
                const provider = escapeHtml(providerModelLabel(row.provider, row.model));
                const runLabel = row.run_id != null ? `Run ${row.run_id}` : 'Run not recorded';
                if (explicit) {
                    const tokens = [
                        row.tokens_in != null ? `in ${row.tokens_in}` : null,
                        row.tokens_out != null ? `out ${row.tokens_out}` : null,
                        row.tokens_total != null ? `total ${row.tokens_total}` : null
                    ]
                        .filter(Boolean)
                        .join(', ');
                    return `<tr><td>${escapeHtml(formatSastWhen(row.day_sast))}<small>${escapeHtml(formatWhen(row.recorded_at))}</small></td><td>${escapeHtml(runLabel)}<small>${escapeHtml(row.client_key || '')}</small></td><td>${provider}</td><td>${escapeHtml(tokens || 'Tokens not recorded')}</td><td>${escapeHtml(formatUsd(row.provider_cost_usd))}</td><td>${escapeHtml(formatUsd(row.resource_cost_usd))}<small>${escapeHtml(row.resource_notes || '')}</small></td><td>${escapeHtml(formatUsd(row.cost_usd))}</td><td>${escapeHtml(row.source || 'Source not recorded')}</td></tr>`;
                }
                return `<tr><td>${escapeHtml(formatSastWhen(row.day_sast))}<small>${escapeHtml(formatWhen(row.recorded_at))}</small></td><td>${escapeHtml(runLabel)}<small>${escapeHtml(row.client_key || '')}</small></td><td>${provider}</td><td>${escapeHtml(formatUsd(row.cost_usd))}</td><td>${escapeHtml(row.source || 'Source not recorded')}</td></tr>`;
            })
            .join('');
        result.innerHTML = `${summary}<div class="reports-table-scroll"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }

    function render() {
        if (tab === 'report' || tab === 'stats') return;
        if (loading || failure) {
            const loadingLabel = tab === 'costs' ? 'Loading costs...' : 'Loading history...';
            result.textContent = loading ? loadingLabel : failure;
            root.dataset.reportsReady = '1';
            return;
        }
        if (tab === 'costs') renderCosts();
        else renderHistory();
        result.querySelectorAll('table').forEach((table) => {
            const headers = [...table.querySelectorAll('th')];
            headers.forEach((header) => {
                header.setAttribute('scope', 'col');
                header.dataset.label = header.textContent;
            });
            table.querySelectorAll('tbody tr').forEach((row) => {
                [...row.cells].forEach((cell, index) => { cell.dataset.label = headers[index]?.textContent || ''; });
            });
        });
        root.dataset.reportsReady = '1';
    }

    function settle(promise) {
        return promise.then((data) => ({ ok: true, data })).catch((error) => {
            if (error?.name === 'AbortError') throw error;
            return { ok: false, error };
        });
    }

    async function load() {
        request?.abort();
        request = new AbortController();
        const current = ++generation;
        loading = true;
        failure = '';
        historyFailure = '';
        costFailure = '';
        render();
        const display = root.querySelector('#reports-cost-display')?.value || 'blended';
        try {
            const jobs = [
                settle(api(HISTORY_RUNS_URL, { signal: request.signal })),
                settle(api(HISTORY_TIME_URL, { signal: request.signal })),
                settle(api('/chats?include_archived=true&surface=development', { signal: request.signal })),
                settle(api('/projects', { signal: request.signal }))
            ];
            if (tab === 'costs') {
                jobs.push(settle(api(`/workflows/studio/reports/costs?limit=100&rollups=true&display=${encodeURIComponent(display)}`, {
                    signal: request.signal
                })));
            }
            const [runs, times, chats, projects, costs] = await Promise.all(jobs);
            if (!visible || current !== generation) return;
            loading = false;
            if (runs.ok && times.ok) {
                const merged = mergeHistory(runs.data, times.data);
                const catalog = historyCatalog(chats.ok ? chats.data : null, projects.ok ? projects.data : null);
                historyItems = applyHistoryContext(merged.items, catalog);
                historyMeta = merged;
                historyFailure = '';
            } else {
                historyItems = [];
                historyMeta = null;
                const reason = (!runs.ok && runs.error?.message) || (!times.ok && times.error?.message) || '';
                historyFailure = `Could not load history. ${reason}`.trim();
            }
            if (!costs) {
                costFailure = '';
            } else if (costs.ok) {
                costRows = costs.data.items || [];
                costRollups = costs.data.rollups || null;
                costDisplay = costs.data.display || display;
                costFailure = '';
            } else {
                costRows = [];
                costRollups = null;
                costFailure = `Could not load costs. ${costs.error?.message || ''}`.trim();
            }
            render();
        } catch (error) {
            if (!visible || current !== generation || error.name === 'AbortError') return;
            loading = false;
            failure = `Could not load reports. ${error.message}`;
            render();
        }
    }

    for (const select of root.querySelectorAll('#reports-run-filters select')) {
        select.addEventListener('change', render);
    }
    root.querySelector('#reports-cost-display')?.addEventListener('change', () => {
        if (tab === 'costs') load();
    });
    root.querySelector('#reports-cost-source')?.addEventListener('change', render);
    root.querySelectorAll('[data-reports-tab]').forEach((button) => {
        button.addEventListener('click', () => setTab(button.dataset.reportsTab, { reload: true }));
    });

    return {
        enter() {
            visible = true;
            setTab(tab);
            load();
        },
        leave() {
            visible = false;
            generation++;
            request?.abort();
            period.leave();
        }
    };
}
