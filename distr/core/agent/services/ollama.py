"""Backward-compatible import path for the Ollama LLM service."""

from distr.core.agent.services.llm.providers.ollama import OllamaLLMService

__all__ = ["OllamaLLMService"]
