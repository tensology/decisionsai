from __future__ import annotations


def test_legacy_editor_backend_aliases_route_to_cursor_cli():
    from distr.core.project_cli_backends import normalize_backend_id

    assert normalize_backend_id("cursor_ide") == "cursor"
    assert normalize_backend_id("cursor extension") == "cursor"
    assert normalize_backend_id("vscode_ide") == "cursor"
    assert normalize_backend_id("vscode") == "cursor"


def test_cursor_backend_is_not_a_waiting_ide_handoff():
    from distr.core.project_cli_backends.base import BackendTaskResult
    from distr.core.project_cli_backends.harness import HarnessContext, HarnessStatus, dispatch_harness
    from unittest.mock import patch

    async def fake_run_project_task(*args, **kwargs):
        return BackendTaskResult(
            success=True,
            backend_id="cursor",
            engine="cursor",
            output="Status: completed",
        )

    async def run():
        with patch("distr.core.project_cli_backends.registry.run_project_task", fake_run_project_task):
            handle = await dispatch_harness(
                HarnessContext(
                    project=type("Project", (), {"id": 1, "name": "Demo", "folder_location": "/tmp"})(),
                    instruction="Do the work.",
                    backend_id="cursor",
                )
            )
            return handle

    import asyncio

    handle = asyncio.run(run())

    assert handle.status == HarnessStatus.DONE
    assert handle.evidence["engine"] == "cursor"
