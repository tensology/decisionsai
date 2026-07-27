(function () {
    "use strict";

    function position(index, total) {
        var angle = (-Math.PI / 2) + ((2 * Math.PI * index) / total);
        var radius = 40;
        return {
            left: (50 + radius * Math.cos(angle)).toFixed(2) + "%",
            top: (50 + radius * Math.sin(angle)).toFixed(2) + "%"
        };
    }

    function statusClass(status) {
        if (status === "running") return "wf-loop-step-status--running";
        if (status === "waiting") return "wf-loop-step-status--waiting";
        if (status === "passed" || status === "completed") return "wf-loop-step-status--done";
        if (status === "failed" || status === "cancelled") return "wf-loop-step-status--failed";
        return "";
    }

    function instructionPreview(step) {
        var text = String((step && (step.instruction || step.description)) || "").replace(/\s+/g, " ").trim();
        if (!text) return "No instruction yet";
        return text.length > 72 ? text.substring(0, 72) + "…" : text;
    }

    function render(options) {
        var element = options.element;
        var steps = options.steps || [];
        if (!element) return;
        if (!steps.length) {
            element.innerHTML = '<div class="wf-loop-empty text-sm text-gray-500 py-10 text-center">No steps yet. Add the first step to start building the loop.</div>';
            return;
        }
        var nodes = steps.map(function (step, index) {
            var pos = position(index, steps.length);
            var selected = options.expandedStepId === step.id;
            var stateClass = statusClass(step.status);
            return '<button type="button" class="wf-loop-ring-node' + (selected ? " is-selected" : "") + (stateClass ? " " + stateClass : "") + '" data-step-id="' + step.id + '" style="left:' + pos.left + ";top:" + pos.top + '">' +
                '<div class="wf-loop-ring-node-head">' +
                    '<span class="wf-loop-ring-node-ball" aria-label="Step ' + (index + 1) + '">' + (index + 1) + "</span>" +
                    options.toolIcons(step) +
                "</div>" +
                '<span class="wf-loop-ring-node-title">' + options.escape(step.name || ("Step " + (index + 1))) + "</span>" +
                '<span class="wf-loop-ring-node-preview">' + options.escape(instructionPreview(step)) + "</span>" +
            "</button>";
        }).join("");
        element.innerHTML =
            '<div class="wf-loop-ring-stage">' +
                '<svg class="wf-loop-ring-track" viewBox="0 0 200 200" aria-hidden="true">' +
                    '<circle cx="100" cy="100" r="76" fill="none" stroke="rgba(249,115,22,0.28)" stroke-width="2"></circle>' +
                "</svg>" +
                '<div class="wf-loop-ring-center-label" aria-label="Orchestrator">' + options.orchestratorIcon + "<span>Orchestrator</span></div>" +
                '<div class="wf-loop-ring-nodes">' + nodes + "</div>" +
            "</div>";
        options.syncSelection();
    }

    function bind(options) {
        var ring = options.element;
        if (!ring || options.locked) return;
        var draggingStepId = null;
        var didDrag = false;
        ring.querySelectorAll(".wf-loop-ring-node").forEach(function (node) {
            var stepId = parseInt(node.dataset.stepId, 10);
            if (!stepId) return;
            node.setAttribute("draggable", "true");
            node.addEventListener("dragstart", function (event) {
                didDrag = false;
                draggingStepId = stepId;
                node.classList.add("wf-loop-step-dragging");
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", String(stepId));
                }
            });
            node.addEventListener("drag", function () { didDrag = true; });
            node.addEventListener("dragend", function () {
                draggingStepId = null;
                ring.querySelectorAll(".wf-loop-step-drop-target").forEach(function (item) {
                    item.classList.remove("wf-loop-step-drop-target");
                });
                ring.querySelectorAll(".wf-loop-step-dragging").forEach(function (item) {
                    item.classList.remove("wf-loop-step-dragging");
                });
                window.setTimeout(function () { didDrag = false; }, 0);
            });
            node.addEventListener("dragover", function (event) {
                if (!draggingStepId || draggingStepId === stepId) return;
                event.preventDefault();
                node.classList.add("wf-loop-step-drop-target");
                if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            });
            node.addEventListener("dragleave", function () {
                node.classList.remove("wf-loop-step-drop-target");
            });
            node.addEventListener("drop", function (event) {
                event.preventDefault();
                node.classList.remove("wf-loop-step-drop-target");
                if (!draggingStepId || draggingStepId === stepId) return;
                options.reorder(draggingStepId, stepId);
            });
            node.addEventListener("click", function (event) {
                if (didDrag) {
                    event.preventDefault();
                    return;
                }
                options.select(stepId);
            });
        });
    }

    window.DecisionsWorkflowRingView = { render: render, bind: bind };
})();
