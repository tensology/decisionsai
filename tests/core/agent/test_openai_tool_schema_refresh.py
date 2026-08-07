from types import SimpleNamespace

from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService


def test_tool_schema_builder_uses_fresh_filtered_tools(monkeypatch):
    service = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    request_tool = SimpleNamespace(name="request_tool")
    mouse_tool = SimpleNamespace(name="mouse_movement")
    rounds = [[request_tool], [request_tool, mouse_tool]]

    service._get_filtered_tools = lambda _message: rounds.pop(0)
    monkeypatch.setattr(
        "distr.core.agent.services.llm.openai_compat.convert_tools_to_openai_format",
        lambda tools: [tool.name for tool in tools],
    )

    first = service._tools_list_for_message("again")
    second = service._tools_list_for_message("again")

    assert first == ["request_tool"]
    assert second == ["request_tool", "mouse_movement"]
