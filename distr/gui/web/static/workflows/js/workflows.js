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

    // Inline SVG icons (14x14, currentColor)
    var SVG_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    var SVG_FORWARD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 19 22 12 13 5 13 19"/><polygon points="2 19 11 12 2 5 2 19"/></svg>';
    var SVG_CANCEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    var SVG_PLAY_REC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M8 5v14l11-7z"/></svg>';

    function esc(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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

    function api(method, path, body) {
        var opts = { method: method, headers: { "Content-Type": "application/json" } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(API + path, opts).then(function (r) {
            if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || "Request failed"); });
            return r.json();
        });
    }

    // ── Workflow list ──
    function loadList() {
        var search = (document.getElementById("wf-search") || {}).value || "";
        api("GET", "/workflows?limit=50" + (search ? "&search=" + encodeURIComponent(search) : ""))
            .then(function (data) {
                var el = document.getElementById("wf-list");
                if (!data.length) { el.innerHTML = '<p class="text-sm text-gray-500">No workflows yet.</p>'; return; }
                el.innerHTML = data.map(function (w) {
                    var active = currentWorkflowId === w.id ? " border-[#f97316] bg-white/10" : " border-transparent hover:bg-white/5";
                    var badge = w.schedule_enabled ? '<span class="text-xs px-1.5 py-0.5 rounded bg-blue-600/40 text-blue-300 ml-auto">' + esc(w.schedule_preset || "sched") + "</span>" : "";
                    var dot = w.status === "active" ? "bg-green-400" : w.status === "paused" ? "bg-yellow-400" : "bg-gray-500";
                    return '<div class="flex items-center gap-2 rounded border px-3 py-2 cursor-pointer' + active + '" data-id="' + w.id + '">' +
                        '<span class="w-2 h-2 rounded-full ' + dot + ' flex-shrink-0"></span>' +
                        '<span class="text-sm text-white truncate">' + esc(w.name) + '</span>' +
                        '<span class="text-xs text-gray-500 ml-1">' + (w.step_count || 0) + '</span>' +
                        badge + '</div>';
                }).join("");
                el.querySelectorAll("[data-id]").forEach(function (row) {
                    row.addEventListener("click", function () { selectWorkflow(parseInt(row.dataset.id, 10)); });
                });

                // Auto-select last workflow or first in list if nothing selected yet
                if (!currentWorkflowId && data.length) {
                    var lastId = null;
                    try { lastId = parseInt(localStorage.getItem("wf_last_selected"), 10); } catch (e) {}
                    var match = lastId && data.some(function (w) { return w.id === lastId; });
                    selectWorkflow(match ? lastId : data[0].id);
                }
            }).catch(function (e) { console.error("Load workflows failed", e); });
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
            document.getElementById("wf-detail-status").textContent = data.status;
            document.getElementById("wf-status-select").value = data.status || "draft";
            renderSteps(data.steps || []);
            renderSchedule(data);
            renderVariables(data.variables || []);
            renderRuns(data.runs || []);
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
            // Update step header badges
            steps.forEach(function (s) {
                var card = document.querySelector('.step-card[data-step-id="' + s.id + '"]');
                if (!card) return;
                var badge = card.querySelector(".step-status-badge");
                if (badge) {
                    badge.textContent = s.status;
                    badge.className = "step-status-badge text-xs px-1.5 py-0.5 rounded " + statusBadgeClass(s.status);
                }
                // Update result display if expanded
                var resultEl = card.querySelector(".sf-result-content");
                if (resultEl && s.result) {
                    resultEl.textContent = s.result;
                    var wrap = card.querySelector(".sf-result-wrap");
                    if (wrap) wrap.classList.remove("hidden");
                }
            });
            checkActiveRun();
        }).catch(function () {});
    }

    function statusBadgeClass(status) {
        var m = { pending: "bg-white/10 text-gray-400", running: "bg-blue-600/40 text-blue-300 animate-pulse",
            passed: "bg-green-600/40 text-green-300", failed: "bg-red-600/40 text-red-300",
            cancelled: "bg-gray-600/40 text-gray-400", skipped: "bg-yellow-600/40 text-yellow-300",
            waiting: "bg-amber-600/40 text-amber-300 animate-pulse" };
        return m[status] || "bg-white/10 text-gray-400";
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
                if (data.status === "waiting") {
                    runBar.innerHTML = '<div class="flex items-center gap-3 px-4 py-2 bg-amber-900/30 border-b border-amber-500/30">' +
                        '<span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>' +
                        '<span class="text-xs text-amber-300">Waiting for continue...</span>' +
                        '<button type="button" class="wf-continue-run px-2 py-1 rounded border border-amber-500/50 text-amber-400 text-xs hover:bg-amber-500/20" data-run-id="' + data.id + '">Continue</button>' +
                        '<button type="button" class="wf-cancel-run ml-auto px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-run-id="' + data.id + '">Cancel Run</button>' +
                    '</div>';
                    runBar.querySelector(".wf-continue-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/runs/" + data.id + "/continue")
                            .then(function () { snack("Run continued"); startPolling(); loadDetail(currentWorkflowId); })
                            .catch(function (e) { snack(e.message || "Failed to continue", "error"); });
                    });
                    runBar.querySelector(".wf-cancel-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/cancel-run/" + data.id)
                            .then(function () { snack("Run cancelled"); stopPolling(); loadDetail(currentWorkflowId); })
                            .catch(function () { snack("Failed to cancel", "error"); });
                    });
                    startPolling();
                } else {
                    runBar.innerHTML = '<div class="flex items-center gap-3 px-4 py-2 bg-blue-900/30 border-b border-blue-500/30">' +
                        '<span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>' +
                        '<span class="text-xs text-blue-300">Workflow running...</span>' +
                        '<button type="button" class="wf-cancel-run ml-auto px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-run-id="' + data.id + '">Cancel Run</button>' +
                    '</div>';
                    runBar.querySelector(".wf-cancel-run").addEventListener("click", function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/cancel-run/" + data.id)
                            .then(function () { snack("Run cancelled"); stopPolling(); loadDetail(currentWorkflowId); })
                            .catch(function () { snack("Failed to cancel", "error"); });
                    });
                    startPolling();
                }
            } else {
                runBar.innerHTML = "";
                stopPolling();
            }
        }).catch(function () {});
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
            var typeLabel = { agent_instruction: "Agent", play_recording: "Recording", run_command: "Command", http_request: "HTTP", execute_code: "Code", playwright: "Playwright" }[s.action_type] || s.action_type;
            var chevronCls = isOpen ? "chevron open" : "chevron";
            var statusCls = statusBadgeClass(s.status);
            var headerBtns = '';
            if (s.status === "waiting") {
                headerBtns = '<button type="button" class="sh-continue-waiting px-2 py-0.5 rounded border border-amber-500/50 text-amber-400 text-xs hover:bg-amber-500/20" data-step-id="' + s.id + '">Continue</button>';
            } else if (s.status === "running") {
                headerBtns = '<button type="button" class="sh-cancel inline-flex items-center justify-center w-6 h-6 rounded border border-red-500/50 text-red-400 hover:bg-red-500/20" data-step-id="' + s.id + '" title="Cancel">' + SVG_CANCEL + '</button>';
            } else {
                headerBtns = '<button type="button" class="sh-run-isolated inline-flex items-center justify-center w-6 h-6 rounded border border-blue-500/50 text-blue-400 hover:bg-blue-500/20" data-step-id="' + s.id + '" title="Run Isolated">' + SVG_PLAY + '</button>' +
                    '<button type="button" class="sh-run-continue inline-flex items-center justify-center w-6 h-6 rounded border border-green-500/50 text-green-400 hover:bg-green-500/20" data-step-id="' + s.id + '" title="Continue From Here">' + SVG_FORWARD + '</button>';
            }
            return '<div class="step-card border border-white/20 rounded-lg' + (isOpen ? " expanded" : "") + '" data-step-id="' + s.id + '">' +
                '<div class="step-header flex items-center gap-3 px-4 py-3" data-step-id="' + s.id + '">' +
                    '<span class="' + chevronCls + ' text-gray-400 text-xs">▶</span>' +
                    '<span class="text-sm font-medium text-white flex-1 truncate">' + esc(s.name) + '</span>' +
                    '<span class="step-status-badge text-xs px-1.5 py-0.5 rounded ' + statusCls + '">' + esc(s.status) + '</span>' +
                    '<span class="text-xs px-1.5 py-0.5 rounded bg-white/10 text-gray-400">' + esc(typeLabel) + '</span>' +
                    headerBtns +
                    '<span class="text-xs text-gray-600">#' + s.position + '</span>' +
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
        el.querySelectorAll(".sh-run-isolated").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); executeStep(parseInt(btn.dataset.stepId, 10)); });
        });
        el.querySelectorAll(".sh-run-continue").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); runFromStep(parseInt(btn.dataset.stepId, 10)); });
        });
        el.querySelectorAll(".sh-cancel").forEach(function (btn) {
            btn.addEventListener("click", function (e) { e.stopPropagation(); cancelStep(parseInt(btn.dataset.stepId, 10)); });
        });
        el.querySelectorAll(".sh-continue-waiting").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                continueWaitingRun();
            });
        });

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
            '<option value="play_recording"' + (step.action_type === "play_recording" ? " selected" : "") + '>Play Recording</option>' +
            '<option value="run_command"' + (step.action_type === "run_command" ? " selected" : "") + '>Run Command</option>' +
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
        html += '</div>';

        // ── Result display ──
        var hasResult = step.result && step.result.trim();
        html += '<div class="sf-result-wrap border-t border-white/10 pt-3 mt-1' + (hasResult ? "" : " hidden") + '">';
        html += '<label class="block text-xs text-gray-500 mb-1">Last Result</label>';
        html += '<pre class="sf-result-content w-full px-3 py-2 bg-[#0d1333] border border-white/10 rounded text-xs text-gray-300 font-mono max-h-40 overflow-auto whitespace-pre-wrap">' + esc(step.result || "") + '</pre>';
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
        container.querySelector(".sf-delete").addEventListener("click", function () {
            if (confirm("Delete this step?")) deleteStep(step.id);
        });

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

    function executeStep(stepId) {
        api("POST", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/execute")
            .then(function () { snack("Step sent to agent"); startPolling(); loadDetail(currentWorkflowId); })
            .catch(function (e) { snack(e.message || "Execute failed", "error"); });
    }

    function runFromStep(stepId) {
        // Start a full workflow run (the run engine handles routing from step to step)
        api("POST", "/workflows/" + currentWorkflowId + "/run")
            .then(function () { snack("Workflow started"); startPolling(); loadDetail(currentWorkflowId); })
            .catch(function (e) { snack(e.message || "Run failed", "error"); });
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
                    .then(function () { snack("Run continued"); startPolling(); loadDetail(currentWorkflowId); })
                    .catch(function (e) { snack(e.message || "Failed to continue", "error"); });
            } else {
                snack("No active run to continue", "error");
            }
        }).catch(function () { snack("Failed to find active run", "error"); });
    }

    function loadStepHistory(stepId, container) {
        api("GET", "/workflows/" + currentWorkflowId + "/steps/" + stepId + "/results?limit=10")
            .then(function (data) {
                if (!data.length) {
                    container.innerHTML = '<p class="text-xs text-gray-600">No results yet.</p>';
                    return;
                }
                container.innerHTML = data.map(function (r) {
                    var statusColor = r.status === "passed" ? "text-green-400" : r.status === "failed" ? "text-red-400" : "text-gray-400";
                    var ts = r.created_at ? new Date(r.created_at).toLocaleString() : "—";
                    var response = (r.agent_response || "").substring(0, 200);
                    return '<div class="bg-[#0d1333] border border-white/10 rounded px-3 py-2">' +
                        '<div class="flex items-center gap-2 mb-1">' +
                        '<span class="text-xs ' + statusColor + ' font-medium">' + esc(r.status) + '</span>' +
                        '<span class="text-xs text-gray-600 ml-auto">' + ts + '</span>' +
                        (r.run_id ? '<span class="text-xs text-gray-600">Run #' + r.run_id + '</span>' : '') +
                        '</div>' +
                        '<pre class="text-xs text-gray-400 font-mono whitespace-pre-wrap max-h-20 overflow-auto">' + esc(response) + '</pre>' +
                    '</div>';
                }).join('');
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

    // ── Variables tab ──
    function renderVariables(vars) {
        var el = document.getElementById("wf-vars-list");
        if (!vars.length) { el.innerHTML = '<p class="text-sm text-gray-500 py-2">No variables defined.</p>'; return; }
        el.innerHTML = vars.map(function (v) {
            return '<div class="flex items-center gap-2 bg-[#152054]/50 rounded px-3 py-2 border border-white/10" data-var-id="' + v.id + '">' +
                '<input type="text" class="vf-name flex-1 px-2 py-1 bg-transparent border-b border-white/20 text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(v.name) + '" placeholder="Name">' +
                '<input type="text" class="vf-val flex-1 px-2 py-1 bg-transparent border-b border-white/20 text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(v.default_value) + '" placeholder="Default value">' +
                '<input type="text" class="vf-desc flex-1 px-2 py-1 bg-transparent border-b border-white/20 text-gray-400 text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(v.description) + '" placeholder="Description">' +
                '<button type="button" class="vf-save text-green-400 text-xs hover:text-green-300 px-1" title="Save">✓</button>' +
                '<button type="button" class="vf-delete text-red-400 text-xs hover:text-red-300 px-1" title="Delete">✕</button>' +
            '</div>';
        }).join('');
        el.querySelectorAll("[data-var-id]").forEach(function (row) {
            var varId = parseInt(row.dataset.varId, 10);
            row.querySelector(".vf-save").addEventListener("click", function () {
                api("PATCH", "/workflows/" + currentWorkflowId + "/variables/" + varId, {
                    name: row.querySelector(".vf-name").value.trim(),
                    default_value: row.querySelector(".vf-val").value,
                    description: row.querySelector(".vf-desc").value.trim()
                }).then(function () { snack("Variable saved"); }).catch(function () { snack("Failed to save variable", "error"); });
            });
            row.querySelector(".vf-delete").addEventListener("click", function () {
                if (!confirm("Delete this variable?")) return;
                api("DELETE", "/workflows/" + currentWorkflowId + "/variables/" + varId)
                    .then(function () { snack("Variable deleted"); loadDetail(currentWorkflowId); })
                    .catch(function () { snack("Failed to delete variable", "error"); });
            });
        });
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
            return '<div class="flex items-center gap-3 bg-[#152054]/50 rounded px-3 py-2 border border-white/10">' +
                '<span class="text-xs text-gray-500">#' + r.id + '</span>' +
                '<span class="text-xs ' + statusColor + ' font-medium">' + esc(r.status) + '</span>' +
                '<span class="text-xs text-gray-500 ml-auto">' + started + '</span>' +
                '<span class="text-xs text-gray-600">→</span>' +
                '<span class="text-xs text-gray-500">' + ended + '</span>' +
            '</div>';
        }).join('');
    }

    // ── Context Rules tab ──
    var _contextRulesDebounceTimer = null;
    function renderContextRules(data) {
        var textarea = document.getElementById("wf-context-rules");
        var statusEl = document.getElementById("wf-context-save-status");
        if (!textarea) return;
        textarea.value = data.context_rules || "";
        if (statusEl) statusEl.textContent = "";
        // Remove old listener by cloning
        var newTextarea = textarea.cloneNode(true);
        textarea.parentNode.replaceChild(newTextarea, textarea);
        newTextarea.addEventListener("input", function () {
            if (_contextRulesDebounceTimer) clearTimeout(_contextRulesDebounceTimer);
            if (statusEl) statusEl.textContent = "Saving...";
            _contextRulesDebounceTimer = setTimeout(function () {
                var text = newTextarea.value;
                api("PATCH", "/workflows/" + currentWorkflowId, { context_rules: text })
                    .then(function () {
                        if (statusEl) { statusEl.textContent = "Saved"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 2000); }
                    })
                    .catch(function () {
                        if (statusEl) statusEl.textContent = "Save failed";
                    });
            }, 1000);
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
            btn.addEventListener("click", function () { switchTab(btn.dataset.tab); });
        });

        // Name edit
        var nameEl = document.getElementById("wf-detail-name");
        if (nameEl) {
            nameEl.addEventListener("blur", function () {
                if (!currentWorkflowId) return;
                api("PATCH", "/workflows/" + currentWorkflowId, { name: nameEl.value.trim() || "Untitled" })
                    .then(function () { loadList(); }).catch(function () { snack("Failed to rename", "error"); });
            });
        }

        // Status change
        var statusEl = document.getElementById("wf-status-select");
        if (statusEl) {
            statusEl.addEventListener("change", function () {
                if (!currentWorkflowId) return;
                api("PATCH", "/workflows/" + currentWorkflowId, { status: statusEl.value })
                    .then(function () { document.getElementById("wf-detail-status").textContent = statusEl.value; loadList(); })
                    .catch(function () { snack("Failed to update status", "error"); });
            });
        }

        // Delete workflow
        var deleteBtn = document.getElementById("wf-delete-btn");
        if (deleteBtn) {
            deleteBtn.addEventListener("click", function () {
                if (!currentWorkflowId || !confirm("Delete this workflow? This cannot be undone.")) return;
                api("DELETE", "/workflows/" + currentWorkflowId)
                    .then(function () {
                        snack("Workflow deleted"); currentWorkflowId = null; currentWorkflow = null; expandedStepId = null;
                        document.getElementById("wf-detail").classList.add("hidden");
                        document.getElementById("wf-empty").classList.remove("hidden");
                        loadList();
                    }).catch(function () { snack("Failed to delete workflow", "error"); });
            });
        }

        // Duplicate
        var dupBtn = document.getElementById("wf-duplicate-btn");
        if (dupBtn) {
            dupBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                api("POST", "/workflows/" + currentWorkflowId + "/duplicate")
                    .then(function (data) { snack("Workflow duplicated"); selectWorkflow(data.id); })
                    .catch(function () { snack("Failed to duplicate", "error"); });
            });
        }

        // Export to presets (.dwf bundle)
        var exportBtn = document.getElementById("wf-export-btn");
        if (exportBtn) {
            exportBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                api("POST", "/workflows/" + currentWorkflowId + "/export-preset")
                    .then(function (data) { snack("Exported as " + (data.filename || "preset")); checkPresetsExist(); })
                    .catch(function () { snack("Failed to export", "error"); });
            });
        }

        // Download .dwf file
        var downloadBtn = document.getElementById("wf-download-btn");
        if (downloadBtn) {
            downloadBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                window.location.href = API + "/workflows/" + currentWorkflowId + "/export";
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
                    .then(function () { snack("Workflow started"); startPolling(); loadDetail(currentWorkflowId); })
                    .catch(function (e) { snack(e.message || "Run failed", "error"); });
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

        // Add variable
        var addVarBtn = document.getElementById("wf-add-var-btn");
        if (addVarBtn) {
            addVarBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                api("POST", "/workflows/" + currentWorkflowId + "/variables", { name: "new_var", default_value: "", description: "" })
                    .then(function () { snack("Variable added"); loadDetail(currentWorkflowId); })
                    .catch(function () { snack("Failed to add variable", "error"); });
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

        // Workflow Builder: generate workflow from description
        var builderBtn = document.getElementById("wf-builder-btn");
        if (builderBtn) {
            builderBtn.addEventListener("click", function () {
                var desc = (document.getElementById("wf-builder-desc").value || "").trim();
                if (!desc) { snack("Please describe your workflow", "error"); return; }
                builderBtn.disabled = true;
                document.getElementById("wf-builder-spinner").classList.remove("hidden");
                document.getElementById("wf-builder-error").classList.add("hidden");
                api("POST", "/workflows/generate", { description: desc })
                    .then(function (data) {
                        snack("Workflow generated");
                        document.getElementById("wf-builder-desc").value = "";
                        selectWorkflow(data.id);
                        loadList();
                    })
                    .catch(function (e) {
                        var errEl = document.getElementById("wf-builder-error");
                        errEl.textContent = e.message || "Generation failed";
                        errEl.classList.remove("hidden");
                    })
                    .finally(function () {
                        builderBtn.disabled = false;
                        document.getElementById("wf-builder-spinner").classList.add("hidden");
                    });
            });
        }

        // Plan Steps: LLM step generation from instruction
        var planBtn = document.getElementById("wf-plan-btn");
        if (planBtn) {
            planBtn.addEventListener("click", function () {
                var desc = (document.getElementById("wf-builder-desc").value || "").trim();
                if (!desc) { snack("Enter an instruction first", "error"); return; }
                planBtn.disabled = true;
                planBtn.textContent = "Planning...";
                document.getElementById("wf-builder-error").classList.add("hidden");
                api("POST", "/workflows/plan", { instruction: desc })
                    .then(function (data) {
                        snack("Steps planned");
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
                        planBtn.textContent = "Plan Steps";
                    });
            });
        }

        // Generate Steps for existing workflow
        var genStepsBtn = document.getElementById("wf-generate-steps-btn");
        if (genStepsBtn) {
            genStepsBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                var instruction = prompt("Describe the steps to generate:");
                if (!instruction || !instruction.trim()) return;
                genStepsBtn.disabled = true;
                api("POST", "/workflows/" + currentWorkflowId + "/generate-steps", { instruction: instruction.trim() })
                    .then(function () {
                        snack("Steps generated");
                        loadDetail(currentWorkflowId);
                    })
                    .catch(function (e) { snack(e.message || "Step generation failed", "error"); })
                    .finally(function () { genStepsBtn.disabled = false; });
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
