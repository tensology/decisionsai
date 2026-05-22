from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_cursor_and_vscode_ide_backends_are_registered():
    from distr.core.project_cli_backends import get_backend, normalize_backend_id

    assert normalize_backend_id("cursor extension") == "cursor_ide"
    assert normalize_backend_id("vscode") == "vscode_ide"
    assert get_backend("cursor_ide").name == "Cursor IDE"
    assert get_backend("vscode_ide").name == "VS Code IDE"


def test_cli_output_compaction_keeps_head_and_tail():
    from distr.core.project_cli_backends.registry import _compact_cli_output

    output = "start\n" + ("noise\n" * 2000) + "useful final summary"

    compacted = _compact_cli_output(output, limit=1000)

    assert compacted.startswith("start")
    assert "omitted" in compacted
    assert compacted.endswith("useful final summary")
    assert len(compacted) < len(output)


def test_ide_backend_writes_ticket_packet_and_opens_editor(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.registry import CursorIdeBackend

    opened = []

    def fake_which(command):
        return f"/usr/local/bin/{command}" if command == "cursor" else None

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout="decisionsai.decisionsai\n", stderr="")

    def fake_popen(args, **kwargs):
        opened.append(args)
        return SimpleNamespace(pid=123)

    monkeypatch.setattr("distr.core.project_cli_backends.registry.shutil.which", fake_which)
    monkeypatch.setattr("distr.core.project_cli_backends.registry.subprocess.run", fake_run)
    monkeypatch.setattr("distr.core.project_cli_backends.registry.subprocess.Popen", fake_popen)

    task = SimpleNamespace(
        project_id=7,
        project_name="Fixture",
        folder=str(tmp_path),
        instruction="Build the settings panel and run tests.",
        chat_id=None,
        audit_id=11,
        run_id=22,
        workflow_id=33,
        step_id=44,
        origin="workflow",
        model="",
    )

    backend = CursorIdeBackend()
    result = asyncio.run(backend.send_task(task))

    assert result.success is True
    assert result.engine == "ide_ticket"
    assert opened == [["/usr/local/bin/cursor", str(tmp_path)]]

    tickets = list((tmp_path / ".tickets").glob("decisionsai_cursor_ide_*.md"))
    assert len(tickets) == 1
    body = tickets[0].read_text(encoding="utf-8")
    assert "decisions-ide-meta" in body
    assert '"run_id":22' in body
    assert '"workflow_id":33' in body
    assert '"step_id":44' in body
    assert "Build the settings panel and run tests." in body
    assert "Status: completed | failed | needs_input" in body
