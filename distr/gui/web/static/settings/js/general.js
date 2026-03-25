// General Settings JavaScript

// Cached TTS providers from backend (populated on first load)
let _ttsProviders = [];

// Fetch and cache TTS providers (called once on init)
async function loadTTSProviders() {
    try {
        const resp = await fetch('/api/tts/providers');
        if (resp.ok) _ttsProviders = await resp.json();
    } catch (e) {
        console.error('Failed to load TTS providers:', e);
    }
}

// Populate the provider dropdown from cached data
function populateProviderDropdown(selectedId) {
    const select = document.getElementById('tts_provider');
    if (!select) return;
    select.innerHTML = '';
    _ttsProviders.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === selectedId) opt.selected = true;
        select.appendChild(opt);
    });
}

// Load general settings from backend
async function loadGeneralSettings() {
    try {
        // Load providers first so dropdown can be populated
        await loadTTSProviders();

        const response = await fetch('/api/general');
        if (!response.ok) {
            throw new Error('Failed to load general settings');
        }
        const settings = await response.json();

        // Load startup checkboxes
        document.getElementById('load_splash_sound').checked = settings.load_splash_sound || false;
        document.getElementById('show_about').checked = settings.show_about || false;
        document.getElementById('welcome_greet_me').checked = settings.welcome_greet_me || false;
        document.getElementById('always_confirm_file_operations').checked = settings.always_confirm_file_operations || false;

        // Load listening state radio buttons
        const listeningState = settings.listening_state || 'remember';
        if (listeningState === 'remember') {
            document.getElementById('remember_listening').checked = true;
        } else if (listeningState === 'stop') {
            document.getElementById('always_stop').checked = true;
        } else if (listeningState === 'start') {
            document.getElementById('always_start').checked = true;
        }

        // Load TTS settings — use provider registry, no hardcoded maps
        const voiceProvider = settings.voice_provider || 'kokoro';
        populateProviderDropdown(voiceProvider);
        updateVoiceOptions(voiceProvider);

        // Set the saved voice for the active provider
        const providerInfo = _ttsProviders.find(p => p.id === voiceProvider);
        const settingsKey = providerInfo ? providerInfo.settings_key : null;
        const defaultVoice = providerInfo ? providerInfo.default_voice : '';
        const savedVoice = settingsKey ? (settings[settingsKey] || defaultVoice) : defaultVoice;
        document.getElementById('tts_voice').value = savedVoice;

        // Load sliders
        const playbackSpeed = settings.playback_speed !== undefined ? settings.playback_speed : 1.0;
        document.getElementById('playback_speed').value = playbackSpeed;
        updatePlaybackSpeedLabel(playbackSpeed);

        const speechVolume = settings.speech_volume !== undefined ? settings.speech_volume : 100;
        document.getElementById('speech_volume').value = speechVolume;
        document.getElementById('speech_volume_value').textContent = speechVolume + '%';

        const vadThreshold = settings.vad_threshold !== undefined ? settings.vad_threshold : 50;
        document.getElementById('vad_level').value = vadThreshold;
        document.getElementById('vad_level_value').textContent = vadThreshold + '%';

        // ElevenLabs voice options (only when provider is elevenlabs)
        const stability = settings.elevenlabs_stability !== undefined ? settings.elevenlabs_stability : 0.5;
        const similarity = settings.elevenlabs_similarity_boost !== undefined ? settings.elevenlabs_similarity_boost : 0.6;
        const styleVal = settings.elevenlabs_style !== undefined ? settings.elevenlabs_style : 0.25;
        const speakerBoost = settings.elevenlabs_use_speaker_boost !== undefined ? settings.elevenlabs_use_speaker_boost : true;
        document.getElementById('elevenlabs_stability').value = Math.round(stability * 100);
        document.getElementById('elevenlabs_stability_value').textContent = Math.round(stability * 100) + '%';
        document.getElementById('elevenlabs_similarity').value = Math.round(similarity * 100);
        document.getElementById('elevenlabs_similarity_value').textContent = Math.round(similarity * 100) + '%';
        document.getElementById('elevenlabs_style').value = Math.round(styleVal * 100);
        document.getElementById('elevenlabs_style_value').textContent = Math.round(styleVal * 100) + '%';
        document.getElementById('elevenlabs_speaker_boost').checked = speakerBoost;
        const elevenlabsOpts = document.getElementById('elevenlabs_voice_options');
        if (elevenlabsOpts) elevenlabsOpts.classList.toggle('hidden', voiceProvider !== 'elevenlabs');

        // Load oracle settings
        document.getElementById('restore_position').checked = settings.restore_position !== undefined ? settings.restore_position : true;

        // Show/hide custom voice button based on provider
        _updateCustomVoiceButton(voiceProvider);
        _updateDeleteButton();

        document.getElementById('oracle_position').value = settings.oracle_position || 'custom';

        console.log('General settings loaded successfully:', settings);
    } catch (error) {
        console.error('Error loading general settings:', error);
        showNotification('Failed to load general settings: ' + error.message, 'error');
    }
}

