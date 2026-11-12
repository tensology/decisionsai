"""Stale Gmail intake rows clear from the Gmail folder once the message leaves the inbox (2026-11-11)."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.incoming import service as incoming


def _intake(rows):
    return type("Intake", (), {"list_inbox": lambda self, limit: rows})()


@pytest.fixture
def isolated(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(incoming, "get_session", session_scope)
    monkeypatch.setattr(incoming, "_load_mailshot_inbox", lambda max_results: ([], {"connected": True, "error": ""}))
    monkeypatch.setattr("distr.core.incoming.project_links.apply_project_links", lambda items, refresh=False: None)
    calls = {"inbox_ids": 0}

    def setup(*, gmail_rows, gmail_status, intake_rows, inbox_ids):
        monkeypatch.setattr(incoming, "_load_gmail_inbox", lambda max_results: (gmail_rows, gmail_status))
        monkeypatch.setattr("distr.core.work_intake.get_work_intake_service", lambda: _intake(intake_rows))

        def ids(**_):
            calls["inbox_ids"] += 1
            return inbox_ids
        monkeypatch.setattr(incoming, "_gmail_inbox_message_ids", ids)
        return calls
    return setup


LIVE = [{"id": "live-1", "threadId": "t1", "subject": "Still here", "labels": ["INBOX"], "body": "x"}]
DJANGO = {"event_id": 295150, "source": "gmail", "source_message_id": "gone-1", "source_thread_id": "gone-1",
          "subject": "[Django] ERROR (EXTERNAL IP): Internal Server Error", "status": "ticket_created",
          "action": "create_ticket", "ticket_id": 306, "needs_attention": False}


def _keys(result):
    return [row["key"] for row in result["items"] if row["source"] == "gmail"]


def test_cleared_gmail_message_no_longer_shows_as_intake_fallback(isolated):
    calls = isolated(gmail_rows=LIVE, gmail_status={"connected": True, "error": ""}, intake_rows=[DJANGO], inbox_ids={"live-1"})
    result = incoming.list_development_incoming(limit=50)
    assert _keys(result) == ["gmail:live-1"]
    assert result["counts"]["gmail"] == 1
    assert result["channels"]["gmail"] == {"connected": True, "error": ""}
    assert calls["inbox_ids"] == 1


def test_message_still_in_inbox_beyond_list_slice_is_kept(isolated):
    isolated(gmail_rows=LIVE, gmail_status={"connected": True, "error": ""}, intake_rows=[DJANGO], inbox_ids={"live-1", "gone-1"})
    assert "intake:295150" in _keys(incoming.list_development_incoming(limit=50))


def test_needs_attention_intake_is_kept_without_extra_gmail_call(isolated):
    calls = isolated(gmail_rows=LIVE, gmail_status={"connected": True, "error": ""},
                     intake_rows=[{**DJANGO, "needs_attention": True}], inbox_ids=set())
    assert "intake:295150" in _keys(incoming.list_development_incoming(limit=50))
    assert calls["inbox_ids"] == 0


@pytest.mark.parametrize("status", [{"connected": False, "error": "Connect Gmail in Settings to load inbox threads."},
                                    {"connected": True, "error": "Reconnect Gmail in Settings to load inbox threads."}])
def test_unknown_gmail_state_keeps_existing_fallback(isolated, status):
    calls = isolated(gmail_rows=[], gmail_status=status, intake_rows=[DJANGO], inbox_ids=set())
    result = incoming.list_development_incoming(limit=50)
    assert "intake:295150" in _keys(result)
    assert result["channels"]["gmail"] == status
    assert calls["inbox_ids"] == 0


def test_unverifiable_inbox_membership_keeps_row(isolated):
    isolated(gmail_rows=LIVE, gmail_status={"connected": True, "error": ""}, intake_rows=[DJANGO], inbox_ids=None)
    assert "intake:295150" in _keys(incoming.list_development_incoming(limit=50))


def test_local_only_listing_never_calls_gmail(isolated):
    calls = isolated(gmail_rows=LIVE, gmail_status={"connected": True, "error": ""}, intake_rows=[DJANGO], inbox_ids=set())
    result = incoming.list_development_incoming(limit=50, network=False)
    assert "intake:295150" in _keys(result)
    assert calls["inbox_ids"] == 0


class _Connector:
    pages: list = []
    connected = True
    requested: list = []

    def is_connected(self):
        return self.connected

    def _make_request(self, method, url):
        type(self).requested.append(url)
        return self.pages.pop(0) if self.pages else None


def _patch_connector(monkeypatch, pages, connected=True):
    _Connector.pages = list(pages)
    _Connector.connected = connected
    _Connector.requested = []
    monkeypatch.setattr("distr.core.agent.services.integrations.google_workspace.GoogleWorkspaceConnector", _Connector)


def test_inbox_ids_follow_pages(monkeypatch):
    _patch_connector(monkeypatch, [{"messages": [{"id": "a"}], "nextPageToken": "p2"}, {"messages": [{"id": "b"}]}])
    assert incoming._gmail_inbox_message_ids() == {"a", "b"}
    assert "pageToken=p2" in _Connector.requested[1] and "q=in%3Ainbox" in _Connector.requested[0]


@pytest.mark.parametrize("pages,connected", [([None], True), ([{"messages": []}], False),
                                             ([{"messages": [{"id": "a"}], "nextPageToken": "more"}], True)])
def test_inbox_ids_unknown_on_failure_disconnect_or_truncation(monkeypatch, pages, connected):
    _patch_connector(monkeypatch, pages * 20 if pages[0] and pages[0].get("nextPageToken") else pages, connected)
    assert incoming._gmail_inbox_message_ids(max_pages=3) is None


def test_failed_inbox_read_on_connected_account_reports_reconnect(monkeypatch):
    class Failing(_Connector):
        last_error = "Google Workspace request failed: timeout"

        def check_inbox(self, max_results, query):
            return []
    monkeypatch.setattr("distr.core.agent.services.integrations.google_workspace.GoogleWorkspaceConnector", Failing)
    assert incoming._load_gmail_inbox(max_results=10) == ([], {"connected": True, "error": "Reconnect Gmail in Settings to load inbox threads."})
