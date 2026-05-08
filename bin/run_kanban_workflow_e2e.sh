#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Kanban/Workflow targeted regression"
pytest -q \
  tests/core/test_result_packet.py \
  tests/core/test_step_router.py::TestRouteIntegration::test_route_updates_result_packet_in_run_data \
  tests/test_kanban_agent/test_ticket_processing.py::TestSequentialTicketProcessing::test_tickets_run_in_source_lane_position_order

echo "==> Kanban/Workflow UI E2E (WebKit)"
pytest -q \
  tests/ui/test_kanban_workflow_e2e_webkit.py \
  tests/ui/test_workflows_active_run_webkit.py \
  -m e2e_playwright \
  --browser webkit

echo "==> Done"
