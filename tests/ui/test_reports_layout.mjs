import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const period = await import('../../distr/gui/web/static/development/reports/period.js');
const reports = await import('../../distr/gui/web/static/development/reports/index.js');

test('Today uses the SAST date at the UTC midnight boundary', () => {
    assert.equal(period.sastToday(new Date('2026-11-09T21:59:59Z')), '2026-11-09');
    assert.equal(period.sastToday(new Date('2026-11-09T22:00:00Z')), '2026-11-10');
    assert.equal(period.sastToday(new Date('2026-11-09T12:00:00-08:00')), '2026-11-09');
});

test('an entry ending at SAST midnight does not create a second zero-minute work row', () => {
    const block = { start_local: '2026-10-24T23:27:02.230000', end_local: '2026-10-25T00:00:00', seconds: 1977.77 };
    assert.equal(period.segments(block).length, 1);
    assert.equal(period.segments(block)[0].day, '2026-10-24');
    assert.equal(period.daySlice(block, '2026-10-25'), null);
    assert.equal(period.daySlice(block, '2026-10-24').range, '23:27–24:00');
});

test('a selected day shows that day slice, not the whole stored span', () => {
    const carry = {
        id: 'b413',
        ref: 'chat:158',
        project_name: 'Unassigned',
        source_label: 'Decisions thread timer',
        title: 'Thread 158',
        start_local: '2026-10-29T14:33:33',
        end_local: '2026-10-30T08:50:29',
        seconds: 65816
    };
    const friday = period.daySlice(carry, '2026-10-30');
    const thursday = period.daySlice(carry, '2026-10-29');
    assert.equal(friday.range, '00:00–08:50');
    assert.ok(friday.seconds < 9 * 3600);
    assert.notEqual(friday.seconds, 65816);
    assert.equal(thursday.range, '14:33–24:00');
    assert.equal(period.daySlice(carry, '2026-10-28'), null);
    const short = period.daySlice({
        start_local: '2026-10-30T09:01:10',
        end_local: '2026-10-30T09:01:30',
        seconds: 20
    }, '2026-10-30');
    assert.equal(short.range, '09:01–09:01');
    assert.equal(short.seconds, 20);
    assert.equal(period.formatSlice(20), '20s');
    const ide = '[DECISIONS DEVELOPMENT THREAD] Work as the active IDE coding agent. Inspect, edit, run checks.';
    const inRange = { title: ide, source_label: 'Pi via Decisions', start_local: '2026-10-30T09:01:05', end_local: '2026-10-30T09:16:00' };
    const beforeRange = { ...inRange, start_local: '2026-10-22T09:00:00', end_local: '2026-10-22T09:05:00' };
    assert.equal(period.displayWorkTitle(inRange).shown, 'Pi via Decisions');
    assert.equal(period.displayWorkTitle(inRange).original, ide);
    assert.match(period.displayWorkTitle(beforeRange).shown, /DECISIONS DEVELOPMENT THREAD/);
    assert.equal(period.displayWorkTitle({
        title: 'Load latest Spoortrack DB',
        source_label: 'Decisions thread timer',
        start_local: '2026-10-30T09:01:00',
        end_local: '2026-10-30T09:03:00'
    }).shown, 'Load latest Spoortrack DB');
    const overlapA = period.daySlice({ start_local: '2026-10-30T09:01:00', end_local: '2026-10-30T09:20:00' }, '2026-10-30');
    const overlapB = period.daySlice({ start_local: '2026-10-30T09:05:00', end_local: '2026-10-30T09:15:00' }, '2026-10-30');
    const later = period.daySlice({ start_local: '2026-10-30T11:00:00', end_local: '2026-10-30T11:20:00' }, '2026-10-30');
    assert.ok(overlapA && overlapB && later);
    assert.notEqual(overlapA.range, overlapB.range);
    assert.ok(later.start > overlapA.end);
});

