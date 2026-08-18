"""Integration tests for ``request_tool`` fuzzy injection (production-shaped).

These tests use:
- ``warm_tool_cache()`` so ``_tool_cache`` matches real app startup
- ``LLMSharedMixin._wire_request_tool_callback`` as in Ollama/other providers
- Real ``thefuzz`` / ``fuzzywuzzy`` scoring (skipped if neither is installed)
- **Dynamic tool pick**: first non-always-on cached tool — works even when optional
  integrations (e.g. ``web_search``) fail to construct in CI

Run fast unit tests only:
  pytest tests/core/agent/test_tool_retriever.py -q

Include integration:
  pytest tests/core/agent/test_request_tool_integration.py -q -m integration

Exclude integration:
  pytest tests/core/agent -q -m \"not integration\"

Requires fuzzy library (see repo ``requirements.txt``).
"""

from __future__ import annotations

import json
import logging

import pytest

pytestmark = pytest.mark.integration


def test_cache_contains_request_tool_after_warm(warm_real_tool_cache) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    assert get_cached_tool("request_tool") is not None


def test_exact_registry_class_name_injects_tool(
    injectable_tool_example,
    request_tool_harness_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exact registry class string maps to the cached instance (stable fuzzy score)."""
    tname = injectable_tool_example["tool_name"]
    rclass = injectable_tool_example["registry_class"]
    inst = injectable_tool_example["instance"]

    harness = request_tool_harness_factory(exclude_names=frozenset({tname}))
    assert tname not in harness._tools_dict

    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    rtt = harness._tools_dict["request_tool"]
    msg = rtt._run(text=rclass)

    assert tname in harness._tools_dict
    assert harness._tools_dict[tname] is inst
    assert [x.name for x in harness._tools].count(tname) == 1
    assert "retry" in msg.lower() or "available" in msg.lower()

    telemetry_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "distr.core.agent.tool_telemetry"
        and "TOOL_TELEMETRY {" in r.getMessage()
    ]
    assert telemetry_lines, "expected TOOL_TELEMETRY INFO lines"
    payload = json.loads(telemetry_lines[-1].split(" ", 1)[1])
    assert payload["event"] == "request_tool"
    assert payload["success"] is True
    assert payload.get("injection_performed") is True
    assert payload.get("injected_tool_name") == tname


def test_natural_language_injects_same_tool_when_description_matches(
    injectable_tool_example,
    request_tool_harness_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sentence from ``TOOL_DESCRIPTIONS`` should exceed fuzzy threshold (>=75)."""
    from distr.core.agent.tools.loader import TOOL_DESCRIPTIONS

    tname = injectable_tool_example["tool_name"]
    rclass = injectable_tool_example["registry_class"]

    harness = request_tool_harness_factory(exclude_names=frozenset({tname}))
    desc = TOOL_DESCRIPTIONS.get(rclass, "").strip()
    if len(desc) < 40:
        pytest.skip(f"TOOL_DESCRIPTIONS too short for NL test ({rclass!r})")

    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    rtt = harness._tools_dict["request_tool"]
    rtt._run(text=desc[:200])

    assert tname in harness._tools_dict
    payload = None
    for r in reversed(caplog.records):
        if r.name == "distr.core.agent.tool_telemetry" and "TOOL_TELEMETRY {" in r.getMessage():
            payload = json.loads(r.getMessage().split(" ", 1)[1])
            break
    assert payload is not None
    assert payload.get("injected_tool_name") == tname
    assert payload.get("fuzzy_score", 0) >= 75


def test_second_request_is_idempotent_and_telemetry_reflects_it(
    injectable_tool_example,
    request_tool_harness_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tname = injectable_tool_example["tool_name"]
    rclass = injectable_tool_example["registry_class"]

    harness = request_tool_harness_factory(exclude_names=frozenset({tname}))
    rtt = harness._tools_dict["request_tool"]
    rtt._run(text=rclass)
    assert tname in harness._tools_dict
    n_tools = len(harness._tools)

    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    caplog.clear()
    msg2 = rtt._run(text=rclass)

    assert len(harness._tools) == n_tools
    assert "already" in msg2.lower()
    payload = None
    for r in reversed(caplog.records):
        if r.name == "distr.core.agent.tool_telemetry" and "TOOL_TELEMETRY {" in r.getMessage():
            payload = json.loads(r.getMessage().split(" ", 1)[1])
            break
    assert payload is not None
    assert payload.get("injection_performed") is False


def test_empty_query_short_circuits_without_crash(request_tool_harness_factory) -> None:
    harness = request_tool_harness_factory()
    rtt = harness._tools_dict["request_tool"]
    out = rtt._run(text="   ")
    assert "provide" in out.lower() or "description" in out.lower()


def test_missing_capability_is_built_and_exposed_in_same_request(
    request_tool_harness_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from distr.core.agent.tools.artifacts import ArtifactTool, BuildToolTool

    harness = request_tool_harness_factory()
    build_tool = harness._tools_dict["build_tool"]
    generated = ArtifactTool(
        {
            "format": 1,
            "name": "built__quantum_flux_consolidator",
            "description": "A generated test capability.",
            "input_schema": {"type": "object", "properties": {}},
            "steps": [
                {
                    "id": "run",
                    "name": "Run",
                    "kind": "python",
                    "code": "print('ok')",
                }
            ],
        }
    )

    def _fake_build(self, request: str, **kwargs) -> str:
        assert "quantum flux" in request.lower()
        self._on_tool_built(generated)
        return f"Built deterministic capability '{generated.name}'."

    monkeypatch.setattr(BuildToolTool, "_run", _fake_build)
    message = harness._tools_dict["request_tool"]._run(
        text="Perform quantum flux consolidation using the lunar checksum protocol."
    )

    assert generated.name in harness._tools_dict
    assert generated.name in harness._sticky_tool_names
    assert generated.name in message
    assert harness._tools_dict["build_tool"] is build_tool


def test_mouse_movement_natural_language_is_exposed_in_current_request(
    request_tool_harness_factory,
    warm_real_tool_cache,
) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    mouse_tool = get_cached_tool("mouse_movement")
    if mouse_tool is None:
        pytest.skip("mouse_movement tool is not available in cache")

    harness = request_tool_harness_factory()
    if mouse_tool.name not in harness._tools_dict:
        harness._tools.append(mouse_tool)
        harness._tools_dict[mouse_tool.name] = mouse_tool
    harness._sticky_tool_names = set()
    harness._wire_request_tool_callback()

    rtt = harness._tools_dict["request_tool"]
    msg = rtt._run(
        text="Need a tool to move the mouse down by a small amount or directional mouse movement."
    )

    assert "mouse_movement" in harness._sticky_tool_names
    assert "exposed" in msg.lower()


def test_second_request_for_exposed_cached_tool_is_idempotent(
    request_tool_harness_factory,
    warm_real_tool_cache,
) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    mouse_tool = get_cached_tool("mouse_movement")
    if mouse_tool is None:
        pytest.skip("mouse_movement tool is not available in cache")

    harness = request_tool_harness_factory()
    if mouse_tool.name not in harness._tools_dict:
        harness._tools.append(mouse_tool)
        harness._tools_dict[mouse_tool.name] = mouse_tool
    harness._sticky_tool_names = set()
    harness._wire_request_tool_callback()
    rtt = harness._tools_dict["request_tool"]

    rtt._run(text="MouseMovementTool")
    msg = rtt._run(text="MouseMovementTool")

    assert harness._sticky_tool_names == {"mouse_movement"}
    assert "already exposed" in msg.lower()


def test_cached_screenshot_tool_is_exposed_in_current_request(
    request_tool_harness_factory,
    warm_real_tool_cache,
) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    screenshot_tool = get_cached_tool("screenshot_analyzer")
    if screenshot_tool is None:
        pytest.skip("screenshot_analyzer tool is not available in cache")

    harness = request_tool_harness_factory()
    if screenshot_tool.name not in harness._tools_dict:
        harness._tools.append(screenshot_tool)
        harness._tools_dict[screenshot_tool.name] = screenshot_tool
    harness._sticky_tool_names = set()
    harness._wire_request_tool_callback()

    rtt = harness._tools_dict["request_tool"]
    msg = rtt._run(text="I need to take a proper screenshot and analyze the screen.")

    assert "screenshot_analyzer" in harness._sticky_tool_names
    assert "now exposed" in msg.lower()


def test_gmail_query_injects_google_workspace_without_fuzzy_match(
    request_tool_harness_factory,
    warm_real_tool_cache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    gw_tool = get_cached_tool("google_workspace")
    if gw_tool is None:
        pytest.skip("google_workspace tool is not available in cache")

    harness = request_tool_harness_factory(exclude_names=frozenset({"google_workspace"}))
    assert "google_workspace" not in harness._tools_dict

    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    rtt = harness._tools_dict["request_tool"]
    msg = rtt._run(text="count my gmail emails from snuza")

    assert "google_workspace" in harness._tools_dict
    assert harness._tools_dict["google_workspace"] is gw_tool
    assert "gmail" in msg.lower() or "email" in msg.lower()

    payload = None
    for r in reversed(caplog.records):
        if r.name == "distr.core.agent.tool_telemetry" and "TOOL_TELEMETRY {" in r.getMessage():
            payload = json.loads(r.getMessage().split(" ", 1)[1])
            break
    assert payload is not None
    assert payload["event"] == "request_tool"
    assert payload["success"] is True
    assert payload.get("injected_tool_name") == "google_workspace"
    assert payload.get("injection_performed") is True


def test_gmail_query_uses_existing_google_workspace_when_already_active(
    request_tool_harness_factory,
    warm_real_tool_cache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from distr.core.agent.tools.loader import get_cached_tool

    gw_tool = get_cached_tool("google_workspace")
    if gw_tool is None:
        pytest.skip("google_workspace tool is not available in cache")

    harness = request_tool_harness_factory()
    if "google_workspace" not in harness._tools_dict:
        harness._tools.append(gw_tool)
        harness._tools_dict[gw_tool.name] = gw_tool
    n_tools = len(harness._tools)

    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    rtt = harness._tools_dict["request_tool"]
    msg = rtt._run(text="gmail inbox count for django errors")

    assert len(harness._tools) == n_tools
    assert "mailbox read results" in msg.lower()
    assert "google_workspace" in msg.lower()

    payload = None
    for r in reversed(caplog.records):
        if r.name == "distr.core.agent.tool_telemetry" and "TOOL_TELEMETRY {" in r.getMessage():
            payload = json.loads(r.getMessage().split(" ", 1)[1])
            break
    assert payload is not None
    assert payload["event"] == "request_tool"
    assert payload["success"] is True
    assert payload.get("injected_tool_name") == "google_workspace"
    assert payload.get("injection_performed") is False
