import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Import as ESM without imposing package.json changes on the browser asset tree.
const source = await readFile(new URL('../../distr/gui/web/static/development/planning/wireframe.js', import.meta.url), 'utf8');
const { parseWireframe, renderWireframe, explainWireframeDiagnostics } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const errors = result => result.diagnostics.filter(d => d.severity === 'error');
const boxes = svg => [...svg.matchAll(/<g id="([^"]+)" data-type="([^"]+)" data-line="(\d+)" data-x="([\d.]+)" data-y="([\d.]+)" data-width="([\d.]+)" data-height="([\d.]+)"/g)].map(m => ({ id: m[1], type: m[2], line: +m[3], x: +m[4], y: +m[5], w: +m[6], h: +m[7] }));
const separated = (a, b) => a.x + a.w <= b.x + .001 || b.x + b.w <= a.x + .001 || a.y + a.h <= b.y + .001 || b.y + b.h <= a.y + .001;

test('project images render from host manifests, never URLs in DSL source', () => {
    const dsl = 'screen Brand\n  image "Logo" asset=7 width=160 height=64';
    const url = 'http://127.0.0.1:8778/api/workflows/studio/plan-workspaces/1/attachments/7';
    const result = renderWireframe(dsl, '', undefined, [{id:7, mime_type:'image/svg+xml', url}]);
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /<img src="http:\/\/127.0.0.1:8778/);
    assert.match(result.svg, /<image href="http:\/\/127.0.0.1:8778/);
    assert.equal(boxes(result.svg).find(box => box.type === 'image').h, 64);
    assert.match(result.html, /script-src 'none'/);
    assert.match(result.html, /object-fit:contain/);
    assert.match(renderWireframe(dsl).html, /Project image unavailable/);
    for (const bad of ['javascript:alert(1)', 'https://example.com/tracker', url+'?secret=1', url.replace('/7','/8')]) {
        assert.doesNotMatch(renderWireframe(dsl, '', undefined, [{id:7, mime_type:'image/png', url:bad}]).html, /<img /);
    }
    assert.doesNotMatch(renderWireframe(dsl, '', undefined, [{id:7,mime_type:'text/html',url}]).html, /<img /);
    assert.ok(errors(renderWireframe('screen Brand\n  image Logo asset=javascript:alert(1)')).length);
});

test('preview uses readable typography, compact actions and bounded form widths', () => {
    const result = renderWireframe(`screen Customers
  heading Customers
  form "New customer"
    input Name
    input Email type=email
    button Save primary`);
    assert.deepEqual(errors(result), []);
    assert.doesNotMatch(result.svg + result.html, /monospace/);
    assert.match(result.svg, /font-size="28"/);
    assert.match(result.html, /color-scheme:light/);
    assert.match(result.html, /@media\(max-width:/);
    assert.match(result.html, /\.sheet\{width:100%!important;max-width:var\(--page-width\)/);
    const all = boxes(result.svg);
    assert.equal(all.find(b => b.type === 'form').w, 640);
    assert.equal(all.find(b => b.type === 'button').w, 96);
    assert.ok(all.find(b => b.type === 'button').h >= 42);
    assert.ok(all.filter(b => b.type === 'input').every(b => b.h >= 68));
});

test('HTML uses intrinsic text flow and distinct navigation, section and tab styles', () => {
    const result = renderWireframe(`screen Example width=390
  nav
    link Customers route=/customers active
    link Settings route=/settings
  heading "A heading that wraps at actual word boundaries"
  form "Customer details"
    input Name
  tabs
    tab Overview active
    tab Activity`);
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /A heading that wraps at actual word boundaries/);
    assert.doesNotMatch(result.html, /<br>/);
    assert.match(result.html, /aria-current="page"/);
    assert.match(result.html, /\.form>\.label[^}]+font-size:18px/);
    assert.match(result.html, /\.tabs>\.tab\[data-wf-active=true\]/);
    assert.match(result.html, /height:auto!important;min-height:var\(--min-height\)/);
});

test('responsive rules preserve explicit spacing and grid count; calendar grid is scoped', () => {
    const result = renderWireframe(`screen Example
  grid columns=1
    text One
    text Two
  spacer height=200
  calendar Schedule`);
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /--columns:1;/);
    assert.match(result.html, /--min-height:200px;/);
    assert.match(result.html, /\.grid\{grid-template-columns:repeat\(var\(--columns\),minmax\(0,1fr\)\)\}/);
    assert.match(result.html, /class="calendar-days"/);
    assert.doesNotMatch(result.html, /\.calendar\{display:grid/);
    assert.ok(errors(renderWireframe('screen X\n  input Name constructor=x')).some(d => d.code === 'UNKNOWN_ATTRIBUTE'));
});

test('recursive intrinsic sizing separates root and nested siblings and expands SVG', () => {
    const dsl = `screen "Dashboard"
  row
    sidebar "Navigation"
      nav
        link "Customers" route=/customers
        link "Settings" href=#settings
    main
      card "Customers"
        stack
          heading "A long nested heading with a deliberately narrow wrapping area"
          textarea "Notes" rows=12
        button "Save"
      card "Next section"
        calendar "Schedule"
  heading "After the whole row"
  input "Final field"`;
    const result = renderWireframe(dsl);
    assert.deepEqual(errors(result), []);
    const all = boxes(result.svg);
    const row = all.find(b => b.type === 'row'), after = all.find(b => b.line === 16);
    assert.ok(after.y >= row.y + row.h);
    const area = all.find(b => b.type === 'textarea'), button = all.find(b => b.type === 'button');
    assert.ok(button.y >= area.y + area.h);
    const ast = parseWireframe(dsl);
    function inspect(node) {
        const parent = all.find(b => b.id === node.id);
        const children = node.children.map(n => all.find(b => b.id === n.id));
        for (const child of children) {
            assert.ok(child.x >= parent.x && child.y >= parent.y);
            assert.ok(child.x + child.w <= parent.x + parent.w + .001);
            assert.ok(child.y + child.h <= parent.y + parent.h + .001);
        }
        children.forEach((a, i) => children.slice(i + 1).forEach(b => assert.ok(separated(a, b), `${a.line} overlaps ${b.line}`)));
        node.children.forEach(inspect);
    }
    inspect(ast.screens[0]);
    const height = +result.svg.match(/viewBox="0 0 [\d.]+ ([\d.]+)"/)[1];
    assert.ok(height > 620);
    assert.ok(all.every(b => b.y + b.h < height));
});

test('grid wraps rows using tallest recursive child, width/height/gap zero respected', () => {
    const result = renderWireframe(`screen "Grid" width=400
  grid columns=2 gap=0 padding=0
    textarea "Tall" rows=8
    button "Short"
    card "Third" height=0
      input "Child"
    text "Fourth"
  text "Following"`);
    assert.deepEqual(errors(result), []);
    const all = boxes(result.svg), tall = all.find(b => b.line === 3), third = all.find(b => b.line === 5);
    assert.equal(third.y, tall.y + tall.h);
    assert.ok(result.diagnostics.some(d => d.code === 'HEIGHT_EXPANDED'));
});

test('templates inherit named slots and preserve source lines and unique instance IDs', () => {
    const dsl = `template AppShell
  row
    sidebar "Menu"
      nav
        link "Home" route=/home
    main
      slot content
        text "Default content"
template CustomerShell uses=AppShell
  content
    heading "Customer default"
screen "Customers" id=customers uses=CustomerShell route=/customers
  content
    table "Customers" source=Customer
      column "Name" field=name
    dialog "Edit"
      form
        input "Email" type=email required=true bind=customer.email requirement=FR-012
screen "Other" id=other uses=AppShell`;
    const ast = parseWireframe(dsl);
    assert.deepEqual(errors(ast), []);
    assert.equal(ast.screens.length, 2);
    assert.equal(ast.templates.length, 2);
    const first = renderWireframe(dsl), second = renderWireframe(dsl, 'Other', 'other');
    assert.match(first.svg, /data-type="table" data-line="14"/);
    assert.doesNotMatch(first.svg, /Default content|Customer default/);
    assert.match(second.svg, /Default content/);
    assert.match(first.html, /type="email"/);
    assert.match(first.html, /data-wf-requirement="FR-012"/);
    const ids = [...boxes(first.svg), ...boxes(second.svg)].map(b => b.id);
    assert.equal(new Set(ids).size, ids.length);
    assert.deepEqual(parseWireframe(dsl), ast);
    assert.equal(errors(renderWireframe(dsl, 'Missing', 'missing'))[0].code, 'UNKNOWN_PAGE');
});

test('diagnostic protocol and fail-closed invalid source', () => {
    const invalid = [
        ['screen X\n   input Bad', 'INDENTATION'],
        ['screen X\n\tinput Bad', 'INDENTATION'],
        ['screen X\n    input Bad', 'INDENTATION'],
        ['screen X\n  unknown Nope', 'UNKNOWN_COMPONENT'],
        ['screen X\n  input X onclick=run', 'UNKNOWN_ATTRIBUTE'],
        ['screen X\n  row gap=NaN', 'INVALID_BOUND'],
        ['screen X width=Infinity', 'INVALID_BOUND'],
        ['screen X\n  spacer height=-1', 'INVALID_BOUND'],
        ['screen X\n  grid columns=2.5', 'INVALID_BOUND'],
        ['screen X\n  slider min=9 max=1', 'INVALID_RANGE'],
        ['screen X\n  slider value=Infinity', 'INVALID_NUMBER'],
        ['screen X\n  input X required=yes', 'INVALID_BOOLEAN'],
        ['screen X\n  input X type=file', 'INPUT_TYPE'],
        ['screen "Unclosed', 'SYNTAX'],
        ['screen X\n  input X\n    button Nope', 'LEAF_CHILDREN'],
        ['screen X\n  date X value=2025-02-29', 'INVALID_DATE'],
        ['screen X\n  option X', 'STRUCTURE'],
        ['screen X\n  button A id=dup\n  button B id=dup', 'DUPLICATE_ID'],
        ['screen X width=300 width=400', 'DUPLICATE_ATTRIBUTE'],
        ['screen X uses=Missing', 'UNKNOWN_TEMPLATE'],
        ['template A uses=B\ntemplate B uses=A\nscreen X', 'TEMPLATE_CYCLE'],
        ['template A\n  slot content\nscreen X uses=A\n  content wrong\n    text Nope', 'UNKNOWN_SLOT'],
        ['template A\n  slot content\n  slot content\nscreen X', 'DUPLICATE_SLOT'],
        ['screen X\n  text "abc\u0000def"', 'CONTROL_CHARACTER'],
    ];
    for (const [dsl, code] of invalid) {
        const result = renderWireframe(dsl);
        assert.ok(result.diagnostics.some(d => d.code === code), `${code}: ${JSON.stringify(result.diagnostics)}`);
        const d = result.diagnostics.find(d => d.code === code);
        assert.equal(d.severity, 'error');
        assert.equal(typeof d.line, 'number');
        assert.equal(typeof d.column, 'number');
        assert.equal(typeof d.evidence, 'string');
        assert.match(result.html, /role="alert"|could not be drawn|Invalid wireframe|Unsupported attribute|Next:/i);
        assert.equal(boxes(result.svg).length, 0);
    }
});

test('bounded source, nodes, template expansion, nesting and layout', () => {
    assert.equal(errors(parseWireframe('x'.repeat(100001)))[0].code, 'SOURCE_LIMIT');
    assert.equal(errors(parseWireframe(null))[0].code, 'SOURCE_LIMIT');
    assert.ok(errors(parseWireframe('screen X\n' + '  text X\n'.repeat(1001))).some(d => d.code === 'NODE_LIMIT'));
    const deep = Array.from({ length: 35 }, (_, i) => '  '.repeat(i) + (i ? 'stack' : 'screen X')).join('\n');
    assert.ok(errors(parseWireframe(deep)).some(d => d.code === 'INDENTATION'));
    const big = 'template A\n' + '  text X\n'.repeat(200) + Array.from({ length: 15 }, (_, i) => `screen S${i} uses=A`).join('\n');
    assert.ok(errors(parseWireframe(big)).some(d => d.code === 'EXPANSION_LIMIT'));
    assert.ok(errors(renderWireframe('screen X\n' + '  spacer height=20000\n'.repeat(20))).some(d => d.code === 'LAYOUT_LIMIT'));
});

test('HTML and SVG escape labels and metadata; no executable URL, script, network or event handlers', () => {
    const dsl = `screen "Safe"
  text "<script>alert(1)</script>"
  input '" autofocus onfocus="alert(1)' value='<img src=x onerror=alert(1)>' bind='"><svg/onload=alert(1)>'
  link "Route" href=/customers
  image "Offline" src=https://example.com/image.png`;
    const result = renderWireframe(dsl, '</title><script>alert(2)</script>');
    assert.deepEqual(errors(result), []);
    assert.doesNotMatch(result.html, /<script|<img|<svg/);
    // Check real attribute names, excluding escaped text inside quoted values.
    for (const tag of result.html.match(/<[^>]+>/g)) {
        const outsideValues = tag.replace(/"[^"]*"|'[^']*'/g, '""');
        assert.doesNotMatch(outsideValues, /\son\w+\s*=/i);
    }
    assert.match(result.html, /&lt;script&gt;/);
    assert.match(result.html, /href="#\/customers"/);
    assert.match(result.html, /script-src 'none'/);
    assert.match(result.html, /form-action 'none'/);
    assert.match(result.html, /<base href="about:srcdoc">/);
    assert.doesNotMatch(result.svg, /<script|<foreignObject|<image|onload="/);
    assert.match(result.svg, /xmlns="http:\/\/www.w3.org\/2000\/svg"/);
    for (const url of ['javascript:alert(1)', 'https://evil.test', '//evil.test', '/\\evil.test', 'data:text/html,x']) {
        assert.ok(errors(renderWireframe(`screen X\n  link X href='${url.replaceAll('\\', '\\\\')}'`)).some(d => d.code === 'UNSAFE_URL'), url);
    }
});

test('familiar controls use distinct SVG shapes and native HTML semantics', () => {
    const dsl = `screen Controls
  checkbox "Accept" checked=true
  radio "Choice" name=choice checked=true
  toggle "Enabled" checked=true
  slider "Amount" value=50
  input "Secret" type=password value=secret
  input "Email" type=email required=true
  date "Date" value=2024-02-29
  select "Choose" value=b
    option "Alpha" value=a
    option "Beta" value=b
  dropdown "More" options="One|Two"
  textarea "Notes" rows=4
  progress "Done" value=70
  calendar "Month" value=2024-02-29`;
    const result = renderWireframe(dsl);
    assert.deepEqual(errors(result), []);
    for (const pattern of [/type="checkbox"/, /type="radio"/, /role="switch"/, /type="range"/, /type="password"/, /type="email"/, /type="date"/, /<textarea/, /<select/, /<progress/, /value="b" selected/]) assert.match(result.html, pattern);
    const fragments = ['checkbox', 'radio', 'toggle', 'slider'].map(type => result.svg.match(new RegExp(`<g id="[^"]+" data-type="${type}"[^]*?</g>`))[0]);
    assert.match(fragments[0], /<rect/); assert.doesNotMatch(fragments[0], /<circle/);
    assert.match(fragments[1], /<circle/); assert.doesNotMatch(fragments[1], /<rect/);
    assert.match(fragments[2], /<rect/); assert.match(fragments[2], /<circle/);
    assert.doesNotMatch(result.svg, />secret</);
    assert.match(result.html, /2024-02-29/);
});

test('broad component vocabulary, deterministic output and source updates', () => {
    const components = 'nav sidebar header hero footer section stack row grid card form main list item menu tabs tab modal dialog alert toast draglist field input textarea select dropdown checkbox radio toggle slider date calendar button link badge avatar image divider spacer chart progress text heading'.split(' ');
    assert.ok(components.length >= 40);
    for (const type of components) {
        const dsl = `screen X\n  ${type} "Example"`;
        const result = renderWireframe(dsl);
        assert.deepEqual(errors(result), [], type);
        assert.match(result.svg, new RegExp(`data-type="${type}"`));
        assert.match(result.html, new RegExp(`data-type="${type}"`));
        assert.deepEqual(renderWireframe(dsl), result);
    }
    const a = renderWireframe('screen X\n  text Alpha');
    const b = renderWireframe('screen X\n  text Beta');
    assert.notEqual(a.svg, b.svg); assert.notEqual(a.html, b.html);
    assert.deepEqual(boxes(a.svg).map(b => b.id), boxes(b.svg).map(b => b.id));
    assert.deepEqual(errors(renderWireframe('')), []);
    assert.deepEqual(errors(renderWireframe('heading Hello\ntext World')), []);
    const divider = renderWireframe('screen X\n  divider "Not visible"\n  text Following');
    assert.doesNotMatch(divider.svg, /Not visible/);
});

test('quoted equals labels, escapes, boolean shorthand and explicit IDs are unambiguous', () => {
    const dsl = `screen "name=value"
  input "Email" required id=l3 placeholder="Your \\\"email\\\" address"
  checkbox "Accept" checked`;
    const ast = parseWireframe(dsl);
    assert.deepEqual(errors(ast), []);
    assert.equal(ast.label, 'name=value');
    assert.equal(ast.children[0].attrs.placeholder, 'Your "email" address');
    assert.equal(ast.children[0].attrs.required, 'true');
    const all = boxes(renderWireframe(dsl).svg);
    assert.equal(new Set(all.map(b => b.id)).size, all.length);
    assert.ok(errors(parseWireframe('screen "Name"junk')).some(d => d.code === 'SYNTAX'));
    assert.ok(errors(parseWireframe('screen X\n  text "\ud800"')).some(d => d.code === 'CONTROL_CHARACTER'));
    assert.deepEqual(errors(parseWireframe('screen X\n  text "🙂"')), []);
});

test('selected display matches native select and unsupported behavior is explicit', () => {
    const result = renderWireframe(`screen X
  select "Choice" value=b
    option "Alpha" value=a
    option "Beta" value=b
  draglist "Items"
  chart "Revenue" source=Customer
  button "Save" action=submit
  calendar "Month"`);
    assert.deepEqual(errors(result), []);
    assert.match(result.svg, />Beta</);
    assert.doesNotMatch(result.svg, />Alpha</);
    for (const code of ['STATIC_DRAGLIST', 'STATIC_CHART', 'STATIC_SOURCE', 'STATIC_ACTION', 'STATIC_INTERACTION']) assert.ok(result.diagnostics.some(d => d.code === code && d.severity === 'warning'));
    assert.ok(errors(renderWireframe('screen X\n  calendar X width=100')).some(d => d.code === 'LAYOUT_LIMIT'));
    assert.ok(errors(parseWireframe('screen X\n  select X value=bad options="a|b"')).some(d => d.code === 'OPTION_SELECTION'));
});


test('navigational button href/route is valid and Merrypak-ish multi-page outlines render', () => {
    const dsl = `screen "Overview · Merrypak" device=desktop route=/
  stack
    heading "Merrypak journey map"
    text "Routes from the project route table."
    heading shop
    button "Products" href=/products
    link "Cart" route=/cart
screen "Products" id=products device=desktop route=/products
  stack
    heading Products
    text "Route /products"
    button "Overview" route=/
    form "Add to cart"
      input "Qty" type=number min=1 max=99 value=1
      button "Add" variant=primary requirement=FR-001 bind=Cart.qty`;
    const ast = parseWireframe(dsl);
    assert.deepEqual(errors(ast), []);
    assert.equal(ast.screens.length, 2);
    const overview = renderWireframe(dsl, 'Merrypak', ast.screens[0].id);
    const products = renderWireframe(dsl, 'Merrypak', 'products');
    assert.deepEqual(errors(overview), []);
    assert.deepEqual(errors(products), []);
    assert.doesNotMatch(overview.html + products.html, /Invalid wireframe|could not be drawn/i);
    assert.match(overview.html, /Products/);
    assert.match(products.html, /requirement|FR-001|data-wf-requirement="FR-001"/);
    assert.ok(boxes(overview.svg).some(b => b.type === 'button'));
    const explained = explainWireframeDiagnostics(renderWireframe('screen X\n  input Y onclick=no').diagnostics);
    assert.ok(explained.errorCount >= 1);
    assert.match(explained.steps[0] || '', /Unsupported attribute|Remove the unsupported|onclick/i);
    assert.match(renderWireframe('screen X\n  input Y onclick=no').html, /Next:/);
});


test('screen regions draw header hero section footer and labeled buttons', () => {
    const result = renderWireframe(`screen "Login" device=desktop route=/login
  stack
    header "Account"
    hero "Welcome back"
    heading "Login"
    section "Search by categories"
    form "Login"
      input "Email" type=email
      input "Password" type=password
      button "Login" variant=primary
    footer "Need help"`);
    assert.deepEqual(errors(result), []);
    for (const type of ['header', 'hero', 'section', 'footer', 'button']) {
        assert.match(result.html, new RegExp(`data-type="${type}"`));
        assert.match(result.svg, new RegExp(`data-type="${type}"`));
    }
    assert.match(result.html, />Login</);
    assert.match(result.svg, />Login</);
    assert.match(result.html, /Welcome back/);
    assert.match(result.html, /Need help/);
    assert.doesNotMatch(result.html, /<button[^>]*>\s*<\/button>/);
    const button = boxes(result.svg).find(box => box.type === 'button');
    assert.ok(button.w >= 96 && button.h >= 42);
    assert.ok(button.w < 900);
});
