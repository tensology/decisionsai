// Initiative Settings JavaScript

(function() {
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
        }).catch(function() {});
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
