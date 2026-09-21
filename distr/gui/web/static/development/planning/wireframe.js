/* Wireframe DSL v1. Two spaces per level; labels and attribute values may be
 * quoted (backslash escapes the next character). No expressions are evaluated.
 * parseWireframe retains the first-screen shape for existing consumers and adds
 * screens/templates/diagnostics. renderWireframe(source, title, pageId?) renders
 * one page. bind/binding/requirement/route are annotations, not runtime code.
 * Diagnostics: {severity: 'error'|'warning', code, line, column, message, evidence}.
 * Errors are fail-closed at render time. Templates use named slot/content nodes;
 * overriding content replaces a slot's default children, including in inheritance.
 */
const LIMIT = { source: 100000, nodes: 1000, depth: 32, expanded: 2000, label: 2048, height: 200000 };
const containers = new Set('screen template slot content main nav sidebar header section stack row grid card form list item table menu tabs tab modal dialog alert toast draglist'.split(' '));
const leaves = 'field input textarea select dropdown option checkbox radio toggle slider date calendar button link column badge avatar image divider spacer chart progress text heading'.split(' ');
const types = new Set([...containers, ...leaves]);
const common = 'id label width height gap padding bind binding requirement disabled'.split(' ');
const attributes = {
    screen: 'device uses route', template: 'uses', slot: 'name', content: 'name',
    grid: 'columns', row: '', sidebar: '', input: 'type name value placeholder required min max step',
    field: 'type name value placeholder required min max step', textarea: 'name value placeholder required rows',
    select: 'name value placeholder required options', dropdown: 'name value placeholder required options',
    option: 'value selected', checkbox: 'name checked value required', radio: 'name checked value required',
    toggle: 'name checked', slider: 'name value min max step', date: 'name value required min max',
    calendar: 'value', button: 'variant primary action', link: 'href route active',
    nav: 'active', tab: 'active', tabs: 'active', table: 'source', column: 'field',
    image: 'alt src asset', avatar: 'initials', progress: 'value max', chart: 'kind source',
    draglist: 'source', list: 'source', dialog: 'open', modal: 'open', alert: 'variant', toast: 'variant'
};
const booleanAttrs = new Set('disabled required checked selected primary active open'.split(' '));
const numberBounds = { width: [80, 1600], height: [0, 20000], gap: [0, 80], padding: [0, 80], columns: [1, 12], rows: [1, 40] };
const inputTypes = new Set('text email password number tel url search time datetime-local month week color'.split(' '));
const esc = value => String(value ?? '').replace(/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff\ufffe\uffff]/gu, '\ufffd').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shown = node => node.label || node.attrs.label || '';
const numeric = (node, key, fallback) => node.attrs[key] === undefined ? fallback : Number(node.attrs[key]);
const enabled = (node, key) => node.attrs[key] === 'true';
const localURL = value => /^(?:#[^\s\\]*|\/(?!\/)[^\s\\]*)$/.test(value) && !/[\u0000-\u001f\u007f]/.test(value);

function diagnostic(list, node, code, message, severity = 'error') {
    list.push({ severity, code, line: node?.line || 0, column: node?.column || 1,
        message, evidence: String(node?.raw || '').slice(0, 240) });
}

function tokenize(value) {
    const tokens = [];
    let token = '', quote = '', started = false, quotedLabel = false, closed = false;
    for (let i = 0; i < value.length; i++) {
        const c = value[i];
        if (quote) {
            if (c === '\\') {
                if (++i === value.length) throw new Error('Trailing escape in quoted value.');
                token += value[i];
            } else if (c === quote) { quote = ''; closed = true; }
            else token += c;
        } else if (/\s/.test(c)) {
            if (started) tokens.push({ value: token, quotedLabel });
            token = ''; started = false; quotedLabel = false; closed = false;
        } else if (closed) throw new Error('Expected whitespace after a quoted value.');
        else if (c === '"' || c === "'") {
            if (started && !/^[A-Za-z][\w-]*=$/.test(token)) throw new Error('Quotes must enclose an entire label or attribute value.');
            quotedLabel = !started; quote = c; started = true;
        } else { token += c; started = true; }
    }
    if (quote) throw new Error('Unterminated quoted value.');
    if (started) tokens.push({ value: token, quotedLabel });
    return tokens;
}

function validate(node, diagnostics) {
    const allowed = new Set([...common, ...(attributes[node.type] || '').split(' ')]);
    for (const [key, value] of Object.entries(node.attrs)) {
        if (!allowed.has(key)) diagnostic(diagnostics, node, 'UNKNOWN_ATTRIBUTE', `Unsupported attribute "${key}" on ${node.type}.`);
        if (booleanAttrs.has(key) && !['true', 'false'].includes(value)) diagnostic(diagnostics, node, 'INVALID_BOOLEAN', `${key} must be true or false.`);
        if (Object.hasOwn(numberBounds, key)) {
            const [min, max] = numberBounds[key];
            if (!/^\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value)) || Number(value) < min || Number(value) > max || (['columns', 'rows'].includes(key) && !Number.isInteger(Number(value)))) {
                diagnostic(diagnostics, node, 'INVALID_BOUND', `${key} must be finite and between ${min} and ${max}${['columns', 'rows'].includes(key) ? ' (integer)' : ''}.`);
            }
        }
    }
    if (node.attrs.id !== undefined && !/^[A-Za-z][\w-]{0,63}$/.test(node.attrs.id)) diagnostic(diagnostics, node, 'INVALID_ID', 'id must start with a letter and contain at most 64 letters, digits, underscores or hyphens.');
    if (node.attrs.type && !inputTypes.has(node.attrs.type)) diagnostic(diagnostics, node, 'INPUT_TYPE', `Unsupported input type "${node.attrs.type}"; use dedicated checkbox, radio or date components.`);
    if (node.attrs.device && !['desktop', 'tablet', 'mobile'].includes(node.attrs.device)) diagnostic(diagnostics, node, 'DEVICE', 'device must be desktop, tablet or mobile.');
    for (const key of ['href', 'route']) if (node.attrs[key] !== undefined && !localURL(node.attrs[key])) diagnostic(diagnostics, node, 'UNSAFE_URL', `${key} must be a local anchor or absolute local route.`);
    if (node.attrs.src !== undefined) diagnostic(diagnostics, node, 'UNSUPPORTED_SOURCE', 'Images are offline placeholders; src is not fetched.', 'warning');
    if (node.attrs.asset !== undefined && !/^[1-9]\d{0,15}$/.test(node.attrs.asset)) diagnostic(diagnostics, node, 'INVALID_ASSET', 'asset must be an uploaded project image ID.');
    if (node.attrs.source !== undefined) diagnostic(diagnostics, node, 'STATIC_SOURCE', 'source is metadata only; no data is fetched or generated.', 'warning');
    if (['calendar', 'tabs', 'tab', 'dialog', 'modal'].includes(node.type)) diagnostic(diagnostics, node, 'STATIC_INTERACTION', `${node.type} is shown inline; ${node.type === 'calendar' ? 'date selection uses the date control' : 'switching, opening and closing are not implemented'}.`, 'warning');
    if (node.attrs.variant !== undefined && node.type !== 'button') diagnostic(diagnostics, node, 'STATIC_STATE', 'variant is metadata only on this component.', 'warning');
    if (node.type === 'button' && node.attrs.variant && !['primary', 'secondary', 'default'].includes(node.attrs.variant)) diagnostic(diagnostics, node, 'UNSUPPORTED_VARIANT', 'Button variant supports primary, secondary or default.');
    if (node.attrs.disabled !== undefined && !['input', 'field', 'textarea', 'select', 'dropdown', 'checkbox', 'radio', 'toggle', 'slider', 'date', 'button', 'calendar', 'form'].includes(node.type)) diagnostic(diagnostics, node, 'UNSUPPORTED_DISABLED', 'disabled is supported only on form controls, forms and calendars.');
    if (['input', 'field'].includes(node.type) && node.attrs.type !== 'number' && ['min', 'max', 'step'].some(key => node.attrs[key] !== undefined)) diagnostic(diagnostics, node, 'UNSUPPORTED_BOUND', 'min/max/step on input/field require type=number; use date for ISO dates.');
    if (node.attrs.action !== undefined) diagnostic(diagnostics, node, 'STATIC_ACTION', 'action is metadata only; preview buttons do not execute actions or submit forms.', 'warning');
    if (node.type === 'draglist') diagnostic(diagnostics, node, 'STATIC_DRAGLIST', 'Drag handles are visual only; reordering is not implemented.', 'warning');
    if (node.type === 'chart') diagnostic(diagnostics, node, 'STATIC_CHART', 'Chart is an empty plotting frame; source and kind do not render data.', 'warning');
    if (['input', 'field', 'slider', 'progress'].includes(node.type) && (node.type === 'slider' || node.type === 'progress' || node.attrs.type === 'number')) {
        for (const key of ['min', 'max', 'step', 'value']) if (node.attrs[key] !== undefined && (!/^-?\d+(?:\.\d+)?$/.test(node.attrs[key]) || !Number.isFinite(Number(node.attrs[key])) || Math.abs(Number(node.attrs[key])) > 1e9)) diagnostic(diagnostics, node, 'INVALID_NUMBER', `${key} must be a finite number within +/-1e9.`);
        const min = numeric(node, 'min', 0), max = numeric(node, 'max', 100);
        if ((node.attrs.min !== undefined || node.type === 'slider' || node.type === 'progress') && (node.attrs.max !== undefined || node.type === 'slider' || node.type === 'progress') && min >= max) diagnostic(diagnostics, node, 'INVALID_RANGE', 'min must be less than max.');
        if (node.attrs.step !== undefined && Number(node.attrs.step) <= 0) diagnostic(diagnostics, node, 'INVALID_RANGE', 'step must be positive.');
        if (['slider', 'progress'].includes(node.type) && (numeric(node, 'value', min) < min || numeric(node, 'value', min) > max)) diagnostic(diagnostics, node, 'INVALID_RANGE', 'value must lie within the control range.');
    }
    for (const key of ['value', 'min', 'max']) if (['calendar', 'date'].includes(node.type) && node.attrs[key] !== undefined) {
        const value = node.attrs[key];
        const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00Z`) : null;
        if (!date || !Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) diagnostic(diagnostics, node, 'INVALID_DATE', `${key} must be a real ISO date (YYYY-MM-DD).`);
    }
    if (node.type === 'date' && node.attrs.min && node.attrs.max && node.attrs.min > node.attrs.max) diagnostic(diagnostics, node, 'INVALID_RANGE', 'Date min must not follow max.');
}

export function parseWireframe(source) {
    const diagnostics = [], roots = [], path = [], ids = new Set();
    const empty = { type: 'screen', label: 'Wireframe', attrs: {}, children: [], line: 0, column: 1, id: 'wf-root' };
    const result = (screens = [], templates = []) => ({ ...(screens[0] || empty), screens, templates, diagnostics });
    if (typeof source !== 'string' || source.length > LIMIT.source) {
        diagnostic(diagnostics, null, 'SOURCE_LIMIT', `Source must be a string of at most ${LIMIT.source} characters.`);
        return result();
    }
    let count = 0;
    const lines = source.split(/\r?\n/);
    for (let index = 0; index < lines.length; index++) {
        const raw = lines[index];
        if (!raw.trim() || raw.trimStart().startsWith('#')) continue;
        const indent = raw.match(/^\s*/)[0];
        const depth = indent.length / 2;
        const node = { type: '', label: '', attrs: Object.create(null), children: [], line: index + 1, column: indent.length + 1, raw, id: `wf-l${index + 1}` };
        if (++count > LIMIT.nodes) { diagnostic(diagnostics, node, 'NODE_LIMIT', `Maximum ${LIMIT.nodes} source nodes.`); break; }
        if (!/^ *$/.test(indent) || !Number.isInteger(depth) || depth > path.length || depth > LIMIT.depth) {
            diagnostic(diagnostics, node, 'INDENTATION', `Use two spaces per level without skipped levels; maximum depth ${LIMIT.depth}.`);
            path.length = 0;
            continue;
        }
        path.length = depth;
        let tokens;
        try { tokens = tokenize(raw.trim()); }
        catch (error) { diagnostic(diagnostics, node, 'SYNTAX', error.message); continue; }
        const typeToken = tokens.shift();
        node.type = typeToken?.value;
        if (typeToken?.quotedLabel) diagnostic(diagnostics, node, 'SYNTAX', 'Component names cannot be quoted.');
        if (!types.has(node.type)) { diagnostic(diagnostics, node, 'UNKNOWN_COMPONENT', `Unknown component "${node.type}".`); continue; }
        let hasLabel = false;
        for (const { value: token, quotedLabel } of tokens) {
            const match = quotedLabel ? null : /^([A-Za-z][\w-]*)=(.*)$/.exec(token);
            if (match) {
                if (Object.hasOwn(node.attrs, match[1])) diagnostic(diagnostics, node, 'DUPLICATE_ATTRIBUTE', `Duplicate attribute "${match[1]}".`);
                node.attrs[match[1]] = match[2];
            } else if (!quotedLabel && booleanAttrs.has(token)) {
                if (Object.hasOwn(node.attrs, token)) diagnostic(diagnostics, node, 'DUPLICATE_ATTRIBUTE', `Duplicate attribute "${token}".`);
                node.attrs[token] = 'true';
            } else if (!hasLabel) { node.label = token; hasLabel = true; }
            else diagnostic(diagnostics, node, 'SYNTAX', `Unexpected token "${token}"; quote labels containing spaces.`);
        }
        if ([node.label, ...Object.values(node.attrs)].some(value => value.length > LIMIT.label)) diagnostic(diagnostics, node, 'TEXT_LIMIT', `Labels and attribute values are limited to ${LIMIT.label} characters.`);
        if (/[\u0000-\u0008\u000b-\u001f\u007f\ud800-\udfff\ufffe\uffff]/u.test(raw)) diagnostic(diagnostics, node, 'CONTROL_CHARACTER', 'Control characters and invalid Unicode are not permitted.');
        validate(node, diagnostics);
        if (node.attrs.id) {
            if (ids.has(node.attrs.id)) diagnostic(diagnostics, node, 'DUPLICATE_ID', `Duplicate id "${node.attrs.id}".`);
            ids.add(node.attrs.id); node.id = `wf-id-${node.attrs.id}`;
        }
        const parent = path[depth - 1];
        if (['screen', 'template'].includes(node.type) && parent) diagnostic(diagnostics, node, 'STRUCTURE', `${node.type} must be at the root.`);
        if (parent && !containers.has(parent.type) && !['select', 'dropdown'].includes(parent.type)) diagnostic(diagnostics, node, 'LEAF_CHILDREN', `${parent.type} cannot contain children.`);
        if (node.type === 'form' && path.some(ancestor => ancestor.type === 'form')) diagnostic(diagnostics, node, 'STRUCTURE', 'Forms cannot be nested.');
        if (['select', 'dropdown'].includes(parent?.type) && node.type !== 'option') diagnostic(diagnostics, node, 'STRUCTURE', 'Select/dropdown children must be options.');
        if (node.type === 'option' && !['select', 'dropdown'].includes(parent?.type)) diagnostic(diagnostics, node, 'STRUCTURE', 'option must belong to select/dropdown.');
        if (node.type === 'column' && parent?.type !== 'table') diagnostic(diagnostics, node, 'STRUCTURE', 'column must belong to table.');
        if (parent?.type === 'table' && node.type !== 'column') diagnostic(diagnostics, node, 'STRUCTURE', 'table supports column declarations only.');
        if (node.type === 'content' && (!['screen', 'template'].includes(parent?.type) || !parent.attrs.uses)) diagnostic(diagnostics, node, 'STRUCTURE', 'content must directly belong to a screen/template with uses=.');
        if (node.type === 'slot' && path[0]?.type !== 'template') diagnostic(diagnostics, node, 'STRUCTURE', 'slot must be declared within a template.');
        (parent ? parent.children : roots).push(node);
        path.push(node);
    }
    function checkOptions(node) {
        if (['select', 'dropdown'].includes(node.type)) {
            const options = selectedOptions(node);
            if (options.filter(option => option.selected).length > 1) diagnostic(diagnostics, node, 'OPTION_SELECTION', 'Only one option may be selected.');
            if (node.attrs.value !== undefined && !options.some(option => option.value === node.attrs.value)) diagnostic(diagnostics, node, 'OPTION_SELECTION', 'value must match a declared option.');
        }
        node.children.forEach(checkOptions);
    }
    roots.forEach(checkOptions);
    const templates = roots.filter(node => node.type === 'template');
    const byName = new Map();
    for (const template of templates) {
        if (!template.label || byName.has(template.label)) diagnostic(diagnostics, template, 'TEMPLATE_NAME', 'Templates need unique nonempty names.');
        byName.set(template.label, template);
    }
    // Validate the complete dependency graph, even templates unused by a screen.
    const checked = new Set();
    function check(template, trail = []) {
        if (trail.includes(template)) { diagnostic(diagnostics, template, 'TEMPLATE_CYCLE', `Template cycle: ${[...trail, template].map(n => n.label).join(' -> ')}.`); return; }
        if (checked.has(template)) return;
        if (trail.length >= LIMIT.depth) { diagnostic(diagnostics, template, 'TEMPLATE_DEPTH', 'Template inheritance is too deep.'); return; }
        if (template.attrs.uses) {
            const base = byName.get(template.attrs.uses);
            if (!base) diagnostic(diagnostics, template, 'UNKNOWN_TEMPLATE', `Unknown template "${template.attrs.uses}".`);
            else check(base, [...trail, template]);
        }
        checked.add(template);
    }
    templates.forEach(template => check(template));
    let screens = roots.filter(node => node.type === 'screen');
    const loose = roots.filter(node => !['screen', 'template'].includes(node.type));
    if (screens.length && loose.length) loose.forEach(node => diagnostic(diagnostics, node, 'STRUCTURE', 'Place root controls inside a screen when declaring screens.'));
    else if (!screens.length) screens = [{ ...empty, children: loose }];
    if (diagnostics.some(d => d.severity === 'error')) return result(screens, templates);
    let expanded = 0;
    const name = node => node.attrs.name || shown(node) || 'content';
    function copy(node, prefix, depth = 0) {
        if (++expanded > LIMIT.expanded || depth > LIMIT.depth) throw new Error('Expanded template size/depth exceeds the document limit.');
        return { ...node, attrs: { ...node.attrs }, id: `${prefix}-${node.id}`, children: node.children.map(child => copy(child, prefix, depth + 1)) };
    }
    function expand(node) {
        let children = [];
        if (node.attrs.uses) {
            const base = byName.get(node.attrs.uses);
            if (!base) { diagnostic(diagnostics, node, 'UNKNOWN_TEMPLATE', `Unknown template "${node.attrs.uses}".`); return node; }
            children = expand(base).children.map(child => copy(child, node.id));
        }
        const fills = new Map();
        for (const child of node.children.filter(child => child.type === 'content')) {
            if (fills.has(name(child))) diagnostic(diagnostics, child, 'DUPLICATE_CONTENT', `Duplicate content "${name(child)}".`);
            fills.set(name(child), child);
        }
        const slots = new Set();
        function fill(child) {
            if (child.type === 'slot') {
                const key = name(child);
                if (slots.has(key)) diagnostic(diagnostics, child, 'DUPLICATE_SLOT', `Duplicate slot "${key}".`);
                slots.add(key);
                if (fills.has(key)) child.children = fills.get(key).children.map(n => copy(n, node.id));
            }
            child.children.forEach(fill);
        }
        children.push(...node.children.filter(child => child.type !== 'content').map(child => copy(child, node.id)));
        children.forEach(fill);
        for (const [key, child] of fills) if (!slots.has(key)) diagnostic(diagnostics, child, 'UNKNOWN_SLOT', `Unknown slot "${key}".`);
        return { ...node, children };
    }
    try {
        // Also validate unused slot declarations and inheritance overrides.
        templates.forEach(template => expand(template));
        screens = screens.map(screen => expand(screen));
        function depthCheck(node, depth = 0) {
            if (depth > LIMIT.depth) throw new Error('Expanded template depth exceeds the document limit.');
            node.children.forEach(child => depthCheck(child, depth + 1));
        }
        screens.forEach(screen => depthCheck(screen));
    } catch (error) { diagnostic(diagnostics, null, 'EXPANSION_LIMIT', error.message); }
    return result(screens, templates);
}

// Conservative glyph metrics, including wide Unicode glyphs. Hard-wrap long words
// too; SVG and HTML consume these same lines instead of relying on browser metrics.
function wrap(value, width, size = 13) {
    const lines = []; let line = '', used = 0;
    for (const c of String(value)) {
        const advance = size * (/[^\u0020-\u024f]/u.test(c) ? 1.1 : /[MW@#%]/.test(c) ? .95 : .64);
        if (line && used + advance > Math.max(1, width)) { lines.push(line); line = ''; used = 0; }
        line += c; used += advance;
    }
    if (line) lines.push(line);
    return lines;
}
const transparent = new Set(['screen', 'stack', 'row', 'grid', 'main', 'slot', 'content', 'section', 'header', 'nav', 'list', 'item', 'menu']);
const fields = new Set(['input', 'field', 'textarea', 'select', 'dropdown', 'date']);

function measure(node, available, diagnostics) {
    const compact = ['button', 'badge', 'avatar'].includes(node.type);
    const defaultWidth = compact ? Math.max(96, shown(node).length * 9 + 32) : node.type === 'form' ? Math.min(640, available) : available;
    const width = Math.min(available, numeric(node, 'width', defaultWidth));
    const defaultPadding = ['card', 'dialog', 'modal', 'sidebar', 'alert', 'toast', 'form'].includes(node.type) ? 24 : compact || ['column', 'tab'].includes(node.type) ? 12 : 0;
    const padding = Math.min(numeric(node, 'padding', defaultPadding), Math.max(0, (width - 80) / 2));
    const inner = Math.max(1, width - padding * 2), gap = numeric(node, 'gap', 20);
    if (node.type === 'calendar' && inner < 224) throw new Error(`Calendar needs at least 224px of inner width at source line ${node.line}.`);
    const lines = wrap(['screen', 'slot', 'content', 'divider', 'spacer'].includes(node.type) ? '' : shown(node), inner - (['checkbox', 'radio', 'toggle'].includes(node.type) ? 36 : 0), node.type === 'heading' ? 28 : 14);
    const titleHeight = lines.length * (node.type === 'heading' ? 36 : 20);
    const box = { node, x: 0, y: 0, width, height: 0, padding, lines, titleHeight, children: [] };
    let height = padding * 2 + titleHeight;
    if (containers.has(node.type) || node.type === 'table') {
        const start = padding + titleHeight + (titleHeight && node.children.length ? gap : 0);
        const horizontal = ['row', 'grid', 'table', 'tabs'].includes(node.type);
        const desired = node.type === 'grid' ? numeric(node, 'columns', 2) : Math.max(1, node.children.length);
        const columns = horizontal ? Math.max(1, Math.min(desired, Math.floor((inner + gap) / (100 + gap)))) : 1;
        const cellWidth = (inner - gap * (columns - 1)) / columns;
        let y = start, rowHeight = 0;
        node.children.forEach((child, index) => {
            if (index && index % columns === 0) { y += rowHeight + gap; rowHeight = 0; }
            const childBox = measure(child, cellWidth, diagnostics);
            childBox.x = padding + (index % columns) * (cellWidth + gap); childBox.y = y;
            rowHeight = Math.max(rowHeight, childBox.height);
            box.children.push(childBox);
        });
        height = y + rowHeight + padding;
        if (!node.children.length) height = Math.max(height, ['slot', 'content', 'screen', 'stack'].includes(node.type) ? 0 : 48);
    } else if (fields.has(node.type)) {
        const options = ['select', 'dropdown'].includes(node.type) ? selectedOptions(node) : [];
        const selected = options.find(option => option.selected) || options[0];
        box.valueLines = wrap((options.length ? selected.label : node.attrs.value) || node.attrs.placeholder || (['select', 'dropdown'].includes(node.type) ? 'Select...' : node.type === 'date' ? 'yyyy-mm-dd' : ''), inner - 32);
        if (node.attrs.type === 'password' && node.attrs.value) box.valueLines = wrap('•'.repeat(Math.min(node.attrs.value.length, 24)), inner - 32);
        height += (titleHeight ? 6 : 0) + Math.max(node.type === 'textarea' ? numeric(node, 'rows', 3) * 20 + 20 : 42, box.valueLines.length * 20 + 20);
    } else if (node.type === 'image' && node.attrs.asset) height = padding * 2 + Math.max(24, numeric(node, 'height', 120));
    else if (node.type === 'calendar') height += 196;
    else if (['chart', 'image'].includes(node.type)) height += 132;
    else if (['slider', 'progress'].includes(node.type)) height += 28;
    else if (node.type === 'avatar') height += 32;
    else if (node.type === 'divider') height = 1;
    else if (node.type === 'spacer') height = 24;
    else height = Math.max(height, ['checkbox', 'radio', 'toggle', 'avatar'].includes(node.type) ? 48 : node.type === 'button' ? 42 : 32);
    if (node.attrs.height !== undefined && Number(node.attrs.height) < height) diagnostic(diagnostics, node, 'HEIGHT_EXPANDED', `height=${node.attrs.height} expanded to ${Math.ceil(height)} to fit content.`, 'warning');
    box.height = Math.ceil(Math.max(height, numeric(node, 'height', 0)));
    if (box.height > LIMIT.height) throw new Error(`Layout exceeds ${LIMIT.height}px height at source line ${node.line}.`);
    return box;
}

function selectedOptions(node) {
    const options = node.children.map(child => ({ label: shown(child), value: child.attrs.value ?? shown(child), selected: enabled(child, 'selected') }));
    if (node.attrs.options) options.push(...node.attrs.options.split('|').map(label => ({ label, value: label })));
    if (node.attrs.value !== undefined) options.forEach(option => { option.selected = option.value === node.attrs.value; });
    return options;
}
const palette = { ink: '#182230', muted: '#596579', panel: '#f5f7fa', line: '#cbd2dc', background: '#ffffff', accent: '#253d63' };
const rect = (x, y, width, height, extra = '') => `<rect x="${x}" y="${y}" width="${Math.max(0, width)}" height="${Math.max(0, height)}" rx="6" ${extra}/>`;
const svgLines = (lines, x, y, size = 14, extra = '') => lines.map((line, i) => `<text x="${x}" y="${y + i * (size === 28 ? 36 : 20)}" font-size="${size}" ${extra}>${esc(line)}</text>`).join('');

function renderSVG(box, parentX = 0, parentY = 0, assetURLs = {}) {
    const { node, width: w, height: h, padding: p } = box;
    const x = parentX + box.x, y = parentY + box.y, t = node.type;
    const labelX = x + p + (['checkbox', 'radio', 'toggle'].includes(t) ? 36 : 0);
    const primary = t === 'button' && (enabled(node, 'primary') || node.attrs.variant === 'primary');
    let out = `<g id="${esc(node.id)}" data-type="${t}" data-line="${node.line}" data-x="${x}" data-y="${y}" data-width="${w}" data-height="${h}">`;
    if (containers.has(t) && !transparent.has(t)) out += rect(x, y, w, h, `fill="${palette.panel}" stroke="${palette.line}"`);
    if (['button', 'badge', 'tab', 'column'].includes(t)) out += rect(x, y, w, h, `fill="${primary ? palette.accent : palette.panel}" stroke="${primary ? palette.accent : palette.line}"`);
    if (t !== 'spacer' && !(t === 'image' && assetURLs[node.attrs.asset])) out += svgLines(box.lines, labelX, y + p + (t === 'heading' ? 28 : 15), t === 'heading' ? 28 : 14, `fill="${primary ? '#ffffff' : t === 'text' ? palette.muted : palette.ink}" font-weight="${t === 'heading' ? 650 : fields.has(t) || containers.has(t) || t === 'button' ? 500 : 400}"`);
    const cy = y + p + box.titleHeight + (box.titleHeight ? 6 : 0), cw = w - p * 2;
    if (fields.has(t)) {
        out += rect(x + p, cy, cw, h - (cy - y) - p, `fill="${palette.background}" stroke="${palette.line}"`);
        out += svgLines(box.valueLines, x + p + 12, cy + 26, 14, `fill="${node.attrs.value ? palette.ink : palette.muted}"`);
        if (['select', 'dropdown'].includes(t)) out += `<path d="M${x + w - p - 20} ${cy + 14} l5 5 l5 -5" fill="none" stroke="${palette.ink}"/>`;
        if (t === 'date') out += rect(x + w - p - 24, cy + 10, 16, 16, `fill="none" stroke="${palette.ink}"`);
    } else if (t === 'checkbox') {
        out += rect(x + p, y + p, 18, 18, `fill="none" stroke="${palette.ink}"`);
        if (enabled(node, 'checked')) out += `<path d="M${x + p + 3} ${y + p + 9} l5 5 l8 -10" fill="none" stroke="${palette.ink}"/>`;
    } else if (t === 'radio') {
        out += `<circle cx="${x + p + 9}" cy="${y + p + 9}" r="9" fill="none" stroke="${palette.ink}"/>`;
        if (enabled(node, 'checked')) out += `<circle cx="${x + p + 9}" cy="${y + p + 9}" r="4"/>`;
    } else if (t === 'toggle') out += rect(x + p, y + p, 34, 18, `fill="${enabled(node, 'checked') ? '#71717a' : '#27272a'}" stroke="${palette.line}"`) + `<circle cx="${x + p + (enabled(node, 'checked') ? 25 : 9)}" cy="${y + p + 9}" r="6"/>`;
    else if (['slider', 'progress'].includes(t)) {
        const min = numeric(node, 'min', 0), max = numeric(node, 'max', 100), fraction = (numeric(node, 'value', min) - min) / (max - min);
        out += rect(x + p, cy + 8, cw, 6, `fill="${palette.line}"`) + rect(x + p, cy + 8, cw * fraction, 6, `fill="${palette.ink}"`);
        if (t === 'slider') out += `<circle cx="${x + p + 6 + Math.max(0, cw - 12) * fraction}" cy="${cy + 11}" r="6"/>`;
    } else if (t === 'divider') out += `<path d="M${x} ${y} h${w}" stroke="${palette.line}"/>`;
    else if (t === 'calendar') {
        const calendar = calendarCells(node);
        out += svgLines([calendar.month], x + p, cy + 15);
        calendar.cells.forEach((value, index) => { out += svgLines([value], x + p + (index % 7) * cw / 7, cy + 40 + Math.floor(index / 7) * 22); });
    } else if (t === 'image' && assetURLs[node.attrs.asset]) {
        out += `<image href="${esc(assetURLs[node.attrs.asset])}" x="${x + p}" y="${y + p}" width="${cw}" height="${h - p * 2}" preserveAspectRatio="xMinYMid meet"><title>${esc(node.attrs.alt || shown(node))}</title></image>`;
    } else if (['image', 'chart'].includes(t)) {
        out += rect(x + p, cy, cw, 120, `fill="none" stroke="${palette.line}"`);
        out += `<path d="M${x + p} ${cy + (t === 'image' ? 0 : 120)} L${x + w - p} ${cy + 120}${t === 'image' ? ` M${x + p} ${cy + 120} L${x + w - p} ${cy}` : ''}" stroke="${palette.line}"/>`;
    } else if (t === 'avatar') out += `<circle cx="${x + w - 16}" cy="${y + h - 16}" r="12" fill="none" stroke="${palette.line}"/>`;
    else if (t === 'link') out += `<path d="M${x + p} ${y + p + box.titleHeight} h${Math.min(cw, shown(node).length * 8)}" stroke="${palette.muted}"/>`;
    if (t === 'draglist') out += `<text x="${x + w - 18}" y="${y + 17}">⠿</text>`;
    box.children.forEach(child => { out += renderSVG(child, x, y, assetURLs); });
    return out + '</g>';
}

function calendarCells(node) {
    // Fixed fallback makes preview output independent of the clock and timezone.
    const iso = node.attrs.value || '2024-01-01';
    const year = Number(iso.slice(0, 4)), month = Number(iso.slice(5, 7));
    const first = new Date(`${iso.slice(0, 7)}-01T00:00:00Z`).getUTCDay();
    const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
    return { month: iso.slice(0, 7), title: `${['January','February','March','April','May','June','July','August','September','October','November','December'][month - 1]} ${year}`, selected: Number(iso.slice(8,10)), cells: ['S', 'M', 'T', 'W', 'T', 'F', 'S', ...Array.from({ length: Math.ceil((first + days) / 7) * 7 }, (_, i) => i >= first && i < first + days ? String(i - first + 1) : '')] };
}

function renderHTML(box, assetURLs = {}) {
    const { node, padding: p } = box, t = node.type, a = node.attrs;
    // SVG needs conservative line estimates. HTML must let the browser shape
    // and wrap actual words, particularly when the inspector is resized.
    const label = ['screen', 'slot', 'content', 'divider', 'spacer'].includes(t) ? '' : esc(shown(node));
    const controlId = `${node.id}-control`;
    const attrs = keys => keys.filter(key => a[key] !== undefined).map(key => ` ${key}="${esc(a[key])}"`).join('');
    const bools = ['disabled', 'required'].filter(key => enabled(node, key)).map(key => ` ${key}`).join('');
    const accessible = ` id="${esc(controlId)}" aria-label="${esc(shown(node) || t)}"${bools}`;
    const metadata = Object.entries(a).map(([key, value]) => ` data-wf-${key}="${esc(value)}"`).join('');
    const style = `left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px;padding:${p}px;--gap:${numeric(node, 'gap', 20)}px;--columns:${numeric(node, 'columns', 2)};--min-height:${numeric(node, 'height', 0)}px;--max-width:${box.width}px;`;
    const children = box.children.map(child => renderHTML(child, assetURLs)).join('');
    let body = label ? `<div class="label${t === 'heading' ? ' heading' : ''}">${label}</div>` : '';
    const controlStyle = ` style="height:${Math.max(36, box.height - p * 2 - box.titleHeight - (box.titleHeight ? 6 : 0))}px"`;
    if (fields.has(t)) {
        body = `<label for="${esc(controlId)}" class="label">${label}</label>`;
        if (['select', 'dropdown'].includes(t)) body += `<select${accessible}${attrs(['name'])}${controlStyle}>${selectedOptions(node).map(option => `<option value="${esc(option.value)}"${option.selected ? ' selected' : ''}>${esc(option.label)}</option>`).join('')}</select>`;
        else if (t === 'textarea') body += `<textarea${accessible}${attrs(['name', 'placeholder', 'rows'])}${controlStyle}>${esc(a.value || '')}</textarea>`;
        else body += `<input${accessible} type="${t === 'date' ? 'date' : esc(a.type || 'text')}"${attrs(['name', 'value', 'placeholder', 'min', 'max', 'step'])}${controlStyle}>`;
    } else if (['checkbox', 'radio', 'toggle'].includes(t)) body = `<label class="choice"><input${accessible} type="${t === 'radio' ? 'radio' : 'checkbox'}"${t === 'toggle' ? ' role="switch"' : ''}${attrs(['name', 'value'])}${enabled(node, 'checked') ? ' checked' : ''}><span>${label}</span></label>`;
    else if (t === 'slider') body += `<input${accessible} type="range"${attrs(['name', 'min', 'max', 'step', 'value'])}>`;
    else if (t === 'progress') body += `<progress${accessible} max="${esc(a.max || '100')}" value="${esc(a.value || '0')}"></progress>`;
    else if (t === 'button') body = `<button${accessible} type="button">${label || 'Button'}</button>`;
    else if (t === 'link') {
        const url = a.href || a.route || '#';
        body = `<a href="${esc(url.startsWith('/') ? `#${url}` : url)}"${enabled(node, 'active') ? ' aria-current="page"' : ''}>${label || 'Link'}</a>`;
    } else if (t === 'calendar') {
        const calendar = calendarCells(node);
        body += `<div class="calendar-month">${calendar.title}</div><div class="calendar-days" role="group" aria-label="Calendar">${calendar.cells.map((value, i) => i < 7 || !value ? `<span>${value}</span>` : `<button type="button" aria-label="${calendar.month}-${value.padStart(2, '0')}"${Number(value) === calendar.selected ? ' aria-pressed="true"' : ''}${bools}>${value}</button>`).join('')}</div>`;
    } else if (t === 'divider') body = '<hr>';
    else if (t === 'spacer') body = '';
    else if (t === 'image') body = assetURLs[a.asset]
        ? `<img src="${esc(assetURLs[a.asset])}" alt="${esc(a.alt || shown(node) || 'Project image')}" style="display:block;width:100%;height:${box.height - p * 2}px;object-fit:contain;object-position:left center" referrerpolicy="no-referrer">`
        : body + `<div class="placeholder" role="img" aria-label="${esc(a.alt || shown(node) || 'Image placeholder')}">${a.asset ? 'Project image unavailable' : 'Image'}</div>`;
    else if (t === 'chart') body += '<div class="placeholder" role="img" aria-label="Empty chart">No data connected</div>';
    else if (t === 'avatar') body += `<span class="avatar">${esc((a.initials || shown(node) || '?').slice(0, 2))}</span>`;
    if (t === 'draglist') body += '<span class="handle" aria-label="Drag handle (static)">⠿</span>';
    const tag = t === 'form' ? 'fieldset' : t === 'nav' ? 'nav' : t === 'main' ? 'main' : t === 'sidebar' ? 'aside' : t === 'header' ? 'header' : 'div';
    const role = { dialog: 'dialog', modal: 'dialog', table: 'table', column: 'columnheader', alert: 'alert', toast: 'status', list: 'list', draglist: 'list', item: 'listitem', menu: 'navigation' }[t];
    const plain = transparent.has(t) || (!containers.has(t) && !['badge', 'tab', 'column'].includes(t));
    return `<${tag} id="${esc(node.id)}" class="node ${plain ? 'plain' : ''} ${t}" data-type="${t}" data-line="${node.line}"${metadata} style="${style}"${t === 'form' && enabled(node, 'disabled') ? ' disabled' : ''}${role ? ` role="${role}" aria-label="${esc(shown(node) || t)}"` : ''}>${body}${children}</${tag}>`;
}

const css = `:root{color-scheme:light}
*{box-sizing:border-box;scrollbar-color:#a3adbc #f5f7fa;scrollbar-width:thin}
body{margin:0;background:${palette.background};color:${palette.ink};font:14px/20px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
::selection{background:#dbe5f4;color:#182230}
.canvas{position:relative;min-height:100%;background:#fff;border:1px solid #e1e6ed;border-radius:14px;box-shadow:0 12px 30px rgba(24,34,48,.08);overflow:hidden}.node{position:absolute;min-width:0;border-radius:6px}
.node:not(.plain){background:${palette.panel};outline:1px solid ${palette.line}}
.card,.dialog,.modal{border-radius:12px}.label{display:block;overflow-wrap:anywhere}
.field>.label,.input>.label,.select>.label,.dropdown>.label,.textarea>.label,.date>.label{font-weight:500}
.form>.label,.card>.label,.section>.label,.dialog>.label,.modal>.label{font-size:18px;line-height:26px;font-weight:600;letter-spacing:-.015em}
.heading{font-size:28px;line-height:36px;font-weight:650;letter-spacing:-.025em}.text{color:${palette.muted}}
input,textarea,select,button,progress{font:inherit;color:inherit;background:white;border:1px solid ${palette.line};border-radius:6px;max-width:100%;accent-color:${palette.accent};caret-color:${palette.accent}}
input::placeholder,textarea::placeholder{color:${palette.muted};opacity:1}
input:not([type=checkbox]):not([type=radio]),textarea,select,progress{width:100%}
.label+input,.label+textarea,.label+select{margin-top:6px}input,textarea,select{padding:10px 12px}textarea{resize:none}
button{cursor:pointer;padding:10px 16px;font-weight:500;white-space:normal;overflow-wrap:anywhere}
.button{padding:0!important}.button button{width:100%;height:100%;min-height:42px}
button:hover{background:#eef2f7}button:disabled,input:disabled,select:disabled,textarea:disabled{opacity:.5;cursor:not-allowed}
.button[data-wf-variant=primary] button,.button[data-wf-primary=true] button{background:${palette.accent};border-color:${palette.accent};color:white}
.button[data-wf-variant=primary] button:hover,.button[data-wf-primary=true] button:hover{background:#182c4a}
.choice{display:flex;align-items:flex-start;gap:18px}.choice input{flex:none;margin:1px 0;width:18px;height:18px;padding:0}
input[role=switch]{appearance:none;width:34px;height:18px;border-radius:12px;background:#cbd2dc}
input[role=switch]:checked{background:${palette.accent}}input[role=switch]:after{content:'';display:block;width:12px;height:12px;border-radius:50%;background:white;transform:translate(1px,1px)}input[role=switch]:checked:after{transform:translate(17px,1px)}
a{color:${palette.accent};text-underline-offset:3px}.calendar-days{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));grid-auto-rows:22px;text-align:center;font-variant-numeric:tabular-nums}.calendar-days button{padding:0;font-size:12px;border-color:transparent}
.placeholder{height:120px;border:1px dashed ${palette.line};color:${palette.muted};background:${palette.panel};display:grid;place-items:center}.avatar{display:inline-grid;place-items:center;background:#e5ebf3;border-radius:50%;width:32px;height:32px;font-weight:600}.handle{position:absolute;right:4px;top:0}hr{margin:0;border:0;border-top:1px solid ${palette.line}}:focus-visible{outline:2px solid ${palette.accent};outline-offset:3px}
fieldset.node{margin:0;border:0;min-inline-size:0}`;

// HTML is a responsive document, not an absolutely positioned copy of SVG.
// Keep the source's explicit sizes and spacing, but use intrinsic text height.
const flowCSS = `
.sheet{width:100%!important;max-width:var(--page-width);min-height:100%;margin-inline:auto;padding:32px!important;background:#f3f5f8}
.canvas{width:100%!important;height:auto!important}
.node{position:relative;left:auto!important;top:auto!important;width:100%!important;height:auto!important;min-height:var(--min-height)}
.node[data-wf-width],.form{max-width:var(--max-width)}
.form{background:#fff;border:1px solid #e1e6ed;border-radius:12px;box-shadow:0 4px 12px rgba(24,34,48,.05)}
.node>.node{margin-top:var(--gap)}.node>.node:first-child{margin-top:0}.node>.label+.node{margin-top:var(--gap)}
.heading{margin:0}.heading+.form,.heading+.section{margin-top:28px}
.button,.badge,.avatar{width:fit-content!important;max-width:100%}.button button{min-width:96px}
.spacer{min-height:max(24px,var(--min-height))}.image,.chart{min-height:max(140px,var(--min-height))}.divider{min-height:1px}
.image[data-wf-asset]{min-height:var(--min-height)}
.row,.grid{display:grid;gap:var(--gap)}.row{grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}.grid{grid-template-columns:repeat(var(--columns),minmax(0,1fr))}
.row>.node,.grid>.node{margin:0}.row>.label,.grid>.label{grid-column:1/-1}
.row:has(>.button):not(:has(>.node:not(.button):not(.link))){display:flex;flex-wrap:wrap;gap:12px}
.nav,.menu{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.nav>.node,.menu>.node{width:auto!important;margin:0}.nav>.label,.menu>.label{font-weight:600;margin-right:auto}
.nav a,.menu a{display:block;padding:8px 12px;border-radius:6px;text-decoration:none;color:${palette.muted};font-weight:500}
.nav a:hover,.menu a:hover{background:#e9eef5;color:${palette.ink}}
.nav a[aria-current],.menu a[aria-current]{background:#e5ebf3;color:${palette.accent}}
.sidebar .nav,.sidebar .menu{align-items:stretch;flex-direction:column}.sidebar .nav>.node,.sidebar .menu>.node{width:100%!important}
.tabs{display:flex;flex-wrap:wrap;gap:4px;background:transparent!important;outline:0!important;border-radius:0;border-bottom:1px solid ${palette.line}}
.tabs>.tab{width:auto!important;margin:0;background:transparent;outline:0;border-radius:0;color:${palette.muted};padding:10px 14px!important}
.tabs>.tab[data-wf-active=true]{color:${palette.accent};box-shadow:inset 0 -2px ${palette.accent};font-weight:600}
.card,.dialog,.modal{background:white!important}.badge{font-size:12px;line-height:18px;padding:3px 8px!important;border-radius:4px}
.calendar{max-width:320px}.calendar-days{grid-auto-rows:36px;align-items:center;margin-top:12px}.calendar-days button{height:32px;min-width:32px}
.calendar-month{font-weight:600;margin-top:8px}.calendar-days button[aria-pressed=true]{background:${palette.accent};color:white}
@media(max-width:520px){.grid{grid-template-columns:1fr}.sheet{padding:16px!important}.canvas{border-radius:10px}input,textarea,select{font-size:16px}}
`;

export function renderWireframe(source, title = 'Wireframe', pageId, assets = []) {
    // Only the host supplies this manifest. DSL source can name an ID, never a URL.
    // Restrict even host-provided URLs to the exact project attachment endpoint.
    const assetURLs = Object.create(null);
    for (const asset of assets) {
        if (!/^image\/(?:png|jpeg|webp|svg\+xml)$/.test(asset.mime_type || '')) continue;
        try {
            const url = new URL(asset.url);
            if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) continue;
            if (!new RegExp(`^/api/workflows/studio/plan-workspaces/[1-9]\\d*/attachments/${Number(asset.id)}$`).test(url.pathname)) continue;
            assetURLs[String(asset.id)] = url.href;
        } catch { /* A missing or relative manifest URL remains a placeholder. */ }
    }
    const ast = parseWireframe(source), diagnostics = [...ast.diagnostics];
    const page = pageId === undefined ? ast.screens[0] : ast.screens.find(screen => screen.attrs.id === pageId || screen.id === pageId || screen.label === pageId);
    if (!page && !diagnostics.some(d => d.severity === 'error')) diagnostic(diagnostics, null, 'UNKNOWN_PAGE', `Unknown page "${pageId}".`);
    let box;
    if (!diagnostics.some(d => d.severity === 'error')) {
        try { box = measure(page, numeric(page, 'width', { mobile: 390, tablet: 768, desktop: 920 }[page.attrs.device] || 920), diagnostics); }
        catch (error) { diagnostic(diagnostics, page, 'LAYOUT_LIMIT', error.message); }
    }
    const safeTitle = String(page?.label || title || 'Wireframe').slice(0, LIMIT.label);
    const width = (box?.width || 600) + 48;
    const offset = 24;
    const height = offset + (box?.height || 64) + 24;
    const content = box ? renderSVG(box, 24, offset, assetURLs) : svgLines(['Invalid wireframe. See diagnostics.'], 24, offset + 20);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${esc(safeTitle)}"><title>${esc(safeTitle)}</title><rect width="100%" height="100%" fill="${palette.background}"/><g fill="${palette.ink}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">${content}</g></svg>`;
    // Defense in depth, including if opened outside the caller's sandboxed iframe.
    // No scripts, external resources, form submission, raw HTML or executable URLs.
    // srcdoc otherwise inherits its embedder's URL: fragment links could reload
    // that network URL inside the frame. Pin resolution to an offline about URL.
    // At narrow preview widths use native document flow instead of clipping the
    // desktop coordinate canvas. The AST, labels and control bindings stay intact.
    const imagePolicy = Object.values(assetURLs).map(esc).join(' ') || "'none'";
    const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src ${imagePolicy}; connect-src 'none'; form-action 'none'; base-uri about:"><base href="about:srcdoc"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(safeTitle)}</title><style>${css}${flowCSS}</style></head><body><div class="sheet" style="padding:24px;--page-width:${width}px"><div class="canvas" style="width:${box?.width || 552}px;height:${box?.height || 64}px">${box ? renderHTML(box, assetURLs) : '<p role="alert">Invalid wireframe. See diagnostics.</p>'}</div></div></body></html>`;
    return { svg, html, diagnostics };
}
