// Request-token + AbortController guard for plan loads: a newer load or leaving the plan
// cancels the in-flight request and makes any late result a no-op.
export function createLoadGuard(Controller = globalThis.AbortController) {
    let token = 0;
    let controller = null;
    return {
        begin() {
            controller?.abort();
            controller = Controller ? new Controller() : null;
            const mine = ++token;
            return { signal: controller?.signal, isCurrent: () => mine === token };
        },
        cancel() {
            controller?.abort();
            controller = null;
            token += 1;
        },
        get token() { return token; },
    };
}

export const isAbort = (error) => error?.name === 'AbortError';
