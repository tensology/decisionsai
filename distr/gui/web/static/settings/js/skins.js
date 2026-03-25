// Skins Settings JavaScript
var _skinsList = [];
var _selectedSkin = null;
var _editingSkin = null;
var _editingSkinConfig = null;
var _skinFiles = [];
var _selectedHook = null;

var EVENT_HOOKS = [
    "idle", "hands_free_listening", "ptt_active", "dictation",
    "recording_action", "file_drop_success", "tts_response",
    "running_action", "running_step_runner", "snippet_copied",
    "thinking", "needs_attention"
];
var PLAYBACK_MODES = ["loop", "pingpong"];

async function loadSkinsSettings() {
    try {
        var resp = await fetch('/api/skins');
        if (!resp.ok) throw new Error('Failed to load skins');
        var data = await resp.json();
        _skinsList = data.skins || [];
        _selectedSkin = data.selected_skin || 'oracle';
        var rawSize = data.sphere_size !== undefined ? data.sphere_size : 180;
        var scale = rawSize > 10 ? Math.max(4, Math.min(10, Math.round(rawSize / 20))) : Math.max(4, Math.min(10, parseInt(rawSize, 10) || 9));
        var slider = document.getElementById('skins_oracle_size');
        if (slider) slider.value = scale;
        updateSkinsOracleSizeLabel(scale);
        renderSkinsGrid();
        // Auto-open the selected skin's detail
        showEditorForSkin(_selectedSkin);
    } catch (e) {
        console.error('Error loading skins:', e);
    }
}

function renderSkinsGrid() {
    var grid = document.getElementById('skins_grid');
    if (!grid) return;
    grid.innerHTML = '';
    _skinsList.forEach(function(skin) {
        var sel = skin.folder_name === _selectedSkin;
        var card = document.createElement('div');
        card.className = 'flex flex-col items-center gap-3 p-4 rounded-xl border-2 cursor-pointer transition-all ' +
            (sel ? 'border-[#10a37f] bg-[#10a37f]/10 shadow-lg shadow-[#10a37f]/20' : 'border-[#565869] bg-[#0d1117] hover:border-[#7a7c8c]');
        card.dataset.folder = skin.folder_name;

        // Preview image/video — use the idle animation from the API
        var idleFile = skin.idle_animation || 'idle.webm';
        var previewUrl = '/api/skins/' + encodeURIComponent(skin.folder_name) + '/preview/' + encodeURIComponent(idleFile);
        var previewEl;
        var ext = idleFile.split('.').pop().toLowerCase();
        var isVideo = (ext === 'webm');

        if (skin.type === 'oracle') {
            // Oracle: round preview with cover
            previewEl = document.createElement('img');
            previewEl.src = previewUrl;
            previewEl.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%;';
            previewEl.onerror = function() { this.style.display = 'none'; };
        } else if (isVideo) {
            previewEl = document.createElement('video');
            previewEl.src = previewUrl;
            previewEl.autoplay = true; previewEl.muted = true; previewEl.loop = true;
            previewEl.setAttribute('playsinline', '');
            previewEl.style.cssText = 'width:100%; aspect-ratio:1; object-fit:contain;';
        } else {
            // Static image (png, jpg, webp)
            previewEl = document.createElement('img');
            previewEl.src = previewUrl;
            previewEl.style.cssText = 'width:100%; aspect-ratio:1; object-fit:contain;';
        }

        var previewWrap = document.createElement('div');
        if (skin.type === 'oracle') {
            previewWrap.className = 'w-full aspect-square overflow-hidden rounded-full bg-[#0d1117] flex items-center justify-center';
        } else {
            previewWrap.className = 'w-full aspect-square overflow-hidden rounded-lg bg-[#0d1117] flex items-center justify-center';
        }
        previewWrap.appendChild(previewEl);
        card.appendChild(previewWrap);

        // Name
        var nameEl = document.createElement('span');
        nameEl.className = 'text-sm font-medium text-white text-center';
        nameEl.textContent = skin.name;
        card.appendChild(nameEl);

        // Selected indicator
        if (sel) {
            var badge = document.createElement('span');
            badge.className = 'text-xs text-[#10a37f] font-medium';
            badge.textContent = '✓ Active';
            card.appendChild(badge);
        }

        card.addEventListener('click', function() { selectSkin(skin.folder_name); });
        grid.appendChild(card);
    });
}

