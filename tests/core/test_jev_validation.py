from __future__ import annotations

import json


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        tools = [
            "jev_route_model",
            "jev_guard_tool_call",
            "jev_route_task",
            "jev_check_research",
            "jev_review_completion",
            "jev_decide",
        ]
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": name} for name in tools]},
        }).encode("utf-8")


def test_validate_jev_confirms_auth_and_expected_tool_surface(monkeypatch):
    from distr.core.api_validation import validate_jev

    captured = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    valid, error = validate_jev("jev-test-key")

    assert valid is True
    assert error == ""
    assert captured == {"authorization": "Bearer jev-test-key", "timeout": 15}