// Save general settings to backend
async function saveGeneralSettings() {
    try {
        // Get listening state
        let listeningState = 'remember';
        if (document.getElementById('always_stop').checked) {
            listeningState = 'stop';
        } else if (document.getElementById('always_start').checked) {
            listeningState = 'start';
        }

        const voiceProvider = document.getElementById('tts_provider').value;
        const selectedVoice = document.getElementById('tts_voice').value;

        const settings = {
            load_splash_sound: document.getElementById('load_splash_sound').checked,
            show_about: document.getElementById('show_about').checked,
            welcome_greet_me: document.getElementById('welcome_greet_me').checked,
            always_confirm_file_operations: document.getElementById('always_confirm_file_operations').checked,
            listening_state: listeningState,
            voice_provider: voiceProvider,
            playback_speed: parseFloat(document.getElementById('playback_speed').value),
            speech_volume: parseInt(document.getElementById('speech_volume').value),
            vad_threshold: parseInt(document.getElementById('vad_level').value),
            elevenlabs_stability: parseInt(document.getElementById('elevenlabs_stability').value, 10) / 100,
            elevenlabs_similarity_boost: parseInt(document.getElementById('elevenlabs_similarity').value, 10) / 100,
            elevenlabs_style: parseInt(document.getElementById('elevenlabs_style').value, 10) / 100,
            elevenlabs_use_speaker_boost: document.getElementById('elevenlabs_speaker_boost').checked,
            restore_position: document.getElementById('restore_position').checked,
            oracle_position: document.getElementById('oracle_position').value
        };

        // Set voice for each provider from the registry (active provider gets
        // the selected voice, others keep their default)
        _ttsProviders.forEach(p => {
            settings[p.settings_key] = (p.id === voiceProvider) ? selectedVoice : p.default_voice;
        });

        const response = await fetch('/api/general', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save general settings');
        }

        const result = await response.json();
        showNotification('General settings saved and oracle updated', 'success');
        console.log('General settings saved:', result);
    } catch (error) {
        console.error('Error saving general settings:', error);
        showNotification('Failed to save general settings: ' + error.message, 'error');
    }
}

// Update voice options from cached provider data (no extra fetch needed)
async function updateVoiceOptions(provider) {
    const voiceSelect = document.getElementById('tts_voice');
    voiceSelect.innerHTML = '';

    const providerInfo = _ttsProviders.find(p => p.id === provider);
    if (!providerInfo) return;

    const voices = providerInfo.voices || [];
    voices.forEach(voice => {
        const option = document.createElement('option');
        option.value = voice.id;
        option.textContent = voice.name;
        if (voice.custom) option.dataset.custom = '1';
        voiceSelect.appendChild(option);
    });

    _updateDeleteButton();
}

// Update playback speed label
function updatePlaybackSpeedLabel(value) {
    const speed = parseFloat(value).toFixed(1);
    document.getElementById('playback_speed_value').textContent = speed + 'x';
}

// Voice playback — audio plays in the browser via HTML5 Audio
let _voiceAudio = null;
let _voiceLoading = false;

function _setVoiceUI(state) {
    // state: 'idle' | 'loading' | 'playing'
    const playIcon = document.getElementById('play_icon');
    const stopIcon = document.getElementById('stop_icon');
    const spinner = document.getElementById('play_spinner');
    const btn = document.getElementById('play_voice_button');

    if (playIcon) playIcon.classList.toggle('hidden', state !== 'idle');
    if (stopIcon) stopIcon.classList.toggle('hidden', state !== 'playing');
    if (spinner) spinner.classList.toggle('hidden', state !== 'loading');
    if (btn) btn.disabled = (state === 'loading');
}

// Toggle play/stop
function toggleVoicePlayback() {
    if (_voiceAudio && !_voiceAudio.paused) {
        stopVoice();
    } else if (!_voiceLoading) {
        playVoice();
    }
}

// Play voice sample — fetch WAV from server, play in browser
async function playVoice() {
    if (_voiceLoading) return;

    const provider = document.getElementById('tts_provider').value;
    const voiceSelect = document.getElementById('tts_voice');
    const voice = voiceSelect.value;
    const voiceName = voiceSelect.options[voiceSelect.selectedIndex]
        ? voiceSelect.options[voiceSelect.selectedIndex].text : voice;
    const speed = parseFloat(document.getElementById('playback_speed').value);

    // Stop any previous playback
    stopVoice();

    _voiceLoading = true;
    _setVoiceUI('loading');

    try {
        const response = await fetch('/api/play-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, voice, speed, voice_name: voiceName })
        });

        if (!response.ok) {
            let msg = 'Failed to generate voice sample';
            try { const err = await response.json(); msg = err.error || msg; } catch(e) {}
            throw new Error(msg);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);

        _voiceAudio = new Audio(url);
        _voiceAudio.onended = function() {
            _setVoiceUI('idle');
            URL.revokeObjectURL(url);
            _voiceAudio = null;
        };
        _voiceAudio.onerror = function() {
            _setVoiceUI('idle');
            URL.revokeObjectURL(url);
            _voiceAudio = null;
            showNotification('Audio playback failed', 'error');
        };

        _voiceLoading = false;
        _setVoiceUI('playing');
        _voiceAudio.play();
    } catch (error) {
        console.error('Error playing voice sample:', error);
        showNotification('Failed to play voice: ' + error.message, 'error');
        _voiceLoading = false;
        _setVoiceUI('idle');
    }
}

