/**
 * DecisionsAI Mermaid diagram viewer.
 *
 * Opens freestanding popup windows with render, code edit, PNG/JPEG export,
 * and clipboard copy helpers.
 */
(function () {
    "use strict";

    var STORAGE_PREFIX = "decisions-mermaid:";
    var _mermaidReady = null;

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function normalizeHistoryTimestamp(ts) {
        if (ts == null || ts === "") return null;
        if (typeof ts === "string") {
            var parsedText = ts.trim();
            if (!parsedText) return null;
            if (/^\d+(\.\d+)?$/.test(parsedText)) {
                ts = Number(parsedText);
            } else {
                var fromText = new Date(parsedText);
                return Number.isFinite(fromText.getTime()) ? fromText : null;
            }
        }
        var numeric = Number(ts);
        if (!Number.isFinite(numeric) || numeric <= 0) return null;
        if (numeric > 1000000000000) {
            numeric = Math.round(numeric);
        } else if (numeric > 10000000000) {
            numeric = Math.round(numeric);
        } else {
            numeric = Math.round(numeric * 1000);
        }
        var date = new Date(numeric);
        if (!Number.isFinite(date.getTime())) return null;
        if (date.getFullYear() < 2020 || date.getFullYear() > 2100) return null;
        return date;
    }

    function randomKey() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    }

    function loadMermaid() {
        if (window.mermaid) return Promise.resolve(window.mermaid);
        if (_mermaidReady) return _mermaidReady;
        _mermaidReady = new Promise(function (resolve, reject) {
            var script = document.createElement("script");
            script.src = "/static/vendor/mermaid/mermaid.min.js";
            script.async = true;
            script.onload = function () {
                if (!window.mermaid) {
                    reject(new Error("Mermaid failed to load"));
                    return;
                }
                window.mermaid.initialize({
                    startOnLoad: false,
                    theme: "default",
                    securityLevel: "strict",
                });
                resolve(window.mermaid);
            };
            script.onerror = function () {
                reject(new Error("Could not load Mermaid library"));
            };
            document.head.appendChild(script);
        });
        return _mermaidReady;
    }

    function parseQuery() {
        var params = new URLSearchParams(window.location.search);
        return {
            id: (params.get("id") || "").trim(),
            key: (params.get("key") || "").trim(),
            title: (params.get("title") || "").trim(),
        };
    }

    function svgToCanvas(svgEl, mime, quality) {
        return new Promise(function (resolve, reject) {
            var svgData = new XMLSerializer().serializeToString(svgEl);
            var blob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
            var url = URL.createObjectURL(blob);
            var img = new Image();
            img.onload = function () {
                var canvas = document.createElement("canvas");
                canvas.width = img.naturalWidth || img.width;
                canvas.height = img.naturalHeight || img.height;
                var ctx = canvas.getContext("2d");
                if (!ctx) {
                    URL.revokeObjectURL(url);
                    reject(new Error("Canvas unavailable"));
                    return;
                }
                if (mime === "image/jpeg") {
                    ctx.fillStyle = "#ffffff";
                    ctx.fillRect(0, 0, canvas.width, canvas.height);
                }
                ctx.drawImage(img, 0, 0);
                URL.revokeObjectURL(url);
                canvas.toBlob(function (out) {
                    if (!out) reject(new Error("Export failed"));
                    else resolve(out);
                }, mime, quality);
            };
            img.onerror = function () {
                URL.revokeObjectURL(url);
                reject(new Error("Could not rasterize diagram"));
            };
            img.src = url;
        });
    }

    function downloadBlob(blob, filename) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    }

    function setStatus(el, message, kind) {
        if (!el) return;
        el.textContent = message || "";
        el.classList.remove("mermaid-viewer-status-error", "mermaid-viewer-status-ok");
        if (kind) el.classList.add("mermaid-viewer-status-" + kind);
    }

    function buildViewerDom(root, title) {
        root.innerHTML =
            '<header class="mermaid-viewer-toolbar">' +
            '<div class="mermaid-viewer-title" id="mermaidViewerTitle">' + esc(title || "Diagram") + "</div>" +
            '<div class="mermaid-viewer-actions">' +
            '<button type="button" class="mermaid-viewer-btn" data-action="copy-code">Copy code</button>' +
            '<button type="button" class="mermaid-viewer-btn" data-action="copy-image">Copy image</button>' +
            '<button type="button" class="mermaid-viewer-btn" data-action="export-png">PNG</button>' +
            '<button type="button" class="mermaid-viewer-btn" data-action="export-jpg">JPEG</button>' +
            '<button type="button" class="mermaid-viewer-btn" data-action="export-google">Google Drawing</button>' +
            '<button type="button" class="mermaid-viewer-btn mermaid-viewer-btn-primary" data-action="render">Render</button>' +
            "</div></header>" +
            '<div class="mermaid-viewer-main">' +
            '<div class="mermaid-viewer-canvas-wrap"><div class="mermaid-viewer-canvas" id="mermaidViewerCanvas"></div></div>' +
            '<aside class="mermaid-viewer-side">' +
            '<div class="mermaid-viewer-side-label">History</div>' +
            '<div class="mermaid-viewer-history" id="mermaidViewerHistory"></div>' +
            '<div class="mermaid-viewer-side-label">Mermaid source</div>' +
            '<textarea class="mermaid-viewer-code" id="mermaidViewerCode" spellcheck="false"></textarea>' +
            "</aside></div>" +
            '<div class="mermaid-viewer-status" id="mermaidViewerStatus"></div>';

        return {
            titleEl: document.getElementById("mermaidViewerTitle"),
            canvasEl: document.getElementById("mermaidViewerCanvas"),
            codeEl: document.getElementById("mermaidViewerCode"),
            statusEl: document.getElementById("mermaidViewerStatus"),
            historyEl: document.getElementById("mermaidViewerHistory"),
        };
    }

    function apiFetch(path, opts) {
        if (window.DecisionsAPI && window.DecisionsAPI.fetch) {
            return window.DecisionsAPI.fetch(path, opts);
        }
        return fetch(path, opts).then(function (r) {
            if (!r.ok) throw new Error(r.statusText);
            return r.json();
        });
    }

    function loadHistoryList(historyEl, onSelect, statusEl) {
        if (!historyEl) return Promise.resolve();
        historyEl.innerHTML = '<div class="mermaid-viewer-history-empty">Loading…</div>';
        return apiFetch("/api/diagrams/history").then(function (data) {
            var items = (data && data.items) || [];
            if (!items.length) {
                historyEl.innerHTML = '<div class="mermaid-viewer-history-empty">No diagrams yet.</div>';
                return;
            }
            historyEl.innerHTML = items.map(function (item) {
                var whenDate = normalizeHistoryTimestamp(item.created_at);
                var when = whenDate ? whenDate.toLocaleString() : "Date unavailable";
                return (
                    '<div class="mermaid-viewer-history-item" data-history-id="' + esc(item.id) + '">' +
                    '<div class="mermaid-viewer-history-meta">' +
                    '<span class="mermaid-viewer-history-title">' + esc(item.title || "Diagram") + "</span>" +
                    '<span class="mermaid-viewer-history-time">' + esc(when) + "</span>" +
                    "</div>" +
                    '<div class="mermaid-viewer-history-actions">' +
                    '<button type="button" class="mermaid-viewer-open-btn mermaid-action-btn" data-action="open-diagram" data-history-id="' + esc(item.id) + '" title="Open diagram in viewer" aria-label="Open diagram in viewer">' +
                    '<svg xmlns="http://www.w3.org/2000/svg" class="mermaid-action-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z" />' +
                    '<circle cx="12" cy="12" r="3" />' +
                    '</svg></button>' +
                    '<button type="button" class="mermaid-viewer-delete-btn mermaid-action-btn mermaid-action-btn-danger" data-action="delete-diagram" data-history-id="' + esc(item.id) + '" title="Delete diagram" aria-label="Delete diagram">' +
                    '<svg xmlns="http://www.w3.org/2000/svg" class="mermaid-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
                    '<path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>' +
                    "</svg></button>" +
                    "</div>" +
                    "</div>"
                );
            }).join("");
            historyEl.querySelectorAll("[data-action='open-diagram']").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var id = btn.getAttribute("data-history-id");
                    var match = items.find(function (row) { return row.id === id; });
                    if (match && onSelect) onSelect(match);
                });
            });
            historyEl.querySelectorAll("[data-action='delete-diagram']").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var id = btn.getAttribute("data-history-id");
                    if (!id) return;
                            var row = items.find(function (row) { return row.id === id; }) || {};
                            var title = row.title || "diagram";
                            if (!window.DecisionsAPI || typeof window.DecisionsAPI.confirm !== "function") {
                                if (!window.confirm("Delete " + (title || "diagram") + "?")) {
                                    return;
                                }
                                apiFetch("/api/diagrams/" + encodeURIComponent(id), { method: "DELETE" })
                                    .then(function () { loadHistoryList(historyEl, onSelect, statusEl); })
                                    .catch(function () {
                                        setStatus(statusEl, "Failed to delete diagram.", "error");
                                    });
                                return;
                            }
                            window.DecisionsAPI.confirm({
                                title: "Delete Mermaid diagram",
                                message: 'Delete "' + title + '"? This cannot be undone.',
                        confirmLabel: "Delete",
                        danger: true,
                        onConfirm: function () {}
                            }).then(function (confirmed) {
                                if (!confirmed) return;
                                apiFetch("/api/diagrams/" + encodeURIComponent(id), { method: "DELETE" })
                                    .then(function () { loadHistoryList(historyEl, onSelect, statusEl); })
                                    .catch(function () {
                                        setStatus(statusEl, "Failed to delete diagram.", "error");
                                    });
                            });
                        });
            });
        }).catch(function () {
            historyEl.innerHTML = '<div class="mermaid-viewer-history-empty">History unavailable.</div>';
        });
    }

    async function persistDiagramHistory(title, code) {
        try {
            await apiFetch("/api/diagrams", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: title, code: code }),
            });
        } catch (_) {
            /* non-fatal */
        }
    }

    async function renderInto(canvasEl, code) {
        var mermaid = await loadMermaid();
        var renderId = "mermaid-" + randomKey().replace(/[^a-zA-Z0-9]/g, "");
        var result = await mermaid.render(renderId, code);
        canvasEl.innerHTML = result.svg;
        return canvasEl.querySelector("svg");
    }

    function wireViewer(root, initial) {
        var parts = buildViewerDom(root, initial.title);
        parts.codeEl.value = initial.code || "";
        var lastPersisted = initial && initial.persisted ? (initial.code || "") : "";

        var renderDiagram = async function () {
            var code = parts.codeEl.value.trim();
            if (!code) {
                setStatus(parts.statusEl, "Enter Mermaid code to render.", "error");
                return null;
            }
            setStatus(parts.statusEl, "Rendering…", null);
            try {
                var svg = await renderInto(parts.canvasEl, code);
                setStatus(parts.statusEl, "Rendered.", "ok");
                var title = (parts.titleEl && parts.titleEl.textContent) || initial.title || "Diagram";
                if (code !== lastPersisted) {
                    lastPersisted = code;
                    persistDiagramHistory(title, code).then(function () {
                        loadHistoryList(parts.historyEl, loadHistoryItem, parts.statusEl);
                    });
                }
                return svg;
            } catch (err) {
                parts.canvasEl.innerHTML = "";
                setStatus(parts.statusEl, (err && err.message) || "Render failed.", "error");
                return null;
            }
        };

        function loadHistoryItem(item) {
            if (!item) return;
            parts.codeEl.value = item.code || "";
            if (parts.titleEl) parts.titleEl.textContent = item.title || "Diagram";
            lastPersisted = item.code || "";
            renderDiagram();
        }

        loadHistoryList(parts.historyEl, loadHistoryItem, parts.statusEl);

        root.querySelectorAll("[data-action]").forEach(function (btn) {
            btn.addEventListener("click", async function () {
                var action = btn.getAttribute("data-action");
                if (action === "render") {
                    await renderDiagram();
                    return;
                }
                if (action === "copy-code") {
                    try {
                        await navigator.clipboard.writeText(parts.codeEl.value);
                        setStatus(parts.statusEl, "Code copied.", "ok");
                    } catch (_) {
                        setStatus(parts.statusEl, "Clipboard unavailable.", "error");
                    }
                    return;
                }
                var svg = parts.canvasEl.querySelector("svg") || await renderDiagram();
                if (!svg) return;
                if (action === "export-google") {
                    try {
                        setStatus(parts.statusEl, "Uploading to Google Drive…", null);
                        var svgMarkup = new XMLSerializer().serializeToString(svg);
                        var title = (parts.titleEl && parts.titleEl.textContent) || initial.title || "Diagram";
                        var result = await apiFetch("/api/diagrams/export/google-drawing", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ svg: svgMarkup, title: title, folder_id: "root" }),
                        });
                        if (result && result.url) {
                            setStatus(parts.statusEl, "Opened Google Drawing export.", "ok");
                            window.open(result.url, "_blank", "noopener,noreferrer");
                        } else {
                            setStatus(parts.statusEl, "Google Drawing created.", "ok");
                        }
                    } catch (err) {
                        setStatus(parts.statusEl, (err && err.message) || "Google export failed. Connect Google in Settings → Advanced.", "error");
                    }
                    return;
                }
                if (action === "copy-image") {
                    try {
                        var pngBlob = await svgToCanvas(svg, "image/png");
                        await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob })]);
                        setStatus(parts.statusEl, "Image copied.", "ok");
                    } catch (_) {
                        setStatus(parts.statusEl, "Could not copy image.", "error");
                    }
                    return;
                }
                if (action === "export-png" || action === "export-jpg") {
                    try {
                        var mime = action === "export-jpg" ? "image/jpeg" : "image/png";
                        var ext = action === "export-jpg" ? "jpg" : "png";
                        var blob = await svgToCanvas(svg, mime, 0.92);
                        var safeTitle = (initial.title || "diagram").replace(/[^\w.-]+/g, "-").slice(0, 60) || "diagram";
                        downloadBlob(blob, safeTitle + "." + ext);
                        setStatus(parts.statusEl, "Downloaded " + ext.toUpperCase() + ".", "ok");
                    } catch (err) {
                        setStatus(parts.statusEl, (err && err.message) || "Export failed.", "error");
                    }
                }
            });
        });

        renderDiagram();
    }

    async function fetchDiagramById(id) {
        var api = window.DecisionsAPI && window.DecisionsAPI.fetch;
        var fetcher = api || window.fetch.bind(window);
        var payload = await fetcher("/api/diagrams/" + encodeURIComponent(id));
        return {
            title: payload.title || "Diagram",
            code: payload.code || "",
        };
    }

    function readFromSession(key) {
        try {
            var raw = sessionStorage.getItem(STORAGE_PREFIX + key);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function writeToSession(key, payload) {
        sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(payload));
    }

    function openPopup(opts) {
        opts = opts || {};
        var code = (opts.code || "").trim();
        if (!code) return null;
        var title = (opts.title || "Diagram").trim() || "Diagram";
        var key = opts.key || randomKey();
        writeToSession(key, { title: title, code: code });
        var url = "/diagram/?key=" + encodeURIComponent(key) + "&title=" + encodeURIComponent(title);
        var features = "popup=yes,width=1100,height=760,resizable=yes,scrollbars=yes";
        return window.open(url, "decisions-mermaid-" + key.slice(0, 8), features);
    }

    async function openById(id, title) {
        var url = "/diagram/?id=" + encodeURIComponent(id);
        if (title) url += "&title=" + encodeURIComponent(title);
        var features = "popup=yes,width=1100,height=760,resizable=yes,scrollbars=yes";
        return window.open(url, "decisions-mermaid-" + id.slice(0, 8), features);
    }

    async function initPage() {
        document.body.classList.add("mermaid-viewer-body");
        var root = document.getElementById("mermaid-viewer-root");
        if (!root) return;

        var query = parseQuery();
        var payload = { title: query.title || "Diagram", code: "", persisted: false };

        if (query.key) {
            var stored = readFromSession(query.key);
            if (stored) payload = stored;
        } else if (query.id) {
            try {
                payload = await fetchDiagramById(query.id);
                payload.persisted = true;
            } catch (err) {
                root.innerHTML = '<p style="padding:20px;color:#fca5a5;">' + esc((err && err.message) || "Diagram not found.") + "</p>";
                return;
            }
        }

        if (query.title) payload.title = query.title;
        wireViewer(root, payload);
    }

    window.DecisionsMermaid = {
        open: openPopup,
        openById: openById,
        initPage: initPage,
        renderInlinePreview: async function (container, code) {
            if (!container) return;
            try {
                await renderInto(container, code);
            } catch (_) {
                container.textContent = "Could not preview diagram.";
            }
        },
    };
})();
