from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests

from distr.core.paths import MODELS_DIR


logger = logging.getLogger(__name__)

TERMINAL_BENCH_URL = "https://www.tbench.ai/leaderboard/terminal-bench/2.1"
ARTIFICIAL_ANALYSIS_URL = "https://artificialanalysis.ai/leaderboards/models"
KILO_URL = "https://kilo.ai/leaderboard"
ONYX_URL = "https://onyx.app/llm-leaderboard"
OPENWEBUI_URL = "https://openwebui.com/leaderboard"
OPEN_LLM_LEADERBOARD_URL = "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard#/"

BENCHMARK_CACHE_PATH = os.path.join(MODELS_DIR, "llm_benchmark_cache.json")
BENCHMARK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

_refresh_running = False
_refresh_lock = threading.Lock()


def normalize_model_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.split("/")[-1]
    raw = re.sub(r"\bclaude\s+([0-9.]+)\s+(fable|opus|sonnet|haiku)\b", r"claude \2 \1", raw)
    raw = re.sub(r"\bgpt\s+([0-9.]+)\s+(mini|nano|pro|codex|flash)\b", r"gpt \2 \1", raw)
    raw = re.sub(r"\([^)]*\)", " ", raw)
    raw = raw.replace("_", " ").replace("-", " ")
    raw = re.sub(r"\b(with|fallback|adaptive|reasoning|max|xhigh|high|medium|effort|api|model|preview|latest)\b", " ", raw)
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict[str, Any]]]] = []
        self._table: list[list[dict[str, Any]]] | None = None
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        if tag == "table":
            self._table = []
            return
        if tag == "tr" and self._table is not None:
            self._row = []
            return
        if tag in {"td", "th"} and self._row is not None:
            self._cell = {"text_parts": [], "hrefs": []}
            return
        if self._cell is None:
            return
        if tag == "a" and attr_map.get("href"):
            self._cell["hrefs"].append(attr_map["href"])
        if attr_map.get("aria-label"):
            self._cell["text_parts"].append(attr_map["aria-label"])
        if tag == "img" and attr_map.get("alt"):
            self._cell["text_parts"].append(attr_map["alt"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = " ".join(" ".join(self._cell.get("text_parts") or []).split())
            hrefs = [href for href in self._cell.get("hrefs") or [] if href]
            self._row.append({"text": text, "hrefs": hrefs})
            self._cell = None
            return
        if tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
            return
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        text = " ".join(data.split())
        if text:
            self._cell["text_parts"].append(text)


def _http_get(url: str) -> str:
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 DecisionsAI benchmark fetcher"},
    )
    response.raise_for_status()
    return response.text


def _parse_money(value: str | None) -> float | None:
    text = (value or "").strip().replace(",", "")
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    match = re.search(r"-?\$?([0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def _parse_percent(value: str | None) -> float | None:
    text = (value or "").strip().replace(",", "")
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def _parse_float(value: str | None) -> float | None:
    text = (value or "").strip().replace(",", "")
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def _parse_context_tokens(value: str | None) -> int | None:
    text = (value or "").strip().upper().replace(",", "")
    if not text or text in {"--", "N/A"}:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


def _format_source_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _best_metric(rows: list[dict[str, Any]], metric_key: str) -> Any:
    for row in rows:
        metrics = row.get("metrics") or {}
        value = metrics.get(metric_key)
        if value not in (None, "", [], {}):
            return value
    return None


def _source_metric(
    *,
    source_id: str,
    source_label: str,
    source_url: str,
    detail_url: str | None,
    model_name: str,
    provider: str,
    rank: int | None,
    performance_raw: float | None,
    value_raw: float | None,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_label": source_label,
        "source_url": source_url,
        "detail_url": detail_url or source_url,
        "model_name": model_name,
        "provider": provider,
        "rank": rank,
        "performance_raw": performance_raw,
        "value_raw": value_raw,
        "metrics": {k: v for k, v in metrics.items() if v not in (None, "", [], {})},
        "updated_at": _format_source_timestamp(),
    }


def parse_terminal_bench_entries(html: str) -> list[dict[str, Any]]:
    markers = ['\\"rows\\":[', '\\"entries\\":[', '"rows":[', '"entries":[']
    text = html or ""
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


def _dedupe_repeated_tail(value: str) -> str:
    words = value.split()
    if len(words) % 2 == 0 and words[: len(words) // 2] == words[len(words) // 2 :]:
        return " ".join(words[: len(words) // 2])
    return value


def _parse_table_rows(html: str) -> list[list[dict[str, Any]]]:
    parser = _HTMLTableParser()
    parser.feed(html)
    if not parser.tables:
        return []
    return max(parser.tables, key=len)


def _fetch_terminal_bench_rows() -> list[dict[str, Any]]:
    entries = parse_terminal_bench_entries(_http_get(TERMINAL_BENCH_URL))
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        model_name = _entry_model_label(entry)
        key = normalize_model_key(model_name)
        if not key:
            continue
        accuracy = float(entry.get("accuracy") or 0.0) * 100.0
        row = grouped.setdefault(
            key,
            {
                "key": key,
                "model_name": model_name,
                "provider": ((entry.get("modelProviders") or [""])[0] if isinstance(entry.get("modelProviders"), list) else "") or "",
                "organization": ((entry.get("modelOrganization") or [""])[0] if isinstance(entry.get("modelOrganization"), list) else "") or "",
                "best_accuracy": 0.0,
                "average_accuracy": 0.0,
                "latest_date": "",
                "submission_count": 0,
                "verified_submission_count": 0,
            },
        )
        row["submission_count"] += 1
        row["average_accuracy"] += accuracy
        row["best_accuracy"] = max(row["best_accuracy"], accuracy)
        row["latest_date"] = max(row["latest_date"], str(entry.get("date") or ""))
        if bool(entry.get("verified")):
            row["verified_submission_count"] += 1
    rows = []
    sorted_rows = sorted(grouped.values(), key=lambda item: (item["best_accuracy"], item["submission_count"]), reverse=True)
    for index, row in enumerate(sorted_rows, start=1):
        submission_count = max(int(row["submission_count"]), 1)
        performance = round(float(row["best_accuracy"]), 1)
        rows.append(
            _source_metric(
                source_id="terminal_bench",
                source_label="Terminal-Bench 2.1",
                source_url=TERMINAL_BENCH_URL,
                detail_url=TERMINAL_BENCH_URL,
                model_name=str(row["model_name"] or row["key"]),
                provider=str(row.get("provider") or ""),
                rank=index,
                performance_raw=performance,
                value_raw=(performance / max(submission_count, 1)) if performance else None,
                metrics={
                    "best_accuracy": performance,
                    "average_accuracy": round(float(row["average_accuracy"]) / submission_count, 1),
                    "submission_count": submission_count,
                    "verified_submission_count": int(row["verified_submission_count"] or 0),
                    "latest_date": row.get("latest_date") or "",
                },
            )
        )
    return rows


def _fetch_artificial_analysis_rows() -> list[dict[str, Any]]:
    rows = _parse_table_rows(_http_get(ARTIFICIAL_ANALYSIS_URL))
    if len(rows) < 3:
        return []
    data_rows = rows[2:]
    parsed = []
    for index, row in enumerate(data_rows, start=1):
        if len(row) < 8:
            continue
        model_name = row[0]["text"]
        if not model_name:
            continue
        provider = " ".join(dict.fromkeys((row[2]["text"] or "").split()))
        intelligence = _parse_float(row[3]["text"])
        blended_price = _parse_money(row[4]["text"])
        speed_tps = _parse_float(row[5]["text"])
        latency_s = _parse_float(row[6]["text"])
        response_s = _parse_float(row[7]["text"])
        detail_url = None
        if len(row) > 8 and row[8].get("hrefs"):
            detail_url = urljoin(ARTIFICIAL_ANALYSIS_URL, row[8]["hrefs"][0])
        parsed.append(
            _source_metric(
                source_id="artificial_analysis",
                source_label="Artificial Analysis",
                source_url=ARTIFICIAL_ANALYSIS_URL,
                detail_url=detail_url,
                model_name=model_name,
                provider=provider,
                rank=index,
                performance_raw=intelligence,
                value_raw=(intelligence / blended_price) if intelligence and blended_price else None,
                metrics={
                    "context_window": _parse_context_tokens(row[1]["text"]),
                    "context_window_label": row[1]["text"] or "",
                    "creator": provider,
                    "intelligence_index": intelligence,
                    "blended_price_per_1m": blended_price,
                    "output_speed_tps": speed_tps,
                    "latency_first_chunk_s": latency_s,
                    "end_to_end_response_s": response_s,
                },
            )
        )
    return parsed


def _fetch_kilo_rows() -> list[dict[str, Any]]:
    parser = _HTMLTableParser()
    parser.feed(_http_get(KILO_URL))
    target_table: list[list[dict[str, Any]]] = []
    for table in parser.tables:
        if table and [cell["text"] for cell in table[0]] == ["Rank", "Model", "Completion", "Cost per attempt"]:
            target_table = table
            break
    if len(target_table) < 2:
        return []
    parsed = []
    for row in target_table[1:]:
        if len(row) < 4:
            continue
        rank = int(_parse_float(row[0]["text"]) or 0) or None
        raw_model = row[1]["text"]
        provider = ""
        model_name = raw_model
        if ":" in raw_model:
            left, right = raw_model.split(":", 1)
            provider = re.sub(r"\s+logo\b", "", left, flags=re.I).strip()
            model_name = _dedupe_repeated_tail(right.strip())
        detail_url = row[1]["hrefs"][0] if row[1].get("hrefs") else None
        completion = _parse_percent(row[2]["text"])
        cost_per_attempt = _parse_money(row[3]["text"])
        parsed.append(
            _source_metric(
                source_id="kilo",
                source_label="Kilo",
                source_url=KILO_URL,
                detail_url=urljoin(KILO_URL, detail_url) if detail_url else KILO_URL,
                model_name=model_name,
                provider=provider,
                rank=rank,
                performance_raw=completion,
                value_raw=(completion / cost_per_attempt) if completion and cost_per_attempt else None,
                metrics={
                    "completion_percent": completion,
                    "cost_per_attempt_usd": cost_per_attempt,
                },
            )
        )
    return parsed


_KNOWN_PROVIDERS = [
    "Anthropic",
    "OpenAI",
    "Google",
    "Google DeepMind",
    "DeepSeek",
    "Meta",
    "Mistral",
    "xAI",
    "Moonshot",
    "MiniMax",
    "Alibaba",
    "Qwen",
    "NVIDIA",
    "Cohere",
]


def _split_model_provider(value: str) -> tuple[str, str]:
    text = (value or "").strip()
    for provider in sorted(_KNOWN_PROVIDERS, key=len, reverse=True):
        suffix = f" {provider}"
        if text.endswith(suffix):
            return text[: -len(suffix)].strip(), provider
    return text, ""


def _fetch_onyx_rows() -> list[dict[str, Any]]:
    rows = _parse_table_rows(_http_get(ONYX_URL))
    if len(rows) < 2:
        return []
    headers = [cell["text"] for cell in rows[0]]
    data_rows = rows[1:]
    parsed = []
    total_rows = len(data_rows)
    benchmark_columns = {
        "MMLU-Pro",
        "GPQA Diamond",
        "IFEval",
        "Chatbot Arena",
        "SWE-bench Verified",
        "HumanEval",
        "LiveCodeBench",
    }
    for index, row in enumerate(data_rows, start=1):
        if len(row) != len(headers):
            continue
        row_map = {headers[i]: row[i] for i in range(len(headers))}
        model_name, provider = _split_model_provider(row_map["Model"]["text"])
        rank_score = 100.0 if total_rows <= 1 else round(100.0 * (1.0 - ((index - 1) / (total_rows - 1))), 1)
        input_price = _parse_money(row_map.get("Input $/1M", {}).get("text"))
        output_price = _parse_money(row_map.get("Output $/1M", {}).get("text"))
        benchmark_metrics = {
            key: _parse_float(row_map.get(key, {}).get("text"))
            for key in benchmark_columns
        }
        parsed.append(
            _source_metric(
                source_id="onyx",
                source_label="Onyx",
                source_url=ONYX_URL,
                detail_url=ONYX_URL,
                model_name=model_name,
                provider=provider,
                rank=index,
                performance_raw=rank_score,
                value_raw=(rank_score / (input_price + output_price)) if rank_score and input_price and output_price else None,
                metrics={
                    "params": row_map.get("Params", {}).get("text") or "",
                    "context_window": _parse_context_tokens(row_map.get("Context", {}).get("text")),
                    "context_window_label": row_map.get("Context", {}).get("text") or "",
                    "input_price_per_1m": input_price,
                    "output_price_per_1m": output_price,
                    **{k: v for k, v in benchmark_metrics.items() if v is not None},
                },
            )
        )
    return parsed


def _normalize_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    performance_values = [float(row["performance_raw"]) for row in rows if row.get("performance_raw") is not None]
    value_values = [float(row["value_raw"]) for row in rows if row.get("value_raw") is not None and math.isfinite(float(row["value_raw"]))]
    max_perf = max(performance_values) if performance_values else None
    max_value = max(value_values) if value_values else None
    for row in rows:
        performance_raw = row.get("performance_raw")
        value_raw = row.get("value_raw")
        row["performance_score"] = round((float(performance_raw) / max_perf) * 100.0, 1) if max_perf and performance_raw is not None else None
        row["value_score"] = round((float(value_raw) / max_value) * 100.0, 1) if max_value and value_raw is not None and math.isfinite(float(value_raw)) else None
    return rows


def _merge_rows(source_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for rows in source_rows.values():
        for row in rows:
            key = normalize_model_key(row.get("model_name"))
            if not key:
                continue
            merged = grouped.setdefault(
                key,
                {
                    "id": key,
                    "model_name": row.get("model_name") or key,
                    "provider": row.get("provider") or "",
                    "sources": [],
                },
            )
            if not merged.get("provider") and row.get("provider"):
                merged["provider"] = row["provider"]
            if len(str(row.get("model_name") or "")) > len(str(merged.get("model_name") or "")):
                merged["model_name"] = row.get("model_name") or merged["model_name"]
            merged["sources"].append(row)
    merged_rows = []
    for merged in grouped.values():
        source_items = merged["sources"]
        performance_scores = [float(item["performance_score"]) for item in source_items if item.get("performance_score") is not None]
        value_scores = [float(item["value_score"]) for item in source_items if item.get("value_score") is not None]
        metrics = {
            "context_window": max([v for v in (_best_metric(source_items, "context_window"),) if isinstance(v, int)], default=None),
            "context_window_label": _best_metric(source_items, "context_window_label"),
            "blended_price_per_1m": _best_metric(source_items, "blended_price_per_1m"),
            "input_price_per_1m": _best_metric(source_items, "input_price_per_1m"),
            "output_price_per_1m": _best_metric(source_items, "output_price_per_1m"),
            "output_speed_tps": _best_metric(source_items, "output_speed_tps"),
            "latency_first_chunk_s": _best_metric(source_items, "latency_first_chunk_s"),
            "end_to_end_response_s": _best_metric(source_items, "end_to_end_response_s"),
            "cost_per_attempt_usd": _best_metric(source_items, "cost_per_attempt_usd"),
            "completion_percent": _best_metric(source_items, "completion_percent"),
            "intelligence_index": _best_metric(source_items, "intelligence_index"),
        }
        last_dates = [
            str(item.get("metrics", {}).get("latest_date") or "")
            for item in source_items
            if item.get("metrics", {}).get("latest_date")
        ]
        merged_rows.append(
            {
                "id": merged["id"],
                "label": merged["model_name"],
                "model_name": merged["model_name"],
                "provider": merged.get("provider") or "",
                "performance_score": round(sum(performance_scores) / len(performance_scores), 1) if performance_scores else None,
                "value_score": round(sum(value_scores) / len(value_scores), 1) if value_scores else None,
                "source_count": len(source_items),
                "last_benchmark_date": max(last_dates) if last_dates else "",
                "metrics": {k: v for k, v in metrics.items() if v not in (None, "", [], {})},
                "sources": [
                    {
                        "id": item["source_id"],
                        "label": item["source_label"],
                        "url": item["source_url"],
                        "detail_url": item.get("detail_url") or item["source_url"],
                        "rank": item.get("rank"),
                        "performance_score": item.get("performance_score"),
                        "value_score": item.get("value_score"),
                        "metrics": item.get("metrics") or {},
                        "updated_at": item.get("updated_at") or "",
                    }
                    for item in source_items
                ],
            }
        )
    merged_rows.sort(
        key=lambda row: (
            float(row.get("performance_score") or 0.0),
            float(row.get("value_score") or 0.0),
            int(row.get("source_count") or 0),
        ),
        reverse=True,
    )
    return merged_rows


def _fetch_all_sources() -> dict[str, Any]:
    source_results: dict[str, list[dict[str, Any]]] = {}
    source_status: dict[str, dict[str, Any]] = {}
    sources = {
        "terminal_bench": ("Terminal-Bench 2.1", TERMINAL_BENCH_URL, _fetch_terminal_bench_rows),
        "artificial_analysis": ("Artificial Analysis", ARTIFICIAL_ANALYSIS_URL, _fetch_artificial_analysis_rows),
        "kilo": ("Kilo", KILO_URL, _fetch_kilo_rows),
        "onyx": ("Onyx", ONYX_URL, _fetch_onyx_rows),
    }
    for source_id, (label, url, fetcher) in sources.items():
        try:
            rows = _normalize_scores(fetcher())
            source_results[source_id] = rows
            source_status[source_id] = {"label": label, "url": url, "row_count": len(rows), "status": "ok"}
        except Exception as exc:
            logger.warning("Failed to fetch benchmark source %s: %s", source_id, exc)
            source_results[source_id] = []
            source_status[source_id] = {"label": label, "url": url, "row_count": 0, "status": "error", "error": str(exc)}
    source_status["openwebui"] = {"label": "Open WebUI", "url": OPENWEBUI_URL, "row_count": 0, "status": "reference_only"}
    source_status["open_llm_leaderboard"] = {"label": "Open LLM Leaderboard", "url": OPEN_LLM_LEADERBOARD_URL, "row_count": 0, "status": "reference_only"}
    return {
        "version": 2,
        "timestamp": time.time(),
        "last_updated": _format_source_timestamp(),
        "leaderboard": _merge_rows(source_results),
        "sources": source_status,
    }


def _read_benchmark_cache() -> dict[str, Any] | None:
    try:
        with open(BENCHMARK_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Failed to read benchmark cache: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("leaderboard"), list):
        return None
    return payload


def _write_benchmark_cache(payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(BENCHMARK_CACHE_PATH), exist_ok=True)
    temp_path = f"{BENCHMARK_CACHE_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temp_path, BENCHMARK_CACHE_PATH)


def is_benchmark_cache_stale(payload: dict[str, Any] | None = None) -> bool:
    cache = payload if isinstance(payload, dict) else _read_benchmark_cache()
    if not cache:
        return True
    timestamp = cache.get("timestamp")
    if not isinstance(timestamp, (int, float)):
        return True
    return (time.time() - float(timestamp)) >= BENCHMARK_CACHE_TTL_SECONDS


def refresh_benchmark_cache() -> None:
    global _refresh_running
    with _refresh_lock:
        if _refresh_running:
            return
        _refresh_running = True
    try:
        payload = _fetch_all_sources()
        _write_benchmark_cache(payload)
    finally:
        with _refresh_lock:
            _refresh_running = False


def trigger_benchmark_refresh(force: bool = False) -> bool:
    cache = _read_benchmark_cache()
    if not force and cache and not is_benchmark_cache_stale(cache):
        return False
    with _refresh_lock:
        global _refresh_running
        if _refresh_running:
            return False
        _refresh_running = True

    def _run() -> None:
        global _refresh_running
        try:
            payload = _fetch_all_sources()
            _write_benchmark_cache(payload)
        except Exception as exc:
            logger.warning("Benchmark refresh failed: %s", exc)
        finally:
            with _refresh_lock:
                _refresh_running = False

    threading.Thread(target=_run, daemon=True, name="llm-benchmark-refresh").start()
    return True


def load_benchmark_index(force_refresh: bool = False, allow_sync_fill: bool = True) -> dict[str, Any]:
    if force_refresh:
        refresh_benchmark_cache()
    cache = _read_benchmark_cache()
    if cache:
        return cache
    if not allow_sync_fill:
        return {
            "version": 2,
            "timestamp": 0,
            "last_updated": "",
            "leaderboard": [],
            "sources": {},
        }
    payload = _fetch_all_sources()
    try:
        _write_benchmark_cache(payload)
    except Exception as exc:
        logger.debug("Failed to write benchmark cache: %s", exc)
    return payload


def load_terminal_bench_leaderboard(force_refresh: bool = False) -> list[dict[str, Any]]:
    data = load_benchmark_index(force_refresh=force_refresh)
    return [row for row in (data.get("leaderboard") or []) if any(source.get("id") == "terminal_bench" for source in row.get("sources") or [])]


def _infer_use_case(llm_type: str, model_name: str, provider: str) -> str:
    llm_type = (llm_type or "").strip().lower()
    label = f"{model_name} {provider}".lower()
    if llm_type in {"coding", "workflow", "computer_use"}:
        if "codex" in label or "coder" in label or "code" in label:
            return "Best for coding agents, implementation, and tool-driven workflows."
        if "opus" in label or "sonnet" in label:
            return "Best for longer planning-heavy coding work with tools."
        if "flash" in label or "mini" in label or "fast" in label:
            return "Best for lower-cost workflow runs and fast coding iterations."
        return "Best for coding, workflow automation, and terminal-style agent tasks."
    if llm_type == "video":
        return "Best for video generation and prompt iteration."
    if llm_type == "image":
        return "Best for image generation and visual prompt refinement."
    if llm_type == "vision":
        return "Best for multimodal reasoning and image-aware review."
    return "Best for general conversation, tool use, and agent workflows."


def _value_score(row: dict[str, Any]) -> float | None:
    raw = row.get("value_score")
    return round(float(raw), 1) if raw is not None else None


def _profile_from_row(row: dict[str, Any], *, llm_type: str, requested_provider: str = "") -> dict[str, Any]:
    model_name = str(row.get("model_name") or row.get("label") or row.get("id") or "").strip()
    provider = str(row.get("provider") or requested_provider or "").strip().lower()
    metrics = row.get("metrics") or {}
    return {
        "id": str(row.get("id") or normalize_model_key(model_name)),
        "label": model_name,
        "provider": provider,
        "organization": "",
        "performance_score": round(float(row.get("performance_score")), 1) if row.get("performance_score") is not None else None,
        "value_score": _value_score(row),
        "summary": _infer_use_case(llm_type, model_name, provider),
        "best_use_case": _infer_use_case(llm_type, model_name, provider),
        "latest_date": row.get("last_benchmark_date") or "",
        "submission_count": int(row.get("source_count") or 0),
        "verified_submission_count": 0,
        "agents": [],
        "benchmark": {
            "source": "multi-source",
            "sources": [source.get("id") for source in (row.get("sources") or [])],
        },
        "metrics": metrics,
        "sources": row.get("sources") or [],
    }


def _fallback_profile(model: str, provider: str, llm_type: str) -> dict[str, Any]:
    label = (model or "Selected model").strip()
    provider_value = (provider or "").strip().lower()
    return {
        "id": normalize_model_key(label) or label.lower(),
        "label": label,
        "provider": provider_value,
        "organization": "",
        "performance_score": None,
        "value_score": None,
        "summary": _infer_use_case(llm_type, label, provider_value),
        "best_use_case": _infer_use_case(llm_type, label, provider_value),
        "latest_date": "",
        "submission_count": 0,
        "verified_submission_count": 0,
        "agents": [],
        "benchmark": {"source": "multi-source", "sources": []},
        "metrics": {},
        "sources": [],
    }


def find_benchmark_row(model: str, provider: str = "", force_refresh: bool = False, allow_sync_fill: bool = False) -> dict[str, Any] | None:
    data = load_benchmark_index(force_refresh=force_refresh, allow_sync_fill=allow_sync_fill)
    rows = data.get("leaderboard") or []
    target = normalize_model_key(model)
    provider_key = (provider or "").strip().lower()
    if not target:
        return None
    for row in rows:
        row_key = str(row.get("id") or "")
        row_provider = str(row.get("provider") or "").strip().lower()
        if row_key == target and (not provider_key or not row_provider or row_provider == provider_key):
            return row
    for row in rows:
        row_key = str(row.get("id") or "")
        if target and (target in row_key or row_key in target):
            return row
    return None


def build_llm_benchmark_payload(
    *,
    llm_type: str,
    provider: str,
    model: str,
    compare_model: str = "",
    sort: str = "performance",
    limit: int = 40,
) -> dict[str, Any]:
    cache = _read_benchmark_cache()
    cache_missing = not cache or not isinstance(cache.get("leaderboard"), list)
    stale = is_benchmark_cache_stale(cache)
    refresh_started = False
    if cache_missing or stale:
        refresh_started = trigger_benchmark_refresh(force=True)
    if cache and isinstance(cache.get("leaderboard"), list):
        data = cache
    elif cache_missing:
        data = {
            "leaderboard": [],
            "sources": {},
            "last_updated": "",
        }
    else:
        data = load_benchmark_index(allow_sync_fill=False)
    rows = data.get("leaderboard") or []
    selected_row = find_benchmark_row(model, provider, allow_sync_fill=False) if model else None
    selected = _profile_from_row(selected_row, llm_type=llm_type, requested_provider=provider) if selected_row else _fallback_profile(model, provider, llm_type)
    comparison_row = find_benchmark_row(compare_model, "", allow_sync_fill=False) if compare_model else (rows[1] if len(rows) > 1 else None)
    comparison = _profile_from_row(comparison_row, llm_type=llm_type) if comparison_row else _fallback_profile(compare_model or "Comparison model", "", llm_type)
    sort_key = "value_score" if str(sort or "").lower() == "value" else "performance_score"
    leaderboard = [_profile_from_row(row, llm_type=llm_type) for row in rows]
    leaderboard.sort(
        key=lambda row: (
            float(row.get(sort_key) or 0.0),
            float(row.get("performance_score") or 0.0),
            int(row.get("submission_count") or 0),
        ),
        reverse=True,
    )
    return {
        "type": llm_type,
        "sort": "value" if sort_key == "value_score" else "performance",
        "selected_model": selected,
        "comparison_model": comparison,
        "leaderboard": leaderboard[: max(int(limit or 40), 1)],
        "source": {
            "id": "multi_source",
            "label": "Multi-source benchmark cache",
            "url": TERMINAL_BENCH_URL,
        },
        "sources": data.get("sources") or {},
        "last_updated": data.get("last_updated"),
        "refreshing": bool(refresh_started or _refresh_running),
        "cache_missing": bool(cache_missing),
        "cache_stale": bool(stale),
    }
