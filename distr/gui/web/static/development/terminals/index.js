// Owns terminals rendering, interactions, and private view state.
export function createTerminals({ context, actions, el, token }) {
    const state = {
        terminalBoardKey: '',
        terminalProjectId: null,
        terminalProjectDetails: {},
        terminalSessions: {},
        terminalActiveSessionId: '',
        terminalStatusSyncing: false,
        terminalPolling: null,
        terminalViews: new Map(),
        terminalCommandClipboard: ''
    };
    function terminalBoardForProject(projectId) {
        return context.boards.find((board) => Number(board.project_id) === Number(projectId)) || null;
    }

    function getTerminalSessions(projectId) {
        return state.terminalSessions[String(projectId)] || [];
    }

    function primeTerminalSelection({ projectId, boardKey }) {
        state.terminalProjectId = Number(projectId) || null;
        state.terminalBoardKey = boardKey || '';
    }

    function startTerminalPolling() {
        if (state.terminalPolling) return;
        state.terminalPolling = window.setInterval(() => {
            if (document.visibilityState === 'visible') syncTerminalStatuses();
        }, 2000);
    }

    function leave() {
        window.clearInterval(state.terminalPolling);
        state.terminalPolling = null;
        closeTerminalViews();
    }

    function terminalCommands(value) {
        return String(value || '')
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);
    }

    function formatTerminalMemory(bytes) {
        const value = Number(bytes || 0);
        if (value <= 0) return 'Memory unavailable';
        const megabytes = value / (1024 * 1024);
        if (megabytes < 1024) return `Memory ${megabytes < 10 ? megabytes.toFixed(1) : Math.round(megabytes)} MB`;
        return `Memory ${(megabytes / 1024).toFixed(1)} GB`;
    }

    function terminalProjectActionIcon(action, busy) {
        if (busy) return '<svg class="terminal-action-spinner" aria-hidden="true" viewBox="0 0 20 20"><circle cx="10" cy="10" r="6.5"></circle></svg>';
        if (action === 'stop') return '<svg aria-hidden="true" viewBox="0 0 20 20"><rect x="6" y="6" width="8" height="8" rx="1.5"></rect></svg>';
        return '<svg aria-hidden="true" viewBox="0 0 20 20"><path d="M7 5.5v9l7-4.5z"></path></svg>';
    }

    function syncTerminalCommandLines() {
        const commands = Array.from(el('terminal-command-rows').querySelectorAll('input'))
            .map((input) => input.value.trim())
            .filter(Boolean);
        el('terminal-command-lines').value = commands.join('\n');
        return commands;
    }

    function renderTerminalCommandRows(value) {
        const commands = Array.isArray(value) ? value : terminalCommands(value);
        const rows = commands.length ? commands : [''];
        el('terminal-command-rows').innerHTML = rows
            .map(
                (command, index) =>
                    `<div class="terminal-command-row"><span>${String(index + 1).padStart(2, '0')}</span><input type="text" value="${actions.escapeHtml(command)}" aria-label="Terminal ${index + 1} command" placeholder="Command for terminal ${index + 1}"><button type="button" data-terminal-command-remove="${index}" aria-label="Remove terminal ${index + 1}" title="Remove terminal">×</button></div>`
            )
            .join('');
        syncTerminalCommandLines();
    }

    function addTerminalCommandRow() {
        const inputs = Array.from(el('terminal-command-rows').querySelectorAll('input'));
        if (inputs.length && !inputs[inputs.length - 1].value.trim()) {
            inputs[inputs.length - 1].focus();
            return;
        }
        const commands = inputs.map((input) => input.value);
        commands.push('');
        renderTerminalCommandRows(commands);
        const updatedInputs = el('terminal-command-rows').querySelectorAll('input');
        updatedInputs[updatedInputs.length - 1]?.focus();
    }

    async function copyTerminalCommands() {
        const commands = syncTerminalCommandLines();
        if (!commands.length) {
            actions.toast('There are no terminal commands to copy.', 'error');
            return;
        }
        const commandText = commands.join('\n');
        state.terminalCommandClipboard = commandText;
        try {
            await navigator.clipboard.writeText(commandText);
        } catch (_) {
            // The in-app clipboard still supports copying between terminal projects.
        }
        actions.toast(`${commands.length} terminal command${commands.length === 1 ? '' : 's'} copied.`);
    }

    async function pasteTerminalCommands() {
        let commandText = state.terminalCommandClipboard;
        if (!commandText) {
            try {
                commandText = await navigator.clipboard.readText();
            } catch (_) {
                // Browser clipboard permission is optional; use the in-app copy instead.
            }
        }
        const commands = terminalCommands(commandText);
        if (!commands.length) {
            actions.toast('No terminal commands are available to paste.', 'error');
            return;
        }
        renderTerminalCommandRows(commands);
        const inputs = el('terminal-command-rows').querySelectorAll('input');
        inputs[inputs.length - 1]?.focus();
        actions.toast(`${commands.length} terminal command${commands.length === 1 ? '' : 's'} pasted. Save to keep them.`);
    }

    function removeTerminalCommandRow(index) {
        const commands = Array.from(el('terminal-command-rows').querySelectorAll('input')).map((input) => input.value);
        commands.splice(Number(index), 1);
        renderTerminalCommandRows(commands);
    }

    function closeTerminalViews() {
        state.terminalViews.forEach((view) => {
            try {
                view.socket?.close();
            } catch (_) {
                /* already closed */
            }
            try {
                view.resizeObserver?.disconnect();
            } catch (_) {
                /* optional */
            }
            try {
                view.linkProvider?.dispose();
            } catch (_) {
                /* disposed with terminal */
            }
            try {
                view.term?.dispose();
            } catch (_) {
                /* optional */
            }
        });
        state.terminalViews.clear();
    }

    function openTerminalUrl(value) {
        try {
            const url = new URL(String(value || ''));
            if (!['http:', 'https:'].includes(url.protocol)) return;
            const opened = window.open(url.href, '_blank', 'noopener,noreferrer');
            if (opened) opened.opener = null;
        } catch (_) {
            // Ignore malformed or unsupported terminal links.
        }
    }

    function terminalUrlLinkProvider(term) {
        return {
            provideLinks(lineNumber, callback) {
                const line = term.buffer.active.getLine(Number(lineNumber) - 1);
                const text = line?.translateToString(true) || '';
                const links = [];
                const matcher = /https?:\/\/[^\s<>"'`]+/gi;
                for (const match of text.matchAll(matcher)) {
                    const url = match[0].replace(/[),.;!?]+$/, '');
                    if (!url) continue;
                    const start = Number(match.index || 0) + 1;
                    links.push({
                        range: {
                            start: { x: start, y: lineNumber },
                            end: { x: start + url.length - 1, y: lineNumber }
                        },
                        text: url,
                        activate: (_event, link) => openTerminalUrl(link),
                        decorations: { pointerCursor: true, underline: true }
                    });
                }
                callback(links.length ? links : undefined);
            }
        };
    }

    async function loadProjectTerminalSessions(projectId) {
        const [startup, shell] = await Promise.all([
            actions.api(`/projects/${Number(projectId)}/startup-sessions`),
            actions.api(`/projects/${Number(projectId)}/shell-terminal`).catch(() => ({ sessions: [] }))
        ]);
        const sessions = [...(startup.sessions || []), ...(shell.sessions || [])];
        state.terminalSessions[String(projectId)] = sessions;
        return sessions;
    }

    function terminalManagedSessionKey(sessions) {
        return (sessions || [])
            .filter((session) => !String(session.process_id || session.terminal_id || '').startsWith('discovered:'))
            .map((session) => `${session.process_id || session.terminal_id || ''}:${session.pid || ''}:${session.status || 'running'}`)
            .sort()
            .join('|');
    }

    async function syncTerminalStatuses() {
        if (state.terminalStatusSyncing || !['terminals_home', 'terminals'].includes(context.workspaceMode)) return;
        const projectIds = context.projects.map((project) => Number(project.id)).filter(Boolean);
        if (!projectIds.length) return;
        state.terminalStatusSyncing = true;
        try {
            const data = await actions.api(`/projects/terminal-status?project_ids=${projectIds.join(',')}`);
            const statuses = data.projects || {};
            let refreshOpenProject = false;
            projectIds.forEach((projectId) => {
                const previous = state.terminalSessions[String(projectId)] || [];
                const managed = statuses[String(projectId)]?.sessions || [];
                const changed = terminalManagedSessionKey(previous) !== terminalManagedSessionKey(managed);
                if (!changed) return;
                state.terminalSessions[String(projectId)] = managed;
                if (context.workspaceMode === 'terminals_home') paintTerminalProjectCard(projectId);
                if (context.workspaceMode === 'terminals' && Number(state.terminalProjectId) === projectId) refreshOpenProject = true;
            });
            if (refreshOpenProject) await refreshTerminalSessions();
        } catch (_) {
            const fallbackIds = context.workspaceMode === 'terminals' && state.terminalProjectId ? [Number(state.terminalProjectId)] : projectIds;
            await Promise.allSettled(
                fallbackIds.map(async (projectId) => {
                    await loadProjectTerminalSessions(projectId);
                    if (context.workspaceMode === 'terminals_home') paintTerminalProjectCard(projectId);
                    if (context.workspaceMode === 'terminals' && Number(state.terminalProjectId) === projectId) renderTerminalWorkspace();
                })
            );
        } finally {
            state.terminalStatusSyncing = false;
        }
    }

    async function loadTerminalProjectConfiguration(project) {
        if (!project || Object.prototype.hasOwnProperty.call(project, 'startup_instructions')) return project;
        project._terminalConfigError = '';
        try {
            const cached = state.terminalProjectDetails[String(project.id)];
            const detail = cached || (await actions.api(`/projects/${Number(project.id)}`));
            state.terminalProjectDetails[String(project.id)] = detail;
            project.startup_instructions = detail.startup_instructions || '';
            return project;
        } catch (error) {
            project._terminalConfigError = error?.message || 'Could not load commands';
            throw error;
        }
    }

    function terminalProjectCard(project) {
        const sessions = state.terminalSessions[String(project.id)] || [];
        const count = sessions.length;
        const commandsLoaded = Object.prototype.hasOwnProperty.call(project, 'startup_instructions');
        const commandsFailed = Boolean(project._terminalConfigError);
        const sessionCommands = sessions.map((session) => String(session.command || '').trim()).filter(Boolean);
        const configuredCommands = commandsLoaded ? terminalCommands(project.startup_instructions) : [];
        const commands = sessionCommands.length ? sessionCommands : configuredCommands;
        const starting = sessions.some(
            (session) => String(session.status || '').toLowerCase() === 'starting' || String(session.process_id || '').startsWith('starting:')
        );
        const stateLabel = starting ? 'Starting' : count ? 'Running' : 'Stopped';
        const memoryBytes = sessions.reduce((total, session) => total + Number(session.memory_bytes || 0), 0);
        const metric = count
            ? memoryBytes > 0
                ? formatTerminalMemory(memoryBytes)
                : `${count} running process${count === 1 ? '' : 'es'}`
            : commandsLoaded
              ? `${configuredCommands.length} configured command${configuredCommands.length === 1 ? '' : 's'}`
              : commandsFailed
                ? 'Commands unavailable'
                : 'Loading commands';
        const commandRows = commands.length
            ? commands
                  .map(
                      (command) =>
                          `<span class="terminal-project-command"><i aria-hidden="true">$</i><code title="${actions.escapeHtml(command)}">${actions.escapeHtml(command)}</code></span>`
                  )
                  .join('')
            : `<span class="terminal-project-command terminal-project-command-empty"><i aria-hidden="true">$</i><code>${commandsLoaded ? 'No commands configured' : commandsFailed ? 'Could not load commands' : 'Loading commands...'}</code></span>`;
        const stateClass = starting ? ' starting' : count ? '' : ' stopped';
        const action = count ? 'stop' : 'start';
        const actionLabel = count ? 'Stop' : 'Start';
        const actionReady = count > 0 || commandsLoaded || commandsFailed;
        return `<article class="terminal-project-card${count ? ' running' : ''}${starting ? ' starting' : ''}" data-terminal-project="${Number(project.id)}">
            <header class="terminal-project-titlebar">
                <span class="terminal-project-icon" aria-hidden="true"><svg viewBox="0 0 18 18"><rect x="1.5" y="2.5" width="15" height="13" rx="2"></rect><path d="m5 7 2 2-2 2M9 11h4"></path></svg></span>
                <strong>${actions.escapeHtml(project.name || 'Untitled project')}</strong>
                <span class="terminal-project-controls"><i class="terminal-state${stateClass}" role="img" aria-label="${stateLabel}" title="${stateLabel}"></i><button type="button" class="terminal-project-action ${action}" data-terminal-project-action="${action}" aria-label="${actionLabel} terminals for ${actions.escapeHtml(project.name || 'Untitled project')}" title="${commandsFailed && !count ? 'Retry loading commands' : `${actionLabel} terminals`}"${actionReady ? '' : ' disabled'}>${terminalProjectActionIcon(action, !actionReady)}</button></span>
            </header>
            <button type="button" class="terminal-project-screen" data-terminal-project-open aria-label="Open terminals for ${actions.escapeHtml(project.name || 'Untitled project')}">
                ${commandRows}
            </button>
            <footer class="terminal-project-footer">
                <span class="terminal-project-metric">${actions.escapeHtml(metric)}</span>
                <button type="button" class="terminal-project-open" data-terminal-project-open>Open <i aria-hidden="true">&#8594;</i></button>
            </footer>
        </article>`;
    }

    async function setTerminalProjectRunning(projectId, shouldRun, button) {
        const project = actions.projectById(projectId);
        if (!project || button?.disabled) return;
        if (shouldRun && !Object.prototype.hasOwnProperty.call(project, 'startup_instructions')) {
            try {
                await loadTerminalProjectConfiguration(project);
            } catch (error) {
                actions.toast(error.message || 'Could not load the terminal commands.', 'error');
                return;
            }
        }
        const commands = terminalCommands(project.startup_instructions);
        if (shouldRun && !commands.length) {
            actions.toast('Add at least one terminal command first.', 'error');
            return;
        }
        if (button) {
            button.disabled = true;
            button.classList.add('busy');
            button.setAttribute('aria-label', shouldRun ? 'Starting terminals' : 'Stopping terminals');
            button.title = shouldRun ? 'Starting terminals' : 'Stopping terminals';
            button.innerHTML = terminalProjectActionIcon(shouldRun ? 'start' : 'stop', shouldRun);
        }
        try {
            if (shouldRun) {
                const result = await actions.terminalActionApi(`/projects/${Number(projectId)}/startup-terminals/start`, {
                    method: 'POST',
                    body: { commands, startup_instructions: commands.join('\n') }
                });
                if (result?.success === false) throw new Error(result.message || 'Could not start the project terminals.');
                const returnedSessions = result.sessions || [];
                state.terminalSessions[String(projectId)] = returnedSessions.length
                    ? returnedSessions
                    : commands.map((command, index) => ({
                          process_id: `starting:${projectId}:${index}`,
                          command,
                          status: 'starting'
                      }));
                actions.toast(result.message || 'Project terminals started.');
            } else {
                const displayedSessions = state.terminalSessions[String(projectId)] || [];
                const auxiliarySessions = displayedSessions.filter((session) => {
                    const processId = String(session.process_id || session.terminal_id || '');
                    const purpose = String(session.purpose || '').toLowerCase();
                    return (
                        processId &&
                        !processId.startsWith('starting:') &&
                        (purpose === 'cli_shell' || processId.startsWith('discovered:') || (purpose && purpose !== 'startup'))
                    );
                });
                const auxiliaryResults = await Promise.allSettled(
                    auxiliarySessions.map((session) =>
                        actions.terminalActionApi('/projects/kill-terminal', {
                            method: 'POST',
                            body: {
                                process_id: session.process_id || session.terminal_id,
                                pid: Number(session.pid || 0) || undefined,
                                project_id: Number(projectId)
                            }
                        })
                    )
                );
                const result = await actions.terminalActionApi(`/projects/${Number(projectId)}/startup-terminals/stop`, { method: 'POST', body: {} });
                if (result?.success === false) throw new Error(result.message || 'Could not stop the project terminals.');
                const auxiliaryStopped = auxiliaryResults.filter((settled) => settled.status === 'fulfilled' && settled.value?.success !== false).length;
                const stopped = Number(result.stopped || 0) + auxiliaryStopped;
                state.terminalSessions[String(projectId)] = [];
                actions.toast(
                    stopped
                        ? `Stopped ${stopped} terminal${stopped === 1 ? '' : 's'} for ${project.name || 'this project'}.`
                        : result.message || `No running terminals found for ${project.name || 'this project'}.`,
                    'success'
                );
            }
            paintTerminalProjectCard(projectId);
            window.setTimeout(
                async () => {
                    if (context.workspaceMode !== 'terminals_home') return;
                    try {
                        await loadProjectTerminalSessions(Number(projectId));
                    } catch (_) {
                        state.terminalSessions[String(projectId)] = [];
                    }
                    if (context.workspaceMode === 'terminals_home') paintTerminalProjectCard(projectId);
                },
                shouldRun ? 900 : 250
            );
        } catch (error) {
            actions.toast(error.message || `Could not ${shouldRun ? 'start' : 'stop'} the project terminals.`, 'error');
            paintTerminalProjectCard(projectId);
            window.setTimeout(async () => {
                try {
                    await loadProjectTerminalSessions(Number(projectId));
                } catch (_) {
                    /* Keep the last known state when reconciliation also fails. */
                }
                if (context.workspaceMode === 'terminals_home') paintTerminalProjectCard(projectId);
            }, 250);
        } finally {
            if (button?.isConnected && button.classList.contains('busy')) paintTerminalProjectCard(projectId);
        }
    }

    function bindTerminalProjectCard(card) {
        if (!card) return;
        card.querySelectorAll('[data-terminal-project-open]').forEach((button) =>
            button.addEventListener('click', () => openProjectTerminals(Number(card.dataset.terminalProject)))
        );
        const actionButton = card.querySelector('[data-terminal-project-action]');
        actionButton?.addEventListener('click', () =>
            setTerminalProjectRunning(Number(card.dataset.terminalProject), actionButton.dataset.terminalProjectAction === 'start', actionButton)
        );
    }

    function paintTerminalProjectCard(projectId) {
        const grid = el('terminal-project-grid');
        const project = actions.projectById(projectId);
        const current = grid?.querySelector(`[data-terminal-project="${Number(projectId)}"]`);
        if (!grid || !project || !current) return;
        const template = document.createElement('template');
        template.innerHTML = terminalProjectCard(project).trim();
        const next = template.content.firstElementChild;
        current.replaceWith(next);
        bindTerminalProjectCard(next);
    }

    function paintTerminalProjectGrid() {
        const grid = el('terminal-project-grid');
        if (!grid) return;
        grid.innerHTML = context.projects.length ? context.projects.map(terminalProjectCard).join('') : '<div class="terminal-project-empty">No projects</div>';
        grid.querySelectorAll('[data-terminal-project]').forEach(bindTerminalProjectCard);
    }

    async function renderTerminalsHome() {
        closeTerminalViews();
        state.terminalProjectId = null;
        state.terminalBoardKey = '';
        context.selectedBoardKey = '';
        actions.renderSidebar();
        paintTerminalProjectGrid();
        await Promise.all(
            context.projects.map(async (project) => {
                const results = await Promise.allSettled([loadTerminalProjectConfiguration(project), loadProjectTerminalSessions(Number(project.id))]);
                if (results[1].status === 'rejected') state.terminalSessions[String(project.id)] = [];
                if (context.workspaceMode === 'terminals_home') paintTerminalProjectCard(Number(project.id));
            })
        );
    }

    async function openProjectTerminals(projectId, options) {
        const project = actions.projectById(projectId);
        if (!project) {
            actions.toast('Project not found.', 'error');
            return;
        }
        if (Number(state.terminalProjectId) !== Number(projectId)) state.terminalActiveSessionId = '';
        state.terminalProjectId = Number(projectId);
        const board = terminalBoardForProject(projectId);
        if (board) {
            state.terminalBoardKey = board.key;
            context.selectedBoardKey = board.key;
        }
        actions.setWorkspaceMode('terminals', { focus: false, updateUrl: false });
        try {
            const detail = await actions.api(`/projects/${Number(projectId)}`);
            state.terminalProjectDetails[String(projectId)] = detail;
            project.startup_instructions = detail.startup_instructions || '';
            el('terminal-command-lines').value = detail.startup_instructions || '';
            el('terminal-project-name').textContent = detail.name || project.name || 'Project terminals';
            renderTerminalCommandRows(detail.startup_instructions || '');
            await refreshTerminalSessions();
            if (options?.updateUrl !== false) actions.setDevelopmentLocation(options?.path || `/development/terminals/${Number(projectId)}/`, options);
        } catch (error) {
            actions.toast(error.message || 'Could not load project terminals.', 'error');
        }
    }

    function connectTerminalView(session) {
        const processId = String(session.process_id || session.terminal_id || '');
        const container = document.querySelector(`[data-terminal-output="${CSS.escape(processId)}"]`);
        if (!processId || !container || processId.startsWith('discovered:') || processId.startsWith('starting:') || typeof window.Terminal !== 'function')
            return;
        container.replaceChildren();
        const term = new window.Terminal({
            convertEol: true,
            cursorBlink: true,
            cursorStyle: 'bar',
            fontSize: 12,
            lineHeight: 1.3,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            theme: {
                background: '#070a0f',
                foreground: '#d7dde8',
                cursor: '#f97316',
                selection: 'rgba(249,115,22,.28)'
            },
            linkHandler: { activate: (_event, link) => openTerminalUrl(link) }
        });
        term.open(container);
        const linkProvider = term.registerLinkProvider(terminalUrlLinkProvider(term));
        const launchedCommand = String(session.command || '')
            .replace(/[\u0000-\u001f\u007f]/g, ' ')
            .trim();
        if (launchedCommand) term.writeln(`\x1b[38;5;244m$ ${launchedCommand}\x1b[0m`);
        const resize = () => {
            const cols = Math.max(20, Math.floor((container.clientWidth - 14) / 7.4));
            const rows = Math.max(6, Math.floor((container.clientHeight - 10) / 16));
            try {
                term.resize(cols, rows);
            } catch (_) {
                /* hidden during navigation */
            }
        };
        const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
        resizeObserver?.observe(container);
        window.setTimeout(resize, 0);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let url = `${protocol}//${window.location.host}/api/projects/startup-terminal/${encodeURIComponent(processId)}/ws`;
        if (token) url += `?internal_token=${encodeURIComponent(token)}`;
        const socket = new WebSocket(url);
        socket.addEventListener('open', () => {
            resize();
            socket.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
        });
        socket.addEventListener('message', (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'output' && message.data) term.write(message.data);
            } catch (_) {
                /* malformed terminal frame */
            }
        });
        term.onData((data) => {
            if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'input', data }));
        });
        state.terminalViews.set(processId, {
            term,
            socket,
            resizeObserver,
            linkProvider,
            resize
        });
    }

    function selectTerminalSession(processId) {
        const sessionList = el('terminal-session-list');
        if (!sessionList || !processId) return;
        state.terminalActiveSessionId = String(processId);
        sessionList.querySelectorAll('[data-terminal-tab]').forEach((tab) => {
            const active = tab.dataset.terminalTab === String(processId);
            tab.classList.toggle('active', active);
            tab.closest('.terminal-session-tab-shell')?.classList.toggle('active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
            tab.tabIndex = active ? 0 : -1;
        });
        sessionList.querySelectorAll('[data-terminal-session]').forEach((panel) => {
            const active = panel.dataset.terminalSession === String(processId);
            panel.classList.toggle('active', active);
            panel.hidden = !active;
        });
        const view = state.terminalViews.get(String(processId));
        window.setTimeout(() => {
            view?.resize?.();
            view?.term?.focus?.();
        }, 0);
    }

    function renderTerminalWorkspace(sessions) {
        if (context.workspaceMode !== 'terminals') return;
        const projectId = Number(state.terminalProjectId || actions.boardByKey(state.terminalBoardKey)?.project_id || 0);
        const rows = Array.isArray(sessions) ? sessions : state.terminalSessions[String(projectId)] || [];
        const managedRows = rows.filter((session) => !String(session.process_id || session.terminal_id || '').startsWith('discovered:'));
        const managedPids = new Set(managedRows.map((session) => Number(session.pid || 0)).filter(Boolean));
        const discoveredRows = rows.filter((session) => {
            const processId = String(session.process_id || session.terminal_id || '');
            return processId.startsWith('discovered:') && !managedPids.has(Number(session.ppid || 0));
        });
        const visibleRows = [...managedRows, ...discoveredRows];
        const sessionList = el('terminal-session-list');
        const sessionKey = visibleRows.length
            ? visibleRows.map((session) => `${session.process_id || session.terminal_id || ''}:${session.pid || ''}:${session.status || 'running'}`).join('|')
            : 'empty';
        el('terminal-start-all').classList.toggle('hidden', visibleRows.length > 0);
        el('terminal-stop-all').classList.toggle('hidden', visibleRows.length === 0);
        const starting = visibleRows.some(
            (session) => String(session.status || '').toLowerCase() === 'starting' || String(session.process_id || '').startsWith('starting:')
        );
        const count = visibleRows.filter((session) => String(session.status || 'running').toLowerCase() !== 'starting').length;
        const countNode = el('terminal-running-count');
        countNode.textContent = starting ? 'Starting' : count ? `${count} terminal${count === 1 ? '' : 's'}` : 'Stopped';
        countNode.classList.toggle('active', count > 0);
        countNode.classList.toggle('starting', starting);
        if (sessionList.dataset.sessionKey === sessionKey) return;
        closeTerminalViews();
        sessionList.dataset.sessionKey = sessionKey;
        const managedIds = managedRows.map((session) => String(session.process_id || session.terminal_id || ''));
        if (!managedIds.includes(state.terminalActiveSessionId)) state.terminalActiveSessionId = managedIds[0] || '';
        const tabsHtml = managedRows.length
            ? `<div class="terminal-session-tabs" role="tablist" aria-label="Running terminals">${managedRows
                  .map((session, index) => {
                      const processId = String(session.process_id || session.terminal_id || '');
                      const command = session.command || session.purpose || 'Terminal';
                      const active = processId === state.terminalActiveSessionId;
                      const starting = String(session.status || '').toLowerCase() === 'starting' || processId.startsWith('starting:');
                      return `<div class="terminal-session-tab-shell${active ? ' active' : ''}"><button type="button" role="tab" class="terminal-session-tab${active ? ' active' : ''}" data-terminal-tab="${actions.escapeHtml(processId)}" aria-selected="${active ? 'true' : 'false'}" aria-controls="terminal-panel-${actions.escapeHtml(processId)}" tabindex="${active ? '0' : '-1'}"><span class="terminal-state${starting ? ' starting' : ''}" aria-hidden="true"></span><b>${String(index + 1).padStart(2, '0')}</b><code title="${actions.escapeHtml(command)}">${actions.escapeHtml(command)}</code></button>${starting ? '' : `<button type="button" class="terminal-tab-stop" data-terminal-kill="${actions.escapeHtml(processId)}" data-terminal-pid="${actions.escapeHtml(session.pid || '')}" aria-label="Stop terminal ${index + 1}" title="Stop this terminal">×</button>`}</div>`;
                  })
                  .join('')}</div>`
            : '';
        const managedHtml = managedRows
            .map((session) => {
                const processId = String(session.process_id || session.terminal_id || '');
                const command = session.command || session.purpose || 'Terminal';
                const starting = String(session.status || '').toLowerCase() === 'starting' || processId.startsWith('starting:');
                const active = processId === state.terminalActiveSessionId;
                return `<article id="terminal-panel-${actions.escapeHtml(processId)}" role="tabpanel" class="terminal-session${starting ? ' starting' : ''}${active ? ' active' : ''}" data-terminal-session="${actions.escapeHtml(processId)}"${active ? '' : ' hidden'}><div class="terminal-output" data-terminal-output="${actions.escapeHtml(processId)}"><div class="terminal-output-fallback"><code>$ ${actions.escapeHtml(command)}</code><span>${starting ? 'Starting terminal...' : 'Connecting...'}</span></div></div></article>`;
            })
            .join('');
        const discoveredHtml = discoveredRows.length
            ? `<div class="terminal-process-list" aria-label="Detected project processes">${discoveredRows
                  .map((session) => {
                      const processId = String(session.process_id || '');
                      const command = session.command || session.purpose || 'Project process';
                      return `<div class="terminal-process-row"><span class="terminal-state" aria-label="Running"></span><code title="${actions.escapeHtml(command)}">${actions.escapeHtml(command)}</code><small>Detected process</small><button type="button" data-terminal-kill="${actions.escapeHtml(processId)}" data-terminal-pid="${actions.escapeHtml(session.pid || '')}" aria-label="Stop detected process" title="Stop process">×</button></div>`;
                  })
                  .join('')}</div>`
            : '';
        const terminalDeck = managedRows.length
            ? `<section class="terminal-session-deck">${tabsHtml}<div class="terminal-session-panels">${managedHtml}</div></section>`
            : '';
        sessionList.innerHTML = visibleRows.length
            ? `${terminalDeck}${discoveredHtml}`
            : '<div class="terminal-session-empty"><code>&gt;_</code><span>No terminal processes are running.</span></div>';
        sessionList
            .querySelectorAll('[data-terminal-tab]')
            .forEach((tab) => tab.addEventListener('click', () => selectTerminalSession(tab.dataset.terminalTab)));
        sessionList
            .querySelectorAll('[data-terminal-kill]')
            .forEach((button) => button.addEventListener('click', () => killTerminal(button.dataset.terminalKill, button.dataset.terminalPid)));
        managedRows.forEach(connectTerminalView);
    }

    async function refreshTerminalSessions(options) {
        const projectId = Number(state.terminalProjectId || actions.boardByKey(state.terminalBoardKey)?.project_id || 0);
        if (!projectId) {
            renderTerminalWorkspace([]);
            return;
        }
        const previous = state.terminalSessions[String(projectId)] || [];
        try {
            const sessions = await loadProjectTerminalSessions(projectId);
            if (options?.keepStarting && !sessions.length && previous.some((session) => String(session.process_id || '').startsWith('starting:'))) {
                state.terminalSessions[String(projectId)] = previous;
                renderTerminalWorkspace(previous);
                window.setTimeout(() => {
                    if (context.workspaceMode === 'terminals' && Number(state.terminalProjectId) === projectId) refreshTerminalSessions();
                }, 1500);
                return;
            }
            renderTerminalWorkspace(sessions);
        } catch (error) {
            renderTerminalWorkspace();
            actions.toast(error.message || 'Could not load project terminals.', 'error');
        }
    }

    async function startBoardTerminals() {
        const projectId = Number(state.terminalProjectId || actions.boardByKey(state.terminalBoardKey)?.project_id || 0);
        if (!projectId) return;
        const commands = syncTerminalCommandLines();
        const instructions = commands.join('\n');
        const startButton = el('terminal-start-all');
        startButton.disabled = true;
        startButton.setAttribute('aria-label', 'Starting all terminals');
        startButton.title = 'Starting all terminals';
        startButton.innerHTML = terminalProjectActionIcon('start', true);
        try {
            const result = await actions.api(`/projects/${projectId}/startup-terminals/start`, {
                method: 'POST',
                body: { commands, startup_instructions: instructions }
            });
            state.terminalProjectDetails[String(projectId)] = {
                ...(state.terminalProjectDetails[String(projectId)] || {}),
                startup_instructions: instructions
            };
            const project = actions.projectById(projectId);
            if (project) project.startup_instructions = instructions;
            actions.toast(result.message || 'Project terminals started.');
            const returnedSessions = result.sessions || [];
            const sessions = returnedSessions.length
                ? returnedSessions
                : commands.map((command, index) => ({
                      process_id: `starting:${projectId}:${index}`,
                      command,
                      status: 'starting'
                  }));
            state.terminalSessions[String(projectId)] = sessions;
            renderTerminalWorkspace(sessions);
            if (!returnedSessions.length && sessions.length) {
                window.setTimeout(() => {
                    if (context.workspaceMode === 'terminals' && Number(state.terminalProjectId) === projectId) refreshTerminalSessions({ keepStarting: true });
                }, 900);
            }
        } catch (error) {
            actions.toast(error.message || 'Could not start the project terminals.', 'error');
        } finally {
            startButton.disabled = false;
            startButton.setAttribute('aria-label', 'Start all terminals');
            startButton.title = 'Start all terminals';
            startButton.innerHTML = terminalProjectActionIcon('start', false);
        }
    }

    async function stopBoardTerminals() {
        const projectId = Number(state.terminalProjectId || actions.boardByKey(state.terminalBoardKey)?.project_id || 0);
        if (!projectId) return;
        const stopButton = el('terminal-stop-all');
        stopButton.disabled = true;
        stopButton.setAttribute('aria-label', 'Stopping all terminals');
        stopButton.title = 'Stopping all terminals';
        try {
            const current = state.terminalSessions[String(projectId)] || [];
            const managedPids = new Set(
                current
                    .filter((session) => !String(session.process_id || '').startsWith('discovered:'))
                    .map((session) => Number(session.pid || 0))
                    .filter(Boolean)
            );
            const auxiliary = current.filter((session) => {
                const processId = String(session.process_id || session.terminal_id || '');
                const purpose = String(session.purpose || '').toLowerCase();
                if (processId.startsWith('discovered:')) return !managedPids.has(Number(session.ppid || 0));
                return purpose === 'cli_shell';
            });
            const processResults = await Promise.all(
                auxiliary.map((session) =>
                    actions.terminalActionApi('/projects/kill-terminal', {
                        method: 'POST',
                        body: {
                            process_id: session.process_id || session.terminal_id,
                            pid: Number(session.pid || 0) || undefined,
                            project_id: projectId
                        }
                    })
                )
            );
            const result = await actions.terminalActionApi(`/projects/${projectId}/startup-terminals/stop`, { method: 'POST', body: {} });
            await refreshTerminalSessions();
            const failed = processResults.filter((item) => !item?.success).length;
            actions.toast(
                failed ? `${failed} terminal process${failed === 1 ? '' : 'es'} could not be stopped.` : result.message || 'Project terminals stopped.',
                failed ? 'error' : 'success'
            );
        } catch (error) {
            actions.toast(error.message || 'Could not stop the project terminals.', 'error');
        } finally {
            stopButton.disabled = false;
            stopButton.setAttribute('aria-label', 'Stop all terminals');
            stopButton.title = 'Stop all terminals';
            stopButton.innerHTML = terminalProjectActionIcon('stop', false);
        }
    }

    async function saveTerminalCommands(event) {
        event?.preventDefault();
        const projectId = Number(state.terminalProjectId || 0);
        if (!projectId) return;
        const startupInstructions = syncTerminalCommandLines().join('\n');
        try {
            await actions.api(`/projects/${projectId}`, {
                method: 'PUT',
                body: { startup_instructions: startupInstructions }
            });
            const project = actions.projectById(projectId);
            if (project) project.startup_instructions = startupInstructions;
            const board = terminalBoardForProject(projectId);
            if (board) board.startup_instructions = startupInstructions;
            state.terminalProjectDetails[String(projectId)] = {
                ...(state.terminalProjectDetails[String(projectId)] || {}),
                startup_instructions: startupInstructions
            };
            actions.toast('Terminal lines saved.');
        } catch (error) {
            actions.toast(error.message || 'Could not save terminal lines.', 'error');
        }
    }

    async function killTerminal(processId, pid) {
        try {
            await actions.api('/projects/kill-terminal', {
                method: 'POST',
                body: {
                    process_id: processId,
                    pid: Number(pid || 0) || undefined,
                    project_id: state.terminalProjectId || undefined
                }
            });
            await refreshTerminalSessions();
            actions.toast('Terminal stopped.');
        } catch (error) {
            actions.toast(error.message || 'Could not stop the terminal.', 'error');
        }
    }

    function bindEvents() {
        el('terminal-projects-back').addEventListener('click', () => actions.setWorkspaceMode('terminals_home'));
        el('terminal-command-form').addEventListener('submit', saveTerminalCommands);
        el('terminal-command-rows').addEventListener('input', syncTerminalCommandLines);
        el('terminal-command-rows').addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || event.isComposing) return;
            const input = event.target.closest('input');
            if (!input) return;
            event.preventDefault();
            const inputs = Array.from(el('terminal-command-rows').querySelectorAll('input'));
            const index = inputs.indexOf(input);
            if (index < inputs.length - 1) {
                inputs[index + 1].focus();
                return;
            }
            if (input.value.trim()) addTerminalCommandRow();
        });
        el('terminal-command-rows').addEventListener('click', (event) => {
            const button = event.target.closest('[data-terminal-command-remove]');
            if (button) removeTerminalCommandRow(button.dataset.terminalCommandRemove);
        });
        el('terminal-add-command').addEventListener('click', addTerminalCommandRow);
        el('terminal-copy-commands').addEventListener('click', copyTerminalCommands);
        el('terminal-paste-commands').addEventListener('click', pasteTerminalCommands);
        el('terminal-start-all').addEventListener('click', startBoardTerminals);
        el('terminal-stop-all').addEventListener('click', stopBoardTerminals);
    }
    return {
        bindEvents,
        closeTerminalViews,
        getTerminalSessions,
        loadProjectTerminalSessions,
        openProjectTerminals,
        paintTerminalProjectGrid,
        primeTerminalSelection,
        renderTerminalWorkspace,
        renderTerminalsHome,
        startTerminalPolling,
        leave
    };
}
