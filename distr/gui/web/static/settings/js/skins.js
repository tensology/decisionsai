// Skins Settings JavaScript
var _skinsList = [];
var _selectedSkin = null;
var _loadedSkin = null;
var _loadedSkinScale = 9;
var _selectedSkinScale = 9;
var _editingSkin = null;
var _editingSkinConfig = null;
var _skinFiles = [];
var _selectedHook = null;
var _skinsBusyDepth = 0;

var EVENT_HOOKS = [
    "idle", "hands_free_listening", "ptt_active", "dictation",
    "recording_action", "file_drop_success", "tts_response",
    "running_action", "running_step_runner", "snippet_copied",
    "thinking", "needs_attention"
];
var PLAYBACK_MODES = ["loop", "pingpong"];
var _skinCardBaseClass = 'relative flex flex-col items-center gap-3 p-4 rounded-xl border-2 cursor-pointer transition-all';
var _skinCardSelectedClass = 'border-[#34d399] bg-[#10a37f]/16 shadow-lg shadow-[#10a37f]/25';
var _skinCardLoadedClass = 'border-[#10a37f] bg-[#10a37f]/10';
var _skinCardIdleClass = 'border-[#565869] bg-[#0d1117] hover:border-[#7a7c8c]';
var _skinsSectionTitle = 'Skins';
var _skinsSectionSubtitle = 'Select and customize your oracle-avatar skin';

function _setLoadButtonState(busy) {
    var loadBtn = document.getElementById('skin_detail_load');
    var loadLabel = document.getElementById('skin_detail_load_label');
    var loadIcon = document.getElementById('skin_detail_load_icon');
    if (!loadBtn) return;
    if (loadLabel) {
        loadLabel.textContent = busy ? 'Loading...' : 'Load';
    } else {
        loadBtn.textContent = busy ? 'Loading...' : 'Load';
    }
    if (loadIcon) {
        loadIcon.classList.toggle('hidden', !!busy);
    }
}

function _replaceSkinsUrl(nextSkinFolder) {
    if (!window.history || !window.history.replaceState) return;
    var params = new URLSearchParams(window.location.search);
    if (nextSkinFolder) params.set('skin', nextSkinFolder);
    else params.delete('skin');
    var nextSearch = params.toString();
    var nextUrl = window.location.pathname + (nextSearch ? '?' + nextSearch : '') + window.location.hash;
    window.history.replaceState({}, '', nextUrl);
}

function _getSkinFromUrl() {
    var params = new URLSearchParams(window.location.search);
    return params.get('skin') || '';
}

function _syncSkinsSectionHeader(isEditorOpen, skinType) {
    var titleEl = document.getElementById('skins_section_title');
    var subtitleEl = document.getElementById('skins_section_subtitle');
    var backBtn = document.getElementById('skins_section_back');
    if (titleEl) titleEl.textContent = _skinsSectionTitle;
    if (subtitleEl) {
        subtitleEl.textContent = skinType === 'oracle'
            ? 'Select and customize your oracle skin'
            : _skinsSectionSubtitle;
    }
    if (backBtn) backBtn.classList.toggle('hidden', !isEditorOpen);
}

function _setSkinsBusy(busy, message, showOverlay, showButtons) {
    showOverlay = (showOverlay !== false);
    showButtons = (showButtons !== false);
    if (busy) _skinsBusyDepth += 1;
    else _skinsBusyDepth = Math.max(0, _skinsBusyDepth - 1);
    var effectiveBusy = _skinsBusyDepth > 0;
    var overlay = document.getElementById('skins_loading_overlay');
    var textEl = document.getElementById('skins_loading_text');
    var loadBtn = document.getElementById('skin_detail_load');
    var saveBtn = document.getElementById('skin_detail_save');
    if (overlay) {
        if (showOverlay) {
            overlay.classList.toggle('hidden', !effectiveBusy);
        } else {
            overlay.classList.add('hidden');
        }
    }
    if (textEl && showOverlay) textEl.textContent = effectiveBusy ? (message || 'Loading...') : '';
    if (showButtons && loadBtn) {
        loadBtn.disabled = !!effectiveBusy;
        loadBtn.classList.toggle('opacity-50', !!effectiveBusy);
        loadBtn.classList.toggle('cursor-not-allowed', !!effectiveBusy);
        _setLoadButtonState(!!effectiveBusy);
    }
    if (showButtons && saveBtn) {
        saveBtn.disabled = !!effectiveBusy;
        saveBtn.classList.toggle('opacity-50', !!effectiveBusy);
        saveBtn.classList.toggle('cursor-not-allowed', !!effectiveBusy);
        saveBtn.textContent = effectiveBusy ? 'Saving...' : 'Save';
    }
}

function _updateSkinCardSelectionState() {
    var grid = document.getElementById('skins_grid');
    if (!grid) return;

    Array.prototype.forEach.call(grid.querySelectorAll('[data-skin-card]'), function(card) {
        var folderName = card.dataset.folder;
        var isSelected = folderName === _selectedSkin;
        var isLoaded = folderName === _loadedSkin;
        var badge = card.querySelector('[data-skin-badge]');
        var previewWrap = card.querySelector('[data-skin-preview-wrap]');

        if (isSelected) {
            card.className = _skinCardBaseClass + ' ' + _skinCardSelectedClass;
        } else if (isLoaded) {
            card.className = _skinCardBaseClass + ' ' + _skinCardLoadedClass;
        } else {
            card.className = _skinCardBaseClass + ' ' + _skinCardIdleClass;
        }

        if (previewWrap) {
            if (isSelected) {
                previewWrap.classList.add('bg-[#10a37f]/18');
                previewWrap.classList.remove('bg-[#0d1117]', 'bg-[#10a37f]/12');
            } else if (isLoaded) {
                previewWrap.classList.add('bg-[#10a37f]/12');
                previewWrap.classList.remove('bg-[#0d1117]', 'bg-[#10a37f]/18');
            } else {
                previewWrap.classList.add('bg-[#0d1117]');
                previewWrap.classList.remove('bg-[#10a37f]/12', 'bg-[#10a37f]/18');
            }
        }

        if (badge) {
            badge.textContent = '';
            badge.className = 'hidden';
        }
    });
}

