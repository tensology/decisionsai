// Audio Settings JavaScript

// Last device names from API – used so Save never overwrites with empty when dropdown fails to match
var _lastLoadedInputDevice = 'System Default';
var _lastLoadedOutputDevice = 'System Default';

function _setSelectValueIfPossible(select, value) {
    if (!select || !value || value === 'System Default') return;
    if (select.value === value) return;
    select.value = value;
    if (select.value !== value) {
        for (var i = 0; i < select.options.length; i++) {
            var opt = select.options[i];
            if (opt.value === value || (opt.value && opt.value.indexOf(value) !== -1) || (value && value.indexOf(opt.value) !== -1)) {
                select.value = opt.value;
                return;
            }
        }
    }
}

// Load audio settings from backend
async function loadAudioSettings() {
    try {
        // Load devices first so dropdowns are populated before we set values
        await loadAudioDevices();

        const response = await fetch('/api/audio');
        if (!response.ok) {
            throw new Error('Failed to load audio settings');
        }
        const settings = await response.json();

        _lastLoadedInputDevice = settings.input_device || 'System Default';
        _lastLoadedOutputDevice = settings.output_device || 'System Default';

        const inputSelect = document.getElementById('input_device');
        const outputSelect = document.getElementById('output_device');

        _setSelectValueIfPossible(inputSelect, settings.input_device);
        _setSelectValueIfPossible(outputSelect, settings.output_device);

        // Highlight matching rows in the device tables
        _highlightSavedDevice('audio_output_tbody', settings.output_device);
        _highlightSavedDevice('audio_input_tbody', settings.input_device);

        const rememberEl = document.getElementById('remember_audio_settings');
        if (rememberEl) rememberEl.checked = settings.remember_audio_settings || false;

        console.log('Audio settings loaded successfully');
    } catch (error) {
        console.error('Error loading audio settings:', error);
        showNotification('Failed to load audio settings: ' + error.message, 'error');
    }
}

// Highlight a saved device in a table (clear others first, then match exact or substring)
function _highlightSavedDevice(tbodyId, deviceName) {
    const rows = document.querySelectorAll(`#${tbodyId} tr`);
    rows.forEach(function(row) {
        row.classList.remove('selected', 'bg-[#007bff]');
    });
    if (!deviceName || deviceName === 'System Default') return;
    const nameLower = deviceName.toLowerCase();
    rows.forEach(function(row) {
        const nameCell = row.querySelector('td:first-child');
        if (!nameCell) return;
        const rowName = nameCell.textContent.trim();
        if (rowName === deviceName || rowName.toLowerCase().indexOf(nameLower) !== -1 || nameLower.indexOf(rowName.toLowerCase()) !== -1) {
            row.classList.add('selected', 'bg-[#007bff]');
        }
    });
}

// Save audio settings to backend (triggers hot-swap). Dropdown is the source of truth.
async function saveAudioSettings(options) {
    options = options || {};
    try {
        var inputVal = document.getElementById('input_device').value;
        var outputVal = document.getElementById('output_device').value;
        if (!inputVal || inputVal === '') inputVal = _lastLoadedInputDevice;
        if (!outputVal || outputVal === '') outputVal = _lastLoadedOutputDevice;
        // Normalize "System Default" so backend gets consistent value
        if (inputVal === 'system_default') inputVal = 'System Default';
        if (outputVal === 'system_default') outputVal = 'System Default';

        const settings = {
            input_device: inputVal,
            output_device: outputVal,
            remember_audio_settings: document.getElementById('remember_audio_settings').checked,
            locked_output: outputVal,
            locked_input: inputVal
        };

        const response = await fetch('/api/audio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save audio settings');
        }

        // Keep dropdowns showing what we saved (they already are; sync table highlight to match)
        document.getElementById('input_device').value = settings.input_device;
        document.getElementById('output_device').value = settings.output_device;
        _highlightSavedDevice('audio_output_tbody', settings.output_device);
        _highlightSavedDevice('audio_input_tbody', settings.input_device);

        const result = await response.json();
        if (!options.silent) {
            showNotification('Audio settings saved', 'success');
        }
        console.log('Audio settings saved:', result);
    } catch (error) {
        console.error('Error saving audio settings:', error);
        showNotification('Failed to save audio settings: ' + error.message, 'error');
    }
}

