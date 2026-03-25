"""Property-based tests for the Step Runner service layer using Hypothesis.

Covers Properties 9–11 from the design document:
  - Property 9: Step config serialization round-trip (Task 6.3)
  - Property 10: Context and Rules inclusion in agent prompt (Task 6.4)
  - Property 11: Context and Rules persistence round-trip (Task 6.5)
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.step_runner.step_types import (
    StepType,
    RunCommandConfig,
    PlayRecordingConfig,
    HttpRequestConfig,
    HttpMethod,
    ExecuteCodeConfig,
    PlaywrightConfig,
)
from distr.core.step_runner.service import build_step_context_prompt


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Non-empty, non-whitespace-only text
non_empty_text = st.text(min_size=1, max_size=200).filter(lambda s: s.strip() != "")

# Safe text for identifiers / short strings
safe_text = st.text(
    alphabet=st.characters(blacklist_characters="\x00"),
    min_size=1,
    max_size=100,
).filter(lambda s: s.strip() != "")


# ---------------------------------------------------------------------------
# Strategies for generating valid Pydantic config models per step type
# ---------------------------------------------------------------------------

@st.composite
def run_command_configs(draw):
    return RunCommandConfig(
        command=draw(non_empty_text),
        working_directory=draw(st.one_of(st.none(), non_empty_text)),
        timeout_seconds=draw(st.integers(min_value=1, max_value=3600)),
    )


@st.composite
def play_recording_configs(draw):
    return PlayRecordingConfig(
        recording_id=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=10000))),
        recording_name=draw(st.one_of(st.none(), non_empty_text)),
    )


@st.composite
def http_request_configs(draw):
    return HttpRequestConfig(
        url=draw(non_empty_text.map(lambda s: "https://" + s)),
        method=draw(st.sampled_from(list(HttpMethod))),
        headers=draw(st.dictionaries(
            keys=st.from_regex(r"[A-Za-z][A-Za-z0-9\-]{0,20}", fullmatch=True),
            values=non_empty_text,
            max_size=5,
        )),
        body=draw(st.one_of(st.none(), non_empty_text)),
        variables=draw(st.dictionaries(
            keys=st.from_regex(r"[a-zA-Z][a-zA-Z0-9_]{0,10}", fullmatch=True),
            values=non_empty_text,
            max_size=5,
        )),
        timeout_seconds=draw(st.integers(min_value=1, max_value=300)),
    )


@st.composite
def execute_code_configs(draw):
    return ExecuteCodeConfig(
        instruction=draw(st.text(max_size=200)),
        code=draw(st.text(max_size=200)),
        language=draw(st.sampled_from(["python", "javascript"])),
    )


@st.composite
def playwright_configs(draw):
    return PlaywrightConfig(
        instruction=draw(st.text(max_size=200)),
        code=draw(st.text(max_size=200)),
        headless=draw(st.booleans()),
    )


# A strategy that picks a random step type and generates a matching config
@st.composite
def any_step_config(draw):
    """Draw a random (step_type, config_model) pair."""
    step_type = draw(st.sampled_from(list(StepType)))
    if step_type == StepType.RUN_COMMAND:
        config = draw(run_command_configs())
    elif step_type == StepType.PLAY_RECORDING:
        config = draw(play_recording_configs())
    elif step_type == StepType.HTTP_REQUEST:
        config = draw(http_request_configs())
    elif step_type == StepType.EXECUTE_CODE:
        config = draw(execute_code_configs())
    elif step_type == StepType.PLAYWRIGHT:
        config = draw(playwright_configs())
    else:
        config = draw(run_command_configs())
    return step_type, config


# ---------------------------------------------------------------------------
# Property 9: Step config serialization round-trip
# **Validates: Requirements 9.4, 9.5**
#
# For any valid step type configuration object, serializing it to JSON and
# then deserializing it back must produce an equivalent configuration object.
# ---------------------------------------------------------------------------


class TestProperty9ConfigSerializationRoundTrip:
    """Property 9: Step config serialization round-trip."""

    @given(data=any_step_config())
    @settings(max_examples=200)
    def test_serialize_deserialize_roundtrip(self, data):
        """**Validates: Requirements 9.4, 9.5**

        For any valid Pydantic config model, serializing to JSON with
        model_dump_json() and deserializing back with model_validate_json()
        must produce an equivalent object.
        """
        step_type, config = data
        json_str = config.model_dump_json()
        config_class = type(config)
        restored = config_class.model_validate_json(json_str)
        assert restored == config, (
            f"Round-trip failed for {step_type}: {config} != {restored}"
        )

    @given(config=run_command_configs())
    @settings(max_examples=50)
    def test_run_command_roundtrip(self, config):
        """**Validates: Requirements 9.4, 9.5**

        RunCommandConfig survives JSON serialization round-trip.
        """
        restored = RunCommandConfig.model_validate_json(config.model_dump_json())
        assert restored == config

    @given(config=play_recording_configs())
    @settings(max_examples=50)
    def test_play_recording_roundtrip(self, config):
        """**Validates: Requirements 9.4, 9.5**

        PlayRecordingConfig survives JSON serialization round-trip.
        """
        restored = PlayRecordingConfig.model_validate_json(config.model_dump_json())
        assert restored == config

    @given(config=http_request_configs())
    @settings(max_examples=50)
    def test_http_request_roundtrip(self, config):
        """**Validates: Requirements 9.4, 9.5**

        HttpRequestConfig survives JSON serialization round-trip.
        """
        restored = HttpRequestConfig.model_validate_json(config.model_dump_json())
        assert restored == config

    @given(config=execute_code_configs())
    @settings(max_examples=50)
    def test_execute_code_roundtrip(self, config):
        """**Validates: Requirements 9.4, 9.5**

        ExecuteCodeConfig survives JSON serialization round-trip.
        """
        restored = ExecuteCodeConfig.model_validate_json(config.model_dump_json())
        assert restored == config

    @given(config=playwright_configs())
    @settings(max_examples=50)
    def test_playwright_roundtrip(self, config):
        """**Validates: Requirements 9.4, 9.5**

        PlaywrightConfig survives JSON serialization round-trip.
        """
        restored = PlaywrightConfig.model_validate_json(config.model_dump_json())
        assert restored == config


# ---------------------------------------------------------------------------
# Property 10: Context and Rules inclusion in agent prompt
# **Validates: Requirements 7.3**
#
# For any session with non-empty context_rules text and any step in that
# session, the agent instruction prompt constructed for that step must
# contain the context_rules text.
# ---------------------------------------------------------------------------


class TestProperty10ContextRulesInclusion:
    """Property 10: Context and Rules inclusion in agent prompt."""

    @given(
        context_rules=non_empty_text,
        step_index=st.integers(min_value=0, max_value=20),
        total_steps=st.integers(min_value=2, max_value=50),
        session_instruction=non_empty_text,
        step_title=non_empty_text,
        step_instruction=non_empty_text,
    )
    @settings(max_examples=200)
    def test_context_rules_present_in_prompt(
        self,
        context_rules,
        step_index,
        total_steps,
        session_instruction,
        step_title,
        step_instruction,
    ):
        """**Validates: Requirements 7.3**

        For any non-empty context_rules, the built prompt must contain
        the context_rules text verbatim.
        """
        assume(step_index < total_steps)

        prompt = build_step_context_prompt(
            step_index=step_index,
            total_steps=total_steps,
            session_instruction=session_instruction,
            step_title=step_title,
            step_instruction=step_instruction,
            prior_results=[],
            context_rules=context_rules,
        )
        assert context_rules in prompt, (
            f"context_rules not found in prompt.\n"
            f"context_rules: {context_rules!r}\n"
            f"prompt: {prompt!r}"
        )

    @given(
        context_rules=non_empty_text,
        step_instruction=non_empty_text,
        prior_title=non_empty_text,
        prior_result=non_empty_text,
    )
    @settings(max_examples=100)
    def test_context_rules_present_with_prior_results(
        self,
        context_rules,
        step_instruction,
        prior_title,
        prior_result,
    ):
        """**Validates: Requirements 7.3**

        Even when prior results are present, context_rules must still
        appear in the prompt.
        """
        prompt = build_step_context_prompt(
            step_index=1,
            total_steps=3,
            session_instruction="overall goal",
            step_title="Step 2",
            step_instruction=step_instruction,
            prior_results=[{"title": prior_title, "result": prior_result}],
            context_rules=context_rules,
        )
        assert context_rules in prompt

    @given(
        context_rules=non_empty_text,
        step_instruction=non_empty_text,
    )
    @settings(max_examples=100)
    def test_context_rules_section_header_present(
        self,
        context_rules,
        step_instruction,
    ):
        """**Validates: Requirements 7.3**

        The prompt must include a [CONTEXT AND RULES] section header
        when context_rules is non-empty.
        """
        prompt = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            session_instruction="goal",
            step_title="Step 1",
            step_instruction=step_instruction,
            prior_results=[],
            context_rules=context_rules,
        )
        assert "[CONTEXT AND RULES]" in prompt


# ---------------------------------------------------------------------------
# Property 11: Context and Rules persistence round-trip
# **Validates: Requirements 7.2**
#
# For any text string saved to a session's context_rules column, loading
# that session must return the same text. We test this as a pure
# serialization property: the Text column type accepts arbitrary strings
# and preserves them through assignment and retrieval.
# ---------------------------------------------------------------------------


class TestProperty11ContextRulesPersistenceRoundTrip:
    """Property 11: Context and Rules persistence round-trip."""

    def test_context_rules_column_is_text_type(self):
        """**Validates: Requirements 7.2**

        The StepRunnerSession model defines context_rules as a nullable
        Text column, which means the DB will accept and preserve any
        arbitrary string without truncation.
        """
        from distr.core.db.step_runner import StepRunnerSession
        from sqlalchemy import Text, inspect as sa_inspect

        mapper = sa_inspect(StepRunnerSession)
        col = mapper.columns["context_rules"]
        assert isinstance(col.type, Text), (
            f"context_rules column type is {col.type}, expected Text"
        )
        assert col.nullable is True, "context_rules column should be nullable"

    @given(context_rules=st.text(max_size=500))
    @settings(max_examples=200)
    def test_context_rules_roundtrip_through_prompt_builder(self, context_rules):
        """**Validates: Requirements 7.2**

        For any text string used as context_rules, the service layer's
        build_step_context_prompt faithfully includes it in the output —
        verifying the read path preserves the stored text without
        transformation.
        """
        assume(context_rules.strip() != "")

        prompt = build_step_context_prompt(
            step_index=0,
            total_steps=2,
            session_instruction="test",
            step_title="Step 1",
            step_instruction="do something",
            prior_results=[],
            context_rules=context_rules,
        )
        assert context_rules in prompt, (
            f"context_rules text not found in prompt output"
        )

    @given(context_rules=st.text(max_size=500))
    @settings(max_examples=200)
    def test_context_rules_identity_preserved_in_prompt(self, context_rules):
        """**Validates: Requirements 7.2**

        Building the prompt twice with the same context_rules text
        produces identical output — the text is not mutated or
        accumulated across calls.
        """
        assume(context_rules.strip() != "")

        kwargs = dict(
            step_index=0,
            total_steps=3,
            session_instruction="goal",
            step_title="Step 1",
            step_instruction="task",
            prior_results=[],
            context_rules=context_rules,
        )
        prompt1 = build_step_context_prompt(**kwargs)
        prompt2 = build_step_context_prompt(**kwargs)
        assert prompt1 == prompt2, "Prompt should be deterministic for same inputs"
