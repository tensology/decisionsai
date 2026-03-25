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

    function loadBoards() {
        apiFetch("/api/kanban/boards").then(function(boards) {
            dbBoards = boards.filter(function(b) { return b.source === "database"; });
            renderSidebarBoards(boards);

            // Auto-select last board or first in list if nothing selected
            if (!currentBoard && dbBoards.length) {
                var last = null;
                try { last = JSON.parse(localStorage.getItem("kb_last_selected")); } catch (e) {}
                if (last && last.source === "database" && dbBoards.some(function(b) { return b.id === last.id; })) {
                    selectBoard(last.source, last.id);
                } else {
                    selectBoard("database", dbBoards[0].id);
                }
            }
        }).catch(function() { showSnackbar("Failed to load boards", "error"); });

        apiFetch("/api/kanban/external-boards").then(function(data) {
            renderExternalBoards("kb-trello-boards", data.trello || [], "trello");
            renderExternalBoards("kb-jira-boards", data.jira || [], "jira");
        }).catch(function() {});
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
            div.innerHTML = '<span class="kb-src-icon kb-src-db"></span><span class="flex-1 truncate">' + esc(b.name) + '</span>';
            div.onclick = function() { selectBoard("database", b.id); };
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
        var board = dbBoards.find(function(b) { return b.id === ctxMenuBoardId; });
        var newName = prompt("Rename board:", board ? board.name : "");
        if (!newName || !newName.trim()) { hideBoardContextMenu(); return; }
        apiFetch("/api/kanban/boards/" + ctxMenuBoardId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName.trim() })
        }).then(function() {
            showSnackbar("Board renamed");
            loadBoards();
            if (currentBoard && currentBoard.id === ctxMenuBoardId) selectBoard("database", ctxMenuBoardId);
        }).catch(function(e) { showSnackbar("Rename failed: " + e.message, "error"); });
        hideBoardContextMenu();
    }

    function ctxEditBoard() {
        if (!ctxMenuBoardId) return;
        hideBoardContextMenu();
        openBoardModal(ctxMenuBoardId);
    }

    function ctxDeleteBoard() {
        if (!ctxMenuBoardId) return;
        var board = dbBoards.find(function(b) { return b.id === ctxMenuBoardId; });
        var name = board ? board.name : "this board";
        if (!confirm('Delete board "' + name + '" and all its tickets? This cannot be undone.')) { hideBoardContextMenu(); return; }
        apiFetch("/api/kanban/boards/" + ctxMenuBoardId, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            if (currentBoard && currentBoard.id === ctxMenuBoardId) {
                currentBoard = null; currentBoardData = null;
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
            }
            loadBoards();
        }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
        hideBoardContextMenu();
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
        loadBoards();
    }

    function renderBoard(data, isLocal) {
        document.getElementById("kb-board-title").textContent = data.name || "Board";
        var badge = document.getElementById("kb-board-source-badge");
        var source = currentBoard.source;
        badge.textContent = source.charAt(0).toUpperCase() + source.slice(1);
        badge.className = "text-xs px-2 py-0.5 rounded text-white " + (source === "database" ? "bg-[#f97316]" : source === "trello" ? "bg-[#0079bf]" : "bg-[#0052cc]");

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
            linked_snippet_id: parseInt(document.getElementById("kb-modal-link-snippet").value) || null,
            linked_action_id: parseInt(document.getElementById("kb-modal-link-action").value) || null,
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
            populateSelect("kb-modal-link-snippet", data.snippets, "id", "title", ticket.linked_snippet_id);
            populateSelect("kb-modal-link-action", data.actions, "id", "title", ticket.linked_action_id);
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

    // ── Board modal (tabbed: Details + Agent + Advanced) ──

    var agentFreq = "daily";
    var agentDays = [];
    var _agentProviders = [];  // cached provider list

    function loadAgentProviders() {
        return apiFetch("/api/llms/available-providers").then(function(data) {
            _agentProviders = data.providers || [{ id: "ollama", name: "Ollama" }];
            ["kb-agent-orch-provider", "kb-agent-coder-provider", "kb-agent-sub-provider"].forEach(function(selId) {
                var sel = document.getElementById(selId);
                var cur = sel.value;
                sel.innerHTML = '<option value="">(chat default)</option>';
                _agentProviders.forEach(function(p) {
                    var opt = document.createElement("option");
                    opt.value = p.id; opt.textContent = p.name;
                    sel.appendChild(opt);
                });
                if (cur) sel.value = cur;
            });
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
        editingBoardId = boardId || null;
        document.getElementById("kb-board-modal-title").textContent = boardId ? "Edit Board" : "New Board";
        document.getElementById("kb-board-modal-save").textContent = boardId ? "Save" : "Create";
        // Reset to Details tab
        switchBoardModalTab("details");

        loadAgentProviders().then(function() {
            if (boardId && currentBoardData && currentBoardData.id === boardId) {
                populateBoardModal(currentBoardData);
            } else if (boardId) {
                apiFetch("/api/kanban/boards/" + boardId).then(function(data) {
                    populateBoardModal(data);
                }).catch(function() {});
            } else {
                document.getElementById("kb-board-modal-name").value = "";
                document.getElementById("kb-board-modal-desc").value = "";
                document.getElementById("kb-agent-enabled").checked = false;
                setAgentFreq("daily");
                agentDays = [];
                renderAgentDays();
                document.getElementById("kb-agent-time").value = "09:00";
                document.getElementById("kb-agent-monthly-day").value = "1";
                document.getElementById("kb-agent-orch-provider").value = "";
                document.getElementById("kb-agent-orch-model").innerHTML = '<option value="">(chat default)</option>';
                document.getElementById("kb-agent-coder-provider").value = "";
                document.getElementById("kb-agent-coder-model").innerHTML = '<option value="">(chat default)</option>';
                document.getElementById("kb-agent-sub-provider").value = "";
                document.getElementById("kb-agent-sub-model").innerHTML = '<option value="">(chat default)</option>';
                populateAgentLaneSelects([], "", "");
                loadBoardDefaults(null);
            }
        });
        document.getElementById("kb-board-modal").classList.remove("hidden");
        if (typeof injectInfoIcons === 'function') injectInfoIcons();
    }

    function populateBoardModal(data) {
        document.getElementById("kb-board-modal-name").value = data.name || "";
        document.getElementById("kb-board-modal-desc").value = data.description || "";
        document.getElementById("kb-agent-enabled").checked = !!data.agent_enabled;
        setAgentFreq(data.agent_frequency || "daily");
        agentDays = data.agent_days || [];
        renderAgentDays();
        document.getElementById("kb-agent-time").value = data.agent_time || "09:00";
        document.getElementById("kb-agent-monthly-day").value = String(data.agent_monthly_day || 1);

        // Set provider selects then load models with saved values
        var orchProv = data.agent_orchestrator_provider || "";
        var coderProv = data.agent_coder_provider || "";
        var subProv = data.agent_sub_provider || "";
        document.getElementById("kb-agent-orch-provider").value = orchProv;
        document.getElementById("kb-agent-coder-provider").value = coderProv;
        document.getElementById("kb-agent-sub-provider").value = subProv;
        loadAgentModels("kb-agent-orch", orchProv, data.agent_orchestrator_model || "");
        loadAgentModels("kb-agent-coder", coderProv, data.agent_coder_model || "");
        loadAgentModels("kb-agent-sub", subProv, data.agent_sub_model || "");

        var laneNames = (data.lanes || []).map(function(l) { return l.name; });
        populateAgentLaneSelects(laneNames, data.agent_source_lane || "", data.agent_done_lane || "");
        loadBoardDefaults(data);
    }

    function populateAgentLaneSelects(laneNames, sourceLane, doneLane) {
        ["kb-agent-source-lane", "kb-agent-done-lane"].forEach(function(selId) {
            var sel = document.getElementById(selId);
            var val = selId.indexOf("source") >= 0 ? sourceLane : doneLane;
            sel.innerHTML = '<option value="">Select lane</option>';
            laneNames.forEach(function(name) {
                var opt = document.createElement("option");
                opt.value = name; opt.textContent = name;
                if (name === val) opt.selected = true;
                sel.appendChild(opt);
            });
        });
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

    function setAgentFreq(freq) {
        agentFreq = freq;
        document.querySelectorAll(".kb-agent-freq").forEach(function(btn) {
            var isActive = btn.dataset.freq === freq;
            btn.setAttribute("data-active", isActive ? "1" : "0");
        });
        document.getElementById("kb-agent-days-wrap").classList.toggle("hidden", freq !== "weekly");
        document.getElementById("kb-agent-monthly-wrap").classList.toggle("hidden", freq !== "monthly");
    }

    function renderAgentDays() {
        document.querySelectorAll(".kb-agent-day").forEach(function(btn) {
            var day = parseInt(btn.dataset.day);
            var sel = agentDays.indexOf(day) >= 0;
            btn.setAttribute("data-selected", sel ? "1" : "0");
        });
    }

    function toggleAgentDay(day) {
        var idx = agentDays.indexOf(day);
        if (idx >= 0) agentDays.splice(idx, 1);
        else agentDays.push(day);
        renderAgentDays();
    }

    function closeBoardModal() {
        document.getElementById("kb-board-modal").classList.add("hidden");
        editingBoardId = null;
    }

    function saveBoardModal() {
        var name = document.getElementById("kb-board-modal-name").value.trim();
        if (!name) { showSnackbar("Board name is required", "error"); return; }
        var payload = {
            name: name,
            description: document.getElementById("kb-board-modal-desc").value.trim(),
            agent_enabled: document.getElementById("kb-agent-enabled").checked,
            agent_frequency: agentFreq,
            agent_time: document.getElementById("kb-agent-time").value || "09:00",
            agent_days: agentDays,
            agent_monthly_day: parseInt(document.getElementById("kb-agent-monthly-day").value) || 1,
            agent_orchestrator_provider: document.getElementById("kb-agent-orch-provider").value,
            agent_orchestrator_model: document.getElementById("kb-agent-orch-model").value,
            agent_coder_provider: document.getElementById("kb-agent-coder-provider").value,
            agent_coder_model: document.getElementById("kb-agent-coder-model").value,
            agent_sub_provider: document.getElementById("kb-agent-sub-provider").value,
            agent_sub_model: document.getElementById("kb-agent-sub-model").value,
            agent_source_lane: document.getElementById("kb-agent-source-lane").value,
            agent_done_lane: document.getElementById("kb-agent-done-lane").value,
            default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value) || 0,
            default_project_id: parseInt(document.getElementById("kb-board-def-project").value) || 0,

        };
        if (editingBoardId) {
            apiFetch("/api/kanban/boards/" + editingBoardId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function() {
                showSnackbar("Board updated"); closeBoardModal(); loadBoards();
                if (currentBoard && currentBoard.id === editingBoardId) selectBoard("database", editingBoardId);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
        } else {
            apiFetch("/api/kanban/boards", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: payload.name, description: payload.description })
            }).then(function(data) {
                // After create, save agent settings
                payload.name = undefined; payload.description = undefined;
                return apiFetch("/api/kanban/boards/" + data.id, {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                }).then(function() { return data; });
            }).then(function(data) {
                showSnackbar("Board created"); closeBoardModal(); loadBoards();
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
            loadBoards();
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

    // ── Event bindings ──

    function init() {
        // Board sidebar
        document.getElementById("kb-add-board").addEventListener("click", function() { openBoardModal(null); });
        document.getElementById("kb-create-big").addEventListener("click", function() { openBoardModal(null); });
        document.getElementById("kb-search").addEventListener("input", function() { loadBoards(); });

        // Board actions
        document.getElementById("kb-add-ticket").addEventListener("click", addTicket);
        document.getElementById("kb-edit-board").addEventListener("click", function() {
            if (currentBoard && currentBoard.source === "database") openBoardModal(currentBoard.id);
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
            if (e.target === this) closeBoardModal();
        });

        // Agent frequency buttons
        document.querySelectorAll(".kb-agent-freq").forEach(function(btn) {
            btn.addEventListener("click", function() { setAgentFreq(btn.dataset.freq); });
        });

        // Agent day buttons
        document.querySelectorAll(".kb-agent-day").forEach(function(btn) {
            btn.addEventListener("click", function() { toggleAgentDay(parseInt(btn.dataset.day)); });
        });

        // Agent provider change → reload models
        ["kb-agent-orch", "kb-agent-coder", "kb-agent-sub"].forEach(function(prefix) {
            document.getElementById(prefix + "-provider").addEventListener("change", function() {
                loadAgentModels(prefix, this.value, "");
            });
        });

        // Ticket modal
        document.getElementById("kb-modal-close").addEventListener("click", closeTicketModal);
        document.getElementById("kb-modal-save").addEventListener("click", saveTicket);
        document.getElementById("kb-modal-delete").addEventListener("click", deleteTicket);
        document.getElementById("kb-modal-send-project").addEventListener("click", sendTicketToProject);
        document.getElementById("kb-ticket-modal").addEventListener("click", function(e) {
            if (e.target === this) closeTicketModal();
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
            if (e.target === this) closeCopyModal();
        });

        // Context menu
        document.querySelector(".kb-ctx-edit").addEventListener("click", ctxEditBoard);
        document.querySelector(".kb-ctx-rename").addEventListener("click", ctxRenameBoard);
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
