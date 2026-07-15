"""Model- and harness-neutral durable memory deltas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _strings(value: Any, *, limit: int = 40, item_limit: int = 1000) -> list[str]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in result:
            result.append(clean[:item_limit])
        if len(result) >= limit:
            break
    return result


@dataclass
class MemoryDelta:
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [self.summary.strip()] if self.summary.strip() else []
        for label, values in (
            ("Facts", self.facts),
            ("Decisions", self.decisions),
            ("Constraints", self.constraints),
            ("Commands", self.commands),
            ("Files", self.changed_files),
            ("Artifacts", self.artifacts),
            ("Risks", self.risks),
            ("Blockers", self.blockers),
            ("Next actions", self.next_actions),
            ("Evidence", self.evidence),
        ):
            if values:
                lines.append(f"{label}: " + "; ".join(values))
        return "\n".join(lines).strip()


def normalize_memory_delta(raw: dict[str, Any] | None, *, summary: str = "", provenance: dict[str, Any] | None = None) -> MemoryDelta:
    data = dict(raw or {})
    return MemoryDelta(
        summary=str(data.get("summary") or summary or "").strip()[:2000],
        facts=_strings(data.get("facts")),
        decisions=_strings(data.get("decisions")),
        constraints=_strings(data.get("constraints")),
        commands=_strings(data.get("commands") or data.get("commands_run")),
        changed_files=_strings(data.get("changed_files") or data.get("files_changed")),
        artifacts=_strings(data.get("artifacts")),
        risks=_strings(data.get("risks") or data.get("security")),
        blockers=_strings(data.get("blockers")),
        next_actions=_strings(data.get("next_actions")),
        evidence=_strings(data.get("evidence") or data.get("tests_run")),
        provenance=dict(provenance or data.get("provenance") or {}),
    )
