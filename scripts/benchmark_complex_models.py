#!/usr/bin/env python3
"""Stress-test coding models against a realistic mixed-language project.

The benchmark is deliberately harder than a small function exercise.  It asks
each backend to trace a feature through a Python domain/service/URL stack and a
JavaScript/React/HTML client while ignoring plausible legacy and generated
decoys.  Scoring combines executable behavior, cross-layer contracts, and
scope discipline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distr.core.project_cli_backends.base import ProjectTask
from distr.core.project_cli_backends.registry import get_backend


ALLOWED_CHANGES = {
    "app/repositories/tickets.py",
    "app/services/tickets.py",
    "app/web/views.py",
    "web/api/tickets.js",
    "web/hooks/useTickets.js",
    "web/components/TicketBoard.jsx",
    "templates/board.html",
}


FILES: dict[str, str] = {
    "AGENTS.md": """# Complex benchmark rules

- Work only in the seven files explicitly listed in the task.
- Do not edit tests, models, URL declarations, generated code, vendor code, or legacy code.
- Trace existing abstractions before changing an implementation.
- Run `python -m unittest discover -s tests -v` before reporting completion.
- Prefix shell commands with `rtk` when supported.
""",
    "app/__init__.py": "",
    "app/models/__init__.py": "",
    "app/models/base.py": """from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Entity:
    id: int
    created_at: datetime

    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.created_at).total_seconds())
""",
    "app/models/ticket.py": """from dataclasses import dataclass
from app.models.base import Entity


@dataclass(frozen=True)
class Ticket(Entity):
    board_id: int
    title: str
    position: int
    archived: bool = False
""",
    "app/repositories/__init__.py": "",
    "app/repositories/base.py": """from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    @abstractmethod
    def get(self, object_id: int) -> T | None:
        raise NotImplementedError
""",
    "app/repositories/tickets.py": """from collections.abc import Iterable
from app.models.ticket import Ticket
from app.repositories.base import Repository


class TicketRepository(Repository[Ticket]):
    def __init__(self, tickets: Iterable[Ticket]):
        self._tickets = list(tickets)

    def get(self, object_id: int) -> Ticket | None:
        return next((ticket for ticket in self._tickets if ticket.id == object_id), None)

    def list_for_board(self, board_id: int) -> list[Ticket]:
        # BUG: archived tickets leak into the default board view.
        return [ticket for ticket in self._tickets if ticket.board_id == board_id]
""",
    "app/services/__init__.py": "",
    "app/services/tickets.py": """from app.repositories.tickets import TicketRepository


class TicketService:
    def __init__(self, repository: TicketRepository):
        self.repository = repository

    def list_board_tickets(self, board_id: int):
        return self.repository.list_for_board(board_id)
""",
    "app/utils/__init__.py": "",
    "app/utils/query.py": """TRUE_QUERY_VALUES = frozenset({"1", "true", "yes", "on"})


def query_flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_QUERY_VALUES
""",
    "app/web/__init__.py": "",
    "app/web/urls.py": """ROUTES = {
    "board-tickets": "/api/boards/<board_id>/tickets",
}
""",
    "app/web/views.py": """from app.services.tickets import TicketService


def board_tickets_view(service: TicketService, board_id: object, query: dict[str, object]):
    tickets = service.list_board_tickets(int(board_id))
    return {
        "items": [{"id": ticket.id, "title": ticket.title, "archived": ticket.archived} for ticket in tickets],
        "meta": {"count": len(tickets)},
    }
""",
    "web/api/tickets.js": """export async function fetchBoardTickets(boardId) {
  const response = await fetch(`/api/boards/${encodeURIComponent(boardId)}/tickets`);
  if (!response.ok) throw new Error(`Ticket request failed: ${response.status}`);
  return response.json();
}
""",
    "web/hooks/useTickets.js": """import { useCallback, useEffect, useState } from 'react';
import { fetchBoardTickets } from '../api/tickets';

export function useTickets(boardId) {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await fetchBoardTickets(boardId);
      setTickets(payload.items);
    } finally {
      setLoading(false);
    }
  }, [boardId]);

  useEffect(() => { reload(); }, [reload]);
  return { tickets, loading, reload };
}
""",
    "web/components/TicketBoard.jsx": """import React from 'react';
