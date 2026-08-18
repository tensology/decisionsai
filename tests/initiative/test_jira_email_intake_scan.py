from distr.core.kanban.jira_intake import scan_jira_email_proposals


def test_scan_collates_three_jira_mails_into_one_proposal():
    messages = [
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-1) assigned", "snippet": "x"},
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-2) updated", "snippet": "y"},
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-3) commented", "body": "ACME-3"},
        {"from": "friend@example.com", "subject": "hi", "snippet": "nope"},
    ]
    proposal = scan_jira_email_proposals(messages)
    assert proposal is not None
    assert proposal["action_type"] == "jira_intake"
    assert proposal["payload"]["collated"] is True
    assert proposal["payload"]["issue_keys"] == ["ACME-1", "ACME-2", "ACME-3"]
    assert "one batch" in proposal["description"].lower() or "stage" in proposal["description"].lower()


def test_scan_returns_none_without_jira_mail():
    assert scan_jira_email_proposals([
        {"from": "a@b.com", "subject": "Hello", "snippet": "world"},
    ]) is None
