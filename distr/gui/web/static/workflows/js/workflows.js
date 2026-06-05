/**
 * Workflows UI — ticket queue, run visibility, and execution rules.
 * Internal steps still exist in the engine, but the main UI treats them as orchestration detail.
 */
(function () {
    var API = "/api";
    var currentWorkflowId = null;
    var currentWorkflow = null;
    var expandedStepId = null;
    var activeStepTab = {};  // stepId -> active tab name
    var pollTimer = null;
    var executionSessionPollTimer = null;
    var lastKnownVersion = null;
    var versionPollTimer = null;
    var ws = null;
    var wsReconnectTimer = null;
    var activeRunsScope = "all";
    var workflowRunsSubtab = "active";
    var latestActiveRuns = [];
    var latestWorkflowExecutionSessions = [];
    var latestHermesEvents = [];
    var latestBoardActivity = null;
    var latestLearnedRules = [];
    var wfContextMenuEl = null;
    var wfContextMenuId = null;
    var workflowRuntimeStateById = {};
    var workflowBoardOptions = [];
    var workflowDatabaseBoards = [];
    var workflowBoardLoadToken = 0;
    var workflowBoardCollapsedColumns = {};
    var selectedWorkflowBoardTicketKey = "";
    var workflowBoardTicketByKey = {};
    var workflowLinkable = { projects: [], workflows: [] };
    var workflowQueueTickets = [];
    var workflowActionsCatalog = [];
    var workflowActionsCatalogLoaded = false;
    var workflowSkillsCatalog = [];
    var workflowSkillsCatalogLoaded = false;
    var workflowQueueBoardFilter = "all";
    var workflowQueueDragTicketId = null;
    var workflowBoardDragPayload = null;
    var workflowDropDocumentBound = false;
    var workflowLastDragPoint = null;
    var workflowPendingTicketLinks = {};
    var workflowLinkedExternalTicketKeys = {};
    var selectedWorkflowCliTicketId = null;
    var workflowTicketModalState = null;
    var workflowWhatsappTicketDraft = null;
    var workflowWhatsappProgressTimer = null;
    var workflowWhatsappProgressValue = 8;
    var expandedWorkflowExecutionSessionId = null;
    var pendingWorkflowRunTicketId = null;
    var WHATSAPP_ICON_SVG = '<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.27-1.38a9.9 9.9 0 0 0 4.77 1.21h.01c5.46 0 9.91-4.45 9.91-9.91C21.96 6.45 17.51 2 12.04 2Zm0 18.16h-.01a8.2 8.2 0 0 1-4.18-1.14l-.3-.18-3.12.82.83-3.04-.2-.31a8.2 8.2 0 0 1-1.26-4.39c0-4.54 3.7-8.24 8.25-8.24 2.2 0 4.27.86 5.82 2.42a8.2 8.2 0 0 1 2.42 5.83c0 4.54-3.7 8.23-8.25 8.23Zm4.52-6.17c-.25-.12-1.47-.72-1.7-.81-.23-.08-.39-.12-.56.13-.16.24-.64.8-.78.96-.14.16-.29.18-.54.06-.25-.13-1.04-.38-1.99-1.22-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.02-.38.11-.5.11-.11.25-.29.37-.43.12-.15.16-.25.25-.41.08-.17.04-.31-.02-.43-.06-.13-.56-1.35-.77-1.85-.2-.49-.41-.42-.56-.43h-.48c-.16 0-.43.06-.65.31-.23.25-.86.84-.86 2.04 0 1.2.88 2.37 1 2.53.12.17 1.73 2.64 4.18 3.7.58.25 1.04.4 1.39.51.58.18 1.11.16 1.53.1.47-.07 1.47-.6 1.68-1.18.21-.58.21-1.08.14-1.18-.06-.1-.22-.16-.47-.28Z"/></svg>';
    var DEFAULT_RUN_SETTINGS = {
        execution_mode: "sequential",
        concurrency_scope: "project",
        max_parallel_tickets: 3,
        branch_per_ticket: true,
        max_correction_attempts: 1,
        auto_dispatch_corrections: false
    };

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

    function normalizeStepConfig(step) {
        var config = step && step.config ? step.config : {};
        if (typeof config === "string") {
            try { config = JSON.parse(config); } catch (e) { config = {}; }
        }
        if (!config || typeof config !== "object" || Array.isArray(config)) return {};
        return Object.assign({}, config);
    }

    function boardSourceLabel(source) {
        source = (source || "database").toLowerCase();
        if (source === "database") return "Local";
        if (source === "trello") return "Trello";
        if (source === "jira") return "Jira";
        return source.charAt(0).toUpperCase() + source.slice(1);
    }

    function workflowBoardCollapseKey(selected, lane) {
        var boardKey = selected ? selected.value : "board";
        var laneKey = lane && lane.id != null ? lane.id : (lane && lane.name ? lane.name : "lane");
        return boardKey + "::" + String(laneKey);
    }

    function loadWorkflowBoardCollapsedColumns() {
        try {
            workflowBoardCollapsedColumns = JSON.parse(localStorage.getItem("wf_board_collapsed_columns") || "{}") || {};
        } catch (e) {
            workflowBoardCollapsedColumns = {};
        }
    }

    function saveWorkflowBoardCollapsedColumns() {
        try { localStorage.setItem("wf_board_collapsed_columns", JSON.stringify(workflowBoardCollapsedColumns || {})); } catch (e) {}
    }

    function workflowTicketKey(selected, lane, ticket) {
        var boardKey = selected ? selected.value : "board";
        var laneKey = lane && lane.id != null ? lane.id : (lane && lane.name ? lane.name : "lane");
        var ticketKey = ticket && ticket.id != null ? ticket.id : (ticket && ticket.title ? ticket.title : "ticket");
        return boardKey + "::" + String(laneKey) + "::" + String(ticketKey);
    }

    function workflowPayloadLinkKey(payload) {
        if (!payload) return "";
        var source = (payload.source || payload.external_source || "database").toLowerCase();
        if (source !== "database") {
            var externalId = payload.external_id || payload.ticket_id || "";
            return externalId ? (source + ":" + String(externalId)) : "";
        }
        return payload.ticket_id ? ("database:" + String(payload.ticket_id)) : "";
    }

    function workflowTicketExternalLinkKey(selected, ticket) {
        if (!selected || !ticket || selected.source === "database") return "";
        var externalId = ticket.id || ticket.key || ticket.external_id || "";
        return externalId ? (selected.source + ":" + String(externalId)) : "";
    }

    function workflowStatusBadge(ticket) {
        var raw = String((ticket && (ticket.workflow_status || ticket.status)) || "").toLowerCase();
        if (raw === "running" || raw === "waiting" || raw === "in_progress" || raw === "in progress") {
            return '<span class="shrink-0 rounded border border-blue-400/40 bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-200">In progress</span>';
        }
        if (raw === "done" || raw === "completed" || raw === "complete") {
            return '<span class="shrink-0 rounded border border-green-400/40 bg-green-500/10 px-1.5 py-0.5 text-[10px] text-green-200">Done</span>';
        }
        return "";
    }

    function workflowBoardTicketRowClasses(isLinked, isSelected, canDrag, blocked, pending) {
        var classes = "wf-board-ticket-row block w-full text-left px-3 py-2 border border-transparent transition-colors ";
        if (pending) {
            classes += "bg-amber-500/10 border-amber-500/50 cursor-wait opacity-80 ";
            return classes;
        }
        if (isLinked) {
            classes += "bg-green-500/10 border-green-500/50 cursor-default ";
            return classes;
        }
        if (blocked) {
            classes += "opacity-55 cursor-not-allowed ";
            return classes;
        }
        classes += canDrag ? "cursor-grab active:cursor-grabbing " : "cursor-default ";
        classes += isSelected
            ? "bg-blue-500/20 border-blue-500/50 "
            : "hover:bg-white/5 ";
        return classes;
    }

    function applyWorkflowBoardTicketSelectionStyles(list) {
        if (!list) return;
        list.querySelectorAll(".wf-board-ticket-row").forEach(function (row) {
            var selectedRow = row.dataset.ticketKey === selectedWorkflowBoardTicketKey;
            var linkedRow = row.dataset.linkedWorkflow === "true";
            [
                "bg-[#f97316]/15", "ring-[#f97316]/50",
                "bg-blue-500/20", "border-blue-500/50",
                "bg-green-500/10", "border-green-500/50",
                "hover:bg-white/5", "ring-1", "border-transparent"
            ].forEach(function (cls) { row.classList.remove(cls); });
            if (linkedRow) {
                row.classList.add("bg-green-500/10", "border-green-500/50");
            } else if (selectedRow) {
                row.classList.add("bg-blue-500/20", "border-blue-500/50");
            } else {
                row.classList.add("border-transparent", "hover:bg-white/5");
            }
        });
    }

    function ticketTextValue(value) {
        if (value == null) return "";
        if (typeof value === "string") return value;
        if (Array.isArray(value)) return value.map(ticketTextValue).filter(Boolean).join("\n");
        if (typeof value === "object") {
            if (value.text) return ticketTextValue(value.text);
            if (value.content) return ticketTextValue(value.content);
            try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
        }
        return String(value);
    }

    function ticketMetaRows(ticket, context) {
        var rows = [];
        if (context && context.selected) rows.push(["Board", context.selected.name + " (" + boardSourceLabel(context.selected.source) + ")"]);
        if (context && context.lane) rows.push(["Column", context.lane.name || ""]);
        if (ticket.id != null) rows.push(["ID", ticket.id]);
        if (ticket.priority) rows.push(["Priority", ticket.priority]);
        if (ticket.complexity) rows.push(["Complexity", ticket.complexity]);
        if (ticket.time_estimate) rows.push(["Estimate", ticket.time_estimate]);
        if (ticket.time_spent) rows.push(["Duration", ticket.time_spent]);
        if (ticket.workflow_status) rows.push(["Workflow", ticket.workflow_status]);
        if (ticket.external_source || ticket.source_provider) rows.push(["Source", ticket.external_source || ticket.source_provider]);
        if (ticket.external_url || ticket.source_url || ticket.url) rows.push(["URL", ticket.external_url || ticket.source_url || ticket.url]);
        return rows.filter(function (row) { return row[1] != null && String(row[1]).trim() !== ""; });
    }

    function projectNameById(projectId) {
        if (!projectId) return "";
        var match = (workflowLinkable.projects || []).filter(function (project) {
            return String(project.id) === String(projectId);
        })[0];
        return match ? (match.name || ("Project #" + projectId)) : ("Project #" + projectId);
    }

    function currentWorkflowBoardOption() {
        var select = document.getElementById("wf-board-select");
        var value = select ? select.value : "";
        return (workflowBoardOptions || []).filter(function (opt) { return opt.value === value; })[0] || null;
    }

    function workflowDatabaseBoardForProject(projectId) {
        projectId = projectId ? String(projectId) : "";
        if (!projectId) return null;
        return (workflowDatabaseBoards || []).filter(function (board) {
            return board && board.default_project_id && String(board.default_project_id) === projectId;
        })[0] || null;
    }

    function optionHtml(items, selectedId, labelKey) {
        var html = '<option value="">None</option>';
        (items || []).forEach(function (item) {
            var label = item[labelKey] || item.title || item.name || ("#" + item.id);
            html += '<option value="' + esc(String(item.id)) + '"' + (String(item.id) === String(selectedId || "") ? " selected" : "") + '>' + esc(label) + '</option>';
        });
        return html;
    }

    function lastItems(items, limit) {
        if (!Array.isArray(items)) return [];
        return items.slice(Math.max(0, items.length - limit));
    }

    function renderUiTasteControls(packet, run, screenshots) {
        run = run || {};
        screenshots = Array.isArray(screenshots) ? screenshots : [];
        var workflowId = run.workflow_id || currentWorkflowId || "";
        var runId = run.id || "";
        if (!workflowId || !runId) return "";
        var metadata = {
            ticket_id: run.ticket_id || null,
            board_id: run.board_id || null,
            project_id: run.project_id || null,
            execution_session_id: run.execution_session_id || null
        };
        var screenshotAttr = esc(screenshots.join("\n"));
        var metaAttr = esc(JSON.stringify(metadata));
        return '' +
            '<div class="mt-2 flex flex-wrap items-center gap-1.5" data-testid="wf-ui-taste-controls" data-screenshot-paths="' + screenshotAttr + '" data-ui-feedback-meta="' + metaAttr + '">' +
                '<span class="text-[11px] text-gray-500 mr-1">Taste</span>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-green-500/30 bg-green-500/10 text-[11px] text-green-200 hover:bg-green-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="approved">Approve</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-green-500/30 bg-green-500/10 text-[11px] text-green-200 hover:bg-green-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="approved" data-ui-save-baseline="true">Approve + baseline</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-[11px] text-red-200 hover:bg-red-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="spacing_off">Spacing</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-[11px] text-red-200 hover:bg-red-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="flow_bad">Flow</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-[11px] text-red-200 hover:bg-red-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="hierarchy_unclear">Hierarchy</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-[11px] text-red-200 hover:bg-red-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="inconsistent_styling">Style</button>' +
                '<button type="button" class="wf-ui-feedback-btn px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-[11px] text-red-200 hover:bg-red-500/20" data-workflow-id="' + esc(workflowId) + '" data-run-id="' + esc(runId) + '" data-ui-feedback-label="too_many_clicks">Clicks</button>' +
            '</div>';
    }

    function submitUiTasteFeedback(button) {
        if (!button) return;
        var workflowId = button.dataset.workflowId || currentWorkflowId;
        var runId = button.dataset.runId || "";
        var label = button.dataset.uiFeedbackLabel || "";
        if (!workflowId || !runId || !label) return;
        var wrap = button.closest ? button.closest("[data-testid='wf-ui-taste-controls']") : null;
        var screenshotPaths = wrap && wrap.dataset.screenshotPaths ? wrap.dataset.screenshotPaths.split("\n").filter(Boolean) : [];
        var metadata = {};
        if (wrap && wrap.dataset.uiFeedbackMeta) {
            try { metadata = JSON.parse(wrap.dataset.uiFeedbackMeta) || {}; } catch (e) { metadata = {}; }
        }
        var promptText = label === "approved" ? "Optional note for what worked:" : "What should change next time?";
        var reason = window.prompt ? window.prompt(promptText, "") : "";
        if (reason === null) return;
        var saveAsBaseline = button.dataset.uiSaveBaseline === "true";
        var visualBaselineName = "";
        var baselineScreenName = "";
        if (saveAsBaseline) {
            visualBaselineName = window.prompt ? window.prompt("Baseline name:", "Approved UI") : "Approved UI";
            if (visualBaselineName === null) return;
            baselineScreenName = window.prompt ? window.prompt("Baseline screen name:", "Run " + runId) : "Run " + runId;
            if (baselineScreenName === null) return;
        }
        button.disabled = true;
        api("POST", "/workflows/" + workflowId + "/runs/" + runId + "/ui-feedback", {
            label: label,
            reason: reason || "",
            ticket_id: metadata.ticket_id || null,
            board_id: metadata.board_id || null,
            project_id: metadata.project_id || null,
            execution_session_id: metadata.execution_session_id || null,
            screenshot_paths: screenshotPaths,
            save_as_visual_baseline: saveAsBaseline,
            visual_baseline_name: visualBaselineName || null,
            baseline_screen_name: baselineScreenName || null
        })
            .then(function (data) {
                snack(workflowFeedbackText(data, "UI feedback recorded"));
                loadHermesTimeline({ quiet: true });
                refreshBoardHermesViews({ quiet: true });
                if (saveAsBaseline && data.visual_baseline_readiness) {
                    loadVisualBaselines({ quiet: true });
                }
            })
            .catch(function (e) {
                snack(workflowErrorText(e, "Failed to record UI feedback"), "error");
            })
            .finally(function () { button.disabled = false; });
    }

    function renderCorrectionStatus(packet, run) {
        packet = packet || {};
        run = run || {};
        var execution = packet.execution || {};
        var snapshots = Array.isArray(execution.validation_snapshots) ? execution.validation_snapshots : [];
        var attempts = Array.isArray(run.correction_attempts) ? run.correction_attempts.slice() : [];
        var knownAttemptIds = {};
        attempts.forEach(function (attempt) {
            if (attempt && attempt.id != null) knownAttemptIds[String(attempt.id)] = true;
        });
        snapshots.forEach(function (snapshot) {
            if (!snapshot || !snapshot.correction_attempt_id) return;
            var id = String(snapshot.correction_attempt_id);
            if (knownAttemptIds[id]) return;
            attempts.push({
                id: snapshot.correction_attempt_id,
                validation_record_id: snapshot.record_id || snapshot.validation_record_id || null,
                status: "queued",
                attempt_number: null,
                dispatch_result: {},
                validation_type: snapshot.validation_type || ""
            });
            knownAttemptIds[id] = true;
        });
        attempts = attempts.filter(function (attempt) { return attempt && attempt.id != null; });
        if (!attempts.length) return "";
        var html = '<div class="mt-2 flex flex-wrap items-center gap-1.5" data-testid="wf-run-correction-status">';
        attempts.slice(-3).forEach(function (attempt) {
            var dispatch = attempt.dispatch_result || {};
            var autoDispatched = dispatch.auto_dispatch === true || dispatch.terminal_ui_quality_gate === true || attempt.status === "dispatched";
            var label = autoDispatched ? "Correction auto-dispatched" : "Correction queued";
            var cls = autoDispatched ? "border-blue-500/30 bg-blue-500/10 text-blue-200" : "border-amber-500/30 bg-amber-500/10 text-amber-200";
            html += '<span class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ' + cls + '">' +
                esc(label) + ' #' + esc(attempt.id) +
                (attempt.status ? ' <span class="text-gray-400">' + esc(attempt.status) + '</span>' : '') +
                '</span>';
        });
        html += '</div>';
        return html;
    }

    function renderRunPacketEvidence(packet, run) {
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
        var correctionStatus = renderCorrectionStatus(packet, run);
        var hasEvidence = packet.summary || audit.final_verdict || actionTrace.length || validations.length || screenshots.length || logs.length || patches.length || links.length || correctionStatus;
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
        html += correctionStatus;

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
        html += renderUiTasteControls(packet, run, screenshots);
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
        opts.danger = opts.danger !== false;
        if (window.DecisionsAPI && typeof window.DecisionsAPI.confirm === "function") {
            window.DecisionsAPI.confirm(opts);
            return;
        }
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

    function openWorkflowTicketModal(ticket, context) {
        ticket = ticket || {};
        context = context || {};
        var modal = document.getElementById("kb-ticket-modal");
        if (!modal) return;
        var title = ticket.title || ticket.name || "Untitled ticket";
        var description = ticketTextValue(ticket.description || ticket.desc || "");
        var rows = ticketMetaRows(ticket, context);
        var todos = Array.isArray(ticket.todos) ? ticket.todos : [];
        var links = Array.isArray(ticket.links) ? ticket.links : [];
        var files = Array.isArray(ticket.files) ? ticket.files : [];
        var media = Array.isArray(ticket.media) ? ticket.media : [];
        var isLocal = !!(ticket.id && (!context.selected || !context.selected.source || context.selected.source === "database"));
        workflowTicketModalState = {
            ticket: ticket,
            context: context,
            isLocal: isLocal,
            boardValue: context.selected && context.selected.value ? context.selected.value : ""
        };
        document.getElementById("kb-modal-title").textContent = title;
        document.getElementById("kb-modal-ticket-title").value = title;
        document.getElementById("kb-modal-ticket-desc").value = description;
        document.getElementById("kb-modal-ticket-estimate").value = ticket.time_estimate || "";
        document.getElementById("kb-modal-ticket-duration").value = ticket.time_spent || "";
        document.getElementById("kb-modal-ticket-complexity").value = ticket.complexity || "medium";
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function (btn) {
            var active = (btn.dataset.pri || "") === (ticket.priority || "medium");
            btn.classList.toggle("bg-[#f97316]", active);
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
        });
        ["kb-modal-ticket-title", "kb-modal-ticket-desc", "kb-modal-ticket-estimate", "kb-modal-ticket-duration", "kb-modal-ticket-complexity"].forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            el.disabled = !isLocal && id === "kb-modal-ticket-complexity";
            el.readOnly = !isLocal && id !== "kb-modal-ticket-complexity";
            el.classList.toggle("opacity-60", !isLocal);
        });
        document.getElementById("kb-modal-save").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-delete").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-upload-btn").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-add-link").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-add-todo").classList.toggle("hidden", !isLocal);
        var sourceBox = document.getElementById("kb-modal-source-meta");
        var sourceBody = document.getElementById("kb-modal-source-meta-body");
        if (sourceBox && sourceBody) {
            sourceBody.innerHTML = rows.map(function (row) { return '<div><span class="text-gray-500">' + esc(row[0]) + ':</span> ' + esc(String(row[1])) + '</div>'; }).join("");
            sourceBox.classList.toggle("hidden", !rows.length);
        }
        document.getElementById("kb-modal-links").innerHTML = links.length ? links.map(function (link) {
            return '<div class="flex items-center gap-2 text-xs rounded border border-white/10 bg-[#152054]/70 px-2 py-1.5">' +
                '<a class="text-blue-300 hover:text-blue-200 truncate flex-1" href="' + esc(link.url || "") + '" target="_blank" rel="noopener noreferrer">' + esc(link.title || link.url || "Link") + '</a>' +
                (isLocal && link.id ? '<button type="button" class="wf-ticket-delete-link text-red-400 hover:text-red-300 px-1" data-link-id="' + esc(link.id) + '" title="Delete link">&times;</button>' : "") +
                '</div>';
        }).join("") : '<p class="text-xs text-gray-500 italic">No links</p>';
        document.getElementById("kb-modal-files").innerHTML = files.concat(media).length ? files.concat(media).map(function (file) {
            var url = file.url || file.thumbnail || "";
            var label = esc(file.filename || file.name || url || "File");
            var link = url ? '<a class="text-blue-300 hover:text-blue-200 truncate flex-1" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>' : '<span class="text-gray-300 truncate flex-1">' + label + '</span>';
            return '<div class="flex items-center gap-2 text-xs rounded border border-white/10 bg-[#152054]/70 px-2 py-1.5">' + link +
                (isLocal && file.id ? '<button type="button" class="wf-ticket-delete-file text-red-400 hover:text-red-300 px-1" data-file-id="' + esc(file.id) + '" title="Delete file">&times;</button>' : "") +
                '</div>';
        }).join("") : '<p class="text-xs text-gray-500 italic">No attachments</p>';
        document.getElementById("kb-modal-todos").innerHTML = todos.length ? todos.map(function (todo) {
            return '<div class="flex items-center gap-2 text-xs rounded border border-white/10 bg-[#152054]/70 px-2 py-1.5">' +
                '<input type="checkbox" class="wf-ticket-toggle-todo accent-[#f97316]" data-todo-id="' + esc(todo.id || "") + '" ' + (todo.done ? "checked" : "") + (isLocal ? "" : " disabled") + '>' +
                '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + esc(todo.text || todo.name || "") + '</span>' +
                (isLocal && todo.id ? '<button type="button" class="wf-ticket-delete-todo text-red-400 hover:text-red-300 px-1" data-todo-id="' + esc(todo.id) + '" title="Delete to-do">&times;</button>' : "") +
                '</div>';
        }).join("") : '<p class="text-xs text-gray-500 italic">No tasks</p>';
        document.getElementById("kb-modal-audit-summary").innerHTML = '<div class="p-2 bg-[#152054] border border-white/10 rounded"><div class="text-[11px] text-gray-400">Report</div><div class="text-sm text-white">' + (ticket.audit_entries && ticket.audit_entries.length ? esc(String(ticket.audit_entries.length)) : "0") + '</div></div>';
        document.getElementById("kb-modal-audit-runs").innerHTML = '<div class="text-xs text-gray-500">No run report loaded here.</div>';
        document.getElementById("kb-modal-audit-entries").innerHTML = (ticket.audit_entries || []).map(function (entry) {
            return '<div class="p-2 bg-[#152054] border border-white/10 rounded text-xs text-gray-300">' + esc(entry.summary || entry.status || "Audit entry") + '</div>';
        }).join("") || '<div class="text-xs text-gray-500">No audit entries yet.</div>';
        var linkedWorkflow = document.getElementById("kb-modal-link-workflow");
        var linkedProject = document.getElementById("kb-modal-link-project");
        if (linkedWorkflow) linkedWorkflow.innerHTML = optionHtml(workflowLinkable.workflows || [], ticket.linked_workflow_id || (context.selected && context.selected.default_workflow_id), "title");
        if (linkedProject) linkedProject.innerHTML = optionHtml(workflowLinkable.projects || [], ticket.linked_project_id || (context.selected && context.selected.default_project_id), "name");
        bindWorkflowTicketModalDynamicControls();
        switchWorkflowTicketTab("details");
        modal.classList.remove("hidden");
    }

    function openWorkflowBoardTicket(key) {
        var item = workflowBoardTicketByKey[key];
        if (!item || !item.ticket) return;
        if (item.selected && item.selected.source === "database" && item.ticket.id != null) {
            api("GET", "/kanban/tickets/" + encodeURIComponent(item.ticket.id))
                .then(function (ticket) { openWorkflowTicketModal(ticket, item); })
                .catch(function () { openWorkflowTicketModal(item.ticket, item); });
            return;
        }
        openWorkflowTicketModal(item.ticket, item);
    }

    function switchWorkflowTicketTab(tab) {
        document.querySelectorAll(".kb-tm-tab").forEach(function (btn) {
            var active = btn.dataset.ttab === tab;
            btn.classList.toggle("active", active);
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
            btn.classList.toggle("border-[#f97316]", active);
            btn.classList.toggle("border-transparent", !active);
        });
        document.querySelectorAll(".kb-tm-pane").forEach(function (pane) { pane.classList.add("hidden"); });
        var target = document.getElementById("kb-tm-tab-" + tab);
        if (target) target.classList.remove("hidden");
    }

    function closeWorkflowTicketModal() {
        var modal = document.getElementById("kb-ticket-modal");
        if (modal) modal.classList.add("hidden");
        workflowTicketModalState = null;
    }

    function currentWorkflowModalTicketId() {
        return workflowTicketModalState && workflowTicketModalState.ticket ? workflowTicketModalState.ticket.id : null;
    }

    function reloadWorkflowTicketModal() {
        var ticketId = currentWorkflowModalTicketId();
        if (!ticketId) return Promise.resolve();
        return api("GET", "/kanban/tickets/" + encodeURIComponent(ticketId))
            .then(function (ticket) {
                openWorkflowTicketModal(ticket, workflowTicketModalState ? workflowTicketModalState.context : {});
                loadWorkflowTicketQueue();
                if (workflowTicketModalState && workflowTicketModalState.boardValue) {
                    loadWorkflowBoardTickets(workflowTicketModalState.boardValue);
                }
            });
    }

    function addWorkflowTicketLink() {
        var ticketId = currentWorkflowModalTicketId();
        if (!ticketId || !workflowTicketModalState || !workflowTicketModalState.isLocal) return;
        var titleEl = document.getElementById("kb-modal-link-title");
        var urlEl = document.getElementById("kb-modal-link-url");
        var title = titleEl ? titleEl.value.trim() : "";
        var url = urlEl ? urlEl.value.trim() : "";
        if (!title || !url) {
            snack("Title and URL required", "error");
            return;
        }
        api("POST", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/links", { title: title, url: url })
            .then(function () {
                if (titleEl) titleEl.value = "";
                if (urlEl) urlEl.value = "";
                snack("Link saved");
                return reloadWorkflowTicketModal();
            })
            .catch(function (e) { snack(e.message || "Failed to save link", "error"); });
    }

    function addWorkflowTicketTodo() {
        var ticketId = currentWorkflowModalTicketId();
        if (!ticketId || !workflowTicketModalState || !workflowTicketModalState.isLocal) return;
        var input = document.getElementById("kb-modal-todo-input");
        var text = input ? input.value.trim() : "";
        if (!text) return;
        api("POST", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/todos", { text: text })
            .then(function () {
                if (input) input.value = "";
                snack("To-do added");
                return reloadWorkflowTicketModal();
            })
            .catch(function (e) { snack(e.message || "Failed to add to-do", "error"); });
    }

    function uploadWorkflowTicketFiles(fileList) {
        var ticketId = currentWorkflowModalTicketId();
        if (!ticketId || !workflowTicketModalState || !workflowTicketModalState.isLocal || !fileList || !fileList.length) return;
        var uploads = [];
        Array.prototype.forEach.call(fileList, function (file) {
            var form = new FormData();
            form.append("file", file);
            uploads.push(fetch(API + "/kanban/tickets/" + encodeURIComponent(ticketId) + "/files", { method: "POST", body: form }).then(function (r) {
                if (!r.ok) throw new Error("Upload failed");
                return r.json();
            }));
        });
        Promise.all(uploads)
            .then(function () { snack("Files uploaded"); return reloadWorkflowTicketModal(); })
            .catch(function (e) { snack(e.message || "Upload failed", "error"); });
    }

    function bindWorkflowTicketModalDynamicControls() {
        var ticketId = currentWorkflowModalTicketId();
        if (!ticketId || !workflowTicketModalState || !workflowTicketModalState.isLocal) return;
        document.querySelectorAll(".wf-ticket-delete-link").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("DELETE", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/links/" + encodeURIComponent(btn.dataset.linkId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete link", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-delete-file").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("DELETE", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/files/" + encodeURIComponent(btn.dataset.fileId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete file", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-toggle-todo").forEach(function (input) {
            input.addEventListener("change", function () {
                api("PUT", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/todos/" + encodeURIComponent(input.dataset.todoId || ""), { done: input.checked })
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to update to-do", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-delete-todo").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("DELETE", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/todos/" + encodeURIComponent(btn.dataset.todoId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete to-do", "error"); });
            });
        });
    }

    function openWorkflowBoardEditModal() {
        var opt = currentWorkflowBoardOption();
        if (!opt) {
            snack("Select a board first", "error");
            return;
        }
        var existing = document.getElementById("wf-board-edit-modal");
        if (existing) existing.remove();
        var color = /^#[0-9a-f]{6}$/i.test(opt.color || "") ? opt.color : "#f97316";
        var html = '' +
            '<div id="wf-board-edit-modal" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60">' +
                '<div class="w-full max-w-xl mx-4 bg-[#1a1f3a] border border-white/20 rounded-xl shadow-2xl overflow-hidden">' +
                    '<div class="flex items-start justify-between gap-3 px-5 pt-5 pb-3">' +
                        '<div class="min-w-0 flex-1">' +
                            '<p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Edit board</p>' +
                            '<h3 class="min-w-0 text-white text-lg font-semibold truncate">' + esc(opt.name) + '</h3>' +
                        '</div>' +
                        '<div class="flex flex-col items-end gap-2 flex-shrink-0">' +
                            '<button type="button" class="wf-board-edit-close text-gray-400 hover:text-white text-xl leading-none">&times;</button>' +
                            '<span class="px-1.5 py-0.5 rounded border border-white/15 bg-white/5 text-[11px] text-gray-400">' + esc(boardSourceLabel(opt.source)) + '</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="flex gap-6 px-5 border-b border-white/10">' +
                        '<button type="button" class="wf-board-edit-tab pb-2 text-sm text-white border-b-2 border-[#f97316]" data-tab="details">Details</button>' +
                        '<button type="button" class="wf-board-edit-tab pb-2 text-sm text-gray-400 border-b-2 border-transparent hover:text-white" data-tab="advanced">Advanced</button>' +
                        (opt.source === "database" ? '<button type="button" class="wf-board-edit-tab pb-2 text-sm text-gray-400 border-b-2 border-transparent hover:text-white" data-tab="hermes">Routing</button>' : '') +
                    '</div>' +
                    '<div id="wf-board-edit-tab-details" class="wf-board-edit-pane p-5 space-y-3">' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Board name</label>' +
                            '<input type="text" id="wf-board-edit-name" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(opt.name) + '">' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Description</label>' +
                            '<input type="text" id="wf-board-edit-desc" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(opt.description || "") + '" placeholder="Optional description">' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Board colour</label>' +
                            '<div class="flex items-center gap-3">' +
                                '<input type="color" id="wf-board-edit-color" value="' + esc(color) + '" class="w-10 h-10 rounded border border-white/20 bg-transparent cursor-pointer" style="padding:2px;">' +
                                '<span id="wf-board-edit-color-hex" class="text-xs text-gray-400 font-mono">' + esc(color) + '</span>' +
                                '<button type="button" id="wf-board-edit-color-reset" class="text-xs text-gray-500 hover:text-white underline">Reset</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                    '<div id="wf-board-edit-tab-advanced" class="wf-board-edit-pane hidden p-5 space-y-3">' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Default project</label>' +
                            '<select id="wf-board-edit-project" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none">' +
                                optionHtml(workflowLinkable.projects || [], opt.default_project_id, "name") +
                            '</select>' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Default workflow</label>' +
                            '<select id="wf-board-edit-workflow" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none">' +
                                optionHtml(workflowLinkable.workflows || [], opt.default_workflow_id, "title") +
                            '</select>' +
                        '</div>' +
                    '</div>' +
                    '<div id="wf-board-edit-tab-hermes" class="wf-board-edit-pane hidden p-5 space-y-3">' +
                        '<p class="text-xs text-gray-400">Hybrid routing: policy baseline with optional LLM advisory override.</p>' +
                        '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">' +
                            '<label class="space-y-1"><span class="text-xs text-gray-500">Routing mode</span>' +
                                '<select id="wf-board-hermes-routing-mode" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
                                    '<option value="hybrid">Hybrid</option><option value="policy">Policy only</option><option value="llm">LLM advisory</option>' +
                                '</select></label>' +
                            '<label class="space-y-1"><span class="text-xs text-gray-500">Prefer IDE above complexity</span>' +
                                '<select id="wf-board-hermes-ide-threshold" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
                                    '<option value="">Off</option><option value="medium">Medium+</option><option value="high">High only</option>' +
                                '</select></label>' +
                        '</div>' +
                        '<label class="flex items-center gap-2 text-sm text-gray-300">' +
                            '<input type="checkbox" id="wf-board-hermes-require-approval" class="accent-[#f97316]">' +
                            '<span>Require approval before applying route overrides</span>' +
                        '</label>' +
                        '<div class="rounded border border-white/10 bg-[#152054]/40 p-3 space-y-2">' +
                            '<p class="text-xs text-gray-400 uppercase tracking-wide">Complexity routing overrides</p>' +
                            '<div class="grid grid-cols-1 md:grid-cols-3 gap-2">' +
                                ['low', 'medium', 'high'].map(function (level) {
                                    return '<label class="space-y-1"><span class="text-[11px] text-gray-500">' + level + '</span>' +
                                        '<input type="text" class="wf-board-route-backend w-full px-2 py-1.5 bg-[#0d1333] border border-white/20 rounded text-white text-xs" data-level="' + level + '" placeholder="backend / model">' +
                                    '</label>';
                                }).join("") +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="flex items-center justify-center gap-3 px-5 py-4 border-t border-white/10">' +
                        '<button type="button" class="wf-board-edit-close min-w-[72px] px-3 py-1.5 rounded border border-white/20 text-center text-gray-300 text-xs hover:bg-white/10">Cancel</button>' +
                        '<button type="button" id="wf-board-edit-save" class="min-w-[72px] px-3 py-1.5 rounded bg-[#f97316] text-center text-white text-xs font-medium hover:bg-[#ea580c]">Save</button>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        var modal = document.getElementById("wf-board-edit-modal");
        if (!modal) return;
        function closeModal() { modal.remove(); }
        modal.addEventListener("click", function (evt) { if (evt.target === modal) closeModal(); });
        modal.querySelectorAll(".wf-board-edit-close").forEach(function (btn) { btn.addEventListener("click", closeModal); });
        modal.querySelectorAll(".wf-board-edit-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var tab = btn.dataset.tab || "details";
                modal.querySelectorAll(".wf-board-edit-tab").forEach(function (item) {
                    var active = item.dataset.tab === tab;
                    item.classList.toggle("text-white", active);
                    item.classList.toggle("text-gray-400", !active);
                    item.style.borderColor = active ? "#f97316" : "transparent";
                });
                modal.querySelectorAll(".wf-board-edit-pane").forEach(function (pane) { pane.classList.add("hidden"); });
                var pane = document.getElementById("wf-board-edit-tab-" + tab);
                if (pane) pane.classList.remove("hidden");
            });
        });
        var colorInput = document.getElementById("wf-board-edit-color");
        var colorHex = document.getElementById("wf-board-edit-color-hex");
        if (colorInput && colorHex) {
            colorInput.addEventListener("input", function () { colorHex.textContent = colorInput.value; });
        }
        var colorReset = document.getElementById("wf-board-edit-color-reset");
        if (colorReset && colorInput && colorHex) {
            colorReset.addEventListener("click", function () {
                colorInput.value = "#f97316";
                colorHex.textContent = "#f97316";
            });
        }
        function populateHermesPolicy(policy) {
            policy = policy && typeof policy === "object" ? policy : {};
            var modeEl = document.getElementById("wf-board-hermes-routing-mode");
            var ideEl = document.getElementById("wf-board-hermes-ide-threshold");
            var approvalEl = document.getElementById("wf-board-hermes-require-approval");
            if (modeEl) modeEl.value = policy.routing_mode || "hybrid";
            if (ideEl) ideEl.value = policy.prefer_ide_above_complexity || "";
            if (approvalEl) approvalEl.checked = policy.require_approval_for_override !== false;
            var routes = policy.complexity_routing || {};
            document.querySelectorAll(".wf-board-route-backend").forEach(function (input) {
                var level = input.dataset.level || "";
                var route = routes[level] || {};
                var backend = route.backend || "";
                var model = route.model || "";
                input.value = backend ? (model && model !== "auto" ? backend + " / " + model : backend) : "";
            });
        }
        if (opt.source === "database") {
            api("GET", "/kanban/boards/" + encodeURIComponent(opt.id)).then(function (data) {
                populateHermesPolicy(data && data.hermes_policy);
            }).catch(function () { populateHermesPolicy({}); });
        }
        var saveBtn = document.getElementById("wf-board-edit-save");
        if (saveBtn) {
            saveBtn.addEventListener("click", function () {
                var name = (document.getElementById("wf-board-edit-name").value || "").trim() || opt.name;
                var description = (document.getElementById("wf-board-edit-desc").value || "").trim();
                var projectId = document.getElementById("wf-board-edit-project").value || null;
                var workflowId = document.getElementById("wf-board-edit-workflow").value || null;
                var nextColor = document.getElementById("wf-board-edit-color").value || "";
                var payload = {
                    name: name,
                    description: description,
                    default_project_id: projectId ? parseInt(projectId, 10) : null,
                    default_workflow_id: workflowId ? parseInt(workflowId, 10) : null,
                    color: nextColor,
                };
                if (opt.source === "database") {
                    var complexityRouting = {};
                    document.querySelectorAll(".wf-board-route-backend").forEach(function (input) {
                        var level = input.dataset.level || "";
                        var raw = (input.value || "").trim();
                        if (!level || !raw) return;
                        var parts = raw.split("/").map(function (part) { return part.trim(); });
                        complexityRouting[level] = {
                            backend: parts[0] || raw,
                            model: parts[1] || "auto",
                        };
                    });
                    payload.hermes_policy = {
                        routing_mode: (document.getElementById("wf-board-hermes-routing-mode") || {}).value || "hybrid",
                        require_approval_for_override: !!(document.getElementById("wf-board-hermes-require-approval") || {}).checked,
                        prefer_ide_above_complexity: (document.getElementById("wf-board-hermes-ide-threshold") || {}).value || "",
                        complexity_routing: complexityRouting,
                    };
                }
                saveBtn.disabled = true;
                var request = opt.source === "database"
                    ? api("PUT", "/kanban/boards/" + encodeURIComponent(opt.id), payload)
                    : api("POST", "/kanban/external-boards/" + encodeURIComponent(opt.source) + "/" + encodeURIComponent(opt.id) + "/register", payload);
                request.then(function () {
                    closeModal();
                    snack("Board updated");
                    loadWorkflowBoards();
                }).catch(function (e) {
                    saveBtn.disabled = false;
                    snack(e.message || "Failed to update board", "error");
                });
            });
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

    function loadWorkflowActionsCatalog() {
        return api("GET", "/workflows/actions/catalog")
            .then(function (rows) {
                workflowActionsCatalog = Array.isArray(rows) ? rows : [];
                workflowActionsCatalogLoaded = true;
                return workflowActionsCatalog;
            })
            .catch(function () {
                workflowActionsCatalog = [];
                workflowActionsCatalogLoaded = true;
                return workflowActionsCatalog;
            });
    }

    function actionModeLabel(action) {
        if (!action) return "";
        return action.mode === "instruction" ? "Instruction" : "Recording";
    }

    function renderActionOptions(selectedId) {
        var html = '<option value="">Select saved Action...</option>';
        workflowActionsCatalog.forEach(function (action) {
            var selected = String(selectedId || "") === String(action.id) ? " selected" : "";
            var disabled = action.usable ? "" : " disabled";
            var suffix = action.usable ? actionModeLabel(action) : (action.mode === "instruction" ? "Missing instruction" : "Missing recording");
            html += '<option value="' + action.id + '"' + selected + disabled + '>' + esc(action.title) + ' - ' + esc(suffix) + '</option>';
        });
        return html;
    }

    function hydrateActionSelect(container, step) {
        var select = container.querySelector(".sf-decision-action");
        if (!select) return;
        var selected = step.action_id || (step.config && (step.config.action_id || step.config.recording_id)) || "";
        select.innerHTML = workflowActionsCatalogLoaded ? renderActionOptions(selected) : '<option value="">Loading Actions...</option>';
        if (!workflowActionsCatalogLoaded) {
            loadWorkflowActionsCatalog().then(function () {
                if (!document.body.contains(select)) return;
                select.innerHTML = renderActionOptions(selected);
                select.value = selected ? String(selected) : "";
                updateDecisionActionSummary(container);
            });
        } else {
            select.value = selected ? String(selected) : "";
        }
        updateDecisionActionSummary(container);
    }

    function updateDecisionActionSummary(container) {
        var select = container.querySelector(".sf-decision-action");
        var summary = container.querySelector(".sf-decision-action-summary");
        if (!select || !summary) return;
        var action = workflowActionsCatalog.find(function (item) { return String(item.id) === String(select.value || ""); });
        if (!action) {
            summary.textContent = "Pick one saved action from Decisions. The workflow engine will run it as a step and audit the result.";
            summary.className = "sf-decision-action-summary text-xs text-gray-500 mt-2";
            return;
        }
        summary.textContent = actionModeLabel(action) + " action" + (action.usable ? "" : " is not runnable yet") + (action.description ? " - " + action.description : "");
        summary.className = "sf-decision-action-summary text-xs mt-2 " + (action.usable ? "text-emerald-300" : "text-amber-300");
    }

    function closeWorkflowContextMenu() {
        if (wfContextMenuEl) wfContextMenuEl.classList.add("hidden");
        wfContextMenuId = null;
    }

    function ensureWorkflowContextMenu() {
        if (wfContextMenuEl) return wfContextMenuEl;
        var html = '' +
            '<div id="wf-context-menu" class="hidden fixed z-[9999] min-w-[180px] bg-[#1a1f3a] border border-white/20 rounded-lg shadow-2xl py-1">' +
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
                    var state = workflowRuntimeStateById[w.id] || {};
                    var dotClass = "bg-gray-500";
                    if (state.status === "waiting") dotClass = "bg-yellow-400";
                    else if (state.status === "running") dotClass = "bg-blue-400";
                    return '<div class="flex items-center gap-2 rounded border px-3 py-2 cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#f97316]/60' + active + '" data-id="' + w.id + '" tabindex="0" role="option" aria-selected="' + (currentWorkflowId === w.id ? "true" : "false") + '">' +
                        '<span class="w-2 h-2 rounded-full ' + dotClass + ' flex-shrink-0"></span>' +
                        '<span class="text-sm text-white truncate">' + esc(w.name) + '</span>' +
                        '<span class="ml-auto inline-flex items-center gap-1.5">' +
                            '<span class="text-xs text-gray-500">' + (w.step_count || 0) + '</span>' +
                        '</span>' +
                        '</div>';
                }).join("");
                el.querySelectorAll("[data-id]").forEach(function (row) {
                    row.addEventListener("click", function () { selectWorkflow(parseInt(row.dataset.id, 10)); });
                    row.addEventListener("focus", function () { selectWorkflow(parseInt(row.dataset.id, 10)); });
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
        workflowQueueBoardFilter = "all";
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
            var nameStatus = document.getElementById("wf-name-save-status");
            if (nameStatus) nameStatus.textContent = "";
            renderRuns(data.runs || []);
            renderRunSettings(data);
            renderControlOverview(data);
            loadActiveRuns();
            loadWorkflowExecutionSessions();
            loadWorkflowTicketQueue();
            loadHermesTimeline({ quiet: true });
            renderContextRules(data);
            renderSkillChains(data);
            loadLearnedRules({ quiet: true });
            checkActiveRun();
        }).catch(function () { snack("Failed to load workflow", "error"); });
    }

    function normalizeWorkflowBoardOption(source, board) {
        if (!board || board.id == null) return null;
        source = source || board.source || "database";
        return {
            id: String(board.id),
            source: source,
            name: board.name || "Untitled Board",
            url: board.url || board.external_url || "",
            local_id: board.local_id || (source === "database" ? board.id : null),
            default_project_id: board.default_project_id || null,
            default_workflow_id: board.default_workflow_id || null,
            description: board.description || "",
            color: board.color || "",
            whatsapp_links: Array.isArray(board.whatsapp_links) ? board.whatsapp_links : [],
            value: source + ":" + String(board.id),
            label: (board.name || "Untitled Board") + " [" + boardSourceLabel(source) + "]"
        };
    }

    function renderWorkflowBoardSelect(options) {
        var select = document.getElementById("wf-board-select");
        if (!select) return;
        workflowBoardOptions = options || [];
        if (!workflowBoardOptions.length) {
            select.innerHTML = '<option value="">No boards available</option>';
            renderWorkflowBoardTickets(null, null, "No boards available.");
            return;
        }
        var groups = [
            { source: "database", label: "Local" },
            { source: "trello", label: "Trello" },
            { source: "jira", label: "Jira" }
        ];
        var html = "";
        groups.forEach(function (group) {
            var items = workflowBoardOptions.filter(function (opt) { return opt.source === group.source; });
            if (!items.length) return;
            html += '<optgroup label="' + esc(group.label) + '">';
            html += items.map(function (opt) {
                var projectName = projectNameById(opt.default_project_id);
                var label = opt.name + (projectName ? "  [" + projectName + "]" : "");
                return '<option value="' + esc(opt.value) + '">' + esc(label) + '</option>';
            }).join("");
            html += '</optgroup>';
        });
        select.innerHTML = html;
        var saved = "";
        try { saved = localStorage.getItem("wf_board_selected") || ""; } catch (e) {}
        var selected = workflowBoardOptions.some(function (opt) { return opt.value === saved; }) ? saved : workflowBoardOptions[0].value;
        select.value = selected;
        loadWorkflowBoardTickets(selected);
    }

    function renderWorkflowBoardSpinner(label) {
        var list = document.getElementById("wf-board-ticket-list");
        if (!list) return;
        var text = label ? '<span class="mt-3 text-xs text-gray-500">' + esc(label) + '</span>' : "";
        list.innerHTML = '' +
            '<div class="h-full min-h-[180px] flex flex-col items-center justify-center">' +
                '<span class="inline-block w-6 h-6 rounded-full border-2 border-white/15 border-t-[#f97316]" style="animation: wf-spin 0.75s linear infinite;"></span>' +
                text +
            '</div>';
    }

    function workflowBoardLocalId(selected) {
        if (!selected) return "";
        return selected.source === "database" ? selected.id : (selected.local_id || "");
    }

    function attachWorkflowBoardWhatsappLinks(data, selected) {
        var localBoardId = workflowBoardLocalId(selected);
        if (!localBoardId) return Promise.resolve(data || {});
        return api("GET", "/kanban/boards/" + encodeURIComponent(localBoardId) + "/whatsapp-links")
            .then(function (links) {
                var merged = data || {};
                merged.whatsapp_links = Array.isArray(links) ? links : [];
                return merged;
            })
            .catch(function () { return data || {}; });
    }

    function stopWorkflowWhatsappProgress(finalValue) {
        if (workflowWhatsappProgressTimer) {
            clearInterval(workflowWhatsappProgressTimer);
            workflowWhatsappProgressTimer = null;
        }
        if (typeof finalValue === "number") {
            workflowWhatsappProgressValue = finalValue;
            var bar = document.getElementById("wf-wa-ticket-loading-bar");
            if (bar) bar.style.width = Math.max(0, Math.min(100, finalValue)) + "%";
        }
    }

    function updateWorkflowWhatsappProgress(step, detail, value) {
        var titleEl = document.getElementById("wf-wa-ticket-loading-title");
        var detailEl = document.getElementById("wf-wa-ticket-loading-detail");
        var stepEl = document.getElementById("wf-wa-ticket-loading-step");
        var bar = document.getElementById("wf-wa-ticket-loading-bar");
        if (titleEl) titleEl.textContent = step || "Preparing WhatsApp Ticket...";
        if (detailEl && detail) detailEl.textContent = detail;
        if (stepEl) stepEl.textContent = step || "Working";
        if (typeof value === "number") workflowWhatsappProgressValue = value;
        if (bar) bar.style.width = Math.max(4, Math.min(96, workflowWhatsappProgressValue)) + "%";
    }

    function startWorkflowWhatsappProgress(step, detail, value) {
        stopWorkflowWhatsappProgress();
        workflowWhatsappProgressValue = typeof value === "number" ? value : 8;
        updateWorkflowWhatsappProgress(step, detail, workflowWhatsappProgressValue);
        workflowWhatsappProgressTimer = setInterval(function () {
            var cap = workflowWhatsappProgressValue < 40 ? 68 : 92;
            if (workflowWhatsappProgressValue < cap) {
                workflowWhatsappProgressValue += workflowWhatsappProgressValue < 40 ? 3 : 1;
                updateWorkflowWhatsappProgress(step, detail, workflowWhatsappProgressValue);
            }
        }, 900);
    }

    function setWorkflowWhatsappTicketLoading(isLoading, detail, step, progress) {
        var overlay = document.getElementById("wf-wa-ticket-loading");
        var saveBtn = document.getElementById("wf-wa-ticket-save");
        if (overlay) overlay.classList.toggle("hidden", !isLoading);
        if (isLoading) {
            if (!workflowWhatsappProgressTimer) startWorkflowWhatsappProgress(step || "Preparing WhatsApp Ticket...", detail || "Working through WhatsApp context.", progress);
            else updateWorkflowWhatsappProgress(step || "Preparing WhatsApp Ticket...", detail || "Working through WhatsApp context.", progress);
        } else {
            stopWorkflowWhatsappProgress(100);
        }
        if (saveBtn) {
            saveBtn.disabled = !!isLoading;
            saveBtn.classList.toggle("opacity-60", !!isLoading);
            saveBtn.classList.toggle("cursor-wait", !!isLoading);
        }
    }

    function closeWorkflowWhatsappTicketModal() {
        var modal = document.getElementById("wf-wa-ticket-modal");
        if (modal) modal.classList.add("hidden");
        workflowWhatsappTicketDraft = null;
        setWorkflowWhatsappLoadOlderVisible(false);
        setWorkflowWhatsappTicketLoading(false);
    }

    function workflowWhatsappQualityText(quality) {
        if (!quality || typeof quality.score === "undefined") return "";
        var bits = ["quality " + quality.score + "/100"];
        var issueCount = Array.isArray(quality.issues) ? quality.issues.length : 0;
        var warningCount = Array.isArray(quality.warnings) ? quality.warnings.length : 0;
        if (issueCount) bits.push(issueCount + " issue" + (issueCount === 1 ? "" : "s"));
        if (warningCount) bits.push(warningCount + " warning" + (warningCount === 1 ? "" : "s"));
        return bits.join(", ");
    }

    function whatsappMediaUrl(item) {
        return item && (item.preview_url || item.media_path || item.download_url || item.local_preview_url || item.url || "");
    }

    function workflowWhatsappMediaKind(item) {
        item = item || {};
        var type = String(item.media_type || "").toLowerCase();
        var mime = String(item.media_mime_type || "").toLowerCase();
        var name = String(item.media_filename || whatsappMediaUrl(item) || "").toLowerCase();
        if (type === "photo" || type === "image" || mime.indexOf("image/") === 0 || /\.(png|jpe?g|webp|gif|bmp|heic|heif)$/i.test(name)) return "image";
        if (type === "audio" || type === "voice" || type === "ptt" || mime.indexOf("audio/") === 0 || /\.(ogg|mp3|m4a|opus|wav|webm)$/i.test(name)) return "audio";
        if (type === "video" || mime.indexOf("video/") === 0 || /\.(mp4|webm|mov|m4v|3gp)$/i.test(name)) return "video";
        if (mime === "application/pdf" || /\.pdf$/i.test(name)) return "pdf";
        if (mime.indexOf("text/") === 0 || /\.(md|markdown|txt|csv|json|log|xml|yaml|yml)$/i.test(name)) return "text";
        return "file";
    }

    function workflowWhatsappMediaIcon(kind) {
        if (kind === "image") return "photo";
        if (kind === "audio") return "audio";
        if (kind === "video") return "video";
        if (kind === "pdf") return "PDF";
        if (kind === "text") return "TXT";
        return "file";
    }

    function closeWorkflowWhatsappMediaPreview() {
        var modal = document.getElementById("wf-wa-media-preview-modal");
        if (modal) modal.classList.add("hidden");
    }

    function openWorkflowWhatsappMediaPreview(item) {
        item = item || {};
        var modal = document.getElementById("wf-wa-media-preview-modal");
        var body = document.getElementById("wf-wa-media-preview-body");
        var title = document.getElementById("wf-wa-media-preview-title");
        var meta = document.getElementById("wf-wa-media-preview-meta");
        var url = whatsappMediaUrl(item);
        if (!modal || !body || !url) return;
        if (title) title.textContent = item.media_filename || "WhatsApp media";
        if (meta) meta.textContent = [item.sender || "", item.timestamp || "", item.media_type || ""].filter(Boolean).join(" / ");
        var kind = workflowWhatsappMediaKind(item);
        if (kind === "image") {
            body.innerHTML = '<img src="' + esc(url) + '" alt="' + esc(item.media_filename || "WhatsApp image") + '" class="max-w-full max-h-[70vh] object-contain rounded border border-white/10">';
        } else if (kind === "audio") {
            body.innerHTML = '<audio controls class="w-full" src="' + esc(url) + '"></audio>';
        } else if (kind === "video") {
            body.innerHTML = '<video controls class="max-w-full max-h-[70vh] rounded border border-white/10" src="' + esc(url) + '"></video>';
        } else if (kind === "pdf") {
            body.innerHTML = '<iframe src="' + esc(url) + '" class="w-full min-h-[70vh] rounded border border-white/10 bg-white"></iframe>';
        } else if (kind === "text") {
            body.innerHTML = '<div class="w-full rounded border border-white/10 bg-black/20 p-3 text-xs text-gray-400">Loading attachment...</div>';
            fetch(url)
                .then(function (resp) {
                    if (!resp.ok) throw new Error("Could not load attachment");
                    return resp.text();
                })
                .then(function (text) {
                    body.innerHTML = '<pre class="w-full max-h-[70vh] overflow-auto whitespace-pre-wrap rounded border border-white/10 bg-black/30 p-3 text-xs text-gray-200">' + esc(text) + '</pre>';
                })
                .catch(function () {
                    body.innerHTML = '<a class="text-blue-300 hover:text-blue-200 underline" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open attachment</a>';
                });
        } else {
            body.innerHTML = '<a class="text-blue-300 hover:text-blue-200 underline" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open attachment</a>';
        }
        modal.classList.remove("hidden");
    }

    function renderWorkflowWhatsappTicketMedia(media) {
        var container = document.getElementById("wf-wa-ticket-media");
        if (!container) return;
        media = Array.isArray(media) ? media : [];
        if (!media.length) {
            container.innerHTML = "";
            return;
        }
        var html = '<div class="text-sm text-gray-400">WhatsApp media that will be attached to the ticket</div>';
        media.forEach(function (item, idx) {
            var kind = workflowWhatsappMediaKind(item);
            var url = whatsappMediaUrl(item);
            var clickable = !!url;
            html += '<button type="button" class="wf-wa-media-preview-btn w-full flex items-center gap-2 p-2 bg-[#0a1030] rounded border border-white/10 text-left transition-colors" data-media-index="' + idx + '"' + (clickable ? "" : " disabled") + '>';
            if (kind === "image" && url) {
                html += '<img src="' + esc(url) + '" class="w-12 h-12 object-cover rounded border border-white/10 bg-black/20" alt="' + esc(item.media_filename || "WhatsApp image") + '" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{className:\'w-12 h-12 flex items-center justify-center bg-white/5 rounded text-[10px] text-gray-400 border border-white/10\',textContent:\'photo\'}))">';
            } else {
                html += '<div class="w-12 h-12 flex items-center justify-center bg-white/5 rounded border border-white/10 text-[10px] text-gray-400">' + esc(workflowWhatsappMediaIcon(kind)) + '</div>';
            }
            var sub = [item.media_type || kind || "media", item.media_mime_type || "", item.sender || "", item.timestamp || ""].filter(Boolean).join(" / ");
            html += '<div class="flex-1 min-w-0"><div class="text-sm text-white truncate">' + esc(item.media_filename || "WhatsApp media") + '</div><div class="text-xs text-gray-500 truncate">' + esc(sub) + '</div>';
            if (item.caption) html += '<div class="mt-1 text-xs text-gray-400 line-clamp-2">' + esc(item.caption) + '</div>';
            html += '</div>';
            html += clickable ? '<span class="text-xs text-[#f97316] flex-shrink-0">View</span>' : '<span class="text-xs text-gray-600 flex-shrink-0">Missing</span>';
            html += '</button>';
        });
        container.innerHTML = html;
        container.querySelectorAll(".wf-wa-media-preview-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = parseInt(btn.dataset.mediaIndex || "-1", 10);
                if (idx >= 0 && media[idx]) openWorkflowWhatsappMediaPreview(media[idx]);
            });
        });
    }

    function setWorkflowWhatsappLoadOlderVisible(show) {
        var btn = document.getElementById("wf-wa-ticket-load-older");
        if (btn) btn.classList.toggle("hidden", !show);
    }

    function workflowWhatsappEmptyStatusText(data) {
        var stats = data && data.intake_stats ? data.intake_stats : {};
        var reason = data && data.empty_reason ? data.empty_reason : "no_unticketed_messages";
        if (reason === "no_unticketed_in_recent_window") {
            var windowHours = stats.since_hours || 48;
            return "No unticketed WhatsApp messages in the last " + windowHours + " hours for this chat. "
                + stats.total_unticketed + " older unticketed message" + (stats.total_unticketed === 1 ? "" : "s")
                + " exist; use Load older unticketed messages if you need to ticket backlog.";
        }
        if (reason === "no_new_messages_since_last_ticket") {
            var parts = ["No new WhatsApp messages since the last ticket for this chat."];
            if (stats.total_ticketed) {
                parts.push(stats.total_ticketed + " message" + (stats.total_ticketed === 1 ? "" : "s") + " already linked to tickets.");
            }
            if (stats.total_unticketed) {
                parts.push(stats.total_unticketed + " older unticketed message" + (stats.total_unticketed === 1 ? "" : "s") + " remain outside the current window.");
            }
            parts.push("Send a new WhatsApp message, sync again, or load older unticketed messages.");
            return parts.join(" ");
        }
        if (stats.total_unticketed) {
            return "No WhatsApp messages matched the current intake window. " + stats.total_unticketed + " unticketed message" + (stats.total_unticketed === 1 ? "" : "s") + " exist for this chat; try loading older messages.";
        }
        return "No unticketed WhatsApp messages found for this board link. Messages may already be linked to tickets, or sync has not pulled new chat activity yet.";
    }

    function applyWorkflowWhatsappPreviewData(data, syncNote) {
        syncNote = syncNote || "";
        workflowWhatsappTicketDraft = {
            board_id: workflowWhatsappTicketDraft.board_id,
            link_id: workflowWhatsappTicketDraft.link_id,
            scope: workflowWhatsappTicketDraft.scope || "new_since_last_ticket",
            message_count: data.message_count || 0,
            message_ids: data.message_ids || []
        };
        var saveBtn = document.getElementById("wf-wa-ticket-save");
        if (data.empty) {
            document.getElementById("wf-wa-ticket-title").value = "";
            document.getElementById("wf-wa-ticket-desc").value = "";
            document.getElementById("wf-wa-ticket-priority").value = "medium";
            document.getElementById("wf-wa-ticket-complexity").value = "medium";
            document.getElementById("wf-wa-ticket-meta").textContent = (data.board_name || "Board") + " / " + (data.lane_name || "Backlog") + " / " + (data.contact_name || "WhatsApp");
            document.getElementById("wf-wa-ticket-status").textContent = workflowWhatsappEmptyStatusText(data) + syncNote;
            renderWorkflowWhatsappTicketMedia([]);
            setWorkflowWhatsappLoadOlderVisible(!!(data.intake_stats && data.intake_stats.older_unticketed_available));
            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.classList.add("opacity-60", "cursor-not-allowed");
            }
            setWorkflowWhatsappTicketLoading(false);
            return Promise.resolve();
        }
        setWorkflowWhatsappLoadOlderVisible(false);
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.classList.remove("opacity-60", "cursor-not-allowed");
        }
        document.getElementById("wf-wa-ticket-title").value = data.title || "WhatsApp ticket";
        document.getElementById("wf-wa-ticket-desc").value = data.description || "";
        document.getElementById("wf-wa-ticket-priority").value = data.priority || "medium";
        document.getElementById("wf-wa-ticket-complexity").value = data.complexity || "medium";
        document.getElementById("wf-wa-ticket-meta").textContent = (data.board_name || "Board") + " / " + (data.lane_name || "Backlog") + " / " + (data.contact_name || "WhatsApp");
        var previewQuality = workflowWhatsappQualityText(data.quality);
        document.getElementById("wf-wa-ticket-status").textContent = "Draft ready from " + (data.message_count || 0) + " unticketed WhatsApp message" + ((data.message_count || 0) === 1 ? "" : "s") + (previewQuality ? "; " + previewQuality : "") + "; improving from WhatsApp context...";
        renderWorkflowWhatsappTicketMedia(data.media || []);
        if (!workflowWhatsappTicketDraft.message_ids.length) {
            document.getElementById("wf-wa-ticket-status").textContent = "Draft updated; you can edit before creating." + (previewQuality ? " " + previewQuality + "." : "");
            setWorkflowWhatsappTicketLoading(false);
            return Promise.resolve();
        }
        setWorkflowWhatsappTicketLoading(true, "Composing a clean ticket from the messages, captions, attachments, and voice transcripts. This can take a little while.", "Composing ticket", 68);
        return api("POST", "/kanban/whatsapp/compose-ticket", {
            message_ids: workflowWhatsappTicketDraft.message_ids
        }).then(function (composed) {
            if (composed.title) document.getElementById("wf-wa-ticket-title").value = composed.title;
            if (composed.description) document.getElementById("wf-wa-ticket-desc").value = composed.description;
            if (composed.priority) document.getElementById("wf-wa-ticket-priority").value = composed.priority;
            if (composed.complexity) document.getElementById("wf-wa-ticket-complexity").value = composed.complexity;
            renderWorkflowWhatsappTicketMedia(composed.media || data.media || []);
            var composedQuality = workflowWhatsappQualityText(composed.quality);
            if (composed.fallback) {
                document.getElementById("wf-wa-ticket-status").textContent = "Ticket drafted locally. AI compose failed, but you can edit and create it." + (composedQuality ? " " + composedQuality + "." : "");
            } else {
                document.getElementById("wf-wa-ticket-status").textContent = "Draft updated; you can edit before creating." + (composedQuality ? " " + composedQuality + "." : "");
            }
        }).catch(function () {
            document.getElementById("wf-wa-ticket-status").textContent = "Using quick draft. AI compose request failed, but you can edit and create it.";
        }).finally(function () {
            setWorkflowWhatsappTicketLoading(false);
        });
    }

    function loadWorkflowWhatsappTicketPreview(scope) {
        if (!workflowWhatsappTicketDraft || !workflowWhatsappTicketDraft.board_id) return Promise.resolve();
        var boardId = workflowWhatsappTicketDraft.board_id;
        var linkId = workflowWhatsappTicketDraft.link_id || "";
        workflowWhatsappTicketDraft.scope = scope || "new_since_last_ticket";
        setWorkflowWhatsappTicketLoading(true, "Finding WhatsApp messages for this board.", "Collecting messages", 12);
        return api("POST", "/kanban/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-preview", {
            link_id: linkId ? parseInt(linkId, 10) : null,
            limit: 500,
            scope: workflowWhatsappTicketDraft.scope
        }).then(function (data) {
            setWorkflowWhatsappTicketLoading(true, "Reading messages, attaching media, and transcribing voice notes where needed.", "Preparing media", 42);
            return applyWorkflowWhatsappPreviewData(data, "");
        });
    }

    function openWorkflowWhatsappTicketModal(btn) {
        if (!btn || btn.disabled) return;
        var boardId = btn.dataset.boardId || "";
        var linkId = btn.dataset.linkId || "";
        if (!boardId) {
            snack("This board needs a local board link before WhatsApp intake can create tickets", "error");
            return;
        }
        var modal = document.getElementById("wf-wa-ticket-modal");
        if (!modal) {
            snack("WhatsApp ticket modal not found", "error");
            return;
        }
        workflowWhatsappTicketDraft = { board_id: boardId, link_id: linkId, scope: "new_since_last_ticket" };
        document.getElementById("wf-wa-ticket-title").value = "WhatsApp ticket";
        document.getElementById("wf-wa-ticket-desc").value = "";
        document.getElementById("wf-wa-ticket-priority").value = "medium";
        document.getElementById("wf-wa-ticket-complexity").value = "medium";
        document.getElementById("wf-wa-ticket-meta").textContent = "Board WhatsApp intake";
        document.getElementById("wf-wa-ticket-status").textContent = "Draft ready; loading WhatsApp context...";
        renderWorkflowWhatsappTicketMedia([]);
        setWorkflowWhatsappLoadOlderVisible(false);
        var saveBtn = document.getElementById("wf-wa-ticket-save");
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.classList.remove("opacity-60", "cursor-not-allowed");
        }
        modal.classList.remove("hidden");
        setWorkflowWhatsappTicketLoading(true, "Syncing WhatsApp messages from relay, then loading board context.", "Syncing WhatsApp", 4);
        btn.disabled = true;
        btn.classList.add("opacity-60", "cursor-wait");
        var syncNote = "";
        api("POST", "/kanban/whatsapp/sync", {}).then(function (syncResult) {
            if (syncResult && syncResult.error) {
                syncNote = " Sync skipped: " + syncResult.error + ". Using locally stored messages.";
            } else if (syncResult && typeof syncResult.synced === "number") {
                syncNote = " Synced " + syncResult.synced + " message(s) from relay.";
            }
        }).catch(function () {
            syncNote = " Sync unavailable; using locally stored messages.";
        }).finally(function () {
            setWorkflowWhatsappTicketLoading(true, "Finding new WhatsApp messages for this board." + syncNote, "Collecting messages", 12);
            return api("POST", "/kanban/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-preview", {
                link_id: linkId ? parseInt(linkId, 10) : null,
                limit: 500,
                scope: "new_since_last_ticket"
            });
        }).then(function (data) {
            setWorkflowWhatsappTicketLoading(true, "Reading messages, attaching media, and transcribing voice notes where needed.", "Preparing media", 42);
            return applyWorkflowWhatsappPreviewData(data, syncNote);
        }).catch(function (e) {
            document.getElementById("wf-wa-ticket-status").textContent = e.message || "Could not load WhatsApp messages";
            setWorkflowWhatsappTicketLoading(false);
            snack(e.message || "Could not load WhatsApp messages", "error");
        }).finally(function () {
            btn.disabled = false;
            btn.classList.remove("opacity-60", "cursor-wait");
        });
    }

    function submitWorkflowWhatsappTicketModal() {
        if (!workflowWhatsappTicketDraft || !workflowWhatsappTicketDraft.board_id) return;
        if (!workflowWhatsappTicketDraft.message_ids || !workflowWhatsappTicketDraft.message_ids.length) {
            snack("No WhatsApp messages are selected for this ticket yet", "error");
            return;
        }
        var title = (document.getElementById("wf-wa-ticket-title").value || "").trim();
        if (!title) {
            snack("Title is required", "error");
            return;
        }
        setWorkflowWhatsappTicketLoading(true, "Saving the ticket, attaching media, and recording the WhatsApp audit trail.", "Creating ticket", 72);
        api("POST", "/kanban/boards/" + encodeURIComponent(workflowWhatsappTicketDraft.board_id) + "/whatsapp-snapshot-ticket", {
            link_id: workflowWhatsappTicketDraft.link_id ? parseInt(workflowWhatsappTicketDraft.link_id, 10) : null,
            limit: 500,
            message_ids: workflowWhatsappTicketDraft.message_ids || [],
            title: title,
            description: (document.getElementById("wf-wa-ticket-desc").value || "").trim(),
            priority: document.getElementById("wf-wa-ticket-priority").value || "medium",
            complexity: document.getElementById("wf-wa-ticket-complexity").value || "medium"
        }).then(function (data) {
            var count = data && data.message_count ? data.message_count : workflowWhatsappTicketDraft.message_count || 0;
            snack("Created WhatsApp ticket" + (count ? " from " + count + " messages" : ""));
            closeWorkflowWhatsappTicketModal();
            var select = document.getElementById("wf-board-select");
            if (select && select.value) loadWorkflowBoardTickets(select.value);
            loadWorkflowTicketQueue();
            renderWorkflowCliTab();
        }).catch(function (e) {
            var quality = e.workflowDetail && e.workflowDetail.quality ? e.workflowDetail.quality : null;
            var qualityProblems = quality ? (quality.issues || []).concat(quality.warnings || []) : [];
            var message = e.message || "Could not create WhatsApp ticket";
            if (qualityProblems.length) message += ": " + qualityProblems.slice(0, 2).join("; ");
            var status = document.getElementById("wf-wa-ticket-status");
            if (status) status.textContent = message;
            snack(message, "error");
        }).finally(function () {
            setWorkflowWhatsappTicketLoading(false);
        });
    }

    function loadWorkflowBoards() {
        var select = document.getElementById("wf-board-select");
        if (select) select.innerHTML = '<option value="">Loading boards...</option>';
        renderWorkflowBoardSpinner("");

        Promise.all([
            api("GET", "/kanban/boards").catch(function () { return []; }),
            api("GET", "/kanban/external-boards").catch(function () { return { trello: [], jira: [] }; }),
            api("GET", "/kanban/linkable").catch(function () { return { projects: [], workflows: [] }; })
        ]).then(function (results) {
            var localBoards = Array.isArray(results[0]) ? results[0] : [];
            var external = results[1] || {};
            workflowLinkable = results[2] || { projects: [], workflows: [] };
            workflowDatabaseBoards = localBoards.filter(function (b) { return (b.source || "database") === "database"; });
            var options = [];
            var seen = {};

            workflowDatabaseBoards.forEach(function (b) {
                var opt = normalizeWorkflowBoardOption("database", b);
                if (opt) { options.push(opt); seen[opt.value] = true; }
            });
            (external.trello || []).forEach(function (b) {
                var opt = normalizeWorkflowBoardOption("trello", b);
                if (opt && !seen[opt.value]) { options.push(opt); seen[opt.value] = true; }
            });
            (external.jira || []).forEach(function (b) {
                var opt = normalizeWorkflowBoardOption("jira", b);
                if (opt && !seen[opt.value]) { options.push(opt); seen[opt.value] = true; }
            });

            localBoards.filter(function (b) {
                var source = (b.source || "").toLowerCase();
                return source === "trello" || source === "jira";
            }).forEach(function (b) {
                var opt = normalizeWorkflowBoardOption(b.source, {
                    id: b.external_board_id || b.id,
                    name: b.name,
                    url: b.external_url,
                    local_id: b.id,
                    default_project_id: b.default_project_id,
                    default_workflow_id: b.default_workflow_id,
                    color: b.color,
                    whatsapp_links: b.whatsapp_links || []
                });
                if (opt && !seen[opt.value]) { options.push(opt); seen[opt.value] = true; }
            });

            renderWorkflowBoardSelect(options);
        }).catch(function () {
            if (select) select.innerHTML = '<option value="">Failed to load boards</option>';
            renderWorkflowBoardTickets(null, null, "Failed to load boards.");
        });
    }

    function loadWorkflowBoardTickets(value, attempt) {
        var selected = workflowBoardOptions.filter(function (opt) { return opt.value === value; })[0];
        var list = document.getElementById("wf-board-ticket-list");
        if (!selected || !list) return;
        attempt = attempt || 0;
        workflowBoardLoadToken += 1;
        var token = workflowBoardLoadToken;
        try { localStorage.setItem("wf_board_selected", value); } catch (e) {}
        renderWorkflowBoardSpinner("Loading tickets...");

        var path = selected.source === "database"
            ? "/kanban/boards/" + encodeURIComponent(selected.id)
            : "/kanban/external-boards/" + encodeURIComponent(selected.source) + "/" + encodeURIComponent(selected.id);

        api("GET", path).then(function (data) {
            if (token !== workflowBoardLoadToken) return;
            if (selected.source !== "database" && data && data.cache_ready === false && attempt < 60) {
                renderWorkflowBoardTickets(data, selected, "Syncing board tickets...");
                setTimeout(function () {
                    if (token === workflowBoardLoadToken) loadWorkflowBoardTickets(value, attempt + 1);
                }, 800);
                return;
            }
            attachWorkflowBoardWhatsappLinks(data, selected).then(function (merged) {
                if (token !== workflowBoardLoadToken) return;
                renderWorkflowBoardTickets(merged, selected);
            });
        }).catch(function (e) {
            if (token !== workflowBoardLoadToken) return;
            renderWorkflowBoardTickets(null, selected, e.message || "Failed to load tickets.");
        });
    }

    function renderWorkflowBoardTickets(board, selected, message) {
        var list = document.getElementById("wf-board-ticket-list");
        if (!list) return;
        if (message && !board) {
            list.innerHTML = '<p class="text-sm text-gray-500">' + esc(message) + '</p>';
            return;
        }
        var lanes = board && Array.isArray(board.lanes) ? board.lanes : [];
        if (!lanes.length) {
            list.innerHTML = '<p class="text-sm text-gray-500">' + esc(message || "No columns or tickets found for this board.") + '</p>';
            return;
        }
        var boardName = (board && board.name) || (selected && selected.name) || "Board";
        var boardHasProject = !!(selected && selected.default_project_id);
        var boardWhatsappLinks = (board && Array.isArray(board.whatsapp_links) ? board.whatsapp_links : [])
            .concat(selected && Array.isArray(selected.whatsapp_links) ? selected.whatsapp_links : []);
        var boardWhatsappLink = boardWhatsappLinks.filter(function (link) { return link && link.id; })[0] || null;
        var localBoardId = workflowBoardLocalId(selected);
        var firstLanePosition = lanes.length ? lanes[0].position : 0;
        var html = '<div class="text-xs text-gray-500 mb-1 truncate">' + esc(boardName) + '</div>';
        workflowBoardTicketByKey = {};
        if (message) {
            html += '<div class="text-xs text-amber-300 mb-2">' + esc(message) + '</div>';
        }
        if (!boardHasProject) {
            html += '<div class="mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200">Link this board to a project before dragging tickets into a workflow.</div>';
        }
        lanes.forEach(function (lane) {
            var tickets = Array.isArray(lane.tickets) ? lane.tickets : [];
            var collapseKey = workflowBoardCollapseKey(selected, lane);
            var collapsed = !!workflowBoardCollapsedColumns[collapseKey];
            var laneName = String(lane.name || "").toLowerCase();
            var isWhatsappIntakeColumn = !!(boardWhatsappLink && localBoardId && (laneName === "backlog" || laneName.indexOf("backlog") >= 0 || lane.position === firstLanePosition));
            html += '<div class="wf-board-column rounded border border-white/10 bg-[#10183f] overflow-hidden" data-collapse-key="' + esc(collapseKey) + '">';
            html += '<div class="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2">';
            html += '<button type="button" class="wf-board-column-toggle min-w-0 flex flex-1 items-center gap-2 text-left hover:text-white" data-collapse-key="' + esc(collapseKey) + '">';
            html += '<span class="chevron' + (collapsed ? "" : " open") + ' text-gray-500">›</span><span class="text-xs font-medium text-gray-200 truncate">' + esc(lane.name || "Column") + '</span>';
            html += '</button>';
            html += '<div class="flex items-center gap-1.5 flex-shrink-0">';
            if (isWhatsappIntakeColumn) {
                html += '<button type="button" class="wf-board-whatsapp-snapshot inline-flex h-6 w-6 items-center justify-center rounded border border-[#25D366]/40 bg-[#25D366]/10 text-[#25D366] hover:bg-[#25D366]/20" title="Create ticket from linked WhatsApp messages" aria-label="Create ticket from linked WhatsApp messages" data-board-id="' + esc(localBoardId) + '" data-link-id="' + esc(boardWhatsappLink.id) + '">' + WHATSAPP_ICON_SVG + '</button>';
            }
            html += '<span class="text-[11px] text-gray-500">' + tickets.length + '</span>';
            html += '</div>';
            html += '</div>';
            html += '<div class="wf-board-column-body' + (collapsed ? " hidden" : "") + '">';
            if (!tickets.length) {
                html += '<div class="px-3 py-2 text-xs text-gray-500">No tickets</div>';
            } else {
                html += '<div class="divide-y divide-white/5">';
                tickets.forEach(function (ticket) {
                    var title = ticket.title || ticket.name || "Untitled ticket";
                    var meta = ticket.priority || ticket.id || "";
                    var ticketKey = workflowTicketKey(selected, lane, ticket);
                    var isSelected = selectedWorkflowBoardTicketKey === ticketKey;
                    var badge = workflowStatusBadge(ticket);
                    var externalLinkKey = workflowTicketExternalLinkKey(selected, ticket);
                    var localLinkKey = ticket.id ? ("database:" + String(ticket.id)) : "";
                    var isExternallyLinked = !!(externalLinkKey && workflowLinkedExternalTicketKeys[externalLinkKey]);
                    var isPendingLink = !!((externalLinkKey && workflowPendingTicketLinks[externalLinkKey]) || (localLinkKey && workflowPendingTicketLinks[localLinkKey]));
                    var isLinkedToWorkflow = !!ticket.linked_workflow_id || isExternallyLinked;
                    var blockedByMissingProject = !isLinkedToWorkflow && !isPendingLink && !boardHasProject;
                    var canDragToWorkflow = !!(ticket.id && !isLinkedToWorkflow && !isPendingLink && boardHasProject);
                    var linkedLabel = isLinkedToWorkflow ? '<span class="text-[10px] px-1.5 py-0.5 rounded border border-green-500/50 bg-green-500/10 text-green-300 flex-shrink-0">linked</span>' : "";
                    var pendingLabel = isPendingLink ? '<span class="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/50 bg-amber-500/20 text-amber-200 flex-shrink-0">linking</span>' : "";
                    var blockedLabel = blockedByMissingProject ? '<span class="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/50 bg-amber-500/20 text-amber-200 flex-shrink-0">no project</span>' : "";
                    workflowBoardTicketByKey[ticketKey] = { ticket: ticket, lane: lane, selected: selected, board: board };
                    html += '<button type="button" class="' + workflowBoardTicketRowClasses(isLinkedToWorkflow, isSelected, canDragToWorkflow, blockedByMissingProject, isPendingLink) + '" data-ticket-key="' + esc(ticketKey) + '" data-linked-workflow="' + (isLinkedToWorkflow ? "true" : "false") + '" draggable="' + (canDragToWorkflow ? "true" : "false") + '">';
                    html += '<div class="flex items-start justify-between gap-2">';
                    html += '<div class="min-w-0 text-xs text-gray-200 leading-snug break-words">' + esc(title) + '</div>';
                    html += badge || linkedLabel || pendingLabel || blockedLabel;
                    html += '</div>';
                    if (meta) html += '<div class="text-[11px] text-gray-500 mt-0.5">' + esc(meta) + '</div>';
                    html += '</button>';
                });
                html += '</div>';
            }
            html += '</div>';
            html += '</div>';
        });
        list.innerHTML = html;
        list.querySelectorAll(".wf-board-column-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var key = btn.dataset.collapseKey || "";
                if (!key) return;
                workflowBoardCollapsedColumns[key] = !workflowBoardCollapsedColumns[key];
                saveWorkflowBoardCollapsedColumns();
                var col = btn.closest(".wf-board-column");
                var body = col ? col.querySelector(".wf-board-column-body") : null;
                var chev = btn.querySelector(".chevron");
                var isCollapsed = !!workflowBoardCollapsedColumns[key];
                if (body) body.classList.toggle("hidden", isCollapsed);
                if (chev) chev.classList.toggle("open", !isCollapsed);
            });
        });
        list.querySelectorAll(".wf-board-whatsapp-snapshot").forEach(function (btn) {
            btn.addEventListener("click", function (evt) {
                evt.preventDefault();
                evt.stopPropagation();
                openWorkflowWhatsappTicketModal(btn);
            });
        });
        list.querySelectorAll(".wf-board-ticket-row").forEach(function (btn) {
            btn.addEventListener("click", function () {
                selectedWorkflowBoardTicketKey = btn.dataset.ticketKey || "";
                applyWorkflowBoardTicketSelectionStyles(list);
            });
            btn.addEventListener("dblclick", function () {
                openWorkflowBoardTicket(btn.dataset.ticketKey || "");
            });
            btn.addEventListener("dragstart", function (evt) {
                var item = workflowBoardTicketByKey[btn.dataset.ticketKey || ""];
                if (!item || !item.ticket || item.ticket.linked_workflow_id) {
                    evt.preventDefault();
                    return;
                }
                if (!item.selected || !item.selected.default_project_id) {
                    evt.preventDefault();
                    snack("Link this board to a project before adding tickets to a workflow", "error");
                    return;
                }
                var isExternal = item.selected.source !== "database";
                var destinationBoard = isExternal ? workflowDatabaseBoardForProject(item.selected.default_project_id) : null;
                var payload = {
                    type: "workflow-board-ticket",
                    ticket_key: btn.dataset.ticketKey || "",
                    ticket_id: item.ticket.id,
                    title: item.ticket.title || item.ticket.name || "Untitled ticket",
                    description: ticketTextValue(item.ticket.description || item.ticket.desc || ""),
                    priority: item.ticket.priority || "medium",
                    complexity: item.ticket.complexity || "",
                    source: item.selected.source,
                    external_source: isExternal ? item.selected.source : "",
                    external_id: isExternal ? String(item.ticket.id || item.ticket.key || item.ticket.external_id || "") : "",
                    external_url: item.ticket.url || item.ticket.external_url || "",
                    board_id: item.selected.source === "database" ? item.selected.id : item.selected.local_id,
                    local_board_id: item.selected.local_id,
                    destination_board_id: destinationBoard ? destinationBoard.id : null,
                    default_project_id: item.selected.default_project_id || null
                };
                workflowBoardDragPayload = payload;
                workflowLastDragPoint = null;
                evt.dataTransfer.effectAllowed = "copyMove";
                evt.dataTransfer.setData("application/json", JSON.stringify(payload));
                evt.dataTransfer.setData("text/plain", String(item.ticket.id));
            });
            btn.addEventListener("dragend", function () {
                if (workflowBoardDragPayload && workflowLastDragPoint && workflowDropZoneContainsPoint(workflowLastDragPoint)) {
                    handleWorkflowTicketDropPayload(workflowBoardDragPayload);
                }
                setTimeout(function () {
                    workflowBoardDragPayload = null;
                    workflowLastDragPoint = null;
                }, 250);
            });
        });
        loadBoardActivity({ quiet: true });
    }

    function getSelectedBoardLocalId() {
        var select = document.getElementById("wf-board-select");
        if (!select || !select.value) return "";
        var selected = workflowBoardOptions.filter(function (opt) { return opt.value === select.value; })[0];
        return workflowBoardLocalId(selected);
    }

    function learnedRuleTypeLabel(ruleType) {
        var value = String(ruleType || "rule").replace(/_/g, " ");
        return value.charAt(0).toUpperCase() + value.slice(1);
    }

    function renderBoardActivity(data) {
        var list = document.getElementById("wf-board-activity-list");
        var empty = document.getElementById("wf-board-activity-empty");
        if (!list || !empty) return;
        data = data || {};
        var events = Array.isArray(data.events) ? data.events : [];
        var rules = Array.isArray(data.learned_rules) ? data.learned_rules : [];
        latestBoardActivity = data;
        if (!events.length && !rules.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "No board activity yet. Run a ticket to populate activity.";
            return;
        }
        empty.classList.add("hidden");
        var html = "";
        if (rules.length) {
            html += '<div class="mb-2 rounded border border-sky-500/20 bg-sky-500/5 px-2 py-1.5 text-[11px] text-sky-200">' +
                esc(String(rules.length)) + " learned rule" + (rules.length === 1 ? "" : "s") + " on this board" +
                "</div>";
        }
        html += events.slice(0, 10).map(function (event) {
            var when = event.created_at ? new Date(event.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
            return '' +
                '<div class="rounded border border-white/10 bg-[#10183f]/80 px-2 py-1.5">' +
                    '<div class="flex items-start gap-2">' +
                        '<span class="mt-0.5 text-[9px] uppercase tracking-wide px-1 py-0.5 rounded border ' + hermesStatusClass(event.status || "event") + '">' + esc(event.status || "event") + '</span>' +
                        '<div class="min-w-0 flex-1">' +
                            '<p class="text-[11px] text-gray-200 truncate">' + esc(event.summary || event.event_type || "Event") + '</p>' +
                            '<p class="text-[10px] text-gray-500 truncate">' + esc(event.event_type || "") + (event.ticket_id ? " · ticket #" + event.ticket_id : "") + '</p>' +
                        '</div>' +
                        '<span class="text-[10px] text-gray-500 flex-shrink-0">' + esc(when) + '</span>' +
                    '</div>' +
                '</div>';
        }).join("");
        list.innerHTML = html;
    }

    function loadBoardActivity(options) {
        options = options || {};
        var boardId = getSelectedBoardLocalId();
        if (!boardId) {
            renderBoardActivity(null);
            var empty = document.getElementById("wf-board-activity-empty");
            if (empty) {
                empty.classList.remove("hidden");
                empty.textContent = "Select a board to view activity.";
            }
            return Promise.resolve(null);
        }
        return api("GET", "/kanban/boards/" + encodeURIComponent(boardId) + "/activity?event_limit=12&rule_limit=5")
            .then(function (data) {
                renderBoardActivity(data);
                return data;
            })
            .catch(function (e) {
                renderBoardActivity(null);
                if (!options.quiet) snack(e.message || "Failed to load board activity", "error");
                return null;
            });
    }

    function renderLearnedRules(rules, boardId) {
        var list = document.getElementById("wf-learned-rules-list");
        var empty = document.getElementById("wf-learned-rules-empty");
        var note = document.getElementById("wf-learned-rules-note");
        if (!list || !empty) return;
        rules = Array.isArray(rules) ? rules : [];
        latestLearnedRules = rules;
        if (!boardId) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "Select a board to view learned rules.";
            if (note) note.textContent = "Captured from validation failures and IDE iteration on the selected board.";
            return;
        }
        if (!rules.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "No learned rules yet for this board.";
            if (note) note.textContent = "Rules are captured when validation fails or IDE iteration reports back.";
            return;
        }
        empty.classList.add("hidden");
        if (note) note.textContent = "These rules are injected into validation context for tickets on board #" + boardId + ".";
        list.innerHTML = rules.map(function (rule) {
            var enabled = rule.enabled !== false;
            var evidence = parseInt(rule.evidence_count, 10) || 0;
            var confidence = Math.round((parseFloat(rule.confidence) || 0) * 100);
            return '' +
                '<div class="rounded border border-white/10 bg-[#10183f]/70 px-3 py-2" data-learned-rule-id="' + esc(rule.id) + '">' +
                    '<div class="flex items-start gap-3">' +
                        '<label class="mt-1 flex items-center gap-2 flex-shrink-0">' +
                            '<input type="checkbox" class="wf-learned-rule-toggle accent-[#f97316]" data-rule-id="' + esc(rule.id) + '"' + (enabled ? " checked" : "") + '>' +
                            '<span class="text-[10px] uppercase tracking-wide text-gray-500">' + esc(learnedRuleTypeLabel(rule.rule_type)) + '</span>' +
                        '</label>' +
                        '<div class="min-w-0 flex-1">' +
                            '<p class="text-sm text-gray-200">' + esc(rule.summary || "") + '</p>' +
                            '<p class="mt-1 text-[11px] text-gray-500">Evidence: ' + esc(String(evidence)) + (confidence ? " · confidence " + confidence + "%" : "") + '</p>' +
                        '</div>' +
                        (boardId ? '<button type="button" class="wf-learned-rule-promote flex-shrink-0 px-2 py-1 rounded border border-white/20 text-[11px] text-gray-300 hover:bg-white/10" data-rule-id="' + esc(rule.id) + '">Promote hint</button>' : '') +
                    '</div>' +
                '</div>';
        }).join("");
        list.querySelectorAll(".wf-learned-rule-toggle").forEach(function (input) {
            input.addEventListener("change", function () {
                var ruleId = input.dataset.ruleId;
                if (!ruleId || !boardId) return;
                api("PATCH", "/kanban/boards/" + encodeURIComponent(boardId) + "/learned-rules/" + encodeURIComponent(ruleId), {
                    enabled: !!input.checked
                }).then(function () {
                    snack(input.checked ? "Learned rule enabled" : "Learned rule disabled", "success");
                    loadBoardActivity({ quiet: true });
                }).catch(function (e) {
                    input.checked = !input.checked;
                    snack(e.message || "Failed to update learned rule", "error");
                });
            });
        });
        list.querySelectorAll(".wf-learned-rule-promote").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var ruleId = btn.dataset.ruleId;
                if (!ruleId || !boardId) return;
                btn.disabled = true;
                api("POST", "/kanban/boards/" + encodeURIComponent(boardId) + "/learned-rules/" + encodeURIComponent(ruleId) + "/promote", {
                    category: "general"
                }).then(function () {
                    snack("Learned rule promoted to board hints", "success");
                    refreshBoardHermesViews({ quiet: true });
                }).catch(function (e) {
                    snack(e.message || "Failed to promote learned rule", "error");
                }).finally(function () { btn.disabled = false; });
            });
        });
    }

    function loadLearnedRules(options) {
        options = options || {};
        var boardId = getSelectedBoardLocalId();
        if (!boardId) {
            renderLearnedRules([], "");
            return Promise.resolve([]);
        }
        return api("GET", "/kanban/boards/" + encodeURIComponent(boardId) + "/activity?event_limit=1&rule_limit=30")
            .then(function (data) {
                var rules = data && Array.isArray(data.learned_rules) ? data.learned_rules : [];
                renderLearnedRules(rules, boardId);
                return rules;
            })
            .catch(function (e) {
                renderLearnedRules([], boardId);
                if (!options.quiet) snack(e.message || "Failed to load learned rules", "error");
                return [];
            });
    }

    function renderVisualBaselineReadiness(readiness, boardId) {
        var note = document.getElementById("wf-visual-baselines-note");
        if (!note) return;
        if (!boardId) {
            note.textContent = "Reference screens used by UI validation for the selected board.";
            note.className = "text-xs text-gray-500";
            return;
        }
        readiness = readiness || {};
        var baselineCount = Number(readiness.baseline_count || 0);
        var missingCount = Number(readiness.missing_screen_count || 0);
        if (!baselineCount) {
            note.textContent = "No visual baselines yet. Save real screenshot paths before relying on baseline checks.";
            note.className = "text-xs text-amber-300";
        } else if (readiness.ready) {
            note.textContent = "Visual baselines ready: all referenced screenshot files exist.";
            note.className = "text-xs text-emerald-300";
        } else {
            note.textContent = "Visual baselines not ready: " + missingCount + " referenced screenshot file" + (missingCount === 1 ? " is" : "s are") + " missing.";
            note.className = "text-xs text-amber-300";
        }
    }

    function renderVisualBaselines(baselines, boardId, readiness) {
        var list = document.getElementById("wf-visual-baselines-list");
        var empty = document.getElementById("wf-visual-baselines-empty");
        var note = document.getElementById("wf-visual-baselines-note");
        if (!list || !empty) return;
        baselines = Array.isArray(baselines) ? baselines : [];
        readiness = readiness || {};
        var missingByPath = {};
        (Array.isArray(readiness.missing) ? readiness.missing : []).forEach(function (item) {
            if (item && item.screenshot_path) missingByPath[item.screenshot_path] = true;
        });
        if (!boardId) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "Select a board to view visual baselines.";
            renderVisualBaselineReadiness(null, "");
            return;
        }
        if (!baselines.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "No visual baselines yet for this board.";
            renderVisualBaselineReadiness(readiness, boardId);
            return;
        }
        empty.classList.add("hidden");
        renderVisualBaselineReadiness(readiness, boardId);
        list.innerHTML = baselines.map(function (baseline) {
            var screens = Array.isArray(baseline.screens) ? baseline.screens : [];
            var missingScreens = 0;
            var screenText = screens.map(function (screen) {
                var missing = screen.screenshot_path && missingByPath[screen.screenshot_path];
                if (missing) missingScreens += 1;
                var screenStatus = missing ? "missing" : "ready";
                var screenLabel = missing ? "Missing reference file" : "Ready for comparison";
                var screenClass = missing
                    ? "border-amber-400/30 bg-amber-500/10 text-amber-200"
                    : "border-emerald-400/30 bg-emerald-500/10 text-emerald-200";
                return '<span data-visual-baseline-screen-status="' + esc(screenStatus) + '">' +
                    esc(screen.screen_name || "Screen") +
                    (screen.screenshot_path ? ' <span class="text-gray-600">·</span> <span class="font-mono break-all">' + esc(screen.screenshot_path) + '</span>' : "") +
                    ' <span class="ml-1 rounded border ' + screenClass + ' px-1.5 py-0.5 text-[10px] uppercase tracking-wide">' + screenLabel + '</span>' +
                    '</span>';
            }).join("<br>");
            var baselineStatus = screens.length && !missingScreens ? "ready" : "not_ready";
            var baselineLabel = baselineStatus === "ready" ? "Ready" : "Needs reference files";
            var baselineClass = baselineStatus === "ready"
                ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-200"
                : "border-amber-400/30 bg-amber-500/10 text-amber-200";
            return '' +
                '<div class="rounded border border-white/10 bg-[#10183f]/70 px-3 py-2" data-visual-baseline-id="' + esc(baseline.id) + '" data-visual-baseline-status="' + esc(baselineStatus) + '">' +
                    '<div class="flex items-start justify-between gap-3">' +
                        '<div class="min-w-0 flex-1">' +
                            '<p class="text-sm font-medium text-gray-100">' + esc(baseline.name || "Visual baseline") + '</p>' +
                            '<p class="mt-1 text-[11px] text-gray-500">' + esc(String(screens.length)) + ' reference screen' + (screens.length === 1 ? "" : "s") + (baseline.version ? " · " + esc(baseline.version) : "") + '</p>' +
                            (screenText ? '<p class="mt-1 text-[11px] text-gray-400">' + screenText + '</p>' : '') +
                        '</div>' +
                        '<div class="flex flex-col items-end gap-1 flex-shrink-0">' +
                            '<span class="rounded border ' + baselineClass + ' px-1.5 py-0.5 text-[10px] uppercase tracking-wide">' + baselineLabel + '</span>' +
                            '<span class="text-[10px] uppercase tracking-wide text-gray-500">' + esc(baseline.scope || "global") + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>';
        }).join("");
    }

    function loadVisualBaselines(options) {
        options = options || {};
        var boardId = getSelectedBoardLocalId();
        if (!boardId) {
            renderVisualBaselines([], "");
            return Promise.resolve([]);
        }
        return api("GET", "/workflows/visual-baselines?board_id=" + encodeURIComponent(boardId))
            .then(function (data) {
                var baselines = data && Array.isArray(data.visual_baselines) ? data.visual_baselines : [];
                return api("GET", "/workflows/visual-baselines/readiness?board_id=" + encodeURIComponent(boardId))
                    .then(function (readinessData) {
                        var readiness = readinessData && readinessData.visual_baseline_readiness ? readinessData.visual_baseline_readiness : {};
                        renderVisualBaselines(baselines, boardId, readiness);
                        return baselines;
                    })
                    .catch(function () {
                        renderVisualBaselines(baselines, boardId, {});
                        return baselines;
                    });
            })
            .catch(function (e) {
                renderVisualBaselines([], boardId);
                if (!options.quiet) snack(e.message || "Failed to load visual baselines", "error");
                return [];
            });
    }

    function scheduledActionWhenText(schedule) {
        schedule = schedule || {};
        if (schedule.kind === "once") return "once at " + (schedule.run_at || "?");
        if (schedule.kind === "weekdays") return "weekdays at " + (schedule.time || "?");
        if (schedule.kind === "daily") return "daily at " + (schedule.time || "?");
        return "weekly at " + (schedule.time || "?");
    }

    function scheduledActionSummary(action) {
        action = action || {};
        if (action.type === "open_app") return "Open " + (action.app_name || "app");
        if (action.type === "type_text") return "Type text" + (action.press_enter === false ? "" : " and press Enter");
        if (action.type === "play_recording") return "Play recording " + (action.recording_name || "");
        if (action.type === "keypress") return "Press " + (action.key || "key");
        return action.type || "Desktop action";
    }

    function renderScheduledActions(actions) {
        var list = document.getElementById("wf-scheduled-actions-list");
        var empty = document.getElementById("wf-scheduled-actions-empty");
        var note = document.getElementById("wf-scheduled-actions-note");
        if (!list || !empty) return;
        actions = Array.isArray(actions) ? actions : [];
        if (note) note.textContent = actions.length ? "Queued one-time and recurring desktop actions." : "No scheduled desktop actions are queued.";
        if (!actions.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            empty.textContent = "No scheduled desktop actions queued.";
            return;
        }
        empty.classList.add("hidden");
        list.innerHTML = actions.map(function (item) {
            var title = item.title || "Scheduled action";
            var enabled = item.enabled !== false;
            var runLog = Array.isArray(item.run_log) && item.run_log.length ? item.run_log[0] : null;
            var schedule = item.schedule || {};
            var scheduleKind = schedule.kind === "weekdays" || schedule.kind === "weekly" || schedule.kind === "daily" ? schedule.kind : "daily";
            var scheduleTime = schedule.time || "09:00";
            return '' +
                '<div class="rounded border border-white/10 bg-[#10183f]/70 px-3 py-2" data-scheduled-action-title="' + esc(title) + '">' +
                    '<div class="flex items-start justify-between gap-3">' +
                        '<div class="min-w-0 flex-1">' +
                            '<p class="text-sm font-medium text-gray-100">' + esc(title) + '</p>' +
                            '<p class="mt-1 text-[11px] text-gray-500">' + esc(scheduledActionSummary(item.action)) + ' · ' + esc(scheduledActionWhenText(item.schedule)) + (item.next_run_at ? ' · next ' + esc(item.next_run_at) : '') + '</p>' +
                            (runLog ? '<p class="mt-1 text-[11px] text-gray-400">Last run: ' + esc(runLog.status || "") + (runLog.result ? ' · ' + esc(runLog.result) : '') + '</p>' : '') +
                            '<div class="mt-2 flex flex-wrap items-center gap-2">' +
                                '<select class="wf-scheduled-action-kind rounded border border-white/20 bg-[#0d1333] px-2 py-1 text-[11px] text-gray-200" data-scheduled-action-title="' + esc(title) + '">' +
                                    '<option value="daily"' + (scheduleKind === "daily" ? " selected" : "") + '>Daily</option>' +
                                    '<option value="weekdays"' + (scheduleKind === "weekdays" ? " selected" : "") + '>Weekdays</option>' +
                                    '<option value="weekly"' + (scheduleKind === "weekly" ? " selected" : "") + '>Weekly</option>' +
                                '</select>' +
                                '<input type="time" class="wf-scheduled-action-time rounded border border-white/20 bg-[#0d1333] px-2 py-1 text-[11px] text-gray-200" data-decisions-datetime-label="Schedule time" data-scheduled-action-title="' + esc(title) + '" value="' + esc(scheduleTime) + '">' +
                                '<button type="button" class="wf-scheduled-action-reschedule px-2 py-1 rounded border border-white/20 text-gray-300 text-[11px] hover:bg-white/10" data-scheduled-action-title="' + esc(title) + '">Reschedule</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="flex flex-shrink-0 items-center gap-2">' +
                            '<span class="text-[10px] uppercase tracking-wide ' + (enabled ? 'text-emerald-300' : 'text-gray-500') + '">' + (enabled ? 'active' : 'disabled') + '</span>' +
                            '<button type="button" class="wf-scheduled-action-toggle px-2 py-1 rounded border border-white/20 text-gray-300 text-[11px] hover:bg-white/10" data-scheduled-action-title="' + esc(title) + '" data-scheduled-action-enabled="' + (enabled ? "true" : "false") + '">' + (enabled ? "Disable" : "Enable") + '</button>' +
                            '<button type="button" class="wf-scheduled-action-cancel px-2 py-1 rounded border border-red-500/40 text-red-200 text-[11px] hover:bg-red-500/15" data-scheduled-action-title="' + esc(title) + '">Cancel</button>' +
                        '</div>' +
                    '</div>' +
                '</div>';
        }).join("");
    }

    function loadScheduledActions(options) {
        options = options || {};
        return api("GET", "/workflows/scheduled-actions")
            .then(function (data) {
                var actions = data && Array.isArray(data.scheduled_actions) ? data.scheduled_actions : [];
                renderScheduledActions(actions);
                return actions;
            })
            .catch(function (e) {
                renderScheduledActions([]);
                if (!options.quiet) snack(e.message || "Failed to load scheduled actions", "error");
                return [];
            });
    }

    function disableScheduledActionByTitle(title, disable) {
        title = (title || "").trim();
        if (!title) return Promise.resolve();
        return api("PATCH", "/workflows/scheduled-actions/by-title?title=" + encodeURIComponent(title), {
            enabled: !disable
        }).then(function () {
            snack(disable ? "Scheduled action disabled" : "Scheduled action enabled", "success");
            return loadScheduledActions({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to update scheduled action", "error");
        });
    }

    function rescheduleScheduledActionByTitle(title, kind, time) {
        title = (title || "").trim();
        kind = (kind || "daily").trim();
        time = (time || "").trim();
        if (!title || !time) {
            snack("Choose a time before rescheduling", "error");
            return Promise.resolve();
        }
        return api("PATCH", "/workflows/scheduled-actions/by-title?title=" + encodeURIComponent(title), {
            enabled: true,
            schedule: {
                kind: kind,
                time: time
            }
        }).then(function () {
            snack("Scheduled action rescheduled", "success");
            return loadScheduledActions({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to reschedule scheduled action", "error");
        });
    }

    function cancelScheduledActionByTitle(title) {
        title = (title || "").trim();
        if (!title) return Promise.resolve();
        return api("DELETE", "/workflows/scheduled-actions/by-title?title=" + encodeURIComponent(title))
            .then(function () {
                snack("Scheduled action cancelled", "success");
                return loadScheduledActions({ quiet: true });
            })
            .catch(function (e) {
                snack(e.message || "Failed to cancel scheduled action", "error");
            });
    }

    function createVisualBaseline() {
        var boardId = getSelectedBoardLocalId();
        if (!boardId) {
            snack("Select a board before saving a visual baseline", "error");
            return;
        }
        var nameEl = document.getElementById("wf-baseline-name");
        var screenEl = document.getElementById("wf-baseline-screen-name");
        var pathEl = document.getElementById("wf-baseline-screenshot-path");
        var notesEl = document.getElementById("wf-baseline-notes");
        var name = (nameEl && nameEl.value || "").trim();
        var screenName = (screenEl && screenEl.value || "").trim();
        var screenshotPath = (pathEl && pathEl.value || "").trim();
        var notes = (notesEl && notesEl.value || "").trim();
        if (!name || !screenName || !screenshotPath) {
            snack("Baseline name, screen, and screenshot path are required", "error");
            return;
        }
        var btn = document.getElementById("wf-save-visual-baseline");
        if (btn) btn.disabled = true;
        api("POST", "/workflows/visual-baselines", {
            name: name,
            board_id: parseInt(boardId, 10),
            store_copy: true,
            screens: [{
                screen_name: screenName,
                screenshot_path: screenshotPath,
                notes: notes
            }]
        }).then(function () {
            snack("Visual baseline saved", "success");
            if (screenEl) screenEl.value = "";
            if (pathEl) pathEl.value = "";
            if (notesEl) notesEl.value = "";
            loadVisualBaselines({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to save visual baseline", "error");
        }).finally(function () {
            if (btn) btn.disabled = false;
        });
    }

    function refreshBoardHermesViews(options) {
        options = options || {};
        loadBoardActivity(options);
        loadLearnedRules(options);
        loadVisualBaselines(options);
        loadScheduledActions(options);
    }

    // Soft refresh — only update step statuses/results without rebuilding DOM
    function softRefresh() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId).then(function (data) {
            currentWorkflow = data;
            var steps = data.steps || [];
            var preserveOpenEditor = isStepEditorInteractionActive();
            if (!preserveOpenEditor) {
                renderSteps(steps);
            }
            renderRuns(data.runs || []);
            renderControlOverview(data);
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
                if (!preserveOpenEditor && expandedStepId === s.id && (s.status === "passed" || s.status === "failed" || s.status === "cancelled")) {
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
            loadWorkflowExecutionSessions();
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

    function executionSessionIsActive(session) {
        var status = String((session && session.status) || "").toLowerCase();
        return status === "queued" || status === "running";
    }

    function executionSessionStatusClass(status) {
        status = String(status || "").toLowerCase();
        if (status === "completed") return "bg-green-500/15 text-green-300";
        if (status === "failed") return "bg-red-500/15 text-red-300";
        if (status === "running") return "bg-blue-500/15 text-blue-300";
        if (status === "queued") return "bg-amber-500/15 text-amber-200";
        return "bg-white/10 text-gray-300";
    }

    function executionSessionByTicketId(ticketId) {
        var id = String(ticketId || "");
        return (latestWorkflowExecutionSessions || []).filter(function (session) {
            return String(session.ticket_id || "") === id;
        })[0] || null;
    }

    function activeExecutionSessionByTicketId(ticketId) {
        var id = String(ticketId || "");
        return (latestWorkflowExecutionSessions || []).filter(function (session) {
            return String(session.ticket_id || "") === id && executionSessionIsActive(session);
        })[0] || null;
    }

    function executionSessionOutputText(session) {
        var packet = session && session.output_packet;
        if (!packet || typeof packet !== "object") return "";
        return packet.output || packet.error || packet.summary || "";
    }

    function executionSessionInstructionPreview(session) {
        return (session && (session.instruction || (session.input_packet && session.input_packet.instruction))) || "";
    }

    function executionSessionRuntimeSnapshot(session) {
        var inputPacket = session && session.input_packet;
        if (inputPacket && typeof inputPacket === "object" && inputPacket.runtime_snapshot) {
            return inputPacket.runtime_snapshot;
        }
        var outputPacket = session && session.output_packet;
        if (outputPacket && typeof outputPacket === "object" && outputPacket.runtime_snapshot) {
            return outputPacket.runtime_snapshot;
        }
        return null;
    }

    function renderRuntimeSnapshot(snapshot) {
        if (!snapshot || typeof snapshot !== "object") return "";
        var sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : [];
        var durable = Array.isArray(snapshot.durable_sessions) ? snapshot.durable_sessions : [];
        var urls = Array.isArray(snapshot.urls) ? snapshot.urls : [];
        var activeCount = snapshot.active_terminal_count != null ? snapshot.active_terminal_count : sessions.length;
        var urlChips = urls.length ? urls.slice(0, 4).map(function (item) {
            var url = item && item.url ? String(item.url) : "";
            if (!url) return "";
            return '<span class="rounded bg-green-500/10 border border-green-500/30 px-1.5 py-0.5 text-[11px] text-green-200 font-mono">' + esc(url) + '</span>';
        }).join("") : '<span class="text-[11px] text-gray-500">No app URL detected yet</span>';
        var rows = (sessions.length ? sessions : durable).slice(0, 4).map(function (runtime) {
            var status = runtime.status || (runtime.alive ? "running" : "unknown");
            var command = runtime.command || "";
            var pid = runtime.pid ? ("PID " + runtime.pid) : "No PID";
            var cwd = runtime.cwd || "";
            return '<div class="rounded border border-white/10 bg-black/15 px-2 py-1.5">' +
                '<div class="flex items-center gap-2 min-w-0">' +
                    '<span class="rounded bg-blue-500/15 px-1.5 py-0.5 text-[10px] text-blue-200">' + esc(status) + '</span>' +
                    '<span class="text-[11px] text-gray-400">' + esc(pid) + '</span>' +
                    '<span class="text-[11px] text-gray-500 truncate">' + esc(runtime.purpose || "runtime") + '</span>' +
                '</div>' +
                (command ? '<div class="mt-1 text-[11px] text-gray-300 font-mono truncate">' + esc(command) + '</div>' : '') +
                (cwd ? '<div class="mt-0.5 text-[10px] text-gray-600 font-mono truncate">' + esc(cwd) + '</div>' : '') +
            '</div>';
        }).join("");
        return '<div class="rounded border border-green-500/20 bg-green-500/5 p-3">' +
            '<div class="flex items-center justify-between gap-3 mb-2">' +
                '<p class="text-[11px] uppercase tracking-wide text-green-200">Project runtime</p>' +
                '<span class="text-[11px] text-gray-400">' + esc(activeCount) + ' active terminal' + (activeCount === 1 ? "" : "s") + '</span>' +
            '</div>' +
            '<div class="flex flex-wrap gap-1.5 mb-2">' + urlChips + '</div>' +
            (rows ? '<div class="space-y-1.5">' + rows + '</div>' : '<div class="text-xs text-gray-500">No Decisions-owned project terminal was running when this step started.</div>') +
            '<p class="mt-2 text-[11px] text-gray-500">' + esc(snapshot.safe_restart_policy || "The workflow engine reuses active project runtimes and only restarts Decisions-owned terminals.") + '</p>' +
        '</div>';
    }

    function startExecutionSessionPolling() {
        if (executionSessionPollTimer) return;
        executionSessionPollTimer = setInterval(function () {
            loadWorkflowExecutionSessions({ quiet: true });
        }, 3000);
    }

    function stopExecutionSessionPolling() {
        if (executionSessionPollTimer) {
            clearInterval(executionSessionPollTimer);
            executionSessionPollTimer = null;
        }
    }

    function syncExecutionSessionPolling() {
        if ((latestWorkflowExecutionSessions || []).some(executionSessionIsActive)) {
            startExecutionSessionPolling();
        } else {
            stopExecutionSessionPolling();
        }
    }

    function runSourceText(r) {
        if (!r) return "Manual";
        if (r.source_label) return r.source_label;
        var source = String(r.source_type || "").replace(/_/g, " ").trim();
        return source ? source.charAt(0).toUpperCase() + source.slice(1) : "Manual";
    }

    function runMetaText(r, fallbackWorkflowName) {
        r = r || {};
        return {
            boardText: r.board_name || (r.board_id ? ("Board #" + r.board_id) : "No board"),
            ticketText: r.ticket_title || (r.ticket_id ? ("Ticket #" + r.ticket_id) : "No ticket"),
            projectText: r.project_name || (r.project_id ? ("Project #" + r.project_id) : "No project"),
            sourceText: runSourceText(r),
            workflowText: r.workflow_name || fallbackWorkflowName || (r.workflow_id ? ("Workflow #" + r.workflow_id) : "")
        };
    }

    function workflowHasActiveRuns() {
        return (latestActiveRuns || []).some(function (r) {
            return currentWorkflowId && r && String(r.workflow_id) === String(currentWorkflowId);
        });
    }

    function normalizedRunSettings(data) {
        var raw = data && data.run_settings;
        var settings = raw && typeof raw === "object" ? raw : {};
        return {
            execution_mode: settings.execution_mode === "parallel" ? "parallel" : DEFAULT_RUN_SETTINGS.execution_mode,
            concurrency_scope: settings.concurrency_scope === "workflow" ? "workflow" : DEFAULT_RUN_SETTINGS.concurrency_scope,
            max_parallel_tickets: Math.max(1, Math.min(12, parseInt(settings.max_parallel_tickets || DEFAULT_RUN_SETTINGS.max_parallel_tickets, 10) || DEFAULT_RUN_SETTINGS.max_parallel_tickets)),
            branch_per_ticket: settings.branch_per_ticket !== false,
            max_correction_attempts: Math.max(0, Math.min(5, parseInt(settings.max_correction_attempts || DEFAULT_RUN_SETTINGS.max_correction_attempts, 10) || DEFAULT_RUN_SETTINGS.max_correction_attempts)),
            auto_dispatch_corrections: settings.auto_dispatch_corrections === true
        };
    }

    function collectRunSettings() {
        return {
            execution_mode: (document.getElementById("wf-run-execution-mode") || {}).value || DEFAULT_RUN_SETTINGS.execution_mode,
            concurrency_scope: (document.getElementById("wf-run-concurrency-scope") || {}).value || DEFAULT_RUN_SETTINGS.concurrency_scope,
            max_parallel_tickets: Math.max(1, Math.min(12, parseInt((document.getElementById("wf-run-max-parallel") || {}).value || DEFAULT_RUN_SETTINGS.max_parallel_tickets, 10) || DEFAULT_RUN_SETTINGS.max_parallel_tickets)),
            branch_per_ticket: !!((document.getElementById("wf-run-branch-per-ticket") || {}).checked),
            max_correction_attempts: Math.max(0, Math.min(5, parseInt((document.getElementById("wf-run-max-corrections") || {}).value || DEFAULT_RUN_SETTINGS.max_correction_attempts, 10) || DEFAULT_RUN_SETTINGS.max_correction_attempts)),
            auto_dispatch_corrections: !!((document.getElementById("wf-run-auto-corrections") || {}).checked)
        };
    }

    function renderRunSettings(data) {
        var settings = normalizedRunSettings(data || currentWorkflow || {});
        var modeEl = document.getElementById("wf-run-execution-mode");
        var scopeEl = document.getElementById("wf-run-concurrency-scope");
        var maxEl = document.getElementById("wf-run-max-parallel");
        var branchEl = document.getElementById("wf-run-branch-per-ticket");
        var maxCorrectionsEl = document.getElementById("wf-run-max-corrections");
        var autoCorrectionsEl = document.getElementById("wf-run-auto-corrections");
        var saveEl = document.getElementById("wf-run-settings-save");
        var noteEl = document.getElementById("wf-run-settings-lock-note");
        var panelEl = document.getElementById("wf-run-settings-panel");
        if (!modeEl || !scopeEl || !maxEl || !branchEl) return;
        modeEl.value = settings.execution_mode;
        scopeEl.value = settings.concurrency_scope;
        maxEl.value = settings.max_parallel_tickets;
        branchEl.checked = !!settings.branch_per_ticket;
        if (maxCorrectionsEl) maxCorrectionsEl.value = settings.max_correction_attempts;
        if (autoCorrectionsEl) autoCorrectionsEl.checked = !!settings.auto_dispatch_corrections;
        var locked = workflowHasActiveRuns();
        if (panelEl) {
            panelEl.classList.toggle("opacity-70", locked);
            panelEl.classList.toggle("border-amber-500/30", locked);
        }
        document.querySelectorAll(".wf-run-setting").forEach(function (el) { el.disabled = locked; });
        if (saveEl) saveEl.disabled = locked;
        if (noteEl) {
            noteEl.textContent = locked ? "Locked while this workflow has active runs." : "Controls how queued tickets are scheduled before a run starts.";
            noteEl.className = "text-xs " + (locked ? "text-amber-300" : "text-gray-500");
        }
    }

    function renderHermesSetupSummary() {
        var el = document.getElementById("wf-hermes-setup-summary");
        var noteEl = document.getElementById("wf-hermes-setup-note");
        if (!el) return;
        el.innerHTML = '<div class="col-span-full rounded border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-500">Loading execution setup...</div>';
        api("GET", "/workflows/hermes-setup")
            .then(function (data) {
                var readiness = Array.isArray(data.readiness) ? data.readiness : [];
                var readyCount = readiness.filter(function (item) { return item && item.status === "ready"; }).length;
                var total = readiness.length || 0;
                var sources = Array.isArray(data.connected_sources) ? data.connected_sources : [];
                if (noteEl) {
                    noteEl.textContent = (total ? readyCount + "/" + total + " ready" : "Execution setup") +
                        (sources.length ? " · Sources: " + sources.join(", ") : " · No connected source channels detected");
                }
                if (!readiness.length) {
                    el.innerHTML = '<div class="col-span-full rounded border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-500">No execution setup data yet.</div>';
                    return;
                }
                el.innerHTML = readiness.map(function (item) {
                    var ready = item.status === "ready";
                    var cls = ready ? "border-green-500/30 bg-green-500/10 text-green-200" : "border-amber-500/30 bg-amber-500/10 text-amber-200";
                    return '<div class="rounded border px-3 py-2 ' + cls + '">' +
                        '<p class="text-xs font-semibold">' + esc(item.label || item.key || "Setup") + '</p>' +
                        '<p class="mt-1 text-[11px] opacity-80">' + esc(item.detail || (ready ? "Ready" : "Needs setup")) + '</p>' +
                    '</div>';
                }).join("");
            })
            .catch(function () {
                el.innerHTML = '<div class="col-span-full rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">Failed to load execution setup.</div>';
                if (noteEl) noteEl.textContent = "Execution setup could not be loaded.";
            });
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
        var ticketsListEl = document.getElementById("wf-workflow-tickets-list");
        var stopAllBtn = document.getElementById("wf-stop-reset-btn");
        if (!listEl || !emptyEl || !ticketsListEl) return;
        var query = "/workflows/active-runs?limit=50";
        if (activeRunsScope === "current" && currentWorkflowId) {
            query += "&workflow_id=" + encodeURIComponent(currentWorkflowId);
        }
        api("GET", query).then(function (runs) {
            latestActiveRuns = Array.isArray(runs) ? runs : [];
            var stateByWorkflow = {};
            latestActiveRuns.forEach(function (r) {
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
            renderRunSettings(currentWorkflow);
            renderBoardConsumers(latestActiveRuns);
            renderRunCommandCenter(latestActiveRuns);
            renderWorkflowTickets(workflowQueueTickets);
            if (stopAllBtn) {
                var hasActiveCurrentWorkflowRuns = latestActiveRuns.some(function (r) {
                    return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
                });
                stopAllBtn.classList.toggle("hidden", !hasActiveCurrentWorkflowRuns);
            }
            if (!latestActiveRuns.length) {
                listEl.innerHTML = "";
                emptyEl.classList.remove("hidden");
                return;
            }
            emptyEl.classList.add("hidden");
            listEl.innerHTML = latestActiveRuns.map(function (r) {
                var isCurrentWorkflow = currentWorkflowId && String(currentWorkflowId) === String(r.workflow_id);
                var statusColor = r.status === "waiting" ? "text-amber-300 bg-amber-600/20" : "text-blue-300 bg-blue-600/20";
                var phase = r.phase ? String(r.phase) : "planning";
                var meta = runMetaText(r);
                var boardText = meta.boardText;
                var ticketText = meta.ticketText;
                var projectText = meta.projectText;
                var sourceText = meta.sourceText;
                var stepText = r.current_step_name || (r.current_step_id ? ("Step #" + r.current_step_id) : "Starting");
                var workflowText = meta.workflowText || ("Workflow #" + r.workflow_id);
                var routeCard = renderRouteCard(r.execution_route || {}, {
                    pendingApproval: r.pending_route_approval && Object.keys(r.pending_route_approval || {}).length
                });
                var rowCls = "rounded px-3 py-2 border border-white/10 " + (isCurrentWorkflow ? "wf-live-run" : "bg-[#152054]/50");
                var waitingKind = r.waiting_kind || "";
                var continueLabel = waitingKind === "ide_handoff"
                    ? "Report IDE complete"
                    : (waitingKind === "route_approval" ? "Review route" : "Continue");
                var actions = '<div class="flex items-center gap-2 ml-auto">' +
                    (r.status === "waiting" ? '<button type="button" class="wf-active-continue px-2 py-1 rounded border border-amber-500/50 text-amber-300 text-xs hover:bg-amber-500/20" data-workflow-id="' + esc(r.workflow_id) + '" data-run-id="' + esc(r.id) + '" data-waiting-kind="' + esc(waitingKind) + '">' + esc(continueLabel) + '</button>' : '') +
                    '<button type="button" class="wf-active-stop inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-workflow-id="' + esc(r.workflow_id) + '" data-run-id="' + esc(r.id) + '">' + SVG_STOP + '<span>Stop</span></button>' +
                '</div>';
                return '<div class="' + rowCls + '">' +
                    '<div class="flex items-center gap-2 mb-1">' +
                        '<span class="text-xs text-gray-400">Run #' + r.id + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded ' + statusColor + '">' + esc(r.status) + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded bg-green-600/20 text-green-300">' + esc(phase) + '</span>' +
                        '<span class="text-xs text-gray-500">Elapsed ' + esc(formatElapsed(r.elapsed_seconds)) + '</span>' +
                        actions +
                    '</div>' +
                    '<div class="grid grid-cols-1 md:grid-cols-2 gap-1 text-xs">' +
                        '<div><span class="text-gray-500">Board:</span> <span class="text-gray-200">' + esc(boardText) + '</span></div>' +
                        '<div><span class="text-gray-500">Ticket:</span> <span class="text-gray-200">' + esc(ticketText) + '</span></div>' +
                        '<div><span class="text-gray-500">Project:</span> <span class="text-gray-200">' + esc(projectText) + '</span></div>' +
                        '<div><span class="text-gray-500">Source:</span> <span class="text-gray-200">' + esc(sourceText) + '</span></div>' +
                        '<div><span class="text-gray-500">Workflow:</span> <span class="text-gray-200">' + esc(workflowText) + '</span></div>' +
                        '<div><span class="text-gray-500">Current step:</span> <span class="text-gray-200">' + esc(stepText) + '</span></div>' +
                    '</div>' +
                    '<div class="mt-2">' + routeCard + '</div>' +
                '</div>';
            }).join("");
            listEl.querySelectorAll(".wf-active-continue").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    btn.disabled = true;
                    var waitingKind = btn.dataset.waitingKind || "";
                    var workflowId = btn.dataset.workflowId;
                    var runId = btn.dataset.runId;
                    function submitContinue(feedback) {
                        continueWorkflowRun(workflowId, runId, { input: feedback || "" })
                            .then(function (resp) {
                                snack(workflowFeedbackText(resp, "Run continued"));
                                loadActiveRuns();
                                if (currentWorkflowId) loadDetail(currentWorkflowId);
                                refreshBoardHermesViews({ quiet: true });
                            })
                            .catch(function (e) {
                                btn.disabled = false;
                                snack(workflowErrorText(e, "Failed to continue"), "error");
                            });
                    }
                    if (waitingKind === "ide_handoff") {
                        btn.disabled = false;
                        openIdeHandoffModal(submitContinue);
                        return;
                    }
                    submitContinue("");
                });
            });
            listEl.querySelectorAll(".wf-active-stop").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    btn.disabled = true;
                    api("POST", "/workflows/" + encodeURIComponent(btn.dataset.workflowId) + "/cancel-run/" + encodeURIComponent(btn.dataset.runId))
                        .then(function (resp) {
                            snack(workflowFeedbackText(resp, "Run cancelled"));
                            stopPolling();
                            loadActiveRuns();
                            if (currentWorkflowId) loadDetail(currentWorkflowId);
                        })
                        .catch(function (e) {
                            btn.disabled = false;
                            snack(workflowErrorText(e, "Failed to cancel"), "error");
                        });
                });
            });
        }).catch(function () {
            renderWorkflowTickets(workflowQueueTickets);
        });
    }

    function renderWorkflowExecutionSessions(sessions) {
        var listEl = document.getElementById("wf-execution-sessions-list");
        var emptyEl = document.getElementById("wf-execution-sessions-empty");
        var clearBtn = document.getElementById("wf-clear-execution-sessions");
        if (!listEl || !emptyEl) return;
        sessions = Array.isArray(sessions) ? sessions : [];
        if (clearBtn) clearBtn.disabled = !sessions.length;
        if (!sessions.length) {
            listEl.innerHTML = "";
            emptyEl.classList.remove("hidden");
            return;
        }
        emptyEl.classList.add("hidden");
        listEl.innerHTML = sessions.map(function (session) {
            var active = executionSessionIsActive(session);
            var status = session.status || "session";
            var expanded = String(expandedWorkflowExecutionSessionId || "") === String(session.id || "");
            var timeLabel = active
                ? ("Elapsed " + formatElapsed(session.elapsed_seconds))
                : (session.duration_seconds != null ? ("Duration " + formatElapsed(session.duration_seconds)) : "");
            var eventCount = Array.isArray(session.events) ? session.events.length : 0;
            var latestEvent = eventCount ? session.events[eventCount - 1] : null;
            var output = executionSessionOutputText(session);
            var instruction = executionSessionInstructionPreview(session);
            var runtimeSnapshot = executionSessionRuntimeSnapshot(session);
            var runtimeUrls = runtimeSnapshot && Array.isArray(runtimeSnapshot.urls) ? runtimeSnapshot.urls : [];
            var runtimeUrl = runtimeUrls.length && runtimeUrls[0] && runtimeUrls[0].url ? runtimeUrls[0].url : "";
            var runtimeCount = runtimeSnapshot && runtimeSnapshot.active_terminal_count != null ? runtimeSnapshot.active_terminal_count : null;
            var started = session.started_at ? new Date(session.started_at).toLocaleString() : "";
            var completed = session.completed_at ? new Date(session.completed_at).toLocaleString() : "";
            var detailHtml = expanded ? (
                '<div class="mt-3 border-t border-white/10 pt-3 space-y-3">' +
                    '<div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">' +
                        '<div><span class="text-gray-500">Started:</span> <span class="text-gray-300">' + esc(started || "-") + '</span></div>' +
                        '<div><span class="text-gray-500">Completed:</span> <span class="text-gray-300">' + esc(completed || "-") + '</span></div>' +
                        '<div><span class="text-gray-500">Folder:</span> <span class="text-gray-300 font-mono break-all">' + esc(session.project_folder || "-") + '</span></div>' +
                        '<div><span class="text-gray-500">Session:</span> <span class="text-gray-300">#' + esc(session.id) + '</span></div>' +
                    '</div>' +
                    renderRuntimeSnapshot(runtimeSnapshot) +
                    (instruction ? '<div><p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Instruction</p><pre class="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/20 border border-white/10 p-2 text-[11px] text-gray-300">' + esc(instruction) + '</pre></div>' : '') +
                    (output ? '<div><p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Output</p><pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/20 border border-white/10 p-2 text-[11px] text-gray-300">' + esc(output) + '</pre></div>' : '') +
                    '<div><p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Events</p>' +
                        (eventCount ? '<div class="rounded border border-white/10 divide-y divide-white/10">' + session.events.slice(-8).map(function (event) {
                            return '<div class="px-2 py-1.5 text-xs">' +
                                '<div class="flex items-center gap-2">' +
                                    '<span class="text-gray-500">' + esc(event.created_at ? new Date(event.created_at).toLocaleTimeString() : "") + '</span>' +
                                    '<span class="text-gray-300">' + esc(event.event_type || "event") + '</span>' +
                                    (event.status ? '<span class="text-gray-500">/ ' + esc(event.status) + '</span>' : '') +
                                '</div>' +
                                (event.message ? '<div class="text-gray-400 mt-0.5 whitespace-pre-wrap">' + esc(event.message) + '</div>' : '') +
                            '</div>';
                        }).join("") + '</div>' : '<div class="text-xs text-gray-500">No events recorded yet.</div>') +
                    '</div>' +
                '</div>'
            ) : "";
            return '<div class="rounded border border-white/10 bg-[#152054]/45 px-3 py-2">' +
                '<div class="flex items-start gap-3">' +
                    '<div class="min-w-0 flex-1">' +
                        '<div class="flex items-center gap-2 min-w-0">' +
                            '<span class="text-xs text-gray-500">Session #' + esc(session.id) + '</span>' +
                            '<span class="text-[11px] px-1.5 py-0.5 rounded ' + executionSessionStatusClass(status) + '">' + esc(status) + '</span>' +
                            '<span class="text-[11px] px-1.5 py-0.5 rounded bg-white/10 text-gray-300">' + esc(session.backend_id || session.route_backend || "backend") + '</span>' +
                            '<span class="text-[11px] text-gray-500 truncate">' + esc(session.model || session.selected_model || "auto") + '</span>' +
                            (timeLabel ? '<span class="ml-auto text-xs text-gray-500">' + esc(timeLabel) + '</span>' : '') +
                        '</div>' +
                        '<div class="mt-1 text-sm text-white truncate">' + esc(session.ticket_title || ("Ticket #" + (session.ticket_id || ""))) + '</div>' +
                        '<div class="mt-1 grid grid-cols-1 md:grid-cols-3 gap-1 text-xs">' +
                            '<div><span class="text-gray-500">Project:</span> <span class="text-gray-200">' + esc(session.project_name || (session.project_id ? "Project #" + session.project_id : "No project")) + '</span></div>' +
                            '<div><span class="text-gray-500">Board:</span> <span class="text-gray-200">' + esc(session.board_name || "No board") + '</span></div>' +
                            '<div><span class="text-gray-500">Runtime:</span> <span class="text-gray-200">' + esc(runtimeUrl || (runtimeCount != null ? runtimeCount + " terminal(s)" : "Not observed")) + '</span></div>' +
                        '</div>' +
                        (latestEvent ? '<div class="mt-2 text-xs text-gray-400 truncate">' + esc(latestEvent.message || latestEvent.event_type || "") + '</div>' : '') +
                        (session.error ? '<div class="mt-2 text-xs text-red-300 whitespace-pre-wrap">' + esc(session.error) + '</div>' : '') +
                        detailHtml +
                    '</div>' +
                    '<div class="flex flex-col items-end gap-1 flex-shrink-0">' +
                        '<button type="button" class="wf-execution-session-toggle px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10" data-session-id="' + esc(session.id) + '">' + (expanded ? "Hide" : "Details") + '</button>' +
                        (session.ticket_id ? '<button type="button" class="wf-execution-session-ticket px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10" data-ticket-id="' + esc(session.ticket_id) + '">Open ticket</button>' : '') +
                    '</div>' +
                '</div>' +
            '</div>';
        }).join("");
        listEl.querySelectorAll(".wf-execution-session-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var id = btn.dataset.sessionId || "";
                expandedWorkflowExecutionSessionId = String(expandedWorkflowExecutionSessionId || "") === id ? null : id;
                renderWorkflowExecutionSessions(latestWorkflowExecutionSessions);
            });
        });
        listEl.querySelectorAll(".wf-execution-session-ticket").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var ticketId = btn.dataset.ticketId || "";
                if (!ticketId) return;
                api("GET", "/kanban/tickets/" + encodeURIComponent(ticketId))
                    .then(function (ticket) {
                        openWorkflowTicketModal(ticket, { selected: { source: "database", value: "", label: ticket.board_name || "Workflow execution" } });
                    })
                    .catch(function (e) { snack(e.message || "Failed to open ticket", "error"); });
            });
        });
    }

    function loadWorkflowExecutionSessions(options) {
        options = options || {};
        if (!currentWorkflowId) {
            latestWorkflowExecutionSessions = [];
            renderWorkflowExecutionSessions([]);
            stopExecutionSessionPolling();
            renderWorkflowTickets(workflowQueueTickets);
            renderWorkflowCliTab();
            return;
        }
        api("GET", "/kanban/workflows/" + encodeURIComponent(currentWorkflowId) + "/execution-sessions?limit=50")
            .then(function (data) {
                latestWorkflowExecutionSessions = Array.isArray(data.sessions) ? data.sessions : [];
                renderWorkflowExecutionSessions(latestWorkflowExecutionSessions);
                syncExecutionSessionPolling();
                renderWorkflowTickets(workflowQueueTickets);
                renderWorkflowCliTab();
            })
            .catch(function (e) {
                latestWorkflowExecutionSessions = [];
                renderWorkflowExecutionSessions([]);
                syncExecutionSessionPolling();
                if (!options.quiet) snack(e.message || "Failed to load CLI execution sessions", "error");
            });
    }

    function loadWorkflowTicketQueue() {
        if (!currentWorkflowId) {
            workflowQueueTickets = [];
            renderWorkflowTickets([]);
            return;
        }
        api("GET", "/kanban/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets")
            .then(function (tickets) {
                workflowQueueTickets = Array.isArray(tickets) ? tickets : [];
                workflowLinkedExternalTicketKeys = {};
                workflowQueueTickets.forEach(function (ticket) {
                    var source = ticket.external_source || ticket.source_provider || "";
                    var externalId = ticket.external_id || ticket.source_external_id || "";
                    if (source && externalId) {
                        workflowLinkedExternalTicketKeys[String(source).toLowerCase() + ":" + String(externalId)] = true;
                    }
                });
                renderWorkflowTickets(workflowQueueTickets);
            })
            .catch(function (e) {
                snack(e.message || "Failed to load workflow tickets", "error");
            });
    }

    function activeRunByTicketId(ticketId) {
        var id = String(ticketId || "");
        return (latestActiveRuns || []).filter(function (r) {
            return r && r.ticket_id && String(r.ticket_id) === id && currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
        })[0] || null;
    }

    function workflowQueueBoardKey(ticket) {
        if (!ticket) return "";
        if (ticket.board_id != null && ticket.board_id !== "") return "board:" + String(ticket.board_id);
        if (ticket.board_name) return "name:" + String(ticket.board_name).toLowerCase();
        return "";
    }

    function workflowQueueBoardLabel(ticket) {
        return (ticket && ticket.board_name) ? ticket.board_name : "Unknown board";
    }

    function workflowQueueBoardFilters(tickets) {
        var seen = {};
        var filters = [];
        tickets.forEach(function (ticket) {
            var key = workflowQueueBoardKey(ticket);
            if (!key || seen[key]) return;
            seen[key] = true;
            filters.push({
                key: key,
                label: workflowQueueBoardLabel(ticket),
                project: ticket.linked_project_name || projectNameById(ticket.linked_project_id)
            });
        });
        filters.sort(function (a, b) { return a.label.localeCompare(b.label); });
        return filters;
    }

    function renderWorkflowQueueFilters(filters, totalCount, visibleCount) {
        if (!filters.length) return "";
        var allActive = workflowQueueBoardFilter === "all";
        var html = '<div class="mt-2 flex flex-wrap items-center gap-2 border-t border-white/10 pt-2">' +
            '<button type="button" class="wf-workflow-ticket-filter inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs ' + (allActive ? "bg-[#f97316] text-white" : "bg-white/10 text-gray-300 hover:bg-white/15") + '" data-board-filter="all">' +
                '<span>All</span>' +
                '<span class="wf-count-badge ' + (allActive ? "" : "wf-count-badge-muted") + '">' + totalCount + '</span>' +
            '</button>';
        filters.forEach(function (filter) {
            var count = workflowQueueTickets.filter(function (ticket) { return workflowQueueBoardKey(ticket) === filter.key; }).length;
            var active = workflowQueueBoardFilter === filter.key;
            html += '<button type="button" class="wf-workflow-ticket-filter inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs ' + (active ? "bg-[#f97316] text-white" : "bg-white/10 text-gray-300 hover:bg-white/15") + '" data-board-filter="' + esc(filter.key) + '">' +
                '<span>' + esc(filter.label) + '</span>' +
                (filter.project ? '<span class="rounded bg-black/20 px-1.5 py-0.5 text-[10px] opacity-90">' + esc(filter.project) + '</span>' : '') +
                '<span class="wf-count-badge ' + (active ? "" : "wf-count-badge-muted") + '">' + count + '</span>' +
            '</button>';
        });
        if (visibleCount !== totalCount) {
            html += '<span class="text-xs text-gray-500 ml-auto">' + visibleCount + ' shown</span>';
        }
        return html + '</div>';
    }

    function workflowTicketRouteLabel(ticket) {
        var route = ticket && ticket.cli_route ? ticket.cli_route : {};
        var backend = route.backend || route.backend_id || "";
        var model = route.model || "";
        var source = route.source || route.route_source || "";
        var label = "";
        if (!backend && !model) return "";
        if (!model || String(model).toLowerCase() === "auto") label = backend || "auto";
        else label = backend ? backend + " / " + model : model;
        if (source) label += " · " + source.replace(/_/g, " ");
        return label;
    }

    function formatRouteSource(source) {
        if (!source) return "policy";
        return String(source).replace(/_/g, " ");
    }

    function renderRouteCard(route, options) {
        options = options || {};
        route = route && typeof route === "object" ? route : {};
        var backend = route.backend || route.backend_id || "auto";
        var model = route.model || "auto";
        var source = route.source || route.route_source || "policy";
        var rationale = route.rationale || route.route_rationale || "";
        var pending = options.pendingApproval || route.requires_approval;
        var html = '<div class="rounded border border-blue-500/25 bg-blue-500/5 px-3 py-2 text-xs">' +
            '<div class="flex flex-wrap items-center gap-2">' +
                '<span class="text-[10px] uppercase tracking-wide text-blue-200">Route</span>' +
                '<span class="rounded bg-white/10 px-1.5 py-0.5 text-gray-200">' + esc(backend) + '</span>' +
                '<span class="rounded bg-white/10 px-1.5 py-0.5 text-gray-300">' + esc(model) + '</span>' +
                '<span class="rounded bg-blue-500/15 px-1.5 py-0.5 text-blue-200">' + esc(formatRouteSource(source)) + '</span>' +
                (pending ? '<span class="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-200">override pending approval</span>' : '') +
            '</div>';
        if (rationale) html += '<p class="mt-1 text-[11px] text-gray-400">' + esc(rationale) + '</p>';
        html += '</div>';
        return html;
    }

    function openIdeHandoffModal(onSubmit) {
        var existing = document.getElementById("wf-ide-handoff-modal");
        if (existing) existing.remove();
        var html = '' +
            '<div id="wf-ide-handoff-modal" class="fixed inset-0 z-[10001] flex items-center justify-center bg-black/60">' +
                '<div class="w-full max-w-lg mx-4 bg-[#1a1f3a] border border-white/20 rounded-xl shadow-2xl overflow-hidden">' +
                    '<div class="px-5 pt-5 pb-3">' +
                        '<p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">IDE handoff</p>' +
                        '<h3 class="text-white text-lg font-semibold">Report IDE work complete</h3>' +
                        '<p class="mt-1 text-xs text-gray-400">Summarize files changed, tests run, and current status.</p>' +
                    '</div>' +
                    '<div class="px-5 pb-2">' +
                        '<textarea id="wf-ide-handoff-feedback" rows="5" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none" placeholder="Updated auth middleware, added tests, ready for validation."></textarea>' +
                    '</div>' +
                    '<div class="flex items-center justify-end gap-2 px-5 py-4 border-t border-white/10">' +
                        '<button type="button" class="wf-ide-handoff-cancel px-3 py-1.5 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10">Cancel</button>' +
                        '<button type="button" id="wf-ide-handoff-submit" class="px-3 py-1.5 rounded bg-[#f97316] text-white text-xs font-medium hover:bg-[#ea580c]">Continue workflow</button>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        var modal = document.getElementById("wf-ide-handoff-modal");
        var textarea = document.getElementById("wf-ide-handoff-feedback");
        function closeModal() { if (modal) modal.remove(); }
        modal.addEventListener("click", function (evt) { if (evt.target === modal) closeModal(); });
        modal.querySelectorAll(".wf-ide-handoff-cancel").forEach(function (btn) { btn.addEventListener("click", closeModal); });
        var submitBtn = document.getElementById("wf-ide-handoff-submit");
        if (submitBtn) {
            submitBtn.addEventListener("click", function () {
                var feedback = textarea ? (textarea.value || "").trim() : "";
                if (!feedback) {
                    snack("Add a short IDE completion summary", "error");
                    return;
                }
                closeModal();
                if (typeof onSubmit === "function") onSubmit(feedback);
            });
        }
        if (textarea) textarea.focus();
    }

    function renderRunCommandCenter(runs) {
        var el = document.getElementById("wf-run-command-center");
        if (!el) return;
        runs = Array.isArray(runs) ? runs : [];
        var currentRuns = runs.filter(function (r) {
            return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
        });
        if (!currentRuns.length) {
            el.classList.add("hidden");
            el.innerHTML = "";
            return;
        }
        var run = currentRuns[0];
        var route = run.execution_route || {};
        var pending = run.pending_route_approval || {};
        var hasPendingRoute = pending && typeof pending === "object" && Object.keys(pending).length > 0;
        var pendingBackend = hasPendingRoute ? (pending.backend || "auto") : "";
        var pendingModel = hasPendingRoute ? (pending.model || "auto") : "";
        var pendingRationale = hasPendingRoute ? (pending.rationale || "") : "";
        var runtimeHtml = "";
        if (run.project_id) {
            runtimeHtml = '<p class="text-[11px] text-gray-500 mt-2">Project #' + esc(run.project_id) + (run.project_name ? " · " + esc(run.project_name) : "") + '</p>';
        }
        var approvalHtml = "";
        if (hasPendingRoute && (run.waiting_kind === "route_approval" || run.status === "waiting")) {
            approvalHtml = '<div class="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-3">' +
                '<p class="text-xs text-amber-200 font-medium">Route override pending approval</p>' +
                '<p class="mt-1 text-[11px] text-gray-300">Suggested: <span class="font-mono">' + esc(pendingBackend) + '</span>' +
                (pendingModel ? ' / <span class="font-mono">' + esc(pendingModel) + '</span>' : '') + '</p>' +
                (pendingRationale ? '<p class="mt-1 text-[11px] text-gray-400">' + esc(pendingRationale) + '</p>' : '') +
                '<div class="mt-2 flex items-center gap-2">' +
                    '<button type="button" class="wf-route-approve px-2 py-1 rounded bg-green-600/80 text-white text-xs hover:bg-green-600" data-run-id="' + esc(run.id) + '" data-workflow-id="' + esc(run.workflow_id) + '">Approve override</button>' +
                    '<button type="button" class="wf-route-reject px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10" data-run-id="' + esc(run.id) + '" data-workflow-id="' + esc(run.workflow_id) + '">Use policy route</button>' +
                '</div>' +
            '</div>';
        }
        el.classList.remove("hidden");
        var steerHtml = "";
        if (run.steerable) {
            steerHtml = '<div class="mt-3 rounded border border-white/10 bg-[#0d1333]/60 p-3">' +
                '<p class="text-xs text-gray-300 font-medium">Steer harness</p>' +
                '<p class="text-[11px] text-gray-500 mt-1">Redirect Pi/Codex mid-step without restarting the run.</p>' +
                '<textarea id="wf-run-steer-input" rows="2" class="mt-2 w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs" placeholder="Focus on the login form only — skip the dashboard refactor"></textarea>' +
                '<div class="mt-2 flex items-center gap-2">' +
                    '<button type="button" class="wf-run-steer-send px-2 py-1 rounded bg-[#f97316] text-white text-xs hover:bg-[#ea580c]" data-run-id="' + esc(run.id) + '" data-workflow-id="' + esc(run.workflow_id) + '">Send steer</button>' +
                '</div>' +
                (run.last_harness_steer && run.last_harness_steer.message ?
                    '<p class="mt-2 text-[11px] text-gray-500">Last steer (' + esc(run.last_harness_steer.method || "queued") + '): ' + esc(String(run.last_harness_steer.message).slice(0, 120)) + '</p>' : '') +
            '</div>';
        }
        var handoff = run.latest_backend_handoff && typeof run.latest_backend_handoff === "object" ? run.latest_backend_handoff : {};
        var humanHtml = "";
        if ((run.human_intervention_state && run.human_intervention_state !== "none") || run.worker_question || handoff.backend_id) {
            humanHtml = '<div class="mt-3 rounded border border-amber-500/25 bg-amber-500/10 p-3">' +
                '<p class="text-xs text-amber-100 font-medium">Human intervention</p>' +
                '<p class="mt-1 text-[11px] text-gray-300">State: <span class="font-mono">' + esc(run.human_intervention_state || "none") + '</span>' +
                    (run.next_action ? ' · next: <span class="font-mono">' + esc(run.next_action) + '</span>' : '') + '</p>' +
                (run.worker_question ? '<p class="mt-1 text-[11px] text-gray-300">Worker asks: ' + esc(String(run.worker_question).slice(0, 240)) + '</p>' : '') +
                (handoff.backend_id ? '<p class="mt-1 text-[11px] text-gray-500">Handoff: ' + esc(handoff.backend_id || "") + (handoff.model ? " / " + esc(handoff.model) : "") + (handoff.handoff_event_id ? " · event #" + esc(handoff.handoff_event_id) : "") + '</p>' : '') +
            '</div>';
        }
        el.innerHTML = '<div class="flex items-start justify-between gap-3 mb-2">' +
                '<div><p class="text-sm font-semibold text-white">Run command center</p>' +
                '<p class="text-xs text-gray-400">Run #' + esc(run.id) + ' · ' + esc(run.status || "running") + '</p></div>' +
                (run.ide_handoff_pending ? '<span class="rounded bg-amber-500/15 px-2 py-1 text-[11px] text-amber-200">IDE handoff pending</span>' : '') +
            '</div>' +
            renderRouteCard(route, { pendingApproval: hasPendingRoute }) +
            approvalHtml +
            humanHtml +
            steerHtml +
            runtimeHtml;
        el.querySelectorAll(".wf-route-approve").forEach(function (btn) {
            btn.addEventListener("click", function () {
                submitRouteApproval(btn.dataset.workflowId, btn.dataset.runId, true);
            });
        });
        el.querySelectorAll(".wf-route-reject").forEach(function (btn) {
            btn.addEventListener("click", function () {
                submitRouteApproval(btn.dataset.workflowId, btn.dataset.runId, false);
            });
        });
        el.querySelectorAll(".wf-run-steer-send").forEach(function (btn) {
            btn.addEventListener("click", function () {
                submitHarnessSteer(btn.dataset.workflowId, btn.dataset.runId);
            });
        });
    }

    function submitHarnessSteer(workflowId, runId) {
        var input = document.getElementById("wf-run-steer-input");
        var message = input ? (input.value || "").trim() : "";
        if (!message) {
            snack("Enter a steer message", "error");
            return;
        }
        api("POST", "/workflows/" + encodeURIComponent(workflowId) + "/runs/" + encodeURIComponent(runId) + "/steer", {
            message: message
        }).then(function (resp) {
            snack(resp.delivered ? "Steer delivered to harness" : "Steer queued for harness", "success");
            if (input) input.value = "";
            loadActiveRuns();
            loadHermesTimeline({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to steer harness", "error");
        });
    }

    function submitRouteApproval(workflowId, runId, approved) {
        if (!workflowId || !runId) return;
        api("POST", "/workflows/" + encodeURIComponent(workflowId) + "/runs/" + encodeURIComponent(runId) + "/route-approval", {
            approved: !!approved
        }).then(function (resp) {
            snack(approved ? "Route override approved" : "Using policy route", "success");
            loadActiveRuns();
            if (currentWorkflowId) loadDetail(currentWorkflowId);
            loadHermesTimeline({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to update route approval", "error");
        });
    }

    function parseSkillChainInput(raw) {
        return String(raw || "").split(",").map(function (part) { return part.trim(); }).filter(Boolean);
    }

    function loadWorkflowSkillsCatalog() {
        if (workflowSkillsCatalogLoaded) return Promise.resolve(workflowSkillsCatalog);
        return api("GET", "/workflows/skills?limit=300").then(function (data) {
            workflowSkillsCatalog = Array.isArray(data && data.skills) ? data.skills : [];
            workflowSkillsCatalogLoaded = true;
            renderSkillsCatalogPanel();
            return workflowSkillsCatalog;
        }).catch(function () {
            workflowSkillsCatalog = [];
            return workflowSkillsCatalog;
        });
    }

    function renderSkillsCatalogPanel() {
        var datalist = document.getElementById("wf-skills-datalist");
        var panel = document.getElementById("wf-skills-catalog");
        if (!datalist && !panel) return;
        if (datalist) {
            datalist.innerHTML = workflowSkillsCatalog.map(function (skill) {
                var label = (skill.name || skill.id || "") + " — " + String(skill.description || "").slice(0, 80);
                return '<option value="' + esc(skill.id || "") + '">' + esc(label) + "</option>";
            }).join("");
        }
        if (panel && workflowSkillsCatalog.length) {
            panel.classList.remove("hidden");
            var googleSkills = workflowSkillsCatalog.filter(function (s) { return (s.source || "") === "google/skills"; });
            var coreSkills = workflowSkillsCatalog.filter(function (s) { return (s.source || "") !== "google/skills"; });
            panel.innerHTML =
                "<p class=\"text-gray-300 mb-1\">Bundled skills (" + workflowSkillsCatalog.length + ")</p>" +
                (googleSkills.length ? "<p class=\"text-[#f97316] mb-1\">Google Cloud (" + googleSkills.length + "): " +
                    googleSkills.slice(0, 8).map(function (s) { return esc(s.id); }).join(", ") +
                    (googleSkills.length > 8 ? "…" : "") + "</p>" : "") +
                "<p class=\"text-gray-500\">Core: " + coreSkills.slice(0, 10).map(function (s) { return esc(s.id); }).join(", ") +
                    (coreSkills.length > 10 ? "…" : "") + "</p>";
        }
    }

    function renderSkillChains(data) {
        var preEl = document.getElementById("wf-pre-chain");
        var postEl = document.getElementById("wf-post-chain");
        var saveEl = document.getElementById("wf-skill-chains-save");
        if (!preEl || !postEl) return;
        var pre = Array.isArray(data && data.pre_chain) ? data.pre_chain : [];
        var post = Array.isArray(data && data.post_chain) ? data.post_chain : [];
        preEl.value = pre.join(", ");
        postEl.value = post.join(", ");
        var locked = workflowHasActiveRuns();
        preEl.disabled = locked;
        postEl.disabled = locked;
        if (saveEl) saveEl.disabled = locked;
    }

    function saveSkillChains() {
        if (!currentWorkflowId) return;
        var preEl = document.getElementById("wf-pre-chain");
        var postEl = document.getElementById("wf-post-chain");
        var saveEl = document.getElementById("wf-skill-chains-save");
        if (saveEl) saveEl.disabled = true;
        api("PATCH", "/workflows/" + currentWorkflowId, {
            pre_chain: parseSkillChainInput(preEl ? preEl.value : ""),
            post_chain: parseSkillChainInput(postEl ? postEl.value : "")
        }).then(function () {
            snack("Skill chains saved", "success");
            if (currentWorkflowId) loadDetail(currentWorkflowId);
        }).catch(function (e) {
            snack(e.message || "Failed to save skill chains", "error");
        }).finally(function () {
            if (saveEl) saveEl.disabled = workflowHasActiveRuns();
        });
    }

    function workflowComplexitySelect(ticket, locked) {
        var current = normalizeComplexity(ticket && ticket.complexity);
        return '<select class="wf-workflow-ticket-complexity w-full rounded-md border px-2 py-1.5 text-xs leading-tight focus:border-[#f97316] focus:outline-none disabled:opacity-60" title="Ticket complexity routing" aria-label="Ticket complexity routing" data-ticket-id="' + esc(ticket.id) + '"' + (locked ? " disabled" : "") + '>' +
            '<option value="low"' + (current === "low" ? " selected" : "") + '>Low</option>' +
            '<option value="medium"' + (current === "medium" ? " selected" : "") + '>Medium</option>' +
            '<option value="high"' + (current === "high" ? " selected" : "") + '>High</option>' +
        '</select>';
    }

    function renderWorkflowTickets(tickets) {
        var listEl = document.getElementById("wf-workflow-tickets-list");
        var emptyEl = document.getElementById("wf-workflow-tickets-empty");
        if (!listEl || !emptyEl) return;
        tickets = Array.isArray(tickets) ? tickets.slice() : [];
        tickets.sort(function (a, b) {
            return (a.workflow_queue_position || 0) - (b.workflow_queue_position || 0) || (a.id || 0) - (b.id || 0);
        });
        if (!tickets.length) {
            listEl.innerHTML = '<div class="h-full min-h-[300px] flex items-center justify-center text-center text-sm text-gray-500 pointer-events-none">Drop a ticket here to add it to this workflow queue.</div>';
            emptyEl.classList.remove("hidden");
            listEl.classList.add("border-dashed");
            bindWorkflowTicketDropZone();
            renderWorkflowCliTab();
            return;
        }
        var boardFilters = workflowQueueBoardFilters(tickets);
        if (workflowQueueBoardFilter !== "all" && !boardFilters.some(function (filter) { return filter.key === workflowQueueBoardFilter; })) {
            workflowQueueBoardFilter = "all";
        }
        var visibleTickets = workflowQueueBoardFilter === "all"
            ? tickets
            : tickets.filter(function (ticket) { return workflowQueueBoardKey(ticket) === workflowQueueBoardFilter; });
        emptyEl.classList.add("hidden");
        listEl.classList.remove("border-dashed");
        var rowsHtml = visibleTickets.map(function (ticket, idx) {
            var queueIndex = tickets.findIndex(function (item) { return String(item.id) === String(ticket.id); });
            var queueId = queueIndex >= 0 ? queueIndex + 1 : idx + 1;
            var run = activeRunByTicketId(ticket.id);
            var executionSession = activeExecutionSessionByTicketId(ticket.id);
            var status = run ? (run.status || "running") : (executionSession ? (executionSession.status || "running") : "Queued");
            var statusColor = run || executionSession
                ? (status === "waiting" || status === "queued" ? "text-amber-300 bg-amber-600/20" : "text-blue-300 bg-blue-600/20")
                : "text-gray-300 bg-white/10";
            var title = ticket.title || ("Ticket #" + ticket.id);
            var canRun = !run;
            var canRemove = !run && !executionSession;
            var dragClass = run || executionSession ? "cursor-default" : "cursor-grab active:cursor-grabbing";
            var priority = ticket.priority || "medium";
            var locked = !!(run || executionSession);
            var routeLabel = workflowTicketRouteLabel(ticket);
            var boardProject = esc(ticket.board_name || "No board") + (ticket.linked_project_name ? " / " + esc(ticket.linked_project_name) : "");
            return '<tr class="wf-workflow-ticket-row border-t border-white/10 bg-[#152054]/30 hover:bg-white/5 ' + dragClass + '" draggable="' + (run || executionSession ? "false" : "true") + '" data-ticket-id="' + esc(ticket.id) + '">' +
                '<td class="px-3 py-3 align-middle text-xs text-gray-400">' +
                    '<span class="wf-count-badge wf-count-badge-muted">' + queueId + '</span>' +
                '</td>' +
                '<td class="px-3 py-3 align-middle min-w-0">' +
                    '<p class="text-sm text-white font-medium leading-snug whitespace-normal break-words">' + esc(title) + '</p>' +
                    '<div class="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">' +
                        '<span class="rounded bg-white/5 px-1.5 py-0.5" title="Board / project">' + boardProject + '</span>' +
                        '<span class="rounded bg-white/5 px-1.5 py-0.5" title="Priority">' + esc(priority) + '</span>' +
                        (routeLabel ? '<span class="rounded bg-blue-500/10 px-1.5 py-0.5 text-blue-300" title="' + esc(routeLabel) + '">' + esc(routeLabel) + '</span>' : '') +
                    '</div>' +
                '</td>' +
                '<td class="px-2 py-3 align-middle">' + workflowComplexitySelect(ticket, locked) + '</td>' +
                '<td class="px-2 py-3 align-middle">' +
                    '<span class="inline-flex whitespace-nowrap text-xs px-2 py-1 rounded ' + statusColor + '">' + esc(status) + '</span>' +
                '</td>' +
                '<td class="px-2 py-3 align-middle text-right">' +
                    '<div class="inline-flex items-center justify-end gap-3">' +
                        (canRun ? '<button type="button" class="wf-workflow-ticket-run inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#f97316] text-white text-xs font-medium hover:bg-[#ea580c]" data-ticket-id="' + esc(ticket.id) + '" title="Run ticket through this workflow">' + SVG_PLAY + '<span>Run</span></button>' : '') +
                        (canRemove ? '<button type="button" class="wf-workflow-ticket-remove inline-flex h-10 w-10 items-center justify-center rounded text-red-400 hover:bg-red-500/20 hover:text-red-300" data-ticket-id="' + esc(ticket.id) + '" title="Remove from workflow" aria-label="Remove from workflow">' + SVG_TRASH + '</button>' : '<span class="inline-flex h-10 w-10 items-center justify-center text-gray-600" title="Locked while running">-</span>') +
                    '</div>' +
                '</td>' +
            '</tr>';
        }).join("");
        listEl.innerHTML = '<div class="overflow-auto rounded border border-white/10">' +
            '<table class="w-full table-fixed text-left">' +
                '<colgroup>' +
                    '<col style="width: 3rem;">' +
                    '<col>' +
                    '<col style="width: 6.5rem;">' +
                    '<col style="width: 6.25rem;">' +
                    '<col style="width: 8.5rem;">' +
                '</colgroup>' +
                '<thead class="bg-[#10183f] text-[11px] uppercase tracking-wide text-gray-500">' +
                    '<tr>' +
                        '<th class="px-2 py-2 font-medium">QID</th>' +
                        '<th class="px-3 py-2 font-medium">Ticket</th>' +
                        '<th class="px-2 py-2 font-medium">Complexity</th>' +
                        '<th class="px-2 py-2 font-medium">Status</th>' +
                        '<th class="px-2 py-2 font-medium text-right"></th>' +
                    '</tr>' +
                '</thead>' +
                '<tbody>' + rowsHtml + '</tbody>' +
            '</table>' +
        '</div>' +
        renderWorkflowQueueFilters(boardFilters, tickets.length, visibleTickets.length);
        bindWorkflowTicketQueueRows(listEl);
        listEl.querySelectorAll(".wf-workflow-ticket-filter").forEach(function (btn) {
            btn.addEventListener("click", function () {
                workflowQueueBoardFilter = btn.dataset.boardFilter || "all";
                renderWorkflowTickets(workflowQueueTickets);
            });
        });
        listEl.querySelectorAll(".wf-workflow-ticket-complexity").forEach(function (select) {
            select.addEventListener("click", function (evt) { evt.stopPropagation(); });
            select.addEventListener("dragstart", function (evt) { evt.preventDefault(); });
            select.addEventListener("change", function () {
                updateWorkflowQueueTicketComplexity(select.dataset.ticketId || "", select.value);
            });
        });
        bindWorkflowTicketDropZone();
        renderWorkflowCliTab();
    }

    function normalizeComplexity(value) {
        value = String(value || "medium").toLowerCase();
        return value === "low" || value === "high" ? value : "medium";
    }

    function updateWorkflowQueueTicketComplexity(ticketId, value) {
        var ticket = workflowQueueTickets.filter(function (item) { return String(item.id) === String(ticketId); })[0];
        if (!ticket || !ticket.id) return;
        var next = normalizeComplexity(value);
        if (normalizeComplexity(ticket.complexity) === next) return;
        api("PUT", "/kanban/tickets/" + encodeURIComponent(ticket.id), { complexity: next })
            .then(function () {
                ticket.complexity = next;
                snack("Ticket complexity updated");
                loadWorkflowTicketQueue();
                var boardSelect = document.getElementById("wf-board-select");
                if (boardSelect && boardSelect.value) loadWorkflowBoardTickets(boardSelect.value);
            })
            .catch(function (e) {
                snack(e.message || "Failed to update ticket complexity", "error");
                renderWorkflowTickets(workflowQueueTickets);
            });
    }

    function workflowCliTicketById(ticketId) {
        return (workflowQueueTickets || []).filter(function (ticket) {
            return String(ticket.id) === String(ticketId);
        })[0] || null;
    }

    function resetWorkflowCliControls(message) {
        var output = document.getElementById("wf-cli-output");
        if (output && message) output.innerHTML = '<div class="text-gray-500">' + esc(message) + '</div>';
    }

    function renderWorkflowCliTab() {
        var list = document.getElementById("wf-cli-ticket-list");
        var title = document.getElementById("wf-cli-title");
        var meta = document.getElementById("wf-cli-meta");
        var output = document.getElementById("wf-cli-output");
        if (!list || !title || !meta || !output) return;
        var tickets = (workflowQueueTickets || []).slice().sort(function (a, b) {
            return (a.workflow_queue_position || 0) - (b.workflow_queue_position || 0) || (a.id || 0) - (b.id || 0);
        });
        if (!tickets.length) {
            selectedWorkflowCliTicketId = null;
            list.innerHTML = '<div class="h-full flex items-center justify-center px-4 text-center text-sm text-gray-500">No tickets in this workflow queue.</div>';
            title.textContent = "No ticket selected";
            meta.textContent = "Drag tickets into the workflow queue first.";
            resetWorkflowCliControls("No CLI activity selected.");
            output.innerHTML = '<div class="text-gray-500">No CLI activity selected.</div>';
            return;
        }
        if (!selectedWorkflowCliTicketId || !workflowCliTicketById(selectedWorkflowCliTicketId)) {
            selectedWorkflowCliTicketId = String(tickets[0].id);
        }
        list.innerHTML = tickets.map(function (ticket, idx) {
            var active = String(ticket.id) === String(selectedWorkflowCliTicketId);
            var run = activeRunByTicketId(ticket.id);
            var executionSession = activeExecutionSessionByTicketId(ticket.id);
            var projectName = ticket.linked_project_name || projectNameById(ticket.linked_project_id);
            var runBadge = run
                ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">workflow run</span>'
                : (executionSession ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">CLI ' + esc(executionSession.status || "running") + '</span>' : '');
            var projectBadge = projectName ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-300">' + esc(projectName) + '</span>' : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-200">no project</span>';
            return '<button type="button" class="wf-cli-ticket-row w-full text-left px-3 py-2 transition-colors ' + (active ? 'bg-blue-500/20 border-l-2 border-blue-500' : 'hover:bg-white/5 border-l-2 border-transparent') + '" data-ticket-id="' + esc(ticket.id) + '">' +
                '<div class="flex items-start justify-between gap-2">' +
                    '<div class="min-w-0">' +
                        '<p class="text-xs text-white truncate">' + (idx + 1) + '. ' + esc(ticket.title || ("Ticket #" + ticket.id)) + '</p>' +
                        '<p class="text-[11px] text-gray-500 truncate">' + esc(ticket.board_name || "No board") + (ticket.lane_name ? " / " + esc(ticket.lane_name) : "") + '</p>' +
                    '</div>' +
                    '<div class="flex flex-col items-end gap-1 flex-shrink-0">' + runBadge + projectBadge + '</div>' +
                '</div>' +
            '</button>';
        }).join("");
        list.querySelectorAll(".wf-cli-ticket-row").forEach(function (btn) {
            btn.addEventListener("click", function () {
                selectedWorkflowCliTicketId = btn.dataset.ticketId || "";
                renderWorkflowCliTab();
                loadWorkflowCliTicketActivity(selectedWorkflowCliTicketId);
            });
        });
        var selected = workflowCliTicketById(selectedWorkflowCliTicketId);
        if (!selected) return;
        var selectedProjectName = selected.linked_project_name || projectNameById(selected.linked_project_id);
        var selectedRun = activeRunByTicketId(selected.id);
        title.textContent = selected.title || ("Ticket #" + selected.id);
        meta.textContent = (selectedProjectName ? ("Project: " + selectedProjectName) : "No linked project") +
            " / Board: " + (selected.board_name || "No board") +
            (selectedRun ? (" / Active run #" + (selectedRun.id || "")) : "");
    }

    function loadWorkflowCliTicketActivity(ticketId) {
        var output = document.getElementById("wf-cli-output");
        if (!output || !ticketId) return;
        output.innerHTML = '<div class="text-gray-500">Loading CLI trail...</div>';
        Promise.all([
            api("GET", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/execution-sessions").catch(function (e) { return { sessions: [], error: e.message }; }),
            api("GET", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/audit-entries").catch(function (e) { return { entries: [], error: e.message }; })
        ]).then(function (results) {
            var sessions = (results[0] && results[0].sessions) || [];
            var entries = (results[1] && results[1].entries) || [];
            var html = "";
            if (sessions.length) {
                html += '<div class="rounded border border-white/10 bg-white/5 p-3">' +
                    '<div class="text-[11px] uppercase tracking-wide text-gray-500 mb-2">CLI / IDE step sessions</div>' +
                    sessions.slice(0, 8).map(function (session) {
                        var status = session.status || session.state || "session";
                        var label = session.execution_session_id || session.session_id || session.id || "";
                        var timing = session.started_at ? ("Started " + session.started_at) : "";
                        if (session.completed_at) timing += (timing ? " / " : "") + "Finished " + session.completed_at;
                        if (session.duration_seconds != null) timing += (timing ? " / " : "") + formatElapsed(session.duration_seconds);
                        else if (session.elapsed_seconds) timing += (timing ? " / " : "") + formatElapsed(session.elapsed_seconds);
                        var route = [session.backend_id || session.route_backend || session.engine || "", session.model || session.selected_model || ""].filter(Boolean).join(" / ");
                        return '<div class="py-1 border-t border-white/5 first:border-t-0">' +
                            '<div class="text-gray-200">' + esc(status) + (label ? " / " + esc(label) : "") + '</div>' +
                            '<div class="text-gray-500">' + esc(route || session.created_at || "") + '</div>' +
                            (timing ? '<div class="text-gray-500">' + esc(timing) + '</div>' : '') +
                            (session.error ? '<div class="mt-1 text-red-300 whitespace-pre-wrap">' + esc(session.error) + '</div>' : '') +
                        '</div>';
                    }).join("") +
                '</div>';
            }
            if (entries.length) {
                html += '<div class="rounded border border-white/10 bg-white/5 p-3">' +
                    '<div class="text-[11px] uppercase tracking-wide text-gray-500 mb-2">Ticket execution trail</div>' +
                    entries.slice(0, 12).map(function (entry) {
                        return '<div class="py-2 border-t border-white/5 first:border-t-0">' +
                            '<div class="text-gray-200 whitespace-pre-wrap">' + esc(entry.summary || entry.status || "Audit entry") + '</div>' +
                            '<div class="text-gray-500">' + esc(entry.created_date || "") + (entry.execution_lane ? " / " + esc(entry.execution_lane) : "") + '</div>' +
                            (entry.details ? '<div class="mt-1 text-gray-400 whitespace-pre-wrap">' + esc(entry.details) + '</div>' : '') +
                        '</div>';
                    }).join("") +
                '</div>';
            }
            output.innerHTML = html || '<div class="text-gray-500">No CLI or IDE trail yet. It will appear here when a workflow step executes this ticket.</div>';
        });
    }


    function workflowRunPreviewRouteHtml(ticket, ctx) {
        ticket = ticket || {};
        ctx = ctx || {};
        var projectName = ctx.project_name || ticket.linked_project_name || projectNameById(ticket.linked_project_id) || "";
        var projectFolder = ctx.project_folder || "";
        var backend = ctx.backend_id || (ticket.cli_route && (ticket.cli_route.backend || ticket.cli_route.backend_id)) || "auto";
        var model = ctx.model || (ticket.cli_route && ticket.cli_route.model) || "auto";
        var complexity = ctx.complexity || ticket.complexity || "medium";
        var rows = [
            ["Ticket", ticket.title || ctx.title || ("Ticket #" + (ticket.id || ctx.ticket_id || ""))],
            ["Board", ticket.board_name || "Unknown board"],
            ["Project", projectName || "No linked project"],
            ["Complexity", complexity],
            ["Executor", backend],
            ["Model", model || "auto"]
        ];
        if (projectFolder) rows.push(["Folder", projectFolder]);
        return '<div class="space-y-2">' + rows.map(function (row) {
            return '<div class="grid grid-cols-[7rem,1fr] gap-3 text-sm">' +
                '<div class="text-gray-500">' + esc(row[0]) + '</div>' +
                '<div class="text-gray-100 break-words">' + esc(row[1] || "-") + '</div>' +
            '</div>';
        }).join("") + '</div>';
    }

    function closeWorkflowRunPreview() {
        pendingWorkflowRunTicketId = null;
        var modal = document.getElementById("wf-run-preview-modal");
        if (modal) modal.classList.add("hidden");
    }

    function openWorkflowRunPreview(ticketId) {
        if (!currentWorkflowId || !ticketId) return;
        var ticket = workflowCliTicketById(ticketId) || workflowQueueTickets.filter(function (item) {
            return String(item.id) === String(ticketId);
        })[0];
        if (activeRunByTicketId(ticketId)) {
            snack("This ticket already has an active workflow run", "error");
            return;
        }
        pendingWorkflowRunTicketId = String(ticketId);
        var modal = document.getElementById("wf-run-preview-modal");
        var subtitle = document.getElementById("wf-run-preview-subtitle");
        var body = document.getElementById("wf-run-preview-body");
        var warning = document.getElementById("wf-run-preview-warning");
        var confirmBtn = document.getElementById("wf-run-preview-confirm");
        if (!modal || !body || !confirmBtn) {
            startWorkflowTicketRun(ticketId);
            return;
        }
        if (subtitle) subtitle.textContent = ticket ? (ticket.title || ("Ticket #" + ticketId)) : ("Ticket #" + ticketId);
        body.innerHTML = workflowRunPreviewRouteHtml(ticket, {});
        if (warning) {
            warning.textContent = "Checking project and executor route...";
            warning.classList.remove("hidden");
        }
        confirmBtn.disabled = true;
        modal.classList.remove("hidden");
        api("GET", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/cli-context")
            .then(function (ctx) {
                if (pendingWorkflowRunTicketId !== String(ticketId)) return;
                body.innerHTML = workflowRunPreviewRouteHtml(ticket, ctx);
                if (warning) {
                    warning.textContent = "This will start the workflow run. The agent will execute, validate, and report back through the run log.";
                    warning.classList.remove("hidden");
                }
                confirmBtn.disabled = false;
            })
            .catch(function (e) {
                if (pendingWorkflowRunTicketId !== String(ticketId)) return;
                body.innerHTML = workflowRunPreviewRouteHtml(ticket, {});
                if (warning) {
                    warning.textContent = e.message || "This ticket does not have a complete project/executor route yet. Link the board or ticket to a project before running.";
                    warning.classList.remove("hidden");
                }
                confirmBtn.disabled = true;
            });
    }

    function confirmWorkflowRunPreview() {
        var ticketId = pendingWorkflowRunTicketId;
        closeWorkflowRunPreview();
        if (ticketId) startWorkflowTicketRun(ticketId);
    }

    function startWorkflowTicketRun(ticketId) {
        if (!currentWorkflowId || !ticketId) return;
        var ticket = workflowCliTicketById(ticketId) || workflowQueueTickets.filter(function (item) {
            return String(item.id) === String(ticketId);
        })[0];
        if (activeRunByTicketId(ticketId)) {
            snack("This ticket already has an active workflow run", "error");
            return;
        }
        var buttons = document.querySelectorAll('.wf-workflow-ticket-run[data-ticket-id="' + esc(ticketId) + '"]');
        buttons.forEach(function (btn) { btn.disabled = true; btn.textContent = "Starting"; });
        api("POST", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/send-to-workflow", {
            workflow_id: parseInt(currentWorkflowId, 10)
        }).then(function (data) {
            snack(workflowFeedbackText(data, "Ticket run started"));
            if (ticket && ticket.id) {
                selectedWorkflowCliTicketId = String(ticket.id);
            }
            startPolling();
            switchTab("runs");
            loadDetail(currentWorkflowId);
            loadWorkflowTicketQueue();
            loadActiveRuns();
            loadWorkflowExecutionSessions();
            refreshBoardHermesViews({ quiet: true });
        }).catch(function (e) {
            snack(workflowErrorText(e, "Failed to start ticket run"), "error");
            buttons.forEach(function (btn) { btn.disabled = false; btn.textContent = "Run"; });
        });
    }

    function assignTicketToCurrentWorkflow(ticketId, payload) {
        if (!currentWorkflowId || !ticketId) return;
        var alreadyQueued = workflowQueueTickets.some(function (ticket) { return String(ticket.id) === String(ticketId); });
        if (alreadyQueued) {
            snack("Ticket is already in this workflow queue");
            return;
        }
        var linkKey = "database:" + String(ticketId);
        if (workflowPendingTicketLinks[linkKey]) {
            snack("Ticket is already being linked");
            return;
        }
        workflowPendingTicketLinks[linkKey] = true;
        markWorkflowBoardTicketPending(payload);
        api("PUT", "/kanban/tickets/" + encodeURIComponent(ticketId), {
            linked_workflow_id: parseInt(currentWorkflowId, 10),
            workflow_queue_position: workflowQueueTickets.length
        }).then(function () {
            delete workflowPendingTicketLinks[linkKey];
            snack("Ticket added to workflow queue");
            loadWorkflowTicketQueue();
            loadActiveRuns();
            var selectedBoard = document.getElementById("wf-board-select");
            if (selectedBoard && selectedBoard.value) loadWorkflowBoardTickets(selectedBoard.value);
        }).catch(function (e) {
            snack(e.message || "Failed to add ticket to workflow", "error");
            delete workflowPendingTicketLinks[linkKey];
        });
    }

    function copyExternalTicketToCurrentWorkflow(payload) {
        if (!currentWorkflowId || !payload) return;
        var destinationBoard = payload.destination_board_id
            ? null
            : workflowDatabaseBoardForProject(payload.default_project_id);
        var boardId = payload.destination_board_id || (destinationBoard && destinationBoard.id) || payload.local_board_id || payload.board_id;
        if (!boardId) {
            snack("Link this Jira/Trello board's project to a local board before adding tickets to a workflow", "error");
            return;
        }
        var linkKey = workflowPayloadLinkKey(payload);
        if (linkKey && (workflowPendingTicketLinks[linkKey] || workflowLinkedExternalTicketKeys[linkKey])) {
            snack("Ticket is already linked to a workflow");
            return;
        }
        if (linkKey) workflowPendingTicketLinks[linkKey] = true;
        markWorkflowBoardTicketPending(payload);
        api("POST", "/kanban/tickets/copy-external-to-board", {
            board_id: parseInt(boardId, 10),
            title: payload.title || "Untitled ticket",
            description: payload.description || "",
            priority: payload.priority || "medium",
            complexity: payload.complexity || "",
            external_source: payload.source || payload.external_source || "",
            external_id: payload.external_id || payload.ticket_id || "",
            external_url: payload.external_url || payload.url || "",
            linked_project_id: payload.default_project_id || null,
            linked_workflow_id: parseInt(currentWorkflowId, 10)
        }).then(function () {
            if (linkKey) workflowLinkedExternalTicketKeys[linkKey] = true;
            if (linkKey) delete workflowPendingTicketLinks[linkKey];
            snack("Ticket copied into workflow queue");
            loadWorkflowTicketQueue();
            loadActiveRuns();
            var selectedBoard = document.getElementById("wf-board-select");
            if (selectedBoard && selectedBoard.value) loadWorkflowBoardTickets(selectedBoard.value);
        }).catch(function (e) {
            snack(e.message || "Failed to copy ticket into workflow", "error");
            if (linkKey) delete workflowPendingTicketLinks[linkKey];
        });
    }

    function handleWorkflowTicketDropPayload(payloadOrId) {
        if (!payloadOrId) return;
        if (typeof payloadOrId === "string" || typeof payloadOrId === "number") {
            assignTicketToCurrentWorkflow(payloadOrId);
            return;
        }
        if (payloadOrId.type === "workflow-board-ticket") {
            if (!payloadOrId.default_project_id) {
                snack("Link this board to a project before adding tickets to a workflow", "error");
                return;
            }
            var linkKey = workflowPayloadLinkKey(payloadOrId);
            if (linkKey && (workflowPendingTicketLinks[linkKey] || workflowLinkedExternalTicketKeys[linkKey])) {
                snack("Ticket is already linked to a workflow");
                return;
            }
            if (payloadOrId.source && payloadOrId.source !== "database") {
                copyExternalTicketToCurrentWorkflow(payloadOrId);
            } else if (payloadOrId.ticket_id) {
                assignTicketToCurrentWorkflow(payloadOrId.ticket_id, payloadOrId);
            }
        }
    }

    function persistWorkflowTicketQueueOrder() {
        if (!currentWorkflowId) return;
        var ids = workflowQueueTickets.map(function (t) { return t.id; }).filter(Boolean);
        api("PUT", "/kanban/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets/reorder", { ticket_ids: ids })
            .then(function () { loadWorkflowTicketQueue(); })
            .catch(function (e) { snack(e.message || "Failed to save ticket order", "error"); });
    }

    function moveWorkflowQueueTicket(dragId, targetId) {
        if (!dragId || !targetId || String(dragId) === String(targetId)) return;
        var from = -1;
        var to = -1;
        workflowQueueTickets.forEach(function (ticket, idx) {
            if (String(ticket.id) === String(dragId)) from = idx;
            if (String(ticket.id) === String(targetId)) to = idx;
        });
        if (from < 0 || to < 0) return;
        var item = workflowQueueTickets.splice(from, 1)[0];
        workflowQueueTickets.splice(to, 0, item);
        workflowQueueTickets.forEach(function (ticket, idx) { ticket.workflow_queue_position = idx; });
        renderWorkflowTickets(workflowQueueTickets);
        persistWorkflowTicketQueueOrder();
    }

    function workflowTicketPayloadFromDrop(evt) {
        if (workflowBoardDragPayload) return workflowBoardDragPayload;
        if (!evt.dataTransfer) return null;
        var raw = evt.dataTransfer.getData("application/json") || "";
        if (raw) {
            try {
                var payload = JSON.parse(raw);
                if (payload && payload.type === "workflow-board-ticket" && payload.ticket_id) {
                    return payload;
                }
            } catch (e) {}
        }
        return evt.dataTransfer.getData("text/plain") || null;
    }

    function markWorkflowBoardTicketPending(payload) {
        if (!payload || !payload.ticket_key) return;
        var key = String(payload.ticket_key);
        var escapedKey = window.CSS && CSS.escape ? CSS.escape(key) : key.replace(/"/g, '\\"');
        var row = document.querySelector('.wf-board-ticket-row[data-ticket-key="' + escapedKey + '"]');
        if (!row) return;
        row.setAttribute("draggable", "false");
        row.className = workflowBoardTicketRowClasses(false, false, false, false, true);
        var badgeWrap = row.querySelector(".flex.items-start.justify-between");
        if (badgeWrap && badgeWrap.children.length > 1) {
            badgeWrap.children[1].outerHTML = '<span class="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/50 bg-amber-500/20 text-amber-200 flex-shrink-0">linking</span>';
        }
    }

    function workflowDropZoneContainsPoint(evt) {
        var list = document.getElementById("wf-workflow-tickets-list");
        var tab = document.getElementById("wf-tab-tickets");
        var zone = list || tab;
        if (!zone || (tab && tab.classList.contains("hidden"))) return false;
        var rect = zone.getBoundingClientRect();
        return evt.clientX >= rect.left && evt.clientX <= rect.right && evt.clientY >= rect.top && evt.clientY <= rect.bottom;
    }

    function rememberWorkflowDragPoint(evt) {
        if (!evt) return;
        workflowLastDragPoint = { clientX: evt.clientX, clientY: evt.clientY };
    }

    function bindWorkflowTicketQueueRows(listEl) {
        listEl.querySelectorAll(".wf-workflow-ticket-row").forEach(function (row) {
            row.addEventListener("dragstart", function (evt) {
                if (row.getAttribute("draggable") === "false") {
                    evt.preventDefault();
                    return;
                }
                workflowQueueDragTicketId = row.dataset.ticketId || "";
                evt.dataTransfer.effectAllowed = "move";
                evt.dataTransfer.setData("text/plain", workflowQueueDragTicketId);
            });
            row.addEventListener("dragend", function () {
                workflowQueueDragTicketId = null;
                listEl.querySelectorAll(".wf-workflow-ticket-row").forEach(function (r) {
                    r.classList.remove("ring-1", "ring-[#f97316]/50");
                });
            });
            row.addEventListener("dragover", function (evt) {
                evt.preventDefault();
                row.classList.add("ring-1", "ring-[#f97316]/50");
            });
            row.addEventListener("dragleave", function () {
                row.classList.remove("ring-1", "ring-[#f97316]/50");
            });
            row.addEventListener("drop", function (evt) {
                evt.preventDefault();
                evt.stopPropagation();
                row.classList.remove("ring-1", "ring-[#f97316]/50");
                if (workflowQueueDragTicketId) {
                    moveWorkflowQueueTicket(workflowQueueDragTicketId, row.dataset.ticketId || "");
                } else {
                    handleWorkflowTicketDropPayload(workflowTicketPayloadFromDrop(evt));
                }
            });
            var runBtn = row.querySelector(".wf-workflow-ticket-run");
            if (runBtn) {
                runBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    openWorkflowRunPreview(runBtn.dataset.ticketId || "");
                });
            }
            var removeBtn = row.querySelector(".wf-workflow-ticket-remove");
            if (removeBtn) {
                removeBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    var ticketId = removeBtn.dataset.ticketId || "";
                    if (!ticketId || !currentWorkflowId) return;
                    api("DELETE", "/kanban/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets/" + encodeURIComponent(ticketId))
                        .then(function () {
                            snack("Ticket removed from workflow queue");
                            loadWorkflowTicketQueue();
                            var selectedBoard = document.getElementById("wf-board-select");
                            if (selectedBoard && selectedBoard.value) loadWorkflowBoardTickets(selectedBoard.value);
                        })
                        .catch(function (e) { snack(e.message || "Failed to remove ticket", "error"); });
                });
            }
            row.addEventListener("dblclick", function () {
                var id = row.dataset.ticketId || "";
                if (!id) return;
                api("GET", "/kanban/tickets/" + encodeURIComponent(id))
                    .then(function (ticket) {
                        openWorkflowTicketModal(ticket, { selected: { source: "database", value: "", label: ticket.board_name || "Workflow queue" } });
                    })
                    .catch(function (e) { snack(e.message || "Failed to open ticket", "error"); });
            });
        });
    }

    function bindWorkflowTicketDropZone() {
        var tab = document.getElementById("wf-tab-tickets");
        var list = document.getElementById("wf-workflow-tickets-list");
        var empty = document.getElementById("wf-workflow-tickets-empty");
        var targets = [tab, list, empty].filter(Boolean);
        if (!targets.length) return;

        function dragHasTicket(evt) {
            if (workflowQueueDragTicketId || workflowBoardDragPayload) return true;
            if (!evt.dataTransfer) return false;
            var types = Array.prototype.slice.call(evt.dataTransfer.types || []);
            return types.indexOf("application/json") !== -1 || types.indexOf("text/plain") !== -1;
        }

        function setDropActive(active) {
            [tab, list].filter(Boolean).forEach(function (el) {
                el.classList.toggle("ring-1", active);
                el.classList.toggle("ring-[#f97316]/50", active);
                el.classList.toggle("bg-[#f97316]/5", active);
                el.classList.toggle("rounded", active);
            });
        }

        targets.forEach(function (target) {
            if (target.dataset.workflowDropBound === "1") return;
            target.dataset.workflowDropBound = "1";
            target.addEventListener("dragenter", function (evt) {
                if (!dragHasTicket(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                setDropActive(true);
            }, true);
            target.addEventListener("dragover", function (evt) {
                if (!dragHasTicket(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
                setDropActive(true);
            }, true);
            target.addEventListener("dragleave", function (evt) {
                if (tab && tab.contains(evt.relatedTarget)) return;
                setDropActive(false);
            }, true);
            target.addEventListener("drop", function (evt) {
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                evt.stopPropagation();
                setDropActive(false);
                if (workflowQueueDragTicketId) return;
                handleWorkflowTicketDropPayload(workflowTicketPayloadFromDrop(evt));
                workflowBoardDragPayload = null;
                workflowLastDragPoint = null;
            }, true);
        });

        if (!workflowDropDocumentBound) {
            workflowDropDocumentBound = true;
            document.addEventListener("dragover", function (evt) {
                if (!dragHasTicket(evt) || !workflowDropZoneContainsPoint(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
                setDropActive(true);
            }, true);
            document.addEventListener("drop", function (evt) {
                if (!dragHasTicket(evt) || !workflowDropZoneContainsPoint(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                evt.stopPropagation();
                setDropActive(false);
                if (workflowQueueDragTicketId) return;
                handleWorkflowTicketDropPayload(workflowTicketPayloadFromDrop(evt));
                workflowBoardDragPayload = null;
                workflowLastDragPoint = null;
            }, true);
            document.addEventListener("dragend", function () {
                setDropActive(false);
            }, true);
        }
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

    function isStepEditorInteractionActive() {
        if (!expandedStepId) return false;
        var active = document.activeElement;
        if (!active) return false;
        var body = document.getElementById("step-body-" + expandedStepId);
        return !!(body && body.contains(active));
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
                if (runBar) runBar.innerHTML = "";
                startPolling();
            } else {
                if (runBar) runBar.innerHTML = "";
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
        if (!el) return;
        if (!steps.length) {
            el.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">No steps yet. Click "+ Add Step" to begin.</p>';
            return;
        }
        el.innerHTML = steps.map(function (s) {
            var isOpen = expandedStepId === s.id;
            var typeLabel = { agent_instruction: "Agent", computer_use: "Computer Use", decision_action: "Action", play_recording: "Recording", run_command: "Command", send_to_project_cli: "Project CLI", http_request: "HTTP", execute_code: "Code", playwright: "Playwright" }[s.action_type] || s.action_type;
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
        var stepConfig = normalizeStepConfig(step);

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
            '<option value="decision_action"' + (step.action_type === "decision_action" ? " selected" : "") + '>Decisions Action</option>' +
            '<option value="play_recording"' + (step.action_type === "play_recording" ? " selected" : "") + '>Play Recording</option>' +
            '<option value="run_command"' + (step.action_type === "run_command" ? " selected" : "") + '>Run Command</option>' +
            '<option value="send_to_project_cli"' + (step.action_type === "send_to_project_cli" ? " selected" : "") + '>Send to Project CLI</option>' +
            '<option value="http_request"' + (step.action_type === "http_request" ? " selected" : "") + '>HTTP Request</option>' +
            '<option value="execute_code"' + (step.action_type === "execute_code" ? " selected" : "") + '>Execute Code</option>' +
            '<option value="playwright"' + (step.action_type === "playwright" ? " selected" : "") + '>Playwright</option>' +
            '</select></div>';
        html += '</div>';
        var isRecording = step.action_type === "play_recording";
        var isDecisionAction = step.action_type === "decision_action";
        var isCodeType = step.action_type === "execute_code" || step.action_type === "playwright";
        // Instruction (hidden for play_recording)
        html += '<div class="sf-instruction-wrap' + ((isRecording || isDecisionAction) ? " hidden" : "") + '">' +
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
        // Decisions Action controls
        html += '<div class="sf-decision-action-wrap' + (!isDecisionAction ? " hidden" : "") + '">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Description</label>' +
            '<textarea class="sf-desc-action w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm h-12 resize-none" placeholder="Brief description of why this workflow should run the saved action...">' + esc(step.description) + '</textarea></div>';
        html += '<div class="mt-2"><label class="block text-xs text-gray-500 mb-1">Saved Action</label>' +
            '<select class="sf-decision-action w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm"><option value="">Loading Actions...</option></select>' +
            '<p class="sf-decision-action-summary text-xs text-gray-500 mt-2">Pick one saved action from Decisions. The workflow engine will run it as a step and audit the result.</p></div>';
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
        html += '<div class="mt-3 border-t border-white/10 pt-3">';
        html += '<label class="flex items-center gap-2 cursor-pointer">' +
            '<input type="checkbox" class="sf-ui-capture rounded"' + ((stepConfig.ui_quality_capture || stepConfig.capture_ui_evidence) ? " checked" : "") + '>' +
            '<span class="text-sm text-gray-300">Capture UI evidence and compare with visual baseline</span></label>';
        html += '<div class="grid grid-cols-3 gap-3 mt-2">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Visual Baseline</label>' +
            '<input type="text" class="sf-visual-baseline-name w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + esc(stepConfig.visual_baseline_name || stepConfig.baseline_name || "") + '" placeholder="Gold Admin"></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Baseline Screen</label>' +
            '<input type="text" class="sf-baseline-screen-name w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + esc(stepConfig.baseline_screen_name || stepConfig.visual_baseline_screen || "") + '" placeholder="Dashboard"></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Diff Threshold</label>' +
            '<input type="number" min="0" max="1" step="0.01" class="sf-visual-diff-threshold w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm" value="' + esc(stepConfig.visual_diff_threshold == null ? "" : stepConfig.visual_diff_threshold) + '" placeholder="0.10"></div>';
        html += '</div></div>';
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
        hydrateActionSelect(container, step);

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
                var isDecisionAction = actionTypeSelect.value === "decision_action";
                container.querySelector(".sf-instruction-wrap").classList.toggle("hidden", isRec || isDecisionAction);
                container.querySelector(".sf-recording-wrap").classList.toggle("hidden", !isRec);
                container.querySelector(".sf-decision-action-wrap").classList.toggle("hidden", !isDecisionAction);
                if (isDecisionAction) hydrateActionSelect(container, step);
            });
        }
        var decisionActionSelect = container.querySelector(".sf-decision-action");
        if (decisionActionSelect) {
            decisionActionSelect.addEventListener("change", function () {
                updateDecisionActionSummary(container);
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
        container.querySelector(".sf-save").addEventListener("click", function () { saveStep(step.id, container, step); });
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
                showConfirmModal({
                    title: "Clear step history",
                    message: "Clear this step's audit history? This cannot be undone.",
                    confirmLabel: "Clear",
                    onConfirm: function () {
                        api("DELETE", "/workflows/" + currentWorkflowId + "/steps/" + step.id + "/results")
                            .then(function () {
                                snack("Step audit history cleared");
                                var histContainer = container.querySelector(".sf-history-tab-list");
                                if (histContainer) loadStepHistory(step.id, histContainer);
                                var resultWrap = container.querySelector(".sf-result-wrap");
                                if (resultWrap) resultWrap.classList.add("hidden");
                            })
                            .catch(function (e) { snack(e.message || "Failed to clear history", "error"); });
                    },
                });
            });
        }
    }

    function saveStep(stepId, container, step) {
        var passVal = container.querySelector(".sf-pass").value;
        var failVal = container.querySelector(".sf-fail").value;
        var routingMode = container.querySelector(".sf-routing-mode") ? container.querySelector(".sf-routing-mode").value : "static";
        var routingPrompt = container.querySelector(".sf-routing-prompt") ? container.querySelector(".sf-routing-prompt").value : "";
        var actionType = container.querySelector(".sf-action-type").value;
        var isRec = actionType === "play_recording";
        var isDecisionAction = actionType === "decision_action";
        var isCodeType = actionType === "execute_code" || actionType === "playwright";
        var descEl = isDecisionAction ? container.querySelector(".sf-desc-action") : (isRec ? container.querySelector(".sf-desc-rec") : container.querySelector(".sf-desc"));
        var body = {
            name: container.querySelector(".sf-name").value.trim() || "Untitled",
            description: descEl ? descEl.value.trim() : "",
            action_type: actionType,
            instruction: (isRec || isDecisionAction) ? "" : container.querySelector(".sf-instruction").value,
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
        var config = normalizeStepConfig(step);
        var uiCaptureEl = container.querySelector(".sf-ui-capture");
        var baselineNameEl = container.querySelector(".sf-visual-baseline-name");
        var baselineScreenEl = container.querySelector(".sf-baseline-screen-name");
        var thresholdEl = container.querySelector(".sf-visual-diff-threshold");
        config.ui_quality_capture = uiCaptureEl ? uiCaptureEl.checked : false;
        config.visual_baseline_name = baselineNameEl ? baselineNameEl.value.trim() : "";
        config.baseline_screen_name = baselineScreenEl ? baselineScreenEl.value.trim() : "";
        if (thresholdEl && thresholdEl.value !== "") {
            config.visual_diff_threshold = parseFloat(thresholdEl.value);
        } else {
            delete config.visual_diff_threshold;
        }
        body.config = config;
        if (isDecisionAction) {
            var actionSelect = container.querySelector(".sf-decision-action");
            body.action_id = actionSelect && actionSelect.value ? parseInt(actionSelect.value, 10) : null;
        }
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

    function continueWorkflowRun(workflowId, runId, options) {
        options = options || {};
        var payload = {};
        if (options.input) payload.input = options.input;
        return api("POST", "/workflows/" + encodeURIComponent(workflowId) + "/runs/" + encodeURIComponent(runId) + "/continue", payload);
    }

    function continueWaitingRun() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId + "/active-run").then(function (data) {
            if (data && data.id) {
                var waitingKind = data.waiting_kind || "";
                function submitContinue(feedback) {
                    continueWorkflowRun(currentWorkflowId, data.id, { input: feedback || "" })
                        .then(function (resp) {
                            snack(workflowFeedbackText(resp, "Run continued"));
                            startPolling();
                            loadDetail(currentWorkflowId);
                            refreshBoardHermesViews({ quiet: true });
                        })
                        .catch(function (e) { snack(workflowErrorText(e, "Failed to continue"), "error"); });
                }
                if (waitingKind === "ide_handoff") {
                    openIdeHandoffModal(submitContinue);
                    return;
                }
                submitContinue("");
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

    // ── Runs tab ──
    function renderControlOverview(data) {
        var el = document.getElementById("wf-control-overview");
        if (!el) return;
        data = data || {};
        var steps = Array.isArray(data.steps) ? data.steps : [];
        var runs = Array.isArray(data.runs) ? data.runs : [];
        var latest = runs.length ? runs[0] : null;
        var waitingSteps = steps.filter(function (s) { return !!s.wait_for_continue; }).length;
        var approvalSteps = steps.filter(function (s) { return !!s.require_approval; }).length;
        var validationSteps = steps.filter(function (s) {
            var vt = (s.validation_type || "").trim();
            return vt && vt !== "none";
        }).length;
        var latestStatus = latest ? latest.status : "not run";
        var latestClass = {
            running: "text-blue-300 border-blue-500/30 bg-blue-500/10",
            waiting: "text-amber-300 border-amber-500/30 bg-amber-500/10",
            completed: "text-green-300 border-green-500/30 bg-green-500/10",
            failed: "text-red-300 border-red-500/30 bg-red-500/10",
            cancelled: "text-gray-300 border-white/15 bg-white/5"
        }[latestStatus] || "text-gray-300 border-white/15 bg-white/5";

        el.innerHTML = '' +
            '<div class="flex flex-wrap items-center gap-2 text-xs">' +
                '<span class="inline-flex items-center gap-1.5 rounded border border-white/10 bg-[#152054]/50 px-2.5 py-1 text-gray-400">Flow <span class="wf-count-badge">' + steps.length + '</span></span>' +
                '<span class="inline-flex items-center gap-1.5 rounded border border-white/10 bg-[#152054]/50 px-2.5 py-1 text-gray-400">Checks <span class="wf-count-badge">' + validationSteps + '</span></span>' +
                '<span class="inline-flex items-center gap-1.5 rounded border border-white/10 bg-[#152054]/50 px-2.5 py-1 text-gray-400">Pauses <span class="wf-count-badge">' + waitingSteps + '</span></span>' +
                '<span class="inline-flex items-center gap-1.5 rounded border border-white/10 bg-[#152054]/50 px-2.5 py-1 text-gray-400">Approvals <span class="wf-count-badge">' + approvalSteps + '</span></span>' +
                '<span class="inline-flex items-center gap-1.5 rounded border px-2.5 py-1 ' + latestClass + '">Latest <strong>' + esc(latestStatus) + '</strong></span>' +
            '</div>';

    }

    function renderRuns(runs) {
        var el = document.getElementById("wf-runs-list");
        var empty = document.getElementById("wf-runs-empty");
        var clearBtn = document.getElementById("wf-clear-runs-btn");
        runs = (Array.isArray(runs) ? runs : []).filter(function (run) {
            var status = String(run && run.status || "").toLowerCase();
            return status !== "running" && status !== "waiting";
        });
        if (clearBtn) clearBtn.disabled = !runs.length;
        if (!runs.length) { el.innerHTML = ""; empty.classList.remove("hidden"); return; }
        empty.classList.add("hidden");
        el.innerHTML = runs.map(function (r) {
            var statusColor = { running: "text-blue-400", completed: "text-green-400", failed: "text-red-400", cancelled: "text-gray-400", waiting: "text-amber-400" }[r.status] || "text-gray-400";
            var started = r.started_at ? new Date(r.started_at).toLocaleString() : "—";
            var ended = r.completed_at ? new Date(r.completed_at).toLocaleString() : "—";
            var meta = runMetaText(r, currentWorkflow && currentWorkflow.name);
            return '<div class="wf-run-item bg-[#152054]/50 rounded px-3 py-2 border border-white/10" data-run-id="' + r.id + '">' +
                '<div class="flex items-center gap-3">' +
                    '<span class="text-xs text-gray-500">#' + r.id + '</span>' +
                    '<span class="text-xs ' + statusColor + ' font-medium">' + esc(r.status) + '</span>' +
                    '<span class="text-xs px-1.5 py-0.5 rounded bg-white/10 text-gray-300">' + esc(meta.sourceText) + '</span>' +
                    '<span class="text-xs text-gray-500 ml-auto">' + started + '</span>' +
                    '<span class="text-xs text-gray-600">→</span>' +
                    '<span class="text-xs text-gray-500">' + ended + '</span>' +
                '</div>' +
                '<div class="mt-2 grid grid-cols-1 md:grid-cols-4 gap-1 text-xs">' +
                    '<div><span class="text-gray-500">Board:</span> <span class="text-gray-200">' + esc(meta.boardText) + '</span></div>' +
                    '<div><span class="text-gray-500">Ticket:</span> <span class="text-gray-200">' + esc(meta.ticketText) + '</span></div>' +
                    '<div><span class="text-gray-500">Project:</span> <span class="text-gray-200">' + esc(meta.projectText) + '</span></div>' +
                    '<div><span class="text-gray-500">Workflow:</span> <span class="text-gray-200">' + esc(meta.workflowText || (currentWorkflow && currentWorkflow.name) || "") + '</span></div>' +
                '</div>' +
                renderRunPacketEvidence(r.result_packet, r) +
            '</div>';
        }).join('');
    }

    function loadWorkflowRunHistory(options) {
        options = options || {};
        if (!currentWorkflowId) {
            renderRuns([]);
            return Promise.resolve([]);
        }
        return api("GET", "/workflows/" + currentWorkflowId + "/runs?limit=20")
            .then(function (runs) {
                renderRuns(runs || []);
                return runs || [];
            })
            .catch(function (e) {
                if (!options.quiet) snack(e.message || "Failed to load run history", "error");
                return [];
            });
    }

    function renderCorrections(corrections) {
        var list = document.getElementById("wf-corrections-list");
        var empty = document.getElementById("wf-corrections-empty");
        if (!list || !empty) return;
        corrections = Array.isArray(corrections) ? corrections : [];
        if (!corrections.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            return;
        }
        empty.classList.add("hidden");
        list.innerHTML = corrections.map(function (attempt) {
            var status = attempt.status || "queued";
            var dispatch = attempt.dispatch_result || {};
            var autoLabel = dispatch.auto_dispatch ? "Auto-dispatched" : "Queued";
            var statusClass = status === "dispatched" ? "text-blue-300 border-blue-500/30 bg-blue-500/10" :
                status === "completed" ? "text-green-300 border-green-500/30 bg-green-500/10" :
                "text-amber-300 border-amber-500/30 bg-amber-500/10";
            var packet = attempt.correction_packet || {};
            var failed = packet.failed_validation || {};
            var expected = failed.expected || "";
            var observed = failed.observed || "";
            return '' +
                '<div class="rounded border border-white/10 bg-[#152054]/50 px-3 py-2" data-correction-id="' + esc(attempt.id) + '">' +
                    '<div class="flex flex-wrap items-center gap-2">' +
                        '<span class="text-xs text-gray-500">#' + esc(attempt.id) + '</span>' +
                        '<span class="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] ' + statusClass + '">' + esc(status) + '</span>' +
                        '<span class="text-xs text-gray-300">' + esc(autoLabel) + '</span>' +
                        (attempt.run_id ? '<span class="text-xs text-gray-500">Run #' + esc(attempt.run_id) + '</span>' : '') +
                        (attempt.step_id ? '<span class="text-xs text-gray-500">Step #' + esc(attempt.step_id) + '</span>' : '') +
                        (attempt.attempt_number ? '<span class="text-xs text-gray-500">Attempt ' + esc(attempt.attempt_number) + '</span>' : '') +
                    '</div>' +
                    '<div class="mt-1 text-[11px] text-gray-400">' +
                        '<span class="text-gray-500">' + esc(failed.validation_type || "validation") + '</span>' +
                        (expected ? ' Expected: ' + esc(expected) : '') +
                        (observed ? ' Observed: ' + esc(observed) : '') +
                    '</div>' +
                '</div>';
        }).join("");
    }

    function loadWorkflowCorrections(options) {
        options = options || {};
        if (!currentWorkflowId) {
            renderCorrections([]);
            return Promise.resolve([]);
        }
        var filter = document.getElementById("wf-corrections-status-filter");
        var status = filter && filter.value ? filter.value : "";
        var endpoint = "/workflows/" + currentWorkflowId + "/corrections";
        var query = endpoint + "?limit=50";
        if (status) query += "&status=" + encodeURIComponent(status);
        return api("GET", query)
            .then(function (items) {
                renderCorrections(items || []);
                return items || [];
            })
            .catch(function (e) {
                if (!options.quiet) snack(e.message || "Failed to load corrections", "error");
                renderCorrections([]);
                return [];
            });
    }

    function switchRunsSubtab(tab) {
        workflowRunsSubtab = tab === "recent" || tab === "sessions" || tab === "timeline" || tab === "corrections" ? tab : "active";
        document.querySelectorAll(".wf-runs-subtab").forEach(function (btn) {
            var active = (btn.dataset.runsTab || "active") === workflowRunsSubtab;
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
            btn.style.borderColor = active ? "#f97316" : "transparent";
        });
        document.querySelectorAll(".wf-runs-pane").forEach(function (pane) {
            pane.classList.add("hidden");
        });
        var pane = document.getElementById("wf-runs-pane-" + workflowRunsSubtab);
        if (pane) pane.classList.remove("hidden");
        if (workflowRunsSubtab === "timeline") loadHermesTimeline({ quiet: true });
        if (workflowRunsSubtab === "corrections") loadWorkflowCorrections({ quiet: true });
        if (workflowRunsSubtab === "recent") loadWorkflowRunHistory({ quiet: true });
    }

    function clearWorkflowRunAudit() {
        if (!currentWorkflowId) return;
        showConfirmModal({
            title: "Clear run history",
            message: "Clear the run history for this workflow?\n\nThis removes completed run records and step result records only. Executor sessions and event logs stay intact.",
            confirmLabel: "Clear",
            onConfirm: function () {
                var btn = document.getElementById("wf-clear-runs-btn");
                if (btn) btn.disabled = true;
                api("DELETE", "/workflows/" + currentWorkflowId + "/runs")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Workflow run history cleared"));
                        stopPolling();
                        loadDetail(currentWorkflowId);
                        loadList();
                        loadActiveRuns();
                    })
                    .catch(function (e) {
                        if (btn) btn.disabled = false;
                        snack(workflowErrorText(e, "Failed to clear workflow history"), "error");
                    });
            },
        });
    }

    function clearWorkflowExecutionSessions() {
        if (!currentWorkflowId) return;
        showConfirmModal({
            title: "Clear executor log",
            message: "Clear the executor log for this workflow?\n\nThis removes CLI/IDE execution sessions only. Run history and event logs stay intact.",
            confirmLabel: "Clear",
            onConfirm: function () {
                var btn = document.getElementById("wf-clear-execution-sessions");
                if (btn) btn.disabled = true;
                api("DELETE", "/workflows/" + currentWorkflowId + "/executor-sessions")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Executor log cleared"));
                        expandedWorkflowExecutionSessionId = null;
                        loadWorkflowExecutionSessions();
                        renderWorkflowCliTab();
                    })
                    .catch(function (e) {
                        if (btn) btn.disabled = false;
                        snack(workflowErrorText(e, "Failed to clear executor log"), "error");
                    });
            },
        });
    }

    function clearWorkflowEvents() {
        if (!currentWorkflowId) return;
        showConfirmModal({
            title: "Clear event stream",
            message: "Clear the event stream for this workflow?\n\nThis removes orchestration events only. Run history and executor logs stay intact.",
            confirmLabel: "Clear",
            onConfirm: function () {
                var btn = document.getElementById("wf-clear-hermes-events");
                if (btn) btn.disabled = true;
                api("DELETE", "/workflows/" + currentWorkflowId + "/events")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Workflow events cleared"));
                        loadHermesTimeline();
                    })
                    .catch(function (e) {
                        if (btn) btn.disabled = false;
                        snack(workflowErrorText(e, "Failed to clear workflow events"), "error");
                    });
            },
        });
    }

    // ── Hermes timeline ──
    function hermesStatusClass(status) {
        status = String(status || "").toLowerCase();
        if (status === "completed" || status === "passed") return "text-green-300 border-green-500/30 bg-green-500/10";
        if (status === "failed" || status === "error") return "text-red-300 border-red-500/30 bg-red-500/10";
        if (status === "waiting" || status === "queued") return "text-amber-300 border-amber-500/30 bg-amber-500/10";
        if (status === "cancelled") return "text-gray-300 border-white/15 bg-white/5";
        return "text-blue-300 border-blue-500/30 bg-blue-500/10";
    }

    function hermesMetaText(event) {
        var parts = [];
        if (event.ticket_id) parts.push("ticket #" + event.ticket_id);
        if (event.run_id) parts.push("run #" + event.run_id);
        if (event.step_id) parts.push("step #" + event.step_id);
        if (event.execution_session_id) parts.push("session #" + event.execution_session_id);
        if (event.project_id) parts.push("project #" + event.project_id);
        return parts.join(" · ");
    }

    function renderHermesTimeline(events) {
        var list = document.getElementById("wf-hermes-events-list");
        var empty = document.getElementById("wf-hermes-events-empty");
        var clearBtn = document.getElementById("wf-clear-hermes-events");
        if (!list || !empty) return;
        events = Array.isArray(events) ? events : [];
        latestHermesEvents = events;
        if (clearBtn) clearBtn.disabled = !events.length;
        if (!events.length) {
            list.innerHTML = "";
            empty.classList.remove("hidden");
            return;
        }
        empty.classList.add("hidden");
        list.innerHTML = events.map(function (event) {
            var when = event.created_at ? new Date(event.created_at).toLocaleString() : "";
            var status = event.status || "event";
            var meta = hermesMetaText(event);
            var payload = event.payload && typeof event.payload === "object" ? event.payload : {};
            var evidence = event.evidence && typeof event.evidence === "object" ? event.evidence : {};
            var detailLines = [];
            if (payload.decision && typeof payload.decision === "object") {
                if (payload.decision.backend) detailLines.push("Route backend: " + payload.decision.backend);
                if (payload.decision.model) detailLines.push("Route model: " + payload.decision.model);
                if (payload.decision.source) detailLines.push("Route source: " + payload.decision.source);
                if (payload.decision.rationale) detailLines.push("Rationale: " + payload.decision.rationale);
            }
            if (payload.override && typeof payload.override === "object" && payload.override.backend) {
                detailLines.push("Override: " + payload.override.backend + (payload.override.model ? " / " + payload.override.model : ""));
            }
            if (payload.route_backend) detailLines.push("Backend: " + payload.route_backend);
            if (payload.model) detailLines.push("Model: " + payload.model);
            if (payload.complexity) detailLines.push("Complexity: " + payload.complexity);
            if (payload.step_name) detailLines.push("Step: " + payload.step_name);
            if (payload.validation_type) detailLines.push("Validation: " + payload.validation_type);
            if (payload.validation_record_id) detailLines.push("Validation record: #" + payload.validation_record_id);
            if (payload.correction_attempt_id) detailLines.push("Correction attempt: #" + payload.correction_attempt_id);
            if (payload.attempt_number) detailLines.push("Attempt: " + payload.attempt_number);
            if (payload.correction_hint) detailLines.push("Correction: " + payload.correction_hint);
            if (payload.target_backend) detailLines.push("Target: " + payload.target_backend + (payload.target_model ? " / " + payload.target_model : ""));
            if (payload.expected) detailLines.push("Expected: " + payload.expected);
            if (payload.active_terminal_count != null) detailLines.push("Runtime terminals: " + payload.active_terminal_count);
            if (Array.isArray(payload.urls) && payload.urls.length && payload.urls[0].url) detailLines.push("App URL: " + payload.urls[0].url);
            if (payload.runtime_snapshot && Array.isArray(payload.runtime_snapshot.urls) && payload.runtime_snapshot.urls.length && payload.runtime_snapshot.urls[0].url) detailLines.push("App URL: " + payload.runtime_snapshot.urls[0].url);
            if (evidence.error) detailLines.push("Error: " + evidence.error);
            if (evidence.result_preview) detailLines.push("Evidence: " + evidence.result_preview);
            return '' +
                '<div class="rounded border border-white/10 bg-[#10183f] px-3 py-2">' +
                    '<div class="flex items-start gap-3">' +
                        '<span class="mt-0.5 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ' + hermesStatusClass(status) + '">' + esc(status) + '</span>' +
                        '<div class="min-w-0 flex-1">' +
                            '<div class="flex items-center gap-2 min-w-0">' +
                                '<p class="text-sm text-white font-medium truncate">' + esc(event.summary || event.event_type || "Run event") + '</p>' +
                                '<span class="text-[11px] text-gray-500 flex-shrink-0">' + esc(event.source || "") + '</span>' +
                            '</div>' +
                            '<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">' +
                                '<span>' + esc(event.event_type || "event") + '</span>' +
                                (meta ? '<span>' + esc(meta) + '</span>' : '') +
                            '</div>' +
                            (detailLines.length ? '<div class="mt-2 space-y-1 text-xs text-gray-400">' + detailLines.slice(0, 4).map(function (line) { return '<p class="truncate">' + esc(line) + '</p>'; }).join('') + '</div>' : '') +
                        '</div>' +
                        '<span class="text-[11px] text-gray-500 flex-shrink-0">' + esc(when) + '</span>' +
                    '</div>' +
                '</div>';
        }).join('');
    }

    function loadHermesTimeline(options) {
        options = options || {};
        if (!currentWorkflowId) {
            renderHermesTimeline([]);
            return Promise.resolve([]);
        }
        return api("GET", "/workflows/" + currentWorkflowId + "/hermes-events?limit=120" + (function () {
            var boardId = getSelectedBoardLocalId();
            return boardId ? ("&board_id=" + encodeURIComponent(boardId)) : "";
        })())
            .then(function (events) {
                renderHermesTimeline(events);
                return events;
            })
            .catch(function (e) {
                renderHermesTimeline([]);
                if (!options.quiet) snack(e.message || "Failed to load timeline", "error");
                return [];
            });
    }

    // ── Agent Context tab ──
    function renderContextRules(data) {
        var listEl = document.getElementById("wf-context-items-list");
        var statusEl = document.getElementById("wf-context-save-status");
        if (!listEl) return;
        var items = Array.isArray(data.context_items) ? data.context_items : [];
        if (!items.length) {
            listEl.innerHTML = '<tr><td colspan="2" class="px-3 py-6 text-center text-sm text-gray-500">No context rules yet.</td></tr>';
            if (statusEl) statusEl.textContent = "";
            return;
        }
        listEl.innerHTML = items.map(function (item) {
            var title = item.title || "Context Rule";
            var tone = title === "Universal Quality Standards" ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200" :
                (title === "Adaptive Quality Memory" ? "border-sky-500/40 bg-sky-500/10 text-sky-200" : "border-white/15 bg-white/5 text-gray-300");
            return '' +
                '<tr data-context-item-id="' + item.id + '" data-context-title="' + esc(title) + '" class="bg-[#152054]/35 align-top">' +
                    '<td class="px-3 py-2">' +
                        '<div class="flex items-center gap-2 mb-2">' +
                            '<span class="inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ' + tone + '">' + esc(title) + '</span>' +
                        '</div>' +
                        '<input type="text" class="wf-ci-content w-full px-3 py-2 bg-[#0d1333] border border-white/10 rounded text-white text-sm focus:outline-none focus:border-[#f97316]" value="' + esc(item.content || "") + '" placeholder="Rule...">' +
                    '</td>' +
                    '<td class="px-3 py-2 align-middle">' +
                        '<div class="flex items-center justify-end gap-2 h-full">' +
                            '<button type="button" class="wf-ci-delete px-3 py-1.5 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20">Delete</button>' +
                            '<button type="button" class="wf-ci-save px-3 py-1.5 rounded bg-[#f97316] text-white text-xs hover:bg-[#ea580c]">Save</button>' +
                        '</div>' +
                    '</td>' +
                '</tr>';
        }).join("");
        if (statusEl) statusEl.textContent = "";

        listEl.querySelectorAll("[data-context-item-id]").forEach(function (row) {
            var contextItemId = parseInt(row.dataset.contextItemId, 10);
            var saveBtn = row.querySelector(".wf-ci-save");
            var deleteBtn = row.querySelector(".wf-ci-delete");
            var contentEl = row.querySelector(".wf-ci-content");

            if (saveBtn) {
                saveBtn.addEventListener("click", function () {
                    if (!currentWorkflowId) return;
                    if (statusEl) statusEl.textContent = "Saving...";
                    api("PATCH", "/workflows/" + currentWorkflowId + "/context-items/" + contextItemId, {
                        title: row.dataset.contextTitle || "Context Rule",
                        content: contentEl ? contentEl.value : "",
                        notes: ""
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
                    showConfirmModal({
                        title: "Delete context item",
                        message: "Delete this context item?",
                        confirmLabel: "Delete",
                        onConfirm: function () {
                            api("DELETE", "/workflows/" + currentWorkflowId + "/context-items/" + contextItemId)
                                .then(function () {
                                    if (statusEl) { statusEl.textContent = "Deleted"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500); }
                                    loadDetail(currentWorkflowId);
                                })
                                .catch(function () {
                                    if (statusEl) statusEl.textContent = "Delete failed";
                                });
                        },
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
        if (tab === "context") loadLearnedRules({ quiet: true });
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
        document.addEventListener("click", function (evt) {
            var btn = evt.target && evt.target.closest ? evt.target.closest(".wf-ui-feedback-btn") : null;
            if (!btn) return;
            evt.preventDefault();
            evt.stopPropagation();
            submitUiTasteFeedback(btn);
        });
        document.addEventListener("keydown", function (evt) {
            if (evt.key === "Escape") closeWorkflowContextMenu();
            if (evt.defaultPrevented) return;
            if (document.getElementById("decisions-confirm-modal")) return;
            if (isTypingTarget(evt.target)) return;
            if (evt.key === "ArrowDown" || evt.key === "ArrowUp") {
                var list = document.getElementById("wf-list");
                var insideList = list && evt.target && list.contains(evt.target);
                if (insideList) {
                    evt.preventDefault();
                    var rows = Array.prototype.slice.call(list.querySelectorAll("[data-id]"));
                    if (!rows.length) return;
                    var active = evt.target.closest ? evt.target.closest("[data-id]") : null;
                    var idx = rows.indexOf(active);
                    if (idx < 0 && currentWorkflowId != null) {
                        idx = rows.findIndex(function(row) { return String(row.dataset.id) === String(currentWorkflowId); });
                    }
                    if (idx < 0) idx = evt.key === "ArrowDown" ? -1 : 0;
                    var next = rows[Math.max(0, Math.min(rows.length - 1, idx + (evt.key === "ArrowDown" ? 1 : -1)))];
                    if (next) next.focus();
                    return;
                }
            }
            if (!currentWorkflowId) return;
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
                if (btn.dataset.tab === "runs" || btn.dataset.tab === "tickets") loadActiveRuns();
                if (btn.dataset.tab === "runs") loadWorkflowExecutionSessions();
                if (btn.dataset.tab === "tickets") loadWorkflowTicketQueue();
            });
        });

        var refreshActiveRuns = document.getElementById("wf-refresh-active-runs");
        if (refreshActiveRuns) {
            refreshActiveRuns.addEventListener("click", function () {
                loadActiveRuns();
                loadWorkflowExecutionSessions();
            });
        }
        var runSettingsSave = document.getElementById("wf-run-settings-save");
        if (runSettingsSave) {
            runSettingsSave.addEventListener("click", function () {
                if (!currentWorkflowId || workflowHasActiveRuns()) {
                    renderRunSettings(currentWorkflow);
                    return;
                }
                var settings = collectRunSettings();
                runSettingsSave.disabled = true;
                api("PATCH", "/workflows/" + currentWorkflowId, { run_settings: settings })
                    .then(function () {
                        if (currentWorkflow) currentWorkflow.run_settings = settings;
                        snack("Run settings saved");
                        renderRunSettings(currentWorkflow);
                    })
                    .catch(function (e) { snack(e.message || "Failed to save run settings", "error"); })
                    .finally(function () { renderRunSettings(currentWorkflow); });
            });
        }

        var refreshWorkflowTickets = document.getElementById("wf-refresh-workflow-tickets");
        if (refreshWorkflowTickets) {
            refreshWorkflowTickets.addEventListener("click", function () {
                loadWorkflowTicketQueue();
                loadActiveRuns();
                loadWorkflowExecutionSessions();
            });
        }
        var workflowCliRefresh = document.getElementById("wf-cli-refresh");
        if (workflowCliRefresh) {
            workflowCliRefresh.addEventListener("click", function () {
                loadWorkflowTicketQueue();
                loadActiveRuns();
                loadWorkflowExecutionSessions();
                if (selectedWorkflowCliTicketId) loadWorkflowCliTicketActivity(selectedWorkflowCliTicketId);
            });
        }
        var workflowExecutionRefresh = document.getElementById("wf-refresh-execution-sessions");
        if (workflowExecutionRefresh) {
            workflowExecutionRefresh.addEventListener("click", function () {
                loadWorkflowExecutionSessions();
            });
        }
        var hermesRefresh = document.getElementById("wf-refresh-hermes-events");
        if (hermesRefresh) {
            hermesRefresh.addEventListener("click", function () {
                loadHermesTimeline();
                refreshBoardHermesViews({ quiet: true });
            });
        }
        var correctionsRefresh = document.getElementById("wf-refresh-corrections");
        if (correctionsRefresh) {
            correctionsRefresh.addEventListener("click", function () {
                loadWorkflowCorrections();
            });
        }
        var correctionsStatusFilter = document.getElementById("wf-corrections-status-filter");
        if (correctionsStatusFilter) {
            correctionsStatusFilter.addEventListener("change", function () {
                loadWorkflowCorrections();
            });
        }
        var hermesSetupBtn = document.getElementById("wf-open-hermes-setup");
        if (hermesSetupBtn) {
            hermesSetupBtn.addEventListener("click", function () {
                var legacySetupBtn = document.getElementById("wf-sr-llm-btn");
                if (legacySetupBtn) legacySetupBtn.click();
            });
        }
        bindWorkflowTicketDropZone();

        var activeRunsScopeEl = document.getElementById("wf-active-runs-scope");
        if (activeRunsScopeEl) {
            activeRunsScopeEl.addEventListener("change", function () {
                activeRunsScope = activeRunsScopeEl.value === "current" ? "current" : "all";
                loadActiveRuns();
            });
        }
        document.querySelectorAll(".wf-runs-subtab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchRunsSubtab(btn.dataset.runsTab || "active");
            });
        });
        switchRunsSubtab(workflowRunsSubtab);

        var workflowBoardSelect = document.getElementById("wf-board-select");
        if (workflowBoardSelect) {
            workflowBoardSelect.addEventListener("change", function () {
                if (workflowBoardSelect.value) loadWorkflowBoardTickets(workflowBoardSelect.value);
                refreshBoardHermesViews({ quiet: true });
            });
        }
        var refreshBoardActivityBtn = document.getElementById("wf-refresh-board-activity");
        if (refreshBoardActivityBtn) {
            refreshBoardActivityBtn.addEventListener("click", function () {
                refreshBoardHermesViews();
            });
        }
        var refreshLearnedRulesBtn = document.getElementById("wf-refresh-learned-rules");
        if (refreshLearnedRulesBtn) {
            refreshLearnedRulesBtn.addEventListener("click", function () {
                refreshBoardHermesViews();
            });
        }
        var refreshScheduledActionsBtn = document.getElementById("wf-refresh-scheduled-actions");
        if (refreshScheduledActionsBtn) {
            refreshScheduledActionsBtn.addEventListener("click", function () {
                loadScheduledActions();
            });
        }
        var scheduledActionsList = document.getElementById("wf-scheduled-actions-list");
        if (scheduledActionsList) {
            scheduledActionsList.addEventListener("click", function (evt) {
                var toggle = evt.target.closest(".wf-scheduled-action-toggle");
                if (toggle) {
                    disableScheduledActionByTitle(toggle.dataset.scheduledActionTitle || "", toggle.dataset.scheduledActionEnabled === "true");
                    return;
                }
                var cancel = evt.target.closest(".wf-scheduled-action-cancel");
                if (cancel) {
                    cancelScheduledActionByTitle(cancel.dataset.scheduledActionTitle || "");
                    return;
                }
                var reschedule = evt.target.closest(".wf-scheduled-action-reschedule");
                if (reschedule) {
                    var card = reschedule.closest("[data-scheduled-action-title]");
                    var kindEl = card ? card.querySelector(".wf-scheduled-action-kind") : null;
                    var timeEl = card ? card.querySelector(".wf-scheduled-action-time") : null;
                    rescheduleScheduledActionByTitle(
                        reschedule.dataset.scheduledActionTitle || "",
                        kindEl ? kindEl.value : "daily",
                        timeEl ? timeEl.value : ""
                    );
                }
            });
        }
        var refreshVisualBaselinesBtn = document.getElementById("wf-refresh-visual-baselines");
        if (refreshVisualBaselinesBtn) {
            refreshVisualBaselinesBtn.addEventListener("click", function () {
                loadVisualBaselines();
            });
        }
        var saveVisualBaselineBtn = document.getElementById("wf-save-visual-baseline");
        if (saveVisualBaselineBtn) {
            saveVisualBaselineBtn.addEventListener("click", createVisualBaseline);
        }
        var skillChainsSaveBtn = document.getElementById("wf-skill-chains-save");
        if (skillChainsSaveBtn) {
            skillChainsSaveBtn.addEventListener("click", saveSkillChains);
        }
        var workflowBoardEdit = document.getElementById("wf-edit-board-link");
        if (workflowBoardEdit) {
            workflowBoardEdit.addEventListener("click", openWorkflowBoardEditModal);
        }
        document.querySelectorAll(".kb-tm-tab").forEach(function (btn) {
            btn.addEventListener("click", function () { switchWorkflowTicketTab(btn.dataset.ttab || "details"); });
        });
        var ticketClose = document.getElementById("kb-modal-close");
        if (ticketClose) ticketClose.addEventListener("click", closeWorkflowTicketModal);
        var ticketCancel = document.getElementById("kb-modal-cancel");
        if (ticketCancel) ticketCancel.addEventListener("click", closeWorkflowTicketModal);
        var ticketModal = document.getElementById("kb-ticket-modal");
        if (ticketModal) {
            ticketModal.addEventListener("click", function (evt) {
                if (evt.target === ticketModal) closeWorkflowTicketModal();
            });
        }
        var ticketCopy = document.getElementById("kb-modal-act-copy");
        if (ticketCopy) {
            ticketCopy.addEventListener("click", function () {
                var text = (document.getElementById("kb-modal-ticket-title").value || "") + "\n\n" + (document.getElementById("kb-modal-ticket-desc").value || "");
                navigator.clipboard && navigator.clipboard.writeText(text).then(function () { snack("Ticket copied"); }).catch(function () {});
            });
        }
        var ticketSave = document.getElementById("kb-modal-save");
        if (ticketSave) {
            ticketSave.addEventListener("click", function () {
                var state = workflowTicketModalState;
                var item = state && state.ticket ? state : workflowBoardTicketByKey[selectedWorkflowBoardTicketKey];
                var ticket = item && item.ticket ? item.ticket : null;
                var selected = item && item.context && item.context.selected ? item.context.selected : item && item.selected ? item.selected : {};
                if (!ticket || !ticket.id || !state || !state.isLocal) return;
                var payload = {
                    title: document.getElementById("kb-modal-ticket-title").value.trim(),
                    description: document.getElementById("kb-modal-ticket-desc").value,
                    priority: (document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]") || {}).dataset ? document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]").dataset.pri : "medium",
                    complexity: document.getElementById("kb-modal-ticket-complexity").value,
                    time_estimate: document.getElementById("kb-modal-ticket-estimate").value.trim(),
                    time_spent: document.getElementById("kb-modal-ticket-duration").value.trim(),
                    linked_workflow_id: parseInt(document.getElementById("kb-modal-link-workflow").value, 10) || null,
                    linked_project_id: parseInt(document.getElementById("kb-modal-link-project").value, 10) || null
                };
                api("PUT", "/kanban/tickets/" + encodeURIComponent(ticket.id), payload)
                    .then(function () {
                        snack("Ticket saved");
                        closeWorkflowTicketModal();
                        loadWorkflowTicketQueue();
                        if (selected && selected.value) loadWorkflowBoardTickets(selected.value);
                    })
                    .catch(function (e) { snack(e.message || "Failed to save ticket", "error"); });
            });
        }
        var ticketDelete = document.getElementById("kb-modal-delete");
        if (ticketDelete) {
            ticketDelete.addEventListener("click", function () {
                var state = workflowTicketModalState;
                var ticket = state && state.ticket ? state.ticket : null;
                var selected = state && state.context ? state.context.selected : {};
                if (!ticket || !ticket.id || !state || !state.isLocal) return;
                showConfirmModal({
                    title: "Delete ticket",
                    message: "Delete this ticket? This cannot be undone.",
                    confirmLabel: "Delete",
                    onConfirm: function () {
                        api("DELETE", "/kanban/tickets/" + encodeURIComponent(ticket.id))
                            .then(function () {
                                snack("Ticket deleted");
                                closeWorkflowTicketModal();
                                loadWorkflowTicketQueue();
                                if (selected && selected.value) loadWorkflowBoardTickets(selected.value);
                            })
                            .catch(function (e) { snack(e.message || "Failed to delete ticket", "error"); });
                    }
                });
            });
        }
        var addLinkBtn = document.getElementById("kb-modal-add-link");
        if (addLinkBtn) addLinkBtn.addEventListener("click", addWorkflowTicketLink);
        var linkUrlInput = document.getElementById("kb-modal-link-url");
        if (linkUrlInput) {
            linkUrlInput.addEventListener("keydown", function (e) {
                if (e.key === "Enter") addWorkflowTicketLink();
            });
        }
        var uploadBtn = document.getElementById("kb-modal-upload-btn");
        var fileInput = document.getElementById("kb-modal-file-input");
        if (uploadBtn && fileInput) {
            uploadBtn.addEventListener("click", function () { fileInput.click(); });
            fileInput.addEventListener("change", function () {
                uploadWorkflowTicketFiles(fileInput.files);
                fileInput.value = "";
            });
        }
        var addTodoBtn = document.getElementById("kb-modal-add-todo");
        if (addTodoBtn) addTodoBtn.addEventListener("click", addWorkflowTicketTodo);
        var todoInput = document.getElementById("kb-modal-todo-input");
        if (todoInput) {
            todoInput.addEventListener("keydown", function (e) {
                if (e.key === "Enter") addWorkflowTicketTodo();
            });
        }
        var ticketProject = document.getElementById("kb-modal-act-project");
        if (ticketProject) {
            ticketProject.addEventListener("click", function () {
                var ticketId = currentWorkflowModalTicketId();
                if (!ticketId) return;
                ticketProject.disabled = true;
                api("POST", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/send-to-project")
                    .then(function (r) { snack((r && r.message) || "Ticket sent to project"); })
                    .catch(function (e) { snack(e.message || "Failed to send to project", "error"); })
                    .finally(function () { ticketProject.disabled = false; });
            });
        }
        var ticketCli = document.getElementById("kb-modal-act-cli");
        if (ticketCli) {
            ticketCli.addEventListener("click", function () {
                var ticketId = currentWorkflowModalTicketId();
                if (!ticketId) return;
                ticketCli.disabled = true;
                api("POST", "/kanban/tickets/" + encodeURIComponent(ticketId) + "/send-to-cli")
                    .then(function (r) { snack((r && r.message) || "Ticket sent to CLI"); })
                    .catch(function (e) { snack(e.message || "Failed to send to CLI", "error"); })
                    .finally(function () { ticketCli.disabled = false; });
            });
        }
        var ticketWorkflow = document.getElementById("kb-modal-act-workflow");
        if (ticketWorkflow) {
            ticketWorkflow.addEventListener("click", function () {
                var ticketId = currentWorkflowModalTicketId();
                var workflowSelect = document.getElementById("kb-modal-link-workflow");
                var workflowId = workflowSelect ? parseInt(workflowSelect.value, 10) : null;
                if (!ticketId || !workflowId) {
                    snack("Choose a workflow in Advanced first", "error");
                    return;
                }
                api("PUT", "/kanban/tickets/" + encodeURIComponent(ticketId), { linked_workflow_id: workflowId })
                    .then(function () {
                        snack("Ticket linked to workflow");
                        return reloadWorkflowTicketModal();
                    })
                    .catch(function (e) { snack(e.message || "Failed to link workflow", "error"); });
            });
        }
        var ticketDiscuss = document.getElementById("kb-modal-act-discuss");
        if (ticketDiscuss) {
            ticketDiscuss.addEventListener("click", function () {
                var state = workflowTicketModalState;
                if (!state || !state.ticket) return;
                var title = (document.getElementById("kb-modal-ticket-title").value || "").trim();
                var description = document.getElementById("kb-modal-ticket-desc").value || "";
                var message = "[Workflow ticket discussion]\\n\\nTicket #" + state.ticket.id + "\\n\\nTitle:\\n" + title + "\\n\\nDescription:\\n" + description;
                api("GET", "/chats")
                    .then(function (data) {
                        var chats = Array.isArray(data) ? data : (data && Array.isArray(data.chats) ? data.chats : []);
                        if (chats.length && chats[0].id) return chats[0].id;
                        return api("POST", "/chats", {}).then(function (created) { return created.id; });
                    })
                    .then(function (chatId) {
                        return api("POST", "/chats/" + encodeURIComponent(chatId) + "/load-in-agent").then(function () {
                            return api("POST", "/chats/" + encodeURIComponent(chatId) + "/send-to-agent", { message: message, speak: true });
                        });
                    })
                    .then(function () { snack("Ticket sent to agent chat"); })
                    .catch(function (e) { snack(e.message || "Failed to discuss ticket", "error"); });
            });
        }
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function (btn) {
            btn.addEventListener("click", function () {
                document.querySelectorAll("#kb-modal-priority-btns button").forEach(function (b) {
                    b.classList.remove("bg-[#f97316]", "text-white");
                    b.classList.add("text-gray-400");
                });
                btn.classList.add("bg-[#f97316]", "text-white");
                btn.classList.remove("text-gray-400");
            });
        });

        var addContextItemBtn = document.getElementById("wf-add-context-item-btn");
        if (addContextItemBtn) {
            addContextItemBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                var statusEl = document.getElementById("wf-context-save-status");
                if (statusEl) statusEl.textContent = "Saving...";
                api("POST", "/workflows/" + currentWorkflowId + "/context-items", {
                    title: "Context Rule",
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

        function saveWorkflowName() {
            if (!currentWorkflowId) return;
            var nameEl = document.getElementById("wf-detail-name");
            var statusEl = document.getElementById("wf-name-save-status");
            var nextName = (nameEl && nameEl.value ? nameEl.value.trim() : "") || "Untitled Workflow";
            if (statusEl) statusEl.textContent = "Saving...";
            api("PATCH", "/workflows/" + currentWorkflowId, { name: nextName })
                .then(function () {
                    if (currentWorkflow) currentWorkflow.name = nextName;
                    if (nameEl) nameEl.value = nextName;
                    if (statusEl) {
                        statusEl.textContent = "Saved";
                        setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500);
                    }
                    loadList();
                })
                .catch(function (e) {
                    if (statusEl) statusEl.textContent = "Failed";
                    snack(e.message || "Failed to save workflow name", "error");
                });
        }

        var saveNameBtn = document.getElementById("wf-save-name-btn");
        if (saveNameBtn) {
            saveNameBtn.addEventListener("click", saveWorkflowName);
        }
        var detailNameEl = document.getElementById("wf-detail-name");
        if (detailNameEl) {
            detailNameEl.addEventListener("keydown", function (evt) {
                if (evt.key === "Enter") {
                    evt.preventDefault();
                    saveWorkflowName();
                    detailNameEl.blur();
                }
                if (evt.key === "Escape" && currentWorkflow) {
                    evt.preventDefault();
                    detailNameEl.value = currentWorkflow.name || "";
                    detailNameEl.blur();
                }
            });
        }

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

        // Stop + Reset workflow
        var stopResetBtn = document.getElementById("wf-stop-reset-btn");
        if (stopResetBtn) {
            stopResetBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                showConfirmModal({
                    title: "Stop active runs",
                    message: "Stop all active runs for this workflow?\n\nThis will cancel active runs and reset workflow step states/results.",
                    confirmLabel: "Stop",
                    onConfirm: function () {
                        api("POST", "/workflows/" + currentWorkflowId + "/stop-reset")
                            .then(function (data) {
                                snack(workflowFeedbackText(data, "Active runs stopped"));
                                stopPolling();
                                loadDetail(currentWorkflowId);
                                loadActiveRuns();
                            })
                            .catch(function (e) { snack(workflowErrorText(e, "Failed to stop active runs"), "error"); });
                    },
                });
            });
        }
        var clearRunsBtn = document.getElementById("wf-clear-runs-btn");
        if (clearRunsBtn) {
            clearRunsBtn.addEventListener("click", clearWorkflowRunAudit);
        }
        var clearExecutionSessionsBtn = document.getElementById("wf-clear-execution-sessions");
        if (clearExecutionSessionsBtn) {
            clearExecutionSessionsBtn.addEventListener("click", clearWorkflowExecutionSessions);
        }
        var clearHermesEventsBtn = document.getElementById("wf-clear-hermes-events");
        if (clearHermesEventsBtn) {
            clearHermesEventsBtn.addEventListener("click", clearWorkflowEvents);
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

        var runPreviewModal = document.getElementById("wf-run-preview-modal");
        var runPreviewClose = document.getElementById("wf-run-preview-close");
        var runPreviewCancel = document.getElementById("wf-run-preview-cancel");
        var runPreviewConfirm = document.getElementById("wf-run-preview-confirm");
        if (runPreviewClose) runPreviewClose.addEventListener("click", closeWorkflowRunPreview);
        if (runPreviewCancel) runPreviewCancel.addEventListener("click", closeWorkflowRunPreview);
        if (runPreviewConfirm) runPreviewConfirm.addEventListener("click", confirmWorkflowRunPreview);
        if (runPreviewModal) {
            runPreviewModal.addEventListener("click", function (evt) {
                if (evt.target === runPreviewModal) closeWorkflowRunPreview();
            });
        }

        var waMediaPreviewModal = document.getElementById("wf-wa-media-preview-modal");
        var waMediaPreviewClose = document.getElementById("wf-wa-media-preview-close");
        if (waMediaPreviewClose) waMediaPreviewClose.addEventListener("click", closeWorkflowWhatsappMediaPreview);
        if (waMediaPreviewModal) {
            waMediaPreviewModal.addEventListener("click", function (evt) {
                if (evt.target === waMediaPreviewModal) closeWorkflowWhatsappMediaPreview();
            });
        }

        var waTicketModal = document.getElementById("wf-wa-ticket-modal");
        var waCloseBtn = document.getElementById("wf-wa-ticket-close");
        var waCancelBtn = document.getElementById("wf-wa-ticket-cancel");
        var waSaveBtn = document.getElementById("wf-wa-ticket-save");
        var waLoadOlderBtn = document.getElementById("wf-wa-ticket-load-older");
        if (waCloseBtn) waCloseBtn.addEventListener("click", closeWorkflowWhatsappTicketModal);
        if (waCancelBtn) waCancelBtn.addEventListener("click", closeWorkflowWhatsappTicketModal);
        if (waSaveBtn) waSaveBtn.addEventListener("click", submitWorkflowWhatsappTicketModal);
        if (waLoadOlderBtn) {
            waLoadOlderBtn.addEventListener("click", function () {
                loadWorkflowWhatsappTicketPreview("all_unticketed").catch(function (e) {
                    snack(e.message || "Could not load older WhatsApp messages", "error");
                });
            });
        }
        if (waTicketModal) {
            waTicketModal.addEventListener("click", function (evt) {
                if (evt.target === waTicketModal) closeWorkflowWhatsappTicketModal();
            });
        }

        loadList();
        loadWorkflowBoardCollapsedColumns();
        loadWorkflowBoards();
        loadWorkflowSkillsCatalog();
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
