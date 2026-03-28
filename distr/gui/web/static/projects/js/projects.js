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
    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "projects-snackbar" }); }

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

        var boardLink = document.getElementById("project-open-board");
        var boardUrl = (project.board_name || project.provider) ? "/board/?project_id=" + project.id : "/board/";
        boardLink.href = boardUrl;
        boardLink.classList.toggle("hidden", !(project.board_name || project.provider));

        document.getElementById("project-use").textContent = project.in_use ? "In use" : "Use";
        document.getElementById("project-use").disabled = !!project.in_use;
        renderList(projectsData);
    }

    function selectProject(id) {
        currentProjectId = id;
        return fetch("/api/projects/" + id)
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(project) {
                showDetail(project);
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
        if (tabName === "board") {
            var ps = document.getElementById("detail-provider");
            var bs = document.getElementById("detail-board");
            var bid = bs && bs.value ? bs.value : null;
            var bname = bs && bs.options[bs.selectedIndex] ? bs.options[bs.selectedIndex].textContent.trim() : "";
            loadBoardProvidersAndSelect(ps ? ps.value : "", bid, bname);
            loadKanbanBoardStatus();
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

    function onBoardProviderChange() {
        var providerSel = document.getElementById("detail-provider");
        var boardSel = document.getElementById("detail-board");
        if (!providerSel || !boardSel) return;
        var p = (providerSel.value || "").trim();
        if (!p) {
            boardSel.innerHTML = "<option value=\"\">None</option>";
            boardSel.value = "";
            boardSel.disabled = false;
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
            if (e.target === this) closeCreateProjectModal();
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
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
