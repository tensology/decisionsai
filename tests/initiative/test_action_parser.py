import pytest

from distr.core.initiative.proposed_action import (
    VALID_ACTION_TYPES,
    ProposedAction,
    deserialize,
    parse_llm_response,
    serialize,
)


class TestParseLLMResponse:
    def test_valid_json(self):
        raw = '{"action_type": "suggestion", "description": "Do something"}'
        action = parse_llm_response(raw)
        assert action.action_type == "suggestion"
        assert action.description == "Do something"

    def test_strips_json_fence(self):
        raw = '```json\n{"action_type": "routine_task", "description": "Run task"}\n```'
        action = parse_llm_response(raw)
        assert action.action_type == "routine_task"

    def test_strips_plain_fence(self):
        raw = '```\n{"action_type": "suggestion", "description": "test"}\n```'
        action = parse_llm_response(raw)
        assert action.action_type == "suggestion"

    def test_invalid_json_returns_none_action(self):
        action = parse_llm_response("not valid json at all")
        assert action.action_type == "none"

    def test_invalid_action_type_defaults_to_none(self):
        raw = '{"action_type": "invalid_type", "description": "test"}'
        action = parse_llm_response(raw)
        assert action.action_type == "none"

    def test_missing_action_type_defaults_to_none(self):
        raw = '{"description": "test"}'
        action = parse_llm_response(raw)
        assert action.action_type == "none"

    def test_missing_description_uses_default(self):
        raw = '{"action_type": "suggestion"}'
        action = parse_llm_response(raw)
        assert action.description == "No description provided"

    def test_empty_description_uses_default(self):
        raw = '{"action_type": "suggestion", "description": ""}'
        action = parse_llm_response(raw)
        assert action.description == "No description provided"

    def test_all_valid_action_types(self):
        for at in VALID_ACTION_TYPES:
            raw = f'{{"action_type": "{at}", "description": "test"}}'
            action = parse_llm_response(raw)
            assert action.action_type == at


class TestSerializeDeserialize:
    def test_round_trip(self):
        action = ProposedAction(
            action_type="routine_task",
            description="Run daily task",
            payload={"runner_type": "step_runner"},
            draft="draft content",
            telegram_message="[Initiative] Running",
            requires_confirmation=False,
        )
        assert deserialize(serialize(action)) == action

    def test_deserialize_missing_fields_use_defaults(self):
        action = deserialize({})
        assert action.action_type == "none"
        assert action.description == "No description provided"
        assert action.payload == {}
        assert action.draft == ""
        assert action.telegram_message == ""
        assert action.requires_confirmation is False
