"""
Pre-flight checks for Pi coding CLI sessions.

Validates pi binary, project folder, Ollama availability, and model access
before spawning `pi --mode rpc` or accepting a prompt — so subscription /
missing-model failures surface immediately instead of hanging on "Starting agent...".
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

OLLAMA_CHAT_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/") + "/api/chat"


@dataclass
class PreflightCheck:
    id: str
    ok: bool
    message: str


@dataclass
class PreflightFix:
    """Actionable remediation the web UI can offer in one click."""

    id: str
    label: str
    action: str  # use_model | open_url | recheck | focus_model | copy_command
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PiPreflightResult:
    ok: bool
    provider: str = "ollama"
    model: str = ""
    cwd: str = ""
    checks: list[PreflightCheck] = field(default_factory=list)
    user_message: str = ""
    fixes: list[PreflightFix] = field(default_factory=list)
    suggested_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "cwd": self.cwd,
            "user_message": self.user_message,
            "checks": [
                {"id": c.id, "ok": c.ok, "message": c.message}
                for c in self.checks
            ],
            "fixes": [
                {
                    "id": f.id,
                    "label": f.label,
                    "action": f.action,
                    "payload": f.payload or {},
                }
                for f in self.fixes
            ],
            "suggested_models": list(self.suggested_models),
        }


def resolve_coding_cli_config(project_id: Optional[int] = None) -> tuple[str, str, str]:
    """Return (provider, model, cwd) for the coding CLI."""
    provider = "ollama"
    model = ""
    cwd = os.path.expanduser("~")

    try:
        from distr.core.db import get_session as db_session
        from sqlalchemy import text
        from distr.core.db.projects import Project

        with db_session() as session:
            row = session.execute(
                text("SELECT coding_llm_provider, coding_llm_model FROM settings LIMIT 1")
            ).first()
            if row:
                provider = (row[0] or "ollama").strip().lower()
                model = (row[1] or "").strip()
            if project_id:
                project = session.query(Project).filter(Project.id == project_id).first()
                if project:
                    project_model = (project.coding_backend_model or "").strip()
                    if project_model:
                        model = project_model
                    folder = (project.folder_location or "").strip()
                    if folder and os.path.isdir(folder):
                        cwd = folder
    except Exception as e:
        logger.debug("resolve_coding_cli_config: %s", e)

    return provider, model, cwd


def _normalize_ollama_error(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "Ollama rejected the model request."
    # Strip JSON quoting from API bodies
    m = re.search(r'"errorMessage"\s*:\s*"([^"]+)"', text)
    if m:
        text = m.group(1)
    text = text.replace('\\"', '"')
    if "requires a subscription" in text.lower() or "upgrade for access" in text.lower():
        return (
            f"{text} "
            "Pick a local model in the CLI dropdown (for example qwen3:8b) or upgrade Ollama cloud at https://ollama.com/upgrade."
        )
    if "not found" in text.lower() and "model" in text.lower():
        return f"{text} Run `ollama pull <model>` or choose another model in the CLI dropdown."
    return text


def _ollama_reachable() -> tuple[bool, str]:
    if not shutil.which("ollama"):
        return False, "Ollama is not installed or not on PATH. Install from https://ollama.com or choose a non-Ollama provider in CLI settings."
    try:
        req = urllib.request.Request(f"{OLLAMA_CHAT_URL.rsplit('/api/', 1)[0]}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return True, ""
    except urllib.error.URLError:
        return False, "Ollama is not running. Start it with `ollama serve` or open the Ollama app, then try again."
    except Exception as e:
        return False, f"Could not reach Ollama: {e}"
    return False, "Ollama is not responding."


def _ollama_installed_model_ids() -> set[str]:
    ids: set[str] = set()
    try:
        from distr.gui.utils.get_ollama_models import get_installed_ollama_models

        for entry in get_installed_ollama_models() or []:
            mid = (entry.get("id") or "").strip()
            if mid:
                ids.add(mid)
                ids.add(mid.split(":")[0] + ":latest")
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines()[1:]:
                parts = line.split()
                if parts:
                    ids.add(parts[0])
    except Exception:
        pass
    return ids


def _model_matches_installed(model: str, installed: set[str]) -> bool:
    if not model:
        return False
    if model in installed:
        return True
    base = model.split(":")[0]
    for candidate in (f"{base}:latest", f"{base}:8b", model):
        if candidate in installed:
            return True
    return any(i.startswith(base + ":") or i == base for i in installed)


def _probe_ollama_model(model: str, timeout: float = 25.0) -> tuple[bool, str]:
    """Send a minimal chat request to verify the model actually works."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "stream": False,
            "options": {"num_predict": 8},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            err = parsed.get("error") or parsed.get("message") or detail
        except json.JSONDecodeError:
            err = detail or f"HTTP {exc.code}"
        return False, _normalize_ollama_error(str(err))
    except urllib.error.URLError as exc:
        return False, f"Could not reach Ollama at {OLLAMA_CHAT_URL}: {exc.reason}"
    except Exception as exc:
        return False, str(exc)

    if payload.get("error"):
        return False, _normalize_ollama_error(str(payload["error"]))

    msg = payload.get("message") or {}
    if isinstance(msg, dict) and msg.get("error"):
        return False, _normalize_ollama_error(str(msg["error"]))

    return True, ""


