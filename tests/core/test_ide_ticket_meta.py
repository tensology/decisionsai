"""IDE work packet metadata must match the VS Code extension contract."""

from __future__ import annotations

from distr.core.project_cli_backends.base import ProjectTask
from distr.core.project_cli_backends.registry import VSCodeIdeBackend


def test_vscode_ide_ticket_includes_extension_callback_meta(monkeypatch):
    monkeypatch.setenv("DECISIONS_API_BASE", "http://127.0.0.1:8765")
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "test-internal-token")
    backend = VSCodeIdeBackend()
    body = backend._ticket_body(
        ProjectTask(
            project_id=1,
            project_name="Demo",
            folder="/tmp/demo",
            instruction="Fix the header spacing.",
            run_id=9,
            workflow_id=3,
            step_id=7,
            ticket_id=12,
            board_id=4,
            execution_session_id=55,
        )
    )

    assert "<!-- decisions-meta:" in body
    assert "<!-- decisions-ide-meta:" in body
    assert "auto_continue_on_pickup: false" in body
    assert "callback_payload_type: workflow_continue" in body
    assert "http://127.0.0.1:8765/api/workflows/3/runs/9/continue" in body
    assert "http://127.0.0.1:8765/api/workflows/3/runs/9/codex-events" in body
    assert "internal_token=test-internal-token" in body
    assert "Report Workflow Complete" in body
