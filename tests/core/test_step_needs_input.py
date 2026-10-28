"""A worker that says it needs a human must pause the run, not pass it."""

from __future__ import annotations

import pytest

from distr.core.workflow.step_executor import _result_reports_needs_input


@pytest.mark.parametrize(
    "text",
    [
        "Status: needs_input",
        "**Status:** needs_input",
        "  Status : needs_input  ",
        "Project CLI backend: cursor\nStatus: needs_input\nBlockers: allow shell",
    ],
)
def test_needs_input_contract_is_detected(text):
    assert _result_reports_needs_input(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Status: completed",
        "**Status:** passed",
        "it mentions needs_input in prose only",
        "",
    ],
)
def test_non_blocking_text_is_not_flagged(text):
    assert _result_reports_needs_input(text) is False
