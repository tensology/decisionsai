"""
Benchmark Models Tool

Lets the agent answer conversational questions about the latest and best
models using the curated multi-source benchmark cache.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.services.llm_benchmark_service import (
    is_benchmark_cache_stale,
    load_benchmark_index,
    trigger_benchmark_refresh,
)

logger = logging.getLogger(__name__)

_REFERENCE_MARKER = "\n\nREFERENCE:\n"


class BenchmarkModelsInput(BaseModel):
    query: str = Field(
        default="",
        description="What you want to know about the latest, best, cheapest, fastest, or recommended AI models.",
    )


def _normalize_provider(value: str) -> str:
    text = (value or "").strip().lower()
    aliases = {
        "open ai": "openai",
        "openai": "openai",
        "anthropic": "anthropic",
        "claude": "anthropic",
        "google": "google",
        "gemini": "google",
        "deepseek": "deepseek",
        "meta": "meta",
        "mistral": "mistral",
        "xai": "xai",
        "grok": "xai",
        "nvidia": "nvidia",
        "qwen": "qwen",
        "alibaba": "qwen",
        "minimax": "minimax",
        "moonshot": "moonshot",
    }
    return aliases.get(text, text.replace(" ", ""))


def _query_type(query: str) -> str:
    text = (query or "").lower()
    if any(token in text for token in ("cheap", "cheapest", "value", "budget", "cost")):
        return "value"
    if any(token in text for token in ("fast", "latency", "speed", "responsive", "tokens per second")):
        return "speed"
    return "performance"


def _query_limit(query: str) -> int:
    match = re.search(r"\btop\s+([0-9]{1,2})\b", query or "", flags=re.I)
    if match:
        return max(1, min(int(match.group(1)), 10))
    return 5


def _query_provider(query: str) -> str:
    text = (query or "").lower()
    for token in (
        "openai",
        "anthropic",
        "claude",
        "google",
        "gemini",
        "deepseek",
        "meta",
        "mistral",
        "xai",
        "grok",
        "nvidia",
        "qwen",
        "minimax",
        "moonshot",
    ):
        if token in text:
            return _normalize_provider(token)
    return ""


def _query_focus(query: str) -> str:
    text = (query or "").lower()
    if any(token in text for token in ("code", "coding", "developer", "terminal", "workflow", "agent")):
        return "coding"
    if any(token in text for token in ("vision", "image", "multimodal")):
        return "vision"
    if any(token in text for token in ("video",)):
        return "video"
    return "general"


def _best_use_case(row: dict[str, Any], focus: str) -> bool:
    label = f"{row.get('label') or ''} {row.get('provider') or ''}".lower()
    if focus == "coding":
        return any(token in label for token in ("codex", "code", "coder", "claude", "deepseek", "gpt"))
    if focus == "vision":
        metrics = row.get("metrics") or {}
        return bool(metrics.get("context_window"))
    return True


def _row_sort_value(row: dict[str, Any], mode: str) -> float:
    metrics = row.get("metrics") or {}
    if mode == "value":
        return float(row.get("value_score") or 0.0)
    if mode == "speed":
        if metrics.get("output_speed_tps") is not None:
            return float(metrics.get("output_speed_tps") or 0.0)
        if metrics.get("latency_first_chunk_s") is not None:
            return 10000.0 - float(metrics.get("latency_first_chunk_s") or 0.0)
        return 0.0
    return float(row.get("performance_score") or 0.0)


def _summary_line(rows: list[dict[str, Any]], mode: str, focus: str, provider: str, refreshing: bool) -> str:
    if not rows:
        sentence = "I don't have a strong benchmark match in the cache yet."
    else:
        best = rows[0]
        metric_label = "performance" if mode == "performance" else "value" if mode == "value" else "speed"
        sentence = (
            f"Right now the strongest {focus if focus != 'general' else ''} models in the cache "
            f"{'for ' + provider if provider else ''} are led by {best.get('label') or 'that model'} "
            f"on {metric_label}."
        )
        sentence = " ".join(sentence.split())
    if refreshing:
        sentence += " I'm also refreshing the benchmark sources in the background so this can get newer without blocking the conversation."
    return sentence


def _reference_block(rows: list[dict[str, Any]], mode: str, focus: str, provider: str, payload: dict[str, Any]) -> str:
    lines = [
        f"mode={mode}",
        f"focus={focus}",
        f"provider_filter={provider or 'none'}",
        f"last_updated={payload.get('last_updated') or 'unknown'}",
        f"refreshing={bool(payload.get('refreshing'))}",
    ]
    source_meta = payload.get("sources") or {}
    if source_meta:
        lines.append("sources=" + ", ".join(
            f"{key}:{value.get('status')}:{value.get('row_count')}"
            for key, value in source_meta.items()
        ))
    for index, row in enumerate(rows, start=1):
        metrics = row.get("metrics") or {}
        source_labels = ", ".join(source.get("label") or source.get("id") or "" for source in (row.get("sources") or []))
        lines.append(
            f"{index}. model={row.get('label') or ''} | provider={row.get('provider') or ''} | "
            f"performance={row.get('performance_score')} | value={row.get('value_score')} | "
            f"context={metrics.get('context_window_label') or metrics.get('context_window') or ''} | "
            f"speed_tps={metrics.get('output_speed_tps') or ''} | latency_s={metrics.get('latency_first_chunk_s') or ''} | "
            f"price_blended={metrics.get('blended_price_per_1m') or ''} | input_price={metrics.get('input_price_per_1m') or ''} | "
            f"output_price={metrics.get('output_price_per_1m') or ''} | sources={source_labels}"
        )
    return _REFERENCE_MARKER + "\n".join(lines)


class BenchmarkModelsTool(BaseTool):
    name: str = "benchmark_models"
    description: str = (
        "USE THIS TOOL when the user asks about the latest models, best models, top models, cheapest models, "
        "fastest models, best coding models, model rankings, leaderboards, pricing, latency, context window, "
        "or what model to choose right now. Returns a short conversational summary FIRST, then a REFERENCE block "
        "with source-backed metrics and rankings."
    )
    args_schema: type[BaseModel] = BenchmarkModelsInput

    def get_triggers(self) -> list[str]:
        return [
            "latest models",
            "latest and greatest models",
            "best models",
            "top models",
            "best model",
            "what model should i use",
            "cheapest model",
            "fastest model",
            "best coding model",
            "model leaderboard",
            "model rankings",
        ]

    def _run(self, query: str = "", **kwargs) -> str:
        try:
            cache = load_benchmark_index(allow_sync_fill=False)
            if is_benchmark_cache_stale(cache):
                trigger_benchmark_refresh(force=True)
            rows = list(cache.get("leaderboard") or [])
            mode = _query_type(query)
            provider = _query_provider(query)
            focus = _query_focus(query)
            limit = _query_limit(query)
            if provider:
                rows = [row for row in rows if _normalize_provider(str(row.get("provider") or "")) == provider]
            rows = [row for row in rows if _best_use_case(row, focus)]
            rows.sort(key=lambda row: _row_sort_value(row, mode), reverse=True)
            selected = rows[:limit]
            payload = {
                "last_updated": cache.get("last_updated"),
                "sources": cache.get("sources") or {},
                "refreshing": is_benchmark_cache_stale(cache),
            }
            return _summary_line(selected, mode, focus, provider, payload["refreshing"]) + _reference_block(selected, mode, focus, provider, payload)
        except Exception as exc:
            logger.error("benchmark_models failed: %s", exc, exc_info=True)
            return f"I hit a problem while reading the benchmark cache.{_REFERENCE_MARKER}error={exc}"

    async def _arun(self, query: str = "", **kwargs) -> str:
        return self._run(query=query, **kwargs)
