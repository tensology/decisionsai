from distr.core.kanban.client_message_humanize import build_client_work_update, humanize_client_message


def test_humanize_strips_ai_tells_and_dashes():
    raw = "This is a pivotal update — showcasing our robust solution furthermore."
    out = humanize_client_message(raw)
    assert "—" not in out
    assert "pivotal" not in out.lower()
    assert "furthermore" not in out.lower()


def test_build_client_update_is_direct():
    msg = build_client_work_update(
        contact="Maya",
        work_title="ACME-2: Fix checkout",
        result_summary="Checkout passes browser validation.",
        time_spent="1h",
    )
    assert "Maya" in msg
    assert "checkout" in msg.lower()
    assert "—" not in msg
