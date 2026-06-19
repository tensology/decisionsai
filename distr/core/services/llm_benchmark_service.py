from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests

from distr.core.paths import MODELS_DIR


logger = logging.getLogger(__name__)

TERMINAL_BENCH_URL = "https://www.tbench.ai/leaderboard/terminal-bench/2.0"
TERMINAL_BENCH_CACHE_PATH = os.path.join(MODELS_DIR, "terminal_bench_cache.json")
TERMINAL_BENCH_CACHE_TTL_SECONDS = 12 * 60 * 60


def normalize_model_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.split("/")[-1]
    raw = raw.replace("_", " ").replace("-", " ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\b(instruct|preview|latest|api|model)\b", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def parse_terminal_bench_entries(html: str) -> list[dict[str, Any]]:
    text = html or ""
    markers = ['\\"rows\\":[', '\\"entries\\":[', '"rows":[', '"entries":[']
    start = -1
    marker = ""
    for candidate in markers:
        start = text.find(candidate)
        if start >= 0:
            marker = candidate
            break
    if start < 0:
        raise ValueError("Terminal-Bench entries payload not found")
    idx = start + len(marker) - 1
    depth = 0
    end = -1
    while idx < len(text):
        char = text[idx]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
        idx += 1
    if end < 0:
        raise ValueError("Terminal-Bench entries payload was incomplete")
    encoded = text[start + len(marker) - 1:end]
    decoded = bytes(encoded, "utf-8").decode("unicode_escape")
    entries = json.loads(decoded)
    if not isinstance(entries, list):
        raise ValueError("Terminal-Bench entries payload was not a list")
    return entries


def _entry_model_label(entry: dict[str, Any]) -> str:
    model = entry.get("model")
    if isinstance(model, list) and model:
        return str(model[0]).strip()
    model_names = entry.get("modelNames")
    if isinstance(model_names, list) and model_names:
        return str(model_names[0]).strip()
    return ""


def aggregate_terminal_bench_models(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        label = _entry_model_label(entry)
        key = normalize_model_key(label) or normalize_model_key(
            ((entry.get("modelNames") or [None])[0] if isinstance(entry.get("modelNames"), list) else None)
        )
        if not key:
            continue
        accuracy = float(entry.get("accuracy") or 0.0) * 100.0
        row = grouped.setdefault(
            key,
            {
                "id": key,
                "model_name": label or key,
                "provider": ((entry.get("modelProviders") or [""])[0] if isinstance(entry.get("modelProviders"), list) else "") or "",
                "organization": ((entry.get("modelOrganization") or [""])[0] if isinstance(entry.get("modelOrganization"), list) else "") or "",
                "best_accuracy": 0.0,
                "average_accuracy": 0.0,
                "latest_date": "",
                "submission_count": 0,
                "verified_submission_count": 0,
                "agents": set(),
            },
        )
        row["submission_count"] += 1
        row["average_accuracy"] += accuracy
        row["best_accuracy"] = max(row["best_accuracy"], accuracy)
        if bool(entry.get("verified")):
            row["verified_submission_count"] += 1
        row["latest_date"] = max(row["latest_date"], str(entry.get("date") or ""))
        row["agents"].add(str(entry.get("agent") or "").strip())
    rows = []
    for row in grouped.values():
        submission_count = max(int(row["submission_count"]), 1)
        rows.append(
            {
                **row,
                "average_accuracy": round(float(row["average_accuracy"]) / submission_count, 1),
                "best_accuracy": round(float(row["best_accuracy"]), 1),
                "agents": sorted(agent for agent in row["agents"] if agent),
            }
        )
    rows.sort(key=lambda item: (item["best_accuracy"], item["average_accuracy"], item["submission_count"]), reverse=True)
    return rows


def _read_terminal_bench_cache() -> list[dict[str, Any]] | None:
    try:
        with open(TERMINAL_BENCH_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Failed to read Terminal-Bench cache: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    timestamp = payload.get("timestamp")
    rows = payload.get("leaderboard")
    if not isinstance(timestamp, (int, float)) or not isinstance(rows, list):
        return None
    if time.time() - float(timestamp) >= TERMINAL_BENCH_CACHE_TTL_SECONDS:
        return None
    return rows


def _write_terminal_bench_cache(rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(TERMINAL_BENCH_CACHE_PATH), exist_ok=True)
    temp_path = f"{TERMINAL_BENCH_CACHE_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump({"timestamp": time.time(), "leaderboard": rows}, handle)
    os.replace(temp_path, TERMINAL_BENCH_CACHE_PATH)


def load_terminal_bench_leaderboard(force_refresh: bool = False) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = _read_terminal_bench_cache()
        if cached is not None:
            return cached
    response = requests.get(TERMINAL_BENCH_URL, timeout=20)
    response.raise_for_status()
    rows = aggregate_terminal_bench_models(parse_terminal_bench_entries(response.text))
    try:
        _write_terminal_bench_cache(rows)
    except Exception as exc:
        logger.debug("Failed to write Terminal-Bench cache: %s", exc)
    return rows


def _infer_use_case(llm_type: str, model_name: str, provider: str) -> str:
    llm_type = (llm_type or "").strip().lower()
    label = f"{model_name} {provider}".lower()
    if llm_type in {"coding", "workflow", "computer_use"}:
        if "codex" in label or "coder" in label or "code" in label:
            return "Best for coding agents and implementation workflows."
        if "sonnet" in label or "opus" in label:
            return "Best for longer coding tasks with tool use and planning."
        if "flash" in label or "mini" in label or "fast" in label:
            return "Best for fast coding iterations and cheaper workflow runs."
        return "Best for terminal-style coding, workflow, and tool-driven tasks."
    if llm_type == "video":
        return "Best for video generation and prompt iteration."
    if llm_type == "image":
        return "Best for image generation and creative prompt refinement."
    if llm_type == "vision":
        return "Best for image-aware reasoning and multimodal review."
    return "Best for general conversation, tool use, and agent workflows."


def _value_score(row: dict[str, Any]) -> float:
    score = float(row.get("best_accuracy") or row.get("performance_score") or 0.0)
    label = f"{row.get('model_name') or row.get('label') or ''} {row.get('provider') or ''}".lower()
    if any(token in label for token in ("mini", "flash", "fast", "haiku", "free", "oss", "local", "nano")):
        score += 8.0
    if any(token in label for token in ("opus", "pro", "max")):
        score -= 2.0
    return round(score, 1)


def _profile_from_row(row: dict[str, Any], *, llm_type: str, requested_provider: str = "") -> dict[str, Any]:
    model_name = str(row.get("model_name") or row.get("label") or row.get("id") or "").strip()
    provider = str(row.get("provider") or requested_provider or "").strip().lower()
    performance = round(float(row.get("best_accuracy") or row.get("performance_score") or 0.0), 1)
    value_score = _value_score(row)
    return {
        "id": str(row.get("id") or normalize_model_key(model_name)),
        "label": model_name,
        "provider": provider,
        "organization": row.get("organization") or "",
        "performance_score": performance,
        "value_score": value_score,
        "summary": _infer_use_case(llm_type, model_name, provider),
        "best_use_case": _infer_use_case(llm_type, model_name, provider),
        "latest_date": row.get("latest_date") or "",
        "submission_count": int(row.get("submission_count") or 0),
        "verified_submission_count": int(row.get("verified_submission_count") or 0),
        "agents": row.get("agents") or [],
        "benchmark": {
            "source": "terminal-bench",
            "best_accuracy": performance,
            "average_accuracy": round(float(row.get("average_accuracy") or performance), 1),
        },
    }


def _fallback_profile(model: str, provider: str, llm_type: str) -> dict[str, Any]:
    label = (model or "Selected model").strip()
    provider = (provider or "").strip().lower()
    return {
        "id": normalize_model_key(label) or label.lower(),
        "label": label,
        "provider": provider,
        "organization": "",
        "performance_score": 0.0,
        "value_score": 0.0,
        "summary": _infer_use_case(llm_type, label, provider),
        "best_use_case": _infer_use_case(llm_type, label, provider),
        "latest_date": "",
        "submission_count": 0,
        "verified_submission_count": 0,
        "agents": [],
        "benchmark": {
            "source": "terminal-bench",
            "best_accuracy": 0.0,
            "average_accuracy": 0.0,
        },
    }


def build_llm_benchmark_payload(
    *,
    llm_type: str,
    provider: str,
    model: str,
    compare_model: str = "",
    sort: str = "performance",
    limit: int = 40,
) -> dict[str, Any]:
    leaderboard_rows = load_terminal_bench_leaderboard()
    by_id = {str(row.get("id") or ""): row for row in leaderboard_rows}
    selected_key = normalize_model_key(model)
    compare_key = normalize_model_key(compare_model)
    selected = _profile_from_row(by_id[selected_key], llm_type=llm_type, requested_provider=provider) if selected_key in by_id else _fallback_profile(model, provider, llm_type)
    comparison = _profile_from_row(by_id[compare_key], llm_type=llm_type, requested_provider="") if compare_key in by_id else (
        _profile_from_row(leaderboard_rows[1], llm_type=llm_type) if len(leaderboard_rows) > 1 else _fallback_profile(compare_model or "Comparison model", "", llm_type)
    )
    sort_key = "value_score" if str(sort or "").lower() == "value" else "performance_score"
    leaderboard = [_profile_from_row(row, llm_type=llm_type) for row in leaderboard_rows]
    leaderboard.sort(key=lambda row: (float(row.get(sort_key) or 0.0), float(row.get("performance_score") or 0.0)), reverse=True)
    return {
        "type": llm_type,
        "sort": "value" if sort_key == "value_score" else "performance",
        "selected_model": selected,
        "comparison_model": comparison,
        "leaderboard": leaderboard[: max(int(limit or 40), 1)],
        "source": {
            "id": "terminal-bench",
            "label": "Terminal-Bench 2.0",
            "url": TERMINAL_BENCH_URL,
        },
    }
