# Feature: workflow-step-runner-unification, Task 5.3
"""
Unit tests for build_step_context_prompt().

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
"""

from distr.core.workflow.service import build_step_context_prompt


class TestSingleStepPassthrough:
    """Requirement 6.2: single-step with no context rules returns raw instruction."""

    def test_single_step_no_context_returns_raw(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=1,
            workflow_description="Do something",
            step_title="Step 1",
            step_instruction="Run the tests",
            prior_results=[],
            context_rules="",
        )
        assert result == "Run the tests"

    def test_single_step_none_context_rules_returns_raw(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=1,
            workflow_description="Do something",
            step_title="Step 1",
            step_instruction="Run the tests",
            prior_results=[],
            context_rules="",
            continuation_input="",
        )
        assert result == "Run the tests"

    def test_single_step_with_context_rules_does_not_passthrough(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=1,
            workflow_description="Do something",
            step_title="Step 1",
            step_instruction="Run the tests",
            prior_results=[],
            context_rules="Be careful",
        )
        assert result != "Run the tests"
        assert "[CONTEXT AND RULES]" in result
        assert "Be careful" in result


class TestMultiStepPrompt:
    """Requirement 6.1: prompt includes description, prior results, step info."""

    def test_includes_workflow_description(self):
        result = build_step_context_prompt(
            step_index=1,
            total_steps=3,
            workflow_description="Deploy the app",
            step_title="Build",
            step_instruction="Run npm build",
            prior_results=[{"title": "Setup", "result": "Done"}],
        )
        assert "Deploy the app" in result

    def test_includes_prior_results(self):
        prior = [
            {"title": "Step A", "result": "Result A"},
            {"title": "Step B", "result": "Result B"},
        ]
        result = build_step_context_prompt(
            step_index=2,
            total_steps=4,
            workflow_description="Test workflow",
            step_title="Step C",
            step_instruction="Do C",
            prior_results=prior,
        )
        assert "Step A: Result A" in result
        assert "Step B: Result B" in result

    def test_limits_prior_results_to_5(self):
        prior = [{"title": f"S{i}", "result": f"R{i}"} for i in range(8)]
        result = build_step_context_prompt(
            step_index=8,
            total_steps=10,
            workflow_description="Big workflow",
            step_title="Step 9",
            step_instruction="Do step 9",
            prior_results=prior,
        )
        # Should include last 5 (indices 3-7)
        assert "S3: R3" in result
        assert "S7: R7" in result
        # Should NOT include first 3 (indices 0-2)
        assert "S0: R0" not in result
        assert "S2: R2" not in result

    def test_includes_step_title_and_instruction(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="My Step",
            step_instruction="Do the thing",
            prior_results=[],
        )
        assert "My Step" in result
        assert "Do the thing" in result

    def test_includes_step_runner_header(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
        )
        assert "[STEP RUNNER]" in result
        assert "1 of 2" in result


class TestContextRules:
    """Requirement 6.3: context rules prepended as [CONTEXT AND RULES]."""

    def test_context_rules_section_present(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            context_rules="Always use Python 3.12",
        )
        assert "[CONTEXT AND RULES]" in result
        assert "Always use Python 3.12" in result

    def test_context_rules_before_step_runner_header(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            context_rules="Rule text",
        )
        ctx_pos = result.index("[CONTEXT AND RULES]")
        header_pos = result.index("[STEP RUNNER]")
        assert ctx_pos < header_pos

    def test_empty_context_rules_no_section(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            context_rules="",
        )
        assert "[CONTEXT AND RULES]" not in result


class TestContinuationInput:
    """Requirement 6.5: continuation input appended as [USER INPUT]."""

    def test_user_input_section_present(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            continuation_input="Please continue with option B",
        )
        assert "[USER INPUT]" in result
        assert "Please continue with option B" in result

    def test_user_input_after_main_body(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            continuation_input="User says hi",
        )
        header_pos = result.index("[STEP RUNNER]")
        input_pos = result.index("[USER INPUT]")
        assert input_pos > header_pos

    def test_no_user_input_section_when_empty(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            continuation_input="",
        )
        assert "[USER INPUT]" not in result

    def test_single_step_with_continuation_does_not_passthrough(self):
        """Even a single step should get full prompt when continuation_input is provided."""
        result = build_step_context_prompt(
            step_index=0,
            total_steps=1,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Do it",
            prior_results=[],
            context_rules="",
            continuation_input="User provided input",
        )
        assert "[USER INPUT]" in result
        assert "User provided input" in result
        assert "[STEP RUNNER]" in result


class TestVariableResolution:
    """Requirement 6.4/6.5: {{variable}} placeholders resolved before returning."""

    def test_resolves_step_variables_in_instruction(self):
        prior = [{"title": "Fetch", "result": "hello-world"}]
        result = build_step_context_prompt(
            step_index=1,
            total_steps=2,
            workflow_description="Test",
            step_title="Use result",
            step_instruction="Deploy {{step_1}}",
            prior_results=prior,
        )
        assert "hello-world" in result
        assert "{{step_1}}" not in result

    def test_resolves_variables_in_single_step_passthrough(self):
        """Even single-step passthrough should resolve variables."""
        result = build_step_context_prompt(
            step_index=0,
            total_steps=1,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Run {{step_1}} command",
            prior_results=[],
        )
        # No prior results, so {{step_1}} is unresolvable — left as-is
        assert "{{step_1}}" in result

    def test_unresolvable_placeholders_left_as_is(self):
        result = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            workflow_description="Test",
            step_title="Step 1",
            step_instruction="Use {{unknown_var}}",
            prior_results=[],
        )
        assert "{{unknown_var}}" in result
