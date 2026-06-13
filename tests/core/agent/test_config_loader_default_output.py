import json
from types import SimpleNamespace
from unittest.mock import patch

from distr.core.agent import config_loader


def test_resolve_system_default_output_device_parses_subprocess_payload():
    payload = json.dumps({"index": 7, "name": "AirPods Pro"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        index, name = config_loader.resolve_system_default_output_device()

    assert index == 7
    assert name == "AirPods Pro"


def test_resolve_system_default_output_device_returns_none_on_failure():
    with patch("subprocess.run", side_effect=OSError("boom")):
        index, name = config_loader.resolve_system_default_output_device()

    assert index is None
    assert name is None
