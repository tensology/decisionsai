from pathlib import Path

from scripts.benchmark_complex_models import (
    ALLOWED_CHANGES,
    FILES,
    PROMPT,
    _copy_workspace,
    _count_test_results,
    _hidden_contract_checks,
    grade_workspace,
)


def test_fixture_is_mixed_language_and_contains_decoys():
    suffixes = {Path(path).suffix for path in FILES}
    assert {".py", ".js", ".jsx", ".html"} <= suffixes
    assert {"legacy/TicketBoard.jsx", "generated/api-client.js", "vendor/query.py"} <= set(FILES)


def test_prompt_names_all_and_only_allowed_mutation_paths():
    for path in ALLOWED_CHANGES:
        assert path in PROMPT
    assert "Do not modify tests" in PROMPT
    assert "legacy/" in PROMPT


def test_pristine_fixture_fails_behavior_but_passes_scope_gate(tmp_path):
    folder = tmp_path / "fixture"
    _copy_workspace(folder)
    result = grade_workspace(folder)
    assert result["tests_exit_code"] != 0
    assert result["behavior"]["failed"] > 0
    assert result["scope_discipline"]["points"] == 8


def test_scope_grader_detects_protected_file_edits(tmp_path):
    folder = tmp_path / "fixture"
    _copy_workspace(folder)
    (folder / "app/models/ticket.py").write_text("# wrong layer\n", encoding="utf-8")
    result = grade_workspace(folder)
    assert result["scope_discipline"]["points"] == 0
    assert "app/models/ticket.py" in result["scope_discipline"]["protected_changes"]


def test_scope_grader_detects_unexpected_files(tmp_path):
    folder = tmp_path / "fixture"
    _copy_workspace(folder)
    (folder / "notes.txt").write_text("unrequested artifact", encoding="utf-8")
    result = grade_workspace(folder)
    assert "notes.txt" in result["scope_discipline"]["unexpected_files"]


def test_test_result_parser_counts_subtests_as_one_gate():
    output = "test_one ... ok\ntest_two ... FAIL\ntest_three ... ERROR\n"
    assert _count_test_results(output) == (1, 2)


def test_hidden_contracts_cover_preservation_rules_outside_visible_tests(tmp_path):
    folder = tmp_path / "fixture"
    _copy_workspace(folder)
    checks = _hidden_contract_checks(folder)
    assert len(checks) == 10
    assert checks["view_reuses_shared_query_helper"] is False
    assert all(value for name, value in checks.items() if name != "view_reuses_shared_query_helper")


def test_hidden_contracts_detect_cross_layer_regression(tmp_path):
    folder = tmp_path / "fixture"
    _copy_workspace(folder)
    component = folder / "web/components/TicketBoard.jsx"
    component.write_text(component.read_text().replace(" aria-busy={loading}", ""), encoding="utf-8")
    checks = _hidden_contract_checks(folder)
    assert checks["component_loading_contract_preserved"] is False
