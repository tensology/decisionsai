import sys

_sm = sys.modules.get("distr.core.settings")
if _sm is not None and getattr(_sm, "__file__", None) is None:
    del sys.modules["distr.core.settings"]

import distr.core.settings as settings_mod


def test_ticket_board_no_longer_defines_agent_settings_defaults():
    defaults = settings_mod.DEFAULT_SETTINGS

    assert not any(key.startswith("kanban_agent_") for key in defaults)
    assert "kanban_cli_tool" in defaults
    assert "kanban_cli_auth" in defaults

    for level in ("low", "medium", "high"):
        assert f"project_cli_{level}_backend" in defaults
        assert f"project_cli_{level}_model" in defaults
