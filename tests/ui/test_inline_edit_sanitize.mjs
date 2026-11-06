import test from 'node:test';
import assert from 'node:assert/strict';
const base = '../../distr/gui/web/static/development/planning/inline-edit/';
const { sanitizeHtml, safeHref, safeColour, safeStyle } = await import(new URL(base + 'sanitize.js', import.meta.url));
const { parseBlocks, inlineToHtml, markdownToEditableHtml } = await import(new URL(base + 'markdown.js', import.meta.url));

const noActive = (html) => {
    assert.doesNotMatch(html, /<script|<iframe|<img|<svg|<style|\son\w+=|javascript:|data:|vbscript:|expression\(|url\(/i, html);
};

test('allowlisted inline tags survive, canonicalised', () => {
    assert.equal(sanitizeHtml('<u>a</u> <mark>b</mark> <strong>c</strong> <s>d</s> <em>e</em>'), '<u>a</u> <mark>b</mark> <strong>c</strong> <s>d</s> <em>e</em>');
    assert.equal(sanitizeHtml('<span style="color: #ef4444">x</span>'), '<span style="color: #ef4444">x</span>');
    assert.equal(sanitizeHtml('<span style="background-color: rgb(250, 204, 21); font-size: 90px">x</span>'), '<span style="background-color: rgb(250, 204, 21)">x</span>');
    assert.equal(sanitizeHtml('<a href="https://example.com/a?b=1">x</a>'), '<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">x</a>');
});

test('attack strings are neutralised', () => {
    const attacks = [
        '<script>alert(1)</script>ok', '<img src=x onerror=alert(1)>', '<a href="javascript:alert(1)">x</a>',
        '<a href="JaVaScRiPt:alert(1)">x</a>', '<a href="&#106;avascript:alert(1)">x</a>', '<a href=" javascript:alert(1)">x</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>', '<a href="vbscript:x">x</a>', '<span style="color: red; background: url(javascript:x)">x</span>',
        '<span style="color: expression(alert(1))">x</span>', '<span style="color:url(x)">x</span>', '<u onclick="x()">x</u>', '<mark onmouseover=alert(1)>x',
        '<svg onload=alert(1)>', '<iframe src="https://e.com"></iframe>', '<style>*{}</style>', '<<script>script>alert(1)<</script>/script>',
        '<u><mark>unclosed', '</u></mark>stray closers', '<a href="https://ok.com" onclick="x">x</a>', '<!-- <script>x</script> -->', '<span style="color:#fff" onclick=1>',
    ];
    for (const attack of attacks) noActive(sanitizeHtml(attack));
    assert.equal(sanitizeHtml('<script>alert(1)</script>ok'), 'ok');
    assert.equal(sanitizeHtml('<a href="javascript:alert(1)">x</a>'), '<a>x</a>');
    assert.equal(sanitizeHtml('<u><mark>unclosed'), '<u><mark>unclosed</mark></u>');
    assert.equal(sanitizeHtml('</u></mark>stray'), 'stray');
    assert.equal(sanitizeHtml('<span style="color: expression(alert(1))">x</span>'), '<span>x</span>');
    assert.equal(sanitizeHtml('a < b > c & d'), 'a &lt; b &gt; c &amp; d');
});

test('hrefs: only http(s) and mailto, bare domains normalised', () => {
    assert.equal(safeHref('example.com/x'), 'https://example.com/x');
    assert.equal(safeHref('mailto:paul@example.com'), 'mailto:paul@example.com');
    assert.equal(safeHref('paul@example.com'), 'mailto:paul@example.com');
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', 'ftp://x.com', '//evil.com', 'not a url', 'java\tscript:x']) assert.equal(safeHref(bad), '', bad);
});

test('colours: hex, rgb(), hsl(), small named list only', () => {
    for (const ok of ['#fff', '#ef4444', 'rgb(1, 2, 3)', 'rgba(1,2,3,.5)', 'hsl(20 50% 40%)', 'red']) assert.ok(safeColour(ok), ok);
    for (const bad of ['url(x)', 'expression(1)', 'var(--x)', 'redd', '#12', 'rgb(1,2)', 'calc(1)']) assert.equal(safeColour(bad), '', bad);
    assert.equal(safeStyle('color: red; position: fixed'), 'color: red');
});

test('markdown inline renders native marks and kept inline HTML', () => {
    const html = inlineToHtml('**b** ~~s~~ *e* `c<d` [l](https://x.com) <u>u</u> <mark>m</mark> <span style="color: #3b82f6">col</span> <script>x</script>');
    assert.match(html, /<strong>b<\/strong> <s>s<\/s> <em>e<\/em> <code>c&lt;d<\/code> <a href="https:\/\/x.com\/" target="_blank" rel="noopener noreferrer">l<\/a> <u>u<\/u> <mark>m<\/mark> <span style="color: #3b82f6">col<\/span>/);
    noActive(html);
    const unsafe = inlineToHtml('[x](javascript:alert(1)) <img src=x onerror=alert(1)>');
    assert.equal(unsafe, '[x](javascript:alert(1)) ');   // left as inert text, no tag, no href
});

test('blocks: raw fences/tables/quotes kept verbatim, lines map 1:1', () => {
    const md = '# Title\n\nSome **text** with <u>u</u>\n- item one\n  - nested\n1. first\n\n```js\nconst a = 1;\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n> quote\n---\nFR-001: thing';
    const blocks = parseBlocks(md);
    assert.equal(blocks.map((b) => b.source).join('\n'), md);
    assert.deepEqual(blocks.map((b) => b.kind), ['heading', 'gap', 'para', 'item', 'item', 'item', 'gap', 'raw', 'raw', 'raw', 'raw', 'para']);
    const { html } = markdownToEditableHtml(md);
    noActive(html);
    assert.match(html, /<pre class="md-e-raw" data-b="7" spellcheck="false">```js\nconst a = 1;\n```<\/pre>/);
});

test('#9 links with * or _ in the URL are not mangled by emphasis', () => {
    const html = inlineToHtml('see [doc](https://x.io/a*b*c_d_e) and *em* and _it_');
    assert.match(html, /<a href="https:\/\/x\.io\/a\*b\*c_d_e" target="_blank" rel="noopener noreferrer">doc<\/a>/);
    assert.match(html, /<em>em<\/em> and <em>it<\/em>/);
});
