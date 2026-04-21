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
        function confirmWaLink() {
            var boardId = parseInt(document.getElementById("kb-wa-link-board").value);
            if (!boardId) { deps.showSnackbar("Select a board"); return; }
            var autoSnapshot = document.getElementById("kb-wa-link-auto").checked;
            var waCtxMenuData = deps.getWaCtxMenuData();
            deps.apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
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
                    deps.showSnackbar("Linked " + waCtxMenuData.name + " to board");
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
            deps.apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                deps.openTicketFromWhatsApp(phone, title, desc, linkData.board_id || null, [{ id: msgId }]);
            }).catch(function() {
                deps.openTicketFromWhatsApp(phone, title, desc, null, [{ id: msgId }]);
            });
        }

        function waMsgCtxMarkProcessed() {
            deps.hideWaMsgContextMenu();
            var waMsgCtxData = deps.getWaMsgCtxData();
            if (!waMsgCtxData) return;
            deps.apiFetch("/api/kanban/whatsapp/messages/" + waMsgCtxData.message_id + "/processed", {
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
            deps.apiFetch("/api/kanban/whatsapp/messages/" + waMsgCtxData.message_id, {
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

        function loadBoardWaLinks(boardId) {
            var linksEl = document.getElementById("kb-bm-wa-links");
            linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">Loading...</div>';
            deps.apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links").then(function(links) {
                if (!links.length) {
                    linksEl.innerHTML = '<div class="text-xs text-gray-500 italic">No WhatsApp numbers linked</div>';
                    return;
                }
                var html = "";
                links.forEach(function(l) {
                    html += '<div class="flex items-center justify-between px-3 py-2 bg-[#152054] rounded border border-white/10">';
                    html += '<div class="flex items-center gap-2">';
                    html += '<div class="w-6 h-6 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-[10px] font-bold">' + deps.esc((l.contact_name || l.phone_number || "?").charAt(0).toUpperCase()) + '</div>';
                    html += '<div>';
                    html += '<div class="text-sm text-white">' + deps.esc(l.contact_name || l.phone_number) + '</div>';
                    html += '<div class="text-[10px] text-gray-500">' + deps.esc(l.phone_number) + '</div>';
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
                linksEl.querySelectorAll(".kb-wa-link-auto-toggle").forEach(function(el) {
                    el.addEventListener("change", function() {
                        var lid = el.dataset.linkId;
                        var bid = el.dataset.boardId;
                        deps.apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, {
                            method: "PATCH",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({ auto_snapshot: el.checked })
                        });
                    });
                });
                linksEl.querySelectorAll(".kb-wa-link-remove").forEach(function(el) {
                    el.addEventListener("click", function() {
                        var lid = el.dataset.linkId;
                        var bid = el.dataset.boardId;
                        deps.apiFetch("/api/kanban/boards/" + bid + "/whatsapp-links/" + lid, { method: "DELETE" }).then(function() {
                            loadBoardWaLinks(bid);
                        });
                    });
                });
            });
        }

        function addWaLinkFromBoardModal() {
            var phone = document.getElementById("kb-bm-wa-add-phone").value.trim();
            var name = document.getElementById("kb-bm-wa-add-name").value.trim();
            if (!phone) { deps.showSnackbar("Enter a phone number"); return; }
            var boardId = deps.getEditingBoardId();
            if (!boardId) { deps.showSnackbar("Save the board first"); return; }
            var jid = phone + "@s.whatsapp.net";
            deps.apiFetch("/api/kanban/boards/" + boardId + "/whatsapp-links", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ phone_jid: jid, phone_number: phone, contact_name: name })
            }).then(function(r) {
                if (r.success) {
                    deps.showSnackbar("Linked " + (name || phone) + " to board");
                    document.getElementById("kb-bm-wa-add-phone").value = "";
                    document.getElementById("kb-bm-wa-add-name").value = "";
                    loadBoardWaLinks(boardId);
                }
            });
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
            deps.apiFetch("/api/kanban/whatsapp/relay/clear-messages", { method: "POST" }).then(function(result) {
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
                    deps.apiFetch("/api/kanban/whatsapp/chat/" + encodeURIComponent(phone), { method: "DELETE" }).then(function(r) {
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
            deps.apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                var boardId = linkData.board_id || null;
                deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
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
                deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
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
            deps.apiFetch("/api/kanban/boards").then(function(boards) {
                var dbBs = boards.filter(function(b) { return b.source === "database"; });
                if (!dbBs.length) { deps.showSnackbar("No database boards to create ticket in"); return; }
                var currentBoard = deps.getCurrentBoard();
                var targetBoard = (currentBoard && currentBoard.source === "database") ? currentBoard.id : dbBs[0].id;
                return deps.apiFetch("/api/kanban/whatsapp/linked-board?phone=" + encodeURIComponent(phone)).then(function(linkData) {
                    if (linkData && linkData.board_id) targetBoard = linkData.board_id;
                    return deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500");
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
                    return deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(phone) + "&limit=500").then(function(data) {
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
            addWaLinkFromBoardModal: addWaLinkFromBoardModal,
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
        };
    }

    function createRuntime(deps) {
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
            var base = "/api/kanban/whatsapp/relay-media/" + encodeURIComponent(msg.id);
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
            var localUrl = "/api/kanban/whatsapp/messages?limit=500";

            Promise.all([
                deps.apiFetch(localUrl).then(function(data) {
                    // Keep local DB as the source of truth for list rendering.
                    // Do not auto-hydrate from relay when local is empty; users can
                    // explicitly trigger sync via the Sync action.
                    return data || { messages: [] };
                }).catch(function() {
                    return fetchFromRelay().catch(function() { return { messages: [] }; });
                }),
                deps.apiFetch("/api/kanban/whatsapp/chats?limit=500&offset=0&search=").then(function(chatsData) {
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
                }).catch(function() { return { groupNames: {}, chatNames: {} }; })
            ]).then(function(results) {
                state.waGroupNames = (results[1] && results[1].groupNames) || {};
                state.waChatNames = (results[1] && results[1].chatNames) || {};
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
                return deps.apiFetch("/api/kanban/whatsapp/relay/messages?limit=500").then(function(data) {
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
                if (msg.whatsapp_timestamp && msg.whatsapp_timestamp > chatMap[chatPhone].lastTs) chatMap[chatPhone].lastTs = msg.whatsapp_timestamp;
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
                    target.messages.sort(function(a, b) { return (a.whatsapp_timestamp || 0) - (b.whatsapp_timestamp || 0); });
                    target.lastTs = Math.max(target.lastTs || 0, aliasChat.lastTs || 0);
                    delete chatMap[aliasKey];
                });
            }
            state.waChats = Object.values(chatMap);
            state.waChats.sort(function(a, b) { return b.lastTs - a.lastTs; });

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
                if (!state.waChats.length) deps.showWhatsAppNoMessagesState();
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
                html += '<div class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs hover:bg-white/5' + active + '" data-wa-sender="' + deps.esc(sender) + '" data-wa-name="' + deps.esc(name) + '" data-wa-chat-type="' + (chat.chat_type || "private") + '">';
                if (state.waSidebarChatListMode) html += '<input type="checkbox" class="accent-[#25D366] w-3.5 h-3.5 shrink-0 kb-wa-chat-select" data-wa-phone="' + deps.esc(sender) + '"' + rowSel + " />";
                html += '<div class="w-8 h-8 rounded-full bg-[#25D366]/20 flex items-center justify-center text-[#25D366] text-xs font-bold flex-shrink-0">' + deps.esc(name.charAt(0).toUpperCase()) + "</div>";
                html += '<div class="flex-1 min-w-0"><div class="flex items-center justify-between"><span class="text-white truncate font-medium">' + name + "</span>" + groupBadge + '<span class="text-gray-500 text-[10px] ml-1 flex-shrink-0">' + timeStr + "</span></div>";
                html += '<div class="text-gray-500 truncate">' + deps.esc(preview) + (unread ? ' <span class="text-[#25D366] font-bold">(' + unread + ")</span>" : "") + "</div></div></div>";
            });
            chatListEl.innerHTML = html;
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
            deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(state.waSelectedJid) + "&limit=200").then(function(data) {
                if (!data || !data.messages) return;
                var messages = deps.dedupeThreadMessages(data.messages || []);
                messages.sort(function(a, b) { return (a.whatsapp_timestamp || 0) - (b.whatsapp_timestamp || 0); });
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
            var state = deps.getState();
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

            msgList.innerHTML = '<div class="text-sm text-gray-500 text-center py-8">Loading messages...</div>';
            msgView.dataset.waSender = String(sender || "");
            msgView.dataset.waChatType = String(state.waSelectedChatType || "private");
            msgView.dataset.waTargetJid = "";
            state.waThreadMessages = [];
            state.waSelectedMessageIds = {};
            deps.setState(state);
            deps.updateWaThreadSelectToggleUi();

            deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").then(function(data) {
                if (data.messages && data.messages.length > 0) return data;
                return deps.apiFetch("/api/kanban/whatsapp/sync", { method: "POST" }).then(function() {
                    return deps.apiFetch("/api/kanban/whatsapp/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200");
                }).catch(function() {
                    return deps.apiFetch("/api/kanban/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                        return { messages: [] };
                    });
                });
            }).catch(function() {
                return deps.apiFetch("/api/kanban/whatsapp/relay/messages?jid_phone=" + encodeURIComponent(sender) + "&limit=200").catch(function() {
                    return { messages: [] };
                });
            }).then(function(data) {
                var messages = deps.dedupeThreadMessages(data.messages || []);
                var nextState = deps.getState();
                nextState.waThreadMessages = messages.slice();
                if (nextState.waSelectionMode) {
                    nextState.waThreadMessages.forEach(function(msg) {
                        if (deps.waIsMessageSelectable(msg)) nextState.waSelectedMessageIds[String(msg.id)] = true;
                    });
                }
                deps.setState(nextState);
                countEl.textContent = messages.length + " messages";
                var snapCountEl = document.getElementById("kb-wa-thread-snapshot-count");
                if (snapCountEl) snapCountEl.textContent = messages.length + " messages";
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
                messages.sort(function(a, b) { return (a.whatsapp_timestamp || 0) - (b.whatsapp_timestamp || 0); });
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
                    if (msg.caption && msg.caption !== msg.text) html += '<div class="wa-msg-text text-sm text-gray-200 whitespace-pre-wrap mt-1">' + deps.esc(msg.caption) + "</div>";
                    html += renderWaMediaHtml(msg);
                    html += '<div class="flex items-center justify-end gap-1 mt-0.5"><span class="text-[10px] text-gray-500">' + timeStr + "</span>";
                    if (isMine) html += '<span class="text-[10px] text-blue-400">✓✓</span>';
                    if (msg.processed) html += '<span class="text-[10px] text-green-400" title="Processed">✓</span>';
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
                msgList.scrollTop = msgList.scrollHeight;
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

            deps.apiFetch("/api/kanban/whatsapp/send", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }).then(function(resp) {
                if (!resp || !resp.success) throw new Error((resp && resp.error) || "Send failed");
                inputEl.value = "";
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
                var resp = await deps.apiFetch("/api/kanban/whatsapp/send", {
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
