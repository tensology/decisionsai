/**
 * Projects page: matches desktop Projects UI (distr/gui/projects.py).
 * Left: search + project list + Add. Right: empty state or detail form with tabs (Details, Settings, Board).
 */
(function() {
    var currentProjectId = null;
    var searchText = "";

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function setError(el, msg) {
        el.innerHTML = "<p class=\"text-sm text-amber-400\">" + msg + "</p>";
    }

    var apiFetch = window.DecisionsAPI.fetch;
    function showSnackbar(msg, type, opts) { window.DecisionsAPI.snackbar(msg, type, Object.assign({ id: "projects-snackbar" }, opts || {})); }

    function setEmpty(el) {
        el.innerHTML = "<p class=\"text-sm text-gray-400\">No projects yet. Create one with Add Project.</p>";
    }

    function filterProjects(data) {
        if (!searchText.trim()) return data;
        var q = searchText.trim().toLowerCase();
        return data.filter(function(p) {
            return (p.name || "").toLowerCase().indexOf(q) !== -1 || (p.description || "").toLowerCase().indexOf(q) !== -1;
        });
    }

    var contextMenuProjectId = null;

    function renderList(data) {
        var el = document.getElementById("projects-list");
        if (!el) return;
        if (!Array.isArray(data)) data = [];
        var filtered = filterProjects(data);
        if (!filtered.length) {
            el.innerHTML = "<p class=\"text-sm text-gray-400\">No projects match.</p>";
            return;
        }
        var deleteSvg = "<svg class=\"w-4 h-4 flex-shrink-0\" fill=\"none\" stroke=\"currentColor\" viewBox=\"0 0 24 24\"><path stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16\"/></svg>";
        el.innerHTML = filtered.map(function(p) {
            var inUse = p.in_use ? " <span class=\"text-xs text-[#f97316]\">(in use)</span>" : "";
            var active = currentProjectId === p.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            return "<div class=\"project-item-wrapper flex items-center gap-1 rounded border" + active + " group\" data-id=\"" + p.id + "\">" +
                "<button type=\"button\" class=\"project-item flex-1 min-w-0 text-left px-3 py-2 text-white text-sm\" data-id=\"" + p.id + "\">" + escapeAttr(p.name || "Untitled") + inUse + "</button>" +
                "<button type=\"button\" class=\"project-item-delete p-1.5 rounded text-gray-400 hover:text-red-400 hover:bg-red-500/20 flex-shrink-0\" data-id=\"" + p.id + "\" aria-label=\"Delete\">" + deleteSvg + "</button>" +
                "</div>";
        }).join("");
        el.querySelectorAll(".project-item").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                selectProject(parseInt(btn.getAttribute("data-id"), 10));
            });
        });
        el.querySelectorAll(".project-item-delete").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                e.preventDefault();
                deleteProjectFromList(parseInt(btn.getAttribute("data-id"), 10));
            });
        });
        el.querySelectorAll(".project-item-wrapper").forEach(function(wrap) {
            wrap.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                showProjectContextMenu(parseInt(wrap.getAttribute("data-id"), 10), e.clientX, e.clientY);
            });
        });
    }

    function showProjectContextMenu(projectId, x, y) {
        var menu = document.getElementById("project-context-menu");
        if (!menu) return;
        contextMenuProjectId = projectId;
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
    }

    function hideProjectContextMenu() {
        var menu = document.getElementById("project-context-menu");
        if (menu) menu.classList.add("hidden");
        contextMenuProjectId = null;
    }

    function deleteProjectFromList(id) {
        var name = (projectsData.filter(function(p) { return p.id === id; })[0] || {}).name || "this project";
        if (!confirm("Remove project \"" + name + "\"? This cannot be undone and will remove board data for this project.")) return;
        fetch("/api/projects/" + id, { method: "DELETE" })
            .then(function(r) {
                if (r.ok) {
                    if (currentProjectId === id) {
                        currentProjectId = null;
                        showEmpty();
                    }
                    loadProjects();
                    showSnackbar("Project removed", "success");
                } else {
                    return r.json().then(function(e) { throw new Error(e.detail || "Failed to remove"); });
                }
            })
            .catch(function(e) { showSnackbar(e.message || "Failed to remove project", "error"); });
    }

    function loadProjects() {
        var el = document.getElementById("projects-list");
        if (!el) return;
        fetch("/api/projects")
            .then(function(r) {
                if (!r.ok) {
                    return r.json().then(function(err) {
                        setError(el, "Could not load projects: " + (err && err.detail ? err.detail : "HTTP " + r.status));
                    }).catch(function() {
                        setError(el, "Could not load projects (HTTP " + r.status + "). Check server logs.");
                    }).then(function() { throw new Error(r.status); });
                }
                return r.json();
            })
            .then(function(data) {
                if (!el) return;
                if (!Array.isArray(data)) data = [];
                projectsData = data;
                renderList(data);
                var inUse = (data || []).filter(function(p) { return p.in_use; })[0];
                if (inUse) selectProject(inUse.id);
            })
            .catch(function() {
                if (el && el.innerHTML.indexOf("Loading") !== -1) setEmpty(el);
            });
    }

    var projectsData = [];

    function showEmpty() {
        document.getElementById("projects-empty").classList.remove("hidden");
        document.getElementById("projects-detail").classList.add("hidden");
        currentProjectId = null;
        renderList(projectsData);
    }

    function showDetail(project) {
        document.getElementById("projects-empty").classList.add("hidden");
        document.getElementById("projects-detail").classList.remove("hidden");
        currentProjectId = project.id;
        document.getElementById("project-detail-title").textContent = project.name || "Project";
        document.getElementById("detail-name").value = project.name || "";
        document.getElementById("detail-description").value = project.description || "";
        document.getElementById("detail-folder").value = project.folder_location || "";
        var words = [];
        try { words = JSON.parse(project.additional_trigger_words || "[]"); } catch (e) {}
        renderTriggerBadges(words || []);
        var triggersInput = document.getElementById("detail-triggers-input");
        if (triggersInput) triggersInput.value = "";
        document.getElementById("detail-provider").value = project.provider || "";
        document.getElementById("detail-startup").value = project.startup_instructions || "";
        loadBoardProvidersAndSelect(project.provider || "", project.board_id || "", project.board_name || "");

        var tbody = document.getElementById("detail-items-body");
        var rows = [];
        (project.context_items || []).forEach(function(c) {
            var preview = (c.content || "").length > 80 ? (c.content || "").substring(0, 80) + "..." : (c.content || "");
            rows.push("<tr class=\"border-t border-white/10\"><td class=\"px-3 py-2\">Context</td><td class=\"px-3 py-2\">" + escapeAttr(c.title) + "</td><td class=\"px-3 py-2 text-gray-500\">" + escapeAttr(preview) + "</td><td class=\"px-3 py-2\">—</td><td class=\"px-3 py-2 flex gap-1\"><button type=\"button\" class=\"context-edit p-1.5 rounded border border-white/20 text-gray-300 hover:bg-white/10 inline-flex\" data-id=\"" + c.id + "\" aria-label=\"Edit\"><svg class=\"w-4 h-4\" fill=\"none\" stroke=\"currentColor\" viewBox=\"0 0 24 24\"><path stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z\"/></svg></button><button type=\"button\" class=\"context-remove p-1.5 rounded border border-red-500/50 text-red-400 hover:bg-red-500/20 inline-flex\" data-id=\"" + c.id + "\" aria-label=\"Remove\"><svg class=\"w-4 h-4\" fill=\"none\" stroke=\"currentColor\" viewBox=\"0 0 24 24\"><path stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16\"/></svg></button></td></tr>");
        });
        (project.files || []).forEach(function(f) {
            rows.push("<tr class=\"border-t border-white/10\"><td class=\"px-3 py-2\">File</td><td class=\"px-3 py-2\">" + escapeAttr(f.filename) + "</td><td class=\"px-3 py-2 text-gray-500\">" + escapeAttr(f.description || "") + "</td><td class=\"px-3 py-2\"><button type=\"button\" class=\"file-open-finder px-2 py-1 text-xs rounded border border-white/20 text-gray-300 hover:bg-white/10\" data-id=\"" + f.id + "\">Open in Finder</button></td><td class=\"px-3 py-2\"><button type=\"button\" class=\"file-remove px-2 py-1 text-xs rounded border border-red-500/50 text-red-400 hover:bg-red-500/20\" data-id=\"" + f.id + "\">Remove</button></td></tr>");
        });
        tbody.innerHTML = rows.length ? rows.join("") : "<tr><td colspan=\"5\" class=\"px-3 py-4 text-gray-500 text-center\">No context items or files. Add a context item or upload a file.</td></tr>";

        tbody.querySelectorAll(".context-edit").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var id = parseInt(btn.getAttribute("data-id"), 10);
                var item = (project.context_items || []).filter(function(c) { return c.id === id; })[0];
                if (item) openContextItemModal(item);
            });
        });
        tbody.querySelectorAll(".context-remove").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var id = parseInt(btn.getAttribute("data-id"), 10);
                removeContextItem(id);
            });
        });
        tbody.querySelectorAll(".file-remove").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var id = parseInt(btn.getAttribute("data-id"), 10);
                removeProjectFile(id);
            });
        });
        tbody.querySelectorAll(".file-open-finder").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var id = parseInt(btn.getAttribute("data-id"), 10);
                openProjectFileFolder(id);
            });
        });

        document.getElementById("project-use").textContent = project.in_use ? "In use" : "Use";
        document.getElementById("project-use").disabled = !!project.in_use;
        renderList(projectsData);
    }

    function selectProject(id) {
        var prevProjectId = currentProjectId;
        currentProjectId = id;
        return fetch("/api/projects/" + id)
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(project) {
                showDetail(project);
                // If the terminal tab is active and project changed, reconnect
                if (prevProjectId !== id) {
                    var tabEl = document.getElementById("tab-terminal");
                    if (tabEl && !tabEl.classList.contains("hidden")) {
                        initTerminal();
                    }
                }
            })
            .catch(function() {
                alert("Could not load project.");
            });
    }

    function switchTab(tabName) {
        document.querySelectorAll(".project-tab").forEach(function(t) {
            t.classList.toggle("text-white", t.getAttribute("data-tab") === tabName);
            t.classList.toggle("border-[#f97316]", t.getAttribute("data-tab") === tabName);
            t.classList.toggle("text-gray-400", t.getAttribute("data-tab") !== tabName);
            t.classList.toggle("border-transparent", t.getAttribute("data-tab") !== tabName);
        });
        document.querySelectorAll(".project-tab-pane").forEach(function(p) {
            p.classList.add("hidden");
        });
        var pane = document.getElementById("tab-" + tabName);
        if (pane) pane.classList.remove("hidden");
        // Terminal tab lives outside projects-tab-content and needs
        // the full flex space; hide the content div when terminal is active
        var tabContent = document.getElementById("projects-tab-content");
        if (tabName === "terminal") {
            tabContent.classList.add("hidden");
        } else {
            tabContent.classList.remove("hidden");
        }
        if (tabName === "board") {
            var ps = document.getElementById("detail-provider");
            var bs = document.getElementById("detail-board");
            var bid = bs && bs.value ? bs.value : null;
            var bname = bs && bs.options[bs.selectedIndex] ? bs.options[bs.selectedIndex].textContent.trim() : "";
            loadBoardProvidersAndSelect(ps ? ps.value : "", bid, bname);
            loadKanbanBoardStatus();
        }
        if (tabName === "terminal") {
            initTerminal();
        } else {
            destroyTerminal();
        }
    }

    function loadBoardProvidersAndSelect(providerValue, boardId, boardName) {
        var providerSel = document.getElementById("detail-provider");
        var boardSel = document.getElementById("detail-board");
        if (!providerSel || !boardSel) return;
        providerValue = (providerValue || "").toString().trim();
        boardId = (boardId || "").toString().trim();
        boardName = (boardName || "").toString().trim();
        fetch("/api/projects/board-providers")
            .then(function(r) { return r.ok ? r.json() : { providers: [] }; })
            .catch(function() { return { providers: [] }; })
            .then(function(data) {
                var opts = [{ id: "", name: "None" }].concat(data.providers || []);
                providerSel.innerHTML = opts.map(function(p) { return "<option value=\"" + escapeAttr(String(p.id)) + "\">" + escapeAttr(p.name) + "</option>"; }).join("");
                providerSel.value = opts.some(function(p) { return String(p.id) === providerValue; }) ? providerValue : "";
                return providerSel.value ? fetch("/api/projects/boards?provider=" + encodeURIComponent(providerSel.value)).then(function(r) { return r.ok ? r.json() : { boards: [] }; }).catch(function() { return { boards: [] }; }) : { boards: [] };
            })
            .then(function(data) {
                var boards = (data && data.boards) ? data.boards : [];
                var boardOpts = [{ id: "", name: "None" }].concat(boards);
                boardSel.innerHTML = boardOpts.map(function(b) { return "<option value=\"" + escapeAttr(String(b.id)) + "\">" + escapeAttr(b.name || "") + "</option>"; }).join("");
                var wantId = boardId;
                var found = wantId && boardOpts.some(function(b) { return String(b.id) === String(wantId); });
                if (found) {
                    selectBoardOption(boardSel, wantId);
                } else if (wantId && boardName) {
                    boardSel.innerHTML = "<option value=\"\">None</option><option value=\"" + escapeAttr(wantId) + "\">" + escapeAttr(boardName) + "</option>";
                    selectBoardOption(boardSel, wantId);
                } else {
                    boardSel.value = "";
                }
                boardSel.disabled = false;
                updateKanbanVisibility();
            });
    }

    function selectBoardOption(boardSel, boardId) {
        if (!boardSel || boardId == null || boardId === "") return;
        var want = String(boardId);
        for (var i = 0; i < boardSel.options.length; i++) {
            if (String(boardSel.options[i].value) === want) {
                boardSel.selectedIndex = i;
                return;
            }
        }
        boardSel.value = want;
    }

    function updateKanbanVisibility() {
        var providerSel = document.getElementById("detail-provider");
        var boardSel = document.getElementById("detail-board");
        var kanbanSection = document.getElementById("kanban-board-section");
        if (!kanbanSection) return;
        var hasExternalBoard = providerSel && providerSel.value && boardSel && boardSel.value;
        kanbanSection.classList.toggle("hidden", !!hasExternalBoard);
    }

    function onBoardProviderChange() {
        var providerSel = document.getElementById("detail-provider");
        var boardSel = document.getElementById("detail-board");
        if (!providerSel || !boardSel) return;
        var p = (providerSel.value || "").trim();
        if (!p) {
            boardSel.innerHTML = "<option value=\"\">None</option>";
            boardSel.value = "";
            boardSel.disabled = false;
            updateKanbanVisibility();
            return;
        }
        boardSel.disabled = true;
        boardSel.innerHTML = "<option value=\"\">Loading boards…</option>";
        boardSel.value = "";
        fetch("/api/projects/boards?provider=" + encodeURIComponent(p))
            .then(function(r) { return r.ok ? r.json() : { boards: [] }; })
            .catch(function() { return { boards: [] }; })
            .then(function(data) {
                var boards = data.boards || [];
                boardSel.innerHTML = "<option value=\"\">None</option>" + boards.map(function(b) { return "<option value=\"" + escapeAttr(String(b.id)) + "\">" + escapeAttr(b.name || "") + "</option>"; }).join("");
                boardSel.value = "";
                boardSel.disabled = false;
                updateKanbanVisibility();
            });
    }

    function renderTriggerBadges(words) {
        var wrap = document.getElementById("detail-triggers-wrap");
        if (!wrap) return;
        wrap.innerHTML = "";
        (words || []).forEach(function(word) {
            var w = String(word).trim();
            if (!w) return;
            var badge = document.createElement("span");
            badge.className = "trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm";
            badge.setAttribute("data-word", w);
            badge.innerHTML = "<span class=\"trigger-word\">" + escapeAttr(w) + "</span> <button type=\"button\" class=\"trigger-remove ml-0.5 text-gray-400 hover:text-white focus:outline-none\" aria-label=\"Remove\">&times;</button>";
            var removeBtn = badge.querySelector(".trigger-remove");
            if (removeBtn) removeBtn.addEventListener("click", function() { badge.remove(); });
            wrap.appendChild(badge);
        });
    }

    function getTriggerWordsArray() {
        var wrap = document.getElementById("detail-triggers-wrap");
        if (!wrap) return [];
        var seen = {};
        var words = [];
        wrap.querySelectorAll(".trigger-badge[data-word]").forEach(function(b) {
            var w = (b.getAttribute("data-word") || "").trim();
            if (w && !seen[w]) { seen[w] = true; words.push(w); }
        });
        var input = document.getElementById("detail-triggers-input");
        if (input && (input.value || "").trim()) {
            (input.value || "").split(",").forEach(function(s) {
                var w = s.trim();
                if (w && !seen[w]) { seen[w] = true; words.push(w); }
            });
        }
        return words;
    }

    function addTriggerWordsFromInput() {
        var input = document.getElementById("detail-triggers-input");
        if (!input) return;
        var raw = (input.value || "").trim();
        if (!raw) return;
        var toAdd = raw.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        var wrap = document.getElementById("detail-triggers-wrap");
        if (!wrap) return;
        var existing = new Set();
        wrap.querySelectorAll(".trigger-badge[data-word]").forEach(function(b) { existing.add((b.getAttribute("data-word") || "").trim()); });
        toAdd.forEach(function(w) {
            if (existing.has(w)) return;
            existing.add(w);
            var badge = document.createElement("span");
            badge.className = "trigger-badge inline-flex items-center gap-1 px-2 py-1 rounded bg-white/10 border border-white/20 text-gray-200 text-sm";
            badge.setAttribute("data-word", w);
            badge.innerHTML = "<span class=\"trigger-word\">" + escapeAttr(w) + "</span> <button type=\"button\" class=\"trigger-remove ml-0.5 text-gray-400 hover:text-white focus:outline-none\" aria-label=\"Remove\">&times;</button>";
            var removeBtn = badge.querySelector(".trigger-remove");
            if (removeBtn) removeBtn.addEventListener("click", function() { badge.remove(); });
            wrap.appendChild(badge);
        });
        input.value = "";
    }

    function saveProject() {
        if (!currentProjectId) return;
        var boardSel = document.getElementById("detail-board");
        var boardId = boardSel ? (boardSel.value || "").trim() || null : null;
        var boardName = boardSel && boardSel.options[boardSel.selectedIndex] ? (boardSel.options[boardSel.selectedIndex].textContent || "").trim() || null : null;
        var payload = {
            name: (document.getElementById("detail-name").value || "").trim() || "New Project",
            description: (document.getElementById("detail-description").value || "").trim(),
            folder_location: (document.getElementById("detail-folder").value || "").trim(),
            additional_trigger_words: JSON.stringify(getTriggerWordsArray()),
            startup_instructions: (document.getElementById("detail-startup").value || "").trim(),
            provider: (document.getElementById("detail-provider").value || "").trim() || null,
            board_id: boardId,
            board_name: boardName
        };
        fetch("/api/projects/" + currentProjectId, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function(r) {
                if (r.ok) {
                    showSnackbar("'" + payload.name + "' updated", "success");
                    // Refresh the list without changing selection
                    fetch("/api/projects").then(function(r2) { return r2.ok ? r2.json() : []; }).then(function(data) {
                        projectsData = Array.isArray(data) ? data : [];
                        renderList(projectsData);
                    }).catch(function() {});
                } else {
                    r.json().then(function(e) { showSnackbar(e.detail || "Failed to save", "error"); });
                }
            })
            .catch(function() { showSnackbar("Failed to save project", "error"); });
    }

    function useProject() {
        if (!currentProjectId) return;
        fetch("/api/projects/" + currentProjectId + "/use", { method: "POST" })
            .then(function(r) {
                if (r.ok) {
                    loadProjects();
                    selectProject(currentProjectId);
                } else {
                    r.json().then(function(e) { alert(e.detail || "Failed"); });
                }
            })
            .catch(function() { alert("Failed to set project in use"); });
    }

    function addProject() {
        document.getElementById("create-project-name").value = "";
        document.getElementById("create-project-folder").value = "";
        document.getElementById("create-project-modal").classList.remove("hidden");
    }

    function closeCreateProjectModal() {
        document.getElementById("create-project-modal").classList.add("hidden");
    }

    function saveNewProject() {
        var name = document.getElementById("create-project-name").value.trim();
        if (!name) { showSnackbar("Project name is required", "error"); return; }
        var folder = document.getElementById("create-project-folder").value.trim();
        fetch("/api/projects", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, folder_location: folder }),
        })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || "Create failed"); });
                return r.json();
            })
            .then(function(data) {
                closeCreateProjectModal();
                loadProjects();
                selectProject(data.id);
                showSnackbar("Project created", "success");
            })
            .catch(function(e) {
                showSnackbar(e.message || "Failed to create project", "error");
            });
    }

    function browseForNewProject() {
        var input = document.getElementById("create-project-folder");
        var initial = (input.value || "").trim();
        var url = "/api/browse-folder";
        if (initial) url += "?initial_dir=" + encodeURIComponent(initial);
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.path) {
                    input.value = data.path;
                    // Auto-fill name from folder if name is empty
                    var nameInput = document.getElementById("create-project-name");
                    if (!nameInput.value.trim()) {
                        var parts = data.path.replace(/\/+$/, "").split("/");
                        nameInput.value = parts[parts.length - 1] || "";
                    }
                }
            })
            .catch(function() { showSnackbar("Could not open folder picker", "error"); });
    }

    function openContextItemModal(item) {
        var modal = document.getElementById("context-item-modal");
        var titleEl = document.getElementById("context-item-modal-title");
        var idEl = document.getElementById("context-item-id");
        var titleInput = document.getElementById("context-item-title");
        var contentInput = document.getElementById("context-item-content");
        if (!modal || !titleEl || !idEl || !titleInput || !contentInput) return;
        if (item) {
            titleEl.textContent = "Edit Context Item";
            idEl.value = String(item.id);
            titleInput.value = item.title || "";
            contentInput.value = item.content || "";
        } else {
            titleEl.textContent = "Add Context Item";
            idEl.value = "";
            titleInput.value = "";
            contentInput.value = "";
        }
        modal.classList.remove("hidden");
    }

    function closeContextItemModal() {
        var modal = document.getElementById("context-item-modal");
        if (modal) modal.classList.add("hidden");
    }

    function saveContextItem() {
        if (!currentProjectId) return;
        var idEl = document.getElementById("context-item-id");
        var titleInput = document.getElementById("context-item-title");
        var contentInput = document.getElementById("context-item-content");
        var itemId = (idEl && idEl.value) ? idEl.value.trim() : "";
        var title = (titleInput && titleInput.value) ? titleInput.value.trim() : "";
        var content = (contentInput && contentInput.value) ? contentInput.value.trim() : "";
        if (!title) {
            alert("Title is required.");
            return;
        }
        if (!content) {
            alert("Content is required.");
            return;
        }
        var url, method, body;
        if (itemId) {
            url = "/api/projects/" + currentProjectId + "/context-items/" + itemId;
            method = "PUT";
            body = JSON.stringify({ title: title, content: content });
        } else {
            url = "/api/projects/" + currentProjectId + "/context-items";
            method = "POST";
            body = JSON.stringify({ title: title, content: content });
        }
        fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: body })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || "Save failed"); });
                return r.ok ? {} : r.json();
            })
            .then(function() {
                closeContextItemModal();
                selectProject(currentProjectId);
            })
            .catch(function(e) {
                alert(e.message || "Failed to save context item");
            });
    }

    function removeContextItem(id) {
        if (!currentProjectId || !id) return;
        if (!confirm("Remove this context item?")) return;
        fetch("/api/projects/" + currentProjectId + "/context-items/" + id, { method: "DELETE" })
            .then(function(r) {
                if (r.ok) {
                    selectProject(currentProjectId);
                } else {
                    return r.json().then(function(e) { throw new Error(e.detail || "Remove failed"); });
                }
            })
            .catch(function(e) {
                alert(e.message || "Failed to remove context item");
            });
    }

    function openProjectFileFolder(id) {
        if (!currentProjectId || !id) return;
        fetch("/api/projects/" + currentProjectId + "/files/" + id + "/open-folder", { method: "POST" })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || "Failed"); });
            })
            .catch(function(e) { showSnackbar(e.message || "Could not open folder", "error"); });
    }

    function removeProjectFile(id) {
        if (!currentProjectId || !id) return;
        if (!confirm("Remove this file from the project?")) return;
        fetch("/api/projects/" + currentProjectId + "/files/" + id, { method: "DELETE" })
            .then(function(r) {
                if (r.ok) {
                    selectProject(currentProjectId);
                    showSnackbar("File removed", "success");
                } else {
                    return r.json().then(function(e) { throw new Error(e.detail || "Remove failed"); });
                }
            })
            .catch(function(e) {
                showSnackbar(e.message || "Failed to remove file", "error");
            });
    }

    function uploadProjectFiles(files) {
        if (!currentProjectId || !files || !files.length) return;
        var done = 0;
        var errs = [];
        function next(i) {
            if (i >= files.length) {
                selectProject(currentProjectId);
                if (errs.length) showSnackbar("Uploaded " + (files.length - errs.length) + "; " + errs.length + " failed", "error");
                else showSnackbar("File(s) uploaded", "success");
                return;
            }
            var form = new FormData();
            form.append("file", files[i]);
            fetch("/api/projects/" + currentProjectId + "/files", { method: "POST", body: form })
                .then(function(r) {
                    if (!r.ok) return r.json().then(function(e) { errs.push(e.detail || "Failed"); });
                })
                .catch(function() { errs.push("Network error"); })
                .then(function() { next(i + 1); });
        }
        next(0);
    }

    function browseFolder() {
        var input = document.getElementById("detail-folder");
        if (!input) return;
        var initial = (input.value || "").trim();
        var url = "/api/browse-folder";
        if (initial) url += "?initial_dir=" + encodeURIComponent(initial);
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.path) {
                    input.value = data.path;
                } else if (data.error) {
                    alert(data.error);
                }
            })
            .catch(function() { alert("Could not open folder picker."); });
    }

    function loadKanbanBoardStatus() {
        if (!currentProjectId) return;
        var statusEl = document.getElementById("kanban-board-status");
        var actionsEl = document.getElementById("kanban-board-actions");
        if (!statusEl || !actionsEl) return;
        statusEl.textContent = "Checking...";
        // Remove any existing buttons
        var oldBtn = actionsEl.querySelector("button, a");
        while (oldBtn) { oldBtn.remove(); oldBtn = actionsEl.querySelector("button, a"); }

        fetch("/api/projects/" + currentProjectId + "/kanban-board")
            .then(function(r) { return r.ok ? r.json() : { board: null }; })
            .catch(function() { return { board: null }; })
            .then(function(data) {
                if (data.board) {
                    statusEl.textContent = "Board: " + data.board.name;
                    var link = document.createElement("a");
                    link.href = "/kanban/?board_id=" + data.board.id;
                    link.className = "px-4 py-2 rounded bg-[#f97316] text-white hover:bg-[#ea580c] text-sm font-medium no-underline";
                    link.textContent = "Go To Board";
                    actionsEl.appendChild(link);
                } else {
                    statusEl.textContent = "No kanban board for this project.";
                    var btn = document.createElement("button");
                    btn.type = "button";
                    btn.className = "px-4 py-2 rounded bg-[#f97316] text-white hover:bg-[#ea580c] text-sm font-medium";
                    btn.textContent = "Create Board";
                    btn.addEventListener("click", createKanbanBoard);
                    actionsEl.appendChild(btn);
                }
            });
    }

    function createKanbanBoard() {
        if (!currentProjectId) return;
        var statusEl = document.getElementById("kanban-board-status");
        if (statusEl) statusEl.textContent = "Creating...";
        fetch("/api/projects/" + currentProjectId + "/kanban-board", { method: "POST" })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (data && data.board) {
                    showSnackbar("Board '" + data.board.name + "' " + (data.created ? "created" : "linked"), "success");
                    loadKanbanBoardStatus();
                } else {
                    showSnackbar("Failed to create board", "error");
                    if (statusEl) statusEl.textContent = "Error creating board.";
                }
            })
            .catch(function() {
                showSnackbar("Failed to create board", "error");
                if (statusEl) statusEl.textContent = "Error creating board.";
            });
    }

    function init() {
        var listEl = document.getElementById("projects-list");
        if (!listEl) return;

        document.addEventListener("click", function(e) {
            var menu = document.getElementById("project-context-menu");
            if (menu && !menu.classList.contains("hidden") && !menu.contains(e.target)) hideProjectContextMenu();
        });

        var ctxUse = document.querySelector(".project-ctx-use");
        var ctxEdit = document.querySelector(".project-ctx-edit");
        var ctxDelete = document.querySelector(".project-ctx-delete");
        if (ctxUse) ctxUse.addEventListener("click", function() {
            var id = contextMenuProjectId;
            hideProjectContextMenu();
            if (id != null) selectProject(id).then(function() { useProject(); });
        });
        if (ctxEdit) ctxEdit.addEventListener("click", function() {
            var id = contextMenuProjectId;
            hideProjectContextMenu();
            if (id != null) selectProject(id);
        });
        if (ctxDelete) ctxDelete.addEventListener("click", function() {
            var id = contextMenuProjectId;
            hideProjectContextMenu();
            if (id != null) deleteProjectFromList(id);
        });

        loadProjects();

        document.getElementById("projects-search").addEventListener("input", function() {
            searchText = this.value;
            renderList(projectsData);
        });

        document.getElementById("project-add").addEventListener("click", addProject);
        document.getElementById("project-create-big").addEventListener("click", addProject);

        // Create project modal
        document.getElementById("create-project-cancel").addEventListener("click", closeCreateProjectModal);
        document.getElementById("create-project-save").addEventListener("click", saveNewProject);
        document.getElementById("create-project-browse").addEventListener("click", browseForNewProject);
        document.getElementById("create-project-modal").addEventListener("click", function(e) {
        });
        document.getElementById("create-project-name").addEventListener("keydown", function(e) {
            if (e.key === "Enter") { e.preventDefault(); saveNewProject(); }
        });

        var folderBrowseBtn = document.getElementById("folder-browse");
        if (folderBrowseBtn) folderBrowseBtn.addEventListener("click", browseFolder);

        var triggersInput = document.getElementById("detail-triggers-input");
        if (triggersInput) triggersInput.addEventListener("keydown", function(e) {
            if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                addTriggerWordsFromInput();
            }
        });

        document.querySelectorAll(".project-tab").forEach(function(btn) {
            btn.addEventListener("click", function() {
                switchTab(btn.getAttribute("data-tab"));
            });
        });

        var detailProvider = document.getElementById("detail-provider");
        if (detailProvider) detailProvider.addEventListener("change", onBoardProviderChange);

        var detailBoard = document.getElementById("detail-board");
        if (detailBoard) detailBoard.addEventListener("change", updateKanbanVisibility);

        document.getElementById("project-update").addEventListener("click", saveProject);
        document.getElementById("project-use").addEventListener("click", useProject);
        document.getElementById("project-remove").addEventListener("click", function() {
            if (!currentProjectId) return;
            var name = document.getElementById("project-detail-title").textContent || "this project";
            if (!confirm("Remove project \"" + name + "\"? This cannot be undone and will remove board data for this project.")) return;
            fetch("/api/projects/" + currentProjectId, { method: "DELETE" })
                .then(function(r) {
                    if (r.ok) {
                        currentProjectId = null;
                        showEmpty();
                        loadProjects();
                    } else {
                        r.json().then(function(e) { alert(e.detail || "Failed to remove"); });
                    }
                })
                .catch(function() { alert("Failed to remove project"); });
        });

        var contextAddBtn = document.getElementById("context-item-add");
        if (contextAddBtn) contextAddBtn.addEventListener("click", function() {
            if (!currentProjectId) return;
            openContextItemModal(null);
        });
        var fileUploadBtn = document.getElementById("file-upload-btn");
        var fileUploadInput = document.getElementById("file-upload-input");
        if (fileUploadBtn && fileUploadInput) {
            fileUploadBtn.addEventListener("click", function() {
                if (!currentProjectId) return;
                fileUploadInput.click();
            });
            fileUploadInput.addEventListener("change", function() {
                var files = this.files;
                if (files && files.length) {
                    uploadProjectFiles(Array.prototype.slice.call(files));
                    this.value = "";
                }
            });
        }
        var contextSaveBtn = document.getElementById("context-item-save");
        if (contextSaveBtn) contextSaveBtn.addEventListener("click", saveContextItem);
        var contextCancelBtn = document.getElementById("context-item-cancel");
        if (contextCancelBtn) contextCancelBtn.addEventListener("click", closeContextItemModal);

        // Terminal tab
        var terminalRestartBtn = document.getElementById("terminal-restart");
        if (terminalRestartBtn) terminalRestartBtn.addEventListener("click", function() { restartTerminal(); });
        var terminalOverviewBtn = document.getElementById("terminal-overview");
        if (terminalOverviewBtn) terminalOverviewBtn.addEventListener("click", function() { readOutOverview(); });
        var terminalOverviewClose = document.getElementById("terminal-overview-close");
        if (terminalOverviewClose) terminalOverviewClose.addEventListener("click", function() {
            var overlay = document.getElementById("terminal-overview-overlay");
            if (overlay) overlay.classList.add("hidden");
        });
        // Terminal input
        var terminalInput = document.getElementById("terminal-input");
        if (terminalInput) terminalInput.addEventListener("keydown", function(e) {
            if (e.key === "Enter") {
                e.preventDefault();
                var val = terminalInput.value.trim();
                if (val) {
                    sendTerminalPrompt(val);
                    terminalInput.value = "";
                }
            } else if (e.key === "Escape") {
                e.preventDefault();
                abortTerminal();
            }
        });
        var terminalSendBtn = document.getElementById("terminal-input-send");
        if (terminalSendBtn) terminalSendBtn.addEventListener("click", function() {
            var inp = document.getElementById("terminal-input");
            if (inp && inp.value.trim()) {
                sendTerminalPrompt(inp.value.trim());
                inp.value = "";
            }
        });
    }

    // ── Terminal Management (pi RPC mode) ───────────────────────────────

    var _termWs = null;     // WebSocket connection to pi RPC
    var _termWsProjectId = null;  // Which project the WS is connected to
    var _termPollTimer = null;
    var _termTranscript = [];  // Array of {type, text, tool?, ts}
    var _currentAssistantEl = null;  // Current streaming assistant message element
    var _currentAssistantText = "";  // Current streaming text buffer
    var _termAgentRunning = false;   // Is pi currently processing?

    function initTerminal() {
        if (!currentProjectId) return;
        // If already connected to the SAME project, just ensure the WS is open
        if (_termWs && _termWsProjectId === currentProjectId) {
            if (_termWs.readyState !== WebSocket.OPEN && _termWs.readyState !== WebSocket.CONNECTING) {
                connectTerminalWs();
            }
            return;
        }
        // Different project (or first connection) — destroy old and reconnect
        destroyTerminal();
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) transcript.innerHTML = '';
        _clearTerminalState();
        _termTranscript = [];
        connectTerminalWs();
    }

    function _killWs() {
        // Close the current WS without triggering auto-reconnect.
        if (_termWs) {
            _termWs.onclose = null;  // prevent auto-reconnect
            _termWs.onerror = null;
            try { _termWs.close(); } catch (e) {}
            _termWs = null;
            _termWsProjectId = null;
        }
    }

    function connectTerminalWs() {
        if (!currentProjectId) return;

        // Close existing without triggering its onclose auto-reconnect
        _killWs();

        var token = (window.DECISIONSAI_INTERNAL_API_TOKEN || "").trim();
        var wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        var wsUrl = wsProtocol + "//" + window.location.host + "/api/projects/" + currentProjectId + "/terminal/ws";
        if (token) {
            wsUrl += "?internal_token=" + encodeURIComponent(token);
        }

        updateTerminalStatus("connecting");

        _termWs = new WebSocket(wsUrl);
        _termWsProjectId = currentProjectId;

        _termWs.onopen = function() {
            updateTerminalStatus("connected");
            // Clear placeholder / show connection message
            var transcript = document.getElementById("terminal-transcript");
            if (transcript && !transcript.querySelector(".transcript-msg")) {
                transcript.innerHTML = '<div class="transcript-msg system">Connected</div>';
            }
        };

        _termWs.onmessage = function(event) {
            try {
                var msg = JSON.parse(event.data);
                handleRpcEvent(msg);
            } catch (e) {}
        };

        _termWs.onclose = function() {
            // Only auto-reconnect if this is still the active WS (not replaced by project switch)
            var closedWs = this;
            updateTerminalStatus("disconnected");
            setTimeout(function() {
                if (closedWs !== _termWs) return;  // replaced by new connection
                var tabEl = document.getElementById("tab-terminal");
                if (tabEl && !tabEl.classList.contains("hidden") && currentProjectId) {
                    connectTerminalWs();
                }
            }, 3000);
        };

        _termWs.onerror = function() {
            updateTerminalStatus("error");
        };

        // Keepalive ping
        if (_termPollTimer) clearInterval(_termPollTimer);
        _termPollTimer = setInterval(function() {
            if (_termWs && _termWs.readyState === WebSocket.OPEN) {
                _termWs.send(JSON.stringify({type: "ping"}));
            }
        }, 30000);
    }

    function handleRpcEvent(msg) {
        var transcript = document.getElementById("terminal-transcript");
        if (!transcript) return;

        switch (msg.type) {
            // ── Connection ──
            case "connected":
                updateTerminalStatus("connected");
                _clearTerminalState();
                transcript.innerHTML = '';
                if (msg.buffer && msg.buffer.length) {
                    msg.buffer.forEach(function(entry) {
                        _renderBufferMessage(transcript, entry);
                    });
                }
                break;

            // ── Agent lifecycle ──
            case "agent_start":
                _termAgentRunning = true;
                break;
            case "agent_end":
                _termAgentRunning = false;
                _clearTerminalState();
                var sep = document.createElement("div");
                sep.className = "transcript-separator";
                transcript.appendChild(sep);
                scrollTranscript();
                break;

            // ── Turn lifecycle — no visual output ──
            case "turn_start":
            case "turn_end":
                break;

            // ── Message streaming (the main event) ──
            case "message_update": {
                var evt = msg.assistantMessageEvent;
                if (!evt) break;
                switch (evt.type) {
                    case "start":
                        // New assistant message beginning
                        _currentAssistantText = "";
                        _currentAssistantEl = null;
                        startAssistantMessage(transcript);
                        break;
                    case "text_start":
                        // Text content block starting
                        if (!_currentAssistantEl) startAssistantMessage(transcript);
                        break;
                    case "text_delta":
                        _currentAssistantText += (evt.delta || "");
                        updateAssistantMessage(transcript, _currentAssistantText);
                        break;
                    case "text_end":
                        // Text block complete — already shown
                        break;
                    case "thinking_start":
                    case "thinking_delta":
                    case "thinking_end":
                        // Thinking blocks — don't render
                        break;
                    case "toolcall_start":
                        // LLM decided to call a tool — DON'T render here.
                        // tool_execution_start will fire with the real args and name.
                        // Just finalize any in-progress assistant text.
                        if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                        break;
                    case "toolcall_delta":
                        // Argument streaming — skip
                        break;
                    case "toolcall_end":
                        // Tool call object fully resolved — skip, tool_execution_start shows it
                        break;
                    case "done":
                        if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                        break;
                    case "error":
                        appendTranscriptLine(transcript, "error", evt.reason || "Error");
                        break;
                }
                break;
            }

            // ── Message start/end ──
            case "message_start": {
                var m = msg.message || {};
                if (m.role === "assistant") {
                    // Start a new assistant block — but message_update "start" may also do this
                    if (!_currentAssistantEl) {
                        _currentAssistantText = "";
                        startAssistantMessage(transcript);
                    }
                }
                // User messages already shown locally — skip entirely
                break;
            }
            case "message_end": {
                var m = msg.message || {};
                if (m.role === "assistant" && _currentAssistantEl) {
                    finalizeAssistantMessage(transcript);
                }
                break;
            }

            // ── Tool execution (the real tool call) ──
            case "tool_execution_start": {
                if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                var tName = msg.toolName || "tool";
                var tArgs = msg.args || {};
                var argsStr = "";
                try { argsStr = JSON.stringify(tArgs); } catch(e) { argsStr = String(tArgs); }
                appendTranscriptLine(transcript, "tool-call", tName + " " + _truncateArgs(argsStr, 200));
                break;
            }
            case "tool_execution_update": {
                var partial = msg.partialResult || {};
                var pText = "";
                if (partial.content && Array.isArray(partial.content)) {
                    pText = partial.content.filter(function(b){ return b.type === "text"; }).map(function(b){ return b.text; }).join("\n");
                }
                if (pText) {
                    updateOrAppendToolResult(transcript, msg.toolCallId, pText, false);
                }
                break;
            }
            case "tool_execution_end": {
                var result = msg.result || {};
                var rText = "";
                if (result.content && Array.isArray(result.content)) {
                    rText = result.content.filter(function(b){ return b.type === "text"; }).map(function(b){ return b.text; }).join("\n");
                }
                if (rText) {
                    var isErr = msg.isError === true;
                    updateOrAppendToolResult(transcript, msg.toolCallId || "_", rText, isErr);
                }
                break;
            }

            // ── Compaction ──
            case "compaction_start":
                appendTranscriptLine(transcript, "system", "Compacting context...");
                break;
            case "compaction_end":
                appendTranscriptLine(transcript, "system", "Context compacted");
                break;

            // ── Auto-retry ──
            case "auto_retry_start":
            case "auto_retry_end":
                break;

            // ── Queue updates ──
            case "queue_update":
                break;

            // ── Extension UI ──
            case "extension_ui_request":
                // Fire-and-forget notifications like setStatus, notify
                var method = msg.method || "";
                if (method === "notify") {
                    appendTranscriptLine(transcript, "system", msg.message || "");
                } else if (method === "setStatus") {
                    // Status bar text — skip
                }
                break;
            case "extension_error":
                appendTranscriptLine(transcript, "error", msg.error || "Extension error");
                break;

            // ── RPC responses ──
            case "response":
                // Command responses (prompt, steer, abort, etc.) — not rendered
                break;

            // ── Error from backend ──
            case "error":
                appendTranscriptLine(transcript, "error", msg.message || "Unknown error");
                updateTerminalStatus("error");
                break;

            // ── Keepalives ──
            case "pong":
            case "ping":
                break;

            // ── Unknown — suppress, don't render JSON ──
            default:
                break;
        }
    }

    var _toolResultEls = {};  // toolCallId -> DOM element for streaming tool output

    function _clearTerminalState() {
        _currentAssistantEl = null;
        _currentAssistantText = "";
        _toolResultEls = {};
    }

    function startAssistantMessage(transcript) {
        var el = document.createElement("div");
        el.className = "transcript-msg assistant streaming";
        el.innerHTML = "";
        transcript.appendChild(el);
        _currentAssistantEl = el;
        scrollTranscript();
    }

    function updateAssistantMessage(transcript, text) {
        if (!_currentAssistantEl) {
            startAssistantMessage(transcript);
        }
        _currentAssistantEl.innerHTML = renderMarkdownLite(text);
        scrollTranscript();
    }

    function finalizeAssistantMessage(transcript) {
        if (_currentAssistantEl) {
            _currentAssistantEl.classList.remove("streaming");
            // Remove streaming cursor if present
            var cursor = _currentAssistantEl.querySelector(".streaming-cursor");
            if (cursor) cursor.remove();
        }
        _currentAssistantText = "";
        _currentAssistantEl = null;
    }

    function appendTranscriptLine(transcript, type, text) {
        var el = document.createElement("div");
        el.className = "transcript-msg " + escapeAttr(type);
        // Tool calls and results may contain long text — preserve whitespace
        if (type === "tool-result" || type === "tool-error" || type === "assistant") {
            el.innerHTML = renderMarkdownLite(text);
        } else {
            el.textContent = text;
        }
        transcript.appendChild(el);
        scrollTranscript();
        return el;
    }

    function updateOrAppendToolResult(transcript, toolCallId, text, isError) {
        var cssClass = isError ? "tool-error" : "tool-result";
        var existing = _toolResultEls[toolCallId];
        if (existing) {
            // Update existing (streaming)
            existing.innerHTML = renderMarkdownLiteNoCursor(text);
            scrollTranscript();
        } else {
            // New result element
            var el = appendTranscriptLine(transcript, cssClass, text);
            _toolResultEls[toolCallId] = el;
        }
    }

    function scrollTranscript() {
        var container = document.getElementById("terminal-container");
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }

    function renderMarkdownLite(text) {
        if (!text) return "";
        var s = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        // Code blocks
        s = s.replace(/```([\s\S]*?)```/g, "<pre class=\"code-block\">$1</pre>");
        // Inline code
        s = s.replace(/`([^`]+)`/g, "<code class=\"inline-code\">$1</code>");
        // Bold
        s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        // Line breaks
        s = s.replace(/\n/g, "<br>");
        // Streaming cursor
        s += "<span class=\"streaming-cursor\">▌</span>";
        return s;
    }

    function renderMarkdownLiteNoCursor(text) {
        if (!text) return "";
        var s = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        s = s.replace(/```([\s\S]*?)```/g, "<pre class=\"code-block\">$1</pre>");
        s = s.replace(/`([^`]+)`/g, "<code class=\"inline-code\">$1</code>");
        s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        s = s.replace(/\n/g, "<br>");
        return s;
    }

    function _truncateArgs(argsStr, maxLen) {
        if (!argsStr) return "";
        if (argsStr.length > maxLen) return argsStr.substring(0, maxLen) + "...";
        return argsStr;
    }

    function _renderBufferMessage(transcript, entry) {
        // Render a message from the RPC buffer (connected message replay)
        // format: {role: "user"|"assistant", content: "", tool_name: "", tool_result: "", is_error: bool}
        var role = entry.role || "";
        if (role === "user" && entry.content) {
            appendTranscriptLine(transcript, "user", entry.content);
        } else if (role === "assistant" && entry.content) {
            appendTranscriptLine(transcript, "assistant", entry.content);
        } else if (role === "tool_result") {
            var cls = entry.is_error ? "tool-error" : "tool-result";
            var label = entry.tool_name ? entry.tool_name + ": " : "";
            appendTranscriptLine(transcript, cls, label + (entry.tool_result || ""));
        }
    }

    function truncateStr(s, maxLen) {
        if (!s) return "";
        s = String(s).replace(/\n/g, " ");
        if (s.length > maxLen) return s.substring(0, maxLen) + "...";
        return s;
    }

    function updateTerminalStatus(status) {
        var dot = document.getElementById("terminal-status");
        var text = document.getElementById("terminal-status-text");
        if (!dot || !text) return;
        switch (status) {
            case "connected":
                dot.className = "w-2 h-2 rounded-full bg-green-500";
                text.textContent = "Connected";
                text.className = "text-xs text-green-400";
                break;
            case "connecting":
                dot.className = "w-2 h-2 rounded-full bg-yellow-500";
                text.textContent = "Connecting...";
                text.className = "text-xs text-yellow-400";
                break;
            case "error":
                dot.className = "w-2 h-2 rounded-full bg-red-500";
                text.textContent = "Error";
                text.className = "text-xs text-red-400";
                break;
            case "disconnected":
            default:
                dot.className = "w-2 h-2 rounded-full bg-gray-500";
                text.textContent = "Disconnected";
                text.className = "text-xs text-gray-400";
                break;
        }
    }

    function restartTerminal() {
        if (!currentProjectId) return;
        destroyTerminal();
        apiFetch("/api/projects/" + currentProjectId + "/terminal/restart", { method: "POST" })
            .then(function(data) {
                if (data.success) {
                    // Clear transcript
                    var transcript = document.getElementById("terminal-transcript");
                    if (transcript) transcript.innerHTML = '';
                    _clearTerminalState();
                    _termTranscript = [];
                    initTerminal();
                } else {
                    showSnackbar(data.error || "Failed to restart terminal", "error");
                }
            })
            .catch(function() {
                showSnackbar("Failed to restart terminal", "error");
            });
    }

    function abortTerminal() {
        if (!_termWs || _termWs.readyState !== WebSocket.OPEN) return;
        if (!_termAgentRunning) return;  // Nothing to abort
        _termWs.send(JSON.stringify({type: "abort"}));
        // Show in transcript
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) {
            appendTranscriptLine(transcript, "system", "\u23f9 Aborted");
        }
        // Finalize any in-progress assistant message
        if (_currentAssistantEl) finalizeAssistantMessage(transcript);
        _termAgentRunning = false;
        _clearTerminalState();
    }

    function sendTerminalPrompt(instruction) {
        if (!instruction || !instruction.trim()) return;
        if (!_termWs || _termWs.readyState !== WebSocket.OPEN) {
            showSnackbar("Terminal not connected — try restarting", "error");
            return;
        }
        // Show the user message immediately (pi will also echo it via message_start,
        // but we mark it so we don't duplicate)
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) {
            var el = appendTranscriptLine(transcript, "user", instruction);
            el.setAttribute("data-prompt-text", instruction.substring(0, 100));
        }
        _termWs.send(JSON.stringify({type: "prompt", message: instruction}));
    }

    function sendTerminalSteer(instruction) {
        if (!instruction || !instruction.trim()) return;
        if (!_termWs || _termWs.readyState !== WebSocket.OPEN) return;
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) {
            appendTranscriptLine(transcript, "user", "[steer] " + instruction);
        }
        _termWs.send(JSON.stringify({type: "steer", message: instruction}));
    }

    function readOutOverview() {
        if (!currentProjectId) return;
        var btn = document.getElementById("terminal-overview");
        var originalText = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = "\u23F3 Analyzing...";
        }
        // Show loading overlay
        var overlay = document.getElementById("terminal-overview-overlay");
        var overviewText = document.getElementById("terminal-overview-text");
        if (overlay) overlay.classList.remove("hidden");
        if (overviewText) overviewText.textContent = "Analyzing terminal output...";

        apiFetch("/api/projects/" + currentProjectId + "/terminal/overview", { method: "POST" })
            .then(function(data) {
                if (data.summary) {
                    if (overviewText) overviewText.textContent = data.summary;
                    showSnackbar("Overview ready — speaking aloud", "success", { duration: 15000 });
                } else if (data.error) {
                    if (overviewText) overviewText.textContent = "Error: " + data.error;
                    showSnackbar(data.error, "error");
                }
            })
            .catch(function(e) {
                if (overviewText) overviewText.textContent = "Failed to get overview.";
                showSnackbar("Failed to get terminal overview", "error");
            })
            .finally(function() {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                }
            });
    }

    function destroyTerminal() {
        if (_termPollTimer) {
            clearInterval(_termPollTimer);
            _termPollTimer = null;
        }
        _killWs();
        _currentAssistantEl = null;
        _currentAssistantText = "";
        updateTerminalStatus("disconnected");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
