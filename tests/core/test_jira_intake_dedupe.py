from distr.core.kanban.jira_intake import filter_new_jira_keys


def test_dedupe_mixed_and_idempotent():
    assert filter_new_jira_keys(["ACME-1", "ACME-2"], []) == ["ACME-1", "ACME-2"]
    assert filter_new_jira_keys(["ACME-1", "ACME-2"], ["acme-1"]) == ["ACME-2"]
    assert filter_new_jira_keys(["ACME-1", "ACME-1", "ACME-2"], ["ACME-1", "ACME-2"]) == []
