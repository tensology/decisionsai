import { bindWorkflowStepNumbers, nextWorkflowLoopId, workflowLoopHtml, workflowStepDataAttrs, workflowStepNumbersHtml, workflowViewSwitchHtml } from '../../shared/workflow-loop.js?v=20261022-threadring2';
import { shortModelName } from '../../../shared/model_variant.js?v=20261022-composer6';
// Owns threads transcript rendering, interactions, and private view state.
export function createThreadsTranscript({ context, actions, el, token }) {
    function messageTime(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function turnTimingHtml(message) {
        const startedAt = message.turn_started_at || message.timestamp;
        const startedLabel = messageTime(startedAt);
        if (!startedLabel) return '';
        const rawDuration = Number(message.turn_duration_seconds);
        const hasDuration = Number.isFinite(rawDuration) && rawDuration > 0;
        const duration = hasDuration ? actions.formatSeconds(rawDuration, true) : '';
        return `<div class="message-timing" aria-label="Started ${actions.escapeHtml(startedLabel)}${hasDuration ? `, worked for ${actions.escapeHtml(duration)}` : ''}"><time datetime="${actions.escapeHtml(startedAt)}">${actions.escapeHtml(startedLabel)}</time>${hasDuration ? `<span aria-hidden="true">·</span><span>${actions.escapeHtml(duration)}</span>` : ''}</div>`;
    }

    function markdownInline(line) {
        const tokens = [];
        const hold = (html) => {
            const token = `@@DECISIONS_INLINE_${tokens.length}@@`;
            tokens.push(html);
            return token;
        };
        let rendered = line
            .replace(/`([^`]+)`/g, (_match, content) => {
                if (/^https?:\/\/[^\s]+$/i.test(content)) {
                    return hold(`<a class="inline-code-link" href="${content}" target="_blank" rel="noopener"><code>${content}</code></a>`);
                }
                const fileReference = /(?:^|\/)[^/\s]+\.[a-z0-9]{1,8}(?::\d+)?$/i.test(content);
                return hold(`<code${fileReference ? ' class="file-ref"' : ''}>${content}</code>`);
            })
            .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi, (_match, label, url) => hold(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`))
            .replace(/https?:\/\/[^\s<]+/gi, (value) => {
                const trailing = value.match(/[),.!?:;]+$/)?.[0] || '';
                const url = trailing ? value.slice(0, -trailing.length) : value;
                return `${hold(`<a href="${url}" target="_blank" rel="noopener">${url}</a>`)}${trailing}`;
            })
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>');
        tokens.forEach((html, index) => {
            rendered = rendered.replace(`@@DECISIONS_INLINE_${index}@@`, html);
        });
        return rendered;
    }

    function markdownCodeBlock(language, code) {
        const normalizedLanguage = String(language || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9_-]/g, '');
        const diff = normalizedLanguage === 'diff' || normalizedLanguage === 'patch';
        const lines = code
            .replace(/^\n|\n$/g, '')
            .split('\n')
            .map((line) => {
                let className = 'code-line';
                if (diff && /^\+(?!\+\+)/.test(line)) className += ' diff-add';
                else if (diff && /^-(?!--)/.test(line)) className += ' diff-remove';
                else if (diff && /^@@/.test(line)) className += ' diff-meta';
                return `<span class="${className}">${line || ' '}</span>`;
            })
            .join('');
        return `<pre class="code-block${normalizedLanguage ? ` language-${normalizedLanguage}` : ''}"><code>${lines}</code></pre>`;
    }

    function markdownTableCells(line) {
        return line
            .trim()
            .replace(/^\||\|$/g, '')
            .split('|')
            .map((cell) => cell.trim());
    }

    function markdownTableHtml(headers, rows) {
        const heading = headers.map((cell) => `<th>${markdownInline(cell)}</th>`).join('');
        const body = rows.map((row) => `<tr>${headers.map((_header, index) => `<td>${markdownInline(row[index] || '')}</td>`).join('')}</tr>`).join('');
        return `<div class="md-table-wrap"><table><thead><tr>${heading}</tr></thead><tbody>${body}</tbody></table></div>`;
    }

    function markdownLineHtml(trimmed) {
        const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
        if (heading) {
            const level = Math.min(4, heading[1].length + 1);
            return `<h${level}>${markdownInline(heading[2])}</h${level}>`;
        }
        const bullet = trimmed.match(/^[-*]\s+(.+)$/);
        if (bullet) return `<div class="md-list-item"><span>•</span><div>${markdownInline(bullet[1])}</div></div>`;
        const numbered = trimmed.match(/^(\d+)\.\s+(.+)$/);
        if (numbered) return `<div class="md-list-item"><span>${numbered[1]}.</span><div>${markdownInline(numbered[2])}</div></div>`;
        return `<p>${markdownInline(trimmed)}</p>`;
    }

    function markdownTableAt(lines, startIndex) {
        const headerLine = lines[startIndex].trim();
        const dividerLine = lines[startIndex + 1];
        if (!/^\|.*\|$/.test(headerLine) || !dividerLine) return null;
        const divider = markdownTableCells(dividerLine).every((cell) => /^:?-{3,}:?$/.test(cell));
        if (!divider) return null;
        const rows = [];
        let nextIndex = startIndex + 2;
        while (nextIndex < lines.length && /^\|.*\|$/.test(lines[nextIndex].trim())) {
            rows.push(markdownTableCells(lines[nextIndex]));
            nextIndex += 1;
        }
        return {
            html: markdownTableHtml(markdownTableCells(headerLine), rows),
            nextIndex
        };
    }

    function markdownBlockAt(lines, index) {
        const trimmed = lines[index].trim();
        if (!trimmed) return { html: '<div class="md-gap"></div>', nextIndex: index + 1 };
        if (/^@@DECISIONS_CODE_\d+@@$/.test(trimmed)) return { html: trimmed, nextIndex: index + 1 };
        const table = markdownTableAt(lines, index);
        if (table) return table;
        return { html: markdownLineHtml(trimmed), nextIndex: index + 1 };
    }

    function assistantMarkdown(value) {
        const codeBlocks = [];
        const safe = actions.escapeHtml(value || '').replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_match, language, code) => {
            const token = `@@DECISIONS_CODE_${codeBlocks.length}@@`;
            codeBlocks.push(markdownCodeBlock(language, code));
            return token;
        });
        const lines = safe.split('\n');
        const rendered = [];
        let index = 0;
        while (index < lines.length) {
            const block = markdownBlockAt(lines, index);
            rendered.push(block.html);
            index = block.nextIndex;
        }
        return codeBlocks.reduce((html, block, index) => html.replace(`@@DECISIONS_CODE_${index}@@`, block), rendered.join(''));
    }

    function activityHtml(activity) {
        const status = activity.status ? String(activity.status).toLowerCase() : '';
        const rawTitle = activity.title || activity.tool_name || activity.event_type || 'Activity';
        const title = /^request received$/i.test(rawTitle) ? 'Analyzing request' : rawTitle;
        let detail = activity.summary || activity.result_summary || activity.detail || activity.result_detail || '';
        const titleUrl = firstHttpUrl(title);
        const detailUrl = firstHttpUrl(detail);
        if (titleUrl && detailUrl && normalizedHttpUrl(titleUrl).replace(/\/$/, '') === normalizedHttpUrl(detailUrl).replace(/\/$/, '')) {
            const remainder = String(detail)
                .replace(detailUrl, '')
                .replace(/^\s*(?:opened\s+url\s*:\s*)?/i, '')
                .trim();
            detail = remainder;
        }
        const route = /route|model|provider|dispatch/i.test(`${activity.event_type || ''} ${title}`);
        const icon = status === 'failed' ? '!' : status === 'running' ? '●' : status === 'completed' ? '✓' : route ? '↻' : '·';
        return `<div class="activity-row${route ? ' route' : ''}${status === 'failed' ? ' failed' : ''}">
            <span class="activity-icon">${icon}</span>
            <div><div class="activity-title">${linkifyActivityText(title)}</div>${detail ? `<div class="activity-detail">${linkifyActivityText(detail)}</div>` : ''}</div>
            ${status ? `<span class="activity-meta">${actions.escapeHtml(status)}</span>` : ''}
        </div>`;
    }

    function activityDedupeText(value) {
        return String(value || '')
            .toLowerCase()
            .replace(/\s+/g, ' ')
            .trim();
    }

    function activityDedupeKeys(activity) {
        const kind = turnEventKind(activity);
        const values = [activity?.title, activity?.summary, activity?.result_summary, activity?.detail, activity?.result_detail]
            .map(activityDedupeText)
            .filter(Boolean);
        return new Set(values.map((value) => `${kind}:${value}`));
    }

    function durableActivityDedupeKeys(turns) {
        const keys = new Set();
        (turns || [])
            .flatMap((turn) => turn.events || [])
            .forEach((event) => {
                const type = String(event?.event_type || '').toLowerCase();
                if (!['tool_completed', 'tool_failed', 'tool_waiting'].includes(type)) return;
                activityDedupeKeys(event).forEach((key) => keys.add(key));
            });
        return keys;
    }

    function isDurableActivityDuplicate(activity, keys) {
        return Array.from(activityDedupeKeys(activity)).some((key) => keys.has(key));
    }

    function firstHttpUrl(value) {
        const match = String(value || '').match(/https?:\/\/[^\s<>"']+/i);
        return match ? match[0].replace(/[),.;!?]+$/, '') : '';
    }

    function normalizedHttpUrl(value) {
        try {
            const parsed = new URL(String(value || ''));
            if (!['http:', 'https:'].includes(parsed.protocol)) return '';
            return parsed.href;
        } catch (_error) {
            return '';
        }
    }

    function linkifyActivityText(value) {
        const text = String(value || '');
        const matcher = /https?:\/\/[^\s<>"']+/gi;
        let cursor = 0;
        let rendered = '';
        for (const match of text.matchAll(matcher)) {
            const rawMatch = match[0];
            const url = rawMatch.replace(/[),.;!?]+$/, '');
            const suffix = rawMatch.slice(url.length);
            const href = normalizedHttpUrl(url);
            rendered += actions.escapeHtml(text.slice(cursor, match.index));
            rendered += href
                ? `<a href="${actions.escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${actions.escapeHtml(url)}</a>${actions.escapeHtml(suffix)}`
                : actions.escapeHtml(rawMatch);
            cursor = Number(match.index) + rawMatch.length;
        }
        return rendered + actions.escapeHtml(text.slice(cursor));
    }

    function gitStatusEntries(value) {
        if (Array.isArray(value)) return value;
        if (value && typeof value === 'object') return Object.entries(value).map(([path, status]) => `${status} ${path}`);
        return [];
    }

    function turnChangesHtml() {
        const changes = context.currentRun?.changes || {};
        const files = Array.isArray(changes.files) ? changes.files : [];
        if (!files.length || changes.undone) return '';
        const additions = Number(changes.additions || 0);
        const deletions = Number(changes.deletions || 0);
        const rows = files
            .map(
                (file) => `<tr class="turn-change-row">
            <td title="${actions.escapeHtml(file.path || '')}">${actions.escapeHtml(file.path || 'Changed file')}</td>
            <td><b>+${Number(file.additions || 0)}</b></td>
            <td><i>-${Number(file.deletions || 0)}</i></td>
        </tr>`
            )
            .join('');
        const undoTitle = changes.reversible
            ? 'Restore only the files changed by this turn'
            : 'Undo is unavailable because this turn overlaps changes that already existed';
        return `<section class="turn-changes" aria-label="Files changed by this turn">
            <div class="turn-changes-head">
                <button type="button" class="turn-changes-toggle" data-turn-changes-toggle aria-expanded="true"><span class="change-disclosure">›</span><strong>Edited ${files.length} ${files.length === 1 ? 'file' : 'files'}</strong><small><b>+${additions}</b><i>-${deletions}</i></small></button>
                <div class="turn-change-actions"><button type="button" data-turn-undo ${changes.reversible ? '' : 'disabled'} title="${actions.escapeHtml(undoTitle)}">Undo</button><button type="button" data-turn-review>Review</button></div>
            </div>
            <div class="turn-change-list"><table><thead><tr><th>File</th><th>Added</th><th>Removed</th></tr></thead><tbody>${rows}</tbody></table></div>
        </section>`;
    }

    // Spoken rows linked to replies (spoken_source_row_id -> spoken text); set per render.
    let spokenBySource = new Map();

    function spokenTextForMessage(message) {
        if (!message || message.chat_row_id == null) return '';
        return spokenBySource.get(String(message.chat_row_id)) || '';
    }

    function playSpokenButtonHtml(index) {
        const playIcon = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7-4.5z"></path></svg>';
        return `<button type="button" class="read-response-button play-spoken-button" data-play-spoken="${index}" aria-label="Play spoken version" title="Play spoken version">${playIcon}</button>`;
    }

    function messageActionsHtml(message, index, latestAssistant) {
        const copyIcon =
            '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8" height="8" rx="1.5"></rect><path d="M3 10V4.5A1.5 1.5 0 0 1 4.5 3H10"></path></svg>';
        const forkIcon =
            '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="4" cy="3" r="1.5"></circle><circle cx="12" cy="5" r="1.5"></circle><circle cx="12" cy="12" r="1.5"></circle><path d="M4 4.5v3A4.5 4.5 0 0 0 8.5 12h2M5.5 5h5"></path></svg>';
        const mouthIcon =
            '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8c2-2.2 4-2.2 6-1.2C10 5.8 12 5.8 14 8c-1.6 2.6-3.8 3.6-6 3.6S3.6 10.6 2 8z"></path><path d="M3.5 8.2h9"></path></svg>';
        const playButton = message.spoken
            ? ''
            : spokenTextForMessage(message) ? playSpokenButtonHtml(index) : '';
        const readButton = latestAssistant && !message.spoken && !playButton
            ? `<button type="button" class="read-response-button" data-read-response="${index}" aria-label="Read response aloud" title="Read response: speak a short spoken version and add it to this thread">${mouthIcon}</button>`
            : playButton;
        return `<footer class="message-footer"><div class="message-actions" aria-label="Response actions"><button type="button" data-copy-message="${index}" aria-label="Copy response" title="Copy response to clipboard">${copyIcon}</button>${readButton}${latestAssistant ? `<button type="button" data-fork-current aria-label="Fork thread" title="Continue from this transcript in a new independent thread. This does not create a Git branch.">${forkIcon}</button>` : ''}</div>${turnTimingHtml(message)}</footer>`;
    }

    function skillIconSvg() {
        return '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.25 2.5h3.5v2.25H12v3.5H9.75v2.25h-3.5V8.25H4v-3.5h2.25z"></path><path d="M6.25 4.75h3.5v3.5h-3.5z"></path></svg>';
    }

    function skillDisplayName(skillId) {
        const skill = context.skills.find((item) => String(item.id) === String(skillId));
        return String(skill?.name || skillId || 'Skill');
    }

    function messageSkillIconsHtml(message) {
        const skills = Array.isArray(message.skills) ? message.skills : [];
        if (!skills.length) return '';
        return `<div class="message-skill-icons" aria-label="Skills used for this request">${skills
            .map((skill) => {
                const id = typeof skill === 'string' ? skill : skill?.id;
                const label = typeof skill === 'object' && skill?.name ? skill.name : skillDisplayName(id);
                return `<span class="message-skill-icon" title="${actions.escapeHtml(label)}" aria-label="${actions.escapeHtml(label)}">${skillIconSvg()}</span>`;
            })
            .join('')}</div>`;
    }

    function ticketMovePresentation(message) {
        const content = String(message?.content || '').trim();
        let match = content.match(/^Ticket moved:\s*(.+?)\s+to\s+(.+?)\.$/i);
        if (match) return { from: match[1], to: match[2] };
        match = content.match(/^Ticket moved to\s+(.+?)(?:\s+after workflow completion)?\.$/i);
        if (match) return { from: '', to: match[1] };
        match = content.match(/^Ticket #\d+\s+"[\s\S]+?"(?:\s+on board\s+"[^"]+")?\s+was moved(?:\s+from\s+"([^"]+)")?\s+to lane\s+"([^"]+)"\.$/i);
        if (match) return { from: match[1] || '', to: match[2] };
        match = content.match(/^Ticket #\d+\s+"[\s\S]+?"(?:\s+on board\s+"[^"]+")?\s+advanced to lane\s+"([^"]+)"\s+after the board workflow completed\.$/i);
        return match ? { from: '', to: match[1] } : null;
    }

    function ticketMoveHtml(message, move) {
        const label = move.from
            ? `<span>Moved:</span> <strong>${actions.escapeHtml(move.from)}</strong> <span aria-hidden="true">→</span> <strong>${actions.escapeHtml(move.to)}</strong>`
            : `<span>Moved to</span> <strong>${actions.escapeHtml(move.to)}</strong>`;
        const timestamp = messageTime(message.timestamp);
        return `<article class="studio-message thread-event"><div class="thread-status-event"><span class="thread-status-dot" aria-hidden="true"></span>${label}${timestamp ? `<time>${actions.escapeHtml(timestamp)}</time>` : ''}</div></article>`;
    }

    function userMessagePresentation(message) {
        const marker = '\n\nFocused context for this turn:\n\n';
        const content = String(message.content || '');
        const markerIndex = content.indexOf(marker);
        const skills = Array.isArray(message.skills) ? [...message.skills] : [];
        if (markerIndex < 0) return { content, skills };
        const generatedSkillBlock =
            /\[Skill:\s*[^\]]+\]\s*\nUse the installed skill "([^"]+)" for this turn\. Read it with read_harness_skill before acting, then follow its instructions for this request\./g;
        const context = content.slice(markerIndex + marker.length);
        let matched = false;
        const remainingContext = context
            .replace(generatedSkillBlock, (_, skillId) => {
                matched = true;
                if (!skills.includes(skillId)) skills.push(skillId);
                return '';
            })
            .replace(/\n{3,}/g, '\n\n')
            .trim();
        if (!matched) return { content, skills };
        return {
            content: content.slice(0, markerIndex) + (remainingContext ? `${marker}${remainingContext}` : ''),
            skills
        };
    }

    function turnEventIconSvg(kind) {
        const paths = {
            agent: '<path d="M8 2.1l1.15 3.05L12.2 6.3 9.15 7.45 8 10.5 6.85 7.45 3.8 6.3l3.05-1.15z"></path><path d="M12.1 10.2l.55 1.45 1.45.55-1.45.55-.55 1.45-.55-1.45-1.45-.55 1.45-.55z"></path>',
            tests: '<circle cx="8" cy="8" r="5.4"></circle><path d="M5.5 8.1l1.55 1.55 3.5-3.55"></path>',
            commands: '<rect x="2.2" y="3" width="11.6" height="10" rx="2"></rect><path d="M4.6 6.1l2 1.7-2 1.7M8.2 10h3"></path>',
            edits: '<path d="M3 11.9l.45-2.25 6.7-6.7a1.35 1.35 0 0 1 1.9 1.9l-6.7 6.7z"></path><path d="M8.9 4.2l2.9 2.9M3 13h10"></path>',
            browser:
                '<circle cx="8" cy="8" r="5.5"></circle><path d="M2.7 8h10.6M8 2.5c1.45 1.5 2.15 3.35 2.15 5.5S9.45 12 8 13.5C6.55 12 5.85 10.15 5.85 8S6.55 4 8 2.5z"></path>',
            inspection: '<circle cx="6.9" cy="6.9" r="3.8"></circle><path d="M9.8 9.8l3.2 3.2"></path>',
            skills: '<path d="M6.25 2.5h3.5v2.25H12v3.5H9.75v2.25h-3.5V8.25H4v-3.5h2.25z"></path><path d="M6.25 4.75h3.5v3.5h-3.5z"></path>',
            guidance: '<path d="M3 5.2h7.4M8.2 3l2.2 2.2-2.2 2.2M13 10.8H5.6M7.8 8.6l-2.2 2.2L7.8 13"></path>',
            other: '<circle cx="4" cy="8" r="1"></circle><circle cx="8" cy="8" r="1"></circle><circle cx="12" cy="8" r="1"></circle>'
        };
        return `<svg viewBox="0 0 16 16" aria-hidden="true">${paths[kind] || paths.other}</svg>`;
    }

    function turnEventKind(event) {
        const metadata = event?.metadata || {};
        const toolName = String(metadata.tool_name || event?.tool_name || event?.title || '').toLowerCase();
        const command = turnCommand(event);
        const signal = `${toolName} ${command} ${metadata.agent_name || ''}`.toLowerCase();
        if (/agent|reviewer|review_agent|subagent|tdd|security_review/.test(signal)) return 'agent';
        if (/pytest|test\b|vitest|jest|playwright test|cypress|lint|ruff|mypy|typecheck|npm run (?:test|check|lint)|pnpm (?:test|check|lint)/.test(signal))
            return 'tests';
        if (/replace_text|write_file|file_change|file_edit|edit|patch|apply_patch/.test(toolName)) return 'edits';
        if (/view_image|screenshot|browser|playwright|chrome|web_search|smart_open|open_page|open_url/.test(toolName)) return 'browser';
        if (/run_command|terminal|shell|command|exec/.test(toolName)) return 'commands';
        if (/read_file|list_files|search_files|search|read|list|\brg\b/.test(toolName)) return 'inspection';
        if (/harness_skill|skill|guide/.test(toolName)) return 'skills';
        return 'other';
    }

    function turnEventAgentName(event) {
        const metadata = event?.metadata || {};
        const explicit = metadata.agent_name || metadata.reviewer_name || metadata.subagent_name;
        if (explicit) return String(explicit);
        const signal = String(metadata.tool_name || event?.tool_name || event?.title || '').toLowerCase();
        if (/tdd/.test(signal)) return 'TDD guide';
        if (/security/.test(signal)) return 'Security reviewer';
        if (/review/.test(signal)) return 'Code reviewer';
        if (/development_agent|cursor_agent/.test(signal)) {
            if (/cursor/.test(signal) || /cursor/i.test(String(event?.title || event?.agent_name || ''))) {
                return 'Cursor';
            }
            return 'Development agent';
        }
        if (/tool_compiler/.test(signal)) return 'Tool builder';
        return String(event?.title || 'Development agent');
    }

    // ---- Dev-agent approval card (tool, action, argument diff, Approve / Deny, expiry) ----
    function approvalValue(value) {
        if (value == null || value === '') return '—';
        if (typeof value === 'object') return value.dateTime || value.date || JSON.stringify(value);
        return String(value);
    }
    function approvalDiffHtml(details) {
        const d = details || {};
        const before = d.before && typeof d.before === 'object' ? d.before : null;
        const after = d.after && typeof d.after === 'object' ? d.after : null;
        if (before || after) {
            const keys = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})])).filter((k) => !['htmlLink', 'status'].includes(k));
            const rows = keys.map((k) => {
                const b = before ? approvalValue(before[k]) : '';
                const a = after ? (k in after ? approvalValue(after[k]) : (before ? '(unchanged)' : '—')) : '(removed)';
                const changed = before && after && k in after && b !== a;
                return `<tr class="${changed ? 'changed' : ''}"><th>${actions.escapeHtml(k)}</th>${before ? `<td>${actions.escapeHtml(b)}</td>` : ''}${after || before ? `<td>${actions.escapeHtml(after ? a : '(will be removed)')}</td>` : ''}</tr>`;
            }).join('');
            const head = `<tr><th></th>${before ? '<th>Current</th>' : ''}<th>${after ? (before ? 'Proposed' : 'New') : 'After'}</th></tr>`;
            return `<table class="dev-approval-diff">${head}${rows}</table>`;
        }
        const rest = Object.entries(d).filter(([k]) => !['provider', 'action'].includes(k));
        if (!rest.length) return '';
        return `<table class="dev-approval-diff">${rest.map(([k, v]) => `<tr><th>${actions.escapeHtml(k)}</th><td>${actions.escapeHtml(approvalValue(v))}</td></tr>`).join('')}</table>`;
    }
    function approvalCardHtml(req) {
        const status = String(req.status || 'pending').toLowerCase();
        const expires = req.expires_at ? new Date(req.expires_at) : null;
        const expired = status === 'pending' && expires && expires.getTime() < Date.now();
        const shown = expired ? 'expired' : status;
        const label = { pending: 'Waiting for you', approved: 'Approved', consumed: 'Approved and done', denied: 'Denied', expired: 'Expired' }[shown] || shown;
        const when = expires ? expires.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        const buttons = shown === 'pending'
            ? `<div class="dev-approval-actions"><button type="button" class="dev-approval-approve" data-dev-approval-action="approve" data-request-id="${actions.escapeHtml(req.request_id)}">Approve</button><button type="button" class="dev-approval-deny" data-dev-approval-action="deny" data-request-id="${actions.escapeHtml(req.request_id)}">Deny</button><small>Expires ${actions.escapeHtml(when)}</small></div>`
            : '';
        return `<div class="turn-activity-block dev-approval-card status-${actions.escapeHtml(shown)}" data-dev-approval="${actions.escapeHtml(req.request_id)}" role="group" aria-label="Approval request">
            <div class="dev-approval-head"><strong>${actions.escapeHtml(`${req.tool || 'tool'} · ${req.action || ''}`)}</strong><span class="dev-approval-status">${actions.escapeHtml(label)}</span></div>
            <p class="dev-approval-summary">${actions.escapeHtml(req.summary || '')}</p>
            ${approvalDiffHtml(req.details)}
            ${buttons}
        </div>`;
    }
    if (!window.__decisionsDevApprovalBound) {
        window.__decisionsDevApprovalBound = true;
        document.addEventListener('click', async (e) => {
            const button = e.target.closest?.('[data-dev-approval-action]');
            if (!button) return;
            e.preventDefault();
            const card = button.closest('[data-dev-approval]');
            const id = button.dataset.requestId;
            const verb = button.dataset.devApprovalAction === 'approve' ? 'approve' : 'deny';
            card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
            const headers = { 'Content-Type': 'application/json', 'X-Decisions-User-Action': 'click' };
            const tok = (typeof token === 'function' ? token() : token) || document.querySelector('meta[name="decisionsai-internal-api-token"]')?.content || '';
            if (tok) headers['X-DecisionsAI-Internal-Token'] = tok;
            try {
                const resp = await fetch(`/api/dev-approvals/${encodeURIComponent(id)}/${verb}`, { method: 'POST', headers, credentials: 'same-origin', body: '{}' });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && card) card.outerHTML = approvalCardHtml(data);
                else if (card) {
                    card.querySelectorAll('button').forEach((b) => { b.disabled = false; });
                    const s = card.querySelector('.dev-approval-status');
                    if (s) s.textContent = data.detail || 'Could not save your choice';
                }
            } catch (_) {
                card?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
            }
        });
    }

    function turnEventPresentation(event) {
        const approvalRequest = event?.metadata?.approval_request;
        if (approvalRequest && approvalRequest.request_id) {
            return { kind: 'approval', approval: approvalRequest, running: false, failed: false, title: '', preview: '', detail: '', status: approvalRequest.status || 'pending' };
        }
        const type = String(event?.event_type || '').toLowerCase();
        const status = String(event?.status || '').toLowerCase();
        if (type === 'turn_steered') {
            const detail = String(event.detail || event.summary || '').trim();
            return detail
                ? {
                      kind: 'guidance',
                      title: 'Instruction updated',
                      preview: String(event.title || 'Applied new guidance'),
                      detail,
                      status: 'updated',
                      failed: false
                  }
                : null;
        }
        if (!['tool_started', 'tool_completed', 'tool_failed', 'tool_waiting'].includes(type)) return null;
        const failed = type === 'tool_failed' || status === 'failed';
        const running = type === 'tool_started' && !failed;
        const waiting = type === 'tool_waiting' || status === 'waiting';
        const shellFailure = isRoutineShellStep(event) && failed;
        const kind = shellFailure ? 'commands' : turnEventKind(event);
        const command = turnCommand(event);
        const summary = String(event?.summary || event?.title || '').trim();
        let detail = String(event?.detail || '').trim();
        let title = String(event?.title || 'Development action');
        let preview = summary;
        let stateLabel = waiting ? 'needs attention' : failed ? 'failed' : running ? 'running' : 'finished';
        if (kind === 'agent') {
            title = `${turnEventAgentName(event)} ${running ? 'started' : waiting ? 'needs attention' : failed ? 'failed' : 'finished'}`;
            if (!running && !waiting) title += agentIdentitySuffix(event);
            if (String(event?.metadata?.tool_name || '').toLowerCase() === 'development_agent') preview = '';
        } else if (kind === 'tests') {
            title = running ? 'Running tests' : failed ? 'Tests failed' : 'Tests passed';
            preview = command || summary;
        } else if (shellFailure) {
            title = 'Command failed';
            preview = '';
            detail = [command, String(event?.detail || event?.summary || '').trim()].filter(Boolean).join('\n\n');
        } else if (kind === 'commands') {
            title = running ? 'Running command' : failed ? 'Command failed' : 'Ran command';
            preview = command || summary;
        } else if (kind === 'edits') {
            title = running ? 'Updating files' : failed ? 'File update failed' : 'Updated files';
        } else if (kind === 'browser') {
            title = running ? 'Checking in browser' : failed ? 'Browser check failed' : 'Browser check finished';
        } else if (kind === 'inspection') {
            title = running ? 'Inspecting project' : failed ? 'Inspection failed' : 'Inspected project';
        } else if (kind === 'skills') {
            title = running ? 'Consulting project guide' : failed ? 'Project guide failed' : 'Project guide consulted';
        }
        if (!shellFailure && (detail === preview || detail === command)) detail = '';
        const files = kind === 'edits' && Array.isArray(event?.metadata?.files) ? event.metadata.files : [];
        return {
            kind,
            title,
            preview,
            detail,
            status: stateLabel,
            failed,
            running,
            waiting,
            files
        };
    }

    function turnEventFilesHtml(files) {
        if (!files.length) return '';
        return `<span class="turn-event-files">${files.map((file) => `<span class="turn-event-file"><span title="${actions.escapeHtml(file.path || '')}">${actions.escapeHtml(file.path || 'Changed file')}</span><small><b>+${Number(file.additions || 0)}</b><i>-${Number(file.deletions || 0)}</i></small></span>`).join('')}</span>`;
    }