import { useTickets } from '../hooks/useTickets';

export function TicketBoard({ boardId }) {
  const { tickets, loading, reload } = useTickets(boardId);
  return (
    <section aria-busy={loading}>
      <button type="button" onClick={reload}>Refresh</button>
      <p>{tickets.length} tickets</p>
      <ul>{tickets.map(ticket => <li key={ticket.id}>{ticket.title}</li>)}</ul>
    </section>
  );
}
""",
    "templates/board.html": """<!doctype html>
<html lang="en">
  <body>
    <main id="ticket-board-root" data-board-id="{{ board.id }}"></main>
    <script type="module" src="/static/board-entry.js"></script>
  </body>
</html>
""",
    "legacy/TicketBoard.jsx": """// Decoy: this retired component is intentionally not imported.
export function TicketBoard() { return null; }
""",
    "generated/api-client.js": """// GENERATED FILE. Changes will be overwritten.
export const boardTicketsPath = id => `/v0/boards/${id}/tickets`;
""",
    "vendor/query.py": """# Third-party compatibility shim. Do not modify.
def query_flag(value):
    return value == "yes"
""",
    "README.md": """# Atlas Tickets

The active stack is `app/`, `web/`, and `templates/`. The similarly named
`legacy/`, `generated/`, and `vendor/` paths are not application source.
""",
    "tests/__init__.py": "",
    "tests/test_backend_behavior.py": """import unittest
from datetime import datetime, timezone

from app.models.ticket import Ticket
from app.repositories.tickets import TicketRepository
from app.services.tickets import TicketService
from app.web.views import board_tickets_view


def ticket(ticket_id, board_id, position, *, archived=False):
    return Ticket(ticket_id, datetime.now(timezone.utc), board_id, f"T{ticket_id}", position, archived)


class BackendBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.repository = TicketRepository([
            ticket(4, 7, 20, archived=True),
            ticket(2, 7, 10),
            ticket(3, 8, 1),
            ticket(1, 7, 10, archived=True),
            ticket(5, 7, 5),
        ])
        self.service = TicketService(self.repository)

    def test_repository_hides_archived_by_default(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7)], [5, 2])

    def test_repository_can_include_archived(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7, include_archived=True)], [5, 1, 2, 4])

    def test_repository_does_not_leak_other_boards(self):
        self.assertNotIn(3, [item.id for item in self.repository.list_for_board(7, include_archived=True)])

    def test_repository_orders_by_position_then_id(self):
        self.assertEqual([item.id for item in self.repository.list_for_board(7, include_archived=True)], [5, 1, 2, 4])

    def test_service_preserves_default(self):
        self.assertEqual([item.id for item in self.service.list_board_tickets(7)], [5, 2])

    def test_service_forwards_include_archived(self):
        self.assertEqual(len(self.service.list_board_tickets(7, include_archived=True)), 4)

    def test_service_normalizes_numeric_board_id(self):
        self.assertEqual([item.id for item in self.service.list_board_tickets("7")], [5, 2])

    def test_view_default_contract(self):
        payload = board_tickets_view(self.service, "7", {})
        self.assertEqual(payload["meta"], {"count": 2, "include_archived": False})

    def test_view_true_query_spellings(self):
        for value in ("1", "true", "TRUE", " yes ", "on", True):
            with self.subTest(value=value):
                payload = board_tickets_view(self.service, 7, {"include_archived": value})
                self.assertEqual(payload["meta"], {"count": 4, "include_archived": True})

    def test_view_false_query_spellings(self):
        for value in ("0", "false", "no", "off", "", False, None):
            with self.subTest(value=value):
                self.assertEqual(board_tickets_view(self.service, 7, {"include_archived": value})["meta"]["count"], 2)

    def test_view_item_shape_is_stable(self):
        self.assertEqual(set(board_tickets_view(self.service, 7, {})["items"][0]), {"id", "title", "archived"})

    def test_invalid_board_id_is_rejected(self):
        with self.assertRaises((TypeError, ValueError)):
            board_tickets_view(self.service, "not-an-id", {})


if __name__ == "__main__":
    unittest.main()
