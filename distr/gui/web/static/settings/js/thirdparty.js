// Third Party Providers JavaScript - matches Qt implementation

// Provider configuration - matches ThirdPartyTab exactly
const PROVIDERS = [
    {id: 'assemblyai', name: 'AssemblyAI', keyField: 'key'},
    {id: 'openai', name: 'OpenAI', keyField: 'key'},
    {id: 'anthropic', name: 'Anthropic', keyField: 'key'},
    {id: 'elevenlabs', name: 'ElevenLabs', keyField: 'key'},
    {id: 'openrouter', name: 'OpenRouter', keyField: 'key'},
    {id: 'groq', name: 'Groq', keyField: 'key'},
    {id: 'kilocode', name: 'KiloCode', keyField: 'key', settingsKey: 'kilo'},
    {id: 'rube', name: 'Rube', keyField: 'token'}
];

// Validation states
const validationStates = {};
function isMaskedSecret(value) {
    return !!value && value.indexOf('*') !== -1;
}

// Load settings from backend
async function loadThirdPartySettings() {
    try {
        const response = await fetch('/api/thirdparty');
        if (!response.ok) {
            throw new Error('Failed to load settings');
        }
        const settings = await response.json();

        // Load Ollama URL
        document.getElementById('ollama_url').value = settings.ollama_url || 'http://localhost:11434/';

        // Load provider settings and validate if enabled with key
        const validationPromises = [];

        PROVIDERS.forEach(provider => {
            const settingsKey = provider.settingsKey || provider.id;
            const checkbox = document.getElementById(`${provider.id}_enabled`);
            const input = document.getElementById(`${provider.id}_${provider.keyField}`);

            const enabled = settings[`${settingsKey}_enabled`] || false;
            const key = settings[`${settingsKey}_${provider.keyField}`] || '';

            if (checkbox) {
                checkbox.checked = enabled;
                toggleProviderInput(provider.id, enabled);
            }

            if (input) {
                input.value = key;
                if (isMaskedSecret(key)) {
                    input.setAttribute('data-masked', 'true');
                } else {
                    input.removeAttribute('data-masked');
                }

                // Auto-validate if enabled and has a key
                if (enabled && key && key.trim() && !isMaskedSecret(key)) {
                    validationPromises.push(validateProvider(provider.id));
                } else {
                    clearValidationIndicator(provider.id);
                }
            }
        });

        // Wait for all validations to complete
        if (validationPromises.length > 0) {
            await Promise.all(validationPromises);
        }

        console.log('Settings loaded and validated successfully');
    } catch (error) {
        console.error('Error loading settings:', error);
        showNotification('Failed to load settings: ' + error.message, 'error');
    }
}

// Save settings to backend
async function saveThirdPartySettings() {
    try {
        // Check for invalid keys before saving
        const invalidProviders = [];

        PROVIDERS.forEach(provider => {
            const checkbox = document.getElementById(`${provider.id}_enabled`);
            const input = document.getElementById(`${provider.id}_${provider.keyField}`);
            const validationState = validationStates[provider.id];

            // If enabled and has a key, it must be validated and valid
            if (checkbox && checkbox.checked && input && input.value.trim()) {
                const masked = isMaskedSecret(input.value.trim());
                if (!masked && (validationState === 'invalid' || !validationState)) {
                        invalidProviders.push(provider.name);
                }
            }
        });

        // Prevent saving if there are invalid keys
        if (invalidProviders.length > 0) {
            const providerList = invalidProviders.join(', ');
            showNotification(`Cannot save: Invalid API keys for ${providerList}. Please validate all enabled providers before saving.`, 'error');
            return;
        }

        const settings = {
            ollama_url: document.getElementById('ollama_url').value || 'http://localhost:11434/'
        };

        // Collect provider settings
        PROVIDERS.forEach(provider => {
            const settingsKey = provider.settingsKey || provider.id;
            const checkbox = document.getElementById(`${provider.id}_enabled`);
            const input = document.getElementById(`${provider.id}_${provider.keyField}`);

            settings[`${settingsKey}_enabled`] = checkbox ? checkbox.checked : false;
            settings[`${settingsKey}_${provider.keyField}`] = input ? input.value : '';
        });

        const response = await fetch('/api/thirdparty', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save settings');
        }

        const result = await response.json();
        showNotification('Settings saved successfully', 'success');
        console.log('Settings saved:', result);

        // Notify same-page tabs (LLMs, Kanban) to refresh provider dropdowns
        window.dispatchEvent(new CustomEvent('thirdparty-providers-changed'));

        // Notify other open pages (Chat) via BroadcastChannel
        try {
            const bc = new BroadcastChannel('providers-changed');
            bc.postMessage({ type: 'thirdparty-providers-changed' });
            bc.close();
        } catch (_) { /* BroadcastChannel not supported — no-op */ }
    } catch (error) {
        console.error('Error saving settings:', error);
        showNotification('Failed to save settings: ' + error.message, 'error');
    }
}

