// Owns report loading, filters and cleanup. No dependencies on other feature state.
export function createReports({ api, root, escapeHtml }) {
    let runRows = [],
        timeRows = [],
        request = null,
        generation = 0,
        visible = false,
        tab = 'runs';
    let loading = false,
        failure = '';
    const result = root.querySelector('#reports-results');
    const heading = root.querySelector('#reports-heading');
    const description = root.querySelector('#reports-description');
    const runFilters = root.querySelector('#reports-run-filters');

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
        const stamp = String(value);
        return new Date(`${stamp}${/[Z+-]\d{2}:?\d{2}$|Z$/.test(stamp) ? '' : 'Z'}`).toLocaleString();
    }

    function setTab(next) {
        tab = next === 'time' ? 'time' : 'runs';
        root.querySelectorAll('[data-reports-tab]').forEach((button) => {
            const active = button.dataset.reportsTab === tab;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        if (runFilters) runFilters.hidden = tab !== 'runs';
        if (heading) heading.textContent = tab === 'time' ? 'Time log' : 'Run history';
        if (description) {
            description.textContent =
                tab === 'time'
                    ? 'Closed Development thread time intervals. The live counter appears here after pause.'
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

    function render() {
        if (loading || failure) {
            result.textContent = loading ? (tab === 'time' ? 'Loading time log...' : 'Loading run history...') : failure;
            return;
        }
        if (tab === 'time') renderTime();
        else renderRuns();
    }

    async function load() {
        request?.abort();
        request = new AbortController();
        const current = ++generation;
        loading = true;
        failure = '';
        render();
        try {
            const [runs, times] = await Promise.all([
                api('/workflows/studio/reports?limit=100', { signal: request.signal }),
                api('/workflows/studio/reports/time?limit=100', { signal: request.signal }).catch(() => ({ items: [] }))
            ]);
            if (!visible || current !== generation) return;
            loading = false;
            runRows = runs.items || [];
            timeRows = times.items || [];
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