function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function selectSkin(folderName) {
    try {
        var resp = await fetch('/api/skins/select', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({skin_name: folderName})
        });
        if (!resp.ok) { var err = await resp.json(); throw new Error(err.detail || 'Failed'); }
        _selectedSkin = folderName;
        renderSkinsGrid();
        showEditorForSkin(folderName);
    } catch (e) {
        console.error('Error selecting skin:', e);
    }
}

async function showEditorForSkin(folderName) {
    var skin = _skinsList.find(function(s) { return s.folder_name === folderName; });
    if (!skin) return;

    var panel = document.getElementById('skin_detail_panel');
    var oracleDetail = document.getElementById('oracle_detail');
    var avatarDetail = document.getElementById('avatar_detail');

    panel.classList.remove('hidden');

    if (skin.type === 'oracle') {
        avatarDetail.classList.add('hidden');
        oracleDetail.classList.remove('hidden');
        document.getElementById('oracle_detail_name').textContent = skin.name;
        await loadOracleEditor(folderName);
    } else {
        oracleDetail.classList.add('hidden');
        avatarDetail.classList.remove('hidden');
        await loadAvatarEditor(folderName, skin.name);
    }
}

// ---------------------------------------------------------------------------
// Oracle editor
// ---------------------------------------------------------------------------

async function loadOracleEditor(folderName) {
    try {
        var r = await Promise.all([
            fetch('/api/skins/' + encodeURIComponent(folderName) + '/config'),
            fetch('/api/skins/' + encodeURIComponent(folderName) + '/files')
        ]);
        if (!r[0].ok || !r[1].ok) throw new Error('Failed');
        _editingSkin = folderName;
        _editingSkinConfig = await r[0].json();
        var allFiles = (await r[1].json()).files || [];
        _skinFiles = allFiles;

        // Oracle skin files: numbered animations (webm or gif)
        var skinFiles = allFiles.filter(function(f) {
            var name = f.replace(/\.[^.]+$/, '');
            return /^\d+$/.test(name);
        });
        skinFiles.sort(function(a, b) {
            var na = parseInt(a.replace(/\.[^.]+$/, ''), 10);
            var nb = parseInt(b.replace(/\.[^.]+$/, ''), 10);
            return (isNaN(na) ? 9999 : na) - (isNaN(nb) ? 9999 : nb);
        });

        var sel = document.getElementById('oracle_gif_select');
        sel.innerHTML = '';
        var cur = (_editingSkinConfig.events && _editingSkinConfig.events.idle) ? _editingSkinConfig.events.idle.animation : '0.webm';
        skinFiles.forEach(function(f, i) {
            var o = document.createElement('option');
            o.value = f; o.textContent = 'Skin ' + (i + 1);
            if (f === cur) o.selected = true;
            sel.appendChild(o);
        });
        sel.onchange = function() { previewOracleGif(this.value); saveOracleGif(this.value); };
        previewOracleGif(cur);
    } catch (e) { console.error('Error loading oracle editor:', e); }
}

function previewOracleGif(filename) {
    var container = document.getElementById('oracle_preview_container');
    var ph = document.getElementById('oracle_preview_placeholder');
    if (!filename || !_editingSkin) { if (ph) ph.style.display='block'; return; }

    // Remove any existing preview element
    var oldImg = container.querySelector('img');
    var oldVid = container.querySelector('video');
    if (oldImg) oldImg.remove();
    if (oldVid) oldVid.remove();
    if (ph) ph.style.display = 'none';

    var url = '/api/skins/' + encodeURIComponent(_editingSkin) + '/preview/' + encodeURIComponent(filename);
    var ext = filename.split('.').pop().toLowerCase();

    if (ext === 'webm') {
        var vid = document.createElement('video');
        vid.src = url;
        vid.autoplay = true; vid.muted = true; vid.loop = true;
        vid.setAttribute('playsinline', '');
        vid.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%;';
        container.appendChild(vid);
        vid.play().catch(function(){});
    } else {
        var img = document.createElement('img');
        img.src = url;
        img.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%;';
        container.appendChild(img);
    }
}

