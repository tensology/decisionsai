import { openDrawer } from './canvas-chrome.js?v=20261125-canvas-workspace';

export const HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'];
const EXAMPLE = '# @endpoint-response ';
const MAX_EXAMPLE = 12000;
const MAX_RESPONSE = 128000;

export function endpointExample(source, screenLine) {
    const next = source.split(/\r?\n/)[screenLine]?.trimStart();
    if (!next?.startsWith(EXAMPLE)) return null;
    try {
        const example = JSON.parse(next.slice(EXAMPLE.length));
        return example && Object.hasOwn(example, 'body') ? example : null;
    }
    catch { return null; }
}

export function saveEndpointExample(source, screenLine, example) {
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const lines = source.split(newline);
    const replace = lines[screenLine]?.trimStart().startsWith(EXAMPLE) ? 1 : 0;
    const encoded = JSON.stringify(example);
    if (encoded.length > MAX_EXAMPLE) throw new Error('The JSON example must be under 12,000 characters.');
    lines.splice(screenLine, replace, '  ' + EXAMPLE + encoded);
    return lines.join(newline);
}

function destination(server, path) {
    let origin;
    try { origin = new URL(server.trim()); }
    catch { throw new Error('Enter the full HTTP or HTTPS address of your development server.'); }
    if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password ||
        origin.pathname !== '/' || origin.search || origin.hash) {
        throw new Error('Use only the development server origin, including the port if needed.');
    }
    if (!['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)) {
        throw new Error('Use a local development address on localhost, 127.0.0.1, or [::1].');
    }
    if (!/^\/(?!\/)[^\s\\#{}]*$/.test(path)) throw new Error('Enter a local request path with any route parameters filled in.');
    const url = new URL(path, origin);
    if (url.origin !== origin.origin) throw new Error('The path must stay on the development server.');
    return url;
}

async function responseText(response) {
    if (!response.body) return { text: '', truncated: false };
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let remaining = MAX_RESPONSE;
    let text = '';
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) return { text: text + decoder.decode(), truncated: false };
            text += decoder.decode(value.subarray(0, remaining), { stream: true });
            remaining -= value.length;
            if (remaining < 0) {
                await reader.cancel();
                return { text: text + decoder.decode(), truncated: true };
            }
        }
    } finally {
        reader.releaseLock();
    }
}

export function openEndpointEditor({ label, route, method, projectKey, example, onSave, opener }) {
    const storageKey = 'decisions.plan.endpoint-origin.' + projectKey;
    let request;
    let bodyFromResponse;
    let responseStatus = example?.status ?? null;
    const { drawer } = openDrawer({
        title: label, opener, className: 'plan-endpoint-drawer',
        onClose: () => request?.abort(),
        html: '<div class="plan-endpoint-workbench">' +
            '<form class="plan-endpoint-request">' +
            '<label>Local or development server<input name="server" type="url" placeholder="http://localhost:8000" required></label>' +
            '<div class="plan-endpoint-target"><label>Method<select name="method">' + HTTP_METHODS.map(verb => '<option>' + verb + '</option>').join('') +
            '</select></label><label>Request path<input name="path" placeholder="/api/items" required></label></div>' +
            '<label>Request JSON<textarea name="body" rows="4" placeholder="Optional JSON body"></textarea></label>' +
            '<button type="submit" class="header-button primary">Send</button></form>' +
            '<section class="plan-endpoint-response" aria-label="Response" hidden><h3 data-response-status></h3><pre></pre>' +
            '<button type="button" class="header-button" data-use-response hidden>Use as example</button></section>' +
            '<div class="plan-endpoint-example"><label>JSON response example<textarea name="example" rows="5"></textarea></label>' +
            '<button type="button" class="header-button" data-save-example>Save example</button></div>' +
            '<p role="status" class="plan-endpoint-status"></p></div>',
    });
    const form = drawer.querySelector('form');
    const send = form.querySelector('[type=submit]');
    const response = drawer.querySelector('.plan-endpoint-response');
    const responseHeading = response.querySelector('h3');
    const useResponse = response.querySelector('button');
    const responseOutput = response.querySelector('pre');
    const exampleField = drawer.querySelector('[name=example]');
    const save = drawer.querySelector('[data-save-example]');
    const status = drawer.querySelector('[role=status]');
    const fields = form.elements;
    try { fields.server.value = localStorage.getItem(storageKey) || ''; } catch { /* optional preference */ }
    fields.method.value = HTTP_METHODS.includes(method) ? method : 'GET';
    fields.path.value = route || '';
    exampleField.value = example ? JSON.stringify(example.body, null, 2) : '';
    save.hidden = !onSave;
    save.disabled = !exampleField.value;
    exampleField.readOnly = !onSave;
    exampleField.oninput = () => { save.disabled = !exampleField.value.trim(); };

    function syncMethod() {
        fields.body.disabled = ['GET', 'HEAD'].includes(fields.method.value);
    }
    fields.method.onchange = syncMethod;
    syncMethod();

    form.onsubmit = async (event) => {
        event.preventDefault();
        if (request) return;
        let url;
        let body;
        try {
            url = destination(fields.server.value, fields.path.value.trim());
            if (!fields.body.disabled && fields.body.value.trim()) {
                JSON.parse(fields.body.value);
                body = fields.body.value;
            }
        } catch (error) {
            status.textContent = error instanceof SyntaxError ? 'Request body must be valid JSON.' : error.message;
            return;
        }
        try { localStorage.setItem(storageKey, url.origin); } catch { /* optional preference */ }
        request = new AbortController();
        const timeout = setTimeout(() => request?.abort(), 15000);
        send.disabled = true;
        status.textContent = 'Sending request';
        response.hidden = true;
        useResponse.hidden = true;
        try {
            const result = await fetch(url, {
                method: fields.method.value, body,
                headers: body ? { 'Content-Type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
                credentials: 'omit', redirect: 'error', signal: request.signal,
            });
            const { text, truncated } = await responseText(result);
            let formatted = text;
            if (text && !truncated) {
                try {
                    bodyFromResponse = JSON.parse(text);
                    formatted = JSON.stringify(bodyFromResponse, null, 2);
                    useResponse.hidden = !onSave || formatted.length > MAX_EXAMPLE;
                } catch { /* show the response body as text */ }
            }
            responseHeading.textContent = [result.status, result.statusText].filter(Boolean).join(' ');
            response.dataset.httpStatus = String(result.status);
            responseOutput.textContent = formatted || 'Empty response';
            response.hidden = false;
            status.textContent = truncated ? 'Showing the first 128 KB of the response.' : '';
        } catch (error) {
            status.textContent = error.name === 'AbortError' ? 'The request was cancelled or timed out.' :
                'Could not read the response. Check the server address and whether it allows requests from DecisionsAI.';
        } finally {
            clearTimeout(timeout);
            request = null;
            send.disabled = false;
        }
    };
    useResponse.onclick = () => {
        exampleField.value = JSON.stringify(bodyFromResponse, null, 2);
        responseStatus = Number(response.dataset.httpStatus);
        exampleField.focus();
        exampleField.setSelectionRange(0, 0);
        exampleField.scrollTop = 0;
        save.disabled = false;
    };
    save.onclick = async () => {
        save.disabled = true;
        try {
            const body = JSON.parse(exampleField.value);
            await onSave({ status: responseStatus, body });
            status.textContent = 'Example saved';
        } catch (error) {
            status.textContent = error instanceof SyntaxError ? 'Response example must be valid JSON.' : error.message;
        } finally { save.disabled = false; }
    };
}
