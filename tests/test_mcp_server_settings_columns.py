"""mcpcols 2026-10-22: mcp_server_* settings columns exist, persist, and migrate idempotently.

Runs in a subprocess with a throwaway HOME + DECISIONS_DB_DIR under /tmp; never touches the real DB.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import os, sqlite3
from distr.core.paths import DB_DIR
assert DB_DIR.startswith("/tmp") or DB_DIR.startswith("/private/tmp"), DB_DIR
import distr.core.db as db  # creates schema + runs migrations
from distr.core.db.migrations import run_migrations
path = os.path.join(DB_DIR, "settings.db")
def cols():
    return {r[1]: r[4] for r in sqlite3.connect(path).execute("PRAGMA table_info(settings)")}
c = cols()
assert "mcp_server_enabled" in c and "mcp_server_relay_vendors" in c, sorted(c)
run_migrations(); run_migrations()  # twice more: must be safe
assert "mcp_server_enabled" in cols()
from distr.core.settings import load_settings_from_db, save_settings_to_db
s = load_settings_from_db()
if not s:
    from distr.core.settings import DEFAULT_SETTINGS
    save_settings_to_db(dict(DEFAULT_SETTINGS))
from distr.core.mcp_server import config as c2
os.environ.pop(c2.ENV_ENABLED, None)
assert c2.is_enabled() is False
assert c2.relay_vendors_enabled() is True  # empty column = default on
c2.set_enabled(True)
assert c2.is_enabled() is True
row = sqlite3.connect(path).execute("SELECT mcp_server_enabled FROM settings").fetchone()
assert row and bool(row[0]) is True, row
c2.set_enabled(False)
assert c2.is_enabled() is False
print("ALL_OK")
'''


def test_mcp_server_columns_persist_and_migrate_idempotently():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        env = {**os.environ, "HOME": tmp, "DECISIONS_DB_DIR": os.path.join(tmp, "db"),
               "DECISIONS_TEST_MODE": "1", "PYTHONPATH": str(ROOT)}
        env.pop("DECISIONS_MCP_SERVER_ENABLED", None)
        p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=240)
        assert "ALL_OK" in p.stdout, (p.stdout[-1500:], p.stderr[-3000:])
