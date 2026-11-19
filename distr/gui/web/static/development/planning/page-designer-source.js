import { parseWireframe } from './wireframe.js?v=20261119-page-designer';

export const WIDGETS = {
    row: 'row',
    stack: 'stack',
    heading: 'heading "Heading"',
    text: 'text "Text"',
    button: 'button "Button" variant=primary',
    input: 'input "Text field"',
};
const layouts = new Set(['screen', 'content', 'main', 'section', 'row', 'stack', 'card', 'form', 'grid']);

export function designPage(source, pageIndex) {
    const parsed = parseWireframe(source);
    const screen = parsed.sourceScreens[pageIndex];
    const layers = new Map();
    function visit(node, parent = null, depth = 0) {
        layers.set(node.line, { node, parent, depth });
        node.children.forEach(child => visit(child, node, depth + 1));
    }
    if (screen) visit(screen);
    const root = screen?.children.find(child => child.type === 'content') || screen;
    return { id: screen?.id, root, layers, valid: Boolean(screen?.line) && !parsed.diagnostics.some(entry => entry.severity === 'error') };
}

export function insertionLayout(page, line) {
    let entry = page.layers.get(line);
    while (entry && entry.node.type !== 'screen') {
        if (layouts.has(entry.node.type)) return entry.node;
        entry = page.layers.get(entry.parent?.line);
    }
    return page.root;
}

function endOf(node) {
    const last = node.children.at(-1);
    return last ? endOf(last) : node.line;
}

// Last source line (1-based) owned by a node: its subtree plus any comment or
// "#|" annotation lines indented under it, so notes travel with their widget.
function blockEnd(lines, node) {
    let end = endOf(node);
    while (end < lines.length) {
        const next = lines[end];
        const indent = next.match(/^ */)[0].length;
        if (!next.trimStart().startsWith('#') || indent < node.column) break;
        end++;
    }
    return end;
}

export function addWidget(source, layout, widget) {
    if (!layout || !Object.hasOwn(WIDGETS, widget)) return null;
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const lines = source.split(newline);
    let end = endOf(layout);
    while (/^\s*#\|/.test(lines[end] || '')) end++;
    lines.splice(end, 0, ' '.repeat(layout.column + 1) + WIDGETS[widget]);
    return { source: lines.join(newline), line: end + 1 };
}

export function editWidgetLabel(source, node, label) {
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const lines = source.split(newline);
    const attrs = Object.entries(node.attrs).filter(([key]) => key !== 'label');
    const tokens = [node.type, JSON.stringify(label.trim()), ...attrs.map(([key, value]) => `${key}=${JSON.stringify(value)}`)];
    lines[node.line - 1] = ' '.repeat(node.column - 1) + tokens.join(' ');
    return { source: lines.join(newline), line: node.line };
}

export function moveWidget(source, node, layout, index) {
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const lines = source.split(newline);
    const first = node.line - 1;
    const end = blockEnd(lines, node);
    const before = layout.children[index];
    const destination = before ? before.line - 1 : blockEnd(lines, layout);
    if (destination >= first && destination <= end) return null;
    const offset = layout.column + 2 - node.column;
    const block = lines.splice(first, end - first).map(line => {
        if (!line.trim()) return line;
        return offset >= 0 ? ' '.repeat(offset) + line : line.slice(Math.min(-offset, line.match(/^ */)[0].length));
    });
    const insert = destination > end ? destination - block.length : destination;
    lines.splice(insert, 0, ...block);
    return { source: lines.join(newline), line: insert + 1 };
}

export function removeWidget(source, node, parent) {
    if (!node || !parent || node.type === 'screen' || node.type === 'content') return null;
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const lines = source.split(newline);
    lines.splice(node.line - 1, blockEnd(lines, node) - node.line + 1);
    const siblings = parent.children;
    const index = siblings.indexOf(node);
    const neighbour = siblings[index - 1] || null;
    return { source: lines.join(newline), line: neighbour ? neighbour.line : parent.line };
}
