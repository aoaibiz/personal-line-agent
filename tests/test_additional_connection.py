import json
from types import SimpleNamespace

import pytest

from line_agent import additional_connection as a

OWNER = "u" + "1" * 32


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "node_modules").mkdir()
    monkeypatch.setattr(a, "adapter_directory", lambda: d)
    return d


def script(adapter, body):
    (adapter / "worker.mjs").write_text(
        "import {createInterface} from 'node:readline';const rl=createInterface({input:process.stdin});const {value}=await rl[Symbol.asyncIterator]().next();rl.close();const r=JSON.parse(value);"
        + body
        + "\nprocess.exit(0);\n"
    )


def test_private_state_and_credential_isolation(service, adapter):
    service.store.put("credential", "session", {"preserved": "synthetic-existing"})
    script(
        adapter,
        "console.log(JSON.stringify({event:'result',result:{connected:true},state:{owner:r.owner,token:'synthetic-token',storage:{key:'synthetic-key'}}}));",
    )
    result = a.invoke(service.store, "login", OWNER, display=lambda *args: None)
    assert result == {"connected": True}
    assert "token" not in json.dumps(result)
    assert service.store.get("credential", "session") == {"preserved": "synthetic-existing"}
    assert service.store.get("additional_connection", "linejs")["token"] == "synthetic-token"
    assert b"synthetic-token" not in service.store.db.read_bytes()
    assert a.invoke(service.store, "check", OWNER) == {"connected": True}


def test_wrong_owner_and_nonzero_exit_not_saved(service, adapter):
    script(
        adapter,
        "console.log(JSON.stringify({event:'result',result:{connected:true},state:{owner:'u'+ '2'.repeat(32),token:'secret',storage:{}}}));",
    )
    with pytest.raises(a.AdditionalFailure):
        a.invoke(service.store, "login", OWNER, display=lambda *args: None)
    assert service.store.get("additional_connection", "linejs") is None
    script(
        adapter,
        "console.log(JSON.stringify({event:'result',result:{connected:true},state:{owner:r.owner,token:'secret',storage:{}}}));process.exit(1);",
    )
    with pytest.raises(a.AdditionalFailure):
        a.invoke(service.store, "login", OWNER, display=lambda *args: None)
    assert service.store.get("additional_connection", "linejs") is None


def test_cancel_success_race_not_saved(service, adapter):
    script(
        adapter,
        "console.log(JSON.stringify({event:'qr',value:'https://line.me/R/au/q/synthetic'}));console.log(JSON.stringify({event:'result',result:{connected:true},state:{owner:r.owner,token:'secret',storage:{}}}));",
    )
    dialog = SimpleNamespace(poll=lambda: 0)
    with pytest.raises(a.AdditionalFailure) as e:
        a.invoke(service.store, "login", OWNER, display=lambda *args: dialog)
    assert e.value.reason == "owner_cancelled_or_ui_failed"
    assert service.store.get("additional_connection", "linejs") is None


def test_invalid_unknown_error_sanitized(service, adapter):
    script(
        adapter,
        "console.log(JSON.stringify({event:'error',stage:'secret-token',reason:'secret-token'}));",
    )
    with pytest.raises(a.AdditionalFailure) as e:
        a.invoke(service.store, "login", OWNER, display=lambda *args: None)
    assert e.value.stage == "adapter" and e.value.reason == "upstream_failed"


def test_no_implicit_auth(service, adapter):
    with pytest.raises(a.AdditionalFailure) as e:
        a.invoke(service.store, "albums", OWNER)
    assert e.value.reason == "additional_connection_required"
    with pytest.raises(ValueError):
        a.invoke(service.store, "send", OWNER)


def test_auth_urls_not_arbitrary(monkeypatch):
    monkeypatch.setattr(a, "available", lambda: True)
    for kind, value in [
        ("qr", "https://example.com"),
        ("pin", "secret"),
        ("qr", "https://line.me.evil/R/au/q/a"),
    ]:
        with pytest.raises(a.AdditionalFailure):
            a.show_auth(kind, value)


def test_refreshed_state_survives_content_error(service, adapter):
    service.store.put(
        "additional_connection", "linejs", {"owner": OWNER, "token": "synthetic-old", "storage": {}}
    )
    script(
        adapter,
        "console.log(JSON.stringify({event:'error',stage:'content',reason:'transport_timeout',state:{owner:r.owner,token:'synthetic-new',storage:{}}}));",
    )
    with pytest.raises(a.AdditionalFailure) as error:
        a.invoke(service.store, "albums", OWNER)
    assert error.value.reason == "transport_timeout"
    assert service.store.get("additional_connection", "linejs")["token"] == "synthetic-new"
    assert service.store.get("additional_diagnostic", "last")["state"] == "failed"


