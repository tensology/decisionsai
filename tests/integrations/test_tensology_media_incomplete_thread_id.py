import logging
from unittest.mock import MagicMock

import pytest

from distr.core import tensology_client as tc


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    tc._MEDIA_INCOMPLETE_UNREPORTED.clear()
    monkeypatch.setattr(tc, '_resolve_tensology_project', lambda ticket: {'id': 2})
    monkeypatch.setattr(tc, '_localize_tensology_attachments', lambda _client, ticket, _project: ticket)
    yield
    tc._MEDIA_INCOMPLETE_UNREPORTED.clear()


def _ticket(thread_id=''):
    return {'id': 153, 'reference': 'PROJ-T-153', 'decisions_thread_id': thread_id,
            'attachments': [{'name': 'big.mp4', 'download_error': 'Attachment is larger than limit.'}]}


def test_media_incomplete_with_thread_sends_thread_id(monkeypatch):
    monkeypatch.setattr(tc, '_existing_tensology_thread', lambda ticket: 42)
    client = MagicMock()
    assert tc._process_tensology_ticket(client, _ticket('42')) is False
    client.patch.assert_called_once()
    path, body = client.patch.call_args.args
    assert path == 'tickets/153/execution'
    assert body['thread_id'] == '42' and body['status'] == 'failed'
    assert 'Media transfer incomplete' in body['detail']


def test_media_incomplete_without_thread_skips_remote_and_logs_once(monkeypatch, caplog):
    monkeypatch.setattr(tc, '_existing_tensology_thread', lambda ticket: None)
    create = MagicMock()
    monkeypatch.setattr(tc, '_create_tensology_thread', create)
    client = MagicMock()
    with caplog.at_level(logging.WARNING, logger=tc.__name__):
        for _ in range(3):
            assert tc._process_tensology_ticket(client, _ticket()) is False
    client.patch.assert_not_called()
    client.post.assert_not_called()
    create.assert_not_called()
    hits = [r for r in caplog.records if 'incomplete media and no Development thread' in r.getMessage()]
    assert len(hits) == 1
