"""Property-based tests for Step Input Context Assembly using Hypothesis.

Covers Property 13 from the design document:
  - Property 13: Context assembly per step type (Task 7.4)

**Validates: Requirements 10.2, 10.3, 10.4, 10.5**
"""

import json
from types import SimpleNamespace

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow_engine.context_assembly import (
    StepInputContext,
    WorkflowInput,
    assemble_step_context,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

non_empty_text = st.text(min_size=1, max_size=200).filter(lambda s: s.strip() != "")

source_types = st.sampled_from(["instruction", "kanban_ticket", "api", "scheduled"])

image_paths = st.lists(
    st.text(min_size=1, max_size=50).map(lambda s: f"/img/{s}.png"),
    max_size=3,
)

attachment_strategy = st.lists(
    st.fixed_dictionaries({
        "filename": st.text(min_size=1, max_size=20),
        "path": st.text(min_size=1, max_size=50),
    }),
    max_size=3,
)


@st.composite
def workflow_input_dicts(draw):
    """Generate a valid workflow_input dict."""
    return {
        "source_type": draw(source_types),
        "text": draw(non_empty_text),
        "title": draw(st.text(max_size=100)),
        "images": draw(image_paths),
        "attachments": draw(attachment_strategy),
        "metadata": draw(st.dictionaries(
            keys=st.text(min_size=1, max_size=10).filter(lambda s: s.strip() != ""),
            values=st.text(max_size=50),
            max_size=3,
        )),
    }


@st.composite
def prior_results_strategy(draw):
    """Generate a list of prior step results."""
    return draw(st.lists(
        st.fixed_dictionaries({
            "result": st.text(max_size=200),
            "title": non_empty_text,
            "step_type": st.sampled_from(["run_command", "http_request", "execute_code"]),
        }),
        min_size=0,
        max_size=5,
    ))


@st.composite
def step_config_dicts(draw):
    """Generate a step config dict."""
    return draw(st.dictionaries(
        keys=st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""),
        values=st.one_of(non_empty_text, st.integers(min_value=0, max_value=1000), st.booleans()),
        max_size=5,
    ))


# The 5 new step types
NEW_STEP_TYPES = ["run_command", "play_recording", "http_request", "execute_code", "playwright"]

# Types treated as agent_instruction (anything not in the 5 new types)
AGENT_INSTRUCTION_TYPES = ["agent_instruction", "some_custom_type", ""]


def _make_session(context_rules=None, workflow_input_dict=None):
    return SimpleNamespace(
        context_rules=context_rules,
        workflow_input=(
            json.dumps(workflow_input_dict) if workflow_input_dict else None
        ),
    )


def _make_step(step_type, config_dict=None):
    return SimpleNamespace(
        step_type=step_type,
        config=json.dumps(config_dict) if config_dict else None,
    )


# ---------------------------------------------------------------------------
# Property 13: Context assembly per step type
# ---------------------------------------------------------------------------


class TestProperty13ContextAssemblyPerStepType:
    """Property 13: Context assembly per step type."""

    @given(
        context_rules=non_empty_text,
        wi_dict=workflow_input_dicts(),
        prior=prior_results_strategy(),
        config=step_config_dicts(),
        step_type=st.sampled_from(AGENT_INSTRUCTION_TYPES),
    )
    @settings(max_examples=200)
    def test_agent_instruction_has_all_fields(
        self, context_rules, wi_dict, prior, config, step_type,
    ):
        """**Validates: Requirements 10.2**

        Agent Instruction steps (and any unknown type treated as agent_instruction)
        have all fields populated: workflow_input with images, workflow_rules,
        previous_results, and step_config.
        """
        session = _make_session(context_rules=context_rules, workflow_input_dict=wi_dict)
        step = _make_step(step_type, config)
        ctx = assemble_step_context(session, step, prior)

        # workflow_input present with full data including images
        assert ctx.workflow_input is not None
        assert ctx.workflow_input.source_type == wi_dict["source_type"]
        assert ctx.workflow_input.text == wi_dict["text"]
        assert ctx.workflow_input.images == wi_dict["images"]
        assert ctx.workflow_input.attachments == wi_dict["attachments"]

        # workflow_rules present
        assert ctx.workflow_rules == context_rules

        # previous_results present
        assert ctx.previous_results == prior

        # step_config present
        assert ctx.step_config == config

    @given(
        context_rules=non_empty_text,
        wi_dict=workflow_input_dicts(),
        prior=prior_results_strategy(),
        config=step_config_dicts(),
        step_type=st.sampled_from(["execute_code", "playwright"]),
    )
    @settings(max_examples=200)
    def test_execute_code_playwright_text_only(
        self, context_rules, wi_dict, prior, config, step_type,
    ):
        """**Validates: Requirements 10.3**

        Execute Code and Playwright steps have workflow_input (text only, no images),
        workflow_rules, step_config, and resolved_variables.
        """
        session = _make_session(context_rules=context_rules, workflow_input_dict=wi_dict)
        step = _make_step(step_type, config)
        ctx = assemble_step_context(session, step, prior)

        # workflow_input present but images/attachments stripped
        assert ctx.workflow_input is not None
        assert ctx.workflow_input.text == wi_dict["text"]
        assert ctx.workflow_input.images == []
        assert ctx.workflow_input.attachments == []

        # workflow_rules present
        assert ctx.workflow_rules == context_rules

        # step_config present
        assert ctx.step_config == config

        # resolved_variables populated from prior results
        for i in range(len(prior)):
            assert f"step_{i + 1}" in ctx.resolved_variables

    @given(
        context_rules=non_empty_text,
        wi_dict=workflow_input_dicts(),
        prior=prior_results_strategy(),
        config=step_config_dicts(),
        step_type=st.sampled_from(["run_command", "http_request"]),
    )
    @settings(max_examples=200)
    def test_run_command_http_request_variables_only(
        self, context_rules, wi_dict, prior, config, step_type,
    ):
        """**Validates: Requirements 10.4**

        Run Command and HTTP Request steps have only resolved_variables and
        step_config — no workflow_input or workflow_rules.
        """
        session = _make_session(context_rules=context_rules, workflow_input_dict=wi_dict)
        step = _make_step(step_type, config)
        ctx = assemble_step_context(session, step, prior)

        # No workflow_input
        assert ctx.workflow_input is None

        # No workflow_rules
        assert ctx.workflow_rules == ""

        # No previous_results list
        assert ctx.previous_results == []

        # step_config present
        assert ctx.step_config == config

        # resolved_variables populated from prior results
        for i in range(len(prior)):
            assert f"step_{i + 1}" in ctx.resolved_variables

    @given(
        context_rules=non_empty_text,
        wi_dict=workflow_input_dicts(),
        prior=prior_results_strategy(),
        config=step_config_dicts(),
    )
    @settings(max_examples=200)
    def test_play_recording_config_only(
        self, context_rules, wi_dict, prior, config,
    ):
        """**Validates: Requirements 10.5**

        Play Recording steps have only step_config — no workflow_input,
        no workflow_rules, no previous_results, no resolved_variables.
        """
        session = _make_session(context_rules=context_rules, workflow_input_dict=wi_dict)
        step = _make_step("play_recording", config)
        ctx = assemble_step_context(session, step, prior)

        # Only step_config
        assert ctx.step_config == config

        # Nothing else
        assert ctx.workflow_input is None
        assert ctx.workflow_rules == ""
        assert ctx.previous_results == []
        assert ctx.resolved_variables == {}
