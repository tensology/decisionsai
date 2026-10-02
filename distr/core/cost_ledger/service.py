"""Durable cost ledger: record, link deliverables, list, roll up."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from distr.core.cost_ledger.flags import get_invoice_display, is_cost_ledger_enabled, normalize_display
from distr.core.cost_ledger.pricing import (
    blended_cost_usd,
    estimate_local_resource_cost_usd,
    estimate_provider_cost_usd,
    is_local_provider,
    normalize_provider,
    pricing_snapshot,
)
from distr.core.cost_ledger.schema import ensure_tables
from distr.core.db import engine

logger = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
VALID_SOURCES = frozenset({"harness", "workflow", "chat", "execution"})


def _now() -> float:
    return time.time()


def _slugify(value: str | None) -> str:
    text_value = str(value or "").strip().lower()
    text_value = re.sub(r"[^a-z0-9]+", "-", text_value)
    return text_value.strip("-")[:80]


def derive_client_key(*, project_id: int | None = None, project_name: str | None = None) -> str:
    """Derive client_key from project name slug. Empty when unknown.

    Projects have no client_id column today — do not invent a Client ORM.
    """
    name = project_name
    if not name and project_id is not None:
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project

            with get_session() as db:
                row = db.query(Project.name).filter(Project.id == int(project_id)).first()
                name = row[0] if row else None
        except Exception:
            logger.debug("derive_client_key project lookup failed", exc_info=True)
            name = None
    return _slugify(name) if name else ""


def _sast_date_label(epoch: float | None) -> str:
    if epoch is None:
        return ""
    dt = datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(SAST)
    return dt.strftime("%Y-%m-%d")


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    provider_cost = float(data.get("provider_cost_usd") or 0.0)
    resource_cost = float(data.get("resource_cost_usd") or 0.0)
    data["blended_cost_usd"] = blended_cost_usd(provider_cost, resource_cost)
    data["day_sast"] = _sast_date_label(data.get("recorded_at"))
    data["day_utc"] = (
        datetime.fromtimestamp(float(data["recorded_at"]), tz=timezone.utc).strftime("%Y-%m-%d")
        if data.get("recorded_at") is not None
        else ""
    )
    return data


def record_usage(
    *,
    run_id: int | None = None,
    ticket_id: int | None = None,
    project_id: int | None = None,
    client_key: str | None = None,
    worker_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    started_at: float | None = None,
    ended_at: float | None = None,
    duration_seconds: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_total: int | None = None,
    source: str = "workflow",
    deliverable_ref: str | None = None,
    step_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    bind=None,
) -> Optional[dict[str, Any]]:
    """Insert one ledger row. Returns the row dict, or None when disabled / no-op."""
    if not is_cost_ledger_enabled(settings):
        return None
    ensure_tables(bind=bind)
    target = bind if bind is not None else engine

    src = str(source or "workflow").strip().lower()
    if src not in VALID_SOURCES:
        src = "workflow"

    tin = int(tokens_in) if tokens_in is not None else None
    tout = int(tokens_out) if tokens_out is not None else None
    ttotal = int(tokens_total) if tokens_total is not None else None
    if ttotal is None and (tin is not None or tout is not None):
        ttotal = int(tin or 0) + int(tout or 0)
    if tin is None and tout is None and ttotal is not None:
        # Keep tokens_total; leave in/out null so callers see the single number.
        pass

    # Leave ended_at None when the caller omits it so finalize_run can stamp later.
    ended = ended_at
    started = started_at
    duration = duration_seconds
    if duration is None and started is not None and ended is not None:
        duration = max(0.0, float(ended) - float(started))

    prov = normalize_provider(provider)
    provider_cost = estimate_provider_cost_usd(
        provider=prov,
        tokens_in=tin,
        tokens_out=tout,
        tokens_total=ttotal,
    )
    resource_cost = 0.0
    resource_notes = ""
    if is_local_provider(prov):
        resource_cost, resource_notes = estimate_local_resource_cost_usd(
            duration_seconds=duration,
            model=model,
        )
        provider_cost = 0.0

    key = (client_key if client_key is not None else None) or ""
    if not key and project_id is not None:
        key = derive_client_key(project_id=project_id)

    recorded = _now()
    meta_json = json.dumps(metadata, default=str) if metadata else None

    with target.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO cost_ledger_entries(
                    run_id, ticket_id, project_id, client_key,
                    worker_id, provider, model,
                    started_at, ended_at, recorded_at, duration_seconds,
                    tokens_in, tokens_out, tokens_total,
                    provider_cost_usd, resource_cost_usd, resource_notes,
                    source, deliverable_ref, step_id, metadata_json
                ) VALUES (
                    :run_id, :ticket_id, :project_id, :client_key,
                    :worker_id, :provider, :model,
                    :started_at, :ended_at, :recorded_at, :duration_seconds,
                    :tokens_in, :tokens_out, :tokens_total,
                    :provider_cost_usd, :resource_cost_usd, :resource_notes,
                    :source, :deliverable_ref, :step_id, :metadata_json
                )
                """
            ),
            {
                "run_id": int(run_id) if run_id is not None else None,
                "ticket_id": int(ticket_id) if ticket_id is not None else None,
                "project_id": int(project_id) if project_id is not None else None,
                "client_key": key or None,
                "worker_id": str(worker_id) if worker_id else None,
                "provider": prov or None,
                "model": str(model) if model else None,
                "started_at": float(started) if started is not None else None,
                "ended_at": float(ended) if ended is not None else None,
                "recorded_at": recorded,
                "duration_seconds": float(duration) if duration is not None else None,
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens_total": ttotal,
                "provider_cost_usd": provider_cost,
                "resource_cost_usd": resource_cost,
                "resource_notes": resource_notes or None,
                "source": src,
                "deliverable_ref": str(deliverable_ref) if deliverable_ref else None,
                "step_id": int(step_id) if step_id is not None else None,
                "metadata_json": meta_json,
            },
        )
        entry_id = int(result.lastrowid)
        row = conn.execute(
            text("SELECT * FROM cost_ledger_entries WHERE id = :id"),
            {"id": entry_id},
        ).mappings().one()
    return _row_to_dict(row)


