import base64
import time

import pytest

from line_agent import additional_features as feature

TARGET = "m" + "1" * 32


def test_media_read_private_output_scope_and_limits(service, monkeypatch):
    owner = service.line.ids[0]
    service.store.put("setting", "identity", {"mid": owner})
    monkeypatch.setattr(
        feature,
        "call",
        lambda *a, **k: {"ok": True, "data": {"data": base64.b64encode(b"synthetic").decode()}},
    )
    with pytest.raises(ValueError, match="list_required"):
        feature.openchat_media_get(service, TARGET, "123")
    service.store.put(
        "openchat_reference", TARGET, {"owner": owner, "expires_at": time.time() + 10}
    )
    out = feature.openchat_media_get(service, TARGET, "123")
    from pathlib import Path

    path = Path(out["path"])
    assert path.read_bytes() == b"synthetic"
    assert path.stat().st_mode & 0o777 == 0o600
    assert out["preview"] is False
    monkeypatch.setattr(feature, "call", lambda *a, **k: {"ok": False, "reason": "upstream_failed"})
    assert feature.openchat_media_get(service, TARGET, "123")["ok"] is False


def test_search_parser_empty_missing_payload_and_untrusted_links():
    import json

    from line_agent.openchat_search import parse_search

    nodes = [
        ["ShallowReactive", 1],
        {"pinia": 2},
        ["Reactive", 3],
        {"search": 4},
        {
            "keyword": 5,
            "searchedSquares": 6,
            "continuationToken": 13,
            "isSubmittedSearch": 14,
            "isLoading": 15,
        },
        "fixture",
        [7],
        {"square": 8, "memberCount": 12},
        {"emid": 9, "name": 10, "desc": 11},
        "SYNTHETIC_PUBLIC_ID",
        "Synthetic room",
        "Fixture description",
        7,
        "",
        True,
        False,
    ]

    def html():
        return (
            '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(nodes) + "</script>"
        )

    row = parse_search(html(), "fixture")["items"][0]
    assert row["url"] == "https://openchat.line.me/jp/cover/SYNTHETIC_PUBLIC_ID"
    nodes[6] = []
    assert parse_search(html(), "fixture")["items"] == []
    with pytest.raises(ValueError):
        parse_search("<html>error</html>", "fixture")
    with pytest.raises(ValueError):
        parse_search(html(), "other")
    nodes[6] = [7]
    nodes[9] = "../../etc"
    with pytest.raises(ValueError):
        parse_search(html(), "fixture")


def test_search_network_failure_is_not_empty_and_does_not_grant_write(service, monkeypatch):
    import urllib.request

    from line_agent.openchat_search import search_public

    class Failure:
        def open(self, *a, **k):
            raise OSError("synthetic")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Failure())
    row = search_public(service, "fixture")
    assert row["ok"] is False and row["items"] is None
    assert service.store.get("setting", "owner_client_delegation") is None


def test_search_diagnostics_identify_fetch_vs_decode(service, monkeypatch):
    import urllib.request

    from line_agent.openchat_search import search_public

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, size):
            return b"<html>changed</html>"

    class Opener:
        def open(self, *a, **k):
            return Reply()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    result = search_public(service, "test")
    assert result["stage"] == "public_search_decode"
    assert result["reason"] == "public_search_schema_changed"
    assert result["items"] is None


def test_public_cover_ticket_requires_matching_official_link_and_structured_data():
    from line_agent.openchat_search import parse_invitation

    page = '<a href="line://ti/g2/SYNTHETIC123?utm_source=test">Open</a>'
    page += '<script id="__NUXT_DATA__">[{"invitationTicket":1},"SYNTHETIC123"]</script>'
    assert parse_invitation(page) == "SYNTHETIC123"
    with pytest.raises(ValueError):
        parse_invitation(page.replace('line://ti/g2/SYNTHETIC123', 'line://ti/g2/OTHER'))
    with pytest.raises(ValueError):
        parse_invitation(page.replace('line://ti/g2/', 'https://attacker.invalid/'))
    with pytest.raises(ValueError):
        parse_invitation(page + '<a href="line://ti/g2/OTHER">Other</a>')


def test_public_resolver_binds_fresh_search_to_exact_typed_read(service, monkeypatch):
    from line_agent import openchat_search as public

    owner = service.line.ids[0]
    service.store.put("setting", "identity", {"mid": owner})
    monkeypatch.setattr(public, "resolve_ticket", lambda public_id: {"ok": True, "ticket": "SYNTHETIC123"})
    def read(svc, command, **kwargs):
        assert command == "square_resolve"
        assert kwargs == {"invitation_ticket": "SYNTHETIC123"}
        return {"ok": True, "data": {"square": {"mid": "s" + "1" * 32, "name": "Fixture"}}}
    monkeypatch.setattr(feature, "call", read)
    with pytest.raises(ValueError, match="public_search_required"):
        feature.openchat_resolve_public(service, "SYNTHETIC_PUBLIC")
    service.store.put("openchat_public_reference", "SYNTHETIC_PUBLIC", {"owner": owner, "expires_at": time.time()+10})
    result = feature.openchat_resolve_public(service, "SYNTHETIC_PUBLIC")
    assert result["ok"] and result["join_performed"] is False
    assert "SYNTHETIC123" not in str(result)
    assert service.store.get("setting", "owner_client_delegation") is None
