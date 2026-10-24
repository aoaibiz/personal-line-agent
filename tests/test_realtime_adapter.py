"""SSE boundary contract tests. All messages and API objects are synthetic."""
from types import SimpleNamespace

import pytest
from requests.exceptions import ReadTimeout

from line_agent.line import LineAdapter
from line_agent.realtime import ReceiveFailure, event_messages

OWNER = 'u' + 'f' * 32
PEER = 'u' + 'a' * 32


def wire_adapter(service, monkeypatch, events, *, status=200):
    monkeypatch.setattr('line_agent.line.shutil.which', lambda name: '/synthetic/node')
    service.store.put('setting', 'identity', {'mid': OWNER})
    service.store.put('credential', 'session', {'mid': OWNER})
    service.store.put('setting', 'event_cursor', {'revision': 0})
    calls = []
    response = SimpleNamespace(status_code=status, iter_lines=lambda **k: iter(()),
        close=lambda: calls.append('response_closed'))
    def original_send(*args, **kwargs):
        return response
    transport = SimpleNamespace(_send=original_send)
    def stream(**kwargs):
        transport._send('GET', 'https://synthetic.invalid', stream=True)
        try:
            for row in events:
                if isinstance(row, Exception):
                    raise row
                yield row
        finally:
            calls.append('stream_closed')
    api = SimpleNamespace(
        e2ee=SimpleNamespace(register_group_key=lambda *a: calls.append("registered_key")),
        get_profile=lambda: {'mid': OWNER}, transport=transport,
        ops=SimpleNamespace(_open_sse=stream), close=lambda: calls.append('api_closed'),
        decrypt_message=lambda m: {**m, 'chunks': [], 'text': 'synthetic decrypted'},
    )
    adapter = LineAdapter(service.store)
    monkeypatch.setattr(adapter, '_client', lambda session: api)
    monkeypatch.setattr(adapter, '_save', lambda api: calls.append('credentials_saved'))
    return adapter, api, calls, original_send


def test_wire_projection_decrypt_and_atomic_save(service, monkeypatch):
    event = SimpleNamespace(event='message', data={'operations': [{
        'revision': '1', 'type': 26, 'untrusted_extra': 'discard', 'message': {
            'id': 'synthetic-1', 'from': PEER, 'to': OWNER, 'contentType': 0,
            'chunks': ['synthetic encrypted payload'], 'contentMetadata': {'secret': 'discard'},
        }}]})
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [event])
    adapter.receive_events(lambda e: event_messages(service, e), 1)
    msg = service.store.get('message', PEER + ':synthetic-1')
    assert msg['text'] == 'synthetic decrypted' and msg['decrypted']
    assert 'chunks' not in msg and 'contentMetadata' not in msg
    assert service.store.get('setting', 'event_cursor')['revision'] == 1
    assert calls[-2:] == ['credentials_saved', 'api_closed']
    assert api.transport._send is original


def test_idle_timeout_keeps_cursor_and_closes(service, monkeypatch):
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [ReadTimeout('private')])
    adapter.receive_events(lambda e: event_messages(service, e), 1)
    assert service.store.get('setting', 'event_cursor')['revision'] == 0
    assert service.store.count('delivery') == 0
    assert calls[-2:] == ['credentials_saved', 'api_closed']
    assert api.transport._send is original


def test_non200_never_claims_open_and_closes(service, monkeypatch):
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [], status=401)
    received = []
    with pytest.raises(ReceiveFailure) as error:
        adapter.receive_events(received.append, 1)
    assert error.value.reason == 'sse_open_rejected' and received == []
    assert 'response_closed' in calls and 'credentials_saved' in calls
    assert api.transport._send is original


