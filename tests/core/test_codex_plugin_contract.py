from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "plugins" / "codex-ide"


def test_codex_plugin_documents_ide_first_and_cli_fallback():
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Codex IDE/chat surface as the preferred execution path" in readme
    assert "Codex CLI remains a fallback transport" in readme
    assert "show setup state" in readme
    assert "IDE behavior and context layer" in readme
    assert "project IDE session reporter" in readme
    assert "workflow checkpoints" in readme


def test_codex_worker_skill_requires_evidence_blockers_and_next_step():
    skill = (PLUGIN_ROOT / "skills" / "decisions-codex-worker" / "SKILL.md").read_text(encoding="utf-8")

    assert "Evidence: ..." in skill
    assert "Blockers: ..." in skill
    assert "Next step: ..." in skill
    assert "Prefer the Codex IDE/chat surface as the primary execution context" in skill
    assert "is fallback transport" in skill
    assert "checkpoint" in skill
    assert "retry" in skill
    assert "escalate" in skill
    assert "[DECISIONS CODEX CALLBACK]" in skill
    assert "user_steer" in skill
    assert "codex_interrupted" in skill
    assert "For any normal Codex IDE/chat prompt inside a DecisionsAI project folder" in skill
    assert "--turn-input" in skill
    assert "--turn-output" in skill
    assert "exits quietly" in skill


def test_codex_reporter_supports_project_ide_session_endpoint():
    reporter = (PLUGIN_ROOT / "scripts" / "report_decisions_event.py").read_text(encoding="utf-8")

    assert "--callback-url" in reporter
    assert "--cwd" in reporter
    assert "/api/ide/sessions/event" in reporter
    assert "--turn-input" in reporter
    assert "--turn-output" in reporter
    assert '"session_id": session_id if session_id is not None else args.execution_session_id' in reporter
    assert "--strict" in reporter
    assert "return 0, \"\"" in reporter


def test_codex_plugin_installer_repairs_active_codex_cache():
    installer = (PLUGIN_ROOT / "scripts" / "install_local.py").read_text(encoding="utf-8")

    assert '"plugin", "add", selector' in installer
    assert 'selector = f"{PLUGIN_NAME}@{marketplace_name}"' in installer
    assert "INSTALLED_BY_DEFAULT" in installer
    assert "Restart Codex or reload plugins, then enable DecisionsAI Codex from the plugin list." not in installer
