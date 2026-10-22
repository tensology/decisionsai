"""Settings, paths and switches for frustration capture / nightly review."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

FLAG_KEY = "frustration_capture"
ENV_KEY = "DECISIONS_FRUSTRATION_CAPTURE"
LAYA_ENV_KEY = "DECISIONS_FRUSTRATION_LAYA"          # 0 = regex-only
REDACT_ENV_KEY = "DECISIONS_FRUSTRATION_REDACT"      # redaction is on unless explicitly 0
REVIEWER_ENV_KEY = "DECISIONS_SELFIMPROVE_REVIEWER"  # force codex|claude|decisions|none

# Projects / skills whose lessons must never be auto-proposed (P1 / live money).
EXCLUDE_PATTERNS = [
    r"\bP1\b", r"live[-_ ]?money", r"\btrading\b", r"currency[-_]trader", r"auctionnow",
    r"\bpayments?\b", r"\bpayfast\b", r"\bstripe\b", r"\bbank(ing)?\b", r"\bwallet\b",
]


def _off(raw: Any) -> bool:
    return str(raw).strip().lower() in {"0", "false", "no", "off", "disabled"}


def capture_enabled(settings: dict[str, Any] | None = None) -> bool:
    raw = os.environ.get(ENV_KEY)
    if raw is not None and raw.strip():
        return not _off(raw)
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            settings = {}
    if settings and FLAG_KEY in settings and settings.get(FLAG_KEY) is not None:
        return not _off(settings.get(FLAG_KEY))
    return True


def redaction_enabled() -> bool:
    return not _off(os.environ.get(REDACT_ENV_KEY, "1"))


def home() -> Path:
    return Path(os.path.expanduser("~"))


def root() -> Path:
    return home() / ".decisions" / "self-improve"


def state_file() -> Path:
    return root() / "state.json"


def proposals_dir() -> Path:
    return root() / "proposals"


def ensure_dirs() -> None:
    for d in (root(), proposals_dir()):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)


# ── Laya (optional local classifier) ──────────────────────────────────────────

def laya_python() -> Path:
    return home() / ".virtualenvs" / "laya" / "bin" / "python"


def laya_available() -> bool:
    if _off(os.environ.get(LAYA_ENV_KEY, "1")):
        return False
    return laya_python().is_file()


def ensure_classifier(log: Callable[[str], None], *, dry_run: bool = False) -> str:
    """Install Laya if missing and the machine can run it; else regex-only."""
    if _off(os.environ.get(LAYA_ENV_KEY, "1")):
        log("Laya disabled (DECISIONS_FRUSTRATION_LAYA=0): regex-only capture")
        return "regex"
    if laya_python().is_file():
        log("Laya present: live spotting uses Laya + regex")
        return "laya"
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        log("Laya skipped (needs Apple Silicon/MPS here): regex-only capture")
        return "regex"
    free_gb = shutil.disk_usage(str(home())).free / 1e9
    if free_gb < 6:
        log(f"Laya skipped (only {free_gb:.1f} GB free; needs ~3 GB): regex-only capture")
        return "regex"
    log("install Laya (~1 GB venv + ~0.8 GB model on first use) into ~/.virtualenvs/laya")
    if dry_run:
        return "laya"
    try:
        from distr.core import laya_runtime

        laya_runtime.install()
        return "laya"
    except Exception as exc:
        log(f"Laya install failed ({type(exc).__name__}): regex-only capture")
        return "regex"


# ── reviewer CLI for the nightly retro ────────────────────────────────────────

CODEX_BIN_ENV_KEY = "DECISIONS_CODEX_BIN"  # explicit codex path, e.g. the CLI bundled in ChatGPT.app


def _cli_path(name: str) -> str | None:
    if name == "codex" and os.environ.get(CODEX_BIN_ENV_KEY):
        p = Path(os.environ[CODEX_BIN_ENV_KEY]).expanduser()
        return str(p) if p.exists() else None
    return shutil.which(name) or next(
        (str(p) for p in (home() / ".local" / "bin" / name, Path("/opt/homebrew/bin") / name) if p.exists()), None)


def reviewer_chain() -> list[tuple[str, str | None]]:
    """Available reviewers in order: codex exec -> claude -p -> Decisions LLM."""
    forced = (os.environ.get(REVIEWER_ENV_KEY) or "").strip().lower()
    order = [forced] if forced else ["codex", "claude", "decisions"]
    chain: list[tuple[str, str | None]] = []
    for name in order:
        if name in {"codex", "claude"}:
            path = _cli_path(name)
            if path:
                chain.append((name, path))
        elif name == "decisions":
            chain.append(("decisions", None))
    return chain


def pick_reviewer() -> tuple[str, str | None]:
    chain = reviewer_chain()
    return chain[0] if chain else ("none", None)


def _short(text: str) -> str:
    from distr.core.mempalace.selfimprove.redact import redact

    line = next((ln for ln in (text or "").splitlines() if ln.strip() and not ln.startswith("Warning:")), "")
    return redact(line.strip())[:160]


def run_reviewer(prompt: str, *, timeout: int = 600) -> tuple[str, str]:
    """Try each reviewer in order and return (name, text) from the first that works.

    A failure (not logged in, out of credit, timeout) falls through to the next one.
    Raises RuntimeError listing every failure when none works.
    """
    failures: list[str] = []
    for name, path in reviewer_chain():
        try:
            if name == "codex":
                r = subprocess.run([path, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-"],
                                   input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(root()))
            elif name == "claude":
                r = subprocess.run([path, "-p", "--output-format", "text", "--tools", "", "--no-session-persistence"],
                                   input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(root()))
            else:
                model, text = _decisions_llm(prompt)
                return f"decisions({model})", text
            if r.returncode == 0 and r.stdout.strip():
                return name, r.stdout
            failures.append(f"{name}: rc={r.returncode} {_short(r.stdout) or _short(r.stderr)}")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {_short(str(exc))}")
    raise RuntimeError("no reviewer worked (" + "; ".join(failures or ["none installed"]) + ")")


def _decisions_llm(prompt: str) -> tuple[str, str]:
    """Decisions-side LLM (OpenRouter free models by default; see selfimprove/llm.py)."""
    from distr.core.mempalace.selfimprove import llm

    return llm.complete(prompt)
