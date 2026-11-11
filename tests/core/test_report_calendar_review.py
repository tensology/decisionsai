import json
from copy import deepcopy

from distr.core.reports.calendar_review import apply_review, union_seconds, stamp


def fixture(tmp_path):
    data = {'schema': 1, 'period': {'start': '2026-10-23', 'end': '2026-11-09'},
            'reviewed_through': '2026-11-09T12:00:00Z', 'project_map': {'2': 175, '7': 194},
            'calendar': [
                {'id': 1, 'project_id': 175, 'title': 'Corrected registration validation and verified recovery.',
                 'start_at': '2026-10-26T10:00:00Z', 'end_at': '2026-10-26T10:20:00Z',
                 'billable': True, 'billing_excluded': False},
                {'id': 2, 'project_id': 194, 'title': 'Reorganised device schedules and verified the layout.',
                 'start_at': '2026-10-26T11:00:00Z', 'end_at': '2026-10-26T11:30:00Z',
                 'billable': False, 'billing_excluded': False}],
            'unbilled_event_ids': [1], 'evidence': {'1': {'ref': 'pi_local:exact-thread', 'family': 'pi',
                  'source_start': '2026-10-26T08:00:00Z', 'source_end': '2026-10-26T10:20:00Z'}}, 'holds': []}
    path = tmp_path/'calendar-review.json';path.write_text(json.dumps(data))
    snapshot = {'period': data['period'], 'project_ids': [], 'totals': {}, 'by_family': [
        {'family': 'pi', 'label': 'Pi', 'seconds': 0}], 'projects': [{'id': 2, 'name': 'Merrypak'}, {'id': 7, 'name': 'Spoortrack [old]'}],
        'blocks': [
            {'id': 'raw-overlap', 'project_id': 2, 'project_name': 'Merrypak', 'family': 'pi',
             'start': '2026-10-26T08:00:00Z', 'end': '2026-10-26T12:00:00Z', 'seconds': 14400},
            {'id': 'other-project', 'project_id': 9, 'project_name': 'Internal', 'family': 'pi',
             'start': '2026-10-26T15:00:00Z', 'end': '2026-10-26T15:30:00Z', 'seconds': 1800}]}
    return data,path,snapshot


def test_native_calendar_replaces_stale_run_envelope_without_mutating_evidence(tmp_path):
    data,path,snapshot = fixture(tmp_path);original=deepcopy(snapshot)
    result=apply_review(snapshot,path=path)
    assert snapshot==original
    assert [r['id'] for r in result['blocks']]==['calendar-1','calendar-2','other-project']
    assert result['totals']['seconds']==4800
    block=result['blocks'][0]
    assert block['seconds']==1200
    assert block['source_start']=='2026-10-26T08:00:00Z'
    assert block['ref']=='pi_local:exact-thread'
    assert result['by_project'][2]['seconds']==1200


