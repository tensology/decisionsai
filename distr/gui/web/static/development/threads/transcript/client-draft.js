// Display-only: an agent-written client message ("Reply to send Greg on WhatsApp:" plus a
// quoted draft) belongs to the separate Message Client action, not inside the work response.
// The stored response is unchanged; the quoted wording becomes the Message Client seed.
const HEADING = /^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*(?:suggested |proposed |draft(?:ed)? )?(?:reply|response|message)(?: to send)?(?: (?:to|for) [^\n:*]{1,60})?(?: (?:on|via|by) (?:whatsapp|telegram|email|e-mail|gmail|sms))?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*:?[ \t]*$/i;

// Only headings addressed to someone or a channel, or labelled as a draft, count.
const ADDRESSED = /to send|\b(?:to|for) [a-z]|\b(?:on|via|by) (?:whatsapp|telegram|email|e-mail|gmail|sms)|suggested|proposed|draft/i;

export function splitClientDraft(value) {
    const content = String(value || '');
    const lines = content.split('\n');
    for (let start = 0; start < lines.length; start += 1) {
        if (!HEADING.test(lines[start]) || !lines[start].includes(':') || !ADDRESSED.test(lines[start])) continue;
        let index = start + 1;
        while (index < lines.length && !lines[index].trim()) index += 1;
        const quoteStart = index;
        while (index < lines.length && /^[ \t]*>/.test(lines[index])) index += 1;
        if (index === quoteStart) continue;
        const draft = lines.slice(quoteStart, index).map(line => line.replace(/^[ \t]*>[ \t]?/, '')).join('\n').trim();
        if (!draft) continue;
        const rest = [...lines.slice(0, start), ...lines.slice(index)].join('\n').replace(/\n{3,}/g, '\n\n').trim();
        return { content: rest, draft };
    }
    return { content, draft: '' };
}
