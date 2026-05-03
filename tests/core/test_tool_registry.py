"""Tests for distr.core.agent.tools.registry.ToolRegistry."""

import pytest
from langchain_core.tools import BaseTool

from distr.core.agent.tools.registry import (
    RegisteredTool,
    ToolRegistry,
    get_tool_registry,
    reset_tool_registry_for_tests,
)


class _StubTool(BaseTool):
    """Minimal LangChain tool for registry tests."""

    name: str = "stub_tool"
    description: str = "test"

    def _run(self, *args, **kwargs):  # noqa: ANN002
        return ""


class _StubTool2(BaseTool):
    name: str = "other_stub"
    description: str = "second"

    def _run(self, *args, **kwargs):  # noqa: ANN002
        return ""


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_tool_registry_for_tests()
    yield
    reset_tool_registry_for_tests()


def test_register_and_get_by_name():
    reg = ToolRegistry()
    t = _StubTool()
    reg.register(t, "native")
    assert reg.get_by_name("stub_tool") is t
    assert reg.count() == 1


def test_duplicate_name_raises():
    reg = ToolRegistry()
    reg.register(_StubTool(), "native")
    with pytest.raises(ValueError, match="Duplicate tool name"):
        reg.register(_StubTool(), "native")


def test_unregister_by_source():
    reg = ToolRegistry()
    reg.register(_StubTool(), "native")
    reg.register(_StubTool2(), "mcp:demo")
    assert reg.unregister_by_source("native") == 1
    assert reg.get_by_name("stub_tool") is None
    assert reg.get_by_name("other_stub") is not None


def test_get_all_only_available():
    reg = ToolRegistry()
    reg.register(_StubTool(), "native", available=False)
    assert reg.get_all() == []
    assert reg.set_available("stub_tool", True) is True
    assert len(reg.get_all()) == 1


def test_search_by_substring():
    reg = ToolRegistry()
    reg.register(_StubTool(), "native")
    reg.register(_StubTool2(), "native")
    found = reg.search("other")
    assert len(found) == 1
    assert found[0].name == "other_stub"


def test_get_tool_registry_singleton():
    a = get_tool_registry()
    b = get_tool_registry()
    assert a is b


def test_registered_tool_dataclass_fields():
    reg = ToolRegistry()
    t = _StubTool()
    reg.register(t, "skill:x")
    rec = reg.get_record("stub_tool")
    assert isinstance(rec, RegisteredTool)
    assert rec.source == "skill:x"
    assert rec.available is True
