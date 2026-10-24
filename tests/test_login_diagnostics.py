from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from okline.exceptions import LineApiError, LineTransportError
from okline.transport import Tokens

from line_agent import cli
from line_agent.line import LineAdapter
from line_agent.store import Store


class SyntheticLogin:
    def __init__(self, *, fail_stage=None):
        self.fail_stage = fail_stage
        self.qr_attempts = 0
        self.closed = False
        self.tokens = Tokens()
        self.e2ee = SimpleNamespace(is_ready=lambda: False)
        self.transport = SimpleNamespace(call=self.transport_call)

    def transport_call(self, endpoint, *args, **kwargs):
        if endpoint.endswith("qrCodeLoginV2") and self.fail_stage == "issue":
            raise LineApiError(
                "synthetic private token and body",
                code=8,
                status=403,
                raw={"secret": "do not print"},
            )
        return {}

    def qr_login(self, on_qr, on_pin):
        self.qr_attempts += 1
        on_qr("synthetic-qr-do-not-log")
        self.transport.call("LoginQrCode.SecondaryQrCodeLoginService.qrCodeLoginV2", [])
        self.tokens.access_token = "synthetic-credential-never-from-user"
        self.tokens.mid = "u" + "a" * 32
        return SimpleNamespace(success=True)

    def get_profile(self):
        if self.fail_stage == "profile":
            raise LineTransportError("synthetic secret transport details", status=503)
        return {"mid": "u" + "a" * 32}

    def close(self):
        self.closed = True


def test_cli_failure_names_operation_and_stage_without_raw_error(monkeypatch, capsys):
    from line_agent.diagnostics import diagnostic_error

    def fail(args):
        raise diagnostic_error("qr_issue", LineApiError("do-not-print-this", code=8, status=403))

    monkeypatch.setattr(cli, "run", fail)
    monkeypatch.setattr("sys.argv", ["line-agent", "login"])
    with pytest.raises(SystemExit):
        cli.main()
    error = capsys.readouterr().err
    assert "operation=login" in error
    assert "stage=qr_issue" in error
    assert "http=403" in error
    assert "line_code=8" in error
    assert "do-not-print-this" not in error


def test_qr_issuer_failure_exposes_only_safe_diagnostic(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin(fail_stage="issue")
    monkeypatch.setattr(adapter, "_client", lambda *args: api)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: None, lambda pin: None)
    assert exc.value.stage == "qr_issue"
    assert exc.value.http_status == 403
    assert "private token" not in str(exc.value)
    assert adapter.store.get("credential", "session") is None
    assert api.closed


def test_valid_login_is_preserved_when_profile_check_fails_and_resumes_without_qr(
    tmp_path, monkeypatch
):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin(fail_stage="profile")
    monkeypatch.setattr(adapter, "_client", lambda *args: api)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: None, lambda pin: None)
    assert exc.value.stage == "profile_check"
    assert (
        adapter.store.get("credential", "session")["accessToken"]
        == "synthetic-credential-never-from-user"
    )
    api.fail_stage = None
    assert adapter.login(lambda qr: pytest.fail("must not request another QR"), lambda pin: None)[
        "connected"
    ]
    assert api.qr_attempts == 1


def test_expected_certificate_rejection_keeps_sdk_pin_fallback(tmp_path, monkeypatch):
    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin()
    original = api.qr_login

    def login(on_qr, on_pin):
        try:
            api.transport.call("LoginQrCode.SecondaryQrCodeLoginService.verifyCertificate", [])
        except LineApiError:
            on_pin("synthetic-pin")
        return original(on_qr, on_pin)

    old_call = api.transport.call

    def certificate_call(endpoint, *args, **kwargs):
        if endpoint.endswith("verifyCertificate"):
            raise LineApiError("expected certificate rejection", code=8)
        return old_call(endpoint, *args, **kwargs)

    api.transport.call = certificate_call
    api.qr_login = login
    monkeypatch.setattr(adapter, "_client", lambda *args: api)
    pins = []
    assert adapter.login(lambda qr: None, pins.append)["connected"]
    assert pins == ["synthetic-pin"]


