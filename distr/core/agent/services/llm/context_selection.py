"""Pure, non-destructive selection of complete conversation turns."""

from __future__ import annotations

import json
from typing import Any, Iterable


def _estimated_tokens(messages: Iterable[dict[str, Any]]) -> int:
    """Conservative provider-agnostic estimate including message overhead."""
    total = 0
    for message in messages:
        try:
            encoded = json.dumps(message, ensure_ascii=False, default=str)
        except Exception:
            encoded = str(message)
        total += max(4, (len(encoded) + 2) // 3)
    return total


def _conversation_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group messages into user-led units so tool exchanges are never split."""
    units: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user" and current:
            units.append(current)
            current = []
        current.append(message)
    if current:
        units.append(current)
    return units


def select_messages_for_context(
    messages: Iterable[dict[str, Any]],
    *,
    max_tokens: int,
    reserve_tokens: int = 4096,
) -> list[dict[str, Any]]:
    """Return system messages plus the newest complete units within the budget.

    The input is never mutated. The newest conversational unit is retained even
    when it alone exceeds the estimated budget, ensuring the active request is
    never silently discarded.
    """
    source = list(messages or [])
    if not source:
        return []

    systems = [message for message in source if message.get("role") == "system"]
    conversation = [message for message in source if message.get("role") != "system"]
    units = _conversation_units(conversation)
    available = max(1, int(max_tokens or 0) - max(0, int(reserve_tokens or 0)))
    used = _estimated_tokens(systems)
    selected_reversed: list[list[dict[str, Any]]] = []

    for unit in reversed(units):
        unit_cost = _estimated_tokens(unit)
        if selected_reversed and used + unit_cost > available:
            break
        selected_reversed.append(unit)
        used += unit_cost

    selected = list(systems)
    for unit in reversed(selected_reversed):
        selected.extend(unit)
    return selected

