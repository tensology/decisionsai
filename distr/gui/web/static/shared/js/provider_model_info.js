/**
 * Provider Model Info — injects ℹ️ icons next to provider dropdowns
 * and opens a recommendation modal with two-lane paid vs free comparison.
 */
(function () {
    'use strict';

    var PROVIDER_SELECT_IDS = [
        'conversational_provider', 'coding_provider', 'vision_provider', 'image_provider',
        'emptyStateLlmProvider', 'llmProvider',
        'kb-agent-orch-provider', 'kb-agent-coder-provider', 'kb-agent-sub-provider'
    ];

    var CATEGORY_META = {
        tool_calling:      { emoji: '🛠️', label: 'Tool Calling' },
        coding:            { emoji: '🔧', label: 'Coding' },
        vision:            { emoji: '👁️', label: 'Vision' },
        image_generation:  { emoji: '🎨', label: 'Image Generation' }
    };
    var CAT_ORDER = ['tool_calling', 'coding', 'vision', 'image_generation'];

    var styleInjected = false;
    var modalEl = null;
    var _pollTimer = null;
    var _pollStart = 0;
    var _currentProviderData = null;
    var _currentProviderKey = null;
    var _currentLastUpdated = null;

    /* ── CSS ───────────────────────────────────────────────────── */
    function injectStyles() {
        if (styleInjected) return;
        styleInjected = true;
        var css = [
            '.model-info-btn{background:#1a1f3a;border:1px solid #565869;border-right:none;border-radius:6px 0 0 6px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:border-color .2s,color .2s;padding:0 8px;color:#6b7280;box-sizing:border-box}',
            '.model-info-btn:hover{border-color:#10a37f;color:#10a37f}',
            '.model-info-btn svg{width:14px;height:14px;pointer-events:none}',
            '.mr-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;animation:mrFadeIn .15s ease}',
            '@keyframes mrFadeIn{from{opacity:0}to{opacity:1}}',
            '.mr-modal{background:#1a1f3a;border:1px solid rgba(255,255,255,.2);border-radius:.75rem;width:780px;max-width:95vw;max-height:85vh;overflow-y:auto;color:#ececf1;font-family:Quicksand,sans-serif;animation:mrSlideUp .2s ease}',
            '@keyframes mrSlideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}',
            '.mr-header{display:flex;align-items:center;justify-content:space-between;padding:16px 20px 12px;border-bottom:1px solid rgba(255,255,255,.1)}',
            '.mr-title{font-size:1.05rem;font-weight:600;color:#fff;margin:0}',
            '.mr-header-btns{display:flex;gap:8px;align-items:center}',
            '.mr-btn-refresh{background:transparent;border:1px solid #565869;border-radius:4px;width:30px;height:30px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:15px;transition:border-color .2s}',
            '.mr-btn-refresh:hover{border-color:#f97316}',
            '.mr-btn-refresh.spinning{animation:mrSpin 1s linear infinite;pointer-events:none;opacity:.6}',
            '@keyframes mrSpin{from{transform:rotate(0)}to{transform:rotate(360deg)}}',
            '.mr-btn-close{background:transparent;border:none;color:#9ca3af;font-size:22px;cursor:pointer;padding:0 4px;line-height:1}',
            '.mr-btn-close:hover{color:#fff}',
            '.mr-btn-back{background:transparent;border:1px solid #565869;border-radius:4px;padding:2px 10px;cursor:pointer;color:#9ca3af;font-size:.8rem;transition:border-color .2s}',
            '.mr-btn-back:hover{border-color:#f97316;color:#fff}',
            '.mr-body{padding:16px 20px}',
            /* Category card (list view) — two columns */
            '.mr-category{background:#152054;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:14px 16px;margin-bottom:10px;cursor:pointer;transition:border-color .2s}',
            '.mr-category:hover{border-color:#f97316}',
            '.mr-cat-header{font-size:.85rem;font-weight:600;color:#9ca3af;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}',
            '.mr-cat-arrow{color:#565869;font-size:.8rem}',
            '.mr-lanes{display:grid;grid-template-columns:1fr 1fr;gap:12px}',
            '.mr-lane{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:6px;padding:10px 12px}',
            '.mr-lane-label{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}',
            '.mr-lane-paid .mr-lane-label{color:#f59e0b}',
            '.mr-lane-free .mr-lane-label{color:#22c55e}',
            '.mr-lane-model{font-size:.9rem;font-weight:600;color:#fff;margin-bottom:2px}',
            '.mr-lane-cost{font-size:.75rem;color:#9ca3af;margin-bottom:6px}',
            '.mr-lane-null{font-size:.8rem;color:#6b7280;font-style:italic;padding:8px 0}',
            /* Compact quality bars for list view */
            '.mr-qc-row{display:flex;align-items:center;gap:6px;margin-bottom:3px}',
            '.mr-qc-label{font-size:.65rem;color:#6b7280;width:52px;flex-shrink:0}',
            '.mr-qc-bar{height:4px;background:#1e2a5a;border-radius:2px;flex:1;overflow:hidden}',
            '.mr-qc-fill{height:100%;border-radius:2px}',
            '.mr-qc-score{font-size:.65rem;color:#9ca3af;width:16px;text-align:right;flex-shrink:0}',
            /* Detail view */
            '.mr-detail-section{margin-bottom:16px}',
            '.mr-detail-label{font-size:.72rem;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}',
            '.mr-detail-lanes{display:grid;grid-template-columns:1fr 1fr;gap:16px}',
            '.mr-detail-lane{background:#152054;border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:14px}',
            '.mr-detail-lane h3{font-size:.95rem;font-weight:700;color:#fff;margin:0 0 4px}',
            '.mr-detail-lane .lane-tag{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;display:inline-block;padding:2px 8px;border-radius:3px}',
            '.mr-detail-lane .lane-tag-paid{background:rgba(245,158,11,.15);color:#f59e0b}',
            '.mr-detail-lane .lane-tag-free{background:rgba(34,197,94,.15);color:#22c55e}',
            '.mr-detail-stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px}',
            '.mr-detail-stat{background:rgba(255,255,255,.04);border-radius:4px;padding:6px 8px}',
            '.mr-detail-stat-label{font-size:.65rem;color:#6b7280}',
            '.mr-detail-stat-value{font-size:.85rem;font-weight:600;color:#fff;margin-top:1px}',
            '.mr-quality-row{display:flex;align-items:center;gap:8px;margin-bottom:5px}',
            '.mr-quality-label{font-size:.72rem;color:#9ca3af;width:90px;flex-shrink:0}',
            '.mr-quality-bar{height:6px;background:#1e2a5a;border-radius:3px;flex:1;overflow:hidden}',
            '.mr-quality-fill{height:100%;border-radius:3px;transition:width .3s}',
            '.mr-quality-score{font-size:.72rem;color:#fff;width:28px;text-align:right;flex-shrink:0}',
            '.mr-source-link{display:block;padding:5px 8px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:4px;margin-bottom:4px;text-decoration:none;color:#60a5fa;font-size:.75rem;transition:border-color .2s}',
            '.mr-source-link:hover{border-color:#f97316;color:#f97316}',
            '.mr-lane-desc{font-size:.75rem;color:#d1d5db;margin-bottom:6px}',
            '.mr-footer{padding:10px 20px 14px;text-align:right;font-size:.72rem;color:#6b7280;border-top:1px solid rgba(255,255,255,.05)}',
            '.mr-loading{text-align:center;padding:40px 20px;color:#9ca3af}',
            '.mr-loading .spinner{display:inline-block;width:24px;height:24px;border:3px solid #565869;border-top-color:#f97316;border-radius:50%;animation:mrSpin .8s linear infinite;margin-bottom:10px}',
            '.mr-empty{text-align:center;padding:40px 20px;color:#9ca3af}',
            '.mr-empty button{margin-top:12px;padding:8px 20px;background:#f97316;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.85rem}',
            '.mr-empty button:hover{background:#ea580c}'
        ].join('\n');
        var style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);
    }

    /* ── Icon injection ───────────────────────────────────────── */
    function createInfoButton(selectEl) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'model-info-btn';
        btn.title = 'Model recommendations';
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
        btn.addEventListener('click', function () {
            openRecommendationModal(selectEl.value);
        });
        return btn;
    }

    function injectInfoIcons() {
        injectStyles();
        for (var i = 0; i < PROVIDER_SELECT_IDS.length; i++) {
            var id = PROVIDER_SELECT_IDS[i];
            var sel = document.getElementById(id);
            if (!sel) continue;
            var parent = sel.parentElement;
            if (!parent) continue;
            if (parent.querySelector('.model-info-btn')) continue;

            // Read the select's rendered height so the button matches exactly
            var selHeight = sel.offsetHeight || sel.getBoundingClientRect().height;

            var wrapper = document.createElement('div');
            wrapper.style.cssText = 'display:flex;align-items:stretch;width:100%';

            var btn = createInfoButton(sel);
            if (selHeight) {
                btn.style.height = selHeight + 'px';
            }

            // Remove left border/radius on select, it joins to the button
            sel.style.borderRadius = '0 6px 6px 0';
            sel.style.borderLeft = 'none';

            parent.replaceChild(wrapper, sel);
            wrapper.appendChild(btn);
            wrapper.appendChild(sel);
        }
    }

    /* ── Modal open / fetch ────────────────────────────────────── */
    function openRecommendationModal(providerValue) {
        injectStyles();
        showModal(buildLoadingHTML());
        fetch('/api/llms/recommendations?provider=' + encodeURIComponent(providerValue || ''))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var providers = data.providers || {};
                var keys = Object.keys(providers);
                if (keys.length === 0) {
                    updateModalBody(buildEmptyHTML());
                } else {
                    var key = keys[0];
                    _currentProviderData = providers[key];
                    _currentProviderKey = key;
                    _currentLastUpdated = data.last_updated;
                    updateModalBody(buildListView(_currentProviderData, key, data.last_updated));
                }
            })
            .catch(function () {
                updateModalBody('<div class="mr-empty">Failed to load recommendations.</div>');
            });
    }

    /* ── Modal DOM helpers ─────────────────────────────────────── */
    function showModal(bodyHTML) {
        closeModal();
        var overlay = document.createElement('div');
        overlay.className = 'mr-overlay';
        overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
        var modal = document.createElement('div');
        modal.className = 'mr-modal';
        modal.innerHTML = bodyHTML;
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        modalEl = overlay;
        document.addEventListener('keydown', onEscape);
    }

    function updateModalBody(html) {
        if (!modalEl) return;
        var modal = modalEl.querySelector('.mr-modal');
        if (modal) modal.innerHTML = html;
    }

    function closeModal() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
        if (modalEl) { modalEl.remove(); modalEl = null; }
        _currentProviderData = null;
        document.removeEventListener('keydown', onEscape);
    }

    function onEscape(e) { if (e.key === 'Escape') closeModal(); }

    /* ── HTML builders ─────────────────────────────────────────── */
    function buildLoadingHTML() {
        return '<div class="mr-loading"><div class="spinner"></div><div>Loading recommendations…</div></div>';
    }

    function buildEmptyHTML() {
        return '<div class="mr-header"><span class="mr-title">Model Recommendations</span><div class="mr-header-btns"><button class="mr-btn-close" id="mrCloseBtn">&times;</button></div></div>' +
            '<div class="mr-empty"><p>No recommendations available yet.</p><button onclick="window._mrRefresh()">Generate Now</button></div>';
    }

    /* ── List view (two-lane category cards) ────────────────────── */
    function isOllamaProvider(providerKey) {
        return (providerKey || '').toLowerCase() === 'ollama';
    }

    function buildListView(providerData, providerKey, lastUpdated) {
        var displayName = (providerData && providerData.display_name) || providerKey || 'Provider';
        var cats = (providerData && providerData.categories) || {};
        var isOllama = isOllamaProvider(providerKey);

        var html = '<div class="mr-header"><span class="mr-title">' + esc(displayName) + ' — Recommended Models</span>';
        html += '<div class="mr-header-btns"><button class="mr-btn-refresh" id="mrRefreshBtn" title="Refresh recommendations">🔄</button>';
        html += '<button class="mr-btn-close" id="mrCloseBtn">&times;</button></div></div>';
        html += '<div class="mr-body">';

        for (var i = 0; i < CAT_ORDER.length; i++) {
            var catKey = CAT_ORDER[i];
            var meta = CATEGORY_META[catKey];
            var entry = cats[catKey];
            if (!entry) continue;

            html += '<div class="mr-category" data-cat="' + catKey + '">';
            html += '<div class="mr-cat-header"><span>' + meta.emoji + ' ' + meta.label + '</span><span class="mr-cat-arrow">▸ details</span></div>';

            if (isOllama) {
                // Ollama: single column, no paid/free split — show the free lane as the recommendation
                var model = entry.free || entry.paid;
                if (model) {
                    html += '<div style="padding:4px 0">';
                    html += '<div class="mr-lane-model">' + esc(model.model_name || model.model_id || '') + '</div>';
                    html += '<div class="mr-lane-cost" style="color:#22c55e">Free / Local</div>';
                    var q = model.quality;
                    if (q) {
                        html += buildCompactBar('Overall', q.overall);
                        html += buildCompactBar('Speed', q.speed);
                        html += buildCompactBar('Reasoning', q.reasoning);
                    }
                    html += '</div>';
                } else {
                    html += '<div class="mr-lane-null">No recommendation</div>';
                }
            } else {
                html += '<div class="mr-lanes">';
                html += buildLaneCard(entry.paid, 'paid');
                html += buildLaneCard(entry.free, 'free');
                html += '</div>';
            }
            html += '</div>';
        }

        html += '</div>';
        if (lastUpdated) {
            var d = new Date(lastUpdated);
            html += '<div class="mr-footer">Last updated: ' + d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) + '</div>';
        }
        return html;
    }

    function buildLaneCard(lane, type) {
        var labelClass = type === 'paid' ? 'mr-lane-paid' : 'mr-lane-free';
        var icon = type === 'paid' ? '💰' : '🖥️';
        var label = type === 'paid' ? 'Paid (API Key)' : 'Free / Local';
        var html = '<div class="mr-lane ' + labelClass + '">';
        html += '<div class="mr-lane-label">' + icon + ' ' + label + '</div>';
        if (!lane) {
            html += '<div class="mr-lane-null">Not available</div>';
            html += '</div>';
            return html;
        }
        html += '<div class="mr-lane-model">' + esc(lane.model_name || lane.model_id || '') + '</div>';
        html += '<div class="mr-lane-cost">' + formatCostBadge(lane) + '</div>';
        // Compact quality bars
        var q = lane.quality;
        if (q) {
            html += buildCompactBar('Overall', q.overall);
            html += buildCompactBar('Speed', q.speed);
            html += buildCompactBar('Reasoning', q.reasoning);
            html += buildCompactBar('Cost Eff.', q.cost_efficiency);
        }
        html += '</div>';
        return html;
    }

    function buildCompactBar(label, score) {
        score = Math.max(0, Math.min(10, score || 0));
        var pct = score * 10;
        var color = score >= 8 ? '#22c55e' : score >= 6 ? '#eab308' : '#ef4444';
        return '<div class="mr-qc-row"><span class="mr-qc-label">' + label + '</span>' +
            '<div class="mr-qc-bar"><div class="mr-qc-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
            '<span class="mr-qc-score">' + score + '</span></div>';
    }

    function formatCostBadge(lane) {
        if (!lane || !lane.pricing) return '';
        var p = lane.pricing;
        if (p.per_prompt_est) return esc(p.per_prompt_est) + '/prompt';
        if (p.input === 0 && p.output === 0) return 'Free / Local';
        return '~$' + ((p.input + p.output) / 2000).toFixed(4) + '/prompt';
    }

    /* ── Detail view (two-lane deep-dive) ──────────────────────── */
    function showDetailView(catKey) {
        if (!_currentProviderData) return;
        var cats = _currentProviderData.categories || {};
        var entry = cats[catKey];
        if (!entry) return;
        var meta = CATEGORY_META[catKey] || { emoji: '', label: catKey };
        var isOllama = isOllamaProvider(_currentProviderKey);

        var html = '<div class="mr-header">';
        html += '<div style="display:flex;align-items:center;gap:8px"><button class="mr-btn-back" id="mrBackBtn">← Back</button>';
        html += '<span class="mr-title">' + meta.emoji + ' ' + esc(meta.label) + '</span></div>';
        html += '<div class="mr-header-btns"><button class="mr-btn-close" id="mrCloseBtn">&times;</button></div></div>';
        html += '<div class="mr-body">';

        if (isOllama) {
            // Ollama: single-column detail, no paid/free split
            var model = entry.free || entry.paid;
            if (model) {
                html += buildDetailLane(model, 'free');
            } else {
                html += '<div style="padding:20px 0;color:#6b7280;font-style:italic">No recommendation available.</div>';
            }
        } else {
            html += '<div class="mr-detail-lanes">';
            html += buildDetailLane(entry.paid, 'paid');
            html += buildDetailLane(entry.free, 'free');
            html += '</div>';
        }

        html += '</div>';
        if (_currentLastUpdated) {
            var d = new Date(_currentLastUpdated);
            html += '<div class="mr-footer">Last updated: ' + d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) + '</div>';
        }
        updateModalBody(html);
    }

    function buildDetailLane(lane, type) {
        var tagClass = type === 'paid' ? 'lane-tag-paid' : 'lane-tag-free';
        var icon = type === 'paid' ? '💰' : '🖥️';
        var label = type === 'paid' ? 'Paid (API Key)' : 'Free / Local';
        var html = '<div class="mr-detail-lane">';
        html += '<span class="lane-tag ' + tagClass + '">' + icon + ' ' + label + '</span>';
        if (!lane) {
            html += '<div style="padding:20px 0;color:#6b7280;font-style:italic">No ' + type + ' option available for this provider.</div></div>';
            return html;
        }
        html += '<h3>' + esc(lane.model_name || lane.model_id || '') + '</h3>';
        html += '<div style="font-size:.75rem;color:#6b7280;margin-bottom:8px">' + esc(lane.model_id || '') + '</div>';

        if (lane.description) {
            html += '<div class="mr-lane-desc">' + esc(lane.description) + '</div>';
        }

        // Stats grid
        html += '<div class="mr-detail-stat-grid">';
        html += buildStat('Cost/prompt', formatCostBadge(lane));
        html += buildStat('Released', lane.released || 'Unknown');
        html += buildStat('Context', lane.context_window ? fmtNum(lane.context_window) + ' tokens' : 'N/A');
        var p = lane.pricing;
        if (p && (p.input > 0 || p.output > 0)) {
            html += buildStat('Per 1M tokens', '$' + p.input.toFixed(2) + ' in / $' + p.output.toFixed(2) + ' out');
        }
        html += '</div>';

        // Quality bars
        var q = lane.quality;
        if (q) {
            html += '<div style="margin-bottom:10px">';
            html += buildQualityBar('Overall', q.overall);
            html += buildQualityBar('Speed', q.speed);
            html += buildQualityBar('Reasoning', q.reasoning);
            html += buildQualityBar('Cost Efficiency', q.cost_efficiency);
            html += '</div>';
        }

        // Sources
        var sources = lane.sources;
        if (sources && sources.length) {
            for (var i = 0; i < sources.length; i++) {
                var s = sources[i];
                html += '<a class="mr-source-link" href="' + escAttr(s.url) + '" target="_blank" rel="noopener">' + esc(s.title || s.url) + ' ↗</a>';
            }
        }

        html += '</div>';
        return html;
    }

    /* ── Formatting helpers ───────────────────────────────────── */
    function buildStat(label, value) {
        return '<div class="mr-detail-stat"><div class="mr-detail-stat-label">' + esc(label) + '</div><div class="mr-detail-stat-value">' + esc(value) + '</div></div>';
    }

    function buildQualityBar(label, score) {
        score = Math.max(0, Math.min(10, score || 0));
        var pct = score * 10;
        var color = score >= 8 ? '#22c55e' : score >= 6 ? '#eab308' : '#ef4444';
        return '<div class="mr-quality-row"><span class="mr-quality-label">' + esc(label) + '</span>' +
            '<div class="mr-quality-bar"><div class="mr-quality-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
            '<span class="mr-quality-score">' + score + '/10</span></div>';
    }

    function fmtNum(n) {
        if (n >= 1000000) return (n / 1000000).toFixed(0) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(0) + 'k';
        return String(n);
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function escAttr(s) {
        return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /* ── Refresh wiring ────────────────────────────────────────── */
    function triggerRefresh() {
        // Show generating state — just the centered spinner, no spinning refresh button
        updateModalBody(
            '<div class="mr-header"><span class="mr-title">Model Recommendations</span>' +
            '<div class="mr-header-btns"><button class="mr-btn-close" id="mrCloseBtn">&times;</button></div></div>' +
            '<div class="mr-loading"><div class="spinner"></div>' +
            '<div>Generating recommendations…</div>' +
            '<div style="font-size:.72rem;color:#6b7280;margin-top:8px">This takes a few minutes (web search + LLM per provider)</div></div>'
        );
        fetch('/api/llms/recommendations/refresh', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'already_running' || data.status === 'started') {
                    startPolling();
                }
            })
            .catch(function () {
                updateModalBody('<div class="mr-empty">Failed to start generation.</div>');
            });
    }

    function startPolling() {
        if (_pollTimer) return;
        _pollStart = Date.now();
        _pollTimer = setInterval(function () {
            if (Date.now() - _pollStart > 360000) {
                clearInterval(_pollTimer); _pollTimer = null;
                updateModalBody(
                    '<div class="mr-header"><span class="mr-title">Model Recommendations</span>' +
                    '<div class="mr-header-btns"><button class="mr-btn-close" id="mrCloseBtn">&times;</button></div></div>' +
                    '<div class="mr-empty"><p>Generation is taking longer than expected. Check back later.</p></div>'
                );
                return;
            }
            fetch('/api/llms/recommendations')
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var providers = data.providers || {};
                    var keys = Object.keys(providers);
                    // Data is ready when we have at least one provider with categories
                    if (keys.length > 0 && data.last_updated) {
                        clearInterval(_pollTimer); _pollTimer = null;
                        _currentProviderData = providers[keys[0]];
                        _currentProviderKey = keys[0];
                        _currentLastUpdated = data.last_updated;
                        updateModalBody(buildListView(_currentProviderData, keys[0], data.last_updated));
                    }
                });
        }, 5000);
    }

    /* ── Global hooks ──────────────────────────────────────────── */
    window._mrClose = closeModal;
    window._mrRefresh = triggerRefresh;

    /* ── Delegated click handler ───────────────────────────────── */
    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t) return;
        if (t.id === 'mrCloseBtn') { closeModal(); return; }
        if (t.id === 'mrRefreshBtn') { triggerRefresh(); return; }
        if (t.id === 'mrBackBtn') {
            if (_currentProviderData) updateModalBody(buildListView(_currentProviderData, _currentProviderKey, _currentLastUpdated));
            return;
        }
        var card = t.closest('.mr-category');
        if (card && card.dataset.cat) {
            showDetailView(card.dataset.cat);
        }
    });

    /* ── Public API ────────────────────────────────────────────── */
    window.injectInfoIcons = injectInfoIcons;
    window.openRecommendationModal = openRecommendationModal;

    /* ── Auto-init ─────────────────────────────────────────────── */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectInfoIcons);
    } else {
        injectInfoIcons();
    }
})();
