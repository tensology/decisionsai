from distr.core.harness.intake import classify_intake


def test_classifies_ui_heavy_ticket_with_codex_pressure():
    profile = classify_intake(
        "Redesign the workflow runs panel. UI critical. Need screenshots and click flow polish."
    )

    assert profile["ui_heavy"] is True
    assert profile["complexity"] == "medium"
    assert "ui_critical" in profile["risk_flags"]
    assert profile["override"] == "ui_critical"
    assert profile["route_pressure"] == "codex"
    assert any("ui critical" in reason.lower() for reason in profile["reasons"])


def test_demote_to_cursor_only_when_low_risk():
    profile = classify_intake("Demote to Cursor. Rename the button text copy.")

    assert profile["override"] == "demote_to_cursor"
    assert profile["route_pressure"] == "cursor"
    assert profile["complexity"] == "low"


def test_detects_high_risk_cross_module_auth_migration():
    profile = classify_intake("Promote to Codex. Refactor auth and database migration across modules.")

    assert profile["override"] == "promote_to_codex"
    assert profile["complexity"] == "high"
    assert {"auth", "migration", "cross_module"}.issubset(set(profile["risk_flags"]))
    assert profile["route_pressure"] == "codex"


def test_backend_ticket_preserving_frontend_is_not_ui_work():
    profile = classify_intake(
        "Verify the copied Django backend and its environment-backed settings. "
        "Preserve the existing frontend and TrackPlayer work. The recovery note is authoritative."
    )

    assert profile["ui_heavy"] is False
    assert "auth" not in profile["risk_flags"]


def test_single_line_button_change_is_ui_work():
    profile = classify_intake("Make the green button black.")

    assert profile["ui_heavy"] is True
