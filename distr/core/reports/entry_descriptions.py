"""Evidence-backed client descriptions over recorded time intervals. Source rows stay intact."""
from datetime import datetime, UTC, timedelta
import hashlib
import json
from pathlib import Path


def description_path():
    from distr.core.paths import DB_DIR
    return Path(DB_DIR) / 'reports' / 'entry_descriptions.json'


def entry_key(block):
    if block.get('description_key'):
        return block['description_key']
    def stamp(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.isoformat(timespec='microseconds')
    start = block.get('source_start') or block.get('job_start') or block['start']
    end = block.get('source_end') or block.get('job_end') or block['end']
    raw = json.dumps([block['source'], block['ref'], stamp(start), stamp(end)], separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def load_descriptions(path=None):
    try:
        payload = json.loads(Path(path or description_path()).read_text())
        return payload.get('entries', {}) if payload.get('schema') == 1 else {}
    except (OSError, ValueError, AttributeError):
        return {}


def apply_descriptions(blocks, *, path=None):
    records = load_descriptions(path)
    starts = [row['start'][:10] for row in records.values() if isinstance(row, dict) and isinstance(row.get('start'), str)]
    review_from = min(starts) if starts else None
    recent = {}
    for row in records.values():
        if not isinstance(row, dict) or not all(row.get(k) for k in ('source', 'ref', 'start', 'end')):
            continue
        key = (row['source'], row['ref'], row['start'])
        if key not in recent or row['end'] > recent[key]['end']:
            recent[key] = row
    for block in blocks:
        if block.get('invoice_status'):
            continue
        record = records.get(entry_key(block))
        if record is None:
            start = block.get('job_start') or block['start']
            end = block.get('job_end') or block['end']
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace('Z', '+00:00')).replace(tzinfo=None)
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace('Z', '+00:00')).replace(tzinfo=None)
            source_start = (start+timedelta(hours=2)).replace(microsecond=0).isoformat()
            source_end = (end+timedelta(hours=2)).replace(microsecond=0).isoformat()
            previous = recent.get((block['source'], block['ref'], source_start))
            if previous and previous['end'] < source_end:
                record = {**previous, 'status':'needs_review'}
                block['description_asof'] = previous['end']
        if not isinstance(record, dict) or not isinstance(record.get('description'), str):
            start = block['start']
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace('Z', '+00:00'))
            if review_from and (start + timedelta(hours=2)).date().isoformat() >= review_from:
                block['recorded_title'] = block['title']
                # Review state is metadata, not a replacement for recorded work.
                # A historical review must not erase descriptions on later days.
                block['title'] = block['title'] or 'Work description unavailable.'
                block['description_status'] = 'needs_review'
            continue
        description = ' '.join(record['description'].split())
        if not 12 <= len(description) <= 400 or record.get('status') not in {'ready', 'needs_review'}:
            continue
        block['recorded_title'] = block['title']
        block['title'] = description
        block['description_status'] = record['status']
        block['description_evidence_hash'] = record.get('evidence_hash')
        if not block.get('project_id'):
            block['description_status'] = 'needs_review'
        companions = [records.get(key) for key in block.get('related_description_keys', [])]
        if any(isinstance(row, dict) and row.get('status') == 'needs_review' for row in companions):
            block['description_status'] = 'needs_review'
            if not block['title'].lower().startswith('review required:'):
                block['title'] = 'Review required: ' + block['title']
    return blocks
