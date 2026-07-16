"""
Model Recommendations Service.

Manages a cached JSON file of per-provider model recommendations.
A background agent (using a local Ollama model + web search) refreshes
the file every 14 days so users always see current guidance.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from distr.core.paths import MODELS_DIR

logger = logging.getLogger(__name__)

BUNDLED_RECOMMENDATIONS_FILE = Path(__file__).parent.parent / "data" / "model_recommendations.json"
RECOMMENDATIONS_FILE = Path(MODELS_DIR) / "model_recommendations.json"
STALE_DAYS = 14
_refresh_running = False

PROVIDERS = [
    ("ollama", "Ollama"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("groq", "Groq"),
    ("openrouter", "OpenRouter"),
    ("kilocode", "KiloCode"),
    ("gemini", "Google Gemini"),
    ("nvidia", "NVIDIA"),
]

_EMPTY = {"providers": {}, "last_updated": None, "generated_by": None}


def _recommendations_read_path() -> Path | None:
    """Prefer the writable per-user cache, then the read-only bundled seed."""
    if RECOMMENDATIONS_FILE.exists():
        return RECOMMENDATIONS_FILE
    if BUNDLED_RECOMMENDATIONS_FILE.exists():
        return BUNDLED_RECOMMENDATIONS_FILE
    return None


def is_stale() -> bool:
    """Return True if the recommendations file is missing or older than STALE_DAYS."""
    source = _recommendations_read_path()
    if source is None:
        return True
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        last_str = data.get("last_updated")
        if not last_str:
            return True
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last
        return age.days >= STALE_DAYS
    except Exception:
        return True


def load_recommendations(provider: Optional[str] = None) -> dict:
    """Read the user cache or bundled seed, optionally filtered by provider."""
    source = _recommendations_read_path()
    if source is None:
        return dict(_EMPTY)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return dict(_EMPTY)

    if provider:
        providers = data.get("providers", {})
        filtered = {provider: providers[provider]} if provider in providers else {}
        return {
            "last_updated": data.get("last_updated"),
            "generated_by": data.get("generated_by"),
            "providers": filtered,
        }
    return data


def _write_recommendations(data: dict) -> None:
    """Atomically write the recommendations JSON (temp file + rename)."""
    RECOMMENDATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(RECOMMENDATIONS_FILE.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(RECOMMENDATIONS_FILE))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def refresh_recommendations(model: str = "ornith:9b") -> None:
    """Run the recommendation agent. Safe to call from a background thread."""
    global _refresh_running
    if _refresh_running:
        logger.info("Model recommendations refresh already running, skipping")
        return
    _refresh_running = True
    try:
        _do_refresh(model)
    finally:
        _refresh_running = False


def _do_refresh(model: str) -> None:
    """Core refresh logic: web search + LLM for each provider, write JSON."""
    logger.info("Starting model recommendations refresh (model=%s)", model)
    deadline = time.time() + 300  # 5-minute timeout

    results = {}
    for provider_id, provider_name in PROVIDERS:
        if time.time() > deadline:
            logger.warning("Recommendation refresh timeout — writing partial results")
            break
        try:
            provider_data = _research_provider(provider_id, provider_name, model)
            if provider_data:
                results[provider_id] = provider_data
                logger.info("Recommendations generated for %s", provider_name)
        except Exception as e:
            logger.error("Failed to generate recommendations for %s: %s", provider_name, e)

    if not results:
        logger.warning("No recommendations generated — keeping existing file")
        return

    # Merge with existing data so we don't lose providers that timed out
    existing = load_recommendations()
    existing_providers = existing.get("providers", {})
    existing_providers.update(results)

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "generated_by": model,
        "providers": existing_providers,
    }
    _write_recommendations(output)
    logger.info("Model recommendations file updated (%d providers)", len(existing_providers))


def _research_provider(provider_id: str, provider_name: str, model: str) -> Optional[dict]:
    """Research and generate recommendations for a single provider."""
    # Step 1: Web search for current model info
    search_snippets, source_urls = _web_search(provider_name)

    # Step 2: Ask Ollama to produce structured recommendations
    prompt = _build_prompt(provider_name, search_snippets)
    raw = _call_ollama(model, prompt)
    if not raw:
        return None

    # Step 3: Parse and validate, then attach source URLs from search
    result = _parse_provider_response(raw, provider_name)
    if result and source_urls:
        _attach_search_sources(result, source_urls)
    return result


def _web_search(provider_name: str) -> tuple:
    """Search the web for current model information.

    Returns (snippets_text, source_urls) where source_urls is a list of
    {"title": ..., "url": ...} dicts harvested from search results.
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("ddgs not installed — skipping web search for %s", provider_name)
        return "", []

    queries = [
        f"{provider_name} best LLM models 2025 2026 pricing comparison",
        f"{provider_name} AI model recommendations coding tool calling",
        f"{provider_name} LLM model benchmark quality review",
    ]
    snippets = []
    source_urls = []
    try:
        with DDGS() as ddgs:
            for q in queries:
                results = list(ddgs.text(q, max_results=5))
                for r in results:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    href = r.get("href", "")
                    snippets.append(f"{title}: {body}")
                    if href and title:
                        source_urls.append({"title": title, "url": href})
    except Exception as e:
        logger.warning("Web search failed for %s: %s", provider_name, e)

    return "\n\n".join(snippets[:15]), source_urls[:12]


