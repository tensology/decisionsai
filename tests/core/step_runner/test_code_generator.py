"""Unit tests for distr.core.step_runner.code_generator."""

from unittest.mock import patch, MagicMock

import pytest

from distr.core.step_runner.code_generator import (
    CodeGeneratorService,
    _strip_code_fences,
    _litellm_model,
)
from distr.core.step_runner.step_types import StepType


# ---------------------------------------------------------------------------
# Helper: _strip_code_fences
# ---------------------------------------------------------------------------

class TestStripCodeFences:
    def test_removes_python_fences(self):
        text = "```python\nprint('hi')\n```"
        assert _strip_code_fences(text) == "print('hi')"

    def test_removes_plain_fences(self):
        text = "```\ncode\n```"
        assert _strip_code_fences(text) == "code"

    def test_no_fences_unchanged(self):
        text = "print('hi')"
        assert _strip_code_fences(text) == "print('hi')"

    def test_strips_whitespace(self):
        text = "  \n```python\ncode\n```\n  "
        assert _strip_code_fences(text) == "code"


# ---------------------------------------------------------------------------
# Helper: _litellm_model
# ---------------------------------------------------------------------------

class TestLitellmModel:
    def test_ollama_with_model(self):
        assert _litellm_model("ollama", "codellama", {}) == "ollama/codellama"

    def test_ollama_without_model(self):
        assert _litellm_model("ollama", "", {}) == "ollama/llama3.2"

    def test_openai(self):
        assert _litellm_model("openai", "gpt-4", {}) == "gpt-4"

    def test_openai_default(self):
        assert _litellm_model("openai", "", {}) == "gpt-4o-mini"

    def test_anthropic(self):
        assert _litellm_model("anthropic", "claude-3-opus", {}) == "claude-3-opus"

    def test_unknown_provider_with_model(self):
        assert _litellm_model("unknown", "mymodel", {}) == "ollama/mymodel"

    def test_unknown_provider_without_model(self):
        assert _litellm_model("unknown", "", {}) == "ollama/llama3.2"


# ---------------------------------------------------------------------------
# CodeGeneratorService._get_coding_llm
# ---------------------------------------------------------------------------

SETTINGS_PATCH = "distr.core.settings.load_settings_from_db"


class TestGetCodingLlm:
    def _make_service(self):
        return CodeGeneratorService()

    @patch(SETTINGS_PATCH)
    def test_uses_coding_llm_settings(self, mock_load):
        mock_load.return_value = {
            "coding_llm_provider": "openai",
            "coding_llm_model": "gpt-4",
        }
        svc = self._make_service()
        provider, model, _ = svc._get_coding_llm()
        assert provider == "openai"
        assert model == "gpt-4"

    @patch(SETTINGS_PATCH)
    def test_falls_back_to_agent_settings(self, mock_load):
        mock_load.return_value = {
            "coding_llm_provider": "",
            "coding_llm_model": "",
            "agent_provider": "Anthropic",
            "agent_model": "claude-3-opus",
        }
        svc = self._make_service()
        provider, model, _ = svc._get_coding_llm()
        assert provider == "anthropic"
        assert model == "claude-3-opus"

    @patch(SETTINGS_PATCH)
    def test_defaults_to_ollama(self, mock_load):
        mock_load.return_value = {}
        svc = self._make_service()
        provider, model, _ = svc._get_coding_llm()
        assert provider == "ollama"
        assert model == "llama3.2"


# ---------------------------------------------------------------------------
# CodeGeneratorService.generate_code
# ---------------------------------------------------------------------------

class TestGenerateCode:
    def _mock_litellm_response(self, content):
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_generate_execute_code(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response("print('hello')")

        svc = CodeGeneratorService()
        result = svc.generate_code("print hello", StepType.EXECUTE_CODE)

        assert result == "print('hello')"
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args
        assert "print hello" in call_kwargs.kwargs["messages"][0]["content"]

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_generate_playwright_code(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response(
            "from playwright.sync_api import sync_playwright"
        )

        svc = CodeGeneratorService()
        result = svc.generate_code("open google", StepType.PLAYWRIGHT)

        assert "playwright" in result
        call_kwargs = mock_completion.call_args
        prompt = call_kwargs.kwargs["messages"][0]["content"]
        assert "Playwright" in prompt
        assert "sync" in prompt.lower()

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_generate_code_with_context(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response("code")

        svc = CodeGeneratorService()
        svc.generate_code("do stuff", StepType.EXECUTE_CODE, context="some context")

        prompt = mock_completion.call_args.kwargs["messages"][0]["content"]
        assert "some context" in prompt

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_strips_markdown_fences_from_response(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response(
            "```python\nprint('hi')\n```"
        )

        svc = CodeGeneratorService()
        result = svc.generate_code("print hi", StepType.EXECUTE_CODE)
        assert result == "print('hi')"

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_empty_response_raises(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response("")

        svc = CodeGeneratorService()
        with pytest.raises(RuntimeError, match="empty response"):
            svc.generate_code("do something", StepType.EXECUTE_CODE)

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion", side_effect=ConnectionError("timeout"))
    def test_llm_unreachable_raises_descriptive_error(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}

        svc = CodeGeneratorService()
        with pytest.raises(RuntimeError, match="Failed to reach the coding LLM"):
            svc.generate_code("do something", StepType.EXECUTE_CODE)


# ---------------------------------------------------------------------------
# CodeGeneratorService.fix_code
# ---------------------------------------------------------------------------

class TestFixCode:
    def _mock_litellm_response(self, content):
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion")
    def test_fix_code_returns_corrected_code(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}
        mock_completion.return_value = self._mock_litellm_response("print('fixed')")

        svc = CodeGeneratorService()
        result = svc.fix_code(
            code="print('broken'",
            error="SyntaxError: unexpected EOF",
            instruction="print something",
            step_type=StepType.EXECUTE_CODE,
        )

        assert result == "print('fixed')"
        prompt = mock_completion.call_args.kwargs["messages"][0]["content"]
        assert "SyntaxError" in prompt
        assert "print('broken'" in prompt

    @patch(SETTINGS_PATCH)
    @patch("litellm.completion", side_effect=Exception("network error"))
    def test_fix_code_llm_error_raises(self, mock_completion, mock_load):
        mock_load.return_value = {"coding_llm_provider": "openai", "coding_llm_model": "gpt-4"}

        svc = CodeGeneratorService()
        with pytest.raises(RuntimeError, match="Failed to reach the coding LLM"):
            svc.fix_code("code", "error", "instruction", StepType.EXECUTE_CODE)
