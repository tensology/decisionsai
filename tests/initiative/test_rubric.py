"""Unit tests for distr.core.initiative.rubric."""

from distr.core.initiative.policy import PolicyDecision
from distr.core.initiative.rubric import RubricScore


def test_total_sum():
    r = RubricScore(4, 5, 5, 3, 4)
    assert r.total == 21


def test_from_payload_clamps_and_defaults():
    r = RubricScore.from_payload(
        {"impact": 0, "risk": 99, "cost": "2.7", "urgency": None, "confidence": "x"}
    )
    assert r.impact == 1
    assert r.risk == 5
    assert r.cost == 2
    assert r.urgency == 3
    assert r.confidence == 3


def test_from_payload_empty_or_no_keys_returns_none():
    assert RubricScore.from_payload(None) is None
    assert RubricScore.from_payload({}) is None
    assert RubricScore.from_payload({"note": "x"}) is None


def test_from_payload_partial_dims():
    r = RubricScore.from_payload({"impact": 5})
    assert r == RubricScore(5, 3, 3, 3, 3)
    assert r.total == 17


def test_policy_decision_observe_skip_below_13():
    r = RubricScore(2, 2, 2, 2, 2)
    assert r.total == 10
    assert r.policy_decision("observe") == PolicyDecision.SKIP


def test_policy_decision_observe_draft_from_13():
    r = RubricScore(3, 3, 3, 2, 2)
    assert r.total == 13
    assert r.policy_decision("observe") == PolicyDecision.DRAFT_AND_ASK


def test_policy_decision_operate_execute_at_18():
    r = RubricScore(4, 4, 4, 3, 3)
    assert r.total == 18
    assert r.policy_decision("operate") == PolicyDecision.EXECUTE


def test_policy_decision_operate_draft_below_18():
    r = RubricScore(4, 4, 3, 3, 2)
    assert r.total == 16
    assert r.policy_decision("operate") == PolicyDecision.DRAFT_AND_ASK


def test_as_dict_roundtrip_keys():
    r = RubricScore(1, 2, 3, 4, 5)
    assert r.as_dict() == {
        "impact": 1,
        "risk": 2,
        "cost": 3,
        "urgency": 4,
        "confidence": 5,
    }
