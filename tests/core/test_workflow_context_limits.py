from distr.core.workflow.context_limits import compact_execution_result
from distr.core.workflow.router import _missing_expected_outputs


def test_compact_execution_result_preserves_required_contract_at_tail():
    verbose_inspection = "inspection evidence\n" * 600
    contract = """context_packet: scoped evidence
unknowns: none
route_recommendation: use the selected local worker
ui_design_read_if_applicable: N/A because this is backend-only
Status: completed
"""

    compact = compact_execution_result(verbose_inspection + contract, max_chars=6000)

    assert len(compact) <= 6000
    assert "verbose worker output omitted" in compact
    assert _missing_expected_outputs(
        compact,
        [
            "context_packet",
            "unknowns",
            "route_recommendation",
            "ui_design_read_if_applicable",
        ],
    ) == []
    assert compact.endswith("Status: completed")


def test_compact_execution_result_leaves_short_output_unchanged():
    value = "context_packet: compact\nStatus: completed"

    assert compact_execution_result(value) == value
