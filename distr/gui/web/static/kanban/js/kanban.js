(function() {
    "use strict";

    var currentBoard = null;       // { id, source, ... }
    var currentBoardData = null;   // full board data with lanes/tickets
    var currentBoardHasProject = false; // whether current board has a linked project
    var dbBoards = [];
    var editingBoardId = null;     // null = create, number = edit
    var modalTicketId = null;
    /** Loaded ticket's source_chat_id (null = unset); used to avoid overwriting on save. */
    var modalTicketSourceChatId = null;
    var modalTicketReadOnlyDetails = false;
    var kanbanTicketModalDetailsHeight = 0;
    var sendWorkflowContext = null;
    var copyModalState = null;     // { mode: 'single'|'lane', ... } for copy modal
    var ctxMenuBoardId = null;     // board id for context menu
    var waChats = [];
    var waSelectedJid = null;
    var waSelectedChatType = "private";
    var waActiveThread = null;     // { sender, name, chat_type, target_jid }
    var waCtxMenuData = null;      // { jid, phone, name }
    var waMsgCtxData = null;       // { message_id (db id) }
    var waThreadMessages = [];
    var waSelectionMode = false;
    var waSelectedMessageIds = {};
    var waConnected = false;
    var waWS = null;
    var waWSReconnectTimer = null;
    var waWsAuthBundle = null;
    var waWsAuthAppUserId = "local-ui";
    var waVoiceRecorder = null;
    var waVoiceStream = null;
    var waVoiceChunks = [];
    var waVoiceRecording = false;
    var waVoiceStartedAtMs = 0;
    var waVoiceTimerInterval = null;
    var waThreadRefreshTimer = null;
    var waPendingAttachment = null; // { name, mime_type, data_b64, kind }
    var waGroupNames = {};          // { "<group-jid>": "Group Name" }
    var waChatNames = {};           // { "<jid-or-phone>": "Display Name" }
    var waDraftByPhone = {};        // { "<phone>": { text, source, ... } }
    var activeSourceTab = "local";
    var kbBoardWS = null;
    var kbBoardWSReconnectTimer = null;
    var kbBoardRefreshTimer = null;
    var kbBoardViewMode = "list";
    var waTicketComposeInFlight = false;
    var waSidebarChatListMode = false;
    var waSelectedChatPhones = {};

    // ── Helpers ──

    function kbDocumentsWorkspaceOpen() {
        return !!(window.KanbanDocuments && typeof window.KanbanDocuments.isExpanded === "function" && window.KanbanDocuments.isExpanded());
    }

    function kbRevealBoardView() {
        if (kbDocumentsWorkspaceOpen()) return;
        var boardView = document.getElementById("kb-board-view");
        if (boardView) boardView.classList.remove("hidden");
    }

    var commonUtils = window.KanbanCommonUtils;
    var waHelpers = window.KanbanWhatsAppHelpers;
    var esc = commonUtils.esc;
    var waMsgHasLinkedTicket = waHelpers.waMsgHasLinkedTicket;
    var waIsMessageSelectable = waHelpers.waIsMessageSelectable;
    try {
        var bootParams = new URLSearchParams(window.location.search);
        var bootView = (bootParams.get("view") || "").toLowerCase();
        if (bootView === "kanban" || bootView === "list") {
            kbBoardViewMode = bootView;
            localStorage.setItem("kb_board_view_mode", kbBoardViewMode);
        } else {
            var savedBoardView = localStorage.getItem("kb_board_view_mode");
            if (savedBoardView === "kanban" || savedBoardView === "list") {
                kbBoardViewMode = savedBoardView;
            }
        }
    } catch (e) {
        kbBoardViewMode = "list";
    }
    function updateWaThreadSelectToggleUi() {
        var toggleBtn = document.getElementById("kb-wa-thread-select-toggle");
        if (!toggleBtn) return;
        if (waSelectionMode) {
            var selectedCount = Object.keys(waSelectedMessageIds).length;
            toggleBtn.classList.add("bg-[#25D366]/20", "border-[#25D366]/50", "text-[#25D366]");
            toggleBtn.classList.remove("border-white/25", "text-gray-200");
            toggleBtn.textContent = "Select Messages (" + selectedCount + ")";
        } else {
            toggleBtn.classList.remove("bg-[#25D366]/20", "border-[#25D366]/50", "text-[#25D366]");
            toggleBtn.classList.add("border-white/25", "text-gray-200");
            toggleBtn.textContent = "Select Messages";
        }
    }
    function collectWaSnapshotMessages(allMessages) {
        var unticketed = allMessages.filter(waIsMessageSelectable);
        if (!waSelectionMode) return unticketed;
        return unticketed.filter(function(msg) { return !!waSelectedMessageIds[String(msg.id)]; });
    }
    function setWaSelectionMode(enabled) {
        waSelectionMode = !!enabled;
        waSelectedMessageIds = {};
        if (waSelectionMode) {
            waThreadMessages.forEach(function(msg) {
                if (waIsMessageSelectable(msg)) {
                    waSelectedMessageIds[String(msg.id)] = true;
                }
            });
        }
        updateWaThreadSelectToggleUi();
        if (waSelectedJid) {
            refreshWaThreadSelectionUI();
        }
    }

    /** Toggle checkboxes/select UI in the thread without re-fetching or rebuilding DOM. */
    function refreshWaThreadSelectionUI() {
        var msgList = document.getElementById("kb-wa-thread-messages");
        if (!msgList) return;
        if (waSelectionMode) {
            // Inject checkboxes into message rows that don't have them yet
            msgList.querySelectorAll("[data-wa-msg-id]").forEach(function(row) {
                if (row.querySelector(".kb-wa-msg-select")) return; // already has checkbox
                var msgId = row.getAttribute("data-wa-msg-id");
                var msg = waThreadMessages.find(function(m) { return String(m.id) === String(msgId); });
                if (!msg) return;
                var label = document.createElement("label");
                label.className = "pt-1 cursor-pointer";
                label.title = "Select message";
                var cb = document.createElement("input");
                cb.type = "checkbox";
                cb.className = "accent-[#25D366] w-4 h-4 kb-wa-msg-select";
                cb.setAttribute("data-wa-msg-id", msgId);
                if (waIsMessageSelectable(msg)) {
                    if (waSelectedMessageIds[String(msgId)]) cb.checked = true;
                    label.appendChild(cb);
                } else {
                    var spacer = document.createElement("span");
                    spacer.className = "pt-1 w-4 h-4 inline-block";
                    row.insertBefore(spacer, row.firstChild);
                    return;
                }
                row.insertBefore(label, row.firstChild);
                cb.addEventListener("change", function() {
                    var mid = String(cb.dataset.waMsgId || "");
                    if (!mid) return;
                    if (cb.checked) waSelectedMessageIds[mid] = true;
                    else delete waSelectedMessageIds[mid];
                    updateWaThreadSelectToggleUi();
                });
            });
        } else {
            // Remove all checkboxes and spacers
            msgList.querySelectorAll(".kb-wa-msg-select").forEach(function(el) {
                var label = el.closest("label");
                if (label) label.remove();
                else el.remove();
            });
            msgList.querySelectorAll("[data-wa-msg-id] > span.pt-1.w-4").forEach(function(el) { el.remove(); });
        }
        // Update the create-tickets button text
        var createBtn = document.getElementById("kb-wa-thread-create-tickets");
        if (createBtn) {
            createBtn.textContent = waSelectionMode ? "Snapshot Selected Messages" : "Create Ticket from Messages";
        }
    }
    function isMessagesPanelVisible() {
        var panel = document.getElementById("kb-panel-messages");
        return !!panel && !panel.classList.contains("hidden");
    }
    function updateWaSidebarFooterUi() {
        var foot = document.getElementById("kb-wa-sidebar-footer");
        if (foot) {
            if (!waChats.length) {
                foot.classList.add("hidden");
                return;
            }
            foot.classList.remove("hidden");
        }
        var def = document.getElementById("kb-wa-sidebar-footer-default");
        var sel = document.getElementById("kb-wa-sidebar-footer-select");
        var countEl = document.getElementById("kb-wa-sidebar-select-count");
        if (!def || !sel) return;
        def.classList.toggle("hidden", !!waSidebarChatListMode);
        sel.classList.toggle("hidden", !waSidebarChatListMode);
        if (waSidebarChatListMode) {
            var n = Object.keys(waSelectedChatPhones).filter(function(k) { return waSelectedChatPhones[k]; }).length;
            if (countEl) countEl.textContent = n + " selected";
        }
    }
    function setWaSidebarChatListMode(enabled) {
        waSidebarChatListMode = !!enabled;
        waSelectedChatPhones = {};
        if (waSidebarChatListMode) {
            var searchEl = document.getElementById("kb-wa-search");
            if (searchEl && (searchEl.value || "").trim()) searchEl.value = "";
        }
        renderWhatsAppChatList();
        updateWaSidebarFooterUi();
        if (waSidebarChatListMode) {
            showSnackbar("Selection mode: tick chats in the list, then use Export or Delete below.", "info");
        }
    }
    function toggleWaSidebarChatSelection(phone) {
        if (!phone) return;
        if (waSelectedChatPhones[phone]) delete waSelectedChatPhones[phone];
        else waSelectedChatPhones[phone] = true;
        renderWhatsAppChatList();
        updateWaSidebarFooterUi();
    }
    var waDownloadJsonFile = waHelpers.waDownloadJsonFile;
    function waExportMessagesForPhones(phones, label) {
        apiFetch("/api/tickets/whatsapp/messages?limit=100000").then(function(data) {
            var all = data.messages || [];
            var msgs = phones && phones.length ? all.filter(function(m) {
                var jp = String(m.jid_phone || "");
                return phones.indexOf(jp) !== -1;
            }) : all;
            waDownloadJsonFile("whatsapp-messages-" + label + "-" + Date.now() + ".json", {
                exported_at: new Date().toISOString(),
                filter_contacts: phones && phones.length ? phones : null,
                total: msgs.length,
                messages: msgs
            });
            showSnackbar("Exported " + msgs.length + " message" + (msgs.length === 1 ? "" : "s"));
        }).catch(function(err) {
            showSnackbar("Export failed: " + err.message, "error");
        });
    }
    function waDeleteChatsByPhones(phones, onDone) {
        phones = phones.filter(Boolean);
        if (!phones.length) {
            if (onDone) onDone();
            return;
        }
        var i = 0;
        function next() {
            if (i >= phones.length) {
                if (onDone) onDone();
                return;
            }
            var p = phones[i++];
            apiFetch("/api/tickets/whatsapp/chat/" + encodeURIComponent(p), { method: "DELETE" }).then(function() { next(); }).catch(function() { next(); });
        }
        next();
    }
    function setWaThreadControlsEnabled(enabled) {
        var inputEl = document.getElementById("kb-wa-thread-input");
        var sendBtn = document.getElementById("kb-wa-thread-send");
        var voiceBtn = document.getElementById("kb-wa-thread-voice");
        var attachBtn = document.getElementById("kb-wa-thread-attach");
        var deleteBtn = document.getElementById("kb-wa-thread-delete");
        var selectBtn = document.getElementById("kb-wa-thread-select-toggle");
        var snapshotBtn = document.getElementById("kb-wa-thread-create-tickets");
        if (inputEl) inputEl.disabled = !enabled;
        if (sendBtn) sendBtn.disabled = !enabled;
        if (voiceBtn) voiceBtn.disabled = !enabled;
        if (attachBtn) attachBtn.disabled = !enabled;
        if (deleteBtn) deleteBtn.disabled = !enabled;
        if (selectBtn) selectBtn.disabled = !enabled;
        if (snapshotBtn) snapshotBtn.disabled = !enabled;
    }
    function scheduleWaThreadRefresh(delayMs) {
        if (waThreadRefreshTimer) clearTimeout(waThreadRefreshTimer);
        waThreadRefreshTimer = setTimeout(function() {
            waThreadRefreshTimer = null;
            if (!waSelectedJid) return;
            refreshWaThreadIfOpen();
        }, delayMs || 450);
    }
    function appendOptimisticSentTextMessage(textValue) {
        var msgList = document.getElementById("kb-wa-thread-messages");
        var countEl = document.getElementById("kb-wa-thread-count");
        if (!msgList || !textValue) return;
        var text = String(textValue || "").trim();
        if (!text) return;
        var now = new Date();
        var timeStr = now.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
        var tempId = "optimistic_" + Date.now();
        var row = document.createElement("div");
        row.className = "flex justify-end items-start gap-2";
        row.setAttribute("data-wa-msg-id", tempId);
        row.innerHTML =
            '<div class="wa-msg-bubble bg-[#005c4b] px-3 py-2 opacity-95" style="max-width:75%;border-radius:8px;">'
            + '<div class="wa-msg-text text-sm text-white whitespace-pre-wrap">' + esc(text) + '</div>'
            + '<div class="flex items-center justify-end gap-1 mt-0.5">'
            + '<span class="text-[10px] text-gray-500">' + timeStr + '</span>'
            + '<span class="text-[10px] text-blue-300">…</span>'
            + '</div></div>';
        msgList.appendChild(row);
        msgList.scrollTop = msgList.scrollHeight;
        if (countEl && /messages$/.test(countEl.textContent || "")) {
            var current = parseInt((countEl.textContent || "0").split(" ")[0], 10);
            if (!isNaN(current)) countEl.textContent = (current + 1) + " messages";
        }
    }
    function appendOptimisticSentMediaMessage(attachmentName, attachmentKind, captionText) {
        var msgList = document.getElementById("kb-wa-thread-messages");
        if (!msgList) return;
        var now = new Date();
        var timeStr = now.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
        var isImage = attachmentKind === "image";
        var label = isImage ? "image" : "file";
        var captionHtml = captionText ? ('<div class="text-sm text-white whitespace-pre-wrap mt-1">' + esc(captionText) + '</div>') : "";
        var previewHtml = "";
        if (isImage && waPendingAttachment && waPendingAttachment.data_b64) {
            previewHtml = '<div class="mb-1 rounded overflow-hidden"><img src="data:' + esc(waPendingAttachment.mime_type || 'image/jpeg') + ';base64,' + waPendingAttachment.data_b64 + '" class="max-w-full max-h-[200px] rounded object-cover opacity-90" /></div>';
        }
        var row = document.createElement("div");
        row.className = "flex justify-end items-start gap-2";
        row.innerHTML =
            '<div class="wa-msg-bubble bg-[#005c4b] px-3 py-2 opacity-95" style="max-width:75%;border-radius:8px;">'
            + (previewHtml || '<div class="text-sm text-white">📎 [' + esc(label) + '] ' + esc(attachmentName || "attachment") + '</div>')
            + captionHtml
            + '<div class="flex items-center justify-end gap-1 mt-0.5">'
            + '<span class="text-[10px] text-gray-500">' + timeStr + '</span>'
            + '<span class="text-[10px] text-blue-300">…</span>'
            + '</div></div>';
        msgList.appendChild(row);
        msgList.scrollTop = msgList.scrollHeight;
    }
    function clearWaPendingAttachment() {
        waPendingAttachment = null;
        var fileInput = document.getElementById("kb-wa-thread-file");
        if (fileInput) fileInput.value = "";
    }
    function syncAndRefreshThreadAfterSend() {
        apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).finally(function() {
            loadWhatsAppChats(true);
            scheduleWaThreadRefresh(250);
        });
    }
    /** When true, show only the centered empty block; when false, show header / thread / composer. */
    function waShowThreadGlobalEmpty(show) {
        var g = document.getElementById("kb-wa-thread-global-empty");
        var n = document.getElementById("kb-wa-thread-normal");
        if (g) g.classList.toggle("hidden", !show);
        if (n) n.classList.toggle("hidden", !!show);
    }
    function showWhatsAppNoMessagesState() {
        if (!isMessagesPanelVisible()) return;
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        var msgView = document.getElementById("kb-wa-thread-view");
        boardView.classList.add("hidden");
        emptyView.classList.add("hidden");
        msgView.classList.remove("hidden");
        waShowThreadGlobalEmpty(true);
        msgView.dataset.waSender = "";
        msgView.dataset.waChatType = "private";
        msgView.dataset.waTargetJid = "";
        updateWaSidebarFooterUi();
    }
    var dedupeThreadMessages = waHelpers.dedupeThreadMessages;

    var stripHtml = commonUtils.stripHtml;

    function publishWaSubscriptions() {
        if (!waWS || waWS.readyState !== WebSocket.OPEN) return;
        fetchWaWsAuthBundle().then(function(bundle) {
            var subTokens = ((bundle && bundle.subscription_tokens) || []).map(function(x) { return x.token; }).filter(Boolean);
            try {
                waWS.send(JSON.stringify({ type: "subscribe", subscribe_tokens: subTokens }));
            } catch (err) {}
        }).catch(function() {});
    }

    function collectWaPhonesForSubscription() {
        var phones = [];
        waChats.forEach(function(c) {
            if (c && c.sender) phones.push(String(c.sender));
        });
        if (waSelectedJid) phones.push(String(waSelectedJid));
        return Array.from(new Set(phones.filter(Boolean)));
    }

    function fetchWaWsAuthBundle() {
        var phones = collectWaPhonesForSubscription();
        return apiFetch("/api/tickets/whatsapp/ws-auth", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                app_user_id: waWsAuthAppUserId,
                subscribe_phones: phones
            })
        }).then(function(resp) {
            if (!resp || !resp.success || !resp.ws_token) {
                throw new Error((resp && resp.error) || "Failed to fetch websocket auth");
            }
            waWsAuthBundle = resp;
            return resp;
        });
    }

    var truncate = commonUtils.truncate;

    var apiFetch = window.DecisionsAPI.fetch;

    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "kb-snackbar" }); }
    var modalHelpers = window.KanbanModalHelpers;

    function isAnyKanbanModalOpen(exceptId) {
        return modalHelpers.isAnyModalOpen(exceptId);
    }

    function hideAllKanbanModals(exceptId) {
        modalHelpers.hideAllModals(exceptId);
    }

    function hideKanbanFloatingUi() {
        var ids = [
            "kb-board-ctx-menu",
            "kb-ext-board-ctx-menu",
            "kb-wa-ctx-menu",
            "kb-wa-msg-ctx-menu",
            "kb-wa-sync-ctx-menu",
            "kb-wa-msg-actions-menu",
            "kb-wa-media-lightbox",
            "kb-wa-media-lightbox-inner",
        ];
        ids.forEach(function(id) {
            var el = document.getElementById(id);
            if (!el) return;
            if (id === "kb-wa-media-lightbox-inner") el.innerHTML = "";
            else {
                el.classList.add("hidden");
                el.classList.remove("flex");
            }
        });
        try { if (currentBoard && boardSettings) boardSettings.hideBoardContextMenu(); } catch (e) {}
        try { if (waManagement) waManagement.hideWaChatContextMenu(); } catch (e) {}
        try { if (waManagement) waManagement.hideWaMsgContextMenuLocal(); } catch (e) {}
        try { if (waManagement) waManagement.hideWaSyncContextMenu(); } catch (e) {}
        try { if (waRuntime && typeof waRuntime.hideWaMsgActionsMenu === "function") waRuntime.hideWaMsgActionsMenu(); } catch (e) {}
    }

    function resetMessagesSurfaceForBoardMode() {
        hideKanbanFloatingUi();
        clearWaPendingAttachment();
        if (waVoiceRecorder && waVoiceRecording) {
            try { waVoiceRecorder.stop(); } catch (e) {}
        }
        try { if (waRuntime) waRuntime.resetWhatsAppVoiceRecordingUi(); } catch (e) {}
        waSelectionMode = false;
        waSelectedMessageIds = {};
        waSidebarChatListMode = false;
        waSelectedChatPhones = {};
        waPendingAttachment = null;
        updateWaThreadSelectToggleUi();
        updateWaSidebarFooterUi();
        var msgView = document.getElementById("kb-wa-thread-view");
        if (msgView) msgView.classList.add("hidden");
        waShowThreadGlobalEmpty(false);
        var loading = document.getElementById("kb-loading");
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        if (loading) loading.classList.add("hidden");
        if (currentBoard) {
            if (boardView) boardView.classList.remove("hidden");
            if (emptyView) emptyView.classList.add("hidden");
        } else {
            if (boardView) boardView.classList.add("hidden");
            if (emptyView) emptyView.classList.remove("hidden");
        }
    }

    function resetBoardSurfaceForMessagesMode() {
        hideKanbanFloatingUi();
        hideAllKanbanModals();
        var loading = document.getElementById("kb-loading");
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        if (loading) loading.classList.add("hidden");
        if (boardView) boardView.classList.add("hidden");
        if (emptyView) emptyView.classList.add("hidden");
    }

    function showKanbanConfirm(opts) {
        if (window.DecisionsAPI && typeof window.DecisionsAPI.confirm === "function") {
            window.DecisionsAPI.confirm(opts);
            return;
        }
        modalHelpers.showConfirm(opts);
    }
    function hideKanbanConfirm() {
        var shared = document.getElementById("decisions-confirm-modal");
        if (shared) {
            shared.remove();
            return;
        }
        modalHelpers.hideConfirm();
    }

    function isKeyboardEditingTarget(target) {
        if (!target) return false;
        var tag = (target.tagName || "").toLowerCase();
        return !!(target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select");
    }

    function findLocalBoardById(boardId) {
        return (dbBoards || []).find(function(board) { return String(board.id) === String(boardId); }) || null;
    }

    function clearSelectedBoardSurface() {
        currentBoard = null;
        currentBoardData = null;
        var boardView = document.getElementById("kb-board-view");
        var loading = document.getElementById("kb-loading");
        var empty = document.getElementById("kb-empty");
        if (boardView) boardView.classList.add("hidden");
        if (loading) loading.classList.add("hidden");
        if (empty) empty.classList.remove("hidden");
    }

    function deleteLocalBoardById(boardId) {
        return apiFetch("/api/tickets/boards/" + boardId, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            if (currentBoard && String(currentBoard.id) === String(boardId)) {
                clearSelectedBoardSurface();
            }
            return loadBoards(true);
        }).catch(function(e) {
            showSnackbar("Delete failed: " + e.message, "error");
        });
    }

    function confirmDeleteLocalBoardById(boardId) {
        if (!boardId) return;
        var board = findLocalBoardById(boardId);
        var name = (board && board.name) || (currentBoardData && String(currentBoard.id) === String(boardId) ? currentBoardData.name : "") || "this board";
        showKanbanConfirm({
            title: "Delete board",
            message: 'Delete board "' + name + '" and all its tickets? This cannot be undone.',
            confirmLabel: "Delete",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                deleteLocalBoardById(boardId);
            },
        });
    }

    function confirmDeleteCurrentLocalBoard() {
        if (!currentBoard || currentBoard.source !== "database" || !currentBoard.id) return;
        confirmDeleteLocalBoardById(currentBoard.id);
    }

    function shouldOpenSelectedBoardDeleteConfirm(e) {
        if (!e || e.key !== "Delete") return false;
        if (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return false;
        if (isKeyboardEditingTarget(e.target)) return false;
        if (document.getElementById("decisions-confirm-modal")) return false;
        if (isAnyKanbanModalOpen()) return false;
        if (isMessagesPanelVisible()) return false;
        var boardView = document.getElementById("kb-board-view");
        if (!boardView || boardView.classList.contains("hidden")) return false;
        return !!(currentBoard && currentBoard.source === "database" && currentBoard.id);
    }

    /** External ticket modal reuses the same DOM as local tickets — clear DB ticket id so actions hit the Jira path. */
    function prepareExternalTicketModal() {
        modalTicketId = null;
        modalTicketSourceChatId = null;
        modalTicketReadOnlyDetails = true;
    }

    var ticketUi = window.KanbanTicketUi.create({
        esc: esc,
        stripHtml: stripHtml,
        truncate: truncate,
        setPriorityButtons: setPriorityButtons,
        setTicketComplexity: setTicketComplexity,
        renderModalLinks: renderModalLinks,
        renderModalFiles: renderModalFiles,
        renderModalTodos: renderModalTodos,
        switchTicketTab: switchTicketTab,
        showSnackbar: showSnackbar,
        prepareExternalTicketModal: prepareExternalTicketModal,
        setExternalModalTicketId: function(id) {
            modalTicketId = id || null;
            modalTicketReadOnlyDetails = true;
        },
        openTicketModal: openTicketModal,
        copyAndPushExternalTicket: copyAndPushExternalTicket,
        openSendWorkflowModal: openSendWorkflowModal,
        openCopyModal: openCopyModal,
        apiFetch: apiFetch,
        sendTicketToProjectById: sendTicketToProjectById,
        sendTicketToAgentById: sendTicketToAgentById,
        pushTicketToCli: pushTicketToCli,
        reloadCurrentDatabaseBoard: reloadCurrentDatabaseBoard,
        showKanbanConfirm: showKanbanConfirm,
        hideKanbanConfirm: hideKanbanConfirm,
        startTicketDiscussion: startTicketDiscussion,
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        showRunPopover: showRunPopover,
    });
    var ticketModalSections = window.KanbanTicketModalSections.create({
        esc: esc,
        apiFetch: apiFetch,
        showSnackbar: showSnackbar,
        getModalTicketId: function() { return modalTicketId; },
        ensureModalTicketId: function() {
            return modalTicketId ? Promise.resolve(modalTicketId) : persistExternalTicketLocalCopy();
        },
        renderModalAuditEntries: renderModalAuditEntries,
        loadModalAuditReport: loadModalAuditReport,
    });
    var boardSettings = window.KanbanBoardSettings.create({
        apiFetch: apiFetch,
        showSnackbar: showSnackbar,
        getEditingBoardId: function() { return editingBoardId; },
        setEditingBoardId: function(v) { editingBoardId = v; },
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        clearCurrentBoard: function() { currentBoard = null; currentBoardData = null; },
        selectBoard: selectBoard,
        loadBoards: loadBoards,
        loadBoardWaLinks: loadBoardWaLinks,
        saveSelectedBoardWaLink: function(boardId) { return waManagement.saveSelectedWaLinkForBoard(boardId); },
        populateSelect: populateSelect,
        getAgentProviders: function() { return _agentProviders; },
        setAgentProviders: function(providers) { _agentProviders = providers || []; },
        getDbBoards: function() { return dbBoards; },
        getCtxMenuBoardId: function() { return ctxMenuBoardId; },
        setCtxMenuBoardId: function(v) { ctxMenuBoardId = v; },
        showKanbanConfirm: showKanbanConfirm,
        hideKanbanConfirm: hideKanbanConfirm,
        closeWhatsAppThread: closeWhatsAppThread,
        resetMessagesSurfaceForBoardMode: resetMessagesSurfaceForBoardMode,
        resetBoardSurfaceForMessagesMode: resetBoardSurfaceForMessagesMode,
        switchSourceTab: switchSourceTab,
        getExternalBoards: getExternalBoards,
        renderExternalBoards: renderExternalBoards,
        resetExternalCache: function() { _externalCache = null; _externalCacheTime = 0; },
        addTicket: addTicket,
        setBoardViewMode: setBoardViewMode,
        onEnterMessagesTab: function() {
            if (waChats.length) {
                var hasSelected = waSelectedJid && waChats.some(function(chat) { return chat.sender === waSelectedJid; });
                if (!hasSelected) {
                    waSelectedJid = waChats[0].sender;
                    waSelectedChatType = waChats[0].chat_type || "private";
                }
                var selectedChat = waChats.find(function(chat) { return chat.sender === waSelectedJid; }) || waChats[0];
                renderWhatsAppChatList();
                if (selectedChat) showWhatsAppThread(selectedChat.sender, selectedChat.name || selectedChat.sender);
            } else {
                showWhatsAppNoMessagesState();
            }
            loadWhatsAppChats(true);
        },
        handleEditBoardClick: function() {
            if (currentBoard && currentBoard.source === "database" && currentBoard.id) {
                boardSettings.openBoardModal(currentBoard.id);
            } else if (currentBoard && (currentBoard.source === "trello" || currentBoard.source === "jira")) {
                boardSettings.openExternalBoardConfigModal(currentBoard.source, currentBoard.id);
            } else if (currentBoardData && currentBoardData.id) {
                boardSettings.openBoardModal(currentBoardData.id);
            }
        },
    });
    var waManagement = window.KanbanWhatsAppManagement.create({
        apiFetch: apiFetch,
        esc: esc,
        showSnackbar: showSnackbar,
        showKanbanConfirm: showKanbanConfirm,
        hideKanbanConfirm: hideKanbanConfirm,
        getWaCtxMenuData: function() { return waCtxMenuData; },
        setWaCtxMenuData: function(data) { waCtxMenuData = data; },
        getWaMsgCtxData: function() { return waMsgCtxData; },
        getWaSelectionMode: function() { return waSelectionMode; },
        getWaSelectedJid: function() { return waSelectedJid; },
        getEditingBoardId: function() { return editingBoardId; },
        getWaState: function() {
            return {
                waSelectedJid: waSelectedJid,
                waSelectedChatType: waSelectedChatType,
                waThreadMessages: waThreadMessages,
                waSelectedMessageIds: waSelectedMessageIds,
                waSelectionMode: waSelectionMode,
            };
        },
        setWaState: function(s) {
            if (!s) return;
            waSelectedJid = s.waSelectedJid;
            waSelectedChatType = s.waSelectedChatType;
            waThreadMessages = s.waThreadMessages;
            waSelectedMessageIds = s.waSelectedMessageIds;
            waSelectionMode = s.waSelectionMode;
        },
        updateWaThreadSelectToggleUi: updateWaThreadSelectToggleUi,
        renderWhatsAppChatList: renderWhatsAppChatList,
        showWhatsAppNoMessagesState: showWhatsAppNoMessagesState,
        loadWhatsAppChats: loadWhatsAppChats,
        showWhatsAppThread: showWhatsAppThread,
        openWaLinkModal: openWaLinkModal,
        collectWaSnapshotMessages: collectWaSnapshotMessages,
        getCurrentBoard: function() { return currentBoard; },
        hideWaMsgContextMenu: hideWaMsgContextMenu,
        refreshWaThreadIfOpen: refreshWaThreadIfOpen,
        waCtxSnapshotToBoardBottom: function() { return waManagement.waCtxSnapshotToBoardBottom(); },
        openTicketFromWhatsApp: openTicketFromWhatsApp,
        waRunDeleteChatConfirm: function(phone, name) { return waManagement.waRunDeleteChatConfirm(phone, name); },
        isAnyKanbanModalOpen: isAnyKanbanModalOpen,
        isMessagesPanelVisible: isMessagesPanelVisible,
        waThreadDeleteHotkeyIgnored: function() { return waManagement.waThreadDeleteHotkeyIgnored(); },
        switchSidebarTab: switchSidebarTab,
        applySidebarTabFromUrl: applySidebarTabFromUrl,
        bindWhatsAppUiHandlers: bindWhatsAppUiHandlers,
        startWhatsAppBootstrap: startWhatsAppBootstrap,
    });
    var waRuntime = window.KanbanWhatsAppRuntime.create({
        apiFetch: apiFetch,
        showSnackbar: showSnackbar,
        esc: esc,
        dedupeThreadMessages: dedupeThreadMessages,
        switchSidebarTab: switchSidebarTab,
        applySidebarTabFromUrl: applySidebarTabFromUrl,
        updateTabBarVisibility: boardSettings.updateTabBarVisibility,
        updateWaThreadSelectToggleUi: updateWaThreadSelectToggleUi,
        updateWaSidebarFooterUi: updateWaSidebarFooterUi,
        isMessagesPanelVisible: isMessagesPanelVisible,
        showWhatsAppNoMessagesState: showWhatsAppNoMessagesState,
        publishWaSubscriptions: publishWaSubscriptions,
        waShowThreadGlobalEmpty: waShowThreadGlobalEmpty,
        setWaThreadControlsEnabled: setWaThreadControlsEnabled,
        waCtxSnapshotToBoardBottom: waCtxSnapshotToBoardBottom,
        showWaMsgContextMenu: showWaMsgContextMenu,
        openTicketModal: openTicketModal,
        waMsgHasLinkedTicket: waMsgHasLinkedTicket,
        waIsMessageSelectable: waIsMessageSelectable,
        waResolveTargetJid: waHelpers.waResolveTargetJid,
        waBlobToBase64: waHelpers.waBlobToBase64,
        formatVoiceDuration: waHelpers.formatVoiceDuration,
        getWaVoiceFilename: waHelpers.getWaVoiceFilename,
        clearWaPendingAttachment: clearWaPendingAttachment,
        appendOptimisticSentMediaMessage: appendOptimisticSentMediaMessage,
        appendOptimisticSentTextMessage: appendOptimisticSentTextMessage,
        syncAndRefreshThreadAfterSend: syncAndRefreshThreadAfterSend,
        refreshWaThreadIfOpen: refreshWaThreadIfOpen,
        setWaMsgCtxData: function(data) { waMsgCtxData = data; },
        setWaChatLinkContext: function(sender, name, chatType) {
            return waManagement.setWaChatLinkContext(sender, name, chatType);
        },
        showWaChatContextMenu: showWaChatContextMenu,
        getState: function() {
            return {
                waChats: waChats,
                waSelectedJid: waSelectedJid,
                waSelectedChatType: waSelectedChatType,
                waActiveThread: waActiveThread,
                waThreadMessages: waThreadMessages,
                waSelectionMode: waSelectionMode,
                waSelectedMessageIds: waSelectedMessageIds,
                waConnected: waConnected,
                waVoiceRecorder: waVoiceRecorder,
                waVoiceStream: waVoiceStream,
                waVoiceChunks: waVoiceChunks,
                waVoiceRecording: waVoiceRecording,
                waVoiceStartedAtMs: waVoiceStartedAtMs,
                waVoiceTimerInterval: waVoiceTimerInterval,
                waPendingAttachment: waPendingAttachment,
                waSidebarChatListMode: waSidebarChatListMode,
                waSelectedChatPhones: waSelectedChatPhones,
                waGroupNames: waGroupNames,
                waChatNames: waChatNames,
                waDraftByPhone: waDraftByPhone,
            };
        },
        setState: function(s) {
            if (!s) return;
            waChats = s.waChats;
            waSelectedJid = s.waSelectedJid;
            waSelectedChatType = s.waSelectedChatType;
            waActiveThread = s.waActiveThread;
            waThreadMessages = s.waThreadMessages;
            waSelectionMode = s.waSelectionMode;
            waSelectedMessageIds = s.waSelectedMessageIds;
            waConnected = s.waConnected;
            waVoiceRecorder = s.waVoiceRecorder;
            waVoiceStream = s.waVoiceStream;
            waVoiceChunks = s.waVoiceChunks;
            waVoiceRecording = s.waVoiceRecording;
            waVoiceStartedAtMs = s.waVoiceStartedAtMs;
            waVoiceTimerInterval = s.waVoiceTimerInterval;
            waPendingAttachment = s.waPendingAttachment;
            waSidebarChatListMode = s.waSidebarChatListMode;
            waSelectedChatPhones = s.waSelectedChatPhones;
            waGroupNames = s.waGroupNames;
            waChatNames = s.waChatNames;
            waDraftByPhone = s.waDraftByPhone || {};
        },
    });
    var ticketActions = window.KanbanTicketActions.create({
        apiFetch: apiFetch,
        esc: esc,
        showSnackbar: showSnackbar,
        stripHtml: stripHtml,
        mergeSourceChatIntoPayload: commonUtils.mergeSourceChatIntoPayload,
        selectBoard: selectBoard,
        openTicketModal: openTicketModal,
        openCreateExternalTicketModal: openCreateExternalTicketModal,
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        getDbBoards: function() { return dbBoards; },
        getCopyModalState: function() { return copyModalState; },
        setCopyModalState: function(v) { copyModalState = v; },
    });
    var externalTicketModal = window.KanbanExternalTicketModal.create({
        apiFetch: apiFetch,
        esc: esc,
        showSnackbar: showSnackbar,
        mergeSourceChatIntoPayload: commonUtils.mergeSourceChatIntoPayload,
        selectBoard: selectBoard,
        getExternalBoards: getExternalBoards,
        refreshWaThreadIfOpen: refreshWaThreadIfOpen,
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        getWaTicketComposeInFlight: function() { return waTicketComposeInFlight; },
        setWaTicketComposeInFlight: function(v) { waTicketComposeInFlight = !!v; },
    });
    function reloadCurrentDatabaseBoard() {
        if (currentBoard && currentBoard.source === "database" && currentBoard.id) {
            selectBoard("database", currentBoard.id);
        }
    }

    // ── Load boards sidebar ──

    var _boardsCache = null;
    var _externalCache = null;
    var _boardsCacheTime = 0;
    var _externalCacheTime = 0;
    var BOARDS_CACHE_TTL = 60000; // 1 minute (local boards change often)
    var EXTERNAL_CACHE_TTL = 300000; // 5 minutes (external boards are slower + less volatile)

    function sortExternalBoardsForSidebar(boards) {
        if (!Array.isArray(boards)) return [];
        var configured = boards.filter(function(b) { return b && b.local_id; });
        var unconfigured = boards.filter(function(b) { return b && !b.local_id; });
        configured.sort(function(a, b) {
            var aStamp = String(a.modified_date || "");
            var bStamp = String(b.modified_date || "");
            if (aStamp !== bStamp) return aStamp < bStamp ? 1 : -1;
            return String(a.name || "").toLowerCase().localeCompare(String(b.name || "").toLowerCase());
        });
        unconfigured.sort(function(a, b) {
            return String(a.name || "").toLowerCase().localeCompare(String(b.name || "").toLowerCase());
        });
        return configured.concat(unconfigured);
    }

    function touchExternalBoardActivity(source, boardId) {
        if (source !== "trello" && source !== "jira") return Promise.resolve();
        return apiFetch("/api/tickets/external-boards/" + source + "/" + encodeURIComponent(boardId) + "/touch", { method: "POST" })
            .then(function(res) {
                if (!res || !res.success || !_externalCache) return;
                var list = _externalCache[source];
                if (!Array.isArray(list)) return;
                var board = list.filter(function(b) { return String(b.id) === String(boardId); })[0];
                if (!board) return;
                if (res.modified_date) board.modified_date = res.modified_date;
                if (res.local_id) {
                    board.local_id = res.local_id;
                    board.has_local_config = true;
                }
                list = sortExternalBoardsForSidebar(list);
                _externalCache[source] = list;
                renderExternalBoards(source === "trello" ? "kb-trello-boards" : "kb-jira-boards", list, source);
            })
            .catch(function() {});
    }

    function getExternalBoards(forceRefresh) {
        var now = Date.now();
        var stale = forceRefresh || !_externalCache || (now - _externalCacheTime > EXTERNAL_CACHE_TTL);
        if (!stale && _externalCache) {
            return Promise.resolve(_externalCache);
        }
        return apiFetch("/api/tickets/external-boards").then(function(data) {
            data = data || { trello: [], jira: [] };
            _externalCache = {
                trello: sortExternalBoardsForSidebar(data.trello || []),
                jira: sortExternalBoardsForSidebar(data.jira || []),
            };
            _externalCacheTime = Date.now();
            return _externalCache;
        });
    }

    function loadBoards(forceRefresh) {
        var now = Date.now();
        var boardsStale = forceRefresh || !_boardsCache || (now - _boardsCacheTime > BOARDS_CACHE_TTL);
        var externalStale = forceRefresh || !_externalCache || (now - _externalCacheTime > EXTERNAL_CACHE_TTL);

        var boardsPromise = boardsStale
            ? apiFetch("/api/tickets/boards").then(function(boards) {
                _boardsCache = boards;
                _boardsCacheTime = Date.now();
                return boards;
            })
            : Promise.resolve(_boardsCache);

        boardsPromise.then(function(boards) {
            dbBoards = boards.filter(function(b) { return b.source === "database"; });
            renderSidebarBoards(boards);

            // Auto-select: URL param > localStorage > first board
            if (!currentBoard) {
                var params = new URLSearchParams(window.location.search);
                var urlView = (params.get("view") || "").toLowerCase();
                if (urlView === "list" || urlView === "kanban") {
                    kbBoardViewMode = urlView;
                    try { localStorage.setItem("kb_board_view_mode", kbBoardViewMode); } catch (e) {}
                }
                var urlSource = (params.get("source") || "").toLowerCase();
                var urlBoardIdRaw = (params.get("board_id") || "").trim();
                var urlBoardUrl = (params.get("board_url") || "").trim();
                if ((urlSource === "jira" || urlSource === "trello") && urlBoardIdRaw) {
                    selectBoard(urlSource, urlBoardIdRaw, urlBoardUrl);
                } else {
                    var urlBoardId = parseInt(urlBoardIdRaw, 10);
                    if (urlBoardId && dbBoards.some(function(b) { return b.id === urlBoardId; })) {
                        selectBoard("database", urlBoardId);
                    } else if (dbBoards.length) {
                        var last = null;
                        try { last = JSON.parse(localStorage.getItem("kb_last_selected")); } catch (e) {}
                        if (last && last.source === "database" && dbBoards.some(function(b) { return b.id === last.id; })) {
                            selectBoard(last.source, last.id);
                        } else {
                            selectBoard("database", dbBoards[0].id);
                        }
                    }
                }
            }
        }).catch(function() { showSnackbar("Failed to load boards", "error"); });

        if (externalStale) {
            getExternalBoards(true).then(function(data) {
                _externalCache = data;
                _externalCacheTime = Date.now();
                renderExternalBoards("kb-trello-boards", data.trello || [], "trello");
                renderExternalBoards("kb-jira-boards", data.jira || [], "jira");
            }).catch(function(e) {
                console.error("Failed to load external boards:", e);
            });
        } else if (_externalCache) {
            renderExternalBoards("kb-trello-boards", _externalCache.trello || [], "trello");
            renderExternalBoards("kb-jira-boards", _externalCache.jira || [], "jira");
        }
    }

    function updateSourceTabsVisibility() {
        var trelloTab = document.getElementById("kb-src-trello");
        var jiraTab = document.getElementById("kb-src-jira");
        var tabsRow = document.getElementById("kb-src-tabs-row");
        // Show tabs row only if at least one external source is available
        var hasExternal = !trelloTab.classList.contains("hidden") || !jiraTab.classList.contains("hidden");
        tabsRow.classList.toggle("hidden", !hasExternal);
    }

    function switchSourceTab(src) {
        activeSourceTab = src;
        document.querySelectorAll(".kb-src-tab").forEach(function(btn) {
            btn.classList.toggle("active", btn.dataset.src === src);
        });
        document.getElementById("kb-local-boards-container").classList.toggle("hidden", src !== "local");
        document.getElementById("kb-trello-boards-container").classList.toggle("hidden", src !== "trello");
        document.getElementById("kb-jira-boards-container").classList.toggle("hidden", src !== "jira");
    }

    function focusFirstBoardInActiveSource() {
        var containerId = activeSourceTab === "trello" ? "kb-trello-boards" : (activeSourceTab === "jira" ? "kb-jira-boards" : "kb-db-boards");
        var selector = containerId === "kb-db-boards" ? ".kb-board-item-wrapper" : ".kb-board-item";
        var first = document.querySelector("#" + containerId + " " + selector);
        if (first) first.focus();
    }

    function getActiveBoardRows() {
        var containerId = activeSourceTab === "trello" ? "kb-trello-boards" : (activeSourceTab === "jira" ? "kb-jira-boards" : "kb-db-boards");
        var selector = containerId === "kb-db-boards" ? ".kb-board-item-wrapper" : ".kb-board-item";
        return Array.prototype.slice.call(document.querySelectorAll("#" + containerId + " " + selector));
    }

    function getBoardRowFromEventTarget(target) {
        if (!target || !target.closest) return null;
        return target.closest(".kb-board-item-wrapper") || target.closest(".kb-board-item");
    }

    var boardDeleteIconSvg = '<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';

    function focusBoardRowByOffset(offset) {
        var rows = getActiveBoardRows();
        if (!rows.length) return;
        var active = getBoardRowFromEventTarget(document.activeElement);
        var idx = rows.indexOf(active);
        if (idx < 0 && currentBoard) {
            idx = rows.findIndex(function(row) {
                return String(row.dataset.boardId) === String(currentBoard.id) && String(row.dataset.boardSource || "database") === String(currentBoard.source || "database");
            });
        }
        if (idx < 0) idx = offset > 0 ? -1 : 0;
        var next = rows[Math.max(0, Math.min(rows.length - 1, idx + offset))];
        if (next) next.focus();
    }

    function bindBoardSidebarKeyboard() {
        var sidebar = document.getElementById("kb-sidebar") || document.getElementById("kb-panel-tickets");
        if (!sidebar || sidebar.dataset.boardKeyboardBound === "1") return;
        sidebar.dataset.boardKeyboardBound = "1";
        sidebar.addEventListener("keydown", function(e) {
            if (isKeyboardEditingTarget(e.target)) return;
            var sourceTab = e.target && e.target.closest ? e.target.closest(".kb-src-tab") : null;
            if (sourceTab && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                switchSourceTab(sourceTab.dataset.src || "local");
                focusFirstBoardInActiveSource();
                return;
            }
            if (sourceTab && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
                e.preventDefault();
                var tabs = Array.prototype.slice.call(document.querySelectorAll(".kb-src-tab:not(.hidden)"));
                var idx = tabs.indexOf(sourceTab);
                var next = tabs[Math.max(0, Math.min(tabs.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)))];
                if (next) next.focus();
                return;
            }
            var boardRow = getBoardRowFromEventTarget(e.target);
            if (boardRow) {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    focusBoardRowByOffset(e.key === "ArrowDown" ? 1 : -1);
                } else if (e.key === "Enter") {
                    if (e.target && e.target.closest && e.target.closest(".kb-board-item-delete")) return;
                    e.preventDefault();
                    selectBoard(boardRow.dataset.boardSource || "database", boardRow.dataset.boardId, boardRow.dataset.boardUrl || "");
                } else if (e.key === "Delete") {
                    if (document.getElementById("decisions-confirm-modal") || isAnyKanbanModalOpen()) return;
                    if ((boardRow.dataset.boardSource || "database") !== "database") return;
                    e.preventDefault();
                    confirmDeleteLocalBoardById(boardRow.dataset.boardId);
                }
            }
        });
    }

    function renderSidebarBoards(boards) {
        var container = document.getElementById("kb-db-boards");
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var db = boards.filter(function(b) { return b.source === "database"; });
        if (search) db = db.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; });
        container.innerHTML = db.length ? "" : '<p class="text-xs text-gray-500 italic">No boards yet</p>';
        db.forEach(function(b) {
            var wrapper = document.createElement("div");
            var isActive = currentBoard && currentBoard.id === b.id && currentBoard.source === "database";
            wrapper.className = "kb-board-item-wrapper text-gray-300" + (isActive ? " active" : "");
            wrapper.draggable = true;
            wrapper.dataset.boardId = b.id;
            wrapper.dataset.boardSource = "database";
            wrapper.tabIndex = 0;
            wrapper.setAttribute("role", "option");
            wrapper.setAttribute("aria-selected", isActive ? "true" : "false");
            var inUseDot = b.in_use
                ? '<span class="kb-board-in-use-dot w-2 h-2 rounded-full bg-[#f97316] flex-shrink-0" title="In use" aria-label="In use"></span>'
                : "";
            wrapper.innerHTML =
                '<button type="button" class="kb-board-item-main">' +
                    '<span class="kb-src-icon" style="background:' + esc(b.color || "#f97316") + '"></span>' +
                    '<span class="flex-1 truncate">' + esc(b.name) + "</span>" +
                "</button>" +
                inUseDot +
                '<button type="button" class="kb-board-item-delete" aria-label="Delete board">' + boardDeleteIconSvg + "</button>";
            var mainBtn = wrapper.querySelector(".kb-board-item-main");
            var deleteBtn = wrapper.querySelector(".kb-board-item-delete");
            if (mainBtn) {
                mainBtn.addEventListener("click", function(e) {
                    e.stopPropagation();
                    selectBoard("database", b.id);
                });
            }
            if (deleteBtn) {
                deleteBtn.addEventListener("click", function(e) {
                    e.stopPropagation();
                    e.preventDefault();
                    confirmDeleteLocalBoardById(b.id);
                });
            }
            wrapper.addEventListener("focus", function() {
                if (!currentBoard || currentBoard.id !== b.id || currentBoard.source !== "database") {
                    selectBoard("database", b.id);
                }
            });
            wrapper.addEventListener("dblclick", function(e) {
                if (e.target && e.target.closest && e.target.closest(".kb-board-item-delete")) return;
                boardSettings.openBoardModal(b.id);
            });
            wrapper.addEventListener("dragstart", function(e) {
                if (e.target && e.target.closest && e.target.closest(".kb-board-item-delete")) {
                    e.preventDefault();
                    return;
                }
                e.dataTransfer.setData("text/plain", "board:" + b.id);
                wrapper.classList.add("dragging");
            });
            wrapper.addEventListener("dragend", function() { wrapper.classList.remove("dragging"); });
            wrapper.addEventListener("dragover", function(e) { e.preventDefault(); wrapper.style.borderTop = "2px solid #f97316"; });
            wrapper.addEventListener("dragleave", function() { wrapper.style.borderTop = ""; });
            wrapper.addEventListener("drop", function(e) {
                e.preventDefault();
                wrapper.style.borderTop = "";
                var data = e.dataTransfer.getData("text/plain");
                if (!data.startsWith("board:")) return;
                var draggedId = parseInt(data.split(":")[1], 10);
                if (draggedId === b.id) return;
                var items = container.querySelectorAll("[data-board-id]");
                var order = [];
                items.forEach(function(el) { order.push(parseInt(el.dataset.boardId, 10)); });
                order = order.filter(function(id) { return id !== draggedId; });
                var dropIdx = order.indexOf(b.id);
                order.splice(dropIdx, 0, draggedId);
                apiFetch("/api/tickets/boards/reorder", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ order: order })
                }).then(function() { loadBoards(true); }).catch(function() {});
            });
            wrapper.addEventListener("contextmenu", function(e) { e.preventDefault(); boardSettings.showBoardContextMenu(e, b.id); });
            container.appendChild(wrapper);
        });
    }

    function renderExternalBoards(containerId, boards, source) {
        var container = document.getElementById(containerId);
        // Show/hide the source tab based on whether there are boards
        var tabId = source === "trello" ? "kb-src-trello" : "kb-src-jira";
        var tabBtn = document.getElementById(tabId);
        if (boards.length > 0) {
            tabBtn.classList.remove("hidden");
        } else {
            tabBtn.classList.add("hidden");
            // If we were on this tab and it became empty, switch to local
            if (activeSourceTab === source) switchSourceTab("local");
        }
        updateSourceTabsVisibility();
        if (!boards.length) {
            container.innerHTML = '<p class="text-xs text-gray-500 italic">No ' + source + ' account connected</p>';
            return;
        }
        boards = sortExternalBoardsForSidebar(boards);
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var filtered = search ? boards.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; }) : boards;
        container.innerHTML = filtered.length ? "" : '<p class="text-xs text-gray-500 italic">No matching boards</p>';
        filtered.forEach(function(b) {
            var div = document.createElement("div");
            div.className = "kb-board-item text-gray-300" + (currentBoard && currentBoard.id === b.id && currentBoard.source === source ? " active" : "");
            div.dataset.boardId = b.id;
            div.dataset.boardSource = source;
            div.dataset.boardUrl = b.url || "";
            div.tabIndex = 0;
            div.setAttribute("role", "option");
            div.setAttribute("aria-selected", currentBoard && currentBoard.id === b.id && currentBoard.source === source ? "true" : "false");
            var defaultColor = source === "trello" ? "#0079bf" : "#0052cc";
            var iconColor = b.color || defaultColor;
            div.innerHTML = '<span class="kb-src-icon" style="background:' + esc(iconColor) + '"></span><span class="flex-1 truncate">' + esc(b.name) + '</span>';
            div.onclick = function() { selectBoard(source, b.id, b.url); };
            div.onfocus = function() { if (!currentBoard || currentBoard.id !== b.id || currentBoard.source !== source) selectBoard(source, b.id, b.url); };
            div.oncontextmenu = function(e) { e.preventDefault(); showExtBoardContextMenu(e, source, b.id, b.url); };
            container.appendChild(div);
        });
    }

    // ── External board context menu (right-click on Trello/Jira boards) ──
    var extCtxMenuData = null;
    function showExtBoardContextMenu(e, source, boardId, boardUrl) {
        extCtxMenuData = { source: source, boardId: boardId, boardUrl: boardUrl };
        var menu = document.getElementById("kb-ext-board-ctx-menu");
        menu.style.left = e.clientX + "px";
        menu.style.top = e.clientY + "px";
        menu.classList.remove("hidden");
    }
    function hideExtBoardContextMenu() {
        document.getElementById("kb-ext-board-ctx-menu").classList.add("hidden");
        extCtxMenuData = null;
    }
    function extCtxConfigure() {
        if (!extCtxMenuData) return;
        var src = extCtxMenuData.source, bid = extCtxMenuData.boardId;
        hideExtBoardContextMenu();
        boardSettings.openExternalBoardConfigModal(src, bid);
    }

    // ── Create external ticket modal ──
    function openCreateExternalTicketModal() {
        return externalTicketModal.openCreateExternalTicketModal();
    }


    // ── WhatsApp media helpers ──────────────────────────────────────────
    function waImageLightbox(src) {
        var lb = document.getElementById("kb-wa-lightbox");
        var img = document.getElementById("kb-wa-lightbox-img");
        img.src = src;
        lb.classList.remove("hidden");
    }

    function waToggleAudio(btn) {
        var container = btn.closest(".bg-\[\#25D366\]\/10") || btn.parentElement;
        var audio = container.querySelector("audio");
        var playIcon = btn.querySelector(".play-icon");
        var pauseIcon = btn.querySelector(".pause-icon");
        var timeEl = container.querySelector(".wa-voice-time");
        if (!audio) return;

        if (audio.paused) {
            // Pause any other playing voice notes
            document.querySelectorAll(".wa-msg-bubble audio").forEach(function(a) {
                if (a !== audio) { a.pause(); a.currentTime = 0; }
            });
            audio.play();
            if (playIcon) playIcon.classList.add("hidden");
            if (pauseIcon) pauseIcon.classList.remove("hidden");
        } else {
            audio.pause();
            if (playIcon) playIcon.classList.remove("hidden");
            if (pauseIcon) pauseIcon.classList.add("hidden");
        }

        audio.addEventListener("timeupdate", function() {
            if (timeEl) {
                var mins = Math.floor(audio.currentTime / 60);
                var secs = Math.floor(audio.currentTime % 60);
                timeEl.textContent = mins + ":" + (secs < 10 ? "0" : "") + secs;
            }
        });
        audio.addEventListener("ended", function() {
            if (playIcon) playIcon.classList.remove("hidden");
            if (pauseIcon) pauseIcon.classList.add("hidden");
            audio.currentTime = 0;
        });
    }
    // ── Select board ──

    function selectBoard(source, id, extUrl, opts) {
        opts = opts || {};
        var boardChanged = !currentBoard || String(currentBoard.id) !== String(id) || currentBoard.source !== source;
        if (boardChanged && kbDocumentsWorkspaceOpen() && window.KanbanDocuments && typeof window.KanbanDocuments.collapse === "function") {
            window.KanbanDocuments.collapse();
        }
        currentBoard = { id: id, source: source, extUrl: extUrl || "" };
        var keepMessagesVisible = isMessagesPanelVisible();
        // Auto-switch source tab when selecting a board
        var srcTab = source === "database" ? "local" : source;
        switchSourceTab(srcTab);
        try { localStorage.setItem("kb_last_selected", JSON.stringify({ source: source, id: id })); } catch (e) {}
        if (!keepMessagesVisible) {
        // Show loading spinner
        document.getElementById("kb-empty").classList.add("hidden");
        document.getElementById("kb-board-view").classList.add("hidden");
        document.getElementById("kb-loading").classList.remove("hidden");
        // Close any open WhatsApp thread view
        document.getElementById("kb-wa-thread-view").classList.add("hidden");
        waSelectedJid = null;
        }

        if (source === "database") {
            var boardPromise = Promise.all([
                apiFetch("/api/tickets/boards/" + id),
                apiFetch("/api/tickets/boards").catch(function() { return []; }),
            ]).then(function(results) {
                var data = results[0] || {};
                currentBoardData = data;
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                kbRevealBoardView();
                }
                renderBoard(data, true);
            }).catch(function(e) {
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                }
                showSnackbar("Failed to load board: " + e.message, "error");
                if (opts.rejectOnError) throw e;
            });
            loadBoards(); // uses cache, just re-renders sidebar active state
            return boardPromise;
        } else {
            touchExternalBoardActivity(source, id);
            var externalPromise = (function fetchExtBoard(attempt) {
                attempt = attempt || 0;
                var forceQ = opts.forceRefresh && attempt === 0 ? "?force_refresh=1" : "";
                return apiFetch("/api/tickets/external-boards/" + source + "/" + encodeURIComponent(id) + forceQ).then(function(data) {
                    currentBoardData = data;
                    if (data.cache_ready === false && attempt < 90) {
                        if (!keepMessagesVisible) {
                            document.getElementById("kb-loading").classList.add("hidden");
                            kbRevealBoardView();
                        }
                        renderBoard(data, false);
                        return new Promise(function(resolve) {
                            setTimeout(function() { resolve(fetchExtBoard(attempt + 1)); }, 700);
                        });
                    }
                    if (!keepMessagesVisible) {
                        document.getElementById("kb-loading").classList.add("hidden");
                        document.getElementById("kb-board-view").classList.remove("hidden");
                    }
                    renderBoard(data, false);
                }).catch(function(e) {
                    if (!keepMessagesVisible) {
                        document.getElementById("kb-loading").classList.add("hidden");
                        document.getElementById("kb-empty").classList.remove("hidden");
                    }
                    showSnackbar("Failed to load external board: " + e.message, "error");
                    if (opts.rejectOnError) throw e;
                });
            })(0);
            loadBoards(); // uses cache, just re-renders sidebar active state
            return externalPromise;
        }
    }

    function renderBoard(data, isLocal) {
        // Apply board accent color as CSS variable
        var boardColor = data.color || (isLocal ? null : (currentBoard.source === "trello" ? "#0079bf" : "#0052cc")) || "#f97316";
        document.getElementById("kb-board-view").style.setProperty("--kb-accent", boardColor);

        document.getElementById("kb-board-title").textContent = data.name || "Board";
        var badge = document.getElementById("kb-board-source-badge");
        var source = currentBoard.source;
        if (source === "database") {
            badge.classList.add("hidden");
        } else {
            badge.classList.remove("hidden");
            badge.textContent = source.charAt(0).toUpperCase() + source.slice(1);
            badge.className = "text-xs px-2 py-0.5 rounded text-white";
            badge.style.backgroundColor = source === "trello" ? "#0079bf" : "#0052cc";
        }

        var addTicketBtn = document.getElementById("kb-add-ticket");
        var addTicketLabel = document.getElementById("kb-add-ticket-label");
        if (!isLocal) {
            var canCreateExternal = data.can_create_ticket !== false;
            addTicketBtn.style.display = canCreateExternal ? "" : "none";
            if (addTicketLabel) addTicketLabel.textContent = "Create ticket";
            addTicketBtn.title = canCreateExternal
                ? ("Create a ticket on this " + (currentBoard.source === 'trello' ? 'Trello' : 'Jira') + " board")
                : "You do not have permission to create tickets on this board";
            addTicketBtn.setAttribute("aria-label", "Create ticket");
        } else {
            addTicketBtn.style.display = "";
            if (addTicketLabel) addTicketLabel.textContent = "Add ticket";
            addTicketBtn.title = "Add ticket";
            addTicketBtn.setAttribute("aria-label", "Add ticket");
        }
        var refreshBtn = document.getElementById("kb-refresh-boards");
        if (refreshBtn) {
            refreshBtn.classList.toggle("hidden", isLocal);
        }
        // Edit button stays icon-only in the header; title/aria-label carry the text.
        var editBtn = document.getElementById("kb-edit-board");
        var editLabel = document.getElementById("kb-edit-board-label");
        editBtn.style.display = "";
        if (editLabel) editLabel.textContent = "Edit";
        editBtn.title = isLocal ? "Edit board settings" : "Edit this external board";
        editBtn.setAttribute("aria-label", editBtn.title);
        // Delete button — hidden from header (available in right-click context menu)
        document.getElementById("kb-delete-board").style.display = "none";

        var extLink = document.getElementById("kb-board-ext-link");
        if (!isLocal && (data.url || currentBoard.extUrl)) {
            extLink.classList.remove("hidden");
            extLink.href = data.url || currentBoard.extUrl;
        } else {
            extLink.classList.add("hidden");
        }

        // Store board-level data for conditional actions
        currentBoardData = data;
        currentBoardHasProject = !!data.default_project_id;
        if (!isLocal && data.local_id) {
            currentBoard._localId = data.local_id;
        }

        renderBoardTickets(data.lanes || [], isLocal, data);
    }

    function updateBoardViewButtons() {
        document.querySelectorAll(".kb-view-toggle").forEach(function(btn) {
            var active = btn.dataset.view === kbBoardViewMode;
            btn.classList.toggle("active", active);
            btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }

    function setBoardViewMode(mode) {
        kbBoardViewMode = mode === "list" ? "list" : "kanban";
        try { localStorage.setItem("kb_board_view_mode", kbBoardViewMode); } catch (e) {}
        updateBoardViewButtons();
        if (currentBoardData && currentBoard) {
            renderBoardTickets(currentBoardData.lanes || [], currentBoard.source === "database", currentBoardData);
        }
    }

    function renderBoardTickets(lanes, isLocal, boardData) {
        var lanesEl = document.getElementById("kb-lanes");
        var listEl = document.getElementById("kb-ticket-list");
        updateBoardViewButtons();
        if (kbBoardViewMode === "list") {
            lanesEl.classList.add("hidden");
            listEl.classList.remove("hidden");
            renderTicketList(lanes, isLocal, boardData);
            return;
        }
        listEl.classList.add("hidden");
        lanesEl.classList.remove("hidden");
        renderLanes(lanes, isLocal, boardData);
    }

    function refreshCurrentBoardRealtime() {
        if (!currentBoard || currentBoard.source !== "database") return;
        var boardId = currentBoard.id;
        Promise.all([
            apiFetch("/api/tickets/boards/" + boardId),
            apiFetch("/api/tickets/boards").catch(function() { return []; }),
        ]).then(function(results) {
            if (!currentBoard || currentBoard.source !== "database" || currentBoard.id !== boardId) return;
            var data = results[0] || {};
            currentBoardData = data;
            renderBoard(data, true);
        }).catch(function() {});
    }

    function scheduleRealtimeBoardRefresh() {
        if (kbBoardRefreshTimer) return;
        kbBoardRefreshTimer = setTimeout(function() {
            kbBoardRefreshTimer = null;
            refreshCurrentBoardRealtime();
        }, 120);
    }

    function connectKanbanBoardWS() {
        if (kbBoardWS && (kbBoardWS.readyState === WebSocket.OPEN || kbBoardWS.readyState === WebSocket.CONNECTING)) return;
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + location.host + "/api/tickets/ws/boards";
        try {
            kbBoardWS = new WebSocket(url);
        } catch (e) {
            if (!kbBoardWSReconnectTimer) kbBoardWSReconnectTimer = setTimeout(function() {
                kbBoardWSReconnectTimer = null;
                connectKanbanBoardWS();
            }, 5000);
            return;
        }
        kbBoardWS.onopen = function() {
            if (kbBoardWSReconnectTimer) {
                clearTimeout(kbBoardWSReconnectTimer);
                kbBoardWSReconnectTimer = null;
            }
        };
        kbBoardWS.onmessage = function(evt) {
            try {
                var msg = JSON.parse(evt.data || "{}");
                if (msg.type === "ping") return;
                if (msg.type !== "kanban_updated") return;
                if (!currentBoard || currentBoard.source !== "database") return;
                if (msg.board_id != null && String(msg.board_id) !== String(currentBoard.id)) return;
                var eventType = (msg.event || "").toLowerCase();
                var payload = msg.payload || {};
                if (eventType === "ticket_workflow_status") {
                    if (payload && payload.ticket_id && payload.status) {
                        setTicketWorkflowStatusOnCard(payload.ticket_id, payload.status);
                    }
                    return;
                }
                if (eventType === "run_completed") {
                    if (payload && payload.ticket_id && payload.status) {
                        setTicketWorkflowStatusOnCard(payload.ticket_id, payload.status);
                        showSnackbar("Workflow " + String(payload.status) + " for ticket #" + String(payload.ticket_id));
                        return;
                    }
                }
                scheduleRealtimeBoardRefresh();
            } catch (e) {}
        };
        kbBoardWS.onclose = function() {
            kbBoardWS = null;
            if (!kbBoardWSReconnectTimer) kbBoardWSReconnectTimer = setTimeout(function() {
                kbBoardWSReconnectTimer = null;
                connectKanbanBoardWS();
            }, 5000);
        };
        kbBoardWS.onerror = function() {};
    }

    function bindLaneCopyAllButton(rootEl, lane) {
        if (!rootEl || !lane) return;
        var btn = rootEl.querySelector(".kb-lane-copy-all");
        if (!btn) return;
        btn.addEventListener("click", function(e) {
            e.preventDefault();
            e.stopPropagation();
            openCopyLaneModal(lane);
        });
    }

    function bindLaneWhatsappSnapshotButton(rootEl) {
        if (!rootEl) return;
        var btn = rootEl.querySelector(".kb-lane-whatsapp-snapshot");
        if (!btn) return;
        btn.addEventListener("click", function(e) {
            e.preventDefault();
            e.stopPropagation();
            openBoardWhatsappSnapshotFromLane(btn);
        });
    }

    function laneWhatsappIntakeLink(lane, boardData, isLocal) {
        if (!isLocal || !lane || !boardData) return null;
        var links = Array.isArray(boardData.whatsapp_links) ? boardData.whatsapp_links : [];
        var link = links.filter(function(item) { return item && item.id; })[0] || null;
        if (!link) return null;
        var lanes = Array.isArray(boardData.lanes) ? boardData.lanes : [];
        var firstLanePosition = lanes.length ? lanes[0].position : 0;
        var laneName = String(lane.name || "").toLowerCase();
        var isIntakeLane = laneName === "backlog" || laneName.indexOf("backlog") >= 0 || lane.position === firstLanePosition;
        return isIntakeLane ? link : null;
    }

    function laneHeaderToolsHtml(lane, isLocal, boardData) {
        var count = (lane.tickets || []).length;
        var copyBtn = (!isLocal && count > 0 && ticketActions.laneCopyAllButtonHtml)
            ? ticketActions.laneCopyAllButtonHtml("Copy all in " + (lane.name || "column") + " to local board")
            : "";
        var waLink = laneWhatsappIntakeLink(lane, boardData, isLocal);
        var waBtn = (waLink && ticketActions.laneWhatsappSnapshotButtonHtml)
            ? ticketActions.laneWhatsappSnapshotButtonHtml({
                boardId: boardData && boardData.id,
                linkId: waLink.id,
                laneId: lane.id,
                laneName: lane.name || "",
                boardName: (boardData && boardData.name) || "",
                linkPhone: waLink.phone_number || "",
                tooltip: "Create ticket from linked WhatsApp messages",
            })
            : "";
        return '<div class="flex items-center gap-1.5 flex-shrink-0">' + copyBtn + waBtn +
            '<span class="text-xs text-gray-500">' + count + "</span></div>";
    }

    function renderLanes(lanes, isLocal, boardData) {
        var container = document.getElementById("kb-lanes");
        container.innerHTML = "";
        var boardData = currentBoardData || {};
        lanes.forEach(function(lane) {
            var col = document.createElement("div");
            col.className = "kb-lane flex flex-col bg-[#152054]/50 rounded-lg border border-white/10";
            col.innerHTML = '<div class="px-3 py-2 border-b border-white/10 flex items-center justify-between gap-2">' +
                '<span class="text-sm font-medium text-gray-300 truncate">' + esc(lane.name) + "</span>" +
                laneHeaderToolsHtml(lane, isLocal, boardData) + "</div>";
            bindLaneCopyAllButton(col, lane);
            bindLaneWhatsappSnapshotButton(col);
            var body = document.createElement("div");
            body.className = "kb-lane-body flex-1 p-2 space-y-2 overflow-y-auto";
            body.dataset.laneId = lane.id;
            body.addEventListener("dragover", function(e) { e.preventDefault(); body.classList.add("drag-over"); });
            body.addEventListener("dragleave", function() { body.classList.remove("drag-over"); });
            body.addEventListener("drop", function(e) {
                e.preventDefault(); body.classList.remove("drag-over");
                var ticketId = e.dataTransfer.getData("text/plain");
                if (!ticketId) return;
                if (isLocal) {
                    var tid = parseInt(ticketId, 10);
                    if (!isFinite(tid)) return;
                    moveTicket(tid, lane.id, body, e.clientY);
                } else {
                    moveExternalTicket(ticketId, lane.id, body, e.clientY);
                }
            });
            (lane.tickets || []).forEach(function(ticket) { body.appendChild(createTicketCard(ticket, isLocal, boardData)); });
            col.appendChild(body);
            container.appendChild(col);
        });
    }

    function listLaneExpandedStorageKey() {
        if (!currentBoard || currentBoard.id == null) return null;
        var source = currentBoard.source || "database";
        return "kb_list_lane_expanded_" + source + "_" + String(currentBoard.id);
    }

    function resolveDefaultListExpandedLaneId(lanes) {
        if (!lanes || !lanes.length) return null;
        var currentLane = lanes.find(function(lane) {
            return String(lane.name || "").trim().toLowerCase() === "current";
        });
        if (currentLane && currentLane.id != null) return String(currentLane.id);
        return lanes[0].id != null ? String(lanes[0].id) : null;
    }

    function getSavedListExpandedLaneId(lanes) {
        var key = listLaneExpandedStorageKey();
        if (key) {
            try {
                var saved = localStorage.getItem(key);
                if (saved === "__none__") return null;
                if (saved && lanes.some(function(lane) { return String(lane.id) === String(saved); })) {
                    return String(saved);
                }
            } catch (e) {}
        }
        return resolveDefaultListExpandedLaneId(lanes);
    }

    function saveListExpandedLaneId(laneId) {
        var key = listLaneExpandedStorageKey();
        if (!key) return;
        try {
            if (laneId == null || laneId === "") {
                localStorage.setItem(key, "__none__");
            } else {
                localStorage.setItem(key, String(laneId));
            }
        } catch (e) {}
    }

    function listLaneChevronSvg() {
        return '<svg class="kb-ticket-list-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>';
    }

    function initVisibleListRowMarquees(rootEl) {
        (rootEl || document).querySelectorAll(".kb-ticket-list-section--expanded .kb-ticket-list-row").forEach(function(row) {
            ticketUi.initListRowMarquee(row);
        });
    }

    function setListLaneExpanded(container, laneId) {
        if (!container) return;
        var expandedId = laneId != null && laneId !== "" ? String(laneId) : "";
        container.querySelectorAll(".kb-ticket-list-section").forEach(function(section) {
            var isExpanded = expandedId && String(section.dataset.laneId) === expandedId;
            section.classList.toggle("kb-ticket-list-section--expanded", isExpanded);
            var head = section.querySelector(".kb-ticket-list-section-head");
            var body = section.querySelector(".kb-ticket-list-section-body");
            if (head) head.setAttribute("aria-expanded", isExpanded ? "true" : "false");
            if (body) body.hidden = !isExpanded;
        });
        if (expandedId) {
            var active = container.querySelector('.kb-ticket-list-section[data-lane-id="' + expandedId + '"]');
            if (active) initVisibleListRowMarquees(active);
        }
    }

    function listLaneDropPosition(bodyEl) {
        if (!bodyEl) return 0;
        return bodyEl.querySelectorAll(".kb-ticket-list-row, .kb-card").length;
    }

    function moveTicketToLane(ticketId, laneId, bodyEl) {
        var laneIdNum = parseInt(laneId, 10);
        if (!isFinite(ticketId) || !isFinite(laneIdNum)) return;
        apiFetch("/api/tickets/tickets/" + ticketId + "/move", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: laneIdNum, position: listLaneDropPosition(bodyEl) }),
        }).then(function() {
            if (currentBoard && currentBoard.source === "database") {
                selectBoard("database", currentBoard.id);
            }
        }).catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    function moveExternalTicketToLane(ticketId, targetLaneId, bodyEl) {
        var src = currentBoard && currentBoard.source;
        if (src !== "trello" && src !== "jira") return;
        var bid = currentBoard.id;
        apiFetch("/api/tickets/external-boards/" + src + "/" + encodeURIComponent(bid) + "/move-ticket", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ticket_id: String(ticketId),
                target_lane_id: String(targetLaneId),
                position: listLaneDropPosition(bodyEl),
            }),
        }).then(function() {
            selectBoard(src, bid, currentBoard.extUrl || "");
        }).catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    function bindListLaneDropTargets(container, isLocal) {
        container.querySelectorAll(".kb-ticket-list-section").forEach(function(section) {
            var laneId = section.dataset.laneId;
            if (!laneId) return;
            var body = section.querySelector(".kb-ticket-list-section-body");
            var head = section.querySelector(".kb-ticket-list-section-head");

            function markDragOver(el) {
                if (!el) return;
                el.classList.add("drag-over");
                section.classList.add("drag-over");
            }

            function clearDragOver(el) {
                if (!el) return;
                el.classList.remove("drag-over");
                if (!section.querySelector(".drag-over")) {
                    section.classList.remove("drag-over");
                }
            }

            function handleDragOver(e) {
                e.preventDefault();
                e.stopPropagation();
                if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
                markDragOver(e.currentTarget);
            }

            function handleDragLeave(e) {
                var el = e.currentTarget;
                if (el.contains(e.relatedTarget)) return;
                clearDragOver(el);
            }

            function handleDrop(e) {
                e.preventDefault();
                e.stopPropagation();
                clearDragOver(e.currentTarget);
                section.classList.remove("drag-over");
                var ticketId = e.dataTransfer.getData("text/plain");
                if (!ticketId) return;
                var sourceLaneId = e.dataTransfer.getData("application/x-kanban-source-lane");
                if (sourceLaneId && String(sourceLaneId) === String(laneId)) return;
                if (isLocal) {
                    var tid = parseInt(ticketId, 10);
                    if (!isFinite(tid)) return;
                    moveTicketToLane(tid, laneId, body);
                } else {
                    moveExternalTicketToLane(ticketId, laneId, body);
                }
            }

            [section, head, body].forEach(function(el) {
                if (!el) return;
                el.addEventListener("dragenter", handleDragOver);
                el.addEventListener("dragover", handleDragOver);
                el.addEventListener("dragleave", handleDragLeave);
                el.addEventListener("drop", handleDrop);
            });
        });
    }

    function bindListLaneAccordion(container) {
        container.querySelectorAll(".kb-ticket-list-section-head").forEach(function(head) {
            head.addEventListener("click", function() {
                var section = head.closest(".kb-ticket-list-section");
                if (!section) return;
                var laneId = section.dataset.laneId;
                if (!laneId) return;
                if (section.classList.contains("kb-ticket-list-section--expanded")) {
                    setListLaneExpanded(container, null);
                    saveListExpandedLaneId(null);
                    return;
                }
                setListLaneExpanded(container, laneId);
                saveListExpandedLaneId(laneId);
            });
        });
    }

    function renderTicketList(lanes, isLocal, boardData) {
        var container = document.getElementById("kb-ticket-list");
        container.innerHTML = "";
        var expandedLaneId = getSavedListExpandedLaneId(lanes);
        lanes.forEach(function(lane) {
            var tickets = lane.tickets || [];
            if (window.KanbanTicketUi && window.KanbanTicketUi.compareTicketsForListView) {
                tickets = tickets.slice().sort(window.KanbanTicketUi.compareTicketsForListView);
            }
            var laneId = lane.id != null ? String(lane.id) : "";
            var isExpanded = expandedLaneId && laneId === String(expandedLaneId);
            var section = document.createElement("section");
            section.className = "kb-ticket-list-section" + (isExpanded ? " kb-ticket-list-section--expanded" : "");
            section.dataset.laneId = laneId;
            var boardData = currentBoardData || {};
            var laneCopyBtn = (!isLocal && tickets.length > 0 && ticketActions.laneCopyAllButtonHtml)
                ? ticketActions.laneCopyAllButtonHtml("Copy all in " + (lane.name || "column") + " to local board")
                : "";
            var waLink = laneWhatsappIntakeLink(lane, boardData, isLocal);
            var laneWaBtn = (waLink && ticketActions.laneWhatsappSnapshotButtonHtml)
                ? ticketActions.laneWhatsappSnapshotButtonHtml({
                    boardId: boardData.id,
                    linkId: waLink.id,
                    laneId: lane.id,
                    laneName: lane.name || "",
                    boardName: boardData.name || "",
                    linkPhone: waLink.phone_number || "",
                    tooltip: "Create ticket from linked WhatsApp messages",
                })
                : "";
            section.innerHTML =
                '<div class="kb-ticket-list-section-head-row flex items-center gap-1">' +
                    '<button type="button" class="kb-ticket-list-section-head flex-1 min-w-0" aria-expanded="' + (isExpanded ? "true" : "false") + '">' +
                        listLaneChevronSvg() +
                        '<span class="kb-ticket-list-section-title">' + esc(lane.name) + "</span>" +
                        '<span class="kb-ticket-list-section-count">' + tickets.length + "</span>" +
                    "</button>" +
                    laneCopyBtn +
                    laneWaBtn +
                "</div>";
            bindLaneCopyAllButton(section, lane);
            bindLaneWhatsappSnapshotButton(section);
            var body = document.createElement("div");
            body.className = "kb-ticket-list-section-body space-y-1";
            body.hidden = !isExpanded;
            if (!tickets.length) {
                body.innerHTML = '<div class="px-3 py-2 text-xs text-gray-500 italic border border-dashed border-white/10 rounded">No tickets</div>';
            } else {
                tickets.forEach(function(ticket) {
                    body.appendChild(ticketUi.createTicketListRow(ticket, isLocal, boardData));
                });
            }
            section.appendChild(body);
            container.appendChild(section);
        });
        bindListLaneAccordion(container);
        bindListLaneDropTargets(container, isLocal);
        initVisibleListRowMarquees(container);
    }

    /** Push a local ticket to CLI (with confirmation and spinners). */
    function pushTicketToCli(ticketId, btnEl) {
        showKanbanConfirm({
            title: "Push ticket to CLI",
            message: "Push ticket #" + ticketId + " to the project CLI?",
            confirmLabel: "Push",
            onConfirm: function() {
                if (btnEl) {
                    btnEl.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
                    btnEl.classList.add("text-orange-400");
                    btnEl.disabled = true;
                }
                apiFetch("/api/tickets/tickets/" + ticketId + "/send-to-cli", { method: "POST" })
                    .then(function(r) {
                        showSnackbar(r.message || "Sent to CLI");
                        _pollCliStatus(ticketId, btnEl);
                    })
                    .catch(function(err) {
                        showSnackbar("CLI error: " + err.message, "error");
                        if (btnEl) {
                            btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                            btnEl.classList.remove("text-orange-400");
                            btnEl.disabled = false;
                        }
                    });
            },
        });
    }

    /** Send a local ticket to the project agent route. Auto routing chooses Cursor/Codex by complexity. */
    function sendTicketToAgentById(ticketId, btnEl, opts) {
        opts = opts || {};
        var prevHtml = btnEl ? btnEl.innerHTML : "";
        if (btnEl) {
            btnEl.dataset.prevHtml = prevHtml;
            btnEl.disabled = true;
            btnEl.classList.add("text-orange-400");
            btnEl.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
        }
        var backendId = (opts.backendId || "").trim();
        var payload = {};
        if (backendId) payload.backend_id = backendId;
        showSnackbar(backendId ? ("Sending ticket to " + backendId + "…") : "Sending ticket to agent…", "info");
        return apiFetch("/api/tickets/tickets/" + ticketId + "/send-to-cli", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).then(function(r) {
            var backend = r.backend_id || backendId || "agent";
            showSnackbar(r.message || ("Sent to " + backend));
            _pollCliStatus(ticketId, btnEl || null);
            return r;
        }).catch(function(err) {
            showSnackbar("Agent error: " + err.message, "error");
            throw err;
        }).finally(function() {
            if (btnEl) {
                btnEl.innerHTML = btnEl.dataset.prevHtml || prevHtml;
                btnEl.classList.remove("text-orange-400");
                btnEl.disabled = false;
            }
        });
    }

    /** Send a local ticket to project by ID. */
    function sendTicketToProjectById(ticketId, btnEl) {
        if (btnEl) {
            btnEl.dataset.prevHtml = btnEl.innerHTML;
            btnEl.disabled = true;
            btnEl.classList.add("text-orange-400");
            btnEl.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
        }
        showSnackbar("Sending ticket #" + ticketId + " to project...", "info");
        apiFetch("/api/tickets/tickets/" + ticketId + "/send-to-project", { method: "POST" })
            .then(function(r) {
                showSnackbar(r.message || "Sent to project");
            })
            .catch(function(err) {
                showSnackbar("Error: " + err.message, "error");
            })
            .finally(function() {
                if (btnEl) {
                    btnEl.innerHTML = btnEl.dataset.prevHtml || btnEl.innerHTML;
                    btnEl.classList.remove("text-orange-400");
                    btnEl.disabled = false;
                }
            });
    }

    /** Copy an external ticket to the local board, then optionally send to CLI or project. */
    function copyAndPushExternalTicket(ticket, source, action, selectedWorkflowId, backendOverride, btnEl) {
        if (!dbBoards.length) { showSnackbar("No local boards available", "error"); return; }
        var requiresProject = action === 'cli' || action === 'project' || action === 'agent';
        // Project may be set on the external (Trello/Jira) board config, while tickets are copied
        // onto a source=database board only — local_id points at the external shadow row, not dbBoards.
        var extProjectId = currentBoardData && currentBoardData.default_project_id ? currentBoardData.default_project_id : null;
        // Prefer a database board linked to the same project as the external board config
        var preferredBoard = null;
        if (extProjectId) {
            for (var i = 0; i < dbBoards.length; i++) {
                if (dbBoards[i].default_project_id === extProjectId) {
                    preferredBoard = dbBoards[i];
                    break;
                }
            }
        }
        if (!preferredBoard) preferredBoard = dbBoards[0];

        // Sending to CLI/project needs a project: either on the external board or on the destination local board.
        if (requiresProject) {
            var destProject = preferredBoard && preferredBoard.default_project_id ? preferredBoard.default_project_id : null;
            if (!extProjectId && !destProject) {
                showSnackbar("Link this Jira/Trello board to a project (board settings) or set a default project on a local board before sending to " + (action === 'cli' ? "CLI" : "project"), "error");
                return;
            }
        }

        var effectiveLinkedProject = extProjectId || (preferredBoard && preferredBoard.default_project_id) || null;
        var payload = {
            board_id: preferredBoard.id,
            title: ticket.title,
            description: ticket.description || "",
            priority: ticket.priority || "medium",
            time_estimate: ticket.time_estimate || "",
            time_spent: ticket.time_spent || "",
            external_source: source,
            external_id: String(ticket.id),
            external_url: ticket.url || "",
            auto_send_to_project: action === 'project',
            auto_send_to_workflow: action === 'workflow',
            auto_send_to_cli: action === 'cli',
        };
        if (effectiveLinkedProject) {
            payload.linked_project_id = effectiveLinkedProject;
        }
        if (selectedWorkflowId) {
            payload.linked_workflow_id = selectedWorkflowId;
        }
        commonUtils.mergeSourceChatIntoPayload(payload);

        apiFetch("/api/tickets/tickets/copy-external-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function(r) {
            if ((action === 'cli' || action === 'agent') && r.id) {
                showSnackbar("Ticket copied — sending to agent…");
                sendTicketToAgentById(r.id, btnEl || null, { backendId: backendOverride || "" });
            } else if (action === 'workflow') {
                if (r.workflow_started) {
                    showSnackbar("Ticket copied and sent to workflow");
                } else {
                    showSnackbar("Ticket copied but workflow did not start: " + (r.workflow_error || "unknown error"), "error");
                }
            } else if (action === 'project') {
                showSnackbar(r.sent_to_project ? "Ticket copied & sent to project: " + (r.project_name || "") : "Ticket copied to board");
            } else {
                showSnackbar("Ticket copied to board");
            }
            // Refresh the target board if it's currently displayed
            if (currentBoard && currentBoard.source === "database" && currentBoard.id === preferredBoard.id) {
                selectBoard("database", preferredBoard.id);
            }
        }).catch(function(err) {
            showSnackbar("Error: " + err.message, "error");
        });
    }

    var DISCUSS_PROMPT_MAX_CHARS = 14000;

    /** Keep Ticket Board in sync with Chat tab when sessionStorage was never set (Kanban page does not load chat.js). */
    function persistSourceChatIdForTickets(chatId) {
        if (chatId == null || chatId < 1) return;
        try {
            sessionStorage.setItem("decisions_source_chat_id", String(Number(chatId)));
        } catch (e) { /* ignore */ }
    }

    /** Prefer agent's loaded chat, then last-used from settings, then most recently modified row. */
    function pickChatIdFromChatsListPayload(data) {
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

    /**
     * Resolve which chat should receive ticket-discussion messages.
     * Uses the same source as the Chat tab when available; otherwise GET /api/chats (agent + last + newest);
     * if there are no chats, POST /api/chats to create one so "Let's talk about it" always has a target.
     */
    function resolveChatIdForTicketDiscuss() {
        var direct =
            typeof commonUtils.getSourceChatIdForTickets === "function"
                ? commonUtils.getSourceChatIdForTickets()
                : null;
        if (direct != null && direct >= 1) {
            persistSourceChatIdForTickets(direct);
            return Promise.resolve(direct);
        }
        return apiFetch("/api/chats")
            .then(function (data) {
                var cid = pickChatIdFromChatsListPayload(data);
                if (cid != null) return cid;
                return apiFetch("/api/chats", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                }).then(function (created) {
                    if (!created || created.id == null) {
                        throw new Error("Could not create a chat");
                    }
                    var nc = typeof created.id === "number" ? created.id : parseInt(created.id, 10);
                    if (isNaN(nc) || nc < 1) throw new Error("Invalid chat id from create");
                    return nc;
                });
            })
            .then(function (chatId) {
                persistSourceChatIdForTickets(chatId);
                return chatId;
            });
    }

    /**
     * Build the first user message for a new chat so the agent opens in active ticket-orchestration mode:
     * acknowledge the ticket, inspect linked project context where possible, then speak back with a useful next step.
     */
    function buildTicketDiscussionProjectContext(ticket, boardData) {
        ticket = ticket || {};
        boardData = boardData || {};
        var linkedProjectId = ticket.linked_project_id || null;
        var boardProjectId = boardData.default_project_id || null;
        var effectiveProjectId = linkedProjectId || boardProjectId || null;
        var projectName =
            ticket.linked_project_name ||
            ticket.project_name ||
            boardData.default_project_name ||
            boardData.project_name ||
            "";
        var projectFolder =
            ticket.linked_project_folder ||
            ticket.project_folder ||
            ticket.folder_location ||
            boardData.default_project_folder ||
            boardData.project_folder ||
            boardData.folder_location ||
            "";
        var sourceLabel = linkedProjectId ? "ticket link" : (boardProjectId ? "board default" : "");
        return {
            hasProject: !!effectiveProjectId,
            id: effectiveProjectId,
            name: projectName,
            folder: projectFolder,
            source: sourceLabel,
        };
    }

    function buildTicketDiscussionStartingQuestion(ticket, isLocal, boardLabel, source, boardData) {
        var title = (ticket.title || "").trim() || "(untitled)";
        var descRaw = ticket.description || "";
        var desc = stripHtml(descRaw);
        if (desc.length > DISCUSS_PROMPT_MAX_CHARS) {
            desc = desc.substring(0, DISCUSS_PROMPT_MAX_CHARS) + "\n…[description truncated for chat size]";
        }
        var idPart = isLocal ? ("Local ticket id: " + ticket.id) : ("External id: " + String(ticket.id));
        var urlLine = ticket.url ? "\n- URL: " + ticket.url : "";
        var meta = [];
        if (ticket.time_estimate) meta.push("Estimate: " + ticket.time_estimate);
        if (ticket.time_spent) meta.push("Spent: " + ticket.time_spent);
        if (ticket.priority) meta.push("Priority: " + ticket.priority);
        if (ticket.members && ticket.members.length) meta.push("People: " + ticket.members.join(", "));
        if (ticket.labels && ticket.labels.length) meta.push("Labels: " + ticket.labels.join(", "));
        var metaBlock = meta.length ? ("\n- " + meta.join("\n- ")) : "";
        var projectContext = buildTicketDiscussionProjectContext(ticket, boardData);
        var projectBlock = "";
        if (projectContext.hasProject) {
            projectBlock =
                "\n\n**Linked project**\n" +
                "- Project id: " + projectContext.id +
                (projectContext.name ? "\n- Project name: " + projectContext.name : "") +
                (projectContext.folder ? "\n- Project folder: " + projectContext.folder : "") +
                (projectContext.source ? "\n- Link source: " + projectContext.source : "");
        } else {
            projectBlock =
                "\n\n**Linked project**\n" +
                "- None visible on the ticket or board. If project context matters, ask which project to use.";
        }
        var todosBlock = "";
        if (ticket.todos && ticket.todos.length) {
            todosBlock =
                "\n**Checklist / subtasks**\n" +
                ticket.todos
                    .map(function(t) {
                        var mark = t.done ? "[x]" : "[ ]";
                        return mark + " " + (t.text || "");
                    })
                    .join("\n");
        }
        var descNote = "";
        if (!desc && descRaw && String(descRaw).trim()) {
            descNote =
                "\n\n*(Jira returned a non-empty description in HTML/ADF that is not expanded to plain text here — open the issue URL above for the full body, images, and acceptance details.)*";
        }
        var orchestratorHint = !isLocal
            ? "\n\n**Orchestrator instruction:** This ticket is shown on an **external** board (Jira/Trello). The context "
                + "for this turn is **fully in this user message** unless you see a separate 'Local ticket id'. Do **not** "
                + "call the ticket-board tool (`create_ticket` with action `discuss_ticket` or `get_ticket`) using the "
                + "Jira key to reload the issue; there may be no local `KanbanTicket` row until the user uses "
                + "**Copy to local board**. Answer from this message and the URL; suggest copying to the board only "
                + "if they need send-to-project or a local ticket id."
            : "";
        return (
            "[Ticket Board — orchestrator engage this ticket]\n" +
            "The user clicked **Send to Orchestrator**. Treat this as the start of a real, spoken conversation about this exact ticket and its linked project.\n" +
            "Your first reply should be natural and TTS-friendly, not a markdown dump. Start by saying what you see in the ticket title/description, then say what you think the project needs next.\n" +
            "If a linked project is provided below, use the available project/local-context tools to resolve the project, confirm whether a local folder exists, and inspect the project context before giving the user a useful answer. Mention what you found in plain English.\n" +
            "If Cursor/Codex/IDE session tools are available, check whether this project appears available there, but do not start a CLI/backend run, create files, edit the board, or create a workflow unless the user asks for that.\n" +
            "End with one concrete suggested next step and one focused question or offer to proceed. Do not ask 3-5 generic questions.\n\n" +
            "**Context** — Source: " + source + " · Board: " + (boardLabel || "(unknown)") + " · " + idPart + urlLine + metaBlock + "\n\n" +
            "**Title**\n" + title + "\n" +
            todosBlock +
            "\n**Description**\n" + (desc || "(none)") +
            projectBlock +
            descNote +
            orchestratorHint
        );
    }

    var _ticketDiscussInFlight = false;

    /**
     * Send ticket context into the current ticket chat and open that chat so the reply is visible.
     * Agent should respond in voice if speak is true.
     */
    function startTicketDiscussion(ticket, isLocal) {
        if (!ticket) {
            showSnackbar("No ticket to discuss", "error");
            return;
        }
        if (_ticketDiscussInFlight) {
            return;
        }
        var src = currentBoard && currentBoard.source ? currentBoard.source : "database";
        var boardLabel = (currentBoardData && currentBoardData.name) ? currentBoardData.name : "";
        var localBoardId = null;
        if (currentBoard) {
            if (src === "database" && currentBoard.id != null) {
                localBoardId = currentBoard.id;
            } else if (currentBoardData && currentBoardData.local_id) {
                localBoardId = currentBoardData.local_id;
            } else if (currentBoard._localId) {
                localBoardId = currentBoard._localId;
            }
        }
        _ticketDiscussInFlight = true;
        showSnackbar("Sending ticket to the orchestrator…", "info");
        resolveChatIdForTicketDiscuss()
            .then(function (chatId) {
                return apiFetch("/api/tickets/tickets/engage-orchestrator", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        chat_id: chatId,
                        ticket: ticket,
                        is_local: !!isLocal,
                        local_board_id: localBoardId,
                        source: src,
                        board_name: boardLabel,
                    }),
                });
            })
            .then(function (res) {
                var brief = (res && res.display_message) ? res.display_message : "Ticket sent to the orchestrator";
                showSnackbar(brief, "success");
            })
            .catch(function (e) {
                showSnackbar("Could not reach the agent: " + (e && e.message ? e.message : String(e)), "error");
            })
            .finally(function () {
                _ticketDiscussInFlight = false;
            });
    }

    function getModalTicketSnapshotForDiscuss() {
        var title = (document.getElementById("kb-modal-ticket-title").value || "").trim();
        var descArea = document.getElementById("kb-modal-ticket-desc");
        var description = descArea ? descArea.value : "";
        var estEl = document.getElementById("kb-modal-ticket-estimate");
        var durEl = document.getElementById("kb-modal-ticket-duration");
        var projectSelect = document.getElementById("kb-modal-link-project");
        var linkedProjectId = projectSelect ? (parseInt(projectSelect.value, 10) || null) : null;
        var linkedProjectName = "";
        if (projectSelect && projectSelect.selectedIndex >= 0) {
            var selectedProjectOption = projectSelect.options[projectSelect.selectedIndex];
            linkedProjectName = selectedProjectOption ? (selectedProjectOption.text || "").trim() : "";
        }
        return {
            id: modalTicketId,
            title: title,
            description: description,
            time_estimate: estEl ? estEl.value : "",
            time_spent: durEl ? durEl.value : "",
            priority: getSelectedPriority(),
            complexity: getTicketComplexity(),
            context_notes: (document.getElementById("kb-modal-context-notes").value || "").trim(),
            linked_project_id: linkedProjectId,
            linked_project_name: linkedProjectId ? linkedProjectName : "",
            url: ""
        };
    }

    /** Open a detail modal for an external (Trello/Jira) ticket. */
    function openExternalTicketModal(ticket, source) {
        return ticketUi.openExternalTicketModal(ticket, source);
    }

    /** Render media attachments for external tickets. */
    function renderExternalMedia(media, source) {
        return ticketUi.renderExternalMedia(media, source);
    }

    /** Render todos from external sources (read-only). */
    function renderExternalTodos(todos) {
        return ticketUi.renderExternalTodos(todos);
    }

    function _pollCliStatus(ticketId, btnEl) {
        // Poll the workflow audit log for the CLI session to complete
        var attempts = 0;
        var maxAttempts = 120; // 10 minutes at 5s intervals
        var interval = setInterval(function() {
            attempts++;
            if (attempts > maxAttempts) {
                clearInterval(interval);
                showSnackbar("CLI still running — check the audit log", "warning");
                if (btnEl) {
                    btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                    btnEl.classList.remove("text-orange-400");
                    btnEl.disabled = false;
                }
                return;
            }
            apiFetch("/api/workflows?type=pi_agent&limit=1&search=Ticket%20%23" + ticketId)
                .then(function(sessions) {
                    if (sessions && sessions.length > 0) {
                        var s = sessions[0];
                        if (s.status === "completed" || s.status === "failed") {
                            clearInterval(interval);
                            var icon = s.status === "completed"
                                ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
                                : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
                            if (btnEl) {
                                btnEl.innerHTML = icon;
                                btnEl.classList.remove("text-orange-400");
                                btnEl.disabled = false;
                            }
                            showSnackbar("CLI " + s.status + " for ticket #" + ticketId, s.status === "completed" ? "success" : "error");
                            // Reset icon after 5 seconds
                            setTimeout(function() {
                                if (btnEl) {
                                    btnEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
                                }
                            }, 5000);
                        }
                    }
                })
                .catch(function() {}); // silently ignore poll errors
        }, 5000);
    }

    function createTicketCard(ticket, isLocal, boardData) {
        return ticketUi.createTicketCard(ticket, isLocal, boardData);
    }

    // ── Drag & drop move ──
    /**
     * 0-based index where the ticket should land in the target lane, from pointer Y.
     * Skips the dragged card in the same lane so reordering within a lane is not always "append".
     */
    function computeTicketDropPosition(bodyEl, ticketId, clientY) {
        var cards = Array.prototype.slice.call(bodyEl.querySelectorAll(".kb-card, .kb-ticket-list-row"));
        var dragEl = null;
        for (var i = 0; i < cards.length; i++) {
            if (cards[i].dataset.ticketId === String(ticketId)) {
                dragEl = cards[i];
                break;
            }
        }
        var pos = 0;
        for (var j = 0; j < cards.length; j++) {
            var c = cards[j];
            if (c === dragEl) continue;
            var r = c.getBoundingClientRect();
            if (clientY < r.top + r.height / 2) break;
            pos++;
        }
        return pos;
    }

    function moveTicket(ticketId, laneId, bodyEl, clientY) {
        var position = typeof clientY === "number"
            ? computeTicketDropPosition(bodyEl, ticketId, clientY)
            : bodyEl.querySelectorAll(".kb-card, .kb-ticket-list-row").length;
        apiFetch("/api/tickets/tickets/" + ticketId + "/move", {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: laneId, position: position })
        }).then(function() { selectBoard("database", currentBoard.id); })
        .catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    /** Move a Trello/Jira ticket on the remote board (list or column), then refresh the board. */
    function moveExternalTicket(ticketId, targetLaneId, bodyEl, clientY) {
        var src = currentBoard && currentBoard.source;
        if (src !== "trello" && src !== "jira") return;
        var bid = currentBoard.id;
        var position = typeof clientY === "number"
            ? computeTicketDropPosition(bodyEl, String(ticketId), clientY)
            : bodyEl.querySelectorAll(".kb-card, .kb-ticket-list-row").length;
        apiFetch("/api/tickets/external-boards/" + src + "/" + encodeURIComponent(bid) + "/move-ticket", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ticket_id: String(ticketId),
                target_lane_id: String(targetLaneId),
                position: position
            })
        }).then(function() {
            selectBoard(src, bid, currentBoard.extUrl || "");
        }).catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    // ── Ticket modal ──

    function renderModalContextNotes(notes) {
        var el = document.getElementById("kb-modal-context-notes");
        if (!el) return;
        var text = (notes || "").trim();
        el.value = text;
    }

    function ticketSourceLinkMarkup(label) {
        return '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAi0lEQVR42u3WQQqDQBAAwXlFPprXe0quQVRyCGjsKvDoQo/u6gwAAMCJlufj9YsrP4T8m5DfDvkzIX8wGoAt4BD0GfQj5Ff45vFH9yTij+7NxO+tkYrfWisXv14zGf+5djY+/eTFixcvXrx48eLFixcvXvzVBjB3lY7/ZghTkY7fGsJUpeMBAAD+zRvbrtesCjwpyAAAAABJRU5ErkJggg==" alt="" aria-hidden="true">';
    }

    function setTicketModalSourceUrl(url, label) {
        var link = document.getElementById("kb-modal-url-link");
        if (!link) return;
        var value = (url || "").trim();
        if (!value) {
            link.classList.add("hidden");
            link.removeAttribute("href");
            link.innerHTML = "";
            return;
        }
        var sourceLabel = label || "Open source";
        link.href = value;
        link.innerHTML = ticketSourceLinkMarkup(sourceLabel);
        link.title = sourceLabel;
        link.setAttribute("aria-label", sourceLabel);
        link.classList.remove("hidden");
    }

    function preferredLocalBoardForExternalCache() {
        var extProjectId = currentBoardData && currentBoardData.default_project_id ? currentBoardData.default_project_id : null;
        if (extProjectId) {
            for (var i = 0; i < dbBoards.length; i++) {
                if (dbBoards[i].default_project_id === extProjectId) return dbBoards[i];
            }
        }
        return dbBoards.length ? dbBoards[0] : null;
    }

    function persistExternalTicketLocalCopy() {
        var ticket = window._extTicketData || null;
        var source = window._extTicketSource || (currentBoard && currentBoard.source) || "";
        var board = preferredLocalBoardForExternalCache();
        if (!ticket || !source) {
            return Promise.reject(new Error("No external ticket loaded"));
        }
        if (!board || !board.id) {
            return Promise.reject(new Error("No local board available for the ticket cache"));
        }
        var payload = {
            board_id: board.id,
            title: ticket.title || "Untitled ticket",
            description: ticket.description || "",
            priority: getSelectedPriority(),
            complexity: getTicketComplexity(),
            time_estimate: ticket.time_estimate || "",
            time_spent: ticket.time_spent || "",
            external_source: source,
            external_id: String(ticket.id || ticket.external_id || ""),
            external_url: ticket.url || ticket.external_url || "",
            media: Array.isArray(ticket.media) ? ticket.media : [],
            todos: Array.isArray(ticket.todos) ? ticket.todos : [],
        };
        if (currentBoardData && currentBoardData.default_project_id) {
            payload.linked_project_id = currentBoardData.default_project_id;
        }
        commonUtils.mergeSourceChatIntoPayload(payload);
        return apiFetch("/api/tickets/tickets/copy-external-to-board", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).then(function(res) {
            if (!res || !res.id) throw new Error("Local ticket cache was not created");
            modalTicketId = res.id;
            return res.id;
        });
    }

    function openTicketModal(ticketId) {
        modalTicketId = ticketId;
        modalTicketSourceChatId = null;
        kanbanTicketModalDetailsHeight = 0;
        window._extTicketData = null;
        window._extTicketSource = null;
        switchTicketTab("details");
        // Reset modal to local-ticket mode
        resetTicketModalForLocal();
        apiFetch("/api/tickets/tickets/" + ticketId).then(function(t) {
            document.getElementById("kb-modal-ticket-title").value = t.title || "";
            document.getElementById("kb-modal-ticket-desc").value = t.description || "";
            document.getElementById("kb-modal-ticket-estimate").value = t.time_estimate || "";
            document.getElementById("kb-modal-ticket-duration").value = t.time_spent || "";
            setTicketComplexity(t.complexity || "medium");
            renderTicketSourceMeta(t);
            setPriorityButtons(t.priority || "medium");
            renderModalLinks(t.links || []);
            renderModalFiles(t.files || []);
            renderModalTodos(t.todos || []);
            renderModalContextNotes(t.context_notes || "");
            var providerLabel = t.source_provider || t.external_source || "";
            setTicketModalSourceUrl(t.source_url || t.external_url || "", providerLabel ? ("Go to " + providerLabel.charAt(0).toUpperCase() + providerLabel.slice(1)) : "Open source");
            modalTicketSourceChatId = t.source_chat_id != null ? t.source_chat_id : null;
            document.getElementById("kb-ticket-modal").classList.remove("hidden");
            requestAnimationFrame(function () { syncKanbanTicketModalHeights(); });
        }).catch(function(e) { showSnackbar("Failed to load ticket: " + e.message, "error"); });
    }

    function loadModalAuditReport(ticketId, fallbackEntries) {
        apiFetch("/api/tickets/tickets/" + ticketId + "/audit-report")
            .then(function(report) {
                renderModalAuditEntries(report.entries || []);
                renderModalAuditSummary(report);
                renderModalAuditRuns(report.runs || []);
            })
            .catch(function() {
                renderModalAuditEntries(fallbackEntries || []);
                renderModalAuditSummary({ total_entries: (fallbackEntries || []).length, runs: [] });
                renderModalAuditRuns([]);
            });
    }

    function clearModalAuditReport() {
        if (!modalTicketId) return;
        showKanbanConfirm({
            title: "Clear report?",
            message: "This will remove all report and audit history entries for this ticket.",
            confirmLabel: "Clear Report",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                apiFetch("/api/tickets/tickets/" + modalTicketId + "/audit-report", { method: "DELETE" })
                    .then(function(res) {
                        var deleted = Number((res && res.deleted_entries) || 0);
                        showSnackbar("Report cleared" + (deleted ? " (" + deleted + " entries)" : ""));
                        loadModalAuditReport(modalTicketId, []);
                    })
                    .catch(function(e) {
                        showSnackbar("Failed to clear report: " + e.message, "error");
                    });
            }
        });
    }

    function renderModalAuditSummary(report) {
        var container = document.getElementById("kb-modal-audit-summary");
        if (!container) return;
        var runs = Array.isArray(report && report.runs) ? report.runs : [];
        var totalRuns = runs.length;
        var totalEntries = Number(report && report.total_entries) || 0;
        var totalSeconds = 0;
        runs.forEach(function(r) { totalSeconds += Number(r.total_duration_seconds || 0); });
        container.innerHTML =
            '<div class="p-2 bg-[#152054] border border-white/10 rounded"><div class="text-[11px] text-gray-400">Runs</div><div class="text-sm text-white">' + esc(String(totalRuns)) + "</div></div>" +
            '<div class="p-2 bg-[#152054] border border-white/10 rounded"><div class="text-[11px] text-gray-400">Audit Entries</div><div class="text-sm text-white">' + esc(String(totalEntries)) + "</div></div>" +
            '<div class="p-2 bg-[#152054] border border-white/10 rounded"><div class="text-[11px] text-gray-400">Total Runtime</div><div class="text-sm text-white">' + esc(formatDuration(totalSeconds)) + "</div></div>";
    }

    function renderModalAuditRuns(runs) {
        var container = document.getElementById("kb-modal-audit-runs");
        if (!container) return;
        var list = Array.isArray(runs) ? runs : [];
        if (!list.length) {
            container.innerHTML = '<div class="text-xs text-gray-500">No run report yet.</div>';
            return;
        }
        container.innerHTML = "";
        list.forEach(function(run) {
            var row = document.createElement("div");
            row.className = "p-2 bg-[#152054] border border-white/10 rounded space-y-2";
            var runId = run.run_id == null ? "n/a" : String(run.run_id);
            var total = Number(run.total_duration_seconds || 0);
            var steps = Array.isArray(run.step_breakdown) ? run.step_breakdown : [];
            var maxStepSeconds = 1;
            steps.forEach(function(s) { maxStepSeconds = Math.max(maxStepSeconds, Number(s.total_seconds || 0)); });
            var timelineHtml = steps.map(function(s) {
                var sec = Number(s.total_seconds || 0);
                var width = Math.max(8, Math.round((sec / maxStepSeconds) * 100));
                return '<div class="flex items-center gap-2">' +
                    '<div class="w-16 text-[11px] text-gray-400">Step ' + esc(String(s.step_id)) + '</div>' +
                    '<div class="flex-1 h-2 bg-[#0c153f] rounded overflow-hidden"><div class="h-2 bg-[#f97316]" style="width:' + esc(String(width)) + '%"></div></div>' +
                    '<div class="w-28 text-[11px] text-gray-300 text-right">' + esc(formatDuration(sec)) + "</div>" +
                    "</div>";
            }).join("");
            var tableRows = steps.map(function(s) {
                return "<tr>" +
                    '<td class="py-1 pr-3">Step ' + esc(String(s.step_id)) + "</td>" +
                    '<td class="py-1 pr-3">' + esc(String(s.attempts || 0)) + "</td>" +
                    '<td class="py-1 pr-3">' + esc(formatDuration(Number(s.total_seconds || 0))) + "</td>" +
                    '<td class="py-1">' + esc(formatDuration(Number(s.wait_seconds || 0))) + "</td>" +
                    "</tr>";
            }).join("");
            row.innerHTML =
                '<div class="flex items-center justify-between"><div class="text-xs text-white">Run #' + esc(runId) + '</div><div class="text-[11px] text-gray-400">' + esc(run.started_at || "") + " -> " + esc(run.finished_at || "") + "</div></div>" +
                '<div class="text-[11px] text-gray-400">Total runtime: ' + esc(formatDuration(total)) + "</div>" +
                '<div class="space-y-1">' + (timelineHtml || '<div class="text-[11px] text-gray-500">No step timing data.</div>') + "</div>" +
                '<table class="w-full text-[11px] text-gray-300"><thead><tr class="text-gray-500"><th class="text-left py-1 pr-3">Step</th><th class="text-left py-1 pr-3">Attempts</th><th class="text-left py-1 pr-3">Run Time</th><th class="text-left py-1">Wait Time</th></tr></thead><tbody>' + tableRows + "</tbody></table>";
            container.appendChild(row);
        });
    }

    function formatDuration(totalSeconds) {
        var sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
        var h = Math.floor(sec / 3600);
        var m = Math.floor((sec % 3600) / 60);
        var s = sec % 60;
        if (h > 0) return h + "h " + m + "m " + s + "s";
        if (m > 0) return m + "m " + s + "s";
        return s + "s";
    }

    function renderModalAuditEntries(entries) {
        var container = document.getElementById("kb-modal-audit-entries");
        if (!container) return;
        var rows = Array.isArray(entries) ? entries : [];
        if (!rows.length) {
            container.innerHTML = '<div class="text-xs text-gray-500">No audit entries yet.</div>';
            return;
        }
        container.innerHTML = "";
        rows.forEach(function(entry) {
            var row = document.createElement("div");
            row.className = "p-2 bg-[#152054] border border-white/10 rounded";
            var status = (entry.status || "pending").toString();
            var lane = (entry.execution_lane || "cursor").toString();
            var stamp = (entry.created_date || "").toString();
            var summary = (entry.summary || "").toString();
            var details = (entry.details || "").toString();
            var meta = "lane: " + esc(lane) + " | status: " + esc(status);
            if (entry.step_id != null) meta += " | step: " + esc(String(entry.step_id));
            if (entry.run_id != null) meta += " | run: " + esc(String(entry.run_id));
            row.innerHTML =
                '<div class="text-[11px] text-gray-400">' + esc(stamp || "unknown time") + "</div>" +
                '<div class="text-xs text-gray-200 mt-0.5">' + esc(summary || "(no summary)") + "</div>" +
                '<div class="text-[11px] text-gray-500 mt-0.5">' + meta + "</div>" +
                (details ? '<div class="text-[11px] text-gray-400 mt-1 whitespace-pre-wrap">' + esc(details) + "</div>" : "");
            container.appendChild(row);
        });
    }

    function closeTicketModal() {
        document.getElementById("kb-ticket-modal").classList.add("hidden");
        modalTicketId = null;
        modalTicketSourceChatId = null;
        modalTicketReadOnlyDetails = false;
        window._extTicketData = null;
        window._extTicketSource = null;
        var descArea = document.getElementById("kb-modal-ticket-desc");
        var richDiv = descArea.parentElement.querySelector(".kb-ext-rich-desc");
        if (richDiv) richDiv.remove();
        descArea.style.display = "";
        resetTicketModalForLocal();
    }

    /** Reset modal UI back to local-ticket (editable) mode. */
    function resetTicketModalForLocal() {
        var titleEl = document.getElementById("kb-modal-ticket-title");
        var descArea = document.getElementById("kb-modal-ticket-desc");
        var estimateInput = document.getElementById("kb-modal-ticket-estimate");
        var durationInput = document.getElementById("kb-modal-ticket-duration");
        var complexitySelect = document.getElementById("kb-modal-ticket-complexity");
        if (titleEl) {
            titleEl.readOnly = false;
            titleEl.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        }
        descArea.readOnly = false;
        descArea.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        descArea.style.display = "";
        if (estimateInput) {
            estimateInput.readOnly = false;
            estimateInput.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        }
        if (durationInput) {
            durationInput.readOnly = false;
            durationInput.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        }
        if (complexitySelect) {
            complexitySelect.value = "auto";
            complexitySelect.disabled = false;
            complexitySelect.classList.remove("opacity-50", "cursor-not-allowed");
        }
        var notesEl = document.getElementById("kb-modal-context-notes");
        if (notesEl) {
            notesEl.readOnly = false;
            notesEl.classList.remove("bg-[#152054]/50", "cursor-not-allowed");
        }
        modalTicketReadOnlyDetails = false;
        renderTicketSourceMeta(null);
        var richDiv = descArea.parentElement.querySelector(".kb-ext-rich-desc");
        if (richDiv) richDiv.remove();
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.classList.remove("opacity-50", "cursor-not-allowed");
            btn.disabled = false;
        });
        var modalFooter = document.getElementById("kb-modal-footer");
        if (modalFooter) modalFooter.classList.remove("hidden");
        var modalActions = document.getElementById("kb-modal-actions");
        if (modalActions) modalActions.classList.remove("hidden");
        setTicketModalSourceUrl("");
        // Clear todos input
        var todoInput = document.getElementById("kb-modal-todo-input");
        if (todoInput) todoInput.readOnly = false;
    }

    function ticketMetaField(label, valueHtml) {
        return '<div class="kb-ticket-meta-field"><div class="kb-ticket-meta-label">' + esc(label) +
            '</div><div class="kb-ticket-meta-value">' + valueHtml + "</div></div>";
    }

    function setPriorityButtons(pri) {
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            var isActive = btn.dataset.pri === pri;
            btn.className = "px-3 py-1.5 rounded text-xs font-medium border " +
                (isActive ? "border-[#f97316] bg-[#f97316] text-white" : "border-white/20 text-gray-400");
        });
    }

    function getSelectedPriority() {
        var active = document.querySelector("#kb-modal-priority-btns button.bg-\\[\\#f97316\\]");
        return active ? active.dataset.pri : "medium";
    }

    function setTicketComplexity(value) {
        var select = document.getElementById("kb-modal-ticket-complexity");
        if (!select) return;
        var normalized = ["auto", "low", "medium", "high"].indexOf((value || "").toLowerCase()) >= 0 ? value.toLowerCase() : "auto";
        select.value = normalized;
    }

    function getTicketComplexity() {
        var select = document.getElementById("kb-modal-ticket-complexity");
        return select && select.value ? select.value : "auto";
    }

    function renderTicketSourceMeta(ticket) {
        var box = document.getElementById("kb-modal-source-meta");
        var body = document.getElementById("kb-modal-source-meta-body");
        if (!box || !body) return;
        var source = ticket && (ticket.source_provider || ticket.external_source || (ticket.whatsapp_message_id ? "whatsapp" : ""));
        if (!source) {
            box.classList.add("hidden");
            body.innerHTML = "";
            return;
        }
        var rows = [];
        rows.push(ticketMetaField("Provider", esc(source)));
        if (ticket.source_contact) rows.push(ticketMetaField("Contact", esc(ticket.source_contact)));
        if (ticket.source_external_id || ticket.external_id || ticket.whatsapp_message_wa_id) {
            rows.push(ticketMetaField("ID", esc(ticket.source_external_id || ticket.external_id || ticket.whatsapp_message_wa_id)));
        }
        if (ticket.source_thread_id) rows.push(ticketMetaField("Thread", esc(ticket.source_thread_id)));
        var url = ticket.source_url || ticket.external_url || "";
        if (url) {
            rows.push(ticketMetaField(
                "Source",
                '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" class="text-[#f97316] hover:underline">Open source</a>'
            ));
        }
        body.innerHTML = rows.join("");
        box.classList.remove("hidden");
    }

    var isValidTimeTrackingValue = commonUtils.isValidTimeTrackingValue;

    function saveTicket() {
        var estimate = document.getElementById("kb-modal-ticket-estimate").value.trim();
        var duration = document.getElementById("kb-modal-ticket-duration").value.trim();
        if (!isValidTimeTrackingValue(estimate)) {
            showSnackbar("Invalid Initial Estimate format. Use values like 30m, 2h, 1d 3h.", "error");
            return;
        }
        if (!isValidTimeTrackingValue(duration)) {
            showSnackbar("Invalid Actual Duration format. Use values like 30m, 2h, 1d 3h.", "error");
            return;
        }
        var payload = {
            priority: getSelectedPriority(),
            complexity: getTicketComplexity(),
            linked_snippet_id: null,
            linked_action_id: null,
            context_notes: (document.getElementById("kb-modal-context-notes").value || "").trim(),
        };
        if (!modalTicketReadOnlyDetails) {
            payload.title = document.getElementById("kb-modal-ticket-title").value.trim();
            payload.description = document.getElementById("kb-modal-ticket-desc").value.trim();
            payload.time_estimate = estimate;
            payload.time_spent = duration;
        }
        if (modalTicketSourceChatId == null && typeof commonUtils.getSourceChatIdForTickets === "function") {
            var linkCid = commonUtils.getSourceChatIdForTickets();
            if (linkCid != null) payload.source_chat_id = linkCid;
        }
        if (!modalTicketReadOnlyDetails && !payload.title) { showSnackbar("Title is required", "error"); return; }
        var wasExternalOnly = !modalTicketId && window._extTicketData;
        var ensureLocal = modalTicketId ? Promise.resolve(modalTicketId) : persistExternalTicketLocalCopy();
        ensureLocal.then(function(ticketId) {
            return apiFetch("/api/tickets/tickets/" + ticketId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
        }).then(function() {
            showSnackbar(wasExternalOnly ? "Ticket cached locally and saved" : "Ticket saved");
            if (!modalTicketReadOnlyDetails) {
                closeTicketModal();
                selectBoard("database", currentBoard.id);
            }
        }).catch(function(e) { showSnackbar("Save failed: " + e.message, "error"); });
    }

    function deleteTicket() {
        if (!modalTicketId) return;
        var tid = modalTicketId;
        showKanbanConfirm({
            title: "Delete local copy",
            message: "Delete the local DecisionsAI copy/cache of this ticket? This will not delete the Jira or Trello source item.",
            confirmLabel: "Delete local copy",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                apiFetch("/api/tickets/tickets/" + tid, { method: "DELETE" }).then(function() {
                    showSnackbar("Ticket deleted");
                    closeTicketModal();
                    reloadCurrentDatabaseBoard();
                }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
            }
        });
    }

    function sendTicketToProject() {
        if (!modalTicketId) return;
        apiFetch("/api/tickets/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
            .then(function(data) {
                showSnackbar("Ticket sent to project: " + (data.project_name || ""));
            })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    function resolveWorkflowModalDefault(ticket) {
        var boardWorkflowId = currentBoardData && currentBoardData.default_workflow_id ? currentBoardData.default_workflow_id : null;
        var ticketWorkflowId = ticket && ticket.linked_workflow_id ? ticket.linked_workflow_id : null;
        if (boardWorkflowId) return { selectedWorkflowId: boardWorkflowId, hint: "Board default workflow is preselected." };
        if (ticketWorkflowId) return { selectedWorkflowId: ticketWorkflowId, hint: "Ticket-linked workflow is preselected." };
        return { selectedWorkflowId: null, hint: "No workflow linked yet. Pick one below." };
    }

    function closeSendWorkflowModal() {
        var modal = document.getElementById("kb-send-workflow-modal");
        var confirmBtn = document.getElementById("kb-send-workflow-confirm");
        var hintEl = document.getElementById("kb-send-workflow-hint");
        if (modal) modal.classList.add("hidden");
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.classList.remove("opacity-50", "cursor-not-allowed");
            confirmBtn.textContent = "Send";
        }
        if (hintEl && sendWorkflowContext && sendWorkflowContext.defaultHint) {
            hintEl.textContent = sendWorkflowContext.defaultHint;
        }
        sendWorkflowContext = null;
    }

    function confirmSendWorkflowModal() {
        var sel = document.getElementById("kb-send-workflow-select");
        var confirmBtn = document.getElementById("kb-send-workflow-confirm");
        var hintEl = document.getElementById("kb-send-workflow-hint");
        var workflowId = parseInt(sel.value, 10) || null;
        if (!workflowId) {
            showSnackbar("Please select a workflow first", "error");
            return;
        }
        if (!sendWorkflowContext) return;
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.classList.add("opacity-50", "cursor-not-allowed");
            confirmBtn.textContent = "Sending...";
        }
        if (hintEl) {
            hintEl.textContent = "Dispatching ticket to workflow...";
        }
        var targetTicketId = sendWorkflowContext.ticket && sendWorkflowContext.ticket.id ? sendWorkflowContext.ticket.id : null;
        if (sendWorkflowContext.isLocal) {
            showSnackbar("Sending ticket to workflow...");
            apiFetch("/api/tickets/tickets/" + sendWorkflowContext.ticket.id + "/send-to-workflow", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ workflow_id: workflowId }),
            }).then(function(resp) {
                if (hintEl) hintEl.textContent = "Ticket sent. Workflow is now running.";
                showSnackbar(resp.message || "Ticket sent to workflow");
                if (targetTicketId) {
                    setTicketWorkflowStatusOnCard(targetTicketId, "running");
                }
                closeSendWorkflowModal();
            }).catch(function(e) {
                if (confirmBtn) {
                    confirmBtn.disabled = false;
                    confirmBtn.classList.remove("opacity-50", "cursor-not-allowed");
                    confirmBtn.textContent = "Send";
                }
                if (hintEl && sendWorkflowContext && sendWorkflowContext.defaultHint) {
                    hintEl.textContent = sendWorkflowContext.defaultHint;
                }
                showSnackbar("Workflow dispatch failed: " + e.message, "error");
            });
            return;
        }
        copyAndPushExternalTicket(sendWorkflowContext.ticket, sendWorkflowContext.source, "workflow", workflowId);
        closeSendWorkflowModal();
    }

    // ── Workflow run popover ──────────────────────────────────────────

    var _runPopover = null;

    function _closeRunPopover() {
        if (_runPopover) {
            _runPopover.remove();
            _runPopover = null;
        }
    }

    // Global click-outside to close popover.
    document.addEventListener("click", function(e) {
        if (_runPopover && !_runPopover.contains(e.target) && !e.target.closest(".kb-wf-status-badge")) {
            _closeRunPopover();
        }
    }, true);

    function showRunPopover(badgeEl, ticketId) {
        _closeRunPopover();
        apiFetch("/api/tickets/tickets/" + ticketId + "/active-run").then(function(data) {
            if (!data || !data.active) {
                showSnackbar("No active run found for this ticket");
                return;
            }
            var pop = document.createElement("div");
            pop.className = "kb-run-popover fixed z-50 bg-[#1a2550] border border-white/20 rounded-lg shadow-xl p-3 text-xs";
            pop.style.minWidth = "220px";
            pop.style.maxWidth = "300px";

            var statusCls = data.status === "waiting" ? "text-amber-300" : "text-sky-300";
            var stepLine = data.current_step_name
                ? '<div class="text-gray-400 mt-1">Step: <span class="text-gray-200">' + esc(data.current_step_name) + "</span></div>"
                : "";
            var phaseLine = data.phase
                ? '<div class="text-gray-400">Phase: <span class="text-gray-200">' + esc(data.phase) + "</span></div>"
                : "";
            var wfName = data.workflow_name || ("Workflow #" + data.workflow_id);

            pop.innerHTML =
                '<div class="flex items-center justify-between gap-2 mb-2">' +
                    '<span class="font-medium text-white truncate">' + esc(wfName) + "</span>" +
                    '<span class="' + statusCls + ' font-medium shrink-0">' + esc(data.status) + "</span>" +
                "</div>" +
                stepLine + phaseLine +
                '<div class="flex items-center gap-2 mt-3">' +
                    '<button class="kb-run-pop-cancel flex-1 py-1 rounded border border-red-500/60 text-red-400 hover:bg-red-500/20 transition-colors">Stop Run</button>' +
                    '<a class="kb-run-pop-view flex-1 text-center py-1 rounded border border-white/20 text-gray-300 hover:bg-white/10 transition-colors" href="/workflows/" target="_blank">View →</a>' +
                "</div>";

            pop.querySelector(".kb-run-pop-cancel").addEventListener("click", function() {
                _closeRunPopover();
                cancelTicketRun(data.run_id, data.workflow_id, ticketId);
            });

            // Set localStorage so the workflows page auto-selects this workflow.
            pop.querySelector(".kb-run-pop-view").addEventListener("click", function() {
                try { localStorage.setItem("wf_last_selected", String(data.workflow_id)); } catch(e) {}
            });

            document.body.appendChild(pop);
            _runPopover = pop;

            // Position below the badge, clamp to viewport.
            var rect = badgeEl.getBoundingClientRect();
            var top = rect.bottom + 6;
            var left = rect.left;
            var popW = 240;
            if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8;
            if (top + 140 > window.innerHeight - 8) top = rect.top - 140 - 6;
            pop.style.top = top + "px";
            pop.style.left = left + "px";
        }).catch(function(e) {
            showSnackbar("Could not load run info: " + e.message, "error");
        });
    }

    function cancelTicketRun(runId, workflowId, ticketId) {
        showKanbanConfirm({
            title: "Stop workflow run?",
            message: "The workflow will be cancelled and can be restarted from the ticket.",
            confirmLabel: "Stop Run",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                apiFetch("/api/workflows/" + workflowId + "/cancel-run/" + runId, { method: "POST" })
                    .then(function() {
                        showSnackbar("Workflow run stopped");
                        setTicketWorkflowStatusOnCard(ticketId, "cancelled");
                    })
                    .catch(function(e) {
                        showSnackbar("Could not stop run: " + e.message, "error");
                    });
            },
        });
    }

    function setTicketWorkflowStatusOnCard(ticketId, workflowStatus) {
        if (!ticketId) return;
        var card = document.querySelector('.kb-card[data-ticket-id="' + String(ticketId) + '"]');
        if (!card) return;
        var status = String(workflowStatus || "").toLowerCase();
        var badge = card.querySelector(".kb-wf-status-badge");
        if (!status) {
            if (badge) badge.remove();
            return;
        }
        var isActive = status === "running" || status === "waiting";
        var cls = "bg-gray-500/25 text-gray-200";
        if (isActive) cls = "bg-sky-500/25 text-sky-200 cursor-pointer hover:bg-sky-500/40";
        else if (status === "completed") cls = "bg-green-500/25 text-green-200";
        else if (status === "failed" || status === "cancelled") cls = "bg-red-500/25 text-red-200";
        if (!badge) {
            var actionsRow = card.querySelector(".kb-card-actions");
            if (!actionsRow) return;
            var wrap = document.createElement("div");
            wrap.className = "mt-1";
            var tag = isActive ? "button" : "span";
            wrap.innerHTML = '<' + tag + ' class="kb-wf-status-badge text-[10px] px-1.5 py-0.5 rounded font-medium"></' + tag + '>';
            card.insertBefore(wrap, actionsRow);
            badge = wrap.querySelector(".kb-wf-status-badge");
            if (isActive) {
                badge.addEventListener("click", function(e) {
                    e.stopPropagation();
                    showRunPopover(badge, ticketId);
                });
            }
        }
        badge.className = "kb-wf-status-badge " + cls + " text-[10px] px-1.5 py-0.5 rounded font-medium";
        badge.textContent = status;
    }

    function openSendWorkflowModal(ticket, source) {
        var isLocal = !source || source === "database";
        var modal = document.getElementById("kb-send-workflow-modal");
        var selectEl = document.getElementById("kb-send-workflow-select");
        var hintEl = document.getElementById("kb-send-workflow-hint");
        if (!modal || !selectEl || !hintEl) return;
        var defaults = resolveWorkflowModalDefault(ticket);
        sendWorkflowContext = {
            ticket: ticket,
            source: source || "database",
            isLocal: isLocal,
            defaultHint: defaults.hint,
        };
        hintEl.textContent = defaults.hint;
        selectEl.innerHTML = '<option value="">Select workflow...</option>';
        apiFetch("/api/tickets/linkable").then(function(data) {
            var workflows = (data && data.workflows) || [];
            workflows.forEach(function(wf) {
                var opt = document.createElement("option");
                opt.value = String(wf.id);
                opt.textContent = wf.title || ("Workflow #" + wf.id);
                if (defaults.selectedWorkflowId && String(wf.id) === String(defaults.selectedWorkflowId)) {
                    opt.selected = true;
                }
                selectEl.appendChild(opt);
            });
            modal.classList.remove("hidden");
        }).catch(function(e) {
            showSnackbar("Failed to load workflows: " + e.message, "error");
        });
    }

    // ── Modal: Links ──

    function renderModalLinks(links) { return ticketModalSections.renderModalLinks(links); }
    function addLink() { return ticketModalSections.addLink(); }
    function deleteLink(linkId) { return ticketModalSections.deleteLink(linkId); }

    // ── Modal: Files ──

    function renderModalFiles(files) { return ticketModalSections.renderModalFiles(files); }
    function uploadFiles(fileList) { return ticketModalSections.uploadFiles(fileList); }
    function deleteFile(fileId) { return ticketModalSections.deleteFile(fileId); }

    // ── Modal: Todos ──

    function renderModalTodos(todos) { return ticketModalSections.renderModalTodos(todos); }
    function addTodo() { return ticketModalSections.addTodo(); }
    function toggleTodo(todoId, done) { return ticketModalSections.toggleTodo(todoId, done); }
    function deleteTodo(todoId) { return ticketModalSections.deleteTodo(todoId); }
    function refreshModalTicket() { return ticketModalSections.refreshModalTicket(); }

    // ── Linkable entities ──

    function loadLinkableEntities(ticket) { return ticketModalSections.loadLinkableEntities(ticket); }
    function populateSelect(selectId, items, valKey, labelKey, selectedVal) { return ticketModalSections.populateSelect(selectId, items, valKey, labelKey, selectedVal); }

    // ── Board modal (tabbed: Details + Advanced) ──

    var _agentProviders = [];  // cached provider list

    function switchTicketTab(tab) {
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            var isActive = btn.dataset.ttab === tab;
            btn.classList.toggle("active", isActive);
            btn.classList.toggle("text-white", isActive);
            btn.classList.toggle("text-gray-400", !isActive);
            btn.style.borderColor = isActive ? "#f97316" : "transparent";
        });
        document.querySelectorAll(".kb-tm-pane").forEach(function(pane) { pane.classList.add("hidden"); });
        var pane = document.getElementById("kb-tm-tab-" + tab);
        if (pane) pane.classList.remove("hidden");
        requestAnimationFrame(function () { syncKanbanTicketModalHeights(); });
    }

    function syncKanbanTicketModalHeights() {
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
        if (!height && kanbanTicketModalDetailsHeight) {
            height = kanbanTicketModalDetailsHeight;
        }
        if (!height) return;
        kanbanTicketModalDetailsHeight = height;
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

    // ── Add ticket ──

    function addTicket() {
        return ticketActions.addTicket();
    }

    // ── Copy external ticket to local board ──

    function openCopyModal(ticket) {
        return ticketActions.openCopyModal(ticket);
    }

    function openCopyLaneModal(lane) {
        return ticketActions.openCopyLaneModal(lane);
    }

    function closeCopyModal() {
        return ticketActions.closeCopyModal();
    }

    function confirmCopy() {
        return ticketActions.confirmCopy();
    }

    // ── Global Settings Modal ──

    // ── Event bindings ──

    // ── Event bindings ──

    // ═══════════════════════════════════════════════════════════════════
    // WhatsApp Chat/Messages Integration
    // ═══════════════════════════════════════════════════════════════════

    // ── Sidebar tab switching ──

    function switchSidebarTab(tab) {
        return boardSettings.switchSidebarTab(tab);
    }

    function applySidebarTabFromUrl() {
        if (boardSettings.getSidebarTabFromUrl() !== "messages") {
            return false;
        }
        var tabMessages = document.getElementById("kb-tab-messages");
        if (tabMessages) {
            tabMessages.classList.remove("hidden");
            boardSettings.updateTabBarVisibility();
        }
        // Only switch panels when not already on Messages. switchSidebarTab runs
        // onEnterMessagesTab → loadWhatsAppChats; re-calling it after every fetch
        // caused an infinite reload loop while ?tab=messages was active.
        if (!isMessagesPanelVisible()) {
            switchSidebarTab("messages");
        }
        return true;
    }

    function loadWhatsAppChats(forceRefresh) {
        return waRuntime.loadWhatsAppChats(forceRefresh);
    }

    function syncFromRelay() {
        var el = document.getElementById("kb-wa-status");
        el.textContent = "Syncing from server...";
        apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).then(function(result) {
            var newCount = Number(result && result.synced) || 0;
            var warning = result && result.warning ? String(result.warning) : "";
            var relayDown = result && result.relay_link_ok === false;
            if (relayDown && warning) {
                if (newCount > 0) {
                    showSnackbar(
                        "Synced " + newCount + " older message" + (newCount !== 1 ? "s" : "") + ". " + warning,
                        "warning"
                    );
                } else {
                    showSnackbar(warning, "error");
                }
            } else if (newCount > 0) {
                showSnackbar("Synced " + newCount + " new message" + (newCount !== 1 ? "s" : "") + " from server", "success");
            } else {
                showSnackbar("No new messages on server");
            }
            // Refresh from local DB after relay -> local sync completes.
            loadWhatsAppChats(true);
        }).catch(function(err) {
            el.textContent = "Sync failed";
            showSnackbar("Sync failed: " + err.message, "error");
        });
    }

    function processWhatsAppMessages(data) {
        return waRuntime.processWhatsAppMessages(data);
    }

    function renderWhatsAppChatList() {
        return waRuntime.renderWhatsAppChatList();
    }

    /** Light thread refresh: only fetch messages, compare with current, append new ones — no full DOM rebuild unless message set changed significantly. */
    function refreshWaThreadIfOpen() {
        return waRuntime.refreshWaThreadIfOpen();
    }

    function showWhatsAppThread(sender, name) {
        return waRuntime.showWhatsAppThread(sender, name);
    }

    function closeWhatsAppThread() {
        var msgView = document.getElementById("kb-wa-thread-view");
        msgView.classList.add("hidden");
        if (waVoiceRecorder && waVoiceRecording) {
            try { waVoiceRecorder.stop(); } catch (e) {}
        }
        waRuntime.resetWhatsAppVoiceRecordingUi();
        // Restore the previous board view or empty state
        document.getElementById("kb-loading").classList.add("hidden");
        if (currentBoard) {
            kbRevealBoardView();
        } else {
            document.getElementById("kb-empty").classList.remove("hidden");
        }
        waSelectedJid = null;
        waThreadMessages = [];
        waSelectedMessageIds = {};
        waSelectionMode = false;
        updateWaThreadSelectToggleUi();
        clearWaPendingAttachment();
        renderWhatsAppChatList();
    }

    function showWaChatContextMenu(x, y) {
        return waManagement.showWaChatContextMenu(x, y);
    }

    function hideWaChatContextMenu() {
        return waManagement.hideWaChatContextMenu();
    }

    function showWaMsgContextMenu(x, y) {
        return waManagement.showWaMsgContextMenu(x, y);
    }

    function hideWaMsgContextMenu() {
        return waManagement.hideWaMsgContextMenuLocal();
    }

    function showWaSyncContextMenu(x, y) {
        return waManagement.showWaSyncContextMenu(x, y);
    }

    function hideWaSyncContextMenu() {
        return waManagement.hideWaSyncContextMenu();
    }

    function clearWaServerMessages() {
        return waManagement.clearWaServerMessages();
    }

    // ── WhatsApp chat context menu actions ──

    function waCtxViewMessages() {
        return waManagement.waCtxViewMessages();
    }

    function waCtxLinkToBoard() {
        return waManagement.waCtxLinkToBoard();
    }

    /** True if focus is in a text field where Delete/Backspace should edit text, not open delete-chat. */
    function waThreadDeleteHotkeyIgnored() {
        return waManagement.waThreadDeleteHotkeyIgnored();
    }

    /** Confirm and DELETE /api/tickets/whatsapp/chat/:phone — full thread wipe (shared by sidebar menu, thread header, message menu in selection mode). */
    function waRunDeleteChatConfirm(phone, displayName) {
        return waManagement.waRunDeleteChatConfirm(phone, displayName);
    }

    function waCtxDeleteChat() {
        return waManagement.waCtxDeleteChat();
    }

    function waCtxSnapshotToBoard() {
        return waManagement.waCtxSnapshotToBoard();
    }

    // ── Bottom snapshot (create ticket from all messages) ───────────────
    function waCtxSnapshotToBoardBottom() {
        return waManagement.waCtxSnapshotToBoardBottom();
    }
    function openTicketFromWhatsApp(phone, title, description, boardId, msgs) {
        return externalTicketModal.openTicketFromWhatsApp(phone, title, description, boardId, msgs);
    }

    function openBoardWhatsappSnapshotFromLane(btn) {
        if (!btn) return;
        return externalTicketModal.openBoardWhatsappSnapshotTicket({
            boardId: btn.dataset.boardId,
            linkId: btn.dataset.linkId,
            laneId: btn.dataset.laneId,
            laneName: btn.dataset.laneName,
            boardName: btn.dataset.boardName,
            linkPhone: btn.dataset.linkPhone,
        });
    }

    function openWaLinkModal(chatData) {
        var modal = document.getElementById("kb-wa-link-modal");
        var phoneEl = document.getElementById("kb-wa-link-phone");
        var boardSelect = document.getElementById("kb-wa-link-board");
        var autoCheckbox = document.getElementById("kb-wa-link-auto");

        if (!modal || !phoneEl || !boardSelect) return;
        phoneEl.textContent = (chatData.name || "") + " (" + chatData.phone + ")";
        if (autoCheckbox) autoCheckbox.checked = false;
        boardSelect.innerHTML = '<option value="">Select a board...</option>';
        modal.classList.remove("hidden");

        Promise.all([
            apiFetch("/api/tickets/boards"),
            apiFetch("/api/tickets/whatsapp/linked-board?phone=" + encodeURIComponent(chatData.phone || "")).catch(function() {
                return { board_id: null, board_name: null };
            }),
        ]).then(function(results) {
            var boards = results[0] || [];
            var linkData = results[1] || {};
            boardSelect.innerHTML = '<option value="">Select a board...</option>';
            boards.forEach(function(b) {
                var selected = "";
                if (linkData.board_id && linkData.board_id === b.id) selected = " selected";
                else if (!linkData.board_id && currentBoard && currentBoard.id === b.id) selected = " selected";
                var sourceTag = b.source === "database" ? "Local" : (b.source || "").toUpperCase();
                boardSelect.innerHTML += '<option value="' + b.id + '"' + selected + '>' + esc(b.name) + " [" + esc(sourceTag) + ']</option>';
            });
        }).catch(function(err) {
            showSnackbar("Failed to load boards: " + (err && err.message ? err.message : String(err)), "error");
        });
    }

    // ── Board modal WhatsApp tab ──

    function loadBoardWaLinks(boardId) {
        return waManagement.loadBoardWaLinks(boardId);
    }

    function bindWhatsAppUiHandlers() {
        var waRefreshBtn = document.getElementById("kb-wa-refresh");
        waRefreshBtn.addEventListener("click", function() { syncFromRelay(); });
        waRefreshBtn.addEventListener("contextmenu", function(e) {
            e.preventDefault();
            showWaSyncContextMenu(e.clientX, e.clientY);
        });
        document.getElementById("kb-wa-search").addEventListener("input", renderWhatsAppChatList);
        document.getElementById("kb-wa-export-all").addEventListener("click", function() {
            waExportMessagesForPhones(null, "all");
        });
        document.getElementById("kb-wa-delete-all").addEventListener("click", function() {
            if (!waChats.length) {
                showSnackbar("No chats to delete");
                return;
            }
            showKanbanConfirm({
                title: "Delete all chats",
                message: "Delete all WhatsApp messages stored locally for every contact? This cannot be undone.",
                confirmLabel: "Delete all",
                danger: true,
                onConfirm: function() {
                    apiFetch("/api/tickets/whatsapp/chats", { method: "DELETE" }).then(function(resp) {
                        hideKanbanConfirm();
                        waSidebarChatListMode = false;
                        waSelectedChatPhones = {};
                        updateWaSidebarFooterUi();
                        waSelectedJid = null;
                        loadWhatsAppChats(true);
                        var deletedChats = Number(resp && resp.deleted_chats) || 0;
                        var deletedMsgs = Number(resp && resp.deleted) || 0;
                        showSnackbar("Deleted " + deletedChats + " chat" + (deletedChats === 1 ? "" : "s") + " (" + deletedMsgs + " messages)", "success");
        }).catch(function(err) {
                        hideKanbanConfirm();
                        showSnackbar("Delete all failed: " + err.message, "error");
                    });
                }
            });
        });
        document.getElementById("kb-wa-sidebar-select-toggle").addEventListener("click", function() {
            setWaSidebarChatListMode(!waSidebarChatListMode);
        });
        (function() {
            var sideFoot = document.getElementById("kb-wa-sidebar-footer");
            if (!sideFoot) return;
            sideFoot.addEventListener("click", function(e) {
                if (!e.target || !e.target.closest) return;
                if (!e.target.closest("#kb-wa-sidebar-cancel-select")) return;
                e.preventDefault();
                e.stopPropagation();
                setWaSidebarChatListMode(false);
            }, true);
        })();
        document.getElementById("kb-wa-export-selected").addEventListener("click", function() {
            var phones = Object.keys(waSelectedChatPhones).filter(function(k) { return waSelectedChatPhones[k]; });
            if (!phones.length) {
                showSnackbar("Select at least one chat");
                return;
            }
            waExportMessagesForPhones(phones, "selected");
        });
        document.getElementById("kb-wa-delete-selected").addEventListener("click", function() {
            var phones = Object.keys(waSelectedChatPhones).filter(function(k) { return waSelectedChatPhones[k]; });
            if (!phones.length) {
                showSnackbar("Select at least one chat");
                return;
            }
            showKanbanConfirm({
                title: "Delete selected chats",
                message: "Delete stored messages for " + phones.length + " contact" + (phones.length === 1 ? "" : "s") + "? This cannot be undone.",
                confirmLabel: "Delete",
                danger: true,
                onConfirm: function() {
                    waDeleteChatsByPhones(phones, function() {
                        hideKanbanConfirm();
                        setWaSidebarChatListMode(false);
                        loadWhatsAppChats(true);
                        showSnackbar("Deleted selected chats", "success");
                    });
                }
                });
            });
        updateWaSidebarFooterUi();
        if (typeof waRuntime.bindWaMessageActionsUi === "function") waRuntime.bindWaMessageActionsUi();
        if (typeof waRuntime.bindWaDraftComposer === "function") waRuntime.bindWaDraftComposer();
        document.getElementById("kb-wa-thread-attach").addEventListener("click", function() {
            var fileInput = document.getElementById("kb-wa-thread-file");
            if (fileInput) fileInput.click();
        });
        document.getElementById("kb-wa-thread-file").addEventListener("change", function() {
            var fileInput = document.getElementById("kb-wa-thread-file");
            var statusEl = document.getElementById("kb-wa-thread-send-status");
            var file = fileInput && fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
            if (!file) {
                clearWaPendingAttachment();
                return;
            }
            waRuntime.setWaSendStatus("Attaching " + file.name + "...", "text-gray-500");
            waHelpers.waBlobToBase64(file).then(function(b64) {
                var mime = String(file.type || "application/octet-stream");
                var kind = mime.indexOf("image/") === 0 ? "image" : "document";
                waPendingAttachment = {
                    name: file.name || ("attachment-" + Date.now()),
                    mime_type: mime,
                    data_b64: b64,
                    kind: kind
                };
                waRuntime.setWaSendStatus("Attached: " + waPendingAttachment.name, "text-green-400");
            }).catch(function(err) {
                clearWaPendingAttachment();
                if (statusEl) {
                    statusEl.classList.remove("hidden");
                    statusEl.classList.remove("text-gray-500", "text-green-400");
                    statusEl.classList.add("text-red-400");
                    statusEl.textContent = "Attachment failed: " + (err && err.message ? err.message : String(err));
                }
            });
        });
        document.getElementById("kb-wa-thread-send").addEventListener("click", waRuntime.sendWhatsAppThreadMessage);
        document.getElementById("kb-wa-thread-select-toggle").addEventListener("click", function() {
            if (!waSelectedJid) {
                showSnackbar("Open a thread first");
                return;
            }
            setWaSelectionMode(!waSelectionMode);
        });
        document.getElementById("kb-wa-thread-voice").addEventListener("click", function() {
            if (waVoiceRecording) {
                waRuntime.stopWhatsAppVoiceRecordingAndSend();
                        } else {
                waRuntime.startWhatsAppVoiceRecording();
                        }
                    });
        document.getElementById("kb-wa-thread-input").addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                waRuntime.sendWhatsAppThreadMessage();
                }
            });
        waRuntime.updateWhatsAppComposerState();

        // Chat context menu
        document.querySelector(".kb-wa-ctx-view").addEventListener("click", waCtxViewMessages);
        document.querySelector(".kb-wa-ctx-link").addEventListener("click", waCtxLinkToBoard);
        document.querySelector(".kb-wa-ctx-snapshot").addEventListener("click", waCtxSnapshotToBoard);
        document.querySelector(".kb-wa-ctx-delete-chat").addEventListener("click", waCtxDeleteChat);

        // Close context menus on outside click
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-wa-ctx-menu")) hideWaChatContextMenu();
            if (!e.target.closest("#kb-wa-msg-ctx-menu")) hideWaMsgContextMenu();
            if (!e.target.closest("#kb-wa-sync-ctx-menu")) hideWaSyncContextMenu();
            if (!e.target.closest("#kb-wa-msg-actions-menu") && !e.target.closest(".kb-wa-msg-more")) {
                if (typeof waRuntime.hideWaMsgActionsMenu === "function") waRuntime.hideWaMsgActionsMenu();
            }
        });

        document.getElementById("kb-wa-link-cancel").addEventListener("click", function() {
            document.getElementById("kb-wa-link-modal").classList.add("hidden");
        });
        document.getElementById("kb-wa-link-confirm").addEventListener("click", waManagement.confirmWaLink);

        // Message context menu
        document.querySelector(".kb-wa-msgctx-link").addEventListener("click", function() {
            return waManagement.waMsgCtxLinkToBoard();
        });
        document.querySelector(".kb-wa-msgctx-ticket").addEventListener("click", waManagement.waMsgCtxCreateTicket);
        document.querySelector(".kb-wa-msgctx-mark-processed").addEventListener("click", waManagement.waMsgCtxMarkProcessed);
        document.querySelector(".kb-wa-msgctx-delete").addEventListener("click", waManagement.waMsgCtxDelete);
        document.querySelector(".kb-wa-syncctx-sync").addEventListener("click", function() {
            hideWaSyncContextMenu();
            syncFromRelay();
        });
        document.querySelector(".kb-wa-syncctx-clear-server").addEventListener("click", function() {
            hideWaSyncContextMenu();
            showKanbanConfirm({
                title: "Clear Server Messages",
                message: "This will request the relay server to wipe stored WhatsApp messages. Continue?",
                confirmLabel: "Clear Server Messages",
                danger: true,
                onConfirm: function() {
                    hideKanbanConfirm();
                    clearWaServerMessages();
                }
            });
        });

        // Board modal: WhatsApp links (inside Advanced)
        var waChatSelect = document.getElementById("kb-bm-wa-chat-select");
        if (waChatSelect) {
            waChatSelect.addEventListener("change", function() {
                var sel = String(waChatSelect.value || "").trim();
                var boardId = editingBoardId;
                var selectedChat = {
                    jid: sel,
                    phone: sel.split("@")[0].split(":")[0],
                    chat_type: sel.indexOf("@g.us") >= 0 ? "group" : "private",
                };
                waManagement.handleBoardWaChatSelectChange(boardId, sel).then(function() {
                    waManagement.syncWaChatSelectLinkedState();
                    waManagement.loadWaGroupPeopleCandidates(selectedChat);
                });
            });
        }
        var waRefreshCandidatesBtn = document.getElementById("kb-bm-wa-refresh-candidates");
        if (waRefreshCandidatesBtn) {
            waRefreshCandidatesBtn.addEventListener("click", function() {
                waManagement.loadWaLinkCandidates();
            });
        }
        if (window.KanbanCustomSelect) {
            window.KanbanCustomSelect.upgradeById("kb-bm-wa-chat-select", { placeholder: "Select chat...", emptyLabel: "Select chat..." });
        }
        waManagement.loadWaLinkCandidates(true);
    }

    function startWhatsAppBootstrap() {
        // Check WhatsApp connection status — show Messages tab if relay is connected.
        // Even if relay is unreachable (401), local messages should still be accessible.
        // loadWhatsAppChats will show the tab if any local messages exist.
        apiFetch("/api/advanced/whatsapp/status").then(function(statusData) {
            if (statusData.status === "connected") {
                waConnected = true;
            }
        }).catch(function() {
            // Relay status check failed (e.g. 401) — don't set waConnected, 
            // but still allow local messages to show via loadWhatsAppChats
        }).finally(function() {
            // Always show Messages tab briefly so loadWhatsAppChats can determine 
            // if there are local messages. If none exist AND relay is down, it will hide the tab.
            if (!waConnected) {
                // Temporarily show the tab so loadWhatsAppChats can populate it
                // processWhatsAppMessages will hide it again if no messages exist
                document.getElementById("kb-tab-messages").classList.remove("hidden");
                boardSettings.updateTabBarVisibility();
            }
            applySidebarTabFromUrl();
            loadWhatsAppChats();
        });

        // ── WebSocket: real-time WhatsApp message updates ──
        var waAutoSyncTimer = null;
        var waAutoSyncBusy = false;

        function runAutoSyncFallback() {
            if (waAutoSyncBusy) return;
            if (!isMessagesPanelVisible()) return;
            waAutoSyncBusy = true;
            apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).then(function(result) {
                var synced = Number(result && result.synced) || 0;
                if (synced > 0) {
                    loadWhatsAppChats(true);
                    // Only refresh thread incrementally if one is open — no full re-render
                    if (waSelectedJid) {
                        refreshWaThreadIfOpen();
                    }
                }
            }).catch(function() {
                // Ignore transient failures; timer will retry.
            }).finally(function() {
                waAutoSyncBusy = false;
            });
        }

        function startAutoSyncFallback() {
            if (waAutoSyncTimer) return;
            // Fallback when websocket stream is unavailable/silent.
            waAutoSyncTimer = setInterval(runAutoSyncFallback, 7000);
            // Trigger one immediate background sync when entering Messages.
            setTimeout(runAutoSyncFallback, 500);
        }

        function stopAutoSyncFallback() {
            if (!waAutoSyncTimer) return;
            clearInterval(waAutoSyncTimer);
            waAutoSyncTimer = null;
        }

        function connectWaWS() {
            if (waWS && (waWS.readyState === WebSocket.OPEN || waWS.readyState === WebSocket.CONNECTING)) return;
            fetchWaWsAuthBundle().then(function(bundle) {
                var wsBase = "";
                if (window.location.host.indexOf("decisionsai.net") !== -1) {
                    var sameScheme = (window.location.protocol === "https:") ? "wss://" : "ws://";
                    wsBase = sameScheme + window.location.host + "/ws/whatsapp";
                } else {
                    wsBase = "wss://www.decisionsai.net/ws/whatsapp";
                }
                var wsUrl = wsBase + "?ws_token=" + encodeURIComponent(bundle.ws_token);
                try {
                    waWS = new WebSocket(wsUrl);
                } catch (err) {
                    throw err;
                }
                waWS.onopen = function() {
                    try {
                        var subTokens = ((bundle && bundle.subscription_tokens) || []).map(function(x) { return x.token; }).filter(Boolean);
                        waWS.send(JSON.stringify({ client_type: "desktop", subscribe_tokens: subTokens }));
                    } catch (err) {}
                    stopAutoSyncFallback();
                };
                waWS.onmessage = function(event) {
                    try {
                        var payload = JSON.parse(event.data || "{}");
                        if (payload.type === "ping") return;
                        if (payload.type !== "whatsapp_message") return;
                        var msg = payload.data || {};
                        var who = msg.sender_push_name || msg.sender_phone || msg.jid_phone || "Unknown";
                        var preview = msg.text ? msg.text.substring(0, 50) : (msg.media_type ? "📎 " + msg.media_type : "");
                        showSnackbar("WhatsApp: " + who + " — " + preview, "success");
                        // Ensure relay events are persisted into local DB before UI refresh.
                        apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).finally(function() {
                        loadWhatsAppChats(true);
                        if (waSelectedJid && (waSelectedJid === msg.jid_phone || waSelectedJid === msg.sender_phone)) {
                                refreshWaThreadIfOpen();
                            }
                        });
                        stopAutoSyncFallback();
                    } catch (err) {}
                };
                waWS.onerror = function() {
                    startAutoSyncFallback();
                };
                waWS.onclose = function() {
                    waWS = null;
                    startAutoSyncFallback();
                    if (!waWSReconnectTimer) waWSReconnectTimer = setTimeout(function() {
                        waWSReconnectTimer = null;
                        connectWaWS();
                    }, 5000);
                };
            }).catch(function() {
                startAutoSyncFallback();
                if (!waWSReconnectTimer) waWSReconnectTimer = setTimeout(function() {
                    waWSReconnectTimer = null;
                    connectWaWS();
                }, 5000);
            });
        }
        connectWaWS();
        // Keep updates flowing even if websocket stream is unavailable.
        startAutoSyncFallback();
    }

    // Initialize WhatsApp section
    function initWhatsApp() {
        return waManagement.initWhatsApp();
    }

    // ═══════════════════════════════════════════════════════════════════
    // End WhatsApp Integration
    // ═══════════════════════════════════════════════════════════════════
    function init() {
        // Reset overlays on startup so kanban always loads into board workspace.
        hideAllKanbanModals();
        // Apply sidebar tab from URL before board auto-select so list view stays on Messages.
        applySidebarTabFromUrl();

        // Board sidebar
        document.getElementById("kb-add-board").addEventListener("click", function() { boardSettings.openBoardModal(null); });
        document.getElementById("kb-create-big").addEventListener("click", function() { boardSettings.openBoardModal(null); });
        boardSettings.bindTopLevel();
        boardSettings.bindGlobalSettings();
        boardSettings.bindBoardActions();
        bindBoardSidebarKeyboard();

        // Ticket modal tabs
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchTicketTab(btn.dataset.ttab); });
        });

        boardSettings.bindBoardModal();

        // Ticket modal
        document.getElementById("kb-modal-close").addEventListener("click", closeTicketModal);
        document.querySelectorAll(".kb-modal-save-action").forEach(function(btn) {
            btn.addEventListener("click", saveTicket);
        });
        document.getElementById("kb-modal-delete").addEventListener("click", deleteTicket);
        var clearReportBtn = document.getElementById("kb-modal-clear-report");
        if (clearReportBtn) clearReportBtn.addEventListener("click", clearModalAuditReport);
        // Priority buttons
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.addEventListener("click", function() { setPriorityButtons(btn.dataset.pri); });
        });

        // Links
        document.getElementById("kb-modal-add-link").addEventListener("click", addLink);
        document.getElementById("kb-modal-link-url").addEventListener("keydown", function(e) {
            if (e.key === "Enter") addLink();
        });

        // Files
        document.getElementById("kb-modal-upload-btn").addEventListener("click", function() {
            document.getElementById("kb-modal-file-input").click();
        });
        document.getElementById("kb-modal-file-input").addEventListener("change", function() {
            uploadFiles(this.files); this.value = "";
        });

        // Todos
        document.getElementById("kb-modal-add-todo").addEventListener("click", addTodo);
        document.getElementById("kb-modal-todo-input").addEventListener("keydown", function(e) {
            if (e.key === "Enter") addTodo();
        });

        // Legacy confirm modal listeners are guarded because Kanban now uses
        // the shared DecisionsAPI confirm modal from base.html.
        var legacyConfirmCancel = document.getElementById("kb-confirm-cancel");
        var legacyConfirmOk = document.getElementById("kb-confirm-ok");
        var legacyConfirmModal = document.getElementById("kb-confirm-modal");
        if (legacyConfirmCancel) legacyConfirmCancel.addEventListener("click", hideKanbanConfirm);
        if (legacyConfirmOk) {
            legacyConfirmOk.addEventListener("click", function() {
                modalHelpers.invokeConfirmAction();
            });
        }
        document.getElementById("kb-send-workflow-cancel").addEventListener("click", closeSendWorkflowModal);
        document.getElementById("kb-send-workflow-confirm").addEventListener("click", confirmSendWorkflowModal);
        if (legacyConfirmModal) {
            legacyConfirmModal.addEventListener("click", function(e) {
                if (e.target === this) hideKanbanConfirm();
            });
        }
        document.addEventListener("keydown", function(e) {
            var cfm = document.getElementById("kb-confirm-modal");
            if (!cfm || cfm.classList.contains("hidden")) return;
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                hideKanbanConfirm();
                return;
            }
            var confirmKeys = e.key === "Enter" || e.key === "Delete";
            if (confirmKeys && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
                var cancelBtn = document.getElementById("kb-confirm-cancel");
                if (cancelBtn && (e.target === cancelBtn || cancelBtn.contains(e.target))) {
                    e.preventDefault();
                    e.stopPropagation();
                    hideKanbanConfirm();
                    return;
                }
                e.preventDefault();
                e.stopPropagation();
                modalHelpers.invokeConfirmAction();
            }
        }, true);
        document.addEventListener("keydown", function(e) {
            if (!shouldOpenSelectedBoardDeleteConfirm(e)) return;
            e.preventDefault();
            e.stopPropagation();
            confirmDeleteCurrentLocalBoard();
        }, true);

        // Copy modal
        document.getElementById("kb-copy-cancel").addEventListener("click", closeCopyModal);
        document.getElementById("kb-copy-confirm").addEventListener("click", confirmCopy);
        document.getElementById("kb-copy-board-select").addEventListener("change", function() {
            ticketActions.onCopyBoardChanged();
        });
        document.getElementById("kb-copy-modal").addEventListener("click", function(e) {
        });


        // Load initial data first (populates board menu with context menu elements)
        loadBoards();
        try { initWhatsApp(); } catch(e) { console.warn('WhatsApp init skipped:', e); }
        try { connectKanbanBoardWS(); } catch(e) { console.warn('Board WS skipped:', e); }
        
        // Context menu (after boards are populated to ensure DOM elements exist)
        document.querySelector(".kb-ctx-activate").addEventListener("click", boardSettings.ctxActivateBoard);
        document.querySelector(".kb-ctx-configure").addEventListener("click", boardSettings.ctxConfigureBoard);
        document.querySelector(".kb-ctx-rename").addEventListener("click", boardSettings.ctxRenameBoard);
        document.querySelector(".kb-ctx-archive").addEventListener("click", boardSettings.ctxArchiveBoard);
        document.querySelector(".kb-ctx-delete").addEventListener("click", boardSettings.ctxDeleteBoard);
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-board-ctx-menu")) boardSettings.hideBoardContextMenu();
            if (!e.target.closest("#kb-ext-board-ctx-menu")) hideExtBoardContextMenu();
        });
        // External board context menu
        document.querySelector(".kb-ext-ctx-configure").addEventListener("click", extCtxConfigure);
        // Create external ticket modal
        document.getElementById("kb-cet-cancel").addEventListener("click", externalTicketModal.closeCreateExtTicketModal);
        document.getElementById("kb-cet-cancel-2").addEventListener("click", externalTicketModal.closeCreateExtTicketModal);
        document.getElementById("kb-cet-submit").addEventListener("click", externalTicketModal.submitCreateExtTicket);
        document.getElementById("kb-cet-file-input").addEventListener("change", externalTicketModal.handleCetFileSelect);
        // CET priority buttons
        document.querySelectorAll("#kb-cet-priority-btns button").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var pri = btn.dataset.cetPri;
                document.getElementById("kb-cet-priority").value = pri;
                document.querySelectorAll("#kb-cet-priority-btns button").forEach(function(b) {
                    b.className = "px-3 py-1.5 rounded text-xs font-medium border border-white/20 text-gray-400";
                    if (b.dataset.cetPri === "low") b.className += " hover:border-green-500";
                    if (b.dataset.cetPri === "medium") b.className += " hover:border-yellow-500";
                    if (b.dataset.cetPri === "high") b.className += " hover:border-orange-500";
                    if (b.dataset.cetPri === "critical") b.className += " hover:border-red-500";
                });
                btn.className = "px-3 py-1.5 rounded text-xs font-medium border ";
                if (pri === "low") btn.className += "border-green-500 text-green-400 bg-green-500/10";
                if (pri === "medium") btn.className += "border-[#f97316] text-[#f97316] bg-[#f97316]/10";
                if (pri === "high") btn.className += "border-orange-500 text-orange-400 bg-orange-500/10";
                if (pri === "critical") btn.className += "border-red-500 text-red-400 bg-red-500/10";
            });
        });
        document.getElementById("kb-create-ext-ticket-modal").addEventListener("click", function(e) {
            if (e.target === this) externalTicketModal.closeCreateExtTicketModal();
        });
        
        // Initially hide the tab bar since Messages is hidden by default
        boardSettings.updateTabBarVisibility();

        // ── Final safety: ensure no modal overlays are visible on init ──
        // (protects against cached page state or prior JS errors)
        document.querySelectorAll('.kb-modal-overlay').forEach(function(overlay) {
            overlay.classList.add('hidden');
        });

        if (window.KanbanDocuments && typeof window.KanbanDocuments.init === "function") {
            window.KanbanDocuments.init({
                apiFetch: apiFetch,
                showKanbanConfirm: showKanbanConfirm,
                hideKanbanConfirm: hideKanbanConfirm,
                showSnackbar: showSnackbar,
                restoreMainPanel: function() {
                    if (isMessagesPanelVisible()) return;
                    var boardView = document.getElementById("kb-board-view");
                    var emptyView = document.getElementById("kb-empty");
                    var loadingView = document.getElementById("kb-loading");
                    var waThread = document.getElementById("kb-wa-thread-view");
                    if (waThread) waThread.classList.add("hidden");
                    if (currentBoard && currentBoardData) {
                        if (emptyView) emptyView.classList.add("hidden");
                        if (loadingView) loadingView.classList.add("hidden");
                        kbRevealBoardView();
                    } else if (currentBoard) {
                        if (emptyView) emptyView.classList.add("hidden");
                        if (loadingView) loadingView.classList.remove("hidden");
                        if (boardView) boardView.classList.add("hidden");
                    } else {
                        if (boardView) boardView.classList.add("hidden");
                        if (loadingView) loadingView.classList.add("hidden");
                        if (emptyView) emptyView.classList.remove("hidden");
                    }
                },
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