function _getOraclePlaybackMode() {
    if (_editingSkinConfig && _editingSkinConfig.events && _editingSkinConfig.events.idle) {
        return _editingSkinConfig.events.idle.playback || 'loop';
    }
    return 'loop';
}

function _applyPingPongToVideo(vid, isPingPong) {
    // Clean up any previous ping-pong state
    if (vid._ppCleanup) vid._ppCleanup();
    if (vid._ppEndHandler) vid.removeEventListener('ended', vid._ppEndHandler);
    if (vid._ppRafId) cancelAnimationFrame(vid._ppRafId);
    vid._ppEndHandler = null;
    vid._ppRafId = null;
    vid._ppDirection = null;

    // For WebM (VP8/VP9) videos, true reverse playback is not possible
    // because these codecs only decode forward (keyframe compression).
    // playbackRate=-1 and currentTime stepping both produce visible stutter.
    // The only smooth option is native forward loop.
    // We set loop=true for both modes — ping-pong skins will loop
    // forward seamlessly. The playback mode is stored in skin.json so
    // the Qt/desktop renderer can do proper ping-pong, but in the web
    // preview we always use smooth forward looping.
    vid.loop = true;
    vid.playbackRate = 1;
}

async function loadSkinsSettings() {
    try {
        var responses = await Promise.all([
            fetch('/api/skins'),
            fetch('/api/general'),
        ]);
        if (!responses[0].ok) throw new Error('Failed to load skins');
        var data = await responses[0].json();
        var general = responses[1].ok ? await responses[1].json() : {};
        _skinsList = data.skins || [];
        _selectedSkin = data.selected_skin || 'oracle';
        _loadedSkin = data.selected_skin || 'oracle';
        var rawSize = data.sphere_size !== undefined ? data.sphere_size : 180;
        var scale = rawSize > 10 ? Math.max(4, Math.min(10, Math.round(rawSize / 20))) : Math.max(4, Math.min(10, parseInt(rawSize, 10) || 9));
        _loadedSkinScale = scale;
        _selectedSkinScale = scale;
        var slider = document.getElementById('skins_oracle_size');
        if (slider) slider.value = scale;
        updateSkinsOracleSizeLabel(scale);

        // Oracle settings now live in this tab.
        var restoreEl = document.getElementById('restore_position');
        if (restoreEl) restoreEl.checked = general.restore_position !== undefined ? general.restore_position : true;
        var positionEl = document.getElementById('oracle_position');
        if (positionEl) positionEl.value = general.oracle_position || 'custom';

        renderSkinsGrid();
        var skinFromUrl = _getSkinFromUrl();
        var shouldOpenEditor = skinFromUrl && (window.location.hash || '').toLowerCase() === '#skins';
        if (shouldOpenEditor && _skinsList.some(function(skin) { return skin.folder_name === skinFromUrl; })) {
            await openSkinEditor(skinFromUrl);
        } else {
            closeSkinEditor();
            if (skinFromUrl && !_skinsList.some(function(skin) { return skin.folder_name === skinFromUrl; })) {
                _replaceSkinsUrl('');
            }
        }
    } catch (e) {
        console.error('Error loading skins:', e);
    }
}

async function _saveSkinEditorState(showToast) {
    _setSkinsBusy(true, 'Loading selected skin...');
    try {
        if (!(_selectedSkin || _loadedSkin)) {
            throw new Error('No skin selected');
        }

        if (_editingSkin && _editingSkinConfig && !document.getElementById('avatar_detail').classList.contains('hidden')) {
            await _persistAvatarConfig(false);
        }

        var restoreEl = document.getElementById('restore_position');
        var positionEl = document.getElementById('oracle_position');
        var restorePosition = restoreEl ? restoreEl.checked : true;
        var oraclePosition = positionEl ? positionEl.value : 'custom';

        // Preserve all existing general settings fields, only override oracle ones.
        var currentGeneralResp = await fetch('/api/general');
        if (!currentGeneralResp.ok) throw new Error('Failed to load current general settings');
        var payload = await currentGeneralResp.json();
        payload.restore_position = restorePosition;
        payload.oracle_position = oraclePosition;

        var response = await fetch('/api/general', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            var err = await response.json().catch(function() { return {}; });
            throw new Error(err.detail || 'Failed to save skins settings');
        }

        if (_editingSkin && _editingSkinConfig && document.getElementById('oracle_detail') && !document.getElementById('oracle_detail').classList.contains('hidden')) {
            // Oracle editor writes are already persisted as fields change; nothing extra needed here.
        }

        var slider = document.getElementById('skins_oracle_size');
        var stagedScale = slider ? parseInt(slider.value, 10) || _selectedSkinScale : _selectedSkinScale;
        var sizeSaveResp = await fetch('/api/skins/' + encodeURIComponent(_selectedSkin || _loadedSkin || 'oracle') + '/size', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sphere_size: stagedScale })
        });
        if (!sizeSaveResp.ok) {
            throw new Error('Failed to save skin size');
        }

        _selectedSkinScale = stagedScale;
        if ((_selectedSkin || _loadedSkin) === _loadedSkin) {
            var liveSizeResp = await fetch('/api/oracle/size', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sphere_size: stagedScale })
            });
            if (!liveSizeResp.ok) {
                throw new Error('Failed to update loaded size');
            }
            _loadedSkinScale = stagedScale;
        }

        if (showToast && typeof showNotification === 'function') showNotification('Skin settings saved', 'success');
        return {
            selectedSkin: _selectedSkin || _loadedSkin || 'oracle',
            stagedScale: stagedScale
        };
    } catch (e) {
        console.error('Error saving skins settings:', e);
        if (typeof showNotification === 'function') showNotification('Failed to save skin settings', 'error');
        return null;
    } finally {
        _setSkinsBusy(false);
    }
}

