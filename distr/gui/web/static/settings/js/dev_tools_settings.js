// Third party > "Development agent tools" card: files / browser toggles, calendar provider,
// iCloud (CalDAV) Apple ID + app-specific password (write-only, never shown), and the
// "sign in once" helper for the dedicated Decisions Chrome profile.
// /api/dev-tools/* calls get the internal token from the base.html fetch wrapper.
(function () {
    'use strict';
    var CARD_ID = 'thirdparty_connect_card_dev_tools';
    var BTN = 'px-3 py-1.5 rounded-md text-xs font-medium bg-[#0d1117] border border-[#565869] text-[#ececf1] hover:border-[#f97316] hover:text-white transition-colors';
    var INPUT = 'w-full rounded-md border border-[#565869] bg-[#0d1117] px-3 py-2 text-sm text-white';
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function modal(title) {
        var old = document.getElementById('dev-tools-modal'); if (old) old.remove();
        var wrap = document.createElement('div');
        wrap.id = 'dev-tools-modal';
        wrap.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);padding:1rem';
        wrap.innerHTML = '<div class="w-full max-w-2xl rounded-xl border border-[#565869] bg-[#1a1f3a] p-6 shadow-xl" style="max-height:85vh;overflow-y:auto" role="dialog" aria-modal="true" aria-label="' + esc(title) + '">' +
            '<div class="mb-4 flex items-center justify-between"><h3 class="text-lg font-semibold text-white">' + esc(title) + '</h3><button type="button" data-close class="' + BTN + '">Close</button></div><div data-body></div></div>';
        wrap.addEventListener('click', function (e) { if (e.target === wrap || e.target.hasAttribute('data-close')) wrap.remove(); });
        document.body.appendChild(wrap);
        return wrap.querySelector('[data-body]');
    }
    function toggle(id, label, help, on) {
        return '<label class="mb-3 flex items-center justify-between gap-4 rounded-lg border border-[#565869] bg-[#111827] p-3 cursor-pointer">' +
            '<span><span class="block text-sm font-medium text-white">' + esc(label) + '</span><span class="block text-xs text-[#9ca3af]">' + esc(help) + '</span></span>' +
            '<input type="checkbox" id="' + id + '" class="h-5 w-5 accent-[#f97316]"' + (on ? ' checked' : '') + '></label>';
    }
    function post(path, body) {
        return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
    }
    function open() {
        var body = modal('Development agent tools');
        body.innerHTML = '<p class="text-sm text-[#9ca3af]">Loading…</p>';
        fetch('/api/dev-tools/settings', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(draw)
            .catch(function () { body.innerHTML = '<p class="text-sm text-red-300">Could not load settings.</p>'; });
        function draw(st) {
            var prov = st.dev_calendar_provider || 'auto';
            body.innerHTML =
                toggle('devtools-files', 'Files', 'Downloads, Desktop, Documents, the project folder and /Volumes. Deletes go to the Trash; trash, move and overwrite ask you first.', st.dev_files_enabled) +
                toggle('devtools-browser', 'Browser', 'One Chrome window using the separate Decisions profile (not your main profile). Off by default; submits and purchases ask you first.', st.dev_browser_enabled) +
                '<div class="mb-4 rounded-lg border border-[#565869] bg-[#111827] p-3"><div class="mb-2 text-sm font-medium text-white">Decisions Chrome profile</div>' +
                '<div class="mb-2 text-xs text-[#9ca3af]">Sign in once to the sites the agent should use. Stored in ' + esc(st.browser_profile) + '.</div>' +
                '<button type="button" class="' + BTN + '" id="devtools-signin">Open profile to sign in</button> <span class="text-xs text-[#9ca3af]" id="devtools-signin-msg"></span></div>' +
                '<div class="mb-4 rounded-lg border border-[#565869] bg-[#111827] p-3"><div class="mb-2 text-sm font-medium text-white">Calendar</div>' +
                '<label class="mb-2 block text-xs text-[#d1d5db]">Provider <select id="devtools-cal-provider" class="' + INPUT + '">' +
                ['auto', 'google', 'icloud'].map(function (p) { return '<option value="' + p + '"' + (p === prov ? ' selected' : '') + '>' + ({ auto: 'Auto (Google if linked, else iCloud)', google: 'Google', icloud: 'iCloud' })[p] + '</option>'; }).join('') + '</select></label>' +
                '<label class="mb-2 block text-xs text-[#d1d5db]">Apple ID ' + (st.icloud_apple_id_set ? '(saved: ' + esc(st.icloud_apple_id_masked) + ')' : '') +
                '<input id="devtools-icloud-id" type="email" autocomplete="off" class="' + INPUT + '" placeholder="' + (st.icloud_apple_id_set ? 'Leave blank to keep' : 'name@icloud.com') + '"></label>' +
                '<label class="mb-2 block text-xs text-[#d1d5db]">App-specific password ' + (st.icloud_app_password_set ? '(saved)' : '') +
                '<input id="devtools-icloud-pw" type="password" autocomplete="new-password" class="' + INPUT + '" placeholder="' + (st.icloud_app_password_set ? '••••••••  (leave blank to keep)' : 'xxxx-xxxx-xxxx-xxxx') + '"></label>' +
                '<div class="mb-2 text-xs text-[#9ca3af]">Create one at account.apple.com → Sign-In and Security → App-Specific Passwords.</div>' +
                '<label class="mb-2 block text-xs text-[#d1d5db]">iCloud calendar name (optional)<input id="devtools-icloud-cal" class="' + INPUT + '" value="' + esc(st.icloud_calendar_name) + '" placeholder="First calendar"></label>' +
                '<div class="flex flex-wrap gap-2"><button type="button" class="' + BTN + '" id="devtools-save">Save</button><button type="button" class="' + BTN + '" id="devtools-test">Test iCloud</button>' +
                (st.icloud_apple_id_set ? '<button type="button" class="' + BTN + '" id="devtools-clear">Remove iCloud</button>' : '') +
                '<span class="text-xs text-[#9ca3af]" id="devtools-msg"></span></div></div>';
            var msg = body.querySelector('#devtools-msg');
            function save(extra) {
                var payload = Object.assign({
                    dev_files_enabled: body.querySelector('#devtools-files').checked,
                    dev_browser_enabled: body.querySelector('#devtools-browser').checked,
                    dev_calendar_provider: body.querySelector('#devtools-cal-provider').value,
                    icloud_calendar_name: body.querySelector('#devtools-icloud-cal').value,
                    icloud_apple_id: body.querySelector('#devtools-icloud-id').value,
                    icloud_app_password: body.querySelector('#devtools-icloud-pw').value
                }, extra || {});
                return post('/api/dev-tools/settings', payload).then(function (st2) { draw(st2); return st2; });
            }
            body.querySelector('#devtools-files').addEventListener('change', function () { save(); });
            body.querySelector('#devtools-browser').addEventListener('change', function () { save(); });
            body.querySelector('#devtools-save').addEventListener('click', function () { msg.textContent = 'Saving…'; save().then(function () { var m = body.querySelector('#devtools-msg'); if (m) m.textContent = 'Saved'; }); });
            body.querySelector('#devtools-test').addEventListener('click', function () {
                msg.textContent = 'Testing…';
                save().then(function () { return post('/api/dev-tools/icloud/test'); }).then(function (r) { var m = body.querySelector('#devtools-msg'); if (m) m.textContent = r.message || (r.ok ? 'Connected' : 'Failed'); });
            });
            var clr = body.querySelector('#devtools-clear');
            if (clr) clr.addEventListener('click', function () { save({ icloud_clear: true, icloud_apple_id: '', icloud_app_password: '' }); });
            body.querySelector('#devtools-signin').addEventListener('click', function () {
                post('/api/dev-tools/browser/signin').then(function (r) { body.querySelector('#devtools-signin-msg').textContent = r.message || ''; });
            });
        }
    }
    function ensureCard() {
        var grid = document.getElementById('thirdparty_connect_grid');
        if (!grid || document.getElementById(CARD_ID)) return;
        var card = document.createElement('div');
        card.id = CARD_ID;
        card.className = 'relative w-full h-full text-center rounded-xl border border-[#565869] bg-[#111827]/70 p-6 transition-colors cursor-pointer hover:border-[#f97316]';
        card.setAttribute('role', 'button'); card.setAttribute('tabindex', '0');
        card.innerHTML = '<div class="flex h-full flex-col items-center justify-center gap-4"><div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-white/95 p-2 shadow-sm">' +
            '<svg viewBox="0 0 24 24" class="h-10 w-10" fill="none" stroke="#111827" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"></path></svg></div>' +
            '<div class="text-base font-semibold leading-5 text-white">Development agent tools</div></div>';
        card.addEventListener('click', open);
        card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
        grid.appendChild(card);
    }
    window.openDevToolsSettings = open;
    function init() {
        ensureCard();
        var grid = document.getElementById('thirdparty_connect_grid');
        if (grid && window.MutationObserver) new MutationObserver(ensureCard).observe(grid, { childList: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
