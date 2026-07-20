from __future__ import annotations

from pathlib import Path


def test_harness_pack_bootstrap_projects_vendor_context_to_detected_harnesses(tmp_path, monkeypatch):
    from distr.core.harness_pack import ensure_harness_pack_setup

    detected = {
        "codex": "/usr/local/bin/codex",
        "claude": "/usr/local/bin/claude",
        "cursor": "/usr/local/bin/cursor",
        "cursor-agent": "/usr/local/bin/cursor-agent",
        "pi": "/usr/local/bin/pi",
    }

    monkeypatch.setattr("distr.core.harness_pack.shutil.which", lambda name: detected.get(name))

    result = ensure_harness_pack_setup(
        home=tmp_path,
        run_full=True,
        install_codex_plugin=False,
        install_editor_extension=False,
    )

    assert result["vendor_ready"] is True
    assert result["detected"]["codex"] is True
    assert result["detected"]["claude"] is True
    assert result["detected"]["cursor"] is True
    assert result["detected"]["pi"] is True

    state_path = tmp_path / ".decisions" / "harness-pack-state.json"
    assert state_path.exists()

    registry_path = tmp_path / ".decisions" / "harness" / "ecc-skills-registry.json"
    registry_text = registry_path.read_text(encoding="utf-8")
    assert "react-patterns" in registry_text
    assert "plugins/ecc" in registry_text

    manifest_path = tmp_path / ".decisions" / "harness" / "ecc-surface-manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "agents" in manifest_text
    assert "mcp_configs" in manifest_text
    assert "rust_control_plane" in manifest_text

    for relative in [
        ".claude/skills/decisions-ecc-harness/SKILL.md",
        ".cursor/skills/decisions-ecc-harness/SKILL.md",
        ".pi/skills/decisions-ecc-harness/SKILL.md",
        "plugins/decisions-codex/skills/ecc-harness-pack/SKILL.md",
    ]:
        path = tmp_path / relative
        assert path.exists(), relative
        text = path.read_text(encoding="utf-8")
        assert "DecisionsAI ECC Harness Pack" in text
        assert "plugins/ecc" in text
        assert "surface manifest" in text


def test_harness_pack_bootstrap_is_idempotent_when_state_matches(tmp_path, monkeypatch):
    from distr.core.harness_pack import ensure_harness_pack_setup

    monkeypatch.setattr("distr.core.harness_pack.shutil.which", lambda name: "/usr/local/bin/codex" if name == "codex" else None)

    first = ensure_harness_pack_setup(
        home=tmp_path,
        run_full=False,
        install_codex_plugin=False,
        install_editor_extension=False,
    )
    marker = tmp_path / "plugins" / "decisions-codex" / "skills" / "ecc-harness-pack" / "SKILL.md"
    before = marker.read_text(encoding="utf-8")

    second = ensure_harness_pack_setup(
        home=tmp_path,
        run_full=False,
        install_codex_plugin=False,
        install_editor_extension=False,
    )

    assert first["fingerprint"] == second["fingerprint"]
    assert marker.read_text(encoding="utf-8") == before
    assert second["status"] == "current"


def test_workflow_skill_provision_can_push_vendored_ecc_skill(tmp_path):
    from distr.core.workflow.skill_provision import push_skill_to_project

    dest = push_skill_to_project(
        skill_id="react-patterns",
        project_folder=str(tmp_path),
        backend_id="pi",
    )

    assert dest is not None
    pushed = Path(dest)
    assert pushed.exists()
    assert pushed.parent.name == "react-patterns"
    assert "React" in pushed.read_text(encoding="utf-8")
