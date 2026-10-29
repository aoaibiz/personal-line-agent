"""Read-only album discovery from actual talk notifications.

This is deliberately NOT the full album catalogue: Chrome does not expose a
verified catalogue route. A notification may omit photos, or describe deleted
photos. Never infer completeness from its mediaCount.
"""
import hashlib
import json
import os
import re
import uuid
from urllib.parse import parse_qs, urlsplit

from .platform_security import private_directory, private_open, reject_reparse
from .received_media import LIMIT, TOKEN, image_extension

DIGITS = re.compile(r'[0-9]{1,30}\Z')


class AlbumFailure(Exception):
    def __init__(self, reason, status=None, code=None):
        self.reason = reason
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.code = code if type(code) is int and 0 <= code <= 99999 else None


def _validate(service, chat, album_id=None):
    from .identifiers import is_group
    if not is_group(chat):
        raise ValueError('group_required')
    if album_id is not None and (not isinstance(album_id, str) or not DIGITS.fullmatch(album_id)):
        raise ValueError('invalid_album_id')
    if service.paused():
        raise ValueError('agent_paused')


def notification_index(messages, chat):
    """Return only references that belong to this chat and match their album link."""
    if not isinstance(messages, list) or len(messages) > 100:
        raise AlbumFailure('unexpected_history')
    albums = {}
    skipped = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get('to') != chat or msg.get('contentType') != 16:
            continue
        md = msg.get('contentMetadata') or {}
        if not isinstance(md, dict) or 'albumName' not in md:
            continue
        try:
            link_text = md.get('postEndUrl', '')
            if not isinstance(link_text, str) or len(link_text) > 4096:
                raise ValueError()
            link = urlsplit(link_text)
            query = parse_qs(link.query)
            aid = md.get('cafeId')
            if (link.scheme != 'line' or link.netloc != 'group' or link.path != '/home/albums/album'
                    or query.get('homeId') != [chat] or query.get('albumId') != [aid]
                    or not isinstance(aid, str) or not DIGITS.fullmatch(aid)
                    or not isinstance(md.get('albumName'), str)):
                raise ValueError()
            raw = md.get('previewMedias', '[]')
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ValueError()
            media = json.loads(raw)
            if not isinstance(media, list) or len(media) > 100:
                raise ValueError()
            mid = str(msg.get('id', ''))
            if not DIGITS.fullmatch(mid):
                raise ValueError()
            row = albums.setdefault(aid, {'album_id': aid, 'name': md['albumName'][:300],
                                         'photos': {}, 'notification_ids': []})
            row['notification_ids'].append(mid)
            for item in media:
                if (not isinstance(item, dict) or item.get('svc') != 'album'
                        or item.get('sid') != 'o' or item.get('mediaType') != 'I'
                        or not isinstance(item.get('mediaOid'), str)
                        or not TOKEN.fullmatch(item['mediaOid'])):
                    skipped += 1
                    continue
                # Opaque caller handle; object locator stays inside the adapter.
                pid = hashlib.sha256((chat + '\0' + aid + '\0' + item['mediaOid']).encode()).hexdigest()
                row['photos'][pid] = {'photo_id': pid, 'oid': item['mediaOid'], 'notification_id': mid}
        except (ValueError, TypeError):
            skipped += 1
    return albums, skipped


def _guard(api):
    call = api.transport.call
    def read_call(endpoint, *args, **kw):
        if endpoint not in {'Talk.TalkService.getRecentMessagesV2',
                            'Talk.ChannelService.issueChannelToken'}:
            raise AlbumFailure('non_read_rpc_denied')
        if endpoint.endswith('issueChannelToken') and (not args or args[0] != ['1375220249']):
            raise AlbumFailure('channel_scope_denied')
        return call(endpoint, *args, **kw)
    api.transport.call = read_call


