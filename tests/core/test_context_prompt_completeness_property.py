# Feature: workflow-step-runner-unification, Property 5: Context prompt completeness
"""
Property-based test verifying that `build_step_context_prompt()` includes all
provided components in the assembled prompt for multi-step workflows.

- workflow_description appears in the prompt
- All prior_results (up to 5) appear in the prompt
- step_title appears in the prompt
- step_instruction appears in the prompt
- When context_rules is non-empty, [CONTEXT AND RULES] section appears with the rules text
- When continuation_input is non-empty, [USER INPUT] section appears with the input text

**Validates: Requirements 6.1, 6.3, 6.5, 4.4**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow.service import build_step_context_prompt

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Simple alphanumeric text that avoids {{ patterns (to prevent variable
# resolution from altering the text).
_safe_text = st.from_regex(r"[A-Za-z0-9 ]{1,60}", fullmatch=True).filter(
    lambda s: "{{" not in s and s.strip()
)

_prior_result_strategy = st.fixed_dictionaries(
    {"title": _safe_text, "result": _safe_text}
)

_prior_results_strategy = st.lists(
    _prior_result_strategy, min_size=0, max_size=5
)

# total_steps >= 2 so the single-step passthrough doesn't apply
_total_steps_strategy = st.integers(min_value=2, max_value=20)

# context_rules: either empty string or non-empty safe text
_context_rules_strategy = st.one_of(st.just(""), _safe_text)

# continuation_input: either empty string or non-empty safe text
_continuation_input_strategy = st.one_of(st.just(""), _safe_text)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    workflow_description=_safe_text,
    prior_results=_prior_results_strategy,
    step_title=_safe_text,
    step_instruction=_safe_text,
    context_rules=_context_rules_strategy,
    continuation_input=_continuation_input_strategy,
    total_steps=_total_steps_strategy,
)
def test_context_prompt_contains_all_components(
    workflow_description: str,
    prior_results: list,
    step_title: str,
    step_instruction: str,
    context_rules: str,
    continuation_input: str,
    total_steps: int,
) -> None:
    """**Validates: Requirements 6.1, 6.3, 6.5, 4.4**

    For any multi-step workflow (total_steps >= 2), the assembled context prompt
    SHALL contain the workflow description, all prior results (up to 5), the
    step title, the step instruction, context rules as [CONTEXT AND RULES] when
    non-empty, and continuation input as [USER INPUT] when non-empty."""
    step_index = min(len(prior_results), total_steps - 1)

    prompt = build_step_context_prompt(
        step_index=step_index,
        total_steps=total_steps,
        workflow_description=workflow_description,
        step_title=step_title,
        step_instruction=step_instruction,
        prior_results=prior_results,
        context_rules=context_rules,
        continuation_input=continuation_input,
    )

    # workflow_description appears in the prompt
    assert workflow_description in prompt, (
        f"workflow_description {workflow_description!r} not found in prompt"
    )

    # All prior_results (up to last 5) appear in the prompt
    for item in prior_results[-5:]:
        title = item.get("title") or "Step"
        result = item.get("result") or "Completed."
        assert title in prompt, (
            f"prior result title {title!r} not found in prompt"
        )
        assert result in prompt, (
            f"prior result text {result!r} not found in prompt"
        )

    # step_title appears in the prompt
    assert step_title in prompt, (
        f"step_title {step_title!r} not found in prompt"
    )

    # step_instruction appears in the prompt
    assert step_instruction in prompt, (
        f"step_instruction {step_instruction!r} not found in prompt"
    )

    # When context_rules is non-empty, [CONTEXT AND RULES] section appears
    if context_rules:
        assert "[CONTEXT AND RULES]" in prompt, (
            "Expected [CONTEXT AND RULES] section when context_rules is non-empty"
        )
        assert context_rules in prompt, (
            f"context_rules {context_rules!r} not found in prompt"
        )
    else:
        assert "[CONTEXT AND RULES]" not in prompt, (
            "Unexpected [CONTEXT AND RULES] section when context_rules is empty"
        )

    # When continuation_input is non-empty, [USER INPUT] section appears
    if continuation_input:
        assert "[USER INPUT]" in prompt, (
            "Expected [USER INPUT] section when continuation_input is non-empty"
        )
        assert continuation_input in prompt, (
            f"continuation_input {continuation_input!r} not found in prompt"
        )
    else:
        assert "[USER INPUT]" not in prompt, (
            "Unexpected [USER INPUT] section when continuation_input is empty"
        )
