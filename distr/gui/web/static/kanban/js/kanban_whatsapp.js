(function() {
    "use strict";

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

    function parseWaDateSeconds(value) {
        if (!value) return 0;
        if (typeof value === "number") return value > 100000000000 ? Math.floor(value / 1000) : value;
        var numeric = Number(value);
        if (!isNaN(numeric) && numeric > 0) return numeric > 100000000000 ? Math.floor(numeric / 1000) : numeric;
        var parsed = Date.parse(value);
        return isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
    }

    function getWaMessageSortTs(msg) {
        if (!msg) return 0;
        return parseWaDateSeconds(msg.whatsapp_timestamp) ||
            parseWaDateSeconds(msg.timestamp) ||
            parseWaDateSeconds(msg.created_date) ||
            parseWaDateSeconds(msg.received_at) ||
            parseWaDateSeconds(msg.received_at_utc) ||
            0;
    }

    function sortWaMessagesAsc(a, b) {
        var diff = getWaMessageSortTs(a) - getWaMessageSortTs(b);
        if (diff) return diff;
        return (Number(a && a.id) || 0) - (Number(b && b.id) || 0);
    }

    function dedupeThreadMessages(messages) {
        var seen = {};
        return (messages || []).filter(function(msg) {
            // Include WhatsApp message_id and local DB id — otherwise two media-only
            // messages in the same second (empty text/caption) collapse and the wrong
            // row id is used for /relay-media, producing broken images.
            var key = [
                msg.jid || "",
                msg.from_me ? "1" : "0",
                String(msg.whatsapp_timestamp || ""),
                msg.text || "",
                msg.caption || "",
                msg.media_type || "",
                msg.media_filename || "",
                String(msg.message_id || ""),
                String(msg.id != null ? msg.id : "")
            ].join("|");
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
    }

    function waDownloadJsonFile(filename, payload) {
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
    }

    function waResolveTargetJid(sender, chatType) {
        if (!sender) return "";
        if (sender.indexOf("@") !== -1) return sender;
        if (chatType === "group") return sender + "@g.us";
        return sender + "@s.whatsapp.net";
    }

    function waThreadMessagesUrl(sender, limit) {
        return "/api/tickets/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=" + (limit || 200) + "&sort=desc";
    }

    function scrollWaThreadToBottom(msgList) {
        if (!msgList) return;
        var scrollNow = function() {
            msgList.scrollTop = msgList.scrollHeight;
        };
        scrollNow();
        requestAnimationFrame(scrollNow);
        setTimeout(scrollNow, 100);
        setTimeout(scrollNow, 350);
        msgList.querySelectorAll("img, video").forEach(function(el) {
            el.addEventListener("load", scrollNow, { once: true });
            el.addEventListener("loadedmetadata", scrollNow, { once: true });
        });
    }

    function waBlobToBase64(blob) {
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

    function formatVoiceDuration(totalSeconds) {
        var secs = Math.max(0, totalSeconds | 0);
        var mm = String(Math.floor(secs / 60)).padStart(2, "0");
        var ss = String(secs % 60).padStart(2, "0");
        return mm + ":" + ss;
    }

    function getWaVoiceFilename(mimeType) {
        if (!mimeType) return "voice-note.ogg";
        if (mimeType.indexOf("webm") >= 0) return "voice-note.webm";
        if (mimeType.indexOf("ogg") >= 0 || mimeType.indexOf("opus") >= 0) return "voice-note.ogg";
        if (mimeType.indexOf("mp4") >= 0 || mimeType.indexOf("m4a") >= 0) return "voice-note.m4a";
        if (mimeType.indexOf("mpeg") >= 0 || mimeType.indexOf("mp3") >= 0) return "voice-note.mp3";
        return "voice-note.ogg";
    }

    function create(deps) {
        var waBoardLinkCandidatesByJid = {};
        var waBoardLinkedByJid = {};

        function syncWaChatSelectLinkedState() {
            var chatSelect = document.getElementById("kb-bm-wa-chat-select");
            if (!chatSelect) return;
            var selected = String(chatSelect.value || "").trim();
            var firstLinkedJid = "";
            var linkedValues = {};
            Array.prototype.forEach.call(chatSelect.options, function(opt) {
                if (!opt.value) return;
                var candidate = waBoardLinkCandidatesByJid[opt.value] || {};
                var baseLabel = candidate.name || opt.value;
                var isLinked = !!waBoardLinkedByJid[opt.value];
                opt.textContent = baseLabel;
                opt.dataset.linked = isLinked ? "1" : "";
                if (isLinked) {
                    linkedValues[opt.value] = true;
                    if (!firstLinkedJid) firstLinkedJid = opt.value;
                }
            });
            if (!selected && firstLinkedJid) chatSelect.value = firstLinkedJid;
            if (window.KanbanCustomSelect) {
                var custom = chatSelect._kbCustomSelect || window.KanbanCustomSelect.upgradeById("kb-bm-wa-chat-select", { placeholder: "Select chat..." });
                if (custom) custom.setLinkedValues(linkedValues);
                else window.KanbanCustomSelect.refresh(chatSelect);
            }
        }

        function populateWaChatSelect(chats) {
            var chatSelect = document.getElementById("kb-bm-wa-chat-select");
            if (!chatSelect) return;
            waBoardLinkCandidatesByJid = {};
            chatSelect.innerHTML = '<option value="">Select chat...</option>';
            (chats || []).forEach(function(chat) {
                var jid = String(chat.id || "").trim();
                var name = String(chat.name || "").trim();
                if (!jid || !name) return;
                var phone = jid.split("@")[0].split(":")[0];
                var isGroup = jid.indexOf("@g.us") >= 0;
                waBoardLinkCandidatesByJid[jid] = {
                    jid: jid,
                    phone: phone,
                    name: name,
                    chat_type: isGroup ? "group" : "private",
                };
                var opt = document.createElement("option");
                opt.value = jid;
                opt.textContent = name;
                chatSelect.appendChild(opt);
            });
            syncWaChatSelectLinkedState();
        }

        function loadWaGroupPeopleCandidates(selectedChat) {
            return Promise.resolve(selectedChat || null);
        }

        function loadWaLinkCandidates(silent) {
            return deps.apiFetch("/api/tickets/whatsapp/chats?limit=500&offset=0&search=").then(function(chatsData) {
                populateWaChatSelect((chatsData && chatsData.chats) || []);
                syncWaChatSelectLinkedState();
            }).catch(function(err) {
                if (!silent) deps.showSnackbar("Failed to load WhatsApp chat list: " + err.message, "error");
            });
        }

        function confirmWaLink() {
            var boardId = parseInt(document.getElementById("kb-wa-link-board").value);
            if (!boardId) { deps.showSnackbar("Select a board"); return; }
            var autoSnapshot = document.getElementById("kb-wa-link-auto").checked;
            var waCtxMenuData = deps.getWaCtxMenuData();
            deps.apiFetch("/api/tickets/boards/" + boardId + "/whatsapp-links", {
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
                    deps.showSnackbar("Linked " + waCtxMenuData.name + " to project board");
                    document.getElementById("kb-wa-link-modal").classList.add("hidden");
                }
            }).catch(function(err) {
                deps.showSnackbar("Failed to link: " + err.message);
            });
        }

        function waMsgCtxCreateTicket() {
            deps.hideWaMsgContextMenu();
            var waMsgCtxData = deps.getWaMsgCtxData();
            if (!waMsgCtxData) return;
            if (deps.getWaSelectionMode()) {
                deps.waCtxSnapshotToBoardBottom();
                return;
            }
            var msgId = waMsgCtxData.message_id;
            var phone = deps.getWaSelectedJid();
            var threadName = document.getElementById("kb-wa-thread-title").textContent;
            var msgEl = document.querySelector('[data-wa-msg-id="' + msgId + '"]');
            var msgText = "";
            if (msgEl) {
                var textEl = msgEl.querySelector(".wa-msg-text");
                if (textEl) msgText = textEl.textContent;
            }
            var title = threadName + " - Message";
            var desc = msgText || "WhatsApp message";
            deps.apiFetch("/api/tickets/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                deps.openTicketFromWhatsApp(phone, title, desc, linkData.board_id || null, [{ id: msgId }]);
            }).catch(function() {
                deps.openTicketFromWhatsApp(phone, title, desc, null, [{ id: msgId }]);
            });
        }

        function waMsgCtxMarkProcessed() {
            deps.hideWaMsgContextMenu();
            var waMsgCtxData = deps.getWaMsgCtxData();
            if (!waMsgCtxData) return;
            deps.apiFetch("/api/tickets/whatsapp/messages/" + waMsgCtxData.message_id + "/processed", {
                method: "POST"
            }).then(function(r) {
                if (r.success) {
                    deps.showSnackbar("Marked as processed");
                    if (deps.getWaSelectedJid()) deps.refreshWaThreadIfOpen();
                }
            }).catch(function(err) {
                deps.showSnackbar("Failed to mark processed: " + err.message);
            });
        }

        function waMsgCtxDelete() {
            deps.hideWaMsgContextMenu();
            if (deps.getWaSelectionMode() && deps.getWaSelectedJid()) {
                var threadName = document.getElementById("kb-wa-thread-title").textContent || deps.getWaSelectedJid();
                deps.waRunDeleteChatConfirm(deps.getWaSelectedJid(), threadName);
                return;
            }
            var waMsgCtxData = deps.getWaMsgCtxData();
            if (!waMsgCtxData) return;
            deps.apiFetch("/api/tickets/whatsapp/messages/" + waMsgCtxData.message_id, {
                method: "DELETE"
            }).then(function(r) {
                if (r.success) {
                    deps.showSnackbar("Message deleted");
                    if (deps.getWaSelectedJid()) deps.refreshWaThreadIfOpen();
                }
            }).catch(function(err) {
                deps.showSnackbar("Failed to delete: " + err.message);
            });
        }

        function unlinkAllBoardWaLinks(boardId) {
            if (!boardId) {
                waBoardLinkedByJid = {};
                syncWaChatSelectLinkedState();
                return Promise.resolve();
            }
            return deps.apiFetch("/api/tickets/boards/" + boardId + "/whatsapp-links").then(function(links) {
                var removals = (links || []).map(function(link) {
                    return deps.apiFetch("/api/tickets/boards/" + boardId + "/whatsapp-links/" + link.id, { method: "DELETE" });
                });
                if (!removals.length) {
                    waBoardLinkedByJid = {};
                    syncWaChatSelectLinkedState();
                    return;
                }
                return Promise.all(removals).then(function() {
                    waBoardLinkedByJid = {};
                    var chatSelect = document.getElementById("kb-bm-wa-chat-select");
                    if (chatSelect) chatSelect.value = "";
                    syncWaChatSelectLinkedState();
                });
            });
        }

        function loadBoardWaLinks(boardId) {
            if (!boardId) {
                waBoardLinkedByJid = {};
                syncWaChatSelectLinkedState();
                return Promise.resolve([]);
            }
            return deps.apiFetch("/api/tickets/boards/" + boardId + "/whatsapp-links").then(function(links) {
                waBoardLinkedByJid = {};
                var boardLinkedJid = "";
                (links || []).forEach(function(l) {
                    var jid = String(l.phone_jid || "").trim();
                    if (jid) {
                        waBoardLinkedByJid[jid] = l;
                        if (!boardLinkedJid) boardLinkedJid = jid;
                    }
                });
                var chatSelect = document.getElementById("kb-bm-wa-chat-select");
                if (chatSelect && boardLinkedJid) chatSelect.value = boardLinkedJid;
                syncWaChatSelectLinkedState();
                return links || [];
            });
        }

        function saveSelectedWaLinkForBoard(boardId) {
            var chatSelect = document.getElementById("kb-bm-wa-chat-select");
            var selectedJid = chatSelect ? String(chatSelect.value || "").trim() : "";
            if (!boardId) return Promise.reject(new Error("Save the board before linking WhatsApp"));
            if (!selectedJid) return unlinkAllBoardWaLinks(boardId).then(function() { return { skipped: true, unlinked: true }; });
            var selectedChat = waBoardLinkCandidatesByJid[selectedJid] || null;
            if (!selectedChat) return Promise.reject(new Error("Invalid WhatsApp chat selection"));
            if (waBoardLinkedByJid[selectedJid]) return Promise.resolve({ skipped: true, linked: true });
            var linkJid = selectedChat.jid;
            var linkPhone = selectedChat.phone || linkJid.split("@")[0].split(":")[0];
            var linkName = selectedChat.name;

            return unlinkAllBoardWaLinks(boardId).then(function() {
                return deps.apiFetch("/api/tickets/boards/" + boardId + "/whatsapp-links", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        phone_jid: linkJid,
                        phone_number: linkPhone,
                        contact_name: linkName,
                    })
                });
            }).then(function(r) {
                if (r && r.success) {
                    waBoardLinkedByJid[linkJid] = {
                        id: r.id,
                        board_id: boardId,
                        phone_jid: linkJid,
                        phone_number: linkPhone,
                        contact_name: linkName,
                    };
                    syncWaChatSelectLinkedState();
                }
                return r;
            });
        }

        function addWaLinkFromBoardModal() {
            var boardId = deps.getEditingBoardId();
            return saveSelectedWaLinkForBoard(boardId).then(function(r) {
                if (r && r.unlinked) deps.showSnackbar("WhatsApp chat unlinked from board");
                else if (r && !r.skipped) deps.showSnackbar("WhatsApp chat linked to board");
                if (boardId) loadBoardWaLinks(boardId);
                return r;
            }).catch(function(err) {
                deps.showSnackbar(err.message || "Failed to link WhatsApp chat", "error");
            });
        }

        function handleBoardWaChatSelectChange(boardId, selectedJid) {
            if (!boardId) return Promise.resolve();
            if (!selectedJid) return unlinkAllBoardWaLinks(boardId);
            return Promise.resolve();
        }

        function showWaChatContextMenu(x, y) {
            var menu = document.getElementById("kb-wa-ctx-menu");
            menu.style.left = x + "px";
            menu.style.top = y + "px";
            menu.classList.remove("hidden");
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

        function hideWaMsgContextMenuLocal() {
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
            var state = deps.getWaState();
            if (statusEl) statusEl.textContent = "Clearing server messages...";
            deps.apiFetch("/api/tickets/whatsapp/relay/clear-messages", { method: "POST" }).then(function(result) {
                if (result && result.success) {
                    deps.showSnackbar("Server messages cleared", "success");
                    state.waSelectedJid = null;
                    state.waSelectedChatType = "private";
                    state.waThreadMessages = [];
                    state.waSelectedMessageIds = {};
                    state.waSelectionMode = false;
                    deps.setWaState(state);
                    deps.updateWaThreadSelectToggleUi();
                    if (deps.isMessagesPanelVisible()) deps.showWhatsAppNoMessagesState();
                    deps.loadWhatsAppChats(true);
                    return;
                }
                var reason = (result && (result.error || result.detail)) || "Unknown error";
                deps.showSnackbar("Failed to clear server messages: " + reason, "error");
            }).catch(function(err) {
                deps.showSnackbar("Failed to clear server messages: " + err.message, "error");
            }).finally(function() {
                if (statusEl) statusEl.textContent = "";
            });
        }

        function waCtxViewMessages() {
            hideWaChatContextMenu();
            var waCtxMenuData = deps.getWaCtxMenuData();
            if (!waCtxMenuData) return;
            var state = deps.getWaState();
            state.waSelectedJid = waCtxMenuData.phone;
            deps.setWaState(state);
            deps.renderWhatsAppChatList();
            deps.showWhatsAppThread(waCtxMenuData.phone, waCtxMenuData.name);
        }

        function waCtxLinkToBoard() {
            hideWaChatContextMenu();
            var waCtxMenuData = deps.getWaCtxMenuData();
            if (!waCtxMenuData) return;
            deps.openWaLinkModal(waCtxMenuData);
        }

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

        function waRunDeleteChatConfirm(phone, displayName) {
            if (!phone) return;
            var name = displayName || phone;
            deps.showKanbanConfirm({
                title: "Delete Chat",
                message: "Delete all messages from " + name + "? This cannot be undone.",
                confirmLabel: "Delete",
                danger: true,
                onConfirm: function() {
                    deps.apiFetch("/api/tickets/whatsapp/chat/" + encodeURIComponent(phone), { method: "DELETE" }).then(function(r) {
                        deps.hideKanbanConfirm();
                        if (r && r.success) {
                            deps.showSnackbar("Deleted " + (r.deleted || "all") + " messages from " + name);
                            var state = deps.getWaState();
                            state.waSelectedJid = null;
                            state.waSelectedChatType = "private";
                            state.waThreadMessages = [];
                            state.waSelectedMessageIds = {};
                            state.waSelectionMode = false;
                            deps.setWaState(state);
                            deps.updateWaThreadSelectToggleUi();
                            deps.renderWhatsAppChatList();
                            deps.loadWhatsAppChats(true);
                        } else {
                            deps.showSnackbar("Failed to delete chat", "error");
                        }
                    }).catch(function(err) {
                        deps.hideKanbanConfirm();
                        deps.showSnackbar("Failed to delete: " + err.message, "error");
                    });
                }
            });
        }

        function waCtxDeleteChat() {
            hideWaChatContextMenu();
            var waCtxMenuData = deps.getWaCtxMenuData();
            if (!waCtxMenuData) return;
            waRunDeleteChatConfirm(waCtxMenuData.phone, waCtxMenuData.name);
        }

        function waCtxSnapshotToBoard() {
            hideWaChatContextMenu();
            var waCtxMenuData = deps.getWaCtxMenuData();
            if (!waCtxMenuData) return;
            var phone = waCtxMenuData.phone;
            var threadName = waCtxMenuData.name;
            deps.apiFetch("/api/tickets/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                var boardId = linkData.board_id || null;
                deps.apiFetch("/api/tickets/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                    var msgs = data.messages || [];
                    if (!msgs.length) { deps.showSnackbar("No messages from " + threadName); return; }
                    var unTicketed = msgs.filter(function(m) { return !deps.waMsgHasLinkedTicket(m); });
                    if (!unTicketed.length) { deps.showSnackbar("All messages already in tickets"); return; }
                    var ticketTitle = deps.esc(threadName) + " - " + unTicketed.length + " messages";
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
                    deps.openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, boardId, unTicketed);
                });
            }).catch(function() {
                deps.apiFetch("/api/tickets/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                    var msgs = data.messages || [];
                    if (!msgs.length) { deps.showSnackbar("No messages from " + threadName); return; }
                    var unTicketed = msgs.filter(function(m) { return !deps.waMsgHasLinkedTicket(m); });
                    if (!unTicketed.length) { deps.showSnackbar("All messages already in tickets"); return; }
                    var ticketTitle = deps.esc(threadName) + " - " + unTicketed.length + " messages";
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
                    deps.openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, null, unTicketed);
                });
            });
        }

        function waCtxSnapshotToBoardBottom() {
            var state = deps.getWaState();
            if (!state.waSelectedJid) return;
            var phone = state.waSelectedJid;
            var threadName = document.getElementById("kb-wa-thread-title").textContent;
            deps.apiFetch("/api/tickets/boards").then(function(boards) {
                var dbBs = boards.filter(function(b) { return b.source === "database"; });
                if (!dbBs.length) { deps.showSnackbar("No database boards to create ticket in"); return; }
                var currentBoard = deps.getCurrentBoard();
                var targetBoard = (currentBoard && currentBoard.source === "database") ? currentBoard.id : dbBs[0].id;
                return deps.apiFetch("/api/tickets/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                    if (linkData && linkData.board_id) targetBoard = linkData.board_id;
                    return deps.apiFetch("/api/tickets/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500");
                }).then(function(data) {
                    if (!data) return;
                    var allMsgs = data.messages || [];
                    var msgs = deps.collectWaSnapshotMessages(allMsgs);
                    if (!msgs.length) {
                        deps.showSnackbar(state.waSelectionMode ? "Select at least one available message" : "No new messages to add to a ticket");
                        return;
                    }
                    var ticketTitle = deps.esc(threadName) + " - " + msgs.length + " messages";
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
                    deps.openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, targetBoard, msgs);
                }).catch(function() {
                    return deps.apiFetch("/api/tickets/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
                        if (!data) return;
                        var allMsgs = data.messages || [];
                        var msgs = deps.collectWaSnapshotMessages(allMsgs);
                        if (!msgs.length) {
                            deps.showSnackbar(state.waSelectionMode ? "Select at least one available message" : "No new messages to add to a ticket");
                            return;
                        }
                        var ticketTitle = deps.esc(threadName) + " - " + msgs.length + " messages";
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
                        deps.openTicketFromWhatsApp(phone, ticketTitle, ticketDesc, targetBoard, msgs);
                    });
                });
            });
        }

        function initWhatsApp() {
            document.getElementById("kb-tab-tickets").addEventListener("click", function() { deps.switchSidebarTab("tickets"); });
            document.getElementById("kb-tab-messages").addEventListener("click", function() { deps.switchSidebarTab("messages"); });
            document.getElementById("kb-wa-thread-delete").addEventListener("click", function() {
                if (!deps.getWaSelectedJid()) return;
                var threadName = document.getElementById("kb-wa-thread-title").textContent || deps.getWaSelectedJid();
                deps.waRunDeleteChatConfirm(deps.getWaSelectedJid(), threadName);
            });
            document.addEventListener("keydown", function(e) {
                if (e.repeat) return;
                if (e.key !== "Delete" && e.key !== "Backspace") return;
                var cfm = document.getElementById("kb-confirm-modal");
                if (cfm && !cfm.classList.contains("hidden")) return;
                if (deps.isAnyKanbanModalOpen("kb-confirm-modal")) return;
                var msgView = document.getElementById("kb-wa-thread-view");
                if (!msgView || msgView.classList.contains("hidden")) return;
                if (!deps.getWaSelectedJid() || !deps.isMessagesPanelVisible()) return;
                if (deps.waThreadDeleteHotkeyIgnored()) return;
                e.preventDefault();
                e.stopPropagation();
                var threadName = document.getElementById("kb-wa-thread-title").textContent || deps.getWaSelectedJid();
                deps.waRunDeleteChatConfirm(deps.getWaSelectedJid(), threadName);
            }, true);
            deps.bindWhatsAppUiHandlers();
            deps.startWhatsAppBootstrap();
        }

        return {
            confirmWaLink: confirmWaLink,
            waMsgCtxCreateTicket: waMsgCtxCreateTicket,
            waMsgCtxMarkProcessed: waMsgCtxMarkProcessed,
            waMsgCtxDelete: waMsgCtxDelete,
            loadBoardWaLinks: loadBoardWaLinks,
            unlinkAllBoardWaLinks: unlinkAllBoardWaLinks,
            handleBoardWaChatSelectChange: handleBoardWaChatSelectChange,
            addWaLinkFromBoardModal: addWaLinkFromBoardModal,
            saveSelectedWaLinkForBoard: saveSelectedWaLinkForBoard,
            showWaChatContextMenu: showWaChatContextMenu,
            hideWaChatContextMenu: hideWaChatContextMenu,
            showWaMsgContextMenu: showWaMsgContextMenu,
            hideWaMsgContextMenuLocal: hideWaMsgContextMenuLocal,
            showWaSyncContextMenu: showWaSyncContextMenu,
            hideWaSyncContextMenu: hideWaSyncContextMenu,
            clearWaServerMessages: clearWaServerMessages,
            waCtxViewMessages: waCtxViewMessages,
            waCtxLinkToBoard: waCtxLinkToBoard,
            waThreadDeleteHotkeyIgnored: waThreadDeleteHotkeyIgnored,
            waRunDeleteChatConfirm: waRunDeleteChatConfirm,
            waCtxDeleteChat: waCtxDeleteChat,
            waCtxSnapshotToBoard: waCtxSnapshotToBoard,
            waCtxSnapshotToBoardBottom: waCtxSnapshotToBoardBottom,
            initWhatsApp: initWhatsApp,
            loadWaLinkCandidates: loadWaLinkCandidates,
            loadWaGroupPeopleCandidates: loadWaGroupPeopleCandidates,
            syncWaChatSelectLinkedState: syncWaChatSelectLinkedState,
        };
    }

    function createRuntime(deps) {
        var waDraftSaveTimers = {};
        var waDraftLastSaved = {};

        function loadWaDraftMap() {
            return deps.apiFetch("/api/tickets/whatsapp/drafts").then(function(data) {
                var map = {};
                (data.drafts || []).forEach(function(d) {
                    if (d && d.jid_phone && String(d.text || "").trim()) map[d.jid_phone] = d;
                });
                var state = deps.getState();
                state.waDraftByPhone = map;
                deps.setState(state);
                return map;
            }).catch(function() { return {}; });
        }

        function waHasDraft(jidPhone) {
            var d = (deps.getState().waDraftByPhone || {})[jidPhone];
            return !!(d && String(d.text || "").trim());
        }

        function waDraftIndicatorHtml(jidPhone) {
            if (!waHasDraft(jidPhone)) return "";
            return '<span class="kb-wa-draft-dot inline-block w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" title="Draft waiting"></span>';
        }

        function flushWaDraftSave(jidPhone, fromSwitch) {
            if (!jidPhone) return Promise.resolve();
            var inputEl = document.getElementById("kb-wa-thread-input");
            var state = deps.getState();
            var isActive = state.waSelectedJid === jidPhone;
            var cached = (state.waDraftByPhone || {})[jidPhone] || {};
            var text = isActive && inputEl ? (inputEl.value || "") : (cached.text || "");
            var trimmed = String(text || "").trim();
            if (waDraftSaveTimers[jidPhone]) {
                clearTimeout(waDraftSaveTimers[jidPhone]);
                delete waDraftSaveTimers[jidPhone];
            }
            if (trimmed === (waDraftLastSaved[jidPhone] || "")) return Promise.resolve();

            var chat = (state.waChats || []).find(function(c) { return c.sender === jidPhone; }) || {};
            var msgView = document.getElementById("kb-wa-thread-view");
            var targetJid = (msgView && msgView.dataset.waTargetJid) || (state.waActiveThread && state.waActiveThread.target_jid) || "";
            var payload = {
                text: text,
                jid: targetJid || "",
                chat_type: chat.chat_type || state.waSelectedChatType || "private",
                contact_name: chat.name || (state.waActiveThread && state.waActiveThread.name) || "",
                source: "user"
            };

            return deps.apiFetch("/api/tickets/whatsapp/drafts/" + encodeURIComponent(jidPhone), {
                method: "PUT",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }).then(function(resp) {
                var stateNow = deps.getState();
                stateNow.waDraftByPhone = stateNow.waDraftByPhone || {};
                if (resp && resp.draft && String(resp.draft.text || "").trim()) {
                    stateNow.waDraftByPhone[jidPhone] = resp.draft;
                    waDraftLastSaved[jidPhone] = String(resp.draft.text || "").trim();
                } else {
                    delete stateNow.waDraftByPhone[jidPhone];
                    delete waDraftLastSaved[jidPhone];
                }
                deps.setState(stateNow);
                if (!fromSwitch) renderWhatsAppChatList();
            }).catch(function() {});
        }

        function scheduleWaDraftSave(jidPhone) {
            if (!jidPhone) return;
            if (waDraftSaveTimers[jidPhone]) clearTimeout(waDraftSaveTimers[jidPhone]);
            waDraftSaveTimers[jidPhone] = setTimeout(function() {
                delete waDraftSaveTimers[jidPhone];
                flushWaDraftSave(jidPhone, false);
            }, 450);
        }

        function applyWaDraftToComposer(jidPhone, name) {
            var inputEl = document.getElementById("kb-wa-thread-input");
            if (!inputEl || !jidPhone) return;
            var state = deps.getState();
            var draft = (state.waDraftByPhone || {})[jidPhone];
            if (draft && String(draft.text || "").trim()) {
                inputEl.value = draft.text;
                waDraftLastSaved[jidPhone] = String(draft.text || "").trim();
                if (draft.source === "agent") setWaSendStatus("Draft ready for review", "text-blue-400");
                return;
            }
            deps.apiFetch("/api/tickets/whatsapp/drafts/" + encodeURIComponent(jidPhone)).then(function(data) {
                if (deps.getState().waSelectedJid !== jidPhone) return;
                var d = data && data.draft;
                if (!d || !String(d.text || "").trim()) {
                    inputEl.value = "";
                    waDraftLastSaved[jidPhone] = "";
                    return;
                }
                var stateNow = deps.getState();
                stateNow.waDraftByPhone = stateNow.waDraftByPhone || {};
                stateNow.waDraftByPhone[jidPhone] = d;
                deps.setState(stateNow);
                inputEl.value = d.text;
                waDraftLastSaved[jidPhone] = String(d.text || "").trim();
                if (d.source === "agent") setWaSendStatus("Draft ready for review", "text-blue-400");
                renderWhatsAppChatList();
            }).catch(function() {});
        }

        function clearWaDraftAfterSend(jidPhone) {
            if (!jidPhone) return;
            if (waDraftSaveTimers[jidPhone]) {
                clearTimeout(waDraftSaveTimers[jidPhone]);
                delete waDraftSaveTimers[jidPhone];
            }
            delete waDraftLastSaved[jidPhone];
            var state = deps.getState();
            if (state.waDraftByPhone) delete state.waDraftByPhone[jidPhone];
            deps.setState(state);
            deps.apiFetch("/api/tickets/whatsapp/drafts/" + encodeURIComponent(jidPhone), { method: "DELETE" }).catch(function() {});
            renderWhatsAppChatList();
        }

        function bindWaDraftComposer() {
            var inputEl = document.getElementById("kb-wa-thread-input");
            if (!inputEl || inputEl.dataset.waDraftBound) return;
            inputEl.dataset.waDraftBound = "1";
            inputEl.addEventListener("input", function() {
                var state = deps.getState();
                if (!state.waSelectedJid) return;
                scheduleWaDraftSave(state.waSelectedJid);
            });
            inputEl.addEventListener("blur", function() {
                var state = deps.getState();
                if (state.waSelectedJid) flushWaDraftSave(state.waSelectedJid, false);
            });
        }

        function isWaVoiceType(msg) {
            var t = String((msg && msg.media_type) || "").toLowerCase();
            var m = String((msg && msg.media_mime_type) || "").toLowerCase();
            return t === "voice" || t === "audio" || t === "ptt" || m.indexOf("audio/") === 0;
        }

        function isWaImageType(msg) {
            var t = String((msg && msg.media_type) || "").toLowerCase();
            var m = String((msg && msg.media_mime_type) || "").toLowerCase();
            return t === "photo" || t === "image" || m.indexOf("image/") === 0;
        }

        function isWaVideoType(msg) {
            var t = String((msg && msg.media_type) || "").toLowerCase();
            var m = String((msg && msg.media_mime_type) || "").toLowerCase();
            return t === "video" || m.indexOf("video/") === 0;
        }

        function buildWaMediaUrl(msg, format) {
            var base = "/api/tickets/whatsapp/relay-media/" + encodeURIComponent(msg.id);
            var q = [];
            if (format) q.push("format=" + encodeURIComponent(format));
            if (msg && msg.message_id) q.push("wa_key=" + encodeURIComponent(msg.message_id));
            if (q.length) base += "?" + q.join("&");
            return base;
        }

        function renderWaMediaHtml(msg) {
            if (!msg || !msg.media_type) return "";
            var mediaUrl = buildWaMediaUrl(msg, "");
            var safeName = deps.esc(msg.media_filename || (msg.media_type + "-attachment"));
            var imgStyle = "max-width:min(100%,360px);max-height:240px;width:auto;height:auto;object-fit:contain;display:block;";
            var vidStyle = "max-width:min(100%,360px);max-height:240px;width:auto;height:auto;";
            if (isWaImageType(msg)) {
                return '<div class="mt-1"><img src="' + mediaUrl + '" alt="' + safeName + '" class="rounded border border-white/10 object-contain bg-black/20" style="' + imgStyle + '" loading="lazy"></div>';
            }
            if (isWaVideoType(msg)) {
                return '<div class="mt-1"><video controls preload="metadata" class="rounded border border-white/10 bg-black/30" style="' + vidStyle + '"><source src="' + mediaUrl + '"></video></div>';
            }
            if (isWaVoiceType(msg)) {
                var m4aUrl = buildWaMediaUrl(msg, "m4a");
                return '<div class="mt-1"><audio controls preload="metadata" class="w-[280px] max-w-full"><source src="' + m4aUrl + '" type="audio/mp4"><source src="' + mediaUrl + '"></audio></div>';
            }
            return '<div class="mt-1"><a href="' + mediaUrl + '" target="_blank" rel="noopener" class="text-xs text-[#25D366] underline">Open attachment: ' + safeName + "</a></div>";
        }

        function loadWhatsAppChats(forceRefresh) {
            var el = document.getElementById("kb-wa-status");
            var chatListEl = document.getElementById("kb-wa-chats");
            var state = deps.getState();
            if (!state.waChats || !state.waChats.length || forceRefresh) el.textContent = "Loading...";
            var localUrl = "/api/tickets/whatsapp/messages?limit=2000&sort=desc";

            Promise.all([
                deps.apiFetch(localUrl).then(function(data) {
                    // Keep local DB as the source of truth for list rendering.
                    // Do not auto-hydrate from relay when local is empty; users can
                    // explicitly trigger sync via the Sync action.
                    return data || { messages: [] };
                }).catch(function() {
                    return fetchFromRelay().catch(function() { return { messages: [] }; });
                }),
                deps.apiFetch("/api/tickets/whatsapp/chats?limit=500&offset=0&search=").then(function(chatsData) {
                    var map = {};
                    var allNames = {};
                    var list = (chatsData && chatsData.chats) || [];
                    list.forEach(function(chat) {
                        var id = String((chat && chat.id) || "").trim();
                        var name = String((chat && chat.name) || "").trim();
                        if (!id || !name) return;
                        allNames[id] = name;
                        allNames[id.split("@")[0]] = name;
                        if (id.indexOf("@g.us") >= 0) map[id] = name;
                    });
                    return { groupNames: map, chatNames: allNames };
                }).catch(function() { return { groupNames: {}, chatNames: {} }; }),
                loadWaDraftMap().catch(function() { return {}; })
            ]).then(function(results) {
                state.waGroupNames = (results[1] && results[1].groupNames) || {};
                state.waChatNames = (results[1] && results[1].chatNames) || {};
                state.waDraftByPhone = results[2] || {};
                deps.setState(state);
                try {
                    processWhatsAppMessages(results[0] || { messages: [] });
                } catch (renderErr) {
                    console.error("WhatsApp render pipeline error:", renderErr);
                    var latestState = deps.getState();
                    if (latestState && latestState.waChats && latestState.waChats.length) {
                        return;
                    } else {
                        el.textContent = "WhatsApp data error";
                    }
                }
            }).catch(function() {
                var latestState = deps.getState();
                // Do not clobber an already-rendered chat list on transient failures.
                if (latestState && latestState.waChats && latestState.waChats.length) {
                    return;
                }
                el.textContent = "WhatsApp not connected";
                chatListEl.innerHTML = "";
                state.waConnected = false;
                deps.setState(state);
                var tabMessages = document.getElementById("kb-tab-messages");
                // Keep the Messages tab available even when relay/local fetch fails,
                // so the UI does not force-switch back to boards on transient failures.
                if (tabMessages) tabMessages.classList.remove("hidden");
                deps.updateTabBarVisibility();
            });

            function fetchFromRelay() {
                return deps.apiFetch("/api/tickets/whatsapp/relay/messages?limit=500").then(function(data) {
                    if (data.messages && data.messages.length >= 0) return data;
                    throw new Error("Relay returned no data");
                });
            }
        }

        function processWhatsAppMessages(data) {
            var state = deps.getState();
            var el = document.getElementById("kb-wa-status");
            var chatListEl = document.getElementById("kb-wa-chats");
            var messages = data.messages || [];
            var chatMap = {};
            var previousNameBySender = {};
            (state.waChats || []).forEach(function(c) {
                if (c && c.sender && c.name) previousNameBySender[String(c.sender)] = String(c.name);
            });
            messages.forEach(function(msg) {
                var chatPhone = msg.jid_phone || (msg.jid || "").split("@")[0];
                var chatName;
                if ((msg.jid || "").endsWith("@g.us") || msg.chat_type === "group") {
                    var groupJid = msg.jid || "";
                    var resolvedGroupName = msg.group_name || state.waGroupNames[groupJid] || state.waGroupNames[(chatPhone ? (chatPhone + "@g.us") : "")] || "";
                    chatName = resolvedGroupName || groupJid.split("@")[0] || chatPhone || "Group Chat";
                } else {
                    var privateName = state.waChatNames[msg.jid] || state.waChatNames[chatPhone] || previousNameBySender[chatPhone] || "";
                    // For outbound messages, sender fields usually describe the current user.
                    // Use sender identity only for inbound messages to avoid renaming threads to self.
                    if (msg.from_me) chatName = privateName || chatPhone || "Unknown";
                    else chatName = msg.sender_push_name || privateName || msg.sender_phone || chatPhone || "Unknown";
                }
                if (!chatMap[chatPhone]) {
                    var isGroup = (msg.jid || "").endsWith("@g.us") || msg.chat_type === "group";
                    chatMap[chatPhone] = { sender: chatPhone, name: chatName, messages: [], lastTs: 0, unread: 0, chat_type: isGroup ? "group" : "private" };
                }
                chatMap[chatPhone].messages.push(msg);
                var msgTs = getWaMessageSortTs(msg);
                if (msgTs > chatMap[chatPhone].lastTs) chatMap[chatPhone].lastTs = msgTs;
                if (!msg.processed) chatMap[chatPhone].unread++;
                if (!msg.from_me && msg.sender_push_name && chatMap[chatPhone].chat_type !== "group") chatMap[chatPhone].name = msg.sender_push_name;
            });

            // Merge outbound-only private alias threads (often @lid ids) into the most
            // likely inbound private thread by timestamp proximity.
            var chatKeys = Object.keys(chatMap);
            var aliasCandidates = chatKeys.filter(function(key) {
                var c = chatMap[key];
                if (!c || c.chat_type === "group") return false;
                var hasInbound = (c.messages || []).some(function(m) { return !m.from_me; });
                var hasOutbound = (c.messages || []).some(function(m) { return !!m.from_me; });
                if (!hasOutbound || hasInbound) return false;
                var knownName = state.waChatNames[key] || state.waChatNames[(key ? (key + "@s.whatsapp.net") : "")] || state.waChatNames[(key ? (key + "@lid") : "")];
                return !knownName;
            });
            if (aliasCandidates.length) {
                var targetCandidates = chatKeys.filter(function(key) {
                    var c = chatMap[key];
                    if (!c || c.chat_type === "group") return false;
                    return (c.messages || []).some(function(m) { return !m.from_me; });
                });
                var ALIAS_WINDOW_SECONDS = 3 * 60 * 60;
                aliasCandidates.forEach(function(aliasKey) {
                    var aliasChat = chatMap[aliasKey];
                    if (!aliasChat) return;
                    var bestKey = null;
                    var bestDiff = Number.POSITIVE_INFINITY;
                    targetCandidates.forEach(function(targetKey) {
                        if (!chatMap[targetKey] || targetKey === aliasKey) return;
                        var diff = Math.abs((chatMap[targetKey].lastTs || 0) - (aliasChat.lastTs || 0));
                        if (diff < bestDiff) {
                            bestDiff = diff;
                            bestKey = targetKey;
                        }
                    });
                    if (!bestKey || bestDiff > ALIAS_WINDOW_SECONDS) return;
                    var target = chatMap[bestKey];
                    target.messages = (target.messages || []).concat(aliasChat.messages || []);
                    target.messages.sort(sortWaMessagesAsc);
                    target.lastTs = Math.max(target.lastTs || 0, aliasChat.lastTs || 0);
                    delete chatMap[aliasKey];
                });
            }
            state.waChats = Object.values(chatMap);
            state.waChats.forEach(function(chat) {
                chat.messages = (chat.messages || []).sort(sortWaMessagesAsc);
                var latest = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
                chat.lastTs = latest ? getWaMessageSortTs(latest) : (chat.lastTs || 0);
            });
            state.waChats.sort(function(a, b) {
                var diff = (b.lastTs || 0) - (a.lastTs || 0);
                if (diff) return diff;
                return String(a.name || a.sender || "").localeCompare(String(b.name || b.sender || ""));
            });

            if (messages.length === 0) {
                // Keep empty-state copy in one place (chat list), avoid duplicate text.
                el.textContent = "";
                chatListEl.innerHTML = "<div class='text-xs text-gray-500 italic py-2'>" + (state.waConnected ? "No captured messages yet" : "Relay server unreachable. Messages captured locally will appear here.") + "</div>";
                if (!state.waConnected) {
                    document.getElementById("kb-tab-messages").classList.remove("hidden");
                    deps.updateTabBarVisibility();
                }
                state.waSelectedJid = null;
                state.waSelectedChatType = "private";
                state.waThreadMessages = [];
                state.waSelectedMessageIds = {};
                state.waSelectionMode = false;
                state.waSidebarChatListMode = false;
                state.waSelectedChatPhones = {};
                deps.setState(state);
                deps.updateWaThreadSelectToggleUi();
                deps.updateWaSidebarFooterUi();
                if (deps.isMessagesPanelVisible()) deps.showWhatsAppNoMessagesState();
                return;
            }
            state.waConnected = true;
            document.getElementById("kb-tab-messages").classList.remove("hidden");
            deps.updateTabBarVisibility();
            el.textContent = state.waChats.length + " contacts with messages";
            var hasSelected = state.waSelectedJid && state.waChats.some(function(chat) { return chat.sender === state.waSelectedJid; });
            if (!hasSelected && state.waChats.length) {
                state.waSelectedJid = state.waChats[0].sender;
                state.waSelectedChatType = state.waChats[0].chat_type || "private";
            }
            deps.setState(state);
            renderWhatsAppChatList();
            deps.updateWaSidebarFooterUi();
            if (deps.isMessagesPanelVisible() && state.waSelectedJid) deps.refreshWaThreadIfOpen();
            try { deps.publishWaSubscriptions(); } catch (err) {}
        }

        function renderWhatsAppChatList() {
            var state = deps.getState();
            var chatListEl = document.getElementById("kb-wa-chats");
            var searchInput = document.getElementById("kb-wa-search");
            var searchVal = ((searchInput && searchInput.value) || "").toLowerCase();
            var filtered = state.waChats;
            if (searchVal) {
                filtered = state.waChats.filter(function(c) {
                    return (c.name || "").toLowerCase().includes(searchVal) || (c.sender || "").includes(searchVal);
                });
            }
            if (!filtered.length) {
                if (state.waChats.length && state.waSidebarChatListMode) chatListEl.innerHTML = '<div class="text-xs text-gray-400 italic py-2 px-1 leading-snug">No chats match the search box. Clear search to see contacts and select them.</div>';
                else chatListEl.innerHTML = '<div class="text-xs text-gray-500 italic py-2">No incoming messages</div>';
                if (!state.waChats.length && deps.isMessagesPanelVisible()) deps.showWhatsAppNoMessagesState();
                return;
            }
            var html = "";
            filtered.forEach(function(chat) {
                var sender = chat.sender || "";
                var name = deps.esc(chat.name || sender);
                var unread = chat.unread || 0;
                var lastMsg = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
                var preview = "";
                if (lastMsg) {
                    preview = lastMsg.text ? lastMsg.text.substring(0, 60) : (lastMsg.media_type ? "📎 " + lastMsg.media_type : "");
                    if (lastMsg.caption && !lastMsg.text) preview = (lastMsg.caption || "").substring(0, 60);
                }
                var active = state.waSelectedJid === chat.sender ? " bg-[#25D366]/10 border-l-2 border-[#25D366]" : "";
                var timeStr = "";
                if (chat.lastTs) timeStr = new Date(chat.lastTs * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                var groupBadge = chat.chat_type === "group" ? ' <span class="text-[8px] bg-[#25D366]/20 text-[#25D366] px-1 rounded">Group</span>' : "";
                var rowSel = state.waSidebarChatListMode ? (!!state.waSelectedChatPhones[sender] ? " checked" : "") : "";
                html += '<div class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs hover:bg-white/5 focus:outline-none focus:ring-2 focus:ring-[#25D366]/50' + active + '" data-wa-sender="' + deps.esc(sender) + '" data-wa-name="' + deps.esc(name) + '" data-wa-chat-type="' + (chat.chat_type || "private") + '" tabindex="0" role="option" aria-selected="' + (state.waSelectedJid === chat.sender ? "true" : "false") + '">';
                if (state.waSidebarChatListMode) html += '<input type="checkbox" class="accent-[#25D366] w-3.5 h-3.5 shrink-0 kb-wa-chat-select" data-wa-phone="' + deps.esc(sender) + '"' + rowSel + " />";
                html += '<div class="w-8 h-8 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-xs font-bold flex-shrink-0">' + deps.esc(name.charAt(0).toUpperCase()) + "</div>";
                html += '<div class="flex-1 min-w-0"><div class="flex items-center justify-between"><div class="flex items-center gap-1 min-w-0"><span class="text-white truncate font-medium">' + name + "</span>" + waDraftIndicatorHtml(sender) + groupBadge + '</div><span class="text-gray-500 text-[10px] ml-1 flex-shrink-0">' + timeStr + "</span></div>";
                html += '<div class="text-gray-500 truncate">' + deps.esc(preview) + (unread ? ' <span class="text-[#25D366] font-bold">(' + unread + ")</span>" : "") + "</div></div></div>";
            });
            chatListEl.innerHTML = html;
            function activateChatRow(row) {
                var sender = String(row.dataset.waSender || "");
                if (!sender) return;
                var stateNow = deps.getState();
                stateNow.waSelectedJid = sender;
                stateNow.waSelectedChatType = String(row.dataset.waChatType || "private");
                deps.setState(stateNow);
                renderWhatsAppChatList();
                showWhatsAppThread(sender, row.dataset.waName || sender);
            }
            chatListEl.onkeydown = function(e) {
                if (e.target && e.target.closest && e.target.closest("input,textarea,select")) return;
                var row = e.target && e.target.closest ? e.target.closest("[data-wa-sender]") : null;
                if (!row) return;
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    var rows = Array.prototype.slice.call(chatListEl.querySelectorAll("[data-wa-sender]"));
                    var idx = rows.indexOf(row);
                    var next = rows[Math.max(0, Math.min(rows.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)))];
                    if (next) {
                        next.focus();
                        activateChatRow(next);
                    }
                } else if (e.key === "Enter") {
                    e.preventDefault();
                    activateChatRow(row);
                } else if (e.key === "Delete") {
                    e.preventDefault();
                    deps.waRunDeleteChatConfirm(row.dataset.waSender || "", row.dataset.waName || row.dataset.waSender || "this chat");
                }
            };
            chatListEl.querySelectorAll("[data-wa-sender]").forEach(function(row) {
                row.addEventListener("click", function(e) {
                    var sender = String(row.dataset.waSender || "");
                    if (!sender) return;
                    var stateNow = deps.getState();
                    var isSelectMode = !!stateNow.waSidebarChatListMode;
                    if (isSelectMode) {
                        if (e.target && e.target.closest && e.target.closest(".kb-wa-chat-select")) return;
                        if (stateNow.waSelectedChatPhones[sender]) delete stateNow.waSelectedChatPhones[sender];
                        else stateNow.waSelectedChatPhones[sender] = true;
                        deps.setState(stateNow);
                        renderWhatsAppChatList();
                        deps.updateWaSidebarFooterUi();
                        return;
                    }
                    stateNow.waSelectedJid = sender;
                    stateNow.waSelectedChatType = String(row.dataset.waChatType || "private");
                    deps.setState(stateNow);
                    renderWhatsAppChatList();
                    showWhatsAppThread(sender, row.dataset.waName || sender);
                });
                row.addEventListener("focus", function() {
                    if (!deps.getState().waSidebarChatListMode) activateChatRow(row);
                });
            });
            chatListEl.querySelectorAll(".kb-wa-chat-select").forEach(function(el) {
                el.addEventListener("change", function(e) {
                    var sender = String(el.dataset.waPhone || "");
                    if (!sender) return;
                    var stateNow = deps.getState();
                    if (el.checked) stateNow.waSelectedChatPhones[sender] = true;
                    else delete stateNow.waSelectedChatPhones[sender];
                    deps.setState(stateNow);
                    deps.updateWaSidebarFooterUi();
                    if (e && e.stopPropagation) e.stopPropagation();
                });
            });
        }

        function refreshWaThreadIfOpen() {
            var state = deps.getState();
            if (!state.waSelectedJid) return;
            var msgList = document.getElementById("kb-wa-thread-messages");
            if (!msgList) return;
            var msgView = document.getElementById("kb-wa-thread-view");
            if (!msgView || msgView.classList.contains("hidden")) return;
            deps.apiFetch(waThreadMessagesUrl(state.waSelectedJid, 200)).then(function(data) {
                if (!data || !data.messages) return;
                var messages = deps.dedupeThreadMessages(data.messages || []);
                messages.sort(sortWaMessagesAsc);
                if (messages.length === state.waThreadMessages.length) {
                    var sameSet = true;
                    for (var i = 0; i < messages.length; i++) {
                        if (String(messages[i].id) !== String((state.waThreadMessages[i] || {}).id)) { sameSet = false; break; }
                    }
                    if (sameSet) return;
                }
                showWhatsAppThread(state.waSelectedJid, document.getElementById("kb-wa-thread-title").textContent);
            }).catch(function() {});
        }

        function showWhatsAppThread(sender, name) {
            if (!deps.isMessagesPanelVisible()) return;
            var state = deps.getState();
            var prevJid = (state.waSelectedJid || "").trim();
            if (prevJid && prevJid !== sender) flushWaDraftSave(prevJid, true);
            state.waSelectedJid = sender;
            state.waActiveThread = {
                sender: sender,
                name: name || sender,
                chat_type: state.waSelectedChatType || "private",
                target_jid: ""
            };
            deps.setState(state);

            var boardView = document.getElementById("kb-board-view");
            var emptyView = document.getElementById("kb-empty");
            var msgView = document.getElementById("kb-wa-thread-view");
            boardView.classList.add("hidden");
            emptyView.classList.add("hidden");
            msgView.classList.remove("hidden");
            deps.waShowThreadGlobalEmpty(false);

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
            if (state.waSelectedChatType === "group") chatTypeEl.classList.remove("hidden");
            else chatTypeEl.classList.add("hidden");

            var inputEl = document.getElementById("kb-wa-thread-input");
            if (inputEl) inputEl.value = "";
            applyWaDraftToComposer(sender, name);

            msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">Loading messages...</div>';
            msgView.dataset.waSender = String(sender || "");
            msgView.dataset.waChatType = String(state.waSelectedChatType || "private");
            msgView.dataset.waTargetJid = "";
            state.waThreadMessages = [];
            state.waSelectedMessageIds = {};
            deps.setState(state);
            deps.updateWaThreadSelectToggleUi();

            deps.apiFetch(waThreadMessagesUrl(sender, 200)).then(function(data) {
                if (data.messages && data.messages.length > 0) return data;
                return deps.apiFetch("/api/tickets/whatsapp/sync", { method: "POST" }).then(function() {
                    return deps.apiFetch(waThreadMessagesUrl(sender, 200));
                }).catch(function() {
                    return deps.apiFetch("/api/tickets/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                        return { messages: [] };
                    });
                });
            }).catch(function() {
                return deps.apiFetch("/api/tickets/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                    return { messages: [] };
                });
            }).then(function(data) {
                if (!deps.isMessagesPanelVisible() || msgView.classList.contains("hidden")) return;
                var messages = deps.dedupeThreadMessages(data.messages || []);
                var nextState = deps.getState();
                nextState.waThreadMessages = messages.slice();
                if (nextState.waSelectionMode) {
                    nextState.waThreadMessages.forEach(function(msg) {
                        if (deps.waIsMessageSelectable(msg)) nextState.waSelectedMessageIds[String(msg.id)] = true;
                    });
                }
                deps.setState(nextState);
                var totalMessages = Number(data.total);
                var countText = (!isNaN(totalMessages) && totalMessages > messages.length)
                    ? messages.length + " of " + totalMessages + " messages"
                    : messages.length + " messages";
                countEl.textContent = countText;
                var snapCountEl = document.getElementById("kb-wa-thread-snapshot-count");
                if (snapCountEl) snapCountEl.textContent = countText;
                if (!messages.length) {
                    msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">No messages from this number yet</div>';
                    deps.setWaThreadControlsEnabled(false);
                    return;
                }
                deps.setWaThreadControlsEnabled(true);
                for (var mi = messages.length - 1; mi >= 0; mi--) {
                    var mjid = String((messages[mi] && messages[mi].jid) || "");
                    if (mjid && mjid.indexOf("@") !== -1) {
                        msgView.dataset.waTargetJid = mjid;
                        var threadState = deps.getState();
                        if (threadState.waActiveThread) threadState.waActiveThread.target_jid = mjid;
                        deps.setState(threadState);
                        break;
                    }
                }
                messages.sort(sortWaMessagesAsc);
                var html = "";
                var lastDateStr = "";
                messages.forEach(function(msg) {
                    var msgDate = msg.whatsapp_timestamp ? new Date(msg.whatsapp_timestamp * 1000) : null;
                    var dateStr = msgDate ? msgDate.toLocaleDateString([], {year: "numeric", month: "short", day: "numeric"}) : "";
                    if (dateStr && dateStr !== lastDateStr) {
                        html += '<div class="flex items-center justify-center my-3"><span class="text-[10px] text-gray-500 bg-[#152054] px-3 py-1 rounded-full">' + deps.esc(dateStr) + "</span></div>";
                        lastDateStr = dateStr;
                    }
                    var isMine = msg.from_me;
                    var align = isMine ? "justify-end" : "justify-start";
                    var bg = isMine ? "bg-[#005c4b]" : "bg-[#1f2c34]";
                    var timeStr = msgDate ? msgDate.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
                    html += '<div class="flex ' + align + ' items-start gap-2" data-wa-msg-id="' + msg.id + '">';
                    if (deps.getState().waSelectionMode) {
                        if (deps.waIsMessageSelectable(msg)) html += '<label class="pt-1 cursor-pointer" title="Select message"><input type="checkbox" class="accent-[#25D366] w-4 h-4 kb-wa-msg-select" data-wa-msg-id="' + msg.id + '" checked></label>';
                        else html += '<span class="pt-1 w-4 h-4"></span>';
                    }
                    html += '<div class="wa-msg-bubble ' + bg + ' px-3 py-2" style="max-width:75%;border-radius:8px;">';
                    if (deps.getState().waSelectedChatType === "group" && !isMine && msg.sender_push_name) html += '<div class="text-[10px] text-[#25D366] font-medium mb-0.5">' + deps.esc(msg.sender_push_name) + "</div>";
                    if (msg.text) html += '<div class="wa-msg-text text-sm text-white whitespace-pre-wrap">' + deps.esc(msg.text) + "</div>";
                    if (msg.caption && msg.caption !== msg.text) html += '<div class="wa-msg-caption text-sm text-gray-200 whitespace-pre-wrap mt-1">' + deps.esc(msg.caption) + "</div>";
                    html += renderWaMediaHtml(msg);
                    if (msg._analysis_pending) {
                        html += '<div class="wa-msg-analysis-status mt-1 text-[11px] text-gray-300 flex items-center gap-1.5"><svg class="animate-spin h-3.5 w-3.5 text-[#25D366]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z"></path></svg><span>' + deps.esc(msg._analysis_pending) + "...</span></div>";
                    } else if (msg._analysis_error) {
                        html += '<div class="wa-msg-analysis-status mt-1 text-[11px] text-red-300">' + deps.esc(msg._analysis_error) + "</div>";
                    }
                    html += '<div class="wa-msg-meta-row flex items-center justify-end gap-1 mt-0.5"><span class="text-[10px] text-gray-500">' + timeStr + "</span>";
                    if (isMine) html += '<span class="text-[10px] text-blue-400">✓✓</span>';
                    html += '<button type="button" class="kb-wa-msg-more ml-0.5 shrink-0 rounded p-0.5 text-gray-400 hover:bg-white/10 hover:text-white" title="More" aria-label="Message actions" data-wa-msg-id="' + msg.id + '"><svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg></button>';
                    if (msg.snapshot_group) {
                        if (msg.ticket_id) html += '<button type="button" class="text-[10px] text-[#f97316] hover:text-[#fb923c] underline-offset-2 hover:underline" title="Open snapshot ticket #' + msg.ticket_id + '" data-wa-ticket-id="' + msg.ticket_id + '">📷</button>';
                        else html += '<span class="text-[10px] text-[#f97316]" title="Snapshot: ' + deps.esc(msg.snapshot_group) + '">📷</span>';
                    }
                    html += "</div></div></div>";
                });
                var unticketedCount = messages.filter(function(msg) { return !deps.waMsgHasLinkedTicket(msg); }).length;
                if (unticketedCount > 0) html += '<div class="flex items-center gap-3 py-4 mt-2 px-4"><div class="flex-1 h-px bg-white/10"></div><button type="button" id="kb-wa-thread-create-tickets" class="bg-[#f97316]/20 text-[#f97316] border border-[#f97316]/50 px-5 py-2 rounded-lg font-semibold text-sm hover:bg-[#f97316]/30 transition-colors inline-flex items-center gap-2 whitespace-nowrap">' + (deps.getState().waSelectionMode ? "Snapshot Selected Messages" : "Create Ticket from Messages") + "</button><div class=\"flex-1 h-px bg-white/10\"></div></div>";
                else html += '<div class="flex items-center justify-center py-4 mt-2"><span class="text-xs text-gray-500 italic">All messages have been added to tickets</span></div>';
                msgList.innerHTML = html;
                scrollWaThreadToBottom(msgList);
                var createTicketBtn = document.getElementById("kb-wa-thread-create-tickets");
                if (createTicketBtn) createTicketBtn.addEventListener("click", function() { deps.waCtxSnapshotToBoardBottom(); });
                msgList.querySelectorAll(".kb-wa-msg-select").forEach(function(el) {
                    el.addEventListener("change", function() {
                        var msgId = String(el.dataset.waMsgId || "");
                        if (!msgId) return;
                        var checkboxState = deps.getState();
                        if (el.checked) checkboxState.waSelectedMessageIds[msgId] = true;
                        else delete checkboxState.waSelectedMessageIds[msgId];
                        deps.setState(checkboxState);
                        deps.updateWaThreadSelectToggleUi();
                    });
                });
                msgList.querySelectorAll("[data-wa-msg-id]").forEach(function(el) {
                    el.addEventListener("contextmenu", function(e) {
                        e.preventDefault();
                        deps.setWaMsgCtxData({ message_id: parseInt(el.dataset.waMsgId, 10) });
                        deps.showWaMsgContextMenu(e.clientX, e.clientY);
                    });
                });
                msgList.querySelectorAll("[data-wa-ticket-id]").forEach(function(el) {
                    el.addEventListener("click", function() {
                        var ticketId = parseInt(el.dataset.waTicketId || "0", 10);
                        if (ticketId) deps.openTicketModal(ticketId);
                    });
                });
            }).catch(function() {
                countEl.textContent = "Error";
                msgList.innerHTML = '<div class="text-sm text-red-400 text-center py-8">Failed to load messages</div>';
                deps.setWaThreadControlsEnabled(false);
            });
        }

        function waResolveTargetJid(sender, chatTypeOverride) {
            return deps.waResolveTargetJid(sender, chatTypeOverride || deps.getState().waSelectedChatType);
        }

        function setWaSendStatus(statusText, colorClass) {
            var statusEl = document.getElementById("kb-wa-thread-send-status");
            if (!statusEl) return;
            statusEl.classList.remove("hidden", "text-red-400", "text-green-400", "text-gray-500");
            statusEl.classList.add(colorClass || "text-gray-500");
            statusEl.textContent = statusText || "";
        }

        function updateWhatsAppComposerState() {
            var state = deps.getState();
            var sendBtn = document.getElementById("kb-wa-thread-send");
            var voiceBtn = document.getElementById("kb-wa-thread-voice");
            var attachBtn = document.getElementById("kb-wa-thread-attach");
            var inputEl = document.getElementById("kb-wa-thread-input");
            if (sendBtn) sendBtn.disabled = state.waVoiceRecording;
            if (voiceBtn) voiceBtn.disabled = !!inputEl && inputEl.disabled;
            if (attachBtn) attachBtn.disabled = state.waVoiceRecording || (!!inputEl && inputEl.disabled);
        }

        function startVoiceRecordingTimer() {
            var state = deps.getState();
            state.waVoiceStartedAtMs = Date.now();
            deps.setState(state);
            setWaSendStatus("Recording... 00:00", "text-gray-500");
            if (state.waVoiceTimerInterval) clearInterval(state.waVoiceTimerInterval);
            state.waVoiceTimerInterval = setInterval(function() {
                var nowState = deps.getState();
                var elapsedSec = Math.floor((Date.now() - nowState.waVoiceStartedAtMs) / 1000);
                setWaSendStatus("Recording... " + deps.formatVoiceDuration(elapsedSec), "text-gray-500");
            }, 500);
            deps.setState(state);
        }

        function resetWhatsAppVoiceRecordingUi() {
            var state = deps.getState();
            var btn = document.getElementById("kb-wa-thread-voice");
            state.waVoiceRecording = false;
            state.waVoiceRecorder = null;
            state.waVoiceChunks = [];
            state.waVoiceStartedAtMs = 0;
            if (state.waVoiceTimerInterval) {
                clearInterval(state.waVoiceTimerInterval);
                state.waVoiceTimerInterval = null;
            }
            if (state.waVoiceStream) {
                try { state.waVoiceStream.getTracks().forEach(function(t) { t.stop(); }); } catch (e) {}
                state.waVoiceStream = null;
            }
            if (btn) {
                btn.classList.remove("bg-red-600/20", "border-red-500/50", "text-red-400");
                btn.classList.add("border-[#25D366]/50", "text-[#25D366]");
            }
            deps.setState(state);
            updateWhatsAppComposerState();
        }

        function sendWhatsAppThreadMessage() {
            var state = deps.getState();
            var inputEl = document.getElementById("kb-wa-thread-input");
            var statusEl = document.getElementById("kb-wa-thread-send-status");
            var msgView = document.getElementById("kb-wa-thread-view");
            if (!inputEl || !state.waSelectedJid || !msgView) return;
            if (state.waVoiceRecording) return;
            var text = (inputEl.value || "").trim();
            if (!text && !state.waPendingAttachment) return;
            var pinnedSender = (msgView.dataset.waSender || state.waSelectedJid || "").trim();
            var pinnedChatType = (msgView.dataset.waChatType || state.waSelectedChatType || "private").trim();
            var pinnedTargetJid = (msgView.dataset.waTargetJid || "").trim();
            var jid = pinnedTargetJid || waResolveTargetJid(pinnedSender, pinnedChatType);
            if (!jid) return;
            var optimisticText = text;
            var pendingAttachment = state.waPendingAttachment ? {
                name: state.waPendingAttachment.name,
                mime_type: state.waPendingAttachment.mime_type,
                data_b64: state.waPendingAttachment.data_b64,
                kind: state.waPendingAttachment.kind
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

            deps.apiFetch("/api/tickets/whatsapp/send", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }).then(function(resp) {
                if (!resp || !resp.success) throw new Error((resp && resp.error) || "Send failed");
                inputEl.value = "";
                clearWaDraftAfterSend(pinnedSender);
                deps.clearWaPendingAttachment();
                statusEl.classList.remove("text-gray-500", "text-red-400");
                statusEl.classList.add("text-green-400");
                statusEl.textContent = "Sent";
                if (pendingAttachment) deps.appendOptimisticSentMediaMessage(pendingAttachment.name, pendingAttachment.kind, optimisticText);
                else deps.appendOptimisticSentTextMessage(optimisticText);
                deps.syncAndRefreshThreadAfterSend();
            }).catch(function(err) {
                statusEl.classList.remove("text-gray-500", "text-green-400");
                statusEl.classList.add("text-red-400");
                if (pendingAttachment) {
                    statusEl.textContent = "Attachment send failed (relay may not support media yet): " + err.message;
                    deps.clearWaPendingAttachment();
                } else {
                    statusEl.textContent = "Failed to send: " + err.message;
                }
            }).finally(function() {
                inputEl.disabled = false;
                inputEl.focus();
                updateWhatsAppComposerState();
            });
        }

        async function startWhatsAppVoiceRecording() {
            var state = deps.getState();
            if (state.waVoiceRecording) return;
            if (!state.waSelectedJid) {
                setWaSendStatus("Select a chat first", "text-red-400");
                return;
            }
            var btn = document.getElementById("kb-wa-thread-voice");
            try {
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
                    setWaSendStatus("Voice notes not supported in this browser", "text-red-400");
                    return;
                }
                state.waVoiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                var mimeType = MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")
                    ? "audio/ogg;codecs=opus"
                    : (MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4"));
                state.waVoiceChunks = [];
                state.waVoiceRecorder = new MediaRecorder(state.waVoiceStream, { mimeType: mimeType });
                state.waVoiceRecorder.ondataavailable = function(event) {
                    if (event.data && event.data.size > 0) {
                        var nowState = deps.getState();
                        nowState.waVoiceChunks.push(event.data);
                        deps.setState(nowState);
                    }
                };
                state.waVoiceRecorder.start();
                state.waVoiceRecording = true;
                if (btn) {
                    btn.classList.add("bg-red-600/20", "border-red-500/50", "text-red-400");
                    btn.classList.remove("border-[#25D366]/50", "text-[#25D366]");
                }
                deps.setState(state);
                updateWhatsAppComposerState();
                startVoiceRecordingTimer();
            } catch (err) {
                setWaSendStatus("Microphone error: " + (err && err.message ? err.message : String(err)), "text-red-400");
                updateWhatsAppComposerState();
            }
        }

        async function stopWhatsAppVoiceRecordingAndSend() {
            var state = deps.getState();
            if (!state.waVoiceRecording || !state.waVoiceRecorder) return;
            var inputEl = document.getElementById("kb-wa-thread-input");
            var msgView = document.getElementById("kb-wa-thread-view");
            var caption = (inputEl && inputEl.value ? inputEl.value.trim() : "");
            try {
                await new Promise(function(resolve) {
                    state.waVoiceRecorder.onstop = resolve;
                    state.waVoiceRecorder.stop();
                });
                var pinnedSender = (msgView && msgView.dataset.waSender ? msgView.dataset.waSender : state.waSelectedJid || "").trim();
                var pinnedChatType = (msgView && msgView.dataset.waChatType ? msgView.dataset.waChatType : state.waSelectedChatType || "private").trim();
                var pinnedTargetJid = (msgView && msgView.dataset.waTargetJid ? msgView.dataset.waTargetJid : "").trim();
                var jid = pinnedTargetJid || waResolveTargetJid(pinnedSender, pinnedChatType);
                if (!jid) {
                    setWaSendStatus("No chat selected", "text-red-400");
                    return;
                }
                var blob = new Blob(state.waVoiceChunks, { type: state.waVoiceRecorder.mimeType || "audio/webm" });
                if (!blob.size) {
                    setWaSendStatus("No audio captured", "text-red-400");
                    return;
                }
                setWaSendStatus("Sending voice note...", "text-gray-500");
                var b64 = await deps.waBlobToBase64(blob);
                var payload = {
                    jid: jid,
                    caption: caption,
                    audio: {
                        data_b64: b64,
                        mime_type: state.waVoiceRecorder.mimeType || "audio/webm",
                        ptt: true,
                        filename: deps.getWaVoiceFilename(state.waVoiceRecorder.mimeType || "audio/webm")
                    }
                };
                var resp = await deps.apiFetch("/api/tickets/whatsapp/send", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
                if (!resp || !resp.success) throw new Error((resp && resp.error) || "Send failed");
                if (inputEl) inputEl.value = "";
                setWaSendStatus("Voice note sent", "text-green-400");
                deps.syncAndRefreshThreadAfterSend();
            } catch (err) {
                setWaSendStatus("Failed to send voice note: " + (err && err.message ? err.message : String(err)), "text-red-400");
            } finally {
                resetWhatsAppVoiceRecordingUi();
            }
        }

        var waActionsCurrentMsg = null;

        function hideWaMsgActionsMenu() {
            var m = document.getElementById("kb-wa-msg-actions-menu");
            if (m) m.classList.add("hidden");
            waActionsCurrentMsg = null;
        }

        function hideWaMediaLightbox() {
            var overlay = document.getElementById("kb-wa-media-lightbox");
            var inner = document.getElementById("kb-wa-media-lightbox-inner");
            if (inner) inner.innerHTML = "";
            if (overlay) {
                overlay.classList.add("hidden");
                overlay.classList.remove("flex");
            }
        }

        function openWaMediaLightbox(msg) {
            var overlay = document.getElementById("kb-wa-media-lightbox");
            var inner = document.getElementById("kb-wa-media-lightbox-inner");
            if (!overlay || !inner) return;
            inner.innerHTML = "";
            var url = buildWaMediaUrl(msg, "");
            if (isWaImageType(msg)) {
                var img = document.createElement("img");
                img.src = url;
                img.className = "max-h-[90vh] max-w-[95vw] rounded object-contain shadow-lg";
                img.alt = "";
                inner.appendChild(img);
            } else if (isWaVideoType(msg)) {
                var v = document.createElement("video");
                v.controls = true;
                v.className = "max-h-[90vh] max-w-[95vw]";
                v.src = url;
                inner.appendChild(v);
            } else {
                return;
            }
            overlay.classList.remove("hidden");
            overlay.classList.add("flex");
        }

        function showWaMsgActionsMenu(anchorBtn, msg) {
            waActionsCurrentMsg = msg;
            var menu = document.getElementById("kb-wa-msg-actions-menu");
            if (!menu) return;
            var openBtn = menu.querySelector(".kb-wa-act-open");
            var analyzeBtn = menu.querySelector(".kb-wa-act-analyze");
            var copyBtn = menu.querySelector(".kb-wa-act-copy");
            var canOpen = isWaImageType(msg) || isWaVideoType(msg);
            var canAnalyze = isWaVoiceType(msg) || isWaVideoType(msg) || isWaImageType(msg);
            var textJoined = [msg.text, msg.caption].filter(Boolean).join("\n\n").trim();
            var canCopy = !!(textJoined || isWaImageType(msg));
            openBtn.classList.toggle("hidden", !canOpen);
            if (analyzeBtn) {
                analyzeBtn.classList.toggle("hidden", !canAnalyze);
                if (isWaImageType(msg)) analyzeBtn.textContent = "OCR image";
                else if (isWaVoiceType(msg) || isWaVideoType(msg)) analyzeBtn.textContent = "Transcribe";
                else analyzeBtn.textContent = "Analyze";
            }
            copyBtn.classList.toggle("hidden", !canCopy);
            var rect = anchorBtn.getBoundingClientRect();
            menu.style.left = Math.min(rect.left, window.innerWidth - 180) + "px";
            menu.style.top = (rect.bottom + 4) + "px";
            menu.classList.remove("hidden");
            setTimeout(function() {
                var r = menu.getBoundingClientRect();
                if (r.bottom > window.innerHeight - 8) menu.style.top = Math.max(8, rect.top - r.height - 4) + "px";
                if (r.right > window.innerWidth - 8) menu.style.left = Math.max(8, window.innerWidth - r.width - 8) + "px";
            }, 0);
        }

        function waCopyMessageContent(msg) {
            if (isWaImageType(msg)) {
                var url = buildWaMediaUrl(msg, "");
                fetch(url, { credentials: "same-origin" }).then(function(r) {
                    if (!r.ok) throw new Error("Could not load image");
                    return r.blob();
                }).then(function(blob) {
                    var t = blob.type || "image/png";
                    return navigator.clipboard.write([new ClipboardItem({ [t]: blob })]);
                }).then(function() {
                    deps.showSnackbar("Image copied to clipboard", "success");
                }).catch(function(err) {
                    deps.showSnackbar("Copy failed: " + (err && err.message ? err.message : String(err)), "error");
                });
                return;
            }
            var t = [msg.text, msg.caption].filter(Boolean).join("\n\n").trim();
            if (!t) {
                deps.showSnackbar("Nothing to copy", "warning");
                return;
            }
            navigator.clipboard.writeText(t).then(function() {
                deps.showSnackbar("Copied to clipboard", "success");
            }).catch(function(err) {
                deps.showSnackbar("Copy failed: " + (err && err.message ? err.message : String(err)), "error");
            });
        }

        function waSanitizeFilename(name, fallback) {
            var s = String(name || fallback || "download").replace(/[^a-zA-Z0-9._-]+/g, "_");
            return s || "download";
        }

        function waTriggerDownload(url, filename) {
            var a = document.createElement("a");
            a.href = url;
            a.setAttribute("download", filename);
            a.rel = "noopener";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }

        function waDownloadMessageMedia(msg) {
            var safeBase = waSanitizeFilename(msg.media_filename, "whatsapp-" + msg.id);
            if (isWaVoiceType(msg)) {
                var mp3Url = buildWaMediaUrl(msg, "mp3");
                fetch(mp3Url, { credentials: "same-origin" }).then(function(r) {
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.blob();
                }).then(function(blob) {
                    var u = URL.createObjectURL(blob);
                    var name = waSanitizeFilename(safeBase.replace(/\.[^.]+$/, "") + ".mp3", "voice-" + msg.id + ".mp3");
                    waTriggerDownload(u, name);
                    setTimeout(function() { URL.revokeObjectURL(u); }, 2000);
                    deps.showSnackbar("Download started", "success");
                }).catch(function() {
                    deps.showSnackbar("MP3 unavailable — downloading original audio", "warning");
                    waTriggerDownload(buildWaMediaUrl(msg, ""), waSanitizeFilename(safeBase, "voice-" + msg.id));
                });
                return;
            }
            if (msg.media_type) {
                waTriggerDownload(buildWaMediaUrl(msg, ""), safeBase);
                deps.showSnackbar("Download started", "success");
                return;
            }
            var textOnly = [msg.text, msg.caption].filter(Boolean).join("\n\n");
            if (!textOnly.trim()) {
                deps.showSnackbar("Nothing to download", "warning");
                return;
            }
            var blob = new Blob([textOnly], { type: "text/plain;charset=utf-8" });
            var u = URL.createObjectURL(blob);
            waTriggerDownload(u, "message-" + msg.id + ".txt");
            setTimeout(function() { URL.revokeObjectURL(u); }, 2000);
            deps.showSnackbar("Download started", "success");
        }

        function waAnalyzeMessageMedia(msg) {
            function analysisLabelFromResponse(resp, fallbackMsg) {
                var t = String((resp && resp.analysis_type) || "").toLowerCase();
                if (t === "ocr") return "OCR";
                if (t === "transcription") return "Transcription";
                return isWaImageType(fallbackMsg) ? "OCR" : "Transcription";
            }

            function upsertExtractedBlock(existingCaption, label, extractedText) {
                var text = String(extractedText || "").trim();
                if (!text) return String(existingCaption || "");
                var existing = String(existingCaption || "").trim();
                var header = "[" + label + "]";
                var newBlock = header + "\n" + text;
                if (!existing) return newBlock;
                var start = existing.indexOf(header);
                if (start === -1) return existing + "\n\n" + newBlock;
                var before = existing.slice(0, start).trimEnd();
                var rest = existing.slice(start);
                var nextHeader = rest.indexOf("\n[", header.length);
                var after = nextHeader >= 0 ? rest.slice(nextHeader + 1).trimStart() : "";
                if (before && after) return before + "\n\n" + newBlock + "\n\n" + after;
                if (before) return before + "\n\n" + newBlock;
                if (after) return newBlock + "\n\n" + after;
                return newBlock;
            }

            function getThreadMessageById(messageId) {
                var st = deps.getState();
                var arr = (st && st.waThreadMessages) || [];
                for (var i = 0; i < arr.length; i++) {
                    if (String(arr[i].id) === String(messageId)) return arr[i];
                }
                return null;
            }

            function updateThreadMessageById(messageId, updater) {
                var st = deps.getState();
                if (!st || !st.waThreadMessages || !st.waThreadMessages.length) return null;
                var updated = null;
                st.waThreadMessages = st.waThreadMessages.map(function(item) {
                    if (String(item.id) !== String(messageId)) return item;
                    updated = updater(item || {});
                    return updated || item;
                });
                deps.setState(st);
                return updated;
            }

            function ensureAnalysisStatusEl(row, bubble, beforeEl) {
                var statusEl = row.querySelector(".wa-msg-analysis-status");
                if (statusEl) return statusEl;
                statusEl = document.createElement("div");
                statusEl.className = "wa-msg-analysis-status mt-1 text-[11px]";
                if (beforeEl) bubble.insertBefore(statusEl, beforeEl);
                else bubble.appendChild(statusEl);
                return statusEl;
            }

            function patchMessageDom(messageId, msgData) {
                var row = document.querySelector('[data-wa-msg-id="' + messageId + '"]');
                if (!row) return;
                var bubble = row.querySelector(".wa-msg-bubble");
                if (!bubble) return;
                var metaRow = bubble.querySelector(".wa-msg-meta-row");
                var statusEl = row.querySelector(".wa-msg-analysis-status");

                if (msgData._analysis_pending || msgData._analysis_error) {
                    statusEl = ensureAnalysisStatusEl(row, bubble, metaRow || null);
                    if (msgData._analysis_pending) {
                        statusEl.className = "wa-msg-analysis-status mt-1 text-[11px] text-gray-300 flex items-center gap-1.5";
                        statusEl.innerHTML = '<svg class="animate-spin h-3.5 w-3.5 text-[#25D366]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z"></path></svg><span>' + deps.esc(msgData._analysis_pending) + "...</span>";
                    } else {
                        statusEl.className = "wa-msg-analysis-status mt-1 text-[11px] text-red-300";
                        statusEl.textContent = msgData._analysis_error || "Analysis failed";
                    }
                } else if (statusEl) {
                    statusEl.remove();
                }

                if (!msgData._analysis_pending && !msgData._analysis_error && msgData.caption) {
                    var captionEl = bubble.querySelector(".wa-msg-caption");
                    if (!captionEl) {
                        captionEl = document.createElement("div");
                        captionEl.className = "wa-msg-caption text-sm text-gray-200 whitespace-pre-wrap mt-1";
                        if (metaRow) bubble.insertBefore(captionEl, metaRow);
                        else bubble.appendChild(captionEl);
                    }
                    captionEl.textContent = msgData.caption;
                }
            }

            if (!msg || !msg.id) {
                deps.showSnackbar("Message unavailable", "warning");
                return;
            }

            var kind = isWaImageType(msg) ? "OCR" : "Transcription";
            var pendingMsg = updateThreadMessageById(msg.id, function(item) {
                var next = Object.assign({}, item);
                next._analysis_pending = kind;
                next._analysis_error = "";
                return next;
            }) || getThreadMessageById(msg.id) || msg;
            patchMessageDom(msg.id, pendingMsg);

            deps.apiFetch("/api/tickets/whatsapp/messages/" + encodeURIComponent(msg.id) + "/analyze-media", {
                method: "POST",
            }).then(function(resp) {
                if (!resp || resp.success !== true) {
                    throw new Error((resp && (resp.error || resp.detail)) || "Analysis failed");
                }
                var successLabel = analysisLabelFromResponse(resp, msg);
                var merged = updateThreadMessageById(msg.id, function(item) {
                    var next = Object.assign({}, item);
                    next._analysis_pending = "";
                    next._analysis_error = "";
                    next.caption = upsertExtractedBlock(next.caption, successLabel, resp.text);
                    return next;
                }) || getThreadMessageById(msg.id) || msg;
                patchMessageDom(msg.id, merged);
                deps.showSnackbar(successLabel + " added to message", "success");
                setTimeout(function() { deps.refreshWaThreadIfOpen(); }, 800);
            }).catch(function(err) {
                var failed = updateThreadMessageById(msg.id, function(item) {
                    var next = Object.assign({}, item);
                    next._analysis_pending = "";
                    next._analysis_error = (err && err.message ? err.message : String(err));
                    return next;
                }) || getThreadMessageById(msg.id) || msg;
                patchMessageDom(msg.id, failed);
                deps.showSnackbar("Failed: " + (err && err.message ? err.message : String(err)), "error");
            });
        }

        function bindWaMessageActionsUi() {
            if (window._waMsgActionsUiBound) return;
            window._waMsgActionsUiBound = true;
            var msgList = document.getElementById("kb-wa-thread-messages");
            if (msgList) {
                msgList.addEventListener("click", function(ev) {
                    var btn = ev.target.closest(".kb-wa-msg-more");
                    if (!btn || !msgList.contains(btn)) return;
                    ev.preventDefault();
                    ev.stopPropagation();
                    var id = parseInt(btn.getAttribute("data-wa-msg-id"), 10);
                    var state = deps.getState();
                    var arr = state.waThreadMessages || [];
                    var msg = arr.find(function(m) { return String(m.id) === String(id); });
                    if (!msg) return;
                    showWaMsgActionsMenu(btn, msg);
                });
            }
            var menu = document.getElementById("kb-wa-msg-actions-menu");
            if (menu) {
                var openEl = menu.querySelector(".kb-wa-act-open");
                var analyzeEl = menu.querySelector(".kb-wa-act-analyze");
                var copyEl = menu.querySelector(".kb-wa-act-copy");
                var dlEl = menu.querySelector(".kb-wa-act-download");
                if (openEl) openEl.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var msg = waActionsCurrentMsg;
                    hideWaMsgActionsMenu();
                    if (msg) openWaMediaLightbox(msg);
                });
                if (analyzeEl) analyzeEl.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var msg = waActionsCurrentMsg;
                    hideWaMsgActionsMenu();
                    if (msg) waAnalyzeMessageMedia(msg);
                });
                if (copyEl) copyEl.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var msg = waActionsCurrentMsg;
                    hideWaMsgActionsMenu();
                    if (msg) waCopyMessageContent(msg);
                });
                if (dlEl) dlEl.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var msg = waActionsCurrentMsg;
                    hideWaMsgActionsMenu();
                    if (msg) waDownloadMessageMedia(msg);
                });
            }
            var lb = document.getElementById("kb-wa-media-lightbox");
            var lbClose = document.getElementById("kb-wa-media-lightbox-close");
            if (lbClose) lbClose.addEventListener("click", function(e) {
                e.stopPropagation();
                hideWaMediaLightbox();
            });
            if (lb) {
                lb.addEventListener("click", function(e) {
                    if (e.target === lb) hideWaMediaLightbox();
                });
            }
            document.addEventListener("keydown", function(e) {
                if (e.key === "Escape") hideWaMediaLightbox();
            });
        }

        return {
            loadWhatsAppChats: loadWhatsAppChats,
            processWhatsAppMessages: processWhatsAppMessages,
            renderWhatsAppChatList: renderWhatsAppChatList,
            refreshWaThreadIfOpen: refreshWaThreadIfOpen,
            showWhatsAppThread: showWhatsAppThread,
            waResolveTargetJid: waResolveTargetJid,
            sendWhatsAppThreadMessage: sendWhatsAppThreadMessage,
            setWaSendStatus: setWaSendStatus,
            updateWhatsAppComposerState: updateWhatsAppComposerState,
            resetWhatsAppVoiceRecordingUi: resetWhatsAppVoiceRecordingUi,
            startWhatsAppVoiceRecording: startWhatsAppVoiceRecording,
            stopWhatsAppVoiceRecordingAndSend: stopWhatsAppVoiceRecordingAndSend,
            bindWaMessageActionsUi: bindWaMessageActionsUi,
            bindWaDraftComposer: bindWaDraftComposer,
            loadWaDraftMap: loadWaDraftMap,
            hideWaMsgActionsMenu: hideWaMsgActionsMenu,
        };
    }

    window.KanbanWhatsAppHelpers = {
        waMsgHasLinkedTicket: waMsgHasLinkedTicket,
        waIsMessageSelectable: waIsMessageSelectable,
        dedupeThreadMessages: dedupeThreadMessages,
        waDownloadJsonFile: waDownloadJsonFile,
        waResolveTargetJid: waResolveTargetJid,
        waBlobToBase64: waBlobToBase64,
        formatVoiceDuration: formatVoiceDuration,
        getWaVoiceFilename: getWaVoiceFilename,
    };
    window.KanbanWhatsAppManagement = { create: create };
    window.KanbanWhatsAppRuntime = { create: createRuntime };
})();
