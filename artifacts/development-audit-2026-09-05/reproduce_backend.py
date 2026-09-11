"""Read-only audit of application code, using temporary databases and files only."""
import contextlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['DECISIONS_DB_DIR'] = tempfile.mkdtemp(prefix='decisions-audit-db-')
os.environ['DECISIONS_TEST_MODE'] = '1'
runpy.run_path(str(ROOT / 'tests/conftest.py'))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.workflow import planning_workspace as plan
from distr.core.automation import imports
from distr.core.automation import scheduler
from distr.core.db.automation import Automation
from distr.core.db.time import utc_now_naive
from datetime import timedelta
from distr.core.db import Chat
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.workflow import development_control as control

out = []
def result(name, observed):
    out.append({'case': name, 'observed': observed})

with tempfile.TemporaryDirectory(prefix='decisions-audit-fixture-') as temp:
    folder = Path(temp)
    engine = create_engine(f'sqlite:///{folder / "fixture.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    @contextlib.contextmanager
    def session():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    plan.get_session = session
    project_root = folder / 'project'
    project_root.mkdir()
    with session() as db:
        p = Project(name='Audit project', folder_location=str(project_root))
        db.add(p)
        db.commit()
        pid = p.id
    a = plan.ensure_workspace(board_key='decisions:1', board_provider='decisions', board_name='A', project_id=pid)
    b = plan.ensure_workspace(board_key='decisions:2', board_provider='decisions', board_name='B', project_id=pid)
    item = plan.create_item(workspace_id=b['id'], item_type='brief', content='Board B original')
    replaced = plan.apply_instruction(a['id'], item_id=item['id'], instruction='replace with Cross-board replacement')
    assert replaced['item']['workspace_id'] == b['id']
    result('Cross-board replacement bypasses workspace ownership', replaced['item']['content'])
    approved = plan.update_item(item['id'], status='approved')
    changed = plan.update_item(item['id'], content='Changed after approval')
    assert changed['status'] == 'approved'
    result('Content edit retains prior approval', changed['status'])
    first = plan.update_item(item['id'], content='Tab A update')
    second = plan.update_item(item['id'], content='Tab B stale update')
    assert second['content'] == 'Tab B stale update'
    result('Stale tab update overwrites newer content without version token', second['content'])
    (project_root / 'README.md').write_text('# Project\nOriginal repository description')
    discovery = plan.discover_project(a['id'], instruction='Scan project')['item']
    plan.update_item(discovery['id'], content='User curated analysis')
    scan = plan.discover_project(a['id'], instruction='Scan project')['item']
    assert 'User curated analysis' not in scan['content']
    result('Repeated discovery replaces user-curated overview', scan['id'] == discovery['id'])
    generated = plan.apply_instruction(a['id'], instruction='Create a PRD for offline invoices with QR payment')['item']
    assert 'offline invoices' not in generated['content']
    result('Language request loses requested subject in created content', 'template only')
    stable = plan.update_item(item['id'], content='Database baseline')
    with patch('sqlalchemy.orm.Session.commit', side_effect=RuntimeError('Simulated commit failure')):
        try:
            plan.update_item(item['id'], content='File changed before database commit')
        except RuntimeError:
            pass
    disk = Path(stable['file_path']).read_text()
    db_value = next(x for x in plan.get_workspace(b['id'])['items'] if x['id'] == item['id'])['content']
    assert disk != db_value
    result('Commit failure diverges file and database', {'file':disk, 'database':db_value})
    child = project_root / 'nested-app'
    child.mkdir()
    with session() as db:
        nested = Project(name='Nested unrelated app', folder_location=str(child))
        db.add(nested)
        db.commit()
        nested_id = nested.id
    with patch('distr.core.db.get_session', session):
        matched = imports._match_project_id([str(project_root)])
    assert matched == nested_id
    result('Import matches deeper descendant instead of exact cwd', {'matched_nested_project': True})
    schedule = imports.schedule_from_rrule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=9;COUNT=3')
    assert schedule['kind'] == 'weekly' and 'count' not in schedule
    result('RRULE loses two-week interval and finite run count', schedule)
    with session() as db:
        auto = Automation(name='Audit one-shot',instruction='Fixture only',schedule_enabled=True,
                          schedule_preset='once',next_run_at=utc_now_naive()-timedelta(minutes=1))
        db.add(auto)
        db.commit()
        aid = auto.id
    with patch('distr.core.db.get_session',session), patch(
        'distr.core.automation_orchestrator.dispatch_automation_to_current_chat',
        return_value={'status':'failed','summary':'Thread preparation failed','automation_run_id':None}
    ):
        dispatched = scheduler.run_scheduled_automation({'id':f'auto_{aid}','record_id':aid})
    with session() as db:
        auto = db.get(Automation,aid)
        assert dispatched is True and auto.next_run_at is None and not auto.schedule_enabled
        result('One-shot schedule consumed and reported dispatched after failure result',
               {'scheduler_return':dispatched,'enabled':auto.schedule_enabled,'next_run':auto.next_run_at})
    with session() as db:
        chat = Chat(title='Audit thread',params=json.dumps({'development':{'source_type':'prompt'}}))
        db.add(chat)
        db.flush()
        cid = chat.id
        db.add(DevelopmentWorkItem(chat_id=cid,identity_key=f'thread:{cid}',time_paused=False,
                                  time_started_at=utc_now_naive()-timedelta(days=3),
                                  time_last_activity_at=utc_now_naive()-timedelta(days=3)))
        db.commit()
    with patch.object(control,'get_session',session), patch(
        'distr.core.turn_runtime.steer_active_turn',return_value={'accepted':True}
    ) as steer, patch('distr.core.workflow.development_harness.development_execution_active',return_value=False):
        first = control.enqueue_command(cid,'First instruction')
        second = control.enqueue_command(cid,'Second instruction')
        control.dispatch_pending(cid)
        control.dispatch_pending(cid)
        statuses = [x['status'] for x in control.list_commands(cid)]
        assert statuses == ['delivered','queued'] and steer.call_count == 1
        result('Delivered command starves next queued command', {'statuses':statuses,'worker_deliveries':steer.call_count})
        timer = control.thread_time_state(cid)
        assert timer['running'] and timer['seconds'] >= 259200
        result('Idle timer without response never auto-pauses', {'running':timer['running'],'hours':round(timer['seconds']/3600,1)})

rendered = json.dumps(out, indent=2)
(Path(__file__).parent/'backend-evidence.json').write_text(rendered+'\n')
print(rendered)
