import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from line_agent.received_media import fetch_images, guarded_transport


def setup_api(service, messages):
    calls = []
    def download(*args, **kwargs):
        calls.append(args)
        return b'\x89PNG\r\n\x1a\n' + b'synthetic'
    api = SimpleNamespace(
        transport=SimpleNamespace(call=lambda *a, **k: None, _send=lambda *a, **k: None),
        get_recent_messages=lambda chat, count: messages,
        obs=SimpleNamespace(download_object=download),
    )
    service.line.call = lambda operation: operation(api)
    return calls


def test_scope_and_private_save(service):
    chat = 'c' + '1' * 32
    calls = setup_api(service, [
        {'id': '123', 'to': chat, 'contentType': 1},
        {'id': '124', 'to': 'c' + '2' * 32, 'contentType': 1},
        {'id': '125', 'to': chat, 'contentType': 0},
    ])
    r = fetch_images(service, chat, ['123', '124', '125', '126'])
    assert [x['state'] for x in r['items']] == ['saved', 'scope_or_type_mismatch', 'scope_or_type_mismatch', 'not_in_recent_page']
    assert len(calls) == 1
    p = Path(r['items'][0]['path'])
    if os.name == "nt":
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        allowed = [user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
                   win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)]
        for path in (p, p.parent):
            sd = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION |
                                               win32security.OWNER_SECURITY_INFORMATION)
            assert sd.GetSecurityDescriptorOwner() == user
            if path == p.parent:
                assert sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            acl = sd.GetSecurityDescriptorDacl()
            assert acl is not None and acl.GetAceCount() > 0
            for index in range(acl.GetAceCount()):
                ace = acl.GetAce(index)
                assert ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
                assert any(ace[2] == principal for principal in allowed)
    else:
        assert p.stat().st_mode & 0o777 == 0o600
        assert p.parent.stat().st_mode & 0o777 == 0o700


def test_guard_blocks_writes_and_foreign_host():
    transport = SimpleNamespace(call=lambda *a, **k: None, _send=lambda *a, **k: None)
    guarded_transport(SimpleNamespace(transport=transport))
    for rpc in ['sendMessage', 'sendChatChecked', 'registerE2EEGroupKey']:
        with pytest.raises(ValueError, match='non_read_rpc_denied'):
            transport.call('Talk.TalkService.' + rpc)
    with pytest.raises(ValueError, match='destination_denied'):
        transport._send('GET', 'https://example.com/r/talk/m/123')
    with pytest.raises(ValueError, match='object_path_denied'):
        transport._send('POST', 'https://obs.line-apps.com/r/talk/m/123')


def test_error_does_not_leak_upstream(service):
    setup_api(service, [])
    def failed(_):
        raise RuntimeError('synthetic secret')
    # Error inside the operation is sanitized; never return raw metadata.
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a: None, _send=lambda *a: None),
                          get_recent_messages=lambda *a: failed(None))
    service.line.call = lambda op: op(api)
    r = fetch_images(service, 'c' + '1' * 32, ['123'])
    assert r['reason'] == 'image_recovery_failed'
    assert 'synthetic secret' not in str(r)


def test_group_only_and_output_quota(service):
    chat = 'c' + '1' * 32
    setup_api(service, [{'id': '123', 'to': chat, 'contentType': 1}])
    with pytest.raises(ValueError, match='group_required'):
        fetch_images(service, 'u' + '1' * 32, ['123'])
    folder = service.store.path / 'received-images'
    folder.mkdir(mode=0o700)
    with (folder / 'synthetic-large.bin').open('wb') as stream:
        stream.truncate(256 * 1024 * 1024)
    r = fetch_images(service, chat, ['123'])
    assert r['reason'] == 'output_quota_exceeded'
    assert len(list(folder.iterdir())) == 1


