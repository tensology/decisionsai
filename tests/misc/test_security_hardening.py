from distr.core import utils as core_utils
from distr.gui.web.security import (
    redact_connected_account,
    redact_thirdparty_settings,
    validate_safe_outbound_url,
)


def test_settings_secret_roundtrip_encrypt_decrypt():
    raw = "sk-test-secret-1234"
    encrypted = core_utils._encrypt_secret(raw)
    assert encrypted != raw
    assert encrypted.startswith(core_utils.ENCRYPTION_PREFIX)
    assert core_utils._decrypt_secret(encrypted) == raw


def test_redacted_thirdparty_does_not_expose_plaintext():
    data = {
        "openai_enabled": True,
        "openai_key": "sk-abcdef123456",
        "assemblyai_enabled": False,
        "assemblyai_key": "",
    }
    redacted = redact_thirdparty_settings(data)
    assert redacted["openai_key"] != "sk-abcdef123456"
    assert redacted["openai_key_set"] is True
    assert redacted["assemblyai_key_set"] is False


def test_redacted_accounts_strip_tokens():
    account = {
        "provider": "trello",
        "name": "Primary",
        "api_key": "abc1234567890",
        "api_token": "topsecret",
        "is_valid": True,
    }
    out = redact_connected_account(account)
    assert "api_token" not in out
    assert "api_key" not in out
    assert out["has_api_token"] is True


def test_ssrf_guard_blocks_localhost_targets():
    try:
        validate_safe_outbound_url("http://127.0.0.1:8080")
        assert False, "expected localhost target to be rejected"
    except ValueError:
        assert True
