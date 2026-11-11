// Period report: period picker, multi-project filter, calendar, per-project pie, Stats tab.
// Data: GET /workflows/studio/reports/period, POST .../period/build (Build report).
// All dates are Africa/Johannesburg; the server sends start_local / end_local already in that zone.
const PREFS_KEY = 'decisions.reports.period.v1';
const PALETTE = ['#f97316', '#38bdf8', '#a78bfa', '#22c55e', '#f43f5e', '#eab308', '#14b8a6', '#ec4899', '#8b5cf6', '#84cc16', '#06b6d4', '#fb7185'];
export const HOUR_ZOOM_LEVELS = [18, 28, 36, 48, 64, 88];
export const DEFAULT_HOUR_PX = 36;
export const WEEK_HOUR_PX = 28;
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STATE_WORD = { empty: 'No records', unavailable: 'Not found', not_read: 'Not read', excluded: 'Excluded', unsupported: 'No adapter' };

const sastFormatter = new Intl.DateTimeFormat('en', {
    timeZone: 'Africa/Johannesburg', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
});

function sastClock(now = new Date()) {
    const parts = sastFormatter.formatToParts(now);
    const value = (kind) => parts.find((part) => part.type === kind).value;
    return {
        day: `${value('year')}-${value('month')}-${value('day')}`,
        minutes: Number(value('hour')) * 60 + Number(value('minute'))
    };
}

export function sastToday(now) {
    return sastClock(now).day;
}

