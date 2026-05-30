from distr.gui.web.routes.settings._shared import resolve_secret_update
from distr.gui.web.security import mask_secret


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
