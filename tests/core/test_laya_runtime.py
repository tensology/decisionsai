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
