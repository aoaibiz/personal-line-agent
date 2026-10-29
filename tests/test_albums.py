import json
import os
from types import SimpleNamespace

import pytest

from line_agent.albums import (
    AlbumFailure,
    _guard,
    download_photos,
    list_albums,
    list_photos,
    notification_index,
)

CHAT = 'c' + '1' * 32
OTHER = 'c' + '2' * 32


def notification(chat=CHAT, oid='synthetic_object'):
    return {'id': '123', 'to': chat, 'contentType': 16, 'contentMetadata': {
        'albumName': 'Synthetic album', 'cafeId': '456', 'mediaCount': '100',
        'postEndUrl': f'line://group/home/albums/album?albumId=456&homeId={chat}',
        'previewMedias': json.dumps([{'svc': 'album', 'sid': 'o', 'mediaOid': oid, 'mediaType': 'I'}]),
    }}


def setup(service, messages, fail=False):
    calls = []
    def call(endpoint, *args, **kw):
        calls.append(endpoint)
        if fail:
            raise RuntimeError('synthetic secret token')
        return {'channelAccessToken': 'synthetic-token'}
    api = SimpleNamespace(transport=SimpleNamespace(call=call), get_recent_messages=lambda *a: messages)
    service.line.call = lambda op: op(api)
    return api, calls


def test_listing_is_scoped_and_explicitly_partial(service):
    setup(service, [notification(), notification(), notification(OTHER)])
    result = list_albums(service, CHAT)
    assert result['complete'] is False
    assert len(result['items']) == 1
    assert result['items'][0]['referenced_photo_count'] == 1
    assert '100' not in str(result['items'])  # mediaCount isn't album total
    setup(service, [notification()])
    result = list_photos(service, CHAT, '456')
    assert len(result['items']) == 1
    assert 'synthetic_object' not in str(result)


def test_cross_group_link_and_path_injection_are_not_references():
    msg = notification()
    msg['contentMetadata']['postEndUrl'] = msg['contentMetadata']['postEndUrl'].replace(CHAT, OTHER)
    assert not notification_index([msg], CHAT)[0]
    albums, skipped = notification_index([notification(oid='../external')], CHAT)
    assert len(albums['456']['photos']) == 0
    assert skipped == 1


def test_channel_failure_stops_download_and_does_not_expose_token(service):
    setup(service, [notification()], fail=True)
    pid = next(iter(notification_index([notification()], CHAT)[0]['456']['photos']))
    result = download_photos(service, CHAT, '456', [pid])
    assert result['reason'] == 'album_channel_unavailable'
    assert result['stage'] == 'album_channel'
    assert result['items'] == []
    assert 'synthetic secret' not in str(result)
    assert not (service.store.path / 'album-images').exists()


def test_arbitrary_photo_rejected_before_channel_call(service):
    _, calls = setup(service, [notification()])
    result = download_photos(service, CHAT, '456', ['0' * 64])
    assert result['reason'] == 'photo_not_in_album_notifications'
    assert calls == []


def test_guards_and_pause(service):
    api, calls = setup(service, [])
    _guard(api)
    for endpoint in ['Talk.TalkService.sendMessage', 'Talk.TalkService.sendChatChecked',
                     'Talk.TalkService.registerE2EEGroupKey']:
        with pytest.raises(AlbumFailure):
            api.transport.call(endpoint)
    with pytest.raises(AlbumFailure):
        api.transport.call('Talk.ChannelService.issueChannelToken', ['other'])
    assert not calls
    service.pause()
    with pytest.raises(ValueError):
        list_albums(service, CHAT)


def test_malformed_notification_does_not_hide_valid_records():
    bad = notification()
    bad['contentMetadata']['postEndUrl'] = 42
    albums, skipped = notification_index([bad, notification()], CHAT)
    assert len(albums) == 1
    assert skipped == 1


class Response:
    def __init__(self, status, chunks):
        self.status_code, self.chunks, self.closed = status, chunks, False
    def iter_content(self, _):
        return iter(self.chunks)
    def close(self):
        self.closed = True


def download_setup(service, responses):
    msg = notification()
    items = json.loads(msg['contentMetadata']['previewMedias'])
    items.append({**items[0], 'mediaOid': 'second_object'})
    msg['contentMetadata']['previewMedias'] = json.dumps(items)
    api, _ = setup(service, [msg])
    api.transport.config = SimpleNamespace(application_header='TEST')
    calls = []
    def send(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return responses.pop(0)
    api.transport._send = send
    ids = list(notification_index([msg], CHAT)[0]['456']['photos'])
    return ids, calls


@pytest.mark.parametrize('status', [302, 403])
def test_http_failure_no_save_or_redirect(service, status):
    response = Response(status, [])
    ids, calls = download_setup(service, [response])
    result = download_photos(service, CHAT, '456', [ids[0]])
    assert result['reason'] == 'album_photo_http_error'
    assert result['http_status'] == status
    assert result['items'] == []
    assert response.closed
    assert calls[0][2]['allow_redirects'] is False
    assert calls[0][0] == 'GET'
    assert not (service.store.path / 'album-images').exists()


def test_bounded_download(service, monkeypatch):
    monkeypatch.setattr('line_agent.albums.LIMIT', 10)
    response = Response(200, [b'a' * 8, b'b' * 8])
    ids, _ = download_setup(service, [response])
    result = download_photos(service, CHAT, '456', [ids[0]])
    assert result['reason'] == 'image_too_large'
    assert response.closed
    assert not (service.store.path / 'album-images').exists()


def test_success_save_and_later_failure_preserves_partial_result(service):
    from pathlib import Path
    first, second = Response(200, [b'\xff\xd8\xffsynthetic']), Response(403, [])
    ids, calls = download_setup(service, [first, second])
    result = download_photos(service, CHAT, '456', ids)
    assert result['ok'] is False
    assert len(result['items']) == 1
    path = Path(result['items'][0]['path'])
    if os.name == "nt":
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        allowed = [user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
                   win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)]
        for path in (path, path.parent):
            sd = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION |
                                               win32security.OWNER_SECURITY_INFORMATION)
            assert sd.GetSecurityDescriptorOwner() == user
            assert sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            acl = sd.GetSecurityDescriptorDacl()
            assert acl is not None and acl.GetAceCount() > 0
            for index in range(acl.GetAceCount()):
                ace = acl.GetAce(index)
                assert ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
                assert any(ace[2] == principal for principal in allowed)
    else:
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.read_bytes() == b'\xff\xd8\xffsynthetic'
    assert result['items'][0]['visual_verified'] is False
    assert first.closed and second.closed
    assert len(calls) == 2


def test_channel_numeric_diagnostic_preserved(service):
    from okline.exceptions import LineApiError
    api, _ = setup(service, [notification()])
    def reject(*a, **k):
        raise LineApiError('synthetic secret', code=6, status=400)
    api.transport.call = reject
    pid = next(iter(notification_index([notification()], CHAT)[0]['456']['photos']))
    result = download_photos(service, CHAT, '456', [pid])
    assert result['reason'] == 'album_channel_rejected'
    assert result['line_code'] == 6
    assert result['http_status'] == 400
    assert 'synthetic secret' not in str(result)


def test_pause_while_waiting_for_sdk_stops_read(service):
    def call(operation):
        service.pause()
        return operation(SimpleNamespace())
    service.line.call = call
    result = list_albums(service, CHAT)
    assert result['reason'] == 'agent_paused'
