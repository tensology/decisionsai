(function () {
    'use strict';

    let snapshot = null;
    let loadPromise = null;
    let loadSequence = 0;

    function escapeAttribute(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function fallbackHtml() {
        return '<span class="assistant-avatar-fallback" aria-hidden="true"></span>';
    }

    function html() {
        if (!snapshot?.src) return fallbackHtml();
        const kind = snapshot.type === 'oracle' ? 'oracle' : 'avatar';
        return `<img class="assistant-avatar-image ${kind}" src="${escapeAttribute(snapshot.src)}" alt="" aria-hidden="true" data-skin-name="${escapeAttribute(snapshot.name || 'Oracle')}">`;
    }

    function name() {
        return snapshot?.name || 'Assistant';
    }

    function apply(root = document) {
        root.querySelectorAll?.('[data-assistant-identity]').forEach((target) => {
            target.innerHTML = html();
        });
        root.querySelectorAll?.('[data-assistant-name]').forEach((target) => {
            target.textContent = name();
        });
    }

    function drawVideoFrame(video, skin) {
        const canvas = document.createElement('canvas');
        canvas.width = 128;
        canvas.height = 128;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        if (!context || !video.videoWidth || !video.videoHeight) return null;

        const isOracle = skin.type === 'oracle';
        const baseScale = isOracle
            ? Math.max(canvas.width / video.videoWidth, canvas.height / video.videoHeight)
            : Math.min(canvas.width / video.videoWidth, canvas.height / video.videoHeight);
        const imageScale = Math.max(0.1, Number(skin.image_scale) || 1);
        const drawWidth = video.videoWidth * baseScale * imageScale;
        const drawHeight = video.videoHeight * baseScale * imageScale;
        const drawX = (canvas.width - drawWidth) / 2 + (Number(skin.image_offset_x) || 0);
        const drawY = (canvas.height - drawHeight) / 2 + (Number(skin.image_offset_y) || 0);
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(video, drawX, drawY, drawWidth, drawHeight);

        if (Array.isArray(skin.chroma_key) && skin.chroma_key.length === 3) {
            const frame = context.getImageData(0, 0, canvas.width, canvas.height);
            const pixels = frame.data;
            const threshold = Math.max(0, Number(skin.chroma_threshold) || 30);
            const softLimit = threshold + Math.max(1, threshold * 0.6);
            const [keyR, keyG, keyB] = skin.chroma_key.map((value) => Number(value) || 0);
            for (let index = 0; index < pixels.length; index += 4) {
                const red = pixels[index] - keyR;
                const green = pixels[index + 1] - keyG;
                const blue = pixels[index + 2] - keyB;
                const distance = Math.sqrt((red * red) + (green * green) + (blue * blue));
                if (distance <= threshold) pixels[index + 3] = 0;
                else if (distance < softLimit) pixels[index + 3] = Math.round(255 * ((distance - threshold) / (softLimit - threshold)));
            }
            context.putImageData(frame, 0, 0);
        }
        return canvas.toDataURL('image/png');
    }

    function captureVideo(previewUrl, skin) {
        return new Promise((resolve) => {
            const video = document.createElement('video');
            let settled = false;
            const finish = (value) => {
                if (settled) return;
                settled = true;
                clearTimeout(timeout);
                video.pause();
                video.removeAttribute('src');
                video.load();
                video.remove();
                resolve(value || null);
            };
            const capture = () => {
                try { finish(drawVideoFrame(video, skin)); }
                catch (_error) { finish(null); }
            };
            const timeout = setTimeout(() => finish(null), 4000);
            video.muted = true;
            video.preload = 'auto';
            video.playsInline = true;
            video.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:-10px;top:-10px';
            video.addEventListener('error', () => finish(null), { once: true });
            video.addEventListener('loadeddata', () => {
                const seekTarget = Number.isFinite(video.duration) ? Math.min(0.15, video.duration / 2) : 0;
                if (seekTarget > 0.01) {
                    video.addEventListener('seeked', capture, { once: true });
                    video.currentTime = seekTarget;
                } else capture();
            }, { once: true });
            video.src = previewUrl;
            document.body.appendChild(video);
            video.load();
        });
    }

    async function load({ force = false } = {}) {
        if (loadPromise && !force) return loadPromise;
        const sequence = ++loadSequence;
        loadPromise = (async () => {
            const response = await fetch('/api/skins');
            if (!response.ok) throw new Error(`Skin request failed with ${response.status}`);
            const data = await response.json();
            const skins = Array.isArray(data.skins) ? data.skins : [];
            const selected = data.selected_skin || 'oracle';
            const skin = skins.find((item) => item.folder_name === selected)
                || skins.find((item) => item.folder_name === 'oracle')
                || skins[0];
            if (!skin) return null;
            const idleFile = skin.idle_animation || 'idle.webm';
            const previewUrl = `/api/skins/${encodeURIComponent(skin.folder_name)}/preview/${encodeURIComponent(idleFile)}`;
            const src = idleFile.toLowerCase().endsWith('.webm') ? await captureVideo(previewUrl, skin) : previewUrl;
            if (sequence !== loadSequence || !src) return null;
            snapshot = { src, type: skin.type, name: skin.name || skin.folder_name };
            apply(document);
            document.dispatchEvent(new CustomEvent('decisions:assistant-identity-changed', { detail: { name: snapshot.name, type: snapshot.type } }));
            return snapshot;
        })().catch((error) => {
            console.warn('Could not load the selected Oracle/avatar identity:', error);
            return null;
        });
        return loadPromise;
    }

    window.DecisionsAssistantIdentity = { apply, html, load, name };
    load();
})();
