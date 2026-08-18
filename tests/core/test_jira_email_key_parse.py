from distr.core.kanban.jira_intake import extract_jira_issue_keys


def test_extracts_ordered_unique_keys():
    keys = extract_jira_issue_keys(
        "[JIRA] (ACME-12) You were assigned",
        "Also see ACME-12 and OPS-9 in the body. OPS-9 again.",
    )
    assert keys == ["ACME-12", "OPS-9"]


def test_ignores_empty_and_false_friends():
    assert extract_jira_issue_keys("", None, "UTF-8 encoding") == []
    assert extract_jira_issue_keys("version 1-2 of the doc") == []
