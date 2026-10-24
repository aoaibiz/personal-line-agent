"""Synthetic keys only; real SDK restore and encrypted SQLite checkpoint paths."""
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from okline.e2ee import E2EEManager
from okline.transport import Tokens

from line_agent.diagnostics import DiagnosticError
from line_agent.e2ee_state import login_poll_timeout, restore, transport_kind
from line_agent.line import LineAdapter
from line_agent.store import Store

OWNER = "u" + "a" * 32
MATERIAL = {"mid": OWNER, "latestKeyId": 1, "keys": {"1": "synthetic-key-blob"}}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "synthetic", Fernet.generate_key())


def api_with_bridge(importer):
    api = SimpleNamespace(tokens=Tokens(access_token="synthetic-new-token", mid=OWNER),
                          transport=SimpleNamespace(bridge=SimpleNamespace(e2ee_load_key=importer)))
    api.e2ee = E2EEManager(api)
    return api


def test_real_sdk_restore_then_resave_across_two_connections(store):
    # E2EEManager's real imported-blob cache must preserve loaded keys without
    # calling the WASM's unsupported re-export of an imported key.
    store.put("credential", "session", {"mid": OWNER, "accessToken": "synthetic-old-token",
                                        "e2ee": MATERIAL})
    adapter = LineAdapter(store)
    for _ in range(2):
        api = api_with_bridge(lambda blob: 42)
        restore(api, store.get("credential", "session"), store)
        assert api.e2ee.is_ready() and set(api.e2ee.my_keys) == {1}
        adapter._save(api)
    assert store.get("credential", "session")["e2ee"] == MATERIAL
    assert store.get("setting", "e2ee_health")["ready"]
    assert b"synthetic-key-blob" not in store.db.read_bytes()


def test_sdk_silent_partial_restore_is_rejected_without_overwrite(store):
    material = {**MATERIAL, "keys": {"1": "synthetic-good", "2": "synthetic-bad"}}
    original = {"mid": OWNER, "e2ee": material}
    store.put("credential", "session", original)
    def importer(blob):
        if blob == "synthetic-bad":
            raise RuntimeError("synthetic-sensitive-upstream-error")
        return 42
    with pytest.raises(DiagnosticError) as exc:
        restore(api_with_bridge(importer), original, store)
    assert exc.value.reason == "e2ee_restore_failed"
    assert "sensitive" not in str(exc.value)
    assert store.get("credential", "session") == original


def test_export_failure_keeps_old_keys_and_rotated_tokens(store):
    store.put("credential", "session", {"mid": OWNER, "accessToken": "synthetic-old-token",
                                        "e2ee": MATERIAL})
    api = api_with_bridge(lambda blob: 42)
    api.e2ee.my_keys = {1: 42}
    api.e2ee.latest_key_id = 1
    api.e2ee.export_keys = lambda: {"mid": OWNER, "latestKeyId": 1, "keys": {}}
    with pytest.raises(DiagnosticError) as exc:
        LineAdapter(store)._save(api)
    assert exc.value.reason == "e2ee_export_failed"
    saved = store.get("credential", "session")
    assert saved["e2ee"] == MATERIAL
    assert saved["accessToken"] == "synthetic-new-token"


def test_unready_process_cannot_erase_saved_keys(store):
    store.put("credential", "session", {"mid": OWNER, "e2ee": MATERIAL})
    with pytest.raises(DiagnosticError):
        LineAdapter(store)._save(api_with_bridge(lambda blob: 42))
    assert store.get("credential", "session")["e2ee"] == MATERIAL


def test_other_account_cannot_replace_saved_session(store):
    before = {"mid": "u" + "b" * 32, "accessToken": "synthetic-original"}
    store.put("credential", "session", before)
    with pytest.raises(DiagnosticError) as exc:
        LineAdapter(store)._save(api_with_bridge(lambda blob: 42))
    assert exc.value.reason == "e2ee_identity_mismatch"
    assert store.get("credential", "session") == before


def test_missing_keys_has_explicit_health_without_invented_readiness(store):
    restore(api_with_bridge(lambda blob: 42), {"mid": OWNER}, store)
    assert store.get("setting", "e2ee_health")["state"] == "missing"


def test_ciphertext_noop_cannot_be_reported_as_decrypted(store):
    raw = {"id": "synthetic-message", "chunks": ["synthetic-ciphertext"], "text": "placeholder"}
    result = LineAdapter._messages(SimpleNamespace(decrypt_message=lambda msg: msg), [raw])[0]
    assert not result["decrypted"]
    assert "text" not in result and "chunks" not in result
    assert result["decryption_error"]["reason"] == "message_decryption_failed"


def test_login_long_poll_read_timeout_outlives_server_wait():
    assert login_poll_timeout({"X-LST": "110000"}) == (10, 125)
    assert login_poll_timeout({"X-LST": "10000"}) == (10, 30)
    assert login_poll_timeout({}) is None
    assert login_poll_timeout({"X-LST": "synthetic-secret"}) is None
    assert login_poll_timeout({"X-LST": "999999999"}) is None


def test_network_diagnostic_walks_wrappers_without_logging_prose():
    from okline.exceptions import LineTransportError
    from requests.exceptions import ReadTimeout
    root = ReadTimeout("https://synthetic.private/?token=never-log")
    error = LineTransportError("synthetic-private-body")
    error.__cause__ = root
    assert transport_kind(error) == "read_timeout"
