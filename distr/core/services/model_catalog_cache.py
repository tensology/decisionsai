from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable

from distr.core.paths import MODELS_DIR


logger = logging.getLogger(__name__)

MODEL_CATALOG_CACHE_DIR = os.path.join(MODELS_DIR, "provider_model_catalog_cache")
MODEL_CATALOG_CACHE_TTL_SECONDS = 24 * 60 * 60
MODEL_CATALOG_CACHE_VERSION = 1


def _ensure_cache_dir() -> None:
    os.makedirs(MODEL_CATALOG_CACHE_DIR, exist_ok=True)


def normalize_auth_fingerprint(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "default"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_file_path(provider: str, auth_fingerprint: str | None) -> str:
    provider_key = str(provider or "").strip().lower() or "unknown"
    fingerprint = normalize_auth_fingerprint(auth_fingerprint)
    return os.path.join(MODEL_CATALOG_CACHE_DIR, f"{provider_key}_{fingerprint}.json")


def _read_cache(path: str) -> list[Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Failed reading model catalog cache %s: %s", path, exc)
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("version") != MODEL_CATALOG_CACHE_VERSION:
        return None
    timestamp = payload.get("timestamp")
    models = payload.get("models")
    if not isinstance(timestamp, (int, float)) or not isinstance(models, list):
        return None
    if time.time() - float(timestamp) >= MODEL_CATALOG_CACHE_TTL_SECONDS:
        return None
    return models


def _write_cache(path: str, provider: str, auth_fingerprint: str | None, models: list[Any]) -> None:
    _ensure_cache_dir()
    payload = {
        "version": MODEL_CATALOG_CACHE_VERSION,
        "provider": str(provider or "").strip().lower(),
        "auth_fingerprint": normalize_auth_fingerprint(auth_fingerprint),
        "timestamp": time.time(),
        "models": models,
    }
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temp_path, path)


def get_or_fetch_model_catalog(
    provider: str,
    *,
    fetcher: Callable[[], list[Any]],
    auth_fingerprint: str | None = None,
    force_refresh: bool = False,
) -> list[Any]:
    path = _cache_file_path(provider, auth_fingerprint)
    if not force_refresh:
        cached = _read_cache(path)
        if cached is not None:
            return cached

    models = list(fetcher() or [])
    try:
        _write_cache(path, provider, auth_fingerprint, models)
    except Exception as exc:
        logger.debug("Failed writing model catalog cache %s: %s", path, exc)
    return models


def flush_model_catalog_cache(provider: str | None = None) -> int:
    _ensure_cache_dir()
    provider_key = str(provider or "").strip().lower()
    removed = 0
    for name in os.listdir(MODEL_CATALOG_CACHE_DIR):
        if not name.endswith(".json"):
            continue
        if provider_key and not name.startswith(f"{provider_key}_"):
            continue
        try:
            os.remove(os.path.join(MODEL_CATALOG_CACHE_DIR, name))
            removed += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.debug("Failed removing model catalog cache %s: %s", name, exc)
    return removed
