"""Tests for ICM workspace memory companion store."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import distr.core.workspace_memory.paths as wm_paths
from distr.core.workspace_memory.paths import AGENTS_FILE, HANDOFF_FILE, companion_root
from distr.core.workspace_memory.pickup_handoff import (
    build_pickup_brief,
    perform_handoff,
    read_handoff_preview,
    write_handoff,
)


class WorkspaceMemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        wm_paths.WORKSPACES_ROOT = Path(self._tmpdir.name) / "workspaces"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_handoff_round_trip(self):
        with patch("distr.core.workspace_memory.sync.sync_projection_for_project"):
            perform_handoff("tickets", 99, summary="Pinterest world VR walkthrough parked.", source="test")
            preview = read_handoff_preview("tickets", 99)
            self.assertIn("Pinterest world", preview)
            brief = build_pickup_brief(entity_type="tickets", entity_id=99, title="Ticket 99")
            self.assertIn("Pinterest world", brief)
            handoff_path = companion_root("tickets", 99) / "memory" / HANDOFF_FILE
            self.assertTrue(handoff_path.is_file())

    def test_projection_sync_on_handoff(self):
        with patch("distr.core.workspace_memory.sync.sync_projection_for_project") as mock_sync:
            root = companion_root("projects", 5)
            root.mkdir(parents=True, exist_ok=True)
            (root / "decisions.json").write_text(
                json.dumps({"project_id": 5, "folder_location": "/tmp/proj"}),
                encoding="utf-8",
            )
            write_handoff("projects", 5, body="Session done.", source="test")
            mock_sync.assert_called_once_with(5)

    def test_ticket_bootstrap_writes_scaffold(self):
        ticket = type("T", (), {
            "id": 42,
            "title": "Auth demo",
            "description": "Fix login",
            "context_notes": "V2 scope",
            "lane_id": 1,
            "linked_project_id": 7,
            "linked_workflow_id": 3,
        })()
        lane = type("L", (), {"board_id": 5})()
        ticket_query = mock.MagicMock()
        ticket_query.filter.return_value.first.return_value = ticket
        lane_query = mock.MagicMock()
        lane_query.filter.return_value.first.return_value = lane

        def _query(model):
            name = getattr(model, "__name__", str(model))
            if name == "KanbanTicket":
                return ticket_query
            return lane_query

        with patch("distr.core.workspace_memory.provision.bootstrap_org"):
            with patch("distr.core.workspace_memory.provision.bootstrap_board"):
                with patch("distr.core.workspace_memory.provision.get_session") as mock_session:
                    session = mock_session.return_value.__enter__.return_value
                    session.query.side_effect = _query
                    from distr.core.workspace_memory.provision import bootstrap_ticket

                    path = bootstrap_ticket(42, force=True)
        root = Path(path)
        self.assertTrue((root / AGENTS_FILE).is_file())
        self.assertTrue((root / "router.md").is_file())
        decisions = json.loads((root / "decisions.json").read_text(encoding="utf-8"))
        self.assertEqual(decisions.get("ticket_id"), 42)

    def test_workflow_harness_handoff_package(self):
        wf = type("W", (), {"id": 9, "name": "Ship loop", "context_rules": "Always run tests."})()
        wf_query = mock.MagicMock()
        wf_query.filter.return_value.first.return_value = wf
        with patch("distr.core.workspace_memory.harness_handoff.bootstrap_workflow") as mock_boot:
            with patch("distr.core.workspace_memory.harness_handoff.sync_entity_references"):
                with patch("distr.core.workspace_memory.harness_handoff.sync_workflow_stages"):
                    with patch("distr.core.workspace_memory.harness_handoff._workflow_board_and_project", return_value=(None, None, "Ship loop", "")):
                        with patch("distr.core.workspace_memory.harness_handoff.build_step_routing_table", return_value="| step | read |"):
                            with patch("distr.core.workspace_memory.harness_handoff.read_handoff_preview", return_value="Last: auth done."):
                                with patch("distr.core.workspace_memory.harness_handoff.workspace_summary", return_value={}):
                                    with patch("distr.core.workspace_memory.harness_handoff.router_chain", return_value=[]):
                                        mock_boot.return_value = str(companion_root("workflows", 9))
                                        root = companion_root("workflows", 9)
                                        root.mkdir(parents=True, exist_ok=True)
                                        (root / AGENTS_FILE).write_text("# agents\n", encoding="utf-8")
                                        from distr.core.workspace_memory.harness_handoff import build_workflow_harness_handoff

                                        pkg = build_workflow_harness_handoff(9, refresh=True)
        self.assertEqual(pkg["workflow_id"], 9)
        self.assertIn("Ship loop", pkg["paste_block"])
        self.assertIn("Return contract", pkg["paste_block"])
        self.assertIn(AGENTS_FILE, pkg["entry_file"])
        self.assertTrue(pkg["pickup_prompt"])

    def test_resolve_workflow_id_by_name(self):
        with patch("distr.core.workflow.service.list_workflows") as mock_list:
            mock_list.return_value = [{"id": 7, "name": "Development loop"}]
            from distr.core.workflow.workflow_resolve import resolve_workflow_id

            wid, err = resolve_workflow_id(workflow_name="Development")
            self.assertIsNone(err)
            self.assertEqual(wid, 7)

    def test_dogfood_exit_gate_requires_screenshots(self):
        from distr.core.workflow.dogfood_gate import enforce_dogfood_exit_gate

        with patch("distr.core.workflow.dogfood_gate.is_dogfood_workflow", return_value=True):
            status, packet, missing = enforce_dogfood_exit_gate(
                packet={"harness_report": {"status": "completed", "summary": "ok"}},
                run_status="completed",
                workflow_id=1,
            )
        self.assertEqual(status, "failed")
        self.assertIn("playwright_screenshots", missing)


if __name__ == "__main__":
    unittest.main()