def link_deliverable(
    run_id: int,
    ref: str,
    *,
    ledger_entry_id: int | None = None,
    deliverable_id: str | None = None,
    settings: dict[str, Any] | None = None,
    bind=None,
) -> Optional[dict[str, Any]]:
    """Link a deliverable path/id to a run (and optionally a ledger entry).

    Also stamps deliverable_ref on the latest matching entry when entry id omitted.
    """
    if not is_cost_ledger_enabled(settings):
        return None
    ensure_tables(bind=bind)
    target = bind if bind is not None else engine
    path = str(ref or "").strip()
    if not path:
        return None
    did = str(deliverable_id or path)
    now = _now()
    entry_id = ledger_entry_id

    with target.begin() as conn:
        if entry_id is None:
            found = conn.execute(
                text(
                    "SELECT id FROM cost_ledger_entries WHERE run_id = :run_id "
                    "ORDER BY recorded_at DESC, id DESC LIMIT 1"
                ),
                {"run_id": int(run_id)},
            ).first()
            if found:
                entry_id = int(found[0])
                conn.execute(
                    text(
                        "UPDATE cost_ledger_entries SET deliverable_ref = :ref "
                        "WHERE id = :id AND (deliverable_ref IS NULL OR deliverable_ref = '')"
                    ),
                    {"ref": path, "id": entry_id},
                )
        result = conn.execute(
            text(
                """
                INSERT INTO cost_ledger_deliverables(
                    deliverable_id, deliverable_path, run_id, ledger_entry_id, created_at
                ) VALUES (
                    :deliverable_id, :deliverable_path, :run_id, :ledger_entry_id, :created_at
                )
                """
            ),
            {
                "deliverable_id": did,
                "deliverable_path": path,
                "run_id": int(run_id),
                "ledger_entry_id": int(entry_id) if entry_id is not None else None,
                "created_at": now,
            },
        )
        link_id = int(result.lastrowid)
        row = conn.execute(
            text("SELECT * FROM cost_ledger_deliverables WHERE id = :id"),
            {"id": link_id},
        ).mappings().one()
    return dict(row)


