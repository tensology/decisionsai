// MCP extras: (1) Third party > Connect "MCP server (connect to Decisions)" card + detail modal,
// (2) MCP Servers "View local commands" modal. All /api/mcp* calls are internal-token
// authenticated by the base.html fetch wrapper. The token is never logged.
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function shellQuote(a) {
        a = String(a);
        return /^[A-Za-z0-9_@%+=:,./-]+$/.test(a) ? a : "'" + a.replace(/'/g, "'\\''") + "'";
    }
    function copyText(text, btn) {
        var done = function () {
            if (!btn) return;
            var old = btn.textContent; btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = old; }, 1200);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallback(); });
        } else { fallback(); }
        function fallback() {
            var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
            ta.select(); try { document.execCommand('copy'); done(); } catch (_) {} ta.remove();
        }
    }
    var BTN = 'px-3 py-1.5 rounded-md text-xs font-medium bg-[#0d1117] border border-[#565869] text-[#ececf1] hover:border-[#f97316] hover:text-white transition-colors';
    var BTN_DANGER = 'px-3 py-1.5 rounded-md text-xs font-medium bg-[#7f1d1d] border border-[#b91c1c] text-white hover:bg-[#991b1b] transition-colors';

    function openModal(id, title, bodyHtml) {
        closeModal(id);
        var wrap = document.createElement('div');
        wrap.id = id;
        // Inline styles: the compiled Tailwind lacks z-[1000]; app nav is z-index 50.
        wrap.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);padding:1rem';
        wrap.innerHTML =
            '<div class="w-full max-w-3xl rounded-xl border border-[#565869] bg-[#1a1f3a] p-6 shadow-xl" style="display:flex;flex-direction:column;max-height:85vh;overflow:hidden;position:relative" role="dialog" aria-modal="true" aria-label="' + esc(title) + '">' +
            '<div class="mb-4 flex items-center justify-between gap-4" style="flex:0 0 auto"><h3 class="text-lg font-semibold text-white">' + esc(title) + '</h3>' +
            '<button type="button" data-close class="' + BTN + '" aria-label="Close">Close</button></div>' +
            '<div data-body style="flex:1 1 auto;min-height:0;overflow-y:auto;max-height:calc(85vh - 7rem)">' + bodyHtml + '</div></div>';
        wrap.addEventListener('click', function (e) { if (e.target === wrap || e.target.hasAttribute('data-close')) closeModal(id); });
        document.addEventListener('keydown', function onKey(e) {
            if (e.key === 'Escape') { closeModal(id); document.removeEventListener('keydown', onKey); }
        });
        document.body.appendChild(wrap);
        return wrap.querySelector('[data-body]');
    }
    function closeModal(id) { var el = document.getElementById(id); if (el) el.remove(); }

    // ------------------------------------------------------------ local commands modal
    function renderLocalCommands(body) {
        body.innerHTML = '<p class="text-sm text-[#9ca3af]">Loading…</p>';
        fetch('/api/mcp/local-commands').then(function (r) { return r.json(); }).then(function (data) {
            var rows = (data && data.servers) || [];
            if (!rows.length) { body.innerHTML = '<p class="text-sm text-[#9ca3af]">No local (stdio) MCP servers configured.</p>'; return; }
            body.innerHTML = '<div class="space-y-3">' + rows.map(function (row, i) {
                    var line = (row.command || []).map(shellQuote).join(' ');
                    return '<div class="rounded-lg border border-[#565869] bg-[#111827] p-3" data-row="' + i + '">' +
                        '<div class="mb-2 flex items-center justify-between gap-3">' +
                        '<div class="text-sm font-medium text-white">' + esc(row.name) + (row.enabled ? '' : ' <span class="text-xs text-[#9ca3af]">(disabled)</span>') + '</div>' +
                        '<div class="flex gap-2"><button type="button" class="' + BTN + '" data-copy>Copy</button>' +
                        '<button type="button" class="' + BTN_DANGER + '" data-delete>Delete</button></div></div>' +
                        '<pre class="whitespace-pre-wrap break-all rounded bg-[#0d1117] p-2 text-xs text-[#e5e7eb] font-mono" data-line>' + esc(line) + '</pre>' +
                        '<div class="mt-2 hidden items-center justify-end gap-2 text-xs text-[#fcd9bd]" data-confirm>Delete “' + esc(row.name) + '” from MCP servers?' +
                        '<button type="button" class="' + BTN + '" data-cancel>Cancel</button>' +
                        '<button type="button" class="' + BTN_DANGER + '" data-confirm-delete>Yes, delete</button></div></div>';
                }).join('') + '</div>';
            rows.forEach(function (row, i) {
                var el = body.querySelector('[data-row="' + i + '"]');
                var line = (row.command || []).map(shellQuote).join(' ');
                var confirmEl = el.querySelector('[data-confirm]');
                el.querySelector('[data-copy]').addEventListener('click', function (e) { copyText(line, e.currentTarget); });
                el.querySelector('[data-delete]').addEventListener('click', function () { confirmEl.classList.remove('hidden'); confirmEl.classList.add('flex'); });
                el.querySelector('[data-cancel]').addEventListener('click', function () { confirmEl.classList.add('hidden'); confirmEl.classList.remove('flex'); });
                el.querySelector('[data-confirm-delete]').addEventListener('click', function () {
                    fetch('/api/mcp/servers/' + encodeURIComponent(row.name), { method: 'DELETE' })
                        .then(function (r) { return r.json().then(function (b) { if (!r.ok || !b.success) throw new Error(b.error || r.statusText); }); })
                        .then(function () {
                            renderLocalCommands(body);
                            if (typeof window.loadMCPSettings === 'function') window.loadMCPSettings();
                        })
                        .catch(function (err) { confirmEl.textContent = 'Delete failed: ' + err.message; });
                });
            });
        }).catch(function () { body.innerHTML = '<p class="text-sm text-red-300">Could not load local MCP commands.</p>'; });
    }
    window.openMCPLocalCommands = function () { renderLocalCommands(openModal('mcp-local-commands-modal', 'Local MCP commands', '')); };

    // ------------------------------------------------------------ MCP server mode card
    var CARD_ID = 'thirdparty_connect_card_mcp_server';
    var ICON = '<svg viewBox="0 0 24 24" class="h-10 w-10" fill="none" stroke="#111827" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="1.5"></rect><rect x="3" y="14" width="18" height="6" rx="1.5"></rect><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"></path></svg>';

    function ensureCard() {
        var grid = document.getElementById('thirdparty_connect_grid');
        if (!grid || document.getElementById(CARD_ID)) return;
        var card = document.createElement('div');
        card.id = CARD_ID;
        card.className = 'relative w-full h-full text-center rounded-xl border border-[#565869] bg-[#111827]/70 p-6 transition-colors cursor-pointer hover:border-[#f97316]';
        card.setAttribute('role', 'button'); card.setAttribute('tabindex', '0');
        card.innerHTML = '<div class="flex h-full flex-col items-center justify-center gap-4">' +
            '<div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-white/95 p-2 shadow-sm">' + ICON + '</div>' +
            '<div class="text-base font-semibold leading-5 text-white break-words text-center">MCP server (connect to Decisions)</div></div>';
        card.addEventListener('click', openServerMode);
        card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openServerMode(); } });
        grid.appendChild(card);
    }

    function block(title, text) {
        return '<div class="mb-4"><div class="mb-1 flex items-center justify-between"><div class="text-xs font-medium text-[#d1d5db]">' + esc(title) + '</div>' +
            '<button type="button" class="' + BTN + '" data-copy-block>Copy</button></div>' +
            '<pre class="whitespace-pre-wrap break-all rounded bg-[#0d1117] p-3 text-xs text-[#e5e7eb] font-mono" data-block>' + esc(text) + '</pre></div>';
    }

    function openServerMode() {
        var body = openModal('mcp-server-mode-modal', 'MCP server (connect to Decisions)', '<p class="text-sm text-[#9ca3af]">Loading…</p>');
        var revealed = null;
        function load() {
            fetch('/api/mcp/server-mode').then(function (r) { return r.json(); }).then(function (st) { draw(st); })
                .catch(function () { body.innerHTML = '<p class="text-sm text-red-300">Could not load MCP server settings.</p>'; });
        }
        function draw(st) {
            var tokenShown = revealed || st.token_masked;
            var tokenForConfig = '<YOUR_DECISIONS_MCP_TOKEN>';
            var httpJson = JSON.stringify({ mcpServers: { decisions: { url: st.url, headers: { Authorization: 'Bearer ' + tokenForConfig } } } }, null, 2);
            var curl = "curl -s " + st.url + " \\\n  -H 'Authorization: Bearer " + tokenForConfig + "' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Accept: application/json, text/event-stream' \\\n  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}'";
            var stdioJson = JSON.stringify({ mcpServers: { decisions: { command: '<decisions venv>/bin/python', args: ['-m', 'distr.core.mcp_server'], cwd: '<DecisionsAI repo>', env: { DECISIONS_MCP_SERVER_RELAY: '0' } } } }, null, 2);
            body.innerHTML =
                '<div class="mb-4 flex items-center justify-between gap-4 rounded-lg border border-[#565869] bg-[#111827] p-4">' +
                '<div><div class="text-sm font-medium text-white">Enable MCP server mode</div>' +
                '<div class="text-xs text-[#9ca3af]">Lets local MCP clients use Decisions tools and relayed vendor MCP tools. Loopback only, bearer token required.</div></div>' +
                '<label class="inline-flex cursor-pointer items-center gap-2"><input type="checkbox" id="mcp-server-mode-toggle" class="h-5 w-5 accent-[#f97316]"' + (st.enabled ? ' checked' : '') + '>' +
                '<span class="text-sm text-white">' + (st.enabled ? 'On' : 'Off') + '</span></label></div>' +
                '<p class="mb-4 text-xs ' + (st.restart_required ? 'text-[#fcd9bd]' : 'text-[#9ca3af]') + '">' +
                (st.restart_required ? 'Restart Decisions to apply this change.' : 'Changing this setting requires a Decisions restart.') +
                ' Endpoint is currently ' + (st.mounted ? 'active' : 'not active') + '.</p>' +
                '<div class="mb-4"><div class="mb-1 text-xs font-medium text-[#d1d5db]">Endpoint URL</div>' +
                '<div class="flex items-center gap-2"><code class="flex-1 break-all rounded bg-[#0d1117] p-2 text-xs text-[#e5e7eb]">' + esc(st.url) + '</code>' +
                '<button type="button" class="' + BTN + '" id="mcp-server-copy-url">Copy</button></div></div>' +
                '<div class="mb-4"><div class="mb-1 text-xs font-medium text-[#d1d5db]">Bearer token</div>' +
                '<div class="flex flex-wrap items-center gap-2"><code class="flex-1 break-all rounded bg-[#0d1117] p-2 text-xs text-[#e5e7eb]" id="mcp-server-token">' + esc(tokenShown) + '</code>' +
                '<button type="button" class="' + BTN + '" id="mcp-server-reveal">' + (revealed ? 'Hide' : 'Reveal') + '</button>' +
                '<button type="button" class="' + BTN + '" id="mcp-server-copy-token">Copy</button>' +
                (st.token_from_env ? '' : '<button type="button" class="' + BTN_DANGER + '" id="mcp-server-regen">Regenerate</button>') + '</div>' +
                '<div class="mt-1 hidden text-xs text-[#fcd9bd]" id="mcp-server-regen-confirm">Regenerating disconnects existing clients. <button type="button" class="' + BTN + '" id="mcp-server-regen-yes">Yes, regenerate</button></div></div>' +
                block('HTTP client config (Claude Code / Cursor / Codex-style mcp.json)', httpJson) +
                block('curl test', curl) +
                block('stdio client config (no token; spawns its own process)', stdioJson);
            body.querySelectorAll('[data-copy-block]').forEach(function (b) {
                b.addEventListener('click', function () { copyText(b.parentElement.nextElementSibling.textContent, b); });
            });
            body.querySelector('#mcp-server-copy-url').addEventListener('click', function (e) { copyText(st.url, e.currentTarget); });
            body.querySelector('#mcp-server-reveal').addEventListener('click', function () {
                if (revealed) { revealed = null; draw(st); return; }
                fetchToken().then(function (t) { revealed = t; draw(st); });
            });
            body.querySelector('#mcp-server-copy-token').addEventListener('click', function (e) {
                var btn = e.currentTarget; fetchToken().then(function (t) { copyText(t, btn); });
            });
            var regen = body.querySelector('#mcp-server-regen');
            if (regen) regen.addEventListener('click', function () { body.querySelector('#mcp-server-regen-confirm').classList.remove('hidden'); });
            var regenYes = body.querySelector('#mcp-server-regen-yes');
            if (regenYes) regenYes.addEventListener('click', function () {
                fetch('/api/mcp/server-mode/token/regenerate', { method: 'POST' }).then(function () { revealed = null; load(); });
            });
            body.querySelector('#mcp-server-mode-toggle').addEventListener('change', function (e) {
                fetch('/api/mcp/server-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: e.target.checked }) })
                    .then(function () { load(); });
            });
        }
        function fetchToken() {
            return fetch('/api/mcp/server-mode/token', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { return d.token || ''; });
        }
        load();
    }
    window.openMCPServerModeSettings = openServerMode;

    function init() {
        var btn = document.getElementById('mcp-view-local-commands');
        if (btn) btn.addEventListener('click', window.openMCPLocalCommands);
        ensureCard();
        var grid = document.getElementById('thirdparty_connect_grid');
        if (grid && window.MutationObserver) new MutationObserver(ensureCard).observe(grid, { childList: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
