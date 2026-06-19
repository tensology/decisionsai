import string

from distr.core.agent.services.stt import assemblyai as assemblyai_stt


def test_assemblyai_service_imports_runtime_dependencies_for_dictation():
    assert assemblyai_stt.InterruptionFrame is not None
    assert assemblyai_stt.UserStoppedSpeakingFrame is not None
    assert assemblyai_stt.string is string