def test_sdk_http_status_is_preserved(service):
    from okline.exceptions import LineTransportError
    def failure(*args):
        raise LineTransportError('synthetic secret', status=503)
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a: None, _send=lambda *a: None),
                          get_recent_messages=failure)
    service.line.call = lambda op: op(api)
    result = fetch_images(service, 'c' + '1' * 32, ['123'])
    assert result['http_status'] == 503
    assert 'synthetic secret' not in str(result)


class SyntheticResponse:
    def __init__(self, status, chunks):
        self.status_code, self.chunks, self.closed = status, chunks, False
    def iter_content(self, size):
        return iter(self.chunks)
    def close(self):
        self.closed = True


def test_download_transport_blocks_redirect_and_closes():
    response = SyntheticResponse(302, [])
    calls = []
    def send(method, url, **kw):
        calls.append(kw)
        return response
    transport = SimpleNamespace(call=lambda *a: None, _send=send)
    guarded_transport(SimpleNamespace(transport=transport))
    with pytest.raises(ValueError, match='redirect_denied'):
        transport._send('GET', 'https://obs.line-apps.com/r/talk/m/123')
    assert response.closed
    assert calls[0]['allow_redirects'] is False


def test_stream_limit_closes_response(monkeypatch):
    monkeypatch.setattr('line_agent.received_media.LIMIT', 10)
    response = SyntheticResponse(200, [b'a' * 8, b'b' * 8])
    transport = SimpleNamespace(call=lambda *a: None, _send=lambda *a, **k: response)
    guarded_transport(SimpleNamespace(transport=transport))
    with pytest.raises(ValueError, match='image_too_large'):
        transport._send('GET', 'https://obs.line-apps.com/r/talk/m/123')
    assert response.closed


def test_stream_content_is_retained_and_closed():
    response = SyntheticResponse(200, [b'a', b'b'])
    transport = SimpleNamespace(call=lambda *a: None, _send=lambda *a, **k: response)
    guarded_transport(SimpleNamespace(transport=transport))
    r = transport._send('GET', 'https://obs.line-apps.com/r/talk/m/123')
    assert r._content == b'ab'
    assert r._content_consumed and r.closed


def test_decrypt_failure_no_file_and_no_secret(service):
    def decrypt(_):
        raise RuntimeError('synthetic key material')
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a: None, _send=lambda *a: None),
        get_recent_messages=lambda *a: [{'id':'123', 'to':'c'+'1'*32, 'contentType':1,'chunks':['opaque']}],
        e2ee=SimpleNamespace(decrypt=decrypt))
    service.line.call = lambda op: op(api)
    result = fetch_images(service, 'c'+'1'*32, ['123'])
    assert result['ok'] is False and result['stage'] == 'image_decrypt'
    assert 'synthetic key' not in str(result)
    assert not (service.store.path/'received-images').exists()


def test_rpc_requires_exact_namespace():
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a: None, _send=lambda *a: None))
    guarded_transport(api)
    with pytest.raises(ValueError, match='non_read_rpc_denied'):
        api.transport.call('Other.Service.getRecentMessagesV2')


def test_pause_while_waiting_for_sdk_stops_read(service):
    api = SimpleNamespace()
    def call(operation):
        service.pause()
        return operation(api)
    service.line.call = call
    result = fetch_images(service, 'c'+'1'*32, ['123'])
    assert result['reason'] == 'agent_paused'


def test_pause_between_images_stops_second_download(service):
    chat = 'c'+'1'*32
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a: None, _send=lambda *a: None),
        get_recent_messages=lambda *a: [{'id':i, 'to':chat, 'contentType':1} for i in ['123','124']])
    calls = []
    def download(*a, **k):
        calls.append(a)
        service.pause()
        return b'\xff\xd8\xffsynthetic'
    api.obs = SimpleNamespace(download_object=download)
    service.line.call = lambda operation: operation(api)
    result = fetch_images(service, chat, ['123','124'])
    assert result['reason'] == 'agent_paused'
    assert len(calls) == 1 and len(result['items']) == 1
