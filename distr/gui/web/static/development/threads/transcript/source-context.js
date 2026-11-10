// Separate generated source evidence for display only. The engine keeps the original message.
export function splitProposalSourceContext(value) {
    const content = String(value || '');
    const empty = { content, evidence: [] };
    const header = /\n\nRetained proposal conversation evidence \([^\n]+\):\n/.exec(content);
    if (!header) return empty;
    const start = header.index + header[0].length;
    if (content[start] !== '[') return empty;
    let depth = 0, quoted = false, escaped = false;
    for (let index = start; index < content.length; index += 1) {
        const char = content[index];
        if (quoted) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === '"') quoted = false;
            continue;
        }
        if (char === '"') quoted = true;
        else if (char === '[' || char === '{') depth += 1;
        else if (char === ']' || char === '}') depth -= 1;
        if (depth !== 0) continue;
        try {
            const evidence = JSON.parse(content.slice(start, index + 1));
            if (!Array.isArray(evidence) || !evidence.length || !evidence.every(row => row && typeof row === 'object' && typeof row.id === 'string')) return empty;
            const seen = new Set();
            return {
                content: content.slice(0, header.index) + content.slice(index + 1),
                evidence: evidence.filter(row => {
                    const key = JSON.stringify([row.id, row.direction, row.body, row.transcript]);
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                }),
            };
        } catch (_) { return empty; }
    }
    return empty;
}
