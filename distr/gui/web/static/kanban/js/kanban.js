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
            div.innerHTML = '<span class="kb-src-icon" style="background:' + esc(b.color || '#f97316') + '"></span><span class="flex-1 truncate">' + esc(b.name) + '</span>' + (b.agent_enabled ? '<svg class="kb-agent-indicator" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0" title="Agent check-in enabled"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="11"/></svg>' : '');
            div.onclick = function() { selectBoard("database", b.id); };
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
                    if (ticketId) moveTicket(parseInt(ticketId), lane.id, body);
                });
            }
            (lane.tickets || []).forEach(function(ticket) { body.appendChild(createTicketCard(ticket, isLocal)); });
            col.appendChild(body);
            container.appendChild(col);
        });
    }

    function createTicketCard(ticket, isLocal) {
        var card = document.createElement("div");
        card.className = "kb-card bg-[#1a1f3a] rounded-lg border border-white/20 p-3 cursor-pointer hover:border-[#f97316]/50 transition-colors relative";
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
            if (isLocal) { openTicketModal(ticket.id); }
            else if (ticket.url) { window.open(ticket.url, "_blank"); }
        });
        return card;
    }

    // ── Drag & drop move ──

    function moveTicket(ticketId, laneId, bodyEl) {
        var position = bodyEl.querySelectorAll(".kb-card").length;
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
            // Show "Send to project" button if ticket or board has a linked project
            var sendBtn = document.getElementById("kb-modal-send-project");
            var hasProject = !!t.linked_project_id || (currentBoardData && !!currentBoardData.default_project_id);
            sendBtn.classList.toggle("hidden", !hasProject);
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
        var payload = {
            title: document.getElementById("kb-modal-ticket-title").value.trim(),
            description: document.getElementById("kb-modal-ticket-desc").value.trim(),
            priority: getSelectedPriority(),
            linked_workflow_id: parseInt(document.getElementById("kb-modal-link-workflow").value) || null,
            linked_project_id: parseInt(document.getElementById("kb-modal-link-project").value) || null,
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
        if (!confirm("Delete this ticket?")) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId, { method: "DELETE" }).then(function() {
            showSnackbar("Ticket deleted"); closeTicketModal();
            selectBoard("database", currentBoard.id);
        }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
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
            populateSelect("kb-modal-link-project", data.projects, "id", "name", ticket.linked_project_id);
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
            populateSelect("kb-board-def-workflow", ld.step_runner_workflows || ld.workflows, "id", "title", data ? data.default_workflow_id : null);
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
        document.getElementById("kb-modal-send-project").addEventListener("click", sendTicketToProject);
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

        // Copy modal
        document.getElementById("kb-copy-cancel").addEventListener("click", closeCopyModal);
        document.getElementById("kb-copy-confirm").addEventListener("click", confirmCopy);
        document.getElementById("kb-copy-modal").addEventListener("click", function(e) {
        });

        // Context menu
        document.querySelector(".kb-ctx-edit").addEventListener("click", ctxEditBoard);
        document.querySelector(".kb-ctx-rename").addEventListener("click", ctxRenameBoard);
        document.querySelector(".kb-ctx-archive").addEventListener("click", ctxArchiveBoard);
        document.querySelector(".kb-ctx-delete").addEventListener("click", ctxDeleteBoard);
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-board-ctx-menu")) hideBoardContextMenu();
        });

        // Load initial data
        loadBoards();
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
