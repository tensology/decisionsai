"""Tests for shared LiteLLM configuration helpers."""

from distr.core.litellm_utils import configure_litellm, litellm_completion


def test_configure_litellm_is_idempotent(monkeypatch):
    configure_litellm()
    configure_litellm()


def test_litellm_completion_redirects_stderr(monkeypatch):
    fake_litellm = type(
        "LiteLLM",
        (),
        {
            "set_verbose": False,
            "suppress_debug_info": True,
            "completion": staticmethod(lambda **kwargs: "ok"),
        },
    )()

    import sys

    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    result = litellm_completion(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result == "ok"
