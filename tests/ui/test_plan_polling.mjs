import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../distr/gui/web/static/development/planning/polling.js', import.meta.url), 'utf8');
const { createPlanPolling } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function fixture(read) {
    const jobs = new Map(), applied = [], errors = [];
    let ident = 0, visible = true;
    const polling = createPlanPolling({ read,
        apply: value => { applied.push(value); return value.status === 'running'; },
        onError: error => errors.push(error), active: () => visible,
        schedule: (fn, delay) => { jobs.set(++ident, { fn, delay }); return ident; },
        cancel: id => jobs.delete(id),
    });
    return { polling, jobs, applied, errors, hide: () => { visible = false; },
        tick: async () => { const [id, job] = [...jobs][0]; jobs.delete(id); await job.fn(); } };
}

test('polls until completion and stops without another request', async () => {
    const values = [{ status: 'running' }, { status: 'complete' }];
    const f = fixture(async () => values.shift());
    f.polling.start();
    await f.tick();
    assert.equal(f.jobs.size, 1);
    await f.tick();
    assert.equal(f.jobs.size, 0);
    assert.deepEqual(f.applied.map(v => v.status), ['running', 'complete']);
});

test('failed reads back off and preserve the running conversation', async () => {
    let calls = 0;
    const f = fixture(async () => { if (++calls < 3) throw Error('offline'); return { status: 'running' }; });
    f.polling.start();
    await f.tick();
    assert.equal([...f.jobs.values()][0].delay, 3000);
    await f.tick();
    assert.equal([...f.jobs.values()][0].delay, 6000);
    assert.equal(f.applied.length, 0);
    await f.tick();
    assert.equal([...f.jobs.values()][0].delay, 1500);
    assert.equal(f.errors.length, 2);
});

test('leaving discards an in-flight response', async () => {
    let resolve;
    const f = fixture(() => new Promise(done => { resolve = done; }));
    f.polling.start();
    const pending = f.tick();
    f.polling.stop();
    resolve({ status: 'complete' });
    await pending;
    assert.equal(f.applied.length, 0);
    assert.equal(f.jobs.size, 0);
});

test('hidden sessions do not issue requests and restarting cancels old timers', async () => {
    let calls = 0;
    const f = fixture(async () => { calls++; return { status: 'running' }; });
    f.polling.start();
    f.polling.start();
    assert.equal(f.jobs.size, 1);
    f.hide();
    await f.tick();
    assert.equal(calls, 0);
    assert.equal(f.jobs.size, 0);
});