def test_login_with_wrong_account_does_not_replace_existing_identity(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    owner = "u" + "b" * 32
    adapter.store.put("setting", "identity", {"mid": owner})
    monkeypatch.setattr(adapter, "_client", lambda *args: SyntheticLogin())
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: None, lambda pin: None)
    assert exc.value.reason == "identity_mismatch"
    assert adapter.store.get("setting", "identity")["mid"] == owner
    assert adapter.store.get("credential", "session") is None


@pytest.mark.parametrize(
    "upstream",
    [
        LineApiError(
            "synthetic_token=do-not-log", code=123456789, status=403, raw={"key": "private"}
        ),
        LineTransportError("https://private.example/?token=do-not-log", status=503),
        RuntimeError("synthetic_token=do-not-log"),
        AttributeError("synthetic_token=do-not-log"),
    ],
)
def test_diagnostic_cannot_include_identifiers_or_upstream_prose(upstream):
    from line_agent.diagnostics import diagnostic_error, format_diagnostic

    text = format_diagnostic("login", diagnostic_error("qr_issue", upstream))
    for forbidden in ("do-not-log", "private.example", "123456789", "synthetic_token", "raw"):
        assert forbidden not in text
    assert "stage=qr_issue" in text


def test_resumed_profile_failure_saves_rotated_tokens_without_new_login(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin()
    api.tokens.mid = "u" + "a" * 32
    api.tokens.access_token = "synthetic-old-access"
    api.tokens.refresh_token = "synthetic-old-refresh"
    adapter._save(api)
    adapter.store.put("setting", "identity", {"mid": api.tokens.mid})

    def rotated_then_failed():
        api.tokens.access_token = "synthetic-rotated-access"
        api.tokens.refresh_token = "synthetic-rotated-refresh"
        raise LineTransportError("synthetic timeout after refresh", status=503)

    api.get_profile = rotated_then_failed
    monkeypatch.setattr(adapter, "_client", lambda *args: api)
    with pytest.raises(DiagnosticError):
        adapter.login(lambda qr: pytest.fail("must not reauthenticate"), lambda pin: None)
    saved = adapter.store.get("credential", "session")
    assert saved["accessToken"] == "synthetic-rotated-access"
    assert saved["refreshToken"] == "synthetic-rotated-refresh"
    assert api.qr_attempts == 0


def test_checkpoint_save_failure_retains_correlated_trace(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin(fail_stage="profile")
    api.tokens.mid = "u" + "a" * 32
    api.tokens.access_token = "synthetic-access"
    adapter._save(api)
    adapter.store.put("setting", "identity", {"mid": api.tokens.mid})
    monkeypatch.setattr(adapter, "_client", lambda *args: api)

    def fail_save(*args):
        raise PermissionError("PRIVATE")

    monkeypatch.setattr(adapter, "_save", fail_save)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: pytest.fail("must not reauthenticate"), lambda pin: None)
    trace = adapter.store.get("login_trace", exc.value.login_trace_id)
    assert trace["stage"] == exc.value.stage == "session_save"
    assert trace["reason"] == exc.value.reason == "permission_denied"
    assert "PRIVATE" not in str(trace)


def test_explicit_key_repair_requires_fresh_qr_and_keeps_encrypted_recovery(tmp_path, monkeypatch):
    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    owner = "u" + "a" * 32
    old = {"mid": owner, "accessToken": "synthetic-old", "certificate": "synthetic-certificate"}
    adapter.store.put("credential", "session", old)
    adapter.store.put("setting", "identity", {"mid": owner})
    api = SyntheticLogin()
    material = {"mid": owner, "latestKeyId": 9, "keys": {"9": "synthetic-private-blob"}}
    api.e2ee = SimpleNamespace(is_ready=lambda: bool(api.e2ee.my_keys), my_keys={},
                               export_keys=lambda: material)
    original = api.qr_login
    def qr_login(on_qr, on_pin, **kw):
        assert kw == {"certificate": "synthetic-certificate"}
        result = original(on_qr, on_pin)
        api.e2ee.my_keys = {9: 100}
        return result
    api.qr_login = qr_login
    def client(session):
        assert session is None
        return api
    monkeypatch.setattr(adapter, "_client", client)
    result = adapter.login(lambda qr: None, lambda pin: None, repair_e2ee=True)
    assert result["e2ee_ready"] and not result["resumed_saved_login"]
    assert api.qr_attempts == 1
    assert adapter.store.get("credential", "session")["e2ee"] == material
    assert adapter.store.records("credential_recovery")[0]["value"] == old
    assert b"synthetic-private-blob" not in adapter.store.db.read_bytes()


def test_qr_repair_without_keys_is_an_error_and_preserves_token_checkpoint(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError
    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin()
    original = api.qr_login
    api.qr_login = lambda on_qr, on_pin, **kw: original(on_qr, on_pin)
    monkeypatch.setattr(adapter, "_client", lambda session: api)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: None, lambda pin: None, repair_e2ee=True)
    assert exc.value.reason == "e2ee_login_metadata_missing"
    assert adapter.store.get("credential", "session")["accessToken"]
    assert not adapter.store.get("setting", "e2ee_health")["ready"]


def test_failed_qr_repair_with_old_keys_retains_specific_qr_reason(tmp_path, monkeypatch):
    from line_agent.diagnostics import DiagnosticError
    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    owner = "u" + "a" * 32
    old_keys = {"mid": owner, "latestKeyId": 9, "keys": {"9": "synthetic-old-blob"}}
    adapter.store.put("credential", "session", {"mid": owner, "accessToken": "synthetic-old",
                                               "e2ee": old_keys})
    adapter.store.put("setting", "identity", {"mid": owner})
    api = SyntheticLogin()
    original = api.qr_login
    api.qr_login = lambda on_qr, on_pin, **kw: original(on_qr, on_pin)
    monkeypatch.setattr(adapter, "_client", lambda session: api)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda qr: None, lambda pin: None, repair_e2ee=True)
    assert exc.value.reason == "e2ee_login_metadata_missing"
    assert adapter.store.get("credential", "session")["e2ee"] == old_keys
    assert adapter.store.get("setting", "e2ee_health")["reason"] == exc.value.reason


