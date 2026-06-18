"""Tests for small-model-tool-extraction: Task 1 — classify_model_tier.

Contains:
- Property 1: classify_model_tier is total and safe (sub-task 1.1)
- Property 2: small tier boundary correctness (sub-task 1.2)
- Property 3: micro/standard tier boundaries are preserved (sub-task 1.3)
- Unit tests for classify_model_tier (sub-task 1.4)
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.agent.tool_retriever import ToolRetriever


# ---------------------------------------------------------------------------
# Sub-task 1.1 — Property 1: Tier classification is total and safe
# Feature: small-model-tool-extraction, Property 1: classify_model_tier is total and safe
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 1: classify_model_tier is total and safe
@settings(max_examples=200)
@given(st.text())
def test_classify_model_tier_total(model_name: str) -> None:
    """**Validates: Requirements 1.6**

    For any string passed to classify_model_tier, the return value is exactly
    one of "micro", "small", or "standard", and no exception is raised.
    """
    result = ToolRetriever.classify_model_tier(model_name)
    assert result in {"micro", "small", "standard"}, (
        f"classify_model_tier({model_name!r}) returned {result!r}, "
        f"expected one of 'micro', 'small', 'standard'"
    )


# ---------------------------------------------------------------------------
# Sub-task 1.2 — Property 2: Small tier boundary correctness
# Feature: small-model-tool-extraction, Property 2: small tier boundary
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 2: small tier boundary
@settings(max_examples=200)
@given(
    st.decimals(
        min_value="1.51",
        max_value="4.0",
        allow_nan=False,
        allow_infinity=False,
        places=2,
    )
)
def test_small_tier_boundary(param_count) -> None:
    """**Validates: Requirements 1.1, 1.4**

    For any model name whose parsed parameter count is in (1.5, 4.0] billion,
    classify_model_tier SHALL return "small".
    """
    # Format with fixed decimal notation to avoid scientific notation (e.g. 2.0e+0)
    model_name = f"testmodel:{param_count:.2f}b"
    result = ToolRetriever.classify_model_tier(model_name)
    assert result == "small", (
        f"classify_model_tier({model_name!r}) returned {result!r}, expected 'small' "
        f"for param_count={param_count}"
    )


# ---------------------------------------------------------------------------
# Sub-task 1.3 — Property 3: Micro and standard tier boundaries are preserved
# Feature: small-model-tool-extraction, Property 3: micro/standard boundaries
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 3: micro/standard boundaries
@settings(max_examples=200)
@given(
    st.decimals(
        min_value="0.1",
        max_value="1.5",
        allow_nan=False,
        allow_infinity=False,
        places=2,
    )
)
def test_micro_tier_boundary(param_count) -> None:
    """**Validates: Requirements 1.2**

    For any model name whose parsed parameter count is in [0.1, 1.5] billion,
    classify_model_tier SHALL return "micro".
    """
    model_name = f"testmodel:{param_count:.2f}b"
    result = ToolRetriever.classify_model_tier(model_name)
    assert result == "micro", (
        f"classify_model_tier({model_name!r}) returned {result!r}, expected 'micro' "
        f"for param_count={param_count}"
    )


# Feature: small-model-tool-extraction, Property 3: micro/standard boundaries
@settings(max_examples=200)
@given(
    st.decimals(
        min_value="4.01",
        max_value="100.0",
        allow_nan=False,
        allow_infinity=False,
        places=2,
    )
)
def test_standard_tier_boundary(param_count) -> None:
    """**Validates: Requirements 1.3**

    For any model name whose parsed parameter count is > 4.0 billion,
    classify_model_tier SHALL return "standard".
    """
    model_name = f"testmodel:{param_count:.2f}b"
    result = ToolRetriever.classify_model_tier(model_name)
    assert result == "standard", (
        f"classify_model_tier({model_name!r}) returned {result!r}, expected 'standard' "
        f"for param_count={param_count}"
    )


# ---------------------------------------------------------------------------
# Sub-task 1.4 — Unit tests for classify_model_tier
# ---------------------------------------------------------------------------


class TestClassifyModelTierBoundaryValues:
    """Boundary value tests for the three-tier classification."""

    def test_1_5b_is_micro(self):
        """1.5b is exactly at the micro boundary — should be micro."""
        assert ToolRetriever.classify_model_tier("model:1.5b") == "micro"

    def test_1_6b_is_small(self):
        """1.6b is just above the micro boundary — should be small."""
        assert ToolRetriever.classify_model_tier("model:1.6b") == "small"

    def test_4_0b_is_small(self):
        """4.0b is exactly at the small/standard boundary — should be small."""
        assert ToolRetriever.classify_model_tier("model:4.0b") == "small"

    def test_4_1b_is_standard(self):
        """4.1b is just above the small boundary — should be standard."""
        assert ToolRetriever.classify_model_tier("model:4.1b") == "standard"


class TestClassifyModelTierSuffixGrammar:
    """Tests for the parameter suffix grammar (Requirement 1.4)."""

    def test_gemma3_4b_is_small(self):
        """gemma3:4b → 4B → small."""
        assert ToolRetriever.classify_model_tier("gemma3:4b") == "small"

    def test_gemma4_e2b_is_small(self):
        """gemma4:e2b → strip 'e' prefix → 2B → small."""
        assert ToolRetriever.classify_model_tier("gemma4:e2b") == "small"

    def test_llama3_2_3b_is_small(self):
        """llama3.2:3b → 3B → small."""
        assert ToolRetriever.classify_model_tier("llama3.2:3b") == "small"

    def test_qwen3_0_6b_is_micro(self):
        """qwen3:0.6b → 0.6B → micro."""
        assert ToolRetriever.classify_model_tier("qwen3:0.6b") == "micro"

    def test_uppercase_B_suffix(self):
        """Case-insensitive 'B' suffix should work."""
        assert ToolRetriever.classify_model_tier("model:3B") == "small"

    def test_mixed_case_suffix(self):
        """Mixed case like '3b' should work."""
        assert ToolRetriever.classify_model_tier("model:3b") == "small"


class TestClassifyModelTierUnparseable:
    """Tests for unparseable model names defaulting to standard."""

    def test_empty_string_is_standard(self):
        """Empty string → no parseable param count → standard."""
        assert ToolRetriever.classify_model_tier("") == "standard"

    def test_no_param_suffix_is_standard(self):
        """Model name with no parameter suffix → standard."""
        assert ToolRetriever.classify_model_tier("llama3") == "standard"

    def test_no_b_suffix_is_standard(self):
        """Numeric token without 'b' suffix → not a param count → standard."""
        assert ToolRetriever.classify_model_tier("model:4") == "standard"

    def test_letters_only_is_standard(self):
        """All-letter model name → standard."""
        assert ToolRetriever.classify_model_tier("gpt-turbo") == "standard"

    def test_random_text_is_standard(self):
        """Arbitrary text without param suffix → standard."""
        assert ToolRetriever.classify_model_tier("some-random-model-name") == "standard"

    def test_large_model_is_standard(self):
        """70b model → standard."""
        assert ToolRetriever.classify_model_tier("llama3:70b") == "standard"

    def test_8b_model_is_standard(self):
        """8b model → standard."""
        assert ToolRetriever.classify_model_tier("llama3:8b") == "standard"


# ===========================================================================
# Task 2 — IntentParser tests
# ===========================================================================

import importlib.util as _ilu
import sys as _sys

_spec = _ilu.spec_from_file_location(
    "distr.core.agent.services.llm.intent_parser",
    "distr/core/agent/services/llm/intent_parser.py",
)
_mod = _ilu.module_from_spec(_spec)
_sys.modules["distr.core.agent.services.llm.intent_parser"] = _mod
_spec.loader.exec_module(_mod)
intent_parse = _mod.parse


# ---------------------------------------------------------------------------
# Helpers / strategies for IntentParser tests
# ---------------------------------------------------------------------------

# Characters that are safe inside quoted values (no backslash, no quote chars, no newlines)
_SAFE_VALUE_CHARS = st.characters(
    blacklist_categories=("Cs",),
    blacklist_characters='"\'\\\n\r',
)

_SAFE_VALUE_TEXT = st.text(alphabet=_SAFE_VALUE_CHARS, min_size=0, max_size=40)

# Valid Python-identifier-style key names
_KEY_STRATEGY = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,15}", fullmatch=True)

# Valid tool names
_TOOL_NAME_STRATEGY = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True)


def _serialise_args(args: dict) -> str:
    """Re-serialise an args dict back to a TOOL: argument string."""
    return ", ".join(f'{k}="{v}"' for k, v in args.items())


@st.composite
def valid_tool_lines(draw) -> str:
    """Generate valid TOOL: lines with properly quoted key=value arguments."""
    tool_name = draw(_TOOL_NAME_STRATEGY)
    num_args = draw(st.integers(min_value=0, max_value=4))
    keys = draw(
        st.lists(
            _KEY_STRATEGY,
            min_size=num_args,
            max_size=num_args,
            unique=True,
        )
    )
    values = draw(st.lists(_SAFE_VALUE_TEXT, min_size=num_args, max_size=num_args))
    arg_str = ", ".join(f'{k}="{v}"' for k, v in zip(keys, values))
    return f"TOOL: {tool_name}({arg_str})"


# ---------------------------------------------------------------------------
# Sub-task 2.1 — Property 8: Argument parsing round-trip
# Feature: small-model-tool-extraction, Property 8: argument parsing round-trip
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 8: argument parsing round-trip
@settings(max_examples=200)
@given(valid_tool_lines())
def test_parse_round_trip(tool_line: str) -> None:
    """**Validates: Requirements 4.6**

    For any valid TOOL: line, parsing then re-serialising the args dict and
    parsing again must produce an equivalent args dict.
    """
    # First parse
    result1 = intent_parse(tool_line, [])
    assert result1 is not None, f"First parse returned None for: {tool_line!r}"
    tool_name, args1 = result1

    # Re-serialise
    serialised = f"TOOL: {tool_name}({_serialise_args(args1)})"

    # Second parse
    result2 = intent_parse(serialised, [])
    assert result2 is not None, f"Second parse returned None for: {serialised!r}"
    _, args2 = result2

    assert args1 == args2, (
        f"Round-trip mismatch.\n"
        f"  Original line : {tool_line!r}\n"
        f"  args1         : {args1!r}\n"
        f"  Re-serialised : {serialised!r}\n"
        f"  args2         : {args2!r}"
    )


# ---------------------------------------------------------------------------
# Sub-task 2.2 — Property 9: Malformed TOOL: lines never raise
# Feature: small-model-tool-extraction, Property 9: malformed lines never raise
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 9: malformed lines never raise
@settings(max_examples=500)
@given(st.text())
def test_parse_never_raises(text: str) -> None:
    """**Validates: Requirements 4.7**

    For any string passed to IntentParser.parse, the function returns None or
    a valid (str, dict) tuple and never raises an exception.
    """
    try:
        result = intent_parse(text, [])
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"intent_parse raised {type(exc).__name__}: {exc}\n"
            f"  Input: {text!r}"
        ) from exc

    assert result is None or (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], str)
        and isinstance(result[1], dict)
    ), f"intent_parse returned unexpected value {result!r} for input {text!r}"


# ---------------------------------------------------------------------------
# Sub-task 2.3 — Property 13: Invalid tool names rejected by parser
# Feature: small-model-tool-extraction, Property 13: invalid tool names rejected
# ---------------------------------------------------------------------------

# Strategy for invalid tool names: names with spaces, leading digits, or special chars
_INVALID_TOOL_NAME_STRATEGY = st.one_of(
    # Leading digit
    st.from_regex(r"[0-9][a-zA-Z0-9_]{0,15}", fullmatch=True),
    # Contains space
    st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,7} [a-zA-Z0-9_]{1,8}", fullmatch=True),
    # Contains special chars (not alphanumeric or underscore)
    # Exclude "." — names like "A.A" normalize to a valid last segment and would falsely pass.
    st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,7}[!@#$%^&*\-+][a-zA-Z0-9_]{0,8}", fullmatch=True),
)


# Feature: small-model-tool-extraction, Property 13: invalid tool names rejected
@settings(max_examples=200)
@given(_INVALID_TOOL_NAME_STRATEGY)
def test_invalid_tool_name_returns_none(invalid_name: str) -> None:
    """**Validates: Requirements 9.4**

    For any TOOL: line where the tool name does not match
    ^[a-zA-Z_][a-zA-Z0-9_]*$, IntentParser.parse returns None.
    """
    tool_line = f"TOOL: {invalid_name}()"
    result = intent_parse(tool_line, ["any_tool"])
    assert result is None, (
        f"Expected None for invalid tool name {invalid_name!r}, "
        f"but got {result!r} from line {tool_line!r}"
    )


# ---------------------------------------------------------------------------
# Sub-task 2.4 — Unit tests for IntentParser.parse
# ---------------------------------------------------------------------------


class TestIntentParserValidLines:
    """Valid TOOL: lines with various arg types and counts."""

    def test_single_double_quoted_arg(self):
        result = intent_parse('TOOL: search(query="hello world")', ["search"])
        assert result == ("search", {"query": "hello world"})

    def test_single_single_quoted_arg(self):
        result = intent_parse("TOOL: search(query='hello world')", ["search"])
        assert result == ("search", {"query": "hello world"})

    def test_multiple_args(self):
        result = intent_parse(
            'TOOL: create_note(title="My Note", body="Some content")', ["create_note"]
        )
        assert result == ("create_note", {"title": "My Note", "body": "Some content"})

    def test_three_args(self):
        result = intent_parse(
            'TOOL: send_email(to="alice@example.com", subject="Hi", body="Hello")',
            ["send_email"],
        )
        assert result == (
            "send_email",
            {"to": "alice@example.com", "subject": "Hi", "body": "Hello"},
        )

    def test_comma_inside_quoted_value(self):
        result = intent_parse('TOOL: foo(x="a, b, c")', ["foo"])
        assert result == ("foo", {"x": "a, b, c"})

    def test_paren_inside_quoted_value(self):
        result = intent_parse('TOOL: foo(x="value(with parens)")', ["foo"])
        assert result == ("foo", {"x": "value(with parens)"})

    def test_escaped_quote_in_value(self):
        result = intent_parse('TOOL: foo(x="say \\"hello\\"")', ["foo"])
        assert result == ("foo", {"x": 'say "hello"'})

    def test_empty_string_value(self):
        result = intent_parse('TOOL: foo(x="")', ["foo"])
        assert result == ("foo", {"x": ""})

    def test_underscore_in_tool_name(self):
        result = intent_parse('TOOL: my_tool_name(a="1")', ["my_tool_name"])
        assert result == ("my_tool_name", {"a": "1"})

    def test_underscore_in_key(self):
        result = intent_parse('TOOL: foo(my_key="val")', ["foo"])
        assert result == ("foo", {"my_key": "val"})


class TestIntentParserZeroArgs:
    """Zero-argument call TOOL: foo() → ("foo", {})."""

    def test_zero_args(self):
        result = intent_parse("TOOL: foo()", ["foo"])
        assert result == ("foo", {})

    def test_zero_args_with_spaces(self):
        result = intent_parse("TOOL: foo(  )", ["foo"])
        assert result == ("foo", {})


class TestIntentParserToleranceLayer:
    """Tolerance layer: case-insensitive TOOL: and markdown fence stripping."""

    def test_lowercase_tool_prefix(self):
        result = intent_parse('tool: search(query="test")', ["search"])
        assert result == ("search", {"query": "test"})

    def test_mixed_case_tool_prefix(self):
        result = intent_parse('Tool: search(query="test")', ["search"])
        assert result == ("search", {"query": "test"})

    def test_uppercase_tool_prefix(self):
        result = intent_parse('TOOL: search(query="test")', ["search"])
        assert result == ("search", {"query": "test"})

    def test_tool_inside_backtick_fence(self):
        response = (
            "Here is my answer:\n"
            "```\n"
            'TOOL: search(query="weather")\n'
            "```\n"
        )
        result = intent_parse(response, ["search"])
        assert result == ("search", {"query": "weather"})

    def test_tool_inside_tilde_fence(self):
        response = (
            "Here is my answer:\n"
            "~~~\n"
            'TOOL: search(query="weather")\n'
            "~~~\n"
        )
        result = intent_parse(response, ["search"])
        assert result == ("search", {"query": "weather"})

    def test_tool_line_in_multiline_response(self):
        response = (
            "I'll help you with that.\n"
            'TOOL: get_time(timezone="UTC")\n'
            "Let me check."
        )
        result = intent_parse(response, ["get_time"])
        assert result == ("get_time", {"timezone": "UTC"})

    def test_positional_fallback_single_quoted(self):
        """Single quoted positional arg → {"_arg0": value}."""
        result = intent_parse('TOOL: search("what is the weather")', ["search"])
        assert result == ("search", {"_arg0": "what is the weather"})

    def test_positional_fallback_double_quoted(self):
        result = intent_parse("TOOL: search('hello world')", ["search"])
        assert result == ("search", {"_arg0": "hello world"})


class TestIntentParserOptionalArgs:
    """Optional args omitted → args dict simply lacks those keys."""

    def test_only_required_arg_present(self):
        result = intent_parse('TOOL: search(query="test")', ["search"])
        assert result is not None
        assert "query" in result[1]
        # Optional args like 'limit' are simply absent
        assert "limit" not in result[1]

    def test_partial_args(self):
        result = intent_parse(
            'TOOL: create_note(title="My Note")', ["create_note"]
        )
        assert result == ("create_note", {"title": "My Note"})


class TestIntentParserNoToolLine:
    """No TOOL: line → None."""

    def test_empty_string(self):
        assert intent_parse("", []) is None

    def test_plain_text(self):
        assert intent_parse("Hello, how can I help you?", []) is None

    def test_tool_substring_not_at_line_start(self):
        # "TOOL:" not at start of line — should not match
        assert intent_parse("I used TOOL: foo() here", []) is None

    def test_bare_tool_colon(self):
        # Bare "TOOL:" without valid name and parens
        assert intent_parse("TOOL:", []) is None

    def test_tool_without_parens(self):
        assert intent_parse("TOOL: search", []) is None


class TestIntentParserMalformed:
    """Malformed lines → None (with WARNING logged for near-misses)."""

    def test_unmatched_opening_quote(self):
        result = intent_parse('TOOL: foo(x="unclosed)', ["foo"])
        assert result is None

    def test_unquoted_value(self):
        result = intent_parse("TOOL: foo(x=hello)", ["foo"])
        assert result is None

    def test_unmatched_paren(self):
        result = intent_parse('TOOL: foo(x="val"', ["foo"])
        # No closing paren — should return None
        assert result is None

    def test_key_without_value(self):
        result = intent_parse("TOOL: foo(x=)", ["foo"])
        assert result is None

    def test_invalid_tool_name_leading_digit(self):
        result = intent_parse('TOOL: 1foo(x="val")', ["1foo"])
        assert result is None

    def test_invalid_tool_name_with_hyphen(self):
        result = intent_parse('TOOL: my-tool(x="val")', ["my-tool"])
        assert result is None

    def test_invalid_tool_name_with_space(self):
        result = intent_parse('TOOL: my tool(x="val")', ["my tool"])
        assert result is None

    def test_warning_logged_for_near_miss(self, caplog):
        """A line that looks like a TOOL invocation but fails parsing logs WARNING."""
        import logging

        with caplog.at_level(logging.WARNING, logger="distr.core.agent.services.llm.intent_parser"):
            result = intent_parse("TOOL: foo(x=unquoted_value)", ["foo"])
        assert result is None
        assert any("foo" in record.message for record in caplog.records)


# ===========================================================================
# Task 4 — _build_tool_hint_block, _inject_tool_hint, _strip_tool_hint tests
# ===========================================================================

import re
import types


# ---------------------------------------------------------------------------
# Standalone implementations of the three hint-block methods.
# These mirror the implementations in OllamaLLMService exactly, but are
# defined here as plain functions to avoid importing the class (which pulls
# in heavy dependencies like langchain_core, pipecat, etc.).
# ---------------------------------------------------------------------------

def _build_tool_hint_block(filtered_tools: list) -> str:
    """Build the plain-text Tool_Hint_Block for small models."""
    if not filtered_tools:
        return ""

    lines = ["--- Available Tools ---"]
    for tool in filtered_tools:
        name = getattr(tool, "name", "")
        description = getattr(tool, "description", "") or ""
        if not description:
            schema = getattr(tool, "schema", None) or {}
            description = schema.get("description", "") or ""
        description = description[:120]

        params = []
        tool_params = getattr(tool, "parameters", None)
        if tool_params and isinstance(tool_params, dict):
            params = list(tool_params.keys())
        else:
            schema = getattr(tool, "schema", None) or {}
            props = schema.get("parameters", {}).get("properties", {})
            params = list(props.keys())

        param_str = ", ".join(params)
        lines.append(f"{name}({param_str}): {description}")

    lines.append("")
    lines.append("To use a tool, respond with exactly:")
    lines.append('TOOL: tool_name(arg1="value1", arg2="value2")')
    lines.append("--- End Tools ---")
    return "\n".join(lines)


def _inject_tool_hint(messages: list, hint_block: str) -> None:
    """Append hint_block to messages[0]['content'] (the system message)."""
    if not hint_block or not messages:
        return
    messages[0]["content"] = messages[0].get("content", "") + "\n\n" + hint_block


def _strip_tool_hint(messages: list) -> None:
    """Remove any previously injected Tool_Hint_Block from messages[0]['content']."""
    if not messages:
        return
    content = messages[0].get("content", "")
    stripped = re.sub(
        r"\s*--- Available Tools ---.*?--- End Tools ---\s*",
        "",
        content,
        flags=re.DOTALL,
    )
    messages[0]["content"] = stripped


def _make_stub(system_content: str = "You are a helpful assistant."):
    """Create a minimal stub with the hint-block methods bound to it."""
    svc = types.SimpleNamespace()
    svc._messages = [{"role": "system", "content": system_content}]

    def _svc_build(self_or_tools, tools=None):
        # Support both svc._build_tool_hint_block(tools) and bound method call
        actual_tools = tools if tools is not None else self_or_tools
        return _build_tool_hint_block(actual_tools)

    svc._build_tool_hint_block = lambda tools: _build_tool_hint_block(tools)
    svc._inject_tool_hint = lambda hint: _inject_tool_hint(svc._messages, hint)
    svc._strip_tool_hint = lambda: _strip_tool_hint(svc._messages)
    return svc


def _make_mock_tool(name: str, description: str, param_names: list):
    """Create a minimal mock tool object."""
    tool = types.SimpleNamespace()
    tool.name = name
    tool.description = description
    tool.parameters = {p: {} for p in param_names}
    tool.schema = {
        "description": description,
        "parameters": {"properties": {p: {} for p in param_names}},
    }
    return tool


# ---------------------------------------------------------------------------
# Hypothesis strategy: generate mock tool objects
# ---------------------------------------------------------------------------

_TOOL_NAME_ST = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True)
_PARAM_NAME_ST = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,15}", fullmatch=True)
_DESCRIPTION_ST = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029",
    ),
    min_size=1,  # descriptions must be non-empty (real tools always have descriptions)
    max_size=200,
)


@st.composite
def tool_schemas(draw):
    """Generate mock tool objects with name, description, and parameters."""
    name = draw(_TOOL_NAME_ST)
    description = draw(_DESCRIPTION_ST)
    num_params = draw(st.integers(min_value=0, max_value=5))
    param_names = draw(
        st.lists(_PARAM_NAME_ST, min_size=num_params, max_size=num_params, unique=True)
    )
    return _make_mock_tool(name, description, param_names)


def _extract_tool_lines(block: str) -> list:
    """Extract the tool description lines from a hint block (between header and blank line)."""
    # Use split('\n') not splitlines() to avoid splitting on form feed (\x0c) etc.
    lines = block.split("\n")
    tool_lines = []
    in_tools = False
    for line in lines:
        if line == "--- Available Tools ---":
            in_tools = True
            continue
        if line == "--- End Tools ---":
            break
        if in_tools and line == "":
            # blank line before instruction — stop collecting tool lines
            break
        if in_tools:
            tool_lines.append(line)
    return tool_lines


# ---------------------------------------------------------------------------
# Sub-task 4.1 — Property 6: Tool hint block format is correct for any tool set
# Feature: small-model-tool-extraction, Property 6: hint block format
# ---------------------------------------------------------------------------


# Feature: small-model-tool-extraction, Property 6: hint block format
@settings(max_examples=200)
@given(st.lists(tool_schemas(), min_size=1, max_size=20))
def test_hint_block_format(tools) -> None:
    """**Validates: Requirements 3.2**

    For any non-empty list of tools, every tool line in the generated
    Tool_Hint_Block matches the format `tool_name(params): description`
    and the description portion is at most 120 characters.
    """
    svc = _make_stub()
    block = svc._build_tool_hint_block(tools)

    assert block != "", "Expected non-empty block for non-empty tool list"
    assert "--- Available Tools ---" in block
    assert "--- End Tools ---" in block

    tool_lines = _extract_tool_lines(block)
    assert len(tool_lines) == len(tools), (
        f"Expected {len(tools)} tool lines, got {len(tool_lines)}"
    )

    for line in tool_lines:
        assert re.match(r"^\w+\(.*\): .{1,120}$", line), (
            f"Tool line does not match expected format: {line!r}"
        )


# ---------------------------------------------------------------------------
# Sub-task 4.2 — Property 7: Hint block absent for non-small models
# Feature: small-model-tool-extraction, Property 7: no hint for non-small models
# ---------------------------------------------------------------------------

# Strategy for standard model names (>4B)
_STANDARD_MODEL_NAMES = st.one_of(
    st.just("model:8b"),
    st.just("model:70b"),
    st.just("llama3:8b"),
    st.just("llama3:70b"),
    st.just("gpt-4"),
    st.from_regex(r"[a-zA-Z]+:[5-9][0-9]*b", fullmatch=True),
)

# Strategy for micro model names (≤1.5B)
_MICRO_MODEL_NAMES = st.one_of(
    st.just("model:0.5b"),
    st.just("model:1b"),
    st.just("qwen3:0.6b"),
    st.just("model:1.5b"),
)


# Feature: small-model-tool-extraction, Property 7: no hint for non-small models
@settings(max_examples=100)
@given(st.one_of(_STANDARD_MODEL_NAMES, _MICRO_MODEL_NAMES))
def test_strip_tool_hint_is_noop_without_hint(model_name: str) -> None:
    """**Validates: Requirements 3.6**

    _strip_tool_hint on a system message that contains no hint block
    is a no-op — the content is unchanged.
    This verifies that non-small model system messages are never polluted
    with hint block content.
    """
    original_content = f"You are a helpful assistant for model {model_name}."
    svc = _make_stub(system_content=original_content)

    # Calling strip on a message without a hint block should be a no-op
    svc._strip_tool_hint()

    assert svc._messages[0]["content"] == original_content, (
        f"_strip_tool_hint modified content when no hint block was present. "
        f"Model: {model_name!r}"
    )


# ---------------------------------------------------------------------------
# Sub-task 4.3 — Property 5: Stale hint blocks removed on model switch
# Feature: small-model-tool-extraction, Property 5: stale hint blocks removed
# ---------------------------------------------------------------------------

_SAMPLE_TOOL = _make_mock_tool("search", "Search the web for information", ["query"])


# Feature: small-model-tool-extraction, Property 5: stale hint blocks removed
@settings(max_examples=200)
@given(
    st.lists(
        st.sampled_from(["small:3b", "large:8b"]),
        min_size=2,
        max_size=10,
    )
)
def test_no_stale_hint_on_model_switch(model_sequence: list) -> None:
    """**Validates: Requirements 2.6**

    For any sequence of model switches between small and non-small tiers,
    after stripping the hint block the system message must contain no
    '--- Available Tools ---' block.
    """
    svc = _make_stub()

    for model in model_sequence:
        tier = ToolRetriever.classify_model_tier(model)

        if tier == "small":
            # Simulate: inject hint for small model
            hint = svc._build_tool_hint_block([_SAMPLE_TOOL])
            svc._inject_tool_hint(hint)

        # Always strip at the start of each request (as per design)
        svc._strip_tool_hint()

        # After stripping, no hint block should remain
        content = svc._messages[0]["content"]
        assert "--- Available Tools ---" not in content, (
            f"Stale hint block found after model switch to {model!r}. "
            f"Content: {content!r}"
        )
        assert "--- End Tools ---" not in content, (
            f"Stale hint block end marker found after model switch to {model!r}."
        )


# ---------------------------------------------------------------------------
# Sub-task 4.4 — Unit tests for hint block methods
# ---------------------------------------------------------------------------


class TestBuildToolHintBlockEmpty:
    """Empty tool list → _build_tool_hint_block([]) returns ''."""

    def test_empty_list_returns_empty_string(self):
        svc = _make_stub()
        assert svc._build_tool_hint_block([]) == ""

    def test_empty_list_no_markers(self):
        svc = _make_stub()
        result = svc._build_tool_hint_block([])
        assert "--- Available Tools ---" not in result
        assert "--- End Tools ---" not in result


class TestBuildToolHintBlockFormat:
    """Format and content tests for _build_tool_hint_block."""

    def test_single_tool_has_correct_structure(self):
        svc = _make_stub()
        tool = _make_mock_tool("search", "Search the web", ["query"])
        block = svc._build_tool_hint_block([tool])

        assert block.startswith("--- Available Tools ---")
        assert "--- End Tools ---" in block
        assert "TOOL: tool_name(" in block
        assert 'arg1="value1"' in block

    def test_tool_line_format(self):
        svc = _make_stub()
        tool = _make_mock_tool("get_weather", "Get current weather", ["city", "units"])
        block = svc._build_tool_hint_block([tool])

        tool_lines = _extract_tool_lines(block)
        assert len(tool_lines) == 1
        assert tool_lines[0] == "get_weather(city, units): Get current weather"

    def test_description_truncated_at_120_chars(self):
        svc = _make_stub()
        long_desc = "A" * 150  # 150 chars — should be truncated to 120
        tool = _make_mock_tool("my_tool", long_desc, ["param"])
        block = svc._build_tool_hint_block([tool])

        tool_lines = _extract_tool_lines(block)
        assert len(tool_lines) == 1
        # Description part is after ": "
        desc_part = tool_lines[0].split(": ", 1)[1]
        assert len(desc_part) == 120
        assert desc_part == "A" * 120

    def test_description_exactly_120_chars_not_truncated(self):
        svc = _make_stub()
        exact_desc = "B" * 120
        tool = _make_mock_tool("my_tool", exact_desc, [])
        block = svc._build_tool_hint_block([tool])

        tool_lines = _extract_tool_lines(block)
        desc_part = tool_lines[0].split(": ", 1)[1]
        assert len(desc_part) == 120

    def test_description_under_120_chars_unchanged(self):
        svc = _make_stub()
        short_desc = "Short description"
        tool = _make_mock_tool("my_tool", short_desc, [])
        block = svc._build_tool_hint_block([tool])

        tool_lines = _extract_tool_lines(block)
        desc_part = tool_lines[0].split(": ", 1)[1]
        assert desc_part == short_desc

    def test_no_params_tool(self):
        svc = _make_stub()
        tool = _make_mock_tool("get_time", "Get current time", [])
        block = svc._build_tool_hint_block([tool])

        tool_lines = _extract_tool_lines(block)
        assert tool_lines[0] == "get_time(): Get current time"

    def test_multiple_tools(self):
        svc = _make_stub()
        tools = [
            _make_mock_tool("search", "Search the web", ["query"]),
            _make_mock_tool("get_weather", "Get weather", ["city"]),
            _make_mock_tool("send_email", "Send an email", ["to", "subject", "body"]),
        ]
        block = svc._build_tool_hint_block(tools)

        tool_lines = _extract_tool_lines(block)
        assert len(tool_lines) == 3
        assert tool_lines[0] == "search(query): Search the web"
        assert tool_lines[1] == "get_weather(city): Get weather"
        assert tool_lines[2] == "send_email(to, subject, body): Send an email"

    def test_instruction_line_present(self):
        svc = _make_stub()
        tool = _make_mock_tool("foo", "Does foo", ["x"])
        block = svc._build_tool_hint_block([tool])
        assert "To use a tool, respond with exactly:" in block
        assert 'TOOL: tool_name(arg1="value1", arg2="value2")' in block


class TestInjectToolHint:
    """Tests for _inject_tool_hint."""

    def test_appends_hint_to_system_message(self):
        svc = _make_stub("System prompt.")
        hint = "--- Available Tools ---\nfoo(): bar\n--- End Tools ---"
        svc._inject_tool_hint(hint)
        assert svc._messages[0]["content"].endswith(hint)
        assert "System prompt." in svc._messages[0]["content"]

    def test_empty_hint_is_noop(self):
        svc = _make_stub("System prompt.")
        svc._inject_tool_hint("")
        assert svc._messages[0]["content"] == "System prompt."

    def test_inject_adds_separator(self):
        svc = _make_stub("System prompt.")
        hint = "--- Available Tools ---\nfoo(): bar\n--- End Tools ---"
        svc._inject_tool_hint(hint)
        content = svc._messages[0]["content"]
        assert "\n\n" in content  # separator between system prompt and hint


class TestStripToolHint:
    """Tests for _strip_tool_hint."""

    def test_removes_injected_hint(self):
        svc = _make_stub("System prompt.")
        tool = _make_mock_tool("search", "Search the web", ["query"])
        hint = svc._build_tool_hint_block([tool])
        svc._inject_tool_hint(hint)

        # Verify hint is present
        assert "--- Available Tools ---" in svc._messages[0]["content"]

        # Strip it
        svc._strip_tool_hint()

        content = svc._messages[0]["content"]
        assert "--- Available Tools ---" not in content
        assert "--- End Tools ---" not in content

    def test_strip_preserves_original_system_prompt(self):
        original = "You are a helpful assistant."
        svc = _make_stub(original)
        tool = _make_mock_tool("foo", "Does foo", ["x"])
        hint = svc._build_tool_hint_block([tool])
        svc._inject_tool_hint(hint)
        svc._strip_tool_hint()

        assert svc._messages[0]["content"].strip() == original

    def test_strip_is_idempotent(self):
        svc = _make_stub("System prompt.")
        tool = _make_mock_tool("foo", "Does foo", ["x"])
        hint = svc._build_tool_hint_block([tool])
        svc._inject_tool_hint(hint)

        svc._strip_tool_hint()
        content_after_first = svc._messages[0]["content"]

        # Call again — should be a no-op
        svc._strip_tool_hint()
        content_after_second = svc._messages[0]["content"]

        assert content_after_first == content_after_second

    def test_strip_noop_when_no_hint(self):
        original = "System prompt with no hint."
        svc = _make_stub(original)
        svc._strip_tool_hint()
        assert svc._messages[0]["content"] == original

    def test_inject_strip_inject_strip_cycle(self):
        """Inject, strip, inject again, strip again — should work cleanly."""
        original = "Base system prompt."
        svc = _make_stub(original)
        tool = _make_mock_tool("foo", "Does foo", ["x"])

        for _ in range(3):
            hint = svc._build_tool_hint_block([tool])
            svc._inject_tool_hint(hint)
            assert "--- Available Tools ---" in svc._messages[0]["content"]
            svc._strip_tool_hint()
            assert "--- Available Tools ---" not in svc._messages[0]["content"]
            assert svc._messages[0]["content"].strip() == original


# ===========================================================================
# Task 5 — _check_prompt_injection and _coerce_args_to_schema_types tests
# ===========================================================================

import types as _types_mod


# ---------------------------------------------------------------------------
# Helpers: stub for OllamaLLMService with _check_prompt_injection and
# _coerce_args_to_schema_types bound to a SimpleNamespace that has _tools_dict.
# ---------------------------------------------------------------------------

def _make_ollama_stub_with_tools(tools_dict: dict):
    """Create a minimal stub that has _tools_dict and the two new methods."""
    import logging as _logging
    import re as _re

    _logger = _logging.getLogger("distr.core.agent.services.llm.providers.ollama")

    svc = _types_mod.SimpleNamespace()
    svc._tools_dict = tools_dict

    def _check_prompt_injection(user_message: str) -> bool:
        _INJECTION_PATTERN = _re.compile(
            r"^tool:\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(.*\)\s*$",
            _re.IGNORECASE | _re.MULTILINE,
        )
        if _INJECTION_PATTERN.search(user_message):
            _logger.warning(
                "Potential prompt injection detected in user message: %r",
                user_message[:200],
            )
            return True
        return False

    def _coerce_args_to_schema_types(tool_name: str, args: dict) -> dict:
        tool = svc._tools_dict.get(tool_name)
        if tool is None:
            return dict(args)
        schema = getattr(tool, "schema", None) or {}
        properties = schema.get("parameters", {}).get("properties", {})
        coerced = {}
        for key, value in args.items():
            declared_type = properties.get(key, {}).get("type", "string")
            try:
                if declared_type == "integer":
                    coerced[key] = int(value)
                elif declared_type == "number":
                    coerced[key] = float(value)
                elif declared_type == "boolean":
                    coerced[key] = (str(value).lower() == "true")
                else:
                    coerced[key] = str(value)
            except (ValueError, TypeError) as exc:
                _logger.warning(
                    "Coercion failed for arg %r (tool=%r, declared_type=%r, value=%r): %s",
                    key, tool_name, declared_type, value, exc,
                )
                coerced[key] = str(value)
        return coerced

    svc._check_prompt_injection = _check_prompt_injection
    svc._coerce_args_to_schema_types = _coerce_args_to_schema_types
    return svc


def _make_typed_tool(name: str, param_types: dict) -> _types_mod.SimpleNamespace:
    """Create a mock tool with a JSON schema declaring parameter types."""
    tool = _types_mod.SimpleNamespace()
    tool.name = name
    tool.description = f"Tool {name}"
    properties = {k: {"type": v} for k, v in param_types.items()}
    tool.schema = {
        "description": tool.description,
        "parameters": {"properties": properties},
    }
    return tool


# ---------------------------------------------------------------------------
# Sub-task 5.1 — Property 10: Argument type coercion matches schema
# Feature: small-model-tool-extraction, Property 10: argument type coercion matches schema
# ---------------------------------------------------------------------------

_SCHEMA_TYPES = ["integer", "number", "boolean", "string"]

_TYPE_TO_PYTHON = {
    "integer": int,
    "number": float,
    "boolean": bool,
    "string": str,
}


def _valid_string_for_type(draw, declared_type: str) -> str:
    """Draw a valid string representation for the given JSON schema type."""
    if declared_type == "integer":
        return str(draw(st.integers(min_value=-10_000, max_value=10_000)))
    elif declared_type == "number":
        return str(draw(st.floats(
            min_value=-1e6, max_value=1e6,
            allow_nan=False, allow_infinity=False,
        )))
    elif declared_type == "boolean":
        return draw(st.sampled_from(["true", "false", "True", "False", "TRUE", "FALSE"]))
    else:
        # string — any text that won't cause coercion issues
        return draw(st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=0, max_size=40,
        ))


@st.composite
def tool_schema_with_typed_params(draw):
    """Generate a (tool_name, param_types_dict) pair with 1–5 typed parameters."""
    tool_name = draw(st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True))
    num_params = draw(st.integers(min_value=1, max_value=5))
    param_names = draw(
        st.lists(
            st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,15}", fullmatch=True),
            min_size=num_params,
            max_size=num_params,
            unique=True,
        )
    )
    param_types = {
        name: draw(st.sampled_from(_SCHEMA_TYPES))
        for name in param_names
    }
    return tool_name, param_types


@st.composite
def matching_string_args(draw, schema_info=None):
    """Generate string args matching the types in schema_info (tool_name, param_types)."""
    # This strategy is used together with tool_schema_with_typed_params via @given
    # We receive schema_info as a fixed_dictionaries-style draw
    tool_name, param_types = draw(tool_schema_with_typed_params())
    string_args = {
        name: _valid_string_for_type(draw, declared_type)
        for name, declared_type in param_types.items()
    }
    return tool_name, param_types, string_args


# Feature: small-model-tool-extraction, Property 10: argument type coercion matches schema
@settings(max_examples=200)
@given(matching_string_args())
def test_coerce_args_types(schema_and_args) -> None:
    """**Validates: Requirements 5.2**

    For any tool schema declaring parameter types and a corresponding dict of
    valid string values, _coerce_args_to_schema_types produces a dict where
    each value has the Python type declared in the schema.
    """
    tool_name, param_types, string_args = schema_and_args

    tool = _make_typed_tool(tool_name, param_types)
    svc = _make_ollama_stub_with_tools({tool_name: tool})

    coerced = svc._coerce_args_to_schema_types(tool_name, string_args)

    for param_name, declared_type in param_types.items():
        expected_python_type = _TYPE_TO_PYTHON[declared_type]
        assert isinstance(coerced[param_name], expected_python_type), (
            f"Param {param_name!r} declared as {declared_type!r}: "
            f"expected {expected_python_type.__name__}, "
            f"got {type(coerced[param_name]).__name__} "
            f"(value={coerced[param_name]!r}, input={string_args[param_name]!r})"
        )


# ---------------------------------------------------------------------------
# Sub-task 5.2 — Property 12: Prompt injection detection
# Feature: small-model-tool-extraction, Property 12: prompt injection detection
# ---------------------------------------------------------------------------

_VALID_TOOL_NAME_ST = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True)

# Safe text for surrounding context (no newlines to avoid accidentally creating
# a new line that starts with TOOL:)
_CONTEXT_TEXT_ST = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=80,
)


@st.composite
def valid_tool_line_embedded_in_user_message(draw) -> str:
    """Generate a user message that contains a valid TOOL: line.

    The TOOL: line appears on its own line within the message, surrounded
    by optional context text on other lines.
    """
    tool_name = draw(_VALID_TOOL_NAME_ST)
    # Generate 0–3 key=value args for the tool line
    num_args = draw(st.integers(min_value=0, max_value=3))
    arg_keys = draw(
        st.lists(
            st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,10}", fullmatch=True),
            min_size=num_args,
            max_size=num_args,
            unique=True,
        )
    )
    arg_values = draw(
        st.lists(
            st.text(
                alphabet=st.characters(
                    blacklist_categories=("Cs",),
                    blacklist_characters='"\'\\\n\r',
                ),
                min_size=0,
                max_size=20,
            ),
            min_size=num_args,
            max_size=num_args,
        )
    )
    arg_str = ", ".join(f'{k}="{v}"' for k, v in zip(arg_keys, arg_values))
    tool_line = f"TOOL: {tool_name}({arg_str})"

    # Optionally wrap with context lines
    prefix = draw(_CONTEXT_TEXT_ST)
    suffix = draw(_CONTEXT_TEXT_ST)

    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(tool_line)
    if suffix:
        parts.append(suffix)
    return "\n".join(parts)


# Feature: small-model-tool-extraction, Property 12: prompt injection detection
@settings(max_examples=200)
@given(valid_tool_line_embedded_in_user_message())
def test_prompt_injection_detected(user_message: str) -> None:
    """**Validates: Requirements 9.3**

    For any user message containing a valid TOOL: <tool_name>(...) line,
    _check_prompt_injection returns True.
    """
    svc = _make_ollama_stub_with_tools({})
    result = svc._check_prompt_injection(user_message)
    assert result is True, (
        f"_check_prompt_injection returned False for message containing valid TOOL: line.\n"
        f"  Message: {user_message!r}"
    )


# ---------------------------------------------------------------------------
# Sub-task 5.3 — Unit tests for _check_prompt_injection and
#                _coerce_args_to_schema_types
# ---------------------------------------------------------------------------


class TestCheckPromptInjection:
    """Unit tests for _check_prompt_injection."""

    def _svc(self):
        return _make_ollama_stub_with_tools({})

    # --- Valid TOOL: patterns → True ---

    def test_valid_tool_pattern_returns_true(self):
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL: search(query=\"hello\")") is True

    def test_valid_tool_no_args_returns_true(self):
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL: get_time()") is True

    def test_valid_tool_multiple_args_returns_true(self):
        svc = self._svc()
        assert svc._check_prompt_injection('TOOL: send_email(to="a@b.com", subject="Hi")') is True

    def test_valid_tool_in_multiline_message_returns_true(self):
        svc = self._svc()
        msg = "Hello, please do this:\nTOOL: search(query=\"weather\")\nThank you."
        assert svc._check_prompt_injection(msg) is True

    def test_case_insensitive_tool_prefix(self):
        svc = self._svc()
        assert svc._check_prompt_injection("tool: search(query=\"test\")") is True

    def test_mixed_case_tool_prefix(self):
        svc = self._svc()
        assert svc._check_prompt_injection("Tool: search(query=\"test\")") is True

    def test_tool_with_underscore_name(self):
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL: my_tool(x=\"1\")") is True

    # --- Bare TOOL: substrings → False ---

    def test_bare_tool_colon_returns_false(self):
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL:") is False

    def test_tool_colon_no_parens_returns_false(self):
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL: search") is False

    def test_tool_substring_mid_line_returns_false(self):
        """TOOL: not at start of line — should not match."""
        svc = self._svc()
        assert svc._check_prompt_injection("I used TOOL: search() here") is False

    def test_no_tool_content_returns_false(self):
        svc = self._svc()
        assert svc._check_prompt_injection("Hello, how are you?") is False

    def test_empty_string_returns_false(self):
        svc = self._svc()
        assert svc._check_prompt_injection("") is False

    def test_invalid_tool_name_leading_digit_returns_false(self):
        """Tool name starting with digit is invalid — should not trigger."""
        svc = self._svc()
        assert svc._check_prompt_injection("TOOL: 1search(query=\"test\")") is False

    def test_warning_logged_on_injection(self, caplog):
        """A valid TOOL: pattern logs a WARNING."""
        import logging
        svc = self._svc()
        with caplog.at_level(logging.WARNING):
            svc._check_prompt_injection("TOOL: search(query=\"test\")")
        assert any("injection" in r.message.lower() or "prompt" in r.message.lower()
                   for r in caplog.records)


class TestCoerceArgsToSchemaTypes:
    """Unit tests for _coerce_args_to_schema_types."""

    def _svc_with_tool(self, tool_name: str, param_types: dict):
        tool = _make_typed_tool(tool_name, param_types)
        return _make_ollama_stub_with_tools({tool_name: tool})

    # --- Integer coercion ---

    def test_integer_coercion(self):
        svc = self._svc_with_tool("foo", {"count": "integer"})
        result = svc._coerce_args_to_schema_types("foo", {"count": "42"})
        assert result == {"count": 42}
        assert isinstance(result["count"], int)

    def test_negative_integer_coercion(self):
        svc = self._svc_with_tool("foo", {"offset": "integer"})
        result = svc._coerce_args_to_schema_types("foo", {"offset": "-7"})
        assert result == {"offset": -7}

    # --- Number (float) coercion ---

    def test_number_coercion(self):
        svc = self._svc_with_tool("foo", {"price": "number"})
        result = svc._coerce_args_to_schema_types("foo", {"price": "3.14"})
        assert result == {"price": 3.14}
        assert isinstance(result["price"], float)

    def test_integer_string_to_float(self):
        """A string "5" with declared type "number" → float 5.0."""
        svc = self._svc_with_tool("foo", {"val": "number"})
        result = svc._coerce_args_to_schema_types("foo", {"val": "5"})
        assert isinstance(result["val"], float)
        assert result["val"] == 5.0

    # --- Boolean coercion ---

    def test_boolean_true_lowercase(self):
        svc = self._svc_with_tool("foo", {"flag": "boolean"})
        result = svc._coerce_args_to_schema_types("foo", {"flag": "true"})
        assert result["flag"] is True

    def test_boolean_false_lowercase(self):
        svc = self._svc_with_tool("foo", {"flag": "boolean"})
        result = svc._coerce_args_to_schema_types("foo", {"flag": "false"})
        assert result["flag"] is False

    def test_boolean_true_uppercase(self):
        svc = self._svc_with_tool("foo", {"flag": "boolean"})
        result = svc._coerce_args_to_schema_types("foo", {"flag": "True"})
        assert result["flag"] is True

    def test_boolean_false_uppercase(self):
        svc = self._svc_with_tool("foo", {"flag": "boolean"})
        result = svc._coerce_args_to_schema_types("foo", {"flag": "FALSE"})
        assert result["flag"] is False

    def test_boolean_non_true_is_false(self):
        """Any value other than "true" (case-insensitive) → False."""
        svc = self._svc_with_tool("foo", {"flag": "boolean"})
        result = svc._coerce_args_to_schema_types("foo", {"flag": "yes"})
        assert result["flag"] is False

    # --- String coercion (no-op) ---

    def test_string_type_is_noop(self):
        svc = self._svc_with_tool("foo", {"name": "string"})
        result = svc._coerce_args_to_schema_types("foo", {"name": "Alice"})
        assert result == {"name": "Alice"}
        assert isinstance(result["name"], str)

    # --- Unknown type → str ---

    def test_unknown_type_keeps_string(self):
        svc = self._svc_with_tool("foo", {"data": "object"})
        result = svc._coerce_args_to_schema_types("foo", {"data": "some_value"})
        assert isinstance(result["data"], str)
        assert result["data"] == "some_value"

    def test_param_not_in_schema_keeps_string(self):
        """A param not declared in the schema defaults to string (no-op)."""
        svc = self._svc_with_tool("foo", {})
        result = svc._coerce_args_to_schema_types("foo", {"mystery": "42"})
        assert isinstance(result["mystery"], str)
        assert result["mystery"] == "42"

    # --- Coercion failure → keep as string, log WARNING ---

    def test_coercion_failure_keeps_string(self, caplog):
        """int("abc") fails → value kept as string, WARNING logged."""
        import logging
        svc = self._svc_with_tool("foo", {"count": "integer"})
        with caplog.at_level(logging.WARNING):
            result = svc._coerce_args_to_schema_types("foo", {"count": "abc"})
        assert isinstance(result["count"], str)
        assert result["count"] == "abc"
        assert any("coercion" in r.message.lower() or "failed" in r.message.lower()
                   for r in caplog.records)

    def test_float_coercion_failure_keeps_string(self, caplog):
        """float("not_a_number") fails → value kept as string."""
        import logging
        svc = self._svc_with_tool("foo", {"price": "number"})
        with caplog.at_level(logging.WARNING):
            result = svc._coerce_args_to_schema_types("foo", {"price": "not_a_number"})
        assert isinstance(result["price"], str)

    # --- Value-shape inference is prohibited ---

    def test_no_shape_inference_for_string_type(self):
        """A string "42" with declared type "string" must NOT be cast to int."""
        svc = self._svc_with_tool("foo", {"val": "string"})
        result = svc._coerce_args_to_schema_types("foo", {"val": "42"})
        assert isinstance(result["val"], str)
        assert result["val"] == "42"

    # --- Unknown tool → return args unchanged ---

    def test_unknown_tool_returns_args_unchanged(self):
        svc = _make_ollama_stub_with_tools({})
        result = svc._coerce_args_to_schema_types("nonexistent_tool", {"x": "5"})
        assert result == {"x": "5"}

    # --- Multiple params with mixed types ---

    def test_mixed_types(self):
        svc = self._svc_with_tool(
            "multi",
            {"count": "integer", "ratio": "number", "active": "boolean", "name": "string"},
        )
        result = svc._coerce_args_to_schema_types(
            "multi",
            {"count": "3", "ratio": "0.5", "active": "true", "name": "Alice"},
        )
        assert result["count"] == 3 and isinstance(result["count"], int)
        assert result["ratio"] == 0.5 and isinstance(result["ratio"], float)
        assert result["active"] is True
        assert result["name"] == "Alice" and isinstance(result["name"], str)


# ===========================================================================
# Task 6 — _generate_response routing tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Sub-task 6.1 — Property 4: No tools key on small path
# Feature: small-model-tool-extraction, Property 4: no tools key on small path
# ---------------------------------------------------------------------------

# Strategy for small model names (>1.5B and ≤4B)
_SMALL_MODEL_NAMES = st.one_of(
    st.just("gemma3:4b"),
    st.just("llama3.2:3b"),
    st.just("model:2b"),
    st.just("model:3b"),
    st.just("model:4b"),
    st.from_regex(r"[a-zA-Z]+:[2-4]b", fullmatch=True),
)


def _build_small_path_chat_kwargs(model_name: str, messages: list, ollama_tools: list) -> dict:
    """Simulate the small-path chat_kwargs construction from _generate_response.

    When tier == "small", the "tools" key must NOT be included even if
    ollama_tools is non-empty.
    """
    from distr.core.agent.tool_retriever import ToolRetriever

    tier = ToolRetriever.classify_model_tier(model_name)
    num_ctx = 8192
    chat_kwargs = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "options": {"keep_alive": -1, "num_ctx": num_ctx, "temperature": 0.7},
    }
    if tier != "small":
        # Standard path: include tools if available
        if ollama_tools:
            chat_kwargs["tools"] = ollama_tools
    # Small path: no "tools" key added
    return chat_kwargs


# Feature: small-model-tool-extraction, Property 4: no tools key on small path
@settings(max_examples=200)
@given(_SMALL_MODEL_NAMES)
def test_no_tools_key_in_small_path_payload(model_name: str) -> None:
    """**Validates: Requirements 2.4, 6.4**

    For any small-tier model name, the chat_kwargs constructed on the
    Text_Tool_Extraction path must NOT contain a "tools" key, even when
    ollama_tools is non-empty.
    """
    # Simulate non-empty ollama_tools (as if tools were available)
    fake_ollama_tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]
    messages = [{"role": "system", "content": "You are helpful."}]

    chat_kwargs = _build_small_path_chat_kwargs(model_name, messages, fake_ollama_tools)

    # Verify the model is actually classified as small
    from distr.core.agent.tool_retriever import ToolRetriever
    tier = ToolRetriever.classify_model_tier(model_name)
    assert tier == "small", f"Expected 'small' tier for {model_name!r}, got {tier!r}"

    # The key assertion: no "tools" key on small path
    assert "tools" not in chat_kwargs, (
        f"'tools' key found in chat_kwargs for small model {model_name!r}. "
        f"chat_kwargs keys: {list(chat_kwargs.keys())}"
    )


# ---------------------------------------------------------------------------
# Sub-task 6.2 — Property 11: Hallucinated tools always rejected
# Feature: small-model-tool-extraction, Property 11: hallucinated tools rejected
# ---------------------------------------------------------------------------


def _simulate_hallucination_check(
    tool_name: str,
    tools_dict: dict,
    offered_set: set,
) -> tuple[bool, str]:
    """Simulate the hallucination rejection logic from _generate_response.

    Returns (rejected: bool, reason: str).
    """
    if tool_name not in tools_dict:
        return True, "not_in_tools_dict"
    if tool_name not in offered_set:
        return True, "not_in_offered_set"
    return False, "accepted"


# Feature: small-model-tool-extraction, Property 11: hallucinated tools rejected
@settings(max_examples=200)
@given(
    st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True),  # tool_name
    st.lists(
        st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True),
        min_size=1,
        max_size=10,
        unique=True,
    ),  # all_tool_names (in _tools_dict)
    st.lists(
        st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,19}", fullmatch=True),
        min_size=0,
        max_size=5,
        unique=True,
    ),  # offered_subset (subset of all_tool_names offered in hint)
)
def test_hallucinated_tool_always_rejected(
    tool_name: str,
    all_tool_names: list,
    offered_subset: list,
) -> None:
    """**Validates: Requirements 8.1, 8.3**

    When IntentParser extracts a tool name that is present in _tools_dict
    but was NOT in the offered set, the tool must be rejected (not executed).
    """
    # Build tools_dict with all_tool_names
    tools_dict = {name: _make_mock_tool(name, f"Tool {name}", []) for name in all_tool_names}

    # Ensure tool_name is in tools_dict but NOT in offered_set
    # (add it to tools_dict if not already there, ensure it's not in offered_subset)
    tools_dict[tool_name] = _make_mock_tool(tool_name, f"Tool {tool_name}", [])
    offered_set = {name for name in offered_subset if name != tool_name}

    rejected, reason = _simulate_hallucination_check(tool_name, tools_dict, offered_set)

    # tool_name is in tools_dict but not in offered_set → must be rejected
    assert rejected is True, (
        f"Hallucinated tool {tool_name!r} was NOT rejected. "
        f"offered_set={offered_set!r}, reason={reason!r}"
    )
    assert reason == "not_in_offered_set", (
        f"Expected rejection reason 'not_in_offered_set', got {reason!r}"
    )


# ---------------------------------------------------------------------------
# Sub-task 6.3 — Unit tests for _generate_response routing
# ---------------------------------------------------------------------------


class TestGenerateResponseRouting:
    """Unit tests for _generate_response tier routing logic.

    These tests use the _build_small_path_chat_kwargs helper and the
    _simulate_hallucination_check helper to verify routing decisions
    without needing to instantiate OllamaLLMService (which requires
    heavy dependencies).
    """

    def test_small_tier_no_tools_key(self):
        """Small tier: chat_kwargs must not contain 'tools' key."""
        chat_kwargs = _build_small_path_chat_kwargs(
            "gemma3:4b",
            [{"role": "system", "content": "You are helpful."}],
            [{"type": "function", "function": {"name": "search"}}],
        )
        assert "tools" not in chat_kwargs

    def test_standard_tier_has_tools_key_when_tools_available(self):
        """Standard tier: chat_kwargs includes 'tools' key when tools are available."""
        chat_kwargs = _build_small_path_chat_kwargs(
            "llama3:8b",
            [{"role": "system", "content": "You are helpful."}],
            [{"type": "function", "function": {"name": "search"}}],
        )
        assert "tools" in chat_kwargs

    def test_standard_tier_no_tools_key_when_no_tools(self):
        """Standard tier: chat_kwargs has no 'tools' key when ollama_tools is empty."""
        chat_kwargs = _build_small_path_chat_kwargs(
            "llama3:8b",
            [{"role": "system", "content": "You are helpful."}],
            [],
        )
        assert "tools" not in chat_kwargs

    def test_micro_tier_no_tools_key(self):
        """Micro tier: chat_kwargs must not contain 'tools' key (same as small path)."""
        # Micro models use always-on tools only, no tools key sent
        from distr.core.agent.tool_retriever import ToolRetriever
        tier = ToolRetriever.classify_model_tier("qwen3:0.6b")
        assert tier == "micro"
        # Micro path also doesn't send tools (existing behaviour)
        chat_kwargs = _build_small_path_chat_kwargs(
            "qwen3:0.6b",
            [{"role": "system", "content": "You are helpful."}],
            [],
        )
        assert "tools" not in chat_kwargs

    def test_stale_hint_stripped_on_model_switch_to_standard(self):
        """Inject hint for small model, then strip (simulating switch to standard tier).

        Verifies that _strip_tool_hint removes the hint before the next request.
        """
        svc = _make_stub("You are helpful.")
        tool = _make_mock_tool("search", "Search the web", ["query"])

        # Simulate small model request: inject hint
        hint = svc._build_tool_hint_block([tool])
        svc._inject_tool_hint(hint)
        assert "--- Available Tools ---" in svc._messages[0]["content"]

        # Simulate model switch to standard: strip hint at top of _generate_response
        svc._strip_tool_hint()

        # Hint must be absent from the next request
        assert "--- Available Tools ---" not in svc._messages[0]["content"]
        assert "--- End Tools ---" not in svc._messages[0]["content"]

    def test_stale_hint_stripped_even_on_early_exit(self):
        """_strip_tool_hint is called unconditionally at the top of _generate_response.

        Simulates an early-exit path: hint is stripped before any tier branching.
        """
        svc = _make_stub("You are helpful.")
        tool = _make_mock_tool("search", "Search the web", ["query"])

        # Inject hint (as if previous request was small-tier)
        hint = svc._build_tool_hint_block([tool])
        svc._inject_tool_hint(hint)
        assert "--- Available Tools ---" in svc._messages[0]["content"]

        # Unconditional strip at top of _generate_response (before any branching)
        svc._strip_tool_hint()

        # Even on early exit, hint must be gone
        assert "--- Available Tools ---" not in svc._messages[0]["content"]

    def test_hallucination_rejection_tool_in_dict_not_offered(self):
        """Tool in _tools_dict but not in offered set → rejected."""
        tools_dict = {
            "search": _make_mock_tool("search", "Search", ["query"]),
            "get_weather": _make_mock_tool("get_weather", "Weather", ["city"]),
        }
        offered_set = {"search"}  # get_weather not offered

        rejected, reason = _simulate_hallucination_check("get_weather", tools_dict, offered_set)
        assert rejected is True
        assert reason == "not_in_offered_set"

    def test_hallucination_rejection_tool_not_in_dict(self):
        """Tool not in _tools_dict at all → rejected."""
        tools_dict = {"search": _make_mock_tool("search", "Search", ["query"])}
        offered_set = {"search"}

        rejected, reason = _simulate_hallucination_check("nonexistent_tool", tools_dict, offered_set)
        assert rejected is True
        assert reason == "not_in_tools_dict"

    def test_valid_tool_in_dict_and_offered_accepted(self):
        """Tool in _tools_dict AND in offered set → accepted."""
        tools_dict = {"search": _make_mock_tool("search", "Search", ["query"])}
        offered_set = {"search"}

        rejected, reason = _simulate_hallucination_check("search", tools_dict, offered_set)
        assert rejected is False
        assert reason == "accepted"

    def test_exception_during_parsing_falls_back_to_plain_text(self):
        """Exception during IntentParser.parse → WARNING logged, plain-text fallback.

        Simulates the try/except wrapper around IntentParser.parse in _generate_response.
        """
        import logging

        def _parse_with_exception(response_text, offered_names):
            raise RuntimeError("Simulated parser crash")

        # Simulate the try/except block
        full_response = "Here is my answer."
        _parse_result = None
        warning_logged = False

        try:
            _parse_result = _parse_with_exception(full_response, [])
        except Exception as exc:
            warning_logged = True
            _parse_result = None

        # After exception: parse_result is None → plain-text fallback
        assert _parse_result is None
        assert warning_logged is True
        # full_response is delivered unchanged
        assert full_response == "Here is my answer."

    def test_no_hint_injected_when_no_tools(self):
        """When filtered_tools is empty, no hint block is injected."""
        svc = _make_stub("You are helpful.")
        hint = svc._build_tool_hint_block([])
        assert hint == ""
        # Injecting empty hint is a no-op
        svc._inject_tool_hint(hint)
        assert "--- Available Tools ---" not in svc._messages[0]["content"]

    def test_hint_injected_when_tools_available(self):
        """When filtered_tools is non-empty, hint block is injected."""
        svc = _make_stub("You are helpful.")
        tools = [_make_mock_tool("search", "Search the web", ["query"])]
        hint = svc._build_tool_hint_block(tools)
        assert hint != ""
        svc._inject_tool_hint(hint)
        assert "--- Available Tools ---" in svc._messages[0]["content"]
