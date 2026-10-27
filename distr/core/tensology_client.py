from __future__ import annotations

import json
import getpass
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import threading
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any


_LAST_DISCUSSION_POLL = 0.0
_LAST_CONTEXT_POLL = 0.0
_LAST_TICKET_POLL = 0.0
DISCUSSION_POLL_SECONDS = 30
TICKET_POLL_SECONDS = 3  # legacy constant; tickets now arrive by push (distr.core.tensology_stream)
TICKET_POLL_ACTIVE_SECONDS = 3
TICKET_POLL_IDLE_SECONDS = 15
_TICKET_POLL_INTERVAL = TICKET_POLL_IDLE_SECONDS
_LAST_STATE_SYNC = 0.0
STATE_SYNC_SECONDS = 3  # local-only thread state check; Tensology is called only on change
TENSOLOGY_LIVE_TICKET_STATUSES = (
    "awaiting_setup,awaiting_decisions,queued,development,testing,refinement,retesting,verification,blocked"
)
TENSOLOGY_LIVE_TICKET_STATUS_SET = frozenset(TENSOLOGY_LIVE_TICKET_STATUSES.split(","))
_TENSOLOGY_TICKETS: dict[int, dict[str, Any]] = {}
_TENSOLOGY_TICKETS_LOCK = threading.Lock()
_TICKET_PROCESS_LOCK = threading.Lock()
ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tif", ".tiff"})
# Sentinel used when reporting a routing block before any thread exists. PATCH
# updates status without claiming, so a later real thread can still POST-claim.
ROUTE_BLOCKED_THREAD_ID = "decisions-route-blocked"


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

    def download(self, location: str, destination: Path, *, max_bytes: int = ATTACHMENT_MAX_BYTES) -> int:
        """Download a ticket attachment. The API key is only ever sent to the Tensology host."""
        base = urllib.parse.urlsplit(self.base_url)
        if location.startswith(("http://", "https://")):
            url = location
        elif location.startswith("/"):
            url = f"{self.base_url.rstrip('/')}{location}"
        else:
            url = f"{self.base_url.rstrip('/')}/api/integrations/v1/{location.lstrip('/')}"
        target = urllib.parse.urlsplit(url)
        headers = {"Accept": "*/*", "X-Request-ID": str(uuid.uuid4()), "X-Tensology-Source": self.source}
        if target.netloc.lower().removeprefix("www.") == base.netloc.lower().removeprefix("www."):
            headers["Authorization"] = f"Bearer {self.api_key}"
        destination = Path(destination)
        partial = destination.with_name(destination.name + ".part")
        request = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                declared = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
                if declared and str(declared).isdigit() and int(declared) > max_bytes:
                    raise TensologyApiError(f"Attachment is larger than {max_bytes} bytes.", code="attachment_too_large")
                total = 0
                with open(partial, "wb") as handle:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise TensologyApiError(f"Attachment is larger than {max_bytes} bytes.", code="attachment_too_large")
                        handle.write(chunk)
            if total == 0:
                raise TensologyApiError("Attachment download was empty.", code="attachment_empty")
            partial.replace(destination)
            return total
        except urllib.error.HTTPError as exc:
            raise TensologyApiError(f"Attachment download failed: HTTP {exc.code}", status=exc.code, code="attachment_http_error") from exc
        except TensologyApiError:
            raise
        except Exception as exc:
            raise TensologyApiError(f"Attachment download failed: {exc}", code="attachment_unavailable") from exc
        finally:
            if partial.exists():
                partial.unlink()

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


def fetch_pending_work_requests() -> list[dict[str, Any]]:
    """Pending Tensology work proposals awaiting a decision (queue / send / dismiss).

    Best-effort: never raises for a missing key or a Tensology outage, so it is safe
    to call while building the day plan.
    """
    try:
        client = configured_tensology_client()
    except TensologyApiError:
        return []
    try:
        data = client.get("work-requests", query={"status": "pending"})
    except TensologyApiError:
        return []
    return [item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)]


def decide_work_request(proposal_id: int, action: str) -> dict[str, Any]:
    """Queue, send to Decisions, or dismiss one pending Tensology proposal."""
    normalized = str(action or "").strip().lower()
    aliases = {"run": "send_to_decisions", "ticket": "queue", "queue": "queue",
               "dismiss": "dismiss", "send": "send_to_decisions", "send_to_decisions": "send_to_decisions"}
    resolved = aliases.get(normalized, normalized)
    if resolved not in {"queue", "send_to_decisions", "dismiss"}:
        raise TensologyApiError("Action must be run, queue, or dismiss.", code="invalid_action")
    client = configured_tensology_client()
    return client.post(
        f"work-requests/{int(proposal_id)}/action",
        {"action": resolved},
        idempotency_key=f"work-request-{int(proposal_id)}-{resolved}",
    )