test('display title removes only repeated exact project prefixes and retains the source title', () => {
    const base = { start_local: '2026-11-10T09:00:00', end_local: '2026-11-10T10:00:00' };
    const cleaned = period.displayWorkTitle({
        ...base,
        project_name: 'Spoortrack [new]',
        title: 'Spoortrack [new] - Spoortrack [new]: Removed customer report title',
        source_label: 'Decisions thread timer'
    });
    assert.equal(cleaned.shown, 'Removed customer report title');
    assert.equal(cleaned.original, 'Spoortrack [new] - Spoortrack [new]: Removed customer report title');
    assert.equal(period.displayWorkTitle({
        ...base,
        project_name: 'Spoortrack [new]',
        title: 'Spoortrack [new] — Cleaned with legacy separator'
    }).shown, 'Cleaned with legacy separator');
    assert.equal(period.displayWorkTitle({
        ...base,
        project_name: 'Spoortrack',
        title: 'Spoortracking: Keep the different project prefix'
    }).shown, 'Spoortracking: Keep the different project prefix');
    assert.equal(period.displayWorkTitle({ ...base, project_name: '[alpha].*', title: '[alpha].* | Literal prefix' }).shown, 'Literal prefix');
    assert.deepEqual(period.displayWorkTitle({ ...base }), { shown: 'Unknown', original: '' });
    assert.deepEqual(period.displayWorkTitle({ ...base, title: 'Thread 34', source_label: 'Pi via Decisions' }), {
        shown: 'Thread 34', original: 'Thread 34'
    });
});

test('hour zoom steps stay on the scale and clamp', () => {
    assert.equal(period.stepHourZoom(period.DEFAULT_HOUR_PX, 1), 48);
    assert.equal(period.stepHourZoom(period.DEFAULT_HOUR_PX, -1), 28);
    assert.equal(period.stepHourZoom(18, -1), 18);
    assert.equal(period.stepHourZoom(88, 1), 88);
    assert.ok(period.DEFAULT_HOUR_PX > period.WEEK_HOUR_PX);
});

test('opening a date brings its actual work into view, including early and late work', () => {
    const early={start_local:'2026-10-25T03:45:00',end_local:'2026-10-25T04:22:00'};
    const late={start_local:'2026-11-05T16:13:00',end_local:'2026-11-05T17:15:00'};
    assert.equal(period.calendarFocusMinute([early,late],['2026-10-25']),195);
    assert.equal(period.calendarFocusMinute([early,late],['2026-11-05']),943);
    assert.equal(period.calendarFocusMinute([early,late],['2026-11-01']),420);
    assert.equal(period.calendarFocusMinute([{start_local:'2026-10-24T23:27:00',end_local:'2026-10-25T00:20:00'}],['2026-10-25']),0);
});

test('week lanes sit beside each other instead of stacking', () => {
    const items = [
        { start: 9 * 60, end: 11 * 60, name: 'A', projectId: 1 },
        { start: 9 * 60 + 15, end: 10 * 60, name: 'B', projectId: 2 },
        { start: 9 * 60 + 30, end: 12 * 60, name: 'C', projectId: 3 },
    ];
    const laid = period.layoutOverlapBars(items, 3);
    assert.equal(laid.bars.length, 3);
    assert.ok(laid.bars.every((bar) => bar.overflow === false));
    const spans = laid.bars.map((bar) => [bar.leftPct, bar.leftPct + bar.widthPct]);
    for (let i = 0; i < spans.length; i++) {
        for (let j = i + 1; j < spans.length; j++) {
            const overlap = Math.min(spans[i][1], spans[j][1]) - Math.max(spans[i][0], spans[j][0]);
            assert.ok(overlap <= 0.01, `lanes ${i} and ${j} overlap by ${overlap}`);
        }
    }
});

test('extra overlapping bars collapse into one overflow lane', () => {
    const items = [0, 1, 2, 3, 4].map((index) => ({
        start: 8 * 60,
        end: 10 * 60,
        name: `Item ${index}`,
        projectId: index + 1,
    }));
    const laid = period.layoutOverlapBars(items, 3);
    const overflow = laid.bars.filter((bar) => bar.overflow);
    const detail = laid.bars.filter((bar) => !bar.overflow);
    assert.equal(detail.length, 2);
    assert.equal(overflow.length, 1);
    assert.equal(overflow[0].overflowCount, 3);
    const spans = laid.bars.map((bar) => [bar.leftPct, bar.leftPct + bar.widthPct]);
    for (let i = 0; i < spans.length; i++) {
        for (let j = i + 1; j < spans.length; j++) {
            const overlap = Math.min(spans[i][1], spans[j][1]) - Math.max(spans[i][0], spans[j][0]);
            assert.ok(overlap <= 0.01);
        }
    }
});

