"""The assessor is the only model in the suggestion path, so it must fail closed."""

from types import SimpleNamespace

import pytest

from distr.core.initiative import assessor


def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ---------------------------------------------------------------- parsing

def test_parses_a_plain_json_object():
    result = assessor.parse_assessment(
        '{"worthy": true, "confidence": 0.8, "kind": "Dropped Thread", "text": "Have you thought about it?"}'
    )
    assert result["worthy"] is True
    assert result["confidence"] == 0.8
    assert result["kind"] == "dropped-thread"
    assert result["text"] == "Have you thought about it?"


def test_parses_json_wrapped_in_a_code_fence():
    raw = '```json\n{"worthy": true, "confidence": 0.9, "kind": "idea", "text": "Try X"}\n```'
    assert assessor.parse_assessment(raw)["text"] == "Try X"


def test_parses_json_embedded_in_prose():
    raw = 'Sure! {"worthy": true, "confidence": 0.7, "kind": "idea", "text": "Try X"} Hope that helps.'
    assert assessor.parse_assessment(raw)["text"] == "Try X"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no json here at all",
        "{ not valid json",
        "[]",
        '"a string"',
        '{"worthy": false, "confidence": 0.99, "kind": "idea", "text": "should not appear"}',
        '{"worthy": true, "confidence": 0.9, "kind": "idea", "text": ""}',
        '{"worthy": true, "confidence": 0.9, "kind": "idea"}',
    ],
)
def test_unusable_or_unworthy_replies_mean_silence(raw):
    assert assessor.parse_assessment(raw) is None


def test_confidence_is_clamped_and_junk_becomes_zero():
    assert assessor.parse_assessment(
        '{"worthy": true, "confidence": 9, "kind": "idea", "text": "x"}'
    )["confidence"] == 1.0
    assert assessor.parse_assessment(
        '{"worthy": true, "confidence": -4, "kind": "idea", "text": "x"}'
    )["confidence"] == 0.0
    assert assessor.parse_assessment(
        '{"worthy": true, "confidence": "abc", "kind": "idea", "text": "x"}'
    )["confidence"] == 0.0


def test_kind_is_slugified_and_defaulted():
    assert assessor.parse_assessment(
        '{"worthy": true, "confidence": 0.9, "kind": "  Tool   Hint!  ", "text": "x"}'
    )["kind"] == "tool-hint"
    assert assessor.parse_assessment(
        '{"worthy": true, "confidence": 0.9, "text": "x"}'
    )["kind"] == "idea"


def test_text_is_collapsed_and_truncated():
    long_text = "word " * 300
    result = assessor.parse_assessment(
        '{"worthy": true, "confidence": 0.9, "kind": "idea", "text": "%s"}' % long_text.strip()
    )
    assert len(result["text"]) <= assessor.MAX_TEXT_CHARS
    assert "  " not in result["text"]


def test_rubric_is_passed_through_when_present():
    result = assessor.parse_assessment(
        '{"worthy": true, "confidence": 0.9, "kind": "idea", "text": "x", "rubric": {"impact": 5}}'
    )
    assert result["rubric"] == {"impact": 5}


# ---------------------------------------------------------------- assess()

def test_assess_says_nothing_for_an_empty_digest():
    assert assessor.assess("") is None
    assert assessor.assess("   ") is None


def test_assess_returns_the_assessment_on_success(monkeypatch):
    import distr.core.litellm_utils as lu
    import distr.core.llm_factory as lf

    monkeypatch.setattr(lf, "resolve_llm_candidates", lambda settings: [("openai", "gpt-4o-mini")])
    monkeypatch.setattr(
        lu, "litellm_completion",
        lambda **k: _reply('{"worthy": true, "confidence": 0.9, "kind": "idea", "text": "Try X"}'),
    )
    result = assessor.assess("desktop: Warp", settings={"openai_key": "x"})
    assert result["text"] == "Try X"
    assert result["provider"] == "openai"


def test_assess_is_silent_when_the_model_says_nothing(monkeypatch):
    import distr.core.litellm_utils as lu
    import distr.core.llm_factory as lf

    monkeypatch.setattr(lf, "resolve_llm_candidates", lambda settings: [("openai", "gpt-4o-mini")])
    monkeypatch.setattr(lu, "litellm_completion", lambda **k: _reply('{"worthy": false}'))
    assert assessor.assess("desktop: Warp", settings={}) is None


def test_assess_falls_through_to_the_next_candidate(monkeypatch):
    import distr.core.litellm_utils as lu
    import distr.core.llm_factory as lf

    monkeypatch.setattr(
        lf, "resolve_llm_candidates",
        lambda settings: [("openai", "dead"), ("ollama", "llama3.2")],
    )
    calls = []

    def completion(**kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            raise RuntimeError("provider down")
        return _reply('{"worthy": true, "confidence": 0.9, "kind": "idea", "text": "Try X"}')

    monkeypatch.setattr(lu, "litellm_completion", completion)
    result = assessor.assess("desktop: Warp", settings={})
    assert result["text"] == "Try X"
    assert result["provider"] == "ollama"
    assert len(calls) == 2


def test_assess_is_silent_when_every_candidate_fails(monkeypatch):
    import distr.core.litellm_utils as lu
    import distr.core.llm_factory as lf

    monkeypatch.setattr(lf, "resolve_llm_candidates", lambda settings: [("openai", "dead")])

    def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(lu, "litellm_completion", boom)
    assert assessor.assess("desktop: Warp", settings={}) is None


def test_assess_is_silent_when_there_are_no_candidates(monkeypatch):
    import distr.core.llm_factory as lf

    monkeypatch.setattr(lf, "resolve_llm_candidates", lambda settings: [])
    assert assessor.assess("desktop: Warp", settings={}) is None


def test_assess_never_raises_when_candidate_resolution_explodes(monkeypatch):
    import distr.core.llm_factory as lf

    def boom(settings):
        raise RuntimeError("no settings")

    monkeypatch.setattr(lf, "resolve_llm_candidates", boom)
    assert assessor.assess("desktop: Warp", settings={}) is None


def test_recent_suggestions_are_included_in_the_prompt(monkeypatch):
    import distr.core.litellm_utils as lu
    import distr.core.llm_factory as lf

    monkeypatch.setattr(lf, "resolve_llm_candidates", lambda settings: [("openai", "m")])
    captured = {}

    def completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _reply('{"worthy": false}')

    monkeypatch.setattr(lu, "litellm_completion", completion)
    assessor.assess(
        "desktop: Warp",
        settings={},
        recent_suggestions=["you already said this one"],
    )
    user_message = captured["messages"][1]["content"]
    assert "you already said this one" in user_message
    assert "do not repeat" in user_message.lower()
