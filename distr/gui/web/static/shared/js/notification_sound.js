/**
 * Shared notification sound player.
 *
 * Slugs match distr.core.notification_sounds and the files live under
 * assets/sounds/notifications. The user picks one in Settings.
 */
(function () {
    'use strict';

    var FILES = {
        bell: '/assets/sounds/notifications/bell.wav',
        chime: '/assets/sounds/notifications/chime.mp3',
        dong: '/assets/sounds/notifications/dong.mp3',
        gentle: '/assets/sounds/notifications/gentle.mp3',
        tone: '/assets/sounds/notifications/tone.mp3',
        elegant: '/assets/sounds/notifications/elegant.mp3',
        achievement: '/assets/sounds/notifications/achievement.mp3',
        none: ''
    };

    var DEFAULT_SLUG = 'bell';
    var cachedSlug = null;
    var pending = null;
    var lastPlayedAt = 0;
    var catalogPending = null;
    var aliases = {};

    function catalog() {
        if (catalogPending) return catalogPending;
        catalogPending = fetch('/assets/sounds/notifications/catalog.json?v=20261030-crossroads')
            .then(function (response) {
                if (!response.ok) throw new Error('Notification sounds could not be loaded.');
                return response.json();
            })
            .then(function (data) {
                aliases = data.aliases || {};
                (data.sounds || []).forEach(function (item) {
                    FILES[item.slug] = item.file ? '/assets/sounds/notifications/' + item.file : '';
                });
                return data.sounds || [];
            })
            .catch(function (error) { catalogPending = null; throw error; });
        return catalogPending;
    }

    function resolveSlug(slug) {
        var key = String(slug || DEFAULT_SLUG).toLowerCase();
        return Object.prototype.hasOwnProperty.call(aliases, key) ? aliases[key] : key;
    }

    function urlFor(slug) {
        var key = String(slug || DEFAULT_SLUG).toLowerCase();
        return Object.prototype.hasOwnProperty.call(FILES, key) ? FILES[key] : FILES[DEFAULT_SLUG];
    }

    function play(slug) {
        var key = String(slug || DEFAULT_SLUG).toLowerCase();
        if (!Object.prototype.hasOwnProperty.call(FILES, key)) {
            return catalog().catch(function () { return []; }).then(function () { playUrl(urlFor(key)); });
        }
        return playUrl(urlFor(key));
    }

    function playUrl(url) {
        if (!url) return;
        // Collapse bursts: several notices can land within a second or two.
        var now = Date.now();
        if (now - lastPlayedAt < 1200) return;
        lastPlayedAt = now;
        try {
            var audio = new Audio(url);
            audio.volume = 0.8;
            audio.play().catch(function () {});
        } catch (error) {
            console.warn('Could not play notification sound:', error);
        }
    }

    function current() {
        if (cachedSlug !== null) return Promise.resolve(cachedSlug);
        if (pending) return pending;
        pending = fetch('/api/general')
            .then(function (response) { return response.ok ? response.json() : {}; })
            .then(function (data) {
                cachedSlug = String((data && data.notification_sound) || DEFAULT_SLUG).toLowerCase();
                return cachedSlug;
            })
            .catch(function () {
                cachedSlug = DEFAULT_SLUG;
                return cachedSlug;
            })
            .finally(function () { pending = null; });
        return pending;
    }

    /** Play the configured sound. By default only when the tab is in the background. */
    function playCurrent(options) {
        var onlyWhenHidden = !options || options.onlyWhenHidden !== false;
        if (onlyWhenHidden && typeof document !== 'undefined' && !document.hidden) return Promise.resolve();
        return current().then(function (slug) {
            if (slug && slug !== 'none') return play(slug);
        });
    }

    window.DecisionsNotificationSound = {
        play: play,
        current: current,
        playCurrent: playCurrent,
        catalog: catalog,
        resolveSlug: resolveSlug,
        FILES: FILES
    };
})();