test('per-project sum keeps a cross-project overlap that global clock drops', () => {
    const figures = period.clockFigures({
        by_project: [{ seconds: 3600 }, { seconds: 3600 }],
        totals: { seconds: 3600 }
    });
    assert.equal(figures.perProjectSum, 7200);
    assert.equal(figures.globalClock, 3600);
    assert.ok(figures.perProjectSum > figures.globalClock);
});

test('recorded time and first start fall back to blocks already in the response', () => {
    const project = { project_id: 2, seconds: 3600 };
    const blocks = [{ project_id: 2, seconds: 1800, start_local: '2026-10-10T10:00:00' }, { project_id: 2, seconds: 1800, start_local: '2026-10-11T09:00:00' }];
    assert.equal(period.recordedSeconds(project, blocks), 3600);
    assert.equal(period.recordedSeconds({ project_id: 2, activity_seconds: 999 }, blocks), 999);
    const start = period.firstStartStamp(project, blocks);
    assert.equal(start.stamp, '2026-10-10T10:00:00');
    assert.equal(start.kind, 'returned');
    assert.match(period.firstStartText(start), /may be period-clipped/);
    assert.doesNotMatch(period.firstStartText(start), /clipped to the period\)$/);
    const server = period.firstStartStamp({ project_id: 2, first_start_local: '2026-09-30T22:00:00' }, blocks);
    assert.equal(server.kind, 'contributing');
    assert.match(period.firstStartText(server), /not the project's first-ever start/);
});

test('period ledger uses the day rollup and does not invent a zero for a missing page', () => {
    const view = period.periodLedger({
        items: [{ project_id: 1, day_sast: '2026-10-15', cost_usd: 1.25 }],
        rollups: { total_cost_usd: 4, entry_count: 3, by_day: [{ key: '2026-10-15', cost_usd: 1.25 }, { key: '2026-11-02', cost_usd: 2.75 }] }
    }, { start: '2026-10-01', end: '2026-10-31' });
    assert.equal(view.periodTotal, 1.25);
    assert.equal(view.scope, 'all');
    assert.equal(view.allStored, 4);
    assert.equal(view.truncated, true);
    assert.equal(view.byProject.get(1).partial, true);
    assert.match(period.partialAmount(view.byProject.get(1).cost, true), /partial page/);
    const selected = period.periodLedger({
        items: [
            { project_id: 1, day_sast: '2026-10-10', cost_usd: 1 },
            { project_id: 2, day_sast: '2026-10-10', cost_usd: 9 }
        ],
        rollups: { entry_count: 1000, total_cost_usd: 100, by_day: [{ key: '2026-10-10', cost_usd: 100 }] }
    }, { start: '2026-10-01', end: '2026-10-31' }, [1]);
    assert.equal(selected.scope, 'selected');
    assert.equal(selected.periodTotal, null);
    assert.equal(selected.allProjectPeriodTotal, 100);
    assert.equal(selected.byProject.has(2), false);
    assert.match(period.partialAmount(selected.byProject.get(1).cost, true), /partial page/);
    const empty = period.periodLedger(null, { start: '2026-10-01', end: '2026-10-31' });
    assert.equal(empty.available, false);
});

test('report types use returned sources and do not invent an annotation zero', () => {
    const sources = [
        { source: 'decisions_thread', label: 'Decisions thread timer', state: 'data', seconds: 3600 },
        { source: 'schedule_timer', label: 'Decisions schedule timer', state: 'empty', seconds: 0 },
        { source: 'cursor_local', label: 'Cursor IDE local history', state: 'not_read', seconds: 0 },
        { source: 'pi_local', label: 'Pi (Py) local sessions', state: 'data', seconds: 1200 }
    ];
    const rows = period.workTypeRows(sources);
    assert.equal(rows.find((row) => row.label === 'Thread timer').text, '1h 00m');
    assert.equal(rows.find((row) => row.label === 'Schedule timer').text, 'No records');
    assert.equal(rows.find((row) => row.label === 'Workflow').text, 'Not in this response');
    assert.equal(rows.find((row) => row.label === 'Annotation').text, 'Not in this response');
    assert.equal(rows.some((row) => /cursor|pi|codex/i.test(row.label)), false);
    const harness = period.harnessRows(sources);
    assert.equal(harness.find((row) => row.label === 'Cursor IDE local history').text, 'Not read');
    assert.equal(harness.find((row) => row.label === 'Pi (Py) local sessions').text, '20m');
    assert.equal(harness.some((row) => row.text === '0m' || row.text === '0h 00m'), false);
});

