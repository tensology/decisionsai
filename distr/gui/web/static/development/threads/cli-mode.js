// Development thread CLI mode: one real project-folder PTY per thread (xterm + startup-terminal WS).
// Toggle OFF hides the pane and detaches websockets only — the PTY keeps running.
// Toggle ON reconnects the same process_id for that thread (no new spawn, no tab bar).
export function createThreadCliMode({ el, token, getContext, toast, api }) {
    const mode = {
        on: false,
        chatId: null,
        projectId: null,
        backendId: '',
        // chatId -> session { tabId, processId, command, label, cwd, term, socket, resizeObserver, linkProvider, host, createdAt }
        sessions: Object.create(null),
        recoveredProjectId: null,
        recoveredProjects: new Set(),
        persistTimer: null
    };

    function pane() {
        return el('thread-cli-pane');
    }
    function statusNode() {
        return el('thread-cli-status');
    }
    function toggleBtn() {
        return el('thread-cli-mode-toggle');
    }
    function composerLayer() {
        return el('development-composer-layer');
    }
    function hosts() {
        return el('thread-cli-xterm-hosts');
    }

    function threadTabId(chatId) {
        return `thread-${Number(chatId) || 0}`;
    }

    function commandForBackend(backendId) {
        const id = String(backendId || 'pi').toLowerCase() || 'pi';
        if (id === 'pi' || id === 'pi-coding-agent' || !id) return 'pi';
        if (id === 'codex' || id === 'openai-codex') return 'codex';
        if (id === 'cursor' || id === 'cursor-agent') return 'agent';
        return id;
    }

    function currentSession() {
        const chatId = Number(mode.chatId || 0);
        if (!chatId) return null;
        return mode.sessions[chatId] || null;
    }

    function setStatus(kind, text) {
        const node = statusNode();
        if (!node) return;
        node.dataset.state = kind || 'idle';
        node.textContent = text || '';
    }

    function syncToggle() {
        const btn = toggleBtn();
        if (!btn) return;
        const ctx = getContext() || {};
        const hasProject = Boolean(Number(ctx.projectId || 0));
        const inChat = ctx.workspaceMode === 'chat' && Boolean(Number(ctx.chatId || 0));
        btn.classList.toggle('hidden', !inChat);
        btn.disabled = !hasProject;
        btn.setAttribute('aria-pressed', mode.on ? 'true' : 'false');
        btn.title = !hasProject
            ? 'Link this thread to a project to open the real Pi CLI'
            : mode.on
              ? 'Return to chat view'
              : 'Open real Pi CLI in the project folder';
        btn.setAttribute('aria-label', mode.on ? 'Chat or CLI view: CLI active. Switch to Chat' : 'Chat or CLI view: Chat active. Switch to CLI');
    }

    function setComposerVisible(visible) {
        const layer = composerLayer();
        if (!layer) return;
        const ctx = getContext() || {};
        const inChat = ctx.workspaceMode === 'chat';
        const show = Boolean(visible) && inChat && !mode.on;
        layer.classList.toggle('hidden', !show);
        layer.setAttribute('aria-hidden', show ? 'false' : 'true');
    }

    function showChat() {
        const ctx = getContext() || {};
        if (ctx.workspaceMode === 'chat') el('conversation')?.classList.remove('hidden');
        pane()?.classList.add('hidden');
        setComposerVisible(true);
    }

    function isPaneVisible() {
        const node = pane();
        if (!node || node.classList.contains('hidden')) return false;
        // display:none (or not yet laid out) → client box is 0; never fit against that.
        return node.clientWidth > 0 && node.clientHeight > 0;
    }

    function scheduleFit(session) {
        const run = () => {
            if (!mode.on || !isPaneVisible()) return;
            const target = session || currentSession();
            if (!target?.host || target.host.hidden) return;
            if (target.host.clientWidth <= 0 || target.host.clientHeight <= 0) return;
            resizeSession(target);
            try {
                target.term?.scrollToBottom?.();
            } catch (_) {
                /* ignore */
            }
            target.term?.focus?.();
        };
        // Double rAF: wait until the pane is actually painted after leaving display:none.
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(run);
        });
        // Fallback in case the first layout pass still reports 0 (font/metrics settle).
        window.setTimeout(run, 50);
    }

    function showCli() {
        el('conversation')?.classList.add('hidden');
        pane()?.classList.remove('hidden');
        setComposerVisible(false);
        syncHostsVisibility();
        scheduleFit(currentSession());
    }

    function schedulePersist() {
        if (mode.persistTimer) window.clearTimeout(mode.persistTimer);
        mode.persistTimer = window.setTimeout(() => {
            mode.persistTimer = null;
            persistSessions().catch(() => {});
        }, 200);
    }

    async function persistSessions() {
        const projectId = Number(mode.projectId || 0);
        if (!projectId || typeof api !== 'function') return;
        const tabs = Object.keys(mode.sessions)
            .filter((chatKey) => Number(mode.sessions[chatKey]?.projectId || projectId) === projectId)
            .map((chatKey) => {
            const s = mode.sessions[chatKey];
            return {
                tab_id: s.tabId || threadTabId(chatKey),
                process_id: s.processId || null,
                command: s.command || 'pi',
                label: s.label || s.command || 'pi',
                cwd: s.cwd || null,
                visible: true,
                created_at: s.createdAt || Date.now() / 1000,
                chat_id: Number(chatKey) || null
            };
        });
        const active = currentSession();
        const payload = {
            active_tab_id: active?.tabId || null,
            tabs
        };
        try {
            await api(`/projects/${projectId}/pi-cli-terminal/tabs`, { method: 'PUT', body: payload });
        } catch (error) {
            if (error?.status !== 404 && error?.status !== 405) {
                console.warn('pi-cli session persist failed', error);
            }
        }
    }

    function syncHostsVisibility() {
        const root = hosts();
        if (!root) return;
        const activeChat = Number(mode.chatId || 0);
        Object.keys(mode.sessions).forEach((chatKey) => {
            const session = mode.sessions[chatKey];
            if (!session?.host) return;
            const show = mode.on && Number(chatKey) === activeChat;
            session.host.hidden = !show;
            session.host.classList.toggle('is-active', show);
        });
    }

    function openTerminalUrl(url) {
        try {
            window.open(url, '_blank', 'noopener,noreferrer');
        } catch (_) {
            /* ignore */
        }
    }

    function terminalUrlLinkProvider(term) {
        const pattern = /https?:\/\/[^\s"'<>]+/g;
        return {
            provideLinks(bufferLine, callback) {
                const line = term.buffer.active.getLine(bufferLine);
                if (!line) return callback(undefined);
                let text = '';
                for (let i = 0; i < line.length; i += 1) text += line.getCell(i)?.getChars?.() || '';
                const links = [];
                let match;
                pattern.lastIndex = 0;
                while ((match = pattern.exec(text))) {
                    links.push({
                        startIndex: match.index,
                        length: match[0].length,
                        text: match[0],
                        activate: () => openTerminalUrl(match[0])
                    });
                }
                callback(links.length ? links : undefined);
            }
        };
    }

    function ensureHost(session) {
        const root = hosts();
        if (!root || session.host) return session.host;
        const host = document.createElement('div');
        host.className = 'thread-cli-xterm';
        host.dataset.cliChatHost = String(session.chatId || '');
        host.setAttribute('role', 'application');
        host.setAttribute('aria-label', `CLI ${session.command || 'pi'}`);
        host.hidden = true;
        root.appendChild(host);
        session.host = host;
        return host;
    }

    function destroySessionTerminal(session, { disposeTerm = true } = {}) {
        if (!session) return;
        if (session.socket) {
            try {
                session.socket.onclose = null;
                session.socket.onerror = null;
                session.socket.onmessage = null;
                session.socket.close();
            } catch (_) {
                /* ignore */
            }
            session.socket = null;
        }
        if (session.resizeObserver) {
            try {
                session.resizeObserver.disconnect();
            } catch (_) {
                /* ignore */
            }
            session.resizeObserver = null;
        }
        if (session.linkProvider) {
            try {
                session.linkProvider.dispose?.();
            } catch (_) {
                /* ignore */
            }
            session.linkProvider = null;
        }
        if (disposeTerm && session.term) {
            try {
                session.term.dispose();
            } catch (_) {
                /* ignore */
            }
            session.term = null;
            if (session.host) session.host.replaceChildren();
        }
    }

    function resizeSession(session) {
        if (!session?.term || !session.host) return;
        // Never fit while the CLI pane/host is display:none — that yields ~0×0 and a
        // tiny row count; when shown again the TUI redraws with a blank band at the top.
        if (!mode.on || !isPaneVisible()) return;
        const container = session.host;
        if (container.hidden || container.clientWidth <= 0 || container.clientHeight <= 0) return;
        const term = session.term;
        const terminalScreen = container.querySelector('.xterm-screen');
        const screenRect = terminalScreen?.getBoundingClientRect();
        const containerStyle = window.getComputedStyle(container);
        const horizontalPadding = parseFloat(containerStyle.paddingLeft) || 0;
        const verticalPadding = parseFloat(containerStyle.paddingTop) || 0;
        const padX = horizontalPadding + (parseFloat(containerStyle.paddingRight) || 0);
        const padY = verticalPadding + (parseFloat(containerStyle.paddingBottom) || 0);
        const cellWidth = screenRect?.width && term.cols ? screenRect.width / term.cols : 7.4;
        const cellHeight = screenRect?.height && term.rows ? screenRect.height / term.rows : 16;
        if (!Number.isFinite(cellWidth) || cellWidth <= 0 || !Number.isFinite(cellHeight) || cellHeight <= 0) return;
        const cols = Math.max(20, Math.floor((container.clientWidth - padX) / cellWidth));
        const rows = Math.max(6, Math.floor((container.clientHeight - padY) / cellHeight));
        if (term.cols === cols && term.rows === rows) {
            // Still nudge the PTY once after becoming visible so TUIs redraw flush.
            if (session.socket && session.socket.readyState === WebSocket.OPEN && !session._fitSent) {
                session._fitSent = true;
                session.socket.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
            }
            return;
        }
        try {
            term.resize(cols, rows);
        } catch (_) {
            /* hidden */
            return;
        }
        session._fitSent = true;
        if (session.socket && session.socket.readyState === WebSocket.OPEN) {
            session.socket.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
        }
    }

    function ensureTerm(session) {
        if (session.term) {
            // Re-attach observers lost during socket-only detach.
            if (!session.resizeObserver && typeof ResizeObserver === 'function' && session.host) {
                session.resizeObserver = new ResizeObserver(() => resizeSession(session));
                session.resizeObserver.observe(session.host);
            }
            if (!session.linkProvider && session.term.registerLinkProvider) {
                session.linkProvider = session.term.registerLinkProvider(terminalUrlLinkProvider(session.term));
            }
            return session.term;
        }
        const container = ensureHost(session);
        if (!container) return null;
        if (!window.Terminal) {
            setStatus('error', 'xterm.js is not loaded');
            toast?.('xterm.js failed to load — cannot open the real Pi CLI.', 'error');
            return null;
        }
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
                selectionBackground: 'rgba(249,115,22,.28)'
            },
            linkHandler: { activate: (_event, link) => openTerminalUrl(link) }
        });
        term.open(container);
        session.linkProvider = term.registerLinkProvider(terminalUrlLinkProvider(term));
        session.term = term;
        session.resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => resizeSession(session)) : null;
        session.resizeObserver?.observe(container);
        // Gate PTY writes so scrollback replay / CSI probes cannot inject into the live process.
        session._acceptInput = true;
        term.onData((data) => {
            if (!session._acceptInput) return;
            if (session.socket && session.socket.readyState === WebSocket.OPEN) {
                session.socket.send(JSON.stringify({ type: 'input', data }));
            }
        });
        // Defer fit until the pane is visible — opening xterm inside a hidden host
        // measures 0 and leaves blank rows at the top after toggle-on.
        if (mode.on && isPaneVisible()) scheduleFit(session);
        return term;
    }

    function connectPty(session) {
        if (!session?.processId) return;
        // Healthy (open) or still-opening socket: never redial — redialing replays the
        // whole buffer and churns input. Just make sure the view is fitted.
        if (session.socket && (session.socket.readyState === WebSocket.OPEN || session.socket.readyState === WebSocket.CONNECTING)) {
            ensureTerm(session);
            if (session.socket.readyState === WebSocket.OPEN) {
                scheduleFit(session);
                setStatus('connected', `Live PTY · ${session.command || 'pi'}`);
            }
            return;
        }
        const hadTerm = Boolean(session.term) && Boolean(session._hasOutput);
        destroySessionTerminal(session, { disposeTerm: false });
        const term = ensureTerm(session);
        if (!term) return;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let url = `${protocol}//${window.location.host}/api/projects/startup-terminal/${encodeURIComponent(session.processId)}/ws`;
        if (token) url += `?internal_token=${encodeURIComponent(token)}`;
        setStatus('connecting', `Attaching · ${session.command || 'pi'}…`);
        // Input is gated until the server's replay dump (if any) has been fully parsed.
        // Parsing old output re-runs the TUI's terminal queries (DA, cursor position, OSC
        // colour, kitty keyboard); xterm answers those through onData — that was the
        // "weird characters" typed into Pi on every CLI open.
        session._acceptInput = false;
        const processId = session.processId;
        const socket = new WebSocket(url);
        session.socket = socket;
        let opened = false;
        let firstMessage = true;
        let replayTimer = null;
        const enableInput = () => {
            if (session.socket === socket) session._acceptInput = true;
        };
        socket.addEventListener('open', () => {
            opened = true;
            session._connectRetries = 0;
            setStatus('connected', `Live PTY · ${session.command || 'pi'}`);
            session._fitSent = false;
            scheduleFit(session);
            // Empty buffer → no replay message; open input shortly after.
            replayTimer = window.setTimeout(() => {
                if (firstMessage) enableInput();
            }, 250);
        });
        socket.addEventListener('message', (event) => {
            let message = null;
            try {
                message = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            if (message?.type !== 'output' || !message.data) return;
            const isReplay = message.replay === true || (firstMessage && !opened);
            if (firstMessage) {
                firstMessage = false;
                if (replayTimer) window.clearTimeout(replayTimer);
            }
            if (isReplay) {
                if (hadTerm) {
                    // This xterm already shows that history — never re-parse it.
                    enableInput();
                    return;
                }
                session._acceptInput = false;
                session._replaying = true;
                session._hasOutput = true;
                // write(cb): cb runs after the replay is parsed; replies emitted while
                // parsing were dropped by the gate.
                term.write(message.data, () =>
                    window.setTimeout(() => {
                        session._replaying = false;
                        enableInput();
                    }, 0)
                );
                return;
            }
            session._hasOutput = true;
            if (!session._replaying) enableInput();
            term.write(message.data);
        });
        socket.addEventListener('close', () => {
            if (replayTimer) window.clearTimeout(replayTimer);
            if (session.socket !== socket) return;
            session.socket = null;
            session._acceptInput = false;
            const isCurrent = mode.on && Number(session.chatId) === Number(mode.chatId);
            if (!opened && session.processId === processId && !session._respawning && isCurrent) {
                // Could not attach. Only respawn when the server confirms this PTY is gone —
                // a transient failure must never look like "CLI reloaded as a new instance".
                session._respawning = true;
                verifyProcessAlive(session)
                    .then((alive) => {
                        if (alive) {
                            session._respawning = false;
                            session._connectRetries = Number(session._connectRetries || 0) + 1;
                            if (session._connectRetries > 3) {
                                setStatus('error', 'PTY attach failed — toggle CLI to retry');
                                return;
                            }
                            setStatus('connecting', 'Reattaching…');
                            window.setTimeout(() => connectPty(session), 400 * session._connectRetries);
                            return;
                        }
                        session.processId = null;
                        session._hasOutput = false;
                        setStatus('connecting', 'Session ended — starting a fresh CLI…');
                        return startSession(session, { forceNew: false, recover: true }).then(() => {
                            session._respawning = false;
                            if (session.term) {
                                try {
                                    session.term.reset();
                                } catch (_) {
                                    /* ignore */
                                }
                            }
                            connectPty(session);
                            schedulePersist();
                        });
                    })
                    .catch((error) => {
                        session._respawning = false;
                        setStatus('error', error?.message || 'Attach failed');
                        toast?.(error?.message || 'Attach failed', 'error');
                    });
                return;
            }
            if (isCurrent) setStatus('disconnected', 'PTY disconnected — toggle CLI to retry');
        });
        socket.addEventListener('error', () => {
            if (mode.on && Number(session.chatId) === Number(mode.chatId) && !session._respawning && opened) {
                setStatus('error', 'PTY connection failed');
            }
        });
    }

    async function verifyProcessAlive(session) {
        const projectId = Number(session.projectId || mode.projectId || 0);
        if (!projectId || typeof api !== 'function' || !session.processId) return false;
        try {
            const data = await api(`/projects/${projectId}/pi-cli-terminal/status`);
            return (data?.sessions || []).some((row) => String(row.process_id || '') === String(session.processId));
        } catch (_) {
            // Unknown → assume alive (never spawn on a failed check).
            return true;
        }
    }

    async function findAliveThreadProcess(session) {
        const projectId = Number(session.projectId || mode.projectId || 0);
        if (!projectId || typeof api !== 'function') return null;
        try {
            const data = await api(`/projects/${projectId}/pi-cli-terminal/tabs`);
            const row = (data?.tabs || []).find(
                (tab) => (String(tab.tab_id || '') === session.tabId || Number(tab.chat_id || 0) === Number(session.chatId)) && tab.alive && tab.process_id
            );
            return row || null;
        } catch (_) {
            return null;
        }
    }

    async function startSession(session, { forceNew = false, recover = false } = {}) {
        const projectId = Number(mode.projectId || 0);
        if (!projectId || typeof api !== 'function') return null;
        // Toggle-on / thread switch: never spawn when we already have a process_id.
        // Just reattach — calling start is unnecessary and the 404 fallback always spawns.
        if (session.processId && !forceNew) {
            return {
                success: true,
                process_id: session.processId,
                reused: true,
                command: session.command || commandForBackend(mode.backendId)
            };
        }
        const body = {
            command: session.command || commandForBackend(mode.backendId),
            tab_id: session.tabId,
            label: session.label || session.command || 'pi',
            new: Boolean(forceNew),
            recover: Boolean(recover),
            chat_id: session.chatId || null
        };
        let result = null;
        try {
            result = await api(`/projects/${projectId}/pi-cli-terminal/start`, { method: 'POST', body });
        } catch (primaryError) {
            if (primaryError?.status !== 404 && primaryError?.status !== 405) throw primaryError;
            // Pre-reload fallback: only allowed when we do not already own a live process.
            if (session.processId) throw primaryError;
            result = await api('/projects/startup-terminal', {
                method: 'POST',
                body: { project_id: projectId, command: body.command }
            });
            if (result?.success) result.command = body.command;
        }
        if (!result?.success || !result.process_id) {
            throw new Error(result?.error || 'Could not start the CLI PTY.');
        }
        session.processId = result.process_id;
        session.command = result.command || session.command || 'pi';
        session.cwd = result.cwd || session.cwd || null;
        session.label = session.command;
        return result;
    }

    function ensureSessionRecord(chatId) {
        const id = Number(chatId || 0);
        if (!id) return null;
        if (!mode.sessions[id]) {
            const cmd = commandForBackend(mode.backendId);
            mode.sessions[id] = {
                chatId: id,
                projectId: Number(mode.projectId || 0) || null,
                tabId: threadTabId(id),
                processId: null,
                command: cmd,
                label: cmd,
                cwd: null,
                createdAt: Date.now() / 1000,
                term: null,
                socket: null,
                host: null,
                resizeObserver: null,
                linkProvider: null,
                _skipReplay: false,
                _acceptInput: true
            };
        }
        return mode.sessions[id];
    }

    async function activateCurrentThread({ attach = true } = {}) {
        const chatId = Number(mode.chatId || 0);
        if (!chatId) {
            setStatus('error', 'No thread selected');
            return;
        }
        const session = ensureSessionRecord(chatId);
        ensureHost(session);
        syncHostsVisibility();
        if (attach && mode.on) {
            try {
                if (!session.processId) {
                    // Never spawn if this thread already owns a live PTY on the server.
                    const alive = await findAliveThreadProcess(session);
                    if (alive?.process_id) {
                        session.processId = alive.process_id;
                        session.command = alive.command || session.command;
                    } else {
                        await startSession(session, { forceNew: false, recover: true });
                    }
                } else {
                    // Reuse existing process_id — do not call start / do not spawn.
                    await startSession(session, { forceNew: false });
                }
                connectPty(session);
            } catch (error) {
                setStatus('error', error?.message || 'Attach failed');
                toast?.(error?.message || 'Attach failed', 'error');
            }
        }
        schedulePersist();
        scheduleFit(session);
    }

    async function ensureProjectContext() {
        const ctx = getContext() || {};
        const projectId = Number(ctx.projectId || 0);
        if (!projectId) {
            setStatus('error', 'No project linked');
            toast?.('Link this thread to a project to open the real Pi CLI.', 'error');
            return false;
        }
        mode.projectId = projectId;
        mode.chatId = Number(ctx.chatId || 0) || null;
        mode.backendId = String(ctx.backendId || 'pi').toLowerCase() || 'pi';
        return true;
    }

    function ingestTabsPayload(tabsPayload) {
        const incoming = Array.isArray(tabsPayload?.tabs) ? tabsPayload.tabs : [];
        incoming.forEach((row) => {
            const tabId = String(row.tab_id || '');
            let chatId = Number(row.chat_id || 0);
            if (!chatId && tabId.startsWith('thread-')) {
                chatId = Number(tabId.slice('thread-'.length)) || 0;
            }
            // Legacy multi-tab rows without a thread id: bind only to current chat if empty.
            if (!chatId) {
                chatId = Number(mode.chatId || 0);
                if (!chatId) return;
                // Do not overwrite an existing thread session with a legacy foreign row.
                if (mode.sessions[chatId]?.processId && mode.sessions[chatId].processId !== row.process_id) {
                    return;
                }
            }
            const existing = mode.sessions[chatId];
            const cmd = row.command || commandForBackend(mode.backendId);
            if (existing) {
                if (!existing.processId && row.process_id) existing.processId = row.process_id;
                existing.command = cmd;
                existing.label = row.label || cmd;
                existing.cwd = row.cwd || existing.cwd || null;
                existing.tabId = threadTabId(chatId);
                if (row.created_at) existing.createdAt = row.created_at;
            } else {
                mode.sessions[chatId] = {
                    chatId,
                    projectId: Number(mode.projectId || 0) || null,
                    tabId: threadTabId(chatId),
                    processId: row.process_id || null,
                    command: cmd,
                    label: row.label || cmd,
                    cwd: row.cwd || null,
                    createdAt: row.created_at || Date.now() / 1000,
                    term: null,
                    socket: null,
                    host: null,
                    resizeObserver: null,
                    linkProvider: null,
                    _skipReplay: false,
                    _acceptInput: true
                };
                ensureHost(mode.sessions[chatId]);
            }
        });
    }

    async function recoverOrLoadSessions() {
        if (!(await ensureProjectContext())) return;
        const projectId = mode.projectId;
        if (mode.recoveredProjects.has(projectId)) {
            mode.recoveredProjectId = projectId;
            return;
        }
        let tabsPayload = null;
        try {
            // Prefer recover: respawns saved sessions after Decisions restart; no-ops if PTYs alive.
            tabsPayload = await api(`/projects/${projectId}/pi-cli-terminal/recover`, { method: 'POST', body: {} });
        } catch (error) {
            if (error?.status !== 404 && error?.status !== 405) throw error;
            try {
                tabsPayload = await api(`/projects/${projectId}/pi-cli-terminal/tabs`);
            } catch (tabsError) {
                if (tabsError?.status !== 404 && tabsError?.status !== 405) throw tabsError;
                try {
                    const status = await api(`/projects/${projectId}/pi-cli-terminal/status`);
                    const sessions = status?.sessions || [];
                    tabsPayload = {
                        tabs: sessions.map((s, i) => ({
                            tab_id: `thread-${mode.chatId || i}`,
                            process_id: s.process_id,
                            command: s.command || 'pi',
                            label: s.command || 'pi',
                            visible: true,
                            chat_id: mode.chatId || null,
                            created_at: Date.now() / 1000
                        })),
                        active_tab_id: mode.chatId ? threadTabId(mode.chatId) : null
                    };
                } catch (_) {
                    tabsPayload = { tabs: [], active_tab_id: null };
                }
            }
        }
        ingestTabsPayload(tabsPayload);
        mode.recoveredProjectId = projectId;
        mode.recoveredProjects.add(projectId);
    }

    async function showExistingOrFirst() {
        if (!(await ensureProjectContext())) return;
        if (mode.recoveredProjectId !== mode.projectId) {
            await recoverOrLoadSessions();
        }
        const chatId = Number(mode.chatId || 0);
        if (!chatId) {
            setStatus('error', 'No thread selected');
            return;
        }
        ensureSessionRecord(chatId);
        await activateCurrentThread({ attach: true });
    }

    function detachAllSockets() {
        Object.keys(mode.sessions).forEach((chatKey) => {
            const session = mode.sessions[chatKey];
            if (!session?.socket) return;
            try {
                session.socket.onclose = null;
                session.socket.close();
            } catch (_) {
                /* ignore */
            }
            session.socket = null;
        });
    }

    function setOn(next) {
        const want = Boolean(next);
        if (want === mode.on) {
            syncToggle();
            return;
        }
        mode.on = want;
        syncToggle();
        if (!want) {
            // Hide pane only. Keep PTY, its websocket and xterm live (hidden), so toggling
            // back on needs no redial / replay. Do NOT resize while hiding.
            const session = currentSession();
            if (session) session._fitSent = false;
            showChat();
            setStatus('idle', '');
            schedulePersist();
            return;
        }
        showCli();
        showExistingOrFirst().catch((error) => {
            setStatus('error', error?.message || 'CLI failed');
            toast?.(error?.message || 'CLI failed', 'error');
        });
    }

    function toggle() {
        setOn(!mode.on);
    }

    function parkForOtherWorkspace() {
        // Keep mode.on + session/PTY model and live sockets (the CLI is not listed in
        // project terminals any more). Just hide the in-thread pane.
        pane()?.classList.add('hidden');
        setStatus('idle', '');
    }

    function clearAllSessions({ disposeTerm = true } = {}) {
        Object.keys(mode.sessions).forEach((chatKey) => {
            destroySessionTerminal(mode.sessions[chatKey], { disposeTerm });
        });
        mode.sessions = Object.create(null);
        hosts()?.replaceChildren();
    }

    function syncFromShell() {
        syncToggle();
        if (!mode.on) return;
        const ctx = getContext() || {};
        const inChat = ctx.workspaceMode === 'chat' && Boolean(Number(ctx.chatId || 0));
        if (!inChat) {
            // Leaving chat (e.g. project terminals) must NOT turn CLI mode off.
            parkForOtherWorkspace();
            return;
        }
        const projectId = Number(ctx.projectId || 0);
        const backendId = String(ctx.backendId || 'pi').toLowerCase() || 'pi';
        const chatId = Number(ctx.chatId || 0) || null;
        if (!projectId) {
            // Destination thread has no project: no CLI possible — show its chat
            // (keep mode.on so returning to a project thread opens straight into CLI).
            el('conversation')?.classList.remove('hidden');
            pane()?.classList.add('hidden');
            const layer = composerLayer();
            layer?.classList.remove('hidden');
            layer?.setAttribute('aria-hidden', 'false');
            setStatus('idle', '');
            return;
        }
        showCli();
        if (projectId !== mode.projectId) {
            // Project changed: keep every thread's live socket/xterm (sessions are keyed by
            // chat id, so project A's PTY is never used for project B). Load B's sessions.
            // CLI pane stays up with a connecting state — never flash the chat.
            setStatus('connecting', 'Opening CLI…');
            mode.recoveredProjectId = null;
            mode.projectId = projectId;
            mode.backendId = backendId;
            mode.chatId = chatId;
            showExistingOrFirst().catch((error) => setStatus('error', error?.message || 'CLI failed'));
            return;
        }
        mode.backendId = backendId;
        if (chatId && chatId !== mode.chatId) {
            // Thread switch: show that thread's session only (start only if it has none).
            // The previous thread's socket stays open (hidden) so returning needs no replay.
            mode.chatId = chatId;
            setStatus('connecting', 'Opening CLI…');
            showExistingOrFirst().catch((error) => setStatus('error', error?.message || 'CLI failed'));
            return;
        }
        mode.chatId = chatId || mode.chatId;
        const session = currentSession();
        if (session) {
            connectPty(session);
            scheduleFit(session);
        }
    }

    // ---- Image / file attach into the live PTY --------------------------------
    // Reuses the chat composer's upload path (POST /api/workflows/studio/attachments),
    // which stores the file under ~/.decisions/.../attachments and returns its local
    // path. That temp path is pasted into the CLI input exactly like a terminal
    // drag-drop/paste of a file path, so Pi picks the image up as an attachment.
    function transferFiles(dataTransfer) {
        const items = Array.from(dataTransfer?.items || []).filter((item) => item.kind === 'file');
        const hasDirectory = items.some((item) => item.webkitGetAsEntry?.()?.isDirectory);
        const files = Array.from(dataTransfer?.files || []);
        return { files, hasDirectory, hasFiles: items.length > 0 || files.length > 0 || Array.from(dataTransfer?.types || []).includes('Files') };
    }

    function shellQuotePath(path) {
        const text = String(path || '');
        return /^[A-Za-z0-9_\/.~:@%+=,-]+$/.test(text) ? text : `'${text.replace(/'/g, `'\\''`)}'`;
    }

    async function uploadCliFile(file) {
        const form = new FormData();
        form.append('file', file, file.name || 'image.png');
        const projectId = Number(mode.projectId || (getContext() || {}).projectId || 0);
        if (projectId) form.append('project_id', String(projectId));
        const headers = token ? { 'X-DecisionsAI-Internal-Token': token } : {};
        const response = await fetch('/api/workflows/studio/attachments', { method: 'POST', headers, body: form });
        let result = {};
        try {
            result = await response.json();
        } catch (_) {
            /* ignore */
        }
        if (!response.ok || !result.path) throw new Error(result.detail || `Could not attach ${file.name || 'file'}`);
        return result;
    }

    async function attachFilesToCli(files, origin) {
        const list = Array.from(files || []).filter(Boolean);
        if (!list.length) return;
        const session = currentSession();
        if (!mode.on || !session?.term) {
            toast?.('Open the CLI for this thread first.', 'error');
            return;
        }
        const previous = statusNode()?.textContent || '';
        const previousState = statusNode()?.dataset.state || 'connected';
        setStatus('connecting', `Uploading ${list.length === 1 ? (list[0].name || 'image') : `${list.length} files`}…`);
        const paths = [];
        try {
            for (const file of list) {
                const result = await uploadCliFile(file);
                paths.push(shellQuotePath(result.path));
            }
        } catch (error) {
            toast?.(error?.message || 'Could not attach the file.', 'error');
        } finally {
            setStatus(previousState, previous);
        }
        if (!paths.length) return;
        if (!session.socket || session.socket.readyState !== WebSocket.OPEN) {
            toast?.('CLI is not connected — the file was uploaded but could not be inserted.', 'error');
            return;
        }
        // term.paste honours bracketed-paste mode, so the TUI sees a real paste.
        if (!session._replaying) session._acceptInput = true;
        session.term.paste(`${paths.join(' ')} `);
        session.term.focus?.();
        toast?.(`${paths.length === 1 ? 'Image' : `${paths.length} files`} ${origin === 'drop' ? 'attached to' : 'pasted into'} the CLI.`);
    }

    function bindAttach() {
        const node = pane();
        if (!node || node.dataset.attachBound === 'true') return;
        node.dataset.attachBound = 'true';
        let depth = 0;
        const setDrop = (on) => node.classList.toggle('is-drop-target', Boolean(on));
        // Capture phase so xterm's own textarea paste handler never sees image-only clipboards.
        node.addEventListener(
            'paste',
            (event) => {
                const files = Array.from(event.clipboardData?.files || []);
                const images = files.filter((file) => String(file.type || '').startsWith('image/'));
                if (!images.length) return; // plain text paste: leave to xterm
                event.preventDefault();
                event.stopPropagation();
                attachFilesToCli(images, 'paste');
            },
            true
        );
        node.addEventListener('dragenter', (event) => {
            if (!transferFiles(event.dataTransfer).hasFiles) return;
            event.preventDefault();
            event.stopPropagation();
            depth += 1;
            setDrop(true);
        });
        node.addEventListener('dragover', (event) => {
            const info = transferFiles(event.dataTransfer);
            if (!info.hasFiles) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = info.hasDirectory ? 'none' : 'copy';
            setDrop(true);
        });
        node.addEventListener('dragleave', (event) => {
            event.stopPropagation();
            depth = Math.max(0, depth - 1);
            if (!depth) setDrop(false);
        });
        node.addEventListener('drop', (event) => {
            const info = transferFiles(event.dataTransfer);
            if (!info.hasFiles) return;
            event.preventDefault();
            event.stopPropagation();
            depth = 0;
            setDrop(false);
            if (info.hasDirectory) {
                toast?.('Folders cannot be attached to the CLI. Drop project folders on the left sidebar.', 'error');
                return;
            }
            attachFilesToCli(info.files, 'drop');
        });
    }

    function bind() {
        bindAttach();
        toggleBtn()?.addEventListener('click', () => toggle());
        window.addEventListener('resize', () => {
            if (mode.on) scheduleFit(currentSession());
        });
        syncToggle();
    }

    return {
        bind,
        syncFromShell,
        setOn,
        isOn: () => mode.on,
        // True when the chat workspace should show the CLI pane instead of the transcript.
        ownsChatView: () => {
            if (!mode.on) return false;
            const ctx = getContext() || {};
            return Boolean(Number(ctx.chatId || 0)) && Boolean(Number(ctx.projectId || 0));
        },
        disconnect: () => detachAllSockets(),
        // test/helpers
        _state: mode
    };
}
