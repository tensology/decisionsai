// Owns report loading, filters and cleanup. No dependencies on other feature state.
export function createReports({ api, root, escapeHtml }) {
    let runRows = [],
        timeRows = [],
        costRows = [],
        costRollups = null,
        costDisplay = 'blended',
        request = null,
        generation = 0,
        visible = false,
        tab = 'runs';
    let loading = false,
        failure = '',
        timeFailure = '',
        costFailure = '';
    const result = root.querySelector('#reports-results');
    const heading = root.querySelector('#reports-heading');
    const description = root.querySelector('#reports-description');
    const runFilters = root.querySelector('#reports-run-filters');
    const costFilters = root.querySelector('#reports-cost-filters');

    function formatDuration(seconds) {
        if (seconds == null) return 'Unavailable';
        const value = Number(seconds);
        if (!Number.isFinite(value) || value < 0) return 'Unavailable';
        if (value < 60) return `${Math.round(value)} s`;
        const minutes = Math.floor(value / 60);
        const rem = Math.round(value % 60);
        if (minutes < 60) return rem ? `${minutes}m ${rem}s` : `${minutes}m`;
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        return mins ? `${hours}h ${mins}m` : `${hours}h`;
    }

    function formatWhen(value) {
        if (!value) return 'Unknown';
        // Epoch seconds from ledger, or ISO strings from run/time APIs.
        if (typeof value === 'number' || (/^\d+(\.\d+)?$/.test(String(value)) && Number(value) > 1e9)) {
            return new Date(Number(value) * 1000).toLocaleString();
        }
        const stamp = String(value);
        return new Date(`${stamp}${/[Z+-]\d{2}:?\d{2}$|Z$/.test(stamp) ? '' : 'Z'}`).toLocaleString();
    }

    function formatUsd(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '$0.00';
        if (Math.abs(n) < 0.0001 && n !== 0) return `$${n.toFixed(6)}`;
        return `$${n.toFixed(4)}`;
    }

    function setTab(next) {
        tab = next === 'time' ? 'time' : next === 'costs' ? 'costs' : 'runs';
        root.querySelectorAll('[data-reports-tab]').forEach((button) => {
            const active = button.dataset.reportsTab === tab;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        if (runFilters) runFilters.hidden = tab !== 'runs';
        if (costFilters) costFilters.hidden = tab !== 'costs';
        if (heading) {
            heading.textContent =
                tab === 'time' ? 'Time log' : tab === 'costs' ? 'Costs' : 'Run history';
        }
        if (description) {
            description.textContent =
                tab === 'time'
                    ? 'Closed Development thread time intervals. The live counter appears here after pause.'
                    : tab === 'costs'
                      ? 'Durable per-step cost estimates (provider tokens + local resource). Blended shows one figure; explicit splits token/provider/resource lines. Dates labeled in Africa/Johannesburg (SAST).'
                      : 'Recent workflow, automation and standalone execution records. Linked records may describe stages of the same work.';
        }
        render();
    }

    function renderRuns() {
        const source = root.querySelector('#reports-source').value;
        const status = root.querySelector('#reports-status').value;
        const selected = runRows.filter(
            (row) => (source === 'all' || row.kind === source) && (status === 'all' || row.status === status)
        );
        if (!selected.length) {
            result.innerHTML = '<p>No runs match these filters.</p>';
            return;
        }
        result.innerHTML = `<div class="reports-table-scroll"><table><thead><tr><th>Run</th><th>Source</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>${selected
            .map((row) => {
                const link =
                    row.chat_id != null
                        ? `<a href="/development/threads/${Number(row.chat_id)}/">${escapeHtml(row.name)}</a>`
                        : escapeHtml(row.name);
                return `<tr><td><strong>${link}</strong><small>${escapeHtml(row.project_name || '')}</small></td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(formatWhen(row.started_at))}</td><td>${row.duration_seconds == null ? 'In progress or unavailable' : escapeHtml(formatDuration(row.duration_seconds))}</td></tr>`;
            })
            .join('')}</tbody></table></div>`;
    }

    function renderTime() {
        if (timeFailure) {
            result.textContent = timeFailure;
            return;
        }
        if (!timeRows.length) {
            result.innerHTML = '<p>No closed time intervals yet. Play and pause the thread timer to log entries.</p>';
            return;
        }
        result.innerHTML = `<div class="reports-table-scroll"><table><thead><tr><th>Thread</th><th>Ticket</th><th>Started</th><th>Ended</th><th>Duration</th><th>Source</th></tr></thead><tbody>${timeRows
            .map((row) => {
                const title = escapeHtml(row.thread_title || `Thread ${row.chat_id}`);
                const link =
                    row.chat_id != null
                        ? `<a href="/development/threads/${Number(row.chat_id)}/">${title}</a>`
                        : title;
                const ticket = escapeHtml(row.ticket_title || row.ticket_key || '');
                return `<tr><td><strong>${link}</strong><small>${escapeHtml(row.board_key || '')}</small></td><td>${ticket}</td><td>${escapeHtml(formatWhen(row.started_at))}</td><td>${escapeHtml(formatWhen(row.ended_at))}</td><td>${escapeHtml(formatDuration(row.seconds))}</td><td>${escapeHtml(row.source || '')}</td></tr>`;
            })
            .join('')}</tbody></table></div>`;
    }

    function renderCosts() {
        if (costFailure) {
            result.textContent = costFailure;
            return;
        }
        const sourceFilter = root.querySelector('#reports-cost-source')?.value || 'all';
        const selected = costRows.filter((row) => sourceFilter === 'all' || row.source === sourceFilter);
        const total = costRollups?.total_cost_usd;
        const dayCount = costRollups?.by_day?.length || 0;
        const summary = `<div class="reports-cost-summary"><span>Display: <strong>${escapeHtml(costDisplay)}</strong></span><span>Entries: <strong>${selected.length}</strong></span><span>Total (all): <strong>${escapeHtml(formatUsd(total))}</strong></span><span>SAST days: <strong>${dayCount}</strong></span></div>`;
        if (!selected.length) {
            result.innerHTML = `${summary}<p>No cost ledger entries yet. Workflow steps record usage when cost_ledger_enabled is on.</p>`;
            return;
        }
        const explicit = costDisplay === 'explicit';
        const head = explicit
            ? '<tr><th>When (SAST day)</th><th>Run</th><th>Provider / model</th><th>Tokens</th><th>Provider USD</th><th>Resource USD</th><th>Total</th><th>Source</th></tr>'
            : '<tr><th>When (SAST day)</th><th>Run</th><th>Provider / model</th><th>Cost</th><th>Source</th></tr>';
        const body = selected
            .map((row) => {
                const provider = escapeHtml(`${row.provider || '—'} / ${row.model || '—'}`);
                const runLabel = row.run_id != null ? `run ${row.run_id}` : '—';
                if (explicit) {
                    const tokens = [
                        row.tokens_in != null ? `in ${row.tokens_in}` : null,
                        row.tokens_out != null ? `out ${row.tokens_out}` : null,
                        row.tokens_total != null ? `total ${row.tokens_total}` : null
                    ]
                        .filter(Boolean)
                        .join(', ');
                    return `<tr><td>${escapeHtml(row.day_sast || '')}<small>${escapeHtml(formatWhen(row.recorded_at))}</small></td><td>${escapeHtml(runLabel)}<small>${escapeHtml(row.client_key || '')}</small></td><td>${provider}</td><td>${escapeHtml(tokens || '—')}</td><td>${escapeHtml(formatUsd(row.provider_cost_usd))}</td><td>${escapeHtml(formatUsd(row.resource_cost_usd))}<small>${escapeHtml(row.resource_notes || '')}</small></td><td>${escapeHtml(formatUsd(row.cost_usd))}</td><td>${escapeHtml(row.source || '')}</td></tr>`;
                }
                return `<tr><td>${escapeHtml(row.day_sast || '')}<small>${escapeHtml(formatWhen(row.recorded_at))}</small></td><td>${escapeHtml(runLabel)}<small>${escapeHtml(row.client_key || '')}</small></td><td>${provider}</td><td>${escapeHtml(formatUsd(row.cost_usd))}</td><td>${escapeHtml(row.source || '')}</td></tr>`;
            })
            .join('');
        result.innerHTML = `${summary}<div class="reports-table-scroll"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }

    function render() {
        if (loading || failure) {
            const loadingLabel =
                tab === 'time' ? 'Loading time log...' : tab === 'costs' ? 'Loading costs...' : 'Loading run history...';
            result.textContent = loading ? loadingLabel : failure;
            return;
        }
        if (tab === 'time') renderTime();
        else if (tab === 'costs') renderCosts();
        else renderRuns();
    }

    async function load() {
        request?.abort();
        request = new AbortController();
        const current = ++generation;
        loading = true;
        failure = '';
        timeFailure = '';
        costFailure = '';
        render();
        const display = root.querySelector('#reports-cost-display')?.value || 'blended';
        try {
            const [runs, times, costs] = await Promise.all([
                api('/workflows/studio/reports?limit=100', { signal: request.signal }),
                api('/workflows/studio/reports/time?limit=100', { signal: request.signal })
                    .then((data) => ({ ok: true, data }))
                    .catch((error) => ({ ok: false, error })),
                api(`/workflows/studio/reports/costs?limit=100&rollups=true&display=${encodeURIComponent(display)}`, {
                    signal: request.signal
                })
                    .then((data) => ({ ok: true, data }))
                    .catch((error) => ({ ok: false, error }))
            ]);
            if (!visible || current !== generation) return;
            loading = false;
            runRows = runs.items || [];
            if (times.ok) {
                timeRows = times.data.items || [];
                timeFailure = '';
            } else {
                timeRows = [];
                timeFailure = `Could not load the time log. ${times.error?.message || 'Use Refresh to retry.'}`;
            }
            if (costs.ok) {
                costRows = costs.data.items || [];
                costRollups = costs.data.rollups || null;
                costDisplay = costs.data.display || display;
                costFailure = '';
            } else {
                costRows = [];
                costRollups = null;
                costFailure = `Could not load costs. ${costs.error?.message || 'Use Refresh to retry.'}`;
            }
            render();
        } catch (error) {
            if (!visible || current !== generation || error.name === 'AbortError') return;
            loading = false;
            failure = `Could not load reports. ${error.message} Use Refresh to retry.`;
            render();
        }
    }

    root.querySelector('#reports-refresh').addEventListener('click', load);
    for (const select of root.querySelectorAll('#reports-run-filters select')) {
        select.addEventListener('change', render);
    }
    root.querySelector('#reports-cost-display')?.addEventListener('change', () => {
        if (tab === 'costs') load();
    });
    root.querySelector('#reports-cost-source')?.addEventListener('change', render);
    root.querySelectorAll('[data-reports-tab]').forEach((button) => {
        button.addEventListener('click', () => setTab(button.dataset.reportsTab));
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
        }
    };
}
