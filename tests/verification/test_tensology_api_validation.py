import json
from unittest.mock import MagicMock, patch

from distr.core.api_validation import validate_tensology


def _response(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"data": payload}).encode()
    return response


def test_tensology_validation_checks_identity_and_capabilities():
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _response({"audience": "decisionsai"}),
            _response({"mail": True, "workshop": True}),
        ],
    ) as urlopen:
        assert validate_tensology("tns_test") == (True, "")

    assert urlopen.call_count == 2
    assert urlopen.call_args_list[1].args[0].full_url.endswith("/capabilities")


def test_tensology_validation_rejects_key_without_mail_scope():
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _response({"audience": "decisionsai"}),
            _response({"mail": False, "workshop": True}),
        ],
    ):
        valid, message = validate_tensology("tns_test")

    assert valid is False
    assert "mail access" in message
