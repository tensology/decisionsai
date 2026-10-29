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
        const style = `--assistant-image-scale:${Math.max(0.1, Number(snapshot.imageScale) || 1)};--assistant-image-offset-x:${((Number(snapshot.imageOffsetX) || 0) / 128) * 100}%;--assistant-image-offset-y:${((Number(snapshot.imageOffsetY) || 0) / 128) * 100}%`;
        if (snapshot.animated) {
            return `<video class="assistant-avatar-image ${kind}" src="${escapeAttribute(snapshot.src)}" style="${style}" autoplay muted loop playsinline aria-hidden="true" data-skin-name="${escapeAttribute(snapshot.name || 'Oracle')}"></video>`;
        }
        return `<img class="assistant-avatar-image ${kind}" src="${escapeAttribute(snapshot.src)}" style="${style}" alt="" aria-hidden="true" data-skin-name="${escapeAttribute(snapshot.name || 'Oracle')}">`;
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
        root.querySelectorAll?.('video.assistant-avatar-image').forEach((video) => {
            video.play().catch(() => {});
        });
    }

    async function load({ force = false, skinName = null } = {}) {
        if (loadPromise && !force) return loadPromise;
        const sequence = ++loadSequence;
        loadPromise = (async () => {
            const response = await fetch('/api/skins');
            if (!response.ok) throw new Error(`Skin request failed with ${response.status}`);
            const data = await response.json();
            const skins = Array.isArray(data.skins) ? data.skins : [];
            const selected = skinName || data.selected_skin || 'oracle';
            const skin = skins.find((item) => item.folder_name === selected)
                || skins.find((item) => item.folder_name === 'oracle')
                || skins[0];
            if (!skin) return null;
            // A chroma-keyed animation still carries its green screen on this
            // surface, and an avatar cannot remove it. The static preview image
            // is already a transparent cut-out, so prefer it for those skins.
            const hasChromaKey = Array.isArray(skin.chroma_key) && skin.chroma_key.length === 3;
            const sourceFile = (hasChromaKey && skin.preview_image)
                ? skin.preview_image
                : (skin.idle_animation || 'idle.webm');
            const src = `/api/skins/${encodeURIComponent(skin.folder_name)}/preview/${encodeURIComponent(sourceFile)}`;
            const animated = sourceFile.toLowerCase().endsWith('.webm');
            if (sequence !== loadSequence || !src) return null;
            snapshot = {
                src, animated, type: skin.type, name: skin.name || skin.folder_name,
                imageScale: skin.image_scale, imageOffsetX: skin.image_offset_x, imageOffsetY: skin.image_offset_y,
            };
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
