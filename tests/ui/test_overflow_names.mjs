import { register } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';

register(new URL('./strip-query-loader.mjs', import.meta.url).href);
const {
    OVERFLOW_NAME_DEFAULTS,
    PROJECT_NAME_SELECTORS,
    MARQUEE_TARGETS,
    marqueeDistancePx,
    marqueeDurationSec,
    isOverflowing,
} = await import('../../distr/gui/web/static/development/shared/overflow-names.js');

test('defaults: lists hover/focus, header and breadcrumbs auto-scroll, reduced motion keeps the title', () => {
    assert.equal(OVERFLOW_NAME_DEFAULTS.listMode, 'hover');
    assert.equal(OVERFLOW_NAME_DEFAULTS.headerMode, 'auto');
    assert.equal(OVERFLOW_NAME_DEFAULTS.breadcrumbMode, 'auto');
    assert.equal(OVERFLOW_NAME_DEFAULTS.reducedMotion, 'title-only');
    assert.ok(OVERFLOW_NAME_DEFAULTS.pxPerSecond <= 40);
    assert.ok(OVERFLOW_NAME_DEFAULTS.pausePortion > 0);
});

test('the distance is the first copy plus the gap, so a loop lands on the duplicate', () => {
    assert.equal(marqueeDistancePx(200, 40), 240);
    assert.equal(marqueeDistancePx(0, 40), 0);
    assert.equal(marqueeDistancePx(100, 0), 100);
});

test('duration is clamped and scales with distance', () => {
    assert.equal(marqueeDurationSec(10, OVERFLOW_NAME_DEFAULTS), OVERFLOW_NAME_DEFAULTS.minDurationSec);
    assert.equal(marqueeDurationSec(360, OVERFLOW_NAME_DEFAULTS), 10);
    assert.equal(marqueeDurationSec(10_000, OVERFLOW_NAME_DEFAULTS), OVERFLOW_NAME_DEFAULTS.maxDurationSec);
});

test('overflow uses a 1px tolerance so subpixel layout does not flicker', () => {
    assert.equal(isOverflowing(100, 100, 1), false);
    assert.equal(isOverflowing(101, 100, 1), false);
    assert.equal(isOverflowing(102, 100, 1), true);
});

test('project-name selectors never include thread-title hosts, and marquee targets skip project names', () => {
    assert.ok(PROJECT_NAME_SELECTORS.includes('#header-project'));
    assert.ok(PROJECT_NAME_SELECTORS.includes('.project-select span'));
    assert.ok(!PROJECT_NAME_SELECTORS.includes('#header-task'));
    const selectors = MARQUEE_TARGETS.map((t) => t.selector);
    assert.ok(selectors.includes('#header-task'));
    assert.ok(selectors.includes('.task-title'));
    assert.ok(selectors.includes('#development-task-header .task-breadcrumb strong[data-plan-crumb]'));
    assert.ok(!selectors.some((s) => /header-project|project-select|proposal-project|plan-project|terminal-project-titlebar|#terminal-project-name/.test(s)));
    const header = MARQUEE_TARGETS.find((t) => t.selector === '#header-task');
    const list = MARQUEE_TARGETS.find((t) => t.selector === '.task-title');
    assert.equal(header.mode, 'auto');
    assert.equal(list.mode, 'hover');
});
