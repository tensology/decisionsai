// Initiative Settings JavaScript

(function() {
    var STATUS_POLL_MS = 12000;
    var _statusTimer = null;
    var FIELDS = {
        level: 'initiative_level',
        allowTelegram: 'initiative_allow_telegram',
        allowRoutineTasks: 'initiative_allow_routine_tasks',
        askExternalComms: 'initiative_ask_external_comms',
        askFileChanges: 'initiative_ask_file_changes',
        askSensitive: 'initiative_ask_sensitive',
    };

    function setLevel(level) {
        document.getElementById(FIELDS.level).value = level;
        document.querySelectorAll('.initiative-btn').forEach(function(btn) {
            var isActive = btn.dataset.level === level;
            btn.classList.toggle('border-[#f97316]', isActive);
            btn.classList.toggle('bg-[#f97316]/10', isActive);
            btn.classList.toggle('border-white/20', !isActive);
        });
    }

    function init() {
        document.querySelectorAll('.initiative-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                setLevel(btn.dataset.level);
            });
        });
        loadInitiativeSettings();
        startInitiativeStatusPoll();
    }

    function updateDraftNavBadge(count) {
        var badge = document.getElementById('initiative-nav-badge');
        if (!badge) return;
        var n = typeof count === 'number' ? count : 0;
        if (n > 0) {
            badge.textContent = n > 99 ? '99+' : String(n);
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }

    function paintHealthDot(dot, labelEl, state, detail) {
        dot.title = detail || '';
        dot.classList.remove('bg-green-500', 'bg-yellow-400', 'bg-red-500', 'bg-gray-500');
        if (state === 'green') {
            dot.classList.add('bg-green-500');
            labelEl.textContent = 'Cycle status: healthy';
        } else if (state === 'yellow') {
            dot.classList.add('bg-yellow-400');
            labelEl.textContent = 'Cycle status: busy or degraded — ' + (detail || 'see activity logs');
        } else if (state === 'red') {
            dot.classList.add('bg-red-500');
            labelEl.textContent = 'Cycle status: failing repeatedly — ' + (detail || 'check desktop logs');
        } else {
            dot.classList.add('bg-gray-500');
            labelEl.textContent = detail || 'Cycle status: desktop app may not be running';
        }
    }

    window.pollInitiativeStatus = function() {
        fetch('/api/initiative/status').then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(st) {
            var dot = document.getElementById('initiative-health-dot');
            var label = document.getElementById('initiative-health-label');
            if (!dot || !label) return;

            var cf = parseInt(st.consecutive_failures, 10) || 0;
            var running = !!st.running;
            var lastErr = st.last_error ? String(st.last_error).split('\n')[0].slice(0, 120) : '';

            if (cf >= 3) {
                paintHealthDot(dot, label, 'red', lastErr);
                return;
            }
            if (running) {
                paintHealthDot(dot, label, 'yellow', 'A cycle is in progress');
                return;
            }
            if (cf > 0 || lastErr) {
                paintHealthDot(dot, label, 'yellow', lastErr || cf + ' recent failure(s)');
                return;
            }
            if (st.last_cycle_at != null || st.cycle_count > 0) {
                paintHealthDot(dot, label, 'green', '');
                return;
            }
            paintHealthDot(dot, label, 'idle', 'No cycles yet — open the desktop app with Initiative enabled');
        }).catch(function() {
            var dot = document.getElementById('initiative-health-dot');
            var label = document.getElementById('initiative-health-label');
            if (dot && label) paintHealthDot(dot, label, 'idle', 'Could not reach initiative status (is the web API running?)');
        });
    };

    function startInitiativeStatusPoll() {
        window.pollInitiativeStatus();
        if (_statusTimer) clearInterval(_statusTimer);
        _statusTimer = setInterval(window.pollInitiativeStatus, STATUS_POLL_MS);
    }

    // ------------------------------------------------------------------
    // Settings load/save
    // ------------------------------------------------------------------

    window.loadInitiativeSettings = function() {
        fetch('/api/initiative').then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(data) {
            setLevel(data.initiative_level || 'assist');
            document.getElementById(FIELDS.allowTelegram).checked = !!data.initiative_allow_telegram;
            document.getElementById(FIELDS.allowRoutineTasks).checked = !!data.initiative_allow_routine_tasks;
            document.getElementById(FIELDS.askExternalComms).checked = data.initiative_ask_external_comms !== false;
            document.getElementById(FIELDS.askFileChanges).checked = data.initiative_ask_file_changes !== false;
            document.getElementById(FIELDS.askSensitive).checked = data.initiative_ask_sensitive !== false;
        }).catch(function() {});

        loadDrafts();
    };

    window.saveInitiativeSettings = function() {
        var payload = {
            initiative_level: document.getElementById(FIELDS.level).value,
            initiative_allow_telegram: document.getElementById(FIELDS.allowTelegram).checked,
            initiative_allow_routine_tasks: document.getElementById(FIELDS.allowRoutineTasks).checked,
            initiative_ask_external_comms: document.getElementById(FIELDS.askExternalComms).checked,
            initiative_ask_file_changes: document.getElementById(FIELDS.askFileChanges).checked,
            initiative_ask_sensitive: document.getElementById(FIELDS.askSensitive).checked,
        };
        fetch('/api/initiative', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).then(function(r) {
            if (r.ok) showNotification('Initiative settings saved', 'success');
            else showNotification('Failed to save', 'error');
        }).catch(function() { showNotification('Failed to save', 'error'); });
    };

    // ------------------------------------------------------------------
    // Pending drafts
    // ------------------------------------------------------------------

    function loadDrafts() {
        var container = document.getElementById('initiative-drafts');
        var emptyMsg = document.getElementById('initiative-drafts-empty');
        if (!container) return;

        fetch('/api/initiative/drafts').then(function(r) { return r.ok ? r.json() : []; })
        .then(function(drafts) {
            // Remove old draft cards (keep the empty message element)
            container.querySelectorAll('.draft-card').forEach(function(el) { el.remove(); });

            updateDraftNavBadge(drafts ? drafts.length : 0);

            if (!drafts || drafts.length === 0) {
                if (emptyMsg) emptyMsg.style.display = '';
                return;
            }
            if (emptyMsg) emptyMsg.style.display = 'none';

            drafts.forEach(function(draft) {
                var card = document.createElement('div');
                card.className = 'draft-card p-4 rounded-lg border border-white/10 bg-white/5';
                card.innerHTML =
                    '<div class="flex items-start justify-between gap-3">' +
                        '<div class="flex-1 min-w-0">' +
                            '<div class="text-sm text-white font-medium mb-1">' + escapeHtml(draft.description) + '</div>' +
                            '<div class="text-xs text-gray-400 mb-2">Type: ' + escapeHtml(draft.action_type) + '</div>' +
                            (draft.draft && draft.draft !== draft.description
                                ? '<div class="text-xs text-gray-500 bg-black/20 rounded p-2 mb-2 max-h-20 overflow-y-auto">' + escapeHtml(draft.draft) + '</div>'
                                : '') +
                        '</div>' +
                        '<div class="flex gap-2 shrink-0">' +
                            '<button onclick="approveDraft(\'' + draft.id + '\')" class="px-3 py-1 text-xs rounded bg-green-600 hover:bg-green-500 text-white transition-colors">Approve</button>' +
                            '<button onclick="rejectDraft(\'' + draft.id + '\')" class="px-3 py-1 text-xs rounded bg-red-600 hover:bg-red-500 text-white transition-colors">Reject</button>' +
                        '</div>' +
                    '</div>';
                container.appendChild(card);
            });
        }).catch(function() { updateDraftNavBadge(0); });
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    window.approveDraft = function(id) {
        fetch('/api/initiative/drafts/' + id + '/approve', { method: 'POST' })
        .then(function(r) {
            if (r.ok) {
                showNotification('Action approved', 'success');
                loadDrafts();
            } else {
                showNotification('Failed to approve', 'error');
            }
        }).catch(function() { showNotification('Failed to approve', 'error'); });
    };

    window.rejectDraft = function(id) {
        fetch('/api/initiative/drafts/' + id + '/reject', { method: 'POST' })
        .then(function(r) {
            if (r.ok) {
                showNotification('Action rejected', 'success');
                loadDrafts();
            } else {
                showNotification('Failed to reject', 'error');
            }
        }).catch(function() { showNotification('Failed to reject', 'error'); });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
