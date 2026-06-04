// Initiative Settings JavaScript

(function() {
    var STATUS_POLL_MS = 12000;
    var _statusTimer = null;
    var FIELDS = {
        level: 'initiative_level',
        allowTelegram: 'initiative_allow_telegram',
        allowRoutineTasks: 'initiative_allow_routine_tasks',
        scanBoards: 'initiative_scan_boards',
        scanExternalBoards: 'initiative_scan_external_boards',
        scanEmail: 'initiative_scan_email',
        scanWhatsapp: 'initiative_scan_whatsapp',
        scanTelegram: 'initiative_scan_telegram',
        suggestBacklogPromotion: 'initiative_suggest_backlog_promotion',
        allowTicketLaneMoves: 'initiative_allow_ticket_lane_moves',
        allowWorkflowStart: 'initiative_allow_workflow_start',
        allowProjectCli: 'initiative_allow_project_cli',
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
            btn.classList.toggle('border-white/15', !isActive);
        });
        paintPostureSummary(level);
    }

    function init() {
        document.querySelectorAll('.initiative-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                setLevel(btn.dataset.level);
                applyLevelDefaults(btn.dataset.level);
            });
        });
        loadInitiativeSettings();
        startInitiativeStatusPoll();
    }

    function updateDraftNavBadge(count) {
        var badge = document.getElementById('initiative-nav-badge');
        if (!badge) return;
        badge.classList.add('hidden');
    }

    function paintPostureSummary(level) {
        var el = document.getElementById('initiative-posture-summary');
        if (!el) return;
        var copy = {
            observe: 'The agent stays quiet and only responds when you ask.',
            assist: 'The agent watches context and suggests useful next steps during conversation.',
            operate: 'The agent runs daily check-ins, sends gated decisions to Telegram, and runs approved routines.',
            own: 'The agent follows through on defined outcomes and only pulls you in for sensitive or unclear decisions.',
        };
        el.textContent = copy[level] || copy.assist;
    }

    function setChecked(id, value) {
        var el = document.getElementById(id);
        if (el) el.checked = !!value;
    }

    function applyLevelDefaults(level) {
        var presets = {
            observe: {
                tg: false, routine: false, lanes: false, workflows: false, cli: false,
                boards: false, backlog: false, whatsapp: false, telegram: false, external: false, email: false,
                askExternal: true, askFiles: true, askSensitive: true,
            },
            assist: {
                tg: false, routine: false, lanes: false, workflows: false, cli: false,
                boards: true, backlog: true, whatsapp: true, telegram: true, external: false, email: false,
                askExternal: true, askFiles: true, askSensitive: true,
            },
            operate: {
                tg: true, routine: true, lanes: false, workflows: true, cli: true,
                boards: true, backlog: true, whatsapp: true, telegram: true, external: true, email: true,
                askExternal: true, askFiles: true, askSensitive: true,
            },
            own: {
                tg: true, routine: true, lanes: true, workflows: true, cli: true,
                boards: true, backlog: true, whatsapp: true, telegram: true, external: true, email: true,
                askExternal: true, askFiles: false, askSensitive: true,
            },
        };
        var p = presets[level] || presets.assist;
        setChecked(FIELDS.allowTelegram, p.tg);
        setChecked(FIELDS.allowRoutineTasks, p.routine);
        setChecked(FIELDS.allowTicketLaneMoves, p.lanes);
        setChecked(FIELDS.allowWorkflowStart, p.workflows);
        setChecked(FIELDS.allowProjectCli, p.cli);
        setChecked(FIELDS.scanBoards, p.boards);
        setChecked(FIELDS.suggestBacklogPromotion, p.backlog);
        setChecked(FIELDS.scanWhatsapp, p.whatsapp);
        setChecked(FIELDS.scanTelegram, p.telegram);
        setChecked(FIELDS.scanExternalBoards, p.external);
        setChecked(FIELDS.scanEmail, p.email);
        setChecked(FIELDS.askExternalComms, p.askExternal);
        setChecked(FIELDS.askFileChanges, p.askFiles);
        setChecked(FIELDS.askSensitive, p.askSensitive);
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
            document.getElementById(FIELDS.scanBoards).checked = data.initiative_scan_boards !== false;
            document.getElementById(FIELDS.scanExternalBoards).checked = !!data.initiative_scan_external_boards;
            document.getElementById(FIELDS.scanEmail).checked = !!data.initiative_scan_email;
            document.getElementById(FIELDS.scanWhatsapp).checked = data.initiative_scan_whatsapp !== false;
            document.getElementById(FIELDS.scanTelegram).checked = data.initiative_scan_telegram !== false;
            document.getElementById(FIELDS.suggestBacklogPromotion).checked = data.initiative_suggest_backlog_promotion !== false;
            document.getElementById(FIELDS.allowTicketLaneMoves).checked = !!data.initiative_allow_ticket_lane_moves;
            document.getElementById(FIELDS.allowWorkflowStart).checked = !!data.initiative_allow_workflow_start;
            document.getElementById(FIELDS.allowProjectCli).checked = !!data.initiative_allow_project_cli;
            document.getElementById(FIELDS.askExternalComms).checked = data.initiative_ask_external_comms !== false;
            document.getElementById(FIELDS.askFileChanges).checked = data.initiative_ask_file_changes !== false;
            document.getElementById(FIELDS.askSensitive).checked = data.initiative_ask_sensitive !== false;
        }).catch(function() {});

        updateDraftNavBadge(0);
    };

    window.saveInitiativeSettings = function() {
        var payload = {
            initiative_level: document.getElementById(FIELDS.level).value,
            initiative_allow_telegram: document.getElementById(FIELDS.allowTelegram).checked,
            initiative_allow_routine_tasks: document.getElementById(FIELDS.allowRoutineTasks).checked,
            initiative_scan_boards: document.getElementById(FIELDS.scanBoards).checked,
            initiative_scan_external_boards: document.getElementById(FIELDS.scanExternalBoards).checked,
            initiative_scan_email: document.getElementById(FIELDS.scanEmail).checked,
            initiative_scan_whatsapp: document.getElementById(FIELDS.scanWhatsapp).checked,
            initiative_scan_telegram: document.getElementById(FIELDS.scanTelegram).checked,
            initiative_suggest_backlog_promotion: document.getElementById(FIELDS.suggestBacklogPromotion).checked,
            initiative_allow_ticket_lane_moves: document.getElementById(FIELDS.allowTicketLaneMoves).checked,
            initiative_allow_workflow_start: document.getElementById(FIELDS.allowWorkflowStart).checked,
            initiative_allow_project_cli: document.getElementById(FIELDS.allowProjectCli).checked,
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
                updateDraftNavBadge(0);
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
                updateDraftNavBadge(0);
            } else {
                showNotification('Failed to reject', 'error');
            }
        }).catch(function() { showNotification('Failed to reject', 'error'); });
    };

    window.rejectAllDrafts = function() {
        var btn = document.getElementById('initiative-reject-all-drafts');
        if (btn && btn.disabled) return;
        if (btn) btn.disabled = true;

        fetch('/api/initiative/drafts/reject-all', { method: 'POST' })
        .then(function(r) {
            return r.json().catch(function() { return {}; }).then(function(data) {
                if (r.ok) {
                    var removed = typeof data.removed === 'number' ? data.removed : 0;
                    showNotification(removed > 0 ? ('Rejected ' + removed + ' pending action' + (removed === 1 ? '' : 's')) : 'No pending actions to reject', 'success');
                    updateDraftNavBadge(0);
                } else {
                    showNotification('Failed to reject pending actions', 'error');
                }
            });
        }).catch(function() {
            showNotification('Failed to reject pending actions', 'error');
        }).finally(function() {
            if (btn) btn.disabled = false;
        });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
