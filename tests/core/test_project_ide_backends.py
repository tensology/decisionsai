from __future__ import annotations


def test_ide_backends_are_registered_separately_from_cli():
    from distr.core.project_cli_backends import list_backends, normalize_backend_id

    backend_ids = {backend.id for backend in list_backends()}

    assert "cursor_ide" in backend_ids
    assert "codex_ide" in backend_ids
    assert normalize_backend_id("cursor extension") == "cursor"
    assert normalize_backend_id("vscode") == "cursor_ide"


def test_cli_output_compaction_keeps_head_and_tail():
    from distr.core.project_cli_backends.registry import _compact_cli_output

    output = "start\n" + ("noise\n" * 2000) + "useful final summary"

    compacted = _compact_cli_output(output, limit=1000)

    assert compacted.startswith("start")
    assert "omitted" in compacted
    assert compacted.endswith("useful final summary")
    assert len(compacted) < len(output)


def test_cursor_plugin_setup_state_replaces_editor_extension_contract():
    from distr.gui.web.routes.settings.projects import _cursor_plugin_state

    state = _cursor_plugin_state()

    assert state["path"].endswith("cursor_plugin/decisions-cursor")
    assert any(path.endswith("cursor_plugin/decisions-cursor") for path in state["candidates"])
    assert "manifest_exists" in state


def test_codex_and_cursor_status_expose_remote_handoff_readiness(monkeypatch):
    from distr.core.project_cli_backends.registry import CodexBackend, CursorBackend, CursorIdeBackend

    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._first_executable",
        lambda candidates: f"/usr/local/bin/{candidates[0]}",
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._version_for",
        lambda path, args=None: "test-version",
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._cursor_auth_ready",
        lambda path: True,
    )
    monkeypatch.setenv("DECISIONS_CODEX_REPORTER", "/tmp/decisions-codex-reporter.py")
    monkeypatch.setenv("DECISIONS_CURSOR_REPORTER", "/tmp/decisions-cursor-reporter.py")

    codex = CodexBackend().setup_status().to_dict()
    cursor = CursorBackend().setup_status().to_dict()
    cursor_ide = CursorIdeBackend().setup_status().to_dict()

    assert codex["can_receive_remote_handoff"] is True
    assert codex["handoff_method"] == "one_shot_cli_with_callback"
    assert cursor["handoff_method"] == "one_shot_cli_with_callback"
    assert cursor_ide["handoff_method"] == "ide_work_packet"
    assert cursor_ide["can_receive_remote_handoff"] is True


def test_cursor_status_blocks_remote_handoff_when_not_authenticated(monkeypatch):
    from distr.core.project_cli_backends.registry import CursorBackend

    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._first_executable",
        lambda candidates: f"/usr/local/bin/{candidates[0]}",
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._version_for",
        lambda path, args=None: "test-version",
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry._cursor_auth_ready",
        lambda path: False,
    )

    cursor = CursorBackend().setup_status().to_dict()

    assert cursor["ready"] is False
    assert cursor["can_receive_remote_handoff"] is False
    assert cursor["state"] == "auth_required"
    assert "cursor-agent login" in cursor["message"]
