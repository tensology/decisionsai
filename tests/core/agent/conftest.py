"""Agent-specific fixtures (retrieval, ``request_tool`` integration).

Headless stubs live in ``tests/conftest.py`` (PyQt6, settings).
"""

from __future__ import annotations

import pytest


def _require_fuzzy_library() -> None:
    """``core_mixin`` uses thefuzz, else fuzzywuzzy (see project requirements)."""
    for _name in ("thefuzz", "fuzzywuzzy"):
        try:
            __import__(_name)
            return
        except ImportError:
            continue
    pytest.skip(
        "request_tool integration needs thefuzz or fuzzywuzzy (pip install -r requirements.txt)",
    )


@pytest.fixture(scope="session")
def warm_real_tool_cache():
    """Populate ``_tool_cache`` once per session (production-like instantiation).

    Some tools may fail to construct (optional sidecars); callers must skip tests
    when a required tool name is missing from the cache.
    """
    _require_fuzzy_library()
    from distr.core.agent.tools.loader import _tool_cache, warm_tool_cache

    if not _tool_cache:
        warm_tool_cache()

    assert _tool_cache, "warm_tool_cache produced an empty cache"
    return _tool_cache


@pytest.fixture(scope="session")
def injectable_tool_example(warm_real_tool_cache):
    """First cached tool not in ALWAYS_ON — used for injection integration tests."""
    from distr.core.agent.tool_retriever import ALWAYS_ON_NAMES
    from distr.core.agent.tools.loader import _tool_cache

    for tool_name in sorted(_tool_cache.keys()):
        if tool_name in ALWAYS_ON_NAMES or tool_name == "request_tool":
            continue
        inst = _tool_cache[tool_name]
        reg_class = type(inst).__name__
        return {"tool_name": tool_name, "registry_class": reg_class, "instance": inst}
    pytest.skip(
        "no injectable tools in cache (only always-on present — broaden warm_tool_cache deps)",
    )


@pytest.fixture
def request_tool_harness_factory(warm_real_tool_cache):
    """Build an :class:`LLMSharedMixin` stub with always-on tools + ``request_tool`` wired.

    Pass *exclude_names* to withhold specific tool **names** (see ``tool.name``) so tests
    can verify on-demand injection for that capability.
    """
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
    from distr.core.agent.tool_retriever import ALWAYS_ON_NAMES
    from distr.core.agent.tools.loader import get_cached_tool

    def _make(
        *,
        model_name: str = "integration/test-model",
        exclude_names: frozenset[str] | None = None,
    ) -> LLMSharedMixin:
        exclude = exclude_names or frozenset()
        names: list[str] = []
        for n in sorted(ALWAYS_ON_NAMES):
            if n in exclude:
                continue
            tool = get_cached_tool(n)
            if tool is not None:
                names.append(n)
        rt = get_cached_tool("request_tool")
        assert rt is not None, "request_tool must be in cache after warm_tool_cache()"
        tools = [get_cached_tool(n) for n in names]
        tools = [t for t in tools if t is not None]
        tools.append(rt)

        class _Harness(LLMSharedMixin):
            """Minimal surface for ``_wire_request_tool_callback``."""

            def __init__(self) -> None:
                self._model_name = model_name
                self._tools = list(tools)
                self._tools_dict = {t.name: t for t in self._tools}

        h = _Harness()
        h._wire_request_tool_callback()
        return h

    return _make