""",
    "tests/test_frontend_contract.py": r"""import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = (ROOT / "web/api/tickets.js").read_text()
        cls.hook = (ROOT / "web/hooks/useTickets.js").read_text()
        cls.component = (ROOT / "web/components/TicketBoard.jsx").read_text()
        cls.template = (ROOT / "templates/board.html").read_text()

    def test_api_accepts_options_without_breaking_old_callers(self):
        self.assertRegex(self.api, r"fetchBoardTickets\s*\(\s*boardId\s*,\s*\{[^}]*includeArchived\s*=\s*false[^}]*\}\s*=\s*\{\s*\}")

    def test_api_uses_include_archived_query_name(self):
        self.assertIn("include_archived", self.api)

    def test_api_only_adds_true_value_when_enabled(self):
        self.assertRegex(self.api, r"if\s*\(\s*includeArchived\s*\)")
        self.assertRegex(self.api, r"include_archived[^\n]*(true|1)")

    def test_api_still_encodes_board_id(self):
        self.assertIn("encodeURIComponent(boardId)", self.api)

    def test_hook_owns_include_archived_state(self):
        self.assertRegex(self.hook, r"useState\s*\(\s*false\s*\)")
        self.assertIn("includeArchived", self.hook)

    def test_hook_passes_flag_to_api(self):
        self.assertRegex(self.hook, r"fetchBoardTickets\s*\(\s*boardId\s*,\s*\{\s*includeArchived\s*\}\s*\)")

    def test_hook_reload_tracks_flag_dependency(self):
        match = re.search(r"useCallback\s*\([\s\S]*?,\s*\[([^]]+)]\s*\);", self.hook)
        self.assertIsNotNone(match)
        self.assertIn("includeArchived", match.group(1))

    def test_hook_uses_idiomatic_callback_shape(self):
        self.assertNotRegex(self.hook, r"useCallback\s*\(\s*\[")

    def test_hook_returns_toggle_contract(self):
        self.assertIn("setIncludeArchived", self.hook)

    def test_component_renders_checkbox(self):
        self.assertRegex(self.component, r"type\s*=\s*[\"']checkbox[\"']")

    def test_component_checkbox_is_controlled(self):
        self.assertIn("checked={includeArchived}", self.component)
        self.assertIn("setIncludeArchived", self.component)

    def test_component_has_accessible_label(self):
        self.assertRegex(self.component.lower(), r"(show|include) archived")

    def test_component_keeps_refresh_action(self):
        self.assertIn("onClick={reload}", self.component)

    def test_template_exposes_archived_default(self):
        self.assertRegex(self.template, r"data-include-archived=[\"']false[\"']")


if __name__ == "__main__":
    unittest.main()
""",
}


PROMPT = """Implement an optional "Show archived" feature across this ticket board.

This is a mixed Python, JavaScript, React, and HTML project. Trace the active code path before editing. Similar files exist under legacy/, generated/, and vendor/ but they are decoys and must not be changed.

Required behavior:
1. TicketRepository.list_for_board accepts include_archived=False, hides archived tickets by default, optionally includes them, never leaks another board, and always sorts by (position, id).
2. TicketService.list_board_tickets keeps the same default, normalizes board_id to int, and forwards include_archived.
3. board_tickets_view parses query["include_archived"] with the existing app.utils.query.query_flag helper. Its meta object contains both count and the normalized include_archived boolean. Keep the existing item shape.
4. fetchBoardTickets remains backward-compatible, accepts an optional { includeArchived = false } options object, and only appends include_archived=true when enabled. Keep board ID URL encoding and error handling.
5. useTickets owns includeArchived state, passes it to the API, reloads when it changes, and returns includeArchived plus setIncludeArchived.
6. TicketBoard renders an accessible controlled checkbox labelled "Show archived", without removing refresh, loading, count, or ticket rendering.
7. The server-rendered mount in templates/board.html declares data-include-archived="false".

You may modify only:
- app/repositories/tickets.py
- app/services/tickets.py
- app/web/views.py
- web/api/tickets.js
- web/hooks/useTickets.js
- web/components/TicketBoard.jsx
- templates/board.html

