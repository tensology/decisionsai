// Line/block diff + Claude-style in-place edit animation for Planning documents.
// No extra dependencies. Agent updates land via workspace refresh after a chat turn
// (monitor() reloads the workspace when the turn stops) or refreshWorkspace().

export function splitBlocks(text) {
    const src = String(text ?? '').replace(/\r\n/g, '\n');
    if (!src.trim()) return [];
    return src.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
}

export function diffBlocks(oldBlocks, newBlocks) {
    const a = oldBlocks || [];
    const b = newBlocks || [];
    const n = a.length;
    const m = b.length;
    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
        for (let j = m - 1; j >= 0; j--) {
            dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }
    const raw = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
        if (a[i] === b[j]) {
            raw.push({ type: 'equal', text: a[i] });
            i++;
            j++;
        } else if (dp[i + 1][j] >= dp[i][j + 1]) {
            raw.push({ type: 'delete', text: a[i] });
            i++;
        } else {
            raw.push({ type: 'insert', text: b[j] });
            j++;
        }
    }
    while (i < n) raw.push({ type: 'delete', text: a[i++] });
    while (j < m) raw.push({ type: 'insert', text: b[j++] });
    const ops = [];
    for (const op of raw) {
        const prev = ops[ops.length - 1];
        if (op.type === 'insert' && prev?.type === 'delete') {
            ops[ops.length - 1] = { type: 'replace', oldText: prev.text, newText: op.text };
        } else ops.push(op);
    }
    return ops;
}

const wait = (ms, alive) => new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms);
    const check = () => {
        if (!alive || alive()) return;
        clearTimeout(timer);
        resolve(false);
    };
    if (alive) queueMicrotask(check);
});

function frames(count = 1) {
    return new Promise((resolve) => {
        const step = (left) => {
            if (left <= 0) return resolve();
            requestAnimationFrame(() => step(left - 1));
        };
        requestAnimationFrame(() => step(count - 1));
    });
}

// Wait until every named transition (and/or a named animation) finishes on `el`.
// Timeout fallback matches the intended duration so a missed event cannot stall.
export function waitForStyle(el, { properties = [], animation = '', timeoutMs = 0, alive } = {}) {
    return new Promise((resolve) => {
        if (!el || (alive && !alive())) return resolve(false);
        const pending = new Set(properties);
        let settled = false;
        const finish = (ok) => {
            if (settled) return;
            settled = true;
            el.removeEventListener('transitionend', onTransition);
            el.removeEventListener('animationend', onAnimation);
            clearTimeout(timer);
            clearInterval(aliveTimer);
            resolve(ok);
        };
        const onTransition = (event) => {
            if (event.target !== el) return;
            if (pending.size) {
                pending.delete(event.propertyName);
                if (pending.size) return;
            }
            finish(true);
        };
        const onAnimation = (event) => {
            if (event.target !== el) return;
            if (animation && event.animationName !== animation) return;
            finish(true);
        };
        el.addEventListener('transitionend', onTransition);
        el.addEventListener('animationend', onAnimation);
        const timer = setTimeout(() => finish(true), Math.max(0, timeoutMs) + 32);
        const aliveTimer = setInterval(() => {
            if ((alive && !alive()) || !el.isConnected) finish(false);
        }, 32);
    });
}

function reducedMotion() {
    return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
}

async function typewrite(el, text, alive) {
    el.textContent = '';
    const step = text.length > 40 ? 2 : 1;
    const delay = text.length > 80 ? 8 : 16;
    for (let i = 0; i < text.length; i += step) {
        if (alive && !alive()) return false;
        el.textContent = text.slice(0, i + step);
        if (!await wait(delay, alive)) return false;
    }
    el.textContent = text;
    return true;
}

function holdHidden(block) {
    // Keep the block invisible and collapsed after `out` until `in` is ready.
    block.style.opacity = '0';
    if (!block.style.maxHeight) block.style.maxHeight = '0px';
}

function releaseHold(block) {
    block.style.opacity = '';
}

