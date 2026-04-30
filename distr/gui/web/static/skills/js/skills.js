/**
 * Skills page — browse, search, view, and push skills to projects
 */
(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────
  var allSkills = [];
  var filteredSkills = [];
  var searchQuery = "";
  var PAGE_SIZE = 24;
  var currentPage = 0;
  var currentSkillId = null;
  var skillsOverviewAudio = null;
  var skillsOverviewObjectUrl = null;
  var skillsOverviewLoading = false;
  var skillOverviewAbort = null;
  var skillsOverviewTriggerBtn = null;

  // ── Tag inference ────────────────────────────────────────────────
  var TAG_RULES = {
    "Planning": ["scope", "epic", "story", "priorit", "research", "opportunity"],
    "Documentation": ["doc", "creator", "writer", "reference", "presentation"],
    "Execution": ["execut", "pipeline", "coordinat", "task-creator", "task-executor", "validator"],
    "Quality": ["quality", "test", "regression", "checker", "planner"],
    "Auditing": ["auditor", "audit", "security", "dead-code", "pattern", "dependency"],
    "Bootstrap": ["bootstrap", "generat", "setup", "docker", "cicd", "linter", "healthcheck"],
    "Performance": ["performance", "optim", "upgrad", "moderniz", "bundle"],
    "Creative": ["art", "design", "canvas", "pptx", "docx", "xlsx", "pdf", "brand", "theme", "web-artifacts"],
    "Dev Tools": ["mcp", "skill-creator", "webapp-test"],
    "Superpowers": ["brainstorm", "debugging", "git-worktree", "code-review", "subagent"],
  };

  function inferTags(skill) {
    var text = ((skill.name || "") + " " + (skill.description || "") + " " + (skill.id || "")).toLowerCase();
    var tags = [];
    for (var tag in TAG_RULES) {
      var kws = TAG_RULES[tag];
      for (var i = 0; i < kws.length; i++) {
        if (text.indexOf(kws[i]) >= 0) { tags.push(tag); break; }
      }
    }
    return tags.length > 0 ? tags : ["Other"];
  }

  // ── Escape HTML ──────────────────────────────────────────────────
  function esc(text) {
    var el = document.createElement("span");
    el.textContent = text;
    return el.innerHTML;
  }

  // ── Render markdown-ish content ──────────────────────────────────
  function renderContent(raw) {
    // Strip frontmatter
    if (raw.startsWith("---")) {
      var end = raw.indexOf("---", 3);
      if (end > 0) raw = raw.substring(end + 3).trim();
    }
    var html = esc(raw);
    // Bold: **text**
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic: *text*
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
    // Code: `text`
    html = html.replace(/`([^`]+)`/g, '<code class="bg-white/5 px-1 py-0.5 rounded text-[#fb923c] text-xs">$1</code>');
    // Headings
    html = html.replace(/^### (.+)$/gm, '<h4 class="text-sm font-semibold text-[#fb923c] mt-4 mb-1">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 class="text-sm font-semibold text-white mt-4 mb-1">$1</h3>');
    // Lists: - item or * item
    html = html.replace(/^[\-\*] (.+)$/gm, '<div class="flex gap-2 ml-2"><span class="text-[#f97316]">•</span><span>$1</span></div>');
    // Numbered lists: 1. item
    html = html.replace(/^(\d+)\. (.+)$/gm, '<div class="flex gap-2 ml-2"><span class="text-[#f97316] min-w-[1.5rem]">$1.</span><span>$2</span></div>');
    // Horizontal rules
    html = html.replace(/^---$/gm, '<hr class="border-white/10 my-3">');
    // Line breaks → paragraphs
    html = html.replace(/\n{2,}/g, "</p><p class='mb-2'>");
    if (!html.startsWith("<")) html = "<p class='mb-2'>" + html + "</p>";
    return html;
  }

  // ── Copy skill to clipboard ──────────────────────────────────────
  function copySkillToClipboard(skillId) {
    // First check if skill has content cached
    var skill = allSkills.find(function (s) { return s.id === skillId; });
    if (skill && skill.content) {
      copyToClipboard(skill.content);
      return;
    }

    // Otherwise fetch the content from API
    fetch("/api/skills/" + encodeURIComponent(skillId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.content) {
          // Update skill with content for future use
          if (skill) skill.content = data.content;
          copyToClipboard(data.content);
        } else {
          showSnackbar("Failed to copy skill - no content", "error");
        }
      })
      .catch(function (err) {
        showSnackbar("Failed to copy skill: " + (err.message || err), "error");
      });
  }

  // ── Helper to copy to clipboard and show result ───────────────────
  function copyToClipboard(text) {
    navigator.clipboard.writeText(text)
      .then(function () {
        showSnackbar("Skill markdown copied!", "success");
      })
      .catch(function () {
        showSnackbar("Copy failed", "error");
      });
  }

  // ── Show snackbar notification ────────────────────────────────────
  function showSnackbar(message, type) {
    var snackbar = document.getElementById("skills-snackbar");
    if (!snackbar) {
      snackbar = document.createElement("div");
      snackbar.id = "skills-snackbar";
      snackbar.textContent = message;
      snackbar.style.cssText = "visibility:hidden; min-width:250px; background-color:#333; color:#fff; text-align:center; border-radius:8px; padding:12px 16px; position:fixed; z-index:1000; bottom:30px; left:50%; transform:translateX(-50%); font-size:14px; box-shadow:0 4px 12px rgba(0,0,0,0.3);";
      document.body.appendChild(snackbar);
    }
    snackbar.textContent = message;
    snackbar.style.backgroundColor = type === "success" ? "#10b981" : "#ef4444";
    snackbar.style.visibility = "visible";
    setTimeout(function () { snackbar.style.visibility = "hidden"; }, 2000);
  }

  // ── Init ─────────────────────────────────────────────────────────
  function init() {
    loadSkills();
    loadProjects();
    bindEvents();
  }

  // ── Load projects for push dropdown ──────────────────────────────
  function loadProjects() {
    fetch("/api/projects")
      .then(function (r) { return r.json(); })
      .then(function (projects) {
        var sel = document.getElementById("skill-modal-project");
        if (!sel || !projects) return;
        // Keep the placeholder option
        (Array.isArray(projects) ? projects : []).forEach(function (p) {
          var opt = document.createElement("option");
          opt.value = p.path || p.folder || p.id || "";
          opt.textContent = p.name || p.path || "Unknown";
          sel.appendChild(opt);
        });
      })
      .catch(function () {
        // Projects endpoint may not exist — fall back to text input
        var sel = document.getElementById("skill-modal-project");
        if (sel) {
          sel.outerHTML = '<input type="text" id="skill-modal-project" placeholder="/path/to/project" class="w-full px-3 py-2 bg-[#152054] border border-white/20 rounded text-white placeholder-gray-500 focus:border-[#f97316] focus:outline-none text-sm">';
        }
      });
  }

  // ── Load skills from API ────────────────────────────────────────
  function loadSkills() {
    fetch("/api/skills")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        allSkills = (data || []).map(function (s) {
          s.tags = inferTags(s);
          return s;
        });
        applyFilters();
      })
      .catch(function (err) {
        document.getElementById("skills-grid").innerHTML =
          '<p class="text-sm text-red-400">Failed to load skills: ' + (err.message || err) + "</p>";
      });
  }

  // ── Filter & search ─────────────────────────────────────────────
  function applyFilters() {
    filteredSkills = allSkills.filter(function (s) {
      if (!searchQuery) return true;
      var q = searchQuery.toLowerCase();
      return (
        (s.name || "").toLowerCase().indexOf(q) >= 0 ||
        (s.description || "").toLowerCase().indexOf(q) >= 0 ||
        (s.id || "").toLowerCase().indexOf(q) >= 0 ||
        s.tags.some(function (t) { return t.toLowerCase().indexOf(q) >= 0; })
      );
    });
    currentPage = 0;
    renderGrid();
    renderPagination();
  }

  // ── Render grid ─────────────────────────────────────────────────
  function renderGrid() {
    var grid = document.getElementById("skills-grid");
    var start = currentPage * PAGE_SIZE;
    var page = filteredSkills.slice(start, start + PAGE_SIZE);

    if (page.length === 0) {
      grid.innerHTML = '<p class="text-sm text-gray-400 col-span-full">No skills match your search.</p>';
      return;
    }

    var html = "";
    page.forEach(function (skill) {
      var shortDesc = (skill.description || "No description");
      if (shortDesc.length > 120) shortDesc = shortDesc.substring(0, 120) + "…";

      var tagBadges = skill.tags.slice(0, 3).map(function (t) {
        return '<span class="inline-block px-1.5 py-0.5 text-[10px] font-medium rounded bg-[#f97316]/15 text-[#fb923c]">' + esc(t) + "</span>";
      }).join(" ");

      html += '<div class="skill-card bg-[#152054] rounded-lg border border-white/10 p-4 hover:border-[#f97316]/50 transition-colors cursor-pointer relative" data-id="' + esc(skill.id) + '">';
      html += '  <button type="button" class="copy-btn absolute top-3 right-3 p-1 text-gray-400 hover:text-[#fb923c] transition-colors" title="Copy skill markdown">' +
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>' +
              '</button>';
      html += '  <div class="flex flex-wrap gap-1 mb-2">' + tagBadges + "</div>";
      html += '  <h4 class="text-sm font-semibold text-white mb-1.5 leading-tight">' + esc(skill.name || skill.id) + "</h4>";
      html += '  <p class="text-xs text-gray-400 leading-relaxed">' + esc(shortDesc) + "</p>";
      html +=
        '  <div class="flex gap-2 mt-3">' +
        '    <button type="button" class="edit-skill-btn inline-flex items-center justify-center gap-1.5 py-2 px-2 rounded-md border border-white/20 bg-[#1a1f3a]/60 text-[11px] sm:text-xs font-medium text-gray-200 hover:border-[#f97316]/45 hover:bg-[#f97316]/10 hover:text-[#fb923c] transition-colors min-w-0" title="Edit this skill">' +
        '      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>' +
        '      <span class="truncate leading-tight">Edit</span>' +
        "    </button>" +
        '    <button type="button" class="push-project-btn flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-2 rounded-md border border-white/20 bg-[#1a1f3a]/60 text-[11px] sm:text-xs font-medium text-gray-200 hover:border-[#f97316]/45 hover:bg-[#f97316]/10 hover:text-[#fb923c] transition-colors min-w-0" title="Push skill to project">' +
        '      <svg class="w-4 h-4 shrink-0 text-[#fb923c]" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>' +
        '      <span class="truncate leading-tight">Push to project</span>' +
        "    </button>" +
        '    <button type="button" class="overview-read-btn flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-2 rounded-md border border-white/20 bg-[#1a1f3a]/60 text-[11px] sm:text-xs font-medium text-gray-200 hover:border-[#f97316]/45 hover:bg-[#f97316]/10 hover:text-[#fb923c] transition-colors min-w-0" title="Read this Overview">' +
        '      <svg class="w-4 h-4 shrink-0 text-[#fb923c]" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1l4.707-4.707A1 1 0 0112 5.586v12.828a1 1 0 01-1.707.707L6 15h-.586z"/></svg>' +
        '      <span class="truncate leading-tight">Read this Overview</span>' +
        "    </button>" +
        "  </div>";
      html += "</div>";
    });
    grid.innerHTML = html;

    grid.querySelectorAll(".skill-card").forEach(function (card) {
      var copyBtn = card.querySelector(".copy-btn");
      if (copyBtn) {
        copyBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          var skillId = card.getAttribute("data-id");
          copySkillToClipboard(skillId);
        });
      }
      var pushBtn = card.querySelector(".push-project-btn");
      if (pushBtn) {
        pushBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          var skillId = card.getAttribute("data-id");
          openSkillPushModal(skillId);
        });
      }
      var editBtn = card.querySelector(".edit-skill-btn");
      if (editBtn) {
        editBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          var skillId = card.getAttribute("data-id");
          openSkillEditor(skillId);
        });
      }
      var overviewBtn = card.querySelector(".overview-read-btn");
      if (overviewBtn) {
        overviewBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          tellMeAboutSkill(card.getAttribute("data-id"), overviewBtn);
        });
      }
      card.addEventListener("click", function () {
        openSkillDetail(card.getAttribute("data-id"));
      });
    });
  }

  // ── Pagination ──────────────────────────────────────────────────
  function renderPagination() {
    var total = filteredSkills.length;
    var totalPages = Math.ceil(total / PAGE_SIZE) || 1;
    var start = currentPage * PAGE_SIZE + 1;
    var end = Math.min((currentPage + 1) * PAGE_SIZE, total);

    document.getElementById("skills-page-info").textContent =
      total > 0 ? "Showing " + start + "–" + end + " of " + total : "No skills found";

    document.getElementById("skills-prev").disabled = currentPage === 0;
    document.getElementById("skills-next").disabled = currentPage >= totalPages - 1;
  }

  // ── Open skill push modal (for push button) ─────────────────────
  function openSkillPushModal(skillId) {
    openSkillDetail(skillId);
  }

  // ── Skill detail modal ──────────────────────────────────────────
  function openSkillDetail(skillId) {
    stopSkillsOverviewPlayback();
    currentSkillId = skillId;
    var skill = allSkills.find(function (s) { return s.id === skillId; });
    if (!skill) return;

    // Fill header
    document.getElementById("skill-modal-title").textContent = skill.name || skillId;
    document.getElementById("skill-modal-description").textContent = skill.description || "";
    document.getElementById("skill-modal-instructions").value = "";

    // Tags
    var tagsEl = document.getElementById("skill-modal-tags");
    tagsEl.innerHTML = skill.tags.map(function (t) {
      return '<span class="inline-block px-2 py-0.5 text-xs font-medium rounded-full bg-[#f97316]/15 text-[#fb923c]">' + esc(t) + "</span>";
    }).join("");

    // Set project dropdown to first project if available
    var projSel = document.getElementById("skill-modal-project");
    if (projSel && projSel.options.length > 1 && !projSel.value) {
      projSel.selectedIndex = 1;
    }

    // Clear status & AI overview block
    document.getElementById("skill-modal-push-status").textContent = "";
    var ovWrap = document.getElementById("skill-modal-overview-wrap");
    var ovEl = document.getElementById("skill-modal-overview");
    if (ovWrap) ovWrap.classList.add("hidden");
    if (ovEl) ovEl.textContent = "";

    // Loading state
    document.getElementById("skill-modal-content").innerHTML = '<p class="text-gray-500">Loading…</p>';

    // Show modal
    document.getElementById("skill-modal").classList.remove("hidden");

    // Fetch full content
    fetch("/api/skills/" + skillId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var content = data.content || "";
        document.getElementById("skill-modal-content").innerHTML = renderContent(content);
      })
      .catch(function () {
        document.getElementById("skill-modal-content").innerHTML = '<p class="text-red-400">Could not load skill content.</p>';
      });
  }

  function hideSkillsTtsChrome() {
    var spin = document.getElementById("skills-tts-spinner");
    var stopBtn = document.getElementById("skills-tts-stop");
    var playBtn = document.getElementById("skills-tts-play");
    if (spin) spin.classList.add("hidden");
    if (stopBtn) stopBtn.classList.add("hidden");
    if (playBtn) playBtn.classList.add("hidden");
  }

  /** After overview audio finishes naturally — keep MP3 blob for replay (Play button). */
  function onOverviewPlaybackEnded() {
    skillsOverviewLoading = false;
    skillsOverviewAudio = null;
    var stopBtn = document.getElementById("skills-tts-stop");
    var playBtn = document.getElementById("skills-tts-play");
    if (stopBtn) stopBtn.classList.add("hidden");
    if (playBtn && skillsOverviewObjectUrl) playBtn.classList.remove("hidden");
  }

  function attachOverviewEndedListener() {
    if (!skillsOverviewAudio) return;
    skillsOverviewAudio.addEventListener("ended", function onEnded() {
      skillsOverviewAudio.removeEventListener("ended", onEnded);
      onOverviewPlaybackEnded();
    });
  }

  function replaySkillsOverview() {
    if (!skillsOverviewObjectUrl) return;
    skillsOverviewAudio = new Audio(skillsOverviewObjectUrl);
    attachOverviewEndedListener();
    var playBtn = document.getElementById("skills-tts-play");
    var stopBtn = document.getElementById("skills-tts-stop");
    if (playBtn) playBtn.classList.add("hidden");
    if (stopBtn) stopBtn.classList.remove("hidden");
    skillsOverviewAudio.play().catch(function () {});
  }

  function stopSkillsOverviewPlayback() {
    if (skillOverviewAbort) {
      try {
        skillOverviewAbort.abort();
      } catch (_) {}
      skillOverviewAbort = null;
    }
    if (skillsOverviewAudio) {
      skillsOverviewAudio.pause();
      skillsOverviewAudio.currentTime = 0;
      skillsOverviewAudio = null;
    }
    if (skillsOverviewObjectUrl) {
      URL.revokeObjectURL(skillsOverviewObjectUrl);
      skillsOverviewObjectUrl = null;
    }
    if (skillsOverviewTriggerBtn) {
      skillsOverviewTriggerBtn.disabled = false;
      skillsOverviewTriggerBtn.classList.remove("opacity-50", "cursor-wait");
      skillsOverviewTriggerBtn = null;
    }
    skillsOverviewLoading = false;
    hideSkillsTtsChrome();
  }

  /** LLM spoken overview + TTS; spinner next to search until audio ready, then stop control. */
  function tellMeAboutSkill(skillId, triggerBtn) {
    if (!skillId || skillsOverviewLoading) return;
    stopSkillsOverviewPlayback();
    skillsOverviewLoading = true;
    skillsOverviewTriggerBtn = triggerBtn || null;
    skillOverviewAbort = new AbortController();

    var spinner = document.getElementById("skills-tts-spinner");
    var stopBtn = document.getElementById("skills-tts-stop");
    var playBtn = document.getElementById("skills-tts-play");
    if (spinner) spinner.classList.remove("hidden");
    if (stopBtn) stopBtn.classList.add("hidden");
    if (playBtn) playBtn.classList.add("hidden");

    if (skillsOverviewTriggerBtn) {
      skillsOverviewTriggerBtn.disabled = true;
      skillsOverviewTriggerBtn.classList.add("opacity-50", "cursor-wait");
    }

    fetch("/api/skills/" + encodeURIComponent(skillId) + "/spoken-overview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: skillOverviewAbort.signal,
    })
      .then(function (r) {
        if (!r.ok) {
          return r
            .json()
            .catch(function () {
              return {};
            })
            .then(function (j) {
              var d = j.detail;
              var msg =
                typeof d === "string"
                  ? d
                  : Array.isArray(d) && d[0] && d[0].msg
                    ? d[0].msg
                    : r.statusText || "Request failed";
              throw new Error(msg);
            });
        }
        return r.json();
      })
      .then(function (data) {
        if (spinner) spinner.classList.add("hidden");

        var modalEl = document.getElementById("skill-modal");
        var modalOpen = modalEl && !modalEl.classList.contains("hidden");
        if (modalOpen && currentSkillId === skillId) {
          var overviewEl = document.getElementById("skill-modal-overview");
          var wrap = document.getElementById("skill-modal-overview-wrap");
          if (overviewEl && data.overview) {
            overviewEl.textContent = data.overview;
            if (wrap) wrap.classList.remove("hidden");
          }
        }

        var b64 = data.audio_mp3_base64;
        if (!b64) throw new Error("No audio in response");

        var binStr = atob(b64);
        var bytes = new Uint8Array(binStr.length);
        for (var i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
        var blob = new Blob([bytes], { type: "audio/mpeg" });
        skillsOverviewObjectUrl = URL.createObjectURL(blob);
        skillsOverviewAudio = new Audio(skillsOverviewObjectUrl);
        attachOverviewEndedListener();

        if (stopBtn) stopBtn.classList.remove("hidden");

        return skillsOverviewAudio.play().catch(function () {});
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        stopSkillsOverviewPlayback();
        showSnackbar((err && err.message) || "Could not generate overview", "error");
      })
      .then(function () {
        skillOverviewAbort = null;
      });
  }

  function closeSkillModal() {
    stopSkillsOverviewPlayback();
    document.getElementById("skill-modal").classList.add("hidden");
    currentSkillId = null;
  }

  // ── Push skill ──────────────────────────────────────────────────
  function pushSkill() {
    if (!currentSkillId) return;

    var projectEl = document.getElementById("skill-modal-project");
    var instructions = document.getElementById("skill-modal-instructions").value.trim();
    var statusEl = document.getElementById("skill-modal-push-status");

    // Get project path — from select or input
    var projectPath;
    if (projectEl.tagName === "SELECT") {
      projectPath = projectEl.value;
    } else {
      projectPath = projectEl.value || ".";
    }

    if (!projectPath) {
      statusEl.textContent = "Please select a project.";
      statusEl.className = "mt-2 text-xs text-red-400";
      return;
    }

    statusEl.textContent = "Pushing…";
    statusEl.className = "mt-2 text-xs text-gray-400";

    fetch("/api/skills/" + currentSkillId + "/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_path: projectPath, instructions: instructions })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.success) {
        statusEl.textContent = "✅ " + data.message;
        statusEl.className = "mt-2 text-xs text-green-400";
        if (window.DecisionsAPI && window.DecisionsAPI.snackbar) {
          window.DecisionsAPI.snackbar(data.message, "success");
        }
      } else {
        statusEl.textContent = "❌ " + (data.detail || "Push failed");
        statusEl.className = "mt-2 text-xs text-red-400";
      }
    })
    .catch(function (err) {
      statusEl.textContent = "❌ Push failed: " + err.message;
      statusEl.className = "mt-2 text-xs text-red-400";
    });
  }

  // ── Skill Editor ────────────────────────────────────────────────
  var editingSkillId = null;

  function openSkillEditor(skillId) {
    stopSkillsOverviewPlayback();
    editingSkillId = skillId || null;

    var title = document.getElementById("skill-editor-title");
    var nameField = document.getElementById("skill-editor-name");
    var descField = document.getElementById("skill-editor-description");
    var contentField = document.getElementById("skill-editor-content");
    var deleteBtn = document.getElementById("skill-editor-delete");
    var statusEl = document.getElementById("skill-editor-status");

    statusEl.textContent = "";

    if (skillId) {
      // Edit mode
      title.textContent = "Edit Skill";
      deleteBtn.classList.remove("hidden");
      var skill = allSkills.find(function (s) { return s.id === skillId; });
      if (skill) {
        nameField.value = skill.name || "";
        descField.value = skill.description || "";
      }
      // Fetch content
      fetch("/api/skills/" + skillId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var raw = data.content || "";
          // Strip frontmatter for editing
          if (raw.startsWith("---")) {
            var end = raw.indexOf("---", 3);
            if (end > 0) raw = raw.substring(end + 3).trim();
          }
          contentField.value = raw;
        })
        .catch(function () {
          contentField.value = "";
        });
    } else {
      // Create mode
      title.textContent = "Create Skill";
      deleteBtn.classList.add("hidden");
      nameField.value = "";
      descField.value = "";
      contentField.value = "";
    }

    document.getElementById("skill-editor-modal").classList.remove("hidden");
    nameField.focus();
  }

  function closeSkillEditor() {
    document.getElementById("skill-editor-modal").classList.add("hidden");
    editingSkillId = null;
  }

  function saveSkill() {
    var nameField = document.getElementById("skill-editor-name");
    var descField = document.getElementById("skill-editor-description");
    var contentField = document.getElementById("skill-editor-content");
    var statusEl = document.getElementById("skill-editor-status");
    var saveBtn = document.getElementById("skill-editor-save");

    var name = nameField.value.trim();
    var description = descField.value.trim();
    var content = contentField.value.trim();

    if (!name) {
      statusEl.textContent = "Name is required.";
      statusEl.className = "text-xs text-red-400";
      return;
    }
    if (!content) {
      statusEl.textContent = "Content is required.";
      statusEl.className = "text-xs text-red-400";
      return;
    }

    statusEl.textContent = editingSkillId ? "Saving…" : "Creating…";
    statusEl.className = "text-xs text-gray-400";
    saveBtn.disabled = true;
    saveBtn.classList.add("opacity-50");

    var url = editingSkillId
      ? "/api/skills/" + editingSkillId + "/save"
      : "/api/skills/create";
    var method = editingSkillId ? "PUT" : "POST";

    fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, description: description, content: content })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        saveBtn.disabled = false;
        saveBtn.classList.remove("opacity-50");
        if (data.success) {
          statusEl.textContent = "\u2705 " + data.message;
          statusEl.className = "text-xs text-green-400";
          // Reload skills to pick up new/edited skill
          setTimeout(function () {
            closeSkillEditor();
            loadSkills();
          }, 800);
        } else {
          statusEl.textContent = "\u274c " + (data.detail || "Save failed");
          statusEl.className = "text-xs text-red-400";
        }
      })
      .catch(function (err) {
        saveBtn.disabled = false;
        saveBtn.classList.remove("opacity-50");
        statusEl.textContent = "\u274c " + err.message;
        statusEl.className = "text-xs text-red-400";
      });
  }

  function deleteSkill() {
    if (!editingSkillId) return;
    var statusEl = document.getElementById("skill-editor-status");
    if (!confirm("Delete skill '" + editingSkillId + "'? This cannot be undone.")) return;

    statusEl.textContent = "Deleting…";
    statusEl.className = "text-xs text-gray-400";

    fetch("/api/skills/" + editingSkillId, { method: "DELETE" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          closeSkillEditor();
          loadSkills();
          showSnackbar(data.message, "success");
        } else {
          statusEl.textContent = "\u274c " + (data.detail || "Delete failed");
          statusEl.className = "text-xs text-red-400";
        }
      })
      .catch(function (err) {
        statusEl.textContent = "\u274c " + err.message;
        statusEl.className = "text-xs text-red-400";
      });
  }

  // ── Event bindings ───────────────────────────────────────────────
  function bindEvents() {
    // Create button
    var createBtn = document.getElementById("skills-create-btn");
    if (createBtn) {
      createBtn.addEventListener("click", function () {
        openSkillEditor(null);
      });
    }

    // Editor modal
    var editorClose = document.getElementById("skill-editor-close");
    var editorCancel = document.getElementById("skill-editor-cancel");
    var editorSave = document.getElementById("skill-editor-save");
    var editorDelete = document.getElementById("skill-editor-delete");
    var editorModal = document.getElementById("skill-editor-modal");
    if (editorClose) editorClose.addEventListener("click", closeSkillEditor);
    if (editorCancel) editorCancel.addEventListener("click", closeSkillEditor);
    if (editorSave) editorSave.addEventListener("click", saveSkill);
    if (editorDelete) editorDelete.addEventListener("click", deleteSkill);
    if (editorModal) {
      editorModal.addEventListener("click", function (e) {
        if (e.target === this) closeSkillEditor();
      });
    }
    // Ctrl+S / Cmd+S in editor
    var editorContent = document.getElementById("skill-editor-content");
    var editorName = document.getElementById("skill-editor-name");
    if (editorContent) {
      editorContent.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
          e.preventDefault();
          saveSkill();
        }
      });
    }
    if (editorName) {
      editorName.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
          e.preventDefault();
          saveSkill();
        }
      });
    }

    // Search
    var searchInput = document.getElementById("skills-search");
    var searchTimeout;
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(function () {
        searchQuery = searchInput.value.trim();
        applyFilters();
      }, 250);
    });

    // Pagination
    document.getElementById("skills-prev").addEventListener("click", function () {
      if (currentPage > 0) { currentPage--; renderGrid(); renderPagination(); }
    });
    document.getElementById("skills-next").addEventListener("click", function () {
      var totalPages = Math.ceil(filteredSkills.length / PAGE_SIZE) || 1;
      if (currentPage < totalPages - 1) { currentPage++; renderGrid(); renderPagination(); }
    });

    // Modal close
    document.getElementById("skill-modal-close").addEventListener("click", closeSkillModal);
    document.getElementById("skill-modal").addEventListener("click", function (e) {
      if (e.target === this) closeSkillModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeSkillModal();
    });

    // Push button
    document.getElementById("skill-modal-push").addEventListener("click", pushSkill);

    var stopOverview = document.getElementById("skills-tts-stop");
    if (stopOverview) {
      stopOverview.addEventListener("click", function () {
        stopSkillsOverviewPlayback();
      });
    }
    var playOverview = document.getElementById("skills-tts-play");
    if (playOverview) {
      playOverview.addEventListener("click", function () {
        replaySkillsOverview();
      });
    }

    // Enter key in instructions field
    document.getElementById("skill-modal-instructions").addEventListener("keydown", function (e) {
      if (e.key === "Enter") pushSkill();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();