async function saveSkinsSettings() {
    var saved = await _saveSkinEditorState(false);
    if (!saved) return;

    await _loadSkinByName(saved.selectedSkin, saved.stagedScale, true);
}

async function _loadSkinByName(folderName, skinScale, showToast) {
    _setSkinsBusy(true, 'Loading selected skin...');
    try {
        var resolvedScale = skinScale;
        if (resolvedScale === undefined || resolvedScale === null) {
            var sizeRespForSkin = await fetch('/api/skins/' + encodeURIComponent(folderName) + '/size');
            if (!sizeRespForSkin.ok) throw new Error('Failed to load skin size');
            var sizeData = await sizeRespForSkin.json();
            var rawSize = sizeData.sphere_size !== undefined ? sizeData.sphere_size : _loadedSkinScale;
            resolvedScale = rawSize > 10 ? Math.max(4, Math.min(10, Math.round(rawSize / 20))) : Math.max(4, Math.min(10, parseInt(rawSize, 10) || _loadedSkinScale || 9));
        }

        var selectResp = await fetch('/api/skins/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ skin_name: folderName })
        });

        if (!selectResp.ok) {
            var selectErr = await selectResp.json().catch(function() { return {}; });
            throw new Error(selectErr.detail || 'Failed to load selected skin');
        }

        var sizeResp = await fetch('/api/oracle/size', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sphere_size: resolvedScale })
        });
        if (!sizeResp.ok) {
            throw new Error('Failed to load selected size');
        }

        _selectedSkin = folderName;
        _loadedSkin = folderName;
        _selectedSkinScale = resolvedScale;
        _loadedSkinScale = resolvedScale;
        _updateSkinCardSelectionState();

        if (showToast && typeof showNotification === 'function') showNotification('Skin loaded', 'success');
    } catch (e) {
        console.error('Error loading selected skin:', e);
        if (typeof showNotification === 'function') showNotification('Failed to load skin', 'error');
    } finally {
        _setSkinsBusy(false);
    }
}

function renderSkinsGrid() {
    var grid = document.getElementById('skins_grid');
    if (!grid) return;
    grid.innerHTML = '';
    _skinsList.forEach(function(skin) {
        var sel = skin.folder_name === _selectedSkin;
        var isLoaded = skin.folder_name === _loadedSkin;
        var card = document.createElement('div');
        card.className = _skinCardBaseClass + ' ' + (sel ? _skinCardSelectedClass : (isLoaded ? _skinCardLoadedClass : _skinCardIdleClass));
        card.dataset.folder = skin.folder_name;
        card.dataset.skinCard = 'true';

        // Preview image/video — use the idle animation from the API
        var idleFile = skin.idle_animation || 'idle.webm';
        var previewUrl = '/api/skins/' + encodeURIComponent(skin.folder_name) + '/preview/' + encodeURIComponent(idleFile);
        var previewEl;
        var ext = idleFile.split('.').pop().toLowerCase();
        var isVideo = (ext === 'webm');

        if (skin.type === 'oracle') {
            // Oracle: round preview with cover
            if (isVideo) {
                previewEl = document.createElement('video');
                previewEl.src = previewUrl;
                previewEl.autoplay = true; previewEl.muted = true;
                previewEl.setAttribute('playsinline', '');
                previewEl.loop = true; // Always loop in grid cards
                _applyPingPongToVideo(previewEl, skin.idle_playback === 'pingpong');
            } else {
                previewEl = document.createElement('img');
                previewEl.src = previewUrl;
                previewEl.onerror = function() { this.style.display = 'none'; };
            }
            previewEl.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%;';
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
            previewWrap.className = 'w-full aspect-square overflow-hidden rounded-full flex items-center justify-center border border-[#565869] ' +
                (sel ? 'bg-[#10a37f]/18' : (isLoaded ? 'bg-[#10a37f]/12' : 'bg-[#0d1117]'));
        } else {
            previewWrap.className = 'w-full aspect-square overflow-hidden rounded-lg flex items-center justify-center ' +
                (sel ? 'bg-[#10a37f]/18' : (isLoaded ? 'bg-[#10a37f]/12' : 'bg-[#0d1117]'));
        }
        previewWrap.dataset.skinPreviewWrap = 'true';
        previewWrap.appendChild(previewEl);
        card.appendChild(previewWrap);

        var titleRow = document.createElement('div');
        titleRow.className = 'flex w-full items-center justify-between gap-2';

        var nameEl = document.createElement('span');
        nameEl.className = 'min-w-0 flex-1 text-sm font-medium text-white';
        nameEl.textContent = skin.name;
        titleRow.appendChild(nameEl);

        var editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'absolute top-3 right-3 flex h-8 w-8 items-center justify-center rounded-full border border-[#565869] bg-[#1a1f3a]/95 text-[#ececf1] transition-colors hover:border-[#10a37f] hover:text-white';
        editButton.setAttribute('aria-label', 'Edit ' + skin.name);
        editButton.innerHTML = '' +
            '<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="m14.7 5.3 4 4"></path>' +
            '<path d="M4 20l3.8-.8L19 8a2.8 2.8 0 1 0-4-4L3.8 15.2 3 19.9Z"></path>' +
            '<path d="M13.5 6.5 17.5 10.5"></path>' +
            '</svg>';
        editButton.addEventListener('click', function(e) {
            e.stopPropagation();
            openSkinEditor(skin.folder_name);
        });
        card.appendChild(editButton);

        var loadButton = document.createElement('button');
        loadButton.type = 'button';
        loadButton.className = 'flex h-8 w-8 items-center justify-center rounded-full bg-[#f97316] text-white shadow-md shadow-[#f97316]/20 transition-colors hover:bg-[#ea580c]';
        loadButton.setAttribute('aria-label', 'Load ' + skin.name);
        loadButton.innerHTML = '' +
            '<svg viewBox="0 0 16 16" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M3 8h8"></path>' +
            '<path d="m8 4 4 4-4 4"></path>' +
            '</svg>';
        loadButton.addEventListener('click', function(e) {
            e.stopPropagation();
            _loadSkinByName(skin.folder_name, null, true);
        });
        titleRow.appendChild(loadButton);
        card.appendChild(titleRow);

        // Selected indicator
        var badge = document.createElement('span');
        badge.dataset.skinBadge = 'true';
        badge.className = 'hidden';
        card.appendChild(badge);

        card.addEventListener('click', function() { selectSkin(skin.folder_name); });
        card.addEventListener('dblclick', function(e) {
            e.preventDefault();
            openSkinEditor(skin.folder_name);
        });
        grid.appendChild(card);
    });

    // Append the Create Skin card as the last grid item
    var createCard = document.createElement('div');
    createCard.id = 'create_skin_card';
    createCard.className = 'flex flex-col items-center justify-center gap-2 p-4 rounded-xl border-2 border-dashed border-[#565869] cursor-pointer transition-all hover:border-[#10a37f] hover:bg-[#10a37f]/5 min-h-0';

    var plusIcon = document.createElement('div');
    plusIcon.className = 'w-full aspect-square rounded-lg bg-[#0d1117] flex items-center justify-center text-3xl text-[#565869]';
    plusIcon.textContent = '+';
    createCard.appendChild(plusIcon);

    var createName = document.createElement('span');
    createName.className = 'text-sm font-medium text-[#565869] text-center';
    createName.textContent = 'Create Skin';
    createCard.appendChild(createName);

    createCard.addEventListener('click', function() {
        if (_maskoConfigured) {
            openCreateSkinModal();
        } else {
            _navigateToMaskoKey();
        }
    });

    grid.appendChild(createCard);
}

