"""Synthetic source/account/queue/model boundaries; never access real LINE or Codex."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from line_agent.assistance import (
    classification,
    draft_get,
    live_signature,
    process_pending,
)
from line_agent.assistance_context import assemble_context, configure_source, digest
from line_agent.assistance_model import AssistanceFailure, implementation, validate_result
from line_agent.realtime import event_messages

OWNER = 'u' + 'f' * 32
PEER = 'u' + 'a' * 32


def authorize(store):
    store.put('setting', 'assistance_consent', {'provider': 'OpenAI Codex CLI',
        'scope': 'incoming_classification_drafts',
        'source_digest': digest(store.get('setting', 'assistance_source')),
        'identity_index': store._id('consent_owner', OWNER)})


def receive(service, peer=PEER, msg='incoming', rev=1, text='synthetic question', stamp=4000):
    return event_messages(service, {'event': 'message', 'data': [{'revision': rev, 'type': 26,
        'message': {'id': msg, 'from': peer, 'to': OWNER, 'text': text, 'createdTime': stamp,
                    'contentType': 0, 'decrypted': True}}]})


@pytest.fixture
def assistance(service, tmp_path):
    s = service.store
    s.put('setting', 'identity', {'mid': OWNER})
    s.put('setting', 'event_cursor', {'revision': 0})
    s.put('contact', PEER, {'displayName': '架空の相手'})
    corpus = tmp_path/'corpus.sqlite'
    with sqlite3.connect(corpus) as db:
        db.execute('CREATE TABLE entities(entity_key,current_mid,native_mid,kind,expected_records)')
        db.execute('CREATE TABLE messages(uid,entity_key,native_message_id,utc_ts,source_pk,'
                   'timestamp_jst,sender_role,content_type,text,text_state)')
        db.execute('INSERT INTO entities VALUES(?,?,?,?,?)', ('person1', PEER, PEER, 'person', 3))
        for i in range(3):
            text = f'synthetic owner example {i}'
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (f'uid{i}', 'person1', f'own{i}', i+1, i, 'synthetic date',
                        'owner_candidate', 0, text, 'available'))
            service.ingest(PEER, [{'id': f'own{i}', 'from': OWNER, 'to': PEER, 'text': text,
                'createdTime': (i+1)*1000, 'contentType': 0}])
    snapshot = tmp_path/'snapshot.json'
    snapshot.write_text(json.dumps({'rows': [{'対象ID': 'person1', '分類': '仕事', 'LINE名': '架空',
        '種別': '個人', '履歴件数': 3, '理由': 'synthetic prior business context',
        '判定元': 'base_prediction', '根拠メッセージID': ['uid0']}]}))
    configure_source(s, corpus, snapshot)
    authorize(s)
    receive(service)
    return service, corpus, snapshot


class Provider:
    def __init__(self, hook=None):
        self.metadata = {'provider': 'synthetic', 'implementation': implementation()}
        self.hook = hook
        self.calls = 0

    def infer(self, doc):
        self.calls += 1
        if self.hook:
            self.hook(doc)
        return {'result': prediction(doc), 'provider': self.metadata, 'usage': None, 'tool_calls': 0}


def prediction(doc):
    return {'category': 'プライベート', 'category_basis': 'messages', 'name_evidence': None, 'relationship': ['friend'], 'purpose': ['everyday'],
        'reason': 'synthetic classification explanation', 'evidence': [doc['trigger_ref']],
        'referral_evidence': [], 'uncertainty': 'synthetic uncertainty',
        'draft_status': 'draft', 'text': 'synthetic draft', 'draft_reason': 'synthetic draft reason',
        'draft_evidence': [doc['trigger_ref']], 'questions': []}


def test_source_plus_live_pipeline_durable_and_no_send(assistance):
    service, _, _ = assistance
    p = Provider()
    result = process_pending(service, p)
    assert result['completed'] == 1
    draft = draft_get(service.store, PEER)
    assert draft['state'] == 'ready' and draft['draft']['text'] == 'synthetic draft'
    assert draft['classification']['category'] == '仕事'
    assert draft['classification']['source'] == 'prior_classification'
    assert draft['coverage']['available_count'] == 4
    assert draft['coverage']['input_count'] == 4
    assert draft['draft']['approval'] == 'not_approved'
    assert process_pending(service, p)['completed'] == 0 and p.calls == 1
    assert service.store.count('action') == 0 and not service.line.calls


def test_unreadable_trigger_stops_before_model_and_is_not_complete(assistance):
    service, _, _ = assistance
    msg = service.store.get('message', PEER+':incoming')
    msg.pop('text')
    msg['decrypted'] = False
    service.store.put('message', PEER+':incoming', msg, scope=PEER)
    provider = Provider()
    assert process_pending(service, provider)['failed'] == 1
    assert provider.calls == 0
    job = service.store.get('classification_job', PEER)
    assert job['state'] == 'error' and job['reason'] == 'message_decryption_failed'
    assert service.store.count('current_assistance') == 0


def test_backup_text_cannot_hide_live_trigger_decryption_failure(assistance):
    service, corpus, snapshot = assistance
    with sqlite3.connect(corpus) as db:
        db.execute('UPDATE entities SET expected_records=4')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('incoming_uid', 'person1', 'incoming', 4, 4, 'synthetic date',
                    'participant', 0, 'synthetic original backup text', 'available'))
    rows = json.loads(snapshot.read_text())
    rows['rows'][0]['履歴件数'] = 4
    snapshot.write_text(json.dumps(rows))
    configure_source(service.store, corpus, snapshot)
    authorize(service.store)
    msg = service.store.get('message', PEER+':incoming')
    msg.pop('text')
    msg['decrypted'] = False
    service.store.put('message', PEER+':incoming', msg, scope=PEER)
    context = assemble_context(service.store, PEER)
    assert context['trigger_decryption_failed']
    provider = Provider()
    assert process_pending(service, provider)['failed'] == 1 and provider.calls == 0


def test_source_selection_without_new_consent_revokes_external_use(assistance):
    service, corpus, snapshot = assistance
    configure_source(service.store, corpus, snapshot)
    with pytest.raises(AssistanceFailure) as error:
        process_pending(service, Provider())
    assert error.value.reason == 'external_ai_not_authorized'


def test_consent_cannot_cross_source_or_account(assistance):
    service, _, _ = assistance
    source = service.store.get('setting', 'assistance_source')
    service.store.put('setting', 'assistance_source', {**source, 'snapshot_sha256': 'wrong'})
    p = Provider()
    with pytest.raises(AssistanceFailure):
        process_pending(service, p)
    assert p.calls == 0


def test_wrong_account_backup_cannot_be_enabled(assistance):
    service, corpus, snapshot = assistance
    service.store.put('setting', 'identity', {'mid': 'u'+'b'*32})
    with pytest.raises(ValueError, match='context_owner_not_corroborated'):
        configure_source(service.store, corpus, snapshot)


def test_nonempty_wal_is_rejected(assistance):
    service, corpus, _ = assistance
    Path(str(corpus)+'-wal').write_bytes(b'synthetic noncheckpointed WAL')
    p = Provider()
    assert process_pending(service, p)['failed'] == 1 and p.calls == 0
    job = service.store.get('classification_job', PEER)
    assert job['reason'] == 'context_uncheckpointed_wal'


def test_new_incoming_during_generation_cannot_finish_old_job(assistance):
    service, _, _ = assistance
    p = Provider(lambda doc: receive(service, msg='next', rev=2, stamp=5000))
    assert process_pending(service, p)['stale'] == 1
    assert service.store.get('classification_job', PEER)['state'] == 'pending'
    assert service.store.get('current_assistance', PEER) is None


def test_owner_reply_during_generation_invalidates_draft(assistance):
    service, _, _ = assistance
    def reply(doc):
        service.ingest(PEER, [{'id': 'owner-reply', 'from': OWNER, 'to': PEER,
            'text': 'synthetic owner reply', 'createdTime': 5000}])
    assert process_pending(service, Provider(reply))['stale'] == 1
    assert service.store.get('current_assistance', PEER) is None


def test_owner_replied_before_inference_suppresses_model_draft(assistance):
    service, _, _ = assistance
    service.ingest(PEER, [{'id': 'owner-reply', 'from': OWNER, 'to': PEER,
        'text': 'synthetic reply', 'createdTime': 5000}])
    assert process_pending(service, Provider())['completed'] == 1
    assert draft_get(service.store, PEER)['draft']['status'] == 'no_reply_needed'


def test_delivery_of_already_saved_message_still_invalidates_old_draft(assistance):
    service, _, _ = assistance
    message = {'id': 'already-saved', 'from': PEER, 'to': OWNER, 'text': 'synthetic next',
               'createdTime': 5000, 'contentType': 0, 'decrypted': True}
    service.ingest(PEER, [message])
    assert process_pending(service, Provider())['completed'] == 1
    before = service.store.get('message', PEER+':already-saved')
    assert draft_get(service.store, PEER)['state'] == 'ready'
    receive(service, msg='already-saved', text='synthetic next', stamp=5000, rev=2)
    assert before == service.store.get('message', PEER+':already-saved')
    assert draft_get(service.store, PEER)['state'] == 'stale'
    assert draft_get(service.store, PEER)['draft']['text'] is None


def test_source_bytes_changed_after_generation_not_served_as_fresh(assistance):
    service, _, snapshot = assistance
    assert process_pending(service, Provider())['completed'] == 1
    snapshot.write_text(snapshot.read_text()+' ')
    assert draft_get(service.store, PEER)['state'] == 'stale'


def test_frozen_owner_proof_not_changed_by_new_unrelated_messages(assistance):
    service, _, _ = assistance
    before = service.store.get('setting', 'assistance_source')['binding']
    service.ingest(PEER, [{'id': 'unrelated', 'from': OWNER, 'text': 'synthetic unmatched',
                          'to': PEER, 'createdTime': 1000}])
    assert assemble_context(service.store, PEER)['prior']
    assert service.store.get('setting', 'assistance_source')['binding'] == before


def test_deferred_failed_jobs_do_not_starve_later_chat(assistance):
    service, _, _ = assistance
    p = Provider()
    for i in range(3):
        chat = 'u'+str(i+1)*32
        job = {'chat': chat, 'state': 'error', 'updated_at': -100+i, 'latest_message_id': 'bad'}
        service.store.put('classification_job', chat, job)
        key = digest({'chat': chat, 'message': 'bad', 'updated_at': job['updated_at'],
                      'provider': p.metadata})
        service.store.put('assistance_attempt', key, {'attempts': 2})
    r = process_pending(service, p, limit=1)
    assert r['completed'] == 1 and r['deferred'] == 3 and p.calls == 1


def test_model_failure_has_stage_reason_and_retry_bound(assistance):
    service, _, _ = assistance
    def fail(doc):
        raise AssistanceFailure('model_cli_failed', {'exit_code': 1, 'provider_signal': 'authentication'})
    p = Provider(fail)
    assert process_pending(service, p)['failed'] == 1
    row = service.store.records('assistance_attempt')[0]['value']
    assert row['stage'] == 'model_inference'
    assert row['details']['provider_signal'] == 'authentication'
    assert row['next_check'] == 'model_inference_contract_and_dependencies'
    assert process_pending(service, p)['deferred'] == 1 and p.calls == 1


def test_invalid_model_reference_does_not_complete_job(assistance):
    service, _, _ = assistance
    class Bad(Provider):
        def infer(self, doc):
            value = super().infer(doc)
            value['result']['evidence'] = ['other-chat:fake']
            return value
    assert process_pending(service, Bad())['failed'] == 1
    assert service.store.get('classification_job', PEER)['state'] == 'error'
    assert not service.store.get('current_assistance', PEER)


def test_owner_mixed_correction_is_preserved(assistance):
    service, _, _ = assistance
    service.store.put('memory', PEER, {'labels': ['仕事', 'プライベート'], 'confidence': 'owner_corrected'})
    doc = assemble_context(service.store, PEER)
    c = classification(doc, prediction(doc))
    assert c['category'] == '両方' and c['owner_confirmed']


def test_priority_referral_does_not_require_keyword_quote(assistance):
    service, _, _ = assistance
    doc = assemble_context(service.store, PEER)
    value = prediction(doc)
    value['referral_evidence'] = [doc['trigger_ref']]
    assert classification(doc, value)['category'] == '仕事'
    assert 'referral' in classification(doc, value)['rule_ids']


def test_missing_trigger_time_does_not_falsely_claim_owner_replied(assistance):
    service, _, _ = assistance
    receive(service, msg='no-time', rev=2, stamp=0)
    doc = assemble_context(service.store, PEER)
    assert not doc['trigger_timestamp_known'] and not doc['owner_replied']
    value = validate_result(prediction(doc), doc)
    assert value['draft_status'] == 'needs_owner_input' and value['text'] is None


def test_budget_exhaustion_keeps_pending_without_call(assistance):
    service, _, _ = assistance
    service.store.put('setting', 'assistance_budget', {'requests': [time.time()]*30})
    p = Provider()
    assert process_pending(service, p)['deferred'] == 1 and p.calls == 0
    assert service.store.get('classification_job', PEER)['state'] == 'pending'


def test_freshness_changes_on_owner_correction(assistance):
    service, _, _ = assistance
    before = live_signature(service.store, PEER)
    service.store.put('memory', PEER, {'labels': ['プライベート'], 'confidence': 'owner_corrected'})
    assert live_signature(service.store, PEER) != before


def test_mixed_source_other_entity_is_withheld_before_model(assistance):
    service, corpus, snapshot = assistance
    other = 'u'+'c'*32
    with sqlite3.connect(corpus) as db:
        db.execute('INSERT INTO entities VALUES(?,?,?,?,?)', ('other', other, other, 'person', 1))
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('foreign-uid', 'other', 'foreign-id', 1, 10, 'synthetic date',
                    'owner_candidate', 0, 'synthetic other-account secret', 'available'))
    payload = json.loads(snapshot.read_text())
    payload['rows'].append({'対象ID': 'other', '分類': '仕事', 'LINE名': 'synthetic foreign name',
        '履歴件数': 1, '理由': 'foreign reason', '判定元': 'owner', '根拠メッセージID': ['foreign-uid']})
    snapshot.write_text(json.dumps(payload))
    configure_source(service.store, corpus, snapshot)
    authorize(service.store)
    receive(service, peer=other, msg='other-incoming', rev=2, stamp=5000)
    doc = assemble_context(service.store, other)
    assert doc['prior'] is None
    assert doc['coverage'] == 'live_only_backup_withheld'
    assert doc['source']['withheld_backup_count'] == 1
    assert 'synthetic other-account secret' not in json.dumps(doc)
    assert 'synthetic foreign name' not in json.dumps(doc)


@pytest.mark.parametrize('stamp', [0, 4000])
def test_owner_reply_with_missing_or_equal_time_requires_owner_input(assistance, stamp):
    service, _, _ = assistance
    service.ingest(PEER, [{'id': 'ambiguous-owner-reply', 'from': OWNER, 'to': PEER,
        'text': 'synthetic owner reply', 'createdTime': stamp}])
    doc = assemble_context(service.store, PEER)
    assert doc['trigger_timestamp_known'] and doc['owner_response_order_uncertain']
    value = validate_result(prediction(doc), doc)
    assert value['draft_status'] == 'needs_owner_input' and value['text'] is None


def test_previous_draft_does_not_hide_new_failure_diagnostics(assistance):
    service, _, _ = assistance
    assert process_pending(service, Provider())['completed'] == 1
    receive(service, msg='new-trigger', rev=2, stamp=6000)
    def fail(doc):
        raise AssistanceFailure('model_cli_failed', {'exit_code': 1, 'provider_signal': 'authentication'})
    assert process_pending(service, Provider(fail))['failed'] == 1
    value = draft_get(service.store, PEER)
    assert value['state'] == 'stale' and value['draft']['text'] is None
    assert value['processing']['failure_stage'] == 'model_inference'
    assert value['processing']['details']['provider_signal'] == 'authentication'
    assert value['processing']['operation_id']


def test_explicit_retry_preserves_failure_and_budget(assistance):
    from line_agent.assistance import retry_failed
    service, _, _ = assistance
    def fail(doc):
        raise AssistanceFailure('model_timeout')
    p = Provider(fail)
    assert process_pending(service, p)['failed'] == 1
    before = service.store.get('setting', 'assistance_budget')
    assert retry_failed(service, p) == 1
    assert service.store.count('assistance_retry') == 1
    assert service.store.get('setting', 'assistance_budget') == before
    assert process_pending(service, Provider())['completed'] == 1


def test_draft_pagination_validates_source_once_and_only_page(service, monkeypatch):
    from line_agent.assistance import drafts_list
    calls, checks = [], []
    for i in range(55):
        chat = 'u'+f'{i:032x}'
        service.store.put('current_assistance', chat, {'generation': str(i), 'created_at': i})
    def get(store, chat, *, source_changed):
        calls.append(chat)
        assert source_changed is False
        return {'chat': chat}
    def source(store):
        checks.append(True)
        return False
    monkeypatch.setattr('line_agent.assistance.draft_get', get)
    monkeypatch.setattr('line_agent.assistance._source_changed', source)
    value = drafts_list(service.store, offset=20, limit=10)
    assert value['total'] == 55 and value['next_offset'] == 30
    assert len(calls) == 10 and len(checks) == 1
    assert calls[0] == 'u'+f'{34:032x}'


def test_prior_basis_supported_without_fabricated_message_quote(assistance):
    service, _, _ = assistance
    doc = assemble_context(service.store, PEER)
    value = prediction(doc)
    value.update(category='仕事', category_basis='prior', evidence=[])
    assert validate_result(value, doc)['category'] == '仕事'
    value['category'] = 'プライベート'
    with pytest.raises(AssistanceFailure) as error:
        validate_result(value, doc)
    assert error.value.reason == 'model_prior_evidence_invalid'


def test_name_basis_requires_real_display_quote_not_keyword_gate(assistance):
    service, _, _ = assistance
    doc = assemble_context(service.store, PEER)
    doc.update(display_name='架空の英会話スクール', prior=None, coverage='live_only_backup_withheld')
    value = prediction(doc)
    value.update(category='仕事', category_basis='name', name_evidence='英会話スクール', evidence=[])
    checked = validate_result(value, doc)
    assert classification(doc, checked)['category'] == '仕事'
    value['name_evidence'] = 'not in this name'
    with pytest.raises(AssistanceFailure) as error:
        validate_result(value, doc)
    assert error.value.reason == 'model_name_evidence_invalid'


def test_owner_basis_cannot_be_self_declared_by_model(assistance):
    service, _, _ = assistance
    doc = assemble_context(service.store, PEER)
    value = prediction(doc)
    value.update(category_basis='owner_correction', category='仕事', evidence=[])
    with pytest.raises(AssistanceFailure):
        validate_result(value, doc)
    doc['owner_memory'] = {'confidence': 'owner_corrected', 'labels': ['仕事']}
    assert validate_result(value, doc)['category'] == '仕事'


def test_explicit_refresh_only_stale_preserves_previous_result(assistance):
    from line_agent.assistance import refresh_stale
    service, _, _ = assistance
    assert process_pending(service, Provider())['completed'] == 1
    old_count = service.store.count('assistance_result')
    assert refresh_stale(service) == 0
    service.store.put('setting', 'assistance_businesses', [{'name': 'synthetic business'}])
    assert refresh_stale(service) == 1
    assert service.store.count('assistance_refresh') == 1
    assert service.store.count('assistance_result') == old_count
    assert service.store.get('classification_job', PEER)['state'] == 'pending'
    assert process_pending(service, Provider())['completed'] == 1
    assert service.store.count('assistance_result') == old_count+1


def test_two_sequential_explicit_refreshes_get_fresh_attempt_budget(assistance):
    from line_agent.assistance import refresh_stale
    service, _, _ = assistance
    provider = Provider()
    assert process_pending(service, provider)['completed'] == 1
    for version in range(2):
        service.store.put('setting', 'assistance_businesses', [{'name': f'synthetic {version}'}])
        assert refresh_stale(service) == 1
        result = process_pending(service, provider)
        assert result['completed'] == 1 and result['deferred'] == 0
    assert provider.calls == 3
    assert service.store.count('assistance_result') == 3
    assert service.store.count('assistance_refresh') == 2


def test_twice_killed_generation_becomes_diagnosable_and_manually_retryable(assistance):
    from line_agent.assistance import retry_failed
    service, _, _ = assistance
    provider = Provider()
    job = service.store.get('classification_job', PEER)
    key = digest({'chat': PEER, 'message': job['latest_message_id'], 'updated_at': job['updated_at'],
                  'provider': provider.metadata})
    service.store.put('assistance_attempt', key, {'attempts': 2, 'state': 'running',
                                               'operation_id': 'synthetic-interruption'})
    assert process_pending(service, provider)['deferred'] == 1
    assert provider.calls == 0
    assert service.store.get('classification_job', PEER)['reason'] == 'assistance_interrupted'
    assert service.store.count('assistance_failure') == 1
    assert retry_failed(service, provider) == 1
    assert process_pending(service, provider)['completed'] == 1
