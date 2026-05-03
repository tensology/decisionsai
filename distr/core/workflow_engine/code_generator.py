"""Code Generator Service for Execute Code and Playwright step types.

Uses the coding LLM from global settings (coding_llm_provider / coding_llm_model)
to convert natural language instructions into executable code, and to fix failing
code given error output.
"""

import logging
import re
from typing import Optional, Tuple

from distr.core.workflow_engine.step_types import StepType

logger = logging.getLogger(__name__)

try:
    import litellm as _litellm_client
except ImportError:
    _litellm_client = None  # type: ignore[misc, assignment]

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GENERATE_EXECUTE_CODE_PROMPT = """\
You are a code generator. Write a standalone Python script that accomplishes the following task.

Instruction:
{instruction}

{context_block}

Requirements:
- Output a complete, runnable Python script.
- Include error handling with try/except and meaningful exit codes (0 for success, non-zero for failure).
- Print results to stdout.
- Do NOT include markdown fences or explanations — output ONLY the Python code."""

_GENERATE_PLAYWRIGHT_PROMPT = """\
You are a code generator. Write a Python script using the Playwright **sync** API that accomplishes the following task.

Instruction:
{instruction}

{context_block}

Requirements:
- Use `from playwright.sync_api import sync_playwright`.
- Launch a Chromium browser (the caller controls headless mode; assume headless).
- Include error handling with try/except and meaningful exit codes (0 for success, non-zero for failure).
- Print results or status to stdout.
- Close the browser and playwright instance in a finally block.
- Do NOT include markdown fences or explanations — output ONLY the Python code."""

_FIX_CODE_PROMPT = """\
The following Python code failed when executed.

Original instruction:
{instruction}

Code:
```python
{code}
```

Error output:
```
{error}
```

Fix the code so it runs successfully. Return ONLY the corrected Python code without markdown fences or explanations."""


def _litellm_model(provider: str, model: str, settings: dict) -> str:
    """Map provider + model to litellm model string.

    Mirrors the helper in ``distr.core.workflow.service`` so the code
    generator can resolve the same provider/model pairs.
    """
    if provider == "ollama":
        return f"ollama/{model}" if model else "ollama/llama3.2"
    if provider == "openai":
        return model or "gpt-4o-mini"
    if provider == "anthropic":
        return model or "claude-3-5-sonnet-20241022"
    if provider == "groq":
        return model or "groq/llama-3.1-70b-versatile"
    if provider == "openrouter":
        return model or "openrouter/openai/gpt-4o-mini"
    if provider == "kilocode":
        return model or "kilocode/kilocode"
    if provider == "gemini":
        return model or "gemini/gemini-2.5-flash"
    return f"ollama/{model}" if model else "ollama/llama3.2"


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wraps its response."""
    text = text.strip()
    text = re.sub(r"^```[\w]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


class CodeGeneratorService:
    """Generate and fix code using the coding LLM from global settings."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_code(
        self,
        instruction: str,
        step_type: StepType,
        context: Optional[str] = None,
    ) -> str:
        """Generate code from a natural-language instruction.

        For ``EXECUTE_CODE`` steps the result is a standalone Python script.
        For ``PLAYWRIGHT`` steps the result uses ``playwright.sync_api``.

        Raises ``RuntimeError`` with a descriptive message when the LLM is
        unreachable or returns an error.
        """
        context_block = f"Additional context:\n{context}" if context else ""

        if step_type == StepType.PLAYWRIGHT:
            prompt = _GENERATE_PLAYWRIGHT_PROMPT.format(
                instruction=instruction, context_block=context_block
            )
        else:
            prompt = _GENERATE_EXECUTE_CODE_PROMPT.format(
                instruction=instruction, context_block=context_block
            )

        return self._call_coding_llm(prompt)

    def fix_code(
        self,
        code: str,
        error: str,
        instruction: str,
        step_type: StepType,
    ) -> str:
        """Ask the coding LLM to fix *code* given the *error* output.

        Returns the corrected code string.  Raises ``RuntimeError`` when the
        LLM is unreachable.
        """
        prompt = _FIX_CODE_PROMPT.format(
            instruction=instruction, code=code, error=error
        )
        return self._call_coding_llm(prompt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_coding_llm(self) -> Tuple[str, str, dict]:
        """Load coding LLM provider and model from global settings.

        Falls back to ``agent_provider`` / ``agent_model`` when the dedicated
        coding keys are not configured.

        If an LLM override context is active with non-empty coder settings,
        those take precedence over global settings.

        Returns ``(provider, model, settings)`` tuple.
        """
        from distr.core.settings import load_settings_from_db
        from distr.core.llm_override import get_llm_override

        settings = load_settings_from_db()

        # Check for board-level LLM override (coder role)
        override = get_llm_override()
        if override and override.coder_provider:
            provider = override.coder_provider.strip().lower()
            model = (override.coder_model or "").strip()
            if not model and provider == "ollama":
                model = "llama3.2"
            return provider, model, settings

        provider = (
            (settings.get("coding_llm_provider") or "").strip()
            or (settings.get("agent_provider") or "").strip()
            or "Ollama"
        ).strip().lower()

        model = (
            (settings.get("coding_llm_model") or "").strip()
            or (settings.get("agent_model") or "").strip()
            or ""
        )

        if not model and provider == "ollama":
            model = "llama3.2"

        return provider, model, settings

    def _call_coding_llm(self, prompt: str) -> str:
        """Send *prompt* to the coding LLM and return the generated code."""
        provider, model, settings = self._get_coding_llm()
        model_str = _litellm_model(provider, model, settings)

        messages = [{"role": "user", "content": prompt}]

        try:
            if _litellm_client is None:
                raise ImportError("litellm")
            response = _litellm_client.completion(
                model=model_str,
                messages=messages,
                max_tokens=4096,
                temperature=0.2,
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError(
                    "Coding LLM returned an empty response. "
                    "Please check your coding_llm_provider and coding_llm_model settings."
                )
            return _strip_code_fences(content.strip())
        except ImportError:
            raise RuntimeError(
                "litellm is not installed. Please install it with: pip install litellm"
            )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Coding LLM call failed: %s", exc, exc_info=True)
            raise RuntimeError(
                f"Failed to reach the coding LLM ({provider}/{model}): {exc}"
            ) from exc
