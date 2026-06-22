from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "skills"
REGISTRY_FILE = SKILLS_DIR / "skills_registry.json"

NEW_SKILLS = {
    "decisions-frontier-prep",
    "decisions-harness-audit",
    "decisions-harness-optimize",
    "codebase-design",
    "domain-modeling",
    "architecture-deepening-review",
}


def _registry_rows() -> list[dict]:
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def test_reference_integration_skills_are_registered_with_provenance():
    rows = _registry_rows()
    by_id = {row["id"]: row for row in rows}

    assert NEW_SKILLS.issubset(by_id)
    assert len(by_id) == len(rows)

    for skill_id in NEW_SKILLS:
        row = by_id[skill_id]
        skill_dir = SKILLS_DIR / row["path"]
        assert (skill_dir / "SKILL.md").is_file()
        assert row["source"] == "reference-adapted"
        assert row["conflict_policy"] in {"local_preferred", "merged_selectively"}
        assert {"codex", "claude", "cursor", "gemini", "cline", "pi"}.issubset(
            set(row["target_surfaces"])
        )
        provenance = row["provenance"]
        assert provenance["repo"].startswith("https://github.com/")
        assert provenance["commit"]
        assert provenance["adapted_from"]


def test_new_reference_skills_keep_claude_specific_guidance_in_adapter_files():
    allowed_paths = {
        "references/claude-code.md",
        "references/harness-adapters.md",
    }
    forbidden = ("Workflow(", "Task(", "AskUserQuestion", "~/.claude")

    for skill_id in NEW_SKILLS:
        skill_dir = SKILLS_DIR / skill_id
        for path in skill_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(skill_dir).as_posix()
            if rel in allowed_paths:
                continue
            text = path.read_text(encoding="utf-8")
            assert not any(term in text for term in forbidden), f"{skill_id}:{rel}"


def test_reference_skills_are_explicit_not_default_pre_chain(tmp_path):
    from distr.core.capabilities_pack import merge_harness_pre_chain

    chain = merge_harness_pre_chain([], project_folder=str(tmp_path))

    assert not (NEW_SKILLS & set(chain))


def test_reference_skill_resources_project_to_codex_and_pi(tmp_path):
    from distr.core.workflow.skill_provision import push_skill_to_project

    codex_dest = push_skill_to_project(
        skill_id="decisions-frontier-prep",
        project_folder=str(tmp_path),
        backend_id="codex",
    )
    assert codex_dest
    assert (tmp_path / ".codex" / "commands" / "decisions-frontier-prep.md").is_file()
    assert (
        tmp_path
        / ".codex"
        / "commands"
        / "decisions-frontier-prep"
        / "references"
        / "queue-schema.md"
    ).is_file()

    pi_dest = push_skill_to_project(
        skill_id="decisions-harness-audit",
        project_folder=str(tmp_path),
        backend_id="pi",
    )
    assert pi_dest
    assert (tmp_path / ".pi" / "skills" / "decisions-harness-audit" / "SKILL.md").is_file()
    assert (
        tmp_path
        / ".pi"
        / "skills"
        / "decisions-harness-audit"
        / "references"
        / "harness-adapters.md"
    ).is_file()


def test_canonical_skills_contain_selective_matt_pocock_merges():
    expected = {
        "test-driven-development": [
            "public interface",
            "vertical slice",
            "horizontal",
        ],
        "systematic-debugging": [
            "feedback loop",
            "minimise",
            "ranked hypotheses",
            "post-mortem",
        ],
        "brainstorming": [
            "grilling",
            "one question at a time",
            "branch of the design tree",
        ],
        "writing-plans": [
            "vertical slice",
            "independently-grabbable",
            "tracer bullet",
        ],
    }
    for skill_id, needles in expected.items():
        text = (SKILLS_DIR / skill_id / "SKILL.md").read_text(encoding="utf-8").lower()
        for needle in needles:
            assert needle in text


def test_reference_skill_assessment_records_agentmanager_boundary():
    path = PROJECT_ROOT / "docs" / "skills" / "reference-skill-integration-assessment.md"
    text = path.read_text(encoding="utf-8")

    assert "AgentManager" in text
    assert "one-agent-per-board" in text
    assert "reference only" in text
    assert "batch agent teams" in text