def _is_ollama_cloud_model(name: str) -> bool:
    """True for Ollama cloud tags (glm-5.1:cloud, qwen3-next:80b-cloud, etc.)."""
    n = (name or "").lower().strip()
    if not n:
        return False
    if ":cloud" in n:
        return True
    if ":" in n:
        tag = n.rsplit(":", 1)[-1]
        if "cloud" in tag:
            return True
    return False


def _pick_local_fallback_models(installed: set[str], limit: int = 5) -> list[str]:
    """Prefer coding-friendly local models (non-cloud) for one-click switch."""
    if not installed:
        try:
            from distr.core.system_resources import recommend_ollama_defaults

            rec = recommend_ollama_defaults()
            fallback = (rec.get("coding") or "").strip()
            if fallback and not _is_ollama_cloud_model(fallback):
                return [fallback]
        except Exception:
            pass
        return ["qwen3:8b", "qwen2.5-coder:7b"]

    locals_only = [m for m in installed if m and not _is_ollama_cloud_model(m)]
    priority = (
        "qwen2.5-coder",
        "qwen3",
        "qwen2.5",
        "deepseek",
        "codellama",
        "llama3",
        "gemma",
    )

    def score(name: str) -> tuple[int, str]:
        base = name.split(":")[0].lower()
        for idx, prefix in enumerate(priority):
            if base.startswith(prefix) or prefix in base:
                return (idx, name)
        return (len(priority), name)

    ranked = sorted(locals_only, key=score)
    out: list[str] = []
    for mid in ranked:
        if mid not in out:
            out.append(mid)
        if len(out) >= limit:
            break
    return out


def _ollama_cli_authenticated() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ollama", "signin"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        if "already signed in" in combined.lower():
            m = re.search(r"signed in as user ['\"]([^'\"]+)['\"]", combined, re.I)
            user = m.group(1) if m else "your account"
            return True, user
        if result.returncode == 0:
            return True, "signed in"
        return False, combined or "Not signed in to Ollama"
    except FileNotFoundError:
        return False, "Ollama CLI not found"
    except Exception as e:
        return False, str(e)


def _build_fixes(
    result: PiPreflightResult,
    installed: set[str],
) -> None:
    """Populate result.fixes and result.suggested_models from failed checks."""
    failed = {c.id for c in result.checks if not c.ok}
    fixes: list[PreflightFix] = []
    suggested = _pick_local_fallback_models(installed)
    result.suggested_models = suggested

    if "pi_binary" in failed:
        fixes.append(
            PreflightFix(
                id="install_pi",
                label="Install Pi (npm)",
                action="copy_command",
                payload={"command": "npm install -g @mariozechner/pi-coding-agent"},
            )
        )

    if "ollama_running" in failed:
        fixes.append(
            PreflightFix(
                id="start_ollama",
                label="Copy: start Ollama",
                action="copy_command",
                payload={"command": "ollama serve"},
            )
        )
        fixes.append(
            PreflightFix(
                id="ollama_download",
                label="Get Ollama",
                action="open_url",
                payload={"url": "https://ollama.com/download"},
            )
        )

    if "ollama_model_installed" in failed and result.model:
        fixes.append(
            PreflightFix(
                id="pull_model",
                label=f"Copy: ollama pull {result.model}",
                action="copy_command",
                payload={"command": f"ollama pull {result.model}"},
            )
        )

    cloud_probe_failed = "ollama_model_probe" in failed and _is_ollama_cloud_model(result.model or "")
    subscription_error = any(
        "subscription" in (c.message or "").lower() or "upgrade" in (c.message or "").lower()
        for c in result.checks
        if c.id == "ollama_model_probe" and not c.ok
    )

    if cloud_probe_failed or subscription_error:
        signed_in, who = _ollama_cli_authenticated()
        if not signed_in:
            fixes.append(
                PreflightFix(
                    id="ollama_signin",
                    label="Copy: ollama signin",
                    action="copy_command",
                    payload={"command": "ollama signin"},
                )
            )
        else:
            fixes.append(
                PreflightFix(
                    id="ollama_account",
                    label=f"Ollama signed in as {who}",
                    action="open_url",
                    payload={"url": "https://ollama.com/settings"},
                )
            )
        for mid in suggested[:3]:
            fixes.append(
                PreflightFix(
                    id=f"use_{mid.replace(':', '_')}",
                    label=f"Use local model: {mid}",
                    action="use_model",
                    payload={"model": mid, "provider": "ollama"},
                )
            )
        fixes.append(
            PreflightFix(
                id="ollama_settings",
                label="Ollama cloud usage & limits",
                action="open_url",
                payload={"url": "https://ollama.com/settings"},
            )
        )
        if subscription_error:
            fixes.append(
                PreflightFix(
                    id="ollama_upgrade",
                    label="Ollama pricing / upgrade",
                    action="open_url",
                    payload={"url": "https://ollama.com/pricing"},
                )
            )

    elif "ollama_model_probe" in failed and suggested:
        fixes.append(
            PreflightFix(
                id=f"use_{suggested[0].replace(':', '_')}",
                label=f"Try local model: {suggested[0]}",
                action="use_model",
                payload={"model": suggested[0], "provider": "ollama"},
            )
        )

    if "model_configured" in failed:
        fixes.append(
            PreflightFix(
                id="pick_model",
                label="Choose a model below",
                action="focus_model",
                payload={},
            )
        )

    if not result.ok:
        fixes.append(
            PreflightFix(
                id="focus_model_dropdown",
                label="Open model dropdown",
                action="focus_model",
                payload={},
            )
        )

    fixes.append(
        PreflightFix(
            id="recheck",
            label="Run check again",
            action="recheck",
            payload={},
        )
    )

    # De-dupe by id
    seen: set[str] = set()
    result.fixes = []
    for fix in fixes:
        if fix.id in seen:
            continue
        seen.add(fix.id)
        result.fixes.append(fix)


