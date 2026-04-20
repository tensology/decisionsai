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

    function esc(s) { var d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }
    /** True if this message is already tied to a Kanban ticket (direct link or same snapshot batch). */
    function waMsgHasLinkedTicket(msg) {
        if (!msg) return false;
        if (msg.has_ticket === true || msg.has_ticket === 1) return true;
        if (typeof msg.has_ticket === "string") {
            var hs = msg.has_ticket.toLowerCase().trim();
            if (hs === "true" || hs === "1") return true;
        }
        var tid = msg.ticket_id;
        if (tid != null && tid !== "") {
            var n = Number(tid);
            if (!isNaN(n) && n > 0) return true;
        }
        return false;
    }
    function waIsMessageSelectable(msg) {
        return !!msg && !waMsgHasLinkedTicket(msg);
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
    function waDownloadJsonFile(filename, payload) {
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
    }
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
    function dedupeThreadMessages(messages) {
        var seen = {};
        return messages.filter(function(msg) {
            var key = [
                msg.jid || "",
                msg.from_me ? "1" : "0",
                String(msg.whatsapp_timestamp || ""),
                msg.text || "",
                msg.caption || "",
                msg.media_type || "",
                msg.media_filename || ""
            ].join("|");
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
    }

    /** Strip HTML tags from a string, decode entities, and trim. */
    function stripHtml(html) {
        if (!html) return "";
        // If it's a Jira ADF object (not a string), try to extract plain text
        if (typeof html === "object") {
            var parts = [];
            (function walk(node) {
                if (Array.isArray(node)) { node.forEach(walk); return; }
                if (typeof node === "object" && node !== null) {
                    if (node.type === "text") { parts.push(node.text || ""); }
                    if (node.type === "hardBreak" || node.type === "paragraph") { parts.push("\n"); }
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

    /** Truncate text to maxLen characters, adding ellipsis if truncated. */
    function truncate(s, maxLen) {
        if (!s) return "";
        s = s.replace(/\s+/g, " ").trim();
        return s.length > maxLen ? s.substring(0, maxLen) + "…" : s;
    }

    var apiFetch = window.DecisionsAPI.fetch;
    function showSnackbar(msg, type) { window.DecisionsAPI.snackbar(msg, type, { id: "kb-snackbar" }); }

    /** In-app confirm modal (matches Ticket Board styling). opts: { title, message, confirmLabel, danger, onConfirm } */
    var _kbConfirmCallback = null;
    function showKanbanConfirm(opts) {
        opts = opts || {};
        document.getElementById("kb-confirm-title").textContent = opts.title || "Confirm";
        document.getElementById("kb-confirm-message").textContent = opts.message || "";
        var okBtn = document.getElementById("kb-confirm-ok");
        okBtn.textContent = opts.confirmLabel || "OK";
        okBtn.className = opts.danger
            ? "px-4 py-2 rounded text-white text-sm bg-red-600 hover:bg-red-700"
            : "px-4 py-2 rounded text-white text-sm bg-[#f97316] hover:bg-[#ea580c]";
        _kbConfirmCallback = typeof opts.onConfirm === "function" ? opts.onConfirm : null;
        document.getElementById("kb-confirm-modal").classList.remove("hidden");
        requestAnimationFrame(function() {
            try { okBtn.focus(); } catch (err) {}
        });
    }
    function hideKanbanConfirm() {
        _kbConfirmCallback = null;
        document.getElementById("kb-confirm-modal").classList.add("hidden");
    }
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
    var CACHE_TTL = 60000; // 1 minute

    function loadBoards(forceRefresh) {
        var now = Date.now();
        var boardsStale = forceRefresh || !_boardsCache || (now - _boardsCacheTime > CACHE_TTL);
        var externalStale = forceRefresh || !_externalCache || (now - _externalCacheTime > CACHE_TTL);

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
            apiFetch("/api/kanban/external-boards").then(function(data) {
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
            div.ondblclick = function() { openBoardModal(b.id); };
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
            div.oncontextmenu = function(e) { e.preventDefault(); showBoardContextMenu(e, b.id); };
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
        openExternalBoardConfigModal(src, bid);
    }

    // ── Create external ticket modal ──
    function openCreateExternalTicketModal() {
        if (!currentBoard || !currentBoard.source || currentBoard.source === "database") {
            showSnackbar("Select a Trello or Jira board first", "error"); return;
        }
        if (currentBoardData && currentBoardData.can_create_ticket === false) {
            showSnackbar("You do not have permission to create tickets on this board", "error"); return;
        }
        var modal = document.getElementById("kb-create-ext-ticket-modal");
        document.getElementById("kb-cet-title").value = "";
        document.getElementById("kb-cet-desc").value = "";
        document.getElementById("kb-cet-lane").innerHTML = '<option value="">Select a list/column...</option>';
        if (currentBoardData && currentBoardData.lanes) {
            currentBoardData.lanes.forEach(function(lane) {
                var opt = document.createElement("option");
                opt.value = lane.id; opt.textContent = lane.name;
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
    
    // ── Tab switching for create ticket modal ────────────────────────────
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

    // ── Distill WhatsApp messages via LLM ────────────────────────────────
    function composeWaTicket(messageIds, titleEl, descEl, statusEl) {
        titleEl = titleEl || document.getElementById("kb-cet-title");
        descEl = descEl || document.getElementById("kb-cet-desc");
        statusEl = statusEl || document.getElementById("kb-cet-distill-status");
        if (!messageIds || !messageIds.length) return;
        apiFetch("/api/kanban/whatsapp/compose-ticket", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message_ids: messageIds})
        }).then(function(r) {
            waTicketComposeInFlight = false;
            setCetLoadingOverlay(false);
            // Clear loading state — re-enable fields and button
            titleEl.disabled = false;
            descEl.disabled = false;
            titleEl.placeholder = "Ticket title";
            descEl.placeholder = "Describe the ticket...";
            var submitBtn = document.getElementById("kb-cet-submit");
            submitBtn.disabled = false;
            submitBtn.classList.remove("opacity-50", "cursor-not-allowed");

            if (r.title) titleEl.value = r.title;
            if (r.description) descEl.value = r.description;

            // Populate media tab
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
                    if (m.media_type === "photo" || m.media_type === "image") {
                        preview = '<img src="' + m.media_path + '" class="w-10 h-10 object-cover rounded" onerror="this.style.display=\'none\'">';
                    } else {
                        preview = '<div class="w-10 h-10 flex items-center justify-center bg-white/5 rounded text-lg">' + icon + '</div>';
                    }
                    item.innerHTML = preview + '<div class="flex-1 min-w-0"><div class="text-sm text-white truncate">' + esc(m.media_filename) + '</div><div class="text-xs text-gray-500">' + esc(m.media_type) + '</div></div><input type="hidden" name="wa-media" value="' + m.message_id + '">';
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
        }).catch(function(e) {
            waTicketComposeInFlight = false;
            setCetLoadingOverlay(false);
            // Clear loading state on error too
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

    function setCetLoadingOverlay(isLoading) {
        var overlay = document.getElementById("kb-cet-loading-overlay");
        if (!overlay) return;
        if (isLoading) {
            overlay.classList.remove("hidden");
        } else {
            overlay.classList.add("hidden");
        }
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
function closeCreateExtTicketModal() {
        var modal = document.getElementById("kb-create-ext-ticket-modal");
        modal.classList.add("hidden");
        waTicketComposeInFlight = false;
        setCetLoadingOverlay(false);
        // Reset WhatsApp metadata
        delete modal.dataset.whatsappPhone;
        delete modal.dataset.whatsappMsgIds;
        delete modal.dataset.whatsappBoardId;
        // Hide the board selector (it's only shown for WhatsApp)
        var boardRow = document.getElementById("kb-cet-board-row");
        if (boardRow) boardRow.style.display = "none";
        // Reset tabs to details
        cetSwitchTab("details");
        // Clear media previews
        var mediaContainer = document.getElementById("kb-cet-wa-media");
        if (mediaContainer) mediaContainer.innerHTML = "";
        var countEl = document.getElementById("kb-cet-attach-count");
        if (countEl) countEl.classList.add("hidden");
        // Clear distill status
        var statusEl = document.getElementById("kb-cet-distill-status");
        if (statusEl) statusEl.textContent = "";
        // Re-enable title/desc/submit in case they were disabled for loading
        var titleEl = document.getElementById("kb-cet-title");
        var descEl = document.getElementById("kb-cet-desc");
        var submitBtn = document.getElementById("kb-cet-submit");
        if (titleEl) { titleEl.disabled = false; titleEl.placeholder = "Ticket title"; }
        if (descEl) { descEl.disabled = false; descEl.placeholder = "Describe the ticket..."; }
        if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove("opacity-50", "cursor-not-allowed"); }
    }
    function submitCreateExtTicket() {
        var title = document.getElementById("kb-cet-title").value.trim();
        if (!title) { showSnackbar("Title is required", "error"); return; }
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

        // Database board — create ticket locally
        if (boardSource === "database") {
            if (!laneId) { showSnackbar("Select a lane", "error"); return; }
            apiFetch("/api/kanban/tickets", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    title: title,
                    description: desc,
                    lane_id: parseInt(laneId),
                    priority: priority || "medium",
                    board_id: boardId
                })
            }).then(function(r) {
                if (!r || !r.success) { showSnackbar("Failed to create ticket", "error"); return; }
                showSnackbar("Ticket created");
                // Mark WhatsApp messages as snapshotted
                if (waPhone) {
                    var msgIdList = JSON.parse(waMsgIds);
                    apiFetch("/api/kanban/whatsapp/messages/mark-snapshot-group", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            jid_phone: waPhone,
                            snapshot_group: r.id + "_" + r.lane_id,
                            message_ids: msgIdList
                        })
                    }).catch(function() {});
                    // Attach WhatsApp media to the ticket
                    var mediaEls = document.querySelectorAll("#kb-cet-wa-media input[name=wa-media]");
                    mediaEls.forEach(function(el) {
                        var msgId = parseInt(el.value);
                        apiFetch("/api/kanban/tickets/" + r.id + "/attach-whatsapp-media", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({ message_id: msgId })
                        }).catch(function() {});
                    });
                }
                closeCreateExtTicketModal();
                selectBoard("database", boardId);
                if (waPhone) {
                    refreshWaThreadIfOpen();
                }
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
            return;
        }

        // External board (Trello/Jira) — use the selected board info
        var extSource = boardSource;
        var extBoardId = boardId;
        // Look up the board name from the dropdown for the API call
        var boardSelect = document.getElementById("kb-cet-board");
        var selectedOption = boardSelect ? boardSelect.options[boardSelect.selectedIndex] : null;
        var extBoardName = selectedOption ? selectedOption.textContent : "";

        // We need to find the external board's actual ID from our board list
        apiFetch("/api/kanban/external-boards").then(function(extData) {
            var allExt = (extData.trello || []).concat(extData.jira || []);
            var extBoard = allExt.find(function(b) { return b.source === extSource && b.id === extBoardId; });
            if (!extBoard) {
                // Try by name match
                extBoard = allExt.find(function(b) { return b.source === extSource && b.name === extBoardName; });
            }
            if (!extBoard) { showSnackbar("Could not find " + extSource + " board", "error"); return; }

            var payload = { title: title, description: desc, lane_id: laneId || null, priority: priority };
            var fileInput = document.getElementById("kb-cet-file-input");
            var files = fileInput.files;
            showSnackbar("Creating ticket on " + (extSource === "trello" ? "Trello" : "Jira") + "...");

            apiFetch("/api/kanban/external-boards/" + extSource + "/" + encodeURIComponent(extBoard.id) + "/create-ticket", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function(r) {
                if (r.success && r.ticket) {
                    // Mark WhatsApp messages as snapshotted
                    if (waPhone) {
                        var msgIdList = JSON.parse(waMsgIds);
                        apiFetch("/api/kanban/whatsapp/messages/mark-snapshot-group", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                jid_phone: waPhone,
                                snapshot_group: "ext_" + r.ticket.id,
                                message_ids: msgIdList
                            })
                        }).catch(function() {});
                    }
                    if (files && files.length > 0 && r.ticket.id) {
                        return uploadExtTicketAttachments(extSource, r.ticket.id, files).then(function() { return r; });
                    }
                    return r;
                }
                return r;
            }).then(function() {
                showSnackbar("Ticket created on " + (extSource === "trello" ? "Trello" : "Jira"));
                closeCreateExtTicketModal();
                selectBoard(extSource, extBoard.id, extBoard.url || "");
                if (waPhone) {
                    refreshWaThreadIfOpen();
                }
            }).catch(function(e) { showSnackbar("Failed to create ticket: " + e.message, "error"); });
        }).catch(function(e) { showSnackbar("Failed to load external boards: " + e.message, "error"); });
    }
    function uploadExtTicketAttachments(source, extTicketId, files) {
        var promises = [];
        for (var i = 0; i < files.length; i++) {
            var fd = new FormData();
            fd.append("file", files[i]);
            promises.push(apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(extTicketId) + "/attach", {
                method: "POST", body: fd
            }).catch(function(e) { console.error("Failed to upload attachment:", e); }));
        }
        return Promise.all(promises);
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
            div.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>' + esc(f.name) + ' <span class="text-gray-600">(' + (f.size < 1024*1024 ? (f.size/1024).toFixed(1) + 'KB' : (f.size/1024/1024).toFixed(1) + 'MB') + ')</span>';
            listDiv.appendChild(div);
        }
    }

    // ── Board context menu (right-click) ──

    function showBoardContextMenu(e, boardId) {
        ctxMenuBoardId = boardId;
        var menu = document.getElementById("kb-board-ctx-menu");
        menu.style.left = e.clientX + "px";
        menu.style.top = e.clientY + "px";
        menu.classList.remove("hidden");
    }
    function ctxConfigureBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        openBoardModal(boardId);
    }

    function hideBoardContextMenu() {
        document.getElementById("kb-board-ctx-menu").classList.add("hidden");
        ctxMenuBoardId = null;
    }

    function ctxRenameBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        var board = dbBoards.find(function(b) { return b.id === boardId; });
        var newName = prompt("Rename board:", board ? board.name : "");
        if (!newName || !newName.trim()) { hideBoardContextMenu(); return; }
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName.trim() })
        }).then(function() {
            showSnackbar("Board renamed");
            loadBoards(true);
            if (currentBoard && currentBoard.id === boardId) selectBoard("database", boardId);
        }).catch(function(e) { showSnackbar("Rename failed: " + e.message, "error"); });
    }

    function ctxEditBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        openBoardModal(boardId);
    }

    function ctxArchiveBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId + "/archive", { method: "POST" }).then(function() {
            showSnackbar("Board archived");
            if (currentBoard && currentBoard.id === boardId) {
                currentBoard = null; currentBoardData = null;
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
            }
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Archive failed: " + e.message, "error"); });
    }

    function ctxDeleteBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        var board = dbBoards.find(function(b) { return b.id === boardId; });
        var name = board ? board.name : "this board";
        if (!confirm('Delete board "' + name + '" and all its tickets? This cannot be undone.')) { hideBoardContextMenu(); return; }
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            if (currentBoard && currentBoard.id === boardId) {
                currentBoard = null; currentBoardData = null;
                document.getElementById("kb-board-view").classList.add("hidden");
                document.getElementById("kb-loading").classList.add("hidden");
                document.getElementById("kb-empty").classList.remove("hidden");
            }
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Delete failed: " + e.message, "error"); });
    }

    function ctxActivateBoard() {
        if (!ctxMenuBoardId) return;
        var boardId = ctxMenuBoardId;
        hideBoardContextMenu();
        apiFetch("/api/kanban/boards/" + boardId + "/use", { method: "POST" }).then(function(data) {
            showSnackbar("Board set as active");
            loadBoards(true);
            if (data && data.linked_project) {
                if (confirm('This board is linked to project "' + data.linked_project.name + '". Activate that project too?')) {
                    apiFetch("/api/projects/" + data.linked_project.id + "/use", { method: "POST" }).then(function() {
                        showSnackbar("Project activated");
                    }).catch(function() {});
                }
            }
        }).catch(function(e) { showSnackbar("Activate failed: " + e.message, "error"); });
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
        // Populate the modal with external ticket data
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
        // For external tickets, render description as HTML (Jira ADF→HTML done by backend, Trello is native HTML)
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
                img.style.maxWidth = "100%"; img.style.borderRadius = "4px"; img.loading = "lazy";
            });
            richDiv.querySelectorAll("a").forEach(function(a) {
                a.target = "_blank"; a.rel = "noopener noreferrer";
            });
            descArea.parentElement.insertBefore(richDiv, descArea.nextSibling);
        } else {
            var prevRich = descArea.parentElement.querySelector(".kb-ext-rich-desc");
            if (prevRich) prevRich.remove();
            descArea.value = stripHtml(rawDesc);
            descArea.readOnly = true;
            descArea.classList.add("bg-[#152054]/50", "cursor-not-allowed");
            descArea.style.display = "";
        }

        // Hide priority editing for external
        document.querySelectorAll("#kb-modal-priority-btns button").forEach(function(btn) {
            btn.classList.add("opacity-50", "cursor-not-allowed");
            btn.disabled = true;
        });
        setPriorityButtons(ticket.priority || "medium");

        // Clear links & show media/todos
        renderModalLinks([]);
        renderExternalMedia(ticket.media || [], source);
        renderExternalTodos(ticket.todos || []);

        // Show external metadata
        var metaContainer = document.getElementById("kb-modal-external-meta");
        if (metaContainer) {
            var metaHtml = '';
            if (ticket.url) {
                metaHtml += '<div class="flex items-center gap-2"><span class="text-xs text-gray-500">Source:</span><a href="' + esc(ticket.url) + '" target="_blank" class="text-xs text-[#f97316] hover:underline">Open in ' + esc(source.charAt(0).toUpperCase() + source.slice(1)) + '</a></div>';
            }
            if (ticket.members && ticket.members.length) {
                metaHtml += '<div class="text-xs text-gray-400">Members: ' + ticket.members.map(esc).join(', ') + '</div>';
            }
            if (ticket.labels && ticket.labels.length) {
                metaHtml += '<div class="flex flex-wrap gap-1">' + ticket.labels.map(function(lb) { return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + esc(lb) + '</span>'; }).join('') + '</div>';
            }
            if (ticket.time_estimate || ticket.time_spent) {
                metaHtml += '<div class="text-xs text-gray-400">';
                if (ticket.time_estimate) metaHtml += 'Estimate: ' + esc(ticket.time_estimate);
                if (ticket.time_spent) metaHtml += ' | Spent: ' + esc(ticket.time_spent);
                metaHtml += '</div>';
            }
            if (ticket.reporter) {
                metaHtml += '<div class="text-xs text-gray-400">Reporter: ' + esc(ticket.reporter) + '</div>';
            }
            metaContainer.innerHTML = metaHtml;
            metaContainer.classList.remove("hidden");
        }

        // Show transfer/copy button, hide save/delete
        document.getElementById("kb-modal-transfer-ext").classList.remove("hidden");
        document.getElementById("kb-modal-save").classList.add("hidden");
        document.getElementById("kb-modal-delete").classList.add("hidden");
        // Change title to show source
        document.getElementById("kb-modal-title").textContent = ticket.title || "Ticket";

        // Store external ticket data for actions
        window._extTicketData = ticket;
        window._extTicketSource = source;

        // Show modal
        switchTicketTab("details");
        document.getElementById("kb-ticket-modal").classList.remove("hidden");
    }

    /** Render media attachments for external tickets. */
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
                imgUrl = '/api/kanban/external-boards/jira/proxy-image?url=' + encodeURIComponent(imgUrl);
                thumbUrl = m.thumbnail ? '/api/kanban/external-boards/jira/proxy-image?url=' + encodeURIComponent(m.thumbnail) : imgUrl;
            }
            if (mtype === "image" && thumbUrl) {
                html += '<div class="flex items-start gap-2 p-2 bg-[#152054] rounded border border-white/10">';
                html += '<a href="' + esc(imgUrl) + '" target="_blank" class="flex-shrink-0"><img src="' + esc(thumbUrl) + '" alt="' + esc(m.name || 'image') + '" class="max-w-[160px] max-h-[120px] object-cover rounded border border-white/10" loading="lazy"/></a>';
                html += '<div class="flex-1 min-w-0">';
                html += '<div class="text-xs text-gray-300 truncate">' + esc(m.name || 'Attachment') + '</div>';
                html += '<div class="text-[10px] text-gray-500">' + esc(m.type || '') + '</div>';
                html += '</div></div>';
            } else {
                html += '<div class="flex items-center gap-2 p-2 bg-[#152054] rounded border border-white/10">';
                html += '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="flex-shrink-0 text-gray-400"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>';
                html += '<a href="' + esc(imgUrl) + '" target="_blank" class="text-xs text-blue-400 hover:underline truncate">' + esc(m.name || 'Download') + '</a>';
                html += '</div>';
            }
        });
        html += '</div>';
        container.innerHTML = html;
    }

    /** Render todos from external sources (read-only). */
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
                '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + esc(todo.text) + '</span>';
            container.appendChild(row);
        });
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
        boardData = boardData || currentBoardData || {};
        var card = document.createElement("div");
        card.className = "kb-card bg-[#1a1f3a] rounded-lg border border-white/20 p-3 cursor-pointer hover:border-[#f97316]/50 transition-colors relative";
        card.dataset.ticketId = String(ticket.id);
        if (isLocal) {
            card.draggable = true;
            card.addEventListener("dragstart", function(e) {
                e.dataTransfer.setData("text/plain", String(ticket.id));
                card.classList.add("dragging");
            });
            card.addEventListener("dragend", function() { card.classList.remove("dragging"); });
        }
        if (
            currentAgentStatus &&
            currentAgentStatus.state !== "idle" &&
            currentAgentStatus.current_ticket_id != null &&
            String(currentAgentStatus.current_ticket_id) === String(ticket.id)
        ) {
            card.classList.add("kb-in-progress");
        }
        var pri = (ticket.priority || "medium").toLowerCase();
        var priClass = "kb-pri-" + pri;
        // Strip HTML from description for display
        var cleanDesc = stripHtml(ticket.description || "");
        var truncatedDesc = truncate(cleanDesc, 120);
        var todoCount = (ticket.todos || []).length;
        var todoDone = (ticket.todos || []).filter(function(t) { return t.done; }).length;
        var todoHtml = todoCount ? '<span class="text-xs text-gray-500 ml-2">✓ ' + todoDone + '/' + todoCount + '</span>' : '';
        // Labels for external tickets
        var labelsHtml = '';
        if (ticket.labels && ticket.labels.length) {
            labelsHtml = '<div class="flex flex-wrap gap-1 mt-1">' +
                ticket.labels.map(function(lb) { return '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300">' + esc(lb) + '</span>'; }).join('') +
                '</div>';
        }
        // Members for external tickets
        var membersHtml = '';
        if (ticket.members && ticket.members.length) {
            membersHtml = '<div class="flex items-center gap-1 mt-1">' +
                ticket.members.map(function(m) { return '<span class="text-[10px] text-gray-400">' + esc(m) + '</span>'; }).join(', ') +
                '</div>';
        }
        // Time tracking for Jira
        var timeHtml = '';
        if (ticket.time_estimate || ticket.time_spent) {
            timeHtml = '<div class="text-[10px] text-gray-500 mt-1">';
            if (ticket.time_estimate) timeHtml += '⏱ ' + esc(ticket.time_estimate);
            if (ticket.time_spent) timeHtml += ' / ' + esc(ticket.time_spent) + ' done';
            timeHtml += '</div>';
        }
        // Media (attachments/images) from external ticket
        var mediaHtml = '';
        if (ticket.media && ticket.media.length) {
            mediaHtml = '<div class="flex flex-wrap gap-1 mt-1">';
            ticket.media.forEach(function(m) {
                var imgUrl = m.url;
                var thumbUrl = m.thumbnail || m.url;
                if (!isLocal && currentBoard && currentBoard.source === 'jira' && imgUrl) {
                    imgUrl = '/api/kanban/external-boards/jira/proxy-image?url=' + encodeURIComponent(imgUrl);
                    thumbUrl = m.thumbnail ? '/api/kanban/external-boards/jira/proxy-image?url=' + encodeURIComponent(m.thumbnail) : imgUrl;
                }
                var mtype = (m.type || '').startsWith('video/') ? 'video' : 'image';
                if (mtype === 'image') {
                    mediaHtml += '<div class="relative rounded overflow-hidden border border-white/10">';
                    mediaHtml += '<img src="' + esc(thumbUrl || imgUrl) + '" alt="' + esc(m.name || 'attachment') + '" class="max-w-[80px] max-h-[60px] object-cover rounded" loading="lazy" onerror="this.style.display=\'none\'">';
                    mediaHtml += '</div>';
                } else {
                    mediaHtml += '<a href="' + esc(imgUrl) + '" target="_blank" class="text-[10px] text-blue-400 hover:underline flex items-center gap-0.5">';
                    mediaHtml += '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>' + esc(m.name || 'file') + '</a>';
                }
            });
            mediaHtml += '</div>';
        }
        // Determine if project-linked actions should show
        var hasProject = !!(boardData.default_project_id || (isLocal && boardData.id));
        // Source badge for external tickets
        var sourceBadge = '';
        if (!isLocal && currentBoard.source) {
            var srcColor = currentBoard.source === 'trello' ? '#0079bf' : '#0052cc';
            sourceBadge = '<span class="text-[9px] px-1 py-0.5 rounded text-white" style="background:' + srcColor + '">' + esc(currentBoard.source) + '</span>';
        }
        // External URL link icon
        var extLinkHtml = '';
        if (!isLocal && ticket.url) {
            extLinkHtml = '<a href="' + esc(ticket.url) + '" target="_blank" class="text-gray-500 hover:text-blue-400 transition-colors" title="Open in ' + esc(currentBoard.source || 'browser') + '" onclick="event.stopPropagation()">' +
                '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>';
        }
        card.innerHTML = '<div class="flex items-start justify-between gap-2">' +
            '<span class="text-sm text-white leading-snug flex-1">' + esc(ticket.title) + '</span>' +
            '<div class="flex items-center gap-1.5 flex-shrink-0">' + sourceBadge + '<span class="' + priClass + ' text-[10px] px-1.5 py-0.5 rounded text-white font-medium">' + esc(pri) + '</span>' + extLinkHtml + '</div>' +
            '</div>' +
            (truncatedDesc ? '<p class="text-xs text-gray-500 mt-1 line-clamp-2">' + esc(truncatedDesc) + '</p>' : '') +
            labelsHtml + membersHtml + timeHtml + mediaHtml +
            '<div class="flex items-center justify-center gap-2 mt-2 kb-card-actions">' +
                '<button class="kb-act-copy text-gray-500 hover:text-white transition-colors" title="Copy title & description"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>' +
                (hasProject ? '<button class="kb-act-cli text-gray-500 hover:text-orange-400 transition-colors" title="Push to CLI"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>' : '<button class="kb-act-cli text-gray-700 cursor-not-allowed" title="Push to CLI (link a project to this board first)" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>') +
                (hasProject ? '<button class="kb-act-project text-gray-500 hover:text-blue-400 transition-colors" title="Send to Project (.tickets)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></button>' : '<button class="kb-act-project text-gray-700 cursor-not-allowed" title="Send to Project (.tickets) — link a project to this board first" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/<polyline points="14 2 14 8 20 8"/></svg></button>') +
                (!isLocal ? '<button class="kb-act-transfer text-gray-500 hover:text-green-400 transition-colors" title="Copy to local board"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 16H3v-5"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg></button>' : '') +
                (isLocal ? '<button class="kb-act-delete text-gray-500 hover:text-red-400 transition-colors" title="Delete ticket"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>' : '<button class="kb-act-delete text-gray-700 cursor-not-allowed" title="Delete not available for external tickets" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>') +
            '</div>' +
            '<div class="flex items-center mt-1">' + todoHtml + '</div>';
        // Click handler: open modal for ALL tickets (local and external)
        card.addEventListener("click", function(e) {
            if (e.target.closest(".kb-card-actions") || e.target.closest(".kb-act-transfer") || e.target.closest("a")) return;
            if (isLocal) {
                openTicketModal(ticket.id);
            } else {
                openExternalTicketModal(ticket, currentBoard.source);
            }
        });
        // Wire up action buttons
        // Copy title + description
        var copyBtn = card.querySelector(".kb-act-copy");
        if (copyBtn) copyBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            var text = stripHtml(ticket.title) + (cleanDesc ? "\n\n" + cleanDesc : "");
            navigator.clipboard.writeText(text).then(function() { showSnackbar("Copied to clipboard"); });
        });
        // Push to CLI
        var cliBtn = card.querySelector(".kb-act-cli");
        if (cliBtn && !cliBtn.disabled) cliBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            if (isLocal) {
                pushTicketToCli(ticket.id, cliBtn);
            } else {
                // External ticket: copy to local board first, then push
                copyAndPushExternalTicket(ticket, currentBoard.source, 'cli');
            }
        });
        // Send to Project
        var projectBtn = card.querySelector(".kb-act-project");
        if (projectBtn && !projectBtn.disabled) projectBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            if (isLocal) {
                sendTicketToProjectById(ticket.id);
            } else {
                // External ticket: copy to local board first, then send
                copyAndPushExternalTicket(ticket, currentBoard.source, 'project');
            }
        });
        // Transfer/Copy to local board (external only)
        var transferBtn = card.querySelector(".kb-act-transfer");
        if (transferBtn) transferBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            openCopyModal(ticket);
        });
        // Delete (local only)
        var delBtn = card.querySelector(".kb-act-delete");
        if (delBtn && isLocal) delBtn.addEventListener("click", function(e) {
            e.stopPropagation();
            var tid = ticket.id;
            showKanbanConfirm({
                title: "Delete ticket",
                message: "Delete \"" + ticket.title + "\"? This cannot be undone.",
                confirmLabel: "Delete",
                danger: true,
                onConfirm: function() {
                    hideKanbanConfirm();
                    apiFetch("/api/kanban/tickets/" + tid, { method: "DELETE" })
                        .then(function() {
                            showSnackbar("Ticket deleted");
                            reloadCurrentDatabaseBoard();
                        })
                        .catch(function(err) { showSnackbar("Delete failed: " + err.message, "error"); });
                }
            });
        });
        return card;
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

    function isValidTimeTrackingValue(value) {
        var v = (value || "").trim();
        if (!v) return true;
        // Jira-style: 30m, 2h, 1d 3h, 1w 2d 4h 30m
        return /^\d+\s*[wdhm](\s+\d+\s*[wdhm])*$/i.test(v);
    }

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

    function renderModalLinks(links) {
        var container = document.getElementById("kb-modal-links");
        container.innerHTML = "";
        links.forEach(function(link) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<a href="' + esc(link.url) + '" target="_blank" class="text-[#f97316] hover:underline flex-1 truncate">' + esc(link.title) + '</a>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("button").onclick = function() { deleteLink(link.id); };
            container.appendChild(row);
        });
    }

    function addLink() {
        if (!modalTicketId) return;
        var title = document.getElementById("kb-modal-link-title").value.trim();
        var url = document.getElementById("kb-modal-link-url").value.trim();
        if (!title || !url) { showSnackbar("Title and URL required", "error"); return; }
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/links", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: title, url: url })
        }).then(function() {
            document.getElementById("kb-modal-link-title").value = "";
            document.getElementById("kb-modal-link-url").value = "";
            refreshModalTicket();
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    function deleteLink(linkId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/links/" + linkId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Modal: Files ──

    function renderModalFiles(files) {
        var container = document.getElementById("kb-modal-files");
        container.innerHTML = "";
        files.forEach(function(f) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<span class="text-gray-300 flex-1 truncate">📎 ' + esc(f.filename) + '</span>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("button").onclick = function() { deleteFile(f.id); };
            container.appendChild(row);
        });
    }

    function uploadFiles(fileList) {
        if (!modalTicketId || !fileList.length) return;
        var promises = [];
        for (var i = 0; i < fileList.length; i++) {
            var form = new FormData();
            form.append("file", fileList[i]);
            promises.push(apiFetch("/api/kanban/tickets/" + modalTicketId + "/files", { method: "POST", body: form }));
        }
        Promise.all(promises).then(function() {
            showSnackbar("Files uploaded"); refreshModalTicket();
        }).catch(function(e) { showSnackbar("Upload failed: " + e.message, "error"); });
    }

    function deleteFile(fileId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/files/" + fileId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); })
            .catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Modal: Todos ──

    function renderModalTodos(todos) {
        var container = document.getElementById("kb-modal-todos");
        container.innerHTML = "";
        todos.forEach(function(todo) {
            var row = document.createElement("div");
            row.className = "flex items-center gap-2 text-xs";
            row.innerHTML = '<input type="checkbox" ' + (todo.done ? "checked" : "") + ' class="accent-[#f97316]">' +
                '<span class="flex-1 ' + (todo.done ? "line-through text-gray-500" : "text-gray-300") + '">' + esc(todo.text) + '</span>' +
                '<button type="button" class="text-red-400 hover:text-red-300">&times;</button>';
            row.querySelector("input").onchange = function() { toggleTodo(todo.id, !todo.done); };
            row.querySelector("button").onclick = function() { deleteTodo(todo.id); };
            container.appendChild(row);
        });
    }

    function addTodo() {
        if (!modalTicketId) return;
        var text = document.getElementById("kb-modal-todo-input").value.trim();
        if (!text) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
        }).then(function() {
            document.getElementById("kb-modal-todo-input").value = "";
            refreshModalTicket();
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    function toggleTodo(todoId, done) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos/" + todoId, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ done: done })
        }).then(function() { refreshModalTicket(); }).catch(function() {});
    }

    function deleteTodo(todoId) {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId + "/todos/" + todoId, { method: "DELETE" })
            .then(function() { refreshModalTicket(); }).catch(function() {});
    }

    function refreshModalTicket() {
        if (!modalTicketId) return;
        apiFetch("/api/kanban/tickets/" + modalTicketId).then(function(t) {
            renderModalLinks(t.links || []);
            renderModalFiles(t.files || []);
            renderModalTodos(t.todos || []);
        }).catch(function() {});
    }

    // ── Linkable entities ──

    function loadLinkableEntities(ticket) {
        apiFetch("/api/kanban/linkable").then(function(data) {
            populateSelect("kb-modal-link-workflow", data.workflows, "id", "title", ticket.linked_workflow_id);
        }).catch(function() {});
    }

    function populateSelect(selectId, items, valKey, labelKey, selectedVal) {
        var sel = document.getElementById(selectId);
        sel.innerHTML = '<option value="">None</option>';
        (items || []).forEach(function(item) {
            var opt = document.createElement("option");
            opt.value = item[valKey];
            opt.textContent = item[labelKey] || ("Item #" + item[valKey]);
            if (selectedVal && String(item[valKey]) === String(selectedVal)) opt.selected = true;
            sel.appendChild(opt);
        });
    }

    // ── Board modal (tabbed: Details + Advanced) ──

    var _agentProviders = [];  // cached provider list

    function _populateProviderDropdowns(selectIds) {
        selectIds.forEach(function(selId) {
            var sel = document.getElementById(selId);
            if (!sel) return;
            var cur = sel.value;
            sel.innerHTML = '<option value="">(chat default)</option>';
            _agentProviders.forEach(function(p) {
                var opt = document.createElement("option");
                opt.value = p.id; opt.textContent = p.name;
                sel.appendChild(opt);
            });
            if (cur) sel.value = cur;
        });
    }

    function loadAgentProviders() {
        return apiFetch("/api/llms/available-providers").then(function(data) {
            _agentProviders = data.providers || [{ id: "ollama", name: "Ollama" }];
        }).catch(function() {
            _agentProviders = [{ id: "ollama", name: "Ollama" }];
        });
    }

    function loadAgentModels(prefix, provider, selectedModel) {
        var sel = document.getElementById(prefix + "-model");
        if (!provider) {
            sel.innerHTML = '<option value="">(chat default)</option>';
            return Promise.resolve();
        }
        sel.innerHTML = '<option value="">Loading...</option>';
        sel.disabled = true;
        return apiFetch("/api/llms/models?type=conversational&provider=" + encodeURIComponent(provider)).then(function(data) {
            var models = data.models || [];
            sel.innerHTML = '<option value="">(chat default)</option>';
            models.forEach(function(m) {
                var opt = document.createElement("option");
                opt.value = (typeof m === "object" && m.id != null) ? m.id : m;
                opt.textContent = (typeof m === "object" && m.name != null) ? m.name : (m || "");
                sel.appendChild(opt);
            });
            if (selectedModel) {
                sel.value = selectedModel;
                if (!sel.value && selectedModel) {
                    var extra = document.createElement("option");
                    extra.value = selectedModel; extra.textContent = selectedModel;
                    sel.appendChild(extra); sel.value = selectedModel;
                }
            }
        }).catch(function() {
            sel.innerHTML = '<option value="">(chat default)</option>';
        }).then(function() { sel.disabled = false; });
    }

    function loadBoardDefaults(data) {
        apiFetch("/api/kanban/linkable").then(function(ld) {
            populateSelect("kb-board-def-workflow", ld.workflows, "id", "title", data ? data.default_workflow_id : null);
            populateSelect("kb-board-def-project", ld.projects, "id", "name", data ? data.default_project_id : null);

        }).catch(function() {});
    }

    function openBoardModal(boardId) {
        // Ensure boardId is a number (not string from URL params etc.)
        boardId = boardId ? parseInt(boardId, 10) : null;
        editingBoardId = boardId;
        document.getElementById("kb-board-modal-title").textContent = boardId ? "Edit Board" : "New Board";
        document.getElementById("kb-board-modal-save").textContent = boardId ? "Save" : "Create";
        // Reset to Details tab
        switchBoardModalTab("details");

        if (boardId && currentBoardData && currentBoardData.id == boardId) {
            populateBoardModal(currentBoardData);
        } else if (boardId) {
            apiFetch("/api/kanban/boards/" + boardId).then(function(data) {
                populateBoardModal(data);
            }).catch(function() {});
        } else {
            document.getElementById("kb-board-modal-name").value = "";
            document.getElementById("kb-board-modal-desc").value = "";
            document.getElementById("kb-board-modal-agent-enabled").checked = false;
            var colorInput = document.getElementById("kb-board-modal-color");
            var colorHex = document.getElementById("kb-board-modal-color-hex");
            colorInput.value = "#f97316";
            colorHex.textContent = "#f97316";
            loadBoardDefaults(null);
        }
        document.getElementById("kb-board-modal").classList.remove("hidden");
        if (typeof injectInfoIcons === 'function') injectInfoIcons();
    }

    function populateBoardModal(data) {
        document.getElementById("kb-board-modal-name").value = data.name || "";
        document.getElementById("kb-board-modal-desc").value = data.description || "";
        document.getElementById("kb-board-modal-agent-enabled").checked = !!data.agent_enabled;
        var colorInput = document.getElementById("kb-board-modal-color");
        var colorHex = document.getElementById("kb-board-modal-color-hex");
        var c = data.color || "#f97316";
        colorInput.value = c;
        colorHex.textContent = c;
        loadBoardDefaults(data);
    }

    /** Open config modal for an external (Trello/Jira) board.
     *  This creates/updates a local KanbanBoard record to store project, workflow, color, agent config.
     */
    function openExternalBoardConfigModal(provider, extBoardId) {
        apiFetch("/api/kanban/external-boards/" + provider + "/" + encodeURIComponent(extBoardId)).then(function(data) {
            document.getElementById("kb-board-modal-title").textContent = "Configure " + (provider === "trello" ? "Trello" : "Jira") + " Board";
            document.getElementById("kb-board-modal-save").textContent = "Save";
            editingBoardId = data.local_id || null;

            document.getElementById("kb-board-modal-name").value = data.name || "";
            document.getElementById("kb-board-modal-name").readOnly = false;
            document.getElementById("kb-board-modal-desc").value = "";
            document.getElementById("kb-board-modal-agent-enabled").checked = !!data.agent_enabled;

            var colorInput = document.getElementById("kb-board-modal-color");
            var colorHex = document.getElementById("kb-board-modal-color-hex");
            var c = data.color || (provider === "trello" ? "#0079bf" : "#0052cc");
            colorInput.value = c;
            colorHex.textContent = c;

            loadBoardDefaults({
                default_workflow_id: data.default_workflow_id,
                default_project_id: data.default_project_id,
            });

            window._extBoardConfig = { provider: provider, extBoardId: extBoardId };
            switchBoardModalTab("details");
            document.getElementById("kb-board-modal").classList.remove("hidden");
        }).catch(function(e) {
            showSnackbar("Failed to load external board config: " + e.message, "error");
        });
    }

    function switchBoardModalTab(tab) {
        document.querySelectorAll(".kb-bm-tab").forEach(function(btn) {
            var isActive = btn.dataset.tab === tab;
            btn.classList.toggle("active", isActive);
            btn.classList.toggle("text-white", isActive);
            btn.classList.toggle("text-gray-400", !isActive);
            btn.style.borderColor = isActive ? "#f97316" : "transparent";
        });
        document.querySelectorAll(".kb-bm-pane").forEach(function(pane) { pane.classList.add("hidden"); });
        var pane = document.getElementById("kb-bm-tab-" + tab);
        if (pane) pane.classList.remove("hidden");
        // Load WhatsApp links when switching to that tab
        if (tab === "whatsapp" && editingBoardId) {
            loadBoardWaLinks(editingBoardId);
        }
    }

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

    function closeBoardModal() {
        document.getElementById("kb-board-modal").classList.add("hidden");
        editingBoardId = null;
        window._extBoardConfig = null;
    }

    function saveBoardModal() {
        // Handle external board config saving
        if (window._extBoardConfig) {
            var extCfg = window._extBoardConfig;
            var payload = {
                name: document.getElementById("kb-board-modal-name").value.trim(),
                default_project_id: parseInt(document.getElementById("kb-board-def-project").value) || 0,
                default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value) || 0,
                color: document.getElementById("kb-board-modal-color").value || "",
                agent_enabled: document.getElementById("kb-board-modal-agent-enabled").checked,
            };
            apiFetch("/api/kanban/external-boards/" + extCfg.provider + "/" + encodeURIComponent(extCfg.extBoardId) + "/register", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function(r) {
                showSnackbar("External board configured");
                window._extBoardConfig = null;
                closeBoardModal();
                // Refresh the current board view
                if (currentBoard) selectBoard(currentBoard.source, currentBoard.id, currentBoard.extUrl);
                loadBoards(true);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
            return;
        }
        var name = document.getElementById("kb-board-modal-name").value.trim();
        if (!name) { showSnackbar("Board name is required", "error"); return; }
        var boardColor = document.getElementById("kb-board-modal-color").value || "";
        var payload = {
            name: name,
            description: document.getElementById("kb-board-modal-desc").value.trim(),
            default_workflow_id: parseInt(document.getElementById("kb-board-def-workflow").value) || 0,
            default_project_id: parseInt(document.getElementById("kb-board-def-project").value) || 0,
            color: boardColor,
            agent_enabled: document.getElementById("kb-board-modal-agent-enabled").checked,
        };
        if (editingBoardId) {
            apiFetch("/api/kanban/boards/" + editingBoardId, {
                method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function() {
                showSnackbar("Board updated"); closeBoardModal(); loadBoards(true);
                if (currentBoard && currentBoard.id === editingBoardId) selectBoard("database", editingBoardId);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
        } else {
            apiFetch("/api/kanban/boards", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: payload.name, description: payload.description })
            }).then(function(data) {
                // After create, save board defaults
                return apiFetch("/api/kanban/boards/" + data.id, {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        default_workflow_id: payload.default_workflow_id,
                        default_project_id: payload.default_project_id
                    })
                }).then(function() { return data; });
            }).then(function(data) {
                showSnackbar("Board created"); closeBoardModal(); loadBoards(true);
                selectBoard("database", data.id);
            }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
        }
    }

    function deleteBoard() {
        if (!currentBoard || currentBoard.source !== "database") return;
        if (!confirm("Delete this board and all its tickets? This cannot be undone.")) return;
        apiFetch("/api/kanban/boards/" + currentBoard.id, { method: "DELETE" }).then(function() {
            showSnackbar("Board deleted");
            currentBoard = null; currentBoardData = null;
            document.getElementById("kb-board-view").classList.add("hidden");
            document.getElementById("kb-loading").classList.add("hidden");
            document.getElementById("kb-empty").classList.remove("hidden");
            loadBoards(true);
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Add ticket ──

    function addTicket() {
        if (!currentBoard) return;
        if (currentBoard.source !== "database") {
            if (currentBoardData && currentBoardData.can_create_ticket === false) {
                showSnackbar("You do not have permission to create tickets on this board", "error");
                return;
            }
            openCreateExternalTicketModal(); return;
        }
        if (!currentBoardData) return;
        var firstLane = (currentBoardData.lanes || [])[0];
        if (!firstLane) { showSnackbar("Board has no lanes", "error"); return; }
        apiFetch("/api/kanban/tickets", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: firstLane.id, title: "New Ticket", priority: "medium" })
        }).then(function(data) {
            selectBoard("database", currentBoard.id);
            setTimeout(function() { openTicketModal(data.id); }, 300);
        }).catch(function(e) { showSnackbar("Failed: " + e.message, "error"); });
    }

    // ── Copy external ticket to local board ──

    function openCopyModal(ticket) {
        copyTicketData = {
            title: ticket.title || "",
            description: ticket.description || "",
            priority: ticket.priority || "medium",
            time_estimate: ticket.time_estimate || "",
            time_spent: ticket.time_spent || "",
            external_source: ticket.external_source || (currentBoard.source !== "database" ? currentBoard.source : null),
            external_id: ticket.external_id || (currentBoard.source !== "database" ? String(ticket.id) : null),
            external_url: ticket.external_url || ticket.url || "",
        };
        var sel = document.getElementById("kb-copy-board-select");
        sel.innerHTML = "";
        if (!dbBoards.length) {
            sel.innerHTML = '<option value="">No boards available</option>';
            document.getElementById("kb-copy-confirm").disabled = true;
        } else {
            document.getElementById("kb-copy-confirm").disabled = false;
            dbBoards.forEach(function(b) {
                var opt = document.createElement("option");
                opt.value = b.id; opt.textContent = b.name;
                sel.appendChild(opt);
            });
        }
        document.getElementById("kb-copy-modal").classList.remove("hidden");
    }

    function closeCopyModal() {
        document.getElementById("kb-copy-modal").classList.add("hidden");
        copyTicketData = null;
    }

    function confirmCopy() {
        if (!copyTicketData) return;
        var boardId = parseInt(document.getElementById("kb-copy-board-select").value);
        if (!boardId) { showSnackbar("Select a board", "error"); return; }
        apiFetch("/api/kanban/tickets/copy-external-to-board", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                board_id: boardId,
                title: copyTicketData.title,
                description: stripHtml(copyTicketData.description),
                priority: copyTicketData.priority || "medium",
                time_estimate: copyTicketData.time_estimate || "",
                time_spent: copyTicketData.time_spent || "",
                external_source: copyTicketData.external_source,
                external_id: copyTicketData.external_id,
                external_url: copyTicketData.external_url,
            })
        }).then(function() {
            showSnackbar("Ticket copied to board"); closeCopyModal();
            // Refresh the target board if currently displayed
            if (currentBoard && currentBoard.source === "database" && currentBoard.id === boardId) {
                selectBoard("database", boardId);
            }
        }).catch(function(e) { showSnackbar("Copy failed: " + e.message, "error"); });
    }

    // ── Global Settings Modal ──

    var gsFreq = "daily";
    var gsHours = [];
    var gsDays = [];

    function loadGlobalKanbanSettings() {
        apiFetch("/api/kanban/settings").then(function(s) {

            // LLM Configuration — populate provider dropdowns then load models
            _populateProviderDropdowns(["kb-gs-orch-provider", "kb-gs-coder-provider", "kb-gs-sub-provider"]);

            var orchProv = s.kanban_agent_orchestrator_provider || "";
            var coderProv = s.kanban_agent_coder_provider || "";
            var subProv = s.kanban_agent_sub_provider || "";
            document.getElementById("kb-gs-orch-provider").value = orchProv;
            document.getElementById("kb-gs-coder-provider").value = coderProv;
            document.getElementById("kb-gs-sub-provider").value = subProv;
            loadAgentModels("kb-gs-orch", orchProv, s.kanban_agent_orchestrator_model || "");
            loadAgentModels("kb-gs-coder", coderProv, s.kanban_agent_coder_model || "");
            loadAgentModels("kb-gs-sub", subProv, s.kanban_agent_sub_model || "");

            // Advanced — agent toggle
            document.getElementById("kb-gs-agent-enabled").checked = !!s.kanban_agent_enabled;

            // Frequency
            gsFreq = s.kanban_agent_frequency || "daily";
            document.getElementById("kb-gs-frequency").value = gsFreq;
            updateGsFrequencyUI(gsFreq);

            // Hour picker
            gsHours = [];
            try { gsHours = typeof s.kanban_agent_hours === "string" ? JSON.parse(s.kanban_agent_hours) : (s.kanban_agent_hours || []); } catch (e) { gsHours = []; }
            document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
                var h = parseInt(btn.dataset.hour);
                btn.setAttribute("data-selected", gsHours.indexOf(h) >= 0 ? "1" : "0");
            });

            // Day picker
            gsDays = [];
            try { gsDays = typeof s.kanban_agent_days === "string" ? JSON.parse(s.kanban_agent_days) : (s.kanban_agent_days || []); } catch (e) { gsDays = []; }
            document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
                var d = parseInt(btn.dataset.day);
                btn.setAttribute("data-selected", gsDays.indexOf(d) >= 0 ? "1" : "0");
            });

            // Monthly day
            document.getElementById("kb-gs-monthly-day-input").value = String(s.kanban_agent_monthly_day || 1);

            // Time
            document.getElementById("kb-gs-time-input").value = s.kanban_agent_time || "09:00";

            // Lane routing — populate dropdowns from board lanes
            var laneNames = [];
            if (currentBoardData && currentBoardData.lanes) {
                laneNames = currentBoardData.lanes.map(function(l) { return l.name || l.title || ""; }).filter(Boolean);
            }
            var srcSel = document.getElementById("kb-gs-source-lane");
            var doneSel = document.getElementById("kb-gs-done-lane");
            var srcVal = s.kanban_agent_source_lane || "";
            var doneVal = s.kanban_agent_done_lane || "";
            [srcSel, doneSel].forEach(function(sel) {
                sel.innerHTML = '<option value="">Select lane…</option>';
                laneNames.forEach(function(name) {
                    var opt = document.createElement("option");
                    opt.value = name; opt.textContent = name;
                    sel.appendChild(opt);
                });
            });
            srcSel.value = srcVal;
            doneSel.value = doneVal;

        }).catch(function(e) { showSnackbar("Failed to load settings: " + e.message, "error"); });
    }

    function saveGlobalKanbanSettings() {
        // Collect selected hours
        var hours = [];
        document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
            if (btn.getAttribute("data-selected") === "1") hours.push(parseInt(btn.dataset.hour));
        });
        // Deduplicate hours
        hours = hours.filter(function(v, i, a) { return a.indexOf(v) === i; });

        // Collect selected days
        var days = [];
        document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
            if (btn.getAttribute("data-selected") === "1") days.push(parseInt(btn.dataset.day));
        });

        var payload = {
            kanban_agent_orchestrator_provider: document.getElementById("kb-gs-orch-provider").value,
            kanban_agent_orchestrator_model: document.getElementById("kb-gs-orch-model").value,
            kanban_agent_coder_provider: document.getElementById("kb-gs-coder-provider").value,
            kanban_agent_coder_model: document.getElementById("kb-gs-coder-model").value,
            kanban_agent_sub_provider: document.getElementById("kb-gs-sub-provider").value,
            kanban_agent_sub_model: document.getElementById("kb-gs-sub-model").value,
            kanban_agent_enabled: document.getElementById("kb-gs-agent-enabled").checked,
            kanban_agent_frequency: document.getElementById("kb-gs-frequency").value,
            kanban_agent_time: document.getElementById("kb-gs-time-input").value || "09:00",
            kanban_agent_hours: hours,
            kanban_agent_days: days,
            kanban_agent_monthly_day: parseInt(document.getElementById("kb-gs-monthly-day-input").value) || 1,
            kanban_agent_source_lane: document.getElementById("kb-gs-source-lane").value,
            kanban_agent_done_lane: document.getElementById("kb-gs-done-lane").value
        };

        apiFetch("/api/kanban/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function() {
            showSnackbar("Settings saved");
            closeGlobalSettingsModal();
        }).catch(function(e) { showSnackbar("Save failed: " + e.message, "error"); });
    }

    function openGlobalSettingsModal() {
        // Ensure providers are loaded, then load settings
        apiFetch("/api/llms/available-providers").then(function(data) {
            _agentProviders = data.providers || [{ id: "ollama", name: "Ollama" }];
        }).catch(function() {
            _agentProviders = [{ id: "ollama", name: "Ollama" }];
        }).then(function() {
            loadGlobalKanbanSettings();
        });
        document.getElementById("kb-global-settings-modal").classList.remove("hidden");
    }

    function closeGlobalSettingsModal() {
        document.getElementById("kb-global-settings-modal").classList.add("hidden");
    }

    function updateGsFrequencyUI(freq) {
        var hourPicker = document.getElementById("kb-gs-hour-picker");
        var dayPicker = document.getElementById("kb-gs-day-picker");
        var monthlyDay = document.getElementById("kb-gs-monthly-day");
        var timePicker = document.getElementById("kb-gs-time");

        var isMinuteBased = freq.endsWith("min");
        hourPicker.classList.toggle("hidden", freq !== "hourly");
        dayPicker.classList.toggle("hidden", freq !== "weekly" && freq !== "fortnightly");
        monthlyDay.classList.toggle("hidden", freq !== "monthly");
        timePicker.classList.toggle("hidden", freq === "hourly" || isMinuteBased);
    }

    // ── Event bindings ──

    // ── Event bindings ──

    // ═══════════════════════════════════════════════════════════════════
    // WhatsApp Chat/Messages Integration
    // ═══════════════════════════════════════════════════════════════════

    // ── Sidebar tab switching ──

    function updateTabBarVisibility() {
        var tabMessages = document.getElementById("kb-tab-messages");
        var tabBar = document.getElementById("kb-tab-bar");
        // Hide the entire tab bar when Messages tab is hidden (only one tab = no need for tabs)
        tabBar.classList.toggle("hidden", tabMessages.classList.contains("hidden"));
    }
    function setSidebarTabInUrl(tab) {
        try {
            var url = new URL(window.location.href);
            if (tab === "messages") {
                url.searchParams.set("tab", "messages");
            } else {
                url.searchParams.delete("tab");
            }
            window.history.replaceState({}, "", url.toString());
        } catch (e) {}
    }
    function getSidebarTabFromUrl() {
        try {
            var params = new URLSearchParams(window.location.search);
            return (params.get("tab") || "").toLowerCase();
        } catch (e) {
            return "";
        }
    }

    function switchSidebarTab(tab) {
        var ticketsPanel = document.getElementById("kb-panel-tickets");
        var messagesPanel = document.getElementById("kb-panel-messages");
        var tabTickets = document.getElementById("kb-tab-tickets");
        var tabMessages = document.getElementById("kb-tab-messages");
        if (tab === "messages") {
            setSidebarTabInUrl("messages");
            ticketsPanel.classList.add("hidden");
            messagesPanel.classList.remove("hidden");
            tabTickets.classList.remove("active");
            tabMessages.classList.add("active");
            // Messages is its own section; never leave board-loading UI visible behind it.
            document.getElementById("kb-loading").classList.add("hidden");
            document.getElementById("kb-board-view").classList.add("hidden");
            document.getElementById("kb-empty").classList.add("hidden");

            // If chat data is already loaded, open the active/first contact immediately.
            if (waChats.length) {
                var hasSelected = waSelectedJid && waChats.some(function(chat) { return chat.sender === waSelectedJid; });
                if (!hasSelected) {
                    waSelectedJid = waChats[0].sender;
                    waSelectedChatType = waChats[0].chat_type || "private";
                }
                var selectedChat = waChats.find(function(chat) { return chat.sender === waSelectedJid; }) || waChats[0];
                renderWhatsAppChatList();
                if (selectedChat) {
                    showWhatsAppThread(selectedChat.sender, selectedChat.name || selectedChat.sender);
                }
            } else {
                // No chats yet: show a clean blank messages section while loading.
                showWhatsAppNoMessagesState();
            }

            // Always refresh on tab entry so first available contact is auto-selected after load.
            loadWhatsAppChats(true);
        } else {
            setSidebarTabInUrl("tickets");
            ticketsPanel.classList.remove("hidden");
            messagesPanel.classList.add("hidden");
            tabTickets.classList.add("active");
            tabMessages.classList.remove("active");
            // Close thread view if open — restore board view
            closeWhatsAppThread();
        }
    }

    // ── Relay server base URL (for syncing messages when desktop app is offline) ──
    var waRelayBase = window.DecisionsAI && window.DecisionsAI.waRelayBase
        ? window.DecisionsAI.waRelayBase
        : "https://www.decisionsai.net/api/whatsapp";

    function loadWhatsAppChats(forceRefresh) {
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");

        el.textContent = "Loading...";
        // First try local desktop app, then fall back to relay server
        var localUrl = "/api/kanban/whatsapp/messages?limit=500";
        var relayUrl = waRelayBase + "/messages?limit=500";

        Promise.all([
            apiFetch(localUrl).then(function(data) {
                if (data.messages && data.messages.length > 0) {
                    return data;
                }
                // Local DB empty — try relay server for stored messages
                return fetchFromRelay().catch(function() { return { messages: [] }; });
            }).catch(function() {
                // Local endpoint not available (desktop app offline) — try relay
                return fetchFromRelay().catch(function() { return { messages: [] }; });
            }),
            apiFetch("/api/kanban/whatsapp/chats?limit=500&offset=0&search=").then(function(chatsData) {
                var map = {};
                var list = (chatsData && chatsData.chats) || [];
                list.forEach(function(chat) {
                    var id = String((chat && chat.id) || "").trim();
                    var name = String((chat && chat.name) || "").trim();
                    if (!id || !name) return;
                    if (id.indexOf("@g.us") >= 0) map[id] = name;
                });
                return map;
            }).catch(function() {
                return {};
            })
        ]).then(function(results) {
            waGroupNames = results[1] || {};
            processWhatsAppMessages(results[0] || { messages: [] });
        }).catch(function(err) {
            el.textContent = "WhatsApp not connected";
            chatListEl.innerHTML = "";
            waConnected = false;
            // If messages tab was active but WA failed, switch back to tickets tab
            // so the user isn't stuck on a blank screen.
            var tabMessages = document.getElementById("kb-tab-messages");
            if (tabMessages && tabMessages.classList.contains("active")) {
                switchSidebarTab("tickets");
            }
            tabMessages.classList.add("hidden");
            updateTabBarVisibility();
        });

        function fetchFromRelay() {
            return apiFetch("/api/kanban/whatsapp/relay/messages?limit=500").then(function(data) {
                if (data.messages && data.messages.length >= 0) return data;
                throw new Error("Relay returned no data");
            });
        }
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
        var el = document.getElementById("kb-wa-status");
        var chatListEl = document.getElementById("kb-wa-chats");
        var messages = data.messages || [];
        // Group messages by jid_phone (the conversation/chat key)
        var chatMap = {};
        messages.forEach(function(msg) {
            var chatPhone = msg.jid_phone || (msg.jid || "").split("@")[0];
            var chatName;
            if ((msg.jid || "").endsWith("@g.us") || msg.chat_type === "group") {
                // Group chat — the group JID is like '12345-9876@g.us'
                // Prefer backend-resolved group name; fallback to group identifier.
                var groupJid = msg.jid || "";
                var resolvedGroupName = msg.group_name || waGroupNames[groupJid] || waGroupNames[(chatPhone ? (chatPhone + "@g.us") : "")] || "";
                chatName = resolvedGroupName || groupJid.split("@")[0] || chatPhone || "Group Chat";
            } else {
                chatName = msg.sender_push_name || msg.sender_phone || chatPhone || "Unknown";
            }
            if (!chatMap[chatPhone]) {
                var isGroup = (msg.jid || "").endsWith("@g.us") || msg.chat_type === "group";
                chatMap[chatPhone] = { sender: chatPhone, name: chatName, messages: [], lastTs: 0, unread: 0, chat_type: isGroup ? "group" : "private" };
            }
            chatMap[chatPhone].messages.push(msg);
            if (msg.whatsapp_timestamp && msg.whatsapp_timestamp > chatMap[chatPhone].lastTs) {
                chatMap[chatPhone].lastTs = msg.whatsapp_timestamp;
            }
            if (!msg.processed) chatMap[chatPhone].unread++;
            // Use the latest sender push name as the display name
            // For groups, don't overwrite with sender name — keep group identifier
            if (msg.sender_push_name && chatMap[chatPhone].chat_type !== 'group') {
                chatMap[chatPhone].name = msg.sender_push_name;
            }
        });
        waChats = Object.values(chatMap);
        // Sort by most recent message first
        waChats.sort(function(a, b) { return b.lastTs - a.lastTs; });
        if (messages.length === 0) {
            el.textContent = waConnected ? "No captured messages yet" : "Relay offline — local messages only";
            chatListEl.innerHTML = "<div class='text-xs text-gray-500 italic py-2'>" + (waConnected ? "No captured messages yet" : "Relay server unreachable. Messages captured locally will appear here.") + "</div>";
            // Keep the Messages tab visible even if relay is offline — local messages may arrive later
            // Only hide if user never had WhatsApp configured at all
            if (!waConnected) {
                // Don't hide the tab — show the "relay offline" state so users know what's happening
                document.getElementById("kb-tab-messages").classList.remove("hidden");
                updateTabBarVisibility();
            }
            waSelectedJid = null;
            waSelectedChatType = "private";
            waThreadMessages = [];
            waSelectedMessageIds = {};
            waSelectionMode = false;
            updateWaThreadSelectToggleUi();
            waSidebarChatListMode = false;
            waSelectedChatPhones = {};
            updateWaSidebarFooterUi();
            if (isMessagesPanelVisible()) showWhatsAppNoMessagesState();
            return;
        }
        // Show Messages tab when WhatsApp has captured messages
        waConnected = true;
        document.getElementById("kb-tab-messages").classList.remove("hidden");
        updateTabBarVisibility();
        el.textContent = waChats.length + " contacts with messages";
        var hasSelected = waSelectedJid && waChats.some(function(chat) { return chat.sender === waSelectedJid; });
        if (!hasSelected && waChats.length) {
            waSelectedJid = waChats[0].sender;
            waSelectedChatType = waChats[0].chat_type || "private";
        }
        renderWhatsAppChatList();
        updateWaSidebarFooterUi();
        if (isMessagesPanelVisible() && waSelectedJid) {
            var selectedChat = waChats.find(function(chat) { return chat.sender === waSelectedJid; });
            // Only do a full re-render if messages count changed, otherwise skip
            refreshWaThreadIfOpen();
        }
        try { publishWaSubscriptions(); } catch (err) {}
    }

    function renderWhatsAppChatList() {
        var chatListEl = document.getElementById("kb-wa-chats");
        var searchVal = (document.getElementById("kb-wa-search").value || "").toLowerCase();
        var filtered = waChats;
        if (searchVal) {
            filtered = waChats.filter(function(c) {
                return (c.name || "").toLowerCase().includes(searchVal) || (c.sender || "").includes(searchVal);
            });
        }
        if (!filtered.length) {
            if (waChats.length && waSidebarChatListMode) {
                chatListEl.innerHTML = '<div class="text-xs text-gray-400 italic py-2 px-1 leading-snug">No chats match the search box. Clear search to see contacts and select them.</div>';
            } else {
                chatListEl.innerHTML = '<div class="text-xs text-gray-500 italic py-2">No incoming messages</div>';
            }
            if (!waChats.length) showWhatsAppNoMessagesState();
            return;
        }
        var html = "";
        filtered.forEach(function(chat) {
            var sender = chat.sender || "";
            var name = esc(chat.name || sender);
            var unread = chat.unread || 0;
            var lastMsg = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
            var preview = "";
            if (lastMsg) {
                preview = lastMsg.text ? lastMsg.text.substring(0, 60) : (lastMsg.media_type ? "📎 " + lastMsg.media_type : "");
                if (lastMsg.caption && !lastMsg.text) preview = (lastMsg.caption || "").substring(0, 60);
            }
            var active = waSelectedJid === chat.sender ? " bg-[#25D366]/10 border-l-2 border-[#25D366]" : "";
            var lastTs = chat.lastTs;
            var timeStr = "";
            if (lastTs) {
                var d = new Date(lastTs * 1000);
                timeStr = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
            }
            var groupBadge = chat.chat_type === 'group' ? ' <span class="text-[8px] bg-[#25D366]/20 text-[#25D366] px-1 rounded">Group</span>' : '';
            var rowSel = waSidebarChatListMode ? (!!waSelectedChatPhones[sender] ? " checked" : "") : "";
            html += '<div class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs hover:bg-white/5' + active + '" data-wa-sender="' + esc(sender) + '" data-wa-name="' + esc(name) + '" data-wa-chat-type="' + (chat.chat_type || 'private') + '">';
            if (waSidebarChatListMode) {
                html += '<input type="checkbox" class="accent-[#25D366] w-3.5 h-3.5 shrink-0 kb-wa-chat-select" data-wa-phone="' + esc(sender) + '"' + rowSel + ' />';
            }
            html += '<div class="w-8 h-8 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-xs font-bold flex-shrink-0">';
            html += esc(name.charAt(0).toUpperCase());
            html += '</div>';
            html += '<div class="flex-1 min-w-0">';
            html += '<div class="flex items-center justify-between">';
            html += '<span class="text-white truncate font-medium">' + name + '</span>' + groupBadge;
            html += '<span class="text-gray-500 text-[10px] ml-1 flex-shrink-0">' + timeStr + '</span>';
            html += '</div>';
            html += '<div class="text-gray-500 truncate">' + esc(preview) + (unread ? ' <span class="text-[#25D366] font-bold">(' + unread + ')</span>' : '') + '</div>';
            html += '</div></div>';
        });
        chatListEl.innerHTML = html;

        // Click handlers
        chatListEl.querySelectorAll("[data-wa-sender]").forEach(function(el) {
            el.addEventListener("click", function(e) {
                if (waSidebarChatListMode) {
                    if (e.target && e.target.closest && e.target.closest(".kb-wa-chat-select")) return;
                    toggleWaSidebarChatSelection(el.dataset.waSender);
                    return;
                }
                waSelectedJid = el.dataset.waSender;
                waSelectedChatType = el.dataset.waChatType || "private";
                renderWhatsAppChatList();
                try { publishWaSubscriptions(); } catch (err) {}
                showWhatsAppThread(el.dataset.waSender, el.dataset.waName);
            });
            el.addEventListener("contextmenu", function(e) {
                e.preventDefault();
                var phone = el.dataset.waSender;
                waCtxMenuData = { jid: phone + "@s.whatsapp.net", phone: phone, name: el.dataset.waName };
                showWaChatContextMenu(e.clientX, e.clientY);
            });
        });
        chatListEl.querySelectorAll(".kb-wa-chat-select").forEach(function(cb) {
            cb.addEventListener("click", function(e) { e.stopPropagation(); });
            cb.addEventListener("change", function() {
                var phone = cb.dataset.waPhone || "";
                if (cb.checked) waSelectedChatPhones[phone] = true;
                else delete waSelectedChatPhones[phone];
                updateWaSidebarFooterUi();
            });
        });
    }

    /** Light thread refresh: only fetch messages, compare with current, append new ones — no full DOM rebuild unless message set changed significantly. */
    function refreshWaThreadIfOpen() {
        if (!waSelectedJid) return;
        var msgList = document.getElementById("kb-wa-thread-messages");
        if (!msgList) return;
        var msgView = document.getElementById("kb-wa-thread-view");
        if (!msgView || msgView.classList.contains("hidden")) return;

        apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(waSelectedJid) + "&limit=200").then(function(data) {
            if (!data || !data.messages) return;
            var messages = dedupeThreadMessages(data.messages || []);
            messages.sort(function(a, b) { return (a.whatsapp_timestamp || 0) - (b.whatsapp_timestamp || 0); });
            // Quick check: if message count is same as current, skip
            if (messages.length === waThreadMessages.length) {
                // Deeper check: see if any IDs differ
                var sameSet = true;
                for (var i = 0; i < messages.length; i++) {
                    if (String(messages[i].id) !== String((waThreadMessages[i] || {}).id)) { sameSet = false; break; }
                }
                if (sameSet) return; // No changes
            }
            // Messages changed — do a full re-render since we need to update all state
            showWhatsAppThread(waSelectedJid, document.getElementById("kb-wa-thread-title").textContent);
        }).catch(function() {
            // Silently ignore refresh failures
        });
    }

    function showWhatsAppThread(sender, name) {
        waSelectedJid = sender;
        waActiveThread = {
            sender: sender,
            name: name || sender,
            chat_type: waSelectedChatType || "private",
            target_jid: ""
        };
        // Show the message thread view in the right panel (replacing kanban board)
        var boardView = document.getElementById("kb-board-view");
        var emptyView = document.getElementById("kb-empty");
        var msgView = document.getElementById("kb-wa-thread-view");

        boardView.classList.add("hidden");
        emptyView.classList.add("hidden");
        msgView.classList.remove("hidden");
        waShowThreadGlobalEmpty(false);

        var titleEl = document.getElementById("kb-wa-thread-title");
        var countEl = document.getElementById("kb-wa-thread-count");
        var msgList = document.getElementById("kb-wa-thread-messages");
        var avatarEl = document.getElementById("kb-wa-thread-avatar");
        var sendStatusEl = document.getElementById("kb-wa-thread-send-status");
        if (sendStatusEl) {
            sendStatusEl.classList.add("hidden");
            sendStatusEl.textContent = "";
        }

        titleEl.textContent = name;
        avatarEl.textContent = name.charAt(0).toUpperCase();
        countEl.textContent = "Loading...";
        var chatTypeEl = document.getElementById("kb-wa-thread-chat-type");
        if (waSelectedChatType === "group") {
            chatTypeEl.classList.remove("hidden");
        } else {
            chatTypeEl.classList.add("hidden");
        }
        msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">Loading messages...</div>';
        msgView.dataset.waSender = String(sender || "");
        msgView.dataset.waChatType = String(waSelectedChatType || "private");
        msgView.dataset.waTargetJid = "";
        waThreadMessages = [];
        waSelectedMessageIds = {};
        updateWaThreadSelectToggleUi();

        apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").then(function(data) {
            if (data.messages && data.messages.length > 0) return data;
            // No local messages — sync from relay first, then fetch from local DB
            return apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).then(function() {
                return apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200");
            }).catch(function() {
                // Sync failed, try relay directly (won't have has_ticket/snapshot_group)
                return apiFetch("/api/kanban/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                    // Relay also failed (e.g. 401 auth) — return empty to show "No messages" instead of "Error"
                    return { messages: [] };
                });
            });
        }).catch(function() {
            // Local endpoint failed entirely — try relay
            return apiFetch("/api/kanban/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                // Relay also failed — return empty instead of crashing
                return { messages: [] };
            });
        }).then(function(data) {
            var messages = dedupeThreadMessages(data.messages || []);
            waThreadMessages = messages.slice();
            if (waSelectionMode) {
                waThreadMessages.forEach(function(msg) {
                    if (waIsMessageSelectable(msg)) {
                        waSelectedMessageIds[String(msg.id)] = true;
                    }
                });
            }
            countEl.textContent = messages.length + " messages";
                var snapCountEl = document.getElementById("kb-wa-thread-snapshot-count");
                if (snapCountEl) snapCountEl.textContent = messages.length + " messages";
            if (!messages.length) {
                msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">No messages from this number yet</div>';
                setWaThreadControlsEnabled(false);
                return;
            }
            setWaThreadControlsEnabled(true);
            // Pin thread send target to the exact conversation JID from data when available.
            for (var mi = messages.length - 1; mi >= 0; mi--) {
                var mjid = String((messages[mi] && messages[mi].jid) || "");
                if (mjid && mjid.indexOf("@") !== -1) {
                    msgView.dataset.waTargetJid = mjid;
                    if (waActiveThread) waActiveThread.target_jid = mjid;
                    break;
                }
            }
            // Ensure chronological order (oldest first)
            messages.sort(function(a, b) { return (a.whatsapp_timestamp || 0) - (b.whatsapp_timestamp || 0); });
            var html = "";
            // Date separator helper
            var lastDateStr = "";
            messages.forEach(function(msg) {
                // Date separator
                var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                var dateStr = msgDate ? msgDate.toLocaleDateString([], {year: "numeric", month: "short", day: "numeric"}) : "";
                if (dateStr && dateStr !== lastDateStr) {
                    html += '<div class="flex items-center justify-center my-3"><span class="text-[10px] text-gray-500 bg-[#152054] px-3 py-1 rounded-full">' + esc(dateStr) + '</span></div>';
                    lastDateStr = dateStr;
                }

                var isMine = msg.from_me;
                var align = isMine ? "justify-end" : "justify-start";
                var bg = isMine ? "bg-[#005c4b]" : "bg-[#1f2c34]";
                var timeStr = msgDate ? msgDate.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";

                html += '<div class="flex ' + align + ' items-start gap-2" data-wa-msg-id="' + msg.id + '">';
                if (waSelectionMode) {
                    if (waIsMessageSelectable(msg)) {
                        html += '<label class="pt-1 cursor-pointer" title="Select message"><input type="checkbox" class="accent-[#25D366] w-4 h-4 kb-wa-msg-select" data-wa-msg-id="' + msg.id + '" checked></label>';
                    } else {
                        html += '<span class="pt-1 w-4 h-4"></span>';
                    }
                }
                html += '<div class="wa-msg-bubble ' + bg + ' px-3 py-2" style="max-width:75%;border-radius:8px;">';
                // Show sender name in group chats
                if (waSelectedChatType === 'group' && !isMine && msg.sender_push_name) {
                    html += '<div class="text-[10px] text-[#25D366] font-medium mb-0.5">' + esc(msg.sender_push_name) + '</div>';
                }

                // Media preview
                var mediaPath = msg.media_type ? ("/api/kanban/whatsapp/relay-media/" + msg.id) : "";
                var mediaPathM4a = msg.media_type ? ("/api/kanban/whatsapp/relay-media/" + msg.id + "?format=m4a") : "";
                if (msg.media_type === "photo" || msg.media_type === "image") {
                    // Lazy-load: show placeholder, only load img when visible (IntersectionObserver)
                    html += '<div class="mb-1 rounded overflow-hidden cursor-pointer wa-media-container" data-media-href="' + esc(mediaPath) + '" onclick="waImageLightbox(this.dataset.mediaHref)">';
                    html += '<div class="wa-media-placeholder flex items-center gap-2 px-3 py-3 bg-black/20 rounded-lg"><div class="w-10 h-10 flex items-center justify-center bg-white/5 rounded text-lg">🖼️</div><span class="text-xs text-gray-400">Loading…</span></div>';
                    html += '<img data-src="' + esc(mediaPath) + '" class="max-w-full max-h-[300px] rounded object-cover hidden wa-lazy-img" loading="lazy" onerror="this.classList.add(\'hidden\');var ph=this.parentElement.querySelector(\'.wa-media-placeholder\');if(ph){ph.innerHTML=\'<div style=\"display:flex;align-items:center;gap:8px\"><div style=\"font-size:1.5rem\">🖼️</div><span style=\"font-size:.75rem;color:#9ca3af\">Photo unavailable</span></div>\';ph.classList.remove(\'hidden\');}">';
                    html += '</div>';
                } else if (msg.media_type === "voice" || msg.media_type === "audio" || msg.media_type === "ptt") {
                    var durationSec = msg.media_duration || 0;
                    var durationStr = durationSec ? (Math.floor(durationSec / 60) + ":" + (durationSec % 60 < 10 ? "0" : "") + (durationSec % 60)) : "0:00";
                    html += '<div class="mb-1 flex items-center gap-2 bg-[#25D366]/10 rounded-lg px-3 py-2">';
                    html += '<div class="w-8 h-8 flex items-center justify-center bg-[#25D366]/20 rounded-full text-[#25D366] cursor-pointer flex-shrink-0" onclick="waToggleAudio(this)"><svg class="w-4 h-4 play-icon" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg><svg class="w-4 h-4 pause-icon hidden" fill="currentColor" viewBox="0 0 24 24"><path d="M6 4h4v16H6zm8 0h4v16h-4z"/></svg></div>';
                    html += '<div class="flex-1 min-w-0"><div class="flex items-center gap-[2px] h-5">';
                    for (var wi = 0; wi < 32; wi++) { html += '<div class="w-[3px] bg-[#25D366]/40 rounded-full" style="height:' + (4 + Math.floor(Math.random()*16)) + 'px"></div>'; }
                    html += '</div><div class="text-[10px] text-gray-400 mt-0.5 wa-voice-time">' + esc(durationStr) + '</div></div>';
                    // Dual-source audio: OGG (Chrome/Firefox) + M4A (Safari/iOS)
                    html += '<audio class="hidden" preload="none">';
                    html += '<source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "audio/ogg") + '">';
                    html += '<source src="' + esc(mediaPathM4a) + '" type="audio/mp4">';
                    html += '</audio>';
                    html += '</div>';
                } else if (msg.media_type === "video") {
                    html += '<div class="mb-1 rounded overflow-hidden wa-media-container"><div class="wa-video-placeholder flex items-center justify-center bg-black/20 rounded-lg px-4 py-6 cursor-pointer" onclick="this.classList.add(\'hidden\');var v=this.nextElementSibling;v.classList.remove(\'hidden\');v.play();"><div class="text-center"><div class="text-2xl">🎬</div><div class="text-xs text-gray-400 mt-1">Tap to play video</div></div></div><video controls preload="none" class="max-w-full max-h-[300px] hidden"><source src="' + esc(mediaPath) + '" type="' + esc(msg.media_mime_type || "video/mp4") + '"></video></div>';
                } else if (msg.media_type) {
                    var icon = msg.media_type === "sticker" ? "🏷️" : "📄";
                    var safeLocalName = msg.media_local_path ? msg.media_local_path.split("/").pop() : "";
                    var fname = esc(msg.media_filename || safeLocalName || (msg.media_type + "-" + msg.id));
                    var sizeStr = msg.media_file_length ? (msg.media_file_length < 1024*1024 ? (msg.media_file_length/1024).toFixed(1) + " KB" : (msg.media_file_length/1024/1024).toFixed(1) + " MB") : "";
                    html += '<div class="mb-1 flex items-center gap-2 px-2 py-1 bg-black/20 rounded">';
                    html += '<span>' + icon + '</span>';
                    html += '<a href="' + esc(mediaPath) + '" class="text-blue-400 underline text-xs truncate flex-1" target="_blank">' + fname + '</a>';
                    html += '<span class="text-gray-500 text-[10px]">' + sizeStr + '</span>';
                    html += '</div>';
                }

                // Caption
                if (msg.caption && String(msg.caption) !== String(msg.text || "")) {
                    html += '<div class="text-sm text-white whitespace-pre-wrap">' + esc(msg.caption) + '</div>';
                }

                // Text
                if (msg.text) {
                    html += '<div class="wa-msg-text text-sm text-white whitespace-pre-wrap">' + esc(msg.text) + '</div>';
                }

                // Timestamp + status
                html += '<div class="flex items-center justify-end gap-1 mt-0.5">';
                html += '<span class="text-[10px] text-gray-500">' + timeStr + '</span>';
                if (isMine) html += '<span class="text-[10px] text-blue-400">✓✓</span>';
                if (msg.processed) html += '<span class="text-[10px] text-green-400" title="Processed">✓</span>';
                // Show snapshot group if message was part of a snapshot
                if (msg.snapshot_group) {
                    if (msg.ticket_id) {
                        html += '<button type="button" class="text-[10px] text-[#f97316] hover:text-[#fb923c] underline-offset-2 hover:underline" title="Open snapshot ticket #' + msg.ticket_id + '" data-wa-ticket-id="' + msg.ticket_id + '">📷</button>';
                    } else {
                        html += '<span class="text-[10px] text-[#f97316]" title="Snapshot: ' + esc(msg.snapshot_group) + '">📷</span>';
                    }
                }
                html += '</div>';

                html += '</div></div>';
            });
            // Count messages that are NOT yet in a ticket/snapshot
                var unticketedCount = messages.filter(function(msg) {
                    return !waMsgHasLinkedTicket(msg);
                }).length;

                // Add "Create Tickets from Messages" button at the bottom with horizontal rules
                if (unticketedCount > 0) {
                    html += '<div class="flex items-center gap-3 py-4 mt-2 px-4">';
                    html += '<div class="flex-1 h-px bg-white/10"></div>';
                    html += '<button type="button" id="kb-wa-thread-create-tickets" class="bg-[#f97316]/20 text-[#f97316] border border-[#f97316]/50 px-5 py-2 rounded-lg font-semibold text-sm hover:bg-[#f97316]/30 transition-colors inline-flex items-center gap-2 whitespace-nowrap">';
                    html += '<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>';
                    html += waSelectionMode ? 'Snapshot Selected Messages' : 'Create Ticket from Messages';
                    html += '</button>';
                    html += '<div class="flex-1 h-px bg-white/10"></div>';
                    html += '</div>';
                } else {
                    html += '<div class="flex items-center justify-center py-4 mt-2">';
                    html += '<span class="text-xs text-gray-500 italic">All messages have been added to tickets</span>';
                    html += '</div>';
                }

            msgList.innerHTML = html;

            // Lazy-load images via IntersectionObserver — only set src when visible
            if ("IntersectionObserver" in window) {
                var imgObserver = new IntersectionObserver(function(entries) {
                    entries.forEach(function(entry) {
                        if (entry.isIntersecting) {
                            var img = entry.target;
                            var src = img.getAttribute("data-src");
                            if (src) {
                                img.src = src;
                                img.removeAttribute("data-src");
                                img.classList.remove("hidden");
                                var ph = img.parentElement.querySelector(".wa-media-placeholder");
                                if (ph) ph.classList.add("hidden");
                            }
                            imgObserver.unobserve(img);
                        }
                    });
                }, { root: msgList, threshold: 0.1 });
                msgList.querySelectorAll(".wa-lazy-img").forEach(function(img) {
                    imgObserver.observe(img);
                });
            } else {
                // Fallback: load all images immediately
                msgList.querySelectorAll(".wa-lazy-img").forEach(function(img) {
                    img.src = img.getAttribute("data-src") || "";
                    img.removeAttribute("data-src");
                    img.classList.remove("hidden");
                    var ph = img.parentElement.querySelector(".wa-media-placeholder");
                    if (ph) ph.classList.add("hidden");
                });
            }

            // Scroll to bottom (newest)
            msgList.scrollTop = msgList.scrollHeight;

            // Wire up the create tickets button
            var createTicketBtn = document.getElementById("kb-wa-thread-create-tickets");
            if (createTicketBtn) {
                createTicketBtn.addEventListener("click", function() {
                    waCtxSnapshotToBoardBottom();
                });
            }
            msgList.querySelectorAll(".kb-wa-msg-select").forEach(function(el) {
                el.addEventListener("change", function() {
                    var msgId = String(el.dataset.waMsgId || "");
                    if (!msgId) return;
                    if (el.checked) waSelectedMessageIds[msgId] = true;
                    else delete waSelectedMessageIds[msgId];
                    updateWaThreadSelectToggleUi();
                });
            });

            // Right-click on messages
            msgList.querySelectorAll("[data-wa-msg-id]").forEach(function(el) {
                el.addEventListener("contextmenu", function(e) {
                    e.preventDefault();
                    waMsgCtxData = { message_id: parseInt(el.dataset.waMsgId) };
                    showWaMsgContextMenu(e.clientX, e.clientY);
                });
            });
            msgList.querySelectorAll("[data-wa-ticket-id]").forEach(function(el) {
                el.addEventListener("click", function() {
                    var ticketId = parseInt(el.dataset.waTicketId || "0", 10);
                    if (ticketId) openTicketModal(ticketId);
                });
            });
        }).catch(function(err) {
            countEl.textContent = "Error";
            msgList.innerHTML = '<div class="text-sm text-red-400 text-center py-8">Failed to load messages</div>';
            setWaThreadControlsEnabled(false);
        });
    }

    function _waResolveTargetJid(sender, chatTypeOverride) {
        if (!sender) return "";
        if (sender.indexOf("@") !== -1) return sender;
        var ctype = chatTypeOverride || waSelectedChatType;
        if (ctype === "group") return sender + "@g.us";
        return sender + "@s.whatsapp.net";
    }

    function sendWhatsAppThreadMessage() {
        var inputEl = document.getElementById("kb-wa-thread-input");
        var statusEl = document.getElementById("kb-wa-thread-send-status");
        var msgView = document.getElementById("kb-wa-thread-view");
        if (!inputEl || !waSelectedJid || !msgView) return;
        if (waVoiceRecording) return;
        var text = (inputEl.value || "").trim();
        if (!text && !waPendingAttachment) return;

        // Always send to the currently opened thread target, never stale selection.
        var pinnedSender = (msgView.dataset.waSender || waSelectedJid || "").trim();
        var pinnedChatType = (msgView.dataset.waChatType || waSelectedChatType || "private").trim();
        var pinnedTargetJid = (msgView.dataset.waTargetJid || "").trim();
        var jid = pinnedTargetJid || _waResolveTargetJid(pinnedSender, pinnedChatType);
        if (!jid) return;
        var optimisticText = text;
        var pendingAttachment = waPendingAttachment ? {
            name: waPendingAttachment.name,
            mime_type: waPendingAttachment.mime_type,
            data_b64: waPendingAttachment.data_b64,
            kind: waPendingAttachment.kind
        } : null;

        inputEl.disabled = true;
        statusEl.classList.remove("hidden");
        statusEl.classList.remove("text-red-400", "text-green-400");
        statusEl.classList.add("text-gray-500");
        statusEl.textContent = "Sending...";

        var payload = { jid: jid, text: pendingAttachment ? "" : text };
        if (pendingAttachment) {
            payload.caption = text || "";
            payload.media = {
                data_b64: pendingAttachment.data_b64,
                mime_type: pendingAttachment.mime_type,
                filename: pendingAttachment.name,
                kind: pendingAttachment.kind
            };
        }

        apiFetch("/api/kanban/whatsapp/send", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        }).then(function(resp) {
            if (!resp || !resp.success) {
                throw new Error((resp && resp.error) || "Send failed");
            }
            inputEl.value = "";
            clearWaPendingAttachment();
            statusEl.classList.remove("text-gray-500", "text-red-400");
            statusEl.classList.add("text-green-400");
            statusEl.textContent = "Sent";
            if (pendingAttachment) {
                appendOptimisticSentMediaMessage(pendingAttachment.name, pendingAttachment.kind, optimisticText);
            } else {
                appendOptimisticSentTextMessage(optimisticText);
            }
            syncAndRefreshThreadAfterSend();
        }).catch(function(err) {
            statusEl.classList.remove("text-gray-500", "text-green-400");
            statusEl.classList.add("text-red-400");
            if (pendingAttachment) {
                statusEl.textContent = "Attachment send failed (relay may not support media yet): " + err.message;
                // Prevent repeated 400 errors on next send if relay rejects media payloads.
                clearWaPendingAttachment();
            } else {
                statusEl.textContent = "Failed to send: " + err.message;
            }
        }).finally(function() {
            inputEl.disabled = false;
            inputEl.focus();
            _updateWhatsAppComposerState();
        });
    }

    function _setWaSendStatus(statusText, colorClass) {
        var statusEl = document.getElementById("kb-wa-thread-send-status");
        if (!statusEl) return;
        statusEl.classList.remove("hidden", "text-red-400", "text-green-400", "text-gray-500");
        statusEl.classList.add(colorClass || "text-gray-500");
        statusEl.textContent = statusText || "";
    }

    function _waBlobToBase64(blob) {
        return new Promise(function(resolve, reject) {
            var reader = new FileReader();
            reader.onloadend = function() {
                var res = String(reader.result || "");
                var idx = res.indexOf(",");
                resolve(idx >= 0 ? res.substring(idx + 1) : res);
            };
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }

    function _formatVoiceDuration(totalSeconds) {
        var secs = Math.max(0, totalSeconds | 0);
        var mm = String(Math.floor(secs / 60)).padStart(2, "0");
        var ss = String(secs % 60).padStart(2, "0");
        return mm + ":" + ss;
    }

    function _updateWhatsAppComposerState() {
        var sendBtn = document.getElementById("kb-wa-thread-send");
        var voiceBtn = document.getElementById("kb-wa-thread-voice");
        var attachBtn = document.getElementById("kb-wa-thread-attach");
        var inputEl = document.getElementById("kb-wa-thread-input");
        if (sendBtn) sendBtn.disabled = waVoiceRecording;
        if (voiceBtn) voiceBtn.disabled = !!inputEl && inputEl.disabled;
        if (attachBtn) attachBtn.disabled = waVoiceRecording || (!!inputEl && inputEl.disabled);
    }

    function _startVoiceRecordingTimer() {
        waVoiceStartedAtMs = Date.now();
        _setWaSendStatus("Recording... 00:00", "text-gray-500");
        if (waVoiceTimerInterval) clearInterval(waVoiceTimerInterval);
        waVoiceTimerInterval = setInterval(function() {
            var elapsedSec = Math.floor((Date.now() - waVoiceStartedAtMs) / 1000);
            _setWaSendStatus("Recording... " + _formatVoiceDuration(elapsedSec), "text-gray-500");
        }, 500);
    }

    function _getWaVoiceFilename(mimeType) {
        if (!mimeType) return "voice-note.ogg";
        if (mimeType.indexOf("webm") >= 0) return "voice-note.webm";
        if (mimeType.indexOf("ogg") >= 0 || mimeType.indexOf("opus") >= 0) return "voice-note.ogg";
        if (mimeType.indexOf("mp4") >= 0 || mimeType.indexOf("m4a") >= 0) return "voice-note.m4a";
        if (mimeType.indexOf("mpeg") >= 0 || mimeType.indexOf("mp3") >= 0) return "voice-note.mp3";
        return "voice-note.ogg";
    }

    function _resetWhatsAppVoiceRecordingUi() {
        var btn = document.getElementById("kb-wa-thread-voice");
        waVoiceRecording = false;
        waVoiceRecorder = null;
        waVoiceChunks = [];
        waVoiceStartedAtMs = 0;
        if (waVoiceTimerInterval) {
            clearInterval(waVoiceTimerInterval);
            waVoiceTimerInterval = null;
        }
        if (waVoiceStream) {
            try { waVoiceStream.getTracks().forEach(function(t) { t.stop(); }); } catch (e) {}
            waVoiceStream = null;
        }
        if (btn) {
            btn.classList.remove("bg-red-600/20", "border-red-500/50", "text-red-400");
            btn.classList.add("border-[#25D366]/50", "text-[#25D366]");
        }
        _updateWhatsAppComposerState();
    }

    async function startWhatsAppVoiceRecording() {
        if (waVoiceRecording) return;
        if (!waSelectedJid) {
            _setWaSendStatus("Select a chat first", "text-red-400");
            return;
        }
        var btn = document.getElementById("kb-wa-thread-voice");
        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
                _setWaSendStatus("Voice notes not supported in this browser", "text-red-400");
                return;
            }
            waVoiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            var mimeType = MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")
                ? "audio/ogg;codecs=opus"
                : (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
                    ? "audio/webm;codecs=opus"
                    : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4"));
            waVoiceChunks = [];
            waVoiceRecorder = new MediaRecorder(waVoiceStream, { mimeType: mimeType });
            waVoiceRecorder.ondataavailable = function(event) {
                if (event.data && event.data.size > 0) waVoiceChunks.push(event.data);
            };
            waVoiceRecorder.start();
            waVoiceRecording = true;
            if (btn) {
                btn.classList.add("bg-red-600/20", "border-red-500/50", "text-red-400");
                btn.classList.remove("border-[#25D366]/50", "text-[#25D366]");
            }
            _updateWhatsAppComposerState();
            _startVoiceRecordingTimer();
        } catch (err) {
            _setWaSendStatus("Microphone error: " + (err && err.message ? err.message : String(err)), "text-red-400");
            _updateWhatsAppComposerState();
        }
    }

    async function stopWhatsAppVoiceRecordingAndSend() {
        if (!waVoiceRecording || !waVoiceRecorder) return;
        var inputEl = document.getElementById("kb-wa-thread-input");
        var msgView = document.getElementById("kb-wa-thread-view");
        var caption = (inputEl && inputEl.value ? inputEl.value.trim() : "");
        try {
            await new Promise(function(resolve) {
                waVoiceRecorder.onstop = resolve;
                waVoiceRecorder.stop();
            });
            var pinnedSender = (msgView && msgView.dataset.waSender ? msgView.dataset.waSender : waSelectedJid || "").trim();
            var pinnedChatType = (msgView && msgView.dataset.waChatType ? msgView.dataset.waChatType : waSelectedChatType || "private").trim();
            var pinnedTargetJid = (msgView && msgView.dataset.waTargetJid ? msgView.dataset.waTargetJid : "").trim();
            var jid = pinnedTargetJid || _waResolveTargetJid(pinnedSender, pinnedChatType);
            if (!jid) {
                _setWaSendStatus("No chat selected", "text-red-400");
                return;
            }
            var blob = new Blob(waVoiceChunks, { type: waVoiceRecorder.mimeType || "audio/webm" });
            if (!blob.size) {
                _setWaSendStatus("No audio captured", "text-red-400");
                return;
            }
            _setWaSendStatus("Sending voice note...", "text-gray-500");
            var b64 = await _waBlobToBase64(blob);
            var payload = {
                jid: jid,
                caption: caption,
                audio: {
                    data_b64: b64,
                    mime_type: waVoiceRecorder.mimeType || "audio/webm",
                    ptt: true,
                    filename: _getWaVoiceFilename(waVoiceRecorder.mimeType || "audio/webm")
                }
            };
            var resp = await apiFetch("/api/kanban/whatsapp/send", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            });
            if (!resp || !resp.success) {
                throw new Error((resp && resp.error) || "Send failed");
            }
            if (inputEl) inputEl.value = "";
            _setWaSendStatus("Voice note sent", "text-green-400");
            syncAndRefreshThreadAfterSend();
        } catch (err) {
            _setWaSendStatus("Failed to send voice note: " + (err && err.message ? err.message : String(err)), "text-red-400");
        } finally {
            _resetWhatsAppVoiceRecordingUi();
        }
    }

    function closeWhatsAppThread() {
        var msgView = document.getElementById("kb-wa-thread-view");
        msgView.classList.add("hidden");
        if (waVoiceRecorder && waVoiceRecording) {
            try { waVoiceRecorder.stop(); } catch (e) {}
        }
        _resetWhatsAppVoiceRecordingUi();
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
        var menu = document.getElementById("kb-wa-ctx-menu");
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        // Position adjustment if off-screen
        setTimeout(function() {
            var rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + "px";
            if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + "px";
        }, 0);
    }

    function hideWaChatContextMenu() {
        document.getElementById("kb-wa-ctx-menu").classList.add("hidden");
    }

    function showWaMsgContextMenu(x, y) {
        var menu = document.getElementById("kb-wa-msg-ctx-menu");
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        setTimeout(function() {
            var rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + "px";
            if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + "px";
        }, 0);
    }

    function hideWaMsgContextMenu() {
        document.getElementById("kb-wa-msg-ctx-menu").classList.add("hidden");
    }

    function showWaSyncContextMenu(x, y) {
        var menu = document.getElementById("kb-wa-sync-ctx-menu");
        if (!menu) return;
        menu.style.left = x + "px";
        menu.style.top = y + "px";
        menu.classList.remove("hidden");
        setTimeout(function() {
            var rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + "px";
            if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + "px";
        }, 0);
    }

    function hideWaSyncContextMenu() {
        var menu = document.getElementById("kb-wa-sync-ctx-menu");
        if (menu) menu.classList.add("hidden");
    }

    function clearWaServerMessages() {
        var statusEl = document.getElementById("kb-wa-status");
        if (statusEl) statusEl.textContent = "Clearing server messages...";
        apiFetch("/api/kanban/whatsapp/relay/clear-messages", { method: "POST" }).then(function(result) {
            if (result && result.success) {
                showSnackbar("Server messages cleared", "success");
                waSelectedJid = null;
                waSelectedChatType = "private";
                waThreadMessages = [];
                waSelectedMessageIds = {};
                waSelectionMode = false;
                updateWaThreadSelectToggleUi();
                if (isMessagesPanelVisible()) showWhatsAppNoMessagesState();
                loadWhatsAppChats(true);
                return;
            }
            var reason = (result && (result.error || result.detail)) || "Unknown error";
            showSnackbar("Failed to clear server messages: " + reason, "error");
        }).catch(function(err) {
            showSnackbar("Failed to clear server messages: " + err.message, "error");
        }).finally(function() {
            if (statusEl) statusEl.textContent = "";
        });
    }

    // ── WhatsApp chat context menu actions ──

    function waCtxViewMessages() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        waSelectedJid = waCtxMenuData.phone;
        renderWhatsAppChatList();
        showWhatsAppThread(waCtxMenuData.phone, waCtxMenuData.name);
    }

    function waCtxLinkToBoard() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        openWaLinkModal(waCtxMenuData);
    }

    /** True if focus is in a text field where Delete/Backspace should edit text, not open delete-chat. */
    function waThreadDeleteHotkeyIgnored() {
        var el = document.activeElement;
        if (!el) return false;
        var tag = (el.tagName || "").toLowerCase();
        if (tag === "textarea") return true;
        if (tag === "input") {
            var it = (el.type || "text").toLowerCase();
            if (it === "checkbox" || it === "radio" || it === "button" || it === "submit" || it === "reset" || it === "range" || it === "file" || it === "color") return false;
            return true;
        }
        if (el.isContentEditable) return true;
        return false;
    }

    /** Confirm and DELETE /api/kanban/whatsapp/chat/:phone — full thread wipe (shared by sidebar menu, thread header, message menu in selection mode). */
    function waRunDeleteChatConfirm(phone, displayName) {
        if (!phone) return;
        var name = displayName || phone;
        showKanbanConfirm({
            title: "Delete Chat",
            message: "Delete all messages from " + name + "? This cannot be undone.",
            confirmLabel: "Delete",
            danger: true,
            onConfirm: function() {
                apiFetch("/api/kanban/whatsapp/chat/" + encodeURIComponent(phone), {
                    method: "DELETE"
                }).then(function(r) {
                    hideKanbanConfirm();
                    if (r && r.success) {
                        showSnackbar("Deleted " + (r.deleted || "all") + " messages from " + name);
                        waSelectedJid = null;
                        waSelectedChatType = "private";
                        waThreadMessages = [];
                        waSelectedMessageIds = {};
                        waSelectionMode = false;
                        updateWaThreadSelectToggleUi();
                        renderWhatsAppChatList();
                        loadWhatsAppChats(true);
                    } else {
                        showSnackbar("Failed to delete chat", "error");
                    }
                }).catch(function(err) {
                    hideKanbanConfirm();
                    showSnackbar("Failed to delete: " + err.message, "error");
                });
            }
        });
    }

    function waCtxDeleteChat() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        waRunDeleteChatConfirm(waCtxMenuData.phone, waCtxMenuData.name);
    }

    function waCtxSnapshotToBoard() {
        hideWaChatContextMenu();
        if (!waCtxMenuData) return;
        var phone = waCtxMenuData.phone;
        var threadName = waCtxMenuData.name;
        // Fetch messages, then open the modal pre-populated
        apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
            var boardId = linkData.board_id || null;
            apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                var msgs = data.messages || [];
                if (!msgs.length) { showSnackbar("No messages from " + threadName); return; }
                var unTicketed = msgs.filter(function(m) { return !waMsgHasLinkedTicket(m); });
                if (!unTicketed.length) { showSnackbar("All messages already in tickets"); return; }
                var ticketTitle = esc(threadName) + " - " + unTicketed.length + " messages";
                var ticketDesc = "";
                unTicketed.forEach(function(msg) {
                    var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                    var timeStr = msgDate ? msgDate.toLocaleString() : "";
                    var senderName = msg.sender_push_name || msg.sender_phone || "Unknown";
                    ticketDesc += "[" + timeStr + "] " + senderName + ":\n";
                    if (msg.text) ticketDesc += msg.text + "\n";
                    if (msg.caption) ticketDesc += "[caption: " + msg.caption + "]\n";
                    if (msg.media_type) ticketDesc += "[" + msg.media_type + "]\n";
                    ticketDesc += "\n";
                });
                openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, boardId, unTicketed);
            });
        }).catch(function() {
            // No linked board — open modal without a pre-selected board
            apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                var msgs = data.messages || [];
                if (!msgs.length) { showSnackbar("No messages from " + threadName); return; }
                var unTicketed = msgs.filter(function(m) { return !waMsgHasLinkedTicket(m); });
                if (!unTicketed.length) { showSnackbar("All messages already in tickets"); return; }
                var ticketTitle = esc(threadName) + " - " + unTicketed.length + " messages";
                var ticketDesc = "";
                unTicketed.forEach(function(msg) {
                    var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                    var timeStr = msgDate ? msgDate.toLocaleString() : "";
                    var senderName = msg.sender_push_name || msg.sender_phone || "Unknown";
                    ticketDesc += "[" + timeStr + "] " + senderName + ":\n";
                    if (msg.text) ticketDesc += msg.text + "\n";
                    if (msg.caption) ticketDesc += "[caption: " + msg.caption + "]\n";
                    if (msg.media_type) ticketDesc += "[" + msg.media_type + "]\n";
                    ticketDesc += "\n";
                });
                openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, null, unTicketed);
            });
        });
    }

    // ── Bottom snapshot (create ticket from all messages) ───────────────
    function waCtxSnapshotToBoardBottom() {
        if (!waSelectedJid) return;
        var phone = waSelectedJid;
        var threadName = document.getElementById("kb-wa-thread-title").textContent;
        // Get db boards
        apiFetch("/api/kanban/boards").then(function(boards) {
            var dbBs = boards.filter(function(b) { return b.source === "database"; });
            if (!dbBs.length) { showSnackbar("No database boards to create ticket in"); return; }
            var targetBoard = (currentBoard && currentBoard.source === "database") ? currentBoard.id : dbBs[0].id;
            // Prefer the board linked to this WhatsApp phone (if configured).
            return apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                if (linkData && linkData.board_id) {
                    targetBoard = linkData.board_id;
                }
                // Fetch un-ticketed messages
                return apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500");
            }).then(function(data) {
                if (!data) return;
                var allMsgs = data.messages || [];
                var msgs = collectWaSnapshotMessages(allMsgs);
                if (!msgs.length) {
                    showSnackbar(waSelectionMode ? "Select at least one available message" : "No new messages to add to a ticket");
                    return;
                }
                // Build pre-populated ticket data
                var ticketTitle = esc(threadName) + " - " + msgs.length + " messages";
                var ticketDesc = "";
                msgs.forEach(function(msg) {
                    var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                    var timeStr = msgDate ? msgDate.toLocaleString() : "";
                    var senderName = msg.sender_push_name || msg.sender_phone || "Unknown";
                    ticketDesc += "[" + timeStr + "] " + senderName + ":\n";
                    if (msg.text) ticketDesc += msg.text + "\n";
                    if (msg.caption) ticketDesc += "[caption: " + msg.caption + "]\n";
                    if (msg.media_type) ticketDesc += "[" + msg.media_type + "]\n";
                    ticketDesc += "\n";
                });
                // Open the ticket modal pre-populated with the data
                openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, targetBoard, msgs);
            }).catch(function() {
                return apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                    if (!data) return;
                    var allMsgs = data.messages || [];
                    var msgs = collectWaSnapshotMessages(allMsgs);
                    if (!msgs.length) {
                        showSnackbar(waSelectionMode ? "Select at least one available message" : "No new messages to add to a ticket");
                        return;
                    }
                    var ticketTitle = esc(threadName) + " - " + msgs.length + " messages";
                    var ticketDesc = "";
                    msgs.forEach(function(msg) {
                        var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                        var timeStr = msgDate ? msgDate.toLocaleString() : "";
                        var senderName = msg.sender_push_name || msg.sender_phone || "Unknown";
                        ticketDesc += "[" + timeStr + "] " + senderName + ":\n";
                        if (msg.text) ticketDesc += msg.text + "\n";
                        if (msg.caption) ticketDesc += "[caption: " + msg.caption + "]\n";
                        if (msg.media_type) ticketDesc += "[" + msg.media_type + "]\n";
                        ticketDesc += "\n";
                    });
                    openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, targetBoard, msgs);
                });
            });
        });
    }
    function openTicketFromWhatsApp(phone, title, description, boardId, msgs) {
        if (waTicketComposeInFlight) {
            return;
        }
        waTicketComposeInFlight = true;
        // Open the ticket creation modal immediately with a spinner
        var modal = document.getElementById("kb-create-ext-ticket-modal");
        if (!modal) { waTicketComposeInFlight = false; showSnackbar("Ticket form not found"); return; }

        // Show the board row (hidden by default for non-WhatsApp use)
        document.getElementById("kb-cet-board-row").style.display = "";

        // Show full-form loading state while LLM composes
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

        // Store metadata for after creation
        modal.dataset.whatsappPhone = phone;
        modal.dataset.whatsappMsgIds = JSON.stringify(msgs.map(function(m) { return m.id; }));
        modal.dataset.whatsappBoardId = boardId;

        // Show modal immediately — user sees the form with spinners
        document.getElementById("kb-cet-files-list").innerHTML = "";
        document.getElementById("kb-cet-file-input").value = "";
        modal.classList.remove("hidden");

        // Switch to details tab
        cetSwitchTab("details");

        // Fire the LLM compose call — this updates title/desc when done
        var msgIds = msgs.map(function(m) { return m.id; });
        composeWaTicket(msgIds, titleEl, descEl, statusEl);

        // Load ALL boards (database + external) into the dropdown
        Promise.all([
            apiFetch("/api/kanban/boards"),
            apiFetch("/api/kanban/external-boards").catch(function() { return {trello: [], jira: []}; })
        ]).then(function(results) {
            var boards = results[0];
            var extData = results[1];
            var boardSelect = document.getElementById("kb-cet-board");
            boardSelect.innerHTML = "";

            // Database boards
            var dbBs = boards.filter(function(b) { return b.source === "database"; });
            if (dbBs.length) {
                var optgroup = document.createElement("optgroup");
                optgroup.label = "Local";
                dbBs.forEach(function(b) {
                    var opt = document.createElement("option");
                    opt.value = "database:" + b.id;
                    opt.textContent = b.name;
                    if (b.id == boardId) opt.selected = true;
                    optgroup.appendChild(opt);
                });
                boardSelect.appendChild(optgroup);
            }

            // Trello boards
            var trelloBs = (extData && extData.trello) || [];
            if (trelloBs.length) {
                var optgroup = document.createElement("optgroup");
                optgroup.label = "Trello";
                trelloBs.forEach(function(b) {
                    var opt = document.createElement("option");
                    opt.value = "trello:" + b.id;
                    opt.textContent = b.name;
                    if (b.id == boardId) opt.selected = true;
                    optgroup.appendChild(opt);
                });
                boardSelect.appendChild(optgroup);
            }

            // Jira boards
            var jiraBs = (extData && extData.jira) || [];
            if (jiraBs.length) {
                var optgroup = document.createElement("optgroup");
                optgroup.label = "Jira";
                jiraBs.forEach(function(b) {
                    var opt = document.createElement("option");
                    opt.value = "jira:" + b.id;
                    opt.textContent = b.name;
                    if (b.id == boardId) opt.selected = true;
                    optgroup.appendChild(opt);
                });
                boardSelect.appendChild(optgroup);
            }

            // Show the board row
            document.getElementById("kb-cet-board-row").style.display = "";

            // Load lanes for the selected board
            function loadLanes(source, bid) {
                if (source === "database") {
                    apiFetch("/api/kanban/boards/" + bid).then(function(boardData) {
                        var laneInput = document.getElementById("kb-cet-lane");
                        var lanes = boardData.lanes || [];
                        var target = lanes.find(function(l) { return l.name === "Backlog"; }) || lanes[0];
                        if (target) laneInput.value = target.id;
                    });
                } else {
                    apiFetch("/api/kanban/external-boards/" + source + "/" + encodeURIComponent(bid)).then(function(extBoard) {
                        var laneInput = document.getElementById("kb-cet-lane");
                        var cols = extBoard.columns || extBoard.lists || [];
                        if (cols.length) laneInput.value = cols[0].name || cols[0].id || "";
                    }).catch(function() {});
                }
            }

            // Initial load
            var firstOpt = boardSelect.options[boardSelect.selectedIndex];
            if (!firstOpt && boardSelect.options.length > 0) {
                boardSelect.selectedIndex = 0;
                firstOpt = boardSelect.options[0];
            }
            if (firstOpt) {
                var firstVal = firstOpt.value.split(":");
                loadLanes(firstVal[0], parseInt(firstVal[1]));
            }

            // When board changes, reload lanes
            boardSelect.addEventListener("change", function() {
                var selVal = boardSelect.value.split(":");
                loadLanes(selVal[0], parseInt(selVal[1]));
            });

            // Reset files (modal is already shown)
            document.getElementById("kb-cet-files-list").innerHTML = "";
            document.getElementById("kb-cet-file-input").value = "";
        });
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

    function confirmWaLink() {
        var boardId = parseInt(document.getElementById("kb-wa-link-board").value);
        if (!boardId) { showSnackbar("Select a board"); return; }
        var autoSnapshot = document.getElementById("kb-wa-link-auto").checked;

        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                phone_jid: waCtxMenuData.jid,
                phone_number: waCtxMenuData.phone,
                contact_name: waCtxMenuData.name,
                auto_snapshot: autoSnapshot,
            })
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Linked " + waCtxMenuData.name + " to board");
                document.getElementById("kb-wa-link-modal").classList.add("hidden");
            }
        }).catch(function(err) {
            showSnackbar("Failed to link: " + err.message);
        });
    }

    // ── WhatsApp message context menu actions ──

    function waMsgCtxCreateTicket() {
        hideWaMsgContextMenu();
        if (!waMsgCtxData) return;
        if (waSelectionMode) {
            waCtxSnapshotToBoardBottom();
            return;
        }
        var msgId = waMsgCtxData.message_id;
        var phone = waSelectedJid;
        var threadName = document.getElementById("kb-wa-thread-title").textContent;
        // Find message text from the DOM
        var msgEl = document.querySelector('[data-wa-msg-id="' + msgId + '"]');
        var msgText = "";
        if (msgEl) {
            var textEl = msgEl.querySelector(".wa-msg-text");
            if (textEl) msgText = textEl.textContent;
        }
        var title = threadName + " - Message";
        var desc = msgText || "WhatsApp message";
        // Look up linked board, then open the modal
        apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
            var boardId = linkData.board_id || null;
            openTicketFromWhatsApp(phone, title, desc, boardId, [{ id: msgId }]);
        }).catch(function() {
            openTicketFromWhatsApp(phone, title, desc, null, [{ id: msgId }]);
        });
    }

    function waMsgCtxMarkProcessed() {
        hideWaMsgContextMenu();
        if (!waMsgCtxData) return;
        apiFetch("/api/kanban/whatsapp/messages/" + waMsgCtxData.message_id + "/processed", {
            method: "POST"
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Marked as processed");
                if (waSelectedJid) {
                    refreshWaThreadIfOpen();
                }
            }
        }).catch(function(err) {
            showSnackbar("Failed to mark processed: " + err.message);
        });
    }

    function waMsgCtxDelete() {
        hideWaMsgContextMenu();
        if (waSelectionMode && waSelectedJid) {
            var threadName = document.getElementById("kb-wa-thread-title").textContent || waSelectedJid;
            waRunDeleteChatConfirm(waSelectedJid, threadName);
            return;
        }
        if (!waMsgCtxData) return;
        apiFetch("/api/kanban/whatsapp/messages/" + waMsgCtxData.message_id, {
            method: "DELETE"
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Message deleted");
                if (waSelectedJid) {
                    refreshWaThreadIfOpen();
                }
            }
        }).catch(function(err) {
            showSnackbar("Failed to delete: " + err.message);
        });
    }

    // ── Board modal WhatsApp tab ──

    function loadBoardWaLinks(boardId) {
        var linksEl = document.getElementById("kb-bm-wa-links");
        linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">Loading...</div>';
        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links").then(function(links) {
            if (!links.length) {
                linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">No WhatsApp numbers linked</div>';
                return;
            }
            var html = "";
            links.forEach(function(l) {
                html += '<div class="flex items-center justify-between px-3 py-2 bg-[#152054] rounded border border-white/10">';
                html += '<div class="flex items-center gap-2">';
                html += '<div class="w-6 h-6 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-[10px] font-bold">' + esc((l.contact_name || l.phone_number || "?").charAt(0).toUpperCase()) + '</div>';
                html += '<div>';
                html += '<div class="text-sm text-white">' + esc(l.contact_name || l.phone_number) + '</div>';
                html += '<div class="text-[10px] text-gray-500">' + esc(l.phone_number) + '</div>';
                html += '</div></div>';
                html += '<div class="flex items-center gap-2">';
                html += '<label class="flex items-center gap-1 cursor-pointer select-none" title="Auto-snapshot new messages as tickets">';
                html += '<input type="checkbox" class="accent-[#25D366] w-3 h-3 kb-wa-link-auto-toggle" data-link-id="' + l.id + '" data-board-id="' + boardId + '"' + (l.auto_snapshot ? ' checked' : '') + '>'; 
                html += '<span class="text-[10px] text-gray-500">Auto</span>';
                html += '</label>';
                html += '<button type="button" class="kb-wa-link-remove text-red-400 hover:text-red-300 text-xs px-1" data-link-id="' + l.id + '" data-board-id="' + boardId + '" title="Unlink">✕</button>';
                html += '</div></div>';
            });
            linksEl.innerHTML = html;

            // Auto-toggle handlers
            linksEl.querySelectorAll(".kb-wa-link-auto-toggle").forEach(function(el) {
                el.addEventListener("change", function() {
                    var lid = el.dataset.linkId;
                    var bid = el.dataset.boardId;
                    apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, {
                        method: "PATCH",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({ auto_snapshot: el.checked })
                    });
                });
            });

            // Remove handlers
            linksEl.querySelectorAll(".kb-wa-link-remove").forEach(function(el) {
                el.addEventListener("click", function() {
                    var lid = el.dataset.linkId;
                    var bid = el.dataset.boardId;
                    apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, { method: "DELETE" }).then(function() {
                        loadBoardWaLinks(bid);
                    });
                });
            });
        });
    }

    function addWaLinkFromBoardModal() {
        var phone = document.getElementById("kb-bm-wa-add-phone").value.trim();
        var name = document.getElementById("kb-bm-wa-add-name").value.trim();
        if (!phone) { showSnackbar("Enter a phone number"); return; }
        var boardId = editingBoardId;
        if (!boardId) { showSnackbar("Save the board first"); return; }
        // Build JID from phone number
        var jid = phone + "@s.whatsapp.net";
        apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ phone_jid: jid, phone_number: phone, contact_name: name })
        }).then(function(r) {
            if (r.success) {
                showSnackbar("Linked " + (name || phone) + " to board");
                document.getElementById("kb-bm-wa-add-phone").value = "";
                document.getElementById("kb-bm-wa-add-name").value = "";
                loadBoardWaLinks(boardId);
            }
        });
    }

    // Initialize WhatsApp section
    function initWhatsApp() {
        // Sidebar tab switching
        document.getElementById("kb-tab-tickets").addEventListener("click", function() { switchSidebarTab("tickets"); });
        document.getElementById("kb-tab-messages").addEventListener("click", function() { switchSidebarTab("messages"); });

        // WhatsApp thread back button


      


        // WhatsApp thread delete button - confirms via modal (same flow as Delete in message menu during selection mode)
        document.getElementById("kb-wa-thread-delete").addEventListener("click", function() {
            if (!waSelectedJid) return;
            var threadName = document.getElementById("kb-wa-thread-title").textContent || waSelectedJid;
            waRunDeleteChatConfirm(waSelectedJid, threadName);
        });

        document.addEventListener("keydown", function(e) {
            if (e.repeat) return;
            if (e.key !== "Delete" && e.key !== "Backspace") return;
            var cfm = document.getElementById("kb-confirm-modal");
            if (cfm && !cfm.classList.contains("hidden")) return;
            var msgView = document.getElementById("kb-wa-thread-view");
            if (!msgView || msgView.classList.contains("hidden")) return;
            if (!waSelectedJid || !isMessagesPanelVisible()) return;
            if (waThreadDeleteHotkeyIgnored()) return;
            e.preventDefault();
            e.stopPropagation();
            var threadName = document.getElementById("kb-wa-thread-title").textContent || waSelectedJid;
            waRunDeleteChatConfirm(waSelectedJid, threadName);
        }, true);

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
                    var phones = waChats.map(function(c) { return c.sender; });
                    waDeleteChatsByPhones(phones, function() {
                        hideKanbanConfirm();
                        waSidebarChatListMode = false;
                        waSelectedChatPhones = {};
                        updateWaSidebarFooterUi();
                        waSelectedJid = null;
                        loadWhatsAppChats(true);
                        showSnackbar("All chats deleted", "success");
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
            _setWaSendStatus("Attaching " + file.name + "...", "text-gray-500");
            _waBlobToBase64(file).then(function(b64) {
                var mime = String(file.type || "application/octet-stream");
                var kind = mime.indexOf("image/") === 0 ? "image" : "document";
                waPendingAttachment = {
                    name: file.name || ("attachment-" + Date.now()),
                    mime_type: mime,
                    data_b64: b64,
                    kind: kind
                };
                _setWaSendStatus("Attached: " + waPendingAttachment.name, "text-green-400");
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
        document.getElementById("kb-wa-thread-send").addEventListener("click", sendWhatsAppThreadMessage);
        document.getElementById("kb-wa-thread-select-toggle").addEventListener("click", function() {
            if (!waSelectedJid) {
                showSnackbar("Open a thread first");
                return;
            }
            setWaSelectionMode(!waSelectionMode);
        });
        document.getElementById("kb-wa-thread-voice").addEventListener("click", function() {
            if (waVoiceRecording) {
                stopWhatsAppVoiceRecordingAndSend();
            } else {
                startWhatsAppVoiceRecording();
            }
        });
        document.getElementById("kb-wa-thread-input").addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendWhatsAppThreadMessage();
            }
        });
        _updateWhatsAppComposerState();

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
        document.getElementById("kb-wa-link-confirm").addEventListener("click", confirmWaLink);

        // Message context menu
        document.querySelector(".kb-wa-msgctx-ticket").addEventListener("click", waMsgCtxCreateTicket);
        document.querySelector(".kb-wa-msgctx-mark-processed").addEventListener("click", waMsgCtxMarkProcessed);
        document.querySelector(".kb-wa-msgctx-delete").addEventListener("click", waMsgCtxDelete);
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
        document.getElementById("kb-bm-wa-add-btn").addEventListener("click", addWaLinkFromBoardModal);

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
                updateTabBarVisibility();
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

    // ═══════════════════════════════════════════════════════════════════
    // End WhatsApp Integration
    // ═══════════════════════════════════════════════════════════════════
    function init() {
        // Apply sidebar tab from URL before any initial board rendering to avoid tab flicker.
        var initialSidebarTab = getSidebarTabFromUrl();
        if (initialSidebarTab === "messages") {
            switchSidebarTab("messages");
        }

        // Board sidebar
        document.getElementById("kb-add-board").addEventListener("click", function() { openBoardModal(null); });
        document.getElementById("kb-create-big").addEventListener("click", function() { openBoardModal(null); });
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
        // Source tab switching
        document.querySelectorAll(".kb-src-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchSourceTab(btn.dataset.src); });
        });
        document.getElementById("kb-search").addEventListener("input", function() { loadBoards(); });

        // Global settings modal
        document.getElementById("kb-settings-cog").addEventListener("click", openGlobalSettingsModal);
        document.getElementById("kb-gs-close").addEventListener("click", closeGlobalSettingsModal);
        document.getElementById("kb-gs-cancel").addEventListener("click", closeGlobalSettingsModal);
        document.getElementById("kb-gs-save").addEventListener("click", saveGlobalKanbanSettings);
        document.getElementById("kb-global-settings-modal").addEventListener("click", function(e) {
            // Clicking overlay does NOT close — use close/cancel buttons
        });

        // Global settings frequency change
        document.getElementById("kb-gs-frequency").addEventListener("change", function() {
            gsFreq = this.value;
            updateGsFrequencyUI(gsFreq);
        });

        // Global settings hour picker toggles
        document.querySelectorAll(".kb-gs-hour").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var cur = btn.getAttribute("data-selected") === "1";
                btn.setAttribute("data-selected", cur ? "0" : "1");
            });
        });

        // Global settings day picker toggles
        document.querySelectorAll(".kb-gs-day").forEach(function(btn) {
            btn.addEventListener("click", function() {
                var cur = btn.getAttribute("data-selected") === "1";
                btn.setAttribute("data-selected", cur ? "0" : "1");
            });
        });

        // Global settings LLM provider change → reload models
        ["kb-gs-orch", "kb-gs-coder", "kb-gs-sub"].forEach(function(prefix) {
            document.getElementById(prefix + "-provider").addEventListener("change", function() {
                loadAgentModels(prefix, this.value, "");
            });
        });

        // Board actions
        document.getElementById("kb-add-ticket").addEventListener("click", addTicket);
        document.getElementById("kb-edit-board").addEventListener("click", function() {
            if (currentBoard && currentBoard.source === "database" && currentBoard.id) {
                openBoardModal(currentBoard.id);
            } else if (currentBoard && (currentBoard.source === "trello" || currentBoard.source === "jira")) {
                // External board — open config modal
                openExternalBoardConfigModal(currentBoard.source, currentBoard.id);
            } else if (currentBoardData && currentBoardData.id) {
                openBoardModal(currentBoardData.id);
            }
        });
        document.getElementById("kb-delete-board").addEventListener("click", deleteBoard);

        // Board modal tabs
        document.querySelectorAll(".kb-bm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchBoardModalTab(btn.dataset.tab); });
        });

        // Ticket modal tabs
        document.querySelectorAll(".kb-tm-tab").forEach(function(btn) {
            btn.addEventListener("click", function() { switchTicketTab(btn.dataset.ttab); });
        });

        // Board modal
        document.getElementById("kb-board-modal-cancel").addEventListener("click", closeBoardModal);
        document.getElementById("kb-board-modal-x").addEventListener("click", closeBoardModal);
        document.getElementById("kb-board-modal-save").addEventListener("click", saveBoardModal);
        document.getElementById("kb-board-modal").addEventListener("click", function(e) {
        });
        // Color picker sync
        document.getElementById("kb-board-modal-color").addEventListener("input", function() {
            document.getElementById("kb-board-modal-color-hex").textContent = this.value;
        });
        document.getElementById("kb-board-modal-color-reset").addEventListener("click", function() {
            var colorInput = document.getElementById("kb-board-modal-color");
            colorInput.value = "#f97316";
            document.getElementById("kb-board-modal-color-hex").textContent = "#f97316";
        });

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
            if (_kbConfirmCallback) _kbConfirmCallback();
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
                if (_kbConfirmCallback) _kbConfirmCallback();
            }
        }, true);

        // Copy modal
        document.getElementById("kb-copy-cancel").addEventListener("click", closeCopyModal);
        document.getElementById("kb-copy-confirm").addEventListener("click", confirmCopy);
        document.getElementById("kb-copy-modal").addEventListener("click", function(e) {
        });


        // Load initial data first (populates board menu with context menu elements)
        loadBoards();
        initWhatsApp();
        connectKanbanBoardWS();
        
        // Context menu (after boards are populated to ensure DOM elements exist)
        document.querySelector(".kb-ctx-activate").addEventListener("click", ctxActivateBoard);
        document.querySelector(".kb-ctx-configure").addEventListener("click", ctxConfigureBoard);
        document.querySelector(".kb-ctx-rename").addEventListener("click", ctxRenameBoard);
        document.querySelector(".kb-ctx-archive").addEventListener("click", ctxArchiveBoard);
        document.querySelector(".kb-ctx-delete").addEventListener("click", ctxDeleteBoard);
        document.addEventListener("click", function(e) {
            if (!e.target.closest("#kb-board-ctx-menu")) hideBoardContextMenu();
            if (!e.target.closest("#kb-ext-board-ctx-menu")) hideExtBoardContextMenu();
        });
        // External board context menu
        document.querySelector(".kb-ext-ctx-configure").addEventListener("click", extCtxConfigure);
        // Create external ticket modal
        document.getElementById("kb-cet-cancel").addEventListener("click", closeCreateExtTicketModal);
        document.getElementById("kb-cet-cancel-2").addEventListener("click", closeCreateExtTicketModal);
        document.getElementById("kb-cet-submit").addEventListener("click", submitCreateExtTicket);
        document.getElementById("kb-cet-file-input").addEventListener("change", handleCetFileSelect);
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
            if (e.target === this) closeCreateExtTicketModal();
        });
        
        // Initially hide the tab bar since Messages is hidden by default
        updateTabBarVisibility();
    }

    // Auto-refresh provider dropdowns when third-party keys are saved (same page)
    window.addEventListener('thirdparty-providers-changed', function() {
        if (typeof loadAgentProviders === 'function') loadAgentProviders();
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
