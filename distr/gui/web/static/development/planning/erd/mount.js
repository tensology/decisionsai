// Data tab: the visual ERD canvas is the only editing surface. The Mermaid erDiagram stays the
// stored/synced representation (agents, rebuild, tickets) and is shown only read-only, behind a
// collapsed "Advanced" toggle that is never remembered open.
// Saves go through the existing PATCH /workflows/studio/plan-items/{id} (revision + .mmd sync).
import { addedDbColumns, isErdMermaid } from './parser.js?v=20261103-rows1';
import { advancedButton, availableBottom } from '../canvas-chrome.js?v=20261103-ifaces3';
import { databasesButton } from './db-drawer.js?v=20261103-ifaces3';

export { isErdMermaid };

export const erdIsDirty = (session) => Object.values(session?.erdEditors || {}).some((entry) => entry?.editor?.dirty);

// A live editor is reused across re-renders (keeps undo + unsaved edits); everything that changes per
// mount (item row, tick, Advanced view, callbacks) lives in entry.ctx, refreshed on every mount, so a
// reused editor never saves against a stale item / revision or ticks a detached node.
export async function mountErdItem({ preview, item, session, api, toast = () => {}, isBlocked = () => false, reloadLatest = async () => {}, onSchemaApplied = async () => {} }) {
    session.erdEditors ||= {};
    session.erdMounting ||= {};
    const host = document.createElement('div');
    host.className = 'erd-host';
    preview.append(host);
    // The Mermaid source is read-only and lives in the Advanced drawer (opened from the canvas dock).
    const ctx = { item, toast, isBlocked, reloadLatest, api, onSchemaApplied };   // the brief save tick is drawn by the canvas

    let entry = session.erdEditors[item.id];
    if (entry && !entry.editor.dirty && entry.editor.savedText !== item.content) { entry.editor.destroy(); entry = null; delete session.erdEditors[item.id]; }
    if (!entry) {
        // Guard a second mount racing the first one's dynamic import: both await the same creation.
        session.erdMounting[item.id] ||= (async () => {
            const { createErdEditor } = await import('./canvas.js?v=20261103-rows1');
            const created = { ctx };
            created.editor = createErdEditor({
                text: item.content || '',
                onSave: (content) => saveWith(created.ctx, content),
                onReloadLatest: (content) => created.ctx.reloadLatest(content),
                onViewRows: (query) => viewRows(created.ctx, query),
                aspect: layoutAspect(preview),
            });
            created.editor.el.querySelector('.erd-dock-left')?.append(advancedButton({ text: () => created.ctx.item.content || '' }));
            // N5 phase 4: link local databases and pull their schema into this or a per-link ERD (explicit apply).
            created.editor.el.querySelector('.erd-dock-left')?.append(databasesButton(() => ({
                workspaceId: created.ctx.item.workspace_id, item: created.ctx.item, api: created.ctx.api,
                toast: created.ctx.toast, onApplied: (updated) => created.ctx.onSchemaApplied(updated),
            })));
            session.erdEditors[item.id] = created;
            return created;
        })().finally(() => { delete session.erdMounting[item.id]; });
        entry = await session.erdMounting[item.id];
        if (!host.isConnected) { entry.ctx = ctx; return; }   // a newer render owns the editor now
        entry.ctx = ctx;
        host.append(entry.editor.el);
        requestAnimationFrame(() => entry.editor.mounted());
    } else {
        entry.ctx = ctx;
        host.append(entry.editor.el);
    }
}

const linkBase = (item) => `/workflows/studio/plan-workspaces/${encodeURIComponent(item.workspace_id)}/db-links`;

async function viewRows(ctx, query) {
    const params = new URLSearchParams({ schema: query.schema, table: query.table, page: String(query.page), page_size: String(query.page_size) });
    return ctx.api(`${linkBase(ctx.item)}/${encodeURIComponent(query.link_id)}/rows?${params}`);
}

async function saveWith(ctx, content) {
    if (ctx.isBlocked()) throw new Error('Plan is busy; will retry on your next edit.');
    const { item } = ctx;
    const revision = item.revision_count == null ? null : Number(item.revision_count);   // null = no revision check, never a fake 0
    try {
        for (const column of addedDbColumns(item.content || '', content)) {
            await ctx.api(`${linkBase(item)}/${encodeURIComponent(column.link_id)}/columns`, {
                method: 'POST',
                body: { schema: column.schema, table: column.table, column: column.column, field_type: column.field_type },
            });
        }
        const updated = await ctx.api(`/workflows/studio/plan-items/${encodeURIComponent(item.id)}`, {
            method: 'PATCH', body: { content, expected_revision: revision },
        });
        Object.assign(item, updated);   // same row object the Data tab renders from
        const pendingSync = Boolean(updated?.file_sync_error);
        if (pendingSync) ctx.toast('Saved. The .mmd file sync is pending and will retry.', 'info');
        return updated;
    } catch (error) {
        ctx.toast(`Could not save the ERD: ${error?.message || 'unknown error'}`, 'error');
        throw error;
    }
}

// Width/height hint for the auto-layout so a fresh diagram fits the canvas (the panel floats over it).
function layoutAspect(preview) {
    const width = Math.max(320, (preview.clientWidth || 900) - 24);
    // same rule as the canvas sizing: the window height below the preview minus the toolbar (~60px)
    const height = Math.max(320, Math.round(availableBottom(preview) - Math.max(0, preview.getBoundingClientRect().top) - 76));
    return Math.min(2.4, Math.max(0.5, width / height));
}
