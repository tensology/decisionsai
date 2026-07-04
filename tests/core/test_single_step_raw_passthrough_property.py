# Feature: workflow-step-runner-unification, Property 6: Single-step raw instruction passthrough
"""
Property-based test verifying that `build_step_context_prompt()` returns the
raw step instruction unchanged when total_steps=1, prior_results is empty,
workflow_description is empty, and context_rules is empty or None.

**Validates: Requirements 6.2**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.workflow.service import build_step_context_prompt

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Simple alphanumeric text that avoids {{ patterns (to prevent variable
# resolution from altering the text).
_safe_instruction = st.from_regex(r"[A-Za-z0-9 ]{1,120}", fullmatch=True).filter(
    lambda s: "{{" not in s and s.strip()
)

# context_rules: empty string or None (both should trigger passthrough)
_empty_context_rules = st.one_of(st.just(""), st.just(None))

# step_title is irrelevant for single-step passthrough when no workflow goal is present.
_safe_text = st.from_regex(r"[A-Za-z0-9 ]{1,60}", fullmatch=True).filter(
    lambda s: "{{" not in s and s.strip()
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    step_instruction=_safe_instruction,
    context_rules=_empty_context_rules,
    workflow_description=st.just(""),
    step_title=_safe_text,
)
def test_single_step_returns_raw_instruction(
    step_instruction: str,
    context_rules: str,
    workflow_description: str,
    step_title: str,
) -> None:
    """**Validates: Requirements 6.2**

    For any step instruction string, when total_steps is 1, prior_results is
    empty, workflow_description is empty, and context_rules is empty or None,
    build_step_context_prompt() SHALL return the raw step instruction unchanged."""
    result = build_step_context_prompt(
        step_index=0,
        total_steps=1,
        workflow_description=workflow_description,
        step_title=step_title,
        step_instruction=step_instruction,
        prior_results=[],
        context_rules=context_rules or "",
        continuation_input="",
    )

    assert result == step_instruction, (
        f"Expected raw instruction {step_instruction!r}, got {result!r}"
    )
