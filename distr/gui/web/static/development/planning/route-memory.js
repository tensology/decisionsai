// Chat route defaults (provider + model): remembered in this browser per plan workspace and globally
// (localStorage, like the board "last thread" memory). The plan conversation's own saved route still wins;
// a change in the selects applies to this chat and becomes the new default.
const ROUTE_KEY = 'decisions.plan.route';
export function rememberedRoute(storage, workspaceId) {
    try {
        const all = JSON.parse(storage?.getItem(ROUTE_KEY) || '{}');
        return all.workspaces?.[String(workspaceId)] || all.global || null;
    } catch { return null; }
}
export function rememberRoute(storage, workspaceId, route) {
    try {
        const all = JSON.parse(storage?.getItem(ROUTE_KEY) || '{}');
        const value = { provider: String(route.provider || ''), model: String(route.model || '') };
        all.workspaces = { ...(all.workspaces || {}), [String(workspaceId)]: value };
        all.global = value;
        storage?.setItem(ROUTE_KEY, JSON.stringify(all));
    } catch { /* storage unavailable: per-chat only */ }
}
export const routeStore = () => { try { return window.localStorage; } catch { return null; } };