test('history merges run and time lists without calling a new route', () => {
    const merged = reports.mergeHistory(
        { items: [
            { id: 'execution:1', kind: 'execution', name: 'Pi execution', status: 'completed', project_name: 'Alpha', started_at: '2026-10-10T08:00:00', completed_at: '2026-10-10T09:00:00', duration_seconds: 3600 },
            { id: 'workflow:2', kind: 'workflow', name: 'Nightly load', status: 'failed', started_at: '2026-10-11T01:00:00', completed_at: null, duration_seconds: null }
        ] },
        { items: [
            { id: 'time:3', chat_id: 9, thread_title: 'Checkout flow', ticket_title: 'DEV-1', ticket_key: 'DEV-1', started_at: '2026-10-10T08:00:00', ended_at: '2026-10-10T09:00:00', seconds: 3600 }
        ] }
    );
    assert.equal(merged.items.length, 3);
    assert.equal(merged.items.find((row) => row.id === 'execution:1').description, null);
    assert.equal(merged.items.find((row) => row.id === 'time:3').description, 'Checkout flow');
    assert.equal(merged.items.find((row) => row.id === 'time:3').status, null);
    assert.equal(merged.items.find((row) => row.id === 'workflow:2').description, 'Nightly load');
    const capped = reports.mergeHistory(
        { items: Array.from({ length: 100 }, (_, i) => ({ id: `workflow:${i}`, kind: 'workflow', name: `Run ${i}`, status: 'completed', started_at: '2026-10-01T00:00:00' })), limit: 100 },
        { items: Array.from({ length: 100 }, (_, i) => ({ id: `time:${i}`, thread_title: `Timer ${i}`, started_at: '2026-10-02T00:00:00', seconds: 60 })), limit: 100 }
    );
    assert.match(reports.historyCaption(capped.window), /latest 100 only/);
    assert.match(reports.historyCaption(capped.window), /no offset or cursor/);
    assert.equal(reports.HISTORY_RUNS_URL.includes('offset'), false);
    assert.equal(reports.HISTORY_TIME_URL.includes('cursor'), false);
    const boundary = reports.mergeHistory(
        { items: [{ id: 'execution:1', kind: 'execution', name: 'Pi execution', status: 'completed' }, { id: 'execution:1', kind: 'execution', name: 'Pi execution', status: 'completed' }, { id: 'workflow:1', kind: 'workflow', name: 'Same number', status: 'completed' }] },
        { items: [{ id: 'time:1', thread_title: 'Timer' }, { id: 'execution:1', thread_title: 'Not a run' }] }
    );
    assert.equal(boundary.items.filter((row) => row.id === 'execution:1').length, 1);
    assert.equal(boundary.items.some((row) => row.id === 'workflow:1'), true);
    assert.equal(boundary.items.some((row) => row.id === 'time:1'), true);
    const missing = reports.mergeHistory({ items: [{ name: 'A' }, { name: 'B' }] }, { items: [{ thread_title: 'C' }] });
    assert.equal(missing.items.length, 3);
});

test('utc instants convert to Johannesburg, including the date boundary', () => {
    assert.equal(period.formatInstantSast('2026-11-07T12:00:00Z'), '2026-11-07 14:00 SAST');
    assert.equal(period.formatInstantSast('2026-11-07T22:30:00Z'), '2026-11-08 00:30 SAST');
    assert.equal(period.formatInstantSast('2026-10-10T10:00:00'), '');
    assert.equal(period.formatSast('2026-10-10T10:00:00'), '2026-10-10 10:00 SAST');
});

