/**
 * DecisionsAI — live Laya submission state for thread lists.
 *
 * Listens to the existing events stream (window "decisionsai-event", fed by
 * events_stream.js from GET /api/events/stream) for ORCHESTRATION_EVENT
 * payloads whose kind is laya.submission.*. Keeps one state per thread id and
 * re-broadcasts a window CustomEvent "decisionsai-laya-thread" with detail
 * { threadId, state, created, projectId, title, submissionId, error }.
 * state: "active" (accepted/routed/progress) | "done" | "failed".
 * Never selects, opens or focuses a thread. No polling.
 */
(function () {
    if (window.DecisionsLayaThreads) return;
    var threads = new Map(); // threadId -> record
    var subToThread = new Map(); // submissionId -> threadId
    var ORCH = "orchestration.event";
    var PREFIX = "laya.submission.";

    function stateFor(kind) {
        if (kind === "done") return "done";
        if (kind === "failed") return "failed";
        if (kind === "accepted" || kind === "routed" || kind === "progress") return "active";
        return "";
    }

    function handle(data) {
        if (!data || typeof data !== "object") return;
        var full = String(data.kind || "");
        if (full.indexOf(PREFIX) !== 0) return;
        var kind = full.slice(PREFIX.length);
        var st = stateFor(kind);
        if (!st) return;
        var sid = data.submission_id ? String(data.submission_id) : "";
        var tid = Number(data.thread_id);
        if (!(tid > 0) && sid && subToThread.has(sid)) tid = subToThread.get(sid);
        if (!(tid > 0)) return; // "accepted" before routing: no thread yet
        if (sid) subToThread.set(sid, tid);
        var prev = threads.get(tid) || {};
        var rec = {
            threadId: tid,
            state: st,
            kind: kind,
            created: Boolean(prev.created || data.thread_created === true),
            projectId: data.project_id != null ? Number(data.project_id) : (prev.projectId != null ? prev.projectId : null),
            projectName: data.project_name || prev.projectName || "",
            title: data.thread_title || prev.title || "",
            submissionId: sid || prev.submissionId || "",
            error: st === "failed" ? String(data.error || "Laya submission failed") : "",
            at: Date.now(),
        };
        threads.set(tid, rec);
        try {
            window.dispatchEvent(new CustomEvent("decisionsai-laya-thread", { detail: rec }));
        } catch (e) { /* ignore */ }
    }

    window.addEventListener("decisionsai-event", function (ev) {
        var d = ev && ev.detail;
        if (!d || d.type !== ORCH) return;
        handle(d.data);
    });

    var css = ".laya-live-state{display:inline-block;width:11px;height:11px;flex:0 0 11px;margin-left:6px;border-radius:50%;vertical-align:middle}" +
        ".laya-live-state--active{border:1.5px solid rgba(255,255,255,.2);border-top-color:#f97316;animation:laya-live-spin .72s linear infinite}" +
        ".laya-live-state--failed{background:#ef4444;box-shadow:0 0 0 2px rgba(239,68,68,.25)}" +
        "@keyframes laya-live-spin{to{transform:rotate(360deg)}}";
    try {
        var s = document.createElement("style");
        s.id = "laya-thread-live-style";
        s.textContent = css;
        (document.head || document.documentElement).appendChild(s);
    } catch (e) { /* ignore */ }

    function badgeHtml(threadId) {
        var r = threads.get(Number(threadId));
        if (!r) return "";
        if (r.state === "active") return '<span class="laya-live-state laya-live-state--active" role="status" aria-label="Laya submission in progress" title="Laya submission in progress"></span>';
        if (r.state === "failed") return '<span class="laya-live-state laya-live-state--failed" role="status" aria-label="Laya submission failed" title="' +
            String(r.error).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }) + '"></span>';
        return "";
    }

    window.DecisionsLayaThreads = {
        get: function (id) { return threads.get(Number(id)) || null; },
        all: function () { return Array.from(threads.values()); },
        isActive: function (id) { var r = threads.get(Number(id)); return Boolean(r && r.state === "active"); },
        isFailed: function (id) { var r = threads.get(Number(id)); return Boolean(r && r.state === "failed"); },
        badgeHtml: badgeHtml,
        _handle: handle, // test hook: feed a raw ORCHESTRATION_EVENT payload
    };
})();