_PROPOSAL_DECISION_RE = re.compile(r"^\s*(run|queue|dismiss|send)\s+(all|\d+)\s*$", re.IGNORECASE)
_PROPOSAL_VERB_ACTION = {
    "run": "send_to_decisions",
    "send": "send_to_decisions",
    "queue": "queue",
    "dismiss": "dismiss",
}
_PROPOSAL_ACTION_PHRASE = {
    "send_to_decisions": "queued for Decisions",
    "queue": "sent to the ticket queue",
    "dismiss": "dismissed",
}


def handle_proposal_decision_reply(text: str) -> str | None:
    """Resolve a plain "run 1", "queue 2" or "dismiss all" reply against pending proposals.

    Returns a short human acknowledgement, or None when the text is not a proposal
    decision (so other handlers keep it).
    """
    match = _PROPOSAL_DECISION_RE.match(str(text or ""))
    if not match:
        return None
    action = _PROPOSAL_VERB_ACTION[match.group(1).lower()]
    target = match.group(2).lower()

    proposals = fetch_pending_work_requests()
    if not proposals:
        return "There are no Tensology proposals waiting right now."
    if target == "all":
        chosen = proposals
    else:
        index = int(target)
        if index < 1 or index > len(proposals):
            return f"There's no proposal {index}. {len(proposals)} are waiting."
        chosen = [proposals[index - 1]]

    applied, failed = [], []
    for proposal in chosen:
        title = " ".join(str(proposal.get("title") or "").split()) or f"proposal {proposal.get('id')}"
        try:
            decide_work_request(int(proposal["id"]), action)
            applied.append(title)
        except Exception as exc:
            failed.append(f"{title} ({exc})")

    phrase = _PROPOSAL_ACTION_PHRASE[action]
    if not applied:
        return f"I couldn't action those proposals: {'; '.join(failed)}"
    summary = f"Done — {len(applied)} proposal(s) {phrase}: {'; '.join(applied)}."
    if failed:
        summary += f" Not applied: {'; '.join(failed)}."
    return summary


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
    """Dedupe by Tensology ticket: a thread already claimed for this ticket is never recreated."""
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentWorkItem

    claimed = str(ticket.get("decisions_thread_id") or "").strip()
    if claimed.isdigit():
        return int(claimed)
    identity = _tensology_thread_identity(ticket)
    if not identity:
        return None
    with get_session() as db:
        row = db.query(DevelopmentWorkItem).filter(
            DevelopmentWorkItem.identity_key == identity
        ).first()
        return int(row.chat_id) if row else None


def _resolve_tensology_project(ticket: dict[str, Any]):
    """Match the local project by ``decisions_project_id`` first, then by exact (case-insensitive) name."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    raw_id = str(ticket.get("decisions_project_id") or "").strip()
    name = str(ticket.get("project_name") or "").strip()
    with get_session() as db:
        project = None
        if raw_id.isdigit():
            project = db.get(Project, int(raw_id))
        if project is None and name:
            project = db.query(Project).filter(Project.name.ilike(name)).first()
        if project is None:
            return None
        snapshot = {
            "id": int(project.id),
            "name": project.name,
            "folder_location": project.folder_location or "",
            "start_time_tracker": bool(project.start_time_tracker),
            "coding_backend": str(getattr(project, "coding_backend", "") or "").strip(),
            "coding_backend_model": str(getattr(project, "coding_backend_model", "") or "").strip(),
        }
    folder = Path(snapshot["folder_location"]).expanduser() if snapshot["folder_location"] else None
    if not folder or not folder.is_dir():
        return None
    return snapshot


def _usable_repository_folder(raw_path: str, search_roots: list[Path] | None = None) -> Path | None:
    """Use the registered path when it exists here, otherwise the same folder name beside other local projects."""
    folder = Path(raw_path).expanduser()
    if folder.is_dir():
        return folder
    name = folder.name
    if not name or name in {".", ".."}:
        return None
    roots = _local_project_roots() if search_roots is None else search_roots
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in (name, f"*/{name}"):
            for candidate in root.glob(pattern):
                if not candidate.is_dir() or "OLD" in candidate.parts or candidate in matches:
                    continue
                matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    return None


def _local_project_roots() -> list[Path]:
    """Parents of project folders already on this machine."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    roots: list[Path] = []
    seen: set[Path] = set()
    with get_session() as db:
        locations = db.query(Project.folder_location).all()
    for (location,) in locations:
        if not location:
            continue
        parent = Path(location).expanduser().parent
        if parent in seen or not parent.is_dir():
            continue
        seen.add(parent)
        roots.append(parent)
    return roots


