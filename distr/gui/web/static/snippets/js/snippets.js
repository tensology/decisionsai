/**
 * Snippets page: intentionally simple.
 * A snippet is just text to paste, plus an optional hotkey.
 */
(function() {
  var snippets = [];
  var apiFetch = window.DecisionsAPI.fetch;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showSnackbar(message, type) {
    window.DecisionsAPI.snackbar(message, type, { id: "snippets-snackbar" });
  }

  function formatHotkey(combo) {
    if (!combo) return "";
    return String(combo).split("+").map(function(part) {
      if (part === "cmd") return "Cmd";
      if (part === "ctrl") return "Ctrl";
      if (part === "alt") return "Alt";
      if (part === "shift") return "Shift";
      if (part === "space") return "Space";
      if (part === "escape") return "Esc";
      return part.length === 1 ? part.toUpperCase() : part.charAt(0).toUpperCase() + part.slice(1);
    }).join("+");
  }

  function normalizeKey(key) {
    var value = String(key || "").toLowerCase();
    if (value === " ") return "space";
    if (value === "esc") return "escape";
    if (value.indexOf("arrow") === 0) return value.replace("arrow", "");
    return value;
  }

  function normalizeHotkey(event) {
    var parts = [];
    if (event.metaKey) parts.push("cmd");
    if (event.ctrlKey) parts.push("ctrl");
    if (event.altKey) parts.push("alt");
    if (event.shiftKey) parts.push("shift");
    var key = normalizeKey(event.key);
    if (key && ["cmd", "ctrl", "alt", "shift", "meta", "control"].indexOf(key) === -1) parts.push(key);
    return parts.join("+");
  }

  function snippetTitle(index, text) {
    var firstLine = String(text || "").trim().split(/\n/)[0].trim();
    return firstLine ? firstLine.slice(0, 80) : "Snippet " + (index + 1);
  }

  function render() {
    var root = document.getElementById("snippets-list");
    if (!root) return;
    if (!snippets.length) {
      root.innerHTML = "<div class=\"p-6 text-sm text-gray-400\">No snippets yet.</div>";
      return;
    }
    root.innerHTML = snippets.map(function(snippet, index) {
      return "<div class=\"snippet-row grid gap-3 px-6 py-4 border-b border-white/10 items-start\" style=\"grid-template-columns:56px minmax(0,1fr) 220px 96px\" data-id=\"" + snippet.id + "\">" +
        "<div class=\"snippet-num w-8 h-8 rounded-full bg-white/10 border border-white/10 flex items-center justify-center text-sm font-semibold text-gray-300\">" + (index + 1) + "</div>" +
        "<textarea class=\"snippet-text w-full min-h-[54px] px-3 py-2 bg-[#152054] border border-white/20 rounded text-white text-sm resize-y focus:border-[#f97316] focus:outline-none\" rows=\"2\" placeholder=\"Snippet text...\">" + esc(snippet.description || "") + "</textarea>" +
        "<div class=\"space-y-2\">" +
          "<button type=\"button\" class=\"snippet-hotkey w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-left text-sm " + (snippet.remote_hotkey ? "text-[#93c5fd]" : "text-gray-500") + "\" data-hotkey=\"" + esc(snippet.remote_hotkey || "") + "\">" + (snippet.remote_hotkey ? esc(formatHotkey(snippet.remote_hotkey)) : "Optional") + "</button>" +
          (snippet.remote_hotkey ? "<button type=\"button\" class=\"snippet-clear text-xs text-red-400 hover:text-red-300\">Clear</button>" : "") +
        "</div>" +
        "<div class=\"flex gap-2 justify-end\">" +
          "<button type=\"button\" class=\"snippet-save px-3 py-2 rounded bg-[#f97316] text-white hover:bg-[#ea580c] text-sm\">Save</button>" +
          "<button type=\"button\" class=\"snippet-delete px-3 py-2 rounded border border-red-500/40 text-red-400 hover:bg-red-500/20 text-sm\">Delete</button>" +
        "</div>" +
      "</div>";
    }).join("");
    bindRows();
  }

  function bindRows() {
    document.querySelectorAll(".snippet-row").forEach(function(row) {
      var id = parseInt(row.getAttribute("data-id"), 10);
      var hotkeyBtn = row.querySelector(".snippet-hotkey");
      var clearBtn = row.querySelector(".snippet-clear");
      var saveBtn = row.querySelector(".snippet-save");
      var deleteBtn = row.querySelector(".snippet-delete");

      if (hotkeyBtn) {
        hotkeyBtn.addEventListener("click", function() {
          hotkeyBtn.focus();
          hotkeyBtn.textContent = "Press keys...";
          hotkeyBtn.classList.remove("text-gray-500");
          hotkeyBtn.classList.add("text-[#93c5fd]");
        });
        hotkeyBtn.addEventListener("keydown", function(e) {
          e.preventDefault();
          var combo = normalizeHotkey(e);
          if (!combo) return;
          hotkeyBtn.setAttribute("data-hotkey", combo);
          hotkeyBtn.textContent = formatHotkey(combo);
          hotkeyBtn.blur();
        });
      }
      if (clearBtn) {
        clearBtn.addEventListener("click", function() {
          hotkeyBtn.setAttribute("data-hotkey", "");
          hotkeyBtn.textContent = "Optional";
          hotkeyBtn.classList.remove("text-[#93c5fd]");
          hotkeyBtn.classList.add("text-gray-500");
          clearBtn.remove();
        });
      }
      if (saveBtn) saveBtn.addEventListener("click", function() { saveSnippet(row, id); });
      if (deleteBtn) deleteBtn.addEventListener("click", function() { deleteSnippet(id); });
    });
  }

  function loadSnippets() {
    var root = document.getElementById("snippets-list");
    if (root) root.innerHTML = "<div class=\"p-6 text-sm text-gray-400\">Loading snippets...</div>";
    return apiFetch("/api/snippets")
      .then(function(data) {
        snippets = Array.isArray(data) ? data : [];
        render();
      })
      .catch(function(e) {
        if (root) root.innerHTML = "<div class=\"p-6 text-sm text-red-400\">Could not load snippets.</div>";
        showSnackbar(e.message || "Could not load snippets", "error");
      });
  }

  function saveSnippet(row, id) {
    var text = (row.querySelector(".snippet-text").value || "").trim();
    var hotkey = row.querySelector(".snippet-hotkey").getAttribute("data-hotkey") || "";
    var index = snippets.findIndex(function(s) { return s.id === id; });
    apiFetch("/api/snippets/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: snippetTitle(index, text),
        description: text,
        additional_trigger_words: "[]",
        remote_hotkey: hotkey
      })
    }).then(function() {
      showSnackbar("Snippet saved", "success");
      loadSnippets();
    }).catch(function(e) {
      showSnackbar(e.message || "Could not save snippet", "error");
    });
  }

  function addSnippet() {
    var next = snippets.length + 1;
    apiFetch("/api/snippets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: "Snippet " + next,
        description: "",
        additional_trigger_words: "[]",
        remote_hotkey: ""
      })
    }).then(function() {
      showSnackbar("Snippet added", "success");
      loadSnippets();
    }).catch(function(e) {
      showSnackbar(e.message || "Could not add snippet", "error");
    });
  }

  function deleteSnippet(id) {
    if (!confirm("Delete this snippet?")) return;
    apiFetch("/api/snippets/" + id, { method: "DELETE" })
      .then(function() {
        showSnackbar("Snippet deleted", "success");
        loadSnippets();
      })
      .catch(function(e) { showSnackbar(e.message || "Could not delete snippet", "error"); });
  }

  var addBtn = document.getElementById("snippet-add");
  if (addBtn) addBtn.addEventListener("click", addSnippet);
  loadSnippets();
})();
