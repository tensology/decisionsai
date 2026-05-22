from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "codex_plugin" / "decisions-codex"


def test_codex_plugin_documents_cli_as_transport_and_plugin_as_behavior_layer():
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "When Codex exists on the operating system and is authenticated" in readme
    assert "treat the Codex" in readme and "CLI as the reliable execution path for now." in readme
    assert "show setup state" in readme
    assert "behavior and context layer" in readme
    assert "persistent" in readme and "goal state" in readme
    assert "workflow checkpoints" in readme
    assert "plugin-native channel" in readme


def test_codex_worker_skill_requires_evidence_blockers_and_next_step():
    skill = (PLUGIN_ROOT / "skills" / "decisions-codex-worker" / "SKILL.md").read_text(encoding="utf-8")

    assert "Evidence: ..." in skill
    assert "Blockers: ..." in skill
    assert "Next step: ..." in skill
    assert "If the Codex CLI is available and authenticated" in skill
    assert "checkpoint" in skill
    assert "retry" in skill
    assert "escalate" in skill
    assert "[DECISIONS CODEX CALLBACK]" in skill
    assert "user_steer" in skill
    assert "codex_interrupted" in skill
