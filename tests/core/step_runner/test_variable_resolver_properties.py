"""Property-based tests for variable_resolver using Hypothesis.

Covers Properties 5, 6, and 12 from the design document:
  - Property 5: Variable resolution completeness (Task 3.2)
  - Property 6: Unresolvable placeholders preserved (Task 3.3)
  - Property 12: Variable resolution idempotence (Task 3.4)
"""

import json

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow_engine.variable_resolver import resolve_http_variables


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Safe text that won't contain {{ or }} to avoid accidental placeholder creation
safe_text = st.text(
    alphabet=st.characters(blacklist_characters="{}"),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip() != "")

# Identifiers suitable for JSON keys / variable names
identifier = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_]{0,15}", fullmatch=True)

# Simple string values that won't contain placeholder syntax
safe_value = st.text(
    alphabet=st.characters(blacklist_characters="{}"),
    min_size=1,
    max_size=50,
)

# Strategy for a step result with plain text (non-JSON)
plain_step_result = safe_value.map(lambda v: {"result": v})

# Strategy for a step result with a JSON dict body

@st.composite
def json_step_result(draw):
    """Generate a step result whose 'result' is a JSON dict string."""
    num_fields = draw(st.integers(min_value=1, max_value=5))
    fields = {}
    for _ in range(num_fields):
        key = draw(identifier)
        val = draw(safe_value)
        fields[key] = val
    return {"result": json.dumps(fields)}, fields


