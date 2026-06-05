/**
 * Automations page: matches Actions layout.
 * Left: search + automation list + Add. Right: empty state or detail form with run history inside the item.
 */
(function () {
    "use strict";

    var currentAutomationId = null;
    var automationsData = [];
    var searchText = "";
    var isCreating = false;

    var apiFetch = window.DecisionsAPI ? window.DecisionsAPI.fetch : function(path, options) {
        return fetch(path, options).then(function(res) {
            if (!res.ok) throw new Error(res.statusText || "Request failed");
            return res.json();
        });
    };

    function showSnackbar(msg, type) {
        if (window.DecisionsAPI && window.DecisionsAPI.snackbar) {
            window.DecisionsAPI.snackbar(msg, type || "info", { id: "automation-snackbar" });
        }
    }

    function automationKeyboardTargetIsEditable(target) {
        if (!target) return false;
        var tagName = String(target.tagName || "").toLowerCase();
        return tagName === "input" ||
            tagName === "textarea" ||
            tagName === "select" ||
            target.isContentEditable;
    }

    function escapeAttr(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function shortDate(value) {
        if (!value) return "-";
        try {
            return new Date(value).toLocaleString();
        } catch (_) {
            return value;
        }
    }

    function scheduleLabel(schedule) {
        var cfg = schedule || {};
        var kind = String(cfg.kind || cfg.frequency || "daily");
        var labels = {
            once: "Once",
            "15min": "Every 15 minutes",
            "30min": "Every 30 minutes",
            hourly: "Hourly",
            daily: "Daily",
            weekly: "Weekly"
        };
        if (kind === "once") return cfg.run_at ? "Once at " + shortDate(cfg.run_at) : "Once";
        if (cfg.time && (kind === "daily" || kind === "weekly")) return (labels[kind] || kind) + " at " + cfg.time;
        return labels[kind] || kind;
    }

    function emptyAutomation() {
        return {
            id: "",
            name: "",
            status: "active",
            instruction: "",
            schedule: { kind: "daily", time: "09:00" },
            next_run_at: null
        };
    }

    function filterAutomations(data) {
        if (!searchText.trim()) return data;
        var q = searchText.trim().toLowerCase();
        return data.filter(function(a) {
            return String(a.name || "").toLowerCase().indexOf(q) !== -1 ||
                String(a.instruction || "").toLowerCase().indexOf(q) !== -1;
        });
    }

    function renderList(data) {
        var el = document.getElementById("automation-list");
        if (!el) return;
        if (!Array.isArray(data)) data = [];
        automationsData = data;
        var filtered = filterAutomations(data);
        if (!filtered.length) {
            el.innerHTML = data.length === 0
                ? '<p class="text-sm text-gray-400">No automations yet. Create one with Add Automation.</p>'
                : '<p class="text-sm text-gray-400">No automations match.</p>';
            return;
        }
        el.innerHTML = filtered.map(function(a) {
            var active = currentAutomationId === a.id ? " bg-white/10 border-[#f97316]" : " border-transparent hover:bg-white/5";
            var status = a.status === "active" ? "text-emerald-300" : "text-gray-500";
            return '<div class="automation-item-wrapper flex items-center gap-1 rounded border' + active + ' group focus:outline-none focus:ring-2 focus:ring-[#f97316]/60" data-id="' + escapeAttr(a.id) + '" tabindex="0" role="option" aria-selected="' + (currentAutomationId === a.id ? "true" : "false") + '">' +
                '<button type="button" class="automation-item flex-1 min-w-0 text-left px-3 py-2 text-white text-sm" data-id="' + escapeAttr(a.id) + '">' +
                    '<span class="block truncate">' + escapeAttr(a.name || "Untitled Automation") + '</span>' +
                    '<span class="block truncate text-xs text-gray-500 mt-0.5">' + escapeAttr(scheduleLabel(a.schedule)) + '</span>' +
                '</button>' +
                '<span class="px-2 text-xs ' + status + '">' + escapeAttr(a.status || "active") + '</span>' +
            '</div>';
        }).join("");
        el.querySelectorAll(".automation-item").forEach(function(btn) {
            btn.addEventListener("click", function(e) {
                e.stopPropagation();
                selectAutomation(btn.getAttribute("data-id"));
            });
        });
        el.querySelectorAll(".automation-item-wrapper").forEach(function(row) {
            row.addEventListener("focus", function() {
                var id = row.getAttribute("data-id");
                if (id && currentAutomationId !== id) selectAutomation(id);
            });
        });
    }

    function showEmpty() {
        document.getElementById("automation-empty").classList.remove("hidden");
        document.getElementById("automation-detail").classList.add("hidden");
        currentAutomationId = null;
        isCreating = false;
        renderList(automationsData);
    }

    function showDetail() {
        document.getElementById("automation-empty").classList.add("hidden");
        document.getElementById("automation-detail").classList.remove("hidden");
    }

    function setValue(id, value) {
        var el = document.getElementById(id);
        if (el) el.value = value == null ? "" : String(value);
    }

    function fillForm(row) {
        row = row || emptyAutomation();
        var schedule = row.schedule || {};
        setValue("automation-id", row.id || "");
        setValue("automation-name", row.name || "");
        setValue("automation-instruction", row.instruction || "");
        setValue("automation-kind", schedule.kind || schedule.frequency || "daily");
        setValue("automation-time", schedule.time || "09:00");
        setValue("automation-run-at", String(schedule.run_at || "").replace(/Z$/, ""));
        setValue("automation-status", row.status || "active");
        document.getElementById("automation-detail-title").textContent = row.id ? (row.name || "Untitled Automation") : "New Automation";
        document.getElementById("automation-detail-meta").textContent = row.id ? ("Next run: " + shortDate(row.next_run_at)) : "Not saved yet";
        document.getElementById("automation-delete").disabled = !row.id;
        document.getElementById("automation-run").disabled = !row.id;
        updateScheduleControls();
    }

    function renderRuns(runs) {
        var el = document.getElementById("automation-runs");
        if (!el) return;
        if (!currentAutomationId) {
            el.innerHTML = '<p class="text-xs text-gray-500">Save an automation to see runs.</p>';
            return;
        }
        if (!runs || !runs.length) {
            el.innerHTML = '<p class="text-xs text-gray-500">No runs yet.</p>';
            return;
        }
        el.innerHTML = runs.map(function(run) {
            var chat = run.chat_id ? " · chat #" + run.chat_id : "";
            return '<div class="rounded border border-white/10 bg-[#152054] px-3 py-2">' +
                '<div class="flex items-center justify-between gap-2">' +
                    '<span class="text-sm text-white">' + escapeAttr(run.status || "recorded") + '</span>' +
                    '<span class="text-xs text-gray-500">' + escapeAttr(shortDate(run.started_at)) + '</span>' +
                '</div>' +
                '<div class="text-xs text-gray-400 mt-1">' + escapeAttr((run.summary || "Automation run recorded.") + chat) + '</div>' +
            '</div>';
        }).join("");
    }

    function selectAutomation(id) {
        var row = automationsData.filter(function(a) { return a.id === id; })[0];
        if (!row) {
            showEmpty();
            return;
        }
        currentAutomationId = id;
        isCreating = false;
        fillForm(row);
        showDetail();
        setActiveTab("details");
        renderList(automationsData);
        apiFetch("/api/automations/" + encodeURIComponent(id) + "/runs")
            .then(function(data) { renderRuns(data.runs || []); })
            .catch(function() { renderRuns([]); });
    }

    function createNewAutomation() {
        currentAutomationId = null;
        isCreating = true;
        fillForm(emptyAutomation());
        showDetail();
        renderRuns([]);
        renderList(automationsData);
        var name = document.getElementById("automation-name");
        if (name) name.focus();
        setActiveTab("details");
    }

    function payloadFromForm() {
        var kind = document.getElementById("automation-kind").value || "daily";
        var schedule = { kind: kind };
        var time = document.getElementById("automation-time").value;
        var runAt = document.getElementById("automation-run-at").value;
        if (time) schedule.time = time;
        if (runAt) schedule.run_at = runAt;
        return {
            name: (document.getElementById("automation-name").value || "Untitled Automation").trim(),
            automation_type: "scheduled_instruction",
            status: document.getElementById("automation-status").value || "active",
            instruction: (document.getElementById("automation-instruction").value || "").trim(),
            schedule: schedule
        };
    }

    function loadAutomations(skipAutoSelect) {
        var list = document.getElementById("automation-list");
        if (list) list.innerHTML = '<p class="text-sm text-gray-400">Loading automations...</p>';
        return apiFetch("/api/automations")
            .then(function(data) {
                automationsData = data.automations || [];
                renderList(automationsData);
                if (currentAutomationId && automationsData.some(function(a) { return a.id === currentAutomationId; })) {
                    selectAutomation(currentAutomationId);
                } else if (!skipAutoSelect && automationsData.length) {
                    selectAutomation(automationsData[0].id);
                } else if (!isCreating) {
                    showEmpty();
                }
            })
            .catch(function(e) {
                if (list) list.innerHTML = '<p class="text-sm text-amber-400">Could not load automations.</p>';
                showSnackbar(e.message || "Could not load automations", "error");
            });
    }

    function saveAutomation(evt) {
        evt.preventDefault();
        var id = document.getElementById("automation-id").value;
        var method = id ? "PUT" : "POST";
        var path = id ? "/api/automations/" + encodeURIComponent(id) : "/api/automations";
        apiFetch(path, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payloadFromForm())
        }).then(function(data) {
            currentAutomationId = (data.automation || {}).id || id;
            isCreating = false;
            showSnackbar("Automation saved", "success");
            return loadAutomations(true);
        }).catch(function(e) {
            showSnackbar(e.message || "Could not save automation", "error");
        });
    }

    function deleteSelected(idOverride) {
        var id = idOverride || document.getElementById("automation-id").value;
        if (!id) return;
        var row = automationsData.filter(function(a) { return a.id === id; })[0] || {};
        window.DecisionsAPI.confirm({
            title: "Remove automation",
            message: 'Remove automation "' + (row.name || "Untitled Automation") + '"? This cannot be undone.',
            confirmLabel: "Remove",
            danger: true,
            onConfirm: function() {
                apiFetch("/api/automations/" + encodeURIComponent(id), { method: "DELETE" })
                    .then(function() {
                        currentAutomationId = null;
                        isCreating = false;
                        showSnackbar("Automation removed", "success");
                        return loadAutomations();
                    })
                    .catch(function(e) { showSnackbar(e.message || "Could not remove automation", "error"); });
            }
        });
    }

    function runSelected() {
        var id = document.getElementById("automation-id").value;
        if (!id) {
            showSnackbar("Save the automation before running it.", "error");
            return;
        }
        apiFetch("/api/automations/" + encodeURIComponent(id) + "/run", { method: "POST" })
            .then(function(data) {
                showSnackbar("Automation run recorded", "success");
                setActiveTab("history");
                renderRuns(data && data.run ? [data.run] : []);
                return loadAutomations(true).then(function() {
                    setActiveTab("history");
                    return apiFetch("/api/automations/" + encodeURIComponent(id) + "/runs")
                        .then(function(runsData) { renderRuns(runsData.runs || []); })
                        .catch(function() {});
                });
            })
            .catch(function(e) { showSnackbar(e.message || "Could not run automation", "error"); });
    }

    function bindKeyboard() {
        var listEl = document.getElementById("automation-list");
        if (!listEl) return;
        listEl.addEventListener("keydown", function(e) {
            if (document.getElementById("decisions-confirm-modal") || automationKeyboardTargetIsEditable(e.target)) return;
            var rows = Array.prototype.slice.call(document.querySelectorAll("#automation-list .automation-item-wrapper"));
            if (!rows.length) return;
            var active = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".automation-item-wrapper") : null;
            var idx = rows.indexOf(active);
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                var next = rows[Math.max(0, Math.min(rows.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)))];
                if (next) next.focus();
            } else if (e.key === "Enter" && active) {
                e.preventDefault();
                selectAutomation(active.getAttribute("data-id"));
            } else if ((e.key === "Delete" || e.key === "Backspace") && active) {
                e.preventDefault();
                deleteSelected(active.getAttribute("data-id"));
            }
        });

        document.addEventListener("keydown", function(e) {
            if (document.getElementById("decisions-confirm-modal") || automationKeyboardTargetIsEditable(e.target)) return;
            if (e.key !== "Delete" && e.key !== "Backspace") return;
            var id = document.getElementById("automation-id").value;
            if (!id) return;
            e.preventDefault();
            deleteSelected(id);
        });
    }

    function setActiveTab(tab) {
        tab = tab === "history" ? "history" : "details";
        document.querySelectorAll(".automation-tab").forEach(function(btn) {
            var active = btn.getAttribute("data-tab") === tab;
            btn.classList.toggle("border-[#f97316]", active);
            btn.classList.toggle("border-transparent", !active);
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
        });
        document.querySelectorAll(".automation-tab-pane").forEach(function(pane) {
            pane.classList.toggle("hidden", pane.id !== "automation-tab-" + tab);
        });
    }

    function updateScheduleControls() {
        var kind = document.getElementById("automation-kind");
        var time = document.getElementById("automation-time");
        var runAt = document.getElementById("automation-run-at");
        if (!kind || !time || !runAt) return;
        var once = kind.value === "once";
        time.disabled = once;
        runAt.disabled = !once;
        [time, runAt].forEach(function(el) {
            var disabled = el.disabled;
            var group = el.parentElement;
            el.style.cursor = disabled ? "not-allowed" : "";
            el.style.opacity = disabled ? "0.48" : "1";
            if (group) group.style.opacity = disabled ? "0.62" : "1";
            if (window.DecisionsDateTime) window.DecisionsDateTime.refreshInput(el);
        });
    }

    function bind() {
        document.getElementById("automation-search").addEventListener("input", function(e) {
            searchText = e.target.value || "";
            renderList(automationsData);
        });
        document.getElementById("automation-new").addEventListener("click", createNewAutomation);
        document.getElementById("automation-create-big").addEventListener("click", createNewAutomation);
        document.getElementById("automation-detail").addEventListener("submit", saveAutomation);
        document.getElementById("automation-delete").addEventListener("click", deleteSelected);
        document.getElementById("automation-run").addEventListener("click", runSelected);
        document.getElementById("automation-kind").addEventListener("change", updateScheduleControls);
        document.querySelectorAll(".automation-tab").forEach(function(btn) {
            btn.addEventListener("click", function() {
                setActiveTab(btn.getAttribute("data-tab"));
            });
        });
        bindKeyboard();
    }

    document.addEventListener("DOMContentLoaded", function() {
        bind();
        loadAutomations();
    });
})();
