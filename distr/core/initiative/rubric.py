"""Five-dimension initiative rubric (1–5 each) → policy thresholds (DESIGN §2.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_DIM = 3
_DIM_KEYS = ("impact", "risk", "cost", "urgency", "confidence")


def _clamp_int(value: Any, default: int = _DEFAULT_DIM) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(1, min(5, n))


@dataclass(frozen=True, slots=True)
class RubricScore:
    """Numeric rubric; ``risk`` is inverted vs plain English (5 = safest)."""

    impact: int
    risk: int
    cost: int
    urgency: int
    confidence: int

    @property
    def total(self) -> int:
        return (
            self.impact + self.risk + self.cost + self.urgency + self.confidence
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "impact": self.impact,
            "risk": self.risk,
            "cost": self.cost,
            "urgency": self.urgency,
            "confidence": self.confidence,
        }

    @classmethod
    def from_payload(cls, raw: dict[str, Any] | None) -> RubricScore | None:
        """Build from LLM JSON; missing dims → 3. Empty / non-dict → None."""
        if not raw or not isinstance(raw, dict):
            return None
        if not any(k in raw for k in _DIM_KEYS):
            return None
        return cls(
            impact=_clamp_int(raw.get("impact", _DEFAULT_DIM)),
            risk=_clamp_int(raw.get("risk", _DEFAULT_DIM)),
            cost=_clamp_int(raw.get("cost", _DEFAULT_DIM)),
            urgency=_clamp_int(raw.get("urgency", _DEFAULT_DIM)),
            confidence=_clamp_int(raw.get("confidence", _DEFAULT_DIM)),
        )

    def policy_decision(self, level: str) -> "PolicyDecision":
        """Map total score + autonomy level to execute / draft / skip."""
        # Local import: rubric.py is imported by policy.py at module level.
        from distr.core.initiative.policy import PolicyDecision

        lv = (level or "").strip().lower()
        t = self.total
        if t >= 18 and lv in ("operate", "own"):
            return PolicyDecision.EXECUTE
        if t >= 13:
            return PolicyDecision.DRAFT_AND_ASK
        return PolicyDecision.SKIP
