"""autocog 2026-10-22: row icons (play / list / clock / cog), separate harness+model and
schedule modals, calendar schedule <-> cron mapping, and the PUT endpoint re-applying
the schedule (next_run_at). DB parts run in a subprocess with a throwaway /tmp HOME +
DECISIONS_DB_DIR, never the real db/settings.db.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
JS = ROOT / "distr/gui/web/static/development/automations/index.js"

from distr.core.automation.schedule_cron import (  # noqa: E402
    ScheduleValidationError,
    calendar_schedule_to_cron,
    cron_to_calendar_schedule,
)

CASES = [
    ({"kind": "daily", "time": "07:05"}, "5 7 * * *"),
    ({"kind": "weekly", "time": "09:30", "days": "1,3,5"}, "30 9 * * 1,3,5"),
    ({"kind": "weekly", "time": "18:00", "days": "0,6"}, "0 18 * * 0,6"),
    ({"kind": "monthly", "time": "23:59", "days": "31"}, "59 23 31 * *"),
    ({"kind": "yearly", "time": "08:00", "days": "29", "month": "2"}, "0 8 29 2 *"),
]


@pytest.mark.parametrize("schedule,cron", CASES)
def test_schedule_to_cron_and_back(schedule, cron):
    assert calendar_schedule_to_cron(schedule) == cron
    back = cron_to_calendar_schedule(cron)
    assert back["kind"] == schedule["kind"] and back["time"] == schedule["time"]
    if "days" in schedule:
        assert back["days"] == schedule["days"]
    if "month" in schedule:
        assert back["month"] == schedule["month"]
    assert calendar_schedule_to_cron(back) == cron


def test_weekday_normalization():
    assert calendar_schedule_to_cron({"kind": "weekly", "time": "9:00", "days": "5,1,7,1"}) == "0 9 * * 0,1,5"


@pytest.mark.parametrize("bad", [
    {"kind": "daily", "time": "24:00"},
    {"kind": "daily", "time": "nine"},
    {"kind": "weekly", "time": "09:00", "days": ""},
    {"kind": "weekly", "time": "09:00", "days": "8"},
    {"kind": "monthly", "time": "09:00", "days": "0"},
    {"kind": "monthly", "time": "09:00", "days": "32"},
    {"kind": "yearly", "time": "09:00", "days": "31", "month": "4"},
    {"kind": "yearly", "time": "09:00", "days": "1", "month": "13"},
    {"kind": "fortnightly", "time": "09:00"},
])
def test_invalid_schedules_rejected(bad):
    with pytest.raises(ScheduleValidationError):
        calendar_schedule_to_cron(bad)


@pytest.mark.parametrize("cron", ["*/5 * * * *", "0 9 1 * 1", "0 9 * *"])
def test_non_calendar_cron_rejected(cron):
    with pytest.raises(ScheduleValidationError):
        cron_to_calendar_schedule(cron)


SCRIPT = r'''
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from distr.core.db import init_db
init_db()
from distr.core.automation.store import create_automation
from distr.core.automation import store
from distr.core.automation.scheduler_advance import advance_next_run_for_automation
from distr.core.db import get_session
from distr.core.db.automation import Automation
from distr.gui.web.routes.automations import create_routes
from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token, require_internal_token_request

app = FastAPI()
@app.middleware("http")
async def guard(request: Request, call_next):  # same rule as server.py: non-GET /api needs the token
    if request.url.path.startswith("/api/") and request.method != "GET":
        try:
            require_internal_token_request(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)
app.include_router(create_routes(), prefix="/api")
c = TestClient(app)
good = {INTERNAL_AUTH_HEADER: get_internal_api_token()}
TZ = "Africa/Johannesburg"
a = create_automation(name="T", automation_type="scheduled_instruction", status="active", instruction="x",
                      preset_id="", schedule={"kind": "daily", "time": "09:00", "timezone": TZ},
                      action_config={"cli_route_mode": "inherit"})
url = f"/api/automations/{a['id']}"

assert c.put(url, json={"schedule": {"kind": "weekly", "time": "10:00", "days": "1"}}).status_code == 401

def local_next(auto):
    n = datetime.fromisoformat(auto["next_run_at"].rstrip("Z")).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ))
    return n

r = c.put(url, headers=good, json={"schedule": {"kind": "weekly", "time": "10:15", "days": "2,4", "timezone": TZ}})
assert r.status_code == 200, r.text
au = r.json()["automation"]
assert au["schedule"]["kind"] == "weekly" and au["schedule"]["days"] == "2,4" and au["schedule"]["time"] == "10:15"
n = local_next(au); assert n.isoweekday() in (2, 4) and (n.hour, n.minute) == (10, 15), n

r = c.put(url, headers=good, json={"schedule": {"kind": "monthly", "time": "06:30", "days": "15", "timezone": TZ}})
n = local_next(r.json()["automation"]); assert n.day == 15 and (n.hour, n.minute) == (6, 30), n

r = c.put(url, headers=good, json={"schedule": {"kind": "yearly", "time": "07:45", "days": "3", "month": "11", "timezone": TZ}})
assert r.status_code == 200, r.text
au = r.json()["automation"]
assert au["schedule"]["kind"] == "yearly" and au["schedule"]["month"] == "11" and au["schedule"]["days"] == "3"
n = local_next(au); assert (n.month, n.day, n.hour, n.minute) == (11, 3, 7, 45), n
# Advance after a run (scheduler path) stays on the yearly cadence.
with get_session() as s:
    row = s.get(Automation, au["record_id"]); advance_next_run_for_automation(row); s.commit()
    adv = datetime.fromisoformat(row.next_run_at.isoformat()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ))
    assert (adv.month, adv.day, adv.hour, adv.minute) == (11, 3, 7, 45), adv

for bad in ({"kind": "weekly", "time": "10:00", "days": ""}, {"kind": "yearly", "time": "10:00", "days": "31", "month": "2"},
            {"kind": "daily", "time": "25:00"}):
    r = c.put(url, headers=good, json={"schedule": bad}); assert r.status_code in (400, 422), (bad, r.status_code, r.text)
assert c.get(url).json()["automation"]["schedule"]["kind"] == "yearly"  # rejected edits left it untouched

# Harness/model edit goes through action_config and leaves the schedule alone.
r = c.put(url, headers=good, json={"action_config": {"cli_route_mode": "custom", "cli_backend": "codex", "cli_model": "gpt-x", "cli_model_provider": "openai"}})
au = r.json()["automation"]
assert au["action_config"]["cli_backend"] == "codex" and au["action_config"]["cli_model"] == "gpt-x"
assert au["schedule"]["kind"] == "yearly"
print("ALL_OK")
'''


def test_put_endpoint_reapplies_schedule_db():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        env = {**os.environ, "HOME": tmp, "DECISIONS_DB_DIR": os.path.join(tmp, "db"),
               "DECISIONS_TEST_MODE": "1", "PYTHONPATH": str(ROOT)}
        for k in ("DECISIONSAI_INTERNAL_API_TOKEN", "DECISIONS_INTERNAL_API_TOKEN"):
            env.pop(k, None)
        p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=240)
        assert "ALL_OK" in p.stdout, (p.stdout[-1500:], p.stderr[-4000:])


def test_server_guards_non_get_api():
    src = (ROOT / "distr/gui/web/server.py").read_text()
    assert 'method != "GET"' in src and "require_internal_token_request(request)" in src


def test_row_icons_and_separate_modals():
    js = JS.read_text()
    assert "${button('run', 'Run now')}${toggle}${button('history', 'Run history')}${isMessageTriggered(automation) ? '' : button('schedule', 'Edit schedule')}${button('settings', 'Harness and model')}" in js
    for key in ("run:", "history:", "schedule:", "settings:"):
        assert key in js
    assert "action === 'settings') openRouteModal(" in js and "action === 'schedule') openScheduleModal(" in js
    route = js[js.index("function openRouteModal"):js.index("const CAL_WEEKDAYS")]
    sched = js[js.index("function openScheduleModal"):js.index("// automodel 2026-10-22: per-automation")]
    assert "data-automation-cli-backend" in route and "data-sched" not in route
    assert "data-sched-kind" in sched and "data-automation-cli" not in sched
    assert "Applies to future runs" in route
    for k in ("'daily', 'Daily'", "'weekly', 'Weekly'", "'monthly', 'Monthly'", "'yearly', 'Yearly'"):
        assert k in sched
    assert "Cancel" in js and "data-edit-save" in js


def test_inline_harness_model_removed_and_history_modal_runs_only():
    js = JS.read_text()
    assert '<span role="columnheader">Harness · model</span>' not in js and 'data-row-action="model" data-automation-id' not in js
    tri = 'M6 4.5v11a.8.8 0 0 0 1.2.7l9-5.5'
    bolt = 'M11 2 4 11.5h5L8 18l8-10h-5l1-6z'
    icons = js[js.index("const ICONS = {"):js.index("function rowActionsHtml")]
    run_line = next(l for l in icons.splitlines() if l.strip().startswith("run:"))
    resume_line = next(l for l in icons.splitlines() if l.strip().startswith("resume:"))
    assert tri in run_line and bolt in resume_line  # icons swapped
    assert "button('resume', 'Resume')" in js and "action === 'run') runAutomation(" in js
    hist = js[js.index("async function openHistoryModal"):js.index("// autocog 2026-10-22: two separate")]
    assert "bindModelRoute" not in hist and "data-automation-cli" not in hist


def test_cache_busters():
    # Shared entry points are bumped by several jobs; only require a versioned URL there.
    import re
    app = (ROOT / "distr/gui/web/static/development/app.js").read_text()
    css = (ROOT / "distr/gui/web/static/development/styles.css").read_text()
    html = (ROOT / "distr/gui/web/templates/development/index.html").read_text()
    assert re.search(r"automations/index\.js\?v=\d{8}-[\w-]+'", app)
    assert re.search(r"automations/styles\.css\?v=\d{8}-[\w-]+'", css)
    assert re.search(r'development/app\.js\?v=\d{8}-[\w-]+"', html)
    assert re.search(r'development/styles\.css\?v=\d{8}-[\w-]+"', html)
