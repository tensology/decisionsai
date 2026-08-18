from types import SimpleNamespace

from distr.core.kanban.jira_intake import fetch_jira_issues


def test_fetch_skips_failed_keys(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/ACME-1"):
            return Resp(200, {"key": "ACME-1", "fields": {"summary": "One"}})
        return Resp(404, {})

    issues = fetch_jira_issues(
        {"email": "a@b.c", "api_token": "t", "server_url": "https://acme.atlassian.net"},
        ["ACME-1", "ACME-404"],
        http_get=fake_get,
    )
    assert [i["key"] for i in issues] == ["ACME-1"]
    assert len(calls) == 2