// Stop voice sample
function stopVoice() {
    _voiceLoading = false;
    if (_voiceAudio) {
        _voiceAudio.pause();
        _voiceAudio.currentTime = 0;
        _voiceAudio = null;
    }
    _setVoiceUI('idle');
}

// Setup General-tab range sliders and live API pushes (playback speed, speech volume, VAD, ElevenLabs, oracle)
function setupGeneralSliders() {
    var throttleMs = 80;
    const playbackSpeedSlider = document.getElementById('playback_speed');
    const playbackSpeedValue = document.getElementById('playback_speed_value');
    if (playbackSpeedSlider && playbackSpeedValue) {
        var playbackSpeedThrottle = null;
        function applyPlaybackSpeed(speed) {
            speed = parseFloat(speed) || 1.0;
            playbackSpeedValue.textContent = speed.toFixed(1) + 'x';
            fetch('/api/voice/playback-speed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ playback_speed: speed }) })
                .then(function(r) { if (r.ok) return; return r.json().then(function(err) { throw new Error(err.detail || 'Failed'); }); })
                .catch(function(e) { console.error(e); if (typeof showNotification === 'function') showNotification('Playback speed update failed', 'error'); });
        }
        playbackSpeedSlider.addEventListener('input', function() {
            const speed = parseFloat(this.value, 10);
            playbackSpeedValue.textContent = speed.toFixed(1) + 'x';
            if (playbackSpeedThrottle) clearTimeout(playbackSpeedThrottle);
            playbackSpeedThrottle = setTimeout(function() { playbackSpeedThrottle = null; applyPlaybackSpeed(speed); }, throttleMs);
        });
        playbackSpeedSlider.addEventListener('change', function() {
            if (playbackSpeedThrottle) { clearTimeout(playbackSpeedThrottle); playbackSpeedThrottle = null; }
            applyPlaybackSpeed(this.value);
        });
    }
    const speechVolumeSlider = document.getElementById('speech_volume');
    const speechVolumeValue = document.getElementById('speech_volume_value');
    if (speechVolumeSlider && speechVolumeValue) {
        var speechVolumeThrottle = null;
        function applySpeechVolume(vol) {
            vol = parseInt(vol, 10);
            speechVolumeValue.textContent = vol + '%';
            fetch('/api/voice/speech-volume', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speech_volume: vol }) })
                .then(function(r) { if (r.ok) return; return r.json().then(function(err) { throw new Error(err.detail || 'Failed'); }); })
                .catch(function(e) { console.error(e); if (typeof showNotification === 'function') showNotification('Speech volume update failed', 'error'); });
        }
        speechVolumeSlider.addEventListener('input', function() {
            speechVolumeValue.textContent = this.value + '%';
            if (speechVolumeThrottle) clearTimeout(speechVolumeThrottle);
            speechVolumeThrottle = setTimeout(function() { speechVolumeThrottle = null; applySpeechVolume(speechVolumeSlider.value); }, throttleMs);
        });
        speechVolumeSlider.addEventListener('change', function() {
            if (speechVolumeThrottle) { clearTimeout(speechVolumeThrottle); speechVolumeThrottle = null; }
            applySpeechVolume(this.value);
        });
    }
    const vadLevelSlider = document.getElementById('vad_level');
    const vadLevelValue = document.getElementById('vad_level_value');
    if (vadLevelSlider && vadLevelValue) {
        var vadLevelThrottle = null;
        function applyVadLevel(val) {
            val = parseInt(val, 10);
            vadLevelValue.textContent = val + '%';
            fetch('/api/voice/vad-threshold', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vad_threshold: val }) })
                .then(function(r) { if (r.ok) return; return r.json().then(function(err) { throw new Error(err.detail || 'Failed'); }); })
                .catch(function(e) { console.error(e); if (typeof showNotification === 'function') showNotification('VAD update failed', 'error'); });
        }
        vadLevelSlider.addEventListener('input', function() {
            vadLevelValue.textContent = this.value + '%';
            if (vadLevelThrottle) clearTimeout(vadLevelThrottle);
            vadLevelThrottle = setTimeout(function() { vadLevelThrottle = null; applyVadLevel(vadLevelSlider.value); }, throttleMs);
        });
        vadLevelSlider.addEventListener('change', function() {
            if (vadLevelThrottle) { clearTimeout(vadLevelThrottle); vadLevelThrottle = null; }
            applyVadLevel(this.value);
        });
    }
    function applyElevenLabsVoiceSettings() {
        const stability = parseInt(document.getElementById('elevenlabs_stability').value, 10) / 100;
        const similarity_boost = parseInt(document.getElementById('elevenlabs_similarity').value, 10) / 100;
        const style = parseInt(document.getElementById('elevenlabs_style').value, 10) / 100;
        const use_speaker_boost = document.getElementById('elevenlabs_speaker_boost').checked;
        fetch('/api/voice/elevenlabs-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stability: stability, similarity_boost: similarity_boost, style: style, use_speaker_boost: use_speaker_boost }) })
            .then(function(r) { if (r.ok) return; return r.json().then(function(err) { throw new Error(err.detail || 'Failed'); }); })
            .catch(function(e) { console.error(e); if (typeof showNotification === 'function') showNotification('ElevenLabs update failed', 'error'); });
    }
    const elevenlabsStability = document.getElementById('elevenlabs_stability');
    const elevenlabsSimilarity = document.getElementById('elevenlabs_similarity');
    const elevenlabsStyle = document.getElementById('elevenlabs_style');
    const elevenlabsSpeakerBoost = document.getElementById('elevenlabs_speaker_boost');
    if (elevenlabsStability && document.getElementById('elevenlabs_stability_value')) {
        var elevenlabsThrottle = null;
        function updateElevenLabsLabels() {
            document.getElementById('elevenlabs_stability_value').textContent = elevenlabsStability.value + '%';
            document.getElementById('elevenlabs_similarity_value').textContent = elevenlabsSimilarity.value + '%';
            document.getElementById('elevenlabs_style_value').textContent = elevenlabsStyle.value + '%';
        }
        elevenlabsStability.addEventListener('input', function() { updateElevenLabsLabels(); if (elevenlabsThrottle) clearTimeout(elevenlabsThrottle); elevenlabsThrottle = setTimeout(function() { elevenlabsThrottle = null; applyElevenLabsVoiceSettings(); }, throttleMs); });
        elevenlabsStability.addEventListener('change', function() { if (elevenlabsThrottle) { clearTimeout(elevenlabsThrottle); elevenlabsThrottle = null; } applyElevenLabsVoiceSettings(); });
        elevenlabsSimilarity.addEventListener('input', function() { updateElevenLabsLabels(); if (elevenlabsThrottle) clearTimeout(elevenlabsThrottle); elevenlabsThrottle = setTimeout(function() { elevenlabsThrottle = null; applyElevenLabsVoiceSettings(); }, throttleMs); });
        elevenlabsSimilarity.addEventListener('change', function() { if (elevenlabsThrottle) { clearTimeout(elevenlabsThrottle); elevenlabsThrottle = null; } applyElevenLabsVoiceSettings(); });
        elevenlabsStyle.addEventListener('input', function() { updateElevenLabsLabels(); if (elevenlabsThrottle) clearTimeout(elevenlabsThrottle); elevenlabsThrottle = setTimeout(function() { elevenlabsThrottle = null; applyElevenLabsVoiceSettings(); }, throttleMs); });
        elevenlabsStyle.addEventListener('change', function() { if (elevenlabsThrottle) { clearTimeout(elevenlabsThrottle); elevenlabsThrottle = null; } applyElevenLabsVoiceSettings(); });
        if (elevenlabsSpeakerBoost) elevenlabsSpeakerBoost.addEventListener('change', function() { applyElevenLabsVoiceSettings(); });
    }
    const playVoiceButton = document.getElementById('play_voice_button');
    if (playVoiceButton) playVoiceButton.addEventListener('click', function() { playVoice(); });
    const oraclePositionSelect = document.getElementById('oracle_position');
    if (oraclePositionSelect) {
        oraclePositionSelect.addEventListener('change', function() {
            const position = this.value || 'custom';
            fetch('/api/oracle/position', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ oracle_position: position }) })
                .then(function(r) { if (r.ok) return; return r.json().then(function(err) { throw new Error(err.detail || 'Failed'); }); })
                .catch(function(e) { console.error(e); if (typeof showNotification === 'function') showNotification('Oracle position update failed', 'error'); });
        });
    }
}