async function saveOracleGif(filename) {
    if (!_editingSkinConfig) return;
    for (var h in _editingSkinConfig.events) _editingSkinConfig.events[h].animation = filename;
    try {
        await fetch('/api/skins/' + encodeURIComponent(_editingSkin) + '/config', {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(_editingSkinConfig)
        });
        await fetch('/api/skins/select', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({skin_name: _editingSkin})
        });
    } catch (e) { console.error(e); }
}

// ---------------------------------------------------------------------------
// Avatar editor
// ---------------------------------------------------------------------------

async function loadAvatarEditor(folderName, displayName) {
    document.getElementById('avatar_editor_name').textContent = displayName;
    try {
        var r = await Promise.all([
            fetch('/api/skins/' + encodeURIComponent(folderName) + '/config'),
            fetch('/api/skins/' + encodeURIComponent(folderName) + '/files')
        ]);
        if (!r[0].ok || !r[1].ok) throw new Error('Failed');
        _editingSkin = folderName;
        _editingSkinConfig = await r[0].json();
        _skinFiles = (await r[1].json()).files || [];
        renderAvatarHooksTable();
        _selectedHook = 'idle'; highlightHookRow('idle');
        var idleAnim = (_editingSkinConfig.events && _editingSkinConfig.events.idle) ? _editingSkinConfig.events.idle.animation : null;
        if (idleAnim) previewAnimation(idleAnim);
    } catch (e) { console.error('Error loading avatar editor:', e); }
}

function renderAvatarHooksTable() {
    var tbody = document.getElementById('avatar_hooks_tbody');
    if (!tbody || !_editingSkinConfig) return;
    tbody.innerHTML = '';
    var events = _editingSkinConfig.events || {};
    EVENT_HOOKS.forEach(function(hook) {
        var resp = events[hook] || {};
        var tr = document.createElement('tr');
        tr.className = 'border-b border-[#565869]/50 cursor-pointer hover:bg-[#565869]/20 transition-colors';
        tr.dataset.hook = hook;
        tr.addEventListener('click', function(e) {
            if (e.target.tagName === 'SELECT') return;
            _selectedHook = hook; highlightHookRow(hook);
            var a = events[hook] ? events[hook].animation : null;
            if (a) previewAnimation(a);
        });

        var td1 = document.createElement('td'); td1.className = 'py-2 px-2 text-[#ececf1] text-xs font-mono'; td1.textContent = hook; tr.appendChild(td1);

        var td2 = document.createElement('td'); td2.className = 'py-2 px-2';
        var as = document.createElement('select'); as.className = 'w-full bg-[#0d1117] border border-[#565869] rounded px-2 py-1 text-white text-xs';
        as.dataset.hook = hook; as.dataset.field = 'animation';
        _skinFiles.forEach(function(f) {
            var o = document.createElement('option'); o.value = f;
            o.textContent = f.replace('.webm', '').replace('.gif', '');
            if (f === resp.animation) o.selected = true; as.appendChild(o);
        });
        as.addEventListener('change', function() { _selectedHook = hook; highlightHookRow(hook); previewAnimation(this.value); });
        td2.appendChild(as); tr.appendChild(td2);

        var td3 = document.createElement('td'); td3.className = 'py-2 px-2';
        var ps = document.createElement('select'); ps.className = 'bg-[#0d1117] border border-[#565869] rounded px-2 py-1 text-white text-xs';
        ps.dataset.hook = hook; ps.dataset.field = 'playback';
        PLAYBACK_MODES.forEach(function(m) { var o = document.createElement('option'); o.value = m; o.textContent = m; if (m === (resp.playback || 'pingpong')) o.selected = true; ps.appendChild(o); });
        td3.appendChild(ps); tr.appendChild(td3);

        tbody.appendChild(tr);
    });
}

function highlightHookRow(hook) {
    document.querySelectorAll('#avatar_hooks_tbody tr').forEach(function(r) {
        r.classList.toggle('bg-[#10a37f]/10', r.dataset.hook === hook);
    });
}

// ---------------------------------------------------------------------------
// Preview
// ---------------------------------------------------------------------------

