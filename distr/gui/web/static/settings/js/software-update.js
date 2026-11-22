(function() {
    'use strict';

    var endpoint = '/api/software-update';
    var active = ['waiting', 'updating', 'preparing', 'restarting'];

    function init() {
        var panel = document.getElementById('software_update_panel');
        if (!panel) return;
        var status = document.getElementById('software_update_status');
        var check = document.getElementById('software_update_check');
        var apply = document.getElementById('software_update_apply');
        var release = document.getElementById('software_update_release');
        var checking = false;
        var monitoring = false;

        async function request(query, options) {
            var response = await fetch(endpoint + query, {cache: 'no-store', ...options});
            var data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'The update request failed.');
            return data;
        }

        async function refresh(force, keepMessage) {
            if (checking || monitoring) return;
            checking = true;
            check.disabled = true;
            apply.disabled = true;
            if (!keepMessage) status.textContent = 'Checking for updates…';
            try {
                var data = await request(force ? '?refresh=true' : '');
                var operation = data.operation || {};
                if (active.includes(operation.phase)) {
                    monitor();
                    return;
                }
                if (!keepMessage) {
                    status.textContent = operation.phase === 'failed' && !force
                        ? operation.message
                        : data.message;
                }
                apply.hidden = data.status !== 'available';
                release.hidden = data.status !== 'unsupported';
            } catch (error) {
                if (!keepMessage) status.textContent = 'Could not check for DecisionsAI updates.';
                apply.hidden = true;
            } finally {
                checking = false;
                check.disabled = monitoring;
                apply.disabled = monitoring;
            }
        }

        async function monitor() {
            if (monitoring) return;
            monitoring = true;
            apply.hidden = true;
            check.disabled = true;
            var deadline = Date.now() + 15 * 60 * 1000;
            while (Date.now() < deadline) {
                try {
                    var data = await request('?monitor=true');
                    var operation = data.operation || {};
                    if (operation.message) status.textContent = operation.message;
                    if (operation.phase === 'complete' || operation.phase === 'failed') {
                        monitoring = false;
                        await refresh(false, true);
                        return;
                    }
                } catch (error) {
                    status.textContent = 'DecisionsAI is restarting. This page will reconnect automatically.';
                }
                await new Promise(function(resolve) { setTimeout(resolve, 2000); });
            }
            monitoring = false;
            check.disabled = false;
            status.textContent = 'Still waiting for DecisionsAI. Check software-update.log for progress.';
        }

        check.addEventListener('click', function() { refresh(true); });
        apply.addEventListener('click', async function() {
            if (monitoring || checking) return;
            checking = true;
            check.disabled = true;
            apply.disabled = true;
            status.textContent = 'Preparing to close and update DecisionsAI…';
            try {
                await request('', {method: 'POST', headers: {'X-Decisions-Update': 'click'}});
                monitor();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                checking = false;
                check.disabled = monitoring;
                apply.disabled = monitoring;
            }
        });

        refresh(false);
        setInterval(function() {
            if (!document.hidden && panel.getClientRects().length) refresh(false);
        }, 300000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
