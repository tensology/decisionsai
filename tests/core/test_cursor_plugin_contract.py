from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "plugins" / "cursor-ide"


def test_cursor_plugin_manifest_and_worker_skill_exist():
    manifest = (PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
    skill = (PLUGIN_ROOT / "skills" / "decisions-cursor-worker" / "SKILL.md").read_text(encoding="utf-8")

    assert '"name": "decisions-cursor"' in manifest
    assert '"displayName": "DecisionsAI Cursor"' in manifest
    assert '"skills": "./skills/"' in manifest
    assert "Status: completed | failed | needs_input" in skill
    assert "[DECISIONS CURSOR CALLBACK]" in skill
    assert "cursor_completed" in skill


def test_cursor_plugin_installer_targets_cursor_local_plugin_folder():
    installer = (PLUGIN_ROOT / "scripts" / "install_local.py").read_text(encoding="utf-8")

    assert 'Path.home() / ".cursor" / "plugins" / "local" / PLUGIN_NAME' in installer
    assert '".cursor-plugin" / "plugin.json"' in installer


def test_cursor_plugin_ships_decisions_event_reporter():
    reporter = (PLUGIN_ROOT / "scripts" / "report_decisions_event.py").read_text(encoding="utf-8")

    assert "Report Cursor-side workflow events back to DecisionsAI" in reporter
    assert "--callback-url" in reporter
    assert "--cwd" in reporter
    assert "_discover_packet_meta" in reporter
    assert "/codex-events" in reporter
    assert "execution_session_id" in reporter
    assert "urllib.request" in reporter


def test_cursor_worker_requires_prompt_by_prompt_orchestrator_visibility():
    skill = (PLUGIN_ROOT / "skills" / "decisions-cursor-worker" / "SKILL.md").read_text(encoding="utf-8")

    assert "Prefer the Cursor IDE/chat surface as the primary execution context" in skill
    assert "is fallback transport" in skill
    assert "after every user prompt" in skill
    assert "DecisionsAI is reachable" in skill
    assert "cursor_prompt_submitted" in skill
    assert "For any normal Cursor IDE/chat prompt inside a DecisionsAI project folder" in skill
    assert "--turn-input" in skill
    assert "--turn-output" in skill
    assert "exits quietly" in skill


def test_cursor_reporter_supports_turn_reporting_and_quiet_offline_mode():
    reporter = (PLUGIN_ROOT / "scripts" / "report_decisions_event.py").read_text(encoding="utf-8")

    assert "--turn-input" in reporter
    assert "--turn-output" in reporter
    assert '"session_id": session_id if session_id is not None else args.execution_session_id' in reporter
    assert "--strict" in reporter
    assert "return 0, \"\"" in reporter
