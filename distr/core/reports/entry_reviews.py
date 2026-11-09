"""Interval-specific invoice review overlays. Recorded times and provider files stay intact."""
from datetime import datetime, UTC
import json
from pathlib import Path

from distr.core.reports.entry_descriptions import entry_key


def review_path():
    from distr.core.paths import DB_DIR
    return Path(DB_DIR) / 'reports' / 'invoice_entry_reviews.json'


def load_reviews(path=None):
    try:
        payload = json.loads(Path(path or review_path()).read_text())
        return payload.get('entries', {}) if payload.get('schema') == 1 else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _valid(row, projects):
    return (isinstance(row, dict) and row.get('invoice_status') in {'ready', 'zero_rate', 'non_billable', 'needs_review'}
            and isinstance(row.get('project_id'), int) and row['project_id'] in {0, *projects}
            and (row['project_id'] != 0 or row['invoice_status'] not in {'ready', 'zero_rate'})
            and isinstance(row.get('description'), str) and 20 <= len(row['description']) <= 400
            and bool(row.get('reviewed_at')) and bool(row.get('evidence')))


def apply_project_reviews(blocks, projects, *, path=None):
    """Apply exact interval ownership before duplicate matching and project filtering."""
    records = load_reviews(path)
    for block in blocks:
        row = records.get(entry_key(block))
        if not _valid(row, projects):
            continue
        block['project_id'] = row['project_id']
        block['project_provenance'] = 'reviewed_source_evidence'
    return blocks


def _decorate(block, row):
    return {**block, 'recorded_title': block.get('recorded_title') or block['title'],
            'title': ' '.join(row['description'].split()), 'project_id': row['project_id'],
            'project_provenance': 'reviewed_source_evidence',
            'description_status': 'needs_review' if row['invoice_status'] == 'needs_review' and not row.get('description_verified') else 'ready',
            'invoice_status': row['invoice_status'], 'invoice_reason': row.get('reason', ''),
            'invoice_reviewed_at': row['reviewed_at'], 'invoice_evidence': row['evidence'],
            'invoice_source_missing': bool(row.get('source_missing')),
            'invoice_source_note': row.get('source_note', ''),
            'invoice_source_excerpt': row.get('source_excerpt', '')}


def apply_invoice_reviews(blocks, projects, *, path=None):
    """Decorate reviewed entries, or split mixed work only at evidenced contiguous bounds."""
    records = load_reviews(path)
    result = []
    for block in blocks:
        row = records.get(entry_key(block))
        if not _valid(row, projects):
            result.append(block)
            continue
        # An explicit UI reassignment after the review wins, and requires a fresh review.
        if block.get('manual_project_override') and block['project_id'] != row['project_id']:
            result.append({**_decorate(block, row), 'project_id': block['project_id'],
                           'project_provenance': 'manual', 'invoice_status': 'needs_review',
                           'invoice_reason': 'Project changed after the evidence review.',
                           'description_status': 'needs_review'})
            continue
        segments = row.get('segments')
        if not segments:
            result.append(_decorate(block, row))
            continue
        pieces = []
        cursor = block['start']
        for segment in segments:
            try:
                def stamp(value):
                    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
                start, end = stamp(segment['start']), stamp(segment['end'])
            except (ValueError, TypeError, KeyError):
                break
            reviewed = {**row, **segment}
            if start != cursor or end <= start or end > block['end'] or not _valid(reviewed, projects):
                break
            part = _decorate({**block, 'start': start, 'end': end}, reviewed)
            part['review_parent_key'] = entry_key(block)
            pieces.append(part)
            cursor = end
        else:
            if pieces and cursor == block['end']:
                result.extend(pieces)
                continue
        # Reject partial, overlapping or stale allocations as a whole, never lose time.
        result.append({**block, 'description_status': 'needs_review', 'invoice_status': 'needs_review',
                       'invoice_reason': 'The reviewed allocation no longer matches the full recorded interval.'})
    return result


def apply_billing_policy(blocks, *, path=None):
    """Respect verified billing settings and standing zero-charge rules without changing them."""
    try:
        payload = json.loads(Path(path or review_path()).read_text())
        policies = payload.get('project_billing', {}) if payload.get('schema') == 1 else {}
        activity = payload.get('activity_billing', {}) if payload.get('schema') == 1 else {}
    except (OSError, ValueError, AttributeError):
        return blocks
    for block in blocks:
        try:
            decision = activity.get(entry_key(block)) if activity else None
        except (KeyError, TypeError, ValueError):
            decision = None
        if (isinstance(decision, dict) and decision.get('mode') == 'non_billable'
                and decision.get('decided_at') and decision.get('evidence') and decision.get('basis')):
            block['invoice_status'] = 'non_billable'
            block['invoice_reason'] = decision['basis']
        policy = policies.get(str(block.get('project_id')))
        if not isinstance(policy, dict) or not policy.get('checked_at') or not policy.get('basis'):
            continue
        block['billing_project_id'] = policy.get('billing_project_id')
        block['billing_policy_asof'] = policy['checked_at']
        if policy.get('mode') == 'non_billable':
            block['invoice_status'] = 'non_billable'
            block['invoice_reason'] = policy['basis']
        elif policy.get('mode') == 'unknown' and block.get('invoice_status') != 'non_billable':
            block['invoice_status'] = 'needs_review'
            block['invoice_reason'] = policy['basis']
        elif policy.get('mode') == 'zero_rate' and block.get('invoice_status') != 'non_billable':
            if block.get('description_status') == 'ready':
                block['invoice_status'] = 'zero_rate'
                block['invoice_reason'] = policy['basis']
                block['invoice_rate_override'] = 0
                block['invoice_strikethrough'] = True
    return blocks


def reviewed_source_project(ref, start, end, projects, *, path=None):
    """Return one reviewed owner for these exact source bounds, never a session-wide guess."""
    if start is None or end is None:
        return None
    def stamp(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    owners = set()
    for row in load_reviews(path).values():
        if not _valid(row, projects) or ref not in row.get('source_refs', [row.get('ref')]):
            continue
        for piece in row.get('segments') or [row]:
            try:
                if stamp(piece['start']) == start and stamp(piece['end']) == end:
                    owners.add(piece['project_id'])
            except (ValueError, TypeError, KeyError):
                continue
    return next(iter(owners)) if len(owners) == 1 else None
