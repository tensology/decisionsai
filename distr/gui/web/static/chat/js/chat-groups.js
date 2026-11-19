/* Group chats: several agents and Paul in one thread. Replies stream per agent
 * (several at once); read-aloud plays one agent at a time in its own voice. */
(function () {
    const API = '/api';
    const state = { groups: [], openId: null, streaming: false, skins: null, speak: localStorage.getItem('decisions.groupSpeak') === '1', editId: null, rendered: 0 };
    const $ = id => document.getElementById(id);
    const els = {};

    function esc(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    async function skins() {
        if (state.skins) return state.skins;
        try {
            const res = await fetch('/api/skins');
            state.skins = res.ok ? ((await res.json()).skins || []) : [];
        } catch (_) { state.skins = []; }
        return state.skins;
    }

    function avatar(name, skinName, cls = 'group-avatar') {
        const span = document.createElement('span');
        span.className = cls;
        span.setAttribute('aria-hidden', 'true');
        span.textContent = (name || 'A').slice(0, 1);
        const skin = (state.skins || []).find(s => s.folder_name === skinName);
        const file = skin && (skin.preview_image || skin.idle_animation);
        if (file && !file.endsWith('.webm')) {
            const img = document.createElement('img');
            img.src = `/api/skins/${encodeURIComponent(skin.folder_name)}/preview/${encodeURIComponent(file)}`;
            img.alt = '';
            img.addEventListener('error', () => img.remove());
            span.append(img);
        }
        return span;
    }

    // ---- sidebar list --------------------------------------------------
    async function refresh() {
        try {
            const res = await fetch(`${API}/groups`);
            if (!res.ok) return;
            state.groups = (await res.json()).groups || [];
        } catch (_) { return; }
        await skins();
        renderList();
        if (state.openId && !state.streaming) refreshOpenQuietly();
    }

    function renderList() {
        els.list.replaceChildren();
        for (const group of state.groups) {
            const row = document.createElement('div');
            row.className = 'chat-item group-item' + (group.id === state.openId ? ' active' : '');
            row.setAttribute('role', 'option');
            row.setAttribute('tabindex', '0');
            row.dataset.groupId = String(group.id);
            const stack = document.createElement('span');
            stack.className = 'group-avatar-stack';
            group.members.slice(0, 3).forEach(m => stack.append(avatar(m.name, m.skin_name)));
            const wrap = document.createElement('span');
            wrap.className = 'chat-item-title-wrap';
            wrap.innerHTML = `<span class="chat-item-title">${esc(group.title)}</span><span class="chat-item-preview">${esc(group.latest_message || 'No messages yet')}</span>`;
            row.append(stack, wrap);
            row.addEventListener('click', () => open(group.id));
            row.addEventListener('keydown', e => { if (e.key === 'Enter') open(group.id); });
            row.addEventListener('contextmenu', e => { e.preventDefault(); removeGroup(group); });
            els.list.append(row);
        }
    }

    async function removeGroup(group) {
        const ok = await window.DecisionsAPI.confirm({
            title: 'Remove group', message: `Remove “${group.title}” from the sidebar? The agents are not affected.`,
            confirmLabel: 'Remove', danger: true,
        });
        if (!ok) return;
        await fetch(`${API}/groups/${group.id}`, { method: 'DELETE' });
        if (state.openId === group.id) close();
        refresh();
    }

    // ---- open / close ---------------------------------------------------
    async function open(groupId) {
        state.openId = groupId;
        document.body.classList.add('group-mode');
        els.view.hidden = false;
        document.querySelectorAll('#chatList .chat-item.active').forEach(el => el.classList.remove('active'));
        renderList();
        await load();
        els.input.focus();
    }

    function close() {
        state.openId = null;
        document.body.classList.remove('group-mode');
        els.view.hidden = true;
        renderList();
    }

    async function load() {
        const res = await fetch(`${API}/groups/${state.openId}`);
        if (!res.ok) return;
        const data = await res.json();
        await skins();
        els.title.textContent = data.title;
        els.members.replaceChildren(...data.members.map(m => {
            const chip = document.createElement('span');
            chip.className = 'group-member' + (m.is_orchestrator ? ' is-orchestrator' : '');
            chip.title = m.is_orchestrator ? `${m.name} (orchestrator)` : m.name;
            chip.append(avatar(m.name, m.skin_name, 'group-avatar group-avatar--sm'), document.createTextNode(m.name));
            return chip;
        }));
        els.messages.replaceChildren();
        data.messages.forEach(addMessage);
        state.rendered = data.messages.length;
        scroll();
    }

    async function refreshOpenQuietly() {
        try {
            const res = await fetch(`${API}/groups/${state.openId}`);
            if (!res.ok) return;
            const data = await res.json();
            if (data.messages.length !== state.rendered && !state.streaming) {
                els.messages.replaceChildren();
                data.messages.forEach(addMessage);
                state.rendered = data.messages.length;
                scroll();
            }
        } catch (_) { /* next poll */ }
    }

    // ---- messages -------------------------------------------------------
    function format(text) {
        return typeof window.formatMessage === 'function' ? window.formatMessage(text) : esc(text).replace(/\n/g, '<br>');
    }

    function addMessage(msg) {
        const row = document.createElement('div');
        const isUser = msg.role === 'user';
        row.className = `message ${isUser ? 'user' : 'assistant'} group-message` + (msg.error ? ' group-message--error' : '');
        if (!isUser) {
            row.dataset.agentId = String(msg.agent_id || '');
            row.append(avatar(msg.agent_name, msg.skin_name, 'group-avatar group-avatar--msg'));
        }
        const content = document.createElement('div');
        content.className = 'message-content';
        content.innerHTML = (isUser ? '' : `<span class="group-message-name">${esc(msg.agent_name)}</span>`) +
            `<div class="message-text">${format(msg.content || '')}</div>`;
        row.append(content);
        els.messages.append(row);
        return row;
    }

    function notice(text) {
        const p = document.createElement('p');
        p.className = 'group-notice';
        p.textContent = text;
        els.messages.append(p);
        scroll();
    }

    function scroll() { els.messages.scrollTop = els.messages.scrollHeight; }

    async function send() {
        const text = els.input.value.trim();
        if (!text || state.streaming || !state.openId) return;
        els.input.value = '';
        autosize();
        state.streaming = true;
        els.send.disabled = true;
        const live = new Map();   // agent_id -> {row, text}
        try {
            const res = await fetch(`${API}/groups/${state.openId}/messages`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text })
            });
            if (!res.ok || !res.body) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'The group could not answer.');
            }
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            for (;;) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let nl;
                while ((nl = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, nl).trim();
                    buffer = buffer.slice(nl + 1);
                    if (line) handle(JSON.parse(line), live);
                }
            }
        } catch (error) {
            notice(error.message || 'The group could not answer.');
        } finally {
            state.streaming = false;
            els.send.disabled = !els.input.value.trim();
            refresh();
        }
    }

    function handle(event, live) {
        if (event.type === 'message') {
            addMessage(event);
            state.rendered += 1;
        } else if (event.type === 'start') {
            const row = addMessage({ role: 'agent', agent_id: event.agent_id, agent_name: event.agent_name, skin_name: event.skin_name, content: '' });
            row.classList.add('group-message--typing');
            live.set(event.agent_id, { row, text: '' });
        } else if (event.type === 'token') {
            const item = live.get(event.agent_id);
            if (!item) return;
            item.text += event.text;
            item.row.classList.remove('group-message--typing');
            item.row.querySelector('.message-text').textContent = item.text;
        } else if (event.type === 'done') {
            const item = live.get(event.agent_id);
            if (item) {
                item.row.classList.remove('group-message--typing');
                item.row.classList.toggle('group-message--error', Boolean(event.error));
                item.row.querySelector('.message-text').innerHTML = format(event.content);
                live.delete(event.agent_id);
            }
            state.rendered += 1;
            if (state.speak && !event.error) speak(event);
        } else if (event.type === 'capped') {
            notice(`Paused after ${event.limit} agent replies. Send another message to continue.`);
        } else if (event.type === 'error') {
            notice(event.message);
        }
        scroll();
    }

    // ---- read aloud: one agent at a time, each in its own voice ---------
    const speechQueue = [];
    let speaking = false;
    function speak(reply) {
        speechQueue.push(reply);
        if (!speaking) nextSpeech();
    }
    async function nextSpeech() {
        const reply = speechQueue.shift();
        if (!reply) { speaking = false; return; }
        speaking = true;
        try {
            const res = await fetch(`${API}/chats/tts/generate`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: reply.content, provider: reply.voice_provider || null, voice: reply.voice_model || null })
            });
            if (!res.ok) throw new Error('tts');
            const url = URL.createObjectURL(await res.blob());
            const audio = new Audio(url);
            audio.addEventListener('ended', () => { URL.revokeObjectURL(url); nextSpeech(); });
            audio.addEventListener('error', () => { URL.revokeObjectURL(url); nextSpeech(); });
            await audio.play();
        } catch (_) { nextSpeech(); }
    }

    // ---- create / edit members -----------------------------------------
    async function openPicker(editGroup) {
        state.editId = editGroup ? editGroup.id : null;
        $('newGroupTitle').textContent = editGroup ? 'Group agents' : 'New group';
        $('newGroupSave').textContent = editGroup ? 'Save' : 'Create group';
        $('newGroupName').closest('.form-group').hidden = Boolean(editGroup);
        $('newGroupName').value = '';
        $('newGroupError').hidden = true;
        const res = await fetch(`${API}/agents`);
        const agents = res.ok ? ((await res.json()).agents || []) : [];
        await skins();
        const chosen = new Set((editGroup ? editGroup.members : []).map(m => m.id));
        const box = $('newGroupAgents');
        box.replaceChildren(...agents.map(agent => {
            const label = document.createElement('label');
            label.className = 'group-agent-pick';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.value = String(agent.id);
            input.checked = chosen.has(agent.id);
            label.append(input, avatar(agent.name, agent.skin_name, 'group-avatar group-avatar--sm'), document.createTextNode(agent.name));
            return label;
        }));
        $('newGroupModal').style.display = 'flex';
    }

    function closePicker() { $('newGroupModal').style.display = 'none'; }

    async function savePicker() {
        const ids = Array.from($('newGroupAgents').querySelectorAll('input:checked')).map(i => Number(i.value));
        const error = $('newGroupError');
        if (!ids.length) { error.textContent = 'Choose at least one agent.'; error.hidden = false; return; }
        const editing = state.editId;
        const res = await fetch(editing ? `${API}/groups/${editing}/members` : `${API}/groups`, {
            method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(editing ? { agent_ids: ids } : { agent_ids: ids, title: $('newGroupName').value.trim() })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { error.textContent = data.detail || 'Could not save the group.'; error.hidden = false; return; }
        closePicker();
        await refresh();
        open(data.group.id);
    }

    function autosize() {
        els.input.style.height = 'auto';
        els.input.style.height = Math.min(els.input.scrollHeight, 200) + 'px';
    }

    function init() {
        Object.assign(els, {
            list: $('groupList'), view: $('groupView'), title: $('groupViewTitle'), members: $('groupViewMembers'),
            messages: $('groupMessages'), input: $('groupInput'), send: $('groupSendButton'), speaker: $('groupSpeakerToggle'),
        });
        if (!els.list || !els.view) return;
        $('newGroupBtn').addEventListener('click', () => openPicker(null));
        $('groupAddMemberBtn').addEventListener('click', () => {
            const group = state.groups.find(g => g.id === state.openId);
            if (group) openPicker(group);
        });
        $('newGroupClose').addEventListener('click', closePicker);
        $('newGroupCancel').addEventListener('click', closePicker);
        $('newGroupSave').addEventListener('click', savePicker);
        els.input.addEventListener('input', () => { autosize(); els.send.disabled = state.streaming || !els.input.value.trim(); });
        els.input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
        els.send.addEventListener('click', send);
        els.speaker.setAttribute('aria-pressed', state.speak ? 'true' : 'false');
        els.speaker.addEventListener('click', () => {
            state.speak = !state.speak;
            localStorage.setItem('decisions.groupSpeak', state.speak ? '1' : '0');
            els.speaker.setAttribute('aria-pressed', state.speak ? 'true' : 'false');
        });
        // Picking an agent (or creating one) leaves the group view.
        const leave = e => { if (state.openId && e.target.closest('.chat-item, #primaryAgentCard, #newChatBtn')) close(); };
        $('chatList').addEventListener('click', leave, true);
        $('newChatBtn').addEventListener('click', leave, true);
        refresh();
        setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 5000);
    }

    window.DecisionsGroups = { refresh, open, close };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
