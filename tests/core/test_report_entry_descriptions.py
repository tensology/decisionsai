from datetime import datetime, timedelta
import json

from distr.core.reports.entry_descriptions import entry_key, apply_descriptions
from distr.core.reports.month import combine_duplicate_runs, _union_seconds


def block(source='pi_decisions', ref='execution:12', start=None, end=None, instruction='exact'):
    start = start or datetime(2026, 10, 27, 8)
    return {'source':source,'ref':ref,'family':'pi','project_id':7,'title':'Done and committed',
            'instruction_hash':instruction,'start':start,'end':end or start+timedelta(minutes=5)}


def test_same_run_is_one_entry_with_both_sources_and_unchanged_clock_time():
    execution = block()
    local = block('pi_local','pi_local:original',end=execution['end']-timedelta(seconds=1))
    rows = combine_duplicate_runs([execution,local])
    assert len(rows) == 1
    assert rows[0]['ref'] == 'execution:12'
    assert rows[0]['related_refs'] == ['pi_local:original']
    assert rows[0]['thread_ref'] == 'pi_local:original'
    assert rows[0]['description_key'] == entry_key(execution)
    assert _union_seconds([(r['start'],r['end']) for r in rows]) == _union_seconds([(r['start'],r['end']) for r in [execution,local]])


def test_matching_title_or_time_is_not_enough_to_merge_work():
    execution=block()
    different=block('pi_local','pi_local:other',instruction='different')
    assert len(combine_duplicate_runs([execution,different])) == 2
    later=block('pi_local','pi_local:later',start=execution['start']+timedelta(hours=1))
    assert len(combine_duplicate_runs([execution,later])) == 2
    wrong_project=block('pi_local','pi_local:wrong')
    wrong_project['project_id']=8
    assert len(combine_duplicate_runs([execution,wrong_project])) == 2
    brief=block(end=execution['start']+timedelta(seconds=2))
    separate=block('pi_local','pi_local:separate',start=execution['start']+timedelta(seconds=3),end=execution['start']+timedelta(seconds=5))
    assert len(combine_duplicate_runs([brief,separate])) == 2


def test_ambiguous_source_matches_are_kept_separate():
    rows=[block(),block('pi_local','pi_local:a'),block('pi_local','pi_local:b')]
    assert len(combine_duplicate_runs(rows)) == 3


def test_description_projection_changes_words_not_recorded_identity_or_time(tmp_path):
    original=block()
    key=entry_key(original)
    path=tmp_path/'descriptions.json'
    path.write_text(json.dumps({'schema':1,'entries':{key:{'description':'Added per-thread cost tracking and a visible task progress indicator.','status':'ready','start':'2026-10-27'}}}))
    result=apply_descriptions([dict(original)],path=path)[0]
    assert result['title'].startswith('Added per-thread')
    assert result['recorded_title'] == 'Done and committed'
    assert (result['source'],result['ref'],result['start'],result['end'],result['project_id']) == (original['source'],original['ref'],original['start'],original['end'],original['project_id'])


def test_missing_or_conflicting_evidence_is_never_silently_marked_invoice_ready(tmp_path):
    original=block()
    original['related_description_keys']=['companion']
    path=tmp_path/'descriptions.json'
    path.write_text(json.dumps({'schema':1,'entries':{entry_key(original):{'description':'Updated the catalogue cache failure handling.','status':'ready','start':'2026-10-23'},'companion':{'description':'Review required: completion evidence conflicts.','status':'needs_review','start':'2026-10-23'}}}))
    rows=apply_descriptions([original,block(ref='execution:missing')],path=path)
    assert all(row['description_status']=='needs_review' for row in rows)
    assert rows[0]['title'].startswith('Review required:')
    assert rows[1]['title']=='Done and committed'


def test_old_review_keeps_new_work_description_without_approving_it(tmp_path):
    path=tmp_path/'descriptions.json'
    path.write_text(json.dumps({'schema':1,'entries':{'old':{
        'start':'2026-10-23','description':'Corrected registration validation.','status':'ready'}}}))
    original=block(start=datetime(2026,11,10,12))
    original['title']='Added per-byte billing support to the device settings.'
    result=apply_descriptions([dict(original)],path=path)[0]
    assert result['title']==original['title']
    assert result['recorded_title']==original['title']
    assert result['description_status']=='needs_review'
    assert result['start']==original['start'] and result['end']==original['end']


def test_cache_key_is_stable_across_serialisation_and_timezone_formatting():
    original=block()
    serialised={**original,'start':original['start'].isoformat()+'Z','end':original['end'].isoformat()+'Z'}
    assert entry_key(serialised)==entry_key(original)


def test_growing_interval_keeps_its_known_description_but_requires_a_fresh_review(tmp_path):
    original=block()
    path=tmp_path/'descriptions.json'
    path.write_text(json.dumps({'schema':1,'entries':{entry_key(original):{
        'description':'Improved report descriptions and linked their original work evidence.','status':'ready',
        'source':original['source'],'ref':original['ref'],'start':'2026-10-27T10:00:00','end':'2026-10-27T10:05:00'}}}))
    changed={**original,'end':original['end']+timedelta(minutes=3)}
    result=apply_descriptions([changed],path=path)[0]
    assert result['title'].startswith('Improved report')
    assert result['description_status']=='needs_review'
    assert result['description_asof']=='2026-10-27T10:05:00'
    assert result['end']==changed['end']


def test_batch_validator_rejects_status_only_and_internal_commit_metadata():
    import pytest
    from scripts.rewrite_report_descriptions import validate
    with pytest.raises(ValueError):
        validate([{'id':'x','description':'Done and committed abc1234 to main.','status':'ready'}],[{'id':'x'}])
    with pytest.raises(ValueError):
        validate([],[{'id':'x'}])
    valid=validate([{'id':'x','description':'Added persistent per-thread cost tracking and visible task progress.','status':'ready'}],[{'id':'x'}])
    assert len(valid)==1
