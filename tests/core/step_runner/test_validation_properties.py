"""Property-based tests for StepValidator using Hypothesis.

Covers Properties 1–4 from the design document:
  - Property 1: Validation before execution (Task 2.3)
  - Property 2: Validation failure preserves step status (Task 2.4)
  - Property 3: Validation error structure (Task 2.5)
  - Property 4: Per-type validation correctness (Task 2.6)
"""

import copy

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow_engine.step_types import StepType, HttpMethod
from distr.core.workflow_engine.validation import StepValidator, ValidationError


validator = StepValidator()

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Non-empty, non-whitespace-only text
non_empty_text = st.text(min_size=1).filter(lambda s: s.strip() != "")

# Text that is empty or whitespace-only
empty_or_whitespace = st.one_of(st.just(""), st.text().filter(lambda s: s.strip() == ""))

# All known step type string values
known_step_types = st.sampled_from([t.value for t in StepType])

# Strings that are NOT a known step type value
unknown_step_types = st.text(min_size=1).filter(
    lambda s: s not in {t.value for t in StepType}
)

# Valid HTTP methods
valid_http_methods = st.sampled_from([m.value for m in HttpMethod])

# Invalid HTTP methods — strings not in the valid set
invalid_http_methods = st.text(min_size=1).filter(
    lambda s: s.upper() not in {m.value for m in HttpMethod}
)

# URLs that start with http:// or https://
valid_urls = st.one_of(
    non_empty_text.map(lambda s: "https://" + s),
    non_empty_text.map(lambda s: "http://" + s),
)

# URLs that do NOT start with http:// or https:// (and are non-empty)
non_http_urls = non_empty_text.filter(
    lambda s: not s.strip().startswith(("http://", "https://"))
)

# ---------------------------------------------------------------------------
# Strategy builders for valid configs per step type
# ---------------------------------------------------------------------------


@st.composite
def valid_config_for(draw, step_type: str):
    """Return a config dict that should pass validation for *step_type*."""
    if step_type == StepType.RUN_COMMAND.value:
        return {"command": draw(non_empty_text)}

    if step_type == StepType.PLAY_RECORDING.value:
        # At least one of recording_id or recording_name must be present
        choice = draw(st.integers(min_value=0, max_value=2))
        if choice == 0:
            return {"recording_id": draw(st.integers(min_value=1))}
        elif choice == 1:
            return {"recording_name": draw(non_empty_text)}
        else:
            return {
                "recording_id": draw(st.integers(min_value=1)),
                "recording_name": draw(non_empty_text),
            }

    if step_type == StepType.HTTP_REQUEST.value:
        cfg = {"url": draw(valid_urls)}
        if draw(st.booleans()):
            cfg["method"] = draw(valid_http_methods)
        return cfg

    if step_type == StepType.EXECUTE_CODE.value:
        choice = draw(st.integers(min_value=0, max_value=2))
        if choice == 0:
            return {"instruction": draw(non_empty_text)}
        elif choice == 1:
            return {"code": draw(non_empty_text)}
        else:
            return {
                "instruction": draw(non_empty_text),
                "code": draw(non_empty_text),
            }

    if step_type == StepType.PLAYWRIGHT.value:
        choice = draw(st.integers(min_value=0, max_value=2))
        if choice == 0:
            return {"instruction": draw(non_empty_text)}
        elif choice == 1:
            return {"code": draw(non_empty_text)}
        else:
            return {
                "instruction": draw(non_empty_text),
                "code": draw(non_empty_text),
            }

    if step_type == StepType.SEND_TO_PROJECT_CLI.value:
        return {"instruction": draw(non_empty_text)}

    if step_type == StepType.COMPUTER_USE.value:
        if draw(st.booleans()):
            return {"goal": draw(non_empty_text)}
        return {"instruction": draw(non_empty_text)}

    # Should not reach here for known types
    return {}


