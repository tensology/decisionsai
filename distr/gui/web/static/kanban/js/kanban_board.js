(function() {
    "use strict";

    function esc(s) {
        var d = document.createElement("div");
        d.textContent = s || "";
        return d.innerHTML;
    }

    function truncate(s, maxLen) {
        if (!s) return "";
        s = s.replace(/\s+/g, " ").trim();
        return s.length > maxLen ? s.substring(0, maxLen) + "…" : s;
    }

    function stripHtml(html) {
        if (!html) return "";
        if (typeof html === "object") {
            var parts = [];
            (function walk(node) {
                if (Array.isArray(node)) { node.forEach(walk); return; }
                if (typeof node === "object" && node !== null) {
                    if (node.type === "text") parts.push(node.text || "");
                    if (node.type === "hardBreak" || node.type === "paragraph") parts.push("\n");
                    if (node.content) walk(node.content);
                    if (Array.isArray(node.marks)) node.marks.forEach(walk);
                }
            })(html);
            return parts.join("").replace(/\n{3,}/g, "\n\n").trim();
        }
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        return (tmp.textContent || tmp.innerText || "").replace(/\s+/g, " ").trim();
    }

    function isValidTimeTrackingValue(value) {
        var v = (value || "").trim();
        if (!v) return true;
        return /^\d+\s*[wdhm](\s+\d+\s*[wdhm])*$/i.test(v);
    }

    var SOURCE_CHAT_STORAGE_KEY = "decisions_source_chat_id";

    function getSourceChatIdForTickets() {
        var id = null;
        try {
            if (window.DecisionsWebChat && typeof window.DecisionsWebChat.getSourceChatIdForTickets === "function") {
                id = window.DecisionsWebChat.getSourceChatIdForTickets();
            }
            if (id == null || id < 1) {
                var raw = sessionStorage.getItem(SOURCE_CHAT_STORAGE_KEY);
                if (raw) {
                    var n = parseInt(raw, 10);
                    if (!isNaN(n) && n >= 1) id = n;
                }
            }
        } catch (e) { /* ignore */ }
        return id != null && id >= 1 ? id : null;
    }

    /** Mutates obj (plain ticket POST body) to include source_chat_id when known. */
    function mergeSourceChatIntoPayload(obj) {
        if (!obj || typeof obj !== "object") return obj;
        var cid = getSourceChatIdForTickets();
        if (cid != null) obj.source_chat_id = cid;
        return obj;
    }

    window.KanbanCommonUtils = {
        esc: esc,
        truncate: truncate,
        stripHtml: stripHtml,
        isValidTimeTrackingValue: isValidTimeTrackingValue,
        getSourceChatIdForTickets: getSourceChatIdForTickets,
        mergeSourceChatIntoPayload: mergeSourceChatIntoPayload,
    };
})();

(function() {
    "use strict";

    var confirmCallback = null;

    function isAnyModalOpen(exceptId) {
        return Array.prototype.some.call(
            document.querySelectorAll(".kb-modal-overlay"),
            function(modal) {
                return modal.id !== exceptId && !modal.classList.contains("hidden");
            }
        );
    }

    function hideAllModals(exceptId) {
        document.querySelectorAll(".kb-modal-overlay").forEach(function(modal) {
            if (modal.id !== exceptId) modal.classList.add("hidden");
        });
    }

    function showConfirm(opts) {
        opts = opts || {};
        document.getElementById("kb-confirm-title").textContent = opts.title || "Confirm";
        document.getElementById("kb-confirm-message").textContent = opts.message || "";
        var okBtn = document.getElementById("kb-confirm-ok");
        okBtn.textContent = opts.confirmLabel || "OK";
        okBtn.className = opts.danger
            ? "px-4 py-2 rounded text-white text-sm bg-red-600 hover:bg-red-700"
            : "px-4 py-2 rounded text-white text-sm bg-[#f97316] hover:bg-[#ea580c]";
        confirmCallback = typeof opts.onConfirm === "function" ? opts.onConfirm : null;
        hideAllModals("kb-confirm-modal");
        document.getElementById("kb-confirm-modal").classList.remove("hidden");
        requestAnimationFrame(function() {
            try { okBtn.focus(); } catch (err) {}
        });
    }

    function hideConfirm() {
        confirmCallback = null;
        document.getElementById("kb-confirm-modal").classList.add("hidden");
    }

    function invokeConfirmAction() {
        if (confirmCallback) confirmCallback();
    }

    window.KanbanModalHelpers = {
        isAnyModalOpen: isAnyModalOpen,
        hideAllModals: hideAllModals,
        showConfirm: showConfirm,
        hideConfirm: hideConfirm,
        invokeConfirmAction: invokeConfirmAction,
    };
})();

