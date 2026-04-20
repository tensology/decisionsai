"""Unit tests for distr.core.workflow_engine.variable_resolver."""

import logging

from distr.core.workflow_engine.variable_resolver import resolve_http_variables


class TestResolveStepN:
    """Tests for {{step_N}} full-result resolution."""

    def test_step_reference_resolves_full_result(self):
        config = {"url": "https://api.example.com/{{step_1}}", "headers": {}, "body": None}
        results = [{"result": "abc123"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://api.example.com/abc123"

    def test_multiple_step_references(self):
        config = {"url": "https://{{step_1}}.example.com/{{step_2}}"}
        results = [{"result": "host"}, {"result": "path"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://host.example.com/path"

    def test_step_index_is_one_based(self):
        config = {"url": "{{step_1}}/{{step_2}}"}
        results = [{"result": "first"}, {"result": "second"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "first/second"


class TestResolveStepNField:
    """Tests for {{step_N.field}} JSON field resolution."""

    def test_json_field_access(self):
        config = {"url": "https://api.example.com/users/{{step_1.id}}"}
        results = [{"result": '{"id": 42, "name": "Alice"}'}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://api.example.com/users/42"

    def test_multiple_json_fields(self):
        config = {"url": "https://api.example.com/{{step_1.resource}}/{{step_1.id}}"}
        results = [{"result": '{"resource": "users", "id": 99}'}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://api.example.com/users/99"

    def test_non_json_result_no_field_access(self):
        """When result is not JSON, step_N.field should be unresolvable."""
        config = {"url": "https://api.example.com/{{step_1.id}}"}
        results = [{"result": "plain text"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://api.example.com/{{step_1.id}}"


class TestResolveExplicitVariables:
    """Tests for {{variable_name}} from the explicit variables map."""

    def test_explicit_variable_resolves(self):
        config = {
            "url": "https://api.example.com/{{base_path}}",
            "variables": {"base_path": "v2"},
        }
        resolved = resolve_http_variables(config, [])
        assert resolved["url"] == "https://api.example.com/v2"

    def test_explicit_variable_overrides_step(self):
        """Explicit variables take precedence over step-derived keys."""
        config = {
            "url": "{{step_1}}",
            "variables": {"step_1": "override"},
        }
        results = [{"result": "original"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "override"


class TestUnresolvablePlaceholders:
    """Tests for unresolvable placeholders being left as-is."""

    def test_unknown_placeholder_left_as_is(self):
        config = {"url": "https://api.example.com/{{unknown}}"}
        resolved = resolve_http_variables(config, [])
        assert resolved["url"] == "https://api.example.com/{{unknown}}"

    def test_out_of_range_step_left_as_is(self):
        config = {"url": "{{step_5}}"}
        results = [{"result": "only one"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "{{step_5}}"

    def test_warning_logged_for_unresolvable(self, caplog):
        config = {"url": "{{missing_var}}"}
        with caplog.at_level(logging.WARNING):
            resolve_http_variables(config, [])
        assert "missing_var" in caplog.text


class TestResolutionTargets:
    """Tests that resolution applies to URL, headers, and body."""

    def test_resolves_in_headers(self):
        config = {
            "url": "https://api.example.com",
            "headers": {"Authorization": "Bearer {{step_1}}"},
        }
        results = [{"result": "tok123"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["headers"]["Authorization"] == "Bearer tok123"

    def test_resolves_in_body(self):
        config = {
            "url": "https://api.example.com",
            "body": '{"user": "{{step_1.name}}"}',
        }
        results = [{"result": '{"name": "Bob"}'}]
        resolved = resolve_http_variables(config, results)
        assert resolved["body"] == '{"user": "Bob"}'

    def test_resolves_across_url_headers_body(self):
        config = {
            "url": "https://{{api_host}}/items",
            "headers": {"X-Token": "{{step_1}}"},
            "body": '{"id": "{{step_2.id}}"}',
            "variables": {"api_host": "prod.example.com"},
        }
        results = [
            {"result": "mytoken"},
            {"result": '{"id": "item-42"}'},
        ]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "https://prod.example.com/items"
        assert resolved["headers"]["X-Token"] == "mytoken"
        assert resolved["body"] == '{"id": "item-42"}'


class TestNoMutation:
    """Tests that the original config is not mutated."""

    def test_original_config_unchanged(self):
        config = {
            "url": "{{step_1}}",
            "headers": {"H": "{{step_1}}"},
            "body": "{{step_1}}",
        }
        results = [{"result": "val"}]
        resolve_http_variables(config, results)
        assert config["url"] == "{{step_1}}"
        assert config["headers"]["H"] == "{{step_1}}"
        assert config["body"] == "{{step_1}}"


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_config(self):
        resolved = resolve_http_variables({}, [])
        assert resolved == {}

    def test_none_body_stays_none(self):
        config = {"url": "https://example.com", "body": None}
        resolved = resolve_http_variables(config, [])
        assert resolved["body"] is None

    def test_empty_string_body_stays_empty(self):
        config = {"url": "https://example.com", "body": ""}
        resolved = resolve_http_variables(config, [])
        assert resolved["body"] == ""

    def test_no_placeholders_returns_unchanged_values(self):
        config = {"url": "https://example.com", "headers": {"A": "B"}, "body": "hello"}
        resolved = resolve_http_variables(config, [])
        assert resolved["url"] == "https://example.com"
        assert resolved["headers"] == {"A": "B"}
        assert resolved["body"] == "hello"

    def test_missing_result_key_defaults_to_empty(self):
        config = {"url": "{{step_1}}"}
        results = [{}]  # no "result" key
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == ""

    def test_empty_variables_map(self):
        config = {"url": "{{step_1}}", "variables": {}}
        results = [{"result": "val"}]
        resolved = resolve_http_variables(config, results)
        assert resolved["url"] == "val"