// Initialize general settings when DOM is loaded
function _initGeneral() {
    if (!document.getElementById('tab-general')) return;
    loadGeneralSettings();
    setupGeneralSliders();
    const providerSelect = document.getElementById('tts_provider');
    if (providerSelect) {
        providerSelect.addEventListener('change', function() {
            const p = this.value;
            updateVoiceOptions(p);
            const el = document.getElementById('elevenlabs_voice_options');
            if (el) el.classList.toggle('hidden', p !== 'elevenlabs');
            _updateCustomVoiceButton(p);
        });
    }
    const addBtn = document.getElementById('add_custom_voice_btn');
    if (addBtn) addBtn.addEventListener('click', openCustomVoiceModal);
    const voiceSelect = document.getElementById('tts_voice');
    if (voiceSelect) voiceSelect.addEventListener('change', _updateDeleteButton);
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initGeneral);
} else {
    _initGeneral();
}

// ── Custom Voice Cloning ─────────────────────────────────────────────

// Show/hide the "+ Custom" button based on whether the provider supports custom voices
function _updateCustomVoiceButton(providerId) {
    const btn = document.getElementById('add_custom_voice_btn');
    if (!btn) return;
    const info = _ttsProviders.find(p => p.id === providerId);
    btn.classList.toggle('hidden', !(info && info.supports_custom_voices));
}

// Show/hide the delete and edit buttons based on whether the selected voice is custom
function _updateDeleteButton() {
    const voiceSelect = document.getElementById('tts_voice');
    const selected = voiceSelect && voiceSelect.selectedOptions[0];
    const isCustom = selected && selected.dataset.custom === '1';
    const delBtn = document.getElementById('delete_custom_voice_btn');
    const editBtn = document.getElementById('edit_custom_voice_btn');
    if (delBtn) delBtn.classList.toggle('hidden', !isCustom);
    if (editBtn) editBtn.classList.toggle('hidden', !isCustom);
}

