(function () {
    "use strict";

    var tokenKey = "decisionsai.irc.token";
    var nameKey = "decisionsai.irc.displayName";
    var clientKey = "decisionsai.irc.clientId";
    function getClientId() {
        var existing = localStorage.getItem(clientKey);
        if (existing) return existing;
        var generated = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : "client-" + Date.now() + "-" + Math.random().toString(36).slice(2);
        localStorage.setItem(clientKey, generated);
        return generated;
    }
    var state = {
        token: localStorage.getItem(tokenKey) || "",
        clientId: getClientId(),
        displayName: localStorage.getItem(nameKey) || "",
        user: null,
        rooms: [],
        joinedRooms: [],
        activeRoom: "decisions-ai",
        messages: {},
        members: {},
        unread: {},
        ws: null,
        wsUrl: "",
        reconnectTimer: null,
        reconnectAttempts: 0,
        commandIndex: 0,
        pendingDelete: null,
    };

    var el = {};
    var commands = [
        { name: "/join", args: "room", hint: "Create or join a room" },
        { name: "/part", args: "", hint: "Leave the current room" },
        { name: "/rooms", args: "", hint: "List available rooms" },
        { name: "/users", args: "", hint: "List members in this room" },
        { name: "/nick", args: "name", hint: "Change your display name" },
        { name: "/me", args: "action", hint: "Send an action message" },
        { name: "/delete", args: "message-id", hint: "Delete one of your messages" },
        { name: "/help", args: "", hint: "Show command help" },
        { name: "/warn", args: "nick reason", hint: "Warn a user", admin: true },
        { name: "/mute", args: "nick 10m", hint: "Mute a user", admin: true },
        { name: "/kick", args: "nick", hint: "Remove a user from a room", admin: true },
        { name: "/ban", args: "nick", hint: "Ban a user", admin: true },
        { name: "/promote", args: "nick room_moderator", hint: "Grant a role", superAdmin: true },
        { name: "/demote", args: "nick", hint: "Revoke room role", superAdmin: true },
    ];

    function $(id) { return document.getElementById(id); }

    function api(url, opts) {
        opts = opts || {};
        opts.headers = opts.headers || {};
        if (state.token) opts.headers.Authorization = "Bearer " + state.token;
        return window.DecisionsAPI.fetch(url, opts);
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function slugName(room) {
        return room && room.slug ? room.slug : "decisions-ai";
    }

    function setStatus(text, connected) {
        el.statusText.textContent = text;
        el.statusDot.classList.toggle("connected", !!connected);
    }

    function formatTime(iso) {
        if (!iso) return "";
        try {
            return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch (_) {
            return "";
        }
    }

    function currentMessages() {
        return state.messages[state.activeRoom] || [];
    }

    function renderRooms() {
        var rooms = state.rooms.slice();
        if (!rooms.some(function (room) { return room.slug === "decisions-ai"; })) {
            rooms.unshift({ slug: "decisions-ai", name: "Decisions AI", member_count: 0, is_default: true });
        }
        var joined = new Set(state.joinedRooms.concat(["decisions-ai"]));
        el.roomList.innerHTML = rooms.map(function (room) {
            var slug = slugName(room);
            var label = room.name || slug;
            var count = state.unread[slug] ? '<span class="irc-room-unread">' + escapeHtml(state.unread[slug]) + '</span>' : "";
            var joinedClass = joined.has(slug) ? " joined" : "";
            return (
                '<button type="button" class="irc-room-item' + joinedClass + (slug === state.activeRoom ? " active" : "") + '" data-room="' + escapeHtml(slug) + '">' +
                '<span>#' + escapeHtml(label) + '</span>' +
                '<span class="irc-room-meta">' + count + '<span class="irc-room-count">' + escapeHtml(room.member_count || "") + '</span></span>' +
                '</button>'
            );
        }).join("");
    }

    function renderMembers() {
        var members = state.members[state.activeRoom] || [];
        el.memberCount.textContent = String(members.length);
        if (!members.length) {
            el.memberList.innerHTML = '<div class="irc-empty-state">No members visible yet</div>';
            return;
        }
        var isAdmin = state.user && ["admin", "super_admin"].includes(state.user.role);
        var isSuperAdmin = state.user && state.user.role === "super_admin";
        var selfMember = members.find(function (member) { return state.user && member.id === state.user.id; });
        var canModerate = isAdmin || (selfMember && selfMember.room_role === "room_moderator");
        el.memberList.innerHTML = members.map(function (member) {
            var role = member.role === "super_admin" ? "admin" : (member.role || "");
            var roomRole = member.room_role === "room_moderator" ? "mod" : "";
            var isMe = state.user && member.id === state.user.id;
            return (
                '<div class="irc-member" data-member-name="' + escapeHtml(member.display_name) + '" data-member-id="' + escapeHtml(member.id) + '">' +
                '<span class="irc-member-dot' + (member.online ? " online" : "") + '"></span>' +
                '<span class="irc-member-name" title="' + escapeHtml(member.display_name) + '">' + escapeHtml(member.display_name) + '</span>' +
                '<span class="irc-member-role">' + escapeHtml([role === "user" ? "" : role, roomRole].filter(Boolean).join(" / ")) + '</span>' +
                '<div class="irc-member-actions">' +
                (canModerate && !isMe ? '<button type="button" data-member-action="warn" title="Warn">Warn</button><button type="button" data-member-action="mute" title="Mute 10 minutes">Mute</button><button type="button" data-member-action="kick" title="Remove from room">Remove</button>' : "") +
                (isAdmin && !isMe ? '<button type="button" data-member-action="ban" title="Ban">Ban</button>' : "") +
                (isSuperAdmin && !isMe ? '<button type="button" data-member-action="make_mod" title="Make room moderator">Make Mod</button><button type="button" data-member-action="remove_mod" title="Remove room moderator">Unmod</button><button type="button" data-member-action="make_admin" title="Make admin">Admin</button><button type="button" data-member-action="make_user" title="Make normal user">User</button>' : "") +
                '</div>' +
                '</div>'
            );
        }).join("");
    }

    function renderMessages() {
        var msgs = currentMessages();
        var activeRoomData = state.rooms.find(function (room) { return room.slug === state.activeRoom; });
        el.roomName.textContent = "#" + state.activeRoom;
        el.roomTopic.textContent = activeRoomData && activeRoomData.topic ? activeRoomData.topic : (state.activeRoom === "decisions-ai" ? "Default shared room" : "Shared room");
        if (!msgs.length) {
            el.messageStream.innerHTML = '<div class="irc-empty-state">No messages yet</div>';
            return;
        }
        el.messageStream.innerHTML = msgs.map(function (msg) {
            var mine = state.user && msg.sender_id === state.user.id;
            var kind = msg.kind === "system" || msg.kind === "error" ? msg.kind : "";
            var canDelete = mine && msg.id && !String(msg.id).startsWith("local-") && kind === "";
            return (
                '<article class="irc-message ' + (mine ? "mine " : "") + escapeHtml(kind) + '" data-message-id="' + escapeHtml(msg.id || "") + '">' +
                '<div class="irc-message-meta">' +
                '<span class="irc-message-sender">' + escapeHtml(msg.sender_name || "System") + '</span>' +
                '<span>' + escapeHtml(formatTime(msg.created_at)) + '</span>' +
                (canDelete ? '<button type="button" class="irc-message-delete" data-message-delete="' + escapeHtml(msg.id) + '" title="Delete message">Delete</button>' : "") +
                '</div>' +
                '<div class="irc-message-body">' + escapeHtml(msg.content || "") + '</div>' +
                '</article>'
            );
        }).join("");
        el.messageStream.scrollTop = el.messageStream.scrollHeight;
    }

    function renderUser() {
        if (!state.user) return;
        el.currentUser.textContent = state.user.display_name + (state.user.role && state.user.role !== "user" ? " · " + state.user.role : "");
        renderCommandList();
    }

    function renderAll() {
        renderUser();
        renderRooms();
        renderMembers();
        renderMessages();
        renderCommandPalette();
    }

    function addMessage(room, msg) {
        if (!room || !msg) return;
        state.messages[room] = (state.messages[room] || []).concat([msg]).slice(-300);
        if (room === state.activeRoom) {
            state.unread[room] = 0;
            renderMessages();
        } else {
            state.unread[room] = Math.min((state.unread[room] || 0) + 1, 99);
            renderRooms();
        }
    }

    function removeMessage(room, messageId) {
        if (!room || !messageId) return;
        state.messages[room] = (state.messages[room] || []).filter(function (msg) { return msg.id !== messageId; });
        if (room === state.activeRoom) renderMessages();
    }

    function addSystem(text, type) {
        addMessage(state.activeRoom, {
            id: "local-" + Date.now() + "-" + Math.random(),
            kind: type || "system",
            sender_name: type === "error" ? "Error" : "System",
            content: text,
            created_at: new Date().toISOString(),
        });
    }

    function handleFrame(msg) {
        if (msg.type === "chat_ready") {
            state.user = msg.user;
            state.rooms = msg.rooms || [];
            state.joinedRooms = state.rooms.map(function (room) { return room.slug; });
            state.activeRoom = msg.active_room || "decisions-ai";
            renderAll();
            loadRooms();
            loadMessages(state.activeRoom);
            return;
        }
        if (msg.type === "message") {
            addMessage(msg.room, msg.message);
            return;
        }
        if (msg.type === "message_deleted") {
            removeMessage(msg.room, msg.message_id);
            return;
        }
        if (msg.type === "system") {
            addMessage(msg.room || state.activeRoom, {
                id: "system-" + Date.now() + "-" + Math.random(),
                kind: "system",
                sender_name: "System",
                content: msg.text || msg.message || "",
                created_at: msg.created_at || new Date().toISOString(),
            });
            return;
        }
        if (msg.type === "error") {
            addSystem(msg.message || "Chat error", "error");
            return;
        }
        if (msg.type === "presence") {
            state.members[msg.room] = msg.members || [];
            if (msg.room === state.activeRoom) renderMembers();
            return;
        }
        if (msg.type === "room_joined") {
            state.rooms = [msg.room].concat(state.rooms.filter(function (room) { return room.slug !== msg.room.slug; }));
            state.joinedRooms = [msg.room.slug].concat(state.joinedRooms.filter(function (slug) { return slug !== msg.room.slug; }));
            state.activeRoom = msg.room.slug;
            state.unread[msg.room.slug] = 0;
            state.messages[msg.room.slug] = msg.messages || [];
            closeRoomModal();
            renderAll();
            loadRooms();
            return;
        }
        if (msg.type === "room_left") {
            state.joinedRooms = state.joinedRooms.filter(function (slug) { return slug !== msg.room; });
            if (state.activeRoom === msg.room) state.activeRoom = "decisions-ai";
            renderAll();
            if (msg.reason) addSystem("Left #" + msg.room + ": " + msg.reason);
            loadRooms();
            return;
        }
        if (msg.type === "rooms") {
            state.rooms = msg.rooms || [];
            renderRooms();
            return;
        }
        if (msg.type === "user_updated") {
            state.user = msg.user;
            state.displayName = msg.user.display_name;
            localStorage.setItem(nameKey, state.displayName);
            renderUser();
            renderCommandPalette();
            return;
        }
        if (msg.type === "moderation") {
            addSystem([msg.action || "moderation", msg.target_name || "", msg.reason || ""].filter(Boolean).join(": "));
            return;
        }
    }

    function connectWs() {
        if (!state.token || !state.wsUrl) return;
        if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;
        var url = state.wsUrl + "?token=" + encodeURIComponent(state.token);
        setStatus("Connecting", false);
        state.ws = new WebSocket(url);
        state.ws.onopen = function () {
            state.reconnectAttempts = 0;
            setStatus("Connected", true);
        };
        state.ws.onmessage = function (event) {
            try { handleFrame(JSON.parse(event.data)); } catch (_) {}
        };
        state.ws.onclose = function () {
            setStatus("Disconnected", false);
            clearTimeout(state.reconnectTimer);
            state.reconnectAttempts += 1;
            state.reconnectTimer = setTimeout(connectWs, Math.min(1000 * Math.pow(1.4, state.reconnectAttempts), 8000));
        };
        state.ws.onerror = function () {
            setStatus("Connection error", false);
        };
    }

    function sendFrame(payload) {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            addSystem("Chat is reconnecting. Try again in a moment.", "error");
            return false;
        }
        state.ws.send(JSON.stringify(payload));
        return true;
    }

    function sendInput() {
        var text = el.input.value.trim();
        if (!text) return;
        closeCommandPalette();
        if (text === "/help" || text === "/commands") {
            showHelp();
            el.input.value = "";
            return;
        }
        if (text.charAt(0) === "/") {
            sendFrame({ type: "command", raw: text, room: state.activeRoom });
        } else {
            sendFrame({ type: "message", room: state.activeRoom, text: text });
        }
        el.input.value = "";
        resizeInput();
    }

    function availableCommands() {
        var isAdmin = state.user && ["admin", "super_admin"].includes(state.user.role);
        var isSuperAdmin = state.user && state.user.role === "super_admin";
        return commands.filter(function (command) {
            if (command.superAdmin) return isSuperAdmin;
            return !command.admin || isAdmin;
        });
    }

    function commandMatches() {
        var value = el.input.value;
        if (value.charAt(0) !== "/") return [];
        if (/\s/.test(value)) return [];
        var query = value.split(/\s+/)[0].toLowerCase();
        return availableCommands().filter(function (command) {
            return command.name.indexOf(query) === 0;
        });
    }

    function closeCommandPalette() {
        state.commandIndex = 0;
        el.commandPalette.hidden = true;
        el.commandPalette.innerHTML = "";
    }

    function completeCommand(command) {
        var suffix = command.args ? " " : "";
        el.input.value = command.name + suffix;
        closeCommandPalette();
        el.input.focus();
    }

    function renderCommandPalette() {
        if (!el.commandPalette || !el.input || document.activeElement !== el.input) return;
        var matches = commandMatches();
        if (!matches.length) {
            closeCommandPalette();
            return;
        }
        state.commandIndex = Math.max(0, Math.min(state.commandIndex, matches.length - 1));
        el.commandPalette.hidden = false;
        el.commandPalette.innerHTML = matches.map(function (command, index) {
            return (
                '<button type="button" class="irc-command-option' + (index === state.commandIndex ? " active" : "") + '" data-command="' + escapeHtml(command.name) + '">' +
                '<span><strong>' + escapeHtml(command.name) + '</strong>' + (command.args ? ' <em>' + escapeHtml(command.args) + '</em>' : "") + '</span>' +
                '<small>' + escapeHtml(command.hint) + '</small>' +
                '</button>'
            );
        }).join("");
    }

    function renderCommandList() {
        if (!el.commandList) return;
        el.commandList.innerHTML = availableCommands().map(function (command) {
            return '<code title="' + escapeHtml(command.hint) + '">' + escapeHtml(command.name + (command.args ? " " + command.args : "")) + '</code>';
        }).join("");
    }

    function loadMessages(room) {
        if (!state.token) {
            renderMessages();
            return;
        }
        api("/api/irc/rooms/" + encodeURIComponent(room) + "/messages?limit=160")
            .then(function (data) {
                state.messages[room] = data.messages || [];
                if (room === state.activeRoom) renderMessages();
            })
            .catch(function () {});
    }

    function loadRooms() {
        if (!state.token) return;
        api("/api/irc/rooms")
            .then(function (data) {
                state.rooms = data.rooms || state.rooms;
                state.joinedRooms = data.joined || state.joinedRooms;
                renderRooms();
            })
            .catch(function () {});
    }

    function startSession(name, adminCode, updateDisplayName) {
        state.displayName = (name || state.displayName || "Guest").trim() || "Guest";
        el.modalError.textContent = "";
        api("/api/irc/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                display_name: state.displayName,
                admin_code: adminCode || "",
                client_id: state.clientId,
                update_display_name: !!updateDisplayName,
            }),
        }).then(function (data) {
            state.token = data.token;
            state.user = data.user;
            state.rooms = data.rooms || [];
            state.joinedRooms = state.rooms.map(function (room) { return room.slug; });
            state.activeRoom = data.default_room ? data.default_room.slug : "decisions-ai";
            state.wsUrl = data.ws_url;
            localStorage.setItem(tokenKey, state.token);
            localStorage.setItem(nameKey, state.user.display_name);
            el.adminCodeInput.value = "";
            closeNameModal();
            renderAll();
            loadRooms();
            clearTimeout(state.reconnectTimer);
            if (state.ws) {
                state.ws.onclose = null;
                state.ws.close();
                state.ws = null;
            }
            connectWs();
            loadMessages(state.activeRoom);
        }).catch(function (err) {
            el.modalError.textContent = err.message || String(err);
            openNameModal();
        });
    }

    function openNameModal() {
        el.nameInput.value = state.displayName || "";
        el.adminCodeInput.value = "";
        el.nameModal.hidden = false;
        setTimeout(function () { el.nameInput.focus(); el.nameInput.select(); }, 20);
    }

    function closeNameModal() {
        el.nameModal.hidden = true;
    }

    function openRoomModal() {
        el.newRoomInput.value = "";
        el.roomModal.hidden = false;
        setTimeout(function () { el.newRoomInput.focus(); }, 20);
    }

    function closeRoomModal() {
        el.roomModal.hidden = true;
    }

    function openDeleteModal(messageId) {
        var msg = currentMessages().find(function (item) { return item.id === messageId; });
        if (!msg) return;
        state.pendingDelete = { id: messageId, room: state.activeRoom };
        el.deletePreview.textContent = msg.content || "";
        el.deleteModal.hidden = false;
        setTimeout(function () { el.deleteConfirm.focus(); }, 20);
    }

    function closeDeleteModal() {
        state.pendingDelete = null;
        el.deletePreview.textContent = "";
        el.deleteModal.hidden = true;
    }

    function confirmDeleteMessage() {
        if (!state.pendingDelete) return;
        sendFrame({ type: "delete_message", room: state.pendingDelete.room, message_id: state.pendingDelete.id });
        closeDeleteModal();
    }

    function joinRoomFromModal() {
        var name = el.newRoomInput.value.trim();
        if (!name) return;
        sendFrame({ type: "command", raw: "/join " + name, room: state.activeRoom });
    }

    function showHelp() {
        var help = availableCommands().map(function (command) {
            return command.name + (command.args ? " " + command.args : "") + " - " + command.hint;
        }).join("\n");
        addSystem(help);
    }

    function resizeInput() {
        if (!el.input) return;
        el.input.style.height = "auto";
        el.input.style.height = Math.min(el.input.scrollHeight, 144) + "px";
    }

    function fillCommand(raw) {
        el.input.value = raw;
        resizeInput();
        el.input.focus();
    }

    function bindEvents() {
        el.roomList.addEventListener("click", function (event) {
            var button = event.target.closest("[data-room]");
            if (!button) return;
            state.activeRoom = button.dataset.room;
            state.unread[state.activeRoom] = 0;
            renderAll();
            if (!state.joinedRooms.includes(state.activeRoom)) {
                sendFrame({ type: "command", raw: "/join " + state.activeRoom, room: state.activeRoom });
            } else {
                loadMessages(state.activeRoom);
            }
        });
        el.messageStream.addEventListener("click", function (event) {
            var button = event.target.closest("[data-message-delete]");
            if (!button) return;
            openDeleteModal(button.dataset.messageDelete);
        });
        el.memberList.addEventListener("click", function (event) {
            var actionButton = event.target.closest("[data-member-action]");
            var row = event.target.closest("[data-member-name]");
            if (!row) return;
            if (!actionButton) {
                return;
            }
            var action = actionButton.dataset.memberAction;
            if (["warn", "mute", "kick", "ban"].includes(action)) {
                sendFrame({
                    type: "moderate_user",
                    action: action,
                    target_user_id: row.dataset.memberId,
                    room: state.activeRoom,
                    reason: action === "kick" ? "Removed stale member" : "",
                });
                return;
            }
            var roleMap = {
                make_mod: "room_moderator",
                remove_mod: "member",
                make_admin: "admin",
                make_user: "user",
            };
            if (roleMap[action]) {
                sendFrame({
                    type: "change_role",
                    role: roleMap[action],
                    target_user_id: row.dataset.memberId,
                    room: state.activeRoom,
                    reason: "Changed from member panel",
                });
                return;
            }
        });
        el.send.addEventListener("click", sendInput);
        el.input.addEventListener("keydown", function (event) {
            var matches = commandMatches();
            if (matches.length && ["ArrowDown", "ArrowUp", "Tab"].includes(event.key)) {
                event.preventDefault();
                if (event.key === "ArrowDown") state.commandIndex = (state.commandIndex + 1) % matches.length;
                if (event.key === "ArrowUp") state.commandIndex = (state.commandIndex - 1 + matches.length) % matches.length;
                if (event.key === "Tab") {
                    completeCommand(matches[state.commandIndex]);
                    return;
                }
                renderCommandPalette();
                return;
            }
            if (event.key === "Escape" && !el.commandPalette.hidden) {
                event.preventDefault();
                closeCommandPalette();
                return;
            }
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendInput();
            }
        });
        el.input.addEventListener("input", function () {
            state.commandIndex = 0;
            resizeInput();
            renderCommandPalette();
        });
        el.input.addEventListener("focus", renderCommandPalette);
        el.input.addEventListener("blur", function () {
            setTimeout(closeCommandPalette, 120);
        });
        el.commandPalette.addEventListener("mousedown", function (event) {
            var button = event.target.closest("[data-command]");
            if (!button) return;
            var command = commands.find(function (item) { return item.name === button.dataset.command; });
            if (command) completeCommand(command);
        });
        el.newRoom.addEventListener("click", openRoomModal);
        el.roomCancel.addEventListener("click", closeRoomModal);
        el.roomJoin.addEventListener("click", joinRoomFromModal);
        el.newRoomInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") joinRoomFromModal();
            if (event.key === "Escape") closeRoomModal();
        });
        el.deleteCancel.addEventListener("click", closeDeleteModal);
        el.deleteConfirm.addEventListener("click", confirmDeleteMessage);
        el.deleteModal.addEventListener("click", function (event) {
            if (event.target === el.deleteModal) closeDeleteModal();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !el.deleteModal.hidden) closeDeleteModal();
        });
        el.joinButton.addEventListener("click", function () { startSession(el.nameInput.value, el.adminCodeInput.value, true); });
        el.nameInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") startSession(el.nameInput.value, el.adminCodeInput.value, true);
        });
        el.adminCodeInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") startSession(el.nameInput.value, el.adminCodeInput.value, true);
        });
        el.adminCodeButton.addEventListener("click", openNameModal);
        el.helpButton.addEventListener("click", showHelp);
    }

    function init() {
        el.currentUser = $("irc-current-user");
        el.statusText = $("irc-status-text");
        el.statusDot = $("irc-status-dot");
        el.roomList = $("irc-room-list");
        el.roomName = $("irc-room-name");
        el.roomTopic = $("irc-room-topic");
        el.messageStream = $("irc-message-stream");
        el.memberList = $("irc-member-list");
        el.memberCount = $("irc-member-count");
        el.input = $("irc-input");
        el.commandPalette = $("irc-command-palette");
        el.send = $("irc-send");
        el.nameModal = $("irc-name-modal");
        el.nameInput = $("irc-display-name");
        el.adminCodeInput = $("irc-admin-code");
        el.adminCodeButton = $("irc-admin-code-button");
        el.joinButton = $("irc-join-button");
        el.modalError = $("irc-modal-error");
        el.newRoom = $("irc-new-room");
        el.roomModal = $("irc-room-modal");
        el.newRoomInput = $("irc-new-room-name");
        el.roomCancel = $("irc-room-cancel");
        el.roomJoin = $("irc-room-join");
        el.deleteModal = $("irc-delete-modal");
        el.deletePreview = $("irc-delete-preview");
        el.deleteCancel = $("irc-delete-cancel");
        el.deleteConfirm = $("irc-delete-confirm");
        el.helpButton = $("irc-command-help");
        el.commandList = $("irc-command-list");
        bindEvents();
        renderAll();
        if (state.displayName) {
            startSession(state.displayName, "", false);
        } else {
            openNameModal();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
