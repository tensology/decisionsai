from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed
from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
from distr.core.project_cli_backends.base import ProjectTask
from distr.core.project_cli_backends.registry import CodexBackend, CursorBackend


def test_normalize_codex_intelligence_and_speed():
    assert normalize_codex_intelligence("Extra High") == "xhigh"
    assert normalize_codex_intelligence("medium") == "medium"
    assert normalize_codex_intelligence("bogus") == ""
    assert normalize_codex_speed("fast") == "fast"
    assert normalize_codex_speed("invalid") == ""


def test_complexity_route_includes_codex_prefs(monkeypatch):
    class Backend:
        def setup_status(self):
            return type("Status", (), {"ready": True})()

    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", lambda backend_id: Backend())
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "project_cli_high_backend": "codex",
            "project_cli_high_model": "gpt-5.3-codex",
            "project_cli_high_codex_intelligence": "high",
            "project_cli_high_codex_speed": "fast",
        },
    )
    project = type("Project", (), {"coding_backend": "pi", "coding_backend_model": ""})()

    route = resolve_ticket_cli_route(project, "high")

    assert route["backend"] == "codex"
    assert route["codex_reasoning_effort"] == "high"
    assert route["codex_service_tier"] == "fast"


def test_codex_build_command_passes_config_overrides():
    backend = CodexBackend()
    task = ProjectTask(
        project_id=1,
        project_name="demo",
        folder="/tmp",
        instruction="fix tests",
        model="gpt-5.3-codex",
        codex_reasoning_effort="medium",
        codex_service_tier="flex",
    )
    cmd = backend._build_command("codex", task)
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "-c" in cmd
    assert 'model_reasoning_effort="medium"' in cmd
    assert 'service_tier="flex"' in cmd


def test_codex_build_command_allows_sandbox_override(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_CODEX_SANDBOX", "danger-full-access")
    backend = CodexBackend()
    task = ProjectTask(
        project_id=1,
        project_name="demo",
        folder="/tmp",
        instruction="fix tests",
    )

    cmd = backend._build_command("codex", task)

    assert "--sandbox" in cmd
    assert "danger-full-access" in cmd


def test_codex_read_only_workflow_step_overrides_mutable_sandbox(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_CODEX_SANDBOX", "danger-full-access")
    backend = CodexBackend()
    task = ProjectTask(
        project_id=1,
        project_name="demo",
        folder="/tmp",
        instruction="Inspect the ticket without editing files.",
        adapter_options={"read_only_expected": True},
    )

    cmd = backend._build_command("codex", task)

    sandbox_index = cmd.index("--sandbox")
    assert cmd[sandbox_index + 1] == "read-only"
    assert "danger-full-access" not in cmd


def test_codex_result_handoff_keeps_final_contract_without_cli_noise():
    backend = CodexBackend()
    raw = (
        "OpenAI Codex v1\nMCP warning\nuser prompt with Status: example\n"
        "tokens used\n105440\n"
        "Status: completed\n"
        "Summary: Scoped the ticket.\n"
        "context_packet: concise evidence\n"
        "Files changed: none\n"
        "Blockers: none\n"
    )

    result = backend._result_output(raw)

    assert result.startswith("Status: completed")
    assert "context_packet: concise evidence" in result
    assert "OpenAI Codex" not in result
    assert "105440" not in result


def test_codex_build_command_embeds_decisions_callback(monkeypatch):
    monkeypatch.setenv("DECISIONS_API_BASE", "http://127.0.0.1:8765")
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "test-internal-token")
    backend = CodexBackend()
    task = ProjectTask(
        project_id=4,
        project_name="demo",
        folder="/tmp",
        instruction="fix tests",
        workflow_id=2,
        run_id=9,
        step_id=11,
        ticket_id=7,
        execution_session_id=31,
    )

    cmd = backend._build_command("codex", task)
    instruction = cmd[-1]

    assert "[DECISIONS CODEX CALLBACK]" in instruction
    assert '"callback_url_env":"DECISIONS_CALLBACK_URL"' in instruction
    assert "/api/workflows/2/runs/9/codex-events" not in instruction
    assert "test-internal-token" not in instruction
    assert '"execution_session_id":31' in instruction
    assert "report_decisions_event.py" in instruction
    assert "codex_prompt_submitted" in instruction
    assert instruction.endswith("fix tests")


def test_cursor_build_command_embeds_decisions_callback(monkeypatch):
    monkeypatch.setenv("DECISIONS_API_BASE", "http://127.0.0.1:8765")
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "test-internal-token")
    backend = CursorBackend()
    task = ProjectTask(
        project_id=4,
        project_name="demo",
        folder="/tmp",
        instruction="fix tests",
        workflow_id=2,
        run_id=9,
        step_id=11,
        ticket_id=7,
        execution_session_id=31,
    )

    cmd = backend._build_command("cursor-agent", task)
    instruction = cmd[-1]

    assert cmd[:3] == ["cursor-agent", "--trust", "-p"]
    assert "[DECISIONS CURSOR CALLBACK]" in instruction
    assert '"callback_url_env":"DECISIONS_CALLBACK_URL"' in instruction
    assert "/api/workflows/2/runs/9/codex-events" not in instruction
    assert "test-internal-token" not in instruction
    assert '"execution_session_id":31' in instruction
    assert "report_decisions_event.py" in instruction
    assert "cursor_progress" in instruction
    assert instruction.endswith("fix tests")
