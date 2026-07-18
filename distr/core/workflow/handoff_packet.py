"""Compact, typed state passed between DecisionsAI workflow steps.

The packet is intentionally a state transition rather than a conversation
transcript. Durable logs and workspace memory stay at their source paths; a
worker receives only the current objective, relevant facts, prior outcomes,
and references needed to inspect the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re
from typing import Any, Iterable


PACKET_VERSION = 1
DEFAULT_MAX_CHARS = 8_000
_WORD_RE = re.compile(r"[a-z0-9_./-]{3,}", re.IGNORECASE)


def _clean(value: Any) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()


def _bounded(value: Any, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    marker = "\n[section compacted]\n"
    head = max(0, int(limit * 0.72))
    tail = max(0, limit - head - len(marker))
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _fingerprint(value: str) -> str:
    return sha256(_clean(value).encode("utf-8", errors="ignore")).hexdigest()[:12]


def _unique_text(items: Iterable[Any], *, limit: int = 40) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean(item)
        if not text:
            continue
        key = _fingerprint(text)
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
        if len(output) >= limit:
            break
    return output


def select_relevant_memory(
    candidates: Iterable[Any],
    *,
    query: str,
    max_items: int = 8,
    max_item_chars: int = 320,
) -> list[str]:
    """Choose deterministic, query-relevant memory fragments without a model call."""
    query_terms = set(_WORD_RE.findall((query or "").lower()))
    ranked: list[tuple[int, int, str]] = []
    for order, candidate in enumerate(_unique_text(candidates, limit=80)):
        for fragment in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z])", candidate):
            text = " ".join(fragment.split()).strip(" -")
            if len(text) < 8:
                continue
            terms = set(_WORD_RE.findall(text.lower()))
            overlap = len(query_terms & terms)
            # Explicit constraints and recent decisions remain useful even when
            # their vocabulary does not perfectly match the current step.
            priority = 2 if any(token in text.lower() for token in ("must", "do not", "decision", "blocker", "acceptance")) else 0
            ranked.append((overlap * 10 + priority, -order, text[:max_item_chars]))
    ranked.sort(reverse=True)
    selected = [text for score, _order, text in ranked if score > 0][:max_items]
    return _unique_text(selected, limit=max_items)


@dataclass(slots=True)
class StepHandoffPacket:
    identity: dict[str, Any]
    objective: str
    current_step: dict[str, Any]
    constraints: list[str] = field(default_factory=list)
    prior_outcomes: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    memory_facts: list[str] = field(default_factory=list)
    continuation: str = ""
    return_contract: str = ""
    version: int = PACKET_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "identity": dict(self.identity),
            "objective": self.objective,
            "current_step": dict(self.current_step),
            "constraints": list(self.constraints),
            "prior_outcomes": list(self.prior_outcomes),
            "artifact_refs": list(self.artifact_refs),
            "memory_refs": list(self.memory_refs),
            "memory_facts": list(self.memory_facts),
            "continuation": self.continuation,
        }

    def render(self, *, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, dict[str, Any]]:
        sections: list[tuple[str, str]] = []
        identity_lines = [
            f"- {key}: {value}"
            for key, value in self.identity.items()
            if value not in (None, "", [])
        ]
        sections.append(("identity", "# DecisionsAI step handoff\n\nPacket version: " + str(self.version) + "\n\n## Identity\n" + "\n".join(identity_lines)))
        sections.append(("objective", "## Objective and ticket context\n" + _bounded(self.objective, 3_200)))

        step_title = _clean(self.current_step.get("title") or "Current step")
        step_instruction = _bounded(self.current_step.get("instruction"), 2_200)
        sections.append(("current_step", f"## Current step: {step_title}\n{step_instruction}"))

        constraints = _unique_text(self.constraints, limit=12)
        if constraints:
            sections.append(("constraints", "## Constraints and validation\n" + _bounded("\n\n".join(constraints), 1_200)))

        prior_lines: list[str] = []
        for outcome in self.prior_outcomes[-3:]:
            title = _clean(outcome.get("title") or "Prior step")
            status = _clean(outcome.get("status") or "completed")
            summary = _bounded(outcome.get("summary") or outcome.get("result"), 420)
            prior_lines.append(f"- {title} [{status}]: {summary}")
        if prior_lines:
            sections.append(("prior_outcomes", "## Prior step deltas\n" + "\n".join(prior_lines)))

        refs = _unique_text([*self.artifact_refs, *self.memory_refs], limit=12)
        if refs:
            sections.append(("references", "## Evidence and memory references\n" + "\n".join(f"- {item}" for item in refs)))
        facts = _unique_text(self.memory_facts, limit=5)
        if facts:
            sections.append(("memory_facts", "## Relevant memory facts\n" + "\n".join(f"- {item}" for item in facts)))
        if _clean(self.continuation):
            sections.append(("human_steering", "## Latest human steering\n" + _bounded(self.continuation, 1_000)))
        if _clean(self.return_contract):
            sections.append(("return_contract", "## Return contract\n" + _bounded(self.return_contract, 1_500)))

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for name, value in sections:
            value = _clean(value)
            fingerprint = _fingerprint(value)
            if fingerprint in seen:
                duplicates.append(name)
                continue
            seen.add(fingerprint)
            deduped.append((name, value))

        prompt = "\n\n".join(value for _name, value in deduped)
        if len(prompt) > max_chars:
            # Preserve identity, current instruction, and return contract as
            # atomic sections. Spend the remaining budget on objective/history.
            critical_names = {"identity", "current_step", "return_contract"}
            critical = [(name, value) for name, value in deduped if name in critical_names]
            optional = [(name, value) for name, value in deduped if name not in critical_names]
            marker = "[optional handoff context compacted to configured budget]"
            critical_chars = sum(len(value) for _name, value in critical) + max(0, len(critical) - 1) * 2
            available = max(0, max_chars - critical_chars - len(marker) - 8)
            optional_text = "\n\n".join(value for _name, value in optional)
            optional_text = _bounded(optional_text, available) if available else ""
            critical_map = dict(critical)
            ordered = [critical_map.get("identity", ""), critical_map.get("current_step", "")]
            if optional_text:
                ordered.extend([marker, optional_text])
            ordered.append(critical_map.get("return_contract", ""))
            prompt = "\n\n".join(value for value in ordered if value)
            if len(prompt) > max_chars:
                prompt = prompt[:max_chars]

        telemetry = {
            "packet_version": self.version,
            "total_chars": len(prompt),
            "max_chars": max_chars,
            "section_chars": {name: len(value) for name, value in deduped},
            "section_hashes": {name: _fingerprint(value) for name, value in deduped},
            "deduplicated_sections": duplicates,
            "prior_outcome_count": len(self.prior_outcomes),
            "memory_fact_count": len(facts),
            "reference_count": len(refs),
            "budget_utilization": round(len(prompt) / max(1, max_chars), 3),
            "trimmed_prior_outcomes": max(0, len(self.prior_outcomes) - 3),
            "trimmed_references": max(0, len([*self.artifact_refs, *self.memory_refs]) - len(refs)),
        }
        return prompt, telemetry
