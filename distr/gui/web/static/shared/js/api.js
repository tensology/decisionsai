/**
 * Shared API utilities — centralizes fetch wrapper and snackbar notifications.
 *
 * Usage (inside an IIFE or module):
 *   var api = window.DecisionsAPI;
 *   api.fetch("/api/foo", { method: "POST", ... }).then(...)
 *   api.snackbar("Saved!", "success");
 *   api.snackbar("Oops", "error");
 */
(function () {
    "use strict";

    /**
     * Thin fetch wrapper — auto-parses JSON, throws on non-ok with detail message.
     */
    function apiFetch(url, opts) {
        opts = opts || {};
        return fetch(url, opts).then(function (r) {
            if (!r.ok) {
                return r.json().catch(function () {
                    return {};
                }).then(function (e) {
                    var msg = e.error || e.detail || e.message;
                    if (msg == null && typeof e === "string") msg = e;
                    if (typeof msg !== "string") {
                        msg = msg != null ? JSON.stringify(msg) : r.statusText;
                    }
                    throw new Error(msg || r.statusText);
                });
            }
            // Handle 204 No Content
            var ct = r.headers.get("content-type") || "";
            if (r.status === 204 || !ct.includes("application/json")) {
                return {};
            }
            return r.json();
        });
    }

    /**
     * Show a temporary snackbar notification.
     * @param {string} message  - Text to display
     * @param {string} [type]   - "success" (default), "error", or "info"
     * @param {object} [opts]   - Optional overrides: { id, duration }
     */
    function showSnackbar(message, type, opts) {
        type = type || "success";
        opts = opts || {};
        var id = opts.id || "shared-snackbar";
        var duration = opts.duration || 3000;

        var existing = document.getElementById(id);
        if (existing) existing.remove();

        var el = document.createElement("div");
        el.id = id;
        el.style.cssText =
            "position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);" +
            "z-index:9999;padding:0.75rem 1.25rem;border-radius:0.5rem;" +
            "color:#fff;font-size:0.875rem;font-weight:500;" +
            "box-shadow:0 4px 12px rgba(0,0,0,.3);transition:opacity 0.3s;";
        el.style.background =
            type === "error" ? "#dc2626" :
            type === "info"  ? "#1a237e" :
                               "#16a34a";
        el.textContent = message;
        document.body.appendChild(el);

        requestAnimationFrame(function () { el.style.opacity = "1"; });
        setTimeout(function () {
            el.style.opacity = "0";
            setTimeout(function () { el.remove(); }, 300);
        }, duration);
    }

    // Expose as a global namespace
    window.DecisionsAPI = {
        fetch: apiFetch,
        snackbar: showSnackbar
    };
})();
