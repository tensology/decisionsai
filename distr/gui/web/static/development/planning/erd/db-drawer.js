// Data tab "Databases" drawer (N5 phase 4): list / add / edit / archive local Postgres or MySQL links, set a
// password, test, introspect (refreshes the cached snapshot), review the schema diff, pick tables, apply.
// Passwords: typed into a password field, sent only to PUT .../secret, the field is cleared straight after,
// never kept in module state and never shown again. Apply is always explicit and revision-checked.
import { openDrawer, esc } from '../canvas-chrome.js?v=20261103-ifaces3';

const BASE = '/workflows/studio/plan-workspaces';
export const MAX_APPLY = 300;
const ENGINES = { postgres: 'PostgreSQL', mysql: 'MySQL' };
const STATUS = { new: 'Not tested', ok: 'Connected', auth_failed: 'Sign-in failed', unreachable: 'Unreachable', timeout: 'Timed out',
    non_loopback: 'Not local', driver_missing: 'Driver missing', too_large: 'Too large', db_not_found: 'No such database', failed: 'Failed' };
const dbIcon = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><ellipse cx="8" cy="4" rx="5" ry="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

const linksUrl = (ws) => `${BASE}/${encodeURIComponent(ws)}/db-links`;
const linkUrl = (ws, id, suffix = '') => `${linksUrl(ws)}/${encodeURIComponent(id)}${suffix}`;

/** Rows for the picker: every diff entry with its kind, filtered by a search string (table, schema or entity). */
export function pickerRows(diff, search = '') {
    if (!diff) return [];
    const q = String(search || '').trim().toLowerCase();
    const rows = [
        ...diff.added.map((r) => ({ ...r, kind: 'added' })),
        ...diff.changed.map((r) => ({ ...r, kind: 'changed' })),
        ...diff.removed.map((r) => ({ ...r, kind: r.already_marked ? 'gone' : 'removed' })),
        ...diff.unchanged.map((r) => ({ ...r, kind: 'unchanged' })),
        ...diff.conflicts.map((r) => ({ ...r, kind: 'conflict', disabled: true })),
    ];
    return q ? rows.filter((r) => `${r.schema}.${r.table} ${r.entity || ''}`.toLowerCase().includes(q)) : rows;
}

/** Default selection: what would change the ERD (added, changed, newly removed), capped at MAX_APPLY. */
export function defaultSelection(diff) {
    return new Set(pickerRows(diff).filter((r) => ['added', 'changed', 'removed'].includes(r.kind)).slice(0, MAX_APPLY).map((r) => r.key));
}

export function describeChange(c) {
    if (c.change === 'added') return `+ ${c.column} (${c.to})`;
    if (c.change === 'removed') return `− ${c.column}`;
    if (c.change === 'type') return `${c.column}: ${c.from} → ${c.to}`;
    if (c.change === 'nullable') return `${c.column}: ${c.to ? 'nullable' : 'required'}`;
    if (c.change === 'keys') return `${c.column}: keys ${(c.from || []).join(',') || '–'} → ${(c.to || []).join(',') || '–'}`;
    if (c.change === 'back_in_db') return 'back in the database';
    return c.change;
}

export function databasesButton(options) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'erd-dock-button erd-dock-wide plan-databases-open';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', 'Databases');
    button.title = 'Databases: link a local database and pull its schema';
    button.innerHTML = `${dbIcon}<span class="erd-dock-label">Databases</span>`;
    button.addEventListener('click', () => openDatabasesDrawer({ ...options(), opener: button }));
    return button;
}

