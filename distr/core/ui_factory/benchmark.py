"""Ten-case deterministic benchmark for the executable UI template packs."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tempfile
import time

from .compiler import compile_screen_contract


def _criterion(case_id: str) -> list[dict[str, str]]:
    return [{
        "id": f"{case_id}-renders",
        "given": "the screen contract and representative data",
        "when": "the route is opened",
        "then": "the requested screen and primary action are visible",
        "verification": "Playwright asserts the screen root and named primary action are visible",
    }]


def representative_contracts() -> list[dict[str, object]]:
    cases = [
        ("customer-list", "crud-list-detail", "Customers", "Add customer"),
        ("invoice-list", "crud-list-detail", "Invoices", "Create invoice"),
        ("user-admin", "admin-screen", "User administration", "Invite user"),
        ("device-admin", "admin-screen", "Device administration", "Register device"),
        ("product-portal", "customer-portal", "Products", "View basket"),
        ("service-portal", "customer-portal", "Services", "Book service"),
        ("project-dashboard", "dashboard", "Project health", "Export report"),
        ("sales-dashboard", "dashboard", "Sales overview", "Create report"),
        ("checkout-funnel", "funnel", "Checkout", "Continue to payment"),
        ("onboarding-funnel", "funnel", "Set up your account", "Continue"),
    ]
    contracts: list[dict[str, object]] = []
    for screen_id, screen_type, title, action in cases:
        contract: dict[str, object] = {
            "screen_id": screen_id,
            "screen_type": screen_type,
            "title": title,
            "route": f"/{screen_id}",
            "primary_action": action,
            "entity": title.lower(),
            "acceptance_criteria": _criterion(screen_id),
        }
        if screen_type == "crud-list-detail":
            contract.update({"columns": ["name", "status", "updated_at"], "filters": ["status"], "form_mode": "drawer"})
        elif screen_type == "funnel":
            contract.update({"states": ["loading", "error", "populated"], "fields": [{"name": "email", "label": "Email", "type": "email", "required": True}]})
        contracts.append(contract)
    return contracts


def run_template_benchmark(output_dir: str | Path | None = None) -> dict[str, object]:
    started = time.perf_counter()
    owned_temp = tempfile.TemporaryDirectory() if output_dir is None else None
    root = Path(owned_temp.name if owned_temp else output_dir).resolve()
    results = []
    try:
        for payload in representative_contracts():
            case_dir = root / str(payload["screen_id"])
            result = compile_screen_contract(payload, case_dir)
            files = [Path(path) for path in result.files]
            results.append({
                **asdict(result),
                "passed": len(files) == 4 and all(path.is_file() and path.stat().st_size > 0 for path in files),
                "bytes": sum(path.stat().st_size for path in files),
            })
        passed = sum(1 for item in results if item["passed"])
        return {
            "case_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0.0,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "cases": results,
        }
    finally:
        if owned_temp:
            owned_temp.cleanup()