// Delete the currently selected custom voice
function deleteSelectedCustomVoice() {
    const voiceSelect = document.getElementById('tts_voice');
    const selected = voiceSelect && voiceSelect.selectedOptions[0];
    if (!selected || selected.dataset.custom !== '1') return;

    const voiceId = selected.value;
    const voiceName = selected.textContent.replace(/^⭐\s*/, '');

    if (!confirm('Delete custom voice "' + voiceName + '"? This cannot be undone.')) return;

    const provider = document.getElementById('tts_provider').value;
    _resolveAndDeleteCustomVoice(provider, voiceId, voiceName);
}

async function _resolveAndDeleteCustomVoice(provider, voiceId, voiceName) {
    try {
        // Fetch custom voices from DB to find the matching one
        const resp = await fetch('/api/custom-voices?provider=' + encodeURIComponent(provider));
        if (!resp.ok) throw new Error('Failed to fetch custom voices');
        const customs = await resp.json();

        // Match by provider_voice_id or by "custom_<id>" pattern
        const match = customs.find(cv =>
            cv.provider_voice_id === voiceId || 'custom_' + cv.id === voiceId
        );

        if (match) {
            // Voice exists in local DB — delete via DB endpoint
            const delResp = await fetch('/api/custom-voices/' + match.id, { method: 'DELETE' });
            if (!delResp.ok) {
                const err = await delResp.json();
                throw new Error(err.error || 'Delete failed');
            }
        } else if (provider === 'elevenlabs') {
            // Cloned voice on ElevenLabs but not in local DB — delete via API directly
            const delResp = await fetch('/api/elevenlabs-voices/' + encodeURIComponent(voiceId), { method: 'DELETE' });
            if (!delResp.ok) {
                const err = await delResp.json();
                throw new Error(err.error || 'Delete failed');
            }
        } else {
            showNotification('Custom voice not found in database', 'error');
            return;
        }

        showNotification('Custom voice "' + voiceName + '" deleted', 'success');
        await _refreshProviderVoices();

        // Select the first voice in the list
        const voiceSelect = document.getElementById('tts_voice');
        if (voiceSelect && voiceSelect.options.length > 0) {
            voiceSelect.selectedIndex = 0;
        }
        _updateDeleteButton();
    } catch (e) {
        console.error('Delete custom voice error:', e);
        showNotification('Failed to delete voice: ' + e.message, 'error');
    }
}

