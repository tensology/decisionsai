from __future__ import annotations


def test_legacy_editor_extension_backends_are_not_registered():
    from distr.core.project_cli_backends import list_backends, normalize_backend_id

    backend_ids = {backend.id for backend in list_backends()}

    assert "cursor_ide" not in backend_ids
    assert "vscode_ide" not in backend_ids
    assert normalize_backend_id("cursor extension") == "cursor"
    assert normalize_backend_id("vscode") == "cursor"


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
