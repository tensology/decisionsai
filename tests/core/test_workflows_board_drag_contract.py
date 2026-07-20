from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_JS = ROOT / "distr/gui/web/static/workflows/js/workflows.js"
WORKFLOWS_HTML = ROOT / "distr/gui/web/templates/workflows/workflows.html"
WORKFLOWS_PY = ROOT / "distr/gui/web/routes/settings/workflows.py"
CHAT_PY = ROOT / "distr/gui/web/routes/chat.py"
KANBAN_TICKET_JS = ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js"


def test_workflows_board_rows_use_kanban_list_with_workflow_mouse_drag():
    workflows = WORKFLOWS_JS.read_text(encoding="utf-8")
    kanban = KANBAN_TICKET_JS.read_text(encoding="utf-8")
    render_block = workflows.split("function renderWorkflowBoardTickets(board, selected, message)", 1)[1].split(
        "function getSelectedBoardLocalId", 1
    )[0]

    assert "disableListDrag: true" in render_block
    assert "ticketUi.createTicketListRow" in render_block
    assert "bindWorkflowBoardListRow(row, ticket, lane, selected, board)" in render_block
    assert "bindTicketListRowDrag(row, ticket.id, canDrag && !listOpts.disableListDrag)" in kanban


def test_workflows_board_drag_ghost_clones_row_inside_ticket_surface_shell():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    ghost_block = js.split("function createWorkflowBoardDragGhost(row, evt)", 1)[1].split(
        "function bindWorkflowTicketRowDragSources", 1
    )[0]

    assert "row.cloneNode(true)" in ghost_block
    assert "wf-board-ticket-drag-ghost-shell" in ghost_block
    assert "wf-ticket-list-surface" in ghost_block
    assert "wf-board-ticket-drag-ghost-shell" in html
    assert "upgradeWorkflowBoardTicketGrip" in js


def test_workflow_queue_rows_define_list_drag_handle_helper():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    assert "function workflowListDragHandleHtml(draggable, title)" in js
    assert "workflowListDragHandleHtml(canReorder" in js


def test_workflows_board_drag_uses_direct_grip_mouse_handlers():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert "function bindWorkflowBoardGripMouseDrag(handle, row)" in js
    assert "bindWorkflowBoardGripMouseDrag(handle, row)" in js.split(
        "function upgradeWorkflowBoardTicketGrip(row, canDrag)", 1
    )[1].split("function normalizeWorkflowPriority", 1)[0]
    assert "startWorkflowBoardGripMouseTracking" in js
    assert "beginWorkflowBoardMouseDrag" in js


def test_workflows_websocket_handler_is_typed_once():
    workflows_py = WORKFLOWS_PY.read_text(encoding="utf-8")
    chat_py = CHAT_PY.read_text(encoding="utf-8")

    assert '@router.websocket("/workflows/ws")' in workflows_py
    assert '@router.websocket("/ws/workflows")' in workflows_py
    assert '@router.websocket("/workflows/ws")' not in chat_py


def test_workflow_queue_remove_uses_confirmation_and_keyboard_helpers():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")

    assert "wf-workflow-ticket-remove" in js
    assert 'id="wf-delete-btn"' not in html
    assert "function removeWorkflowQueueTicket(ticketId, options)" in js
    assert "function selectWorkflowQueueTicket(ticketId, rowEl)" in js
    assert 'namespace: "workflow-queue"' in js
    assert "function finishDetailTabRestore()" in js
    assert "var shouldRestoreDetailTabOnce = true" in js


def test_workflows_ticket_modal_footer_has_save_delete_only():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert 'id="kb-modal-save"' in html
    assert 'id="kb-modal-delete"' in html
    assert "kb-modal-act-discuss" not in js


def test_workflow_context_menu_is_configure_duplicate_delete_only():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    menu_block = js.split("function ensureWorkflowContextMenu()", 1)[1].split(
        "function openWorkflowContextMenu", 1
    )[0]

    assert 'data-action="configure"' in menu_block
    assert 'data-action="duplicate"' in menu_block
    assert 'data-action="delete"' in menu_block
    assert 'data-action="run"' not in menu_block
    assert 'data-action="export"' not in menu_block
    assert 'data-action="download"' not in menu_block
    assert 'data-action="purge-all"' not in menu_block


