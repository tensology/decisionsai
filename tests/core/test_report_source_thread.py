import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.reports import source_thread


def write_session(root, name, events):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(e) for e in events))
    return path


@pytest.fixture(autouse=True)
def projects(monkeypatch):
    monkeypatch.setattr('distr.core.reports.month.load_projects', lambda *a: {7: {'name': 'Client', 'folder': '/work/client'}})


def test_historical_exact_id_with_paging_and_project(tmp_path):
    events = [{'id': 'old-thread', 'cwd': '/work/client'}]
    events += [{'type': 'message', 'message': {'role': 'user' if i % 2 == 0 else 'assistant', 'content': [{'type': 'text', 'text': f'Message {i}'}]}} for i in range(7)]
    write_session(tmp_path, 'historic/session.jsonl', events)
    write_session(tmp_path, 'recent.jsonl', [{'id': 'recent', 'cwd': '/work/other'}])
    last = source_thread.read_source_thread('pi_local:old-thread', pi_root=tmp_path, limit=3)
    assert last['thread_id'] == 'old-thread'
    assert last['project_id'] == 7
    assert [m['index'] for m in last['messages']] == [4, 5, 6]
    earlier = source_thread.read_source_thread('pi_local:old-thread', pi_root=tmp_path, limit=3, before=last['before'])
    assert [m['index'] for m in earlier['messages']] == [1, 2, 3]
    first = source_thread.read_source_thread('pi_local:old-thread', pi_root=tmp_path, limit=3, before=earlier['before'])
    assert [m['index'] for m in first['messages']] == [0]
    assert first['before'] is None


def test_codex_conversation_excludes_tools_and_duplicate_event_messages(tmp_path):
    path = write_session(tmp_path, 'rollout.jsonl', [
        {'type': 'session_meta', 'payload': {'id': 'exact', 'cwd': '/work/client'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<script>test</script>'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Recorded result'}]}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Recorded result'}},
        {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'private tool detail'}},
    ])
    original = path.read_bytes()
    data = source_thread.read_source_thread('codex_local:exact', codex_root=tmp_path)
    assert [m['text'] for m in data['messages']] == ['<script>test</script>', 'Recorded result']
    assert path.read_bytes() == original


def test_compressed_codex_archive_preserves_exact_thread_clock_and_source(tmp_path):
    import zstandard
    from distr.core.reports.month import _session_activity
    rows = [
        {'type':'session_meta','timestamp':'2026-10-23T08:00:00Z','payload':{'id':'archived-exact','cwd':'/work/client'}},
        {'type':'event_msg','timestamp':'2026-10-23T08:00:01Z','payload':{'type':'user_message','message':'Correct registration validation.'}},
        {'type':'response_item','timestamp':'2026-10-23T08:15:01Z','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Corrected validation and verified the regression tests.'}]}},
    ]
    path=tmp_path/'historical.jsonl.zst'
    path.write_bytes(zstandard.ZstdCompressor().compress(('\n'.join(json.dumps(row) for row in rows)+'\n').encode()))
    original=path.read_bytes()
    meta,stamps=_session_activity(path,kind='codex')
    assert meta['id']=='archived-exact'
    assert stamps[-1]-stamps[0]==__import__('datetime').timedelta(minutes=15,seconds=1)
    result=source_thread.read_source_thread('codex_local:archived-exact',codex_root=tmp_path,
              start=datetime(2026,10,23,8),end=datetime(2026,10,23,8,16))
    assert result['thread_id']=='archived-exact' and result['scoped']
    assert result['messages'][0]['text']=='Corrected validation and verified the regression tests.'
    assert path.read_bytes()==original and not (tmp_path/'historical.jsonl').exists()


def test_corrupt_archive_does_not_hide_a_valid_exact_source_thread(tmp_path):
    (tmp_path/'broken.jsonl.zst').write_bytes(b'not a zstandard frame')
    write_session(tmp_path,'valid.jsonl',[{'id':'valid','cwd':'/work/client'}])
    result=source_thread.read_source_thread('pi_local:valid',pi_root=tmp_path)
    assert result['thread_id']=='valid'


