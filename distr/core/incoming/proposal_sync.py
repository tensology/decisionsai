"""Versioned Incoming exchange with Tensology, keyed by exact source snapshots."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()
_LAST_RESULT: dict[str, Any] = {"state": "idle", "uploaded": 0, "updated": 0, "conflicts": []}
_WAITING_FOR_TRIAGE: set[str] = set()


def wait_for_triage(request_id):
    with _LOCK:
        _WAITING_FOR_TRIAGE.add(str(request_id or ''))


def finish_triage(rows):
    """Complete checks only after their newly generated local proposals are ready."""
    with _LOCK:
        waiting = list(_WAITING_FOR_TRIAGE)
        _WAITING_FOR_TRIAGE.clear()
    if not waiting:
        return
    try:
        remote = fetch_remote_proposals()
        finished = False
        for request_id in waiting:
            _, result = reconcile(rows, remote, request_id=request_id or None)
            finished = finished or result.get('state') != 'requested'
    except Exception:
        # Keep an observable pending request when the canonical store is offline;
        # a later Check incoming retries safely without a false completion.
        return
    if finished:
        from distr.core.incoming.proposals import invalidate_proposals_list_cache
        from distr.core.incoming.events import publish_incoming_change
        invalidate_proposals_list_cache()
        publish_incoming_change(status='proposal_sync')


def source_identity(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    thread = str(row.get("source_thread_id") or (row.get("source_conversation") or {}).get("thread_id") or "")
    outbound = {str(item.get('id')) for item in row.get('evidence') or [] if isinstance(item, dict) and item.get('id') and item.get('direction') == 'outbound'}
    ids = sorted({str(value) for value in row.get("message_ids", row.get("source_message_ids", [])) if value is not None and str(value)} - outbound)
    if not source or not thread or not ids:
        raise ValueError("An exact source snapshot is required before syncing this proposal.")
    return hashlib.sha256(json.dumps([source, thread, ids], separators=(",", ":")).encode()).hexdigest()


def _with_source_context(row):
    from distr.core.incoming import proposals as incoming
    outbound = {str(item.get('id')) for item in row.get('evidence') or [] if isinstance(item, dict) and item.get('id') and item.get('direction') == 'outbound'}
    return {**row, 'local_snapshot_key': row.get('local_snapshot_key') or incoming._proposal_handoff_key(row),
            'message_ids': [str(value) for value in row.get('message_ids') or [] if str(value) not in outbound],
            'source_context_message_ids': sorted(set(map(str, row.get('source_context_message_ids') or [])) | outbound)}


def _instant(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def _mapped_projects(client) -> dict[int, int]:
    import os
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    def path(value):
        return os.path.normpath(str(value).strip()) if str(value or '').strip() else None
    with get_session() as db:
        paths = {}
        for local in db.query(Project).filter(Project.deleted_at.is_(None)).all():
            if key := path(local.folder_location):
                paths.setdefault(key, []).append(local.id)
    result = {}
    for project in client.get("projects") or []:
        if isinstance(project, dict) and type(project.get("decisions_project_id")) is int:
            result[project["decisions_project_id"]] = int(project["id"])
        elif isinstance(project, dict) and (candidates := paths.get(path(project.get('local_folder_path')), [])) and len(candidates) == 1:
            if candidates[0] in result and result[candidates[0]] != int(project['id']):
                raise ValueError('Several Tensology projects match the same local folder. Review their identities.')
            result[candidates[0]] = int(project['id'])
    return result


def fetch_remote_proposals():
    """Unlike the day-plan best-effort reader, reconciliation must expose failures."""
    from distr.core.tensology_client import configured_tensology_client
    client = configured_tensology_client(source='incoming-reconcile')
    rows = {}
    for status in ('pending', 'all'):
        for offset in range(0, 10000, 200):
            response = client.get('work-requests', query={'status': status, 'offset': offset})
            if not isinstance(response, list):
                raise ValueError('Tensology returned an invalid proposal collection.')
            rows.update({str(row['id']): row for row in response if isinstance(row, dict) and row.get('id') is not None})
            if len(response) < 200:
                break
        else:
            raise ValueError('Proposal reconciliation exceeded its 10000-row safety bound. The collection has not been fully checked.')
    return list(rows.values())


def _corrections():
    from distr.core.db import get_session
    from distr.core.db.projects import IncomingProposalProject
    with get_session() as db:
        return {row.key: {"project_id": row.project_id, "updated_at": row.updated_at} for row in db.query(IncomingProposalProject).all()}


def _patch(client, row, changes):
    revision = row.get("updated_at")
    if not revision:
        raise ValueError("Reload this proposal to obtain its saved revision.")
    identity = row.get("source_identity") or source_identity(row)
    body = {**changes, "updated_at": revision, "source_identity": identity}
    suffix = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    return client.patch(f"work-requests/{int(str(row['id']).split(':')[-1])}", body, idempotency_key=f"proposal-edit-{suffix}")


def update_proposal(proposal, changes, *, expected_updated_at):
    """A user save must update the canonical row before the UI reports success."""
    from distr.core.incoming import proposals as incoming
    from distr.core.tensology_client import configured_tensology_client
    client = configured_tensology_client(source="incoming-edit")
    allowed = {"project_id", "title", "description", "tasks", "task_completed_indexes", "priority", "estimated_minutes"}
    if set(changes) - allowed:
        raise ValueError("Only proposal details and tasks can be edited.")
    changes = dict(changes)
    if "estimated_minutes" in changes:
        from decimal import Decimal, InvalidOperation
        try:
            changes["estimated_hours"] = str(Decimal(str(changes.pop("estimated_minutes"))) / 60)
        except (InvalidOperation, TypeError):
            raise ValueError("Estimated minutes must be a number.")
    if "project_id" in changes:
        mapping = _mapped_projects(client)
        project_id = changes["project_id"]
        if type(project_id) is not int or project_id not in mapping:
            raise ValueError("Sync this project to Tensology before assigning its proposal.")
        changes["project_id"] = mapping[project_id]
    if not str(proposal.get("id") or "").startswith("tensology:"):
        canonical = _upload(client, proposal, _mapped_projects(client))
        proposal = incoming._from_tensology(canonical, incoming._project_map())
        expected_updated_at = proposal["updated_at"]
    if not expected_updated_at or expected_updated_at != proposal.get("updated_at"):
        raise ValueError("This proposal changed. Reload it before saving.")
    saved = _patch(client, proposal, changes)
    incoming.invalidate_proposals_list_cache()
    from distr.core.incoming.events import publish_incoming_change
    publish_incoming_change(status="proposal_updated")
    return incoming._from_tensology(saved, incoming._project_map())


def _upload(client, row, mapping):
    from .project_links import proposal_in_scope
    if not proposal_in_scope(row):
        raise ValueError('Link the source conversation to this project before syncing its proposal.')
    row = _with_source_context(row)
    project_id = row.get("project_id")
    if type(project_id) is not int or project_id not in mapping:
        raise ValueError("This proposal's project is not linked to Tensology.")
    tasks = row.get("tasks") or []
    from distr.core.incoming.proposals import _normalize_tasks
    details = _normalize_tasks(tasks)
    titles = [str(task.get('title') or '') if isinstance(task, dict) else task for task in details]
    from decimal import Decimal
    minutes = row.get('estimated_minutes')
    body = {"source": row.get("source"), "source_thread_id": row.get("source_thread_id"),
            "source_message_ids": row.get("message_ids") or [], "source_identity": source_identity(row),
            "source_context_message_ids": row.get('source_context_message_ids') or [str(item['id']) for item in row.get('evidence') or [] if isinstance(item, dict) and item.get('id') and item.get('direction') == 'outbound'],
            "project_id": mapping[project_id], "title": row.get("title"), "description": row.get("description") or "",
            "tasks": titles, "source_task_details": details if any(isinstance(task, dict) for task in details) else [],
            "task_completed_indexes": row.get('task_completed_indexes') or [],
            "priority": row.get("priority") or "medium", "evidence": row.get("evidence") or [],
            "estimated_hours": str(Decimal(str(60 if minutes in (None, '') else minutes)) / 60)}
    from distr.core.incoming.media import list_proposal_attachment_manifest
    body['attachments'] = [{key: item.get(key) for key in ('name', 'mime_type', 'kind', 'source_message_id', 'caption', 'transcript')}
                           for item in list_proposal_attachment_manifest(row)]
    return client.post("work-requests/sync", body, idempotency_key=f"proposal-sync-{body['source_identity']}")


def reconcile(local_rows, remote_rows, *, request_id=None, dry_run=False):
    """Latest saved assignment wins; refreshes and triage reads are never edits."""
    from distr.core.incoming import proposals as incoming
    from distr.core.tensology_client import configured_tensology_client
    global _LAST_RESULT
    result = {"state": "complete", "uploaded": 0, "updated": 0, "conflicts": [], "request_id": request_id, "planned_changes": []}
    canonical = list(remote_rows)
    with _LOCK:
        try:
            client = configured_tensology_client(source="incoming-reconcile")
            mapping = _mapped_projects(client)
            corrections = _corrections()
            by_identity = {}
            for row in canonical:
                try:
                    by_identity.setdefault(source_identity(row), []).append(row)
                except ValueError:
                    continue
            accepted = incoming._accepted_proposal_keys()
            local_rows = incoming._scoped_proposals(incoming._undismissed_proposals([row for row in local_rows if incoming._proposal_handoff_key(row) not in accepted]))
            local_rows = [_with_source_context(row) for row in local_rows]
            for row in local_rows:
                try:
                    identity = source_identity(row)
                    matches = by_identity.get(identity) or []
                    if len(matches) > 1:
                        raise ValueError("Several canonical proposals share this source snapshot.")
                    if not matches:
                        source_key = (row.get('source'), row.get('source_thread_id'))
                        ids = set(map(str, row.get('message_ids') or []))
                        covered = {str(value) for other in canonical
                                   if (other.get('source'), (other.get('source_conversation') or {}).get('thread_id')) == source_key
                                   for value in other.get('source_message_ids') or []}
                        if ids and ids <= covered:
                            result['suppressed_local'] = result.get('suppressed_local', 0) + 1
                            continue
                        if ids.intersection(covered):
                            result['planned_changes'].append({'proposal_id': str(row.get('id') or ''), 'action': 'review_source_window', 'source_identity': identity, 'total_messages': len(ids), 'uncovered_messages': len(ids - covered)})
                            raise ValueError(f'This source window overlaps existing proposals ({len(ids - covered)} of {len(ids)} messages uncovered). Review its exact identity before importing.')
                        if row.get('project_id') not in mapping:
                            raise ValueError("This proposal's project is not linked to Tensology.")
                        if dry_run:
                            result["planned_changes"].append({"proposal_id": str(row.get("id") or ""), "action": "create", "source_identity": identity, "project_id": mapping.get(row.get("project_id"))})
                            continue
                        saved = _upload(client, row, mapping)
                        canonical.append(saved)
                        by_identity[identity] = [saved]
                        result["uploaded"] += bool(saved.get("created"))
                except Exception as exc:
                    result["conflicts"].append({"proposal_id": str(row.get("id") or ""), "reason": str(exc)[:300]})
            # A locally saved correction has its own real edit timestamp. Never use
            # a list/read time as an edit, and never resurrect a decided proposal.
            for index, row in enumerate(canonical):
                if row.get("status") != "pending":
                    continue
                key = f"tensology:{row['id']}"
                if (row.get('project') or {}).get('id') not in mapping.values():
                    result['conflicts'].append({'proposal_id': key, 'reason': 'The project identity is not linked to Tensology. Review its project mapping.'})
                correction = corrections.get(key)
                if not correction:
                    for local in local_rows:
                        try:
                            same = source_identity(local) == source_identity(row)
                        except ValueError:
                            same = False
                        if same:
                            correction = corrections.get(local.get('local_snapshot_key') or incoming._proposal_handoff_key(local))
                            if correction:
                                break
                if not correction:
                    continue
                try:
                    local_project = correction["project_id"]
                    if local_project not in mapping:
                        raise ValueError("Saved project correction has no explicit Tensology mapping.")
                    if mapping[local_project] == (row.get("project") or {}).get("id"):
                        continue
                    edited, upstream = _instant(correction["updated_at"]), _instant(row.get('details_edited_at') or row.get("updated_at") or row.get('created_at'))
                    if not edited or not upstream or edited == upstream:
                        raise ValueError("Project versions need review because edit order is unavailable or tied.")
                    if edited > upstream:
                        if dry_run:
                            result["planned_changes"].append({"proposal_id": key, "action": "reassign", "source_identity": source_identity(row), "old_project_id": (row.get("project") or {}).get("id"), "new_project_id": mapping[local_project], "local_edited_at": edited.isoformat(), "canonical_edited_at": upstream.isoformat()})
                            continue
                        canonical[index] = _patch(client, row, {"project_id": mapping[local_project]})
                        result["updated"] += 1
                    # Older local corrections are ignored at read time. Keep the
                    # original row as an audit trail rather than deleting it.
                except Exception as exc:
                    result["conflicts"].append({"proposal_id": key, "reason": str(exc)[:300]})
            if result["conflicts"]:
                result["state"] = "partial"
            if not dry_run and incoming._TRIAGE_CACHE.get('running'):
                result['state'] = 'requested'
            elif not dry_run:
                client.post("work-requests/reconciliation", result, idempotency_key=f"reconcile-{request_id or hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()[:32]}")
                _WAITING_FOR_TRIAGE.discard(str(request_id or ''))
        except Exception as exc:
            result.update(state="partial", error=str(exc)[:300])
        if not dry_run:
            _LAST_RESULT = result
    return canonical, result


def last_result():
    return dict(_LAST_RESULT)


def _thread_receipts():
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session
    with get_session() as db:
        return [(chat_id, receipt) for chat_id, params in db.query(Chat.id, Chat.params).all()
                if isinstance(receipt := _chat_params(params).get('incoming_proposal_handoff'), dict)]


def reconcile_decisions(remote_rows):
    """Retry durable local decisions, and import decisions missed while offline."""
    from . import proposals as incoming
    from distr.core.tensology_client import configured_tensology_client
    receipts = _thread_receipts()
    rows, errors = [], []
    client = None
    for remote in remote_rows:
        row = dict(remote)
        shaped = incoming._from_tensology(row)
        if not shaped:
            rows.append(row)
            continue
        if row.get('status') != 'pending':
            incoming.record_remote_proposal_decision({**shaped, 'request_id': row['id'], 'status': row.get('status')})
            rows.append(row)
            continue
        thread_id = None
        for chat_id, receipt in receipts:
            match = receipt.get('key') == incoming._proposal_handoff_key(shaped)
            if not match and receipt.get('proposal'):
                try:
                    match = source_identity(receipt['proposal']) == source_identity(row)
                except ValueError:
                    pass
            if match:
                thread_id = chat_id
                break
        action = 'acknowledge_thread' if thread_id else ('dismiss' if not incoming._undismissed_proposals([shaped]) else None)
        if action:
            try:
                client = client or configured_tensology_client(source='incoming-decision-retry')
                body = {'action': action, **({'thread_id': thread_id} if thread_id else {})}
                saved = client.post(f"work-requests/{row['id']}/action", body,
                                    idempotency_key=f"proposal-decision-{row['id']}-{action}-{thread_id or ''}")
                row['status'] = saved['status']
                row['stored_status'] = saved['status']
            except Exception as exc:
                errors.append(f"{row['id']}: {str(exc)[:100]}")
        rows.append(row)
    return rows, errors