// Populate dropdowns and tables from API data (shared by load and detect)
function populateAudioDevicesFromData(data) {
    const inputSelect = document.getElementById('input_device');
    const outputSelect = document.getElementById('output_device');
    const prevInput = inputSelect ? inputSelect.value : '';
    const prevOutput = outputSelect ? outputSelect.value : '';

    if (inputSelect) {
        inputSelect.innerHTML = '';
        (data.input_devices || []).forEach(device => {
            const option = document.createElement('option');
            option.value = device.name;
            option.textContent = device.name;
            inputSelect.appendChild(option);
        });
    }
    if (outputSelect) {
        outputSelect.innerHTML = '';
        (data.output_devices || []).forEach(device => {
            const option = document.createElement('option');
            option.value = device.name;
            option.textContent = device.name;
            outputSelect.appendChild(option);
        });
    }

    if (inputSelect) {
        var targetInput = prevInput || _lastLoadedInputDevice;
        _setSelectValueIfPossible(inputSelect, targetInput);
        if (inputSelect.value && inputSelect.value !== 'System Default') {
            _lastLoadedInputDevice = inputSelect.value;
        }
    }
    if (outputSelect) {
        var targetOutput = prevOutput || _lastLoadedOutputDevice;
        _setSelectValueIfPossible(outputSelect, targetOutput);
        if (outputSelect.value && outputSelect.value !== 'System Default') {
            _lastLoadedOutputDevice = outputSelect.value;
        }
    }

    const outputTbody = document.getElementById('audio_output_tbody');
    if (outputTbody) {
        outputTbody.innerHTML = '';
        (data.output_devices || []).filter(d => d.name !== 'System Default').forEach(device => {
            const row = document.createElement('tr');
            row.className = 'hover:bg-[#565869] cursor-pointer border-b border-[#565869]';
            row.onclick = function() { selectTableRow(this, 'output'); };
            const nameCell = document.createElement('td');
            nameCell.className = 'px-3 py-2 text-sm text-[#ececf1]';
            nameCell.textContent = device.name;
            row.appendChild(nameCell);
            const typeCell = document.createElement('td');
            typeCell.className = 'px-3 py-2 text-sm text-[#ececf1]';
            typeCell.textContent = device.type || 'Other';
            row.appendChild(typeCell);
            outputTbody.appendChild(row);
        });
    }

    const inputTbody = document.getElementById('audio_input_tbody');
    if (inputTbody) {
        inputTbody.innerHTML = '';
        (data.input_devices || []).filter(d => d.name !== 'System Default').forEach(device => {
            const row = document.createElement('tr');
            row.className = 'hover:bg-[#565869] cursor-pointer border-b border-[#565869]';
            row.onclick = function() { selectTableRow(this, 'input'); };
            const nameCell = document.createElement('td');
            nameCell.className = 'px-3 py-2 text-sm text-[#ececf1]';
            nameCell.textContent = device.name;
            row.appendChild(nameCell);
            const typeCell = document.createElement('td');
            typeCell.className = 'px-3 py-2 text-sm text-[#ececf1]';
            typeCell.textContent = device.type || 'Other';
            row.appendChild(typeCell);
            inputTbody.appendChild(row);
        });
    }
}

// Load available audio devices
async function loadAudioDevices() {
    try {
        const response = await fetch('/api/audio/devices');
        if (!response.ok) {
            throw new Error('Failed to load audio devices');
        }
        const data = await response.json();
        populateAudioDevicesFromData(data);
        console.log('Audio devices loaded successfully');
    } catch (error) {
        console.error('Error loading audio devices:', error);
        // Restore System Default so the Audio tab doesn't look empty
        const inputSelect = document.getElementById('input_device');
        const outputSelect = document.getElementById('output_device');
        if (inputSelect && !inputSelect.options.length) {
            const opt = document.createElement('option');
            opt.value = 'System Default';
            opt.textContent = 'System Default';
            inputSelect.appendChild(opt);
        }
        if (outputSelect && !outputSelect.options.length) {
            const opt = document.createElement('option');
            opt.value = 'System Default';
            opt.textContent = 'System Default';
            outputSelect.appendChild(opt);
        }
    }
}

