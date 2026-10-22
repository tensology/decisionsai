"""schedmodel 2026-10-22: per-schedule harness/provider/model; inherited schedules follow the global default.

DB part runs in a subprocess with a throwaway /tmp HOME + DECISIONS_DB_DIR.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "distr/gui/web/static/development/automations/index.js"

SCRIPT = r'''
from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.automation.model_route import resolve_automation_route
inherit = {"cli_route_mode": "inherit"}
custom = {"cli_route_mode": "custom", "cli_backend": "pi", "cli_model_provider": "minimax", "cli_model": "minimax/minimax-m3.1-flash"}
s = load_settings_from_db(); s.update(automation_cli_backend="claude", automation_cli_model_provider="anthropic", automation_cli_model="claude-opus-4"); save_settings_to_db(s)
r, src = resolve_automation_route(inherit, load_settings_from_db()); assert src == "automation_global_default" and r["backend"] == "claude", r
s = load_settings_from_db(); s.update(automation_cli_backend="codex", automation_cli_model_provider="openai", automation_cli_model="gpt-5.3-codex"); save_settings_to_db(s)
r, _ = resolve_automation_route(inherit, load_settings_from_db()); assert r["backend"] == "codex" and r["model"] == "gpt-5.3-codex", r
r, src = resolve_automation_route(custom, load_settings_from_db()); assert src == "automation_override" and r == {"backend": "pi", "model_provider": "minimax", "model": "minimax/minimax-m3.1-flash"}, r
print("ALL_OK")
'''


def test_inherited_schedule_follows_global_default_db():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        env = {**os.environ, "HOME": tmp, "DECISIONS_DB_DIR": os.path.join(tmp, "db"),
               "DECISIONS_TEST_MODE": "1", "PYTHONPATH": str(ROOT)}
        p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=180)
        assert "ALL_OK" in p.stdout, (p.stdout[-1500:], p.stderr[-3000:])


def test_orchestrator_reads_settings_per_run():
    src = (ROOT / "distr/core/automation_orchestrator.py").read_text()
    i = src.index("resolve_automation_route(config, _settings)")
    assert "load_settings_from_db()" in src[i - 400:i]


def test_modal_has_harness_provider_model_pickers_and_inherit():
    js = JS.read_text()
    for marker in ("data-automation-cli-backend", "data-automation-cli-provider", "data-automation-cli-model"):
        assert marker in js
    assert "Inherit default" in js
    assert "cli_route_mode: 'inherit'" in js and "cli_route_mode: 'custom'" in js
    assert "cli_model_provider: option?.dataset.provider || providerSel?.value" in js


def test_list_shows_readable_default_and_no_auto_tag():
    js = JS.read_text()
    assert "Default: ${inner}" in js
    assert "Next run" in js and "Last run" in js  # autocog: inline Harness · model column moved to the cog modal
    assert "Africa/Johannesburg" in js
    code = "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))
    assert "#auto" not in code and "auto_${" not in code


def test_cache_busters():
    import re
    assert re.search(r"automations/index\.js\?v=\d{8}-[\w-]+'", (ROOT / "distr/gui/web/static/development/app.js").read_text())
    assert re.search(r'development/app\.js\?v=\d{8}-[\w-]+"', (ROOT / "distr/gui/web/templates/development/index.html").read_text())
