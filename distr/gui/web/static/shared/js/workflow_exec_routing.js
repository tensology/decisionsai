// Shared Complexity routing pickers + save (extracted verbatim from workflows.js, 2026-10-22).
// Used by Workflows > Complexity routing and Settings > LLMs > Complexity routing so both
// read/write the same project_cli_{low,medium,high}_* settings via POST /api/workflows/orchestrator-setup.
(function () {
    function create(ctx) {
        ctx = ctx || {};
        var api = ctx.api;
        var esc = ctx.esc || function (s) {
            if (!s) return "";
            return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        };
        function workflowBoardProjectId() { return ctx.projectId ? ctx.projectId() : null; }
        function workflowCliBackendWorkflowReady(backend) {
            if (ctx.backendReady) return ctx.backendReady(backend);
            if (!backend) return false;
            if (typeof backend.workflow_ready === "boolean") return backend.workflow_ready;
            return !!backend.ready;
        }
        function refreshWorkflowConfigExecutorPills(root) {
            return ctx.onRoutingChanged ? ctx.onRoutingChanged(root) : Promise.resolve();
        }
        function workflowCliActiveBackend() { return ctx.activeBackend ? ctx.activeBackend() : ""; }
        function workflowExecDisplayModelLabel(model) {
            if (ctx.modelLabel) {
                try { var l = ctx.modelLabel(model || {}); if (l) return l; } catch (e) { /* fall through */ }
            }
            return (model && (model.name || model.id)) || "";
        }

        var WORKFLOW_EXEC_ROUTE_DEFAULTS = {
            low: { backend: "cursor", model: "auto" },
            medium: { backend: "codex", model: "auto" },
            high: { backend: "codex", model: "gpt-5.3-codex" }
        };
        var WORKFLOW_EXEC_BACKEND_OPTIONS = [
            { id: "codex", label: "Codex CLI" },
            { id: "claude_code", label: "Claude Code" },
            { id: "opencode", label: "OpenCode" },
            { id: "kilocode", label: "Kilo" },
            { id: "cursor", label: "Cursor CLI" },
            { id: "cline", label: "Cline" },
            { id: "hermes_agent", label: "External agent (optional)" },
            { id: "pi", label: "Pi" }
        ];
        var WORKFLOW_EXEC_IDE_BACKEND_IDS = { cursor_ide: true, codex_ide: true };
        var WORKFLOW_CLI_BACKEND_ORDER = {
            codex: 0,
            claude_code: 1,
            opencode: 2,
            kilocode: 3,
            pi: 99
        };

        function workflowExecBackendIsIde(backendId) {
            return !!WORKFLOW_EXEC_IDE_BACKEND_IDS[(backendId || "").trim()];
        }

        function workflowExecNormalizeCliBackend(backendId) {
            backendId = String(backendId || "").trim();
            if (backendId === "cursor_ide") return "cursor";
            if (backendId === "codex_ide") return "codex";
            return backendId;
        }
        function workflowExecRouteLevels() {
            return ["low", "medium", "high"];
        }

        function workflowCliBackendLabel(label) {
            return String(label || "").replace(/\s+CLI$/i, "").trim();
        }

        function workflowExecRoutePrefix(root) {
            if (root && (root.id === "sr-llm-modal" || (root.closest && root.closest("#sr-llm-modal")))) {
                return "wf-global-exec";
            }
            return "wf-global-exec";
        }

        function workflowExecRouteEl(root, level, part) {
            var prefix = workflowExecRoutePrefix(root);
            return root.querySelector("#" + prefix + "-" + level + "-" + part);
        }

        function workflowExecRouteModelLabel(model) {
            if (!model) return "";
            if (typeof model === "string") return model;
            return model.name || model.id || model.model || "";
        }

        function workflowExecRouteModelValue(model) {
            if (!model) return "";
            if (typeof model === "string") return model;
            return model.id || model.model || model.name || "";
        }

        function cliModelGroupsFromList(models) {
            var groups = {};
            (models || []).forEach(function (m) {
                if (!m) return;
                var p = m.provider || "other";
                if (!groups[p]) groups[p] = [];
                groups[p].push(m);
            });
            return groups;
        }

        function appendCliModelOption(parent, model, selectedId) {
            var opt = document.createElement("option");
            opt.value = model.id;
            opt.dataset.provider = model.provider || "";
            opt.dataset.scope = model.scope || "available";
            opt.dataset.free = model.free ? "true" : "false";
            var flags = [];
            if (model.scope === "scoped") flags.push("scoped");
            if (model.free) flags.push("free");
            if (model.tier) flags.push(model.tier);
            opt.textContent = workflowExecDisplayModelLabel(model) + (flags.length ? " - " + flags.join(", ") : "");
            if (model.supports_chat === false) opt.disabled = true;
            if (model.id === selectedId) opt.selected = true;
            parent.appendChild(opt);
        }

        function populateCliModelSelect(select, data, options) {
            if (!select) return;
            options = options || {};
            var models = Array.isArray(data.models) ? data.models : [];
            var current = (data.current_model || "").trim();
            var includeAuto = options.includeAuto !== false;
            select.innerHTML = "";
            if (includeAuto) {
                var autoOpt = document.createElement("option");
                autoOpt.value = "auto";
                autoOpt.textContent = "Auto";
                autoOpt.dataset.provider = "";
                select.appendChild(autoOpt);
            }
            var groups = cliModelGroupsFromList(models);
            Object.keys(groups).sort().forEach(function (prov) {
                var og = document.createElement("optgroup");
                og.label = prov.charAt(0).toUpperCase() + prov.slice(1);
                groups[prov].forEach(function (m) {
                    appendCliModelOption(og, m, current);
                });
                select.appendChild(og);
            });
            if (current && !models.some(function (m) { return m.id === current; })) {
                var cur = document.createElement("option");
                cur.value = current;
                cur.dataset.provider = data.current_provider || "";
                cur.textContent = workflowExecDisplayModelLabel({ id: current });
                cur.selected = true;
                select.insertBefore(cur, select.firstChild);
            }
            if (includeAuto) {
                var hasCurrent = current && Array.prototype.some.call(select.options, function (o) { return o.value === current; });
                select.value = hasCurrent ? current : "auto";
            } else if (current) {
                select.value = current;
            }
        }

        function selectedCliModelProvider(select) {
            if (!select || select.selectedIndex < 0) return "";
            var opt = select.options[select.selectedIndex];
            return opt ? String(opt.dataset.provider || "").trim() : "";
        }

        function workflowStepBackendOptionsHtml(selected) {
            selected = String(selected || "").trim();
            var html = '<option value="">Use workflow route</option>';
            WORKFLOW_EXEC_BACKEND_OPTIONS.forEach(function (opt) {
                if (opt.id === "cursor_ide" || opt.id === "codex_ide") return;
                html += '<option value="' + esc(opt.id) + '"' + (selected === opt.id ? " selected" : "") + '>' + esc(workflowCliBackendLabel(opt.label)) + '</option>';
            });
            return html;
        }

        function loadStepCliModels(container, selectedModel) {
            var backendSel = container.querySelector(".sf-cli-backend");
            var modelSel = container.querySelector(".sf-cli-model");
            var hint = container.querySelector(".sf-cli-model-hint");
            if (!backendSel || !modelSel) return Promise.resolve();
            var backendId = (backendSel.value || (typeof workflowCliActiveBackend === "function" ? workflowCliActiveBackend() : "") || "pi").trim();
            if (workflowExecBackendIsIde(backendId)) {
                modelSel.innerHTML = '<option value="">Model selected in IDE</option>';
                modelSel.disabled = true;
                if (hint) hint.textContent = "IDE handoff chooses the model inside the editor.";
                return Promise.resolve();
            }
            modelSel.disabled = true;
            modelSel.innerHTML = '<option value="auto">Loading...</option>';
            var params = "backend_id=" + encodeURIComponent(backendId);
            var projectId = workflowBoardProjectId();
            if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
            return api("GET", "/projects/cli-models?" + params)
                .then(function (data) {
                    populateCliModelSelect(modelSel, {
                        models: data.models || [],
                        current_model: selectedModel || "auto",
                        current_provider: data.current_provider || ""
                    }, { includeAuto: true });
                    modelSel.disabled = false;
                    if (hint) hint.textContent = data.message || "Auto follows workflow policy unless this step picks a concrete model.";
                })
                .catch(function (e) {
                    modelSel.innerHTML = '<option value="auto">Auto</option>';
                    modelSel.disabled = false;
                    if (hint) hint.textContent = (e && e.message) || "Could not load models for this backend.";
                });
        }

        function providerForCliModelBackend(backendId, provider) {
            backendId = String(backendId || "pi").trim();
            if (backendId === "pi" || backendId === "opencode") {
                return provider || "ollama";
            }
            return backendId;
        }

        // harnesssrc 2026-10-22: offer only installed harnesses, from the shared source
        // (/static/shared/harnesses.js -> GET /api/harnesses/installed). null = not loaded yet.
        var WORKFLOW_EXEC_INSTALLED_IDS = null;
        var workflowExecInstalledRequest = null;
        function workflowExecLoadInstalled() {
            if (!workflowExecInstalledRequest) {
                workflowExecInstalledRequest = import("/static/shared/harnesses.js?v=20261022-harnesssrc")
                    .then(function (mod) { return mod.loadInstalledHarnesses(); })
                    .then(function (rows) {
                        WORKFLOW_EXEC_INSTALLED_IDS = {};
                        (rows || []).forEach(function (row) { WORKFLOW_EXEC_INSTALLED_IDS[row.id] = true; });
                        return WORKFLOW_EXEC_INSTALLED_IDS;
                    })
                    .catch(function () { return null; });
            }
            return workflowExecInstalledRequest;
        }
        function workflowExecOfferBackend(id) {
            if (workflowExecBackendIsIde(id)) return false;
            return !WORKFLOW_EXEC_INSTALLED_IDS || !!WORKFLOW_EXEC_INSTALLED_IDS[id];
        }
        // A saved value that is not installed stays visible as the current value, disabled, never offered.
        function workflowExecKeepCurrent(select, current) {
            if (!current) return;
            var present = Array.prototype.some.call(select.options, function (o) { return o.value === current; });
            if (!present) {
                var item = WORKFLOW_EXEC_BACKEND_OPTIONS.filter(function (o) { return o.id === current; })[0];
                var opt = document.createElement("option");
                opt.value = current;
                opt.disabled = true;
                opt.textContent = (item ? workflowCliBackendLabel(item.label) : current) + " \u2014 not installed";
                select.appendChild(opt);
            }
            select.value = current;
        }

        function workflowExecRouteBackendOptionsHtml(readyMap) {
            return WORKFLOW_EXEC_BACKEND_OPTIONS.filter(function (item) {
                return workflowExecOfferBackend(item.id);
            }).map(function (item) {
                return '<option value="' + esc(item.id) + '">' + esc(workflowCliBackendLabel(item.label)) + "</option>";
            }).join("");
        }

        function workflowExecCliBackendOptionsHtml(readyMap, includeEmpty) {
            var html = includeEmpty ? '<option value="">None</option>' : "";
            WORKFLOW_EXEC_BACKEND_OPTIONS.forEach(function (item) {
                if (!workflowExecOfferBackend(item.id)) return;
                html += '<option value="' + esc(item.id) + '">' + esc(workflowCliBackendLabel(item.label)) + "</option>";
            });
            return html;
        }

        // 2026-10-22: one grid row per tier with a header row; Auto checkbox in the first column;
        // fallback is a sub-row; shared notes shown once in a footnote. Codex cog removed.
        function workflowExecEnsureStyles() {
            if (document.getElementById("wf-exec-route-table-styles")) return;
            var st = document.createElement("style");
            st.id = "wf-exec-route-table-styles";
            st.textContent = [
                ".wf-exec-route-table .hidden { display: none !important; }",
                ".wf-exec-route-table { display: flex; flex-direction: column; gap: 0; width: 100%; text-align: center; }",
                ".wf-exec-route-table .wf-exec-grid { display: grid; grid-template-columns: 3.25rem 5rem minmax(6rem, 1fr) minmax(6rem, 0.85fr) minmax(9rem, 1.5fr); gap: 0.5rem; align-items: center; justify-items: stretch; }",
                ".wf-exec-route-table .wf-exec-grid > * { min-width: 0; }",
                ".wf-exec-route-table .wf-exec-grid--head { padding: 0 0 0.375rem; border-bottom: 1px solid rgba(255,255,255,0.12); font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: #9ca3af; }",
                ".wf-exec-route-table .wf-exec-route-block { padding: 0.5rem 0; border-bottom: 1px solid rgba(255,255,255,0.07); display: flex; flex-direction: column; gap: 0.375rem; }",
                ".wf-exec-route-table .wf-exec-tier { display: flex; align-items: center; justify-content: center; gap: 0.5rem; white-space: nowrap; }",
                ".wf-exec-route-table .wf-exec-tier-name { font-size: 0.8125rem; font-weight: 600; color: #ececf1; }",
                ".wf-exec-route-table .wf-exec-tier-name--fallback { font-size: 0.75rem; font-weight: 500; color: #9ca3af; }",
                ".wf-exec-route-table .wf-exec-auto-cell { display: flex; align-items: center; justify-content: center; }",
                ".wf-exec-route-table .wf-exec-route-block.is-auto .wf-exec-provider, .wf-exec-route-table .wf-exec-route-block.is-auto .wf-exec-model { opacity: 0.45; cursor: not-allowed; }",
                ".wf-exec-route-table .wf-exec-route-help { margin: 0.75rem 0 0; padding: 0; text-align: left; font-size: 0.6875rem; line-height: 1.5; color: #9ca3af; }",
                ".wf-exec-route-table .wf-exec-route-help li { list-style: disc; margin-left: 1rem; }",
                ".wf-exec-route-table .wf-exec-auto-label { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer; font-size: 0.6875rem; color: #d1d5db; user-select: none; }",
                ".wf-exec-route-table .wf-exec-auto { appearance: none; -webkit-appearance: none; width: 0.95rem; height: 0.95rem; margin: 0; border: 1px solid #6b7280; border-radius: 0.25rem; background: #152054 center / 0.7rem no-repeat; cursor: pointer; transition: background-color .15s, border-color .15s; }",
                ".wf-exec-route-table .wf-exec-auto:hover { border-color: #9ca3af; }",
                ".wf-exec-route-table .wf-exec-auto:checked { background-color: var(--wf-exec-accent, #10a37f); border-color: var(--wf-exec-accent, #10a37f); background-image: url(\"data:image/svg+xml,%3csvg viewBox='0 0 16 16' fill='white' xmlns='http://www.w3.org/2000/svg'%3e%3cpath d='M12.207 4.793a1 1 0 010 1.414l-5 5a1 1 0 01-1.414 0l-2-2a1 1 0 011.414-1.414L6.5 9.086l4.293-4.293a1 1 0 011.414 0z'/%3e%3c/svg%3e\"); }",
                ".wf-exec-route-table .wf-exec-auto:focus-visible { outline: 2px solid var(--wf-exec-accent, #10a37f); outline-offset: 2px; }",
                ".wf-exec-route-table select { width: 100%; min-height: 2rem; text-align: center; text-align-last: center; text-overflow: ellipsis; white-space: nowrap; }",
                ".wf-exec-route-table .wf-exec-route-footnote { margin: 0.5rem 0 0; font-size: 0.6875rem; line-height: 1.4; color: #6b7280; }",
                ".wf-exec-route-table .wf-exec-route-footnote li { list-style: none; }",
                ".wf-exec-route-table .wf-exec-route-footnote .wf-exec-route-footnote--warn { color: rgba(253, 230, 138, 0.9); }"
            ].join("\n");
            document.head.appendChild(st);
        }

        function workflowExecSelectHtml(id, cls, level, inner, aria) {
            return '<select id="' + id + '" class="' + cls + ' px-2 py-1.5 bg-[#152054] border border-white/20 rounded text-white text-xs focus:outline-none" data-level="' + level + '" aria-label="' + esc(aria) + '">' + inner + "</select>";
        }

        function workflowExecRouteHtml(rootOrPrefix) {
            var prefix = typeof rootOrPrefix === "string" ? rootOrPrefix : workflowExecRoutePrefix(rootOrPrefix);
            workflowExecEnsureStyles();
            var backendOptions = workflowExecRouteBackendOptionsHtml();
            var fallbackBackendOptions = workflowExecCliBackendOptionsHtml({}, true);
            var head = '<div class="wf-exec-grid wf-exec-grid--head" role="row">' +
                '<span role="columnheader">Auto</span><span role="columnheader">Tier</span><span role="columnheader">Harness</span>' +
                '<span role="columnheader">Provider</span><span role="columnheader">Model</span></div>';
            var rows = workflowExecRouteLevels().map(function (level) {
                var label = level.charAt(0).toUpperCase() + level.slice(1);
                var id = function (part) { return prefix + "-" + level + "-" + part; };
                return '<div class="wf-exec-route-block" data-level="' + level + '" role="rowgroup">' +
                    '<div class="wf-exec-grid wf-exec-grid--primary" role="row">' +
                        '<div class="wf-exec-auto-cell"><input id="' + id("auto") + '" type="checkbox" class="wf-exec-auto" data-level="' + level + '" title="Auto: the harness / Auto policy picks the model for ' + label + ' work" aria-label="' + label + ' uses Auto"></div>' +
                        '<div class="wf-exec-tier"><span class="wf-exec-tier-name">' + label + '</span></div>' +
                        workflowExecSelectHtml(id("backend"), "wf-exec-backend", level, backendOptions, label + " harness") +
                        workflowExecSelectHtml(id("provider"), "wf-exec-provider", level, '<option value="">All providers</option>', label + " provider") +
                        workflowExecSelectHtml(id("model"), "wf-exec-model", level, '<option value="auto">Auto</option>', label + " model") +
                    "</div>" +
                    '<div id="' + id("fallback-row") + '" class="wf-exec-route-fallback hidden">' +
                        '<div class="wf-exec-grid wf-exec-grid--fallback" role="row">' +
                            '<span aria-hidden="true"></span><div class="wf-exec-tier"><span class="wf-exec-tier-name wf-exec-tier-name--fallback">&#8627; Fallback</span></div>' +
                            workflowExecSelectHtml(id("fallback-backend"), "wf-exec-fallback-backend", level, fallbackBackendOptions, label + " fallback harness") +
                            workflowExecSelectHtml(id("fallback-provider"), "wf-exec-fallback-provider", level, '<option value="">All providers</option>', label + " fallback provider") +
                            workflowExecSelectHtml(id("fallback-model"), "wf-exec-fallback-model", level, '<option value="auto">Auto</option>', label + " fallback model") +
                        "</div>" +
                    "</div>" +
                    '<p id="' + id("model-hint") + '" class="wf-exec-model-hint hidden"></p>' +
                    // Codex intelligence/speed stay as hidden fields so saved values round-trip unchanged.
                    '<div id="' + id("codex-prefs") + '" class="hidden">' +
                        '<select id="' + id("codex-intelligence") + '" class="wf-exec-codex-intelligence" data-level="' + level + '" aria-hidden="true" tabindex="-1">' +
                            '<option value="">Default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option></select>' +
                        '<select id="' + id("codex-speed") + '" class="wf-exec-codex-speed" data-level="' + level + '" aria-hidden="true" tabindex="-1">' +
                            '<option value="">Default</option><option value="flex">Flex</option><option value="fast">Fast</option></select>' +
                    "</div>" +
                "</div>";
            }).join("");
            var foot = '<ul id="' + prefix + '-route-footnote" class="wf-exec-route-footnote">' +
                '<li>Fallback: CLI harness and model used when the primary route is unavailable or not set up.</li></ul>';
            // autocol 2026-10-22: how routing is used (mirrors core/kanban/ticket_policy.py + coordination_plan.py).
            var help = '<ul class="wf-exec-route-help">' +
                '<li><b>Rating:</b> each ticket gets Low, Medium or High. You can set it; otherwise it is rated from its text (length, files, todos, and words like migration, auth, refactor, deploy). A workflow step can set its own tier; if nothing rates it, Medium is used (High for high-risk runs).</li>' +
                '<li><b>Tier:</b> the matching row runs the work with its harness and model. A board can override a row.</li>' +
                '<li><b>Fallback:</b> used when the row&#39;s harness is not set up or not available, and for failover when a run fails. With no fallback set, the built-in default for that tier is tried, then Pi.</li>' +
                '<li><b>Auto:</b> ticked means the row keeps its harness but does not pin a model: the harness picks its own model, and workflows with Auto routing pick a model per step (planning, build, review). Your saved model is kept for when you untick it.</li>' +
                '</ul>';
            return '<div class="wf-exec-route-table" role="table" aria-label="Complexity routing">' + head + rows + foot + help + "</div>";
        }

        // Provider column: derived from the model options' data-provider (what save reads).
        function workflowExecProviderOf(opt) { return opt ? String(opt.dataset.provider || "").trim() : ""; }
        function workflowExecFilterModels(modelSel, provider) {
            Array.prototype.forEach.call(modelSel.querySelectorAll("option"), function (o) {
                var p = workflowExecProviderOf(o);
                o.hidden = !!provider && !!p && p !== provider && !o.selected;
            });
            Array.prototype.forEach.call(modelSel.querySelectorAll("optgroup"), function (g) {
                g.hidden = !!provider && !Array.prototype.some.call(g.children, function (o) { return !o.hidden; });
            });
        }
        function workflowExecSyncProviderPicker(modelSel, providerSel) {
            if (!modelSel || !providerSel) return;
            var providers = [];
            Array.prototype.forEach.call(modelSel.options, function (o) {
                var p = workflowExecProviderOf(o);
                if (p && providers.indexOf(p) < 0) providers.push(p);
            });
            var current = workflowExecProviderOf(modelSel.selectedOptions && modelSel.selectedOptions[0]) || modelSel.dataset.pinnedProvider || "";
            if (current && providers.indexOf(current) < 0) providers.push(current);
            providerSel.innerHTML = '<option value="">All providers</option>' + providers.sort().map(function (p) {
                return '<option value="' + esc(p) + '">' + esc(p.charAt(0).toUpperCase() + p.slice(1)) + "</option>";
            }).join("");
            providerSel.value = current;
            providerSel.disabled = modelSel.disabled || providers.length === 0;
            workflowExecFilterModels(modelSel, providerSel.value);
        }
        function bindWorkflowExecProviderPickers(root) {
            workflowExecRouteLevels().forEach(function (level) {
                [["model", "provider"], ["fallback-model", "fallback-provider"]].forEach(function (pair) {
                    var modelSel = workflowExecRouteEl(root, level, pair[0]);
                    var providerSel = workflowExecRouteEl(root, level, pair[1]);
                    if (!modelSel || !providerSel || providerSel.dataset.execBound === "1") return;
                    providerSel.dataset.execBound = "1";
                    providerSel.addEventListener("change", function () {
                        var want = providerSel.value;
                        var cur = modelSel.selectedOptions && modelSel.selectedOptions[0];
                        if (want && cur && workflowExecProviderOf(cur) !== want) {
                            var first = Array.prototype.find.call(modelSel.options, function (o) { return !o.disabled && workflowExecProviderOf(o) === want; });
                            if (first) {
                                modelSel.value = first.value;
                                modelSel.dataset.pinnedProvider = want;
                                modelSel.dispatchEvent(new Event("change", { bubbles: true }));
                            }
                        }
                        workflowExecFilterModels(modelSel, want);
                    });
                    modelSel.addEventListener("change", function () {
                        var p = workflowExecProviderOf(modelSel.selectedOptions && modelSel.selectedOptions[0]);
                        if (p && providerSel.value !== p) providerSel.value = p;
                        workflowExecFilterModels(modelSel, providerSel.value);
                    });
                    new MutationObserver(function () { workflowExecSyncProviderPicker(modelSel, providerSel); })
                        .observe(modelSel, { childList: true, attributes: true, attributeFilter: ["disabled"] });
                    workflowExecSyncProviderPicker(modelSel, providerSel);
                });
            });
        }

        // Per-tier hints are collected and shown once in the footnote (deduplicated).
        function renderWorkflowExecRouteFootnote(root) {
            var foot = root.querySelector(".wf-exec-route-footnote");
            if (!foot) return;
            var seen = {}, items = [];
            workflowExecRouteLevels().forEach(function (level) {
                var hint = workflowExecRouteEl(root, level, "model-hint");
                var text = hint ? String(hint.dataset.message || "").trim() : "";
                if (!text) return;
                if (!seen[text]) { seen[text] = []; items.push(text); }
                seen[text].push(level.charAt(0).toUpperCase() + level.slice(1));
            });
            foot.innerHTML = items.map(function (text) {
                return '<li class="wf-exec-route-footnote--warn">' + esc(seen[text].join(", ")) + ": " + esc(text) + "</li>";
            }).join("") + "<li>Fallback: CLI harness and model used when the primary route is unavailable or not set up.</li>";
        }

        function updateWorkflowExecBackendSelects(root, backends) {
            root = root || document;
            if (WORKFLOW_EXEC_INSTALLED_IDS === null) {
                workflowExecLoadInstalled().then(function (ids) {
                    if (ids) updateWorkflowExecBackendSelects(root, backends);
                });
            }
            var readyMap = {};
            (backends || []).forEach(function (row) {
                readyMap[row.id] = workflowCliBackendWorkflowReady(row);
            });
            var optionsHtml = workflowExecRouteBackendOptionsHtml(readyMap);
            var fallbackOptionsHtml = workflowExecCliBackendOptionsHtml(readyMap, true);
            root.querySelectorAll(".wf-exec-backend").forEach(function (select) {
                var current = select.value;
                select.innerHTML = optionsHtml;
                workflowExecKeepCurrent(select, current);
            });
            root.querySelectorAll(".wf-exec-fallback-backend").forEach(function (select) {
                var current = select.value;
                select.innerHTML = fallbackOptionsHtml;
                workflowExecKeepCurrent(select, current);
            });
        }

        function updateWorkflowExecFallbackVisibility(level, root) {
            root = root || document;
            var backend = workflowExecRouteEl(root, level, "backend");
            var row = workflowExecRouteEl(root, level, "fallback-row");
            // Fallbacks apply to every backend (e.g. Pi via OpenRouter -> Pi via
            // Kilo Code), not only IDE handoffs. Never clear a configured value here.
            if (row) row.classList.toggle("hidden", !backend);
        }

        function updateWorkflowExecCodexPrefVisibility(level, root) {
            root = root || document;
            var backend = workflowExecRouteEl(root, level, "backend");
            var panel = workflowExecRouteEl(root, level, "codex-prefs");
            var isCodexCli = backend && backend.value === "codex";
            if (!isCodexCli && panel) panel.classList.add("hidden");
        }

        function toggleWorkflowExecCodexPrefPanel(level, root) {
            root = root || document;
            var backend = workflowExecRouteEl(root, level, "backend");
            var panel = workflowExecRouteEl(root, level, "codex-prefs");
            if (!panel || !backend || backend.value !== "codex") return;
            panel.classList.toggle("hidden");
        }

        function workflowExecModelsSourceIsVerified(source) {
            var s = String(source || "").toLowerCase();
            return s === "cursor-api" || s === "codex-cli" || s === "anthropic-api" || s === "pi-models"
                || s === "opencode-cli" || s === "opencode-fallback" || s === "opencode-missing";
        }

        function setWorkflowExecRouteModelHint(root, level, message) {
            var hint = workflowExecRouteEl(root, level, "model-hint");
            if (!hint) return;
            hint.dataset.message = (message || "").trim();
            hint.textContent = "";
            hint.classList.add("hidden");
            renderWorkflowExecRouteFootnote(root);
        }

        function setWorkflowExecRouteIdeModelSelect(select) {
            if (!select) return;
            select.innerHTML = "";
            var opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "Determined by the CLI";
            select.appendChild(opt);
            select.value = "";
            select.disabled = true;
        }

        function loadWorkflowExecRouteModels(level, backendId, selectedModel, root) {
            root = root || document;
            var select = workflowExecRouteEl(root, level, "model");
            if (!select) return Promise.resolve();
            backendId = (backendId || "codex").trim();
            setWorkflowExecRouteModelHint(root, level, "");
            if (workflowExecBackendIsIde(backendId)) {
                setWorkflowExecRouteIdeModelSelect(select);
                updateWorkflowExecCodexPrefVisibility(level, root);
                updateWorkflowExecFallbackVisibility(level, root);
                return Promise.resolve();
            }
            var preferred = (selectedModel || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model || "auto").trim();
            select.disabled = true;
            select.innerHTML = "";
            var loadingOpt = document.createElement("option");
            loadingOpt.value = "auto";
            loadingOpt.textContent = "Loading...";
            select.appendChild(loadingOpt);
            var params = "backend_id=" + encodeURIComponent(backendId);
            var projectId = workflowBoardProjectId();
            if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
            return api("GET", "/projects/cli-models?" + params)
                .then(function (data) {
                    var source = (data && data.source) || "";
                    var message = (data && data.message) || "";
                    var models = Array.isArray(data.models) ? data.models : [];
                    var sourceLower = String(source).toLowerCase();
                    var verified = workflowExecModelsSourceIsVerified(source);
                    var aliasesOnly = sourceLower === "claude-code-aliases";
                    var defaultsOnly = sourceLower.indexOf("defaults") >= 0 || sourceLower === "error";
                    populateCliModelSelect(select, {
                        models: models,
                        current_model: preferred,
                        current_provider: select.dataset.pinnedProvider || data.current_provider || ""
                    }, { includeAuto: true });
                    if (aliasesOnly && message) {
                        setWorkflowExecRouteModelHint(root, level, message);
                    } else if (defaultsOnly) {
                        setWorkflowExecRouteModelHint(
                            root,
                            level,
                            message || "Could not load models from this executor. Use Auto or fix the CLI/API setup."
                        );
                    } else if (!verified && !models.length) {
                        setWorkflowExecRouteModelHint(
                            root,
                            level,
                            message || "No models reported by this executor."
                        );
                    } else if (message) {
                        setWorkflowExecRouteModelHint(root, level, message);
                    } else {
                        setWorkflowExecRouteModelHint(root, level, "");
                    }
                    select.disabled = false;
                    syncWorkflowExecAutoToggle(level, root);
                    updateWorkflowExecCodexPrefVisibility(level, root);
                })
                .catch(function (e) {
                    populateCliModelSelect(select, {
                        models: [],
                        current_model: preferred,
                        current_provider: select.dataset.pinnedProvider || ""
                    }, { includeAuto: true });
                    syncWorkflowExecAutoToggle(level, root);
                    setWorkflowExecRouteModelHint(
                        root,
                        level,
                        ((e && e.message) ? e.message : "Could not load models from this executor.") +
                        (preferred !== "auto" ? " Keeping the pinned model visible." : "")
                    );
                    updateWorkflowExecCodexPrefVisibility(level, root);
                });
        }

        function loadWorkflowExecRouteFallbackModels(level, backendId, selectedModel, root) {
            root = root || document;
            var select = workflowExecRouteEl(root, level, "fallback-model");
            var backendSelect = workflowExecRouteEl(root, level, "fallback-backend");
            if (!select) return Promise.resolve();
            backendId = (backendId || (backendSelect && backendSelect.value) || "").trim();
            if (!backendId || workflowExecBackendIsIde(backendId)) {
                select.innerHTML = '<option value="auto">Auto</option>';
                select.value = "auto";
                select.disabled = true;
                return Promise.resolve();
            }
            var preferred = (selectedModel || "auto").trim();
            select.disabled = true;
            select.innerHTML = '<option value="auto">Loading...</option>';
            var params = "backend_id=" + encodeURIComponent(backendId);
            var projectId = workflowBoardProjectId();
            if (projectId) params += "&project_id=" + encodeURIComponent(projectId);
            return api("GET", "/projects/cli-models?" + params)
                .then(function (data) {
                    var models = Array.isArray(data.models) ? data.models : [];
                    var seen = {};
                    select.innerHTML = "";
                    function append(value, label) {
                        value = (value || "auto").trim();
                        if (!value || seen[value]) return;
                        seen[value] = true;
                        var opt = document.createElement("option");
                        opt.value = value;
                        opt.textContent = label || value;
                        select.appendChild(opt);
                    }
                    append("auto", "Auto");
                    models.forEach(function (model) {
                        var value = workflowExecRouteModelValue(model);
                        if (!value || value === "auto") return;
                        append(value, workflowExecDisplayModelLabel(model));
                        var added = select.options[select.options.length - 1];
                        if (added && added.value === value && !added.dataset.provider) {
                            added.dataset.provider = String(model.provider || model.model_provider || "").trim();
                        }
                    });
                    var current = preferred || "auto";
                    if (!seen[current]) {
                        // Keep a saved fallback even if the live catalogue omits it.
                        append(current, workflowExecDisplayModelLabel({ id: current }) + " (saved)");
                    }
                    select.value = current;
                    select.disabled = false;
                })
                .catch(function () {
                    select.innerHTML = '<option value="auto">Auto</option>';
                    select.value = "auto";
                    select.disabled = false;
                });
        }

        function populateWorkflowExecRouting(data, llmData, root) {
            root = root || document;
            data = data || {};
            llmData = llmData || {};
            var loads = [];
            workflowExecRouteLevels().forEach(function (level) {
                var backend = workflowExecRouteEl(root, level, "backend");
                var route = (data.routing && data.routing[level]) || {};
                var backendValue = workflowExecNormalizeCliBackend(route.backend || llmData["project_cli_" + level + "_backend"] || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].backend);
                var modelValue = route.model || llmData["project_cli_" + level + "_model"] || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model;
                var providerValue = route.model_provider || llmData["project_cli_" + level + "_model_provider"] || "";
                var fallbackBackendValue = workflowExecNormalizeCliBackend(route.fallback_backend || llmData["project_cli_" + level + "_fallback_backend"] || "");
                var fallbackModelValue = route.fallback_model || llmData["project_cli_" + level + "_fallback_model"] || "";
                if (backend) workflowExecKeepCurrent(backend, backendValue);
                var autoBox = workflowExecRouteEl(root, level, "auto");
                if (autoBox) {
                    var autoRaw = route.auto != null ? route.auto : llmData["project_cli_" + level + "_auto"];
                    autoBox.checked = autoRaw === true || autoRaw === 1 || autoRaw === "1" || autoRaw === "true";
                }
                var modelSelect = workflowExecRouteEl(root, level, "model");
                if (modelSelect) modelSelect.dataset.pinnedProvider = providerValue;
                var fallbackBackend = workflowExecRouteEl(root, level, "fallback-backend");
                if (fallbackBackend) {
                    if (fallbackBackendValue) workflowExecKeepCurrent(fallbackBackend, fallbackBackendValue);
                    else fallbackBackend.value = "";
                }
                var fallbackModelSelect = workflowExecRouteEl(root, level, "fallback-model");
                if (fallbackModelSelect) {
                    fallbackModelSelect.dataset.pinnedProvider =
                        route.fallback_model_provider || llmData["project_cli_" + level + "_fallback_model_provider"] || "";
                }
                var intelligence = workflowExecRouteEl(root, level, "codex-intelligence");
                var speed = workflowExecRouteEl(root, level, "codex-speed");
                if (intelligence) intelligence.value = route.codex_intelligence || "";
                if (speed) speed.value = route.codex_speed || "";
                var prefsPanel = workflowExecRouteEl(root, level, "codex-prefs");
                if (prefsPanel) prefsPanel.classList.add("hidden");
                loads.push(loadWorkflowExecRouteModels(level, backendValue, modelValue, root));
                loads.push(loadWorkflowExecRouteFallbackModels(level, fallbackBackendValue, fallbackModelValue, root));
                updateWorkflowExecCodexPrefVisibility(level, root);
                updateWorkflowExecFallbackVisibility(level, root);
            });
            return Promise.all(loads);
        }

        function collectWorkflowExecRouting(root) {
            root = root || document;
            var routing = {};
            workflowExecRouteLevels().forEach(function (level) {
                var backend = workflowExecRouteEl(root, level, "backend");
                var model = workflowExecRouteEl(root, level, "model");
                var auto = workflowExecRouteEl(root, level, "auto");
                var intelligence = workflowExecRouteEl(root, level, "codex-intelligence");
                var speed = workflowExecRouteEl(root, level, "codex-speed");
                var backendValue = (backend && backend.value) || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].backend;
                var row = {
                    backend: backendValue,
                    model: workflowExecBackendIsIde(backendValue)
                        ? ""
                        : ((model && model.value) || WORKFLOW_EXEC_ROUTE_DEFAULTS[level].model),
                    auto: !!(auto && auto.checked)
                };
                if (model && model.selectedOptions && model.selectedOptions[0]) {
                    row.model_provider = String(
                        model.selectedOptions[0].dataset.provider || model.dataset.pinnedProvider || ""
                    ).trim();
                }
                // Always send the fallback so the server never has to guess.
                var fallbackBackend = workflowExecRouteEl(root, level, "fallback-backend");
                var fallbackModel = workflowExecRouteEl(root, level, "fallback-model");
                row.fallback_backend = (fallbackBackend && fallbackBackend.value) || "";
                row.fallback_model = row.fallback_backend
                    ? ((fallbackModel && fallbackModel.value) || "auto")
                    : "";
                row.fallback_model_provider = row.fallback_backend && fallbackModel
                    ? String(
                        selectedCliModelProvider(fallbackModel) || fallbackModel.dataset.pinnedProvider || ""
                    ).trim()
                    : "";
                if (row.backend === "codex") {
                    row.codex_intelligence = (intelligence && intelligence.value) || "";
                    row.codex_speed = (speed && speed.value) || "";
                }
                routing[level] = row;
            });
            return routing;
        }

        function bindWorkflowExecRoutingControls(root) {
            root = root || document;
            root.querySelectorAll(".wf-exec-backend").forEach(function (select) {
                if (select.dataset.execBound === "1") return;
                select.dataset.execBound = "1";
                select.addEventListener("change", function () {
                    loadWorkflowExecRouteModels(select.dataset.level, select.value, "auto", root);
                    updateWorkflowExecFallbackVisibility(select.dataset.level, root);
                    refreshWorkflowConfigExecutorPills(root);
                });
            });
            root.querySelectorAll(".wf-exec-model").forEach(function (select) {
                if (select.dataset.modelPinBound === "1") return;
                select.dataset.modelPinBound = "1";
                select.addEventListener("change", function () {
                    select.dataset.pinnedProvider = selectedCliModelProvider(select);
                    syncWorkflowExecAutoToggle(select.dataset.level, root);
                });
            });
            root.querySelectorAll(".wf-exec-auto").forEach(function (checkbox) {
                if (checkbox.dataset.execBound === "1") return;
                checkbox.dataset.execBound = "1";
                checkbox.addEventListener("change", function () {
                    syncWorkflowExecAutoToggle(checkbox.dataset.level, root);
                });
            });
            root.querySelectorAll(".wf-exec-fallback-backend").forEach(function (select) {
                if (select.dataset.execBound === "1") return;
                select.dataset.execBound = "1";
                select.addEventListener("change", function () {
                    loadWorkflowExecRouteFallbackModels(select.dataset.level, select.value, "auto", root);
                });
            });
            bindWorkflowExecProviderPickers(root);
        }

        // autocol 2026-10-22: Auto is its own per-tier flag; ticking it greys the provider/model
        // pickers but keeps their values so unticking restores the manual route.
        function syncWorkflowExecAutoToggle(level, root) {
            root = root || document;
            var auto = workflowExecRouteEl(root, level, "auto");
            if (!auto) return;
            var on = !!auto.checked;
            ["provider", "model"].forEach(function (part) {
                var el = workflowExecRouteEl(root, level, part);
                if (!el) return;
                el.disabled = on;
                el.title = on ? "Auto is on for this tier; the saved value is kept" : "";
            });
            var block = auto.closest ? auto.closest(".wf-exec-route-block") : null;
            if (block) block.classList.toggle("is-auto", on);
        }

        function saveWorkflowExecRouting(root, options) {
            options = options || {};
            var payload = {
                enabled: options.enabled != null ? !!options.enabled : true,
                routing: collectWorkflowExecRouting(root)
            };
            return api("POST", "/workflows/orchestrator-setup", payload).then(function (resp) {
                refreshWorkflowConfigExecutorPills(root);
                return resp;
            });
        }

        return {
            WORKFLOW_EXEC_ROUTE_DEFAULTS: WORKFLOW_EXEC_ROUTE_DEFAULTS,
            WORKFLOW_EXEC_BACKEND_OPTIONS: WORKFLOW_EXEC_BACKEND_OPTIONS,
            WORKFLOW_CLI_BACKEND_ORDER: WORKFLOW_CLI_BACKEND_ORDER,
            bindWorkflowExecProviderPickers: bindWorkflowExecProviderPickers,
            renderWorkflowExecRouteFootnote: renderWorkflowExecRouteFootnote,
            workflowExecBackendIsIde: workflowExecBackendIsIde,
            workflowExecNormalizeCliBackend: workflowExecNormalizeCliBackend,
            workflowExecRouteLevels: workflowExecRouteLevels,
            workflowCliBackendLabel: workflowCliBackendLabel,
            workflowExecRoutePrefix: workflowExecRoutePrefix,
            workflowExecRouteEl: workflowExecRouteEl,
            workflowExecRouteModelLabel: workflowExecRouteModelLabel,
            workflowExecRouteModelValue: workflowExecRouteModelValue,
            cliModelGroupsFromList: cliModelGroupsFromList,
            appendCliModelOption: appendCliModelOption,
            populateCliModelSelect: populateCliModelSelect,
            selectedCliModelProvider: selectedCliModelProvider,
            workflowStepBackendOptionsHtml: workflowStepBackendOptionsHtml,
            loadStepCliModels: loadStepCliModels,
            providerForCliModelBackend: providerForCliModelBackend,
            workflowExecRouteBackendOptionsHtml: workflowExecRouteBackendOptionsHtml,
            workflowExecCliBackendOptionsHtml: workflowExecCliBackendOptionsHtml,
            workflowExecRouteHtml: workflowExecRouteHtml,
            updateWorkflowExecBackendSelects: updateWorkflowExecBackendSelects,
            updateWorkflowExecFallbackVisibility: updateWorkflowExecFallbackVisibility,
            updateWorkflowExecCodexPrefVisibility: updateWorkflowExecCodexPrefVisibility,
            toggleWorkflowExecCodexPrefPanel: toggleWorkflowExecCodexPrefPanel,
            workflowExecModelsSourceIsVerified: workflowExecModelsSourceIsVerified,
            setWorkflowExecRouteModelHint: setWorkflowExecRouteModelHint,
            setWorkflowExecRouteIdeModelSelect: setWorkflowExecRouteIdeModelSelect,
            loadWorkflowExecRouteModels: loadWorkflowExecRouteModels,
            loadWorkflowExecRouteFallbackModels: loadWorkflowExecRouteFallbackModels,
            populateWorkflowExecRouting: populateWorkflowExecRouting,
            collectWorkflowExecRouting: collectWorkflowExecRouting,
            bindWorkflowExecRoutingControls: bindWorkflowExecRoutingControls,
            syncWorkflowExecAutoToggle: syncWorkflowExecAutoToggle,
            saveWorkflowExecRouting: saveWorkflowExecRouting
        };
    }
    window.WorkflowExecRoutingKit = { create: create };
})();
