from distr.core.workflow import execution_mode


PROPOSAL = {
    "title": "Migrate production authentication",
    "description": "Change the production authentication database schema.",
    "tasks": ["Validate sign-in after migration", "Verify the rollback plan"],
}


def test_proposal_complexity_matches_canonical_scope_assessment():
    decision = execution_mode.choose_proposal_execution_mode(PROPOSAL)
    scope_text = "\n".join([PROPOSAL["title"], PROPOSAL["description"], *PROPOSAL["tasks"]])

    assert decision["scope_text"] == scope_text
    assert decision["complexity"] != "low"
    assert decision["complexity"] == execution_mode.assess_development_request(scope_text)["complexity"]


def test_proposal_decision_ignores_transport_metadata_and_caller_assessments():
    expected = execution_mode.choose_proposal_execution_mode(PROPOSAL)

    for surface, complexity in (("web", "low"), ("telegram", "medium")):
        wrapped = {
            **PROPOSAL,
            "source_metadata": {"approval_source": surface},
            "execution_complexity": complexity,
        }
        assert execution_mode.choose_proposal_execution_mode(wrapped) == expected
        assert execution_mode.choose_development_execution_mode(
            "Rename a button label.",
            assessment={"source": surface, "complexity": complexity, "proposal_scope": wrapped},
        ) == expected


def test_proposal_reason_describes_one_direct_turn():
    decision = execution_mode.choose_proposal_execution_mode(PROPOSAL)

    assert decision["reason"] == "Requested work is handed to Development as one direct agent turn."
    assert decision["visible_reason"] == "Handled directly as one Development agent turn."


def test_proposal_complexity_defaults_to_medium_when_assessment_raises(monkeypatch):
    def fail_assessment(scope_text):
        raise RuntimeError("Assessment failed")

    monkeypatch.setattr(execution_mode, "assess_development_request", fail_assessment)

    assert execution_mode.choose_proposal_execution_mode(PROPOSAL)["complexity"] == "medium"


def test_tensology_thread_uses_xhigh_reasoning_for_high_proposal_complexity(monkeypatch):
    from distr.core import tensology_client

    captured = []

    def capture_post(path, body, **kwargs):
        captured.append(body)
        return {"task": {"id": 349}}

    monkeypatch.setattr(execution_mode, "choose_proposal_execution_mode", lambda proposal: {"complexity": "high"})
    monkeypatch.setattr(tensology_client, "_ensure_tensology_kanban_ticket", lambda *args: 296)
    monkeypatch.setattr(tensology_client, "_execution_skill_ids", lambda ticket: ([], False))
    monkeypatch.setattr(tensology_client, "_repo_default_branch", lambda path: "main")
    monkeypatch.setattr(tensology_client, "_local_api_post", capture_post)
    ticket = {**PROPOSAL, "id": 1162, "reference": "ACCEPTANCE-1", "project_id": 175}
    project = {
        "id": 2,
        "name": "Example",
        "folder_location": "/tmp",
        "coding_backend": "pi",
        "coding_backend_model": "deepseek/deepseek-v4.1-flash",
    }

    tensology_client._create_tensology_thread(ticket, project)

    assert len(captured) == 1
    assert captured[0]["reasoning_effort"] == "xhigh"