// Open the custom voice creation modal
function openCustomVoiceModal() {
    const provider = document.getElementById('tts_provider').value;
    const info = _ttsProviders.find(p => p.id === provider);
    if (!info || !info.supports_custom_voices) return;

    // Reset form
    document.getElementById('cv_form').reset();
    document.getElementById('cv_provider').value = provider;
    document.getElementById('cv_modal_provider_label').textContent = info.name;
    document.getElementById('cv_error').classList.add('hidden');
    document.getElementById('cv_form').classList.remove('hidden');
    document.getElementById('cv_processing').classList.add('hidden');
    document.getElementById('cv_submit_btn').disabled = true;
    document.getElementById('cv_transcribing').classList.add('hidden');
    document.getElementById('cv_prompt').value = '';
    // Reset gender toggle to female
    setCvGender('female');
    // Reset audio mode to upload
    setCvAudioMode('upload');
    // Clear any previous recording
    _cvRecordedBlob = null;

    // Show limit info for providers with a cap
    const limitEl = document.getElementById('cv_limit_info');
    let limitReached = false;
    if (info.custom_voice_limit) {
        const customCount = (info.voices || []).filter(v => v.custom).length;
        limitEl.textContent = customCount + '/' + info.custom_voice_limit + ' custom voices used';
        limitEl.classList.remove('hidden');
        if (customCount >= info.custom_voice_limit) {
            limitEl.textContent += ' — limit reached';
            limitReached = true;
        }
    } else {
        limitEl.classList.add('hidden');
    }

    const modal = document.getElementById('custom_voice_modal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.getElementById('cv_name').focus();

    // Wire up auto-transcription on file selection and enable submit when file is picked
    const audioInput = document.getElementById('cv_audio');
    audioInput.onchange = function() {
        if (this.files && this.files.length > 0) {
            if (!limitReached) document.getElementById('cv_submit_btn').disabled = false;
            _autoTranscribe(this.files[0]);
        } else {
            document.getElementById('cv_submit_btn').disabled = true;
        }
    };
}

// Close the modal
function closeCustomVoiceModal() {
    _cleanupRecording();
    _cvRecordedBlob = null;
    const modal = document.getElementById('custom_voice_modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

// Toggle gender selection in the custom voice modal
function setCvGender(gender) {
    document.getElementById('cv_gender').value = gender;
    const femaleBtn = document.getElementById('cv_gender_female');
    const maleBtn = document.getElementById('cv_gender_male');
    if (gender === 'male') {
        maleBtn.className = 'px-4 py-1.5 text-sm rounded transition-colors bg-[#10a37f] text-white';
        femaleBtn.className = 'px-4 py-1.5 text-sm rounded transition-colors text-gray-400 hover:text-white';
    } else {
        femaleBtn.className = 'px-4 py-1.5 text-sm rounded transition-colors bg-[#10a37f] text-white';
        maleBtn.className = 'px-4 py-1.5 text-sm rounded transition-colors text-gray-400 hover:text-white';
    }
}

// Submit the custom voice form
function submitCustomVoice(e) {
    e.preventDefault();
    const errEl = document.getElementById('cv_error');
    errEl.classList.add('hidden');

    const name = document.getElementById('cv_name').value.trim();
    const provider = document.getElementById('cv_provider').value;
    const systemPrompt = document.getElementById('cv_prompt').value.trim();
    const audioInput = document.getElementById('cv_audio');
    const isRecordMode = _cvAudioMode === 'record';

    if (!name) { _showCvError('Name is required'); return false; }
    if (isRecordMode && !_cvRecordedBlob) { _showCvError('Please record a voice sample first'); return false; }
    if (!isRecordMode && (!audioInput.files || audioInput.files.length === 0)) { _showCvError('At least one audio file is required'); return false; }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('provider', provider);
    fd.append('system_prompt', systemPrompt);
    fd.append('personality', document.getElementById('cv_personality').value.trim());
    fd.append('gender', document.getElementById('cv_gender').value || 'female');

    if (isRecordMode && _cvRecordedBlob) {
        // Use correct extension based on actual recording format
        const ext = (_cvRecordedBlob.type || '').includes('webm') ? 'webm' : 'ogg';
        fd.append('audio_0', _cvRecordedBlob, 'recording.' + ext);
    } else {
        for (let i = 0; i < audioInput.files.length; i++) {
            fd.append('audio_' + i, audioInput.files[i]);
        }
    }

    document.getElementById('cv_submit_btn').disabled = true;
    document.getElementById('cv_form').classList.add('hidden');
    document.getElementById('cv_processing').classList.remove('hidden');
    document.getElementById('cv_processing_text').textContent = 'Processing voice clone...';

    fetch('/api/custom-voices', { method: 'POST', body: fd })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            if (!ok) {
                _cvDone();
                _showCvError(data.error || 'Failed to create voice');
                return;
            }
            // Poll for status
            _pollCustomVoice(data.id);
        })
        .catch(err => {
            _cvDone();
            _showCvError('Network error: ' + err.message);
        });

    return false;
}

// Auto-transcribe the first uploaded audio file
function _autoTranscribe(file) {
    const indicator = document.getElementById('cv_transcribing');
    const promptEl = document.getElementById('cv_prompt');
    indicator.classList.remove('hidden');

    const fd = new FormData();
    fd.append('audio', file);

    fetch('/api/custom-voices/transcribe', { method: 'POST', body: fd })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            indicator.classList.add('hidden');
            if (ok && data.transcript) {
                promptEl.value = data.transcript;
            }
        })
        .catch(() => {
            indicator.classList.add('hidden');
        });
}

function _showCvError(msg) {
    const el = document.getElementById('cv_error');
    el.textContent = msg;
    el.classList.remove('hidden');
    document.getElementById('cv_form').classList.remove('hidden');
    document.getElementById('cv_processing').classList.add('hidden');
    document.getElementById('cv_submit_btn').disabled = false;
}

function _cvDone() {
    document.getElementById('cv_submit_btn').disabled = false;
}

// Poll custom voice status until ready or failed
function _pollCustomVoice(voiceId) {
    const interval = setInterval(() => {
        fetch('/api/custom-voices/' + voiceId + '/status')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ready') {
                    clearInterval(interval);
                    document.getElementById('cv_processing_text').textContent = 'Voice ready!';
                    showNotification('Custom voice created successfully', 'success');
                    setTimeout(() => {
                        closeCustomVoiceModal();
                        _refreshProviderVoices();
                    }, 800);
                } else if (data.status === 'failed') {
                    clearInterval(interval);
                    _showCvError(data.error_message || 'Voice processing failed');
                }
                // else still processing, keep polling
            })
            .catch(() => {
                clearInterval(interval);
                _showCvError('Lost connection while checking status');
            });
    }, 2000);
}

// Refresh the provider list and voice dropdown after a custom voice is created/deleted
async function _refreshProviderVoices() {
    await loadTTSProviders();
    const provider = document.getElementById('tts_provider').value;
    const currentVoice = document.getElementById('tts_voice').value;
    await updateVoiceOptions(provider);
    // Try to re-select the previously selected voice
    const voiceSelect = document.getElementById('tts_voice');
    if (voiceSelect) {
        const opts = Array.from(voiceSelect.options).map(o => o.value);
        if (opts.includes(currentVoice)) voiceSelect.value = currentVoice;
    }
    _updateCustomVoiceButton(provider);
}

// ── Edit Custom Voice Modal ───────────────────────────────────────────

