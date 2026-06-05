from pathlib import Path


def test_verify_agent_harness_setup_installs_claude_ecc_surface(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    home = tmp_path / "home"
    plugin = root / "vendor" / "ecc" / ".claude-plugin"
    commands = root / "vendor" / "ecc" / ".claude" / "commands"
    plugin.mkdir(parents=True)
    commands.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"ecc"}', encoding="utf-8")
    (commands / "feature-development.md").write_text("Feature workflow", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)

    monkeypatch.setattr("scripts.verify_agent_harness_setup._have", lambda command: False)

    from scripts.verify_agent_harness_setup import verify_agent_harness_setup

    result = verify_agent_harness_setup(root, home=home, quiet=True)

    assert result["claude"] is True
    assert (home / ".claude" / "plugins" / "local" / "ecc" / "plugin.json").is_file()
    assert (home / ".claude" / "commands" / "feature-development.md").is_file()
