(function () {
    "use strict";

    var pollTimer = null;
    var apiFetch = window.DecisionsAPI && window.DecisionsAPI.fetch
        ? window.DecisionsAPI.fetch.bind(window.DecisionsAPI)
        : function (url, opts) { return fetch(url, opts).then(function (r) { return r.json(); }); };

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function statusClass(status) {
        return "downloads-status downloads-status-" + esc(status || "queued");
    }

    function formatStatus(status) {
        var raw = String(status || "queued");
        return raw.charAt(0).toUpperCase() + raw.slice(1);
    }

    function fileNameFromPath(path) {
        var text = String(path || "");
        if (!text) return "Download";
        var parts = text.split(/[\\/]/);
        return parts[parts.length - 1] || text;
    }

    function iconFolder() {
        return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    }

    function iconClose() {
        return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
    }

    function jobFileItems(job) {
        var items = Array.isArray(job.file_items) ? job.file_items.slice() : [];
        if (!items.length && Array.isArray(job.files)) {
            items = job.files.map(function (path) {
                return {
                    path: path,
                    name: fileNameFromPath(path),
                    status: job.status === "failed" ? "failed" : "completed",
                    progress: 100
                };
            });
        }
        if (!items.length && job.current_file) {
            items.push({
                path: job.current_file,
                name: fileNameFromPath(job.current_file),
                status: job.status,
                progress: Number(job.progress) || 0,
                speed: job.speed || "",
                eta: job.eta || ""
            });
        }
        return items.map(function (item) {
            var next = Object.assign({}, item);
            next.name = next.name || fileNameFromPath(next.path);
            return next;
        });
    }

    function renderFileRow(job, item) {
        var status = item.status || job.status || "completed";
        var isActive = status === "running" || status === "queued";
        var progress = Math.max(0, Math.min(100, Number(item.progress != null ? item.progress : job.progress) || 0));
        var sublineParts = [];
        if (status === "failed" && item.error) sublineParts.push(item.error);
        else if (status === "completed") sublineParts.push("Downloaded");
        else if (status === "cancelled") sublineParts.push("Cancelled");
        else sublineParts.push(formatStatus(status));
        if (isActive && item.speed) sublineParts.push(item.speed);
        if (isActive && item.eta) sublineParts.push("ETA " + item.eta);
        return (
            '<div class="downloads-file-row">' +
            '<div class="downloads-file-main">' +
            '<div class="downloads-file-name" title="' + esc(item.name || "") + '">' + esc(item.name || "Download") + '</div>' +
            '<div class="downloads-file-subline">' + esc(sublineParts.join(" · ")) + '</div>' +
            (isActive ? (
                '<div class="downloads-file-progress">' +
                '<div class="downloads-progress-wrap"><div class="downloads-progress-bar" style="width:' + progress + '%"></div></div>' +
                '</div>'
            ) : '') +
            '</div>' +
            '<div class="downloads-file-actions">' +
            ((status === "completed" || status === "failed" || status === "cancelled") && item.path
                ? '<button type="button" class="downloads-icon-btn" title="Show in Finder" data-reveal-job="' + esc(job.id) + '" data-reveal-path="' + esc(item.path) + '">' + iconFolder() + '</button>'
                : '') +
            ((status === "completed" || status === "failed" || status === "cancelled")
                ? '<button type="button" class="downloads-icon-btn downloads-icon-btn-danger" title="Remove from list" data-remove-id="' + esc(job.id) + '">' + iconClose() + '</button>'
                : '') +
            ((status === "running" || status === "queued")
                ? '<button type="button" class="downloads-icon-btn downloads-icon-btn-danger" title="Cancel download" data-cancel-id="' + esc(job.id) + '">' + iconClose() + '</button>'
                : '') +
            '</div>' +
            '</div>'
        );
    }

    function renderJobCard(job) {
        var isActive = job.status === "running" || job.status === "queued";
        var items = jobFileItems(job);
        var title = job.title || (items[0] && items[0].name) || "Download";
        return (
            '<article class="downloads-card" data-job-id="' + esc(job.id) + '">' +
            '<div class="downloads-card-head">' +
            '<div>' +
            '<h3 class="downloads-card-title">' + esc(title) + '</h3>' +
            '<div class="downloads-card-meta">' + esc(job.message || "") + '</div>' +
            '</div>' +
            '<span class="' + statusClass(job.status) + '">' + esc(formatStatus(job.status)) + '</span>' +
            '</div>' +
            (isActive ? (
                '<div class="downloads-job-progress">' +
                '<div class="downloads-progress-wrap"><div class="downloads-progress-bar" style="width:' + Math.max(0, Math.min(100, Number(job.progress) || 0)) + '%"></div></div>' +
                '<div class="downloads-progress-label"><span>' + esc(((Number(job.progress) || 0).toFixed(1)) + "%") + '</span><span>' + esc([job.speed || "", job.eta ? "ETA " + job.eta : ""].filter(Boolean).join(" · ")) + '</span></div>' +
                '</div>'
            ) : '') +
            '<div class="downloads-file-list">' + items.map(function (item) { return renderFileRow(job, item); }).join("") + '</div>' +
            '</article>'
        );
    }

    function renderLists(jobs) {
        var rows = (jobs || []).slice().sort(function (a, b) {
            var aActive = (a.status === "running" || a.status === "queued") ? 1 : 0;
            var bActive = (b.status === "running" || b.status === "queued") ? 1 : 0;
            if (aActive !== bActive) return bActive - aActive;
            return Number(b.updated_at || b.created_at || 0) - Number(a.updated_at || a.created_at || 0);
        });
        var activeCount = rows.filter(function (j) { return j.status === "running" || j.status === "queued"; }).length;
        var inactiveCount = rows.length - activeCount;
        var list = document.getElementById("downloadsList");
        var empty = document.getElementById("downloadsEmpty");
        var refreshBtn = document.getElementById("downloadsRefreshBtn");
        var clearBtn = document.getElementById("downloadsClearBtn");
        if (list) {
            list.innerHTML = rows.map(renderJobCard).join("");
        }
        if (empty) {
            empty.style.display = rows.length ? "none" : "";
        }
        if (refreshBtn) {
            refreshBtn.hidden = rows.length === 0;
        }
        if (clearBtn) {
            clearBtn.hidden = rows.length === 0;
            clearBtn.disabled = inactiveCount === 0;
        }
    }

    function refresh() {
        return apiFetch("/api/downloads?include_completed=true").then(function (data) {
            var jobs = (data && data.jobs) || [];
            renderLists(jobs);
            var hasActive = jobs.some(function (j) {
                return j.status === "running" || j.status === "queued";
            });
            if (hasActive && !pollTimer) {
                pollTimer = window.setInterval(refresh, 1500);
            } else if (!hasActive && pollTimer) {
                window.clearInterval(pollTimer);
                pollTimer = null;
            }
        }).catch(function () {
            if (window.DecisionsAPI) window.DecisionsAPI.snackbar("Could not load downloads", "error");
        });
    }

    function cancelJob(jobId) {
        return apiFetch("/api/downloads/" + encodeURIComponent(jobId), { method: "DELETE" })
            .then(function () { return refresh(); });
    }

    function removeJob(jobId) {
        return apiFetch("/api/downloads/" + encodeURIComponent(jobId), { method: "DELETE" })
            .then(function () { return refresh(); });
    }

    function revealFile(jobId, filePath) {
        return apiFetch("/api/downloads/" + encodeURIComponent(jobId) + "/reveal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file_path: filePath })
        });
    }

    function clearInactive() {
        return apiFetch("/api/downloads/clear-inactive", { method: "POST" })
            .then(function () { return refresh(); });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var refreshBtn = document.getElementById("downloadsRefreshBtn");
        var clearBtn = document.getElementById("downloadsClearBtn");
        if (refreshBtn) refreshBtn.addEventListener("click", refresh);
        if (clearBtn) clearBtn.addEventListener("click", clearInactive);
        document.body.addEventListener("click", function (e) {
            var cancelBtn = e.target && e.target.closest ? e.target.closest("[data-cancel-id]") : null;
            if (cancelBtn) {
                cancelJob(cancelBtn.getAttribute("data-cancel-id"));
                return;
            }
            var removeBtn = e.target && e.target.closest ? e.target.closest("[data-remove-id]") : null;
            if (removeBtn) {
                removeJob(removeBtn.getAttribute("data-remove-id"));
                return;
            }
            var revealBtn = e.target && e.target.closest ? e.target.closest("[data-reveal-job]") : null;
            if (revealBtn) {
                revealFile(revealBtn.getAttribute("data-reveal-job"), revealBtn.getAttribute("data-reveal-path"))
                    .catch(function () {
                        if (window.DecisionsAPI) window.DecisionsAPI.snackbar("Could not open Finder", "error");
                    });
            }
        });
        refresh();
    });

    window.loadDownloadsSection = refresh;
})();
