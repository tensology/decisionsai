from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def test_load_json_connected_accounts_imports_settings_loader():
  py = (ROOT / "distr/gui/web/routes/kanban.py").read_text(encoding="utf-8")
  start = py.index("def _load_json_connected_accounts")
  block = py[start : start + 400]
  assert "from distr.core.settings import load_settings_from_db" in block


def test_load_json_connected_accounts_returns_parsed_list():
  from distr.gui.web.routes.kanban import _load_json_connected_accounts

  sample = [{"provider": "jira", "email": "a@b.com", "api_token": "tok"}]
  with patch("distr.core.settings.load_settings_from_db", return_value={"connected_accounts": sample}):
    assert _load_json_connected_accounts() == sample
