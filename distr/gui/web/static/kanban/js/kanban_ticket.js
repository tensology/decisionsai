(function() {
    "use strict";

    function createTicketUi(deps) {
        function buildSourceBadge(source) {
            if (!source || source === "database") return "";
            return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-300 font-medium">' + deps.esc(source) + "</span>";
        }

        function buildComplexityBadge(complexity) {
            var level = (complexity || "medium").toLowerCase();
            var numeral = "II";
            if (level === "low") numeral = "I";
            if (level === "high") numeral = "III";
            return '<span class="kb-complexity-numeral" title="Complexity: ' + deps.esc(level) + '" aria-label="Complexity ' + deps.esc(level) + '">' + numeral + "</span>";
        }

        function buildExternalLink(ticketUrl, source) {
            if (!ticketUrl) return "";
            var sourceLabel = source || "browser";
            return '<a href="' + deps.esc(ticketUrl) + '" target="_blank" class="kb-card-action-btn text-white transition-colors" data-tooltip="Open in ' + deps.esc(sourceLabel) + '" aria-label="Open in ' + deps.esc(sourceLabel) + '" onclick="event.stopPropagation()">' +
                '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>';
        }

        function actionButtonHtml(config) {
            if (config.hidden) return "";
            var disabled = !!config.disabled;
            var stateClass = disabled ? "text-gray-700 cursor-not-allowed" : "text-white";
            var tooltip = config.tooltip || "";
            return '<span class="kb-card-action-tip">' +
                '<button class="' + config.keyClass + " kb-card-action-btn " + stateClass + ' transition-colors" title="' + tooltip + '" aria-label="' + tooltip + '"' + (disabled ? " disabled" : "") + ">" +
                config.iconSvg + "</button></span>";
        }

        function buildActionRow(opts) {
            var leftActions = [
                actionButtonHtml({
                    keyClass: "kb-act-copy",
                    tooltip: "Copy ticket title and description",
                    nativeTooltip: true,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
                }),
                actionButtonHtml({
                    keyClass: "kb-act-workflow",
                    tooltip: "Send to Workflow",
                    nativeTooltip: true,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 12h5"/><path d="M13 12l3-4"/><path d="M13 12l3 4"/></svg>',
                }),
                actionButtonHtml({
                    keyClass: "kb-act-cli",
                    tooltip: "Push to CLI",
                    hidden: !opts.hasProject,
                    nativeTooltip: true,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
                }),
            ];
            leftActions.push(actionButtonHtml({
                keyClass: "kb-act-project",
                tooltip: "Send to Project (.tickets)",
                hidden: !opts.hasProject,
                nativeTooltip: true,
                iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
            }));
            if (opts.canTransfer) {
                leftActions.push(actionButtonHtml({
                    keyClass: "kb-act-transfer",
                    tooltip: "Copy ticket into a local board",
                    disabled: false,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
                }));
            }
            leftActions.push(actionButtonHtml({
                keyClass: "kb-act-discuss",
                tooltip: "Let's talk about it — feeds this ticket into your agent chat (uses your current / last chat, or creates one)",
                nativeTooltip: true,
                iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
            }));
            var deleteAction = "";
            if (opts.canDelete) {
                deleteAction = actionButtonHtml({
                    keyClass: "kb-act-delete",
                    tooltip: "Delete ticket",
                    disabled: false,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>',
                });
            }
            return '<div class="kb-card-actions-left">' + leftActions.join("") + "</div>" +
                '<div class="kb-card-actions-right">' + deleteAction + "</div>";
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
                '<div class="flex items-center gap-1.5 flex-shrink-0">' + sourceBadge + complexityBadge + '<span class="' + opts.priorityClass + ' text-[10px] px-1.5 py-0.5 rounded text-white font-medium">' + deps.esc(opts.priority) + "</span>" + (opts.workflowStatusBadgeHtml || "") + extLinkHtml + "</div>" +
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
                    imgUrl = "/api/kanban/external-boards/jira/proxy-image?url=" + encodeURIComponent(imgUrl);
                    thumbUrl = m.thumbnail ? "/api/kanban/external-boards/jira/proxy-image?url=" + encodeURIComponent(m.thumbnail) : imgUrl;
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
            renderExternalMedia(ticket.media || [], source);
            renderExternalTodos(ticket.todos || []);

            var metaContainer = document.getElementById("kb-modal-external-meta");
            if (metaContainer) {
                var rowParts = [];
                if (ticket.url) {
                    rowParts.push(
                        '<div class="flex items-center gap-1.5 flex-shrink-0"><span class="text-gray-500">Source</span>' +
                        '<a href="' + deps.esc(ticket.url) + '" target="_blank" rel="noopener noreferrer" class="text-[#f97316] hover:underline whitespace-nowrap">Open in ' +
                        deps.esc(source.charAt(0).toUpperCase() + source.slice(1)) + "</a></div>"
                    );
                }
                if (ticket.members && ticket.members.length) {
                    rowParts.push(
                        '<div class="flex items-center gap-1.5 min-w-0 max-w-[220px]"><span class="text-gray-500 flex-shrink-0">Members</span>' +
                        '<span class="text-gray-200 truncate" title="' + deps.esc(ticket.members.join(", ")) + '">' +
                        ticket.members.map(deps.esc).join(", ") + "</span></div>"
                    );
                }
                if (ticket.labels && ticket.labels.length) {
                    rowParts.push(
                        '<div class="flex items-center gap-1.5 flex-wrap min-w-0"><span class="text-gray-500 flex-shrink-0">Labels</span>' +
                        '<span class="flex flex-wrap gap-1">' +
                        ticket.labels.map(function(lb) {
                            return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 whitespace-nowrap">' + deps.esc(lb) + "</span>";
                        }).join("") +
                        "</span></div>"
                    );
                }
                if (ticket.reporter) {
                    rowParts.push(
                        '<div class="flex items-center gap-1.5 flex-shrink-0"><span class="text-gray-500">Reporter</span>' +
                        '<span class="text-gray-200">' + deps.esc(ticket.reporter) + "</span></div>"
                    );
                }
                metaContainer.innerHTML =
                    '<div class="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">' + rowParts.join("") + "</div>";
                metaContainer.classList.remove("hidden");
            }

            document.getElementById("kb-modal-transfer-ext").classList.remove("hidden");
            document.getElementById("kb-modal-save").classList.add("hidden");
            document.getElementById("kb-modal-delete").classList.add("hidden");
            var projectActionBtn = document.getElementById("kb-modal-act-project");
            var cliActionBtn = document.getElementById("kb-modal-act-cli");
            if (projectActionBtn) {
                var currentBoardData = deps.getCurrentBoardData() || {};
                var canPush = !!currentBoardData.default_project_id;
                projectActionBtn.classList.toggle("hidden", !canPush);
                projectActionBtn.disabled = false;
                projectActionBtn.classList.remove("opacity-40", "cursor-not-allowed");
                projectActionBtn.title = "Send to Project (.tickets)";
                projectActionBtn.setAttribute("aria-label", projectActionBtn.title);
                if (cliActionBtn) {
                    cliActionBtn.classList.toggle("hidden", !canPush);
                    cliActionBtn.disabled = false;
                    cliActionBtn.classList.remove("opacity-40", "cursor-not-allowed");
                    cliActionBtn.title = "Push to CLI";
                    cliActionBtn.setAttribute("aria-label", cliActionBtn.title);
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
            var currentAgentStatus = deps.getCurrentAgentStatus();
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
            if (currentAgentStatus && currentAgentStatus.state !== "idle" && currentAgentStatus.current_ticket_id != null && String(currentAgentStatus.current_ticket_id) === String(ticket.id)) {
                card.classList.add("kb-in-progress");
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
                        imgUrl = "/api/kanban/external-boards/jira/proxy-image?url=" + encodeURIComponent(imgUrl);
                        thumbUrl = m.thumbnail ? "/api/kanban/external-boards/jira/proxy-image?url=" + encodeURIComponent(m.thumbnail) : imgUrl;
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

            card.addEventListener("click", function(e) {
                if (e.target.closest(".kb-card-actions") || e.target.closest(".kb-act-transfer") || e.target.closest(".kb-act-discuss") || e.target.closest("a")) return;
                if (isLocal) deps.openTicketModal(ticket.id);
                else openExternalTicketModal(ticket, currentBoard.source);
            });
            var copyBtn = card.querySelector(".kb-act-copy");
            if (copyBtn) copyBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                var text = deps.stripHtml(ticket.title) + (cleanDesc ? "\n\n" + cleanDesc : "");
                navigator.clipboard.writeText(text).then(function() { deps.showSnackbar("Copied to clipboard"); });
            });
            var cliBtn = card.querySelector(".kb-act-cli");
            if (cliBtn && !cliBtn.disabled) cliBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                if (isLocal) deps.pushTicketToCli(ticket.id, cliBtn);
                else deps.copyAndPushExternalTicket(ticket, currentBoard.source, "cli");
            });
            var projectBtn = card.querySelector(".kb-act-project");
            if (projectBtn && !projectBtn.disabled) projectBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                if (isLocal) deps.sendTicketToProjectById(ticket.id, projectBtn);
                else deps.copyAndPushExternalTicket(ticket, currentBoard.source, "project");
            });
            var transferBtn = card.querySelector(".kb-act-transfer");
            if (transferBtn && !transferBtn.disabled) transferBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                deps.openCopyModal(ticket);
            });
            var workflowBtn = card.querySelector(".kb-act-workflow");
            if (workflowBtn && !workflowBtn.disabled) workflowBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                if (typeof deps.openSendWorkflowModal === "function") {
                    deps.openSendWorkflowModal(ticket, isLocal ? null : currentBoard.source);
                }
            });
            var wfBadge = card.querySelector(".kb-wf-status-badge");
            if (wfBadge && wfBadge.tagName === "BUTTON" && isLocal && typeof deps.showRunPopover === "function") {
                wfBadge.addEventListener("click", function(e) {
                    e.stopPropagation();
                    deps.showRunPopover(wfBadge, ticket.id);
                });
            }
            var discussBtn = card.querySelector(".kb-act-discuss");
            if (discussBtn && !discussBtn.disabled) {
                discussBtn.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (typeof deps.startTicketDiscussion === "function") {
                        deps.startTicketDiscussion(ticket, isLocal);
                    }
                });
            }
            var delBtn = card.querySelector(".kb-act-delete");
            if (delBtn && !delBtn.disabled) delBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                var tid = ticket.id;
                deps.showKanbanConfirm({
                    title: "Delete ticket",
                    message: 'Delete "' + ticket.title + '"? This cannot be undone.',
                    confirmLabel: "Delete",
                    danger: true,
                    onConfirm: function() {
                        deps.hideKanbanConfirm();
                        deps.apiFetch("/api/kanban/tickets/" + tid, { method: "DELETE" }).then(function() {
                            deps.showSnackbar("Ticket deleted");
                            deps.reloadCurrentDatabaseBoard();
                        }).catch(function(err) {
                            deps.showSnackbar("Delete failed: " + err.message, "error");
                        });
                    }
                });
            });
            return card;
        }

        return {
            renderExternalMedia: renderExternalMedia,
            renderExternalTodos: renderExternalTodos,
            openExternalTicketModal: openExternalTicketModal,
            createTicketCard: createTicketCard,
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
            if (!deps.getModalTicketId()) return;
            var title = document.getElementById("kb-modal-link-title").value.trim();
            var url = document.getElementById("kb-modal-link-url").value.trim();
            if (!title || !url) { deps.showSnackbar("Title and URL required", "error"); return; }
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/links", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: title, url: url })
            }).then(function() {
                document.getElementById("kb-modal-link-title").value = "";
                document.getElementById("kb-modal-link-url").value = "";
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function deleteLink(linkId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/links/" + linkId, { method: "DELETE" })
                .then(function() { refreshModalTicket(); })
                .catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function renderModalFiles(files) {
            var container = document.getElementById("kb-modal-files");
            container.innerHTML = "";
            files.forEach(function(f) {
                var row = document.createElement("div");
                row.className = "flex items-center gap-2 text-xs";
                var url = f.url || (f.id && deps.getModalTicketId() ? "/api/kanban/tickets/" + encodeURIComponent(deps.getModalTicketId()) + "/files/" + encodeURIComponent(f.id) + "/content" : "");
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
            if (!deps.getModalTicketId() || !fileList.length) return;
            var promises = [];
            for (var i = 0; i < fileList.length; i++) {
                var form = new FormData();
                form.append("file", fileList[i]);
                promises.push(deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/files", { method: "POST", body: form }));
            }
            Promise.all(promises).then(function() {
                deps.showSnackbar("Files uploaded");
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Upload failed: " + e.message, "error"); });
        }

        function deleteFile(fileId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/files/" + fileId, { method: "DELETE" })
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
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/todos", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: text })
            }).then(function() {
                input.value = "";
                refreshModalTicket();
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
        }

        function toggleTodo(todoId, done) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/todos/" + todoId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ done: done })
            }).then(function() { refreshModalTicket(); }).catch(function() {});
        }

        function deleteTodo(todoId) {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId() + "/todos/" + todoId, { method: "DELETE" })
                .then(function() { refreshModalTicket(); }).catch(function() {});
        }

        function refreshModalTicket() {
            if (!deps.getModalTicketId()) return;
            deps.apiFetch("/api/kanban/tickets/" + deps.getModalTicketId()).then(function(t) {
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
            deps.apiFetch("/api/kanban/linkable").then(function(data) {
                populateSelect("kb-modal-link-workflow", data.workflows, "id", "title", ticket.linked_workflow_id);
                populateSelect("kb-modal-link-project", data.projects, "id", "name", ticket.linked_project_id);
            }).catch(function() {});
        }

        function populateSelect(selectId, items, valKey, labelKey, selectedVal) {
            var sel = document.getElementById(selectId);
            if (!sel) return;
            sel.innerHTML = '<option value="">Inherit from board default</option>';
            (items || []).forEach(function(it) {
                var o = document.createElement("option");
                o.value = it[valKey];
                o.textContent = it[labelKey] || ("Item #" + it[valKey]);
                if (selectedVal && String(it[valKey]) === String(selectedVal)) o.selected = true;
                sel.appendChild(o);
            });
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
            deps.setCopyTicketData({
                title: ticket.title || "",
                description: ticket.description || "",
                priority: ticket.priority || "medium",
                time_estimate: ticket.time_estimate || "",
                time_spent: ticket.time_spent || "",
                external_source: ticket.external_source || (currentBoard.source !== "database" ? currentBoard.source : null),
                external_id: ticket.external_id || (currentBoard.source !== "database" ? String(ticket.id) : null),
                external_url: ticket.external_url || ticket.url || "",
            });
            var sel = document.getElementById("kb-copy-board-select");
            sel.innerHTML = "";
            var dbBoards = deps.getDbBoards();
            if (!dbBoards.length) {
                sel.innerHTML = '<option value="">No boards available</option>';
                document.getElementById("kb-copy-confirm").disabled = true;
            } else {
                document.getElementById("kb-copy-confirm").disabled = false;
                dbBoards.forEach(function(b) {
                    var opt = document.createElement("option");
                    opt.value = b.id;
                    opt.textContent = b.name;
                    sel.appendChild(opt);
                });
            }
            document.getElementById("kb-copy-modal").classList.remove("hidden");
        }

        function closeCopyModal() {
            document.getElementById("kb-copy-modal").classList.add("hidden");
            deps.setCopyTicketData(null);
        }

        function confirmCopy() {
            var copyTicketData = deps.getCopyTicketData();
            if (!copyTicketData) return;
            var boardId = parseInt(document.getElementById("kb-copy-board-select").value, 10);
            if (!boardId) { deps.showSnackbar("Select a board", "error"); return; }
            var copyPayload = {
                board_id: boardId,
                title: copyTicketData.title,
                description: copyTicketData.external_source
                    ? (copyTicketData.description || "")
                    : deps.stripHtml(copyTicketData.description || ""),
                priority: copyTicketData.priority || "medium",
                time_estimate: copyTicketData.time_estimate || "",
                time_spent: copyTicketData.time_spent || "",
                external_source: copyTicketData.external_source,
                external_id: copyTicketData.external_id,
                external_url: copyTicketData.external_url,
            };
            if (typeof deps.mergeSourceChatIntoPayload === "function") {
                deps.mergeSourceChatIntoPayload(copyPayload);
            }
            deps.apiFetch("/api/kanban/tickets/copy-external-to-board", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(copyPayload)
            }).then(function() {
                deps.showSnackbar("Ticket copied to board");
                closeCopyModal();
                var currentBoard = deps.getCurrentBoard();
                if (currentBoard && currentBoard.source === "database" && currentBoard.id === boardId) {
                    deps.selectBoard("database", boardId);
                }
            }).catch(function(e) { deps.showSnackbar("Copy failed: " + e.message, "error"); });
        }

        return {
            addTicket: addTicket,
            openCopyModal: openCopyModal,
            closeCopyModal: closeCopyModal,
            confirmCopy: confirmCopy,
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

        function composeWaTicket(messageIds, titleEl, descEl, statusEl) {
            titleEl = titleEl || document.getElementById("kb-cet-title");
            descEl = descEl || document.getElementById("kb-cet-desc");
            statusEl = statusEl || document.getElementById("kb-cet-distill-status");
            if (!messageIds || !messageIds.length) return;
            resetCetComposeStatus(statusEl, "Draft ready; improving from WhatsApp context...", "text-[#f97316]");
            setCetLoadingOverlay(true, "Transcribing voice notes, extracting image text, reading attachments, and composing one clean ticket.", "Composing WhatsApp ticket", 26);
            deps.apiFetch("/api/kanban/whatsapp/compose-ticket", {
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
                if (complexityEl) complexityEl.value = r.complexity || "";
                var mediaContainer = document.getElementById("kb-cet-wa-media");
                mediaContainer.innerHTML = "";
                var media = r.media || [];
                var countEl = document.getElementById("kb-cet-attach-count");
                if (media.length) {
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
                        if (m.media_type === "photo" || m.media_type === "image") preview = '<img src="' + m.media_path + '" class="w-10 h-10 object-cover rounded" onerror="this.style.display=\'none\'">';
                        else preview = '<div class="w-10 h-10 flex items-center justify-center bg-white/5 rounded text-lg">' + icon + "</div>";
                        item.innerHTML = preview + '<div class="flex-1 min-w-0"><div class="text-sm text-white truncate">' + deps.esc(m.media_filename) + '</div><div class="text-xs text-gray-500">' + deps.esc(m.media_type) + '</div></div><input type="hidden" name="wa-media" value="' + m.message_id + '">';
                        mediaContainer.appendChild(item);
                    });
                } else {
                    countEl.classList.add("hidden");
                }
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
                return deps.apiFetch("/api/kanban/boards/" + encodeURIComponent(boardId)).then(function(boardData) {
                    populateCreateTicketLanes(boardData.lanes || [], selectedValue, preferredName);
                });
            }
            return deps.apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(boardId)).then(function(extBoard) {
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
            if (complexityEl) complexityEl.value = "";
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
                deps.apiFetch("/api/kanban/boards"),
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
            if (complexityEl) complexityEl.value = "";
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
            if (complexityEl) complexityEl.value = "";
            if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove("opacity-50", "cursor-not-allowed"); }
        }

        function uploadExtTicketAttachments(source, extTicketId, files) {
            var promises = [];
            for (var i = 0; i < files.length; i++) {
                var fd = new FormData();
                fd.append("file", files[i]);
                promises.push(deps.apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(extTicketId) + "/attach", {
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
            var complexity = complexityEl ? complexityEl.value : "";
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
                    deps.apiFetch("/api/kanban/boards/" + encodeURIComponent(boardId) + "/whatsapp-snapshot-ticket", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            lane_id: parseInt(laneId, 10),
                            message_ids: sourceMsgIds,
                            title: title,
                            description: desc,
                            priority: priority || "medium",
                            complexity: complexity || undefined
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
                var cetPayload = { title: title, description: desc, lane_id: parseInt(laneId, 10), priority: priority || "medium", board_id: boardId };
                if (complexity) cetPayload.complexity = complexity;
                if (typeof deps.mergeSourceChatIntoPayload === "function") {
                    deps.mergeSourceChatIntoPayload(cetPayload);
                }
                deps.apiFetch("/api/kanban/tickets", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(cetPayload)
                }).then(function(r) {
                    if (!r || !r.success) { deps.showSnackbar("Failed to create ticket", "error"); return; }
                    deps.showSnackbar("Ticket created");
                    if (waPhone) {
                        var msgIdList = JSON.parse(waMsgIds);
                        deps.apiFetch("/api/kanban/whatsapp/messages/mark-snapshot-group", {
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
                            deps.apiFetch("/api/kanban/tickets/" + r.id + "/attach-whatsapp-media", {
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
                deps.apiFetch("/api/kanban/external-boards/" + extSource + "/" + encodeURIComponent(extBoard.id) + "/create-ticket", {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
                }).then(function(r) {
                    if (r.success && r.ticket) {
                        if (waPhone) {
                            var msgIdList = JSON.parse(waMsgIds);
                            deps.apiFetch("/api/kanban/whatsapp/messages/mark-snapshot-group", {
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
            openCreateExternalTicketModal: openCreateExternalTicketModal,
            cetSwitchTab: cetSwitchTab,
            closeCreateExtTicketModal: closeCreateExtTicketModal,
            submitCreateExtTicket: submitCreateExtTicket,
            uploadExtTicketAttachments: uploadExtTicketAttachments,
            handleCetFileSelect: handleCetFileSelect,
        };
    }

    window.KanbanTicketUi = { create: createTicketUi };
    window.KanbanTicketModalSections = { create: createTicketModalSections };
    window.KanbanTicketActions = { create: createTicketActions };
    window.KanbanExternalTicketModal = { create: createExternalTicketModal };
})();
