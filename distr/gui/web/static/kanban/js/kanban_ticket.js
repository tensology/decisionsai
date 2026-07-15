(function() {
    "use strict";

    function createTicketUi(deps) {
        var agentMenuEl = null;
        var agentMenuBound = false;
        var ticketActionMenuBound = false;

        function backendReady(backends, backendId) {
            var row = (backends || []).find(function(b) { return b && b.id === backendId; });
            return !!(row && (row.ready || row.available));
        }

        function hideAgentMenu() {
            if (agentMenuEl) agentMenuEl.classList.add("hidden");
        }

        function ensureAgentMenu() {
            if (agentMenuEl) return agentMenuEl;
            agentMenuEl = document.createElement("div");
            agentMenuEl.id = "kb-ticket-agent-menu";
            agentMenuEl.className = "fixed hidden z-50 min-w-[210px] py-1 bg-[#1a1f3a] border border-white/20 rounded-lg shadow-xl";
            document.body.appendChild(agentMenuEl);
            if (!agentMenuBound) {
                agentMenuBound = true;
                document.addEventListener("click", function(e) {
                    if (!agentMenuEl || e.target.closest("#kb-ticket-agent-menu") || e.target.closest(".kb-act-agent")) return;
                    hideAgentMenu();
                });
                document.addEventListener("keydown", function(e) {
                    if (e.key === "Escape") hideAgentMenu();
                });
            }
            return agentMenuEl;
        }

        function menuButtonHtml(action, label, hint, disabled) {
            return '<button type="button" data-action="' + deps.esc(action) + '" class="w-full text-left px-4 py-2 text-sm flex flex-col gap-0.5 ' +
                (disabled ? 'text-gray-600 cursor-not-allowed' : 'text-gray-300 hover:bg-white/10') + '"' + (disabled ? " disabled" : "") + ">" +
                '<span class="font-medium">' + deps.esc(label) + "</span>" +
                (hint ? '<span class="text-[11px] text-gray-500">' + deps.esc(hint) + "</span>" : "") +
                "</button>";
        }

        function runAgentDispatch(ticket, isLocal, backendId, btnEl) {
            if (!ticket) return;
            if (isLocal) {
                deps.sendTicketToAgentById(ticket.id, btnEl, { backendId: backendId || "" });
                return;
            }
            var currentBoard = deps.getCurrentBoard();
            if (!currentBoard) return;
            deps.copyAndPushExternalTicket(ticket, currentBoard.source, "agent", null, backendId || "", btnEl);
        }

        function showAgentMenu(event, ticket, isLocal, btnEl, opts) {
            event.preventDefault();
            event.stopPropagation();
            var menu = ensureAgentMenu();
            var canRun = !!(opts && opts.hasProject);
            menu.innerHTML =
                menuButtonHtml("discuss", "Send to Orchestrator", "Send context into the current agent chat.", false) +
                '<div class="my-1 border-t border-white/10"></div>' +
                menuButtonHtml("auto", "Run with auto route", "Use complexity routing and availability fallback.", !canRun) +
                menuButtonHtml("cursor", "Send to Cursor", "Force Cursor when available.", true) +
                menuButtonHtml("codex", "Send to Codex", "Force Codex when available.", true);
            menu.style.left = Math.min(event.clientX, window.innerWidth - 230) + "px";
            menu.style.top = Math.min(event.clientY, window.innerHeight - 190) + "px";
            menu.classList.remove("hidden");

            deps.apiFetch("/api/projects/cli-backends").then(function(data) {
                var backends = (data && data.backends) || [];
                var cursorReady = canRun && backendReady(backends, "cursor");
                var codexReady = canRun && backendReady(backends, "codex");
                menu.innerHTML =
                    menuButtonHtml("discuss", "Send to Orchestrator", "Send context into the current agent chat.", false) +
                    '<div class="my-1 border-t border-white/10"></div>' +
                    menuButtonHtml("auto", "Run with auto route", "Complexity decides Cursor/Codex; falls back if needed.", !canRun) +
                    menuButtonHtml("cursor", "Send to Cursor", cursorReady ? "Available now." : "Cursor is not available.", !cursorReady) +
                    menuButtonHtml("codex", "Send to Codex", codexReady ? "Available now." : "Codex is not available.", !codexReady);
            }).catch(function() {
                menu.innerHTML =
                    menuButtonHtml("discuss", "Send to Orchestrator", "Send context into the current agent chat.", false) +
                    '<div class="my-1 border-t border-white/10"></div>' +
                    menuButtonHtml("auto", "Run with auto route", "Could not check availability; backend will validate.", !canRun) +
                    menuButtonHtml("cursor", "Send to Cursor", "Availability check failed.", true) +
                    menuButtonHtml("codex", "Send to Codex", "Availability check failed.", true);
            });

            menu.onclick = function(e) {
                var item = e.target.closest("button[data-action]");
                if (!item || item.disabled) return;
                e.preventDefault();
                e.stopPropagation();
                var action = item.getAttribute("data-action");
                hideAgentMenu();
                if (action === "discuss") {
                    deps.startTicketDiscussion(ticket, isLocal);
                } else if (action === "auto") {
                    runAgentDispatch(ticket, isLocal, "", btnEl);
                } else if (action === "cursor" || action === "codex") {
                    runAgentDispatch(ticket, isLocal, action, btnEl);
                }
            };
        }

        function buildSourceBadge(source) {
            if (!source || source === "database") return "";
            return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-300 font-medium">' + deps.esc(source) + "</span>";
        }

        function externalSourceLinkMarkup(source) {
            return '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAi0lEQVR42u3WQQqDQBAAwXlFPprXe0quQVRyCGjsKvDoQo/u6gwAAMCJlufj9YsrP4T8m5DfDvkzIX8wGoAt4BD0GfQj5Ff45vFH9yTij+7NxO+tkYrfWisXv14zGf+5djY+/eTFixcvXrx48eLFixcvXvzVBjB3lY7/ZghTkY7fGsJUpeMBAAD+zRvbrtesCjwpyAAAAABJRU5ErkJggg==" alt="" aria-hidden="true">';
        }

        function metricTipHtml(tooltip, innerHtml) {
            var tip = tooltip || "";
            return '<span class="kb-card-action-tip" data-tooltip="' + deps.esc(tip) + '" tabindex="0">' + innerHtml + "</span>";
        }

        function formatMetricLabel(kind, value) {
            var level = (value || "medium").toLowerCase();
            return kind + ": " + level.charAt(0).toUpperCase() + level.slice(1);
        }

        function buildPriorityBadge(priority) {
            var pri = (priority || "medium").toLowerCase();
            if (pri !== "critical" && pri !== "high" && pri !== "low") pri = "medium";
            var priClass = "kb-pri-" + pri;
            return metricTipHtml(
                formatMetricLabel("Priority", pri),
                '<span class="kb-metric-badge ' + priClass + '" aria-label="' + deps.esc(formatMetricLabel("Priority", pri)) + '">' + deps.esc(pri) + "</span>"
            );
        }

        function buildComplexityBadge(complexity) {
            var level = (complexity || "medium").toLowerCase();
            if (level !== "low" && level !== "high") level = "medium";
            var numeral = "II";
            if (level === "low") numeral = "I";
            if (level === "high") numeral = "III";
            return metricTipHtml(
                formatMetricLabel("Complexity", level),
                '<span class="kb-metric-badge kb-complexity-numeral kb-cx-' + level + '" aria-label="' + deps.esc(formatMetricLabel("Complexity", level)) + '">' + numeral + "</span>"
            );
        }

        function buildExternalLink(ticketUrl, source) {
            if (!ticketUrl) return "";
            var sourceLabel = source || "browser";
            return '<a href="' + deps.esc(ticketUrl) + '" target="_blank" class="kb-card-action-btn text-white transition-colors" data-tooltip="Open in ' + deps.esc(sourceLabel) + '" aria-label="Open in ' + deps.esc(sourceLabel) + '" onclick="event.stopPropagation()">' +
                '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>';
        }

        function ticketClipboardText(ticket) {
            var text = (ticket.title || "").trim();
            var description = deps.stripHtml(ticket.description || "").trim();
            if (description) text += (text ? "\n\n" : "") + description;
            var attachments = [];
            (ticket.files || []).forEach(function(f) {
                var url = f.download_url || f.url || "";
                if (url) attachments.push(url);
            });
            (ticket.media || []).forEach(function(m) {
                var mediaUrl = m.url || m.download_url || "";
                if (mediaUrl) attachments.push(mediaUrl);
            });
            if (attachments.length) text += "\n\nAttachments:\n" + attachments.map(function(url) { return "- " + url; }).join("\n");
            return text || "(empty ticket)";
        }

        function copyTicketToClipboard(ticket) {
            var text = ticketClipboardText(ticket || {});
            if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
                deps.showSnackbar("Clipboard is unavailable in this browser", "error");
                return;
            }
            navigator.clipboard.writeText(text).then(function() {
                deps.showSnackbar("Copied title and description");
            }).catch(function(err) {
                deps.showSnackbar("Copy failed: " + (err && err.message ? err.message : String(err)), "error");
            });
        }

        function actionIconSvg(key) {
            var icons = {
                copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
                agent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4"/><path d="M12 18v4"/><rect x="4" y="6" width="16" height="12" rx="3"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><path d="M9 15h6"/></svg>',
                workflowQueue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
                cli: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
                project: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
                workflow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 12h5"/><path d="M13 12l3-4"/><path d="M13 12l3 4"/></svg>',
                transfer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
                delete: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v5"/><path d="M14 11v5"/></svg>',
                menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>',
                chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>',
            };
            return icons[key] || "";
        }

        function actionMenuItemHtml(config) {
            if (config.hidden) return "";
            var dangerClass = config.danger ? " is-danger" : "";
            return '<button type="button" class="kb-card-action-menu-item' + dangerClass + '" data-kb-card-action="' + deps.esc(config.action) + '" role="menuitem">' +
                actionIconSvg(config.icon) +
                '<span>' + deps.esc(config.label) + "</span>" +
                "</button>";
        }

        function legacyActionButtonHtml(config) {
            if (config.hidden) return "";
            var tooltip = config.tooltip || "";
            return '<span class="kb-card-action-tip" data-tooltip="' + deps.esc(tooltip) + '">' +
                '<button type="button" class="' + config.keyClass + ' kb-card-action-btn text-white transition-colors" aria-label="' + deps.esc(tooltip) + '">' +
                actionIconSvg(config.icon) + "</button></span>";
        }

        function buildWorkflowQueueActionRow(opts) {
            return legacyActionButtonHtml({
                keyClass: "kb-act-add-workflow",
                icon: "workflowQueue",
                tooltip: "Add to workflow queue",
                hidden: !opts.showAddToWorkflow,
            });
        }

        function buildActionRow(opts) {
            opts = opts || {};
            if (opts.showAddToWorkflow) {
                return buildWorkflowQueueActionRow(opts);
            }
            var actions = [
                actionMenuItemHtml({
                    action: "copy",
                    icon: "copy",
                    label: "Copy details",
                    hidden: !!opts.hideCopy,
                }),
                actionMenuItemHtml({
                    action: "agent",
                    icon: "agent",
                    label: "Discuss in Orchestrator",
                    hidden: !!opts.hideAgent,
                }),
                actionMenuItemHtml({
                    action: "add-workflow",
                    icon: "workflowQueue",
                    label: "Add to workflow queue",
                    hidden: !opts.showAddToWorkflow,
                }),
                actionMenuItemHtml({
                    action: "cli",
                    icon: "cli",
                    label: "Run with Cursor/Codex",
                    hidden: opts.layout === "list" || !opts.hasProject,
                }),
                actionMenuItemHtml({
                    action: "project",
                    icon: "project",
                    label: "Send to Project",
                    hidden: opts.layout === "list" || !opts.hasProject,
                }),
                actionMenuItemHtml({
                    action: "workflow",
                    icon: "workflow",
                    label: "Send to Workflow",
                    hidden: opts.hideWorkflow,
                }),
                actionMenuItemHtml({
                    action: "transfer",
                    icon: "transfer",
                    label: "Copy to local board",
                    hidden: !opts.canTransfer,
                })
            ];
            var deleteAction = actionMenuItemHtml({
                action: "delete",
                icon: "delete",
                label: "Delete ticket",
                hidden: !opts.canDelete,
                danger: true,
            });
            var menuItems = actions.join("") + deleteAction;
            return '<div class="kb-card-actions-menu-wrap">' +
                '<button type="button" class="kb-card-action-btn kb-card-actions-trigger" aria-label="Ticket actions" aria-haspopup="menu" aria-expanded="false"><span>Actions</span>' + actionIconSvg("chevron") + "</button>" +
                '<div class="kb-card-actions-menu hidden" role="menu">' + menuItems + "</div>" +
                "</div>";
        }

        function initListRowMarquee(row) {
            var desc = row.querySelector(".kb-ticket-list-desc");
            if (!desc || desc.dataset.marqueeInit === "done") return;
            var track = desc.querySelector(".kb-ticket-list-desc-track");
            if (!track) {
                desc.classList.add("kb-ticket-list-desc--empty");
                desc.dataset.marqueeInit = "done";
                return;
            }
            var first = track.querySelector("span");
            if (!first) return;
            var text = (first.textContent || "").trim();
            if (!text) {
                desc.classList.add("kb-ticket-list-desc--empty");
                desc.dataset.marqueeInit = "done";
                return;
            }
            requestAnimationFrame(function() {
                if (desc.dataset.marqueeInit === "done") return;
                if (track.scrollWidth <= desc.clientWidth + 1) return;
                desc.dataset.marqueeInit = "done";
                desc.classList.add("kb-ticket-list-desc--marquee");
                var clone = first.cloneNode(true);
                clone.setAttribute("aria-hidden", "true");
                track.appendChild(clone);
                var duration = Math.max(10, Math.round(track.scrollWidth / 40));
                track.style.setProperty("--kb-marquee-duration", duration + "s");
            });
        }

        function deleteLocalTicket(ticket) {
            if (!ticket || !ticket.id) return;
            deps.showKanbanConfirm({
                title: "Delete ticket",
                message: "Delete this ticket? This cannot be undone.",
                confirmLabel: "Delete",
                danger: true,
                onConfirm: function() {
                    deps.hideKanbanConfirm();
                    deps.apiFetch("/api/tickets/tickets/" + ticket.id, { method: "DELETE" }).then(function() {
                        deps.showSnackbar("Ticket deleted");
                        deps.reloadCurrentDatabaseBoard();
                    }).catch(function(e) {
                        deps.showSnackbar("Delete failed: " + e.message, "error");
                    });
                },
            });
        }

        function closeTicketActionMenus(scope) {
            var root = scope || document;
            root.querySelectorAll(".kb-card-actions-menu:not(.hidden)").forEach(function(menu) {
                menu.classList.add("hidden");
                menu.style.left = "";
                menu.style.top = "";
                var wrap = menu.closest(".kb-card-actions-menu-wrap");
                var trigger = wrap ? wrap.querySelector(".kb-card-actions-trigger") : null;
                if (trigger) trigger.setAttribute("aria-expanded", "false");
            });
        }

        function positionTicketActionMenu(menu, trigger) {
            if (!menu || !trigger) return;
            var gap = 6;
            var margin = 8;
            var rect = trigger.getBoundingClientRect();
            var menuWidth = menu.offsetWidth || 220;
            var menuHeight = menu.offsetHeight || 220;
            var left = rect.right - menuWidth;
            left = Math.max(margin, Math.min(left, window.innerWidth - menuWidth - margin));
            var top = rect.bottom + gap;
            if (top + menuHeight > window.innerHeight - margin) {
                top = rect.top - menuHeight - gap;
            }
            top = Math.max(margin, Math.min(top, window.innerHeight - menuHeight - margin));
            menu.style.left = Math.round(left) + "px";
            menu.style.top = Math.round(top) + "px";
        }

        function bindTicketActions(rootEl, ticket, isLocal, hasProject) {
            var currentBoard = deps.getCurrentBoard();
            if (!ticketActionMenuBound) {
                ticketActionMenuBound = true;
                document.addEventListener("click", function(e) {
                    if (e.target.closest(".kb-card-actions-menu-wrap")) return;
                    closeTicketActionMenus(document);
                });
                document.addEventListener("keydown", function(e) {
                    if (e.key === "Escape") closeTicketActionMenus(document);
                });
                window.addEventListener("resize", function() {
                    closeTicketActionMenus(document);
                });
                window.addEventListener("scroll", function() {
                    closeTicketActionMenus(document);
                }, true);
            }
            rootEl.addEventListener("click", function(e) {
                if (e.target.closest(".kb-card-actions") || e.target.closest(".kb-ticket-list-actions") || e.target.closest(".kb-ticket-list-drag-handle") || e.target.closest(".kb-ticket-list-badges") || e.target.closest("a")) return;
                if (isLocal) deps.openTicketModal(ticket.id);
                else openExternalTicketModal(ticket, currentBoard.source);
            });
            var actionTrigger = rootEl.querySelector(".kb-card-actions-trigger");
            var actionMenu = rootEl.querySelector(".kb-card-actions-menu");
            if (actionTrigger && actionMenu) {
                actionTrigger.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    var wasOpen = !actionMenu.classList.contains("hidden");
                    closeTicketActionMenus(document);
                    if (!wasOpen) {
                        actionMenu.classList.remove("hidden");
                        actionTrigger.setAttribute("aria-expanded", "true");
                        positionTicketActionMenu(actionMenu, actionTrigger);
                    }
                });
                actionMenu.addEventListener("click", function(e) {
                    var item = e.target.closest("[data-kb-card-action]");
                    if (!item) return;
                    e.preventDefault();
                    e.stopPropagation();
                    var action = item.getAttribute("data-kb-card-action");
                    closeTicketActionMenus(document);
                    if (action === "copy") {
                        copyTicketToClipboard(ticket);
                    } else if (action === "agent") {
                        deps.startTicketDiscussion(ticket, isLocal);
                    } else if (action === "cli") {
                        if (isLocal) deps.pushTicketToCli(ticket.id, actionTrigger);
                        else deps.copyAndPushExternalTicket(ticket, currentBoard.source, "cli", null, "", actionTrigger);
                    } else if (action === "project") {
                        if (isLocal) deps.sendTicketToProjectById(ticket.id, actionTrigger);
                        else deps.copyAndPushExternalTicket(ticket, currentBoard.source, "project", null, "", actionTrigger);
                    } else if (action === "add-workflow") {
                        if (typeof deps.addTicketToWorkflowQueue === "function") {
                            deps.addTicketToWorkflowQueue(ticket, isLocal, actionTrigger);
                        }
                    } else if (action === "workflow") {
                        deps.openSendWorkflowModal(ticket, isLocal ? "database" : currentBoard.source);
                    } else if (action === "transfer") {
                        deps.openCopyModal(ticket);
                    } else if (action === "delete") {
                        deleteLocalTicket(ticket);
                    }
                });
                actionMenu.addEventListener("keydown", function(e) {
                    if (e.key === "Escape") {
                        e.preventDefault();
                        closeTicketActionMenus(document);
                        actionTrigger.focus();
                    }
                });
            }
            var addWorkflowBtn = rootEl.querySelector(".kb-act-add-workflow");
            if (addWorkflowBtn) {
                addWorkflowBtn.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (typeof deps.addTicketToWorkflowQueue === "function") {
                        deps.addTicketToWorkflowQueue(ticket, isLocal, addWorkflowBtn);
                    }
                });
            }
            var wfBadge = rootEl.querySelector(".kb-wf-status-badge");
            if (wfBadge && wfBadge.tagName === "BUTTON" && isLocal && typeof deps.showRunPopover === "function") {
                wfBadge.addEventListener("click", function(e) {
                    e.stopPropagation();
                    deps.showRunPopover(wfBadge, ticket.id);
                });
            }
        }

        function buildCardMarkup(opts) {
            var sourceBadge = buildSourceBadge(opts.source);
            var complexityBadge = buildComplexityBadge(opts.complexity);
            var extLinkHtml = opts.isLocal ? "" : buildExternalLink(opts.ticketUrl, opts.source);
            var actionRowHtml = buildActionRow({
                hasProject: opts.hasProject,
                canTransfer: opts.canTransfer,
                canDelete: opts.canDelete,
            });
            return '<div class="flex items-start justify-between gap-2">' +
                '<span class="text-[14px] font-medium text-white leading-snug flex-1">' + deps.esc(opts.title) + "</span>" +
                '<div class="flex items-center gap-1.5 flex-shrink-0">' + sourceBadge + complexityBadge + buildPriorityBadge(opts.priority) + (opts.workflowStatusBadgeHtml || "") + extLinkHtml + "</div>" +
                "</div>" +
                (opts.description ? '<p class="text-xs text-gray-400 mt-2 mb-1 leading-relaxed line-clamp-2">' + deps.esc(opts.description) + "</p>" : "") +
                (opts.labelsHtml || "") + (opts.membersHtml || "") + (opts.timeHtml || "") + (opts.mediaHtml || "") +
                '<div class="kb-card-actions mt-2">' + actionRowHtml + "</div>" +
                '<div class="flex items-center mt-1">' + (opts.todoHtml || "") + "</div>";
        }

        function renderExternalMedia(media, source) {
            var container = document.getElementById("kb-modal-files");
            container.innerHTML = "";
            if (!media || !media.length) {
                container.innerHTML = '<p class="text-xs text-gray-500 italic">No attachments</p>';
                return;
            }
            var html = '<div class="space-y-2">';
            media.forEach(function(m) {
                var mtype = (m.type || "").startsWith("video/") ? "video" : "image";
                var imgUrl = m.url;
                var thumbUrl = m.thumbnail || m.url;
                if (source === "jira" && imgUrl) {
                    imgUrl = "/api/tickets/external-boards/jira/proxy-image?url=" + encodeURIComponent(imgUrl);
                    thumbUrl = m.thumbnail ? "/api/tickets/external-boards/jira/proxy-image?url=" + encodeURIComponent(m.thumbnail) : imgUrl;
                }
                if (mtype === "image" && thumbUrl) {
                    html += '<div class="flex items-start gap-2 p-2 bg-[#152054] rounded border border-white/10">';
                    html += '<a href="' + deps.esc(imgUrl) + '" target="_blank" class="flex-shrink-0"><img src="' + deps.esc(thumbUrl) + '" alt="' + deps.esc(m.name || "image") + '" class="max-w-[160px] max-h-[120px] object-cover rounded border border-white/10" loading="lazy"/></a>';
                    html += '<div class="flex-1 min-w-0">';
                    html += '<div class="text-xs text-gray-300 truncate">' + deps.esc(m.name || "Attachment") + "</div>";
                    html += '<div class="text-[10px] text-gray-500">' + deps.esc(m.type || "") + "</div>";
                    html += "</div></div>";
                } else {
                    html += '<div class="flex items-center gap-2 p-2 bg-[#152054] rounded border border-white/10">';
                    html += '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="flex-shrink-0 text-gray-400"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>';
                    html += '<a href="' + deps.esc(imgUrl) + '" target="_blank" class="text-xs text-blue-400 hover:underline truncate">' + deps.esc(m.name || "Download") + "</a>";
                    html += "</div>";
                }
            });
            html += "</div>";
            container.innerHTML = html;
        }

        function renderExternalTodos(todos) {
            var container = document.getElementById("kb-modal-todos");
            container.innerHTML = "";
            if (!todos.length) {
                container.innerHTML = '<p class="text-xs text-gray-500 italic">No tasks</p>';
                return;
            }
            todos.forEach(function(todo) {
                var row = document.createElement("div");
                row.className = "flex items-center gap-2 text-xs";
                row.innerHTML = '<input type="checkbox" ' + (todo.done ? "checked" : "") + ' class="accent-[#f97316]" disabled>' +
                    '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + deps.esc(todo.text) + "</span>";
                container.appendChild(row);
            });
        }

        function ticketMetaField(label, valueHtml) {
            return '<div class="kb-ticket-meta-field"><div class="kb-ticket-meta-label">' + deps.esc(label) +
                '</div><div class="kb-ticket-meta-value">' + valueHtml + "</div></div>";
        }

        function openExternalTicketModal(ticket, source) {
            if (typeof deps.prepareExternalTicketModal === "function") {
                deps.prepareExternalTicketModal();
            }
            var titleEl = document.getElementById("kb-modal-ticket-title");
            if (titleEl) {
                titleEl.value = ticket.title || "";
                titleEl.readOnly = true;
                titleEl.classList.add("bg-[#152054]/50", "cursor-not-allowed");
            }
            var estimateInput = document.getElementById("kb-modal-ticket-estimate");
            var durationInput = document.getElementById("kb-modal-ticket-duration");
            if (estimateInput) {
                estimateInput.value = ticket.time_estimate || "";
                estimateInput.readOnly = true;
                estimateInput.classList.add("bg-[#152054]/50", "cursor-not-allowed");
            }
            if (durationInput) {
                durationInput.value = ticket.time_spent || "";
                durationInput.readOnly = true;
                durationInput.classList.add("bg-[#152054]/50", "cursor-not-allowed");
            }
            var descArea = document.getElementById("kb-modal-ticket-desc");
            var rawDesc = ticket.description || "";
            var prevRich = descArea.parentElement.querySelector(".kb-ext-rich-desc");
            if (prevRich) prevRich.remove();
            descArea.style.display = "";
            var plainDesc = deps.stripHtml(rawDesc);
            if (plainDesc.length > 60000) {
                plainDesc = plainDesc.substring(0, 60000) + "\n…[truncated]";
            }
            descArea.value = plainDesc;
            descArea.readOnly = true;
            descArea.classList.add("bg-[#152054]/50", "cursor-not-allowed");

            document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
                btn.classList.add("opacity-50", "cursor-not-allowed");
                btn.disabled = true;
            });
            deps.setPriorityButtons(ticket.priority || "medium");
            if (typeof deps.setTicketComplexity === "function") {
                deps.setTicketComplexity(ticket.complexity || "medium");
            }
            var complexitySelect = document.getElementById("kb-modal-ticket-complexity");
            if (complexitySelect) {
                complexitySelect.disabled = true;
                complexitySelect.classList.add("opacity-50", "cursor-not-allowed");
            }
            deps.renderModalLinks([]);
            if (ticket.local_cache_id && typeof deps.setExternalModalTicketId === "function") {
                deps.setExternalModalTicketId(ticket.local_cache_id);
            }
            deps.renderModalLinks(ticket.links || []);
            if (typeof deps.renderModalFiles === "function") {
                deps.renderModalFiles(ticket.files || []);
            }
            if (ticket.local_cache_id && typeof deps.renderModalTodos === "function") {
                deps.renderModalTodos(ticket.todos || []);
            } else {
                renderExternalTodos(ticket.todos || []);
            }
            renderExternalMedia(ticket.media || [], source);
            var notesEl = document.getElementById("kb-modal-context-notes");
            if (notesEl) {
                notesEl.value = ticket.context_notes || "";
                notesEl.readOnly = false;
                notesEl.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
            }

            var modalFooter = document.getElementById("kb-modal-footer");
            if (modalFooter) modalFooter.classList.remove("hidden");
            var modalActions = document.getElementById("kb-modal-actions");
            if (modalActions) modalActions.classList.add("hidden");
            var urlLink = document.getElementById("kb-modal-url-link");
            if (urlLink) {
                if (ticket.url) {
                    urlLink.href = ticket.url;
                    var sourceLabel = "Go to " + source.charAt(0).toUpperCase() + source.slice(1);
                    urlLink.innerHTML = externalSourceLinkMarkup(source);
                    urlLink.title = sourceLabel;
                    urlLink.setAttribute("aria-label", sourceLabel);
                    urlLink.classList.remove("hidden");
                } else {
                    urlLink.classList.add("hidden");
                    urlLink.removeAttribute("href");
                    urlLink.innerHTML = "";
                }
            }
            document.getElementById("kb-modal-title").textContent = ticket.title || "Ticket";
            window._extTicketData = ticket;
            window._extTicketSource = source;
            deps.switchTicketTab("details");
            document.getElementById("kb-ticket-modal").classList.remove("hidden");
        }

        function createTicketCard(ticket, isLocal, boardData) {
            boardData = boardData || deps.getCurrentBoardData() || {};
            var currentBoard = deps.getCurrentBoard();
            var card = document.createElement("div");
            card.className = "kb-card bg-[#1a1f3a] rounded-lg border border-white/20 p-4 cursor-pointer hover:border-[#f97316]/50 transition-colors relative";
            card.dataset.ticketId = String(ticket.id);
            var extDnD = !isLocal && currentBoard && (currentBoard.source === "trello" || currentBoard.source === "jira");
            if (isLocal || extDnD) {
                card.draggable = true;
                card.addEventListener("dragstart", function(e) {
                    e.dataTransfer.setData("text/plain", String(ticket.id));
                    card.classList.add("dragging");
                });
                card.addEventListener("dragend", function() { card.classList.remove("dragging"); });
            }
            var pri = (ticket.priority || "medium").toLowerCase();
            var priClass = "kb-pri-" + pri;
            var cleanDesc = deps.stripHtml(ticket.description || "");
            var truncatedDesc = deps.truncate(cleanDesc, 120);
            var todoCount = (ticket.todos || []).length;
            var todoDone = (ticket.todos || []).filter(function(t) { return t.done; }).length;
            var todoHtml = todoCount ? '<span class="text-xs text-gray-500 ml-2">✓ ' + todoDone + "/" + todoCount + "</span>" : "";
            var labelsHtml = "";
            if (ticket.labels && ticket.labels.length) {
                labelsHtml = '<div class="flex flex-wrap gap-1 mt-1">' + ticket.labels.map(function(lb) {
                    return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + deps.esc(lb) + "</span>";
                }).join("") + "</div>";
            }
            var membersHtml = "";
            if (ticket.members && ticket.members.length) {
                membersHtml = '<div class="flex items-center gap-1 mt-1">' + ticket.members.map(function(m) {
                    return '<span class="text-[10px] text-gray-400">' + deps.esc(m) + "</span>";
                }).join(", ") + "</div>";
            }
            var timeHtml = "";
            if (ticket.time_estimate || ticket.time_spent) {
                timeHtml = '<div class="text-[10px] text-gray-500 mt-1">';
                if (ticket.time_estimate) timeHtml += "⏱ " + deps.esc(ticket.time_estimate);
                if (ticket.time_spent) timeHtml += " / " + deps.esc(ticket.time_spent) + " done";
                timeHtml += "</div>";
            }
            var mediaHtml = "";
            if (ticket.media && ticket.media.length) {
                mediaHtml = '<div class="flex flex-wrap gap-1 mt-1">';
                ticket.media.forEach(function(m) {
                    var imgUrl = m.url;
                    var thumbUrl = m.thumbnail || m.url;
                    if (!isLocal && currentBoard && currentBoard.source === "jira" && imgUrl) {
                        imgUrl = "/api/tickets/external-boards/jira/proxy-image?url=" + encodeURIComponent(imgUrl);
                        thumbUrl = m.thumbnail ? "/api/tickets/external-boards/jira/proxy-image?url=" + encodeURIComponent(m.thumbnail) : imgUrl;
                    }
                    var mtype = (m.type || "").startsWith("video/") ? "video" : "image";
                    if (mtype === "image") {
                        mediaHtml += '<div class="relative rounded overflow-hidden border border-white/10">';
                        mediaHtml += '<img src="' + deps.esc(thumbUrl || imgUrl) + '" alt="' + deps.esc(m.name || "attachment") + '" class="max-w-[80px] max-h-[60px] object-cover rounded" loading="lazy" onerror="this.style.display=\'none\'">';
                        mediaHtml += "</div>";
                    } else {
                        mediaHtml += '<a href="' + deps.esc(imgUrl) + '" target="_blank" class="text-[10px] text-blue-400 hover:underline flex items-center gap-0.5">';
                        mediaHtml += '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>' + deps.esc(m.name || "file") + "</a>";
                    }
                });
                mediaHtml += "</div>";
            }

            var hasProject = !!(ticket.linked_project_id || boardData.default_project_id);
            var workflowStatus = (ticket.workflow_status || "").toLowerCase();
            var workflowStatusBadgeHtml = "";
            if (workflowStatus) {
                var wfIsActive = isLocal && (workflowStatus === "running" || workflowStatus === "waiting");
                var wfColorClass = "bg-gray-500/25 text-gray-200";
                if (workflowStatus === "running" || workflowStatus === "waiting") wfColorClass = "bg-sky-500/25 text-sky-200" + (wfIsActive ? " cursor-pointer hover:bg-sky-500/40" : "");
                else if (workflowStatus === "completed") wfColorClass = "bg-green-500/25 text-green-200";
                else if (workflowStatus === "failed" || workflowStatus === "cancelled") wfColorClass = "bg-red-500/25 text-red-200";
                var wfTag = wfIsActive ? "button" : "span";
                workflowStatusBadgeHtml = '<' + wfTag + ' class="kb-wf-status-badge ' + wfColorClass + ' text-[10px] px-1.5 py-0.5 rounded font-medium">' + deps.esc(workflowStatus) + "</" + wfTag + ">";
            }
            var canDelete = isLocal;
            var canTransfer = !isLocal;
            card.innerHTML = buildCardMarkup({
                esc: deps.esc,
                title: ticket.title || "",
                priority: pri,
                complexity: ticket.complexity || "medium",
                priorityClass: priClass,
                description: truncatedDesc,
                labelsHtml: labelsHtml,
                membersHtml: membersHtml,
                timeHtml: timeHtml,
                mediaHtml: mediaHtml,
                todoHtml: todoHtml,
                ticketUrl: ticket.url || "",
                source: ticket.source_provider || (currentBoard && currentBoard.source ? currentBoard.source : "database"),
                isLocal: isLocal,
                hasProject: hasProject,
                workflowStatusBadgeHtml: workflowStatusBadgeHtml,
                canTransfer: canTransfer,
                canDelete: canDelete,
            });

            bindTicketActions(card, ticket, isLocal, hasProject);
            return card;
        }

        function buildListDragHandleHtml(draggable) {
            if (!draggable) {
                return '<span class="kb-ticket-list-drag-handle kb-ticket-list-drag-handle--static" aria-hidden="true"></span>';
            }
            return '<span class="kb-ticket-list-drag-handle" title="Drag to another lane" aria-label="Drag to another lane">' +
                '<svg width="10" height="16" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true">' +
                '<circle cx="2.5" cy="2" r="1.2"/><circle cx="7.5" cy="2" r="1.2"/>' +
                '<circle cx="2.5" cy="8" r="1.2"/><circle cx="7.5" cy="8" r="1.2"/>' +
                '<circle cx="2.5" cy="14" r="1.2"/><circle cx="7.5" cy="14" r="1.2"/>' +
                "</svg></span>";
        }

        function externalBoardSupportsListDrag(currentBoard) {
            if (!currentBoard) return false;
            return currentBoard.source === "trello" || currentBoard.source === "jira";
        }

        function ticketListDescriptionHtml(cleanDesc) {
            if (!cleanDesc) return "";
            return '<div class="kb-ticket-list-desc" tabindex="0"><div class="kb-ticket-list-desc-track"><span>' + deps.esc(cleanDesc) + "</span></div></div>";
        }

        function bindTicketListRowDrag(row, ticketId, enabled) {
            if (!enabled) return;
            var handle = row.querySelector(".kb-ticket-list-drag-handle");
            if (!handle) return;
            handle.draggable = true;
            handle.addEventListener("dragstart", function(e) {
                e.stopPropagation();
                var section = row.closest(".kb-ticket-list-section");
                e.dataTransfer.setData("text/plain", String(ticketId));
                e.dataTransfer.setData("application/x-kanban-source-lane", section ? (section.dataset.laneId || "") : "");
                e.dataTransfer.effectAllowed = "move";
                row.classList.add("dragging");
            });
            handle.addEventListener("dragend", function() {
                row.classList.remove("dragging");
            });
        }

        function createTicketListRow(ticket, isLocal, boardData, listOpts) {
            listOpts = listOpts || {};
            boardData = boardData || deps.getCurrentBoardData() || {};
            var currentBoard = deps.getCurrentBoard();
            var row = document.createElement("div");
            row.className = "kb-ticket-list-row";
            row.dataset.ticketId = String(ticket.id);
            var canDrag = isLocal || (!isLocal && externalBoardSupportsListDrag(currentBoard));
            var hasProject = !!(ticket.linked_project_id || boardData.default_project_id);
            var canDelete = isLocal;
            var canTransfer = !listOpts.hideTransfer && !isLocal;
            var source = ticket.source_provider || (currentBoard && currentBoard.source ? currentBoard.source : "database");
            var cleanDesc = deps.stripHtml(ticket.description || "").replace(/\s+/g, " ").trim();
            var descHtml = ticketListDescriptionHtml(cleanDesc);
            var contentClass = "kb-ticket-list-content" + (cleanDesc ? "" : " kb-ticket-list-content--no-desc");
            var actionRowHtml = buildActionRow({
                layout: "list",
                hasProject: hasProject,
                canTransfer: canTransfer,
                canDelete: canDelete,
                hideWorkflow: !!listOpts.hideWorkflow,
                hideCopy: !!listOpts.hideCopy,
                hideAgent: !!listOpts.hideAgent,
                showAddToWorkflow: !!listOpts.showAddToWorkflow,
            });
            row.innerHTML =
                '<div class="kb-ticket-list-prefix">' +
                    buildListDragHandleHtml(canDrag) +
                    '<span class="kb-ticket-list-badges">' + buildSourceBadge(source) + buildComplexityBadge(ticket.complexity) + buildPriorityBadge(ticket.priority) + "</span>" +
                "</div>" +
                '<div class="' + contentClass + '">' +
                    '<span class="kb-ticket-list-title">' + deps.esc(ticket.title || "") + "</span>" +
                    descHtml +
                "</div>" +
                '<div class="kb-ticket-list-actions">' + actionRowHtml + "</div>";
            bindTicketListRowDrag(row, ticket.id, canDrag && !listOpts.disableListDrag);
            bindTicketActions(row, ticket, isLocal, hasProject);
            return row;
        }

        return {
            renderExternalMedia: renderExternalMedia,
            renderExternalTodos: renderExternalTodos,
            openExternalTicketModal: openExternalTicketModal,
            createTicketCard: createTicketCard,
            createTicketListRow: createTicketListRow,
            initListRowMarquee: initListRowMarquee,
            copyTicketToClipboard: copyTicketToClipboard,
            ticketClipboardText: ticketClipboardText,
        };
    }

    function createTicketModalSections(deps) {
        function renderModalLinks(links) {
            var container = document.getElementById("kb-modal-links");
            container.innerHTML = "";
            links.forEach(function(link) {
                var row = document.createElement("div");
                row.className = "flex items-center gap-2 text-xs";
                row.innerHTML = '<a href="' + deps.esc(link.url) + '" target="_blank" class="text-[#f97316] hover:underline flex-1 truncate">' + deps.esc(link.title) + '</a>' +
                    '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
                row.querySelector("button").onclick = function() { deleteLink(link.id); };
                container.appendChild(row);
            });
        }

        function addLink() {
            var title = document.getElementById("kb-modal-link-title").value.trim();
            var url = document.getElementById("kb-modal-link-url").value.trim();
            if (!title || !url) { deps.showSnackbar("Title and URL required", "error"); return; }
            var ensure = typeof deps.ensureModalTicketId === "function" ? deps.ensureModalTicketId() : Promise.resolve(deps.getModalTicketId());
            ensure.then(function(ticketId) {
                if (!ticketId) throw new Error("No ticket cache available");
                return deps.apiFetch("/api/tickets/tickets/" + ticketId + "/links", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: title, url: url })
                });
            }).then(function() {
                document.getElementById("kb-modal-link-title").value = "";
                document.getElementById("kb-modal-link-url").value = "";
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function deleteLink(linkId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/tickets/tickets/" + deps.getModalTicketId() + "/links/" + linkId, { method: "DELETE" })
                .then(function() { refreshModalTicket(); })
                .catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function renderModalFiles(files) {
            var container = document.getElementById("kb-modal-files");
            container.innerHTML = "";
            files.forEach(function(f) {
                var row = document.createElement("div");
                row.className = "flex items-center gap-2 text-xs";
                var url = f.url || (f.id && deps.getModalTicketId() ? "/api/tickets/tickets/" + encodeURIComponent(deps.getModalTicketId()) + "/files/" + encodeURIComponent(f.id) + "/content" : "");
                var label = deps.esc(f.filename || f.name || "Attachment");
                row.innerHTML = (url
                    ? '<a class="text-blue-300 hover:text-blue-200 flex-1 truncate" href="' + deps.esc(url) + '" target="_blank" rel="noopener noreferrer">📎 ' + label + '</a>'
                    : '<span class="text-gray-300 flex-1 truncate">📎 ' + label + '</span>') +
                    '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
                row.querySelector("button").onclick = function() { deleteFile(f.id); };
                container.appendChild(row);
            });
        }

        function uploadFiles(fileList) {
            if (!fileList.length) return;
            var ensure = typeof deps.ensureModalTicketId === "function" ? deps.ensureModalTicketId() : Promise.resolve(deps.getModalTicketId());
            ensure.then(function(ticketId) {
                if (!ticketId) throw new Error("No ticket cache available");
                var promises = [];
                for (var i = 0; i < fileList.length; i++) {
                    var form = new FormData();
                    form.append("file", fileList[i]);
                    promises.push(deps.apiFetch("/api/tickets/tickets/" + ticketId + "/files", { method: "POST", body: form }));
                }
                return Promise.all(promises);
            }).then(function() {
                deps.showSnackbar("Files uploaded");
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Upload failed: " + e.message, "error"); });
        }

        function deleteFile(fileId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/tickets/tickets/" + deps.getModalTicketId() + "/files/" + fileId, { method: "DELETE" })
                .then(function() { refreshModalTicket(); })
                .catch(function(e) { deps.showSnackbar("Upload failed: " + e.message, "error"); });
        }

        function renderModalTodos(todos) {
            var container = document.getElementById("kb-modal-todos");
            container.innerHTML = "";
            todos.forEach(function(todo) {
                var row = document.createElement("div");
                row.className = "flex items-center gap-2 text-xs";
                row.innerHTML =
                    '<input type="checkbox" ' + (todo.done ? "checked" : "") + ' class="accent-[#f97316]">' +
                    '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + deps.esc(todo.text) + '</span>' +
                    '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
                row.querySelector("input").onchange = function() { toggleTodo(todo.id, !todo.done); };
                row.querySelector("button").onclick = function() { deleteTodo(todo.id); };
                container.appendChild(row);
            });
        }

        function addTodo() {
            var input = document.getElementById("kb-modal-todo-input");
            var text = input.value.trim();
            if (!text) return;
            var ensure = typeof deps.ensureModalTicketId === "function" ? deps.ensureModalTicketId() : Promise.resolve(deps.getModalTicketId());
            ensure.then(function(ticketId) {
                if (!ticketId) throw new Error("No ticket cache available");
                return deps.apiFetch("/api/tickets/tickets/" + ticketId + "/todos", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text: text })
                });
            }).then(function() {
                input.value = "";
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function toggleTodo(todoId, done) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/tickets/tickets/" + deps.getModalTicketId() + "/todos/" + todoId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ done: done })
            }).then(function() { refreshModalTicket(); }).catch(function() {});
        }

        function deleteTodo(todoId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/tickets/tickets/" + deps.getModalTicketId() + "/todos/" + todoId, { method: "DELETE" })
                .then(function() { refreshModalTicket(); }).catch(function() {});
        }

        function refreshModalTicket() {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/tickets/tickets/" + deps.getModalTicketId()).then(function(t) {
                renderModalLinks(t.links || []);
                renderModalFiles(t.files || []);
                renderModalTodos(t.todos || []);
                if (typeof deps.loadModalAuditReport === "function") {
                    deps.loadModalAuditReport(deps.getModalTicketId(), t.audit_entries || []);
                } else if (typeof deps.renderModalAuditEntries === "function") {
                    deps.renderModalAuditEntries(t.audit_entries || []);
                }
            }).catch(function() {});
        }

        function loadLinkableEntities(ticket) {
            deps.apiFetch("/api/tickets/linkable").then(function(data) {
                populateSelect("kb-modal-link-workflow", data.workflows, "id", "title", ticket.linked_workflow_id);
                populateSelect("kb-modal-link-project", data.projects, "id", "name", ticket.linked_project_id);
            }).catch(function() {});
        }

        function populateSelect(selectId, items, valKey, labelKey, selectedVal, opts) {
            opts = opts || {};
            var sel = document.getElementById(selectId);
            if (!sel) return;
            var emptyLabel = opts.emptyLabel || "Inherit from board default";
            sel.innerHTML = '<option value="">' + emptyLabel + "</option>";
            (items || []).forEach(function(it) {
                var o = document.createElement("option");
                o.value = it[valKey];
                o.textContent = it[labelKey] || ("Item #" + it[valKey]);
                if (selectedVal && String(it[valKey]) === String(selectedVal)) o.selected = true;
                sel.appendChild(o);
            });
            if (window.KanbanCustomSelect) {
                window.KanbanCustomSelect.refresh(sel);
            }
        }

        return {
            renderModalLinks: renderModalLinks,
            addLink: addLink,
            deleteLink: deleteLink,
            renderModalFiles: renderModalFiles,
            uploadFiles: uploadFiles,
            deleteFile: deleteFile,
            renderModalTodos: renderModalTodos,
            addTodo: addTodo,
            toggleTodo: toggleTodo,
            deleteTodo: deleteTodo,
            refreshModalTicket: refreshModalTicket,
            loadLinkableEntities: loadLinkableEntities,
            populateSelect: populateSelect,
        };
    }

    function createTicketActions(deps) {
        var laneCopyAllIconSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>';

        function preferredCopyLaneId(lanes) {
            if (!lanes || !lanes.length) return null;
            var backlog = lanes.find(function(lane) {
                return String(lane.name || "").trim().toLowerCase() === "backlog";
            });
            if (backlog && backlog.id != null) return String(backlog.id);
            return lanes[0].id != null ? String(lanes[0].id) : null;
        }

        function populateCopyBoardSelect() {
            var sel = document.getElementById("kb-copy-board-select");
            if (!sel) return Promise.resolve(null);
            sel.innerHTML = "";
            var dbBoards = deps.getDbBoards();
            if (!dbBoards.length) {
                sel.innerHTML = '<option value="">No boards available</option>';
                document.getElementById("kb-copy-confirm").disabled = true;
                return Promise.resolve(null);
            }
            document.getElementById("kb-copy-confirm").disabled = false;
            dbBoards.forEach(function(b) {
                var opt = document.createElement("option");
                opt.value = b.id;
                opt.textContent = b.name;
                sel.appendChild(opt);
            });
            var boardId = parseInt(sel.value, 10);
            if (!boardId) return Promise.resolve(null);
            return refreshCopyLaneSelect(boardId);
        }

        function refreshCopyLaneSelect(boardId) {
            var laneSel = document.getElementById("kb-copy-lane-select");
            if (!laneSel) return Promise.resolve();
            laneSel.innerHTML = '<option value="">Loading lanes...</option>';
            laneSel.disabled = true;
            if (!boardId) {
                laneSel.innerHTML = '<option value="">Select a board first</option>';
                return Promise.resolve();
            }
            return deps.apiFetch("/api/tickets/boards/" + boardId).then(function(boardData) {
                var lanes = (boardData && boardData.lanes) || [];
                laneSel.innerHTML = "";
                if (!lanes.length) {
                    laneSel.innerHTML = '<option value="">No lanes on this board</option>';
                    document.getElementById("kb-copy-confirm").disabled = true;
                    return;
                }
                lanes.forEach(function(lane) {
                    var opt = document.createElement("option");
                    opt.value = lane.id;
                    opt.textContent = lane.name || ("Lane " + lane.id);
                    laneSel.appendChild(opt);
                });
                var preferred = preferredCopyLaneId(lanes);
                if (preferred) laneSel.value = preferred;
                laneSel.disabled = false;
                document.getElementById("kb-copy-confirm").disabled = false;
            }).catch(function(e) {
                laneSel.innerHTML = '<option value="">Could not load lanes</option>';
                deps.showSnackbar("Could not load lanes: " + e.message, "error");
            });
        }

        function showCopyModalUi(state) {
            var titleEl = document.getElementById("kb-copy-modal-title");
            var hintEl = document.getElementById("kb-copy-modal-hint");
            if (state.mode === "lane") {
                var count = (state.tickets || []).length;
                if (titleEl) titleEl.textContent = "Copy column to local board";
                if (hintEl) {
                    hintEl.textContent = "Copy " + count + " ticket" + (count === 1 ? "" : "s") +
                        ' from "' + (state.laneName || "column") + '" into a local board lane.';
                }
            } else {
                if (titleEl) titleEl.textContent = "Copy to local board";
                if (hintEl) hintEl.textContent = "Choose a local board and lane for this ticket.";
            }
            populateCopyBoardSelect().then(function() {
                document.getElementById("kb-copy-modal").classList.remove("hidden");
            });
        }

        function buildCopyTicketPayload(ticketFields, boardId, laneId, linkedProjectId) {
            var copyPayload = {
                board_id: boardId,
                lane_id: laneId,
                title: ticketFields.title,
                description: ticketFields.external_source
                    ? (ticketFields.description || "")
                    : deps.stripHtml(ticketFields.description || ""),
                priority: ticketFields.priority || "medium",
                time_estimate: ticketFields.time_estimate || "",
                time_spent: ticketFields.time_spent || "",
                external_source: ticketFields.external_source,
                external_id: ticketFields.external_id,
                external_url: ticketFields.external_url,
                complexity: ticketFields.complexity || null,
                media: Array.isArray(ticketFields.media) ? ticketFields.media : [],
                todos: Array.isArray(ticketFields.todos) ? ticketFields.todos : [],
            };
            if (linkedProjectId) copyPayload.linked_project_id = linkedProjectId;
            if (typeof deps.mergeSourceChatIntoPayload === "function") {
                deps.mergeSourceChatIntoPayload(copyPayload);
            }
            return copyPayload;
        }

        function addTicket() {
            var currentBoard = deps.getCurrentBoard();
            var currentBoardData = deps.getCurrentBoardData();
            if (!currentBoard) return;
            if (currentBoardData && currentBoardData.can_create_ticket === false) {
                deps.showSnackbar("You do not have permission to create tickets on this board", "error");
                return;
            }
            deps.openCreateExternalTicketModal();
        }

        function openCopyModal(ticket) {
            var currentBoard = deps.getCurrentBoard();
            deps.setCopyModalState({
                mode: "single",
                ticket: {
                    title: ticket.title || "",
                    description: ticket.description || "",
                    priority: ticket.priority || "medium",
                    time_estimate: ticket.time_estimate || "",
                    time_spent: ticket.time_spent || "",
                    complexity: ticket.complexity || null,
                    external_source: ticket.external_source || (currentBoard.source !== "database" ? currentBoard.source : null),
                    external_id: ticket.external_id || (currentBoard.source !== "database" ? String(ticket.id) : null),
                    external_url: ticket.external_url || ticket.url || "",
                    media: Array.isArray(ticket.media) ? ticket.media : [],
                    todos: Array.isArray(ticket.todos) ? ticket.todos : [],
                },
            });
            showCopyModalUi(deps.getCopyModalState());
        }

        function openCopyLaneModal(lane) {
            var currentBoard = deps.getCurrentBoard();
            var tickets = (lane && lane.tickets) || [];
            if (!tickets.length) {
                deps.showSnackbar("No tickets in this column");
                return;
            }
            deps.setCopyModalState({
                mode: "lane",
                laneName: lane.name || "Column",
                tickets: tickets.map(function(ticket) {
                    return {
                        title: ticket.title || "",
                        description: ticket.description || "",
                        priority: ticket.priority || "medium",
                        time_estimate: ticket.time_estimate || "",
                        time_spent: ticket.time_spent || "",
                        complexity: ticket.complexity || null,
                        external_source: ticket.external_source || (currentBoard.source !== "database" ? currentBoard.source : null),
                        external_id: ticket.external_id || (currentBoard.source !== "database" ? String(ticket.id) : null),
                        external_url: ticket.external_url || ticket.url || "",
                        media: Array.isArray(ticket.media) ? ticket.media : [],
                        todos: Array.isArray(ticket.todos) ? ticket.todos : [],
                    };
                }),
            });
            showCopyModalUi(deps.getCopyModalState());
        }

        function closeCopyModal() {
            document.getElementById("kb-copy-modal").classList.add("hidden");
            deps.setCopyModalState(null);
        }

        function onCopyBoardChanged() {
            var boardId = parseInt(document.getElementById("kb-copy-board-select").value, 10);
            refreshCopyLaneSelect(boardId);
        }

        function confirmCopy() {
            var copyModalState = deps.getCopyModalState();
            if (!copyModalState) return;
            var boardId = parseInt(document.getElementById("kb-copy-board-select").value, 10);
            var laneId = parseInt(document.getElementById("kb-copy-lane-select").value, 10);
            if (!boardId) { deps.showSnackbar("Select a board", "error"); return; }
            if (!laneId) { deps.showSnackbar("Select a lane", "error"); return; }
            var boardData = deps.getCurrentBoardData() || {};
            var linkedProjectId = boardData.default_project_id || null;
            var confirmBtn = document.getElementById("kb-copy-confirm");
            if (confirmBtn) {
                confirmBtn.disabled = true;
                confirmBtn.textContent = "Copying...";
            }
            var finish = function() {
                if (confirmBtn) {
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = "Copy";
                }
            };
            if (copyModalState.mode === "lane") {
                var bulkPayload = {
                    board_id: boardId,
                    lane_id: laneId,
                    tickets: copyModalState.tickets || [],
                };
                if (linkedProjectId) bulkPayload.linked_project_id = linkedProjectId;
                deps.apiFetch("/api/tickets/tickets/bulk-copy-to-board", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(bulkPayload),
                }).then(function(result) {
                    var copied = Number(result.copied || 0);
                    var reused = Number(result.reused || 0);
                    var skipped = Number(result.skipped || 0);
                    var parts = [];
                    if (copied) parts.push(copied + " copied");
                    if (reused) parts.push(reused + " already local");
                    if (skipped) parts.push(skipped + " skipped");
                    deps.showSnackbar(parts.length ? parts.join(", ") : "No tickets copied");
                    if (result.errors && result.errors.length) {
                        deps.showSnackbar(result.errors[0], "error");
                    }
                    closeCopyModal();
                    var currentBoard = deps.getCurrentBoard();
                    if (currentBoard && currentBoard.source === "database" && currentBoard.id === boardId) {
                        deps.selectBoard("database", boardId);
                    }
                }).catch(function(e) {
                    deps.showSnackbar("Copy failed: " + e.message, "error");
                }).finally(finish);
                return;
            }
            var copyPayload = buildCopyTicketPayload(
                copyModalState.ticket || {},
                boardId,
                laneId,
                linkedProjectId
            );
            deps.apiFetch("/api/tickets/tickets/copy-external-to-board", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(copyPayload)
            }).then(function() {
                deps.showSnackbar("Ticket copied to board");
                closeCopyModal();
                var currentBoard = deps.getCurrentBoard();
                if (currentBoard && currentBoard.source === "database" && currentBoard.id === boardId) {
                    deps.selectBoard("database", boardId);
                }
            }).catch(function(e) {
                deps.showSnackbar("Copy failed: " + e.message, "error");
            }).finally(finish);
        }

        var laneWhatsappIconSvg = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.27-1.38a9.9 9.9 0 0 0 4.77 1.21h.01c5.46 0 9.91-4.45 9.91-9.91C21.96 6.45 17.51 2 12.04 2Zm0 18.16h-.01a8.2 8.2 0 0 1-4.18-1.14l-.3-.18-3.12.82.83-3.04-.2-.31a8.2 8.2 0 0 1-1.26-4.39c0-4.54 3.7-8.24 8.25-8.24 2.2 0 4.27.86 5.82 2.42a8.2 8.2 0 0 1 2.42 5.83c0 4.54-3.7 8.23-8.25 8.23Zm4.52-6.17c-.25-.12-1.47-.72-1.7-.81-.23-.08-.39-.12-.56.13-.16.24-.64.8-.78.96-.14.16-.29.18-.54.06-.25-.13-1.04-.38-1.99-1.22-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.02-.38.11-.5.11-.11.25-.29.37-.43.12-.15.16-.25.25-.41.08-.17.04-.31-.02-.43-.06-.13-.56-1.35-.77-1.85-.2-.49-.41-.42-.56-.43h-.48c-.16 0-.43.06-.65.31-.23.25-.86.84-.86 2.04 0 1.2.88 2.37 1 2.53.12.17 1.73 2.64 4.18 3.7.58.25 1.04.4 1.39.51.58.18 1.11.16 1.53.1.47-.07 1.47-.6 1.68-1.18.21-.58.21-1.08.14-1.18-.06-.1-.22-.16-.47-.28Z"/></svg>';

        return {
            addTicket: addTicket,
            openCopyModal: openCopyModal,
            openCopyLaneModal: openCopyLaneModal,
            closeCopyModal: closeCopyModal,
            confirmCopy: confirmCopy,
            onCopyBoardChanged: onCopyBoardChanged,
            laneCopyAllButtonHtml: function(tooltip) {
                return '<button type="button" class="kb-lane-copy-all" title="' + deps.esc(tooltip || "Copy all to local board") +
                    '" aria-label="' + deps.esc(tooltip || "Copy all to local board") + '">' + laneCopyAllIconSvg + "</button>";
            },
            laneWhatsappSnapshotButtonHtml: function(opts) {
                opts = opts || {};
                var tooltip = opts.tooltip || "Create ticket from linked WhatsApp messages";
                return '<button type="button" class="kb-lane-whatsapp-snapshot" title="' + deps.esc(tooltip) +
                    '" aria-label="' + deps.esc(tooltip) + '" data-board-id="' + deps.esc(String(opts.boardId || "")) +
                    '" data-link-id="' + deps.esc(String(opts.linkId || "")) + '" data-lane-id="' + deps.esc(String(opts.laneId || "")) +
                    '" data-lane-name="' + deps.esc(opts.laneName || "") + '" data-board-name="' + deps.esc(opts.boardName || "") +
                    '" data-link-phone="' + deps.esc(opts.linkPhone || "") + '">' + laneWhatsappIconSvg + "</button>";
            },
        };
    }

    function createExternalTicketModal(deps) {
        var cetProgressTimer = null;
        var cetProgressValue = 8;

        function stopCetProgress(finalValue) {
            if (cetProgressTimer) {
                clearInterval(cetProgressTimer);
                cetProgressTimer = null;
            }
            if (typeof finalValue === "number") {
                cetProgressValue = finalValue;
                var bar = document.getElementById("kb-cet-loading-bar");
                if (bar) bar.style.width = Math.max(0, Math.min(100, finalValue)) + "%";
            }
        }

        function updateCetProgress(step, detail, value) {
            var titleEl = document.getElementById("kb-cet-loading-title");
            var detailEl = document.getElementById("kb-cet-loading-detail");
            var stepEl = document.getElementById("kb-cet-loading-step");
            var bar = document.getElementById("kb-cet-loading-bar");
            if (titleEl) titleEl.textContent = step || "Preparing WhatsApp Ticket...";
            if (detailEl && detail) detailEl.textContent = detail;
            if (stepEl) stepEl.textContent = step || "Working";
            if (typeof value === "number") cetProgressValue = value;
            if (bar) bar.style.width = Math.max(4, Math.min(96, cetProgressValue)) + "%";
        }

        function startCetProgress(step, detail, value) {
            stopCetProgress();
            cetProgressValue = typeof value === "number" ? value : 8;
            updateCetProgress(step, detail, cetProgressValue);
            cetProgressTimer = setInterval(function() {
                var cap = cetProgressValue < 40 ? 68 : 92;
                if (cetProgressValue < cap) {
                    cetProgressValue += cetProgressValue < 40 ? 3 : 1;
                    updateCetProgress(step, detail, cetProgressValue);
                }
            }, 900);
        }

        function setCetLoadingOverlay(isLoading, detail, step, progress) {
            var overlay = document.getElementById("kb-cet-loading-overlay");
            if (!overlay) return;
            if (isLoading) overlay.classList.remove("hidden");
            else overlay.classList.add("hidden");
            if (isLoading) {
                if (!cetProgressTimer) startCetProgress(step || "Preparing WhatsApp Ticket...", detail || "Working through WhatsApp context.", progress);
                else updateCetProgress(step || "Preparing WhatsApp Ticket...", detail || "Working through WhatsApp context.", progress);
            } else {
                stopCetProgress(100);
            }
        }

        function setCetPriority(priority) {
            var pri = priority || "medium";
            var input = document.getElementById("kb-cet-priority");
            if (input) input.value = pri;
            document.querySelectorAll("#kb-cet-priority-btns button").forEach(function(btn) {
                if (btn.dataset.cetPri === pri) btn.click();
            });
        }

        function resetCetComposeStatus(statusEl, text, colorClass) {
            if (!statusEl) return;
            statusEl.classList.remove("text-gray-500", "text-green-400", "text-yellow-500", "text-red-400", "text-[#f97316]");
            statusEl.classList.add(colorClass || "text-gray-500");
            statusEl.textContent = text || "";
        }

        function summarizeComposeError(errorText) {
            var raw = String(errorText || "").trim();
            if (!raw) return "AI compose failed";
            var modelMatch = raw.match(/'message':\s*'([^']+)'/);
            if (modelMatch && modelMatch[1]) return modelMatch[1];
            var jsonMatch = raw.match(/"message"\s*:\s*"([^"]+)"/);
            if (jsonMatch && jsonMatch[1]) return jsonMatch[1];
            return raw.length > 220 ? raw.slice(0, 217) + "..." : raw;
        }

        function renderCetWaMedia(media) {
            var mediaContainer = document.getElementById("kb-cet-wa-media");
            var countEl = document.getElementById("kb-cet-attach-count");
            if (!mediaContainer) return;
            mediaContainer.innerHTML = "";
            media = media || [];
            if (!countEl) return;
            if (!media.length) {
                countEl.classList.add("hidden");
                return;
            }
            countEl.textContent = media.length;
            countEl.classList.remove("hidden");
            var label = document.createElement("div");
            label.className = "text-sm text-gray-400 mb-1";
            label.textContent = "WhatsApp Media (will be linked to ticket)";
            mediaContainer.appendChild(label);
            media.forEach(function(m) {
                var item = document.createElement("div");
                item.className = "flex items-center gap-2 p-2 bg-[#0a1030] rounded border border-white/10";
                var icon = m.media_type === "photo" || m.media_type === "image" ? "&#128247;" : "&#128206;";
                var preview = "";
                if (m.media_type === "photo" || m.media_type === "image") {
                    preview = '<img src="' + m.media_path + '" class="w-10 h-10 object-cover rounded" onerror="this.style.display=\'none\'">';
                } else {
                    preview = '<div class="w-10 h-10 flex items-center justify-center bg-white/5 rounded text-lg">' + icon + "</div>";
                }
                item.innerHTML = preview + '<div class="flex-1 min-w-0"><div class="text-sm text-white truncate">' + deps.esc(m.media_filename) + '</div><div class="text-xs text-gray-500">' + deps.esc(m.media_type) + '</div></div><input type="hidden" name="wa-media" value="' + m.message_id + '">';
                mediaContainer.appendChild(item);
            });
        }

        function openBoardWhatsappSnapshotTicket(opts) {
            opts = opts || {};
            var boardId = opts.boardId;
            var linkId = opts.linkId;
            var laneId = opts.laneId;
            var laneName = opts.laneName || "Backlog";
            var boardName = opts.boardName || "Board";
            var linkPhone = opts.linkPhone || "";
            if (!boardId || !linkId) {
                deps.showSnackbar("Link a WhatsApp chat to this board first", "error");
                return;
            }
            if (deps.getWaTicketComposeInFlight && deps.getWaTicketComposeInFlight()) return;
            if (deps.setWaTicketComposeInFlight) deps.setWaTicketComposeInFlight(true);
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            if (!modal) {
                if (deps.setWaTicketComposeInFlight) deps.setWaTicketComposeInFlight(false);
                deps.showSnackbar("Ticket form not found", "error");
                return;
            }
            document.getElementById("kb-cet-heading").textContent = "Create Ticket from WhatsApp";
            document.getElementById("kb-cet-board-row").style.display = "none";
            setCreateTicketBoardValue("database", boardId, boardName);
            var currentBoardData = deps.getCurrentBoardData && deps.getCurrentBoardData();
            var lanes = (currentBoardData && currentBoardData.lanes) || [];
            populateCreateTicketLanes(lanes, laneId, laneName);
            var titleEl = document.getElementById("kb-cet-title");
            var descEl = document.getElementById("kb-cet-desc");
            var statusEl = document.getElementById("kb-cet-distill-status");
            titleEl.value = "WhatsApp ticket";
            descEl.value = "";
            titleEl.dataset.composeBaseTitle = "WhatsApp ticket";
            descEl.dataset.composeBaseDesc = "";
            titleEl.disabled = false;
            descEl.disabled = false;
            setCetPriority("medium");
            var complexityEl = document.getElementById("kb-cet-complexity");
            if (complexityEl) complexityEl.value = "auto";
            var submitBtn = document.getElementById("kb-cet-submit");
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
            }
            modal.dataset.whatsappPhone = linkPhone;
            modal.dataset.whatsappBoardId = String(boardId);
            modal.dataset.whatsappMsgIds = "[]";
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
            renderCetWaMedia([]);
            resetCetComposeStatus(statusEl, "Draft ready; loading WhatsApp context...", "text-[#f97316]");
            modal.classList.remove("hidden");
            cetSwitchTab("details");
            setCetLoadingOverlay(true, "Syncing WhatsApp messages from relay, then loading board context.", "Syncing WhatsApp", 4);
            var syncNote = "";
            deps.apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).then(function(syncResult) {
                if (syncResult && syncResult.error) {
                    syncNote = " Sync skipped: " + syncResult.error + ". Using locally stored messages.";
                } else if (syncResult && typeof syncResult.synced === "number") {
                    syncNote = " Synced " + syncResult.synced + " message(s) from relay.";
                }
            }).catch(function() {
                syncNote = " Sync unavailable; using locally stored messages.";
            }).finally(function() {
                setCetLoadingOverlay(true, "Finding new WhatsApp messages for this board." + syncNote, "Collecting messages", 12);
                return deps.apiFetch("/api/tickets/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-preview", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        link_id: parseInt(linkId, 10),
                        limit: 500,
                        scope: "new_since_last_ticket",
                    }),
                });
            }).then(function(data) {
                if (!data || data.empty) {
                    setCetLoadingOverlay(false);
                    if (deps.setWaTicketComposeInFlight) deps.setWaTicketComposeInFlight(false);
                    deps.showSnackbar((data && data.empty_reason) ? "No new WhatsApp messages for this board" : "No WhatsApp messages to ticket", "error");
                    closeCreateExtTicketModal();
                    return;
                }
                modal.dataset.whatsappMsgIds = JSON.stringify(data.message_ids || []);
                if (data.contact_name && !linkPhone) {
                    modal.dataset.whatsappPhone = data.contact_name;
                }
                if (data.lane_id) {
                    populateCreateTicketLanes(lanes, data.lane_id, data.lane_name || laneName);
                }
                titleEl.value = data.title || "WhatsApp ticket";
                descEl.value = data.description || "";
                titleEl.dataset.composeBaseTitle = titleEl.value;
                descEl.dataset.composeBaseDesc = descEl.value;
                setCetPriority(data.priority || "medium");
                if (complexityEl) complexityEl.value = data.complexity || "auto";
                renderCetWaMedia(data.media || []);
                setCetLoadingOverlay(true, "Reading messages, attaching media, and transcribing voice notes where needed.", "Preparing media", 42);
                if (!data.message_ids || !data.message_ids.length) {
                    setCetLoadingOverlay(false);
                    if (deps.setWaTicketComposeInFlight) deps.setWaTicketComposeInFlight(false);
                    resetCetComposeStatus(statusEl, "Draft updated; you can edit before creating.", "text-green-400");
                    return;
                }
                composeWaTicket(data.message_ids, titleEl, descEl, statusEl);
            }).catch(function(e) {
                setCetLoadingOverlay(false);
                if (deps.setWaTicketComposeInFlight) deps.setWaTicketComposeInFlight(false);
                deps.showSnackbar(e.message || "Could not load WhatsApp messages", "error");
                closeCreateExtTicketModal();
            });
        }

        function composeWaTicket(messageIds, titleEl, descEl, statusEl) {
            titleEl = titleEl || document.getElementById("kb-cet-title");
            descEl = descEl || document.getElementById("kb-cet-desc");
            statusEl = statusEl || document.getElementById("kb-cet-distill-status");
            if (!messageIds || !messageIds.length) return;
            resetCetComposeStatus(statusEl, "Draft ready; improving from WhatsApp context...", "text-[#f97316]");
            setCetLoadingOverlay(true, "Transcribing voice notes, extracting image text, reading attachments, and composing one clean ticket.", "Composing WhatsApp ticket", 26);
            deps.apiFetch("/api/tickets/whatsapp/compose-ticket", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({message_ids: messageIds})
            }).then(function(r) {
                deps.setWaTicketComposeInFlight(false);
                setCetLoadingOverlay(false);
                titleEl.placeholder = "Ticket title";
                descEl.placeholder = "Describe the ticket...";
                var submitBtn = document.getElementById("kb-cet-submit");
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
                }
                var baseTitle = titleEl.dataset.composeBaseTitle || "";
                var baseDesc = descEl.dataset.composeBaseDesc || "";
                if (r.title && (!titleEl.value.trim() || titleEl.value === baseTitle)) titleEl.value = r.title;
                if (r.description && (!descEl.value.trim() || descEl.value === baseDesc)) descEl.value = r.description;
                if (r.priority) setCetPriority(r.priority);
                var complexityEl = document.getElementById("kb-cet-complexity");
                if (complexityEl) complexityEl.value = r.complexity || "auto";
                renderCetWaMedia(r.media || []);
                if (r.fallback) {
                    var err = summarizeComposeError(r.compose_error || r.error);
                    resetCetComposeStatus(statusEl, "Ticket drafted locally. AI compose failed: " + err, "text-yellow-500");
                } else {
                    var complexityText = r.complexity ? " · complexity: " + r.complexity : "";
                    resetCetComposeStatus(statusEl, "Draft updated; you can edit before creating" + complexityText, "text-green-400");
                }
            }).catch(function(e) {
                deps.setWaTicketComposeInFlight(false);
                setCetLoadingOverlay(false);
                titleEl.placeholder = "Ticket title";
                descEl.placeholder = "Describe the ticket...";
                var submitBtn = document.getElementById("kb-cet-submit");
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
                }
                resetCetComposeStatus(statusEl, "Using quick draft. AI compose request failed: " + summarizeComposeError(e && e.message ? e.message : e), "text-yellow-500");
            });
        }

        function setCreateTicketBoardValue(source, boardId, label) {
            var boardSelect = document.getElementById("kb-cet-board");
            if (!boardSelect) return;
            source = source || "database";
            boardSelect.innerHTML = "";
            var opt = document.createElement("option");
            opt.value = source + ":" + boardId;
            opt.textContent = label || "Current board";
            opt.selected = true;
            boardSelect.appendChild(opt);
        }

        function populateCreateTicketLanes(lanes, selectedValue, preferredName) {
            var laneSelect = document.getElementById("kb-cet-lane");
            if (!laneSelect) return;
            laneSelect.innerHTML = '<option value="">Select a lane...</option>';
            lanes = Array.isArray(lanes) ? lanes : [];
            lanes.forEach(function(lane) {
                var opt = document.createElement("option");
                opt.value = lane.id != null ? String(lane.id) : (lane.name || "");
                opt.textContent = lane.name || String(lane.id || "");
                laneSelect.appendChild(opt);
            });
            var preferred = "";
            if (selectedValue != null && selectedValue !== "") {
                preferred = String(selectedValue);
            } else if (preferredName) {
                var match = lanes.find(function(lane) {
                    return String(lane.name || "").toLowerCase() === String(preferredName).toLowerCase();
                });
                if (match) preferred = match.id != null ? String(match.id) : (match.name || "");
            }
            if (!preferred) {
                var backlog = lanes.find(function(lane) { return String(lane.name || "").toLowerCase() === "backlog"; });
                var first = backlog || lanes[0];
                if (first) preferred = first.id != null ? String(first.id) : (first.name || "");
            }
            laneSelect.value = preferred;
        }

        function loadCreateTicketLanes(source, boardId, selectedValue, preferredName) {
            source = source || "database";
            if (!boardId) {
                populateCreateTicketLanes([], "", "");
                return Promise.resolve();
            }
            if (source === "database") {
                return deps.apiFetch("/api/tickets/boards/" + encodeURIComponent(boardId)).then(function(boardData) {
                    populateCreateTicketLanes(boardData.lanes || [], selectedValue, preferredName);
                });
            }
            return deps.apiFetch("/api/tickets/external-boards/" + source + "/" + encodeURIComponent(boardId)).then(function(extBoard) {
                var lanes = extBoard.lanes || extBoard.columns || extBoard.lists || [];
                populateCreateTicketLanes(lanes, selectedValue, preferredName);
            });
        }

        function openTicketFromWhatsApp(phone, title, description, boardId, msgs) {
            if (deps.getWaTicketComposeInFlight()) return;
            deps.setWaTicketComposeInFlight(true);
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            if (!modal) { deps.setWaTicketComposeInFlight(false); deps.showSnackbar("Ticket form not found"); return; }
            document.getElementById("kb-cet-board-row").style.display = "";
            document.getElementById("kb-cet-heading").textContent = "Create Ticket from WhatsApp";
            var titleEl = document.getElementById("kb-cet-title");
            var descEl = document.getElementById("kb-cet-desc");
            var submitBtn = document.getElementById("kb-cet-submit");
            var quickTitle = (title || "").trim() || "WhatsApp ticket";
            var quickDesc = (description || "").trim();
            titleEl.value = quickTitle;
            descEl.value = quickDesc;
            titleEl.dataset.composeBaseTitle = quickTitle;
            descEl.dataset.composeBaseDesc = quickDesc;
            titleEl.disabled = false;
            descEl.disabled = false;
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
            }
            titleEl.placeholder = "Ticket title";
            descEl.placeholder = "Describe the ticket...";
            setCetPriority("medium");
            var complexityEl = document.getElementById("kb-cet-complexity");
            if (complexityEl) complexityEl.value = "auto";
            var statusEl = document.getElementById("kb-cet-distill-status");
            resetCetComposeStatus(statusEl, "Draft ready; loading WhatsApp context...", "text-[#f97316]");
            modal.dataset.whatsappPhone = phone;
            modal.dataset.whatsappMsgIds = JSON.stringify(msgs.map(function(m) { return m.id; }));
            modal.dataset.whatsappBoardId = boardId;
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
            modal.classList.remove("hidden");
            cetSwitchTab("details");
            var msgIds = msgs.map(function(m) { return m.id; });
            setCetLoadingOverlay(true, "Opening the ticket form now. The transcript, image text, media context, and draft will fill in as they are ready.", "Collecting WhatsApp context", 8);
            composeWaTicket(msgIds, titleEl, descEl, statusEl);
            Promise.all([
                deps.apiFetch("/api/tickets/boards"),
                deps.getExternalBoards(false).catch(function() { return {trello: [], jira: []}; })
            ]).then(function(results) {
                var boards = results[0];
                var extData = results[1];
                var boardSelect = document.getElementById("kb-cet-board");
                boardSelect.innerHTML = "";
                var dbBs = boards.filter(function(b) { return b.source === "database"; });
                if (dbBs.length) {
                    var dbOptgroup = document.createElement("optgroup");
                    dbOptgroup.label = "Local";
                    dbBs.forEach(function(b) {
                        var opt = document.createElement("option");
                        opt.value = "database:" + b.id;
                        opt.textContent = b.name;
                        if (b.id == boardId) opt.selected = true;
                        dbOptgroup.appendChild(opt);
                    });
                    boardSelect.appendChild(dbOptgroup);
                }
                var trelloBs = (extData && extData.trello) || [];
                if (trelloBs.length) {
                    var trelloOptgroup = document.createElement("optgroup");
                    trelloOptgroup.label = "Trello";
                    trelloBs.forEach(function(b) {
                        var opt = document.createElement("option");
                        opt.value = "trello:" + b.id;
                        opt.textContent = b.name;
                        if (b.id == boardId) opt.selected = true;
                        trelloOptgroup.appendChild(opt);
                    });
                    boardSelect.appendChild(trelloOptgroup);
                }
                var jiraBs = (extData && extData.jira) || [];
                if (jiraBs.length) {
                    var jiraOptgroup = document.createElement("optgroup");
                    jiraOptgroup.label = "Jira";
                    jiraBs.forEach(function(b) {
                        var opt = document.createElement("option");
                        opt.value = "jira:" + b.id;
                        opt.textContent = b.name;
                        if (b.id == boardId) opt.selected = true;
                        jiraOptgroup.appendChild(opt);
                    });
                    boardSelect.appendChild(jiraOptgroup);
                }
                document.getElementById("kb-cet-board-row").style.display = "";
                function loadLanes(source, bid) {
                    return loadCreateTicketLanes(source, bid, "", "Backlog").catch(function() {});
                }
                var firstOpt = boardSelect.options[boardSelect.selectedIndex];
                if (!firstOpt && boardSelect.options.length > 0) {
                    boardSelect.selectedIndex = 0;
                    firstOpt = boardSelect.options[0];
                }
                if (firstOpt) {
                    var firstVal = firstOpt.value.split(":");
                    loadLanes(firstVal[0], firstVal[1]);
                }
                boardSelect.onchange = function() {
                    var selVal = boardSelect.value.split(":");
                    loadLanes(selVal[0], selVal[1]);
                };
                document.getElementById("kb-cet-files-list").innerHTML = "";
                document.getElementById("kb-cet-file-input").value = "";
            });
        }

        function openCreateExternalTicketModal() {
            var currentBoard = deps.getCurrentBoard();
            var currentBoardData = deps.getCurrentBoardData();
            if (!currentBoard || !currentBoard.source) {
                deps.showSnackbar("Select a board first", "error");
                return;
            }
            if (currentBoardData && currentBoardData.can_create_ticket === false) {
                deps.showSnackbar("You do not have permission to create tickets on this board", "error");
                return;
            }
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            document.getElementById("kb-cet-title").value = "";
            document.getElementById("kb-cet-desc").value = "";
            setCreateTicketBoardValue(currentBoard.source, currentBoard.id, currentBoard.name);
            if (currentBoardData && currentBoardData.lanes) {
                populateCreateTicketLanes(currentBoardData.lanes, "", "Backlog");
            } else {
                loadCreateTicketLanes(currentBoard.source, currentBoard.id, "", "Backlog").catch(function() {
                    populateCreateTicketLanes([], "", "");
                });
            }
            setCetPriority("medium");
            var complexityEl = document.getElementById("kb-cet-complexity");
            if (complexityEl) complexityEl.value = "auto";
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
            document.getElementById("kb-cet-heading").textContent = currentBoard.source === "database"
                ? "Create Ticket"
                : "Create " + (currentBoard.source === "trello" ? "Trello" : "Jira") + " Ticket";
            var boardRow = document.getElementById("kb-cet-board-row");
            if (boardRow) boardRow.style.display = "none";
            setCetLoadingOverlay(false);
            cetSwitchTab("details");
            modal.classList.remove("hidden");
        }

        function cetSwitchTab(tab) {
            var detailsPanel = document.getElementById("kb-cet-panel-details");
            var attachPanel = document.getElementById("kb-cet-panel-attachments");
            var detailsTab = document.getElementById("kb-cet-tab-details");
            var attachTab = document.getElementById("kb-cet-tab-attachments");
            if (tab === "details") {
                detailsPanel.classList.remove("hidden");
                attachPanel.classList.add("hidden");
                detailsTab.classList.add("text-[#f97316]", "border-[#f97316]");
                detailsTab.classList.remove("text-gray-400", "border-transparent");
                attachTab.classList.remove("text-[#f97316]", "border-[#f97316]");
                attachTab.classList.add("text-gray-400", "border-transparent");
            } else {
                detailsPanel.classList.add("hidden");
                attachPanel.classList.remove("hidden");
                attachTab.classList.add("text-[#f97316]", "border-[#f97316]");
                attachTab.classList.remove("text-gray-400", "border-transparent");
                detailsTab.classList.remove("text-[#f97316]", "border-[#f97316]");
                detailsTab.classList.add("text-gray-400", "border-transparent");
            }
        }

        function closeCreateExtTicketModal() {
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            modal.classList.add("hidden");
            deps.setWaTicketComposeInFlight(false);
            setCetLoadingOverlay(false);
            delete modal.dataset.whatsappPhone;
            delete modal.dataset.whatsappMsgIds;
            delete modal.dataset.whatsappBoardId;
            var boardRow = document.getElementById("kb-cet-board-row");
            if (boardRow) boardRow.style.display = "none";
            cetSwitchTab("details");
            var mediaContainer = document.getElementById("kb-cet-wa-media");
            if (mediaContainer) mediaContainer.innerHTML = "";
            var countEl = document.getElementById("kb-cet-attach-count");
            if (countEl) countEl.classList.add("hidden");
            var statusEl = document.getElementById("kb-cet-distill-status");
            if (statusEl) statusEl.textContent = "";
            var titleEl = document.getElementById("kb-cet-title");
            var descEl = document.getElementById("kb-cet-desc");
            var submitBtn = document.getElementById("kb-cet-submit");
            var complexityEl = document.getElementById("kb-cet-complexity");
            if (titleEl) { titleEl.disabled = false; titleEl.placeholder = "Ticket title"; delete titleEl.dataset.composeBaseTitle; }
            if (descEl) { descEl.disabled = false; descEl.placeholder = "Describe the ticket..."; delete descEl.dataset.composeBaseDesc; }
            if (complexityEl) complexityEl.value = "auto";
            if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove("opacity-50", "cursor-not-allowed"); }
        }

        function uploadExtTicketAttachments(source, extTicketId, files) {
            var promises = [];
            for (var i = 0; i < files.length; i++) {
                var fd = new FormData();
                fd.append("file", files[i]);
                promises.push(deps.apiFetch("/api/tickets/external-boards/" + source + "/" + encodeURIComponent(extTicketId) + "/attach", {
                    method: "POST", body: fd
                }).catch(function(e) { console.error("Failed to upload attachment:", e); }));
            }
            return Promise.all(promises);
        }

        function submitCreateExtTicket() {
            var title = document.getElementById("kb-cet-title").value.trim();
            if (!title) { deps.showSnackbar("Title is required", "error"); return; }
            var desc = document.getElementById("kb-cet-desc").value.trim();
            var laneId = document.getElementById("kb-cet-lane").value;
            var priority = document.getElementById("kb-cet-priority").value;
            var complexityEl = document.getElementById("kb-cet-complexity");
            var complexity = complexityEl ? (complexityEl.value || "auto") : "auto";
            var boardSelect = document.getElementById("kb-cet-board");
            var selectedValue = boardSelect ? boardSelect.value : "";
            var parts = selectedValue.split(":");
            var boardSource = parts[0] || "database";
            var boardIdRaw = parts[1] || "";
            var boardId = boardSource === "database" ? (parseInt(boardIdRaw, 10) || 0) : boardIdRaw;
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            var waPhone = modal ? modal.dataset.whatsappPhone : "";
            var waMsgIds = modal ? (modal.dataset.whatsappMsgIds || "[]") : "[]";

            if (boardSource === "database") {
                if (!laneId) { deps.showSnackbar("Select a lane", "error"); return; }
                if (waPhone) {
                    var sourceMsgIds = JSON.parse(waMsgIds);
                    deps.apiFetch("/api/tickets/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-ticket", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            lane_id: parseInt(laneId, 10),
                            message_ids: sourceMsgIds,
                            title: title,
                            description: desc,
                            priority: priority || "medium",
                            complexity: complexity
                        })
                    }).then(function(r) {
                        if (!r || !r.success) { deps.showSnackbar("Failed to create WhatsApp ticket", "error"); return; }
                        deps.showSnackbar("Ticket created");
                        closeCreateExtTicketModal();
                        deps.selectBoard("database", boardId);
                        deps.refreshWaThreadIfOpen();
                    }).catch(function(e) {
                        var detail = e && e.detail ? e.detail : null;
                        var quality = detail && detail.quality ? detail.quality : null;
                        var problems = quality ? (quality.issues || []).concat(quality.warnings || []) : [];
                        deps.showSnackbar("Failed: " + (e.message || "Could not create WhatsApp ticket") + (problems.length ? ": " + problems.slice(0, 2).join("; ") : ""), "error");
                    });
                    return;
                }
                var cetPayload = { title: title, description: desc, lane_id: parseInt(laneId, 10), priority: priority || "medium", complexity: complexity, board_id: boardId };
                if (typeof deps.mergeSourceChatIntoPayload === "function") {
                    deps.mergeSourceChatIntoPayload(cetPayload);
                }
                deps.apiFetch("/api/tickets/tickets", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(cetPayload)
                }).then(function(r) {
                    if (!r || !r.success) { deps.showSnackbar("Failed to create ticket", "error"); return; }
                    deps.showSnackbar("Ticket created");
                    if (waPhone) {
                        var msgIdList = JSON.parse(waMsgIds);
                        deps.apiFetch("/api/tickets/whatsapp/messages/mark-snapshot-group", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({ jid_phone: waPhone, snapshot_group: r.id + "_" + r.lane_id, message_ids: msgIdList })
                        }).catch(function() {});
                        var mediaEls = document.querySelectorAll("#kb-cet-wa-media input[name=wa-media]");
                        var attachMsgIds = [];
                        mediaEls.forEach(function(el) {
                            var mediaMsgId = parseInt(el.value, 10);
                            if (mediaMsgId) attachMsgIds.push(mediaMsgId);
                        });
                        if (!attachMsgIds.length) attachMsgIds = msgIdList;
                        attachMsgIds.forEach(function(msgId) {
                            deps.apiFetch("/api/tickets/tickets/" + r.id + "/attach-whatsapp-media", {
                                method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({ message_id: msgId })
                            }).catch(function() {});
                        });
                    }
                    closeCreateExtTicketModal();
                    deps.selectBoard("database", boardId);
                    if (waPhone) deps.refreshWaThreadIfOpen();
                }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
                return;
            }

            var extSource = boardSource;
            var extBoardId = boardId;
            var selectedOption = boardSelect ? boardSelect.options[boardSelect.selectedIndex] : null;
            var extBoardName = selectedOption ? selectedOption.textContent : "";
            deps.getExternalBoards(false).then(function(extData) {
                var allExt = (extData.trello || []).concat(extData.jira || []);
                var extBoard = allExt.find(function(b) { return b.source === extSource && b.id === extBoardId; });
                if (!extBoard) extBoard = allExt.find(function(b) { return b.source === extSource && b.name === extBoardName; });
                if (!extBoard) { deps.showSnackbar("Could not find " + extSource + " board", "error"); return; }
                var payload = { title: title, description: desc, lane_id: laneId || null, priority: priority };
                var fileInput = document.getElementById("kb-cet-file-input");
                var files = fileInput.files;
                deps.showSnackbar("Creating ticket on " + (extSource === "trello" ? "Trello" : "Jira") + "...");
                deps.apiFetch("/api/tickets/external-boards/" + extSource + "/" + encodeURIComponent(extBoard.id) + "/create-ticket", {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
                }).then(function(r) {
                    if (r.success && r.ticket) {
                        if (waPhone) {
                            var msgIdList = JSON.parse(waMsgIds);
                            deps.apiFetch("/api/tickets/whatsapp/messages/mark-snapshot-group", {
                                method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({ jid_phone: waPhone, snapshot_group: "ext_" + r.ticket.id, message_ids: msgIdList })
                            }).catch(function() {});
                        }
                        if (files && files.length > 0 && r.ticket.id) return uploadExtTicketAttachments(extSource, r.ticket.id, files).then(function() { return r; });
                        return r;
                    }
                    return r;
                }).then(function() {
                    deps.showSnackbar("Ticket created on " + (extSource === "trello" ? "Trello" : "Jira"));
                    closeCreateExtTicketModal();
                    deps.selectBoard(extSource, extBoard.id, extBoard.url || "");
                    if (waPhone) deps.refreshWaThreadIfOpen();
                }).catch(function(e) { deps.showSnackbar("Failed to create ticket: " + e.message, "error"); });
            }).catch(function(e) { deps.showSnackbar("Failed to load external boards: " + e.message, "error"); });
        }

        function handleCetFileSelect() {
            var fileInput = document.getElementById("kb-cet-file-input");
            var listDiv = document.getElementById("kb-cet-files-list");
            listDiv.innerHTML = "";
            var files = fileInput.files;
            for (var i = 0; i < files.length; i++) {
                var f = files[i];
                var div = document.createElement("div");
                div.className = "text-xs text-gray-400 flex items-center gap-1";
                div.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>' + deps.esc(f.name) + ' <span class="text-gray-600">(' + (f.size < 1024 * 1024 ? (f.size / 1024).toFixed(1) + "KB" : (f.size / 1024 / 1024).toFixed(1) + "MB") + ')</span>';
                listDiv.appendChild(div);
            }
        }

        return {
            setCetLoadingOverlay: setCetLoadingOverlay,
            composeWaTicket: composeWaTicket,
            openTicketFromWhatsApp: openTicketFromWhatsApp,
            openBoardWhatsappSnapshotTicket: openBoardWhatsappSnapshotTicket,
            openCreateExternalTicketModal: openCreateExternalTicketModal,
            cetSwitchTab: cetSwitchTab,
            closeCreateExtTicketModal: closeCreateExtTicketModal,
            submitCreateExtTicket: submitCreateExtTicket,
            uploadExtTicketAttachments: uploadExtTicketAttachments,
            handleCetFileSelect: handleCetFileSelect,
        };
    }

    function normalizeComplexityLevel(value) {
        var raw = String(value || "medium").toLowerCase().trim().replace(/[\s-]+/g, "_");
        if (raw === "i" || raw === "1") return "low";
        if (raw === "ii" || raw === "2") return "medium";
        if (raw === "iii" || raw === "3") return "high";
        if (raw === "extra_high" || raw === "extrahigh" || raw === "xhigh") return "extra_high";
        if (raw === "low" || raw === "medium" || raw === "high") return raw;
        return "medium";
    }

    function complexityRank(value) {
        var level = normalizeComplexityLevel(value);
        var ranks = { extra_high: 5, high: 4, medium: 3, low: 1 };
        return ranks[level] != null ? ranks[level] : 2;
    }

    function normalizePriorityLevel(value) {
        var raw = String(value || "medium").toLowerCase().trim();
        if (raw === "urgent") return "critical";
        if (raw === "normal") return "medium";
        if (raw === "critical" || raw === "high" || raw === "low") return raw;
        return "medium";
    }

    function priorityRank(value) {
        var level = normalizePriorityLevel(value);
        var ranks = { critical: 5, high: 4, medium: 3, low: 1 };
        return ranks[level] != null ? ranks[level] : 2;
    }

    /** List view: highest complexity first, then priority, then lane position. */
    function compareTicketsForListView(a, b) {
        var cxDiff = complexityRank(b.complexity) - complexityRank(a.complexity);
        if (cxDiff) return cxDiff;
        var priDiff = priorityRank(b.priority) - priorityRank(a.priority);
        if (priDiff) return priDiff;
        var posA = typeof a.position === "number" ? a.position : parseInt(a.position, 10) || 0;
        var posB = typeof b.position === "number" ? b.position : parseInt(b.position, 10) || 0;
        if (posA !== posB) return posA - posB;
        return String(a.title || "").localeCompare(String(b.title || ""), undefined, { sensitivity: "base" });
    }

    window.KanbanTicketUi = {
        create: createTicketUi,
        compareTicketsForListView: compareTicketsForListView,
        complexityRank: complexityRank,
        priorityRank: priorityRank,
    };
    window.KanbanTicketModalSections = { create: createTicketModalSections };
    window.KanbanTicketActions = { create: createTicketActions };
    window.KanbanExternalTicketModal = { create: createExternalTicketModal };
})();
