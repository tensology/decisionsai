from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


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
