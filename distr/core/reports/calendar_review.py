"""Project the audited calendar data into the existing report, without changing sources."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path


def review_path():
    from distr.core.paths import DB_DIR
    return Path(DB_DIR) / 'reports' / 'calendar_review.json'


def load_review(path=None):
    target = Path(path or review_path())
    if not target.exists():
        return None
    data = json.loads(target.read_text())
    if data.get('schema') != 1 or not isinstance(data.get('calendar'), list):
        raise ValueError('The audited calendar review is invalid. Reconcile it before building a report.')
    return data


def stamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def union_seconds(spans):
    merged = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum((end-start).total_seconds() for start, end in merged)


def apply_review(snapshot, *, path=None, data=None, drafts=None, live_projects=None):
    from distr.core.reports.month import SAST, resolve_period, split_by_sast_day

    data = (load_review(path) if data is None else data) or {}
    draft_days = (drafts or {}).get('days', [])
    hidden_local_projects = set(data.get('hidden_local_project_ids', []))
    if not data and not draft_days:
        return snapshot
    period = resolve_period(start=snapshot['period']['start'], end=snapshot['period']['end'])
    lo, hi = period.bounds_utc()
    calendar_scope = data.get('scope') == 'synchronized_calendar'
    regions = []
    if data:
        reviewed_lo, reviewed_hi = resolve_period(start=data['period']['start'], end=data['period']['end']).bounds_utc()
        if not calendar_scope:
            reviewed_hi = min(reviewed_hi, stamp(data['reviewed_through']))
        regions.append((reviewed_lo, reviewed_hi, calendar_scope))
    for day in draft_days:
        day_lo, day_hi = resolve_period(start=day, end=day).bounds_utc()
        regions.append((day_lo, day_hi, True))
    if not hidden_local_projects and not any(a < hi and z > lo for a, z, _ in regions):
        return snapshot
    project_map = {int(remote): int(local) for local, remote in data.get('project_map', {}).items()}
    local_ids = set(project_map.values())
    events = data.get('calendar', [])
    connected = live_projects is not None
    if connected:
        project_map[None] = 0
    selected = set(snapshot.get('project_ids') or [])
    names = {row['id']: row['name'] for row in snapshot.get('projects', [])}
    # Calendar-only work (for example an attended office meeting) need not create
    # or reactivate a coding project just to appear in the report filter.
    if connected:
        names.update({project_map[remote]: project['name'] for remote, project in live_projects.items()})
    for event in events:
        pid = project_map.get(event['project_id'])
        proof = data.get('evidence', {}).get(str(event['id']), {})
        if pid is not None and pid not in names:
            names[pid] = proof.get('project_name') or (f"Calendar project {event['project_id']}" if pid else "Unassigned")
    rows = []
    invoice_only = snapshot.get('invoice_only', False)
    def local(value):
        return value.replace(tzinfo=UTC).astimezone(SAST).replace(tzinfo=None).isoformat()
    def piece(block, start, end):
        return {**block, 'start': start.isoformat()+'Z', 'end': end.isoformat()+'Z',
                'start_local': local(start), 'end_local': local(end), 'seconds': (end-start).total_seconds()}
    for block in snapshot['blocks']:
        if block['project_id'] in hidden_local_projects:
            continue
        spans = [(stamp(block['start']), stamp(block['end']))]
        for start, end, all_projects in regions:
            if not all_projects and block['project_id'] not in local_ids:
                continue
            remaining = []
            for a, z in spans:
                if z <= start or a >= end:
                    remaining.append((a, z))
                else:
                    if a < start:
                        remaining.append((a, start))
                    if z > end:
                        remaining.append((end, z))
            spans = remaining
        for a, z in spans:
            if a == stamp(block['start']) and z == stamp(block['end']):
                rows.append(block)
            else:
                rows.append(piece(block, a, z))
    unbilled_ids = set(data.get('unbilled_event_ids', []))
    evidence = data.get('evidence', {})
    draft_by_key = {f"report-draft:{draft['id']}": draft for draft in (drafts or {}).get('entries', [])}
    for event in events:
        pid = project_map.get(event['project_id'])
        if pid is None or (selected and pid not in selected):
            continue
        if invoice_only and (not event['billable'] or event['id'] not in unbilled_ids):
            continue
        a, z = max(lo, stamp(event['start_at'])), min(hi, stamp(event['end_at']))
        if z <= a:
            continue
        proof = evidence.get(str(event['id']), {})
        if event.get('ticket_source') == 'decisionsai':
            proof = draft_by_key.get(event.get('ticket_key'), proof)
        ref = proof.get('ref') or f"calendar_event:{event['id']}"
        status = 'non_billable' if not event['billable'] else 'zero_rate' if event['billing_excluded'] else 'ready'
        needs_review = proof.get('description_status') == 'needs_review'
        if needs_review:
            status = 'needs_review'
        invoiced = data.get('invoiced_events', {}).get(str(event['id']))
        editor = {}
        if connected:
            from distr.core.reports.calendar_sync import edit_lock, edit_version
            lock = edit_lock(event, data)
            editor = {'calendar_edit_lock': lock, 'calendar_version': edit_version(event),
                      'calendar_project_id': event['project_id'], 'entry_start': event['start_at'],
                      'entry_end': event['end_at'], 'entry_start_local': local(stamp(event['start_at'])),
                      'entry_end_local': local(stamp(event['end_at']))}
        rows.append(piece({
            **editor, 'id': f"calendar-{event['id']}", 'source': 'audited_calendar',
            'family': proof.get('family', 'decisions'), 'source_label': 'Tensology calendar' if connected else 'Audited calendar work',
            'project_id': pid, 'project_name': names.get(pid, 'Unassigned') if connected else proof.get('project_name') or names.get(pid, 'Unassigned'),
            'title': event['title'], 'ref': ref, 'related_refs': proof.get('related_refs', []),
            'thread_ref': proof.get('thread_ref'),
            'source_start': proof.get('source_start') or event['start_at'],
            'source_end': proof.get('source_end') or event['end_at'],
            'description_status': 'needs_review' if needs_review else 'ready', 'invoice_status': status,
            'timing_basis': proof.get('timing_basis', 'Allocated active work; the block position is not a continuous attendance record.'),
            'reviewed_description': None if connected else proof.get('reviewed_description'),
            'invoice_reason': (proof.get('invoice_reason') if needs_review else 'Non-billable project work.' if not event['billable'] else
                               'Matches the unbilled Tensology calendar and billing preview.' if event['id'] in unbilled_ids else
                               f"Already included in unsent draft {invoiced}; excluded from unbilled time." if invoiced else
                               'Outside the current unbilled window or already covered by an invoice.'),
            'calendar_event_id': event['id'], 'invoice_reviewed_at': data['reviewed_through'],
            'starts_before_period': stamp(event['start_at']) < lo,
            'ends_after_period': stamp(event['end_at']) > hi,
        }, a, z))
    if drafts and not invoice_only:
        from distr.core.reports.calendar_sync import unsaved_drafts
        remote_by_local = {local_id: remote for remote, local_id in project_map.items()}
        for draft in unsaved_drafts(drafts, events):
            pid = draft['project_id']
            if pid in hidden_local_projects or (selected and pid not in selected):
                continue
            start, end = stamp(draft['start_at']), stamp(draft['end_at'])
            a, z = max(lo, start), min(hi, end)
            if z <= a:
                continue
            names.setdefault(pid, draft['project_name'])
            rows.append(piece({
                **{key: draft[key] for key in ('project_id', 'family', 'title', 'ref', 'related_refs',
                                               'thread_ref', 'source_start', 'source_end', 'timing_basis')},
                'id': f"draft-{draft['id']}", 'calendar_draft_id': draft['id'],
                'source': 'calendar_draft', 'source_label': 'Draft',
                'project_name': names[pid], 'description_status': 'ready', 'invoice_status': 'needs_review',
                'invoice_reason': 'Save to Tensology as non-billable time.',
                'calendar_project_id': remote_by_local.get(pid),
                'entry_start_local': local(start), 'entry_end_local': local(end),
                'entry_start': draft['start_at'], 'entry_end': draft['end_at'],
            }, a, z))
    rows.sort(key=lambda row: (row['start'], row['id']))
    project_spans, day_spans, family_spans = defaultdict(list), defaultdict(list), defaultdict(list)
    day_projects, activity = defaultdict(lambda: defaultdict(list)), defaultdict(float)
    for block in rows:
        pid = block['project_id']; a, z = stamp(block['start']), stamp(block['end'])
        project_spans[pid].append((a,z)); family_spans[block['family']].append((a,z))
        activity[pid] += (z-a).total_seconds()
        for day, start, end in split_by_sast_day(a,z):
            day_spans[day].append((start,end)); day_projects[day][str(pid)].append((start,end))
    result = dict(snapshot)
    result['projects'] = [{'id': pid, 'name': name} for pid, name in names.items()]
    result['blocks'] = rows
    result['by_project'] = sorted([
        {'project_id': pid, 'name': next(row['project_name'] for row in rows if row['project_id']==pid),
         'seconds': union_seconds(spans), 'activity_seconds': activity[pid], 'blocks': sum(row['project_id']==pid for row in rows),
         'days': sum(str(pid) in value for value in day_projects.values()),
         'first_start_local': local(min(a for a,z in spans))}
        for pid, spans in project_spans.items()], key=lambda row: -row['seconds'])
    result['by_day'] = [{'date': day, 'seconds': union_seconds(spans),
                         'projects': {pid: union_seconds(parts) for pid,parts in day_projects[day].items()}}
                        for day,spans in sorted(day_spans.items())]
    result['by_family'] = [{**row, 'seconds': union_seconds(family_spans.get(row['family'], [])),
                            'blocks': sum(block['family'] == row['family'] for block in rows)}
                           for row in snapshot.get('by_family', [])]
    result['totals'] = {**snapshot['totals'], 'seconds': union_seconds([(stamp(r['start']),stamp(r['end'])) for r in rows]),
                        'activity_seconds': sum(activity.values()), 'blocks': len(rows), 'projects': len(project_spans),
                        'active_days': len(day_spans)}
    if connected:
        result['calendar_connected'] = True
        result['calendar_projects'] = sorted(
            [{'id': pid, 'name': project['name']} for pid, project in live_projects.items()],
            key=lambda project: project['name'].casefold(),
        )
        result['sources'] = [{'source': 'audited_calendar', 'label': 'Tensology calendar',
                              'records': len(events), 'blocks': sum(row['source'] == 'audited_calendar' for row in rows),
                              'state': 'data' if events else 'empty'}]
        result['notes'] = ['Saved time and projects are loaded from Tensology. Drafts appear until saved. Raw activity is available in History.']
    elif calendar_scope and lo >= reviewed_lo and hi <= reviewed_hi:
        result['sources'] = [{'source': 'audited_calendar', 'label': 'Reconciled Tensology calendar',
                              'records': len(rows), 'blocks': len(rows),
                              'seconds': result['totals']['seconds'], 'state': 'data' if rows else 'empty'}]
        result['notes'] = [
            'Descriptions, projects and time blocks match the reconciled Tensology calendar for this period.',
            'Work is combined by project and South African day using completed source turns and confirmed meetings. Agent activity is not a measurement of personal labour. Gaps, duplicates and daily totals below 15 minutes are excluded.',
            'Raw activity remains in History. Unsynchronized activity is excluded from this calendar review.',
        ]
    result['calendar_review'] = {'reviewed_through': data.get('reviewed_through'), 'minimum_seconds': 900,
                                 'scope': data.get('scope', 'reviewed_projects'),
                                 'source': 'Tensology calendar and native billing preview',
                                 'held_work_items': len(data.get('holds', []))}
    result['invoice_summary'] = {
        'ready': sum(row.get('invoice_status') in {'ready', 'zero_rate'} for row in rows),
        'non_billable': sum(row.get('invoice_status') == 'non_billable' for row in rows),
        'needs_review': sum(row.get('invoice_status') == 'needs_review' for row in rows),
    }
    result['highlights'] = {str(pid): [{'title': row.get('title') or 'Work description needs review.',
                                      'seconds': (stamp(row['end'])-stamp(row['start'])).total_seconds(), 'days': 1,
                                      'sources': [row.get('source_label', '')]} for row in rows if row['project_id']==pid]
                            for pid in project_spans}
    return result
