from types import SimpleNamespace

from distr.core.agent.tools.system.project_tools import _find_explicit_projects_from_instruction


def test_find_explicit_projects_matches_project_name_phrase():
    projects = [
        SimpleNamespace(name="DecisionsAI", additional_trigger_words='["decisions"]'),
        SimpleNamespace(name="RelightSA", additional_trigger_words='["relight"]'),
    ]
    result = _find_explicit_projects_from_instruction(
        projects,
        "create this ticket in DecisionsAI and include rollout notes",
    )
    assert len(result) == 1
    assert result[0].name == "DecisionsAI"


def test_find_explicit_projects_returns_multiple_on_ambiguity():
    projects = [
        SimpleNamespace(name="DecisionsAI", additional_trigger_words='["decisions"]'),
        SimpleNamespace(name="RelightSA", additional_trigger_words='["relight"]'),
    ]
    result = _find_explicit_projects_from_instruction(
        projects,
        "write this into DecisionsAI or RelightSA",
    )
    assert {p.name for p in result} == {"DecisionsAI", "RelightSA"}
