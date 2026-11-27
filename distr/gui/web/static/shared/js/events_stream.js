/**
 * DecisionsAI — EventSource client for /api/events/stream (R22).
 * Dispatches window CustomEvent "decisionsai-event" with detail { type, data }.
 */
(function () {
    function getMetaToken() {
        var m = document.querySelector('meta[name="decisionsai-internal-api-token"]');
        return m && m.content ? String(m.content).trim() : "";
    }

    function getClientId() {
        try {
            var k = "decisionsai_sse_client_id";
            var id = sessionStorage.getItem(k);
            if (!id) {
                id = typeof crypto !== "undefined" && crypto.randomUUID
                    ? crypto.randomUUID()
                    : String(Math.random()).slice(2) + String(Date.now());
                sessionStorage.setItem(k, id);
            }
            return id;
        } catch (e) {
            return "fallback";
        }
    }

    var token = getMetaToken();
    if (!token) {
        return;
    }

    var reconnectMs = 1000;
    var maxReconnectMs = 30000;
    var es = null;

    function retryLater() {
        setTimeout(connect, reconnectMs);
        reconnectMs = Math.min(maxReconnectMs, reconnectMs * 2);
    }

    function connect() {
        if (es) {
            try {
                es.close();
            } catch (e) { /* ignore */ }
            es = null;
        }
        // EventSource cannot send headers: swap the token (sent as a header)
        // for a one-use ticket so the token never appears in the URL.
        fetch("/api/events/stream-ticket", {
            method: "POST",
            headers: { "X-DecisionsAI-Internal-Token": token },
            credentials: "same-origin"
        }).then(function (r) {
            if (r.status === 404) {
                // Server started before stream tickets existed: legacy query auth until restart.
                return { legacy: true };
            }
            return r.ok ? r.json() : null;
        }).then(function (body) {
            if (body && body.legacy) {
                openStream("internal_token=" + encodeURIComponent(token));
                return;
            }
            if (!body || !body.ticket) {
                retryLater();
                return;
            }
            openStream("ticket=" + encodeURIComponent(body.ticket));
        }).catch(retryLater);
    }

    function openStream(auth) {
        var url =
            "/api/events/stream?" +
            auth +
            "&client_id=" +
            encodeURIComponent(getClientId());
        es = new EventSource(url);
        es.addEventListener("ready", function () {
            reconnectMs = 1000;
        });
        es.addEventListener("app", function (ev) {
            try {
                var o = JSON.parse(ev.data);
                window.dispatchEvent(new CustomEvent("decisionsai-event", { detail: o }));
            } catch (e) { /* ignore */ }
        });
        es.onerror = function () {
            try {
                es.close();
            } catch (e2) { /* ignore */ }
            es = null;
            retryLater();
        };
    }

    connect();
    window.DecisionsAISSE = { reconnect: connect, close: function () {
        if (es) {
            try {
                es.close();
            } catch (e) { /* ignore */ }
            es = null;
        }
    } };
})();
