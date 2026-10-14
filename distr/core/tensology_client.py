from __future__ import annotations

import json
import getpass
import platform
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
from dataclasses import dataclass
from typing import Any


_LAST_DISCUSSION_POLL = 0.0
_LAST_CONTEXT_POLL = 0.0
DISCUSSION_POLL_SECONDS = 30


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
    settings["project_cli_medium_backend"] = remote_provider
    settings["project_cli_medium_model"] = remote_model
    save_settings_to_db(settings)


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