def test_archived_continuation_is_selected_by_exact_variant_not_parent_uuid(tmp_path):
    import zstandard
    original_id='01a17ddf-acc7-70e3-809e-0909fd1edfb0'
    branch_id='01a17de9-367c-7841-9021-f7a040f17091'
    rows=[{'type':'session_meta','payload':{'id':original_id,'cwd':'/work/client'}},
          {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Continued billing changes.'}]}}]
    write_session(tmp_path,f'parent-{original_id}.jsonl',rows[:1])
    archive=tmp_path/f'rollout-2026-10-27T17-09-58-{original_id}_{branch_id}.jsonl.zst'
    archive.write_bytes(zstandard.ZstdCompressor().compress('\n'.join(json.dumps(r) for r in rows).encode()))
    parent=source_thread.read_source_thread('codex_local:'+original_id,codex_root=tmp_path)
    assert parent['messages']==[]
    result=source_thread.read_source_thread(f'codex_local:{original_id}_{branch_id}',codex_root=tmp_path)
    assert result['thread_id']==original_id
    assert result['messages'][0]['text']=='Continued billing changes.'
    assert result['source_variant']==f'{original_id}_{branch_id}'
    (tmp_path/f'parent-{original_id}.jsonl').unlink()
    with pytest.raises(FileNotFoundError):
        source_thread.read_source_thread('codex_local:'+original_id,codex_root=tmp_path)


def test_default_codex_lookup_includes_archived_sessions_without_extracting(tmp_path,monkeypatch):
    import zstandard
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    root=tmp_path/'.codex'/'archived_sessions';root.mkdir(parents=True)
    rows=[{'type':'session_meta','payload':{'id':'archived-only','cwd':'/work/client'}},
          {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Historical client work.'}]}}]
    path=root/'older.jsonl.zst';path.write_bytes(zstandard.ZstdCompressor().compress('\n'.join(json.dumps(r) for r in rows).encode()))
    original=path.read_bytes()
    result=source_thread.read_source_thread('codex_local:archived-only')
    assert result['messages'][0]['text']=='Historical client work.'
    assert path.read_bytes()==original and not (root/'older.jsonl').exists()


def test_missing_and_invalid_reference_do_not_fall_back_to_another_thread(tmp_path):
    write_session(tmp_path, 'other.jsonl', [{'id': 'other', 'cwd': '/work/client'}])
    with pytest.raises(FileNotFoundError):
        source_thread.read_source_thread('pi_local:missing', pi_root=tmp_path)
    with pytest.raises(ValueError):
        source_thread.read_source_thread('pi_local:../../secret', pi_root=tmp_path)


def test_symlinks_and_ambiguous_duplicate_ids_are_rejected(tmp_path):
    root = tmp_path / 'sessions'
    outside = write_session(tmp_path, 'outside.jsonl', [{'id': 'hidden'}])
    root.mkdir()
    (root / 'link.jsonl').symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        source_thread.read_source_thread('pi_local:hidden', pi_root=root)
    write_session(root, 'one.jsonl', [{'id': 'same'}])
    write_session(root, 'two.jsonl', [{'id': 'same'}])
    with pytest.raises(RuntimeError, match='Multiple'):
        source_thread.read_source_thread('pi_local:same', pi_root=root)


def test_empty_and_long_messages_are_explicit(tmp_path):
    write_session(tmp_path, 'empty.jsonl', [{'id': 'empty', 'cwd': '/other'}])
    data = source_thread.read_source_thread('pi_local:empty', pi_root=tmp_path)
    assert data['messages'] == [] and data['project_id'] is None
    write_session(tmp_path, 'long.jsonl', [{'id': 'long'}, {'message': {'role': 'assistant', 'content': 'x' * 21000}}])
    row = source_thread.read_source_thread('pi_local:long', pi_root=tmp_path)['messages'][0]
    assert len(row['text']) == 20000 and row['truncated']


def test_http_missing_thread_and_no_store(monkeypatch):
    from distr.gui.web.routes.development.reports import register_routes
    router, app = APIRouter(), FastAPI()
    register_routes(router, None)
    app.include_router(router)
    def missing(*a, **kw):
        raise FileNotFoundError('Original missing')
    monkeypatch.setattr(source_thread, 'read_source_thread', missing)
    client = TestClient(app)
    assert client.get('/workflows/studio/reports/source-thread', params={'ref': 'pi_local:gone'}).status_code == 404
    monkeypatch.setattr(source_thread, 'read_source_thread', lambda *a, **kw: {'thread_id': 'exact'})
    response = client.get('/workflows/studio/reports/source-thread', params={'ref': 'pi_local:exact'})
    assert response.json()['thread_id'] == 'exact'
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('backend', ['codex', 'cursor'])
def test_execution_resolves_exact_original_thread_not_execution_id(tmp_path, monkeypatch, backend):
    packet = {'external_thread_id': 'original-thread', 'cwd': '/work/client'}
    @contextmanager
    def session():
        yield SimpleNamespace(get=lambda model, ident: SimpleNamespace(input_packet=json.dumps(packet), route_backend=backend))
    if backend == 'cursor':
        monkeypatch.setattr('distr.core.external_agent_context._cursor_transcripts_root_for_folder', lambda folder: tmp_path)
        write_session(tmp_path, 'original-thread/original-thread.jsonl', [{'role': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Cursor source'}]}}])
    else:
        write_session(tmp_path, 'old.jsonl', [{'type': 'session_meta', 'payload': {'id': 'original-thread', 'cwd': '/work/client'}}, {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Codex source'}}])
    data = source_thread.read_source_thread('execution:12', codex_root=tmp_path, get_session=session)
    assert data['thread_id'] == 'original-thread'
    assert data['surface'] == backend
    assert data['project_id'] == 7
    assert data['messages'][0]['text'] == f'{backend.title()} source'


def test_entry_scope_shows_that_intervals_messages_and_allows_full_thread(tmp_path):
    write_session(tmp_path, 'scoped.jsonl', [
        {'id': 'scoped', 'cwd': '/work/client'},
        {'timestamp': '2026-10-27T11:59:43.887Z', 'message': {'role': 'user', 'content': 'D3 scope issue'}},
        {'timestamp': '2026-10-27T12:24:25.497Z', 'message': {'role': 'assistant', 'content': 'D3 issue resolved'}},
        {'timestamp': '2026-10-27T13:39:13Z', 'message': {'role': 'user', 'content': 'Trading bot status'}},
    ])
    kwargs = {'pi_root': tmp_path, 'start': datetime(2026, 10, 27, 11, 59, 43, 887000),
              'end': datetime(2026, 10, 27, 12, 24, 25, 497000)}
    scoped = source_thread.read_source_thread('pi_local:scoped', **kwargs)
    assert scoped['scoped']
    assert [m['text'] for m in scoped['messages']] == ['D3 scope issue', 'D3 issue resolved']
    whole = source_thread.read_source_thread('pi_local:scoped', all_messages=True, **kwargs)
    assert not whole['scoped']
    assert len(whole['messages']) == 3


@pytest.mark.parametrize('matches', [0, 1, 2])
def test_execution_without_id_requires_unique_full_instruction_and_start(tmp_path, matches):
    instruction = 'Inspect the exact registered client task'
    stamp = datetime(2026, 10, 27, 8, 0)
    for i in range(matches):
        write_session(tmp_path, f'thread{i}.jsonl', [
            {'id': f'thread{i}', 'cwd': '/work/client'},
            {'timestamp': '2026-10-27T08:00:00Z', 'message': {'role': 'user', 'content': instruction}},
            {'timestamp': '2026-10-27T08:00:02Z', 'message': {'role': 'assistant', 'content': 'Exact source result'}},
        ])
    # A similar title or the same request at a different time is insufficient.
    write_session(tmp_path, 'wrong.jsonl', [
        {'id': 'wrong', 'cwd': '/work/client'},
        {'timestamp': '2026-10-27T09:00:00Z', 'message': {'role': 'user', 'content': instruction}},
    ])
    @contextmanager
    def session():
        yield SimpleNamespace(get=lambda *a: SimpleNamespace(input_packet=json.dumps({'instruction': instruction}), route_backend='pi', started_at=stamp))
    if matches == 1:
        result = source_thread.read_source_thread('execution:123', pi_root=tmp_path, get_session=session)
        assert result['thread_id'] == 'thread0'
        assert result['messages'][-1]['text'] == 'Exact source result'
    else:
        error = FileNotFoundError if matches == 0 else RuntimeError
        with pytest.raises(error):
            source_thread.read_source_thread('execution:123', pi_root=tmp_path, get_session=session)
