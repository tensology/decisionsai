/**
 * Projects page: matches desktop Projects UI (distr/gui/projects.py).
 * Left: search + project list + Add. Right: empty state or detail form with tabs (Details, Startup, CLI, Terminal).
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
            var inUseDot = p.in_use
                ? "<span class=\"project-in-use-dot w-2 h-2 rounded-full bg-[#f97316] flex-shrink-0\" title=\"In use\" aria-label=\"In use\"></span>"
                : "";
            var active = currentProjectId === p.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            return "<div class=\"project-item-wrapper flex items-center gap-1 rounded border" + active + " group focus:outline-none focus:ring-2 focus:ring-[#f97316]/60\" data-id=\"" + p.id + "\" tabindex=\"0\" role=\"option\" aria-selected=\"" + (currentProjectId === p.id ? "true" : "false") + "\">" +
                "<button type=\"button\" class=\"project-item flex-1 min-w-0 text-left px-3 py-2 text-white text-sm\" data-id=\"" + p.id + "\">" + escapeAttr(p.name || "Untitled") + "</button>" +
                inUseDot +
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
            wrap.addEventListener("focus", function() {
                var id = parseInt(wrap.getAttribute("data-id"), 10);
                if (id && currentProjectId !== id) selectProject(id);
            });
        });
    }

    function isTypingTarget(target) {
        if (!target) return false;
        var tag = (target.tagName || "").toLowerCase();
        return !!(target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select");
    }

    function bindProjectListKeyboard() {
        if (!window.DecisionsListKeyboard) return;
        window.DecisionsListKeyboard.bind({
            listEl: "projects-list",
            namespace: "projects",
            rowSelector: ".project-item-wrapper",
            getRowId: function(row) { return parseInt(row.getAttribute("data-id"), 10); },
            getSelectedId: function() { return currentProjectId; },
            onSelect: function(id) { selectProject(id); },
            onDelete: function(id) { deleteProjectFromList(id); },
            pageGuard: function() { return !!document.getElementById("projects-list"); },
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
        window.DecisionsAPI.confirm({
            title: "Remove project",
            message: "Remove project \"" + name + "\"? This cannot be undone and will remove board data for this project.",
            confirmLabel: "Remove",
            danger: true,
            onConfirm: function() {
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
        });
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
                var params = new URLSearchParams(window.location.search);
                var requestedId = parseInt(params.get("project_id"), 10);
                if (requestedId && data.some(function(p) { return p.id === requestedId; })) {
                    var requestedTab = (params.get("tab") || "").trim().toLowerCase();
                    selectProject(requestedId).then(function() {
                        if (requestedTab) switchTab(requestedTab);
                    });
                    return;
                }
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
        destroyShellTerminal();
        renderList(projectsData);
    }

    function showDetail(project) {
        document.getElementById("projects-empty").classList.add("hidden");
        document.getElementById("projects-detail").classList.remove("hidden");
        currentProjectId = project.id;
        document.getElementById("project-detail-title").textContent = project.name || "Project";
        document.getElementById("detail-name").value = project.name || "";
        document.getElementById("detail-description").value = project.description || "";
        var notesEl = document.getElementById("detail-notes");
        if (notesEl) notesEl.value = project.notes || "";
        document.getElementById("detail-folder").value = project.folder_location || "";
        var words = [];
        try { words = JSON.parse(project.additional_trigger_words || "[]"); } catch (e) {}
        renderTriggerBadges(words || []);
        var triggersInput = document.getElementById("detail-triggers-input");
        if (triggersInput) triggersInput.value = "";
        document.getElementById("detail-startup").value = project.startup_instructions || "";
        var startTracker = document.getElementById("detail-start-time-tracker");
        if (startTracker) startTracker.checked = project.start_time_tracker !== false;
        updateStartupTimeTrackerVisibility(project);
        var terminalBackendSel = document.getElementById("terminal-backend-select");
        if (terminalBackendSel) terminalBackendSel.value = project.coding_backend || "pi";
        loadProjectCliBackends(project.id, project.coding_backend || "pi");
        loadCliModels(project.coding_backend || "pi");
        loadProjectBoardSelect(project);

        var useBtn = document.getElementById("project-use");
        if (useBtn) {
            useBtn.classList.toggle("hidden", !!project.in_use);
            useBtn.textContent = "Use";
        }
        renderList(projectsData);
    }

    function selectProject(id) {
        var prevProjectId = currentProjectId;
        currentProjectId = id;

        // Tear down interactive shell WS when switching projects (different PTY / folder).
        if (prevProjectId && prevProjectId !== id) {
            destroyShellTerminal();
        }

        // Save running terminals for the project we're leaving
        if (prevProjectId && prevProjectId !== id && Object.keys(_startupTerminals).length > 0) {
            detachProjectTerminals(prevProjectId);
        }

        return fetch("/api/projects/" + id)
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(project) {
                showDetail(project);
                switchTab("details");
                // Reconnect any running terminals for this project.
                // First check in-memory state (navigation), then query server (page reload).
                if (Object.keys(_startupTerminals).length === 0) {
                    if (_projectTerminalState[id] && _projectTerminalState[id].length) {
                        reattachProjectTerminals(id);
                    } else {
                        // No running terminals for this project — reset buttons
                        var sBtn = document.getElementById("startup-start-btn");
                        var tBtn = document.getElementById("startup-terminate-all-btn");
                        if (sBtn) sBtn.classList.remove("hidden");
                        if (tBtn) tBtn.classList.add("hidden");
                        // Page was reloaded — ask server if any sessions are still running
                        apiFetch("/api/projects/" + id + "/startup-sessions")
                            .then(function(data) {
                                if (!data.sessions || !data.sessions.length) return;
                                var alive = data.sessions.filter(function(s) { return s.alive; });
                                if (!alive.length) return;
                                _projectTerminalState[id] = alive.map(function(s) {
                                    return { processId: s.process_id, command: s.command, pid: s.pid || null };
                                });
                                reattachProjectTerminals(id);
                                // Switch to startup tab so the terminals are visible
                                switchTab("startup");
                            })
                            .catch(function() {});
                    }
                }
                // If a terminal tab is active and project changed, reconnect
                if (prevProjectId !== id) {
                    var cliTabEl = document.getElementById("tab-cli");
                    var shellTabEl = document.getElementById("tab-terminal");
                    if (cliTabEl && !cliTabEl.classList.contains("hidden")) {
                        initTerminal();
                    }
                    if (shellTabEl && !shellTabEl.classList.contains("hidden")) {
                        initShellTerminal();
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
        if (tabName === "cli" || tabName === "terminal") {
            tabContent.classList.add("hidden");
        } else {
            tabContent.classList.remove("hidden");
        }
        if (tabName === "cli") {
            initTerminal();
        } else {
            destroyTerminal();
        }
        if (tabName === "terminal") {
            initShellTerminal();
            // Hidden->visible transitions can need an extra resize pass.
            setTimeout(function() { try { scheduleShellResize(); } catch (e) {} }, 60);
            setTimeout(function() { try { scheduleShellResize(); } catch (e) {} }, 220);
        }
        // Do not close the shell WebSocket when leaving the Terminal tab — switching CLI ↔ Terminal
        // would reconnect and the server replays the PTY buffer, duplicating the whole scrollback.
        var sections = window.ProjectsTabSections || {};
        if (sections[tabName] && typeof sections[tabName].onActivated === "function") {
            sections[tabName].onActivated();
        }
    }

    var projectBoardOptions = [];

    function projectBoardSelectedValue(project) {
        if (!project) return "";
        if (project.kanban_board_id) return "local:" + String(project.kanban_board_id);
        var provider = (project.provider || "").toString().trim();
        var boardId = (project.board_id || "").toString().trim();
        if (provider && boardId) return provider + ":" + boardId;
        return "";
    }

    function projectHasLinkedBoard(project) {
        if (!project) return false;
        if (project.kanban_board_id) return true;
        var provider = (project.provider || "").toString().trim();
        var boardId = (project.board_id || "").toString().trim();
        return !!(provider && boardId);
    }

    function boardFieldsHaveLinkedBoard(fields) {
        if (!fields) return false;
        if (fields.kanban_board_id) return true;
        return !!((fields.provider || "").trim() && (fields.board_id || "").trim());
    }

    function updateStartupTimeTrackerVisibility(project) {
        var label = document.getElementById("startup-time-tracker-label");
        var checkbox = document.getElementById("detail-start-time-tracker");
        if (!label) return;
        var linked = projectHasLinkedBoard(project);
        label.classList.toggle("hidden", !linked);
        if (!linked && checkbox) checkbox.checked = false;
    }

    function updateStartupTimeTrackerVisibilityFromBoardSelect() {
        var boardSel = document.getElementById("detail-board");
        var fields = parseProjectBoardValue(boardSel ? boardSel.value : "");
        var label = document.getElementById("startup-time-tracker-label");
        var checkbox = document.getElementById("detail-start-time-tracker");
        if (!label) return;
        var linked = boardFieldsHaveLinkedBoard(fields);
        label.classList.toggle("hidden", !linked);
        if (!linked && checkbox) checkbox.checked = false;
    }

    function renderProjectBoardSelectHtml() {
        var groups = [
            { key: "local", label: "Local", match: function(opt) { return opt.source === "local"; } },
            { key: "trello", label: "Trello", match: function(opt) { return opt.source === "trello"; } },
            { key: "jira", label: "Jira", match: function(opt) { return opt.source === "jira"; } }
        ];
        var html = '<option value="">None</option>';
        groups.forEach(function(group) {
            var items = projectBoardOptions.filter(group.match);
            if (!items.length) return;
            html += '<optgroup label="' + escapeAttr(group.label) + '">';
            html += items.map(function(opt) {
                return '<option value="' + escapeAttr(opt.value) + '">' + escapeAttr(opt.label) + "</option>";
            }).join("");
            html += "</optgroup>";
        });
        return html;
    }

    function loadProjectBoardSelect(project) {
        var boardSel = document.getElementById("detail-board");
        if (!boardSel) return;
        boardSel.disabled = true;
        boardSel.innerHTML = '<option value="">Loading boards...</option>';
        Promise.all([
            fetch("/api/tickets/boards").then(function(r) { return r.ok ? r.json() : []; }).catch(function() { return []; }),
            fetch("/api/tickets/external-boards").then(function(r) { return r.ok ? r.json() : { trello: [], jira: [] }; }).catch(function() { return { trello: [], jira: [] }; })
        ]).then(function(results) {
            var boards = Array.isArray(results[0]) ? results[0] : [];
            var external = results[1] || {};
            projectBoardOptions = [];
            boards.filter(function(b) { return b.source === "database"; }).forEach(function(b) {
                projectBoardOptions.push({
                    source: "local",
                    label: b.name || ("Board " + b.id),
                    value: "local:" + b.id
                });
            });
            (external.trello || []).forEach(function(b) {
                projectBoardOptions.push({
                    source: "trello",
                    label: b.name || String(b.id || ""),
                    value: "trello:" + b.id
                });
            });
            (external.jira || []).forEach(function(b) {
                projectBoardOptions.push({
                    source: "jira",
                    label: b.name || String(b.id || ""),
                    value: "jira:" + b.id
                });
            });
            boardSel.innerHTML = renderProjectBoardSelectHtml();
            var want = projectBoardSelectedValue(project);
            var finishSelection = function() {
                if (want && !projectBoardOptions.some(function(opt) { return opt.value === want; })) {
                    var fallbackLabel = (project.board_name || "").trim() || want.split(":").slice(1).join(":");
                    var source = want.split(":")[0];
                    if (source === "local" || source === "trello" || source === "jira") {
                        projectBoardOptions.push({ source: source, label: fallbackLabel, value: want });
                        boardSel.innerHTML = renderProjectBoardSelectHtml();
                    }
                }
                boardSel.value = want && Array.from(boardSel.options).some(function(opt) { return opt.value === want; }) ? want : "";
                boardSel.disabled = false;
                updateProjectBoardGoto();
                updateStartupTimeTrackerVisibilityFromBoardSelect();
            };
            if (!want && project && project.id) {
                fetch("/api/projects/" + project.id + "/kanban-board")
                    .then(function(r) { return r.ok ? r.json() : { board: null }; })
                    .catch(function() { return { board: null }; })
                    .then(function(data) {
                        if (data.board && data.board.id) {
                            want = "local:" + String(data.board.id);
                        }
                        finishSelection();
                    });
                return;
            }
            finishSelection();
        });
    }

    function parseProjectBoardValue(value) {
        var raw = (value || "").trim();
        if (!raw) {
            return { provider: null, board_id: null, board_name: null, kanban_board_id: null };
        }
        var parts = raw.split(":");
        var source = parts[0];
        var id = parts.slice(1).join(":");
        var boardSel = document.getElementById("detail-board");
        var boardName = null;
        if (boardSel && boardSel.selectedIndex >= 0) {
            boardName = (boardSel.options[boardSel.selectedIndex].textContent || "").trim() || null;
        }
        if (source === "local") {
            return {
                provider: null,
                board_id: null,
                board_name: boardName,
                kanban_board_id: parseInt(id, 10) || null
            };
        }
        if (source === "trello" || source === "jira") {
            return {
                provider: source,
                board_id: id || null,
                board_name: boardName,
                kanban_board_id: null
            };
        }
        return { provider: null, board_id: null, board_name: null, kanban_board_id: null };
    }

    function updateProjectBoardGoto() {
        var gotoEl = document.getElementById("detail-board-goto");
        var boardSel = document.getElementById("detail-board");
        if (!gotoEl || !boardSel) return;
        var raw = (boardSel.value || "").trim();
        if (!raw) {
            gotoEl.classList.add("hidden");
            gotoEl.href = "#";
            return;
        }
        var parts = raw.split(":");
        var source = parts[0];
        var id = parts.slice(1).join(":");
        var href = "/tickets/";
        if (source === "local") {
            href += "?board_id=" + encodeURIComponent(id);
        } else if (source === "trello" || source === "jira") {
            href += "?source=" + encodeURIComponent(source) + "&board_id=" + encodeURIComponent(id);
        } else {
            gotoEl.classList.add("hidden");
            gotoEl.href = "#";
            return;
        }
        gotoEl.href = href;
        gotoEl.classList.remove("hidden");
    }

    function renderTriggerBadges(words) {
        var wrap = document.getElementById("trigger-badges-flex");
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
        var wrap = document.getElementById("trigger-badges-flex");
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
        var wrap = document.getElementById("trigger-badges-flex");
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
        var boardFields = parseProjectBoardValue(boardSel ? boardSel.value : "");
        var payload = {
            name: (document.getElementById("detail-name").value || "").trim() || "New Project",
            description: (document.getElementById("detail-description").value || "").trim(),
            notes: (document.getElementById("detail-notes")?.value || "").trim(),
            folder_location: (document.getElementById("detail-folder").value || "").trim(),
            additional_trigger_words: JSON.stringify(getTriggerWordsArray()),
            startup_instructions: (document.getElementById("detail-startup").value || "").trim(),
            start_time_tracker: !!(document.getElementById("detail-start-time-tracker") && document.getElementById("detail-start-time-tracker").checked),
            provider: boardFields.provider,
            board_id: boardFields.board_id,
            board_name: boardFields.board_name,
            kanban_board_id: boardFields.kanban_board_id
        };
        var modelSel = document.getElementById("terminal-model-select");
        if (modelSel && (modelSel.value || "").trim()) {
            payload.coding_backend_model = (modelSel.value || "").trim();
        }
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

    function backendStateClasses(state, active) {
        if (state === "ready") {
            return active
                ? "border-[#22c55e]/60 bg-[#22c55e]/10 text-green-100"
                : "border-white/15 bg-white/[0.03] text-gray-300";
        }
        if (state === "missing" || state === "misconfigured") {
            return active
                ? "border-amber-400/60 bg-amber-500/10 text-amber-100"
                : "border-white/15 bg-white/[0.03] text-gray-300";
        }
        return "border-white/15 bg-white/[0.03] text-gray-300";
    }

    function populateBackendSelect(sel, backends, active) {
        if (!sel) return;
        sel.innerHTML = backends.map(function(b) {
            return "<option value=\"" + escapeAttr(b.id) + "\">" + escapeAttr(b.name) + "</option>";
        }).join("");
        sel.value = active;
    }

    function activeCodingBackend() {
        var terminalSel = document.getElementById("terminal-backend-select");
        var detailSel = document.getElementById("detail-coding-backend");
        return ((terminalSel && terminalSel.value) || (detailSel && detailSel.value) || "pi").trim();
    }

    function loadProjectCliBackends(projectId, activeBackend) {
        var sel = document.getElementById("detail-coding-backend");
        var terminalSel = document.getElementById("terminal-backend-select");
        var list = document.getElementById("coding-backend-status-list");
        var pill = document.getElementById("coding-backend-active-pill");
        if (!sel && !terminalSel && !list && !pill) return;
        activeBackend = activeBackend || "pi";
        apiFetch("/api/projects/" + projectId + "/cli-backends")
            .then(function(data) {
                var backends = data.backends || [];
                var active = data.active_backend || activeBackend || "pi";
                populateBackendSelect(sel, backends, active);
                populateBackendSelect(terminalSel, backends, active);
                var activeItem = backends.filter(function(b) { return b.id === active; })[0];
                if (pill) {
                    pill.textContent = activeItem ? (activeItem.name + " / " + activeItem.state) : "Pi / default";
                    pill.className = "text-xs px-2 py-1 rounded border " + (activeItem && activeItem.ready ? "border-green-500/40 text-green-300 bg-green-500/10" : "border-amber-400/40 text-amber-200 bg-amber-500/10");
                }
                if (list) {
                    list.innerHTML = backends.map(function(b) {
                        var activeBadge = b.active ? "<span class=\"text-[10px] px-1.5 py-0.5 rounded bg-[#f97316]/20 text-[#fdba74]\">Active</span>" : "";
                        var stateLabel = b.ready ? "Ready" : (b.setup_required ? "Setup required" : (b.state || "Unavailable"));
                        var setup = b.ready ? "" : "<div class=\"mt-1 text-[11px] text-gray-400 leading-snug\">" + escapeAttr(b.setup_instructions || b.message || "") + "</div>";
                        return "<div class=\"rounded border p-2 text-xs " + backendStateClasses(b.state, b.active) + "\">" +
                            "<div class=\"flex items-center justify-between gap-2\"><span class=\"font-medium text-white\">" + escapeAttr(b.name) + "</span>" + activeBadge + "</div>" +
                            "<div class=\"mt-1 text-gray-300\">" + escapeAttr(stateLabel) + "</div>" +
                            "<div class=\"mt-0.5 text-[11px] text-gray-500\">" + escapeAttr(b.path || "Not found on PATH") + "</div>" +
                            setup +
                            "</div>";
                    }).join("");
                }
            })
            .catch(function() {
                if (list) list.innerHTML = "<div class=\"text-xs text-amber-300\">Could not load CLI backend status.</div>";
                if (pill) pill.textContent = "Unavailable";
            });
    }

    function setProjectCodingBackend(event) {
        if (!currentProjectId) return;
        var sel = event && event.currentTarget ? event.currentTarget : document.getElementById("detail-coding-backend");
        if (!sel) return;
        var backend = (sel.value || "pi").trim();
        var detailSel = document.getElementById("detail-coding-backend");
        var terminalSel = document.getElementById("terminal-backend-select");
        if (detailSel) detailSel.value = backend;
        if (terminalSel) terminalSel.value = backend;
        loadCliModels(backend);
        apiFetch("/api/projects/" + currentProjectId + "/coding-backend", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ coding_backend: backend })
        }).then(function(resp) {
            if (resp && resp.success) {
                showSnackbar("Coding backend set to " + sel.options[sel.selectedIndex].textContent, "success");
                loadProjectCliBackends(currentProjectId, backend);
                loadCodexSync(currentProjectId);
                destroyTerminal();
                var cliTabEl = document.getElementById("tab-cli");
                if (cliTabEl && !cliTabEl.classList.contains("hidden")) initTerminal();
            } else {
                showSnackbar("Failed to set coding backend", "error");
            }
        }).catch(function() {
            showSnackbar("Failed to set coding backend", "error");
        });
    }

    function renderCodexSync(data) {
        var panel = document.getElementById("codex-sync-panel");
        var statusEl = document.getElementById("codex-sync-status");
        var btn = document.getElementById("codex-sync-btn");
        if (!panel || !statusEl || !btn) return;
        panel.classList.remove("hidden");
        var backend = data.backend || {};
        var plugin = data.plugin || {};
        var active = data.current_backend === "codex";
        var ready = !!data.sync_ready;
        var folderText = data.folder_exists ? "project folder found" : "project folder missing";
        var pluginText = plugin.available ? "plugin scaffold found" : "plugin scaffold not found";
        var backendText = backend.ready ? "Codex CLI ready" : (backend.message || "Codex CLI needs setup");
        statusEl.textContent = backendText + " · " + folderText + " · " + pluginText;
        statusEl.className = "text-xs mt-1 " + (ready ? "text-green-300" : "text-amber-200");
        btn.textContent = "Use Codex";
        btn.disabled = false;
        btn.className = active
            ? "hidden"
            : "px-3 py-1.5 text-sm rounded border border-white/20 text-gray-200 hover:bg-white/10";
        btn.title = data.message || "";
    }

    function loadCodexSync(projectId) {
        var panel = document.getElementById("codex-sync-panel");
        var statusEl = document.getElementById("codex-sync-status");
        if (panel) panel.classList.remove("hidden");
        if (statusEl) statusEl.textContent = "Checking Codex project state...";
        if (!projectId) return;
        apiFetch("/api/projects/" + projectId + "/codex-sync")
            .then(renderCodexSync)
            .catch(function() {
                if (statusEl) {
                    statusEl.textContent = "Could not check Codex integration.";
                    statusEl.className = "text-xs text-amber-200 mt-1";
                }
            });
    }

    function syncProjectToCodex() {
        if (!currentProjectId) return;
        var btn = document.getElementById("codex-sync-btn");
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Syncing...";
        }
        apiFetch("/api/projects/" + currentProjectId + "/codex-sync", { method: "POST" })
            .then(function(resp) {
                if (resp && resp.success) {
                    showSnackbar("Project set to Codex CLI", "success");
                    loadCliModels("codex");
                    loadProjectCliBackends(currentProjectId, "codex");
                    loadCodexSync(currentProjectId);
                    destroyTerminal();
                    var cliTabEl = document.getElementById("tab-cli");
                    if (cliTabEl && !cliTabEl.classList.contains("hidden")) initTerminal();
                } else {
                    showSnackbar("Could not sync project to Codex", "error");
                    loadCodexSync(currentProjectId);
                }
            })
            .catch(function() {
                showSnackbar("Could not sync project to Codex", "error");
                loadCodexSync(currentProjectId);
            });
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

    function init() {
        var listEl = document.getElementById("projects-list");
        if (!listEl) return;
        bindProjectListKeyboard();

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

        // Startup terminal buttons
        document.getElementById("startup-start-btn")?.addEventListener("click", startStartupTerminals);
        document.getElementById("startup-terminate-all-btn")?.addEventListener("click", terminateAllStartupTerminals);

        var detailBoard = document.getElementById("detail-board");
        if (detailBoard) {
            detailBoard.addEventListener("change", function() {
                updateProjectBoardGoto();
                updateStartupTimeTrackerVisibilityFromBoardSelect();
            });
        }

        var codingBackend = document.getElementById("detail-coding-backend");
        if (codingBackend) codingBackend.addEventListener("change", setProjectCodingBackend);
        var terminalCodingBackend = document.getElementById("terminal-backend-select");
        if (terminalCodingBackend) terminalCodingBackend.addEventListener("change", setProjectCodingBackend);
        var codexSyncBtn = document.getElementById("codex-sync-btn");
        if (codexSyncBtn) codexSyncBtn.addEventListener("click", syncProjectToCodex);

        document.getElementById("project-update").addEventListener("click", saveProject);
        document.getElementById("project-use").addEventListener("click", useProject);
        document.getElementById("project-remove").addEventListener("click", function() {
            if (!currentProjectId) return;
            var name = document.getElementById("project-detail-title").textContent || "this project";
            window.DecisionsAPI.confirm({
                title: "Remove project",
                message: "Remove project \"" + name + "\"? This cannot be undone and will remove board data for this project.",
                confirmLabel: "Remove",
                danger: true,
                onConfirm: function() {
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
                }
            });
        });

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

        // Load CLI models into dropdown
        loadCliModels();
        wireCliPreflightPanel();

        // Shell terminal tab
        var shellRestartBtn = document.getElementById("shell-terminal-restart");
        if (shellRestartBtn) shellRestartBtn.addEventListener("click", function() { restartShellTerminal(); });
        var shellCommandInput = document.getElementById("shell-terminal-command-input");
        if (shellCommandInput) shellCommandInput.addEventListener("keydown", function(e) {
            var filtered = getFilteredShellCommands();
            var idx = filtered.indexOf(_shellBadgeSelectedCommand);
            if (e.key === "ArrowDown") {
                e.preventDefault();
                if (!filtered.length) return;
                if (idx < 0) _shellBadgeSelectedCommand = filtered[0];
                else _shellBadgeSelectedCommand = filtered[Math.min(filtered.length - 1, idx + 1)];
                renderShellCommandBadges();
                return;
            }
            if (e.key === "ArrowUp") {
                e.preventDefault();
                if (!filtered.length) return;
                if (idx < 0) _shellBadgeSelectedCommand = filtered[filtered.length - 1];
                else _shellBadgeSelectedCommand = filtered[Math.max(0, idx - 1)];
                renderShellCommandBadges();
                return;
            }
            if (e.key === "Escape") {
                e.preventDefault();
                _shellBadgeSelectedCommand = "";
                renderShellCommandBadges();
                shellCommandInput.focus();
                return;
            }
            if (e.key === "Enter") {
                e.preventDefault();
                var cmd = shellCommandInput.value.trim();
                if (_shellBadgeSelectedCommand) {
                    sendShellCommand(_shellBadgeSelectedCommand);
                    return;
                }
                if (!cmd) return;
                addShellCommandBadge(cmd);
                sendShellCommand(cmd);
                shellCommandInput.value = "";
                _shellBadgeSearchQuery = "";
                _shellBadgeSelectedCommand = "";
                renderShellCommandBadges();
                return;
            }
            setTimeout(function() {
                _shellBadgeSearchQuery = (shellCommandInput.value || "").trim().toLowerCase();
                _shellBadgeSelectedCommand = "";
                renderShellCommandBadges();
            }, 0);
        });
        renderShellCommandBadges();
        window.addEventListener("resize", function() {
            var tabEl = document.getElementById("tab-terminal");
            if (tabEl && !tabEl.classList.contains("hidden")) scheduleShellResize();
        });
    }

    // ?? Terminal Management (pi RPC mode) ???????????????????????????????

    var _termWs = null;     // WebSocket connection to pi RPC
    var _termWsProjectId = null;  // Which project the WS is connected to
    var _termPollTimer = null;
    var _termTranscript = [];  // Array of {type, text, tool?, ts}
    var _currentAssistantEl = null;  // Current streaming assistant message element
    var _currentAssistantText = "";  // Current streaming text buffer
    var _termAgentRunning = false;   // Is pi currently processing?
    var _termActivityHideTimer = null;
    var _cliPreflightOk = true;      // Last preflight result for Pi CLI
    var _cliPreflightDismissed = false;
    var _lastPreflightPayload = null;

    function _checkLabel(id) {
        var labels = {
            pi_binary: "Pi installed",
            project_folder: "Project folder",
            model_configured: "Model selected",
            ollama_running: "Ollama running",
            ollama_model_installed: "Model installed locally",
            ollama_model_probe: "Model responds",
            provider: "Provider"
        };
        return labels[id] || id.replace(/_/g, " ");
    }

    function _applyCliModelFromPreflight(model, provider) {
        if (!model) return Promise.resolve(false);
        return apiFetch("/api/projects/cli-model", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project_id: currentProjectId,
                backend_id: "pi",
                model: model,
                provider: provider || "ollama"
            })
        }).then(function(resp) {
            if (resp.success) {
                showSnackbar("Switched CLI model to " + model, "success");
                loadCliModels();
                return refreshCliPreflight();
            }
            var err = resp.error || (resp.preflight && resp.preflight.user_message) || "Could not set model";
            showSnackbar(err, "error", { duration: 14000 });
            if (resp.preflight) renderCliPreflightPanel(resp.preflight);
            return false;
        });
    }

    function _runPreflightFix(fix) {
        if (!fix || !fix.action) return;
        var payload = fix.payload || {};
        if (fix.action === "use_model") {
            _applyCliModelFromPreflight(payload.model, payload.provider);
            return;
        }
        if (fix.action === "open_url" && payload.url) {
            window.open(payload.url, "_blank", "noopener,noreferrer");
            return;
        }
        if (fix.action === "copy_command" && payload.command) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(payload.command).then(function() {
                    showSnackbar("Copied: " + payload.command, "success");
                }).catch(function() {
                    showSnackbar(payload.command, "info", { duration: 12000 });
                });
            } else {
                showSnackbar(payload.command, "info", { duration: 12000 });
            }
            return;
        }
        if (fix.action === "focus_model") {
            var sel = document.getElementById("terminal-model-select");
            if (sel) {
                sel.focus();
                sel.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
            return;
        }
        if (fix.action === "recheck") {
            _cliPreflightDismissed = false;
            refreshCliPreflight();
        }
    }

    function renderCliPreflightPanel(pf) {
        _lastPreflightPayload = pf || null;
        var panel = document.getElementById("terminal-preflight-panel");
        var okBar = document.getElementById("terminal-preflight-ok");
        var sendBtn = document.getElementById("terminal-input-send");
        var termInput = document.getElementById("terminal-input");
        if (!panel || !okBar) return;

        _cliPreflightOk = !!(pf && pf.ok);
        var isPi = activeCodingBackend() === "pi";

        if (!isPi) {
            panel.classList.add("hidden");
            okBar.classList.add("hidden");
            if (sendBtn) sendBtn.disabled = false;
            if (termInput) termInput.disabled = false;
            return;
        }

        if (_cliPreflightOk) {
            panel.classList.add("hidden");
            okBar.classList.remove("hidden");
            var okText = document.getElementById("terminal-preflight-ok-text");
            if (okText) {
                var modelLine = pf && pf.model ? (pf.provider || "ollama") + "/" + pf.model : "";
                okText.textContent = modelLine ? ("CLI ready — " + modelLine) : "CLI ready — you can send prompts.";
            }
            if (sendBtn) sendBtn.disabled = false;
            if (termInput) termInput.disabled = false;
            return;
        }

        okBar.classList.add("hidden");
        if (_cliPreflightDismissed) {
            panel.classList.add("hidden");
        } else {
            panel.classList.remove("hidden");
        }
        if (sendBtn) sendBtn.disabled = true;
        if (termInput) termInput.disabled = true;

        var title = document.getElementById("terminal-preflight-title");
        if (title) title.textContent = "Fix CLI before sending prompts";
        var summary = document.getElementById("terminal-preflight-summary");
        if (summary) summary.textContent = (pf && pf.user_message) ? pf.user_message : "Something is blocking the coding agent.";

        var checksEl = document.getElementById("terminal-preflight-checks");
        if (checksEl) {
            checksEl.innerHTML = "";
            (pf.checks || []).forEach(function(c) {
                var li = document.createElement("li");
                li.className = "flex gap-2 items-start";
                var icon = c.ok ? "\u2713" : "\u2717";
                var color = c.ok ? "text-emerald-400" : "text-red-300";
                li.innerHTML = "<span class=\"" + color + " shrink-0\">" + icon + "</span><span>" + _checkLabel(c.id) + ": " + (c.message || "") + "</span>";
                checksEl.appendChild(li);
            });
        }

        var actionsEl = document.getElementById("terminal-preflight-actions");
        if (actionsEl) {
            actionsEl.innerHTML = "";
            (pf.fixes || []).forEach(function(fix) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "text-xs px-2.5 py-1.5 rounded border border-amber-400/50 text-amber-50 bg-amber-900/40 hover:bg-amber-800/60";
                btn.textContent = fix.label || "Fix";
                btn.addEventListener("click", function() { _runPreflightFix(fix); });
                actionsEl.appendChild(btn);
            });
        }
    }

    function wireCliPreflightPanel() {
        var dismiss = document.getElementById("terminal-preflight-dismiss");
        if (dismiss && !dismiss.dataset.wired) {
            dismiss.dataset.wired = "1";
            dismiss.addEventListener("click", function() {
                _cliPreflightDismissed = true;
                var panel = document.getElementById("terminal-preflight-panel");
                if (panel) panel.classList.add("hidden");
            });
        }
        var recheckOk = document.getElementById("terminal-preflight-recheck-ok");
        if (recheckOk && !recheckOk.dataset.wired) {
            recheckOk.dataset.wired = "1";
            recheckOk.addEventListener("click", function() {
                _cliPreflightDismissed = false;
                refreshCliPreflight();
            });
        }
    }

    function _applyCliPreflight(pf, opts) {
        opts = opts || {};
        renderCliPreflightPanel(pf);
        if (pf && pf.ok) return;
        var msg = (pf && pf.user_message) ? pf.user_message : "CLI is not ready. Check the coding model.";
        if (!opts.silentSnackbar) {
            showSnackbar(msg, "error", { duration: 14000 });
        }
        if (opts.appendTranscript) {
            var transcript = document.getElementById("terminal-transcript");
            if (transcript) {
                appendTranscriptLine(transcript, "error", msg);
                scrollTranscript();
            }
        }
        setTerminalActivity("done");
        _termAgentRunning = false;
    }

    function refreshCliPreflight() {
        if (!currentProjectId || activeCodingBackend() !== "pi") {
            _cliPreflightOk = true;
            return Promise.resolve(true);
        }
        wireCliPreflightPanel();
        return apiFetch("/api/projects/" + currentProjectId + "/cli/preflight?probe=true")
            .then(function(pf) {
                _applyCliPreflight(pf, { silentSnackbar: true });
                return !!pf.ok;
            })
            .catch(function() {
                return true;
            });
    }

    function setLastSubmittedQuestionDisplay(text) {
        var el = document.getElementById("terminal-last-question-text");
        var wrap = document.getElementById("terminal-last-question-wrap");
        if (!el) return;
        var s = (text || "").trim();
        if (!s) {
            el.textContent = "";
            if (wrap) wrap.classList.add("hidden");
            return;
        }
        el.textContent = s;
        if (wrap) wrap.classList.remove("hidden");
    }

    function collapseOpenCalloutsInTranscript(transcript) {
        if (!transcript) return;
        transcript.querySelectorAll("details.transcript-callout").forEach(function(d) {
            d.open = false;
        });
    }

    /** Only the last completed assistant outcome is "Final outcome"; earlier ones are "Response". Streaming stays "Response". */
    function syncOutcomeCalloutTitles(transcript) {
        if (!transcript) return;
        var list = Array.prototype.slice.call(transcript.querySelectorAll("details.transcript-callout.outcome"));
        var completed = list.filter(function(d) {
            var chip = d.querySelector(".transcript-chip");
            return chip && !chip.classList.contains("transcript-chip-live");
        });
        var finalEl = completed.length ? completed[completed.length - 1] : null;
        list.forEach(function(d) {
            var title = d.querySelector(".transcript-callout-title");
            var chip = d.querySelector(".transcript-chip");
            if (!title || !chip) return;
            if (chip.classList.contains("transcript-chip-live")) {
                title.textContent = "Response";
                return;
            }
            title.textContent = d === finalEl ? "Final outcome" : "Response";
        });
    }

    function bufferEntryUserText(entry) {
        if (!entry || (entry.role || "") !== "user" || !entry.content) return "";
        var content = entry.content;
        if (typeof content === "string") return content;
        if (Array.isArray(content)) {
            return content.filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
        }
        return "";
    }

    function initTerminal() {
        if (!currentProjectId) return;
        // If already connected to the SAME project, just ensure the WS is open
        if (_termWs && _termWsProjectId === currentProjectId) {
            if (_termWs.readyState !== WebSocket.OPEN && _termWs.readyState !== WebSocket.CONNECTING) {
                connectTerminalWs();
            }
            return;
        }
        // Different project (or first connection) � destroy old and reconnect
        destroyTerminal();
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) transcript.innerHTML = "";
        _clearTerminalState();
        _termTranscript = [];
        setLastSubmittedQuestionDisplay("");
        _cliPreflightDismissed = false;
        _cliPreflightOk = true;
        wireCliPreflightPanel();
        connectTerminalWs();
    }

    function setTerminalActivity(state) {
        var pill = document.getElementById("terminal-activity");
        var spinner = document.getElementById("terminal-activity-spinner");
        var text = document.getElementById("terminal-activity-text");
        if (!pill || !spinner || !text) return;
        if (_termActivityHideTimer) {
            clearTimeout(_termActivityHideTimer);
            _termActivityHideTimer = null;
        }
        if (state === "running") {
            pill.classList.remove("hidden");
            pill.classList.add("flex");
            spinner.classList.remove("hidden");
            text.textContent = "Working...";
            pill.className = "flex w-full min-h-9 items-center justify-center gap-1.5 text-xs px-2.5 py-2 rounded-md border border-orange-400/45 text-orange-100 bg-orange-500/15 box-border leading-none";
            return;
        }
        if (state === "done") {
            pill.classList.remove("hidden");
            pill.classList.add("flex");
            spinner.classList.add("hidden");
            text.textContent = "Work complete";
            pill.className = "flex w-full min-h-9 items-center justify-center gap-1.5 text-xs px-2.5 py-2 rounded-md border border-white/25 text-[#0d1117] bg-white/95 font-medium box-border leading-none";
            _termActivityHideTimer = setTimeout(function() {
                pill.classList.add("hidden");
                pill.classList.remove("flex");
            }, 8000);
            return;
        }
        pill.classList.add("hidden");
        pill.classList.remove("flex");
        spinner.classList.add("hidden");
        text.textContent = "Idle";
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
                transcript.innerHTML = '<div class="transcript-msg system">Ready — type a prompt to start</div>';
            }
            if (activeCodingBackend() === "pi") {
                refreshCliPreflight();
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
                var tabEl = document.getElementById("tab-cli");
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
            // ?? Connection ??
            case "connected":
                updateTerminalStatus("connected");
                _clearTerminalState();
                transcript.innerHTML = "";
                var lastReplayUser = "";
                if (msg.buffer && msg.buffer.length) {
                    msg.buffer.forEach(function(entry) {
                        var ut = bufferEntryUserText(entry);
                        if (ut) lastReplayUser = ut;
                        _renderBufferMessage(transcript, entry);
                    });
                }
                setLastSubmittedQuestionDisplay(lastReplayUser);
                syncOutcomeCalloutTitles(transcript);
                break;

            // ?? Agent lifecycle ??
            case "agent_start":
                _termAgentRunning = true;
                setTerminalActivity("running");
                collapseOpenCalloutsInTranscript(transcript);
                break;
            case "agent_end":
                _termAgentRunning = false;
                setTerminalActivity("done");
                _clearTerminalState();
                var sep = document.createElement("div");
                sep.className = "transcript-separator";
                transcript.appendChild(sep);
                scrollTranscript();
                break;

            // ?? Turn lifecycle � no visual output ??
            case "turn_start":
                setTerminalActivity("running");
                break;
            case "turn_end":
                if (!_termAgentRunning) setTerminalActivity("done");
                break;

            // ?? Message streaming (the main event) ??
            case "message_update": {
                var evt = msg.assistantMessageEvent;
                if (!evt) break;
                switch (evt.type) {
                    case "start":
                        // New assistant message beginning
                        _currentAssistantText = "";
                        if (!_currentAssistantEl) startAssistantMessage(transcript);
                        break;
                    case "text_start":
                        // Text content block starting
                        endThinkingCallout();
                        if (!_currentAssistantEl) startAssistantMessage(transcript);
                        break;
                    case "text_delta":
                        _currentAssistantText += (evt.delta || "");
                        scheduleAssistantRender(transcript);
                        break;
                    case "text_end":
                        // Text block complete � already shown
                        break;
                    case "thinking_start":
                        startThinkingCallout(transcript);
                        break;
                    case "thinking_delta":
                        updateThinkingCallout(transcript, evt.delta || "");
                        break;
                    case "thinking_end":
                        endThinkingCallout();
                        break;
                    case "toolcall_start":
                        // LLM decided to call a tool � DON'T render here.
                        // tool_execution_start will fire with the real args and name.
                        // Just finalize any in-progress assistant text.
                        if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                        break;
                    case "toolcall_delta":
                        // Argument streaming � skip
                        break;
                    case "toolcall_end":
                        // Tool call object fully resolved � skip, tool_execution_start shows it
                        break;
                    case "done":
                        endThinkingCallout();
                        if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                        break;
                    case "error":
                        appendTranscriptLine(transcript, "error", evt.reason || "Error");
                        break;
                }
                break;
            }

            // ?? Message start/end ??
            case "message_start": {
                var m = msg.message || {};
                if (m.role === "assistant") {
                    // message_update:start handles assistant callout creation to avoid duplication
                } else if (m.role === "user") {
                    // Show user messages that came from the backend (e.g., agent-sent prompts)
                    // Check if we already rendered this locally (from sendTerminalPrompt)
                    var content = m.content || "";
                    if (typeof content === "string") content = content;
                    else if (Array.isArray(content)) content = content.filter(function(b){ return b.type === "text"; }).map(function(b){ return b.text; }).join("");
                    if (content) {
                        // Check if we already rendered this exact prompt locally
                        var existing = transcript.querySelector('[data-prompt-text="' + content.substring(0, 100).replace(/"/g, '&quot;') + '"]');
                        if (!existing) {
                            appendTranscriptLine(transcript, "user", content);
                            scrollTranscript();
                        }
                    }
                }
                break;
            }
            case "message_end": {
                var m = msg.message || {};
                endThinkingCallout();
                if (m.role === "assistant") {
                    if (m.stopReason === "error" || m.errorMessage) {
                        var errText = m.errorMessage || "The model returned an error.";
                        appendTranscriptLine(transcript, "error", errText);
                        _termAgentRunning = false;
                        setTerminalActivity("done");
                        _clearTerminalState();
                        _cliPreflightOk = false;
                    } else if (_currentAssistantEl) {
                        finalizeAssistantMessage(transcript);
                    }
                }
                break;
            }

            case "preflight":
                _applyCliPreflight(msg, { silentSnackbar: true });
                break;

            // ?? Tool execution (the real tool call) ??
            case "tool_execution_start": {
                if (_currentAssistantEl) finalizeAssistantMessage(transcript);
                endThinkingCallout();
                var tName = msg.toolName || "tool";
                var tArgs = msg.args || {};
                var argsStr = "";
                try { argsStr = JSON.stringify(tArgs); } catch(e) { argsStr = String(tArgs); }
                startToolCallout(transcript, msg.toolCallId, tName + " " + _truncateArgs(argsStr, 200));
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
                var isErr = msg.isError === true;
                if (result.content && Array.isArray(result.content)) {
                    rText = result.content.filter(function(b){ return b.type === "text"; }).map(function(b){ return b.text; }).join("\n");
                }
                if (rText) {
                    updateOrAppendToolResult(transcript, msg.toolCallId || "_", rText, isErr);
                }
                completeToolCallout(msg.toolCallId || "_", isErr);
                break;
            }

            // ?? Compaction ??
            case "compaction_start":
                appendTranscriptLine(transcript, "system", "Compacting context...");
                break;
            case "compaction_end":
                appendTranscriptLine(transcript, "system", "Context compacted");
                break;

            // ?? Auto-retry ??
            case "auto_retry_start":
            case "auto_retry_end":
                break;

            // ?? Queue updates ??
            case "queue_update":
                break;

            // ?? Extension UI ??
            case "extension_ui_request":
                // Fire-and-forget notifications like setStatus, notify
                var method = msg.method || "";
                if (method === "notify") {
                    appendTranscriptLine(transcript, "system", msg.message || "");
                } else if (method === "setStatus") {
                    // Status bar text � skip
                }
                break;
            case "extension_error":
                appendTranscriptLine(transcript, "error", msg.error || "Extension error");
                break;

            // ?? RPC responses ??
            case "response":
                // Command responses (prompt, steer, abort, etc.) � not rendered
                break;

            // ?? Error from backend ??
            case "error":
                appendTranscriptLine(transcript, "error", msg.message || "Unknown error");
                updateTerminalStatus("error");
                setTerminalActivity("done");
                _termAgentRunning = false;
                _clearTerminalState();
                if (msg.preflight && msg.preflight.ok === false) {
                    _cliPreflightDismissed = false;
                    renderCliPreflightPanel(msg.preflight);
                }
                break;

            // ?? Keepalives ??
            case "pong":
            case "ping":
                break;

            // ?? Unknown � suppress, don't render JSON ??
            default:
                break;
        }
    }

    var _toolResultEls = {};  // toolCallId -> DOM element for streaming tool output
    var _toolCalloutEls = {}; // toolCallId -> <details> callout element
    var _thinkingCallout = null; // <details> for current thinking stream

    // ── Streaming performance: debounce rAF ──────────────────────────────
    var _renderRafPending = false;   // rAF scheduled?
    var _scrollRafPending = false;   // scroll rAF scheduled?

    /** Schedule a scroll on next animation frame (deduplicates rapid calls). */
    function scheduleScrollTranscript() {
        if (_scrollRafPending) return;
        _scrollRafPending = true;
        requestAnimationFrame(function() {
            _scrollRafPending = false;
            var container = document.getElementById("terminal-container");
            if (container) container.scrollTop = container.scrollHeight;
        });
    }

    /**
     * Re-render the streaming assistant message using requestAnimationFrame batching.
     * Instead of innerHTML on every text_delta, we schedule at most one render per frame.
     */
    function scheduleAssistantRender(transcript) {
        if (_renderRafPending) return;   // already scheduled — will use latest text
        _renderRafPending = true;
        requestAnimationFrame(function() {
            _renderRafPending = false;
            if (!_currentAssistantEl) return;
            // Full markdown re-render only once per frame, with latest accumulated text
            var text = _currentAssistantText;
            _currentAssistantEl.innerHTML = renderMarkdownLite(text);
        });
        scheduleScrollTranscript();
    }

    function _clearTerminalState() {
        _currentAssistantEl = null;
        _currentAssistantText = "";
        _toolResultEls = {};
        _toolCalloutEls = {};
        _thinkingCallout = null;
        _renderRafPending = false;
        _scrollRafPending = false;
        _lastRenderedText = "";
        _lastRenderedHtml = "";
    }

    function startThinkingCallout(transcript) {
        if (_thinkingCallout) return _thinkingCallout;
        var details = document.createElement("details");
        details.className = "transcript-callout thinking";
        details.open = true;
        details.innerHTML = "<summary><span class=\"transcript-callout-title\">Thinking</span><span class=\"transcript-chip transcript-chip-live\">Running</span></summary><div class=\"transcript-callout-body\"></div>";
        transcript.appendChild(details);
        _thinkingCallout = details;
        scheduleScrollTranscript();
        return details;
    }

    var _thinkingRafPending = false;

    function updateThinkingCallout(transcript, deltaText) {
        if (!_thinkingCallout) startThinkingCallout(transcript);
        var body = _thinkingCallout.querySelector(".transcript-callout-body");
        if (!body) return;
        body.textContent += (deltaText || "");
        // Throttle scroll
        if (!_thinkingRafPending) {
            _thinkingRafPending = true;
            requestAnimationFrame(function() {
                _thinkingRafPending = false;
                if (_thinkingCallout) _thinkingCallout.scrollTop = _thinkingCallout.scrollHeight;
                scheduleScrollTranscript();
            });
        }
    }

    function endThinkingCallout() {
        if (_thinkingCallout) {
            var chip = _thinkingCallout.querySelector(".transcript-chip");
            if (chip) {
                chip.className = "transcript-chip transcript-chip-done";
                chip.textContent = "Done";
            }
            _thinkingCallout.open = false;
            _thinkingCallout = null;
        }
    }

    function startToolCallout(transcript, toolCallId, titleText) {
        var key = toolCallId || ("tool_" + Date.now() + "_" + Math.random());
        var details = document.createElement("details");
        details.className = "transcript-callout tooling";
        details.open = true;
        details.setAttribute("data-tool-call-id", key);
        details.innerHTML = "<summary><span class=\"transcript-callout-title\">Tooling: " + escapeAttr(titleText || "tool") + "</span><span class=\"transcript-chip transcript-chip-live\">Running</span></summary><div class=\"transcript-callout-body\"></div>";
        transcript.appendChild(details);
        var body = details.querySelector(".transcript-callout-body");
        _toolResultEls[key] = body || details;
        _toolCalloutEls[key] = details;
        scheduleScrollTranscript();
        return key;
    }

    function completeToolCallout(toolCallId, isError) {
        var key = toolCallId || "_";
        var details = _toolCalloutEls[key];
        if (!details) return;
        var chip = details.querySelector(".transcript-chip");
        if (chip) {
            if (isError) {
                chip.className = "transcript-chip transcript-chip-error";
                chip.textContent = "Error";
            } else {
                chip.className = "transcript-chip transcript-chip-done";
                chip.textContent = "Done";
            }
        }
        details.open = false;
    }

    function startAssistantMessage(transcript) {
        var details = document.createElement("details");
        details.className = "transcript-callout outcome";
        details.open = true;
        details.innerHTML = "<summary><span class=\"transcript-callout-title\">Response</span><span class=\"transcript-chip transcript-chip-live\">Streaming</span></summary><div class=\"transcript-callout-body transcript-msg assistant streaming\"></div>";
        transcript.appendChild(details);
        var body = details.querySelector(".transcript-callout-body");
        if (body) body.innerHTML = "";
        _currentAssistantEl = body || details;
        syncOutcomeCalloutTitles(transcript);
        scheduleScrollTranscript();
    }

    function updateAssistantMessage(transcript, text) {
        if (!_currentAssistantEl) {
            startAssistantMessage(transcript);
        }
        // Direct render — only called from finalize or low-frequency paths now.
        // Streaming deltas use scheduleAssistantRender instead.
        _currentAssistantEl.innerHTML = renderMarkdownLite(text);
        scheduleScrollTranscript();
    }

    function finalizeAssistantMessage(transcript) {
        if (_currentAssistantEl) {
            _currentAssistantEl.classList.remove("streaming");
            // Remove streaming cursor if present
            var cursor = _currentAssistantEl.querySelector(".streaming-cursor");
            if (cursor) cursor.remove();
            var details = _currentAssistantEl.closest("details.transcript-callout.outcome");
            if (details) {
                var finalText = (_currentAssistantEl.textContent || "").trim();
                if (!finalText) {
                    details.remove();
                    _currentAssistantText = "";
                    _currentAssistantEl = null;
                    syncOutcomeCalloutTitles(transcript);
                    return;
                }
                var chip = details.querySelector(".transcript-chip");
                if (chip) {
                    chip.className = "transcript-chip transcript-chip-done";
                    chip.textContent = "Done";
                }
                details.open = true;
                syncOutcomeCalloutTitles(transcript);
            }
        }
        _currentAssistantText = "";
        _currentAssistantEl = null;
    }

    function appendAssistantOutcomeCallout(transcript, text) {
        if (!text || !String(text).trim()) return;
        var details = document.createElement("details");
        details.className = "transcript-callout outcome";
        details.open = true;
        details.innerHTML = "<summary><span class=\"transcript-callout-title\">Response</span><span class=\"transcript-chip transcript-chip-done\">Done</span></summary><div class=\"transcript-callout-body transcript-msg assistant\"></div>";
        var body = details.querySelector(".transcript-callout-body");
        if (body) body.innerHTML = renderMarkdownLiteNoCursor(text || "");
        transcript.appendChild(details);
        syncOutcomeCalloutTitles(transcript);
        scrollTranscript();
    }

    function appendTranscriptLine(transcript, type, text) {
        var el = document.createElement("div");
        el.className = "transcript-msg " + escapeAttr(type);
        if (type === "user") el.classList.add("transcript-msg-user-archived");
        // Tool calls and results may contain long text � preserve whitespace
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
            scheduleScrollTranscript();
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

    var _lastRenderedText = "";
    var _lastRenderedHtml = "";

    function renderMarkdownLite(text) {
        if (!text) return "";
        if (text === _lastRenderedText) return _lastRenderedHtml;
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
        s += "<span class=\"streaming-cursor\">?</span>";
        _lastRenderedText = text;
        _lastRenderedHtml = s;
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
            appendAssistantOutcomeCallout(transcript, entry.content);
        } else if (role === "tool_result") {
            var toolName = entry.tool_name || "tool";
            var toolId = "replay_" + toolName + "_" + Date.now() + "_" + Math.floor(Math.random() * 100000);
            startToolCallout(transcript, toolId, toolName);
            updateOrAppendToolResult(transcript, toolId, entry.tool_result || "", !!entry.is_error);
            completeToolCallout(toolId, !!entry.is_error);
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
                    if (transcript) transcript.innerHTML = "";
                    _clearTerminalState();
                    _termTranscript = [];
                    setLastSubmittedQuestionDisplay("");
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

    // ?? CLI Model Dropdown ???????????????????????????????????????????

    var _cliModelsRequestId = 0;

    function loadCliModels(backend) {
        var sel = document.getElementById("terminal-model-select");
        if (!sel) return;
        backend = (backend || activeCodingBackend() || "pi").trim();
        var params = new URLSearchParams({ backend_id: backend });
        if (currentProjectId) params.set("project_id", String(currentProjectId));
        var requestId = ++_cliModelsRequestId;
        sel.innerHTML = '<option value="">Loading models...</option>';

        apiFetch("/api/projects/cli-models?" + params.toString())
            .then(function(data) {
                if (requestId !== _cliModelsRequestId) return;
                sel.innerHTML = "";
                var models = data.models || [];
                var current = data.current_model || "";
                var currentProvider = data.current_provider || "";
                if (!models.length) {
                    var opt = document.createElement("option");
                    opt.value = "";
                    opt.textContent = data.message || current || "No models reported by this CLI";
                    sel.appendChild(opt);
                    return;
                }
                // Group by provider
                var groups = {};
                models.forEach(function(m) {
                    var p = m.provider || "other";
                    if (!groups[p]) groups[p] = [];
                    groups[p].push(m);
                });
                Object.keys(groups).sort().forEach(function(prov) {
                    var og = document.createElement("optgroup");
                    og.label = prov.charAt(0).toUpperCase() + prov.slice(1);
                    groups[prov].forEach(function(m) {
                        var opt = document.createElement("option");
                        opt.value = m.id;
                        opt.dataset.provider = m.provider || "";
                        opt.textContent = m.name || m.id;
                        if (m.id === current) opt.selected = true;
                        og.appendChild(opt);
                    });
                    sel.appendChild(og);
                });
                // If current not in list, add it
                if (current && !models.some(function(m) { return m.id === current; })) {
                    var opt = document.createElement("option");
                    opt.value = current; opt.dataset.provider = currentProvider; opt.textContent = current + " (current)"; opt.selected = true;
                    sel.insertBefore(opt, sel.firstChild);
                }
            })
            .catch(function() {
                if (requestId !== _cliModelsRequestId) return;
                sel.innerHTML = '<option value="">Failed to load</option>';
            });

        sel.onchange = function() {
            var opt = sel.options[sel.selectedIndex];
            var model = sel.value;
            var provider = opt ? (opt.dataset.provider || "") : "";
            if (!model) return;
            apiFetch("/api/projects/cli-model", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: currentProjectId,
                    backend_id: activeCodingBackend(),
                    model: model,
                    provider: provider
                })
            }).then(function(resp) {
                if (resp.success) {
                    showSnackbar("Model set to " + model, "success");
                    refreshCliPreflight();
                } else {
                    var err = resp.error || (resp.preflight && resp.preflight.user_message) || "Failed to set model";
                    showSnackbar(err, "error", { duration: 14000 });
                    if (resp.preflight) _applyCliPreflight(resp.preflight, { appendTranscript: false });
                }
            }).catch(function() {
                showSnackbar("Failed to set model", "error");
            });
        };
    }

    function sendTerminalPrompt(instruction) {
        if (!instruction || !instruction.trim()) return;
        if (activeCodingBackend() === "pi" && !_cliPreflightOk) {
            _cliPreflightDismissed = false;
            if (_lastPreflightPayload) {
                renderCliPreflightPanel(_lastPreflightPayload);
            } else {
                refreshCliPreflight();
            }
            showSnackbar("Fix the issues in the CLI setup panel above before sending.", "error");
            return;
        }
        if (!_termWs || _termWs.readyState !== WebSocket.OPEN) {
            showSnackbar("Terminal not connected � try restarting", "error");
            return;
        }
        setTerminalActivity("running");
        setLastSubmittedQuestionDisplay(instruction);
        // Show the user message immediately (pi will also echo it via message_start,
        // but we mark it so we don't duplicate)
        var transcript = document.getElementById("terminal-transcript");
        if (transcript) {
            collapseOpenCalloutsInTranscript(transcript);
            var el = appendTranscriptLine(transcript, "user", instruction);
            el.setAttribute("data-prompt-text", instruction.substring(0, 100));
        }
        // Show starting hint if pi process isn't running yet
        if (!_termAgentRunning) {
            appendTranscriptLine(transcript, "system", "Starting agent...");
        }
        var modelSel = document.getElementById("terminal-model-select");
        var model = modelSel ? (modelSel.value || "").trim() : "";
        _termWs.send(JSON.stringify({
            type: "prompt",
            message: instruction,
            model: model && model !== "auto" ? model : ""
        }));
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
        if (btn) btn.classList.add("hidden");
        // Show loading overlay
        var overlay = document.getElementById("terminal-overview-overlay");
        var overviewText = document.getElementById("terminal-overview-text");
        if (overlay) overlay.classList.remove("hidden");
        if (overviewText) overviewText.textContent = "Analyzing terminal output...";

        apiFetch("/api/projects/" + currentProjectId + "/terminal/overview", { method: "POST" })
            .then(function(data) {
                if (data.summary) {
                    if (overviewText) overviewText.textContent = data.summary;
                    showSnackbar("Overview ready � speaking aloud", "success", { duration: 15000 });
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
                    btn.classList.remove("hidden");
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

    // ── Terminal tab (interactive shell PTY) ───────────────────────────────
    var _shellWs = null;
    var _shellWsProjectId = null;
    var _shellProcessId = null;
    var _shellXterm = null;
    var _shellFitTimer = null;
    var _shellTerminalStateByProject = {};
    /** Prevents overlapping /api/projects/.../shell-terminal bootstrap calls. */
    var _shellBootstrapping = false;
    var _shellCommandsStorageKey = "projects_global_terminal_commands_v1";
    var _shellBadgeSearchQuery = "";
    var _shellBadgeSelectedCommand = "";
    var _defaultShellCommands = ["ls", "pwd", "git status", "npm run dev", "python manage.py runserver 8080"];

    function ensureShellXterm() {
        if (_shellXterm) return true;
        var TermCtor = typeof window.Terminal === "function" ? window.Terminal : (typeof window.XTerm === "function" ? window.XTerm : null);
        var host = document.getElementById("shell-terminal-xterm");
        if (!TermCtor || !host) return false;
        _shellXterm = new TermCtor({
            convertEol: true, cursorBlink: true, cursorStyle: "bar",
            fontSize: 12, lineHeight: 1.25, scrollback: 8000,
            fontFamily: "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, monospace",
            theme: { background: "#0d1117", foreground: "#e6edf3", cursor: "#f97316", selection: "rgba(249, 115, 22, 0.25)" }
        });
        host.innerHTML = "";
        _shellXterm.open(host);
        _registerLinkProvider(_shellXterm);
        _shellXterm.onData(function(data) {
            if (_shellWs && _shellWs.readyState === WebSocket.OPEN) {
                _shellWs.send(JSON.stringify({ type: "input", data: data }));
            }
        });
        scheduleShellResize();
        return true;
    }

    function scheduleShellResize() {
        if (!_shellXterm) return;
        if (_shellFitTimer) clearTimeout(_shellFitTimer);
        _shellFitTimer = setTimeout(function() {
            var host = document.getElementById("shell-terminal-xterm");
            if (!host || !_shellXterm || !_shellXterm.element) return;
            var cellW = _shellXterm._core && _shellXterm._core._renderService && _shellXterm._core._renderService.dimensions ? (_shellXterm._core._renderService.dimensions.actualCellWidth || 8) : 8;
            var cellH = _shellXterm._core && _shellXterm._core._renderService && _shellXterm._core._renderService.dimensions ? (_shellXterm._core._renderService.dimensions.actualCellHeight || 17) : 17;
            // Small safety margins prevent row clipping at the bottom edge.
            var safeWidth = Math.max(0, host.clientWidth - 10);
            var safeHeight = Math.max(0, host.clientHeight - 12);
            var cols = Math.max(20, Math.floor(safeWidth / Math.max(1, cellW)));
            var rows = Math.max(8, Math.floor(safeHeight / Math.max(1, cellH)));
            try { _shellXterm.resize(cols, rows); } catch (e) {}
            if (_shellWs && _shellWs.readyState === WebSocket.OPEN) {
                _shellWs.send(JSON.stringify({ type: "resize", cols: cols, rows: rows }));
            }
        }, 80);
    }

    function updateShellStatus(status) {
        var dot = document.getElementById("shell-terminal-status");
        var text = document.getElementById("shell-terminal-status-text");
        if (!dot || !text) return;
        if (status === "connected") { dot.className = "w-2 h-2 rounded-full bg-green-500"; text.textContent = "Connected"; text.className = "text-xs text-green-400"; return; }
        if (status === "connecting") { dot.className = "w-2 h-2 rounded-full bg-yellow-500"; text.textContent = "Connecting..."; text.className = "text-xs text-yellow-400"; return; }
        if (status === "error") { dot.className = "w-2 h-2 rounded-full bg-red-500"; text.textContent = "Error"; text.className = "text-xs text-red-400"; return; }
        dot.className = "w-2 h-2 rounded-full bg-gray-500"; text.textContent = "Disconnected"; text.className = "text-xs text-gray-400";
    }

    function killShellWs() {
        if (_shellWs) {
            _shellWs.onclose = null;
            _shellWs.onerror = null;
            try { _shellWs.close(); } catch (e) {}
            _shellWs = null;
            _shellWsProjectId = null;
        }
    }

    function connectShellWs() {
        if (!currentProjectId || !_shellProcessId) return;
        killShellWs();
        // Server replays sess._raw_buffer on every WebSocket attach; clear first so reconnects do not duplicate scrollback.
        if (_shellXterm) {
            try { _shellXterm.clear(); } catch (e) {}
        }
        var token = (window.DECISIONSAI_INTERNAL_API_TOKEN || "").trim();
        var wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        var wsUrl = wsProtocol + "//" + window.location.host + "/api/projects/startup-terminal/" + encodeURIComponent(_shellProcessId) + "/ws";
        if (token) wsUrl += "?internal_token=" + encodeURIComponent(token);

        updateShellStatus("connecting");
        _shellWs = new WebSocket(wsUrl);
        _shellWsProjectId = currentProjectId;

        _shellWs.onopen = function() {
            updateShellStatus("connected");
            scheduleShellResize();
            var shellInput = document.getElementById("shell-terminal-command-input");
            if (shellInput) shellInput.focus();
        };
        _shellWs.onmessage = function(event) {
            try {
                var msg = JSON.parse(event.data);
                if (msg.type === "output" && _shellXterm) _shellXterm.write(msg.data || "");
            } catch (e) {}
        };
        _shellWs.onclose = function() { updateShellStatus("disconnected"); };
        _shellWs.onerror = function() { updateShellStatus("error"); };
    }

    function initShellTerminal() {
        if (!currentProjectId) return;
        if (!ensureShellXterm()) {
            showSnackbar("xterm.js failed to load - refresh the page", "error");
            return;
        }
        // Already connected for this project — tab switches only need a resize/focus pass.
        if (_shellWs && _shellWs.readyState === WebSocket.OPEN && _shellWsProjectId === currentProjectId && _shellProcessId) {
            updateShellStatus("connected");
            scheduleShellResize();
            var shellInput = document.getElementById("shell-terminal-command-input");
            if (shellInput) shellInput.focus();
            return;
        }
        if (_shellBootstrapping) return;
        _shellBootstrapping = true;
        updateShellStatus("connecting");
        apiFetch("/api/projects/" + currentProjectId + "/shell-terminal")
            .then(function(data) {
                var sessions = (data && data.sessions) || [];
                if (sessions.length) {
                    _shellProcessId = sessions[0].process_id;
                    _shellTerminalStateByProject[currentProjectId] = _shellProcessId;
                    connectShellWs();
                    return;
                }
                return apiFetch("/api/projects/" + currentProjectId + "/shell-terminal/start", { method: "POST" }).then(function(resp) {
                    if (!resp || !resp.success || !resp.process_id) throw new Error((resp && resp.error) || "Failed to start shell");
                    _shellProcessId = resp.process_id;
                    _shellTerminalStateByProject[currentProjectId] = _shellProcessId;
                    connectShellWs();
                });
            })
            .catch(function(err) {
                updateShellStatus("error");
                if (_shellXterm) _shellXterm.writeln("\x1b[31mFailed to start shell: " + (err && err.message ? err.message : String(err)) + "\x1b[0m");
            })
            .finally(function() {
                _shellBootstrapping = false;
            });
    }

    function destroyShellTerminal() {
        killShellWs();
        _shellProcessId = null;
        _shellBootstrapping = false;
        if (_shellXterm) {
            try { _shellXterm.clear(); } catch (e) {}
        }
        updateShellStatus("disconnected");
    }

    function restartShellTerminal() {
        if (!currentProjectId) return;
        var oldProcessId = _shellProcessId || _shellTerminalStateByProject[currentProjectId] || "";
        destroyShellTerminal();
        var p = oldProcessId
            ? apiFetch("/api/projects/kill-terminal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ process_id: oldProcessId }) })
            : Promise.resolve({ success: true });
        p.then(function() {
            _shellProcessId = null;
            delete _shellTerminalStateByProject[currentProjectId];
            if (_shellXterm) _shellXterm.clear();
            initShellTerminal();
        }).catch(function() { showSnackbar("Failed to restart terminal", "error"); });
    }

    function sendShellCommand(command) {
        var cmd = String(command || "").trim();
        if (!cmd) return;
        if (!_shellWs || _shellWs.readyState !== WebSocket.OPEN) {
            showSnackbar("Terminal not connected - try restarting", "error");
            return;
        }
        _shellWs.send(JSON.stringify({ type: "input", data: cmd + "\n" }));
    }

    function getShellCommands() {
        try {
            var raw = localStorage.getItem(_shellCommandsStorageKey);
            if (raw !== null) {
                var parsed = JSON.parse(raw);
                if (Array.isArray(parsed)) {
                    return parsed.map(function(s) { return String(s || "").trim(); }).filter(Boolean);
                }
            }
        } catch (e) {}
        return _defaultShellCommands.slice();
    }

    function saveShellCommands(commands) {
        localStorage.setItem(_shellCommandsStorageKey, JSON.stringify(commands));
    }

    function addShellCommandBadge(command) {
        var cmd = String(command || "").trim();
        if (!cmd) return;
        var commands = getShellCommands();
        if (commands.indexOf(cmd) === -1) {
            commands.push(cmd);
            saveShellCommands(commands);
        }
        renderShellCommandBadges();
    }

    function removeShellCommandBadge(command) {
        var commands = getShellCommands().filter(function(c) { return c !== command; });
        saveShellCommands(commands);
        renderShellCommandBadges();
    }

    function getFilteredShellCommands() {
        var commands = getShellCommands();
        if (!_shellBadgeSearchQuery) return commands;
        return commands.filter(function(cmd) { return cmd.toLowerCase().indexOf(_shellBadgeSearchQuery) !== -1; });
    }

    function renderShellCommandBadges() {
        var wrap = document.getElementById("shell-terminal-command-badges");
        if (!wrap) return;
        var commands = getFilteredShellCommands();
        if (!commands.length) {
            _shellBadgeSelectedCommand = "";
            wrap.innerHTML = '<span class="text-xs text-gray-500">No matching badges</span>';
            return;
        }
        if (commands.indexOf(_shellBadgeSelectedCommand) === -1) _shellBadgeSelectedCommand = "";
        wrap.innerHTML = "";
        commands.forEach(function(cmd) {
            var item = document.createElement("div");
            item.className = "inline-flex items-center gap-1";
            var selected = cmd === _shellBadgeSelectedCommand;
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = selected ? "px-2.5 py-1 rounded-full border border-[#f97316] text-white bg-[#f97316] text-xs font-mono" : "px-2.5 py-1 rounded-full border border-[#f97316]/50 text-[#f97316] bg-[#f97316]/10 hover:bg-[#f97316]/20 text-xs font-mono";
            btn.textContent = cmd;
            btn.title = "Run in terminal";
            btn.addEventListener("click", function() {
                _shellBadgeSelectedCommand = cmd;
                renderShellCommandBadges();
                sendShellCommand(cmd);
                var input = document.getElementById("shell-terminal-command-input");
                if (input) input.focus();
            });
            var removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "w-5 h-5 rounded-full border border-white/20 text-gray-400 hover:text-red-400 hover:border-red-500/50 text-[11px] leading-none";
            removeBtn.textContent = "×";
            removeBtn.title = "Remove badge";
            removeBtn.addEventListener("click", function() {
                removeShellCommandBadge(cmd);
                var input = document.getElementById("shell-terminal-command-input");
                if (input) input.focus();
            });
            item.appendChild(btn);
            item.appendChild(removeBtn);
            wrap.appendChild(item);
        });
    }

    // ?????????????????????????????????????????????????????????????????????????
    // ?? Startup Terminals (xterm.js grid) ?????????????????????????????????????
    var _startupTerminals = {};  // Map of terminalId -> { term, closeBtn, processId }
    var _nextTerminalId = 0;
    var _maxStartupTerminalOutputChars = 250000;
    // Per-project terminal persistence: projectId -> [{termId, processId, command}]
    var _projectTerminalState = {};

    // Register a URL link provider on an xterm instance so URLs are clickable.
    function _registerLinkProvider(term) {
        if (typeof term.registerLinkProvider !== "function") return;
        var urlRe = /https?:\/\/[^\s\x1b\x07\x08\x0d\x0a"'<>[\]{}|\\^`]+/g;
        term.registerLinkProvider({
            provideLinks: function(y, callback) {
                var line = term.buffer && term.buffer.active && term.buffer.active.getLine(y - 1);
                if (!line) { callback([]); return; }
                var text = line.translateToString(true);
                var links = [];
                var m;
                urlRe.lastIndex = 0;
                while ((m = urlRe.exec(text)) !== null) {
                    (function(url, startX) {
                        links.push({
                            text: url,
                            range: {
                                start: { x: startX + 1, y: y },
                                end: { x: startX + url.length, y: y }
                            },
                            activate: function() { window.open(url, "_blank"); }
                        });
                    })(m[0], m.index);
                }
                callback(links);
            }
        });
    }

    function _appendStartupTerminalOutput(termId, chunk) {
        var td = _startupTerminals[termId];
        if (!td || !chunk) return;
        td.outputBuffer = (td.outputBuffer || "") + String(chunk);
        if (td.outputBuffer.length > _maxStartupTerminalOutputChars) {
            td.outputBuffer = td.outputBuffer.slice(td.outputBuffer.length - _maxStartupTerminalOutputChars);
        }
    }

    function _copyTextToClipboard(text) {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function(resolve, reject) {
            try {
                var ta = document.createElement("textarea");
                ta.value = text;
                ta.setAttribute("readonly", "");
                ta.style.position = "fixed";
                ta.style.left = "-9999px";
                document.body.appendChild(ta);
                ta.select();
                var ok = document.execCommand("copy");
                document.body.removeChild(ta);
                if (ok) resolve();
                else reject(new Error("copy command failed"));
            } catch (err) {
                reject(err);
            }
        });
    }

    function copyStartupTerminalOutput(termId) {
        var td = _startupTerminals[termId];
        if (!td) return;
        var text = td.outputBuffer || "";
        if (!text.trim()) {
            showSnackbar("No terminal output to copy yet", "info");
            return;
        }
        _copyTextToClipboard(text).then(function() {
            showSnackbar("Copied terminal output", "success");
        }).catch(function(err) {
            console.error("Failed to copy terminal output:", err);
            showSnackbar("Failed to copy terminal output", "error");
        });
    }

    function showStartupTerminalChrome() {
        var grid = document.getElementById("startup-terminal-grid");
        var startBtn = document.getElementById("startup-start-btn");
        var termBtn = document.getElementById("startup-terminate-all-btn");
        if (grid) grid.classList.remove("hidden");
        if (startBtn) startBtn.classList.add("hidden");
        if (termBtn) termBtn.classList.remove("hidden");
    }

    function clearStartupTerminalPlaceholders() {
        var grid = document.getElementById("startup-terminal-grid");
        if (!grid) return;
        grid.querySelectorAll(".startup-terminal-card--pending").forEach(function(card) {
            card.remove();
        });
        if (!grid.querySelector(".startup-terminal-card:not(.startup-terminal-card--pending)")) {
            grid.classList.add("hidden");
        }
    }

    function renderStartupTerminalPlaceholders(commands) {
        var grid = document.getElementById("startup-terminal-grid");
        if (!grid) return;
        showStartupTerminalChrome();
        grid.innerHTML = "";
        commands.forEach(function(command, index) {
            var card = document.createElement("div");
            card.className = "startup-terminal-card startup-terminal-card--pending";
            card.dataset.pendingIndex = String(index);
            card.innerHTML = '<div class="startup-terminal-header">' +
                '<span class="startup-terminal-title" title="' + escapeAttr(command) + '">Queued for startup</span>' +
                '</div>' +
                '<div class="startup-terminal-pending-body">' +
                '<div class="startup-terminal-pending-command">' + escapeHtml(command) + '</div>' +
                '<div class="startup-terminal-pending-status">Waiting for terminal session...</div>' +
                '</div>';
            grid.appendChild(card);
        });
    }

    function refreshStartupSessionsUntilVisible(projectId, commands) {
        var attempts = 0;
        var maxAttempts = 24;
        var delay = 500;

        function poll() {
            if (!projectId || currentProjectId !== projectId) return;
            apiFetch("/api/projects/" + projectId + "/startup-sessions")
                .then(function(data) {
                    var sessions = Array.isArray(data.sessions) ? data.sessions : [];
                    var alive = sessions.filter(function(s) { return s.alive; });
                    if (alive.length) {
                        _projectTerminalState[projectId] = alive.map(function(s) {
                            return { processId: s.process_id, command: s.command, pid: s.pid || null };
                        });
                        reattachProjectTerminals(projectId);
                        switchTab("startup");
                        return;
                    }
                    attempts += 1;
                    if (attempts < maxAttempts) {
                        setTimeout(poll, delay);
                    } else if (commands && commands.length) {
                        showSnackbar("Startup terminals did not attach. Check folder path and startup commands, then try again.", "error");
                    }
                })
                .catch(function() {
                    attempts += 1;
                    if (attempts < maxAttempts) setTimeout(poll, delay);
                });
        }

        poll();
    }

    function formatStartupStartSnackbar(response, commands) {
        var count = Number(response && response.started) || (commands ? commands.length : 0);
        var failed = Number(response && response.failed) || 0;
        if (failed > 0) {
            return "Started " + count + " startup terminal" + (count === 1 ? "" : "s") + "; " + failed + " failed.";
        }
        if (count > 0) {
            return "Starting " + count + " startup terminal" + (count === 1 ? "" : "s") + ".";
        }
        return "Startup terminals queued.";
    }

    function startStartupTerminals() {
        if (!currentProjectId) {
            showSnackbar("Select a project first", "error");
            return;
        }
        var TermCtor = typeof window.Terminal === "function" ? window.Terminal : (typeof window.XTerm === "function" ? window.XTerm : null);
        if (!TermCtor) {
            showSnackbar("xterm.js failed to load � refresh the page", "error");
            return;
        }
        var startupText = document.getElementById("detail-startup")?.value || "";
        var commands = startupText.split("\n")
            .map(function(line) { return line.trim(); })
            .filter(function(line) {
                return line.length && line.charAt(0) !== "#";
            });

        if (!commands.length) {
            showSnackbar("No startup commands to run (empty or only # comments)", "info");
            return;
        }

        switchTab("startup");
        renderStartupTerminalPlaceholders(commands);

        var trackerLabel = document.getElementById("startup-time-tracker-label");
        var startTrackerEl = document.getElementById("detail-start-time-tracker");
        var includeTimeTracker = !!(
            startTrackerEl &&
            trackerLabel &&
            !trackerLabel.classList.contains("hidden") &&
            startTrackerEl.checked
        );

        apiFetch("/api/projects/" + currentProjectId + "/startup-terminals/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                commands: commands,
                startup_instructions: startupText.trim(),
                start_time_tracker: includeTimeTracker
            })
        }).then(function(response) {
            if (!response || !response.success) {
                clearStartupTerminalPlaceholders();
                showSnackbar((response && response.message) || "Failed to start startup terminals", "error");
                return;
            }
            showSnackbar(formatStartupStartSnackbar(response, commands), response.failed ? "error" : "success");
            var sessions = Array.isArray(response.sessions) ? response.sessions : [];
            if (!sessions.length && !(response && response.started > 0)) {
                clearStartupTerminalPlaceholders();
                showSnackbar((response && response.message) || "No startup terminals started", "error");
                return;
            }
            if (response.action === "already_running" && sessions.length) {
                clearStartupTerminalPlaceholders();
            }
            if (sessions.length) {
                _projectTerminalState[currentProjectId] = sessions.map(function(session) {
                    return {
                        processId: session.process_id,
                        command: session.command || "",
                        pid: session.pid || null
                    };
                });
                reattachProjectTerminals(currentProjectId);
            }
            refreshStartupSessionsUntilVisible(currentProjectId, commands);
        }).catch(function(err) {
            clearStartupTerminalPlaceholders();
            showSnackbar("Failed to start startup terminals: " + (err && err.message ? err.message : ""), "error");
        });
    }

    function createStartupTerminal(command, index, TermCtor) {
        var termId = "term-" + (_nextTerminalId++);
        var grid = document.getElementById("startup-terminal-grid");
        if (!grid) return;

        var card = document.createElement("div");
        card.className = "startup-terminal-card";
        card.dataset.termId = termId;

        card.innerHTML = '<div class="startup-terminal-header">' +
            '<span class="startup-terminal-title" title="' + escapeAttr(command) + '">PID: —</span>' +
            '<div class="startup-terminal-btns">' +
            '<button type="button" class="startup-terminal-copy" title="Copy output">' +
            '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="3" width="6" height="7" rx="1"/><path d="M2.5 8.5V2.8a.8.8 0 0 1 .8-.8h4.9"/></svg>' +
            '</button>' +
            '<button type="button" class="startup-terminal-expand" title="Expand">' +
            '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 5V1h4M11 7v4H7M1 5l4-4M11 7l-4 4"/></svg>' +
            '</button>' +
            '<button type="button" class="startup-terminal-close" title="Close terminal">\u2716</button>' +
            '</div>' +
            '</div>' +
            '<div class="startup-terminal-xterm" id="' + termId + '-xterm"></div>';
        grid.appendChild(card);

        var xtermContainer = document.getElementById(termId + "-xterm");
        var term = new TermCtor({
            convertEol: true,
            cursorBlink: true,
            cursorStyle: "bar",
            fontSize: 10,
            lineHeight: 1.2,
            scrollback: 5000,
            fontFamily: "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, monospace",
            theme: {
                background: "#0d1117",
                foreground: "#e6edf3",
                cursor: "#f97316",
                selection: "rgba(249, 115, 22, 0.3)"
            }
        });
        term.open(xtermContainer, true);
        _registerLinkProvider(term);

        var closeBtn = card.querySelector(".startup-terminal-close");
        closeBtn.addEventListener("click", function() { closeStartupTerminal(termId); });

        var copyBtn = card.querySelector(".startup-terminal-copy");
        if (copyBtn) copyBtn.addEventListener("click", function() { copyStartupTerminalOutput(termId); });

        var expandBtn = card.querySelector(".startup-terminal-expand");
        expandBtn.addEventListener("click", function() { expandTerminal(termId); });

        _startupTerminals[termId] = {
            term: term,
            closeBtn: closeBtn,
            command: command,
            processId: null,
            ws: null,
            outputBuffer: "",
            _roTarget: xtermContainer
        };

        function measureAndResize() {
            if (!xtermContainer || !term.element) return;
            var w = xtermContainer.clientWidth - 16 || 400;  // subtract padding
            var h = xtermContainer.clientHeight - 8 || 180;  // subtract padding
            if (w <= 0 || h <= 0) return;
            var cols = Math.max(20, Math.floor(w / 7.2));
            var rows = Math.max(5, Math.floor(h / 14));
            try { term.resize(cols, rows); } catch (e) {}
        }
        setTimeout(function() {
            measureAndResize();
            term.reset();
            term.writeln("\x1b[33mStarting\x1b[0m " + command + " ...");
            _appendStartupTerminalOutput(termId, "Starting " + command + " ...\n");
        }, 50);

        var ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(function() {
            measureAndResize();
            var td = _startupTerminals[termId];
            if (td && td.ws && td.ws.readyState === WebSocket.OPEN) {
                try {
                    td.ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
                } catch (e2) {}
            }
        }) : null;
        if (ro && xtermContainer) ro.observe(xtermContainer);

        _startupTerminals[termId]._resizeObserver = ro;

        startTerminalProcess(termId);
    }

    function startTerminalProcess(termId) {
        var termData = _startupTerminals[termId];
        if (!termData) return;
        
        // Determine project folder
        var project = projectsData.find(function(p) { return p.id === currentProjectId; });
        var projectFolder = project?.folder_location || "/Users/paul/development/TENSOLOGY/DecisionsAI";
        
        apiFetch("/api/projects/startup-terminal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project_id: currentProjectId,
                command: termData.command,
                working_dir: projectFolder
            })
        }).then(function(response) {
            if (!response.success) {
                showSnackbar("Failed to start terminal: " + (response.error || "unknown error"), "error");
                return;
            }
            termData.processId = response.process_id;
            termData.osPid = response.pid || null;
            // Update title to show OS PID
            var titleEl = document.querySelector(".startup-terminal-card[data-term-id='" + termId + "'] .startup-terminal-title");
            if (titleEl && response.pid) titleEl.textContent = "PID " + response.pid;
            connectTerminalsWebSocket(termId, response.process_id);
        }).catch(function(err) {
            showSnackbar("Failed to start terminal process: " + (err && err.message ? err.message : ""), "error");
            console.error("Terminal start error:", err);
        });
    }

    function connectTerminalsWebSocket(termId, processId) {
        var termData = _startupTerminals[termId];
        if (!termData || !processId) return;

        var term = termData.term;
        var token = (window.DECISIONSAI_INTERNAL_API_TOKEN || "").trim();
        var wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        var wsUrl = wsProtocol + "//" + window.location.host + "/api/projects/startup-terminal/" + encodeURIComponent(processId) + "/ws";
        if (token) {
            wsUrl += "?internal_token=" + encodeURIComponent(token);
        }

        var ws = new WebSocket(wsUrl);
        termData.ws = ws;

        ws.onopen = function() {
            try {
                ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
            } catch (e1) {}
            term.onData(function(data) {
                if (ws.readyState === WebSocket.OPEN) {
                    try {
                        ws.send(JSON.stringify({ type: "input", data: data }));
                    } catch (e2) {}
                }
            });
        };

        ws.onmessage = function(ev) {
            try {
                var msg = JSON.parse(ev.data);
                if (msg.type === "output" && msg.data) {
                    term.write(msg.data);
                    _appendStartupTerminalOutput(termId, msg.data);
                } else if (msg.type === "exit") {
                    term.writeln("");
                    term.writeln("\x1b[90m[Process ended]\x1b[0m");
                    _appendStartupTerminalOutput(termId, "\n[Process ended]\n");
                }
            } catch (e3) {}
        };

        ws.onerror = function() {
            term.writeln("\r\n\x1b[31m[WebSocket error]\x1b[0m");
            _appendStartupTerminalOutput(termId, "\n[WebSocket error]\n");
        };

        ws.onclose = function(ev) {
            termData.ws = null;
            if (ev.code === 1008) {
                term.writeln("\r\n\x1b[31m[Session not found — process may have exited before connecting]\x1b[0m");
                _appendStartupTerminalOutput(termId, "\n[Session not found — process may have exited before connecting]\n");
            } else if (ev.code !== 1000) {
                term.writeln("\r\n\x1b[90m[Disconnected]\x1b[0m");
                _appendStartupTerminalOutput(termId, "\n[Disconnected]\n");
            }
        };
    }

    // Detach all terminals for a project without killing backend processes.
    // Called when navigating away from a project.
    function detachProjectTerminals(projectId) {
        if (!projectId) return;
        var saved = [];
        Object.keys(_startupTerminals).forEach(function(termId) {
            var td = _startupTerminals[termId];
            if (td.processId) {
                saved.push({ termId: termId, processId: td.processId, command: td.command || "", pid: td.osPid || null });
            }
            // Disconnect WS silently
            if (td.ws) {
                try { td.ws.onclose = null; td.ws.onerror = null; td.ws.close(); } catch(e) {}
                td.ws = null;
            }
            if (td._resizeObserver) {
                try { td._resizeObserver.disconnect(); } catch(e) {}
            }
            if (td.term) {
                try { td.term.dispose(); } catch(e) {}
            }
        });
        if (saved.length) {
            _projectTerminalState[projectId] = saved;
        }
        _startupTerminals = {};
        var grid = document.getElementById("startup-terminal-grid");
        if (grid) { grid.innerHTML = ""; grid.classList.add("hidden"); }
        // Reset buttons to default state (no terminals running)
        var startBtn = document.getElementById("startup-start-btn");
        var termBtn = document.getElementById("startup-terminate-all-btn");
        if (startBtn) startBtn.classList.remove("hidden");
        if (termBtn) termBtn.classList.add("hidden");
    }

    // Reconnect terminals for a project that was previously running.
    function reattachProjectTerminals(projectId) {
        var saved = _projectTerminalState[projectId];
        if (!saved || !saved.length) return;

        var TermCtor = typeof window.Terminal === "function" ? window.Terminal : null;
        if (!TermCtor) return;

        var grid = document.getElementById("startup-terminal-grid");
        var startBtn = document.getElementById("startup-start-btn");
        var termBtn = document.getElementById("startup-terminate-all-btn");
        if (startBtn) startBtn.classList.add("hidden");
        if (termBtn) termBtn.classList.remove("hidden");
        if (grid) { grid.classList.remove("hidden"); grid.innerHTML = ""; }

        saved.forEach(function(entry) {
            createStartupTerminalForProcess(entry.command, entry.processId, TermCtor, entry.pid || null);
        });
    }

    // Create a terminal card and connect it to an already-running backend process.
    function createStartupTerminalForProcess(command, processId, TermCtor, osPid) {
        var termId = "term-" + (_nextTerminalId++);
        var grid = document.getElementById("startup-terminal-grid");
        if (!grid) return;

        var card = document.createElement("div");
        card.className = "startup-terminal-card";
        card.dataset.termId = termId;
        var titleText = osPid ? "PID " + osPid : "PID —";
        card.innerHTML = '<div class="startup-terminal-header">' +
            '<span class="startup-terminal-title" title="' + escapeAttr(command) + '">' + escapeAttr(titleText) + '</span>' +
            '<div class="startup-terminal-btns">' +
            '<button type="button" class="startup-terminal-copy" title="Copy output">' +
            '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="3" width="6" height="7" rx="1"/><path d="M2.5 8.5V2.8a.8.8 0 0 1 .8-.8h4.9"/></svg>' +
            '</button>' +
            '<button type="button" class="startup-terminal-expand" title="Expand">' +
            '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 5V1h4M11 7v4H7M1 5l4-4M11 7l-4 4"/></svg>' +
            '</button>' +
            '<button type="button" class="startup-terminal-close" title="Close terminal">\u2716</button>' +
            '</div>' +
            '</div>' +
            '<div class="startup-terminal-xterm" id="' + termId + '-xterm"></div>';
        grid.appendChild(card);

        var xtermContainer = document.getElementById(termId + "-xterm");
        var term = new TermCtor({
            convertEol: true, cursorBlink: true, cursorStyle: "bar",
            fontSize: 10, lineHeight: 1.2,
            fontFamily: "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, monospace",
            theme: { background: "#0d1117", foreground: "#e6edf3", cursor: "#f97316", selection: "rgba(249,115,22,0.3)" }
        });
        term.open(xtermContainer);
        _registerLinkProvider(term);

        var closeBtn = card.querySelector(".startup-terminal-close");
        closeBtn.addEventListener("click", function() { closeStartupTerminal(termId); });

        var copyBtn2 = card.querySelector(".startup-terminal-copy");
        if (copyBtn2) copyBtn2.addEventListener("click", function() { copyStartupTerminalOutput(termId); });

        var expandBtn2 = card.querySelector(".startup-terminal-expand");
        if (expandBtn2) expandBtn2.addEventListener("click", function() { expandTerminal(termId); });

        _startupTerminals[termId] = { term: term, command: command, processId: processId, ws: null, outputBuffer: "", _roTarget: xtermContainer };

        function measureAndResize() {
            if (!xtermContainer || !term.element) return;
            var w = xtermContainer.clientWidth - 16 || 400;
            var h = xtermContainer.clientHeight - 8 || 180;
            if (w <= 0 || h <= 0) return;
            try { term.resize(Math.max(20, Math.floor(w / 7.2)), Math.max(5, Math.floor(h / 14))); } catch(e) {}
        }
        var ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measureAndResize) : null;
        if (ro) { ro.observe(xtermContainer); _startupTerminals[termId]._resizeObserver = ro; }

        setTimeout(function() { measureAndResize(); }, 50);
        connectTerminalsWebSocket(termId, processId);
    }

    function closeStartupTerminal(termId) {
        var termData = _startupTerminals[termId];
        if (!termData) return;

        if (termData.ws) {
            try {
                termData.ws.onclose = null;
                termData.ws.close();
            } catch (e) {}
            termData.ws = null;
        }
        if (termData._resizeObserver) {
            try {
                if (termData._roTarget) termData._resizeObserver.unobserve(termData._roTarget);
            } catch (e2) {}
            try { termData._resizeObserver.disconnect(); } catch (e2b) {}
        }

        if (termData.processId) {
            apiFetch("/api/projects/kill-terminal", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ process_id: termData.processId })
            }).catch(function() {});
        }

        if (termData.term) {
            try { termData.term.dispose(); } catch (e3) {}
        }

        delete _startupTerminals[termId];
        var card = document.querySelector(".startup-terminal-card[data-term-id='" + termId + "']");
        if (card) card.remove();

        if (Object.keys(_startupTerminals).length === 0) {
            restoreStartupTerminalChrome();
        }
    }

    // ── Terminal expand / collapse ───────────────────────────────────────────
    var _expandedTermId = null;
    var _expandOriginalParent = null;

    function expandTerminal(termId) {
        if (_expandedTermId) collapseTerminal();  // only one at a time

        var termData = _startupTerminals[termId];
        if (!termData) return;

        var modal = document.getElementById("terminal-expand-modal");
        var body  = document.getElementById("terminal-expand-body");
        var title = document.getElementById("terminal-expand-title");
        if (!modal || !body) return;

        var xtermEl = termData._roTarget;
        if (!xtermEl) return;

        _expandedTermId = termId;
        _expandOriginalParent = xtermEl.parentNode;

        // Read PID from the card's title element (always up-to-date)
        var cardTitle = document.querySelector(".startup-terminal-card[data-term-id='" + termId + "'] .startup-terminal-title");
        title.textContent = cardTitle ? cardTitle.textContent : ("PID " + (termData.osPid || "—"));

        // Move the xterm container into the modal body
        body.appendChild(xtermEl);
        // Enable scrollbar in expanded view
        xtermEl.style.overflow = "auto";
        var viewport = xtermEl.querySelector(".xterm-viewport");
        if (viewport) viewport.style.overflowY = "auto";
        modal.classList.remove("hidden");

        // Resize xterm to fill the modal, then scroll to bottom
        requestAnimationFrame(function() {
            if (!termData.term || !termData.term.element) return;
            var w = body.clientWidth - 16;
            var h = body.clientHeight - 16;
            if (w > 0 && h > 0) {
                try {
                    termData.term.resize(
                        Math.max(20, Math.floor(w / 7.2)),
                        Math.max(5,  Math.floor(h / 14))
                    );
                } catch(e) {}
            }
            try { termData.term.scrollToBottom(); } catch(e) {}
        });
    }

    function collapseTerminal() {
        if (!_expandedTermId) return;

        var termData = _startupTerminals[_expandedTermId];
        var modal = document.getElementById("terminal-expand-modal");
        modal.classList.add("hidden");

        if (termData && _expandOriginalParent) {
            // Restore scrollbar hidden state for the card view
            var xtermEl2 = termData._roTarget;
            xtermEl2.style.overflow = "hidden";
            var viewport2 = xtermEl2.querySelector(".xterm-viewport");
            if (viewport2) viewport2.style.overflowY = "";
            // Move the xterm container back to its card
            _expandOriginalParent.appendChild(xtermEl2);
            // Resize back to card dimensions
            requestAnimationFrame(function() {
                if (!termData.term || !termData.term.element) return;
                var cont = termData._roTarget;
                var w = cont.clientWidth - 16;
                var h = cont.clientHeight - 8;
                if (w > 0 && h > 0) {
                    try {
                        termData.term.resize(
                            Math.max(20, Math.floor(w / 7.2)),
                            Math.max(5,  Math.floor(h / 14))
                        );
                    } catch(e) {}
                }
            });
        }

        _expandedTermId = null;
        _expandOriginalParent = null;
    }

    // Wire up modal buttons and ESC key
    document.addEventListener("keydown", function(e) {
        if (e.key === "Escape" && _expandedTermId) collapseTerminal();
    });
    document.addEventListener("DOMContentLoaded", function() {
        var collapseBtn = document.getElementById("terminal-expand-collapse");
        if (collapseBtn) collapseBtn.addEventListener("click", collapseTerminal);

        var stopBtn = document.getElementById("terminal-expand-stop");
        if (stopBtn) stopBtn.addEventListener("click", function() {
            var termId = _expandedTermId;
            collapseTerminal();          // restore card first
            if (termId) closeStartupTerminal(termId);  // then kill process
        });

        var copyBtn = document.getElementById("terminal-expand-copy");
        if (copyBtn) copyBtn.addEventListener("click", function() {
            if (_expandedTermId) copyStartupTerminalOutput(_expandedTermId);
        });

        // Click backdrop to collapse (not stop)
        var modal = document.getElementById("terminal-expand-modal");
        if (modal) modal.addEventListener("click", function(e) {
            if (e.target === modal) collapseTerminal();
        });
    });
    // ────────────────────────────────────────────────────────────────────────

    function restoreStartupTerminalChrome() {
        var grid = document.getElementById("startup-terminal-grid");
        if (grid) {
            grid.innerHTML = "";
            grid.classList.add("hidden");
        }
        var startBtn = document.getElementById("startup-start-btn");
        var termBtn = document.getElementById("startup-terminate-all-btn");
        if (startBtn) startBtn.classList.remove("hidden");
        if (termBtn) termBtn.classList.add("hidden");
    }

    function terminateAllStartupTerminals() {
        if (!currentProjectId) {
            showSnackbar("Select a project first", "error");
            return;
        }

        window.DecisionsAPI.confirm({
            title: "Terminate startup terminals",
            message: "Terminate all startup terminals?",
            confirmLabel: "Terminate",
            danger: true,
            onConfirm: function() {
                apiFetch("/api/projects/" + currentProjectId + "/startup-terminals/stop", {
                    method: "POST"
                }).then(function(response) {
                    Object.keys(_startupTerminals).forEach(function(termId) {
                        var termData = _startupTerminals[termId];
                        if (termData && termData.ws) {
                            try { termData.ws.onclose = null; termData.ws.close(); } catch (e) {}
                        }
                        if (termData && termData.term) {
                            try { termData.term.dispose(); } catch (e2) {}
                        }
                    });
                    _startupTerminals = {};
                    delete _projectTerminalState[currentProjectId];
                    restoreStartupTerminalChrome();
                    var stopped = Number(response && response.stopped) || 0;
                    if (stopped > 0) {
                        showSnackbar("Stopped " + stopped + " startup terminal" + (stopped === 1 ? "" : "s") + ".", "success");
                    } else {
                        showSnackbar("No startup terminals were running.", "info");
                    }
                }).catch(function(err) {
                    showSnackbar("Failed to stop startup terminals: " + (err && err.message ? err.message : ""), "error");
                });
            }
        });
    }
    
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
