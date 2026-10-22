"""Focused tests for complexity_route_to_llm / resolve_complexity_llm_candidates."""
from distr.core.llm_factory import complexity_route_to_llm, resolve_complexity_llm_candidates


def test_route_mapping_rules():
    assert complexity_route_to_llm("pi", "openrouter", "") is None
    assert complexity_route_to_llm("pi", "openrouter", "auto") is None
    assert complexity_route_to_llm("pi", "openrouter", "minimax/minimax-m3.1")[1] == "minimax/minimax-m3.1"
    assert complexity_route_to_llm("pi", "openrouter", "minimax/minimax-m3.1")[0].lower() == "openrouter"
    assert complexity_route_to_llm("pi", "", "minimax/minimax-m2.5") == ("OpenRouter", "minimax/minimax-m2.5")
    assert complexity_route_to_llm("pi", "", "qwen3:8b")[0] == "Ollama"
    assert complexity_route_to_llm("cursor", "", "gpt-oss:cloud")[0] == "Ollama"
    assert complexity_route_to_llm("cursor", "", "sonnet-4.5") is None
    assert complexity_route_to_llm("codex", None, "gpt-5-codex") is None


def test_candidates_primary_then_fallback_dedup_and_level_default():
    s = {
        "project_cli_high_backend": "pi", "project_cli_high_model_provider": "openrouter",
        "project_cli_high_model": "minimax/minimax-m3.1",
        "project_cli_high_fallback_backend": "pi", "project_cli_high_fallback_model_provider": "openrouter",
        "project_cli_high_fallback_model": "minimax/minimax-m2.5",
        "project_cli_medium_backend": "pi", "project_cli_medium_model": "minimax/minimax-m2.5",
        "project_cli_medium_fallback_backend": "pi", "project_cli_medium_fallback_model": "minimax/minimax-m2.5",
    }
    high = resolve_complexity_llm_candidates(s, "HIGH")
    assert [m for _, m in high] == ["minimax/minimax-m3.1", "minimax/minimax-m2.5"]
    assert resolve_complexity_llm_candidates(s, "bogus") == resolve_complexity_llm_candidates(s, "medium")
    assert len(resolve_complexity_llm_candidates(s, "medium")) == 1
    assert resolve_complexity_llm_candidates({}, "low") == []


def test_no_free_minimax_in_tiers_defaults():
    import distr.core.llm_factory as lf
    src = open(lf.__file__, encoding="utf-8").read()
    assert "minimax-m2.5:free" not in src.lower()