@st.composite
def step_results_list(draw, min_size=1, max_size=5):
    """Generate an ordered list of step results (plain text only)."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    return [draw(plain_step_result) for _ in range(n)]


@st.composite
def explicit_variables_map(draw, min_size=0, max_size=5):
    """Generate a dict of explicit variable name → value mappings.

    Keys are identifiers that do NOT look like step_N or step_N.field
    to avoid collisions with step-derived keys.
    """
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    variables = {}
    for _ in range(n):
        key = draw(identifier.filter(lambda k: not k.startswith("step_")))
        val = draw(safe_value)
        variables[key] = val
    return variables


# ---------------------------------------------------------------------------
# Property 5: Variable resolution completeness
# **Validates: Requirements 5.1, 5.2, 5.3, 5.5**
#
# For any HTTP Request config containing {{step_N}}, {{step_N.field}}, or
# {{variable_name}} placeholders, and any ordered list of previous step
# results, the resolver must: replace {{step_N}} with the full result text
# of step N, replace {{step_N.field}} with the value of field from step N's
# JSON-parsed result, and replace {{key}} with the value from the explicit
# variables map — all in URL, headers, and body.
# ---------------------------------------------------------------------------


class TestProperty5VariableResolutionCompleteness:
    """Property 5: Variable resolution completeness."""

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_resolves_to_full_result_in_url(self, data):
        """**Validates: Requirements 5.1, 5.3**

        For any step result at index i, {{step_{i+1}}} in the URL resolves
        to the full result text of that step.
        """
        results = data.draw(step_results_list(min_size=1, max_size=5))
        idx = data.draw(st.integers(min_value=0, max_value=len(results) - 1))
        step_num = idx + 1
        config = {"url": f"https://example.com/{{{{{f'step_{step_num}'}}}}}", "headers": {}, "body": None}
        resolved = resolve_http_variables(config, results)
        expected_value = str(results[idx].get("result", ""))
        assert resolved["url"] == f"https://example.com/{expected_value}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_field_resolves_to_json_field_in_url(self, data):
        """**Validates: Requirements 5.1, 5.2**

        For any step result that is a JSON dict, {{step_N.field}} in the URL
        resolves to the string value of that field.
        """
        result_entry, fields = data.draw(json_step_result())
        assume(len(fields) > 0)
        field_name = data.draw(st.sampled_from(sorted(fields.keys())))
        config = {"url": f"https://example.com/{{{{step_1.{field_name}}}}}", "headers": {}, "body": None}
        resolved = resolve_http_variables(config, [result_entry])
        assert resolved["url"] == f"https://example.com/{fields[field_name]}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_explicit_variable_resolves_in_url(self, data):
        """**Validates: Requirements 5.1, 5.5**

        For any explicit variable in the variables map, {{key}} in the URL
        resolves to the variable's value.
        """
        variables = data.draw(explicit_variables_map(min_size=1, max_size=5))
        var_name = data.draw(st.sampled_from(sorted(variables.keys())))
        config = {
            "url": f"https://example.com/{{{{{var_name}}}}}",
            "headers": {},
            "body": None,
            "variables": variables,
        }
        resolved = resolve_http_variables(config, [])
        assert resolved["url"] == f"https://example.com/{variables[var_name]}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_resolves_in_headers(self, data):
        """**Validates: Requirements 5.1, 5.3**

        {{step_N}} placeholders in header values are resolved.
        """
        results = data.draw(step_results_list(min_size=1, max_size=3))
        idx = data.draw(st.integers(min_value=0, max_value=len(results) - 1))
        step_num = idx + 1
        header_key = data.draw(identifier)
        config = {
            "url": "https://example.com",
            "headers": {header_key: f"Bearer {{{{{f'step_{step_num}'}}}}}"},
        }
        resolved = resolve_http_variables(config, results)
        expected_value = str(results[idx].get("result", ""))
        assert resolved["headers"][header_key] == f"Bearer {expected_value}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_resolves_in_body(self, data):
        """**Validates: Requirements 5.1, 5.3**

        {{step_N}} placeholders in the body are resolved.
        """
        results = data.draw(step_results_list(min_size=1, max_size=3))
        idx = data.draw(st.integers(min_value=0, max_value=len(results) - 1))
        step_num = idx + 1
        config = {
            "url": "https://example.com",
            "body": f"data={{{{{f'step_{step_num}'}}}}}",
        }
        resolved = resolve_http_variables(config, results)
        expected_value = str(results[idx].get("result", ""))
        assert resolved["body"] == f"data={expected_value}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_explicit_variable_resolves_in_headers_and_body(self, data):
        """**Validates: Requirements 5.1, 5.5**

        Explicit variables resolve in both headers and body.
        """
        variables = data.draw(explicit_variables_map(min_size=1, max_size=3))
        var_name = data.draw(st.sampled_from(sorted(variables.keys())))
        header_key = data.draw(identifier)
        config = {
            "url": "https://example.com",
            "headers": {header_key: f"{{{{{var_name}}}}}"},
            "body": f"payload={{{{{var_name}}}}}",
            "variables": variables,
        }
        resolved = resolve_http_variables(config, [])
        assert resolved["headers"][header_key] == variables[var_name]
        assert resolved["body"] == f"payload={variables[var_name]}"


# ---------------------------------------------------------------------------
# Property 6: Unresolvable placeholders preserved
# **Validates: Requirements 5.4**
#
# For any HTTP Request config containing a {{...}} placeholder that
# references a non-existent step index or a non-existent field, the resolver
# must leave that placeholder text unchanged in the output.
# ---------------------------------------------------------------------------


class TestProperty6UnresolvablePlaceholdersPreserved:
    """Property 6: Unresolvable placeholders preserved."""

    @given(data=st.data())
    @settings(max_examples=200)
    def test_out_of_range_step_placeholder_preserved_in_url(self, data):
        """**Validates: Requirements 5.4**

        A {{step_N}} placeholder where N exceeds the number of previous
        results is left unchanged in the URL.
        """
        num_results = data.draw(st.integers(min_value=0, max_value=3))
        results = [{"result": f"result_{i}"} for i in range(num_results)]
        bad_index = data.draw(st.integers(min_value=num_results + 1, max_value=num_results + 10))
        placeholder = f"{{{{step_{bad_index}}}}}"
        config = {"url": f"https://example.com/{placeholder}", "headers": {}, "body": None}
        resolved = resolve_http_variables(config, results)
        assert placeholder in resolved["url"]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_nonexistent_field_placeholder_preserved_in_url(self, data):
        """**Validates: Requirements 5.4**

        A {{step_N.field}} placeholder where 'field' does not exist in the
        JSON result is left unchanged.
        """
        json_result = json.dumps({"existing_key": "value"})
        results = [{"result": json_result}]
        bad_field = data.draw(identifier.filter(lambda f: f != "existing_key"))
        placeholder = f"{{{{step_1.{bad_field}}}}}"
        config = {"url": f"https://example.com/{placeholder}", "headers": {}, "body": None}
        resolved = resolve_http_variables(config, results)
        assert placeholder in resolved["url"]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_unknown_variable_placeholder_preserved_in_url(self, data):
        """**Validates: Requirements 5.4**

        A {{variable_name}} placeholder that is not in the explicit variables
        map and not a step reference is left unchanged.
        """
        unknown_var = data.draw(identifier.filter(lambda k: not k.startswith("step_")))
        placeholder = f"{{{{{unknown_var}}}}}"
        config = {"url": f"https://example.com/{placeholder}", "headers": {}, "body": None}
        resolved = resolve_http_variables(config, [])
        assert placeholder in resolved["url"]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_unresolvable_placeholder_preserved_in_headers(self, data):
        """**Validates: Requirements 5.4**

        Unresolvable placeholders in header values are left unchanged.
        """
        unknown_var = data.draw(identifier.filter(lambda k: not k.startswith("step_")))
        placeholder = f"{{{{{unknown_var}}}}}"
        header_key = data.draw(identifier)
        config = {
            "url": "https://example.com",
            "headers": {header_key: f"prefix-{placeholder}-suffix"},
        }
        resolved = resolve_http_variables(config, [])
        assert placeholder in resolved["headers"][header_key]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_unresolvable_placeholder_preserved_in_body(self, data):
        """**Validates: Requirements 5.4**

        Unresolvable placeholders in the body are left unchanged.
        """
        unknown_var = data.draw(identifier.filter(lambda k: not k.startswith("step_")))
        placeholder = f"{{{{{unknown_var}}}}}"
        config = {
            "url": "https://example.com",
            "body": f"data={placeholder}",
        }
        resolved = resolve_http_variables(config, [])
        assert placeholder in resolved["body"]


# ---------------------------------------------------------------------------
# Property 12: Variable resolution idempotence
# **Validates: Requirements 5.1**
#
# For any HTTP Request config and previous step results, resolving variables
# once and then resolving again on the result must produce the same output —
# resolution is idempotent.
# ---------------------------------------------------------------------------


class TestProperty12VariableResolutionIdempotence:
    """Property 12: Variable resolution idempotence."""

    @given(data=st.data())
    @settings(max_examples=200)
    def test_double_resolution_equals_single_resolution(self, data):
        """**Validates: Requirements 5.1**

        Resolving variables once and then resolving again on the already-
        resolved config produces the same output.
        """
        results = data.draw(step_results_list(min_size=0, max_size=3))
        variables = data.draw(explicit_variables_map(min_size=0, max_size=3))

        # Build a config that may contain various placeholders
        url_parts = []
        num_parts = data.draw(st.integers(min_value=1, max_value=3))
        for _ in range(num_parts):
            choice = data.draw(st.integers(min_value=0, max_value=2))
            if choice == 0 and len(results) > 0:
                idx = data.draw(st.integers(min_value=1, max_value=len(results)))
                url_parts.append(f"{{{{step_{idx}}}}}")
            elif choice == 1 and len(variables) > 0:
                var_name = data.draw(st.sampled_from(sorted(variables.keys())))
                url_parts.append(f"{{{{{var_name}}}}}")
            else:
                url_parts.append(data.draw(safe_text))

        url = "https://example.com/" + "/".join(url_parts)

        header_key = data.draw(identifier)
        header_val_choice = data.draw(st.integers(min_value=0, max_value=1))
        if header_val_choice == 0 and len(results) > 0:
            idx = data.draw(st.integers(min_value=1, max_value=len(results)))
            header_val = f"Bearer {{{{step_{idx}}}}}"
        else:
            header_val = data.draw(safe_text)

        body_choice = data.draw(st.integers(min_value=0, max_value=1))
        if body_choice == 0 and len(variables) > 0:
            var_name = data.draw(st.sampled_from(sorted(variables.keys())))
            body = f"payload={{{{{var_name}}}}}"
        else:
            body = data.draw(safe_text)

        config = {
            "url": url,
            "headers": {header_key: header_val},
            "body": body,
            "variables": variables,
        }

        # First resolution
        resolved_once = resolve_http_variables(config, results)
        # Second resolution on the already-resolved output
        resolved_twice = resolve_http_variables(resolved_once, results)

        assert resolved_once["url"] == resolved_twice["url"]
        assert resolved_once["headers"] == resolved_twice["headers"]
        assert resolved_once["body"] == resolved_twice["body"]


@st.composite
def json_step_result(draw):
    """Generate a step result whose 'result' is a JSON dict string."""
    num_fields = draw(st.integers(min_value=1, max_value=5))
    fields = {}
    for _ in range(num_fields):
        key = draw(identifier)
        val = draw(safe_value)
        fields[key] = val
    return {"result": json.dumps(fields)}, fields


@st.composite
def step_results_list(draw, min_size=1, max_size=5):
    """Generate an ordered list of step results (mix of plain and JSON)."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    results = []
    for _ in range(n):
        if draw(st.booleans()):
            result_pair = draw(json_step_result())
            results.append(result_pair[0])
        else:
            results.append(draw(plain_step_result))
    return results


