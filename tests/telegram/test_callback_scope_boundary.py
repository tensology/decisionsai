"""Relay button tokens must never be interpreted as another command or text."""
import json
import threading
from core.test_telegram_checkpoint_scope import checkpoint,intake
from distr.core.db.workflow import AutoWorkflowRun
from distr.core.workflow import interactions
from .test_telegram_delivery_stability import DummyTelegramMessages


def test_callback_token_wins_over_unrelated_text(checkpoint):
    session,item,scope,next_step,ids=checkpoint
    with session() as db:
        other=AutoWorkflowRun(workflow_id=scope['workflow_id'],current_step_id=next_step,status='waiting',
            run_data=json.dumps({'project_id':ids[1],'waiting_kind':'approval'}))
        db.add(other);db.commit();other_id=other.id
    other_control=interactions.create_workflow_interaction(workflow_id=scope['workflow_id'],run_id=other_id,
        step_id=next_step,kind='approval',telegram_chat_id=123)
    manager=DummyTelegramMessages()
    previous=set(threading.enumerate())
    manager._handle_telegram_message({'data':{'message_id':1101,'chat_id':123,'from':{'id':123},
        'callback_data':f"wf:{item['token']}:stop",'text':f"wf:{other_control['token']}:stop"}})
    # Callback application is deferred by the actual manager boundary.
    for worker in threading.enumerate():
        if worker not in previous and worker.name.startswith('workflow-interaction-'):
            worker.join(timeout=5);assert not worker.is_alive()
    with session() as db:
        assert db.get(AutoWorkflowRun,scope['run_id']).status=='cancelled'
        assert db.get(AutoWorkflowRun,other_id).status=='waiting'
    assert manager.enqueued==[]


def test_malformed_button_never_becomes_stop_for_other_pending_run(checkpoint):
    session,_,scope,_,_=checkpoint
    manager=DummyTelegramMessages()
    manager._handle_telegram_message({'data':{'message_id':1102,'chat_id':123,'from':{'id':123},
        'callback_data':'wf:old-token:stop:unsupported'}})
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'
    assert manager.enqueued==[]
    assert any('invalid' in line.lower() for line in manager.sent)


def test_unrecognized_callback_never_becomes_typed_stop(checkpoint):
    session,_,scope,_,_=checkpoint
    manager=DummyTelegramMessages()
    manager._handle_telegram_message({'data':{'message_id':1103,'chat_id':123,'from':{'id':123},
        'callback_data':'legacy:stop'}})
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'
    assert manager.enqueued==[]
    assert any('no longer available' in line.lower() for line in manager.sent)
