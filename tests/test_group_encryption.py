from types import SimpleNamespace

import pytest
from okline.exceptions import LineApiError

from line_agent.diagnostics import DiagnosticError
from line_agent.group_encryption import install


def test_registration_rejection_99_is_bounded_without_message_send():
    calls = []
    def register(group):
        calls.append(group)
        if len(calls) < 3:
            raise LineApiError("synthetic_private", code=99, status=400)
        return (10, 20)
    api = SimpleNamespace(e2ee=SimpleNamespace(register_group_key=register))
    install(api)
    assert api.e2ee.register_group_key("synthetic_group") == (10, 20)
    assert len(calls) == 3


@pytest.mark.parametrize("code,expected", [(99, 4), (5, 1)])
def test_exhausted_or_other_rejection_never_falls_into_outer_send_retry(code, expected):
    calls = []
    def register(group):
        calls.append(group)
        raise LineApiError("synthetic_private", code=code, status=400)
    api = SimpleNamespace(e2ee=SimpleNamespace(register_group_key=register))
    install(api)
    with pytest.raises(DiagnosticError) as exc:
        api.e2ee.register_group_key("synthetic_group")
    assert exc.value.stage == "group_key_register" and exc.value.line_code == code
    assert "synthetic_private" not in str(exc.value)
    assert len(calls) == expected


def test_transport_failure_is_not_retried():
    calls = []
    def register(group):
        calls.append(group)
        raise TimeoutError("synthetic_private")
    api = SimpleNamespace(e2ee=SimpleNamespace(register_group_key=register))
    install(api)
    with pytest.raises(TimeoutError):
        api.e2ee.register_group_key("synthetic_group")
    assert len(calls) == 1
