// MCP settings — /api/mcp (mcp_config.json)

(function () {
    var _rows = [];

    function esc(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/"/g, '&quot;');
    }

    function parseEnvLines(text) {
        var out = {};
        (text || '').split('\n').forEach(function (line) {
            line = line.trim();
            if (!line || line.indexOf('=') < 0) return;
            var i = line.indexOf('=');
            var k = line.slice(0, i).trim();
            var v = line.slice(i + 1).trim();
            if (k) out[k] = v;
        });
        return out;
    }

    function envToLines(env) {
        if (!env || typeof env !== 'object') return '';
        return Object.keys(env).sort().map(function (k) {
            return k + '=' + (env[k] == null ? '' : String(env[k]));
        }).join('\n');
    }

    function parseCommandJson(text) {
        try {
            var v = JSON.parse((text || '').trim() || '[]');
            return Array.isArray(v) ? v.map(function (x) { return String(x); }) : [];
        } catch (e) {
            return [];
        }
    }

    function render() {
        var mount = document.getElementById('mcp-servers-mount');
        if (!mount) return;
        mount.innerHTML = '';
        _rows.forEach(function (row, idx) {
            var card = document.createElement('div');
            card.className = 'border border-[#565869] rounded-lg p-4 bg-[#1a1f3a] space-y-3';
            card.innerHTML =
                '<div class="flex flex-wrap gap-3 items-end">' +
                '<div class="flex-1 min-w-[140px]">' +
                '<label class="block text-xs text-[#9ca3af] mb-1">Name</label>' +
                '<input type="text" data-field="name" class="mcp-field w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white" value="' + esc(row.name) + '" placeholder="my_server" />' +
                '</div>' +
                '<div>' +
                '<label class="block text-xs text-[#9ca3af] mb-1">Enabled</label>' +
                '<input type="checkbox" data-field="enabled" class="mcp-field h-5 w-5 rounded border-[#565869]" ' + (row.enabled ? 'checked' : '') + ' />' +
                '</div>' +
                '<div class="min-w-[120px]">' +
                '<label class="block text-xs text-[#9ca3af] mb-1">Transport</label>' +
                '<select data-field="transport" class="mcp-field w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white">' +
                '<option value="stdio"' + (row.transport !== 'sse' ? ' selected' : '') + '>stdio</option>' +
                '<option value="sse"' + (row.transport === 'sse' ? ' selected' : '') + '>sse</option>' +
                '</select>' +
                '</div>' +
                '<button type="button" data-remove="' + idx + '" class="px-3 py-2 rounded-md text-sm bg-red-900/50 text-red-200 hover:bg-red-900">Remove</button>' +
                '</div>' +
                '<div data-stdio-block>' +
                '<label class="block text-xs text-[#9ca3af] mb-1">Command (JSON array)</label>' +
                '<textarea data-field="command" rows="2" class="w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white font-mono">' +
                esc(JSON.stringify(row.command || [])) + '</textarea>' +
                '<label class="block text-xs text-[#9ca3af] mb-1 mt-2">Env (KEY=value per line)</label>' +
                '<textarea data-field="env" rows="2" class="w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white font-mono">' +
                esc(envToLines(row.env)) + '</textarea>' +
                '</div>' +
                '<div data-sse-block class="hidden">' +
                '<label class="block text-xs text-[#9ca3af] mb-1">SSE URL</label>' +
                '<input type="text" data-field="url" class="mcp-field w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white" value="' + esc(row.url) + '" placeholder="https://..." />' +
                '<label class="block text-xs text-[#9ca3af] mb-1 mt-2">Headers (KEY=value per line)</label>' +
                '<textarea data-field="headers" rows="2" class="w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white font-mono">' +
                esc(envToLines(row.headers)) + '</textarea>' +
                '</div>';
            mount.appendChild(card);

            var cmdTa0 = card.querySelector('[data-field="command"]');
            if (cmdTa0) cmdTa0.setAttribute('placeholder', '["executable","arg1"]');

            var transportSel = card.querySelector('[data-field="transport"]');
            var stdioBlock = card.querySelector('[data-stdio-block]');
            var sseBlock = card.querySelector('[data-sse-block]');
            function syncTransport() {
                var t = transportSel && transportSel.value === 'sse' ? 'sse' : 'stdio';
                if (stdioBlock) stdioBlock.classList.toggle('hidden', t === 'sse');
                if (sseBlock) sseBlock.classList.toggle('hidden', t !== 'sse');
            }
            if (transportSel) transportSel.addEventListener('change', syncTransport);
            syncTransport();

            var rm = card.querySelector('[data-remove="' + idx + '"]');
            if (rm) rm.addEventListener('click', function () {
                _rows.splice(idx, 1);
                render();
            });
        });
    }

    function collectPayload() {
        var cards = document.querySelectorAll('#mcp-servers-mount > div');
        var servers = [];
        for (var c = 0; c < cards.length; c++) {
            var card = cards[c];
            var nameEl = card.querySelector('[data-field="name"]');
            var name = nameEl ? nameEl.value.trim() : '';
            if (!name) continue;
            var enEl = card.querySelector('[data-field="enabled"]');
            var enabled = enEl ? !!enEl.checked : true;
            var transport = (card.querySelector('[data-field="transport"]') || {}).value || 'stdio';
            var cmdTa = card.querySelector('[data-field="command"]');
            var command = parseCommandJson(cmdTa ? cmdTa.value : '[]');
            var envTa = card.querySelector('[data-field="env"]');
            var env = parseEnvLines(envTa ? envTa.value : '');
            var urlEl = card.querySelector('[data-field="url"]');
            var url = urlEl ? urlEl.value.trim() : '';
            var hdrTa = card.querySelector('[data-field="headers"]');
            var headers = parseEnvLines(hdrTa ? hdrTa.value : '');
            servers.push({
                name: name,
                enabled: enabled,
                transport: transport === 'sse' ? 'sse' : 'stdio',
                command: transport === 'sse' ? [] : command,
                env: transport === 'sse' ? {} : env,
                url: transport === 'sse' ? url : '',
                headers: transport === 'sse' ? headers : {},
            });
        }
        return { servers: servers };
    }

    window.loadMCPSettings = function () {
        fetch('/api/mcp')
            .then(function (r) { return r.ok ? r.json() : { servers: [] }; })
            .then(function (data) {
                _rows = (data.servers || []).map(function (s) {
                    return {
                        name: s.name || '',
                        enabled: s.enabled !== false,
                        transport: s.transport === 'sse' ? 'sse' : 'stdio',
                        command: Array.isArray(s.command) ? s.command : [],
                        env: s.env && typeof s.env === 'object' ? s.env : {},
                        url: s.url || '',
                        headers: s.headers && typeof s.headers === 'object' ? s.headers : {},
                    };
                });
                render();
            })
            .catch(function () {
                _rows = [];
                render();
            });
    };

    window.saveMCPSettings = function () {
        var payload = collectPayload();
        fetch('/api/mcp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    if (!r.ok) throw new Error(body.error || body.detail || r.statusText);
                    return body;
                });
            })
            .then(function () {
                if (typeof window.showNotification === 'function') {
                    window.showNotification('MCP configuration saved', 'success');
                }
            })
            .catch(function (e) {
                var msg = e && e.message ? e.message : String(e);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Save failed: ' + msg, 'error');
                }
            });
    };

    function init() {
        var addBtn = document.getElementById('mcp-add-row');
        if (addBtn) {
            addBtn.addEventListener('click', function () {
                _rows.push({
                    name: '',
                    enabled: true,
                    transport: 'stdio',
                    command: [],
                    env: {},
                    url: '',
                    headers: {},
                });
                render();
            });
        }
        window.loadMCPSettings();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
