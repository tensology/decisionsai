from datetime import datetime, timedelta
import json

from distr.core.reports.entry_descriptions import entry_key, apply_descriptions
from distr.core.reports.entry_reviews import apply_project_reviews, apply_invoice_reviews


def block(start=None):
    start = start or datetime(2026, 10, 23, 12)
    return {'source': 'pi_local', 'ref': 'pi_local:reused', 'family': 'pi',
            'title': 'Generic closing status', 'project_id': 0,
            'start': start, 'end': start + timedelta(minutes=10)}


def review(**changes):
    return {'project_id': 21, 'description': 'Corrected duplicate email attachment imports and verified the mail service.',
            'invoice_status': 'ready', 'reason': 'MailShot source edits and successful verification.',
            'reviewed_at': '2026-11-09T10:00:00Z', 'evidence': [{'id': 'pi_local:reused#L10', 'quote': 'success'}],
            **changes}


def store(tmp_path, b, row):
    path = tmp_path / 'reviews.json'
    path.write_text(json.dumps({'schema': 1, 'entries': {entry_key(b): row}}))
    return path


def test_ownership_is_specific_to_one_interval_in_a_reused_ide_thread(tmp_path):
    first, later = block(), block(datetime(2026, 10, 24, 12))
    path = store(tmp_path, first, review())
    result = apply_project_reviews([first, later], {21: {}}, path=path)
    assert [b['project_id'] for b in result] == [21, 0]
    assert result[0]['project_provenance'] == 'reviewed_source_evidence'


def test_review_is_not_overwritten_by_stale_generated_description(tmp_path):
    original = block()
    path = store(tmp_path, original, review())
    result = apply_invoice_reviews([original], {21: {}}, path=path)[0]
    apply_descriptions([result], path=tmp_path / 'absent.json')
    assert result['title'] == review()['description']
    assert result['description_status'] == result['invoice_status'] == 'ready'
    assert (result['start'], result['end'], result['ref']) == (original['start'], original['end'], original['ref'])


def test_unsupported_or_personal_time_has_explicit_non_billable_disposition(tmp_path):
    original = block()
    path = store(tmp_path, original, review(project_id=0, invoice_status='non_billable',
        description='The session contains only a greeting; no client work was performed.'))
    result = apply_invoice_reviews([original], {}, path=path)[0]
    assert result['invoice_status'] == 'non_billable'
    assert result['description_status'] == 'ready'
    assert result['project_id'] == 0


def test_partial_stale_or_overlapping_allocations_never_drop_or_add_time(tmp_path):
    for bounds in [('12:00', '12:05'), ('12:00', '12:11'), ('11:59', '12:10')]:
        original = block()
        path = store(tmp_path, original, review(segments=[{
            'start': '2026-10-23T' + bounds[0] + ':00Z',
            'end': '2026-10-23T' + bounds[1] + ':00Z'}]))
        rows = apply_invoice_reviews([original], {21: {}}, path=path)
        assert len(rows) == 1 and rows[0]['invoice_status'] == 'needs_review'
        assert (rows[0]['start'], rows[0]['end']) == (original['start'], original['end'])


def test_evidenced_task_switch_preserves_the_full_clock_and_separate_projects(tmp_path):
    original = block()
    path = store(tmp_path, original, review(segments=[
        {'start': '2026-10-23T12:00:00Z', 'end': '2026-10-23T12:04:00Z', 'project_id': 21},
        {'start': '2026-10-23T12:04:00Z', 'end': '2026-10-23T12:10:00Z', 'project_id': 22,
         'description': 'Corrected the D3 assistant account permissions and verified permitted account access.'}]))
    rows = apply_invoice_reviews([original], {21: {}, 22: {}}, path=path)
    assert [r['project_id'] for r in rows] == [21, 22]
    assert sum((r['end'] - r['start']).total_seconds() for r in rows) == 600
    assert rows[0]['end'] == rows[1]['start']
    assert all(r['review_parent_key'] == entry_key(original) for r in rows)