// Select a table row and sync the dropdown
function selectTableRow(row, type) {
    const tableId = type === 'output' ? 'audio_output_tbody' : 'audio_input_tbody';
    const rows = document.querySelectorAll(`#${tableId} tr`);
    rows.forEach(r => r.classList.remove('selected', 'bg-[#007bff]'));

    row.classList.add('selected', 'bg-[#007bff]');

    // Sync dropdown to match table selection
    const deviceName = row.querySelector('td:first-child').textContent;
    const selectId = type === 'output' ? 'output_device' : 'input_device';
    document.getElementById(selectId).value = deviceName;
}

// Detect audio devices (runs server-side detection, merges new devices, returns updated list)
async function detectAudioDevices() {
    try {
        showNotification('Detecting audio devices...', 'info');
        const response = await fetch('/api/audio/detect', { method: 'POST' });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.detail || 'Detection failed');
        }
        populateAudioDevicesFromData(data);
        showNotification('Audio devices detected and refreshed', 'success');
    } catch (error) {
        console.error('Error detecting audio devices:', error);
        showNotification('Failed to detect audio devices: ' + error.message, 'error');
    }
}

// Reset audio devices
async function resetAudioDevices() {
    const confirmed = await window.DecisionsAPI.confirm({
        title: "Reset audio devices",
        message: "Reset all audio device settings?",
        confirmLabel: "Reset",
        danger: true,
    });
    if (!confirmed) {
        return;
    }

    try {
        // Clear selections
        document.getElementById('input_device').value = 'System Default';
        document.getElementById('output_device').value = 'System Default';
        document.getElementById('remember_audio_settings').checked = false;

        // Clear table selections
        const allRows = document.querySelectorAll('#audio_output_tbody tr, #audio_input_tbody tr');
        allRows.forEach(row => row.classList.remove('selected', 'bg-[#007bff]'));

        // Reload devices
        await loadAudioDevices();

        showNotification('Audio settings reset', 'success');
    } catch (error) {
        console.error('Error resetting audio settings:', error);
        showNotification('Failed to reset audio settings: ' + error.message, 'error');
    }
}

