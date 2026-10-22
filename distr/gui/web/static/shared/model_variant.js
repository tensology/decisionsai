// 2026-10-22: detect effort/speed already encoded in a model id (e.g. gpt-5.3-codex-high-fast, grok-4.7-medium).
// Only whole segments separated by - _ : / . count, so "flash" is never "fast" and "highlight" is never "high".
const EFFORT = { minimal: 'minimal', low: 'low', medium: 'medium', med: 'medium', high: 'high', xhigh: 'xhigh', 'extra-high': 'xhigh', max: 'max', thinking: 'thinking', reasoning: 'thinking' };
const SPEED = { fast: 'priority', turbo: 'priority', priority: 'priority' };

export function encodedVariant(modelId) {
    const id = String(modelId || '').toLowerCase().trim();
    if (!id) return { effort: '', speed: '' };
    const tail = id.split('/').pop();
    const normalized = tail.replace(/extra-high/g, 'extra_high_');
    const segments = normalized.split(/[-_:.]+/).filter(Boolean);
    // The first segment is the model family ("max-..." or "turbo-..." prefixes are names, not variants).
    const rest = segments.slice(1);
    let effort = '';
    let speed = '';
    rest.forEach((segment) => {
        if (segment === 'extra') return;
        if (segment === 'high' && normalized.includes('extra_high_')) { effort = 'xhigh'; return; }
        if (EFFORT[segment]) effort = EFFORT[segment];
        if (SPEED[segment]) speed = SPEED[segment];
    });
    return { effort, speed };
}

// Display-only: drop bracketed tags that a badge already shows, e.g. "Grok Code Fast (free)" -> "Grok Code Fast".
const TAG_SUFFIX = /\s*\((?:free|paid|high|low|medium|standard|fast|priority|optimized)\)\s*$/i;
export function displayModelName(name) {
    let out = String(name || '').trim();
    while (TAG_SUFFIX.test(out)) out = out.replace(TAG_SUFFIX, '').trim();
    // Tags can also sit before a kept bracket: "X (free) (free) (256K ctx)" -> "X (256K ctx)".
    out = out.replace(/\s*\((?:free|paid|high|low|medium|minimal|xhigh|max|thinking|beta|optimized|exacto)\)(?=\s*\()/gi, '').replace(/\s{2,}/g, ' ').trim();
    return out;
}

// Short readable name for chips: catalog display name if given, else the id without provider prefix and :free/:cloud tags.
export function shortModelName(id, catalogName = '') {
    const named = displayModelName(catalogName);
    if (named && named.toLowerCase() !== String(id || '').toLowerCase()) return named.replace(/^[^:]{1,40}:\s+/, '');
    let tail = String(id || '').split('/').pop() || '';
    tail = tail.replace(/(:free|:optimized|:cloud)+$/gi, '');
    return prettyModelSlug(tail);
}

const BRAND_WORDS = { minimax: 'MiniMax', deepseek: 'DeepSeek', glm: 'GLM', gpt: 'GPT', qwen: 'Qwen', gemini: 'Gemini', claude: 'Claude', grok: 'Grok', kimi: 'Kimi', llama: 'Llama', mistral: 'Mistral', gemma: 'Gemma', sonnet: 'Sonnet', opus: 'Opus', haiku: 'Haiku', seed: 'Seed', dola: 'Dola' };

// "minimax-m3.1-flash" -> "MiniMax M3.1 Flash"; "deepseek-v4.1-flash" -> "DeepSeek V4.1 Flash".
export function prettyModelSlug(slug) {
    const text = String(slug || '').trim();
    if (!text || /\s/.test(text) || /[A-Z]/.test(text)) return text;
    const words = text.split(/[-_]/).filter(Boolean);
    const out = words.map((word) => {
        const lower = word.toLowerCase();
        if (BRAND_WORDS[lower]) return BRAND_WORDS[lower];
        if (/^[a-z]\d/.test(lower)) return lower.toUpperCase();
        if (/^\d/.test(lower)) return lower;
        return lower[0].toUpperCase() + lower.slice(1);
    });
    if (/^GPT$/.test(out[0]) && out[1] && /^\d/.test(out[1])) out.splice(0, 2, `GPT-${out[1]}`);
    return out.join(' ');
}