def preflight_pi_coding_cli(
    project_id: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
    *,
    probe_model: bool = True,
) -> PiPreflightResult:
    """
    Run pre-flight checks for Pi + coding model configuration.

    Set probe_model=False for a fast check (binary, folder, list only).
    Cloud models (:cloud) always probe when probe_model is True.
    """
    from distr.core.pi_rpc import PiRpcSession

    resolved_provider, resolved_model, resolved_cwd = resolve_coding_cli_config(project_id)
    if provider:
        resolved_provider = provider.strip().lower()
    if model:
        resolved_model = model.strip()
    if cwd:
        resolved_cwd = cwd

    checks: list[PreflightCheck] = []
    result = PiPreflightResult(
        ok=True,
        provider=resolved_provider,
        model=resolved_model,
        cwd=resolved_cwd,
        checks=checks,
    )

    def add(check_id: str, ok: bool, message: str) -> None:
        checks.append(PreflightCheck(id=check_id, ok=ok, message=message))
        if not ok:
            result.ok = False
            if not result.user_message:
                result.user_message = message

    pi_bin = PiRpcSession.find_pi()
    if not pi_bin:
        add(
            "pi_binary",
            False,
            "Pi coding agent is not installed. Install with: npm install -g @mariozechner/pi-coding-agent",
        )
        _build_fixes(result, set())
        return result
    else:
        add("pi_binary", True, f"Pi found at {pi_bin}")

    if project_id and resolved_cwd:
        if not os.path.isdir(resolved_cwd):
            add(
                "project_folder",
                False,
                f"Project folder does not exist: {resolved_cwd}. Set a valid folder in Projects.",
            )
        else:
            add("project_folder", True, f"Project folder: {resolved_cwd}")

    if not resolved_model:
        add(
            "model_configured",
            False,
            "No coding model selected. Choose a model in the CLI dropdown before sending a prompt.",
        )
        _build_fixes(result, _ollama_installed_model_ids())
        return result

    add("model_configured", True, f"Model: {resolved_provider}/{resolved_model}")

    if resolved_provider not in ("ollama", ""):
        # Other providers are configured in pi's models.json / env — pi will surface errors at runtime.
        add(
            "provider",
            True,
            f"Provider '{resolved_provider}' — runtime validation is handled by Pi.",
        )
        if not result.ok:
            _build_fixes(result, set())
        return result

    reachable, reach_msg = _ollama_reachable()
    if not reachable:
        add("ollama_running", False, reach_msg)
        _build_fixes(result, set())
        return result
    add("ollama_running", True, "Ollama is running")

    is_cloud = _is_ollama_cloud_model(resolved_model)
    installed = _ollama_installed_model_ids()

    if is_cloud:
        if probe_model:
            ok, msg = _probe_ollama_model(resolved_model)
            add("ollama_model_probe", ok, msg or "Cloud model is available.")
        else:
            add(
                "ollama_model_probe",
                True,
                f"Cloud model {resolved_model} — full probe skipped (use preflight with probe_model=True).",
            )
    else:
        if not _model_matches_installed(resolved_model, installed):
            installed_preview = ", ".join(sorted(installed)[:8])
            hint = f" Installed: {installed_preview}." if installed_preview else " No local chat models found — run `ollama pull <model>`."
            add(
                "ollama_model_installed",
                False,
                f"Model '{resolved_model}' is not installed in Ollama.{hint}",
            )
            _build_fixes(result, installed)
            return result
        add("ollama_model_installed", True, f"Model '{resolved_model}' is installed.")
        if probe_model:
            ok, msg = _probe_ollama_model(resolved_model, timeout=45.0)
            add("ollama_model_probe", ok, msg or "Model responded successfully.")

    if result.ok:
        result.user_message = ""
        result.fixes = []
    else:
        _build_fixes(result, installed)
    return result
