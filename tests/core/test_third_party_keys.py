from __future__ import annotations


def test_composio_api_key_prefers_env(monkeypatch):
    from distr.core.third_party_keys import composio_api_key

    monkeypatch.setenv("COMPOSIO_API_KEY", "env-key")
    assert composio_api_key() == "env-key"


def test_composio_api_key_reads_rube_token_from_settings(monkeypatch):
    from distr.core import third_party_keys

    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("COMPOSIO_KEY", raising=False)
    monkeypatch.setattr(
        third_party_keys,
        "_from_settings",
        lambda *fields: "db-key" if "rube_token" in fields else "",
    )
    assert third_party_keys.composio_api_key() == "db-key"


def test_redact_maps_rube_token_to_composio_key():
    from distr.gui.web.security import redact_thirdparty_settings

    out = redact_thirdparty_settings({"rube_enabled": True, "rube_token": "secret-token"})
    assert out["composio_enabled"] is True
    assert out["composio_key_set"] is True
    assert out["composio_key"] == ""


def test_redact_never_returns_tensology_key():
    from distr.gui.web.security import redact_thirdparty_settings

    out = redact_thirdparty_settings({
        "tensology_enabled": True,
        "tensology_key": "tns_decisionsai_do-not-return",
    })
    assert out["tensology_enabled"] is True
    assert out["tensology_key"] == ""
    assert out["tensology_key_set"] is True
    assert "do-not-return" not in str(out)