def test_invoice_view_and_project_selection_match_unbilled_native_rows(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    snapshot['invoice_only']=True;snapshot['project_ids']=[2]
    snapshot['blocks']=snapshot['blocks'][:1]
    result=apply_review(snapshot,path=path)
    assert len(result['blocks'])==1
    assert result['blocks'][0]['calendar_event_id']==1
    assert result['totals']['seconds']==1200
    data['unbilled_event_ids']=[];path.write_text(json.dumps(data))
    assert apply_review(snapshot,path=path)['totals']['seconds']==0


def test_new_work_after_explicit_cutoff_survives_for_fresh_review(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    snapshot['blocks']=[{'id':'new','project_id':2,'project_name':'Merrypak','family':'pi',
                         'start':'2026-11-09T13:00:00Z','end':'2026-11-09T13:20:00Z'}]
    result=apply_review(snapshot,path=path)
    assert any(r['id']=='new' for r in result['blocks'])


def test_source_parts_outside_audit_window_are_not_lost(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    snapshot['period']={'start':'2026-10-01','end':'2026-11-09'}
    snapshot['blocks']=[{'id':'old','project_id':2,'project_name':'Merrypak','family':'pi',
                        'start':'2026-10-22T21:45:00Z','end':'2026-10-22T22:15:00Z'}]
    result=apply_review(snapshot,path=path)
    part=next(r for r in result['blocks'] if r['id']=='old')
    assert part['seconds']==900 and part['end']=='2026-10-22T22:00:00Z'


def test_exact_time_union_does_not_add_overlaps_gaps_or_rounding():
    spans=[(stamp('2026-10-23T08:00:00.100000Z'),stamp('2026-10-23T08:10:00Z')),
           (stamp('2026-10-23T08:05:00Z'),stamp('2026-10-23T08:10:00Z')),
           (stamp('2026-10-23T10:00:00Z'),stamp('2026-10-23T10:05:00.200000Z'))]
    assert abs(union_seconds(spans)-900.1)<0.000001


def test_calendar_only_project_and_review_hold_remain_visible_without_new_coding_project(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    data['project_map']['1000183']=183
    data['calendar'][0]['project_id']=183
    data['calendar'][0]['billable']=False
    data['evidence']['1'].update(project_name='Crystal Logic',timing_basis='Confirmed meeting attendance.',
                                description_status='needs_review',invoice_reason='Implementation remains under review.')
    path.write_text(json.dumps(data))
    result=apply_review(snapshot,path=path)
    block=next(b for b in result['blocks'] if b.get('calendar_event_id')==1)
    assert block['project_id']==1000183
    assert block['project_name']=='Crystal Logic'
    assert block['description_status']==block['invoice_status']=='needs_review'
    assert block['invoice_reason']=='Implementation remains under review.'
    assert block['timing_basis']=='Confirmed meeting attendance.'
    assert {'id':1000183,'name':'Crystal Logic'} in result['projects']


def test_synchronized_calendar_scope_does_not_mix_internal_or_unreviewed_raw_time(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    data['scope']='synchronized_calendar';path.write_text(json.dumps(data))
    snapshot['project_ids']=[2,7,9]
    snapshot['blocks'].append({'id':'fresh-internal','project_id':9,'family':'pi',
        'start':'2026-11-09T13:00:00Z','end':'2026-11-09T13:02:00Z'})
    original=deepcopy(snapshot)
    result=apply_review(snapshot,path=path)
    assert snapshot==original
    assert {r['calendar_event_id'] for r in result['blocks']}=={1,2}
    assert result['totals']['seconds']==3000
    assert result['calendar_review']['scope']=='synchronized_calendar'
    assert all(b['seconds']>=900 for b in result['blocks'])
    assert result['sources'][0]['blocks']==2
    assert result['by_family'][0]['blocks']==1  # One Pi row; the manual row has a different family.


def test_synchronized_calendar_scope_keeps_unselected_projects_out(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    data['scope']='synchronized_calendar';path.write_text(json.dumps(data))
    snapshot['project_ids']=[9]
    snapshot['blocks']=snapshot['blocks'][1:]
    result=apply_review(snapshot,path=path)
    assert result['blocks']==[] and result['totals']['seconds']==0


def test_synchronized_calendar_scope_preserves_raw_time_outside_its_period(tmp_path):
    data,path,snapshot=fixture(tmp_path)
    data['scope']='synchronized_calendar';path.write_text(json.dumps(data))
    snapshot['period']={'start':'2026-10-01','end':'2026-11-09'}
    snapshot['blocks']=[{'id':'internal-old','project_id':9,'project_name':'Internal','family':'pi',
                        'start':'2026-10-22T21:45:00Z','end':'2026-10-22T22:15:00Z'}]
    result=apply_review(snapshot,path=path)
    old=next(b for b in result['blocks'] if b['id']=='internal-old')
    assert old['seconds']==900 and old['end']=='2026-10-22T22:00:00Z'
