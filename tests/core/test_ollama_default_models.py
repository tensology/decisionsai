import inspect

from distr.core.agent.constants import DEFAULT_MODELS, DEFAULT_OLLAMA_MODELS_BY_TYPE
from distr.core.services.model_recommendations import refresh_recommendations
from distr.core.system_resources import recommend_ollama_defaults


def test_ollama_defaults_use_ornith_9b_for_chat_and_coding():
    defaults = recommend_ollama_defaults(64)

    assert defaults["conversational"] == "ornith:9b"
    assert defaults["coding"] == "ornith:9b"
    assert DEFAULT_MODELS["ollama"] == "ornith:9b"
    assert DEFAULT_OLLAMA_MODELS_BY_TYPE["conversational"] == "ornith:9b"
    assert DEFAULT_OLLAMA_MODELS_BY_TYPE["coding"] == "ornith:9b"


def test_model_recommendation_refresh_uses_chat_capable_ornith_default():
    default_model = inspect.signature(refresh_recommendations).parameters["model"].default

    assert default_model == "ornith:9b"
