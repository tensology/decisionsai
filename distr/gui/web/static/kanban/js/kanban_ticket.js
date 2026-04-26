(function() {
    "use strict";

    function createTicketUi(deps) {
        function buildSourceBadge(source) {
            return "";
        }

        function buildExternalLink(ticketUrl, source) {
            if (!ticketUrl) return "";
            var sourceLabel = source || "browser";
            return '<a href="' + deps.esc(ticketUrl) + '" target="_blank" class="kb-card-action-btn text-white transition-colors" data-tooltip="Open in ' + deps.esc(sourceLabel) + '" aria-label="Open in ' + deps.esc(sourceLabel) + '" onclick="event.stopPropagation()">' +
                '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>';
        }

        function actionButtonHtml(config) {
            var disabled = !!config.disabled;
            var stateClass = disabled ? "text-gray-700 cursor-not-allowed" : "text-white";
            var tooltip = config.tooltip || "";
            var titleAttr = config.nativeTooltip ? ' title="' + tooltip + '"' : "";
            return '<span class="kb-card-action-tip" data-tooltip="' + tooltip + '"' + titleAttr + ">" +
                '<button class="' + config.keyClass + " kb-card-action-btn " + stateClass + ' transition-colors" aria-label="' + tooltip + '"' + (disabled ? " disabled" : "") + ">" +
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
                    keyClass: "kb-act-cli",
                    tooltip: opts.hasProject
                        ? "Push to CLI"
                        : "link ticket/board to project.",
                    disabled: !opts.hasProject,
                    nativeTooltip: true,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
                }),
            ];
            leftActions.push(actionButtonHtml({
                keyClass: "kb-act-project",
                tooltip: opts.hasProject
                    ? "Send to Project (.tickets)"
                    : "link ticket/board to project.",
                disabled: !opts.hasProject,
                nativeTooltip: true,
                iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
            }));
            if (opts.canTransfer) {
                leftActions.push(actionButtonHtml({
                    keyClass: "kb-act-transfer",
                    tooltip: "Copy ticket into a local board",
                    disabled: false,
                    iconSvg: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 16H3v-5"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>',
                }));
            }
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
            var extLinkHtml = opts.isLocal ? "" : buildExternalLink(opts.ticketUrl, opts.source);
            var actionRowHtml = buildActionRow({
                hasProject: opts.hasProject,
                canTransfer: opts.canTransfer,
                canDelete: opts.canDelete,
            });
            return '<div class="flex items-start justify-between gap-2">' +
                '<span class="text-[14px] font-medium text-white leading-snug flex-1">' + deps.esc(opts.title) + "</span>" +
                '<div class="flex items-center gap-1.5 flex-shrink-0">' + sourceBadge + '<span class="' + opts.priorityClass + ' text-[10px] px-1.5 py-0.5 rounded text-white font-medium">' + deps.esc(opts.priority) + "</span>" + extLinkHtml + "</div>" +
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
            document.getElementById("kb-modal-ticket-title").value = ticket.title || "";
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
            if (rawDesc && (rawDesc.includes("<") || (source === "jira" && rawDesc.length > 0))) {
                descArea.value = "";
                descArea.readOnly = true;
                descArea.classList.add("bg-[#152054]/50", "cursor-not-allowed");
                descArea.style.display = "none";
                var existingRich = descArea.parentElement.querySelector(".kb-ext-rich-desc");
                if (existingRich) existingRich.remove();
                var richDiv = document.createElement("div");
                richDiv.className = "kb-ext-rich-desc text-sm text-gray-300 bg-[#152054]/50 rounded p-3 border border-white/10 max-h-64 overflow-y-auto";
                richDiv.innerHTML = rawDesc;
                richDiv.querySelectorAll("img").forEach(function(img) {
                    img.style.maxWidth = "100%";
                    img.style.borderRadius = "4px";
                    img.loading = "lazy";
                });
                richDiv.querySelectorAll("a").forEach(function(a) {
                    a.target = "_blank";
                    a.rel = "noopener noreferrer";
                });
                descArea.parentElement.insertBefore(richDiv, descArea.nextSibling);
            } else {
                var prevRich = descArea.parentElement.querySelector(".kb-ext-rich-desc");
                if (prevRich) prevRich.remove();
                descArea.value = deps.stripHtml(rawDesc);
                descArea.readOnly = true;
                descArea.classList.add("bg-[#152054]/50", "cursor-not-allowed");
                descArea.style.display = "";
            }

            document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
                btn.classList.add("opacity-50", "cursor-not-allowed");
                btn.disabled = true;
            });
            deps.setPriorityButtons(ticket.priority || "medium");
            deps.renderModalLinks([]);
            renderExternalMedia(ticket.media || [], source);
            renderExternalTodos(ticket.todos || []);

            var metaContainer = document.getElementById("kb-modal-external-meta");
            if (metaContainer) {
                var metaHtml = "";
                if (ticket.url) {
                    metaHtml += '<div class="flex items-center gap-2"><span class="text-xs text-gray-500">Source:</span><a href="' + deps.esc(ticket.url) + '" target="_blank" class="text-xs text-[#f97316] hover:underline">Open in ' + deps.esc(source.charAt(0).toUpperCase() + source.slice(1)) + "</a></div>";
                }
                if (ticket.members && ticket.members.length) {
                    metaHtml += '<div class="text-xs text-gray-400">Members: ' + ticket.members.map(deps.esc).join(", ") + "</div>";
                }
                if (ticket.labels && ticket.labels.length) {
                    metaHtml += '<div class="flex flex-wrap gap-1">' + ticket.labels.map(function(lb) {
                        return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + deps.esc(lb) + "</span>";
                    }).join("") + "</div>";
                }
                if (ticket.time_estimate || ticket.time_spent) {
                    metaHtml += '<div class="text-xs text-gray-400">';
                    if (ticket.time_estimate) metaHtml += "Estimate: " + deps.esc(ticket.time_estimate);
                    if (ticket.time_spent) metaHtml += " | Spent: " + deps.esc(ticket.time_spent);
                    metaHtml += "</div>";
                }
                if (ticket.reporter) {
                    metaHtml += '<div class="text-xs text-gray-400">Reporter: ' + deps.esc(ticket.reporter) + "</div>";
                }
                metaContainer.innerHTML = metaHtml;
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
                var disabledTip = "link ticket/board to project.";
                projectActionBtn.disabled = !canPush;
                projectActionBtn.classList.toggle("opacity-40", !canPush);
                projectActionBtn.classList.toggle("cursor-not-allowed", !canPush);
                projectActionBtn.title = canPush ? "Send to Project (.tickets)" : disabledTip;
                projectActionBtn.setAttribute("aria-label", projectActionBtn.title);
                if (cliActionBtn) {
                    cliActionBtn.disabled = !canPush;
                    cliActionBtn.classList.toggle("opacity-40", !canPush);
                    cliActionBtn.classList.toggle("cursor-not-allowed", !canPush);
                    cliActionBtn.title = canPush ? "Push to CLI" : disabledTip;
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
            if (isLocal) {
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
            var canDelete = isLocal;
            var canTransfer = !isLocal;
            card.innerHTML = buildCardMarkup({
                esc: deps.esc,
                title: ticket.title || "",
                priority: pri,
                priorityClass: priClass,
                description: truncatedDesc,
                labelsHtml: labelsHtml,
                membersHtml: membersHtml,
                timeHtml: timeHtml,
                mediaHtml: mediaHtml,
                todoHtml: todoHtml,
                ticketUrl: ticket.url || "",
                source: currentBoard && currentBoard.source ? currentBoard.source : "database",
                isLocal: isLocal,
                hasProject: hasProject,
                canTransfer: canTransfer,
                canDelete: canDelete,
            });

            card.addEventListener("click", function(e) {
                if (e.target.closest(".kb-card-actions") || e.target.closest(".kb-act-transfer") || e.target.closest("a")) return;
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
                if (isLocal) deps.sendTicketToProjectById(ticket.id);
                else deps.copyAndPushExternalTicket(ticket, currentBoard.source, "project");
            });
            var transferBtn = card.querySelector(".kb-act-transfer");
            if (transferBtn && !transferBtn.disabled) transferBtn.addEventListener("click", function(e) {
                e.stopPropagation();
                deps.openCopyModal(ticket);
            });
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
                row.innerHTML = '<span class="text-gray-300 flex-1 truncate">📎 ' + deps.esc(f.filename) + '</span>' +
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
            if (currentBoard.source !== "database") {
                if (currentBoardData && currentBoardData.can_create_ticket === false) {
                    deps.showSnackbar("You do not have permission to create tickets on this board", "error");
                    return;
                }
                deps.openCreateExternalTicketModal();
                return;
            }
            if (!currentBoardData) return;
            var firstLane = (currentBoardData.lanes || [])[0];
            if (!firstLane) { deps.showSnackbar("Board has no lanes", "error"); return; }
            deps.apiFetch("/api/kanban/tickets", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ lane_id: firstLane.id, title: "New Ticket", priority: "medium" })
            }).then(function(data) {
                deps.selectBoard("database", currentBoard.id);
                setTimeout(function() { deps.openTicketModal(data.id); }, 300);
            }).catch(function(e) { deps.showSnackbar("Failed: " + e.message, "error"); });
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
            deps.apiFetch("/api/kanban/tickets/copy-external-to-board", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    board_id: boardId,
                    title: copyTicketData.title,
                    description: deps.stripHtml(copyTicketData.description),
                    priority: copyTicketData.priority || "medium",
                    time_estimate: copyTicketData.time_estimate || "",
                    time_spent: copyTicketData.time_spent || "",
                    external_source: copyTicketData.external_source,
                    external_id: copyTicketData.external_id,
                    external_url: copyTicketData.external_url,
                })
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
        function setCetLoadingOverlay(isLoading) {
            var overlay = document.getElementById("kb-cet-loading-overlay");
            if (!overlay) return;
            if (isLoading) overlay.classList.remove("hidden");
            else overlay.classList.add("hidden");
        }

        function composeWaTicket(messageIds, titleEl, descEl, statusEl) {
            titleEl = titleEl || document.getElementById("kb-cet-title");
            descEl = descEl || document.getElementById("kb-cet-desc");
            statusEl = statusEl || document.getElementById("kb-cet-distill-status");
            if (!messageIds || !messageIds.length) return;
            deps.apiFetch("/api/kanban/whatsapp/compose-ticket", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({message_ids: messageIds})
            }).then(function(r) {
                deps.setWaTicketComposeInFlight(false);
                setCetLoadingOverlay(false);
                titleEl.disabled = false;
                descEl.disabled = false;
                titleEl.placeholder = "Ticket title";
                descEl.placeholder = "Describe the ticket...";
                var submitBtn = document.getElementById("kb-cet-submit");
                submitBtn.disabled = false;
                submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
                if (r.title) titleEl.value = r.title;
                if (r.description) descEl.value = r.description;
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
                    statusEl.textContent = "✏️ Ticket composed from messages (AI unavailable — you can edit)";
                    statusEl.classList.remove("text-[#f97316]");
                    statusEl.classList.add("text-yellow-500");
                } else {
                    statusEl.textContent = "✏️ You can edit the title and description";
                    statusEl.classList.remove("text-[#f97316]");
                    statusEl.classList.add("text-green-400");
                }
            }).catch(function() {
                deps.setWaTicketComposeInFlight(false);
                setCetLoadingOverlay(false);
                titleEl.disabled = false;
                descEl.disabled = false;
                titleEl.placeholder = "Ticket title";
                descEl.placeholder = "Describe the ticket...";
                var submitBtn = document.getElementById("kb-cet-submit");
                submitBtn.disabled = false;
                submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
                statusEl.textContent = "Could not compose ticket — enter a title manually";
                statusEl.classList.remove("text-[#f97316]");
                statusEl.classList.add("text-red-400");
            });
        }

        function openTicketFromWhatsApp(phone, title, description, boardId, msgs) {
            if (deps.getWaTicketComposeInFlight()) return;
            deps.setWaTicketComposeInFlight(true);
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            if (!modal) { deps.setWaTicketComposeInFlight(false); deps.showSnackbar("Ticket form not found"); return; }
            document.getElementById("kb-cet-board-row").style.display = "";
            setCetLoadingOverlay(true);
            document.getElementById("kb-cet-title").value = "";
            document.getElementById("kb-cet-desc").value = "";
            document.getElementById("kb-cet-heading").textContent = "Create Ticket from WhatsApp";
            var titleEl = document.getElementById("kb-cet-title");
            var descEl = document.getElementById("kb-cet-desc");
            var submitBtn = document.getElementById("kb-cet-submit");
            titleEl.disabled = true;
            descEl.disabled = true;
            submitBtn.disabled = true;
            submitBtn.classList.add("opacity-50", "cursor-not-allowed");
            titleEl.placeholder = "AI is composing...";
            descEl.placeholder = "Analyzing messages and composing ticket description...";
            var statusEl = document.getElementById("kb-cet-distill-status");
            statusEl.textContent = "";
            statusEl.classList.remove("text-gray-500", "text-green-400", "text-yellow-500", "text-red-400", "text-[#f97316]");
            modal.dataset.whatsappPhone = phone;
            modal.dataset.whatsappMsgIds = JSON.stringify(msgs.map(function(m) { return m.id; }));
            modal.dataset.whatsappBoardId = boardId;
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
            modal.classList.remove("hidden");
            cetSwitchTab("details");
            var msgIds = msgs.map(function(m) { return m.id; });
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
                    if (source === "database") {
                        deps.apiFetch("/api/kanban/boards/" + bid).then(function(boardData) {
                            var laneInput = document.getElementById("kb-cet-lane");
                            var lanes = boardData.lanes || [];
                            var target = lanes.find(function(l) { return l.name === "Backlog"; }) || lanes[0];
                            if (target) laneInput.value = target.id;
                        });
                    } else {
                        deps.apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(bid)).then(function(extBoard) {
                            var laneInput = document.getElementById("kb-cet-lane");
                            var cols = extBoard.columns || extBoard.lists || [];
                            if (cols.length) laneInput.value = cols[0].name || cols[0].id || "";
                        }).catch(function() {});
                    }
                }
                var firstOpt = boardSelect.options[boardSelect.selectedIndex];
                if (!firstOpt && boardSelect.options.length > 0) {
                    boardSelect.selectedIndex = 0;
                    firstOpt = boardSelect.options[0];
                }
                if (firstOpt) {
                    var firstVal = firstOpt.value.split(":");
                    loadLanes(firstVal[0], parseInt(firstVal[1], 10));
                }
                boardSelect.addEventListener("change", function() {
                    var selVal = boardSelect.value.split(":");
                    loadLanes(selVal[0], parseInt(selVal[1], 10));
                });
                document.getElementById("kb-cet-files-list").innerHTML = "";
                document.getElementById("kb-cet-file-input").value = "";
            });
        }

        function openCreateExternalTicketModal() {
            var currentBoard = deps.getCurrentBoard();
            var currentBoardData = deps.getCurrentBoardData();
            if (!currentBoard || !currentBoard.source || currentBoard.source === "database") {
                deps.showSnackbar("Select a Trello or Jira board first", "error");
                return;
            }
            if (currentBoardData && currentBoardData.can_create_ticket === false) {
                deps.showSnackbar("You do not have permission to create tickets on this board", "error");
                return;
            }
            var modal = document.getElementById("kb-create-ext-ticket-modal");
            document.getElementById("kb-cet-title").value = "";
            document.getElementById("kb-cet-desc").value = "";
            document.getElementById("kb-cet-lane").innerHTML = '<option value="">Select a list/column...</option>';
            if (currentBoardData && currentBoardData.lanes) {
                currentBoardData.lanes.forEach(function(lane) {
                    var opt = document.createElement("option");
                    opt.value = lane.id;
                    opt.textContent = lane.name;
                    document.getElementById("kb-cet-lane").appendChild(opt);
                });
            }
            document.getElementById("kb-cet-priority").value = "medium";
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
            document.getElementById("kb-cet-heading").textContent = "Create " + (currentBoard.source === "trello" ? "Trello" : "Jira") + " Ticket";
            setCetLoadingOverlay(false);
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
            if (titleEl) { titleEl.disabled = false; titleEl.placeholder = "Ticket title"; }
            if (descEl) { descEl.disabled = false; descEl.placeholder = "Describe the ticket..."; }
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
                deps.apiFetch("/api/kanban/tickets", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ title: title, description: desc, lane_id: parseInt(laneId, 10), priority: priority || "medium", board_id: boardId })
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
                        mediaEls.forEach(function(el) {
                            var msgId = parseInt(el.value, 10);
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
