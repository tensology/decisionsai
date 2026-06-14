"""
Reference loop patterns from https://loops.elorm.xyz/loops

Used by the loop-aware planner and tests. Each entry follows the elorm kickoff
template: Goal, Max iterations, Between iterations run, Exit when, Step 1, plus
optional Guardrails and self-pace instructions.
"""
from __future__ import annotations

from typing import Any, Dict, List

from distr.core.workflow.loop_text import GUARDRAILS_FOOTER, SELF_PACE_FOOTER

# Archetypes describe how the planner should compress steps (3–5 steps, not micro-steps)
LOOP_ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "check_fix_until_green": {
        "description": "Run a check command; fix the first failure; repeat until exit.",
        "examples": ["Build Until Green", "E2E Until Green", "Coverage Until Threshold"],
        "primary_action": "send_to_project_cli",
        "check_action": "run_command",
        "typical_steps": 4,
    },
    "review_cleanup": {
        "description": "Review/fix with minimal diffs, run quality checks, subjective exit.",
        "examples": ["De-Sloppify Pass", "PR Self-Review"],
        "primary_action": "send_to_project_cli",
        "check_action": "run_command",
        "typical_steps": 4,
    },
    "incremental_ship": {
        "description": "One unit of work per iteration (e.g. one spec checkbox).",
        "examples": ["Spec-First Ship"],
        "primary_action": "send_to_project_cli",
        "check_action": "run_command",
        "typical_steps": 4,
    },
    "ship_with_ci": {
        "description": "Implement, open PR, wait for CI, fix until green.",
        "examples": ["Ship PR Until Green"],
        "primary_action": "send_to_project_cli",
        "check_action": "run_command",
        "typical_steps": 5,
    },
    "watch_maintain": {
        "description": "Interval watch on external state; triage, fix once, escalate.",
        "examples": ["PR Babysitter"],
        "primary_action": "send_to_project_cli",
        "check_action": "run_command",
        "typical_steps": 4,
    },
    "event_gate": {
        "description": "Gate an action (e.g. commit) on a check passing first.",
        "examples": ["Pre-Commit Guard"],
        "primary_action": "run_command",
        "check_action": "run_command",
        "typical_steps": 3,
    },
}


def _kickoff(name: str, body: str) -> str:
    return f'Start the "{name}" loop.\n{body.strip()}'


