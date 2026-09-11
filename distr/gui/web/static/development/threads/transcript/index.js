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

    function messageActionsHtml(message, index, latestAssistant) {
        const copyIcon =
            '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8" height="8" rx="1.5"></rect><path d="M3 10V4.5A1.5 1.5 0 0 1 4.5 3H10"></path></svg>';
        const forkIcon =
            '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="4" cy="3" r="1.5"></circle><circle cx="12" cy="5" r="1.5"></circle><circle cx="12" cy="12" r="1.5"></circle><path d="M4 4.5v3A4.5 4.5 0 0 0 8.5 12h2M5.5 5h5"></path></svg>';
        return `<footer class="message-footer"><div class="message-actions" aria-label="Response actions"><button type="button" data-copy-message="${index}" aria-label="Copy response" title="Copy response to clipboard">${copyIcon}</button>${latestAssistant ? `<button type="button" data-fork-current aria-label="Fork thread" title="Continue from this transcript in a new independent thread. This does not create a Git branch.">${forkIcon}</button>` : ''}</div>${turnTimingHtml(message)}</footer>`;
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
        if (/development_agent/.test(signal)) return 'Development agent';
        if (/tool_compiler/.test(signal)) return 'Tool builder';
        return String(event?.title || 'Development agent');
    }

    function turnEventPresentation(event) {
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
        const kind = turnEventKind(event);
        const command = turnCommand(event);
        const summary = String(event?.summary || event?.title || '').trim();
        let detail = String(event?.detail || '').trim();
        let title = String(event?.title || 'Development action');
        let preview = summary;
        let stateLabel = waiting ? 'needs attention' : failed ? 'failed' : running ? 'running' : 'finished';
        if (kind === 'agent') {
            title = `${turnEventAgentName(event)} ${running ? 'started' : waiting ? 'needs attention' : failed ? 'failed' : 'finished'}`;
            if (String(event?.metadata?.tool_name || '').toLowerCase() === 'development_agent') preview = '';
        } else if (kind === 'tests') {
            title = running ? 'Running tests' : failed ? 'Tests failed' : 'Tests passed';
            preview = command || summary;
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
        if (detail === preview || detail === command) detail = '';
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

    function turnEventRowsHtml(events, { includeRunning = false, active = false } = {}) {
        return (events || [])
            .map(turnEventPresentation)
            .filter((item) => item && (includeRunning || !item.running))
            .map((item) => {
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

    function messageHtml(message, index, latestAssistantIndex, turnActivity = null, instruction = '', durableActivityKeys = new Set()) {
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
            const workflow = message.workflow_event || {};
            return `<article class="studio-message"><div class="message-avatar">AI</div><div class="message-body">
                <div class="message-workflow"><span class="workflow-label">${actions.escapeHtml(workflow.phase || workflow.type || 'Workflow')}</span><strong>${actions.escapeHtml(workflow.summary || message.content || workflow.workflow_name || 'Workflow update')}</strong><small>${actions.escapeHtml(actions.statusLabel(workflow.status))}</small></div>
                </div></article>`;
        }
        const ticketMove = ticketMovePresentation(message);
        if (ticketMove) return ticketMoveHtml(message, ticketMove);
        const latest = index === latestAssistantIndex;
        const content = workflowSafeAssistantContent(message.content || '');
        return `<article class="studio-message assistant"><div class="message-avatar">AI</div><div class="message-body">${completedTurnActivityHtml(turnActivity)}<div class="message-markdown">${assistantMarkdown(content)}</div>${latest ? turnChangesHtml() : ''}${messageActionsHtml(message, index, latest)}</div></article>`;
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
        const toolName = String(latest?.metadata?.tool_name || latest?.tool_name || latest?.title || '').toLowerCase();
        if (/agent|reviewer|subagent/.test(toolName)) return ['Waiting for', turnEventAgentName(latest)];
        if (/pytest|test\b|vitest|jest|playwright test|cypress|lint|ruff/.test(`${toolName} ${turnCommand(latest)}`))
            return ['Running', 'tests', turnCommand(latest)];
        if (/replace_text|write_file|edit|patch/.test(toolName)) return ['Editing', 'files'];
        if (/run_command|terminal|shell|command/.test(toolName)) return ['Running', 'a command', turnCommand(latest)];
        if (/view_image|image|screenshot/.test(toolName)) return ['Viewing', 'an image'];
        if (/search_files|search|rg/.test(toolName)) return ['Searching', 'the project'];
        if (/read_file|list_files|read|list/.test(toolName)) return ['Reading', 'project files'];
        return [fallbackLabel, fallbackDetail];
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
        return `<article class="studio-message active-turn"><div class="message-body">
            ${draft ? `<div class="active-turn-draft message-markdown">${assistantMarkdown(draft)}</div>` : ''}
            ${completedTurnActionsHtml(activeEvents)}
            <div class="active-turn-summary" role="status" aria-live="polite"><div class="loading-message ${label.toLowerCase()}"><span>${currentLabel} <small>${currentDetail}</small></span></div>${currentCommand ? `<code class="active-command" title="${actions.escapeHtml(currentCommand)}">${actions.escapeHtml(currentCommand)}</code>` : ''}</div>
        </div></article>`;
    }

    function syncDurableTurnActivity() {
        if (!context.currentChat) return;
        const list = el('message-list');
        const current = list.querySelector('.active-turn');
        const html = durableTurnActivity(context.currentChat);
        if (!html) {
            if (current) current.remove();
            return;
        }
        if (current) current.outerHTML = html;
        else list.insertAdjacentHTML('beforeend', html);
    }

    function renderConversation(chat) {
        el('studio-empty').classList.add('hidden');
        const list = el('message-list');
        const messages = (chat.messages || [])
            .filter((message) => !isDevelopmentConversationNoise(message, chat))
            .slice(-140);
        let latestAssistantIndex = -1;
        messages.forEach((message, index) => {
            if ((message.role || 'assistant') === 'assistant' && !ticketMovePresentation(message)) latestAssistantIndex = index;
        });
        const turnsByRow = new Map((chat.turns || []).map((turn) => [String(turn.turn_id), turn]));
        const durableActivityKeys = durableActivityDedupeKeys(chat.turns || []);
        const instructionsByRow = new Map(
            messages
                .filter((message) => message.role === 'user' && message.chat_row_id != null)
                .map((message) => [String(message.chat_row_id), userMessagePresentation(message).content])
        );
        list.innerHTML = `${messages
            .map((message, index) => {
                const rowId = message.chat_row_id == null ? '' : String(message.chat_row_id);
                return messageHtml(message, index, latestAssistantIndex, turnsByRow.get(rowId), instructionsByRow.get(rowId) || '', durableActivityKeys);
            })
            .join('')}${durableTurnActivity(chat)}`;
        list.classList.remove('hidden');
        actions.bindConversationActions(list, messages);
        const conversation = el('conversation');
        window.requestAnimationFrame(() => {
            conversation.scrollTop = conversation.scrollHeight;
        });
    }
    return {
        gitStatusEntries,
        messageTime,
        renderConversation,
        skillIconSvg,
        syncDurableTurnActivity
    };
}
