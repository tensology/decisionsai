import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import pytest
from PyQt6 import QtCore, QtWidgets, QtTest
from distr.gui import development_hot_corner as panel_module
from tests.gui.test_development_hot_corner import _app


@pytest.fixture
def panel(monkeypatch, tmp_path):
    app = _app()
    preferences = QtCore.QSettings(str(tmp_path/'preferences.ini'), QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr(QtCore, 'QSettings', lambda *args: preferences)
    group = {'project_id':9, 'board_key':'decisions:18', 'name':'DecisionsAI', 'threads':[], 'weekly_seconds':0}
    monkeypatch.setattr(panel_module, 'load_development_groups', lambda provider:[group])
    widget = panel_module.DevelopmentHotCornerPanel(enabled=False, pinned_project_ids=[])
    widget.resize(400, 720)
    widget._proposals = [{'id':'local:proposal-test', 'title':'Check the proposal execution handoff', 'project_id':9, 'project_name':'DecisionsAI'}]
    widget._refresh_threads()
    widget._section_rows['proposals'].setExpanded(True)
    widget.show()
    app.processEvents()
    yield widget, group
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_proposals_are_last_expandable_with_project_badge(panel):
    widget, _ = panel
    section=widget._section_rows['proposals']
    assert widget.tree.topLevelItem(widget.tree.topLevelItemCount()-1) is section
    assert widget.findChild(QtWidgets.QLabel,'proposalProjectBadge').text() == 'DecisionsAI'
    index=QtCore.QPersistentModelIndex(widget.tree.indexFromItem(section))
    widget._toggle_section(index,'proposals')
    assert not section.isExpanded()
    widget._refresh_threads()
    assert not widget._section_rows['proposals'].isExpanded()


def test_create_starts_once_and_moves_thread_into_project_only_on_success(panel, monkeypatch):
    widget, group=panel
    jobs=[]
    monkeypatch.setattr(widget,'_run_proposal_task',lambda operation,callback:jobs.append((operation,callback)))
    opened=[]
    widget._open_path=opened.append
    button=widget.findChildren(QtWidgets.QPushButton)[0]
    button.click()
    button=next(b for b in widget.findChildren(QtWidgets.QPushButton) if b.isVisible())
    assert button.text() == 'Starting...'
    assert not button.isEnabled()
    widget._start_proposal(widget._proposals[0])
    assert len(jobs)==1
    assert len(widget._proposals)==1
    jobs[0][1](None,'Execution could not start')
    assert len(widget._proposals)==1
    assert widget._proposal_error == 'Execution could not start'
    widget._start_proposal(widget._proposals[0])
    group['threads']=[{'chat_id':42,'title':'Check the proposal execution handoff','elapsed_seconds':0,'active':True,'time_running':True}]
    jobs[-1][1](42,None)
    assert widget._proposals == []
    assert widget._selected_chat_id == 42
    assert widget._project_rows[0][0].isExpanded()
    assert opened == ['/development/threads/42/']


def test_loading_proposals_does_not_block_gui_and_failure_retains_rows(panel, monkeypatch):
    widget,_=panel
    gate=threading.Event()
    monkeypatch.setattr(panel_module,'load_desktop_proposals', lambda: (gate.wait(2) and {'proposals':[]}) or {})
    start=time.monotonic()
    widget._refresh_proposals()
    assert time.monotonic()-start < .2
    assert widget._proposal_loading
    assert len(widget._proposals)==1
    gate.set()
    deadline=time.monotonic()+3
    while widget._proposal_loading and time.monotonic()<deadline:
        _app().processEvents()
        QtTest.QTest.qWait(10)
    assert not widget._proposal_loading
    widget._proposals=[{'id':'keep','title':'Keep proposal','project_id':9,'project_name':'DecisionsAI'}]
    widget._proposals_loaded(None,'Source offline')
    assert widget._proposals[0]['id']=='keep'


def test_unassigned_proposal_cannot_start_in_another_project(panel):
    widget,_=panel
    widget._proposals=[{'id':'unassigned','title':'Missing project'}]
    widget._render_proposals()
    button=next(b for b in widget.findChildren(QtWidgets.QPushButton) if b.isVisible())
    assert not button.isEnabled()
    assert 'Assign' in button.toolTip()


def test_background_refresh_finishes_safely_after_panel_is_destroyed(monkeypatch):
    app=_app()
    monkeypatch.setattr(panel_module,'load_development_groups',lambda provider:[])
    widget=panel_module.DevelopmentHotCornerPanel(enabled=False,pinned_project_ids=[])
    gate=threading.Event()
    widget._run_proposal_task(lambda: gate.wait(2), lambda *args: pytest.fail('Destroyed panel received callback'))
    future=next(iter(widget._proposal_tasks))
    widget.deleteLater()
    app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    gate.set()
    deadline=time.monotonic()+2
    while not future.done() and time.monotonic()<deadline:
        QtTest.QTest.qWait(10)
    assert future.done()
    app.processEvents()


@pytest.mark.parametrize('width,height',[(400,900),(390,600)])
def test_native_proposal_visual_states(panel, width, height):
    from pathlib import Path
    widget,_=panel
    folder=Path('/tmp/decisions-native-proposals')
    folder.mkdir(exist_ok=True)
    widget.resize(width,height)
    widget._proposals[0]['title']='Check the database refresh and confirm the execution uses the correct project'
    widget._render_proposals()
    def capture(name):
        _app().processEvents()
        assert widget.grab().save(str(folder/f'{name}-{width}x{height}.png'))
    capture('expanded')
    widget._section_rows['proposals'].setExpanded(False)
    capture('collapsed')
    widget._section_rows['proposals'].setExpanded(True)
    widget._proposal_busy={'local:proposal-test'}
    widget._render_proposals()
    capture('starting')
    widget._proposal_busy.clear()
    widget._proposal_error='The execution service is temporarily unavailable. Your proposal is still here. Retry Create thread.'
    widget._render_proposals()
    capture('error')
    widget._proposal_error=''
    widget._proposals=[]
    widget._render_proposals()
    capture('empty')
    widget._proposals=[{'id':'unlinked','title':'A request that needs a project before it can run'}]
    widget._render_proposals()
    capture('unlinked')
