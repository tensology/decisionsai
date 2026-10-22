"""Live Laya routing decisions through the Development HTTP boundary."""

from __future__ import annotations

import os

import pytest

from distr.core.laya_runtime import get_mode, set_mode, status
from distr.core.mcp.config import MCPConfigDocument, MCPServerConfig, load_mcp_config, save_mcp_config
from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _assess(harness: WorkflowTicketLoopHarness, instruction: str) -> dict:
    return harness.api_request(
        "/workflows/studio/routing-assessment",
        method="POST",
        data={"instruction": instruction},
        timeout=60,
    )


def test_live_laya_auto_prefer_and_off_modes_are_visible_and_deterministic() -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_LAYA_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_LAYA_E2E=1 to run live Laya routing certification.")
    runtime = status()
    if not runtime.get("installed"):
        pytest.skip("Laya is not installed.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")
    original_mode = get_mode()
    original_config = load_mcp_config()
    try:
        set_mode("auto")
        confident = _assess(
            harness,
            "Implement a multi-step production security migration across the entire codebase "
            "with independent review and end-to-end validation.",
        )
        assert confident["decision_engine"] == "laya", confident
        assert confident["laya"]["mode"] == "auto", confident
        assert confident["laya"]["used"] is True, confident
        assert float(confident["laya"]["confidence"]) >= 0.60, confident

        low_confidence = _assess(harness, "hello")
        assert low_confidence["decision_engine"] == "heuristic", low_confidence
        assert low_confidence["laya"]["mode"] == "auto", low_confidence
        assert low_confidence["laya"]["used"] is False, low_confidence
        assert float(low_confidence["laya"]["confidence"]) < 0.60, low_confidence

        set_mode("prefer")
        preferred = _assess(harness, "hello")
        assert preferred["decision_engine"] == "laya", preferred
        assert preferred["laya"]["mode"] == "prefer", preferred
        assert preferred["laya"]["used"] is True, preferred

        set_mode("off")
        disabled = _assess(harness, "Implement a production security migration.")
        assert disabled["decision_engine"] == "heuristic", disabled
        assert disabled["laya"] == {"mode": "off", "used": False}, disabled

        broken_servers = [server for server in original_config.servers if server.name != "laya"]
        broken_servers.append(
            MCPServerConfig(
                name="laya",
                enabled=True,
                transport="stdio",
                command=("/definitely/missing/decisions-laya-e2e",),
                env=frozenset({("DECISIONS_LAYA_MODE", "auto")}),
            )
        )
        save_mcp_config(MCPConfigDocument(servers=tuple(broken_servers)))
        failed = _assess(harness, "Implement a production security migration.")
        assert failed["decision_engine"] == "heuristic", failed
        assert failed["laya"]["mode"] == "auto", failed
        assert failed["laya"]["used"] is False, failed
        assert failed["laya"]["fallback_reason"], failed
    finally:
        save_mcp_config(original_config)
        assert get_mode() == original_mode
