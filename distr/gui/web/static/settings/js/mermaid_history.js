// Mermaid History section inside Settings

function escapeMermaidHistoryHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeMermaidHistoryAttr(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeMermaidHistoryTimestamp(ts) {
    if (ts == null || ts === '') return null;
    if (typeof ts === 'string') {
        var parsedText = ts.trim();
        if (!parsedText) return null;
        if (/^\d+(\.\d+)?$/.test(parsedText)) {
            ts = Number(parsedText);
        } else {
            var fromText = new Date(parsedText);
            return Number.isFinite(fromText.getTime()) ? fromText : null;
        }
    }
    var numeric = Number(ts);
    if (!Number.isFinite(numeric) || numeric <= 0) return null;
    if (numeric > 1000000000000) {
        numeric = Math.round(numeric);
    } else if (numeric > 10000000000) {
        numeric = Math.round(numeric);
    } else {
        numeric = Math.round(numeric * 1000);
    }
    var date = new Date(numeric);
    if (!Number.isFinite(date.getTime())) return null;
    if (date.getFullYear() < 2020 || date.getFullYear() > 2100) return null;
    return date;
}

function formatMermaidHistoryTime(ts) {
    var date = normalizeMermaidHistoryTimestamp(ts);
    return date ? date.toLocaleString() : 'Date unavailable';
}

var _mermaidHistoryRequest = null;
var _mermaidHistoryViewerWindow = null;

function openMermaidDiagramViewer(diagramId) {
    if (!diagramId) return;
    var url = '/diagram/?id=' + encodeURIComponent(diagramId);
    if (_mermaidHistoryViewerWindow && !_mermaidHistoryViewerWindow.closed) {
        try {
            _mermaidHistoryViewerWindow.location.href = url;
            _mermaidHistoryViewerWindow.focus();
            return;
        } catch (e) {}
    }
    _mermaidHistoryViewerWindow = window.open(url, 'decisions-mermaid-viewer', 'noopener,noreferrer');
}

var _mermaidHistoryApi = window.DecisionsAPI && typeof window.DecisionsAPI.fetch === 'function'
    ? window.DecisionsAPI.fetch.bind(window.DecisionsAPI)
    : function(url, opts) { return fetch(url, opts).then(function (r) { if (!r.ok) throw new Error(r.statusText); return r.json(); }); };

function removeMermaidHistoryItem(diagramId, diagramTitle) {
    var title = String(diagramTitle || 'this diagram');
    if (!diagramId) return Promise.resolve(false);
    if (!window.DecisionsAPI || typeof window.DecisionsAPI.confirm !== 'function') {
        return Promise.resolve(false);
    }
    return window.DecisionsAPI.confirm({
        title: 'Delete Mermaid diagram',
        message: 'Delete "' + title + '"? This cannot be undone.',
        confirmLabel: 'Delete',
        danger: true,
        onConfirm: function () {}
    }).then(function (confirmed) {
        if (!confirmed) return false;
        return _mermaidHistoryApi('/api/diagrams/' + encodeURIComponent(diagramId), { method: 'DELETE' })
            .then(function () { return true; })
            .catch(function () {
                if (window.DecisionsAPI && window.DecisionsAPI.snackbar) {
                    window.DecisionsAPI.snackbar('Failed to delete diagram.', 'error');
                }
                return false;
            });
    });
}

function fetchMermaidHistoryWithTimeout(timeoutMs) {
    if (_mermaidHistoryRequest && typeof _mermaidHistoryRequest.abort === 'function') {
        try { _mermaidHistoryRequest.abort(); } catch (e) {}
    }
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timeoutId = null;
    _mermaidHistoryRequest = controller;
    if (controller) {
        timeoutId = window.setTimeout(function () {
            try { controller.abort(); } catch (e) {}
        }, timeoutMs || 8000);
    }
    return fetch('/api/diagrams/history', controller ? { signal: controller.signal, cache: 'no-store' } : { cache: 'no-store' })
        .then(function (r) {
            if (timeoutId) window.clearTimeout(timeoutId);
            if (!r.ok) {
                throw new Error('The server responded with status ' + r.status);
            }
            return r.json();
        })
        .finally(function () {
            if (_mermaidHistoryRequest === controller) {
                _mermaidHistoryRequest = null;
            }
        });
}

function loadMermaidHistorySection() {
    var listEl = document.getElementById('mermaidHistoryList');
    if (!listEl) return;
    listEl.innerHTML = '<p class="px-4 py-4 text-sm text-gray-400">Loading diagrams…</p>';
    fetchMermaidHistoryWithTimeout(8000)
        .then(function (data) {
            var items = (data && data.items) || [];
            if (!items.length) {
                listEl.innerHTML = '<p class="px-4 py-4 text-sm text-gray-400">No diagrams yet.</p>';
                return;
            }
            listEl.innerHTML = items.map(function (item) {
                var title = escapeMermaidHistoryHtml(item.title || 'Diagram');
                var createdAt = escapeMermaidHistoryHtml(formatMermaidHistoryTime(item.created_at));
                var id = escapeMermaidHistoryAttr(item.id || '');
                var safeId = id;
                return '' +
                    '<div class="mermaid-history-row border-b border-[#565869]/70 px-3 py-3 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#f97316]/8 transition-colors" data-mermaid-id="' +
                        safeId + '">' +
                        '<div class="min-w-0 flex-1 flex items-center gap-3">' +
                            '<h3 class="text-sm font-semibold text-white truncate">' + title + '</h3>' +
                            '<p class="text-xs text-gray-400 flex-shrink-0 ml-auto">' + createdAt + '</p>' +
                        '</div>' +
                        '<div class="flex-shrink-0">' +
                            '<button type="button" class="mermaid-history-open-btn mermaid-action-btn" data-diagram-id="' +
                                id + '" title="Open diagram in viewer" aria-label="Open diagram in viewer">' +
                                '<svg xmlns="http://www.w3.org/2000/svg" class="mermaid-action-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                                    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z" />' +
                                    '<circle cx="12" cy="12" r="3" />' +
                                '</svg>' +
                            '</button>' +
                    '<button type="button" class="mermaid-history-delete-btn mermaid-action-btn mermaid-action-btn-danger ml-2" data-diagram-id="' +
                        id + '" aria-label="Delete ' + title.replace(/"/g, '&quot;') + '" title="Delete diagram">' +
                        '<svg xmlns="http://www.w3.org/2000/svg" class="mermaid-action-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">' +
                            '<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>' +
                        '</svg>' +
                    '</button>' +
                        '</div>' +
                    '</div>';
            }).join('');
            listEl.querySelectorAll('.mermaid-history-delete-btn').forEach(function (btn) {
                btn.addEventListener('click', function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    var row = btn.closest ? btn.closest('.mermaid-history-row') : null;
                    var diagramId = btn.getAttribute('data-diagram-id');
                    var rowTitle = row ? (row.querySelector('h3') ? row.querySelector('h3').textContent : '') : '';
                    if (!diagramId) return;
                    btn.disabled = true;
                    btn.setAttribute('disabled', 'disabled');
                    removeMermaidHistoryItem(diagramId, rowTitle).then(function (deleted) {
                        if (!deleted) {
                            btn.disabled = false;
                            btn.removeAttribute('disabled');
                            return;
                        }
                        if (row) row.remove();
                        if (listEl.querySelectorAll('.mermaid-history-row').length === 0) {
                            listEl.innerHTML = '<p class=\"px-4 py-4 text-sm text-gray-400\">No diagrams yet.</p>';
                        }
                    }).catch(function () {
                        if (window.DecisionsAPI && window.DecisionsAPI.snackbar) {
                            window.DecisionsAPI.snackbar('Failed to delete diagram.', 'error');
                        } else if (window.alert) {
                            window.alert('Failed to delete diagram.');
                        }
                        btn.disabled = false;
                        btn.removeAttribute('disabled');
                    });
                });
            });
            listEl.querySelectorAll('.mermaid-history-open-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var id = btn.getAttribute('data-diagram-id');
                    if (!id) return;
                    openMermaidDiagramViewer(id);
                });
            });
            listEl.querySelectorAll('.mermaid-history-row').forEach(function (row) {
                row.addEventListener('click', function (evt) {
                    if (evt.target.closest('.mermaid-history-delete-btn') || evt.target.closest('.mermaid-history-open-btn')) {
                        return;
                    }
                    var diagramId = row.getAttribute('data-mermaid-id');
                    openMermaidDiagramViewer(diagramId);
                });
            });
        })
        .catch(function (err) {
            var message = (err && err.name === 'AbortError')
                ? 'Request timed out while loading diagrams.'
                : String((err && err.message) || err);
            listEl.innerHTML = '<p class="px-4 py-4 text-sm text-red-400">Failed to load diagrams: ' +
                message.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</p>';
        });
}

function initMermaidHistorySection() {
    if ((window.location.hash || '').toLowerCase() === '#mermaid') {
        window.setTimeout(loadMermaidHistorySection, 0);
    }
    window.addEventListener('pageshow', function () {
        if ((window.location.hash || '').toLowerCase() === '#mermaid') {
            loadMermaidHistorySection();
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMermaidHistorySection);
} else {
    initMermaidHistorySection();
}

window.loadMermaidHistorySection = loadMermaidHistorySection;
