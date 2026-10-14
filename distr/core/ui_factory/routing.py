"""Evidence-gated local-first route selection for UI work."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .budgets import LOCAL_CORRECTION_LIMIT, output_token_budget


@dataclass(frozen=True)
class UIRouteDecision:
    backend: str
    provider: str
    model: str
    role: str
    output_token_budget: int
    hosted: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def choose_ui_execution_route(
    role: str,
    *,
    local_model: str = "qwen3.8:27b",
    quality_failures: int = 0,
    requires_hosted_capability: bool = False,
) -> UIRouteDecision:
    clean_role = str(role or "implementation").strip().lower()
    escalate = requires_hosted_capability or int(quality_failures or 0) >= LOCAL_CORRECTION_LIMIT
    if escalate:
        reason = (
            "Hosted escalation was unlocked because the required capability is unavailable locally."
            if requires_hosted_capability
            else f"Hosted escalation was unlocked after {quality_failures} failed local quality gates."
        )
        return UIRouteDecision(
            backend="codex",
            provider="openai",
            model="auto",
            role=clean_role,
            output_token_budget=output_token_budget(clean_role),
            hosted=True,
            reason=reason,
        )
    return UIRouteDecision(
        backend="pi",
        provider="ollama",
        model=local_model,
        role=clean_role,
        output_token_budget=output_token_budget(clean_role),
        hosted=False,
        reason="Use the certified local UI worker until an executable quality gate fails twice.",
    )
