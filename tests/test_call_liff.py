import pytest

from line_agent.service import ACTION_MODELS, action_context


def test_call_link_binds_owner_and_fresh_link_snapshot(service):
    owner, peer = service.line.ids[:2]
    p = {"mid": owner, "url_id": "synthetic-link", "title": "new"}
    first = action_context(
        "call_link_update",
        p,
        None,
        None,
        None,
        profile=lambda: {"mid": owner},
        call_link=lambda *a: {"title": "old"},
    )
    changed = action_context(
        "call_link_update",
        p,
        None,
        None,
        None,
        profile=lambda: {"mid": owner},
        call_link=lambda *a: {"title": "changed"},
    )
    assert first != changed
    with pytest.raises(ValueError, match="owner_mismatch"):
        action_context("call_link_update", p, None, None, None, profile=lambda: {"mid": peer})
    with pytest.raises(ValueError, match="call_link_read_unavailable"):
        action_context("call_link_update", p, None, None, None, profile=lambda: {"mid": owner})


def test_liff_closed_message_schema_and_bounds():
    model = ACTION_MODELS["liff_share"]
    base = {"group": "c" + "1" * 32, "liff_id": "1234567890-abc"}
    assert model.model_validate({**base, "messages": [{"type": "text", "text": "synthetic"}]})
    for messages in [
        [],
        [{"type": "text", "text": "x", "token": "secret"}],
        [{"type": "text", "text": "x" * 5001}],
        [{"type": "template", "template": {}}],
        [
            {
                "type": "image",
                "originalContentUrl": "http://example.com/x",
                "previewImageUrl": "https://example.com/x",
            }
        ],
    ]:
        with pytest.raises(ValueError):
            model.model_validate({**base, "messages": messages})


def test_call_link_unknown_result_uses_same_request(service):
    import uuid

    from line_agent.delegation import configure_owner, default_scope, local_principal

    owner = service.line.ids[0]
    service.store.put("setting", "identity", {"mid": owner})
    service.line.public_profile = lambda: {"mid": owner}
    service.line.call_link_snapshot = lambda *a: {
        "owner": owner,
        "url_id": "synthetic-link",
        "title": "old",
    }
    configure_owner(service, default_scope())
    request = str(uuid.uuid4())
    payload = {"mid": owner, "url_id": "synthetic-link", "title": "new"}
    action = service.prepare_once(request, "call_link_update", payload)
    service.line.fail_send = True
    assert (
        service.execute(action["id"], delegation_principal=local_principal(service))["state"]
        == "unknown"
    )
    assert service.prepare_once(request, "call_link_update", payload)["id"] == action["id"]
    with pytest.raises(ValueError):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert len(service.line.calls) == 1


def test_additional_closed_reason_survives_operation_result(service):
    import json

    from line_agent.additional_connection import AdditionalFailure
    from line_agent.delegation import configure_owner, default_scope, local_principal
    from line_agent.diagnostics import DiagnosticError, diagnostic_error
    from line_agent.line_content import ContentFailure

    owner = service.line.ids[0]
    service.store.put("setting", "identity", {"mid": owner})
    service.line.public_profile = lambda: {"mid": owner}
    service.line.call_link_snapshot = lambda *a: {"owner": owner}
    configure_owner(service, default_scope())
    action = service.prepare("call_link_create", {"mid": owner, "title": "synthetic"})

    def execute(kind, payload, *, preflight):
        preflight(service._context(kind, payload))
        raise ContentFailure("additional_content", "liff_consent_required")

    service.line.execute = execute
    result = service.execute(action["id"], delegation_principal=local_principal(service))
    assert result["state"] == "unknown"
    assert result["diagnostic"]["reason"] == "liff_consent_required"
    safe = diagnostic_error(
        "operation", AdditionalFailure("additional_content", "call_link_changed")
    )
    assert safe.reason == "call_link_changed"
    secret = diagnostic_error(
        "operation", AdditionalFailure("secret", "secret", {"message": "secret"})
    )
    assert "secret" not in json.dumps(vars(secret))
    action = service.prepare("call_link_create", {"mid": owner, "title": "before-start"})

    def reject(*args, **kwargs):
        raise AdditionalFailure("additional_content", "call_link_changed")

    service.line.execute = reject
    with pytest.raises(DiagnosticError) as error:
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert error.value.reason == "call_link_changed"
    assert service.action(action["id"])["state"] == "prepared"
