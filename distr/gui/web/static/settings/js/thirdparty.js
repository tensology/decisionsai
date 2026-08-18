const PROVIDERS = [
    {
        id: 'tensology',
        name: 'Tensology API',
        keyField: 'key',
        blurb: 'Connect DecisionsAI to Tensology mail, customers, projects, time entries, and invoices.',
        docsLabel: 'Tensology Admin',
        docsUrl: 'https://www.tensology.com/admin/integrations/integrationcredential/',
        helper: 'Generate a DecisionsAI audience key in Tensology admin, then validate and save it here.',
        infoHtml: 'Open <a href="https://www.tensology.com/admin/integrations/integrationcredential/" target="_blank">Tensology Admin</a>, create a DecisionsAI API connection linked to your developer account, and copy the one-time key here.',
        color: 'from-orange-400/25 to-blue-500/10'
    },
    {
        id: 'openai',
        name: 'OpenAI',
        keyField: 'key',
        blurb: 'Models, reasoning, image generation, and API-backed coding flows.',
        docsLabel: 'OpenAI Platform',
        docsUrl: 'https://platform.openai.com/api-keys',
        helper: 'Create a secret key in OpenAI Platform, then paste it here and validate it before saving.',
        infoHtml: 'Get your OpenAI API key from <a href="https://platform.openai.com/api-keys" target="_blank">OpenAI Platform</a> → API Keys → Create new secret key',
        color: 'from-emerald-400/25 to-emerald-500/10',
        iconPath: '/assets/img/providers/openai.svg'
    },
    {
        id: 'anthropic',
        name: 'Anthropic',
        keyField: 'key',
        blurb: 'Claude models for conversation, planning, and larger reasoning tasks.',
        docsLabel: 'Anthropic Console',
        docsUrl: 'https://console.anthropic.com/settings/keys',
        helper: 'Use an Anthropic API key if you want Claude-backed routing or coding in DecisionsAI.',
        infoHtml: 'Get your Anthropic API key from <a href="https://console.anthropic.com/settings/keys" target="_blank">Anthropic Console</a> → Settings → API Keys',
        color: 'from-orange-400/25 to-orange-500/10',
        iconPath: '/assets/img/providers/anthropic.png'
    },
    {
        id: 'gemini',
        name: 'Google Gemini',
        keyField: 'key',
        blurb: 'Google AI Studio / Gemini access for model routing and cloud generation.',
        docsLabel: 'Google AI Studio',
        docsUrl: 'https://aistudio.google.com/apikey',
        helper: 'Generate a Gemini API key in Google AI Studio and save it here to enable Gemini-backed providers.',
        infoHtml: 'Get your Gemini API key from <a href="https://aistudio.google.com/apikey" target="_blank">Google AI Studio</a> → Get API Key',
        color: 'from-sky-400/25 to-indigo-500/10',
        iconPath: '/assets/img/providers/gemini.png'
    },
    {
        id: 'elevenlabs',
        name: 'ElevenLabs',
        keyField: 'key',
        blurb: 'Voice synthesis, cloning, and higher-end cloud TTS voices.',
        docsLabel: 'ElevenLabs Settings',
        docsUrl: 'https://elevenlabs.io/app/settings/api-keys',
        helper: 'Use your ElevenLabs API key to unlock cloud voices and custom voice workflows.',
        infoHtml: 'Get your ElevenLabs API key from <a href="https://elevenlabs.io/app/settings/api-keys" target="_blank">ElevenLabs</a> → Profile Settings → API Keys',
        color: 'from-violet-400/25 to-fuchsia-500/10',
        iconPath: '/assets/img/providers/elevenlabs.png'
    },
    {
        id: 'assemblyai',
        name: 'AssemblyAI',
        keyField: 'key',
        blurb: 'Cloud transcription and speech-to-text services.',
        docsLabel: 'AssemblyAI Dashboard',
        docsUrl: 'https://www.assemblyai.com/app',
        helper: 'Paste your AssemblyAI key here if you want to use AssemblyAI speech services.',
        infoHtml: 'Get your AssemblyAI API key from <a href="https://www.assemblyai.com/app" target="_blank">AssemblyAI Dashboard</a> → Settings → API Keys',
        color: 'from-cyan-400/25 to-blue-500/10',
        iconPath: '/assets/img/providers/assemblyai.ico'
    },
    {
        id: 'groq',
        name: 'Groq',
        keyField: 'key',
        blurb: 'Fast hosted inference for supported Groq models.',
        docsLabel: 'Groq Console',
        docsUrl: 'https://console.groq.com/keys',
        helper: 'Great when you want very fast hosted completions and supported Groq model access.',
        infoHtml: 'Get your Groq API key from <a href="https://console.groq.com/keys" target="_blank">Groq Console</a> → API Keys → Create API Key',
        color: 'from-fuchsia-400/25 to-pink-500/10',
        iconPath: '/assets/img/providers/groq.png'
    },
    {
        id: 'openrouter',
        name: 'OpenRouter',
        keyField: 'key',
        blurb: 'Unified access to multiple hosted providers through one API key.',
        docsLabel: 'OpenRouter Keys',
        docsUrl: 'https://openrouter.ai/keys',
        helper: 'Use OpenRouter when you want a broad model catalog behind one provider credential.',
        infoHtml: 'Get your OpenRouter API key from <a href="https://openrouter.ai/keys" target="_blank">OpenRouter</a> → Keys → Create New Key',
        color: 'from-indigo-400/25 to-blue-500/10',
        iconPath: '/assets/img/providers/openrouter.ico'
    },
    {
        id: 'nvidia',
        name: 'NVIDIA',
        keyField: 'key',
        blurb: 'Hosted NVIDIA inference and build.nvidia.com model access.',
        docsLabel: 'build.nvidia.com',
        docsUrl: 'https://build.nvidia.com/models',
        helper: 'Generate a public API key in build.nvidia.com if you want NVIDIA-hosted model routing.',
        infoHtml: 'Get your free API key at <a href="https://build.nvidia.com/models" target="_blank" rel="noopener">build.nvidia.com</a> → avatar → API Keys → Generate API Key (enable Public API Endpoints). Enable the checkbox, paste the key, click Validate (saves automatically).',
        color: 'from-lime-400/25 to-green-500/10',
        iconPath: '/assets/img/providers/nvidia.ico'
    },
    {
        id: 'kilocode',
        name: 'KiloCode',
        keyField: 'key',
        settingsKey: 'kilo',
        blurb: 'Kilo cloud access for coding and model-powered workflows.',
        docsLabel: 'Kilo.ai',
        docsUrl: 'https://kilo.ai/',
        helper: 'Add your KiloCode key here if this workspace should use Kilo-backed services.',
        infoHtml: 'Get your KiloCode API key from <a href="https://kilo.ai/" target="_blank">Kilo.ai</a> → Dashboard → API Keys',
        color: 'from-amber-400/25 to-orange-500/10',
        iconPath: '/assets/img/providers/kilocode.png'
    },
    {
        id: 'composio',
        name: 'Composio',
        keyField: 'key',
        skipValidate: true,
        blurb: 'Third-party app connectivity and external tool routing.',
        docsLabel: 'Composio Platform',
        docsUrl: 'https://platform.composio.dev',
        helper: 'Composio is saved directly here. It skips API validation and recalibrates after save.',
        infoHtml: 'Powers <strong>Composio Connect</strong> MCP in Cursor/Codex (1000+ apps). Rube is deprecated — not configured. Get your key from <a href="https://platform.composio.dev" target="_blank">platform.composio.dev</a> → Project Settings → API Keys. Decisions injects it into your IDE MCP config on save — no manual <code>mcp.json</code> editing.',
        color: 'from-slate-300/25 to-slate-500/10',
        iconPath: '/assets/img/providers/composio.png'
    },
    {
        id: 'pixazo',
        name: 'Pixazo',
        keyField: 'key',
        blurb: 'Image, video, and custom media workflows.',
        docsLabel: 'Pixazo',
        docsUrl: 'https://pixazo.ai',
        helper: 'Use Pixazo for custom media generation and provider-backed visual workflows.',
        infoHtml: 'Get your API key at <a href="https://api-console.pixazo.ai/api_keys" target="_blank" rel="noopener">api-console.pixazo.ai</a> — one key for image, video, and audio models. Browse models at <a href="https://www.pixazo.ai/models" target="_blank" rel="noopener">pixazo.ai/models</a>. Validate saves automatically.',
        color: 'from-rose-400/25 to-orange-400/10',
        iconPath: '/assets/img/providers/pixazo.png'
    },
    {
        id: 'masko',
        name: 'Masko AI',
        keyField: 'key',
        blurb: 'AI skin generation and avatar-related workflows.',
        docsLabel: 'Masko.ai',
        docsUrl: 'https://masko.ai',
        helper: 'Add your Masko key if you want AI skin generation available from settings and avatar tools.',
        infoHtml: 'Get your Masko API key from <a href="https://masko.ai" target="_blank">Masko.ai</a> → Dashboard → API Keys. Used for AI skin generation.',
        color: 'from-emerald-300/25 to-teal-500/10',
        iconPath: '/assets/img/providers/masko.png'
    },
    {
        id: 'cursor',
        name: 'Cursor',
        keyField: 'key',
        blurb: 'Cursor-specific API and model access when available.',
        docsLabel: 'Cursor Dashboard',
        docsUrl: 'https://cursor.com/dashboard',
        helper: 'Use this when Cursor account-backed model access is part of your workspace setup.',
        infoHtml: 'Get your Cursor API key from <a href="https://cursor.com/dashboard" target="_blank">Cursor Dashboard</a> → API Keys',
        color: 'from-blue-400/25 to-cyan-500/10',
        iconPath: '/assets/img/providers/cursor.png'
    }
];

