from __future__ import annotations


def test_cline_backend_builds_yolo_act_command(monkeypatch):
    from distr.core.project_cli_backends.base import ProjectTask
    from distr.core.project_cli_backends.registry import ClineBackend

    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._first_executable",
        lambda candidates: "/usr/local/bin/cline",
    )

    backend = ClineBackend()
    task = ProjectTask(
        project_id=1,
        project_name="demo",
        folder="/tmp/demo",
        instruction="Fix the failing test.",
        model="auto",
    )
    cmd = backend._build_command("/usr/local/bin/cline", task)

    assert cmd[:3] == ["/usr/local/bin/cline", "--yolo", "--act"]
    assert cmd[-1] == "Fix the failing test."


def test_cline_backend_passes_model_flag(monkeypatch):
    from distr.core.project_cli_backends.base import ProjectTask
    from distr.core.project_cli_backends.registry import ClineBackend

    backend = ClineBackend()
    task = ProjectTask(
        project_id=1,
        project_name="demo",
        folder="/tmp/demo",
        instruction="Ship it.",
        model="anthropic/claude-sonnet-4",
    )
    cmd = backend._build_command("/usr/local/bin/cline", task)

    assert "--model" in cmd
    assert "anthropic/claude-sonnet-4" in cmd