function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function selectSkin(folderName) {
    if (_selectedSkin === folderName) {
        closeSkinEditor();
        return;
    }
    _selectedSkin = folderName;
    _updateSkinCardSelectionState();
    closeSkinEditor();
}

async function openSkinEditor(folderName) {
    _setSkinsBusy(true, 'Loading skin editor...');
    var panel = document.getElementById('skin_detail_panel');
    var oracleDetail = document.getElementById('oracle_detail');
    var avatarDetail = document.getElementById('avatar_detail');
    if (folderName !== _selectedSkin) {
        await selectSkin(folderName);
    }
    var grid = document.getElementById('skins_grid');
    if (grid) grid.classList.add('hidden');
    if (panel) panel.classList.remove('hidden');
    if (oracleDetail) oracleDetail.classList.add('hidden');
    if (avatarDetail) avatarDetail.classList.add('hidden');
    try {
        await loadSkinSize(folderName);
        await showEditorForSkin(folderName);
    } finally {
        _setSkinsBusy(false);
    }
}

async function loadSkinSize(folderName) {
    try {
        var resp = await fetch('/api/skins/' + encodeURIComponent(folderName) + '/size');
        if (!resp.ok) throw new Error('Failed');
        var data = await resp.json();
        var scale = parseInt(data.sphere_size, 10) || _loadedSkinScale;
        _selectedSkinScale = scale;
        var slider = document.getElementById('skins_oracle_size');
        if (slider) slider.value = scale;
        updateSkinsOracleSizeLabel(scale);
    } catch (e) {
        console.error('Error loading skin size:', e);
    }
}

function closeSkinEditor() {
    var grid = document.getElementById('skins_grid');
    var panel = document.getElementById('skin_detail_panel');
    var oracleDetail = document.getElementById('oracle_detail');
    var avatarDetail = document.getElementById('avatar_detail');

    if (grid) grid.classList.remove('hidden');
    if (panel) panel.classList.add('hidden');
    if (oracleDetail) oracleDetail.classList.add('hidden');
    if (avatarDetail) avatarDetail.classList.add('hidden');
    _replaceSkinsUrl('');
    _syncSkinsSectionHeader(false);
}

async function showEditorForSkin(folderName) {
    var skin = _skinsList.find(function(s) { return s.folder_name === folderName; });
    if (!skin) return;

    var panel = document.getElementById('skin_detail_panel');
    var grid = document.getElementById('skins_grid');
    var oracleDetail = document.getElementById('oracle_detail');
    var avatarDetail = document.getElementById('avatar_detail');

    if (grid) grid.classList.add('hidden');
    panel.classList.remove('hidden');
    _replaceSkinsUrl(folderName);
    _syncSkinsSectionHeader(true, skin.type);

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
            o.value = f; o.textContent = 'Background ' + (i + 1);
            if (f === cur) o.selected = true;
            sel.appendChild(o);
        });
        sel.onchange = function() { previewOracleGif(this.value); saveOracleGif(this.value).then(function() { renderSkinsGrid(); }); };

        // Ping-pong checkbox
        var ppCb = document.getElementById('oracle_pingpong_checkbox');
        var curPlayback = (_editingSkinConfig.events && _editingSkinConfig.events.idle) ? (_editingSkinConfig.events.idle.playback || 'loop') : 'loop';
        if (ppCb) {
            ppCb.checked = (curPlayback === 'pingpong');
            ppCb.onchange = function() { saveOraclePlayback(this.checked ? 'pingpong' : 'loop'); previewOracleGif(sel.value); renderSkinsGrid(); };
        }

        // Unfocused opacity controls
        var uoCb = document.getElementById('oracle_unfocused_opacity_checkbox');
        var uoSlider = document.getElementById('oracle_unfocused_opacity_slider');
        var uoWrap = document.getElementById('oracle_unfocused_opacity_slider_wrap');
        var uoLabel = document.getElementById('oracle_unfocused_opacity_value');
        var rendering = _editingSkinConfig.rendering || {};
        var uoEnabled = rendering.unfocused_opacity_enabled || false;
        var uoVal = rendering.unfocused_opacity !== undefined ? rendering.unfocused_opacity : 0.5;
        if (uoCb) {
            uoCb.checked = uoEnabled;
            if (uoWrap) uoWrap.classList.toggle('hidden', !uoEnabled);
            uoCb.onchange = function() {
                if (uoWrap) uoWrap.classList.toggle('hidden', !this.checked);
                saveOracleUnfocusedOpacity(this.checked, parseFloat(uoSlider.value) / 100);
            };
        }
        if (uoSlider) {
            uoSlider.value = Math.round(uoVal * 100);
            if (uoLabel) uoLabel.textContent = Math.round(uoVal * 100) + '%';
            uoSlider.oninput = function() { if (uoLabel) uoLabel.textContent = this.value + '%'; };
            uoSlider.onchange = function() { saveOracleUnfocusedOpacity(uoCb.checked, parseFloat(this.value) / 100); };
        }

        previewOracleGif(cur);
    } catch (e) { console.error('Error loading oracle editor:', e); }
}

