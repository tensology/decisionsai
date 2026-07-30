(function () {
    'use strict';

    var state = { sites: [], schedule: [], enabled: false, schedule_enabled: false };
    var draftSites = [];
    var draftCounter = 0;
    var dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    function notify(message, type) {
        if (typeof window.showNotification === 'function') window.showNotification(message, type || 'info');
    }

    async function request(url, options) {
        var response = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
        var payload = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(payload.detail || payload.message || 'Monk Mode could not be updated.');
        return payload;
    }

    function setBusy(element, busy) {
        if (!element) return;
        element.disabled = !!busy;
        element.classList.toggle('is-busy', !!busy);
    }

    function renderStatus() {
        var toggle = document.getElementById('monk-enabled');
        var label = document.getElementById('monk-toggle-label');
        var badge = document.getElementById('monk-status-badge');
        if (toggle) toggle.checked = !!state.enabled;
        if (label) label.textContent = state.enabled ? 'Monk Mode on' : 'Monk Mode off';
        if (!badge) return;
        badge.className = 'monk-status';
        if (state.enabled && state.hosts_applied) {
            badge.textContent = 'On';
            badge.classList.add('is-on');
        } else if (state.enabled) {
            badge.textContent = 'Needs attention';
            badge.classList.add('is-warning');
        } else {
            badge.textContent = 'Off';
        }
    }

    function createDraft(url) {
        draftCounter += 1;
        return { id: 'draft-' + Date.now() + '-' + draftCounter, url: url || '', draft: true };
    }

    function splitAddresses(value) {
        return String(value || '').split(/[\n,;]+/).map(function (item) { return item.trim(); }).filter(Boolean);
    }

    function removeDraft(id) {
        draftSites = draftSites.filter(function (site) { return site.id !== id; });
    }

    function makeSiteRow(site, index) {
        var row = document.createElement('div');
        row.className = 'monk-site-row';
        row.setAttribute('role', 'row');
        row.dataset.id = site.id;

        var number = document.createElement('div');
        number.className = 'monk-site-number';
        number.setAttribute('role', 'cell');
        number.textContent = '#' + (index + 1);

        var addressCell = document.createElement('div');
        addressCell.className = 'monk-site-address';
        addressCell.setAttribute('role', 'cell');
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'monk-site-input';
        input.value = site.url || site.hostname || '';
        input.placeholder = 'example.com';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', 'Website address ' + (index + 1));
        input.addEventListener('input', function () {
            if (site.draft) site.url = input.value;
            row.classList.add('is-dirty');
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                saveSite(site, row, input.value);
            }
        });
        input.addEventListener('paste', function (event) {
            var clipboard = event.clipboardData && event.clipboardData.getData('text');
            var addresses = splitAddresses(clipboard);
            if (addresses.length < 2) return;
            event.preventDefault();
            input.value = addresses[0];
            if (site.draft) site.url = addresses[0];
            addresses.slice(1).forEach(function (address) { draftSites.push(createDraft(address)); });
            renderSites(site.id);
            notify(addresses.length + ' website rows added.', 'success');
        });
        addressCell.appendChild(input);

        var actions = document.createElement('div');
        actions.className = 'monk-site-actions';
        actions.setAttribute('role', 'cell');
        var save = document.createElement('button');
        save.type = 'button';
        save.className = 'monk-row-action monk-row-save';
        save.textContent = 'Save';
        save.addEventListener('click', function () { saveSite(site, row, input.value); });
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'monk-row-action monk-row-remove';
        remove.textContent = site.draft ? 'Discard' : 'Remove';
        remove.addEventListener('click', function () {
            if (site.draft) {
                removeDraft(site.id);
                renderSites();
            } else {
                removeSite(site);
            }
        });
        actions.appendChild(save);
        actions.appendChild(remove);

        row.appendChild(number);
        row.appendChild(addressCell);
        row.appendChild(actions);
        return row;
    }

    function renderSites(focusId) {
        var list = document.getElementById('monk-sites-list');
        var empty = document.getElementById('monk-sites-empty');
        if (!list || !empty) return;
        list.innerHTML = '';
        var sites = (state.sites || []).map(function (site) {
            return Object.assign({}, site, { draft: false });
        }).concat(draftSites);
        empty.classList.toggle('is-hidden', sites.length !== 0);
        sites.forEach(function (site, index) { list.appendChild(makeSiteRow(site, index)); });
        if (focusId) {
            var focusRow = list.querySelector('[data-id="' + focusId + '"]');
            var focusInput = focusRow && focusRow.querySelector('.monk-site-input');
            if (focusInput) {
                focusInput.focus();
                focusInput.setSelectionRange(focusInput.value.length, focusInput.value.length);
            }
        }
    }

    async function saveSite(site, row, value) {
        var address = String(value || '').trim();
        if (!address) {
            notify('Enter a website address.', 'error');
            var emptyInput = row.querySelector('.monk-site-input');
            if (emptyInput) emptyInput.focus();
            return;
        }
        var button = row.querySelector('.monk-row-save');
        setBusy(button, true);
        try {
            state = await request(site.draft ? '/api/monk/sites' : '/api/monk/sites/' + encodeURIComponent(site.id), {
                method: site.draft ? 'POST' : 'PUT',
                body: JSON.stringify({ url: address, label: '' })
            });
            if (site.draft) removeDraft(site.id);
            renderStatus();
            renderSites();
            notify('Website saved.', 'success');
        } catch (error) {
            notify(error.message, 'error');
            setBusy(button, false);
        }
    }

    async function removeSite(site) {
        async function perform() {
            try {
                state = await request('/api/monk/sites/' + encodeURIComponent(site.id), { method: 'DELETE' });
                renderStatus();
                renderSites();
                notify('Website removed.', 'success');
            } catch (error) { notify(error.message, 'error'); }
        }
        if (window.DecisionsAPI && typeof window.DecisionsAPI.confirm === 'function') {
            window.DecisionsAPI.confirm({
                title: 'Remove website',
                message: 'Remove ' + site.hostname + ' from Monk Mode?',
                confirmLabel: 'Remove',
                danger: true,
                onConfirm: perform
            });
        } else if (window.confirm('Remove ' + site.hostname + ' from Monk Mode?')) {
            perform();
        }
    }

    function renderScheduleDays(selectedDays) {
        var days = document.getElementById('monk-schedule-days');
        if (!days) return;
        days.innerHTML = '';
        dayNames.forEach(function (name, index) {
            var label = document.createElement('label');
            label.className = 'monk-day-chip';
            var input = document.createElement('input');
            input.type = 'checkbox';
            input.value = String(index);
            input.checked = selectedDays.indexOf(index) >= 0;
            var text = document.createElement('span');
            text.textContent = name;
            label.appendChild(input);
            label.appendChild(text);
            days.appendChild(label);
        });
    }

    function updateScheduleVisibility() {
        var enabled = document.getElementById('monk-schedule-enabled').checked;
        document.getElementById('monk-schedule-editor').classList.toggle('is-hidden', !enabled);
        document.getElementById('monk-schedule-off-copy').classList.toggle('is-hidden', enabled);
        document.getElementById('monk-schedule-actions').classList.toggle('is-hidden', !enabled);
    }

    function renderSchedule() {
        var windows = state.schedule || [];
        var windowData = windows.filter(function (item) { return item.enabled !== false; })[0] || windows[0] || {
            days: [0, 1, 2, 3, 4], start: '09:00', end: '17:00'
        };
        var editor = document.getElementById('monk-schedule-editor');
        editor.dataset.id = windowData.id || '';
        document.getElementById('monk-schedule-enabled').checked = !!state.schedule_enabled;
        document.getElementById('monk-schedule-start').value = windowData.start || '09:00';
        document.getElementById('monk-schedule-end').value = windowData.end || '17:00';
        renderScheduleDays(windowData.days || [0, 1, 2, 3, 4]);
        updateScheduleVisibility();
    }

    function collectSchedule() {
        var editor = document.getElementById('monk-schedule-editor');
        return {
            id: editor.dataset.id || null,
            start: document.getElementById('monk-schedule-start').value,
            end: document.getElementById('monk-schedule-end').value,
            enabled: true,
            days: Array.prototype.map.call(document.querySelectorAll('#monk-schedule-days input:checked'), function (input) {
                return Number(input.value);
            })
        };
    }

    function renderAll() {
        renderStatus();
        renderSchedule();
        renderSites();
    }

    async function loadMonkMode() {
        if (!document.getElementById('tab-monk')) return;
        try {
            state = await request('/api/monk');
            renderAll();
        } catch (error) {
            notify(error.message, 'error');
        }
    }

    function bind() {
        var panel = document.getElementById('tab-monk');
        if (!panel || panel.dataset.bound === '1') return;
        panel.dataset.bound = '1';

        document.getElementById('monk-enabled').addEventListener('change', async function (event) {
            var toggle = event.target;
            setBusy(toggle, true);
            try {
                state = await request('/api/monk/toggle', { method: 'POST', body: JSON.stringify({ enabled: toggle.checked }) });
                renderStatus();
                notify('Monk Mode is ' + (state.enabled ? 'on.' : 'off.'), 'success');
            } catch (error) {
                toggle.checked = !toggle.checked;
                notify(error.message, 'error');
            } finally {
                setBusy(toggle, false);
            }
        });

        document.getElementById('monk-add-site').addEventListener('click', function () {
            var draft = createDraft('');
            draftSites.push(draft);
            renderSites(draft.id);
        });

        document.getElementById('monk-schedule-enabled').addEventListener('change', async function (event) {
            var toggle = event.target;
            updateScheduleVisibility();
            if (toggle.checked) return;
            setBusy(toggle, true);
            try {
                state = await request('/api/monk/schedule', {
                    method: 'PUT',
                    body: JSON.stringify({ enabled: false, windows: [collectSchedule()] })
                });
                renderSchedule();
                renderStatus();
                notify('Schedule turned off.', 'success');
            } catch (error) {
                toggle.checked = true;
                updateScheduleVisibility();
                notify(error.message, 'error');
            } finally {
                setBusy(toggle, false);
            }
        });

        document.getElementById('monk-save-schedule').addEventListener('click', async function (event) {
            var button = event.currentTarget;
            setBusy(button, true);
            try {
                state = await request('/api/monk/schedule', {
                    method: 'PUT',
                    body: JSON.stringify({
                        enabled: document.getElementById('monk-schedule-enabled').checked,
                        windows: [collectSchedule()]
                    })
                });
                renderSchedule();
                renderStatus();
                notify('Schedule saved.', 'success');
            } catch (error) {
                notify(error.message, 'error');
            } finally {
                setBusy(button, false);
            }
        });
    }

    function init() {
        bind();
        loadMonkMode();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
    window.loadMonkMode = loadMonkMode;
})();