async function startIn(block, body, next, { inMs, alive }) {
    const short = next.length <= 160 && !next.includes('\n');
    // Measure the incoming height while still hidden, then (for typewrite) clear
    // the text again so characters appear only after the fade has started at opacity 0.
    body.textContent = next;
    block.style.maxHeight = 'none';
    const target = Math.max(block.scrollHeight, 1);
    block.style.maxHeight = `${target}px`;
    if (short) body.textContent = '';
    void block.offsetHeight;
    await frames(2);
    if (alive && !alive()) return false;
    // Apply `is-in` (initial opacity 0 via fill-mode) before dropping `is-out`,
    // so no frame can snap to full opacity with the new text already visible.
    block.classList.add('is-in');
    void block.offsetHeight;
    block.classList.remove('is-out', 'is-pending-insert');
    releaseHold(block);
    void block.offsetHeight;
    await frames(2);
    if (alive && !alive()) return false;
    if (short) {
        if (!await typewrite(body, next, alive)) return false;
        if (!await waitForStyle(block, { animation: 'plan-doc-in', timeoutMs: inMs, alive })) return false;
    } else {
        body.textContent = next;
        if (!await waitForStyle(block, { animation: 'plan-doc-in', timeoutMs: inMs, alive })) return false;
    }
    block.style.maxHeight = '';
    return true;
}

export async function animateDocumentEdit(container, oldText, newText, options = {}) {
    const alive = options.alive || (() => container?.isConnected);
    const highlightMs = options.highlightMs ?? 180;
    const outMs = options.outMs ?? 280;
    const inMs = options.inMs ?? 280;
    const staggerMs = options.staggerMs ?? 70;
    if (!container) return false;
    const ops = diffBlocks(splitBlocks(oldText), splitBlocks(newText));
    if (reducedMotion() || options.instant || !ops.some((op) => op.type !== 'equal')) {
        return true;
    }
    const view = document.createElement('div');
    view.className = 'plan-doc-anim';
    view.setAttribute('aria-live', 'polite');
    container.replaceChildren(view);
    const nodes = ops.map((op) => {
        const block = document.createElement('div');
        block.className = 'plan-doc-anim-block';
        block.dataset.anim = op.type;
        block.style.willChange = 'opacity, max-height, transform';
        const body = document.createElement('div');
        body.className = 'plan-doc-anim-text';
        body.textContent = op.type === 'insert' ? '' : (op.type === 'replace' ? op.oldText : op.text);
        if (op.type === 'insert') block.classList.add('is-pending-insert');
        block.append(body);
        view.append(block);
        return { op, block, body };
    });
    const firstChange = nodes.find((node) => node.op.type !== 'equal');
    firstChange?.block.scrollIntoView({ block: 'center', behavior: 'smooth', inline: 'nearest' });
    for (const node of nodes) {
        if (alive && !alive()) return false;
        const { op, block, body } = node;
        if (op.type === 'equal') {
            block.style.willChange = '';
            continue;
        }
        block.classList.add('is-highlight');
        if (op.type !== 'insert') {
            block.style.maxHeight = `${Math.max(block.scrollHeight, 1)}px`;
            void block.offsetHeight;
        }
        if (!await waitForStyle(block, { properties: ['background-color'], timeoutMs: highlightMs, alive })) return false;
        if (op.type === 'delete' || op.type === 'replace') {
            block.classList.add('is-out');
            block.style.maxHeight = '0px';
            if (!await waitForStyle(block, { properties: ['opacity', 'max-height'], timeoutMs: outMs, alive })) return false;
            holdHidden(block);
            if (op.type === 'delete') {
                block.classList.remove('is-highlight');
                block.style.willChange = '';
                if (!await wait(staggerMs, alive)) return false;
                continue;
            }
        }
        if (op.type === 'insert' || op.type === 'replace') {
            holdHidden(block);
            const next = op.type === 'replace' ? op.newText : op.text;
            if (!await startIn(block, body, next, { inMs, alive })) return false;
        }
        block.classList.remove('is-highlight');
        block.style.willChange = '';
        if (!await wait(staggerMs, alive)) return false;
    }
    return true;
}

if (typeof window !== 'undefined') {
    window.__planDocEditAnim = { splitBlocks, diffBlocks, animateDocumentEdit, waitForStyle };
}
