// Pure projection of expanded DSL screens. It never invents pages or transitions.
export function wireframeSitemap(documents) {
    const pages = documents.flatMap(({id, screens}) => screens.map(screen => ({artifactId: id, screen})));
    if (pages.length > 200) throw new Error('The sitemap supports at most 200 pages.');
    const label = value => String(value).slice(0, 160).replace(/[&<>"\n\r#]/g, char => `#${char.codePointAt(0)};`);
    const routeKey = value => {
        if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return null;
        return value.split(/[?#]/)[0].replace(/\/+$/, '') || '/';
    };
    const routes = new Map();
    const diagnostics = [];
    const lines = ['flowchart TD'];
    const nodes = pages.map(({artifactId, screen}, index) => {
        const key = `p${index}`;
        const route = routeKey(screen.attrs.route);
        if (route) routes.set(route, [...(routes.get(route) || []), key]);
        lines.push(`  ${key}["${label(screen.label || screen.id)}${route ? ` (${label(route)})` : ''}"]`);
        return {key, artifactId, screen};
    });
    for (const [route, keys] of routes) {
        if (keys.length > 1) diagnostics.push(`Multiple pages define ${route}. Navigation to that route is ambiguous.`);
    }
    const edges = new Set();
    const missing = new Map();
    for (const page of nodes) {
        const visit = node => {
            if (node !== page.screen) {
                const route = routeKey(node.attrs.route || node.attrs.href);
                if (route) {
                    const matches = routes.get(route) || [];
                    let target = matches.length === 1 ? matches[0] : null;
                    if (!target) {
                        if (!missing.has(route)) {
                            if (missing.size >= 200) throw new Error('Too many undefined routes to display in one sitemap.');
                            const key = `u${missing.size}`;
                            missing.set(route, key);
                            lines.push(`  ${key}["${label(route)} (${matches.length ? 'ambiguous' : 'not defined'})"]`);
                            lines.push(`  style ${key} stroke-dasharray:5 5`);
                            if (!matches.length) diagnostics.push(`No wireframe defines ${route}.`);
                        }
                        target = missing.get(route);
                    }
                    edges.add(`  ${page.key} --> ${target}`);
                    if (edges.size > 800) throw new Error('Too many navigation links to display in one sitemap.');
                }
            }
            node.children.forEach(visit);
        };
        visit(page.screen);
    }
    return {source: [...lines, ...edges].join('\n'), diagnostics, pages: nodes};
}
