/**
 * Actions page: matches desktop ActionWindow (distr/gui/action.py) and projects layout.
 * Left: search + action list + Add. Right: empty state or detail form with Record Inputs / Instruction tabs.
 */
(function() {
    var currentActionId = null;
    var searchText = "";
    var actionsData = [];
    var isRecording = false;

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function setError(el, msg) {
        el.innerHTML = "<p class=\"text-sm text-amber-400\">" + msg + "</p>";
    }

    var apiFetch = window.DecisionsAPI.fetch;
    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "actions-snackbar" }); }

    function setEmpty(el) {
        el.innerHTML = "<p class=\"text-sm text-gray-400\">No actions yet. Create one with Add Action.</p>";
    }

    function filterActions(data) {
        if (!searchText.trim()) return data;
        var q = searchText.trim().toLowerCase();
        return data.filter(function(a) {
            var title = (a.title || "").toLowerCase();
            var desc = (a.description || "").toLowerCase();
            return title.indexOf(q) !== -1 || desc.indexOf(q) !== -1;
        });
    }

    var contextMenuActionId = null;

    function renderList(data) {
        var el = document.getElementById("actions-list");
        if (!el) return;
        if (!Array.isArray(data)) data = [];
        actionsData = data;
        var filtered = filterActions(data);
        if (!filtered.length) {
            el.innerHTML = data.length === 0
                ? "<p class=\"text-sm text-gray-400\">No actions yet. Create one with Add Action.</p>"
                : "<p class=\"text-sm text-gray-400\">No actions match.</p>";
            return;
        }
        var deleteSvg = "<svg class=\"w-4 h-4 flex-shrink-0\" fill=\"none\" stroke=\"currentColor\" viewBox=\"0 0 24 24\"><path stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16\"/></svg>";
        el.innerHTML = filtered.map(function(a) {
            var label = a.is_instruction ? " <span class=\"text-xs text-gray-500\">(instruction)</span>" : "";
            var active = currentActionId === a.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            return "<div class=\"action-item-wrapper flex items-center gap-1 rounded border" + active + " group focus:outline-none focus:ring-2 focus:ring-[#f97316]/60\" data-id=\"" + a.id + "\" tabindex=\"0\" role=\"option\" aria-selected=\"" + (currentActionId === a.id ? "true" : "false") + "\">" +
                "<button type=\"button\" class=\"action-item flex-1 min-w-0 text-left px-3 py-2 text-white text-sm\" data-id=\"" + a.id + "\">" + escapeAttr(a.title || "Untitled") + label + "</button>" +
                "<button type=\"button\" class=\"action-item-delete p-1.5 rounded text-gray-400 hover:text-red-400 hover:bg-red-500/20 flex-shrink-0\" data-id=\"" + a.id + "\" aria-label=\"Delete\">" + deleteSvg + "</button>" +
                "</div>";
        }).join("");
        el.querySelectorAll(".action-item").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                selectAction(parseInt(btn.getAttribute("data-id"), 10));
            });
        });
        el.querySelectorAll(".action-item-delete").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                e.preventDefault();
                deleteActionFromList(parseInt(btn.getAttribute("data-id"), 10));
            });
        });
        el.querySelectorAll(".action-item-wrapper").forEach(function(wrap) {
            wrap.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                showActionContextMenu(parseInt(wrap.getAttribute("data-id"), 10), e.clientX, e.clientY);
            });
            wrap.addEventListener("focus", function() {
                var id = parseInt(wrap.getAttribute("data-id"), 10);
                if (id && currentActionId !== id) selectAction(id);
            });
        });
    }

    function isTypingTarget(target) {
        if (!target) return false;
        var tag = (target.tagName || "").toLowerCase();
        return !!(target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select");
    }

    function bindActionListKeyboard() {
        if (!window.DecisionsListKeyboard) return;
        window.DecisionsListKeyboard.bind({
            listEl: "actions-list",
            namespace: "actions",
            rowSelector: ".action-item-wrapper",
            getRowId: function(row) { return parseInt(row.getAttribute("data-id"), 10); },
            getSelectedId: function() { return currentActionId; },
            onSelect: function(id) { selectAction(id); },
            onDelete: function(id) { deleteActionFromList(id); },
            pageGuard: function() { return !!document.getElementById("actions-list"); },
        });
    }

    function showActionContextMenu(actionId, x, y) {
        var menu = document.getElementById("action-context-menu");
        if (!menu) return;
        contextMenuActionId = actionId;
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        var a = actionsData.filter(function(x) { return x.id === actionId; })[0];
        var hasRecording = a && !a.is_instruction && (a.recording_filename || "");
        var playBtn = menu.querySelector(".action-ctx-play");
        if (playBtn) playBtn.style.display = hasRecording ? "" : "none";
    }

    function hideActionContextMenu() {
        var menu = document.getElementById("action-context-menu");
        if (menu) menu.classList.add("hidden");
        contextMenuActionId = null;
    }
    document.addEventListener("click", hideActionContextMenu);

    function deleteActionFromList(id) {
        var name = (actionsData.filter(function(a) { return a.id === id; })[0] || {}).title || "this action";
        window.DecisionsAPI.confirm({
            title: "Remove action",
            message: "Remove action \"" + name + "\"? This cannot be undone.",
            confirmLabel: "Remove",
            danger: true,
            onConfirm: function() {
                fetch("/api/actions/" + id, { method: "DELETE" })
                    .then(function(r) {
                        if (r.ok) {
                            if (currentActionId === id) {
                                currentActionId = null;
                                showEmpty();
                            }
                            loadActions();
                            showSnackbar("Action removed", "success");
                        } else {
                            return r.json().then(function(e) { throw new Error(e.detail || "Failed to remove"); });
                        }
                    })
                    .catch(function(e) { showSnackbar(e.message || "Failed to remove action", "error"); });
            }
        });
    }

    function loadActions(skip_auto_select) {
        var el = document.getElementById("actions-list");
        if (!el) return Promise.resolve();
        return fetch("/api/actions")
            .then(function(r) {
                if (!r.ok) {
                    return r.json().then(function(err) {
                        setError(el, "Could not load actions: " + (err && err.detail ? err.detail : "HTTP " + r.status));
                    }).catch(function() {
                        setError(el, "Could not load actions (HTTP " + r.status + "). Check server logs.");
                    }).then(function() { throw new Error(r.status); });
                }
                return r.json();
            })
            .then(function(data) {
                if (!el) return;
                if (!Array.isArray(data)) data = [];
                actionsData = data;
                renderList(data);
                if (data.length > 0) {
                    if (!skip_auto_select) selectAction(data[0].id);
                } else {
                    showEmpty();
                }
            })
            .catch(function() {
                if (el && el.innerHTML.indexOf("Loading") !== -1) setEmpty(el);
            });
    }

    function showEmpty() {
        document.getElementById("actions-empty").classList.remove("hidden");
        document.getElementById("actions-detail").classList.add("hidden");
        currentActionId = null;
        renderList(actionsData);
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

    function showDetail(action) {
        document.getElementById("actions-empty").classList.add("hidden");
        document.getElementById("actions-detail").classList.remove("hidden");
        currentActionId = action.id;
        document.getElementById("action-detail-title").textContent = action.title || "Action";
        document.getElementById("detail-title").value = action.title || "";
        document.getElementById("detail-description").value = action.description || "";
        document.getElementById("detail-instruction").value = action.instruction_text || "";
        var words = [];
        try { words = JSON.parse(action.additional_trigger_words || "[]"); } catch (e) {}
        renderTriggerBadges(words);
        var triggersInput = document.getElementById("detail-triggers-input");
        if (triggersInput) triggersInput.value = "";

        var isInstr = action.is_instruction === true;
        switchTab(isInstr ? "instruction" : "record");

        var hasRecording = !isInstr && (action.recording_filename || "");
        var playDetail = document.getElementById("detail-play-btn");
        if (playDetail) playDetail.classList.toggle("hidden", !hasRecording);
        var recDetail = document.getElementById("detail-record-btn");
        var stopDetail = document.getElementById("detail-stop-btn");
        if (recDetail) recDetail.classList.remove("hidden");
        if (stopDetail) stopDetail.classList.add("hidden");

        renderList(actionsData);
    }

    function switchTab(tabName) {
        document.querySelectorAll(".action-tab").forEach(function(t) {
            t.classList.toggle("text-white", t.getAttribute("data-tab") === tabName);
            t.classList.toggle("border-[#f97316]", t.getAttribute("data-tab") === tabName);
            t.classList.toggle("text-gray-400", t.getAttribute("data-tab") !== tabName);
            t.classList.toggle("border-transparent", t.getAttribute("data-tab") !== tabName);
        });
        document.querySelectorAll(".action-tab-pane").forEach(function(p) {
            p.classList.add("hidden");
        });
        var pane = document.getElementById("tab-" + tabName);
        if (pane) pane.classList.remove("hidden");
    }

    function selectAction(id) {
        currentActionId = id;
        fetch("/api/actions/" + id)
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(action) {
                showDetail(action);
            })
            .catch(function() {
                showSnackbar("Could not load action", "error");
            });
    }

    function saveAction() {
        if (!currentActionId) return;
        var tabRecord = document.querySelector(".action-tab[data-tab='record']");
        var isInstruction = tabRecord && !tabRecord.classList.contains("border-[#f97316]");
        var title = (document.getElementById("detail-title").value || "").trim() || "New Action";
        var description = (document.getElementById("detail-description").value || "").trim();
        var instructionText = (document.getElementById("detail-instruction").value || "").trim();
        if (isInstruction && !instructionText) {
            showSnackbar("Instruction text is required for instruction actions", "error");
            return;
        }
        var payload = {
            title: title,
            description: description,
            additional_trigger_words: JSON.stringify(getTriggerWordsArray()),
            is_instruction: isInstruction,
            instruction_text: isInstruction ? instructionText : null
        };
        fetch("/api/actions/" + currentActionId, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function(r) {
                if (r.ok) {
                    loadActions(true).then(function() { selectAction(currentActionId); });
                    showSnackbar("Action updated", "success");
                } else {
                    return r.json().then(function(e) { throw new Error(e.detail || "Failed to save"); });
                }
            })
            .catch(function(e) { showSnackbar(e.message || "Failed to save action", "error"); });
    }

    function playAction() {
        if (!currentActionId) return;
        fetch("/api/actions/" + currentActionId + "/play", { method: "POST" })
            .then(function(r) {
                if (r.ok) showSnackbar("Playing action…", "success");
                else return r.json().then(function(e) { throw new Error(e.detail || "Failed to play"); });
            })
            .catch(function(e) { showSnackbar(e.message || "Play requires the desktop app", "error"); });
    }

    function startRecording() {
        isRecording = true;
        var recDetail = document.getElementById("detail-record-btn");
        var stopDetail = document.getElementById("detail-stop-btn");
        if (recDetail) recDetail.classList.add("hidden");
        if (stopDetail) stopDetail.classList.remove("hidden");
        var body = currentActionId ? { action_id: currentActionId } : {};
        fetch("/api/actions/start-recording", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        })
            .then(function(r) {
                if (r.ok) showSnackbar("Recording started (desktop app). Stop when done.", "success");
                else r.json().then(function(e) { showSnackbar(e.detail || "Start recording failed", "error"); });
            })
            .catch(function() { showSnackbar("Start recording failed", "error"); isRecording = false; });
    }

    function stopRecording() {
        if (!isRecording) return;
        isRecording = false;
        var recDetail = document.getElementById("detail-record-btn");
        var stopDetail = document.getElementById("detail-stop-btn");
        if (recDetail) recDetail.classList.remove("hidden");
        if (stopDetail) stopDetail.classList.add("hidden");
        fetch("/api/actions/stop-recording", { method: "POST" })
            .then(function(r) {
                if (r.ok) {
                    showSnackbar("Recording stopped", "success");
                    if (currentActionId) { loadActions(true).then(function() { selectAction(currentActionId); }); }
                } else {
                    r.json().then(function(e) { showSnackbar(e.detail || "Stop failed", "error"); });
                }
            })
            .catch(function() { showSnackbar("Stop recording failed", "error"); });
    }

    function removeAction() {
        if (!currentActionId) return;
        var name = (actionsData.filter(function(a) { return a.id === currentActionId; })[0] || {}).title || "this action";
        window.DecisionsAPI.confirm({
            title: "Remove action",
            message: "Remove action \"" + name + "\"? This cannot be undone.",
            confirmLabel: "Remove",
            danger: true,
            onConfirm: function() {
                fetch("/api/actions/" + currentActionId, { method: "DELETE" })
                    .then(function(r) {
                        if (r.ok) {
                            currentActionId = null;
                            showEmpty();
                            loadActions();
                            showSnackbar("Action removed", "success");
                        } else {
                            return r.json().then(function(e) { throw new Error(e.detail || "Failed to remove"); });
                        }
                    })
                    .catch(function(e) { showSnackbar(e.message || "Failed to remove action", "error"); });
            }
        });
    }

    function addAction() {
        fetch("/api/actions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "New Action", description: "", additional_trigger_words: "[]", is_instruction: false })
        })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || "Create failed"); });
                return r.json();
            })
            .then(function(data) {
                loadActions(true).then(function() { selectAction(data.id); });
                showSnackbar("Action created", "success");
            })
            .catch(function(e) {
                showSnackbar(e.message || "Failed to create action", "error");
            });
    }

    document.querySelectorAll(".action-tab").forEach(function(t) {
        t.addEventListener("click", function() {
            switchTab(t.getAttribute("data-tab"));
        });
    });
    var triggersInput = document.getElementById("detail-triggers-input");
    if (triggersInput) {
        triggersInput.addEventListener("keydown", function(e) {
            if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                addTriggerWordsFromInput();
            }
        });
        triggersInput.addEventListener("blur", addTriggerWordsFromInput);
    }

    document.getElementById("action-create-big") && document.getElementById("action-create-big").addEventListener("click", addAction);
    document.getElementById("action-add") && document.getElementById("action-add").addEventListener("click", addAction);
    document.getElementById("action-update") && document.getElementById("action-update").addEventListener("click", saveAction);
    document.getElementById("action-remove") && document.getElementById("action-remove").addEventListener("click", removeAction);
    document.getElementById("detail-play-btn") && document.getElementById("detail-play-btn").addEventListener("click", playAction);
    document.getElementById("detail-record-btn") && document.getElementById("detail-record-btn").addEventListener("click", startRecording);
    document.getElementById("detail-stop-btn") && document.getElementById("detail-stop-btn").addEventListener("click", stopRecording);

    document.getElementById("action-context-menu") && document.querySelectorAll("#action-context-menu button").forEach(function(btn) {
        btn.addEventListener("click", function() {
            if (contextMenuActionId == null) return;
            if (btn.classList.contains("action-ctx-edit")) {
                selectAction(contextMenuActionId);
            } else if (btn.classList.contains("action-ctx-play")) {
                currentActionId = contextMenuActionId;
                playAction();
            } else if (btn.classList.contains("action-ctx-delete")) {
                deleteActionFromList(contextMenuActionId);
            }
            hideActionContextMenu();
        });
    });

    var searchEl = document.getElementById("actions-search");
    if (searchEl) {
        searchEl.addEventListener("input", function() {
            searchText = searchEl.value || "";
            renderList(actionsData);
        });
    }

    if (document.getElementById("actions-list")) {
        bindActionListKeyboard();
        loadActions();
    }
})();
