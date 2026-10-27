"""Measure what a workflow run actually cost, and compare two runs.

The core bet of the workflow rework is that one session per ticket is much
cheaper than one cold CLI dispatch per step. That bet is only checkable with
numbers, so this turns a run into sessions, models, tokens and wall time.

Usage::

    python -m distr.core.workflow.run_metrics <run_id>
    python -m distr.core.workflow.run_metrics <baseline_run_id> <candidate_run_id>
"""

from __future__ import annotations

import sys
from typing import Any


def run_metrics(run_id: int) -> dict[str, Any]:
    """Return the measurable footprint of one workflow run."""
    from sqlalchemy import text

    from distr.core.db import get_session
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.db.workflow import (
        AutoWorkflow,
        AutoWorkflowRun,
        AutoWorkflowStepResult,
    )

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if run is None:
            return {"run_id": int(run_id), "error": "run not found"}
        workflow = (
            db.query(AutoWorkflow).filter(AutoWorkflow.id == int(run.workflow_id)).first()
            if run.workflow_id
            else None
        )
        step_executions = (
            db.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.run_id == int(run_id))
            .count()
        )
        sessions = (
            db.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.run_id == int(run_id))
            .all()
        )
        try:
            ledger = db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(tokens_in), 0) AS ti,
                           COALESCE(SUM(tokens_out), 0) AS tout,
                           COALESCE(SUM(tokens_total), 0) AS ttotal,
                           COUNT(*) AS n
                    FROM cost_ledger_entries WHERE run_id = :rid
                    """
                ),
                {"rid": int(run_id)},
            ).fetchone()
        except Exception:
            ledger = None

        models: dict[str, int] = {}
        for session in sessions:
            key = f"{session.route_backend or '?'}:{session.selected_model or '?'}"
            models[key] = models.get(key, 0) + 1

        started = run.started_at
        completed = run.completed_at
        wall = (completed - started).total_seconds() if started and completed else None

        return {
            "run_id": int(run_id),
            "workflow_id": int(run.workflow_id) if run.workflow_id else None,
            "workflow_name": workflow.name if workflow else "",
            "status": run.status or "",
            "started_at": started.isoformat() if started else None,
            "completed_at": completed.isoformat() if completed else None,
            "wall_seconds": wall,
            "step_executions": int(step_executions),
            "sessions": len(sessions),
            "models": models,
            "tokens_in": int(getattr(ledger, "ti", 0) or 0) if ledger else 0,
            "tokens_out": int(getattr(ledger, "tout", 0) or 0) if ledger else 0,
            "tokens_total": int(getattr(ledger, "ttotal", 0) or 0) if ledger else 0,
            "ledger_entries": int(getattr(ledger, "n", 0) or 0) if ledger else 0,
        }


def compare_runs(baseline_run_id: int, candidate_run_id: int) -> dict[str, Any]:
    """Compare two runs on the numbers that decided this rework."""
    baseline = run_metrics(baseline_run_id)
    candidate = run_metrics(candidate_run_id)

    def _delta(key: str) -> float | None:
        left = baseline.get(key)
        right = candidate.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return right - left
        return None

    return {
        "baseline": baseline,
        "candidate": candidate,
        "delta": {
            "sessions": _delta("sessions"),
            "step_executions": _delta("step_executions"),
            "tokens_total": _delta("tokens_total"),
            "wall_seconds": _delta("wall_seconds"),
        },
    }


def _format_metrics(metrics: dict[str, Any]) -> str:
    if metrics.get("error"):
        return f"run {metrics.get('run_id')}: {metrics['error']}"

    def _hours(seconds: float | None) -> str:
        if seconds is None:
            return "still running"
        return f"{seconds / 3600:.2f}h"

    models = metrics.get("models") or {}
    model_line = ", ".join(f"{name} x{count}" for name, count in sorted(models.items())) or "none"
    return (
        f"run {metrics['run_id']}  {metrics.get('workflow_name') or ''}  [{metrics.get('status')}]\n"
        f"  sessions        : {metrics.get('sessions')}\n"
        f"  step executions : {metrics.get('step_executions')}\n"
        f"  wall time       : {_hours(metrics.get('wall_seconds'))}\n"
        f"  tokens in/out   : {metrics.get('tokens_in')} / {metrics.get('tokens_out')} "
        f"(total {metrics.get('tokens_total')}, {metrics.get('ledger_entries')} ledger rows)\n"
        f"  models          : {model_line}"
    )


def _format_comparison(compare: dict[str, Any]) -> str:
    delta = compare.get("delta") or {}
    return "\n".join(
        [
            "BASELINE",
            _format_metrics(compare["baseline"]),
            "",
            "CANDIDATE",
            _format_metrics(compare["candidate"]),
            "",
            "DELTA (candidate - baseline)",
            f"  sessions        : {delta.get('sessions')}",
            f"  step executions : {delta.get('step_executions')}",
            f"  tokens total    : {delta.get('tokens_total')}",
            f"  wall seconds    : {delta.get('wall_seconds')}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    try:
        if len(args) == 1:
            print(_format_metrics(run_metrics(int(args[0]))))
            return 0
        print(_format_comparison(compare_runs(int(args[0]), int(args[1]))))
        return 0
    except ValueError:
        print("run ids must be integers")
        return 2


if __name__ == "__main__":  # pragma: no cover - manual tool
    raise SystemExit(main())
