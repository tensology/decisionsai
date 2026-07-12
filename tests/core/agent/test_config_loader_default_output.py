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


def test_resolve_system_default_output_device_avoids_virtual_default():
    payload = json.dumps({"index": 5, "name": "ZoomAudioDevice"})
    fresh_devices = [
        (0, "DELL S2421HN", 0, 2),
        (3, "MacBook Pro Speakers", 0, 2),
        (4, "Microsoft Teams Audio", 1, 1),
        (5, "ZoomAudioDevice", 2, 2),
    ]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with patch.object(config_loader, "_query_devices_fresh_subprocess", return_value=fresh_devices):
            index, name = config_loader.resolve_system_default_output_device()

    assert index == 3
    assert name == "MacBook Pro Speakers"


def test_resolve_system_default_output_device_keeps_explicit_non_virtual_default():
    payload = json.dumps({"index": 0, "name": "DELL S2421HN"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        index, name = config_loader.resolve_system_default_output_device()

    assert index == 0
    assert name == "DELL S2421HN"