const CONNECT_PROVIDERS = [
    {
        id: 'google',
        name: 'Google',
        blurb: 'Google Workspace account access for Gmail, Calendar, Drive, Docs, and Sheets connections.',
        iconPath: '/assets/img/providers/connect/google.svg'
    },
    {
        id: 'telegram',
        name: 'Telegram',
        blurb: 'Link Telegram so DecisionsAI can work with chats, uploads, notifications, and remote controls.',
        iconPath: '/assets/img/providers/connect/telegram.svg'
    },
    {
        id: 'whatsapp',
        name: 'WhatsApp',
        blurb: 'Connect WhatsApp using the local service so conversations and linked-device workflows stay available.',
        iconPath: '/assets/img/providers/connect/whatsapp.svg'
    },
    {
        id: 'jira',
        name: 'Jira',
        blurb: 'Manage Jira accounts for ticket sync, board routing, and project-linked workflow actions.',
        iconPath: '/assets/img/providers/connect/jira.svg'
    },
    {
        id: 'trello',
        name: 'Trello',
        blurb: 'Manage Trello accounts for board access, cards, and workflow-linked project organization.',
        iconPath: '/assets/img/providers/connect/trello.svg'
    },
    {
        id: 'discord',
        name: 'Discord',
        blurb: 'Save the Discord bot token used for Discord messaging and connected workspace actions.',
        iconPath: '/assets/img/providers/connect/discord.svg'
    },
    {
        id: 'slack',
        name: 'Slack',
        blurb: 'Save Slack bot credentials and signing secrets for Slack events and messaging flows.',
        iconPath: '/assets/img/providers/connect/slack.svg'
    },
    {
        id: 'clickup',
        name: 'ClickUp',
        blurb: 'Store your ClickUp token so Initiative and project tooling can access ClickUp workspaces.',
        iconPath: '/assets/img/providers/connect/clickup.svg'
    },
    {
        id: 'monday',
        name: 'Monday',
        blurb: 'Store your Monday token for connected boards and vendor-backed project routing.',
        iconPath: '/assets/img/providers/connect/mondaydotcom.svg'
    }
];

const validationStates = {};
let thirdPartyDrafts = {};
let selectedThirdPartyProviderId = 'openai';
let thirdPartyDetailOpen = false;
let thirdPartyOllamaUrl = 'http://localhost:11434/';
let activeThirdPartySubtab = 'api_keys';
let selectedThirdPartyConnectProviderId = 'google';
let thirdPartyConnectDetailOpen = false;
let thirdPartyConnectStatusCache = {};
let thirdPartyConnectorDraft = null;
let thirdPartyJiraAccountsCache = [];
let thirdPartyTrelloAccountsCache = [];
let thirdPartyJiraEditingName = null;
let thirdPartyTrelloEditingName = null;
let thirdPartyTelegramSession = null;
let thirdPartyTelegramPollInterval = null;
let thirdPartyWhatsAppPollInterval = null;

function getThirdPartyProvider(providerId) {
    return PROVIDERS.find(function (provider) { return provider.id === providerId; }) || null;
}

function providerSettingsKey(provider) {
    return provider && provider.settingsKey ? provider.settingsKey : (provider ? provider.id : '');
}

function currentThirdPartyProvider() {
    return getThirdPartyProvider(selectedThirdPartyProviderId) || PROVIDERS[0];
}

function getThirdPartyConnectProvider(providerId) {
    return CONNECT_PROVIDERS.find(function (provider) { return provider.id === providerId; }) || null;
}

function currentThirdPartyConnectProvider() {
    return getThirdPartyConnectProvider(selectedThirdPartyConnectProviderId) || CONNECT_PROVIDERS[0];
}

function escapeThirdPartyHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function thirdPartySentenceLinesHtml(text) {
    const value = String(text == null ? '' : text).trim();
    if (!value) return '';
    const sentences = value
        .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
        .map(function (sentence) { return sentence.trim(); })
        .filter(Boolean);
    return sentences.map(function (sentence) {
        return '<span class="block">' + escapeThirdPartyHtml(sentence) + '</span>';
    }).join('');
}

function thirdPartyProviderDraft(providerId) {
    if (!thirdPartyDrafts[providerId]) {
        thirdPartyDrafts[providerId] = { enabled: false, key: '', hasStoredKey: false };
    }
    return thirdPartyDrafts[providerId];
}

function thirdPartyIndicatorMeta(providerId) {
    const state = validationStates[providerId];
    if (state === 'valid') return { text: 'Validated', classes: 'border-green-400/30 bg-green-500/10 text-green-300' };
    if (state === 'invalid') return { text: 'Needs attention', classes: 'border-red-400/30 bg-red-500/10 text-red-300' };
    if (state === 'validating') return { text: 'Validating', classes: 'border-amber-400/30 bg-amber-500/10 text-amber-300' };
    return { text: 'Not validated', classes: 'border-white/10 bg-white/5 text-gray-400' };
}

function thirdPartyStatusText(providerId) {
    const draft = thirdPartyProviderDraft(providerId);
    if (draft.enabled) return draft.hasStoredKey ? 'Enabled' : 'Enabled - key needed';
    return draft.hasStoredKey ? 'Saved but disabled' : 'Disabled';
}

function thirdPartyCardStateClasses(providerId) {
    const draft = thirdPartyProviderDraft(providerId);
    const isSelected = providerId === selectedThirdPartyProviderId;
    if (draft.enabled) {
        return isSelected
            ? 'border-[#10a37f] bg-[#10a37f]/10 shadow-[0_0_0_1px_rgba(16,163,127,0.16),0_0_0_2px_rgba(249,115,22,0.38)] shadow-lg shadow-[#10a37f]/20'
            : 'border-[#10a37f] bg-[#10a37f]/10 shadow-[0_0_0_1px_rgba(16,163,127,0.16)] hover:border-[#34d399]';
    }
    return isSelected
        ? 'border-[#565869] bg-[#152054]/40 shadow-[0_0_0_2px_rgba(249,115,22,0.32)]'
        : 'border-[#565869] bg-[#152054]/40 hover:border-[#7a7c8c]';
}

function thirdPartyHasPendingKey(providerId) {
    const draft = thirdPartyProviderDraft(providerId);
    return !!(draft.key || '').trim();
}

function thirdPartyInfoHtml(provider) {
    return provider && provider.infoHtml
        ? provider.infoHtml
        : ('Get your key from <a href="' + escapeThirdPartyHtml(provider.docsUrl) + '" target="_blank">' + escapeThirdPartyHtml(provider.docsLabel) + '</a>');
}

function thirdPartyIconSvg(provider, sizeClass) {
    const path = provider && provider.iconPath;
    const size = sizeClass || 'h-10 w-10';
    if (path) {
        return '<img src="' + escapeThirdPartyHtml(path) + '" alt="' + escapeThirdPartyHtml(provider.name || '') + ' logo" class="' + size + ' rounded-lg object-contain">';
    }
    return '<span class="' + size + ' inline-flex items-center justify-center text-xs font-semibold">' + escapeThirdPartyHtml((provider.name || '?').slice(0, 2).toUpperCase()) + '</span>';
}

function thirdPartyConnectIsConnected(providerId) {
    const status = thirdPartyConnectStatusCache || {};
    if (providerId === 'google') return !!status.google_connected;
    if (providerId === 'telegram') return !!status.telegram_connected;
    if (providerId === 'whatsapp') return !!status.whatsapp_connected;
    if (providerId === 'jira') return !!status.jira_has_valid;
    if (providerId === 'trello') return !!status.trello_has_valid;
    if (providerId === 'discord') return !!status.discord_bot_configured;
    if (providerId === 'slack') return !!(status.slack_bot_configured || status.slack_signing_configured);
    if (providerId === 'clickup') return !!status.clickup_configured;
    if (providerId === 'monday') return !!status.monday_configured;
    return false;
}

function thirdPartyConnectStatusText(providerId) {
    const labels = {
        google: 'Google',
        telegram: 'Telegram',
        whatsapp: 'WhatsApp',
        jira: 'Jira',
        trello: 'Trello',
        discord: 'Discord',
        slack: 'Slack',
        clickup: 'ClickUp',
        monday: 'Monday'
    };
    const label = labels[providerId] || 'Provider';
    return thirdPartyConnectIsConnected(providerId)
        ? label + ' is connected.'
        : label + ' is not connected.';
}

function thirdPartyConnectorHasStoredConfig(providerId) {
    const draft = thirdPartyConnectorDraft || {};
    if (providerId === 'discord') return !!(draft.discord_bot_token || '').trim();
    if (providerId === 'slack') return !!((draft.slack_bot_token || '').trim() || (draft.slack_signing_secret || '').trim());
    if (providerId === 'clickup') return !!(draft.clickup_api_token || '').trim();
    if (providerId === 'monday') return !!(draft.monday_api_token || '').trim();
    return false;
}

function thirdPartyConnectCardStateClasses(providerId) {
    const isSelected = providerId === selectedThirdPartyConnectProviderId;
    if (thirdPartyConnectIsConnected(providerId)) {
        return isSelected
            ? 'border-[#10a37f] bg-[#10a37f]/10 shadow-[0_0_0_1px_rgba(16,163,127,0.16),0_0_0_2px_rgba(249,115,22,0.38)] shadow-lg shadow-[#10a37f]/20'
            : 'border-[#10a37f] bg-[#10a37f]/10 shadow-[0_0_0_1px_rgba(16,163,127,0.16)] hover:border-[#34d399]';
    }
    return isSelected
        ? 'border-[#565869] bg-[#152054]/40 shadow-[0_0_0_2px_rgba(249,115,22,0.32)]'
        : 'border-[#565869] bg-[#152054]/40 hover:border-[#7a7c8c]';
}

function thirdPartyConnectInfoHtml(html) {
    return '<div class="api-key-info" style="margin-top:0">' + html + '</div>';
}

function thirdPartyConnectActionButtonClasses(tone) {
    if (tone === 'danger') {
        return 'px-4 py-2 rounded-md border border-red-400/40 text-red-300 hover:bg-red-500/10 transition-colors';
    }
    if (tone === 'secondary') {
        return 'px-4 py-2 rounded-md border border-white/15 bg-[#10183f] text-[#ececf1] hover:bg-white/10 transition-colors';
    }
    return 'px-4 py-2 rounded-md bg-[#f97316] hover:bg-[#ea580c] text-white font-medium transition-colors';
}

function resetThirdPartyTelegramPolling() {
    if (thirdPartyTelegramPollInterval) {
        clearInterval(thirdPartyTelegramPollInterval);
        thirdPartyTelegramPollInterval = null;
    }
}

function resetThirdPartyWhatsAppPolling() {
    if (thirdPartyWhatsAppPollInterval) {
        clearInterval(thirdPartyWhatsAppPollInterval);
        thirdPartyWhatsAppPollInterval = null;
    }
}

