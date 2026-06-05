from pathlib import Path


def _skill(root: Path, skill_id: str, body: str) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_skill_registry_dedupes_by_canonical_id_and_prefers_local(tmp_path):
    local = tmp_path / "skills"
    ecc = tmp_path / "vendor" / "ecc" / "skills"
    _skill(local, "systematic-debugging", "---\nname: Debugging\n---\nLocal debugging")
    _skill(ecc, "systematic-debugging", "---\nname: Debugging\n---\nECC debugging")
    _skill(ecc, "ecc-guide", "---\nname: ECC Guide\n---\nECC guide")

    from distr.core.skills.registry import SkillRegistry

    registry = SkillRegistry(local_roots=[local], vendor_roots=[ecc]).scan()

    chosen = registry.get("systematic-debugging")
    assert chosen is not None
    assert chosen.source == "local"
    assert chosen.path == local / "systematic-debugging"
    assert registry.get("ecc-guide").source == "ecc_vendor"
    assert registry.conflicts["systematic-debugging"][0].source == "ecc_vendor"


def test_skill_registry_target_paths_cover_codex_cursor_and_claude(tmp_path):
    skills = tmp_path / "skills"
    _skill(skills, "ecc-guide", "---\nname: ECC Guide\n---\nECC guide")

    from distr.core.skills.registry import SkillRegistry

    registry = SkillRegistry(local_roots=[], vendor_roots=[skills]).scan()
    entry = registry.get("ecc-guide")

    assert registry.target_path(entry, "codex", tmp_path / "project") == tmp_path / "project" / ".codex" / "commands" / "ecc-guide.md"
    assert registry.target_path(entry, "cursor", tmp_path / "project") == tmp_path / "project" / ".cursor" / "commands" / "ecc-guide.md"
    assert registry.target_path(entry, "claude", tmp_path / "project") == tmp_path / "project" / ".claude" / "commands" / "ecc-guide.md"