@st.composite
def invalid_config_for(draw, step_type: str):
    """Return a config dict that should FAIL validation for *step_type*."""
    if step_type == StepType.RUN_COMMAND.value:
        return {"command": draw(empty_or_whitespace)}

    if step_type == StepType.PLAY_RECORDING.value:
        # Neither recording_id nor a non-blank recording_name
        cfg = {}
        if draw(st.booleans()):
            cfg["recording_name"] = draw(empty_or_whitespace)
        return cfg

    if step_type == StepType.HTTP_REQUEST.value:
        # Either empty URL or non-http URL
        choice = draw(st.integers(min_value=0, max_value=1))
        if choice == 0:
            return {"url": draw(empty_or_whitespace)}
        else:
            return {"url": draw(non_http_urls)}

    if step_type == StepType.EXECUTE_CODE.value:
        return {
            "instruction": draw(empty_or_whitespace),
            "code": draw(empty_or_whitespace),
        }

    if step_type == StepType.PLAYWRIGHT.value:
        return {
            "instruction": draw(empty_or_whitespace),
            "code": draw(empty_or_whitespace),
        }

    if step_type == StepType.SEND_TO_PROJECT_CLI.value:
        return {"instruction": draw(empty_or_whitespace)}

    if step_type == StepType.COMPUTER_USE.value:
        return {
            "goal": draw(empty_or_whitespace),
            "instruction": draw(empty_or_whitespace),
        }

    return {}


# ---------------------------------------------------------------------------
# Property 1: Validation before execution
# **Validates: Requirements 2.1**
#
# For any step of any step type, executing that step must first pass
# validation — no step can enter "running" status without the
# Validation_Service returning zero errors for its type and config.
# ---------------------------------------------------------------------------


class TestProperty1ValidationBeforeExecution:
    """Property 1: Validation before execution."""

    @given(step_type=known_step_types, data=st.data())
    @settings(max_examples=200)
    def test_valid_configs_produce_no_errors(self, step_type, data):
        """**Validates: Requirements 2.1**

        For any known step type with a valid config, validation returns an
        empty error list — the step is cleared for execution.
        """
        config = data.draw(valid_config_for(step_type))
        errors = validator.validate(step_type, config)
        assert errors == [], f"Expected no errors for {step_type} with {config}, got {errors}"

    @given(step_type=known_step_types, data=st.data())
    @settings(max_examples=200)
    def test_invalid_configs_produce_errors(self, step_type, data):
        """**Validates: Requirements 2.1**

        For any known step type with an invalid config, validation returns a
        non-empty error list — the step must NOT proceed to execution.
        """
        config = data.draw(invalid_config_for(step_type))
        errors = validator.validate(step_type, config)
        assert len(errors) > 0, f"Expected errors for {step_type} with {config}"


# ---------------------------------------------------------------------------
# Property 2: Validation failure preserves step status
# **Validates: Requirements 2.4**
#
# Validation is a pure function — calling validate on invalid config returns
# errors but doesn't mutate the config dict.
# ---------------------------------------------------------------------------


class TestProperty2ValidationPreservesStatus:
    """Property 2: Validation failure preserves step status."""

    @given(step_type=known_step_types, data=st.data())
    @settings(max_examples=200)
    def test_validation_does_not_mutate_config(self, step_type, data):
        """**Validates: Requirements 2.4**

        Calling validate on an invalid config returns errors but the config
        dict itself is unchanged — validation is side-effect-free.
        """
        config = data.draw(invalid_config_for(step_type))
        config_before = copy.deepcopy(config)
        _ = validator.validate(step_type, config)
        assert config == config_before, (
            f"Config was mutated during validation: before={config_before}, after={config}"
        )

    @given(step_type=known_step_types, data=st.data())
    @settings(max_examples=200)
    def test_validation_does_not_mutate_valid_config(self, step_type, data):
        """**Validates: Requirements 2.4**

        Even for valid configs, the dict must not be mutated.
        """
        config = data.draw(valid_config_for(step_type))
        config_before = copy.deepcopy(config)
        _ = validator.validate(step_type, config)
        assert config == config_before, (
            f"Config was mutated during validation: before={config_before}, after={config}"
        )


# ---------------------------------------------------------------------------
# Property 3: Validation error structure
# **Validates: Requirements 2.2**
#
# Every error returned by the Validation_Service must contain a non-empty
# `field` string and a non-empty `message` string.
# ---------------------------------------------------------------------------


class TestProperty3ValidationErrorStructure:
    """Property 3: Validation error structure."""

    @given(step_type=known_step_types, data=st.data())
    @settings(max_examples=200)
    def test_errors_have_non_empty_field_and_message(self, step_type, data):
        """**Validates: Requirements 2.2**

        For any invalid config, every returned ValidationError has a
        non-empty field and a non-empty message.
        """
        config = data.draw(invalid_config_for(step_type))
        errors = validator.validate(step_type, config)
        assert len(errors) > 0
        for err in errors:
            assert isinstance(err, ValidationError)
            assert isinstance(err.field, str) and len(err.field) > 0, (
                f"Error field is empty: {err}"
            )
            assert isinstance(err.message, str) and len(err.message) > 0, (
                f"Error message is empty: {err}"
            )

    @given(unknown=unknown_step_types)
    @settings(max_examples=100)
    def test_unknown_type_errors_have_structure(self, unknown):
        """**Validates: Requirements 2.2**

        Unknown step types also produce well-structured errors.
        """
        errors = validator.validate(unknown, {})
        assert len(errors) > 0
        for err in errors:
            assert isinstance(err, ValidationError)
            assert len(err.field) > 0
            assert len(err.message) > 0


