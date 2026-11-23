(function () {
    'use strict';

    let snapshot = null;
    let loadPromise = null;
    let loadSequence = 0;
    const applied = new WeakMap();

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

    function avatarStyle(identity) {
        return `--assistant-image-scale:${Math.max(0.1, Number(identity.imageScale) || 1)};--assistant-image-offset-x:${((Number(identity.imageOffsetX) || 0) / 128) * 100}%;--assistant-image-offset-y:${((Number(identity.imageOffsetY) || 0) / 128) * 100}%`;
    }

    function html(identity = snapshot) {
        if (!identity?.src) return fallbackHtml();
        const kind = identity.type === 'oracle' ? 'oracle' : 'avatar';
        const style = avatarStyle(identity);
        if (identity.animated) {
            return `<video class="assistant-avatar-image ${kind}" src="${escapeAttribute(identity.src)}" style="${style}" autoplay muted loop playsinline aria-hidden="true" data-skin-name="${escapeAttribute(identity.name || 'Oracle')}"></video>`;
        }
        return `<img class="assistant-avatar-image ${kind}" src="${escapeAttribute(identity.src)}" style="${style}" alt="" aria-hidden="true" data-skin-name="${escapeAttribute(identity.name || 'Oracle')}">`;
    }

    function name() {
        return snapshot?.name || 'Assistant';
    }

    function apply(root = document) {
        const markup = html();
        root.querySelectorAll?.('[data-assistant-identity]').forEach((target) => {
            if (target.hasAttribute('data-assistant-idle')) {
                applyIdle(target);
                return;
            }
            if (applied.get(target) === markup) return;
            target.innerHTML = markup;
            applied.set(target, markup);
            target.querySelector('video')?.play().catch(() => {});
        });
        root.querySelectorAll?.('[data-assistant-name]').forEach((target) => {
            target.textContent = name();
        });
    }

    function applyIdle(target) {
        const idle = snapshot && {
            ...snapshot,
            src: snapshot.idleSrc,
            animated: snapshot.idleAnimated,
        };
        const key = JSON.stringify(idle);
        if (applied.get(target) === key) return;
        applied.set(target, key);
        const videoPreview = window.DecisionsSkinVideo;
        videoPreview?.dispose(target.firstElementChild);

        if (idle?.animated && idle.chromaKey && videoPreview) {
            const preview = videoPreview.create(idle.src, idle.chromaKey, idle.chromaThreshold, avatarStyle(idle), 512);
            preview.className = `assistant-avatar-image ${idle.type === 'oracle' ? 'oracle' : 'avatar'}`;
            preview.dataset.skinName = idle.name;
            target.replaceChildren(preview);
        } else {
            target.innerHTML = html(idle?.animated && idle.chromaKey ? snapshot : idle);
        }
        const video = target.querySelector('video');
        video?.play().catch(() => {});
        if (video && idle.src !== snapshot.src) {
            video.addEventListener('error', () => {
                if (applied.get(target) !== key) return;
                videoPreview?.dispose(target.firstElementChild);
                target.innerHTML = html();
            }, { once: true });
        }
    }

    async function load({ force = false, skinName = null, oracleBackground = null } = {}) {
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
            const hasChromaKey = Array.isArray(skin.chroma_key) && skin.chroma_key.length === 3;
            const background = skin.folder_name === 'oracle'
                ? (skinName ? oracleBackground : data.selected_oracle_background)
                : null;
            const idleFile = background || skin.idle_animation || skin.preview_image || 'idle.webm';
            const sourceFile = hasChromaKey && !background && skin.preview_image ? skin.preview_image : idleFile;
            const base = `/api/skins/${encodeURIComponent(skin.folder_name)}/preview/`;
            const src = base + encodeURIComponent(sourceFile);
            const animated = sourceFile.toLowerCase().endsWith('.webm');
            if (sequence !== loadSequence || !src) return null;
            const next = {
                src, animated, type: skin.type, name: skin.name || skin.folder_name,
                imageScale: skin.image_scale, imageOffsetX: skin.image_offset_x, imageOffsetY: skin.image_offset_y,
                idleSrc: base + encodeURIComponent(idleFile),
                idleAnimated: /\.webm$/i.test(idleFile),
                chromaKey: hasChromaKey ? skin.chroma_key : null,
                chromaThreshold: skin.chroma_threshold,
            };
            const changed = JSON.stringify(snapshot) !== JSON.stringify(next);
            snapshot = next;
            apply(document);
            if (changed) {
                document.dispatchEvent(new CustomEvent('decisions:assistant-identity-changed', { detail: { name: snapshot.name, type: snapshot.type } }));
            }
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