def test_workflow_create_modal_is_compact_with_large_description():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    assert 'id="wf-create-modal"' in html
    assert "wf-create-modal" in html
    assert "640px" in html
    assert 'id="wf-builder-desc"' in html
    assert 'rows="8"' in html
    assert 'min-h-[200px]' in html
    assert 'id="wf-create-cancel"' in html
    assert 'id="wf-create-btn"' in html


def test_workflow_loop_ui_has_ring_and_list_views():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert 'id="wf-loop-ring-view"' in html
    assert 'id="wf-loop-list-view"' in html
    assert 'data-loop-view="ring"' in html
    assert 'data-loop-view="list"' in html
    assert "WORKFLOW_LOOP_MAX_STEPS = 14" in js
    assert "function renderLoopRingView(steps)" in js
    assert 'id="wf-loop-step-modal"' in html
    assert 'id="wf-loop-step-guardrail"' in html
    assert 'id="wf-loop-step-validation"' in html
    assert 'data-loop-step-tab="execution-route"' in html
    assert 'id="wf-loop-step-route-enabled"' in html
    assert 'id="wf-loop-step-route-select"' in html
    assert 'id="wf-loop-step-route-preview"' in html
    assert 'data-loop-step-tab="validation"' in html
    assert 'id="wf-loop-step-determine-skills"' in html
    assert 'id="wf-loop-step-skills-list"' in html
    assert 'id="wf-loop-step-tools-list"' in html
    assert "wf-loop-step-tools-grid" in js
    assert "wf-loop-ring-node-head" in js
    assert 'emoji: "🎭"' in js
    assert "wf-loop-step-layout" in html
    assert "function openLoopStepModal(opts)" in js
    assert "function renderLoopStepSkillsPicker" in js
    assert "function renderLoopStepExecutionRouteEditor" in js
    assert "function loopStepExecutionRouteValue" in js
    assert "function loopStepChosenModels" in js
    assert "function determineLoopStepSkills" in js
    assert "suggestion.refined_instruction" in js
    assert "suggestion.guardrail" in js
    assert "suggestion.validation_prompt" in js
    assert "wf-loop-step-action-type" not in html
    assert "wf-loop-step-wait" not in html
    assert 'id="wf-loop-step-other-tool-wrap"' not in html
    assert 'id="wf-loop-step-other-tool"' not in html
    assert '{ id: "agent", label: "Agent"' in js
    assert '{ id: "python", label: "Python"' in js
    assert '{ id: "shell", label: "Shell"' in js
    assert '{ id: "http", label: "HTTP"' in js
    assert '{ id: "macro", label: "Macro"' in js
    assert 'id="wf-loop-preset-mode"' in html
    assert 'id="wf-loop-preset-capacity"' in html
    assert 'id="wf-loop-preset-import-btn"' in html
    assert 'id="wf-loop-preset-export-btn"' in html
    assert 'id="wf-loop-preset-save-btn"' in html
    assert "function syncLoopStepOtherToolVisibility" not in js
    assert "function syncLoopPresetCapacityHint" in js
    assert "function exportCurrentLoopPreset" in js
    assert "function importLoopPresetFile" in js
    assert "function saveCurrentLoopAsPreset" in js
    assert "mode: mode" in js or "mode: mode," in js


def test_complexity_model_dropdowns_are_the_pin_source_of_truth_with_auto_toggle():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    routes = WORKFLOWS_PY.read_text(encoding="utf-8")

    assert 'class="wf-exec-model' in js
    assert 'class="wf-exec-auto ' in js
    assert "function syncWorkflowExecAutoToggle(level, root)" in js
    assert 'model.value = "auto"' in js
    assert "model.dataset.lastPinnedModel = model.value" in js
    assert 'String(model.dataset.lastPinnedModel || "")' in js
    assert '"model_provider": (settings.get(f"project_cli_{level}_model_provider")' in routes
    assert 'settings[f"project_cli_{level}_model_provider"]' in routes
    assert "Keeping the pinned model visible." in js
    assert ".wf-exec-auto-label" in html


def test_workflows_detail_tabs_use_loop_and_conditional_runs():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert 'data-tab="loop"' in html
    assert 'data-tab="activity"' not in html
    assert 'id="wf-tab-tickets"' in html
    assert 'id="wf-tab-loop"' in html
    assert 'id="wf-tab-cli"' in html
    assert '.wf-tab-content.hidden' in html
    assert 'display: none !important' in html
    assert 'id="wf-tab-activity"' not in html
    assert 'id="wf-steps-list"' in html
    assert 'id="wf-runs-tab-btn"' in html
    assert "Activity log" not in html
    assert 'id="wf-loop-feed-panel"' in html
    assert 'wf-loop-feed-title' not in html
    assert 'id="wf-detail-footer"' not in html
    assert "function readPersistedWorkflowDetailTab()" in js
    assert "function syncWorkflowRunsTabVisibility()" in js
    assert html.index('data-tab="loop"') < html.index('data-tab="tickets"')
    assert 'localStorage.getItem("wf_detail_tab_v2") || "loop"' in js
    assert 'tab === "loop" && !workflowLoopHasSelectedTicket()' not in js
    assert 'btn.title = hasTicketContext' in js
    assert "var showAddAll = boardHasProject;" in js
    assert "showAddToWorkflow: boardHasProject" in js


def test_workflow_queue_row_exposes_loop_then_play_with_visible_loop_ticket_context():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert 'id="wf-tab-run-timer"' in html
    assert "wf-workflow-ticket-loop" in js
    assert "wf-workflow-ticket-run" in js
    assert js.index("wf-workflow-ticket-loop") < js.index("wf-workflow-ticket-run")
    assert "openWorkflowTicketLoop(ticketId, rowEl)" in js
    assert 'id="wf-loop-run-ticket-context"' in html
    assert "Active workflow run" in js
    assert 'renderLoopTicketContextElement(mainEl, contextRun, "main")' in js
    assert "wf-loop-start-ticket" in js
    assert "wf-loop-continue-ticket" in js


def test_loop_mission_control_keeps_tickets_visible_but_binds_execution_to_active_run():
    html = WORKFLOWS_HTML.read_text(encoding="utf-8")
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert 'data-loop-view="list">Timeline</button>' in html
    assert 'id="wf-loop-list-view" class="wf-loop-view"' in html
    assert 'id="wf-loop-feed-panel"' in html
    assert "var workflowLoopViewMode = \"list\";" in js
    assert "var workflowRun = currentWorkflowActiveRun();" in js
    assert "if (workflowRun) return workflowRun;" in js
    assert "You are viewing tickets for" in js
    assert "Show running project tickets" in js
    assert "workflowBoardSelectionExplicit" in js
    assert "focusWorkflowRunBoard(activeCurrentWorkflowRun)" in js
    assert "function loopStepHandoffHtml" in js
    assert "Validated output and result packet" in js
    assert "What is happening" in js
    assert "No action needed" in js
    assert "Execution transcript" in js
    assert "Prompt / handoff" in js
    assert "Command / tool call" in js
    assert "Developer data (JSON)" in js
    assert "Workflow progress" in js
    assert "wf-loop-feed-progress-rail" in js
    assert 'if (key === "run" && workflowSteps.length) return;' in js
    assert "run.waiting_prompt || run.worker_question" in js
    assert 'title === "execution message update" || title === "execution turn start"' in js
    assert 'title === "execution turn end" || title === "execution tool execution start"' in js
    assert ".wf-loop-feed-progress-dot--waiting" in html
    assert "Whole-run allocation" in js
    assert "Plan revision" in js
    assert 'detail=true' in js
    assert ".wf-loop-transcript-record--tool" in html
    assert "workflowRunStatusLabel(run.status) + \" · \"" in js


def test_workflow_lane_add_all_disables_when_nothing_left_to_add():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    target_block = js.split("function hasWorkflowQueueTarget()", 1)[1].split(
        "function getWorkflowRenameDraftName", 1
    )[0]
    add_all_block = js.split("function refreshWorkflowLaneAddAllButtons()", 1)[1].split(
        "function rememberWorkflowBoardTicketSource", 1
    )[0]
    get_addable_block = js.split("function getAddableBoardTicketItems(laneId)", 1)[1].split(
        "function bindWorkflowBoardListRow", 1
    )[0]
    finish_block = js.split("function addAllBoardTicketsToWorkflow(laneId)", 1)[1].split(
        "function handleWorkflowTicketDropPayload", 1
    )[0]

    assert "if (!currentWorkflowId || !currentWorkflow || !detail || detail.classList.contains(\"hidden\")) return false" in target_block
    assert "tab.classList.contains(\"active\")" in target_block
    assert "tab.getAttribute(\"aria-selected\") === \"true\"" in target_block
    assert "btn.disabled = !hasWorkflowQueueTarget() || getAddableBoardTicketItems(laneId).length === 0" in add_all_block
    assert "workflowBoardTicketLinkState(item).canDragToWorkflow" in get_addable_block
    assert "isBoardTicketQueuedInCurrentWorkflow" in js
    assert "refreshWorkflowLaneAddAllButtons()" in finish_block
    assert "refreshWorkflowBoardTicketsFromQueue()" in js
    assert "if (!hasWorkflowQueueTarget())" in finish_block