function updateThirdPartyHeader() {
    const titleEl = document.getElementById('thirdparty_section_title');
    const subtitleEl = document.getElementById('thirdparty_section_subtitle');
    const backBtn = document.getElementById('thirdparty_section_back');
    const iconEl = document.getElementById('thirdparty_section_icon');
    const provider = currentThirdPartyProvider();
    const connectProvider = currentThirdPartyConnectProvider();
    if (!titleEl || !subtitleEl || !backBtn || !iconEl) return;
    if (activeThirdPartySubtab === 'api_keys' && thirdPartyDetailOpen && provider) {
        titleEl.textContent = provider.name;
        subtitleEl.innerHTML = thirdPartySentenceLinesHtml(provider.blurb);
        backBtn.classList.remove('hidden');
        iconEl.innerHTML = thirdPartyIconSvg(provider, 'h-10 w-10');
        iconEl.classList.remove('hidden');
        iconEl.classList.add('flex');
    } else if (activeThirdPartySubtab === 'connect' && thirdPartyConnectDetailOpen && connectProvider) {
        titleEl.textContent = connectProvider.name;
        subtitleEl.innerHTML = thirdPartySentenceLinesHtml(connectProvider.blurb);
        backBtn.classList.remove('hidden');
        iconEl.innerHTML = thirdPartyIconSvg(connectProvider, 'h-10 w-10');
        iconEl.classList.remove('hidden');
        iconEl.classList.add('flex');
    } else {
        titleEl.textContent = 'Third Party Vendors';
        if (activeThirdPartySubtab === 'connect') {
            subtitleEl.innerHTML = thirdPartySentenceLinesHtml('Connect external vendors and work accounts directly from this section.');
        } else if (activeThirdPartySubtab === 'mcp') {
            subtitleEl.innerHTML = thirdPartySentenceLinesHtml('Configure MCP servers alongside the vendors and tools they support.');
        } else {
            subtitleEl.innerHTML = thirdPartySentenceLinesHtml('Manage vendor API keys, connected accounts, and MCP servers from one place.');
        }
        backBtn.classList.add('hidden');
        iconEl.innerHTML = '';
        iconEl.classList.add('hidden');
        iconEl.classList.remove('flex');
    }
}

function renderThirdPartyProviderList() {
    const listEl = document.getElementById('thirdparty_provider_grid');
    if (!listEl) return;
    listEl.innerHTML = PROVIDERS.map(function (provider) {
        return '' +
            '<div class="relative w-full h-full text-center rounded-xl border p-6 transition-colors cursor-pointer ' + thirdPartyCardStateClasses(provider.id) + '" role="button" tabindex="0" onclick="selectThirdPartyProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" ondblclick="openThirdPartyProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();selectThirdPartyProvider(\'' + escapeThirdPartyHtml(provider.id) + '\');}">' +
                '<span role="button" tabindex="0" class="absolute top-3 right-3 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-[#565869] bg-[#0d1117] text-[#ececf1] shadow-sm transition-colors hover:border-[#f97316] hover:text-white" aria-label="Edit ' + escapeThirdPartyHtml(provider.name) + '" onclick="event.stopPropagation(); openThirdPartyProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();event.stopPropagation();openThirdPartyProvider(\'' + escapeThirdPartyHtml(provider.id) + '\');}">' +
                    '<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                        '<path d="m14.7 5.3 4 4"></path>' +
                        '<path d="M4 20l3.8-.8L19 8a2.8 2.8 0 1 0-4-4L3.8 15.2 3 19.9Z"></path>' +
                        '<path d="M13.5 6.5 17.5 10.5"></path>' +
                    '</svg>' +
                '</span>' +
                '<div class="flex h-full flex-col items-center justify-center gap-5">' +
                    '<div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-white/95 p-2 shadow-sm">' + thirdPartyIconSvg(provider, 'h-10 w-10') + '</div>' +
                    '<div class="min-w-0 pt-1">' +
                        '<div class="text-base font-semibold leading-5 text-white break-words text-center">' + escapeThirdPartyHtml(provider.name) + '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }).join('');
}

function renderThirdPartyProviderDetail() {
    const detailEl = document.getElementById('thirdparty_provider_detail');
    const provider = currentThirdPartyProvider();
    if (!detailEl || !provider) return;
    const draft = thirdPartyProviderDraft(provider.id);
    const checked = draft.enabled ? 'checked' : '';
    const inputDisabled = draft.enabled ? '' : 'disabled';
    const validateDisabled = draft.enabled ? '' : 'disabled';
    const placeholder = draft.hasStoredKey ? 'Saved key - paste a new key to replace' : ('Enter ' + provider.name + ' API key');
    const switchClass = draft.enabled ? 'thirdparty-status-switch is-on' : 'thirdparty-status-switch';
    const switchState = draft.enabled ? 'true' : 'false';
    const switchLabel = draft.enabled ? 'Enabled' : 'Disabled';
    const infoHtml = thirdPartyInfoHtml(provider);

    detailEl.innerHTML = '' +
        '<div class="space-y-6 px-1">' +
            '<div class="api-key-info" style="margin-top:0">' + infoHtml + '</div>' +
            '<div class="flex items-center gap-3 flex-wrap">' +
                '<button type="button" id="' + escapeThirdPartyHtml(provider.id) + '_enabled_switch" class="' + switchClass + '" role="switch" aria-checked="' + switchState + '" aria-label="' + switchLabel + '">' +
                    '<span class="thirdparty-status-switch-track" aria-hidden="true"><span class="thirdparty-status-switch-knob"></span></span>' +
                '</button>' +
                '<input type="checkbox" id="' + escapeThirdPartyHtml(provider.id) + '_enabled" class="hidden" ' + checked + '>' +
                '<div class="relative flex-1 min-w-[18rem]">' +
                    '<input type="password" id="' + escapeThirdPartyHtml(provider.id) + '_key" value="' + escapeThirdPartyHtml(draft.key || '') + '" placeholder="' + escapeThirdPartyHtml(placeholder) + '" ' + inputDisabled + ' class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-4 py-2 pr-10 text-white focus:outline-none focus:border-[#f97316] transition-colors hover:border-[#7a7c8c] disabled:opacity-50">' +
                        '<button type="button" class="toggle-password-btn" onclick="togglePasswordVisibility(\'' + escapeThirdPartyHtml(provider.id) + '_key\')" title="Show password">' +
                            '<svg class="eye-icon eye-closed" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>' +
                            '<svg class="eye-icon eye-open hidden" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>' +
                        '</button>' +
                '</div>' +
                '<button type="button" id="' + escapeThirdPartyHtml(provider.id) + '_validate" class="px-4 py-2 rounded-md border border-white/15 bg-[#10183f] text-[#ececf1] hover:bg-white/10 transition-colors disabled:opacity-50 whitespace-nowrap" ' + validateDisabled + '>' + (provider.skipValidate ? 'Validate' : 'Validate') + '</button>' +
            '</div>' +
            '<div class="text-center pt-2">' +
                '<button type="button" id="' + escapeThirdPartyHtml(provider.id) + '_save" class="inline-flex items-center justify-center px-5 py-2 rounded-md bg-[#f97316] hover:bg-[#ea580c] text-white font-medium transition-colors">Save</button>' +
            '</div>' +
        '</div>';

    bindThirdPartyProviderDetail(provider);
}

function bindThirdPartyProviderDetail(provider) {
    const draft = thirdPartyProviderDraft(provider.id);
    const enabledEl = document.getElementById(provider.id + '_enabled');
    const enabledSwitchEl = document.getElementById(provider.id + '_enabled_switch');
    const keyEl = document.getElementById(provider.id + '_key');
    const validateBtn = document.getElementById(provider.id + '_validate');
    const saveBtn = document.getElementById(provider.id + '_save');
    if (enabledSwitchEl && enabledEl) {
        enabledSwitchEl.addEventListener('click', function () {
            enabledEl.checked = !enabledEl.checked;
            enabledEl.dispatchEvent(new Event('change'));
        });
    }
    if (enabledEl) {
        enabledEl.addEventListener('change', function () {
            draft.enabled = !!enabledEl.checked;
            if (!draft.enabled) {
                clearValidationIndicator(provider.id);
            }
            renderThirdPartyProviderList();
            renderThirdPartyProviderDetail();
        });
    }
    if (keyEl) {
        keyEl.addEventListener('input', function () {
            draft.key = keyEl.value || '';
            if ((draft.key || '').trim()) {
                delete validationStates[provider.id];
            }
            renderThirdPartyProviderList();
        });
    }
    if (validateBtn) {
        validateBtn.addEventListener('click', function () {
            if (provider.skipValidate) {
                setValidationIndicator(provider.id, 'valid', 'Ready to save');
                renderThirdPartyProviderList();
                renderThirdPartyProviderDetail();
                return;
            }
            validateProvider(provider.id);
        });
    }
    if (saveBtn) {
        saveBtn.addEventListener('click', function () {
            saveThirdPartySettings();
        });
    }
}

function syncSelectedThirdPartyProviderDraft() {
    const provider = currentThirdPartyProvider();
    if (!provider) return;
    const draft = thirdPartyProviderDraft(provider.id);
    const enabledEl = document.getElementById(provider.id + '_enabled');
    const keyEl = document.getElementById(provider.id + '_key');
    if (enabledEl) draft.enabled = !!enabledEl.checked;
    if (keyEl) draft.key = keyEl.value || '';
}

function buildThirdPartyPayload() {
    syncSelectedThirdPartyProviderDraft();
    const settings = {
        ollama_url: thirdPartyOllamaUrl || 'http://localhost:11434/'
    };
    PROVIDERS.forEach(function (provider) {
        const settingsKey = providerSettingsKey(provider);
        const draft = thirdPartyProviderDraft(provider.id);
        settings[settingsKey + '_enabled'] = !!draft.enabled;
        settings[settingsKey + '_' + provider.keyField] = draft.key || '';
    });
    return settings;
}

function selectThirdPartyConnectProvider(providerId) {
    selectedThirdPartyConnectProviderId = providerId;
    renderThirdPartyScreen();
}

function openThirdPartyConnectProvider(providerId) {
    selectedThirdPartyConnectProviderId = providerId;
    thirdPartyConnectDetailOpen = true;
    loadThirdPartyConnectStatus(false).then(function () {
        if (
            activeThirdPartySubtab === 'connect' &&
            thirdPartyConnectDetailOpen &&
            selectedThirdPartyConnectProviderId === providerId
        ) {
            renderThirdPartyScreen();
        }
    });
}

function closeThirdPartyConnectProvider() {
    resetThirdPartyTelegramPolling();
    resetThirdPartyWhatsAppPolling();
    thirdPartyConnectDetailOpen = false;
    renderThirdPartyScreen();
}

function loadThirdPartyConnectStatus(renderAfter) {
    return fetch(settingsBase + '/api/advanced/connection-status')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            thirdPartyConnectStatusCache = data || {};
            if (renderAfter !== false) {
                renderThirdPartyScreen();
            }
            return thirdPartyConnectStatusCache;
        })
        .catch(function () {
            if (renderAfter !== false) {
                renderThirdPartyScreen();
            }
            return thirdPartyConnectStatusCache;
        });
}