// Open the edit modal for the currently selected custom voice
async function openEditCustomVoiceModal() {
    const voiceSelect = document.getElementById('tts_voice');
    const selected = voiceSelect && voiceSelect.selectedOptions[0];
    if (!selected || selected.dataset.custom !== '1') return;

    const provider = document.getElementById('tts_provider').value;
    const voiceId = selected.value;

    // Fetch custom voices from DB to find the matching one and get personality
    try {
        const resp = await fetch('/api/custom-voices?provider=' + encodeURIComponent(provider));
        if (!resp.ok) throw new Error('Failed to fetch custom voices');
        const customs = await resp.json();
        const match = customs.find(cv =>
            cv.provider_voice_id === voiceId || 'custom_' + cv.id === voiceId
        );
        if (!match) {
            showNotification('Custom voice not found in database', 'error');
            return;
        }

        document.getElementById('edit_voice_id').value = match.id;
        document.getElementById('edit_voice_name').textContent = match.name;
        document.getElementById('edit_voice_personality').value = match.personality || '';
        document.getElementById('edit_voice_error').classList.add('hidden');

        const modal = document.getElementById('edit_voice_modal');
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        document.getElementById('edit_voice_personality').focus();
    } catch (e) {
        console.error('openEditCustomVoiceModal error:', e);
        showNotification('Failed to load voice data: ' + e.message, 'error');
    }
}

// Close the edit modal
function closeEditVoiceModal() {
    const modal = document.getElementById('edit_voice_modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

// Submit the edit form (PATCH personality)
async function submitEditVoice(e) {
    e.preventDefault();
    const voiceId = document.getElementById('edit_voice_id').value;
    const personality = document.getElementById('edit_voice_personality').value.trim();
    const errEl = document.getElementById('edit_voice_error');
    const submitBtn = document.getElementById('edit_voice_submit_btn');
    errEl.classList.add('hidden');
    submitBtn.disabled = true;

    try {
        const resp = await fetch('/api/custom-voices/' + voiceId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ personality })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'Failed to update');
        showNotification('Personality updated', 'success');
        closeEditVoiceModal();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
    }
    return false;
}

// ── Voice Recording for Custom Voice Modal ──────────────────────────────

let _cvAudioMode = 'upload';
let _cvRecordedBlob = null;
let _cvMediaRecorder = null;
let _cvRecordStream = null;
let _cvRecordChunks = [];
let _cvRecordTimer = null;
let _cvRecordStartTime = 0;
let _cvRecordAnalyser = null;
let _cvRecordLevelRAF = null;
let _cvPlaybackAudio = null;
const CV_MAX_RECORD_SECS = 12;

function setCvAudioMode(mode) {
    _cvAudioMode = mode;
    const uploadBtn = document.getElementById('cv_mode_upload');
    const recordBtn = document.getElementById('cv_mode_record');
    const uploadPanel = document.getElementById('cv_upload_panel');
    const recordPanel = document.getElementById('cv_record_panel');
    const activeClass = 'px-4 py-1.5 text-sm rounded transition-colors bg-[#10a37f] text-white';
    const inactiveClass = 'px-4 py-1.5 text-sm rounded transition-colors text-gray-400 hover:text-white';

    if (mode === 'record') {
        recordBtn.className = activeClass;
        uploadBtn.className = inactiveClass;
        uploadPanel.classList.add('hidden');
        recordPanel.classList.remove('hidden');
        // Reset recording wizard to ready state
        _resetRecordingUI();
    } else {
        uploadBtn.className = activeClass;
        recordBtn.className = inactiveClass;
        recordPanel.classList.add('hidden');
        uploadPanel.classList.remove('hidden');
        // Stop any active recording
        _cleanupRecording();
    }
    // Update submit button state
    _updateCvSubmitState();
}

function _resetRecordingUI() {
    document.getElementById('cv_rec_ready').classList.remove('hidden');
    document.getElementById('cv_rec_active').classList.add('hidden');
    document.getElementById('cv_rec_done').classList.add('hidden');
    document.getElementById('cv_rec_timer').textContent = '0:00';
    document.getElementById('cv_rec_level').style.width = '0%';
}

function _updateCvSubmitState() {
    const btn = document.getElementById('cv_submit_btn');
    if (_cvAudioMode === 'record') {
        btn.disabled = !_cvRecordedBlob;
    } else {
        const audioInput = document.getElementById('cv_audio');
        btn.disabled = !audioInput.files || audioInput.files.length === 0;
    }
}

function _cleanupRecording() {
    if (_cvMediaRecorder && _cvMediaRecorder.state !== 'inactive') {
        _cvMediaRecorder.stop();
    }
    if (_cvRecordStream) {
        _cvRecordStream.getTracks().forEach(t => t.stop());
        _cvRecordStream = null;
    }
    if (_cvRecordTimer) {
        clearInterval(_cvRecordTimer);
        _cvRecordTimer = null;
    }
    if (_cvRecordLevelRAF) {
        cancelAnimationFrame(_cvRecordLevelRAF);
        _cvRecordLevelRAF = null;
    }
    if (_cvPlaybackAudio) {
        _cvPlaybackAudio.pause();
        _cvPlaybackAudio = null;
    }
    _cvMediaRecorder = null;
    _cvRecordChunks = [];
    _cvRecordAnalyser = null;
}

