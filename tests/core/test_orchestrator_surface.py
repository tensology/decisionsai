from distr.core.workflow.development_threads import orchestrator_surface


def test_questions_and_stories_stay_in_chat():
    assert orchestrator_surface("How are you doing?") == "chat"
    assert orchestrator_surface("Tell me a story about who you are.") == "chat"
    assert orchestrator_surface("What are you listening for?") == "chat"
    assert orchestrator_surface("can you tell me what this project is about") == "chat"
    assert orchestrator_surface("What's the status on Tensology") == "chat"


def test_project_work_opens_development():
    assert orchestrator_surface("Build local model smoke page") == "development"
    assert orchestrator_surface("Define Extract product behavior") == "development"
    assert orchestrator_surface("Verify card-thread evidence") == "development"
    assert orchestrator_surface("harness-quality-live scratch edit") == "development"
    assert orchestrator_surface("Prepare the implementation scope") == "development"
    assert orchestrator_surface("can you fix the login") == "development"


def test_explicit_surface_wins():
    assert orchestrator_surface("new chat about the build") == "chat"
    assert orchestrator_surface("put this in development") == "development"
    assert orchestrator_surface("") == "unspecified"
    assert orchestrator_surface("Protected thread") == "unspecified"
