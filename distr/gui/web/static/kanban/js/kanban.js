(function() {
    "use strict";

    var currentBoard = null;       // { id, source, ... }
    var currentBoardData = null;   // full board data with lanes/tickets
    var dbBoards = [];
    var editingBoardId = null;     // null = create, number = edit
    var modalTicketId = null;
    var copyTicketData = null;     // { title, description } for copy modal
    var ctxMenuBoardId = null;     // board id for context menu

    // ── Helpers ──

    function esc(s) { var d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

    var apiFetch = window.DecisionsAPI.fetch;
    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "kb-snackbar" }); }

    /** In-app confirm modal (matches Kanban styling). opts: { title, message, confirmLabel, danger, onConfirm } */
    var _kbConfirmCallback = null;
    function showKanbanConfirm(opts) {
        opts = opts || {};
        document.getElementById("kb-confirm-title").textContent = opts.title || "Confirm";
        document.getElementById("kb-confirm-message").textContent = opts.message || "";
        var okBtn = document.getElementById("kb-confirm-ok");
        okBtn.textContent = opts.confirmLabel || "OK";
        okBtn.className = opts.danger
            ? "px-4 py-2 rounded text-white text-sm bg-red-600 hover:bg-red-700"
            : "px-4 py-2 rounded text-white text-sm bg-[#f97316] hover:bg-[#ea580c]";
        _kbConfirmCallback = typeof opts.onConfirm === "function" ? opts.onConfirm : null;
        document.getElementById("kb-confirm-modal").classList.remove("hidden");
    }
    function hideKanbanConfirm() {
        _kbConfirmCallback = null;
        document.getElementById("kb-confirm-modal").classList.add("hidden");
    }
    function reloadCurrentDatabaseBoard() {
        if (currentBoard && currentBoard.source === "database" && currentBoard.id) {
            selectBoard("database", currentBoard.id);
        }
    }

    // ── Load boards sidebar ──

    var _boardsCache = null;
    var _externalCache = null;
    var _boardsCacheTime = 0;
    var _externalCacheTime = 0;
    var CACHE_TTL = 60000; // 1 minute

    function loadBoards(forceRefresh) {
        var now = Date.now();
        var boardsStale = forceRefresh || !_boardsCache || (now - _boardsCacheTime > CACHE_TTL);
        var externalStale = forceRefresh || !_externalCache || (now - _externalCacheTime > CACHE_TTL);

        var boardsPromise = boardsStale
            ? apiFetch("/api/kanban/boards").then(function(boards) {
                _boardsCache = boards;
                _boardsCacheTime = Date.now();
                return boards;
            })
            : Promise.resolve(_boardsCache);

        boardsPromise.then(function(boards) {
            dbBoards = boards.filter(function(b) { return b.source === "database"; });
            renderSidebarBoards(boards);

            // Auto-select: URL param > localStorage > first board
            if (!currentBoard && dbBoards.length) {
                var params = new URLSearchParams(window.location.search);
                var urlBoardId = parseInt(params.get("board_id"), 10);
                if (urlBoardId && dbBoards.some(function(b) { return b.id === urlBoardId; })) {
                    selectBoard("database", urlBoardId);
                } else {
                    var last = null;
                    try { last = JSON.parse(localStorage.getItem("kb_last_selected")); } catch (e) {}
                    if (last && last.source === "database" && dbBoards.some(function(b) { return b.id === last.id; })) {
                        selectBoard(last.source, last.id);
                    } else {
                        selectBoard("database", dbBoards[0].id);
                    }
                }
            }
        }).catch(function() { showSnackbar("Failed to load boards", "error"); });

        if (externalStale) {
            apiFetch("/api/kanban/external-boards").then(function(data) {
                _externalCache = data;
                _externalCacheTime = Date.now();
                renderExternalBoards("kb-trello-boards", data.trello || [], "trello");
                renderExternalBoards("kb-jira-boards", data.jira || [], "jira");
            }).catch(function(e) {
                console.error("Failed to load external boards:", e);
            });
        } else if (_externalCache) {
            renderExternalBoards("kb-trello-boards", _externalCache.trello || [], "trello");
            renderExternalBoards("kb-jira-boards", _externalCache.jira || [], "jira");
        }
    }

    function renderSidebarBoards(boards) {
        var container = document.getElementById("kb-db-boards");
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var db = boards.filter(function(b) { return b.source === "database"; });
        if (search) db = db.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; });
        container.innerHTML = db.length ? "" : '<p class="text-xs text-gray-500 italic">No boards yet</p>';
        db.forEach(function(b) {
            var div = document.createElement("div");
            div.className = "kb-board-item text-gray-300" + (currentBoard && currentBoard.id === b.id && currentBoard.source === "database" ? " active" : "");
            div.draggable = true;
            div.dataset.boardId = b.id;
            var inUseTag = b.in_use ? '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#f97316;color:#fff;margin-left:4px;flex-shrink:0">IN USE</span>' : '';
            div.innerHTML = '<span class="kb-src-icon" style="background:' + esc(b.color || '#f97316') + '"></span><span class="flex-1 truncate">' + esc(b.name) + '</span>' + inUseTag + (b.agent_enabled ? '<svg class="kb-agent-indicator" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0" title="Agent check-in enabled"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="11"/></svg>' : '');
            div.onclick = function() { selectBoard("database", b.id); };
            div.ondblclick = function() { openBoardModal(b.id); };
            div.ondragstart = function(e) { e.dataTransfer.setData("text/plain", "board:" + b.id); div.classList.add("dragging"); };
            div.ondragend = function() { div.classList.remove("dragging"); };
            div.ondragover = function(e) { e.preventDefault(); div.style.borderTop = "2px solid #f97316"; };
            div.ondragleave = function() { div.style.borderTop = ""; };
            div.ondrop = function(e) {
                e.preventDefault();
                div.style.borderTop = "";
                var data = e.dataTransfer.getData("text/plain");
                if (!data.startsWith("board:")) return;
                var draggedId = parseInt(data.split(":")[1], 10);
                if (draggedId === b.id) return;
                // Reorder: collect current order, move dragged before drop target
                var items = container.querySelectorAll("[data-board-id]");
                var order = [];
                items.forEach(function(el) { order.push(parseInt(el.dataset.boardId, 10)); });
                order = order.filter(function(id) { return id !== draggedId; });
                var dropIdx = order.indexOf(b.id);
                order.splice(dropIdx, 0, draggedId);
                apiFetch("/api/kanban/boards/reorder", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ order: order })
                }).then(function() { loadBoards(true); }).catch(function() {});
            };
            div.oncontextmenu = function(e) { e.preventDefault(); showBoardContextMenu(e, b.id); };
            container.appendChild(div);
        });
    }

    function renderExternalBoards(containerId, boards, source) {
        var container = document.getElementById(containerId);
        if (!boards.length) {
            container.innerHTML = '<p class="text-xs text-gray-500 italic">No ' + source + ' account connected</p>';
            return;
        }
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var filtered = search ? boards.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; }) : boards;
        container.innerHTML = "";
        filtered.forEach(function(b) {
            var div = document.createElement("div");
            var cls = source === "trello" ? "kb-src-trello" : "kb-src-jira";
            div.className = "kb-board-item text-gray-300" + (currentBoard && currentBoard.id === b.id && currentBoard.source === source ? " active" : "");
            div.innerHTML = '<span class="kb-src-icon ' + cls + '"></span>' + esc(b.name);
            div.onclick = function() { selectBoard(source, b.id, b.url); };
            container.appendChild(div);
        });
    }

    // ── Board context menu (right-click) ──

    function showBoardContextMenu(e, boardId) {
        ctxMenuBoardId = boardId;
        var menu = document.getElementById("kb-board-ctx-menu");
        menu.style.left = e.clientX + "px";
        menu.style.top = e.clientY + "px";
        menu.classList.remove("hidden");
    }

    function hideBoardContextMenu() {
        document.getElementById("kb-board-ctx-menu").classList.add("hidden");
        ctxMenuBoardId = null;
    }

    function ctxRenameBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        var board = dbBoards.find(function(b) { return b.id === boardId; });
        var newName = prompt("Rename board:", board ? board.name : "");
        if (!newName || !newName.trim()) { hideBoardContextMenu(); return; }
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName.trim() })
        }).then(function() {
            showSnackbar("Board renamed");
            loadBoards(true);
            if (currentBoard && currentBoard.id === boardId) selectBoard("database", boardId);
        }).catch(function(e) { showSnackbar("Rename failed: " + e.message, "error"); });
    }

    function ctxEditBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        openBoardModal(boardId);
    }

    function ctxArchiveBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId + "/archive", { method: "POST" }).then(function() {
            showSnackbar("Board archived");
            if (currentBoard && currentBoard.id === boardId) {
                currentBoard = null; currentBoardData = null;
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
            }
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Archive failed: " + e.message, "error"); });
    }

    function ctxDeleteBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        var board = dbBoards.find(function(b) { return b.id === boardId; });
        var name = board ? board.name : "this board";
        if (!confirm('Delete board "' + name + '" and all its tickets? This cannot be undone.')) { hideBoardContextMenu(); return; }
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            if (currentBoard && currentBoard.id === boardId) {
                currentBoard = null; currentBoardData = null;
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
            }
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
    }

    function ctxActivateBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId + "/use", { method: "POST" }).then(function(data) {
            showSnackbar("Board set as active");
            loadBoards(true);
            if (data && data.linked_project) {
                if (confirm('This board is linked to project "' + data.linked_project.name + '". Activate that project too?')) {
                    apiFetch("/api/projects/" + data.linked_project.id + "/use", { method: "POST" }).then(function() {
                        showSnackbar("Project activated");
                    }).catch(function() {});
                }
            }
        }).catch(function(e) { showSnackbar("Activate failed: " + e.message, "error"); });
    }

    // ── Select board ──

    function selectBoard(source, id, extUrl) {
        currentBoard = { id: id, source: source, extUrl: extUrl || "" };
        try { localStorage.setItem("kb_last_selected", JSON.stringify({ source: source, id: id })); } catch (e) {}
        document.getElementById("kb-empty").classList.add("hidden");
        document.getElementById("kb-board-view").classList.remove("hidden");

        if (source === "database") {
            apiFetch("/api/kanban/boards/" + id).then(function(data) {
                currentBoardData = data;
                renderBoard(data, true);
            }).catch(function(e) { showSnackbar("Failed to load board: " + e.message, "error"); });
        } else {
            apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(id)).then(function(data) {
                currentBoardData = data;
                renderBoard(data, false);
            }).catch(function(e) { showSnackbar("Failed to load external board: " + e.message, "error"); });
        }
        loadBoards(); // uses cache, just re-renders sidebar active state
    }

    function renderBoard(data, isLocal) {
        // Apply board accent color as CSS variable
        var boardColor = data.color || "#f97316";
        document.getElementById("kb-board-view").style.setProperty("--kb-accent", boardColor);

        document.getElementById("kb-board-title").textContent = data.name || "Board";
        var badge = document.getElementById("kb-board-source-badge");
        var source = currentBoard.source;
        if (source === "database") {
            badge.classList.add("hidden");
        } else {
            badge.classList.remove("hidden");
            badge.textContent = source.charAt(0).toUpperCase() + source.slice(1);
            badge.className = "text-xs px-2 py-0.5 rounded text-white";
            badge.style.backgroundColor = source === "trello" ? "#0079bf" : "#0052cc";
        }

        document.getElementById("kb-add-ticket").style.display = isLocal ? "" : "none";
        document.getElementById("kb-edit-board").style.display = isLocal ? "" : "none";
        document.getElementById("kb-delete-board").style.display = isLocal ? "" : "none";
        var extLink = document.getElementById("kb-board-ext-link");
        if (!isLocal && (data.url || currentBoard.extUrl)) {
            extLink.classList.remove("hidden");
            extLink.href = data.url || currentBoard.extUrl;
        } else {
            extLink.classList.add("hidden");
        }
        renderLanes(data.lanes || [], isLocal);
    }

    function renderLanes(lanes, isLocal) {
        var container = document.getElementById("kb-lanes");
        container.innerHTML = "";
        lanes.forEach(function(lane) {
            var col = document.createElement("div");
            col.className = "kb-lane flex flex-col bg-[#152054]/50 rounded-lg border border-white/10";
            col.innerHTML = '<div class="px-3 py-2 border-b border-white/10 flex items-center justify-between">' +
                '<span class="text-sm font-medium text-gray-300">' + esc(lane.name) + '</span>' +
                '<span class="text-xs text-gray-500">' + (lane.tickets || []).length + '</span></div>';
            var body = document.createElement("div");
            body.className = "kb-lane-body flex-1 p-2 space-y-2 overflow-y-auto";
            body.dataset.laneId = lane.id;
            if (isLocal) {
                body.addEventListener("dragover", function(e) { e.preventDefault(); body.classList.add("drag-over"); });
                body.addEventListener("dragleave", function() { body.classList.remove("drag-over"); });
                body.addEventListener("drop", function(e) {
                    e.preventDefault(); body.classList.remove("drag-over");
                    var ticketId = e.dataTransfer.getData("text/plain");
                    if (ticketId) moveTicket(parseInt(ticketId, 10), lane.id, body, e.clientY);
                });
            }
            (lane.tickets || []).forEach(function(ticket) { body.appendChild(createTicketCard(ticket, isLocal)); });
            col.appendChild(body);
            container.appendChild(col);
        });
    }

    function _pollCliStatus(ticketId, btnEl) {
        // Poll the workflow audit log for the CLI session to complete
        var attempts = 0;
        var maxAttempts = 120; // 10 minutes at 5s intervals
        var interval = setInterval(function() {
            attempts++;
            if (attempts > maxAttempts) {
                clearInterval(interval);
                showSnackbar("CLI still running — check the audit log", "warning");
                if (btnEl) {
                    btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                    btnEl.classList.remove("text-orange-400");
                    btnEl.disabled = false;
                }
                return;
            }
            apiFetch("/api/workflows?type=pi_agent&limit=1&search=Ticket%20%23" + ticketId)
                .then(function(sessions) {
                    if (sessions && sessions.length > 0) {
                        var s = sessions[0];
                        if (s.status === "completed" || s.status === "failed") {
                            clearInterval(interval);
                            var icon = s.status === "completed"
                                ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
                                : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
                            if (btnEl) {
                                btnEl.innerHTML = icon;
                                btnEl.classList.remove("text-orange-400");
                                btnEl.disabled = false;
                            }
                            showSnackbar("CLI " + s.status + " for ticket #" + ticketId, s.status === "completed" ? "success" : "error");
                            // Reset icon after 5 seconds
                            setTimeout(function() {
                                if (btnEl) {
                                    btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                                }
                            }, 5000);
                        }
                    }
                })
                .catch(function() {}); // silently ignore poll errors
        }, 5000);
    }

    function createTicketCard(ticket, isLocal) {
        var card = document.createElement("div");
        card.className = "kb-card bg-[#1a1f3a] rounded-lg border border-white/20 p-3 cursor-pointer hover:border-[#f97316]/50 transition-colors relative";
        card.dataset.ticketId = String(ticket.id);
        if (isLocal) {
            card.draggable = true;
            card.addEventListener("dragstart", function(e) {
                e.dataTransfer.setData("text/plain", String(ticket.id));
                card.classList.add("dragging");
            });
            card.addEventListener("dragend", function() { card.classList.remove("dragging"); });
        }
        var priClass = "kb-pri-" + (ticket.priority || "medium");
        var todoCount = (ticket.todos || []).length;
        var todoDone = (ticket.todos || []).filter(function(t) { return t.done; }).length;
        var todoHtml = todoCount ? '<span class="text-xs text-gray-500 ml-2">✓ ' + todoDone + '/' + todoCount + '</span>' : '';
        card.innerHTML = '<div class="flex items-start justify-between gap-2">' +
            '<span class="text-sm text-white leading-snug flex-1">' + esc(ticket.title) + '</span>' +
            '<span class="' + priClass + ' text-[10px] px-1.5 py-0.5 rounded text-white font-medium flex-shrink-0">' + esc(ticket.priority || "medium") + '</span>' +
            '</div>' +
            (ticket.description ? '<p class="text-xs text-gray-500 mt-1 line-clamp-2">' + esc(ticket.description).substring(0, 100) + '</p>' : '') +
            (isLocal ? '<div class="flex items-center justify-center gap-3 mt-2 kb-card-actions">' +
                '<button class="kb-act-copy text-gray-500 hover:text-white transition-colors" title="Copy title &amp; description"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>' +
                '<button class="kb-act-cli text-gray-500 hover:text-orange-400 transition-colors" title="Push to CLI"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>' +
                '<button class="kb-act-cursor text-gray-500 hover:text-blue-400 transition-colors" title="Send to Cursor (.ticket)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></button>' +
                '<button class="kb-act-delete text-gray-500 hover:text-red-400 transition-colors" title="Delete ticket"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>' +
            '</div>' : '') +
            '<div class="flex items-center mt-2">' + todoHtml + '</div>';
        if (!isLocal) {
            var copyBtn = document.createElement("button");
            copyBtn.className = "kb-copy-btn absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded bg-[#f97316] text-white hover:bg-[#ea580c]";
            copyBtn.textContent = "⧉ Copy";
            copyBtn.title = "Copy to a database board";
            if (!dbBoards.length) { copyBtn.classList.add("disabled"); copyBtn.title = "No database boards available"; }
            copyBtn.onclick = function(e) { e.stopPropagation(); if (!dbBoards.length) return; openCopyModal(ticket); };
            card.appendChild(copyBtn);
        }
        card.addEventListener("click", function(e) {
            if (e.target.closest(".kb-copy-btn")) return;
            if (e.target.closest(".kb-card-actions")) return;
            if (isLocal) { openTicketModal(ticket.id); }
            else if (ticket.url) { window.open(ticket.url, "_blank"); }
        });
        // Wire up action buttons for local tickets
        if (isLocal) {
            var copyBtn2 = card.querySelector(".kb-act-copy");
            if (copyBtn2) copyBtn2.addEventListener("click", function(e) {
                e.stopPropagation();
                var text = ticket.title + (ticket.description ? "\n\n" + ticket.description : "");
                navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
            });
            var cliBtn = card.querySelector(".kb-act-cli");
            if (cliBtn) cliBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                if (!confirm("Push ticket #" + ticket.id + " to the project CLI?")) return;
                cliBtn.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
                cliBtn.classList.add("text-orange-400");
                cliBtn.disabled = true;
                apiFetch("/api/kanban/tickets/" + ticket.id + "/send-to-cli", { method: "POST" })
                    .then(function(r) {
                        showSnackbar(r.message || "Sent to CLI");
                        // Poll for completion
                        _pollCliStatus(ticket.id, cliBtn);
                    })
                    .catch(function(err) {
                        showSnackbar("CLI error: " + err.message, "error");
                        cliBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                        cliBtn.classList.remove("text-orange-400");
                        cliBtn.disabled = false;
                    });
            });
            var cursorBtn = card.querySelector(".kb-act-cursor");
            if (cursorBtn) cursorBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                apiFetch("/api/kanban/tickets/" + ticket.id + "/send-to-project", { method: "POST" })
                    .then(function(r) { showSnackbar(r.message || "Sent to project"); })
                    .catch(function(err) { showSnackbar("Error: " + err.message, "error"); });
            });
            var delBtn = card.querySelector(".kb-act-delete");
            if (delBtn) delBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                var tid = ticket.id;
                showKanbanConfirm({
                    title: "Delete ticket",
                    message: "Delete \"" + ticket.title + "\"? This cannot be undone.",
                    confirmLabel: "Delete",
                    danger: true,
                    onConfirm: function() {
                        hideKanbanConfirm();
                        apiFetch("/api/kanban/tickets/" + tid, { method: "DELETE" })
                            .then(function() {
                                showSnackbar("Ticket deleted");
                                reloadCurrentDatabaseBoard();
                            })
                            .catch(function(err) { showSnackbar("Delete failed: " + err.message, "error"); });
                    }
                });
            });
        }
        return card;
    }

    // ── Drag & drop move ──
    /**
     * 0-based index where the ticket should land in the target lane, from pointer Y.
     * Skips the dragged card in the same lane so reordering within a lane is not always "append".
     */
    function computeTicketDropPosition(bodyEl, ticketId, clientY) {
        var cards = Array.prototype.slice.call(bodyEl.querySelectorAll(".kb-card"));
        var dragEl = null;
        for (var i = 0; i < cards.length; i++) {
            if (cards[i].dataset.ticketId === String(ticketId)) {
                dragEl = cards[i];
                break;
            }
        }
        var pos = 0;
        for (var j = 0; j < cards.length; j++) {
            var c = cards[j];
            if (c === dragEl) continue;
            var r = c.getBoundingClientRect();
            if (clientY < r.top + r.height / 2) break;
            pos++;
        }
        return pos;
    }

    function moveTicket(ticketId, laneId, bodyEl, clientY) {
        var position = typeof clientY === "number"
            ? computeTicketDropPosition(bodyEl, ticketId, clientY)
            : bodyEl.querySelectorAll(".kb-card").length;
        apiFetch("/api/kanban/tickets/" + ticketId + "/move", {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: laneId, position: position })
        }).then(function() { selectBoard("database", currentBoard.id); })
        .catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    // ── Ticket modal ──

    function openTicketModal(ticketId) {
        modalTicketId = ticketId;
        switchTicketTab("details");
        apiFetch("/api/kanban/tickets/" + ticketId).then(function(t) {
            document.getElementById("kb-modal-ticket-title").value = t.title || "";
            document.getElementById("kb-modal-ticket-desc").value = t.description || "";
            setPriorityButtons(t.priority || "medium");
            renderModalLinks(t.links || []);
            renderModalFiles(t.files || []);
            renderModalTodos(t.todos || []);
            loadLinkableEntities(t);
            // Push to CLI checkbox
            var cliCb = document.getElementById("kb-modal-send-to-cli");
            var wfSel = document.getElementById("kb-modal-link-workflow");
            cliCb.checked = !!t.send_to_cli;
            wfSel.disabled = !!t.send_to_cli;
            if (t.send_to_cli) wfSel.value = "";
            cliCb.onchange = function() {
                wfSel.disabled = cliCb.checked;
                if (cliCb.checked) wfSel.value = "";
            };
            // Modal action buttons are always visible (wired up in init)
            document.getElementById("kb-ticket-modal").classList.remove("hidden");
        }).catch(function(e) { showSnackbar("Failed to load ticket: " + e.message, "error"); });
    }

    function closeTicketModal() {
        document.getElementById("kb-ticket-modal").classList.add("hidden");
        modalTicketId = null;
    }

    function setPriorityButtons(pri) {
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            var isActive = btn.dataset.pri === pri;
            btn.className = "px-3 py-1.5 rounded text-xs font-medium border " +
                (isActive ? "border-[#f97316] bg-[#f97316] text-white" : "border-white/20 text-gray-400");
        });
    }

    function getSelectedPriority() {
        var active = document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]");
        return active ? active.dataset.pri : "medium";
    }

    function saveTicket() {
        if (!modalTicketId) return;
        var sendToCli = document.getElementById("kb-modal-send-to-cli").checked;
        var payload = {
            title: document.getElementById("kb-modal-ticket-title").value.trim(),
            description: document.getElementById("kb-modal-ticket-desc").value.trim(),
            priority: getSelectedPriority(),
            linked_workflow_id: sendToCli ? null : (parseInt(document.getElementById("kb-modal-link-workflow").value) || null),
            send_to_cli: sendToCli,
            linked_snippet_id: null,
            linked_action_id: null,
        };
        if (!payload.title) { showSnackbar("Title is required", "error"); return; }
        apiFetch("/api/kanban/tickets/" + modalTicketId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function() {
            showSnackbar("Ticket saved"); closeTicketModal();
            selectBoard("database", currentBoard.id);
        }).catch(function(e) { showSnackbar("Save failed: " + e.message, "error"); });
    }

    function deleteTicket() {
        if (!modalTicketId) return;
        var tid = modalTicketId;
        showKanbanConfirm({
            title: "Delete ticket",
            message: "Delete this ticket? This cannot be undone.",
            confirmLabel: "Delete",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                apiFetch("/api/kanban/tickets/" + tid, { method: "DELETE" }).then(function() {
                    showSnackbar("Ticket deleted");
                    closeTicketModal();
                    reloadCurrentDatabaseBoard();
                }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
            }
        });
    }

    function sendTicketToProject() {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
            .then(function(data) {
                showSnackbar("Ticket sent to project: " + (data.project_name || ""));
            })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Modal: Links ──

    function renderModalLinks(links) {
        var container = document.getElementById("kb-modal-links");
        container.innerHTML = "";
        links.forEach(function(link) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<a href="' + esc(link.url) + '" target="_blank" class="text-[#f97316] hover:underline flex-1 truncate">' + esc(link.title) + '</a>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("button").onclick = function() { deleteLink(link.id); };
            container.appendChild(row);
        });
    }

    function addLink() {
        if (!modalTicketId) return;
        var title = document.getElementById("kb-modal-link-title").value.trim();
        var url = document.getElementById("kb-modal-link-url").value.trim();
        if (!title || !url) { showSnackbar("Title and URL required", "error"); return; }
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/links", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: title, url: url })
        }).then(function() {
            document.getElementById("kb-modal-link-title").value = "";
            document.getElementById("kb-modal-link-url").value = "";
            refreshModalTicket();
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    function deleteLink(linkId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/links/" + linkId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Modal: Files ──

    function renderModalFiles(files) {
        var container = document.getElementById("kb-modal-files");
        container.innerHTML = "";
        files.forEach(function(f) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<span class="text-gray-300 flex-1 truncate">📎 ' + esc(f.filename) + '</span>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("button").onclick = function() { deleteFile(f.id); };
            container.appendChild(row);
        });
    }

    function uploadFiles(fileList) {
        if (!modalTicketId || !fileList.length) return;
        var promises = [];
        for (var i = 0; i < fileList.length; i++) {
            var form = new FormData();
            form.append("file", fileList[i]);
            promises.push(apiFetch("/api/kanban/tickets/" + modalTicketId + "/files", { method: "POST", body: form }));
        }
        Promise.all(promises).then(function() {
            showSnackbar("Files uploaded"); refreshModalTicket();
        }).catch(function(e) { showSnackbar("Upload failed: " + e.message, "error"); });
    }

    function deleteFile(fileId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/files/" + fileId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Modal: Todos ──

    function renderModalTodos(todos) {
        var container = document.getElementById("kb-modal-todos");
        container.innerHTML = "";
        todos.forEach(function(todo) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<input type="checkbox" ' + (todo.done ? "checked" : "") + ' class="accent-[#f97316]">' +
                '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + esc(todo.text) + '</span>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("input").onchange = function() { toggleTodo(todo.id, !todo.done); };
            row.querySelector("button").onclick = function() { deleteTodo(todo.id); };
            container.appendChild(row);
        });
    }

    function addTodo() {
        if (!modalTicketId) return;
        var text = document.getElementById("kb-modal-todo-input").value.trim();
        if (!text) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
        }).then(function() {
            document.getElementById("kb-modal-todo-input").value = "";
            refreshModalTicket();
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    function toggleTodo(todoId, done) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos/" + todoId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ done: done })
        }).then(function() { refreshModalTicket(); }).catch(function() {});
    }

    function deleteTodo(todoId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos/" + todoId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); }).catch(function() {});
    }

    function refreshModalTicket() {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId).then(function(t) {
            renderModalLinks(t.links || []);
            renderModalFiles(t.files || []);
            renderModalTodos(t.todos || []);
        }).catch(function() {});
    }

    // ── Linkable entities ──

    function loadLinkableEntities(ticket) {
        apiFetch("/api/kanban/linkable").then(function(data) {
            populateSelect("kb-modal-link-workflow", data.workflows, "id", "title", ticket.linked_workflow_id);
        }).catch(function() {});
    }

    function populateSelect(selectId, items, valKey, labelKey, selectedVal) {
        var sel = document.getElementById(selectId);
        sel.innerHTML = '<option value="">None</option>';
        (items || []).forEach(function(item) {
            var opt = document.createElement("option");
            opt.value = item[valKey];
            opt.textContent = item[labelKey] || ("Item #" + item[valKey]);
            if (selectedVal && String(item[valKey]) === String(selectedVal)) opt.selected = true;
            sel.appendChild(opt);
        });
    }

    // ── Board modal (tabbed: Details + Advanced) ──

    var _agentProviders = [];  // cached provider list

    function _populateProviderDropdowns(selectIds) {
        selectIds.forEach(function(selId) {
            var sel = document.getElementById(selId);
            if (!sel) return;
            var cur = sel.value;
            sel.innerHTML = '<option value="">(chat default)</option>';
            _agentProviders.forEach(function(p) {
                var opt = document.createElement("option");
                opt.value = p.id; opt.textContent = p.name;
                sel.appendChild(opt);
            });
            if (cur) sel.value = cur;
        });
    }

    function loadAgentProviders() {
        return apiFetch("/api/llms/available-providers").then(function(data) {
            _agentProviders = data.providers || [{ id: "ollama", name: "Ollama" }];
        }).catch(function() {
            _agentProviders = [{ id: "ollama", name: "Ollama" }];
        });
    }

    function loadAgentModels(prefix, provider, selectedModel) {
        var sel = document.getElementById(prefix + "-model");
        if (!provider) {
            sel.innerHTML = '<option value="">(chat default)</option>';
            return Promise.resolve();
        }
        sel.innerHTML = '<option value="">Loading...</option>';
        sel.disabled = true;
        return apiFetch("/api/llms/models?type=conversational&provider=" + encodeURIComponent(provider)).then(function(data) {
            var models = data.models || [];
            sel.innerHTML = '<option value="">(chat default)</option>';
            models.forEach(function(m) {
                var opt = document.createElement("option");
                opt.value = (typeof m === "object" && m.id != null) ? m.id : m;
                opt.textContent = (typeof m === "object" && m.name != null) ? m.name : (m || "");
                sel.appendChild(opt);
            });
            if (selectedModel) {
                sel.value = selectedModel;
                if (!sel.value && selectedModel) {
                    var extra = document.createElement("option");
                    extra.value = selectedModel; extra.textContent = selectedModel;
                    sel.appendChild(extra); sel.value = selectedModel;
                }
            }
        }).catch(function() {
            sel.innerHTML = '<option value="">(chat default)</option>';
        }).then(function() { sel.disabled = false; });
    }

    function loadBoardDefaults(data) {
        apiFetch("/api/kanban/linkable").then(function(ld) {
            populateSelect("kb-board-def-workflow", ld.workflows, "id", "title", data ? data.default_workflow_id : null);
            populateSelect("kb-board-def-project", ld.projects, "id", "name", data ? data.default_project_id : null);

        }).catch(function() {});
    }

    function openBoardModal(boardId) {
        // Ensure boardId is a number (not string from URL params etc.)
        boardId = boardId ? parseInt(boardId, 10) : null;
        editingBoardId = boardId;
        document.getElementById("kb-board-modal-title").textContent = boardId ? "Edit Board" : "New Board";
        document.getElementById("kb-board-modal-save").textContent = boardId ? "Save" : "Create";
        // Reset to Details tab
        switchBoardModalTab("details");

        if (boardId && currentBoardData && currentBoardData.id == boardId) {
            populateBoardModal(currentBoardData);
        } else if (boardId) {
            apiFetch("/api/kanban/boards/" + boardId).then(function(data) {
                populateBoardModal(data);
            }).catch(function() {});
        } else {
            document.getElementById("kb-board-modal-name").value = "";
            document.getElementById("kb-board-modal-desc").value = "";
            document.getElementById("kb-board-modal-agent-enabled").checked = false;
            var colorInput = document.getElementById("kb-board-modal-color");
            var colorHex = document.getElementById("kb-board-modal-color-hex");
            colorInput.value = "#f97316";
            colorHex.textContent = "#f97316";
            loadBoardDefaults(null);
        }
        document.getElementById("kb-board-modal").classList.remove("hidden");
        if (typeof injectInfoIcons === 'function') injectInfoIcons();
    }

    function populateBoardModal(data) {
        document.getElementById("kb-board-modal-name").value = data.name || "";
        document.getElementById("kb-board-modal-desc").value = data.description || "";
        document.getElementById("kb-board-modal-agent-enabled").checked = !!data.agent_enabled;
        var colorInput = document.getElementById("kb-board-modal-color");
        var colorHex = document.getElementById("kb-board-modal-color-hex");
        var c = data.color || "#f97316";
        colorInput.value = c;
        colorHex.textContent = c;
        loadBoardDefaults(data);
    }

    function switchBoardModalTab(tab) {
        document.querySelectorAll(".kb-bm-tab").forEach(function(btn) {
            var isActive = btn.dataset.tab === tab;
            btn.classList.toggle("active", isActive);
            btn.classList.toggle("text-white", isActive);
            btn.classList.toggle("text-gray-400", !isActive);
            btn.style.borderColor = isActive ? "#f97316" : "transparent";
        });
        document.querySelectorAll(".kb-bm-pane").forEach(function(pane) { pane.classList.add("hidden"); });
        var pane = document.getElementById("kb-bm-tab-" + tab);
        if (pane) pane.classList.remove("hidden");
        // Load WhatsApp links when switching to that tab
        if (tab === "whatsapp" && editingBoardId) {
            loadBoardWaLinks(editingBoardId);
        }
    }

    function switchTicketTab(tab) {
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            var isActive = btn.dataset.ttab === tab;
            btn.classList.toggle("active", isActive);
            btn.classList.toggle("text-white", isActive);
            btn.classList.toggle("text-gray-400", !isActive);
            btn.style.borderColor = isActive ? "#f97316" : "transparent";
        });
        document.querySelectorAll(".kb-tm-pane").forEach(function(pane) { pane.classList.add("hidden"); });
        var pane = document.getElementById("kb-tm-tab-" + tab);
        if (pane) pane.classList.remove("hidden");
    }

    function closeBoardModal() {
        document.getElementById("kb-board-modal").classList.add("hidden");
        editingBoardId = null;
    }

    function saveBoardModal() {
        var name = document.getElementById("kb-board-modal-name").value.trim();
        if (!name) { showSnackbar("Board name is required", "error"); return; }
        var boardColor = document.getElementById("kb-board-modal-color").value || "";
        var payload = {
            name: name,
            description: document.getElementById("kb-board-modal-desc").value.trim(),
            default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value) || 0,
            default_project_id: parseInt(document.getElementById("kb-board-def-project").value) || 0,
            color: boardColor,
            agent_enabled: document.getElementById("kb-board-modal-agent-enabled").checked,
        };
        if (editingBoardId) {
            apiFetch("/api/kanban/boards/" + editingBoardId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function() {
                showSnackbar("Board updated"); closeBoardModal(); loadBoards(true);
                if (currentBoard && currentBoard.id === editingBoardId) selectBoard("database", editingBoardId);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
        } else {
            apiFetch("/api/kanban/boards", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: payload.name, description: payload.description })
            }).then(function(data) {
                // After create, save board defaults
                return apiFetch("/api/kanban/boards/" + data.id, {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        default_workflow_id: payload.default_workflow_id,
                        default_project_id: payload.default_project_id
                    })
                }).then(function() { return data; });
            }).then(function(data) {
                showSnackbar("Board created"); closeBoardModal(); loadBoards(true);
                selectBoard("database", data.id);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
        }
    }

    function deleteBoard() {
        if (!currentBoard || currentBoard.source !== "database") return;
        if (!confirm("Delete this board and all its tickets? This cannot be undone.")) return;
        apiFetch("/api/kanban/boards/" + currentBoard.id, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            currentBoard = null; currentBoardData = null;
            document.getElementById("kb-board-view").classList.add("hidden");
            document.getElementById("kb-empty").classList.remove("hidden");
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Add ticket ──

    function addTicket() {
        if (!currentBoard || currentBoard.source !== "database" || !currentBoardData) return;
        var firstLane = (currentBoardData.lanes || [])[0];
        if (!firstLane) { showSnackbar("Board has no lanes", "error"); return; }
        apiFetch("/api/kanban/tickets", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: firstLane.id, title: "New Ticket", priority: "medium" })
        }).then(function(data) {
            selectBoard("database", currentBoard.id);
            setTimeout(function() { openTicketModal(data.id); }, 300);
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Copy external ticket to local board ──

    function openCopyModal(ticket) {
        copyTicketData = { title: ticket.title || "", description: ticket.description || "" };
        var sel = document.getElementById("kb-copy-board-select");
        sel.innerHTML = "";
        if (!dbBoards.length) {
            sel.innerHTML = '<option value="">No boards available</option>';
            document.getElementById("kb-copy-confirm").disabled = true;
        } else {
            document.getElementById("kb-copy-confirm").disabled = false;
            dbBoards.forEach(function(b) {
                var opt = document.createElement("option");
                opt.value = b.id; opt.textContent = b.name;
                sel.appendChild(opt);
            });
        }
        document.getElementById("kb-copy-modal").classList.remove("hidden");
    }

    function closeCopyModal() {
        document.getElementById("kb-copy-modal").classList.add("hidden");
        copyTicketData = null;
    }

    function confirmCopy() {
        if (!copyTicketData) return;
        var boardId = parseInt(document.getElementById("kb-copy-board-select").value);
        if (!boardId) { showSnackbar("Select a board", "error"); return; }
        apiFetch("/api/kanban/tickets/copy-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ board_id: boardId, title: copyTicketData.title, description: copyTicketData.description })
        }).then(function() {
            showSnackbar("Ticket copied to board"); closeCopyModal();
        }).catch(function(e) { showSnackbar("Copy failed: " + e.message, "error"); });
    }

    // ── Global Settings Modal ──

    var gsFreq = "daily";
    var gsHours = [];
    var gsDays = [];

    function loadGlobalKanbanSettings() {
        apiFetch("/api/kanban/settings").then(function(s) {

            // LLM Configuration — populate provider dropdowns then load models
            _populateProviderDropdowns(["kb-gs-orch-provider", "kb-gs-coder-provider", "kb-gs-sub-provider"]);

            var orchProv = s.kanban_agent_orchestrator_provider || "";
            var coderProv = s.kanban_agent_coder_provider || "";
            var subProv = s.kanban_agent_sub_provider || "";
            document.getElementById("kb-gs-orch-provider").value = orchProv;
            document.getElementById("kb-gs-coder-provider").value = coderProv;
            document.getElementById("kb-gs-sub-provider").value = subProv;
            loadAgentModels("kb-gs-orch", orchProv, s.kanban_agent_orchestrator_model || "");
            loadAgentModels("kb-gs-coder", coderProv, s.kanban_agent_coder_model || "");
            loadAgentModels("kb-gs-sub", subProv, s.kanban_agent_sub_model || "");

            // Advanced — agent toggle
            document.getElementById("kb-gs-agent-enabled").checked = !!s.kanban_agent_enabled;

            // Frequency
            gsFreq = s.kanban_agent_frequency || "daily";
            document.getElementById("kb-gs-frequency").value = gsFreq;
            updateGsFrequencyUI(gsFreq);

            // Hour picker
            gsHours = [];
            try { gsHours = typeof s.kanban_agent_hours === "string" ? JSON.parse(s.kanban_agent_hours) : (s.kanban_agent_hours || []); } catch (e) { gsHours = []; }
            document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                var h = parseInt(btn.dataset.hour);
                btn.setAttribute("data-selected", gsHours.indexOf(h) >= 0 ? "1" : "0");
            });

            // Day picker
            gsDays = [];
            try { gsDays = typeof s.kanban_agent_days === "string" ? JSON.parse(s.kanban_agent_days) : (s.kanban_agent_days || []); } catch (e) { gsDays = []; }
            document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
                var d = parseInt(btn.dataset.day);
                btn.setAttribute("data-selected", gsDays.indexOf(d) >= 0 ? "1" : "0");
            });

            // Monthly day
            document.getElementById("kb-gs-monthly-day-input").value = String(s.kanban_agent_monthly_day || 1);

            // Time
            document.getElementById("kb-gs-time-input").value = s.kanban_agent_time || "09:00";

            // Lane routing — populate dropdowns from board lanes
            var laneNames = [];
            if (currentBoardData && currentBoardData.lanes) {
                laneNames = currentBoardData.lanes.map(function(l) { return l.name || l.title || ""; }).filter(Boolean);
            }
            var srcSel = document.getElementById("kb-gs-source-lane");
            var doneSel = document.getElementById("kb-gs-done-lane");
            var srcVal = s.kanban_agent_source_lane || "";
            var doneVal = s.kanban_agent_done_lane || "";
            [srcSel, doneSel].forEach(function(sel) {
                sel.innerHTML = '<option value="">Select lane…</option>';
                laneNames.forEach(function(name) {
                    var opt = document.createElement("option");
                    opt.value = name; opt.textContent = name;
                    sel.appendChild(opt);
                });
            });
            srcSel.value = srcVal;
            doneSel.value = doneVal;

        }).catch(function(e) { showSnackbar("Failed to load settings: " + e.message, "error"); });
    }

    function saveGlobalKanbanSettings() {
        // Collect selected hours
        var hours = [];
        document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
            if (btn.getAttribute("data-selected") === "1") hours.push(parseInt(btn.dataset.hour));
        });
        // Deduplicate hours
        hours = hours.filter(function(v, i, a) { return a.indexOf(v) === i; });

        // Collect selected days
        var days = [];
        document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
            if (btn.getAttribute("data-selected") === "1") days.push(parseInt(btn.dataset.day));
        });

        var payload = {
            kanban_agent_orchestrator_provider: document.getElementById("kb-gs-orch-provider").value,
            kanban_agent_orchestrator_model: document.getElementById("kb-gs-orch-model").value,
            kanban_agent_coder_provider: document.getElementById("kb-gs-coder-provider").value,
            kanban_agent_coder_model: document.getElementById("kb-gs-coder-model").value,
            kanban_agent_sub_provider: document.getElementById("kb-gs-sub-provider").value,
            kanban_agent_sub_model: document.getElementById("kb-gs-sub-model").value,
            kanban_agent_enabled: document.getElementById("kb-gs-agent-enabled").checked,
            kanban_agent_frequency: document.getElementById("kb-gs-frequency").value,
            kanban_agent_time: document.getElementById("kb-gs-time-input").value || "09:00",
            kanban_agent_hours: hours,
            kanban_agent_days: days,
            kanban_agent_monthly_day: parseInt(document.getElementById("kb-gs-monthly-day-input").value) || 1,
            kanban_agent_source_lane: document.getElementById("kb-gs-source-lane").value,
            kanban_agent_done_lane: document.getElementById("kb-gs-done-lane").value
        };

        apiFetch("/api/kanban/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function() {
            showSnackbar("Settings saved");
            closeGlobalSettingsModal();
        }).catch(function(e) { showSnackbar("Save failed: " + e.message, "error"); });
    }

    function openGlobalSettingsModal() {
        // Ensure providers are loaded, then load settings
        apiFetch("/api/llms/available-providers").then(function(data) {
            _agentProviders = data.providers || [{ id: "ollama", name: "Ollama" }];
        }).catch(function() {
            _agentProviders = [{ id: "ollama", name: "Ollama" }];
        }).then(function() {
            loadGlobalKanbanSettings();
        });
        document.getElementById("kb-global-settings-modal").classList.remove("hidden");
    }

    function closeGlobalSettingsModal() {
        document.getElementById("kb-global-settings-modal").classList.add("hidden");
    }

    function updateGsFrequencyUI(freq) {
        var hourPicker = document.getElementById("kb-gs-hour-picker");
        var dayPicker = document.getElementById("kb-gs-day-picker");
        var monthlyDay = document.getElementById("kb-gs-monthly-day");
        var timePicker = document.getElementById("kb-gs-time");

        var isMinuteBased = freq.endsWith("min");
        hourPicker.classList.toggle("hidden", freq !== "hourly");
        dayPicker.classList.toggle("hidden", freq !== "weekly" && freq !== "fortnightly");
        monthlyDay.classList.toggle("hidden", freq !== "monthly");
        timePicker.classList.toggle("hidden", freq === "hourly" || isMinuteBased);
    }

    // ── Event bindings ──

    // ── Event bindings ──

    function init() {
        // Board sidebar
        document.getElementById("kb-add-board").addEventListener("click", function() { openBoardModal(null); });
        document.getElementById("kb-create-big").addEventListener("click", function() { openBoardModal(null); });
        document.getElementById("kb-checkin-btn").addEventListener("click", function() {
            var btn = this;
            btn.disabled = true; btn.textContent = "Running…";
            apiFetch("/api/kanban/agent/checkin", { method: "POST" }).then(function(data) {
                showSnackbar(data.message || "Check-in complete");
                if (currentBoard) selectBoard(currentBoard.source, currentBoard.id);
            }).catch(function(e) {
                showSnackbar("Check-in failed: " + e.message, "error");
            }).finally(function() {
                btn.disabled = false; btn.textContent = "Check-in";
            });
        });
        document.getElementById("kb-search").addEventListener("input", function() { loadBoards(); });

        // Global settings modal
        document.getElementById("kb-settings-cog").addEventListener("click", openGlobalSettingsModal);
        document.getElementById("kb-gs-close").addEventListener("click", closeGlobalSettingsModal);
        document.getElementById("kb-gs-cancel").addEventListener("click", closeGlobalSettingsModal);
        document.getElementById("kb-gs-save").addEventListener("click", saveGlobalKanbanSettings);
        document.getElementById("kb-global-settings-modal").addEventListener("click", function(e) {
            // Clicking overlay does NOT close — use close/cancel buttons
        });

        // Global settings frequency change
        document.getElementById("kb-gs-frequency").addEventListener("change", function() {
            gsFreq = this.value;
            updateGsFrequencyUI(gsFreq);
        });

        // Global settings hour picker toggles
        document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var cur = btn.getAttribute("data-selected") === "1";
                btn.setAttribute("data-selected", cur ? "0" : "1");
            });
        });

        // Global settings day picker toggles
        document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var cur = btn.getAttribute("data-selected") === "1";
                btn.setAttribute("data-selected", cur ? "0" : "1");
            });
        });

        // Global settings LLM provider change → reload models
        ["kb-gs-orch", "kb-gs-coder", "kb-gs-sub"].forEach(function(prefix) {
            document.getElementById(prefix + "-provider").addEventListener("change", function() {
                loadAgentModels(prefix, this.value, "");
            });
        });

        // Board actions
        document.getElementById("kb-add-ticket").addEventListener("click", addTicket);
        document.getElementById("kb-edit-board").addEventListener("click", function() {
            if (currentBoard && currentBoard.source === "database" && currentBoard.id) {
                openBoardModal(currentBoard.id);
            } else if (currentBoardData && currentBoardData.id) {
                openBoardModal(currentBoardData.id);
            }
        });
        document.getElementById("kb-delete-board").addEventListener("click", deleteBoard);

        // Board modal tabs
        document.querySelectorAll(".kb-bm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchBoardModalTab(btn.dataset.tab); });
        });

        // Ticket modal tabs
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchTicketTab(btn.dataset.ttab); });
        });

        // Board modal
        document.getElementById("kb-board-modal-cancel").addEventListener("click", closeBoardModal);
        document.getElementById("kb-board-modal-x").addEventListener("click", closeBoardModal);
        document.getElementById("kb-board-modal-save").addEventListener("click", saveBoardModal);
        document.getElementById("kb-board-modal").addEventListener("click", function(e) {
        });
        // Color picker sync
        document.getElementById("kb-board-modal-color").addEventListener("input", function() {
            document.getElementById("kb-board-modal-color-hex").textContent = this.value;
        });
        document.getElementById("kb-board-modal-color-reset").addEventListener("click", function() {
            var colorInput = document.getElementById("kb-board-modal-color");
            colorInput.value = "#f97316";
            document.getElementById("kb-board-modal-color-hex").textContent = "#f97316";
        });

        // Ticket modal
        document.getElementById("kb-modal-close").addEventListener("click", closeTicketModal);
        document.getElementById("kb-modal-save").addEventListener("click", saveTicket);
        document.getElementById("kb-modal-delete").addEventListener("click", deleteTicket);
        // Modal action buttons (copy, CLI, cursor)
        document.getElementById("kb-modal-act-copy").addEventListener("click", function() {
            if (!modalTicketId) return;
            apiFetch("/api/kanban/tickets/" + modalTicketId).then(function(t) {
                var text = t.title + (t.description ? "\n\n" + t.description : "");
                navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
            });
        });
        document.getElementById("kb-modal-act-cli").addEventListener("click", function() {
            if (!modalTicketId) return;
            if (!confirm("Push ticket #" + modalTicketId + " to the project CLI?")) return;
            var btn = document.getElementById("kb-modal-act-cli");
            btn.innerHTML = '<svg class="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
            btn.classList.add("text-orange-400");
            var tid = modalTicketId;
            apiFetch("/api/kanban/tickets/" + tid + "/send-to-cli", { method: "POST" })
                .then(function(r) {
                    showSnackbar(r.message || "Sent to CLI");
                    _pollCliStatus(tid, btn);
                })
                .catch(function(e) {
                    showSnackbar("CLI error: " + e.message, "error");
                    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                    btn.classList.remove("text-orange-400");
                });
        });
        document.getElementById("kb-modal-act-cursor").addEventListener("click", function() {
            if (!modalTicketId) return;
            apiFetch("/api/kanban/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
                .then(function(r) { showSnackbar("Sent to project: " + (r.project_name || "")); })
                .catch(function(e) { showSnackbar("Error: " + e.message, "error"); });
        });
        document.getElementById("kb-ticket-modal").addEventListener("click", function(e) {
        });

        // Priority buttons
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.addEventListener("click", function() { setPriorityButtons(btn.dataset.pri); });
        });

        // Links
        document.getElementById("kb-modal-add-link").addEventListener("click", addLink);
        document.getElementById("kb-modal-link-url").addEventListener("keydown", function(e) {
            if (e.key === "Enter") addLink();
        });

        // Files
        document.getElementById("kb-modal-upload-btn").addEventListener("click", function() {
            document.getElementById("kb-modal-file-input").click();
        });
        document.getElementById("kb-modal-file-input").addEventListener("change", function() {
            uploadFiles(this.files); this.value = "";
        });

        // Todos
        document.getElementById("kb-modal-add-todo").addEventListener("click", addTodo);
        document.getElementById("kb-modal-todo-input").addEventListener("keydown", function(e) {
            if (e.key === "Enter") addTodo();
        });

        // Confirm modal (ticket delete)
        document.getElementById("kb-confirm-cancel").addEventListener("click", hideKanbanConfirm);
        document.getElementById("kb-confirm-ok").addEventListener("click", function() {
            if (_kbConfirmCallback) _kbConfirmCallback();
        });
        document.getElementById("kb-confirm-modal").addEventListener("click", function(e) {
            if (e.target === this) hideKanbanConfirm();
        });
        document.addEventListener("keydown", function(e) {
            if (e.key === "Escape" && !document.getElementById("kb-confirm-modal").classList.contains("hidden")) {
                hideKanbanConfirm();
            }
        });

        // Copy modal
        document.getElementById("kb-copy-cancel").addEventListener("click", closeCopyModal);
        document.getElementById("kb-copy-confirm").addEventListener("click", confirmCopy);
        document.getElementById("kb-copy-modal").addEventListener("click", function(e) {
        });

    // ═══════════════════════════════════════════════════════════════════
    // WhatsApp Chat/Messages Integration
    // ═══════════════════════════════════════════════════════════════════

    var waChats = [];
    var waSelectedJid = null;
    var waCtxMenuData = null;  // { jid, phone, name }
    var waMsgCtxData = null;   // { message_id (db id) }

    function loadWhatsAppChats(forceRefresh) {
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");
        var searchEl = document.getElementById("kb-wa-search");

        el.textContent = "Loading...";
        apiFetch("/api/kanban/whatsapp/chats?limit=100").then(function(data) {
            waChats = data.chats || [];
            if (data.error) {
                el.textContent = data.error;
                chatListEl.innerHTML = "";
                searchEl.classList.add("hidden");
                return;
            }
            el.textContent = "";
            searchEl.classList.remove("hidden");
            renderWhatsAppChatList();
        }).catch(function(err) {
            el.textContent = "WhatsApp not connected";
            chatListEl.innerHTML = "";
            searchEl.classList.add("hidden");
        });
    }

    function renderWhatsAppChatList() {
        var chatListEl = document.getElementById("kb-wa-chats");
        var searchVal = (document.getElementById("kb-wa-search").value || "").toLowerCase();
        var filtered = waChats;
        if (searchVal) {
            filtered = waChats.filter(function(c) {
                return (c.name || "").toLowerCase().includes(searchVal) || (c.id || "").includes(searchVal);
            });
        }
        if (!filtered.length) {
            chatListEl.innerHTML = '<div class="text-xs text-gray-500 italic py-2">No chats found</div>';
            return;
        }
        var html = "";
        filtered.forEach(function(chat) {
            var phone = (chat.id || "").split("@")[0].split(":")[0];
            var name = esc(chat.name || phone);
            var isGroup = chat.is_group;
            var unread = chat.unread_count || 0;
            var active = waSelectedJid === chat.id ? " bg-[#25D366]/10 border-l-2 border-[#25D366]" : "";
            var lastTs = chat.last_message_timestamp;
            var timeStr = "";
            if (lastTs) {
                var d = new Date(lastTs * 1000);
                timeStr = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
            }
            html += '<div class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs hover:bg-white/5' + active + '" data-wa-jid="' + esc(chat.id) + '" data-wa-phone="' + esc(phone) + '" data-wa-name="' + esc(name) + '">';
            html += '<div class="w-8 h-8 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-xs font-bold flex-shrink-0">';
            html += esc(name.charAt(0).toUpperCase());
            html += '</div>';
            html += '<div class="flex-1 min-w-0">';
            html += '<div class="flex items-center justify-between">';
            html += '<span class="text-white truncate">' + (isGroup ? '👥 ' : '') + name + '</span>';
            html += '<span class="text-gray-500 text-[10px] ml-1 flex-shrink-0">' + timeStr + '</span>';
            html += '</div>';
            html += '<div class="text-gray-500 truncate">' + esc(phone) + (unread ? ' <span class="text-[#25D366] font-bold">(' + unread + ')</span>' : '') + '</div>';
            html += '</div></div>';
        });
        chatListEl.innerHTML = html;

        // Click handlers
        chatListEl.querySelectorAll("[data-wa-jid]").forEach(function(el) {
            el.addEventListener("click", function() {
                waSelectedJid = el.dataset.waJid;
                renderWhatsAppChatList();
                openWhatsAppMessagePanel(el.dataset.waJid, el.dataset.waPhone, el.dataset.waName);
            });
            el.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                waCtxMenuData = { jid: el.dataset.waJid, phone: el.dataset.waPhone, name: el.dataset.waName };
                showWaChatContextMenu(e.clientX, e.clientY);
            });
        });
    }

    function openWhatsAppMessagePanel(jid, phone, name) {
        var panel = document.getElementById("kb-wa-msg-panel");
        var title = document.getElementById("kb-wa-msg-title");
        var msgList = document.getElementById("kb-wa-msg-list");
        var countEl = document.getElementById("kb-wa-msg-count");

        title.textContent = name;
        countEl.textContent = "Loading...";
        msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">Loading messages...</div>';
        panel.classList.remove("hidden");

        apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=200").then(function(data) {
            var messages = data.messages || [];
            countEl.textContent = messages.length + " messages";
            if (!messages.length) {
                msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">No messages from this number yet</div>';
                return;
            }
            var html = "";
            messages.forEach(function(msg) {
                var isMine = msg.from_me;
                var align = isMine ? "justify-end" : "justify-start";
                var bg = isMine ? "bg-[#005c4b]" : "bg-[#1f2c34]";
                var timestamp = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                var timeStr = timestamp ? timestamp.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
                var senderName = esc(msg.sender_push_name || msg.sender_phone || "");

                html += '<div class="flex ' + align + '" data-wa-msg-id="' + msg.id + '">';
                html += '<div class="max-w-[85%] rounded-lg px-3 py-2 ' + bg + '" style="border-radius: 8px;">';

                // Sender name (for group chats)
                if (!isMine && msg.chat_type === "group") {
                    html += '<div class="text-xs text-[#25D366] mb-1">' + senderName + '</div>';
                }

                // Media preview
                if (msg.media_type && msg.media_local_path) {
                    var mediaPath = "/api/kanban/whatsapp/media?path=" + encodeURIComponent(msg.media_local_path);
                    if (msg.media_type === "photo" || msg.media_type === "image") {
                        html += '<div class="mb-1 rounded overflow-hidden"><img src="' + esc(mediaPath) + '" class="max-w-full max-h-[300px] rounded cursor-pointer" loading="lazy" onclick="window.open(\'' + esc(mediaPath) + '\', \'_blank\')"></div>';
                    } else if (msg.media_type === "voice" || msg.media_type === "audio") {
                        html += '<div class="mb-1"><audio controls class="max-w-full" style="height:36px;"><source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "audio/ogg") + '"></audio></div>';
                    } else if (msg.media_type === "video") {
                        html += '<div class="mb-1 rounded overflow-hidden"><video controls class="max-w-full max-h-[300px]"><source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "video/mp4") + '"></video></div>';
                    } else {
                        // Document / sticker / other
                        var icon = msg.media_type === "sticker" ? "🏷️" : "📎";
                        var fname = esc(msg.media_filename || msg.media_local_path.split("/").pop());
                        var sizeStr = msg.media_file_length ? (msg.media_file_length < 1024*1024 ? (msg.media_file_length/1024).toFixed(1) + " KB" : (msg.media_file_length/1024/1024).toFixed(1) + " MB") : "";
                        html += '<div class="mb-1 flex items-center gap-2 px-2 py-1 bg-black/20 rounded">';
                        html += '<span>' + icon + '</span>';
                        html += '<a href="' + esc(mediaPath) + '" class="text-blue-400 underline text-xs truncate flex-1" target="_blank">' + fname + '</a>';
                        html += '<span class="text-gray-500 text-[10px]">' + sizeStr + '</span>';
                        html += '</div>';
                    }
                } else if (msg.media_type && !msg.media_local_path) {
                    // Media not yet downloaded
                    var mediaIcon = { photo: "🖼️", voice: "🎙️", audio: "🎵", video: "🎬", document: "📄", sticker: "🏷️" };
                    html += '<div class="mb-1 px-2 py-1 bg-black/20 rounded text-xs text-gray-400">' + (mediaIcon[msg.media_type] || "📎") + ' ' + esc(msg.media_type) + (msg.media_filename ? ": " + esc(msg.media_filename) : "") + '</div>';
                }

                // Caption (for media messages)
                if (msg.caption) {
                    html += '<div class="text-sm text-white whitespace-pre-wrap">' + esc(msg.caption) + '</div>';
                }

                // Text message
                if (msg.text) {
                    html += '<div class="text-sm text-white whitespace-pre-wrap">' + esc(msg.text) + '</div>';
                }

                // Timestamp + status
                html += '<div class="flex items-center justify-end gap-1 mt-0.5">';
                html += '<span class="text-[10px] text-gray-500">' + timeStr + '</span>';
                if (isMine) html += '<span class="text-[10px] text-blue-400">✓✓</span>';
                if (msg.processed) html += '<span class="text-[10px] text-green-400" title="Processed">✓</span>';
                html += '</div>';

                html += '</div></div>';
            });
            msgList.innerHTML = html;

            // Right-click on messages
            msgList.querySelectorAll("[data-wa-msg-id]").forEach(function(el) {
                el.addEventListener("contextmenu", function(e) {
                    e.preventDefault();
                    waMsgCtxData = { message_id: parseInt(el.dataset.waMsgId) };
                    showWaMsgContextMenu(e.clientX, e.clientY);
                });
            });
        }).catch(function(err) {
            countEl.textContent = "Error";
            msgList.innerHTML = '<div class="text-sm text-red-400 text-center py-8">Failed to load messages</div>';
        });
    }

    function showWaChatContextMenu(x, y) {
        var menu = document.getElementById("kb-wa-ctx-menu");
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        // Position adjustment if off-screen
        setTimeout(function() {
            var rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + "px";
            if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + "px";
        }, 0);
    }

    function hideWaChatContextMenu() {
        document.getElementById("kb-wa-ctx-menu").classList.add("hidden");
    }

    function showWaMsgContextMenu(x, y) {
        var menu = document.getElementById("kb-wa-msg-ctx-menu");
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        setTimeout(function() {
            var rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + "px";
            if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + "px";
        }, 0);
    }

    function hideWaMsgContextMenu() {
        document.getElementById("kb-wa-msg-ctx-menu").classList.add("hidden");
    }

    // ── WhatsApp chat context menu actions ──

    function waCtxViewMessages() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        waSelectedJid = waCtxMenuData.jid;
        renderWhatsAppChatList();
        openWhatsAppMessagePanel(waCtxMenuData.jid, waCtxMenuData.phone, waCtxMenuData.name);
    }

    function waCtxLinkToBoard() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        openWaLinkModal(waCtxMenuData);
    }

    function waCtxSnapshotToBoard() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        // First show board picker, then snapshot all unprocessed messages
        var phone = waCtxMenuData.phone;
        // Get db boards for selection
        apiFetch("/api/kanban/boards").then(function(boards) {
            var dbBs = boards.filter(function(b) { return b.source === "database"; });
            if (!dbBs.length) { showSnackbar("No database boards to snapshot into"); return; }
            // Use current board or first board
            var targetBoard = (currentBoard && currentBoard.source === "database") ? currentBoard.id : dbBs[0].id;
            // Fetch unprocessed messages for this phone
            apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500&unprocessed_only=true").then(function(data) {
                var msgs = data.messages || [];
                if (!msgs.length) { showSnackbar("No unprocessed messages from " + waCtxMenuData.name); return; }
                var created = 0, errors = 0;
                var promises = msgs.map(function(msg) {
                    return apiFetch("/api/kanban/tickets/from-whatsapp/" + msg.id, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ board_id: targetBoard })
                    }).then(function(r) {
                        if (r.success) created++;
                    }).catch(function() { errors++; });
                });
                Promise.all(promises).then(function() {
                    showSnackbar("Created " + created + " ticket(s) from WhatsApp messages" + (errors ? " (" + errors + " errors)" : ""));
                    reloadCurrentDatabaseBoard();
                });
            });
        });
    }

    // ── WhatsApp link modal ──

    function openWaLinkModal(chatData) {
        var modal = document.getElementById("kb-wa-link-modal");
        var phoneEl = document.getElementById("kb-wa-link-phone");
        var boardSelect = document.getElementById("kb-wa-link-board");

        phoneEl.textContent = (chatData.name || "") + " (" + chatData.phone + ")";

        // Populate board dropdown
        apiFetch("/api/kanban/boards").then(function(boards) {
            var dbBs = boards.filter(function(b) { return b.source === "database"; });
            boardSelect.innerHTML = '<option value="">Select a board...</option>';
            dbBs.forEach(function(b) {
                var selected = (currentBoard && currentBoard.id === b.id) ? " selected" : "";
                boardSelect.innerHTML += '<option value="' + b.id + '"' + selected + '>' + esc(b.name) + '</option>';
            });
        });

        modal.classList.remove("hidden");
    }

    function confirmWaLink() {
        var boardId = parseInt(document.getElementById("kb-wa-link-board").value);
        if (!boardId) { showSnackbar("Select a board"); return; }
        var autoSnapshot = document.getElementById("kb-wa-link-auto").checked;

        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                phone_jid: waCtxMenuData.jid,
                phone_number: waCtxMenuData.phone,
                contact_name: waCtxMenuData.name,
                auto_snapshot: autoSnapshot,
            })
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Linked " + waCtxMenuData.name + " to board");
                document.getElementById("kb-wa-link-modal").classList.add("hidden");
            }
        }).catch(function(err) {
            showSnackbar("Failed to link: " + err.message);
        });
    }

    // ── WhatsApp message context menu actions ──

    function waMsgCtxCreateTicket() {
        hideWaMsgContextMenu();
        if (!waMsgCtxData) return;
        var boardId = (currentBoard && currentBoard.source === "database") ? currentBoard.id : null;
        if (!boardId) {
            // Get first db board
            apiFetch("/api/kanban/boards").then(function(boards) {
                var dbBs = boards.filter(function(b) { return b.source === "database"; });
                if (!dbBs.length) { showSnackbar("No boards available"); return; }
                createTicketFromWaMsg(waMsgCtxData.message_id, dbBs[0].id);
            });
        } else {
            createTicketFromWaMsg(waMsgCtxData.message_id, boardId);
        }
    }

    function createTicketFromWaMsg(msgId, boardId) {
        apiFetch("/api/kanban/tickets/from-whatsapp/" + msgId, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ board_id: boardId })
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Created ticket from WhatsApp message");
                reloadCurrentDatabaseBoard();
            }
        }).catch(function(err) {
            showSnackbar("Failed to create ticket: " + err.message);
        });
    }

    function waMsgCtxMarkProcessed() {
        hideWaMsgContextMenu();
        if (!waMsgCtxData) return;
        // Mark a message as processed directly in the DB
        apiFetch("/api/kanban/whatsapp/messages/" + waMsgCtxData.message_id + "/processed", {
            method: "POST"
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Marked as processed");
                // Refresh the message panel if open
                if (waSelectedJid) {
                    var phone = waSelectedJid.split("@")[0].split(":")[0];
                    var name = waCtxMenuData ? waCtxMenuData.name : phone;
                    openWhatsAppMessagePanel(waSelectedJid, phone, name);
                }
            }
        }).catch(function(err) {
            showSnackbar("Failed to mark processed: " + err.message);
        });
    }

    // ── Board modal WhatsApp tab ──

    function loadBoardWaLinks(boardId) {
        var linksEl = document.getElementById("kb-bm-wa-links");
        linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">Loading...</div>';
        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links").then(function(links) {
            if (!links.length) {
                linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">No WhatsApp numbers linked</div>';
                return;
            }
            var html = "";
            links.forEach(function(l) {
                html += '<div class="flex items-center justify-between px-3 py-2 bg-[#152054] rounded border border-white/10">';
                html += '<div class="flex items-center gap-2">';
                html += '<div class="w-6 h-6 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-[10px] font-bold">' + esc((l.contact_name || l.phone_number || "?").charAt(0).toUpperCase()) + '</div>';
                html += '<div>';
                html += '<div class="text-sm text-white">' + esc(l.contact_name || l.phone_number) + '</div>';
                html += '<div class="text-[10px] text-gray-500">' + esc(l.phone_number) + '</div>';
                html += '</div></div>';
                html += '<div class="flex items-center gap-2">';
                html += '<label class="flex items-center gap-1 cursor-pointer select-none" title="Auto-snapshot new messages as tickets">';
                html += '<input type="checkbox" class="accent-[#25D366] w-3 h-3 kb-wa-link-auto-toggle" data-link-id="' + l.id + '" data-board-id="' + boardId + '"' + (l.auto_snapshot ? ' checked' : '') + '>'; 
                html += '<span class="text-[10px] text-gray-500">Auto</span>';
                html += '</label>';
                html += '<button type="button" class="kb-wa-link-remove text-red-400 hover:text-red-300 text-xs px-1" data-link-id="' + l.id + '" data-board-id="' + boardId + '" title="Unlink">✕</button>';
                html += '</div></div>';
            });
            linksEl.innerHTML = html;

            // Auto-toggle handlers
            linksEl.querySelectorAll(".kb-wa-link-auto-toggle").forEach(function(el) {
                el.addEventListener("change", function() {
                    var lid = el.dataset.linkId;
                    var bid = el.dataset.boardId;
                    apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, {
                        method: "PATCH",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ auto_snapshot: el.checked })
                    });
                });
            });

            // Remove handlers
            linksEl.querySelectorAll(".kb-wa-link-remove").forEach(function(el) {
                el.addEventListener("click", function() {
                    var lid = el.dataset.linkId;
                    var bid = el.dataset.boardId;
                    apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, { method: "DELETE" }).then(function() {
                        loadBoardWaLinks(bid);
                    });
                });
            });
        });
    }

    function addWaLinkFromBoardModal() {
        var phone = document.getElementById("kb-bm-wa-add-phone").value.trim();
        var name = document.getElementById("kb-bm-wa-add-name").value.trim();
        if (!phone) { showSnackbar("Enter a phone number"); return; }
        var boardId = editingBoardId;
        if (!boardId) { showSnackbar("Save the board first"); return; }
        // Build JID from phone number
        var jid = phone + "@s.whatsapp.net";
        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ phone_jid: jid, phone_number: phone, contact_name: name })
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Linked " + (name || phone) + " to board");
                document.getElementById("kb-bm-wa-add-phone").value = "";
                document.getElementById("kb-bm-wa-add-name").value = "";
                loadBoardWaLinks(boardId);
            }
        });
    }

    // Initialize WhatsApp section
    function initWhatsApp() {
        document.getElementById("kb-wa-refresh").addEventListener("click", function() { loadWhatsAppChats(true); });
        document.getElementById("kb-wa-search").addEventListener("input", renderWhatsAppChatList);

        // Chat context menu
        document.querySelector(".kb-wa-ctx-view").addEventListener("click", waCtxViewMessages);
        document.querySelector(".kb-wa-ctx-link").addEventListener("click", waCtxLinkToBoard);
        document.querySelector(".kb-wa-ctx-snapshot").addEventListener("click", waCtxSnapshotToBoard);

        // Close context menus on outside click
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-wa-ctx-menu")) hideWaChatContextMenu();
            if (!e.target.closest("#kb-wa-msg-ctx-menu")) hideWaMsgContextMenu();
        });

        // Message panel close
        document.getElementById("kb-wa-msg-close").addEventListener("click", function() {
            document.getElementById("kb-wa-msg-panel").classList.add("hidden");
        });

        // Link modal
        document.getElementById("kb-wa-link-cancel").addEventListener("click", function() {
            document.getElementById("kb-wa-link-modal").classList.add("hidden");
        });
        document.getElementById("kb-wa-link-confirm").addEventListener("click", confirmWaLink);

        // Message context menu
        document.querySelector(".kb-wa-msgctx-ticket").addEventListener("click", waMsgCtxCreateTicket);
        document.querySelector(".kb-wa-msgctx-mark-processed").addEventListener("click", waMsgCtxMarkProcessed);

        // Board modal: WhatsApp tab — add link button
        document.getElementById("kb-bm-wa-add-btn").addEventListener("click", addWaLinkFromBoardModal);

        // Load chats
        loadWhatsAppChats();
    }

    // ═══════════════════════════════════════════════════════════════════
    // End WhatsApp Integration
    // ═══════════════════════════════════════════════════════════════════

        // Context menu
        document.querySelector(".kb-ctx-activate").addEventListener("click", ctxActivateBoard);
        document.querySelector(".kb-ctx-edit").addEventListener("click", ctxEditBoard);
        document.querySelector(".kb-ctx-rename").addEventListener("click", ctxRenameBoard);
        document.querySelector(".kb-ctx-archive").addEventListener("click", ctxArchiveBoard);
        document.querySelector(".kb-ctx-delete").addEventListener("click", ctxDeleteBoard);
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-board-ctx-menu")) hideBoardContextMenu();
        });

        // Load initial data
        loadBoards();
        initWhatsApp();
    }

    // Auto-refresh provider dropdowns when third-party keys are saved (same page)
    window.addEventListener('thirdparty-providers-changed', function() {
        if (typeof loadAgentProviders === 'function') loadAgentProviders();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