function disconnectThirdPartyGoogle() {
    const disconnectBtn = document.getElementById('thirdparty_connect_google_disconnect');
    if (!window.DecisionsAPI || typeof window.DecisionsAPI.confirm !== 'function') {
        if (typeof window.showNotification === 'function') {
            window.showNotification('The confirmation dialog is unavailable. Reload the page and try again.', 'error');
        }
        return Promise.resolve(false);
    }

    return window.DecisionsAPI.confirm({
        title: 'Disconnect Google',
        message: 'Disconnect Google? This removes the saved account tokens. You can reconnect later without uploading the OAuth configuration again.',
        confirmLabel: 'Disconnect',
        danger: true
    }).then(function (confirmed) {
        if (!confirmed) return false;
        if (disconnectBtn) {
            disconnectBtn.disabled = true;
            disconnectBtn.setAttribute('aria-busy', 'true');
            disconnectBtn.textContent = 'Disconnecting...';
        }
        return fetch(settingsBase + '/api/advanced/google/disconnect', { method: 'POST' })
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                    if (!response.ok || !data.success) {
                        throw new Error(data.error || 'Disconnect failed');
                    }
                    return data;
                });
            })
            .then(function () {
                return loadThirdPartyConnectStatus(false);
            })
            .then(function () {
                renderThirdPartyScreen();
                if (typeof window.updateConnectionStatus === 'function') {
                    window.updateConnectionStatus();
                }
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Google disconnected', 'success');
                }
                return true;
            })
            .catch(function (error) {
                if (disconnectBtn && document.contains(disconnectBtn)) {
                    disconnectBtn.disabled = false;
                    disconnectBtn.removeAttribute('aria-busy');
                    disconnectBtn.textContent = 'Disconnect';
                }
                if (typeof window.showNotification === 'function') {
                    window.showNotification(error.message || 'Disconnect failed', 'error');
                }
                return false;
            });
    });
}

function loadThirdPartyConnectorDraft(renderAfter) {
    return fetch(settingsBase + '/api/advanced/integration-connectors')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            thirdPartyConnectorDraft = data || {};
            if (renderAfter !== false && activeThirdPartySubtab === 'connect' && thirdPartyConnectDetailOpen) {
                renderThirdPartyConnectDetail();
            }
            return thirdPartyConnectorDraft;
        })
        .catch(function () {
            if (!thirdPartyConnectorDraft) {
                thirdPartyConnectorDraft = {};
            }
            return thirdPartyConnectorDraft;
        });
}

