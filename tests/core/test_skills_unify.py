"""Shared-skill (~/.agents/skills) linking, frontmatter parsing, catalog scan and lint."""

import os
from pathlib import Path


def _skill(root: Path, name: str, desc: str = "Use when testing.") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nBody\n", encoding="utf-8")
    return d


def test_link_skill_tree_keeps_correct_link_and_archives_real_dir(tmp_path: Path):
    from distr.core.harness_bootstrap import link_skill_tree

    src = _skill(tmp_path / "src", "alpha")
    dest = tmp_path / "h" / "alpha"
    assert link_skill_tree(src, dest) is True
    assert dest.is_symlink() and dest.resolve() == src.resolve()
    assert link_skill_tree(src, dest) is False  # never re-creates a correct link

    real = _skill(tmp_path / "h2", "alpha")
    archive = tmp_path / "archive"
    assert link_skill_tree(src, real, archive_root=archive) is True
    assert real.is_symlink()
    assert (archive / "h2" / "alpha" / "SKILL.md").is_file()  # archived, not deleted


def test_install_publishes_shared_dir_and_links_harnesses(tmp_path: Path):
    from distr.core.harness_bootstrap import install_skills_to_harnesses, shared_skills_dir

    src = _skill(tmp_path / "repo", "beta")
    detected = {"claude": True, "cursor": True, "pi": True, "kilo": True}
    written = install_skills_to_harnesses(
        home=tmp_path, detected=detected, skill_sources={"beta": src}, also_commands=False
    )
    shared = shared_skills_dir(tmp_path) / "beta"
    assert shared.is_symlink() and shared.resolve() == src.resolve()
    for rel in (".claude/skills/beta", ".cursor/skills/beta"):
        link = tmp_path / rel
        assert link.is_symlink() and Path(os.readlink(link)) == shared
        assert str(link) in written
    # Pi and Kilo read ~/.agents/skills natively: no copies, no ~/.pi/skills
    assert not (tmp_path / ".pi" / "skills").exists()
    assert not (tmp_path / ".pi" / "agent" / "skills" / "beta").exists()
    assert not (tmp_path / ".kilocode" / "skills" / "beta").exists()
    # idempotent
    again = install_skills_to_harnesses(
        home=tmp_path, detected=detected, skill_sources={"beta": src}, also_commands=False
    )
    assert str(shared) not in again


def test_install_never_replaces_user_owned_shared_dir(tmp_path: Path):
    from distr.core.harness_bootstrap import install_skills_to_harnesses

    src = _skill(tmp_path / "repo", "gamma", "Repo version.")
    user = _skill(tmp_path / ".agents" / "skills", "gamma", "Hand-edited canonical version.")
    install_skills_to_harnesses(
        home=tmp_path, detected={"claude": True}, skill_sources={"gamma": src}, also_commands=False
    )
    assert not user.is_symlink()
    assert "Hand-edited" in (user / "SKILL.md").read_text(encoding="utf-8")
    assert (tmp_path / ".claude" / "skills" / "gamma").resolve() == user.resolve()


def test_harness_bases_rely_on_shared_dir_for_pi_and_kilo(tmp_path: Path):
    from distr.core.harness_bootstrap import harness_skill_bases, projection_paths

    bases = harness_skill_bases(tmp_path, {"pi": True, "kilo": True})
    assert "pi" not in bases and "kilo" not in bases
    assert projection_paths(tmp_path, {"pi": True}, "x")["pi"] == tmp_path / ".pi" / "agent" / "skills" / "x" / "SKILL.md"


def test_write_projection_skill_never_writes_through_symlink(tmp_path: Path):
    from distr.core.harness_bootstrap import write_projection_skill

    shared = _skill(tmp_path / ".agents" / "skills", "proj", "Shared text.")
    link = tmp_path / ".claude" / "skills" / "proj"
    link.parent.mkdir(parents=True)
    os.symlink(shared, link)
    write_projection_skill(link / "SKILL.md", "harness specific")
    assert "Shared text." in (shared / "SKILL.md").read_text(encoding="utf-8")
    assert not link.is_symlink() and (link / "SKILL.md").read_text(encoding="utf-8") == "harness specific"


def test_frontmatter_fields_handles_block_scalars_and_fallback(monkeypatch):
    import builtins

    from distr.core.skills import registry

    text = "---\nname: demo\ndescription: >-\n  Folded first line\n  and second.\nmetadata:\n  a: b\n---\nBody"
    assert registry._frontmatter_fields(text, "f") == ("demo", "Folded first line and second.")
    assert registry._frontmatter_fields("# no frontmatter", "fb") == ("fb", "")

    real_import = builtins.__import__

    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    assert registry._frontmatter_fields(text, "f") == ("demo", "Folded first line and second.")
    literal = "---\nname: lit\ndescription: |\n  L1\n  L2\n---\n"
    assert registry._frontmatter_fields(literal, "f") == ("lit", "L1 L2")


def test_catalog_scans_unregistered_skill_folders_and_repairs_placeholders():
    from distr.core.skills.catalog import load_registry

    load_registry.cache_clear()
    try:
        rows = {r["id"]: r for r in load_registry()}
        for skill_id in ("jev-tool-guard", "decisions-harness-stack", "decisions-composio", "decisions-visual-plan"):
            assert skill_id in rows, skill_id
            assert rows[skill_id]["description"].strip() not in ("", ">", ">-", "|")
        local = [r for r in rows.values() if r.get("source") == "local"]
        assert not [r["id"] for r in local if str(r.get("description") or "").strip() in ("", ">", ">-", "|", "|-")]
    finally:
        load_registry.cache_clear()


def test_skills_lint_reports_drift_and_frontmatter(tmp_path: Path):
    from distr.core.skills.lint import lint_home

    shared = tmp_path / ".agents" / "skills"
    _skill(shared, "good")
    bad = shared / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("# no frontmatter\nUse TodoWrite and ~/.claude/x\n", encoding="utf-8")
    claude = tmp_path / ".claude" / "skills"
    claude.mkdir(parents=True)
    _skill(claude, "good")  # real copy -> drift
    report = lint_home(tmp_path)
    counts = report["counts"]
    assert counts["no-frontmatter"] == 1
    assert counts["drift-copy"] == 1
    assert counts["drift-missing-link"] == 1  # 'bad' not linked in claude
    assert counts["claude-only-tools"] == 1
    assert counts["harness-home-paths"] == 1
    assert report["errors"] >= 2
