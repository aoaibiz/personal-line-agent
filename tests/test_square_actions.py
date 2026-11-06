import uuid

import pytest

from line_agent.delegation import configure_owner, default_scope, local_principal

TARGET = "m" + "1" * 32


def ready(service):
    service.store.put("setting", "identity", {"mid": service.line.ids[0]})
    service.line.square_snapshot = lambda kind, payload: {
        "target": payload["target"],
        "member": "p" + "2" * 32,
        "revision": "1",
    }


def test_ordinary_all_targets_does_not_authorize_square(service):
    ready(service)
    scope = default_scope()
    configure_owner(service, scope)
    action = service.prepare("square_send_text", {"target": TARGET, "text": "synthetic"})
    with pytest.raises(ValueError, match="target_out_of_scope"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls


def test_square_specific_scope_and_unknown_not_replayed(service):
    ready(service)
    scope = default_scope()
    scope["square_targets"] = [TARGET]
    configure_owner(service, scope)
    request = str(uuid.uuid4())
    payload = {"target": TARGET, "text": "synthetic"}
    action = service.prepare_once(request, "square_send_text", payload)
    service.line.fail_send = True
    assert (
        service.execute(action["id"], delegation_principal=local_principal(service))["state"]
        == "unknown"
    )
    assert service.prepare_once(request, "square_send_text", payload)["id"] == action["id"]
    with pytest.raises(ValueError):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert len(service.line.calls) == 1


def test_square_target_namespace_and_explicit_block(service):
    ready(service)
    for target in [service.line.ids[0], "s" + "2" * 32, "c" + "3" * 32]:
        with pytest.raises(ValueError):
            service.prepare("square_send_text", {"target": target, "text": "x"})
    scope = default_scope()
    scope["square_targets"] = [TARGET]
    scope["blocked_square_targets"] = [TARGET]
    configure_owner(service, scope)
    action = service.prepare("square_leave_chat", {"target": TARGET})
    with pytest.raises(ValueError, match="target_blocked"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls
