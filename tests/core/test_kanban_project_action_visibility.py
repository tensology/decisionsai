from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_project_bound_card_actions_are_hidden_without_project():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")

    assert "if (config.hidden) return \"\";" in js
    assert 'keyClass: "kb-act-copy"' in js
    assert 'keyClass: "kb-act-agent"' in js
    assert 'keyClass: "kb-act-add-workflow"' in js
    assert 'keyClass: "kb-act-cli"' in js
    assert 'keyClass: "kb-act-project"' in js
    assert 'keyClass: "kb-act-workflow"' in js
    assert 'keyClass: "kb-act-transfer"' in js
    assert 'keyClass: "kb-act-delete"' in js
    assert 'tooltip: "Send to Orchestrator"' in js
    assert 'tooltip: "Copy title and description"' in js
    assert 'tooltip: "Copy to local board"' in js
    assert "hasProject" in js
    assert "canTransfer" in js
    assert "canDelete" in js
    assert "link ticket/board to project" not in js


def test_kanban_orchestrator_prompt_is_active_and_project_aware():
    py = (ROOT / "distr/core/kanban/ticket_orchestrator_engagement.py").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert "[Ticket Board — orchestrator engage this ticket]" in py
    assert "You have consumed this ticket for this turn" in py
    assert "Work context now active" in py
    assert 'apiFetch("/api/tickets/tickets/engage-orchestrator"' in js
    assert "local_board_id: localBoardId" in js


def test_kanban_api_payloads_expose_project_context_for_orchestrator():
    py = (ROOT / "distr/gui/web/routes/kanban.py").read_text(encoding="utf-8")

    assert "def _project_context_payload" in py
    assert "default_project_name" in py
    assert "default_project_folder" in py
    assert '**_project_context_payload(linked_project, "linked")' in py


def test_workflows_move_copy_and_orchestrator_actions_to_workflow_ticket_queue():
    kanban_ticket_js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")
    workflows_js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "hideCopy: !!listOpts.hideCopy" in kanban_ticket_js
    assert "hideAgent: !!listOpts.hideAgent" in kanban_ticket_js
    assert "copyTicketToClipboard: copyTicketToClipboard" in kanban_ticket_js

    assert "hideCopy: true" in workflows_js
    assert "hideAgent: true" in workflows_js
    assert "wf-workflow-ticket-copy" in workflows_js
    assert "wf-workflow-ticket-orchestrator" in workflows_js
    assert "copyWorkflowQueueTicket" in workflows_js
    assert "sendWorkflowQueueTicketToOrchestrator" in workflows_js
