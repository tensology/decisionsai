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
ROLE_MAX_CHARS = {
    "planning": 6_500,
    "implementation": 7_500,
    "correction": 7_500,
    "review": 7_000,
    "final_polish": 7_500,
    "reporting": 5_500,
}
_WORD_RE = re.compile(r"[a-z0-9_./-]{3,}", re.IGNORECASE)
_SOURCE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CONTRACT_SECTION_NAMES = {
    "non goals",
    "acceptance criteria",
    "browser evidence required",
    "dependencies",
    "expected artifacts",
    "rollback notes",
    "model and route",
    "supplied source urls",
}


def _clean(value: Any) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()


def _bounded(value: Any, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    marker = "\n[section compacted]\n"
    head = max(0, int(limit * 0.72))
    tail = max(0, limit - head - len(marker))
    head_text = text[:head]
    tail_text = text[-tail:] if tail else ""
    # Avoid fragments such as ``manage.p [section compacted] is implemented``.
    # A worker should receive fewer complete statements, never corrupted ones.
    if head < len(text):
        boundary = max(head_text.rfind("\n"), head_text.rfind(". "), head_text.rfind("; "))
        if boundary >= int(head * 0.6):
            head_text = head_text[: boundary + 1]
    if tail_text:
        candidates = [pos for pos in (tail_text.find("\n"), tail_text.find(". "), tail_text.find("; ")) if pos >= 0]
        if candidates:
            boundary = min(candidates)
            if boundary <= int(tail * 0.4):
                tail_text = tail_text[boundary + 1 :]
    return head_text.rstrip() + marker + tail_text.lstrip()


def _fingerprint(value: str) -> str:
    return sha256(_clean(value).encode("utf-8", errors="ignore")).hexdigest()[:12]


def handoff_budget_for_role(role: Any) -> int:
    """Return the bounded worker prompt budget for one workflow role."""
    return ROLE_MAX_CHARS.get(str(role or "").strip().lower(), DEFAULT_MAX_CHARS)


def extract_source_urls(value: Any, *, limit: int = 12) -> list[str]:
    """Preserve exact ticket URLs as critical handoff references."""
    urls = (
        match.group(0).rstrip(".,;:!?)]}")
        for match in _SOURCE_URL_RE.finditer(str(value or ""))
    )
    return _unique_text(urls, limit=limit)


def extract_ticket_contract(value: Any, *, max_chars: int = 2_200) -> str:
    """Preserve contract sections that must survive objective compaction.

    Ticket descriptions are often longer than the objective budget. Keeping a
    prefix loses late acceptance/browser requirements and makes every model
    reason from a different, weaker contract. This deterministic extractor
    promotes those sections into a separate critical handoff block.
    """
    selected: list[str] = []
    active = False
    for raw_line in str(value or "").splitlines():
        line = raw_line.rstrip()
        stripped = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
        top_level = bool(stripped) and not stripped.startswith(("-", "*", ">")) and ":" in stripped
        if top_level:
            label = stripped.split(":", 1)[0]
            normalized = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
            active = normalized in _CONTRACT_SECTION_NAMES
            if active:
                selected.append(line)
            continue
        if active:
            selected.append(line)
    contract = "\n".join(selected).strip()
    return _bounded(contract, max_chars) if contract else ""


def extract_required_handoff_fields(
    value: Any,
    required_fields: Iterable[Any],
    *,
    max_chars: int = 2_400,
) -> str:
    """Extract upstream outputs explicitly required by the current step.

    Required context is execution state, not optional conversation history. It
    must survive prompt compaction so the next worker does not pay to rediscover
    facts already established by the preceding step.
    """
    required = {
        re.sub(r"[^a-z0-9]+", " ", str(item or "").lower()).strip()
        for item in required_fields
        if str(item or "").strip()
    }
    aliases = {
        "final changed files": {"files changed", "drift check"},
        "command log": {"tests run", "commands run", "command", "host command", "exit code"},
        "evidence": {
            "tests run",
            "test results",
            "exit code",
            "drift check",
            "security",
            "ui assessment",
            "browser evidence",
            "blockers",
            "ship verdict",
        },
        "memory delta": {"summary", "self corrections", "lessons", "remaining risks"},
    }
    for name in list(required):
        required.update(aliases.get(name, set()))
    if not required:
        return ""
    captured: list[str] = []
    active = False
    for raw_line in str(value or "").splitlines():
        cleaned = re.sub(r"^[\s#>*+-]+", "", raw_line).strip()
        cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
        label = cleaned.split(":", 1)[0] if ":" in cleaned else ""
        normalized = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        if label:
            if normalized in required:
                active = True
                captured.append(cleaned)
                continue
            if active:
                active = False
        if active:
            captured.append(raw_line.rstrip())
    return _bounded("\n".join(captured).strip(), max_chars) if captured else ""


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
    ticket_contract: str = ""
    required_context: str = ""
    workflow_map: str = ""
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
            "ticket_contract": self.ticket_contract,
            "required_context": self.required_context,
            "current_step": dict(self.current_step),
            "workflow_map": self.workflow_map,
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

        # The worker must learn what it is doing before it sees supporting
        # project material.  In particular, tool-free synthesis steps can be
        # derailed by an early AGENTS.md or artifact reference: smaller models
        # try to inspect the reference before reaching the actual guardrail.
        # Keep the current instruction immediately after identity in both the
        # normal and compacted packet layouts.
        step_title = _clean(self.current_step.get("title") or "Current step")
        step_instruction = _bounded(self.current_step.get("instruction"), 2_200)
        sections.append(("current_step", f"## Current step: {step_title}\n{step_instruction}"))

        sections.append(("objective", "## Objective and ticket context\n" + _bounded(self.objective, 3_200)))
        if _clean(self.ticket_contract):
            sections.append(("ticket_contract", "## Non-negotiable ticket contract\n" + _bounded(self.ticket_contract, 2_200)))
        if _clean(self.required_context):
            sections.append(("required_context", "## Required upstream context\n" + _bounded(self.required_context, 2_400)))

        if _clean(self.workflow_map):
            sections.append(("workflow_map", "## Whole-run coordination map\n" + _bounded(self.workflow_map, 1_800)))

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
        raw_total_chars = len(prompt)
        if len(prompt) > max_chars:
            # Preserve identity, current instruction, and return contract as
            # atomic sections. Spend the remaining budget on objective/history.
            # Reference paths are high leverage for cheaper models: preserving
            # them prevents repeated directory discovery calls.
            critical_names = {
                "identity",
                "ticket_contract",
                "required_context",
                "current_step",
                "human_steering",
                "references",
                "return_contract",
            }
            critical = [(name, value) for name, value in deduped if name in critical_names]
            optional = [(name, value) for name, value in deduped if name not in critical_names]
            marker = "[optional handoff context compacted to configured budget]"
            critical_chars = sum(len(value) for _name, value in critical) + max(0, len(critical) - 1) * 2
            available = max(0, max_chars - critical_chars - len(marker) - 8)
            optional_text = "\n\n".join(value for _name, value in optional)
            optional_text = _bounded(optional_text, available) if available else ""
            critical_map = dict(critical)
            # Put the actual ticket objective ahead of supporting file paths.
            # Cheaper models otherwise tend to exhaust their inspection budget
            # walking references before they have read what the user asked for.
            ordered = [
                critical_map.get("identity", ""),
                critical_map.get("current_step", ""),
            ]
            if optional_text:
                ordered.extend([marker, optional_text])
            if critical_map.get("ticket_contract"):
                ordered.append(critical_map["ticket_contract"])
            if critical_map.get("required_context"):
                ordered.append(critical_map["required_context"])
            if critical_map.get("human_steering"):
                ordered.append(critical_map["human_steering"])
            if critical_map.get("references"):
                ordered.append(critical_map["references"])
            ordered.append(critical_map.get("return_contract", ""))
            prompt = "\n\n".join(value for value in ordered if value)
            if len(prompt) > max_chars:
                prompt = prompt[:max_chars]

        telemetry = {
            "packet_version": self.version,
            "total_chars": len(prompt),
            "raw_total_chars": raw_total_chars,
            "max_chars": max_chars,
            "estimated_input_tokens": max(1, (len(prompt) + 3) // 4),
            "estimated_tokens_saved": max(0, (raw_total_chars - len(prompt) + 3) // 4),
            "compacted": raw_total_chars > len(prompt),
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