test('report load does not call the cost route and labels stats cells', async () => {
    const calls = [];
    const nodes = new Map();
    const blank = () => ({
        innerHTML: '', textContent: '', hidden: true, dataset: {}, disabled: false, value: '',
        addEventListener() {}, setAttribute() {}, classList: { toggle() {} },
        querySelector() { return null; }, querySelectorAll() { return []; }
    });
    const root = {
        dataset: {},
        querySelector(selector) {
            if (!nodes.has(selector)) nodes.set(selector, blank());
            return nodes.get(selector);
        },
        querySelectorAll() { return []; }
    };
    globalThis.localStorage = { getItem() { return '{}'; }, setItem() {} };
    globalThis.document = { addEventListener() {} };
    const report = period.createPeriodReport({
        getPinnedProjectIds: () => [1],
        root,
        escapeHtml: (value) => String(value ?? ''),
        api: async (url) => {
            calls.push(String(url));
            return {
                period: { start: '2026-10-01', end: '2026-10-31' },
                period_options: [{ value: 'rolling', label: 'Default' }],
                projects: [{ id: 1, name: 'Alpha' }],
                project_ids: [],
                by_project: [{ project_id: 1, name: 'Alpha', seconds: 3600, days: 1 }],
                totals: { seconds: 3600, active_days: 1, projects: 1, blocks: 1 },
                blocks: [{ project_id: 1, seconds: 3600, start_local: '2026-10-10T10:00:00' }],
                sources: [],
                by_day: [],
                saved: { created_at: '2026-11-07T12:00:00Z' }
            };
        }
    });
    report.enter('stats');
    await new Promise((resolve) => setImmediate(resolve));
    assert.ok(calls.length > 0);
    assert.ok(calls.every((url) => !url.includes('/costs')));
    assert.equal(nodes.get('#reports-built').textContent, 'Last built 2026-11-07 14:00 SAST');
    const html = nodes.get('#reports-stats-view').innerHTML;
    assert.match(html, /data-label="Project clock"/);
    assert.match(html, /data-label="Earliest block start"/);
    assert.match(html, /data-label="Recorded time"/);
    assert.match(html, /Costs for this period are not in this view/);
    assert.equal(html.includes('Not loaded with this report'), false);
    assert.equal(html.includes('creates ledger tables'), false);
    assert.equal(html.includes('totals.seconds'), false);
    assert.equal(html.includes('by_project.seconds'), false);
    assert.equal(html.includes('$0.00'), false);
    assert.match(html, /not the project's first-ever start/);
    const reportHtml = nodes.get('#reports-period-view').innerHTML;
    const calendarAt = reportHtml.indexOf('reports-calendar-card');
    const chartAt = reportHtml.indexOf('reports-pie-card');
    assert.ok(calendarAt >= 0 && chartAt > calendarAt, 'calendar section is before the project chart');
    assert.match(reportHtml, /data-cal-view="day"/);
    assert.match(reportHtml, /data-cal-view="week"/);
    assert.match(reportHtml, /data-cal-view="month"/);
    assert.match(reportHtml, /<hr class="reports-pie-rule">/);
    assert.equal(reportHtml.includes('in each project on this chart'), false);
    assert.match(reportHtml, /project sum/);
});

test('filtering keeps the loaded project catalog and recalculates overlapping time and costs', () => {
    const entry = (project_id, start, end, seconds) => ({ project_id, start_local: start, end_local: end, seconds });
    const blocks = [
        entry(1, '2026-11-10T23:30:00', '2026-11-11T00:30:00', 3600),
        entry(2, '2026-11-11T00:00:00', '2026-11-11T01:00:00', 3600),
        entry(3, '2026-11-11T01:00:00', '2026-11-11T03:00:00', 7200),
    ];
    const projects = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const data = {
        blocks, projects,
        by_project: [{ project_id: 1 }, { project_id: 2 }, { project_id: 3 }],
        by_day: [
            { date: '2026-11-10', projects: { 1: 1800 } },
            { date: '2026-11-11', projects: { 1: 1800, 2: 3600, 3: 7200 } },
        ],
        costs: { available: true, by_project: [
            { project_id: 1, entries: 1, cost_usd: 1 },
            { project_id: 3, entries: 5, cost_usd: 9 },
        ] },
    };
    const selected = period.selectReportProjects(data, new Set([1, 2]));
    assert.deepEqual(selected.blocks, blocks.slice(0, 2));
    assert.equal(selected.projects, projects);
    assert.deepEqual(selected.by_day.map((day) => day.seconds), [1800, 3600]);
    assert.deepEqual(selected.totals, { active_days: 2, projects: 2, blocks: 2, seconds: 5400, activity_seconds: 7200 });
    assert.equal(selected.costs.total_cost_usd, 1);
    assert.equal(period.selectReportProjects(data, new Set()).blocks.length, 0);
    assert.equal(period.selectReportProjects(data, new Set([1, 2, 3])), data);
});

test('desktop grid keeps the calendar on the left and entries in order within each project', () => {
    const css = readFileSync(new URL('../../distr/gui/web/static/development/reports/styles.css', import.meta.url), 'utf8');
    assert.match(css, /grid-template-areas:\s*"calendar projects"/);
    assert.match(css, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s*minmax\(340px,\s*400px\)/);
    assert.match(css, /grid-template-areas:\s*"calendar"\s*"projects"/);
    assert.match(css, /\.reports-segs[\s\S]*font-size:\s*14px/);
    assert.match(css, /\.reports-segs[\s\S]*min-width:\s*min\(100%,\s*248px\)/);
    const periodSource = readFileSync(new URL('../../distr/gui/web/static/development/reports/period.js', import.meta.url), 'utf8');
    const listStart = periodSource.indexOf('function dayListHtml');
    const listEnd = periodSource.indexOf('function zoomHtml');
    const listSource = periodSource.slice(listStart, listEnd);
    assert.equal(listSource.includes('.sort('), false);
    const app = readFileSync(new URL('../../distr/gui/web/static/development/app.js', import.meta.url), 'utf8');
    const html = readFileSync(new URL('../../distr/gui/web/templates/development/index.html', import.meta.url), 'utf8');
    const shell = readFileSync(new URL('../../distr/gui/web/static/development/styles.css', import.meta.url), 'utf8');
    const reports = readFileSync(new URL('../../distr/gui/web/static/development/reports/index.js', import.meta.url), 'utf8');
    assert.match(app, /reports\/index\.js\?v=[^'\"]+/);
    assert.match(app, /view-switch\.js\?v=20261107-nav68/);
    assert.match(app, /terminals\/index\.js\?v=[^'"]+/);
    assert.match(reports, /period\.js\?v=[^'\"]+/);
    assert.match(shell, /reports\/styles\.css\?v=[^'\"]+/);
    assert.match(html, /styles\.css\?v=[^'\"]+/);
    assert.match(html, /app\.js\?v=[^'\"]+/);
    const dayShell = periodSource.slice(periodSource.indexOf('function dayShellHtml'), periodSource.indexOf('view.addEventListener'));
    assert.equal((dayShell.match(/zoomHtml/g) || []).length, 1);
    assert.match(dayShell, /timeGridHtml\(data, 1, state\.hourPx, zoomHtml\(state\.hourPx\)\)/);
    assert.match(periodSource, /timeGridHtml\(data, 7, state\.weekHourPx, zoomHtml\(state\.weekHourPx\)\)/);
    assert.match(css, /\.reports-day-shell \{[^}]*grid-template-columns:\s*minmax\(280px,\s*1\.25fr\)\s*minmax\(240px,\s*1fr\)/);
    assert.match(css, /@container \(max-width: 640px\) \{\s*\.reports-day-shell \{ grid-template-columns: minmax\(0, 1fr\)/);
});

test('history uses a real chat join and does not invent missing threads or durations', () => {
    const catalog = reports.historyCatalog({
        chats: [
            { id: 598, title: 'Daily work declaration and approvals', project_id: 3, development_execution: { execution_session_id: 954 } },
            { id: 591, title: 'Email action items', project_id: null, development_execution: { execution_session_id: 953 } },
            { id: 584, title: 'Load latest Spoortrack DB', project_id: 10, development_execution: { execution_session_id: 950 } }
        ]
    }, [{ id: 3, name: 'Tensology' }, { id: 10, name: 'Spoortrack' }]);
    const merged = reports.mergeHistory({
        items: [
            { id: 'execution:954', kind: 'execution', name: 'pi execution', status: 'completed', project_id: 3, project_name: 'Tensology', chat_id: null, run_id: 954, started_at: '2026-11-07T09:03:13', completed_at: '2026-11-07T09:03:34', duration_seconds: 20 },
            { id: 'execution:953', kind: 'execution', name: 'pi execution', status: 'failed', project_id: -591, project_name: null, chat_id: null, run_id: 953, started_at: '2026-11-07T08:08:38', completed_at: '2026-11-07T08:08:49', duration_seconds: 11 },
            { id: 'automation:576', kind: 'automation', name: 'Load latest Spoortrack DB', status: 'failed', project_id: 10, project_name: 'Spoortrack', started_at: '2026-11-07T08:08:31', completed_at: '2026-11-07T16:35:18', duration_seconds: null }
        ]
    }, {
        items: [
            { id: 'time:131', chat_id: 598, thread_title: 'Daily work declaration and approvals', started_at: '2026-11-07T09:03:10', ended_at: '2026-11-07T09:03:37', seconds: 26, source: 'auto_pause' },
            { id: 'time:1746', chat_id: 1746, thread_title: 'Thread 1746', started_at: '2026-10-01T00:00:00', ended_at: '2026-10-01T00:10:00', seconds: 600, source: 'auto_pause' }
        ]
    });
    const rows = reports.applyHistoryContext(merged.items, catalog);
    const exec = rows.find((row) => row.id === 'execution:954');
    assert.equal(exec.description, 'Daily work declaration and approvals');
    assert.equal(exec.chat_id, 598);
    assert.equal(exec.project_name, 'Tensology');
    assert.equal(exec.duration_seconds, 20);
    const negative = rows.find((row) => row.id === 'execution:953');
    assert.equal(negative.description, 'Email action items');
    assert.equal(negative.project_id, null);
    assert.equal(negative.project_name, null);
    assert.equal(reports.historyProjectLabel(negative), 'Unassigned');
    const automation = rows.find((row) => row.id === 'automation:576');
    assert.equal(automation.duration_seconds, null);
    assert.equal(automation.description, 'Load latest Spoortrack DB');
    const timer = rows.find((row) => row.id === 'time:131');
    assert.equal(timer.project_id, 3);
    assert.equal(timer.project_name, 'Tensology');
    assert.equal(timer.status, 'Recorded');
    const missing = rows.find((row) => row.id === 'time:1746');
    assert.equal(missing.description, null);
    assert.equal(missing.status, 'Recorded');
    assert.equal(rows.length, merged.items.length);
});

test('costs tab describes a 100-row page and an all-rows total', () => {
    assert.equal(
        reports.COSTS_TAB_NOTE,
        'Latest ledger rows (up to 100), across all dates and projects. Source filters apply only to loaded rows; the total covers all stored rows. The report and Stats do not load this list. Opening Costs does.'
    );
    const page = [{ cost_usd: 0.02 }, { cost_usd: 0.03 }];
    assert.equal(reports.costsDisplayedTotal({ total_cost_usd: 0.9398 }, page), 0.9398);
    assert.equal(page.reduce((sum, row) => sum + row.cost_usd, 0) === 0.9398, false);
});

test('history and cost dates are SAST day-month-year, and missing ends stay honest', () => {
    assert.equal(reports.formatSastWhen('2026-11-07T15:50:00Z'), '7 Nov 2026, 17:50 SAST');
    assert.equal(reports.formatSastWhen('2026-11-07'), '7 Nov 2026');
    assert.equal(reports.formatSastWhen('2026-11-07T22:30:00Z'), '8 Nov 2026, 00:30 SAST');
    assert.equal(reports.historyEndLabel({ status: 'running', ended_at: null }), 'In progress');
    assert.equal(reports.historyEndLabel({ status: 'completed', ended_at: null }), 'Unknown');
    assert.equal(reports.historyEndLabel({ status: 'failed', ended_at: '2026-11-07T15:50:00Z' }), '7 Nov 2026, 17:50 SAST');
    assert.equal(reports.historyProjectLabel({ project_id: null, project_name: '' }), 'Unassigned');
    assert.equal(reports.historyProjectLabel({ project_id: 4, project_name: '' }), 'Project unavailable');
    assert.equal(reports.historyProjectLabel({ project_name: '' }), 'Project unavailable');
    assert.equal(reports.providerModelLabel('—', 'auto'), 'Provider not recorded, chosen automatically');
    assert.equal(reports.providerModelLabel('', ''), 'Provider and model not recorded');
    assert.equal(reports.providerModelLabel('openrouter', 'auto'), 'openrouter, chosen automatically');
});

test('adjacent intervals share a lane', () => {
    const laid = period.layoutOverlapBars([
        { start: 60, end: 120, name: 'Early', projectId: 1 },
        { start: 120, end: 180, name: 'Next', projectId: 1 },
    ], 3);
    assert.equal(laid.laneCount, 1);
    assert.equal(laid.bars[0].leftPct, laid.bars[1].leftPct);
});

 test("project preferences distinguish pinned defaults, saved subsets and explicit empty", () => {
    assert.deepEqual([...period.initialProjectSelection({}, [2, 9])], [2, 9]);
    assert.deepEqual([...period.initialProjectSelection({ projects: [] }, [2, 9])], [2, 9]);
    assert.deepEqual([...period.initialProjectSelection({ projects: [3] }, [2, 9])], [3]);
    assert.deepEqual([...period.initialProjectSelection({ projects: [], projectFilterVersion: 2 }, [2, 9])], []);
    assert.deepEqual([...period.initialProjectSelection({ projects: [0, 3, 3], projectFilterVersion: 2 }, [2, 9])], [0, 3]);
 });

test('non-billable entries are hidden from the calendar and its totals by default, and can be shown', () => {
    const data = {
        projects: [{ id: 1, name: 'Spoortrack' }, { id: 2, name: 'Plan My Charter' }],
        by_project: [{ project_id: 1, name: 'Spoortrack', seconds: 3600 }, { project_id: 2, name: 'Plan My Charter', seconds: 7200 }],
        by_day: [{ date: '2026-11-04', seconds: 10800, projects: { 1: 3600, 2: 7200 } }],
        totals: { seconds: 10800, activity_seconds: 10800, blocks: 2, projects: 2, active_days: 1 },
        blocks: [
            { id: 'calendar-1', project_id: 1, project_name: 'Spoortrack', invoice_status: 'ready', seconds: 3600,
              start_local: '2026-11-04T09:00:00', end_local: '2026-11-04T10:00:00' },
            { id: 'calendar-2', project_id: 2, project_name: 'Plan My Charter', invoice_status: 'non_billable', seconds: 7200,
              start_local: '2026-11-04T16:00:00', end_local: '2026-11-04T18:00:00' },
        ],
    };
    const hidden = period.billableView(data);
    assert.deepEqual(hidden.blocks.map((b) => b.id), ['calendar-1']);
    assert.deepEqual(hidden.by_project.map((r) => [r.project_id, r.seconds]), [[1, 3600]]);
    assert.deepEqual(hidden.by_day, [{ date: '2026-11-04', seconds: 3600, projects: { 1: 3600 } }]);
    assert.equal(hidden.totals.seconds, 3600);
    assert.equal(hidden.totals.blocks, 1);
    assert.deepEqual(hidden.non_billable, { blocks: 1, seconds: 7200, shown: false });
    assert.equal(data.blocks.length, 2, 'source data is not mutated');
    const shown = period.billableView(data, true);
    assert.equal(shown.blocks.length, 2);
    assert.equal(shown.totals.seconds, 10800);
    assert.deepEqual(shown.non_billable, { blocks: 1, seconds: 7200, shown: true });
});

test('the calendar offers a non-billable toggle and remembers the choice', () => {
    const source = readFileSync(new URL('../../distr/gui/web/static/development/reports/period.js', import.meta.url), 'utf8');
    assert.match(source, /showNonBillable: prefs\.showNonBillable === true/);
    assert.match(source, /data-show-nonbillable/);
    assert.match(source, /selectReportProjects\(billableView\(state\.data, state\.showNonBillable\), state\.projects\)/);
});

test("pinned defaults resolve linked projects and board aliases without duplicates", async () => {
    const { createSidebar } = await import("../../distr/gui/web/static/development/sidebar/index.js");
    const previous = globalThis.window;
    globalThis.window = { location: { search: "" }, localStorage: { getItem(key) {
        return key === "decisions-development-pinned-boards" ? JSON.stringify(["decisions:18", "decisions:8", "jira:35", "decisions:1"]) : null;
    } } };
    try {
        const sidebar = createSidebar({ context: { boards: [
            { key: "decisions:18", project_id: 9 },
            { key: "jira:35", aliases: ["decisions:8"], project_id: 4 },
            { key: "decisions:1", project_id: null },
            { key: "decisions:99", project_id: 10 }
        ] }, actions: {}, el() {} });
        assert.deepEqual(sidebar.pinnedProjectIds(), [9, 4]);
    } finally {
        if (previous === undefined) delete globalThis.window;
        else globalThis.window = previous;
    }
});
