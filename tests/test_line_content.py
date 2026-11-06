import time
from types import SimpleNamespace

import pytest
from thrift.protocol.TCompactProtocol import TCompactProtocol
from thrift.Thrift import TMessageType, TType
from thrift.transport.TTransport import TMemoryBuffer

from line_agent import line_content as c

CHAT = "c" + "1" * 32
OWNER = "u" + "2" * 32


class FakeClient:
    def __init__(self):
        self.api = SimpleNamespace(tokens=SimpleNamespace(mid=OWNER))
        self.response = {}

    def home(self, x):
        return x

    def json(self, *a, **kw):
        return self.response

    def download(self, *a):
        return b"\x89PNG\r\n\x1a\nsynthetic"


@pytest.fixture
def client(service, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(c, "ContentClient", lambda api: fake)
    service.line.call = lambda f: f(None)
    return fake


def test_album_chain_filters_binds_and_saves(service, client):
    client.response = {
        "albums": [
            {"chatId": CHAT, "albumId": "12", "title": "synthetic"},
            {"chatId": "c" + "3" * 32, "albumId": "13", "title": "not returned"},
        ],
        "hasMore": False,
    }
    result = c.albums_list(service, CHAT)
    assert [x["album_id"] for x in result["items"]] == ["12"]
    assert result["complete"] is False
    client.response = {
        "photos": [{"obsResourceId": {"svc": "album", "sid": "a", "oid": "object1"}}]
    }
    photos = c.album_photos(service, CHAT, "12")["items"]
    result = c.album_media_get(service, CHAT, "12", [photos[0]["photo_id"]])
    assert result["ok"] and result["items"][0]["bytes"] > 0
    with pytest.raises(ValueError):
        c.album_media_get(service, "c" + "3" * 32, "12", [photos[0]["photo_id"]])
    with pytest.raises(ValueError):
        c.album_media_get(service, CHAT, "13", [photos[0]["photo_id"]])


@pytest.mark.parametrize(
    "photos", [[None], [{"obsResourceId": None}], [{"obsResourceId": {"oid": "../../bad"}}]]
)
def test_malformed_photo_is_explicit(service, client, photos):
    service.store.put("album_reference", CHAT + ":12", {"at": time.time(), "home": CHAT})
    client.response = {"photos": photos}
    result = c.album_photos(service, CHAT, "12")
    assert not result["ok"] and result["reason"] == "unexpected_schema" and result["items"] is None


def test_unavailable_never_empty_success(service, client):
    def fail(*a, **kw):
        raise c.ContentFailure("album_channel", "transport_timeout")

    client.json = fail
    result = c.albums_list(service, CHAT)
    assert result["ok"] is False and result["complete"] is False and result["items"] is None
    assert result["stage"] == "album_channel"


def test_note_schema_and_foreign_home(service, client):
    client.response = {
        "posts": [{"postInfo": {"postId": "4", "homeId": CHAT}, "contents": {"text": "synthetic"}}]
    }
    assert c.notes_list(service, CHAT)["items"][0]["untrusted_content"]
    client.response["posts"][0]["postInfo"]["homeId"] = OWNER
    assert c.notes_list(service, CHAT)["ok"] is False


def test_reference_expiry_pause_and_cursors(service, client):
    service.store.put("album_reference", CHAT + ":12", {"at": 0, "home": CHAT})
    with pytest.raises(ValueError):
        c.album_photos(service, CHAT, "12")
    with pytest.raises(ValueError):
        c.albums_list(service, CHAT, "x" * 2049)
    with pytest.raises(ValueError):
        c.notes_list(service, CHAT, -1)
    service.pause()
    with pytest.raises(ValueError):
        c.albums_list(service, CHAT)


def channel_response(code=None):
    b = TMemoryBuffer()
    p = TCompactProtocol(b)
    p.writeMessageBegin("approveChannelAndIssueChannelToken", TMessageType.REPLY, 0)
    p.writeStructBegin("result")
    p.writeFieldBegin(
        "success" if code is None else "error", TType.STRUCT, 0 if code is None else 1
    )
    p.writeStructBegin("body")
    if code is None:
        p.writeFieldBegin("channelAccessToken", TType.STRING, 5)
        p.writeString("synthetic-channel-secret")
    else:
        p.writeFieldBegin("code", TType.I32, 1)
        p.writeI32(code)
    p.writeFieldEnd()
    p.writeFieldStop()
    p.writeStructEnd()
    p.writeFieldEnd()
    p.writeFieldStop()
    p.writeStructEnd()
    p.writeMessageEnd()
    return b.getvalue()


def test_channel_contract_and_error():
    assert c.decode_channel(channel_response()) == "synthetic-channel-secret"
    with pytest.raises(c.ContentFailure) as err:
        c.decode_channel(channel_response(6))
    assert err.value.code == 6 and err.value.stage == "channel_authorize"
    for data in (b"", b"invalid", channel_response()[:8]):
        with pytest.raises(c.ContentFailure):
            c.decode_channel(data)


def test_additional_mapping_requires_message_proof_and_owner_cache(service, monkeypatch):
    api = SimpleNamespace(
        tokens=SimpleNamespace(mid=OWNER),
        get_chats=lambda *a, **k: {"chats": [{"chatMid": CHAT, "chatName": "synthetic"}]},
        get_recent_messages=lambda *a: [
            {"to": CHAT, "id": "11"},
            {"to": CHAT, "id": "12"},
            {"to": "c" + "4" * 32, "id": "13"},
        ],
    )
    bound = c.AdditionalContentClient(api, service.store)
    calls = []

    def resolve(command, **params):
        calls.append((command, params))
        assert params["message_ids"] == ["11", "12"]
        return {"home": "c" + "5" * 32, "matched_messages": 2}

    monkeypatch.setattr(bound, "invoke", resolve)
    assert bound.home(CHAT) == "c" + "5" * 32
    assert bound.home(CHAT) == "c" + "5" * 32 and len(calls) == 1
    mapping = service.store.get("additional_group_mapping", CHAT)
    mapping["owner"] = "u" + "6" * 32
    service.store.put("additional_group_mapping", CHAT, mapping)
    assert bound.home(CHAT) == "c" + "5" * 32 and len(calls) == 2
    api.get_recent_messages = lambda *a: [{"to": CHAT, "id": "11"}, {"to": CHAT, "id": "11"}]
    mapping["owner"] = OWNER
    mapping["at"] = 0
    service.store.put("additional_group_mapping", CHAT, mapping)
    with pytest.raises(c.ContentFailure) as e:
        bound.home(CHAT)
    assert e.value.reason == "group_mapping_unavailable"


def test_album_real_cursor_and_original_resource(service, client):
    client.response = {
        "albums": [{"chatId": CHAT, "albumId": "12", "title": "synthetic"}],
        "cursor": "page2",
        "hasMore": True,
    }
    assert c.albums_list(service, CHAT)["next_cursor"] == "page2"
    client.response = {
        "photos": [
            {
                "obsResourceId": {"svc": "album", "sid": "o", "oid": "original-object"},
                "resourceType": "IMAGE_ORIGINAL",
            }
        ],
        "hasMore": False,
    }
    result = c.album_photos(service, CHAT, "12")
    assert result["ok"] and result["page_end"]
    out = c.album_media_get(service, CHAT, "12", [result["items"][0]["photo_id"]])
    assert out["ok"]


def test_note_null_info_and_partial_download(service, client):
    client.response = {"posts": [{"postInfo": None, "contents": {}}]}
    assert c.notes_list(service, CHAT)["reason"] == "unexpected_schema"
    for oid in ["a" * 64, "b" * 64]:
        service.store.put(
            "album_photo_reference",
            oid,
            {
                "chat": CHAT,
                "album_id": "12",
                "home": CHAT,
                "at": time.time(),
                "sid": "a",
                "oid": oid,
            },
        )
    count = 0

    def download(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise c.ContentFailure("album_download", "transport_timeout")
        return b"\x89PNG\r\n\x1a\nsynthetic"

    client.download = download
    result = c.album_media_get(service, CHAT, "12", ["a" * 64, "b" * 64])
    assert not result["ok"] and not result["complete"] and len(result["items"]) == 1


def test_upstream_error_code_preserved(service, monkeypatch):
    client = c.AdditionalContentClient(
        SimpleNamespace(tokens=SimpleNamespace(mid=OWNER)), service.store
    )
    monkeypatch.setattr(
        client,
        "invoke",
        lambda *a, **kw: {"code": 500, "message": "private upstream detail", "result": None},
    )
    with pytest.raises(c.ContentFailure) as e:
        client.json("/ext/album/moa/v2/albums", {}, channel="1375220249")
    assert (
        e.value.stage == "content_list"
        and e.value.code == 500
        and e.value.reason == "upstream_rejected"
    )


def test_capability_discovery_without_account(monkeypatch):
    from line_agent import cli

    def forbidden(*a, **kw):
        raise AssertionError("must not open account")

    monkeypatch.setattr(cli, "open_store", forbidden)
    result = cli.run(SimpleNamespace(command="capabilities"))
    assert result["version"] == "0.6.0"
    assert any(t["name"] == "album_media_get" for t in result["tools"])
    assert "unsend_message" in result["operations"]


def test_note_authoritative_zero_and_invalid_empty(service, client):
    client.response = {"homeInfo": {"homeId": CHAT, "postCount": 0}}
    result = c.notes_list(service, CHAT)
    assert (
        result["ok"] and result["items"] == [] and result["total_count"] == 0 and result["complete"]
    )
    for count in [False, "0", None, 1, -1]:
        client.response = {"homeInfo": {"homeId": CHAT, "postCount": count}}
        assert not c.notes_list(service, CHAT)["ok"]
    client.response = {"homeInfo": {"homeId": OWNER, "postCount": 0}}
    assert c.notes_list(service, CHAT)["stage"] == "note_scope"
    client.response = {"posts": [], "homeInfo": {"homeId": CHAT, "postCount": False}}
    assert not c.notes_list(service, CHAT)["ok"]


def test_note_snapshot_scope_and_preserved_write(service, monkeypatch):
    api = SimpleNamespace(tokens=SimpleNamespace(mid=OWNER))
    service.store.put("note_reference", CHAT + ":123", {"home": CHAT, "at": time.time()})
    monkeypatch.setattr(c.AdditionalContentClient, "home", lambda self, chat: CHAT)
    contents = {
        "text": "synthetic",
        "media": [{"objectId": "synthetic"}],
        "contentsStyle": {"synthetic": True},
    }
    calls = []

    def invoke(self, command, **args):
        calls.append((command, args))
        if command == "note_get":
            return {
                "code": 0,
                "result": {
                    "post": {
                        "postInfo": {"homeId": CHAT, "postId": "123", "updatedTime": 1},
                        "contents": contents,
                    }
                },
            }
        return {"code": 0, "result": {"postInfo": {"postId": "123"}}}

    monkeypatch.setattr(c.AdditionalContentClient, "invoke", invoke)
    snap = c.note_snapshot(api, service.store, CHAT, "123")
    assert (
        c.execute_note(
            api,
            service.store,
            "note_update",
            {"group": CHAT, "post_id": "123", "text": "replacement"},
            snap,
        )["id"]
        == "123"
    )
    assert calls[-1][1]["contents"] == contents
    with pytest.raises(ValueError, match="snapshot_required"):
        c.execute_note(api, service.store, "note_delete", {"group": CHAT, "post_id": "123"})
    service.store.put("note_reference", CHAT + ":123", {"home": OWNER, "at": time.time()})
    with pytest.raises(c.ContentFailure):
        c.note_snapshot(api, service.store, CHAT, "123")