function renderThirdPartyConnectList() {
    const listEl = document.getElementById('thirdparty_connect_grid');
    if (!listEl) return;
    listEl.innerHTML = CONNECT_PROVIDERS.map(function (provider) {
        return '' +
            '<div class="relative w-full h-full text-center rounded-xl border p-6 transition-colors cursor-pointer ' + thirdPartyConnectCardStateClasses(provider.id) + '" role="button" tabindex="0" onclick="selectThirdPartyConnectProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" ondblclick="openThirdPartyConnectProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();selectThirdPartyConnectProvider(\'' + escapeThirdPartyHtml(provider.id) + '\');}">' +
                '<span role="button" tabindex="0" class="absolute top-3 right-3 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-[#565869] bg-[#0d1117] text-[#ececf1] shadow-sm transition-colors hover:border-[#f97316] hover:text-white" aria-label="Edit ' + escapeThirdPartyHtml(provider.name) + '" onclick="event.stopPropagation(); openThirdPartyConnectProvider(\'' + escapeThirdPartyHtml(provider.id) + '\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();event.stopPropagation();openThirdPartyConnectProvider(\'' + escapeThirdPartyHtml(provider.id) + '\');}">' +
                    '<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                        '<path d="m14.7 5.3 4 4"></path>' +
                        '<path d="M4 20l3.8-.8L19 8a2.8 2.8 0 1 0-4-4L3.8 15.2 3 19.9Z"></path>' +
                        '<path d="M13.5 6.5 17.5 10.5"></path>' +
                    '</svg>' +
                '</span>' +
                '<div class="flex h-full flex-col items-center justify-center gap-4">' +
                    '<div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-white/95 p-2 shadow-sm">' + thirdPartyIconSvg(provider, 'h-10 w-10') + '</div>' +
                    '<div class="min-w-0">' +
                        '<div class="text-base font-semibold leading-5 text-white break-words text-center">' + escapeThirdPartyHtml(provider.name) + '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }).join('');
}

function applyThirdPartyConnectDeepLink() {
    var query = new URLSearchParams(window.location.search || '');
    var requestedSubtab = (query.get('subtab') || '').toLowerCase();
    var requestedProvider = (query.get('provider') || '').toLowerCase();

    if (requestedSubtab === 'api_keys' || requestedSubtab === 'connect' || requestedSubtab === 'mcp') {
        activeThirdPartySubtab = requestedSubtab;
    }

    if (activeThirdPartySubtab === 'connect' && requestedProvider) {
        var normalizedProvider = requestedProvider.toLowerCase();
        if (getThirdPartyConnectProvider(normalizedProvider)) {
            selectedThirdPartyConnectProviderId = normalizedProvider;
            thirdPartyConnectDetailOpen = true;
        }
    }
}

function thirdPartyConnectDetailMarkup(provider) {
    if (!provider) return '';
    if (provider.id === 'google') {
        return '' +
            '<div class="space-y-6 px-1">' +
                thirdPartyConnectInfoHtml('Use Google OAuth to connect your Google Workspace account for Gmail, Calendar, Drive, Docs, and Sheets access.') +
                '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-4">' +
                    '<p class="text-sm ' + (thirdPartyConnectIsConnected('google') ? 'text-green-300' : 'text-gray-400') + '">Status: ' + escapeThirdPartyHtml(thirdPartyConnectStatusText('google')) + '</p>' +
                    '<div class="flex flex-wrap gap-3">' +
                        '<button type="button" id="thirdparty_connect_google_action" class="' + thirdPartyConnectActionButtonClasses() + '">' + (thirdPartyConnectIsConnected('google') ? 'Reconnect' : 'Connect') + '</button>' +
                        (thirdPartyConnectIsConnected('google') ? '<button type="button" id="thirdparty_connect_google_disconnect" class="' + thirdPartyConnectActionButtonClasses('danger') + '">Disconnect</button>' : '') +
                    '</div>' +
                '</div>' +
            '</div>';
    }
    if (provider.id === 'telegram') {
        return '' +
            '<div class="space-y-6 px-1">' +
                thirdPartyConnectInfoHtml('Scan the Telegram QR code with Telegram. Settings → Devices → Link Desktop Device.') +
                '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-4">' +
                    '<p id="thirdparty_telegram_status" class="text-sm text-gray-400 text-center">' + escapeThirdPartyHtml(thirdPartyConnectIsConnected('telegram') ? 'Telegram is connected.' : 'Telegram is not connected yet.') + '</p>' +
                    '<div id="thirdparty_telegram_qr_container" class="flex justify-center bg-white rounded-lg p-4 min-h-[220px] items-center"><p class="text-[#565869]">Choose Connect to request a QR code.</p></div>' +
                    '<a id="thirdparty_telegram_link" href="#" target="_blank" rel="noopener" class="hidden text-center text-[#0088cc] text-sm block">Open in Telegram</a>' +
                    '<div class="flex flex-wrap justify-center gap-3">' +
                        '<button type="button" id="thirdparty_telegram_action" class="' + thirdPartyConnectActionButtonClasses() + '">' + (thirdPartyConnectIsConnected('telegram') ? 'Refresh QR' : 'Connect') + '</button>' +
                        (thirdPartyConnectIsConnected('telegram') ? '<button type="button" id="thirdparty_telegram_disconnect" class="' + thirdPartyConnectActionButtonClasses('danger') + '">Disconnect</button>' : '') +
                    '</div>' +
                '</div>' +
            '</div>';
    }
    if (provider.id === 'whatsapp') {
        return '' +
            '<div class="space-y-6 px-1">' +
                thirdPartyConnectInfoHtml('Scan the WhatsApp QR code with WhatsApp. Settings → Linked Devices → Link a Device.') +
                '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-4">' +
                    '<p id="thirdparty_whatsapp_status" class="text-sm text-gray-400 text-center">Loading WhatsApp connection state…</p>' +
                    '<div id="thirdparty_whatsapp_qr_container" class="flex justify-center bg-white rounded-lg p-4 min-h-[220px] items-center"><p class="text-[#565869]">Loading QR code…</p></div>' +
                    '<p id="thirdparty_whatsapp_phone_info" class="hidden text-sm text-[#ececf1] text-center"></p>' +
                    '<div class="flex flex-wrap justify-center gap-3">' +
                        '<button type="button" id="thirdparty_whatsapp_action" class="' + thirdPartyConnectActionButtonClasses() + '">Refresh</button>' +
                        '<button type="button" id="thirdparty_whatsapp_disconnect" class="' + thirdPartyConnectActionButtonClasses('danger') + (thirdPartyConnectIsConnected('whatsapp') ? '' : ' hidden') + '">Disconnect</button>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }
    if (provider.id === 'jira') {
        return '' +
            '<div class="space-y-6 px-1">' +
                thirdPartyConnectInfoHtml('Add and manage Jira accounts here. Each saved account is validated before it becomes available.') +
                '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-5">' +
                    '<div id="thirdparty_jira_accounts" class="text-sm text-[#ececf1]">Loading accounts…</div>' +
                    '<div class="border-t border-white/10 pt-5 space-y-3">' +
                        '<div class="flex items-center justify-between gap-3"><h3 id="thirdparty_jira_form_title" class="text-sm font-semibold text-white">Add Jira Account</h3><button type="button" id="thirdparty_jira_reset" class="' + thirdPartyConnectActionButtonClasses('secondary') + '">New</button></div>' +
                        '<input type="text" id="thirdparty_jira_account_name" placeholder="Account name" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<input type="text" id="thirdparty_jira_server_url" placeholder="https://your-domain.atlassian.net" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<input type="text" id="thirdparty_jira_email" placeholder="your.email@example.com" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<input type="password" id="thirdparty_jira_api_token" placeholder="API token" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<p id="thirdparty_jira_form_status" class="text-sm min-h-[1.25rem] text-gray-400"></p>' +
                        '<div class="flex flex-wrap justify-center gap-3"><button type="button" id="thirdparty_jira_save" class="' + thirdPartyConnectActionButtonClasses() + '">Validate & Save</button></div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }
    if (provider.id === 'trello') {
        return '' +
            '<div class="space-y-6 px-1">' +
                thirdPartyConnectInfoHtml('Add and manage Trello accounts here. Generate the token from Trello, then validate and save it.') +
                '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-5">' +
                    '<div id="thirdparty_trello_accounts" class="text-sm text-[#ececf1]">Loading accounts…</div>' +
                    '<div class="border-t border-white/10 pt-5 space-y-3">' +
                        '<div class="flex items-center justify-between gap-3"><h3 id="thirdparty_trello_form_title" class="text-sm font-semibold text-white">Add Trello Account</h3><button type="button" id="thirdparty_trello_reset" class="' + thirdPartyConnectActionButtonClasses('secondary') + '">New</button></div>' +
                        '<input type="text" id="thirdparty_trello_account_name" placeholder="Account name" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<input type="text" id="thirdparty_trello_api_key" placeholder="API key" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                        '<div class="flex flex-wrap gap-3">' +
                            '<input type="password" id="thirdparty_trello_api_token" placeholder="API token" class="min-w-[16rem] flex-1 bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' +
                            '<button type="button" id="thirdparty_trello_generate" class="' + thirdPartyConnectActionButtonClasses('secondary') + '">Generate Token</button>' +
                        '</div>' +
                        '<p id="thirdparty_trello_form_status" class="text-sm min-h-[1.25rem] text-gray-400"></p>' +
                        '<div class="flex flex-wrap justify-center gap-3"><button type="button" id="thirdparty_trello_save" class="' + thirdPartyConnectActionButtonClasses() + '">Validate & Save</button></div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }
    const isSlack = provider.id === 'slack';
    const isDiscord = provider.id === 'discord';
    const isClickUp = provider.id === 'clickup';
    const isMonday = provider.id === 'monday';
    return '' +
        '<div class="space-y-6 px-1">' +
            thirdPartyConnectInfoHtml(
                isDiscord
                    ? 'Save the Discord bot token here. Restart the DecisionsAI desktop app after changing the token so the bot reconnects.'
                    : isSlack
                        ? 'Save the Slack bot token and signing secret here. Environment variables override saved values when they are set.'
                        : isClickUp
                            ? 'Save the ClickUp API token used for connected work sources and initiative workflows.'
                            : 'Save the Monday API token used for connected boards and initiative workflows.'
            ) +
            '<div class="rounded-xl border border-[#565869] bg-[#0d1117] p-5 space-y-4">' +
                '<p class="text-sm ' + (thirdPartyConnectIsConnected(provider.id) ? 'text-green-300' : 'text-gray-400') + '">Status: ' + escapeThirdPartyHtml(thirdPartyConnectStatusText(provider.id)) + '</p>' +
                (isDiscord ? '<input type="password" id="thirdparty_discord_bot_token" placeholder="Discord bot token" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' : '') +
                (isSlack ? '<div class="space-y-3"><input type="password" id="thirdparty_slack_bot_token" placeholder="Slack bot token" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm"><input type="password" id="thirdparty_slack_signing_secret" placeholder="Slack signing secret" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm"></div>' : '') +
                (isClickUp ? '<input type="password" id="thirdparty_clickup_api_token" placeholder="ClickUp API token" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' : '') +
                (isMonday ? '<input type="password" id="thirdparty_monday_api_token" placeholder="Monday API token" class="w-full bg-[#1a1f3a] border border-[#565869] rounded-md px-3 py-2 text-white text-sm">' : '') +
                '<p id="thirdparty_connector_status" class="text-sm min-h-[1.25rem] text-gray-400"></p>' +
                '<div class="flex flex-wrap justify-center gap-3">' +
                    '<button type="button" id="thirdparty_connector_save" class="' + thirdPartyConnectActionButtonClasses() + '">Save</button>' +
                    (thirdPartyConnectorHasStoredConfig(provider.id)
                        ? '<button type="button" id="thirdparty_connector_disconnect" class="' + thirdPartyConnectActionButtonClasses('danger') + '">Disconnect</button>'
                        : '') +
                '</div>' +
            '</div>' +
        '</div>';
}

function renderThirdPartyConnectDetail() {
    const detailEl = document.getElementById('thirdparty_connect_detail');
    const provider = currentThirdPartyConnectProvider();
    if (!detailEl || !provider) return;
    detailEl.innerHTML = thirdPartyConnectDetailMarkup(provider);
    bindThirdPartyConnectDetail(provider);
}

function bindThirdPartyConnectDetail(provider) {
    if (!provider) return;
    if (provider.id === 'google') {
        const actionBtn = document.getElementById('thirdparty_connect_google_action');
        const disconnectBtn = document.getElementById('thirdparty_connect_google_disconnect');
        if (actionBtn) actionBtn.addEventListener('click', function () { window.connectGoogle(); });
        if (disconnectBtn) disconnectBtn.addEventListener('click', disconnectThirdPartyGoogle);
        return;
    }
    if (provider.id === 'telegram') {
        const actionBtn = document.getElementById('thirdparty_telegram_action');
        const disconnectBtn = document.getElementById('thirdparty_telegram_disconnect');
        if (actionBtn) actionBtn.addEventListener('click', function () { startThirdPartyTelegramConnect(); });
        if (disconnectBtn) disconnectBtn.addEventListener('click', function () { window.disconnectTelegramDirect(); });
        return;
    }
    if (provider.id === 'whatsapp') {
        const actionBtn = document.getElementById('thirdparty_whatsapp_action');
        const disconnectBtn = document.getElementById('thirdparty_whatsapp_disconnect');
        if (actionBtn) actionBtn.addEventListener('click', function () { loadThirdPartyWhatsAppInline(); });
        if (disconnectBtn) disconnectBtn.addEventListener('click', function () { window.disconnectWhatsAppDirect(); });
        loadThirdPartyWhatsAppInline();
        return;
    }
    if (provider.id === 'jira') {
        const resetBtn = document.getElementById('thirdparty_jira_reset');
        const saveBtn = document.getElementById('thirdparty_jira_save');
        if (resetBtn) resetBtn.addEventListener('click', function () { populateThirdPartyJiraForm(); });
        if (saveBtn) saveBtn.addEventListener('click', function () { saveThirdPartyJiraAccountInline(); });
        loadThirdPartyJiraAccountsInline();
        return;
    }
    if (provider.id === 'trello') {
        const resetBtn = document.getElementById('thirdparty_trello_reset');
        const saveBtn = document.getElementById('thirdparty_trello_save');
        const generateBtn = document.getElementById('thirdparty_trello_generate');
        if (resetBtn) resetBtn.addEventListener('click', function () { populateThirdPartyTrelloForm(); });
        if (saveBtn) saveBtn.addEventListener('click', function () { saveThirdPartyTrelloAccountInline(); });
        if (generateBtn) generateBtn.addEventListener('click', function () { openThirdPartyTrelloTokenGenerator(); });
        loadThirdPartyTrelloAccountsInline();
        return;
    }
    loadThirdPartyConnectorDraft(false).then(function (draft) {
        if (provider.id === 'discord') {
            const input = document.getElementById('thirdparty_discord_bot_token');
            if (input) input.value = draft.discord_bot_token || '';
        } else if (provider.id === 'slack') {
            const botInput = document.getElementById('thirdparty_slack_bot_token');
            const secretInput = document.getElementById('thirdparty_slack_signing_secret');
            if (botInput) botInput.value = draft.slack_bot_token || '';
            if (secretInput) secretInput.value = draft.slack_signing_secret || '';
        } else if (provider.id === 'clickup') {
            const input = document.getElementById('thirdparty_clickup_api_token');
            if (input) input.value = draft.clickup_api_token || '';
        } else if (provider.id === 'monday') {
            const input = document.getElementById('thirdparty_monday_api_token');
            if (input) input.value = draft.monday_api_token || '';
        }
    });
    const saveBtn = document.getElementById('thirdparty_connector_save');
    const disconnectBtn = document.getElementById('thirdparty_connector_disconnect');
    if (saveBtn) saveBtn.addEventListener('click', function () { saveThirdPartyConnectorInline(provider.id); });
    if (disconnectBtn) disconnectBtn.addEventListener('click', function () { disconnectThirdPartyConnectorInline(provider.id); });
}

function startThirdPartyTelegramConnect() {
    const qrContainer = document.getElementById('thirdparty_telegram_qr_container');
    const statusEl = document.getElementById('thirdparty_telegram_status');
    const linkEl = document.getElementById('thirdparty_telegram_link');
    if (qrContainer) qrContainer.innerHTML = '<p class="text-[#565869]">Loading QR…</p>';
    if (statusEl) {
        statusEl.textContent = 'Requesting QR code…';
        statusEl.className = 'text-sm text-[#9ca3af] text-center';
    }
    if (linkEl) {
        linkEl.classList.add('hidden');
        linkEl.removeAttribute('href');
    }
    resetThirdPartyTelegramPolling();
    fetch(settingsBase + '/api/advanced/telegram/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
    }).then(function (response) { return response.json(); }).then(function (data) {
        if (data.error) {
            if (statusEl) {
                statusEl.textContent = data.error;
                statusEl.className = 'text-sm text-red-400 text-center';
            }
            return;
        }
        thirdPartyTelegramSession = {
            token: data.token,
            appUserId: data.app_user_id
        };
        if (data.qr_code && qrContainer) {
            const img = document.createElement('img');
            img.alt = 'Telegram QR';
            img.className = 'max-w-[220px] max-h-[220px]';
            img.src = data.qr_code.indexOf('data:image') === 0 ? data.qr_code : ('data:image/png;base64,' + data.qr_code);
            qrContainer.innerHTML = '';
            qrContainer.appendChild(img);
        }
        if (statusEl) {
            statusEl.textContent = 'Scan the QR code with your Telegram app.';
            statusEl.className = 'text-sm text-green-400 text-center';
        }
        if (data.link && linkEl) {
            linkEl.href = data.link;
            linkEl.textContent = 'Open in Telegram';
            linkEl.classList.remove('hidden');
        }
        thirdPartyTelegramPollInterval = setInterval(function () {
            if (!thirdPartyTelegramSession || !thirdPartyTelegramSession.token) return;
            fetch(settingsBase + '/api/advanced/telegram/status', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: thirdPartyTelegramSession.token })
            }).then(function (response) { return response.json(); }).then(function (status) {
                if (status.status !== 'connected') return;
                resetThirdPartyTelegramPolling();
                return fetch(settingsBase + '/api/advanced/telegram/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        token: thirdPartyTelegramSession.token,
                        app_user_id: thirdPartyTelegramSession.appUserId,
                        user_id: status.user_id
                    })
                }).then(function () {
                    if (statusEl) {
                        statusEl.textContent = 'Telegram connected successfully.';
                        statusEl.className = 'text-sm text-green-400 text-center';
                    }
                    loadThirdPartyConnectStatus();
                    if (typeof window.showNotification === 'function') window.showNotification('Telegram connected', 'success');
                });
            }).catch(function () {});
        }, 2000);
    }).catch(function () {
        if (statusEl) {
            statusEl.textContent = 'Failed to request Telegram QR code.';
            statusEl.className = 'text-sm text-red-400 text-center';
        }
    });
}