(function() {
    "use strict";

    function create(deps) {
        function populateProviderDropdowns(selectIds) {
            var providers = deps.getAgentProviders();
            if (!providers || !providers.length) {
                providers = [{ id: "ollama", name: "Ollama" }];
            }
            selectIds.forEach(function(selId) {
                var sel = document.getElementById(selId);
                if (!sel) return;
                var cur = sel.value;
                sel.innerHTML = '<option value="">(chat default)</option>';
                providers.forEach(function(p) {
                    var opt = document.createElement("option");
                    opt.value = p.id;
                    opt.textContent = p.name;
                    sel.appendChild(opt);
                });
                if (cur) sel.value = cur;
            });
        }

        function loadAgentProviders() {
            return deps.apiFetch("/api/llms/available-providers").then(function(data) {
                deps.setAgentProviders(data.providers || [{ id: "ollama", name: "Ollama" }]);
            }).catch(function() {
                deps.setAgentProviders([{ id: "ollama", name: "Ollama" }]);
            });
        }

        function loadAgentModels(prefix, provider, selectedModel, llmType) {
            var sel = document.getElementById(prefix + "-model");
            if (!provider) {
                sel.innerHTML = '<option value="">(chat default)</option>';
                return Promise.resolve();
            }
            sel.innerHTML = '<option value="">Loading...</option>';
            sel.disabled = true;
            var type = llmType || "conversational";
            return deps.apiFetch("/api/llms/models?type=" + encodeURIComponent(type) + "&provider=" + encodeURIComponent(provider)).then(function(data) {
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
                        extra.value = selectedModel;
                        extra.textContent = selectedModel;
                        sel.appendChild(extra);
                        sel.value = selectedModel;
                    }
                }
            }).catch(function() {
                sel.innerHTML = '<option value="">(chat default)</option>';
            }).then(function() { sel.disabled = false; });
        }

        function loadBoardDefaults(data) {
            deps.apiFetch("/api/kanban/linkable").then(function(ld) {
                deps.populateSelect("kb-board-def-workflow", ld.workflows, "id", "title", data ? data.default_workflow_id : null);
                deps.populateSelect("kb-board-def-project", ld.projects, "id", "name", data ? data.default_project_id : null);
            }).catch(function() {});
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

        function openBoardModal(boardId) {
            boardId = boardId ? parseInt(boardId, 10) : null;
            deps.setEditingBoardId(boardId);
            document.getElementById("kb-board-modal-title").textContent = boardId ? "Edit Board" : "New Board";
            document.getElementById("kb-board-modal-save").textContent = boardId ? "Save" : "Create";
            switchBoardModalTab("details");
            var currentBoardData = deps.getCurrentBoardData();
            if (boardId && currentBoardData && currentBoardData.id == boardId) {
                populateBoardModal(currentBoardData);
            } else if (boardId) {
                Promise.all([
                    deps.apiFetch("/api/kanban/boards/" + boardId),
                    deps.apiFetch("/api/kanban/boards").catch(function() { return []; }),
                ]).then(function(results) {
                    var data = results[0] || {};
                    var list = Array.isArray(results[1]) ? results[1] : [];
                    var row = list.find(function(b) { return String(b.id) === String(boardId); });
                    if (row && typeof row.agent_enabled !== "undefined") {
                        data.agent_enabled = row.agent_enabled;
                    }
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
            if (boardId) deps.loadBoardWaLinks(boardId);
            else {
                var waLinksEl = document.getElementById("kb-bm-wa-links");
                if (waLinksEl) waLinksEl.innerHTML = '<div class="text-xs text-gray-500 italic">Save the board first to link WhatsApp chats</div>';
            }
            document.getElementById("kb-board-modal").classList.remove("hidden");
            if (typeof injectInfoIcons === "function") injectInfoIcons();
        }

        function openExternalBoardConfigModal(provider, extBoardId) {
            Promise.all([
                deps.getExternalBoards(false).catch(function() { return { trello: [], jira: [] }; }),
                deps.apiFetch("/api/kanban/external-boards/" + provider + "/" + encodeURIComponent(extBoardId) + "/local-config")
                    .catch(function() { return {}; })
            ]).then(function(results) {
                var extData = results[0] || { trello: [], jira: [] };
                var localCfg = results[1] || {};
                var list = provider === "trello" ? (extData.trello || []) : (extData.jira || []);
                var boardMeta = list.find(function(b) { return String(b.id) === String(extBoardId); }) || {};
                var boardName = localCfg.name || boardMeta.name || (provider === "trello" ? "Trello Board" : "Jira Board");
                document.getElementById("kb-board-modal-title").textContent = "Configure " + (provider === "trello" ? "Trello" : "Jira") + " Board";
                document.getElementById("kb-board-modal-save").textContent = "Save";
                deps.setEditingBoardId(localCfg.local_id || null);
                document.getElementById("kb-board-modal-name").value = boardName;
                document.getElementById("kb-board-modal-name").readOnly = false;
                document.getElementById("kb-board-modal-desc").value = "";
                document.getElementById("kb-board-modal-agent-enabled").checked = !!localCfg.agent_enabled;
                var colorInput = document.getElementById("kb-board-modal-color");
                var colorHex = document.getElementById("kb-board-modal-color-hex");
                var c = localCfg.color || boardMeta.color || (provider === "trello" ? "#0079bf" : "#0052cc");
                colorInput.value = c;
                colorHex.textContent = c;
                loadBoardDefaults({
                    default_workflow_id: localCfg.default_workflow_id,
                    default_project_id: localCfg.default_project_id,
                });
                window._extBoardConfig = { provider: provider, extBoardId: extBoardId };
                switchBoardModalTab("details");
                if (localCfg.local_id) deps.loadBoardWaLinks(localCfg.local_id);
                else {
                    var waLinksEl = document.getElementById("kb-bm-wa-links");
                    if (waLinksEl) waLinksEl.innerHTML = '<div class="text-xs text-gray-500 italic">Save this external board config first, then link WhatsApp chats.</div>';
                }
                document.getElementById("kb-board-modal").classList.remove("hidden");
            }).catch(function(e) {
                deps.showSnackbar("Failed to load external board config: " + e.message, "error");
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

        function closeBoardModal() {
            document.getElementById("kb-board-modal").classList.add("hidden");
            deps.setEditingBoardId(null);
            window._extBoardConfig = null;
        }

        function saveBoardModal() {
            if (window._extBoardConfig) {
                var extCfg = window._extBoardConfig;
                var extPayload = {
                    name: document.getElementById("kb-board-modal-name").value.trim(),
                    default_project_id: parseInt(document.getElementById("kb-board-def-project").value, 10) || 0,
                    default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value, 10) || 0,
                    color: document.getElementById("kb-board-modal-color").value || "",
                    agent_enabled: document.getElementById("kb-board-modal-agent-enabled").checked,
                };
                deps.apiFetch("/api/kanban/external-boards/" + extCfg.provider + "/" + encodeURIComponent(extCfg.extBoardId) + "/register", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(extPayload)
                }).then(function() {
                    deps.showSnackbar("External board configured");
                    window._extBoardConfig = null;
                    closeBoardModal();
                    var currentBoard = deps.getCurrentBoard();
                    if (currentBoard) deps.selectBoard(currentBoard.source, currentBoard.id, currentBoard.extUrl);
                    deps.loadBoards(true);
                }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
                return;
            }

            var name = document.getElementById("kb-board-modal-name").value.trim();
            if (!name) { deps.showSnackbar("Board name is required", "error"); return; }
            var payload = {
                name: name,
                description: document.getElementById("kb-board-modal-desc").value.trim(),
                default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value, 10) || 0,
                default_project_id: parseInt(document.getElementById("kb-board-def-project").value, 10) || 0,
                color: document.getElementById("kb-board-modal-color").value || "",
            };
            var agentChecked = document.getElementById("kb-board-modal-agent-enabled").checked;

            if (deps.getEditingBoardId()) {
                var boardId = deps.getEditingBoardId();
                deps.apiFetch("/api/kanban/boards/" + boardId, {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                }).then(function() {
                    return deps.apiFetch("/api/kanban/boards/" + boardId + "/agent-enabled", {
                        method: "PUT", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ agent_enabled: agentChecked }),
                    });
                }).then(function() {
                    deps.showSnackbar("Board updated");
                    closeBoardModal();
                    deps.loadBoards(true);
                    var currentBoard = deps.getCurrentBoard();
                    if (currentBoard && currentBoard.id === boardId) deps.selectBoard("database", boardId);
                }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
            } else {
                deps.apiFetch("/api/kanban/boards", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: payload.name, description: payload.description })
                }).then(function(data) {
                    return deps.apiFetch("/api/kanban/boards/" + data.id, {
                        method: "PUT", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            default_workflow_id: payload.default_workflow_id,
                            default_project_id: payload.default_project_id,
                            color: payload.color,
                        })
                    }).then(function() { return data; });
                }).then(function(data) {
                    return deps.apiFetch("/api/kanban/boards/" + data.id + "/agent-enabled", {
                        method: "PUT", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ agent_enabled: agentChecked }),
                    }).then(function() { return data; });
                }).then(function(data) {
                    deps.showSnackbar("Board created");
                    closeBoardModal();
                    deps.loadBoards(true);
                    deps.selectBoard("database", data.id);
                }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
            }
        }

        function deleteBoard() {
            var currentBoard = deps.getCurrentBoard();
            if (!currentBoard || currentBoard.source !== "database") return;
            if (!confirm("Delete this board and all its tickets? This cannot be undone.")) return;
            deps.apiFetch("/api/kanban/boards/" + currentBoard.id, { method: "DELETE" }).then(function() {
                deps.showSnackbar("Board deleted");
                deps.clearCurrentBoard();
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                deps.loadBoards(true);
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function loadGlobalKanbanSettings() {
            return loadAgentProviders().then(function() {
                return Promise.all([
                deps.apiFetch("/api/kanban/settings"),
                deps.apiFetch("/api/llms"),
                ]);
            }).then(function(results) {
                var s = results[0] || {};
                var llm = results[1] || {};
                // Orchestrator is intentionally hidden here; it follows Conversational LLM globally.
                // Sub-agent and coder are synced with global LLM settings, but editable via dropdowns.
                populateProviderDropdowns(["kb-gs-sub-provider", "kb-gs-coder-provider"]);
                var subProv = (llm.kanban_provider || "").toLowerCase();
                var subModel = llm.kanban_model || "";
                var coderProv = (llm.coding_provider || "").toLowerCase();
                var coderModel = llm.coding_model || "";
                document.getElementById("kb-gs-sub-provider").value = subProv;
                document.getElementById("kb-gs-coder-provider").value = coderProv;
                loadAgentModels("kb-gs-sub", subProv, subModel, "kanban");
                loadAgentModels("kb-gs-coder", coderProv, coderModel, "coding");
                updateKanbanLlmDownloadButtons();
                document.getElementById("kb-gs-agent-enabled").checked = !!s.kanban_agent_enabled;
                document.getElementById("kb-gs-frequency").value = s.kanban_agent_frequency || "daily";
                updateGsFrequencyUI(s.kanban_agent_frequency || "daily");

                var gsHours = [];
                try { gsHours = typeof s.kanban_agent_hours === "string" ? JSON.parse(s.kanban_agent_hours) : (s.kanban_agent_hours || []); } catch (e) { gsHours = []; }
                document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                    var h = parseInt(btn.dataset.hour, 10);
                    btn.setAttribute("data-selected", gsHours.indexOf(h) >= 0 ? "1" : "0");
                });

                var gsDays = [];
                try { gsDays = typeof s.kanban_agent_days === "string" ? JSON.parse(s.kanban_agent_days) : (s.kanban_agent_days || []); } catch (e) { gsDays = []; }
                document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
                    var d = parseInt(btn.dataset.day, 10);
                    btn.setAttribute("data-selected", gsDays.indexOf(d) >= 0 ? "1" : "0");
                });

                document.getElementById("kb-gs-monthly-day-input").value = String(s.kanban_agent_monthly_day || 1);
                document.getElementById("kb-gs-time-input").value = s.kanban_agent_time || "09:00";

                var laneNames = [];
                var currentBoardData = deps.getCurrentBoardData();
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
            }).catch(function(e) { deps.showSnackbar("Failed to load settings: " + e.message, "error"); });
        }

        /** Notify other tabs / listeners that global LLM rows were saved (e.g. Settings → LLMs). */
        function notifyLlmsSettingsSynced() {
            try {
                if (typeof BroadcastChannel !== "undefined") {
                    var ch = new BroadcastChannel("decisions_llms_settings_sync");
                    ch.postMessage({ source: "kanban" });
                    setTimeout(function() {
                        try { ch.close(); } catch (e2) {}
                    }, 0);
                }
            } catch (e) {}
        }

        function saveGlobalKanbanSettings() {
            var hours = [];
            document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                if (btn.getAttribute("data-selected") === "1") hours.push(parseInt(btn.dataset.hour, 10));
            });
            hours = hours.filter(function(v, i, a) { return a.indexOf(v) === i; });
            var days = [];
            document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
                if (btn.getAttribute("data-selected") === "1") days.push(parseInt(btn.dataset.day, 10));
            });
            var payload = {
                kanban_agent_enabled: document.getElementById("kb-gs-agent-enabled").checked,
                kanban_agent_frequency: document.getElementById("kb-gs-frequency").value,
                kanban_agent_time: document.getElementById("kb-gs-time-input").value || "09:00",
                kanban_agent_hours: hours,
                kanban_agent_days: days,
                kanban_agent_monthly_day: parseInt(document.getElementById("kb-gs-monthly-day-input").value, 10) || 1,
                kanban_agent_source_lane: document.getElementById("kb-gs-source-lane").value,
                kanban_agent_done_lane: document.getElementById("kb-gs-done-lane").value
            };
            var llmSyncPromise = deps.apiFetch("/api/llms").then(function(current) {
                var llmPayload = Object.assign({}, current, {
                    coding_provider: document.getElementById("kb-gs-coder-provider").value || "",
                    coding_model: document.getElementById("kb-gs-coder-model").value || "",
                    kanban_provider: document.getElementById("kb-gs-sub-provider").value || "",
                    kanban_model: document.getElementById("kb-gs-sub-model").value || "",
                });
                return deps.apiFetch("/api/llms", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(llmPayload),
                });
            });
            Promise.all([
                deps.apiFetch("/api/kanban/settings", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                }),
                llmSyncPromise
            ]).then(function() {
                deps.showSnackbar("Settings saved");
                notifyLlmsSettingsSynced();
                closeGlobalSettingsModal();
            }).catch(function(e) { deps.showSnackbar("Save failed: " + e.message, "error"); });
        }

        function openGlobalSettingsModal() {
            loadGlobalKanbanSettings();
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

        function updateTabBarVisibility() {
            var tabMessages = document.getElementById("kb-tab-messages");
            var tabBar = document.getElementById("kb-tab-bar");
            tabBar.classList.toggle("hidden", tabMessages.classList.contains("hidden"));
        }

        function setSidebarTabInUrl(tab) {
            try {
                var url = new URL(window.location.href);
                if (tab === "messages") url.searchParams.set("tab", "messages");
                else url.searchParams.delete("tab");
                window.history.replaceState({}, "", url.toString());
            } catch (e) {}
        }

        function getSidebarTabFromUrl() {
            try {
                var params = new URLSearchParams(window.location.search);
                return (params.get("tab") || "").toLowerCase();
            } catch (e) {
                return "";
            }
        }

        function switchSidebarTab(tab) {
            var ticketsPanel = document.getElementById("kb-panel-tickets");
            var messagesPanel = document.getElementById("kb-panel-messages");
            var tabTickets = document.getElementById("kb-tab-tickets");
            var tabMessages = document.getElementById("kb-tab-messages");
            if (tab === "messages") {
                setSidebarTabInUrl("messages");
                ticketsPanel.classList.add("hidden");
                messagesPanel.classList.remove("hidden");
                tabTickets.classList.remove("active");
                tabMessages.classList.add("active");
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-empty").classList.add("hidden");
                deps.onEnterMessagesTab();
            } else {
                setSidebarTabInUrl("tickets");
                ticketsPanel.classList.remove("hidden");
                messagesPanel.classList.add("hidden");
                tabTickets.classList.add("active");
                tabMessages.classList.remove("active");
                deps.closeWhatsAppThread();
                if (!deps.getCurrentBoard()) {
                    document.getElementById("kb-loading").classList.remove("hidden");
                    document.getElementById("kb-board-view").classList.add("hidden");
                    document.getElementById("kb-empty").classList.remove("hidden");
                } else {
                    document.getElementById("kb-loading").classList.add("hidden");
                    document.getElementById("kb-board-view").classList.remove("hidden");
                    document.getElementById("kb-empty").classList.add("hidden");
                }
            }
        }

        function showBoardContextMenu(e, boardId) {
            deps.setCtxMenuBoardId(boardId);
            var menu = document.getElementById("kb-board-ctx-menu");
            menu.style.left = e.clientX + "px";
            menu.style.top = e.clientY + "px";
            menu.classList.remove("hidden");
        }

        function ctxConfigureBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            hideBoardContextMenu();
            openBoardModal(boardId);
        }

        function hideBoardContextMenu() {
            document.getElementById("kb-board-ctx-menu").classList.add("hidden");
            deps.setCtxMenuBoardId(null);
        }

        function ctxRenameBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            var board = deps.getDbBoards().find(function(b) { return b.id === boardId; });
            var newName = prompt("Rename board:", board ? board.name : "");
            if (!newName || !newName.trim()) { hideBoardContextMenu(); return; }
            hideBoardContextMenu();
            deps.apiFetch("/api/kanban/boards/" + boardId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: newName.trim() })
            }).then(function() {
                deps.showSnackbar("Board renamed");
                deps.loadBoards(true);
                var currentBoard = deps.getCurrentBoard();
                if (currentBoard && currentBoard.id === boardId) deps.selectBoard("database", boardId);
            }).catch(function(e) { deps.showSnackbar("Rename failed: " + e.message, "error"); });
        }

        function ctxEditBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            hideBoardContextMenu();
            openBoardModal(boardId);
        }

        function ctxArchiveBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            hideBoardContextMenu();
            deps.apiFetch("/api/kanban/boards/" + boardId + "/archive", { method: "POST" }).then(function() {
                deps.showSnackbar("Board archived");
                var currentBoard = deps.getCurrentBoard();
                if (currentBoard && currentBoard.id === boardId) {
                    deps.clearCurrentBoard();
                    document.getElementById("kb-board-view").classList.add("hidden");
                    document.getElementById("kb-loading").classList.add("hidden");
                    document.getElementById("kb-empty").classList.remove("hidden");
                }
                deps.loadBoards(true);
            }).catch(function(e) { deps.showSnackbar("Archive failed: " + e.message, "error"); });
        }

        function ctxDeleteBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            var board = deps.getDbBoards().find(function(b) { return b.id === boardId; });
            var name = board ? board.name : "this board";
            if (!confirm('Delete board "' + name + '" and all its tickets? This cannot be undone.')) { hideBoardContextMenu(); return; }
            hideBoardContextMenu();
            deps.apiFetch("/api/kanban/boards/" + boardId, { method: "DELETE" }).then(function() {
                deps.showSnackbar("Board deleted");
                var currentBoard = deps.getCurrentBoard();
                if (currentBoard && currentBoard.id === boardId) {
                    deps.clearCurrentBoard();
                    document.getElementById("kb-board-view").classList.add("hidden");
                    document.getElementById("kb-loading").classList.add("hidden");
                    document.getElementById("kb-empty").classList.remove("hidden");
                }
                deps.loadBoards(true);
            }).catch(function(e) { deps.showSnackbar("Delete failed: " + e.message, "error"); });
        }

        function ctxActivateBoard() {
            var boardId = deps.getCtxMenuBoardId();
            if (!boardId) return;
            hideBoardContextMenu();
            deps.apiFetch("/api/kanban/boards/" + boardId + "/use", { method: "POST" }).then(function(data) {
                deps.showSnackbar("Board set as active");
                deps.loadBoards(true);
                if (data && data.linked_project) {
                    if (confirm('This board is linked to project "' + data.linked_project.name + '". Activate that project too?')) {
                        deps.apiFetch("/api/projects/" + data.linked_project.id + "/use", { method: "POST" }).then(function() {
                            deps.showSnackbar("Project activated");
                        }).catch(function() {});
                    }
                }
            }).catch(function(e) { deps.showSnackbar("Activate failed: " + e.message, "error"); });
        }

        function bindTopLevel() {
            document.querySelectorAll(".kb-src-tab").forEach(function(btn) {
                btn.addEventListener("click", function() { deps.switchSourceTab(btn.dataset.src); });
            });
            document.getElementById("kb-search").addEventListener("input", function() { deps.loadBoards(); });
            document.getElementById("kb-refresh-boards").addEventListener("click", function() {
                var btn = document.getElementById("kb-refresh-boards");
                if (!btn) return;
                var icon = btn.querySelector(".kb-refresh-icon");
                var prevTitle = btn.getAttribute("title");
                if (icon) icon.classList.add("animate-spin");
                btn.disabled = true;
                btn.setAttribute("title", "Re-syncing external boards...");
                deps.showSnackbar("Re-syncing external boards... this can take a bit on Jira.", "info");
                deps.resetExternalCache();
                deps.loadBoards(true);
                deps.getExternalBoards(true).then(function(data) {
                    deps.renderExternalBoards("kb-trello-boards", data.trello || [], "trello");
                    deps.renderExternalBoards("kb-jira-boards", data.jira || [], "jira");
                    deps.showSnackbar("External boards re-synced");
                    var cur = deps.getCurrentBoard && deps.getCurrentBoard();
                    if (cur && cur.id != null && cur.source) {
                        if (cur.source === "jira" || cur.source === "trello") {
                            deps.selectBoard(cur.source, cur.id, cur.extUrl || "", { forceRefresh: true });
                        } else if (cur.source === "database") {
                            deps.selectBoard("database", cur.id, "", { forceRefresh: true });
                        }
                    }
                }).catch(function(e) {
                    deps.showSnackbar("Re-sync failed: " + e.message, "error");
                }).finally(function() {
                    if (icon) icon.classList.remove("animate-spin");
                    btn.disabled = false;
                    btn.setAttribute("title", prevTitle || "Re-sync external boards");
                });
            });
        }

        function bindGlobalSettings() {
            document.getElementById("kb-settings-cog").addEventListener("click", openGlobalSettingsModal);
            document.getElementById("kb-gs-close").addEventListener("click", closeGlobalSettingsModal);
            document.getElementById("kb-gs-cancel").addEventListener("click", closeGlobalSettingsModal);
            document.getElementById("kb-gs-save").addEventListener("click", saveGlobalKanbanSettings);
            document.getElementById("kb-gs-frequency").addEventListener("change", function() {
                updateGsFrequencyUI(this.value);
            });
            document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                btn.addEventListener("click", function() {
                    var cur = btn.getAttribute("data-selected") === "1";
                    btn.setAttribute("data-selected", cur ? "0" : "1");
                });
            });
            var selectAllHoursBtn = document.getElementById("kb-gs-hours-select-all");
            if (selectAllHoursBtn) {
                selectAllHoursBtn.addEventListener("click", function() {
                    document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                        btn.setAttribute("data-selected", "1");
                    });
                });
            }
            document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
                btn.addEventListener("click", function() {
                    var cur = btn.getAttribute("data-selected") === "1";
                    btn.setAttribute("data-selected", cur ? "0" : "1");
                });
            });
            document.getElementById("kb-gs-sub-provider").addEventListener("change", function() {
                loadAgentModels("kb-gs-sub", this.value, "", "kanban");
                updateKanbanLlmDownloadButtons();
            });
            document.getElementById("kb-gs-coder-provider").addEventListener("change", function() {
                loadAgentModels("kb-gs-coder", this.value, "", "coding");
                updateKanbanLlmDownloadButtons();
            });

            var subDownload = document.getElementById("kb-gs-sub-download");
            if (subDownload) {
                subDownload.addEventListener("click", function() {
                    if (typeof window.openOllamaModelBrowser === "function") {
                        window.openOllamaModelBrowser("kanban");
                    } else {
                        deps.showSnackbar("Ollama browser is not available on this page.", "error");
                    }
                });
            }
            var coderDownload = document.getElementById("kb-gs-coder-download");
            if (coderDownload) {
                coderDownload.addEventListener("click", function() {
                    if (typeof window.openOllamaModelBrowser === "function") {
                        window.openOllamaModelBrowser("coding");
                    } else {
                        deps.showSnackbar("Ollama browser is not available on this page.", "error");
                    }
                });
            }

            // When the Ollama browser closes, refresh current model lists.
            var browserClose = document.getElementById("ollama_browser_modal_close");
            if (browserClose) {
                browserClose.addEventListener("click", function() {
                    var subProv = document.getElementById("kb-gs-sub-provider").value || "";
                    var coderProv = document.getElementById("kb-gs-coder-provider").value || "";
                    loadAgentModels("kb-gs-sub", subProv, document.getElementById("kb-gs-sub-model").value || "", "kanban");
                    loadAgentModels("kb-gs-coder", coderProv, document.getElementById("kb-gs-coder-model").value || "", "coding");
                });
            }
        }

        function updateKanbanLlmDownloadButtons() {
            var subProv = ((document.getElementById("kb-gs-sub-provider") || {}).value || "").toLowerCase();
            var coderProv = ((document.getElementById("kb-gs-coder-provider") || {}).value || "").toLowerCase();
            var subBtn = document.getElementById("kb-gs-sub-download");
            var coderBtn = document.getElementById("kb-gs-coder-download");
            if (subBtn) subBtn.classList.toggle("hidden", subProv !== "ollama");
            if (coderBtn) coderBtn.classList.toggle("hidden", coderProv !== "ollama");
        }

        function bindBoardActions() {
            document.getElementById("kb-add-ticket").addEventListener("click", deps.addTicket);
            document.getElementById("kb-edit-board").addEventListener("click", function() {
                deps.handleEditBoardClick();
            });
            document.getElementById("kb-delete-board").addEventListener("click", deleteBoard);
            document.querySelectorAll(".kb-bm-tab").forEach(function(btn) {
                btn.addEventListener("click", function() { switchBoardModalTab(btn.dataset.tab); });
            });
        }

        function bindBoardModal() {
            document.getElementById("kb-board-modal-cancel").addEventListener("click", closeBoardModal);
            document.getElementById("kb-board-modal-x").addEventListener("click", closeBoardModal);
            document.getElementById("kb-board-modal-save").addEventListener("click", saveBoardModal);
            document.getElementById("kb-board-modal-color").addEventListener("input", function() {
                document.getElementById("kb-board-modal-color-hex").textContent = this.value;
            });
            document.getElementById("kb-board-modal-color-reset").addEventListener("click", function() {
                var colorInput = document.getElementById("kb-board-modal-color");
                colorInput.value = "#f97316";
                document.getElementById("kb-board-modal-color-hex").textContent = "#f97316";
            });
        }

        return {
            populateProviderDropdowns: populateProviderDropdowns,
            loadAgentProviders: loadAgentProviders,
            loadAgentModels: loadAgentModels,
            loadBoardDefaults: loadBoardDefaults,
            openBoardModal: openBoardModal,
            populateBoardModal: populateBoardModal,
            openExternalBoardConfigModal: openExternalBoardConfigModal,
            switchBoardModalTab: switchBoardModalTab,
            closeBoardModal: closeBoardModal,
            saveBoardModal: saveBoardModal,
            deleteBoard: deleteBoard,
            loadGlobalKanbanSettings: loadGlobalKanbanSettings,
            saveGlobalKanbanSettings: saveGlobalKanbanSettings,
            openGlobalSettingsModal: openGlobalSettingsModal,
            closeGlobalSettingsModal: closeGlobalSettingsModal,
            updateGsFrequencyUI: updateGsFrequencyUI,
            updateTabBarVisibility: updateTabBarVisibility,
            getSidebarTabFromUrl: getSidebarTabFromUrl,
            switchSidebarTab: switchSidebarTab,
            showBoardContextMenu: showBoardContextMenu,
            ctxConfigureBoard: ctxConfigureBoard,
            hideBoardContextMenu: hideBoardContextMenu,
            ctxRenameBoard: ctxRenameBoard,
            ctxEditBoard: ctxEditBoard,
            ctxArchiveBoard: ctxArchiveBoard,
            ctxDeleteBoard: ctxDeleteBoard,
            ctxActivateBoard: ctxActivateBoard,
            bindTopLevel: bindTopLevel,
            bindGlobalSettings: bindGlobalSettings,
            bindBoardActions: bindBoardActions,
            bindBoardModal: bindBoardModal,
        };
    }

    window.KanbanBoardSettings = { create: create };
})();
