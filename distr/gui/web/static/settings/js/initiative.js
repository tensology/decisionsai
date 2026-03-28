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

    window.loadInitiativeSettings = function() {
        fetch('/api/initiative').then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(data) {
            setLevel(data.initiative_level || 'assistive');
            document.getElementById(FIELDS.allowTelegram).checked = !!data.initiative_allow_telegram;
            document.getElementById(FIELDS.allowRoutineTasks).checked = !!data.initiative_allow_routine_tasks;
            document.getElementById(FIELDS.askExternalComms).checked = data.initiative_ask_external_comms !== false;
            document.getElementById(FIELDS.askFileChanges).checked = data.initiative_ask_file_changes !== false;
            document.getElementById(FIELDS.askSensitive).checked = data.initiative_ask_sensitive !== false;
        }).catch(function() {});
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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
