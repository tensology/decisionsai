// Background project scan: poll the lightweight status endpoint until the scan settles,
// then hand the refreshed workspace to the caller (planning/index.js -> refreshWorkspace).

export const SCANNING_HINT = 'Scanning project…';

export function scanHint(scan) {
    if (scan?.state === 'scanning') return { text: SCANNING_HINT, kind: 'scanning' };
    if (scan?.state === 'error') return { text: `Project scan failed: ${scan.error || 'unknown error'} Plan still shows the last successful scan.`, kind: 'error' };
    if (scan?.state === 'unconfirmed') return { text: 'Project scan status was not confirmed. Reopen Plan to check again.', kind: 'warning' };
    if (scan?.state === 'done' && scan.coverage === 'none') return { text: 'Project scan could not map this application. Review the saved plan before building tickets.', kind: 'warning' };
    if (scan?.state === 'done' && scan.coverage === 'partial') return { text: 'Project map is partial. Review it before building tickets.', kind: 'warning' };
    return null;
}

export async function watchPlanScan({ api, workspaceId, isCurrent, onSettled, interval = 1500, maxPolls = 240, sleep }) {
    const wait = sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    const base = `/workflows/studio/plan-workspaces/${encodeURIComponent(workspaceId)}`;
    for (let poll = 0; poll < maxPolls; poll += 1) {
        await wait(interval);
        if (!isCurrent()) return 'abandoned';
        let status;
        try { status = await api(`${base}/scan-status`); } catch { continue; }
        if (!isCurrent()) return 'abandoned';
        if (status?.state === 'scanning') continue;
        let workspace = null;
        if (status?.state === 'done') {
            try { workspace = await api(base); } catch { continue; }
            if (!workspace) continue;
        }
        if (!isCurrent()) return 'abandoned';
        onSettled(status || { state: 'idle' }, workspace);
        return status?.state || 'idle';
    }
    if (isCurrent()) onSettled({ state: 'unconfirmed' }, null);
    return 'timeout';
}
