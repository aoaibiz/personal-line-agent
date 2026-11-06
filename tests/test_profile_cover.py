import pytest

from line_agent.service import action_context


def test_cover_context_binds_owner_and_previous_cover(service):
    owner, other = service.line.ids[:2]
    args = ("set_profile_background", {"mid": owner}, None, None, None)
    old = action_context(*args, profile=lambda: {"mid": owner}, cover=lambda: {"object_id": "old"})
    new = action_context(*args, profile=lambda: {"mid": owner}, cover=lambda: {"object_id": "new"})
    assert old != new
    with pytest.raises(ValueError, match="owner_mismatch"):
        action_context(*args, profile=lambda: {"mid": other}, cover=lambda: {})
    with pytest.raises(ValueError, match="cover_read_unavailable"):
        action_context(*args, profile=lambda: {"mid": owner})


def test_cover_unknown_can_be_reconciled_without_write(service, tmp_path):
    import asyncio
    import uuid

    from line_agent.additional_connection import AdditionalFailure
    from line_agent.delegation import configure_owner, default_scope, local_principal
    from line_agent.media import stage_file
    from line_agent.server import create_server

    owner = service.line.ids[0]
    service.store.put("setting", "identity", {"mid": owner})
    service.line.public_profile = lambda: {"mid": owner}
    service.line.cover_snapshot = lambda: {"owner": owner, "object_id": "previous"}
    file = tmp_path / "synthetic.png"
    file.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    media = stage_file(service.store, file)
    configure_owner(service, default_scope())
    request = str(uuid.uuid4())
    payload = {"mid": owner, "media_id": media["id"]}
    action = service.prepare_once(request, "set_profile_background", payload)
    intended = action["payload"]["cover_object_id"]
    assert len(intended) == 32
    assert (
        service.prepare_once(request, "set_profile_background", payload)["payload"][
            "cover_object_id"
        ]
        == intended
    )

    def execute(kind, payload, *, preflight):
        preflight(service._context(kind, payload))
        raise AdditionalFailure("additional_content", "cover_apply_unconfirmed")

    service.line.execute = execute
    result = service.execute(action["id"], delegation_principal=local_principal(service))
    assert result["state"] == "unknown"
    assert result["diagnostic"]["reason"] == "cover_apply_unconfirmed"
    service.line.cover_snapshot = lambda: {"owner": owner, "object_id": intended}

    async def check():
        server = create_server(service)
        result = (await server.call_tool("profile_background_reconcile", {"request_id": request}))[
            1
        ]
        assert result["current_match"] == "applied"
        assert result["retried"] is False
        assert result["operation_state"] == "unknown"

    asyncio.run(check())
    assert service.action(action["id"])["state"] == "unknown"
    assert not service.line.calls