def _ticket_repository_folder(client: TensologyClient, ticket: dict[str, Any]) -> Path | None:
    """Return the repository directory on this machine, including when the registered path has moved."""
    raw_project_id = str(ticket.get("project_id") or "").strip()
    if not raw_project_id.isdigit():
        return None
    try:
        repositories = client.get("repositories", query={"project_id": int(raw_project_id)})
    except Exception:
        repositories = []
    if not isinstance(repositories, list):
        repositories = []
    for repository in repositories:
        if not isinstance(repository, dict):
            continue
        raw_path = str(repository.get("local_path_hint") or "").strip()
        if not raw_path:
            continue
        folder = _usable_repository_folder(raw_path)
        if folder is not None:
            return folder
    for domain in (ticket.get("project_dev_domain"), ticket.get("project_domain")):
        domain = str(domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not domain:
            continue
        for candidate in (domain, domain if domain.startswith("www.") else f"www.{domain}"):
            folder = _usable_repository_folder(candidate)
            if folder is not None:
                return folder
    return None


def _settings_high_route() -> tuple[str, str]:
    """Return the hosted high coding route from Decisions settings."""
    from distr.core.db import Settings, get_session

    with get_session() as db:
        row = db.query(Settings).first()
        if row is None:
            return "", ""
        return (
            str(row.project_cli_high_backend or "").strip(),
            str(row.project_cli_high_model or "").strip(),
        )


def _create_tensology_project(client: TensologyClient, ticket: dict[str, Any]) -> dict[str, Any] | None:
    """Create or repair the local project from the Tensology repository path and the high route."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.project_cli_backends.timing import is_local_model_route, resolve_pi_model_provider

    name = str(ticket.get("project_name") or "").strip()
    folder = _ticket_repository_folder(client, ticket)
    if not name or folder is None:
        return None
    backend, model = _settings_high_route()
    # Pi runs OpenRouter-style ``vendor/model`` ids through OpenRouter, not
    # through a provider named after the vendor prefix.
    provider = resolve_pi_model_provider(model) if backend.lower() == "pi" and "/" in model else backend
    if not backend or not model or is_local_model_route(backend_id=backend, model=model, provider=provider):
        return None
    with get_session() as db:
        project = db.query(Project).filter(Project.name.ilike(name)).first()
        if project is None:
            db.add(Project(
                name=name,
                folder_location=str(folder),
                coding_backend=backend,
                coding_backend_model=model,
                start_time_tracker=True,
                in_use=False,
            ))
        else:
            current = Path(project.folder_location or "").expanduser()
            if current.is_dir():
                return None
            project.folder_location = str(folder)
            project.coding_backend = backend
            project.coding_backend_model = model
    return _resolve_tensology_project(ticket)


class TensologyRouteError(TensologyApiError):
    """The project has no usable (non-local) route; never fall back to a local free model."""


def _tensology_project_route(project: dict[str, Any]) -> dict[str, str]:
    """Return the project's configured route or raise; local/free models (pi/ollama) are refused."""
    from distr.core.project_cli_backends.timing import is_local_model_route, resolve_pi_model_provider

    backend = str(project.get("coding_backend") or "").strip().lower()
    model = str(project.get("coding_backend_model") or "").strip()
    name = str(project.get("name") or "project")
    if not backend or not model:
        raise TensologyRouteError(
            f"No default coding route is configured for '{name}'. Set a non-local backend and model on the "
            "DecisionsAI project before this ticket can run.",
            code="route_not_configured",
        )
    configured_provider = str(project.get("coding_backend_provider") or project.get("model_provider") or "").strip()
    provider = (
        resolve_pi_model_provider(model, configured_provider)
        if backend == "pi" and "/" in model
        else (configured_provider.lower() or backend)
    )
    if is_local_model_route(backend_id=backend, model=model, provider=provider):
        raise TensologyRouteError(
            f"'{name}' is routed to a local free model ({backend}/{model}), which is not allowed for "
            "Tensology tickets. Choose a hosted backend/model for this project.",
            code="route_not_allowed",
        )
    return {"backend": backend, "provider": provider, "model": model}


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


_OPS_INCIDENT_RE = re.compile(
    r"(?:\b(?:http|status|error)\s*(?:code\s*)?50[0234]\b"
    r"|\b50[0234]\s+(?:internal\s+server\s+error|bad\s+gateway|service\s+unavailable|gateway\s+time-?out)"
    r"|\binternal\s+server\s+error\b"
    r"|\bconnection\s+refused\b|\bconnectionrefusederror\b|\becconnrefused\b"
    r"|\bservice\s+(?:is\s+)?(?:down|unavailable|unreachable|not\s+responding)\b"
    r"|\bsite\s+(?:is\s+)?down\b|\boutage\b"
    r"|\b(?:redis|postgres(?:ql)?|mysql|database|db|cache|celery|rabbitmq)\b[^.\n]{0,60}"
    r"\b(?:refused|unavailable|down|timed?\s*out|connection\s+error|not\s+reachable)\b"
    r"|\boperationalerror\b|\bdjango\b[^.\n]{0,40}\berror\s+email\b)",
    re.IGNORECASE,
)


def _ticket_text(ticket: dict[str, Any]) -> str:
    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    criteria = ticket.get("success_criteria") if isinstance(ticket.get("success_criteria"), list) else []
    return "\n".join(
        str(value or "")
        for value in (
            ticket.get("title"),
            ticket.get("description"),
            ticket.get("implementation_plan"),
            *tasks,
            *criteria,
        )
    )


def _is_ops_incident(ticket: dict[str, Any]) -> bool:
    """Runtime/ops incidents (500s, service down, connection refused)."""
    return bool(_OPS_INCIDENT_RE.search(_ticket_text(ticket)))


def _repo_default_branch(folder: str | Path | None) -> str:
    """Detect the repository's default branch instead of assuming ``main``."""
    import subprocess

    path = Path(str(folder or "")).expanduser()
    if not str(folder or "").strip() or not path.is_dir():
        return "main"

    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    remote_head = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if remote_head:
        return remote_head.split("/", 1)[-1] or "main"
    for candidate in ("main", "master"):
        if git("rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"):
            return candidate
    return git("branch", "--show-current") or "main"


_OPS_INCIDENT_PROFILE = (
    "\n\nIncident profile: investigate-and-report. This ticket reports a runtime/ops incident "
    "(server error, service down, or connection refused). Investigate first: reproduce or read the "
    "error evidence, then decide whether the root cause is infrastructure/configuration (a service "
    "or dependency down, credentials, environment) or a code defect. Do not change code for an "
    "infrastructure cause. Only make a small, targeted code fix when the evidence shows a code defect "
    "(for example missing error handling around an unavailable dependency). Playwright, screenshots, "
    "and visual/UI gates do not apply; mark them N/A. Finish with a human report: root cause, evidence, "
    "impact, what was changed (if anything), and the recommended human action."
)


def _execution_prompt(
    ticket: dict[str, Any],
    complexity: str,
    *,
    default_branch: str = "main",
    ops_incident: bool | None = None,
) -> str:
    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    criteria = ticket.get("success_criteria") if isinstance(ticket.get("success_criteria"), list) else []
    task_lines = "\n".join(f"- {str(item)}" for item in tasks if str(item).strip()) or "- Derive implementation tasks from the approved scope."
    criteria_lines = "\n".join(f"- {str(item)}" for item in criteria if str(item).strip()) or "- The approved request is implemented and verified without unrelated changes."
    source = ticket.get("source") if isinstance(ticket.get("source"), dict) else {}
    attachments = ticket.get("attachments") if isinstance(ticket.get("attachments"), list) else []
    discussion = ticket.get("discussion") if isinstance(ticket.get("discussion"), list) else []
    delivery_context = json.dumps({
        "project_domain": ticket.get("project_domain") or "",
        "project_dev_domain": ticket.get("project_dev_domain") or "",
        "source": source,
        "attachments": attachments,
        "discussion": discussion,
        "discussion_truncated": bool(ticket.get("discussion_truncated")),
        "local_image_paths": ticket.get("local_image_paths") if isinstance(ticket.get("local_image_paths"), list) else [],
        "local_attachment_paths": ticket.get("local_attachment_paths") if isinstance(ticket.get("local_attachment_paths"), list) else [],
    }, ensure_ascii=False, indent=2, default=str)
    return (
        f"Execute approved Tensology ticket {ticket.get('reference')}: {ticket.get('title')}\n\n"
        f"Complexity: {complexity}.\n"
        f"Description:\n{ticket.get('description') or 'No additional description.'}\n\n"
        f"Approved implementation plan:\n{ticket.get('implementation_plan') or 'Use the repository evidence and approved scope.'}\n\n"
        f"Tasks:\n{task_lines}\n\n"
        f"Acceptance criteria:\n{criteria_lines}\n\n"
        f"Source, discussion, and attachment context:\n{delivery_context}\n\n"
        "Treat this as one explicit goal. Follow these observable gates in order: Development, Testing, "
        "Polish or refinement only when evidence shows it is needed, Retesting when refinement changed behavior, "
        "Final verification, then Done. At the start of each gate, emit exactly one line in the form "
        "[DECISIONS_STAGE: development], [DECISIONS_STAGE: testing], [DECISIONS_STAGE: refinement], "
        "[DECISIONS_STAGE: retesting], [DECISIONS_STAGE: verification], or [DECISIONS_STAGE: done]. "
        "Run the repository's required tests and build. Do not emit the done marker "
        "until the final verification passes. If blocked, stop and report the concrete blocker. Preserve unrelated "
        f"working-tree changes and work on {default_branch or 'main'} (the repository's default branch). "
        "Record material changes, failed checks, corrections, phase evidence, "
        "and reusable lessons in the workflow run and project memory so later work does not repeat the same mistake."
        + (_OPS_INCIDENT_PROFILE if (_is_ops_incident(ticket) if ops_incident is None else ops_incident) else "")
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
    # Ops incidents (500 / service down) are investigate-and-report work: the
    # word "page" or "admin" in an error email must not force Playwright gates.
    ui_bearing = any(term in normalized for term in ui_terms) and not _is_ops_incident(ticket)
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
                "project_domain": ticket.get("project_domain") or "",
                "project_dev_domain": ticket.get("project_dev_domain") or "",
                "source": ticket.get("source") if isinstance(ticket.get("source"), dict) else {},
                "attachments": ticket.get("attachments") if isinstance(ticket.get("attachments"), list) else [],
                "discussion": ticket.get("discussion") if isinstance(ticket.get("discussion"), list) else [],
                "local_image_paths": ticket.get("local_image_paths") if isinstance(ticket.get("local_image_paths"), list) else [],
                "local_attachment_paths": ticket.get("local_attachment_paths") if isinstance(ticket.get("local_attachment_paths"), list) else [],
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
    from distr.core.kanban.ticket_policy import resolve_ticket_complexity
    from distr.core.workflow.phases import route_ticket_workflow_id

    tasks = ticket.get("tasks") if isinstance(ticket.get("tasks"), list) else []
    complexity = resolve_ticket_complexity(
        str(ticket.get("title") or ""),
        str(ticket.get("description") or ""),
        requested=str(ticket.get("execution_complexity") or "auto"),
        todo_count=len(tasks),
    )
    # Raises TensologyRouteError before anything is created when the route is unset or local.
    route = _tensology_project_route(project)
    ops_incident = _is_ops_incident(ticket)
    prompt = _execution_prompt(
        ticket,
        complexity,
        default_branch=_repo_default_branch(project.get("folder_location")),
        ops_incident=ops_incident,
    )
    # Route by weight class instead of always using the retired 11-step pipeline.
    # A migration or auth change escalates to critical; a small fix stays cheap.
    workflow_id = route_ticket_workflow_id(
        f"{ticket.get('title') or ''}\n\n{ticket.get('description') or ''}",
        has_repo_target=True,
    )
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
            "backend": route["backend"],
            "provider": route["provider"],
            "model_name": route["model"],
            "reasoning_effort": "xhigh" if complexity == "high" else "high" if complexity == "medium" else "medium",
            "service_tier": "standard",
            "routing_assessment": {
                "complexity": complexity,
                "source": "tensology_approval",
                "reason": f"Approved ticket classified as {complexity}; routed by the project's default route.",
                **({"incident_profile": "investigate_and_report"} if ops_incident else {}),
            },
            "skill_ids": skill_ids,
            "use_playwright": use_playwright,
            # The founder already approved this ticket in Tensology by pressing Send to
            # Decisions, so start work straight away instead of pausing on the pre-run
            # briefing. The run can still be stopped or steered from the thread.
            "skip_human_checkpoints": True,
        },
    )
    task = response.get("task") if isinstance(response, dict) else None
    chat_id = int((task or {}).get("id") or 0)
    if not chat_id:
        raise TensologyApiError("DecisionsAI did not return a Development thread id.", code="thread_missing")
    # Say which policy picked this ticket and why, in the thread the founder opens.
    try:
        from distr.core.workflow.phases import announce_route_choice

        announce_route_choice(
            chat_id,
            f"{ticket.get('title') or ''}\n\n{ticket.get('description') or ''}",
            ticket_id=mirrored_ticket_id,
            has_repo_target=True,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).debug("tensology route announcement failed", exc_info=True)
    return chat_id, {"complexity": complexity, **route}


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


