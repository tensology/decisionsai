"""Time-entry descriptions come from the session's own recorded outcome.

The collator used to title a local CLI block with the first user message (often a
handoff-packet boilerplate). These tests pin the behaviour that the recorded
``Status: completed / Summary: ...`` (Pi) or ``task_complete`` message (Codex) is
used instead, and that the raw prompt is only a fallback when no outcome exists.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

from distr.core.reports import month


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _pi_session(root, session_id, prompt, outcome):
    directory = root / "--Users-example-proj--"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"2026-10-23T08-00-00-000Z_{session_id}.jsonl"
    rows = [
        {"type": "session", "version": 3, "id": session_id,
         "timestamp": "2026-10-23T08:00:00.000Z", "cwd": "/Users/example/proj"},
        {"type": "message", "id": "u1", "timestamp": "2026-10-23T08:00:01.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
        {"type": "message", "id": "a1", "timestamp": "2026-10-23T08:05:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "..."},
             {"type": "text", "text": outcome},
         ]}},
    ]
    _write_jsonl(path, rows)
    return path


def _matcher():
    return month.ProjectMatcher({14: {"name": "Jackcat", "folder": "/Users/example/proj"}})


def test_idle_intervals_use_their_own_work_and_explicit_project_not_one_session_title(tmp_path):
    root = tmp_path / 'sessions'
    root.mkdir()
    path = root / 'session.jsonl'
    _write_jsonl(path, [
        {'type': 'session', 'id': 'same-thread', 'cwd': '/work/org', 'timestamp': '2026-10-27T11:59:43Z'},
        {'timestamp': '2026-10-27T12:00:00Z', 'message': {'role': 'assistant', 'content': [
            {'type': 'toolCall', 'arguments': {'command': 'cd /work/org/portal && npm test'}}]}},
        {'timestamp': '2026-10-27T12:10:00Z', 'message': {'role': 'toolResult', 'content': 'Checked'}},
        {'timestamp': '2026-10-27T12:24:25Z', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'Summary: Corrected the D3 scope guard.'}]}},
        {'timestamp': '2026-10-27T13:39:13Z', 'message': {'role': 'user', 'content': 'How is the trading bot doing?'}},
        {'timestamp': '2026-10-27T13:40:00Z', 'message': {'role': 'assistant', 'content': [
            {'type': 'toolCall', 'arguments': {'workdir': '/work/trading', 'cmd': ['bash', '-lc', 'git status']}}]}},
        {'timestamp': '2026-10-27T13:50:00Z', 'message': {'role': 'toolResult', 'content': 'Checked'}},
        {'timestamp': '2026-10-27T14:03:45Z', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'Summary: Verified the trading bot and bridge.'}]}},
    ])
    matcher = month.ProjectMatcher({3: {'name': 'Portal', 'folder': '/work/org/portal'},
                                    9: {'name': 'Other', 'folder': '/work/org/other'},
                                    18: {'name': 'Trading', 'folder': '/work/trading'}})
    blocks, _, _ = month.collect_local_sessions(datetime(2026, 10, 27), datetime(2026, 10, 28), matcher,
        pi_root=root, codex_root=tmp_path / 'missing')
    # A >20min idle gap remains a gap; neither block copies the other block's summary.
    assert [b['project_id'] for b in blocks] == [3, 18]
    assert [b['title'] for b in blocks] == ['Corrected the D3 scope guard.', 'Verified the trading bot and bridge.']
    assert blocks[0]['end'] < blocks[1]['start']


def test_workspace_evidence_does_not_treat_output_or_remote_paths_as_local_cwd():
    assert month._recorded_workdirs({'command': 'echo "cd /work/client"'}) == []
    assert month._recorded_workdirs({'command': "ssh host 'cd /var/www/site && git status'"}) == []
    assert month._recorded_workdirs({'command': 'cd "/work/client folder" && git status'}) == ['/work/client folder']


def test_mixed_workspaces_are_not_silently_billed_to_the_launch_folder():
    matcher = month.ProjectMatcher({3: {'name': 'Portal', 'folder': '/work/org/portal'},
                                    18: {'name': 'Trading', 'folder': '/work/trading'}})
    meta = {'cwd': '/work/org/portal', 'activity': [
        {'time': datetime(2026, 10, 27, 8), 'role': 'assistant', 'text': '', 'workdirs': ['/work/org/portal']},
        {'time': datetime(2026, 10, 27, 8, 5), 'role': 'assistant', 'text': '', 'workdirs': ['/work/trading']}]}
    context = month.session_interval_context(meta, datetime(2026, 10, 27), datetime(2026, 10, 28), matcher)
    assert context['project_id'] is None
    assert context['project_provenance'] == 'mixed_workspaces'


def test_unregistered_repository_is_not_hidden_by_one_known_workspace():
    matcher=month.ProjectMatcher({3:{'name':'Portal','folder':'/Users/example/development/portal'}})
    meta={'cwd':'/Users/example/development/portal','activity':[
        {'time':datetime(2026,10,27,8),'role':'assistant','text':'','workdirs':['/Users/example/development/portal']},
        {'time':datetime(2026,10,27,8,5),'role':'assistant','text':'','workdirs':['/Users/example/development/mail-app']}]}
    context=month.session_interval_context(meta,datetime(2026,10,27),datetime(2026,10,28),matcher)
    assert context['project_id'] is None
    assert context['project_provenance']=='mixed_workspaces'


def test_pi_recorded_summary_becomes_the_block_title(tmp_path):
    sessions = tmp_path / "pi"
    _pi_session(
        sessions, "abc123", "# DecisionsAI step handoff Packet version: 1 ## Identity - workflow_id: 374",
        "Status: completed\nSummary: Fixed the Redis cache fallback so catalogue pages stop 500ing.",
    )
    path = next((sessions).rglob("*.jsonl"))
    meta, _stamps = month._session_activity(path, kind="pi")
    assert meta["summary"] == "Fixed the Redis cache fallback so catalogue pages stop 500ing."
    assert meta["prompt"].startswith("# DecisionsAI step handoff")

    blocks, _counts, _status = month.collect_local_sessions(
        datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 24, 0, 0), _matcher(),
        pi_root=sessions, codex_root=tmp_path / "codex-missing",
    )
    assert blocks and blocks[0]["project_id"] == 14
    assert blocks[0]["title"] == "Fixed the Redis cache fallback so catalogue pages stop 500ing."


def test_pi_without_summary_uses_final_assistant_message(tmp_path):
    sessions = tmp_path / "pi"
    _pi_session(
        sessions, "def456", "Refactor the invoice totals helper",
        "Rebuilt the invoice totals helper and verified the totals tests.",
    )
    path = next((sessions).rglob("*.jsonl"))
    meta, _stamps = month._session_activity(path, kind="pi")
    assert meta["summary"] == ""
    assert meta["last_assistant"] == "Rebuilt the invoice totals helper and verified the totals tests."

    blocks, _counts, _status = month.collect_local_sessions(
        datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 24, 0, 0), _matcher(),
        pi_root=sessions, codex_root=tmp_path / "codex-missing",
    )
    assert blocks and blocks[0]["title"] == "Rebuilt the invoice totals helper and verified the totals tests."


def test_pi_without_any_assistant_text_falls_back_to_prompt(tmp_path):
    sessions = tmp_path / "pi"
    directory = sessions / "--Users-example-proj--"
    directory.mkdir(parents=True)
    path = directory / "2026-10-23T08-00-00-000Z_jkl012.jsonl"
    _write_jsonl(path, [
        {"type": "session", "version": 3, "id": "jkl012",
         "timestamp": "2026-10-23T08:00:00.000Z", "cwd": "/Users/example/proj"},
        {"type": "message", "id": "u1", "timestamp": "2026-10-23T08:00:01.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "Tidy the settings page"}]}},
        {"type": "message", "id": "a1", "timestamp": "2026-10-23T08:00:02.000Z",
         "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "planning"}]}},
    ])
    blocks, _counts, _status = month.collect_local_sessions(
        datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 24, 0, 0), _matcher(),
        pi_root=sessions, codex_root=tmp_path / "codex-missing",
    )
    assert blocks and blocks[0]["title"] == "Tidy the settings page"


def test_clean_description_strips_status_markers_and_markdown():
    assert month.clean_description(
        "100% Done. **SSH works both ways as root**, verified with real logins:\n\n- D3 -> Hetzner: `ssh`"
    ) == "SSH works both ways as root, verified with real logins"
    assert month.clean_description(
        "50% Done. **The mail fix remains unresolved.** The latest tests provide useful evidence:"
    ) == "The mail fix remains unresolved."
    assert month.clean_description('## Root cause of "not top-right"\nA global rule gives every button a minimum') == 'Root cause of "not top-right"'
    # A greeting/no-op session is not a work description.
    assert month.clean_description("100% Done. Hello Paul. What would you like to work on?") == ""


def test_reused_session_matches_each_intervals_full_instruction(tmp_path):
    import hashlib
    root = tmp_path / 'pi'
    root.mkdir()
    prompts = ['First task ' + 'x' * 9000, 'Different later task']
    _write_jsonl(root / 'reused.jsonl', [
        {'type': 'session', 'id': 'reused', 'timestamp': '2026-10-23T08:00:00Z',
         'cwd': '/Users/example/proj'},
        *[{'type': 'message', 'timestamp': stamp, 'message': {'role': 'user', 'content': prompt}}
          for stamp, prompt in zip(['2026-10-23T08:00:01Z', '2026-10-23T12:00:00Z'], prompts)],
        {'type': 'message', 'timestamp': '2026-10-23T12:00:02Z',
         'message': {'role': 'assistant', 'content': 'Second task result'}},
    ])
    blocks, _, _ = month.collect_local_sessions(
        datetime(2026, 10, 23), datetime(2026, 10, 24), _matcher(),
        pi_root=root, codex_root=tmp_path / 'absent')
    assert [b['instruction_hash'] for b in blocks] == [
        hashlib.sha256(prompt.encode()).hexdigest() for prompt in prompts]


def test_git_commit_subjects_reads_repo_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a, **k: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, **k)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "Test")
    (repo / "a.txt").write_text("one")
    run("add", ".")
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-27T10:05:00+00:00",
           "GIT_COMMITTER_DATE": "2026-10-27T10:05:00+00:00"}
    run("commit", "-q", "-m", "Retire the n8n agents admin page", env=env)

    commits = month._git_commit_subjects(str(repo), datetime(2026, 10, 27), datetime(2026, 10, 28))
    assert commits and commits[-1][1] == "Retire the n8n agents admin page"
    # A repo with no commits in the window contributes nothing.
    assert month._git_commit_subjects(str(repo), datetime(2026, 1, 1), datetime(2026, 1, 2)) == []
    assert month._git_commit_subjects(str(tmp_path / "not-a-repo"), datetime(2026, 10, 27), datetime(2026, 10, 28)) == []


def test_codex_task_complete_message_becomes_the_block_title(tmp_path):
    codex = tmp_path / "codex"
    directory = codex / "2026" / "10" / "23"
    directory.mkdir(parents=True)
    path = directory / "rollout-2026-10-23T08-00-00-ghi789.jsonl"
    _write_jsonl(path, [
        {"timestamp": "2026-10-23T08:00:00.000Z", "ordinal": 0, "type": "session_meta",
         "payload": {"id": "ghi789", "cwd": "/Users/example/proj"}},
        {"timestamp": "2026-10-23T08:00:01.000Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "user_message", "message": "please fix the totals"}},
        {"timestamp": "2026-10-23T08:09:00.000Z", "ordinal": 2, "type": "event_msg",
         "payload": {"type": "task_complete", "last_agent_message": "Rebuilt invoice totals and redeployed."}},
    ])
    meta, _stamps = month._session_activity(path, kind="codex")
    assert meta["task_complete"] == "Rebuilt invoice totals and redeployed."

    blocks, _counts, _status = month.collect_local_sessions(
        datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 24, 0, 0), _matcher(),
        pi_root=tmp_path / "pi-missing", codex_root=codex,
    )
    assert blocks and blocks[0]["title"] == "Rebuilt invoice totals and redeployed."
