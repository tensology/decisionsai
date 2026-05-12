/**
 * Workflows UI — accordion-based step editor with tabbed step forms.
 * Each step expands inline; collapsing destroys the form DOM.
 * Steps have: Description, Action (instruction), Validation, Routing tabs.
 */
(function () {
    var API = "/api";
    var currentWorkflowId = null;
    var currentWorkflow = null;
    var expandedStepId = null;
    var activeStepTab = {};  // stepId -> active tab name
    var pollTimer = null;
    var lastKnownVersion = null;
    var versionPollTimer = null;
    var ws = null;
    var wsReconnectTimer = null;
    var activeRunsScope = "all";
    var wfContextMenuEl = null;
    var wfContextMenuId = null;
    var workflowRuntimeStateById = {};

    // Inline SVG icons (14x14, currentColor)
    var SVG_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    var SVG_FORWARD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 19 22 12 13 5 13 19"/><polygon points="2 19 11 12 2 5 2 19"/></svg>';
    var SVG_CANCEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
       var SVG_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    var SVG_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
    var SVG_PLAY_REC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M8 5v14l11-7z"/></svg>';

    function esc(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function lastItems(items, limit) {
        if (!Array.isArray(items)) return [];
        return items.slice(Math.max(0, items.length - limit));
    }

    function renderRunPacketEvidence(packet) {
        if (!packet || typeof packet !== "object") return "";
        var artifacts = packet.artifacts || {};
        var execution = packet.execution || {};
        var audit = packet.audit || {};
        var actionTrace = lastItems(execution.action_trace, 4);
        var validations = lastItems(execution.validation_snapshots, 4);
        var screenshots = lastItems(artifacts.screenshots, 3);
        var logs = lastItems(artifacts.logs, 3);
        var patches = lastItems(artifacts.diffs_or_patches, 3);
        var links = lastItems(artifacts.links, 3);
        var hasEvidence = packet.summary || audit.final_verdict || actionTrace.length || validations.length || screenshots.length || logs.length || patches.length || links.length;
        if (!hasEvidence) return "";

        var html = '<div class="wf-run-evidence mt-2 pt-2 border-t border-white/10" data-testid="wf-run-evidence">';
        html += '<div class="flex flex-wrap items-center gap-2 text-[11px]">';
        if (packet.summary) html += '<span class="text-gray-300">' + esc(packet.summary) + '</span>';
        if (audit.final_verdict) html += '<span class="px-1.5 py-0.5 rounded bg-white/10 text-gray-200">Verdict: ' + esc(audit.final_verdict) + '</span>';
        html += '</div>';

        if (actionTrace.length) {
            html += '<div class="mt-2" data-testid="wf-run-actions"><p class="text-[11px] text-gray-500 mb-1">Actions</p>';
            actionTrace.forEach(function (item) {
                var desc = item.description || "";
                var result = item.result ? " -> " + item.result : "";
                html += '<div class="text-[11px] text-gray-300 truncate">• ' + esc(item.action_type || "action") + ': ' + esc(desc + result) + '</div>';
            });
            html += '</div>';
        }

        if (validations.length) {
            html += '<div class="mt-2" data-testid="wf-run-validations"><p class="text-[11px] text-gray-500 mb-1">Validation</p>';
            validations.forEach(function (item) {
                var verdict = item.verdict || (item.verified_passed ? "pass" : "fail");
                var cls = verdict === "pass" ? "text-green-300" : "text-red-300";
                html += '<div class="text-[11px] text-gray-300 truncate"><span class="' + cls + '">' + esc(verdict) + '</span> ';
                html += '<span class="text-gray-500">(' + esc(item.validation_type || "none") + ')</span>';
                if (item.expected) html += ' ' + esc(item.expected);
                html += '</div>';
            });
            html += '</div>';
        }

        var artifactGroups = [
            ["Screenshots", screenshots],
            ["Logs", logs],
            ["Patches", patches],
            ["Links", links],
        ].filter(function (pair) { return pair[1].length; });
        if (artifactGroups.length) {
            html += '<div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-1" data-testid="wf-run-artifacts">';
            artifactGroups.forEach(function (pair) {
                html += '<div><span class="text-[11px] text-gray-500">' + pair[0] + ':</span> ';
                html += pair[1].map(function (value) {
                    return '<span class="text-[11px] text-blue-300 break-all">' + esc(value) + '</span>';
                }).join('<span class="text-gray-600">, </span>');
                html += '</div>';
            });
            html += '</div>';
        }
        html += '</div>';
        return html;
    }

    function snack(msg, type) {
        type = type || "success";
        var old = document.getElementById("wf-snackbar");
        if (old) old.remove();
        var el = document.createElement("div");
        el.id = "wf-snackbar";
        el.className = "fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999] px-5 py-3 rounded-lg shadow-lg text-white font-medium text-sm transition-opacity duration-300 " +
            (type === "error" ? "bg-red-600" : "bg-green-600");
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(function () { el.style.opacity = "0"; setTimeout(function () { el.remove(); }, 300); }, 3000);
    }

    function workflowFeedbackText(data, fallback) {
        if (!data || typeof data !== "object") return fallback || "Workflow updated";
        var msg = data.message || data.detail || fallback || "Workflow updated";
        if (data.next_action) msg += " " + data.next_action;
        return msg;
    }

    function workflowErrorText(err, fallback) {
        var data = err && err.workflowDetail;
        if (data && typeof data === "object") return workflowFeedbackText(data, fallback || "Workflow failed");
        return (err && err.message) || fallback || "Workflow failed";
    }

    function showConfirmModal(opts) {
        opts = opts || {};
        var title = opts.title || "Confirm";
        var message = opts.message || "Are you sure?";
        var confirmLabel = opts.confirmLabel || "Confirm";
        var onConfirm = opts.onConfirm || function () {};
        var existing = document.getElementById("wf-confirm-modal");
        if (existing) existing.remove();
        var html = '' +
            '<div id="wf-confirm-modal" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60">' +
                '<div class="w-full max-w-md mx-4 bg-[#1a1f3a] border border-white/20 rounded-xl p-4 shadow-2xl">' +
                    '<h3 class="text-white text-sm font-semibold mb-2">' + esc(title) + '</h3>' +
                    '<p class="text-sm text-gray-300 mb-4">' + esc(message) + '</p>' +
                    '<div class="flex items-center justify-end gap-2">' +
                        '<button type="button" class="wf-confirm-cancel px-3 py-1.5 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10">Cancel</button>' +
                        '<button type="button" class="wf-confirm-ok px-3 py-1.5 rounded border border-red-500/50 text-red-300 text-xs hover:bg-red-500/20">' + esc(confirmLabel) + '</button>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        var modal = document.getElementById("wf-confirm-modal");
        if (!modal) return;
        var keyHandler = null;
        function closeModal() {
            if (keyHandler) {
                document.removeEventListener("keydown", keyHandler, true);
                keyHandler = null;
            }
            modal.remove();
        }
        modal.addEventListener("click", function (evt) {
            if (evt.target === modal) closeModal();
        });
        var cancelBtn = modal.querySelector(".wf-confirm-cancel");
        var okBtn = modal.querySelector(".wf-confirm-ok");
        if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
        if (okBtn) okBtn.addEventListener("click", function () {
            closeModal();
            onConfirm();
        });
        keyHandler = function (evt) {
            if (!document.getElementById("wf-confirm-modal")) return;
            if (evt.key === "Escape") {
                evt.preventDefault();
                evt.stopPropagation();
                closeModal();
                return;
            }
            if (evt.key === "Enter") {
                evt.preventDefault();
                evt.stopPropagation();
                closeModal();
                onConfirm();
            }
        };
        document.addEventListener("keydown", keyHandler, true);
        if (okBtn) okBtn.focus();
    }

    function isTypingTarget(target) {
        if (!target) return false;
        var tag = (target.tagName || "").toUpperCase();
        if (target.isContentEditable) return true;
        if (tag === "TEXTAREA") return true;
        if (tag === "SELECT") return true;
        if (tag !== "INPUT") return false;
        var t = (target.type || "text").toLowerCase();
        // Treat text-like inputs as typing contexts.
        if (t === "button" || t === "submit" || t === "checkbox" || t === "radio" || t === "range" || t === "color" || t === "file") {
            return false;
        }
        return true;
    }

    function showInputModal(opts) {
        opts = opts || {};
        var title = opts.title || "Input";
        var message = opts.message || "";
        var placeholder = opts.placeholder || "";
        var confirmLabel = opts.confirmLabel || "Submit";
        var initialValue = opts.initialValue || "";
        var onConfirm = opts.onConfirm || function () {};

        var existing = document.getElementById("wf-input-modal");
        if (existing) existing.remove();
        var html = '' +
            '<div id="wf-input-modal" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60">' +
                '<div class="w-full max-w-xl mx-4 bg-[#1a1f3a] border border-white/20 rounded-xl p-4 shadow-2xl">' +
                    '<h3 class="text-white text-sm font-semibold mb-2">' + esc(title) + '</h3>' +
                    '<p class="text-sm text-gray-300 mb-3">' + esc(message) + '</p>' +
                    '<textarea class="wf-input-textarea w-full min-h-[120px] px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm placeholder-gray-500 resize-y" placeholder="' + esc(placeholder) + '">' + esc(initialValue) + '</textarea>' +
                    '<div class="mt-4 flex items-center justify-end gap-2">' +
                        '<button type="button" class="wf-input-cancel px-3 py-1.5 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10">Cancel</button>' +
                        '<button type="button" class="wf-input-ok px-3 py-1.5 rounded bg-[#f97316] text-white text-xs font-medium hover:bg-[#ea580c]">' + esc(confirmLabel) + '</button>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        var modal = document.getElementById("wf-input-modal");
        if (!modal) return;
        var textarea = modal.querySelector(".wf-input-textarea");
        function closeModal() { modal.remove(); }
        modal.addEventListener("click", function (evt) {
            if (evt.target === modal) closeModal();
        });
        var cancelBtn = modal.querySelector(".wf-input-cancel");
        var okBtn = modal.querySelector(".wf-input-ok");
        if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
        if (okBtn) okBtn.addEventListener("click", function () {
            var value = textarea ? textarea.value : "";
            closeModal();
            onConfirm(value);
        });
        if (textarea) {
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
        }
    }

    function deleteWorkflowById(workflowId) {
        if (!workflowId) return;
        showConfirmModal({
            title: "Delete workflow",
            message: "Delete this workflow? This cannot be undone.",
            confirmLabel: "Delete",
            onConfirm: function () {
                api("DELETE", "/workflows/" + workflowId)
                    .then(function () {
                        snack("Workflow deleted");
                        if (currentWorkflowId === workflowId) {
                            currentWorkflowId = null;
                            currentWorkflow = null;
                            expandedStepId = null;
                            document.getElementById("wf-detail").classList.add("hidden");
                            document.getElementById("wf-empty").classList.remove("hidden");
                        }
                        loadList();
                    })
                    .catch(function () { snack("Failed to delete workflow", "error"); });
            }
        });
    }

    /** Bulk delete all workflows (audit workflow kept). Shown from list row context menu only. */
    function performPurgeAll() {
        showConfirmModal({
            title: "Delete all workflows",
            message:
                "This permanently deletes every workflow in your library except the hidden audit workflow. Export anything you need first. Continue?",
            confirmLabel: "Delete all",
            onConfirm: function () {
                api("POST", "/workflows/purge-all", { confirm: true, include_audit: false })
                    .then(function (data) {
                        snack("Removed " + (data.removed != null ? data.removed : 0) + " workflow(s)");
                        currentWorkflowId = null;
                        currentWorkflow = null;
                        expandedStepId = null;
                        document.getElementById("wf-detail").classList.add("hidden");
                        document.getElementById("wf-empty").classList.remove("hidden");
                        loadList();
                    })
                    .catch(function (e) {
                        snack(e.message || "Purge failed", "error");
                    });
            }
        });
    }

    function api(method, path, body) {
        var opts = { method: method, headers: { "Content-Type": "application/json" } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(API + path, opts).then(function (r) {
            if (!r.ok) {
                return r.json()
                    .then(function (d) {
                        var detail = d && d.detail;
                        if (typeof detail === "string" && detail.trim()) {
                            var err = new Error(detail);
                            err.workflowDetail = d;
                            throw err;
                        }
                        if (Array.isArray(detail) && detail.length) {
                            var first = detail[0] || {};
                            var loc = Array.isArray(first.loc) ? first.loc.join(".") : "";
                            var msg = first.msg || "Request failed";
                            var validationErr = new Error(loc ? (loc + ": " + msg) : msg);
                            validationErr.workflowDetail = d;
                            throw validationErr;
                        }
                        if (detail && typeof detail === "object") {
                            var objectErr = new Error(JSON.stringify(detail));
                            objectErr.workflowDetail = d;
                            throw objectErr;
                        }
                        var genericErr = new Error("Request failed");
                        genericErr.workflowDetail = d;
                        throw genericErr;
                    })
                    .catch(function (e) {
                        if (e instanceof Error) throw e;
                        throw new Error("Request failed");
                    });
            }
            return r.json();
        });
    }

    function closeWorkflowContextMenu() {
        if (wfContextMenuEl) wfContextMenuEl.classList.add("hidden");
        wfContextMenuId = null;
    }

    function ensureWorkflowContextMenu() {
        if (wfContextMenuEl) return wfContextMenuEl;
        var html = '' +
            '<div id="wf-context-menu" class="hidden fixed z-[9999] min-w-[180px] bg-[#1a1f3a] border border-white/20 rounded-lg shadow-2xl py-1">' +
                '<button type="button" data-action="open" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Open</button>' +
                '<button type="button" data-action="run" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Run now</button>' +
                '<button type="button" data-action="duplicate" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Duplicate</button>' +
                '<button type="button" data-action="export" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Export preset</button>' +
                '<button type="button" data-action="download" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Download bundle</button>' +
                '<div class="my-1 border-t border-white/10"></div>' +
                '<button type="button" data-action="delete" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-red-500/20">Delete</button>' +
                '<div class="my-1 border-t border-white/10"></div>' +
                '<button type="button" data-action="purge-all" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-red-400/90 hover:bg-red-500/20">Delete all workflows…</button>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        wfContextMenuEl = document.getElementById("wf-context-menu");
        wfContextMenuEl.querySelectorAll(".wf-cm-action").forEach(function (btn) {
            btn.addEventListener("click", function (evt) {
                evt.stopPropagation();
                var action = btn.dataset.action;
                var workflowId = wfContextMenuId;
                closeWorkflowContextMenu();
                if (action === "purge-all") {
                    performPurgeAll();
                    return;
                }
                if (!workflowId) return;
                if (action === "open") {
                    selectWorkflow(workflowId);
                    return;
                }
                if (action === "run") {
                    api("POST", "/workflows/" + workflowId + "/run")
                        .then(function (data) { snack(workflowFeedbackText(data, "Workflow started")); if (currentWorkflowId === workflowId) startPolling(); loadList(); if (currentWorkflowId === workflowId) loadDetail(workflowId); })
                        .catch(function (e) { snack(workflowErrorText(e, "Run failed"), "error"); });
                    return;
                }
                if (action === "duplicate") {
                    api("POST", "/workflows/" + workflowId + "/duplicate")
                        .then(function (data) { snack("Workflow duplicated"); selectWorkflow(data.id); })
                        .catch(function () { snack("Failed to duplicate", "error"); });
                    return;
                }
                if (action === "export") {
                    api("POST", "/workflows/" + workflowId + "/export-preset")
                        .then(function (data) { snack("Exported as " + (data.filename || "preset")); checkPresetsExist(); })
                        .catch(function () { snack("Failed to export", "error"); });
                    return;
                }
                if (action === "download") {
                    window.location.href = API + "/workflows/" + workflowId + "/export";
                    return;
                }
                if (action === "delete") {
                    deleteWorkflowById(workflowId);
                }
            });
        });
        return wfContextMenuEl;
    }

    function openWorkflowContextMenu(evt, workflowId) {
        evt.preventDefault();
        evt.stopPropagation();
        var menu = ensureWorkflowContextMenu();
        wfContextMenuId = workflowId;
        menu.classList.remove("hidden");
        menu.style.left = evt.clientX + "px";
        menu.style.top = evt.clientY + "px";
        var rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth - 8) menu.style.left = Math.max(8, window.innerWidth - rect.width - 8) + "px";
        if (rect.bottom > window.innerHeight - 8) menu.style.top = Math.max(8, window.innerHeight - rect.height - 8) + "px";
    }

    // ── Workflow list ──
    function loadList() {
        var search = (document.getElementById("wf-search") || {}).value || "";
        api("GET", "/workflows?limit=50" + (search ? "&search=" + encodeURIComponent(search) : ""))
            .then(function (data) {
                var el = document.getElementById("wf-list");
                if (!data.length) {
                    el.innerHTML = '<p class="text-sm text-gray-500">' + (search ? "No workflows match your search." : "No workflows yet.") + "</p>";
                    return;
                }
                el.innerHTML = data.map(function (w) {
                    var active = currentWorkflowId === w.id ? " border-[#f97316] bg-white/10" : " border-transparent hover:bg-white/5";
                    var badge = w.schedule_enabled ? '<span class="text-xs px-1.5 py-0.5 rounded bg-blue-600/40 text-blue-300 ml-auto">' + esc(w.schedule_preset || "sched") + "</span>" : "";
                    var state = workflowRuntimeStateById[w.id] || {};
                    var dotClass = "bg-gray-500";
                    if (state.status === "waiting") dotClass = "bg-yellow-400";
                    else if (state.status === "running") dotClass = "bg-blue-400";
                    return '<div class="flex items-center gap-2 rounded border px-3 py-2 cursor-pointer' + active + '" data-id="' + w.id + '">' +
                        '<span class="w-2 h-2 rounded-full ' + dotClass + ' flex-shrink-0"></span>' +
                        '<span class="text-sm text-white truncate">' + esc(w.name) + '</span>' +
                        '<span class="ml-auto inline-flex items-center gap-1.5">' +
                            badge +
                            '<span class="text-xs text-gray-500">' + (w.step_count || 0) + '</span>' +
                        '</span>' +
                        '</div>';
                }).join("");
                el.querySelectorAll("[data-id]").forEach(function (row) {
                    row.addEventListener("click", function () { selectWorkflow(parseInt(row.dataset.id, 10)); });
                    row.addEventListener("contextmenu", function (evt) {
                        openWorkflowContextMenu(evt, parseInt(row.dataset.id, 10));
                    });
                });

                // Auto-select last workflow or first in list if nothing selected yet
                if (!currentWorkflowId && data.length) {
                    var lastId = null;
                    try { lastId = parseInt(localStorage.getItem("wf_last_selected"), 10); } catch (e) {}
                    var match = lastId && data.some(function (w) { return w.id === lastId; });
                    selectWorkflow(match ? lastId : data[0].id);
                }
            }).catch(function (e) {
                console.error("Load workflows failed", e);
            });
    }

    function selectWorkflow(id) {
        currentWorkflowId = id;
        expandedStepId = null;
        try { localStorage.setItem("wf_last_selected", id); } catch (e) {}
        loadList();
        loadDetail(id);
    }

    function loadDetail(id) {
        api("GET", "/workflows/" + id).then(function (data) {
            currentWorkflow = data;
            document.getElementById("wf-empty").classList.add("hidden");
            document.getElementById("wf-detail").classList.remove("hidden");
            document.getElementById("wf-detail-name").value = data.name || "";
            renderSteps(data.steps || []);
            renderSchedule(data);
            renderRuns(data.runs || []);
            loadActiveRuns();
            renderContextRules(data);
            checkActiveRun();
        }).catch(function () { snack("Failed to load workflow", "error"); });
    }

    // Soft refresh — only update step statuses/results without rebuilding DOM
    function softRefresh() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId).then(function (data) {
            currentWorkflow = data;
            var steps = data.steps || [];
            // Full re-render keeps button/active-step/routing transitions in sync.
            renderSteps(steps);
            renderRuns(data.runs || []);
            // Update live card state so buttons/highlights follow active step.
            steps.forEach(function (s) {
                applyLiveStepCardState(s, steps);
                var card = document.querySelector('.step-card[data-step-id="' + s.id + '"]');
                if (!card) return;
                // Refresh history tab if it's currently visible
                if (expandedStepId === s.id && activeStepTab[s.id] === "history") {
                    var histContainer = card.querySelector(".sf-history-tab-list");
                    if (histContainer) loadStepHistory(s.id, histContainer);
                }
                // When a running/waiting step transitions to a terminal state,
                // auto-load or refresh history if the step is expanded
                if (expandedStepId === s.id && (s.status === "passed" || s.status === "failed" || s.status === "cancelled")) {
                    var wasRunning = card.querySelector(".sh-stop") || card.querySelector(".sh-continue-waiting");
                    if (wasRunning) {
                        // Step just finished — auto-switch to History tab
                        activeStepTab[s.id] = "history";
                        buildStepForm(s, steps);
                    } else if (activeStepTab[s.id] !== "history") {
                        // Step was already terminal but expanded — refresh history anyway
                        var histContainer2 = card.querySelector(".sf-history-tab-list");
                        if (histContainer2) loadStepHistory(s.id, histContainer2);
                    }
                }
            });
            loadActiveRuns();
            checkActiveRun();
        }).catch(function () {});
    }

    function formatElapsed(seconds) {
        var total = parseInt(seconds, 10) || 0;
        var h = Math.floor(total / 3600);
        var m = Math.floor((total % 3600) / 60);
        var s = total % 60;
        if (h > 0) return h + "h " + String(m).padStart(2, "0") + "m";
        if (m > 0) return m + "m " + String(s).padStart(2, "0") + "s";
        return s + "s";
    }

    function colorForBoard(boardId, index) {
        var palette = ["#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#ef4444", "#14b8a6"];
        var n = parseInt(boardId, 10);
        if (!isNaN(n) && isFinite(n)) return palette[Math.abs(n) % palette.length];
        return palette[index % palette.length];
    }

    function renderBoardConsumers(activeRuns) {
        var el = document.getElementById("wf-board-consumers");
        if (!el) return;
        var runs = Array.isArray(activeRuns) ? activeRuns : [];
        var scoped = runs.filter(function (r) {
            return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
        });
        var seen = {};
        var boards = [];
        scoped.forEach(function (r) {
            var bid = r && r.board_id;
            if (!bid || seen[bid]) return;
            seen[bid] = true;
            boards.push({
                id: bid,
                name: r.board_name || ("Board #" + bid)
            });
        });
        if (!boards.length) {
            el.classList.add("hidden");
            el.innerHTML = "";
            return;
        }
        var dots = boards.slice(0, 6).map(function (b, idx) {
            var color = colorForBoard(b.id, idx);
            return '<span title="' + esc(b.name) + '" class="inline-block w-2.5 h-2.5 rounded-full border border-black/20" style="background:' + color + ';"></span>';
        }).join("");
        var extra = boards.length > 6 ? ('<span class="text-[10px] text-gray-300">+' + (boards.length - 6) + '</span>') : "";
        el.className = "inline-flex items-center gap-1 px-2 py-1 rounded border border-white/15 bg-white/5";
        el.innerHTML = '<span class="text-[10px] text-gray-300">Boards</span>' + dots + extra + '<span class="text-[10px] text-white/90 ml-0.5">' + boards.length + '</span>';
    }

    function loadActiveRuns() {
        var listEl = document.getElementById("wf-active-runs-list");
        var emptyEl = document.getElementById("wf-active-runs-empty");
        if (!listEl || !emptyEl) return;
        var query = "/workflows/active-runs?limit=50";
        if (activeRunsScope === "current" && currentWorkflowId) {
            query += "&workflow_id=" + encodeURIComponent(currentWorkflowId);
        }
        api("GET", query).then(function (runs) {
            var stateByWorkflow = {};
            (runs || []).forEach(function (r) {
                if (!r || !r.workflow_id) return;
                var prev = stateByWorkflow[r.workflow_id];
                // waiting outranks running for stronger attention signal
                if (!prev || prev === "running" || r.status === "waiting") {
                    if (r.status === "waiting" || r.status === "running") {
                        stateByWorkflow[r.workflow_id] = r.status;
                    }
                }
            });
            workflowRuntimeStateById = Object.keys(stateByWorkflow).reduce(function (acc, k) {
                acc[k] = { status: stateByWorkflow[k] };
                return acc;
            }, {});
            loadList();
            renderBoardConsumers(runs || []);
            if (!runs.length) {
                listEl.innerHTML = "";
                emptyEl.classList.remove("hidden");
                return;
            }
            emptyEl.classList.add("hidden");
            listEl.innerHTML = runs.map(function (r) {
                var isCurrentWorkflow = currentWorkflowId && String(currentWorkflowId) === String(r.workflow_id);
                var statusColor = r.status === "waiting" ? "text-amber-300 bg-amber-600/20" : "text-blue-300 bg-blue-600/20";
                var phase = r.phase ? String(r.phase) : "planning";
                var boardText = r.board_name || (r.board_id ? ("Board #" + r.board_id) : "No board");
                var ticketText = r.ticket_title || (r.ticket_id ? ("Ticket #" + r.ticket_id) : "No ticket");
                var projectText = r.project_name || (r.project_id ? ("Project #" + r.project_id) : "No project");
                var stepText = r.current_step_name || (r.current_step_id ? ("Step #" + r.current_step_id) : "Starting");
                var workflowText = r.workflow_name || ("Workflow #" + r.workflow_id);
                var rowCls = "rounded px-3 py-2 border border-white/10 " + (isCurrentWorkflow ? "wf-live-run" : "bg-[#152054]/50");
                return '<div class="' + rowCls + '">' +
                    '<div class="flex items-center gap-2 mb-1">' +
                        '<span class="text-xs text-gray-400">Run #' + r.id + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded ' + statusColor + '">' + esc(r.status) + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded bg-green-600/20 text-green-300">' + esc(phase) + '</span>' +
                        '<span class="text-xs text-gray-500 ml-auto">Elapsed ' + esc(formatElapsed(r.elapsed_seconds)) + '</span>' +
                    '</div>' +
                    '<div class="grid grid-cols-1 md:grid-cols-2 gap-1 text-xs">' +
                        '<div><span class="text-gray-500">Board:</span> <span class="text-gray-200">' + esc(boardText) + '</span></div>' +
                        '<div><span class="text-gray-500">Ticket:</span> <span class="text-gray-200">' + esc(ticketText) + '</span></div>' +
                        '<div><span class="text-gray-500">Project:</span> <span class="text-gray-200">' + esc(projectText) + '</span></div>' +
                        '<div><span class="text-gray-500">Workflow:</span> <span class="text-gray-200">' + esc(workflowText) + '</span></div>' +
                        '<div><span class="text-gray-500">Current step:</span> <span class="text-gray-200">' + esc(stepText) + '</span></div>' +
                    '</div>' +
                '</div>';
            }).join("");
        }).catch(function () {});
    }

    function statusBadgeClass(status) {
        var m = { pending: "bg-white/10 text-gray-400", running: "bg-blue-600/40 text-blue-300 animate-pulse",
            passed: "bg-green-600/40 text-green-300", failed: "bg-red-600/40 text-red-300",
            cancelled: "bg-gray-600/40 text-gray-400", skipped: "bg-yellow-600/40 text-yellow-300",
            waiting: "bg-amber-600/40 text-amber-300 animate-pulse" };
        return m[status] || "bg-white/10 text-gray-400";
    }

    function shouldSuppressCancelledText(status, text) {
        if (status !== "cancelled") return false;
        var normalized = String(text || "").trim().toLowerCase();
        return normalized === "cancelled by user" || normalized === "canceled by user";
    }

    function stepCardClass(status, isOpen) {
        var base = "step-card border rounded-lg";
        var open = isOpen ? " expanded" : "";
        if (status === "running") return base + " border-green-500/60 shadow-[0_0_0_1px_rgba(34,197,94,0.35)]" + open;
        if (status === "waiting") return base + " border-amber-500/60 shadow-[0_0_0_1px_rgba(245,158,11,0.35)]" + open;
        return base + " border-white/20" + open;
    }

    function headerButtonsHtml(step) {
        if (step.status === "waiting") {
            return '<button type="button" class="sh-continue-waiting inline-flex items-center justify-center w-6 h-6 rounded border border-amber-500/50 text-amber-400 hover:bg-amber-500/20" data-step-id="' + step.id + '" title="Continue">' + SVG_FORWARD + '</button>' +
                '<button type="button" class="sh-cancel inline-flex items-center justify-center w-6 h-6 rounded border border-red-500/50 text-red-400 hover:bg-red-500/20" data-step-id="' + step.id + '" title="Cancel">' + SVG_CANCEL + '</button>' +
                '<button type="button" class="sh-delete inline-flex items-center justify-center w-6 h-6 rounded border border-red-500/40 text-red-300 hover:bg-red-500/20" data-step-id="' + step.id + '" title="Delete step">' + SVG_TRASH + '</button>';
        }
        if (step.status === "running") {
            return '<button type="button" class="sh-stop inline-flex items-center justify-center w-6 h-6 rounded border border-orange-500/50 text-orange-400 hover:bg-orange-500/20" data-step-id="' + step.id + '" title="Stop Step">' + SVG_STOP + '</button>' +
                '<button type="button" class="sh-delete inline-flex items-center justify-center w-6 h-6 rounded border border-red-500/40 text-red-300 hover:bg-red-500/20" data-step-id="' + step.id + '" title="Delete step">' + SVG_TRASH + '</button>';
        }
        return '<button type="button" class="sh-run-isolated inline-flex items-center justify-center w-6 h-6 rounded border border-blue-500/50 text-blue-400 hover:bg-blue-500/20" data-step-id="' + step.id + '" title="Run Isolated">' + SVG_PLAY + '</button>' +
            '<button type="button" class="sh-run-continue inline-flex items-center justify-center w-6 h-6 rounded border border-green-500/50 text-green-400 hover:bg-green-500/20" data-step-id="' + step.id + '" title="Continue From Here">' + SVG_FORWARD + '</button>' +
            '<button type="button" class="sh-delete inline-flex items-center justify-center w-6 h-6 rounded border border-red-500/40 text-red-300 hover:bg-red-500/20" data-step-id="' + step.id + '" title="Delete step">' + SVG_TRASH + '</button>';
    }

    function bindStepHeaderActionHandlers(scopeEl) {
        if (!scopeEl) return;
        scopeEl.querySelectorAll(".sh-run-isolated").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); executeStep(parseInt(btn.dataset.stepId, 10)); });
        });
        scopeEl.querySelectorAll(".sh-run-continue").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); runFromStep(parseInt(btn.dataset.stepId, 10)); });
        });
        scopeEl.querySelectorAll(".sh-cancel").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); cancelStep(parseInt(btn.dataset.stepId, 10)); });
        });
        scopeEl.querySelectorAll(".sh-stop").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); stopStep(parseInt(btn.dataset.stepId, 10)); });
        });
        scopeEl.querySelectorAll(".sh-continue-waiting").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                continueWaitingRun();
            });
        });
        scopeEl.querySelectorAll(".sh-delete").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                var stepId = parseInt(btn.dataset.stepId, 10);
                confirmDeleteStep(stepId);
            });
        });
    }

    function applyLiveStepCardState(step, steps) {
        var card = document.querySelector('.step-card[data-step-id="' + step.id + '"]');
        if (!card) return;

        var isOpen = expandedStepId === step.id;
        card.className = stepCardClass(step.status, isOpen);

        var badge = card.querySelector(".step-status-badge");
        if (step.status && step.status !== "pending") {
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "step-status-badge text-xs px-1.5 py-0.5 rounded";
                var typePill = card.querySelector(".step-header .text-xs.bg-white\\/10");
                if (typePill && typePill.parentNode) typePill.parentNode.insertBefore(badge, typePill);
            }
            badge.textContent = step.status;
            badge.className = "step-status-badge text-xs px-1.5 py-0.5 rounded " + statusBadgeClass(step.status);
        } else if (badge) {
            badge.remove();
        }

        var actionsWrap = card.querySelector(".step-header-actions");
        if (actionsWrap) {
            actionsWrap.innerHTML = headerButtonsHtml(step);
            bindStepHeaderActionHandlers(actionsWrap);
        }
    }

    function markStepAsRunningLocally(stepId) {
        if (!currentWorkflow || !currentWorkflow.steps) return;
        var found = false;
        currentWorkflow.steps.forEach(function (s) {
            if (s.id === stepId) {
                s.status = "running";
                found = true;
            } else if (s.status === "running" || s.status === "waiting") {
                s.status = "pending";
            }
        });
        if (found) renderSteps(currentWorkflow.steps || []);
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(softRefresh, 3000);
    }
    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    // ── Version-based polling (fallback for when WebSocket is unavailable) ──
    function startVersionPolling() {
        stopVersionPolling();
        versionPollTimer = setInterval(checkVersion, 3000);
    }
    function stopVersionPolling() {
        if (versionPollTimer) { clearInterval(versionPollTimer); versionPollTimer = null; }
    }
    function checkVersion() {
        api("GET", "/workflows/version").then(function (data) {
            if (lastKnownVersion !== null && data.version !== lastKnownVersion) {
                // Version changed — refresh data
                loadList();
                if (currentWorkflowId) softRefresh();
            }
            lastKnownVersion = data.version;
        }).catch(function () {});
    }

    // ── WebSocket for real-time workflow updates ──
    function connectWebSocket() {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + location.host + "/api/ws/workflows";
        try {
            ws = new WebSocket(url);
        } catch (e) {
            console.warn("WebSocket connect failed, falling back to polling", e);
            startVersionPolling();
            return;
        }
        ws.onopen = function () {
            console.log("Workflow WS: connected");
            // WebSocket is live — stop version polling to avoid duplicate refreshes
            stopVersionPolling();
            if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        };
        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                if (msg.type === "workflow_updated") {
                    loadList();
                    if (currentWorkflowId) softRefresh();
                }
                // Ignore ping/pong and unknown message types
            } catch (e) {
                console.warn("Workflow WS: bad message", e);
            }
        };
        ws.onclose = function () {
            console.log("Workflow WS: disconnected, will reconnect");
            ws = null;
            // Fall back to polling while disconnected, then try to reconnect
            startVersionPolling();
            wsReconnectTimer = setTimeout(connectWebSocket, 5000);
        };
        ws.onerror = function () {
            // onclose will fire after onerror, so reconnect is handled there
            console.warn("Workflow WS: error");
        };
    }

    function checkActiveRun() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId + "/active-run").then(function (data) {
            var runBar = document.getElementById("wf-run-bar");
            if (data && data.id) {
                var phase = data.phase ? String(data.phase) : "planning";
                var boardText = data.board_name || (data.board_id ? ("Board #" + data.board_id) : "No board");
                var ticketText = data.ticket_title || (data.ticket_id ? ("Ticket #" + data.ticket_id) : "No ticket");
                var stepText = data.current_step_name || (data.current_step_id ? ("Step #" + data.current_step_id) : "Starting");
                var detailStrip = '<div class="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px] mt-1">' +
                    '<div><span class="text-gray-500">Board:</span> <span class="text-gray-200">' + esc(boardText) + '</span></div>' +
                    '<div><span class="text-gray-500">Ticket:</span> <span class="text-gray-200">' + esc(ticketText) + '</span></div>' +
                    '<div><span class="text-gray-500">Current step:</span> <span class="text-gray-200">' + esc(stepText) + '</span></div>' +
                '</div>';
                if (data.status === "waiting") {
                    runBar.innerHTML = '<div class="px-4 py-2 bg-amber-900/30 border-b border-amber-500/30">' +
                        '<div class="flex items-center gap-3">' +
                            '<span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>' +
                            '<span class="text-xs text-amber-300">Waiting for continue... (phase: ' + esc(phase) + ')</span>' +
                            '<button type="button" class="wf-open-runs-tab px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10">Open Runs Tab</button>' +
                            '<button type="button" class="wf-continue-run px-2 py-1 rounded border border-amber-500/50 text-amber-400 text-xs hover:bg-amber-500/20" data-run-id="' + data.id + '">Continue</button>' +
                            '<button type="button" class="wf-cancel-run ml-auto inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-run-id="' + data.id + '">' + SVG_STOP + '<span>Stop Run</span></button>' +
                        '</div>' +
                        detailStrip +
                    '</div>';
                    runBar.querySelector(".wf-open-runs-tab").addEventListener("click", function () {
                        switchTab("runs");
                        loadActiveRuns();
                    });
                    runBar.querySelector(".wf-continue-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/runs/" + data.id + "/continue")
                            .then(function (resp) { snack(workflowFeedbackText(resp, "Run continued")); startPolling(); loadDetail(currentWorkflowId); })
                            .catch(function (e) { snack(workflowErrorText(e, "Failed to continue"), "error"); });
                    });
                    runBar.querySelector(".wf-cancel-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/cancel-run/" + data.id)
                            .then(function (resp) { snack(workflowFeedbackText(resp, "Run cancelled")); stopPolling(); loadDetail(currentWorkflowId); })
                            .catch(function (e) { snack(workflowErrorText(e, "Failed to cancel"), "error"); });
                    });
                    startPolling();
                } else {
                    runBar.innerHTML = '<div class="px-4 py-2 bg-blue-900/30 border-b border-blue-500/30">' +
                        '<div class="flex items-center gap-3">' +
                            '<span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>' +
                            '<span class="text-xs text-blue-300">Workflow running... (phase: ' + esc(phase) + ')</span>' +
                            '<button type="button" class="wf-open-runs-tab px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10">Open Runs Tab</button>' +
                            '<button type="button" class="wf-cancel-run ml-auto inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-run-id="' + data.id + '">' + SVG_STOP + '<span>Stop Run</span></button>' +
                        '</div>' +
                        detailStrip +
                    '</div>';
                    runBar.querySelector(".wf-open-runs-tab").addEventListener("click", function () {
                        switchTab("runs");
                        loadActiveRuns();
                    });
                    runBar.querySelector(".wf-cancel-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/cancel-run/" + data.id)
                            .then(function (resp) { snack(workflowFeedbackText(resp, "Run cancelled")); stopPolling(); loadDetail(currentWorkflowId); })
                            .catch(function (e) { snack(workflowErrorText(e, "Failed to cancel"), "error"); });
                    });
                    startPolling();
                }
            } else {
                runBar.innerHTML = "";
                stopPolling();
            }
            loadActiveRuns();
        }).catch(function () {});
    }

    function reorderStepsLocally(steps, draggedId, targetId) {
        if (!Array.isArray(steps)) return null;
        if (!draggedId || !targetId || draggedId === targetId) return null;
        var draggedIndex = steps.findIndex(function (s) { return s.id === draggedId; });
        var targetIndex = steps.findIndex(function (s) { return s.id === targetId; });
        if (draggedIndex < 0 || targetIndex < 0) return null;
        var reordered = steps.slice();
        var moved = reordered.splice(draggedIndex, 1)[0];
        reordered.splice(targetIndex, 0, moved);
        reordered.forEach(function (s, idx) { s.position = idx; });
        return reordered;
    }

    function persistStepOrder(workflowId, steps) {
        var orderedIds = (steps || []).map(function (s) { return s.id; });
        return api("PATCH", "/workflows/" + workflowId + "/steps/reorder", { step_ids: orderedIds });
    }

    function enableStepDragAndDrop(steps) {
        var listEl = document.getElementById("wf-steps-list");
        if (!listEl) return;

        var draggingStepId = null;
        var cards = listEl.querySelectorAll(".step-card");
        cards.forEach(function (card) {
            var stepId = parseInt(card.dataset.stepId, 10);
            var handle = card.querySelector(".step-drag-grip");
            if (!handle || !stepId) return;

            handle.setAttribute("draggable", "true");
            handle.classList.add("step-drag-handle");
            handle.addEventListener("click", function (evt) {
                evt.stopPropagation();
            });

            handle.addEventListener("dragstart", function (evt) {
                draggingStepId = stepId;
                card.classList.add("step-card-dragging");
                if (evt.dataTransfer) {
                    evt.dataTransfer.effectAllowed = "move";
                    evt.dataTransfer.setData("text/plain", String(stepId));
                }
            });

            handle.addEventListener("dragend", function () {
                draggingStepId = null;
                listEl.querySelectorAll(".step-card-drop-target").forEach(function (el) {
                    el.classList.remove("step-card-drop-target");
                });
                listEl.querySelectorAll(".step-card-dragging").forEach(function (el) {
                    el.classList.remove("step-card-dragging");
                });
            });

            card.addEventListener("dragover", function (evt) {
                if (!draggingStepId || draggingStepId === stepId) return;
                evt.preventDefault();
                card.classList.add("step-card-drop-target");
                if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
            });

            card.addEventListener("dragleave", function () {
                card.classList.remove("step-card-drop-target");
            });

            card.addEventListener("drop", function (evt) {
                evt.preventDefault();
                card.classList.remove("step-card-drop-target");
                if (!draggingStepId || draggingStepId === stepId) return;
                var reordered = reorderStepsLocally(steps, draggingStepId, stepId);
                if (!reordered) return;
                if (currentWorkflow) currentWorkflow.steps = reordered;
                renderSteps(reordered);
                persistStepOrder(currentWorkflowId, reordered)
                    .then(function () {
                        snack("Step order updated");
                    })
                    .catch(function (e) {
                        snack(e.message || "Failed to reorder steps", "error");
                        loadDetail(currentWorkflowId);
                    });
            });
        });
    }

    // ── Steps accordion ──
    function renderSteps(steps) {
        var el = document.getElementById("wf-steps-list");
        if (!steps.length) {
            el.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">No steps yet. Click "+ Add Step" to begin.</p>';
            return;
        }
        el.innerHTML = steps.map(function (s) {
            var isOpen = expandedStepId === s.id;
            var typeLabel = { agent_instruction: "Agent", computer_use: "Computer Use", play_recording: "Recording", run_command: "Command", send_to_project_cli: "Project CLI", http_request: "HTTP", execute_code: "Code", playwright: "Playwright" }[s.action_type] || s.action_type;
            var chevronCls = isOpen ? "chevron open" : "chevron";
            var statusCls = statusBadgeClass(s.status);
            var showStepStatusBadge = !!s.status && s.status !== "pending";
            return '<div class="' + stepCardClass(s.status, isOpen) + '" data-step-id="' + s.id + '">' +
                '<div class="step-header flex items-center gap-3 px-4 py-3" data-step-id="' + s.id + '">' +
                    '<span class="step-drag-grip text-xs" title="Drag to reorder">⋮⋮</span>' +
                    '<span class="' + chevronCls + ' text-gray-400 text-xs">▶</span>' +
                    '<span class="text-xs text-gray-500">#' + (parseInt(s.position, 10) + 1) + '</span>' +
                    '<span class="text-sm font-medium text-white flex-1 truncate">' + esc(s.name) + '</span>' +
                    (showStepStatusBadge ? ('<span class="step-status-badge text-xs px-1.5 py-0.5 rounded ' + statusCls + '">' + esc(s.status) + '</span>') : '') +
                    '<span class="text-xs px-1.5 py-0.5 rounded bg-white/10 text-gray-400">' + esc(typeLabel) + '</span>' +
                    '<span class="step-header-actions inline-flex items-center gap-1">' + headerButtonsHtml(s) + '</span>' +
                '</div>' +
                '<div class="step-body' + (isOpen ? "" : " hidden") + '" id="step-body-' + s.id + '"></div>' +
            '</div>';
        }).join("");

        el.querySelectorAll(".step-header").forEach(function (hdr) {
            hdr.addEventListener("click", function () {
                toggleStep(parseInt(hdr.dataset.stepId, 10), steps);
            });
        });

        // Header action buttons (stopPropagation so they don't toggle accordion)
        bindStepHeaderActionHandlers(el);
        enableStepDragAndDrop(steps);

        if (expandedStepId) {
            var openStep = steps.find(function (s) { return s.id === expandedStepId; });
            if (openStep) buildStepForm(openStep, steps);
        }
    }

    function toggleStep(stepId, steps) {
        expandedStepId = (expandedStepId === stepId) ? null : stepId;
        renderSteps(steps);
    }

    function buildStepForm(step, allSteps) {
        var container = document.getElementById("step-body-" + step.id);
        if (!container) return;
        var tab = activeStepTab[step.id] || "action";

        // Routing options — default is now "End workflow"
        var routeOpts = '<option value="">End workflow (default)</option><option value="-1">End workflow (explicit)</option>';
        allSteps.forEach(function (s) {
            if (s.id !== step.id) routeOpts += '<option value="' + s.id + '">' + esc(s.name) + ' (#' + s.position + ')</option>';
        });
        var passVal = step.on_pass_goto == null ? "" : step.on_pass_goto;
        var failVal = step.on_fail_goto == null ? "" : step.on_fail_goto;

        var html = '<div class="border-t border-white/10">';

        // Step inner tabs
        html += '<div class="flex border-b border-white/10 px-4">';
        ["action", "validation", "routing", "history"].forEach(function (t) {
            var label = { action: "Action", validation: "Validation", routing: "Routing", history: "History" }[t];
            var cls = tab === t ? "border-b-2 border-[#f97316] text-white" : "text-gray-500 hover:text-gray-300";
            html += '<button type="button" class="sf-tab px-3 py-2 text-xs font-medium ' + cls + '" data-tab="' + t + '">' + label + '</button>';
        });
        html += '</div>';

        html += '<div class="px-4 pb-4 pt-3 space-y-3">';

        // ── ACTION TAB ──
        html += '<div class="sf-tab-content' + (tab !== "action" ? " hidden" : "") + '" data-tab-content="action">';
        // Name row
        html += '<div class="grid grid-cols-2 gap-3">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Step Name</label>' +
            '<input type="text" class="sf-name w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + esc(step.name) + '"></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Type</label>' +
            '<select class="sf-action-type w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            '<option value="agent_instruction"' + (step.action_type === "agent_instruction" ? " selected" : "") + '>Agent Instruction</option>' +
            '<option value="computer_use"' + (step.action_type === "computer_use" ? " selected" : "") + '>Computer Use</option>' +
            '<option value="play_recording"' + (step.action_type === "play_recording" ? " selected" : "") + '>Play Recording</option>' +
            '<option value="run_command"' + (step.action_type === "run_command" ? " selected" : "") + '>Run Command</option>' +
            '<option value="send_to_project_cli"' + (step.action_type === "send_to_project_cli" ? " selected" : "") + '>Send to Project CLI</option>' +
            '<option value="http_request"' + (step.action_type === "http_request" ? " selected" : "") + '>HTTP Request</option>' +
            '<option value="execute_code"' + (step.action_type === "execute_code" ? " selected" : "") + '>Execute Code</option>' +
            '<option value="playwright"' + (step.action_type === "playwright" ? " selected" : "") + '>Playwright</option>' +
            '</select></div>';
        html += '</div>';
        var isRecording = step.action_type === "play_recording";
        var isCodeType = step.action_type === "execute_code" || step.action_type === "playwright";
        // Instruction (hidden for play_recording)
        html += '<div class="sf-instruction-wrap' + (isRecording ? " hidden" : "") + '">' +
            '<div><label class="block text-xs text-gray-500 mb-1">Description</label>' +
            '<textarea class="sf-desc w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm h-12 resize-none" placeholder="Brief description of what this step does...">' + esc(step.description) + '</textarea></div>';
        if (isCodeType) {
            // Dual-tab sub-editor for execute_code / playwright
            var codeSubTab = (step.code && step.code.trim()) ? "code" : "instruction";
            html += '<div class="sf-code-editor">';
            html += '<div class="flex border-b border-white/10 mb-2">';
            html += '<button type="button" class="sf-code-subtab px-3 py-1.5 text-xs font-medium ' + (codeSubTab === "instruction" ? "border-b-2 border-[#f97316] text-white" : "text-gray-500 hover:text-gray-300") + '" data-subtab="instruction">Instruction</button>';
            html += '<button type="button" class="sf-code-subtab px-3 py-1.5 text-xs font-medium ' + (codeSubTab === "code" ? "border-b-2 border-[#f97316] text-white" : "text-gray-500 hover:text-gray-300") + '" data-subtab="code">Code</button>';
            html += '</div>';
            // Instruction sub-tab
            html += '<div class="sf-code-subtab-content' + (codeSubTab !== "instruction" ? " hidden" : "") + '" data-subtab-content="instruction">';
            html += '<textarea class="sf-instruction w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm font-mono resize-y" style="min-height:120px" placeholder="Enter the instruction for the agent...">' + esc(step.instruction) + '</textarea>';
            html += '<button type="button" class="sf-convert-code mt-2 px-3 py-1.5 rounded border border-blue-500/50 text-blue-400 text-xs hover:bg-blue-500/20">Convert to Code</button>';
            html += '<span class="sf-convert-spinner hidden ml-2 text-xs text-gray-500">Generating...</span>';
            html += '</div>';
            // Code sub-tab
            html += '<div class="sf-code-subtab-content' + (codeSubTab !== "code" ? " hidden" : "") + '" data-subtab-content="code">';
            html += '<textarea class="sf-code w-full px-2 py-1.5 bg-[#0d1333] border border-white/20 rounded text-green-300 text-sm font-mono resize-y" style="min-height:180px" placeholder="Generated or custom code...">' + esc(step.code || "") + '</textarea>';
            html += '<div class="flex items-center gap-2 mt-2">';
            html += '<button type="button" class="sf-test-code px-3 py-1.5 rounded border border-yellow-500/50 text-yellow-400 text-xs hover:bg-yellow-500/20">Test</button>';
            html += '<button type="button" class="sf-save-code px-3 py-1.5 rounded border border-green-500/50 text-green-400 text-xs hover:bg-green-500/20">Save Code</button>';
            html += '<span class="sf-test-spinner hidden ml-2 text-xs text-gray-500">Testing...</span>';
            html += '</div>';
            html += '<div class="sf-test-results hidden mt-2 p-2 bg-[#0d1333] border border-white/10 rounded text-xs font-mono max-h-40 overflow-auto"></div>';
            html += '</div>';
            html += '</div>';
            // Headless toggle for playwright
            if (step.action_type === "playwright") {
                html += '<div class="mt-2"><label class="flex items-center gap-2 cursor-pointer">' +
                    '<input type="checkbox" class="sf-headless rounded"' + (step.headless !== false ? " checked" : "") + '>' +
                    '<span class="text-sm text-gray-300">Run headless (no visible browser)</span></label></div>';
            }
        } else {
            // Standard instruction textarea for all other action types
            html += '<div><label class="block text-xs text-gray-500 mb-1">Instruction</label>' +
                '<textarea class="sf-instruction w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm font-mono resize-y" style="min-height:120px" placeholder="Enter the instruction for the agent...">' + esc(step.instruction) + '</textarea></div>';
        }
        // Wait for continue checkbox (all step types)
        html += '<div class="mt-2"><label class="flex items-center gap-2 cursor-pointer">' +
            '<input type="checkbox" class="sf-wait-for-continue rounded"' + (step.wait_for_continue ? " checked" : "") + '>' +
            '<span class="text-sm text-gray-300">Wait for continue signal after action completes</span></label></div>';
        html += '</div>';
        // Recording controls (only for play_recording)
        html += '<div class="sf-recording-wrap' + (!isRecording ? " hidden" : "") + '">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Description</label>' +
            '<textarea class="sf-desc-rec w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm h-12 resize-none" placeholder="Brief description of what this recording does...">' + esc(step.description) + '</textarea></div>';
        html += '<div class="flex items-center gap-2 mt-2">';
        html += '<button type="button" class="sf-record px-3 py-1.5 rounded border border-purple-500/50 text-purple-400 text-xs hover:bg-purple-500/20">⏺ Record</button>';
        html += '<button type="button" class="sf-stop-record px-3 py-1.5 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20 hidden">⏹ Stop Recording</button>';
        if (step.recording_filename) {
            html += '<button type="button" class="sf-play-recording inline-flex items-center gap-1 px-3 py-1.5 rounded border border-green-500/50 text-green-400 text-xs hover:bg-green-500/20">' + SVG_PLAY_REC + ' Play</button>';
            html += '<span class="text-xs text-green-400">✓ ' + esc(step.recording_filename) + '</span>';
        } else {
            html += '<span class="text-xs text-gray-500">No recording yet. Click Record to capture one.</span>';
        }
        html += '</div>';
        html += '</div>';
        html += '</div>';

        // ── VALIDATION TAB ──
        html += '<div class="sf-tab-content' + (tab !== "validation" ? " hidden" : "") + '" data-tab-content="validation">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Validation Type</label>' +
            '<select class="sf-validation w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            '<option value="none"' + (step.validation_type === "none" ? " selected" : "") + '>None</option>' +
            '<option value="text_match"' + (step.validation_type === "text_match" ? " selected" : "") + '>Text Match</option>' +
            '<option value="screenshot_compare"' + (step.validation_type === "screenshot_compare" ? " selected" : "") + '>Screenshot Compare</option>' +
            '<option value="llm_judgment"' + (step.validation_type === "llm_judgment" ? " selected" : "") + '>LLM Judgment</option>' +
            '<option value="rule_based"' + (step.validation_type === "rule_based" ? " selected" : "") + '>Rule Based</option>' +
            '<option value="playwright"' + (step.validation_type === "playwright" ? " selected" : "") + '>Playwright</option>' +
            '</select></div>';
        // Validation prompt (shown for all except none and playwright)
        var isPlaywrightVal = step.validation_type === "playwright";
        html += '<div class="sf-valprompt-wrap' + (step.validation_type === "none" || isPlaywrightVal ? " hidden" : "") + '">' +
            '<label class="block text-xs text-gray-500 mb-1">Validation Instructions</label>' +
            '<textarea class="sf-valprompt w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm resize-y" style="min-height:80px" placeholder="Describe what constitutes a pass...">' + esc(step.validation_prompt) + '</textarea></div>';
        // Playwright validation code editor (shown for playwright validation type)
        var valCodeSubTab = (step.validation_code && step.validation_code.trim()) ? "code" : "instruction";
        html += '<div class="sf-val-playwright-wrap' + (!isPlaywrightVal ? " hidden" : "") + ' mt-2">';
        html += '<div class="sf-val-code-editor">';
        html += '<div class="flex border-b border-white/10 mb-2">';
        html += '<button type="button" class="sf-val-code-subtab px-3 py-1.5 text-xs font-medium ' + (valCodeSubTab === "instruction" ? "border-b-2 border-[#f97316] text-white" : "text-gray-500 hover:text-gray-300") + '" data-valsubtab="instruction">Instruction</button>';
        html += '<button type="button" class="sf-val-code-subtab px-3 py-1.5 text-xs font-medium ' + (valCodeSubTab === "code" ? "border-b-2 border-[#f97316] text-white" : "text-gray-500 hover:text-gray-300") + '" data-valsubtab="code">Code</button>';
        html += '</div>';
        // Instruction sub-tab
        html += '<div class="sf-val-code-subtab-content' + (valCodeSubTab !== "instruction" ? " hidden" : "") + '" data-valsubtab-content="instruction">';
        html += '<textarea class="sf-val-instruction w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm font-mono resize-y" style="min-height:80px" placeholder="Describe what to validate with Playwright...">' + esc(step.validation_prompt) + '</textarea>';
        html += '<button type="button" class="sf-val-generate-code mt-2 px-3 py-1.5 rounded border border-blue-500/50 text-blue-400 text-xs hover:bg-blue-500/20">Generate Validation Script</button>';
        html += '<span class="sf-val-generate-spinner hidden ml-2 text-xs text-gray-500">Generating...</span>';
        html += '</div>';
        // Code sub-tab
        html += '<div class="sf-val-code-subtab-content' + (valCodeSubTab !== "code" ? " hidden" : "") + '" data-valsubtab-content="code">';
        html += '<textarea class="sf-val-code w-full px-2 py-1.5 bg-[#0d1333] border border-white/20 rounded text-green-300 text-sm font-mono resize-y" style="min-height:120px" placeholder="Playwright validation script...">' + esc(step.validation_code || "") + '</textarea>';
        html += '<div class="flex items-center gap-2 mt-2">';
        html += '<button type="button" class="sf-val-test-code px-3 py-1.5 rounded border border-yellow-500/50 text-yellow-400 text-xs hover:bg-yellow-500/20">Test Validation</button>';
        html += '<span class="sf-val-test-spinner hidden ml-2 text-xs text-gray-500">Testing...</span>';
        html += '</div>';
        html += '<div class="sf-val-test-results hidden mt-2 p-2 bg-[#0d1333] border border-white/10 rounded text-xs font-mono max-h-40 overflow-auto"></div>';
        html += '</div>';
        html += '</div>';
        html += '</div>';
        // Screenshot upload (shown for screenshot_compare)
        html += '<div class="sf-screenshot-wrap' + (step.validation_type !== "screenshot_compare" ? " hidden" : "") + ' mt-2">' +
            '<label class="block text-xs text-gray-500 mb-1">Reference Screenshot</label>' +
            '<div class="flex items-center gap-2">' +
            '<input type="file" class="sf-screenshot-file text-xs text-gray-400" accept="image/*">' +
            (step.screenshot_path ? '<span class="text-xs text-green-400">✓ Uploaded</span>' : '<span class="text-xs text-gray-600">No screenshot</span>') +
            '</div></div>';
        // Require approval
        html += '<div class="mt-3"><label class="flex items-center gap-2 cursor-pointer">' +
            '<input type="checkbox" class="sf-approval rounded"' + (step.require_approval ? " checked" : "") + '>' +
            '<span class="text-sm text-gray-300">Require manual approval before marking as passed</span></label></div>';
        html += '</div>';

        // ── ROUTING TAB ──
        var routingMode = step.routing_mode || "static";
        html += '<div class="sf-tab-content' + (tab !== "routing" ? " hidden" : "") + '" data-tab-content="routing">';
        // Routing mode selector
        html += '<div class="mb-3"><label class="block text-xs text-gray-500 mb-1">Routing Mode</label>' +
            '<select class="sf-routing-mode w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            '<option value="static"' + (routingMode === "static" ? " selected" : "") + '>Static (fixed pass/fail targets)</option>' +
            '<option value="agent_decision"' + (routingMode === "agent_decision" ? " selected" : "") + '>Agent Decision (LLM picks next step)</option>' +
            '</select></div>';
        // Static routing controls
        html += '<div class="sf-static-routing' + (routingMode !== "static" ? " hidden" : "") + '">';
        html += '<div class="grid grid-cols-2 gap-3">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">On Pass → Go To</label>' +
            '<select class="sf-pass w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            routeOpts.replace('value="' + passVal + '"', 'value="' + passVal + '" selected') + '</select></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">On Fail → Go To</label>' +
            '<select class="sf-fail w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            routeOpts.replace('value="' + failVal + '"', 'value="' + failVal + '" selected') + '</select></div>';
        html += '</div></div>';
        // Agent decision controls
        html += '<div class="sf-agent-routing' + (routingMode !== "agent_decision" ? " hidden" : "") + '">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Routing Instructions for Agent</label>' +
            '<textarea class="sf-routing-prompt w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm resize-y" style="min-height:80px" placeholder="Tell the agent how to decide which step to go to next. It will see the step result, pass/fail status, and all available steps...">' + esc(step.routing_prompt || "") + '</textarea></div>';
        html += '<p class="text-xs text-gray-600 mt-1">The agent will see the result of this step and a list of all other steps in the workflow, then decide where to route (or end the workflow).</p>';
        html += '</div>';
        // Common controls
        html += '<div class="grid grid-cols-3 gap-3 mt-3">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Wait Before Next (s)</label>' +
            '<input type="number" class="sf-wait w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + (step.wait_before_next || 0) + '"></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Timeout (s)</label>' +
            '<input type="number" class="sf-timeout w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + (step.timeout_seconds || 300) + '"></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Max Retries</label>' +
            '<input type="number" class="sf-retries w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + (step.max_retries || 0) + '"></div>';
        html += '</div>';
        html += '</div>';

        // ── HISTORY TAB ──
        html += '<div class="sf-tab-content' + (tab !== "history" ? " hidden" : "") + '" data-tab-content="history">';
        html += '<div class="sf-history-tab-list space-y-2 max-h-64 overflow-y-auto">';
        html += '<p class="text-xs text-gray-600">Loading history...</p>';
        html += '</div>';
        html += '<div class="mt-2 pt-2 border-t border-white/10">';
        html += '<button type="button" class="sf-clear-history px-2 py-1 rounded border border-red-500/50 text-red-300 text-xs hover:bg-red-500/20">Clear audit history</button>';
        html += '</div>';
        html += '</div>';

        // ── Bottom bar: Save + Delete ──
        html += '<div class="flex items-center gap-2 pt-3 border-t border-white/10">';
        html += '<button type="button" class="sf-save px-4 py-1.5 rounded bg-[#f97316] text-white text-xs font-medium hover:bg-[#ea580c]">Save</button>';
        html += '<button type="button" class="sf-delete px-3 py-1.5 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20 ml-auto">Delete</button>';
        html += '</div>';

        html += '</div></div>';
        container.innerHTML = html;

        // Tab switching
        container.querySelectorAll(".sf-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                activeStepTab[step.id] = btn.dataset.tab;
                container.querySelectorAll(".sf-tab").forEach(function (b) {
                    b.classList.toggle("border-b-2", b.dataset.tab === btn.dataset.tab);
                    b.classList.toggle("border-[#f97316]", b.dataset.tab === btn.dataset.tab);
                    b.classList.toggle("text-white", b.dataset.tab === btn.dataset.tab);
                    b.classList.toggle("text-gray-500", b.dataset.tab !== btn.dataset.tab);
                });
                container.querySelectorAll(".sf-tab-content").forEach(function (c) {
                    c.classList.toggle("hidden", c.dataset.tabContent !== btn.dataset.tab);
                });
                // Load history when History tab is selected
                if (btn.dataset.tab === "history") {
                    var histContainer = container.querySelector(".sf-history-tab-list");
                    if (histContainer) loadStepHistory(step.id, histContainer);
                }
            });
        });

        // Action type change — toggle instruction vs recording sections
        var actionTypeSelect = container.querySelector(".sf-action-type");
        if (actionTypeSelect) {
            actionTypeSelect.addEventListener("change", function () {
                var isRec = actionTypeSelect.value === "play_recording";
                container.querySelector(".sf-instruction-wrap").classList.toggle("hidden", isRec);
                container.querySelector(".sf-recording-wrap").classList.toggle("hidden", !isRec);
            });
        }

        // Code editor sub-tab switching
        container.querySelectorAll(".sf-code-subtab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                container.querySelectorAll(".sf-code-subtab").forEach(function (b) {
                    b.classList.toggle("border-b-2", b.dataset.subtab === btn.dataset.subtab);
                    b.classList.toggle("border-[#f97316]", b.dataset.subtab === btn.dataset.subtab);
                    b.classList.toggle("text-white", b.dataset.subtab === btn.dataset.subtab);
                    b.classList.toggle("text-gray-500", b.dataset.subtab !== btn.dataset.subtab);
                });
                container.querySelectorAll(".sf-code-subtab-content").forEach(function (c) {
                    c.classList.toggle("hidden", c.dataset.subtabContent !== btn.dataset.subtab);
                });
            });
        });

        // Convert to Code button
        var convertBtn = container.querySelector(".sf-convert-code");
        if (convertBtn) {
            convertBtn.addEventListener("click", function () {
                var instrEl = container.querySelector(".sf-instruction");
                var instruction = instrEl ? instrEl.value.trim() : "";
                if (!instruction) { snack("Enter an instruction first", "error"); return; }
                var spinner = container.querySelector(".sf-convert-spinner");
                convertBtn.disabled = true;
                if (spinner) spinner.classList.remove("hidden");
                var payload = { instruction: instruction, step_type: actionTypeSelect ? actionTypeSelect.value : "execute_code" };
                api("POST", "/workflows/steps/" + step.id + "/generate-code", payload)
                    .then(function (data) {
                        var codeEl = container.querySelector(".sf-code");
                        if (codeEl && data.code) codeEl.value = data.code;
                        // Switch to Code sub-tab
                        container.querySelectorAll(".sf-code-subtab").forEach(function (b) {
                            b.classList.toggle("border-b-2", b.dataset.subtab === "code");
                            b.classList.toggle("border-[#f97316]", b.dataset.subtab === "code");
                            b.classList.toggle("text-white", b.dataset.subtab === "code");
                            b.classList.toggle("text-gray-500", b.dataset.subtab !== "code");
                        });
                        container.querySelectorAll(".sf-code-subtab-content").forEach(function (c) {
                            c.classList.toggle("hidden", c.dataset.subtabContent !== "code");
                        });
                        snack("Code generated");
                    })
                    .catch(function (e) { snack(e.message || "Code generation failed", "error"); })
                    .finally(function () {
                        convertBtn.disabled = false;
                        if (spinner) spinner.classList.add("hidden");
                    });
            });
        }

        // Test Code button
        var testCodeBtn = container.querySelector(".sf-test-code");
        if (testCodeBtn) {
            testCodeBtn.addEventListener("click", function () {
                var codeEl = container.querySelector(".sf-code");
                var code = codeEl ? codeEl.value.trim() : "";
                if (!code) { snack("No code to test", "error"); return; }
                var spinner = container.querySelector(".sf-test-spinner");
                var resultsEl = container.querySelector(".sf-test-results");
                testCodeBtn.disabled = true;
                if (spinner) spinner.classList.remove("hidden");
                var payload = { code: code, step_type: actionTypeSelect ? actionTypeSelect.value : "execute_code" };
                api("POST", "/workflows/steps/" + step.id + "/test-code", payload)
                    .then(function (data) {
                        if (resultsEl) {
                            var statusColor = data.passed ? "text-green-400" : "text-red-400";
                            var statusText = data.passed ? "PASSED" : "FAILED";
                            resultsEl.innerHTML = '<span class="' + statusColor + ' font-medium">' + statusText + '</span>' +
                                (data.output ? '<pre class="mt-1 text-gray-400 whitespace-pre-wrap">' + esc(data.output) + '</pre>' : '');
                            resultsEl.classList.remove("hidden");
                        }
                    })
                    .catch(function (e) {
                        if (resultsEl) {
                            resultsEl.innerHTML = '<span class="text-red-400">Error: ' + esc(e.message || "Test failed") + '</span>';
                            resultsEl.classList.remove("hidden");
                        }
                    })
                    .finally(function () {
                        testCodeBtn.disabled = false;
                        if (spinner) spinner.classList.add("hidden");
                    });
            });
        }

        // Save Code button
        var saveCodeBtn = container.querySelector(".sf-save-code");
        if (saveCodeBtn) {
            saveCodeBtn.addEventListener("click", function () {
                var codeEl = container.querySelector(".sf-code");
                var code = codeEl ? codeEl.value : "";
                api("PATCH", "/workflows/" + currentWorkflowId + "/steps/" + step.id, { code: code })
                    .then(function () { snack("Code saved"); })
                    .catch(function () { snack("Failed to save code", "error"); });
            });
        }

        // Validation type change
        var valSelect = container.querySelector(".sf-validation");
        valSelect.addEventListener("change", function () {
            var isPlaywright = valSelect.value === "playwright";
            container.querySelector(".sf-valprompt-wrap").classList.toggle("hidden", valSelect.value === "none" || isPlaywright);
            container.querySelector(".sf-screenshot-wrap").classList.toggle("hidden", valSelect.value !== "screenshot_compare");
            container.querySelector(".sf-val-playwright-wrap").classList.toggle("hidden", !isPlaywright);
        });

        // Playwright validation code editor sub-tab switching
        container.querySelectorAll(".sf-val-code-subtab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                container.querySelectorAll(".sf-val-code-subtab").forEach(function (b) {
                    b.classList.toggle("border-b-2", b.dataset.valsubtab === btn.dataset.valsubtab);
                    b.classList.toggle("border-[#f97316]", b.dataset.valsubtab === btn.dataset.valsubtab);
                    b.classList.toggle("text-white", b.dataset.valsubtab === btn.dataset.valsubtab);
                    b.classList.toggle("text-gray-500", b.dataset.valsubtab !== btn.dataset.valsubtab);
                });
                container.querySelectorAll(".sf-val-code-subtab-content").forEach(function (c) {
                    c.classList.toggle("hidden", c.dataset.valsubtabContent !== btn.dataset.valsubtab);
                });
            });
        });

        // Generate Validation Script button
        var valGenerateBtn = container.querySelector(".sf-val-generate-code");
        if (valGenerateBtn) {
            valGenerateBtn.addEventListener("click", function () {
                var instrEl = container.querySelector(".sf-val-instruction");
                var instruction = instrEl ? instrEl.value.trim() : "";
                if (!instruction) { snack("Enter a validation instruction first", "error"); return; }
                var spinner = container.querySelector(".sf-val-generate-spinner");
                valGenerateBtn.disabled = true;
                if (spinner) spinner.classList.remove("hidden");
                var payload = { instruction: instruction, step_type: "playwright" };
                api("POST", "/workflows/steps/" + step.id + "/generate-code", payload)
                    .then(function (data) {
                        var codeEl = container.querySelector(".sf-val-code");
                        if (codeEl && data.code) codeEl.value = data.code;
                        // Switch to Code sub-tab
                        container.querySelectorAll(".sf-val-code-subtab").forEach(function (b) {
                            b.classList.toggle("border-b-2", b.dataset.valsubtab === "code");
                            b.classList.toggle("border-[#f97316]", b.dataset.valsubtab === "code");
                            b.classList.toggle("text-white", b.dataset.valsubtab === "code");
                            b.classList.toggle("text-gray-500", b.dataset.valsubtab !== "code");
                        });
                        container.querySelectorAll(".sf-val-code-subtab-content").forEach(function (c) {
                            c.classList.toggle("hidden", c.dataset.valsubtabContent !== "code");
                        });
                        snack("Validation script generated");
                    })
                    .catch(function (e) { snack(e.message || "Code generation failed", "error"); })
                    .finally(function () {
                        valGenerateBtn.disabled = false;
                        if (spinner) spinner.classList.add("hidden");
                    });
            });
        }

        // Test Validation button
        var valTestBtn = container.querySelector(".sf-val-test-code");
        if (valTestBtn) {
            valTestBtn.addEventListener("click", function () {
                var codeEl = container.querySelector(".sf-val-code");
                var code = codeEl ? codeEl.value.trim() : "";
                if (!code) { snack("No validation code to test", "error"); return; }
                var spinner = container.querySelector(".sf-val-test-spinner");
                var resultsEl = container.querySelector(".sf-val-test-results");
                valTestBtn.disabled = true;
                if (spinner) spinner.classList.remove("hidden");
                var payload = { code: code, step_type: "playwright" };
                api("POST", "/workflows/steps/" + step.id + "/test-code", payload)
                    .then(function (data) {
                        if (resultsEl) {
                            var statusColor = data.passed ? "text-green-400" : "text-red-400";
                            var statusText = data.passed ? "PASSED" : "FAILED";
                            resultsEl.innerHTML = '<span class="' + statusColor + ' font-medium">' + statusText + '</span>' +
                                (data.output ? '<pre class="mt-1 text-gray-400 whitespace-pre-wrap">' + esc(data.output) + '</pre>' : '');
                            resultsEl.classList.remove("hidden");
                        }
                    })
                    .catch(function (e) {
                        if (resultsEl) {
                            resultsEl.innerHTML = '<span class="text-red-400">Error: ' + esc(e.message || "Test failed") + '</span>';
                            resultsEl.classList.remove("hidden");
                        }
                    })
                    .finally(function () {
                        valTestBtn.disabled = false;
                        if (spinner) spinner.classList.add("hidden");
                    });
            });
        }

        // Routing mode change
        var routingModeSelect = container.querySelector(".sf-routing-mode");
        if (routingModeSelect) {
            routingModeSelect.addEventListener("change", function () {
                var isAgent = routingModeSelect.value === "agent_decision";
                container.querySelector(".sf-static-routing").classList.toggle("hidden", isAgent);
                container.querySelector(".sf-agent-routing").classList.toggle("hidden", !isAgent);
            });
        }

        // Screenshot upload
        var fileInput = container.querySelector(".sf-screenshot-file");
        if (fileInput) {
            fileInput.addEventListener("change", function () {
                if (!fileInput.files.length) return;
                var fd = new FormData();
                fd.append("file", fileInput.files[0]);
                fetch(API + "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/screenshot", { method: "POST", body: fd })
                    .then(function (r) { return r.json(); })
                    .then(function () { snack("Screenshot uploaded"); })
                    .catch(function () { snack("Upload failed", "error"); });
            });
        }

        // Save
        container.querySelector(".sf-save").addEventListener("click", function () { saveStep(step.id, container); });
        // Delete
        container.querySelector(".sf-delete").addEventListener("click", function () { confirmDeleteStep(step.id); });

        // Recording
        var recordBtn = container.querySelector(".sf-record");
        var stopRecordBtn = container.querySelector(".sf-stop-record");
        if (recordBtn) {
            recordBtn.addEventListener("click", function () {
                api("POST", "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/start-recording")
                    .then(function () {
                        recordBtn.classList.add("hidden");
                        stopRecordBtn.classList.remove("hidden");
                        snack("Recording countdown started...");
                    })
                    .catch(function () { snack("Failed to start recording", "error"); });
            });
        }
        if (stopRecordBtn) {
            stopRecordBtn.addEventListener("click", function () {
                api("POST", "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/stop-recording")
                    .then(function () {
                        stopRecordBtn.classList.add("hidden");
                        recordBtn.classList.remove("hidden");
                        snack("Recording saved");
                        // Delay reload to allow the recorder host to finish saving
                        // the recording filename to the database (signal is async)
                        setTimeout(function () { loadDetail(currentWorkflowId); }, 1500);
                    })
                    .catch(function () { snack("Failed to stop recording", "error"); });
            });
        }

        // Play recording
        var playRecBtn = container.querySelector(".sf-play-recording");
        if (playRecBtn) {
            playRecBtn.addEventListener("click", function () {
                api("POST", "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/play-recording")
                    .then(function () { snack("Playing recording..."); })
                    .catch(function (e) { snack(e.message || "Failed to play recording", "error"); });
            });
        }

        // Load history if History tab is already active on initial render
        if (tab === "history") {
            var histContainer = container.querySelector(".sf-history-tab-list");
            if (histContainer) loadStepHistory(step.id, histContainer);
        }

        var clearHistoryBtn = container.querySelector(".sf-clear-history");
        if (clearHistoryBtn) {
            clearHistoryBtn.addEventListener("click", function () {
                if (!confirm("Clear this step's audit history? This cannot be undone.")) return;
                api("DELETE", "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/results")
                    .then(function () {
                        snack("Step audit history cleared");
                        var histContainer = container.querySelector(".sf-history-tab-list");
                        if (histContainer) loadStepHistory(step.id, histContainer);
                        var resultWrap = container.querySelector(".sf-result-wrap");
                        if (resultWrap) resultWrap.classList.add("hidden");
                    })
                    .catch(function (e) { snack(e.message || "Failed to clear history", "error"); });
            });
        }
    }

    function saveStep(stepId, container) {
        var passVal = container.querySelector(".sf-pass").value;
        var failVal = container.querySelector(".sf-fail").value;
        var routingMode = container.querySelector(".sf-routing-mode") ? container.querySelector(".sf-routing-mode").value : "static";
        var routingPrompt = container.querySelector(".sf-routing-prompt") ? container.querySelector(".sf-routing-prompt").value : "";
        var actionType = container.querySelector(".sf-action-type").value;
        var isRec = actionType === "play_recording";
        var isCodeType = actionType === "execute_code" || actionType === "playwright";
        var descEl = isRec ? container.querySelector(".sf-desc-rec") : container.querySelector(".sf-desc");
        var body = {
            name: container.querySelector(".sf-name").value.trim() || "Untitled",
            description: descEl ? descEl.value.trim() : "",
            action_type: actionType,
            instruction: isRec ? "" : container.querySelector(".sf-instruction").value,
            validation_type: container.querySelector(".sf-validation").value,
            validation_prompt: container.querySelector(".sf-valprompt") ? container.querySelector(".sf-valprompt").value : "",
            routing_mode: routingMode,
            routing_prompt: routingMode === "agent_decision" ? routingPrompt : "",
            on_pass_goto: passVal === "" ? null : parseInt(passVal, 10),
            on_fail_goto: failVal === "" ? null : parseInt(failVal, 10),
            wait_before_next: parseInt(container.querySelector(".sf-wait").value, 10) || 0,
            max_retries: parseInt(container.querySelector(".sf-retries").value, 10) || 0,
            timeout_seconds: parseInt(container.querySelector(".sf-timeout").value, 10) || 300,
            require_approval: container.querySelector(".sf-approval").checked,
            wait_for_continue: container.querySelector(".sf-wait-for-continue") ? container.querySelector(".sf-wait-for-continue").checked : false
        };
        // Include code field for execute_code and playwright step types
        if (isCodeType) {
            var codeEl = container.querySelector(".sf-code");
            body.code = codeEl ? codeEl.value : "";
        }
        // Include validation_code field when validation type is playwright
        if (body.validation_type === "playwright") {
            var valCodeEl = container.querySelector(".sf-val-code");
            body.validation_code = valCodeEl ? valCodeEl.value : "";
            // Also save the validation instruction as validation_prompt
            var valInstrEl = container.querySelector(".sf-val-instruction");
            if (valInstrEl) body.validation_prompt = valInstrEl.value;
        }
        api("PATCH", "/workflows/" + currentWorkflowId + "/steps/" + stepId, body)
            .then(function () { snack("Step saved"); loadDetail(currentWorkflowId); })
            .catch(function () { snack("Failed to save step", "error"); });
    }

    function deleteStep(stepId) {
        api("DELETE", "/workflows/" + currentWorkflowId + "/steps/" + stepId)
            .then(function () { expandedStepId = null; snack("Step deleted"); loadDetail(currentWorkflowId); })
            .catch(function () { snack("Failed to delete step", "error"); });
    }

    function confirmDeleteStep(stepId) {
        showConfirmModal({
            title: "Delete step",
            message: "Delete this step? This cannot be undone.",
            confirmLabel: "Delete",
            onConfirm: function () { deleteStep(stepId); }
        });
    }

    function executeStep(stepId) {
        markStepAsRunningLocally(stepId);
        api("POST", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/execute")
            .then(function () { snack("Step execution started"); startPolling(); loadDetail(currentWorkflowId); })
            .catch(function (e) { snack(e.message || "Execute failed", "error"); });
    }

    function runFromStep(stepId) {
        markStepAsRunningLocally(stepId);
        // Start a full workflow run starting from the given step
        api("POST", "/workflows/" + currentWorkflowId + "/run", { start_step_id: stepId })
            .then(function (data) { snack(workflowFeedbackText(data, "Workflow started from this step")); startPolling(); loadDetail(currentWorkflowId); })
            .catch(function (e) { snack(workflowErrorText(e, "Run failed"), "error"); });
    }

    function stopStep(stepId) {
        api("POST", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/stop")
            .then(function () { snack("Step stopped"); loadDetail(currentWorkflowId); })
            .catch(function () { snack("Failed to stop", "error"); });
    }

    function cancelStep(stepId) {
        api("POST", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/cancel")
            .then(function () { snack("Step cancelled"); loadDetail(currentWorkflowId); })
            .catch(function () { snack("Failed to cancel", "error"); });
    }

    function continueWaitingRun() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId + "/active-run").then(function (data) {
            if (data && data.id) {
                api("POST", "/workflows/" + currentWorkflowId + "/runs/" + data.id + "/continue")
                    .then(function (resp) { snack(workflowFeedbackText(resp, "Run continued")); startPolling(); loadDetail(currentWorkflowId); })
                    .catch(function (e) { snack(workflowErrorText(e, "Failed to continue"), "error"); });
            } else {
                snack(workflowFeedbackText(data, "No active run to continue"), "error");
            }
        }).catch(function (e) { snack(workflowErrorText(e, "Failed to find active run"), "error"); });
    }

    function loadStepHistory(stepId, container) {
        function extractAttachmentPaths(text) {
            if (!text) return [];
            var re = /(\/[^\s"'`]+\.(?:png|jpg|jpeg|webp|gif|mp4|mov|m4a|wav|mp3))/ig;
            var out = [];
            var m;
            while ((m = re.exec(text)) !== null) {
                if (out.indexOf(m[1]) === -1) out.push(m[1]);
            }
            return out;
        }
        function renderAttachmentBlock(paths) {
            if (!paths || !paths.length) return "";
            var html = '<div class="mt-2 border-t border-white/10 pt-2">';
            html += '<p class="text-[11px] text-gray-500 mb-1">Attachments</p>';
            paths.forEach(function (p) {
                var safe = esc(p);
                var lower = (p || "").toLowerCase();
                var isImage = /\.(png|jpg|jpeg|webp|gif)$/.test(lower);
                html += '<div class="mb-1">';
                html += '<a class="text-[11px] text-blue-300 hover:text-blue-200 underline break-all" href="file://' + safe + '" target="_blank" rel="noreferrer">' + safe + '</a>';
                if (isImage) {
                    html += '<div class="mt-1"><img src="file://' + safe + '" class="max-h-40 rounded border border-white/10" alt="Workflow attachment"></div>';
                }
                html += '</div>';
            });
            html += '</div>';
            return html;
        }
        api("GET", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/results?limit=20")
            .then(function (data) {
                if (!data.length) {
                    container.innerHTML = '<p class="text-xs text-gray-600">No results yet.</p>';
                    return;
                }
                container.innerHTML = data.map(function (r, idx) {
                    var statusColor = r.status === "passed" ? "bg-green-600/40 text-green-300" : r.status === "failed" ? "bg-red-600/40 text-red-300" : r.status === "cancelled" ? "bg-gray-600/40 text-gray-300" : r.status === "waiting" ? "bg-amber-600/40 text-amber-300" : "bg-blue-600/40 text-blue-300";
                    var ts = r.created_at ? new Date(r.created_at).toLocaleString() : "—";
                    var response = (r.agent_response || "").trim();
                    if (shouldSuppressCancelledText(r.status, response)) response = "";
                    var attachments = extractAttachmentPaths(response);
                    var isLatest = idx === 0;
                    var isCollapsed = !isLatest;
                    var maxPreviewLen = 150;
                    var preview = response.length > maxPreviewLen ? response.substring(0, maxPreviewLen) + "…" : response;
                    if (!response) { preview = "(no output)"; }

                    var html = '<div class="sf-hist-item border border-white/10 rounded mb-2' + (isLatest ? " border-green-500/20" : "") + '">';
                    // Header row: status badge on right, timestamp
                    html += '<div class="flex items-center justify-between px-3 py-1.5 cursor-pointer sf-hist-header' + (isCollapsed ? " bg-[#0d1333]" : "") + '" data-idx="' + idx + '">';
                    html += '<span class="text-xs text-gray-500">' + esc(ts) + '</span>';
                    html += '<span class="text-xs px-1.5 py-0.5 rounded ml-auto ' + statusColor + '">' + esc(r.status) + '</span>';
                    if (r.run_id) html += '<span class="text-xs text-gray-600 ml-1">Run #' + r.run_id + '</span>';
                    if (isCollapsed) html += '<span class="sf-hist-toggle text-gray-500 text-xs ml-1">&#9654;</span>';
                    if (!isCollapsed) html += '<span class="sf-hist-toggle text-gray-500 text-xs ml-1">&#9660;</span>';
                    html += '</div>';

                    // Content: latest expanded, others collapsed
                    html += '<div class="sf-hist-content px-3 pb-2' + (isCollapsed ? " hidden" : "") + '">';
                    if (isCollapsed) {
                        html += '<p class="text-xs text-gray-500">' + esc(preview) + '</p>';
                    } else {
                        html += '<pre class="text-xs text-gray-300 font-mono whitespace-pre-wrap max-h-64 overflow-auto">' + esc(response || "(no output)") + '</pre>';
                        html += renderAttachmentBlock(attachments);
                    }
                    html += '</div>';
                    html += '</div>';
                    return html;
                }).join('');

                // Toggle expand/collapse on header click
                container.querySelectorAll('.sf-hist-header').forEach(function (hdr) {
                    hdr.addEventListener('click', function () {
                        var item = hdr.closest('.sf-hist-item');
                        var content = item.querySelector('.sf-hist-content');
                        var toggle = hdr.querySelector('.sf-hist-toggle');
                        var isHidden = content.classList.contains('hidden');
                        content.classList.toggle('hidden');
                        if (toggle) toggle.innerHTML = isHidden ? '&#9660;' : '&#9654;';
                        // If expanding, show full content instead of preview
                        if (isHidden) {
                            var pre = content.querySelector('pre');
                            if (!pre) {
                                var idx = parseInt(hdr.dataset.idx, 10);
                                var r = data[idx];
                                var resp = (r && (r.agent_response || '').trim()) || '(no output)';
                                var attachments = extractAttachmentPaths(resp);
                                content.innerHTML = '<pre class="text-xs text-gray-300 font-mono whitespace-pre-wrap max-h-64 overflow-auto">' + esc(resp) + '</pre>' + renderAttachmentBlock(attachments);
                            }
                        }
                    });
                });
            })
            .catch(function () { container.innerHTML = '<p class="text-xs text-red-400">Failed to load history.</p>'; });
    }

    // ── Schedule tab ──
    function renderSchedule(data) {
        var enabled = document.getElementById("wf-sched-enabled");
        var opts = document.getElementById("wf-sched-options");
        enabled.checked = !!data.schedule_enabled;
        opts.classList.toggle("hidden", !data.schedule_enabled);
        var preset = data.schedule_preset || "daily";
        document.getElementById("wf-sched-preset").value = preset;
        document.querySelectorAll(".wf-freq-btn").forEach(function (btn) {
            btn.classList.toggle("wf-pill-active", btn.dataset.freq === preset);
            btn.classList.toggle("wf-pill", btn.dataset.freq !== preset);
        });
        document.getElementById("wf-sched-time").value = data.schedule_time || "09:00";
        document.getElementById("wf-sched-days-wrap").classList.toggle("hidden", preset !== "weekly");
        var selectedDays = (data.schedule_days || "").split(",").filter(Boolean);
        document.querySelectorAll(".wf-day-btn").forEach(function (btn) {
            var active = selectedDays.indexOf(btn.dataset.day) !== -1;
            btn.classList.toggle("wf-pill-active", active);
            btn.classList.toggle("wf-pill", !active);
        });
        document.getElementById("wf-sched-time-wrap").classList.toggle("hidden", preset === "hourly");
    }

    // ── Runs tab ──
    function renderRuns(runs) {
        var el = document.getElementById("wf-runs-list");
        var empty = document.getElementById("wf-runs-empty");
        if (!runs.length) { el.innerHTML = ""; empty.classList.remove("hidden"); return; }
        empty.classList.add("hidden");
        el.innerHTML = runs.map(function (r) {
            var statusColor = { running: "text-blue-400", completed: "text-green-400", failed: "text-red-400", cancelled: "text-gray-400", waiting: "text-amber-400" }[r.status] || "text-gray-400";
            var started = r.started_at ? new Date(r.started_at).toLocaleString() : "—";
            var ended = r.completed_at ? new Date(r.completed_at).toLocaleString() : "—";
            return '<div class="wf-run-item bg-[#152054]/50 rounded px-3 py-2 border border-white/10" data-run-id="' + r.id + '">' +
                '<div class="flex items-center gap-3">' +
                    '<span class="text-xs text-gray-500">#' + r.id + '</span>' +
                    '<span class="text-xs ' + statusColor + ' font-medium">' + esc(r.status) + '</span>' +
                    '<span class="text-xs text-gray-500 ml-auto">' + started + '</span>' +
                    '<span class="text-xs text-gray-600">→</span>' +
                    '<span class="text-xs text-gray-500">' + ended + '</span>' +
                '</div>' +
                renderRunPacketEvidence(r.result_packet) +
            '</div>';
        }).join('');
    }

    // ── Agent Context tab (multi-item CRUD) ──
    function renderContextRules(data) {
        var listEl = document.getElementById("wf-context-items-list");
        var statusEl = document.getElementById("wf-context-save-status");
        if (!listEl) return;
        var items = Array.isArray(data.context_items) ? data.context_items : [];
        if (!items.length) {
            listEl.innerHTML = '<p class="text-sm text-gray-500 py-2">No context items yet. Add one to guide the workflow agent.</p>';
            if (statusEl) statusEl.textContent = "";
            return;
        }
        listEl.innerHTML = items.map(function (item) {
            return '' +
                '<div class="bg-[#152054]/50 rounded px-3 py-2 border border-white/10" data-context-item-id="' + item.id + '">' +
                    '<div class="flex items-center gap-2 mb-2">' +
                        '<input type="text" class="wf-ci-title flex-1 px-2 py-1 bg-transparent border-b border-white/20 text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(item.title || "") + '" placeholder="Title (e.g., SSH Access)">' +
                        '<button type="button" class="wf-ci-save text-green-400 text-xs hover:text-green-300 px-1" title="Save">✓</button>' +
                        '<button type="button" class="wf-ci-delete text-red-400 text-xs hover:text-red-300 px-1" title="Delete">✕</button>' +
                    '</div>' +
                    '<textarea class="wf-ci-content w-full px-2 py-1.5 bg-[#0d1333] border border-white/10 rounded text-white text-sm font-mono resize-y mb-2" rows="4" placeholder="Content (rules, credentials, conventions, reusable guidance)...">' + esc(item.content || "") + '</textarea>' +
                    '<input type="text" class="wf-ci-notes w-full px-2 py-1 bg-transparent border-b border-white/10 text-gray-400 text-xs focus:border-[#f97316] focus:outline-none" value="' + esc(item.notes || "") + '" placeholder="Optional notes">' +
                '</div>';
        }).join("");
        if (statusEl) statusEl.textContent = "";

        listEl.querySelectorAll("[data-context-item-id]").forEach(function (row) {
            var contextItemId = parseInt(row.dataset.contextItemId, 10);
            var saveBtn = row.querySelector(".wf-ci-save");
            var deleteBtn = row.querySelector(".wf-ci-delete");
            var titleEl = row.querySelector(".wf-ci-title");
            var contentEl = row.querySelector(".wf-ci-content");
            var notesEl = row.querySelector(".wf-ci-notes");

            if (saveBtn) {
                saveBtn.addEventListener("click", function () {
                    if (!currentWorkflowId) return;
                    if (statusEl) statusEl.textContent = "Saving...";
                    api("PATCH", "/workflows/" + currentWorkflowId + "/context-items/" + contextItemId, {
                        title: titleEl ? titleEl.value.trim() : "",
                        content: contentEl ? contentEl.value : "",
                        notes: notesEl ? notesEl.value.trim() : ""
                    }).then(function () {
                        if (statusEl) { statusEl.textContent = "Saved"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500); }
                        loadDetail(currentWorkflowId);
                    }).catch(function () {
                        if (statusEl) statusEl.textContent = "Save failed";
                    });
                });
            }

            if (deleteBtn) {
                deleteBtn.addEventListener("click", function () {
                    if (!currentWorkflowId) return;
                    if (!confirm("Delete this context item?")) return;
                    api("DELETE", "/workflows/" + currentWorkflowId + "/context-items/" + contextItemId)
                        .then(function () {
                            if (statusEl) { statusEl.textContent = "Deleted"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500); }
                            loadDetail(currentWorkflowId);
                        })
                        .catch(function () {
                            if (statusEl) statusEl.textContent = "Delete failed";
                        });
                });
            }
        });
    }

    // ── Tabs ──
    function switchTab(tab) {
        document.querySelectorAll(".wf-tab").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.tab === tab);
        });
        document.querySelectorAll(".wf-tab-content").forEach(function (el) { el.classList.add("hidden"); });
        var target = document.getElementById("wf-tab-" + tab);
        if (target) target.classList.remove("hidden");
    }

    // ── Presets ──
    function checkPresetsExist() {
        api("GET", "/workflows/presets").then(function (data) {
            var btn = document.getElementById("wf-presets-btn");
            if (btn) btn.classList.toggle("hidden", !data.length);
        }).catch(function () {});
    }

    function loadPresets() {
        var list = document.getElementById("wf-presets-list");
        if (!list) return;
        list.innerHTML = '<p class="text-xs text-gray-600">Loading...</p>';
        api("GET", "/workflows/presets").then(function (data) {
            var btn = document.getElementById("wf-presets-btn");
            if (btn) btn.classList.toggle("hidden", !data.length);
            var html = '';
            // Import button at top
            html += '<div class="mb-2 pb-2 border-b border-white/10">' +
                '<label class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/5 cursor-pointer text-sm text-blue-400">' +
                '<input type="file" class="hidden wf-import-file" accept=".dwf,.json">' +
                '📥 Import from file (.dwf / .json)' +
                '</label></div>';
            if (!data.length) {
                html += '<p class="text-xs text-gray-600">No presets found. Export a workflow to create one.</p>';
            } else {
                html += data.map(function (p) {
                    var badges = '';
                    if (p.bundle) badges += '<span class="text-xs text-purple-400" title="Bundle with assets">📦</span>';
                    if (p.has_recordings) badges += '<span class="text-xs text-yellow-400" title="Includes recordings">⏺</span>';
                    if (p.has_screenshots) badges += '<span class="text-xs text-green-400" title="Includes screenshots">🖼</span>';
                    return '<div class="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-white/5 cursor-pointer wf-preset-item" data-filename="' + esc(p.filename) + '">' +
                        '<span class="text-sm text-white truncate flex-1">' + esc(p.name) + '</span>' +
                        badges +
                        '<span class="text-xs text-gray-500">' + (p.step_count || 0) + ' steps</span>' +
                    '</div>';
                }).join('');
            }
            list.innerHTML = html;
            // Preset click handlers
            list.querySelectorAll(".wf-preset-item").forEach(function (item) {
                item.addEventListener("click", function () {
                    var fname = item.dataset.filename;
                    api("POST", "/workflows/presets/" + encodeURIComponent(fname) + "/load")
                        .then(function (data) {
                            snack("Preset loaded");
                            document.getElementById("wf-presets-dropdown").classList.add("hidden");
                            selectWorkflow(data.id);
                            loadList();
                        })
                        .catch(function () { snack("Failed to load preset", "error"); });
                });
            });
            // Import file handler
            var importInput = list.querySelector(".wf-import-file");
            if (importInput) {
                importInput.addEventListener("change", function () {
                    if (!importInput.files.length) return;
                    var fd = new FormData();
                    fd.append("file", importInput.files[0]);
                    fetch(API + "/workflows/import", { method: "POST", body: fd })
                        .then(function (r) { return r.json(); })
                        .then(function (data) {
                            if (data.id) {
                                snack("Workflow imported");
                                document.getElementById("wf-presets-dropdown").classList.add("hidden");
                                selectWorkflow(data.id);
                                loadList();
                            } else {
                                snack(data.detail || "Import failed", "error");
                            }
                        })
                        .catch(function () { snack("Import failed", "error"); });
                    importInput.value = "";
                });
            }
        }).catch(function () { list.innerHTML = '<p class="text-xs text-red-400">Failed to load presets.</p>'; });
    }

    // ── Event bindings ──
    function init() {
        document.addEventListener("click", function () { closeWorkflowContextMenu(); });
        document.addEventListener("keydown", function (evt) {
            if (evt.key === "Escape") closeWorkflowContextMenu();
            if (evt.defaultPrevented) return;
            if (document.getElementById("wf-confirm-modal")) return;
            if (!currentWorkflowId) return;
            if (isTypingTarget(evt.target)) return;
            // Delete selected workflow with Delete/Backspace hotkey.
            if (evt.key === "Delete" || evt.key === "Backspace") {
                evt.preventDefault();
                deleteWorkflowById(currentWorkflowId);
            }
        });

        // Create workflow
        var createBtn = document.getElementById("wf-create-btn");
        if (createBtn) {
            createBtn.addEventListener("click", function () {
                var nameEl = document.getElementById("wf-new-name");
                var name = (nameEl.value || "").trim() || "Untitled Workflow";
                api("POST", "/workflows", { name: name, description: "" })
                    .then(function (data) { nameEl.value = ""; snack("Workflow created"); selectWorkflow(data.id); })
                    .catch(function () { snack("Failed to create workflow", "error"); });
            });
        }

        // Search
        var searchEl = document.getElementById("wf-search");
        if (searchEl) {
            var searchTimer = null;
            searchEl.addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(loadList, 300); });
        }

        // Tabs
        document.querySelectorAll(".wf-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchTab(btn.dataset.tab);
                if (btn.dataset.tab === "runs") loadActiveRuns();
            });
        });

        var refreshActiveRuns = document.getElementById("wf-refresh-active-runs");
        if (refreshActiveRuns) {
            refreshActiveRuns.addEventListener("click", function () {
                loadActiveRuns();
            });
        }

        var activeRunsScopeEl = document.getElementById("wf-active-runs-scope");
        if (activeRunsScopeEl) {
            activeRunsScopeEl.addEventListener("change", function () {
                activeRunsScope = activeRunsScopeEl.value === "current" ? "current" : "all";
                loadActiveRuns();
            });
        }

        var addContextItemBtn = document.getElementById("wf-add-context-item-btn");
        if (addContextItemBtn) {
            addContextItemBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                var statusEl = document.getElementById("wf-context-save-status");
                if (statusEl) statusEl.textContent = "Saving...";
                api("POST", "/workflows/" + currentWorkflowId + "/context-items", {
                    title: "New Context Item",
                    content: "",
                    notes: ""
                }).then(function () {
                    if (statusEl) { statusEl.textContent = "Saved"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500); }
                    loadDetail(currentWorkflowId);
                }).catch(function () {
                    if (statusEl) statusEl.textContent = "Save failed";
                });
            });
        }

        // Workflow title is display-only in the header (no inline rename).

        // Delete workflow
        var deleteBtn = document.getElementById("wf-delete-btn");
        if (deleteBtn) {
            deleteBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                deleteWorkflowById(currentWorkflowId);
            });
        }

        // Presets dropdown (cog icon)
        var presetsBtn = document.getElementById("wf-presets-btn");
        var presetsDropdown = document.getElementById("wf-presets-dropdown");
        if (presetsBtn && presetsDropdown) {
            presetsBtn.addEventListener("click", function (e) {
                e.stopPropagation();
                var isHidden = presetsDropdown.classList.contains("hidden");
                presetsDropdown.classList.toggle("hidden");
                if (isHidden) loadPresets();
            });
            presetsDropdown.addEventListener("click", function (e) { e.stopPropagation(); });
            document.addEventListener("click", function () { presetsDropdown.classList.add("hidden"); });
        }

        // Run full workflow
        var runBtn = document.getElementById("wf-run-btn");
        if (runBtn) {
            runBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                api("POST", "/workflows/" + currentWorkflowId + "/run")
                    .then(function (data) { snack(workflowFeedbackText(data, "Workflow started")); startPolling(); loadDetail(currentWorkflowId); })
                    .catch(function (e) { snack(workflowErrorText(e, "Run failed"), "error"); });
            });
        }

        // Stop + Reset workflow
        var stopResetBtn = document.getElementById("wf-stop-reset-btn");
        if (stopResetBtn) {
            stopResetBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                if (!confirm("Stop and reset this workflow?\n\nThis will cancel any active run and reset all step states/results.")) return;
                api("POST", "/workflows/" + currentWorkflowId + "/stop-reset")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Workflow stopped and reset"));
                        stopPolling();
                        loadDetail(currentWorkflowId);
                        loadActiveRuns();
                    })
                    .catch(function (e) { snack(workflowErrorText(e, "Failed to stop and reset workflow"), "error"); });
            });
        }

        // Add step
        var addStepBtn = document.getElementById("wf-add-step-btn");
        if (addStepBtn) {
            addStepBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                api("POST", "/workflows/" + currentWorkflowId + "/steps", { name: "New Step", action_type: "agent_instruction" })
                    .then(function (data) {
                        snack("Step added");
                        var steps = (data.steps || []);
                        if (steps.length) expandedStepId = steps[steps.length - 1].id;
                        loadDetail(currentWorkflowId);
                    }).catch(function () { snack("Failed to add step", "error"); });
            });
        }

        // Schedule: enable toggle
        var schedEnabled = document.getElementById("wf-sched-enabled");
        if (schedEnabled) {
            schedEnabled.addEventListener("change", function () {
                document.getElementById("wf-sched-options").classList.toggle("hidden", !schedEnabled.checked);
            });
        }

        // Schedule: freq pills
        document.querySelectorAll(".wf-freq-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                document.querySelectorAll(".wf-freq-btn").forEach(function (b) { b.classList.remove("wf-pill-active"); b.classList.add("wf-pill"); });
                btn.classList.add("wf-pill-active"); btn.classList.remove("wf-pill");
                document.getElementById("wf-sched-preset").value = btn.dataset.freq;
                document.getElementById("wf-sched-days-wrap").classList.toggle("hidden", btn.dataset.freq !== "weekly");
                document.getElementById("wf-sched-time-wrap").classList.toggle("hidden", btn.dataset.freq === "hourly");
            });
        });

        // Schedule: day pills
        document.querySelectorAll(".wf-day-btn").forEach(function (btn) {
            btn.addEventListener("click", function () { btn.classList.toggle("wf-pill-active"); btn.classList.toggle("wf-pill"); });
        });

        // Schedule: save
        var schedSave = document.getElementById("wf-sched-save");
        if (schedSave) {
            schedSave.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                var days = [];
                document.querySelectorAll(".wf-day-btn.wf-pill-active").forEach(function (b) { days.push(b.dataset.day); });
                api("PATCH", "/workflows/" + currentWorkflowId, {
                    schedule_enabled: document.getElementById("wf-sched-enabled").checked,
                    schedule_preset: document.getElementById("wf-sched-preset").value,
                    schedule_time: document.getElementById("wf-sched-time").value,
                    schedule_days: days.join(",")
                }).then(function () { snack("Schedule saved"); loadList(); })
                  .catch(function () { snack("Failed to save schedule", "error"); });
            });
        }

        // Plan: LLM generates a multi-step workflow from a description
        var planBtn = document.getElementById("wf-plan-btn");
        if (planBtn) {
            planBtn.addEventListener("click", function () {
                var desc = (document.getElementById("wf-builder-desc").value || "").trim();
                if (!desc) { snack("Describe what to automate first", "error"); return; }
                planBtn.disabled = true;
                planBtn.textContent = "Planning...";
                document.getElementById("wf-builder-error").classList.add("hidden");
                api("POST", "/workflows/plan", { instruction: desc })
                    .then(function (data) {
                        snack("Workflow planned — " + (data.steps ? data.steps.length : 0) + " steps");
                        document.getElementById("wf-builder-desc").value = "";
                        selectWorkflow(data.id);
                        loadList();
                    })
                    .catch(function (e) {
                        var errEl = document.getElementById("wf-builder-error");
                        errEl.textContent = e.message || "Plan failed";
                        errEl.classList.remove("hidden");
                    })
                    .finally(function () {
                        planBtn.disabled = false;
                        planBtn.textContent = "⚡ Plan";
                    });
            });
        }

        // Generate Steps for existing workflow
        var genStepsBtn = document.getElementById("wf-generate-steps-btn");
        if (genStepsBtn) {
            genStepsBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                showInputModal({
                    title: "Add Steps from AI",
                    message: "Describe the outcome and AI will append generated steps to this workflow.",
                    placeholder: "Example: Open admin dashboard, export last 7 days of tickets, and attach the CSV to the run history.",
                    confirmLabel: "Generate",
                    onConfirm: function (instruction) {
                        if (!instruction || !instruction.trim()) {
                            snack("Please describe the steps to generate", "error");
                            return;
                        }
                        genStepsBtn.disabled = true;
                        api("POST", "/workflows/" + currentWorkflowId + "/generate-steps", { instruction: instruction.trim() })
                            .then(function () {
                                snack("Steps generated");
                                loadDetail(currentWorkflowId);
                            })
                            .catch(function (e) { snack(e.message || "Step generation failed", "error"); })
                            .finally(function () { genStepsBtn.disabled = false; });
                    }
                });
            });
        }

        loadList();
        checkPresetsExist();
        connectWebSocket();
        // Start version polling as initial fallback until WebSocket connects
        startVersionPolling();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
