"""Regression coverage for Ollama provider/model mismatch handling."""

from distr.core.agent.services.llm.providers.ollama import OllamaLLMService


class _ChatManager:
    current_model = "gpt-5.5"

    def get_current_chat(self):
        return None


def test_ollama_resolver_preserves_cloud_model_for_mismatch_hot_swap(monkeypatch):
    """Cloud model IDs must not be replaced before provider mismatch detection runs."""
    service = OllamaLLMService.__new__(OllamaLLMService)
    service._model_name = "deepseek-v4-pro:cloud"
    service.chat_manager = _ChatManager()

    def fail_if_ollama_validation_runs():
        raise AssertionError("Ollama model validation should not run for OpenAI model IDs")

    monkeypatch.setitem(__import__("sys").modules, "ollama", fail_if_ollama_validation_runs)

    assert service._resolve_current_model() == "gpt-5.5"