async function startCvRecording() {
    try {
        _cleanupRecording();
        _cvRecordedBlob = null;
        _cvRecordChunks = [];

        _cvRecordStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // Set up analyser for level meter
        const audioCtx = new AudioContext();
        const source = audioCtx.createMediaStreamSource(_cvRecordStream);
        _cvRecordAnalyser = audioCtx.createAnalyser();
        _cvRecordAnalyser.fftSize = 256;
        source.connect(_cvRecordAnalyser);

        // Use WAV-compatible mime type, fall back gracefully
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
        _cvMediaRecorder = mimeType
            ? new MediaRecorder(_cvRecordStream, { mimeType })
            : new MediaRecorder(_cvRecordStream);

        _cvMediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) _cvRecordChunks.push(e.data);
        };

        _cvMediaRecorder.onstop = () => {
            const blob = new Blob(_cvRecordChunks, { type: _cvMediaRecorder.mimeType || 'audio/webm' });
            _cvRecordedBlob = blob;
            _cvRecordStream.getTracks().forEach(t => t.stop());
            _cvRecordStream = null;

            // Show done state
            document.getElementById('cv_rec_active').classList.add('hidden');
            document.getElementById('cv_rec_done').classList.remove('hidden');
            const elapsed = (Date.now() - _cvRecordStartTime) / 1000;
            document.getElementById('cv_rec_duration').textContent = elapsed.toFixed(1) + 's';
            _updateCvSubmitState();

            // Auto-transcribe the recording
            _autoTranscribe(blob);
        };

        _cvMediaRecorder.start(250); // collect data every 250ms
        _cvRecordStartTime = Date.now();

        // Switch UI to active state
        document.getElementById('cv_rec_ready').classList.add('hidden');
        document.getElementById('cv_rec_active').classList.remove('hidden');
        document.getElementById('cv_rec_done').classList.add('hidden');

        // Start timer
        _cvRecordTimer = setInterval(() => {
            const elapsed = (Date.now() - _cvRecordStartTime) / 1000;
            const secs = Math.floor(elapsed);
            document.getElementById('cv_rec_timer').textContent =
                Math.floor(secs / 60) + ':' + String(secs % 60).padStart(2, '0');
            // Auto-stop at max duration
            if (elapsed >= CV_MAX_RECORD_SECS) {
                stopCvRecording();
            }
        }, 250);

        // Start level meter animation
        _animateLevel();

    } catch (err) {
        _showCvError('Microphone access denied: ' + err.message);
    }
}

function _animateLevel() {
    if (!_cvRecordAnalyser) return;
    const data = new Uint8Array(_cvRecordAnalyser.frequencyBinCount);
    _cvRecordAnalyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / data.length;
    const pct = Math.min(100, (avg / 128) * 100);
    const levelEl = document.getElementById('cv_rec_level');
    if (levelEl) levelEl.style.width = pct + '%';
    _cvRecordLevelRAF = requestAnimationFrame(_animateLevel);
}

function stopCvRecording() {
    if (_cvRecordTimer) {
        clearInterval(_cvRecordTimer);
        _cvRecordTimer = null;
    }
    if (_cvRecordLevelRAF) {
        cancelAnimationFrame(_cvRecordLevelRAF);
        _cvRecordLevelRAF = null;
    }
    if (_cvMediaRecorder && _cvMediaRecorder.state !== 'inactive') {
        _cvMediaRecorder.stop();
    }
}

function playCvRecording() {
    if (!_cvRecordedBlob) return;
    if (_cvPlaybackAudio) {
        _cvPlaybackAudio.pause();
        _cvPlaybackAudio = null;
        document.getElementById('cv_rec_play_btn').textContent = '▶ Play';
        return;
    }
    const url = URL.createObjectURL(_cvRecordedBlob);
    _cvPlaybackAudio = new Audio(url);
    document.getElementById('cv_rec_play_btn').textContent = '⏹ Stop';
    _cvPlaybackAudio.onended = () => {
        _cvPlaybackAudio = null;
        document.getElementById('cv_rec_play_btn').textContent = '▶ Play';
    };
    _cvPlaybackAudio.play();
}

function retakeCvRecording() {
    _cleanupRecording();
    _cvRecordedBlob = null;
    _resetRecordingUI();
    _updateCvSubmitState();
}

// Export functions for use in other files
window.loadGeneralSettings = loadGeneralSettings;
window.saveGeneralSettings = saveGeneralSettings;
window.playVoice = playVoice;
window.stopVoice = stopVoice;
window.toggleVoicePlayback = toggleVoicePlayback;
window.openCustomVoiceModal = openCustomVoiceModal;
window.closeCustomVoiceModal = closeCustomVoiceModal;
window.submitCustomVoice = submitCustomVoice;
window.setCvGender = setCvGender;
window.setCvAudioMode = setCvAudioMode;
window.startCvRecording = startCvRecording;
window.stopCvRecording = stopCvRecording;
window.playCvRecording = playCvRecording;
window.retakeCvRecording = retakeCvRecording;
window.deleteSelectedCustomVoice = deleteSelectedCustomVoice;
window.openEditCustomVoiceModal = openEditCustomVoiceModal;
window.closeEditVoiceModal = closeEditVoiceModal;
window.submitEditVoice = submitEditVoice;
