"""Compact UI-factory learning signals with central MemPalace dual-write."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class UIFactoryOutcome:
    screen_type: str
    template_pack: str
    model: str
    provider: str
    passed: bool
    output_tokens: int = 0
    wall_time_seconds: float = 0.0
    correction_count: int = 0
    hosted_escalated: bool = False
    rejection_labels: tuple[str, ...] = field(default_factory=tuple)


def record_ui_factory_outcome(
    outcome: UIFactoryOutcome,
    *,
    project_id: int | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = asdict(outcome)
    summary = (
        f"UI factory {outcome.template_pack} {'passed' if outcome.passed else 'failed'} "
        f"with {outcome.provider}/{outcome.model}; corrections={outcome.correction_count}; "
        f"hosted_escalated={outcome.hosted_escalated}; rejections={','.join(outcome.rejection_labels) or 'none'}"
    )
    from distr.core.orchestrator import record_learning_signal
    from distr.core.mempalace.wiring import dual_write_learning

    rule_id = record_learning_signal(
        scope="project" if project_id is not None else "global",
        scope_id=project_id,
        rule_type="ui_factory_outcome",
        summary=summary,
        payload=payload,
        enabled=False,
        promote_after=3,
    )
    memory = dual_write_learning(
        insight=json.dumps(payload, sort_keys=True),
        key=f"ui-factory:{outcome.screen_type}:{outcome.template_pack}",
        learning_type="ui_factory_outcome",
        settings=settings,
    )
    return {"rule_id": rule_id, "mempalace": memory, "payload": payload}