def test_bad_exit_does_not_checkpoint_error_state(service, adapter):
    service.store.put(
        "additional_connection", "linejs", {"owner": OWNER, "token": "synthetic-old", "storage": {}}
    )
    script(
        adapter,
        "console.log(JSON.stringify({event:'error',stage:'content',reason:'transport_timeout',state:{owner:r.owner,token:'synthetic-new',storage:{}}}));process.exit(1);",
    )
    with pytest.raises(a.AdditionalFailure):
        a.invoke(service.store, "albums", OWNER)
    assert service.store.get("additional_connection", "linejs")["token"] == "synthetic-old"


def test_authentication_error_diagnostics_are_closed_and_persisted(service, adapter):
    service.store.put(
        "additional_connection", "linejs", {"owner": OWNER, "token": "synthetic-old", "storage": {}}
    )
    script(
        adapter,
        "console.log(JSON.stringify({event:'error',stage:'authentication',reason:'upstream_failed',diagnostic:{error_kind:'RequestError',line_code:8,http_status:200,token:'never-output',raw:'never-output'}}));",
    )
    with pytest.raises(a.AdditionalFailure) as e:
        a.invoke(service.store, "check", OWNER)
    assert e.value.diagnostic == {"error_kind": "RequestError", "line_code": 8, "http_status": 200}
    last = service.store.get("additional_diagnostic", "last")
    assert last["state"] == "failed" and last["line_code"] == 8
    assert "never-output" not in json.dumps(last)
    assert service.store.get("additional_connection", "linejs")["token"] == "synthetic-old"


def test_connect_requires_saved_state_reconnect(service, monkeypatch):
    import base64

    api = SimpleNamespace(
        tokens=SimpleNamespace(mid=OWNER),
        get_e2ee_public_keys_ex=lambda _: [
            {"keyId": 1, "keyData": base64.b64encode(b"x" * 32).decode()}
        ],
    )
    service.line.call = lambda f: f(api)
    monkeypatch.setattr(a, "available", lambda: True)
    calls = []

    def invoke(store, command, owner, **params):
        calls.append(command)
        if command == "check":
            raise a.AdditionalFailure(
                "additional_authentication", "upstream_failed", {"line_code": 8}
            )
        return {"connected": True}

    monkeypatch.setattr(a, "invoke", invoke)
    result = a.connect(service)
    assert calls == ["login", "check"] and not result["ok"] and result["line_code"] == 8
    monkeypatch.setattr(a, "invoke", lambda *a, **k: {"connected": True})
    assert a.connect(service)["reconnect_verified"] is True


def test_live_check_reports_actual_prefixed_device_rejection(service, monkeypatch):
    def fail(*args, **kwargs):
        raise a.AdditionalFailure("additional_authentication", "upstream_failed", {"line_code": 8})

    monkeypatch.setattr(a, "invoke", fail)
    result = a.check(service)
    assert not result["ok"] and not result["live_verified"]
    assert result["owner_authentication_required"] is True
    assert result["existing_connection_replaced"] is False


def test_explicit_device_selection_and_previous_state_preserved(service, adapter):
    old = {"owner": OWNER, "token": "synthetic-old", "storage": {}, "device": "DESKTOPMAC"}
    service.store.put("additional_connection", "linejs", old)
    script(
        adapter,
        "console.log(JSON.stringify({event:'result',result:{connected:true},state:{owner:r.owner,token:'synthetic-tablet',storage:{},device:r.device}}));",
    )
    assert a.invoke(service.store, "login", OWNER, device="IOSIPAD", display=lambda *args: None)[
        "connected"
    ]
    assert service.store.get("additional_connection", "linejs_previous") == old
    assert service.store.get("additional_connection", "linejs")["device"] == "IOSIPAD"
    assert a.status(service.store)["device"] == "IOSIPAD"
    with pytest.raises(ValueError, match="unsupported_additional_device"):
        a.connect(service, "ANDROID")


def test_reauthentication_defaults_to_saved_device(service, monkeypatch):
    import base64

    service.store.put("additional_connection", "linejs", {"device": "IOSIPAD"})
    service.line.call = lambda f: f(
        SimpleNamespace(
            tokens=SimpleNamespace(mid=OWNER),
            get_e2ee_public_keys_ex=lambda _: [
                {"keyId": 1, "keyData": base64.b64encode(b"x" * 32).decode()}
            ],
        )
    )
    monkeypatch.setattr(a, "available", lambda: True)
    calls = []

    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return {"connected": True}

    monkeypatch.setattr(a, "invoke", invoke)
    assert a.connect(service)["ok"]
    assert calls[0]["device"] == "IOSIPAD"