def test_empty_repair_client_does_not_overwrite_previous_key_health(tmp_path):
    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    before = {"state": "ready", "ready": True, "key_count": 1}
    adapter.store.put("setting", "e2ee_health", before)
    api = adapter._client(None)
    api.close()
    assert adapter.store.get("setting", "e2ee_health") == before


def test_login_scan_wait_trace_and_http_timeout_do_not_leak_private_values(tmp_path, monkeypatch):
    import json

    from requests.exceptions import ReadTimeout

    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    api = SyntheticLogin()
    timeouts = []
    def send(method, url, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        try:
            raise ReadTimeout("synthetic-private-token-url")
        except ReadTimeout as error:
            raise LineTransportError("synthetic-private-body") from error
    api.transport._send = send
    def call(endpoint, *args, **kwargs):
        return api.transport._send('POST', 'https://synthetic.private',
                                   headers={'X-LST': '110000', 'token': 'synthetic-private-token'})
    api.transport.call = call
    def qr_login(on_qr, on_pin, **kwargs):
        on_qr('synthetic-private-qr')
        api.transport.call('LoginQrCode.SecondaryQrCodeLoginPermitNoticeService.checkQrCodeVerified')
    api.qr_login = qr_login
    monkeypatch.setattr(adapter, '_client', lambda session: api)
    with pytest.raises(DiagnosticError):
        adapter.login(lambda qr: None, lambda pin: None, repair_e2ee=True)
    trace = adapter.store.records('login_trace')[0]['value']
    assert trace['stage'] == 'qr_scan_wait' and trace['failure_kind'] == 'read_timeout'
    assert trace['events'][0]['kind'] == 'read_timeout'
    assert timeouts == [(10, 125)]
    assert 'synthetic-private' not in json.dumps(trace)
