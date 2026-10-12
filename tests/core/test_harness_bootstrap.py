from pathlib import Path


def test_harness_skill_bases_projects_kiro_and_grok_via_supported_surfaces(tmp_path: Path):
    from distr.core.harness_bootstrap import harness_skill_bases, projection_paths

    detected = {"kiro": True, "grok": True, "cursor": False}
    bases = harness_skill_bases(tmp_path, detected)

    assert bases["kiro"] == tmp_path / ".kiro" / "skills"
    assert bases["grok"] == tmp_path / ".cursor" / "skills"
    assert projection_paths(tmp_path, detected, "example")["kiro"] == (
        tmp_path / ".kiro" / "skills" / "example" / "SKILL.md"
    )
    assert projection_paths(tmp_path, detected, "example")["grok"] == (
        tmp_path / ".cursor" / "skills" / "example" / "SKILL.md"
    )
