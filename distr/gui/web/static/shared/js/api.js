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

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

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
                    var err = new Error(msg || r.statusText);
                    err.detail = e;
                    throw err;
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
     * @param {string} [type]   - "success" (default), "error", "warning", or "info"
     * @param {object} [opts]   - Optional overrides: { id, duration }
     */
function showSnackbar(message, type, opts) {
        type = type || "success";
        opts = opts || {};
        var id = opts.id || "shared-snackbar";
        var duration = opts.duration != null ? opts.duration : 3000;
        if (opts.multiline) {
            duration = opts.duration != null ? opts.duration : 24000;
        }

        var existing = document.getElementById(id);
        if (existing) existing.remove();

        var el = document.createElement("div");
        el.id = id;
        el.style.cssText =
            "position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);" +
            "z-index:2147483647;padding:0.75rem 1.25rem;border-radius:0.5rem;" +
            "color:#fff;font-size:0.875rem;font-weight:500;" +
            "box-shadow:0 4px 12px rgba(0,0,0,.3);transition:opacity 0.3s;";
        if (opts.multiline) {
            el.style.whiteSpace = "pre-wrap";
            el.style.textAlign = "left";
            el.style.fontWeight = "400";
            el.style.lineHeight = "1.45";
            el.style.maxWidth = opts.maxWidth || "min(92vw, 560px)";
            el.style.maxHeight = "min(70vh, 420px)";
            el.style.overflowY = "auto";
        }
        el.style.background =
            type === "error" ? "#dc2626" :
            type === "warning" ? "#ca8a04" :
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

    /**
     * Shared app confirmation modal.
     * Enter confirms the primary action, Escape cancels, and callers may use
     * either opts.onConfirm or the returned Promise<boolean>.
     */
    function showConfirm(opts) {
        opts = opts || {};
        var title = opts.title || "Confirm";
        var message = opts.message || "Are you sure?";
        var confirmLabel = opts.confirmLabel || "Confirm";
        var cancelLabel = opts.cancelLabel || "Cancel";
        var danger = !!opts.danger;
        var onConfirm = typeof opts.onConfirm === "function" ? opts.onConfirm : null;
        var onCancel = typeof opts.onCancel === "function" ? opts.onCancel : null;
        var previousActive = document.activeElement;

        var existing = document.getElementById("decisions-confirm-modal");
        if (existing) existing.remove();

        return new Promise(function(resolve) {
            var overlay = document.createElement("div");
            overlay.id = "decisions-confirm-modal";
            overlay.className = "decisions-confirm-overlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.setAttribute("aria-labelledby", "decisions-confirm-title");
            overlay.innerHTML =
                '<div class="decisions-confirm-modal">' +
                    '<h3 id="decisions-confirm-title" class="decisions-confirm-title">' + esc(title) + '</h3>' +
                    '<p class="decisions-confirm-message">' + esc(message) + '</p>' +
                    '<div class="decisions-confirm-hotkeys" aria-label="Keyboard shortcuts">' +
                        '<kbd>Enter</kbd><span>confirm</span><kbd>Esc</kbd><span>cancel</span>' +
                    '</div>' +
                    '<div class="decisions-confirm-actions">' +
                        '<button type="button" class="decisions-confirm-cancel">' + esc(cancelLabel) + '</button>' +
                        '<button type="button" class="decisions-confirm-ok' + (danger ? ' is-danger' : '') + '">' + esc(confirmLabel) + '</button>' +
                    '</div>' +
                '</div>';

            document.body.appendChild(overlay);

            var okBtn = overlay.querySelector(".decisions-confirm-ok");
            var cancelBtn = overlay.querySelector(".decisions-confirm-cancel");
            var settled = false;

            function close(result) {
                if (settled) return;
                settled = true;
                document.removeEventListener("keydown", onKeyDown, true);
                overlay.remove();
                if (previousActive && typeof previousActive.focus === "function" && document.contains(previousActive)) {
                    previousActive.focus();
                }
                if (result) {
                    if (onConfirm) onConfirm();
                } else if (onCancel) {
                    onCancel();
                }
                resolve(!!result);
            }

            function onKeyDown(evt) {
                if (!document.getElementById("decisions-confirm-modal")) return;
                if (evt.key === "Escape") {
                    evt.preventDefault();
                    evt.stopPropagation();
                    close(false);
                    return;
                }
                if (evt.key === "Enter") {
                    evt.preventDefault();
                    evt.stopPropagation();
                    close(true);
                }
            }

            overlay.addEventListener("click", function(evt) {
                if (evt.target === overlay) close(false);
            });
            if (cancelBtn) cancelBtn.addEventListener("click", function() { close(false); });
            if (okBtn) okBtn.addEventListener("click", function() { close(true); });
            document.addEventListener("keydown", onKeyDown, true);
            if (okBtn) okBtn.focus();
        });
    }

    // Expose as a global namespace
    window.DecisionsAPI = {
        fetch: apiFetch,
        snackbar: showSnackbar,
        confirm: showConfirm
    };
})();
