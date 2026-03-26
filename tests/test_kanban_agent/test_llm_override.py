# Feature: kanban-agent-workflow
"""
Property tests for LLM Override context mechanism.

Property 11: LLM Override creation includes all board settings
Property 12: LLM Override resolution uses override when non-empty
Property 13: LLM Override lifecycle scoping
"""
from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.llm_override import (
    LLMOverride,
    set_llm_override,
    get_llm_override,
    clear_llm_override,
)


# Strategy: non-empty provider/model strings (printable, no whitespace-only)
_provider_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
)
_model_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=80,
)


class TestLLMOverrideCreation:
    """Property 11: LLM Override creation includes all board settings.

    *For any* Kanban board with orchestrator, coder, and sub-agent provider/model
    settings, the created LLMOverride should contain all six values matching the
    board's configuration.

    **Validates: Requirements 4.1, 5.1, 6.1**
    """

    @given(
        orch_provider=_provider_st,
        orch_model=_model_st,
        coder_provider=_provider_st,
        coder_model=_model_st,
        sub_provider=_provider_st,
        sub_model=_model_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_override_stores_all_six_fields(
        self,
        orch_provider,
        orch_model,
        coder_provider,
        coder_model,
        sub_provider,
        sub_model,
    ):
        """Creating an LLMOverride with all six fields stores them correctly."""
        override = LLMOverride(
            orchestrator_provider=orch_provider,
            orchestrator_model=orch_model,
            coder_provider=coder_provider,
            coder_model=coder_model,
            sub_provider=sub_provider,
            sub_model=sub_model,
        )
        assert override.orchestrator_provider == orch_provider
        assert override.orchestrator_model == orch_model
        assert override.coder_provider == coder_provider
        assert override.coder_model == coder_model
        assert override.sub_provider == sub_provider
        assert override.sub_model == sub_model


class TestLLMOverrideResolution:
    """Property 12: LLM Override resolution uses override when non-empty.

    *For any* active LLMOverride with a non-empty provider/model for a given role
    (orchestrator, coder, sub), the LLM resolution for that role should return the
    override's provider and model instead of the global settings.

    **Validates: Requirements 4.2, 5.2, 6.2**
    """

    @given(
        orch_provider=_provider_st,
        orch_model=_model_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_orchestrator_override_takes_precedence(self, orch_provider, orch_model):
        """When orchestrator override is non-empty, resolve_settings_keys uses it."""
        from distr.core.llm_factory import resolve_settings_keys, normalize_provider

        override = LLMOverride(
            orchestrator_provider=orch_provider,
            orchestrator_model=orch_model,
        )
        token = set_llm_override(override)
        try:
            # Global settings should be ignored
            global_settings = {
                "conversational_llm_provider": "ShouldNotUse",
                "conversational_llm_model": "should-not-use-model",
            }
            provider, model = resolve_settings_keys(global_settings)
            assert provider == normalize_provider(orch_provider)
            assert model == orch_model
        finally:
            clear_llm_override(token)

    @given(
        coder_provider=_provider_st,
        coder_model=_model_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_coder_override_takes_precedence(self, coder_provider, coder_model):
        """When coder override is non-empty, _get_coding_llm uses it."""
        from unittest.mock import patch

        override = LLMOverride(
            coder_provider=coder_provider,
            coder_model=coder_model,
        )
        token = set_llm_override(override)
        try:
            # Mock load_settings_from_db (imported inside _get_coding_llm via distr.core.settings)
            mock_settings = {
                "coding_llm_provider": "ShouldNotUse",
                "coding_llm_model": "should-not-use-model",
                "agent_provider": "ShouldNotUse",
                "agent_model": "should-not-use-model",
            }
            with patch(
                "distr.core.settings.load_settings_from_db",
                return_value=mock_settings,
            ):
                from distr.core.step_runner.code_generator import CodeGeneratorService

                svc = CodeGeneratorService()
                provider, model, _ = svc._get_coding_llm()
                assert provider == coder_provider.strip().lower()
                assert model == coder_model.strip() or (
                    not coder_model.strip() and provider == "ollama" and model == "llama3.2"
                )
        finally:
            clear_llm_override(token)

    @given(
        sub_provider=_provider_st,
        sub_model=_model_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_sub_override_stored_and_retrievable(self, sub_provider, sub_model):
        """When sub override is non-empty, get_llm_override returns it."""
        override = LLMOverride(
            sub_provider=sub_provider,
            sub_model=sub_model,
        )
        token = set_llm_override(override)
        try:
            current = get_llm_override()
            assert current is not None
            assert current.sub_provider == sub_provider
            assert current.sub_model == sub_model
        finally:
            clear_llm_override(token)

    def test_no_override_falls_back_to_global_settings(self):
        """When no override is active, resolve_settings_keys uses global settings."""
        from distr.core.llm_factory import resolve_settings_keys

        # Clear any stale override from previous tests
        current = get_llm_override()
        if current is not None:
            # Force-reset to None by setting and immediately resetting
            token = set_llm_override(LLMOverride())
            clear_llm_override(token)
            # If still set, directly set to None
            if get_llm_override() is not None:
                from distr.core.llm_override import _llm_override_var
                _llm_override_var.set(None)

        assert get_llm_override() is None

        settings = {
            "conversational_llm_provider": "OpenAI",
            "conversational_llm_model": "gpt-4o",
        }
        provider, model = resolve_settings_keys(settings)
        assert provider == "OpenAI"
        assert model == "gpt-4o"


class TestLLMOverrideLifecycle:
    """Property 13: LLM Override lifecycle scoping.

    *For any* workflow run triggered by the Agent_Check_In, the LLMOverride should
    be active (non-None) during the run and cleared (None) after the run reaches a
    terminal status. Setting and clearing should be idempotent — clearing when
    already None should not error.

    **Validates: Requirements 7.1, 7.2**
    """

    @given(
        orch_provider=_provider_st,
        orch_model=_model_st,
        coder_provider=_provider_st,
        coder_model=_model_st,
        sub_provider=_provider_st,
        sub_model=_model_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_override_active_during_run_cleared_after(
        self,
        orch_provider,
        orch_model,
        coder_provider,
        coder_model,
        sub_provider,
        sub_model,
    ):
        """Override is non-None after set, None after clear."""
        override = LLMOverride(
            orchestrator_provider=orch_provider,
            orchestrator_model=orch_model,
            coder_provider=coder_provider,
            coder_model=coder_model,
            sub_provider=sub_provider,
            sub_model=sub_model,
        )

        # Before: no override
        assert get_llm_override() is None

        # Set override — simulates Agent_Check_In start
        token = set_llm_override(override)

        # During: override is active
        current = get_llm_override()
        assert current is not None
        assert current.orchestrator_provider == orch_provider
        assert current.coder_provider == coder_provider
        assert current.sub_provider == sub_provider

        # Clear override — simulates run reaching terminal status
        clear_llm_override(token)

        # After: override is cleared
        assert get_llm_override() is None

    def test_clear_when_already_none_does_not_error(self):
        """Clearing when no override is active should not raise."""
        # Ensure no override is active
        assert get_llm_override() is None

        # Set and immediately clear to get a valid token, then verify state is None
        override = LLMOverride(orchestrator_provider="test")
        token = set_llm_override(override)
        clear_llm_override(token)
        assert get_llm_override() is None

        # Setting None explicitly and clearing is also safe
        token2 = set_llm_override(LLMOverride())
        clear_llm_override(token2)
        assert get_llm_override() is None

    @given(
        provider1=_provider_st,
        provider2=_provider_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_nested_overrides_restore_correctly(self, provider1, provider2):
        """Nested set/clear restores the previous override (or None)."""
        # Set first override
        override1 = LLMOverride(orchestrator_provider=provider1)
        token1 = set_llm_override(override1)

        assert get_llm_override().orchestrator_provider == provider1

        # Set second override (nested)
        override2 = LLMOverride(orchestrator_provider=provider2)
        token2 = set_llm_override(override2)

        assert get_llm_override().orchestrator_provider == provider2

        # Clear second — should restore first
        clear_llm_override(token2)
        assert get_llm_override().orchestrator_provider == provider1

        # Clear first — should restore None
        clear_llm_override(token1)
        assert get_llm_override() is None
