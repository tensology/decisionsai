// Logs tab: poll when visible; Reload refreshes immediately.

var LOGS_POLL_INTERVAL_MS = 2500;
var _logsLastRaw = "";
var _logsPollTimer = null;
var LOGS_API_URL = "/api/logs?tail_lines=500";
var _logsTailCopyLines = 20;

function formatShortLogPath(path) {
    if (!path) return "";
    var normalized = String(path).replace(/\\/g, "/");
    var match = normalized.match(/\/db\/log\/decisions[^/\\]*/i);
    if (match && match[0]) {
        return "." + match[0];
    }
    var segments = normalized.split("/");
    if (segments.length >= 3) {
        return "./" + segments.slice(-3).join("/");
    }
    return normalized;
}

function isLogsTabVisible() {
    var panel = document.getElementById("tab-logs");
    if (!panel) return false;
    var disp = panel.style.getPropertyValue("display");
    return panel.classList.contains("active-tab-panel") || disp === "block";
}

/**
 * Colorize a single log line.
 * Format: "TIMESTAMP - MODULE - LEVEL - MESSAGE"
 * Shows short time (HH:MM:SS), hides module for cleaner look,
 * and highlights key events (transcriptions, TTS, LLM).
 */
function colorizeLine(line) {
    if (!line || !line.trim()) return "";

    // Escape HTML entities
    var safe = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // Match: "2025-01-15 10:30:45,123 - module.name - LEVEL - message..."
    var m = safe.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})[,\.]\d+\s*-\s*([\w\.]+)\s*-\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-\s*(.*)/);
    if (!m) {
        return '<span class="log-plain">' + safe + '</span>';
    }

    var time = m[2];
    var mod = m[3];
    var level = m[4];
    var msg = m[5];

    // Skip DEBUG lines entirely for cleaner output
    if (level === "DEBUG") return "";

    var levelClass = "log-lvl-" + level.toLowerCase();
    var msgClass = "log-msg log-msg-" + level.toLowerCase();

    // Highlight key events with special styling
    if (msg.match(/TRANSCRIPTION \[|LLM Request|PICKED UP:/i)) {
        msgClass += " log-highlight-user";
    } else if (msg.match(/^TTS:|Welcome message|🔧 Tool:/i)) {
        msgClass += " log-highlight-tts";
    } else if (msg.match(/interrupted|Interrupt|WATCHDOG/i)) {
        msgClass += " log-highlight-interrupt";
    } else if (msg.match(/\[LLM\] OUTPUT:/i)) {
        msgClass += " log-highlight-tts";
    }

    // Short module name (last segment only)
    var shortMod = mod.split(".").pop();

    return '<span class="log-ts">' + time + '</span>' +
           ' <span class="log-mod">' + shortMod + '</span>' +
           ' <span class="' + levelClass + '">' + level.charAt(0) + '</span>' +
           ' <span class="' + msgClass + '">' + msg + '</span>';
}

function colorizeLogText(text) {
    var lines = text.split("\n");
    var html = [];
    for (var i = 0; i < lines.length; i++) {
        var colored = colorizeLine(lines[i]);
        if (colored) html.push(colored);
    }
    return html.join("\n");
}

function loadLogs() {
    var contentEl = document.getElementById("logs_content");
    var pathEl = document.getElementById("logs_path");
    if (!contentEl) return;
    var scrollTop = contentEl.scrollTop;
    var scrollHeight = contentEl.scrollHeight;
    var clientHeight = contentEl.clientHeight;
    var isInitialLoad = !contentEl.textContent || contentEl.textContent === "Loading…";
    if (isInitialLoad) contentEl.textContent = "Loading…";
    var url = window.location.pathname.replace(/\/?$/, "").indexOf("/settings") !== -1
        ? "/api/logs?tail_lines=500"
        : LOGS_API_URL;
    fetch(url)
        .then(function(r) {
            if (!r.ok) return r.text().then(function(t) { throw new Error("HTTP " + r.status + (t ? ": " + t.slice(0, 80) : "")); });
            return r.json();
        })
        .then(function(data) {
            var text = data.content != null ? data.content : "";
            _logsLastRaw = String(text || "");
            if (text === "") {
                contentEl.textContent = "(No log content yet.)";
            } else {
                text = text.split("\n").reverse().join("\n");
                contentEl.innerHTML = colorizeLogText(text);
            }
            if (pathEl) pathEl.textContent = data.path ? formatShortLogPath(data.path) : "";
            requestAnimationFrame(function() {
                if (!isInitialLoad && scrollHeight > clientHeight) {
                    var maxScroll = contentEl.scrollHeight - contentEl.clientHeight;
                    contentEl.scrollTop = Math.min(scrollTop, maxScroll);
                }
            });
        })
        .catch(function(err) {
            contentEl.textContent = "Failed to load logs: " + err.message;
            if (pathEl) pathEl.textContent = "URL: " + url;
        });
}

function copyLastLogs() {
    var copyBtn = document.getElementById("logs-copy-btn");
    var raw = _logsLastRaw || "";
    var lines = raw ? raw.split("\n") : [];
    var last = lines.slice(Math.max(0, lines.length - _logsTailCopyLines)).join("\n").trim();
    if (!last) {
        if (copyBtn) {
            copyBtn.disabled = true;
            setTimeout(function () { copyBtn.disabled = false; }, 2000);
        }
        if (typeof window.showNotification === "function") {
            window.showNotification("No log lines available to copy.", "warning");
        }
        return Promise.resolve(false);
    }
    return navigator.clipboard.writeText(last).then(function () {
        if (typeof window.showNotification === "function") {
            window.showNotification("Copied last " + _logsTailCopyLines + " lines", "success");
        }
        return true;
    }).catch(function () {
        var ta = document.createElement("textarea");
        ta.value = last;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {
            document.execCommand("copy");
            if (typeof window.showNotification === "function") {
                window.showNotification("Copied last " + _logsTailCopyLines + " lines", "success");
            }
            return true;
        } finally {
            document.body.removeChild(ta);
        }
    });
}

function stopLogsPolling() {
    if (_logsPollTimer) {
        clearInterval(_logsPollTimer);
        _logsPollTimer = null;
    }
}

function startLogsPolling() {
    if (_logsPollTimer) return;
    loadLogs();
    _logsPollTimer = setInterval(function() {
        if (isLogsTabVisible()) loadLogs();
    }, LOGS_POLL_INTERVAL_MS);
}

function initLogsTab() {
    var panel = document.getElementById("tab-logs");
    if (!panel) return;
    function checkVisibility() {
        if (isLogsTabVisible()) startLogsPolling();
        else stopLogsPolling();
    }
    var copyBtn = document.getElementById("logs-copy-btn");
    if (copyBtn) {
        copyBtn.addEventListener("click", copyLastLogs);
    }
    window.addEventListener("hashchange", checkVisibility);
    var origSwitchTab = window.switchTab;
    if (typeof origSwitchTab === "function") {
        window.switchTab = function(tabName) {
            origSwitchTab(tabName);
            if (tabName === "logs") {
                setTimeout(function() { startLogsPolling(); }, 0);
            } else {
                stopLogsPolling();
            }
        };
    }
    if (isLogsTabVisible()) setTimeout(function() { startLogsPolling(); }, 0);
    setTimeout(checkVisibility, 100);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLogsTab);
} else {
    initLogsTab();
}

window.loadLogs = loadLogs;
