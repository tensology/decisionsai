"""Plan artifacts keep scope and provenance when becoming implementation tasks."""
import json
from datetime import datetime

import pytest

from distr.core.db import Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import PlanItem, PlanItemRevision, PlanFileWrite
from distr.core.planning import build, service
from tests.core.test_plan_build import store, task


def test_archived_documents_do_not_poison_live_plan_build(store):
    with store() as db:
        db.add(PlanItem(id=3,workspace_id=1,title='Old discarded draft',status='archived',content='No longer source'))
        db.add(PlanItemRevision(item_id=3,revision=1,title='Old discarded draft'))
        db.add(PlanFileWrite(item_id=3,target_path='/unused/archive.md',desired_hash='old'))
        db.commit()
    assert build.build_tasks(1,tasks=[task()],expected_revisions={'1':1})['created']==1
    with pytest.raises(ValueError,match='Missing expected revisions'):
        build.build_tasks(1,tasks=[task(source_item_ids=[3])],expected_revisions={'1':1})


@pytest.mark.parametrize('removed',['board','project'])
def test_graph_build_rejects_inactive_ownership(store,removed):
    with store() as db:
        if removed=='board':db.get(KanbanBoard,1).archived=True
        else:db.get(Project,1).deleted_at=datetime.now()
        db.commit()
    with pytest.raises(ValueError,match='consistency'):
        build.build_tasks(1,tasks=[task()],expected_revisions={'1':1})
    with store() as db:assert db.query(KanbanTicket).count()==0


def test_build_provenance_uses_selected_plan_chat_not_first_chat(store):
    with store() as db:
        db.add(Chat(id=41,project_id=1,autonomy_level='plan',params=json.dumps({'plan_workspace_id':1})))
        db.add(Chat(id=42,project_id=1,autonomy_level='plan',params=json.dumps({'plan_workspace_id':1})))
        db.commit()
    result=build.build_tasks(1,tasks=[task()],expected_revisions={'1':1},source_chat_id=42)
    with store() as db:
        ticket=db.get(KanbanTicket,result['tasks'][0]['id'])
        assert ticket.source_thread_id=='42'
        assert ticket.source_chat_id is None  # Planning provenance does not claim implementation ownership.


@pytest.mark.parametrize('chat_project,chat_workspace,archived',[(2,1,False),(1,2,False),(1,1,True)])
def test_build_rejects_foreign_or_archived_plan_chat(store,chat_project,chat_workspace,archived):
    with store() as db:
        db.add(Chat(id=42,project_id=chat_project,autonomy_level='plan',is_archived=archived,
                    params=json.dumps({'plan_workspace_id':chat_workspace})))
        db.commit()
    with pytest.raises(ValueError,match='outside this workspace'):
        build.build_tasks(1,tasks=[task()],expected_revisions={'1':1},source_chat_id=42)


def test_legacy_build_reuses_ticket_after_lane_move(store,monkeypatch):
    monkeypatch.setattr(service,'get_session',store)
    with store() as db:
        db.get(PlanItem,1).status='approved'
        db.add(KanbanLane(id=3,board_id=1,name='Done'))
        db.commit()
    assert service.build_approved_tickets(1)['created']==['Wireframe']
    with store() as db:
        ticket=db.query(KanbanTicket).one();ticket.lane_id=3
        db.commit()
    assert service.build_approved_tickets(1)['reused']==['Wireframe']
    with store() as db:assert db.query(KanbanTicket).count()==1


@pytest.mark.parametrize('removed',['board','project'])
def test_legacy_build_rejects_inactive_ownership(store,monkeypatch,removed):
    monkeypatch.setattr(service,'get_session',store)
    with store() as db:
        db.get(PlanItem,1).status='approved'
        if removed=='board':db.get(KanbanBoard,1).archived=True
        else:db.get(Project,1).deleted_at=datetime.now()
        db.commit()
    with pytest.raises((LookupError,ValueError)):
        service.build_approved_tickets(1)
    with store() as db:assert db.query(KanbanTicket).count()==0
