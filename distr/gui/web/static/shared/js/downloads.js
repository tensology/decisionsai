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

    function renderJobCard(job, options) {
        options = options || {};
        var pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
        var isActive = job.status === "running" || job.status === "queued";
        var speedEta = [];
        if (job.speed) speedEta.push(job.speed);
        if (job.eta) speedEta.push("ETA " + job.eta);
        var filesHtml = "";
        if (job.files && job.files.length) {
            filesHtml = "<ul class=\"downloads-files\">" + job.files.slice(0, 5).map(function (f) {
                return "<li>" + esc(f) + "</li>";
            }).join("") + "</ul>";
        }
        var actions = "";
        if (isActive && options.allowCancel) {
            actions = '<div class="downloads-actions"><button type="button" class="downloads-btn downloads-btn-danger" data-cancel-id="' + esc(job.id) + '">Cancel</button></div>';
        }
        return (
            '<article class="downloads-card" data-job-id="' + esc(job.id) + '">' +
            '<div class="downloads-card-head">' +
            '<div><h3 class="downloads-card-title">' + esc(job.title || "Download") + '</h3>' +
            '<div class="downloads-card-meta">' + esc(job.message || job.url || "") + '</div></div>' +
            '<span class="' + statusClass(job.status) + '">' + esc(job.status) + '</span>' +
            '</div>' +
            (isActive ? (
                '<div class="downloads-progress-wrap"><div class="downloads-progress-bar" style="width:' + pct + '%"></div></div>' +
                '<div class="downloads-progress-label"><span>' + pct.toFixed(1) + '%</span><span>' + esc(speedEta.join(" · ")) + '</span></div>'
            ) : "") +
            (job.error ? '<div class="downloads-card-meta" style="color:#fca5a5">' + esc(job.error) + '</div>' : '') +
            (job.output_dir ? '<div class="downloads-card-meta">Folder: ' + esc(job.output_dir) + '</div>' : '') +
            filesHtml +
            actions +
            '</article>'
        );
    }

    function renderLists(jobs) {
        var active = jobs.filter(function (j) { return j.status === "running" || j.status === "queued"; });
        var history = jobs.filter(function (j) { return j.status !== "running" && j.status !== "queued"; });

        var activeList = document.getElementById("downloadsActiveList");
        var historyList = document.getElementById("downloadsHistoryList");
        var activeEmpty = document.getElementById("downloadsActiveEmpty");
        var historyEmpty = document.getElementById("downloadsHistoryEmpty");

        if (activeList) {
            activeList.innerHTML = active.map(function (j) { return renderJobCard(j, { allowCancel: true }); }).join("");
        }
        if (historyList) {
            historyList.innerHTML = history.map(function (j) { return renderJobCard(j, {}); }).join("");
        }
        if (activeEmpty) activeEmpty.style.display = active.length ? "none" : "";
        if (historyEmpty) historyEmpty.style.display = history.length ? "none" : "";
    }

    function refresh() {
        return apiFetch("/api/downloads?include_completed=true").then(function (data) {
            renderLists((data && data.jobs) || []);
            var hasActive = ((data && data.jobs) || []).some(function (j) {
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

    document.addEventListener("DOMContentLoaded", function () {
        var refreshBtn = document.getElementById("downloadsRefreshBtn");
        if (refreshBtn) refreshBtn.addEventListener("click", refresh);
        document.body.addEventListener("click", function (e) {
            var btn = e.target && e.target.closest ? e.target.closest("[data-cancel-id]") : null;
            if (!btn) return;
            cancelJob(btn.getAttribute("data-cancel-id"));
        });
        refresh();
    });
})();
