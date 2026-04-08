# Feature: workflow-step-runner-unification, Property 2: Invalid workflow_type is rejected
"""
Property-based test verifying that only the four allowed workflow_type values
(`manual`, `instruction`, `scheduled`, `audit`) are accepted by
validate_workflow_type(), and all other strings are rejected.

**Validates: Requirements 1.7**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow.service import validate_workflow_type, VALID_WORKFLOW_TYPES

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for valid workflow types — draw from the exact allowed set.
_valid_type_strategy = st.sampled_from(sorted(VALID_WORKFLOW_TYPES))

# Strategy for arbitrary strings that are NOT in the allowed set.
_arbitrary_string_strategy = st.text(min_size=0, max_size=200)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(wf_type=_valid_type_strategy)
def test_valid_workflow_types_are_accepted(wf_type: str) -> None:
    """**Validates: Requirements 1.7**

    For any string in the allowed set {manual, instruction, scheduled, audit},
    validate_workflow_type() SHALL return True (accept)."""
    assert validate_workflow_type(wf_type) is True, (
        f"Expected workflow_type {wf_type!r} to be accepted, but it was rejected"
    )


@settings(max_examples=100)
@given(wf_type=_arbitrary_string_strategy)
def test_invalid_workflow_types_are_rejected(wf_type: str) -> None:
    """**Validates: Requirements 1.7**

    For any string that is NOT one of {manual, instruction, scheduled, audit},
    validate_workflow_type() SHALL return False (reject)."""
    assume(wf_type not in VALID_WORKFLOW_TYPES)
    assert validate_workflow_type(wf_type) is False, (
        f"Expected workflow_type {wf_type!r} to be rejected, but it was accepted"
    )
