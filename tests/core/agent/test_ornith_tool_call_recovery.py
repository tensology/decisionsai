"""Regression tests for text-encoded tool calls from local Ollama models."""

from types import SimpleNamespace

from distr.core.agent.services.llm.mixins.ollama_response import OllamaResponseMixin
from distr.core.agent.services.llm.text_utils import (
    clean_text_for_tts,
    parse_tool_calls_from_content,
)


class _OllamaToolHarness(OllamaResponseMixin):
    def __init__(self) -> None:
        self._tools_dict = {
            "start_project": SimpleNamespace(name="start_project"),
        }


def test_parse_ornith_invoke_syntax_as_tool_call():
    raw = (
        'Starting Tensology now! (invoke. name="startproject") '
        '(parameter. name="command")1( parameter) (.invoke)'
    )

    parsed = parse_tool_calls_from_content(raw)

    assert parsed == [{"name": "startproject", "arguments": '{"command": "1"}'}]


def test_ornith_startproject_alias_rewrites_to_real_start_project_tool():
    harness = _OllamaToolHarness()
    tool_calls = [{"function": {"name": "startproject", "arguments": {"command": "1"}}}]

    harness._intercept_tool_calls(
        tool_calls,
        "wondering if you could start a project for me. Can you start tensology?",
    )

    assert tool_calls == [
        {
            "function": {
                "name": "start_project",
                "arguments": {
                    "text": "wondering if you could start a project for me. Can you start tensology?"
                },
            }
        }
    ]


def test_ornith_invoke_syntax_is_not_spoken_by_tts():
    raw = (
        'Starting Tensology now! (invoke. name="startproject") '
        '(parameter. name="command")1( parameter) (.invoke)'
        "Tensology is starting up!"
    )

    spoken = clean_text_for_tts(raw, spoken_prose=True)

    assert "invoke" not in spoken.lower()
    assert "parameter" not in spoken.lower()
    assert "startproject" not in spoken.lower()
    assert spoken == "Starting Tensology now! Tensology is starting up!"
