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

    function splitCommandLine(text) {
        var input = (text || '').trim();
        var parts = [];
        var current = '';
        var quote = '';
        var escaped = false;
        for (var i = 0; i < input.length; i++) {
            var ch = input[i];
            if (escaped) {
                current += ch;
                escaped = false;
                continue;
            }
            if (ch === '\\') {
                escaped = true;
                continue;
            }
            if (quote) {
                if (ch === quote) quote = '';
                else current += ch;
                continue;
            }
            if (ch === '"' || ch === "'") {
                quote = ch;
                continue;
            }
            if (/\s/.test(ch)) {
                if (current) {
                    parts.push(current);
                    current = '';
                }
                continue;
            }
            current += ch;
        }
        if (current) parts.push(current);
        return parts;
    }

    function quoteCommandPart(part) {
        var value = String(part == null ? '' : part);
        if (!value) return '';
        if (!/\s|["'\\]/.test(value)) return value;
        return '"' + value.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
    }

    function commandToLine(command) {
        return Array.isArray(command) ? command.map(quoteCommandPart).join(' ') : '';
    }

    function deriveServerName(row, index) {
        if (row.name && String(row.name).trim()) return String(row.name).trim();
        if (row.transport === 'sse' && row.url) {
            try {
                return new URL(row.url).hostname.replace(/^www\./, '').split('.')[0] || ('mcp_server_' + (index + 1));
            } catch (e) {
                return 'remote_mcp_' + (index + 1);
            }
        }
        if (Array.isArray(row.command) && row.command.length) {
            var executable = String(row.command[0] || '').split('/').pop() || 'mcp';
            var packageName = row.command.find(function (part) {
                return String(part || '').indexOf('mcp') >= 0 && String(part || '').indexOf('-') >= 0;
            });
            return String(packageName || executable).replace(/^@/, '').replace(/[^A-Za-z0-9_.-]+/g, '_').slice(0, 80) || ('mcp_server_' + (index + 1));
        }
        return 'mcp_server_' + (index + 1);
    }

    function uniqueServerName(name, seen) {
        var base = String(name || 'mcp_server').trim() || 'mcp_server';
        var candidate = base;
        var suffix = 2;
        while (seen[candidate.toLowerCase()]) {
            candidate = base + '_' + suffix;
            suffix += 1;
        }
        seen[candidate.toLowerCase()] = true;
        return candidate;
    }

    function autosaveMCPSettings() {
        return window.saveMCPSettings({ silent: true });
    }

    function render() {
        var mount = document.getElementById('mcp-servers-mount');
        if (!mount) return;
        mount.innerHTML = '';
        _rows.forEach(function (row, idx) {
            var isRemote = row.transport === 'sse';
            var title = deriveServerName(row, idx);
            var card = document.createElement('div');
            card.className = 'relative border border-[#565869] rounded-lg p-4 bg-[#1a1f3a] space-y-3';
            card.setAttribute('data-mcp-index', String(idx));
            card.innerHTML =
                '<div class="flex items-center justify-between gap-3 pr-10">' +
                '<div class="min-w-0">' +
                '<div class="text-sm font-medium text-white truncate">' + esc(title) + '</div>' +
                '<div class="text-xs text-[#9ca3af]">' + (isRemote ? 'Remote MCP endpoint' : 'Local MCP command') + '</div>' +
                '</div>' +
                '</div>' +
                '<button type="button" data-remove="' + idx + '" class="absolute top-3 right-3 flex h-8 w-8 items-center justify-center rounded-full bg-[#40414f] text-white hover:bg-[#565869] transition-colors" aria-label="Remove MCP server">×</button>' +
                '<label class="block text-xs text-[#9ca3af] mb-1">' + (isRemote ? 'URL' : 'Command') + '</label>' +
                '<input type="text" data-field="' + (isRemote ? 'url' : 'command_line') + '" class="mcp-field w-full bg-[#40414f] border border-[#565869] rounded-md px-3 py-2 text-sm text-white font-mono" value="' + esc(isRemote ? row.url : commandToLine(row.command)) + '" placeholder="' + (isRemote ? 'https://...' : 'npx -y @upstash/context7-mcp@latest') + '" />';
            mount.appendChild(card);

            var rm = card.querySelector('[data-remove="' + idx + '"]');
            if (rm) rm.addEventListener('click', function () {
                _rows.splice(idx, 1);
                render();
                autosaveMCPSettings().catch(function () {});
            });
            var field = card.querySelector('[data-field]');
            if (field) {
                field.addEventListener('change', function () {
                    syncRowsFromForm();
                    autosaveMCPSettings().catch(function () {});
                });
                field.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        field.blur();
                    }
                });
            }
        });
    }

    function collectPayload() {
        var cards = document.querySelectorAll('#mcp-servers-mount > div');
        var servers = [];
        var seen = {};
        for (var c = 0; c < cards.length; c++) {
            var card = cards[c];
            var idx = parseInt(card.getAttribute('data-mcp-index') || String(c), 10);
            var row = _rows[idx] || {};
            var transport = row.transport === 'sse' ? 'sse' : 'stdio';
            var commandLine = card.querySelector('[data-field="command_line"]');
            var command = transport === 'sse' ? [] : splitCommandLine(commandLine ? commandLine.value : '');
            var urlEl = card.querySelector('[data-field="url"]');
            var url = urlEl ? urlEl.value.trim() : '';
            var nextRow = {
                name: row.name || '',
                enabled: row.enabled !== false,
                transport: transport,
                command: command,
                env: row.env && typeof row.env === 'object' ? row.env : {},
                url: transport === 'sse' ? url : '',
                headers: row.headers && typeof row.headers === 'object' ? row.headers : {},
            };
            var name = uniqueServerName(deriveServerName(nextRow, c), seen);
            if (transport === 'stdio' && !command.length) continue;
            if (transport === 'sse' && !url) continue;
            servers.push({
                name: name,
                enabled: nextRow.enabled,
                transport: transport,
                command: transport === 'sse' ? [] : command,
                env: transport === 'sse' ? {} : nextRow.env,
                url: transport === 'sse' ? url : '',
                headers: transport === 'sse' ? nextRow.headers : {},
            });
        }
        return { servers: servers };
    }

    function syncRowsFromForm() {
        _rows = collectPayload().servers.map(normalizeServer);
    }

    function normalizeServer(s) {
        return {
            name: s.name || '',
            enabled: s.enabled !== false,
            transport: s.transport === 'sse' ? 'sse' : 'stdio',
            command: Array.isArray(s.command) ? s.command.map(String) : [],
            env: s.env && typeof s.env === 'object' ? s.env : {},
            url: s.url || '',
            headers: s.headers && typeof s.headers === 'object' ? s.headers : {},
        };
    }

    function mergeImportedServers(servers) {
        var imported = (servers || []).map(normalizeServer);
        imported.forEach(function (server) {
            var key = (server.name || '').trim().toLowerCase();
            var existingIndex = _rows.findIndex(function (row) {
                return (row.name || '').trim().toLowerCase() === key;
            });
            if (existingIndex >= 0) _rows[existingIndex] = server;
            else _rows.push(server);
        });
        render();
        return imported.length;
    }

    function setImportStatus(message, type) {
        var el = document.getElementById('mcp-import-status');
        if (!el) return;
        el.textContent = message || '';
        el.className = 'text-xs ' + (type === 'error' ? 'text-red-200' : type === 'success' ? 'text-green-200' : 'text-[#fcd9bd]');
    }

    window.loadMCPSettings = function () {
        fetch('/api/mcp')
            .then(function (r) { return r.ok ? r.json() : { servers: [] }; })
            .then(function (data) {
                _rows = (data.servers || []).map(normalizeServer);
                render();
            })
            .catch(function () {
                _rows = [];
                render();
            });
    };

    window.saveMCPSettings = function (opts) {
        opts = opts || {};
        var payload = collectPayload();
        return fetch('/api/mcp', {
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
                if (!opts.silent && typeof window.showNotification === 'function') {
                    window.showNotification('MCP configuration saved', 'success');
                }
            })
            .catch(function (e) {
                var msg = e && e.message ? e.message : String(e);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Save failed: ' + msg, 'error');
                }
                throw e;
            });
    };

    function init() {
        var modeCommandBtn = document.getElementById('mcp-mode-command');
        var modeJsonBtn = document.getElementById('mcp-mode-json');
        var formCommand = document.getElementById('mcp-form-command');
        var formJson = document.getElementById('mcp-form-json');

        function setActiveMode(mode) {
            if (!modeCommandBtn || !modeJsonBtn || !formCommand || !formJson) {
                return;
            }
            if (mode === 'json') {
                formCommand.classList.add('hidden');
                formJson.classList.remove('hidden');
                modeCommandBtn.className = 'px-3 py-2 text-sm bg-[#111827] text-[#d1d5db] border-r border-[#565869] hover:bg-[#1f2937]';
                modeJsonBtn.className = 'px-3 py-2 text-sm bg-[#f97316] text-white';
            } else {
                formCommand.classList.remove('hidden');
                formJson.classList.add('hidden');
                modeCommandBtn.className = 'px-3 py-2 text-sm bg-[#f97316] text-white';
                modeJsonBtn.className = 'px-3 py-2 text-sm bg-[#111827] text-[#d1d5db] border-l border-[#565869] hover:bg-[#1f2937]';
            }
        }

        if (modeCommandBtn) modeCommandBtn.addEventListener('click', function () { setActiveMode('command'); });
        if (modeJsonBtn) modeJsonBtn.addEventListener('click', function () { setActiveMode('json'); });

        var addBtn = document.getElementById('mcp-add-command');
        var commandInput = document.getElementById('mcp-manual-command');
        function addManualCommand() {
            var command = splitCommandLine(commandInput ? commandInput.value : '');
            if (!command.length) {
                setImportStatus('Enter an MCP command first.', 'error');
                return;
            }
            var row = normalizeServer({
                name: '',
                enabled: true,
                transport: 'stdio',
                command: command,
                env: {},
                url: '',
                headers: {},
            });
            row.name = deriveServerName(row, _rows.length);
            _rows.push(row);
            if (commandInput) commandInput.value = '';
            render();
            autosaveMCPSettings().then(function () {
                setImportStatus('Command added.', 'success');
            }).catch(function () {});
        }
        if (addBtn) addBtn.addEventListener('click', addManualCommand);
        if (commandInput) {
            commandInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    addManualCommand();
                }
            });
        }
        var importBtn = document.getElementById('mcp-import-json-btn');
        var importTa = document.getElementById('mcp-import-json');
        if (importBtn && importTa) {
            importBtn.addEventListener('click', function () {
                var raw = importTa.value.trim();
                if (!raw) {
                    setImportStatus('Paste MCP JSON first.', 'error');
                    return;
                }
                importBtn.disabled = true;
                importBtn.textContent = 'Importing...';
                setImportStatus('Normalizing pasted MCP JSON...', 'info');
                fetch('/api/mcp/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: raw,
                })
                    .then(function (r) {
                        return r.json().then(function (body) {
                            if (!r.ok) throw new Error(body.error || body.detail || r.statusText);
                            return body;
                        });
                    })
                    .then(function (data) {
                        var count = mergeImportedServers(data.servers || []);
                        var warningText = data.warnings && data.warnings.length ? ' ' + data.warnings.join(' ') : '';
                        setImportStatus(count ? ('Imported ' + count + ' server' + (count === 1 ? '' : 's') + '.' + warningText) : 'No valid MCP servers found.', count ? 'success' : 'error');
                        if (count) importTa.value = '';
                        if (count) return autosaveMCPSettings();
                    })
                    .catch(function (e) {
                        setImportStatus((e && e.message) || 'Import failed', 'error');
                    })
                    .finally(function () {
                        importBtn.disabled = false;
                        importBtn.textContent = 'Import JSON';
                    });
            });
        }

        setActiveMode('command');
        window.loadMCPSettings();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