function previewOracleGif(filename) {
    var container = document.getElementById('oracle_preview_container');
    var ph = document.getElementById('oracle_preview_placeholder');
    if (!container) return;
    container.style.setProperty('background-color', '#000000', 'important');
    container.classList.remove('bg-black');

    // Remove any existing preview element and enforce black background.
    var oldImg = container.querySelector('img');
    var oldVid = container.querySelector('video');
    if (oldImg) oldImg.remove();
    if (oldVid) {
        if (oldVid._ppCleanup) oldVid._ppCleanup();
        if (oldVid._ppEndHandler) oldVid.removeEventListener('ended', oldVid._ppEndHandler);
        if (oldVid._ppRafId) cancelAnimationFrame(oldVid._ppRafId);
        oldVid.remove();
    }

    if (!filename || !_editingSkin) {
        if (ph) {
            ph.style.display = 'block';
            ph.style.color = '#9ca3af';
        }
        return;
    }

    if (ph) ph.style.display = 'none';

    var url = '/api/skins/' + encodeURIComponent(_editingSkin) + '/preview/' + encodeURIComponent(filename);
    var ext = filename.split('.').pop().toLowerCase();
    var isPingPong = (_getOraclePlaybackMode() === 'pingpong');

    if (ext === 'webm') {
        var vid = document.createElement('video');
        vid.src = url;
        vid.autoplay = true; vid.muted = true;
        vid.setAttribute('playsinline', '');
        vid.loop = true; // Always loop in preview
        vid.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%; background:#000000; background-color:#000000;';
        _applyPingPongToVideo(vid, isPingPong);
        container.appendChild(vid);
        vid.play().catch(function(){});
    } else {
        var img = document.createElement('img');
        img.src = url;
        img.style.cssText = 'width:155%; height:155%; object-fit:cover; border-radius:50%; background:#000000; background-color:#000000;';
        container.appendChild(img);
    }
}

async function saveOracleGif(filename) {
    if (!_editingSkinConfig) return;
    for (var h in _editingSkinConfig.events) _editingSkinConfig.events[h].animation = filename;
    // Update local skins list so grid re-renders with the new animation
    _skinsList.forEach(function(s) { if (s.folder_name === _editingSkin) s.idle_animation = filename; });
    try {
        await fetch('/api/skins/' + encodeURIComponent(_editingSkin) + '/config', {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(_editingSkinConfig)
        });
    } catch (e) { console.error(e); }
}

async function saveOraclePlayback(mode) {
    if (!_editingSkinConfig) return;
    for (var h in _editingSkinConfig.events) _editingSkinConfig.events[h].playback = mode;
    // Update local skins list so grid re-renders correctly
    _skinsList.forEach(function(s) { if (s.folder_name === _editingSkin) s.idle_playback = mode; });
    try {
        await fetch('/api/skins/' + encodeURIComponent(_editingSkin) + '/config', {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(_editingSkinConfig)
        });
    } catch (e) { console.error(e); }
}

