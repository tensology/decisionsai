from pathlib import Path


def test_harness_skill_bases_projects_grok_via_cursor_surface(tmp_path: Path):
    from distr.core.harness_bootstrap import harness_skill_bases, projection_paths

    detected = {"grok": True, "cursor": False}
    bases = harness_skill_bases(tmp_path, detected)

    assert bases["grok"] == tmp_path / ".cursor" / "skills"
    assert projection_paths(tmp_path, detected, "example")["grok"] == (
        tmp_path / ".cursor" / "skills" / "example" / "SKILL.md"
    )


def test_harness_skill_bases_include_gemini_and_kiro_native_surfaces(tmp_path: Path):
    from distr.core.harness_bootstrap import harness_command_bases, harness_skill_bases

    detected = {"gemini": True, "kiro": True}
    assert harness_skill_bases(tmp_path, detected) == {
        "gemini": tmp_path / ".gemini" / "skills",
        "kiro": tmp_path / ".kiro" / "skills",
    }
    assert harness_command_bases(tmp_path, detected) == {
        "gemini": tmp_path / ".gemini" / "commands",
        "kiro": tmp_path / ".kiro" / "commands",
    }


def test_reference_skill_projection_includes_ui_crud_discipline(tmp_path: Path, monkeypatch):
    from distr.core import harness_bootstrap, harness_stack

    monkeypatch.setattr(harness_bootstrap, "detected_harnesses", lambda: {"codex": True})

    written = harness_stack._project_reference_skills(tmp_path)

    skill = tmp_path / ".codex" / "skills" / "ui-crud-discipline" / "SKILL.md"
    command = tmp_path / ".codex" / "commands" / "ui-crud-discipline.md"
    assert str(skill.parent) in written
    assert str(command) in written
    crud_text = skill.read_text(encoding="utf-8")
    assert "`UX Principles`" in crud_text
    assert "`UI Principles`" in crud_text
    assert "`Definition of Done - UI`" in crud_text

    principles = tmp_path / ".codex" / "skills" / "ui-ux-principles" / "SKILL.md"
    principles_command = tmp_path / ".codex" / "commands" / "ui-ux-principles.md"
    assert str(principles.parent) in written
    assert str(principles_command) in written
    text = principles.read_text(encoding="utf-8")
    assert "## UX Principles" in text
    assert "## UI Principles" in text
    assert "## Taste Memory" in text
    assert "## Definition of Done - UI" in text
    assert "## Changelog" in text

    completion = tmp_path / ".codex" / "skills" / "completion-protocol" / "SKILL.md"
    completion_command = tmp_path / ".codex" / "commands" / "completion-protocol.md"
    assert str(completion.parent) in written
    assert str(completion_command) in written
    completion_text = completion.read_text(encoding="utf-8")
    assert "## Coverage ledger" in completion_text
    assert "## Negative-space audit" in completion_text
    assert "completion_manifest" in completion_text


def test_global_interface_principles_have_separate_sources_and_learning_log():
    from distr.core import harness_stack

    source = harness_stack.PROJECT_ROOT / "skills" / "ui-ux-principles" / "SKILL.md"
    text = source.read_text(encoding="utf-8")

    assert text.count("## UX Principles") == 1
    assert text.count("## UI Principles") == 1
    assert "Titles, actions, information, and helper copy are not duplicated." in text
    assert "Removing any element would remove a function or necessary meaning." in text
    assert "The screen has one job, one title" in text
    assert "When the same correction appears two or three times" in text
    assert text.count("| 2026-10-14 |") >= 4


def test_interface_builder_and_tester_reference_global_principles():
    from distr.core import harness_stack

    for skill_name in ("ln-150-presentation-creator", "webapp-testing"):
        text = (harness_stack.PROJECT_ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert "GLOBAL_UI_UX_REFERENCE" in text
        assert "ui-ux-principles" in text
        assert "Definition of Done - UI" in text


def test_frac_template_exposes_canonical_ui_acceptance_coverage():
    from distr.core import harness_stack

    template = (
        harness_stack.PROJECT_ROOT
        / "distr"
        / "seed-corpus-pack"
        / "03-done-states-frac"
        / "FRAC-TEMPLATE.md"
    ).read_text(encoding="utf-8")

    assert "location, available actions, and outcome feedback are clear" in template
    assert "design tokens rather than accumulated padding" in template
    assert "page or side drawer" in template
    assert "chosen drawer becomes a full-screen sheet on mobile" in template
    assert "keyboard-only user traverses any view" in template
    assert "automatically disappearing success toast" in template
