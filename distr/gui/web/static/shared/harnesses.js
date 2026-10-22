// harnesssrc 2026-10-22: the ONE client-side source for harness dropdowns.
// Backend truth: GET /api/harnesses/installed (binary detection, installed only).
// ES module: import { loadInstalledHarnesses, harnessSelectOptions } from '/static/shared/harnesses.js'
// Classic scripts: window.DecisionsHarnesses.loadInstalledHarnesses() / .harnessSelectOptions()
const LABELS = { pi: 'Pi', cursor: 'Cursor', codex: 'Codex', claude_code: 'Claude Code', kilocode: 'Kilo', opencode: 'OpenCode', cline: 'Cline' };
let request = null;

function normalize(id) {
    const raw = String(id || '').trim().toLowerCase();
    if (raw === 'claude' || raw === 'claude-code') return 'claude_code';
    if (raw === 'kilo') return 'kilocode';
    if (raw === 'cursor-agent' || raw === 'cursor_agent') return 'cursor';
    return raw;
}

export function harnessLabel(id) {
    const key = normalize(id);
    return LABELS[key] || (key ? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : '');
}

export function loadInstalledHarnesses({ refresh = false } = {}) {
    if (!request || refresh) {
        request = fetch('/api/harnesses/installed')
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((data) => (data.harnesses || []).map((h) => ({ id: normalize(h.id), name: h.name || harnessLabel(h.id), path: h.path || '' })))
            .catch(() =>
                // Pre-restart fallback: the older status endpoint, installed rows only.
                fetch('/api/projects/cli-backends')
                    .then((r) => (r.ok ? r.json() : { backends: [] }))
                    .then((data) => (data.backends || [])
                        .filter((b) => b.installed === true)
                        .map((b) => ({ id: normalize(b.id), name: harnessLabel(b.id) || b.name, path: b.path || '' })))
                    .catch(() => [])
            );
    }
    return request;
}

export function isInstalled(list, id) {
    const key = normalize(id);
    return (list || []).some((h) => h.id === key);
}

// Option HTML for a <select>. A saved value that is not installed is kept visible as the
// selected value ("… — not installed"), disabled, so it never breaks and is never offered.
export function harnessSelectOptions(list, selected, { leading = '', escape = (v) => String(v) } = {}) {
    const key = normalize(selected);
    const rows = (list || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    let html = leading;
    if (key && !rows.some((h) => h.id === key)) {
        html += `<option value="${escape(key)}" disabled selected>${escape(harnessLabel(key))} — not installed</option>`;
    }
    html += rows.map((h) => `<option value="${escape(h.id)}"${h.id === key ? ' selected' : ''}>${escape(h.name)}</option>`).join('');
    return html;
}

if (typeof window !== 'undefined') {
    window.DecisionsHarnesses = { loadInstalledHarnesses, harnessSelectOptions, harnessLabel, isInstalled, normalize };
}