async function saveOracleUnfocusedOpacity(enabled, value) {
    if (!_editingSkinConfig || !_editingSkinConfig.rendering) return;
    _editingSkinConfig.rendering.unfocused_opacity_enabled = enabled;
    _editingSkinConfig.rendering.unfocused_opacity = value;
    try {
        await fetch('/api/skins/' + encodeURIComponent(_editingSkin) + '/config', {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(_editingSkinConfig)
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
            o.textContent = f.replace(/\.[^.]+$/, '');
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

async function _persistAvatarConfig(showToast) {
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
        if (showToast && typeof showNotification === 'function') showNotification('Saved', 'success');
    } catch (e) { console.error(e); }
}

async function saveAvatarConfig() {
    await _persistAvatarConfig(true);
}

// ---------------------------------------------------------------------------
// Size slider
// ---------------------------------------------------------------------------

function updateSkinsOracleSizeLabel(v) {
    var el = document.getElementById('skins_oracle_size_value');
    if (el) el.textContent = (parseInt(v, 10) * 20) + 'px';
}

function setupSkinsSliders() {
    var slider = document.getElementById('skins_oracle_size');
    if (slider) {
        slider.addEventListener('input', function() {
            var scale = parseInt(this.value, 10) || _selectedSkinScale;
            _selectedSkinScale = scale;
            updateSkinsOracleSizeLabel(scale);
            if ((_selectedSkin || _loadedSkin) === _loadedSkin) {
                fetch('/api/oracle/size', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ sphere_size: scale })
                }).then(function(resp) {
                    if (resp.ok) _loadedSkinScale = scale;
                }).catch(function(e) { console.error(e); });
            }
        });
        slider.addEventListener('change', function() {
            var scale = parseInt(this.value, 10) || _selectedSkinScale;
            _selectedSkinScale = scale;
            updateSkinsOracleSizeLabel(scale);
        });
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function _initSkins() {
    var grid = document.getElementById('skins_grid');
    if (!grid || grid.dataset.initialized === '1') return;
    grid.dataset.initialized = '1';
    loadSkinsSettings(); setupSkinsSliders();
    var saveBtn = document.getElementById('skin_detail_save');
    if (saveBtn) saveBtn.addEventListener('click', function() { _saveSkinEditorState(true); });
    var loadBtn = document.getElementById('skin_detail_load');
    if (loadBtn) loadBtn.addEventListener('click', saveSkinsSettings);
    var backBtn = document.getElementById('skin_detail_back');
    if (backBtn) backBtn.addEventListener('click', closeSkinEditor);
    var sectionBackBtn = document.getElementById('skins_section_back');
    if (sectionBackBtn) sectionBackBtn.addEventListener('click', closeSkinEditor);
    _initCreateSkinCard();
    _setupCreateSkinModalClose();
    _syncSkinsSectionHeader(false);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _initSkins);
else _initSkins();

window.loadSkinsSettings = loadSkinsSettings;
window.saveSkinsSettings = saveSkinsSettings;

// ===========================================================================
// Create Skin Modal — Masko AI skin generation
// ===========================================================================

var _csCurrentStep = 1;
var _csSelectedStyle = '';
var _csMode = 'static';
var _csStyles = [];
var _csGenerationId = null;
var _csPollInterval = null;
var _csCreditsBalance = null;

var CS_STEP_NAMES = ['', 'name', 'description', 'style', 'mode', 'confirm', 'progress'];
var EVENT_HOOKS_DISPLAY = {
    idle: 'Idle', hands_free_listening: 'Listening', ptt_active: 'PTT Active',
    dictation: 'Dictation', recording_action: 'Recording', file_drop_success: 'File Drop',
    tts_response: 'TTS Response', running_action: 'Running Action', running_step_runner: 'Workflow',
    snippet_copied: 'Snippet Copied', thinking: 'Thinking', needs_attention: 'Needs Attention'
};

var _maskoConfigured = false;

function _checkMaskoConfigured() {
    return fetch('/api/masko/credits')
        .then(function(r) { return r.ok; })
        .catch(function() { return false; });
}

function _navigateToMaskoKey() {
    // Switch to the Third Party tab and highlight the masko key field
    if (typeof switchTab === 'function') {
        switchTab('thirdparty');
    } else {
        var thirdpartyTab = document.querySelector('[data-tab="thirdparty"]');
        if (thirdpartyTab) thirdpartyTab.click();
    }
    // After a short delay for the tab to render, highlight the masko key field
    setTimeout(function() {
        var maskoCheckbox = document.getElementById('masko_enabled');
        var maskoKeyEl = document.getElementById('masko_key');
        if (maskoCheckbox && !maskoCheckbox.checked) {
            maskoCheckbox.checked = true;
            // Trigger the enable toggle
            if (typeof toggleProviderInput === 'function') toggleProviderInput('masko', true);
        }
        if (maskoKeyEl) {
            maskoKeyEl.scrollIntoView({behavior: 'smooth', block: 'center'});
            // Add a glow effect
            maskoKeyEl.classList.add('masko-glow');
            // Remove glow after a few seconds
            setTimeout(function() { maskoKeyEl.classList.remove('masko-glow'); }, 4000);
            maskoKeyEl.focus();
        }
    }, 300);
}

function _initCreateSkinCard() {
    // The card is now rendered inside renderSkinsGrid as the last grid item
    // We just need to check masko config for click behavior
    _checkMaskoConfigured().then(function(ok) {
        _maskoConfigured = ok;
    });
}

function openCreateSkinModal() {
    _csCurrentStep = 1;
    _csSelectedStyle = '';
    _csMode = 'static';
    _csGenerationId = null;
    if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }

    // Clear inputs
    var nameEl = document.getElementById('cs_skin_name'); if (nameEl) nameEl.value = '';
    var descEl = document.getElementById('cs_description'); if (descEl) descEl.value = '';
    _hideCsErrors();

    var modal = document.getElementById('create_skin_modal');
    if (modal) { modal.style.display = 'flex'; modal.classList.remove('hidden'); }
    _updateCsStep();
}

function closeCreateSkinModal() {
    var modal = document.getElementById('create_skin_modal');
    if (modal) { modal.style.display = 'none'; modal.classList.add('hidden'); }
    if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }
}

function _hideCsErrors() {
    ['cs_name_error','cs_desc_error','cs_style_error'].forEach(function(id) {
        var el = document.getElementById(id); if (el) { el.classList.add('hidden'); el.textContent = ''; }
    });
}

function _updateCsStep() {
    for (var i = 1; i <= 6; i++) {
        var el = document.getElementById('cs_step_' + i);
        if (el) el.classList.toggle('hidden', i !== _csCurrentStep);
    }
    var backBtn = document.getElementById('cs_back_btn');
    var nextBtn = document.getElementById('cs_next_btn');

    if (backBtn) backBtn.classList.toggle('hidden', _csCurrentStep <= 1 || _csCurrentStep === 6);
    if (nextBtn) {
        if (_csCurrentStep === 5) {
            nextBtn.textContent = 'Generate';
            nextBtn.classList.remove('hidden');
        } else if (_csCurrentStep === 6) {
            nextBtn.classList.add('hidden');
        } else {
            nextBtn.textContent = 'Next';
            nextBtn.classList.remove('hidden');
        }
        // Disable next if insufficient credits on step 4
        if (_csCurrentStep === 4 && _csCreditsBalance !== null) {
            var est = _csMode === 'animated' ? 372 : 12;
            if (_csCreditsBalance < est) {
                nextBtn.disabled = true;
                nextBtn.classList.add('opacity-50','cursor-not-allowed');
            } else {
                nextBtn.disabled = false;
                nextBtn.classList.remove('opacity-50','cursor-not-allowed');
            }
        } else {
            nextBtn.disabled = false;
            nextBtn.classList.remove('opacity-50','cursor-not-allowed');
        }
    }

    // Fetch styles when entering step 3
    if (_csCurrentStep === 3) _fetchStyles();
    // Fetch credits when entering step 4
    if (_csCurrentStep === 4) _fetchCredits();
}

function csStepBack() {
    if (_csCurrentStep > 1 && _csCurrentStep < 6) {
        _csCurrentStep--;
        _updateCsStep();
    }
}

