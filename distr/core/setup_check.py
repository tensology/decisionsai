"""First-run / pre-run setup self-check.

Checks the things that silently broke real runs: provider keys that exist but
are not usable, model ids that are not installed (or are another provider's
id), Ollama cloud models that are out of credit, linked projects whose folder
is gone, and missing tool binaries. Anything Decisions can route around is
recorded quietly; at most ONE plain-language message with one action is
produced for the person, and only when nothing configured can work.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SetupIssue:
    key: str
    message: str
    action: str
    blocking: bool = False
    routed_around: bool = False


@dataclass
class SetupReport:
    issues: list[SetupIssue] = field(default_factory=list)
    working_routes: list[str] = field(default_factory=list)

    def one_message(self) -> str:
        """At most one plain sentence + one action, only for a real blocker."""
        blocking = [issue for issue in self.issues if issue.blocking and not issue.routed_around]
        if not blocking:
            return ""
        first = blocking[0]
        return f"{first.message} {first.action}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [issue.__dict__ for issue in self.issues],
            "working_routes": list(self.working_routes),
            "message": self.one_message(),
        }


def _ollama_probe(model: str, base: str) -> str:
    """'ok', 'credits' or 'error' for one tiny Ollama call (cloud models only)."""
    try:
        from urllib.request import Request, urlopen

        body = json.dumps({
            "model": model, "messages": [{"role": "user", "content": "ok"}],
            "stream": False, "options": {"num_predict": 1},
        }).encode()
        request = Request(base.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception as exc:  # urllib raises on 4xx
        text = str(getattr(exc, "read", lambda: b"")() or exc).lower()
        return "credits" if ("credit" in text or "402" in text or "subscribe" in text) else "error"
    text = json.dumps(payload).lower()
    if "credit" in text or "subscribe" in text:
        return "credits"
    return "ok" if payload.get("message") else "error"


def run_setup_check(
    settings: dict[str, Any] | None = None,
    *,
    projects: list[tuple[str, str]] | None = None,
    probe_cloud: bool = False,
    automation_routes: list[tuple[str, str]] | None = None,
) -> SetupReport:
    from distr.core.litellm_utils import ollama_installed_models
    from distr.core.provider_failover import configured_models, provider_is_keyed, provider_label

    if settings is None:
        from distr.core.utils import load_settings_from_db

        settings = load_settings_from_db() or {}
    report = SetupReport()
    base = str(settings.get("ollama_url") or "http://127.0.0.1:11434")
    installed = ollama_installed_models(base)
    routes = list(dict.fromkeys([*configured_models(settings), *(automation_routes or [])]))
    probed: dict[str, str] = {}
    for provider, model in routes:
        label = f"{provider_label(provider)} · {model}"
        if provider == "ollama":
            if installed is None:
                report.issues.append(SetupIssue(f"ollama-down:{model}", "Ollama is not running.",
                                                "Open the Ollama app.", routed_around=True))
                continue
            if model.lower() not in installed and f"{model.lower()}:latest" not in installed:
                hint = " (that is an OpenRouter-style id)" if "/" in model else ""
                report.issues.append(SetupIssue(
                    f"ollama-missing:{model}", f"{label} is not installed in Ollama{hint}.",
                    "Pick an installed model in Settings → LLMs.", routed_around=True))
                continue
            if model.lower().endswith((":cloud", "-cloud")) and probe_cloud:
                status = probed.get(model) or _ollama_probe(model, base)
                probed[model] = status
                if status == "credits":
                    report.issues.append(SetupIssue(
                        "ollama-cloud-credits", "Ollama Cloud is out of credit, so its cloud models can't run.",
                        "Top up at ollama.com/plans, or I'll keep using OpenRouter.", routed_around=True))
                    continue
            report.working_routes.append(label)
            continue
        if not provider_is_keyed(provider, settings):
            report.issues.append(SetupIssue(
                f"key-missing:{provider}", f"{provider_label(provider)} has no API key.",
                f"Add the {provider_label(provider)} key in Settings → LLMs.", routed_around=True))
            continue
        report.working_routes.append(label)
    if not report.working_routes:
        report.issues.append(SetupIssue(
            "no-working-model", "None of the configured AI models can run right now.",
            "Add an OpenRouter key (or credits) in Settings → LLMs.", blocking=True))
    else:
        # Everything above was routed around; say nothing to the person.
        pass

    for name, folder in projects or []:
        if folder and not Path(folder).expanduser().is_dir():
            report.issues.append(SetupIssue(
                f"project-folder:{name}", f"The folder for {name} is missing ({folder}).",
                f"Point {name} at its repo in Projects.", routed_around=True))

    for binary, fallback in (("git", False), ("pi", False), ("rg", True)):
        if not shutil.which(binary):
            report.issues.append(SetupIssue(
                f"binary:{binary}", f"{binary} is not installed.",
                "Install it" + (" (search falls back to Python meanwhile)." if fallback else "."),
                routed_around=fallback))
    return report


def linked_projects() -> list[tuple[str, str]]:
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            return [(str(p.name or ""), str(p.folder_location or "")) for p in db.query(Project).filter(Project.deleted_at.is_(None)).all()]
    except Exception:
        logger.debug("Setup check could not list projects", exc_info=True)
        return []


def automation_model_routes() -> list[tuple[str, str]]:
    try:
        from distr.core.db import get_session
        from sqlalchemy import text

        with get_session() as db:
            rows = db.execute(text("select action_config from automations")).fetchall()
    except Exception:
        return []
    routes: list[tuple[str, str]] = []
    for (raw,) in rows:
        try:
            config = json.loads(raw or "{}")
        except Exception:
            continue
        provider = str(config.get("model_provider") or "").strip().lower()
        model = str(config.get("model") or "").strip()
        if provider and model:
            routes.append((provider, model))
    return routes


if __name__ == "__main__":  # pragma: no cover - manual diagnostic
    result = run_setup_check(projects=linked_projects(), probe_cloud=True, automation_routes=automation_model_routes())
    print(json.dumps(result.to_dict(), indent=2))
