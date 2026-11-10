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


def apply_review(snapshot, *, path=None):
    data = load_review(path)
    if data is None:
        return snapshot
    from distr.core.reports.month import SAST, resolve_period, split_by_sast_day
    period = resolve_period(start=snapshot['period']['start'], end=snapshot['period']['end'])
    lo, hi = period.bounds_utc()
    reviewed_lo, reviewed_hi = resolve_period(start=data['period']['start'], end=data['period']['end']).bounds_utc()
    if hi <= reviewed_lo or lo >= reviewed_hi:
        return snapshot
    calendar_scope = data.get('scope') == 'synchronized_calendar'
    if not calendar_scope:
        reviewed_hi = min(reviewed_hi, stamp(data['reviewed_through']))
    project_map = {int(remote): int(local) for local, remote in data['project_map'].items()}
    local_ids = set(project_map.values())
    selected = set(snapshot.get('project_ids') or [])
    names = {row['id']: row['name'] for row in snapshot.get('projects', [])}
    rows = []
    invoice_only = snapshot.get('invoice_only', False)
    def local(value):
        return value.replace(tzinfo=UTC).astimezone(SAST).replace(tzinfo=None).isoformat()
    def piece(block, start, end):
        return {**block, 'start': start.isoformat()+'Z', 'end': end.isoformat()+'Z',
                'start_local': local(start), 'end_local': local(end), 'seconds': (end-start).total_seconds()}
    for block in snapshot['blocks']:
        a, z = stamp(block['start']), stamp(block['end'])
        if (not calendar_scope and block['project_id'] not in local_ids) or z <= reviewed_lo or a >= reviewed_hi:
            rows.append(block)
        else:
            # The synchronized calendar is authoritative for the reviewed period,
            # including projects with no calendar rows. Raw source activity stays
            # in History and cannot be mixed back into reconciled client totals.
            if a < reviewed_lo:
                rows.append(piece(block, a, reviewed_lo))
            if z > reviewed_hi:
                rows.append(piece(block, reviewed_hi, z))
    unbilled_ids = set(data.get('unbilled_event_ids', []))
    evidence = data.get('evidence', {})
    for event in data['calendar']:
        pid = project_map.get(event['project_id'])
        if pid is None or (selected and pid not in selected):
            continue
        if invoice_only and (not event['billable'] or event['id'] not in unbilled_ids):
            continue
        a, z = max(lo, stamp(event['start_at'])), min(hi, stamp(event['end_at']))
        if z <= a:
            continue
        proof = evidence.get(str(event['id']), {})
        ref = proof.get('ref') or f"calendar_event:{event['id']}"
        status = 'non_billable' if not event['billable'] else 'zero_rate' if event['billing_excluded'] else 'ready'
        invoiced = data.get('invoiced_events', {}).get(str(event['id']))
        rows.append(piece({
            'id': f"calendar-{event['id']}", 'source': 'audited_calendar',
            'family': proof.get('family', 'decisions'), 'source_label': 'Audited calendar work',
            'project_id': pid, 'project_name': proof.get('project_name') or names.get(pid, 'Unassigned'),
            'title': event['title'], 'ref': ref, 'related_refs': proof.get('related_refs', []),
            'thread_ref': proof.get('thread_ref'),
            'source_start': proof.get('source_start') or event['start_at'],
            'source_end': proof.get('source_end') or event['end_at'],
            'description_status': 'ready', 'invoice_status': status,
            'invoice_reason': ('Non-billable project work.' if not event['billable'] else
                               'Matches the unbilled Tensology calendar and billing preview.' if event['id'] in unbilled_ids else
                               f"Already included in unsent draft {invoiced}; excluded from unbilled time." if invoiced else
                               'Outside the current unbilled window or already covered by an invoice.'),
            'calendar_event_id': event['id'], 'invoice_reviewed_at': data['reviewed_through'],
            'starts_before_period': stamp(event['start_at']) < lo,
            'ends_after_period': stamp(event['end_at']) > hi,
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
    if calendar_scope and lo >= reviewed_lo and hi <= reviewed_hi:
        result['sources'] = [{'source': 'audited_calendar', 'label': 'Reconciled Tensology calendar',
                              'records': len(rows), 'blocks': len(rows),
                              'seconds': result['totals']['seconds'], 'state': 'data' if rows else 'empty'}]
        result['notes'] = [
            'Descriptions, projects and time blocks match the reconciled Tensology calendar for this period.',
            'Work is combined by project and South African day using actual recorded productive time. Gaps, duplicates and daily totals below 15 minutes are excluded.',
            'Raw activity remains in History. Unsynchronized activity is excluded from this calendar review.',
        ]
    result['calendar_review'] = {'reviewed_through': data['reviewed_through'], 'minimum_seconds': 900,
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
