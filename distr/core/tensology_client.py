from __future__ import annotations

import json
import getpass
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any


_LAST_DISCUSSION_POLL = 0.0
_LAST_CONTEXT_POLL = 0.0
_LAST_TICKET_POLL = 0.0
DISCUSSION_POLL_SECONDS = 30
TICKET_POLL_SECONDS = 3


class TensologyApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, code: str = "request_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(slots=True)
class TensologyClient:
    base_url: str
    api_key: str
    source: str = "decisionsai"
    timeout: int = 30

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        approved: bool = False,
    ) -> Any:
        url = f"{self.base_url.rstrip('/')}/api/integrations/v1/{path.lstrip('/')}"
        if query:
            clean = {key: value for key, value in query.items() if value not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
            "X-Tensology-Source": self.source,
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if approved:
            headers["X-Tensology-Approval"] = "explicit"
        request = urllib.request.Request(url, data=encoded, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                envelope = json.loads(exc.read().decode("utf-8"))
                error = envelope.get("error") or {}
                message = error.get("message") or str(exc)
                code = error.get("code") or "http_error"
            except Exception:
                message, code = str(exc), "http_error"
            raise TensologyApiError(message, status=exc.code, code=code) from exc
        except Exception as exc:
            raise TensologyApiError(str(exc), code="unavailable") from exc
        error = envelope.get("error")
        if error:
            raise TensologyApiError(error.get("message", "Tensology request failed"), code=error.get("code", "request_failed"))
        return envelope.get("data")

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, query=query)

    def post(self, path: str, body: dict[str, Any], *, idempotency_key: str, approved: bool = False) -> Any:
        return self._request("POST", path, body=body, idempotency_key=idempotency_key, approved=approved)

    def patch(
        self,
        path: str,
        body: dict[str, Any],
        *,
        idempotency_key: str,
        approved: bool = False,
    ) -> Any:
        return self._request(
            "PATCH",
            path,
            body=body,
            idempotency_key=idempotency_key,
            approved=approved,
        )


def configured_tensology_client(source: str = "decisionsai") -> TensologyClient:
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    if not settings.get("tensology_enabled"):
        raise TensologyApiError("Enable Tensology API in Settings before using Tensology tools.", code="not_configured")
    key = str(settings.get("tensology_key") or "").strip()
    if not key:
        raise TensologyApiError("Save a Tensology API key in Settings first.", code="not_configured")
    return TensologyClient(
        base_url=str(settings.get("tensology_url") or "https://www.tensology.com"),
        api_key=key,
        source=source,
    )


def send_tensology_heartbeat() -> None:
    """Keep the configured Tensology connection visibly online."""
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    provider = str(
        settings.get("project_cli_medium_model_provider")
        or settings.get("project_cli_medium_backend")
        or "codex"
    ).strip().lower()
    model = str(settings.get("project_cli_medium_model") or "auto").strip()
    remote = configured_tensology_client(source="decisionsai-heartbeat")._request(
        "POST",
        "presence",
        body={
            "machine_name": platform.node(),
            "account_username": getpass.getuser(),
            "default_provider": provider,
            "default_model": model,
        },
    )
    if not isinstance(remote, dict):
        return
    remote_provider = str(remote.get("default_provider") or "").strip().lower()
    remote_model = str(remote.get("default_model") or "").strip()
    if not remote_provider or not remote_model:
        return
    from distr.core.project_cli_backends import normalize_backend_id
    from distr.core.settings import save_settings_to_db

    remote_provider = normalize_backend_id(remote_provider)
    if (provider, model) == (remote_provider, remote_model):
        return
    # Presence is a report, not a routing authority: only seed the local medium
    # route from Tensology when it has never been configured locally.
    local_backend = str(settings.get("project_cli_medium_backend") or "").strip()
    local_model = str(settings.get("project_cli_medium_model") or "").strip().lower()
    if local_backend and local_model not in {"", "auto"}:
        import logging

        logging.getLogger(__name__).info(
            "Tensology presence suggested medium route %s/%s; keeping locally configured route.",
            remote_provider,
            remote_model,
        )
        return
    save_settings_to_db({
        "project_cli_medium_backend": remote_provider,
        "project_cli_medium_model": remote_model,
    })


def process_tensology_discussion_assessments(*, force: bool = False) -> int:
    """Run bounded local LAYA shadow assessments without granting mutation authority."""
    global _LAST_DISCUSSION_POLL
    now = time.monotonic()
    if not force and now - _LAST_DISCUSSION_POLL < DISCUSSION_POLL_SECONDS:
        return 0
    _LAST_DISCUSSION_POLL = now
    client = configured_tensology_client(source="decisionsai-laya-shadow")
    pending = client.get("discussion-assessments")
    if not isinstance(pending, list):
        return 0
    from distr.core.laya_runtime import LAYA_VERSION, assess_discussion

    completed = 0
    for item in pending[:10]:
        assessment_id = item.get("id") if isinstance(item, dict) else None
        if not assessment_id:
            continue
        try:
            result = assess_discussion(item)
        except Exception as exc:
            result = {
                "status": "failed",
                "version": LAYA_VERSION,
                "latency_ms": 0,
                "error": str(exc)[:500],
            }
        client.post(
            f"discussion-assessments/{assessment_id}",
            result,
            idempotency_key=f"laya-shadow-{assessment_id}",
        )
        completed += 1
    return completed


def process_tensology_discussion_context(*, force: bool = False) -> int:
    """Fulfil signed read-only repository searches for scoped discussion assessments."""
    global _LAST_CONTEXT_POLL
    now = time.monotonic()
    if not force and now - _LAST_CONTEXT_POLL < DISCUSSION_POLL_SECONDS:
        return 0
    _LAST_CONTEXT_POLL = now
    client = configured_tensology_client(source="decisionsai-discussion-context")
    pending = client.get("discussion-context")
    if not isinstance(pending, list):
        return 0
    from distr.core.agent.tools.integrations.tensology_workspace import TensologyWorkspaceTool

    completed = 0
    tool = TensologyWorkspaceTool()
    for request in pending[:10]:
        assessment_id = request.get("assessment_id") if isinstance(request, dict) else None
        tasks = request.get("tasks") if isinstance(request, dict) else None
        if not assessment_id:
            continue
        evidence = []
        errors = []
        for task in (tasks or [])[:8]:
            raw = tool._run(
                action="repository_search",
                params={
                    "project_id": request.get("project_id"),
                    "repository_id": task.get("repository_id"),
                    "query": task.get("query"),
                    "context_token": task.get("context_token"),
                    "source": "decisionsai-discussion-context",
                },
            )
            try:
                result = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                errors.append(str(raw)[:500])
                continue
            for match in result.get("matches") or []:
                snippets = match.get("snippets") or [{"line": 1, "text": "Filename matched the scoped query."}]
                for snippet in snippets:
                    evidence.append({
                        "repository_id": task.get("repository_id"),
                        "repository_name": task.get("repository_name"),
                        "path": match.get("path"),
                        "line": snippet.get("line") or 1,
                        "text": snippet.get("text") or "",
                    })
                    if len(evidence) == 50:
                        break
                if len(evidence) == 50:
                    break
            if len(evidence) == 50:
                break
        body = {"status": "complete", "evidence": evidence}
        if not tasks:
            body = {"status": "failed", "error": "No registered repository on this connected machine is in ticket scope."}
        elif not evidence:
            body = {"status": "failed", "error": "No repository evidence matched the scoped ticket query."}
        if errors and not evidence:
            body = {"status": "failed", "error": errors[0]}
        client.post(
            f"discussion-context/{assessment_id}",
            body,
            idempotency_key=f"discussion-context-{assessment_id}",
        )
        completed += 1
    return completed


def _tensology_thread_identity(ticket: dict[str, Any]) -> str:
    from distr.core.workflow.development_threads import development_identity_key

    project_id = int(ticket.get("project_id") or 0)
    return development_identity_key(
        board_provider="tensology",
        board_key=f"tensology:{project_id}",
        board_ticket_key=str(ticket.get("reference") or ""),
    )


def _existing_tensology_thread(ticket: dict[str, Any]) -> int | None:
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentWorkItem

    identity = _tensology_thread_identity(ticket)
    if not identity:
        return None
    with get_session() as db:
        row = db.query(DevelopmentWorkItem).filter(
            DevelopmentWorkItem.identity_key == identity
        ).first()
        return int(row.chat_id) if row else None


def _resolve_tensology_project(ticket: dict[str, Any]):
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    name = str(ticket.get("project_name") or "").strip()
    with get_session() as db:
        project = db.query(Project).filter(Project.name.ilike(name)).first() if name else None
        if project is None:
            return None
        snapshot = {
            "id": int(project.id),
            "name": project.name,
            "folder_location": project.folder_location or "",
            "start_time_tracker": bool(project.start_time_tracker),
        }
    folder = Path(snapshot["folder_location"]).expanduser() if snapshot["folder_location"] else None
    if not folder or not folder.is_dir():
        return None
    return snapshot


def _local_api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token

    encoded = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:8765/api/{path.lstrip('/')}",
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            INTERNAL_AUTH_HEADER: get_internal_api_token(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise TensologyApiError(
            f"DecisionsAI thread creation failed: {detail or exc.reason}",
            status=exc.code,
            code="local_dispatch_failed",
        ) from exc


def _execution_prompt(ticket: dict[str, Any], complexity: str) -> str:
    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    criteria = ticket.get("success_criteria") if isinstance(ticket.get("success_criteria"), list) else []
    task_lines = "\n".join(f"- {str(item)}" for item in tasks if str(item).strip()) or "- Derive implementation tasks from the approved scope."
    criteria_lines = "\n".join(f"- {str(item)}" for item in criteria if str(item).strip()) or "- The approved request is implemented and verified without unrelated changes."
    return (
        f"Execute approved Tensology ticket {ticket.get('reference')}: {ticket.get('title')}\n\n"
        f"Complexity: {complexity}.\n"
        f"Description:\n{ticket.get('description') or 'No additional description.'}\n\n"
        f"Approved implementation plan:\n{ticket.get('implementation_plan') or 'Use the repository evidence and approved scope.'}\n\n"
        f"Tasks:\n{task_lines}\n\n"
        f"Acceptance criteria:\n{criteria_lines}\n\n"
        "Treat this as one explicit goal. Follow these observable gates in order: Development, Testing, "
        "Polish or refinement only when evidence shows it is needed, Retesting when refinement changed behavior, "
        "Final verification, then Done. At the start of each gate, emit exactly one line in the form "
        "[DECISIONS_STAGE: development], [DECISIONS_STAGE: testing], [DECISIONS_STAGE: refinement], "
        "[DECISIONS_STAGE: retesting], [DECISIONS_STAGE: verification], or [DECISIONS_STAGE: done]. "
        "Run the repository's required tests and build. Do not emit the done marker "
        "until the final verification passes. If blocked, stop and report the concrete blocker. Preserve unrelated "
        "working-tree changes and work on main. Record material changes, failed checks, corrections, phase evidence, "
        "and reusable lessons in the workflow run and project memory so later work does not repeat the same mistake."
    )


def _execution_skill_ids(ticket: dict[str, Any]) -> tuple[list[str], bool]:
    """Return the mandatory quality skills and browser requirement for a ticket."""
    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    criteria = ticket.get("success_criteria") if isinstance(ticket.get("success_criteria"), list) else []
    text = "\n".join(
        str(value or "")
        for value in (
            ticket.get("title"),
            ticket.get("description"),
            ticket.get("implementation_plan"),
            *tasks,
            *criteria,
        )
    ).lower()
    ui_terms = (
        " ui ", "ui/ux", "user interface", "user experience", "frontend", "front-end",
        "screen", "page", "responsive", "mobile", "desktop", "viewport", "modal",
        "form", "button", "table", "dashboard", "navigation", "accessibility",
        "notification", "settings", "admin", "list view", "record editor",
    )
    normalized = f" {text} "
    ui_bearing = any(term in normalized for term in ui_terms)
    skills = ["completion-protocol"]
    if ui_bearing:
        skills.extend(["ui-ux-principles", "ui-crud-discipline"])
    return skills, ui_bearing


def _ensure_tensology_kanban_ticket(
    ticket: dict[str, Any],
    project: dict[str, Any],
    workflow_id: int,
) -> int:
    """Mirror an external Tensology ticket so workflow context survives compaction."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    reference = str(ticket.get("reference") or "").strip()
    if not reference:
        raise TensologyApiError("Tensology ticket reference is required.", code="ticket_reference_missing")
    with get_session() as db:
        board = (
            db.query(KanbanBoard)
            .filter(KanbanBoard.default_project_id == int(project["id"]), KanbanBoard.archived.is_(False))
            .order_by(KanbanBoard.id.asc())
            .first()
        )
        if board is None:
            board = KanbanBoard(
                name=str(project.get("name") or "Tensology project"),
                description=f"Board for project: {project.get('name') or project['id']}",
                source="database",
                default_project_id=int(project["id"]),
                default_workflow_id=int(workflow_id),
                in_use=True,
                archived=False,
            )
            db.add(board)
            db.flush()
            for position, name in enumerate(("Backlog", "In Progress", "QA", "Complete")):
                db.add(KanbanLane(board_id=int(board.id), name=name, position=position))
            db.flush()
        elif board.default_workflow_id != int(workflow_id):
            board.default_workflow_id = int(workflow_id)

        mirrored = (
            db.query(KanbanTicket)
            .filter(
                KanbanTicket.external_source == "tensology",
                KanbanTicket.external_id == reference,
            )
            .first()
        )
        if mirrored is None:
            lane = (
                db.query(KanbanLane)
                .filter(KanbanLane.board_id == int(board.id))
                .order_by(KanbanLane.position.asc(), KanbanLane.id.asc())
                .first()
            )
            if lane is None:
                raise TensologyApiError("The DecisionsAI project board has no lane.", code="project_board_invalid")
            tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
            criteria = ticket.get("success_criteria") if isinstance(ticket.get("success_criteria"), list) else []
            context = {
                "provider": "tensology",
                "project_id": ticket.get("project_id"),
                "reference": reference,
                "implementation_plan": ticket.get("implementation_plan") or "",
                "tasks": tasks,
                "acceptance_criteria": criteria,
            }
            mirrored = KanbanTicket(
                lane_id=int(lane.id),
                title=str(ticket.get("title") or reference),
                description=str(ticket.get("description") or ""),
                priority=str(ticket.get("priority") or "medium"),
                complexity=str(ticket.get("execution_complexity") or "medium"),
                external_source="tensology",
                external_id=reference,
                external_url=str(ticket.get("discussion_url") or ""),
                source_provider="tensology",
                source_external_id=reference,
                source_url=str(ticket.get("discussion_url") or ""),
                source_label=f"Tensology {reference}",
                context_notes=json.dumps(context, ensure_ascii=False),
                linked_workflow_id=int(workflow_id),
                linked_project_id=int(project["id"]),
                send_to_cli=True,
            )
            db.add(mirrored)
            db.flush()
        else:
            mirrored.linked_workflow_id = int(workflow_id)
            mirrored.linked_project_id = int(project["id"])
            mirrored.title = str(ticket.get("title") or mirrored.title)
            mirrored.description = str(ticket.get("description") or mirrored.description or "")
        db.commit()
        return int(mirrored.id)


def _create_tensology_thread(ticket: dict[str, Any], project: dict[str, Any]) -> tuple[int, dict[str, str]]:
    from distr.core.kanban.ticket_policy import resolve_ticket_cli_route, resolve_ticket_complexity
    from distr.core.workflow.developer_workflow import get_or_create_development_workflow

    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    complexity = resolve_ticket_complexity(
        str(ticket.get("title") or ""),
        str(ticket.get("description") or ""),
        requested=str(ticket.get("execution_complexity") or "auto"),
        todo_count=len(tasks),
    )
    route = resolve_ticket_cli_route(None, complexity, board=None)
    prompt = _execution_prompt(ticket, complexity)
    workflow_id = get_or_create_development_workflow()
    mirrored_ticket_id = _ensure_tensology_kanban_ticket(ticket, project, workflow_id)
    skill_ids, use_playwright = _execution_skill_ids(ticket)
    response = _local_api_post(
        "workflows/studio/tasks",
        {
            "prompt": prompt,
            "title": f"{ticket.get('reference')} - {ticket.get('title')}",
            "project_id": project["id"],
            "workflow_id": workflow_id,
            "ticket_id": mirrored_ticket_id,
            "board_key": f"tensology:{int(ticket.get('project_id') or 0)}",
            "board_provider": "tensology",
            "board_ticket_key": str(ticket.get("reference") or ""),
            "board_ticket_title": str(ticket.get("title") or ""),
            "board_ticket_lane": "Approved",
            "route_mode": "manual",
            "execution_profile": "code",
            "autonomy_level": "goal",
            "permission_mode": "standard",
            "backend": route.get("backend") or "pi",
            "provider": route.get("model_provider") or route.get("backend") or "ollama",
            "model_name": route.get("model") or "auto",
            "reasoning_effort": route.get("codex_reasoning_effort") or ("xhigh" if complexity == "high" else "high" if complexity == "medium" else "medium"),
            "service_tier": route.get("codex_service_tier") or "standard",
            "routing_assessment": {
                "complexity": complexity,
                "source": "tensology_approval",
                "reason": f"Approved ticket classified as {complexity} before execution.",
            },
            "skill_ids": skill_ids,
            "use_playwright": use_playwright,
            # The upstream ticket already carries explicit human approval.
            "skip_human_checkpoints": True,
        },
    )
    task = response.get("task") if isinstance(response, dict) else None
    chat_id = int((task or {}).get("id") or 0)
    if not chat_id:
        raise TensologyApiError("DecisionsAI did not return a Development thread id.", code="thread_missing")
    return chat_id, {
        "complexity": complexity,
        "backend": str(route.get("backend") or "pi"),
        "provider": str(route.get("model_provider") or route.get("backend") or ""),
        "model": str(route.get("model") or "auto"),
    }


def _execution_text(execution: dict[str, Any]) -> str:
    packet = execution.get("output_packet") if isinstance(execution.get("output_packet"), dict) else {}
    return "\n".join(
        str(value or "")
        for value in (
            execution.get("streamed_output"), execution.get("summary"), execution.get("error"),
            packet.get("summary"), packet.get("output"),
        )
    )


def _remote_execution_status(local_status: str, execution: dict[str, Any] | None = None) -> str:
    status = str(local_status or "").strip().lower()
    evidence = _execution_text(execution or {})
    markers = re.findall(
        r"\[DECISIONS_STAGE:\s*(development|testing|refinement|retesting|verification|done)\s*\]",
        evidence,
        flags=re.IGNORECASE,
    )
    marked_stage = markers[-1].lower() if markers else ""
    if status in {"initializing", "queued", "running", "paused"}:
        return marked_stage if marked_stage and marked_stage != "done" else "development"
    if status == "waiting":
        return "blocked"
    if status in {"completed", "completed_noop"}:
        verification = execution.get("verification") if isinstance(execution.get("verification"), dict) else {}
        verified = marked_stage == "done" or bool(
            re.search(r"final verification.{0,80}(?:passed|successful|complete)", evidence, flags=re.IGNORECASE | re.DOTALL)
        ) or str(verification.get("final_verdict") or "").strip().lower() == "pass"
        return "done" if verified else "failed"
    if status in {"failed", "cancelled"}:
        return "failed"
    return "queued"


def _tensology_execution_receipt(execution: dict[str, Any], *, chat_id: int) -> dict[str, Any]:
    """Return a redacted, portable proof packet for the canonical Tensology ticket."""
    receipt = execution.get("route_receipt") if isinstance(execution.get("route_receipt"), dict) else {}
    selected = receipt.get("selected") if isinstance(receipt.get("selected"), dict) else {}
    actual = receipt.get("actual") if isinstance(receipt.get("actual"), dict) else {}
    try:
        from distr.core.workflow.studio_artifacts import list_studio_artifacts

        artifacts = list_studio_artifacts(int(chat_id))
    except Exception:
        artifacts = []
    return {
        "job_id": str(execution.get("job_id") or execution.get("id") or ""),
        "route": {
            "complexity": str(receipt.get("complexity") or ""),
            "decision_engine": str(receipt.get("decision_engine") or ""),
            "selected": {key: selected.get(key) for key in ("backend", "provider", "model", "reasoning_effort", "service_tier") if selected.get(key) not in (None, "")},
            "actual": {key: actual.get(key) for key in ("backend", "provider", "model", "runtime") if actual.get(key) not in (None, "")},
        },
        "artifacts": [
            {
                "title": str(item.get("title") or item.get("artifact_type") or "Artifact")[:160],
                "type": str(item.get("artifact_type") or ""),
                "status": str(item.get("status") or ""),
                "format": str(item.get("content_format") or ""),
            }
            for item in artifacts[:30]
            if isinstance(item, dict)
        ],
        "verification": execution.get("verification") if isinstance(execution.get("verification"), dict) else {},
        "blocker": str(execution.get("error") or execution.get("worker_question") or "")[:500],
    }


def process_tensology_approved_tickets(*, force: bool = False) -> int:
    """Create/reuse visible Development threads and mirror their execution state."""
    global _LAST_TICKET_POLL
    now = time.monotonic()
    if not force and now - _LAST_TICKET_POLL < TICKET_POLL_SECONDS:
        return 0
    _LAST_TICKET_POLL = now
    client = configured_tensology_client(source="decisionsai-ticket-runner")
    tickets = client.get(
        "tickets",
        query={
            "execution_status": "awaiting_setup,awaiting_decisions,queued,development,testing,refinement,retesting,verification,blocked"
        },
    )
    if not isinstance(tickets, list):
        return 0

    processed = 0
    for ticket in tickets[:20]:
        if not isinstance(ticket, dict):
            continue
        if not ticket.get("repository_ready"):
            continue
        project = _resolve_tensology_project(ticket)
        if project is None:
            continue
        chat_id = _existing_tensology_thread(ticket)
        route = {
            "complexity": str(ticket.get("execution_complexity") or ""),
            "backend": str(ticket.get("decisions_backend") or ""),
            "provider": str(ticket.get("decisions_provider") or ""),
            "model": str(ticket.get("decisions_model") or ""),
        }
        if chat_id is None:
            chat_id, route = _create_tensology_thread(ticket, project)

        thread_url = f"http://127.0.0.1:8765/development/threads/{chat_id}/"
        if str(ticket.get("decisions_thread_id") or "") != str(chat_id):
            client.post(
                f"tickets/{int(ticket['id'])}/execution",
                {
                    "thread_id": str(chat_id),
                    "decisions_project_id": str(project["id"]),
                    "thread_url": thread_url,
                    "status": "development",
                    "detail": "Development thread created and execution started.",
                    **route,
                },
                idempotency_key=f"tensology-ticket-{int(ticket['id'])}-thread-{chat_id}",
            )
        else:
            from distr.core.workflow.development_harness import development_execution_state

            execution = development_execution_state(chat_id)
            remote_status = _remote_execution_status(str(execution.get("status") or ""), execution)
            if remote_status != str(ticket.get("execution_status") or ""):
                detail = str(execution.get("summary") or execution.get("error") or "")[:500]
                if remote_status == "failed" and str(execution.get("status") or "").lower() in {"completed", "completed_noop"}:
                    detail = "Execution stopped without final verification evidence. Review the Development thread before retrying."
                client.patch(
                    f"tickets/{int(ticket['id'])}/execution",
                    {
                        "thread_id": str(chat_id),
                        "status": remote_status,
                        "detail": detail,
                        "evidence": _tensology_execution_receipt(execution, chat_id=chat_id),
                    },
                    idempotency_key=f"tensology-ticket-{int(ticket['id'])}-status-{remote_status}-{execution.get('job_id') or 'run'}",
                )
        processed += 1
    return processed