def finalize_run(
    run_id: int,
    *,
    ended_at: float | None = None,
    settings: dict[str, Any] | None = None,
    bind=None,
) -> int:
    """Stamp ended_at / duration on open entries for a run. Returns rows updated."""
    if not is_cost_ledger_enabled(settings):
        return 0
    ensure_tables(bind=bind)
    target = bind if bind is not None else engine
    end = float(ended_at if ended_at is not None else _now())
    with target.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE cost_ledger_entries
                SET ended_at = COALESCE(ended_at, :ended_at),
                    duration_seconds = CASE
                        WHEN duration_seconds IS NOT NULL AND duration_seconds > 0 THEN duration_seconds
                        WHEN started_at IS NOT NULL THEN MAX(0, :ended_at - started_at)
                        ELSE duration_seconds
                    END
                WHERE run_id = :run_id
                  AND (ended_at IS NULL OR duration_seconds IS NULL OR duration_seconds = 0)
                """
            ),
            {"run_id": int(run_id), "ended_at": end},
        )
        return int(result.rowcount or 0)


def list_entries(
    *,
    limit: int = 100,
    run_id: int | None = None,
    project_id: int | None = None,
    client_key: str | None = None,
    source: str | None = None,
    display: str | None = None,
    settings: dict[str, Any] | None = None,
    bind=None,
) -> dict[str, Any]:
    """List recent ledger entries with display mode applied for the response shape."""
    ensure_tables(bind=bind)
    target = bind if bind is not None else engine
    limit = max(1, min(int(limit), 500))
    mode = normalize_display(display) if display else get_invoice_display(settings)

    clauses = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if run_id is not None:
        clauses.append("run_id = :run_id")
        params["run_id"] = int(run_id)
    if project_id is not None:
        clauses.append("project_id = :project_id")
        params["project_id"] = int(project_id)
    if client_key:
        clauses.append("client_key = :client_key")
        params["client_key"] = str(client_key)
    if source:
        clauses.append("source = :source")
        params["source"] = str(source).strip().lower()

    sql = (
        "SELECT * FROM cost_ledger_entries WHERE "
        + " AND ".join(clauses)
        + " ORDER BY recorded_at DESC, id DESC LIMIT :limit"
    )
    with target.connect() as conn:
        rows = [_row_to_dict(r) for r in conn.execute(text(sql), params).mappings().all()]

    items = [_present_entry(row, mode) for row in rows]
    return {
        "items": items,
        "limit": limit,
        "display": mode,
        "timezone_label": "Africa/Johannesburg (SAST)",
        "description": (
            "Durable per-step cost ledger estimates. "
            "blended = single cost figure; explicit = token + provider + local resource lines."
        ),
        "pricing": pricing_snapshot(),
    }


def _present_entry(row: dict[str, Any], mode: str) -> dict[str, Any]:
    base = {
        "id": row.get("id"),
        "run_id": row.get("run_id"),
        "ticket_id": row.get("ticket_id"),
        "project_id": row.get("project_id"),
        "client_key": row.get("client_key") or "",
        "worker_id": row.get("worker_id") or "",
        "provider": row.get("provider") or "",
        "model": row.get("model") or "",
        "source": row.get("source") or "",
        "step_id": row.get("step_id"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "recorded_at": row.get("recorded_at"),
        "duration_seconds": row.get("duration_seconds"),
        "deliverable_ref": row.get("deliverable_ref") or "",
        "day_sast": row.get("day_sast") or "",
        "day_utc": row.get("day_utc") or "",
    }
    blended = float(row.get("blended_cost_usd") or 0.0)
    if mode == "explicit":
        base.update(
            {
                "tokens_in": row.get("tokens_in"),
                "tokens_out": row.get("tokens_out"),
                "tokens_total": row.get("tokens_total"),
                "provider_cost_usd": float(row.get("provider_cost_usd") or 0.0),
                "resource_cost_usd": float(row.get("resource_cost_usd") or 0.0),
                "resource_notes": row.get("resource_notes") or "",
                "cost_usd": blended,
            }
        )
    else:
        # Blended: one cost figure; token costs baked in — no separate token line.
        base["cost_usd"] = blended
    return base


def cost_rollups(
    *,
    display: str | None = None,
    settings: dict[str, Any] | None = None,
    bind=None,
) -> dict[str, Any]:
    """Roll up costs by project_id, client_key, and SAST day."""
    ensure_tables(bind=bind)
    target = bind if bind is not None else engine
    mode = normalize_display(display) if display else get_invoice_display(settings)

    with target.connect() as conn:
        rows = [
            _row_to_dict(r)
            for r in conn.execute(
                text("SELECT * FROM cost_ledger_entries ORDER BY recorded_at ASC, id ASC")
            ).mappings().all()
        ]

    by_project: dict[str, dict[str, Any]] = {}
    by_client: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}

    def _acc(bucket: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
        slot = bucket.setdefault(
            key,
            {
                "key": key,
                "entries": 0,
                "tokens_total": 0,
                "provider_cost_usd": 0.0,
                "resource_cost_usd": 0.0,
                "cost_usd": 0.0,
            },
        )
        slot["entries"] += 1
        slot["tokens_total"] += int(row.get("tokens_total") or 0)
        slot["provider_cost_usd"] = round(
            float(slot["provider_cost_usd"]) + float(row.get("provider_cost_usd") or 0.0), 8
        )
        slot["resource_cost_usd"] = round(
            float(slot["resource_cost_usd"]) + float(row.get("resource_cost_usd") or 0.0), 8
        )
        slot["cost_usd"] = round(
            float(slot["cost_usd"]) + float(row.get("blended_cost_usd") or 0.0), 8
        )

    for row in rows:
        pid = row.get("project_id")
        _acc(by_project, str(pid) if pid is not None else "none", row)
        _acc(by_client, str(row.get("client_key") or "none"), row)
        _acc(by_day, str(row.get("day_sast") or "unknown"), row)

    def _finish(bucket: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        items = list(bucket.values())
        if mode == "blended":
            for item in items:
                item.pop("provider_cost_usd", None)
                item.pop("resource_cost_usd", None)
                item.pop("tokens_total", None)
        items.sort(key=lambda x: x["key"])
        return items

    return {
        "display": mode,
        "timezone_label": "Africa/Johannesburg (SAST)",
        "by_project": _finish(by_project),
        "by_client": _finish(by_client),
        "by_day": _finish(by_day),
        "entry_count": len(rows),
        "total_cost_usd": round(sum(float(r.get("blended_cost_usd") or 0.0) for r in rows), 8),
    }
