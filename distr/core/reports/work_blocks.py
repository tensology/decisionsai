"""Time-entry blocks as scoped collections of work.

A time entry is the outcome of one scoped collection of work: the same project,
the same unit of work (a CLI session, an agent run, a thread, or a recorded
calendar entry), on one South African day, with no idle gap longer than
``GAP_SECONDS`` inside it. Work from different scopes is never folded into one
block just because it shares a project or a day, and a block is placed at the
time the work was recorded, not reconstructed backwards from its last minute.

The functions here are pure. They read intervals and return blocks; they never
write to Decisions or to Tensology.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
GAP_SECONDS = 20 * 60
MAX_BLOCK_SECONDS = 6 * 60 * 60


def _instant(value) -> datetime:
    """Naive UTC from a datetime or ISO string (Z, offset, or naive UTC)."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def sast_day(value) -> str:
    return _instant(value).replace(tzinfo=UTC).astimezone(SAST).date().isoformat()


def scope_key(interval: dict) -> str:
    """The unit of work an interval belongs to. Unknown scope falls back to its own description."""
    for key in ("thread_ref", "ref"):
        ref = str(interval.get(key) or "").strip()
        if ref:
            return ref
    text = " ".join(str(interval.get("description") or interval.get("title") or "").split())
    return "description:" + hashlib.sha256(text.encode()).hexdigest()[:16] if text else "unscoped"


def _union_seconds(spans) -> int:
    total, current = 0.0, None
    for start, end in sorted(spans):
        if current is None or start > current[1]:
            if current is not None:
                total += (current[1] - current[0]).total_seconds()
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current is not None:
        total += (current[1] - current[0]).total_seconds()
    return int(total)


def scoped_blocks(intervals: list[dict], *, project_id=None, gap_seconds: int = GAP_SECONDS,
                  max_block_seconds: int = MAX_BLOCK_SECONDS) -> list[dict]:
    """Group recorded intervals into coherent blocks.

    Each interval needs ``start`` and ``end``; ``ref``/``thread_ref``, ``description``
    and ``project_id`` are optional. Intervals are split at SAST midnight first.
    """
    pieces = []
    for interval in intervals:
        start, end = _instant(interval["start"]), _instant(interval["end"])
        if end <= start:
            continue
        pid = interval.get("project_id", project_id)
        cursor = start
        while cursor < end:
            local = cursor.replace(tzinfo=UTC).astimezone(SAST)
            midnight = datetime(local.year, local.month, local.day, tzinfo=SAST) + timedelta(days=1)
            stop = min(end, midnight.astimezone(UTC).replace(tzinfo=None))
            pieces.append({"start": cursor, "end": stop, "project_id": pid, "scope": scope_key(interval),
                           "ref": interval.get("ref") or "", "description": interval.get("description") or ""})
            cursor = stop
    groups = defaultdict(list)
    for piece in pieces:
        groups[(piece["project_id"], piece["scope"], sast_day(piece["start"]))].append(piece)
    blocks = []
    for (pid, scope, day), items in groups.items():
        items.sort(key=lambda item: (item["start"], item["end"]))
        current = None
        for item in items:
            joins = (current is not None
                     and (item["start"] - current["end"]).total_seconds() <= gap_seconds
                     and (max(current["end"], item["end"]) - current["start"]).total_seconds() <= max_block_seconds)
            if joins:
                current["end"] = max(current["end"], item["end"])
                current["spans"].append((item["start"], item["end"]))
                if item["ref"] and item["ref"] not in current["refs"]:
                    current["refs"].append(item["ref"])
                if item["description"] and item["description"] not in current["descriptions"]:
                    current["descriptions"].append(item["description"])
                continue
            current = {"project_id": pid, "scope": scope, "day": day, "start": item["start"], "end": item["end"],
                       "spans": [(item["start"], item["end"])], "refs": [item["ref"]] if item["ref"] else [],
                       "descriptions": [item["description"]] if item["description"] else []}
            blocks.append(current)
    for block in blocks:
        block["active_seconds"] = _union_seconds(block.pop("spans"))
        block["span_seconds"] = int((block["end"] - block["start"]).total_seconds())
    blocks.sort(key=lambda block: (block["start"], str(block["project_id"]), block["scope"]))
    return blocks


def event_integrity(event: dict, proof: dict | None) -> dict:
    """How one saved calendar entry relates to the recorded work behind it.

    ``folded``: the entry combines more than one scoped block of work.
    ``reconstructed``: recorded work falls outside the entry's own start/end, so the
    entry was placed by allocation, not where the work happened.
    """
    intervals = list((proof or {}).get("source_intervals") or [])
    if not intervals:
        return {"state": "no_source_evidence", "scoped_blocks": 0, "source_intervals": 0,
                "folded": False, "reconstructed": False, "recorded_seconds": None}
    blocks = scoped_blocks(intervals, project_id=event.get("project_id"))
    start, end = _instant(event["start_at"]), _instant(event["end_at"])
    outside = any(_instant(i["start"]) < start or _instant(i["end"]) > end for i in intervals)
    folded = len(blocks) > 1
    return {
        "state": "folded" if folded else "reconstructed" if outside else "coherent",
        "scoped_blocks": len(blocks), "source_intervals": len(intervals),
        "folded": folded, "reconstructed": outside,
        "recorded_seconds": _union_seconds([(_instant(i["start"]), _instant(i["end"])) for i in intervals]),
    }


def proposed_split(event: dict, proof: dict | None) -> list[dict]:
    """Scoped replacement blocks for one saved entry, at recorded times. Never written by this module."""
    blocks = scoped_blocks(list((proof or {}).get("source_intervals") or []), project_id=event.get("project_id"))
    return [{
        "project_id": event.get("project_id"), "billable": bool(event.get("billable")),
        "start_at": block["start"].isoformat() + "Z", "end_at": block["end"].isoformat() + "Z",
        "active_seconds": block["active_seconds"], "span_seconds": block["span_seconds"],
        "scope": block["scope"], "refs": block["refs"], "descriptions": block["descriptions"],
        "replaces_event_id": event.get("id"),
    } for block in blocks]
