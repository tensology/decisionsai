"""Live Tensology report entries and explicitly saved report drafts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

from distr.core.reports.calendar_review import load_review, review_path, stamp
from distr.core.tensology_client import TensologyApiError, configured_tensology_client

_WRITE_LOCK = Lock()
_FIELDS = ("title", "start_at", "end_at", "project_id")
_DRAFT_PREFIX = "report-draft:"


class CalendarEditError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def load_drafts():
    path = review_path().with_name("time_drafts.json")
    return json.loads(path.read_text()) if path.exists() else {"days": [], "entries": []}


def _receipt_path(draft_id):
    digest = hashlib.sha256(draft_id.encode()).hexdigest()
    return review_path().parent / "calendar_write_receipts" / f"draft-{digest}.json"


def _write_receipt(path: Path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _instant(value):
    return stamp(value).replace(tzinfo=UTC).isoformat()


def _events(client, start, end):
    rows = client.get("time-entries", query={"start": _instant(start.isoformat()), "end": _instant(end.isoformat())})
    if not isinstance(rows, list):
        raise TensologyApiError("Tensology returned an invalid calendar.")
    if len(rows) >= 500:
        if end - start <= timedelta(hours=1):
            raise TensologyApiError("Tensology returned too many overlapping time entries for this period.")
        middle = start + (end - start) / 2
        return list({event["id"]: event for event in _events(client, start, middle) + _events(client, middle, end)}.values())
    return rows


def _projects(client):
    projects = client.get("projects")
    if not isinstance(projects, list):
        raise TensologyApiError("Tensology returned an invalid project list.")
    return {int(project["id"]): project for project in projects}


def _client():
    try:
        return configured_tensology_client(source="decisions-reports")
    except TensologyApiError as exc:
        if exc.code != "not_configured":
            raise
        return None


def edit_version(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()


def edit_lock(event, review):
    key = str(event["id"])
    if key in review.get("invoiced_events", {}) or review.get("evidence", {}).get(key, {}).get("allocation_estimate"):
        return "This entry has an approved billing allocation."
    if event.get("is_timer_running"):
        return "Stop the running timer in Tensology before editing."
    return None


def project_report(snapshot, period):
    from distr.core.reports.calendar_review import apply_review

    review = load_review() or {}
    drafts = load_drafts()
    client = _client()
    if client is None:
        return apply_review(snapshot, data=review, drafts=drafts)
    projects = _projects(client)
    events = _events(client, *period.bounds_utc())
    project_map = dict(review.get("project_map", {}))
    local_names = {project["id"]: project["name"] for project in snapshot.get("projects", [])}
    for remote, project in projects.items():
        local = project.get("decisions_project_id")
        if local in local_names and str(local) not in project_map and remote not in project_map.values():
            project_map[str(local)] = remote
    for remote in set(projects) | {event["project_id"] for event in events if event["project_id"] is not None}:
        if remote not in project_map.values():
            project_map[str(1_000_000 + remote)] = remote
    projected = {
        **review, "calendar": events, "project_map": project_map, "scope": "synchronized_calendar",
        "period": snapshot["period"], "reviewed_through": datetime.now(UTC).isoformat(),
    }
    return apply_review(snapshot, data=projected, drafts=drafts, live_projects=projects)


def unsaved_drafts(drafts, events):
    saved = {event.get("ticket_key") for event in events if event.get("ticket_source") == "decisionsai"}
    return [
        draft for draft in drafts.get("entries", [])
        if _DRAFT_PREFIX + draft["id"] not in saved
        and not (_receipt_path(draft["id"]).exists() and json.loads(_receipt_path(draft["id"]).read_text()).get("event_id"))
    ]


def save_entry(*, entry_id, draft_id, version, original_start, original_end, title, start_at, end_at, project_id):
    if (entry_id is None) == (draft_id is None):
        raise CalendarEditError("Select one time entry.")
    if not title.strip() or len(title.strip()) > 255:
        raise CalendarEditError("Enter a description of up to 255 characters.")
    if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
        raise CalendarEditError("Enter a start and a later end time.")
    client = _client()
    if client is None:
        raise CalendarEditError("Connect Tensology in Settings to save this entry.", 409)
    with _WRITE_LOCK:
        projects = _projects(client)
        if project_id is not None and project_id not in projects:
            raise CalendarEditError("Choose an available Tensology project.", 409)
        body = {"title": title.strip(), "start_at": start_at.isoformat(), "end_at": end_at.isoformat(), "project_id": project_id}
        if draft_id is not None:
            draft = next((item for item in load_drafts()["entries"] if item["id"] == draft_id), None)
            if draft is None:
                raise CalendarEditError("This draft is no longer available.", 404)
            path = _receipt_path(draft_id)
            receipt = json.loads(path.read_text()) if path.exists() else {}
            if receipt.get("event_id"):
                return {"id": receipt["event_id"]}
            body.update(billable=False, ticket_source="decisionsai", ticket_key=_DRAFT_PREFIX + draft_id)
            if receipt and receipt["body"] != body:
                raise CalendarEditError("A previous save is unconfirmed. Retry with its original values or check the Tensology calendar.", 409)
            _write_receipt(path, {"body": body})
            try:
                response = client.post("time-entries", body, idempotency_key=_DRAFT_PREFIX + draft_id)
            except TensologyApiError as exc:
                # A definite rejection of the first attempt leaves the draft editable.
                if not receipt and exc.status in {400, 401, 403, 404, 422}:
                    path.unlink(missing_ok=True)
                raise
            _write_receipt(path, {"event_id": response["id"]})
            return {"id": response["id"]}
        if not version or original_start is None or original_end is None or original_end <= original_start:
            raise CalendarEditError("Reload Reports before editing this entry.", 409)
        rows = _events(client, stamp(original_start.isoformat()), stamp(original_end.isoformat()))
        current = next((event for event in rows if event["id"] == entry_id), None)
        if current is None or edit_version(current) != version:
            raise CalendarEditError("This entry changed in Tensology. Reload Reports before editing it.", 409)
        locked = edit_lock(current, load_review() or {})
        if locked:
            raise CalendarEditError(locked, 403)
        changes = {key: body[key] for key in _FIELDS if body[key] != current[key]}
        for key in ("start_at", "end_at"):
            if stamp(body[key]) == stamp(current[key]):
                changes.pop(key, None)
        if changes:
            if "project_id" in changes and not current["billable"]:
                changes["billable"] = False
            request_id = uuid4().hex
            _write_receipt(review_path().parent / "calendar_write_receipts" / f"edit-{entry_id}-{request_id}.json", current)
            current = client.patch(f"time-entries/{entry_id}", changes, idempotency_key=request_id)
        return {"id": current["id"]}
