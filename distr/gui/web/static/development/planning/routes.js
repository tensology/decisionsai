export function planningProviderLabel(provider, routes = {}) {
    const name = provider.name || provider.id;
    const route = routes[provider.id];
    if (!route) return name;
    if (route.runtime_id === 'provider_api') return `${name} (API)`;
    if (route.runtime_id === 'cli_harness' && route.backend_id) {
        const backend = {pi: 'Pi', codex: 'Codex CLI', cursor: 'Cursor CLI'}[route.backend_id] || route.backend_id;
        return `${name} (${backend})`;
    }
    return name;
}
