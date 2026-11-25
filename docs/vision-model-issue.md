# Vision model: why changing it in Settings doesn't fix it

Status: diagnosed, not yet fixed.
Scope: how the app resolves and calls the vision model at runtime.

## What you're seeing
You change the vision model in Settings → LLMs, save, and the agent still fails
("it just fucks up"). Changing the model doesn't help.

## What is actually stored
`db/settings.db` → `settings` row:

| field | value |
|---|---|
| `vision_llm_provider` | `openai` |
| `vision_llm_model` | `gpt-5.6-sol` |

The save path is **correct**. `distr/gui/web/routes/settings/llms.py` writes
`vision_llm_provider` / `vision_llm_model` from the form, and the form posts
`vision_provider` / `vision_model` (`static/settings/js/llms.js`). So the value
you save *is* what is stored. The problem is downstream of the save.

## Root cause: the model name is sent verbatim, with no provider/name split
`distr/core/agent/tools/vision/vision_api.py::call_openai_vision` always uses the
**OpenAI SDK with `openai_key`**, and passes the configured model name straight
through as the API `model`:

```python
create_kwargs = {
    "model": vision_model,                       # e.g. "openai/gpt-5.6-sol"
    "messages": vision_messages,
    openai_token_limit_param(vision_model): 2000,
    "timeout": 60.0,
}
```

The token-limit helper *knows* the name may carry a provider prefix — it strips
it to decide `max_tokens` vs `max_completion_tokens`:

```python
def openai_token_limit_param(model: str) -> str:
    name = (model or "").strip().lower()
    if "/" in name:
        name = name.split("/", 1)[1]      # "openai/gpt-5.6-sol" -> "gpt-5.6-sol"
```

…but the same prefixed name is then handed to the API. So:
- model saved **with** a prefix (`openai/gpt-5.6-sol`) → sent as
  `openai/gpt-5.6-sol` → OpenAI returns *model not found*;
- model saved **without** a prefix (`gpt-5.6-sol`) → sent as `gpt-5.6-sol`, which
  only works if that exact name exists on the OpenAI account you're billing.

Either way the call fails, and **changing the model in the dropdown does not
help** if the dropdown offers prefixed names (or names that aren't valid for the
selected provider).

## There are three separate vision paths, and they don't agree
| Path | Entry point | Honors the setting? | Provider handling |
|---|---|---|---|
| Screenshot analysis | `vision/screenshot_analyzer.py::_call_vision_api` → `call_openai_vision` | yes | OpenAI SDK only; **model name not split** |
| Chat image attach | `services/llm/image_utils.py::analyze_image_with_vision_llm` | yes (gated by `check_vision_model_support`) | branches per provider, base_url set correctly, **model name not split** |
| Playwright tool | `integrations/playwright_tool.py` | yes | reads `vision_llm_provider`/`vision_llm_model` |

All three read the same setting, so the setting itself isn't the problem. The
common defect is that **none of them strips the provider prefix off the model
name before the API call**, even though other code in the same file assumes the
prefix can be present.

## Secondary issues found
1. **`call_openai_vision` ignores the configured provider.** It is only reached
   for `provider == "openai"`, but it hardcodes `from openai import OpenAI` and
   `settings['openai_key']`. If a non-OpenAI provider is ever routed here it will
   use the wrong key and the wrong endpoint.
2. ~~**Hardcoded fallback model.**~~ **Fixed.** It used to do
   `if not vision_model: vision_model = "gpt-5.4-mini"`, silently calling a model
   you did not choose. It now raises
   `ValueError("No vision model configured. Set the Vision provider and model in
   Settings → LLMs.")`, and the check runs **before** the API-key check so the
   clearer misconfiguration is reported first. The same silent fallback in
   `playwright_tool.py` (`model or "gpt-5.4-mini"`) now logs an error and skips
   analysis instead of substituting.
3. **`check_vision_model_support` is a name regex**, not a real capability check:
   `(gpt-4|gpt-5|claude-3|claude-4|gemini|gemma-3|llama-4|qwen.*vl|vision|pixtral|llava|moondream)`.
   A model named `gpt-5.6-sol` passes on the `gpt-5` substring even if the
   provider can't serve it, so the chat path happily calls and fails.

## The fix (applied)
A single `split_provider_model(provider, model)` helper in `vision_api.py`, used
by `resolve_vision_llm_config` (both copies — `vision_analyzer` now delegates),
`resolve_vision_model`, `call_openai_vision`, and `playwright_tool.py`, so the
bare name always reaches the client. The prefix is stripped **only when it
equals the configured provider**, so multi-vendor names are untouched:

```python
def split_provider_model(provider: str, model: str) -> tuple[str, str]:
    provider = (provider or "").strip()
    model = (model or "").strip()
    if provider and "/" in model:
        head, _, tail = model.partition("/")
        if tail.strip() and head.strip().lower() == provider.lower():
            return provider, tail.strip()
    return provider, model
```

Silent model substitution was removed: an unconfigured vision model now raises a
clear `ValueError` instead of quietly calling `gpt-5.4-mini`.

Tests: `tests/core/agent/test_vision_provider_split.py`.

## How to confirm before/after
1. Save a **prefixed** vision model (e.g. `openai/gpt-5.6-sol`) and run a
   screenshot analysis. Expect the current code to fail with a model-not-found
   error; the fix should make it work.
2. Save the same model **unprefixed** and repeat — both should now behave
   identically.
3. Check the log line `ScreenshotAnalyzer: Calling vision API with N image(s),
   model: <name>` and confirm `<name>` has no provider prefix.

## Open question for the product owner
Should the vision model field accept `provider/model`, or should it be a bare
model name with the provider chosen separately (as it is now)? The UI posts them
separately (`vision_provider` + `vision_model`), so the cleanest answer is: the
model field is a **bare name**, and any prefix is stripped defensively.
