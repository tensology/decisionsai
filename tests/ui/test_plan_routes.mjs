import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../distr/gui/web/static/development/planning/routes.js', import.meta.url), 'utf8');
const {planningProviderLabel} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('provider labels use server-declared transport, not guessed provider branding', () => {
    const provider = {id: 'openai', name: 'OpenAI'};
    assert.equal(planningProviderLabel(provider, {openai: {runtime_id: 'provider_api'}}), 'OpenAI (API)');
    assert.equal(planningProviderLabel(provider, {openai: {runtime_id: 'cli_harness', backend_id: 'codex'}}), 'OpenAI (Codex CLI)');
    assert.equal(planningProviderLabel(provider), 'OpenAI');
    assert.equal(planningProviderLabel({id:'ollama',name:'Ollama'}, {ollama:{runtime_id:'cli_harness',backend_id:'pi'}}), 'Ollama (Pi)');
});
