// Owns report loading, filters and cleanup. No dependencies on other feature state.
export function createReports({ api, root, escapeHtml }) {
    let rows = [],
        request = null,
        generation = 0,
        visible = false;
    let loading = false,
        failure = '';
    const result = root.querySelector('#reports-results');
    function render() {
        if (loading || failure) {
            result.textContent = loading ? 'Loading run history...' : failure;
            return;
        }
        const source = root.querySelector('#reports-source').value;
        const status = root.querySelector('#reports-status').value;
        const selected = rows.filter((row) => (source === 'all' || row.kind === source) && (status === 'all' || row.status === status));
        if (!selected.length) {
            result.innerHTML = '<p>No runs match these filters.</p>';
            return;
        }
        result.innerHTML = `<div class="reports-table-scroll"><table><thead><tr><th>Run</th><th>Source</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>${selected.map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.project_name || '')}</small></td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.started_at ? new Date(`${row.started_at}${/[Z+-]\d{2}:?\d{2}$|Z$/.test(row.started_at) ? '' : 'Z'}`).toLocaleString() : 'Unknown')}</td><td>${row.duration_seconds == null ? 'In progress or unavailable' : `${Number(row.duration_seconds)} s`}</td></tr>`).join('')}</tbody></table></div>`;
    }
    async function load() {
        request?.abort();
        request = new AbortController();
        const current = ++generation;
        loading = true;
        failure = '';
        render();
        try {
            const data = await api('/workflows/studio/reports?limit=100', { signal: request.signal });
            if (!visible || current !== generation) return;
            loading = false;
            rows = data.items || [];
            render();
        } catch (error) {
            if (!visible || current !== generation || error.name === 'AbortError') return;
            loading = false;
            failure = `Could not load run history. ${error.message} Use Refresh to retry.`;
            render();
        }
    }
    root.querySelector('#reports-refresh').addEventListener('click', load);
    for (const select of root.querySelectorAll('select')) select.addEventListener('change', render);
    return {
        enter() {
            visible = true;
            load();
        },
        leave() {
            visible = false;
            generation++;
            request?.abort();
        }
    };
}
