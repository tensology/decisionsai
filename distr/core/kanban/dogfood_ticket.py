"""Standard dogfood ticket description template."""

from __future__ import annotations

DOGFOOD_TICKET_TEMPLATE = """## User journey
{user_journey}

## Acceptance criteria
{acceptance_criteria}

## Definition of done
- Workflow run status: completed
- Result packet includes Playwright screenshots and harness return contract
- Demo artifact in workflow pipeline/output/
"""


def format_dogfood_ticket_description(
    *,
    user_journey: str,
    acceptance_lines: list[str],
) -> str:
    criteria = "\n".join(f"- {line.strip()}" for line in acceptance_lines if line.strip())
    return DOGFOOD_TICKET_TEMPLATE.format(
        user_journey=user_journey.strip(),
        acceptance_criteria=criteria or "- (define acceptance criteria)",
    )