def _run(service, chat, mode, album_id=None, photo_ids=None):
    _validate(service, chat, album_id)
    if mode == 'download' and (not isinstance(photo_ids, list) or not 1 <= len(photo_ids) <= 5
            or len(set(photo_ids)) != len(photo_ids)
            or any(not isinstance(x, str) or not re.fullmatch(r'[a-f0-9]{64}', x) for x in photo_ids)):
        raise ValueError('invalid_photo_ids')

    def operation(api):
        stage = 'album_notification_lookup'
        result = {'ok': True, 'source': 'recent_100_talk_notifications', 'complete': False,
                  'limitation': '通知に含まれる写真のみ。アルバム全件・削除後の状態は未確認。', 'items': []}
        try:
            if service.paused():
                raise AlbumFailure('agent_paused')
            _guard(api)
            albums, skipped = notification_index(api.get_recent_messages(chat, 100), chat)
            result['skipped_records'] = skipped
            if mode == 'albums':
                result['items'] = [{'album_id': a['album_id'], 'name': a['name'],
                                    'referenced_photo_count': len(a['photos']),
                                    'notification_ids': a['notification_ids']} for a in albums.values()]
                return result
            if album_id not in albums:
                raise AlbumFailure('album_not_in_recent_notifications')
            photos = albums[album_id]['photos']
            if mode == 'photos':
                result['items'] = [{k: v for k, v in p.items() if k != 'oid'} for p in photos.values()]
                return result
            if any(p not in photos for p in photo_ids):
                raise AlbumFailure('photo_not_in_album_notifications')
            # Use the album channel only; do not try other tokens after denial.
            if service.paused():
                raise AlbumFailure('agent_paused')
            stage = 'album_channel'
            try:
                token_data = api.transport.call('Talk.ChannelService.issueChannelToken', ['1375220249'])
            except Exception as error:
                from okline.exceptions import LineApiError, LineTransportError
                reason = ('album_channel_rejected' if isinstance(error, LineApiError)
                          else 'album_channel_transport_failed' if isinstance(error, LineTransportError)
                          else 'album_channel_unavailable')
                raise AlbumFailure(reason, getattr(error, 'status', None), getattr(error, 'code', None)) from None
            token = (token_data.get('channelAccessToken') or token_data.get('token')) if isinstance(token_data, dict) else None
            if not isinstance(token, str) or not token:
                raise AlbumFailure('album_channel_unavailable')
            for pid in photo_ids:
                if service.paused():
                    raise AlbumFailure('agent_paused')
                stage = 'album_photo_download'
                oid = photos[pid]['oid']
                # Fixed host/path; no upstream URL, redirect or caller filesystem path.
                response = api.transport._send('GET', 'https://obs.line-apps.com/r/album/o/' + oid,
                    headers={'X-Line-ChannelToken': token, 'X-Line-Album': album_id,
                             'X-Line-Mid': chat, 'X-Line-Application': api.transport.config.application_header},
                    allow_redirects=False, stream=True, timeout=15)
                try:
                    if response.status_code != 200:
                        raise AlbumFailure('album_photo_http_error', response.status_code)
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        if len(data) + len(chunk) > LIMIT:
                            raise AlbumFailure('image_too_large')
                        data.extend(chunk)
                finally:
                    response.close()
                stage = 'album_photo_save'
                result['items'].append(_save(service.store.path, pid, bytes(data)))
            return result
        except Exception as error:
            result.update(ok=False, stage=stage,
                          reason=error.reason if isinstance(error, AlbumFailure) else 'album_read_failed',
                          http_status=error.status if isinstance(error, AlbumFailure) else None,
                          line_code=error.code if isinstance(error, AlbumFailure) else None)
            return result
    return service.line.call(operation)


def _save(root, photo_id, data):
    ext = image_extension(data)
    if not 0 < len(data) <= LIMIT:
        raise AlbumFailure('image_size_invalid')
    folder = root / 'album-images'
    if folder.is_symlink():
        raise AlbumFailure('unsafe_output')
    private_directory(folder)
    entries = list(folder.iterdir())
    for entry in entries:
        reject_reparse(entry)
    if (len(entries) >= 200 or any(p.is_symlink() or not p.is_file() for p in entries)
            or sum(p.stat().st_size for p in entries) + len(data) > 256 * 1024 * 1024):
        raise AlbumFailure('output_quota_or_unsafe_entry')
    path = folder / (uuid.uuid4().hex + ext)
    fd = private_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    return {'photo_id': photo_id, 'path': str(path), 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'visual_verified': False}


def list_albums(service, chat):
    return _run(service, chat, 'albums')


def list_photos(service, chat, album_id):
    return _run(service, chat, 'photos', album_id)


def download_photos(service, chat, album_id, photo_ids):
    return _run(service, chat, 'download', album_id, photo_ids)
