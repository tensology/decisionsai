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

      html += '<div class="skill-card bg-[#152054] rounded-lg border border-white/10 p-4 hover:border-[#f97316]/50 transition-colors cursor-pointer" data-id="' + esc(skill.id) + '">';
      html += '  <div class="flex flex-wrap gap-1 mb-2">' + tagBadges + "</div>";
      html += '  <h4 class="text-sm font-semibold text-white mb-1.5 leading-tight">' + esc(skill.name || skill.id) + "</h4>";
      html += '  <p class="text-xs text-gray-400 leading-relaxed">' + esc(shortDesc) + "</p>";
      html += "</div>";
    });
    grid.innerHTML = html;

    grid.querySelectorAll(".skill-card").forEach(function (card) {
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

  // ── Skill detail modal ──────────────────────────────────────────
  function openSkillDetail(skillId) {
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

    // Clear status
    document.getElementById("skill-modal-push-status").textContent = "";

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

  function closeSkillModal() {
    document.getElementById("skill-modal").classList.add("hidden");
    currentSkillId = null;
  }

  // ── Push skill ──────────────────────────────────────────────────
  function pushSkill() {
    if (!currentSkillId) return;

    var projectEl = document.getElementById("skill-modal-project");
    var target = document.getElementById("skill-modal-target").value;
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
      body: JSON.stringify({ project_path: projectPath, target: target, instructions: instructions })
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

  // ── Event bindings ───────────────────────────────────────────────
  function bindEvents() {
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