def _build_prompt(provider_name: str, search_context: str) -> str:
    """Build the LLM prompt for generating recommendations.

    The schema uses two lanes per category: ``paid`` and ``free``.
    ``per_prompt_est`` is a human-readable cost string based on ~1 000
    input tokens + ~1 000 output tokens.
    """
    return f"""/no_think
You are an AI model expert and pricing analyst. Based on the following web search results about {provider_name}'s current model offerings, provide detailed model recommendations.

SEARCH RESULTS:
{search_context if search_context else "(No search results available — use your training knowledge)"}

IMPORTANT GUIDELINES:
- For each category, pick the BEST paid model AND the BEST free model.
- "paid" means the model costs money to use via API (cloud-hosted, requires an API key with billing).
- "free" means $0 cost to the user — either:
  • A model that runs locally via Ollama (no API key, no billing, runs on user hardware)
  • A provider's genuinely free tier (e.g. Groq free tier, Google AI Studio free tier)
  • Do NOT list a model as "free" if it requires a paid API key or has usage-based billing.
- For Ollama specifically: ALL models are free/local. Set "paid" to null for every category. Only populate the "free" lane.
- For cloud providers (OpenAI, Anthropic, etc.): most models are "paid". Only set "free" if the provider has a genuinely free tier with no billing required. Otherwise set "free" to null.
- If a provider has no model for a category at all, set both "paid" and "free" to null.
- Use ACTUAL model IDs that the {provider_name} API accepts.
- Pricing: "input" and "output" are per 1M tokens. "per_prompt_est" is a human-readable string estimating cost for ~1k input + ~1k output tokens (e.g. "~$0.003"). For free models use "$0.00". For image models use per-image cost.
- Quality scores are 1-10 integers: overall, speed, reasoning, cost_efficiency.
- "released" is YYYY-MM format.
- "context_window" is max token context length (integer). Use 0 for image-only models.
- "sources" should be 1-3 real URLs to official docs, pricing pages, or comparison articles.
- Categories: tool_calling (default for conversation + tool use), coding, vision, image_generation.

Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "display_name": "{provider_name}",
  "categories": {{
    "tool_calling": {{
      "paid": {{
        "model_id": "<id>",
        "model_name": "<display name>",
        "description": "<1-2 sentence why>",
        "released": "<YYYY-MM>",
        "context_window": <int>,
        "pricing": {{"input": <float>, "output": <float>, "per_prompt_est": "<~$X.XXX>"}},
        "quality": {{"overall": <1-10>, "speed": <1-10>, "reasoning": <1-10>, "cost_efficiency": <1-10>}},
        "sources": [{{"title": "<title>", "url": "<url>"}}]
      }},
      "free": {{
        "model_id": "<id>",
        "model_name": "<display name>",
        "description": "<1-2 sentence why this is the best free/local option>",
        "released": "<YYYY-MM>",
        "context_window": <int>,
        "pricing": {{"input": 0, "output": 0, "per_prompt_est": "$0.00"}},
        "quality": {{"overall": <1-10>, "speed": <1-10>, "reasoning": <1-10>, "cost_efficiency": <1-10>}},
        "sources": [{{"title": "<title>", "url": "<url>"}}]
      }}
    }},
    "coding": {{ "paid": {{...}} or null, "free": {{...}} or null }},
    "vision": {{ "paid": {{...}} or null, "free": {{...}} or null }},
    "image_generation": {{ "paid": {{...}} or null, "free": {{...}} or null }}
  }}
}}"""


def _call_ollama(model: str, prompt: str) -> Optional[str]:
    """Call a local Ollama model and return the raw text response."""
    try:
        import ollama
        client = ollama.Client()
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 6000},
        )
        content = response.get("message", {}).get("content", "")
        return content.strip() if content else None
    except ImportError:
        logger.error("ollama package not installed — cannot generate recommendations")
        return None
    except Exception as e:
        logger.error("Ollama call failed (model=%s): %s", model, e)
        return None


def _parse_provider_response(raw: str, provider_name: str) -> Optional[dict]:
    """Parse the LLM response into a validated provider dict (paid/free lanes)."""
    # Strip think tags (qwen3 wraps reasoning in <think>...</think>)
    text = raw.strip()
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.error("Could not parse JSON from LLM response for %s (first 500 chars): %s", provider_name, text[:500])
                return None
        else:
            logger.error("No JSON found in LLM response for %s (first 500 chars): %s", provider_name, text[:500])
            return None

    if "categories" not in data:
        logger.error("Missing 'categories' in response for %s", provider_name)
        return None

    categories = data["categories"]
    required_cats = ["tool_calling", "coding", "vision", "image_generation"]
    for cat in required_cats:
        if cat not in categories:
            logger.warning("Missing category '%s' for %s — inserting null lanes", cat, provider_name)
            categories[cat] = {"paid": None, "free": None}
            continue
        entry = categories[cat]
        # Validate each lane (paid / free)
        for lane in ("paid", "free"):
            lane_data = entry.get(lane)
            if lane_data is None:
                continue
            for field in ("model_id", "model_name", "description"):
                if not lane_data.get(field):
                    logger.warning("Missing field '%s' in %s/%s/%s", field, provider_name, cat, lane)

    return {
        "display_name": data.get("display_name", provider_name),
        "categories": categories,
    }


def _attach_search_sources(result: dict, source_urls: list) -> None:
    """Merge web-search source URLs into lane entries that lack sources."""
    categories = result.get("categories", {})
    for cat_key, entry in categories.items():
        for lane in ("paid", "free"):
            lane_data = entry.get(lane) if isinstance(entry, dict) else None
            if lane_data is None:
                continue
            existing = lane_data.get("sources") or []
            if len(existing) < 2:
                for src in source_urls:
                    if len(existing) >= 3:
                        break
                    if not any(s.get("url") == src["url"] for s in existing):
                        existing.append(src)
                lane_data["sources"] = existing
