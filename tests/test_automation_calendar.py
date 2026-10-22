"""calendar 2026-10-22: ignore-occurrence persistence, scheduler skip, drag reschedule mapping.

DB parts run in a subprocess with HOME + DECISIONS_DB_DIR in a throwaway /tmp dir.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRAG = ROOT / "distr/gui/web/static/development/automations/calendar_drag.js"

SCRIPT = r'''
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.testclient import TestClient
from distr.core.db import init_db, get_session
init_db()
from distr.core.db.automation import Automation
from distr.core.automation.store import create_automation, update_automation, schedule_dict_from_row
from distr.core.automation.occurrences import occurrence_key
from distr.core.automation.scheduler_advance import advance_next_run_for_automation
from distr.gui.web.routes.automations import create_routes
from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token

TZ = "Africa/Johannesburg"
app = FastAPI(); app.include_router(create_routes(), prefix="/api"); c = TestClient(app)
good = {INTERNAL_AUTH_HEADER: get_internal_api_token()}
a = create_automation(name="Cal", automation_type="scheduled_instruction", status="active", instruction="x", preset_id="",
                      schedule={"kind": "daily", "time": "09:00", "timezone": TZ}, action_config={})
url = f"/api/automations/{a['id']}/occurrences/ignore"
first = a["next_run_at"]; key = occurrence_key(datetime.fromisoformat(first.rstrip("Z")), TZ)

# token required (route-level dependency)
assert c.post(url, json={"occurrence": key}).status_code == 401
assert c.post(url, headers=good, json={"occurrence": "bogus"}).status_code == 422

# ignore persists in schedule JSON and next_run_at skips it
r = c.post(url, headers=good, json={"occurrence": key, "ignored": True}); assert r.status_code == 200, r.text
au = r.json()["automation"]
assert au["schedule"]["ignored_occurrences"] == [key]
nxt = datetime.fromisoformat(au["next_run_at"].rstrip("Z"))
assert nxt - datetime.fromisoformat(first.rstrip("Z")) == timedelta(days=1), (first, au["next_run_at"])
with get_session() as s:
    row = s.get(Automation, au["record_id"])
    assert key in schedule_dict_from_row(row)["ignored_occurrences"]
    assert key in row.schedule_config

# schedule edits that don't mention ignores keep them
au = update_automation(a["id"], schedule={"kind": "daily", "time": "09:00", "timezone": TZ})
assert au["schedule"]["ignored_occurrences"] == [key]

# advance after a run never lands on an ignored day
day2 = occurrence_key(nxt, TZ)
c.post(url, headers=good, json={"occurrence": day2, "ignored": True})
with get_session() as s:
    row = s.get(Automation, au["record_id"]); advance_next_run_for_automation(row)
    assert occurrence_key(row.next_run_at, TZ) not in {key, day2}; s.rollback()

# scheduler claim skips an ignored due occurrence instead of running it
from distr.core.automation.scheduler import run_scheduled_automation as fn
with get_session() as s:
    row = s.get(Automation, au["record_id"])
    due = datetime.utcnow() - timedelta(minutes=1)
    row.next_run_at = due
    sched = schedule_dict_from_row(row)
    sched["ignored_occurrences"] = sorted(set(sched["ignored_occurrences"]) | {occurrence_key(due, TZ)})
    import json as _j; row.schedule_config = _j.dumps(sched); s.commit()
'''

SCRIPT_TAIL = r'''
from distr.core.db.automation import AutomationRun
before = None
with get_session() as s:
    before = s.query(AutomationRun).count()
from distr.core.automation.store import serialize_automation
with get_session() as s:
    payload = serialize_automation(s.get(Automation, au["record_id"]))
res = fn(payload, on_start_orchestration=lambda *a, **k: (_ for _ in ()).throw(AssertionError("dispatched")))
with get_session() as s:
    row = s.get(Automation, au["record_id"])
    assert s.query(AutomationRun).count() == before, "a run was created for an ignored occurrence"
    assert row.next_run_at and row.next_run_at > datetime.utcnow(), row.next_run_at
    assert occurrence_key(row.next_run_at, TZ) not in set(schedule_dict_from_row(row)["ignored_occurrences"])
assert not res

# un-ignore restores it
r = c.post(url, headers=good, json={"occurrence": key, "ignored": False})
assert key not in r.json()["automation"]["schedule"]["ignored_occurrences"]
# interval schedules can't be ignored
b = create_automation(name="I", automation_type="scheduled_instruction", status="active", instruction="x", preset_id="",
                      schedule={"kind": "interval", "interval": 30, "interval_unit": "minutes"}, action_config={})
assert c.post(f"/api/automations/{b['id']}/occurrences/ignore", headers=good, json={"occurrence": key}).status_code == 422
print("ALL_OK")
'''


def _run(script):
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        env = {**os.environ, "HOME": tmp, "DECISIONS_DB_DIR": os.path.join(tmp, "db"),
               "DECISIONS_TEST_MODE": "1", "PYTHONPATH": str(ROOT)}
        for k in ("DECISIONSAI_INTERNAL_API_TOKEN", "DECISIONS_INTERNAL_API_TOKEN"):
            env.pop(k, None)
        return subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), env=env,
                              capture_output=True, text=True, timeout=240)


def test_ignore_persists_and_scheduler_skips():
    p = _run(SCRIPT + SCRIPT_TAIL)
    assert "ALL_OK" in p.stdout, (p.stdout[-2000:], p.stderr[-4000:])


DRAG_CASES = [
    ({"kind": "daily", "time": "09:00"}, ("2026-10-21", "09:00", "2026-10-23", "14:15"), {"kind": "daily", "time": "14:15"}),
    ({"kind": "daily", "time": "09:00,13:00"}, ("2026-10-21", "13:00", "2026-10-21", "16:30"), {"time": "09:00,16:30"}),
    ({"kind": "weekly", "time": "09:00", "days": "1,3"}, ("2026-10-19", "09:00", "2026-10-23", "10:45"), {"time": "10:45", "days": "3,5"}),
    ({"kind": "weekly", "time": "09:00", "days": "1"}, ("2026-10-19", "09:00", "2026-10-25", "09:00"), {"days": "0"}),
    ({"kind": "monthly", "time": "08:00", "days": "15"}, ("2026-10-15", "08:00", "2026-10-20", "07:30"), {"time": "07:30", "days": "20"}),
    ({"kind": "yearly", "time": "08:00", "days": "3", "month": "11"}, ("2026-11-03", "08:00", "2026-12-24", "18:00"), {"time": "18:00", "days": "24", "month": "12"}),
    ({"kind": "once", "run_at": "2026-10-22T09:00"}, ("2026-10-22", "09:00", "2026-10-24", "11:15"), {"run_at": "2026-10-24T11:15"}),
    ({"kind": "interval", "interval": 30}, ("2026-10-22", "00:00", "2026-10-23", "01:00"), None),
]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_drag_reschedule_mapping_and_snap():
    cases = json.dumps([[c[0], list(c[1])] for c in DRAG_CASES])
    js = f"""
    import('{DRAG.as_uri()}').then((m) => {{
      const out = {cases}.map(([s, a]) => m.shiftSchedule({{...s, timezone: 'Africa/Johannesburg', ignored_occurrences: ['2026-10-30T09:00']}}, ...a));
      const snaps = [m.snapMinutes(7), m.snapMinutes(8), m.snapMinutes(52), m.snapMinutes(-20), m.snapMinutes(1500)];
      console.log(JSON.stringify({{ out, snaps, t: m.minutesToTime(615), top: m.topForMinutes(90, 48) }}));
    }});"""
    p = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    data = json.loads(p.stdout.strip().splitlines()[-1])
    for (_, _, expected), got in zip(DRAG_CASES, data["out"]):
        if expected is None:
            assert got is None
            continue
        for k, v in expected.items():
            assert got[k] == v, (expected, got)
        assert got["timezone"] == "Africa/Johannesburg" and got["ignored_occurrences"] == ["2026-10-30T09:00"]
    assert data["snaps"] == [0, 15, 45, 0, 1425]
    assert data["t"] == "10:15" and data["top"] == 72


def test_frontend_wiring():
    js = (ROOT / "distr/gui/web/static/development/automations/index.js").read_text()
    html = (ROOT / "distr/gui/web/templates/development/automations/workspace.html").read_text()
    assert 'data-scheduled-calendar-mode="day"' in html
    assert "/occurrences/ignore" in js and "showUndoToast" in js and "data-cal-now" in js
    assert "60000" in js and "placeStatusFilter" in js
