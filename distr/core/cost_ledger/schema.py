"""SQLite schema for the durable cost ledger."""

from __future__ import annotations

from sqlalchemy import text

from distr.core.db import engine

_ENSURED = False


def ensure_tables(bind=None) -> None:
    """Create cost_ledger tables if missing. Idempotent."""
    global _ENSURED
    target = bind if bind is not None else engine
    with target.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS cost_ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    ticket_id INTEGER,
                    project_id INTEGER,
                    client_key VARCHAR,
                    worker_id VARCHAR,
                    provider VARCHAR,
                    model VARCHAR,
                    started_at FLOAT,
                    ended_at FLOAT,
                    recorded_at FLOAT NOT NULL,
                    duration_seconds FLOAT,
                    tokens_in INTEGER,
                    tokens_out INTEGER,
                    tokens_total INTEGER,
                    provider_cost_usd FLOAT NOT NULL DEFAULT 0,
                    resource_cost_usd FLOAT NOT NULL DEFAULT 0,
                    resource_notes TEXT,
                    source VARCHAR NOT NULL,
                    deliverable_ref TEXT,
                    step_id INTEGER,
                    metadata_json TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cost_ledger_entries_run "
                "ON cost_ledger_entries(run_id, recorded_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cost_ledger_entries_project "
                "ON cost_ledger_entries(project_id, recorded_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cost_ledger_entries_client "
                "ON cost_ledger_entries(client_key, recorded_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cost_ledger_entries_recorded "
                "ON cost_ledger_entries(recorded_at)"
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS cost_ledger_deliverables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deliverable_id VARCHAR,
                    deliverable_path TEXT,
                    run_id INTEGER,
                    ledger_entry_id INTEGER,
                    created_at FLOAT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cost_ledger_deliverables_run "
                "ON cost_ledger_deliverables(run_id)"
            )
        )
    if bind is None:
        _ENSURED = True
