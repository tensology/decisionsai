from distr.core.kanban.client_message_humanize import (
    build_client_work_update,
    humanize_client_message,
    humanizer_skill_path,
)


def test_humanize_strips_ai_tells_and_dashes():
    raw = "This is a pivotal update — showcasing our robust solution furthermore."
    out = humanize_client_message(raw)
    assert "—" not in out
    assert "pivotal" not in out.lower()
    assert "furthermore" not in out.lower()


def test_humanize_applies_skill_text_rules():
    path = humanizer_skill_path()
    assert path is not None and path.name == "SKILL.md"
    skill = path.read_text(encoding="utf-8")
    assert "name: humanizer" in skill
    raw = (
        "In order to achieve this goal the checkout is fixed. "
        "It is important to note that the data shows the button works. "
        "I hope this helps!"
    )
    out = humanize_client_message(raw)
    assert "in order to achieve this goal" not in out.lower()
    assert "it is important to note that" not in out.lower()
    assert "i hope this helps" not in out.lower()
    assert "checkout" in out.lower()
    assert "button works" in out.lower() or "the data shows" in out.lower()


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
    assert "quick update" not in msg.lower()