export function projectColor(id) {
    const n = Number(id);
    return n === 0 ? '#94a3b8' : PALETTE[((n % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

export function formatHours(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) / 60) || 0);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`;
}

export function formatMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 'Unknown';
    const abs = Math.abs(n);
    if (abs !== 0 && abs < 0.01) return `$${n.toFixed(4)}`;
    return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

// Already-local SAST stamps from the server. Do not run them through Date.
export function formatSast(value) {
    if (!value) return '';
    const [day, time] = String(value).split('T');
    if (!day || !time) return '';
    return `${day} ${time.slice(0, 5)} SAST`;
}

// Chart centre is the sum of per-project clock times. Stats global clock is totals.seconds.
// Those differ when two projects overlap: each project keeps its own clock, the global figure counts that minute once.
export function clockFigures(data) {
    const perProjectSum = (data?.by_project || []).reduce((sum, row) => sum + Number(row.seconds || 0), 0);
    const globalClock = data?.totals && data.totals.seconds != null ? Number(data.totals.seconds) : null;
    return { perProjectSum, globalClock };
}

export function recordedSeconds(project, blocks) {
    if (project && project.activity_seconds != null) return Number(project.activity_seconds);
    const rows = (blocks || []).filter((block) => Number(block.project_id) === Number(project?.project_id) && block.seconds != null);
    if (!rows.length) return null;
    return rows.reduce((sum, block) => sum + Number(block.seconds), 0);
}

export function recordedTotal(data) {
    if (data?.totals && data.totals.activity_seconds != null) return Number(data.totals.activity_seconds);
    if (!(data?.blocks || []).some((block) => block.seconds != null)) return null;
    return (data.blocks || []).reduce((sum, block) => sum + Number(block.seconds || 0), 0);
}

// Period-scoped only. first_start_local and job_start_local are the earliest start among blocks
// that overlap the selected period, not the project's first-ever start. A plain start_local may
// already be clipped to the period; this does not claim that clipping was proven.
export function firstStartStamp(project, blocks) {
    if (project?.first_start_local) return { stamp: project.first_start_local, kind: 'contributing' };
    const rows = (blocks || []).filter((block) => Number(block.project_id) === Number(project?.project_id));
    let best = null;
    let kind = null;
    for (const block of rows) {
        if (block.job_start_local) {
            if (!best || block.job_start_local < best) {
                best = block.job_start_local;
                kind = 'contributing';
            }
            continue;
        }
        if (kind === 'contributing' || !block.start_local) continue;
        if (!best || block.start_local < best) {
            best = block.start_local;
            kind = 'returned';
        }
    }
    return { stamp: best, kind };
}

export function firstStartText(info) {
    if (!info?.stamp) return 'Not in this response';
    const when = formatSast(info.stamp);
    if (info.kind === 'returned') return `${when} (earliest returned start; may be period-clipped)`;
    return `${when} (earliest contributing block in this period; not the project's first-ever start)`;
}

// Explicit UTC (or offset) instants only. Local block stamps stay on formatSast.
export function formatInstantSast(value) {
    if (!value) return '';
    const stamp = String(value).trim();
    if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(stamp)) return '';
    const date = new Date(stamp);
    if (Number.isNaN(date.getTime())) return '';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Africa/Johannesburg',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
    }).formatToParts(date).map((part) => [part.type, part.value]));
    if (!parts.year || !parts.hour) return '';
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} SAST`;
}

const WORK_TYPES = [
    { label: 'Thread timer', keys: ['decisions_thread'] },
    { label: 'Schedule timer', keys: ['schedule_timer'] },
    { label: 'Workflow', keys: ['decisions_workflow'] },
    { label: 'Annotation', keys: ['annotation', 'decisions_annotation'] }
];

function sourceText(source) {
    if (!source) return 'Not in this response';
    if (source.state === 'data') return formatHours(source.seconds);
    return STATE_WORD[source.state] || source.state || 'Not in this response';
}

// Work-record types only. Harness rows (Pi, Codex, Cursor, Claude, executions) stay separate.
export function workTypeRows(sources) {
    const byKey = new Map((sources || []).map((source) => [source.source, source]));
    return WORK_TYPES.map((group) => {
        const found = group.keys.map((key) => byKey.get(key)).filter(Boolean);
        if (!found.length) return { label: group.label, text: 'Not in this response' };
        const withData = found.filter((source) => source.state === 'data');
        if (withData.length) {
            const seconds = withData.reduce((sum, source) => sum + Number(source.seconds || 0), 0);
            return { label: group.label, text: formatHours(seconds) };
        }
        return { label: group.label, text: sourceText(found[0]) };
    });
}

export function harnessRows(sources) {
    const used = new Set(WORK_TYPES.flatMap((group) => group.keys));
    used.add('decisions_automation');
    return (sources || []).filter((source) => !used.has(source.source)).map((source) => ({
        label: source.label || source.source,
        text: sourceText(source)
    }));
}

export function automationRow(sources) {
    const row = (sources || []).find((source) => source.source === 'decisions_automation');
    if (!row) return null;
    return { label: 'Automation', text: sourceText(row) };
}

// Day rollup is the full ledger for those SAST days. Item rows may be only the latest page.
export function periodLedger(payload, period, projectIds) {
    if (!payload) return { available: false, reason: 'The cost ledger was not returned.' };
    const start = period?.start || '';
    const end = period?.end || '';
    const selected = Array.isArray(projectIds) && projectIds.length ? new Set(projectIds.map(Number)) : null;
    const inPeriod = (day) => Boolean(start && end && day && day >= start && day <= end);
    const days = (payload.rollups?.by_day || []).filter((row) => inPeriod(String(row.key || row.day_sast || '')));
    const hasRollup = Array.isArray(payload.rollups?.by_day);
    const allProjectPeriodTotal = hasRollup ? days.reduce((sum, row) => sum + Number(row.cost_usd || 0), 0) : null;
    const items = (payload.items || []).filter((row) => {
        if (!inPeriod(String(row.day_sast || ''))) return false;
        if (!selected) return true;
        const id = row.project_id == null ? 0 : Number(row.project_id);
        return selected.has(id);
    });
    const returned = (payload.items || []).length;
    const stored = payload.rollups?.entry_count;
    const truncated = stored != null && stored > returned;
    const byProject = new Map();
    for (const row of items) {
        const id = row.project_id == null ? 0 : Number(row.project_id);
        const slot = byProject.get(id) || { project_id: id, cost: 0, entries: 0, partial: truncated };
        slot.cost += Number(row.cost_usd || 0);
        slot.entries += 1;
        slot.partial = truncated;
        byProject.set(id, slot);
    }
    return {
        available: true,
        scope: selected ? 'selected' : 'all',
        // The day rollup has no project split, so a selection must not reuse it as its total.
        periodTotal: selected ? null : allProjectPeriodTotal,
        allProjectPeriodTotal,
        allStored: payload.rollups?.total_cost_usd ?? null,
        dayCount: days.length,
        byProject,
        truncated,
        returned,
        stored: stored ?? null
    };
}

export function partialAmount(amount, partial) {
    const money = formatMoney(amount);
    return partial ? `${money} (partial page, not the full period cost)` : money;
}

export function stepHourZoom(current, direction) {
    const levels = HOUR_ZOOM_LEVELS;
    const value = Number(current);
    let index = levels.indexOf(value);
    if (index < 0) {
        index = 0;
        for (let i = 1; i < levels.length; i++) {
            if (Math.abs(levels[i] - value) < Math.abs(levels[index] - value)) index = i;
        }
    }
    const next = index + (direction < 0 ? -1 : 1);
    return levels[Math.min(levels.length - 1, Math.max(0, next))];
}

// Place bars in lanes. Past maxLanes, extra bars collapse into one overflow lane
// so they do not paint on top of each other.
export function layoutOverlapBars(items, maxLanes = 3) {
    const sorted = items.map((item) => ({ ...item })).sort((a, b) => a.start - b.start || b.end - a.end);
    const laneEnds = [];
    for (const item of sorted) {
        let lane = laneEnds.findIndex((end) => end <= item.start);
        if (lane < 0) {
            lane = laneEnds.length;
            laneEnds.push(item.end);
        } else laneEnds[lane] = item.end;
        item.lane = lane;
    }
    const count = Math.max(1, laneEnds.length);
    const cap = Math.max(1, maxLanes);
    const lanesUsed = Math.min(count, cap);
    const width = 100 / lanesUsed;
    if (count <= cap) {
        return {
            laneCount: count,
            bars: sorted.map((item) => ({ ...item, leftPct: item.lane * width, widthPct: width, overflow: false }))
        };
    }
    const detail = cap - 1;
    const bars = sorted.filter((item) => item.lane < detail).map((item) => ({
        ...item, leftPct: item.lane * width, widthPct: width, overflow: false
    }));
    const hidden = sorted.filter((item) => item.lane >= detail);
    const clusters = [];
    for (const item of hidden) {
        const current = clusters[clusters.length - 1];
        if (current && item.start < current.end) {
            current.end = Math.max(current.end, item.end);
            current.count += 1;
            current.items.push(...(item.items || []));
        } else clusters.push({ start: item.start, end: item.end, count: 1, projectId: item.projectId, name: item.name, items: [...(item.items || [])] });
    }
    for (const cluster of clusters) {
        bars.push({
            start: cluster.start,
            end: cluster.end,
            projectId: cluster.count === 1 ? cluster.projectId : 0,
            name: cluster.count === 1 ? cluster.name : '',
            leftPct: detail * width,
            widthPct: width,
            overflow: cluster.count > 1,
            overflowCount: cluster.count,
            items: cluster.items
        });
    }
    return { laneCount: lanesUsed, bars };
}

// 'YYYY-MM-DD' arithmetic in UTC so the browser's own zone never shifts a date.
export function addDays(day, n) {
    const [y, m, d] = day.split('-').map(Number);
    const t = new Date(Date.UTC(y, m - 1, d + n));
    return t.toISOString().slice(0, 10);
}
export function weekday(day) {
    const [y, m, d] = day.split('-').map(Number);
    return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; // Monday = 0
}
export const mondayOf = (day) => addDays(day, -weekday(day));

// Split a block into per-day segments in minutes from local midnight.
export function segments(block) {
    const [sd, st] = block.start_local.split('T');
    const [ed, et] = block.end_local.split('T');
    const minutes = (t) => { const [h, m, s] = t.split(':').map(Number); return h * 60 + m + (s || 0) / 60; };
    const out = [];
    let day = sd;
    let start = minutes(st);
    for (let guard = 0; guard < 3; guard++) {
        const last = day === ed;
        if (last && day !== sd && minutes(et) === 0) break;
        out.push({ day, start, end: last ? Math.max(start + 1, minutes(et)) : 1440 });
        if (last) break;
        day = addDays(day, 1);
        start = 0;
    }
    return out;
}

const SIMPLIFY_FROM = '2026-10-23';
const SIMPLIFY_THROUGH = '2026-11-07';

function localMinutes(stamp) {
    const [, time] = String(stamp || '').split('T');
    if (!time) return null;
    const [h, m, s] = time.split(':').map(Number);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
    return h * 60 + m + (s || 0) / 60;
}

function clockLabel(mins) {
    if (mins >= 1440) return '24:00';
    const h = Math.floor(mins / 60);
    const m = Math.floor(mins % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// Portion of a stored interval that falls on one Johannesburg calendar day.
// The ledger stamps are not changed. A zero-length slice stays a row.
export function daySlice(block, day) {
    const startDay = String(block?.start_local || '').slice(0, 10);
    const endDay = String(block?.end_local || '').slice(0, 10);
    if (!startDay || !endDay || day < startDay || day > endDay) return null;
    const start = day === startDay ? localMinutes(block.start_local) : 0;
    const end = day === endDay ? localMinutes(block.end_local) : 1440;
    if (start == null || end == null || end < start) return null;
    if (day === endDay && day !== startDay && end === 0) return null;
    return {
        start,
        end,
        seconds: Math.max(0, Math.round((end - start) * 60)),
        range: `${clockLabel(start)}–${clockLabel(end)}`
    };
}

function coveredSeconds(spans) {
    let total = 0;
    let end = -Infinity;
    for (const [start, stop] of spans.sort((a, b) => a[0] - b[0])) {
        total += Math.max(0, stop - Math.max(start, end));
        end = Math.max(end, stop);
    }
    return total;
}

export function selectReportProjects(data, selected) {
    if ((data.projects || []).every((project) => selected.has(Number(project.id)))) return data;
    const includes = (row) => selected.has(Number(row.project_id));
    const blocks = (data.blocks || []).filter(includes);
    const byProject = (data.by_project || []).filter(includes);
    const byDay = (data.by_day || []).flatMap((day) => {
        const projects = Object.fromEntries(Object.entries(day.projects || {}).filter(([id]) => selected.has(Number(id))));
        if (!Object.keys(projects).length) return [];
        const spans = blocks.flatMap((block) => {
            const slice = daySlice(block, day.date);
            return slice ? [[slice.start * 60, slice.end * 60]] : [];
        });
        return [{ ...day, projects, seconds: coveredSeconds(spans) }];
    });
    const spans = blocks.flatMap((block) => {
        const start = Date.parse(block.start_local + 'Z');
        const end = Date.parse(block.end_local + 'Z');
        return Number.isFinite(start) && Number.isFinite(end) ? [[start / 1000, end / 1000]] : [];
    });
    const costs = data.costs;
    const projectCosts = (costs?.by_project || []).filter(includes);
    return {
        ...data, blocks, by_day: byDay, by_project: byProject, project_ids: [...selected],
        totals: {
            ...data.totals, active_days: byDay.length, projects: byProject.length, blocks: blocks.length,
            seconds: coveredSeconds(spans), activity_seconds: blocks.reduce((sum, block) => sum + Number(block.seconds || 0), 0),
        },
        costs: costs?.available ? {
            ...costs, by_project: projectCosts,
            entries: projectCosts.reduce((sum, row) => sum + Number(row.entries || 0), 0),
            total_cost_usd: projectCosts.reduce((sum, row) => sum + Number(row.cost_usd || 0), 0),
        } : costs,
    };
}

export function formatSlice(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return 'Unknown';
    const value = Math.max(0, Math.round(Number(seconds)));
    if (value < 60) return `${value}s`;
    return formatHours(value);
}

export function calendarFocusMinute(blocks, days) {
    const selected = new Set(days);
    const starts = (blocks || []).flatMap((block) => segments(block))
        .filter((segment) => selected.has(segment.day)).map((segment) => segment.start);
    return starts.length ? Math.max(0, Math.min(...starts) - 30) : 7 * 60;
}

export function simplifyWorkTitle(block) {
    const start = String(block?.start_local || '').slice(0, 10);
    const end = String(block?.end_local || '').slice(0, 10);
    return Boolean(start && end && start <= SIMPLIFY_THROUGH && end >= SIMPLIFY_FROM);
}

export function displayWorkTitle(block) {
    const title = String(block?.title || '').trim();
    const source = String(block?.source_label || '').trim();
    const project = String(block?.project_name || '').trim();
    let description = title;
    if (project) {
        // Compare the project name literally (names may contain regexp characters),
        // then remove only a punctuation separator after an exact leading match.
        while (description.startsWith(project)) {
            const rest = description.slice(project.length);
            const separator = rest.match(/^\s*[:|·,;.!?\-–—]+\s*/u);
            if (!separator) break;
            description = rest.slice(separator[0].length).trim();
        }
    }
    const generic = !description || description === source || /^Thread \d+$/.test(description)
        || /DECISIONS DEVELOPMENT THREAD/i.test(description)
        || /^Work as the active IDE coding agent/i.test(description)
        || /^Pi via Decisions\b/i.test(description);
    const shown = simplifyWorkTitle(block) && generic ? (source || 'Unknown') : (description || source || 'Unknown');
    return { shown, original: title || source || '' };
}

function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {}; } catch (_) { return {}; }
}

export function initialProjectSelection(prefs, pinnedIds) {
    // Version 1 used [] to mean all. Version 2 remembers an intentional empty selection.
    const saved = Array.isArray(prefs.projects) && (prefs.projectFilterVersion === 2 || prefs.projects.length > 0);
    return new Set((saved ? prefs.projects : pinnedIds).map(Number).filter((id) => Number.isInteger(id) && id >= 0));
}

export function createPeriodReport({ api, root, escapeHtml, getPinnedProjectIds = () => null }) {
    const esc = escapeHtml;
    const view = root.querySelector('#reports-period-view');
    const periodSelect = root.querySelector('#reports-period-select');
    const todayButton = root.querySelector('#reports-today');
    const buildButton = root.querySelector('#reports-build');
    const projectsButton = root.querySelector('#reports-projects-button');
    const projectsMenu = root.querySelector('#reports-projects-menu');
    const statsView = root.querySelector('#reports-stats-view');

    const prefs = loadPrefs();
    const storedHour = Number(prefs.hourPx);
    const storedWeekHour = Number(prefs.weekHourPx);
    // Default period is always the rolling window (6th of last month → today); a month pick is not remembered.
    const state = {
        period: 'rolling',
        options: [],
        projects: new Set((prefs.projects || []).map(Number)),
        calView: prefs.calView || 'month',
        hourPx: HOUR_ZOOM_LEVELS.includes(storedHour) ? storedHour : DEFAULT_HOUR_PX,
        weekHourPx: HOUR_ZOOM_LEVELS.includes(storedWeekHour) ? storedWeekHour : WEEK_HOUR_PX,
        cursor: '',
        data: null,
        ledger: null,
        ledgerFailure: '',
        error: '',
        building: false,
        loading: false,
        visible: false,
        tab: 'report',
        request: null,
        generation: 0
    };
    let selectionInitialized = false;
    let managingProjects = false;
    const savedBuilds = new Map();
    const selectionKey = (projects) => [...projects].sort((a, b) => a - b).join(',');
    const buildKey = (period, selection) => `${period?.start}/${period?.end}/${selection}`;
    let clockTimer;

    function updateCalendarClock() {
        const { day, minutes } = sastClock();
        const isToday = state.cursor === day;
        todayButton.setAttribute('aria-current', isToday ? 'date' : 'false');
        const line = view.querySelector('.reports-now-line');
        if (line) {
            line.hidden = !isToday;
            line.style.top = `${minutes / 1440 * 100}%`;
            line.title = `Current time ${clock(minutes)} SAST`;
        }
    }

    function scheduleCalendarClock() {
        if (typeof window === 'undefined') return;
        clearTimeout(clockTimer);
        clockTimer = window.setTimeout(() => {
            updateCalendarClock();
            scheduleCalendarClock();
        }, 60_000 - Date.now() % 60_000);
    }

    function savePrefs() {
        localStorage.setItem(PREFS_KEY, JSON.stringify({
            projects: [...state.projects],
            projectFilterVersion: 2,
            calView: state.calView,
            hourPx: state.hourPx,
            weekHourPx: state.weekHourPx
        }));
    }

    function query(selection) {
        const params = new URLSearchParams();
        if (state.period !== 'rolling') params.set('month', state.period);
        if (selection) params.set('saved_projects', selection);
        return params;
    }

    async function load() {
        state.request?.abort();
        state.request = new AbortController();
        const current = ++state.generation;
        const selection = selectionKey(state.projects);
        state.loading = true;
        state.error = '';
        render();
        try {
            // Do not call the cost route here. list_entries and cost_rollups run ensure_tables.
            const data = await api(`/workflows/studio/reports/period?${query(selection)}`, { signal: state.request.signal });
            if (!state.visible || current !== state.generation) return;
            state.ledger = null;
            state.ledgerFailure = '';
            applyData(data);
            savedBuilds.set(buildKey(data.period, selection), data.saved);
        } catch (error) {
            if (!state.visible || current !== state.generation || error.name === 'AbortError') return;
            state.error = error.status === 404 || error.status === 405
                ? 'The period report needs a Decisions restart to load its new backend. History and Costs still work.'
                : `Could not load the report. ${error.message}`;
        } finally {
            if (current === state.generation) {
                state.loading = false;
                render();
            }
        }
    }

    function applyData(data) {
        state.data = data;
        state.options = data.period_options || state.options;
        const period = data.period || {};
        if (!state.cursor || state.cursor < period.start || state.cursor > period.end) {
            const busiest = [...(selectReportProjects(data, state.projects).by_day || [])].sort((a, b) => b.seconds - a.seconds)[0];
            state.cursor = busiest?.date || period.start;
        }
        renderPeriodSelect();
    }

    async function build() {
        if (state.building) return;
        state.building = true;
        buildButton.disabled = true;
        buildButton.textContent = 'Building…';
        state.error = '';
        render();
        try {
            const body = { projects: [...state.projects] };
            if (state.period !== 'rolling') body.month = state.period;
            const data = await api('/workflows/studio/reports/period/build', { method: 'POST', body });
            savedBuilds.set(buildKey(data.period, selectionKey(body.projects)), data.saved);
        } catch (error) {
            state.error = error.status === 404 || error.status === 405
                ? 'Build report needs a Decisions restart to load its new backend.'
                : `Build failed: ${error.message}`;
        } finally {
            state.building = false;
            buildButton.disabled = false;
            buildButton.textContent = 'Build report';
            render();
        }
    }

    function renderPeriodSelect() {
        periodSelect.innerHTML = state.options.map((o) =>
            `<option value="${esc(o.value)}"${o.value === state.period ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
    }

    function projectName(id) {
        return (state.data?.projects || []).find((p) => Number(p.id) === Number(id))?.name || `Project ${id}`;
    }

    function renderProjectsButton() {
        const n = state.projects.size;
        projectsButton.textContent = n === 0 ? 'No projects' : n === 1 ? projectName([...state.projects][0]) : `${n} projects`;
    }

    function renderProjectsMenu() {
        const seconds = new Map((state.data?.by_project || []).map((r) => [Number(r.project_id), r.seconds]));
        const projects = [...(state.data?.projects || [])].sort((a, b) => (seconds.get(Number(b.id)) || 0) - (seconds.get(Number(a.id)) || 0));
        projectsMenu.innerHTML = `<label class="reports-project-option"><input type="checkbox" data-project="all"${projects.length && projects.every((p) => state.projects.has(Number(p.id))) ? ' checked' : ''}> <span>All projects</span></label>`
            + projects.map((p) => {
                const id = Number(p.id);
                const time = seconds.get(id);
                return `<label class="reports-project-option"><input type="checkbox" data-project="${id}"${state.projects.has(id) ? ' checked' : ''}> <i style="background:${projectColor(id)}"></i><span>${esc(p.name)}</span><small>${time ? esc(formatHours(time)) : ''}</small></label>`;
            }).join('');
    }

    function builtLabel(data) {
        const stamp = data?.saved?.created_at;
        if (!stamp) return '';
        const text = formatInstantSast(stamp);
        return text ? `Last built ${text}` : '';
    }

    function render({ preserveScroll = false } = {}) {
        const scrollSelectors = ['.reports-grid', '.reports-day-side', '.reports-timegrid-scroll', '.reports-pie-card', '#reports-project-legend'];
        const offsets = preserveScroll ? scrollSelectors.map((selector) => view.querySelector(selector)?.scrollTop) : [];
        const focusedProject = document.activeElement?.dataset?.legendProject;
        const focusedManage = document.activeElement?.hasAttribute?.('data-manage-projects');
        closeBlockDetail();
        renderProjectsButton();
        if (!state.visible) return;
        const data = state.data && selectReportProjects(state.data, state.projects);
        buildButton.disabled = state.building || state.projects.size === 0;
        const banner = state.error ? `<p class="reports-error" role="alert">${esc(state.error)}</p>` : '';
        if (!data) {
            updateCalendarClock();
            const empty = banner + (state.loading ? '<p class="reports-muted">Loading report…</p>' : '');
            view.innerHTML = empty;
            statsView.innerHTML = empty;
            root.dataset.reportsReady = '1';
            return;
        }
        const stale = state.loading ? ' is-loading' : '';
        const built = root.querySelector('#reports-built');
        if (built) {
            const label = builtLabel({ saved: savedBuilds.get(buildKey(data.period, selectionKey(state.projects))) });
            built.hidden = !label;
            built.textContent = label;
        }
        view.innerHTML = `${banner}
            <div class="reports-grid${stale}">
                <section class="reports-card reports-calendar-card" aria-label="Calendar">${state.projects.size ? calendarHtml(data) : '<p class="reports-muted">No projects selected. Use Manage to select projects.</p>'}</section>
                <section class="reports-card reports-pie-card" aria-label="Work per project">${pieHtml(data)}</section>
            </div>
            ${blockDetailShellHtml()}`;
        updateCalendarClock();
        statsView.innerHTML = `${banner}${state.projects.size ? statsHtml(data, stale) : '<p class="reports-muted">No projects selected.</p>'}`;
        if (focusedProject != null) {
            (view.querySelector(`[data-legend-project="${Number(focusedProject)}"]`) || view.querySelector('[data-manage-projects]'))?.focus({ preventScroll: true });
        } else if (focusedManage) view.querySelector('[data-manage-projects]')?.focus({ preventScroll: true });
        const scroller = view.querySelector('.reports-timegrid-scroll');
        const hour = Number(view.querySelector('.reports-timegrid')?.dataset.hourPx || WEEK_HOUR_PX);
        if (preserveScroll) {
            scrollSelectors.forEach((selector, index) => {
                const node = view.querySelector(selector);
                if (node && offsets[index] != null) node.scrollTop = offsets[index];
            });
        } else if (scroller) {
            scroller.getBoundingClientRect();
            const first = state.calView === 'week' ? mondayOf(state.cursor) : state.cursor;
            const days = Array.from({length: state.calView === 'week' ? 7 : 1}, (_, i) => addDays(first, i));
            scroller.scrollTop = calendarFocusMinute(data.blocks, days) / 60 * hour;
        }
        root.dataset.reportsReady = '1';
    }

    function td(label, html) {
        return `<td data-label="${esc(label)}">${html}</td>`;
    }

    function embeddedCosts(data) {
        const costs = data?.costs;
        if (!costs || costs.available === false) return null;
        return costs;
    }

    function projectCostText(data, projectId) {
        const costs = embeddedCosts(data);
        if (!costs) return 'Not loaded with this report';
        const slot = (costs.by_project || []).find((row) => Number(row.project_id) === Number(projectId));
        const partial = Boolean(costs.partial || costs.truncated);
        if (!slot) return partial ? 'Not in the returned rows (partial page)' : 'No cost row in this response';
        return partialAmount(slot.cost_usd, partial);
    }

    function statsHtml(data, stale) {
        const figures = clockFigures(data);
        const totals = data.totals || {};
        const p = data.period || {};
        const recorded = recordedTotal(data);
        const globalText = figures.globalClock == null ? 'Not in this response' : formatHours(figures.globalClock);
        const recordedText = recorded == null ? 'Not in this response' : formatHours(recorded);
        const quiet = [
            totals.active_days == null ? '' : `${Number(totals.active_days)} active days`,
            totals.projects == null ? '' : `${Number(totals.projects)} projects`,
            totals.blocks == null ? '' : `${Number(totals.blocks)} work blocks`
        ].filter(Boolean).join(', ');
        return `<div class="reports-stats-tab${stale}">
            <p class="reports-note">${esc(p.start || '')} → ${esc(p.end || '')} SAST${quiet ? `. ${esc(quiet)}` : ''}</p>
            ${projectHtml(data)}
            <p class="reports-note">Each project clock already merges overlapping work inside that project. Adding those clocks gives the chart total${figures.perProjectSum ? ` (${esc(formatHours(figures.perProjectSum))})` : ''}. The global clock${figures.globalClock == null ? '' : ` (${esc(globalText)})`} counts a shared minute once, even when two projects overlap. Recorded time${recorded == null ? '' : ` (${esc(recordedText)})`} adds every block in this period, so the same minute can count more than once. These are this period only, not all-time totals.</p>
            ${costHtml(data)}
            ${countingHtml(state.data)}
        </div>`;
    }

    function costHtml(data) {
        const costs = embeddedCosts(data);
        if (!costs) {
            const reason = data?.costs?.reason ? ` ${data.costs.reason}` : '';
            return `<section class="reports-card"><h3>Costs</h3><p class="reports-note">Costs for this period are not in this view.${esc(reason)} Open the Costs tab for the stored ledger. No zero is filled in here.</p></section>`;
        }
        const scope = data.project_ids?.length ? 'Selected projects' : 'All projects';
        const partial = Boolean(costs.partial || costs.truncated);
        const total = partial ? partialAmount(costs.total_cost_usd, true) : formatMoney(costs.total_cost_usd);
        const rows = (costs.by_project || []).map((row) => `<tr>${td('Project', esc(row.name || 'Unknown'))}${td(`Cost (${scope})`, esc(partialAmount(row.cost_usd, partial)))}${td('Ledger rows', String(row.entries ?? ''))}</tr>`).join('');
        const table = rows
            ? `<div class="reports-table-scroll"><table><thead><tr><th scope="col">Project</th><th scope="col">Cost (${esc(scope)})</th><th scope="col">Ledger rows</th></tr></thead><tbody>${rows}</tbody></table></div>`
            : '';
        return `<section class="reports-card"><h3>Costs</h3>
            <p class="reports-stat-figure"><strong>${esc(total)}</strong> ${esc(scope.toLowerCase())}, from the period payload</p>
            <p class="reports-note">${partial ? 'Every amount here is a partial page, including projects that appear. It is not the full period cost.' : 'These amounts follow the report project selection. All projects means no project filter.'} ${esc(costs.counting || '')}</p>
            ${table}
        </section>`;
    }

    function projectHtml(data) {
        const rows = data.by_project || [];
        if (!rows.length) return '<section class="reports-card"><h3>Projects</h3><p class="reports-note">No recorded work for this selection.</p></section>';
        const scope = data.project_ids?.length ? 'Selected projects' : 'All projects';
        const showCost = Boolean(embeddedCosts(data));
        const body = rows.map((row) => {
            const recorded = recordedSeconds(row, data.blocks);
            const start = firstStartText(firstStartStamp(row, data.blocks));
            const costCell = showCost ? td(`Cost (${scope})`, esc(projectCostText(data, row.project_id))) : '';
            return `<tr>${td('Project', esc(row.name || 'Unknown'))}${costCell}${td('Project clock', esc(formatHours(row.seconds)))}${td('Recorded time', recorded == null ? 'Not in this response' : esc(formatHours(recorded)))}${td('Earliest block start', esc(start))}${td('Active days', String(Number(row.days || 0)))}</tr>`;
        }).join('');
        const costHead = showCost ? `<th scope="col">Cost (${esc(scope)})</th>` : '';
        return `<section class="reports-card"><h3>Projects</h3>
            <div class="reports-table-scroll"><table><thead><tr><th scope="col">Project</th>${costHead}<th scope="col">Project clock</th><th scope="col">Recorded time</th><th scope="col">Earliest block start</th><th scope="col">Active days</th></tr></thead><tbody>${body}</tbody></table></div>
            <p class="reports-note">Project clock merges overlaps inside that project only. Recorded time adds every block in this period, not all-time activity. Earliest block start is the earliest start among blocks that contribute to this period. It is not the project's first-ever start. A value marked "earliest returned start" may be period-clipped.</p>
        </section>`;
    }

    function countingHtml(data) {
        const types = workTypeRows(data.sources);
        const automation = automationRow(data.sources);
        if (automation) types.push(automation);
        const typeRows = types.map((row) => `<tr>${td('Type', esc(row.label))}${td('In this response', esc(row.text))}</tr>`).join('');
        const coverage = harnessRows(data.sources).map((row) => `<tr>${td('Harness', esc(row.label))}${td('Coverage', esc(row.text))}</tr>`).join('');
        const coverageTable = coverage
            ? `<h3>Harness coverage</h3><div class="reports-table-scroll"><table><thead><tr><th scope="col">Harness</th><th scope="col">Coverage</th></tr></thead><tbody>${coverage}</tbody></table></div>`
            : '';
        return `<section class="reports-card"><h3>Report types <small>All projects in this period</small></h3>
            <div class="reports-table-scroll"><table><thead><tr><th scope="col">Type</th><th scope="col">In this response</th></tr></thead><tbody>${typeRows}</tbody></table></div>
            <p class="reports-note">Thread timer, schedule timer, workflow, and annotation are work records. Pi, Codex, Cursor, and Claude are harness coverage, listed separately. A missing type is not shown as zero.</p>
            ${coverageTable}
        </section>`;
    }

    function pieHtml(data) {
        const rows = (data.by_project || []).filter((row) => row.seconds > 0 && state.projects.has(Number(row.project_id)));
        const figures = clockFigures({ by_project: rows });
        const total = figures.perProjectSum || 1;
        const radius = 52;
        const circumference = 2 * Math.PI * radius;
        let offset = 0;
        const arcs = rows.map((row) => {
            const len = (row.seconds / total) * circumference;
            const arc = `<circle r="${radius}" cx="70" cy="70" fill="none" stroke="${projectColor(row.project_id)}" stroke-width="22" stroke-dasharray="${len.toFixed(2)} ${(circumference - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"><title>${esc(row.name)}: ${esc(formatHours(row.seconds))}</title></circle>`;
            offset += len;
            return arc;
        }).join('');
        const legendRows = managingProjects
            ? (data.projects || []).map((p) => ({ project_id: Number(p.id), name: p.name, seconds: rows.find((r) => Number(r.project_id) === Number(p.id))?.seconds }))
            : rows;
        const legend = legendRows.map((row) => `<li><label class="reports-project-legend-row"><i style="background:${projectColor(row.project_id)}"></i><span>${esc(row.name)}</span><small>${row.seconds == null ? '' : `${esc(formatHours(row.seconds))} · ${Math.round((100 * row.seconds) / total)}%`}</small><input type="checkbox" data-legend-project="${Number(row.project_id)}" aria-label="Include ${esc(row.name)}"${state.projects.has(Number(row.project_id)) ? ' checked' : ''}></label></li>`).join('');
        const chart = rows.length ? `<svg viewBox="0 0 140 140" role="img" aria-label="Sum of per-project clock time"><g transform="rotate(-90 70 70)">${arcs}</g><text x="70" y="66" text-anchor="middle">${esc(formatHours(figures.perProjectSum))}</text><text x="70" y="82" text-anchor="middle" class="sub">project sum</text></svg>` : '<p class="reports-muted">No recorded work for this selection.</p>';
        return `<h3>Per project</h3>
            <div class="reports-pie">${chart}
            <hr class="reports-pie-rule">
            <ul id="reports-project-legend">${legend}</ul></div>
            <button type="button" class="reports-manage-projects reports-select-button" data-manage-projects aria-expanded="${managingProjects}" aria-controls="reports-project-legend">Manage</button>`;
    }

    function calendarHtml(data) {
        const tabs = ['day', 'week', 'month'].map((v) => `<button type="button" class="reports-seg${state.calView === v ? ' is-active' : ''}" data-cal-view="${v}" aria-pressed="${state.calView === v}">${v[0].toUpperCase() + v.slice(1)}</button>`).join('');
        const nav = state.calView === 'month' ? '' : `<span class="reports-cal-nav"><button type="button" class="reports-icon-button" data-cal-step="-1" aria-label="Previous">‹</button><strong>${esc(cursorLabel())}</strong><button type="button" class="reports-icon-button" data-cal-step="1" aria-label="Next">›</button></span>`;
        let body;
        if (state.calView === 'month') body = monthHtml(data);
        else if (state.calView === 'week') body = timeGridHtml(data, 7, state.weekHourPx, zoomHtml(state.weekHourPx));
        else body = dayShellHtml(data);
        return `<div class="reports-cal"><div class="reports-cal-head"><div class="reports-segs" role="group" aria-label="Calendar view">${tabs}</div>${nav}</div>${body}</div>`;
    }

    function blockDetailShellHtml() {
        return `<dialog id="reports-block-detail" class="reports-detail" aria-labelledby="reports-detail-title" hidden>
            <div class="reports-detail-backdrop" data-detail-close></div>
            <div class="reports-detail-card">
                <header class="reports-detail-header">
                    <h2 id="reports-detail-title">Time entries</h2>
                    <button type="button" class="reports-detail-close" data-detail-close aria-label="Close">×</button>
                </header>
                <div id="reports-detail-body"></div>
            </div>
        </dialog>`;
    }

    function calendarEditor(b) {
        if (b.calendar_edit_lock) return `<p class="reports-detail-note">${esc(b.calendar_edit_lock)}</p>`;
        if (!state.data?.calendar_connected) {
            return b.calendar_draft_id ? '<p class="reports-detail-note">Connect Tensology in Settings to save this entry.</p>' : '';
        }
        const projects = state.data.calendar_projects || [];
        const current = b.calendar_project_id;
        if (current != null && !projects.some((project) => project.id === current)) {
            return '<p class="reports-detail-note">This project is no longer available for edits in Tensology.</p>';
        }
        const options = [`<option value="">Unassigned</option>`, ...projects.map((project) =>
            `<option value="${Number(project.id)}"${current === project.id ? ' selected' : ''}>${esc(project.name)}</option>`)];
        const projectInput = `reports-calendar-project-${esc(b.id)}`;
        return `<form class="reports-calendar-editor" data-calendar-edit="${esc(b.id)}">
            <label>Description<textarea name="title" rows="3" maxlength="255" required>${esc(b.title)}</textarea></label>
            <div class="reports-calendar-times">
                <label>Start (SAST)<input type="datetime-local" name="start_at" required value="${esc(b.entry_start_local.slice(0, 16))}"></label>
                <label>End (SAST)<input type="datetime-local" name="end_at" required value="${esc(b.entry_end_local.slice(0, 16))}"></label>
            </div>
            <div class="reports-calendar-project"><label for="${projectInput}">Project</label><select id="${projectInput}" name="project_id">${options.join('')}</select></div>
            ${b.calendar_draft_id ? '<p class="reports-detail-note">Saves as non-billable time.</p>' : ''}
            <p class="reports-calendar-error" role="alert" hidden></p>
            <button type="submit" class="primary-button">Save to Tensology</button>
        </form>`;
    }

    function blockDetailHtml(b) {
        const fmt = (v) => (v || '').replace('T', ' ');
        const calendar = b.source === 'audited_calendar' || b.source === 'calendar_draft';
        const editor = calendar ? calendarEditor(b) : '';
        const editable = editor.startsWith('<form');
        const facts = editable ? [] : [['Start', fmt(b.start_local)], ['End', fmt(b.end_local)]];
        if (b.job_start_local && b.job_start_local !== b.start_local) facts.push(['Job start', fmt(b.job_start_local)]);
        if (b.job_end_local && b.job_end_local !== b.end_local) facts.push(['Job end', fmt(b.job_end_local)]);
        if (b.ref) facts.push(['Reference', [b.ref, ...(b.related_refs || [])].join('; ')]);
        const meta = [`<span class="reports-detail-project"><i style="--c:${projectColor(b.project_id)}"></i>${esc(b.project_name || 'Unassigned')}</span>`];
        if (b.calendar_draft_id) meta.push('<span>In progress</span>');
        else if (b.source_label) meta.push(`<span>${esc(b.source_label)}</span>`);
        if (b.seconds != null) meta.push(`<span>${esc(formatHours(b.seconds))}</span>`);
        if (!b.calendar_draft_id && (b.description_status === 'needs_review' || b.invoice_status === 'needs_review')) meta.push('<span>Needs review before invoicing</span>');
        if (b.invoice_status === 'non_billable') facts.push(['Invoice', 'Non-billable activity']);
        else if (b.invoice_status === 'zero_rate') facts.push(['Invoice', 'Tracked at R0 (struck through)']);
        else if (b.invoice_status === 'ready') facts.push(['Invoice', 'Reviewed work']);
        if (b.invoice_reason && !b.calendar_draft_id) facts.push(['Review', b.invoice_reason]);
        if (b.timing_basis) facts.push(['Timing', b.calendar_draft_id
            ? b.timing_basis.replace(/^Draft based on /i, 'Based on ') : b.timing_basis]);
        const current = Number(b.project_id || 0);
        const projects = [...(state.data?.projects || [])].filter((p) => Number(p.id) !== 0);
        const options = [`<option value="0"${current === 0 ? ' selected' : ''}>Unassigned</option>`]
            .concat(projects.map((p) => `<option value="${Number(p.id)}"${current === Number(p.id) ? ' selected' : ''}>${esc(p.name)}</option>`))
            .join('');
        const assign = calendar ? '' : `<label class="reports-detail-assign"><span>Project</span>
            <select data-assign-project data-source="${esc(b.source || '')}" data-ref="${esc(b.ref || '')}" data-source-start="${esc(b.source_start || '')}" data-source-end="${esc(b.source_end || '')}">${options}</select></label>`;
        const note = (b.starts_before_period || b.ends_after_period)
            ? '<p class="reports-detail-note">Clipped to the report period.</p>' : '';
        const descriptionAsOf = b.description_asof
            ? `<p class="reports-detail-note">Description reviewed through ${esc(formatSast(b.description_asof))}. This interval has changed since then.</p>` : '';
        const hasIdeThread = /^(pi_local|codex_local):/.test(b.ref || '')
            || ((b.ref || '').startsWith('execution:') && ['pi', 'codex', 'cursor'].includes(b.family));
        const sourceThread = b.invoice_source_note
            ? `<details class="reports-source-thread"><summary>View recovered source</summary><div class="reports-source-content"><p>${esc(b.invoice_source_note)}</p><p>${esc(b.invoice_source_excerpt || '')}</p></div></details>`
            : b.invoice_source_missing
                ? '<p class="reports-detail-note">The original conversation could not be recovered. This activity is excluded from invoices.</p>'
            : hasIdeThread
            ? `<details class="reports-source-thread" data-source-thread="${esc(b.thread_ref || b.ref)}" data-project-id="${current}" data-thread-start="${esc(b.source_start || b.job_start || b.start || '')}" data-thread-end="${esc(b.source_end || b.job_end || b.end || '')}"><summary>View IDE thread</summary><div class="reports-source-content" aria-live="polite"></div></details>`
            : /^chat:\d+$/.test(b.ref || '')
                ? `<a class="reports-source-link" href="/development/threads/${Number(b.ref.split(':')[1])}/">Open Development thread</a>` : '';
        return `<article class="reports-detail-block">
            ${editable ? '' : `<p class="reports-detail-what">${esc(displayWorkTitle(b).shown)}</p>`}
            ${b.reviewed_description && b.reviewed_description !== b.title ? `<p class="reports-detail-note">${esc(b.reviewed_description)}</p>` : ''}
            <p class="reports-detail-meta">${meta.join('')}</p>
            ${editor}
            ${assign}
            <dl class="reports-detail-facts">${facts.map(([k, v]) => `<div${k === 'Reference' ? ' class="reports-detail-reference"' : ''}><dt>${esc(k)}</dt><dd${k === 'Reference' ? ' class="reports-detail-ref"' : ''}>${esc(String(v == null ? '' : v))}</dd></div>`).join('')}</dl>
            ${sourceThread}
            ${descriptionAsOf}
            ${note}
        </article>`;
    }

    let detailTrigger = null;

    async function loadSourceThread(details, before = null) {
        if (details.dataset.loading === 'true') return;
        const content = details.querySelector('.reports-source-content');
        details.dataset.loading = 'true';
        content.setAttribute('aria-busy', 'true');
        const previous = content.innerHTML;
        if (before == null) content.innerHTML = '<p class="reports-detail-note">Loading thread…</p>';
        else content.querySelector('[data-thread-earlier]')?.setAttribute('disabled', '');
        try {
            const query = new URLSearchParams({ ref: details.dataset.sourceThread });
            if (details.dataset.threadStart && details.dataset.threadEnd) {
                query.set('start', details.dataset.threadStart);
                query.set('end', details.dataset.threadEnd);
                if (details.dataset.fullThread === 'true') query.set('all_messages', 'true');
            }
            if (before != null) query.set('before', String(before));
            const data = await api(`/workflows/studio/reports/source-thread?${query}`);
            if (!details.isConnected) return;
            const facts = [['Thread', data.thread_id], ['Folder', data.cwd || 'Not recorded'], ['Source project', data.project_name]];
            const mismatch = Number(data.project_id || 0) !== Number(details.dataset.projectId)
                ? '<p class="reports-detail-note">The source folder maps to a different project than this entry. Review its project assignment.</p>' : '';
            const mixed = data.project_provenance === 'mixed_workspaces'
                ? '<p class="reports-detail-note">This interval includes multiple project folders and remains unassigned.</p>'
                : data.project_provenance === 'unregistered_workdir'
                    ? '<p class="reports-detail-note">The recorded work folder is not assigned to a report project.</p>' : '';
            const header = `<dl class="reports-detail-facts">${facts.map(([k,v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>${mixed}${mismatch}`;
            const scope = details.dataset.threadStart ? `<div><p class="reports-detail-note">${data.scoped ? 'Messages from this time entry.' : 'Full recorded thread.'}</p><button type="button" class="reports-btn" data-thread-scope>${data.scoped ? 'Show full thread' : 'Show time-entry messages'}</button></div>` : '';
            const earlier = data.before != null ? `<button type="button" class="reports-btn" data-thread-earlier="${Number(data.before)}">Show earlier messages</button>` : '';
            const messages = (data.messages || []).map((m) => `<section class="reports-thread-message"><p class="reports-thread-role">${m.role === 'user' ? 'You' : 'Assistant'}${m.timestamp ? ` · ${esc(formatInstantSast(m.timestamp))}` : ''}</p><div class="reports-thread-text">${esc(m.text)}</div>${m.truncated ? '<p class="reports-detail-note">Long message shortened for preview.</p>' : ''}</section>`).join('');
            if (before == null) content.innerHTML = `${header}${scope}<div class="reports-thread-messages">${earlier}${messages || '<p class="reports-detail-note">No recorded conversation messages.</p>'}</div>`;
            else {
                content.querySelector('[data-thread-earlier]')?.remove();
                content.querySelector('.reports-thread-messages')?.insertAdjacentHTML('afterbegin', earlier + messages);
            }
            if (data.skipped_oversized && before == null) content.insertAdjacentHTML('beforeend', '<p class="reports-detail-note">Some oversized records could not be previewed.</p>');
            details.dataset.loaded = 'true';
        } catch (error) {
            if (!details.isConnected) return;
            if (before != null) content.innerHTML = previous;
            else content.innerHTML = '';
            content.querySelector('.reports-thread-error')?.remove();
            content.insertAdjacentHTML('beforeend', `<p class="reports-thread-error" role="alert">${esc(error?.message || 'Could not load the original thread.')} <button type="button" class="reports-btn" data-thread-retry${before != null ? `="${Number(before)}"` : ''}>Retry</button></p>`);
        } finally {
            details.dataset.loading = 'false';
            content.removeAttribute('aria-busy');
            content.querySelector('[data-thread-earlier]')?.removeAttribute('disabled');
        }
    }

    function closeBlockDetail() {
        const panel = view.querySelector('#reports-block-detail');
        if (!panel || panel.hidden) return;
        panel.close();
        panel.hidden = true;
        document.body.classList.remove('reports-modal-open');
        detailTrigger?.focus?.();
        detailTrigger = null;
    }

    function openBlockDetail(ids, trigger) {
        const wanted = new Set(ids.map(String));
        const blocks = (state.data?.blocks || []).filter((b) => wanted.has(String(b.id)));
        if (!blocks.length) return;
        const panel = view.querySelector('#reports-block-detail');
        const body = view.querySelector('#reports-detail-body');
        if (!panel || !body) return;
        body.innerHTML = blocks.map(blockDetailHtml).join('');
        detailTrigger = trigger || null;
        document.body.classList.add('reports-modal-open');
        panel.hidden = false;
        panel.showModal();
        body.scrollTop = 0;
        panel.querySelector('.reports-detail-close')?.focus();
    }

    function cursorLabel() {
        const fmt = (d) => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-ZA', { timeZone: 'UTC', day: 'numeric', month: 'short' });
        if (state.calView === 'day') return new Date(`${state.cursor}T12:00:00Z`).toLocaleDateString('en-ZA', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
        const mon = mondayOf(state.cursor);
        return `${fmt(mon)} – ${fmt(addDays(mon, 6))}`;
    }

    function monthHtml(data) {
        const p = data.period;
        const byDay = new Map((data.by_day || []).map((d) => [d.date, d]));
        const max = Math.max(1, ...[...byDay.values()].map((d) => d.seconds));
        const cells = [];
        for (let day = mondayOf(p.start); day <= addDays(mondayOf(p.end), 6); day = addDays(day, 1)) {
            const inside = day >= p.start && day <= p.end;
            const info = byDay.get(day);
            const chips = info ? Object.entries(info.projects).sort((a, b) => b[1] - a[1]) : [];
            const shown = chips.slice(0, 3).map(([id, s]) => `<span class="reports-chip" style="--c:${projectColor(id)}" title="${esc(projectName(id))}: ${esc(formatHours(s))}">${esc(projectName(id))}</span>`).join('');
            const more = chips.length > 3 ? `<small>+${chips.length - 3}</small>` : '';
            const heat = info ? (0.08 + 0.32 * (info.seconds / max)).toFixed(2) : 0;
            cells.push(`<button type="button" class="reports-day${inside ? '' : ' is-outside'}${info ? ' has-work' : ''}" data-cal-day="${day}" style="--heat:${heat}" ${inside ? '' : 'tabindex="-1"'}>
                <span class="reports-day-top"><b>${Number(day.slice(8))}</b>${info ? `<em>${esc(formatHours(info.seconds))}</em>` : ''}</span><span class="reports-day-chips">${shown}${more}</span></button>`);
        }
        const weeks = Math.max(1, cells.length / 7);
        return `<div class="reports-month" style="--weeks:${weeks}">${DAY_NAMES.map((d) => `<span class="reports-dow">${d}</span>`).join('')}${cells.join('')}</div>`;
    }

    const clock = (minutes) => `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(Math.floor(minutes % 60)).padStart(2, '0')}`;

    // One display block per project per stretch of work: overlapping or near (5 min) segments merge,
    // so a thread timer and the Pi run inside it read as one block. The day list keeps every record.
    function mergeByProject(segs) {
        const groups = new Map();
        for (const seg of segs) {
            const id = Number(seg.block.project_id);
            if (!groups.has(id)) groups.set(id, []);
            groups.get(id).push(seg);
        }
        const merged = [];
        for (const [projectId, list] of groups) {
            list.sort((a, b) => a.start - b.start);
            let current = null;
            for (const seg of list) {
                const calendar = ['audited_calendar', 'calendar_draft'].includes(seg.block.source);
                if (!calendar && current && !current.calendar && seg.start <= current.end + 5) {
                    current.end = Math.max(current.end, seg.end);
                    current.items.push(seg.block);
                } else {
                    current = { projectId, name: seg.block.project_name, start: seg.start, end: seg.end, items: [seg.block], calendar };
                    merged.push(current);
                }
            }
        }
        return merged.sort((a, b) => a.start - b.start || b.end - a.end);
    }

    function blocksByDay(data, days) {
        const first = days === 7 ? mondayOf(state.cursor) : state.cursor;
        const list = Array.from({ length: days }, (_, i) => addDays(first, i));
        const perDay = new Map(list.map((d) => [d, []]));
        for (const block of data.blocks || []) {
            if (!block.start_local || !block.end_local) continue;
            for (const seg of segments(block)) if (perDay.has(seg.day)) perDay.get(seg.day).push({ ...seg, block });
        }
        return { first, list, perDay };
    }

    function barsHtml(laid, hourPx, compact) {
        return laid.bars.map((bar) => {
            const top = (bar.start / 60) * hourPx;
            const height = Math.max(10, ((bar.end - bar.start) / 60) * hourPx);
            const descriptions = [...new Set((bar.items || []).map((item) => displayWorkTitle(item).shown).filter(Boolean))];
            const detail = bar.overflow
                ? `${bar.overflowCount} overlapping`
                : descriptions.slice(0, 6).join('\n');
            const inProgress = bar.items?.some((item) => item.calendar_draft_id);
            const tip = `${bar.name || 'Overlapping'} · ${clock(bar.start)}–${clock(bar.end)}${inProgress ? ' · In progress' : ''}\n${detail}`;
            let label = '';
            if (bar.overflow) label = `+${bar.overflowCount}`;
            else if (height >= 22 && bar.name) {
                label = `<b>${esc(bar.name)}</b>`;
                if (height >= 36 && descriptions.length) {
                    label += descriptions.map((description) => `<span>${esc(description)}</span>`).join('');
                }
            }
            const cls = bar.overflow ? 'reports-block is-overflow' : 'reports-block';
            const ids = (bar.items || []).map((item) => item.id).filter((id) => id != null);
            const interactive = ids.length ? ` class="${cls} is-interactive" data-block-ids="${esc(ids.join(','))}" role="button" aria-label="${esc(tip.replaceAll('\n', ' '))}" tabindex="0"` : ` class="${cls}"`;
            return `<div${interactive} style="top:${top}px;height:${height}px;left:calc(${bar.leftPct}% + 2px);width:calc(${bar.widthPct}% - 4px);--c:${projectColor(bar.projectId)}" title="${esc(tip)}">${label}</div>`;
        }).join('');
    }

    function timeGridHtml(data, days, hourPx, zoom) {
        const packed = blocksByDay(data, days);
        const hours = Array.from({ length: 24 }, (_, h) => `<span style="top:${h * hourPx}px">${String(h).padStart(2, '0')}:00</span>`).join('');
        const columns = packed.list.map((day) => {
            const laid = layoutOverlapBars(mergeByProject(packed.perDay.get(day)), days === 7 ? 3 : 4);
            const outside = data.period && (day < data.period.start || day > data.period.end) ? ' is-outside' : '';
            const nowLine = days === 1 ? '<div class="reports-now-line" aria-hidden="true" hidden></div>' : '';
            return `<div class="reports-col${outside}"><div class="reports-col-body" style="height:${24 * hourPx}px">${barsHtml(laid, hourPx, days === 7)}${nowLine}</div></div>`;
        }).join('');
        const heads = packed.list.map((day) => {
            const info = (data.by_day || []).find((d) => d.date === day);
            return `<button type="button" class="reports-col-head" data-cal-day="${day}">${DAY_NAMES[weekday(day)]} ${Number(day.slice(8))}${info ? ` <small>${esc(formatHours(info.seconds))}</small>` : ''}</button>`;
        }).join('');
        return `<div class="reports-timegrid" style="--days:${days};--hour:${hourPx}px" data-hour-px="${hourPx}"><div class="reports-col-heads"><span></span>${heads}</div>
            <div class="reports-timegrid-scroll"><div class="reports-hours" style="height:${24 * hourPx}px">${hours}</div>${columns}</div>${zoom || ''}</div>`;
    }

    function dayListHtml(segs) {
        const seen = new Set();
        const groups = new Map();
        for (const seg of segs || []) {
            const b = seg.block;
            if (!b || seen.has(b.id)) continue;
            seen.add(b.id);
            const slice = daySlice(b, seg.day);
            if (!slice) continue;
            const titles = displayWorkTitle(b);
            const full = [
                titles.original,
                b.start_local && b.end_local ? `${b.start_local.replace('T', ' ')} – ${b.end_local.replace('T', ' ')}` : '',
                b.seconds != null ? `Full record ${formatHours(b.seconds)}` : ''
            ].filter(Boolean).join('\n');
            const name = b.project_name || 'Unassigned';
            if (!groups.has(b.project_id)) groups.set(b.project_id, { name, rows: [] });
            groups.get(b.project_id).rows.push(`<li><button type="button" class="reports-day-item" data-block-id="${esc(b.id)}" title="${esc(full)}">
                <span class="reports-day-time"><time>${esc(slice.range)}</time><small>${esc(formatSlice(slice.seconds))}</small></span>
                <span class="reports-day-copy">${esc(titles.shown || 'Unknown')}${b.calendar_draft_id ? '<small>In progress</small>' : ''}</span>
            </button></li>`);
        }
        return groups.size ? [...groups.values()].map(({ name, rows }) => `<section class="reports-day-project">
            <h3>${esc(name)}</h3><ul class="reports-day-list">${rows.join('')}</ul>
        </section>`).join('') : '<p class="reports-muted">No reviewed work blocks on this day. Short or uncertain activity remains in History.</p>';
    }

    function zoomHtml(level) {
        const index = HOUR_ZOOM_LEVELS.indexOf(level);
        const atMin = index <= 0;
        const atMax = index === HOUR_ZOOM_LEVELS.length - 1;
        return `<div class="reports-hour-zoom" role="group" aria-label="Hour zoom">
            <button type="button" class="reports-icon-button" data-hour-zoom="-1" aria-label="Zoom out hours"${atMin ? ' disabled' : ''}>−</button>
            <span>Hours</span>
            <button type="button" class="reports-icon-button" data-hour-zoom="1" aria-label="Zoom in hours"${atMax ? ' disabled' : ''}>+</button>
        </div>`;
    }

    function dayShellHtml(data) {
        const packed = blocksByDay(data, 1);
        return `<div class="reports-day-shell" data-hour-px="${state.hourPx}">
            <aside class="reports-day-side" aria-label="Activity">${dayListHtml(packed.perDay.get(packed.first))}</aside>
            ${timeGridHtml(data, 1, state.hourPx, zoomHtml(state.hourPx))}
        </div>`;
    }

    view.addEventListener('cancel', (event) => {
        if (event.target.id !== 'reports-block-detail') return;
        event.preventDefault();
        closeBlockDetail();
    }, true);
    view.addEventListener('close', (event) => {
        if (event.target.id === 'reports-block-detail') closeBlockDetail();
    }, true);
    view.addEventListener('toggle', (event) => {
        const details = event.target.closest?.('[data-source-thread]');
        if (details?.open && !details.dataset.loaded) loadSourceThread(details);
    }, true);

    view.addEventListener('click', (event) => {
        const scope = event.target.closest('[data-thread-scope]');
        if (scope) {
            const details = scope.closest('[data-source-thread]');
            if (details.dataset.loading === 'true') return;
            details.dataset.fullThread = details.dataset.fullThread === 'true' ? 'false' : 'true';
            loadSourceThread(details);
            return;
        }
        const sourceAction = event.target.closest('[data-thread-earlier],[data-thread-retry]');
        if (sourceAction) {
            const before = sourceAction.dataset.threadEarlier ?? sourceAction.dataset.threadRetry;
            loadSourceThread(sourceAction.closest('[data-source-thread]'), before === '' ? null : Number(before));
            return;
        }
        if (event.target.closest('[data-manage-projects]')) {
            managingProjects = !managingProjects;
            render();
            view.querySelector('[data-manage-projects]')?.focus();
            return;
        }
        if (event.target.closest('[data-detail-close]')) {
            closeBlockDetail();
            return;
        }
        const blockEl = event.target.closest('[data-block-ids],[data-block-id]');
        if (blockEl) {
            openBlockDetail(blockEl.dataset.blockIds ? blockEl.dataset.blockIds.split(',') : [blockEl.dataset.blockId], blockEl);
            return;
        }
        const zoom = event.target.closest('[data-hour-zoom]');
        if (zoom) {
            if (zoom.disabled) return;
            const direction = Number(zoom.dataset.hourZoom);
            if (state.calView === 'week') state.weekHourPx = stepHourZoom(state.weekHourPx, direction);
            else state.hourPx = stepHourZoom(state.hourPx, direction);
            savePrefs();
            render();
            return;
        }
        const viewButton = event.target.closest('[data-cal-view]');
        if (viewButton) {
            state.calView = viewButton.dataset.calView;
            savePrefs();
            render();
            return;
        }
        const step = event.target.closest('[data-cal-step]');
        if (step) {
            state.cursor = addDays(state.cursor, Number(step.dataset.calStep) * (state.calView === 'week' ? 7 : 1));
            render();
            return;
        }
        const day = event.target.closest('[data-cal-day]');
        if (day) {
            state.cursor = day.dataset.calDay;
            state.calView = 'day';
            savePrefs();
            render();
        }
    });
    view.addEventListener('keydown', (event) => {
        const panel = view.querySelector('#reports-block-detail');
        if (event.key === 'Escape' && panel && !panel.hidden) {
            event.preventDefault();
            closeBlockDetail();
            return;
        }
        if (event.key === 'Tab' && panel?.open) {
            const controls = [...panel.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, a[href]')];
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
            return;
        }
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const blockEl = event.target.closest('[data-block-ids],[data-block-id]');
        if (!blockEl) return;
        event.preventDefault();
        openBlockDetail(blockEl.dataset.blockIds ? blockEl.dataset.blockIds.split(',') : [blockEl.dataset.blockId], blockEl);
    });
    view.addEventListener('submit', async (event) => {
        const form = event.target.closest('[data-calendar-edit]');
        if (!form) return;
        event.preventDefault();
        const block = state.data?.blocks.find((item) => item.id === form.dataset.calendarEdit);
        const button = form.querySelector('button[type="submit"]');
        if (!block || button.disabled) return;
        const errorLine = form.querySelector('[role="alert"]');
        const fields = new FormData(form);
        const time = (name, local, original) => fields.get(name) === local.slice(0, 16)
            ? original : `${fields.get(name)}:00+02:00`;
        button.disabled = true;
        button.textContent = 'Saving…';
        errorLine.hidden = true;
        try {
            await api('/workflows/studio/reports/calendar-entry', { method: 'POST', body: {
                entry_id: block.calendar_event_id || null,
                draft_id: block.calendar_draft_id || null,
                version: block.calendar_version || null,
                original_start: block.entry_start,
                original_end: block.entry_end,
                title: fields.get('title'),
                start_at: time('start_at', block.entry_start_local, block.entry_start),
                end_at: time('end_at', block.entry_end_local, block.entry_end),
                project_id: fields.get('project_id') ? Number(fields.get('project_id')) : null,
            }});
            closeBlockDetail();
            await load();
        } catch (error) {
            errorLine.textContent = error.message || 'Could not save to Tensology.';
            errorLine.hidden = false;
        } finally {
            button.textContent = 'Save to Tensology';
            button.disabled = false;
        }
    });
    view.addEventListener('change', async (event) => {
        const project = event.target.closest('[data-legend-project]');
        if (project) {
            updateProjectSelection(Number(project.dataset.legendProject), project.checked);
            return;
        }
        const select = event.target.closest('[data-assign-project]');
        if (!select) return;
        select.disabled = true;
        try {
            await api('/workflows/studio/reports/assign-project', { method: 'POST', body: {
                source: select.dataset.source,
                ref: select.dataset.ref,
                source_start: select.dataset.sourceStart,
                source_end: select.dataset.sourceEnd,
                project_id: Number(select.value),
                confirm: 'assign-project-manual'
            }});
            await load();
        } catch (error) {
            state.error = `Could not assign the project. ${error.message}`;
            render();
        }
    });
    periodSelect.addEventListener('change', () => {
        state.period = periodSelect.value || 'rolling';
        state.cursor = '';
        load();
    });
    todayButton.addEventListener('click', async () => {
        state.cursor = sastToday();
        // Like selecting a calendar date, Today opens that day's work directly.
        state.calView = 'day';
        savePrefs();
        projectsMenu.hidden = true;
        projectsButton.setAttribute('aria-expanded', 'false');
        const period = state.data?.period || {};
        if (state.loading || state.error || !period.start || state.cursor < period.start || state.cursor > period.end) {
            state.period = 'rolling';
            await load();
        } else {
            render();
        }
        const line = view.querySelector('.reports-now-line:not([hidden])');
        if (line && state.visible) {
            const scroller = line.closest('.reports-timegrid-scroll');
            scroller.scrollTop = line.offsetTop - scroller.clientHeight / 2;
        }
    });
    for (const [id, delta] of [['#reports-period-prev', 1], ['#reports-period-next', -1]]) {
        // Options run newest first, so "previous period" moves down the list.
        root.querySelector(id).addEventListener('click', () => {
            const values = state.options.map((o) => o.value);
            const index = values.indexOf(state.period);
            const next = values[Math.min(values.length - 1, Math.max(0, index + delta))];
            if (next && next !== state.period) {
                state.period = next;
                state.cursor = '';
                renderPeriodSelect();
                load();
            }
        });
    }
    projectsButton.addEventListener('click', () => {
        const open = projectsMenu.hidden;
        if (open) renderProjectsMenu();
        projectsMenu.hidden = !open;
        projectsButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', (event) => {
        if (!projectsMenu.hidden && !event.target.closest('#reports-projects')) {
            projectsMenu.hidden = true;
            projectsButton.setAttribute('aria-expanded', 'false');
        }
    });
    function updateProjectSelection(id, checked) {
        const focusedOption = document.activeElement?.dataset?.project;
        if (Number.isInteger(id)) {
            if (checked) state.projects.add(id);
            else state.projects.delete(id);
        }
        if (!projectsMenu.hidden) {
            renderProjectsMenu();
            if (focusedOption) projectsMenu.querySelector(`[data-project="${focusedOption}"]`)?.focus({ preventScroll: true });
        }
        savePrefs();
        render({ preserveScroll: true });
    }
    projectsMenu.addEventListener('change', (event) => {
        const input = event.target.closest('input[data-project]');
        if (!input) return;
        if (input.dataset.project === 'all') {
            state.projects = new Set(input.checked ? (state.data?.projects || []).map((p) => Number(p.id)) : []);
        }
        updateProjectSelection(Number(input.dataset.project), input.checked);
    });
    buildButton.addEventListener('click', build);

    return {
        enter(tab = 'report') {
            if (!selectionInitialized) {
                const pinned = getPinnedProjectIds();
                state.projects = initialProjectSelection(prefs, pinned ?? (prefs.projects || []));
                selectionInitialized = true;
                savePrefs();
            }
            state.tab = tab;
            state.visible = true;
            scheduleCalendarClock();
            if (!state.data || state.error) load();
            else render();
        },
        refresh: load,
        leave() {
            clearTimeout(clockTimer);
            closeBlockDetail();
            state.visible = false;
            state.generation++;
            state.request?.abort();
            projectsMenu.hidden = true;
        }
    };
}