export function openDatabasesDrawer({ workspaceId, item = null, api, onApplied = async () => {}, toast = () => {}, opener }) {
    const state = { links: [], secretStore: '', maxLinks: 8, view: 'list', editing: null, diff: null, diffLink: null,
        picked: new Set(), search: '', target: 'own', busy: '', message: '', error: '', passwordFor: null };
    const shell = openDrawer({ title: 'Databases', opener, className: 'plan-db-drawer', html: '<div class="plan-db" aria-live="polite"></div>',
        onClose: () => { state.passwordFor = null; } });
    const body = shell.drawer.querySelector('.plan-db');

    const call = async (label, fn) => {
        state.busy = label; state.error = ''; state.message = ''; render();
        try { return await fn(); }
        catch (e) { state.error = e?.message || 'Something went wrong.'; return null; }
        finally { state.busy = ''; render(); }
    };
    const loadLinks = () => call('Loading…', async () => {
        const data = await api(linksUrl(workspaceId));
        state.links = data.items || []; state.secretStore = data.secret_store || ''; state.maxLinks = data.max_links || 8;
    });
    const link = (id) => state.links.find((l) => String(l.id) === String(id));

    function render() {
        const focusKey = document.activeElement?.dataset?.focusKey;
        if (state.view === 'form') body.innerHTML = formHtml();
        else if (state.view === 'diff') body.innerHTML = diffHtml();
        else body.innerHTML = listHtml();
        const again = focusKey && body.querySelector(`[data-focus-key="${focusKey}"]`);
        if (again) { again.focus({ preventScroll: true }); if (again.type === 'search') again.setSelectionRange(again.value.length, again.value.length); }
    }
    const notice = () => `${state.busy ? `<p class="plan-db-busy" role="status">${esc(state.busy)}</p>` : ''}${state.error ? `<p class="plan-db-error" role="alert">${esc(state.error)}</p>` : ''}${state.message ? `<p class="plan-db-message" role="status">${esc(state.message)}</p>` : ''}`;

    function listHtml() {
        const rows = state.links.map((l) => `<li class="plan-db-link" data-link-id="${esc(l.id)}">
            <div class="plan-db-link-head"><span class="plan-db-swatch" style="background:${/^#[0-9a-f]{6}$/i.test(l.colour || '') ? l.colour : 'var(--studio-line)'}"></span>
                <strong class="plan-db-label">${esc(l.label)}</strong>
                <span class="plan-db-chip status-${esc(l.status)}">${esc(STATUS[l.status] || l.status)}</span></div>
            <p class="plan-db-meta">${esc(ENGINES[l.engine] || l.engine)} · ${esc(l.host)}${l.port ? `:${esc(l.port)}` : ''} / ${esc(l.database)} · ${esc(l.username)}
                · ${l.has_secret ? `password saved (${esc(l.secret_store || 'session')})` : 'no password'}${l.last_introspected_at ? ` · read ${esc(new Date(l.last_introspected_at + (/[zZ]|[+-]\d\d:\d\d$/.test(l.last_introspected_at) ? '' : 'Z')).toLocaleString())}` : ''}</p>
            ${l.last_error ? `<p class="plan-db-link-error">${esc(l.last_error)}</p>` : ''}
            <div class="plan-db-actions" role="group" aria-label="${esc(l.label)} actions">
                <button type="button" class="header-button" data-act="test">Test</button>
                <button type="button" class="header-button" data-act="introspect">Introspect</button>
                <button type="button" class="header-button primary" data-act="diff">Review diff</button>
                <button type="button" class="header-button" data-act="password">${l.has_secret ? 'Change password' : 'Set password'}</button>
                <button type="button" class="header-button" data-act="edit">Edit</button>
                <button type="button" class="header-button danger" data-act="archive">Archive</button>
            </div>
            ${String(state.passwordFor) === String(l.id) ? `<form class="plan-db-secret" data-secret-form>
                <label>Password for ${esc(l.username)}<input type="password" name="secret" autocomplete="new-password" spellcheck="false" data-focus-key="secret" required></label>
                <button type="submit" class="header-button primary">Save password</button><button type="button" class="header-button" data-act="password-cancel">Cancel</button></form>` : ''}
        </li>`).join('');
        return `${notice()}<div class="plan-db-toolbar"><button type="button" class="header-button primary" data-act="add"${state.links.length >= state.maxLinks ? ' disabled' : ''}>Add link</button>
            <span class="plan-db-store">Passwords: ${esc(state.secretStore || 'session only')}</span></div>
            ${state.links.length ? `<ul class="plan-db-links">${rows}</ul>` : `<p class="plan-db-empty">No databases linked yet. Add a local Postgres or MySQL database to pull its tables into an ERD. Read-only; only this Mac (loopback).</p>`}`;
    }

    function formHtml() {
        const l = state.editing || {};
        const v = (k, d = '') => esc(l[k] ?? d);
        return `${notice()}<form class="plan-db-form" data-link-form>
            <label>Engine<select name="engine">${Object.entries(ENGINES).map(([k, n]) => `<option value="${k}"${(l.engine || 'postgres') === k ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
            <label>Label<input name="label" value="${v('label')}" maxlength="60" placeholder="e.g. Shop dev"></label>
            <label>Host<input name="host" value="${v('host', '127.0.0.1')}" required></label>
            <label>Port<input name="port" type="number" min="1" max="65535" value="${v('port')}" placeholder="default"></label>
            <label>Database<input name="database" value="${v('database')}" required></label>
            <label>User<input name="username" value="${v('username')}" required autocomplete="off"></label>
            <label>Schemas (Postgres, comma separated)<input name="schemas" value="${esc((l.schemas || []).join(', '))}" placeholder="public"></label>
            ${l.id ? '' : '<label>Password (optional, saved to the secret store only)<input name="secret" type="password" autocomplete="new-password" spellcheck="false"></label>'}
            <div class="plan-db-actions"><button type="submit" class="header-button primary">${l.id ? 'Save changes' : 'Add link'}</button>
                <button type="button" class="header-button" data-act="back">Cancel</button></div></form>`;
    }

    function diffHtml() {
        const d = state.diff;
        const l = state.diffLink;
        const rows = pickerRows(d, state.search);
        const canMerge = Boolean(item && d && String(item.id) !== String(state.ownItemId ?? ''));
        const s = d?.summary || {};
        return `${notice()}<div class="plan-db-diff-head"><button type="button" class="header-button" data-act="back">← Links</button>
                <strong>${esc(l?.label || '')}</strong><span class="plan-db-snap">snapshot ${d ? esc(new Date(d.snapshot_at).toLocaleString()) : '–'}</span></div>
            ${d ? `<fieldset class="plan-db-target"><legend>Apply to</legend>
                <label><input type="radio" name="target" value="own"${state.target === 'own' ? ' checked' : ''}> ${state.ownItemId ? 'This link’s own ERD' : `New ERD “Data · ${esc(l?.label || '')}”`}</label>
                ${canMerge ? `<label><input type="radio" name="target" value="merge"${state.target === 'merge' ? ' checked' : ''}> Merge into “${esc(item.title)}”</label>` : ''}</fieldset>
            <p class="plan-db-summary"><span class="plan-db-pill kind-added">${s.added || 0} new</span><span class="plan-db-pill kind-changed">${s.changed || 0} changed</span>
                <span class="plan-db-pill kind-removed">${s.removed || 0} gone</span><span class="plan-db-pill">${s.unchanged || 0} same</span>${s.conflicts ? `<span class="plan-db-pill kind-conflict">${s.conflicts} name clash</span>` : ''}</p>
            ${d.truncated ? '<p class="plan-db-message">The database has more tables than one snapshot holds (300). Use a filter when introspecting.</p>' : ''}
            ${d.hand_made?.length ? `<p class="plan-db-hint">${d.hand_made.length} hand-made table(s) in this ERD are never changed.</p>` : ''}
            <div class="plan-db-picker-bar"><input type="search" class="plan-db-search" placeholder="Search tables" aria-label="Search tables" value="${esc(state.search)}" data-focus-key="search">
                <button type="button" class="header-button" data-act="select-shown">Select shown</button><button type="button" class="header-button" data-act="clear">Clear</button></div>
            <ul class="plan-db-picker" aria-label="Tables">${rows.map((r) => `<li class="plan-db-row kind-${r.kind}">
                <label><input type="checkbox" data-key="${esc(r.key)}"${state.picked.has(r.key) ? ' checked' : ''}${r.disabled ? ' disabled' : ''}>
                <span class="plan-db-row-name">${esc(r.schema)}.<strong>${esc(r.table)}</strong></span><span class="plan-db-pill kind-${r.kind}">${esc(r.kind)}</span></label>
                ${r.changes?.length ? `<ul class="plan-db-changes">${r.changes.slice(0, 8).map((c) => `<li>${esc(describeChange(c))}</li>`).join('')}${r.changes.length > 8 ? `<li>… ${r.changes.length - 8} more</li>` : ''}</ul>` : ''}
                ${r.reason ? `<p class="plan-db-hint">${esc(r.reason)}</p>` : ''}</li>`).join('') || '<li class="plan-db-empty">No tables match.</li>'}</ul>
            <footer class="plan-db-apply"><span class="plan-db-count${state.picked.size > MAX_APPLY ? ' over' : ''}">${state.picked.size} selected · max ${MAX_APPLY}</span>
                <button type="button" class="header-button primary" data-act="apply"${!state.picked.size || state.picked.size > MAX_APPLY || state.busy ? ' disabled' : ''}>Apply ${state.picked.size || ''} table${state.picked.size === 1 ? '' : 's'}</button></footer>` : ''}`;
    }

    async function openDiff(l) {
        state.view = 'diff'; state.diffLink = l; state.search = ''; state.diff = null;
        await loadDiff();
    }
    async function loadDiff() {
        const l = state.diffLink;
        const query = state.target === 'merge' && item ? `?item_id=${encodeURIComponent(item.id)}` : '';
        const d = await call('Comparing with the ERD…', () => api(linkUrl(workspaceId, l.id, `/diff${query}`)));
        if (d) { if (state.target === 'own') state.ownItemId = d.item_id; state.diff = d; state.picked = defaultSelection(d); render(); }
    }

    body.addEventListener('click', async (e) => {
        const button = e.target.closest('button[data-act]');
        if (!button || state.busy) return;
        const act = button.dataset.act;
        const l = link(button.closest('[data-link-id]')?.dataset.linkId);
        if (act === 'add') { state.view = 'form'; state.editing = null; render(); body.querySelector('select, input')?.focus(); }
        else if (act === 'edit') { state.view = 'form'; state.editing = { ...l }; render(); body.querySelector('select, input')?.focus(); }
        else if (act === 'back') { state.view = 'list'; state.diff = null; state.error = ''; render(); }
        else if (act === 'password') { state.passwordFor = l.id; render(); body.querySelector('input[name="secret"]')?.focus(); }
        else if (act === 'password-cancel') { state.passwordFor = null; render(); }
        else if (act === 'test') {
            const r = await call(`Testing ${l.label}…`, () => api(linkUrl(workspaceId, l.id, '/test'), { method: 'POST' }));
            await loadLinks();
            if (r) { state.message = `${l.label}: connected${r.server_version ? ` (server ${r.server_version})` : ''}.`; render(); }
        } else if (act === 'introspect') {
            const r = await call(`Reading the schema of ${l.label}…`, () => api(linkUrl(workspaceId, l.id, '/snapshot'), { method: 'POST' }));
            await loadLinks();
            if (r) { state.message = `${l.label}: ${r.tables.length} table(s) saved to the snapshot.`; render(); }
        } else if (act === 'diff') { state.target = 'own'; await openDiff(l); }
        else if (act === 'archive') {
            const ask = window.DecisionsAPI?.confirm ? window.DecisionsAPI.confirm({ title: 'Archive link', message: `Archive “${l.label}”? Its saved password is removed; ERDs stay.`, confirmLabel: 'Archive', danger: true }) : window.confirm(`Archive “${l.label}”?`);
            if (!(await ask)) return;
            await call('Archiving…', () => api(linkUrl(workspaceId, l.id), { method: 'DELETE' }));
            await loadLinks();
        } else if (act === 'select-shown') { pickerRows(state.diff, state.search).forEach((r) => { if (!r.disabled) state.picked.add(r.key); }); render(); }
        else if (act === 'clear') { state.picked.clear(); render(); }
        else if (act === 'apply') {
            const d = state.diff;
            const mergeId = state.target === 'merge' && item ? item.id : null;
            const itemId = mergeId ?? d.item_id;
            const result = await call('Applying…', () => api(linkUrl(workspaceId, state.diffLink.id, '/apply'), {
                method: 'POST', body: { tables: [...state.picked], item_id: itemId, expected_revision: itemId == null ? null : d.revision },
            }));
            if (!result) return;
            toast(result.message || 'Applied.', 'success');
            await onApplied(result.item);
            state.target = mergeId ? 'merge' : 'own';
            await loadDiff();
            if (!state.error) { state.message = `Applied ${result.applied.length} table(s)${result.skipped.length ? `, skipped ${result.skipped.length}` : ''}.`; render(); }
        }
    });
    body.addEventListener('change', async (e) => {
        if (e.target.matches('input[type="checkbox"][data-key]')) {
            if (e.target.checked) state.picked.add(e.target.dataset.key); else state.picked.delete(e.target.dataset.key);
            render();
        } else if (e.target.matches('input[name="target"]')) { state.target = e.target.value; await loadDiff(); }
    });
    body.addEventListener('input', (e) => {
        if (e.target.matches('.plan-db-search')) { state.search = e.target.value; render(); }
    });
    body.addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        if (form.matches('[data-secret-form]')) {
            const field = form.elements.secret;
            const id = state.passwordFor;
            const payload = { secret: field.value };
            field.value = '';                       // cleared before the request returns; never kept in state
            const ok = await call('Saving password…', () => api(linkUrl(workspaceId, id, '/secret'), { method: 'PUT', body: payload }));
            payload.secret = null;
            if (ok) { state.passwordFor = null; await loadLinks(); state.message = 'Password saved.'; render(); }
            return;
        }
        if (form.matches('[data-link-form]')) {
            const f = form.elements;
            const data = { engine: f.engine.value, label: f.label.value.trim(), host: f.host.value.trim(), database: f.database.value.trim(), username: f.username.value.trim(),
                schemas: f.schemas.value.split(',').map((s) => s.trim()).filter(Boolean) };
            if (f.port.value) data.port = Number(f.port.value);
            if (!data.label) delete data.label;
            let secret = f.secret ? f.secret.value : '';
            if (f.secret) f.secret.value = '';
            const editing = state.editing;
            const saved = await call(editing ? 'Saving…' : 'Adding…', () => editing
                ? api(linkUrl(workspaceId, editing.id), { method: 'PATCH', body: data })
                : api(linksUrl(workspaceId), { method: 'POST', body: data }));
            if (saved && secret) await call('Saving password…', () => api(linkUrl(workspaceId, saved.id, '/secret'), { method: 'PUT', body: { secret } }));
            secret = '';
            if (saved) { state.view = 'list'; state.editing = null; await loadLinks(); }
        }
    });
    render();
    loadLinks();
    return shell;
}
