"""Readiness checks for delegated Hermes workflow execution."""

from __future__ import annotations

from typing import Any

from .models import DelegatedPlan


def preflight_delegated_plan(
    plan: DelegatedPlan,
    runner: Any,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(context or {})
    checks: list[dict[str, Any]] = []

    if plan.kind == "email_document_scope":
        checks.append(_email_check(getattr(runner, "email_adapter", None)))
        checks.append(_method_check("document_extractor", getattr(runner, "document_adapter", None), ["extract"]))
        if plan.target_backend:
            checks.append(_project_handoff_check(getattr(runner, "project_dispatcher", None), plan.target_backend, context))
    elif plan.kind == "desktop_sequence":
        checks.append(
            _method_check(
                "desktop",
                getattr(runner, "desktop_adapter", None),
                ["capture_source_content", "set_clipboard", "create_or_open_file", "write_text", "verify_result"],
            )
        )
    elif plan.kind == "browser_workflow":
        checks.append(_method_check("browser", getattr(runner, "browser_adapter", None), ["execute"]))
    elif plan.kind == "project_handoff":
        checks.append(_project_handoff_check(getattr(runner, "project_dispatcher", None), plan.target_backend or "codex", context))
    else:
        checks.append({
            "name": "delegated_plan",
            "ready": False,
            "detail": f"Plan kind '{plan.kind}' has no executable preflight route.",
            "evidence": {"kind": plan.kind},
        })

    blockers = [check for check in checks if not check.get("ready")]
    return {
        "ready": not blockers,
        "plan_kind": plan.kind,
        "checks": checks,
        "blockers": blockers,
        "context": _safe_context(context),
    }


def format_preflight_for_telegram(report: dict[str, Any]) -> str:
    status = "ready" if report.get("ready") else "not ready"
    lines = [f"Delegated preflight {status}: {report.get('plan_kind') or 'unknown'}"]
    blockers = report.get("blockers") or []
    if blockers:
        lines.append("Blockers:")
        for index, blocker in enumerate(blockers, start=1):
            lines.append(f"{index}. {blocker.get('name')}: {blocker.get('detail')}")
    else:
        lines.append("All required adapters are available for this route.")
    return "\n".join(lines)


def _email_check(adapter: Any) -> dict[str, Any]:
    base = _method_check("email", adapter, ["search_latest_email", "download_attachments"])
    if not base["ready"]:
        return base
    connected = bool(getattr(adapter, "connected", True))
    if not connected:
        return {
            "name": "email",
            "ready": False,
            "detail": "The email adapter is present but Gmail/Google Workspace is not connected.",
            "evidence": {"connected": False},
        }
    return {
        "name": "email",
        "ready": True,
        "detail": "Email search and attachment download are available.",
        "evidence": {"connected": connected},
    }


def _project_handoff_check(dispatcher: Any, backend_id: str, context: dict[str, Any]) -> dict[str, Any]:
    if dispatcher is None or not (hasattr(dispatcher, "dispatch") or hasattr(dispatcher, "check_backend_status")):
        return {
            "name": "project_handoff",
            "ready": False,
            "detail": "No project handoff dispatcher is configured.",
            "evidence": {"backend_id": backend_id},
        }
    if not context.get("project_id"):
        return {
            "name": "project_handoff",
            "ready": False,
            "detail": "A project_id is required before Codex/Cursor handoff can run.",
            "evidence": {"backend_id": backend_id},
        }
    if hasattr(dispatcher, "check_backend_status"):
        status = dispatcher.check_backend_status(backend_id)
        ready = bool(status.get("ready") and status.get("can_receive_remote_handoff", True))
        return {
            "name": "project_handoff",
            "ready": ready,
            "detail": status.get("message") or ("Backend is ready." if ready else "Backend is not ready."),
            "evidence": status,
        }
    return {
        "name": "project_handoff",
        "ready": True,
        "detail": "Project handoff dispatcher is configured.",
        "evidence": {"backend_id": backend_id},
    }


def _method_check(name: str, adapter: Any, methods: list[str]) -> dict[str, Any]:
    if adapter is None:
        return {
            "name": name,
            "ready": False,
            "detail": f"No {name.replace('_', ' ')} adapter is configured.",
            "evidence": {"missing": methods},
        }
    if hasattr(adapter, "check_readiness"):
        result = adapter.check_readiness()
        return {
            "name": name,
            "ready": bool(result.get("ready")),
            "detail": result.get("detail") or result.get("message") or "",
            "evidence": result,
        }
    missing = [method for method in methods if not hasattr(adapter, method)]
    return {
        "name": name,
        "ready": not missing,
        "detail": (
            f"{name.replace('_', ' ').title()} adapter is available."
            if not missing
            else f"{name.replace('_', ' ').title()} adapter is missing methods: {', '.join(missing)}."
        ),
        "evidence": {"missing": missing},
    }


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: context.get(key)
        for key in ("workflow_id", "run_id", "step_id", "ticket_id", "board_id", "project_id", "source_surface")
        if context.get(key) is not None
    }
