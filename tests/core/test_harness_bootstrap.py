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
    assert "ui-ux-system" in crud_text
    assert "Definition of Done - UI" in crud_text

    # ui-ux-system is the single interface authority and is projected as a skill.
    system = tmp_path / ".codex" / "skills" / "ui-ux-system" / "SKILL.md"
    system_command = tmp_path / ".codex" / "commands" / "ui-ux-system.md"
    assert str(system.parent) in written
    assert str(system_command) in written
    system_text = system.read_text(encoding="utf-8")
    assert "single UI/UX authority" in system_text
    assert "Definition of Done - UI" in system_text
    assert "references/taste-log.md" in system_text

    # ui-ux-principles stays only as a redirect to ui-ux-system.
    principles = tmp_path / ".codex" / "skills" / "ui-ux-principles" / "SKILL.md"
    principles_command = tmp_path / ".codex" / "commands" / "ui-ux-principles.md"
    assert str(principles.parent) in written
    assert str(principles_command) in written
    text = principles.read_text(encoding="utf-8")
    assert "Redirect only" in text
    assert "ui-ux-system" in text
    assert "## UX Principles" not in text

    completion = tmp_path / ".codex" / "skills" / "completion-protocol" / "SKILL.md"
    completion_command = tmp_path / ".codex" / "commands" / "completion-protocol.md"
    assert str(completion.parent) in written
    assert str(completion_command) in written
    completion_text = completion.read_text(encoding="utf-8")
    assert "## Coverage ledger" in completion_text
    assert "## Negative-space audit" in completion_text
    assert "## Completion summary" in completion_text


def test_ui_ux_system_is_the_single_interface_authority():
    from distr.core import harness_stack

    root = harness_stack.PROJECT_ROOT / "skills"

    system_text = (root / "ui-ux-system" / "SKILL.md").read_text(encoding="utf-8")
    assert "single UI/UX authority" in system_text
    assert "## Order of authority" in system_text.replace("## 0. Order of authority", "## Order of authority")
    assert "Paul's rules" in system_text
    assert "Definition of Done - UI" in system_text
    assert "references/taste-log.md" in system_text

    # The learning log lives with the authority, not with the redirect.
    log = root / "ui-ux-system" / "references" / "taste-log.md"
    assert log.is_file()
    log_lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("20")]
    assert len(log_lines) >= 100

    # ui-ux-principles is a redirect only: no parallel rule set.
    principles = (root / "ui-ux-principles" / "SKILL.md").read_text(encoding="utf-8")
    assert "Redirect only" in principles
    assert "ui-ux-system" in principles
    assert "## UX Principles" not in principles
    assert "## UI Principles" not in principles
    assert not (root / "ui-ux-principles" / "taste-log.md").exists()


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
