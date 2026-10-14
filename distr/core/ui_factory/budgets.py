"""Role budgets for low-cost, bounded UI execution."""

from __future__ import annotations

ROLE_OUTPUT_TOKEN_BUDGETS = {
    "planning": 2_500,
    "template_selection": 600,
    "implementation": 8_000,
    "review": 2_000,
    "validation": 1_500,
    "correction": 4_000,
    "final_polish": 2_500,
    "reporting": 800,
}

LOCAL_CORRECTION_LIMIT = 2


def output_token_budget(role: str) -> int:
    return ROLE_OUTPUT_TOKEN_BUDGETS.get(str(role or "").strip().lower(), 2_000)


def budget_instruction(role: str, configured: int | None = None) -> str:
    limit = max(200, int(configured or output_token_budget(role)))
    return (
        f"Output budget: at most {limit} tokens for this {role or 'workflow'} step. "
        "Prefer concise diffs and evidence. Do not regenerate stable template code or restate inputs."
    )
