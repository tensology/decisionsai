/* Oracle chat image attachments (attach button, drag/drop, paste).
 * Kept separate from chat.js (Laya-owned). chat.js only calls
 * window.DecisionsChatImages.pendingPath()/clear()/decorate(). */
(function () {
    'use strict';
    const ALLOWED = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    const MAX_BYTES = 8 * 1024 * 1024;
    const REJECT = 'Only JPEG, PNG, GIF or WebP images up to 8 MB can be attached.';
    let pending = null; // { path, url, previewUrl, name }
    let lastSentPreview = null;

    function toast(msg) {
        if (typeof window.showChatSnackbar === 'function') window.showChatSnackbar(msg, 'error');
        else console.warn(msg);
    }

    function renderChip() {
        const tray = document.getElementById('chatImageTray');
        if (!tray) return;
        tray.innerHTML = '';
        tray.hidden = !pending;
        if (!pending) return;
        const chip = document.createElement('div');
        chip.className = 'chat-image-chip';
        const img = document.createElement('img');
        img.src = pending.previewUrl || pending.url;
        img.alt = pending.name || 'Attached image';
        const name = document.createElement('span');
        name.className = 'chat-image-chip-name';
        name.textContent = pending.name || 'image';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'chat-image-chip-remove';
        remove.title = 'Remove image';
        remove.setAttribute('aria-label', 'Remove image');
        remove.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>';
        remove.addEventListener('click', () => { pending = null; renderChip(); });
        chip.append(img, name, remove);
        tray.appendChild(chip);
    }

    async function upload(file) {
        if (!file) return;
        if (!ALLOWED.includes(String(file.type || '').toLowerCase()) || file.size > MAX_BYTES) {
            toast(REJECT);
            return;
        }
        const form = new FormData();
        form.append('file', file, file.name || 'image');
        try {
            const res = await fetch('/api/chat-images', { method: 'POST', body: form });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || REJECT);
            pending = { path: data.path, url: data.url, previewUrl: URL.createObjectURL(file), name: file.name || data.name };
            renderChip();
        } catch (err) {
            toast(err.message || REJECT);
        }
    }

    function firstImage(list) {
        return Array.from(list || []).find((f) => String(f.type || '').startsWith('image/')) || (list && list[0]) || null;
    }

    function bind() {
        const container = document.getElementById('inputContainer');
        const button = document.getElementById('chatAttachButton');
        const input = document.getElementById('chatAttachInput');
        if (!container || !button || !input) return;
        button.addEventListener('click', () => input.click());
        input.addEventListener('change', async () => {
            await upload(firstImage(input.files));
            input.value = '';
        });
        container.addEventListener('dragover', (e) => {
            if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
            e.preventDefault();
            container.classList.add('chat-image-dragover');
        });
        container.addEventListener('dragleave', () => container.classList.remove('chat-image-dragover'));
        container.addEventListener('drop', async (e) => {
            if (!e.dataTransfer?.files?.length) return;
            e.preventDefault();
            container.classList.remove('chat-image-dragover');
            await upload(firstImage(e.dataTransfer.files));
        });
        container.addEventListener('paste', async (e) => {
            const file = Array.from(e.clipboardData?.files || []).find((f) => String(f.type || '').startsWith('image/'));
            if (!file) return;
            e.preventDefault();
            await upload(file);
        });
    }

    window.DecisionsChatImages = {
        pendingPath() { return pending ? pending.path : null; },
        clear() {
            if (pending) lastSentPreview = { url: pending.url, at: Date.now() };
            pending = null;
            renderChip();
        },
        /* Add a thumbnail to a user bubble: stored image_url, or the image just sent (optimistic bubble). */
        decorate(div, message) {
            if (!div || !message || message.role !== 'user') return;
            let url = message.image_url || null;
            if (!url && pending && message.chat_row_id == null) url = pending.previewUrl || pending.url;
            if (!url && lastSentPreview && message.chat_row_id == null && Date.now() - lastSentPreview.at < 15000) url = lastSentPreview.url;
            if (!url) return;
            const host = div.querySelector('.message-text');
            if (!host || host.querySelector('.chat-message-image')) return;
            const link = document.createElement('a');
            link.href = message.image_url || url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.className = 'chat-message-image';
            const img = document.createElement('img');
            img.src = url;
            img.alt = 'Attached image';
            link.appendChild(img);
            host.prepend(link);
        },
        _upload: upload,
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();
})();