// Audio Input Monitor: VAD-style level meter using Web Audio API
function setupAudioInputMonitor() {
    const bar = document.getElementById('audio_level_bar');
    const statusEl = document.getElementById('audio_monitor_status');
    const container = document.getElementById('audio_monitor_container');
    const valueEl = document.getElementById('audio_monitor_value');
    const thresholdMarker = document.getElementById('audio_monitor_threshold_marker');
    const thresholdFill = document.getElementById('audio_monitor_threshold_fill');
    const vadSlider = document.getElementById('vad_level');
    if (!bar || !statusEl || !container || !valueEl) return;
    var audioContext = null;
    var stream = null;
    var analyser = null;
    var rafId = null;
    var timeData = null;
    var vadThreshold = 50;
    var currentPercent = 0;
    var currentRms = 0;
    var statusOverride = '';
    function updateLiveStatus() {
        if (statusOverride) {
            statusEl.textContent = statusOverride;
            return;
        }
        if (!stream) {
            statusEl.textContent = 'Mic off — click bar to enable';
            return;
        }
        statusEl.textContent = currentPercent >= vadThreshold ? 'Listening — over VAD guide' : 'Listening — below VAD guide';
    }
    function setStatus(text) {
        statusOverride = text || '';
        updateLiveStatus();
    }
    function setThreshold(threshold) {
        vadThreshold = Math.max(0, Math.min(100, parseInt(threshold, 10) || 0));
        if (thresholdMarker) thresholdMarker.style.left = vadThreshold + '%';
        if (thresholdFill) thresholdFill.style.width = vadThreshold + '%';
        updateLiveStatus();
    }
    window.updateAudioMonitorVadThreshold = setThreshold;
    function stopMonitor() {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        if (stream) { stream.getTracks().forEach(function(t) { t.stop(); }); stream = null; }
        if (audioContext) { audioContext.close().catch(function() {}); audioContext = null; }
        analyser = null;
        currentPercent = 0;
        currentRms = 0;
        bar.style.width = '0%';
        valueEl.textContent = '0%';
        bar.style.backgroundColor = '#10a37f';
        updateLiveStatus();
    }
    function tick() {
        if (!analyser || !timeData) return;
        analyser.getFloatTimeDomainData(timeData);
        var sumSq = 0;
        for (var i = 0; i < timeData.length; i++) { var s = timeData[i]; sumSq += s * s; }
        currentRms = Math.sqrt(sumSq / timeData.length);
        currentPercent = Math.min(100, Math.round(currentRms * 500));
        if (currentPercent < 0) currentPercent = 0;
        bar.style.width = currentPercent + '%';
        valueEl.textContent = currentPercent + '%';
        bar.style.backgroundColor = currentPercent >= vadThreshold ? '#f59e0b' : '#10a37f';
        updateLiveStatus();
        rafId = requestAnimationFrame(tick);
    }
    function startMonitor() {
        if (stream) return Promise.resolve();
        setStatus('Requesting microphone…');
        return navigator.mediaDevices.getUserMedia({ audio: true })
            .then(function(s) {
                stream = s;
                audioContext = new (window.AudioContext || window.webkitAudioContext)();
                var source = audioContext.createMediaStreamSource(stream);
                analyser = audioContext.createAnalyser();
                analyser.fftSize = 2048;
                analyser.smoothingTimeConstant = 0.5;
                source.connect(analyser);
                var bufferLength = analyser.fftSize;
                timeData = new Float32Array(bufferLength);
                statusOverride = '';
                updateLiveStatus();
                rafId = requestAnimationFrame(tick);
            })
            .catch(function(err) {
                setStatus('Mic blocked or unavailable');
                console.error('Audio Input Monitor:', err);
                throw err;
            });
    }
    setThreshold(vadSlider ? vadSlider.value : 50);
    container.addEventListener('click', function() {
        if (stream) stopMonitor(); else startMonitor();
    });
    window.DecisionsAudioMonitor = {
        start: startMonitor,
        stop: stopMonitor,
        isActive: function() { return !!stream; },
        getSnapshot: function() {
            return {
                active: !!stream,
                percent: currentPercent,
                rms: currentRms,
                threshold: vadThreshold,
            };
        },
        setStatusOverride: function(text) { setStatus(text); },
        clearStatusOverride: function() {
            statusOverride = '';
            updateLiveStatus();
        },
        setThreshold: setThreshold,
    };
}

// Switch between Output/Input tabs in Audio settings
function switchAudioTab(tabName) {
    const outputTab = document.getElementById('audio_output_tab');
    const inputTab = document.getElementById('audio_input_tab');
    const outputTable = document.getElementById('audio_output_table_container');
    const inputTable = document.getElementById('audio_input_table_container');
    if (!outputTab || !inputTab || !outputTable || !inputTable) return;
    if (tabName === 'output') {
        outputTab.classList.remove('border-transparent');
        outputTab.classList.add('border-[#10a37f]');
        inputTab.classList.remove('border-[#10a37f]');
        inputTab.classList.add('border-transparent');
        outputTable.classList.remove('hidden');
        inputTable.classList.add('hidden');
    } else if (tabName === 'input') {
        inputTab.classList.remove('border-transparent');
        inputTab.classList.add('border-[#10a37f]');
        outputTab.classList.remove('border-[#10a37f]');
        outputTab.classList.add('border-transparent');
        inputTable.classList.remove('hidden');
        outputTable.classList.add('hidden');
    }
}

