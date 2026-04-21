(function() {
    "use strict";

    var currentBoard = null;       // { id, source, ... }
    var currentBoardData = null;   // full board data with lanes/tickets
    var currentBoardHasProject = false; // whether current board has a linked project
    var dbBoards = [];
    var editingBoardId = null;     // null = create, number = edit
    var modalTicketId = null;
    var copyTicketData = null;     // { title, description } for copy modal
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
    var activeSourceTab = "local";
    var currentAgentStatus = null;
    var _agentStatusPoll = null;
    var kbBoardWS = null;
    var kbBoardWSReconnectTimer = null;
    var kbBoardRefreshTimer = null;
    var waTicketComposeInFlight = false;
    var waSidebarChatListMode = false;
    var waSelectedChatPhones = {};

    // ── Helpers ──

    var commonUtils = window.KanbanCommonUtils;
    var waHelpers = window.KanbanWhatsAppHelpers;
    var esc = commonUtils.esc;
    var waMsgHasLinkedTicket = waHelpers.waMsgHasLinkedTicket;
    var waIsMessageSelectable = waHelpers.waIsMessageSelectable;
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
        apiFetch("/api/kanban/whatsapp/messages?limit=100000").then(function(data) {
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
            apiFetch("/api/kanban/whatsapp/chat/" + encodeURIComponent(p), { method: "DELETE" }).then(function() { next(); }).catch(function() { next(); });
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
        apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).finally(function() {
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
        return apiFetch("/api/kanban/whatsapp/ws-auth", {
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

    function showKanbanConfirm(opts) {
        modalHelpers.showConfirm(opts);
    }
    function hideKanbanConfirm() {
        modalHelpers.hideConfirm();
    }
    var ticketUi = window.KanbanTicketUi.create({
        esc: esc,
        stripHtml: stripHtml,
        truncate: truncate,
        setPriorityButtons: setPriorityButtons,
        renderModalLinks: renderModalLinks,
        switchTicketTab: switchTicketTab,
        showSnackbar: showSnackbar,
        openTicketModal: openTicketModal,
        copyAndPushExternalTicket: copyAndPushExternalTicket,
        openCopyModal: openCopyModal,
        apiFetch: apiFetch,
        sendTicketToProjectById: sendTicketToProjectById,
        pushTicketToCli: pushTicketToCli,
        reloadCurrentDatabaseBoard: reloadCurrentDatabaseBoard,
        showKanbanConfirm: showKanbanConfirm,
        hideKanbanConfirm: hideKanbanConfirm,
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        getCurrentAgentStatus: function() { return currentAgentStatus; },
    });
    var ticketModalSections = window.KanbanTicketModalSections.create({
        esc: esc,
        apiFetch: apiFetch,
        showSnackbar: showSnackbar,
        getModalTicketId: function() { return modalTicketId; },
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
        populateSelect: populateSelect,
        getAgentProviders: function() { return _agentProviders; },
        setAgentProviders: function(providers) { _agentProviders = providers || []; },
        getDbBoards: function() { return dbBoards; },
        getCtxMenuBoardId: function() { return ctxMenuBoardId; },
        setCtxMenuBoardId: function(v) { ctxMenuBoardId = v; },
        closeWhatsAppThread: closeWhatsAppThread,
        switchSourceTab: switchSourceTab,
        getExternalBoards: getExternalBoards,
        renderExternalBoards: renderExternalBoards,
        resetExternalCache: function() { _externalCache = null; _externalCacheTime = 0; },
        addTicket: addTicket,
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
        bindWhatsAppUiHandlers: bindWhatsAppUiHandlers,
        startWhatsAppBootstrap: startWhatsAppBootstrap,
    });
    var waRuntime = window.KanbanWhatsAppRuntime.create({
        apiFetch: apiFetch,
        esc: esc,
        dedupeThreadMessages: dedupeThreadMessages,
        switchSidebarTab: switchSidebarTab,
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
        },
    });
    var ticketActions = window.KanbanTicketActions.create({
        apiFetch: apiFetch,
        showSnackbar: showSnackbar,
        stripHtml: stripHtml,
        selectBoard: selectBoard,
        openTicketModal: openTicketModal,
        openCreateExternalTicketModal: openCreateExternalTicketModal,
        getCurrentBoard: function() { return currentBoard; },
        getCurrentBoardData: function() { return currentBoardData; },
        getDbBoards: function() { return dbBoards; },
        getCopyTicketData: function() { return copyTicketData; },
        setCopyTicketData: function(v) { copyTicketData = v; },
    });
    var externalTicketModal = window.KanbanExternalTicketModal.create({
        apiFetch: apiFetch,
        esc: esc,
        showSnackbar: showSnackbar,
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

    function getExternalBoards(forceRefresh) {
        var now = Date.now();
        var stale = forceRefresh || !_externalCache || (now - _externalCacheTime > EXTERNAL_CACHE_TTL);
        if (!stale && _externalCache) {
            return Promise.resolve(_externalCache);
        }
        return apiFetch("/api/kanban/external-boards").then(function(data) {
            _externalCache = data || { trello: [], jira: [] };
            _externalCacheTime = Date.now();
            return _externalCache;
        });
    }

    function loadBoards(forceRefresh) {
        var now = Date.now();
        var boardsStale = forceRefresh || !_boardsCache || (now - _boardsCacheTime > BOARDS_CACHE_TTL);
        var externalStale = forceRefresh || !_externalCache || (now - _externalCacheTime > EXTERNAL_CACHE_TTL);

        var boardsPromise = boardsStale
            ? apiFetch("/api/kanban/boards").then(function(boards) {
                _boardsCache = boards;
                _boardsCacheTime = Date.now();
                return boards;
            })
            : Promise.resolve(_boardsCache);

        boardsPromise.then(function(boards) {
            dbBoards = boards.filter(function(b) { return b.source === "database"; });
            renderSidebarBoards(boards);

            // Auto-select: URL param > localStorage > first board
            if (!currentBoard && dbBoards.length) {
                var params = new URLSearchParams(window.location.search);
                var urlBoardId = parseInt(params.get("board_id"), 10);
                if (urlBoardId && dbBoards.some(function(b) { return b.id === urlBoardId; })) {
                    selectBoard("database", urlBoardId);
                } else {
                    var last = null;
                    try { last = JSON.parse(localStorage.getItem("kb_last_selected")); } catch (e) {}
                    if (last && last.source === "database" && dbBoards.some(function(b) { return b.id === last.id; })) {
                        selectBoard(last.source, last.id);
                    } else {
                        selectBoard("database", dbBoards[0].id);
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

    function renderSidebarBoards(boards) {
        var container = document.getElementById("kb-db-boards");
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var db = boards.filter(function(b) { return b.source === "database"; });
        if (search) db = db.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; });
        container.innerHTML = db.length ? "" : '<p class="text-xs text-gray-500 italic">No boards yet</p>';
        db.forEach(function(b) {
            var div = document.createElement("div");
            div.className = "kb-board-item text-gray-300" + (currentBoard && currentBoard.id === b.id && currentBoard.source === "database" ? " active" : "");
            div.draggable = true;
            div.dataset.boardId = b.id;
            var inUseTag = b.in_use ? '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#f97316;color:#fff;margin-left:4px;flex-shrink:0">IN USE</span>' : '';
            div.innerHTML = '<span class="kb-src-icon" style="background:' + esc(b.color || '#f97316') + '"></span><span class="flex-1 truncate">' + esc(b.name) + '</span>' + inUseTag + (b.agent_enabled ? '<svg class="kb-agent-indicator" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0" title="Agent check-in enabled"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="11"/></svg>' : '');
            div.onclick = function() { selectBoard("database", b.id); };
            div.ondblclick = function() { boardSettings.openBoardModal(b.id); };
            div.ondragstart = function(e) { e.dataTransfer.setData("text/plain", "board:" + b.id); div.classList.add("dragging"); };
            div.ondragend = function() { div.classList.remove("dragging"); };
            div.ondragover = function(e) { e.preventDefault(); div.style.borderTop = "2px solid #f97316"; };
            div.ondragleave = function() { div.style.borderTop = ""; };
            div.ondrop = function(e) {
                e.preventDefault();
                div.style.borderTop = "";
                var data = e.dataTransfer.getData("text/plain");
                if (!data.startsWith("board:")) return;
                var draggedId = parseInt(data.split(":")[1], 10);
                if (draggedId === b.id) return;
                // Reorder: collect current order, move dragged before drop target
                var items = container.querySelectorAll("[data-board-id]");
                var order = [];
                items.forEach(function(el) { order.push(parseInt(el.dataset.boardId, 10)); });
                order = order.filter(function(id) { return id !== draggedId; });
                var dropIdx = order.indexOf(b.id);
                order.splice(dropIdx, 0, draggedId);
                apiFetch("/api/kanban/boards/reorder", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ order: order })
                }).then(function() { loadBoards(true); }).catch(function() {});
            };
            div.oncontextmenu = function(e) { e.preventDefault(); boardSettings.showBoardContextMenu(e, b.id); };
            container.appendChild(div);
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
        var search = (document.getElementById("kb-search").value || "").toLowerCase();
        var filtered = search ? boards.filter(function(b) { return b.name.toLowerCase().indexOf(search) >= 0; }) : boards;
        container.innerHTML = filtered.length ? "" : '<p class="text-xs text-gray-500 italic">No matching boards</p>';
        filtered.forEach(function(b) {
            var div = document.createElement("div");
            var cls = source === "trello" ? "kb-src-trello" : "kb-src-jira";
            div.className = "kb-board-item text-gray-300" + (currentBoard && currentBoard.id === b.id && currentBoard.source === source ? " active" : "");
            div.innerHTML = '<span class="kb-src-icon ' + cls + '"></span>' + esc(b.name);
            div.onclick = function() { selectBoard(source, b.id, b.url); };
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

    function selectBoard(source, id, extUrl) {
        stopAgentStatusPolling();
        currentAgentStatus = null;
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
            apiFetch("/api/kanban/boards/" + id).then(function(data) {
                currentBoardData = data;
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-board-view").classList.remove("hidden");
                }
                renderBoard(data, true);
                startAgentStatusPolling(id);
            }).catch(function(e) {
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                }
                showSnackbar("Failed to load board: " + e.message, "error");
            });
        } else {
            apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(id)).then(function(data) {
                currentBoardData = data;
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-board-view").classList.remove("hidden");
                }
                renderBoard(data, false);
                if (data.local_id) {
                    startAgentStatusPolling(data.local_id);
                } else {
                    stopAgentStatusPolling();
                    currentAgentStatus = null;
                    applyAgentStatusVisuals();
                }
            }).catch(function(e) {
                if (!keepMessagesVisible) {
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
                }
                showSnackbar("Failed to load external board: " + e.message, "error");
            });
        }
        loadBoards(); // uses cache, just re-renders sidebar active state
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
        if (!isLocal) {
            var canCreateExternal = data.can_create_ticket !== false;
            addTicketBtn.style.display = canCreateExternal ? "" : "none";
            addTicketBtn.textContent = "+ Create Ticket";
            addTicketBtn.title = canCreateExternal
                ? ("Create a ticket on this " + (currentBoard.source === 'trello' ? 'Trello' : 'Jira') + " board")
                : "You do not have permission to create tickets on this board";
        } else {
            addTicketBtn.style.display = "";
            addTicketBtn.textContent = "+ Add Ticket";
            addTicketBtn.title = "";
        }
        // Configure button — always shows gear icon + "Configure" label
        var editBtn = document.getElementById("kb-edit-board");
        var editLabel = document.getElementById("kb-edit-board-label");
        editBtn.style.display = "";
        if (editLabel) editLabel.textContent = "Configure";
        editBtn.title = isLocal ? "Configure board settings" : "Configure this external board (link project, workflow, etc.)";
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
        currentBoardHasProject = !!(data.default_project_id || (currentBoard.source === "database" && data.id));
        if (!isLocal && data.local_id) {
            currentBoard._localId = data.local_id;
        }

        renderLanes(data.lanes || [], isLocal, data);
        applyAgentStatusVisuals();
    }

    function stopAgentStatusPolling() {
        if (_agentStatusPoll) {
            clearInterval(_agentStatusPoll);
            _agentStatusPoll = null;
        }
    }

    function getCurrentStatusBoardId() {
        if (!currentBoard) return null;
        if (currentBoard.source === "database") return currentBoard.id;
        if (currentBoardData && currentBoardData.local_id) return currentBoardData.local_id;
        return null;
    }

    function startAgentStatusPolling(boardId) {
        stopAgentStatusPolling();
        function tick() {
            var visibleBoardId = getCurrentStatusBoardId();
            if (!visibleBoardId || String(visibleBoardId) !== String(boardId)) return;
            apiFetch("/api/kanban/boards/" + boardId + "/agent-status")
                .then(function(status) {
                    currentAgentStatus = status || null;
                    applyAgentStatusVisuals();
                })
                .catch(function() {});
        }
        tick();
        _agentStatusPoll = setInterval(tick, 3000);
    }

    function refreshCurrentBoardRealtime() {
        if (!currentBoard || currentBoard.source !== "database") return;
        var boardId = currentBoard.id;
        apiFetch("/api/kanban/boards/" + boardId).then(function(data) {
            if (!currentBoard || currentBoard.source !== "database" || currentBoard.id !== boardId) return;
            currentBoardData = data;
            renderBoard(data, true);
        }).catch(function() {});
        apiFetch("/api/kanban/boards/" + boardId + "/agent-status").then(function(status) {
            if (!currentBoard || currentBoard.source !== "database" || currentBoard.id !== boardId) return;
            currentAgentStatus = status || null;
            applyAgentStatusVisuals();
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
        var url = proto + "//" + location.host + "/api/kanban/ws/boards";
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

    function applyAgentStatusVisuals() {
        var pill = document.getElementById("kb-agent-status-pill");
        if (!pill) return;
        if (!currentBoard || !currentAgentStatus || !currentAgentStatus.state || currentAgentStatus.state === "idle") {
            pill.classList.add("hidden");
            pill.textContent = "";
        } else {
            var phase = currentAgentStatus.phase ? String(currentAgentStatus.phase) : "execution";
            var phaseLabel = phase.charAt(0).toUpperCase() + phase.slice(1);
            var cur = currentAgentStatus.current_ticket_title || ("#" + (currentAgentStatus.current_ticket_id || ""));
            var progress = "";
            if (currentAgentStatus.processed_count != null && currentAgentStatus.total_tickets != null) {
                progress = " (" + currentAgentStatus.processed_count + "/" + currentAgentStatus.total_tickets + ")";
            }
            pill.textContent = "Check-in: " + phaseLabel + " - " + cur + progress;
            pill.classList.remove("hidden");
        }

        document.querySelectorAll(".kb-card").forEach(function(card) {
            card.classList.remove("kb-in-progress");
            var badge = card.querySelector(".kb-card-live-status");
            if (badge) badge.remove();
            if (
                currentAgentStatus &&
                currentAgentStatus.state !== "idle" &&
                currentAgentStatus.current_ticket_id != null &&
                String(currentAgentStatus.current_ticket_id) === String(card.dataset.ticketId)
            ) {
                card.classList.add("kb-in-progress");
                var b = document.createElement("div");
                b.className = "kb-card-live-status text-[10px] text-green-300 mt-1";
                var ph = currentAgentStatus.phase ? String(currentAgentStatus.phase) : "execution";
                b.textContent = "In progress - " + ph;
                card.appendChild(b);
            }
        });
    }

    function renderLanes(lanes, isLocal, boardData) {
        var container = document.getElementById("kb-lanes");
        container.innerHTML = "";
        var boardData = currentBoardData || {};
        lanes.forEach(function(lane) {
            var col = document.createElement("div");
            col.className = "kb-lane flex flex-col bg-[#152054]/50 rounded-lg border border-white/10";
            col.innerHTML = '<div class="px-3 py-2 border-b border-white/10 flex items-center justify-between">' +
                '<span class="text-sm font-medium text-gray-300">' + esc(lane.name) + '</span>' +
                '<span class="text-xs text-gray-500">' + (lane.tickets || []).length + '</span></div>';
            var body = document.createElement("div");
            body.className = "kb-lane-body flex-1 p-2 space-y-2 overflow-y-auto";
            body.dataset.laneId = lane.id;
            if (isLocal) {
                body.addEventListener("dragover", function(e) { e.preventDefault(); body.classList.add("drag-over"); });
                body.addEventListener("dragleave", function() { body.classList.remove("drag-over"); });
                body.addEventListener("drop", function(e) {
                    e.preventDefault(); body.classList.remove("drag-over");
                    var ticketId = e.dataTransfer.getData("text/plain");
                    if (ticketId) moveTicket(parseInt(ticketId, 10), lane.id, body, e.clientY);
                });
            }
            (lane.tickets || []).forEach(function(ticket) { body.appendChild(createTicketCard(ticket, isLocal, boardData)); });
            col.appendChild(body);
            container.appendChild(col);
        });
    }

    /** Push a local ticket to CLI (with confirmation and spinners). */
    function pushTicketToCli(ticketId, btnEl) {
        if (!confirm("Push ticket #" + ticketId + " to the project CLI?")) return;
        if (btnEl) {
            btnEl.innerHTML = '<svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93"/></svg>';
            btnEl.classList.add("text-orange-400");
            btnEl.disabled = true;
        }
        apiFetch("/api/kanban/tickets/" + ticketId + "/send-to-cli", { method: "POST" })
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
    }

    /** Send a local ticket to project by ID. */
    function sendTicketToProjectById(ticketId) {
        apiFetch("/api/kanban/tickets/" + ticketId + "/send-to-project", { method: "POST" })
            .then(function(r) { showSnackbar(r.message || "Sent to project"); })
            .catch(function(err) { showSnackbar("Error: " + err.message, "error"); });
    }

    /** Copy an external ticket to the local board, then optionally send to CLI or project. */
    function copyAndPushExternalTicket(ticket, source, action) {
        if (!dbBoards.length) { showSnackbar("No local boards available", "error"); return; }
        // Find the best target board
        var targetBoard = (currentBoardData && currentBoardData.local_id) ? null : null;
        // Prefer a board linked to the same project as the external board config
        var preferredBoard = null;
        for (var i = 0; i < dbBoards.length; i++) {
            if (currentBoardData && currentBoardData.default_project_id && dbBoards[i].default_project_id === currentBoardData.default_project_id) {
                preferredBoard = dbBoards[i]; break;
            }
        }
        if (!preferredBoard && currentBoardData && currentBoardData.local_id) {
            for (var j = 0; j < dbBoards.length; j++) {
                if (dbBoards[j].id === currentBoardData.local_id) { preferredBoard = dbBoards[j]; break; }
            }
        }
        if (!preferredBoard) preferredBoard = dbBoards[0];

        var payload = {
            board_id: preferredBoard.id,
            title: ticket.title,
            description: stripHtml(ticket.description || ""),
            priority: ticket.priority || "medium",
            time_estimate: ticket.time_estimate || "",
            time_spent: ticket.time_spent || "",
            external_source: source,
            external_id: String(ticket.id),
            external_url: ticket.url || "",
            auto_send_to_project: action === 'project',
            auto_send_to_cli: action === 'cli',
        };

        apiFetch("/api/kanban/tickets/copy-external-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function(r) {
            if (action === 'cli' && r.id) {
                showSnackbar("Ticket copied — pushing to CLI…");
                pushTicketToCli(r.id, null);
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
        var cards = Array.prototype.slice.call(bodyEl.querySelectorAll(".kb-card"));
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
            : bodyEl.querySelectorAll(".kb-card").length;
        apiFetch("/api/kanban/tickets/" + ticketId + "/move", {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: laneId, position: position })
        }).then(function() { selectBoard("database", currentBoard.id); })
        .catch(function(e) { showSnackbar("Move failed: " + e.message, "error"); });
    }

    // ── Ticket modal ──

    function openTicketModal(ticketId) {
        modalTicketId = ticketId;
        switchTicketTab("details");
        // Reset modal to local-ticket mode
        resetTicketModalForLocal();
        apiFetch("/api/kanban/tickets/" + ticketId).then(function(t) {
            document.getElementById("kb-modal-ticket-title").value = t.title || "";
            document.getElementById("kb-modal-ticket-desc").value = t.description || "";
            document.getElementById("kb-modal-ticket-estimate").value = t.time_estimate || "";
            document.getElementById("kb-modal-ticket-duration").value = t.time_spent || "";
            setPriorityButtons(t.priority || "medium");
            renderModalLinks(t.links || []);
            renderModalFiles(t.files || []);
            renderModalTodos(t.todos || []);
            loadLinkableEntities(t);
            // Hide external metadata section for local tickets
            var extMeta = document.getElementById("kb-modal-external-meta");
            if (extMeta) extMeta.classList.add("hidden");
            document.getElementById("kb-ticket-modal").classList.remove("hidden");
        }).catch(function(e) { showSnackbar("Failed to load ticket: " + e.message, "error"); });
    }

    function closeTicketModal() {
        document.getElementById("kb-ticket-modal").classList.add("hidden");
        modalTicketId = null;
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
        var descArea = document.getElementById("kb-modal-ticket-desc");
        var estimateInput = document.getElementById("kb-modal-ticket-estimate");
        var durationInput = document.getElementById("kb-modal-ticket-duration");
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
        var richDiv = descArea.parentElement.querySelector(".kb-ext-rich-desc");
        if (richDiv) richDiv.remove();
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.classList.remove("opacity-50", "cursor-not-allowed");
            btn.disabled = false;
        });
        document.getElementById("kb-modal-save").classList.remove("hidden");
        document.getElementById("kb-modal-delete").classList.remove("hidden");
        var transferBtn = document.getElementById("kb-modal-transfer-ext");
        if (transferBtn) transferBtn.classList.add("hidden");
        // Clear todos input
        var todoInput = document.getElementById("kb-modal-todo-input");
        if (todoInput) todoInput.readOnly = false;
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

    var isValidTimeTrackingValue = commonUtils.isValidTimeTrackingValue;

    function saveTicket() {
        if (!modalTicketId) return;
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
            title: document.getElementById("kb-modal-ticket-title").value.trim(),
            description: document.getElementById("kb-modal-ticket-desc").value.trim(),
            priority: getSelectedPriority(),
            time_estimate: estimate,
            time_spent: duration,
            linked_workflow_id: parseInt(document.getElementById("kb-modal-link-workflow").value) || null,
            linked_snippet_id: null,
            linked_action_id: null,
        };
        if (!payload.title) { showSnackbar("Title is required", "error"); return; }
        apiFetch("/api/kanban/tickets/" + modalTicketId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function() {
            showSnackbar("Ticket saved"); closeTicketModal();
            selectBoard("database", currentBoard.id);
        }).catch(function(e) { showSnackbar("Save failed: " + e.message, "error"); });
    }

    function deleteTicket() {
        if (!modalTicketId) return;
        var tid = modalTicketId;
        showKanbanConfirm({
            title: "Delete ticket",
            message: "Delete this ticket? This cannot be undone.",
            confirmLabel: "Delete",
            danger: true,
            onConfirm: function() {
                hideKanbanConfirm();
                apiFetch("/api/kanban/tickets/" + tid, { method: "DELETE" }).then(function() {
                    showSnackbar("Ticket deleted");
                    closeTicketModal();
                    reloadCurrentDatabaseBoard();
                }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
            }
        });
    }

    function sendTicketToProject() {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
            .then(function(data) {
                showSnackbar("Ticket sent to project: " + (data.project_name || ""));
            })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
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
    }

    // ── Add ticket ──

    function addTicket() {
        return ticketActions.addTicket();
    }

    // ── Copy external ticket to local board ──

    function openCopyModal(ticket) {
        return ticketActions.openCopyModal(ticket);
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

    function loadWhatsAppChats(forceRefresh) {
        return waRuntime.loadWhatsAppChats(forceRefresh);
    }

    function syncFromRelay() {
        var el = document.getElementById("kb-wa-status");
        el.textContent = "Syncing from server...";
        apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).then(function(result) {
            var newCount = Number(result && result.synced) || 0;
            if (newCount > 0) {
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
            document.getElementById("kb-board-view").classList.remove("hidden");
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

    /** Confirm and DELETE /api/kanban/whatsapp/chat/:phone — full thread wipe (shared by sidebar menu, thread header, message menu in selection mode). */
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

    function openWaLinkModal(chatData) {
        var modal = document.getElementById("kb-wa-link-modal");
        var phoneEl = document.getElementById("kb-wa-link-phone");
        var boardSelect = document.getElementById("kb-wa-link-board");

        phoneEl.textContent = (chatData.name || "") + " (" + chatData.phone + ")";

        // Populate board dropdown
        apiFetch("/api/kanban/boards").then(function(boards) {
            var dbBs = boards.filter(function(b) { return b.source === "database"; });
            boardSelect.innerHTML = '<option value="">Select a board...</option>';
            dbBs.forEach(function(b) {
                var selected = (currentBoard && currentBoard.id === b.id) ? " selected" : "";
                boardSelect.innerHTML += '<option value="' + b.id + '"' + selected + '>' + esc(b.name) + '</option>';
            });
        });

        modal.classList.remove("hidden");
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
                    apiFetch("/api/kanban/whatsapp/chats", { method: "DELETE" }).then(function(resp) {
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
        });

        document.getElementById("kb-wa-link-cancel").addEventListener("click", function() {
            document.getElementById("kb-wa-link-modal").classList.add("hidden");
        });
        document.getElementById("kb-wa-link-confirm").addEventListener("click", waManagement.confirmWaLink);

        // Message context menu
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

        // Board modal: WhatsApp tab — add link button
        document.getElementById("kb-bm-wa-add-btn").addEventListener("click", waManagement.addWaLinkFromBoardModal);
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
            loadWhatsAppChats();
        });

        // ── WebSocket: real-time WhatsApp message updates ──
        var waAutoSyncTimer = null;
        var waAutoSyncBusy = false;

        function runAutoSyncFallback() {
            if (waAutoSyncBusy) return;
            if (!isMessagesPanelVisible()) return;
            waAutoSyncBusy = true;
            apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).then(function(result) {
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
                        apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).finally(function() {
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
        // Apply sidebar tab from URL before any initial board rendering to avoid tab flicker.
        var initialSidebarTab = boardSettings.getSidebarTabFromUrl();
        // Don't auto-switch to messages on init — WhatsApp may not be available,
        // and hiding the board view causes a blank screen. Only auto-switch if WA is already known connected.
        if (initialSidebarTab === "messages" && waConnected) {
            switchSidebarTab("messages");
        }

        // Board sidebar
        document.getElementById("kb-add-board").addEventListener("click", function() { boardSettings.openBoardModal(null); });
        document.getElementById("kb-create-big").addEventListener("click", function() { boardSettings.openBoardModal(null); });
        function runCheckinFromButton() {
            var buttons = Array.prototype.slice.call(document.querySelectorAll(".kb-checkin-btn"));
            buttons.forEach(function(btn) {
                btn.disabled = true;
                btn.textContent = "Running…";
            });
            apiFetch("/api/kanban/agent/checkin", { method: "POST" }).then(function(data) {
                showSnackbar(data.message || "Check-in complete");
                var statusBoardId = getCurrentStatusBoardId();
                if (statusBoardId) startAgentStatusPolling(statusBoardId);
            }).catch(function(e) {
                showSnackbar("Check-in failed: " + e.message, "error");
            }).finally(function() {
                buttons.forEach(function(btn) {
                    btn.disabled = false;
                    btn.textContent = "Check-in";
                });
            });
        }
        document.querySelectorAll(".kb-checkin-btn").forEach(function(btn) {
            btn.addEventListener("click", function() { runCheckinFromButton(); });
        });
        boardSettings.bindTopLevel();
        boardSettings.bindGlobalSettings();
        boardSettings.bindBoardActions();

        // Ticket modal tabs
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchTicketTab(btn.dataset.ttab); });
        });

        boardSettings.bindBoardModal();

        // Ticket modal
        document.getElementById("kb-modal-close").addEventListener("click", closeTicketModal);
        document.getElementById("kb-modal-save").addEventListener("click", saveTicket);
        document.getElementById("kb-modal-delete").addEventListener("click", deleteTicket);
        // Modal action buttons (copy, CLI, send-to-project, transfer)
        document.getElementById("kb-modal-act-copy").addEventListener("click", function() {
            if (modalTicketId) {
                // Local ticket
                apiFetch("/api/kanban/tickets/" + modalTicketId).then(function(t) {
                    var text = t.title + (t.description ? "\n\n" + t.description : "");
                    if (t.files && t.files.length) {
                        text += "\n\nAttachments:\n" + t.files.map(function(f) { return "- " + (f.download_url || f.url); }).join("\n");
                    }
                    navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
                });
            } else if (window._extTicketData) {
                // External ticket
                var ext = window._extTicketData;
                var text = ext.title + (ext.description ? "\n\n" + stripHtml(ext.description) : "");
                if (ext.media && ext.media.length) {
                    text += "\n\nAttachments:\n" + ext.media.map(function(m) { return "- " + (m.url || m.download_url || ""); }).join("\n");
                }
                navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
            }
        });
        document.getElementById("kb-modal-act-cli").addEventListener("click", function() {
            if (modalTicketId) {
                pushTicketToCli(modalTicketId, document.getElementById("kb-modal-act-cli"));
            } else if (window._extTicketData) {
                copyAndPushExternalTicket(window._extTicketData, window._extTicketSource, 'cli');
            }
        });
        document.getElementById("kb-modal-act-project").addEventListener("click", function() {
            if (modalTicketId) {
                apiFetch("/api/kanban/tickets/" + modalTicketId + "/send-to-project", { method: "POST" })
                    .then(function(r) { showSnackbar("Sent to project: " + (r.project_name || "")); })
                    .catch(function(e) { showSnackbar("Error: " + e.message, "error"); });
            } else if (window._extTicketData) {
                copyAndPushExternalTicket(window._extTicketData, window._extTicketSource, 'project');
            }
        });
        // Transfer/copy button (external tickets)
        var transferBtn = document.getElementById("kb-modal-transfer-ext");
        if (transferBtn) transferBtn.addEventListener("click", function() {
            if (window._extTicketData) {
                openCopyModal(window._extTicketData);
                closeTicketModal();
            }
        });
        document.getElementById("kb-ticket-modal").addEventListener("click", function(e) {
        });

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

        // Confirm modal (ticket delete)
        document.getElementById("kb-confirm-cancel").addEventListener("click", hideKanbanConfirm);
        document.getElementById("kb-confirm-ok").addEventListener("click", function() {
            modalHelpers.invokeConfirmAction();
        });
        document.getElementById("kb-confirm-modal").addEventListener("click", function(e) {
            if (e.target === this) hideKanbanConfirm();
        });
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

        // Copy modal
        document.getElementById("kb-copy-cancel").addEventListener("click", closeCopyModal);
        document.getElementById("kb-copy-confirm").addEventListener("click", confirmCopy);
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
    }

    // Auto-refresh provider dropdowns when third-party keys are saved (same page)
    window.addEventListener('thirdparty-providers-changed', function() {
        boardSettings.loadAgentProviders();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
