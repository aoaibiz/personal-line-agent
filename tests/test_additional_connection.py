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