// Toggle provider input field based on checkbox
function toggleProviderInput(providerId, enabled) {
    const provider = PROVIDERS.find(p => p.id === providerId);
    if (!provider) return;

    const input = document.getElementById(`${providerId}_${provider.keyField}`);
    const validateBtn = document.getElementById(`${providerId}_validate`);

    if (input) {
        input.disabled = !enabled;
        if (!enabled) {
            // Clear validation when disabled
            clearValidationIndicator(providerId);
        }
    }

    if (validateBtn) {
        validateBtn.disabled = !enabled;
    }
}

// Validate API key
async function validateProvider(providerId) {
    const provider = PROVIDERS.find(p => p.id === providerId);
    if (!provider) return;

    const input = document.getElementById(`${providerId}_${provider.keyField}`);
    const key = input ? input.value.trim() : '';

    if (!key) {
        setValidationIndicator(providerId, 'invalid', 'API key is required');
        return;
    }
    if (isMaskedSecret(key)) {
        setValidationIndicator(providerId, 'valid', 'Stored key');
        return;
    }

    // Show validating state
    setValidationIndicator(providerId, 'validating', 'Validating...');

    try {
        const response = await fetch('/api/validate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                provider: providerId,
                key: key
            })
        });

        const result = await response.json();

        if (result.valid) {
            setValidationIndicator(providerId, 'valid', 'Valid');
        } else {
            setValidationIndicator(providerId, 'invalid', result.error || 'Invalid API key');
        }
    } catch (error) {
        console.error(`Validation error for ${providerId}:`, error);
        setValidationIndicator(providerId, 'invalid', 'Validation failed: ' + error.message);
    }
}

// Set validation indicator state
function setValidationIndicator(providerId, state, tooltip) {
    const indicator = document.getElementById(`${providerId}_indicator`);
    if (!indicator) return;

    validationStates[providerId] = state;

    // Clear existing classes
    indicator.className = 'validation-indicator';

    if (state === 'valid') {
        indicator.textContent = '✓';
        indicator.classList.add('valid');
        indicator.title = tooltip || 'Valid';
    } else if (state === 'invalid') {
        indicator.textContent = '✗';
        indicator.classList.add('invalid');
        indicator.title = tooltip || 'Invalid';
    } else if (state === 'validating') {
        indicator.textContent = '⟳';
        indicator.classList.add('validating');
        indicator.title = 'Validating...';
    } else {
        indicator.textContent = '';
        indicator.title = '';
    }
}

// Clear validation indicator
function clearValidationIndicator(providerId) {
    const indicator = document.getElementById(`${providerId}_indicator`);
    if (indicator) {
        indicator.textContent = '';
        indicator.className = 'validation-indicator';
        indicator.title = '';
    }
    delete validationStates[providerId];
}

// Use global showNotification from settings.js (snackbar)

// Initialize third party settings when DOM is loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        // Check if we're on the thirdparty tab
        if (document.getElementById('tab-thirdparty')) {
            loadThirdPartySettings();
        }
    });
} else {
    // DOM already loaded
    if (document.getElementById('tab-thirdparty')) {
        loadThirdPartySettings();
    }
}

// Export functions for use in HTML
window.toggleProviderInput = toggleProviderInput;
window.validateProvider = validateProvider;
window.saveThirdPartySettings = saveThirdPartySettings;
window.loadThirdPartySettings = loadThirdPartySettings;
