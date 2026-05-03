"""R11 self-improvement: draft queue + approve-time MCP/skill install."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

import pytest

from distr.core.initiative.draft_execute import (
    append_installed_skill_to_registry,
    approve_draft_in_queue,
    merge_mcp_server_into_config,
    run_execute_payload,
    validate_skill_install_queue,
    validated_mcp_server_for_install,
)
from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
from distr.core.mcp.config import load_mcp_config


@pytest.fixture
def tmp_mcp_config(monkeypatch, tmp_path: Path) -> Path:
    p = tmp_path / "mcp_config.json"
    p.write_text('{"servers": []}', encoding="utf-8")
    monkeypatch.setattr(
        "distr.core.initiative.draft_execute.default_config_path",
        lambda: p,
    )
    return p


@pytest.fixture
def tmp_skills_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    monkeypatch.setattr(
        "distr.core.initiative.draft_execute.bundled_skills_directory",
        lambda: root,
    )
    return root


@pytest.fixture
def tmp_draft_path(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "initiative_drafts.json"
    monkeypatch.setattr(
        "distr.core.initiative.draft_queue._DEFAULT_DRAFT_PATH",
        str(path),
    )
    return path


def test_validated_mcp_rejects_duplicate(tmp_mcp_config: Path) -> None:
    merge_mcp_server_into_config(
        {"name": "dup", "transport": "stdio", "command": ["true"], "enabled": True}
    )
    with pytest.raises(ValueError, match="duplicate"):
        validated_mcp_server_for_install(
            {"name": "dup", "transport": "stdio", "command": ["true"], "enabled": True}
        )


def test_run_execute_mcp_install(tmp_mcp_config: Path) -> None:
    server = {"name": "s1", "transport": "stdio", "command": ["echo", "hi"], "enabled": True}
    run_execute_payload({"kind": "mcp_install", "server": server})
    doc = load_mcp_config(tmp_mcp_config)
    assert len(doc.servers) == 1
    assert doc.servers[0].name == "s1"


def test_validate_skill_install_queue(tmp_skills_root: Path) -> None:
    tmp_skills_root.mkdir(parents=True)
    url, folder = validate_skill_install_queue(
        "https://example.com/org/foo.git", ""
    )
    assert url.startswith("https://")
    assert folder == "foo"


def test_run_execute_skill_install(tmp_skills_root: Path) -> None:
    tmp_skills_root.mkdir(parents=True)

    def fake_run(cmd, **kwargs):
        assert "clone" in cmd
        dest = Path(cmd[-1])
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text(
            "---\nname: Registry Display Name\ndescription: From frontmatter\n---\n",
            encoding="utf-8",
        )
        return CompletedProcess(cmd, 0, "", "")

    with patch("distr.core.initiative.draft_execute.subprocess.run", side_effect=fake_run):
        run_execute_payload(
            {
                "kind": "skill_install",
                "repo_url": "https://example.com/a/my-skill.git",
                "folder_name": "my-skill",
            }
        )

    assert (tmp_skills_root / "my-skill" / "SKILL.md").is_file()
    reg = tmp_skills_root / "skills_registry.json"
    assert reg.is_file()
    rows = json.loads(reg.read_text(encoding="utf-8"))
    row = next(r for r in rows if r["id"] == "my-skill")
    assert row["path"] == "my-skill"
    assert row["name"] == "Registry Display Name"
    assert row["description"] == "From frontmatter"


def test_registry_append_skips_duplicate(tmp_skills_root: Path) -> None:
    tmp_skills_root.mkdir(parents=True)
    reg = tmp_skills_root / "skills_registry.json"
    existing = [
        {
            "id": "my-skill",
            "name": "Old",
            "description": "Old row",
            "path": "my-skill",
        }
    ]
    reg.write_text(json.dumps(existing), encoding="utf-8")
    skill_dir = tmp_skills_root / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: New\n---\n", encoding="utf-8")

    ok = append_installed_skill_to_registry(
        "my-skill",
        repo_source_url="https://example.com/a.git",
        registry_file=reg,
        skills_root=tmp_skills_root,
    )
    assert ok is False
    assert json.loads(reg.read_text(encoding="utf-8")) == existing


def test_registry_append_aborts_on_corrupt_file(tmp_skills_root: Path) -> None:
    tmp_skills_root.mkdir(parents=True)
    reg = tmp_skills_root / "skills_registry.json"
    reg.write_text("{not json", encoding="utf-8")
    skill_dir = tmp_skills_root / "new-one"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: X\n---\n", encoding="utf-8")

    ok = append_installed_skill_to_registry(
        "new-one",
        repo_source_url="https://example.com/x.git",
        registry_file=reg,
        skills_root=tmp_skills_root,
    )
    assert ok is False
    assert reg.read_text(encoding="utf-8") == "{not json"


def test_run_execute_skill_clone_failure_cleans_tmp(tmp_skills_root: Path) -> None:
    tmp_skills_root.mkdir(parents=True)

    def boom(cmd, **kwargs):
        dest = Path(cmd[-1])
        dest.mkdir(parents=True)
        (dest / "partial.txt").write_text("x", encoding="utf-8")
        raise CalledProcessError(1, cmd, output="", stderr="err")

    with patch("distr.core.initiative.draft_execute.subprocess.run", side_effect=boom):
        with pytest.raises(RuntimeError, match="git clone failed"):
            run_execute_payload(
                {
                    "kind": "skill_install",
                    "repo_url": "https://example.com/a/z.git",
                    "folder_name": "z",
                }
            )

    assert not (tmp_skills_root / "z").exists()


def test_install_mcp_tool_queues_without_writing_config(
    tmp_mcp_config: Path, tmp_draft_path: Path
) -> None:
    from distr.core.agent.tools.system.self_improvement_tools import InstallMCPServerTool

    tool = InstallMCPServerTool()
    msg = tool._run(name="newsrv", transport="stdio", command=["true"])
    assert "Queued pending approval" in msg

    doc = load_mcp_config(tmp_mcp_config)
    assert len(doc.servers) == 0

    q = DraftQueue()
    entries = q.get_all()
    assert len(entries) == 1
    assert entries[0].execute_payload is not None
    assert entries[0].execute_payload["kind"] == "mcp_install"


def test_approve_draft_in_queue_runs_payload(tmp_mcp_config: Path, tmp_path: Path) -> None:
    path = str(tmp_path / "dq.json")
    q = DraftQueue(path=path)
    now = datetime.now(tz=timezone.utc)
    eid = "draft-exec-1"
    entry = DraftEntry(
        id=eid,
        action_type="file_change",
        description="install srv",
        draft="{}",
        reason="r",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=48)).isoformat(),
        execute_payload={
            "kind": "mcp_install",
            "server": {
                "name": "from-queue",
                "transport": "stdio",
                "command": ["true"],
                "enabled": True,
            },
        },
    )
    q.add(entry)
    assert approve_draft_in_queue(q, eid) is True
    assert q.get_by_id(eid) is None
    doc = load_mcp_config(tmp_mcp_config)
    assert any(s.name == "from-queue" for s in doc.servers)


def test_draft_queue_get_by_id_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "d.json")
    q = DraftQueue(path=path)
    now = datetime.now(tz=timezone.utc)
    e = DraftEntry(
        id="abc",
        action_type="file_change",
        description="d",
        draft="body",
        reason="r",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=48)).isoformat(),
        execute_payload={"kind": "mcp_install", "server": {"name": "x"}},
    )
    q.add(e)
    assert q.get_by_id("abc") is not None
    assert q.get_by_id("missing") is None

    q2 = DraftQueue(path=path)
    loaded = q2.get_by_id("abc")
    assert loaded is not None
    assert loaded.execute_payload["kind"] == "mcp_install"