def _report_tensology_route_block(client: TensologyClient, ticket: dict[str, Any], project: dict[str, Any], exc: Exception) -> None:
    """Tell Tensology the ticket is blocked on routing (once per distinct reason); never run locally."""
    import logging

    detail = f"DecisionsAI did not start: {exc}"[:500]
    logging.getLogger(__name__).error(
        "Tensology ticket %s blocked on routing for project %s: %s", ticket.get("reference") or ticket.get("id"), project.get("id"), exc
    )
    if str(ticket.get("execution_status") or "") == "blocked" and str(ticket.get("execution_status_detail") or "") == detail:
        return
    client.patch(
        f"tickets/{int(ticket['id'])}/execution",
        {
            "thread_id": str(ticket.get("decisions_thread_id") or ROUTE_BLOCKED_THREAD_ID),
            "status": "blocked",
            "detail": detail,
        },
        idempotency_key=f"tensology-ticket-{int(ticket['id'])}-route-blocked-{getattr(exc, 'code', 'route')}",
    )


UNMATCHED_PROJECT_CODE = "project_not_matched"


def _report_tensology_unmatched_project(client: TensologyClient, ticket: dict[str, Any]) -> None:
    """No local project (or its folder is missing): warn and tell Tensology the ticket is blocked, once."""
    import logging

    name = str(ticket.get("project_name") or "").strip() or f"project {ticket.get('project_id')}"
    raw_id = str(ticket.get("decisions_project_id") or "").strip()
    detail = (
        f"DecisionsAI did not start: no local DecisionsAI project matches '{name}'"
        + (f" (decisions_project_id {raw_id})" if raw_id else "")
        + " or its folder is missing. Create/link the project in DecisionsAI with an existing folder."
    )[:500]
    logging.getLogger(__name__).warning(
        "Tensology ticket %s skipped: %s", ticket.get("reference") or ticket.get("id"), detail
    )
    if str(ticket.get("decisions_thread_id") or "").strip():
        return  # Already claimed by a thread; never overwrite its projection.
    if str(ticket.get("execution_status") or "") == "blocked" and str(ticket.get("execution_status_detail") or "") == detail:
        return
    client.patch(
        f"tickets/{int(ticket['id'])}/execution",
        {"thread_id": ROUTE_BLOCKED_THREAD_ID, "status": "blocked", "detail": detail},
        idempotency_key=f"tensology-ticket-{int(ticket['id'])}-route-blocked-{UNMATCHED_PROJECT_CODE}",
    )


