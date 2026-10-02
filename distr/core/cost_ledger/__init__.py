"""Durable cost ledger for harness / workflow / chat / execution runs.

Recording is gated by ``cost_ledger_enabled`` (default True). When disabled,
``record_usage`` / ``link_deliverable`` / ``finalize_run`` are no-ops.
"""

from distr.core.cost_ledger.flags import (
    DISPLAY_KEY,
    FLAG_KEY,
    get_invoice_display,
    is_cost_ledger_enabled,
)
from distr.core.cost_ledger.schema import ensure_tables
from distr.core.cost_ledger.service import (
    cost_rollups,
    derive_client_key,
    finalize_run,
    link_deliverable,
    list_entries,
    record_usage,
)

__all__ = [
    "FLAG_KEY",
    "DISPLAY_KEY",
    "is_cost_ledger_enabled",
    "get_invoice_display",
    "ensure_tables",
    "record_usage",
    "link_deliverable",
    "finalize_run",
    "list_entries",
    "cost_rollups",
    "derive_client_key",
]
