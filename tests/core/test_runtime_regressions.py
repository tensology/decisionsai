from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool
from distr.core.db import Settings
from distr.core.integrations.relay_auth import canonical_relay_url


def test_runtime_contract_regressions():
    tool = ScreenshotAnalyzerTool()
    assert tool.normalize_tool_args({"query": "Find Save"})["prompt"] == "Find Save"
    assert tool.normalize_tool_args({})["prompt"] == "Describe the current screen."

    assert canonical_relay_url("https://www.decisionsai.net/api/whatsapp") == (
        "https://decisions.tensology.com/api/whatsapp"
    )
    assert canonical_relay_url("wss://decisionsai.net/ws/telegram") == (
        "wss://decisions.tensology.com/ws/telegram"
    )

    columns = Settings.__table__.columns
    for name in (
        "jira_auto_transition_on_cli_complete",
        "jira_auto_transition_target_status",
        "whatsapp_send_dry_run",
        "mempalace_memory_backend",
        "cost_ledger_enabled",
        "cost_invoice_display",
    ):
        assert name in columns
