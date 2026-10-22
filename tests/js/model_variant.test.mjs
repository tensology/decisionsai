import { encodedVariant as v } from '../../distr/gui/web/static/shared/model_variant.js';
import assert from 'node:assert/strict';
const cases = [
 ['grok-4.7-medium',{effort:'medium',speed:''}], ['gpt-5.3-codex-low-fast',{effort:'low',speed:'priority'}],
 ['gpt-5.3-codex-high',{effort:'high',speed:''}], ['gpt-5.3-codex-xhigh',{effort:'xhigh',speed:''}],
 ['claude-4.6-sonnet-thinking',{effort:'thinking',speed:''}], ['some-model:fast',{effort:'',speed:'priority'}],
 ['gemini-3-flash-preview',{effort:'',speed:''}], ['deepseek-v4.1-flash:cloud',{effort:'',speed:''}],
 ['gpt-5.3-codex',{effort:'',speed:''}], ['highlight-model',{effort:'',speed:''}], ['fastchat-7b',{effort:'',speed:''}],
 ['provider/max-model-v2',{effort:'',speed:''}], ['cursor-grok-4.6-medium',{effort:'medium',speed:''}],
 ['x-ai/grok-code-fast-1:optimized:free',{effort:'',speed:'priority'}], ['auto',{effort:'',speed:''}],
];
for (const [id, want] of cases) assert.deepEqual(v(id), want, id);
console.log('model_variant: all', cases.length, 'cases pass');
import { displayModelName as dn, shortModelName as sn } from '../../distr/gui/web/static/shared/model_variant.js';
assert.equal(dn('Grok Code Fast (free)'), 'Grok Code Fast');
assert.equal(dn('Some Model (1M ctx)'), 'Some Model (1M ctx)');
assert.equal(dn('X (high) (free)'), 'X');
assert.equal(sn('bytedance-seed/dola-seed-2.0-pro:free', 'ByteDance Seed: Dola Seed 2.0 Pro (free)'), 'Dola Seed 2.0 Pro');
assert.equal(sn('deepseek-v4.1-flash:cloud'), 'DeepSeek V4.1 Flash');
assert.equal(sn('minimax/minimax-m3.1-flash'), 'MiniMax M3.1 Flash');
assert.equal(sn('z-ai/glm-5.1'), 'GLM 5.1');
assert.equal(sn('gpt-5.5'), 'GPT-5.5');
assert.equal(dn('Cohere: North Mini Code (free) (free) (256K ctx)'), 'Cohere: North Mini Code (256K ctx)');
console.log('display name helpers pass');
