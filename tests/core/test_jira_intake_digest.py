from distr.core.kanban.jira_intake import format_jira_intake_digest, intake_markup


def test_digest_empty_and_multi():
    assert format_jira_intake_digest([]) == ""
    text = format_jira_intake_digest([
        {"id": 11, "external_id": "ACME-1", "title": "ACME-1: Fix checkout"},
        {"id": 12, "key": "ACME-2", "title": "Add invoice PDF"},
    ])
    assert "2 new Jira ticket(s)" in text
    assert "1. ACME-1: Fix checkout (#11)" in text
    assert "ACME-2" in text
    assert "Run them" in text


def test_intake_controls():
    labels = [b["text"] for b in intake_markup("tok")["inline_keyboard"][0]]
    assert labels == ["Run all", "Prioritize", "Ignore"]