def _remember_tensology_tickets(tickets: list[Any], *, replace: bool = False) -> None:
    """Keep the last known Tensology projection of live tickets for local state sync."""
    with _TENSOLOGY_TICKETS_LOCK:
        if replace:
            _TENSOLOGY_TICKETS.clear()
        for ticket in tickets:
            if not isinstance(ticket, dict) or ticket.get("id") in (None, ""):
                continue
            try:
                ticket_id = int(ticket["id"])
            except (TypeError, ValueError):
                continue
            if str(ticket.get("execution_status") or "") in TENSOLOGY_LIVE_TICKET_STATUS_SET:
                _TENSOLOGY_TICKETS[ticket_id] = dict(ticket)
            else:
                _TENSOLOGY_TICKETS.pop(ticket_id, None)


def _safe_attachment_part(value: Any, fallback: str = "unknown") -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip(".-")[:120] or fallback


def _is_image_attachment(item: dict[str, Any]) -> bool:
    mime = str(item.get("type") or item.get("mime_type") or item.get("content_type") or "").lower()
    if mime.startswith("image/"):
        return True
    name = str(item.get("name") or item.get("filename") or item.get("url") or "")
    return Path(urllib.parse.urlsplit(name).path).suffix.lower() in IMAGE_EXTENSIONS