function loadThirdPartyWhatsAppInline() {
    const qrContainer = document.getElementById('thirdparty_whatsapp_qr_container');
    const statusEl = document.getElementById('thirdparty_whatsapp_status');
    const phoneInfoEl = document.getElementById('thirdparty_whatsapp_phone_info');
    const disconnectBtn = document.getElementById('thirdparty_whatsapp_disconnect');
    if (qrContainer) qrContainer.innerHTML = '<p class="text-[#565869]">Loading QR code…</p>';
    if (statusEl) {
        statusEl.textContent = 'Loading WhatsApp connection state…';
        statusEl.className = 'text-sm text-[#9ca3af] text-center';
    }
    if (phoneInfoEl) {
        phoneInfoEl.textContent = '';
        phoneInfoEl.classList.add('hidden');
    }
    resetThirdPartyWhatsAppPolling();

    function renderWhatsAppState(data) {
        data = data || {};
        if (data.status === 'service_down' || data.status === 'error') {
            if (statusEl) {
                statusEl.textContent = data.error || 'WhatsApp service is unavailable.';
                statusEl.className = 'text-sm text-red-400 text-center';
            }
            if (qrContainer) {
                qrContainer.innerHTML = '<div class="text-center"><p class="text-red-500">Connection unavailable</p><p class="text-sm text-[#565869] mt-2">Check the WhatsApp relay, then press Refresh.</p></div>';
            }
            resetThirdPartyWhatsAppPolling();
            return 'error';
        }
        if (data.status === 'connected' && data.phone) {
            if (qrContainer) qrContainer.innerHTML = '<p class="text-green-500 text-lg">✓ Connected</p>';
            if (statusEl) {
                statusEl.textContent = 'WhatsApp is connected.';
                statusEl.className = 'text-sm text-green-400 text-center';
            }
            if (phoneInfoEl) {
                phoneInfoEl.textContent = data.phone.name || data.phone.jid || 'Unknown';
                phoneInfoEl.classList.remove('hidden');
            }
            if (disconnectBtn) disconnectBtn.classList.remove('hidden');
            return 'connected';
        }
        if (data.qr_image && qrContainer) {
            const img = document.createElement('img');
            img.src = data.qr_image;
            img.alt = 'WhatsApp QR';
            img.className = 'max-w-[256px] max-h-[256px] rounded';
            qrContainer.innerHTML = '';
            qrContainer.appendChild(img);
            if (statusEl) {
                statusEl.textContent = 'Scan with WhatsApp: Settings → Linked Devices → Link a Device';
                statusEl.className = 'text-sm text-green-400 text-center';
            }
        } else if (data.qr_code && qrContainer) {
            qrContainer.innerHTML = '<div class="text-center"><p class="text-sm text-[#ececf1] mb-2">Enter this code in WhatsApp:</p><div class="bg-white p-3 rounded inline-block"><p class="text-xs text-black break-all font-mono" style="max-width:250px;word-break:break-all;">' + escapeThirdPartyHtml(data.qr_code) + '</p></div></div>';
            if (statusEl) {
                statusEl.textContent = 'Scan with WhatsApp: Settings → Linked Devices → Link a Device';
                statusEl.className = 'text-sm text-green-400 text-center';
            }
        } else {
            if (qrContainer) qrContainer.innerHTML = '<p class="text-[#565869]">Starting WhatsApp pairing…</p>';
            if (statusEl) {
                statusEl.textContent = data.status === 'disconnected'
                    ? 'WhatsApp is reconnecting. Waiting for a fresh QR code…'
                    : 'Starting WhatsApp and requesting a QR code…';
                statusEl.className = 'text-sm text-yellow-400 text-center';
            }
        }
        if (disconnectBtn) disconnectBtn.classList.add('hidden');
        return data.qr_image || data.qr_code ? 'qr_ready' : 'starting';
    }

    function saveConnectedWhatsApp(status) {
        resetThirdPartyWhatsAppPolling();
        return fetch(settingsBase + '/api/advanced/whatsapp/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jid: status.phone.jid || '',
                name: status.phone.name || '',
                push_name: status.phone.pushName || status.phone.notify || ''
            })
        }).then(function () {
            renderWhatsAppState(status);
            if (statusEl) statusEl.textContent = 'WhatsApp connected successfully.';
            loadThirdPartyConnectStatus(false);
            if (typeof window.showNotification === 'function') window.showNotification('WhatsApp connected', 'success');
        });
    }

    function pollWhatsAppState() {
        fetch(settingsBase + '/api/advanced/whatsapp/status').then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok && !data.status) data.status = 'error';
                return data;
            });
        }).then(function (status) {
            const state = renderWhatsAppState(status);
            if (state === 'connected') saveConnectedWhatsApp(status);
        }).catch(function () {
            renderWhatsAppState({ status: 'error', error: 'Failed to read WhatsApp connection status.' });
        });
    }

    fetch(settingsBase + '/api/advanced/whatsapp/connect', { method: 'POST' }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
            if (!response.ok && !data.status) data.status = 'error';
            return data;
        });
    }).then(function (data) {
        const state = renderWhatsAppState(data);
        if (state === 'connected') {
            loadThirdPartyConnectStatus(false);
            return;
        }
        if (state === 'error') return;
        thirdPartyWhatsAppPollInterval = setInterval(pollWhatsAppState, 2000);
        pollWhatsAppState();
    }).catch(function () {
        if (statusEl) {
            renderWhatsAppState({ status: 'error', error: 'Failed to start WhatsApp pairing.' });
        }
    });
}

function renderThirdPartyAccountsTable(accounts, columns, editClass, deleteClass) {
    if (!accounts.length) {
        return '<p class="text-[#9ca3af]">No saved accounts yet.</p>';
    }
    return '<table class="w-full text-sm"><thead><tr class="border-b border-[#565869]">' +
        columns.map(function (column) {
            return '<th class="text-left py-2 text-[#9ca3af]">' + escapeThirdPartyHtml(column.label) + '</th>';
        }).join('') +
        '<th></th></tr></thead><tbody>' +
        accounts.map(function (account) {
            return '<tr class="border-b border-[#565869]">' +
                columns.map(function (column) {
                    const value = typeof column.render === 'function' ? column.render(account) : account[column.key];
                    return '<td class="py-2 ' + (column.muted ? 'text-[#9ca3af]' : '') + '">' + escapeThirdPartyHtml(value || '') + '</td>';
                }).join('') +
                '<td class="py-2 text-right"><button type="button" class="' + editClass + ' px-2 py-1 bg-[#0088cc] hover:bg-[#0077b3] text-white rounded text-xs mr-1" data-name="' + escapeThirdPartyHtml(account.name || '') + '">Edit</button><button type="button" class="' + deleteClass + ' px-2 py-1 bg-[#ff6b6b] hover:bg-[#ff5252] text-white rounded text-xs" data-name="' + escapeThirdPartyHtml(account.name || '') + '">Delete</button></td>' +
            '</tr>';
        }).join('') +
        '</tbody></table>';
}

function populateThirdPartyJiraForm(account) {
    thirdPartyJiraEditingName = account && account.name ? account.name : null;
    const title = document.getElementById('thirdparty_jira_form_title');
    const nameEl = document.getElementById('thirdparty_jira_account_name');
    const serverEl = document.getElementById('thirdparty_jira_server_url');
    const emailEl = document.getElementById('thirdparty_jira_email');
    const tokenEl = document.getElementById('thirdparty_jira_api_token');
    const statusEl = document.getElementById('thirdparty_jira_form_status');
    if (title) title.textContent = thirdPartyJiraEditingName ? 'Edit Jira Account' : 'Add Jira Account';
    if (nameEl) nameEl.value = account && account.name ? account.name : 'Jira Account';
    if (serverEl) serverEl.value = account && account.server_url ? account.server_url : '';
    if (emailEl) emailEl.value = '';
    if (tokenEl) tokenEl.value = '';
    if (statusEl) {
        statusEl.textContent = thirdPartyJiraEditingName ? 'Re-enter email and API token to update this account.' : '';
        statusEl.className = 'text-sm min-h-[1.25rem] text-gray-400';
    }
}

function loadThirdPartyJiraAccountsInline() {
    const listEl = document.getElementById('thirdparty_jira_accounts');
    if (!listEl) return;
    listEl.innerHTML = '<p class="text-[#9ca3af]">Loading accounts…</p>';
    fetch(settingsBase + '/api/advanced/accounts?provider=jira').then(function (response) { return response.json(); }).then(function (data) {
        thirdPartyJiraAccountsCache = data.accounts || [];
        listEl.innerHTML = renderThirdPartyAccountsTable(thirdPartyJiraAccountsCache, [
            { key: 'name', label: 'Name' },
            { key: 'server_url', label: 'Server URL', muted: true },
            { label: 'Status', render: function (account) { return account.is_valid ? 'Valid' : 'Invalid'; } }
        ], 'thirdparty-jira-edit', 'thirdparty-jira-delete');
        listEl.querySelectorAll('.thirdparty-jira-edit').forEach(function (button) {
            button.addEventListener('click', function () {
                const name = button.getAttribute('data-name');
                const account = thirdPartyJiraAccountsCache.find(function (entry) { return entry.name === name; });
                populateThirdPartyJiraForm(account || null);
            });
        });
        listEl.querySelectorAll('.thirdparty-jira-delete').forEach(function (button) {
            button.addEventListener('click', function () {
                const name = button.getAttribute('data-name') || '';
                window.DecisionsAPI.confirm({
                    title: 'Delete Jira account',
                    message: 'Delete Jira account "' + name + '"?',
                    confirmLabel: 'Delete',
                    danger: true,
                    onConfirm: function () {
                        fetch(settingsBase + '/api/advanced/accounts', {
                            method: 'DELETE',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ provider: 'jira', name: name })
                        }).then(function () {
                            loadThirdPartyJiraAccountsInline();
                            loadThirdPartyConnectStatus();
                        });
                    }
                });
            });
        });
        if (!thirdPartyJiraEditingName) {
            populateThirdPartyJiraForm();
        }
    }).catch(function () {
        listEl.innerHTML = '<p class="text-red-400">Failed to load Jira accounts.</p>';
    });
}

