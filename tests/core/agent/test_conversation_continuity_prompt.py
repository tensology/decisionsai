from distr.core.agent.services.llm.prompt import load_system_prompt_template


def test_full_prompt_treats_voice_transcripts_as_conversation_history():
    prompt = load_system_prompt_template(
        model_name="gpt-5",
        provider_name="openrouter",
    )

    assert "Voice notes are transcribed into ordinary user messages" in prompt
    assert "Never claim you cannot access an earlier voice note" in prompt
    assert '"it", "that", "again", "do it"' in prompt


def test_lite_prompt_preserves_voice_note_continuity_rule():
    prompt = load_system_prompt_template(
        model_name="qwen2.5:0.5b",
        provider_name="ollama",
    )

    assert "Voice notes are transcribed into ordinary user messages" in prompt
    assert "Never claim the voice note is inaccessible" in prompt


def test_full_prompt_treats_voice_name_as_agent_identity():
    prompt = load_system_prompt_template(
        model_name="gpt-5",
        provider_name="openrouter",
    )

    # The configured voice IS the agent's identity, not just a voice setting.
    assert "You are {agent_name}" in prompt
    assert "is your name and your identity" in prompt
    assert 'If asked for your name, say "{agent_name}".' in prompt
    # The old wording explicitly denied the voice name as an identity.
    assert "presentation choice, not your identity" not in prompt
