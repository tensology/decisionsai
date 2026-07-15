from distr.gui.web.routes.settings._shared import resolve_secret_update
from distr.gui.web.security import _load_or_create_persistent_token, mask_secret


def test_blank_secret_update_preserves_existing_secret():
    assert resolve_secret_update("sk-existing", "") == "sk-existing"
    assert resolve_secret_update("sk-existing", "   ") == "sk-existing"


def test_masked_secret_update_preserves_existing_secret():
    existing = "sk-existing"
    assert resolve_secret_update(existing, mask_secret(existing)) == existing


def test_new_secret_update_replaces_existing_secret():
    assert resolve_secret_update("sk-existing", "sk-new") == "sk-new"


def test_blank_secret_update_stays_blank_when_no_existing_secret():
    assert resolve_secret_update("", "") == ""


def test_internal_api_token_persists_across_backend_restarts(tmp_path):
    token_path = tmp_path / "runtime" / "internal_api_token"
    first = _load_or_create_persistent_token(token_path)
    second = _load_or_create_persistent_token(token_path)

    assert second == first
    assert len(first) >= 32
    assert token_path.stat().st_mode & 0o777 == 0o600
