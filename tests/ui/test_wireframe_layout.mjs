import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../distr/gui/web/static/development/planning/wireframe.js', import.meta.url), 'utf8');
const { parseWireframe, renderWireframe } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const errors = result => result.diagnostics.filter(d => d.severity === 'error');
const head = 'screen "Shop" device=desktop route="/" layout=v2 vw=1440 ph=900 bg=white';

test('a layout=v2 screen renders real flex and grid rules', () => {
    const dsl = [head,
        '  stack',
        '    row justify=between align=center fill=accent pad="8 16" gap=12',
        '      text "Logo" w=120',
        '      text "Menu" w=200 at768="w:-"',
        '    grid columns=4 gap=16 rep=4',
        '      box h=80 fill=light',
    ].join('\n');
    const result = renderWireframe(dsl, 'Shop', undefined, [], { standalone: true });
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /justify-content:space-between/);
    assert.match(result.html, /grid-template-columns:repeat\(4/);
    assert.match(result.html, /@media\(max-width:768px\)/);
    assert.match(result.svg, /foreignObject/);
});

test('layout values are validated against a strict grammar', () => {
    for (const bad of ['w=url(x)', 'gap=999', 'columns=0', 'fill=red', 'mar="1 2 3 4 5"', 'line=zz', 'justify=x;y']) {
        const result = parseWireframe(`${head}\n  stack\n    box ${bad}`);
        assert.ok(errors(result).length, `${bad} must be rejected`);
    }
    assert.deepEqual(errors(parseWireframe(`${head}\n  stack\n    box w=50% minh=40 mar="0 auto 0 auto" clip=true`)), []);
    assert.ok(errors(parseWireframe(`${head} note="bad\\u0007"`)).length >= 0);
});

test('phone overrides must be known layout keys', () => {
    assert.ok(errors(parseWireframe(`${head}\n  stack\n    box at768="rep:3"`)).length);
    assert.ok(errors(parseWireframe(`${head}\n  stack\n    box at768="color:red"`)).length);
    assert.deepEqual(errors(parseWireframe(`${head}\n  stack\n    box w=50% at768="w:100%;gap:-"`)), []);
});

test('desktop-only nodes are hidden on phones and mobile-only nodes are not drawn on the artboard', () => {
    const dsl = [head, '  stack', '    text "Menu" show=desktop', '    text "Burger" show=mobile'].join('\n');
    const result = renderWireframe(dsl, 'Shop', undefined, [], { standalone: true });
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /@media\(max-width:768px\)\{[^}]*display:none/);
});

test('scroll boxes clip and the honest note is drawn as text', () => {
    const dsl = [`${head} note="No layout to show: error reply."`, '  box h=320 w=60% fill=light approx=true',
        '  stack h=200 clip=true', '    text "x"'].join('\n');
    const result = renderWireframe(dsl, 'Shop', undefined, [], { standalone: true });
    assert.deepEqual(errors(result), []);
    assert.match(result.html, /No layout to show: error reply\./);
    assert.match(result.html, /overflow:hidden/);
});

test('hostile text never reaches CSS or markup unescaped', () => {
    const dsl = [`${head} note="<img src=x onerror=alert(1)>"`, '  stack', '    text "<script>alert(1)</script>"'].join('\n');
    const result = renderWireframe(dsl, 'Shop', undefined, [], { standalone: true });
    assert.doesNotMatch(result.html, /<script>alert|<img src=x/);
});
