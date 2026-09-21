import asyncio
import json
from pathlib import Path

import pytest

from distr.core.project_cli_backends import codex_proposal as adapter
from distr.core.project_cli_backends.base import ProjectTask


def task(**kwargs):
    return ProjectTask(project_id=9, project_name='Synthetic project', folder='',
        instruction='Synthetic prompt', model='selected-model',
        adapter_options={'proposal_only':True, 'proposal_system':'Planning contract'}, **kwargs)


def events(*items, complete=True):
    rows = [{'type':'item.completed', 'item':item} for item in items]
    if complete:
        rows.append({'type':'turn.completed'})
    return '\n'.join(json.dumps(row) for row in rows)


def test_exact_model_read_only_temporary_workspace_and_disabled_extensions(monkeypatch):
    calls = []
    async def run(args, **kwargs):
        calls.append((args, kwargs))
        assert Path(kwargs['cwd']).is_dir()
        if '--help' in args:
            return 0, '--ignore-user-config --ignore-rules --ephemeral --sandbox --json'
        if 'features' in args:
            return 0, '\n'.join(adapter.DISABLED_FEATURES + ('skip_host_skill_discovery',))
        return 0, events({'type':'agent_message','text':'{"reply":"Ready","edits":[]}'})
    monkeypatch.setattr(adapter.shutil, 'which', lambda name:'/verified/codex')
    monkeypatch.setattr(adapter, '_run', run)
    result = asyncio.run(adapter.send_codex_proposal(task()))
    assert result.success
    args, options = calls[-1]
    assert args[args.index('--model')+1] == 'selected-model'
    assert args[args.index('--sandbox')+1] == 'read-only'
    assert 'approval_policy="never"' in args
    assert '--ignore-user-config' in args and '--ignore-rules' in args
    assert '--ephemeral' in args and '--skip-git-repo-check' in args
    assert 'project_doc_max_bytes=0' in args and 'web_search="disabled"' in args
    for feature in adapter.DISABLED_FEATURES:
        assert args[args.index(feature)-1] == '--disable'
    assert b'Planning contract\n\nSynthetic prompt' == options['input_bytes']
    assert not Path(options['cwd']).exists()


@pytest.mark.parametrize('output', [
    events({'type':'command_execution','command':'touch secret'}),
    events({'type':'file_change'}),
    events({'type':'mcp_tool_call'}),
    events({'type':'agent_message','text':'{}'}, complete=False),
    events({'type':'agent_message','text':'not JSON'}),
    events({'type':'agent_message','text':'[]'}),
    events({'type':'agent_message','text':'{}'}, {'type':'agent_message','text':'{}'}),
    '{"type":"turn.failed"}',
])
def test_tools_partial_output_and_invalid_proposals_are_rejected(output):
    with pytest.raises(ValueError):
        adapter.parse_output(output)


def test_only_known_startup_notices_are_nonfatal():
    message = {'type':'agent_message','text':'{"reply":"Ready","edits":[]}'}
    notice = {'type':'error','message':'Skill descriptions were shortened to fit the skills context budget.'}
    assert json.loads(adapter.parse_output(events(notice, message)))['reply'] == 'Ready'
    with pytest.raises(ValueError, match='model_error'):
        adapter.parse_output(events({'type':'error','message':'Authentication failed'}, message))


def test_registry_routes_codex_proposal_without_execution_side_effects(monkeypatch):
    from types import SimpleNamespace
    from distr.core.project_cli_backends import registry
    observed = []
    async def send(request, on_event=None):
        observed.append(request)
        return adapter.BackendTaskResult(True,'codex','codex_proposal',output='{}')
    monkeypatch.setattr(adapter, 'send_codex_proposal', send)
    monkeypatch.setattr(registry, '_git_status_short', lambda *a: pytest.fail('No project snapshot'))
    monkeypatch.setattr(registry, '_workspace_state_snapshot', lambda *a: pytest.fail('No workspace snapshot'))
    result = asyncio.run(registry.run_project_task(SimpleNamespace(id=9,name='Scoped',folder_location='/private/project'),
        'Plan context', backend_id_override='codex', model_override='selected-model', adapter_options={'proposal_only':True}))
    assert result.success and len(observed) == 1
    assert observed[0].folder == '' and observed[0].model == 'selected-model'
    assert observed[0].ticket_id is None and observed[0].execution_session_id is None


def test_catalog_filters_hidden_stale_and_malformed_entries(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    monkeypatch.setattr(adapter.shutil, 'which', lambda name:'/verified/codex')
    monkeypatch.setattr(adapter.Path, 'home', lambda:tmp_path)
    monkeypatch.delenv('CODEX_HOME', raising=False)
    folder = tmp_path / '.codex'
    folder.mkdir()
    path = folder / 'models_cache.json'
    cache = {'fetched_at':datetime.now(timezone.utc).isoformat(), 'models':[
        {'slug':'selected-model','visibility':'list'}, {'slug':'hidden','visibility':'hide'},
        {'slug':'--bad model','visibility':'list'}]}
    path.write_text(json.dumps(cache))
    assert adapter.available_models() == ['selected-model']
    cache['fetched_at'] = '2020-01-01T00:00:00Z'
    path.write_text(json.dumps(cache))
    assert adapter.available_models() == []


def test_missing_required_cli_flags_fail_before_inference(monkeypatch):
    calls = []
    async def run(args, **kwargs):
        calls.append(args)
        return 0, '--json --sandbox'
    monkeypatch.setattr(adapter.shutil, 'which', lambda name:'/verified/codex')
    monkeypatch.setattr(adapter, '_run', run)
    result = asyncio.run(adapter.send_codex_proposal(task()))
    assert not result.success
    assert result.diagnostics['proposal_error']['code'] == 'isolation_flags'
    assert len(calls) == 1


def test_does_not_accept_project_directory_or_execution_capabilities(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('No process may start')
    monkeypatch.setattr(adapter, '_run', forbidden)
    request = task()
    request.folder = '/user/project'
    assert not asyncio.run(adapter.send_codex_proposal(request)).success
    request.folder = ''
    request.required_capabilities = ['files']
    assert not asyncio.run(adapter.send_codex_proposal(request)).success