const ROUTINE_SHELL_TOOLS = new Set(['bash', 'sh', 'zsh', 'shell', 'run_shell', 'shell_command', 'run_terminal_cmd', 'terminal', 'exec', 'exec_command', 'run_command', 'command']);
    /** Display-only: routine shell/bash tool steps are hidden from the thread readout (failures still shown collapsed). */
    function isRoutineShellStep(event) {
        const type = String(event?.event_type || '').toLowerCase();
        if (!type.startsWith('tool_')) return false;
        const name = String(event?.metadata?.tool_name || event?.tool_name || '').toLowerCase().trim();
        return ROUTINE_SHELL_TOOLS.has(name);
    }
    function isFailedStep(event) {
        return String(event?.event_type || '').toLowerCase() === 'tool_failed' || String(event?.status || '').toLowerCase() === 'failed';
    }

    const HARNESS_LABELS = { pi: 'Pi', codex: 'Codex', cursor: 'Cursor', claude_code: 'Claude Code', claude: 'Claude Code', opencode: 'OpenCode', gemini: 'Gemini CLI', aider: 'Aider' };
    function harnessLabel(value) {
        const key = String(value || '').trim().toLowerCase();
        if (!key) return '';
        return HARNESS_LABELS[key] || key.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function isWorkerLifecycleEvent(event) {
        const metadata = event?.metadata || {};
        const toolName = String(metadata.tool_name || event?.tool_name || '').toLowerCase();
        return ['development_agent', 'cursor_agent'].includes(toolName) && (metadata.worker_lifecycle || String(metadata.call_id || '').startsWith('worker:'));
    }

    // One completion line per job: fold harness-worker start/end rows into the outer development-agent row.
    function mergeWorkerLifecycleEvents(events) {
        const list = Array.isArray(events) ? events : [];
        const outer = list.find((event) => String(event?.metadata?.tool_name || event?.tool_name || '').toLowerCase() === 'development_agent' && !isWorkerLifecycleEvent(event));
        if (!outer) return list;
        const workers = list.filter(isWorkerLifecycleEvent);
        if (!workers.length && !outer.metadata?.harness && !outer.metadata?.model) return list;
        const worker = workers[workers.length - 1] || {};
        const wm = worker.metadata || {};
        const om = outer.metadata || {};
        const merged = { ...outer, metadata: { ...om, worker_name: om.worker_name || wm.agent_name || wm.title || '', harness: om.harness || wm.harness || String(wm.call_id || '').replace(/^worker:/, ''), model: om.model || wm.model || '' } };
        return list.filter((event) => !isWorkerLifecycleEvent(event)).map((event) => (event === outer ? merged : event));
    }

    function agentIdentitySuffix(event) {
        const metadata = event?.metadata || {};
        const parts = [];
        const worker = String(metadata.worker_name || '').trim();
        if (worker && worker !== turnEventAgentName(event)) parts.push(worker);
        const harness = harnessLabel(metadata.harness);
        if (harness) parts.push(`harness: ${harness}`);
        const rawModel = String(metadata.model || '').trim();
        if (rawModel && rawModel.toLowerCase() !== 'auto') parts.push(`model: ${shortModelName(rawModel)}`);
        return parts.length ? ` (${parts.join(', ')})` : '';
    }

    /** Display-only: fold consecutive identical status rows (same kind, title, state) into one row with a count. */
    function collapseRepeatedEventItems(acc, item) {
        const prev = acc[acc.length - 1];
        const same = prev && item.kind !== 'approval' && prev.kind === item.kind && prev.baseTitle === item.title
            && prev.status === item.status && !item.failed && !prev.failed && !item.detail && !prev.detail;
        if (!same) {
            acc.push({ ...item, baseTitle: item.title, repeat: 1, files: [...(item.files || [])] });
            return acc;
        }
        prev.repeat += 1;
        prev.title = `${prev.baseTitle} ×${prev.repeat}`;
        if (item.preview) prev.preview = item.preview;
        (item.files || []).forEach((file) => {
            const hit = prev.files.find((f) => f.path === file.path);
            if (hit) {
                hit.additions = Number(hit.additions || 0) + Number(file.additions || 0);
                hit.deletions = Number(hit.deletions || 0) + Number(file.deletions || 0);
            } else prev.files.push({ ...file });
        });
        return acc;
    }

    function turnEventRowsHtml(events, { includeRunning = false, active = false } = {}) {
        return mergeWorkerLifecycleEvents(events)
            .filter((event) => !isRoutineShellStep(event) || isFailedStep(event))
            .map(turnEventPresentation)
            .filter((item) => item && (includeRunning || !item.running))
            .reduce(collapseRepeatedEventItems, [])
            .map((item) => {
                if (item.kind === 'approval') return approvalCardHtml(item.approval);
                const classes = `turn-activity-block turn-event-row event-${item.kind}${item.failed ? ' failed' : ''}${item.running ? ' running' : ''}${active ? ' active-turn-action' : ''}`;
                const main = `<span class="turn-activity-icon" aria-hidden="true">${turnEventIconSvg(item.kind)}</span><span class="turn-event-copy"><strong>${actions.escapeHtml(item.title)}</strong>${item.preview && item.preview !== item.title ? `<span class="turn-event-preview${['commands', 'tests'].includes(item.kind) ? ' code' : ''}">${actions.escapeHtml(item.preview)}</span>` : ''}${turnEventFilesHtml(item.files)}</span><small class="turn-event-status">${actions.escapeHtml(item.status)}</small>`;
                if (!item.detail) return `<div class="${classes}"><div class="turn-event-main">${main}</div></div>`;
                return `<details class="${classes}"><summary>${main}<span class="turn-activity-chevron" aria-hidden="true">›</span></summary><div class="turn-activity-content"><pre>${actions.escapeHtml(item.detail)}</pre></div></details>`;
            })
            .join('');
    }

    function completedTurnActivityHtml(turn) {
        const events = Array.isArray(turn?.events) ? turn.events : [];
        const rows = turnEventRowsHtml(events);
        if (!rows) return '';
        const status = String(turn?.status || '').toLowerCase();
        const failed =
            ['failed', 'cancelled'].includes(status) ||
            events.some((event) => ['tool_failed', 'turn_failed'].includes(String(event?.event_type || '').toLowerCase()));
        return `<section class="turn-activity-timeline turn-event-stream${failed ? ' failed' : ''}" aria-label="Activity for this response">${rows}</section>`;
    }

    function messageHtml(message, index, latestAssistantIndex, turnActivity = null, instruction = '', durableActivityKeys = new Set(), messages = []) {
        const role = message.role || 'assistant';
        if (role === 'user') {
            const presentation = userMessagePresentation(message);
            return `<article class="studio-message user"><div class="user-message">${actions.escapeHtml(presentation.content)}${messageSkillIconsHtml({ skills: presentation.skills })}</div></article>`;
        }
        if (role === 'tool') {
            const tool = message.tool_event || {};
            if (isDurableActivityDuplicate(tool, durableActivityKeys)) return '';
            return `<div class="activity-group">${activityHtml(Object.assign({}, tool, { timestamp: message.timestamp }))}</div>`;
        }
        if (role === 'workflow') {
            return workflowMessageHtml(message, index, messages);
        }
        const ticketMove = ticketMovePresentation(message);
        if (ticketMove) return ticketMoveHtml(message, ticketMove);
        if (message.spoken) return spokenMessageHtml(message);
        const latest = index === latestAssistantIndex;
        const content = workflowSafeAssistantContent(message.content || '');
        const assistantIdentity = window.DecisionsAssistantIdentity?.html() || '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
        return `<article class="studio-message assistant"><div class="message-avatar" data-assistant-identity>${assistantIdentity}</div><div class="message-body">${completedTurnActivityHtml(turnActivity)}<div class="message-markdown">${assistantMarkdown(content)}</div>${latest ? turnChangesHtml() : ''}${messageActionsHtml(message, index, latest)}</div></article>`;
    }

    // Spoken versions from the Read response button: quiet row, Copy only, never "final".
    const TURN_HARNESS_LABELS = { pi: 'Pi', cursor: 'Cursor', codex: 'Codex', claude_code: 'Claude Code', kilocode: 'Kilo' };
    // Small label under a sent message: which tier + harness/model Auto picked (or the fixed choice).
    function turnRouteLabelHtml(message) {
        // 2026-10-22 uiclean: label no longer rendered under user bubbles (overlapped next row as stray "Fixed: Codex" text).
        const routes = context.turnRoutes || {};
        const entry = message.chat_row_id != null ? routes[String(message.chat_row_id)] : null;
        if (!entry || !entry.backend) return '';
        const harness = entry.provider === 'kilocode' && entry.backend === 'pi' ? 'Kilo' : TURN_HARNESS_LABELS[entry.backend] || entry.backend;
        const model = entry.model && entry.model.toLowerCase() !== 'auto' ? shortModelName(entry.model) : '';
        const tier = entry.complexity ? entry.complexity[0].toUpperCase() + entry.complexity.slice(1) : '';
        const auto = entry.route_mode !== 'manual';
        const text = auto ? `Auto: ${[tier, harness, model].filter(Boolean).join(', ')}` : `Fixed: ${[harness, model].filter(Boolean).join(' · ')}`;
        const full = `${auto ? 'Auto picked' : 'Fixed choice'}: ${tier ? `${tier} complexity, ` : ''}${harness}${entry.model ? ` with ${entry.model}` : ''}${entry.provider ? ` (${entry.provider})` : ''}`;
        const skillIds = Array.isArray(entry.skill_ids) ? entry.skill_ids.filter(Boolean) : [];
        const skillNames = skillIds.map((id) => skillDisplayName(id));
        const skillNote = skillNames.length
            ? ` <span class="turn-skill-note" title="${actions.escapeHtml(`${entry.skills_auto ? 'Auto-matched' : 'Selected'} skills: ${skillNames.join(', ')}`)}">· ${actions.escapeHtml(skillNames.length > 2 ? `${skillNames.slice(0, 2).join(', ')} +${skillNames.length - 2}` : skillNames.join(', '))}</span>`
            : '';
        return `<div class="turn-route-label${auto ? ' auto' : ' fixed'}" title="${actions.escapeHtml(full)}">${actions.escapeHtml(text)}${skillNote}</div>`;
    }

    function spokenMessageHtml(message) {
        const assistantIdentity = window.DecisionsAssistantIdentity?.html() || '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
        const rowAttr = message.chat_row_id != null ? ` data-chat-row-id="${actions.escapeHtml(String(message.chat_row_id))}"` : '';
        return `<article class="studio-message assistant spoken-message" data-spoken-message${rowAttr}><div class="message-avatar" data-assistant-identity>${assistantIdentity}</div><div class="message-body"><div class="message-markdown">${assistantMarkdown(message.content || '')}</div><footer class="message-footer">${turnTimingHtml(message)}</footer></div></article>`;
    }

    // Append a spoken row in place (no full re-render) so the final reply's buttons stay put.
    function appendSpokenMessage(message, onCopy) {
        const list = el('message-list'); list.__lastRenderedHtml = null; // in-place edit: next full render must not be skipped
        if (!list) return null;
        const holder = document.createElement('div');
        holder.innerHTML = spokenMessageHtml(Object.assign({}, message, { spoken: true }));
        const node = holder.firstElementChild;
        const anchor = list.querySelector('[data-workflow-runner="live"]');
        list.insertBefore(node, anchor && anchor.parentElement === list ? anchor : null);
        return node;
    }

    function workflowSafeAssistantContent(value) {
        const text = String(value || '').trim();
        const low = text.toLowerCase();
        if (
            low.includes('this work runs on openrouter') ||
            low.includes('passed readiness but failed the actual work') ||
            low.includes('model request failed while trying to generate')
        ) {
            return 'The previous workflow attempt stopped before completion. The technical cause is preserved in the ticket Activity and audit history.';
        }
        return text;
    }

    function isDevelopmentConversationNoise(message, currentChat) {
        if (!currentChat?.project_id || String(message?.role || '').toLowerCase() !== 'user') return false;
        const text = String(message?.content || message?.input || '').trim().toLowerCase();
        return /^(?:hello(?:,? are you there)?|are you alive\??(?: hello\.)?|how(?: are| you) doing\??|yo,? what'?s up\??)$/.test(text);
    }

    function turnCommand(event) {
        const metadata = event?.metadata || {};
        const value = metadata.command || metadata.command_preview || metadata.cmd || '';
        if (Array.isArray(value))
            return value
                .map((part) => String(part))
                .join(' ')
                .trim();
        if (value) return String(value).trim();
        const summary = String(event?.summary || '').trim();
        return /^command:\s*/i.test(summary) ? summary.replace(/^command:\s*/i, '').trim() : '';
    }

    function completedTurnActionsHtml(events) {
        const rows = turnEventRowsHtml(events, { active: true });
        return rows ? `<div class="active-turn-history turn-event-stream" aria-label="Completed actions">${rows}</div>` : '';
    }

    function currentTurnAction(latest, fallbackLabel, fallbackDetail) {
        const type = String(latest?.event_type || '').toLowerCase();
        const status = String(latest?.status || '').toLowerCase();
        if (type !== 'tool_started' || !['running', 'queued', 'initializing'].includes(status)) {
            return [fallbackLabel, fallbackDetail];
        }
        if (isRoutineShellStep(latest)) return ['Running', 'commands…'];
        const toolName = String(latest?.metadata?.tool_name || latest?.tool_name || latest?.title || '').toLowerCase();
        if (/agent|reviewer|subagent/.test(toolName)) {
            const agent = turnEventAgentName(latest);
            if (/cursor/i.test(agent) || /cursor/.test(toolName)) return ['Cursor is working', ''];
            return ['Working with', agent];
        }        if (/pytest|test\b|vitest|jest|playwright test|cypress|lint|ruff/.test(`${toolName} ${turnCommand(latest)}`))
            return ['Running', 'tests', turnCommand(latest)];
        if (/replace_text|write_file|edit|patch/.test(toolName)) return ['Editing', 'files'];
        if (/run_command|terminal|shell|command/.test(toolName)) return ['Running', 'a command', turnCommand(latest)];
        if (/view_image|image|screenshot/.test(toolName)) return ['Viewing', 'an image'];
        if (/search_files|search|rg/.test(toolName)) return ['Searching', 'the project'];
        if (/read_file|list_files|read|list/.test(toolName)) return ['Reading', 'project files'];
        return [fallbackLabel, fallbackDetail];
    }

    function workflowDefinitionFor(event = {}) {
        const workflowId = Number(event.workflow_id || context.currentWorkflow?.id || 0);
        if (workflowId && Number(context.currentWorkflow?.id) === workflowId) return context.currentWorkflow;
        if (workflowId && Array.isArray(context.workflows)) {
            return context.workflows.find((item) => Number(item.id) === workflowId) || null;
        }
        return context.currentWorkflow || null;
    }

    function workflowStepStatuses(steps, run, event = {}) {
        const runStatus = String(run?.status || event.status || 'ready').toLowerCase();
        const currentStepId = Number(run?.current_step_id || run?.step_id || event.step_id || 0);
        const currentIndex = steps.findIndex((step) => Number(step.id) === currentStepId);
        const completed = ['completed', 'success', 'succeeded', 'done', 'cancelled', 'canceled'].includes(runStatus);
        const failed = ['failed', 'error'].includes(runStatus);
        return steps.map((step, index) => {
            if (completed) return { ...step, status: 'done' };
            if (!currentStepId && !runStatus) return { ...step, status: 'pending' };
            if (currentIndex < 0) {
                if (Number(step.id) === currentStepId) {
                    return { ...step, status: failed ? 'failed' : runStatus === 'waiting' || runStatus === 'paused' ? 'waiting' : 'running' };
                }
                return { ...step, status: 'pending' };
            }
            if (index < currentIndex) return { ...step, status: 'done' };
            if (index > currentIndex) return { ...step, status: 'pending' };
            if (failed) return { ...step, status: 'failed' };
            if (runStatus === 'waiting' || runStatus === 'paused') return { ...step, status: 'waiting' };
            return { ...step, status: 'running' };
        });
    }

    function workflowRunnerHtml({ workflow, run = null, event = {}, mode = 'live' } = {}) {
        const steps = actions.workflowStepList?.(workflow) || (Array.isArray(workflow?.steps) ? workflow.steps : []);
        if (!steps.length && !event.summary && !workflow?.name) return '';
        const namedSteps = steps.length
            ? steps
            : [{ id: event.step_id || 1, name: event.step_name || event.phase || 'Step', position: 0 }];
        const statusSteps = workflowStepStatuses(namedSteps, run, event);
        const count = statusSteps.length;
        const runStatus = String(run?.status || event.status || (mode === 'done' ? 'completed' : 'running')).toLowerCase();
        const activeIndex = Math.max(
            0,
            statusSteps.findIndex((step) => ['running', 'waiting', 'failed'].includes(String(step.status || '').toLowerCase()))
        );
        const title = workflow?.name || event.workflow_name || 'Workflow';
        const stepRoutes = run?.step_routes || {};
        const routeDataFor = (step) => stepRoutes[String(step.id)] || stepRoutes[step.id] || {};
        const routeFor = (step) => {
            const route = routeDataFor(step);
            return [route.backend || route.backend_id, route.model || step.model].filter(Boolean).join(' · ');
        };
        const activeStepId = statusSteps[activeIndex]?.id;
        const activityFor = (step) => {
            if (String(step.id) !== String(activeStepId)) return {};
            const recent = Array.isArray(run?.recent_activity) ? run.recent_activity : [];
            return recent.find((item) => !item.step_id || String(item.step_id) === String(step.id)) || run?.last_activity || {};
        };
        const activityText = (activity) => {
            const type = String(activity?.event_type || '').replaceAll('_', ' ');
            const message = String(activity?.message || '').trim();
            if (message === 'thinking_delta') return 'The model is reasoning and streaming its response.';
            if (message === 'turn_start') return 'The worker started a new reasoning turn.';
            if (message === 'turn_end') return 'The worker completed a reasoning turn.';
            if (message === 'tool_execution_update') return 'A tool execution update was received.';
            return message || (type ? `${type.charAt(0).toUpperCase()}${type.slice(1)}.` : 'No feedback has been recorded for this step yet.');
        };
        const outputFor = (step) => {
            const own = step.output || step.last_output || step.result || '';
            if (own) return String(own);
            return String(step.id) === String(activeStepId) ? String(event.summary || run?.last_output || run?.summary || '') : '';
        };
        const stepButtonAttrs = (step, index) => {
            const route = routeDataFor(step);
            const activity = activityFor(step);
            const reason = route.rationale || route.reason || activity.selection_reason || '';
            return `${workflowStepDataAttrs(step, index, { escapeHtml: actions.escapeHtml, route: routeFor(step), output: outputFor(step), selected: index === activeIndex })} data-step-position="${index + 1} of ${count}" data-step-backend="${actions.escapeHtml(route.backend || route.backend_id || activity.backend || '')}" data-step-model="${actions.escapeHtml(route.model || step.model || activity.model || '')}" data-step-complexity="${actions.escapeHtml(route.complexity || activity.complexity || '')}" data-step-source="${actions.escapeHtml(route.source || '')}" data-step-reason="${actions.escapeHtml(reason)}" data-step-feedback="${actions.escapeHtml(activityText(activity))}" data-step-feedback-at="${actions.escapeHtml(messageTime(activity.at))}"`;
        };
        const numbers = workflowStepNumbersHtml(statusSteps, {
            activeStepId,
            idPrefix: nextWorkflowLoopId(`thread-wf-steps-${String(run?.id || event.run_id || 'x')}-${mode}`),
            escapeHtml: actions.escapeHtml,
            routeFor,
            outputFor,
            ariaLabel: `${title} steps`
        });
        const runId = String(run?.id || event.run_id || '');
        const interaction = (context.controlState?.interactions || []).find((item) => String(item.run_id || '') === runId) || null;
        const interactionActions = Array.isArray(interaction?.allowed_actions) ? interaction.allowed_actions : [];
        const actionable = interactionActions.filter((action) => action !== 'feedback');
        const approval = run?.pending_route_approval || {};
        const waitingPrompt = interaction?.kind === 'provider_preflight' && (approval.name || approval.model)
            ? `Use ${approval.name || approval.model} for this step and continue the workflow.`
            : run?.worker_question || run?.waiting_prompt || 'Review the pending checkpoint before the workflow continues.';
        const checkpoint = ['waiting', 'paused'].includes(runStatus)
            ? `<section class="thread-workflow-checkpoint" aria-label="Workflow approval required">
                <div><strong>Approval required</strong><p>${actions.escapeHtml(waitingPrompt)}</p></div>
                ${actionable.length ? `<div class="thread-workflow-checkpoint-actions">${actionable.map((action, index) => `<button type="button" class="thread-workflow-checkpoint-action${index === 0 ? ' primary' : ''}${action === 'stop' ? ' danger' : ''}" data-workflow-interaction-token="${actions.escapeHtml(interaction.token)}" data-workflow-interaction-action="${actions.escapeHtml(action)}">${actions.escapeHtml(index === 0 && action !== 'stop' ? 'Approve and continue' : actions.actionLabel(action))}</button>`).join('')}</div>` : ''}
            </section>`
            : '';
        const currentStep = statusSteps[activeIndex];
        const currentRoute = routeDataFor(currentStep);
        const currentActivity = activityFor(currentStep);
        const currentReason = currentRoute.rationale || currentRoute.reason || currentActivity.selection_reason || '';
        const currentRouteLabel = routeFor(currentStep) || 'Route not recorded';
        const currentMeta = [currentRoute.complexity ? `${currentRoute.complexity} complexity` : '', currentRoute.source ? currentRoute.source.replaceAll('_', ' ') : ''].filter(Boolean);
        const feedback = `<section class="thread-workflow-feedback" data-thread-workflow-feedback aria-live="polite">
            <div class="thread-workflow-feedback-head"><div><span>Step ${activeIndex + 1} of ${count}</span><strong>${actions.escapeHtml(currentStep?.name || 'Current step')}</strong></div><em class="workflow-step-pill ${actions.escapeHtml(currentStep?.status || 'pending')}">${actions.escapeHtml(actions.statusLabel(currentStep?.status || 'pending'))}</em></div>
            <div class="thread-workflow-route"><strong>${actions.escapeHtml(currentRouteLabel)}</strong>${currentMeta.length ? `<span>${actions.escapeHtml(currentMeta.join(' · '))}</span>` : ''}</div>
            ${currentReason ? `<p>${actions.escapeHtml(currentReason)}</p>` : ''}
            <div class="thread-workflow-live-feedback"><span class="thread-status-dot"></span><div><strong>Live feedback</strong><p>${actions.escapeHtml(activityText(currentActivity))}</p></div>${currentActivity.at ? `<time datetime="${actions.escapeHtml(currentActivity.at)}">${actions.escapeHtml(messageTime(currentActivity.at))}</time>` : ''}</div>
        </section>`;
        const viewMode = 'loop';
        const ringPrefix = nextWorkflowLoopId(`thread-wf-${String(run?.id || event.run_id || 'x')}-${mode}`);
        const ring = workflowLoopHtml(statusSteps, {
            compact: true,
            activeStepId: statusSteps[activeIndex]?.id,
            idPrefix: ringPrefix,
            escapeHtml: actions.escapeHtml,
            statusLabel: actions.statusLabel,
            coreLabel: interaction ? 'Approval' : actions.statusLabel(runStatus),
            coreCaption: count === 1 ? 'step' : 'steps',
            mapRole: 'tablist',
            ariaLabel: `${title} step loop`,
            emptyHtml: '',
            buttonAttrs: stepButtonAttrs
        });
        return `<article class="studio-message workflow-runner" data-workflow-runner="${mode}" data-run-id="${actions.escapeHtml(String(run?.id || event.run_id || ''))}">
            <div class="thread-workflow-runner ${mode}" data-view="${viewMode}" data-status="${actions.escapeHtml(runStatus)}" role="region" aria-label="${actions.escapeHtml(title)} workflow" aria-live="${mode === 'live' ? 'polite' : 'off'}">
                <div class="thread-workflow-runner-head"><strong>${actions.escapeHtml(title)}</strong><div class="thread-workflow-runner-tools"><span class="thread-workflow-status-badge ${actions.escapeHtml(runStatus)}">${actions.escapeHtml(interaction ? 'Approval required' : actions.statusLabel(runStatus))}</span>${workflowViewSwitchHtml(viewMode, { attr: 'data-thread-workflow-view', className: 'workflow-view-switch thread-workflow-view-switch' })}</div></div>
                ${checkpoint}
                <div class="thread-workflow-ring">${ring}</div>
                <div class="thread-workflow-steps">${numbers}</div>
                ${feedback}
            </div>
        </article>`;
    }

    function openWorkflowStepDialog(button) {
        const dialog = el('thread-workflow-step-dialog');
        if (!dialog) return;
        if (dialog.dataset.backdropBound !== '1') {
            dialog.dataset.backdropBound = '1';
            dialog.addEventListener('click', (event) => {
                if (event.target === dialog) dialog.close('cancel');
            });
        }
        el('thread-workflow-step-position').textContent = `Step ${button.dataset.stepPosition || ''}`.trim();
        el('thread-workflow-step-title').textContent = button.dataset.stepName || 'Workflow step';
        const meta = [
            button.dataset.stepStatus ? actions.statusLabel(button.dataset.stepStatus) : '',
            button.dataset.stepBackend,
            button.dataset.stepModel,
            button.dataset.stepComplexity ? `${button.dataset.stepComplexity} complexity` : '',
            button.dataset.stepSource ? button.dataset.stepSource.replaceAll('_', ' ') : ''
        ].filter(Boolean);
        el('thread-workflow-step-meta').innerHTML = meta.map((item) => `<span>${actions.escapeHtml(item)}</span>`).join('');
        el('thread-workflow-step-reason').textContent = button.dataset.stepReason || 'No model selection reason was recorded for this step.';
        el('thread-workflow-step-instructions').textContent = button.dataset.stepDetail || 'No step instructions are available.';
        const feedback = [button.dataset.stepFeedback, button.dataset.stepOutput].filter(Boolean).join('\n\n');
        el('thread-workflow-step-feedback').textContent = feedback || 'No feedback has been recorded for this step yet.';
        dialog.showModal();
        dialog.querySelector('form')?.scrollTo(0, 0);
    }

    function bindWorkflowStepRails(root) {
        root.querySelectorAll('[data-workflow-runner]').forEach((runner) => {
            runner.querySelectorAll('[data-thread-workflow-view]').forEach((toggle) => {
                toggle.addEventListener('click', () => {
                    const next = toggle.dataset.threadWorkflowView === 'list' ? 'list' : 'loop';
                    document.querySelectorAll('.thread-workflow-runner').forEach((card) => card.setAttribute('data-view', next));
                    document.querySelectorAll('[data-thread-workflow-view]').forEach((item) => item.setAttribute('aria-pressed', String(item.dataset.threadWorkflowView === next)));
                });
            });
            runner.querySelectorAll('[data-workflow-interaction-action]').forEach((button) => {
                button.addEventListener('click', async () => {
                    const label = button.textContent;
                    button.disabled = true;
                    button.textContent = button.dataset.workflowInteractionAction === 'stop' ? 'Stopping...' : 'Approving...';
                    await actions.resolveInteraction(button.dataset.workflowInteractionToken, button.dataset.workflowInteractionAction);
                    if (button.isConnected) {
                        button.disabled = false;
                        button.textContent = label;
                    }
                });
            });
            bindWorkflowStepNumbers(runner, { escapeHtml: actions.escapeHtml });
            runner.querySelectorAll('[data-wf-step]').forEach((button) => button.addEventListener('click', () => openWorkflowStepDialog(button)));
        });
    }

    function liveWorkflowRunnerHtml() {
        const run = context.currentRun;
        const workflow = context.currentWorkflow;
        if (!run || run.direct || !workflow || !actions.isActiveAgentRun?.(run)) return '';
        return workflowRunnerHtml({ workflow, run, mode: 'live' });
    }

    function workflowMessageHtml(message, index = 0, messages = []) {
        const event = message.workflow_event || {};
        if (actions.isActiveAgentRun?.(context.currentRun) && context.currentWorkflow && !context.currentRun?.direct) {
            return '';
        }
        const eventRunId = Number(event.run_id || 0);
        if (eventRunId) {
            let lastIndex = -1;
            messages.forEach((item, itemIndex) => {
                if ((item.role || '') === 'workflow' && Number(item.workflow_event?.run_id || 0) === eventRunId) lastIndex = itemIndex;
            });
            if (lastIndex >= 0 && lastIndex !== index) return '';
        }
        const workflow = workflowDefinitionFor(event);
        const matchingRun =
            context.currentRun && !context.currentRun.direct && Number(actions.workflowRunId?.(context.currentRun) || 0) === eventRunId
                ? context.currentRun
                : { status: event.status, current_step_id: event.step_id, step_id: event.step_id };
        const done = !['initializing', 'queued', 'running', 'waiting', 'paused'].includes(String(matchingRun.status || event.status || '').toLowerCase());
        return workflowRunnerHtml({
            workflow: workflow || { name: event.workflow_name || 'Workflow', steps: [] },
            run: matchingRun,
            event,
            mode: done ? 'done' : 'live'
        });
    }

    function durableTurnActivity(chat) {
        const active = chat.active_turn;
        const run = context.currentRun || {};
        const runStatus = String(run.status || '').toLowerCase();
        const runActive = ['initializing', 'queued', 'running'].includes(runStatus);
        const activeEvents = Array.isArray(active?.events) ? active.events : [];
        if (!runActive) return '';
        const reportedPaths = [
            ...gitStatusEntries(run.git_status_after || run.git_status_current),
            ...gitStatusEntries((run.latest_backend_handoff || {}).git_status_after)
        ];
        const paths = Array.from(
            new Set(
                reportedPaths
                    .map((entry) =>
                        String(entry || '')
                            .replace(/^\s*[MADRCU?!]{1,2}\s+/, '')
                            .trim()
                    )
                    .filter(Boolean)
            )
        );
        const latest =
            activeEvents
                .slice()
                .reverse()
                .find((event) => !/^(?:request received|qa message)$/i.test(String(event.title || event.event_type || ''))) || null;
        const activityStatus = String(run.activity_status || '').toLowerCase();
        const signal = `${run.activity || ''} ${latest?.title || ''} ${latest?.event_type || ''} ${latest?.summary || ''}`.toLowerCase();
        const label =
            activityStatus === 'thinking'
                ? 'Thinking'
                : activityStatus === 'updating'
                  ? 'Updating'
                  : activityStatus === 'working'
                    ? 'Working'
                    : ['initializing', 'queued'].includes(runStatus)
                      ? 'Thinking'
                      : paths.length || /updat|writ|edit|patch|file|implement|code/.test(signal)
                        ? 'Updating'
                        : /think|analy|reason|plan|review|inspect|research/.test(signal)
                          ? 'Thinking'
                          : 'Working';
        const detail = label === 'Updating' ? 'files' : label === 'Thinking' ? 'through the request' : 'on the task';
        const [currentLabel, currentDetail, currentCommand] = currentTurnAction(latest, label, detail);
        const draft = String(run.streamed_output || '').trim();
        const assistantIdentity = window.DecisionsAssistantIdentity?.html() || '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
        return `<article class="studio-message active-turn"><div class="message-avatar" data-assistant-identity>${assistantIdentity}</div><div class="message-body">
            ${draft ? `<div class="active-turn-draft message-markdown">${assistantMarkdown(draft)}</div>` : ''}
            ${completedTurnActionsHtml(activeEvents)}
            <div class="active-turn-summary" role="status" aria-live="polite"><div class="loading-message ${label.toLowerCase()}"><span>${currentLabel} <small>${currentDetail}</small></span></div>${currentCommand ? `<code class="active-command" title="${actions.escapeHtml(currentCommand)}">${actions.escapeHtml(currentCommand)}</code>` : ''}</div>
        </div></article>`;
    }

    function syncDurableTurnActivity() {
        if (!context.currentChat) return;
        const list = el('message-list'); list.__lastRenderedHtml = null; // in-place edit: next full render must not be skipped
        const current = list.querySelector('.active-turn');
        const html = durableTurnActivity(context.currentChat);
        if (!html) {
            if (current) current.remove();
        } else if (current) {
            current.outerHTML = html;
        } else {
            list.insertAdjacentHTML('beforeend', html);
        }
        syncLiveWorkflowRunner();
    }

    function syncLiveWorkflowRunner() {
        if (!context.currentChat) return;
        const list = el('message-list'); list.__lastRenderedHtml = null; // in-place edit: next full render must not be skipped
        const current = list.querySelector('[data-workflow-runner="live"]');
        const html = liveWorkflowRunnerHtml();
        if (!html) {
            if (current) current.remove();
            return;
        }
        if (current) {
            current.outerHTML = html;
            bindWorkflowStepRails(list);
            return;
        }
        const active = list.querySelector('.active-turn');
        if (active) active.insertAdjacentHTML('beforebegin', html);
        else list.insertAdjacentHTML('beforeend', html);
        bindWorkflowStepRails(list);
    }

    function renderConversation(chat) {
        el('studio-empty').classList.add('hidden');
        const list = el('message-list');
        const messages = (chat.messages || [])
            .filter((message) => !isDevelopmentConversationNoise(message, chat))
            .slice(-140);
        spokenBySource = new Map();
        let previousReplyRowId = null;
        messages.forEach((message) => {
            if ((message.role || 'assistant') !== 'assistant') return;
            if (!message.spoken) {
                if (message.chat_row_id != null) previousReplyRowId = message.chat_row_id;
                return;
            }
            // Older servers omit spoken_source_row_id: link to the reply just above.
            const source = message.spoken_source_row_id ?? previousReplyRowId;
            if (source != null && !spokenBySource.has(String(source))) spokenBySource.set(String(source), message.content || '');
        });
        let latestAssistantIndex = -1;
        messages.forEach((message, index) => {
            if ((message.role || 'assistant') === 'assistant' && !message.spoken && !ticketMovePresentation(message)) latestAssistantIndex = index;
        });
        const turnsByRow = new Map((chat.turns || []).map((turn) => [String(turn.turn_id), turn]));
        const durableActivityKeys = durableActivityDedupeKeys(chat.turns || []);
        const instructionsByRow = new Map(
            messages
                .filter((message) => message.role === 'user' && message.chat_row_id != null)
                .map((message) => [String(message.chat_row_id), userMessagePresentation(message).content])
        );
        const nextHtml = `${messages
            .map((message, index) => {
                const rowId = message.chat_row_id == null ? '' : String(message.chat_row_id);
                return messageHtml(message, index, latestAssistantIndex, turnsByRow.get(rowId), instructionsByRow.get(rowId) || '', durableActivityKeys, messages);
            })
            .join('')}${liveWorkflowRunnerHtml()}${durableTurnActivity(chat)}`;
        // 2026-10-22: background polls re-render the same transcript; skip identical rebuilds so the
        // assistant avatar <video>/<img> elements are not re-created (they visibly reset each poll).
        if (list.__lastRenderedHtml === nextHtml && list.childElementCount && !list.classList.contains('hidden')) return;
        list.__lastRenderedHtml = nextHtml;
        list.innerHTML = nextHtml;
        list.classList.remove('hidden');
        actions.bindConversationActions(list, messages);
        bindWorkflowStepRails(list);
        const conversation = el('conversation');
        const runner = list.querySelector('[data-workflow-runner="live"]');
        const runId = runner?.dataset.runId || '';
        if (runId && list.dataset.workflowFocusRunId !== runId) {
            list.dataset.workflowFocusRunId = runId;
            window.setTimeout(() => {
                if (!runner.isConnected) return;
                conversation.scrollTop += runner.getBoundingClientRect().top - conversation.getBoundingClientRect().top - 8;
            }, 80);
        } else if (!runner) {
            window.requestAnimationFrame(() => {
                conversation.scrollTop = conversation.scrollHeight;
            });
        }
    }
    return {
        appendSpokenMessage,
        playSpokenButtonHtml,
        assistantMarkdown,
        gitStatusEntries,
        messageTime,
        renderConversation,
        skillIconSvg,
        syncDurableTurnActivity
    };
}