def _tensology_attachment_root(project: dict[str, Any], ticket: dict[str, Any]) -> Path:
    """Same layout as Development thread ticket attachments (kanban._ticket_thread_attachment_root)."""
    scope = str(int(project["id"])) if project.get("id") not in (None, "") else "inbox"
    root = (
        Path.home() / ".decisions" / "workspaces" / "projects" / scope / "attachments" / "tickets"
        / "tensology"
        / _safe_attachment_part(ticket.get("project_id"))
        / _safe_attachment_part(ticket.get("reference") or ticket.get("id"))
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _has_download_location(item: dict[str, Any]) -> bool:
    return any(str(item.get(key) or "").strip() for key in ("api_path", "absolute_url", "url"))


def _localize_tensology_attachments(client: TensologyClient, ticket: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Download ticket and discussion attachments (images, voice notes, documents).

    Voice notes and images arrive from incoming WhatsApp proposals; audio is downloaded
    so the Development thread keeps the original recording alongside its transcript.
    Failures (404, too large, unreachable) are logged and recorded on the attachment as
    ``download_error``; they never block ticket processing.
    """
    import copy
    import logging

    log = logging.getLogger(__name__)
    ticket = copy.deepcopy(ticket)
    groups: list[list[Any]] = []
    if isinstance(ticket.get("attachments"), list):
        groups.append(ticket["attachments"])
    for message in ticket.get("discussion") or []:
        if isinstance(message, dict) and isinstance(message.get("attachments"), list):
            groups.append(message["attachments"])
    files = [
        item for group in groups
        for item in group
        if isinstance(item, dict) and _has_download_location(item)
    ]
    ticket["local_image_paths"] = []
    ticket["local_attachment_paths"] = []
    if not files:
        return ticket
    root = _tensology_attachment_root(project, ticket)
    used: set[str] = set()
    local_paths: list[str] = []
    image_paths: list[str] = []
    for item in files:
        raw_name = str(item.get("name") or item.get("filename") or Path(urllib.parse.urlsplit(str(item.get("url") or "")).path).name or "attachment")
        name = _safe_attachment_part(Path(raw_name).name, "attachment")
        stem, suffix, counter = Path(name).stem, Path(name).suffix, 1
        while name in used:
            counter += 1
            name = f"{stem}-{counter}{suffix}"
        used.add(name)
        destination = root / name
        is_image = _is_image_attachment(item)
        if destination.is_file() and destination.stat().st_size > 0:
            item["local_path"] = str(destination)
            item["download_status"] = "cached"
            local_paths.append(str(destination))
            if is_image:
                image_paths.append(str(destination))
            continue
        locations: list[str] = []
        for key in ("api_path", "absolute_url", "url"):
            value = str(item.get(key) or "").strip()
            if value and value not in locations:
                locations.append(value)
        errors: list[str] = []
        for location in locations:
            try:
                size = client.download(location, destination)
            except TensologyApiError as exc:
                errors.append(str(exc))
                continue
            item["local_path"] = str(destination)
            item["download_status"] = "downloaded"
            item["size"] = size
            local_paths.append(str(destination))
            if is_image:
                image_paths.append(str(destination))
            log.info("Tensology ticket %s attachment downloaded: %s (%s bytes)", ticket.get("reference"), destination, size)
            break
        else:
            item["download_error"] = "; ".join(errors) or "No downloadable URL on the attachment."
            log.warning("Tensology ticket %s attachment %r not downloaded: %s", ticket.get("reference"), raw_name, item["download_error"])
    ticket["local_attachment_paths"] = local_paths
    ticket["local_image_paths"] = image_paths
    return ticket


def _sync_tensology_ticket_state(client: TensologyClient, ticket: dict[str, Any], chat_id: int) -> bool:
    """Mirror the local Development thread state to Tensology; network only when it changed."""
    from distr.core.workflow.development_harness import development_execution_state

    execution = development_execution_state(chat_id)
    remote_status = _remote_execution_status(str(execution.get("status") or ""), execution)
    if remote_status == str(ticket.get("execution_status") or ""):
        return False
    detail = str(execution.get("summary") or execution.get("error") or "")[:500]
    if remote_status == "failed" and str(execution.get("status") or "").lower() in {"completed", "completed_noop"}:
        detail = "Execution stopped without final verification evidence. Review the Development thread before retrying."
    updated = client.patch(
        f"tickets/{int(ticket['id'])}/execution",
        {
            "thread_id": str(chat_id),
            "status": remote_status,
            "detail": detail,
            "evidence": _tensology_execution_receipt(execution, chat_id=chat_id),
        },
        idempotency_key=f"tensology-ticket-{int(ticket['id'])}-status-{remote_status}-{execution.get('job_id') or 'run'}",
    )
    _remember_tensology_tickets([updated if isinstance(updated, dict) else {**ticket, "execution_status": remote_status}])
    return True


def _process_tensology_ticket(client: TensologyClient, ticket: dict[str, Any]) -> bool:
    """Handle one ticket waiting on DecisionsAI: project, kanban ticket, thread, state mirror.

    Shared by the catch-up fetch and pushed ticket events so both behave identically.
    Returns True when the ticket is live in a Development thread.
    """
    project = _resolve_tensology_project(ticket)
    if project is None:
        project = _create_tensology_project(client, ticket)
    if project is None:
        _report_tensology_unmatched_project(client, ticket)
        return False
    chat_id = _existing_tensology_thread(ticket)
    route = {
        "complexity": str(ticket.get("execution_complexity") or ""),
        "backend": str(ticket.get("decisions_backend") or ""),
        "provider": str(ticket.get("decisions_provider") or ""),
        "model": str(ticket.get("decisions_model") or ""),
    }
    if chat_id is None:
        try:
            ticket = _localize_tensology_attachments(client, ticket, project)
        except Exception as exc:  # never block a ticket on its images
            import logging

            logging.getLogger(__name__).warning("Tensology ticket %s images skipped: %s", ticket.get("reference"), exc)
        try:
            chat_id, route = _create_tensology_thread(ticket, project)
        except TensologyRouteError as exc:
            _report_tensology_route_block(client, ticket, project, exc)
            return False
        try:
            from distr.core.tensology_notify import notify_tensology_ticket_incoming

            notify_tensology_ticket_incoming(ticket=ticket, project=project, chat_id=chat_id)
        except Exception:
            import logging

            logging.getLogger(__name__).debug("Incoming ticket notification failed", exc_info=True)

    thread_url = f"http://127.0.0.1:8765/development/threads/{chat_id}/"
    if str(ticket.get("decisions_thread_id") or "") != str(chat_id):
        claimed = client.post(
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
        _remember_tensology_tickets([
            claimed if isinstance(claimed, dict)
            else {**ticket, "decisions_thread_id": str(chat_id), "execution_status": "development"}
        ])
    else:
        _sync_tensology_ticket_state(client, ticket, chat_id)
    return True


def _fetch_and_process_tensology_tickets(ticket_ids: list[int] | None = None) -> int:
    """Fetch full ticket payloads from GET /api/integrations/v1/tickets and process them."""
    global _TICKET_POLL_INTERVAL
    with _TICKET_PROCESS_LOCK:
        _TICKET_POLL_INTERVAL = TICKET_POLL_IDLE_SECONDS
        client = configured_tensology_client(source="decisionsai-ticket-runner")
        query: dict[str, Any] = {"execution_status": TENSOLOGY_LIVE_TICKET_STATUSES}
        wanted: set[int] = set()
        if ticket_ids:
            wanted = {int(item) for item in ticket_ids}
            query["id"] = ",".join(str(item) for item in sorted(wanted))
        tickets = client.get("tickets", query=query)
        if not isinstance(tickets, list):
            return 0
        if wanted:
            # Older servers ignore ?id=; only handle the tickets the event named.
            tickets = [item for item in tickets if isinstance(item, dict) and _safe_int(item.get("id")) in wanted]
            _remember_tensology_tickets(tickets)
        else:
            tickets = tickets[:20]
            _remember_tensology_tickets(tickets, replace=True)
        processed = 0
        for ticket in tickets:
            if not isinstance(ticket, dict):
                continue
            try:
                if _process_tensology_ticket(client, ticket):
                    processed += 1
            except Exception as exc:
                # One blocked ticket (bad route, missing project, project already
                # running another ticket) must not abort the rest of the batch.
                import logging

                logging.getLogger(__name__).warning(
                    "Tensology ticket %s skipped this pass: %s",
                    ticket.get("reference") or ticket.get("id"),
                    exc,
                )
        if processed:
            _TICKET_POLL_INTERVAL = TICKET_POLL_ACTIVE_SECONDS
        return processed


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ticket_push_paused() -> bool:
    """Honour ``tensology_push_paused``: no ticket fetch/claim/PATCH while the push is paused."""
    try:
        from distr.core.tensology_stream import tensology_push_paused

        return tensology_push_paused()
    except Exception:
        return False


def process_tensology_ticket_events(ticket_ids: list[int]) -> int:
    """Handle pushed ``decisions_ticket`` events: fetch those tickets' full payloads, then process."""
    if _ticket_push_paused():
        import logging

        logging.getLogger(__name__).info("Tensology ticket push paused; ignoring pushed tickets %s", ticket_ids)
        return 0
    ids = [item for item in (_safe_int(value) for value in ticket_ids or []) if item is not None]
    if not ids:
        return process_tensology_approved_tickets(force=True)
    return _fetch_and_process_tensology_tickets(ids)


def process_tensology_approved_tickets(*, force: bool = False) -> int:
    """Catch-up fetch: create/reuse Development threads for every ticket waiting on DecisionsAI.

    Tickets arrive by push over the outbound Tensology websocket
    (``distr.core.tensology_stream``); this full fetch is no longer polled. It runs when
    the stream connects or reconnects so tickets sent while DecisionsAI was offline are
    not lost. Per-ticket handling and idempotency are shared with pushed events.
    """
    global _LAST_TICKET_POLL
    now = time.monotonic()
    if not force and now - _LAST_TICKET_POLL < _TICKET_POLL_INTERVAL:
        return 0
    if _ticket_push_paused():
        import logging

        logging.getLogger(__name__).info("Tensology ticket push paused; skipping ticket catch-up fetch")
        return 0
    _LAST_TICKET_POLL = now
    return _fetch_and_process_tensology_tickets(None)


def sync_tensology_execution_states(*, force: bool = False) -> int:
    """Local-only mirror of Development thread state for tickets already claimed by a thread.

    Replaces the state-sync half of the old poll: it reads local thread state every few
    seconds and only calls Tensology (PATCH) when a ticket's status actually changed.
    """
    global _LAST_STATE_SYNC
    now = time.monotonic()
    if not force and now - _LAST_STATE_SYNC < STATE_SYNC_SECONDS:
        return 0
    _LAST_STATE_SYNC = now
    with _TENSOLOGY_TICKETS_LOCK:
        known = [dict(ticket) for ticket in _TENSOLOGY_TICKETS.values()]
    claimed = [ticket for ticket in known if str(ticket.get("decisions_thread_id") or "").strip()]
    if not claimed:
        return 0
    if _ticket_push_paused():
        return 0  # paused: no ticket status PATCHes to Tensology
    if not _TICKET_PROCESS_LOCK.acquire(blocking=False):
        return 0  # a fetch is running and mirrors state itself
    try:
        client = None
        changed = 0
        for ticket in claimed:
            chat_id = _existing_tensology_thread(ticket)
            if chat_id is None or str(ticket.get("decisions_thread_id") or "") != str(chat_id):
                continue
            client = client or configured_tensology_client(source="decisionsai-ticket-runner")
            if _sync_tensology_ticket_state(client, ticket, chat_id):
                changed += 1
        return changed
    finally:
        _TICKET_PROCESS_LOCK.release()
