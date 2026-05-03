"""Unit tests for :meth:`ToolRetriever.route` (no embedding backends required)."""

from __future__ import annotations

from types import SimpleNamespace

from distr.core.agent.tool_retriever import ToolRetriever


def test_route_returns_all_tools_when_retrieve_returns_none(monkeypatch):
    retriever = ToolRetriever()
    tools = [
        SimpleNamespace(name="alpha"),
        SimpleNamespace(name="beta"),
    ]
    monkeypatch.setattr(retriever, "retrieve", lambda msg, model: None)
    out = retriever.route("hello", tools)
    assert [t.name for t in out] == ["alpha", "beta"]


def test_route_orders_and_filters_by_retrieve_names(monkeypatch):
    retriever = ToolRetriever()
    tools = [
        SimpleNamespace(name="a"),
        SimpleNamespace(name="b"),
        SimpleNamespace(name="c"),
    ]
    monkeypatch.setattr(retriever, "retrieve", lambda msg, model: ["c", "a"])
    out = retriever.route("hello", tools)
    assert [t.name for t in out] == ["c", "a"]


def test_route_skips_unknown_tool_names(monkeypatch):
    retriever = ToolRetriever()
    tools = [SimpleNamespace(name="only")]
    monkeypatch.setattr(retriever, "retrieve", lambda msg, model: ["missing", "only"])
    out = retriever.route("hello", tools)
    assert [t.name for t in out] == ["only"]