def test_workflow_board_add_controls_render_before_workflow_load_then_sync_availability():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    render_block = js.split("function renderWorkflowBoardTickets(board, selected, message)", 1)[1].split(
        "function getSelectedBoardLocalId", 1
    )[0]
    sync_block = js.split("function syncWorkflowBoardTicketRowUi(ticketKey, rowEl)", 1)[1].split(
        "function refreshWorkflowLaneAddAllButtons", 1
    )[0]
    drag_block = js.split("function beginWorkflowBoardMouseDrag(row, ticketKey, evt)", 1)[1].split(
        "function initWorkflowBoardTicketMouseDrag", 1
    )[0]
    add_block = js.split("function addWorkflowBoardTicketToQueue(ticketKey, btnEl)", 1)[1].split(
        "function handleWorkflowTicketDropPayload", 1
    )[0]

    assert "var showAddAll = boardHasProject;" in render_block
    assert "showAddToWorkflow: boardHasProject" in render_block
    assert "hasWorkflowQueueTarget() && state.canDragToWorkflow" in sync_block
    assert "hasWorkflowQueueTarget() && !state.isLinkedToWorkflow && !state.isPendingLink" in sync_block
    assert "if (!hasWorkflowQueueTarget())" in drag_block
    assert 'switchTab("tickets", { persist: true })' in add_block


def test_workflows_board_drag_row_state_uses_dataset_draggable():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    sync_block = js.split("function syncWorkflowBoardTicketRowUi(ticketKey, rowEl)", 1)[1].split(
        "function refreshWorkflowLaneAddAllButtons", 1
    )[0]
    render_block = js.split("function renderWorkflowBoardTickets(board, selected, message)", 1)[1].split(
        "function getSelectedBoardLocalId", 1
    )[0]
    refresh_block = js.split("function refreshWorkflowBoardTicketsFromQueue()", 1)[1].split(
        "function rebuildWorkflowQueueExternalLinkIndex", 1
    )[0]
    restore_block = js.split("function restoreWorkflowBoardTicketAfterQueueRemove(ticketId, externalKey)", 1)[1].split(
        "function workflowBoardTicketDropPayload", 1
    )[0]
    remove_block = js.split("function removeWorkflowQueueTicket(ticketId, options)", 1)[1].split(
        "function createWorkflowQueueListRow", 1
    )[0]

    assert "rowEl || workflowBoardTicketRowForKey(ticketKey)" in sync_block
    assert 'row.dataset.draggable = state.canDragToWorkflow ? "true" : "false"' in sync_block
    assert "upgradeWorkflowBoardTicketGrip(row, state.canDragToWorkflow)" in sync_block
    assert "addWrap.hidden = !showAdd" in sync_block
    assert "linkedToCurrentWorkflow" in js
    assert "workflowExternalLinkKeyFromTicketRecord" in js
    assert "restoreWorkflowBoardTicketAfterQueueRemove(ticketId, externalKey)" in remove_block
    assert "refreshWorkflowBoardTicketsFromQueue()" in restore_block
    assert "rebuildWorkflowQueueExternalLinkIndex()" in refresh_block
    assert "isBoardTicketQueuedInCurrentWorkflow(item)" in refresh_block
    assert "item.ticket.linked_workflow_id = null" in refresh_block
    assert 'String(selected.source).toLowerCase() + ":" + String(externalId)' in js
    assert "external_source: ticket.external_source" in js
    assert "syncWorkflowBoardTicketRowUi(ticketKey, row)" in js
    assert "refreshWorkflowBoardTicketDragBindings(list)" in render_block
    assert "refreshWorkflowBoardTicketsFromQueue()" in render_block


def test_workflow_queue_status_owns_elapsed_time_and_hides_zero_duration():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")
    row_block = js.split("function createWorkflowQueueListRow(ticket, queueId, options)", 1)[1].split(
        "function renderWorkflowQueueList", 1
    )[0]

    assert "var showTime = timeLive || elapsedSeconds > 0;" in row_block
    assert 'class="wf-ticket-status-time ' in row_block
    assert 'class="wf-ticket-status-label"' in row_block
    assert "(showTime" in row_block
    assert 'class="wf-ticket-time-display ' in row_block
    assert 'title="Workflow queue status"' not in row_block