Do not modify tests, models, base classes, URLs, README, AGENTS.md, legacy/, generated/, or vendor/. Run `python -m unittest discover -s tests -v` and report what changed and the test result.
"""


@dataclass(frozen=True)
class BackendSpec:
    name: str
    backend: str
    provider: str
    model: str
    reasoning_effort: str = "medium"


BACKENDS = {
    "gpt": BackendSpec("gpt-5.6-sol-medium", "codex", "openai", "gpt-5.6-sol", "medium"),
    "glimmer": BackendSpec("muse-glimmer-30b-mlx", "pi", "ollama", "muse-glimmer:30b-mlx", "medium"),
}


def _write_workspace(folder: Path) -> None:
    for relative, content in FILES.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (folder / "prompt.txt").write_text(PROMPT, encoding="utf-8")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_tests(folder: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=folder,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _count_test_results(output: str) -> tuple[int, int]:
    passed = sum(1 for line in output.splitlines() if line.rstrip().endswith("... ok"))
    failed = sum(
        1
        for line in output.splitlines()
        if line.rstrip().endswith("... FAIL") or line.rstrip().endswith("... ERROR")
    )
    return passed, failed


def _hidden_contract_checks(folder: Path) -> dict[str, bool]:
    """Evaluate important preservation rules not disclosed as fixture tests."""
    repository = (folder / "app/repositories/tickets.py").read_text(encoding="utf-8")
    service = (folder / "app/services/tickets.py").read_text(encoding="utf-8")
    view = (folder / "app/web/views.py").read_text(encoding="utf-8")
    api = (folder / "web/api/tickets.js").read_text(encoding="utf-8")
    hook = (folder / "web/hooks/useTickets.js").read_text(encoding="utf-8")
    component = (folder / "web/components/TicketBoard.jsx").read_text(encoding="utf-8")
    template = (folder / "templates/board.html").read_text(encoding="utf-8")
    active_text = "\n".join((repository, service, view, api, hook, component, template))
    return {
        "repository_inheritance_preserved": "class TicketRepository(Repository[Ticket])" in repository,
        "repository_get_contract_preserved": "def get(" in repository and "object_id" in repository,
        "service_dependency_preserved": "self.repository" in service,
        "view_reuses_shared_query_helper": "app.utils.query import query_flag" in view and "query_flag(" in view,
        "api_error_handling_preserved": "response.ok" in api and "throw new Error" in api,
        "hook_loading_lifecycle_preserved": all(token in hook for token in ("setLoading(true)", "finally", "setLoading(false)")),
        "component_loading_contract_preserved": "aria-busy={loading}" in component,
        "component_stable_ticket_keys_preserved": "key={ticket.id}" in component,
        "template_entrypoint_preserved": 'id="ticket-board-root"' in template and "/static/board-entry.js" in template,
        "active_code_avoids_decoy_paths": not any(token in active_text for token in ("legacy/", "generated/", "vendor/")),
    }


def grade_workspace(folder: Path) -> dict[str, Any]:
    return_code, output = _run_tests(folder)
    passed, failed = _count_test_results(output)
    protected_changes: list[str] = []
    missing_files: list[str] = []
    changed_allowed: list[str] = []
    for relative, original in FILES.items():
        target = folder / relative
        if not target.exists():
            missing_files.append(relative)
            continue
        changed = _digest(target.read_text(encoding="utf-8")) != _digest(original)
        if changed and relative in ALLOWED_CHANGES:
            changed_allowed.append(relative)
        elif changed:
            protected_changes.append(relative)

    unexpected = []
    known = set(FILES) | {"prompt.txt"}
    for target in folder.rglob("*"):
        if target.is_file() and "__pycache__" not in target.parts:
            relative = target.relative_to(folder).as_posix()
            if relative not in known and not relative.endswith((".pyc", ".pyo")):
                unexpected.append(relative)

    behavior_points = min(passed, 26)
    layer_points = sum(1 for path in ALLOWED_CHANGES if path in changed_allowed)
    scope_points = 8 if not protected_changes and not missing_files and not unexpected else 0
    hidden_checks = _hidden_contract_checks(folder)
    hidden_points = sum(hidden_checks.values())
    total = behavior_points + layer_points + scope_points + hidden_points
    return {
        "score": total,
        "max_score": 51,
        "behavior": {"passed": passed, "failed": failed, "max_points": 26},
        "cross_layer": {
            "changed": sorted(changed_allowed),
            "expected": sorted(ALLOWED_CHANGES),
            "points": layer_points,
            "max_points": 7,
        },
        "scope_discipline": {
            "points": scope_points,
            "max_points": 8,
            "protected_changes": sorted(protected_changes),
            "missing_files": sorted(missing_files),
            "unexpected_files": sorted(unexpected),
        },
        "hidden_contracts": {
            "points": hidden_points,
            "max_points": len(hidden_checks),
            "checks": hidden_checks,
        },
        "tests_exit_code": return_code,
        "tests_output": output,
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


async def _run_backend(spec: BackendSpec, folder: Path, timeout: int) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    def on_event(event: dict[str, Any]) -> None:
        events.append({str(key): _json_safe(value) for key, value in event.items()})

    task = ProjectTask(
        project_id=91001,
        project_name=folder.name,
        folder=str(folder),
        instruction=PROMPT,
        origin="benchmark",
        model=spec.model,
        ticket_complexity="high",
        codex_reasoning_effort=spec.reasoning_effort,
        adapter_options={
            "model_provider": spec.provider,
            "mutation_expected": True,
            "timeout_seconds": timeout,
        },
    )
    started = time.monotonic()
    result = await get_backend(spec.backend).send_task(task, on_event=on_event)
    elapsed = round(time.monotonic() - started, 3)
    usage: dict[str, Any] = {}
    for event in events:
        message = event.get("message")
        if event.get("type") == "message_end" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
            usage = message["usage"]
    return {
        "name": spec.name,
        "backend": spec.backend,
        "provider": spec.provider,
        "model": spec.model,
        "reasoning_effort": spec.reasoning_effort,
        "success": result.success,
        "elapsed_seconds": elapsed,
        "engine": result.engine,
        "error": result.error,
        "output": result.output,
        "usage": usage,
        "event_count": len(events),
        "grade": grade_workspace(folder),
    }


def _copy_workspace(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _write_workspace(destination)


async def _run_suite(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = [item.strip() for item in args.models.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(BACKENDS))
    if unknown:
        raise ValueError(f"Unknown model aliases: {', '.join(unknown)}")

    results = []
    for alias in requested:
        spec = BACKENDS[alias]
        folder = output_dir / spec.name
        _copy_workspace(folder)
        result = await _run_backend(spec, folder, args.timeout)
        results.append(result)
        (output_dir / f"{spec.name}-result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    summary = {
        "benchmark": "complex-project-cross-stack",
        "test_gates": 26,
        "scope_gates": 8,
        "cross_layer_gates": 7,
        "hidden_contract_gates": 10,
        "max_score": 51,
        "results": results,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def _regrade_output_dir(output_dir: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for result_path in sorted(output_dir.glob("*-result.json")):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        workspace = output_dir / str(payload["name"])
        payload["grade"] = grade_workspace(workspace)
        result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(payload)
    summary = {
        "benchmark": "complex-project-cross-stack",
        "test_gates": 26,
        "scope_gates": 8,
        "cross_layer_gates": 7,
        "hidden_contract_gates": 10,
        "max_score": 51,
        "results": results,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="gpt,glimmer", help="Comma-separated aliases: gpt,glimmer")
    parser.add_argument("--output-dir", default="artifacts/model-benchmark-complex")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--prepare", help="Prepare one untouched fixture at this path and exit")
    parser.add_argument("--grade", help="Grade an existing fixture and exit")
    parser.add_argument("--regrade-output-dir", help="Re-run grading for completed result files")
    args = parser.parse_args()

    if args.prepare:
        destination = Path(args.prepare).expanduser().resolve()
        _copy_workspace(destination)
        print(json.dumps({"prepared": str(destination), "test_gates": 26, "hidden_contract_gates": 10, "max_score": 51}, indent=2))
        return 0
    if args.grade:
        print(json.dumps(grade_workspace(Path(args.grade).expanduser().resolve()), indent=2))
        return 0
    if args.regrade_output_dir:
        summary = _regrade_output_dir(Path(args.regrade_output_dir).expanduser().resolve())
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    summary = asyncio.run(_run_suite(args))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(item["success"] for item in summary["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
