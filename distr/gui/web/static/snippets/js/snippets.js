/**
 * Snippets page: intentionally simple.
 * A snippet is just text to paste, plus an optional hotkey.
 */
(function() {
  var snippets = [];
  var currentSnippetId = null;
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

  var MOD_ORDER = ["ctrl", "alt", "shift", "cmd"];
  var MOD_LABELS = { ctrl: "⌃", control: "⌃", alt: "⌥", option: "⌥", shift: "⇧", cmd: "⌘", command: "⌘" };
  var KEY_LABELS = { space: "Space", escape: "Esc", left: "←", right: "→", up: "↑", down: "↓" };
  var DEFAULT_HOTKEYS = Array.from({ length: 12 }, function(_, idx) {
    return "ctrl+shift+f" + (idx + 1);
  });

  function normalizePart(part) {
    var value = String(part || "").toLowerCase();
    if (value === "control") return "ctrl";
    if (value === "option") return "alt";
    if (value === "command") return "cmd";
    return value;
  }

  function hotkeyParts(combo) {
    var raw = String(combo || "").split("+").map(normalizePart).filter(Boolean);
    var mods = MOD_ORDER.filter(function(mod) { return raw.indexOf(mod) !== -1; });
    var keys = raw.filter(function(part) { return MOD_ORDER.indexOf(part) === -1; });
    return mods.concat(keys.slice(-1));
  }

  function hotkeyLabel(part) {
    part = normalizePart(part);
    if (MOD_LABELS[part]) return MOD_LABELS[part];
    if (KEY_LABELS[part]) return KEY_LABELS[part];
    return part.length === 1 ? part.toUpperCase() : part.charAt(0).toUpperCase() + part.slice(1);
  }

  function formatHotkeyBadges(combo) {
    var parts = hotkeyParts(combo);
    if (!parts.length) return "<span class=\"hotkey-placeholder\">No hotkey</span>";
    return parts.map(function(part) {
      return "<span class=\"key-badge\">" + esc(hotkeyLabel(part)) + "</span>";
    }).join("");
  }

  function normalizeKey(event) {
    var value = String((event && event.key) || event || "").toLowerCase();
    var code = String((event && event.code) || "");
    if (value === " ") return "space";
    if (value === "esc") return "escape";
    if (value.indexOf("arrow") === 0) return value.replace("arrow", "");
    if (/^f([1-9]|1[0-2])$/.test(value)) return value;
    if (/^Digit[0-9]$/.test(code)) return code.replace("Digit", "");
    if (/^Key[A-Z]$/.test(code)) return code.replace("Key", "").toLowerCase();
    return value;
  }

  function normalizeHotkey(event) {
    var parts = [];
    if (event.metaKey) parts.push("cmd");
    if (event.ctrlKey) parts.push("ctrl");
    if (event.altKey) parts.push("alt");
    if (event.shiftKey) parts.push("shift");
    var key = normalizeKey(event);
    if (!key || ["cmd", "ctrl", "alt", "shift", "meta", "control", "option"].indexOf(key) !== -1) {
      return "";
    }
    if (key && ["cmd", "ctrl", "alt", "shift", "meta", "control"].indexOf(key) === -1) parts.push(key);
    return parts.join("+");
  }

  function previewHotkey(event) {
    var parts = [];
    if (event.metaKey) parts.push("cmd");
    if (event.ctrlKey) parts.push("ctrl");
    if (event.altKey) parts.push("alt");
    if (event.shiftKey) parts.push("shift");
    var key = normalizeKey(event);
    if (key && ["cmd", "ctrl", "alt", "shift", "meta", "control", "option"].indexOf(key) === -1) parts.push(key);
    return parts.join("+");
  }

  function getSnippetText(snippet) {
    return String((snippet && (snippet.text || snippet.description)) || "");
  }

  function nextDefaultHotkey() {
    var used = new Set(snippets.map(function(snippet) {
      return String(snippet.remote_hotkey || "").toLowerCase();
    }).filter(Boolean));
    for (var i = 0; i < DEFAULT_HOTKEYS.length; i += 1) {
      if (!used.has(DEFAULT_HOTKEYS[i])) return DEFAULT_HOTKEYS[i];
    }
    return "";
  }

  function trashIcon() {
    return "<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M3 6h18\"></path><path d=\"M8 6V4h8v2\"></path><path d=\"M19 6l-1 14H6L5 6\"></path><path d=\"M10 11v5\"></path><path d=\"M14 11v5\"></path></svg>";
  }

  function render() {
    var root = document.getElementById("snippets-list");
    if (!root) return;
    if (!snippets.length) {
      root.innerHTML = "<div class=\"p-6 text-sm text-gray-400\">No snippets yet.</div>";
      return;
    }
    root.innerHTML = snippets.map(function(snippet, index) {
      var hasHotkey = !!snippet.remote_hotkey;
      return "<div class=\"snippet-row\" data-id=\"" + snippet.id + "\" tabindex=\"0\" role=\"option\" aria-label=\"Snippet " + (index + 1) + "\">" +
        "<div class=\"snippet-num\">#" + (index + 1) + "</div>" +
        "<input class=\"snippet-text\" type=\"text\" value=\"" + esc(getSnippetText(snippet)) + "\" placeholder=\"Snippet text...\" aria-label=\"Snippet text\">" +
        "<button type=\"button\" class=\"snippet-hotkey" + (hasHotkey ? "" : " is-empty") + "\" data-hotkey=\"" + esc(snippet.remote_hotkey || "") + "\" title=\"Click then press a hotkey\">" + formatHotkeyBadges(snippet.remote_hotkey || "") + "</button>" +
        "<button type=\"button\" class=\"snippet-save\">Save</button>" +
        "<button type=\"button\" class=\"snippet-delete\" title=\"Delete snippet\" aria-label=\"Delete snippet\">" + trashIcon() + "</button>" +
      "</div>";
    }).join("");
    bindRows();
  }

  function bindRows() {
    document.querySelectorAll(".snippet-row").forEach(function(row) {
      var id = parseInt(row.getAttribute("data-id"), 10);
      var hotkeyBtn = row.querySelector(".snippet-hotkey");
      var saveBtn = row.querySelector(".snippet-save");
      var deleteBtn = row.querySelector(".snippet-delete");

      if (hotkeyBtn) {
        hotkeyBtn.addEventListener("click", function() {
          hotkeyBtn.focus();
          hotkeyBtn.innerHTML = "<span class=\"hotkey-capturing-text\">Press keys…</span>";
          hotkeyBtn.classList.add("capturing");
          hotkeyBtn.classList.remove("is-empty");
        });
        hotkeyBtn.addEventListener("keydown", function(e) {
          e.preventDefault();
          e.stopPropagation();
          if (e.key === "Backspace" || e.key === "Delete") {
            hotkeyBtn.setAttribute("data-hotkey", "");
            hotkeyBtn.innerHTML = formatHotkeyBadges("");
            hotkeyBtn.classList.add("is-empty");
            hotkeyBtn.classList.remove("capturing");
            return;
          }
          var preview = previewHotkey(e);
          if (preview) hotkeyBtn.innerHTML = formatHotkeyBadges(preview);
          var combo = normalizeHotkey(e);
          if (!combo) return;
          hotkeyBtn.setAttribute("data-hotkey", combo);
          hotkeyBtn.innerHTML = formatHotkeyBadges(combo);
          hotkeyBtn.classList.remove("is-empty");
          hotkeyBtn.classList.remove("capturing");
          hotkeyBtn.blur();
        });
        hotkeyBtn.addEventListener("blur", function() {
          hotkeyBtn.classList.remove("capturing");
          hotkeyBtn.innerHTML = formatHotkeyBadges(hotkeyBtn.getAttribute("data-hotkey") || "");
          hotkeyBtn.classList.toggle("is-empty", !hotkeyBtn.getAttribute("data-hotkey"));
        });
      }
      row.addEventListener("focusin", function() { currentSnippetId = id; });
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
    var textEl = row.querySelector(".snippet-text");
    var text = ((textEl && textEl.value) || "").trim();
    var hotkey = row.querySelector(".snippet-hotkey").getAttribute("data-hotkey") || "";
    apiFetch("/api/snippets/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        description: text,
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
    var defaultHotkey = nextDefaultHotkey();
    apiFetch("/api/snippets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: "",
        description: "",
        remote_hotkey: defaultHotkey
      })
    }).then(function() {
      showSnackbar("Snippet added", "success");
      loadSnippets();
    }).catch(function(e) {
      showSnackbar(e.message || "Could not add snippet", "error");
    });
  }

  function deleteSnippet(id) {
    window.DecisionsAPI.confirm({
      title: "Delete snippet",
      message: "Delete this snippet? This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
      onConfirm: function() {
        apiFetch("/api/snippets/" + id, { method: "DELETE" })
          .then(function() {
            showSnackbar("Snippet deleted", "success");
            loadSnippets();
          })
          .catch(function(e) { showSnackbar(e.message || "Could not delete snippet", "error"); });
      }
    });
  }

  function bindSnippetListKeyboard() {
    if (!window.DecisionsListKeyboard) return;
    window.DecisionsListKeyboard.bind({
      listEl: "snippets-list",
      namespace: "snippets",
      rowSelector: ".snippet-row",
      getRowId: function(row) { return parseInt(row.getAttribute("data-id"), 10); },
      getSelectedId: function() { return currentSnippetId; },
      onEnter: function(id, row) {
        var textEl = row.querySelector(".snippet-text");
        if (textEl) textEl.focus();
      },
      onDelete: function(id) { deleteSnippet(id); },
      pageGuard: function() { return !!document.getElementById("snippets-list"); },
      ignoreEnterFrom: ".snippet-hotkey.capturing",
    });
  }

  var addBtn = document.getElementById("snippet-add");
  if (addBtn) addBtn.addEventListener("click", addSnippet);
  bindSnippetListKeyboard();
  loadSnippets();
})();