def test_future_manual_project_choice_wins_and_invalidates_invoice_readiness(tmp_path):
    original = {**block(), 'project_id': 22, 'manual_project_override': True}
    path = store(tmp_path, original, review())
    result = apply_invoice_reviews([original], {21: {}, 22: {}}, path=path)[0]
    assert result['project_id'] == 22 and result['invoice_status'] == 'needs_review'


def test_missing_project_or_missing_evidence_cannot_be_made_invoice_ready(tmp_path):
    original = block()
    for row in [review(project_id=999), review(project_id=0), review(evidence=[]), review(reviewed_at='')]:
        path = store(tmp_path, original, row)
        result = apply_invoice_reviews([original], {21: {}}, path=path)[0]
        assert result == original


def test_invoice_build_excludes_non_billable_and_unreviewed_time_but_keeps_calendar(monkeypatch):
    from distr.core.reports import month
    calls = []
    whole = {'blocks': [{'ref': 'all-activity'}], 'sources': [], 'invoice_summary': {'non_billable': 2, 'needs_review': 1}}
    eligible = {'blocks': [{'ref': 'reviewed-work'}], 'sources': [], 'invoice_summary': whole['invoice_summary']}

    def collate(*args, **kwargs):
        calls.append(kwargs.get('invoice_only', False))
        return dict(eligible if kwargs.get('invoice_only') else whole)

    monkeypatch.setattr(month, 'collate', collate)
    monkeypatch.setattr(month, 'report_route_verdict', lambda *a: {
        'blocked_unverified_label': False, 'selected': {'provider': 'openrouter', 'model': 'fixture'},
        'configured': {'provider': 'openrouter', 'model': 'fixture'}})
    monkeypatch.setattr(month, 'run_analysis', lambda s, **kw: {'summary': s['blocks'][0]['ref'], 'notes': []})
    monkeypatch.setattr(month, 'save_report', lambda s, a, **kw: {'blocks': s['blocks'], 'analysis': a})
    monkeypatch.setattr(month, 'safe_period_costs', lambda *a: {})
    result = month.build_period_report(month.month_period('2026-10'), None)
    assert calls == [True, False]
    assert result['blocks'] == whole['blocks']
    assert result['saved']['blocks'] == eligible['blocks']
    assert 'Excluded 3' in result['saved']['analysis']['notes'][-1]


def test_invoice_eligibility_never_infers_billable_work_from_timer_or_project_alone():
    from distr.core.reports.month import invoice_eligible
    ready = {'project_id': 21, 'description_status': 'ready'}
    assert invoice_eligible(ready)
    assert not invoice_eligible({'project_id': 21})
    assert not invoice_eligible({**ready, 'project_id': 0})
    assert not invoice_eligible({**ready, 'invoice_status': 'non_billable'})
    assert not invoice_eligible({**ready, 'invoice_status': 'needs_review'})


def test_billing_policy_preserves_non_billable_configuration_and_zero_rate_tracking(tmp_path):
    from distr.core.reports.entry_reviews import apply_billing_policy
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({'schema': 1, 'project_billing': {
        '9': {'mode': 'non_billable', 'checked_at': '2026-11-09', 'basis': 'Production setting is non-billable.'},
        '19': {'mode': 'zero_rate', 'checked_at': '2026-11-09', 'basis': 'Standing rule: tracked at R0.'},
        '22': {'mode': 'unknown', 'checked_at': '2026-11-09', 'basis': 'Client mapping is missing.'}}}))
    rows = [{'project_id': pid, 'description_status': 'ready', 'invoice_status': 'ready', 'seconds': 61}
            for pid in (9, 19, 22)]
    result = apply_billing_policy(rows, path=path)
    assert [r['invoice_status'] for r in result] == ['non_billable', 'zero_rate', 'needs_review']
    assert result[1]['invoice_rate_override'] == 0 and result[1]['invoice_strikethrough'] is True
    assert all(r['seconds'] == 61 for r in result)


