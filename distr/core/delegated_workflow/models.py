"""Typed delegated workflow contracts used by Hermes orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _redact(value: Any) -> Any:
    try:
        from distr.core.orchestrator import redact_handoff_payload

        return redact_handoff_payload(value)
    except Exception:
        return value


@dataclass(frozen=True)
class DelegatedStep:
    action: str
    preferred_route: str
    fallback_routes: list[str] = field(default_factory=list)
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    verifies: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass(frozen=True)
class DelegatedPlan:
    kind: str
    source_surface: str
    original_instruction: str
    steps: list[DelegatedStep]
    requires_approval_before: list[str] = field(default_factory=list)
    target_backend: str = ""
    confidence: float = 0.7

    def to_safe_dict(self) -> dict[str, Any]:
        return _redact({
            "kind": self.kind,
            "source_surface": self.source_surface,
            "original_instruction": self.original_instruction,
            "steps": [step.to_safe_dict() for step in self.steps],
            "requires_approval_before": list(self.requires_approval_before),
            "target_backend": self.target_backend,
            "confidence": self.confidence,
        })


@dataclass(frozen=True)
class Roadblock:
    code: str
    title: str
    detail: str
    options: list[str] = field(default_factory=list)
    retryable: bool = True
    payload: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


@dataclass(frozen=True)
class DelegatedRunReport:
    status: str
    plan: DelegatedPlan
    completed_steps: list[str] = field(default_factory=list)
    current_step: str = ""
    roadblock: Roadblock | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return _redact(payload)
