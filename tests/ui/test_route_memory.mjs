import test from 'node:test';
import assert from 'node:assert/strict';
const R = await import(new URL('../../distr/gui/web/static/development/planning/route-memory.js', import.meta.url));
const mem = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)) }; };
test('route memory: per workspace first, then global; broken storage never throws', () => {
    const s = mem();
    assert.equal(R.rememberedRoute(s, 5), null);
    R.rememberRoute(s, 5, { provider: 'openai', model: 'gpt-test' });
    assert.deepEqual(R.rememberedRoute(s, 5), { provider: 'openai', model: 'gpt-test' });
    assert.deepEqual(R.rememberedRoute(s, 9), { provider: 'openai', model: 'gpt-test' });   // global default for a new plan
    R.rememberRoute(s, 9, { provider: 'ollama', model: 'llama' });
    assert.deepEqual(R.rememberedRoute(s, 5), { provider: 'openai', model: 'gpt-test' });   // project keeps its own
    const bad = { getItem: () => '{not json', setItem: () => { throw new Error('quota'); } };
    assert.equal(R.rememberedRoute(bad, 5), null);
    assert.doesNotThrow(() => R.rememberRoute(bad, 5, { provider: 'x' }));
    assert.equal(R.rememberedRoute(null, 5), null);
});