@st.composite
def explicit_variables_map(draw, min_size=0, max_size=3):
    """Generate a dict of explicit variable name → value mappings."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    variables = {}
    for _ in range(n):
        key = draw(identifier)
        val = draw(safe_value)
        variables[key] = val
    return variables


# ---------------------------------------------------------------------------
# Property 5: Variable resolution completeness
# **Validates: Requirements 5.1, 5.2, 5.3, 5.5**
# ---------------------------------------------------------------------------


class TestProperty5VariableResolutionCompleteness:
    """Property 5: Variable resolution completeness.

    For any HTTP Request config containing {{step_N}}, {{step_N.field}}, or
    {{variable_name}} placeholders, and any ordered list of previous step
    results, the resolver must replace them correctly in URL, headers, and body.
    """

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_resolves_to_full_result_text(self, data):
        """**Validates: Requirements 5.1, 5.3**

        {{step_N}} in URL, headers, and body resolves to the full result
        text of step N (1-indexed).
        """
        results = data.draw(step_results_list(min_size=1, max_size=5))
        idx = data.draw(st.integers(min_value=1, max_value=len(results)))
        placeholder = "{{" + f"step_{idx}" + "}}"
        expected = str(results[idx - 1].get("result", ""))

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Step": placeholder},
            "body": f"data={placeholder}",
        }
        resolved = resolve_http_variables(config, results)

        assert resolved["url"] == f"https://example.com/{expected}"
        assert resolved["headers"]["X-Step"] == expected
        assert resolved["body"] == f"data={expected}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_step_n_field_resolves_to_json_field(self, data):
        """**Validates: Requirements 5.1, 5.2**

        {{step_N.field}} resolves to the value of 'field' from step N's
        JSON-parsed result.
        """
        result_pair = data.draw(json_step_result())
        step_result, fields = result_pair
        assume(len(fields) > 0)
        field_name = data.draw(st.sampled_from(sorted(fields.keys())))
        expected_value = str(fields[field_name])

        idx = 1
        placeholder = "{{" + f"step_{idx}.{field_name}" + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Field": placeholder},
            "body": placeholder,
        }
        resolved = resolve_http_variables(config, [step_result])

        assert resolved["url"] == f"https://example.com/{expected_value}"
        assert resolved["headers"]["X-Field"] == expected_value
        assert resolved["body"] == expected_value

    @given(data=st.data())
    @settings(max_examples=200)
    def test_explicit_variable_resolves_in_all_targets(self, data):
        """**Validates: Requirements 5.1, 5.5**

        {{variable_name}} from the explicit variables map resolves in URL,
        headers, and body.
        """
        var_name = data.draw(identifier)
        var_value = data.draw(safe_value)
        placeholder = "{{" + var_name + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Var": placeholder},
            "body": f"val={placeholder}",
            "variables": {var_name: var_value},
        }
        resolved = resolve_http_variables(config, [])

        assert resolved["url"] == f"https://example.com/{var_value}"
        assert resolved["headers"]["X-Var"] == var_value
        assert resolved["body"] == f"val={var_value}"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_mixed_placeholders_all_resolve(self, data):
        """**Validates: Requirements 5.1, 5.2, 5.3, 5.5**

        A config with a mix of {{step_N}}, {{step_N.field}}, and
        {{variable_name}} placeholders resolves all of them correctly.
        """
        # Generate a JSON step result so we can use field access
        result_pair = data.draw(json_step_result())
        step_result, fields = result_pair
        assume(len(fields) > 0)
        field_name = data.draw(st.sampled_from(sorted(fields.keys())))

        var_name = data.draw(identifier)
        # Ensure var_name doesn't collide with step_1 or step_1.field
        assume(var_name != "step_1")
        assume(not var_name.startswith("step_1."))
        var_value = data.draw(safe_value)

        full_result = str(step_result.get("result", ""))
        field_value = str(fields[field_name])

        config = {
            "url": "https://example.com/{{step_1}}/{{" + f"step_1.{field_name}" + "}}/{{" + var_name + "}}",
            "headers": {},
            "body": None,
            "variables": {var_name: var_value},
        }
        resolved = resolve_http_variables(config, [step_result])

        expected_url = f"https://example.com/{full_result}/{field_value}/{var_value}"
        assert resolved["url"] == expected_url


# ---------------------------------------------------------------------------
# Property 6: Unresolvable placeholders preserved
# **Validates: Requirements 5.4**
# ---------------------------------------------------------------------------


class TestProperty6UnresolvablePlaceholdersPreserved:
    """Property 6: Unresolvable placeholders preserved.

    For any HTTP Request config containing a {{...}} placeholder that
    references a non-existent step index or a non-existent field, the
    resolver must leave that placeholder text unchanged in the output.
    """

    @given(data=st.data())
    @settings(max_examples=200)
    def test_out_of_range_step_index_preserved(self, data):
        """**Validates: Requirements 5.4**

        A {{step_N}} placeholder where N exceeds the number of previous
        step results is left unchanged.
        """
        num_results = data.draw(st.integers(min_value=0, max_value=5))
        results = [{"result": data.draw(safe_value)} for _ in range(num_results)]
        bad_idx = data.draw(st.integers(min_value=num_results + 1, max_value=num_results + 10))
        placeholder = "{{" + f"step_{bad_idx}" + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Bad": placeholder},
            "body": placeholder,
        }
        resolved = resolve_http_variables(config, results)

        assert placeholder in resolved["url"]
        assert resolved["headers"]["X-Bad"] == placeholder
        assert resolved["body"] == placeholder

    @given(data=st.data())
    @settings(max_examples=200)
    def test_nonexistent_field_preserved(self, data):
        """**Validates: Requirements 5.4**

        A {{step_N.field}} placeholder where 'field' does not exist in
        step N's JSON result is left unchanged.
        """
        result_pair = data.draw(json_step_result())
        step_result, fields = result_pair
        # Generate a field name that is NOT in the JSON result
        bad_field = data.draw(identifier)
        assume(bad_field not in fields)

        placeholder = "{{" + f"step_1.{bad_field}" + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Missing": placeholder},
            "body": placeholder,
        }
        resolved = resolve_http_variables(config, [step_result])

        assert placeholder in resolved["url"]
        assert resolved["headers"]["X-Missing"] == placeholder
        assert resolved["body"] == placeholder

    @given(data=st.data())
    @settings(max_examples=200)
    def test_unknown_variable_name_preserved(self, data):
        """**Validates: Requirements 5.4**

        A {{variable_name}} placeholder that is not in the explicit
        variables map and not a step reference is left unchanged.
        """
        var_name = data.draw(identifier)
        # Ensure it doesn't look like a step reference
        assume(not var_name.startswith("step_"))
        placeholder = "{{" + var_name + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {"X-Unknown": placeholder},
            "body": placeholder,
            "variables": {},  # empty variables map
        }
        resolved = resolve_http_variables(config, [])

        assert placeholder in resolved["url"]
        assert resolved["headers"]["X-Unknown"] == placeholder
        assert resolved["body"] == placeholder

    @given(data=st.data())
    @settings(max_examples=200)
    def test_field_on_non_json_result_preserved(self, data):
        """**Validates: Requirements 5.4**

        A {{step_N.field}} placeholder where step N's result is not valid
        JSON is left unchanged.
        """
        plain_result = data.draw(safe_value)
        # Ensure it's not valid JSON dict
        try:
            parsed = json.loads(plain_result)
            assume(not isinstance(parsed, dict))
        except (json.JSONDecodeError, TypeError):
            pass  # good — not JSON

        field_name = data.draw(identifier)
        placeholder = "{{" + f"step_1.{field_name}" + "}}"

        config = {
            "url": f"https://example.com/{placeholder}",
            "headers": {},
            "body": None,
        }
        resolved = resolve_http_variables(config, [{"result": plain_result}])

        assert placeholder in resolved["url"]


# ---------------------------------------------------------------------------
# Property 12: Variable resolution idempotence
# **Validates: Requirements 5.1**
# ---------------------------------------------------------------------------


class TestProperty12VariableResolutionIdempotence:
    """Property 12: Variable resolution idempotence.

    For any HTTP Request config and previous step results, resolving
    variables once and then resolving again on the result must produce
    the same output — resolution is idempotent.
    """

    @given(data=st.data())
    @settings(max_examples=200)
    def test_double_resolution_is_idempotent(self, data):
        """**Validates: Requirements 5.1**

        resolve(resolve(config)) == resolve(config) for any config and
        step results.
        """
        results = data.draw(step_results_list(min_size=0, max_size=5))
        variables = data.draw(explicit_variables_map(min_size=0, max_size=3))

        # Build a config with various placeholders
        url_parts = []
        num_placeholders = data.draw(st.integers(min_value=0, max_value=3))
        for _ in range(num_placeholders):
            choice = data.draw(st.integers(min_value=0, max_value=2))
            if choice == 0 and len(results) > 0:
                idx = data.draw(st.integers(min_value=1, max_value=len(results)))
                url_parts.append("{{" + f"step_{idx}" + "}}")
            elif choice == 1 and len(variables) > 0:
                var_name = data.draw(st.sampled_from(sorted(variables.keys())))
                url_parts.append("{{" + var_name + "}}")
            else:
                url_parts.append(data.draw(safe_value))

        url = "https://example.com/" + "/".join(url_parts) if url_parts else "https://example.com"

        header_val = data.draw(safe_value)
        body_val = data.draw(st.one_of(st.none(), safe_value))

        config = {
            "url": url,
            "headers": {"X-Test": header_val},
            "body": body_val,
            "variables": variables,
        }

        first_pass = resolve_http_variables(config, results)
        second_pass = resolve_http_variables(first_pass, results)

        assert first_pass["url"] == second_pass["url"]
        assert first_pass["headers"] == second_pass["headers"]
        assert first_pass["body"] == second_pass["body"]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_already_resolved_config_unchanged(self, data):
        """**Validates: Requirements 5.1**

        A config with no placeholders is unchanged after resolution,
        confirming idempotence for the trivial case.
        """
        url = data.draw(safe_value.map(lambda s: "https://example.com/" + s))
        header_val = data.draw(safe_value)
        body_val = data.draw(st.one_of(st.none(), safe_value))

        config = {
            "url": url,
            "headers": {"X-Test": header_val},
            "body": body_val,
        }

        resolved = resolve_http_variables(config, [])

        assert resolved["url"] == config["url"]
        assert resolved["headers"] == config["headers"]
        assert resolved["body"] == config["body"]
