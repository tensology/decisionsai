(function() {
    "use strict";

    var currentBoard = null;       // { id, source, ... }
    var currentBoardData = null;   // full board data with lanes/tickets
    var currentBoardHasProject = false; // whether current board has a linked project
    var dbBoards = [];
    var editingBoardId = null;     // null = create, number = edit
    var modalTicketId = null;
    var copyTicketData = null;     // { title, description } for copy modal
    var ctxMenuBoardId = null;     // board id for context menu
    var waChats = [];
    var waSelectedJid = null;
    var waCtxMenuData = null;      // { jid, phone, name }
    var waMsgCtxData = null;       // { message_id (db id) }
    var waConnected = false;
    var activeSourceTab = "local";

    // ── Helpers ──

    function esc(s) { var d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

    /** Strip HTML tags from a string, decode entities, and trim. */
    function stripHtml(html) {
        if (!html) return "";
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        return (tmp.textContent || tmp.innerText || "").replace(/\s+/g, " ").trim();
    }

    /** Truncate text to maxLen characters, adding ellipsis if truncated. */
    function truncate(s, maxLen) {
        if (!s) return "";
        s = s.replace(/\s+/g, " ").trim();
        return s.length > maxLen ? s.substring(0, maxLen) + "…" : s;
    }

    var apiFetch = window.DecisionsAPI.fetch;
    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "kb-snackbar" }); }

    /** In-app confirm modal (matches Ticket Board styling). opts: { title, message, confirmLabel, danger, onConfirm } */
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

    function updateSourceTabsVisibility() {
        var trelloTab = document.getElementById("kb-src-trello");
        var jiraTab = document.getElementById("kb-src-jira");
        var tabsRow = document.getElementById("kb-src-tabs-row");
        // Show tabs row only if at least one external source is available
        var hasExternal = !trelloTab.classList.contains("hidden") || !jiraTab.classList.contains("hidden");
        tabsRow.classList.toggle("hidden", !hasExternal);
    }

    function switchSourceTab(src) {
        activeSourceTab = src;
        document.querySelectorAll(".kb-src-tab").forEach(function(btn) {
            btn.classList.toggle("active", btn.dataset.src === src);
        });
        document.getElementById("kb-local-boards-container").classList.toggle("hidden", src !== "local");
        document.getElementById("kb-trello-boards-container").classList.toggle("hidden", src !== "trello");
        document.getElementById("kb-jira-boards-container").classList.toggle("hidden", src !== "jira");
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
        // Show/hide the source tab based on whether there are boards
        var tabId = source === "trello" ? "kb-src-trello" : "kb-src-jira";
        var tabBtn = document.getElementById(tabId);
        if (boards.length > 0) {
            tabBtn.classList.remove("hidden");
        } else {
            tabBtn.classList.add("hidden");
            // If we were on this tab and it became empty, switch to local
            if (activeSourceTab === source) switchSourceTab("local");
        }
        updateSourceTabsVisibility();
        if (!boards.length) {
            container.innerHTML = '<p class="text-xs text-gray-500 italic">No ' + source + ' account connected</p>';
            return;
        }
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var filtered = search ? boards.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; }) : boards;
        container.innerHTML = filtered.length ? "" : '<p class="text-xs text-gray-500 italic">No matching boards</p>';
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
                document.getElementById("kb-loading").classList.add("hidden");
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
                document.getElementById("kb-loading").classList.add("hidden");
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
        // Auto-switch source tab when selecting a board
        var srcTab = source === "database" ? "local" : source;
        switchSourceTab(srcTab);
        try { localStorage.setItem("kb_last_selected", JSON.stringify({ source: source, id: id })); } catch (e) {}
        // Show loading spinner
        document.getElementById("kb-empty").classList.add("hidden");
        document.getElementById("kb-board-view").classList.add("hidden");
        document.getElementById("kb-loading").classList.remove("hidden");
        // Close any open WhatsApp thread view
        document.getElementById("kb-wa-thread-view").classList.add("hidden");
        waSelectedJid = null;

        if (source === "database") {
            apiFetch("/api/kanban/boards/" + id).then(function(data) {
                currentBoardData = data;
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-board-view").classList.remove("hidden");
                renderBoard(data, true);
            }).catch(function(e) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                showSnackbar("Failed to load board: " + e.message, "error");
            });
        } else {
            apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(id)).then(function(data) {
                currentBoardData = data;
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-board-view").classList.remove("hidden");
                renderBoard(data, false);
            }).catch(function(e) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                showSnackbar("Failed to load external board: " + e.message, "error");
            });
        }
        loadBoards(); // uses cache, just re-renders sidebar active state
    }

    function renderBoard(data, isLocal) {
        // Apply board accent color as CSS variable
        var boardColor = data.color || (isLocal ? null : (currentBoard.source === "trello" ? "#0079bf" : "#0052cc")) || "#f97316";
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
        // Show edit/config button for all boards ("Configure" for external)
        var editBtn = document.getElementById("kb-edit-board");
        if (!isLocal) {
            editBtn.style.display = "";
            editBtn.textContent = "Configure";
            editBtn.title = "Configure this external board (link project, workflow, etc.)";
        } else {
            editBtn.style.display = "";
            editBtn.textContent = "Edit";
            editBtn.title = "";
        }
        document.getElementById("kb-delete-board").style.display = isLocal ? "" : "none";

        var extLink = document.getElementById("kb-board-ext-link");
        if (!isLocal && (data.url || currentBoard.extUrl)) {
            extLink.classList.remove("hidden");
            extLink.href = data.url || currentBoard.extUrl;
        } else {
            extLink.classList.add("hidden");
        }

        // Store board-level data for conditional actions
        currentBoardData = data;
        currentBoardHasProject = !!(data.default_project_id || (currentBoard.source === "database" && data.id));
        if (!isLocal && data.local_id) {
            currentBoard._localId = data.local_id;
        }

        renderLanes(data.lanes || [], isLocal, data);
    }

    function renderLanes(lanes, isLocal, boardData) {
        var container = document.getElementById("kb-lanes");
        container.innerHTML = "";
        var boardData = currentBoardData || {};
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
            (lane.tickets || []).forEach(function(ticket) { body.appendChild(createTicketCard(ticket, isLocal, boardData)); });
            col.appendChild(body);
            container.appendChild(col);
        });
    }

    /** Push a local ticket to CLI (with confirmation and spinners). */
    function pushTicketToCli(ticketId, btnEl) {
        if (!confirm("Push ticket #" + ticketId + " to the project CLI?")) return;
        if (btnEl) {
            btnEl.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
            btnEl.classList.add("text-orange-400");
            btnEl.disabled = true;
        }
        apiFetch("/api/kanban/tickets/" + ticketId + "/send-to-cli", { method: "POST" })
            .then(function(r) {
                showSnackbar(r.message || "Sent to CLI");
                _pollCliStatus(ticketId, btnEl);
            })
            .catch(function(err) {
                showSnackbar("CLI error: " + err.message, "error");
                if (btnEl) {
                    btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                    btnEl.classList.remove("text-orange-400");
                    btnEl.disabled = false;
                }
            });
    }

    /** Send a local ticket to project by ID. */
    function sendTicketToProjectById(ticketId) {
        apiFetch("/api/kanban/tickets/" + ticketId + "/send-to-project", { method: "POST" })
            .then(function(r) { showSnackbar(r.message || "Sent to project"); })
            .catch(function(err) { showSnackbar("Error: " + err.message, "error"); });
    }

    /** Copy an external ticket to the local board, then optionally send to CLI or project. */
    function copyAndPushExternalTicket(ticket, source, action) {
        if (!dbBoards.length) { showSnackbar("No local boards available", "error"); return; }
        // Find the best target board
        var targetBoard = (currentBoardData && currentBoardData.local_id) ? null : null;
        // Prefer a board linked to the same project as the external board config
        var preferredBoard = null;
        for (var i = 0; i < dbBoards.length; i++) {
            if (currentBoardData && currentBoardData.default_project_id && dbBoards[i].default_project_id === currentBoardData.default_project_id) {
                preferredBoard = dbBoards[i]; break;
            }
        }
        if (!preferredBoard && currentBoardData && currentBoardData.local_id) {
            for (var j = 0; j < dbBoards.length; j++) {
                if (dbBoards[j].id === currentBoardData.local_id) { preferredBoard = dbBoards[j]; break; }
            }
        }
        if (!preferredBoard) preferredBoard = dbBoards[0];

        var payload = {
            board_id: preferredBoard.id,
            title: ticket.title,
            description: stripHtml(ticket.description || ""),
            priority: ticket.priority || "medium",
            external_source: source,
            external_id: String(ticket.id),
            external_url: ticket.url || "",
            auto_send_to_project: action === 'project',
            auto_send_to_cli: action === 'cli',
        };

        apiFetch("/api/kanban/tickets/copy-external-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function(r) {
            if (action === 'cli' && r.id) {
                showSnackbar("Ticket copied — pushing to CLI…");
                pushTicketToCli(r.id, null);
            } else if (action === 'project') {
                showSnackbar(r.sent_to_project ? "Ticket copied & sent to project: " + (r.project_name || "") : "Ticket copied to board");
            } else {
                showSnackbar("Ticket copied to board");
            }
            // Refresh the target board if it's currently displayed
            if (currentBoard && currentBoard.source === "database" && currentBoard.id === preferredBoard.id) {
                selectBoard("database", preferredBoard.id);
            }
        }).catch(function(err) {
            showSnackbar("Error: " + err.message, "error");
        });
    }

    /** Open a detail modal for an external (Trello/Jira) ticket. */
    function openExternalTicketModal(ticket, source) {
        // Populate the modal with external ticket data
        document.getElementById("kb-modal-ticket-title").value = ticket.title || "";
        // For external tickets, show the description as plain text (stripped of HTML)
        var descArea = document.getElementById("kb-modal-ticket-desc");
        descArea.value = stripHtml(ticket.description || "");
        descArea.readOnly = true;
        descArea.classList.add("bg-[#152054]/50", "cursor-not-allowed");

        // Hide priority editing for external
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.classList.add("opacity-50", "cursor-not-allowed");
            btn.disabled = true;
        });
        setPriorityButtons(ticket.priority || "medium");

        // Clear links/files/todos
        renderModalLinks([]);
        renderModalFiles([]);
        // Show todos from external ticket if available
        renderExternalTodos(ticket.todos || []);

        // Show external metadata
        var metaContainer = document.getElementById("kb-modal-external-meta");
        if (metaContainer) {
            var metaHtml = '';
            if (ticket.url) {
                metaHtml += '<div class="flex items-center gap-2"><span class="text-xs text-gray-500">Source:</span><a href="' + esc(ticket.url) + '" target="_blank" class="text-xs text-[#f97316] hover:underline">Open in ' + esc(source.charAt(0).toUpperCase() + source.slice(1)) + '</a></div>';
            }
            if (ticket.members && ticket.members.length) {
                metaHtml += '<div class="text-xs text-gray-400">Members: ' + ticket.members.map(esc).join(', ') + '</div>';
            }
            if (ticket.labels && ticket.labels.length) {
                metaHtml += '<div class="flex flex-wrap gap-1">' + ticket.labels.map(function(lb) { return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + esc(lb) + '</span>'; }).join('') + '</div>';
            }
            if (ticket.time_estimate || ticket.time_spent) {
                metaHtml += '<div class="text-xs text-gray-400">';
                if (ticket.time_estimate) metaHtml += 'Estimate: ' + esc(ticket.time_estimate);
                if (ticket.time_spent) metaHtml += ' | Spent: ' + esc(ticket.time_spent);
                metaHtml += '</div>';
            }
            if (ticket.reporter) {
                metaHtml += '<div class="text-xs text-gray-400">Reporter: ' + esc(ticket.reporter) + '</div>';
            }
            metaContainer.innerHTML = metaHtml;
            metaContainer.classList.remove("hidden");
        }

        // Show transfer/copy button, hide save/delete
        document.getElementById("kb-modal-transfer-ext").classList.remove("hidden");
        document.getElementById("kb-modal-save").classList.add("hidden");
        document.getElementById("kb-modal-delete").classList.add("hidden");
        // Change title to show source
        document.getElementById("kb-modal-title").textContent = ticket.title || "Ticket";

        // Store external ticket data for actions
        window._extTicketData = ticket;
        window._extTicketSource = source;

        // Show modal
        switchTicketTab("details");
        document.getElementById("kb-ticket-modal").classList.remove("hidden");
    }

    /** Render todos from external sources (read-only). */
    function renderExternalTodos(todos) {
        var container = document.getElementById("kb-modal-todos");
        container.innerHTML = "";
        if (!todos.length) {
            container.innerHTML = '<p class="text-xs text-gray-500 italic">No tasks</p>';
            return;
        }
        todos.forEach(function(todo) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<input type="checkbox" ' + (todo.done ? "checked" : "") + ' class="accent-[#f97316]" disabled>' +
                '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + esc(todo.text) + '</span>';
            container.appendChild(row);
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

    function createTicketCard(ticket, isLocal, boardData) {
        boardData = boardData || currentBoardData || {};
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
        var pri = (ticket.priority || "medium").toLowerCase();
        var priClass = "kb-pri-" + pri;
        // Strip HTML from description for display
        var cleanDesc = stripHtml(ticket.description || "");
        var truncatedDesc = truncate(cleanDesc, 120);
        var todoCount = (ticket.todos || []).length;
        var todoDone = (ticket.todos || []).filter(function(t) { return t.done; }).length;
        var todoHtml = todoCount ? '<span class="text-xs text-gray-500 ml-2">✓ ' + todoDone + '/' + todoCount + '</span>' : '';
        // Labels for external tickets
        var labelsHtml = '';
        if (ticket.labels && ticket.labels.length) {
            labelsHtml = '<div class="flex flex-wrap gap-1 mt-1">' +
                ticket.labels.map(function(lb) { return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + esc(lb) + '</span>'; }).join('') +
                '</div>';
        }
        // Members for external tickets
        var membersHtml = '';
        if (ticket.members && ticket.members.length) {
            membersHtml = '<div class="flex items-center gap-1 mt-1">' +
                ticket.members.map(function(m) { return '<span class="text-[10px] text-gray-400">' + esc(m) + '</span>'; }).join(', ') +
                '</div>';
        }
        // Time tracking for Jira
        var timeHtml = '';
        if (ticket.time_estimate || ticket.time_spent) {
            timeHtml = '<div class="text-[10px] text-gray-500 mt-1">';
            if (ticket.time_estimate) timeHtml += '⏱ ' + esc(ticket.time_estimate);
            if (ticket.time_spent) timeHtml += ' / ' + esc(ticket.time_spent) + ' done';
            timeHtml += '</div>';
        }
        // Determine if project-linked actions should show
        var hasProject = !!(boardData.default_project_id || (isLocal && boardData.id));
        // Source badge for external tickets
        var sourceBadge = '';
        if (!isLocal && currentBoard.source) {
            var srcColor = currentBoard.source === 'trello' ? '#0079bf' : '#0052cc';
            sourceBadge = '<span class="text-[9px] px-1 py-0.5 rounded text-white" style="background:' + srcColor + '">' + esc(currentBoard.source) + '</span>';
        }
        // External URL link icon
        var extLinkHtml = '';
        if (!isLocal && ticket.url) {
            extLinkHtml = '<a href="' + esc(ticket.url) + '" target="_blank" class="text-gray-500 hover:text-blue-400 transition-colors" title="Open in ' + esc(currentBoard.source || 'browser') + '" onclick="event.stopPropagation()">' +
                '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>';
        }
        card.innerHTML = '<div class="flex items-start justify-between gap-2">' +
            '<span class="text-sm text-white leading-snug flex-1">' + esc(ticket.title) + '</span>' +
            '<div class="flex items-center gap-1.5 flex-shrink-0">' + sourceBadge + '<span class="' + priClass + ' text-[10px] px-1.5 py-0.5 rounded text-white font-medium">' + esc(pri) + '</span>' + extLinkHtml + '</div>' +
            '</div>' +
            (truncatedDesc ? '<p class="text-xs text-gray-500 mt-1 line-clamp-2">' + esc(truncatedDesc) + '</p>' : '') +
            labelsHtml + membersHtml + timeHtml +
            '<div class="flex items-center justify-center gap-2 mt-2 kb-card-actions">' +
                '<button class="kb-act-copy text-gray-500 hover:text-white transition-colors" title="Copy title & description"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>' +
                (hasProject ? '<button class="kb-act-cli text-gray-500 hover:text-orange-400 transition-colors" title="Push to CLI"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>' : '<button class="kb-act-cli text-gray-700 cursor-not-allowed" title="Push to CLI (link a project to this board first)" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>') +
                (hasProject ? '<button class="kb-act-project text-gray-500 hover:text-blue-400 transition-colors" title="Send to Project (.tickets)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></button>' : '<button class="kb-act-project text-gray-700 cursor-not-allowed" title="Send to Project (.tickets) — link a project to this board first" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/<polyline points="14 2 14 8 20 8"/></svg></button>') +
                (!isLocal ? '<button class="kb-act-transfer text-gray-500 hover:text-green-400 transition-colors" title="Copy to local board"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 16H3v-5"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg></button>' : '') +
                (isLocal ? '<button class="kb-act-delete text-gray-500 hover:text-red-400 transition-colors" title="Delete ticket"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>' : '<button class="kb-act-delete text-gray-700 cursor-not-allowed" title="Delete not available for external tickets" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>') +
            '</div>' +
            '<div class="flex items-center mt-1">' + todoHtml + '</div>';
        // Click handler: open modal for ALL tickets (local and external)
        card.addEventListener("click", function(e) {
            if (e.target.closest(".kb-card-actions") || e.target.closest(".kb-act-transfer") || e.target.closest("a")) return;
            if (isLocal) {
                openTicketModal(ticket.id);
            } else {
                openExternalTicketModal(ticket, currentBoard.source);
            }
        });
        // Wire up action buttons
        // Copy title + description
        var copyBtn = card.querySelector(".kb-act-copy");
        if (copyBtn) copyBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            var text = stripHtml(ticket.title) + (cleanDesc ? "\n\n" + cleanDesc : "");
            navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
        });
        // Push to CLI
        var cliBtn = card.querySelector(".kb-act-cli");
        if (cliBtn && !cliBtn.disabled) cliBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            if (isLocal) {
                pushTicketToCli(ticket.id, cliBtn);
            } else {
                // External ticket: copy to local board first, then push
                copyAndPushExternalTicket(ticket, currentBoard.source, 'cli');
            }
        });
        // Send to Project
        var projectBtn = card.querySelector(".kb-act-project");
        if (projectBtn && !projectBtn.disabled) projectBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            if (isLocal) {
                sendTicketToProjectById(ticket.id);
            } else {
                // External ticket: copy to local board first, then send
                copyAndPushExternalTicket(ticket, currentBoard.source, 'project');
            }
        });
        // Transfer/Copy to local board (external only)
        var transferBtn = card.querySelector(".kb-act-transfer");
        if (transferBtn) transferBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            openCopyModal(ticket);
        });
        // Delete (local only)
        var delBtn = card.querySelector(".kb-act-delete");
        if (delBtn && isLocal) delBtn.addEventListener("click", function(e) {
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
        // Reset modal to local-ticket mode
        resetTicketModalForLocal();
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
            // Hide external metadata section for local tickets
            var extMeta = document.getElementById("kb-modal-external-meta");
            if (extMeta) extMeta.classList.add("hidden");
            document.getElementById("kb-ticket-modal").classList.remove("hidden");
        }).catch(function(e) { showSnackbar("Failed to load ticket: " + e.message, "error"); });
    }

    function closeTicketModal() {
        document.getElementById("kb-ticket-modal").classList.add("hidden");
        modalTicketId = null;
        window._extTicketData = null;
        window._extTicketSource = null;
        resetTicketModalForLocal();
    }

    /** Reset modal UI back to local-ticket (editable) mode. */
    function resetTicketModalForLocal() {
        var descArea = document.getElementById("kb-modal-ticket-desc");
        descArea.readOnly = false;
        descArea.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.classList.remove("opacity-50", "cursor-not-allowed");
            btn.disabled = false;
        });
        document.getElementById("kb-modal-save").classList.remove("hidden");
        document.getElementById("kb-modal-delete").classList.remove("hidden");
        var transferBtn = document.getElementById("kb-modal-transfer-ext");
        if (transferBtn) transferBtn.classList.add("hidden");
        // Clear todos input
        var todoInput = document.getElementById("kb-modal-todo-input");
        if (todoInput) todoInput.readOnly = false;
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

    /** Open config modal for an external (Trello/Jira) board.
     *  This creates/updates a local KanbanBoard record to store project, workflow, color, agent config.
     */
    function openExternalBoardConfigModal(provider, extBoardId) {
        // Pre-populate from currentBoardData (returned by the API with local config if any)
        var data = currentBoardData || {};
        document.getElementById("kb-board-modal-title").textContent = "Configure " + (provider === "trello" ? "Trello" : "Jira") + " Board";
        document.getElementById("kb-board-modal-save").textContent = "Save";
        editingBoardId = data.local_id || null;

        // Set name from current board
        document.getElementById("kb-board-modal-name").value = data.name || "";
        document.getElementById("kb-board-modal-name").readOnly = false;
        document.getElementById("kb-board-modal-desc").value = "";
        document.getElementById("kb-board-modal-agent-enabled").checked = !!data.agent_enabled;

        var colorInput = document.getElementById("kb-board-modal-color");
        var colorHex = document.getElementById("kb-board-modal-color-hex");
        var c = data.color || (provider === "trello" ? "#0079bf" : "#0052cc");
        colorInput.value = c;
        colorHex.textContent = c;

        // Load board defaults — will populate workflow/project dropdowns
        loadBoardDefaults({
            default_workflow_id: data.default_workflow_id,
            default_project_id: data.default_project_id,
        });

        // Store external board info for saving
        window._extBoardConfig = { provider: provider, extBoardId: extBoardId };

        switchBoardModalTab("details");
        document.getElementById("kb-board-modal").classList.remove("hidden");
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
        window._extBoardConfig = null;
    }

    function saveBoardModal() {
        // Handle external board config saving
        if (window._extBoardConfig) {
            var extCfg = window._extBoardConfig;
            var payload = {
                name: document.getElementById("kb-board-modal-name").value.trim(),
                default_project_id: parseInt(document.getElementById("kb-board-def-project").value) || 0,
                default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value) || 0,
                color: document.getElementById("kb-board-modal-color").value || "",
                agent_enabled: document.getElementById("kb-board-modal-agent-enabled").checked,
            };
            apiFetch("/api/kanban/external-boards/" + extCfg.provider + "/" + encodeURIComponent(extCfg.extBoardId) + "/register", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function(r) {
                showSnackbar("External board configured");
                window._extBoardConfig = null;
                closeBoardModal();
                // Refresh the current board view
                if (currentBoard) selectBoard(currentBoard.source, currentBoard.id, currentBoard.extUrl);
                loadBoards(true);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
            return;
        }
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
            document.getElementById("kb-loading").classList.add("hidden");
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
        copyTicketData = {
            title: ticket.title || "",
            description: ticket.description || "",
            priority: ticket.priority || "medium",
            external_source: ticket.external_source || (currentBoard.source !== "database" ? currentBoard.source : null),
            external_id: ticket.external_id || (currentBoard.source !== "database" ? String(ticket.id) : null),
            external_url: ticket.external_url || ticket.url || "",
        };
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
        apiFetch("/api/kanban/tickets/copy-external-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                board_id: boardId,
                title: copyTicketData.title,
                description: stripHtml(copyTicketData.description),
                priority: copyTicketData.priority || "medium",
                external_source: copyTicketData.external_source,
                external_id: copyTicketData.external_id,
                external_url: copyTicketData.external_url,
            })
        }).then(function() {
            showSnackbar("Ticket copied to board"); closeCopyModal();
            // Refresh the target board if currently displayed
            if (currentBoard && currentBoard.source === "database" && currentBoard.id === boardId) {
                selectBoard("database", boardId);
            }
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
        // Source tab switching
        document.querySelectorAll(".kb-src-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchSourceTab(btn.dataset.src); });
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
            } else if (currentBoard && (currentBoard.source === "trello" || currentBoard.source === "jira")) {
                // External board — open config modal
                openExternalBoardConfigModal(currentBoard.source, currentBoard.id);
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
        // Modal action buttons (copy, CLI, send-to-project, transfer)
        document.getElementById("kb-modal-act-copy").addEventListener("click", function() {
            if (modalTicketId) {
                // Local ticket
                apiFetch("/api/kanban/tickets/" + modalTicketId).then(function(t) {
                    var text = t.title + (t.description ? "\n\n" + t.description : "");
                    navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
                });
            } else if (window._extTicketData) {
                // External ticket
                var ext = window._extTicketData;
                var text = ext.title + (ext.description ? "\n\n" + stripHtml(ext.description) : "");
                navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
            }
        });
        document.getElementById("kb-modal-act-cli").addEventListener("click", function() {
            if (modalTicketId) {
                pushTicketToCli(modalTicketId, document.getElementById("kb-modal-act-cli"));
            } else if (window._extTicketData) {
                copyAndPushExternalTicket(window._extTicketData, window._extTicketSource, 'cli');
            }
        });
        document.getElementById("kb-modal-act-project").addEventListener("click", function() {
            if (modalTicketId) {
                apiFetch("/api/kanban/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
                    .then(function(r) { showSnackbar("Sent to project: " + (r.project_name || "")); })
                    .catch(function(e) { showSnackbar("Error: " + e.message, "error"); });
            } else if (window._extTicketData) {
                copyAndPushExternalTicket(window._extTicketData, window._extTicketSource, 'project');
            }
        });
        // Transfer/copy button (external tickets)
        var transferBtn = document.getElementById("kb-modal-transfer-ext");
        if (transferBtn) transferBtn.addEventListener("click", function() {
            if (window._extTicketData) {
                openCopyModal(window._extTicketData);
                closeTicketModal();
            }
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

    // ── Sidebar tab switching ──

    function updateTabBarVisibility() {
        var tabMessages = document.getElementById("kb-tab-messages");
        var tabBar = document.getElementById("kb-tab-bar");
        // Hide the entire tab bar when Messages tab is hidden (only one tab = no need for tabs)
        tabBar.classList.toggle("hidden", tabMessages.classList.contains("hidden"));
    }

    function switchSidebarTab(tab) {
        var ticketsPanel = document.getElementById("kb-panel-tickets");
        var messagesPanel = document.getElementById("kb-panel-messages");
        var tabTickets = document.getElementById("kb-tab-tickets");
        var tabMessages = document.getElementById("kb-tab-messages");
        if (tab === "messages") {
            ticketsPanel.classList.add("hidden");
            messagesPanel.classList.remove("hidden");
            tabTickets.classList.remove("active");
            tabMessages.classList.add("active");
        } else {
            ticketsPanel.classList.remove("hidden");
            messagesPanel.classList.add("hidden");
            tabTickets.classList.add("active");
            tabMessages.classList.remove("active");
            // Close thread view if open — restore board view
            closeWhatsAppThread();
        }
    }

    // ── Relay server base URL (for syncing messages when desktop app is offline) ──
    var waRelayBase = window.DecisionsAI && window.DecisionsAI.waRelayBase
        ? window.DecisionsAI.waRelayBase
        : "https://www.decisionsai.net/api/whatsapp";

    function loadWhatsAppChats(forceRefresh) {
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");

        el.textContent = "Loading...";
        // First try local desktop app, then fall back to relay server
        var localUrl = "/api/kanban/whatsapp/messages?limit=500";
        var relayUrl = waRelayBase + "/messages?limit=500";

        apiFetch(localUrl).then(function(data) {
            if (data.messages && data.messages.length > 0) {
                return data;
            }
            // Local DB empty — try relay server for stored messages
            return fetchFromRelay();
        }).catch(function() {
            // Local endpoint not available (desktop app offline) — try relay
            return fetchFromRelay();
        }).then(processWhatsAppMessages).catch(function(err) {
            el.textContent = "WhatsApp not connected";
            chatListEl.innerHTML = "";
            waConnected = false;
            document.getElementById("kb-tab-messages").classList.add("hidden");
            updateTabBarVisibility();
        });

        function fetchFromRelay() {
            return fetch(relayUrl, { mode: "cors" }).then(function(resp) {
                if (!resp.ok) throw new Error("Relay returned " + resp.status);
                return resp.json();
            });
        }
    }

    function syncFromRelay() {
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");
        el.textContent = "Syncing from server...";
        var relayUrl = waRelayBase + "/messages?limit=500&unprocessed_only=true";
        fetch(relayUrl, { mode: "cors" }).then(function(resp) {
            if (!resp.ok) throw new Error("Relay returned " + resp.status);
            return resp.json();
        }).then(function(data) {
            var newCount = (data.messages || []).length;
            if (newCount > 0) {
                showSnackbar("Synced " + newCount + " new message" + (newCount !== 1 ? "s" : "") + " from server", "success");
                // Mark them as processed on the relay so they won't appear in future syncs
                data.messages.forEach(function(msg) {
                    fetch(waRelayBase + "/messages/" + msg.id + "/processed", {
                        method: "POST", mode: "cors"
                    }).catch(function() {});
                });
            } else {
                showSnackbar("No new messages on server");
            }
            // Merge relay messages with existing local data and refresh
            loadWhatsAppChats(true);
        }).catch(function(err) {
            el.textContent = "Sync failed";
            showSnackbar("Sync failed: " + err.message, "error");
        });
    }

    function processWhatsAppMessages(data) {
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");
        var messages = data.messages || [];
        // Group messages by jid_phone (the conversation/chat key)
        var chatMap = {};
        messages.forEach(function(msg) {
            var chatPhone = msg.jid_phone || (msg.jid || "").split("@")[0];
            var chatName = msg.sender_push_name || msg.sender_phone || chatPhone || "Unknown";
            if (!chatMap[chatPhone]) {
                chatMap[chatPhone] = { sender: chatPhone, name: chatName, messages: [], lastTs: 0, unread: 0 };
            }
            chatMap[chatPhone].messages.push(msg);
            if (msg.whatsapp_timestamp && msg.whatsapp_timestamp > chatMap[chatPhone].lastTs) {
                chatMap[chatPhone].lastTs = msg.whatsapp_timestamp;
            }
            if (!msg.processed) chatMap[chatPhone].unread++;
            // Use the latest sender push name as the display name
            if (msg.sender_push_name) chatMap[chatPhone].name = msg.sender_push_name;
        });
        waChats = Object.values(chatMap);
        // Sort by most recent message first
        waChats.sort(function(a, b) { return b.lastTs - a.lastTs; });
        if (messages.length === 0) {
            el.textContent = waConnected ? "No captured messages yet" : "Not connected";
            chatListEl.innerHTML = "<div class='text-xs text-gray-500 italic py-2'>No captured messages yet</div>";
            // Don't hide the tab if WhatsApp is connected — just show empty state
            if (!waConnected) {
                document.getElementById("kb-tab-messages").classList.add("hidden");
                updateTabBarVisibility();
            }
            return;
        }
        // Show Messages tab when WhatsApp has captured messages
        waConnected = true;
        document.getElementById("kb-tab-messages").classList.remove("hidden");
        updateTabBarVisibility();
        el.textContent = waChats.length + " contacts with messages";
        renderWhatsAppChatList();
    }

    function renderWhatsAppChatList() {
        var chatListEl = document.getElementById("kb-wa-chats");
        var searchVal = (document.getElementById("kb-wa-search").value || "").toLowerCase();
        var filtered = waChats;
        if (searchVal) {
            filtered = waChats.filter(function(c) {
                return (c.name || "").toLowerCase().includes(searchVal) || (c.sender || "").includes(searchVal);
            });
        }
        if (!filtered.length) {
            chatListEl.innerHTML = '<div class="text-xs text-gray-500 italic py-2">No incoming messages</div>';
            return;
        }
        var html = "";
        filtered.forEach(function(chat) {
            var sender = chat.sender || "";
            var name = esc(chat.name || sender);
            var unread = chat.unread || 0;
            var lastMsg = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
            var preview = "";
            if (lastMsg) {
                preview = lastMsg.text ? lastMsg.text.substring(0, 60) : (lastMsg.media_type ? "📎 " + lastMsg.media_type : "");
                if (lastMsg.caption && !lastMsg.text) preview = (lastMsg.caption || "").substring(0, 60);
            }
            var active = waSelectedJid === chat.sender ? " bg-[#25D366]/10 border-l-2 border-[#25D366]" : "";
            var lastTs = chat.lastTs;
            var timeStr = "";
            if (lastTs) {
                var d = new Date(lastTs * 1000);
                timeStr = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
            }
            html += '<div class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs hover:bg-white/5' + active + '" data-wa-sender="' + esc(sender) + '" data-wa-name="' + esc(name) + '">';
            html += '<div class="w-8 h-8 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-xs font-bold flex-shrink-0">';
            html += esc(name.charAt(0).toUpperCase());
            html += '</div>';
            html += '<div class="flex-1 min-w-0">';
            html += '<div class="flex items-center justify-between">';
            html += '<span class="text-white truncate font-medium">' + name + '</span>';
            html += '<span class="text-gray-500 text-[10px] ml-1 flex-shrink-0">' + timeStr + '</span>';
            html += '</div>';
            html += '<div class="text-gray-500 truncate">' + esc(preview) + (unread ? ' <span class="text-[#25D366] font-bold">(' + unread + ')</span>' : '') + '</div>';
            html += '</div></div>';
        });
        chatListEl.innerHTML = html;

        // Click handlers
        chatListEl.querySelectorAll("[data-wa-sender]").forEach(function(el) {
            el.addEventListener("click", function() {
                waSelectedJid = el.dataset.waSender;
                renderWhatsAppChatList();
                showWhatsAppThread(el.dataset.waSender, el.dataset.waName);
            });
            el.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                var phone = el.dataset.waSender;
                waCtxMenuData = { jid: phone + "@s.whatsapp.net", phone: phone, name: el.dataset.waName };
                showWaChatContextMenu(e.clientX, e.clientY);
            });
        });
    }

    function showWhatsAppThread(sender, name) {
        // Show the message thread view in the right panel (replacing kanban board)
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        var msgView = document.getElementById("kb-wa-thread-view");

        boardView.classList.add("hidden");
        emptyView.classList.add("hidden");
        msgView.classList.remove("hidden");

        var titleEl = document.getElementById("kb-wa-thread-title");
        var countEl = document.getElementById("kb-wa-thread-count");
        var msgList = document.getElementById("kb-wa-thread-messages");
        var avatarEl = document.getElementById("kb-wa-thread-avatar");

        titleEl.textContent = name;
        avatarEl.textContent = name.charAt(0).toUpperCase();
        countEl.textContent = "Loading...";
        msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">Loading messages...</div>';

        apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").then(function(data) {
            if (data.messages && data.messages.length > 0) return data;
            // Fallback to relay server
            return fetch(waRelayBase + "/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200", { mode: "cors" }).then(function(r) { return r.json(); });
        }).catch(function() {
            // Local endpoint failed — try relay
            return fetch(waRelayBase + "/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200", { mode: "cors" }).then(function(r) { return r.json(); });
        }).then(function(data) {
            var messages = data.messages || [];
            countEl.textContent = messages.length + " messages";
            if (!messages.length) {
                msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">No messages from this number yet</div>';
                return;
            }
            var html = "";
            // Date separator helper
            var lastDateStr = "";
            messages.forEach(function(msg) {
                // Date separator
                var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                var dateStr = msgDate ? msgDate.toLocaleDateString([], {year: "numeric", month: "short", day: "numeric"}) : "";
                if (dateStr && dateStr !== lastDateStr) {
                    html += '<div class="flex items-center justify-center my-3"><span class="text-[10px] text-gray-500 bg-[#152054] px-3 py-1 rounded-full">' + esc(dateStr) + '</span></div>';
                    lastDateStr = dateStr;
                }

                var isMine = msg.from_me;
                var align = isMine ? "justify-end" : "justify-start";
                var bg = isMine ? "bg-[#005c4b]" : "bg-[#1f2c34]";
                var timeStr = msgDate ? msgDate.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";

                html += '<div class="flex ' + align + '" data-wa-msg-id="' + msg.id + '">';
                html += '<div class="wa-msg-bubble ' + bg + ' px-3 py-2" style="max-width:75%;border-radius:8px;">';

                // Media preview
                if (msg.media_type && msg.media_local_path) {
                    var mediaPath = "/api/kanban/whatsapp/media?path=" + encodeURIComponent(msg.media_local_path);
                    if (msg.media_type === "photo" || msg.media_type === "image") {
                        html += '<div class="mb-1 rounded overflow-hidden cursor-pointer" onclick="window.open(\'' + esc(mediaPath) + '\', \'_blank\')"><img src="' + esc(mediaPath) + '" class="max-w-full max-h-[350px] rounded" loading="lazy"></div>';
                    } else if (msg.media_type === "voice" || msg.media_type === "audio") {
                        html += '<div class="mb-1"><audio controls class="max-w-full" style="height:36px;"><source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "audio/ogg") + '"></audio></div>';
                    } else if (msg.media_type === "video") {
                        html += '<div class="mb-1 rounded overflow-hidden"><video controls class="max-w-full max-h-[350px]"><source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "video/mp4") + '"></video></div>';
                    } else {
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
                    var mediaIcon = { photo: "🖼️", voice: "🎙️", audio: "🎵", video: "🎬", document: "📄", sticker: "🏷️" };
                    html += '<div class="mb-1 px-2 py-1 bg-black/20 rounded text-xs text-gray-400">' + (mediaIcon[msg.media_type] || "📎") + ' ' + esc(msg.media_type) + (msg.media_filename ? ": " + esc(msg.media_filename) : "") + '</div>';
                }

                // Caption
                if (msg.caption) {
                    html += '<div class="text-sm text-white whitespace-pre-wrap">' + esc(msg.caption) + '</div>';
                }

                // Text
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

            // Scroll to bottom (newest)
            msgList.scrollTop = msgList.scrollHeight;

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

    function closeWhatsAppThread() {
        var msgView = document.getElementById("kb-wa-thread-view");
        msgView.classList.add("hidden");
        // Restore the previous board view or empty state
        document.getElementById("kb-loading").classList.add("hidden");
        if (currentBoard) {
            document.getElementById("kb-board-view").classList.remove("hidden");
        } else {
            document.getElementById("kb-empty").classList.remove("hidden");
        }
        waSelectedJid = null;
        renderWhatsAppChatList();
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
        waSelectedJid = waCtxMenuData.phone;
        renderWhatsAppChatList();
        showWhatsAppThread(waCtxMenuData.phone, waCtxMenuData.name);
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
                    showWhatsAppThread(waSelectedJid, name);
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
        // Sidebar tab switching
        document.getElementById("kb-tab-tickets").addEventListener("click", function() { switchSidebarTab("tickets"); });
        document.getElementById("kb-tab-messages").addEventListener("click", function() { switchSidebarTab("messages"); });

        // WhatsApp thread back button
        document.getElementById("kb-wa-thread-back").addEventListener("click", closeWhatsAppThread);

        // WhatsApp thread snapshot button
        document.getElementById("kb-wa-thread-snapshot").addEventListener("click", function() {
            if (!waSelectedJid) return;
            var phone = waSelectedJid;
            waCtxMenuData = { jid: phone + "@s.whatsapp.net", phone: phone, name: document.getElementById("kb-wa-thread-title").textContent };
            waCtxSnapshotToBoard();
        });

        document.getElementById("kb-wa-refresh").addEventListener("click", function() { syncFromRelay(); });
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

        document.getElementById("kb-wa-link-cancel").addEventListener("click", function() {
            document.getElementById("kb-wa-link-modal").classList.add("hidden");
        });
        document.getElementById("kb-wa-link-confirm").addEventListener("click", confirmWaLink);

        // Message context menu
        document.querySelector(".kb-wa-msgctx-ticket").addEventListener("click", waMsgCtxCreateTicket);
        document.querySelector(".kb-wa-msgctx-mark-processed").addEventListener("click", waMsgCtxMarkProcessed);

        // Board modal: WhatsApp tab — add link button
        document.getElementById("kb-bm-wa-add-btn").addEventListener("click", addWaLinkFromBoardModal);

        // Check WhatsApp connection status first — show Messages tab if connected
        apiFetch("/api/settings/advanced/whatsapp/status").then(function(statusData) {
            if (statusData.status === "connected") {
                waConnected = true;
                document.getElementById("kb-tab-messages").classList.remove("hidden");
                updateTabBarVisibility();
            }
        }).catch(function() {});

        // Load chats
        loadWhatsAppChats();

        // ── SSE: real-time WhatsApp message updates ──
        var waSSE = null;
        function connectWaSSE() {
            if (waSSE) return;  // already connected
            try {
                waSSE = new EventSource("/api/kanban/whatsapp/stream");
                waSSE.addEventListener("whatsapp_message", function(e) {
                    try {
                        var msg = JSON.parse(e.data);
                        // Flash a snackbar notification
                        var who = msg.sender_push_name || msg.sender_phone || msg.jid_phone || "Unknown";
                        var preview = msg.text ? msg.text.substring(0, 50) : (msg.media_type ? "📎 " + msg.media_type : "");
                        showSnackbar("WhatsApp: " + who + " — " + preview, "success");
                        // Refresh the chat list to show the new message
                        loadWhatsAppChats(true);
                        // If a thread is open for this sender, refresh it too
                        if (waSelectedJid && (waSelectedJid === msg.jid_phone || waSelectedJid === msg.sender_phone)) {
                            showWhatsAppThread(waSelectedJid, document.getElementById("kb-wa-thread-title").textContent);
                        }
                    } catch(err) {}
                });
                waSSE.onerror = function() {
                    // Reconnect after disconnect
                    waSSE.close();
                    waSSE = null;
                    setTimeout(connectWaSSE, 5000);
                };
            } catch(err) {}
        }
        connectWaSSE();
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
        // Initially hide the tab bar since Messages is hidden by default
        updateTabBarVisibility();
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