function saveThirdPartyJiraAccountInline() {
    const name = ((document.getElementById('thirdparty_jira_account_name') || {}).value || '').trim();
    const serverUrl = ((document.getElementById('thirdparty_jira_server_url') || {}).value || '').trim();
    const email = ((document.getElementById('thirdparty_jira_email') || {}).value || '').trim();
    const apiToken = ((document.getElementById('thirdparty_jira_api_token') || {}).value || '').trim();
    const statusEl = document.getElementById('thirdparty_jira_form_status');
    if (!name || !serverUrl || !email || !apiToken) {
        if (statusEl) {
            statusEl.textContent = 'Please fill in all fields.';
            statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
        }
        return;
    }
    if (statusEl) {
        statusEl.textContent = 'Validating…';
        statusEl.className = 'text-sm min-h-[1.25rem] text-[#9ca3af]';
    }
    fetch(settingsBase + '/api/advanced/validate/jira', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_url: serverUrl, email: email, api_token: apiToken })
    }).then(function (response) { return response.json(); }).then(function (result) {
        if (!result.valid) {
            if (statusEl) {
                statusEl.textContent = result.error || 'Validation failed';
                statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
            }
            return;
        }
        const payload = {
            provider: 'jira',
            name: name,
            server_url: serverUrl,
            email: email,
            api_token: apiToken
        };
        if (thirdPartyJiraEditingName) payload.original_name = thirdPartyJiraEditingName;
        return fetch(settingsBase + '/api/advanced/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (response) { return response.json(); }).then(function (saveResult) {
            if (!saveResult.success) {
                if (statusEl) {
                    statusEl.textContent = saveResult.error || 'Save failed';
                    statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
                }
                return;
            }
            if (statusEl) {
                statusEl.textContent = 'Saved.';
                statusEl.className = 'text-sm min-h-[1.25rem] text-green-400';
            }
            populateThirdPartyJiraForm();
            loadThirdPartyJiraAccountsInline();
            loadThirdPartyConnectStatus();
            if (typeof window.showNotification === 'function') window.showNotification('Jira account saved', 'success');
        });
    }).catch(function () {
        if (statusEl) {
            statusEl.textContent = 'Request failed';
            statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
        }
    });
}

function populateThirdPartyTrelloForm(account) {
    thirdPartyTrelloEditingName = account && account.name ? account.name : null;
    const title = document.getElementById('thirdparty_trello_form_title');
    const nameEl = document.getElementById('thirdparty_trello_account_name');
    const keyEl = document.getElementById('thirdparty_trello_api_key');
    const tokenEl = document.getElementById('thirdparty_trello_api_token');
    const statusEl = document.getElementById('thirdparty_trello_form_status');
    if (title) title.textContent = thirdPartyTrelloEditingName ? 'Edit Trello Account' : 'Add Trello Account';
    if (nameEl) nameEl.value = account && account.name ? account.name : 'Trello Account';
    if (keyEl) keyEl.value = '';
    if (tokenEl) tokenEl.value = '';
    if (statusEl) {
        statusEl.textContent = thirdPartyTrelloEditingName ? 'Re-enter the API key and token to update this account.' : '';
        statusEl.className = 'text-sm min-h-[1.25rem] text-gray-400';
    }
}

function loadThirdPartyTrelloAccountsInline() {
    const listEl = document.getElementById('thirdparty_trello_accounts');
    if (!listEl) return;
    listEl.innerHTML = '<p class="text-[#9ca3af]">Loading accounts…</p>';
    fetch(settingsBase + '/api/advanced/accounts?provider=trello').then(function (response) { return response.json(); }).then(function (data) {
        thirdPartyTrelloAccountsCache = data.accounts || [];
        listEl.innerHTML = renderThirdPartyAccountsTable(thirdPartyTrelloAccountsCache, [
            { key: 'name', label: 'Name' },
            { key: 'api_key_masked', label: 'API Key', muted: true },
            { label: 'Status', render: function (account) { return account.is_valid ? 'Valid' : 'Invalid'; } }
        ], 'thirdparty-trello-edit', 'thirdparty-trello-delete');
        listEl.querySelectorAll('.thirdparty-trello-edit').forEach(function (button) {
            button.addEventListener('click', function () {
                const name = button.getAttribute('data-name');
                const account = thirdPartyTrelloAccountsCache.find(function (entry) { return entry.name === name; });
                populateThirdPartyTrelloForm(account || null);
            });
        });
        listEl.querySelectorAll('.thirdparty-trello-delete').forEach(function (button) {
            button.addEventListener('click', function () {
                const name = button.getAttribute('data-name') || '';
                window.DecisionsAPI.confirm({
                    title: 'Delete Trello account',
                    message: 'Delete Trello account "' + name + '"?',
                    confirmLabel: 'Delete',
                    danger: true,
                    onConfirm: function () {
                        fetch(settingsBase + '/api/advanced/accounts', {
                            method: 'DELETE',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ provider: 'trello', name: name })
                        }).then(function () {
                            loadThirdPartyTrelloAccountsInline();
                            loadThirdPartyConnectStatus();
                        });
                    }
                });
            });
        });
        if (!thirdPartyTrelloEditingName) {
            populateThirdPartyTrelloForm();
        }
    }).catch(function () {
        listEl.innerHTML = '<p class="text-red-400">Failed to load Trello accounts.</p>';
    });
}

function openThirdPartyTrelloTokenGenerator() {
    const apiKey = ((document.getElementById('thirdparty_trello_api_key') || {}).value || '').trim();
    if (!apiKey) {
        if (typeof window.showNotification === 'function') window.showNotification('Enter the API key first', 'warning');
        return;
    }
    fetch(settingsBase + '/api/advanced/trello/auth-url?api_key=' + encodeURIComponent(apiKey))
        .then(function (response) { return response.json(); })
        .then(function (data) {
            if (data.url) window.open(data.url);
            if (typeof window.showNotification === 'function') window.showNotification('Approve in Trello, then paste the token here.', 'info');
        });
}

function saveThirdPartyTrelloAccountInline() {
    const name = ((document.getElementById('thirdparty_trello_account_name') || {}).value || '').trim();
    const apiKey = ((document.getElementById('thirdparty_trello_api_key') || {}).value || '').trim();
    const apiToken = ((document.getElementById('thirdparty_trello_api_token') || {}).value || '').trim();
    const statusEl = document.getElementById('thirdparty_trello_form_status');
    if (!name || !apiKey || !apiToken) {
        if (statusEl) {
            statusEl.textContent = 'Please fill in all fields.';
            statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
        }
        return;
    }
    if (statusEl) {
        statusEl.textContent = 'Validating…';
        statusEl.className = 'text-sm min-h-[1.25rem] text-[#9ca3af]';
    }
    fetch(settingsBase + '/api/advanced/validate/trello', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey, api_token: apiToken })
    }).then(function (response) { return response.json(); }).then(function (result) {
        if (!result.valid) {
            if (statusEl) {
                statusEl.textContent = result.error || 'Validation failed';
                statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
            }
            return;
        }
        const payload = {
            provider: 'trello',
            name: name,
            api_key: apiKey,
            api_token: apiToken
        };
        if (thirdPartyTrelloEditingName) payload.original_name = thirdPartyTrelloEditingName;
        return fetch(settingsBase + '/api/advanced/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (response) { return response.json(); }).then(function (saveResult) {
            if (!saveResult.success) {
                if (statusEl) {
                    statusEl.textContent = saveResult.error || 'Save failed';
                    statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
                }
                return;
            }
            if (statusEl) {
                statusEl.textContent = 'Saved.';
                statusEl.className = 'text-sm min-h-[1.25rem] text-green-400';
            }
            populateThirdPartyTrelloForm();
            loadThirdPartyTrelloAccountsInline();
            loadThirdPartyConnectStatus();
            if (typeof window.showNotification === 'function') window.showNotification('Trello account saved', 'success');
        });
    }).catch(function () {
        if (statusEl) {
            statusEl.textContent = 'Request failed';
            statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
        }
    });
}

function saveThirdPartyConnectorInline(providerId) {
    const statusEl = document.getElementById('thirdparty_connector_status');
    const payload = {};
    if (providerId === 'discord') {
        payload.discord_bot_token = ((document.getElementById('thirdparty_discord_bot_token') || {}).value || '').trim();
    } else if (providerId === 'slack') {
        payload.slack_bot_token = ((document.getElementById('thirdparty_slack_bot_token') || {}).value || '').trim();
        payload.slack_signing_secret = ((document.getElementById('thirdparty_slack_signing_secret') || {}).value || '').trim();
    } else if (providerId === 'clickup') {
        payload.clickup_api_token = ((document.getElementById('thirdparty_clickup_api_token') || {}).value || '').trim();
    } else if (providerId === 'monday') {
        payload.monday_api_token = ((document.getElementById('thirdparty_monday_api_token') || {}).value || '').trim();
    }
    if (statusEl) {
        statusEl.textContent = 'Saving…';
        statusEl.className = 'text-sm min-h-[1.25rem] text-[#9ca3af]';
    }
    fetch(settingsBase + '/api/advanced/integration-connectors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(function (response) { return response.json(); }).then(function (result) {
        if (statusEl) {
            statusEl.textContent = result.restart_note || 'Saved.';
            statusEl.className = 'text-sm min-h-[1.25rem] text-green-400';
        }
        loadThirdPartyConnectorDraft(false);
        loadThirdPartyConnectStatus();
        if (typeof window.showNotification === 'function') window.showNotification('Connector settings saved', 'success');
    }).catch(function () {
        if (statusEl) {
            statusEl.textContent = 'Save failed';
            statusEl.className = 'text-sm min-h-[1.25rem] text-red-400';
        }
    });
}

function disconnectThirdPartyConnectorInline(providerId) {
    const labels = {
        discord: 'Discord',
        slack: 'Slack',
        clickup: 'ClickUp',
        monday: 'Monday'
    };
    window.disconnectConnectorProvider(providerId, labels[providerId] || providerId);
}

