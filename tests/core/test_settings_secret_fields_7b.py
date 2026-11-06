"""7b: icloud/fishaudio/replicate/masko secrets are encrypted at rest; safe migration and round-trips (temp DB, fake values)."""

from __future__ import annotations

import pytest

from distr.core import utils
from distr.core.db import Session, Settings
from distr.gui.web.security import mask_secret

FIELDS = ("icloud_app_password", "fishaudio_key", "replicate_api_token", "masko_key")
FAKE = {f: f"FAKE-{f}-0123456789" for f in FIELDS}


def _raw(field):
    with Session() as s:
        return getattr(s.query(Settings).first(), field)


def _write_raw(**values):
    with Session() as s:
        row = s.query(Settings).first()
        if row is None:
            row = Settings()
            s.add(row)
        for k, v in values.items():
            setattr(row, k, v)
        s.commit()


@pytest.fixture(autouse=True)
def _reset():
    _write_raw(**{f: "" for f in FIELDS})
    yield
    _write_raw(**{f: "" for f in FIELDS})


def test_fields_are_registered_as_secrets():
    assert set(FIELDS) <= utils.SECRET_SETTINGS_FIELDS


def test_plaintext_rows_migrate_to_enc_and_read_back_identical_and_idempotent():
    _write_raw(**FAKE)
    loaded = utils.load_settings_from_db()
    assert all(loaded[f] == FAKE[f] for f in FIELDS)
    first = {f: _raw(f) for f in FIELDS}
    assert all(v.startswith(utils.ENCRYPTION_PREFIX) and FAKE[f] not in v for f, v in first.items())
    again = utils.load_settings_from_db()
    assert all(again[f] == FAKE[f] for f in FIELDS)
    assert {f: _raw(f) for f in FIELDS} == first          # second load: no re-encryption


def test_already_encrypted_rows_are_untouched():
    enc = {f: utils._encrypt_secret(v) for f, v in FAKE.items()}
    _write_raw(**enc)
    loaded = utils.load_settings_from_db()
    assert all(loaded[f] == FAKE[f] for f in FIELDS)
    assert {f: _raw(f) for f in FIELDS} == enc


def test_empty_and_none_values():
    _write_raw(icloud_app_password="", fishaudio_key=None, replicate_api_token="   ", masko_key="")
    loaded = utils.load_settings_from_db()
    assert loaded["icloud_app_password"] == "" and loaded["masko_key"] == ""
    assert loaded["fishaudio_key"] in (None, "")
    assert loaded["replicate_api_token"].strip() == ""
    utils.save_settings_to_db(utils.load_settings_from_db())
    assert _raw("icloud_app_password") == "" and _raw("masko_key") == ""


def test_ui_save_round_trip_masks_never_overwrite_and_no_double_encryption():
    utils.save_settings_to_db(dict(FAKE))
    stored = {f: _raw(f) for f in FIELDS}
    # Full load->save (what every settings route does) keeps one layer of encryption.
    utils.save_settings_to_db(utils.load_settings_from_db())
    assert all(utils._decrypt_secret(_raw(f)) == FAKE[f] for f in FIELDS)
    # Masked placeholders sent back by the UI never replace the secret.
    utils.save_settings_to_db({**utils.load_settings_from_db(),
                               "fishaudio_key": mask_secret(FAKE["fishaudio_key"]),
                               "masko_key": "••••••••  (leave blank to keep)"})
    loaded = utils.load_settings_from_db()
    assert loaded["fishaudio_key"] == FAKE["fishaudio_key"] and loaded["masko_key"] == FAKE["masko_key"]
    # A real new value still saves.
    utils.save_settings_to_db({"masko_key": "FAKE-new-masko"})
    assert utils.load_settings_from_db()["masko_key"] == "FAKE-new-masko"
    assert stored  # silence unused


def test_failed_decrypt_never_blanks_the_stored_value(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_SETTINGS_SECRET", "FAKE-other-machine-secret")
    foreign = utils._encrypt_secret(FAKE["replicate_api_token"])
    monkeypatch.delenv("DECISIONSAI_SETTINGS_SECRET")
    _write_raw(replicate_api_token=foreign, masko_key="FAKE-plain-masko")   # plaintext neighbour forces the re-save path
    loaded = utils.load_settings_from_db()
    assert loaded["replicate_api_token"] == ""                 # unreadable here
    assert _raw("replicate_api_token") == foreign               # migration re-save kept it
    assert _raw("masko_key").startswith(utils.ENCRYPTION_PREFIX)
    utils.save_settings_to_db(loaded)                           # ordinary UI save
    assert _raw("replicate_api_token") == foreign


def test_dev_tools_view_masks_icloud_password_and_store_is_encrypted():
    from distr.gui.web.routes import dev_tools_settings as dts

    utils.save_settings_to_db({"icloud_app_password": FAKE["icloud_app_password"]})
    assert _raw("icloud_app_password").startswith(utils.ENCRYPTION_PREFIX)
    view = dts._view(utils.load_settings_from_db())
    assert view["icloud_app_password_set"] is True and FAKE["icloud_app_password"] not in str(view)
