from distr.core import laya_runtime


def test_decode_tool_result_unwraps_laya_result_json():
    result = laya_runtime._decode_tool_result(
        {
            "structuredContent": {
                "result": '{"answers":{"domain":{"choice":"code"}},"latency_ms":12.4}'
            }
        }
    )

    assert result == {
        "answers": {"domain": {"choice": "code"}},
        "latency_ms": 12.4,
    }


def test_assess_request_maps_laya_router_output(monkeypatch):
    monkeypatch.setattr(
        laya_runtime,
        "call_tool",
        lambda *_args, **_kwargs: {
            "answers": {
                "difficulty": {"score": 2.6, "answer_confidence": 0.91},
                "domain": {"choice": "code", "answer_confidence": 0.88},
                "needs_tools": {"noul": 0.82, "answer_confidence": 0.84},
                "is_sensitive": {"noul": 0.12, "answer_confidence": 0.80},
            },
            "latency_ms": 34.2,
            "routing": {"model": "english"},
        },
    )

    result = laya_runtime.assess_request("Refactor this service")

    assert result == {
        "complexity": "high",
        "difficulty": 2.6,
        "domain": "code",
        "needs_tools": True,
        "is_sensitive": False,
        "confidence": 0.80,
        "latency_ms": 34.2,
        "routing": {"model": "english"},
    }


def test_assess_discussion_maps_typed_shadow_output(monkeypatch):
    monkeypatch.setattr(
        laya_runtime,
        "call_tool",
        lambda *_args, **_kwargs: {
            "answers": {
                "scope": {"choice": "new_ticket", "answer_confidence": 0.91},
                "needs_repository_context": {"noul": 0.8, "answer_confidence": 0.88},
                "reply_policy": {"choice": "scope_warning", "answer_confidence": 0.9},
                "needs_full_model_review": {"noul": 0.95, "answer_confidence": 0.87},
                "risk_level": {"score": 2.2, "answer_confidence": 0.86},
                "target_repository_group": {"choice": "repository_1", "answer_confidence": 0.89},
            },
            "latency_ms": 11.6,
            "routing": {"model": "english"},
        },
    )

    result = laya_runtime.assess_discussion({"repositories": [{"name": "API", "purpose": "Backend"}]})

    assert result["scope"] == "new_ticket"
    assert result["needs_repository_context"] is True
    assert result["reply_policy"] == "scope_warning"
    assert result["risk_level"] == 2
    assert result["target_repository_group"] == "API"
    assert result["confidence"] == 0.86
    assert result["qualified_for_routing"] is True


def test_low_confidence_discussion_assessment_requires_full_model_review(monkeypatch):
    monkeypatch.setattr(
        laya_runtime,
        "call_tool",
        lambda *_args, **_kwargs: {
            "answers": {
                "scope": {"choice": "ambiguous", "answer_confidence": 0.22},
                "needs_repository_context": {"noul": 0.1, "answer_confidence": 0.7},
                "reply_policy": {"choice": "respond", "answer_confidence": 0.4},
                "needs_full_model_review": {"noul": 0.1, "answer_confidence": 0.7},
                "risk_level": {"score": 2, "answer_confidence": 0.4},
                "target_repository_group": {"choice": "unknown", "answer_confidence": 0.3},
            },
        },
    )

    result = laya_runtime.assess_discussion({"repositories": []})

    assert result["needs_full_model_review"] is True
    assert result["qualified_for_routing"] is False
