"""Telegram command targets must be explicit, stable and current at dispatch."""
from core.test_proposal_continuity_integration import intake
from distr.core.db.projects import Project
from distr.core.integrations.telegram import project_control
from types import SimpleNamespace
import pytest


class Manager:
    def __init__(self):self.sent=[]
    def send_to_telegram(self,text):self.sent.append(text)


def test_ambiguous_named_project_never_selects_shortest_match(intake):
    session,_,ids=intake
    with session() as db:
        db.get(Project,ids[0]).name='Website production'
        db.get(Project,ids[1]).name='Website staging';db.commit()
    queued=[]
    class DeferredThread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):queued.append(self)
    manager=Manager()
    assert project_control.handle_project_control_message(manager,'codex in the Website project to inspect configuration',start_thread=DeferredThread)
    assert queued==[],manager.sent


def test_no_active_project_cannot_fall_back_to_last_modified(intake):
    _,_,_=intake
    queued=[]
    class DeferredThread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):queued.append(self)
    manager=Manager()
    assert project_control.handle_project_control_message(manager,'codex inspect configuration',start_thread=DeferredThread)
    assert queued==[],manager.sent


def test_delayed_dispatch_rejects_changed_folder_before_backend(intake,monkeypatch,tmp_path):
    session,_,ids=intake
    queued=[];calls=[]
    class DeferredThread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):queued.append(self)
    async def backend(project,*args,**kwargs):
        calls.append((project.id,project.folder_location))
        raise AssertionError('A changed target must not execute')
    monkeypatch.setattr('distr.core.project_cli_backends.run_project_task',backend)
    monkeypatch.setattr('distr.core.ide_bridge.record_ide_event',lambda **kwargs:{})
    manager=Manager()
    assert project_control.handle_project_control_message(manager,'codex in the First project to inspect configuration',start_thread=DeferredThread)
    assert len(queued)==1
    replacement=tmp_path/'replacement';replacement.mkdir()
    with session() as db:
        db.get(Project,ids[0]).folder_location=str(replacement);db.commit()
    queued[0].kwargs['target']()
    assert calls==[],manager.sent


@pytest.mark.parametrize('bad_board',['missing','archived'])
def test_invalid_project_board_never_reaches_provider(intake,bad_board):
    from distr.core.db.kanban import KanbanBoard
    session,_,ids=intake
    with session() as db:
        if bad_board=='archived':
            board=KanbanBoard(name='Archived board',archived=True,default_project_id=ids[0]);db.add(board);db.flush()
            board_id=board.id
        else:board_id=99991
        db.get(Project,ids[0]).kanban_board_id=board_id;db.commit()
    queued=[]
    class DeferredThread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):queued.append(self)
    manager=Manager()
    assert project_control.handle_project_control_message(manager,'codex in the First project to inspect configuration',start_thread=DeferredThread)
    assert not queued
    assert 'missing or archived' in manager.sent[-1]


@pytest.mark.parametrize('changed',[False,True])
def test_shared_board_is_valid_but_changed_default_invalidates_delayed_dispatch(intake,monkeypatch,changed):
    from distr.core.db.kanban import KanbanBoard
    session,_,ids=intake
    with session() as db:
        board=KanbanBoard(name='Shared board',default_project_id=ids[1]);db.add(board);db.flush()
        db.get(Project,ids[0]).kanban_board_id=board.id;db.commit();board_id=board.id
    queued=[];calls=[]
    class DeferredThread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):queued.append(self)
    async def backend(project,*args,**kwargs):
        calls.append((project.id,project.folder_location,kwargs['board_id_override']))
        return SimpleNamespace(success=True,output='Scoped result',error='')
    monkeypatch.setattr('distr.core.project_cli_backends.run_project_task',backend)
    monkeypatch.setattr('distr.core.ide_bridge.record_ide_event',lambda **kwargs:{'session':{}})
    manager=Manager()
    project_control.handle_project_control_message(manager,'codex in the First project to inspect configuration',start_thread=DeferredThread)
    assert len(queued)==1
    if changed:
        with session() as db:db.get(KanbanBoard,board_id).default_project_id=ids[0];db.commit()
    queued[0].kwargs['target']()
    assert len(calls)==(0 if changed else 1),manager.sent
    if calls:
        assert calls[0][0]==ids[0] and calls[0][2]==board_id
