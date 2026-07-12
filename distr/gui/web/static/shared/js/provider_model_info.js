/**
 * Provider Model Info - injects info icons next to provider dropdowns
 * and opens the shared LLM benchmark modal.
 */
(function () {
    'use strict';

    var PROVIDER_BENCHMARK_SELECTS = {
        conversational_provider: { type: 'conversational', modelId: 'conversational_model' },
        coding_provider: { type: 'coding', modelId: 'coding_model' },
        vision_provider: { type: 'vision', modelId: 'vision_model' },
        image_provider: { type: 'image', modelId: 'image_model' },
        video_provider: { type: 'video', modelId: 'video_model' },
        workflow_provider: { type: 'workflow', modelId: 'workflow_model' },
        computer_use_provider: { type: 'computer_use', modelId: 'computer_use_model' },
        emptyStateLlmProvider: { type: 'conversational', modelId: 'emptyStateLlmModel' },
        llmProvider: { type: 'conversational', modelId: 'llmModel' },
        chatConfigLlmProvider: { type: 'conversational', modelId: 'chatConfigLlmModel' }
    };

    var PROVIDER_SELECT_IDS = Object.keys(PROVIDER_BENCHMARK_SELECTS);
    var styleInjected = false;

    function ensureBenchmarkNamespace() {
        window.DecisionsBenchmark = window.DecisionsBenchmark || {};
        if (typeof window.DecisionsBenchmark.open === 'function') return;

        window.DecisionsBenchmark.open = function (type, options) {
            var opts = options || {};
            var detail = {
                type: type,
                provider: opts.provider || '',
                model: opts.model || '',
                compareProvider: opts.compareProvider || opts.provider || '',
                compareModel: opts.compareModel || opts.model || ''
            };

            if (typeof window.openLLMBenchmarkModal === 'function') {
                window.openLLMBenchmarkModal(type, detail);
                return true;
            }

            try {
                window.dispatchEvent(new CustomEvent('open-llm-benchmark', { detail: detail }));
                return true;
            } catch (err) {
                console.warn('Unable to open LLM benchmark modal.', err);
                return false;
            }
        };
    }

    function hasSharedModelPopupStyles() {
        return Boolean(
            document.querySelector("link[href*='model_popups.css']") ||
            document.querySelector('style[data-model-popups-styles]')
        );
    }

    function injectStyles() {
        if (styleInjected) return;
        if (hasSharedModelPopupStyles()) {
            styleInjected = true;
            return;
        }

        styleInjected = true;
        var css = [
            '.model-info-btn{background:#1a1f3a;border:1px solid #565869;border-left:none;border-radius:0 6px 6px 0;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:border-color .2s,color .2s;padding:0 8px;color:#6b7280;box-sizing:border-box}',
            '.model-info-btn:hover{border-color:#10a37f;color:#10a37f}',
            '.model-info-btn svg{width:14px;height:14px;pointer-events:none}'
        ].join('\n');
        var style = document.createElement('style');
        style.setAttribute('data-model-popups-styles', '1');
        style.textContent = css;
        document.head.appendChild(style);
    }

    function getSelectedModel(target) {
        var modelEl = target && target.modelId ? document.getElementById(target.modelId) : null;
        return modelEl && modelEl.value ? modelEl.value : '';
    }

    function openBenchmarkModal(target, provider, model) {
        ensureBenchmarkNamespace();
        return window.DecisionsBenchmark.open(target.type, {
            provider: provider || '',
            model: model || '',
            compareProvider: provider || '',
            compareModel: model || ''
        });
    }

    function createInfoButton(selectEl, target) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'model-info-btn';
        btn.title = 'Model benchmarks';
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
        btn.addEventListener('click', function () {
            openBenchmarkModal(target, selectEl.value, getSelectedModel(target));
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
            if (!parent || parent.querySelector('.model-info-btn')) continue;

            var target = PROVIDER_BENCHMARK_SELECTS[id];
            var selHeight = sel.offsetHeight || sel.getBoundingClientRect().height;
            var wrapper = document.createElement('div');
            var btn = createInfoButton(sel, target);

            wrapper.style.cssText = 'display:flex;align-items:stretch;width:100%';
            if (selHeight) {
                btn.style.height = selHeight + 'px';
            }

            sel.style.borderRadius = '6px 0 0 6px';
            sel.style.borderRight = 'none';
            sel.style.flex = '1 1 0';
            sel.style.minWidth = '0';

            parent.replaceChild(wrapper, sel);
            wrapper.appendChild(sel);
            wrapper.appendChild(btn);
        }
    }

    ensureBenchmarkNamespace();
    window.injectInfoIcons = injectInfoIcons;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectInfoIcons);
    } else {
        injectInfoIcons();
    }
})();
