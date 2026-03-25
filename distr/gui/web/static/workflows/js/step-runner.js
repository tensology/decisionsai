/**
 * Step Runner page: break down big instructions into steps, view, approve, execute.
 */
(function() {
    var currentSessionId = null;
    var sessionsData = [];

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    var DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    function formatScheduleLabel(schedule, scheduleTime, scheduleDays) {
        var p = (schedule || "").toLowerCase();
        if (p === "hourly") return "Hourly";
        if (p === "daily") return "Daily at " + (scheduleTime || "09:00");
        if (p === "weekly") {
            var days = (scheduleDays || "1").split(",").map(function(d) { return DAY_NAMES[parseInt(d, 10)] || d; });
            return "Weekly " + days.join(", ") + " at " + (scheduleTime || "09:00");
        }
        return schedule || "";
    }

    function formatNextRun(utcIso) {
        if (!utcIso) return "";
        try {
            var d = new Date(utcIso.endsWith("Z") ? utcIso : utcIso + "Z");
            var now = new Date();
            var diffMs = d - now;
            var diffMins = Math.round(diffMs / 60000);
            var timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            var dateStr = d.toLocaleDateString([], { month: "short", day: "numeric" });
            if (diffMs < 0) return "overdue";
            if (diffMins < 60) return "in " + diffMins + "m";
            if (diffMins < 1440) return "today " + timeStr;
            if (diffMins < 2880) return "tomorrow " + timeStr;
            return dateStr + " " + timeStr;
        } catch (e) {
            return utcIso.slice(0, 16);
        }
    }

    function populateScheduleEditForm(data) {
        var s = (data.schedule || "daily").toLowerCase();
        var preset = "daily";
        if (s === "hourly") preset = "hourly";
        else if (s === "daily") preset = "daily";
        else if (s === "weekly") preset = "weekly";
        else preset = "custom";

        // Set hidden input
        var presetEl = document.getElementById("step-runner-detail-preset");
        if (presetEl) presetEl.value = preset;

        // Highlight active freq pill
        document.querySelectorAll(".sr-detail-freq-btn").forEach(function(btn) {
            btn.dataset.active = btn.getAttribute("data-freq") === preset ? "1" : "0";
        });

        // Time
        var timeEl = document.getElementById("step-runner-detail-time");
        if (timeEl) timeEl.value = (data.schedule_time || "09:00").slice(0, 5);

        // Days
        var days = (data.schedule_days || "1").split(",").map(function(d) { return d.trim(); });
        document.querySelectorAll(".sr-detail-day-btn").forEach(function(btn) {
            btn.dataset.selected = days.indexOf(btn.getAttribute("data-day")) >= 0 ? "1" : "0";
        });

        // Custom desc
        var cronEl = document.getElementById("step-runner-detail-cron");
        if (cronEl) cronEl.value = preset === "custom" ? (data.schedule || "") : "";

        updateDetailScheduleOptionsVisibility();
        updateDetailScheduleSummary();
    }

    function updateDetailScheduleOptionsVisibility() {
        var preset = (document.getElementById("step-runner-detail-preset") || {}).value || "daily";
        var timeWrap = document.getElementById("step-runner-detail-time-wrap");
        var daysWrap = document.getElementById("step-runner-detail-days-wrap");
        var cronWrap = document.getElementById("step-runner-detail-cron-wrap");
        if (timeWrap) timeWrap.classList.toggle("hidden", preset === "hourly" || preset === "custom");
        if (daysWrap) daysWrap.classList.toggle("hidden", preset !== "weekly");
        if (cronWrap) cronWrap.classList.toggle("hidden", preset !== "custom");
    }

    function updateDetailScheduleSummary() {
        var el = document.getElementById("step-runner-detail-summary");
        if (!el) return;
        var preset = (document.getElementById("step-runner-detail-preset") || {}).value || "daily";
        var time = (document.getElementById("step-runner-detail-time") || {}).value || "09:00";
        var desc = (document.getElementById("step-runner-detail-cron") || {}).value || "";
        var summary = buildScheduleSummary(preset, time, getDetailSelectedDays(), desc);
        el.textContent = summary;
        el.style.color = summary.startsWith("\u26a0") ? "#f87171" : "#f97316";
    }

    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "step-runner-snackbar" }); }

    function loadSessions() {
        var search = (document.getElementById("step-runner-search") || {}).value || "";
        var type = (document.getElementById("step-runner-filter") || {}).value || "";
        var url = "/api/step-runner/sessions?limit=50";
        if (type) url += "&session_type=" + encodeURIComponent(type);
        if (search) url += "&search=" + encodeURIComponent(search);
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                sessionsData = Array.isArray(data) ? data : [];
                renderSessions();
            })
            .catch(function(e) {
                console.error("Step Runner: load sessions failed", e);
            });
    }

    function renderSessions() {
        var el = document.getElementById("step-runner-sessions");
        if (!el) return;
        if (!sessionsData.length) {
            el.innerHTML = "<p class=\"text-sm text-gray-500\">No sessions yet.</p>";
            return;
        }
        el.innerHTML = sessionsData.map(function(s) {
            var active = currentSessionId === s.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            var statusColor = s.status === "completed" ? "text-green-400" : s.status === "failed" ? "text-red-400" : "text-gray-400";
            var scheduleBadge = s.session_type === "scheduled" && s.schedule
                ? "<span class=\"text-xs px-1.5 py-0.5 rounded bg-blue-600/40 text-blue-300\" title=\"Next: " + escapeAttr(s.next_run_at || "") + "\">" + escapeAttr(formatScheduleLabel(s.schedule, s.schedule_time, s.schedule_days)) + (s.next_run_at ? " · " + formatNextRun(s.next_run_at) : "") + "</span>"
                : s.session_type === "audit"
                ? "<span class=\"text-xs px-1.5 py-0.5 rounded bg-amber-600/40 text-amber-300\" title=\"Audit log\">Audit</span>"
                : "";
            return "<div class=\"flex items-center gap-2 rounded border" + active + " group\" data-id=\"" + s.id + "\">" +
                "<button type=\"button\" class=\"flex-1 min-w-0 text-left px-3 py-2 text-white text-sm truncate\" data-id=\"" + s.id + "\">" + escapeAttr(s.instruction || "Untitled") + "</button>" +
                scheduleBadge +
                "<span class=\"text-xs " + statusColor + "\">" + escapeAttr(s.status) + "</span>" +
                "</div>";
        }).join("");
        el.querySelectorAll("button[data-id]").forEach(function(btn) {
            btn.addEventListener("click", function() {
                selectSession(parseInt(btn.getAttribute("data-id"), 10));
            });
        });
    }

    function selectSession(id) {
        currentSessionId = id;
        renderSessions();
        loadSessionDetail(id);
    }

    var currentSessionStatus = null;
    var _contextRulesDebounceTimer = null;
    var _currentDetailTab = "steps";

    function switchDetailTab(tab) {
        _currentDetailTab = tab;
        var panels = ["steps", "history", "context"];
        panels.forEach(function(p) {
            var panel = document.getElementById("step-runner-detail-panel-" + p);
            if (panel) panel.classList.toggle("hidden", p !== tab);
        });
        document.querySelectorAll(".step-detail-tab").forEach(function(btn) {
            var isActive = btn.getAttribute("data-tab") === tab;
            btn.classList.toggle("border-[#f97316]", isActive);
            btn.classList.toggle("text-white", isActive);
            btn.classList.toggle("border-transparent", !isActive);
            btn.classList.toggle("text-gray-400", !isActive);
        });
    }

    function loadRunHistory(sessionId) {
        var el = document.getElementById("step-runner-history-list");
        if (!el) return;
        el.innerHTML = "<p class=\"text-xs text-gray-500\">Loading run history...</p>";
        fetch("/api/step-runner/sessions/" + sessionId + "/runs")
            .then(function(r) { return r.json(); })
            .then(function(runs) {
                if (!Array.isArray(runs) || !runs.length) {
                    el.innerHTML = "<p class=\"text-xs text-gray-500\">No runs yet.</p>";
                    return;
                }
                el.innerHTML = runs.map(function(r) {
                    var startedAt = r.started_at ? new Date(r.started_at.endsWith("Z") ? r.started_at : r.started_at + "Z") : null;
                    var completedAt = r.completed_at ? new Date(r.completed_at.endsWith("Z") ? r.completed_at : r.completed_at + "Z") : null;
                    var startStr = startedAt ? startedAt.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
                    var endStr = completedAt ? completedAt.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
                    var badgeColor = r.status === "completed" ? "bg-green-600/40 text-green-300" : r.status === "failed" ? "bg-red-600/40 text-red-300" : r.status === "in_progress" ? "bg-amber-600/40 text-amber-300" : "bg-gray-600/40 text-gray-400";
                    var stepResults = [];
                    try { stepResults = JSON.parse(r.step_results || "[]"); } catch(e) {}
                    if (!Array.isArray(stepResults)) {
                        try { stepResults = (typeof r.step_results === "object" && r.step_results.steps) ? r.step_results.steps : []; } catch(e2) { stepResults = []; }
                    }
                    var stepHtml = "";
                    if (stepResults.length) {
                        stepHtml = "<div class=\"mt-2 space-y-1 pl-3 border-l border-white/10\">" +
                            stepResults.map(function(sr) {
                                var sBadge = sr.status === "completed" ? "bg-green-600/40 text-green-300" : sr.status === "failed" ? "bg-red-600/40 text-red-300" : "bg-gray-600/40 text-gray-400";
                                var output = sr.result || sr.output || "";
                                var outputHtml = output ? "<pre class=\"mt-1 text-xs text-gray-500 whitespace-pre-wrap break-all max-h-24 overflow-auto\">" + escapeAttr(output).slice(0, 500) + "</pre>" : "";
                                return "<div class=\"py-1\">" +
                                    "<div class=\"flex items-center gap-2\">" +
                                    "<span class=\"text-xs text-gray-300\">" + escapeAttr(sr.title || ("Step " + (sr.step_id || ""))) + "</span>" +
                                    "<span class=\"text-xs px-1.5 py-0.5 rounded " + sBadge + "\">" + escapeAttr(sr.status || "unknown") + "</span>" +
                                    "</div>" + outputHtml + "</div>";
                            }).join("") + "</div>";
                    }
                    return "<details class=\"rounded border border-white/10 bg-[#152054]/50\">" +
                        "<summary class=\"flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-white/5\">" +
                        "<span class=\"text-xs text-gray-300\">" + escapeAttr(startStr) + "</span>" +
                        "<span class=\"text-xs px-1.5 py-0.5 rounded " + badgeColor + "\">" + escapeAttr(r.status || "unknown") + "</span>" +
                        "<span class=\"text-xs text-gray-500 ml-auto\">" + escapeAttr(endStr) + "</span>" +
                        "</summary>" +
                        "<div class=\"px-3 pb-3\">" + stepHtml + "</div>" +
                        "</details>";
                }).join("");
            })
            .catch(function(e) {
                el.innerHTML = "<p class=\"text-xs text-red-400\">Failed to load run history.</p>";
                console.error("Step Runner: load run history failed", e);
            });
    }

    function initContextRulesTab(sessionId, contextRulesText) {
        var textarea = document.getElementById("step-runner-context-rules");
        var statusEl = document.getElementById("step-runner-context-save-status");
        if (!textarea) return;
        textarea.value = contextRulesText || "";
        if (statusEl) statusEl.textContent = "";
        // Remove old listener by cloning
        var newTextarea = textarea.cloneNode(true);
        textarea.parentNode.replaceChild(newTextarea, textarea);
        newTextarea.addEventListener("input", function() {
            if (_contextRulesDebounceTimer) clearTimeout(_contextRulesDebounceTimer);
            var saveStatus = document.getElementById("step-runner-context-save-status");
            if (saveStatus) saveStatus.textContent = "Saving...";
            _contextRulesDebounceTimer = setTimeout(function() {
                var text = newTextarea.value;
                fetch("/api/step-runner/sessions/" + sessionId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ context_rules: text })
                })
                    .then(function(r) {
                        if (!r.ok) throw new Error("Save failed");
                        if (saveStatus) saveStatus.textContent = "Saved";
                        setTimeout(function() { if (saveStatus) saveStatus.textContent = ""; }, 2000);
                    })
                    .catch(function() {
                        if (saveStatus) saveStatus.textContent = "Save failed";
                    });
            }, 1000);
        });
    }

    function loadSessionDetail(id) {
        fetch("/api/step-runner/sessions/" + id)
            .then(function(r) {
                if (!r.ok) throw new Error("Session not found");
                return r.json();
            })
            .then(function(data) {
                document.getElementById("step-runner-empty").classList.add("hidden");
                document.getElementById("step-runner-detail").classList.remove("hidden");
                document.getElementById("step-runner-session-title").textContent = (data.instruction || "").slice(0, 80) + (data.instruction && data.instruction.length > 80 ? "..." : "");
                document.getElementById("step-runner-session-status").textContent = data.status || "planned";
                currentSessionStatus = data.status || "planned";
                var scheduleEl = document.getElementById("step-runner-session-schedule");
                var enabledWrap = document.getElementById("step-runner-enabled-wrap");
                if (data.session_type === "scheduled" && data.schedule) {
                    var schedText = formatScheduleLabel(data.schedule, data.schedule_time, data.schedule_days);
                    if (data.next_run_at) schedText += " · next: " + formatNextRun(data.next_run_at);
                    scheduleEl.textContent = schedText;
                    scheduleEl.classList.remove("hidden");
                    enabledWrap.classList.remove("hidden");
                    var cb = document.getElementById("step-runner-enabled");
                    cb.checked = data.enabled !== false;
                    cb.dataset.sessionId = data.id;
                    var editWrap = document.getElementById("step-runner-schedule-edit-wrap");
                    if (editWrap) {
                        editWrap.classList.remove("hidden");
                        populateScheduleEditForm(data);
                    }
                } else {
                    scheduleEl.classList.add("hidden");
                    enabledWrap.classList.add("hidden");
                    var editWrap = document.getElementById("step-runner-schedule-edit-wrap");
                    if (editWrap) editWrap.classList.add("hidden");
                }
                var runsEl = document.getElementById("step-runner-runs");
                var runsList = document.getElementById("step-runner-runs-list");
                if (data.runs && data.runs.length) {
                    runsEl.classList.remove("hidden");
                    runsList.innerHTML = data.runs.map(function(r) {
                        // Local time
                        var startedAt = r.started_at ? new Date(r.started_at.endsWith("Z") ? r.started_at : r.started_at + "Z") : null;
                        var timeStr = startedAt ? startedAt.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
                        // Duration
                        var durStr = "";
                        if (r.started_at && r.completed_at) {
                            var ms = new Date(r.completed_at.endsWith("Z") ? r.completed_at : r.completed_at + "Z") - new Date(r.started_at.endsWith("Z") ? r.started_at : r.started_at + "Z");
                            var secs = Math.round(ms / 1000);
                            durStr = secs < 60 ? secs + "s" : Math.round(secs / 60) + "m " + (secs % 60) + "s";
                        }
                        // Step counts from step_results
                        var stepResults = [];
                        try { stepResults = JSON.parse(r.step_results || "[]"); } catch(e) {}
                        var total = stepResults.length;
                        var completed = stepResults.filter(function(s) { return s.status === "completed"; }).length;
                        var failed = stepResults.filter(function(s) { return s.status === "failed"; }).length;
                        var stepInfo = total ? (completed + "/" + total + " completed" + (failed ? ", " + failed + " failed" : "")) : "";
                        // Status badge
                        var badgeColor = r.status === "completed" ? "bg-green-600/40 text-green-300" : r.status === "failed" ? "bg-red-600/40 text-red-300" : r.status === "in_progress" ? "bg-amber-600/40 text-amber-300" : "bg-gray-600/40 text-gray-400";
                        return "<div class=\"flex items-center gap-2 py-1 text-xs text-gray-400\">" +
                            "<span class=\"text-gray-300\">" + escapeAttr(timeStr) + "</span>" +
                            "<span class=\"px-1.5 py-0.5 rounded " + badgeColor + "\">" + escapeAttr(r.status) + "</span>" +
                            (stepInfo ? "<span>" + escapeAttr(stepInfo) + "</span>" : "") +
                            (durStr ? "<span class=\"ml-auto text-gray-500\">" + escapeAttr(durStr) + "</span>" : "") +
                            "</div>";
                    }).join("");
                } else {
                    runsEl.classList.add("hidden");
                }
                document.getElementById("step-runner-duplicate").dataset.sessionId = data.id;
                document.getElementById("step-runner-delete").dataset.sessionId = data.id;
                var runAllBtn = document.getElementById("step-runner-run-all");
                if (runAllBtn) runAllBtn.dataset.sessionId = data.id;
                var cancelBtn = document.getElementById("step-runner-cancel");
                var skipBtn = document.getElementById("step-runner-skip-step");
                if (cancelBtn) {
                    cancelBtn.dataset.sessionId = data.id;
                    cancelBtn.classList.toggle("hidden", data.status !== "in_progress");
                }
                if (skipBtn) {
                    skipBtn.dataset.sessionId = data.id;
                    skipBtn.classList.toggle("hidden", data.status !== "in_progress");
                }
                // Initialize detail tabs
                switchDetailTab(_currentDetailTab);
                // Wire tab click handlers (re-wire each load to avoid stale closures)
                document.querySelectorAll(".step-detail-tab").forEach(function(btn) {
                    btn.onclick = function() { switchDetailTab(btn.getAttribute("data-tab")); };
                });
                // Load Results History tab data
                loadRunHistory(data.id);
                // Init Context & Rules tab
                initContextRulesTab(data.id, data.context_rules || "");
                renderSteps(data.steps || []);
            })
            .catch(function(e) {
                showSnackbar("Failed to load session", "error");
                console.error(e);
            });
    }

    var STEP_TYPES = [
        { value: "run_command", label: "Run Command" },
        { value: "play_recording", label: "Play Recording" },
        { value: "http_request", label: "HTTP Request" },
        { value: "execute_code", label: "Execute Code" },
        { value: "playwright", label: "Playwright" }
    ];

    var HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

    function parseStepConfig(s) {
        if (!s.config) return {};
        if (typeof s.config === "object") return s.config;
        try { return JSON.parse(s.config); } catch(e) { return {}; }
    }

    function buildStepTypeDropdown(stepId, currentType) {
        var opts = STEP_TYPES.map(function(t) {
            var sel = t.value === currentType ? " selected" : "";
            return "<option value=\"" + t.value + "\"" + sel + ">" + escapeAttr(t.label) + "</option>";
        }).join("");
        return "<select class=\"step-type-select px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-xs\" data-step-id=\"" + stepId + "\">" + opts + "</select>";
    }

    function buildAdaptiveFormFields(stepType, config, stepData) {
        var code = stepData.code || config.code || "";
        var instruction = config.instruction || "";
        if (stepType === "run_command") {
            return "<div class=\"step-type-fields mt-2 space-y-2\">" +
                "<label class=\"block text-xs text-gray-400\">Command</label>" +
                "<textarea class=\"step-cfg-command w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm font-mono\" rows=\"3\" placeholder=\"e.g. npm run build\">" + escapeAttr(config.command || "") + "</textarea>" +
                "<div class=\"flex gap-2\">" +
                "<div class=\"flex-1\"><label class=\"block text-xs text-gray-400\">Working directory</label>" +
                "<input type=\"text\" class=\"step-cfg-workdir w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" placeholder=\"Optional\" value=\"" + escapeAttr(config.working_directory || "") + "\"></div>" +
                "<div class=\"w-24\"><label class=\"block text-xs text-gray-400\">Timeout (s)</label>" +
                "<input type=\"number\" class=\"step-cfg-timeout w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" value=\"" + escapeAttr(String(config.timeout_seconds || 60)) + "\"></div>" +
                "</div></div>";
        }
        if (stepType === "play_recording") {
            return "<div class=\"step-type-fields mt-2 space-y-2\">" +
                "<label class=\"block text-xs text-gray-400\">Recording</label>" +
                "<input type=\"text\" class=\"step-cfg-recording w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" placeholder=\"Recording name or ID\" value=\"" + escapeAttr(config.recording_name || config.recording_id || "") + "\">" +
                "</div>";
        }
        if (stepType === "http_request") {
            var methodOpts = HTTP_METHODS.map(function(m) {
                var sel = (config.method || "GET").toUpperCase() === m ? " selected" : "";
                return "<option value=\"" + m + "\"" + sel + ">" + m + "</option>";
            }).join("");
            return "<div class=\"step-type-fields mt-2 space-y-2\">" +
                "<div class=\"flex gap-2\">" +
                "<div class=\"w-28\"><label class=\"block text-xs text-gray-400\">Method</label>" +
                "<select class=\"step-cfg-method w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\">" + methodOpts + "</select></div>" +
                "<div class=\"flex-1\"><label class=\"block text-xs text-gray-400\">URL</label>" +
                "<input type=\"text\" class=\"step-cfg-url w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" placeholder=\"https://...\" value=\"" + escapeAttr(config.url || "") + "\"></div>" +
                "</div>" +
                "<label class=\"block text-xs text-gray-400\">Headers <span class=\"text-gray-600\">(one per line: Key: Value)</span></label>" +
                "<textarea class=\"step-cfg-headers w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm font-mono\" rows=\"3\" placeholder=\"Content-Type: application/json\">" + escapeAttr(formatHeadersForEdit(config.headers)) + "</textarea>" +
                "<label class=\"block text-xs text-gray-400\">Body</label>" +
                "<textarea class=\"step-cfg-body w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm font-mono\" rows=\"4\" placeholder=\"Request body...\">" + escapeAttr(config.body || "") + "</textarea>" +
                "</div>";
        }
        if (stepType === "execute_code" || stepType === "playwright") {
            var headlessToggle = "";
            if (stepType === "playwright") {
                var isHeadless = config.headless !== false;
                headlessToggle = "<label class=\"flex items-center gap-2 text-xs text-gray-400 mt-2\">" +
                    "<input type=\"checkbox\" class=\"step-cfg-headless\"" + (isHeadless ? " checked" : "") + ">" +
                    " Headless mode</label>";
            }
            return "<div class=\"step-type-fields mt-2 space-y-2\">" +
                "<div class=\"flex gap-1 border-b border-white/10 mb-2\">" +
                "<button type=\"button\" class=\"step-code-tab px-3 py-1 text-xs border-b-2 border-[#f97316] text-white\" data-tab=\"instruction\">Instruction</button>" +
                "<button type=\"button\" class=\"step-code-tab px-3 py-1 text-xs border-b-2 border-transparent text-gray-400\" data-tab=\"code\">Code</button>" +
                "</div>" +
                "<div class=\"step-code-panel\" data-panel=\"instruction\">" +
                "<textarea class=\"step-cfg-instruction w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" rows=\"4\" placeholder=\"Describe what the code should do...\">" + escapeAttr(instruction) + "</textarea>" +
                "<button type=\"button\" class=\"step-convert-to-code mt-2 px-3 py-1.5 rounded bg-[#1a237e] border border-white/20 text-white text-xs hover:bg-[#283593] inline-flex items-center gap-1.5\">" +
                "<span class=\"step-convert-to-code-label\">Convert to Code</span>" +
                "</button>" +
                "</div>" +
                "<div class=\"step-code-panel hidden\" data-panel=\"code\">" +
                "<textarea class=\"step-cfg-code w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm font-mono\" rows=\"6\" placeholder=\"Code...\">" + escapeAttr(code) + "</textarea>" +
                "<button type=\"button\" class=\"step-test-code mt-2 px-3 py-1.5 rounded bg-[#1a237e] border border-white/20 text-white text-xs hover:bg-[#283593] inline-flex items-center gap-1.5\">" +
                "<span class=\"step-test-code-label\">Test</span>" +
                "</button>" +
                "<div class=\"step-test-results hidden mt-3\"></div>" +
                "</div>" +
                headlessToggle +
                "</div>";
        }
        return "";
    }

    function formatHeadersForEdit(headers) {
        if (!headers || typeof headers !== "object") return "";
        return Object.keys(headers).map(function(k) { return k + ": " + headers[k]; }).join("\n");
    }

    function parseHeadersFromEdit(text) {
        var headers = {};
        (text || "").split("\n").forEach(function(line) {
            var idx = line.indexOf(":");
            if (idx > 0) {
                headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
            }
        });
        return headers;
    }

    function collectStepConfig(card, stepType) {
        var config = {};
        if (stepType === "run_command") {
            config.command = (card.querySelector(".step-cfg-command") || {}).value || "";
            config.working_directory = (card.querySelector(".step-cfg-workdir") || {}).value || "";
            config.timeout_seconds = parseInt((card.querySelector(".step-cfg-timeout") || {}).value, 10) || 60;
        } else if (stepType === "play_recording") {
            var val = (card.querySelector(".step-cfg-recording") || {}).value || "";
            if (/^\d+$/.test(val)) { config.recording_id = parseInt(val, 10); }
            else { config.recording_name = val; }
        } else if (stepType === "http_request") {
            config.url = (card.querySelector(".step-cfg-url") || {}).value || "";
            config.method = (card.querySelector(".step-cfg-method") || {}).value || "GET";
            config.headers = parseHeadersFromEdit((card.querySelector(".step-cfg-headers") || {}).value);
            config.body = (card.querySelector(".step-cfg-body") || {}).value || "";
        } else if (stepType === "execute_code" || stepType === "playwright") {
            config.instruction = (card.querySelector(".step-cfg-instruction") || {}).value || "";
            config.code = (card.querySelector(".step-cfg-code") || {}).value || "";
            if (stepType === "playwright") {
                var cb = card.querySelector(".step-cfg-headless");
                config.headless = cb ? cb.checked : true;
            }
        }
        return config;
    }

    function showValidationCallout(card, errors) {
        var callout = card.querySelector(".step-validation-callout");
        if (!callout) return;
        if (!errors || !errors.length) {
            callout.classList.add("hidden");
            callout.innerHTML = "";
            return;
        }
        callout.innerHTML = errors.map(function(e) {
            return "<div class=\"flex gap-2 text-xs\"><span class=\"text-red-400 font-medium\">" + escapeAttr(e.field) + ":</span><span class=\"text-red-300\">" + escapeAttr(e.message) + "</span></div>";
        }).join("");
        callout.classList.remove("hidden");
    }

    function validateAndExecuteStep(stepId, card) {
        var stepType = (card.querySelector(".step-type-select") || {}).value || "run_command";
        var config = collectStepConfig(card, stepType);
        // Clear previous callout
        showValidationCallout(card, []);
        fetch("/api/step-runner/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ step_type: stepType, config: config })
        })
            .then(function(r) {
                if (r.status === 422) {
                    return r.json().then(function(data) {
                        showValidationCallout(card, data.errors || []);
                    });
                }
                if (!r.ok) throw new Error("Validation request failed");
                // Validation passed — clear callout and execute
                showValidationCallout(card, []);
                executeStep(stepId);
            })
            .catch(function(e) {
                showSnackbar("Validation failed: " + e.message, "error");
            });
    }

    function renderSteps(steps) {
        var el = document.getElementById("step-runner-steps");
        if (!el) return;
        if (!steps.length) {
            el.innerHTML = "<p class=\"text-sm text-gray-500\">No steps.</p>";
            return;
        }
        var statusColors = {
            pending: "bg-gray-600/50 text-gray-400",
            approved: "bg-blue-600/50 text-blue-300",
            running: "bg-amber-600/50 text-amber-300",
            completed: "bg-green-600/50 text-green-300",
            failed: "bg-red-600/50 text-red-300",
            skipped: "bg-gray-600/30 text-gray-500",
            cancelled: "bg-gray-600/30 text-gray-500"
        };
        el.innerHTML = steps.map(function(s) {
            var sc = statusColors[s.status] || statusColors.pending;
            var stepType = s.step_type || "run_command";
            var config = parseStepConfig(s);
            var resultHtml = s.result ? "<p class=\"mt-2 text-xs text-gray-500\">" + escapeAttr(s.result).slice(0, 200) + "</p>" : "";
            return "<div class=\"rounded-lg border border-white/10 p-4 bg-[#152054]/50 step-card\" data-step-id=\"" + s.id + "\">" +
                "<div class=\"flex items-center justify-between gap-2\">" +
                "<span class=\"step-title text-sm font-medium text-white\">" + escapeAttr(s.title) + "</span>" +
                "<div class=\"flex items-center gap-2\">" +
                buildStepTypeDropdown(s.id, stepType) +
                "<span class=\"text-xs px-2 py-0.5 rounded " + sc + "\">" + escapeAttr(s.status) + "</span>" +
                "</div></div>" +
                "<p class=\"mt-1 text-sm text-gray-400 step-instruction\">" + escapeAttr(s.instruction) + "</p>" +
                "<div class=\"step-validation-callout hidden mt-2 p-2 rounded bg-red-900/30 border border-red-500/30 space-y-1\"></div>" +
                "<div class=\"step-edit-form hidden mt-2\">" +
                "<input type=\"text\" class=\"step-edit-title w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm mb-1\" placeholder=\"Title\">" +
                "<textarea class=\"step-edit-instruction w-full px-2 py-1 bg-[#152054] border border-white/10 rounded text-white text-sm\" rows=\"2\" placeholder=\"Instruction\"></textarea>" +
                buildAdaptiveFormFields(stepType, config, s) +
                "<button type=\"button\" class=\"step-save mt-2 px-3 py-1 rounded bg-[#f97316] text-white text-xs\">Save</button>" +
                "</div>" +
                resultHtml +
                "<div class=\"mt-3 flex gap-2\">" +
                "<button type=\"button\" class=\"step-edit px-3 py-1.5 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10\" data-id=\"" + s.id + "\">Edit</button>" +
                (s.status === "pending" ? "<button type=\"button\" class=\"step-execute px-3 py-1.5 rounded bg-[#f97316] text-white text-xs hover:bg-[#ea580c]\" data-id=\"" + s.id + "\">Execute</button>" : "") +
                (s.status === "pending" ? "<button type=\"button\" class=\"step-approve px-3 py-1.5 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10\" data-id=\"" + s.id + "\">Approve</button>" : "") +
                "</div>" +
                "</div>";
        }).join("");

        // Wire step type dropdown change
        el.querySelectorAll(".step-type-select").forEach(function(sel) {
            sel.addEventListener("change", function() {
                var stepId = parseInt(sel.dataset.stepId, 10);
                var newType = sel.value;
                fetch("/api/step-runner/steps/" + stepId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ step_type: newType })
                })
                    .then(function(r) {
                        if (!r.ok) throw new Error("Update failed");
                        showSnackbar("Step type updated");
                        if (currentSessionId) loadSessionDetail(currentSessionId);
                    })
                    .catch(function() { showSnackbar("Type update failed", "error"); });
            });
        });

        // Wire execute buttons through validation
        el.querySelectorAll(".step-execute").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var card = btn.closest(".step-card");
                var stepId = parseInt(btn.getAttribute("data-id"), 10);
                validateAndExecuteStep(stepId, card);
            });
        });
        el.querySelectorAll(".step-approve").forEach(function(btn) {
            btn.addEventListener("click", function() { approveStep(parseInt(btn.getAttribute("data-id"), 10)); });
        });
        el.querySelectorAll(".step-edit").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var card = btn.closest(".step-card");
                var form = card.querySelector(".step-edit-form");
                var titleSpan = card.querySelector(".step-title");
                var instP = card.querySelector(".step-instruction");
                if (form.classList.contains("hidden")) {
                    form.classList.remove("hidden");
                    form.querySelector(".step-edit-title").value = (titleSpan && titleSpan.textContent) || "";
                    form.querySelector(".step-edit-instruction").value = (instP && instP.textContent) || "";
                } else {
                    form.classList.add("hidden");
                }
            });
        });

        // Wire code tab switching for execute_code / playwright
        el.querySelectorAll(".step-code-tab").forEach(function(tab) {
            tab.addEventListener("click", function() {
                var card = tab.closest(".step-card");
                var tabName = tab.dataset.tab;
                card.querySelectorAll(".step-code-tab").forEach(function(t) {
                    t.classList.toggle("border-[#f97316]", t.dataset.tab === tabName);
                    t.classList.toggle("text-white", t.dataset.tab === tabName);
                    t.classList.toggle("border-transparent", t.dataset.tab !== tabName);
                    t.classList.toggle("text-gray-400", t.dataset.tab !== tabName);
                });
                card.querySelectorAll(".step-code-panel").forEach(function(p) {
                    p.classList.toggle("hidden", p.dataset.panel !== tabName);
                });
            });
        });

        // Wire "Convert to Code" buttons
        el.querySelectorAll(".step-convert-to-code").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var card = btn.closest(".step-card");
                var stepId = parseInt(card.dataset.stepId, 10);
                var instruction = (card.querySelector(".step-cfg-instruction") || {}).value || "";
                var stepType = (card.querySelector(".step-type-select") || {}).value || "execute_code";
                if (!instruction.trim()) {
                    showSnackbar("Write an instruction first", "error");
                    return;
                }
                var label = btn.querySelector(".step-convert-to-code-label");
                btn.disabled = true;
                if (label) label.textContent = "Generating...";
                fetch("/api/step-runner/generate-code", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ step_id: stepId, instruction: instruction, step_type: stepType })
                })
                    .then(function(r) {
                        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || "Code generation failed"); });
                        return r.json();
                    })
                    .then(function(data) {
                        var codeTextarea = card.querySelector(".step-cfg-code");
                        if (codeTextarea) codeTextarea.value = data.code || "";
                        // Switch to Code tab
                        card.querySelectorAll(".step-code-tab").forEach(function(t) {
                            t.classList.toggle("border-[#f97316]", t.dataset.tab === "code");
                            t.classList.toggle("text-white", t.dataset.tab === "code");
                            t.classList.toggle("border-transparent", t.dataset.tab !== "code");
                            t.classList.toggle("text-gray-400", t.dataset.tab !== "code");
                        });
                        card.querySelectorAll(".step-code-panel").forEach(function(p) {
                            p.classList.toggle("hidden", p.dataset.panel !== "code");
                        });
                        showSnackbar("Code generated");
                    })
                    .catch(function(e) {
                        showSnackbar(e.message || "Code generation failed", "error");
                    })
                    .finally(function() {
                        btn.disabled = false;
                        if (label) label.textContent = "Convert to Code";
                    });
            });
        });

        // Wire "Test" buttons
        el.querySelectorAll(".step-test-code").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var card = btn.closest(".step-card");
                var stepId = parseInt(card.dataset.stepId, 10);
                var code = (card.querySelector(".step-cfg-code") || {}).value || "";
                var stepType = (card.querySelector(".step-type-select") || {}).value || "execute_code";
                var headlessCb = card.querySelector(".step-cfg-headless");
                var headless = headlessCb ? headlessCb.checked : true;
                if (!code.trim()) {
                    showSnackbar("No code to test", "error");
                    return;
                }
                var label = btn.querySelector(".step-test-code-label");
                var resultsArea = card.querySelector(".step-test-results");
                btn.disabled = true;
                if (label) label.textContent = "Testing...";
                if (resultsArea) { resultsArea.classList.add("hidden"); resultsArea.innerHTML = ""; }
                fetch("/api/step-runner/test-code", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ step_id: stepId, code: code, step_type: stepType, headless: headless })
                })
                    .then(function(r) {
                        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || "Test failed"); });
                        return r.json();
                    })
                    .then(function(data) {
                        // Update code textarea with final code (may have been auto-fixed)
                        var codeTextarea = card.querySelector(".step-cfg-code");
                        if (codeTextarea && data.code) codeTextarea.value = data.code;
                        if (data.success) {
                            showSnackbar("Test passed");
                            if (resultsArea) resultsArea.classList.add("hidden");
                        } else {
                            var attemptCount = (data.attempts || []).length;
                            showSnackbar("Test failed after " + attemptCount + " attempt" + (attemptCount !== 1 ? "s" : ""), "error");
                            if (resultsArea && data.attempts && data.attempts.length) {
                                resultsArea.innerHTML = "<details class=\"mt-2\">" +
                                    "<summary class=\"text-xs text-gray-400 cursor-pointer hover:text-gray-300\">Attempt logs (" + data.attempts.length + ")</summary>" +
                                    "<div class=\"mt-1 space-y-2\">" +
                                    data.attempts.map(function(a) {
                                        var statusColor = a.exit_code === 0 ? "text-green-400" : "text-red-400";
                                        return "<div class=\"p-2 rounded bg-[#0d1541] border border-white/5 text-xs\">" +
                                            "<div class=\"flex items-center gap-2 mb-1\">" +
                                            "<span class=\"text-gray-400\">Attempt " + a.attempt + "</span>" +
                                            "<span class=\"" + statusColor + "\">exit " + a.exit_code + "</span>" +
                                            "</div>" +
                                            (a.stderr ? "<pre class=\"text-red-300 whitespace-pre-wrap break-all mt-1 max-h-32 overflow-auto\">" + escapeAttr(a.stderr).slice(0, 2000) + "</pre>" : "") +
                                            (a.stdout ? "<pre class=\"text-gray-400 whitespace-pre-wrap break-all mt-1 max-h-32 overflow-auto\">" + escapeAttr(a.stdout).slice(0, 2000) + "</pre>" : "") +
                                            "</div>";
                                    }).join("") +
                                    "</div></details>";
                                resultsArea.classList.remove("hidden");
                            }
                        }
                    })
                    .catch(function(e) {
                        showSnackbar(e.message || "Test request failed", "error");
                    })
                    .finally(function() {
                        btn.disabled = false;
                        if (label) label.textContent = "Test";
                    });
            });
        });

        // Wire save buttons
        el.querySelectorAll(".step-save").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var card = btn.closest(".step-card");
                var form = card.querySelector(".step-edit-form");
                var stepId = parseInt(card.dataset.stepId, 10);
                var title = form.querySelector(".step-edit-title").value.trim();
                var instruction = form.querySelector(".step-edit-instruction").value.trim();
                var stepType = (card.querySelector(".step-type-select") || {}).value || "run_command";
                var config = collectStepConfig(card, stepType);
                var payload = {};
                if (title) payload.title = title;
                if (instruction) payload.instruction = instruction;
                payload.config = JSON.stringify(config);
                // For execute_code/playwright, also save code separately
                if ((stepType === "execute_code" || stepType === "playwright") && config.code) {
                    payload.code = config.code;
                }
                fetch("/api/step-runner/steps/" + stepId, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                })
                    .then(function(r) {
                        if (!r.ok) throw new Error("Update failed");
                        showSnackbar("Step updated");
                        form.classList.add("hidden");
                        if (currentSessionId) loadSessionDetail(currentSessionId);
                    })
                    .catch(function() { showSnackbar("Update failed", "error"); });
            });
        });
    }

    function executeStep(stepId) {
        fetch("/api/step-runner/execute", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ step_id: stepId })
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.detail) {
                    showSnackbar(data.detail, "error");
                } else {
                    showSnackbar("Step sent to agent");
                }
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function(e) {
                showSnackbar("Execute failed", "error");
                console.error(e);
            });
    }

    function approveStep(stepId) {
        fetch("/api/step-runner/steps/" + stepId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "approved" })
        })
            .then(function(r) {
                if (!r.ok) throw new Error("Update failed");
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function(e) {
                showSnackbar("Approve failed", "error");
            });
    }

    function switchTab(tab) {
        var once = document.getElementById("step-runner-once-actions");
        var scheduled = document.getElementById("step-runner-scheduled-actions");
        var tabOnce = document.getElementById("step-runner-tab-once");
        var tabScheduled = document.getElementById("step-runner-tab-scheduled");
        if (tab === "scheduled") {
            once.classList.add("hidden");
            scheduled.classList.remove("hidden");
            tabOnce.classList.remove("border-[#f97316]", "text-white");
            tabOnce.classList.add("border-transparent", "text-gray-400");
            tabScheduled.classList.remove("border-transparent", "text-gray-400");
            tabScheduled.classList.add("border-[#f97316]", "text-white");
        } else {
            once.classList.remove("hidden");
            scheduled.classList.add("hidden");
            tabOnce.classList.remove("border-transparent", "text-gray-400");
            tabOnce.classList.add("border-[#f97316]", "text-white");
            tabScheduled.classList.remove("border-[#f97316]", "text-white");
            tabScheduled.classList.add("border-transparent", "text-gray-400");
        }
    }
    document.getElementById("step-runner-tab-once").addEventListener("click", function() { switchTab("once"); });
    document.getElementById("step-runner-tab-scheduled").addEventListener("click", function() { switchTab("scheduled"); });

    // Frequency pill buttons (create form)
    document.querySelectorAll(".sr-freq-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
            document.getElementById("step-runner-schedule-preset").value = btn.getAttribute("data-freq");
            document.querySelectorAll(".sr-freq-btn").forEach(function(b) {
                b.dataset.active = b === btn ? "1" : "0";
            });
            updateScheduleOptionsVisibility();
        });
    });

    // Day pill buttons (create form)
    document.querySelectorAll(".sr-day-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
            btn.dataset.selected = btn.dataset.selected === "1" ? "0" : "1";
            updateCreateScheduleSummary();
        });
    });

    // Time input live summary
    var timeInput = document.getElementById("step-runner-time");
    if (timeInput) timeInput.addEventListener("input", updateCreateScheduleSummary);
    var customDescInput = document.getElementById("step-runner-custom-desc");
    if (customDescInput) customDescInput.addEventListener("input", updateCreateScheduleSummary);

    // "Now" buttons
    document.getElementById("step-runner-time-now").addEventListener("click", function() {
        var now = new Date();
        var hh = String(now.getHours()).padStart(2, "0");
        var mm = String(now.getMinutes()).padStart(2, "0");
        document.getElementById("step-runner-time").value = hh + ":" + mm;
        updateCreateScheduleSummary();
    });
    document.getElementById("step-runner-detail-time-now").addEventListener("click", function() {
        var now = new Date();
        var hh = String(now.getHours()).padStart(2, "0");
        var mm = String(now.getMinutes()).padStart(2, "0");
        document.getElementById("step-runner-detail-time").value = hh + ":" + mm;
        updateDetailScheduleSummary();
    });

    // Init: select "daily" pill by default
    (function() {
        var defaultBtn = document.querySelector(".sr-freq-btn[data-freq='daily']");
        if (defaultBtn) {
            defaultBtn.dataset.active = "1";
            document.getElementById("step-runner-schedule-preset").value = "daily";
        }
        updateScheduleOptionsVisibility();
    })();

    function updateScheduleOptionsVisibility() {
        var preset = document.getElementById("step-runner-schedule-preset").value || "daily";
        var timeWrap = document.getElementById("step-runner-option-time");
        var daysWrap = document.getElementById("step-runner-option-days");
        var customWrap = document.getElementById("step-runner-option-custom");
        if (timeWrap) timeWrap.classList.toggle("hidden", preset === "hourly" || preset === "custom");
        if (daysWrap) daysWrap.classList.toggle("hidden", preset !== "weekly");
        if (customWrap) customWrap.classList.toggle("hidden", preset !== "custom");
        updateCreateScheduleSummary();
    }

    function buildScheduleSummary(preset, time, days, customDesc) {
        if (preset === "hourly") return "Runs every hour";
        if (preset === "daily") return "Runs daily at " + (time || "09:00");
        if (preset === "weekly") {
            var DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
            var dayLabels = (days || ["1"]).map(function(d) { return DAY_NAMES[parseInt(d, 10)] || d; });
            return "Runs every " + dayLabels.join(", ") + " at " + (time || "09:00");
        }
        if (preset === "custom") {
            if (!customDesc) return "";
            var parsed = _parseCustomDesc(customDesc.trim().toLowerCase());
            if (!parsed) return "⚠ Couldn't understand that — try: 'every weekday at 9am' or 'every 2 hours'";
            return "→ " + parsed.label;
        }
        return "";
    }

    function getCreateSelectedDays() {
        var days = [];
        document.querySelectorAll(".sr-day-btn").forEach(function(btn) {
            if (btn.dataset.selected === "1") days.push(btn.getAttribute("data-day"));
        });
        return days.length ? days : ["1"];
    }

    function getDetailSelectedDays() {
        var days = [];
        document.querySelectorAll(".sr-detail-day-btn").forEach(function(btn) {
            if (btn.dataset.selected === "1") days.push(btn.getAttribute("data-day"));
        });
        return days.length ? days : ["1"];
    }

    function updateCreateScheduleSummary() {
        var el = document.getElementById("step-runner-schedule-summary");
        if (!el) return;
        var preset = document.getElementById("step-runner-schedule-preset").value || "daily";
        var time = (document.getElementById("step-runner-time") || {}).value || "09:00";
        var summary = buildScheduleSummary(preset, time, getCreateSelectedDays(), (document.getElementById("step-runner-custom-desc") || {}).value || "");
        el.textContent = summary;
        el.style.color = summary.startsWith("\u26a0") ? "#f87171" : "#f97316";
    }

    function getSchedulePayload() {
        var preset = document.getElementById("step-runner-schedule-preset").value || "daily";
        var payload = { schedule: preset };
        if (preset === "daily") {
            payload.schedule_time = (document.getElementById("step-runner-time") || {}).value || "09:00";
        } else if (preset === "weekly") {
            payload.schedule_time = (document.getElementById("step-runner-time") || {}).value || "09:00";
            var days = getCreateSelectedDays();
            payload.schedule_days = days.sort().join(",");
        } else if (preset === "custom") {
            var desc = ((document.getElementById("step-runner-custom-desc") || {}).value || "").trim().toLowerCase();
            var parsed = _parseCustomDesc(desc);
            if (!parsed) return null; // caller must check
            payload.schedule = parsed.cron;
        }
        return payload;
    }

    function _parseCustomDesc(desc) {
        // Returns { cron, label } or null if unrecognised
        var timeMatch = desc.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/);
        var h = 9, m = 0;
        if (timeMatch) {
            h = parseInt(timeMatch[1], 10);
            m = timeMatch[2] ? parseInt(timeMatch[2], 10) : 0;
            if (timeMatch[3] === "pm" && h < 12) h += 12;
            if (timeMatch[3] === "am" && h === 12) h = 0;
        }
        var timeLabel = (h % 12 || 12) + (m ? ":" + String(m).padStart(2, "0") : "") + (h < 12 ? "am" : "pm");

        // "every N hours" / "every hour"
        var mHours = desc.match(/every\s+(\d+)\s+hours?/);
        if (mHours) return { cron: "0 */" + mHours[1] + " * * *", label: "Every " + mHours[1] + " hours" };
        if (/every\s+hour/.test(desc)) return { cron: "0 * * * *", label: "Every hour" };

        // "every N minutes"
        var mMins = desc.match(/every\s+(\d+)\s+min/);
        if (mMins) return { cron: "*/" + mMins[1] + " * * * *", label: "Every " + mMins[1] + " minutes" };

        // weekday / workday / mon-fri
        if (/weekday|mon.*fri|work\s*day/.test(desc)) {
            if (!timeMatch) return { cron: "0 9 * * 1-5", label: "Weekdays at 9am" };
            return { cron: m + " " + h + " * * 1-5", label: "Weekdays at " + timeLabel };
        }
        // weekend
        if (/weekend/.test(desc)) {
            if (!timeMatch) return { cron: "0 9 * * 0,6", label: "Weekends at 9am" };
            return { cron: m + " " + h + " * * 0,6", label: "Weekends at " + timeLabel };
        }
        // daily / every day
        if (/daily|every\s+day/.test(desc)) {
            if (!timeMatch) return { cron: "0 9 * * *", label: "Daily at 9am" };
            return { cron: m + " " + h + " * * *", label: "Daily at " + timeLabel };
        }
        // specific days: monday, tuesday, etc.
        var dayMap = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
        var foundDays = [];
        Object.keys(dayMap).forEach(function(d) {
            if (desc.indexOf(d) >= 0) foundDays.push(dayMap[d]);
        });
        if (foundDays.length) {
            var DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
            var dayStr = foundDays.sort().join(",");
            var dayLabel = foundDays.map(function(d) { return DAY_NAMES[d]; }).join(", ");
            if (!timeMatch) return { cron: "0 9 * * " + dayStr, label: dayLabel + " at 9am" };
            return { cron: m + " " + h + " * * " + dayStr, label: dayLabel + " at " + timeLabel };
        }
        // bare time only → daily at that time
        if (timeMatch) {
            return { cron: m + " " + h + " * * *", label: "Daily at " + timeLabel };
        }
        return null;
    }

    document.getElementById("step-runner-create-scheduled").addEventListener("click", function() {
        var textarea = document.getElementById("step-runner-instruction");
        var instruction = (textarea && textarea.value || "").trim();
        if (!instruction) {
            showSnackbar("Enter an instruction first", "error");
            return;
        }
        var payload = getSchedulePayload();
        if (!payload) {
            showSnackbar("Couldn't understand that schedule — try 'every weekday at 9am'", "error");
            return;
        }
        payload.instruction = instruction;
        var btn = this;
        btn.disabled = true;
        btn.textContent = "Creating...";
        fetch("/api/step-runner/scheduled", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || "Create failed"); });
                return r.json();
            })
            .then(function(data) {
                showSnackbar("Scheduled action created");
                loadSessions();
                currentSessionId = data.id;
                renderSessions();
                loadSessionDetail(data.id);
            })
            .catch(function(e) {
                showSnackbar(e.message || "Create failed", "error");
            })
            .finally(function() {
                btn.disabled = false;
                btn.textContent = "Schedule it";
            });
    });

    document.getElementById("step-runner-enabled").addEventListener("change", function() {
        var id = this.dataset.sessionId;
        if (!id) return;
        fetch("/api/step-runner/sessions/" + id + "/schedule", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: this.checked })
        })
            .then(function(r) {
                if (!r.ok) throw new Error("Update failed");
                showSnackbar(this.checked ? "Enabled" : "Disabled");
                if (currentSessionId) loadSessionDetail(currentSessionId);
            }.bind(this))
            .catch(function() { showSnackbar("Update failed", "error"); });
    });

    document.getElementById("step-runner-search").addEventListener("input", function() { loadSessions(); });
    document.getElementById("step-runner-filter").addEventListener("change", function() { loadSessions(); });

    var detailPresetEl = document.getElementById("step-runner-detail-preset");
    if (detailPresetEl) detailPresetEl.addEventListener("change", updateDetailScheduleOptionsVisibility);

    // Detail freq pill buttons
    document.querySelectorAll(".sr-detail-freq-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
            document.getElementById("step-runner-detail-preset").value = btn.getAttribute("data-freq");
            document.querySelectorAll(".sr-detail-freq-btn").forEach(function(b) {
                b.dataset.active = b === btn ? "1" : "0";
            });
            updateDetailScheduleOptionsVisibility();
            updateDetailScheduleSummary();
        });
    });

    // Detail day pill buttons
    document.querySelectorAll(".sr-detail-day-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
            btn.dataset.selected = btn.dataset.selected === "1" ? "0" : "1";
            updateDetailScheduleSummary();
        });
    });

    // Detail time + custom desc live summary
    var detailTimeInput = document.getElementById("step-runner-detail-time");
    if (detailTimeInput) detailTimeInput.addEventListener("input", updateDetailScheduleSummary);
    var detailCronInput = document.getElementById("step-runner-detail-cron");
    if (detailCronInput) detailCronInput.addEventListener("input", updateDetailScheduleSummary);

    document.getElementById("step-runner-save-schedule").addEventListener("click", function() {
        var id = currentSessionId;
        if (!id) return;
        var preset = (document.getElementById("step-runner-detail-preset") || {}).value || "daily";
        var payload = { schedule: preset };
        if (preset === "daily") {
            payload.schedule_time = (document.getElementById("step-runner-detail-time") || {}).value || "09:00";
        } else if (preset === "weekly") {
            payload.schedule_time = (document.getElementById("step-runner-detail-time") || {}).value || "09:00";
            payload.schedule_days = getDetailSelectedDays().sort().join(",");
        } else if (preset === "custom") {
            var desc = ((document.getElementById("step-runner-detail-cron") || {}).value || "").trim().toLowerCase();
            var parsed = _parseCustomDesc(desc);
            if (!parsed) {
                showSnackbar("Couldn't understand that schedule — try 'every weekday at 9am'", "error");
                return;
            }
            payload.schedule = parsed.cron;
        }
        fetch("/api/step-runner/sessions/" + id + "/schedule", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
            .then(function(r) {
                if (!r.ok) throw new Error("Update failed");
                showSnackbar("Schedule updated");
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function() { showSnackbar("Update failed", "error"); });
    });

    document.getElementById("step-runner-run-all").addEventListener("click", function() {
        var id = this.dataset.sessionId;
        if (!id) return;
        this.disabled = true;
        // Optimistic reset: immediately show all steps as pending
        var stepCards = document.querySelectorAll("#step-runner-steps .step-card");
        stepCards.forEach(function(card) {
            var badge = card.querySelector(".text-xs.px-2");
            if (badge) {
                badge.className = "text-xs px-2 py-0.5 rounded bg-gray-600/50 text-gray-400";
                badge.textContent = "pending";
            }
            // Remove execute/approve buttons so they don't double-fire; they'll re-render on reload
        });
        fetch("/api/step-runner/sessions/" + id + "/run-all", { method: "POST" })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.detail) {
                    showSnackbar(data.detail, "error");
                } else {
                    showSnackbar("Running all steps in sequence");
                }
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function() { showSnackbar("Run all failed", "error"); })
            .finally(function() {
                var btn = document.getElementById("step-runner-run-all");
                if (btn) btn.disabled = false;
            });
    });

    document.getElementById("step-runner-cancel").addEventListener("click", function() {
        var id = this.dataset.sessionId;
        if (!id) return;
        fetch("/api/step-runner/sessions/" + id + "/cancel", { method: "POST" })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.detail) {
                    showSnackbar(data.detail, "error");
                } else {
                    showSnackbar("Session cancelled");
                }
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function() { showSnackbar("Cancel failed", "error"); });
    });

    document.getElementById("step-runner-skip-step").addEventListener("click", function() {
        var id = this.dataset.sessionId;
        if (!id) return;
        fetch("/api/step-runner/sessions/" + id + "/skip-step", { method: "POST" })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.detail) {
                    showSnackbar(data.detail, "error");
                } else {
                    showSnackbar("Skipped current step");
                }
                if (currentSessionId) loadSessionDetail(currentSessionId);
            })
            .catch(function() { showSnackbar("Skip failed", "error"); });
    });

    document.getElementById("step-runner-delete").addEventListener("click", function() {
        var id = this.dataset.sessionId;
        if (!id || !confirm("Delete this session? This cannot be undone.")) return;
        fetch("/api/step-runner/sessions/" + id, { method: "DELETE" })
            .then(function(r) {
                if (!r.ok) throw new Error("Delete failed");
                showSnackbar("Session deleted");
                currentSessionId = null;
                loadSessions();
                document.getElementById("step-runner-empty").classList.remove("hidden");
                document.getElementById("step-runner-detail").classList.add("hidden");
            })
            .catch(function() { showSnackbar("Delete failed", "error"); });
    });

    document.getElementById("step-runner-duplicate").addEventListener("click", function() {
        var id = this.dataset.sessionId;
        if (!id) return;
        fetch("/api/step-runner/sessions/" + id + "/duplicate", { method: "POST" })
            .then(function(r) {
                if (!r.ok) throw new Error("Duplicate failed");
                return r.json();
            })
            .then(function(data) {
                showSnackbar("Session duplicated");
                currentSessionId = data.id;
                loadSessions();
                renderSessions();
                loadSessionDetail(data.id);
            })
            .catch(function() { showSnackbar("Duplicate failed", "error"); });
    });

    document.getElementById("step-runner-plan").addEventListener("click", function() {
        var textarea = document.getElementById("step-runner-instruction");
        var instruction = (textarea && textarea.value || "").trim();
        if (!instruction) {
            showSnackbar("Enter an instruction first", "error");
            return;
        }
        var btn = this;
        btn.disabled = true;
        btn.textContent = "Planning...";
        fetch("/api/step-runner/plan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ instruction: instruction })
        })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || "Plan failed"); });
                return r.json();
            })
            .then(function(data) {
                showSnackbar("Steps planned");
                loadSessions();
                currentSessionId = data.id;
                renderSessions();
                loadSessionDetail(data.id);
            })
            .catch(function(e) {
                showSnackbar(e.message || "Plan failed", "error");
            })
            .finally(function() {
                btn.disabled = false;
                btn.textContent = "Plan Steps";
            });
    });

    loadSessions();

    var _lastStepRunnerVersion = 0;
    var _stepRunnerVersionPollTimeout = null;
    function _pollStepRunnerVersion() {
        fetch("/api/step-runner/version")
            .then(function(r) { return r.ok ? r.json() : {}; })
            .then(function(d) {
                var v = d.version || 0;
                if (v !== _lastStepRunnerVersion) {
                    _lastStepRunnerVersion = v;
                    loadSessions();
                    if (currentSessionId) loadSessionDetail(currentSessionId);
                }
            })
            .catch(function() {})
            .finally(function() {
                var delay = currentSessionStatus === "in_progress" ? 2000 : 5000;
                _stepRunnerVersionPollTimeout = setTimeout(_pollStepRunnerVersion, delay);
            });
    }
    fetch("/api/step-runner/version")
        .then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(d) { _lastStepRunnerVersion = d.version || 0; })
        .catch(function() {})
        .finally(function() { _stepRunnerVersionPollTimeout = setTimeout(_pollStepRunnerVersion, 5000); });
})();