function csStepNext() {
    if (_csCurrentStep === 6) return;

    // Validate current step
    if (_csCurrentStep === 1) {
        var name = (document.getElementById('cs_skin_name').value || '').trim();
        if (!name) {
            var err = document.getElementById('cs_name_error');
            err.textContent = 'Please enter a skin name.'; err.classList.remove('hidden');
            return;
        }
    } else if (_csCurrentStep === 2) {
        var desc = (document.getElementById('cs_description').value || '').trim();
        if (!desc) {
            var err = document.getElementById('cs_desc_error');
            err.textContent = 'Please describe your character.'; err.classList.remove('hidden');
            return;
        }
    } else if (_csCurrentStep === 3) {
        if (!_csSelectedStyle) {
            var err = document.getElementById('cs_style_error');
            err.textContent = 'Please select a style.'; err.classList.remove('hidden');
            return;
        }
    } else if (_csCurrentStep === 5) {
        _startGeneration();
        return;
    }

    _csCurrentStep++;
    _updateCsStep();
}

function selectMode(mode) {
    _csMode = mode;
    var staticBtn = document.getElementById('cs_mode_static');
    var animBtn = document.getElementById('cs_mode_animated');
    if (mode === 'static') {
        staticBtn.className = 'flex-1 px-4 py-3 rounded-lg border-2 text-sm font-medium transition-all border-[#10a37f] bg-[#10a37f]/10 text-[#10a37f]';
        animBtn.className = 'flex-1 px-4 py-3 rounded-lg border-2 text-sm font-medium transition-all border-[#565869] bg-[#0d1117] text-gray-400';
    } else {
        animBtn.className = 'flex-1 px-4 py-3 rounded-lg border-2 text-sm font-medium transition-all border-[#10a37f] bg-[#10a37f]/10 text-[#10a37f]';
        staticBtn.className = 'flex-1 px-4 py-3 rounded-lg border-2 text-sm font-medium transition-all border-[#565869] bg-[#0d1117] text-gray-400';
    }
    _updateCostDisplay();
}

function _updateCostDisplay() {
    var poseCredits = _csMode === 'animated' ? 252 : 12;
    var transCredits = _csMode === 'animated' ? 120 : 0;
    var total = poseCredits + transCredits;

    var poseEl = document.getElementById('cs_pose_credits'); if (poseEl) poseEl.textContent = poseCredits + ' credits';
    var transEl = document.getElementById('cs_transition_credits'); if (transEl) transEl.textContent = '~' + transCredits + ' credits';
    var totalEl = document.getElementById('cs_total_credits'); if (totalEl) totalEl.textContent = total + ' credits';
    var transRow = document.getElementById('cs_transition_row'); if (transRow) transRow.classList.toggle('hidden', _csMode !== 'animated');
    var noteEl = document.getElementById('cs_cost_note'); if (noteEl) noteEl.classList.toggle('hidden', _csMode !== 'animated');

    // Insufficient credits warning
    var warnEl = document.getElementById('cs_insufficient_warning');
    if (_csCreditsBalance !== null && warnEl) {
        warnEl.classList.toggle('hidden', _csCreditsBalance >= total);
    }
}

async function _fetchCredits() {
    var balEl = document.getElementById('cs_credit_balance');
    if (balEl) balEl.textContent = 'Loading...';
    try {
        var resp = await fetch('/api/masko/credits');
        if (resp.ok) {
            var data = await resp.json();
            _csCreditsBalance = data.credits;
            if (balEl) balEl.textContent = data.credits + ' credits';
        } else {
            _csCreditsBalance = null;
            if (balEl) balEl.textContent = 'Could not fetch balance';
        }
    } catch (e) {
        _csCreditsBalance = null;
        if (balEl) balEl.textContent = 'Could not fetch balance';
    }
    _updateCostDisplay();
}

async function _fetchStyles() {
    var grid = document.getElementById('cs_styles_grid'); if (!grid) return;
    var loading = document.getElementById('cs_style_loading');
    if (loading) loading.classList.remove('hidden');
    grid.innerHTML = '<div class="text-xs text-gray-400 col-span-3">Loading styles...</div>';
    try {
        var resp = await fetch('/api/masko/styles');
        if (resp.ok) {
            var data = await resp.json();
            _csStyles = data.styles || [];
            grid.innerHTML = '';
            _csStyles.forEach(function(s) {
                var card = document.createElement('div');
                card.className = 'border-2 rounded-lg p-3 cursor-pointer transition-all text-center hover:border-[#10a37f] ' +
                    (_csSelectedStyle === s.id ? 'border-[#10a37f] bg-[#10a37f]/10' : 'border-[#565869] bg-[#0d1117]');
                card.dataset.styleId = s.id;
                if (s.preview_url) {
                    card.innerHTML = '<img src="' + escapeHtml(s.preview_url) + '" class="w-full aspect-square object-contain rounded mb-1" alt="' + escapeHtml(s.name) + '" onerror="this.style.display=\'none\'" /><div class="text-xs text-[#ececf1]">' + escapeHtml(s.name) + '</div>';
                } else {
                    card.innerHTML = '<div class="text-sm text-[#ececf1] py-4">' + escapeHtml(s.name) + '</div>';
                }
                card.addEventListener('click', function() {
                    _csSelectedStyle = s.id;
                    grid.querySelectorAll('div[data-style-id]').forEach(function(el) {
                        el.className = 'border-2 rounded-lg p-3 cursor-pointer transition-all text-center hover:border-[#10a37f] ' +
                            (el.dataset.styleId === _csSelectedStyle ? 'border-[#10a37f] bg-[#10a37f]/10' : 'border-[#565869] bg-[#0d1117]');
                    });
                });
                grid.appendChild(card);
            });
        } else {
            grid.innerHTML = '<div class="text-xs text-red-400 col-span-3">Failed to load styles. <button onclick="_fetchStyles()" class="text-[#10a37f] underline">Retry</button></div>';
        }
    } catch (e) {
        grid.innerHTML = '<div class="text-xs text-red-400 col-span-3">Network error. <button onclick="_fetchStyles()" class="text-[#10a37f] underline">Retry</button></div>';
    }
    if (loading) loading.classList.add('hidden');
}

