from distr.core.kanban.jira_intake import is_jira_notification_email


def test_recognizes_atlassian_senders():
    assert is_jira_notification_email(
        from_addr="jira@acme.atlassian.net",
        subject="(PROJ-1) assigned you",
    )
    assert is_jira_notification_email(
        from_addr="notifications@atlassian.com",
        subject="Something updated",
    )


def test_rejects_normal_client_mail():
    assert not is_jira_notification_email(
        from_addr="maya@client.com",
        subject="Can we ship Friday?",
    )
    assert not is_jira_notification_email(
        from_addr="boss@company.com",
        subject="Budget for Q3",
    )
