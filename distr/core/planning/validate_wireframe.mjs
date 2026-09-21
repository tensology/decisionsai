// Execute the same parser and renderer used by Plan, never model-supplied code.
import { readFile } from 'node:fs/promises';
const rendererSource = await readFile(new URL('../../gui/web/static/development/planning/wireframe.js', import.meta.url), 'utf8');
const { parseWireframe, renderWireframe } = await import(`data:text/javascript;base64,${Buffer.from(rendererSource).toString('base64')}`);
process.stdin.setEncoding('utf8');
let input = '';
for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 700000) throw new Error('Wireframe input exceeds limit.');
}
const request = JSON.parse(input);
const ast = parseWireframe(request.source);
let diagnostics = ast.diagnostics;
if (request.page_id !== undefined && !ast.screens.some(screen => screen.id === request.page_id || screen.attrs.id === request.page_id)) {
    diagnostics.push({severity: 'error', line: 1, message: 'Selected page no longer exists in this wireframe.'});
}
if (!diagnostics.some(item => item.severity === 'error')) {
    diagnostics = ast.screens.flatMap(screen => renderWireframe(request.source, '', screen.id).diagnostics);
}
const references = [];
function collect(node) {
    for (const attribute of ['bind', 'binding', 'requirement', 'asset']) {
        if (node.attrs[attribute]) references.push({attribute, value: node.attrs[attribute], line: node.line});
    }
    node.children.forEach(collect);
}
ast.screens.forEach(collect);
process.stdout.write(JSON.stringify({diagnostics, references}));
