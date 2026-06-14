"""Local runtime artifact paths for the DecisionsAI repository checkout."""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_ROOT = _PROJECT_ROOT / ".artifacts"


def project_root() -> Path:
    """Return the DecisionsAI repository root."""
    return _PROJECT_ROOT


def repo_artifacts_dir() -> Path:
    """Return the gitignored local artifacts directory for this checkout."""
    return ARTIFACTS_ROOT


def repo_tickets_dir() -> Path:
    """Return local ticket exports and scratch work items for this checkout."""
    return ARTIFACTS_ROOT / "tickets"


def repo_pi_skills_dir() -> Path:
    """Return local Pi skill projections for this checkout."""
    return ARTIFACTS_ROOT / "pi" / "skills"


def repo_cursor_handoffs_dir() -> Path:
    """Return local Cursor handoff files for this checkout."""
    return ARTIFACTS_ROOT / "decisions" / "cursor-handoffs"


def ensure_repo_artifacts_dirs() -> None:
    """Create standard artifact folders when local runtime files are written."""
    for path in (
        repo_tickets_dir(),
        repo_pi_skills_dir(),
        repo_cursor_handoffs_dir(),
        ARTIFACTS_ROOT / "docs",
    ):
        path.mkdir(parents=True, exist_ok=True)
