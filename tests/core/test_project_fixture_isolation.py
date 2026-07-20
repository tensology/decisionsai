from __future__ import annotations

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (
    PROJECT_ROOT / "distr",
    PROJECT_ROOT / "plugins",
    PROJECT_ROOT / "sidecar",
    PROJECT_ROOT / "steprunner",
    PROJECT_ROOT / "bin",
    PROJECT_ROOT / "scripts",
)
SOURCE_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


def test_acceptance_project_fixture_does_not_leak_into_production_code() -> None:
    """Acceptance-project inputs are runtime data, never product behaviour."""

    leaked_paths: list[str] = []
    fixture_markers = (
        re.compile(r"\bkayla(?:[\s_-]*the[\s_-]*crow)?\b", re.IGNORECASE),
        re.compile(r"\bthatshirtshow\b", re.IGNORECASE),
        re.compile(r"www\.kaylathecrow\.com", re.IGNORECASE),
        re.compile(r"5cV5Ezzb6f9VL7EssX2YIH", re.IGNORECASE),
        re.compile(r"UCAmXcJjfdjSArLELSCcL4Ag", re.IGNORECASE),
    )

    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            relative_path = str(path.relative_to(PROJECT_ROOT))
            content = path.read_text(encoding="utf-8", errors="ignore")
            if any(
                marker.search(relative_path) or marker.search(content)
                for marker in fixture_markers
            ):
                leaked_paths.append(str(path.relative_to(PROJECT_ROOT)))

    assert leaked_paths == [], (
        "Acceptance-project identity leaked into production code: "
        + ", ".join(leaked_paths)
    )