def test_received_group_cannot_trigger_sdk_key_registration(service, monkeypatch):
    group = 'c' + 'b' * 32
    event = SimpleNamespace(event='message', data={'operations': [{
        'revision': '1', 'type': 26, 'message': {
            'id': 'synthetic-group', 'from': PEER, 'to': group,
            'chunks': ['synthetic missing group key'],
        }}]})
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [event])
    api.decrypt_message = lambda m: api.e2ee.register_group_key(group)
    adapter.receive_events(lambda e: event_messages(service, e), 1)
    msg = service.store.get('message', group + ':synthetic-group')
    assert msg['decrypted'] is False and 'text' not in msg
    assert 'registered_key' not in calls
    assert api.transport._send is original


def test_live_profile_account_mismatch_stops_before_stream(service, monkeypatch):
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [])
    api.get_profile = lambda: {'mid': PEER}
    with pytest.raises(ReceiveFailure) as error:
        adapter.receive_events(lambda e: event_messages(service, e), 1)
    assert error.value.reason == 'identity_mismatch'
    assert 'stream_closed' not in calls and 'credentials_saved' not in calls
    assert service.store.get('setting', 'event_cursor')['revision'] == 0
    assert api.transport._send is original


@pytest.mark.parametrize('paused', [False, True])
def test_comment_only_stream_obeys_deadline_and_pause(service, monkeypatch, paused):
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [])
    # Simulate the pinned parser that consumes comments without yielding events.
    ticks = iter([0, 0.1, 2, 3])
    monkeypatch.setattr('line_agent.line.time.monotonic', lambda: next(ticks))
    def send(*args, **kwargs):
        return SimpleNamespace(status_code=200, close=lambda: None,
            iter_lines=lambda **k: iter([':keepalive'] * 10))
    api.transport._send = send
    def parse(**kwargs):
        resp = api.transport._send('GET', 'https://synthetic.invalid', stream=True)
        for line in resp.iter_lines(decode_unicode=True):
            if not line.startswith(':'):
                yield SimpleNamespace(event='ping', data={})
    api.ops._open_sse = parse
    if paused:
        service.store.put('setting', 'runtime', {'paused': True})
        with pytest.raises(ReceiveFailure) as error:
            adapter.receive_events(lambda e: None, 1)
        assert error.value.reason == 'paused'
    else:
        adapter.receive_events(lambda e: None, 1)
    assert api.transport._send is send


def test_missing_node_has_dependency_diagnosis_before_network(service, monkeypatch):
    adapter, api, calls, original = wire_adapter(service, monkeypatch, [])
    monkeypatch.setattr('line_agent.line.shutil.which', lambda name: None)
    with pytest.raises(ReceiveFailure) as error:
        adapter.receive_events(lambda e: None, 1)
    assert error.value.reason == 'dependency_missing'
    assert calls == []


def test_wire_utf8_japanese_not_split_by_latin1_response_default(service, monkeypatch):
    import json

    from okline.operations import OperationReceiver
    from requests import Response

    adapter, api, calls, original = wire_adapter(service, monkeypatch, [])
    payload = {'revision': 1, 'type': 26, 'message': {'id': 'japanese', 'from': PEER,
        'to': OWNER, 'text': '必ず確認\u0085よろしく\u2028お願いします', 'contentType': 0}}
    raw = ('event: message\ndata: '+json.dumps(payload, ensure_ascii=False)+'\n\n').encode('utf-8')
    response = Response()
    response.status_code = 200
    response.encoding = 'ISO-8859-1'
    response._content = raw
    response._content_consumed = True
    # Reproduce requests' text/* fallback splitting byte 0x85 inside 必.
    assert len(list(response.iter_lines(decode_unicode=True))) > 3
    api.transport.config = SimpleNamespace(gateway_base='https://synthetic.invalid',
        locale='ja_JP', user_agent='synthetic', ltsm_origin='https://synthetic.invalid')
    api.transport.tokens = SimpleNamespace(access_token=None)
    api.transport._sign = lambda *a: None
    api.transport._send = lambda *a, **k: response
    api.ops = OperationReceiver(api.transport)
    adapter.receive_events(lambda e: event_messages(service, e), 1)
    saved = service.store.get('message', PEER+':japanese')
    assert saved['text'] == payload['message']['text']
    assert service.store.get('setting', 'event_cursor')['revision'] == 1
