/**
 * OpenAI Realtime S2S UI locks — shared by Settings and Chat.
 * Source of truth: GET /api/llms/s2s-locks?model=…
 */
(function (global) {
    var _sttValueBeforeLock = null;
    var _voiceStash = null;

    function applyDisabled(el, locked) {
        if (!el) return;
        el.disabled = !!locked;
        if (locked) {
            el.setAttribute('aria-disabled', 'true');
        } else {
            el.removeAttribute('aria-disabled');
        }
    }

    async function fetchS2sLocks(model) {
        var q = encodeURIComponent(model || '');
        var res = await fetch('/api/llms/s2s-locks?model=' + q);
        if (!res.ok) {
            return { s2s_active: false };
        }
        return res.json();
    }

    /**
     * @param {object} opts
     * @param {string} opts.model
     * @param {HTMLSelectElement|null} opts.sttSelect
     * @param {HTMLSelectElement|null} opts.conversationalProvider
     * @param {HTMLSelectElement|null} opts.conversationalModel
     * @param {HTMLSelectElement|null} opts.ttsProvider
     * @param {HTMLSelectElement|null} opts.openaiTtsModel
     * @param {HTMLSelectElement|null} opts.voiceProvider
     * @param {HTMLSelectElement|null} opts.voiceModel
     * @param {function(string[], string)|null} opts.onRealtimeVoices - (voices, defaultVoice) when S2S
     * @param {function()|null} opts.onRestoreVoices - when leaving S2S
     */
    async function applyS2sLocks(opts) {
        opts = opts || {};
        var locks = await fetchS2sLocks(opts.model);
        var active = !!locks.s2s_active;

        if (opts.sttSelect) {
            if (active && _sttValueBeforeLock === null) {
                _sttValueBeforeLock = opts.sttSelect.value;
            }
            applyDisabled(opts.sttSelect, locks.lock_stt);
            // Never rewrite STT value when locking
            if (!active && _sttValueBeforeLock !== null) {
                _sttValueBeforeLock = null;
            }
        }

        applyDisabled(opts.conversationalProvider, locks.lock_conversational_provider);
        applyDisabled(opts.conversationalModel, locks.lock_conversational_model);
        applyDisabled(opts.ttsProvider, locks.lock_tts_provider);
        applyDisabled(opts.openaiTtsModel, locks.lock_openai_tts_model);

        if (active) {
            if (opts.voiceProvider && opts.voiceModel && !_voiceStash) {
                _voiceStash = {
                    provider: opts.voiceProvider.value,
                    model: opts.voiceModel.value,
                };
            }
            if (opts.voiceProvider) {
                opts.voiceProvider.value = 'openai';
                applyDisabled(opts.voiceProvider, true);
            }
            if (typeof opts.onRealtimeVoices === 'function' && locks.voice_set) {
                opts.onRealtimeVoices(locks.voice_set, locks.default_voice || 'marin');
            }
        } else {
            if (opts.voiceProvider) {
                applyDisabled(opts.voiceProvider, false);
            }
            if (_voiceStash && opts.voiceProvider && opts.voiceModel) {
                var stash = _voiceStash;
                _voiceStash = null;
                if (typeof opts.onRestoreVoices === 'function') {
                    opts.onRestoreVoices(stash);
                } else {
                    opts.voiceProvider.value = stash.provider;
                    opts.voiceModel.value = stash.model;
                }
            } else if (typeof opts.onRestoreVoices === 'function') {
                opts.onRestoreVoices(null);
            }
        }

        return locks;
    }

    global.DecisionsS2S = {
        fetchS2sLocks: fetchS2sLocks,
        applyS2sLocks: applyS2sLocks,
    };
})(typeof window !== 'undefined' ? window : this);