# Legacy elorm kickoffs kept for parse_loop_contract tests when bundles are missing.
_ELORM_LEGACY_KICKOFFS: List[Dict[str, Any]] = [
    {
        "name": "Ship PR Until Green",
        "category": "CI",
        "archetype": "ship_with_ci",
        "kickoff": _kickoff(
            "Ship PR Until Green",
            """Goal: PR is open with all CI checks passing
Max iterations: 10
Between iterations run: gh pr checks
Exit when: all PR checks are success
Step 1: Implement the change, test locally, push, open PR, and fix CI until green.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "gh pr checks",
        "expected_max_iterations": 10,
    },
    {
        "name": "De-Sloppify Pass",
        "category": "Review",
        "archetype": "review_cleanup",
        "kickoff": _kickoff(
            "De-Sloppify Pass",
            """Goal: recent changes are clean, minimal, and convention-aligned
Max iterations: 4
Between iterations run: npm run lint && npm test
Exit when: review finds no slop and checks pass
Step 1: Review the diff for debug code, dead branches, and naming issues. Fix them with minimal diffs.
"""
            + SELF_PACE_FOOTER
            + "\n\n"
            + GUARDRAILS_FOOTER,
        ),
        "expected_check_command": "npm run lint && npm test",
        "expected_max_iterations": 4,
    },
    {
        "name": "Spec-First Ship",
        "category": "Planning",
        "archetype": "incremental_ship",
        "kickoff": _kickoff(
            "Spec-First Ship",
            """Goal: every requirement in spec.md is implemented and checked off
Max iterations: 15
Between iterations run: npm test
Exit when: spec.md has no unchecked requirements
Step 1: Read spec.md, implement the first unchecked item, verify it, mark [x], and stop this iteration.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "npm test",
        "expected_max_iterations": 15,
    },
    {
        "name": "Build Until Green",
        "category": "Testing",
        "archetype": "check_fix_until_green",
        "kickoff": _kickoff(
            "Build Until Green",
            """Goal: production build succeeds
Max iterations: 10
Between iterations run: npm run build
Exit when: npm run build exits 0
Step 1: Run the build. If it fails, fix the first error, then repeat until green.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "npm run build",
        "expected_max_iterations": 10,
    },
    {
        "name": "Coverage Until Threshold",
        "category": "Testing",
        "archetype": "check_fix_until_green",
        "kickoff": _kickoff(
            "Coverage Until Threshold",
            """Goal: coverage meets the target threshold (default 80%) with all tests passing
Max iterations: 12
Between iterations run: npm test -- --coverage
Exit when: coverage threshold is met and tests exit 0
Step 1: Run coverage. Add focused tests for the biggest uncovered gaps, then repeat.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "npm test -- --coverage",
        "expected_max_iterations": 12,
    },
    {
        "name": "PR Babysitter",
        "category": "CI",
        "archetype": "watch_maintain",
        "kickoff": _kickoff(
            "PR Babysitter",
            """Goal: open PRs labeled codex-watch are healthy (CI green, rebased, not stale).
Max iterations: 20
Between iterations run: gh pr list --label "codex-watch"
Exit when: each watched PR is green and current, or escalated.
Step 1: List watched PRs. Fix CI once, rebase if behind, comment if stale. Escalate repeated failures.""",
        ),
        "expected_check_command": 'gh pr list --label "codex-watch"',
        "expected_max_iterations": 20,
    },
    {
        "name": "E2E Until Green",
        "category": "Testing",
        "archetype": "check_fix_until_green",
        "kickoff": _kickoff(
            "E2E Until Green",
            """Goal: E2E suite passes
Max iterations: 10
Between iterations run: npm run test:e2e
Exit when: E2E command exits 0
Step 1: Run E2E tests. Fix the first failing spec, then repeat.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "npm run test:e2e",
        "expected_max_iterations": 10,
    },
    {
        "name": "PR Self-Review",
        "category": "Review",
        "archetype": "review_cleanup",
        "kickoff": _kickoff(
            "PR Self-Review",
            """Goal: three clean self-review passes on the current diff
Max iterations: 3
Between iterations run: git diff main...HEAD
Exit when: three passes complete with no critical findings
Step 1: Review the diff like a senior reviewer. Fix findings, then re-review.
"""
            + SELF_PACE_FOOTER,
        ),
        "expected_check_command": "git diff main...HEAD",
        "expected_max_iterations": 3,
    },
    {
        "name": "Pre-Commit Guard",
        "category": "Testing",
        "archetype": "event_gate",
        "kickoff": _kickoff(
            "Pre-Commit Guard",
            """Goal: block git commits when tests are failing
Between iterations run: npm test
Exit when: tests exit 0 before each commit
Step 1: Before any git commit, run tests. Fix failures before committing.""",
        ),
        "expected_check_command": "npm test",
        "expected_max_iterations": None,
    },
]


def _load_elorm_loop_kickoffs() -> List[Dict[str, Any]]:
    try:
        from distr.core.workflow.loop_preset_loader import list_preset_catalog_entries

        loaded = list_preset_catalog_entries()
        if loaded:
            return loaded
    except Exception:
        pass
    try:
        from distr.core.workflow.loop_preset_definitions import catalog_entries_from_definitions

        return catalog_entries_from_definitions()
    except Exception:
        pass
    return list(_ELORM_LEGACY_KICKOFFS)


def _definitions_catalog() -> List[Dict[str, Any]]:
    from distr.core.workflow.loop_preset_definitions import catalog_entries_from_definitions

    return catalog_entries_from_definitions()


ELORM_LOOP_KICKOFFS: List[Dict[str, Any]] = _load_elorm_loop_kickoffs()


def __getattr__(name: str):
    if name == "ELORM_LOOP_KICKOFFS_FALLBACK":
        return _definitions_catalog()
    raise AttributeError(name)


def infer_loop_archetype(text: str, parsed: Dict[str, Any] | None = None) -> str:
    """Guess elorm loop archetype from kickoff text."""
    lower = (text or "").lower()
    parsed = parsed or {}
    check = str(parsed.get("check_command") or "").lower()
    step1 = str(parsed.get("step_1") or "").lower()

    if "before any git commit" in lower or "pre-commit" in lower:
        return "event_gate"
    if "senior software engineer" in lower or ("plan.md" in lower and "ticket" in lower):
        return "incremental_ship"
    if "gh pr list" in check or "gh pr list" in lower or "babysitter" in lower or "codex-watch" in lower:
        return "watch_maintain"
    if "gh pr checks" in lower or "gh pr checks" in check:
        return "ship_with_ci"
    if "open pr" in step1 or ("open pr" in lower and "ci" in lower and "gh pr list" not in lower):
        return "ship_with_ci"
    if "spec.md" in lower or "unchecked" in lower:
        return "incremental_ship"
    if any(w in lower for w in ("review", "slop", "self-review", "naming")):
        return "review_cleanup"
    if any(w in check for w in ("build", "test:e2e", "coverage", "npm test")):
        return "check_fix_until_green"
    return "check_fix_until_green"


def archetype_planning_hint(archetype: str) -> str:
    """Short planner hint for a given archetype."""
    spec = LOOP_ARCHETYPES.get(archetype) or LOOP_ARCHETYPES["check_fix_until_green"]
    examples = ", ".join(spec.get("examples") or [])
    return (
        f"Archetype: {archetype} — {spec.get('description', '')}. "
        f"Similar loops: {examples}. Target ~{spec.get('typical_steps', 4)} steps."
    )
