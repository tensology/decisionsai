from distr.core.agent.session import AgentSession


class FakePixazoTTSService:
    __module__ = "distr.core.agent.services.tts.pixazo"
    voice_id = "custom_14"
    voice_name = "custom_14"


class FakeOpenAITTSService:
    __module__ = "distr.core.agent.services.tts.openai"
    voice_id = "alloy"
    voice_name = "alloy"


def test_tts_service_matches_target_with_live_pixazo_custom_voice():
    session = object.__new__(AgentSession)

    assert session._tts_service_matches_target(
        FakePixazoTTSService(),
        target_engine="pixazo",
        target_voice_name="custom_14",
        target_voice_id="custom_14",
    )


def test_tts_service_matches_target_rejects_different_engine():
    session = object.__new__(AgentSession)

    assert not session._tts_service_matches_target(
        FakeOpenAITTSService(),
        target_engine="pixazo",
        target_voice_name="custom_14",
        target_voice_id="custom_14",
    )


def test_tts_service_matches_target_rejects_different_voice():
    session = object.__new__(AgentSession)

    assert not session._tts_service_matches_target(
        FakePixazoTTSService(),
        target_engine="pixazo",
        target_voice_name="custom_22",
        target_voice_id="custom_22",
    )
