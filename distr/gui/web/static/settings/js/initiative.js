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
        triageTelegram: 'initiative_telegram_notify_suggestions',
        recommendations: 'initiative_recommendations',
        ownChannel: 'initiative_own_channel',
        ownerWhatsapp: 'initiative_owner_whatsapp',
    };
    var OWN_CHANNELS = ['telegram', 'whatsapp', 'voice', 'log'];

    function getOwnChannel() {
        var el = document.querySelector('input[name="' + FIELDS.ownChannel + '"]:checked');
        return el ? el.value : '';
    }

    function setOwnChannel(value) {
        if (OWN_CHANNELS.indexOf(value) < 0) {
            var tg = document.getElementById(FIELDS.allowTelegram);
            value = tg && tg.checked ? 'telegram' : 'log';
        }
        document.querySelectorAll('input[name="' + FIELDS.ownChannel + '"]').forEach(function(r) {
            r.checked = r.value === value;
        });
        syncOwnPanel();
    }

    function syncOwnPanel(level) {
        level = level || document.getElementById(FIELDS.level).value;
        var panel = document.getElementById('initiative-own-updates');
        if (panel) panel.classList.toggle('hidden', level !== 'own');
        document.querySelectorAll('input[name="' + FIELDS.ownChannel + '"]').forEach(function(r) {
            r.disabled = level !== 'own';
        });
        var row = document.getElementById('initiative-owner-whatsapp-row');
        if (row) row.classList.toggle('hidden', !(level === 'own' && getOwnChannel() === 'whatsapp'));
    }
    var LEGACY_LEVELS = { assist: 'operate', assistive: 'operate', passive: 'observe', proactive: 'operate', autonomous: 'own' };

    function normalizeLevel(level) {
        level = LEGACY_LEVELS[level] || level;
        return (level === 'observe' || level === 'operate' || level === 'own') ? level : 'operate';
    }

    function syncRecommendationsAvailability(level) {
        var rec = document.getElementById(FIELDS.recommendations);
        if (!rec) return;
        var available = level === 'operate' || level === 'own';
        rec.disabled = !available;
        if (!available) rec.checked = false;
        var wrap = rec.closest('label');
        if (wrap) wrap.classList.toggle('opacity-50', !available);
    }

    function setLevel(level) {
        level = normalizeLevel(level);
        document.getElementById(FIELDS.level).value = level;
        syncRecommendationsAvailability(level);
        syncOwnPanel(level);
        document.querySelectorAll('.initiative-btn').forEach(function(btn) {
            var isActive = btn.dataset.level === level;
            btn.classList.toggle('border-[#f97316]', isActive);
            btn.classList.toggle('bg-[#f97316]/10', isActive);
            btn.classList.toggle('shadow-[0_0_0_1px_rgba(249,115,22,0.15)]', isActive);
            btn.classList.toggle('border-white/15', !isActive);
        });
    }

    function init() {
        document.querySelectorAll('.initiative-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                setLevel(btn.dataset.level);
                applyLevelDefaults(btn.dataset.level);
            });
        });
        document.querySelectorAll('input[name="' + FIELDS.ownChannel + '"]').forEach(function(r) {
            r.addEventListener('change', function() { syncOwnPanel(); });
        });
        var inlineSaveBtn = document.getElementById('initiative-inline-save');
        if (inlineSaveBtn) {
            inlineSaveBtn.addEventListener('click', function() {
                window.saveInitiativeSettings();
            });
        }
        loadInitiativeSettings();
        startInitiativeStatusPoll();
    }

    function updateDraftNavBadge(count) {
        var badge = document.getElementById('initiative-nav-badge');
        if (!badge) return;
        badge.classList.add('hidden');
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
                askExternal: true, askFiles: true, askSensitive: true, triageTg: false, recommendations: false,
            },
            operate: {
                tg: true, routine: true, lanes: false, workflows: true, cli: true,
                boards: true, backlog: true, whatsapp: true, telegram: true, external: true, email: true,
                askExternal: true, askFiles: true, askSensitive: true, triageTg: true, recommendations: true,
            },
            own: {
                tg: true, routine: true, lanes: true, workflows: true, cli: true,
                boards: true, backlog: true, whatsapp: true, telegram: true, external: true, email: true,
                askExternal: true, askFiles: false, askSensitive: true, triageTg: true, recommendations: true,
            },
        };
        var p = presets[normalizeLevel(level)] || presets.operate;
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
        setChecked(FIELDS.triageTelegram, p.triageTg);
        setChecked(FIELDS.recommendations, p.recommendations);
        syncRecommendationsAvailability(normalizeLevel(level));
        if (normalizeLevel(level) === 'own' && !getOwnChannel()) setOwnChannel('');
        syncOwnPanel(normalizeLevel(level));
    }

    function paintHealthDot(dot, labelEl, state, detail) {
        dot.title = detail || '';
        dot.classList.remove('bg-green-500', 'bg-yellow-400', 'bg-red-500', 'bg-gray-500');
        if (state === 'green') {
            dot.classList.add('bg-green-500');
            labelEl.textContent = 'Healthy';
        } else if (state === 'yellow') {
            dot.classList.add('bg-yellow-400');
            labelEl.textContent = detail || 'Busy or degraded';
        } else if (state === 'red') {
            dot.classList.add('bg-red-500');
            labelEl.textContent = detail || 'Failing repeatedly';
        } else {
            dot.classList.add('bg-gray-500');
            labelEl.textContent = detail || 'Checking';
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
            setLevel(data.initiative_level || 'operate');
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
            setChecked(FIELDS.triageTelegram, !!data.initiative_telegram_notify_suggestions);
            setChecked(FIELDS.recommendations, !!data.initiative_recommendations);
            syncRecommendationsAvailability(normalizeLevel(data.initiative_level || 'operate'));
            var owner = document.getElementById(FIELDS.ownerWhatsapp);
            if (owner) owner.value = data.initiative_owner_whatsapp || '';
            setOwnChannel(data.initiative_own_channel || '');
        }).catch(function() {});
        loadAssistantNudges();

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
            initiative_telegram_notify_suggestions: !!(document.getElementById(FIELDS.triageTelegram) || {}).checked,
            initiative_recommendations: !!(document.getElementById(FIELDS.recommendations) || {}).checked,
            initiative_own_channel: getOwnChannel(),
            initiative_owner_whatsapp: ((document.getElementById(FIELDS.ownerWhatsapp) || {}).value || '').trim(),
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

    // "Be my assistant": gentle, non-modal suggestions. Hidden when there are none
    // or the server doesn't offer the list yet.
    function loadAssistantNudges() {
        var panel = document.getElementById('initiative-assistant-nudges');
        var list = document.getElementById('initiative-assistant-nudges-list');
        if (!panel || !list) return;
        fetch('/api/initiative/assistant/nudges').then(function(r) { return r.ok ? r.json() : { nudges: [] }; })
        .then(function(data) {
            var nudges = (data && data.nudges) || [];
            list.innerHTML = nudges.map(function(n) {
                return '<li class="flex items-start justify-between gap-3">' +
                    '<span>' + escapeHtml(n.text) +
                    (n.why ? '<span class="block text-xs text-gray-500">' + escapeHtml(n.why) + '</span>' : '') +
                    // Same-channel rule: if the original channel couldn't take it, say so; it is never re-sent elsewhere.
                    (n.undelivered ? '<span class="block text-xs text-amber-600">Not delivered: ' +
                        escapeHtml(n.delivery_note || 'the original channel could not take it') + '</span>' : '') +
                    (n.channel_question ? '<span class="block text-xs text-gray-400 mt-1">' +
                        escapeHtml(n.channel_question.text) + ' ' +
                        (n.channel_question.choices || []).map(function(c) {
                            return '<button type="button" class="mr-2 underline hover:text-white" data-ask-id="' +
                                escapeHtml(n.channel_question.id) + '" data-channel="' + escapeHtml(c.value) + '">' +
                                escapeHtml(c.label) + '</button>';
                        }).join('') + '</span>' : '') +
                    '</span>' +
                    '<button type="button" class="shrink-0 text-xs text-gray-400 hover:text-white" data-nudge-id="' +
                    escapeHtml(n.id) + '">Not now</button></li>';
            }).join('');
            panel.classList.toggle('hidden', nudges.length === 0);
            list.querySelectorAll('button[data-ask-id]').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    fetch('/api/initiative/assistant/channel', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ask_id: btn.dataset.askId, channel: btn.dataset.channel }),
                    }).then(function(r) { return r.json(); }).then(function(res) {
                        if (res && res.message) showNotification(res.message, 'success');
                        loadAssistantNudges();
                    }).catch(function() {});
                });
            });
            list.querySelectorAll('button[data-nudge-id]').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    fetch('/api/initiative/assistant/nudges/' + encodeURIComponent(btn.dataset.nudgeId) + '/dismiss', { method: 'POST' })
                    .then(function() { loadAssistantNudges(); }).catch(function() {});
                });
            });
        }).catch(function() { panel.classList.add('hidden'); });
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