# ---------------------------------------------------------------------------
# Property 4: Per-type validation correctness
# **Validates: Requirements 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11**
#
# Test all the specific validation rules per step type.
# ---------------------------------------------------------------------------


class TestProperty4PerTypeValidationCorrectness:
    """Property 4: Per-type validation correctness."""

    # (a) empty command → error on "command"
    @given(cmd=empty_or_whitespace)
    @settings(max_examples=100)
    def test_run_command_empty_command_errors_on_command(self, cmd):
        """**Validates: Requirements 2.5**

        A Run Command config with an empty/whitespace command produces a
        validation error on the "command" field.
        """
        errors = validator.validate("run_command", {"command": cmd})
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "command" in fields

    # (b) no recording_id/name → error on "recording"
    @given(name=st.one_of(st.none(), empty_or_whitespace))
    @settings(max_examples=100)
    def test_play_recording_missing_both_errors_on_recording(self, name):
        """**Validates: Requirements 2.6**

        A Play Recording config with no recording_id and no valid
        recording_name produces a validation error on "recording".
        """
        config = {}
        if name is not None:
            config["recording_name"] = name
        errors = validator.validate("play_recording", config)
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "recording" in fields

    # (c) empty/non-http URL → error on "url"
    @given(url=st.one_of(empty_or_whitespace, non_http_urls))
    @settings(max_examples=100)
    def test_http_request_bad_url_errors_on_url(self, url):
        """**Validates: Requirements 2.7**

        An HTTP Request config with an empty or non-http(s) URL produces a
        validation error on the "url" field.
        """
        errors = validator.validate("http_request", {"url": url})
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "url" in fields

    # (d) invalid method → error on "method"
    @given(method=invalid_http_methods)
    @settings(max_examples=100)
    def test_http_request_invalid_method_errors_on_method(self, method):
        """**Validates: Requirements 2.8**

        An HTTP Request config with an invalid method produces a validation
        error on the "method" field.
        """
        errors = validator.validate(
            "http_request", {"url": "https://example.com", "method": method}
        )
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "method" in fields

    # (e) empty instruction+code for execute_code → error on "instruction"
    @given(
        instruction=empty_or_whitespace,
        code=empty_or_whitespace,
    )
    @settings(max_examples=100)
    def test_execute_code_empty_both_errors_on_instruction(self, instruction, code):
        """**Validates: Requirements 2.9**

        An Execute Code config with both instruction and code empty produces
        a validation error on the "instruction" field.
        """
        errors = validator.validate(
            "execute_code", {"instruction": instruction, "code": code}
        )
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "instruction" in fields

    # (f) same for playwright
    @given(
        instruction=empty_or_whitespace,
        code=empty_or_whitespace,
    )
    @settings(max_examples=100)
    def test_playwright_empty_both_errors_on_instruction(self, instruction, code):
        """**Validates: Requirements 2.10**

        A Playwright config with both instruction and code empty produces a
        validation error on the "instruction" field.
        """
        errors = validator.validate(
            "playwright", {"instruction": instruction, "code": code}
        )
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "instruction" in fields

    # (g) empty goal+instruction for computer_use → error on "goal"
    @given(
        goal=empty_or_whitespace,
        instruction=empty_or_whitespace,
    )
    @settings(max_examples=100)
    def test_computer_use_empty_goal_errors_on_goal(self, goal, instruction):
        """A Computer Use config with no goal/instruction errors on "goal"."""
        errors = validator.validate(
            "computer_use", {"goal": goal, "instruction": instruction}
        )
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "goal" in fields

    # (h) unknown step type → error on "step_type"
    @given(unknown=unknown_step_types)
    @settings(max_examples=100)
    def test_unknown_step_type_errors_on_step_type(self, unknown):
        """**Validates: Requirements 2.11**

        An unknown step type string produces a validation error on the
        "step_type" field.
        """
        errors = validator.validate(unknown, {})
        assert len(errors) >= 1
        fields = [e.field for e in errors]
        assert "step_type" in fields