def test_source_viewer_uses_only_the_exact_reviewed_interval_owner(tmp_path):
    from distr.core.reports.entry_reviews import reviewed_source_project
    b = block()
    row = review(ref=b['ref'], source_refs=[b['ref']], start=b['start'].isoformat()+'Z', end=b['end'].isoformat()+'Z')
    path = store(tmp_path, b, row)
    assert reviewed_source_project(b['ref'], b['start'], b['end'], {21: {}}, path=path) == 21
    assert reviewed_source_project(b['ref'], b['start'], b['end']+timedelta(seconds=1), {21: {}}, path=path) is None


def test_routine_billing_decision_is_exact_interval_and_preserves_evidenced_work(tmp_path):
    from distr.core.reports.entry_reviews import apply_billing_policy
    original = {**block(), 'project_id': 21, 'description_status': 'ready', 'invoice_status': 'ready'}
    later = {**block(datetime(2026, 10, 24, 12)), 'project_id': 21, 'description_status': 'ready', 'invoice_status': 'ready'}
    path = tmp_path / 'policy.json'
    decision = {'mode': 'non_billable', 'decided_at': '2026-11-09', 'evidence': [{'id': 'execution:1', 'quote': 'Automation continuity'}], 'basis': 'Paul confirmed routine automation is non-billable.'}
    path.write_text(json.dumps({'schema': 1, 'activity_billing': {entry_key(original): decision}}))
    before = [(b['start'], b['end'], b['title'], b['project_id']) for b in (original, later)]
    rows = apply_billing_policy([original, later], path=path)
    assert [b['invoice_status'] for b in rows] == ['non_billable', 'ready']
    assert before == [(b['start'], b['end'], b['title'], b['project_id']) for b in rows]
    decision['evidence'] = []
    path.write_text(json.dumps({'schema': 1, 'activity_billing': {entry_key(later): decision}}))
    assert apply_billing_policy([later], path=path)[0]['invoice_status'] == 'ready'


def test_pdf_export_uses_invoice_eligible_time_instead_of_activity_calendar(monkeypatch):
    from fastapi import FastAPI, APIRouter
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.reports import register_routes
    from distr.core.reports import month
    calls = []
    def get_period_report(*args, **kwargs):
        calls.append(kwargs)
        return {'invoice_only': kwargs.get('invoice_only', False)}
    monkeypatch.setattr(month, 'get_period_report', get_period_report)
    monkeypatch.setattr(month, 'render_pdf', lambda snapshot: b'%PDF-reviewed' if snapshot['invoice_only'] else b'%PDF-wrong')
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).get('/workflows/studio/reports/period/pdf?start=2026-10-23&end=2026-11-09&projects=2')
    assert response.status_code == 200
    assert response.content == b'%PDF-reviewed'
    assert calls[0]['invoice_only'] is True


def test_invoice_pdf_uses_site_label_and_readable_duration():
    from io import BytesIO
    from pypdf import PdfReader
    from distr.core.reports.month import render_pdf
    snapshot = {'period': {'label': 'Review period', 'start': '2026-10-23', 'end': '2026-11-09'},
                'project_ids': [10], 'projects': [{'id': 10, 'name': 'Spoortrack'}],
                'totals': {'seconds': 60, 'blocks': 1, 'projects': 1, 'active_days': 1},
                'by_project': [{'project_id': 10, 'name': 'Spoortrack [new]', 'seconds': 60, 'days': 1}],
                'by_day': [], 'sources': [], 'notes': [],
                'saved': {'provider': 'source-evidence', 'model': 'reviewed-source-ledger',
                          'analysis': {'summary': 'Reviewed work.', 'projects': [{'name': 'Spoortrack [new]', 'hours': 1/60, 'highlights': ['Corrected device schedule alignment.']}], 'notes': []}}}
    text = ' '.join(p.extract_text() for p in PdfReader(BytesIO(render_pdf(snapshot))).pages)
    assert 'Projects: Spoortrack [new]' in text
    assert 'Spoortrack [new] - 1m' in text
    assert '0.016666' not in text
