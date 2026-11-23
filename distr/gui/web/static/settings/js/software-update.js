(function() {
    'use strict';

    var endpoint = '/api/software-update';
    var active = ['waiting', 'updating', 'preparing', 'restarting'];

    function init() {
        var panel = document.getElementById('software_update_panel');
        if (!panel) return;
        var status = document.getElementById('software_update_status');
        var apply = document.getElementById('software_update_apply');
        var checking = false;
        var monitoring = false;

        function showPanel(visible) {
            panel.hidden = !visible;
        }

        async function request(query, options) {
            var response = await fetch(endpoint + query, {cache: 'no-store', ...options});
            var data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'The update request failed.');
            return data;
        }

        async function refresh(force, keepMessage) {
            if (checking || monitoring) return;
            checking = true;
            apply.disabled = true;
            try {
                var data = await request(force ? '?refresh=true' : '');
                var operation = data.operation || {};
                if (active.includes(operation.phase)) {
                    if (!keepMessage && operation.message) status.textContent = operation.message;
                    showPanel(true);
                    monitor();
                    return;
                }
                if (data.status === 'available') {
                    if (!keepMessage) status.textContent = data.message;
                    apply.hidden = false;
                    showPanel(true);
                } else {
                    apply.hidden = true;
                    if (!monitoring) showPanel(false);
                }
            } catch (error) {
                apply.hidden = true;
                if (!monitoring) showPanel(false);
            } finally {
                checking = false;
                apply.disabled = monitoring;
            }
        }

        async function monitor() {
            if (monitoring) return;
            monitoring = true;
            apply.hidden = true;
            apply.disabled = true;
            showPanel(true);
            var deadline = Date.now() + 15 * 60 * 1000;
            while (Date.now() < deadline) {
                try {
                    var data = await request('?monitor=true');
                    var operation = data.operation || {};
                    if (operation.message) status.textContent = operation.message;
                    if (operation.phase === 'complete' || operation.phase === 'failed') {
                        monitoring = false;
                        if (operation.phase === 'failed') {
                            apply.disabled = false;
                            apply.hidden = true;
                            showPanel(true);
                            return;
                        }
                        await refresh(false, true);
                        return;
                    }
                } catch (error) {
                    status.textContent = 'DecisionsAI is restarting. This page will reconnect automatically.';
                }
                await new Promise(function(resolve) { setTimeout(resolve, 2000); });
            }
            monitoring = false;
            apply.disabled = false;
            status.textContent = 'Still waiting for DecisionsAI. Check software-update.log for progress.';
        }

        apply.addEventListener('click', async function() {
            if (monitoring || checking) return;
            checking = true;
            apply.disabled = true;
            status.textContent = 'Preparing to close and update DecisionsAI…';
            showPanel(true);
            try {
                await request('', {method: 'POST', headers: {'X-Decisions-Update': 'click'}});
                monitor();
            } catch (error) {
                status.textContent = error.message;
                showPanel(true);
            } finally {
                checking = false;
                apply.disabled = monitoring;
            }
        });

        refresh(false);
        setInterval(function() {
            if (!document.hidden) refresh(false);
        }, 300000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
