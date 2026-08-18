from distr.core.kanban.jira_intake import jira_issue_to_intake_draft


def test_plain_and_adf_description():
    plain = jira_issue_to_intake_draft({
        "key": "ACME-7",
        "fields": {"summary": "Fix checkout", "description": "Broken button"},
    })
    assert plain["key"] == "ACME-7"
    assert plain["external_id"] == "ACME-7"
    assert plain["title"].startswith("ACME-7:")
    assert "Broken button" in plain["description"]

    adf = jira_issue_to_intake_draft({
        "key": "ACME-8",
        "fields": {
            "summary": "ADF issue",
            "description": {
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello from ADF"}],
                }],
            },
            "attachment": [{"filename": "spec.pdf", "id": "1", "content": "https://x/spec.pdf"}],
        },
    })
    assert "Hello from ADF" in adf["description"]
    assert adf["attachments_meta"][0]["filename"] == "spec.pdf"


def test_missing_fields_still_safe():
    draft = jira_issue_to_intake_draft({"key": "ACME-9", "fields": {}})
    assert draft["external_id"] == "ACME-9"
    assert "Imported from Jira" in draft["description"]
