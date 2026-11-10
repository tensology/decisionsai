"""Read the exact recorded IDE conversation behind a report reference. No execution."""
from collections import deque
import json
from pathlib import Path
import re
from distr.core.reports.session_files import open_session, session_paths, session_variant, session_roots


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(str(p.get('text') or '') for p in content if isinstance(p, dict) and p.get('type') in {'text', 'input_text', 'output_text'})
    return ''


def read_source_thread(ref, *, before=None, limit=50, pi_root=None, codex_root=None, get_session=None, start=None, end=None, all_messages=False):
    from distr.core.reports.month import ProjectMatcher, load_projects
    from distr.core.reports.projection_store import _resolved_provider, ProjectionStoreError

    kind, _, ident = str(ref).partition(':')
    execution_cwd = ''
    lookup_instruction, execution_start = '', None
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident):
        raise ValueError('Invalid source thread reference.')
    if kind == 'execution' and ident.isdigit():
        from distr.core.db import get_session as default_session
        from distr.core.db.kanban import ProjectExecutionSession
        with (get_session or default_session)() as db:
            row = db.get(ProjectExecutionSession, int(ident))
            if row is None:
                raise FileNotFoundError('The recorded execution is no longer available.')
            try:
                packet = json.loads(row.input_packet or '{}')
            except ValueError:
                packet = {}
            if not isinstance(packet, dict):
                packet = {}
            execution_cwd = str(packet.get('cwd') or packet.get('folder') or '')
            lookup_instruction = str(packet.get('instruction') or '')
            execution_start = getattr(row, 'started_at', None)
            ident = str(packet.get('external_thread_id') or packet.get('thread_id') or '')
            kind = f'{row.route_backend}_local'
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident):
            if kind in {'pi_local', 'codex_local'} and lookup_instruction and execution_start:
                ident = ''
            else:
                raise FileNotFoundError('No original IDE thread ID was recorded for this entry.')
    roots = {'pi_local': Path(pi_root or Path.home() / '.pi/agent/sessions'),
             'codex_local': Path(codex_root or Path.home() / '.codex/sessions')}
    if kind == 'cursor_local' and execution_cwd:
        from distr.core.external_agent_context import _cursor_transcripts_root_for_folder
        cursor_root = _cursor_transcripts_root_for_folder(execution_cwd)
        if cursor_root is not None:
            roots[kind] = cursor_root
    if kind not in roots:
        raise FileNotFoundError('The original IDE transcript is not available from this source.')
    root = roots[kind]
    scan_roots = session_roots(root, kind)
    if not scan_roots:
        raise FileNotFoundError('The original IDE transcript is no longer available locally.')
    if not ident and lookup_instruction:
        import hashlib
        from distr.core.reports.month import _session_activity, _session_files
        wanted = hashlib.sha256(' '.join(lookup_instruction.split()).encode()).hexdigest()
        matches = set()
        candidates = [(candidate, active_root) for active_root in scan_roots
                      for candidate in _session_files(active_root, execution_start, kind=kind.removesuffix('_local'), include_archived=True)]
        for candidate, active_root in candidates:
            try:
                path = _resolved_provider(candidate, active_root)
                activity, _ = _session_activity(path, kind=kind.removesuffix('_local'))
                if any(event.get('instruction_hash') == wanted and abs((event['time'] - execution_start).total_seconds()) <= 5
                       for event in activity.get('activity', [])):
                    matches.add(str(activity['id']))
            except (OSError, ValueError, ProjectionStoreError):
                continue
        if len(matches) > 1:
            raise RuntimeError('Multiple IDE threads match this exact execution instruction and start time.')
        if not matches:
            raise FileNotFoundError('No original IDE thread matches this execution instruction and start time.')
        ident = matches.pop()
    found = None
    # Historical threads are resolved by their recorded ID, never by title or recency.
    candidates = ((candidate, active_root) for active_root in scan_roots for candidate in session_paths(active_root))
    for candidate, active_root in candidates:
        try:
            path = _resolved_provider(candidate, active_root)
            with open_session(path) as handle:
                first = json.loads(handle.readline(1024 * 1024))
            if not isinstance(first, dict):
                continue
            candidate_meta = first.get('payload', {}) if kind == 'codex_local' else first
            if not isinstance(candidate_meta, dict):
                continue
            candidate_id = path.stem if kind == 'cursor_local' else str(candidate_meta.get('id') or path.stem)
            variant = session_variant(path) if kind == 'codex_local' else None
            explicit_variant = variant == ident and ident.split('_', 1)[0] == candidate_id
            if candidate_id == ident or explicit_variant:
                if found is not None:
                    raise RuntimeError('Multiple source files claim this IDE thread ID.')
                found, meta = path, candidate_meta
        except (OSError, ValueError, ProjectionStoreError):
            continue
    if found is None:
        raise FileNotFoundError('The original IDE transcript is no longer available locally.')
    if found.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('This transcript is too large to preview.')
    limit = max(1, min(int(limit), 100))
    messages, fallback = deque(maxlen=limit), deque(maxlen=limit)
    counts = {'messages': 0, 'fallback': 0}
    title = ''
    skipped = False

    def append(target, key, role, content, stamp):
        nonlocal title
        text = _text(content).strip()
        if role not in {'user', 'assistant'} or not text:
            return
        if start is not None and end is not None and not all_messages and kind != 'cursor_local':
            from distr.core.reports.month import _parse_ts
            recorded = _parse_ts(str(stamp or ''))
            if recorded is None or not start <= recorded <= end:
                return
        index = counts[key]
        counts[key] += 1
        if role == 'user' and not title:
            title = ' '.join(text.split())[:160]
        if before is None or index < before:
            target.append({'index': index, 'role': role, 'text': text[:20000],
                           'truncated': len(text) > 20000, 'timestamp': stamp})

    with open_session(found) as handle:
        for line in handle:
            if len(line) > 8 * 1024 * 1024:
                skipped = True
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            stamp = event.get('timestamp')
            if kind in {'pi_local', 'cursor_local'}:
                msg = event.get('message') or {}
                if isinstance(msg, dict):
                    append(messages, 'messages', event.get('role') if kind == 'cursor_local' else msg.get('role'), msg.get('content'), stamp)
            else:
                payload = event.get('payload') or {}
                if not isinstance(payload, dict):
                    continue
                if event.get('type') == 'response_item' and payload.get('type') == 'message':
                    append(messages, 'messages', payload.get('role'), payload.get('content'), stamp)
                elif event.get('type') == 'event_msg' and payload.get('type') in {'user_message', 'agent_message'}:
                    append(fallback, 'fallback', 'user' if payload['type'] == 'user_message' else 'assistant', payload.get('message'), stamp)
    rows = list(messages if counts['messages'] else fallback)
    cwd = str(meta.get('cwd') or execution_cwd)
    projects = load_projects(get_session)
    matcher = ProjectMatcher(projects)
    pid = matcher.match(cwd)
    provenance = 'session_folder'
    session_cwd = cwd
    if start is not None and end is not None and kind in {'pi_local', 'codex_local'}:
        from distr.core.reports.month import _session_activity, session_interval_context
        activity, _ = _session_activity(found, kind=kind.removesuffix('_local'))
        context = session_interval_context(activity, start, end, matcher)
        pid, cwd, provenance = context['project_id'], context['working_directory'], context['project_provenance']
    recorded_project = pid
    if get_session is None:
        from distr.core.reports.entry_reviews import reviewed_source_project
        reviewed = reviewed_source_project(str(ref), start, end, projects)
        if reviewed is not None:
            pid, provenance = reviewed, 'reviewed_source_evidence'
    return {'ref': str(ref), 'thread_id': str(meta.get('id') or ident), 'source_variant': ident,
            'surface': kind.removesuffix('_local'),
            'title': title, 'cwd': cwd, 'project_id': pid,
            'session_cwd': session_cwd, 'project_provenance': provenance,
            'recorded_project_id': recorded_project,
            'scoped': start is not None and end is not None and not all_messages and kind != 'cursor_local',
            'project_name': (projects.get(pid) or {}).get('name') or 'Unassigned',
            'messages': rows, 'total_messages': counts['messages'] or counts['fallback'],
            'before': rows[0]['index'] if rows and rows[0]['index'] > 0 else None,
            'skipped_oversized': skipped}
