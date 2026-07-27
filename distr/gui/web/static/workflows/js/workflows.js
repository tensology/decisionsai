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
    var wsReconnectDelay = 5000;
    var wsConnectFailedLogged = false;
    var workflowWsRefreshTimer = null;
    var workflowWsRefreshInFlight = false;
    var workflowWsRefreshQueued = false;
    var workflowWsListRefreshedAt = 0;
    var activeRunsScope = "all";
    var workflowRunsSubtab = "active";
    var workflowMemoryRunId = null;
    var latestSteeringMemory = null;
    var latestActiveRuns = [];
    var latestWorkflowRunHistory = [];
    var latestWorkflowRunHistoryAll = [];
    var workflowRunsFilterTicketId = null;
    var workflowRunsSeenByWorkflowId = {};
    var latestWorkflowExecutionSessions = [];
    var workflowCliInspectorOpen = true;
    var workflowCliInspectorTab = "step-models";
    var workflowStepModelsCalloutVisible = true;
    var workflowBoardPaneCollapsed = false;
    var latestOrchestratorEvents = [];
    var loopFeedRunId = null;
    var latestLoopFeedItems = [];
    var loopFeedScrollPinned = true;
    var loopTranscriptEventsByRun = {};
    var loopTranscriptBlueprintByRun = {};
    var loopTranscriptLoadedAtByRun = {};
    var loopTranscriptLoadingByRun = {};
    var loopTranscriptOpenByRun = {};
    var loopTranscriptRecordOpenByRun = {};
    var shouldRestoreDetailTabOnce = true;
    var wfContextMenuEl = null;
    var wfContextMenuId = null;
    var wfQueueMetricMenuEl = null;
    var wfQueueMetricMenuState = null;
    var workflowRuntimeStateById = {};
    var workflowBoardOptions = [];
    var workflowBoardSelectionExplicit = false;
    var workflowDatabaseBoards = [];
    var workflowBoardLoadToken = 0;
    var workflowBoardRenderState = { data: null, selected: null };
    var workflowBoardTicketByKey = {};
    var workflowTicketUi = null;
    var workflowLinkable = { projects: [], workflows: [] };
    var workflowQueueTickets = [];
    var workflowActionsCatalog = [];
    var workflowActionsCatalogLoaded = false;
    var workflowQueueDragTicketId = null;
    var workflowBoardDragPayload = null;
    var workflowBoardDragGhostEl = null;
    var workflowBoardDragState = null;
    var workflowBoardMouseDragBound = false;
    var workflowDropDocumentBound = false;
    var workflowDropZoneHoverDepth = 0;
    var workflowLastDragPoint = null;
    var workflowPendingTicketLinks = {};
    var workflowLinkedExternalTicketKeys = {};
    var workflowLinkedLocalTicketKeys = {};
    var workflowLocalTicketSourceKeys = {};
    var selectedWorkflowQueueTicketId = null;
    var workflowTicketPendingRunStartedAt = {};
    var workflowTicketTimerInterval = null;
    var workflowTabRunTimerInterval = null;
    var workflowTabDragId = null;
    var workflowActiveTicketIdsSnapshot = [];
    var workflowTicketModalState = null;
    var workflowTicketModalDetailsHeight = 0;
    var workflowWhatsappTicketDraft = null;
    var workflowWhatsappProgressTimer = null;
    var workflowWhatsappProgressValue = 8;
    var expandedWorkflowExecutionSessionId = null;
    var WORKFLOW_LOOP_MAX_STEPS = 14;
    // The execution timeline is the useful default. The ring remains available
    // as a workflow-design overview, but it does not carry enough evidence to
    // explain a live run by itself.
    var workflowLoopViewMode = "list";
    var workflowRingViewPromise = null;
    var workflowWorkspaceMemoryLoadedFor = null;
    var workflowLoopStepModalState = null;
    var workflowLoopStepModalBound = false;
    var workflowLoopSkillsCatalog = null;
    var workflowLoopSkillsCatalogLoading = null;
    var workflowLoopStepContentTab = "instruction";
    var pendingUiTasteFeedback = null;
    var LOOP_STEP_TOOL_OPTIONS = [
        { id: "agent", label: "Agent", emoji: "◎" },
        { id: "playwright", label: "Playwright", emoji: "🎭" },
        { id: "browser_use", label: "Browser use", emoji: "🌐" },
        { id: "computer_use", label: "Computer use", emoji: "🖥️" },
        { id: "cli", label: "CLI", emoji: "🔑" },
        { id: "python", label: "Python", emoji: "Py" },
        { id: "shell", label: "Shell", emoji: "$" },
        { id: "http", label: "HTTP", emoji: "↗" },
        { id: "macro", label: "Macro", emoji: "▶" },
        { id: "ytdlp", label: "yt-dlp", emoji: "↓" }
    ];
    var pendingWorkflowRunTicketId = null;
    var pendingWorkflowRunTicketIds = [];
    var WHATSAPP_ICON_SVG = '<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.27-1.38a9.9 9.9 0 0 0 4.77 1.21h.01c5.46 0 9.91-4.45 9.91-9.91C21.96 6.45 17.51 2 12.04 2Zm0 18.16h-.01a8.2 8.2 0 0 1-4.18-1.14l-.3-.18-3.12.82.83-3.04-.2-.31a8.2 8.2 0 0 1-1.26-4.39c0-4.54 3.7-8.24 8.25-8.24 2.2 0 4.27.86 5.82 2.42a8.2 8.2 0 0 1 2.42 5.83c0 4.54-3.7 8.23-8.25 8.23Zm4.52-6.17c-.25-.12-1.47-.72-1.7-.81-.23-.08-.39-.12-.56.13-.16.24-.64.8-.78.96-.14.16-.29.18-.54.06-.25-.13-1.04-.38-1.99-1.22-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.02-.38.11-.5.11-.11.25-.29.37-.43.12-.15.16-.25.25-.41.08-.17.04-.31-.02-.43-.06-.13-.56-1.35-.77-1.85-.2-.49-.41-.42-.56-.43h-.48c-.16 0-.43.06-.65.31-.23.25-.86.84-.86 2.04 0 1.2.88 2.37 1 2.53.12.17 1.73 2.64 4.18 3.7.58.25 1.04.4 1.39.51.58.18 1.11.16 1.53.1.47-.07 1.47-.6 1.68-1.18.21-.58.21-1.08.14-1.18-.06-.1-.22-.16-.47-.28Z"/></svg>';
    var DEFAULT_RUN_SETTINGS = {
        execution_mode: "sequential",
        concurrency_scope: "project",
        max_parallel_tickets: 3,
        branch_per_ticket: true,
        auto_route_models: true,
        free_only: false,
        prefer_local: true
    };

    // Inline SVG icons (14x14, currentColor)
    var SVG_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    var SVG_LOOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.5 8.5c-2.2 0-4 1.6-4 3.5s1.8 3.5 4 3.5c4 0 5-7 9-7 2.2 0 4 1.6 4 3.5s-1.8 3.5-4 3.5c-4 0-5-7-9-7z"/></svg>';
    var SVG_FORWARD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 19 22 12 13 5 13 19"/><polygon points="2 19 11 12 2 5 2 19"/></svg>';
    var SVG_CANCEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
       var SVG_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    var SVG_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
    var SVG_PLAY_REC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M8 5v14l11-7z"/></svg>';
    var SVG_ORCHESTRATOR_BOT = '<svg class="wf-loop-ring-center-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V5"/><path d="M8 5h8"/><rect x="5" y="8" width="14" height="11" rx="2"/><circle cx="9.5" cy="13" r="1"/><circle cx="14.5" cy="13" r="1"/><path d="M9 16.5h6"/></svg>';

    function esc(s) {
        if (!s) return "";
        return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function renderApprovalDecisionCard(decision, opts) {
        if (!decision || typeof decision !== "object" || !decision.title) return "";
        opts = opts || {};
        var fields = [
            ["About to do", decision.about_to_do],
            ["Why it matters", decision.why_it_matters],
            ["What could go wrong", decision.what_could_go_wrong],
            ["If we stop", decision.fallback],
            ["Recommendation", decision.recommendation]
        ];
        var rows = fields.filter(function (f) { return f[1]; }).map(function (f) {
            return '<div class="mt-1 text-[11px]"><span class="text-gray-500">' + esc(f[0]) + ':</span> <span class="text-gray-200">' + esc(f[1]) + '</span></div>';
        }).join("");
        var hints = decision.reply_hints
            ? '<p class="mt-2 text-[11px] text-gray-400">' + esc(String(decision.reply_hints).replace(/\*\*/g, "")) + '</p>'
            : "";
        var actions = "";
        if (opts.showActions && opts.workflowId && opts.runId) {
            actions = '<div class="mt-2 flex flex-wrap gap-2">' +
                '<button type="button" class="wf-decision-approve px-2 py-1 rounded bg-green-600/80 text-white text-xs hover:bg-green-600" data-workflow-id="' + esc(opts.workflowId) + '" data-run-id="' + esc(opts.runId) + '">Yes, go ahead</button>' +
                '<button type="button" class="wf-decision-stop px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-workflow-id="' + esc(opts.workflowId) + '" data-run-id="' + esc(opts.runId) + '">No, stop</button>' +
            '</div>';
        }
        return '<div class="rounded border border-sky-500/30 bg-sky-500/10 p-3">' +
            '<p class="text-xs text-sky-200 font-medium">' + esc(decision.title) + '</p>' +
            rows + hints + actions +
        '</div>';
    }

    function bindApprovalDecisionButtons(root) {
        if (!root) return;
        root.querySelectorAll(".wf-decision-approve").forEach(function (btn) {
            btn.addEventListener("click", function () {
                btn.disabled = true;
                continueWorkflowRun(btn.dataset.workflowId, btn.dataset.runId, { input: "yes, go ahead" })
                    .then(function (resp) {
                        snack(workflowFeedbackText(resp, "Run continued"));
                        loadActiveRuns();
                        if (currentWorkflowId) loadDetail(currentWorkflowId);
                    })
                    .catch(function (e) {
                        btn.disabled = false;
                        snack(workflowErrorText(e, "Failed to continue"), "error");
                    });
            });
        });
        root.querySelectorAll(".wf-decision-stop").forEach(function (btn) {
            btn.addEventListener("click", function () {
                cancelWorkflowRun(btn.dataset.workflowId, btn.dataset.runId, btn);
            });
        });
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

    function workflowApiFetch(path, opts) {
        opts = opts || {};
        var method = opts.method || "GET";
        var body = opts.body;
        if (typeof body === "string") {
            try { body = JSON.parse(body); } catch (e) { body = undefined; }
        }
        var apiPath = path.indexOf("/api") === 0 ? path.slice(4) : path;
        return api(method, apiPath, body);
    }

    function workflowListLaneExpandedStorageKey(selected) {
        if (!selected || selected.id == null) return null;
        var source = selected.source || "database";
        return "kb_list_lane_expanded_" + source + "_" + String(selected.id);
    }

    function workflowResolveDefaultListExpandedLaneId(lanes) {
        if (!lanes || !lanes.length) return null;
        var currentLane = lanes.filter(function (lane) {
            return String(lane.name || "").trim().toLowerCase() === "current";
        })[0];
        if (currentLane && currentLane.id != null) return String(currentLane.id);
        return lanes[0].id != null ? String(lanes[0].id) : null;
    }

    function getWorkflowListExpandedLaneId(lanes, selected) {
        var key = workflowListLaneExpandedStorageKey(selected);
        if (key) {
            try {
                var saved = localStorage.getItem(key);
                if (saved === "__none__") return null;
                if (saved && lanes.some(function (lane) { return String(lane.id) === String(saved); })) {
                    return String(saved);
                }
            } catch (e) {}
        }
        return workflowResolveDefaultListExpandedLaneId(lanes);
    }

    function saveWorkflowListExpandedLaneId(selected, laneId) {
        var key = workflowListLaneExpandedStorageKey(selected);
        if (!key) return;
        try {
            if (laneId == null || laneId === "") {
                localStorage.setItem(key, "__none__");
            } else {
                localStorage.setItem(key, String(laneId));
            }
        } catch (e) {}
    }

    function workflowListLaneChevronSvg() {
        return '<svg class="kb-ticket-list-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>';
    }

    function setWorkflowListLaneExpanded(container, laneId) {
        if (!container) return;
        var expandedId = laneId != null && laneId !== "" ? String(laneId) : "";
        container.querySelectorAll(".kb-ticket-list-section").forEach(function (section) {
            var isExpanded = expandedId && String(section.dataset.laneId) === expandedId;
            section.classList.toggle("kb-ticket-list-section--expanded", isExpanded);
            var head = section.querySelector(".kb-ticket-list-section-head");
            var body = section.querySelector(".kb-ticket-list-section-body");
            if (head) head.setAttribute("aria-expanded", isExpanded ? "true" : "false");
            if (body) body.hidden = !isExpanded;
        });
        if (expandedId) {
            var active = container.querySelector('.kb-ticket-list-section[data-lane-id="' + expandedId + '"]');
            if (active) initWorkflowListRowMarquees(active);
        }
    }

    function bindWorkflowListLaneAccordion(container, lanes, selected) {
        container.querySelectorAll(".kb-ticket-list-section-head").forEach(function (head) {
            head.addEventListener("click", function () {
                var section = head.closest(".kb-ticket-list-section");
                if (!section) return;
                var laneId = section.dataset.laneId;
                if (!laneId) return;
                if (section.classList.contains("kb-ticket-list-section--expanded")) {
                    setWorkflowListLaneExpanded(container, null);
                    saveWorkflowListExpandedLaneId(selected, null);
                    return;
                }
                setWorkflowListLaneExpanded(container, laneId);
                saveWorkflowListExpandedLaneId(selected, laneId);
            });
        });
    }

    function initWorkflowListRowMarquees(rootEl) {
        var ticketUi = ensureWorkflowTicketUi();
        if (!ticketUi || !ticketUi.initListRowMarquee) return;
        (rootEl || document).querySelectorAll(".kb-ticket-list-section--expanded .kb-ticket-list-row").forEach(function (row) {
            ticketUi.initListRowMarquee(row);
        });
    }

    function workflowTabTitleHtml(name) {
        return '<span class="wf-workflow-tab-title-wrap"><span class="wf-workflow-tab-title">' + esc(name || "Untitled Workflow") + "</span></span>";
    }

    function scheduleWorkflowTabMarquees() {
        requestAnimationFrame(function () {
            requestAnimationFrame(initWorkflowTabMarquees);
        });
    }

    function initWorkflowTabMarquees(rootEl) {
        var root = rootEl && typeof rootEl.querySelectorAll === "function" ? rootEl : document;
        root.querySelectorAll(".wf-workflow-tab").forEach(function (tab) {
            if (tab.querySelector(".wf-workflow-tab-rename")) return;
            var wrap = tab.querySelector(".wf-workflow-tab-title-wrap");
            var title = tab.querySelector(".wf-workflow-tab-title");
            if (!wrap || !title) return;
            title.classList.remove("wf-workflow-tab-title--marquee");
            wrap.classList.remove("wf-workflow-tab-title-wrap--marquee");
            title.style.removeProperty("--marquee-distance");
            var textWidth = title.scrollWidth;
            var available = wrap.clientWidth;
            if (textWidth <= available + 1) return;
            title.classList.add("wf-workflow-tab-title--marquee");
            wrap.classList.add("wf-workflow-tab-title-wrap--marquee");
            title.style.setProperty("--marquee-distance", (textWidth - available) + "px");
        });
    }

    function initLoopTicketTitleMarquees(rootEl) {
        (rootEl || document).querySelectorAll(".wf-loop-ticket-title-wrap").forEach(function (wrap) {
            if (wrap.dataset.marqueeInit === "done") return;
            var track = wrap.querySelector(".wf-loop-ticket-title-track");
            if (!track) {
                wrap.dataset.marqueeInit = "done";
                return;
            }
            var first = track.querySelector("span");
            if (!first) {
                wrap.dataset.marqueeInit = "done";
                return;
            }
            var text = (first.textContent || "").trim();
            if (!text) {
                wrap.dataset.marqueeInit = "done";
                return;
            }
            requestAnimationFrame(function () {
                if (wrap.dataset.marqueeInit === "done") return;
                if (track.scrollWidth <= wrap.clientWidth + 1) {
                    wrap.dataset.marqueeInit = "done";
                    return;
                }
                wrap.dataset.marqueeInit = "done";
                wrap.classList.add("wf-loop-ticket-title-wrap--marquee");
                var clone = first.cloneNode(true);
                clone.setAttribute("aria-hidden", "true");
                track.appendChild(clone);
                var duration = Math.max(10, Math.round(track.scrollWidth / 40));
                track.style.setProperty("--kb-marquee-duration", duration + "s");
            });
        });
    }

    var _workflowTabMarqueeResizeTimer = null;
    window.addEventListener("resize", function () {
        clearTimeout(_workflowTabMarqueeResizeTimer);
        _workflowTabMarqueeResizeTimer = setTimeout(function () {
            scheduleWorkflowTabMarquees();
            initLoopTicketTitleMarquees();
        }, 150);
    });

    function findWorkflowTicketKeyById(ticketId) {
        var keys = Object.keys(workflowBoardTicketByKey || {});
        for (var i = 0; i < keys.length; i++) {
            var item = workflowBoardTicketByKey[keys[i]];
            if (item && item.ticket && String(item.ticket.id) === String(ticketId)) return keys[i];
        }
        return "";
    }

    var _workflowTicketDiscussInFlight = false;

    function persistWorkflowSourceChatId(chatId) {
        if (chatId == null || chatId < 1) return;
        try {
            sessionStorage.setItem("decisions_source_chat_id", String(Number(chatId)));
        } catch (e) { /* ignore */ }
    }

    function pickWorkflowChatIdFromList(data) {
        if (!data || !Array.isArray(data.chats)) return null;
        var present = {};
        var i, c, nid;
        for (i = 0; i < data.chats.length; i++) {
            c = data.chats[i];
            if (c && c.id != null) {
                nid = typeof c.id === "number" ? c.id : parseInt(c.id, 10);
                if (!isNaN(nid) && nid >= 1) present[nid] = true;
            }
        }
        function coercePick(v) {
            if (v == null) return null;
            var n = typeof v === "number" ? v : parseInt(v, 10);
            if (isNaN(n) || n < 1) return null;
            return present[n] ? n : null;
        }
        var agent = coercePick(data.agent_current_chat_id);
        if (agent != null) return agent;
        var last = coercePick(data.last_chat_id);
        if (last != null) return last;
        if (data.chats.length && data.chats[0].id != null) {
            nid = typeof data.chats[0].id === "number" ? data.chats[0].id : parseInt(data.chats[0].id, 10);
            if (!isNaN(nid) && nid >= 1) return nid;
        }
        return null;
    }

    function resolveWorkflowChatIdForTicketDiscuss() {
        try {
            var stored = sessionStorage.getItem("decisions_source_chat_id");
            if (stored) {
                var parsed = parseInt(stored, 10);
                if (!isNaN(parsed) && parsed >= 1) return Promise.resolve(parsed);
            }
        } catch (e) { /* ignore */ }
        if (window.DecisionsWebChat && typeof window.DecisionsWebChat.getSourceChatIdForTickets === "function") {
            var direct = window.DecisionsWebChat.getSourceChatIdForTickets();
            if (direct != null && direct >= 1) {
                persistWorkflowSourceChatId(direct);
                return Promise.resolve(direct);
            }
        }
        return workflowApiFetch("/api/chats").then(function (data) {
            var cid = pickWorkflowChatIdFromList(data);
            if (cid != null) return cid;
            return workflowApiFetch("/api/chats", {
                method: "POST",
                body: {},
            }).then(function (created) {
                if (!created || created.id == null) throw new Error("Could not create a chat");
                var nc = typeof created.id === "number" ? created.id : parseInt(created.id, 10);
                if (isNaN(nc) || nc < 1) throw new Error("Invalid chat id from create");
                return nc;
            });
        }).then(function (chatId) {
            persistWorkflowSourceChatId(chatId);
            return chatId;
        });
    }

    function startWorkflowTicketDiscussion(ticket, isLocal) {
        if (!ticket) {
            snack("No ticket to discuss", "error");
            return;
        }
        if (_workflowTicketDiscussInFlight) return;
        var opt = currentWorkflowBoardOption();
        var boardData = workflowBoardRenderState.data || {};
        var src = opt && opt.source ? opt.source : "database";
        var boardLabel = (boardData && boardData.name) ? boardData.name : (opt && opt.label ? opt.label : "");
        var localBoardId = null;
        if (opt) {
            if (src === "database" && opt.id != null) {
                localBoardId = opt.id;
            } else if (boardData && boardData.local_id) {
                localBoardId = boardData.local_id;
            } else if (opt.local_id) {
                localBoardId = opt.local_id;
            }
        }
        _workflowTicketDiscussInFlight = true;
        snack("Sending ticket to the orchestrator…", "info");
        resolveWorkflowChatIdForTicketDiscuss()
            .then(function (chatId) {
                return workflowApiFetch("/api/tickets/tickets/engage-orchestrator", {
                    method: "POST",
                    body: {
                        chat_id: chatId,
                        ticket: ticket,
                        is_local: !!isLocal,
                        local_board_id: localBoardId,
                        source: src,
                        board_name: boardLabel,
                    },
                });
            })
            .then(function (res) {
                var brief = (res && res.display_message) ? res.display_message : "Ticket sent to the orchestrator";
                snack(brief, "success");
            })
            .catch(function (e) {
                snack("Could not reach the agent: " + (e && e.message ? e.message : String(e)), "error");
            })
            .finally(function () {
                _workflowTicketDiscussInFlight = false;
            });
    }

    function ensureWorkflowTicketUi() {
        if (workflowTicketUi) return workflowTicketUi;
        if (!window.KanbanTicketUi) return null;
        workflowTicketUi = window.KanbanTicketUi.create({
            esc: esc,
            stripHtml: stripHtml,
            truncate: function (s, maxLen) {
                if (!s) return "";
                s = String(s).replace(/\s+/g, " ").trim();
                return s.length > maxLen ? s.substring(0, maxLen) + "…" : s;
            },
            setPriorityButtons: function () {},
            setTicketComplexity: function () {},
            renderModalLinks: function () {},
            switchTicketTab: function () {},
            showSnackbar: snack,
            prepareExternalTicketModal: function () {},
            openTicketModal: function (ticketId) {
                var key = findWorkflowTicketKeyById(ticketId);
                if (key) openWorkflowBoardTicket(key);
            },
            copyAndPushExternalTicket: function () {
                snack("Use Ticket Boards to run actions on external tickets", "info");
            },
            openSendWorkflowModal: function (ticket, source) {
                if (!ticket) return;
                if (currentWorkflowId) {
                    var isLocal = !source || source === "database";
                    if (!isLocal) {
                        snack("Link external tickets from Ticket Boards", "info");
                        return;
                    }
                    api("PATCH", "/tickets/tickets/" + encodeURIComponent(ticket.id), {
                        linked_workflow_id: currentWorkflowId
                    }).then(function () {
                        snack("Ticket linked to workflow", "success");
                        var select = document.getElementById("wf-board-select");
                        if (select && select.value) loadWorkflowBoardTickets(select.value);
                        loadWorkflowTicketQueue();
                    }).catch(function (e) { snack(e.message || "Failed to link ticket", "error"); });
                    return;
                }
                snack("Select a workflow first", "error");
            },
            openCopyModal: function () {
                snack("Use Ticket Boards to copy external tickets", "info");
            },
            apiFetch: workflowApiFetch,
            sendTicketToProjectById: function (ticketId, btnEl) {
                if (btnEl) btnEl.disabled = true;
                workflowApiFetch("/api/tickets/tickets/" + ticketId + "/send-to-project", { method: "POST" })
                    .then(function (r) { snack((r && r.message) || "Sent to project", "success"); })
                    .catch(function (e) { snack(e.message || "Failed to send to project", "error"); })
                    .finally(function () { if (btnEl) btnEl.disabled = false; });
            },
            sendTicketToAgentById: function (ticketId, btnEl) {
                var ticket = null;
                Object.keys(workflowBoardTicketByKey || {}).some(function (key) {
                    var item = workflowBoardTicketByKey[key];
                    if (item && item.ticket && String(item.ticket.id) === String(ticketId)) {
                        ticket = item.ticket;
                        return true;
                    }
                    return false;
                });
                if (!ticket) {
                    snack("Could not find ticket to send to orchestrator", "error");
                    return;
                }
                var opt = currentWorkflowBoardOption();
                var isLocal = !!(opt && opt.source === "database");
                startWorkflowTicketDiscussion(ticket, isLocal);
            },
            pushTicketToCli: function (ticketId, btnEl) {
                if (btnEl) btnEl.disabled = true;
                openTicketInWorkflowCli(ticketId)
                    .then(function () { snack("Ticket opened in CLI", "success"); })
                    .catch(function (e) { snack(e.message || "Failed to open ticket in CLI", "error"); })
                    .finally(function () { if (btnEl) btnEl.disabled = false; });
            },
            reloadCurrentDatabaseBoard: function () {
                var select = document.getElementById("wf-board-select");
                if (select && select.value) loadWorkflowBoardTickets(select.value);
            },
            showKanbanConfirm: function (opts) {
                opts = opts || {};
                if (window.DecisionsAPI && typeof window.DecisionsAPI.confirm === "function") {
                    window.DecisionsAPI.confirm(opts).then(function (confirmed) {
                        if (confirmed && typeof opts.onConfirm === "function") opts.onConfirm();
                        if (!confirmed && typeof opts.onCancel === "function") opts.onCancel();
                    });
                }
            },
            hideKanbanConfirm: function () {},
            startTicketDiscussion: function (ticket, isLocal) {
                startWorkflowTicketDiscussion(ticket, isLocal);
            },
            getCurrentBoard: function () {
                var opt = currentWorkflowBoardOption();
                if (!opt) return null;
                return { id: opt.id, source: opt.source, extUrl: opt.url || "" };
            },
            getCurrentBoardData: function () {
                return workflowBoardRenderState.data || {};
            },
            addTicketToWorkflowQueue: function (ticket, isLocal, btnEl) {
                if (!ticket) return;
                var ticketKey = findWorkflowTicketKeyById(ticket.id);
                if (!ticketKey) {
                    var matchId = ticket.id || ticket.key || ticket.external_id || "";
                    Object.keys(workflowBoardTicketByKey || {}).forEach(function (key) {
                        if (ticketKey) return;
                        var item = workflowBoardTicketByKey[key];
                        if (!item || !item.ticket) return;
                        var itemId = item.ticket.id || item.ticket.key || item.ticket.external_id || "";
                        if (matchId && itemId && String(matchId) === String(itemId)) ticketKey = key;
                    });
                }
                addWorkflowBoardTicketToQueue(ticketKey, btnEl);
            },
            showRunPopover: function () {}
        });
        return workflowTicketUi;
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
        if (!externalId) return "";
        return String(selected.source).toLowerCase() + ":" + String(externalId);
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

    function workflowListDragHandleHtml(draggable, title) {
        title = title || "Drag";
        var gripSvg =
            '<svg width="10" height="16" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true">' +
            '<circle cx="2.5" cy="2" r="1.2"/><circle cx="7.5" cy="2" r="1.2"/>' +
            '<circle cx="2.5" cy="8" r="1.2"/><circle cx="7.5" cy="8" r="1.2"/>' +
            '<circle cx="2.5" cy="14" r="1.2"/><circle cx="7.5" cy="14" r="1.2"/>' +
            "</svg>";
        if (!draggable) {
            return '<span class="kb-ticket-list-drag-handle kb-ticket-list-drag-handle--static" aria-hidden="true">' + gripSvg + "</span>";
        }
        return '<span class="kb-ticket-list-drag-handle" title="' + esc(title) + '" aria-label="' + esc(title) + '">' + gripSvg + "</span>";
    }

    function workflowBoardGripMouseMove(evt) {
        if (!workflowBoardDragState) return;
        rememberWorkflowDragPoint(evt);
        positionWorkflowBoardDragGhost(evt);
        var overDropZone = workflowDropZoneContainsPoint(evt);
        workflowDropZoneHoverDepth = overDropZone ? 1 : 0;
        setWorkflowTicketDropTargetActive(overDropZone);
    }

    function workflowBoardGripMouseUp(evt) {
        if (!workflowBoardDragState) return;
        rememberWorkflowDragPoint(evt);
        var payload = workflowBoardDragState.payload;
        var shouldDrop = payload && workflowDropZoneContainsPoint(evt);
        finishWorkflowBoardMouseDrag();
        if (shouldDrop) handleWorkflowTicketDropPayload(payload);
    }

    function stopWorkflowBoardGripMouseTracking() {
        window.removeEventListener("mousemove", workflowBoardGripMouseMove, true);
        window.removeEventListener("mouseup", workflowBoardGripMouseEnd, true);
    }

    function workflowBoardGripMouseEnd(evt) {
        stopWorkflowBoardGripMouseTracking();
        workflowBoardGripMouseUp(evt);
    }

    function startWorkflowBoardGripMouseTracking() {
        stopWorkflowBoardGripMouseTracking();
        window.addEventListener("mousemove", workflowBoardGripMouseMove, true);
        window.addEventListener("mouseup", workflowBoardGripMouseEnd, true);
    }

    function bindWorkflowBoardGripMouseDrag(handle, row) {
        if (!handle || !row || handle.dataset.wfGripMouseBound === "1") return;
        handle.dataset.wfGripMouseBound = "1";
        handle.addEventListener("mousedown", function (evt) {
            if (evt.button !== 0 || workflowBoardDragState) return;
            if (row.dataset.draggable !== "true" || handle.disabled) return;
            var ticketKey = row.dataset.ticketKey || "";
            if (!ticketKey) return;
            evt.preventDefault();
            evt.stopPropagation();
            if (!beginWorkflowBoardMouseDrag(row, ticketKey, evt)) return;
            startWorkflowBoardGripMouseTracking();
        });
    }

    function upgradeWorkflowBoardTicketGrip(row, canDrag) {
        if (!row) return null;
        var handle = row.querySelector(".kb-ticket-list-drag-handle");
        if (!handle) return null;
        if (canDrag && handle.tagName !== "BUTTON") {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "kb-ticket-list-drag-handle wf-board-ticket-grip";
            btn.title = "Drag to workflow queue";
            btn.setAttribute("aria-label", "Drag to workflow queue");
            btn.innerHTML = handle.innerHTML;
            handle.parentNode.replaceChild(btn, handle);
            handle = btn;
        } else if (!canDrag && handle.tagName === "BUTTON") {
            var span = document.createElement("span");
            span.className = "kb-ticket-list-drag-handle kb-ticket-list-drag-handle--static";
            span.setAttribute("aria-hidden", "true");
            span.innerHTML = handle.innerHTML;
            handle.parentNode.replaceChild(span, handle);
            handle = span;
        }
        if (handle.tagName === "BUTTON") {
            handle.disabled = !canDrag;
            handle.classList.toggle("wf-board-ticket-grip", canDrag);
            handle.title = canDrag ? "Drag to workflow queue" : "";
            if (canDrag) bindWorkflowBoardGripMouseDrag(handle, row);
        }
        return handle;
    }

    function normalizeWorkflowPriority(value) {
        var pri = String(value || "medium").toLowerCase();
        if (pri !== "critical" && pri !== "high" && pri !== "low") pri = "medium";
        return pri;
    }

    function workflowComplexityNumeral(level) {
        level = normalizeComplexity(level);
        return level === "low" ? "I" : (level === "high" ? "III" : "II");
    }

    function workflowPriorityBadgeHtml(priority, opts) {
        opts = opts || {};
        var pri = normalizeWorkflowPriority(priority);
        var title = opts.title || (opts.interactive && !opts.locked ? "Change priority" : ("Priority: " + pri));
        var classes = "kb-metric-badge kb-pri-" + pri;
        if (opts.interactive) classes += " wf-queue-metric-badge";
        if (opts.interactive && !opts.locked) {
            return '<button type="button" class="' + classes + '" title="' + esc(title) + '" aria-label="' + esc(title) + '" aria-haspopup="menu" data-metric="priority" data-ticket-id="' + esc(opts.ticketId || "") + '">' + esc(pri) + "</button>";
        }
        return '<span class="' + classes + '" title="' + esc(title) + '">' + esc(pri) + "</span>";
    }

    function workflowComplexityBadgeHtml(complexity, opts) {
        opts = opts || {};
        var level = normalizeComplexity(complexity);
        var numeral = workflowComplexityNumeral(level);
        var title = opts.title || (opts.interactive && !opts.locked ? "Change complexity" : ("Complexity: " + level));
        var classes = "kb-metric-badge kb-complexity-numeral kb-cx-" + level;
        if (opts.interactive) classes += " wf-queue-metric-badge";
        if (opts.interactive && !opts.locked) {
            return '<button type="button" class="' + classes + '" title="' + esc(title) + '" aria-label="' + esc(title) + '" aria-haspopup="menu" data-metric="complexity" data-ticket-id="' + esc(opts.ticketId || "") + '">' + numeral + "</button>";
        }
        return '<span class="' + classes + '" title="' + esc(title) + '">' + numeral + "</span>";
    }

    function workflowQueueTicketFromApi(ticket) {
        ticket = ticket || {};
        return {
            id: ticket.id,
            title: ticket.title || "",
            description: ticket.description || "",
            priority: ticket.priority || "medium",
            complexity: ticket.complexity || "medium",
            position: ticket.position,
            workflow_queue_position: ticket.workflow_queue_position || 0,
            workflow_status: ticket.workflow_status || "",
            linked_workflow_id: ticket.linked_workflow_id,
            linked_project_id: ticket.linked_project_id || ticket.board_default_project_id || null,
            linked_project_name: ticket.linked_project_name || ticket.board_default_project_name || null,
            linked_project_folder: ticket.linked_project_folder || ticket.board_default_project_folder || null,
            board_default_project_id: ticket.board_default_project_id || null,
            board_default_project_name: ticket.board_default_project_name || null,
            board_default_project_folder: ticket.board_default_project_folder || null,
            board_id: ticket.board_id,
            board_name: ticket.board_name || "",
            external_source: ticket.external_source || "",
            external_id: ticket.external_id || "",
            source_provider: ticket.source_provider || "",
            source_external_id: ticket.source_external_id || "",
            cli_route: ticket.cli_route || null,
            time_spent: ticket.time_spent || "",
            time_estimate: ticket.time_estimate || ""
        };
    }

    function fetchWorkflowQueueTicketRecord(ticketId) {
        return api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId))
            .then(function (ticket) { return workflowQueueTicketFromApi(ticket); });
    }

    function appendWorkflowQueueTicket(ticket) {
        if (!ticket || !ticket.id) return;
        var exists = workflowQueueTickets.some(function (item) { return String(item.id) === String(ticket.id); });
        if (!exists) workflowQueueTickets.push(ticket);
        workflowQueueTickets.sort(function (a, b) {
            return (a.workflow_queue_position || 0) - (b.workflow_queue_position || 0) || (a.id || 0) - (b.id || 0);
        });
        renderWorkflowTickets(workflowQueueTickets);
        refreshWorkflowBoardTicketsFromQueue();
    }

    function workflowBoardTicketRowForKey(ticketKey) {
        var escapedKey = window.CSS && CSS.escape ? CSS.escape(ticketKey) : String(ticketKey).replace(/"/g, '\\"');
        return document.querySelector('.wf-board-ticket-row[data-ticket-key="' + escapedKey + '"]');
    }

    function isLocalDatabaseTicketId(value) {
        return /^\d+$/.test(String(value == null ? "" : value));
    }

    function isExternalWorkflowBoardPayload(payload) {
        if (!payload || payload.type !== "workflow-board-ticket") return false;
        if (payload.external_source) return true;
        var source = String(payload.source || "");
        if (source && source !== "database") return true;
        if (payload.external_id) return true;
        return !isLocalDatabaseTicketId(payload.ticket_id);
    }

    function workflowBoardTicketDragEmptyImage() {
        if (!workflowBoardTicketDragEmptyImage._img) {
            var img = new Image();
            img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
            workflowBoardTicketDragEmptyImage._img = img;
        }
        return workflowBoardTicketDragEmptyImage._img;
    }

    function positionWorkflowBoardDragGhost(evt) {
        if (!workflowBoardDragGhostEl || !evt) return;
        workflowBoardDragGhostEl.style.left = (evt.clientX + 12) + "px";
        workflowBoardDragGhostEl.style.top = (evt.clientY + 10) + "px";
    }

    function removeWorkflowBoardDragGhost() {
        if (workflowBoardDragGhostEl && workflowBoardDragGhostEl.parentNode) {
            workflowBoardDragGhostEl.parentNode.removeChild(workflowBoardDragGhostEl);
        }
        workflowBoardDragGhostEl = null;
    }

    function replaceWorkflowDragHandle(row) {
        if (!row) return null;
        var grip = row.querySelector(".kb-ticket-list-drag-handle");
        if (!grip || grip.dataset.wfGripReplaced === "1") return grip;
        var fresh = grip.cloneNode(true);
        grip.parentNode.replaceChild(fresh, grip);
        fresh.dataset.wfGripReplaced = "1";
        return fresh;
    }

    function workflowTicketDragBlockedTarget(evt) {
        if (!evt || !evt.target || !evt.target.closest) return false;
        return !!(
            evt.target.closest(".kb-ticket-list-actions")
            || evt.target.closest(".wf-workflow-queue-actions")
            || evt.target.closest(".wf-queue-metric-badge")
            || evt.target.closest("button:not(.kb-ticket-list-drag-handle)")
            || evt.target.closest("a")
        );
    }

    function workflowBoardDragImageOffset(row, evt) {
        var rect = row.getBoundingClientRect();
        var clientX = evt && typeof evt.clientX === "number" ? evt.clientX : rect.left + 16;
        var clientY = evt && typeof evt.clientY === "number" ? evt.clientY : rect.top + 16;
        return {
            x: Math.max(0, Math.min(rect.width, clientX - rect.left)),
            y: Math.max(0, Math.min(rect.height, clientY - rect.top)),
        };
    }

    function createWorkflowBoardDragGhost(row, evt) {
        if (!row) return;
        removeWorkflowBoardDragGhost();
        var rect = row.getBoundingClientRect();
        var ghost = row.cloneNode(true);
        ghost.setAttribute("aria-hidden", "true");
        ghost.classList.remove("dragging");
        ghost.style.margin = "0";
        ghost.style.width = rect.width + "px";
        ghost.style.maxWidth = rect.width + "px";
        var shell = document.createElement("div");
        shell.className = "wf-ticket-list-surface wf-board-ticket-drag-ghost-shell";
        shell.style.width = rect.width + "px";
        shell.appendChild(ghost);
        document.body.appendChild(shell);
        workflowBoardDragGhostEl = shell;
        positionWorkflowBoardDragGhost(evt);
    }

    function bindWorkflowTicketRowDragSources(row, handlers, options) {
        options = options || {};
        var gripOnly = !!options.gripOnly;
        if (!row || row.dataset.wfTicketDragBound === "1") return;
        row.dataset.wfTicketDragBound = "1";
        var grip = replaceWorkflowDragHandle(row);
        function onDragStart(evt) {
            if (workflowTicketDragBlockedTarget(evt)) {
                evt.preventDefault();
                return;
            }
            if (handlers.onDragStart) handlers.onDragStart(evt, row);
        }
        function onDrag(evt) {
            rememberWorkflowDragPoint(evt);
            positionWorkflowBoardDragGhost(evt);
            if (handlers.onDrag) handlers.onDrag(evt, row);
        }
        function onDragEnd(evt) {
            row.classList.remove("dragging");
            removeWorkflowBoardDragGhost();
            if (handlers.onDragEnd) handlers.onDragEnd(evt, row);
        }
        row.draggable = false;
        if (!gripOnly) {
            row.draggable = true;
            row.addEventListener("dragstart", onDragStart);
            row.addEventListener("drag", onDrag);
            row.addEventListener("dragend", onDragEnd);
        }
        if (grip) {
            grip.draggable = !!options.draggable;
            if (!options.draggable) return;
            grip.addEventListener("dragstart", function (evt) {
                evt.stopPropagation();
                onDragStart(evt);
            });
            grip.addEventListener("drag", onDrag);
            grip.addEventListener("dragend", onDragEnd);
        }
    }

    function workflowExternalLinkKeyFromTicketRecord(ticket) {
        if (!ticket) return "";
        var source = ticket.external_source || ticket.source_provider || "";
        var externalId = ticket.external_id || ticket.source_external_id || "";
        if (!source || !externalId) return "";
        return String(source).toLowerCase() + ":" + String(externalId);
    }

    function isBoardTicketQueuedInCurrentWorkflow(item) {
        var ticket = item && item.ticket;
        if (!ticket) return false;
        var queuedById = (workflowQueueTickets || []).some(function (queued) {
            return ticket.id != null && queued.id != null && String(queued.id) === String(ticket.id);
        });
        if (queuedById) return true;
        if (item.selected && item.selected.source === "database" && ticket.id != null) {
            var localKey = "database:" + String(ticket.id);
            if (workflowLinkedLocalTicketKeys[localKey]) return true;
        }
        var externalLinkKey = workflowTicketExternalLinkKey(item.selected, ticket);
        return !!(externalLinkKey && workflowLinkedExternalTicketKeys[externalLinkKey]);
    }

    function workflowBoardTicketLinkState(item) {
        var ticket = item.ticket;
        var selected = item.selected;
        var externalLinkKey = workflowTicketExternalLinkKey(selected, ticket);
        var localLinkKey = ticket.id ? ("database:" + String(ticket.id)) : "";
        var isExternallyLinked = !!(externalLinkKey && workflowLinkedExternalTicketKeys[externalLinkKey]);
        var isPendingLink = !!((externalLinkKey && workflowPendingTicketLinks[externalLinkKey]) || (localLinkKey && workflowPendingTicketLinks[localLinkKey]));
        var isQueuedInCurrentWorkflow = isBoardTicketQueuedInCurrentWorkflow(item);
        var linkedWorkflowId = ticket.linked_workflow_id;
        var linkedToCurrentWorkflow = !!(
            currentWorkflowId
            && linkedWorkflowId != null
            && String(linkedWorkflowId) === String(currentWorkflowId)
        );
        var isLinkedToWorkflow = linkedToCurrentWorkflow || isExternallyLinked || isQueuedInCurrentWorkflow;
        var boardHasProject = !!(selected && selected.default_project_id);
        var hasTicketIdentity = !!(ticket.id || ticket.key || ticket.external_id);
        return {
            isLinkedToWorkflow: isLinkedToWorkflow,
            isPendingLink: isPendingLink,
            blockedByMissingProject: !isLinkedToWorkflow && !isPendingLink && !boardHasProject,
            canDragToWorkflow: !!(hasTicketIdentity && !isLinkedToWorkflow && !isPendingLink && boardHasProject),
        };
    }

    function refreshWorkflowBoardTicketsFromQueue() {
        rebuildWorkflowQueueExternalLinkIndex();
        Object.keys(workflowBoardTicketByKey || {}).forEach(function (ticketKey) {
            var item = workflowBoardTicketByKey[ticketKey];
            if (!item || !item.ticket) return;
            if (!isBoardTicketQueuedInCurrentWorkflow(item)) {
                var linkedWorkflowId = item.ticket.linked_workflow_id;
                if (
                    currentWorkflowId
                    && linkedWorkflowId != null
                    && String(linkedWorkflowId) === String(currentWorkflowId)
                ) {
                    item.ticket.linked_workflow_id = null;
                }
            }
            syncWorkflowBoardTicketRowUi(ticketKey);
        });
        refreshWorkflowLaneAddAllButtons();
    }

    function rebuildWorkflowQueueExternalLinkIndex() {
        workflowLinkedExternalTicketKeys = {};
        workflowLinkedLocalTicketKeys = {};
        (workflowQueueTickets || []).forEach(function (ticket) {
            var source = ticket.external_source || ticket.source_provider || "";
            var externalId = ticket.external_id || ticket.source_external_id || "";
            if (source && externalId) {
                var normalizedSource = String(source).toLowerCase();
                var linkKey = normalizedSource + ":" + String(externalId);
                if (normalizedSource === "database") workflowLinkedLocalTicketKeys[linkKey] = true;
                else workflowLinkedExternalTicketKeys[linkKey] = true;
            }
        });
    }

    function finishWorkflowBoardMouseDrag() {
        var state = workflowBoardDragState;
        stopWorkflowBoardGripMouseTracking();
        workflowBoardDragState = null;
        workflowBoardDragPayload = null;
        workflowLastDragPoint = null;
        document.body.classList.remove("wf-board-pointer-dragging");
        if (state && state.row) state.row.classList.remove("dragging");
        removeWorkflowBoardDragGhost();
        setWorkflowTicketDropTargetActive(false);
        workflowDropZoneHoverDepth = 0;
    }

    function beginWorkflowBoardMouseDrag(row, ticketKey, evt) {
        var item = workflowBoardTicketByKey[ticketKey];
        if (!item || !item.ticket) return false;
        var state = workflowBoardTicketLinkState(item);
        if (!state.canDragToWorkflow) return false;
        if (!item.selected || !item.selected.default_project_id) {
            snack("Link this board to a project before adding tickets to a workflow", "error");
            return false;
        }
        var payload = workflowBoardTicketDropPayload(ticketKey);
        if (!payload) return false;
        if (!hasWorkflowQueueTarget()) {
            snack("Select a workflow before adding tickets to its queue", "error");
            return false;
        }
        switchTab("tickets", { persist: false });
        workflowBoardDragState = { row: row, ticketKey: ticketKey, payload: payload };
        workflowBoardDragPayload = payload;
        workflowLastDragPoint = null;
        row.classList.add("dragging");
        createWorkflowBoardDragGhost(row, evt);
        document.body.classList.add("wf-board-pointer-dragging");
        workflowDropZoneHoverDepth = workflowDropZoneContainsPoint(evt) ? 1 : 0;
        setWorkflowTicketDropTargetActive(workflowDropZoneHoverDepth > 0);
        return true;
    }

    function initWorkflowBoardTicketMouseDrag() {
        if (workflowBoardMouseDragBound) return;
        workflowBoardMouseDragBound = true;
        document.addEventListener("selectstart", function (evt) {
            if (workflowBoardDragState) evt.preventDefault();
        });
    }

    function attachWorkflowQueueTicketDrag(row) {
        if (!row || row.dataset.wfQueueDragBound === "1") return;
        row.dataset.wfQueueDragBound = "1";
        bindWorkflowTicketRowDragSources(row, {
            onDragStart: function (evt) {
                workflowQueueDragTicketId = row.dataset.ticketId || "";
                if (!workflowQueueDragTicketId) {
                    evt.preventDefault();
                    return;
                }
                workflowLastDragPoint = null;
                row.classList.add("dragging");
                createWorkflowBoardDragGhost(row, evt);
                evt.dataTransfer.effectAllowed = "move";
                evt.dataTransfer.setData("text/plain", workflowQueueDragTicketId);
            },
            onDragEnd: function () {
                workflowQueueDragTicketId = null;
            }
        }, { gripOnly: true, draggable: true });
    }

    function syncWorkflowBoardTicketRowUi(ticketKey, rowEl) {
        var item = workflowBoardTicketByKey[ticketKey];
        var row = rowEl || workflowBoardTicketRowForKey(ticketKey);
        if (!item || !item.ticket || !row) return;
        var state = workflowBoardTicketLinkState(item);
        row.dataset.linkedWorkflow = state.isLinkedToWorkflow ? "true" : "false";
        row.classList.toggle("wf-board-ticket-linked", state.isLinkedToWorkflow);
        row.classList.toggle("wf-board-ticket-pending", state.isPendingLink);
        row.classList.toggle("opacity-55", state.blockedByMissingProject);
        var handle = row.querySelector(".kb-ticket-list-drag-handle");
        row.draggable = false;
        row.dataset.draggable = state.canDragToWorkflow ? "true" : "false";
        upgradeWorkflowBoardTicketGrip(row, state.canDragToWorkflow);
        var addBtn = row.querySelector(".kb-act-add-workflow");
        if (addBtn) {
            var canAdd = !!(hasWorkflowQueueTarget() && state.canDragToWorkflow);
            var addWrap = addBtn.closest(".kb-card-action-tip");
            var showAdd = !!(hasWorkflowQueueTarget() && !state.isLinkedToWorkflow && !state.isPendingLink);
            if (addWrap) {
                addWrap.hidden = !showAdd;
                addWrap.style.display = showAdd ? "" : "none";
            } else {
                addBtn.hidden = !showAdd;
                addBtn.style.display = showAdd ? "" : "none";
            }
            addBtn.disabled = !canAdd;
            addBtn.title = canAdd
                ? "Add to workflow queue"
                : (state.isPendingLink
                    ? "Adding to workflow queue…"
                    : (state.isLinkedToWorkflow ? "Already in workflow queue" : "Cannot add to workflow"));
            addBtn.setAttribute("aria-label", addBtn.title);
        }
    }

    function refreshWorkflowLaneAddAllButtons() {
        var list = document.getElementById("wf-board-ticket-list");
        if (!list) return;
        list.querySelectorAll(".wf-lane-add-all-board-tickets").forEach(function (btn) {
            var laneId = btn.dataset.laneId || "";
            btn.disabled = !hasWorkflowQueueTarget() || getAddableBoardTicketItems(laneId).length === 0;
        });
    }

    function rememberWorkflowBoardTicketSource(localTicketId, payload) {
        if (!localTicketId || !payload || !payload.ticket_key) return;
        workflowLocalTicketSourceKeys[String(localTicketId)] = String(payload.ticket_key);
    }

    function applyWorkflowBoardTicketLinkedState(ticketKey, payload) {
        if (!ticketKey) return;
        var item = workflowBoardTicketByKey[ticketKey];
        if (item && item.ticket && currentWorkflowId) {
            item.ticket.linked_workflow_id = parseInt(currentWorkflowId, 10);
        }
        if (payload) {
            var externalKey = workflowPayloadLinkKey(payload);
            if (externalKey) workflowLinkedExternalTicketKeys[externalKey] = true;
        }
        syncWorkflowBoardTicketRowUi(ticketKey);
        refreshWorkflowLaneAddAllButtons();
    }

    function restoreWorkflowBoardTicketAfterQueueRemove(ticketId, externalKey) {
        if (ticketId) {
            delete workflowPendingTicketLinks["database:" + String(ticketId)];
            delete workflowLocalTicketSourceKeys[String(ticketId)];
        }
        if (externalKey) {
            var normalizedExternalKey = String(externalKey).toLowerCase();
            delete workflowLinkedExternalTicketKeys[normalizedExternalKey];
            Object.keys(workflowLinkedExternalTicketKeys).forEach(function (key) {
                if (String(key).toLowerCase() === normalizedExternalKey) {
                    delete workflowLinkedExternalTicketKeys[key];
                }
            });
        }
        refreshWorkflowBoardTicketsFromQueue();
    }

    function workflowBoardTicketDropPayload(ticketKey) {
        var item = workflowBoardTicketByKey[ticketKey];
        if (!item || !item.ticket || !item.selected) return null;
        var ticket = item.ticket;
        var selected = item.selected;
        var isExternal = selected.source !== "database";
        var destinationBoard = isExternal ? workflowDatabaseBoardForProject(selected.default_project_id) : null;
        return {
            type: "workflow-board-ticket",
            ticket_key: ticketKey,
            ticket_id: ticket.id,
            title: ticket.title || ticket.name || "Untitled ticket",
            description: ticketTextValue(ticket.description || ticket.desc || ""),
            priority: ticket.priority || "medium",
            complexity: ticket.complexity || "",
            source: selected.source,
            external_source: isExternal ? selected.source : "",
            external_id: isExternal ? String(ticket.id || ticket.key || ticket.external_id || "") : "",
            external_url: ticket.url || ticket.external_url || "",
            media: Array.isArray(ticket.media) ? ticket.media : [],
            todos: Array.isArray(ticket.todos) ? ticket.todos : [],
            board_id: selected.source === "database" ? selected.id : selected.local_id,
            local_board_id: selected.local_id,
            destination_board_id: destinationBoard ? destinationBoard.id : null,
            default_project_id: selected.default_project_id || null
        };
    }

    function getAddableBoardTicketItems(laneId) {
        var items = [];
        Object.keys(workflowBoardTicketByKey || {}).forEach(function (ticketKey) {
            var item = workflowBoardTicketByKey[ticketKey];
            if (!item || !item.ticket || !item.selected) return;
            if (laneId != null && laneId !== "" && item.lane && String(item.lane.id) !== String(laneId)) return;
            if (!workflowBoardTicketLinkState(item).canDragToWorkflow) return;
            var payload = workflowBoardTicketDropPayload(ticketKey);
            if (!payload) return;
            items.push({
                ticketKey: ticketKey,
                ticket: item.ticket,
                selected: item.selected,
                payload: payload,
                lane: item.lane
            });
        });
        return items;
    }

    function bindWorkflowBoardListRow(row, ticket, lane, selected, board) {
        var ticketKey = workflowTicketKey(selected, lane, ticket);
        workflowBoardTicketByKey[ticketKey] = { ticket: ticket, lane: lane, selected: selected, board: board };
        row.classList.add("wf-board-ticket-row");
        row.dataset.ticketKey = ticketKey;
        syncWorkflowBoardTicketRowUi(ticketKey, row);
    }

    function refreshWorkflowBoardTicketDragBindings(rootEl) {
        var root = rootEl || document.getElementById("wf-board-ticket-list");
        if (!root) return;
        root.querySelectorAll(".wf-board-ticket-row").forEach(function (row) {
            var ticketKey = row.dataset.ticketKey || "";
            if (ticketKey) syncWorkflowBoardTicketRowUi(ticketKey, row);
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

    function ticketMetaField(label, value, isRaw) {
        return '<div class="kb-ticket-meta-field"><div class="kb-ticket-meta-label">' + esc(label) + '</div>' +
            '<div class="kb-ticket-meta-value">' + (isRaw ? (value || "") : esc(String(value || ""))) + "</div></div>";
    }

    function ticketLinkLooksPreviewable(url) {
        var u = String(url || "").toLowerCase();
        return !!u && (
            u.indexOf("/proxy-image?") >= 0 ||
            /\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/.test(u)
        );
    }

    function ticketModalLinkRow(link, isLocal) {
        var url = link.url || "";
        var label = esc(link.title || url || "Link");
        var preview = ticketLinkLooksPreviewable(url)
            ? '<a class="flex-shrink-0" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer"><img src="' + esc(url) + '" alt="' + label + '" class="h-12 w-16 rounded object-cover border border-white/10 bg-black/20" loading="lazy" onerror="this.remove()"></a>'
            : "";
        return '<div class="flex items-center gap-2 text-xs rounded border border-white/10 bg-[#152054]/70 px-2 py-1.5">' +
            preview +
            '<a class="text-blue-300 hover:text-blue-200 truncate flex-1" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>' +
            (isLocal && link.id ? '<button type="button" class="wf-ticket-delete-link text-red-400 hover:text-red-300 px-1" data-link-id="' + esc(link.id) + '" title="Delete link">&times;</button>' : "") +
            '</div>';
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

    function closeUiTasteFeedbackModal() {
        var modal = document.getElementById("wf-ui-feedback-modal");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
        pendingUiTasteFeedback = null;
    }

    function ensureUiTasteFeedbackModal() {
        var existing = document.getElementById("wf-ui-feedback-modal");
        if (existing) return existing;
        var shell = document.createElement("div");
        shell.id = "wf-ui-feedback-modal";
        shell.className = "hidden fixed inset-0 z-[100] items-center justify-center bg-black/70 p-4";
        shell.setAttribute("role", "dialog");
        shell.setAttribute("aria-modal", "true");
        shell.setAttribute("aria-labelledby", "wf-ui-feedback-title");
        shell.innerHTML = '' +
            '<div class="w-full max-w-lg rounded-xl border border-white/15 bg-[#111936] shadow-2xl">' +
                '<div class="flex items-center justify-between border-b border-white/10 px-4 py-3">' +
                    '<h3 id="wf-ui-feedback-title" class="text-sm font-semibold text-white">Teach interface standard</h3>' +
                    '<button type="button" id="wf-ui-feedback-close" class="text-xl leading-none text-gray-400 hover:text-white" aria-label="Close feedback dialog">&times;</button>' +
                '</div>' +
                '<div class="space-y-3 p-4">' +
                    '<p id="wf-ui-feedback-help" class="text-xs text-gray-400"></p>' +
                    '<label class="block text-xs text-gray-300" for="wf-ui-feedback-reason">What should this and future projects learn?</label>' +
                    '<textarea id="wf-ui-feedback-reason" rows="5" class="w-full rounded-md border border-white/15 bg-[#0d1333] px-3 py-2 text-sm text-white outline-none focus:border-[#f97316]" placeholder="Describe the reusable interface standard, not only this one symptom."></textarea>' +
                    '<div id="wf-ui-feedback-baseline-fields" class="hidden grid gap-2 sm:grid-cols-2">' +
                        '<label class="text-xs text-gray-300">Baseline name<input id="wf-ui-feedback-baseline-name" class="mt-1 w-full rounded border border-white/15 bg-[#0d1333] px-2 py-1.5 text-sm text-white" value="Approved UI"></label>' +
                        '<label class="text-xs text-gray-300">Screen name<input id="wf-ui-feedback-screen-name" class="mt-1 w-full rounded border border-white/15 bg-[#0d1333] px-2 py-1.5 text-sm text-white"></label>' +
                    '</div>' +
                    '<p id="wf-ui-feedback-error" class="hidden text-xs text-red-300"></p>' +
                '</div>' +
                '<div class="flex justify-end gap-2 border-t border-white/10 px-4 py-3">' +
                    '<button type="button" id="wf-ui-feedback-cancel" class="rounded border border-white/20 px-3 py-1.5 text-xs text-gray-300 hover:bg-white/10">Cancel</button>' +
                    '<button type="button" id="wf-ui-feedback-save" class="rounded bg-[#f97316] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#ea580c]">Save standard</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(shell);
        document.getElementById("wf-ui-feedback-close").onclick = closeUiTasteFeedbackModal;
        document.getElementById("wf-ui-feedback-cancel").onclick = closeUiTasteFeedbackModal;
        shell.addEventListener("click", function (evt) {
            if (evt.target === shell) closeUiTasteFeedbackModal();
        });
        document.getElementById("wf-ui-feedback-save").onclick = submitUiTasteFeedbackFromModal;
        return shell;
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
        var saveAsBaseline = button.dataset.uiSaveBaseline === "true";
        pendingUiTasteFeedback = {
            button: button,
            workflowId: workflowId,
            runId: runId,
            label: label,
            metadata: metadata,
            screenshotPaths: screenshotPaths,
            saveAsBaseline: saveAsBaseline
        };
        var modal = ensureUiTasteFeedbackModal();
        var reasonEl = document.getElementById("wf-ui-feedback-reason");
        var helpEl = document.getElementById("wf-ui-feedback-help");
        var baselineFields = document.getElementById("wf-ui-feedback-baseline-fields");
        var screenName = document.getElementById("wf-ui-feedback-screen-name");
        var errorEl = document.getElementById("wf-ui-feedback-error");
        if (reasonEl) reasonEl.value = "";
        if (helpEl) helpEl.textContent = label === "approved"
            ? "Describe what worked so it can become a reusable standard."
            : "Explain the reusable principle that should prevent this problem in future projects.";
        if (baselineFields) baselineFields.classList.toggle("hidden", !saveAsBaseline);
        if (screenName) screenName.value = "Run " + runId;
        if (errorEl) errorEl.classList.add("hidden");
        modal.classList.remove("hidden");
        modal.classList.add("flex");
        if (reasonEl) reasonEl.focus();
    }

    function submitUiTasteFeedbackFromModal() {
        var pending = pendingUiTasteFeedback;
        if (!pending) return;
        var reasonEl = document.getElementById("wf-ui-feedback-reason");
        var reason = reasonEl ? reasonEl.value.trim() : "";
        var errorEl = document.getElementById("wf-ui-feedback-error");
        if (!reason) {
            if (errorEl) {
                errorEl.textContent = "Describe the reusable standard before saving.";
                errorEl.classList.remove("hidden");
            }
            if (reasonEl) reasonEl.focus();
            return;
        }
        var saveButton = document.getElementById("wf-ui-feedback-save");
        var baselineNameEl = document.getElementById("wf-ui-feedback-baseline-name");
        var screenNameEl = document.getElementById("wf-ui-feedback-screen-name");
        if (saveButton) saveButton.disabled = true;
        if (pending.button) pending.button.disabled = true;
        api("POST", "/workflows/" + pending.workflowId + "/runs/" + pending.runId + "/ui-feedback", {
            label: pending.label,
            reason: reason,
            ticket_id: pending.metadata.ticket_id || null,
            board_id: pending.metadata.board_id || null,
            project_id: pending.metadata.project_id || null,
            execution_session_id: pending.metadata.execution_session_id || null,
            screenshot_paths: pending.screenshotPaths,
            save_as_visual_baseline: pending.saveAsBaseline,
            visual_baseline_name: pending.saveAsBaseline && baselineNameEl ? baselineNameEl.value.trim() || "Approved UI" : null,
            baseline_screen_name: pending.saveAsBaseline && screenNameEl ? screenNameEl.value.trim() || ("Run " + pending.runId) : null
        })
            .then(function (data) {
                snack(workflowFeedbackText(data, "UI feedback recorded"));
                loadOrchestratorTimeline({ quiet: true });
                closeUiTasteFeedbackModal();
            })
            .catch(function (e) {
                snack(workflowErrorText(e, "Failed to record UI feedback"), "error");
            })
            .finally(function () {
                if (saveButton) saveButton.disabled = false;
                if (pending.button) pending.button.disabled = false;
            });
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
        el.className = "fixed bottom-6 left-1/2 -translate-x-1/2 px-5 py-3 rounded-lg shadow-lg text-white font-medium text-sm transition-opacity duration-300 " +
            (type === "error" ? "bg-red-600" : "bg-green-600");
        el.style.zIndex = "2147483647";
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
        workflowTicketModalDetailsHeight = 0;
        var title = ticket.title || ticket.name || "Untitled ticket";
        var description = ticketTextValue(ticket.description || ticket.desc || "");
        var todos = Array.isArray(ticket.todos) ? ticket.todos : [];
        var links = Array.isArray(ticket.links) ? ticket.links : [];
        var files = Array.isArray(ticket.files) ? ticket.files : [];
        var media = Array.isArray(ticket.media) ? ticket.media : [];
        var isLocal = !!(ticket.id && (!context.selected || !context.selected.source || context.selected.source === "database"));
        var sourceName = String(ticket.external_source || ticket.source_provider || "").trim().toLowerCase();
        var sourceUrl = String(ticket.external_url || ticket.source_url || ticket.url || "").trim();
        var canDeleteTicket = isLocal && !sourceName;
        workflowTicketModalState = {
            ticket: ticket,
            context: context,
            isLocal: isLocal,
            canDelete: canDeleteTicket,
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
        document.getElementById("kb-modal-delete").classList.toggle("hidden", !canDeleteTicket);
        document.getElementById("kb-modal-upload-btn").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-add-link").classList.toggle("hidden", !isLocal);
        document.getElementById("kb-modal-add-todo").classList.toggle("hidden", !isLocal);
        var urlLink = document.getElementById("kb-modal-url-link");
        if (urlLink) {
            urlLink.classList.toggle("hidden", !sourceUrl);
            urlLink.href = sourceUrl || "#";
            urlLink.innerHTML = '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAi0lEQVR42u3WQQqDQBAAwXlFPprXe0quQVRyCGjsKvDoQo/u6gwAAMCJlufj9YsrP4T8m5DfDvkzIX8wGoAt4BD0GfQj5Ff45vFH9yTij+7NxO+tkYrfWisXv14zGf+5djY+/eTFixcvXrx48eLFixcvXvzVBjB3lY7/ZghTkY7fGsJUpeMBAAD+zRvbrtesCjwpyAAAAABJRU5ErkJggg==" alt="" aria-hidden="true">';
            var sourceLabel = sourceName ? ("Open " + sourceName + " ticket") : "Open source ticket";
            urlLink.title = sourceLabel;
            urlLink.setAttribute("aria-label", sourceLabel);
        }
        document.getElementById("kb-modal-links").innerHTML = links.length ? links.map(function (link) {
            return ticketModalLinkRow(link, isLocal);
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
        var auditSummary = document.getElementById("kb-modal-audit-summary");
        var auditRuns = document.getElementById("kb-modal-audit-runs");
        var auditEntries = document.getElementById("kb-modal-audit-entries");
        if (auditSummary) {
            auditSummary.innerHTML = '<div class="p-2 bg-[#152054] border border-white/10 rounded"><div class="text-[11px] text-gray-400">Report</div><div class="text-sm text-white">' + (ticket.audit_entries && ticket.audit_entries.length ? esc(String(ticket.audit_entries.length)) : "0") + '</div></div>';
        }
        if (auditRuns) {
            auditRuns.innerHTML = '<div class="text-xs text-gray-500">No run report loaded here.</div>';
        }
        if (auditEntries) {
            auditEntries.innerHTML = (ticket.audit_entries || []).map(function (entry) {
                return '<div class="p-2 bg-[#152054] border border-white/10 rounded text-xs text-gray-300">' + esc(entry.summary || entry.status || "Audit entry") + '</div>';
            }).join("") || '<div class="text-xs text-gray-500">No audit entries yet.</div>';
        }
        bindWorkflowTicketModalDynamicControls();
        switchWorkflowTicketTab("details");
        modal.classList.remove("hidden");
        requestAnimationFrame(function () { syncWorkflowTicketModalHeights(); });
    }

    function openWorkflowBoardTicket(key) {
        var item = workflowBoardTicketByKey[key];
        if (!item || !item.ticket) return;
        if (item.selected && item.selected.source === "database" && item.ticket.id != null) {
            api("GET", "/tickets/tickets/" + encodeURIComponent(item.ticket.id))
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
        requestAnimationFrame(function () { syncWorkflowTicketModalHeights(); });
    }

    function syncWorkflowTicketModalHeights() {
        var modal = document.getElementById("kb-ticket-modal");
        if (!modal) return;
        var body = modal.querySelector(".kb-ticket-modal-body");
        if (!body) return;
        var details = modal.querySelector("#kb-tm-tab-details");
        if (!details) return;
        var wasHidden = details.classList.contains("hidden");
        if (wasHidden) details.classList.remove("hidden");
        var height = details.scrollHeight;
        if (wasHidden) details.classList.add("hidden");
        if (!height && workflowTicketModalDetailsHeight) {
            height = workflowTicketModalDetailsHeight;
        }
        if (!height) return;
        workflowTicketModalDetailsHeight = height;
        body.style.height = height + "px";
        body.style.minHeight = height + "px";
        body.style.maxHeight = height + "px";
        modal.querySelectorAll(".kb-tm-pane").forEach(function (pane) {
            pane.style.height = height + "px";
            pane.style.minHeight = height + "px";
            pane.style.maxHeight = height + "px";
            pane.style.overflowY = "auto";
        });
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
        return api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId))
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
        api("POST", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/links", { title: title, url: url })
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
        api("POST", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/todos", { text: text })
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
            uploads.push(fetch(API + "/tickets/tickets/" + encodeURIComponent(ticketId) + "/files", { method: "POST", body: form }).then(function (r) {
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
                api("DELETE", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/links/" + encodeURIComponent(btn.dataset.linkId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete link", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-delete-file").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("DELETE", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/files/" + encodeURIComponent(btn.dataset.fileId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete file", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-toggle-todo").forEach(function (input) {
            input.addEventListener("change", function () {
                api("PUT", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/todos/" + encodeURIComponent(input.dataset.todoId || ""), { done: input.checked })
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to update to-do", "error"); });
            });
        });
        document.querySelectorAll(".wf-ticket-delete-todo").forEach(function (btn) {
            btn.addEventListener("click", function () {
                api("DELETE", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/todos/" + encodeURIComponent(btn.dataset.todoId || ""))
                    .then(reloadWorkflowTicketModal)
                    .catch(function (e) { snack(e.message || "Failed to delete to-do", "error"); });
            });
        });
    }

    var WORKFLOW_EXEC_ROUTE_DEFAULTS = {
        low: { backend: "cursor", model: "auto" },
        medium: { backend: "codex", model: "auto" },
        high: { backend: "codex", model: "gpt-5.3-codex" }
    };
    var WORKFLOW_EXEC_BACKEND_OPTIONS = [
        { id: "codex", label: "Codex CLI" },
        { id: "claude_code", label: "Claude Code" },
        { id: "opencode", label: "OpenCode" },
        { id: "kiro", label: "Kiro CLI" },
        { id: "cursor", label: "Cursor CLI" },
        { id: "cline", label: "Cline" },
        { id: "hermes_agent", label: "External agent (optional)" },
        { id: "pi", label: "Pi" }
    ];
    var WORKFLOW_EXEC_IDE_BACKEND_IDS = { cursor_ide: true, codex_ide: true };
    var WORKFLOW_CLI_BACKEND_ORDER = {
        codex: 0,
        claude_code: 1,
        opencode: 2,
        kiro: 3,
        pi: 99
    };

    function workflowExecBackendIsIde(backendId) {
        return !!WORKFLOW_EXEC_IDE_BACKEND_IDS[(backendId || "").trim()];
    }

    function workflowExecNormalizeCliBackend(backendId) {
        backendId = String(backendId || "").trim();
        if (backendId === "cursor_ide") return "cursor";
        if (backendId === "codex_ide") return "codex";
        return backendId;
    }
    function workflowExecRouteLevels() {
        return ["low", "medium", "high"];
    }

    function workflowCliBackendLabel(label) {
        return String(label || "").replace(/\s+CLI$/i, "").trim();
    }

    function workflowExecRoutePrefix(root) {
        if (root && (root.id === "sr-llm-modal" || (root.closest && root.closest("#sr-llm-modal")))) {
            return "wf-global-exec";
        }
        return "wf-global-exec";
    }

    function workflowExecRouteEl(root, level, part) {
        var prefix = workflowExecRoutePrefix(root);
        return root.querySelector("#" + prefix + "-" + level + "-" + part);
    }

    function workflowExecRouteModelLabel(model) {
        if (!model) return "";
        if (typeof model === "string") return model;
        return model.name || model.id || model.model || "";
    }

    function workflowExecRouteModelValue(model) {
        if (!model) return "";
        if (typeof model === "string") return model;
        return model.id || model.model || model.name || "";
    }

    function cliModelGroupsFromList(models) {
        var groups = {};
        (models || []).forEach(function (m) {
            if (!m) return;
            var p = m.provider || "other";
            if (!groups[p]) groups[p] = [];
            groups[p].push(m);
        });
        return groups;
    }

    function appendCliModelOption(parent, model, selectedId) {
        var opt = document.createElement("option");
        opt.value = model.id;
        opt.dataset.provider = model.provider || "";
        opt.dataset.scope = model.scope || "available";
        opt.dataset.free = model.free ? "true" : "false";
        var flags = [];
        if (model.scope === "scoped") flags.push("scoped");
        if (model.free) flags.push("free");
        if (model.tier) flags.push(model.tier);
        opt.textContent = (model.name || model.id) + (flags.length ? " - " + flags.join(", ") : "");
        if (model.supports_chat === false) opt.disabled = true;
        if (model.id === selectedId) opt.selected = true;
        parent.appendChild(opt);
    }

    function populateCliModelSelect(select, data, options) {
        if (!select) return;
        options = options || {};
        var models = Array.isArray(data.models) ? data.models : [];
        var current = (data.current_model || "").trim();
        var includeAuto = options.includeAuto !== false;
        select.innerHTML = "";
        if (includeAuto) {
            var autoOpt = document.createElement("option");
            autoOpt.value = "auto";
            autoOpt.textContent = "Auto";
            autoOpt.dataset.provider = "";
            select.appendChild(autoOpt);
        }
        var groups = cliModelGroupsFromList(models);
        Object.keys(groups).sort().forEach(function (prov) {
            var og = document.createElement("optgroup");
            og.label = prov.charAt(0).toUpperCase() + prov.slice(1);
            groups[prov].forEach(function (m) {
                appendCliModelOption(og, m, current);
            });
            select.appendChild(og);
        });
        if (current && !models.some(function (m) { return m.id === current; })) {
            var cur = document.createElement("option");
            cur.value = current;
            cur.dataset.provider = data.current_provider || "";
            cur.textContent = current;
            cur.selected = true;
            select.insertBefore(cur, select.firstChild);
        }
        if (includeAuto) {
            var hasCurrent = current && Array.prototype.some.call(select.options, function (o) { return o.value === current; });
            select.value = hasCurrent ? current : "auto";
        } else if (current) {
            select.value = current;
        }
    }

    function selectedCliModelProvider(select) {
        if (!select || select.selectedIndex < 0) return "";
        var opt = select.options[select.selectedIndex];
        return opt ? String(opt.dataset.provider || "").trim() : "";
    }

    function workflowStepBackendOptionsHtml(selected) {
        selected = String(selected || "").trim();
        var html = '<option value="">Use workflow route</option>';
        WORKFLOW_EXEC_BACKEND_OPTIONS.forEach(function (opt) {
            if (opt.id === "cursor_ide" || opt.id === "codex_ide") return;
            html += '<option value="' + esc(opt.id) + '"' + (selected === opt.id ? " selected" : "") + '>' + esc(workflowCliBackendLabel(opt.label)) + '</option>';
        });
        return html;
    }

    function loadStepCliModels(container, selectedModel) {
        var backendSel = container.querySelector(".sf-cli-backend");
        var modelSel = container.querySelector(".sf-cli-model");
        var hint = container.querySelector(".sf-cli-model-hint");
        if (!backendSel || !modelSel) return Promise.resolve();
        var backendId = (backendSel.value || (typeof workflowCliActiveBackend === "function" ? workflowCliActiveBackend() : "") || "pi").trim();
        if (workflowExecBackendIsIde(backendId)) {
            modelSel.innerHTML = '<option value="">Model selected in IDE</option>';
            modelSel.disabled = true;
            if (hint) hint.textContent = "IDE handoff chooses the model inside the editor.";
            return Promise.resolve();
        }
        modelSel.disabled = true;
        modelSel.innerHTML = '<option value="auto">Loading...</option>';
        var params = "backend_id=" + encodeURIComponent(backendId);
        var projectId = workflowBoardProjectId();
        if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
        return api("GET", "/projects/cli-models?" + params)
            .then(function (data) {
                populateCliModelSelect(modelSel, {
                    models: data.models || [],
                    current_model: selectedModel || "auto",
                    current_provider: data.current_provider || ""
                }, { includeAuto: true });
                modelSel.disabled = false;
                if (hint) hint.textContent = data.message || "Auto follows workflow policy unless this step picks a concrete model.";
            })
            .catch(function (e) {
                modelSel.innerHTML = '<option value="auto">Auto</option>';
                modelSel.disabled = false;
                if (hint) hint.textContent = (e && e.message) || "Could not load models for this backend.";
            });
    }

    function providerForCliModelBackend(backendId, provider) {
        backendId = String(backendId || "pi").trim();
        if (backendId === "pi" || backendId === "opencode") {
            return provider || "ollama";
        }
        return backendId;
    }

    function workflowExecRouteBackendOptionsHtml(readyMap) {
        readyMap = readyMap || {};
        return WORKFLOW_EXEC_BACKEND_OPTIONS.filter(function (item) {
            return !workflowExecBackendIsIde(item.id);
        }).map(function (item) {
            var ready = readyMap[item.id];
            var suffix = ready === false ? " (setup required)" : "";
            return '<option value="' + esc(item.id) + '">' + esc(workflowCliBackendLabel(item.label) + suffix) + "</option>";
        }).join("");
    }

    function workflowExecCliBackendOptionsHtml(readyMap, includeEmpty) {
        readyMap = readyMap || {};
        var html = includeEmpty ? '<option value="">None</option>' : "";
        WORKFLOW_EXEC_BACKEND_OPTIONS.forEach(function (item) {
            if (workflowExecBackendIsIde(item.id)) return;
            var ready = readyMap[item.id];
            var suffix = ready === false ? " (setup required)" : "";
            html += '<option value="' + esc(item.id) + '">' + esc(workflowCliBackendLabel(item.label) + suffix) + "</option>";
        });
        return html;
    }

    function workflowExecRouteHtml(rootOrPrefix) {
        var prefix = typeof rootOrPrefix === "string" ? rootOrPrefix : workflowExecRoutePrefix(rootOrPrefix);
        var backendOptions = workflowExecRouteBackendOptionsHtml();
        var fallbackBackendOptions = workflowExecCliBackendOptionsHtml({}, true);
        return workflowExecRouteLevels().map(function (level) {
            var label = level.charAt(0).toUpperCase() + level.slice(1);
            return '<div class="wf-exec-route-block space-y-2" data-level="' + level + '">' +
                '<div class="grid gap-2 items-center" style="grid-template-columns: 7.5rem minmax(0, 1fr) minmax(0, 1fr) auto auto;">' +
                    '<label class="text-xs text-gray-400 font-medium">' + label + "</label>" +
                    '<select id="' + prefix + "-" + level + '-backend" class="wf-exec-backend w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs focus:border-[#f97316] focus:outline-none" data-level="' + level + '">' +
                        backendOptions +
                    "</select>" +
                    '<select id="' + prefix + "-" + level + '-model" class="wf-exec-model w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs focus:border-[#f97316] focus:outline-none" data-level="' + level + '">' +
                        '<option value="auto">Auto</option>' +
                    "</select>" +
                    '<label class="wf-exec-auto-label" title="Let preflight choose the model for this complexity">' +
                        '<input id="' + prefix + "-" + level + '-auto" type="checkbox" class="wf-exec-auto accent-[#f97316]" data-level="' + level + '">' +
                        '<span>Auto</span>' +
                    '</label>' +
                    '<button type="button" id="' + prefix + "-" + level + '-codex-cog" class="wf-exec-codex-cog inline-flex h-8 w-8 items-center justify-center rounded border border-white/20 text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed" data-level="' + level + '" title="Codex CLI preferences (intelligence &amp; speed)" aria-label="Codex CLI preferences">&#9881;</button>' +
                "</div>" +
                '<div id="' + prefix + "-" + level + '-fallback-row" class="wf-exec-route-fallback hidden space-y-1">' +
                    '<div class="grid gap-2 items-center" style="grid-template-columns: 7.5rem minmax(0, 1fr) minmax(0, 1fr) auto;">' +
                        '<span class="text-xs text-gray-500">Fallback</span>' +
                        '<select id="' + prefix + "-" + level + '-fallback-backend" class="wf-exec-fallback-backend w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs focus:border-[#f97316] focus:outline-none" data-level="' + level + '">' +
                            fallbackBackendOptions +
                        "</select>" +
                        '<select id="' + prefix + "-" + level + '-fallback-model" class="wf-exec-fallback-model w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs focus:border-[#f97316] focus:outline-none" data-level="' + level + '">' +
                            '<option value="auto">Auto</option>' +
                        "</select>" +
                        '<span class="inline-flex h-8 w-8" aria-hidden="true"></span>' +
                    "</div>" +
                    '<p class="text-[10px] leading-snug text-gray-500" style="margin-left: 7.75rem;">CLI executor and model used when the primary route is unavailable or not set up.</p>' +
                "</div>" +
                '<p id="' + prefix + "-" + level + '-model-hint" class="wf-exec-model-hint hidden text-[10px] leading-snug text-amber-200/90" style="margin-left: 7.75rem;"></p>' +
                '<div id="' + prefix + "-" + level + '-codex-prefs" class="hidden ml-[7.75rem] grid grid-cols-2 gap-2 rounded border border-white/10 bg-[#10183f] p-2">' +
                    '<label class="space-y-1"><span class="text-[11px] text-gray-400">Intelligence</span>' +
                        '<select id="' + prefix + "-" + level + '-codex-intelligence" class="wf-exec-codex-intelligence w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs" data-level="' + level + '">' +
                            '<option value="">Default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option>' +
                        "</select></label>" +
                    '<label class="space-y-1"><span class="text-[11px] text-gray-400">Speed</span>' +
                        '<select id="' + prefix + "-" + level + '-codex-speed" class="wf-exec-codex-speed w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs" data-level="' + level + '">' +
                            '<option value="">Default</option><option value="flex">Flex</option><option value="fast">Fast</option>' +
                        "</select></label>" +
                    '<p class="col-span-2 text-[10px] text-gray-500">Codex CLI only — passed as model_reasoning_effort and service_tier when this complexity route runs.</p>' +
                "</div>" +
            "</div>";
        }).join("");
    }

    function updateWorkflowExecBackendSelects(root, backends) {
        root = root || document;
        var readyMap = {};
        (backends || []).forEach(function (row) {
            readyMap[row.id] = workflowCliBackendWorkflowReady(row);
        });
        var optionsHtml = workflowExecRouteBackendOptionsHtml(readyMap);
        var fallbackOptionsHtml = workflowExecCliBackendOptionsHtml(readyMap, true);
        root.querySelectorAll(".wf-exec-backend").forEach(function (select) {
            var current = select.value;
            select.innerHTML = optionsHtml;
            if (current) select.value = current;
        });
        root.querySelectorAll(".wf-exec-fallback-backend").forEach(function (select) {
            var current = select.value;
            select.innerHTML = fallbackOptionsHtml;
            if (current) select.value = current;
        });
    }

    function updateWorkflowExecFallbackVisibility(level, root) {
        root = root || document;
        var backend = workflowExecRouteEl(root, level, "backend");
        var row = workflowExecRouteEl(root, level, "fallback-row");
        var isIde = backend && workflowExecBackendIsIde(backend.value);
        if (row) row.classList.toggle("hidden", !isIde);
        if (!isIde) {
            var fbBackend = workflowExecRouteEl(root, level, "fallback-backend");
            var fbModel = workflowExecRouteEl(root, level, "fallback-model");
            if (fbBackend) fbBackend.value = "";
            if (fbModel) {
                fbModel.innerHTML = '<option value="auto">Auto</option>';
                fbModel.value = "auto";
                fbModel.disabled = true;
            }
        }
    }

    function updateWorkflowExecCodexPrefVisibility(level, root) {
        root = root || document;
        var backend = workflowExecRouteEl(root, level, "backend");
        var cog = workflowExecRouteEl(root, level, "codex-cog");
        var panel = workflowExecRouteEl(root, level, "codex-prefs");
        var isCodexCli = backend && backend.value === "codex";
        if (cog) cog.disabled = !isCodexCli;
        if (!isCodexCli && panel) panel.classList.add("hidden");
    }

    function toggleWorkflowExecCodexPrefPanel(level, root) {
        root = root || document;
        var backend = workflowExecRouteEl(root, level, "backend");
        var panel = workflowExecRouteEl(root, level, "codex-prefs");
        if (!panel || !backend || backend.value !== "codex") return;
        panel.classList.toggle("hidden");
    }

    function workflowExecModelsSourceIsVerified(source) {
        var s = String(source || "").toLowerCase();
        return s === "cursor-api" || s === "codex-cli" || s === "anthropic-api" || s === "pi-models"
            || s === "opencode-cli" || s === "opencode-fallback" || s === "opencode-missing";
    }

    function setWorkflowExecRouteModelHint(root, level, message) {
        var hint = workflowExecRouteEl(root, level, "model-hint");
        if (!hint) return;
        var text = (message || "").trim();
        if (text) {
            hint.textContent = text;
            hint.classList.remove("hidden");
        } else {
            hint.textContent = "";
            hint.classList.add("hidden");
        }
    }

    function setWorkflowExecRouteIdeModelSelect(select) {
        if (!select) return;
        select.innerHTML = "";
        var opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Determined by the CLI";
        select.appendChild(opt);
        select.value = "";
        select.disabled = true;
    }

    function loadWorkflowExecRouteModels(level, backendId, selectedModel, root) {
        root = root || document;
        var select = workflowExecRouteEl(root, level, "model");
        if (!select) return Promise.resolve();
        backendId = (backendId || "codex").trim();
        setWorkflowExecRouteModelHint(root, level, "");
        if (workflowExecBackendIsIde(backendId)) {
            setWorkflowExecRouteIdeModelSelect(select);
            updateWorkflowExecCodexPrefVisibility(level, root);
            updateWorkflowExecFallbackVisibility(level, root);
            return Promise.resolve();
        }
        var preferred = (selectedModel || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model || "auto").trim();
        select.disabled = true;
        select.innerHTML = "";
        var loadingOpt = document.createElement("option");
        loadingOpt.value = "auto";
        loadingOpt.textContent = "Loading...";
        select.appendChild(loadingOpt);
        var params = "backend_id=" + encodeURIComponent(backendId);
        var projectId = workflowBoardProjectId();
        if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
        return api("GET", "/projects/cli-models?" + params)
            .then(function (data) {
                var source = (data && data.source) || "";
                var message = (data && data.message) || "";
                var models = Array.isArray(data.models) ? data.models : [];
                var sourceLower = String(source).toLowerCase();
                var verified = workflowExecModelsSourceIsVerified(source);
                var aliasesOnly = sourceLower === "claude-code-aliases";
                var defaultsOnly = sourceLower.indexOf("defaults") >= 0 || sourceLower === "error";
                populateCliModelSelect(select, {
                    models: models,
                    current_model: preferred,
                    current_provider: select.dataset.pinnedProvider || data.current_provider || ""
                }, { includeAuto: true });
                if (aliasesOnly && message) {
                    setWorkflowExecRouteModelHint(root, level, message);
                } else if (defaultsOnly) {
                    setWorkflowExecRouteModelHint(
                        root,
                        level,
                        message || "Could not load models from this executor. Use Auto or fix the CLI/API setup."
                    );
                } else if (!verified && !models.length) {
                    setWorkflowExecRouteModelHint(
                        root,
                        level,
                        message || "No models reported by this executor."
                    );
                } else if (message) {
                    setWorkflowExecRouteModelHint(root, level, message);
                } else {
                    setWorkflowExecRouteModelHint(root, level, "");
                }
                select.disabled = false;
                syncWorkflowExecAutoToggle(level, root);
                updateWorkflowExecCodexPrefVisibility(level, root);
            })
            .catch(function (e) {
                populateCliModelSelect(select, {
                    models: [],
                    current_model: preferred,
                    current_provider: select.dataset.pinnedProvider || ""
                }, { includeAuto: true });
                syncWorkflowExecAutoToggle(level, root);
                setWorkflowExecRouteModelHint(
                    root,
                    level,
                    ((e && e.message) ? e.message : "Could not load models from this executor.") +
                    (preferred !== "auto" ? " Keeping the pinned model visible." : "")
                );
                updateWorkflowExecCodexPrefVisibility(level, root);
            });
    }

    function loadWorkflowExecRouteFallbackModels(level, backendId, selectedModel, root) {
        root = root || document;
        var select = workflowExecRouteEl(root, level, "fallback-model");
        var backendSelect = workflowExecRouteEl(root, level, "fallback-backend");
        if (!select) return Promise.resolve();
        backendId = (backendId || (backendSelect && backendSelect.value) || "").trim();
        if (!backendId || workflowExecBackendIsIde(backendId)) {
            select.innerHTML = '<option value="auto">Auto</option>';
            select.value = "auto";
            select.disabled = true;
            return Promise.resolve();
        }
        var preferred = (selectedModel || "auto").trim();
        select.disabled = true;
        select.innerHTML = '<option value="auto">Loading...</option>';
        var params = "backend_id=" + encodeURIComponent(backendId);
        var projectId = workflowBoardProjectId();
        if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
        return api("GET", "/projects/cli-models?" + params)
            .then(function (data) {
                var models = Array.isArray(data.models) ? data.models : [];
                var seen = {};
                select.innerHTML = "";
                function append(value, label) {
                    value = (value || "auto").trim();
                    if (!value || seen[value]) return;
                    seen[value] = true;
                    var opt = document.createElement("option");
                    opt.value = value;
                    opt.textContent = label || value;
                    select.appendChild(opt);
                }
                append("auto", "Auto");
                models.forEach(function (model) {
                    var value = workflowExecRouteModelValue(model);
                    if (!value || value === "auto") return;
                    append(value, workflowExecRouteModelLabel(model));
                });
                var current = preferred || "auto";
                if (!seen[current]) current = "auto";
                select.value = current;
                select.disabled = false;
            })
            .catch(function () {
                select.innerHTML = '<option value="auto">Auto</option>';
                select.value = "auto";
                select.disabled = false;
            });
    }

    function populateWorkflowExecRouting(data, llmData, root) {
        root = root || document;
        data = data || {};
        llmData = llmData || {};
        var loads = [];
        workflowExecRouteLevels().forEach(function (level) {
            var backend = workflowExecRouteEl(root, level, "backend");
            var route = (data.routing && data.routing[level]) || {};
            var backendValue = workflowExecNormalizeCliBackend(route.backend || llmData["project_cli_" + level + "_backend"] || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].backend);
            var modelValue = route.model || llmData["project_cli_" + level + "_model"] || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model;
            var providerValue = route.model_provider || llmData["project_cli_" + level + "_model_provider"] || "";
            var fallbackBackendValue = workflowExecNormalizeCliBackend(route.fallback_backend || llmData["project_cli_" + level + "_fallback_backend"] || "");
            var fallbackModelValue = route.fallback_model || llmData["project_cli_" + level + "_fallback_model"] || "";
            if (backend) backend.value = backendValue;
            var modelSelect = workflowExecRouteEl(root, level, "model");
            if (modelSelect) modelSelect.dataset.pinnedProvider = providerValue;
            var fallbackBackend = workflowExecRouteEl(root, level, "fallback-backend");
            if (fallbackBackend) fallbackBackend.value = fallbackBackendValue || "";
            var intelligence = workflowExecRouteEl(root, level, "codex-intelligence");
            var speed = workflowExecRouteEl(root, level, "codex-speed");
            if (intelligence) intelligence.value = route.codex_intelligence || "";
            if (speed) speed.value = route.codex_speed || "";
            var prefsPanel = workflowExecRouteEl(root, level, "codex-prefs");
            if (prefsPanel) prefsPanel.classList.add("hidden");
            loads.push(loadWorkflowExecRouteModels(level, backendValue, modelValue, root));
            loads.push(loadWorkflowExecRouteFallbackModels(level, fallbackBackendValue, fallbackModelValue, root));
            updateWorkflowExecCodexPrefVisibility(level, root);
            updateWorkflowExecFallbackVisibility(level, root);
        });
        return Promise.all(loads);
    }

    function collectWorkflowExecRouting(root) {
        root = root || document;
        var routing = {};
        workflowExecRouteLevels().forEach(function (level) {
            var backend = workflowExecRouteEl(root, level, "backend");
            var model = workflowExecRouteEl(root, level, "model");
            var auto = workflowExecRouteEl(root, level, "auto");
            var intelligence = workflowExecRouteEl(root, level, "codex-intelligence");
            var speed = workflowExecRouteEl(root, level, "codex-speed");
            var backendValue = (backend && backend.value) || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].backend;
            var row = {
                backend: backendValue,
                model: workflowExecBackendIsIde(backendValue)
                    ? ""
                    : ((auto && auto.checked) ? "auto" : ((model && model.value) || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model))
            };
            if (model && model.selectedOptions && model.selectedOptions[0]) {
                row.model_provider = String(
                    model.selectedOptions[0].dataset.provider || model.dataset.pinnedProvider || ""
                ).trim();
            }
            if (workflowExecBackendIsIde(backendValue)) {
                var fallbackBackend = workflowExecRouteEl(root, level, "fallback-backend");
                var fallbackModel = workflowExecRouteEl(root, level, "fallback-model");
                row.fallback_backend = (fallbackBackend && fallbackBackend.value) || "";
                row.fallback_model = row.fallback_backend
                    ? ((fallbackModel && fallbackModel.value) || "auto")
                    : "";
            }
            if (row.backend === "codex") {
                row.codex_intelligence = (intelligence && intelligence.value) || "";
                row.codex_speed = (speed && speed.value) || "";
            }
            routing[level] = row;
        });
        return routing;
    }

    function bindWorkflowExecRoutingControls(root) {
        root = root || document;
        root.querySelectorAll(".wf-exec-backend").forEach(function (select) {
            if (select.dataset.execBound === "1") return;
            select.dataset.execBound = "1";
            select.addEventListener("change", function () {
                loadWorkflowExecRouteModels(select.dataset.level, select.value, "auto", root);
                updateWorkflowExecFallbackVisibility(select.dataset.level, root);
                refreshWorkflowConfigExecutorPills(root);
            });
        });
        root.querySelectorAll(".wf-exec-model").forEach(function (select) {
            if (select.dataset.modelPinBound === "1") return;
            select.dataset.modelPinBound = "1";
            select.addEventListener("change", function () {
                select.dataset.pinnedProvider = selectedCliModelProvider(select);
                syncWorkflowExecAutoToggle(select.dataset.level, root);
            });
        });
        root.querySelectorAll(".wf-exec-auto").forEach(function (checkbox) {
            if (checkbox.dataset.execBound === "1") return;
            checkbox.dataset.execBound = "1";
            checkbox.addEventListener("change", function () {
                var model = workflowExecRouteEl(root, checkbox.dataset.level, "model");
                if (!model) return;
                if (checkbox.checked) {
                    if (model.value && model.value !== "auto") {
                        model.dataset.lastPinnedModel = model.value;
                        model.dataset.lastPinnedProvider = selectedCliModelProvider(model);
                    }
                    model.value = "auto";
                } else if (model.value === "auto") {
                    var remembered = String(model.dataset.lastPinnedModel || "");
                    var concrete = Array.prototype.find.call(model.options || [], function (option) {
                        return remembered && !option.disabled && String(option.value || "") === remembered;
                    }) || Array.prototype.find.call(model.options || [], function (option) {
                        return !option.disabled && String(option.value || "") !== "auto";
                    });
                    if (concrete) {
                        model.value = concrete.value;
                        model.dataset.pinnedProvider = concrete.dataset.provider || model.dataset.lastPinnedProvider || "";
                    }
                    else checkbox.checked = true;
                }
                syncWorkflowExecAutoToggle(checkbox.dataset.level, root);
            });
        });
        root.querySelectorAll(".wf-exec-fallback-backend").forEach(function (select) {
            if (select.dataset.execBound === "1") return;
            select.dataset.execBound = "1";
            select.addEventListener("change", function () {
                loadWorkflowExecRouteFallbackModels(select.dataset.level, select.value, "auto", root);
            });
        });
        root.querySelectorAll(".wf-exec-codex-cog").forEach(function (btn) {
            if (btn.dataset.execBound === "1") return;
            btn.dataset.execBound = "1";
            btn.addEventListener("click", function () {
                toggleWorkflowExecCodexPrefPanel(btn.dataset.level, root);
            });
        });
    }

    function syncWorkflowExecAutoToggle(level, root) {
        root = root || document;
        var model = workflowExecRouteEl(root, level, "model");
        var auto = workflowExecRouteEl(root, level, "auto");
        if (!model || !auto) return;
        auto.checked = String(model.value || "auto") === "auto";
        if (!auto.checked) {
            model.dataset.lastPinnedModel = model.value;
            model.dataset.lastPinnedProvider = selectedCliModelProvider(model) || model.dataset.pinnedProvider || "";
        }
        model.disabled = auto.checked;
        model.setAttribute("aria-label", auto.checked
            ? (level + " complexity model: automatic")
            : (level + " complexity pinned model"));
    }

    function saveWorkflowExecRouting(root, options) {
        options = options || {};
        var payload = {
            enabled: options.enabled != null ? !!options.enabled : true,
            routing: collectWorkflowExecRouting(root)
        };
        return api("POST", "/workflows/orchestrator-setup", payload).then(function (resp) {
            refreshWorkflowConfigExecutorPills(root);
            return resp;
        });
    }

    function workflowBoardProjectId() {
        var opt = currentWorkflowBoardOption();
        return opt && opt.default_project_id ? parseInt(opt.default_project_id, 10) : null;
    }

    function fetchWorkflowProjectCliBackends(projectId) {
        if (projectId) {
            var ctx = workflowCliAreaContext();
            var query = ctx && ctx.board_id ? ("?board_id=" + encodeURIComponent(ctx.board_id)) : "";
            return api("GET", "/projects/" + encodeURIComponent(projectId) + "/cli-backends" + query).catch(function () {
                return api("GET", "/projects/cli-backends");
            });
        }
        return api("GET", "/projects/cli-backends");
    }

    var _wfExecutorInstallContext = { optionalBackends: {}, backendRows: [] };

    function clearWorkflowExecutorInstallCallout() {
        var existing = document.getElementById("wf-executor-install-callout");
        if (existing) existing.remove();
        document.querySelectorAll(".wf-config-exec-pill.is-callout-open").forEach(function (btn) {
            btn.classList.remove("is-callout-open");
        });
    }

    function workflowExecutorInstallCalloutPayload(backendId) {
        var rows = _wfExecutorInstallContext.backendRows || [];
        var row = rows.find(function (b) { return String(b.id) === String(backendId); }) || {};
        var optional = (_wfExecutorInstallContext.optionalBackends || {})[backendId] || {};
        var ready = workflowCliBackendWorkflowReady(row) || optional.ready;
        if (ready) return null;
        var name = row.name || optional.name || backendId;
        var message = optional.message || row.health_message || row.message || row.setup_instructions || (name + " is not installed.");
        var setupCommand = optional.setup_command || row.setup_command || "";
        return { name: name, message: message, setupCommand: setupCommand };
    }

    function showWorkflowExecutorInstallCallout(backendId) {
        var pills = document.getElementById("wf-global-exec-backend-pills");
        if (!pills) return;
        var payload = workflowExecutorInstallCalloutPayload(backendId);
        if (!payload) {
            clearWorkflowExecutorInstallCallout();
            return;
        }
        var existing = document.getElementById("wf-executor-install-callout");
        if (existing && existing.dataset.backendId === String(backendId)) {
            clearWorkflowExecutorInstallCallout();
            return;
        }
        clearWorkflowExecutorInstallCallout();
        var el = document.createElement("div");
        el.id = "wf-executor-install-callout";
        el.className = "wf-config-optional-hint";
        el.dataset.backendId = String(backendId);
        var html = "<span>" + esc(payload.name) + ": " + esc(payload.message) + "</span>";
        if (payload.setupCommand) {
            html += '<button type="button" class="wf-copy-executor-setup">Copy install command</button>';
        }
        el.innerHTML = html;
        pills.insertAdjacentElement("afterend", el);
        var pill = pills.querySelector('.wf-config-exec-pill[data-backend-id="' + backendId + '"]');
        if (pill) pill.classList.add("is-callout-open");
        var copyBtn = el.querySelector(".wf-copy-executor-setup");
        if (copyBtn && payload.setupCommand) {
            copyBtn.addEventListener("click", function () {
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(payload.setupCommand).then(function () { snack("Copied"); });
                }
            });
        }
    }

    function renderWorkflowConfigExecutorPills(container, setup, routeRoot, optionalBackends) {
        if (!container) return;
        setup = setup || {};
        routeRoot = routeRoot || document;
        var backends = [];
        if (Array.isArray(setup.backends)) {
            backends = setup.backends;
        } else if (setup.backends && Array.isArray(setup.backends.backends)) {
            backends = setup.backends.backends;
        }
        var routing = setup.routing || collectWorkflowExecRouting(routeRoot);
        var routeIds = {};
        workflowExecRouteLevels().forEach(function (level) {
            if (routing[level] && routing[level].backend) routeIds[routing[level].backend] = true;
        });
        if (!backends.length) {
            container.innerHTML = '<span class="text-[10px] text-gray-500">Checking executors...</span>';
            return;
        }
        backends = backends.filter(function (row) {
            return row && !workflowExecBackendIsIde(row.id);
        });
        _wfExecutorInstallContext.backendRows = backends;
        if (optionalBackends) {
            _wfExecutorInstallContext.optionalBackends = optionalBackends;
        }
        container.innerHTML = backends.map(function (row) {
            var ready = workflowCliBackendWorkflowReady(row);
            var setupRequired = !!row.setup_required && !ready;
            var cls = "wf-config-exec-pill";
            if (ready) cls += " is-ready";
            if (setupRequired) cls += " is-setup";
            if (routeIds[row.id]) cls += " is-active-route";
            var title = row.health_message || row.message || row.setup_instructions || "";
            return '<button type="button" class="' + cls + '" data-backend-id="' + esc(row.id) + '" title="' + esc(title) + '">' +
                '<span class="wf-config-exec-pill-dot" aria-hidden="true"></span>' +
                '<span>' + esc(row.name || row.id) + "</span>" +
            "</button>";
        }).join("");
        container.querySelectorAll(".wf-config-exec-pill").forEach(function (btn) {
            btn.addEventListener("click", function () {
                if (btn.classList.contains("is-ready")) return;
                showWorkflowExecutorInstallCallout(btn.dataset.backendId || "");
            });
        });
    }

    function refreshWorkflowConfigExecutorPills(routeRoot) {
        routeRoot = routeRoot || document.getElementById("sr-llm-modal") || document;
        var container = routeRoot.querySelector ? routeRoot.querySelector("#wf-global-exec-backend-pills") : document.getElementById("wf-global-exec-backend-pills");
        if (!container) return Promise.resolve();
        var projectId = workflowBoardProjectId();
        return Promise.all([
            api("GET", "/workflows/orchestrator-setup").catch(function () { return {}; }),
            fetchWorkflowProjectCliBackends(projectId)
        ]).then(function (results) {
            var setup = results[0] || {};
            var projectBackends = results[1] || {};
            var backendRows = Array.isArray(projectBackends.backends) ? projectBackends.backends : [];
            if (!backendRows.length && setup.backends && Array.isArray(setup.backends.backends)) {
                backendRows = setup.backends.backends;
            }
            setup.backends = { backends: backendRows };
            renderWorkflowConfigExecutorPills(container, setup, routeRoot, (results[0] || {}).optional_backends || {});
        });
    }

    var _wfCliWs = null;
    var _wfCliProjectId = null;
    var _wfCliAssistantBuffer = "";
    var _wfCliSessionReady = false;
    var _wfCliConnectPromise = null;
    var _wfCliBoundTicket = null;
    var _wfCliTicketTimerStartedAt = null;
    var _wfCliRecoveryActions = [];
    var _wfCliInventoryById = {};
    var _wfCliInventoryMeta = { source: "", message: "" };
    var _wfCliChosenModels = [];
    var _wfCliBackendRows = [];
    var _wfCliModelContextTargetId = "";
    var _wfCliDraggedModelId = "";
    var _wfCliDraggedChosenIndex = -1;
    var _wfCliAreaPresent = false;
    var _wfCliAreaHeartbeatTimer = null;
    var _wfCliAreaEnsurePromise = null;
    var WF_CLI_AREA_HEARTBEAT_MS = 30000;
    var WF_CLI_LOCKED_MODEL_STORAGE_KEY = "wf_cli_locked_pi_model_v1";
    var WF_CLI_BOARD_STATE_STORAGE_PREFIX = "wf_cli_board_state_v1";
    var WF_CLI_DISCONNECTED_PLACEHOLDER = "Choose a CLI to load models. DecisionsAI will keep the board session warm while you stay in workflows.";
    var WF_CLI_CONNECTED_PLACEHOLDER = "Send a prompt...";

    function workflowCliBoundTicketLabel() {
        if (!_wfCliBoundTicket || !_wfCliBoundTicket.id) return "";
        return _wfCliBoundTicket.title || ("Ticket #" + _wfCliBoundTicket.id);
    }

    function bindWorkflowCliTicket(ticket) {
        _wfCliBoundTicket = ticket && ticket.id ? {
            id: ticket.id,
            title: ticket.title || "",
            time_spent: ticket.time_spent || ""
        } : null;
        _wfCliTicketTimerStartedAt = null;
        if (_wfCliBoundTicket) appendWorkflowCliLine("system", "Bound CLI session to " + workflowCliBoundTicketLabel() + ".");
    }

    function startWorkflowCliTicketTimer() {
        if (!_wfCliBoundTicket || _wfCliTicketTimerStartedAt) return;
        _wfCliTicketTimerStartedAt = Date.now();
    }

    function flushWorkflowCliTicketTimer() {
        if (!_wfCliBoundTicket || !_wfCliTicketTimerStartedAt) return Promise.resolve();
        var elapsed = Math.max(1, Math.round((Date.now() - _wfCliTicketTimerStartedAt) / 1000));
        _wfCliTicketTimerStartedAt = null;
        return api("POST", "/tickets/tickets/" + encodeURIComponent(_wfCliBoundTicket.id) + "/time-spent/add", {
            seconds: elapsed,
            source: "workflow_cli"
        }).then(function (resp) {
            if (resp && resp.time_spent) {
                _wfCliBoundTicket.time_spent = resp.time_spent;
                var idx = (workflowQueueTickets || []).findIndex(function (ticket) {
                    return String(ticket.id) === String(_wfCliBoundTicket.id);
                });
                if (idx >= 0) workflowQueueTickets[idx].time_spent = resp.time_spent;
                renderWorkflowTickets(workflowQueueTickets);
            }
        }).catch(function () {});
    }

    function workflowCliBackendDisplayName(backend) {
        var name = ((backend && (backend.name || backend.id)) || "").trim();
        return name.replace(/\s+CLI$/i, "") || name;
    }

    function workflowCliBackendWorkflowReady(backend) {
        if (!backend) return false;
        if (typeof backend.workflow_ready === "boolean") return backend.workflow_ready;
        return !!backend.ready;
    }

    function workflowCliBackendVisualState(backend) {
        if (!backend) return "missing";
        if (backend.running) return "running";
        if (backend.connected) return "connected";
        if (backend.health_state) return String(backend.health_state);
        if (workflowCliBackendWorkflowReady(backend)) return "ready";
        if (backend.installed || backend.state === "auth_required" || backend.state === "not_ready") return "setup";
        return "missing";
    }

    function workflowCliBackendDotHtml(state) {
        state = String(state || "missing");
        return '<span class="wf-cli-backend-dot is-' + esc(state) + '" aria-hidden="true"></span>';
    }

    function workflowCliTabBackends(backends) {
        return (backends || []).filter(function (b) {
            return !workflowExecBackendIsIde(b.id) && b.id !== "hermes_agent";
        }).sort(function (a, b) {
            var aId = String((a && a.id) || "").trim();
            var bId = String((b && b.id) || "").trim();
            var aRank = Object.prototype.hasOwnProperty.call(WORKFLOW_CLI_BACKEND_ORDER, aId) ? WORKFLOW_CLI_BACKEND_ORDER[aId] : 50;
            var bRank = Object.prototype.hasOwnProperty.call(WORKFLOW_CLI_BACKEND_ORDER, bId) ? WORKFLOW_CLI_BACKEND_ORDER[bId] : 50;
            if (aRank !== bRank) return aRank - bRank;
            return workflowCliBackendDisplayName(a).localeCompare(workflowCliBackendDisplayName(b));
        });
    }

    function renderWorkflowCliBackendMenu(backends, activeId) {
        var menu = document.getElementById("wf-cli-backend-menu");
        var trigger = document.getElementById("wf-cli-backend-trigger");
        var nameEl = document.getElementById("wf-cli-backend-trigger-name");
        var dotEl = document.getElementById("wf-cli-backend-trigger-dot");
        backends = workflowCliTabBackends(backends);
        _wfCliBackendRows = backends.slice();
        var active = workflowCliBackendRow(backends, activeId) || backends[0] || null;
        if (nameEl) nameEl.textContent = active ? workflowCliBackendDisplayName(active) : "Choose CLI";
        if (dotEl) {
            dotEl.className = "wf-cli-backend-dot";
            dotEl.classList.add("is-" + workflowCliBackendVisualState(active));
        }
        if (!menu) return;
        menu.innerHTML = backends.map(function (backend) {
            var state = workflowCliBackendVisualState(backend);
            var activeClass = String(backend.id) === String(activeId) ? " is-active" : "";
            return '<button type="button" class="wf-cli-backend-menu-item' + activeClass + '" data-wf-cli-backend-option="' + esc(backend.id) + '" role="option" aria-selected="' + (String(backend.id) === String(activeId) ? "true" : "false") + '">' +
                '<span class="wf-cli-backend-menu-item-copy">' +
                    workflowCliBackendDotHtml(state) +
                    '<span class="wf-cli-backend-menu-item-name">' + esc(workflowCliBackendDisplayName(backend)) + '</span>' +
                '</span>' +
                '<span class="wf-cli-backend-menu-item-check">' + (String(backend.id) === String(activeId) ? "✓" : "") + '</span>' +
            '</button>';
        }).join("");
        if (trigger) trigger.disabled = !backends.length || !workflowBoardProjectId();
    }

    function setWorkflowCliBackendMenuOpen(open) {
        var menu = document.getElementById("wf-cli-backend-menu");
        var trigger = document.getElementById("wf-cli-backend-trigger");
        if (!menu || !trigger) return;
        menu.classList.toggle("is-open", !!open);
        trigger.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function workflowCliResolveActiveBackend(backends, preferred) {
        var cliBackends = workflowCliTabBackends(backends);
        var active = (preferred || "pi").trim();
        if (workflowExecBackendIsIde(active)) {
            active = cliBackends.length ? cliBackends[0].id : "pi";
        } else if (!cliBackends.some(function (b) { return b.id === active; })) {
            active = cliBackends.length ? cliBackends[0].id : "pi";
        }
        return active;
    }

    function workflowCliInputPlaceholder(ready) {
        var input = document.getElementById("wf-cli-input");
        if (!input) return;
        input.placeholder = ready ? WF_CLI_CONNECTED_PLACEHOLDER : WF_CLI_DISCONNECTED_PLACEHOLDER;
    }

    function workflowCliActiveBackend() {
        var sel = document.getElementById("wf-cli-backend-select");
        var current = sel ? String(sel.value || "").trim() : "";
        if (sel && !sel.disabled && current) return current;
        var saved = workflowCliStoredState();
        return String(saved.backend_id || current || "pi").trim() || "pi";
    }

    function workflowCliBoardStateStorageKey() {
        var opt = currentWorkflowBoardOption();
        if (!opt) return "";
        var boardKey = String(opt.value || opt.local_id || opt.id || "").trim();
        var workflowKey = String(currentWorkflowId || 0);
        var projectKey = String(workflowBoardProjectId() || 0);
        if (!boardKey && !projectKey) return "";
        return [WF_CLI_BOARD_STATE_STORAGE_PREFIX, workflowKey, projectKey, boardKey || "board"].join("::");
    }

    function workflowCliStoredState() {
        try {
            var key = workflowCliBoardStateStorageKey();
            if (!key) return {};
            var raw = localStorage.getItem(key) || "";
            if (!raw) return {};
            var parsed = JSON.parse(raw);
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (e) {
            return {};
        }
    }

    function workflowCliSelectedModelValue() {
        var modelSel = document.getElementById("wf-cli-model-select");
        return modelSel ? String(modelSel.value || "").trim() : "";
    }

    function persistWorkflowCliBoardState(overrides) {
        try {
        var key = workflowCliBoardStateStorageKey();
        if (!key) return;
        var next = workflowCliStoredState();
        var capabilities = workflowCliSelectedCapabilities();
        overrides = overrides || {};
            next.backend_id = String(
                overrides.backend_id != null ? overrides.backend_id : workflowCliActiveBackend()
            ).trim() || "pi";
        next.model = String(
            overrides.model != null ? overrides.model : workflowCliSelectedModelValue()
        ).trim();
        next.codex_reasoning_effort = String(
            overrides.codex_reasoning_effort != null ? overrides.codex_reasoning_effort : capabilities.codex_reasoning_effort
        ).trim();
            next.codex_service_tier = String(
                overrides.codex_service_tier != null ? overrides.codex_service_tier : capabilities.codex_service_tier
            ).trim();
            if (Object.prototype.hasOwnProperty.call(overrides, "connected")) {
                next.connected = !!overrides.connected;
            }
            next.project_id = workflowBoardProjectId() || null;
            next.saved_at = new Date().toISOString();
            localStorage.setItem(key, JSON.stringify(next));
        } catch (e) {}
    }

    function workflowCliTabIsActive() {
        var cliTab = document.getElementById("wf-tab-cli");
        return !!(cliTab && !cliTab.hidden && !cliTab.classList.contains("hidden"));
    }

    function workflowCliShouldWarmOnPresence() {
        return workflowCliTabIsActive();
    }

    function workflowActiveDetailTab() {
        var active = document.querySelector('.tab-btn.active[data-tab]');
        return active ? String(active.dataset.tab || "").trim() : "";
    }

    function fetchWorkflowCliTerminalState(projectId) {
        if (!projectId) return Promise.resolve({ alive: false, connected: false, backend_id: "", external_thread_id: "", supports_rpc: false, buffer: "" });
        var ctx = workflowCliAreaContext();
        var query = "lines=160";
        if (ctx && ctx.board_id) query += "&board_id=" + encodeURIComponent(ctx.board_id);
        return api("GET", "/projects/" + encodeURIComponent(projectId) + "/terminal/buffer?" + query)
            .catch(function () { return { alive: false, connected: false, backend_id: "", external_thread_id: "", supports_rpc: false, buffer: "" }; });
    }

    function workflowCliBufferEntryUserText(entry) {
        if (!entry || String(entry.role || "") !== "user") return "";
        if (typeof entry.content === "string") return entry.content;
        if (Array.isArray(entry.content)) {
            return entry.content.filter(function (block) { return block && block.type === "text"; })
                .map(function (block) { return block.text || ""; }).join("");
        }
        return "";
    }

    function renderWorkflowCliBufferEntry(entry) {
        if (!entry) return;
        var role = String(entry.role || "").trim();
        if (role === "user") {
            appendWorkflowCliLine("user", workflowCliBufferEntryUserText(entry));
            return;
        }
        if (role === "assistant" && entry.content) {
            appendWorkflowCliLine("assistant", entry.content);
        }
    }

    function workflowCliActiveSupportsRpc() {
        var sel = document.getElementById("wf-cli-backend-select");
        if (!sel || !sel.selectedOptions || !sel.selectedOptions.length) return false;
        return sel.selectedOptions[0].dataset.supportsRpc === "true";
    }

    function workflowCliReadyLabel() {
        return workflowCliActiveSupportsRpc() ? "Connected" : "Ready";
    }

    function setWorkflowCliSessionReady(ready) {
        _wfCliSessionReady = !!ready;
        var input = document.getElementById("wf-cli-input");
        var sendBtn = document.getElementById("wf-cli-send");
        var modelSel = document.getElementById("wf-cli-model-select");
        if (input) input.disabled = !ready;
        if (sendBtn) sendBtn.disabled = !ready;
        if (modelSel) modelSel.disabled = !ready;
        workflowCliInputPlaceholder(ready);
    }

    function resetWorkflowCliModelSelect(message) {
        var select = document.getElementById("wf-cli-model-select");
        if (!select) return;
        select.innerHTML = '<option value="">' + esc(message || "Choose a CLI first") + "</option>";
        select.disabled = true;
        _wfCliInventoryById = {};
        _wfCliInventoryMeta = { source: "", message: "" };
        renderWorkflowCliModelInventory({ models: [] }, message || "Choose a CLI to load its models.");
    }

    function workflowCliChosenModelKey(model) {
        if (!model) return "";
        return [
            String(model.backend_id || workflowCliActiveBackend() || "").trim(),
            String(model.provider || "").trim(),
            String(model.id || model.model || "").trim()
        ].join("|");
    }

    function workflowCliSnapshotModelMeta(model, backendId) {
        if (!model) return null;
        var id = String(model.id || model.model || "").trim();
        if (!id) return null;
        return {
            id: id,
            model: id,
            name: String(model.name || id).trim() || id,
            provider: String(model.provider || "").trim(),
            backend_id: String(backendId || model.backend_id || workflowCliActiveBackend() || "").trim() || "pi",
            scope: String(model.scope || "").trim(),
            tier: String(model.tier || "").trim(),
            speed_hint: String(model.speed_hint || model.speed || "").trim(),
            intelligence_hint: String(model.intelligence_hint || model.reasoning_effort || model.intelligence || "").trim(),
            free: !!model.free,
            local: !!model.local,
            usable: model.usable !== false,
            supports_chat: model.supports_chat !== false,
            reason: String(model.reason || "").trim(),
            selected_at: model.selected_at || new Date().toISOString()
        };
    }

    function workflowCliModelPrimaryLabel(model) {
        if (!model) return "Model";
        return String(model.name || model.id || model.model || "Model").trim() || "Model";
    }

    function workflowCliCompactModelLabel(value) {
        return String(value || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "");
    }

    function workflowCliModelSecondaryLabel(model) {
        if (!model) return "";
        var primary = workflowCliCompactModelLabel(workflowCliModelPrimaryLabel(model));
        var id = String(model.id || model.model || "").trim();
        if (!id) return "";
        var compactId = workflowCliCompactModelLabel(id);
        if (compactId === primary) return "";
        if (compactId.indexOf(primary) >= 0 || primary.indexOf(compactId) >= 0) return "";
        return id;
    }

    function workflowCliLevelLabel(model) {
        var raw = String((model && (model.intelligence_hint || model.tier)) || "").trim().toLowerCase();
        if (raw === "high" || raw === "max" || raw === "deep" || raw === "opus") return "High";
        if (raw === "low" || raw === "fast" || raw === "light" || raw === "haiku") return "Low";
        if (raw === "medium" || raw === "standard" || raw === "balanced" || raw === "sonnet") return "Medium";
        var id = String(model && (model.id || model.model || "") || "").toLowerCase();
        if (/(opus|max|large|70b|gpt-5\.3-codex$|sonnet\[1m\])/.test(id)) return "High";
        if (/(haiku|fast|spark|mini|nano|small|0\.5b|0\.6b|1\.7b)/.test(id)) return "Low";
        return "Medium";
    }

    function workflowCliSpeedLabel(model) {
        var raw = String((model && model.speed_hint) || "").trim().toLowerCase();
        if (raw === "slow" || raw === "deliberate" || raw === "deep") return "Deep";
        if (raw === "fast" || raw === "instant" || raw === "quick") return "Fast";
        if (raw === "balanced" || raw === "medium" || raw === "standard") return "Balanced";
        var tier = String(model && model.tier || "").toLowerCase();
        if (tier === "high") return "Deep";
        if (tier === "low") return "Fast";
        var id = String(model && (model.id || model.model || "") || "").toLowerCase();
        if (/(fast|spark|haiku|mini|nano|small|0\.5b|0\.6b|1\.7b)/.test(id)) return "Fast";
        if (/(opus|max|large|70b)/.test(id)) return "Deep";
        return "Balanced";
    }

    function workflowCliNormalizeChosenModels(models) {
        var seen = {};
        return (Array.isArray(models) ? models : []).map(function (model) {
            return workflowCliSnapshotModelMeta(model, model && model.backend_id);
        }).filter(function (model) {
            if (!model) return false;
            var key = workflowCliChosenModelKey(model);
            if (!key || seen[key]) return false;
            seen[key] = true;
            return true;
        });
    }

    function workflowCliChosenDropzoneHtml(hasItems) {
        if (hasItems) return "";
        return '<div id="wf-cli-chosen-dropzone" class="wf-cli-chosen-dropzone">' +
            "Drag models here to remember the CLI, provider, model, and metadata for this workflow." +
            '</div>';
    }

    function renderWorkflowCliChosenModels() {
        var list = document.getElementById("wf-cli-chosen-list");
        if (!list) return;
        var chosen = workflowCliNormalizeChosenModels(_wfCliChosenModels);
        _wfCliChosenModels = chosen;
        if (!chosen.length) {
            list.innerHTML = workflowCliChosenDropzoneHtml(false);
            return;
        }
        list.innerHTML = '<table class="wf-cli-model-table wf-cli-chosen-table">' +
            '<thead><tr>' +
                '<th>Model</th>' +
                '<th style="width: 4.75rem;">CLI</th>' +
                '<th style="width: 5.75rem;">Provider</th>' +
                '<th style="width: 5.25rem;">Scope</th>' +
                '<th style="width: 2.25rem;"></th>' +
            '</tr></thead><tbody>' +
            chosen.map(function (model, index) {
            var flags = [];
            if (model.scope) flags.push(model.scope);
            if (model.free) flags.push("free");
            if (model.local) flags.push("local");
            if (model.tier) flags.push(model.tier);
            if (model.supports_chat === false) flags.push("no chat");
            var subtitle = workflowCliModelSecondaryLabel(model);
            return '<tr class="wf-cli-chosen-row">' +
                '<td><div class="wf-cli-model-name" title="' + esc(model.name || model.id) + '">' + esc(model.name || model.id) + '</div>' +
                    (subtitle ? ('<div class="wf-cli-model-id" title="' + esc(subtitle) + '">' + esc(subtitle) + '</div>') : '') + '</td>' +
                '<td>' + esc(model.backend_id || "pi") + '</td>' +
                '<td>' + esc(model.provider || "unknown") + '</td>' +
                '<td><span class="wf-cli-model-flags">' + esc(flags.join(" / ") || "saved") + '</span></td>' +
                '<td><button type="button" class="wf-cli-chosen-remove" data-wf-cli-chosen-remove="' + index + '" title="Remove model" aria-label="Remove model">' + SVG_TRASH + '</button></td>' +
            '</tr>';
        }).join("") + '</tbody></table>';
    }

    function workflowCliChosenModelsFromSettings(data) {
        var settings = normalizedRunSettings(data || currentWorkflow || {});
        _wfCliChosenModels = workflowCliNormalizeChosenModels(settings.chosen_models || []);
        renderWorkflowCliChosenModels();
    }

    function persistWorkflowCliChosenModels() {
        _wfCliChosenModels = workflowCliNormalizeChosenModels(_wfCliChosenModels);
        renderWorkflowCliChosenModels();
        if (!currentWorkflow) currentWorkflow = {};
        currentWorkflow.run_settings = collectRunSettings();
        return saveWorkflowRunSettings().catch(function () {});
    }

    function workflowCliAddChosenModel(model) {
        var snapshot = workflowCliSnapshotModelMeta(model, model && model.backend_id);
        if (!snapshot) return;
        var key = workflowCliChosenModelKey(snapshot);
        _wfCliChosenModels = workflowCliNormalizeChosenModels(
            [snapshot].concat(_wfCliChosenModels.filter(function (entry) {
                return workflowCliChosenModelKey(entry) !== key;
            }))
        );
        persistWorkflowCliChosenModels();
    }

    function workflowCliMoveChosenModel(fromIndex, toIndex) {
        fromIndex = parseInt(fromIndex, 10);
        toIndex = parseInt(toIndex, 10);
        if (!Number.isFinite(fromIndex) || !Number.isFinite(toIndex)) return;
        if (fromIndex < 0 || toIndex < 0 || fromIndex >= _wfCliChosenModels.length || toIndex >= _wfCliChosenModels.length) return;
        if (fromIndex === toIndex) return;
        var next = _wfCliChosenModels.slice();
        var moved = next.splice(fromIndex, 1)[0];
        next.splice(toIndex, 0, moved);
        _wfCliChosenModels = next;
        persistWorkflowCliChosenModels();
    }

    function workflowCliRemoveChosenModel(index) {
        index = parseInt(index, 10);
        if (!Number.isFinite(index) || index < 0 || index >= _wfCliChosenModels.length) return;
        _wfCliChosenModels = _wfCliChosenModels.filter(function (_entry, i) { return i !== index; });
        persistWorkflowCliChosenModels();
    }

    function workflowCliClearChosenModels() {
        if (!_wfCliChosenModels.length) return;
        _wfCliChosenModels = [];
        persistWorkflowCliChosenModels();
    }

    function renderWorkflowCliModelInventory(data, emptyMessage) {
        var list = document.getElementById("wf-cli-model-list");
        var select = document.getElementById("wf-cli-model-select");
        var activeModel = select ? String(select.value || "") : "";
        if (!list) return;
        var models = Array.isArray(data && data.models) ? data.models : [];
        var message = String(data && data.message || "").trim();
        _wfCliInventoryById = {};
        _wfCliInventoryMeta = {
            source: String(data && data.source || "").trim(),
            message: message
        };
        models.forEach(function (model) {
            var snapshot = workflowCliSnapshotModelMeta(model, workflowCliActiveBackend());
            if (snapshot) _wfCliInventoryById[snapshot.id] = snapshot;
        });
        if (!models.length) {
            list.innerHTML = '<p class="text-xs text-gray-500 italic">' + esc(emptyMessage || "No models returned for this CLI.") + "</p>";
            return;
        }
        var noticeHtml = message
            ? ('<div class="text-[11px] text-amber-200/90 rounded border border-amber-500/25 bg-amber-500/10 px-2 py-1.5 mb-2">' + esc(message) + '</div>')
            : "";
        list.innerHTML = noticeHtml + '<table class="wf-cli-model-table wf-cli-available-table">' +
            '<thead><tr>' +
                '<th>Model</th>' +
                '<th style="width: 4.75rem;">Level</th>' +
                '<th style="width: 5.5rem;">Speed</th>' +
                '<th style="width: 2.5rem;">Add</th>' +
            '</tr></thead><tbody>' +
            models.map(function (model) {
            var name = model.name || model.id || model.model || "Model";
            var disabled = model.supports_chat === false;
            var id = model.id || model.model || "";
            var subtitle = workflowCliModelSecondaryLabel(model);
            var levelLabel = workflowCliLevelLabel(model);
            var speedLabel = workflowCliSpeedLabel(model);
            return '<tr class="wf-cli-model-row' + (disabled ? " is-disabled" : "") + (activeModel === id ? " is-active" : "") + '" role="button" tabindex="' + (disabled ? "-1" : "0") + '" draggable="' + (disabled ? "false" : "true") + '" data-model-id="' + esc(id) + '" data-provider="' + esc(model.provider || "") + '">' +
                '<td><div class="wf-cli-model-name" title="' + esc(name) + '">' + esc(name) + '</div>' +
                    (subtitle ? ('<div class="wf-cli-model-id" title="' + esc(subtitle) + '">' + esc(subtitle) + '</div>') : '') + '</td>' +
                '<td><span class="wf-cli-model-flags">' + esc(levelLabel) + '</span></td>' +
                '<td><span class="wf-cli-model-flags">' + esc(speedLabel) + '</span></td>' +
                '<td><button type="button" class="wf-cli-model-add" data-wf-cli-model-add="' + esc(id) + '" title="Add model" aria-label="Add model">+</button></td>' +
            '</tr>';
        }).join("") + '</tbody></table>';
    }

    function workflowCliCapabilityElements() {
        return {
            row: document.getElementById("wf-cli-model-capability-row"),
            levelField: document.getElementById("wf-cli-capability-level-field"),
            speedField: document.getElementById("wf-cli-capability-speed-field"),
            variantField: document.getElementById("wf-cli-capability-variant-field"),
            level: document.getElementById("wf-cli-capability-level"),
            speed: document.getElementById("wf-cli-capability-speed"),
            variant: document.getElementById("wf-cli-capability-variant"),
            hint: document.getElementById("wf-cli-capability-hint")
        };
    }

    function workflowCliCapabilityConfig(backendId) {
        backendId = String(backendId || "").trim();
        if (backendId === "codex" && _wfCliInventoryMeta.source !== "codex-unverified") {
            return {
                level: [
                    { value: "", label: "Auto" },
                    { value: "low", label: "Low" },
                    { value: "medium", label: "Medium" },
                    { value: "high", label: "High" }
                ],
                speed: [
                    { value: "", label: "Default" },
                    { value: "flex", label: "Flex" },
                    { value: "fast", label: "Fast" }
                ],
                hint: "Codex only. Level maps to reasoning effort and speed maps to service tier for workflow CLI prompts."
            };
        }
        return null;
    }

    function workflowCliFillCapabilitySelect(select, items, selectedValue) {
        if (!select) return;
        select.innerHTML = (items || []).map(function (item) {
            return '<option value="' + esc(item.value || "") + '">' + esc(item.label || item.value || "") + '</option>';
        }).join("");
        select.value = selectedValue != null ? String(selectedValue) : "";
    }

    function workflowCliApplyStoredCapabilities(state) {
        var els = workflowCliCapabilityElements();
        state = state || {};
        if (els.level && state.codex_reasoning_effort != null) {
            els.level.value = String(state.codex_reasoning_effort || "");
        }
        if (els.speed && state.codex_service_tier != null) {
            els.speed.value = String(state.codex_service_tier || "");
        }
    }

    function workflowCliApplyModelSelection(modelId, options) {
        options = options || {};
        var modelSel = document.getElementById("wf-cli-model-select");
        if (!modelSel || !modelId) return;
        modelSel.value = modelId;
        var model = _wfCliInventoryById[modelId] || null;
        syncWorkflowCliCapabilityControls(model);
        refreshWorkflowCliModelCardSelection();
        if (options.persistOnly) {
            persistWorkflowCliBoardState({ model: modelSel.value || "" });
            return;
        }
        modelSel.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function syncWorkflowCliCapabilityControls(model) {
        var els = workflowCliCapabilityElements();
        var config = workflowCliCapabilityConfig(workflowCliActiveBackend());
        var levelGuess = workflowCliLevelLabel(model || {}).toLowerCase();
        var speedGuess = workflowCliSpeedLabel(model || {}).toLowerCase();
        var levelOptions = config && Array.isArray(config.level) ? config.level.filter(function (item) { return String(item.label || item.value || "").trim() !== ""; }) : [];
        var speedOptions = config && Array.isArray(config.speed) ? config.speed.filter(function (item) { return String(item.label || item.value || "").trim() !== ""; }) : [];
        var variantOptions = config && Array.isArray(config.variant) ? config.variant.filter(function (item) { return String(item.label || item.value || "").trim() !== ""; }) : [];
        var showLevel = levelOptions.length > 1;
        var showSpeed = speedOptions.length > 1;
        var showVariant = variantOptions.length > 1;
        if (!els.row) return;
        if (!config || (!showLevel && !showSpeed && !showVariant)) {
            els.row.hidden = true;
            els.row.classList.remove("is-visible");
            if (els.levelField) els.levelField.hidden = true;
            if (els.speedField) els.speedField.hidden = true;
            if (els.variantField) els.variantField.hidden = true;
            return;
        }
        els.row.hidden = false;
        els.row.classList.add("is-visible");
        if (els.levelField) els.levelField.hidden = !showLevel;
        if (els.speedField) els.speedField.hidden = !showSpeed;
        if (els.variantField) els.variantField.hidden = !showVariant;
        if (showLevel) workflowCliFillCapabilitySelect(els.level, levelOptions, /low|medium|high/.test(levelGuess) ? levelGuess : "");
        if (showSpeed) workflowCliFillCapabilitySelect(els.speed, speedOptions, /fast/.test(speedGuess) ? "fast" : "");
        if (showVariant) workflowCliFillCapabilitySelect(els.variant, variantOptions, "");
        if (els.hint) els.hint.textContent = "";
        workflowCliApplyStoredCapabilities(workflowCliStoredState());
    }

    function workflowCliSelectedCapabilities() {
        var els = workflowCliCapabilityElements();
        return {
            codex_reasoning_effort: els.level ? String(els.level.value || "").trim() : "",
            codex_service_tier: els.speed ? String(els.speed.value || "").trim() : ""
        };
    }

    function setWorkflowCliActivity(state, text) {
        var box = document.getElementById("wf-cli-activity");
        var label = document.getElementById("wf-cli-activity-text");
        if (!box) return;
        box.classList.toggle("is-busy", state === "busy");
        box.classList.toggle("is-editing", state === "editing");
        box.classList.toggle("is-ready", state === "ready");
        box.classList.toggle("is-visible", state === "busy" || state === "editing");
        if (label) label.textContent = text || "Idle.";
    }

    function refreshWorkflowCliModelCardSelection() {
        var select = document.getElementById("wf-cli-model-select");
        var active = select ? String(select.value || "") : "";
        document.querySelectorAll(".wf-cli-model-row").forEach(function (item) {
            item.classList.toggle("is-active", String(item.dataset.modelId || "") === active);
        });
    }

    function setWorkflowCliBackendSetupStatus(row) {
        if (!_wfCliSessionReady) {
            setWorkflowCliStatus("idle", "Not connected");
        }
    }

    function setWorkflowCliStatus(state, text) {
        var panel = document.getElementById("wf-cli-panel");
        var dot = document.getElementById("wf-cli-status-dot");
        var label = document.getElementById("wf-cli-status-text");
        var color = state === "connected" || state === "running"
            ? "#22c55e"
            : (state === "connecting" ? "#3b82f6" : (state === "error" ? "#ef4444" : "#6b7280"));
        if (panel) {
            panel.classList.toggle("is-connected", state === "connected");
            panel.classList.toggle("is-running", state === "running");
            panel.classList.toggle("is-error", state === "error");
        }
        if (dot) dot.style.background = color;
        if (dot) dot.title = text || state || "Not connected";
        if (label) label.textContent = text || state || "Idle";
    }

    function appendWorkflowCliLine(kind, text) {
        var transcript = document.getElementById("wf-cli-transcript");
        if (!transcript || !text) return;
        var div = document.createElement("div");
        div.className = "wf-cli-line-" + (kind || "system");
        div.textContent = text;
        transcript.appendChild(div);
        transcript.scrollTop = transcript.scrollHeight;
    }

    function workflowCliRecoveryElements() {
        return {
            panel: document.getElementById("wf-cli-recovery-panel"),
            title: document.getElementById("wf-cli-recovery-title"),
            summary: document.getElementById("wf-cli-recovery-summary"),
            checks: document.getElementById("wf-cli-recovery-checks"),
            actions: document.getElementById("wf-cli-recovery-actions"),
            dismiss: document.getElementById("wf-cli-recovery-dismiss")
        };
    }

    function clearWorkflowCliRecovery() {
        var els = workflowCliRecoveryElements();
        _wfCliRecoveryActions = [];
        if (!els.panel) return;
        els.panel.classList.remove("is-open");
        if (els.summary) els.summary.textContent = "";
        if (els.checks) els.checks.innerHTML = "";
        if (els.actions) els.actions.innerHTML = "";
    }

    function showWorkflowCliRecovery(config) {
        var els = workflowCliRecoveryElements();
        if (!els.panel) return;
        _wfCliRecoveryActions = Array.isArray(config && config.actions) ? config.actions.slice() : [];
        if (els.title) els.title.textContent = (config && config.title) || "CLI setup needs attention";
        if (els.summary) els.summary.textContent = (config && config.summary) || "The CLI needs a little help before it can connect.";
        if (els.checks) {
            var summaryText = String((config && config.summary) || "").trim().toLowerCase();
            var checks = (Array.isArray(config && config.checks) ? config.checks : []).filter(function (check) {
                var label = String((check && check.label) || "").trim();
                var message = String((check && check.message) || "").trim();
                var combined = (label && message) ? (label + ": " + message) : (label || message);
                return combined && combined.trim().toLowerCase() !== summaryText;
            });
            els.checks.innerHTML = checks.map(function (check) {
                var ok = !!check.ok;
                return '<div class="wf-cli-recovery-check' + (ok ? ' is-ok' : '') + '">' +
                    '<span class="wf-cli-recovery-check-mark">' + (ok ? '\u2713' : '\u2717') + '</span>' +
                    '<span>' + esc(check.label || check.message || "") + (check.message && check.label ? ': ' + esc(check.message) : "") + '</span>' +
                '</div>';
            }).join("");
        }
        if (els.actions) {
            els.actions.innerHTML = _wfCliRecoveryActions.map(function (action, idx) {
                return '<button type="button" class="wf-cli-recovery-action" data-wf-cli-recovery-action="' + idx + '">' + esc(action.label || "Fix") + '</button>';
            }).join("");
        }
        els.panel.classList.add("is-open");
    }

    function workflowCliCopyText(text, successMsg) {
        if (!text) return Promise.reject(new Error("Nothing to copy"));
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
            return Promise.reject(new Error("Clipboard is unavailable in this browser"));
        }
        return navigator.clipboard.writeText(text).then(function () {
            snack(successMsg || "Copied", "success");
        });
    }

    function workflowCliLockedModelMap() {
        try {
            var raw = localStorage.getItem(WF_CLI_LOCKED_MODEL_STORAGE_KEY);
            var parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (e) {
            return {};
        }
    }

    function workflowCliRememberLockedModel(projectId, candidate) {
        if (!projectId || !candidate || !candidate.model) return;
        try {
            var map = workflowCliLockedModelMap();
            map[String(projectId)] = {
                provider: String(candidate.provider || "ollama"),
                model: String(candidate.model || ""),
                saved_at: Date.now()
            };
            localStorage.setItem(WF_CLI_LOCKED_MODEL_STORAGE_KEY, JSON.stringify(map));
        } catch (e) {}
    }

    function workflowCliLockedModel(projectId) {
        if (!projectId) return null;
        var map = workflowCliLockedModelMap();
        var entry = map[String(projectId)];
        if (!entry || !entry.model) return null;
        return {
            provider: String(entry.provider || "ollama"),
            model: String(entry.model || "")
        };
    }

    function workflowCliForgetLockedModel(projectId) {
        if (!projectId) return;
        try {
            var map = workflowCliLockedModelMap();
            delete map[String(projectId)];
            localStorage.setItem(WF_CLI_LOCKED_MODEL_STORAGE_KEY, JSON.stringify(map));
        } catch (e) {}
    }

    function workflowCliPreflightRecovery(pf) {
        var checks = Array.isArray(pf && pf.checks) ? pf.checks : [];
        var actions = [];
        var piMissing = checks.some(function (check) { return check && check.id === "pi_binary" && !check.ok; });
        if (workflowCliActiveBackend() === "pi" && !piMissing) {
            actions.push({ action: "pi_login", label: "Start Pi login" });
        }
        (Array.isArray(pf && pf.fixes) ? pf.fixes : []).forEach(function (fix) {
            actions.push({
                action: fix.action,
                label: fix.label || "Fix",
                payload: fix.payload || {}
            });
        });
        return {
            title: workflowCliActiveBackend() === "pi" ? "Pi connection needs attention" : "CLI setup needs attention",
            summary: (pf && pf.user_message) || "The CLI is blocked by setup or account issues.",
            checks: checks.map(function (check) {
                return {
                    ok: !!check.ok,
                    label: check.id ? String(check.id).replace(/_/g, " ") : "Check",
                    message: check.message || ""
                };
            }),
            actions: actions
        };
    }

    function workflowCliBackendRecovery(row) {
        row = row || {};
        var summary = row.message || row.setup_instructions || ((row.name || "This CLI") + " is not ready yet.");
        var actions = [];
        var piMissing = row.id === "pi" && /not installed|npm install -g/i.test(summary);
        if (row.id === "pi" && /npm install -g/i.test(summary)) {
            actions.push({
                action: "copy_command",
                label: "Copy install command",
                payload: { command: "npm install -g @mariozechner/pi-coding-agent" }
            });
        }
        if (row.id === "pi" && !piMissing) {
            actions.push({ action: "pi_login", label: "Start Pi login" });
        }
        if (row.id === "cursor" || row.id === "codex" || row.id === "claude_code" || row.id === "opencode" || row.id === "cline") {
            actions.push({ action: "open_key_panel", label: "Open key / provider panel" });
        }
        actions.push({ action: "reconnect", label: "Try opening the session again" });
        return {
            title: (row.name || "CLI") + " needs setup",
            summary: summary,
            checks: [],
            actions: actions
        };
    }

    function workflowCliHandleRecoveryAction(action) {
        action = action || {};
        var payload = action.payload || {};
        if (action.action === "open_url" && payload.url) {
            window.open(payload.url, "_blank", "noopener,noreferrer");
            return;
        }
        if (action.action === "copy_command" && payload.command) {
            workflowCliCopyText(payload.command, "Command copied").catch(function (e) {
                snack(e.message || "Could not copy command", "error");
            });
            return;
        }
        if (action.action === "focus_model") {
            var modelSel = document.getElementById("wf-cli-model-select");
            if (modelSel) {
                modelSel.focus();
                modelSel.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
            return;
        }
        if (action.action === "open_key_panel") {
            setWorkflowCliKeyPanelOpen(true);
            return;
        }
        if (action.action === "pi_login") {
            api("POST", "/pi/login", {}).then(function (resp) {
                snack((resp && resp.message) || "Pi login started", "success");
            }).catch(function (e) {
                snack(workflowCliErrorMessage(e, "Could not start Pi login"), "error");
            });
            return;
        }
        if (action.action === "use_model" && payload.model) {
            var projectId = workflowBoardProjectId();
            if (!projectId) {
                snack("Link the board to a project first", "error");
                return;
            }
            api("POST", "/projects/cli-model", {
                project_id: projectId,
                backend_id: "pi",
                provider: payload.provider || "ollama",
                model: payload.model
            }).then(function () {
                snack("Switched Pi to " + (payload.provider || "ollama") + "/" + payload.model, "success");
                return ensureWorkflowCliSessionForArea({ reason: "model-switch" });
            }).catch(function (e) {
                snack(workflowCliErrorMessage(e, "Could not switch Pi model"), "error");
            });
            return;
        }
        if (action.action === "recheck" || action.action === "reconnect") {
            ensureWorkflowCliSessionForArea({ reason: action.action }).catch(function () {});
        }
    }

    function bindWorkflowCliRecoveryPanel() {
        var els = workflowCliRecoveryElements();
        if (els.dismiss && els.dismiss.dataset.bound !== "1") {
            els.dismiss.dataset.bound = "1";
            els.dismiss.addEventListener("click", clearWorkflowCliRecovery);
        }
        if (els.actions && els.actions.dataset.bound !== "1") {
            els.actions.dataset.bound = "1";
            els.actions.addEventListener("click", function (evt) {
                var btn = evt.target.closest("[data-wf-cli-recovery-action]");
                if (!btn) return;
                var idx = parseInt(btn.getAttribute("data-wf-cli-recovery-action"), 10);
                if (isNaN(idx) || !_wfCliRecoveryActions[idx]) return;
                workflowCliHandleRecoveryAction(_wfCliRecoveryActions[idx]);
            });
        }
    }

    function workflowCliErrorMessage(err, fallback) {
        var msg = (err && err.message) ? String(err.message) : String(err || fallback || "CLI error");
        if (/failed to fetch/i.test(msg)) {
            return "Could not reach the DecisionsAI backend API. Refresh the page after the local server reloads, then try opening the session again.";
        }
        try {
            var parsed = JSON.parse(msg);
            return parsed.user_message || parsed.error || parsed.message || fallback || "CLI error";
        } catch (e) {
            return msg || fallback || "CLI error";
        }
    }

    function clearWorkflowCliTranscript() {
        var transcript = document.getElementById("wf-cli-transcript");
        if (!transcript) return;
        transcript.innerHTML = "";
        _wfCliAssistantBuffer = "";
    }

    function handleWorkflowCliWsMessage(msg) {
        if (!msg || !msg.type) return;
        if (msg.type === "connected") {
            clearWorkflowCliRecovery();
            setWorkflowCliStatus("connected", workflowCliReadyLabel());
            setWorkflowCliActivity("ready", "Connected. Ready for a prompt.");
            if (Array.isArray(msg.buffer)) {
                clearWorkflowCliTranscript();
                msg.buffer.forEach(renderWorkflowCliBufferEntry);
            }
            return;
        }
        if (msg.type === "preflight") {
            if (msg.ok) {
                clearWorkflowCliRecovery();
                markWorkflowCliSessionReady();
            } else {
                _wfCliSessionReady = false;
                setWorkflowCliSessionReady(false);
                resetWorkflowCliModelSelect("Fix CLI setup first");
                appendWorkflowCliLine("system", msg.user_message || "CLI setup check failed.");
                showWorkflowCliRecovery(workflowCliPreflightRecovery(msg));
                setWorkflowCliStatus("error", "Setup required");
                setWorkflowCliActivity("ready", "Setup needs attention before this CLI can work.");
            }
            return;
        }
        if (msg.type === "error") {
            appendWorkflowCliLine("system", msg.message || "CLI error");
            setWorkflowCliStatus("error", "Error");
            setWorkflowCliActivity("ready", "Stopped with an error.");
            return;
        }
        if (msg.type === "agent_start") {
            setWorkflowCliStatus("running", "Running");
            setWorkflowCliActivity("busy", "Working. The CLI agent is running.");
            _wfCliAssistantBuffer = "";
            startWorkflowCliTicketTimer();
            return;
        }
        if (msg.type === "agent_end") {
            if (_wfCliAssistantBuffer) appendWorkflowCliLine("assistant", _wfCliAssistantBuffer);
            _wfCliAssistantBuffer = "";
            setWorkflowCliStatus("connected", workflowCliReadyLabel());
            setWorkflowCliActivity("ready", "Finished. Ready for the next prompt.");
            flushWorkflowCliTicketTimer();
            return;
        }
        if (msg.type === "message_update" && msg.assistantMessageEvent) {
            var evt = msg.assistantMessageEvent;
            var evtType = String(evt.type || "");
            if (/tool|command|shell|exec|browser|search|read/i.test(evtType)) {
                setWorkflowCliActivity("busy", "Using a tool: " + evtType);
            } else if (/file|edit|patch|write/i.test(evtType)) {
                setWorkflowCliActivity("editing", "Editing or inspecting files.");
            }
            if (evt.type === "text_delta" && evt.delta) {
                setWorkflowCliActivity("busy", "Responding.");
                _wfCliAssistantBuffer += evt.delta;
            }
            if (evt.type === "done" && _wfCliAssistantBuffer) {
                appendWorkflowCliLine("assistant", _wfCliAssistantBuffer);
                _wfCliAssistantBuffer = "";
            }
            return;
        }
        if (/tool|command|shell|exec|browser|search|read/i.test(String(msg.type || ""))) {
            setWorkflowCliActivity("busy", "Using a tool: " + msg.type);
        } else if (/file|edit|patch|write/i.test(String(msg.type || ""))) {
            setWorkflowCliActivity("editing", "Editing or inspecting files.");
        } else if (msg.type !== "ping" && msg.type !== "pong") {
            setWorkflowCliActivity("busy", "Working: " + msg.type);
        }
    }

    function disconnectWorkflowCliWs() {
        flushWorkflowCliTicketTimer();
        if (_wfCliWs) {
            try { _wfCliWs.onclose = null; _wfCliWs.close(); } catch (e) {}
            _wfCliWs = null;
        }
        _wfCliProjectId = null;
        _wfCliConnectPromise = null;
        _wfCliSessionReady = false;
        setWorkflowCliSessionReady(false);
        clearWorkflowCliRecovery();
        setWorkflowCliStatus("idle", "Not connected");
        setWorkflowCliActivity("ready", "Idle. This board session will reconnect automatically while you stay in workflows.");
        workflowCliInputPlaceholder(false);
    }

    function workflowCliModelId(model) {
        if (!model) return "";
        if (typeof model === "string") return model.trim();
        return String(model.id || model.model || model.name || "").trim();
    }

    function workflowCliProviderId(model, fallback) {
        if (!model || typeof model === "string") return fallback || "ollama";
        return String(model.provider || fallback || "ollama").trim() || "ollama";
    }

    function workflowCliPiModelUsableNow(model) {
        var id = workflowCliModelId(model);
        if (!id || id === "auto") return false;
        if (model && typeof model === "object" && model.supports_chat === false) return false;
        if (model && typeof model === "object" && model.usable === false) return false;
        return true;
    }

    function workflowCliPushUniqueModelCandidate(out, seen, model, fallbackProvider) {
        var id = workflowCliModelId(model);
        if (!id || seen[id] || !workflowCliPiModelUsableNow(model)) return;
        seen[id] = true;
        out.push({ provider: workflowCliProviderId(model, fallbackProvider), model: id });
    }

    function workflowCliPiFixModelFromPreflight(pf) {
        var fixes = Array.isArray(pf && pf.fixes) ? pf.fixes : [];
        for (var i = 0; i < fixes.length; i++) {
            var payload = fixes[i] && fixes[i].payload;
            if (payload && payload.model && payload.provider) {
                return payload;
            }
        }
        var suggested = Array.isArray(pf && pf.suggested_models) ? pf.suggested_models : [];
        if (suggested.length) return { provider: "ollama", model: suggested[0] };
        return null;
    }

    function workflowCliPiModelCandidatesFromPreflight(pf) {
        var out = [];
        var seen = {};
        var fixes = Array.isArray(pf && pf.fixes) ? pf.fixes : [];
        for (var i = 0; i < fixes.length; i++) {
            var payload = fixes[i] && fixes[i].payload;
            if (payload && payload.model) workflowCliPushUniqueModelCandidate(out, seen, payload, payload.provider || "ollama");
        }
        var suggested = Array.isArray(pf && pf.suggested_models) ? pf.suggested_models : [];
        for (var j = 0; j < suggested.length; j++) {
            workflowCliPushUniqueModelCandidate(out, seen, { provider: "ollama", model: suggested[j] }, "ollama");
        }
        var oldFix = workflowCliPiFixModelFromPreflight(pf);
        if (oldFix) workflowCliPushUniqueModelCandidate(out, seen, oldFix, oldFix.provider || "ollama");
        return out;
    }

    function workflowCliPiModelCandidatesFromCatalog(data) {
        var out = [];
        var seen = {};
        var recommended = data && data.recommended_model;
        if (recommended && recommended.model) workflowCliPushUniqueModelCandidate(out, seen, recommended.model, recommended.provider || "ollama");
        if (recommended && recommended.id) workflowCliPushUniqueModelCandidate(out, seen, recommended, recommended.provider || "ollama");
        var models = Array.isArray(data && data.models) ? data.models : [];
        models
            .slice()
            .sort(function (a, b) {
                function score(model) {
                    var value = 0;
                    if (model.scope === "scoped") value += 40;
                    if (model.local) value += 30;
                    if (model.free) value += 20;
                    if (model.provider === "ollama") value += 10;
                    if (model.supports_chat === false || model.usable === false) value -= 1000;
                    return value;
                }
                return score(b) - score(a);
            })
            .forEach(function (model) {
                workflowCliPushUniqueModelCandidate(out, seen, model, "ollama");
            });
        return out;
    }

    function workflowCliMergeModelCandidates() {
        var out = [];
        var seen = {};
        Array.prototype.slice.call(arguments).forEach(function (group) {
            (group || []).forEach(function (candidate) {
                workflowCliPushUniqueModelCandidate(out, seen, candidate, candidate && candidate.provider || "ollama");
            });
        });
        return out;
    }

    function tryWorkflowCliPiModelCandidates(projectId, candidates, index, originalPf) {
        if (!candidates || index >= candidates.length) {
            throw new Error((originalPf && originalPf.user_message) || "Pi CLI is not ready. No available chat-capable Pi model could be selected automatically.");
        }
        var fix = candidates[index];
        appendWorkflowCliLine("system", "Trying Pi model " + fix.provider + "/" + fix.model + " because the selected model cannot chat.");
        return api("POST", "/projects/cli-model", {
            project_id: projectId,
            backend_id: "pi",
            provider: fix.provider || "ollama",
            model: fix.model
        }).then(function () {
            return api("GET", "/projects/" + encodeURIComponent(projectId) + "/cli/preflight?probe=true");
        }).then(function (retryPf) {
            if (!retryPf || retryPf.ok === false) {
                throw new Error((retryPf && retryPf.user_message) || "Pi CLI is not ready.");
            }
            workflowCliRememberLockedModel(projectId, fix);
            appendWorkflowCliLine("system", "Pi is now using " + (fix.provider || "ollama") + "/" + fix.model + ".");
            return loadWorkflowCliModels("pi", { force: true }).then(function () {
                var modelSel = document.getElementById("wf-cli-model-select");
                if (modelSel && fix.model) {
                    modelSel.value = fix.model;
                    refreshWorkflowCliModelCardSelection();
                    refreshWorkflowCliKeyPanel();
                }
                return retryPf;
            });
        }).catch(function (err) {
            appendWorkflowCliLine("system", "Pi model " + fix.model + " did not work: " + workflowCliErrorMessage(err, "model failed"));
            return tryWorkflowCliPiModelCandidates(projectId, candidates, index + 1, originalPf);
        });
    }

    function retryWorkflowCliPiPreflightWithSuggestedModel(projectId, pf) {
        var params = "backend_id=pi";
        if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
        return api("GET", "/projects/cli-models?" + params).catch(function (err) {
            appendWorkflowCliLine("system", "Could not load Pi model catalog before auto-fix: " + workflowCliErrorMessage(err, "model catalog failed"));
            return { models: [] };
        }).then(function (catalog) {
            renderWorkflowCliModelInventory(catalog, catalog.message || "No models returned for Pi.");
            var remembered = workflowCliLockedModel(projectId);
            var candidates = workflowCliMergeModelCandidates(
                remembered ? [remembered] : [],
                workflowCliPiModelCandidatesFromPreflight(pf),
                workflowCliPiModelCandidatesFromCatalog(catalog)
            ).slice(0, 10);
            if (!candidates.length) {
                throw new Error((pf && pf.user_message) || "Pi CLI is not ready. No chat-capable model was available to try.");
            }
            return tryWorkflowCliPiModelCandidates(projectId, candidates, 0, pf);
        }).catch(function (err) {
            if (err && err.message && /No chat-capable model|No available chat-capable/.test(err.message)) throw err;
            throw err;
        });
    }

    function markWorkflowCliSessionReady() {
        var backendId = workflowCliActiveBackend();
        return loadWorkflowCliModels(backendId, { force: true }).then(function () {
            _wfCliSessionReady = true;
            setWorkflowCliSessionReady(true);
            clearWorkflowCliRecovery();
            persistWorkflowCliBoardState({ connected: true });
            setWorkflowCliStatus("connected", workflowCliReadyLabel());
        });
    }

    function verifyWorkflowCliReady(projectId, backendId) {
        backendId = backendId || "pi";
        if (backendId === "pi") {
            return api("GET", "/projects/" + encodeURIComponent(projectId) + "/cli/preflight?probe=true").then(function (pf) {
                if (!pf || pf.ok === false) {
                    return retryWorkflowCliPiPreflightWithSuggestedModel(projectId, pf).catch(function (err) {
                        var msg = (pf && pf.user_message) || workflowCliErrorMessage(err, "Pi CLI is not ready");
                        var wrapped = new Error(msg);
                        wrapped.workflowCliRecovery = workflowCliPreflightRecovery(pf || { user_message: msg, checks: [] });
                        throw wrapped;
                    });
                }
                return pf;
            });
        }
        return fetchWorkflowProjectCliBackends(projectId).then(function (data) {
            var row = workflowCliBackendRow(data.backends || [], backendId);
            if (row && !workflowCliBackendWorkflowReady(row)) {
                var err = new Error(row.health_message || row.message || row.setup_instructions || (backendId + " is not ready"));
                err.workflowCliRecovery = workflowCliBackendRecovery(row);
                throw err;
            }
            return row;
        });
    }

    function runWorkflowCliBackendSetup(projectId, backendId) {
        return Promise.resolve();
    }

    function openWorkflowCliWebSocket(projectId) {
        if (_wfCliWs && _wfCliProjectId === projectId && _wfCliWs.readyState === WebSocket.OPEN) {
            return Promise.resolve(_wfCliWs);
        }
        if (_wfCliConnectPromise && _wfCliProjectId === projectId) {
            return _wfCliConnectPromise;
        }
        if (_wfCliWs) {
            try { _wfCliWs.onclose = null; _wfCliWs.close(); } catch (e) {}
            _wfCliWs = null;
        }
        _wfCliProjectId = projectId;
        _wfCliConnectPromise = new Promise(function (resolve, reject) {
            var token = (window.DECISIONSAI_INTERNAL_API_TOKEN || "").trim();
            var wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            var wsUrl = wsProtocol + "//" + window.location.host + "/api/projects/" + projectId + "/terminal/ws";
            var ctx = workflowCliAreaContext();
            var params = [];
            if (token) params.push("internal_token=" + encodeURIComponent(token));
            if (ctx && ctx.board_id) params.push("board_id=" + encodeURIComponent(ctx.board_id));
            if (ctx && ctx.workflow_id) params.push("workflow_id=" + encodeURIComponent(ctx.workflow_id));
            if (params.length) wsUrl += "?" + params.join("&");
            setWorkflowCliStatus("connecting", "Connecting");
            var ws = new WebSocket(wsUrl);
            ws.onopen = function () {
                _wfCliWs = ws;
                resolve(ws);
            };
            ws.onmessage = function (event) {
                try { handleWorkflowCliWsMessage(JSON.parse(event.data)); } catch (e) {}
            };
            ws.onerror = function () {
                setWorkflowCliStatus("error", "Connection failed");
                reject(new Error("CLI connection failed"));
            };
            ws.onclose = function () {
                if (_wfCliWs === ws) {
                    _wfCliWs = null;
                    _wfCliConnectPromise = null;
                    if (_wfCliSessionReady) {
                        flushWorkflowCliTicketTimer();
                        _wfCliSessionReady = false;
                        setWorkflowCliSessionReady(false);
                        setWorkflowCliStatus("idle", "Disconnected");
                    }
                }
            };
        }).finally(function () {
            _wfCliConnectPromise = null;
        });
        return _wfCliConnectPromise;
    }

    function workflowCliAreaContext() {
        var opt = currentWorkflowBoardOption();
        var projectId = workflowBoardProjectId();
        var detail = document.getElementById("wf-detail");
        if (!currentWorkflowId || !opt || !projectId || !detail || detail.classList.contains("hidden")) return null;
        return {
            workflow_id: parseInt(currentWorkflowId, 10) || currentWorkflowId,
            board_id: parseInt(opt.local_id || opt.id || "", 10) || _coerceWorkflowCliInt(opt.local_id || opt.id),
            project_id: projectId,
            backend_id: workflowCliActiveBackend() || "pi",
        };
    }

    function _coerceWorkflowCliInt(value) {
        var n = parseInt(value, 10);
        return Number.isFinite(n) ? n : null;
    }

    function workflowCliAreaShouldPing() {
        return !!workflowCliAreaContext() && !document.hidden;
    }

    function workflowCliSendKeepalive(present) {
        var ctx = workflowCliAreaContext();
        if (!ctx) return Promise.resolve();
        return api("POST", "/projects/" + encodeURIComponent(ctx.project_id) + "/terminal/keepalive", {
            backend_id: ctx.backend_id,
            workflow_id: ctx.workflow_id,
            board_id: ctx.board_id,
            present: present !== false,
        }).catch(function () {});
    }

    function workflowCliAreaHeartbeatTick() {
        if (!_wfCliAreaPresent || !workflowCliAreaShouldPing()) return;
        workflowCliSendKeepalive(true);
    }

    function stopWorkflowCliAreaHeartbeat() {
        if (_wfCliAreaHeartbeatTimer) {
            clearInterval(_wfCliAreaHeartbeatTimer);
            _wfCliAreaHeartbeatTimer = null;
        }
    }

    function startWorkflowCliAreaHeartbeat() {
        stopWorkflowCliAreaHeartbeat();
        if (!_wfCliAreaPresent || !workflowCliAreaShouldPing()) return;
        workflowCliSendKeepalive(true);
        _wfCliAreaHeartbeatTimer = setInterval(workflowCliAreaHeartbeatTick, WF_CLI_AREA_HEARTBEAT_MS);
    }

    function startWorkflowCliSession(options) {
        options = options || {};
        var projectId = workflowBoardProjectId();
        var backendId = workflowCliActiveBackend();
        if (!projectId) {
            snack("Link the board to a project first", "error");
            return Promise.reject(new Error("No project"));
        }
        if (_wfCliWs) {
            try { _wfCliWs.onclose = null; _wfCliWs.close(); } catch (e) {}
            _wfCliWs = null;
        }
        _wfCliConnectPromise = null;
        clearWorkflowCliTranscript();
        clearWorkflowCliRecovery();
        _wfCliSessionReady = false;
        setWorkflowCliSessionReady(false);
        resetWorkflowCliModelSelect("Connecting...");
        setWorkflowCliStatus("connecting", "Starting");
        return fetchWorkflowProjectCliBackends(projectId).then(function (data) {
            var row = workflowCliBackendRow(data.backends || [], backendId);
            if (row && !workflowCliBackendWorkflowReady(row)) {
                var err = new Error(row.health_message || row.message || row.setup_instructions || (backendId + " is not installed or configured"));
                err.workflowCliRecovery = workflowCliBackendRecovery(row);
                throw err;
            }
            return syncWorkflowCliProjectBackend(projectId, backendId);
        }).then(function () {
            return runWorkflowCliBackendSetup(projectId, backendId);
        }).then(function () {
            return openWorkflowCliWebSocket(projectId);
        }).then(function () {
            return verifyWorkflowCliReady(projectId, backendId);
        }).then(function () {
            return markWorkflowCliSessionReady();
        }).then(function () {
            return workflowCliSendKeepalive(true);
        }).catch(function (e) {
            var msg = workflowCliErrorMessage(e, "Could not start CLI");
            disconnectWorkflowCliWs();
            appendWorkflowCliLine("system", msg);
            if (e && e.workflowCliRecovery) showWorkflowCliRecovery(e.workflowCliRecovery);
            setWorkflowCliStatus("error", "Not ready");
            persistWorkflowCliBoardState({ connected: false });
            if (!options.quiet) snack(msg, "error");
            throw e;
        });
    }

    function ensureWorkflowCliSessionForArea(options) {
        options = options || {};
        if (_wfCliAreaEnsurePromise) return _wfCliAreaEnsurePromise;
        if (!workflowCliAreaShouldPing()) return Promise.resolve();
        _wfCliAreaEnsurePromise = loadWorkflowCliBackends().then(function (data) {
            return restoreWorkflowCliBoardState(data, options);
        }).then(function () {
            if (_wfCliSessionReady || _wfCliConnectPromise || (_wfCliWs && _wfCliWs.readyState === WebSocket.OPEN)) {
                return workflowCliSendKeepalive(true);
            }
            return startWorkflowCliSession({ automatic: true, quiet: true });
        }).finally(function () {
            _wfCliAreaEnsurePromise = null;
        });
        return _wfCliAreaEnsurePromise;
    }

    function setWorkflowCliAreaPresence(present, options) {
        options = options || {};
        _wfCliAreaPresent = !!present;
        if (_wfCliAreaPresent) {
            startWorkflowCliAreaHeartbeat();
            if (options.eager || workflowCliShouldWarmOnPresence()) {
                return ensureWorkflowCliSessionForArea(options || {});
            }
            return workflowCliSendKeepalive(true);
        }
        stopWorkflowCliAreaHeartbeat();
        return workflowCliSendKeepalive(false);
    }

    function ensureWorkflowCliWs(projectId) {
        if (_wfCliWs && _wfCliProjectId === projectId && _wfCliWs.readyState === WebSocket.OPEN) {
            return Promise.resolve(_wfCliWs);
        }
        return startWorkflowCliSession().then(function () {
            if (!_wfCliWs || _wfCliWs.readyState !== WebSocket.OPEN) {
                throw new Error("CLI is not connected");
            }
            return _wfCliWs;
        });
    }

    function workflowCliBackendRow(backends, backendId) {
        backends = Array.isArray(backends) ? backends : [];
        return backends.filter(function (row) { return String(row.id) === String(backendId); })[0] || null;
    }

    function syncWorkflowCliProjectBackend(projectId, backendId) {
        return api("PUT", "/projects/" + encodeURIComponent(projectId) + "/coding-backend", {
            coding_backend: backendId || "pi"
        });
    }

    function syncWorkflowCliProjectModel(projectId, backendId) {
        var modelSel = document.getElementById("wf-cli-model-select");
        var model = modelSel ? (modelSel.value || "").trim() : "";
        model = (model || "").trim();
        if (!model || model === "auto") return Promise.resolve();
        var provider = providerForCliModelBackend(backendId, selectedCliModelProvider(modelSel));
        return api("POST", "/projects/cli-model", {
            project_id: projectId,
            backend_id: backendId || "pi",
            model: model,
            provider: provider
        }).catch(function (e) {
            var msg = e.message || "Could not set CLI model";
            snack(msg, "error");
            throw e;
        });
    }

    function prepareWorkflowCliSend(projectId) {
        var backendId = workflowCliActiveBackend();
        var modelSel = document.getElementById("wf-cli-model-select");
        var model = modelSel ? (modelSel.value || "auto") : "auto";
        if (!_wfCliSessionReady) {
            return Promise.reject(new Error("The board session is still starting. Try again in a moment."));
        }
        return syncWorkflowCliProjectModel(projectId, backendId);
    }

    function sendWorkflowCliPrompt() {
        var projectId = workflowBoardProjectId();
        var input = document.getElementById("wf-cli-input");
        var modelSel = document.getElementById("wf-cli-model-select");
        var message = input ? (input.value || "").trim() : "";
        if (!projectId) {
            snack("Link the board to a project first", "error");
            return;
        }
        if (!_wfCliSessionReady) {
            snack("The board session is still starting. Try again in a moment.", "error");
            return;
        }
        if (!message) return;
        flushWorkflowCliTicketTimer();
        appendWorkflowCliLine("user", message);
        setWorkflowCliActivity("busy", "Thinking. Waiting for the CLI agent to respond.");
        if (input) input.value = "";
        var model = modelSel ? (modelSel.value || "auto") : "auto";
        var capabilities = workflowCliSelectedCapabilities();
        prepareWorkflowCliSend(projectId).then(function () {
            if (!_wfCliWs || _wfCliWs.readyState !== WebSocket.OPEN) {
                throw new Error("CLI disconnected — open the session again");
            }
            return _wfCliWs;
        }).then(function (ws) {
            startWorkflowCliTicketTimer();
            ws.send(JSON.stringify({
                type: "prompt",
                message: message,
                model: model && model !== "auto" ? model : "",
                codex_reasoning_effort: capabilities.codex_reasoning_effort || "",
                codex_service_tier: capabilities.codex_service_tier || ""
            }));
        }).catch(function (e) {
            var msg = workflowCliErrorMessage(e, "Could not send to project CLI");
            appendWorkflowCliLine("system", msg);
            setWorkflowCliStatus("error", "Not ready");
            snack(msg, "error");
        });
    }

    function setWorkflowCliBackendAndModel(backendId, model) {
        var backendSel = document.getElementById("wf-cli-backend-select");
        var modelSel = document.getElementById("wf-cli-model-select");
        if (backendSel && backendId) backendSel.value = backendId;
        if (modelSel && model) modelSel.value = model;
    }

    function openTicketInWorkflowCli(ticketId) {
        if (!ticketId) return Promise.reject(new Error("No ticket selected"));
        var ticketPromise = api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId));
        var ctxPromise = api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/cli-context");
        return Promise.all([ticketPromise, ctxPromise]).then(function (results) {
            var ticket = results[0] || {};
            var ctx = results[1] || {};
            bindWorkflowCliTicket(ticket);
            switchTab("cli", { persist: true });
            return loadWorkflowCliBackends().then(function () {
                setWorkflowCliBackendAndModel(ctx.backend_id || "", ctx.model || "auto");
                return ensureWorkflowCliSessionForArea({ reason: "ticket-open" });
            }).then(function () {
                bindWorkflowCliTicket(ticket);
                setWorkflowCliBackendAndModel(ctx.backend_id || "", ctx.model || "auto");
                var input = document.getElementById("wf-cli-input");
                if (input) input.value = ctx.instruction || "";
                sendWorkflowCliPrompt();
            });
        });
    }

    function loadWorkflowCliModels(backendId, options) {
        options = options || {};
        var select = document.getElementById("wf-cli-model-select");
        if (!select) return Promise.resolve();
        if (!options.force && !options.allowDisconnected && !_wfCliSessionReady) {
            resetWorkflowCliModelSelect("Choose a CLI first");
            return Promise.resolve();
        }
        var projectId = workflowBoardProjectId();
        var params = "backend_id=" + encodeURIComponent(backendId || "pi");
        if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
        return api("GET", "/projects/cli-models?" + params).then(function (data) {
            populateCliModelSelect(select, data, { includeAuto: true });
            var preferredModel = String(options.preferredModel || "").trim();
            if (preferredModel) {
                var hasPreferred = Array.prototype.some.call(select.options || [], function (opt) {
                    return String(opt.value || "").trim() === preferredModel;
                });
                if (hasPreferred) select.value = preferredModel;
            }
            renderWorkflowCliModelInventory(data, data.message || "No models returned for this CLI.");
            syncWorkflowCliCapabilityControls(_wfCliInventoryById[select.value || ""] || null);
            if (!data.models || !data.models.length) {
                if (data.message) appendWorkflowCliLine("system", data.message);
            }
            select.disabled = !_wfCliSessionReady && !options.keepEnabled;
            refreshWorkflowCliKeyPanel();
            refreshWorkflowCliModelCardSelection();
            persistWorkflowCliBoardState({ backend_id: backendId || "pi", model: select.value || "" });
        }).catch(function (e) {
            select.innerHTML = "";
            var opt = document.createElement("option");
            opt.value = "auto";
            opt.textContent = "Auto";
            select.appendChild(opt);
            select.value = "auto";
            select.disabled = !_wfCliSessionReady && !options.keepEnabled;
            renderWorkflowCliModelInventory({ models: [] }, "Could not load models; using Auto.");
            syncWorkflowCliCapabilityControls(null);
            if (!options.quiet) appendWorkflowCliLine("system", (e && e.message) || "Could not load models; using Auto.");
            refreshWorkflowCliKeyPanel();
        });
    }

    function loadWorkflowCliBackends() {
        var projectId = workflowBoardProjectId();
        var select = document.getElementById("wf-cli-backend-select");
        var backendTrigger = document.getElementById("wf-cli-backend-trigger");
        var backendTriggerName = document.getElementById("wf-cli-backend-trigger-name");
        var backendTriggerDot = document.getElementById("wf-cli-backend-trigger-dot");
        if (!projectId) {
            if (select) select.disabled = true;
            if (backendTrigger) backendTrigger.disabled = true;
            disconnectWorkflowCliWs();
            resetWorkflowCliModelSelect("Choose a CLI first");
            return Promise.resolve();
        }
        if (_wfCliProjectId && _wfCliProjectId !== projectId) disconnectWorkflowCliWs();
        if (backendTriggerName) backendTriggerName.textContent = "Connecting...";
        if (backendTriggerDot) {
            backendTriggerDot.className = "wf-cli-backend-dot";
            backendTriggerDot.classList.add("is-missing");
        }
        return fetchWorkflowProjectCliBackends(projectId).then(function (data) {
            var backends = data.backends || [];
            var cliBackends = workflowCliTabBackends(backends);
            var saved = workflowCliStoredState();
            var currentSelection = select && select.value ? select.value : "";
            var preferred = saved.backend_id || currentSelection || data.active_backend || "pi";
            var active = workflowCliResolveActiveBackend(backends, preferred);
            if (select) {
                select.innerHTML = cliBackends.map(function (b) {
                    return '<option value="' + esc(b.id) + '" data-supports-rpc="' + (b.supports_rpc ? "true" : "false") + '" data-ready="' + (workflowCliBackendWorkflowReady(b) ? "true" : "false") + '">' + esc(workflowCliBackendDisplayName(b)) + "</option>";
                }).join("");
                if (!cliBackends.length) {
                    select.innerHTML = '<option value="pi" data-supports-rpc="true">Pi</option>';
                }
                select.value = active;
                select.disabled = false;
            }
            renderWorkflowCliBackendMenu(cliBackends.length ? cliBackends : backends, active);
            var activeRow = workflowCliBackendRow(cliBackends.length ? cliBackends : backends, active);
            if (!_wfCliSessionReady) {
                setWorkflowCliSessionReady(false);
                setWorkflowCliStatus("idle", "Not connected");
                setWorkflowCliBackendSetupStatus(activeRow);
            }
            refreshWorkflowCliKeyPanel();
            persistWorkflowCliBoardState({ backend_id: active });
            return data;
        });
    }

    function restoreWorkflowCliBoardState(backendsData, options) {
        options = options || {};
        var projectId = workflowBoardProjectId();
        var backendSel = document.getElementById("wf-cli-backend-select");
        if (!projectId || !backendSel) return Promise.resolve();
        var saved = workflowCliStoredState();
        var backendId = String(saved.backend_id || backendSel.value || "pi").trim() || "pi";
        return fetchWorkflowCliTerminalState(projectId).then(function (terminalState) {
            var liveBackendId = String(terminalState && terminalState.backend_id || "").trim();
            var shouldReattach = !!(terminalState && (terminalState.connected || terminalState.alive));
            if (liveBackendId) backendId = liveBackendId;
            backendSel.value = backendId;
            renderWorkflowCliBackendMenu(_wfCliBackendRows, backendId);
            return loadWorkflowCliModels(backendId, {
                allowDisconnected: true,
                preferredModel: saved.model || "",
                quiet: true
            }).then(function () {
                if (!shouldReattach) return;
                if (liveBackendId && liveBackendId !== backendId) return;
                appendWorkflowCliLine("system", "Reattached to the saved workflow CLI session.");
                return openWorkflowCliWebSocket(projectId).then(function () {
                    return markWorkflowCliSessionReady();
                }).catch(function (e) {
                    appendWorkflowCliLine("system", workflowCliErrorMessage(e, "Could not reattach CLI session"));
                    persistWorkflowCliBoardState({ connected: false });
                });
            }).finally(function () {
                persistWorkflowCliBoardState({ connected: shouldReattach });
            });
        }).then(function () {
            return;
        });
    }

    function refreshWorkflowCliTab() {
        syncWorkflowCliLayoutHeight();
        return loadWorkflowCliBackends().then(function (data) {
            syncWorkflowCliLayoutHeight();
            return restoreWorkflowCliBoardState(data).then(function () {
                return data;
            });
        });
    }

    function syncWorkflowCliLayoutHeight() {
        var tab = document.getElementById("wf-tab-cli");
        var panel = document.getElementById("wf-cli-panel");
        var workspace = panel ? panel.querySelector(".wf-cli-workspace") : null;
        if (!tab || !panel || !workspace || tab.hidden || tab.classList.contains("hidden")) return;
        var tabRect = tab.getBoundingClientRect();
        var container = document.getElementById("wf-detail-panel") || document.getElementById("wf-detail-tab-scroll") || tab.parentElement;
        var containerRect = container ? container.getBoundingClientRect() : null;
        var bottom = containerRect ? containerRect.bottom : window.innerHeight;
        var available = Math.floor(bottom - tabRect.top - 18);
        if (!Number.isFinite(available) || available < 220) available = 360;
        panel.style.height = available + "px";
        workspace.style.minHeight = "0";
    }

    function workflowCliNormalizeKeyTarget(id) {
        id = String(id || "").trim().toLowerCase();
        if (id === "openai") return "codex";
        if (id === "google gemini") return "gemini";
        if (id === "kilo") return "kilocode";
        if (id === "ollama" || id === "local" || id === "pi") return "pi";
        return id;
    }

    function workflowCliKeyTargetId() {
        var backend = workflowCliNormalizeKeyTarget(workflowCliActiveBackend());
        var modelSel = document.getElementById("wf-cli-model-select");
        var provider = workflowCliNormalizeKeyTarget(selectedCliModelProvider(modelSel));
        if ((backend === "pi" || backend === "opencode") && provider && provider !== "opencode") {
            return provider;
        }
        return backend || "pi";
    }

    function workflowCliKeyPanelElements() {
        return {
            panel: document.getElementById("wf-cli-key-modal"),
            title: document.getElementById("wf-cli-key-title"),
            status: document.getElementById("wf-cli-key-status"),
            copy: document.getElementById("wf-cli-key-copy"),
            input: document.getElementById("wf-cli-key-input"),
            save: document.getElementById("wf-cli-key-save"),
            toggle: document.getElementById("wf-cli-key-toggle")
        };
    }

    function setWorkflowCliKeyPanelOpen(open) {
        var els = workflowCliKeyPanelElements();
        if (!els.panel) return;
        els.panel.classList.toggle("is-open", !!open);
        els.panel.hidden = !open;
        if (open) refreshWorkflowCliKeyPanel();
    }

    function openWorkflowCliKeyModal() {
        setWorkflowCliKeyPanelOpen(true);
    }

    function closeWorkflowCliKeyModal() {
        setWorkflowCliKeyPanelOpen(false);
    }

    function refreshWorkflowCliKeyPanel() {
        var els = workflowCliKeyPanelElements();
        if (!els.panel) return Promise.resolve();
        var target = workflowCliKeyTargetId();
        if (els.toggle) els.toggle.hidden = true;
        if (els.title) els.title.textContent = "Loading key target...";
        if (els.status) els.status.textContent = target;
        if (els.copy) els.copy.textContent = "Save a credential or endpoint for the selected CLI or routed provider.";
        if (els.input) {
            els.input.value = "";
            els.input.placeholder = "Paste replacement credential or endpoint";
            els.input.hidden = false;
            els.input.disabled = false;
        }
        if (els.save) {
            els.save.hidden = false;
            els.save.disabled = false;
            els.save.textContent = "Save & reconnect";
            els.save.dataset.mode = "save";
        }
        return api("GET", "/projects/cli-setup?backend_id=" + encodeURIComponent(target)).then(function (data) {
            var rows = Array.isArray(data && data.clis) ? data.clis : [];
            var row = rows.filter(function (item) { return String(item.id) === String(target); })[0];
            if (!row) {
                if (els.title) els.title.textContent = "No key target";
                if (els.status) els.status.textContent = "This CLI or provider handles auth elsewhere, so there is nothing swappable here.";
                if (els.copy) els.copy.textContent = "This backend does not expose a configurable credential in the workflow CLI.";
                if (els.input) {
                    els.input.disabled = true;
                    els.input.hidden = true;
                }
                if (els.save) els.save.hidden = true;
                return;
            }
            if (els.toggle) els.toggle.hidden = false;
            if (els.title) els.title.textContent = row.name || target;
            var hasOptionalCliAuth = !!row.credential_optional && workflowCliBackendWorkflowReady(row);
            var hasExistingSecret = !!row.key_set;
            var hasExistingAuth = hasExistingSecret || hasOptionalCliAuth;
            if (els.status) {
                var authStatus = "";
                if (hasExistingSecret) {
                    authStatus = "Saved credential: " + (row.masked || "saved");
                } else if (hasOptionalCliAuth) {
                    authStatus = "CLI auth already available";
                } else if (row.credential_optional) {
                    authStatus = "No saved API key";
                } else {
                    authStatus = "No saved credential";
                }
                els.status.textContent = authStatus + (row.credential_label ? " " + row.credential_label : "");
            }
            if (els.copy) {
                els.copy.textContent = hasExistingAuth
                    ? ((row.notes || "This CLI is already authenticated.") + " No new key is needed unless you want to replace or add an override.")
                    : (row.notes || "Save a credential or endpoint for this CLI.");
            }
            if (els.input) {
                els.input.disabled = hasExistingAuth;
                els.input.hidden = hasExistingAuth;
                els.input.placeholder = row.input_placeholder || (row.credential_type === "url" ? "http://localhost:11434/" : "Paste replacement credential");
            }
            if (els.save) {
                els.save.dataset.target = target;
                els.save.dataset.mode = hasExistingAuth ? "reveal" : "save";
                els.save.disabled = false;
                els.save.hidden = false;
                els.save.textContent = hasExistingAuth
                    ? (hasExistingSecret ? "Replace saved key" : "Add API key override")
                    : "Save & reconnect";
            }
        }).catch(function (e) {
            if (els.toggle) els.toggle.hidden = true;
            if (els.title) els.title.textContent = "Key setup unavailable";
            if (els.status) els.status.textContent = workflowCliErrorMessage(e, "Could not load CLI key setup.");
            if (els.copy) els.copy.textContent = "The workflow CLI could not load setup details for this backend right now.";
            if (els.input) {
                els.input.disabled = true;
                els.input.hidden = true;
            }
            if (els.save) els.save.hidden = true;
        });
    }

    function saveWorkflowCliKeyAndReconnect() {
        var els = workflowCliKeyPanelElements();
        var target = workflowCliKeyTargetId();
        if (els.save && els.save.dataset.mode === "reveal") {
            if (els.input) {
                els.input.hidden = false;
                els.input.disabled = false;
                els.input.focus();
            }
            if (els.copy) {
                els.copy.textContent = "Paste the replacement credential you want DecisionsAI to save for this CLI.";
            }
            if (els.save) {
                els.save.dataset.mode = "save";
                els.save.textContent = "Save & reconnect";
            }
            return;
        }
        var value = els.input ? (els.input.value || "").trim() : "";
        if (!value) {
            snack("Paste the replacement key or endpoint first", "error");
            return;
        }
        if (els.save) els.save.disabled = true;
        api("POST", "/projects/cli-setup", {
            id: target,
            enabled: true,
            value: value
        }).then(function () {
            snack("Saved " + target + " credential", "success");
            if (els.input) els.input.value = "";
            disconnectWorkflowCliWs();
            return loadWorkflowCliBackends();
        }).then(function () {
            return loadWorkflowCliModels(workflowCliActiveBackend(), { force: true });
        }).then(function () {
            closeWorkflowCliKeyModal();
            return startWorkflowCliSession();
        }).catch(function (e) {
            var msg = workflowCliErrorMessage(e, "Could not save CLI credential");
            appendWorkflowCliLine("system", msg);
            snack(msg, "error");
        }).finally(function () {
            if (els.save) els.save.disabled = false;
            refreshWorkflowCliKeyPanel();
        });
    }

    function closeWorkflowCliModelContextMenu() {
        var menu = document.getElementById("wf-cli-model-context-menu");
        if (!menu) return;
        menu.classList.remove("is-open");
        menu.style.left = "";
        menu.style.top = "";
        _wfCliModelContextTargetId = "";
    }

    function openWorkflowCliModelContextMenu(evt, modelId) {
        var menu = document.getElementById("wf-cli-model-context-menu");
        if (!menu || !modelId) return;
        evt.preventDefault();
        _wfCliModelContextTargetId = modelId;
        menu.innerHTML = '' +
            '<button type="button" class="wf-cli-model-context-item" data-wf-cli-model-action="select" role="menuitem">Select model</button>' +
            '<button type="button" class="wf-cli-model-context-item" data-wf-cli-model-action="add" role="menuitem">Add to chosen models</button>';
        menu.style.left = Math.max(12, Math.min(window.innerWidth - 220, evt.clientX || 12)) + "px";
        menu.style.top = Math.max(12, Math.min(window.innerHeight - 120, evt.clientY || 12)) + "px";
        menu.classList.add("is-open");
    }

    function bindWorkflowCliTabControls() {
        bindWorkflowCliRecoveryPanel();
        var backendSel = document.getElementById("wf-cli-backend-select");
        var backendTrigger = document.getElementById("wf-cli-backend-trigger");
        var backendMenu = document.getElementById("wf-cli-backend-menu");
        if (backendSel && backendSel.dataset.bound !== "1") {
            backendSel.dataset.bound = "1";
            backendSel.addEventListener("change", function () {
                setWorkflowCliBackendMenuOpen(false);
                disconnectWorkflowCliWs();
                var projectId = workflowBoardProjectId();
                var backend = backendSel.value || "pi";
                persistWorkflowCliBoardState({ backend_id: backend });
                if (!projectId) return;
                fetchWorkflowProjectCliBackends(projectId).then(function (data) {
                    var cliBackends = workflowCliTabBackends(data.backends || []);
                    var row = workflowCliBackendRow(cliBackends.length ? cliBackends : (data.backends || []), backend);
                    setWorkflowCliBackendSetupStatus(row);
                    renderWorkflowCliBackendMenu(cliBackends.length ? cliBackends : (data.backends || []), backend);
                    refreshWorkflowCliKeyPanel();
                    return loadWorkflowCliModels(backend, { allowDisconnected: true, quiet: true });
                }).then(function () {
                    if (_wfCliAreaPresent) return ensureWorkflowCliSessionForArea({ reason: "backend-change" });
                });
            });
        }
        if (backendTrigger && backendTrigger.dataset.bound !== "1") {
            backendTrigger.dataset.bound = "1";
            backendTrigger.addEventListener("click", function () {
                if (backendTrigger.disabled) return;
                setWorkflowCliBackendMenuOpen(!(backendMenu && backendMenu.classList.contains("is-open")));
            });
        }
        if (backendMenu && backendMenu.dataset.bound !== "1") {
            backendMenu.dataset.bound = "1";
            backendMenu.addEventListener("click", function (evt) {
                var btn = evt.target.closest("[data-wf-cli-backend-option]");
                if (!btn || !backendSel) return;
                backendSel.value = btn.getAttribute("data-wf-cli-backend-option") || "pi";
                backendSel.dispatchEvent(new Event("change", { bubbles: true }));
            });
        }
        var keyToggle = document.getElementById("wf-cli-key-toggle");
        if (keyToggle && keyToggle.dataset.bound !== "1") {
            keyToggle.dataset.bound = "1";
            keyToggle.addEventListener("click", function () {
                openWorkflowCliKeyModal();
            });
        }
        var keySave = document.getElementById("wf-cli-key-save");
        if (keySave && keySave.dataset.bound !== "1") {
            keySave.dataset.bound = "1";
            keySave.addEventListener("click", saveWorkflowCliKeyAndReconnect);
        }
        var keyClose = document.getElementById("wf-cli-key-close");
        if (keyClose && keyClose.dataset.bound !== "1") {
            keyClose.dataset.bound = "1";
            keyClose.addEventListener("click", closeWorkflowCliKeyModal);
        }
        var keyCancel = document.getElementById("wf-cli-key-cancel");
        if (keyCancel && keyCancel.dataset.bound !== "1") {
            keyCancel.dataset.bound = "1";
            keyCancel.addEventListener("click", closeWorkflowCliKeyModal);
        }
        var keyModal = document.getElementById("wf-cli-key-modal");
        if (keyModal && keyModal.dataset.bound !== "1") {
            keyModal.dataset.bound = "1";
            keyModal.addEventListener("click", function (evt) {
                if (evt.target === keyModal) closeWorkflowCliKeyModal();
            });
        }
        var modelList = document.getElementById("wf-cli-model-list");
        if (modelList && modelList.dataset.bound !== "1") {
            modelList.dataset.bound = "1";
            modelList.addEventListener("click", function (evt) {
                var addBtn = evt.target.closest("[data-wf-cli-model-add]");
                if (addBtn) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    var addId = String(addBtn.getAttribute("data-wf-cli-model-add") || "");
                    var model = _wfCliInventoryById[addId];
                    if (model) workflowCliAddChosenModel(model);
                    return;
                }
                var item = evt.target.closest(".wf-cli-model-row");
                if (!item || item.classList.contains("is-disabled")) return;
                var modelId = item.dataset.modelId || "";
                if (!modelId) return;
                workflowCliApplyModelSelection(modelId);
            });
            modelList.addEventListener("keydown", function (evt) {
                if (evt.key !== "Enter" && evt.key !== " ") return;
                var item = evt.target.closest(".wf-cli-model-row");
                if (!item || item.classList.contains("is-disabled")) return;
                evt.preventDefault();
                item.click();
            });
            modelList.addEventListener("dblclick", function (evt) {
                var item = evt.target.closest(".wf-cli-model-row");
                if (!item || item.classList.contains("is-disabled")) return;
                evt.preventDefault();
                workflowCliApplyModelSelection(String(item.dataset.modelId || ""));
            });
            modelList.addEventListener("contextmenu", function (evt) {
                var item = evt.target.closest(".wf-cli-model-row");
                if (!item || item.classList.contains("is-disabled")) return;
                openWorkflowCliModelContextMenu(evt, String(item.dataset.modelId || ""));
            });
            modelList.addEventListener("dragstart", function (evt) {
                var item = evt.target.closest(".wf-cli-model-row");
                if (!item || item.classList.contains("is-disabled")) return;
                _wfCliDraggedModelId = String(item.dataset.modelId || "");
                _wfCliDraggedChosenIndex = -1;
                item.classList.add("is-dragging");
                if (evt.dataTransfer) {
                    evt.dataTransfer.effectAllowed = "copyMove";
                    evt.dataTransfer.setData("text/plain", _wfCliDraggedModelId);
                    evt.dataTransfer.setData("application/x-wf-cli-model", _wfCliDraggedModelId);
                }
            });
            modelList.addEventListener("dragend", function (evt) {
                var item = evt.target.closest(".wf-cli-model-row");
                if (item) item.classList.remove("is-dragging");
                _wfCliDraggedModelId = "";
            });
        }
        var chosenList = document.getElementById("wf-cli-chosen-list");
        if (chosenList && chosenList.dataset.bound !== "1") {
            chosenList.dataset.bound = "1";
            chosenList.addEventListener("click", function (evt) {
                var removeBtn = evt.target.closest("[data-wf-cli-chosen-remove]");
                if (!removeBtn) return;
                workflowCliRemoveChosenModel(removeBtn.getAttribute("data-wf-cli-chosen-remove"));
            });
            chosenList.addEventListener("dragover", function (evt) {
                var dropzone = evt.target.closest("#wf-cli-chosen-dropzone");
                if (_wfCliDraggedModelId) {
                    evt.preventDefault();
                    if (evt.dataTransfer) evt.dataTransfer.dropEffect = "copy";
                }
                chosenList.querySelectorAll(".is-over").forEach(function (el) { el.classList.remove("is-over"); });
                if (dropzone) dropzone.classList.add("is-over");
            });
            chosenList.addEventListener("dragleave", function (evt) {
                var dropzone = evt.target.closest("#wf-cli-chosen-dropzone");
                if (dropzone) dropzone.classList.remove("is-over");
            });
            chosenList.addEventListener("drop", function (evt) {
                if (!_wfCliDraggedModelId) return;
                evt.preventDefault();
                chosenList.querySelectorAll(".is-over").forEach(function (el) { el.classList.remove("is-over"); });
                var model = _wfCliInventoryById[_wfCliDraggedModelId];
                if (model) workflowCliAddChosenModel(model);
                _wfCliDraggedModelId = "";
            });
            chosenList.addEventListener("dragend", function () {
                chosenList.querySelectorAll(".is-over").forEach(function (el) { el.classList.remove("is-over"); });
                _wfCliDraggedModelId = "";
            });
        }
        var sendBtn = document.getElementById("wf-cli-send");
        var input = document.getElementById("wf-cli-input");
        if (sendBtn && sendBtn.dataset.bound !== "1") {
            sendBtn.dataset.bound = "1";
            sendBtn.addEventListener("click", sendWorkflowCliPrompt);
        }
        if (input && input.dataset.bound !== "1") {
            input.dataset.bound = "1";
            input.addEventListener("keydown", function (evt) {
                if (evt.key === "Enter") {
                    evt.preventDefault();
                    sendWorkflowCliPrompt();
                }
            });
        }
        var modelSel = document.getElementById("wf-cli-model-select");
        if (modelSel && modelSel.dataset.bound !== "1") {
            modelSel.dataset.bound = "1";
            modelSel.addEventListener("change", function () {
                syncWorkflowCliCapabilityControls(_wfCliInventoryById[modelSel.value || ""] || null);
                refreshWorkflowCliModelCardSelection();
                refreshWorkflowCliKeyPanel();
                var projectId = workflowBoardProjectId();
                if (workflowCliActiveBackend() === "pi") {
                    if (modelSel.value && modelSel.value !== "auto") {
                        workflowCliRememberLockedModel(projectId, {
                            provider: selectedCliModelProvider(modelSel) || "ollama",
                            model: modelSel.value
                        });
                    } else {
                        workflowCliForgetLockedModel(projectId);
                    }
                }
                persistWorkflowCliBoardState({ model: modelSel.value || "" });
                if (!_wfCliSessionReady) return;
                if (!projectId) return;
                syncWorkflowCliProjectModel(
                    projectId,
                    workflowCliActiveBackend(),
                    modelSel.value || "auto"
                ).catch(function () {});
                return;
            });
        }
        ["wf-cli-capability-level", "wf-cli-capability-speed", "wf-cli-capability-variant"].forEach(function (id) {
            var field = document.getElementById(id);
            if (!field || field.dataset.bound === "1") return;
            field.dataset.bound = "1";
            field.addEventListener("change", function () {
                var capabilities = workflowCliSelectedCapabilities();
                persistWorkflowCliBoardState({
                    codex_reasoning_effort: capabilities.codex_reasoning_effort,
                    codex_service_tier: capabilities.codex_service_tier
                });
            });
        });
        if (!document.documentElement.dataset.wfCliGlobalBound) {
            document.documentElement.dataset.wfCliGlobalBound = "1";
            document.addEventListener("click", function (evt) {
                var liveMenu = document.getElementById("wf-cli-backend-menu");
                var liveTrigger = document.getElementById("wf-cli-backend-trigger");
                if (liveMenu && liveMenu.classList.contains("is-open")) {
                    if (!(liveMenu.contains(evt.target) || (liveTrigger && liveTrigger.contains(evt.target)))) {
                        setWorkflowCliBackendMenuOpen(false);
                    }
                }
                var menu = document.getElementById("wf-cli-model-context-menu");
                if (menu && menu.classList.contains("is-open") && !menu.contains(evt.target)) {
                    closeWorkflowCliModelContextMenu();
                }
            });
            document.addEventListener("keydown", function (evt) {
                if (evt.key === "Escape") {
                    setWorkflowCliBackendMenuOpen(false);
                    closeWorkflowCliModelContextMenu();
                    closeWorkflowCliKeyModal();
                }
            });
        }
        var contextMenu = document.getElementById("wf-cli-model-context-menu");
        if (contextMenu && contextMenu.dataset.bound !== "1") {
            contextMenu.dataset.bound = "1";
            contextMenu.addEventListener("click", function (evt) {
                var action = evt.target.closest("[data-wf-cli-model-action]");
                if (!action) return;
                var model = _wfCliInventoryById[_wfCliModelContextTargetId] || null;
                if (!model) return;
                if (action.getAttribute("data-wf-cli-model-action") === "add") {
                    workflowCliAddChosenModel(model);
                } else {
                    workflowCliApplyModelSelection(_wfCliModelContextTargetId);
                }
                closeWorkflowCliModelContextMenu();
            });
        }
    }

    function ensureGlobalConfigExecutionRoutesShell() {
        var modal = document.getElementById("sr-llm-modal");
        if (!modal) return;
        var routesRoot = document.getElementById("wf-global-exec-routes");
        if (routesRoot && !routesRoot.dataset.initialized) {
            routesRoot.innerHTML = workflowExecRouteHtml("wf-global-exec");
            routesRoot.dataset.initialized = "1";
            bindWorkflowExecRoutingControls(modal);
        }
    }

    function refreshWorkflowGlobalExecutionPanel() {
        var modal = document.getElementById("sr-llm-modal");
        if (!modal) return Promise.resolve();
        ensureGlobalConfigExecutionRoutesShell();
        var projectId = workflowBoardProjectId();
        return Promise.all([
            api("GET", "/workflows/orchestrator-setup").catch(function () { return {}; }),
            api("GET", "/llms").catch(function () { return {}; }),
            fetchWorkflowProjectCliBackends(projectId)
        ]).then(function (results) {
            var setup = results[0] || {};
            var projectBackends = results[2] || {};
            var backendRows = Array.isArray(projectBackends.backends) ? projectBackends.backends : [];
            if (!backendRows.length && setup.backends && Array.isArray(setup.backends.backends)) {
                backendRows = setup.backends.backends;
            }
            updateWorkflowExecBackendSelects(modal, backendRows);
            return populateWorkflowExecRouting(setup, results[1] || {}, modal).then(function () {
                clearWorkflowExecutorInstallCallout();
                _wfExecutorInstallContext.optionalBackends = setup.optional_backends || {};
                return refreshWorkflowConfigExecutorPills(modal);
            });
        }).catch(function () {
            populateWorkflowExecRouting({}, {}, modal);
            clearWorkflowExecutorInstallCallout();
            return refreshWorkflowConfigExecutorPills(modal);
        });
    }

    window.ensureGlobalConfigExecutionRoutesShell = ensureGlobalConfigExecutionRoutesShell;

    function saveWorkflowGlobalExecutionRouting() {
        var modal = document.getElementById("sr-llm-modal");
        if (!modal) return Promise.resolve();
        return saveWorkflowExecRouting(modal);
    }

    window.refreshWorkflowGlobalExecutionPanel = refreshWorkflowGlobalExecutionPanel;
    window.saveWorkflowGlobalExecutionRouting = saveWorkflowGlobalExecutionRouting;

    function syncWorkflowConfigTabBodies() {
        var needsWorkflow = !currentWorkflowId;
        document.querySelectorAll(".wf-config-needs-workflow-hint").forEach(function (el) {
            el.classList.toggle("hidden", !needsWorkflow);
        });
        document.querySelectorAll(".wf-config-tab-body").forEach(function (el) {
            el.classList.toggle("hidden", needsWorkflow);
        });
    }

    function refreshWorkflowCliTabIfVisible() {
        var cliTab = document.getElementById("wf-tab-cli");
        if (cliTab && !cliTab.classList.contains("hidden")) refreshWorkflowCliTab();
    }

    function syncWorkflowCliAreaPresence(options) {
        var shouldBePresent = workflowCliAreaShouldPing();
        return setWorkflowCliAreaPresence(shouldBePresent, options || {});
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
            '<div id="wf-board-edit-modal" role="dialog" aria-modal="true">' +
                '<div class="w-full max-w-3xl mx-4 bg-[#1a1f3a] border border-white/20 rounded-xl shadow-2xl overflow-hidden max-h-[90vh] flex flex-col">' +
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
                    '<div class="overflow-y-auto min-h-0 flex-1 p-5 space-y-3">' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Board name</label>' +
                            '<input type="text" id="wf-board-edit-name" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(opt.name) + '">' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Description</label>' +
                            '<input type="text" id="wf-board-edit-desc" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none" value="' + esc(opt.description || "") + '" placeholder="Optional description">' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-xs text-gray-500 mb-1">Project</label>' +
                            '<select id="wf-board-edit-project" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm focus:border-[#f97316] focus:outline-none">' +
                                optionHtml(workflowLinkable.projects || [], opt.default_project_id, "name") +
                            '</select>' +
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
                    '<div class="flex items-center justify-center gap-3 px-5 py-4 border-t border-white/10 flex-shrink-0">' +
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
        var saveBtn = document.getElementById("wf-board-edit-save");
        if (saveBtn) {
            saveBtn.addEventListener("click", function () {
                var name = (document.getElementById("wf-board-edit-name").value || "").trim() || opt.name;
                var description = (document.getElementById("wf-board-edit-desc").value || "").trim();
                var projectId = document.getElementById("wf-board-edit-project").value || null;
                var workflowEl = document.getElementById("wf-board-edit-workflow");
                var workflowId = workflowEl
                    ? (workflowEl.value || null)
                    : (opt.default_workflow_id != null ? String(opt.default_workflow_id) : null);
                var nextColor = document.getElementById("wf-board-edit-color").value || "";
                var payload = {
                    name: name,
                    description: description,
                    default_project_id: projectId ? parseInt(projectId, 10) : null,
                    default_workflow_id: workflowId ? parseInt(workflowId, 10) : null,
                    color: nextColor,
                };
                saveBtn.disabled = true;
                var boardRequest = opt.source === "database"
                    ? api("PUT", "/tickets/boards/" + encodeURIComponent(opt.id), payload)
                    : api("POST", "/tickets/external-boards/" + encodeURIComponent(opt.source) + "/" + encodeURIComponent(opt.id) + "/register", payload);
                boardRequest
                    .then(function () {
                        closeModal();
                        snack("Board saved");
                        loadWorkflowBoards();
                    })
                    .catch(function (e) {
                        saveBtn.disabled = false;
                        snack(e.message || "Failed to save board settings", "error");
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
                            selectedWorkflowQueueTicketId = null;
                            workflowQueueTickets = [];
                            document.getElementById("wf-detail").classList.add("hidden");
                            document.getElementById("wf-empty").classList.remove("hidden");
                        }
                        var boardSelect = document.getElementById("wf-board-select");
                        if (boardSelect && boardSelect.value) loadWorkflowBoardTickets(boardSelect.value);
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
        var opts = {
            method: method,
            headers: { "Content-Type": "application/json" },
            cache: method === "GET" ? "no-store" : "default"
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(API + path, opts).then(function (r) {
            return r.text().then(function (text) {
                var data = null;
                if (text) {
                    try {
                        data = JSON.parse(text);
                    } catch (e) {
                        data = null;
                    }
                }
                if (!r.ok) {
                    var detail = data && data.detail;
                    if (typeof detail === "string" && detail.trim()) {
                        var err = new Error(detail);
                        err.workflowDetail = data;
                        throw err;
                    }
                    if (Array.isArray(detail) && detail.length) {
                        var first = detail[0] || {};
                        var loc = Array.isArray(first.loc) ? first.loc.join(".") : "";
                        var msg = first.msg || "Request failed";
                        var validationErr = new Error(loc ? (loc + ": " + msg) : msg);
                        validationErr.workflowDetail = data;
                        throw validationErr;
                    }
                    if (detail && typeof detail === "object") {
                        var objectErr = new Error(JSON.stringify(detail));
                        objectErr.workflowDetail = data;
                        throw objectErr;
                    }
                    if (data && typeof data.error === "string" && data.error.trim()) {
                        var apiErr = new Error(data.error);
                        apiErr.workflowDetail = data;
                        throw apiErr;
                    }
                    if (data && typeof data.message === "string" && data.message.trim()) {
                        var messageErr = new Error(data.message);
                        messageErr.workflowDetail = data;
                        throw messageErr;
                    }
                    var plain = (text || "").trim();
                    var genericErr = new Error(plain || ("Request failed (" + r.status + ")"));
                    genericErr.workflowDetail = data;
                    throw genericErr;
                }
                return data !== null && data !== undefined ? data : {};
            });
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
                '<button type="button" data-action="configure" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Configure</button>' +
                '<button type="button" data-action="duplicate" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10">Duplicate</button>' +
                '<div class="my-1 border-t border-white/10"></div>' +
                '<button type="button" data-action="delete" class="wf-cm-action w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-red-500/20">Delete</button>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        wfContextMenuEl = document.getElementById("wf-context-menu");
        wfContextMenuEl.querySelectorAll(".wf-cm-action").forEach(function (btn) {
            btn.addEventListener("click", function (evt) {
                evt.stopPropagation();
                var action = btn.dataset.action;
                var workflowId = wfContextMenuId;
                closeWorkflowContextMenu();
                if (!workflowId) return;
                if (action === "configure") {
                    if (currentWorkflowId !== workflowId) selectWorkflow(workflowId);
                    if (typeof window.openWorkflowExecutionSetup === "function") {
                        window.openWorkflowExecutionSetup();
                    } else {
                        var setupBtn = document.getElementById("wf-sr-llm-btn");
                        if (setupBtn) setupBtn.click();
                    }
                    return;
                }
                if (action === "duplicate") {
                    api("POST", "/workflows/" + workflowId + "/duplicate")
                        .then(function (data) { snack("Workflow duplicated"); selectWorkflow(data.id); })
                        .catch(function () { snack("Failed to duplicate", "error"); });
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
    function currentWorkflowTabOrder() {
        var el = document.getElementById("wf-list");
        if (!el) return [];
        return Array.prototype.slice.call(el.querySelectorAll(".wf-workflow-tab[data-id]"))
            .map(function (tab) { return parseInt(tab.dataset.id, 10); })
            .filter(function (id) { return Number.isFinite(id); });
    }

    function persistWorkflowTabOrder() {
        var ids = currentWorkflowTabOrder();
        if (!ids.length) return;
        api("PATCH", "/workflows/order", { workflow_ids: ids })
            .then(function () { snack("Workflow order saved"); loadList(); })
            .catch(function (e) { snack(e.message || "Failed to save workflow order", "error"); loadList(); });
    }

    function bindWorkflowTabDrag(row) {
        row.draggable = true;
        row.addEventListener("dragstart", function (evt) {
            workflowTabDragId = parseInt(row.dataset.id, 10);
            row.classList.add("opacity-60", "wf-workflow-tab-dragging");
            if (evt.dataTransfer) {
                evt.dataTransfer.effectAllowed = "move";
                evt.dataTransfer.setData("text/plain", String(workflowTabDragId));
                evt.dataTransfer.setData("application/x-workflow-tab", String(workflowTabDragId));
            }
        });
        row.addEventListener("dragover", function (evt) {
            if (!workflowTabDragId || workflowTabDragId === parseInt(row.dataset.id, 10)) return;
            evt.preventDefault();
            row.classList.add("wf-workflow-tab-drop-target");
            if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
        });
        row.addEventListener("dragleave", function () {
            row.classList.remove("wf-workflow-tab-drop-target");
        });
        row.addEventListener("drop", function (evt) {
            evt.preventDefault();
            row.classList.remove("wf-workflow-tab-drop-target");
            var list = document.getElementById("wf-list");
            var dragged = list ? list.querySelector('.wf-workflow-tab[data-id="' + workflowTabDragId + '"]') : null;
            if (!list || !dragged || dragged === row) return;
            var rect = row.getBoundingClientRect();
            var after = evt.clientX > rect.left + rect.width / 2;
            list.insertBefore(dragged, after ? row.nextSibling : row);
            persistWorkflowTabOrder();
        });
        row.addEventListener("dragend", function () {
            row.classList.remove("opacity-60", "wf-workflow-tab-dragging");
            var list = document.getElementById("wf-list");
            if (list) {
                list.querySelectorAll(".wf-workflow-tab").forEach(function (tab) {
                    tab.classList.remove("wf-workflow-tab-drop-target");
                });
            }
            workflowTabDragId = null;
        });
    }

    function loadList() {
        return api("GET", "/workflows?limit=50")
            .then(function (data) {
                var el = document.getElementById("wf-list");
                if (!data.length) {
                    el.innerHTML = "";
                    return;
                }
                el.innerHTML = data.map(function (w) {
                    var isActive = currentWorkflowId === w.id;
                    var title = w.name + (isActive ? " (double-click to rename)" : "");
                    return '<button type="button" class="wf-workflow-tab px-4 py-2 text-sm text-gray-400' + (isActive ? " active" : "") + '" data-id="' + w.id + '" role="tab" aria-selected="' + (isActive ? "true" : "false") + '" tabindex="' + (isActive ? "0" : "-1") + '" title="' + esc(title) + '">' + workflowTabTitleHtml(w.name) + "</button>";
                }).join("");
                el.querySelectorAll("[data-id]").forEach(function (row) {
                    bindWorkflowTabDrag(row);
                    row.addEventListener("click", function () { selectWorkflow(parseInt(row.dataset.id, 10)); });
                    row.addEventListener("dblclick", function (evt) {
                        evt.preventDefault();
                        evt.stopPropagation();
                        if (currentWorkflowId === parseInt(row.dataset.id, 10)) beginWorkflowTabRename(row);
                    });
                    row.addEventListener("contextmenu", function (evt) {
                        openWorkflowContextMenu(evt, parseInt(row.dataset.id, 10));
                    });
                });
                var activeTab = currentWorkflowId != null ? el.querySelector('[data-id="' + currentWorkflowId + '"]') : null;
                if (activeTab && typeof activeTab.scrollIntoView === "function") {
                    activeTab.scrollIntoView({ block: "nearest", inline: "nearest" });
                }
                scheduleWorkflowTabMarquees();

                if (data.length && !currentWorkflow) {
                    var pickId = currentWorkflowId;
                    if (!pickId || !data.some(function (w) { return w.id === pickId; })) {
                        var lastId = null;
                        try { lastId = parseInt(localStorage.getItem("wf_last_selected"), 10); } catch (e) {}
                        pickId = (lastId && data.some(function (w) { return w.id === lastId; })) ? lastId : data[0].id;
                    }
                    if (currentWorkflowId !== pickId) {
                        selectWorkflow(pickId);
                    } else {
                        loadDetail(pickId);
                    }
                }
            }).catch(function (e) {
                console.error("Load workflows failed", e);
            });
    }

    function readPersistedWorkflowDetailTab() {
        try {
            var tab = localStorage.getItem("wf_detail_tab_v2") || "loop";
            if (tab === "activity") tab = "loop";
            return ["loop", "tickets", "cli", "runs"].indexOf(tab) >= 0 ? tab : "loop";
        } catch (e) {
            return "loop";
        }
    }

    function persistWorkflowDetailTab(tab) {
        if (!tab) return;
        try { localStorage.setItem("wf_detail_tab_v2", tab); } catch (e) {}
    }

    function restoreWorkflowDetailTab() {
        var tab = readPersistedWorkflowDetailTab();
        if (tab === "runs") {
            var runsTabBtn = document.getElementById("wf-runs-tab-btn");
            if (!runsTabBtn || runsTabBtn.classList.contains("hidden")) tab = "loop";
        }
        switchTab(tab, { persist: false });
    }

    function selectWorkflow(id) {
        currentWorkflowId = id;
        expandedStepId = null;
        selectedWorkflowQueueTicketId = null;
        workflowRunsFilterTicketId = null;
        loopFeedRunId = null;
        workflowBoardSelectionExplicit = false;
        try { localStorage.setItem("wf_last_selected", id); } catch (e) {}
        loadList();
        loadDetail(id);
        var boardSelect = document.getElementById("wf-board-select");
        if (boardSelect && boardSelect.value) loadWorkflowBoardTickets(boardSelect.value);
    }

    function getActiveWorkflowTab() {
        if (currentWorkflowId == null) return null;
        var list = document.getElementById("wf-list");
        return list ? list.querySelector('.wf-workflow-tab[data-id="' + currentWorkflowId + '"]') : null;
    }

    function hasWorkflowQueueTarget() {
        var detail = document.getElementById("wf-detail");
        if (!currentWorkflowId || !currentWorkflow || !detail || detail.classList.contains("hidden")) return false;
        var tab = getActiveWorkflowTab();
        return !!(tab && tab.classList.contains("active") && tab.getAttribute("aria-selected") === "true");
    }

    function getWorkflowRenameDraftName() {
        var tab = getActiveWorkflowTab();
        var input = tab ? tab.querySelector(".wf-workflow-tab-rename") : null;
        if (input) return (input.value || "").trim();
        return currentWorkflow && currentWorkflow.name ? String(currentWorkflow.name).trim() : "";
    }

    function finishWorkflowTabRename(nextName) {
        var tab = getActiveWorkflowTab();
        if (!tab) return;
        var name = nextName || "Untitled Workflow";
        tab.innerHTML = workflowTabTitleHtml(name);
        tab.title = name + " (double-click to rename)";
        scheduleWorkflowTabMarquees();
    }

    function cancelWorkflowTabRename() {
        finishWorkflowTabRename(currentWorkflow && currentWorkflow.name ? currentWorkflow.name : "Untitled Workflow");
    }

    function beginWorkflowTabRename(tab) {
        if (!tab || tab.querySelector(".wf-workflow-tab-rename")) return;
        var titleEl = tab.querySelector(".wf-workflow-tab-title");
        var currentName = currentWorkflow && currentWorkflow.name ? currentWorkflow.name : "";
        if (!currentName) currentName = titleEl ? titleEl.textContent.trim() : (tab.textContent || "").trim();
        var input = document.createElement("input");
        input.type = "text";
        input.className = "wf-workflow-tab-rename";
        input.value = currentName;
        input.setAttribute("aria-label", "Workflow name");
        tab.textContent = "";
        tab.appendChild(input);
        input.focus();
        input.select();
        input.addEventListener("click", function (evt) { evt.stopPropagation(); });
        input.addEventListener("keydown", function (evt) {
            if (evt.key === "Enter") {
                evt.preventDefault();
                saveWorkflowName();
            }
            if (evt.key === "Escape") {
                evt.preventDefault();
                cancelWorkflowTabRename();
            }
        });
    }

    function saveWorkflowName() {
        if (!currentWorkflowId) return;
        if (document.getElementById("wf-loop-step-modal") && !document.getElementById("wf-loop-step-modal").classList.contains("hidden")) {
            return;
        }
        var statusEl = document.getElementById("wf-name-save-status");
        var nextName = getWorkflowRenameDraftName() || "Untitled Workflow";
        var currentName = currentWorkflow && currentWorkflow.name ? String(currentWorkflow.name).trim() : "";
        if (nextName === currentName) {
            finishWorkflowTabRename(nextName);
            return;
        }
        if (statusEl) statusEl.textContent = "Saving...";
        api("PATCH", "/workflows/" + currentWorkflowId, { name: nextName })
            .then(function () {
                if (currentWorkflow) currentWorkflow.name = nextName;
                finishWorkflowTabRename(nextName);
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

    function finishDetailTabRestore() {
        if (!shouldRestoreDetailTabOnce) return;
        shouldRestoreDetailTabOnce = false;
        restoreWorkflowDetailTab();
    }

    function workflowDetailTargetTab(restoreTabAfterLoad) {
        var tab = restoreTabAfterLoad ? readPersistedWorkflowDetailTab() : workflowActiveDetailTab();
        if (tab === "activity") tab = "tickets";
        if (["tickets", "loop", "cli", "runs"].indexOf(tab) < 0) tab = "tickets";
        return tab;
    }

    function loadDetail(id) {
        var restoreTabAfterLoad = shouldRestoreDetailTabOnce;
        api("GET", "/workflows/" + id).then(function (data) {
            var targetTab = workflowDetailTargetTab(restoreTabAfterLoad);
            var eagerRunsData = targetTab === "runs";
            var eagerCliData = targetTab === "cli";
            currentWorkflow = data;
            document.getElementById("wf-empty").classList.add("hidden");
            document.getElementById("wf-detail").classList.remove("hidden");
            renderSteps(data.steps || []);
            renderRuns(data.runs || []);
            renderRunSettings(data);
            loadWorkflowTicketQueue();
            var activeRunsPromise = eagerRunsData ? loadActiveRuns() : Promise.resolve();
            if (eagerRunsData || eagerCliData) loadWorkflowExecutionSessions({ quiet: !eagerRunsData });
            if (eagerRunsData) {
                loadWorkflowRunHistory({ quiet: true });
                loadOrchestratorTimeline({ quiet: true });
                checkActiveRun();
            } else {
                requestAnimationFrame(function () {
                    loadActiveRuns();
                    checkActiveRun();
                });
            }
            syncWorkflowRunsTabVisibility();
            scheduleWorkflowTabMarquees();
            syncWorkflowHarnessHandoffButton();
            workflowWorkspaceMemoryLoadedFor = null;
            if (targetTab === "loop" || targetTab === "cli") {
                requestAnimationFrame(loadWorkflowWorkspaceMemory);
            }
            syncWorkflowCliAreaPresence({ reason: "load-detail", eager: eagerCliData });
            if (restoreTabAfterLoad) {
                if (activeRunsPromise && typeof activeRunsPromise.then === "function") {
                    activeRunsPromise.finally(finishDetailTabRestore);
                } else {
                    finishDetailTabRestore();
                }
            }
        }).catch(function () { snack("Failed to load workflow", "error"); });
    }

    function loadWorkflowWorkspaceMemory() {
        if (!currentWorkflowId || String(workflowWorkspaceMemoryLoadedFor) === String(currentWorkflowId)) {
            return Promise.resolve();
        }
        var requestedId = currentWorkflowId;
        return api("GET", "/workflows/" + requestedId + "/workspace-memory").then(function (mem) {
            if (String(currentWorkflowId) !== String(requestedId)) return;
            workflowWorkspaceMemoryLoadedFor = requestedId;
            var el = document.getElementById("wf-config-agent-map");
            if (!el) return;
            var paths = (mem && mem.workspace && mem.workspace.companion_paths) || {};
            el.value = paths.workflow ? (paths.workflow + "/agents.md") : "";
        }).catch(function () {});
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

    function renderWorkflowBoardSelect(options, preferredValue, renderOptions) {
        var select = document.getElementById("wf-board-select");
        if (!select) return;
        renderOptions = renderOptions || {};
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
        var currentValue = select.value || "";
        select.innerHTML = html;
        var saved = "";
        try { saved = localStorage.getItem("wf_board_selected") || ""; } catch (e) {}
        var selected = "";
        if (preferredValue && workflowBoardOptions.some(function (opt) { return opt.value === preferredValue; })) {
            selected = preferredValue;
        } else if (currentValue && workflowBoardOptions.some(function (opt) { return opt.value === currentValue; })) {
            selected = currentValue;
        } else if (workflowBoardOptions.some(function (opt) { return opt.value === saved; })) {
            selected = saved;
        } else {
            selected = workflowBoardOptions[0].value;
        }
        select.value = selected;
        if (
            renderOptions.skipReloadIfUnchanged &&
            currentValue &&
            currentValue === selected &&
            workflowBoardRenderState &&
            workflowBoardRenderState.selected &&
            workflowBoardRenderState.selected.value === selected
        ) {
            return;
        }
        loadWorkflowBoardTickets(selected);
    }

    function buildWorkflowBoardOptions(localBoards, external) {
        var options = [];
        var seen = {};
        localBoards = Array.isArray(localBoards) ? localBoards : [];
        external = external || {};

        workflowDatabaseBoards = localBoards.filter(function (b) { return (b.source || "database") === "database"; });
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
        return options;
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
        return api("GET", "/tickets/boards/" + encodeURIComponent(localBoardId) + "/whatsapp-links")
            .then(function (links) {
                var merged = data || {};
                merged.whatsapp_links = Array.isArray(links) ? links : [];
                return merged;
            })
            .catch(function () { return data || {}; });
    }

    function refreshWorkflowExternalBoards(localBoards, preferredValue, attempt) {
        attempt = attempt || 0;
        return api("GET", "/tickets/external-boards").catch(function () {
            return { trello: [], jira: [], cache_ready: true, cache_stale: false };
        }).then(function (external) {
            external = external || {};
            renderWorkflowBoardSelect(buildWorkflowBoardOptions(localBoards, external), preferredValue, {
                skipReloadIfUnchanged: true
            });
            if (external.cache_ready === false && attempt < 30) {
                setTimeout(function () {
                    refreshWorkflowExternalBoards(localBoards, preferredValue, attempt + 1);
                }, 500);
            }
            return external;
        });
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
        return api("POST", "/tickets/whatsapp/compose-ticket", {
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
        return api("POST", "/tickets/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-preview", {
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
        api("POST", "/tickets/whatsapp/sync", {}).then(function (syncResult) {
            if (syncResult && syncResult.error) {
                syncNote = " Sync skipped: " + syncResult.error + ". Using locally stored messages.";
            } else if (syncResult && typeof syncResult.synced === "number") {
                syncNote = " Synced " + syncResult.synced + " message(s) from relay.";
            }
        }).catch(function () {
            syncNote = " Sync unavailable; using locally stored messages.";
        }).finally(function () {
            setWorkflowWhatsappTicketLoading(true, "Finding new WhatsApp messages for this board." + syncNote, "Collecting messages", 12);
            return api("POST", "/tickets/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-preview", {
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
        api("POST", "/tickets/boards/" + encodeURIComponent(workflowWhatsappTicketDraft.board_id) + "/whatsapp-snapshot-ticket", {
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
            refreshWorkflowCliTabIfVisible();
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
        var preferredValue = "";
        if (select) {
            preferredValue = select.value || "";
            if (!preferredValue) {
                try { preferredValue = localStorage.getItem("wf_board_selected") || ""; } catch (e) {}
            }
            select.innerHTML = '<option value="">Loading boards...</option>';
        }

        api("GET", "/tickets/boards").then(function (localBoards) {
            localBoards = Array.isArray(localBoards) ? localBoards : [];
            renderWorkflowBoardSelect(buildWorkflowBoardOptions(localBoards, null), preferredValue);
            refreshWorkflowExternalBoards(localBoards, preferredValue);
        }).catch(function () {
            if (select) select.innerHTML = '<option value="">Failed to load boards</option>';
            renderWorkflowBoardTickets(null, null, "Failed to load boards.");
        });

        api("GET", "/tickets/linkable").then(function (data) {
            workflowLinkable = data || { projects: [], workflows: [] };
        }).catch(function () {
            workflowLinkable = { projects: [], workflows: [] };
        });
    }

    function loadWorkflowBoardTickets(value, attempt) {
        var selected = workflowBoardOptions.filter(function (opt) { return opt.value === value; })[0];
        var list = document.getElementById("wf-board-ticket-list");
        if (!selected || !list) return;
        attempt = attempt || 0;
        if (attempt === 0) {
            workflowBoardLoadToken += 1;
            try { localStorage.setItem("wf_board_selected", value); } catch (e) {}
            renderWorkflowBoardSpinner("Loading tickets...");
        }
        var token = workflowBoardLoadToken;

        var path = selected.source === "database"
            ? "/tickets/boards/" + encodeURIComponent(selected.id) + "/workflow-view"
            : "/tickets/external-boards/" + encodeURIComponent(selected.source) + "/" + encodeURIComponent(selected.id);

        api("GET", path).then(function (data) {
            if (token !== workflowBoardLoadToken) return;
            var hasLaneData = !!(data && Array.isArray(data.lanes) && data.lanes.length);
            if (selected.source !== "database" && data && data.cache_ready === false && !hasLaneData && attempt < 60) {
                renderWorkflowBoardSpinner("Syncing board tickets...");
                setTimeout(function () {
                    if (token !== workflowBoardLoadToken) return;
                    loadWorkflowBoardTickets(value, attempt + 1);
                }, 800);
                return;
            }
            attachWorkflowBoardWhatsappLinks(data, selected).then(function (merged) {
                if (token !== workflowBoardLoadToken) return;
                renderWorkflowBoardTickets(merged, selected, "");
            });
        }).catch(function (e) {
            if (token !== workflowBoardLoadToken) return;
            renderWorkflowBoardTickets(null, selected, e.message || "Failed to load tickets.");
        });
    }

    function workflowBoardExecutionOrderCompare(a, b) {
        var rawQueueA = a && a.workflow_queue_position;
        var rawQueueB = b && b.workflow_queue_position;
        var queueA = rawQueueA !== null && rawQueueA !== undefined && Number.isFinite(Number(rawQueueA)) ? Number(rawQueueA) : null;
        var queueB = rawQueueB !== null && rawQueueB !== undefined && Number.isFinite(Number(rawQueueB)) ? Number(rawQueueB) : null;
        if (queueA !== null && queueB !== null && queueA !== queueB) return queueA - queueB;
        var posA = Number.isFinite(Number(a && a.position)) ? Number(a.position) : 0;
        var posB = Number.isFinite(Number(b && b.position)) ? Number(b.position) : 0;
        if (posA !== posB) return posA - posB;
        return Number(a && a.id || 0) - Number(b && b.id || 0);
    }

    function renderWorkflowBoardTickets(board, selected, message) {
        var list = document.getElementById("wf-board-ticket-list");
        if (!list) return;
        if (message && !board) {
            list.innerHTML = '<p class="text-sm text-gray-500">' + esc(message) + '</p>';
            workflowBoardRenderState = { data: null, selected: null };
            return;
        }
        var lanes = board && Array.isArray(board.lanes) ? board.lanes : [];
        if (!lanes.length) {
            list.innerHTML = '<p class="text-sm text-gray-500">' + esc(message || "No columns or tickets found for this board.") + '</p>';
            workflowBoardRenderState = { data: null, selected: selected || null };
            return;
        }

        workflowBoardRenderState = { data: board, selected: selected };
        workflowBoardTicketByKey = {};
        var ticketUi = ensureWorkflowTicketUi();
        var isLocal = !!(selected && selected.source === "database");
        var boardHasProject = !!(selected && selected.default_project_id);
        var boardData = board || {};
        var expandedLaneId = getWorkflowListExpandedLaneId(lanes, selected);

        list.innerHTML = "";
        list.classList.toggle("wf-board-ticket-list--blocked", !boardHasProject);
        if (message) {
            var msgEl = document.createElement("div");
            msgEl.className = "text-xs text-amber-300 mb-2";
            msgEl.textContent = message;
            list.appendChild(msgEl);
        }
        if (!boardHasProject) {
            var warnEl = document.createElement("div");
            warnEl.className = "mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200";
            warnEl.textContent = "This board is not linked to a project, so tickets cannot be added to a workflow. Click the gear icon next to the board dropdown to link it to a project.";
            list.appendChild(warnEl);
        }

        lanes.forEach(function (lane) {
            var tickets = Array.isArray(lane.tickets) ? lane.tickets : [];
            // Mission Control is an execution surface. Preserve the board's
            // explicit lane order here so the list agrees with the workflow
            // queue and auto-advance order. The ticket-board page can still
            // use its complexity-first triage sort.
            tickets = tickets.slice().sort(workflowBoardExecutionOrderCompare);
            var laneId = lane.id != null ? String(lane.id) : "";
            var isExpanded = expandedLaneId && laneId === String(expandedLaneId);
            var section = document.createElement("section");
            section.className = "kb-ticket-list-section" + (isExpanded ? " kb-ticket-list-section--expanded" : "");
            section.dataset.laneId = laneId;
            // Render the compact queue controls up front. Workflow loading and
            // board loading race on first paint; availability is synchronized
            // below once the selected workflow is known.
            var showAddAll = boardHasProject;
            var addAllHtml = showAddAll
                ? '<button type="button" class="wf-lane-add-all-board-tickets" data-lane-id="' + esc(laneId) + '" title="Add all tickets in this column to the workflow queue">Add all</button>'
                : "";
            section.innerHTML =
                '<div class="kb-ticket-list-section-head-row">' +
                    '<button type="button" class="kb-ticket-list-section-head flex-1 min-w-0" aria-expanded="' + (isExpanded ? "true" : "false") + '">' +
                        workflowListLaneChevronSvg() +
                        '<span class="kb-ticket-list-section-title">' + esc(lane.name || "Column") + "</span>" +
                        '<span class="kb-ticket-list-section-count">' + tickets.length + "</span>" +
                    "</button>" +
                    addAllHtml +
                "</div>";
            var body = document.createElement("div");
            body.className = "kb-ticket-list-section-body space-y-1";
            body.hidden = !isExpanded;
            if (!tickets.length) {
                body.innerHTML = '<div class="px-3 py-2 text-xs text-gray-500 italic border border-dashed border-white/10 rounded">No tickets</div>';
            } else if (ticketUi) {
                tickets.forEach(function (ticket) {
                    var row = ticketUi.createTicketListRow(ticket, isLocal, boardData, {
                        hideWorkflow: true,
                        hideTransfer: true,
                        hideCopy: true,
                        hideAgent: true,
                        disableListDrag: true,
                        showAddToWorkflow: boardHasProject,
                    });
                    bindWorkflowBoardListRow(row, ticket, lane, selected, board);
                    body.appendChild(row);
                });
            } else {
                body.innerHTML = '<div class="px-3 py-2 text-xs text-gray-500">Ticket list unavailable.</div>';
            }
            section.appendChild(body);
            list.appendChild(section);
        });

        bindWorkflowListLaneAccordion(list, lanes, selected);
        list.querySelectorAll(".wf-lane-add-all-board-tickets").forEach(function (btn) {
            var laneId = btn.dataset.laneId || "";
            btn.disabled = getAddableBoardTicketItems(laneId).length === 0;
        });
        refreshWorkflowBoardTicketDragBindings(list);
        initWorkflowListRowMarquees(list);
        refreshWorkflowBoardTicketsFromQueue();
    }

    function getSelectedBoardLocalId() {
        var select = document.getElementById("wf-board-select");
        if (!select || !select.value) return "";
        var selected = workflowBoardOptions.filter(function (opt) { return opt.value === select.value; })[0];
        return workflowBoardLocalId(selected);
    }

    // Soft refresh — only update step statuses/results without rebuilding DOM
    function softRefresh() {
        if (!currentWorkflowId) return Promise.resolve();
        return api("GET", "/workflows/" + currentWorkflowId).then(function (data) {
            currentWorkflow = data;
            var steps = data.steps || [];
            var preserveOpenEditor = isStepEditorInteractionActive();
            if (!preserveOpenEditor) {
                renderSteps(steps);
            }
            renderRuns(data.runs || []);
            // Update live card state so buttons/highlights follow active step.
            steps.forEach(function (s) {
                applyLiveStepCardState(s, steps);
                var card = document.querySelector('.step-card[data-step-id="' + s.id + '"]');
                if (!card) return;
                // Refresh history tab if it's currently visible
            });
            // loadActiveRuns owns the command-center, timeline, memory and
            // activity-feed refresh. Calling those again here doubled every
            // request during live execution and made event bursts freeze the UI.
            return loadActiveRuns();
        }).catch(function () {});
    }

    function scheduleWorkflowLiveRefresh() {
        workflowWsRefreshQueued = true;
        if (workflowWsRefreshTimer || workflowWsRefreshInFlight) return;
        workflowWsRefreshTimer = setTimeout(function flushWorkflowLiveRefresh() {
            workflowWsRefreshTimer = null;
            workflowWsRefreshQueued = false;
            workflowWsRefreshInFlight = true;

            var requests = [];
            if (currentWorkflowId) requests.push(softRefresh());
            else requests.push(loadList());

            // Workflow names/order change rarely. Keep the tabs fresh without
            // rebuilding them for every model token, tool call and heartbeat.
            if (Date.now() - workflowWsListRefreshedAt >= 5000) {
                workflowWsListRefreshedAt = Date.now();
                if (currentWorkflowId) requests.push(loadList());
            }

            Promise.allSettled(requests).finally(function () {
                workflowWsRefreshInFlight = false;
                if (workflowWsRefreshQueued) scheduleWorkflowLiveRefresh();
            });
        }, 250);
    }

    function formatElapsed(seconds) {
        var total = Math.max(0, parseInt(seconds, 10) || 0);
        var h = Math.floor(total / 3600);
        var m = Math.floor((total % 3600) / 60);
        var s = total % 60;
        return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    function parseJiraDurationToSeconds(text) {
        if (!text) return 0;
        var raw = String(text).trim().toLowerCase();
        if (!raw || raw === "-") return 0;
        var total = 0;
        var re = /(\d+)\s*([wdhms])/g;
        var match;
        while ((match = re.exec(raw))) {
            var n = parseInt(match[1], 10) || 0;
            if (match[2] === "w") total += n * 5 * 8 * 3600;
            else if (match[2] === "d") total += n * 8 * 3600;
            else if (match[2] === "h") total += n * 3600;
            else if (match[2] === "m") total += n * 60;
            else if (match[2] === "s") total += n;
        }
        return total;
    }

    function workflowQueueTicketBaseSeconds(ticket) {
        return parseJiraDurationToSeconds(ticket && ticket.time_spent);
    }

    function workflowQueueTicketRunSeconds(ticket) {
        if (!ticket || !ticket.id) return 0;
        var run = activeRunByTicketId(ticket.id);
        if (run) {
            var status = String(run.status || "").toLowerCase();
            if (status === "running" || status === "waiting") {
                delete workflowTicketPendingRunStartedAt[String(ticket.id)];
                return workflowRunDurationSeconds(run);
            }
        }
        var pendingStarted = workflowTicketPendingRunStartedAt[String(ticket.id)];
        if (pendingStarted) {
            return Math.max(0, Math.floor((Date.now() - pendingStarted) / 1000));
        }
        return 0;
    }

    function workflowQueueTicketDisplaySeconds(ticket) {
        return workflowQueueTicketBaseSeconds(ticket) + workflowQueueTicketRunSeconds(ticket);
    }

    function workflowQueueTicketTimeLabel(ticket) {
        return formatElapsed(workflowQueueTicketDisplaySeconds(ticket));
    }

    function workflowQueueTicketTimeIsLive(ticket) {
        if (!ticket || !ticket.id) return false;
        if (workflowTicketPendingRunStartedAt[String(ticket.id)]) return true;
        var run = activeRunByTicketId(ticket.id);
        if (!run) return false;
        var status = String(run.status || "").toLowerCase();
        return status === "running" || status === "waiting";
    }

    function tickWorkflowTicketTimers() {
        var hasLive = false;
        document.querySelectorAll(".wf-ticket-time-display").forEach(function (el) {
            var ticketId = el.dataset.ticketId;
            var ticket = workflowQueueTicketById(ticketId);
            if (!ticket) return;
            el.textContent = formatElapsed(workflowQueueTicketDisplaySeconds(ticket));
            if (workflowQueueTicketTimeIsLive(ticket)) hasLive = true;
        });
        renderWorkflowBoardTimeTotal();
        if (!hasLive && workflowTicketTimerInterval) {
            clearInterval(workflowTicketTimerInterval);
            workflowTicketTimerInterval = null;
        }
    }

    function ensureWorkflowTicketTimerTick() {
        tickWorkflowTicketTimers();
        if (workflowTicketTimerInterval) return;
        var needsTimer = (workflowQueueTickets || []).some(function (ticket) {
            return workflowQueueTicketTimeIsLive(ticket);
        });
        if (!needsTimer) return;
        workflowTicketTimerInterval = setInterval(tickWorkflowTicketTimers, 1000);
    }

    function workflowRunDurationSeconds(run) {
        if (!run) return 0;
        var status = String(run.status || "").toLowerCase();
        if ((status === "running" || status === "waiting") && run.elapsed_seconds != null) {
            return Math.max(0, parseInt(run.elapsed_seconds, 10) || 0);
        }
        if (run.started_at && run.completed_at) {
            var started = Date.parse(run.started_at);
            var ended = Date.parse(run.completed_at);
            if (!isNaN(started) && !isNaN(ended) && ended > started) {
                return Math.floor((ended - started) / 1000);
            }
        }
        if (run.started_at && (status === "running" || status === "waiting")) {
            var startMs = Date.parse(run.started_at);
            if (!isNaN(startMs)) return Math.max(0, Math.floor((Date.now() - startMs) / 1000));
        }
        return 0;
    }

    function updateWorkflowTabRunControls(activeRuns) {
        var run = currentWorkflowActiveRun(activeRuns);
        var timerEl = document.getElementById("wf-tab-run-timer");
        var stopBtn = document.getElementById("wf-tab-run-stop");
        if (!timerEl || !stopBtn) return;
        if (!run) {
            timerEl.classList.add("hidden");
            stopBtn.classList.add("hidden");
            timerEl.textContent = "";
            if (workflowTabRunTimerInterval) {
                clearInterval(workflowTabRunTimerInterval);
                workflowTabRunTimerInterval = null;
            }
            return;
        }
        timerEl.classList.remove("hidden");
        stopBtn.classList.remove("hidden");
        stopBtn.dataset.workflowId = String(run.workflow_id);
        stopBtn.dataset.runId = String(run.id);
        timerEl.textContent = workflowRunStatusLabel(run.status) + " · " + formatElapsed(workflowRunDurationSeconds(run));
        if (!workflowTabRunTimerInterval) {
            workflowTabRunTimerInterval = setInterval(function () {
                var active = currentWorkflowActiveRun();
                if (!active) {
                    updateWorkflowTabRunControls([]);
                    return;
                }
                var el = document.getElementById("wf-tab-run-timer");
                if (el) el.textContent = workflowRunStatusLabel(active.status) + " · " + formatElapsed(workflowRunDurationSeconds(active));
            }, 1000);
        }
    }

    function workflowRunStatusLabel(status) {
        var labels = {
            queued: "Queued",
            preflight: "Preflight",
            running: "Running",
            waiting: "Waiting for you",
            retrying: "Retrying",
            completed: "Completed",
            passed: "Passed",
            failed: "Failed",
            cancelled: "Cancelled",
            skipped: "Skipped"
        };
        var key = String(status || "running").toLowerCase();
        return labels[key] || key.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
    }

    function cancelWorkflowRun(workflowId, runId, buttonEl) {
        if (!workflowId || !runId) return Promise.resolve();
        if (buttonEl) buttonEl.disabled = true;
        return api("POST", "/workflows/" + encodeURIComponent(workflowId) + "/cancel-run/" + encodeURIComponent(runId))
            .then(function (resp) {
                snack(workflowFeedbackText(resp, "Run cancelled"));
                stopPolling();
                loadActiveRuns();
                if (currentWorkflowId) loadDetail(currentWorkflowId);
            })
            .catch(function (e) {
                if (buttonEl) buttonEl.disabled = false;
                snack(workflowErrorText(e, "Failed to cancel"), "error");
            });
    }

    function workflowRunMatchesSelectedBoard(run) {
        if (!run) return false;
        var selected = getSelectedWorkflowBoardOption();
        if (!selected) return true;
        var localBoardId = workflowBoardLocalId(selected);
        if (localBoardId && run.board_id != null && String(run.board_id) === String(localBoardId)) return true;
        if (selected.name && run.board_name && String(run.board_name).toLowerCase() === String(selected.name).toLowerCase()) return true;
        if (run.ticket_id) {
            var ticket = workflowQueueTicketById(run.ticket_id);
            if (ticket && workflowTicketMatchesSelectedBoard(ticket, selected)) return true;
        }
        return false;
    }

    function boardWorkflowRunTotalSeconds() {
        var runs = (latestActiveRuns || []).concat(latestWorkflowRunHistoryAll || []);
        var seen = {};
        var total = 0;
        runs.forEach(function (run) {
            if (!run || !run.id || seen[String(run.id)]) return;
            if (currentWorkflowId && String(run.workflow_id) !== String(currentWorkflowId)) return;
            if (!workflowRunMatchesSelectedBoard(run)) return;
            seen[String(run.id)] = true;
            total += workflowRunDurationSeconds(run);
        });
        return total;
    }

    function renderWorkflowBoardTimeTotal() {
        var el = document.getElementById("wf-workflow-board-time-total");
        if (!el) return;
        var visible = workflowQueueTicketsForSelectedBoard(workflowQueueTickets || []);
        if (!visible.length) {
            el.classList.add("hidden");
            el.textContent = "";
            return;
        }
        var total = visible.reduce(function (sum, ticket) {
            return sum + workflowQueueTicketDisplaySeconds(ticket);
        }, 0);
        el.classList.remove("hidden");
        el.textContent = "Ticket time: " + formatElapsed(total);
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

    function executionSessionThreadId(session) {
        var packet = session && session.input_packet;
        if (packet && typeof packet === "object") {
            if (packet.external_thread_id) return String(packet.external_thread_id);
            if (packet.thread_id) return String(packet.thread_id);
        }
        var events = Array.isArray(session && session.events) ? session.events : [];
        for (var i = events.length - 1; i >= 0; i--) {
            var payload = events[i] && events[i].payload;
            if (!payload || typeof payload !== "object") continue;
            if (payload.external_thread_id) return String(payload.external_thread_id);
            if (payload.thread_id) return String(payload.thread_id);
            if (payload.payload && typeof payload.payload === "object") {
                if (payload.payload.external_thread_id) return String(payload.payload.external_thread_id);
                if (payload.payload.thread_id) return String(payload.payload.thread_id);
            }
        }
        return "";
    }

    function executionSessionContextSummary(session) {
        var packet = session && session.input_packet;
        if (!packet || typeof packet !== "object") return "";
        var parts = [];
        if (packet.project_name) parts.push("Project: " + packet.project_name);
        if (packet.ticket_title) parts.push("Ticket: " + packet.ticket_title);
        if (packet.workflow_id) parts.push("Workflow #" + packet.workflow_id);
        if (packet.run_id) parts.push("Run #" + packet.run_id);
        if (packet.step_id) parts.push("Step #" + packet.step_id);
        if (packet.complexity) parts.push("Complexity: " + packet.complexity);
        return parts.join(" | ");
    }

    function renderWorkflowCliSessionThread(sessions) {
        var list = document.getElementById("wf-cli-session-thread-list");
        if (!list) return;
        sessions = Array.isArray(sessions) ? sessions.slice() : [];
        sessions.sort(function (a, b) {
            return new Date(a.started_at || 0).getTime() - new Date(b.started_at || 0).getTime();
        });
        if (!sessions.length) {
            list.innerHTML = '<p class="wf-cli-session-thread-empty">No workflow-linked CLI session activity yet.</p>';
            return;
        }
        list.innerHTML = sessions.map(function (session) {
            var packet = session && session.input_packet && typeof session.input_packet === "object" ? session.input_packet : {};
            var started = session.started_at ? new Date(session.started_at).toLocaleString() : "";
            var backend = session.backend_id || session.route_backend || packet.backend_id || "backend";
            var model = session.model || session.selected_model || packet.model || "auto";
            var threadId = executionSessionThreadId(session);
            var instruction = executionSessionInstructionPreview(session);
            var output = executionSessionOutputText(session) || session.error || "";
            var contextSummary = executionSessionContextSummary(session);
            var events = Array.isArray(session.events) ? session.events.slice(-4) : [];
            var ticketTitle = session.ticket_title || packet.ticket_title || (session.ticket_id ? ("Ticket #" + session.ticket_id) : "Workflow session");
            return '<div class="wf-cli-session-thread-card">' +
                '<div class="wf-cli-session-thread-head">' +
                    '<div class="min-w-0">' +
                        '<div class="wf-cli-session-thread-title">' + esc(ticketTitle) + '</div>' +
                        (started ? '<div class="text-[11px] text-gray-500 mt-1">' + esc(started) + '</div>' : '') +
                    '</div>' +
                    '<span class="text-[11px] px-1.5 py-0.5 rounded ' + executionSessionStatusClass(session.status || "") + '">' + esc(session.status || "session") + '</span>' +
                '</div>' +
                '<div class="wf-cli-session-thread-meta">' +
                    '<span class="wf-cli-session-thread-chip">CLI: ' + esc(backend) + '</span>' +
                    '<span class="wf-cli-session-thread-chip">Model: ' + esc(model) + '</span>' +
                    '<span class="wf-cli-session-thread-chip">Session #' + esc(session.id) + '</span>' +
                    (threadId ? '<span class="wf-cli-session-thread-chip">Thread: ' + esc(threadId) + '</span>' : '') +
                '</div>' +
                (instruction ? '<div class="wf-cli-session-thread-block"><span class="wf-cli-session-thread-label">Submitted / dispatched prompt</span><p class="wf-cli-session-thread-text">' + esc(instruction) + '</p></div>' : '') +
                (contextSummary ? '<div class="wf-cli-session-thread-block wf-cli-session-thread-block--context"><span class="wf-cli-session-thread-label">Injected workflow context</span><p class="wf-cli-session-thread-text">' + esc(contextSummary) + '</p></div>' : '') +
                (output ? '<div class="wf-cli-session-thread-block wf-cli-session-thread-block--output"><span class="wf-cli-session-thread-label">Output / result</span><p class="wf-cli-session-thread-text">' + esc(output) + '</p></div>' : '') +
                (events.length ? '<div class="wf-cli-session-thread-events">' + events.map(function (event) {
                    return '<div class="wf-cli-session-thread-event"><strong>' + esc(event.event_type || "event") + '</strong>' +
                        (event.status ? ' <span class="text-gray-500">/ ' + esc(event.status) + '</span>' : '') +
                        (event.message ? '<div class="mt-1 whitespace-pre-wrap">' + esc(event.message) + '</div>' : '') +
                    '</div>';
                }).join("") + '</div>' : '') +
            '</div>';
        }).join("");
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

    function stopWorkflowRealtime() {
        stopPolling();
        stopExecutionSessionPolling();
        stopVersionPolling();
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = null;
        }
        if (ws) {
            try { ws.onopen = null; ws.onmessage = null; ws.onerror = null; ws.onclose = null; ws.close(); } catch (e) {}
            ws = null;
        }
    }

    function resumeWorkflowRealtime() {
        connectWebSocket();
        startVersionPolling();
        if (currentWorkflowId) {
            checkActiveRun();
            if (workflowActiveDetailTab() === "runs") {
                loadWorkflowExecutionSessions({ quiet: true });
            }
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

    function workflowQueueTicketById(ticketId) {
        if (ticketId == null || ticketId === "") return null;
        return (workflowQueueTickets || []).filter(function (ticket) {
            return ticket && String(ticket.id) === String(ticketId);
        })[0] || null;
    }

    function workflowTicketLabel(ticketId, fallbackTitle) {
        var ticket = workflowQueueTicketById(ticketId);
        if (ticket && ticket.title) return ticket.title;
        if (fallbackTitle) return fallbackTitle;
        return ticketId ? ("Ticket #" + ticketId) : "";
    }

    function isRunActiveForTicketContext(run) {
        if (!run) return false;
        var status = String(run.status || "").toLowerCase();
        return status === "running" || status === "waiting";
    }

    function loopTicketMetricBit(ticket, key, label) {
        var value = ticket && ticket[key];
        return value ? (label + value) : "";
    }

    function loopRunBoardBit(run, mode) {
        var meta = runMetaText(run, currentWorkflow && currentWorkflow.name);
        if (mode === "feed") return "";
        if (meta.boardText === "No board") return "";
        return meta.boardText;
    }

    function loopRunIdBit(run) {
        return run.id ? ("Run #" + run.id) : "";
    }

    function loopRunStepBit(run) {
        if (run.current_step_name) return run.current_step_name;
        return run.current_step_id ? ("Step #" + run.current_step_id) : "";
    }

    function loopRunStepPosition(run) {
        var steps = currentWorkflow && Array.isArray(currentWorkflow.steps) ? currentWorkflow.steps : [];
        if (!run || run.current_step_id == null) return "";
        var index = steps.findIndex(function (step) {
            return String(step.id) === String(run.current_step_id);
        });
        return index >= 0 ? ("Step " + (index + 1) + " of " + steps.length) : "";
    }

    function selectedBoardLabel() {
        var selected = getSelectedWorkflowBoardOption();
        return selected && selected.name ? selected.name : "the selected board";
    }

    function workflowBoardOptionForRun(run) {
        if (!run) return null;
        return (workflowBoardOptions || []).filter(function (option) {
            var localId = workflowBoardLocalId(option);
            if (localId && run.board_id != null && String(localId) === String(run.board_id)) return true;
            return option.name && run.board_name && String(option.name).toLowerCase() === String(run.board_name).toLowerCase();
        })[0] || null;
    }

    function focusWorkflowRunBoard(run) {
        var option = workflowBoardOptionForRun(run);
        var select = document.getElementById("wf-board-select");
        if (!option || !select) {
            snack("The running board is not available in this board selector", "error");
            return;
        }
        select.value = option.value;
        try { localStorage.setItem("wf_board_selected", option.value); } catch (e) {}
        loadWorkflowBoardTickets(option.value);
        if (run.ticket_id) {
            selectedWorkflowQueueTicketId = String(run.ticket_id);
            workflowRunsFilterTicketId = selectedWorkflowQueueTicketId;
        }
        renderLoopRunTicketContext();
    }

    function loopRunTicketMetaBits(run, ticket, mode) {
        return [
            loopRunIdBit(run),
            workflowRunStatusLabel(run.status),
            loopRunStepPosition(run),
            loopTicketMetricBit(ticket, "priority", "Priority "),
            loopTicketMetricBit(ticket, "complexity", "Complexity "),
            loopRunBoardBit(run, mode)
        ].filter(Boolean);
    }

    function loopQueuedActionHtml(run) {
        if (!run.ticket_id) return "";
        return '<button type="button" class="wf-loop-start-ticket px-3 py-1.5 rounded bg-[#f97316] text-white text-xs font-semibold hover:bg-[#ea580c]" data-ticket-id="' + esc(run.ticket_id) + '">Start workflow</button>';
    }

    function loopWaitingActionHtml(run) {
        if (!run.id) return "";
        return '<button type="button" class="wf-loop-continue-ticket px-3 py-1.5 rounded bg-amber-500 text-[#15182d] text-xs font-semibold hover:bg-amber-400" data-workflow-id="' + esc(run.workflow_id || currentWorkflowId) + '" data-run-id="' + esc(run.id) + '">Review &amp; continue</button>';
    }

    function loopFinishedActionHtml(run) {
        if (!run.id) return "";
        return '<button type="button" class="wf-loop-run-again px-3 py-1.5 rounded border border-[#f97316]/60 text-[#f97316] text-xs hover:bg-[#f97316]/10" data-ticket-id="' + esc(run.ticket_id || "") + '">Run again</button>';
    }

    function loopRunningActionHtml() {
        return '<span class="wf-loop-running-label text-xs text-sky-300">Executing now</span>';
    }

    function loopRunTicketActionHtml(run, mode) {
        if (mode !== "main") return "";
        var status = String(run.status || "queued").toLowerCase();
        var builders = {
            queued: loopQueuedActionHtml,
            waiting: loopWaitingActionHtml,
            running: loopRunningActionHtml
        };
        return (builders[status] || loopFinishedActionHtml)(run);
    }

    function loopRunTicketContextHtml(run, mode) {
        if (!run || !(run.ticket_id || run.ticket_title)) return "";
        var ticket = workflowQueueTicketById(run.ticket_id);
        var title = workflowTicketLabel(run.ticket_id, run.ticket_title);
        var bits = loopRunTicketMetaBits(run, ticket, mode);
        var actions = loopRunTicketActionHtml(run, mode);
        var meta = runMetaText(run, currentWorkflow && currentWorkflow.name);
        var isActive = isRunActiveForTicketContext(run);
        var mismatch = isActive && !workflowRunMatchesSelectedBoard(run);
        var route = run.execution_route || {};
        var routeLabel = [route.backend, route.model_provider, route.model].filter(Boolean).join(" / ");
        var heartbeatAge = run.heartbeat_age_seconds == null ? null : Math.max(0, parseInt(run.heartbeat_age_seconds, 10) || 0);
        var heartbeatText = heartbeatAge == null ? "" : (heartbeatAge < 15 ? "Heartbeat now" : ("Heartbeat " + formatElapsed(heartbeatAge) + " ago"));
        if (mode === "feed") {
            return '<div class="wf-loop-decision-context">' +
                '<div class="wf-loop-ticket-kicker"><span>' + esc(isActive ? "Current execution" : "Run context") + '</span></div>' +
                '<div class="wf-loop-ticket-title">' + esc(loopRunStepBit(run) || title) + '</div>' +
                '<div class="wf-loop-ticket-meta">' + [workflowRunStatusLabel(run.status), routeLabel, heartbeatText].filter(Boolean).map(function (bit) {
                    return '<span>' + esc(bit) + '</span>';
                }).join("") + '</div>' +
            '</div>';
        }
        return '' +
            '<div class="wf-loop-run-header">' +
                '<div class="wf-loop-run-header-main">' +
                    '<div class="wf-loop-ticket-kicker"><span>' + esc(isActive ? "Active workflow run" : "Selected ticket") + '</span></div>' +
                    '<div class="wf-loop-run-breadcrumb">' +
                        '<span>' + esc(meta.projectText) + '</span><b>›</b><span>' + esc(meta.boardText) + '</span><b>›</b>' +
                        '<strong title="' + esc(title) + '">' + esc(title) + '</strong>' +
                    '</div>' +
                    '<div class="wf-loop-ticket-meta">' + bits.slice(0, 7).map(function (bit) {
                        return '<span>' + esc(bit) + '</span>';
                    }).join("") + '</div>' +
                '</div>' +
                '<div class="wf-loop-run-header-route">' +
                    (routeLabel ? '<span class="wf-loop-run-route-label">' + esc(routeLabel) + '</span>' : '') +
                    (heartbeatText ? '<span class="wf-loop-run-heartbeat">● ' + esc(heartbeatText) + '</span>' : '') +
                    (actions ? '<div class="wf-loop-ticket-actions">' + actions + '</div>' : '') +
                '</div>' +
            '</div>' +
            (mismatch ? '<div class="wf-loop-board-mismatch">' +
                '<span>You are viewing tickets for <strong>' + esc(selectedBoardLabel()) + '</strong>, while this run belongs to <strong>' + esc(meta.boardText) + '</strong>.</span>' +
                '<button type="button" class="wf-loop-focus-run-board" data-run-id="' + esc(run.id || "") + '">Show running project tickets</button>' +
            '</div>' : '');
    }

    function selectedLoopRunContext() {
        // Live execution is authoritative in the Loop view. Ticket browsing is
        // allowed to remain on another board, but it must never replace the run
        // identity or activity with an unrelated queued ticket.
        var workflowRun = currentWorkflowActiveRun();
        if (workflowRun) return workflowRun;
        var selectedId = selectedWorkflowQueueTicketId;
        var contextRun = loopFeedContextRun() || (selectedId ? activeRunByTicketId(selectedId) : null);
        if (contextRun) return contextRun;
        return queuedLoopRunContext(workflowQueueTicketById(selectedId));
    }

    function queuedLoopRunContext(ticket) {
        if (!ticket) return null;
        return {
            ticket_id: ticket.id,
            ticket_title: ticket.title,
            status: "queued",
            project_id: ticket.linked_project_id,
            project_name: ticket.project_name,
            board_id: ticket.board_id,
            board_name: ticket.board_name,
            workflow_id: currentWorkflowId
        };
    }

    function renderLoopTicketContextElement(element, contextRun, mode) {
        if (!element) return;
        element.classList.toggle("hidden", !contextRun);
        element.innerHTML = contextRun ? loopRunTicketContextHtml(contextRun, mode) : "";
    }

    function bindLoopTicketContextActions(mainEl) {
        var startBtn = mainEl.querySelector(".wf-loop-start-ticket, .wf-loop-run-again");
        if (startBtn) startBtn.onclick = function () { openWorkflowRunPreview(startBtn.dataset.ticketId); };
        var continueBtn = mainEl.querySelector(".wf-loop-continue-ticket");
        if (continueBtn) continueBtn.onclick = function () {
            continueWorkflowRun(continueBtn.dataset.workflowId, continueBtn.dataset.runId, { input: "yes, go ahead" });
        };
        var focusBoardBtn = mainEl.querySelector(".wf-loop-focus-run-board");
        if (focusBoardBtn) focusBoardBtn.onclick = function () {
            focusWorkflowRunBoard(runRecordById(focusBoardBtn.dataset.runId) || currentWorkflowActiveRun());
        };
    }

    function renderLoopRunTicketContext() {
        var mainEl = document.getElementById("wf-loop-run-ticket-context");
        var contextRun = selectedLoopRunContext();
        renderLoopTicketContextElement(mainEl, contextRun, "main");
        renderLoopTicketContextElement(document.getElementById("wf-loop-feed-ticket-context"), contextRun, "feed");
        if (contextRun && mainEl) bindLoopTicketContextActions(mainEl);
    }

    function workflowHasActiveRuns() {
        return (latestActiveRuns || []).some(function (r) {
            return currentWorkflowId && r && String(r.workflow_id) === String(currentWorkflowId);
        });
    }

    function currentWorkflowActiveRun(activeRuns) {
        activeRuns = activeRuns || latestActiveRuns || [];
        return activeRuns.filter(function (r) {
            return currentWorkflowId && r && String(r.workflow_id) === String(currentWorkflowId) &&
                (r.status === "running" || r.status === "waiting");
        })[0] || null;
    }

    var lastAutoAdvanceSnackRunId = null;

    function renderWorkflowRunBar(activeRuns) {
        var bar = document.getElementById("wf-run-bar");
        if (!bar) return;
        var run = (activeRuns || []).filter(function (r) {
            return currentWorkflowId && r && String(r.workflow_id) === String(currentWorkflowId);
        })[0] || null;
        if (run && run.auto_queued_from_run_id && run.id !== lastAutoAdvanceSnackRunId) {
            lastAutoAdvanceSnackRunId = run.id;
            snack("Starting next ticket in queue", "info");
        }
        bar.classList.add("hidden");
        bar.innerHTML = "";
    }

    function normalizedRunSettings(data) {
        var raw = data && data.run_settings;
        var settings = raw && typeof raw === "object" ? raw : {};
        return {
            execution_mode: settings.execution_mode === "parallel" ? "parallel" : DEFAULT_RUN_SETTINGS.execution_mode,
            concurrency_scope: settings.concurrency_scope === "workflow" ? "workflow" : DEFAULT_RUN_SETTINGS.concurrency_scope,
            max_parallel_tickets: Math.max(1, Math.min(12, parseInt(settings.max_parallel_tickets || DEFAULT_RUN_SETTINGS.max_parallel_tickets, 10) || DEFAULT_RUN_SETTINGS.max_parallel_tickets)),
            branch_per_ticket: settings.branch_per_ticket !== false,
            auto_route_models: settings.auto_route_models !== false,
            free_only: !!settings.free_only,
            prefer_local: settings.prefer_local !== false,
            chosen_models: workflowCliNormalizeChosenModels(settings.chosen_models || [])
        };
    }

    function collectRunSettings() {
        return {
            execution_mode: (document.getElementById("wf-config-run-execution-mode") || {}).value || DEFAULT_RUN_SETTINGS.execution_mode,
            concurrency_scope: (document.getElementById("wf-config-run-concurrency-scope") || {}).value || DEFAULT_RUN_SETTINGS.concurrency_scope,
            max_parallel_tickets: Math.max(1, Math.min(12, parseInt((document.getElementById("wf-config-run-max-parallel") || {}).value || DEFAULT_RUN_SETTINGS.max_parallel_tickets, 10) || DEFAULT_RUN_SETTINGS.max_parallel_tickets)),
            branch_per_ticket: !!((document.getElementById("wf-config-run-branch-per-ticket") || {}).checked),
            auto_route_models: !!((document.getElementById("wf-config-run-auto-route-models") || {}).checked),
            free_only: !!((document.getElementById("wf-config-run-free-only") || {}).checked),
            prefer_local: !!((document.getElementById("wf-config-run-prefer-local") || {}).checked),
            chosen_models: workflowCliNormalizeChosenModels(_wfCliChosenModels)
        };
    }

    function renderRunSettings(data) {
        var settings = normalizedRunSettings(data || currentWorkflow || {});
        var modeEl = document.getElementById("wf-config-run-execution-mode");
        var scopeEl = document.getElementById("wf-config-run-concurrency-scope");
        var maxEl = document.getElementById("wf-config-run-max-parallel");
        var branchEl = document.getElementById("wf-config-run-branch-per-ticket");
        var autoRouteEl = document.getElementById("wf-config-run-auto-route-models");
        var freeOnlyEl = document.getElementById("wf-config-run-free-only");
        var preferLocalEl = document.getElementById("wf-config-run-prefer-local");
        var noteEl = document.getElementById("wf-config-run-lock-note");
        var panelEl = document.getElementById("wf-config-run-settings-panel");
        if (!modeEl || !scopeEl || !maxEl || !branchEl) return;
        modeEl.value = settings.execution_mode;
        scopeEl.value = settings.concurrency_scope;
        maxEl.value = settings.max_parallel_tickets;
        branchEl.checked = !!settings.branch_per_ticket;
        if (autoRouteEl) autoRouteEl.checked = !!settings.auto_route_models;
        if (freeOnlyEl) freeOnlyEl.checked = !!settings.free_only;
        if (preferLocalEl) preferLocalEl.checked = !!settings.prefer_local;
        _wfCliChosenModels = workflowCliNormalizeChosenModels(settings.chosen_models || []);
        renderWorkflowCliChosenModels();
        var locked = workflowHasActiveRuns();
        if (panelEl) {
            panelEl.classList.toggle("is-locked", locked);
        }
        document.querySelectorAll(".wf-config-run-setting").forEach(function (el) { el.disabled = locked; });
        if (noteEl) {
            noteEl.textContent = locked ? "Locked while this workflow has active runs." : "Controls how queued tickets are scheduled before a run starts.";
            noteEl.className = "text-xs mt-0.5 " + (locked ? "text-amber-300" : "text-gray-500");
        }
    }

    function refreshWorkflowConfigPanel() {
        syncWorkflowConfigTabBodies();
        ensureGlobalConfigExecutionRoutesShell();
        return refreshWorkflowGlobalExecutionPanel().then(function () {
            if (currentWorkflowId) {
                renderRunSettings(currentWorkflow);
                renderContextRules(currentWorkflow || {});
            }
        });
    }

    function saveWorkflowRunSettings() {
        if (!currentWorkflowId || workflowHasActiveRuns()) return Promise.resolve();
        var settings = collectRunSettings();
        return api("PATCH", "/workflows/" + currentWorkflowId, { run_settings: settings })
            .then(function () {
                if (currentWorkflow) currentWorkflow.run_settings = settings;
            });
    }

    window.refreshWorkflowConfigPanel = refreshWorkflowConfigPanel;
    window.saveWorkflowRunSettings = saveWorkflowRunSettings;

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
        if (!listEl || !emptyEl || !ticketsListEl) return Promise.resolve();
        var query = "/workflows/active-runs?limit=50";
        if (activeRunsScope === "current" && currentWorkflowId) {
            query += "&workflow_id=" + encodeURIComponent(currentWorkflowId);
        }
        return api("GET", query).then(function (runs) {
            latestActiveRuns = Array.isArray(runs) ? runs : [];
            var activeCurrentWorkflowRun = currentWorkflowActiveRun(latestActiveRuns);
            if (
                activeCurrentWorkflowRun &&
                !workflowBoardSelectionExplicit &&
                !workflowRunMatchesSelectedBoard(activeCurrentWorkflowRun) &&
                workflowBoardOptionForRun(activeCurrentWorkflowRun)
            ) {
                focusWorkflowRunBoard(activeCurrentWorkflowRun);
            }
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
            renderRunSettings(currentWorkflow);
            renderBoardConsumers(latestActiveRuns);
            renderRunCommandCenter(latestActiveRuns);
            renderWorkflowRunBar(latestActiveRuns);
            renderLoopRunTicketContext();
            syncSteeringRunSelect();
            syncLoopFeedRunSelect();
            loadLoopActivityFeed({ quiet: true });
            if (workflowRunsSubtab === "memory") loadWorkflowSteeringMemory({ quiet: true });
            if (workflowRunsSubtab === "timeline") loadOrchestratorTimeline({ quiet: true });
            renderWorkflowTickets(workflowQueueTickets);
            renderWorkflowBoardTimeTotal();
            ensureWorkflowTicketTimerTick();
            var nextActiveTicketIds = latestActiveRuns
                .filter(function (r) {
                    return r && r.ticket_id && (r.status === "running" || r.status === "waiting");
                })
                .map(function (r) { return String(r.ticket_id); });
            workflowActiveTicketIdsSnapshot.forEach(function (ticketId) {
                if (nextActiveTicketIds.indexOf(ticketId) !== -1) return;
                fetchWorkflowQueueTicketRecord(ticketId).then(function (ticket) {
                    if (!ticket || !ticket.id) return;
                    var idx = workflowQueueTickets.findIndex(function (item) {
                        return String(item.id) === String(ticket.id);
                    });
                    if (idx < 0) return;
                    workflowQueueTickets[idx].time_spent = ticket.time_spent || workflowQueueTickets[idx].time_spent;
                    delete workflowTicketPendingRunStartedAt[String(ticket.id)];
                    renderWorkflowTickets(workflowQueueTickets);
                }).catch(function () {});
            });
            workflowActiveTicketIdsSnapshot = nextActiveTicketIds;
            if (stopAllBtn) {
                var hasActiveCurrentWorkflowRuns = latestActiveRuns.some(function (r) {
                    return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
                });
                stopAllBtn.classList.toggle("hidden", !hasActiveCurrentWorkflowRuns);
            }
            syncWorkflowRunsTabVisibility();
            renderWorkflowRunBar(latestActiveRuns);
            updateWorkflowTabRunControls(latestActiveRuns);
            var hasActiveCurrentWorkflowRun = latestActiveRuns.some(function (r) {
                return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId) &&
                    (r.status === "running" || r.status === "waiting");
            });
            if (hasActiveCurrentWorkflowRun) startPolling();
            else stopPolling();
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
                var recentStepNames = workflowCleanStringList(r.recent_step_names).filter(function (name) {
                    return name && name !== stepText;
                }).slice(0, 4);
                var recentStepsHtml = recentStepNames.length
                    ? '<div class="md:col-span-2"><span class="text-gray-500">Recent steps:</span> <span class="text-gray-300">' + esc(recentStepNames.join(" → ")) + '</span></div>'
                    : '';
                var workflowText = meta.workflowText || ("Workflow #" + r.workflow_id);
                var routeCard = renderRouteCard(r.execution_route || {}, {
                    pendingApproval: r.pending_route_approval && Object.keys(r.pending_route_approval || {}).length,
                    skills: workflowCleanStringList(r.current_step_skills).concat(workflowCleanStringList((r.execution_route || {}).skills)),
                    tools: workflowCleanStringList(r.current_step_tools)
                        .concat(workflowCleanStringList((r.execution_route || {}).tools))
                        .concat(workflowCleanStringList(r.recent_step_tools)),
                    context: workflowCleanStringList(r.current_step_context)
                        .concat(workflowCleanStringList((r.execution_route || {}).context))
                        .concat(workflowCleanStringList(r.recent_step_context))
                });
                var loopBadge = r.loop_label
                    ? '<span class="text-xs px-1.5 py-0.5 rounded bg-purple-600/20 text-purple-200">' + esc(r.loop_label) + '</span>'
                    : '';
                var groupSize = parseInt(r.ticket_group_size, 10) || 0;
                var groupPosition = (parseInt(r.ticket_group_index, 10) || 0) + 1;
                var groupBadge = r.ticket_group_id && groupSize > 1
                    ? '<span class="text-xs px-1.5 py-0.5 rounded bg-cyan-600/20 text-cyan-200">Group ' + esc(groupPosition) + '/' + esc(groupSize) + '</span>'
                    : '';
                var rowCls = "rounded px-3 py-2 border border-white/10 " + (isCurrentWorkflow ? "wf-live-run" : "bg-[#152054]/50");
                var waitingKind = r.waiting_kind || "";
                var hasDecision = r.approval_decision && r.approval_decision.title;
                var continueLabel = waitingKind === "ide_handoff"
                    ? "Report CLI complete"
                    : (waitingKind === "route_approval" || waitingKind === "provider_preflight" ? "Review route" : (hasDecision ? "Yes, go ahead" : "Continue"));
                var decisionCard = (r.status === "waiting" && hasDecision)
                    ? '<div class="mt-2">' + renderApprovalDecisionCard(r.approval_decision, {
                        showActions: true,
                        workflowId: r.workflow_id,
                        runId: r.id
                    }) + '</div>'
                    : '';
                var lastActivity = r.last_activity || {};
                var heartbeat = r.last_heartbeat || {};
                var heartbeatAge = r.heartbeat_age_seconds == null ? null : Math.max(0, parseInt(r.heartbeat_age_seconds, 10) || 0);
                var activityState = r.activity_state || "starting";
                var activityText = lastActivity.message || (lastActivity.event_type ? lastActivity.event_type.replace(/_/g, " ") : "");
                var activityHtml = activityText
                    ? '<div class="md:col-span-2"><span class="text-gray-500">Last activity:</span> <span class="text-gray-300">' + esc(activityText) + '</span>' +
                        (heartbeat.at ? ' <span class="text-emerald-300">· heartbeat ' + esc(new Date(heartbeat.at).toLocaleTimeString()) + '</span>' : '') + '</div>'
                    : '<div class="md:col-span-2 text-amber-300">Waiting for the first worker heartbeat…</div>';
                var contextTelemetry = r.latest_context_telemetry || {};
                var contextChars = Math.max(0, parseInt(contextTelemetry.total_chars, 10) || 0);
                var contextMax = Math.max(0, parseInt(contextTelemetry.max_chars, 10) || 0);
                var contextCounts = contextTelemetry.counts || {};
                var contextHtml = contextChars
                    ? '<div class="md:col-span-2"><span class="text-gray-500">Context packet:</span> <span class="text-gray-300">' +
                        esc(contextChars.toLocaleString()) + (contextMax ? (' / ' + esc(contextMax.toLocaleString())) : '') +
                        ' chars · ' + esc(parseInt(contextCounts.artifact_refs, 10) || 0) + ' artifact refs · ' +
                        esc(parseInt(contextCounts.memory_facts, 10) || 0) + ' relevant memory facts</span></div>'
                    : '';
                if (activityState === "stale") {
                    activityHtml += '<div class="md:col-span-2 text-red-300">Worker heartbeat overdue by ' + esc(formatElapsed(heartbeatAge)) + '. You can stop the run or wait for provider recovery.</div>';
                } else if (activityState === "delayed") {
                    activityHtml += '<div class="md:col-span-2 text-amber-300">Worker heartbeat delayed (' + esc(formatElapsed(heartbeatAge)) + ' ago); reconnect/recovery is still in progress.</div>';
                } else if (activityState === "no_heartbeat") {
                    activityHtml += '<div class="md:col-span-2 text-amber-300">No worker heartbeat has arrived after ' + esc(formatElapsed(r.elapsed_seconds)) + '.</div>';
                }
                var actions = '<div class="flex items-center gap-2 ml-auto">' +
                    (r.status === "waiting" ? '<button type="button" class="wf-active-continue px-2 py-1 rounded border border-amber-500/50 text-amber-300 text-xs hover:bg-amber-500/20" data-workflow-id="' + esc(r.workflow_id) + '" data-run-id="' + esc(r.id) + '" data-waiting-kind="' + esc(waitingKind) + '">' + esc(continueLabel) + '</button>' : '') +
                    '<button type="button" class="wf-active-stop inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/50 text-red-400 text-xs hover:bg-red-500/20" data-workflow-id="' + esc(r.workflow_id) + '" data-run-id="' + esc(r.id) + '">' + SVG_STOP + '<span>Stop</span></button>' +
                '</div>';
                return '<div class="' + rowCls + '">' +
                    '<div class="flex items-center gap-2 mb-1">' +
                        '<span class="text-xs text-gray-400">Run #' + r.id + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded ' + statusColor + '">' + esc(r.status) + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded bg-green-600/20 text-green-300">' + esc(phase) + '</span>' +
                        loopBadge +
                        groupBadge +
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
                        activityHtml +
                        contextHtml +
                        recentStepsHtml +
                    '</div>' +
                    '<div class="mt-2">' + routeCard + '</div>' +
                    decisionCard +
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
                    cancelWorkflowRun(btn.dataset.workflowId, btn.dataset.runId, btn);
                });
            });
            bindApprovalDecisionButtons(listEl);
        }).catch(function () {
            renderWorkflowTickets(workflowQueueTickets);
            return [];
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
            var liveConnected = !!session.live_connected;
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
                            (session.external_thread_id ? '<span class="text-[11px] text-gray-500 truncate">thread ' + esc(session.external_thread_id) + '</span>' : '') +
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
                        ((active || liveConnected) && session.project_id && (session.backend_id || session.route_backend) ? '<button type="button" class="wf-execution-session-disconnect px-2 py-1 rounded border border-red-500/40 text-red-200 text-xs hover:bg-red-500/10" data-project-id="' + esc(session.project_id) + '" data-backend-id="' + esc(session.backend_id || session.route_backend || "") + '" data-board-id="' + esc(session.board_id || "") + '">Disconnect</button>' : '') +
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
                api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId))
                    .then(function (ticket) {
                        openWorkflowTicketModal(ticket, { selected: { source: "database", value: "", label: ticket.board_name || "Workflow execution" } });
                    })
                    .catch(function (e) { snack(e.message || "Failed to open ticket", "error"); });
            });
        });
        listEl.querySelectorAll(".wf-execution-session-disconnect").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var projectId = btn.dataset.projectId || "";
                var backendId = btn.dataset.backendId || "";
                var boardId = btn.dataset.boardId || "";
                if (!projectId || !backendId) return;
                api("POST", "/projects/" + encodeURIComponent(projectId) + "/cli-session/disconnect", {
                    backend_id: backendId,
                    board_id: boardId || null
                }).then(function (data) {
                    snack((data && data.message) || "Session disconnected", "success");
                    loadWorkflowExecutionSessions({ quiet: true });
                    if (
                        String(workflowBoardProjectId() || "") === String(projectId) &&
                        String(workflowCliActiveBackend() || "") === String(backendId) &&
                        String(((workflowCliAreaContext() || {}).board_id) || "") === String(boardId || "")
                    ) {
                        disconnectWorkflowCliWs();
                    }
                }).catch(function (e) {
                    snack(e.message || "Failed to disconnect session", "error");
                });
            });
        });
    }

    function loadWorkflowExecutionSessions(options) {
        options = options || {};
        if (!currentWorkflowId) {
            latestWorkflowExecutionSessions = [];
            renderWorkflowExecutionSessions([]);
            renderWorkflowCliSessionThread([]);
            stopExecutionSessionPolling();
            renderWorkflowTickets(workflowQueueTickets);
            refreshWorkflowCliTabIfVisible();
            return;
        }
        api("GET", "/tickets/workflows/" + encodeURIComponent(currentWorkflowId) + "/execution-sessions?limit=50")
            .then(function (data) {
                latestWorkflowExecutionSessions = Array.isArray(data.sessions) ? data.sessions : [];
                renderWorkflowExecutionSessions(latestWorkflowExecutionSessions);
                renderWorkflowCliSessionThread(latestWorkflowExecutionSessions);
                syncExecutionSessionPolling();
                renderWorkflowTickets(workflowQueueTickets);
                refreshWorkflowCliTabIfVisible();
            })
            .catch(function (e) {
                latestWorkflowExecutionSessions = [];
                renderWorkflowExecutionSessions([]);
                renderWorkflowCliSessionThread([]);
                syncExecutionSessionPolling();
                if (!options.quiet) snack(e.message || "Failed to load CLI execution sessions", "error");
            });
    }

    function loadWorkflowTicketQueue() {
        if (!currentWorkflowId) {
            workflowQueueTickets = [];
            renderWorkflowTickets([]);
            renderLoopRunTicketContext();
            return;
        }
        api("GET", "/tickets/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets")
            .then(function (tickets) {
                workflowQueueTickets = Array.isArray(tickets) ? tickets : [];
                rebuildWorkflowQueueExternalLinkIndex();
                renderWorkflowTickets(workflowQueueTickets);
                renderLoopRunTicketContext();
                refreshWorkflowBoardTicketsFromQueue();
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

    function getSelectedWorkflowBoardOption() {
        var select = document.getElementById("wf-board-select");
        if (!select || !select.value) return null;
        return (workflowBoardOptions || []).filter(function (opt) { return opt.value === select.value; })[0] || null;
    }

    function workflowTicketMatchesSelectedBoard(ticket, selected) {
        if (!ticket) return false;
        selected = selected || getSelectedWorkflowBoardOption();
        if (!selected) return true;
        var localBoardId = workflowBoardLocalId(selected);
        if (localBoardId && ticket.board_id != null && String(ticket.board_id) === String(localBoardId)) return true;
        if (selected.name && ticket.board_name && String(ticket.board_name).toLowerCase() === String(selected.name).toLowerCase()) return true;
        return false;
    }

    function workflowQueueTicketsForSelectedBoard(tickets) {
        tickets = Array.isArray(tickets) ? tickets : [];
        var selected = getSelectedWorkflowBoardOption();
        if (!selected) return tickets;
        return tickets.filter(function (ticket) { return workflowTicketMatchesSelectedBoard(ticket, selected); });
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
        var key = String(source).trim().toLowerCase();
        var labels = {
            auto_role_route: "Auto choice",
            workflow_policy_free_local: "Auto · local/free",
            workflow_policy_free_eligible: "Auto · free eligible",
            runtime_provider_failover: "Automatic failover",
            step_execution_route: "Pinned for this step",
            explicit_request_policy: "Requested in this job",
            orchestrator_override: "Orchestrator choice",
            board_override: "Board policy"
        };
        return labels[key] || String(source).replace(/_/g, " ");
    }

    function workflowCleanStringList(value) {
        if (!value) return [];
        if (typeof value === "string") {
            value = value.split(",");
        }
        if (!Array.isArray(value)) return [];
        var seen = {};
        return value.map(function (item) {
            return String(item || "").trim();
        }).filter(function (item) {
            if (!item || seen[item]) return false;
            seen[item] = true;
            return true;
        });
    }

    function workflowPillListHtml(label, values, colorClass) {
        values = workflowCleanStringList(values);
        if (!values.length) return "";
        colorClass = colorClass || "bg-white/10 text-gray-200";
        return '<div class="mt-2 flex flex-wrap items-center gap-1.5">' +
            '<span class="text-[10px] uppercase tracking-wide text-gray-500">' + esc(label) + '</span>' +
            values.slice(0, 8).map(function (value) {
                return '<span class="rounded px-1.5 py-0.5 text-[11px] ' + colorClass + '">' + esc(value) + '</span>';
            }).join("") +
            (values.length > 8 ? '<span class="text-[11px] text-gray-500">+' + (values.length - 8) + '</span>' : '') +
        '</div>';
    }

    function renderRouteCard(route, options) {
        options = options || {};
        route = route && typeof route === "object" ? route : {};
        var backend = route.backend || route.backend_id || "auto";
        var model = route.model || "auto";
        var source = route.source || route.route_source || "policy";
        var rationale = route.rationale || route.route_rationale || "";
        var pending = options.pendingApproval || route.requires_approval;
        var skills = workflowCleanStringList(options.skills || route.skills);
        var tools = workflowCleanStringList(options.tools || route.tools);
        var context = workflowCleanStringList(options.context || route.context);
        var html = '<div class="rounded border border-blue-500/25 bg-blue-500/5 px-3 py-2 text-xs">' +
            '<div class="flex flex-wrap items-center gap-2">' +
                '<span class="text-[10px] uppercase tracking-wide text-blue-200">Route</span>' +
                '<span class="rounded bg-white/10 px-1.5 py-0.5 text-gray-200">' + esc(backend) + '</span>' +
                '<span class="rounded bg-white/10 px-1.5 py-0.5 text-gray-300">' + esc(model) + '</span>' +
                '<span class="rounded bg-blue-500/15 px-1.5 py-0.5 text-blue-200">' + esc(formatRouteSource(source)) + '</span>' +
                (pending ? '<span class="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-200">override pending approval</span>' : '') +
            '</div>';
        if (rationale) html += '<p class="mt-1 text-[11px] text-gray-400">' + esc(rationale) + '</p>';
        html += workflowPillListHtml("Skills", skills, "bg-emerald-500/15 text-emerald-200");
        html += workflowPillListHtml("Tools", tools, "bg-purple-500/15 text-purple-200");
        html += workflowPillListHtml("Context", context, "bg-sky-500/15 text-sky-200");
        html += '</div>';
        return html;
    }

    function openIdeHandoffModal(onSubmit) {
        var existing = document.getElementById("wf-ide-handoff-modal");
        if (existing) existing.remove();
        var html = '' +
            '<div id="wf-ide-handoff-modal" style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;z-index:2147483646;background:rgba(0,0,0,0.6);">' +
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

    function copyWorkflowHarnessText(text, label) {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
            snack("Clipboard is unavailable in this browser", "error");
            return;
        }
        navigator.clipboard.writeText(text || "")
            .then(function () { snack((label || "Text") + " copied", "success"); })
            .catch(function (e) { snack("Copy failed: " + (e && e.message ? e.message : String(e)), "error"); });
    }

    function _workflowActiveRunIdForHarness() {
        var run = currentWorkflowActiveRun(latestActiveRuns || []);
        return run && run.id != null ? run.id : null;
    }

    function openWorkflowHarnessModal() {
        if (!currentWorkflowId) {
            snack("Select a workflow first", "error");
            return;
        }
        var existing = document.getElementById("wf-harness-handoff-modal");
        if (existing) existing.remove();
        var runId = _workflowActiveRunIdForHarness();
        var qs = runId ? ("?run_id=" + encodeURIComponent(String(runId))) : "";
        var html = '' +
            '<div id="wf-harness-handoff-modal" style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;z-index:2147483646;background:rgba(0,0,0,0.6);padding:1rem;">' +
                '<div class="w-full max-w-2xl h-[38rem] min-h-[38rem] max-h-[90vh] flex flex-col bg-[#1a1f3a] border border-white/20 rounded-xl shadow-2xl overflow-hidden relative">' +
                    '<button type="button" class="wf-harness-handoff-close absolute top-4 right-4 inline-flex h-8 w-8 items-center justify-center rounded-md border border-white/20 text-gray-300 text-sm hover:bg-white/10" aria-label="Close modal">×</button>' +
                    '<div class="px-5 pt-5 pb-4 mb-2 flex-shrink-0">' +
                        '<h3 class="text-white text-lg font-semibold text-center">Handoff</h3>' +
                    '</div>' +
                    '<div id="wf-handoff-body-scroll" class="px-5 pb-3 flex-1 min-h-0 overflow-y-auto">' +
                        '<div class="h-full min-h-[24rem] flex items-center justify-center">' +
                            '<div class="text-center text-sm text-gray-400">' +
                                '<div class="w-8 h-8 rounded-full border-2 border-t-[#f97316] animate-spin mx-auto mb-2" style="border-color: rgba(148, 163, 184, 0.22); border-top-color: #f97316;" role="presentation"></div>' +
                                '<p>Loading workflow memory…</p>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="flex flex-wrap items-center justify-center gap-2 px-5 py-4 border-t border-white/10 flex-shrink-0 text-center">' +
                        '<div id="wf-harness-footer-actions" class="flex flex-wrap items-center justify-center gap-2 text-center"></div>' +
                    '</div>' +
                '</div>' +
            '</div>';
        document.body.insertAdjacentHTML("beforeend", html);
        var modal = document.getElementById("wf-harness-handoff-modal");
        function closeModal() {
            if (modal) modal.remove();
            document.removeEventListener("keydown", handleEscapeKey);
        }
        function handleEscapeKey(evt) {
            if (evt && evt.key === "Escape") closeModal();
        }
        modal.addEventListener("click", function (evt) { if (evt.target === modal) closeModal(); });
        modal.querySelectorAll(".wf-harness-handoff-close").forEach(function (btn) {
            btn.addEventListener("click", closeModal);
        });
        document.addEventListener("keydown", handleEscapeKey);
        api("GET", "/workflows/" + currentWorkflowId + "/harness-handoff" + qs).then(function (data) {
            var body = document.getElementById("wf-handoff-body-scroll");
            if (!body) return;
            var entry = (data && data.entry_file) || "";
            var paste = (data && data.paste_block) || "";
            var contract = (data && data.return_contract) || "";
            var pickup = (data && data.pickup_prompt) || "";
            var learningGuide = (data && data.learning_guide_excerpt) || "";
            var handoffPreview = (data && data.handoff_preview) || "";
            var stepRoutingTable = (data && data.step_routing_table) || "";
            var repoProjection = (data && data.repo_projection) || "";
            var boardId = data && data.linked_board_id != null ? String(data.linked_board_id) : "";
            var projectId = data && data.linked_project_id != null ? String(data.linked_project_id) : "";
            var wfName = esc((data && data.workflow_name) || "Workflow");
            body = document.getElementById("wf-handoff-body-scroll");
            if (!body) return;
            body.innerHTML =
                '<div class="space-y-4 text-sm min-h-[24rem] flex flex-col justify-start">' +
                    '<div class="rounded-lg border bg-[#f97316]/10 px-3 py-3 text-center min-h-[4rem] flex items-center justify-center" style="border-color:#6b7280;">' +
                        '<p class="text-sm text-white leading-relaxed text-center">Copy this into your CLI or IDE. It tells the agent what to open and what to do.</p>' +
                    '</div>' +
                    '<textarea id="wf-harness-paste-block" readonly rows="9" class="w-full flex-1 min-h-[16rem] px-3 py-2 bg-[#152054] border border-white/20 rounded text-gray-200 text-xs font-mono">' + esc(paste) + '</textarea>' +
                '</div>';
            var footerActions = document.getElementById("wf-harness-footer-actions");
            if (footerActions) {
                footerActions.innerHTML =
                    '<button type="button" id="wf-harness-copy-full" class="px-2.5 py-1 rounded bg-[#f97316] text-white text-xs hover:bg-[#ea580c]">Copy text</button>' +
                    (entry ? '<button type="button" id="wf-harness-copy-entry" class="px-2.5 py-1 rounded border border-white/20 text-gray-200 text-xs hover:bg-white/10">Copy agent file path</button>' : '');
            }
            var tabs = body.querySelectorAll(".wf-harness-tab");
            var panels = body.querySelectorAll("[data-harness-panel]");
            function setHarnessTab(tabId) {
                tabs.forEach(function (btn) {
                    var isActive = btn.getAttribute("data-harness-tab") === tabId;
                    btn.setAttribute("aria-selected", isActive ? "true" : "false");
                    btn.classList.toggle("text-white", isActive);
                    btn.classList.toggle("bg-white/5", isActive);
                    btn.classList.toggle("border-[#f97316]", isActive);
                    btn.classList.toggle("text-gray-300", !isActive);
                });
                panels.forEach(function (panel) {
                    panel.classList.toggle("hidden", panel.getAttribute("data-harness-panel") !== tabId);
                });
            }
            setHarnessTab("instructions");
            tabs.forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var next = btn.getAttribute("data-harness-tab");
                    if (next) setHarnessTab(next);
                });
            });
            var copyFull = document.getElementById("wf-harness-copy-full");
            if (copyFull) copyFull.addEventListener("click", function () { copyWorkflowHarnessText(paste, "Handle"); });
            var copyPickup = document.getElementById("wf-harness-copy-pickup");
            if (copyPickup) copyPickup.addEventListener("click", function () { copyWorkflowHarnessText(pickup, "Pickup prompt"); });
            var copyEntry = document.getElementById("wf-harness-copy-entry");
            if (copyEntry) copyEntry.addEventListener("click", function () { copyWorkflowHarnessText(entry, "Entry path"); });
            var copyContract = document.getElementById("wf-harness-copy-contract");
            if (copyContract) copyContract.addEventListener("click", function () { copyWorkflowHarnessText(contract, "Return contract"); });
        }).catch(function (e) {
            var body = document.getElementById("wf-handoff-body-scroll");
            if (body) body.innerHTML = '<p class="text-sm text-red-400">' + esc(e.message || "Failed to load harness handoff") + '</p>';
        });
    }

    function syncWorkflowHarnessHandoffButton() {
        var btn = document.getElementById("wf-harness-handoff-btn");
        if (!btn) return;
        if (currentWorkflowId) {
            btn.classList.remove("hidden");
        } else {
            btn.classList.add("hidden");
        }
    }

    function renderBlueprintAdherencePanel(blueprint) {
        blueprint = blueprint && typeof blueprint === "object" ? blueprint : {};
        var power = blueprint.power_budget || {};
        var interrupt = blueprint.interrupt_line || {};
        var drift = blueprint.drift || {};
        var versionPin = blueprint.version_pin || {};
        var reviewModes = Array.isArray(blueprint.review_modes) ? blueprint.review_modes.filter(Boolean) : [];
        var turns = (power.turns_used || 0) + "/" + (power.max_turns || 0);
        var tokens = (power.tokens_used || 0) + "/" + (power.max_tokens || 0);
        var cost = typeof power.estimated_cost_usd === "number" ? power.estimated_cost_usd.toFixed(4) : "0.0000";
        var interruptText = interrupt.question || interrupt.reason || "";
        return '<div class="mt-3 rounded border border-sky-500/25 bg-sky-500/5 p-3">' +
            '<p class="text-xs text-sky-100 font-medium">Blueprint adherence</p>' +
            '<p class="mt-1 text-[11px] text-gray-300">Pattern: <span class="font-mono">' + esc(blueprint.orchestration_strategy || "single") + '</span>' +
                (reviewModes.length ? ' · review: <span class="font-mono">' + esc(reviewModes.join(", ")) + '</span>' : '') +
                '</p>' +
            '<p class="mt-1 text-[11px] text-gray-400">Budget: ' + esc(turns) + ' turns · ' + esc(tokens) + ' tokens · ~$' + esc(cost) +
                (power.exhausted ? ' · <span class="text-amber-300">exhausted</span>' : '') + '</p>' +
            '<p class="mt-1 text-[11px] text-gray-400">Drift: takeovers ' + esc(drift.human_takeovers || 0) +
                (drift.task_success === true ? ' · success' : (drift.task_success === false ? ' · failed' : '')) +
                ' · cost/task ~$' + esc(typeof drift.cost_per_task_usd === "number" ? drift.cost_per_task_usd.toFixed(4) : cost) + '</p>' +
            (interruptText ? '<p class="mt-1 text-[11px] text-amber-200">Interrupt: ' + esc(String(interruptText).slice(0, 220)) +
                (interrupt.recommendation ? ' · Recommend: ' + esc(String(interrupt.recommendation).slice(0, 120)) : '') + '</p>' : '') +
            '<p class="mt-1 text-[11px] text-gray-500">Memory notes: ' + esc(blueprint.memory_notes_written || 0) +
                (versionPin.manifest_hash ? ' · pin ' + esc(versionPin.manifest_hash) : '') +
                (versionPin.tool_bay_version ? ' · tools v' + esc(versionPin.tool_bay_version) : '') + '</p>' +
        '</div>';
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
        var providerCandidates = Array.isArray(run.provider_free_candidates) ? run.provider_free_candidates : [];
        var runtimeHtml = "";
        if (run.project_id) {
            runtimeHtml = '<p class="text-[11px] text-gray-500 mt-2">Project #' + esc(run.project_id) + (run.project_name ? " · " + esc(run.project_name) : "") + '</p>';
        }
        var approvalHtml = "";
        var humanDecision = run.approval_decision && run.approval_decision.title ? run.approval_decision : null;
        if (run.status === "waiting" && humanDecision) {
            approvalHtml = '<div class="mt-3">' + renderApprovalDecisionCard(humanDecision, {
                showActions: true,
                workflowId: run.workflow_id,
                runId: run.id
            }) + '</div>';
        }
        var permKind = (run.waiting_kind || "").toLowerCase();
        var needsPermission = !humanDecision && run.status === "waiting" && (
            permKind.indexOf("approval") >= 0 ||
            permKind === "ide_handoff" ||
            (run.current_step_tools || []).some(function (t) {
                return t === "playwright" || t === "browser_use" || t === "computer_use" || t === "cli";
            })
        );
        if (needsPermission) {
            approvalHtml = '<div class="mt-3 rounded border border-purple-500/30 bg-purple-500/10 p-3">' +
                '<p class="text-xs text-purple-200 font-medium">Harness permission</p>' +
                '<p class="mt-1 text-[11px] text-gray-300">This step is waiting on your harness or tool approval. Open the <strong class="text-gray-200">agent handoff</strong> or steer below.</p>' +
                '<div class="mt-2 flex flex-wrap gap-2">' +
                    '<button type="button" class="wf-run-open-harness px-2 py-1 rounded border border-white/20 text-gray-200 text-xs hover:bg-white/10">Open harness handoff</button>' +
                '</div>' +
            '</div>';
        }
        if (hasPendingRoute && run.waiting_kind === "provider_preflight") {
            var candidateButtons = providerCandidates.map(function (candidate, index) {
                if (candidate && candidate.readiness_failed) return "";
                var name = (candidate && (candidate.name || candidate.model)) || ("Option " + (index + 1));
                return '<button type="button" class="wf-provider-model-select px-2 py-1 rounded border border-amber-400/40 bg-amber-500/10 text-amber-100 text-xs hover:bg-amber-500/20" data-candidate-index="' + index + '" data-run-id="' + esc(run.id) + '" data-workflow-id="' + esc(run.workflow_id) + '">' + esc(name) + '</button>';
            }).join("");
            approvalHtml = '<div class="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-3">' +
                '<p class="text-xs text-amber-200 font-medium">Choose the retry model</p>' +
                '<p class="mt-1 text-[11px] text-gray-300">The selected model is readiness-checked before this step retries. No work is sent if readiness fails.</p>' +
                (run.waiting_prompt ? '<p class="mt-2 text-[11px] text-gray-400">' + esc(run.waiting_prompt) + '</p>' : '') +
                '<div class="mt-2 flex flex-wrap gap-2">' + candidateButtons + '</div>' +
            '</div>';
        } else if (hasPendingRoute && run.waiting_kind === "route_approval") {
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
        var blueprintHtml = renderBlueprintAdherencePanel(run.blueprint || {});
        el.innerHTML = '<div class="flex items-start justify-between gap-3 mb-2">' +
                '<div><p class="text-sm font-semibold text-white">Run command center</p>' +
                '<p class="text-xs text-gray-400">Run #' + esc(run.id) + ' · ' + esc(run.status || "running") +
                (run.waiting_kind ? ' · <span class="text-amber-300">blocked: ' + esc(run.waiting_kind) + '</span>' : '') +
                '</p></div>' +
                '<div class="flex items-center gap-2 flex-shrink-0">' +
                    '<button type="button" class="wf-open-steering-memory px-2 py-1 rounded border border-white/20 text-gray-300 text-xs hover:bg-white/10" data-run-id="' + esc(run.id) + '">View memory</button>' +
                    (run.ide_handoff_pending ? '<span class="rounded bg-amber-500/15 px-2 py-1 text-[11px] text-amber-200">IDE handoff pending</span>' : '') +
                '</div>' +
            '</div>' +
            renderRouteCard(route, {
                pendingApproval: hasPendingRoute,
                skills: workflowCleanStringList(run.current_step_skills).concat(workflowCleanStringList(route.skills)),
                tools: workflowCleanStringList(run.current_step_tools)
                    .concat(workflowCleanStringList(route.tools))
                    .concat(workflowCleanStringList(run.recent_step_tools)),
                context: workflowCleanStringList(run.current_step_context)
                    .concat(workflowCleanStringList(route.context))
                    .concat(workflowCleanStringList(run.recent_step_context))
            }) +
            blueprintHtml +
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
        el.querySelectorAll(".wf-provider-model-select").forEach(function (btn) {
            btn.addEventListener("click", function () {
                submitProviderModelSelection(
                    btn.dataset.workflowId,
                    btn.dataset.runId,
                    Number(btn.dataset.candidateIndex || 0)
                );
            });
        });
        el.querySelectorAll(".wf-run-steer-send").forEach(function (btn) {
            btn.addEventListener("click", function () {
                submitHarnessSteer(btn.dataset.workflowId, btn.dataset.runId);
            });
        });
        el.querySelectorAll(".wf-open-steering-memory").forEach(function (btn) {
            btn.addEventListener("click", function () {
                workflowMemoryRunId = btn.dataset.runId || null;
                switchTab("runs");
                switchRunsSubtab("memory");
            });
        });
        el.querySelectorAll(".wf-run-open-harness").forEach(function (btn) {
            btn.addEventListener("click", function () {
                if (typeof openWorkflowHarnessModal === "function") openWorkflowHarnessModal();
            });
        });
        bindApprovalDecisionButtons(el);
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
            loadOrchestratorTimeline({ quiet: true });
            if (workflowRunsSubtab === "memory") loadWorkflowSteeringMemory({ quiet: true });
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
            loadOrchestratorTimeline({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to update route approval", "error");
        });
    }

    function submitProviderModelSelection(workflowId, runId, candidateIndex) {
        if (!workflowId || !runId) return;
        api("POST", "/workflows/" + encodeURIComponent(workflowId) + "/runs/" + encodeURIComponent(runId) + "/provider-model-selection", {
            candidate_index: Number(candidateIndex || 0)
        }).then(function (resp) {
            snack(resp.status === "waiting" ? "That model was unavailable; choose the next recommendation" : "Model ready; retrying this step", resp.status === "waiting" ? "error" : "success");
            loadActiveRuns();
            if (currentWorkflowId) loadDetail(currentWorkflowId);
            loadOrchestratorTimeline({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to readiness-check that model", "error");
        });
    }

    function workflowQueueTicketLocked(ticketId) {
        if (!ticketId) return false;
        return !!(activeRunByTicketId(ticketId) || activeExecutionSessionByTicketId(ticketId));
    }

    function workflowQueueTicketTitle(ticketId) {
        var ticket = workflowQueueTicketById(ticketId);
        return ticket ? (ticket.title || ("Ticket #" + ticket.id)) : ("Ticket #" + ticketId);
    }

    function copyWorkflowQueueTicket(ticketId) {
        var ticket = workflowQueueTicketById(ticketId);
        if (!ticket) {
            snack("Could not find ticket to copy", "error");
            return;
        }
        var ticketUi = ensureWorkflowTicketUi();
        if (ticketUi && typeof ticketUi.copyTicketToClipboard === "function") {
            ticketUi.copyTicketToClipboard(ticket);
            return;
        }
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
            snack("Clipboard is unavailable in this browser", "error");
            return;
        }
        var text = (ticket.title || "").trim();
        var description = stripHtml(ticket.description || "").trim();
        if (description) text += (text ? "\n\n" : "") + description;
        navigator.clipboard.writeText(text || "(empty ticket)")
            .then(function () { snack("Copied title and description", "success"); })
            .catch(function (e) { snack("Copy failed: " + (e && e.message ? e.message : String(e)), "error"); });
    }

    function sendWorkflowQueueTicketToOrchestrator(ticketId, btnEl) {
        var ticket = workflowQueueTicketById(ticketId);
        if (!ticket) {
            snack("Could not find ticket to send to orchestrator", "error");
            return;
        }
        if (btnEl) btnEl.disabled = true;
        startWorkflowTicketDiscussion(ticket, true);
        setTimeout(function () {
            if (btnEl) btnEl.disabled = false;
        }, 1000);
    }

    function syncWorkflowQueueSelectionUi() {
        document.querySelectorAll(".wf-workflow-ticket-row").forEach(function (row) {
            var selected = selectedWorkflowQueueTicketId &&
                String(row.dataset.ticketId) === String(selectedWorkflowQueueTicketId);
            row.classList.toggle("is-selected", !!selected);
            row.setAttribute("aria-selected", selected ? "true" : "false");
        });
        syncWorkflowQueueActionButtons();
        renderLoopRunTicketContext();
    }

    function syncWorkflowQueueActionButtons() {
        var runAll = document.getElementById("wf-run-all-btn");
        var queue = workflowQueueTicketsForSelectedBoard(workflowQueueTickets || []);
        var runnable = queue.filter(function (t) { return !activeRunByTicketId(t.id); });
        if (runAll) {
            runAll.disabled = !(currentWorkflowId && runnable.length && !workflowHasActiveRuns());
        }
    }

    function runAllWorkflowQueueTickets() {
        if (!currentWorkflowId || workflowHasActiveRuns()) {
            snack("A run is already active", "error");
            return;
        }
        var queue = workflowQueueTicketsForSelectedBoard(workflowQueueTickets || [])
            .filter(function (t) { return !activeRunByTicketId(t.id); });
        if (!queue.length) {
            snack("No runnable tickets in the queue", "error");
            return;
        }
        var first = queue[0];
        snack("Running queue (" + queue.length + " tickets). First: " + (first.title || ("#" + first.id)), "info");
        openWorkflowRunPreview(first.id, queue.map(function (ticket) { return String(ticket.id); }));
    }

    function filterRunsForSelectedTicket(runs) {
        if (!workflowRunsFilterTicketId) return runs;
        return (runs || []).filter(function (r) {
            return String(r.ticket_id) === String(workflowRunsFilterTicketId);
        });
    }

    function focusWorkflowRun(runId, options) {
        options = options || {};
        if (!runId || !currentWorkflowId) return;
        loopFeedRunId = runId;
        workflowMemoryRunId = runId;
        if (options.ticketId) workflowRunsFilterTicketId = String(options.ticketId);
        if (options.switchTab === "loop") switchTab("loop", { persist: true });
        else if (options.switchTab === "runs") {
            switchTab("runs", { persist: true });
            switchRunsSubtab("history");
        }
        syncLoopFeedRunSelect();
        loadLoopActivityFeed({ quiet: false });
        renderLoopRunTicketContext();
    }

    function workflowLoopHasSelectedTicket() {
        return !!selectedWorkflowQueueTicketId;
    }

    function syncWorkflowLoopTabAvailability() {
        var hasTicketContext = workflowLoopHasSelectedTicket();
        document.querySelectorAll('.wf-tab[data-tab="loop"]').forEach(function (btn) {
            btn.disabled = false;
            btn.classList.remove("opacity-50", "cursor-not-allowed");
            btn.title = hasTicketContext
                ? "Design this loop and view the selected ticket's activity"
                : "Design the workflow loop";
        });
    }

    function selectWorkflowQueueTicket(ticketId, rowEl) {
        selectedWorkflowQueueTicketId = ticketId ? String(ticketId) : null;
        workflowRunsFilterTicketId = selectedWorkflowQueueTicketId;
        if (!selectedWorkflowQueueTicketId) {
            loopFeedRunId = null;
            workflowMemoryRunId = null;
        }
        syncWorkflowQueueSelectionUi();
        syncWorkflowLoopTabAvailability();
        syncLoopFeedRunSelect();
        renderLoopRunTicketContext();
        if (rowEl && typeof rowEl.focus === "function") rowEl.focus();
    }

    function ensureWorkflowQueueSelection(tickets) {
        tickets = Array.isArray(tickets) ? tickets : [];
        var visibleTickets = workflowQueueTicketsForSelectedBoard(tickets);
        if (!visibleTickets.length) {
            selectWorkflowQueueTicket(null);
            return;
        }
        var stillVisible = selectedWorkflowQueueTicketId && visibleTickets.some(function (ticket) {
            return String(ticket.id) === String(selectedWorkflowQueueTicketId);
        });
        if (!stillVisible) {
            // On refresh/reconnect, recover the ticket that is actually running
            // before falling back to the first queue row. Otherwise Mission
            // Control can label one ticket while showing another ticket's run.
            var activeRun = currentWorkflowActiveRun();
            var activeTicket = activeRun && activeRun.ticket_id && visibleTickets.filter(function (ticket) {
                return String(ticket.id) === String(activeRun.ticket_id);
            })[0];
            selectWorkflowQueueTicket(String(activeTicket ? activeTicket.id : visibleTickets[0].id));
            return;
        }
        syncWorkflowQueueSelectionUi();
    }

    function isWorkflowQueueKeyboardContext() {
        var ticketsTab = document.getElementById("wf-tab-tickets");
        if (!ticketsTab || ticketsTab.classList.contains("hidden")) return false;
        var modal = document.getElementById("kb-ticket-modal");
        if (modal && !modal.classList.contains("hidden")) return false;
        var confirmModal = document.getElementById("decisions-confirm-modal");
        if (confirmModal) return false;
        return true;
    }

    function removeWorkflowQueueTicket(ticketId, options) {
        options = options || {};
        if (!ticketId || !currentWorkflowId) return;
        if (workflowQueueTicketLocked(ticketId)) {
            snack("Cannot remove a ticket while it is running", "error");
            return;
        }
        var title = workflowQueueTicketTitle(ticketId);
        function performRemove() {
            var removedTicket = workflowQueueTicketById(ticketId);
            var externalKey = workflowExternalLinkKeyFromTicketRecord(removedTicket);
            api("DELETE", "/tickets/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets/" + encodeURIComponent(ticketId))
                .then(function () {
                    snack("Ticket removed from workflow queue");
                    workflowQueueTickets = workflowQueueTickets.filter(function (item) {
                        return String(item.id) !== String(ticketId);
                    });
                    if (selectedWorkflowQueueTicketId && String(selectedWorkflowQueueTicketId) === String(ticketId)) {
                        selectedWorkflowQueueTicketId = null;
                    }
                    renderWorkflowTickets(workflowQueueTickets);
                    restoreWorkflowBoardTicketAfterQueueRemove(ticketId, externalKey);
                })
                .catch(function (e) { snack(e.message || "Failed to remove ticket", "error"); });
        }
        if (options.skipConfirm) {
            performRemove();
            return;
        }
        showConfirmModal({
            title: "Remove from workflow",
            message: 'Remove "' + title + '" from this workflow queue?\n\nThe ticket stays on its board but is no longer linked to this workflow.',
            confirmLabel: "Remove",
            onConfirm: performRemove
        });
    }

    function createWorkflowQueueListRow(ticket, queueId, options) {
        options = options || {};
        var run = activeRunByTicketId(ticket.id);
        var executionSession = activeExecutionSessionByTicketId(ticket.id);
        var locked = !!(run || executionSession);
        var loopLocked = workflowHasActiveRuns();
        var canReorder = !options.static && !locked && !loopLocked;
        var status = run ? (run.status || "running") : (executionSession ? (executionSession.status || "running") : (ticket.workflow_status || "queued"));
        var statusLabel = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Queued";
        var timeLabel = workflowQueueTicketTimeLabel(ticket);
        var timeLive = workflowQueueTicketTimeIsLive(ticket);
        var elapsedSeconds = workflowQueueTicketDisplaySeconds(ticket);
        var showTime = timeLive || elapsedSeconds > 0;
        var timeClass = timeLive
            ? "bg-sky-500/20 text-sky-200"
            : "bg-white/10 text-gray-300";
        var title = ticket.title || ("Ticket #" + ticket.id);
        var cleanDesc = stripHtml(ticket.description || "").replace(/\s+/g, " ").trim();
        var badgeOpts = { interactive: true, ticketId: ticket.id, locked: locked || loopLocked };
        var descHtml = cleanDesc
            ? '<div class="kb-ticket-list-desc" tabindex="0" title="' + esc(cleanDesc) + '"><div class="kb-ticket-list-desc-track"><span>' + esc(cleanDesc) + "</span></div></div>"
            : "";
        var contentClass = "kb-ticket-list-content" + (cleanDesc ? "" : " kb-ticket-list-content--no-desc");
        var titleHtml = '<div class="kb-ticket-list-title-wrap" title="' + esc(title) + '">' + esc(title) + "</div>";
        var row = document.createElement("div");
        row.className = "kb-ticket-list-row wf-workflow-ticket-row" + (run ? " wf-workflow-ticket-row--running" : "");
        if (options.static) row.className += " wf-workflow-ticket-row--static";
        row.dataset.ticketId = String(ticket.id);
        row.tabIndex = 0;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", selectedWorkflowQueueTicketId && String(selectedWorkflowQueueTicketId) === String(ticket.id) ? "true" : "false");
        if (selectedWorkflowQueueTicketId && String(selectedWorkflowQueueTicketId) === String(ticket.id)) {
            row.classList.add("is-selected");
        }
        row.innerHTML =
            '<div class="kb-ticket-list-prefix">' +
                workflowListDragHandleHtml(canReorder, options.static ? "Selected ticket" : (loopLocked ? "Loop is running — reorder locked" : "Drag to reorder queue")) +
                '<span class="kb-ticket-list-badges wf-workflow-queue-badges">' +
                    (options.static ? "" : '<span class="wf-queue-position-label" title="Queue position">' + esc(String(queueId) + ".") + "</span>") +
                    workflowPriorityBadgeHtml(ticket.priority, badgeOpts) +
                    workflowComplexityBadgeHtml(ticket.complexity, badgeOpts) +
                "</span>" +
            "</div>" +
            '<div class="' + contentClass + '">' +
                titleHtml +
                descHtml +
            "</div>" +
            '<div class="kb-ticket-list-actions wf-workflow-queue-actions">' +
                '<span class="wf-ticket-status-time inline-flex h-6 items-center justify-center whitespace-nowrap text-[9px] px-1 rounded tabular-nums leading-none ' + executionSessionStatusClass(status) + '" title="Workflow status' + (showTime ? " and time spent" : "") + '">' +
                    '<span class="wf-ticket-status-label">' + esc(statusLabel) + "</span>" +
                    (showTime
                        ? '<span class="wf-ticket-status-time-divider" aria-hidden="true">·</span><span class="wf-ticket-time-display ' + timeClass + '" data-ticket-id="' + esc(ticket.id) + '">' + esc(timeLabel) + "</span>"
                        : "") +
                "</span>" +
                '<button type="button" class="wf-workflow-ticket-copy kb-card-action-btn kb-act-copy" data-ticket-id="' + esc(ticket.id) + '" title="Copy title and description" aria-label="Copy title and description">' +
                    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>' +
                "</button>" +
                '<button type="button" class="wf-workflow-ticket-orchestrator kb-card-action-btn kb-act-agent" data-ticket-id="' + esc(ticket.id) + '" title="Send to Orchestrator" aria-label="Send to Orchestrator">' +
                    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4"/><path d="M12 18v4"/><rect x="4" y="6" width="16" height="12" rx="3"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><path d="M9 15h6"/></svg>' +
                "</button>" +
                '<button type="button" class="wf-workflow-ticket-loop kb-card-action-btn kb-act-loop" data-ticket-id="' + esc(ticket.id) + '" title="Open this ticket in the loop tab" aria-label="Open ticket loop">' + SVG_LOOP + "</button>" +
                (!run ? '<button type="button" class="wf-workflow-ticket-run kb-card-action-btn kb-act-play" data-ticket-id="' + esc(ticket.id) + '" title="Run ticket through this workflow" aria-label="Run ticket">' + SVG_PLAY + "</button>" : "") +
                (locked ? "" : '<button type="button" class="wf-workflow-ticket-remove kb-card-action-btn kb-act-delete" data-ticket-id="' + esc(ticket.id) + '" title="Remove from workflow" aria-label="Remove from workflow">' + SVG_TRASH + "</button>") +
            "</div>";
        if (canReorder) {
            attachWorkflowQueueTicketDrag(row);
        }
        return row;
    }

    function initWorkflowQueueDescMarquees(rootEl) {
        (rootEl || document).querySelectorAll(".wf-workflow-queue-list .kb-ticket-list-desc").forEach(function (container) {
            if (!container || container.dataset.marqueeInit === "done") return;
            var track = container.querySelector(".kb-ticket-list-desc-track");
            if (!track) {
                container.dataset.marqueeInit = "done";
                return;
            }
            var first = track.querySelector("span");
            if (!first) return;
            var text = (first.textContent || "").trim();
            if (!text) {
                container.dataset.marqueeInit = "done";
                return;
            }
            requestAnimationFrame(function () {
                if (container.dataset.marqueeInit === "done") return;
                if (track.scrollWidth <= container.clientWidth + 1) {
                    container.dataset.marqueeInit = "done";
                    return;
                }
                container.dataset.marqueeInit = "done";
                container.classList.add("kb-ticket-list-desc--marquee");
                var clone = first.cloneNode(true);
                clone.setAttribute("aria-hidden", "true");
                track.appendChild(clone);
                var duration = Math.max(10, Math.round(track.scrollWidth / 40));
                track.style.setProperty("--kb-marquee-duration", duration + "s");
            });
        });
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
            listEl.innerHTML = '<div class="wf-workflow-drop-hint h-full min-h-[300px] flex items-center justify-center text-center text-sm text-gray-500 pointer-events-none" data-hint-default="Drop a ticket here to add it to this workflow queue." data-hint-drop="Drop here">Drop a ticket here to add it to this workflow queue.</div>';
            emptyEl.classList.remove("hidden");
            listEl.classList.add("border-dashed");
            selectWorkflowQueueTicket(null);
            bindWorkflowTicketDropZone();
            renderWorkflowBoardTimeTotal();
            refreshWorkflowCliTabIfVisible();
            return;
        }
        var visibleTickets = workflowQueueTicketsForSelectedBoard(tickets);
        if (!visibleTickets.length) {
            listEl.innerHTML = '<div class="wf-workflow-drop-hint h-full min-h-[300px] flex items-center justify-center text-center text-sm text-gray-500 pointer-events-none" data-hint-default="No tickets from this board are in the workflow queue. Drop a ticket from the board list to add one." data-hint-drop="Drop here">No tickets from this board are in the workflow queue. Drop a ticket from the board list to add one.</div>';
            emptyEl.classList.remove("hidden");
            listEl.classList.add("border-dashed");
            selectWorkflowQueueTicket(null);
            bindWorkflowTicketDropZone();
            renderWorkflowBoardTimeTotal();
            refreshWorkflowCliTabIfVisible();
            return;
        }
        emptyEl.classList.add("hidden");
        listEl.classList.remove("border-dashed");
        var listWrap = document.createElement("div");
        listWrap.className = "wf-workflow-queue-list space-y-1";
        visibleTickets.forEach(function (ticket, idx) {
            listWrap.appendChild(createWorkflowQueueListRow(ticket, idx + 1));
        });
        listEl.innerHTML = "";
        listEl.appendChild(listWrap);
        bindWorkflowTicketQueueRows(listEl);
        bindWorkflowQueueMetricBadges(listEl);
        bindWorkflowTicketDropZone();
        ensureWorkflowQueueSelection(tickets);
        requestAnimationFrame(function () {
            initWorkflowQueueDescMarquees(listWrap);
            ensureWorkflowTicketTimerTick();
        });
        renderWorkflowBoardTimeTotal();
        refreshWorkflowCliTabIfVisible();
    }

    function openWorkflowTicketLoop(ticketId, rowEl) {
        if (!ticketId) return;
        selectWorkflowQueueTicket(ticketId, rowEl || null);
        var run = activeRunByTicketId(ticketId) || runRecordById(loopFeedRunId);
        if (run && String(run.ticket_id || "") === String(ticketId)) {
            focusWorkflowRun(run.id, { ticketId: ticketId, switchTab: "loop" });
            return;
        }
        switchTab("loop", { persist: true });
        renderLoopRunTicketContext();
    }

    function normalizeComplexity(value) {
        value = String(value || "medium").toLowerCase();
        return value === "low" || value === "high" ? value : "medium";
    }

    function refreshWorkflowQueueTicketMetricBadge(ticketId, metric) {
        var ticket = workflowQueueTickets.filter(function (item) { return String(item.id) === String(ticketId); })[0];
        if (!ticket) return;
        var row = document.querySelector('.wf-workflow-ticket-row[data-ticket-id="' + ticketId + '"]');
        if (!row) return;
        var badge = row.querySelector('.wf-queue-metric-badge[data-metric="' + metric + '"]');
        if (!badge) return;
        var locked = !!(activeRunByTicketId(ticket.id) || activeExecutionSessionByTicketId(ticket.id));
        var html = metric === "priority"
            ? workflowPriorityBadgeHtml(ticket.priority, { interactive: true, ticketId: ticket.id, locked: locked })
            : workflowComplexityBadgeHtml(ticket.complexity, { interactive: true, ticketId: ticket.id, locked: locked });
        var wrap = document.createElement("div");
        wrap.innerHTML = html;
        var nextBadge = wrap.firstChild;
        if (!nextBadge) return;
        badge.replaceWith(nextBadge);
        bindWorkflowQueueMetricBadges(row);
    }

    function workflowBoardListBadgesHtml(ticket) {
        return workflowComplexityBadgeHtml(ticket.complexity, {}) + workflowPriorityBadgeHtml(ticket.priority, {});
    }

    function updateWorkflowBoardRenderStateTicket(ticketId, patch) {
        var state = workflowBoardRenderState;
        if (!state || !state.data || !Array.isArray(state.data.lanes)) return;
        state.data.lanes.forEach(function (lane) {
            (lane.tickets || []).forEach(function (ticket) {
                if (String(ticket.id) !== String(ticketId)) return;
                Object.keys(patch || {}).forEach(function (field) {
                    ticket[field] = patch[field];
                });
            });
        });
    }

    function reorderWorkflowBoardTicketRow(ticketKey) {
        var row = workflowBoardTicketRowForKey(ticketKey);
        if (!row) return;
        var body = row.closest(".kb-ticket-list-section-body");
        if (!body) return;
        var rows = Array.prototype.slice.call(body.querySelectorAll(".wf-board-ticket-row"));
        if (rows.length < 2) return;
        var entries = rows.map(function (entryRow) {
            var key = entryRow.dataset.ticketKey || "";
            var item = workflowBoardTicketByKey[key];
            return { key: key, row: entryRow, ticket: item && item.ticket };
        }).filter(function (entry) { return entry.ticket; });
        entries.sort(function (a, b) { return workflowBoardExecutionOrderCompare(a.ticket, b.ticket); });
        entries.forEach(function (entry) { body.appendChild(entry.row); });
    }

    function syncWorkflowBoardTicketView(ticketId, patch) {
        patch = patch || {};
        var fields = Object.keys(patch);
        if (!fields.length) return;
        var reorderKeys = {};
        Object.keys(workflowBoardTicketByKey || {}).forEach(function (ticketKey) {
            var item = workflowBoardTicketByKey[ticketKey];
            if (!item || !item.ticket || String(item.ticket.id) !== String(ticketId)) return;
            fields.forEach(function (field) {
                item.ticket[field] = patch[field];
            });
            var row = workflowBoardTicketRowForKey(ticketKey);
            if (!row) return;
            if (patch.title != null) {
                var titleEl = row.querySelector(".kb-ticket-list-title");
                if (titleEl) titleEl.textContent = patch.title || "";
            }
            if (patch.priority != null || patch.complexity != null) {
                var badges = row.querySelector(".kb-ticket-list-badges");
                if (badges) badges.innerHTML = workflowBoardListBadgesHtml(item.ticket);
            }
            if (patch.priority != null || patch.complexity != null) {
                reorderKeys[ticketKey] = true;
            }
        });
        updateWorkflowBoardRenderStateTicket(ticketId, patch);
        Object.keys(reorderKeys).forEach(function (ticketKey) {
            reorderWorkflowBoardTicketRow(ticketKey);
        });
    }

    function syncWorkflowQueueTicketView(ticketId, patch) {
        var ticket = workflowQueueTickets.filter(function (item) { return String(item.id) === String(ticketId); })[0];
        if (!ticket) return false;
        Object.keys(patch || {}).forEach(function (field) {
            ticket[field] = patch[field];
        });
        if (patch.priority != null) refreshWorkflowQueueTicketMetricBadge(ticketId, "priority");
        if (patch.complexity != null) refreshWorkflowQueueTicketMetricBadge(ticketId, "complexity");
        if (patch.title != null) {
            var row = document.querySelector('.wf-workflow-ticket-row[data-ticket-id="' + ticketId + '"]');
            if (row) {
                var titleEl = row.querySelector(".kb-ticket-list-title");
                if (titleEl) titleEl.textContent = patch.title || "";
            }
        }
        return true;
    }

    function closeWorkflowQueueMetricMenu() {
        if (wfQueueMetricMenuEl) wfQueueMetricMenuEl.classList.add("hidden");
        wfQueueMetricMenuState = null;
    }

    function workflowQueueMetricMenuOptions(metric) {
        if (metric === "priority") {
            return ["low", "medium", "high", "critical"].map(function (value) {
                return { value: value, label: value };
            });
        }
        return ["low", "medium", "high"].map(function (value) {
            return { value: value, label: workflowComplexityNumeral(value) };
        });
    }

    function ensureWorkflowQueueMetricMenu() {
        if (wfQueueMetricMenuEl) return wfQueueMetricMenuEl;
        var html = '<div id="wf-queue-metric-menu" class="hidden fixed z-[10000] min-w-[132px] bg-[#1a1f3a] border border-white/20 rounded-lg shadow-2xl py-1" role="menu"></div>';
        document.body.insertAdjacentHTML("beforeend", html);
        wfQueueMetricMenuEl = document.getElementById("wf-queue-metric-menu");
        wfQueueMetricMenuEl.addEventListener("click", function (evt) { evt.stopPropagation(); });
        return wfQueueMetricMenuEl;
    }

    function openWorkflowQueueMetricMenu(badge, evt) {
        if (!badge) return;
        var ticketId = badge.dataset.ticketId || "";
        var metric = badge.dataset.metric || "";
        if (!ticketId || !metric) return;
        var ticket = workflowQueueTickets.filter(function (item) { return String(item.id) === String(ticketId); })[0];
        if (!ticket) return;
        if (activeRunByTicketId(ticket.id) || activeExecutionSessionByTicketId(ticket.id)) return;

        var menu = ensureWorkflowQueueMetricMenu();
        var currentValue = metric === "priority"
            ? normalizeWorkflowPriority(ticket.priority)
            : normalizeComplexity(ticket.complexity);
        var title = metric === "priority" ? "Priority" : "Complexity";
        var options = workflowQueueMetricMenuOptions(metric);
        menu.innerHTML = '<div class="px-3 py-1.5 text-[10px] uppercase tracking-wide text-gray-500">' + esc(title) + "</div>" +
            options.map(function (option) {
                var active = option.value === currentValue;
                var itemClass = metric === "priority"
                    ? "kb-pri-" + option.value
                    : "kb-cx-" + option.value;
                return '<button type="button" class="wf-queue-metric-menu-item w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-white/10' + (active ? " is-active" : "") + '" role="menuitemradio" aria-checked="' + (active ? "true" : "false") + '" data-value="' + esc(option.value) + '">' +
                    '<span class="wf-queue-metric-menu-badge kb-metric-badge ' + itemClass + '">' + esc(option.label) + "</span>" +
                    (active ? '<span class="wf-queue-metric-menu-check" aria-hidden="true">✓</span>' : "") +
                "</button>";
            }).join("");
        menu.querySelectorAll(".wf-queue-metric-menu-item").forEach(function (btn) {
            btn.addEventListener("click", function (clickEvt) {
                clickEvt.preventDefault();
                clickEvt.stopPropagation();
                var value = btn.dataset.value || "";
                closeWorkflowQueueMetricMenu();
                updateWorkflowQueueTicketMetric(ticketId, metric, value);
            });
        });

        wfQueueMetricMenuState = { ticketId: ticketId, metric: metric };
        menu.classList.remove("hidden");
        var rect = badge.getBoundingClientRect();
        var left = rect.left;
        var top = rect.bottom + 4;
        menu.style.left = left + "px";
        menu.style.top = top + "px";
        var menuRect = menu.getBoundingClientRect();
        if (menuRect.right > window.innerWidth - 8) left = Math.max(8, window.innerWidth - menuRect.width - 8);
        if (menuRect.bottom > window.innerHeight - 8) top = Math.max(8, rect.top - menuRect.height - 4);
        menu.style.left = left + "px";
        menu.style.top = top + "px";
    }

    function updateWorkflowQueueTicketMetric(ticketId, metric, value) {
        var ticket = workflowQueueTickets.filter(function (item) { return String(item.id) === String(ticketId); })[0];
        if (!ticket || !ticket.id) return;
        var payload = {};
        var next;
        if (metric === "priority") {
            next = normalizeWorkflowPriority(value);
            if (normalizeWorkflowPriority(ticket.priority) === next) return;
            payload.priority = next;
        } else {
            next = normalizeComplexity(value);
            if (normalizeComplexity(ticket.complexity) === next) return;
            payload.complexity = next;
        }
        api("PUT", "/tickets/tickets/" + encodeURIComponent(ticket.id), payload)
            .then(function () {
                var patch = metric === "priority" ? { priority: next } : { complexity: next };
                if (metric === "priority") ticket.priority = next;
                else ticket.complexity = next;
                refreshWorkflowQueueTicketMetricBadge(ticket.id, metric);
                syncWorkflowBoardTicketView(ticket.id, patch);
            })
            .catch(function (e) {
                snack(e.message || ("Failed to update ticket " + metric), "error");
                renderWorkflowTickets(workflowQueueTickets);
            });
    }

    function bindWorkflowQueueMetricBadges(root) {
        (root || document).querySelectorAll(".wf-queue-metric-badge").forEach(function (badge) {
            if (badge.dataset.metricBound === "1") return;
            badge.dataset.metricBound = "1";
            badge.addEventListener("click", function (evt) {
                evt.preventDefault();
                evt.stopPropagation();
                if (wfQueueMetricMenuState &&
                    wfQueueMetricMenuState.ticketId === (badge.dataset.ticketId || "") &&
                    wfQueueMetricMenuState.metric === (badge.dataset.metric || "")) {
                    closeWorkflowQueueMetricMenu();
                    return;
                }
                openWorkflowQueueMetricMenu(badge, evt);
            });
        });
    }

    function workflowCliTicketById(ticketId) {
        return (workflowQueueTickets || []).filter(function (ticket) {
            return String(ticket.id) === String(ticketId);
        })[0] || null;
    }

    function workflowExecutorLabel(backend) {
        var b = String(backend || "").toLowerCase();
        if (b === "cursor") return "Cursor IDE";
        if (b === "codex") return "Codex CLI";
        if (b === "claude_code") return "Claude Code CLI";
        if (b === "pi") return "Pi CLI";
        if (b === "opencode") return "OpenCode";
        if (b === "hermes_agent") return "External Agent";
        if (b === "cline") return "Cline CLI";
        if (!b || b === "auto") return "Auto";
        return backend;
    }

    function workflowRunPreviewKvRow(label, value, options) {
        options = options || {};
        var val = value || "-";
        var valueHtml;
        if (options.marquee && val.length > 42) {
            valueHtml = '<span class="wf-run-preview-marquee"><span>' + esc(val) + " · " + esc(val) + "</span></span>";
        } else {
            valueHtml = '<span class="break-words">' + esc(val) + "</span>";
        }
        return '<div class="wf-run-preview-label">' + esc(label) + '</div><div class="wf-run-preview-value">' + valueHtml + "</div>";
    }

    function workflowRunPreviewCtxFromTicket(ticket) {
        ticket = ticket || {};
        var boardData = workflowBoardRenderState.data || {};
        var projectId = ticket.linked_project_id || ticket.board_default_project_id || boardData.default_project_id || null;
        var projectName = ticket.linked_project_name
            || ticket.board_default_project_name
            || boardData.default_project_name
            || projectNameById(projectId)
            || "";
        var projectFolder = ticket.linked_project_folder
            || ticket.board_default_project_folder
            || boardData.default_project_folder
            || "";
        var route = ticket.cli_route && typeof ticket.cli_route === "object" ? ticket.cli_route : {};
        return {
            ticket_id: ticket.id,
            title: ticket.title || "",
            description: ticket.description || "",
            project_id: projectId,
            project_name: projectName,
            project_folder: projectFolder,
            complexity: ticket.complexity || "medium",
            priority: ticket.priority || "medium",
            context_notes: ticket.context_notes || "",
            backend_id: route.backend || route.backend_id || "",
            model: route.model || "auto",
            route: route,
            skills: route.skills || []
        };
    }

    function workflowRunPreviewCanStart(ticket, ctx) {
        ctx = ctx || workflowRunPreviewCtxFromTicket(ticket);
        if ((ctx.project_folder || "").trim()) return true;
        if (ctx.project_id) return true;
        return false;
    }

    function workflowRunPreviewRouteHtml(ticket, ctx) {
        ticket = ticket || {};
        ctx = ctx || {};
        var projectName = ctx.project_name || ticket.linked_project_name || projectNameById(ticket.linked_project_id) || "";
        var projectFolder = ctx.project_folder || "";
        var route = ctx.route && typeof ctx.route === "object" ? ctx.route : (ticket.cli_route || {});
        var backend = ctx.backend_id || route.backend || route.backend_id || "auto";
        var model = ctx.model || route.model || "auto";
        var complexity = ctx.complexity || ticket.complexity || "medium";
        var priority = ctx.priority || ticket.priority || "medium";
        var executor = workflowExecutorLabel(backend);
        var skills = workflowCleanStringList(ctx.skills || route.skills);
        var ticketTitle = ticket.title || ctx.title || ("Ticket #" + (ticket.id || ctx.ticket_id || ""));
        var ticketDesc = ticket.description || ctx.description || "";
        var contextNotes = ctx.context_notes || ticket.context_notes || "";

        var runPanel = '<div class="wf-run-preview-panel active" data-panel="run">' +
            '<div class="wf-run-preview-kv-row wf-run-preview-kv-row--split">' +
                workflowRunPreviewKvRow("Board", ticket.board_name || "Unknown board") +
                workflowRunPreviewKvRow("Project", projectName || "No linked project") +
            "</div>" +
            '<div class="wf-run-preview-kv-row wf-run-preview-kv-row--split">' +
                workflowRunPreviewKvRow("Complexity", complexity) +
                workflowRunPreviewKvRow("Priority", priority) +
            "</div>" +
            '<div class="wf-run-preview-kv-row wf-run-preview-kv-row--split">' +
                workflowRunPreviewKvRow("Executor", executor) +
                workflowRunPreviewKvRow("Model", model || "auto") +
            "</div>";
        if (projectFolder) {
            runPanel += '<div class="wf-run-preview-kv-row">' +
                workflowRunPreviewKvRow("Folder", projectFolder, { marquee: true }) +
            "</div>";
        }
        if (skills.length) {
            runPanel += '<div class="wf-run-preview-kv-row">' +
                workflowRunPreviewKvRow("Skills", skills.join(", ")) +
            "</div>";
        }
        runPanel += "</div>";

        var infoPanel = '<div class="wf-run-preview-panel" data-panel="info">' +
            '<div class="wf-run-preview-kv-row">' +
                workflowRunPreviewKvRow("Ticket", ticketTitle) +
            "</div>";
        if (ticketDesc) {
            infoPanel += '<div class="text-xs text-gray-400 mt-2 whitespace-pre-wrap break-words max-h-32 overflow-y-auto">' + esc(ticketDesc) + "</div>";
        }
        if (contextNotes) {
            infoPanel += '<div class="wf-run-preview-notes">' + esc(contextNotes) + "</div>";
        } else {
            infoPanel += '<p class="text-xs text-gray-500 mt-2">No orchestrator notes on this ticket yet.</p>';
        }
        infoPanel += "</div>";

        return '<div class="wf-run-preview-tabs">' +
            '<button type="button" class="wf-run-preview-tab active" data-tab="run">Run</button>' +
            '<button type="button" class="wf-run-preview-tab" data-tab="info">Info</button>' +
        "</div>" + runPanel + infoPanel;
    }

    function bindWorkflowRunPreviewTabs(root) {
        if (!root) return;
        var tabs = root.querySelectorAll(".wf-run-preview-tab");
        tabs.forEach(function (tab) {
            tab.onclick = function () {
                var name = tab.getAttribute("data-tab");
                root.querySelectorAll(".wf-run-preview-tab").forEach(function (el) {
                    el.classList.toggle("active", el.getAttribute("data-tab") === name);
                });
                root.querySelectorAll(".wf-run-preview-panel").forEach(function (panel) {
                    panel.classList.toggle("active", panel.getAttribute("data-panel") === name);
                });
            };
        });
    }

    function closeWorkflowRunPreview() {
        pendingWorkflowRunTicketId = null;
        pendingWorkflowRunTicketIds = [];
        var modal = document.getElementById("wf-run-preview-modal");
        if (modal) modal.classList.add("hidden");
    }

    function workflowRunPreviewElements() {
        return {
            modal: document.getElementById("wf-run-preview-modal"),
            subtitle: document.getElementById("wf-run-preview-subtitle"),
            body: document.getElementById("wf-run-preview-body"),
            warning: document.getElementById("wf-run-preview-warning"),
            confirmBtn: document.getElementById("wf-run-preview-confirm")
        };
    }

    function workflowRunPreviewFallback(ticketId) {
        if (pendingWorkflowRunTicketIds.length > 1) startWorkflowTicketGroup(pendingWorkflowRunTicketIds);
        else startWorkflowTicketRun(ticketId);
    }

    function setWorkflowRunPreviewWarning(elements, message) {
        if (!elements.warning) return;
        elements.warning.textContent = message || "";
        elements.warning.classList.toggle("hidden", !message);
    }

    function renderWorkflowRunPreviewContext(elements, ticket, ctx) {
        elements.body.innerHTML = workflowRunPreviewRouteHtml(ticket, ctx);
        bindWorkflowRunPreviewTabs(elements.body);
        setWorkflowRunPreviewWarning(elements, "");
        elements.confirmBtn.disabled = !workflowRunPreviewCanStart(ticket, ctx);
    }

    function renderWorkflowRunPreviewError(elements, ticket, localCtx, error) {
        if (workflowRunPreviewCanStart(ticket, localCtx)) {
            setWorkflowRunPreviewWarning(elements, "");
            elements.confirmBtn.disabled = false;
            return;
        }
        renderWorkflowRunPreviewContext(elements, ticket, localCtx);
        setWorkflowRunPreviewWarning(
            elements,
            error.message || "This ticket does not have a complete project/executor route yet. Link the board or ticket to a project before running."
        );
        elements.confirmBtn.disabled = true;
    }

    function loadWorkflowRunPreviewContext(ticketId, ticket, localCtx, elements) {
        api("GET", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/cli-context?preview=1")
            .then(function (ctx) {
                if (pendingWorkflowRunTicketId === String(ticketId)) renderWorkflowRunPreviewContext(elements, ticket, ctx);
            })
            .catch(function (error) {
                if (pendingWorkflowRunTicketId === String(ticketId)) renderWorkflowRunPreviewError(elements, ticket, localCtx, error);
            });
    }

    function workflowRunPreviewTicket(ticketId) {
        return workflowCliTicketById(ticketId) || workflowQueueTickets.filter(function (item) {
            return String(item.id) === String(ticketId);
        })[0];
    }

    function workflowCanBeginRunPreview(ticketId) {
        return Boolean(currentWorkflowId && ticketId);
    }

    function workflowRunPreviewTicketIds(ticketId, ticketIds) {
        return Array.isArray(ticketIds) ? ticketIds.map(String) : [String(ticketId)];
    }

    function beginWorkflowRunPreview(ticketId, ticketIds) {
        if (!workflowCanBeginRunPreview(ticketId)) return;
        var ticket = workflowRunPreviewTicket(ticketId);
        if (activeRunByTicketId(ticketId)) {
            snack("This ticket already has an active workflow run", "error");
            return;
        }
        pendingWorkflowRunTicketId = String(ticketId);
        pendingWorkflowRunTicketIds = workflowRunPreviewTicketIds(ticketId, ticketIds);
        return { ticket: ticket };
    }

    function workflowRunPreviewElementsReady(elements) {
        return Boolean(elements.modal && elements.body && elements.confirmBtn);
    }

    function workflowRunPreviewTicketTitle(ticket, ticketId) {
        if (ticket && ticket.title) return ticket.title;
        return "Ticket #" + ticketId;
    }

    function workflowRunPreviewCountSuffix(count) {
        if (!count) return "";
        return " + " + count + " more ticket" + (count === 1 ? "" : "s");
    }

    function setWorkflowRunPreviewSubtitle(element, ticket, ticketId) {
        if (!element) return;
        var additionalTickets = Math.max(0, pendingWorkflowRunTicketIds.length - 1);
        element.textContent = workflowRunPreviewTicketTitle(ticket, ticketId) + workflowRunPreviewCountSuffix(additionalTickets);
    }

    function openWorkflowRunPreview(ticketId, ticketIds) {
        var state = beginWorkflowRunPreview(ticketId, ticketIds);
        if (!state) return;
        var ticket = state.ticket;
        var elements = workflowRunPreviewElements();
        if (!workflowRunPreviewElementsReady(elements)) {
            workflowRunPreviewFallback(ticketId);
            return;
        }
        setWorkflowRunPreviewSubtitle(elements.subtitle, ticket, ticketId);
        var localCtx = workflowRunPreviewCtxFromTicket(ticket);
        renderWorkflowRunPreviewContext(elements, ticket, localCtx);
        elements.modal.classList.remove("hidden");
        loadWorkflowRunPreviewContext(ticketId, ticket, localCtx, elements);
    }

    function confirmWorkflowRunPreview() {
        var ticketId = pendingWorkflowRunTicketId;
        var ticketIds = pendingWorkflowRunTicketIds.slice();
        closeWorkflowRunPreview();
        if (ticketIds.length > 1) startWorkflowTicketGroup(ticketIds);
        else if (ticketId) startWorkflowTicketRun(ticketId);
    }

    function activeWorkflowDetailTab() {
        var active = document.querySelector(".wf-tab.active[data-tab]");
        return active ? (active.dataset.tab || "") : "";
    }

    function markWorkflowTicketGroupPending(ticketIds, pending) {
        ticketIds.forEach(function (ticketId) {
            var key = String(ticketId);
            if (pending) workflowTicketPendingRunStartedAt[key] = Date.now();
            else delete workflowTicketPendingRunStartedAt[key];
        });
    }

    function refreshWorkflowTicketGroupSurfaces() {
        loadWorkflowExecutionSessions();
        loadLoopActivityFeed({ quiet: true });
        loadOrchestratorTimeline({ quiet: true });
    }

    function handleWorkflowTicketGroupStarted(ticketIds, data) {
        var startedTicketIds = (data.started || []).map(function (item) { return String(item.ticket_id); });
        markWorkflowTicketGroupPending(ticketIds.filter(function (ticketId) {
            return startedTicketIds.indexOf(String(ticketId)) === -1;
        }), false);
        tickWorkflowTicketTimers();
        snack(workflowFeedbackText(data, "Ticket group started"));
        var activeTab = activeWorkflowDetailTab();
        if (!activeTab || activeTab === "tickets") switchTab("loop", { persist: false });
        return Promise.all([loadDetail(currentWorkflowId), loadWorkflowTicketQueue(), loadActiveRuns()]);
    }

    function handleWorkflowTicketGroupFailed(ticketIds, error) {
        markWorkflowTicketGroupPending(ticketIds, false);
        tickWorkflowTicketTimers();
        snack(workflowErrorText(error, "Failed to start ticket group"), "error");
    }

    function showWorkflowRunsTab() {
        workflowRunsSeenByWorkflowId[String(currentWorkflowId)] = true;
        var runsTabBtn = document.getElementById("wf-runs-tab-btn");
        if (runsTabBtn) runsTabBtn.classList.remove("hidden");
    }

    function workflowTicketGroupCanStart(ticketIds) {
        return Boolean(currentWorkflowId && ticketIds.length);
    }

    function startWorkflowTicketGroup(ticketIds) {
        ticketIds = (ticketIds || []).map(function (id) { return parseInt(id, 10); }).filter(Boolean);
        if (!workflowTicketGroupCanStart(ticketIds)) return;
        markWorkflowTicketGroupPending(ticketIds, true);
        ensureWorkflowTicketTimerTick();
        showWorkflowRunsTab();
        startPolling();
        api("POST", "/workflows/" + encodeURIComponent(currentWorkflowId) + "/run-ticket-group", {
            ticket_ids: ticketIds
        }).then(function (data) {
            return handleWorkflowTicketGroupStarted(ticketIds, data);
        }).then(function () {
            refreshWorkflowTicketGroupSurfaces();
        }).catch(function (e) {
            handleWorkflowTicketGroupFailed(ticketIds, e);
        });
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
        workflowTicketPendingRunStartedAt[String(ticketId)] = Date.now();
        ensureWorkflowTicketTimerTick();
        var buttons = document.querySelectorAll('.wf-workflow-ticket-run[data-ticket-id="' + esc(ticketId) + '"]');
        buttons.forEach(function (btn) { btn.disabled = true; btn.textContent = "Starting"; });
        var runsTabBtn = document.getElementById("wf-runs-tab-btn");
        workflowRunsSeenByWorkflowId[String(currentWorkflowId)] = true;
        if (runsTabBtn) runsTabBtn.classList.remove("hidden");
        startPolling();
        [300, 1200, 2500].forEach(function (delay) {
            setTimeout(function () {
                loadActiveRuns();
                loadLoopActivityFeed({ quiet: true });
                loadOrchestratorTimeline({ quiet: true });
            }, delay);
        });
        api("POST", "/tickets/tickets/" + encodeURIComponent(ticketId) + "/send-to-workflow", {
            workflow_id: parseInt(currentWorkflowId, 10)
        }).then(function (data) {
            snack(workflowFeedbackText(data, "Ticket run started"));
            if (!activeWorkflowDetailTab() || activeWorkflowDetailTab() === "tickets") {
                switchTab("loop", { persist: false });
            }
            loadDetail(currentWorkflowId);
            loadWorkflowTicketQueue();
            loadActiveRuns().then(function () {
                if (data && data.run_id) {
                    focusWorkflowRun(data.run_id, {
                        ticketId: ticketId,
                        switchTab: activeWorkflowDetailTab() === "loop" ? "loop" : null
                    });
                } else {
                    renderLoopRunTicketContext();
                }
            });
            loadWorkflowExecutionSessions();
            loadLoopActivityFeed({ quiet: true });
        }).catch(function (e) {
            delete workflowTicketPendingRunStartedAt[String(ticketId)];
            tickWorkflowTicketTimers();
            snack(workflowErrorText(e, "Failed to start ticket run"), "error");
            buttons.forEach(function (btn) { btn.disabled = false; btn.textContent = "Run"; });
        });
    }

    function assignTicketToCurrentWorkflow(ticketId, payload, options) {
        options = options || {};
        if (!currentWorkflowId || !ticketId) return Promise.resolve({ status: "skipped" });
        var alreadyQueued = workflowQueueTickets.some(function (ticket) { return String(ticket.id) === String(ticketId); });
        if (alreadyQueued) {
            if (!options.silent) snack("Ticket is already in this workflow queue");
            return Promise.resolve({ status: "skipped" });
        }
        var linkKey = "database:" + String(ticketId);
        if (workflowPendingTicketLinks[linkKey]) {
            if (!options.silent) snack("Ticket is already being linked");
            return Promise.resolve({ status: "skipped" });
        }
        workflowPendingTicketLinks[linkKey] = true;
        markWorkflowBoardTicketPending(payload);
        return api("PUT", "/tickets/tickets/" + encodeURIComponent(ticketId), {
            linked_workflow_id: parseInt(currentWorkflowId, 10),
            workflow_queue_position: workflowQueueTickets.length
        }).then(function (result) {
            delete workflowPendingTicketLinks[linkKey];
            var queueTicketId = result && result.id ? result.id : ticketId;
            rememberWorkflowBoardTicketSource(queueTicketId, payload);
            if (!options.silent) snack("Ticket added to workflow queue");
            if (payload && payload.ticket_key) applyWorkflowBoardTicketLinkedState(payload.ticket_key, payload);
            return fetchWorkflowQueueTicketRecord(queueTicketId);
        }).then(function (ticket) {
            if (ticket) appendWorkflowQueueTicket(ticket);
            loadActiveRuns();
            return { status: "added" };
        }).catch(function (e) {
            if (!options.silent) snack(e.message || "Failed to add ticket to workflow", "error");
            delete workflowPendingTicketLinks[linkKey];
            return { status: "failed" };
        });
    }

    function copyExternalTicketToCurrentWorkflow(payload, options) {
        options = options || {};
        if (!currentWorkflowId || !payload) return Promise.resolve({ status: "skipped" });
        var destinationBoard = payload.destination_board_id
            ? null
            : workflowDatabaseBoardForProject(payload.default_project_id);
        var boardId = payload.destination_board_id || (destinationBoard && destinationBoard.id) || payload.local_board_id || payload.board_id;
        if (!boardId) {
            if (!options.silent) snack("Link this Jira/Trello board's project to a local board before adding tickets to a workflow", "error");
            return Promise.resolve({ status: "failed" });
        }
        var linkKey = workflowPayloadLinkKey(payload);
        if (linkKey && (workflowPendingTicketLinks[linkKey] || workflowLinkedExternalTicketKeys[linkKey])) {
            if (!options.silent) snack("Ticket is already linked to a workflow");
            return Promise.resolve({ status: "skipped" });
        }
        if (linkKey) workflowPendingTicketLinks[linkKey] = true;
        markWorkflowBoardTicketPending(payload);
        return api("POST", "/tickets/tickets/copy-external-to-board", {
            board_id: parseInt(boardId, 10),
            title: payload.title || "Untitled ticket",
            description: payload.description || "",
            priority: payload.priority || "medium",
            complexity: payload.complexity || "",
            external_source: payload.source || payload.external_source || "",
            external_id: payload.external_id || payload.ticket_id || "",
            external_url: payload.external_url || payload.url || "",
            media: Array.isArray(payload.media) ? payload.media : [],
            linked_project_id: payload.default_project_id || null,
            linked_workflow_id: parseInt(currentWorkflowId, 10)
        }).then(function (result) {
            if (linkKey) delete workflowPendingTicketLinks[linkKey];
            var newId = result && result.id;
            rememberWorkflowBoardTicketSource(newId, payload);
            if (!options.silent) snack("Ticket copied into workflow queue");
            if (payload && payload.ticket_key) applyWorkflowBoardTicketLinkedState(payload.ticket_key, payload);
            if (!newId) return { status: "failed" };
            return fetchWorkflowQueueTicketRecord(newId);
        }).then(function (ticket) {
            if (!ticket || ticket.status) return ticket;
            appendWorkflowQueueTicket(ticket);
            loadActiveRuns();
            return { status: "added" };
        }).catch(function (e) {
            if (!options.silent) snack(e.message || "Failed to copy ticket into workflow", "error");
            if (linkKey) delete workflowPendingTicketLinks[linkKey];
            return { status: "failed" };
        });
    }

    function addAllBoardTicketsToWorkflow(laneId) {
        if (!currentWorkflowId) {
            snack("Select a workflow first", "error");
            return;
        }
        var selected = getSelectedWorkflowBoardOption();
        if (!selected || !selected.default_project_id) {
            snack("Click the gear icon next to the board dropdown to link this board to a project", "error");
            return;
        }
        var items = getAddableBoardTicketItems(laneId);
        if (!items.length) {
            snack("No tickets available to add to this workflow");
            return;
        }
        var buttons = document.querySelectorAll(".wf-lane-add-all-board-tickets");
        buttons.forEach(function (btn) { btn.disabled = true; });
        var added = 0;
        var failed = 0;

        function finish() {
            refreshWorkflowLaneAddAllButtons();
            if (failed && !added) {
                snack("Failed to add tickets to workflow queue", "error");
                return;
            }
            if (!added) {
                snack("No tickets were added to this workflow");
                return;
            }
            snack(added + " ticket" + (added === 1 ? "" : "s") + " added to workflow queue");
            switchTab("tickets", { persist: true });
        }

        function processNext(index) {
            if (index >= items.length) {
                finish();
                return;
            }
            var item = items[index];
            var promise = item.selected.source !== "database"
                ? copyExternalTicketToCurrentWorkflow(item.payload, { silent: true })
                : assignTicketToCurrentWorkflow(item.ticket.id, item.payload, { silent: true });
            promise.then(function (result) {
                if (result && result.status === "added") added += 1;
                else if (result && result.status === "failed") failed += 1;
                processNext(index + 1);
            });
        }

        processNext(0);
    }

    function addWorkflowBoardTicketToQueue(ticketKey, btnEl) {
        if (!ticketKey) {
            snack("Could not add this ticket to the workflow", "error");
            return;
        }
        if (!hasWorkflowQueueTarget()) {
            snack("Select a workflow before adding tickets to its queue", "error");
            return;
        }
        var item = workflowBoardTicketByKey[ticketKey];
        if (!item) return;
        var state = workflowBoardTicketLinkState(item);
        if (!state.canDragToWorkflow) {
            if (state.isLinkedToWorkflow) snack("Ticket is already in this workflow queue");
            else if (state.blockedByMissingProject) snack("Link this board to a project before adding tickets to a workflow", "error");
            else snack("Cannot add this ticket right now", "error");
            return;
        }
        var payload = workflowBoardTicketDropPayload(ticketKey);
        if (!payload) return;
        if (btnEl) btnEl.disabled = true;
        var promise;
        if (isExternalWorkflowBoardPayload(payload)) {
            promise = copyExternalTicketToCurrentWorkflow(payload);
        } else if (isLocalDatabaseTicketId(payload.ticket_id)) {
            promise = assignTicketToCurrentWorkflow(payload.ticket_id, payload);
        } else {
            snack("Could not add this ticket to the workflow", "error");
            if (btnEl) btnEl.disabled = false;
            return;
        }
        if (promise && typeof promise.finally === "function") {
            promise.then(function (result) {
                if (result && result.status === "added") {
                    switchTab("tickets", { persist: true });
                }
            }).finally(function () {
                syncWorkflowBoardTicketRowUi(ticketKey);
            });
        }
    }

    function handleWorkflowTicketDropPayload(payloadOrId) {
        if (!payloadOrId) return;
        if (!hasWorkflowQueueTarget()) {
            snack("Select a workflow before adding tickets to its queue", "error");
            return;
        }
        if (typeof payloadOrId === "string" || typeof payloadOrId === "number") {
            if (isLocalDatabaseTicketId(payloadOrId)) {
                assignTicketToCurrentWorkflow(payloadOrId);
            }
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
            if (isExternalWorkflowBoardPayload(payloadOrId)) {
                copyExternalTicketToCurrentWorkflow(payloadOrId);
            } else if (isLocalDatabaseTicketId(payloadOrId.ticket_id)) {
                assignTicketToCurrentWorkflow(payloadOrId.ticket_id, payloadOrId);
            } else {
                snack("Could not add this ticket to the workflow", "error");
            }
        }
    }

    function persistWorkflowTicketQueueOrder() {
        if (!currentWorkflowId) return;
        var ids = workflowQueueTickets.map(function (t) { return t.id; }).filter(Boolean);
        api("PUT", "/tickets/workflows/" + encodeURIComponent(currentWorkflowId) + "/tickets/reorder", { ticket_ids: ids })
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
        var types = evt && evt.dataTransfer ? Array.prototype.slice.call(evt.dataTransfer.types || []) : [];
        if (types.indexOf("application/x-workflow-tab") !== -1 || workflowTabDragId) return null;
        if (workflowBoardDragPayload) return workflowBoardDragPayload;
        if (!evt.dataTransfer) return null;
        if (types.indexOf("application/x-workflow-board-ticket") === -1 &&
            types.indexOf("application/json") === -1 &&
            types.indexOf("text/plain") === -1) return null;
        var raw = evt.dataTransfer.getData("application/x-workflow-board-ticket")
            || evt.dataTransfer.getData("application/json")
            || "";
        if (raw) {
            try {
                var payload = JSON.parse(raw);
                if (payload && payload.type === "workflow-board-ticket") {
                    return payload;
                }
            } catch (e) {}
        }
        var plain = evt.dataTransfer.getData("text/plain") || "";
        return isLocalDatabaseTicketId(plain) ? plain : null;
    }

    function markWorkflowBoardTicketPending(payload) {
        if (!payload || !payload.ticket_key) return;
        var key = String(payload.ticket_key);
        syncWorkflowBoardTicketRowUi(key);
        var escapedKey = window.CSS && CSS.escape ? CSS.escape(key) : key.replace(/"/g, '\\"');
        var row = document.querySelector('.wf-board-ticket-row[data-ticket-key="' + escapedKey + '"]');
        if (!row) return;
        row.setAttribute("draggable", "false");
        row.classList.add("wf-board-ticket-pending");
    }

    function workflowTicketDropZoneRects() {
        var detail = document.getElementById("wf-detail");
        if (!detail || detail.classList.contains("hidden") || !currentWorkflowId) return [];
        var rects = [];
        var list = document.getElementById("wf-workflow-tickets-list");
        var tab = document.getElementById("wf-tab-tickets");
        var right = document.getElementById("wf-split-right");
        [list, tab, right].forEach(function (el) {
            if (!el) return;
            var rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) rects.push(rect);
        });
        return rects;
    }

    function workflowDropZoneContainsPoint(evt) {
        if (!evt || typeof evt.clientX !== "number" || typeof evt.clientY !== "number") return false;
        var rects = workflowTicketDropZoneRects();
        for (var i = 0; i < rects.length; i++) {
            var rect = rects[i];
            if (evt.clientX >= rect.left && evt.clientX <= rect.right && evt.clientY >= rect.top && evt.clientY <= rect.bottom) {
                return true;
            }
        }
        return false;
    }

    function rememberWorkflowDragPoint(evt) {
        if (!evt) return;
        workflowLastDragPoint = { clientX: evt.clientX, clientY: evt.clientY };
    }

    function bindWorkflowTicketQueueRows(listEl) {
        listEl.querySelectorAll(".wf-workflow-ticket-row, .wf-loop-ticket-banner").forEach(function (row) {
            var isStatic = row.classList.contains("wf-workflow-ticket-row--static") || row.classList.contains("wf-loop-ticket-banner");
            row.addEventListener("click", function (evt) {
                if (evt.target.closest("button, a, input, textarea, select, .kb-ticket-list-desc, .kb-ticket-list-drag-handle")) return;
                selectWorkflowQueueTicket(row.dataset.ticketId || "", row);
            });
            row.addEventListener("dragend", function () {
                workflowQueueDragTicketId = null;
                listEl.querySelectorAll(".wf-workflow-ticket-row").forEach(function (r) {
                    r.classList.remove("ring-1", "ring-[#f97316]/50");
                });
            });
            row.addEventListener("dragover", function (evt) {
                if (isStatic) return;
                evt.preventDefault();
                row.classList.add("ring-1", "ring-[#f97316]/50");
            });
            row.addEventListener("dragleave", function () {
                row.classList.remove("ring-1", "ring-[#f97316]/50");
            });
            row.addEventListener("drop", function (evt) {
                if (isStatic) return;
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
            var loopBtn = row.querySelector(".wf-workflow-ticket-loop");
            if (loopBtn) {
                loopBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    openWorkflowTicketLoop(loopBtn.dataset.ticketId || "", row);
                });
            }
            var copyBtn = row.querySelector(".wf-workflow-ticket-copy");
            if (copyBtn) {
                copyBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    selectWorkflowQueueTicket(copyBtn.dataset.ticketId || "", row);
                    copyWorkflowQueueTicket(copyBtn.dataset.ticketId || "");
                });
            }
            var orchestratorBtn = row.querySelector(".wf-workflow-ticket-orchestrator");
            if (orchestratorBtn) {
                orchestratorBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    selectWorkflowQueueTicket(orchestratorBtn.dataset.ticketId || "", row);
                    sendWorkflowQueueTicketToOrchestrator(orchestratorBtn.dataset.ticketId || "", orchestratorBtn);
                });
            }
            var removeBtn = row.querySelector(".wf-workflow-ticket-remove");
            if (removeBtn) {
                removeBtn.addEventListener("click", function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    selectWorkflowQueueTicket(removeBtn.dataset.ticketId || "", row);
                    removeWorkflowQueueTicket(removeBtn.dataset.ticketId || "");
                });
            }
            row.addEventListener("dblclick", function () {
                var id = row.dataset.ticketId || "";
                if (!id) return;
                openWorkflowTicketLoop(id, row);
            });
        });
    }

    function ensureWorkflowDropOverlay(list) {
        if (!list) return;
        list.classList.add("wf-workflow-ticket-drop-zone");
        if (list.querySelector(".wf-workflow-drop-overlay")) return;
        var overlay = document.createElement("div");
        overlay.className = "wf-workflow-drop-overlay";
        overlay.innerHTML = '<span class="wf-workflow-drop-overlay-label">Drop here</span>';
        list.appendChild(overlay);
    }

    function setWorkflowTicketDropTargetActive(active) {
        var list = document.getElementById("wf-workflow-tickets-list");
        if (list) {
            list.classList.toggle("is-drop-target", !!active);
            var hint = list.querySelector(".wf-workflow-drop-hint");
            if (hint) {
                var dropText = hint.getAttribute("data-hint-drop") || "Drop here";
                var defaultText = hint.getAttribute("data-hint-default") || hint.textContent;
                hint.textContent = active ? dropText : defaultText;
                hint.classList.toggle("is-drop-active", !!active);
            }
        }
    }

    function dragHasBoardTicket(evt) {
        if (workflowTabDragId) return false;
        if (workflowBoardDragPayload) return true;
        if (!evt || !evt.dataTransfer) return false;
        var types = Array.prototype.slice.call(evt.dataTransfer.types || []);
        return types.indexOf("application/x-workflow-board-ticket") !== -1 ||
            types.indexOf("application/json") !== -1;
    }

    function bindWorkflowTicketDropZone() {
        var tab = document.getElementById("wf-tab-tickets");
        var list = document.getElementById("wf-workflow-tickets-list");
        var empty = document.getElementById("wf-workflow-tickets-empty");
        var targets = [tab, list, empty].filter(Boolean);
        if (!targets.length) return;
        ensureWorkflowDropOverlay(list);

        function dragHasTicket(evt) {
            if (workflowTabDragId) return false;
            if (workflowQueueDragTicketId || workflowBoardDragPayload) return true;
            if (!evt.dataTransfer) return false;
            var types = Array.prototype.slice.call(evt.dataTransfer.types || []);
            return types.indexOf("application/json") !== -1 || types.indexOf("application/x-workflow-board-ticket") !== -1;
        }

        function setDropActive(active) {
            setWorkflowTicketDropTargetActive(active && !!workflowBoardDragPayload);
        }

        function resetDropTarget() {
            workflowDropZoneHoverDepth = 0;
            setDropActive(false);
        }

        targets.forEach(function (target) {
            if (target.dataset.workflowDropBound === "1") return;
            target.dataset.workflowDropBound = "1";
            target.addEventListener("dragenter", function (evt) {
                if (!dragHasTicket(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                if (dragHasBoardTicket(evt)) {
                    workflowDropZoneHoverDepth += 1;
                    setDropActive(true);
                }
            }, true);
            target.addEventListener("dragover", function (evt) {
                if (!dragHasTicket(evt)) return;
                rememberWorkflowDragPoint(evt);
                positionWorkflowBoardDragGhost(evt);
                evt.preventDefault();
                if (evt.dataTransfer) {
                    evt.dataTransfer.dropEffect = workflowQueueDragTicketId ? "move" : "copy";
                }
                if (dragHasBoardTicket(evt)) setDropActive(true);
            }, true);
            target.addEventListener("dragleave", function (evt) {
                if (!dragHasBoardTicket(evt)) return;
                workflowDropZoneHoverDepth = Math.max(0, workflowDropZoneHoverDepth - 1);
                if (workflowDropZoneHoverDepth === 0) setDropActive(false);
            }, true);
            target.addEventListener("drop", function (evt) {
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                evt.stopPropagation();
                resetDropTarget();
                removeWorkflowBoardDragGhost();
                if (workflowQueueDragTicketId) return;
                handleWorkflowTicketDropPayload(workflowTicketPayloadFromDrop(evt));
                workflowBoardDragPayload = null;
                workflowLastDragPoint = null;
            }, true);
        });

        if (!workflowDropDocumentBound) {
            workflowDropDocumentBound = true;
            document.addEventListener("drag", function (evt) {
                if (workflowBoardDragGhostEl) positionWorkflowBoardDragGhost(evt);
            }, true);
            document.addEventListener("dragover", function (evt) {
                if (!dragHasTicket(evt)) return;
                if (workflowBoardDragGhostEl) positionWorkflowBoardDragGhost(evt);
                if (!workflowDropZoneContainsPoint(evt)) {
                    if (workflowDropZoneHoverDepth === 0) setDropActive(false);
                    return;
                }
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                if (evt.dataTransfer) {
                    evt.dataTransfer.dropEffect = workflowQueueDragTicketId ? "move" : "copy";
                }
                if (dragHasBoardTicket(evt)) {
                    workflowDropZoneHoverDepth = Math.max(workflowDropZoneHoverDepth, 1);
                    setDropActive(true);
                }
            }, true);
            document.addEventListener("drop", function (evt) {
                if (!dragHasTicket(evt) || !workflowDropZoneContainsPoint(evt)) return;
                rememberWorkflowDragPoint(evt);
                evt.preventDefault();
                evt.stopPropagation();
                resetDropTarget();
                removeWorkflowBoardDragGhost();
                if (workflowQueueDragTicketId) return;
                handleWorkflowTicketDropPayload(workflowTicketPayloadFromDrop(evt));
                workflowBoardDragPayload = null;
                workflowLastDragPoint = null;
            }, true);
            document.addEventListener("dragend", function () {
                resetDropTarget();
                removeWorkflowBoardDragGhost();
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

    function applyLiveStepCardState(step) {
        var statusCls = loopStepStatusClass(step.status);
        var ringNode = document.querySelector('.wf-loop-ring-node[data-step-id="' + step.id + '"]');
        if (ringNode) {
            ringNode.classList.toggle("is-selected", expandedStepId === step.id);
            ringNode.classList.remove(
                "wf-loop-step-status--running",
                "wf-loop-step-status--waiting",
                "wf-loop-step-status--done",
                "wf-loop-step-status--failed"
            );
            if (statusCls) ringNode.classList.add(statusCls);
        }
        var listRow = document.querySelector('.wf-loop-list-row[data-step-id="' + step.id + '"]');
        if (listRow && step.status && step.status !== "pending") {
            var badge = listRow.querySelector(".wf-loop-list-status");
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "wf-loop-list-status text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-300 capitalize";
                var titleRow = listRow.querySelector(".flex.items-center");
                if (titleRow) titleRow.appendChild(badge);
            }
            badge.textContent = step.status;
        }
    }

    function isLoopStepModalOpen() {
        var modal = document.getElementById("wf-loop-step-modal");
        return !!(modal && !modal.classList.contains("hidden"));
    }

    function isStepEditorInteractionActive() {
        if (!isLoopStepModalOpen()) return false;
        var active = document.activeElement;
        if (!active) return false;
        var modal = document.getElementById("wf-loop-step-modal");
        return !!(modal && modal.contains(active));
    }

    function syncLoopStepSelectionUi() {
        document.querySelectorAll(".wf-loop-ring-node").forEach(function (node) {
            var selected = expandedStepId && String(node.dataset.stepId) === String(expandedStepId);
            node.classList.toggle("is-selected", !!selected);
        });
        document.querySelectorAll(".wf-loop-list-row").forEach(function (row) {
            var selected = expandedStepId && String(row.dataset.stepId) === String(expandedStepId);
            row.classList.toggle("border-[#f97316]/60", !!selected);
            row.classList.toggle("bg-[#f97316]/8", !!selected);
            row.classList.toggle("border-white/15", !selected);
            row.classList.toggle("bg-[#152054]/35", !selected);
        });
    }

    function closeLoopStepModal(options) {
        options = options || {};
        var modal = document.getElementById("wf-loop-step-modal");
        if (modal) modal.classList.add("hidden");
        workflowLoopStepModalState = null;
        if (!options.keepSelection) {
            expandedStepId = null;
            if (currentWorkflow) syncLoopStepSelectionUi();
        }
    }

    function loopStepToolOptionById(toolId) {
        return LOOP_STEP_TOOL_OPTIONS.filter(function (opt) {
            return opt.id === String(toolId || "");
        })[0] || null;
    }

    function normalizeWorkflowToolId(toolId) {
        var t = String(toolId || "").trim().toLowerCase();
        var aliases = {
            browser: "browser_use",
            project_cli: "cli",
            ide: "cli",
            cursor: "cli",
            codex: "cli",
            sidecar: "computer_use",
            vision: "computer_use",
            workflow_agent: "agent",
            orchestrator: "agent",
            other: "agent",
            command: "shell",
            terminal: "shell",
            script: "python",
            python_script: "python",
            request: "http",
            recording: "macro"
        };
        if (loopStepToolOptionById(t)) return t;
        return aliases[t] || "";
    }

    function normalizeWorkflowToolList(tools) {
        var out = [];
        if (!Array.isArray(tools)) return out;
        tools.forEach(function (tool) {
            var normalized = normalizeWorkflowToolId(tool);
            if (normalized && out.indexOf(normalized) < 0) out.push(normalized);
        });
        return out;
    }

    function toolsForLoopStepAction(action) {
        action = String(action || "").trim();
        if (action === "agent_instruction") return ["agent"];
        if (action === "computer_use") return ["computer_use"];
        if (action === "playwright") return ["playwright", "browser_use"];
        if (action === "browser_use") return ["browser_use"];
        if (action === "send_to_project_cli") return ["cli"];
        if (action === "execute_code") return ["python"];
        if (action === "run_command") return ["shell"];
        if (action === "http_request") return ["http"];
        if (action === "play_recording") return ["macro"];
        if (action === "ytdlp") return ["ytdlp", "cli"];
        return [];
    }

    function loopStepToolsFromStep(step) {
        var cfg = normalizeStepConfig(step || {});
        if (Array.isArray(cfg.tools) && cfg.tools.length) {
            return normalizeWorkflowToolList(cfg.tools);
        }
        var action = (step && step.action_type) || "";
        return toolsForLoopStepAction(action);
    }

    function loopStepToolIconsTitle(step) {
        return loopStepToolsFromStep(step || {}).map(function (toolId) {
            var opt = loopStepToolOptionById(toolId);
            return opt ? opt.label : toolId;
        }).join(", ");
    }

    function loopStepToolIconsHtml(step, iconsClass) {
        var tools = loopStepToolsFromStep(step || {});
        if (!tools.length) return "";
        iconsClass = iconsClass || "wf-loop-ring-node-tool-icons";
        var icons = tools.map(function (toolId) {
            var opt = loopStepToolOptionById(toolId);
            if (!opt || !opt.emoji) return "";
            return '<span class="wf-loop-step-tool-icon" aria-hidden="true">' + opt.emoji + "</span>";
        }).join("");
        if (!icons) return "";
        return '<span class="' + iconsClass + '" title="' + esc(loopStepToolIconsTitle(step)) + '">' + icons + "</span>";
    }

    function deriveLoopStepActionType(tools) {
        tools = Array.isArray(tools) ? tools : [];
        if (tools.indexOf("computer_use") >= 0) return "computer_use";
        if (tools.indexOf("playwright") >= 0) return "playwright";
        if (tools.indexOf("browser_use") >= 0) return "browser_use";
        if (tools.indexOf("cli") >= 0) return "send_to_project_cli";
        if (tools.indexOf("python") >= 0) return "execute_code";
        if (tools.indexOf("shell") >= 0) return "run_command";
        if (tools.indexOf("http") >= 0) return "http_request";
        if (tools.indexOf("macro") >= 0) return "play_recording";
        if (tools.indexOf("agent") >= 0) return "agent_instruction";
        return "send_to_project_cli";
    }

    function uiToolsFromHarnessSuggestion(suggestion) {
        if (!suggestion) return [];
        if (Array.isArray(suggestion.ui_tools) && suggestion.ui_tools.length) {
            return suggestion.ui_tools.map(String);
        }
        if (Array.isArray(suggestion.tools) && suggestion.tools.length) {
            var mapped = [];
            suggestion.tools.forEach(function (tool) {
                var t = String(tool || "").toLowerCase();
                if (t === "playwright" || t === "browser") {
                    if (mapped.indexOf("playwright") < 0) mapped.push("playwright");
                    if (mapped.indexOf("browser_use") < 0) mapped.push("browser_use");
                } else if (t === "computer_use" || t === "sidecar" || t === "vision") {
                    if (mapped.indexOf("computer_use") < 0) mapped.push("computer_use");
                } else if (t === "project_cli" || t === "cli" || t === "ide") {
                    if (mapped.indexOf("cli") < 0) mapped.push("cli");
                } else {
                    var normalized = normalizeWorkflowToolId(t);
                    if (normalized && mapped.indexOf(normalized) < 0) mapped.push(normalized);
                }
            });
            return mapped;
        }
        return loopStepToolsFromStep({ action_type: suggestion.action_type || "", config: { tools: [] } });
    }

    function loopStepChosenModels() {
        var settings = normalizedRunSettings(currentWorkflow || {});
        return workflowCliNormalizeChosenModels(settings.chosen_models || []);
    }

    function loopStepExecutionRouteConfig(cfg) {
        var route = cfg && cfg.execution_route && typeof cfg.execution_route === "object" ? cfg.execution_route : {};
        return {
            enabled: route.enabled === true,
            mode: route.mode === "scoped" ? "scoped" : "scoped",
            scoped_model_key: String(route.scoped_model_key || "").trim(),
            route_snapshot: route.route_snapshot && typeof route.route_snapshot === "object" ? Object.assign({}, route.route_snapshot) : {}
        };
    }

    function loopStepRouteCapabilityText(route) {
        if (!route) return "Inherited";
        var parts = [];
        if (route.intelligence_hint) parts.push(String(route.intelligence_hint));
        if (route.speed_hint) parts.push(String(route.speed_hint));
        if (route.tier) parts.push(String(route.tier));
        if (route.codex_reasoning_effort) parts.push("reasoning " + String(route.codex_reasoning_effort));
        if (route.codex_service_tier) parts.push("service " + String(route.codex_service_tier));
        return parts.length ? parts.join(" · ") : "Default";
    }

    function loopStepScopedRouteSnapshot(model) {
        if (!model) return {};
        return {
            key: workflowCliChosenModelKey(model),
            backend_id: String(model.backend_id || "").trim(),
            provider: String(model.provider || "").trim(),
            model: String(model.id || model.model || "").trim(),
            name: String(model.name || model.id || model.model || "").trim(),
            scope: String(model.scope || "").trim(),
            tier: String(model.tier || "").trim(),
            speed_hint: String(model.speed_hint || "").trim(),
            intelligence_hint: String(model.intelligence_hint || "").trim()
        };
    }

    function findLoopStepScopedModel(routeConfig) {
        routeConfig = routeConfig || loopStepExecutionRouteConfig({});
        var models = loopStepChosenModels();
        var key = String(routeConfig.scoped_model_key || "").trim();
        var snapshot = routeConfig.route_snapshot || {};
        var model = null;
        if (key) {
            model = models.filter(function (item) {
                return workflowCliChosenModelKey(item) === key;
            })[0] || null;
        }
        if (model) return { model: model, missing: false, snapshot: loopStepScopedRouteSnapshot(model) };
        if (snapshot && snapshot.model) return { model: null, missing: !!key, snapshot: snapshot };
        return { model: null, missing: false, snapshot: {} };
    }

    function renderLoopStepExecutionRouteEditor(routeConfig) {
        var enabledEl = document.getElementById("wf-loop-step-route-enabled");
        var selectEl = document.getElementById("wf-loop-step-route-select");
        var emptyEl = document.getElementById("wf-loop-step-route-empty");
        var warningEl = document.getElementById("wf-loop-step-route-warning");
        var previewEl = document.getElementById("wf-loop-step-route-preview");
        var backendEl = document.getElementById("wf-loop-step-route-preview-backend");
        var providerEl = document.getElementById("wf-loop-step-route-preview-provider");
        var modelEl = document.getElementById("wf-loop-step-route-preview-model");
        var capabilitiesEl = document.getElementById("wf-loop-step-route-preview-capabilities");
        var detailsEl = document.getElementById("wf-loop-step-route-preview-details");
        if (!enabledEl || !selectEl) return;
        routeConfig = routeConfig || loopStepExecutionRouteConfig({});
        var scoped = loopStepChosenModels();
        var selected = findLoopStepScopedModel(routeConfig);
        enabledEl.checked = !!routeConfig.enabled;
        selectEl.disabled = !routeConfig.enabled || !scoped.length;
        selectEl.innerHTML = '<option value="">Choose a scoped route</option>' + scoped.map(function (model) {
            var snapshot = loopStepScopedRouteSnapshot(model);
            var detail = [snapshot.backend_id || "cli", snapshot.provider || "provider", snapshot.model || "auto"].filter(Boolean).join(" / ");
            return '<option value="' + esc(snapshot.key) + '">' + esc(snapshot.name || snapshot.model || "Scoped route") + (detail ? " - " + esc(detail) : "") + '</option>';
        }).join("");
        selectEl.value = selected.model ? workflowCliChosenModelKey(selected.model) : (routeConfig.scoped_model_key || "");
        if (emptyEl) emptyEl.classList.toggle("hidden", !!scoped.length);
        if (warningEl) {
            if (routeConfig.enabled && routeConfig.scoped_model_key && selected.missing) {
                warningEl.textContent = "This scoped route is no longer in the workflow chosen-model catalog. The saved snapshot will still be used unless you pick a replacement.";
                warningEl.classList.remove("hidden");
            } else {
                warningEl.textContent = "";
                warningEl.classList.add("hidden");
            }
        }
        var snapshot = selected.snapshot || {};
        var isEnabled = !!routeConfig.enabled;
        if (previewEl) previewEl.classList.toggle("is-disabled", !isEnabled);
        if (backendEl) backendEl.textContent = isEnabled ? (snapshot.backend_id || "Unknown") : "Current run route";
        if (providerEl) providerEl.textContent = isEnabled ? (snapshot.provider || "Inherited provider") : "Inherited";
        if (modelEl) modelEl.textContent = isEnabled ? (snapshot.name || snapshot.model || "Inherited model") : "Inherited";
        if (capabilitiesEl) capabilitiesEl.textContent = isEnabled ? loopStepRouteCapabilityText(snapshot) : "Inherited";
        if (detailsEl) {
            if (!isEnabled) {
                detailsEl.textContent = "This step will continue using the current route until you explicitly switch it.";
            } else {
                var pieces = [];
                if (snapshot.scope) pieces.push("scope " + snapshot.scope);
                if (snapshot.backend_id) pieces.push("CLI " + snapshot.backend_id);
                if (snapshot.provider) pieces.push("provider " + snapshot.provider);
                if (snapshot.model) pieces.push("model " + snapshot.model);
                detailsEl.textContent = pieces.length ? pieces.join(" · ") : "Saved scoped route snapshot";
            }
        }
    }

    function loopStepExecutionRouteValue() {
        var enabledEl = document.getElementById("wf-loop-step-route-enabled");
        var selectEl = document.getElementById("wf-loop-step-route-select");
        var enabled = !!(enabledEl && enabledEl.checked);
        var key = String((selectEl && selectEl.value) || "").trim();
        if (!enabled) return { enabled: false, mode: "scoped", scoped_model_key: "", route_snapshot: {} };
        if (!key) return { enabled: true, mode: "scoped", scoped_model_key: "", route_snapshot: {} };
        var models = loopStepChosenModels();
        var model = models.filter(function (item) {
            return workflowCliChosenModelKey(item) === key;
        })[0] || null;
        var snapshot = model ? loopStepScopedRouteSnapshot(model) : findLoopStepScopedModel({ scoped_model_key: key, route_snapshot: {} }).snapshot;
        return {
            enabled: true,
            mode: "scoped",
            scoped_model_key: key,
            route_snapshot: snapshot || {}
        };
    }

    function switchLoopStepContentTab(tab) {
        tab = tab === "guardrail" || tab === "validation" || tab === "execution-route" ? tab : "instruction";
        workflowLoopStepContentTab = tab;
        document.querySelectorAll(".wf-loop-step-tab").forEach(function (btn) {
            var active = (btn.getAttribute("data-loop-step-tab") || "instruction") === tab;
            btn.classList.toggle("is-active", active);
            btn.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll(".wf-loop-step-tab-pane").forEach(function (pane) {
            pane.classList.toggle("is-active", pane.id === "wf-loop-step-pane-" + tab);
        });
    }

    function syncLoopStepOrchestratorNote() {
        var noteEl = document.getElementById("wf-loop-step-orchestrator-note");
        if (!noteEl) return;
        var waiting = !!(workflowLoopStepModalState && workflowLoopStepModalState.waitForContinue);
        if (waiting) {
            noteEl.textContent = "Orchestrator will pause this step for your input before continuing.";
            noteEl.classList.remove("hidden");
        } else {
            noteEl.textContent = "";
            noteEl.classList.add("hidden");
        }
    }

    function ensureLoopStepSkillsCatalog() {
        if (workflowLoopSkillsCatalog) return Promise.resolve(workflowLoopSkillsCatalog);
        if (workflowLoopSkillsCatalogLoading) return workflowLoopSkillsCatalogLoading;
        workflowLoopSkillsCatalogLoading = api("GET", "/workflows/skills?limit=200")
            .then(function (resp) {
                workflowLoopSkillsCatalog = Array.isArray(resp && resp.skills) ? resp.skills : [];
                return workflowLoopSkillsCatalog;
            })
            .catch(function () {
                workflowLoopSkillsCatalog = [];
                return workflowLoopSkillsCatalog;
            })
            .finally(function () {
                workflowLoopSkillsCatalogLoading = null;
            });
        return workflowLoopSkillsCatalogLoading;
    }

    function renderLoopStepToolsPicker(selectedTools) {
        var listEl = document.getElementById("wf-loop-step-tools-list");
        if (!listEl) return;
        selectedTools = Array.isArray(selectedTools) ? selectedTools : [];
        listEl.className = "wf-loop-step-picker wf-loop-step-tools-grid";
        listEl.innerHTML = LOOP_STEP_TOOL_OPTIONS.map(function (tool) {
            var checked = selectedTools.indexOf(tool.id) >= 0;
            return '<div class="wf-loop-step-picker-item wf-loop-step-picker-item--tool">' +
                '<input type="checkbox" class="wf-loop-step-tool accent-[#f97316] shrink-0" id="wf-loop-tool-' + esc(tool.id) + '" value="' + esc(tool.id) + '"' + (checked ? " checked" : "") + '>' +
                '<label for="wf-loop-tool-' + esc(tool.id) + '"><span class="text-sm text-gray-200 leading-tight">' + esc(tool.label) + "</span></label>" +
                '<span class="wf-loop-step-tool-emoji" aria-hidden="true" title="' + esc(tool.label) + '">' + (tool.emoji || "🔧") + "</span>" +
            "</div>";
        }).join("");
    }

    function cleanSkillDescriptionForPicker(desc) {
        desc = String(desc || "").trim().replace(/\s+/g, " ");
        if (!desc) return "";
        desc = desc.replace(/^(?:>\s*)+(?:[-*•]\s*)?/g, "");
        desc = desc.replace(/^[-*•]\s+/, "");
        if (desc.length < 12 || /^[>\-\s•*]+$/.test(desc)) return "";
        if (desc.length > 120) desc = desc.slice(0, 117).trim() + "…";
        return desc;
    }

    function renderLoopStepSkillsPicker(selectedSkills) {
        var listEl = document.getElementById("wf-loop-step-skills-list");
        if (!listEl) return;
        selectedSkills = Array.isArray(selectedSkills) ? selectedSkills.map(String) : [];
        var skills = workflowLoopSkillsCatalog || [];
        if (!skills.length) {
            listEl.innerHTML = '<p class="text-xs text-gray-500 px-1 py-2">No skills in catalog.</p>';
            return;
        }
        listEl.innerHTML = skills.map(function (skill) {
            var id = String(skill.id || skill.name || "");
            if (!id) return "";
            var checked = selectedSkills.indexOf(id) >= 0;
            var desc = cleanSkillDescriptionForPicker(skill.description);
            return '<div class="wf-loop-step-picker-item">' +
                '<input type="checkbox" class="wf-loop-step-skill accent-[#f97316] shrink-0" id="wf-loop-skill-' + esc(id) + '" value="' + esc(id) + '"' + (checked ? " checked" : "") + '>' +
                '<label for="wf-loop-skill-' + esc(id) + '" title="' + esc(desc || (skill.name || id)) + '">' +
                    '<span class="text-sm text-gray-200 leading-tight">' + esc(skill.name || id) + '</span>' +
                    (desc ? '<span class="wf-loop-step-skill-desc">' + esc(desc) + '</span>' : '') +
                '</label>' +
            '</div>';
        }).join("");
    }

    function getSelectedLoopStepSkills() {
        return Array.prototype.slice.call(document.querySelectorAll(".wf-loop-step-skill:checked"))
            .map(function (el) { return String(el.value || "").trim(); })
            .filter(Boolean);
    }

    function getSelectedLoopStepTools() {
        return Array.prototype.slice.call(document.querySelectorAll(".wf-loop-step-tool:checked"))
            .map(function (el) { return String(el.value || "").trim(); })
            .filter(Boolean);
    }

    function loopStepModalBuildConfig() {
        var skills = getSelectedLoopStepSkills();
        var tools = getSelectedLoopStepTools();
        var guardrailEl = document.getElementById("wf-loop-step-guardrail");
        var guardrailText = String((guardrailEl && guardrailEl.value) || "").trim();
        var cfg = { skills: skills, tools: tools };
        if (guardrailText) cfg.guardrail = guardrailText;
        var executionRoute = loopStepExecutionRouteValue();
        if (executionRoute.enabled && executionRoute.scoped_model_key) cfg.execution_route = executionRoute;
        var clarify = (workflowLoopStepModalState && workflowLoopStepModalState.clarifyQuestions) || [];
        if (clarify.length) cfg.clarify_questions = clarify;
        return cfg;
    }

    function applyLoopStepSkillSuggestion(suggestion) {
        if (!suggestion) return;
        var instructionEl = document.getElementById("wf-loop-step-instruction");
        var guardrailEl = document.getElementById("wf-loop-step-guardrail");
        var validationEl = document.getElementById("wf-loop-step-validation");
        var hintEl = document.getElementById("wf-loop-step-determine-hint");
        var clarifyEl = document.getElementById("wf-loop-step-clarify");
        if (instructionEl && suggestion.refined_instruction) {
            instructionEl.value = suggestion.refined_instruction;
        }
        if (guardrailEl && suggestion.guardrail) {
            guardrailEl.value = suggestion.guardrail;
        }
        if (validationEl && suggestion.validation_prompt) {
            validationEl.value = suggestion.validation_prompt;
        }
        if (Array.isArray(suggestion.skills)) {
            renderLoopStepSkillsPicker(suggestion.skills);
        }
        renderLoopStepToolsPicker(uiToolsFromHarnessSuggestion(suggestion));
        if (workflowLoopStepModalState && typeof suggestion.wait_for_continue === "boolean") {
            workflowLoopStepModalState.waitForContinue = !!suggestion.wait_for_continue;
            syncLoopStepOrchestratorNote();
        }
        if (hintEl) {
            hintEl.textContent = suggestion.rationale || (
                suggestion.source === "orchestrator_llm" ? "Step refined by orchestrator." : ""
            );
        }
        if (clarifyEl) {
            var qs = suggestion.clarify_questions || [];
            if (workflowLoopStepModalState) workflowLoopStepModalState.clarifyQuestions = qs;
            if (qs.length) {
                clarifyEl.textContent = "Orchestrator may ask: " + qs.join(" · ");
                clarifyEl.classList.remove("hidden");
            } else {
                clarifyEl.classList.add("hidden");
            }
        }
    }

    function determineLoopStepSkills() {
        var instructionEl = document.getElementById("wf-loop-step-instruction");
        var guardrailEl = document.getElementById("wf-loop-step-guardrail");
        var validationEl = document.getElementById("wf-loop-step-validation");
        var hintEl = document.getElementById("wf-loop-step-determine-hint");
        var btn = document.getElementById("wf-loop-step-determine-skills");
        if (hintEl) hintEl.textContent = "Determining skills and tools…";
        if (btn) btn.disabled = true;
        var loopContract = {};
        try {
            loopContract = JSON.parse((currentWorkflow && currentWorkflow.workflow_input) || "{}").loop_contract || {};
        } catch (e) {}
        return api("POST", "/workflows/steps/suggest-harness-llm", {
            instruction: (instructionEl && instructionEl.value) || "",
            guardrail: (guardrailEl && guardrailEl.value) || "",
            validation_prompt: (validationEl && validationEl.value) || "",
            loop_contract: loopContract
        }).then(function (resp) {
            applyLoopStepSkillSuggestion(resp);
            if (hintEl && !hintEl.textContent) {
                hintEl.textContent = resp.source === "orchestrator_llm"
                    ? "Skills and tools selected by orchestrator."
                    : "Used heuristic defaults (orchestrator model unavailable).";
            }
        }).catch(function () {
            if (hintEl) hintEl.textContent = "Could not determine skills and tools.";
        }).finally(function () {
            if (btn) btn.disabled = false;
        });
    }

    function openLoopStepModal(opts) {
        opts = opts || {};
        ensureLoopStepModalBindings();
        var modal = document.getElementById("wf-loop-step-modal");
        if (!modal) return;
        var steps = opts.steps || (currentWorkflow && currentWorkflow.steps) || [];
        var mode = opts.mode === "edit" ? "edit" : "create";
        var step = opts.step || null;
        var stepId = step && step.id != null ? step.id : null;
        if (mode === "edit" && stepId != null) expandedStepId = stepId;

        workflowLoopStepModalState = {
            mode: mode,
            stepId: stepId,
            steps: steps,
            waitForContinue: !!(step && step.wait_for_continue),
            clarifyQuestions: []
        };

        var titleEl = document.getElementById("wf-loop-step-modal-title");
        var metaEl = document.getElementById("wf-loop-step-modal-meta");
        var nameEl = document.getElementById("wf-loop-step-name");
        var instructionEl = document.getElementById("wf-loop-step-instruction");
        var guardrailEl = document.getElementById("wf-loop-step-guardrail");
        var validationEl = document.getElementById("wf-loop-step-validation");
        var hintEl = document.getElementById("wf-loop-step-determine-hint");
        var clarifyEl = document.getElementById("wf-loop-step-clarify");
        var deleteBtn = document.getElementById("wf-loop-step-delete");
        var saveBtn = document.getElementById("wf-loop-step-save");
        var cfg = normalizeStepConfig(step || {});
        var selectedSkills = Array.isArray(cfg.skills) ? cfg.skills.slice() : [];
        var selectedTools = loopStepToolsFromStep(step || {});

        switchLoopStepContentTab("instruction");
        if (hintEl) hintEl.textContent = "";

        if (titleEl) titleEl.textContent = mode === "edit" ? "Edit step" : "Add step";
        if (metaEl) {
            if (mode === "edit" && step) {
                var pos = steps.findIndex(function (s) { return s.id === step.id; });
                metaEl.textContent = "Step " + (pos + 1) + " of " + steps.length;
            } else {
                metaEl.textContent = steps.length + " of " + WORKFLOW_LOOP_MAX_STEPS + " steps used";
            }
        }
        if (nameEl) {
            nameEl.value = mode === "edit" ? (step.name || "") : (opts.defaultName || ("Step " + (steps.length + 1)));
        }
        if (instructionEl) {
            instructionEl.value = mode === "edit" ? (step.instruction || step.description || "") : "";
        }
        if (guardrailEl) {
            guardrailEl.value = cfg.guardrail || "";
        }
        if (validationEl) {
            validationEl.value = (step && (step.validation_prompt || step.verification)) || "";
        }
        renderLoopStepExecutionRouteEditor(loopStepExecutionRouteConfig(cfg));
        if (clarifyEl) clarifyEl.classList.add("hidden");
        var stored = cfg.clarify_questions || [];
        workflowLoopStepModalState.clarifyQuestions = Array.isArray(stored) ? stored : [];
        if (workflowLoopStepModalState.clarifyQuestions.length && clarifyEl) {
            clarifyEl.textContent = "Orchestrator may ask: " + workflowLoopStepModalState.clarifyQuestions.join(" · ");
            clarifyEl.classList.remove("hidden");
        }
        syncLoopStepOrchestratorNote();
        if (deleteBtn) deleteBtn.classList.toggle("hidden", mode !== "edit");
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = mode === "edit" ? "Save" : "Add step";
        }

        renderLoopStepToolsPicker(selectedTools);
        ensureLoopStepSkillsCatalog().then(function () {
            renderLoopStepSkillsPicker(selectedSkills);
        });

        syncLoopStepSelectionUi();
        modal.classList.remove("hidden");
        if (nameEl) {
            nameEl.focus();
            if (mode === "create") nameEl.select();
        }
    }

    function saveLoopStepFromModal() {
        if (!workflowLoopStepModalState || !currentWorkflowId) return;
        var nameEl = document.getElementById("wf-loop-step-name");
        var instructionEl = document.getElementById("wf-loop-step-instruction");
        var validationEl = document.getElementById("wf-loop-step-validation");
        var saveBtn = document.getElementById("wf-loop-step-save");
        var tools = getSelectedLoopStepTools();
        var validationText = String((validationEl && validationEl.value) || "").trim();
        var body = {
            name: String((nameEl && nameEl.value) || "").trim() || "Untitled",
            instruction: (instructionEl && instructionEl.value) || "",
            action_type: deriveLoopStepActionType(tools),
            config: loopStepModalBuildConfig(),
            validation_type: validationText ? "llm_judgment" : "none",
            validation_prompt: validationText,
            wait_for_continue: !!(workflowLoopStepModalState && workflowLoopStepModalState.waitForContinue)
        };
        var state = workflowLoopStepModalState;
        if (saveBtn) saveBtn.disabled = true;

        function finish() {
            if (saveBtn) saveBtn.disabled = false;
        }

        if (state.mode === "create") {
            api("POST", "/workflows/" + currentWorkflowId + "/steps", body)
                .then(function () {
                    snack("Step added");
                    closeLoopStepModal();
                    loadDetail(currentWorkflowId);
                    switchTab("loop", { persist: false });
                })
                .catch(function () { snack("Failed to add step", "error"); })
                .finally(finish);
            return;
        }

        api("PATCH", "/workflows/" + currentWorkflowId + "/steps/" + state.stepId, body)
            .then(function () {
                snack("Step saved");
                closeLoopStepModal({ keepSelection: true });
                expandedStepId = state.stepId;
                loadDetail(currentWorkflowId);
            })
            .catch(function () { snack("Failed to save step", "error"); })
            .finally(finish);
    }

    function syncLoopPresetCapacityHint() {
        var hint = document.getElementById("wf-loop-preset-capacity");
        if (!hint) return;
        var count = (currentWorkflow && Array.isArray(currentWorkflow.steps)) ? currentWorkflow.steps.length : 0;
        hint.textContent = count
            ? (count + " / " + WORKFLOW_LOOP_MAX_STEPS + " steps in this loop")
            : ("Empty loop — presets add up to " + WORKFLOW_LOOP_MAX_STEPS + " steps");
    }

    function openLoopPresetModal() {
        if (!currentWorkflowId) {
            snack("Select a workflow first", "error");
            return;
        }
        var modal = document.getElementById("wf-loop-preset-modal");
        var list = document.getElementById("wf-loop-preset-list");
        var modeEl = document.getElementById("wf-loop-preset-mode");
        if (!modal || !list) return;
        if (modeEl) modeEl.value = "replace";
        syncLoopPresetCapacityHint();
        list.innerHTML = '<p class="text-sm text-gray-500">Loading presets…</p>';
        modal.classList.remove("hidden");
        api("GET", "/workflows/loop-presets").then(function (resp) {
            var presets = (resp && resp.presets) || [];
            if (!presets.length) {
                list.innerHTML = '<p class="text-sm text-gray-500">No presets available.</p>';
                return;
            }
            list.innerHTML = presets.map(function (p) {
                var stepCount = Number(p.step_count) || 0;
                var savedBadge = p.source === "user"
                    ? '<span class="text-[10px] text-[#f97316] flex-shrink-0">Saved</span>'
                    : "";
                var roleLabel = p.role ? String(p.role) : "";
                var categoryLine = [p.category || "", roleLabel].filter(Boolean).join(" · ");
                return '<button type="button" class="wf-loop-preset-item w-full text-left rounded border border-white/15 bg-[#152054]/50 hover:border-[#f97316]/50 px-3 py-2" data-preset="' + esc(p.name) + '" data-step-count="' + esc(stepCount) + '">' +
                    '<div class="flex items-center justify-between gap-2">' +
                        '<div class="text-sm text-white font-medium truncate">' + esc(p.name) + '</div>' +
                        '<div class="flex items-center gap-2 flex-shrink-0">' + savedBadge +
                        '<span class="text-[10px] text-gray-400">' + esc(stepCount) + ' steps</span></div>' +
                    '</div>' +
                    '<div class="text-[11px] text-gray-500">' + esc(categoryLine) + (p.description ? " — " + esc(String(p.description).slice(0, 80)) : "") + '</div>' +
                '</button>';
            }).join("");
            list.querySelectorAll(".wf-loop-preset-item").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    applyLoopPreset(btn.getAttribute("data-preset"), parseInt(btn.getAttribute("data-step-count") || "0", 10) || 0);
                });
            });
        }).catch(function () {
            list.innerHTML = '<p class="text-sm text-red-300">Failed to load presets.</p>';
        });
    }

    function closeLoopPresetModal() {
        var modal = document.getElementById("wf-loop-preset-modal");
        if (modal) modal.classList.add("hidden");
    }

    function applyLoopPreset(presetName, presetStepCount) {
        if (!currentWorkflowId || !presetName) return;
        var modeEl = document.getElementById("wf-loop-preset-mode");
        var mode = (modeEl && modeEl.value) === "append" ? "append" : "replace";
        var currentCount = (currentWorkflow && Array.isArray(currentWorkflow.steps)) ? currentWorkflow.steps.length : 0;
        presetStepCount = Number(presetStepCount) || 0;

        if (mode === "append" && currentCount + presetStepCount > WORKFLOW_LOOP_MAX_STEPS) {
            snack(
                "Cannot append — " + currentCount + " existing + " + presetStepCount +
                " preset steps exceeds the " + WORKFLOW_LOOP_MAX_STEPS + " step limit.",
                "error"
            );
            return;
        }

        function runApply() {
            closeLoopPresetModal();
            snack("Applying preset…");
            api("POST", "/workflows/" + currentWorkflowId + "/apply-loop-preset", {
                preset_name: presetName,
                mode: mode
            }).then(function (resp) {
                var total = resp && resp.total_steps != null ? resp.total_steps : presetStepCount;
                snack(mode === "append"
                    ? ('Loaded preset "' + presetName + '" — ' + total + " steps total")
                    : ('Loaded preset "' + presetName + '"'));
                loadDetail(currentWorkflowId);
                switchTab("loop", { persist: false });
            }).catch(function (e) {
                snack(workflowErrorText(e, "Failed to apply preset"), "error");
            });
        }

        if (mode === "replace" && currentCount > 0) {
            showConfirmModal({
                title: "Replace current loop?",
                message: "Replace all " + currentCount + " existing steps with this " + presetStepCount + "-step preset?\n\nThis cannot be undone.",
                confirmLabel: "Replace loop",
                onConfirm: runApply
            });
            return;
        }
        if (mode === "append" && presetStepCount > 0) {
            showConfirmModal({
                title: "Append preset?",
                message: "Add " + presetStepCount + " steps after your current " + currentCount + " steps?",
                confirmLabel: "Append steps",
                danger: false,
                onConfirm: runApply
            });
            return;
        }
        runApply();
    }

    function getLoopPresetApplyMode() {
        var modeEl = document.getElementById("wf-loop-preset-mode");
        return (modeEl && modeEl.value) === "append" ? "append" : "replace";
    }

    function exportCurrentLoopPreset() {
        if (!currentWorkflowId) {
            snack("Select a workflow first", "error");
            return;
        }
        var steps = (currentWorkflow && currentWorkflow.steps) || [];
        if (!steps.length) {
            snack("This loop has no steps to export", "error");
            return;
        }
        fetch(API + "/workflows/" + currentWorkflowId + "/export-loop-preset")
            .then(function (r) {
                if (!r.ok) {
                    return r.json().then(function (d) {
                        throw new Error((d && d.detail) || "Export failed");
                    });
                }
                var disposition = r.headers.get("Content-Disposition") || "";
                var match = disposition.match(/filename="([^"]+)"/);
                var filename = match ? match[1] : "loop.loop-preset.json";
                return r.blob().then(function (blob) {
                    return { blob: blob, filename: filename };
                });
            })
            .then(function (result) {
                var a = document.createElement("a");
                var url = URL.createObjectURL(result.blob);
                a.href = url;
                a.download = result.filename;
                a.click();
                URL.revokeObjectURL(url);
                snack("Loop exported");
            })
            .catch(function (e) {
                snack((e && e.message) || "Export failed", "error");
            });
    }

    function importLoopPresetFile(file) {
        if (!currentWorkflowId || !file) return;
        var mode = getLoopPresetApplyMode();
        var currentCount = (currentWorkflow && Array.isArray(currentWorkflow.steps)) ? currentWorkflow.steps.length : 0;

        function runImport() {
            var fd = new FormData();
            fd.append("file", file);
            snack("Importing preset…");
            fetch(API + "/workflows/" + currentWorkflowId + "/import-loop-preset?mode=" + encodeURIComponent(mode), {
                method: "POST",
                body: fd
            }).then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok) throw new Error((data && data.detail) || "Import failed");
                    return data;
                });
            }).then(function (resp) {
                closeLoopPresetModal();
                var total = resp && resp.total_steps != null ? resp.total_steps : "";
                snack(mode === "append"
                    ? ("Imported loop preset — " + total + " steps total")
                    : "Imported loop preset");
                loadDetail(currentWorkflowId);
                switchTab("loop", { persist: false });
            }).catch(function (e) {
                snack((e && e.message) || "Import failed", "error");
            });
        }

        if (mode === "replace" && currentCount > 0) {
            showConfirmModal({
                title: "Replace current loop?",
                message: "Import will replace all " + currentCount + " existing steps with the preset file.\n\nThis cannot be undone.",
                confirmLabel: "Import and replace",
                onConfirm: runImport
            });
            return;
        }
        if (mode === "append") {
            showConfirmModal({
                title: "Append imported preset?",
                message: "Add the imported steps after your current " + currentCount + " steps?",
                confirmLabel: "Import and append",
                danger: false,
                onConfirm: runImport
            });
            return;
        }
        runImport();
    }

    function saveCurrentLoopAsPreset() {
        if (!currentWorkflowId) {
            snack("Select a workflow first", "error");
            return;
        }
        var steps = (currentWorkflow && currentWorkflow.steps) || [];
        if (!steps.length) {
            snack("Add at least one step before saving", "error");
            return;
        }
        var defaultName = (currentWorkflow && currentWorkflow.name) ? String(currentWorkflow.name) : "";
        showInputModal({
            title: "Save current steps",
            message: "Save this loop as a reusable preset. Choose a name you will recognize in the preset list.",
            placeholder: "Preset name",
            confirmLabel: "Save preset",
            initialValue: defaultName,
            onConfirm: function (name) {
                name = String(name || "").trim();
                if (!name) {
                    snack("Preset name is required", "error");
                    return;
                }
                api("POST", "/workflows/" + currentWorkflowId + "/save-loop-preset", { name: name })
                    .then(function () {
                        snack("Preset saved — \"" + name + "\"");
                        openLoopPresetModal();
                    })
                    .catch(function (e) {
                        snack(workflowErrorText(e, "Failed to save preset"), "error");
                    });
            }
        });
    }

    function ensureLoopStepModalBindings() {
        if (workflowLoopStepModalBound) return;
        workflowLoopStepModalBound = true;
        var modal = document.getElementById("wf-loop-step-modal");
        if (!modal) return;

        function onClose() {
            closeLoopStepModal();
        }

        var closeBtn = document.getElementById("wf-loop-step-modal-close");
        var cancelBtn = document.getElementById("wf-loop-step-cancel");
        var saveBtn = document.getElementById("wf-loop-step-save");
        var deleteBtn = document.getElementById("wf-loop-step-delete");

        if (closeBtn) closeBtn.addEventListener("click", onClose);
        if (cancelBtn) cancelBtn.addEventListener("click", onClose);
        if (saveBtn) saveBtn.addEventListener("click", saveLoopStepFromModal);
        document.querySelectorAll(".wf-loop-step-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchLoopStepContentTab(btn.getAttribute("data-loop-step-tab") || "instruction");
            });
        });
        var determineSkillsBtn = document.getElementById("wf-loop-step-determine-skills");
        if (determineSkillsBtn) {
            determineSkillsBtn.addEventListener("click", determineLoopStepSkills);
        }
        var routeEnabled = document.getElementById("wf-loop-step-route-enabled");
        var routeSelect = document.getElementById("wf-loop-step-route-select");
        if (routeEnabled) {
            routeEnabled.addEventListener("change", function () {
                renderLoopStepExecutionRouteEditor({
                    enabled: !!routeEnabled.checked,
                    mode: "scoped",
                    scoped_model_key: String((routeSelect && routeSelect.value) || "").trim(),
                    route_snapshot: loopStepExecutionRouteValue().route_snapshot || {}
                });
            });
        }
        if (routeSelect) {
            routeSelect.addEventListener("change", function () {
                renderLoopStepExecutionRouteEditor(loopStepExecutionRouteValue());
            });
        }
        if (deleteBtn) {
            deleteBtn.addEventListener("click", function () {
                if (!workflowLoopStepModalState || workflowLoopStepModalState.stepId == null) return;
                confirmDeleteStep(workflowLoopStepModalState.stepId);
            });
        }

        modal.addEventListener("click", function (evt) {
            if (evt.target === modal) onClose();
        });

        document.addEventListener("keydown", function (evt) {
            if (!isLoopStepModalOpen()) return;
            if (evt.key === "Escape") {
                evt.preventDefault();
                onClose();
                return;
            }
            if (evt.key === "Enter" && (evt.metaKey || evt.ctrlKey)) {
                evt.preventDefault();
                saveLoopStepFromModal();
            }
        });
    }

    function initWorkflowLoopViewMode() {
        try {
            // v2 intentionally resets the old ring-first preference because
            // the execution timeline is now the primary Mission Control view.
            var saved = localStorage.getItem("wf_loop_view_v2");
            if (saved === "list" || saved === "ring") workflowLoopViewMode = saved;
        } catch (e) {}
    }

    function setWorkflowLoopViewMode(mode) {
        workflowLoopViewMode = mode === "list" ? "list" : "ring";
        try { localStorage.setItem("wf_loop_view_v2", workflowLoopViewMode); } catch (e) {}
        document.querySelectorAll(".wf-loop-view-toggle").forEach(function (btn) {
            var active = (btn.dataset.loopView || "ring") === workflowLoopViewMode;
            btn.classList.toggle("active", active);
            btn.classList.toggle("text-white", active);
            btn.classList.toggle("text-gray-400", !active);
        });
        var ringView = document.getElementById("wf-loop-ring-view");
        var listView = document.getElementById("wf-loop-list-view");
        if (ringView) ringView.classList.toggle("hidden", workflowLoopViewMode !== "ring");
        if (listView) listView.classList.toggle("hidden", workflowLoopViewMode !== "list");
        if (currentWorkflow && currentWorkflowId) renderSteps(currentWorkflow.steps || []);
    }

    function syncWorkflowLoopAddStepButton(steps) {
        var btn = document.getElementById("wf-add-step-btn");
        var countEl = document.getElementById("wf-loop-step-count");
        var count = Array.isArray(steps) ? steps.length : 0;
        var atMax = count >= WORKFLOW_LOOP_MAX_STEPS;
        var runLocked = workflowHasActiveRuns();
        if (countEl) countEl.textContent = count + (count === 1 ? " phase" : " phases");
        if (!btn) return;
        btn.disabled = atMax || runLocked;
        btn.title = runLocked
            ? "Cannot add steps while a run is active"
            : (atMax
                ? ("A loop can have at most " + WORKFLOW_LOOP_MAX_STEPS + " steps")
                : "Add a step to this loop");
    }

    function addWorkflowLoopStep() {
        if (!currentWorkflowId) return;
        if (workflowHasActiveRuns()) {
            snack("Cannot add steps while a run is active", "error");
            return;
        }
        var steps = (currentWorkflow && currentWorkflow.steps) || [];
        if (steps.length >= WORKFLOW_LOOP_MAX_STEPS) {
            snack("A loop can have at most " + WORKFLOW_LOOP_MAX_STEPS + " steps", "error");
            return;
        }
        openLoopStepModal({
            mode: "create",
            steps: steps,
            defaultName: "Step " + (steps.length + 1)
        });
    }

    function stepsForLoopView(steps) {
        steps = Array.isArray(steps) ? steps.slice() : [];
        var run = currentWorkflowActiveRun() || loopFeedActiveRun();
        if (!run || !run.current_step_id) return steps;
        if (!isRunActiveForTicketContext(run)) return steps;
        return steps.map(function (step) {
            if (String(step.id) !== String(run.current_step_id)) return step;
            var status = run.status === "waiting" ? "waiting" : "running";
            if (step.status === status) return step;
            return Object.assign({}, step, { status: status });
        });
    }

    function loopStepStatusClass(status) {
        if (status === "running") return "wf-loop-step-status--running";
        if (status === "waiting") return "wf-loop-step-status--waiting";
        if (status === "passed" || status === "completed") return "wf-loop-step-status--done";
        if (status === "failed" || status === "cancelled") return "wf-loop-step-status--failed";
        return "";
    }

    function loopStepInstructionPreview(step) {
        var text = String((step && (step.instruction || step.description)) || "").replace(/\s+/g, " ").trim();
        if (!text) return "No instruction yet";
        return text.length > 72 ? text.substring(0, 72) + "…" : text;
    }

    function loopStepPurpose(step) {
        var text = String((step && (step.instruction || step.description)) || "").replace(/\s+/g, " ").trim();
        if (!text) return "No purpose has been configured for this step.";
        var sentence = text.match(/^.*?[.!?](?:\s|$)/);
        var purpose = sentence ? sentence[0].trim() : text;
        return purpose.length > 180 ? purpose.substring(0, 177) + "…" : purpose;
    }

    function loopStepTimelineGroups() {
        var groups = loopFeedBuildStepGroups(latestLoopFeedItems || []);
        var map = {};
        groups.forEach(function (group) {
            if (group.step_id != null) map[String(group.step_id)] = group;
        });
        return map;
    }

    function loopTimelineItemSubtype(item) {
        return String((item && (item.subtype || item.title)) || "").replace(/ /g, "_").toLowerCase();
    }

    function loopStepRuntime(step, index, group, run) {
        group = group || { items: [], state: "pending" };
        var items = group.items || [];
        var startedItems = items.filter(function (item) { return loopTimelineItemSubtype(item) === "workflow_step_started"; });
        var completed = items.slice().reverse().filter(function (item) { return loopTimelineItemSubtype(item) === "workflow_step_completed"; })[0] || null;
        var routeItems = items.filter(function (item) { return loopTimelineItemSubtype(item) === "route_decided"; });
        var routeItem = routeItems.length ? routeItems[routeItems.length - 1] : null;
        var validation = items.slice().reverse().filter(function (item) { return loopTimelineItemSubtype(item) === "validation_recorded"; })[0] || null;
        var isCurrent = !!(run && run.current_step_id != null && String(run.current_step_id) === String(step.id));
        var state = isCurrent ? (String(run.status || "running").toLowerCase() === "waiting" ? "waiting" : "running") : (group.state || "pending");
        var endTs = completed && completed.ts ? completed.ts : (isCurrent ? Date.now() : 0);
        var started = startedItems.slice().reverse().filter(function (item) {
            return !endTs || !item.ts || item.ts <= endTs;
        })[0] || null;
        var duration = started && started.ts && endTs ? Math.max(0, Math.floor((endTs - started.ts) / 1000)) : 0;
        var decision = routeItem && routeItem.payload && routeItem.payload.decision ? routeItem.payload.decision : {};
        if (isCurrent && run && run.execution_route) decision = run.execution_route;
        var route = [decision.backend, decision.model_provider, decision.model].filter(Boolean).join(" / ");
        var resultItem = completed || validation;
        var outcome = resultItem ? String(resultItem.body || "").trim() : "";
        if (!outcome && resultItem && resultItem.detail && resultItem.detail.length) outcome = String(resultItem.detail[0] || "").trim();
        if (outcome.length > 240) outcome = outcome.substring(0, 237) + "…";
        var nextStep = currentWorkflow && Array.isArray(currentWorkflow.steps) ? currentWorkflow.steps[index + 1] : null;
        return {
            state: state,
            isCurrent: isCurrent,
            duration: duration,
            route: route,
            rationale: decision.rationale || "",
            outcome: outcome,
            nextStep: nextStep,
            attemptCount: routeItems.length,
            itemCount: items.filter(function (item) { return !loopFeedItemIsNoise(item); }).length
        };
    }

    function loopStepStatusLabel(state) {
        var labels = {
            pending: "Not started",
            running: "Running",
            waiting: "Waiting for you",
            passed: "Passed",
            failed: "Needs correction",
            cancelled: "Cancelled",
            skipped: "Skipped"
        };
        return labels[state] || workflowRunStatusLabel(state);
    }

    function loopStepContextSummary(run) {
        var telemetry = run && run.latest_context_telemetry && typeof run.latest_context_telemetry === "object" ? run.latest_context_telemetry : {};
        var parts = [];
        if (telemetry.prior_outcome_count) parts.push(telemetry.prior_outcome_count + " prior outcomes");
        if (telemetry.memory_fact_count) parts.push(telemetry.memory_fact_count + " memory facts");
        if (telemetry.reference_count) parts.push(telemetry.reference_count + " evidence refs");
        if (telemetry.estimated_input_tokens) {
            var tokenCount = parseInt(telemetry.estimated_input_tokens, 10) || 0;
            parts.push("~" + tokenCount.toLocaleString() + " input tokens" + (telemetry.compacted ? ", compacted" : ""));
        }
        return parts.join(" · ");
    }

    function loopStepHandoffHtml(runtime, nextStep) {
        if (!nextStep || (runtime.state !== "passed" && runtime.state !== "failed")) return "";
        var verb = runtime.state === "failed" ? "Failure evidence and correction notes" : "Validated output and result packet";
        return '<div class="wf-loop-handoff" aria-label="Step handoff">' +
            '<span class="wf-loop-handoff-line"></span>' +
            '<span><strong>' + esc(verb) + '</strong> carried into ' + esc(nextStep.name || "the next step") + '</span>' +
            '<span aria-hidden="true">↓</span>' +
        '</div>';
    }

    function selectLoopStep(stepId, steps) {
        steps = steps || (currentWorkflow && currentWorkflow.steps) || [];
        var step = steps.filter(function (s) { return s.id === stepId; })[0];
        if (!step) return;
        openLoopStepModal({ mode: "edit", step: step, steps: steps });
    }

    function renderLoopRingView(steps) {
        var el = document.getElementById("wf-loop-ring-view");
        if (!el) return;
        el.innerHTML = '<div class="wf-loop-empty text-sm text-gray-500 py-10 text-center">Loading ring view…</div>';
        ensureWorkflowRingView().then(function (view) {
            if (workflowLoopViewMode !== "ring") return;
            view.render({
                element: el,
                steps: steps,
                expandedStepId: expandedStepId,
                toolIcons: loopStepToolIconsHtml,
                escape: esc,
                orchestratorIcon: SVG_ORCHESTRATOR_BOT,
                syncSelection: syncLoopStepSelectionUi
            });
        }).catch(function () {
            el.innerHTML = '<div class="wf-loop-empty text-sm text-red-300 py-10 text-center">The ring view could not be loaded. Use Timeline to continue.</div>';
        });
    }

    function ensureWorkflowRingView() {
        if (window.DecisionsWorkflowRingView) return Promise.resolve(window.DecisionsWorkflowRingView);
        if (workflowRingViewPromise) return workflowRingViewPromise;
        workflowRingViewPromise = new Promise(function (resolve, reject) {
            var script = document.createElement("script");
            script.src = "/workflows/static/js/ring_view.js";
            script.async = true;
            script.onload = function () {
                if (window.DecisionsWorkflowRingView) resolve(window.DecisionsWorkflowRingView);
                else reject(new Error("Ring view did not register"));
            };
            script.onerror = reject;
            document.head.appendChild(script);
        });
        return workflowRingViewPromise;
    }

    function renderLoopListView(steps) {
        var el = document.getElementById("wf-steps-list");
        if (!el) return;
        if (!steps.length) {
            el.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">No steps yet. Click "+ Add step" to begin.</p>';
            return;
        }
        var run = currentWorkflowActiveRun() || loopFeedActiveRun();
        var groups = loopStepTimelineGroups();
        el.innerHTML = steps.map(function (step, index) {
            var selected = expandedStepId === step.id;
            var runtime = loopStepRuntime(step, index, groups[String(step.id)], run);
            var statusCls = loopStepStatusClass(runtime.state);
            var contextSummary = runtime.isCurrent ? loopStepContextSummary(run) : "";
            var lastActivity = runtime.isCurrent && run && run.last_activity ? run.last_activity.message : "";
            var nextText = runtime.isCurrent && runtime.nextStep
                ? ("Next: " + (runtime.nextStep.name || "the next workflow step"))
                : (runtime.isCurrent ? "This is the final workflow step." : "");
            return '<div class="wf-loop-list-row-wrap flex items-stretch gap-2 mb-2" data-step-id="' + step.id + '">' +
                '<span class="wf-loop-step-drag-handle kb-ticket-list-drag-handle step-drag-grip shrink-0 self-center" title="Drag to reorder step" aria-label="Drag to reorder step">' +
                    '<svg width="10" height="16" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true">' +
                    '<circle cx="2.5" cy="2" r="1.2"/><circle cx="7.5" cy="2" r="1.2"/>' +
                    '<circle cx="2.5" cy="8" r="1.2"/><circle cx="7.5" cy="8" r="1.2"/>' +
                    '<circle cx="2.5" cy="14" r="1.2"/><circle cx="7.5" cy="14" r="1.2"/>' +
                    "</svg></span>" +
                '<div class="wf-loop-timeline-entry flex-1 min-w-0">' +
                '<button type="button" class="wf-loop-list-row wf-loop-timeline-card flex-1 min-w-0 text-left rounded-lg border ' + (selected ? "is-selected" : "") + (statusCls ? " " + statusCls : "") + '">' +
                    '<span class="wf-loop-timeline-rail"><span class="wf-loop-list-step-ball">' + (index + 1) + '</span><span class="wf-loop-timeline-connector"></span></span>' +
                    '<span class="wf-loop-timeline-content">' +
                        '<span class="wf-loop-timeline-head">' +
                            '<span class="wf-loop-timeline-title-wrap"><strong>' + esc(step.name || ("Step " + (index + 1))) + '</strong><small>' + esc(loopStepPurpose(step)) + '</small></span>' +
                            '<span class="wf-loop-timeline-status"><b>' + esc(loopStepStatusLabel(runtime.state)) + '</b>' +
                                (runtime.duration ? '<small>' + esc(formatElapsed(runtime.duration)) + '</small>' : '') +
                            '</span>' +
                        '</span>' +
                        '<span class="wf-loop-timeline-meta">' +
                            (runtime.route ? '<span class="wf-loop-route-chip">' + esc(runtime.route) + '</span>' : '') +
                            (loopStepToolIconsHtml(step, "wf-loop-list-tool-icons") || "") +
                            (runtime.attemptCount > 1 ? '<span>' + esc(runtime.attemptCount) + ' attempts</span>' : '') +
                            (runtime.itemCount ? '<span>' + esc(runtime.itemCount) + ' meaningful update' + (runtime.itemCount === 1 ? '' : 's') + '</span>' : '') +
                        '</span>' +
                        (runtime.outcome ? '<span class="wf-loop-timeline-result"><b>' + esc(runtime.state === "failed" ? "Why it needs correction" : (runtime.isCurrent ? "Latest check" : "Output")) + ':</b> ' + esc(runtime.outcome) + '</span>' : '') +
                        (runtime.isCurrent ? '<span class="wf-loop-current-work">' +
                            '<span><b>Input:</b> ' + esc(contextSummary || "Ticket scope and outputs from completed steps") + '</span>' +
                            '<span><b>Now:</b> ' + esc(lastActivity || "Waiting for the first worker update") + '</span>' +
                            '<span><b>' + esc(run && run.status === "waiting" ? "Your decision" : "After this") + ':</b> ' + esc(run && run.status === "waiting" ? (run.worker_question || "Review the requested decision before the run continues.") : nextText) + '</span>' +
                        '</span>' : '') +
                    '</span>' +
                "</button>" +
                loopStepHandoffHtml(runtime, runtime.nextStep) +
                "</div>" +
            "</div>";
        }).join("");
        syncLoopStepSelectionUi();
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
        if (pollTimer) return;
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
        if (document.hidden) return;
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + location.host + "/api/workflows/ws";
        try {
            ws = new WebSocket(url);
        } catch (e) {
            if (!wsConnectFailedLogged) {
                console.warn("Workflow WS unavailable, using polling fallback", e);
                wsConnectFailedLogged = true;
            }
            startVersionPolling();
            wsReconnectTimer = setTimeout(connectWebSocket, wsReconnectDelay);
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, 60000);
            return;
        }
        ws.onopen = function () {
            wsConnectFailedLogged = false;
            wsReconnectDelay = 5000;
            stopVersionPolling();
            if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        };
        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                if (msg.type === "workflow_updated") {
                    scheduleWorkflowLiveRefresh();
                }
            } catch (e) {
                console.warn("Workflow WS: bad message", e);
            }
        };
        ws.onclose = function () {
            ws = null;
            startVersionPolling();
            wsReconnectTimer = setTimeout(connectWebSocket, wsReconnectDelay);
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, 60000);
        };
        ws.onerror = function () {
            if (!wsConnectFailedLogged) {
                console.warn("Workflow WS unavailable, using polling fallback");
                wsConnectFailedLogged = true;
            }
            startVersionPolling();
        };
    }

    function checkActiveRun() {
        if (!currentWorkflowId) return;
        api("GET", "/workflows/" + currentWorkflowId + "/active-run").then(function (data) {
            if (data && data.id) {
                startPolling();
            } else {
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

    function applyLoopStepReorder(steps, draggedId, targetId) {
        if (workflowHasActiveRuns()) {
            snack("Cannot reorder steps while a run is active", "error");
            return;
        }
        var reordered = reorderStepsLocally(steps, draggedId, targetId);
        if (!reordered) return;
        if (currentWorkflow) currentWorkflow.steps = reordered;
        renderSteps(reordered);
        persistStepOrder(currentWorkflowId, reordered)
            .then(function () {
                snack("Loop steps reordered");
            })
            .catch(function (e) {
                snack(e.message || "Failed to reorder steps", "error");
                loadDetail(currentWorkflowId);
            });
    }

    function enableLoopListStepDragAndDrop(steps) {
        var listEl = document.getElementById("wf-steps-list");
        if (!listEl || workflowHasActiveRuns()) return;

        var draggingStepId = null;
        listEl.querySelectorAll(".wf-loop-list-row-wrap").forEach(function (wrap) {
            var stepId = parseInt(wrap.dataset.stepId, 10);
            var handle = wrap.querySelector(".wf-loop-step-drag-handle");
            var rowBtn = wrap.querySelector(".wf-loop-list-row");
            if (!handle || !stepId) return;

            handle.setAttribute("draggable", "true");
            handle.addEventListener("click", function (evt) {
                evt.stopPropagation();
            });
            handle.addEventListener("dragstart", function (evt) {
                draggingStepId = stepId;
                wrap.classList.add("wf-loop-step-dragging");
                if (evt.dataTransfer) {
                    evt.dataTransfer.effectAllowed = "move";
                    evt.dataTransfer.setData("text/plain", String(stepId));
                }
            });
            handle.addEventListener("dragend", function () {
                draggingStepId = null;
                listEl.querySelectorAll(".wf-loop-step-drop-target").forEach(function (el) {
                    el.classList.remove("wf-loop-step-drop-target");
                });
                listEl.querySelectorAll(".wf-loop-step-dragging").forEach(function (el) {
                    el.classList.remove("wf-loop-step-dragging");
                });
            });

            wrap.addEventListener("dragover", function (evt) {
                if (!draggingStepId || draggingStepId === stepId) return;
                evt.preventDefault();
                wrap.classList.add("wf-loop-step-drop-target");
                if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
            });
            wrap.addEventListener("dragleave", function () {
                wrap.classList.remove("wf-loop-step-drop-target");
            });
            wrap.addEventListener("drop", function (evt) {
                evt.preventDefault();
                wrap.classList.remove("wf-loop-step-drop-target");
                if (!draggingStepId || draggingStepId === stepId) return;
                applyLoopStepReorder(steps, draggingStepId, stepId);
            });

            if (rowBtn) {
                rowBtn.addEventListener("click", function () {
                    selectLoopStep(stepId, steps);
                });
            }
        });
    }

    function enableLoopRingStepDragAndDrop(steps) {
        var ringEl = document.getElementById("wf-loop-ring-view");
        if (!ringEl) return;
        ensureWorkflowRingView().then(function (view) {
            if (workflowLoopViewMode !== "ring") return;
            view.bind({
                element: ringEl,
                locked: workflowHasActiveRuns(),
                reorder: function (draggedId, targetId) {
                    applyLoopStepReorder(steps, draggedId, targetId);
                },
                select: function (stepId) { selectLoopStep(stepId, steps); }
            });
        });
    }

    function enableLoopStepDragAndDrop(steps) {
        if (workflowLoopViewMode === "list") {
            enableLoopListStepDragAndDrop(steps);
        } else {
            enableLoopRingStepDragAndDrop(steps);
        }
    }

    // ── Loop steps (ring + list) ──
    function renderSteps(steps) {
        steps = stepsForLoopView(Array.isArray(steps) ? steps : []);
        syncWorkflowLoopAddStepButton(steps);
        if (workflowLoopViewMode === "list") {
            renderLoopListView(steps);
        } else {
            renderLoopRingView(steps);
        }
        enableLoopStepDragAndDrop(steps);
        renderLoopRunTicketContext();
        syncLoopFeedPanelHeight();
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
            '<option value="browser_use"' + (step.action_type === "browser_use" ? " selected" : "") + '>Browser Use</option>' +
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
        html += '<div class="sf-cli-override-wrap' + (step.action_type !== "send_to_project_cli" ? " hidden" : "") + ' mt-3 rounded border border-white/10 bg-[#0d1333]/70 p-3">';
        html += '<div class="grid grid-cols-2 gap-3">';
        html += '<div><label class="block text-xs text-gray-500 mb-1">CLI backend</label>' +
            '<select class="sf-cli-backend w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            workflowStepBackendOptionsHtml(stepConfig.backend_id || "") + '</select></div>';
        html += '<div><label class="block text-xs text-gray-500 mb-1">Model</label>' +
            '<select class="sf-cli-model w-full px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-sm">' +
            '<option value="' + esc(stepConfig.model || "auto") + '">' + esc(stepConfig.model || "Auto") + '</option></select></div>';
        html += '</div>';
        html += '<p class="sf-cli-model-hint mt-1 text-xs text-gray-500">Auto follows workflow policy unless this step picks a concrete model.</p>';
        html += '<div class="mt-2 grid gap-2 sm:grid-cols-3">';
        html += '<label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" class="sf-cli-policy-auto accent-[#f97316]"' + (((stepConfig.model_policy || {}).auto_route_models !== false) ? " checked" : "") + '>Auto-route model</label>';
        html += '<label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" class="sf-cli-policy-free accent-[#f97316]"' + ((stepConfig.model_policy || {}).free_only ? " checked" : "") + '>Free/local only</label>';
        html += '<label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" class="sf-cli-policy-local accent-[#f97316]"' + (((stepConfig.model_policy || {}).prefer_local !== false) ? " checked" : "") + '>Prefer local</label>';
        html += '</div>';
        html += '</div>';
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
        if (step.action_type === "send_to_project_cli") {
            loadStepCliModels(container, stepConfig.model || "auto");
        }

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
                var cliWrap = container.querySelector(".sf-cli-override-wrap");
                if (cliWrap) cliWrap.classList.toggle("hidden", actionTypeSelect.value !== "send_to_project_cli");
                if (actionTypeSelect.value === "send_to_project_cli") loadStepCliModels(container, "auto");
                if (isDecisionAction) hydrateActionSelect(container, step);
            });
        }
        var stepCliBackend = container.querySelector(".sf-cli-backend");
        if (stepCliBackend) {
            stepCliBackend.addEventListener("change", function () {
                loadStepCliModels(container, "auto");
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
        if (actionType === "send_to_project_cli") {
            var cliBackendEl = container.querySelector(".sf-cli-backend");
            var cliModelEl = container.querySelector(".sf-cli-model");
            var backendValue = cliBackendEl ? (cliBackendEl.value || "").trim() : "";
            var modelValue = cliModelEl ? (cliModelEl.value || "auto").trim() : "auto";
            if (backendValue) config.backend_id = backendValue;
            else delete config.backend_id;
            if (modelValue) config.model = modelValue;
            else delete config.model;
            config.model_policy = {
                auto_route_models: !!((container.querySelector(".sf-cli-policy-auto") || {}).checked),
                free_only: !!((container.querySelector(".sf-cli-policy-free") || {}).checked),
                prefer_local: !!((container.querySelector(".sf-cli-policy-local") || {}).checked)
            };
        } else {
            delete config.backend_id;
            delete config.model;
            delete config.model_policy;
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
            .then(function () {
                expandedStepId = null;
                closeLoopStepModal();
                snack("Step deleted");
                loadDetail(currentWorkflowId);
            })
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
    function renderRuns(runs) {
        var el = document.getElementById("wf-runs-list");
        var empty = document.getElementById("wf-runs-empty");
        var clearBtn = document.getElementById("wf-clear-runs-btn");
        var filterHint = document.getElementById("wf-runs-filter-hint");
        if (!el || !empty) return;
        runs = (Array.isArray(runs) ? runs : []).filter(function (run) {
            var status = String(run && run.status || "").toLowerCase();
            return status !== "running" && status !== "waiting";
        });
        latestWorkflowRunHistoryAll = runs.slice();
        runs = filterRunsForSelectedTicket(runs);
        latestWorkflowRunHistory = runs;
        renderLoopRunTicketContext();
        if (filterHint) {
            if (workflowRunsFilterTicketId) {
                var ticket = workflowQueueTicketById(workflowRunsFilterTicketId);
                filterHint.classList.remove("hidden");
                filterHint.innerHTML = 'Showing runs for <strong>' + esc(ticket ? (ticket.title || ("Ticket #" + ticket.id)) : ("Ticket #" + workflowRunsFilterTicketId)) +
                    '</strong> · <button type="button" class="wf-runs-clear-filter text-[#f97316] hover:underline">Show all</button>';
            } else {
                filterHint.classList.add("hidden");
                filterHint.innerHTML = "";
            }
        }
        if (currentWorkflowId && runs.length) {
            workflowRunsSeenByWorkflowId[String(currentWorkflowId)] = true;
        }
        if (clearBtn) clearBtn.disabled = !runs.length;
        if (!runs.length) { el.innerHTML = ""; empty.classList.remove("hidden"); return; }
        empty.classList.add("hidden");
        el.innerHTML = runs.map(function (r) {
            var statusColor = { running: "text-blue-400", completed: "text-green-400", failed: "text-red-400", cancelled: "text-gray-400", waiting: "text-amber-400" }[r.status] || "text-gray-400";
            var started = r.started_at ? new Date(r.started_at).toLocaleString() : "—";
            var ended = r.completed_at ? new Date(r.completed_at).toLocaleString() : "—";
            var meta = runMetaText(r, currentWorkflow && currentWorkflow.name);
            return '<div class="wf-run-item bg-[#152054]/50 rounded px-3 py-2 border border-white/10 cursor-pointer hover:border-[#f97316]/40" data-run-id="' + r.id + '" data-ticket-id="' + esc(r.ticket_id || "") + '" title="View loop activity for this run">' +
                '<div class="flex items-center gap-3">' +
                    '<span class="text-xs text-gray-500">#' + r.id + '</span>' +
                    '<span class="text-xs ' + statusColor + ' font-medium">' + esc(r.status) + '</span>' +
                    '<span class="text-xs px-1.5 py-0.5 rounded bg-white/10 text-gray-300">' + esc(meta.sourceText) + '</span>' +
                    '<span class="text-xs text-gray-500 ml-auto">' + started + '</span>' +
                    '<span class="text-xs text-gray-600">→</span>' +
                    '<span class="text-xs text-gray-500">' + ended + '</span>' +
                    '<button type="button" class="wf-run-item-rerun px-2 py-0.5 rounded border border-[#f97316]/50 text-[#f97316] text-[10px] hover:bg-[#f97316]/10" data-ticket-id="' + esc(r.ticket_id || "") + '" title="Run this ticket again">Run again</button>' +
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
        return api("GET", "/workflows/" + currentWorkflowId + "/runs?limit=100")
            .then(function (runs) {
                renderRuns(runs || []);
                renderWorkflowBoardTimeTotal();
                return runs || [];
            })
            .catch(function (e) {
                if (!options.quiet) snack(e.message || "Failed to load run history", "error");
                return [];
            });
    }

    function syncWorkflowRunsTabVisibility() {
        var runsTabBtn = document.getElementById("wf-runs-tab-btn");
        if (!runsTabBtn) return;
        var hasActiveCurrentWorkflowRuns = workflowHasActiveRuns();
        var hasKnownRunHistory = !!(
            currentWorkflowId &&
            (
                workflowRunsSeenByWorkflowId[String(currentWorkflowId)] ||
                (latestWorkflowRunHistory || []).length
            )
        );
        runsTabBtn.classList.toggle("hidden", !(hasActiveCurrentWorkflowRuns || hasKnownRunHistory));
        if (!hasActiveCurrentWorkflowRuns && !hasKnownRunHistory) {
            var activeRunsTab = document.querySelector(".wf-tab.active[data-tab='runs']");
            if (activeRunsTab) switchTab("tickets", { persist: false });
        }
    }

    function switchRunsSubtab(tab) {
        workflowRunsSubtab = (
            tab === "sessions"
            || tab === "timeline"
            || tab === "memory"
            || tab === "history"
            || tab === "inbox"
        ) ? tab : "active";
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
        if (workflowRunsSubtab === "timeline") loadOrchestratorTimeline({ quiet: true });
        if (workflowRunsSubtab === "memory") loadWorkflowSteeringMemory({ quiet: true });
        if (workflowRunsSubtab === "history") loadWorkflowRunHistory({ quiet: true });
        if (workflowRunsSubtab === "inbox") loadWorkIntakeInbox({ quiet: true });
    }

    function loadWorkIntakeInbox(opts) {
        opts = opts || {};
        var list = document.getElementById("wf-intake-inbox-list");
        var empty = document.getElementById("wf-intake-inbox-empty");
        if (!list) return;
        fetch("/api/settings/workflows/intake/inbox?limit=40")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var items = (data && data.items) || [];
                list.innerHTML = items.map(function (item) {
                    var eventId = item.event_id;
                    var text = esc(item.text || item.response_text || "");
                    var meta = esc([item.source, item.action, item.status].filter(Boolean).join(" · "));
                    var ticket = item.ticket_id ? ("#" + item.ticket_id) : "";
                    var run = item.workflow_run_id ? ("run #" + item.workflow_run_id) : "";
                    return (
                        '<div class="rounded border border-white/10 bg-[#12183a]/70 p-3" data-event-id="' + esc(eventId) + '">' +
                        '<div class="flex items-start justify-between gap-2">' +
                        '<div class="min-w-0">' +
                        '<p class="text-xs text-gray-300 break-words">' + text + '</p>' +
                        '<p class="mt-1 text-[11px] text-gray-500">' + meta + (ticket || run ? " · " + esc([ticket, run].filter(Boolean).join(" · ")) : "") + '</p>' +
                        '</div>' +
                        '</div>' +
                        '<div class="mt-2 flex flex-wrap gap-1">' +
                        '<button type="button" class="wf-inbox-action px-2 py-1 rounded border border-white/20 text-gray-300 text-[11px] hover:bg-white/10" data-action="push">Push to Loop</button>' +
                        '<button type="button" class="wf-inbox-action px-2 py-1 rounded border border-white/20 text-gray-300 text-[11px] hover:bg-white/10" data-action="continue">Continue</button>' +
                        '<button type="button" class="wf-inbox-action px-2 py-1 rounded border border-white/20 text-gray-300 text-[11px] hover:bg-white/10" data-action="steer">Steer</button>' +
                        '<button type="button" class="wf-inbox-action px-2 py-1 rounded border border-red-500/40 text-red-300 text-[11px] hover:bg-red-500/10" data-action="stop">Stop</button>' +
                        '<button type="button" class="wf-inbox-action px-2 py-1 rounded border border-white/20 text-gray-400 text-[11px] hover:bg-white/10" data-action="dismiss">Dismiss</button>' +
                        '</div>' +
                        '</div>'
                    );
                }).join("");
                if (empty) empty.classList.toggle("hidden", items.length > 0);
                list.querySelectorAll(".wf-inbox-action").forEach(function (btn) {
                    btn.addEventListener("click", function () {
                        var card = btn.closest("[data-event-id]");
                        var eventId = card && card.getAttribute("data-event-id");
                        var action = btn.getAttribute("data-action") || "";
                        if (!eventId || !action) return;
                        function submitInboxAction(message) {
                            fetch("/api/settings/workflows/intake/inbox/" + encodeURIComponent(eventId) + "/action", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ action: action, message: message || "" })
                            }).then(function (r) { return r.json(); }).then(function (result) {
                                if (result && result.success === false) {
                                    snack(result.error || "Inbox action failed", "error");
                                    return;
                                }
                                snack("Inbox: " + action, "success");
                                loadWorkIntakeInbox({ quiet: true });
                                loadActiveRuns();
                            }).catch(function () {
                                snack("Inbox action failed", "error");
                            });
                        }
                        if (action === "steer") {
                            showInputModal({
                                title: "Steer this run",
                                message: "Tell the orchestrator what should change while preserving the ticket goal.",
                                placeholder: "Add a correction, constraint, or new direction…",
                                confirmLabel: "Send steer",
                                onConfirm: function (message) {
                                    if (String(message || "").trim()) submitInboxAction(message);
                                }
                            });
                            return;
                        }
                        submitInboxAction("");
                    });
                });
            })
            .catch(function () {
                if (!opts.quiet) snack("Could not load intake inbox", "error");
            });
    }

    function submitWorkIntakeCompose() {
        var input = document.getElementById("wf-intake-compose");
        if (!input) return;
        var text = String(input.value || "").trim();
        if (!text) return;
        fetch("/api/settings/workflows/intake/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: "web", user_text: text })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!data || data.success === false) {
                snack((data && data.error) || "Intake failed", "error");
                return;
            }
            input.value = "";
            var decision = (data && data.decision) || {};
            snack(decision.response_text || ("Intake: " + (decision.action || "ok")), "success");
            loadWorkIntakeInbox({ quiet: true });
            loadActiveRuns();
        }).catch(function () {
            snack("Intake failed", "error");
        });
    }

    function loopFeedSelectableRuns() {
        var active = currentWorkflowActiveRuns();
        var activeIds = {};
        active.forEach(function (r) { activeIds[String(r.id)] = true; });
        var history = (latestWorkflowRunHistoryAll || []).filter(function (r) {
            return r && r.id && !activeIds[String(r.id)];
        });
        var runs = active.concat(history);
        // An active run stays visible even while the operator browses tickets
        // from another board. History remains scoped to the selected ticket.
        if (active.length) {
            var selectedHistory = !selectedWorkflowQueueTicketId ? [] : history.filter(function (r) {
                return r && r.ticket_id && String(r.ticket_id) === String(selectedWorkflowQueueTicketId);
            });
            return active.concat(selectedHistory);
        }
        if (!selectedWorkflowQueueTicketId) return [];
        return runs.filter(function (r) {
            return r && r.ticket_id && String(r.ticket_id) === String(selectedWorkflowQueueTicketId);
        });
    }

    function runRecordById(runId) {
        if (!runId) return null;
        return (latestActiveRuns || []).concat(latestWorkflowRunHistoryAll || []).filter(function (r) {
            return String(r.id) === String(runId);
        })[0] || null;
    }

    function loopFeedContextRun() {
        var active = loopFeedActiveRun();
        if (active) return active;
        return runRecordById(loopFeedRunId);
    }

    function currentWorkflowActiveRuns() {
        return (latestActiveRuns || []).filter(function (r) {
            return currentWorkflowId && String(r.workflow_id) === String(currentWorkflowId);
        });
    }

    function syncSteeringRunSelect() {
        var select = document.getElementById("wf-steering-run-select");
        if (!select) return;
        var runs = currentWorkflowActiveRuns();
        var prev = workflowMemoryRunId ? String(workflowMemoryRunId) : "";
        if (!runs.length) {
            select.innerHTML = '<option value="">No active run</option>';
            workflowMemoryRunId = null;
            return;
        }
        select.innerHTML = runs.map(function (r) {
            var label = "Run #" + r.id + " · " + (r.status || "running");
            if (r.ticket_title) label += " · " + r.ticket_title;
            return '<option value="' + esc(r.id) + '">' + esc(label) + '</option>';
        }).join("");
        var ids = runs.map(function (r) { return String(r.id); });
        if (!workflowMemoryRunId || ids.indexOf(String(workflowMemoryRunId)) < 0) {
            workflowMemoryRunId = runs[0].id;
        }
        if (!loopFeedRunId || ids.indexOf(String(loopFeedRunId)) < 0) {
            loopFeedRunId = workflowMemoryRunId;
        }
        select.value = String(workflowMemoryRunId);
    }

    function steeringSourceLabel(source, eventType) {
        var src = String(source || "workflow");
        var evt = String(eventType || "feedback");
        if (src.indexOf("cursor") >= 0 || src.indexOf("codex") >= 0 || src === "ide") return "IDE";
        if (evt === "user_feedback" || evt === "continuation") return "You";
        if (evt === "harness_steer") return "Steer";
        return src;
    }

    function formatSteeringTs(ts) {
        var n = Number(ts);
        if (!n) return "";
        return new Date(n * 1000).toLocaleString();
    }

    function renderSteeringMemory(snapshot) {
        var body = document.getElementById("wf-steering-memory-body");
        if (!body) return;
        if (!snapshot || !snapshot.run_id) {
            body.innerHTML = '<p class="text-sm text-gray-500">Select a workflow with an active run to see steering memory.</p>';
            latestSteeringMemory = null;
            return;
        }
        latestSteeringMemory = snapshot;
        var log = Array.isArray(snapshot.steering_log) ? snapshot.steering_log : [];
        var rules = Array.isArray(snapshot.learned_rules) ? snapshot.learned_rules : [];
        var live = snapshot.live_agent_summary || {};
        var boardId = snapshot.board_id;

        var liveHtml = "";
        if (live.latest_user_steer || live.latest_terminal_summary || snapshot.worker_question) {
            liveHtml = '<div class="rounded border border-amber-500/25 bg-amber-500/10 p-3">' +
                '<p class="text-xs text-amber-100 font-medium">Live worker context</p>' +
                (live.latest_user_steer ? '<p class="mt-1 text-xs text-gray-300">Latest steer: ' + esc(live.latest_user_steer) + '</p>' : '') +
                (snapshot.worker_question ? '<p class="mt-1 text-xs text-gray-300">Worker asks: ' + esc(snapshot.worker_question) + '</p>' : '') +
                (live.latest_terminal_summary ? '<p class="mt-1 text-xs text-gray-400">Summary: ' + esc(live.latest_terminal_summary) + '</p>' : '') +
            '</div>';
        }

        var logHtml = log.length ? log.map(function (entry) {
            var label = steeringSourceLabel(entry.source, entry.event_type);
            return '<div class="rounded border border-white/10 bg-[#10183f] px-3 py-2">' +
                '<div class="flex items-start justify-between gap-2">' +
                    '<span class="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border border-white/15 text-gray-400">' + esc(label) + '</span>' +
                    '<span class="text-[11px] text-gray-500 flex-shrink-0">' + esc(formatSteeringTs(entry.ts)) + '</span>' +
                '</div>' +
                '<p class="mt-1 text-sm text-gray-200 whitespace-pre-wrap">' + esc(entry.message || "") + '</p>' +
            '</div>';
        }).join("") : '<p class="text-sm text-gray-500">No steering entries yet. Continue a waiting step, steer the harness, or work in Cursor/Codex during this run.</p>';

        var rulesHtml = rules.length ? rules.map(function (rule) {
            var linked = rule.run_linked ? '<span class="text-[10px] text-[#f97316]">This run</span>' : "";
            var enabled = !!rule.enabled;
            return '<div class="rounded border border-white/10 bg-[#152054]/45 px-3 py-2">' +
                '<div class="flex items-start gap-2">' +
                    '<label class="mt-0.5 flex items-center gap-2 text-xs text-gray-400 flex-shrink-0">' +
                        '<input type="checkbox" class="wf-steering-rule-toggle rounded border-white/20"' +
                            (enabled ? " checked" : "") +
                            ' data-board-id="' + esc(boardId || "") + '" data-rule-id="' + esc(rule.id) + '">' +
                        '<span>' + (enabled ? "On" : "Off") + '</span>' +
                    '</label>' +
                    '<div class="min-w-0 flex-1">' +
                        '<p class="text-sm text-gray-200">' + esc(rule.summary || "") + '</p>' +
                        '<div class="mt-1 flex flex-wrap gap-2 text-[11px] text-gray-500">' +
                            '<span>' + esc(rule.rule_type || "rule") + '</span>' +
                            (rule.evidence_count ? '<span>×' + esc(rule.evidence_count) + '</span>' : "") +
                            linked +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
        }).join("") : '<p class="text-sm text-gray-500">No board learned rules yet.</p>';

        var adaptive = (snapshot.adaptive_quality_memory || "").trim();
        var adaptiveHtml = adaptive ?
            '<details class="rounded border border-sky-500/25 bg-sky-500/5 p-3"><summary class="text-xs text-sky-200 cursor-pointer">Workflow adaptive memory</summary>' +
            '<pre class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-gray-300">' + esc(adaptive) + '</pre></details>' : "";

        var preview = (snapshot.prompt_preview || "").trim();
        var previewHtml = preview ?
            '<details class="rounded border border-white/10 bg-[#0d1333]/60 p-3"><summary class="text-xs text-gray-300 cursor-pointer">What the next step sees</summary>' +
            '<pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-gray-400 font-mono">' + esc(preview) + '</pre></details>' : "";

        body.innerHTML =
            (liveHtml ? liveHtml : "") +
            '<div><p class="text-xs font-medium text-white mb-2">Steering log</p><div class="space-y-2">' + logHtml + '</div></div>' +
            '<div class="mt-3"><p class="text-xs font-medium text-white mb-2">Board learned rules</p><div class="space-y-2">' + rulesHtml + '</div></div>' +
            adaptiveHtml +
            previewHtml;

        body.querySelectorAll(".wf-steering-rule-toggle").forEach(function (input) {
            input.addEventListener("change", function () {
                toggleSteeringLearnedRule(input.dataset.boardId, input.dataset.ruleId, input.checked);
            });
        });
    }

    function toggleSteeringLearnedRule(boardId, ruleId, enabled) {
        if (!boardId || !ruleId) return;
        api("PATCH", "/tickets/boards/" + encodeURIComponent(boardId) + "/orchestrator-learned-rules/" + encodeURIComponent(ruleId), {
            enabled: !!enabled
        }).then(function () {
            snack(enabled ? "Rule enabled for routing" : "Rule disabled", "success");
            loadWorkflowSteeringMemory({ quiet: true });
        }).catch(function (e) {
            snack(e.message || "Failed to update rule", "error");
            loadWorkflowSteeringMemory({ quiet: true });
        });
    }

    function loadWorkflowSteeringMemory(options) {
        options = options || {};
        syncSteeringRunSelect();
        if (!currentWorkflowId || !workflowMemoryRunId) {
            renderSteeringMemory(null);
            return Promise.resolve(null);
        }
        return api("GET", "/workflows/" + currentWorkflowId + "/runs/" + workflowMemoryRunId + "/steering-memory")
            .then(function (snapshot) {
                renderSteeringMemory(snapshot);
                return snapshot;
            })
            .catch(function (e) {
                renderSteeringMemory(null);
                if (!options.quiet) snack(e.message || "Failed to load steering memory", "error");
                return null;
            });
    }

    function clearWorkflowRunAudit() {
        if (!currentWorkflowId) return;
        showConfirmModal({
            title: "Clear history",
            message: "Clear completed run history for this workflow?\n\nThis removes finished run records so you can start fresh. Active runs, executor logs, and event streams are not removed.",
            confirmLabel: "Clear",
            onConfirm: function () {
                var btn = document.getElementById("wf-clear-runs-btn");
                if (btn) btn.disabled = true;
                api("DELETE", "/workflows/" + currentWorkflowId + "/runs")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Activity log cleared"));
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
                        refreshWorkflowCliTabIfVisible();
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
                var btn = document.getElementById("wf-clear-orchestrator-events");
                if (btn) btn.disabled = true;
                api("DELETE", "/workflows/" + currentWorkflowId + "/events")
                    .then(function (data) {
                        snack(workflowFeedbackText(data, "Workflow events cleared"));
                        loadOrchestratorTimeline();
                    })
                    .catch(function (e) {
                        if (btn) btn.disabled = false;
                        snack(workflowErrorText(e, "Failed to clear workflow events"), "error");
                    });
            },
        });
    }

    // ── Loop activity feed (steering + run events) ──
    function isLoopTabVisible() {
        var tab = document.getElementById("wf-tab-loop");
        return !!(tab && !tab.classList.contains("hidden"));
    }

    function syncLoopFeedPanelHeight() {
        if (!isLoopTabVisible()) return;
        var scroll = document.getElementById("wf-detail-tab-scroll");
        var layout = document.querySelector("#wf-tab-loop .wf-loop-layout");
        var panel = document.getElementById("wf-loop-feed-panel");
        if (!layout || !panel) return;
        if (layout.style.minHeight) layout.style.minHeight = "";
        if (panel.style.minHeight) panel.style.minHeight = "";
    }

    function isLoopFeedExpanded() {
        var layout = document.querySelector("#wf-tab-loop .wf-loop-layout");
        return !!(layout && layout.classList.contains("wf-loop-layout--feed-expanded"));
    }

    function syncLoopFeedExpandUi() {
        var expanded = isLoopFeedExpanded();
        var layout = document.querySelector("#wf-tab-loop .wf-loop-layout");
        var scroll = document.getElementById("wf-detail-tab-scroll");
        var btn = document.getElementById("wf-loop-feed-expand-btn");
        if (scroll) scroll.classList.toggle("wf-tab-scroll--feed-expanded", expanded);
        if (btn) {
            btn.setAttribute("aria-expanded", expanded ? "true" : "false");
            btn.title = expanded ? "Collapse activity feed" : "Expand activity feed";
            btn.setAttribute("aria-label", expanded ? "Collapse activity feed" : "Expand activity feed");
        }
        if (layout && expanded) layout.style.minHeight = "";
    }

    function setLoopFeedExpanded(expanded, options) {
        options = options || {};
        var layout = document.querySelector("#wf-tab-loop .wf-loop-layout");
        if (!layout) return;
        layout.classList.toggle("wf-loop-layout--feed-expanded", !!expanded);
        syncLoopFeedExpandUi();
        if (options.persist !== false) {
            try {
                localStorage.setItem("wf_loop_feed_expanded", expanded ? "1" : "0");
            } catch (e) { /* ignore */ }
        }
        syncLoopFeedPanelHeight();
    }

    function toggleLoopFeedExpanded() {
        setLoopFeedExpanded(!isLoopFeedExpanded());
    }

    function restoreLoopFeedExpandedState() {
        try {
            setLoopFeedExpanded(localStorage.getItem("wf_loop_feed_expanded") === "1", { persist: false });
        } catch (e) {
            syncLoopFeedExpandUi();
        }
    }

    function loopFeedActiveRun() {
        if (!loopFeedRunId) return null;
        return (latestActiveRuns || []).filter(function (r) {
            return String(r.id) === String(loopFeedRunId);
        })[0] || null;
    }

    function syncLoopFeedRunSelect() {
        var select = document.getElementById("wf-loop-feed-run-select");
        var runs = loopFeedSelectableRuns();
        if (!loopFeedRunId || !runs.some(function (r) { return String(r.id) === String(loopFeedRunId); })) {
            var active = loopFeedSelectableRuns();
            loopFeedRunId = active.length ? active[0].id : (runs.length ? runs[0].id : null);
        }
        if (select) {
            if (!runs.length) {
                select.classList.add("hidden");
                select.innerHTML = "";
            } else {
                select.classList.toggle("hidden", runs.length <= 1);
                select.innerHTML = runs.map(function (r) {
                    var label = "Run #" + r.id;
                    if (r.ticket_title) label += " · " + r.ticket_title;
                    if (r.current_step_name) label += " · " + r.current_step_name;
                    else if (r.status) label += " · " + r.status;
                    return '<option value="' + esc(r.id) + '">' + esc(label) + "</option>";
                }).join("");
                select.value = String(loopFeedRunId || runs[0].id);
            }
        }
        renderLoopRunTicketContext();
        updateLoopFeedComposeState();
    }

    function updateLoopFeedComposeState() {
        var input = document.getElementById("wf-loop-feed-input");
        var btn = document.getElementById("wf-loop-feed-steer-btn");
        var run = loopFeedActiveRun();
        var enabled = !!(currentWorkflowId && run && (run.status === "running" || run.status === "waiting"));
        if (input) {
            input.disabled = !enabled;
            input.placeholder = enabled
                ? (run.status === "waiting" ? "Reply to continue the run…" : "Steer the active run…")
                : "Start a ticket run to steer from here";
        }
        if (btn) btn.disabled = !enabled;
    }

    function loopFeedTimestamp(value) {
        if (!value) return 0;
        var n = Date.parse(value);
        if (!isNaN(n)) return n;
        var num = parseFloat(value);
        return isNaN(num) ? 0 : (num > 1e12 ? num : num * 1000);
    }

    function loopFeedKindForEvent(event) {
        var type = String((event && event.event_type) || "").toLowerCase();
        if (type.indexOf("preflight") >= 0) return "event";
        if (type.indexOf("step") >= 0 || type === "loop_iteration" || type === "loop_started") return "step";
        if (type.indexOf("wait") >= 0 || type === "approval_requested" || type === "route_approval_granted") return "waiting";
        if (type.indexOf("steer") >= 0 || type.indexOf("feedback") >= 0) return "steer";
        return "event";
    }

    function loopFeedFormatEventSummary(event) {
        var type = String((event && event.event_type) || "").toLowerCase();
        var subtype = String((event && (event.legacy_event_type || event.subtype)) || "").toLowerCase();
        var summary = String((event && event.summary) || "").trim();
        var payload = event && event.payload && typeof event.payload === "object" ? event.payload : {};
        var evidence = event && event.evidence && typeof event.evidence === "object" ? event.evidence : {};
        var validation = evidence.validation && typeof evidence.validation === "object" ? evidence.validation : {};
        var status = String((event && event.status) || payload.decision || "").toLowerCase();
        if (subtype === "provider_failover") {
            var failedBackend = String(payload.failed_backend || "the first worker").toLowerCase();
            var fallbackBackend = String(payload.fallback_backend || "another worker").toLowerCase();
            var failedLabel = (failedBackend === "pi" || failedBackend === "ollama") ? "The local model" : failedBackend;
            var fallbackLabel = fallbackBackend === "codex" ? "Codex" :
                (fallbackBackend === "claude_code" ? "Claude Code" : fallbackBackend);
            return failedLabel.charAt(0).toUpperCase() + failedLabel.slice(1) +
                " couldn't complete this step, so the orchestrator switched to " +
                fallbackLabel + " and continued automatically.";
        }
        if (type === "workflow_step_completed") {
            var stepName = payload.step_name || "Step";
            if (status === "failed" || status === "fail" || validation.verdict === "fail") {
                var reason = validation.correction_hint || payload.correction_hint || "";
                if (!reason && validation.expected) {
                    reason = "Expected: " + validation.expected;
                }
                return stepName + " failed" + (reason ? ". " + reason : ".");
            }
            return stepName + " passed.";
        }
        if (summary) {
            // Worker commentary is useful, but backend prefixes are not part of
            // the human update shown in Mission Control.
            return summary.replace(/^(?:codex|claude|pi)\s+/i, "");
        }
        if (type === "workflow_run_started") return "Run started.";
        if (type === "workflow_run_completed") return "Run finished.";
        if (type === "workflow_run_cancelled") return "Run cancelled.";
        if (type === "loop_started") return "Loop started.";
        if (type === "loop_iteration") {
            var iter = payload.iteration != null ? payload.iteration : payload.loop_iteration;
            return iter != null ? ("Loop iteration " + iter) : "Loop iteration advanced.";
        }
        if (type === "harness_steer") return payload.message || "Steering sent to project agent.";
        return event.event_type || "Run update";
    }

    function loopFeedEventDetailLines(event) {
        var payload = event && event.payload && typeof event.payload === "object" ? event.payload : {};
        var evidence = event && event.evidence && typeof event.evidence === "object" ? event.evidence : {};
        var validation = evidence.validation && typeof evidence.validation === "object" ? evidence.validation : {};
        var lines = [];
        if (payload.step_name) lines.push("Step: " + payload.step_name);
        if (payload.action_type) {
            var executorLabels = {
                send_to_project_cli: "Project coding agent",
                agent_instruction: "Orchestrator agent",
                playwright: "Browser validation",
                run_command: "Project command"
            };
            lines.push("Worker: " + (executorLabels[payload.action_type] || String(payload.action_type).replace(/_/g, " ")));
        }
        if (validation.validation_type) lines.push("Check: " + validation.validation_type);
        if (validation.verdict === "fail" && validation.expected) {
            lines.push("Expected: " + validation.expected);
        }
        var orch = validation.orchestrator_validator && typeof validation.orchestrator_validator === "object"
            ? validation.orchestrator_validator
            : (payload.orchestrator_validator || {});
        if (orch.correction_hint) lines.push(orch.correction_hint);
        else if (orch.explanation) lines.push(orch.explanation);
        if (payload.decision && payload.decision.backend) {
            lines.push("Route: " + payload.decision.backend + (payload.decision.model ? " / " + payload.decision.model : ""));
            var decisionSkills = workflowCleanStringList(payload.decision.skills);
            if (decisionSkills.length) lines.push("Skills: " + decisionSkills.join(", "));
        }
        if (payload.backend_id || payload.model) {
            lines.push("Route: " + (payload.backend_id || payload.route_backend || "backend") + (payload.model ? " / " + payload.model : ""));
        }
        if (payload.preflight && typeof payload.preflight === "object") {
            var pf = payload.preflight;
            if (pf.state) lines.push("Setup: " + pf.state);
            if (pf.message) lines.push(pf.message);
            if (pf.setup_instructions) lines.push(pf.setup_instructions);
        }
        if (payload.route_backend) lines.push("Backend: " + payload.route_backend);
        var payloadSkills = workflowCleanStringList(payload.skills);
        var payloadTools = workflowCleanStringList(payload.tools);
        var payloadContext = workflowCleanStringList(payload.context);
        if (payloadSkills.length) lines.push("Skills: " + payloadSkills.join(", "));
        if (payloadTools.length) lines.push("Tools: " + payloadTools.join(", "));
        if (payloadContext.length) lines.push("Context: " + payloadContext.join(", "));
        if (payload.correction_hint) lines.push(payload.correction_hint);
        if (evidence.result_preview) lines.push(evidence.result_preview);
        if (evidence.error) lines.push("Error: " + evidence.error);
        return lines.filter(Boolean).slice(0, 8);
    }

    function steeringEntryToFeedItem(entry) {
        if (!entry || !entry.message) return null;
        var run = loopFeedActiveRun();
        return {
            id: "steer-" + String(entry.ts || entry.created_at || entry.message).slice(0, 48),
            kind: "steer",
            title: steeringSourceLabel(entry.source, entry.event_type),
            body: String(entry.message || "").trim(),
            ts: loopFeedTimestamp(entry.ts ? entry.ts * 1000 : entry.created_at),
            detail: [],
            step_id: entry.step_id || (run && run.current_step_id) || null,
            step_name: entry.step_name || (run && run.current_step_name) || ""
        };
    }

    function mergeLoopFeedItems(events, steeringLog, optimisticSteers) {
        var items = [];
        (events || []).forEach(function (event, idx) {
            var payload = event && event.payload && typeof event.payload === "object" ? event.payload : {};
            items.push({
                id: "evt-" + String(event.id || idx),
                kind: loopFeedKindForEvent(event),
                title: String(event.subtype || event.legacy_event_type || event.event_type || "event").replace(/_/g, " "),
                body: loopFeedFormatEventSummary(event),
                ts: loopFeedTimestamp(event.created_at),
                detail: loopFeedEventDetailLines(event),
                event_type: String(event.event_type || ""),
                subtype: String(event.subtype || event.legacy_event_type || ""),
                status: String(event.status || ""),
                step_id: event.step_id || payload.step_id || null,
                step_name: payload.step_name || event.step_name || "",
                payload: payload,
                evidence: event && event.evidence && typeof event.evidence === "object" ? event.evidence : {}
            });
        });
        (steeringLog || []).forEach(function (entry) {
            var item = steeringEntryToFeedItem(entry);
            if (item) items.push(item);
        });
        (optimisticSteers || []).forEach(function (entry) {
            items.push(entry);
        });
        items.sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); });
        return items;
    }

    function loopFeedStepState(group, activeRun) {
        var currentStepId = activeRun && activeRun.current_step_id != null ? String(activeRun.current_step_id) : "";
        if (group.step_id && currentStepId && String(group.step_id) === currentStepId) {
            return activeRun.status === "waiting" ? "waiting" : "running";
        }
        var events = group.items || [];
        var completed = events.slice().reverse().filter(function (item) {
            return String(item.subtype || item.title || "").replace(/ /g, "_").toLowerCase() === "workflow_step_completed";
        })[0];
        if (completed) {
            var completedStatus = String(completed.status || "").toLowerCase();
            return completedStatus === "failed" || completedStatus === "error" || completedStatus === "fail" ? "failed" : "passed";
        }
        var failed = events.some(function (item) {
            var status = String(item.status || "").toLowerCase();
            var type = String(item.event_type || "").toLowerCase();
            var subtype = String(item.subtype || "").toLowerCase();
            return type === "worker_failed" || subtype === "worker_failed" || subtype === "workflow_step_failed" ||
                ((status === "failed" || status === "error") && subtype.indexOf("preflight") >= 0);
        });
        if (failed) return "failed";
        return "pending";
    }

    function loopFeedBuildStepGroups(items) {
        var activeRun = loopFeedActiveRun();
        var workflowSteps = currentWorkflow && Array.isArray(currentWorkflow.steps) ? currentWorkflow.steps : [];
        var groups = [];
        var byKey = {};
        workflowSteps.forEach(function (step, idx) {
            var key = step && step.id != null ? String(step.id) : ("position-" + idx);
            var group = {
                key: key,
                step_id: step && step.id != null ? step.id : null,
                index: idx + 1,
                title: (step && (step.name || step.title)) || ("Step " + (idx + 1)),
                items: []
            };
            byKey[key] = group;
            groups.push(group);
        });
        (items || []).forEach(function (item) {
            var key = item && item.step_id != null ? String(item.step_id) : "run";
            var group = byKey[key];
            if (!group) {
                // Run-level lifecycle events belong in the summary/transcript,
                // not in the phase rail. Creating a synthetic "Run activity"
                // phase made a seven-step workflow appear to have eight steps.
                if (key === "run" && workflowSteps.length) return;
                group = {
                    key: key,
                    step_id: key === "run" ? null : item.step_id,
                    index: groups.length + 1,
                    title: item.step_name || (key === "run" ? "Run activity" : ("Step " + key)),
                    items: []
                };
                byKey[key] = group;
                groups.push(group);
            } else if (!group.title && item.step_name) {
                group.title = item.step_name;
            }
            group.items.push(item);
        });
        groups.forEach(function (group) {
            group.state = loopFeedStepState(group, activeRun);
            group.open = group.state === "running" || group.state === "waiting" || group.items.length > 0;
            if (group.state === "pending" && group.items.length === 0) group.open = false;
        });
        return groups.filter(function (group) {
            return group.items.length || group.state === "running" || group.state === "waiting" || workflowSteps.length;
        });
    }

    function loopFeedRenderItem(item) {
        var when = item.ts ? new Date(item.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
        var detailHtml = (item.detail || []).length
            ? '<div class="wf-loop-feed-msg-detail">' + item.detail.map(function (line) { return esc(line); }).join("<br>") + "</div>"
            : "";
        return '<div class="wf-loop-feed-msg wf-loop-feed-msg--' + esc(item.kind || "event") + '">' +
            '<div class="wf-loop-feed-msg-meta">' + esc(item.title || "Update") + (when ? " · " + esc(when) : "") + "</div>" +
            '<div class="wf-loop-feed-msg-bubble">' + esc(item.body || "") + detailHtml + "</div>" +
        "</div>";
    }

    function loopFeedStepGroupHtml(group, run) {
        var state = group.state || "pending";
        var seen = {};
        var useful = (group.items || []).filter(function (item) {
            if (loopFeedItemIsNoise(item)) return false;
            var key = [item.title || "", item.body || "", (item.detail || []).join("|")].join("::");
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
        // A phase should explain its decisions and outcome without becoming a
        // second terminal. Older diagnostics remain available in the transcript.
        useful = useful.slice(-6);
        var body = useful.length
            ? useful.map(loopFeedRenderItem).join("")
            : '<p class="wf-loop-feed-step-empty">' +
                (state === "pending" ? "Waiting for the previous phase to pass." : "Waiting for a meaningful worker update.") +
              "</p>";
        var open = state === "running" || state === "waiting";
        return '<details class="wf-loop-feed-step wf-loop-feed-step--' + esc(state) + '" data-step-id="' + esc(group.step_id || "") + '"' + (open ? " open" : "") + '>' +
            '<summary class="wf-loop-feed-step-summary">' +
                '<span class="wf-loop-feed-step-index">' + esc(group.index || "") + "</span>" +
                '<span class="wf-loop-feed-step-title" title="' + esc(group.title || "") + '">' + esc(group.title || "Workflow phase") + "</span>" +
                '<span class="wf-loop-feed-step-state">' + esc(loopStepStatusLabel(state)) + "</span>" +
            "</summary>" +
            '<div class="wf-loop-feed-step-body">' + body + "</div>" +
        "</details>";
    }

    function loopFeedProgressRailHtml(groups) {
        return '<div class="wf-loop-feed-progress-rail" aria-label="Workflow phase status">' +
            (groups || []).map(function (group) {
                var state = group.state || "pending";
                return '<span class="wf-loop-feed-progress-dot wf-loop-feed-progress-dot--' + esc(state) + '" title="' +
                    esc((group.index || "") + ". " + (group.title || "Workflow phase") + " — " + loopStepStatusLabel(state)) + '">' +
                    esc(group.index || "") + "</span>";
            }).join("") +
        "</div>";
    }

    function loopTranscriptSubtype(event) {
        return String((event && (event.subtype || event.legacy_event_type || event.event_type)) || "event").toLowerCase();
    }

    function loopTranscriptKind(event) {
        var type = loopTranscriptSubtype(event);
        if (type.indexOf("handoff") >= 0 || type.indexOf("prompt") >= 0 || type === "message_end") return "prompt";
        if (type.indexOf("route") >= 0 || type.indexOf("preflight") >= 0 || type.indexOf("coordination_plan") >= 0) return "route";
        if (type.indexOf("command") >= 0 || type.indexOf("tool") >= 0 || type.indexOf("skill") >= 0) return "tool";
        if (type.indexOf("validation") >= 0 || type.indexOf("review") >= 0) return "check";
        if (type.indexOf("failover") >= 0 || type.indexOf("retry") >= 0 || type.indexOf("iteration") >= 0) return "retry";
        if (type.indexOf("completed") >= 0 || type.indexOf("output") >= 0 || type.indexOf("message_update") >= 0) return "output";
        return "event";
    }

    function loopTranscriptFind(value, keys, depth) {
        if (!value || depth > 5) return null;
        if (typeof value !== "object") return null;
        var i;
        for (i = 0; i < keys.length; i += 1) {
            if (Object.prototype.hasOwnProperty.call(value, keys[i])) {
                var direct = value[keys[i]];
                if (direct !== null && direct !== undefined && direct !== "") return direct;
            }
        }
        var values = Array.isArray(value) ? value : Object.keys(value).map(function (key) { return value[key]; });
        for (i = 0; i < values.length; i += 1) {
            var found = loopTranscriptFind(values[i], keys, depth + 1);
            if (found !== null && found !== undefined && found !== "") return found;
        }
        return null;
    }

    function loopTranscriptLabel(value) {
        return String(value || "")
            .replace(/_/g, " ")
            .replace(/\b\w/g, function (char) { return char.toUpperCase(); });
    }

    function loopTranscriptText(value, depth) {
        depth = depth || 0;
        if (value === null || value === undefined || value === "") return "";
        if (typeof value === "string") return value;
        if (typeof value !== "object") return String(value);
        if (depth > 3) return "More detail is available in developer data.";
        if (Array.isArray(value)) {
            return value.map(function (item) {
                var rendered = loopTranscriptText(item, depth + 1);
                return rendered ? "• " + rendered.replace(/\n/g, "\n  ") : "";
            }).filter(Boolean).join("\n");
        }
        return Object.keys(value).map(function (key) {
            var rendered = loopTranscriptText(value[key], depth + 1);
            if (!rendered) return "";
            return loopTranscriptLabel(key) + ": " + rendered.replace(/\n/g, "\n  ");
        }).filter(Boolean).join("\n");
    }

    function loopTranscriptBlock(label, value) {
        var textValue = loopTranscriptText(value);
        if (!textValue) return "";
        return '<div class="wf-loop-transcript-block"><span>' + esc(label) + '</span><pre>' + esc(textValue) + '</pre></div>';
    }

    function loopTranscriptRecordHtml(event, index) {
        var payload = event && event.payload && typeof event.payload === "object" ? event.payload : {};
        var evidence = event && event.evidence && typeof event.evidence === "object" ? event.evidence : {};
        var kind = loopTranscriptKind(event);
        var subtype = loopTranscriptLabel(loopTranscriptSubtype(event));
        var when = event && event.created_at ? new Date(event.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
        var prompt = loopTranscriptFind(payload, ["instruction", "prompt", "user_text", "request_text"], 0);
        var command = loopTranscriptFind(payload, ["command", "argv", "tool_call"], 0);
        var output = loopTranscriptFind(evidence, ["output", "stdout", "stderr", "result_preview", "result", "response"], 0) ||
            loopTranscriptFind(payload, ["output", "stdout", "stderr", "result_preview", "response"], 0);
        var decision = payload.decision && typeof payload.decision === "object" ? payload.decision : {};
        var route = [decision.backend || payload.backend_id || payload.route_backend, decision.model || payload.model].filter(Boolean).join(" / ");
        var skills = workflowCleanStringList(decision.skills || payload.skills);
        var tools = workflowCleanStringList(decision.tools || payload.tools);
        var validation = evidence.validation && typeof evidence.validation === "object" ? evidence.validation : null;
        var coordinationPlan = payload.coordination_plan && typeof payload.coordination_plan === "object" ? payload.coordination_plan : null;
        var coordinationRevision = payload.revision && typeof payload.revision === "object" ? payload.revision : null;
        var raw = { payload: payload, evidence: evidence };
        var runKey = String(loopFeedRunId || "");
        var recordKey = String((event && event.id) || index);
        var recordOpen = !!(loopTranscriptRecordOpenByRun[runKey] && loopTranscriptRecordOpenByRun[runKey][recordKey]);
        var blocks = "";
        blocks += loopTranscriptBlock("Prompt / handoff", prompt);
        blocks += loopTranscriptBlock("Route", route);
        blocks += loopTranscriptBlock("Skills", skills.length ? skills.join(", ") : "");
        blocks += loopTranscriptBlock("Tools", tools.length ? tools.join(", ") : "");
        blocks += loopTranscriptBlock("Command / tool call", command);
        blocks += loopTranscriptBlock("Worker output", output);
        blocks += loopTranscriptBlock("Validation", validation);
        blocks += loopTranscriptBlock("Whole-run allocation", coordinationPlan);
        blocks += loopTranscriptBlock("Plan revision", coordinationRevision);
        if (payload.version_pin || payload.blueprint || payload.power_budget) {
            blocks += loopTranscriptBlock("Version pin / blueprint", {
                version_pin: payload.version_pin || null,
                blueprint: payload.blueprint || null,
                power_budget: payload.power_budget || null,
                interrupt: payload.interrupt || null
            });
        }
        var kindLabels = { prompt: "Input", route: "Route", tool: "Tool", output: "Output", check: "Check", retry: "Retry", event: "Event" };
        return '<details class="wf-loop-transcript-record wf-loop-transcript-record--' + esc(kind) + '" data-record-key="' + esc(recordKey) + '"' + (recordOpen ? " open" : "") + '>' +
            '<summary><span class="wf-loop-transcript-seq">' + esc(index + 1) + '</span><b>' + esc(kindLabels[kind] || "Event") + '</b><span class="wf-loop-transcript-event">' + esc(subtype) + '</span><time>' + esc(when) + '</time></summary>' +
            '<div class="wf-loop-transcript-record-body">' +
                (event.summary ? '<p class="wf-loop-transcript-summary">' + esc(event.summary) + '</p>' : '') +
                blocks +
                '<details class="wf-loop-transcript-raw"><summary>Developer data (JSON)</summary><pre>' + esc(JSON.stringify(raw, null, 2)) + '</pre></details>' +
            '</div>' +
        '</details>';
    }

    function renderLoopExecutionTranscript(run) {
        if (!run || !run.id) return "";
        var key = String(run.id);
        var events = loopTranscriptEventsByRun[key];
        var open = !!loopTranscriptOpenByRun[key];
        var loading = !!loopTranscriptLoadingByRun[key];
        var label = events ? (events.length + " events") : (loading ? "Loading…" : "Prompt, tools, commands and output");
        var blueprintStrip = "";
        var blueprint = loopTranscriptBlueprintByRun[key] || run.blueprint || {};
        if (blueprint && (blueprint.orchestration_strategy || blueprint.version_pin || blueprint.power_budget || blueprint.drift)) {
            blueprintStrip = '<div class="wf-loop-transcript-blueprint">' + renderBlueprintAdherencePanel(blueprint) + '</div>';
        }
        var body = events
            ? (events.length ? blueprintStrip + events.map(loopTranscriptRecordHtml).join("") : blueprintStrip + '<p class="wf-loop-transcript-empty">No execution events have been recorded yet.</p>')
            : '<p class="wf-loop-transcript-empty">Open this transcript to load the complete, secret-redacted execution trail.</p>';
        return '<details class="wf-loop-transcript" data-run-id="' + esc(key) + '"' + (open ? " open" : "") + '>' +
            '<summary><span><b>Execution transcript</b><small>' + esc(label) + '</small></span><span class="wf-loop-transcript-cli">CLI detail</span></summary>' +
            '<div class="wf-loop-transcript-body">' + body + '</div>' +
        '</details>';
    }

    function bindLoopExecutionTranscript(run) {
        var root = document.querySelector(".wf-loop-transcript[data-run-id]");
        if (!root || !run || !run.id) return;
        root.addEventListener("toggle", function () {
            var key = String(run.id);
            loopTranscriptOpenByRun[key] = !!root.open;
            if (root.open) loadLoopExecutionTranscript(run.id, { quiet: true });
        });
        root.querySelectorAll(".wf-loop-transcript-record[data-record-key]").forEach(function (record) {
            record.addEventListener("toggle", function () {
                var key = String(run.id);
                loopTranscriptRecordOpenByRun[key] = loopTranscriptRecordOpenByRun[key] || {};
                loopTranscriptRecordOpenByRun[key][record.dataset.recordKey] = !!record.open;
            });
        });
    }

    function loadLoopExecutionTranscript(runId, options) {
        options = options || {};
        if (!currentWorkflowId || !runId) return Promise.resolve([]);
        var key = String(runId);
        var loadedAt = Number(loopTranscriptLoadedAtByRun[key] || 0);
        if (loopTranscriptLoadingByRun[key]) return loopTranscriptLoadingByRun[key];
        if (!options.force && loopTranscriptEventsByRun[key] && Date.now() - loadedAt < 15000) {
            return Promise.resolve(loopTranscriptEventsByRun[key]);
        }
        loopTranscriptLoadingByRun[key] = api("GET", "/workflows/" + currentWorkflowId + "/runs/" + runId + "/timeline?limit=500&detail=true")
            .then(function (resp) {
                var events = resp && Array.isArray(resp.events) ? resp.events : [];
                loopTranscriptEventsByRun[key] = events;
                if (resp && resp.blueprint && typeof resp.blueprint === "object") {
                    loopTranscriptBlueprintByRun[key] = resp.blueprint;
                }
                loopTranscriptLoadedAtByRun[key] = Date.now();
                return events;
            })
            .catch(function (e) {
                if (!options.quiet) snack(e.message || "Failed to load execution transcript", "error");
                return loopTranscriptEventsByRun[key] || [];
            })
            .finally(function () {
                delete loopTranscriptLoadingByRun[key];
                if (String(loopFeedRunId || "") === key) renderLoopActivityFeed(latestLoopFeedItems || []);
            });
        return loopTranscriptLoadingByRun[key];
    }

    function loopFeedItemIsNoise(item) {
        var type = String((item && item.event_type) || "").toLowerCase();
        var title = String((item && item.title) || "").toLowerCase();
        var body = String((item && item.body) || "").toLowerCase();
        if (type === "workflow_run_started" || type === "user_notified") return true;
        if (title === "workflow run started" || title === "user notified") return true;
        if ([
            "workflow step preflight",
            "skill provisioned",
            "execution session created",
            "execution preflight",
            "backend handoff created",
            "backend handoff updated",
            "project runtime snapshot",
            "execution executor start",
            "execution backend started",
            "execution agent start",
            "execution agent end",
            "execution backend finished",
            "execution session completed",
            "project handoff dispatched",
            "execution dispatched"
        ].indexOf(title) >= 0) return true;
        // Keep the durable event ledger complete, while the primary activity
        // feed shows decisions and outcomes instead of transport plumbing.
        if (title === "execution message start" || title === "execution message end" ||
            title === "execution command start" || title === "execution heartbeat") return true;
        if (title === "execution message update" || title === "execution turn start" ||
            title === "execution turn end" || title === "execution tool execution start" ||
            title === "execution tool execution end") return true;
        return false;
    }

    function loopFeedHumanActivity(run) {
        var lastActivity = run && run.last_activity ? run.last_activity : {};
        var type = String(lastActivity.event_type || lastActivity.type || "").toLowerCase();
        var message = String(lastActivity.message || "").trim();
        if (type.indexOf("tool_execution") >= 0) return "The worker is inspecting project evidence with its tools.";
        if (type.indexOf("turn_") >= 0 || type.indexOf("message_") >= 0) {
            return "The worker is reasoning over the ticket, project files, and prior evidence.";
        }
        if (!message) return "Waiting for the first worker update.";
        var looksStructured = /^\s*[\[{]/.test(message) ||
            /thinkingSignature|reasoning_details|toolCall|['\"]role['\"]\s*:/.test(message);
        if (looksStructured) return "The worker is reasoning over the ticket, project files, and prior evidence.";
        return message;
    }

    function loopFeedActiveStepMeta(run) {
        var workflowSteps = currentWorkflow && Array.isArray(currentWorkflow.steps) ? currentWorkflow.steps : [];
        var idx = -1;
        var step = null;
        if (run && run.current_step_id != null) {
            workflowSteps.some(function (candidate, i) {
                if (String(candidate.id) === String(run.current_step_id)) {
                    idx = i;
                    step = candidate;
                    return true;
                }
                return false;
            });
        }
        return {
            index: idx >= 0 ? idx + 1 : "",
            title: (step && (step.name || step.title)) || (run && run.current_step_name) || "Current step",
            status: (run && run.status) || (step && step.status) || "running"
        };
    }

    function loopFeedHumanState(run, meta) {
        var status = String((run && run.status) || "running").toLowerCase();
        var route = (run && run.execution_route) || {};
        var worker = [route.backend, route.model_provider, route.model].filter(Boolean).join(" / ") || "the selected worker";
        var heartbeatAge = run && run.heartbeat_age_seconds != null ? Math.max(0, parseInt(run.heartbeat_age_seconds, 10) || 0) : null;
        var activity = loopFeedHumanActivity(run);
        var nextStep = "";
        var steps = currentWorkflow && Array.isArray(currentWorkflow.steps) ? currentWorkflow.steps : [];
        if (meta.index && steps[meta.index]) nextStep = steps[meta.index].name || "the next step";
        if (status === "waiting") {
            return {
                headline: "Your decision is needed before the workflow can continue.",
                now: run.waiting_prompt || run.worker_question || "Review the request below and choose how the workflow should proceed.",
                next: "After your response, the same run continues with its existing ticket and memory.",
                action: "Decision required"
            };
        }
        if (status === "failed" || status === "error") {
            return {
                headline: "This run stopped because the current step could not produce a valid result.",
                now: activity,
                next: "Review the failure evidence before retrying or changing the worker.",
                action: "Review required"
            };
        }
        if (status === "cancelled") {
            return {
                headline: "This workflow run was cancelled.",
                now: activity,
                next: "Nothing else will run unless you start this ticket again.",
                action: "No action needed"
            };
        }
        return {
            headline: worker + " is working on “" + meta.title + "”.",
            now: activity + (heartbeatAge == null ? "" : (heartbeatAge < 15 ? " The worker is responding normally." : " The last heartbeat was " + formatElapsed(heartbeatAge) + " ago.")),
            next: nextStep ? ("If this step passes, the workflow continues to “" + nextStep + "”.") : "This is the final step; the workflow will prepare its delivery report next.",
            action: "No action needed"
        };
    }

    function renderLoopActivityFeed(items) {
        var list = document.getElementById("wf-loop-feed-messages");
        if (!list) return;
        items = Array.isArray(items) ? items : [];
        latestLoopFeedItems = items;
        var run = loopFeedActiveRun() || currentWorkflowActiveRun();
        if (!run) {
            list.innerHTML = '<p class="wf-loop-feed-empty">No active run. Start a queued ticket.</p>';
            return;
        }
        var meta = loopFeedActiveStepMeta(run);
        var humanState = loopFeedHumanState(run, meta);
        var runStatus = String(run.status || "").toLowerCase();
        var state = "running";
        if (runStatus === "waiting") state = "waiting";
        else if (runStatus === "failed" || runStatus === "error") state = "failed";
        else if (runStatus === "completed" || runStatus === "done" || runStatus === "passed") state = "passed";
        else if (runStatus === "cancelled") state = "failed";
        var stepGroups = loopFeedBuildStepGroups(items);
        var currentGroup = stepGroups.filter(function (group) {
            return run.current_step_id != null && String(group.step_id) === String(run.current_step_id);
        })[0] || stepGroups[0];
        var currentPhase = currentGroup
            ? loopFeedStepGroupHtml(currentGroup, run)
            : '<p class="wf-loop-feed-step-empty">The workflow has no configured phases.</p>';
        list.innerHTML =
            '<section class="wf-loop-decision-summary wf-loop-decision-summary--' + esc(state) + '">' +
                '<div class="wf-loop-decision-summary-head"><span>What is happening</span><b>' + esc(humanState.action) + '</b></div>' +
                '<p class="wf-loop-decision-headline">' + esc(humanState.headline) + '</p>' +
                '<dl class="wf-loop-decision-facts">' +
                    '<div><dt>Now</dt><dd>' + esc(humanState.now) + '</dd></div>' +
                    '<div><dt>Next</dt><dd>' + esc(humanState.next) + '</dd></div>' +
                '</dl>' +
            '</section>' +
            '<section class="wf-loop-feed-progress" aria-label="Workflow phase progress">' +
                '<div class="wf-loop-feed-progress-head"><span>Workflow progress</span><b>' + esc(meta.index ? ("Phase " + meta.index + " of " + stepGroups.length) : "Preparing") + '</b></div>' +
                loopFeedProgressRailHtml(stepGroups) +
                currentPhase +
            '</section>' +
            renderLoopExecutionTranscript(run);
        bindLoopExecutionTranscript(run);
        if (loopTranscriptOpenByRun[String(run.id)] && loopTranscriptEventsByRun[String(run.id)]) {
            loadLoopExecutionTranscript(run.id, { quiet: true });
        }
        if (loopFeedScrollPinned) {
            list.scrollTop = list.scrollHeight;
        }
    }

    function loadLoopActivityFeed(options) {
        options = options || {};
        var contextRun = loopFeedActiveRun() || currentWorkflowActiveRun();
        var contextTicketId = contextRun && contextRun.ticket_id ? String(contextRun.ticket_id) : selectedWorkflowQueueTicketId;
        if (!currentWorkflowId || !contextTicketId) {
            renderLoopActivityFeed([]);
            return Promise.resolve([]);
        }
        syncLoopFeedRunSelect();
        var runId = loopFeedRunId;
        contextRun = loopFeedActiveRun() || currentWorkflowActiveRun() || contextRun;
        var boardId = contextRun && contextRun.board_id ? contextRun.board_id : getSelectedBoardLocalId();
        var rawQuery = "/workflows/" + currentWorkflowId + "/orchestrator-events?limit=120";
        if (runId) rawQuery += "&run_id=" + encodeURIComponent(runId);
        if (boardId) rawQuery += "&board_id=" + encodeURIComponent(boardId);
        var eventsPromise = runId
            ? api("GET", "/workflows/" + currentWorkflowId + "/runs/" + runId + "/timeline?limit=500&mission_control=true").then(function (resp) {
                return resp && Array.isArray(resp.events) ? resp.events : [];
            }).catch(function () {
                return api("GET", rawQuery);
            })
            : api("GET", rawQuery);
        var steeringPromise = runId
            ? api("GET", "/workflows/" + currentWorkflowId + "/runs/" + runId + "/steering-memory")
            : Promise.resolve(null);
        return Promise.all([eventsPromise, steeringPromise]).then(function (results) {
            var events = Array.isArray(results[0]) ? results[0] : [];
            events = events.filter(function (event) {
                return !event.ticket_id || String(event.ticket_id) === String(contextTicketId);
            });
            var snapshot = results[1];
            var steeringLog = snapshot && Array.isArray(snapshot.steering_log) ? snapshot.steering_log.slice().reverse() : [];
            var optimistic = (latestLoopFeedItems || []).filter(function (item) {
                return item && item.optimistic;
            });
            var merged = mergeLoopFeedItems(events, steeringLog, optimistic);
            renderLoopActivityFeed(merged);
            if (currentWorkflow) renderSteps(currentWorkflow.steps || []);
            return merged;
        }).catch(function (e) {
            if (!options.quiet) snack(e.message || "Failed to load activity feed", "error");
            renderLoopActivityFeed([]);
            return [];
        });
    }

    function appendLoopFeedOptimisticSteer(message) {
        var run = loopFeedActiveRun();
        var item = {
            id: "optimistic-" + Date.now(),
            kind: "steer",
            title: "You",
            body: message,
            ts: Date.now(),
            detail: [],
            step_id: run && run.current_step_id || null,
            step_name: run && run.current_step_name || "",
            optimistic: true
        };
        renderLoopActivityFeed((latestLoopFeedItems || []).concat([item]));
    }

    function submitLoopSteer() {
        var input = document.getElementById("wf-loop-feed-input");
        var btn = document.getElementById("wf-loop-feed-steer-btn");
        var message = input ? String(input.value || "").trim() : "";
        var run = loopFeedActiveRun();
        if (!currentWorkflowId || !run || !message) return Promise.resolve();
        if (btn) btn.disabled = true;
        appendLoopFeedOptimisticSteer(message);
        if (input) input.value = "";
        var promise;
        if (run.status === "waiting") {
            promise = continueWorkflowRun(currentWorkflowId, run.id, { input: message });
        } else {
            promise = api("POST", "/workflows/" + currentWorkflowId + "/runs/" + run.id + "/steer", { message: message });
        }
        return promise.then(function (resp) {
            snack(workflowFeedbackText(resp, run.status === "waiting" ? "Run continued" : "Steering sent"));
            startPolling();
            loadLoopActivityFeed({ quiet: true });
            loadActiveRuns();
            if (currentWorkflowId) loadDetail(currentWorkflowId);
        }).catch(function (e) {
            snack(workflowErrorText(e, "Steer failed"), "error");
            loadLoopActivityFeed({ quiet: true });
        }).finally(function () {
            updateLoopFeedComposeState();
        });
    }

    // ── Orchestrator timeline ──
    function orchestratorStatusClass(status) {
        status = String(status || "").toLowerCase();
        if (status === "completed" || status === "passed") return "text-green-300 border-green-500/30 bg-green-500/10";
        if (status === "failed" || status === "error") return "text-red-300 border-red-500/30 bg-red-500/10";
        if (status === "waiting" || status === "queued") return "text-amber-300 border-amber-500/30 bg-amber-500/10";
        if (status === "cancelled") return "text-gray-300 border-white/15 bg-white/5";
        return "text-blue-300 border-blue-500/30 bg-blue-500/10";
    }

    function orchestratorMetaText(event) {
        var parts = [];
        if (event.ticket_id) parts.push("ticket #" + event.ticket_id);
        if (event.run_id) parts.push("run #" + event.run_id);
        if (event.step_id) parts.push("step #" + event.step_id);
        if (event.execution_session_id) parts.push("session #" + event.execution_session_id);
        if (event.project_id) parts.push("project #" + event.project_id);
        return parts.join(" · ");
    }

    function renderOrchestratorTimeline(events) {
        var list = document.getElementById("wf-orchestrator-events-list");
        var empty = document.getElementById("wf-orchestrator-events-empty");
        var clearBtn = document.getElementById("wf-clear-orchestrator-events");
        if (!list || !empty) return;
        events = Array.isArray(events) ? events : [];
        latestOrchestratorEvents = events;
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
            var meta = orchestratorMetaText(event);
            var payload = event.payload && typeof event.payload === "object" ? event.payload : {};
            var evidence = event.evidence && typeof event.evidence === "object" ? event.evidence : {};
            var detailLines = [];
            if (payload.decision && typeof payload.decision === "object") {
                if (payload.decision.backend) detailLines.push("Route backend: " + payload.decision.backend);
                if (payload.decision.model) detailLines.push("Route model: " + payload.decision.model);
                if (payload.decision.source) detailLines.push("Route source: " + payload.decision.source);
                if (payload.decision.rationale) detailLines.push("Rationale: " + payload.decision.rationale);
                var decisionSkills = workflowCleanStringList(payload.decision.skills);
                if (decisionSkills.length) detailLines.push("Skills: " + decisionSkills.join(", "));
            }
            if (payload.override && typeof payload.override === "object" && payload.override.backend) {
                detailLines.push("Override: " + payload.override.backend + (payload.override.model ? " / " + payload.override.model : ""));
            }
            var payloadSkills = workflowCleanStringList(payload.skills);
            var payloadTools = workflowCleanStringList(payload.tools);
            var payloadContext = workflowCleanStringList(payload.context);
            var orchestration = payload.orchestration && typeof payload.orchestration === "object" ? payload.orchestration : {};
            if (orchestration.legacy_event_type) detailLines.push("Legacy event: " + orchestration.legacy_event_type);
            if (orchestration.subtype && orchestration.subtype !== orchestration.legacy_event_type) detailLines.push("Subtype: " + orchestration.subtype);
            if (payloadSkills.length) detailLines.push("Skills: " + payloadSkills.join(", "));
            if (payloadTools.length) detailLines.push("Tools: " + payloadTools.join(", "));
            if (payloadContext.length) detailLines.push("Context: " + payloadContext.join(", "));
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
                        '<span class="mt-0.5 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ' + orchestratorStatusClass(status) + '">' + esc(status) + '</span>' +
                        '<div class="min-w-0 flex-1">' +
                            '<div class="flex items-center gap-2 min-w-0">' +
                                '<p class="text-sm text-white font-medium truncate">' + esc(event.summary || event.event_type || "Run event") + '</p>' +
                                '<span class="text-[11px] text-gray-500 flex-shrink-0">' + esc(event.source || "") + '</span>' +
                            '</div>' +
                            '<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">' +
                                '<span>' + esc(event.event_type || "event") + '</span>' +
                                (meta ? '<span>' + esc(meta) + '</span>' : '') +
                            '</div>' +
                            (detailLines.length ? '<div class="mt-2 space-y-1 text-xs text-gray-400">' + detailLines.slice(0, 8).map(function (line) { return '<p class="truncate">' + esc(line) + '</p>'; }).join('') + '</div>' : '') +
                        '</div>' +
                        '<span class="text-[11px] text-gray-500 flex-shrink-0">' + esc(when) + '</span>' +
                    '</div>' +
                '</div>';
        }).join('');
    }

    function loadOrchestratorTimeline(options) {
        options = options || {};
        if (!currentWorkflowId) {
            renderOrchestratorTimeline([]);
            return Promise.resolve([]);
        }
        return api("GET", "/workflows/" + currentWorkflowId + "/orchestrator-events?limit=120" + (function () {
            var boardId = getSelectedBoardLocalId();
            return boardId ? ("&board_id=" + encodeURIComponent(boardId)) : "";
        })())
            .then(function (events) {
                renderOrchestratorTimeline(events);
                return events;
            })
            .catch(function (e) {
                renderOrchestratorTimeline([]);
                if (!options.quiet) snack(e.message || "Failed to load timeline", "error");
                return [];
            });
    }

    // ── Agent Context tab ──
    function renderContextRules(data) {
        var listEl = document.getElementById("wf-config-context-items-list");
        var statusEl = document.getElementById("wf-config-context-save-status");
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
                        refreshWorkflowConfigPanel();
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
                                    refreshWorkflowConfigPanel();
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
    function switchTab(tab, options) {
        options = options || {};
        if (tab === "runs") {
            var runsTabBtn = document.getElementById("wf-runs-tab-btn");
            if (runsTabBtn) runsTabBtn.classList.remove("hidden");
        }
        document.querySelectorAll(".wf-tab").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.tab === tab);
        });
        document.querySelectorAll(".wf-tab-content").forEach(function (el) {
            el.classList.add("hidden");
            el.hidden = true;
        });
        var target = document.getElementById("wf-tab-" + tab);
        if (target) {
            target.classList.remove("hidden");
            target.hidden = false;
        }
        var scrollWrap = document.getElementById("wf-detail-tab-scroll");
        if (scrollWrap) scrollWrap.classList.toggle("wf-tab-scroll--loop", tab === "loop");
        var runAllBtn = document.getElementById("wf-run-all-btn");
        if (runAllBtn) runAllBtn.classList.toggle("hidden", tab !== "tickets");
        if (tab === "loop" && currentWorkflow) {
            loadWorkflowWorkspaceMemory();
            workflowRunsFilterTicketId = selectedWorkflowQueueTicketId;
            renderSteps(currentWorkflow.steps || []);
            restoreLoopFeedExpandedState();
            loadLoopActivityFeed({ quiet: true });
            syncLoopFeedPanelHeight();
        }
        if (tab === "cli") {
            loadWorkflowWorkspaceMemory();
            refreshWorkflowCliTab();
            loadWorkflowExecutionSessions({ quiet: true });
            requestAnimationFrame(syncWorkflowCliLayoutHeight);
        }
        if (tab === "runs") {
            loadActiveRuns();
            loadWorkflowExecutionSessions();
            loadWorkflowRunHistory({ quiet: true });
            loadOrchestratorTimeline({ quiet: true });
        }
        if (tab === "tickets") loadWorkflowTicketQueue();
        if (options.persist !== false) persistWorkflowDetailTab(tab);
        syncWorkflowCliAreaPresence({ reason: "switch-tab" });
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
        document.addEventListener("click", function () {
            closeWorkflowContextMenu();
            closeWorkflowQueueMetricMenu();
        });
        document.addEventListener("click", function (evt) {
            var btn = evt.target && evt.target.closest ? evt.target.closest(".wf-ui-feedback-btn") : null;
            if (!btn) return;
            evt.preventDefault();
            evt.stopPropagation();
            submitUiTasteFeedback(btn);
        });
        document.addEventListener("keydown", function (evt) {
            if (evt.key === "Escape") {
                closeWorkflowContextMenu();
                closeWorkflowQueueMetricMenu();
            }
        });
        if (window.DecisionsListKeyboard) {
            window.DecisionsListKeyboard.bind({
                listEl: "wf-list",
                namespace: "workflows",
                rowSelector: "[data-id]",
                axis: "horizontal",
                selectOnNavigate: true,
                getRowId: function(row) { return parseInt(row.dataset.id, 10); },
                getSelectedId: function() { return currentWorkflowId; },
                onSelect: function(id) { selectWorkflow(id); },
                onDelete: function(id) { deleteWorkflowById(id); },
                pageGuard: function() { return !!document.getElementById("wf-list"); },
                shouldSkip: function() { return isWorkflowQueueKeyboardContext(); },
            });
            window.DecisionsListKeyboard.bind({
                listEl: "wf-workflow-tickets-list",
                namespace: "workflow-queue",
                rowSelector: ".wf-workflow-ticket-row",
                axis: "vertical",
                selectOnNavigate: true,
                documentNavigate: true,
                getRowId: function(row) { return row.dataset.ticketId || ""; },
                getSelectedId: function() { return selectedWorkflowQueueTicketId; },
                onSelect: function(id, row) { selectWorkflowQueueTicket(id, row); },
                onEnter: function(id) {
                    if (id) openWorkflowRunPreview(id);
                },
                onDelete: function(id) {
                    removeWorkflowQueueTicket(id);
                },
                pageGuard: isWorkflowQueueKeyboardContext,
                shouldSkip: function() {
                    var modal = document.getElementById("kb-ticket-modal");
                    return !!(modal && !modal.classList.contains("hidden"));
                },
            });
        }

        function resetWorkflowCreateModalFields() {
            var nameEl = document.getElementById("wf-new-name");
            var descEl = document.getElementById("wf-builder-desc");
            var errEl = document.getElementById("wf-builder-error");
            if (nameEl) nameEl.value = "";
            if (descEl) descEl.value = "";
            if (errEl) {
                errEl.textContent = "";
                errEl.classList.add("hidden");
            }
        }

        function closeWorkflowCreateModal() {
            var modal = document.getElementById("wf-create-modal");
            if (modal) modal.classList.add("hidden");
            resetWorkflowCreateModalFields();
        }

        function openWorkflowCreateModal() {
            var modal = document.getElementById("wf-create-modal");
            resetWorkflowCreateModalFields();
            if (modal) modal.classList.remove("hidden");
            var nameEl = document.getElementById("wf-new-name");
            if (nameEl) nameEl.focus();
        }

        function submitWorkflowCreate() {
            var nameEl = document.getElementById("wf-new-name");
            var descEl = document.getElementById("wf-builder-desc");
            var createBtn = document.getElementById("wf-create-btn");
            var errEl = document.getElementById("wf-builder-error");
            var name = String((nameEl && nameEl.value) || "").trim() || "Untitled Workflow";
            var desc = String((descEl && descEl.value) || "").trim();
            if (errEl) errEl.classList.add("hidden");

            function setBusy(busy, label) {
                if (!createBtn) return;
                createBtn.disabled = !!busy;
                createBtn.textContent = label || "Create";
            }

            setBusy(true, desc ? "Planning..." : "Creating...");

            function finishCreate(data, message) {
                closeWorkflowCreateModal();
                snack(message);
                selectWorkflow(data.id);
                loadList();
            }

            if (desc) {
                api("POST", "/workflows/plan", { instruction: desc, name: name })
                    .then(function (data) {
                        if (!data || data.id == null) {
                            throw new Error("Failed to plan workflow");
                        }
                        return data;
                    })
                    .then(function (data) {
                        var stepCount = data.steps ? data.steps.length : 0;
                        finishCreate(data, "Workflow created — " + stepCount + " step" + (stepCount === 1 ? "" : "s"));
                    })
                    .catch(function (e) {
                        if (errEl) {
                            errEl.textContent = e.message || "Failed to plan workflow";
                            errEl.classList.remove("hidden");
                        } else {
                            snack(e.message || "Failed to plan workflow", "error");
                        }
                    })
                    .finally(function () { setBusy(false); });
                return;
            }

            api("POST", "/workflows", { name: name, description: "" })
                .then(function (data) { finishCreate(data, "Workflow created"); })
                .catch(function (e) {
                    if (errEl) {
                        errEl.textContent = e.message || "Failed to create workflow";
                        errEl.classList.remove("hidden");
                    } else {
                        snack(e.message || "Failed to create workflow", "error");
                    }
                })
                .finally(function () { setBusy(false); });
        }

        function openWorkflowExecutionSetup() {
            if (typeof window.openWorkflowExecutionSetup === "function") {
                window.openWorkflowExecutionSetup();
                return;
            }
            var legacySetupBtn = document.getElementById("wf-sr-llm-btn");
            if (legacySetupBtn) legacySetupBtn.click();
        }

        var menuBtn = document.getElementById("wf-menu-btn");
        if (menuBtn) {
            menuBtn.addEventListener("click", openWorkflowExecutionSetup);
        }
        var newWorkflowBtn = document.getElementById("wf-new-workflow-btn");
        if (newWorkflowBtn) {
            newWorkflowBtn.addEventListener("click", openWorkflowCreateModal);
        }

        var createModal = document.getElementById("wf-create-modal");
        var createModalClose = document.getElementById("wf-create-modal-close");
        var createCancelBtn = document.getElementById("wf-create-cancel");
        if (createModalClose) createModalClose.addEventListener("click", closeWorkflowCreateModal);
        if (createCancelBtn) createCancelBtn.addEventListener("click", closeWorkflowCreateModal);
        if (createModal) {
            createModal.addEventListener("click", function (e) {
                if (e.target === createModal) closeWorkflowCreateModal();
            });
        }
        document.addEventListener("keydown", function (evt) {
            var modal = document.getElementById("wf-create-modal");
            if (!modal || modal.classList.contains("hidden")) return;
            if (evt.key === "Escape") {
                evt.preventDefault();
                closeWorkflowCreateModal();
            }
        });

        var createBtn = document.getElementById("wf-create-btn");
        if (createBtn) createBtn.addEventListener("click", submitWorkflowCreate);

        // Tabs
        document.querySelectorAll(".wf-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                switchTab(btn.dataset.tab);
                if (btn.dataset.tab === "runs" || btn.dataset.tab === "tickets") loadActiveRuns();
            });
        });

        var refreshActiveRuns = document.getElementById("wf-refresh-active-runs");
        if (refreshActiveRuns) {
            refreshActiveRuns.addEventListener("click", function () {
                loadActiveRuns();
                loadWorkflowExecutionSessions();
            });
        }
        var refreshIntakeInbox = document.getElementById("wf-refresh-intake-inbox");
        if (refreshIntakeInbox) {
            refreshIntakeInbox.addEventListener("click", function () {
                loadWorkIntakeInbox();
            });
        }
        var intakeSubmit = document.getElementById("wf-intake-submit");
        if (intakeSubmit) {
            intakeSubmit.addEventListener("click", submitWorkIntakeCompose);
        }
        var intakeCompose = document.getElementById("wf-intake-compose");
        if (intakeCompose) {
            intakeCompose.addEventListener("keydown", function (evt) {
                if (evt.key === "Enter") {
                    evt.preventDefault();
                    submitWorkIntakeCompose();
                }
            });
        }
        var workflowExecutionRefresh = document.getElementById("wf-refresh-execution-sessions");
        if (workflowExecutionRefresh) {
            workflowExecutionRefresh.addEventListener("click", function () {
                loadWorkflowExecutionSessions();
            });
        }
        var orchestratorRefresh = document.getElementById("wf-refresh-orchestrator-events");
        if (orchestratorRefresh) {
            orchestratorRefresh.addEventListener("click", function () {
                loadOrchestratorTimeline();
            });
        }
        var steeringRefresh = document.getElementById("wf-refresh-steering-memory");
        if (steeringRefresh) {
            steeringRefresh.addEventListener("click", function () {
                loadWorkflowSteeringMemory();
            });
        }
        var steeringRunSelect = document.getElementById("wf-steering-run-select");
        if (steeringRunSelect) {
            steeringRunSelect.addEventListener("change", function () {
                workflowMemoryRunId = steeringRunSelect.value || null;
                loopFeedRunId = workflowMemoryRunId;
                loadWorkflowSteeringMemory({ quiet: true });
                loadLoopActivityFeed({ quiet: true });
            });
        }
        var loopFeedRunSelect = document.getElementById("wf-loop-feed-run-select");
        if (loopFeedRunSelect) {
            loopFeedRunSelect.addEventListener("change", function () {
                loopFeedRunId = loopFeedRunSelect.value || null;
                workflowMemoryRunId = loopFeedRunId;
                loadLoopActivityFeed({ quiet: true });
            });
        }
        var loopFeedExpandBtn = document.getElementById("wf-loop-feed-expand-btn");
        if (loopFeedExpandBtn) {
            loopFeedExpandBtn.addEventListener("click", function () {
                toggleLoopFeedExpanded();
            });
        }
        restoreLoopFeedExpandedState();
        var loopFeedSteerBtn = document.getElementById("wf-loop-feed-steer-btn");
        if (loopFeedSteerBtn) {
            loopFeedSteerBtn.addEventListener("click", function () {
                submitLoopSteer();
            });
        }
        var loopFeedInput = document.getElementById("wf-loop-feed-input");
        if (loopFeedInput) {
            loopFeedInput.addEventListener("keydown", function (evt) {
                if (evt.key === "Enter" && !evt.shiftKey) {
                    evt.preventDefault();
                    submitLoopSteer();
                }
            });
        }
        var loopFeedMessages = document.getElementById("wf-loop-feed-messages");
        if (loopFeedMessages) {
            loopFeedMessages.addEventListener("scroll", function () {
                var el = loopFeedMessages;
                loopFeedScrollPinned = (el.scrollHeight - el.scrollTop - el.clientHeight) < 48;
            });
        }
        bindWorkflowTicketDropZone();
        initWorkflowBoardTicketMouseDrag();

        var runAllBtn = document.getElementById("wf-run-all-btn");
        if (runAllBtn) runAllBtn.addEventListener("click", runAllWorkflowQueueTickets);
        var harnessBtn = document.getElementById("wf-harness-handoff-btn");
        if (harnessBtn) harnessBtn.addEventListener("click", openWorkflowHarnessModal);
        var refreshRunHistory = document.getElementById("wf-refresh-run-history");
        if (refreshRunHistory) refreshRunHistory.addEventListener("click", function () { loadWorkflowRunHistory({ quiet: false }); });
        var runsHistoryPane = document.getElementById("wf-runs-pane-history");
        if (runsHistoryPane) {
            runsHistoryPane.addEventListener("click", function (evt) {
                var clearFilter = evt.target.closest(".wf-runs-clear-filter");
                if (clearFilter) {
                    evt.preventDefault();
                    workflowRunsFilterTicketId = null;
                    loadWorkflowRunHistory({ quiet: false });
                    return;
                }
                var rerunBtn = evt.target.closest(".wf-run-item-rerun");
                if (rerunBtn) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    var tid = rerunBtn.dataset.ticketId || "";
                    if (tid) openWorkflowRunPreview(tid);
                    return;
                }
                var item = evt.target.closest(".wf-run-item");
                if (!item || evt.target.closest(".wf-run-item-rerun")) return;
                focusWorkflowRun(item.dataset.runId || "", {
                    ticketId: item.dataset.ticketId || "",
                    switchTab: "loop"
                });
            });
        }

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
                workflowBoardSelectionExplicit = true;
                if (workflowBoardSelect.value) loadWorkflowBoardTickets(workflowBoardSelect.value);
                renderWorkflowTickets(workflowQueueTickets);
                disconnectWorkflowCliWs();
                syncWorkflowCliAreaPresence({ reason: "board-change" });
                refreshWorkflowCliTabIfVisible();
            });
        }
        var boardTicketList = document.getElementById("wf-board-ticket-list");
        if (boardTicketList) {
            boardTicketList.addEventListener("click", function (evt) {
                var addAllBtn = evt.target.closest(".wf-lane-add-all-board-tickets");
                if (!addAllBtn) return;
                evt.preventDefault();
                evt.stopPropagation();
                addAllBoardTicketsToWorkflow(addAllBtn.dataset.laneId || "");
            });
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
        var ticketSave = document.getElementById("kb-modal-save");
        if (ticketSave) {
            ticketSave.addEventListener("click", function () {
                var state = workflowTicketModalState;
                var ticket = state && state.ticket ? state.ticket : null;
                var selected = state && state.context && state.context.selected ? state.context.selected : {};
                if (!ticket || !ticket.id || !state || !state.isLocal) return;
                var payload = {
                    title: document.getElementById("kb-modal-ticket-title").value.trim(),
                    description: document.getElementById("kb-modal-ticket-desc").value,
                    priority: (document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]") || {}).dataset ? document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]").dataset.pri : "medium",
                    complexity: document.getElementById("kb-modal-ticket-complexity").value,
                    time_estimate: document.getElementById("kb-modal-ticket-estimate").value.trim(),
                    time_spent: document.getElementById("kb-modal-ticket-duration").value.trim()
                };
                api("PUT", "/tickets/tickets/" + encodeURIComponent(ticket.id), payload)
                    .then(function () {
                        snack("Ticket saved");
                        closeWorkflowTicketModal();
                        var viewPatch = {
                            title: payload.title,
                            priority: payload.priority,
                            complexity: payload.complexity
                        };
                        if (!syncWorkflowQueueTicketView(ticket.id, viewPatch)) {
                            loadWorkflowTicketQueue();
                        } else {
                            refreshWorkflowCliTabIfVisible();
                        }
                        syncWorkflowBoardTicketView(ticket.id, viewPatch);
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
                if (!ticket || !ticket.id || !state || !state.isLocal || !state.canDelete) return;
                showConfirmModal({
                    title: "Delete ticket",
                    message: "Delete this ticket? This cannot be undone.",
                    confirmLabel: "Delete",
                    onConfirm: function () {
                        api("DELETE", "/tickets/tickets/" + encodeURIComponent(ticket.id))
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

        var addContextItemBtn = document.getElementById("wf-config-add-context-item-btn");
        if (addContextItemBtn) {
            addContextItemBtn.addEventListener("click", function () {
                if (!currentWorkflowId) return;
                var statusEl = document.getElementById("wf-config-context-save-status");
                if (statusEl) statusEl.textContent = "Saving...";
                api("POST", "/workflows/" + currentWorkflowId + "/context-items", {
                    title: "Context Rule",
                    content: "",
                    notes: ""
                }).then(function () {
                    if (statusEl) { statusEl.textContent = "Saved"; setTimeout(function () { if (statusEl) statusEl.textContent = ""; }, 1500); }
                    loadDetail(currentWorkflowId);
                    refreshWorkflowConfigPanel();
                }).catch(function () {
                    if (statusEl) statusEl.textContent = "Save failed";
                });
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
        var tabRunStopBtn = document.getElementById("wf-tab-run-stop");
        if (tabRunStopBtn) {
            tabRunStopBtn.addEventListener("click", function () {
                cancelWorkflowRun(tabRunStopBtn.dataset.workflowId, tabRunStopBtn.dataset.runId, tabRunStopBtn);
            });
        }
        var clearExecutionSessionsBtn = document.getElementById("wf-clear-execution-sessions");
        if (clearExecutionSessionsBtn) {
            clearExecutionSessionsBtn.addEventListener("click", clearWorkflowExecutionSessions);
        }
        var clearOrchestratorEventsBtn = document.getElementById("wf-clear-orchestrator-events");
        if (clearOrchestratorEventsBtn) {
            clearOrchestratorEventsBtn.addEventListener("click", clearWorkflowEvents);
        }

        initWorkflowLoopViewMode();
        setWorkflowLoopViewMode(workflowLoopViewMode);
        document.querySelectorAll(".wf-loop-view-toggle[data-loop-view]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                setWorkflowLoopViewMode(btn.dataset.loopView || "ring");
            });
        });
        bindWorkflowCliTabControls();
        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                stopWorkflowRealtime();
                setWorkflowCliAreaPresence(false, { reason: "hidden" });
                return;
            }
            resumeWorkflowRealtime();
            syncWorkflowCliAreaPresence({ reason: "visible" });
        });
        window.addEventListener("pagehide", function () {
            stopWorkflowRealtime();
            setWorkflowCliAreaPresence(false, { reason: "pagehide" });
        });
        window.addEventListener("beforeunload", function () {
            stopWorkflowRealtime();
        });

        // Add step
        var addStepBtn = document.getElementById("wf-add-step-btn");
        if (addStepBtn) {
            addStepBtn.addEventListener("click", addWorkflowLoopStep);
        }
        var loopPresetMode = document.getElementById("wf-loop-preset-mode");
        if (loopPresetMode) {
            loopPresetMode.addEventListener("change", syncLoopPresetCapacityHint);
        }
        var loopPresetsBtn = document.getElementById("wf-loop-presets-btn");
        if (loopPresetsBtn) loopPresetsBtn.addEventListener("click", openLoopPresetModal);
        var loopPresetClose = document.getElementById("wf-loop-preset-close");
        if (loopPresetClose) loopPresetClose.addEventListener("click", closeLoopPresetModal);
        var loopPresetModal = document.getElementById("wf-loop-preset-modal");
        if (loopPresetModal) {
            loopPresetModal.addEventListener("click", function (evt) {
                if (evt.target === loopPresetModal) closeLoopPresetModal();
            });
        }
        var loopPresetImportBtn = document.getElementById("wf-loop-preset-import-btn");
        var loopPresetImportFile = document.getElementById("wf-loop-preset-import-file");
        if (loopPresetImportBtn && loopPresetImportFile) {
            loopPresetImportBtn.addEventListener("click", function () {
                if (!currentWorkflowId) {
                    snack("Select a workflow first", "error");
                    return;
                }
                loopPresetImportFile.click();
            });
            loopPresetImportFile.addEventListener("change", function () {
                if (!loopPresetImportFile.files || !loopPresetImportFile.files.length) return;
                importLoopPresetFile(loopPresetImportFile.files[0]);
                loopPresetImportFile.value = "";
            });
        }
        var loopPresetExportBtn = document.getElementById("wf-loop-preset-export-btn");
        if (loopPresetExportBtn) loopPresetExportBtn.addEventListener("click", exportCurrentLoopPreset);
        var loopPresetSaveBtn = document.getElementById("wf-loop-preset-save-btn");
        if (loopPresetSaveBtn) loopPresetSaveBtn.addEventListener("click", saveCurrentLoopAsPreset);

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

        loadWorkflowCliInspectorPrefs();
        bindWorkflowCliInspectorControls();
        setWorkflowCliInspectorTab(workflowCliInspectorTab, false);
        setWorkflowCliInspectorOpen(workflowCliInspectorOpen, false);
        setWorkflowStepModelsCalloutVisible(workflowStepModelsCalloutVisible, false);
        syncWorkflowCliAreaPresence({ reason: "init" });
        setWorkflowBoardPaneCollapsed(workflowBoardPaneCollapsed, false);
        bindWorkflowSplitResizer();
        window.addEventListener("resize", function () {
            syncLoopFeedPanelHeight();
            syncWorkflowCliLayoutHeight();
        });

        try {
            var storedWorkflowId = parseInt(localStorage.getItem("wf_last_selected"), 10);
            if (Number.isFinite(storedWorkflowId)) currentWorkflowId = storedWorkflowId;
        } catch (e) {}

        loadList();
        loadWorkflowBoards();
        if (typeof window.requestIdleCallback === "function") {
            window.requestIdleCallback(checkPresetsExist, { timeout: 1500 });
        } else {
            window.setTimeout(checkPresetsExist, 500);
        }
        connectWebSocket();
        // Start version polling as initial fallback until WebSocket connects
        startVersionPolling();
    }

    function loadWorkflowCliInspectorPrefs() {
        try {
            var openRaw = localStorage.getItem("wf_cli_inspector_open");
            if (openRaw === "0") workflowCliInspectorOpen = false;
            var tabRaw = (localStorage.getItem("wf_cli_inspector_tab") || "").trim();
            if (tabRaw === "session-thread" || tabRaw === "step-models") workflowCliInspectorTab = tabRaw;
            if (localStorage.getItem("wf_step_models_callout_visible") === "0") workflowStepModelsCalloutVisible = false;
            workflowBoardPaneCollapsed = localStorage.getItem("wf_board_panel_collapsed") === "1";
        } catch (e) {}
    }

    function setWorkflowCliInspectorTab(tab, persist) {
        tab = tab === "session-thread" ? "session-thread" : "step-models";
        workflowCliInspectorTab = tab;
        document.querySelectorAll(".wf-cli-inspector-tab").forEach(function (btn) {
            btn.classList.toggle("active", (btn.dataset.cliInspectorTab || "") === tab);
        });
        document.querySelectorAll(".wf-cli-inspector-pane").forEach(function (pane) {
            pane.classList.toggle("active", (pane.dataset.cliInspectorPane || "") === tab);
        });
        if (persist !== false) {
            try { localStorage.setItem("wf_cli_inspector_tab", tab); } catch (e) {}
        }
    }

    function setWorkflowCliInspectorOpen(open, persist) {
        workflowCliInspectorOpen = !!open;
        var workspace = document.getElementById("wf-cli-workspace");
        var toggle = document.getElementById("wf-cli-inspector-toggle");
        if (workspace) workspace.classList.toggle("wf-cli-workspace--inspector-collapsed", !workflowCliInspectorOpen);
        if (toggle) {
            toggle.setAttribute("aria-expanded", workflowCliInspectorOpen ? "true" : "false");
            toggle.setAttribute("aria-label", workflowCliInspectorOpen ? "Collapse inspector" : "Expand inspector");
            toggle.title = workflowCliInspectorOpen ? "Collapse inspector" : "Expand inspector";
        }
        if (persist !== false) {
            try { localStorage.setItem("wf_cli_inspector_open", workflowCliInspectorOpen ? "1" : "0"); } catch (e) {}
        }
        syncWorkflowCliLayoutHeight();
    }

    function toggleWorkflowCliInspector() {
        setWorkflowCliInspectorOpen(!workflowCliInspectorOpen);
    }

    function setWorkflowStepModelsCalloutVisible(visible, persist) {
        workflowStepModelsCalloutVisible = !!visible;
        var callout = document.getElementById("wf-cli-step-models-callout");
        if (callout) callout.classList.toggle("hidden", !workflowStepModelsCalloutVisible);
        if (persist !== false) {
            try { localStorage.setItem("wf_step_models_callout_visible", workflowStepModelsCalloutVisible ? "1" : "0"); } catch (e) {}
        }
    }

    function setWorkflowBoardPaneCollapsed(collapsed, persist) {
        workflowBoardPaneCollapsed = !!collapsed;
        var layout = document.getElementById("wf-split-layout");
        var reopenWrap = document.getElementById("wf-board-pane-reopen-wrap");
        var headerToggle = document.getElementById("wf-board-pane-toggle-header");
        if (layout) layout.classList.toggle("wf-board-panel-collapsed", workflowBoardPaneCollapsed);
        if (reopenWrap) reopenWrap.classList.toggle("hidden", !workflowBoardPaneCollapsed);
        if (headerToggle) {
            headerToggle.setAttribute("aria-label", workflowBoardPaneCollapsed ? "Expand board tickets" : "Collapse board tickets");
            headerToggle.title = workflowBoardPaneCollapsed ? "Expand board tickets" : "Collapse board tickets";
            headerToggle.classList.toggle("is-collapsed", workflowBoardPaneCollapsed);
        }
        document.querySelectorAll(".wf-board-pane-toggle").forEach(function (btn) {
            var collapsedNow = !!workflowBoardPaneCollapsed;
            btn.setAttribute("aria-label", collapsedNow ? "Expand board tickets" : "Collapse board tickets");
            btn.title = collapsedNow ? "Expand board tickets" : "Collapse board tickets";
            btn.classList.toggle("is-collapsed", collapsedNow);
        });
        if (persist !== false) {
            try { localStorage.setItem("wf_board_panel_collapsed", workflowBoardPaneCollapsed ? "1" : "0"); } catch (e) {}
        }
    }

    function toggleWorkflowBoardPaneCollapsed(forceValue) {
        if (typeof forceValue === "boolean") {
            setWorkflowBoardPaneCollapsed(forceValue);
            return;
        }
        setWorkflowBoardPaneCollapsed(!workflowBoardPaneCollapsed);
    }

    function bindWorkflowCliInspectorControls() {
        var toggle = document.getElementById("wf-cli-inspector-toggle");
        if (toggle && toggle.dataset.bound !== "1") {
            toggle.dataset.bound = "1";
            toggle.addEventListener("click", function () {
                toggleWorkflowCliInspector();
            });
        }
        document.querySelectorAll(".wf-cli-inspector-tab").forEach(function (btn) {
            if (btn.dataset.bound === "1") return;
            btn.dataset.bound = "1";
            btn.addEventListener("click", function () {
                setWorkflowCliInspectorTab(btn.dataset.cliInspectorTab || "step-models");
            });
        });
        var reopen = document.getElementById("wf-board-pane-reopen");
        if (reopen && reopen.dataset.bound !== "1") {
            reopen.dataset.bound = "1";
            reopen.addEventListener("click", function () {
                setWorkflowBoardPaneCollapsed(false);
            });
        }
        var headerToggle = document.getElementById("wf-board-pane-toggle-header");
        if (headerToggle && headerToggle.dataset.bound !== "1") {
            headerToggle.dataset.bound = "1";
            headerToggle.addEventListener("click", function () {
                toggleWorkflowBoardPaneCollapsed();
            });
        }
        var calloutClose = document.getElementById("wf-cli-step-models-callout-close");
        if (calloutClose && calloutClose.dataset.bound !== "1") {
            calloutClose.dataset.bound = "1";
            calloutClose.addEventListener("click", function () {
                setWorkflowStepModelsCalloutVisible(false);
            });
        }
    }

    function bindWorkflowSplitResizer() {
        var layout = document.getElementById("wf-split-layout");
        var resizer = document.getElementById("wf-split-resizer");
        if (!layout || !resizer) return;

        var storageKey = "wf_split_left_pct";
        var minPanePx = 280;

        function loadPct() {
            try {
                var saved = parseFloat(localStorage.getItem(storageKey));
                if (isFinite(saved) && saved >= 18 && saved <= 82) return saved;
            } catch (e) {}
            return 50;
        }

        function paneLimits() {
            var rect = layout.getBoundingClientRect();
            var resizerWidth = resizer.offsetWidth || 10;
            var available = Math.max(0, rect.width - resizerWidth);
            if (!available) return { minPct: 18, maxPct: 82 };
            var minPct = (minPanePx / available) * 100;
            var maxPct = 100 - minPct;
            return {
                minPct: Math.max(18, minPct),
                maxPct: Math.min(82, maxPct)
            };
        }

        function applyPct(pct, persist) {
            var limits = paneLimits();
            pct = Math.max(limits.minPct, Math.min(limits.maxPct, pct));
            layout.style.setProperty("--wf-split-left-width", pct + "%");
            if (persist !== false) {
                try { localStorage.setItem(storageKey, String(Math.round(pct * 10) / 10)); } catch (e) {}
            }
            return pct;
        }

        applyPct(loadPct(), false);

        var dragging = false;

        function pctFromClientX(clientX) {
            var rect = layout.getBoundingClientRect();
            var resizerWidth = resizer.offsetWidth || 10;
            var available = rect.width - resizerWidth;
            if (available <= 0) return loadPct();
            var leftWidth = clientX - rect.left;
            return (leftWidth / available) * 100;
        }

        function onPointerMove(evt) {
            if (!dragging) return;
            applyPct(pctFromClientX(evt.clientX));
        }

        function stopDrag() {
            if (!dragging) return;
            dragging = false;
            resizer.classList.remove("is-dragging");
            document.body.classList.remove("wf-split-dragging");
            window.removeEventListener("pointermove", onPointerMove);
            window.removeEventListener("pointerup", stopDrag);
            window.removeEventListener("pointercancel", stopDrag);
        }

        resizer.addEventListener("pointerdown", function (evt) {
            if (workflowBoardPaneCollapsed) return;
            if (evt.button !== 0) return;
            evt.preventDefault();
            dragging = true;
            resizer.classList.add("is-dragging");
            document.body.classList.add("wf-split-dragging");
            if (resizer.setPointerCapture) resizer.setPointerCapture(evt.pointerId);
            window.addEventListener("pointermove", onPointerMove);
            window.addEventListener("pointerup", stopDrag);
            window.addEventListener("pointercancel", stopDrag);
        });

        resizer.addEventListener("dblclick", function () {
            if (workflowBoardPaneCollapsed) return;
            applyPct(50);
        });

        resizer.addEventListener("keydown", function (evt) {
            if (workflowBoardPaneCollapsed) return;
            var step = evt.shiftKey ? 5 : 2;
            var current = loadPct();
            if (evt.key === "ArrowLeft") {
                evt.preventDefault();
                applyPct(current - step);
            } else if (evt.key === "ArrowRight") {
                evt.preventDefault();
                applyPct(current + step);
            } else if (evt.key === "Home") {
                evt.preventDefault();
                applyPct(paneLimits().minPct);
            } else if (evt.key === "End") {
                evt.preventDefault();
                applyPct(paneLimits().maxPct);
            }
        });

        window.addEventListener("resize", function () {
            applyPct(loadPct(), false);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        setTimeout(init, 0);
    }
})();