async function _startGeneration() {
    var name = (document.getElementById('cs_skin_name').value || '').trim();
    var description = (document.getElementById('cs_description').value || '').trim();
    var style = _csSelectedStyle;
    var mode = _csMode;

    // Populate confirm step
    var cn = document.getElementById('cs_confirm_name'); if (cn) cn.textContent = name;
    var cd = document.getElementById('cs_confirm_desc'); if (cd) cd.textContent = description.substring(0, 50) + (description.length > 50 ? '...' : '');
    var cs = document.getElementById('cs_confirm_style'); if (cs) cs.textContent = style;
    var cm = document.getElementById('cs_confirm_mode'); if (cm) cm.textContent = mode === 'animated' ? 'Animated' : 'Static';
    var cc = document.getElementById('cs_confirm_credits'); if (cc) cc.textContent = (mode === 'animated' ? '~372' : '12') + ' credits';

    _csCurrentStep = 6; // Jump to progress
    _updateCsStep();

    try {
        var resp = await fetch('/api/skins/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({skin_name: name, description: description, style: style, mode: mode})
        });
        if (!resp.ok) {
            var err = await resp.json();
            _showGenerationError(err.detail || 'Generation failed to start');
            return;
        }
        var data = await resp.json();
        _csGenerationId = data.generation_id;
        // Start polling
        _csPollInterval = setInterval(pollGenerationStatus, 2000);
        pollGenerationStatus();
    } catch (e) {
        _showGenerationError('Network error: ' + e.message);
    }
}

async function pollGenerationStatus() {
    if (!_csGenerationId) return;
    try {
        var resp = await fetch('/api/skins/generate/status?id=' + encodeURIComponent(_csGenerationId));
        if (!resp.ok) return;
        var data = await resp.json();

        // Update progress UI
        var completed = data.completed_jobs || 0;
        var total = data.total_jobs || 12;
        var pct = total > 0 ? Math.round((completed / total) * 100) : 0;
        var bar = document.getElementById('cs_progress_bar'); if (bar) bar.style.width = pct + '%';
        var countEl = document.getElementById('cs_progress_count'); if (countEl) countEl.textContent = completed + ' / ' + total;
        var hookEl = document.getElementById('cs_progress_hook'); if (hookEl) hookEl.textContent = data.current_hook ? (EVENT_HOOKS_DISPLAY[data.current_hook] || data.current_hook) : (completed === total ? 'Finalizing...' : 'Processing...');

        if (data.status === 'complete') {
            if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }
            var hookEl2 = document.getElementById('cs_progress_hook'); if (hookEl2) hookEl2.textContent = 'Complete!';
            setTimeout(function() {
                closeCreateSkinModal();
                loadSkinsSettings(); // Refresh the skin grid
            }, 1500);
        } else if (data.status === 'failed') {
            if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }
            _handleFailedGeneration(data);
        } else if (data.status === 'cancelled') {
            if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }
            closeCreateSkinModal();
        }
    } catch (e) {
        // Continue polling
    }
}

function _handleFailedGeneration(data) {
    var hookStatuses = data.hook_statuses || {};
    var failedHooks = [];
    var completedHooks = [];
    for (var h in hookStatuses) {
        if (hookStatuses[h] === 'failed') failedHooks.push(h);
        else if (hookStatuses[h] === 'completed') completedHooks.push(h);
    }

    var hookEl = document.getElementById('cs_progress_hook'); if (hookEl) hookEl.textContent = 'Generation failed';

    document.getElementById('cs_cancel_btn').classList.add('hidden');

    if (completedHooks.length === 0) {
        // All hooks failed
        document.getElementById('cs_all_failed').classList.remove('hidden');
        document.getElementById('cs_failed_hooks').classList.add('hidden');
    } else {
        // Partial failure — show retry UI
        var list = document.getElementById('cs_failed_list');
        list.innerHTML = '';
        failedHooks.forEach(function(h) {
            var li = document.createElement('li');
            li.textContent = EVENT_HOOKS_DISPLAY[h] || h;
            list.appendChild(li);
        });
        document.getElementById('cs_failed_hooks').classList.remove('hidden');
        document.getElementById('cs_all_failed').classList.add('hidden');
    }
}

function _showGenerationError(msg) {
    var hookEl = document.getElementById('cs_progress_hook'); if (hookEl) hookEl.textContent = 'Error: ' + msg;
    var countEl = document.getElementById('cs_progress_count'); if (countEl) countEl.textContent = '';
    document.getElementById('cs_cancel_btn').classList.remove('hidden');
}

async function retryFailedHooks() {
    if (!_csGenerationId) return;
    document.getElementById('cs_failed_hooks').classList.add('hidden');
    document.getElementById('cs_all_failed').classList.add('hidden');
    document.getElementById('cs_cancel_btn').classList.remove('hidden');
    var hookEl = document.getElementById('cs_progress_hook'); if (hookEl) hookEl.textContent = 'Retrying...';

    try {
        await fetch('/api/skins/generate/retry', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({generation_id: _csGenerationId})
        });
        _csPollInterval = setInterval(pollGenerationStatus, 2000);
        pollGenerationStatus();
    } catch (e) {
        _showGenerationError('Retry failed: ' + e.message);
    }
}

async function retryAllHooks() {
    // Same as retryFailedHooks — sends empty hooks list to retry all
    await retryFailedHooks();
}

async function cancelGeneration() {
    if (!_csGenerationId) { closeCreateSkinModal(); return; }
    try {
        await fetch('/api/skins/generate/cancel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: _csGenerationId})
        });
    } catch (e) {}
    if (_csPollInterval) { clearInterval(_csPollInterval); _csPollInterval = null; }
    closeCreateSkinModal();
}

// Click outside modal to close
function _setupCreateSkinModalClose() {
    var modal = document.getElementById('create_skin_modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) closeCreateSkinModal();
        });
    }
}