function previewAnimation(filename) {
    if (!_editingSkin || !filename) { clearPreview(); return; }
    var video = document.getElementById('skin_preview_video');
    var ph = document.getElementById('skin_preview_placeholder');
    var fnEl = document.getElementById('skin_preview_filename');
    var container = document.getElementById('skin_preview_container');
    var oldImg = container ? container.querySelector('img') : null;
    if (oldImg) oldImg.remove();
    var url = '/api/skins/' + encodeURIComponent(_editingSkin) + '/preview/' + encodeURIComponent(filename);
    var ext = filename.split('.').pop().toLowerCase();
    var videoExts = ['webm'];
    var imageExts = ['gif', 'webp', 'png', 'jpg', 'jpeg'];
    if (videoExts.indexOf(ext) >= 0) {
        video.src = url; video.style.display = 'block'; ph.style.display = 'none';
        video.play().catch(function() {});
    } else if (imageExts.indexOf(ext) >= 0) {
        video.style.display = 'none'; ph.style.display = 'none';
        var img = document.createElement('img'); img.src = url; img.className = 'w-full h-full object-contain';
        container.appendChild(img);
    } else { clearPreview(); }
    if (fnEl) fnEl.textContent = filename.replace(/\.[^.]+$/, '');
}

function clearPreview() {
    var v = document.getElementById('skin_preview_video');
    var p = document.getElementById('skin_preview_placeholder');
    var f = document.getElementById('skin_preview_filename');
    if (v) { v.src = ''; v.style.display = 'none'; }
    if (p) p.style.display = 'block';
    if (f) f.textContent = '';
    var c = document.getElementById('skin_preview_container');
    if (c) { var i = c.querySelector('img'); if (i) i.remove(); }
}

// ---------------------------------------------------------------------------
// Save avatar config
// ---------------------------------------------------------------------------

async function saveAvatarConfig() {
    if (!_editingSkin || !_editingSkinConfig) return;
    var events = {};
    EVENT_HOOKS.forEach(function(hook) {
        var aEl = document.querySelector('[data-hook="' + hook + '"][data-field="animation"]');
        var pEl = document.querySelector('[data-hook="' + hook + '"][data-field="playback"]');
        if (!aEl) return;
        var ex = (_editingSkinConfig.events && _editingSkinConfig.events[hook]) || {};
        events[hook] = {
            animation: aEl.value, playback: pEl ? pEl.value : 'pingpong',
            show_player: ex.show_player || false, show_chat_bubble: ex.show_chat_bubble || false,
            glow: ex.glow || false, glow_color: ex.glow_color || [0,0,0],
            glow_speed: ex.glow_speed || 1000, glow_style: ex.glow_style || 'breathing',
            tray_icon: ex.tray_icon || 'default'
        };
    });
    var payload = { type: _editingSkinConfig.type, name: _editingSkinConfig.name,
        rendering: _editingSkinConfig.rendering, events: events,
        transitions: _editingSkinConfig.transitions || {} };
    try {
        var resp = await fetch('/api/skins/' + encodeURIComponent(_editingSkin) + '/config', {
            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        });
        if (!resp.ok) { var err = await resp.json(); throw new Error(err.detail || 'Failed'); }
        await fetch('/api/skins/select', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({skin_name: _editingSkin})
        });
        if (typeof showNotification === 'function') showNotification('Saved', 'success');
    } catch (e) { console.error(e); }
}

// ---------------------------------------------------------------------------
// Size slider
// ---------------------------------------------------------------------------

function updateSkinsOracleSizeLabel(v) {
    var el = document.getElementById('skins_oracle_size_value');
    if (el) el.textContent = (parseInt(v, 10) * 20) + 'px';
}

function setupSkinsSliders() {
    var t = null;
    function apply(s) {
        s = parseInt(s, 10); updateSkinsOracleSizeLabel(s);
        fetch('/api/oracle/size', { method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({sphere_size: s}) }).catch(function(e) { console.error(e); });
    }
    var slider = document.getElementById('skins_oracle_size');
    if (slider) {
        slider.addEventListener('input', function() { updateSkinsOracleSizeLabel(this.value); if (t) clearTimeout(t); t = setTimeout(function() { t = null; apply(slider.value); }, 80); });
        slider.addEventListener('change', function() { if (t) { clearTimeout(t); t = null; } apply(this.value); });
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function _initSkins() {
    if (!document.getElementById('skins_grid')) return;
    loadSkinsSettings(); setupSkinsSliders();
    var saveBtn = document.getElementById('skin_editor_save');
    if (saveBtn) saveBtn.addEventListener('click', saveAvatarConfig);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _initSkins);
else _initSkins();

window.loadSkinsSettings = loadSkinsSettings;
