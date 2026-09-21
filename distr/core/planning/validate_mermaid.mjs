// Parse source as diagram data using the exact bundled browser library.
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
let input = '';
for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 700000) throw new Error('Diagram input exceeds limit.');
}
const {source} = JSON.parse(input);
const context = vm.createContext({setTimeout, clearTimeout});
vm.runInContext(await readFile(new URL('../../gui/web/static/vendor/mermaid/mermaid.min.js', import.meta.url), 'utf8'), context);
context.mermaid.initialize({startOnLoad: false, securityLevel: 'strict'});
try {
    const parsed = await context.mermaid.parse(source);
    process.stdout.write(JSON.stringify({valid: true, diagram_type: parsed.diagramType}));
} catch (error) {
    process.stdout.write(JSON.stringify({valid: false, error: String(error.message).slice(0, 1000)}));
}
