"""Live, read-only Accessibility survey. Does not change focus or the clipboard."""

import json

from distr.core.agent.tools.clipboard import clipboard_actions as ca


def test_report_accessibility_trust_and_app_support():
    trusted = ca.accessibility_is_trusted()
    apps = ca.ax_selected_text_support_by_app()
    report = {
        "accessibility_trusted": trusted,
        "apps": apps,
        "supports": [row["app"] for row in apps if row.get("supports_ax_selected_text")],
        "accessible_without_selected_text": [
            row["app"] for row in apps if row.get("accessible") and not row.get("supports_ax_selected_text")
        ],
        "inaccessible": [
            row["app"] for row in apps if not row.get("accessible")
        ],
    }
    print("\nAX_SURVEY " + json.dumps(report, indent=2))
    # Always pass: this is a diagnostic for the restart report.
    assert isinstance(trusted, bool)
    assert isinstance(apps, list)
