from __future__ import annotations

import json
from pathlib import Path

import pytest

from distr.core.ui_factory import (
    ContractValidationError,
    ScreenContract,
    choose_ui_execution_route,
    compile_screen_contract,
    representative_contracts,
    run_template_benchmark,
)
from distr.core.ui_factory.budgets import output_token_budget
from distr.core.ui_factory.learning import UIFactoryOutcome, record_ui_factory_outcome


def test_all_representative_contracts_validate_and_cover_each_pack():
    contracts = [ScreenContract.from_dict(item) for item in representative_contracts()]
    assert len(contracts) == 10
    assert {item.screen_type.value for item in contracts} == {
        "admin-screen", "crud-list-detail", "customer-portal", "dashboard", "funnel"
    }


def test_modal_contract_rejects_long_or_nested_forms():
    payload = representative_contracts()[0] | {
        "form_mode": "modal",
        "fields": [{"name": f"field_{index}", "nested": index == 0} for index in range(6)],
    }
    with pytest.raises(ContractValidationError, match="five non-nested fields"):
        ScreenContract.from_dict(payload)


def test_contract_rejects_unsafe_allowed_file_path():
    payload = representative_contracts()[0] | {"allowed_files": ["../outside.tsx"]}
    with pytest.raises(ContractValidationError, match="unsafe path"):
        ScreenContract.from_dict(payload)


def test_compiler_writes_bounded_react_artifacts_and_manifest(tmp_path: Path):
    result = compile_screen_contract(representative_contracts()[0], tmp_path)
    assert len(result.files) == 4
    manifest = json.loads(next(tmp_path.glob("*.ui-manifest.json")).read_text())
    component = next(tmp_path.glob("*.tsx")).read_text()
    stylesheet = next(tmp_path.glob("*.css")).read_text()
    assert manifest["required_viewports"] == ["desktop", "mid", "mobile"]
    assert "data-screen-id" in component
    assert "[data-theme='dark']" in stylesheet
    assert "@media (max-width: 720px)" in stylesheet


def test_compiler_refuses_unapproved_overwrite(tmp_path: Path):
    payload = representative_contracts()[0]
    compile_screen_contract(payload, tmp_path)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        compile_screen_contract(payload, tmp_path)


def test_local_route_escalates_only_after_two_gate_failures():
    first = choose_ui_execution_route("implementation", quality_failures=1)
    second = choose_ui_execution_route("implementation", quality_failures=2)
    assert first.provider == "ollama" and first.hosted is False
    assert second.provider == "openai" and second.hosted is True
    assert first.output_token_budget == output_token_budget("implementation")


def test_ten_case_benchmark_passes(tmp_path: Path):
    result = run_template_benchmark(tmp_path)
    assert result["case_count"] == 10
    assert result["passed"] == 10
    assert result["pass_rate"] == 1.0


def test_ui_outcome_dual_writes_learning_without_auto_promoting(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "distr.core.orchestrator.record_learning_signal",
        lambda **kwargs: recorded.setdefault("rule", kwargs) or 41,
    )
    monkeypatch.setattr(
        "distr.core.mempalace.wiring.dual_write_learning",
        lambda **kwargs: recorded.setdefault("memory", kwargs) or {"ok": True},
    )

    result = record_ui_factory_outcome(UIFactoryOutcome(
        screen_type="crud-list-detail",
        template_pack="crud-list-detail",
        model="qwen3.8:27b",
        provider="ollama",
        passed=False,
        correction_count=1,
        rejection_labels=("spacing_off",),
    ))

    assert result["payload"]["rejection_labels"] == ("spacing_off",)
    assert recorded["rule"]["enabled"] is False
    assert recorded["rule"]["promote_after"] == 3
    assert recorded["memory"]["learning_type"] == "ui_factory_outcome"