async function loadThirdPartySettings() {
    try {
        const response = await fetch('/api/thirdparty');
        if (!response.ok) throw new Error('Failed to load settings');
        const settings = await response.json();
        thirdPartyOllamaUrl = settings.ollama_url || 'http://localhost:11434/';
        PROVIDERS.forEach(function (provider) {
            const settingsKey = providerSettingsKey(provider);
            thirdPartyDrafts[provider.id] = {
                enabled: !!settings[settingsKey + '_enabled'],
                key: '',
                hasStoredKey: !!settings[settingsKey + '_' + provider.keyField + '_set']
            };
            if (thirdPartyDrafts[provider.id].enabled && thirdPartyDrafts[provider.id].hasStoredKey) {
                setValidationIndicator(provider.id, 'valid', 'Stored key');
            } else {
                clearValidationIndicator(provider.id);
            }
        });
        if (!getThirdPartyProvider(selectedThirdPartyProviderId)) {
            selectedThirdPartyProviderId = PROVIDERS[0].id;
        }
        renderThirdPartyProviderList();
        renderThirdPartyScreen();
    } catch (error) {
        console.error('Error loading settings:', error);
        showNotification('Failed to load settings: ' + error.message, 'error');
    }
}

async function saveThirdPartySettings(options) {
    options = options || {};
    const silent = options.silent === true;
    try {
        syncSelectedThirdPartyProviderDraft();
        const invalidProviders = [];
        PROVIDERS.forEach(function (provider) {
            const draft = thirdPartyProviderDraft(provider.id);
            if (!draft.enabled) return;
            if (provider.skipValidate) return;
            if ((draft.key || '').trim() && validationStates[provider.id] !== 'valid') {
                invalidProviders.push(provider.name);
            }
        });
        if (invalidProviders.length) {
            if (!silent) {
                showNotification('Validate these providers before saving: ' + invalidProviders.join(', '), 'error');
            }
            return false;
        }

        const response = await fetch('/api/thirdparty', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildThirdPartyPayload())
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save settings');
        }
        await response.json();

        PROVIDERS.forEach(function (provider) {
            const draft = thirdPartyProviderDraft(provider.id);
            if ((draft.key || '').trim()) {
                draft.hasStoredKey = true;
                draft.key = '';
            }
            if (draft.enabled && draft.hasStoredKey && validationStates[provider.id] !== 'invalid') {
                setValidationIndicator(provider.id, 'valid', 'Stored key');
            }
        });

        renderThirdPartyScreen();
        if (!silent) showNotification('API key settings saved successfully', 'success');

        window.dispatchEvent(new CustomEvent('thirdparty-providers-changed'));
        try {
            const bc = new BroadcastChannel('providers-changed');
            bc.postMessage({ type: 'thirdparty-providers-changed' });
            bc.close();
        } catch (_) {}
        return true;
    } catch (error) {
        console.error('Error saving settings:', error);
        if (!silent) showNotification('Failed to save settings: ' + error.message, 'error');
        return false;
    }
}

async function validateProvider(providerId) {
    const provider = getThirdPartyProvider(providerId);
    if (!provider) return;
    syncSelectedThirdPartyProviderDraft();
    const draft = thirdPartyProviderDraft(providerId);
    const key = (draft.key || '').trim();
    if (!key) {
        if (draft.hasStoredKey) {
            setValidationIndicator(providerId, 'valid', 'Stored key');
        } else {
            setValidationIndicator(providerId, 'invalid', 'Paste a key first');
        }
        renderThirdPartyScreen();
        return;
    }

    setValidationIndicator(providerId, 'validating', 'Validating...');
    renderThirdPartyScreen();

    try {
        const response = await fetch('/api/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: providerId, key: key })
        });
        const result = await response.json();
        if (result.valid) {
            setValidationIndicator(providerId, 'valid', 'Validated');
            showNotification(provider.name + ' key validated', 'success');
        } else {
            setValidationIndicator(providerId, 'invalid', result.error || 'Invalid key');
        }
    } catch (error) {
        console.error('Validation error for ' + providerId + ':', error);
        setValidationIndicator(providerId, 'invalid', 'Validation failed');
    }

    renderThirdPartyScreen();
}

function setValidationIndicator(providerId, state, tooltip) {
    validationStates[providerId] = state;
    if (tooltip) validationStates[providerId + ':tooltip'] = tooltip;
}

function clearValidationIndicator(providerId) {
    delete validationStates[providerId];
    delete validationStates[providerId + ':tooltip'];
}

function toggleProviderInput(providerId, enabled) {
    const draft = thirdPartyProviderDraft(providerId);
    draft.enabled = !!enabled;
    renderThirdPartyScreen();
}

function selectThirdPartyProvider(providerId) {
    syncSelectedThirdPartyProviderDraft();
    selectedThirdPartyProviderId = providerId;
    renderThirdPartyScreen();
}

function openThirdPartyProvider(providerId) {
    selectThirdPartyProvider(providerId);
    thirdPartyDetailOpen = true;
    renderThirdPartyScreen();
}

function closeThirdPartyProvider() {
    syncSelectedThirdPartyProviderDraft();
    if (activeThirdPartySubtab === 'connect') {
        closeThirdPartyConnectProvider();
        return;
    }
    thirdPartyDetailOpen = false;
    renderThirdPartyScreen();
}

function renderThirdPartyScreen() {
    const panels = Array.from(document.querySelectorAll('[data-thirdparty-panel]'));
    const tabButtons = Array.from(document.querySelectorAll('[data-thirdparty-subtab]'));
    const subtabsEl = document.getElementById('thirdparty_subtabs');
    const gridEl = document.getElementById('thirdparty_provider_grid');
    const detailEl = document.getElementById('thirdparty_provider_detail');
    const connectGridEl = document.getElementById('thirdparty_connect_grid');
    const connectDetailEl = document.getElementById('thirdparty_connect_detail');
    panels.forEach(function (panel) {
        panel.classList.toggle('is-active', panel.dataset.thirdpartyPanel === activeThirdPartySubtab);
    });
    tabButtons.forEach(function (button) {
        const isActive = button.dataset.thirdpartySubtab === activeThirdPartySubtab;
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    renderThirdPartyProviderList();
    renderThirdPartyConnectList();
    updateThirdPartyHeader();
    if (subtabsEl) {
        const hideSubtabs =
            (activeThirdPartySubtab === 'api_keys' && thirdPartyDetailOpen) ||
            (activeThirdPartySubtab === 'connect' && thirdPartyConnectDetailOpen);
        subtabsEl.classList.toggle('hidden', hideSubtabs);
        subtabsEl.style.display = hideSubtabs ? 'none' : '';
    }
    if (gridEl && detailEl) {
        if (activeThirdPartySubtab === 'api_keys' && thirdPartyDetailOpen) {
            gridEl.classList.add('hidden');
            detailEl.classList.remove('hidden');
            renderThirdPartyProviderDetail();
        } else {
            gridEl.classList.remove('hidden');
            detailEl.classList.add('hidden');
            detailEl.innerHTML = '';
        }
    }
    if (connectGridEl && connectDetailEl) {
        if (activeThirdPartySubtab === 'connect' && thirdPartyConnectDetailOpen) {
            connectGridEl.classList.add('hidden');
            connectDetailEl.classList.remove('hidden');
            renderThirdPartyConnectDetail();
        } else {
            connectGridEl.classList.remove('hidden');
            connectDetailEl.classList.add('hidden');
            connectDetailEl.innerHTML = '';
        }
    }
    if (!gridEl || !detailEl) return;
    if (activeThirdPartySubtab !== 'api_keys') {
        gridEl.classList.remove('hidden');
        detailEl.classList.add('hidden');
        detailEl.innerHTML = '';
        return;
    }
    if (thirdPartyDetailOpen) {
        gridEl.classList.add('hidden');
        detailEl.classList.remove('hidden');
        renderThirdPartyProviderDetail();
    } else {
        gridEl.classList.remove('hidden');
        detailEl.classList.add('hidden');
        detailEl.innerHTML = '';
    }
}

function initThirdPartySettingsUi() {
    const backBtn = document.getElementById('thirdparty_section_back');
    const subtabButtons = Array.from(document.querySelectorAll('[data-thirdparty-subtab]'));
    if (backBtn && backBtn.dataset.bound !== '1') {
        backBtn.dataset.bound = '1';
        backBtn.addEventListener('click', function () {
            closeThirdPartyProvider();
        });
    }
    subtabButtons.forEach(function (button) {
        if (button.dataset.bound === '1') return;
        button.dataset.bound = '1';
        button.addEventListener('click', function () {
            if (activeThirdPartySubtab === 'connect' && button.dataset.thirdpartySubtab !== 'connect') {
                resetThirdPartyTelegramPolling();
                resetThirdPartyWhatsAppPolling();
            }
            activeThirdPartySubtab = button.dataset.thirdpartySubtab || 'api_keys';
            if (activeThirdPartySubtab === 'api_keys') {
                thirdPartyConnectDetailOpen = false;
            }
            if (activeThirdPartySubtab === 'connect') {
                loadThirdPartyConnectStatus(false);
                loadThirdPartyConnectorDraft(false);
            }
            if (activeThirdPartySubtab === 'mcp' && typeof window.loadMCPSettings === 'function') {
                window.loadMCPSettings();
            }
            renderThirdPartyScreen();
        });
    });
    if (typeof window.updateConnectionStatus === 'function' && !window.__thirdPartyWrappedConnectionStatus) {
        const originalUpdateConnectionStatus = window.updateConnectionStatus;
        window.updateConnectionStatus = function () {
            const result = originalUpdateConnectionStatus.apply(this, arguments);
            loadThirdPartyConnectStatus(false);
            return result;
        };
        window.__thirdPartyWrappedConnectionStatus = true;
    }
    loadThirdPartySettings();
    applyThirdPartyConnectDeepLink();
    loadThirdPartyConnectStatus(false);
    loadThirdPartyConnectorDraft(false);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('tab-thirdparty')) initThirdPartySettingsUi();
    });
} else if (document.getElementById('tab-thirdparty')) {
    initThirdPartySettingsUi();
}

window.toggleProviderInput = toggleProviderInput;
window.validateProvider = validateProvider;
window.saveThirdPartySettings = saveThirdPartySettings;
window.loadThirdPartySettings = loadThirdPartySettings;
window.selectThirdPartyProvider = selectThirdPartyProvider;
window.openThirdPartyProvider = openThirdPartyProvider;
window.selectThirdPartyConnectProvider = selectThirdPartyConnectProvider;
window.openThirdPartyConnectProvider = openThirdPartyConnectProvider;