function initAudioSectionTabs() {
    const tabButtons = Array.from(document.querySelectorAll('[data-audio-subtab]'));
    const panels = Array.from(document.querySelectorAll('[data-audio-panel]'));
    if (!tabButtons.length || !panels.length) return;

    const activateTab = function(tabKey) {
        tabButtons.forEach(function(button) {
            const isActive = button.dataset.audioSubtab === tabKey;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        panels.forEach(function(panel) {
            panel.classList.toggle('is-active', panel.dataset.audioPanel === tabKey);
        });
    };

    tabButtons.forEach(function(button) {
        button.addEventListener('click', function() {
            activateTab(button.dataset.audioSubtab);
        });
    });

    const activeButton = tabButtons.find(function(button) {
        return button.classList.contains('is-active');
    });
    activateTab(activeButton ? activeButton.dataset.audioSubtab : tabButtons[0].dataset.audioSubtab);
}

// Poll for audio device changes (when desktop detects new devices) and refresh dropdowns
var _audioDevicesVersionPollInterval = null;
var _lastAudioDevicesVersion = 0;

function _startAudioDevicesVersionPolling() {
    if (_audioDevicesVersionPollInterval) return;
    _audioDevicesVersionPollInterval = setInterval(async function() {
        var tab = 'general';
        try { if (typeof getTabFromHash === 'function') tab = getTabFromHash(); } catch (e) {}
        if (tab !== 'general' && tab !== 'audio') return;
        try {
            var r = await fetch('/api/audio/devices-version');
            if (!r.ok) return;
            var data = await r.json();
            var v = data.version || 0;
            if (v !== _lastAudioDevicesVersion && _lastAudioDevicesVersion > 0) {
                await loadAudioDevices();
                _highlightSavedDevice('audio_output_tbody', _lastLoadedOutputDevice);
                _highlightSavedDevice('audio_input_tbody', _lastLoadedInputDevice);
                if (typeof showNotification === 'function') showNotification('Audio devices updated', 'info');
            }
            _lastAudioDevicesVersion = v;
        } catch (e) { /* ignore */ }
    }, 5000);
}

function _stopAudioDevicesVersionPolling() {
    if (_audioDevicesVersionPollInterval) {
        clearInterval(_audioDevicesVersionPollInterval);
        _audioDevicesVersionPollInterval = null;
    }
}

// Initialize audio settings when DOM is loaded
function _initAudio() {
    if (!document.getElementById('input_device') && !document.getElementById('tab-audio')) return;
    initAudioSectionTabs();
    if (document.getElementById('tab-audio')) {
        loadAudioSettings().then(function() {
            _lastAudioDevicesVersion = 0;
            fetch('/api/audio/devices-version').then(function(r) { return r.ok ? r.json() : {}; }).then(function(d) {
                _lastAudioDevicesVersion = d.version || 0;
                _startAudioDevicesVersionPolling();
            }).catch(function() { _startAudioDevicesVersionPolling(); });
        });
    } else {
        _lastAudioDevicesVersion = 0;
        fetch('/api/audio/devices-version').then(function(r) { return r.ok ? r.json() : {}; }).then(function(d) {
            _lastAudioDevicesVersion = d.version || 0;
            _startAudioDevicesVersionPolling();
        }).catch(function() { _startAudioDevicesVersionPolling(); });
    }
    setupAudioInputMonitor();
    const saveButton = document.getElementById('audio_save_button');
    if (saveButton) saveButton.addEventListener('click', saveAudioSettings);
    const detectButton = document.getElementById('audio_detect_button');
    if (detectButton) detectButton.addEventListener('click', detectAudioDevices);
    const resetButton = document.getElementById('audio_reset_button');
    if (resetButton) resetButton.addEventListener('click', resetAudioDevices);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initAudio);
} else {
    _initAudio();
}

// Export functions for use in other files / HTML onclick
window.loadAudioSettings = loadAudioSettings;
window.saveAudioSettings = saveAudioSettings;
window.switchAudioTab = switchAudioTab;
