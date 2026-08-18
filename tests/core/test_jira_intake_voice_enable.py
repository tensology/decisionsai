from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool


def test_enable_jira_intake_installs_preset_and_enables_scans(monkeypatch):
    saved = {}

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"initiative_scan_email": False, "initiative_scan_external_boards": False},
    )
    monkeypatch.setattr(
        "distr.core.settings.save_settings_to_db",
        lambda settings: saved.update(settings),
    )
    monkeypatch.setattr(
        "distr.core.initiative.draft_execute.install_automation_preset",
        lambda preset_id: {
            "status": "created",
            "automation": {
                "id": "auto_jira",
                "preset_id": preset_id,
                "schedule": {"kind": "daily", "time": "08:00"},
            },
        },
    )

    result = ProactiveOrchestratorTool()._enable_jira_intake()
    assert result["success"] is True
    assert result["preset_id"] == "jira_morning_intake"
    assert result["status"] == "created"
    assert "Jira morning intake is on" in result["spoken_summary"]
    assert saved["initiative_scan_email"] is True
    assert saved["initiative_scan_external_boards"] is True


def test_enable_jira_intake_idempotent_when_already_installed(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"initiative_scan_email": True, "initiative_scan_external_boards": True},
    )
    monkeypatch.setattr("distr.core.settings.save_settings_to_db", lambda settings: None)
    monkeypatch.setattr(
        "distr.core.initiative.draft_execute.install_automation_preset",
        lambda preset_id: {
            "status": "exists",
            "automation": {
                "id": "auto_jira",
                "preset_id": preset_id,
                "schedule": {"kind": "daily", "time": "08:00"},
            },
        },
    )
    spoken = ProactiveOrchestratorTool()._run(action="enable_jira_intake")
    assert "already on" in spoken.lower() or "already on" in spoken


def test_tool_routes_enable_action_alias():
    tool = ProactiveOrchestratorTool()
    called = {}

    def fake_enable():
        called["ok"] = True
        return {"success": True, "spoken_summary": "Jira morning intake is on.", "action": "enable_jira_intake"}

    tool._enable_jira_intake = fake_enable  # type: ignore[method-assign]
    out = tool._run(action="turn on jira intake")
    assert called["ok"] is True
    assert "Jira morning intake is on" in out
