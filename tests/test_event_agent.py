"""Synthetic accounts only; no network or real LINE sends."""

import threading
import time

import pytest

from line_agent.event_agent import (
    configure,
    enqueue,
    process,
    status,
    valid_decision,
    watch_database,
)
from line_agent.realtime import event_messages

OWNER = "u" + "f" * 32
GROUP = "c" + "a" * 32
OTHER = "c" + "b" * 32


def setup(service, *, replies=True):
    service.store.put("setting", "identity", {"mid": OWNER})
    service.store.put("setting", "event_cursor", {"revision": 0})
    configure(
        service.store, GROUP, instructions="架空の試験。通常の質問のみ。", allow_replies=replies
    )
    policy = service.store.get("event_policy", GROUP)
    policy["enabled_at"] -= 1
    service.store.put("event_policy", GROUP, policy, scope=GROUP)
    msg = {
        "id": "100",
        "from": service.line.ids[0],
        "to": GROUP,
        "createdTime": int(time.time() * 1000),
        "contentType": 0,
        "text": "架空のテスト質問",
        "decrypted": True,
    }
    service.line.messages = [msg]
    return msg


def receive(service, msg, revision=1):
    return event_messages(
        service,
        {
            "event": "message",
            "data": {"operations": [{"revision": revision, "type": 26, "message": msg}]},
        },
    )


class Model:
    def __init__(self, value=None, hook=None):
        self.value = value or {"action": "reply", "text": "どの箇所をご希望ですか？", "task": None}
        self.calls = []
        self.hook = hook

    def infer(self, doc):
        self.calls.append(doc)
        if self.hook:
            self.hook()
        return self.value


def test_full_synthetic_event_to_formal_send_callback_once(service):
    msg = setup(service)
    receive(service, msg)
    sent = []

    def send(*args):
        sent.append(args)
        return {"state": "sent", "external_id": "synthetic-outgoing"}

    model = Model()
    process(service, model, send)
    receive(service, msg, 2)
    process(service, model, send)
    assert len(sent) == len(model.calls) == 1
    assert sent[0][1:] == (GROUP, "どの箇所をご希望ですか？", "100")
    assert status(service.store, GROUP)["items"][0]["state"] == "sent"
    assert status(service.store, GROUP)["host_chat_push"] is False


def test_unconfigured_group_self_and_pre_enable_are_not_queued(service):
    msg = setup(service)
    for changed in ({"to": OTHER}, {"from": OWNER}, {"createdTime": 1}):
        with service.store.atomic() as tx:
            enqueue(
                tx, GROUP if changed.get("to") != OTHER else OTHER, {**msg, **changed}, time.time()
            )
    assert service.store.count("event_job") == 0


def test_receive_failure_rolls_back_dispatch_job_too(service, monkeypatch):
    msg = setup(service)
    original = service.store._encode

    def fail(key, value):
        if key == "event_cursor":
            raise OSError("synthetic disk failure")
        return original(key, value)

    monkeypatch.setattr(service.store, "_encode", fail)
    with pytest.raises(OSError):
        receive(service, msg)
    assert service.store.count("event_job") == service.store.count("delivery") == 0


def test_owner_replies_before_or_during_inference_stop_send(service):
    msg = setup(service)
    receive(service, msg)

    def owner_reply():
        service.line.messages.append(
            {**msg, "id": "101", "from": OWNER, "createdTime": msg["createdTime"] + 1}
        )

    model = Model(hook=owner_reply)
    process(service, model, lambda *a: pytest.fail("must not send"))
    assert status(service.store, GROUP)["items"][0]["state"] == "superseded"


def test_policy_revoked_during_inference_stops_send(service):
    msg = setup(service)
    receive(service, msg)
    model = Model(hook=lambda: configure(service.store, GROUP, enabled=False))
    process(service, model, lambda *a: pytest.fail("must not send"))
    assert status(service.store, GROUP)["items"][0]["state"] == "superseded"


def test_unknown_send_not_retried_after_restart(service):
    msg = setup(service)
    receive(service, msg)
    sent = []

    def send(*args):
        sent.append(args)
        raise TimeoutError("synthetic ambiguous send")

    process(service, Model(), send)
    process(service, Model(), send)
    assert len(sent) == 1
    item = status(service.store, GROUP)["items"][0]
    assert item["state"] == "owner_required" and item["failure_stage"] == "send"
    assert item["request_id"] == sent[0][0]


def test_interrupted_inference_is_not_silently_rerun(service):
    msg = setup(service)
    receive(service, msg)
    key = GROUP + ":100"
    job = service.store.get("event_job", key)
    service.store.put("event_job", key, {**job, "state": "processing"}, scope=GROUP)
    model = Model()
    process(service, model, lambda *a: pytest.fail("must not send"))
    assert not model.calls
    assert status(service.store, GROUP)["items"][0]["reason"] == "event_agent_interrupted"


def test_site_change_handoff_never_claims_completion_or_sends(service):
    msg = setup(service)
    receive(service, msg)
    model = Model({"action": "site_task", "text": None, "task": "架空の写真差替えを確認する"})
    process(service, model, lambda *a: pytest.fail("must not send"))
    assert status(service.store, GROUP)["items"][0]["state"] == "site_task"


def test_drafts_permission_is_not_automatic_reply_permission(service):
    msg = setup(service, replies=False)
    receive(service, msg)
    process(service, Model(), lambda *a: pytest.fail("must not send"))
    assert status(service.store, GROUP)["items"][0]["state"] == "owner_required"


def test_omitted_history_is_error_not_silent_no_reply(service):
    msg = setup(service)
    receive(service, msg)
    service.line.messages = []
    process(service, Model(), lambda *a: pytest.fail("must not send"))
    item = status(service.store, GROUP)["items"][0]
    assert item["state"] == "owner_required" and item["reason"] == "event_agent_trigger_unavailable"


@pytest.mark.parametrize(
    "text", ["https://example.com", "反映しました", "パスワードを送って", "DNSを変更してください"]
)
def test_forbidden_outgoing_content_requires_review(text):
    with pytest.raises(ValueError):
        valid_decision({"action": "reply", "text": text, "task": None})


def test_model_cannot_choose_recipient_or_tool():
    with pytest.raises(ValueError):
        valid_decision({"action": "reply", "text": "架空", "task": None, "to": OTHER})


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS kqueue path")
def test_real_filesystem_event_wakes_handler_without_polling(service):
    stop = threading.Event()
    observed = threading.Event()
    ready = threading.Event()

    def handle():
        if service.store.get("setting", "synthetic-event"):
            observed.set()
            stop.set()
        ready.set()

    thread = threading.Thread(target=watch_database, args=(service.store, stop, handle))
    thread.start()
    try:
        assert ready.wait(3)
        service.store.put("setting", "synthetic-event", {"value": True})
        assert observed.wait(2), "filesystem event did not wake handler"
    finally:
        stop.set()
        thread.join(4)
    assert not thread.is_alive()


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS kqueue path")
def test_idle_reads_do_not_create_self_wakeup_loop(service):
    stop = threading.Event()
    ready = threading.Event()
    calls = []

    def handle():
        calls.append(time.monotonic())
        service.store.records("event_job")
        ready.set()

    thread = threading.Thread(target=watch_database, args=(service.store, stop, handle))
    thread.start()
    try:
        assert ready.wait(2)
        time.sleep(0.3)
        assert len(calls) <= 2
    finally:
        stop.set()
        thread.join(4)
    assert not thread.is_alive()


def test_old_incoming_is_review_only_after_offline_recovery(service):
    msg = setup(service)
    receive(service, msg)
    key = GROUP + ":100"
    job = service.store.get("event_job", key)
    service.store.put("event_job", key, {**job, "received_at": time.time() - 601}, scope=GROUP)
    model = Model()
    process(service, model, lambda *a: pytest.fail("must not send"))
    assert not model.calls
    assert status(service.store, GROUP)["items"][0]["reason"] == "event_agent_stale_incoming"


def test_watch_failure_reason_survives_finally(service, monkeypatch, tmp_path):
    import line_agent.event_agent as module
    import line_agent.store as store_module

    monkeypatch.setattr(store_module, "open_store", lambda *a: service.store)
    monkeypatch.setattr(module, "CodexEventModel", lambda *a: object())

    def fail(*args):
        raise OSError("synthetic watcher failure")

    monkeypatch.setattr(module, "watch_database", fail)
    monkeypatch.setattr(
        module.sys, "argv", ["event-agent", "--home", str(tmp_path), "--root", str(tmp_path), "run"]
    )
    with pytest.raises(SystemExit):
        module.main()
    assert service.store.get("setting", "event_agent_runtime")["state"] == "error"
    assert (
        service.store.get("setting", "event_agent_runtime")["reason"] == "event_agent_watch_failed"
    )


def test_policy_stop_serializes_with_already_started_send(service):
    msg = setup(service)
    receive(service, msg)
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    sent = []

    def send(*args):
        entered.set()
        assert release.wait(3)
        sent.append(args)
        return {"state": "sent", "external_id": "synthetic-sent"}

    worker = threading.Thread(target=process, args=(service, Model(), send))
    worker.start()
    assert entered.wait(3)

    def revoke():
        configure(service.store, GROUP, enabled=False)
        revoked.set()

    revoker = threading.Thread(target=revoke)
    revoker.start()
    try:
        assert not revoked.wait(0.1)
    finally:
        release.set()
        worker.join(4)
        revoker.join(4)
    assert revoked.is_set() and len(sent) == 1
    assert service.store.get("event_policy", GROUP)["enabled"] is False
    process(service, Model(), lambda *args: pytest.fail("must not send again"))


HOST = "00000000-0000-4000-8000-000000000001"


def test_host_queue_routes_once_without_model_or_line_send(service):
    msg = setup(service, replies=False)
    configure(service.store, GROUP, instructions="架空の試験", host_thread=HOST)
    policy = service.store.get("event_policy", GROUP)
    policy["enabled_at"] -= 1
    service.store.put("event_policy", GROUP, policy, scope=GROUP)
    receive(service, msg)
    calls = []

    def notify(thread, message, chat):
        calls.append((thread, message, chat))
        return {"queue_id": HOST}

    model = Model()

    def forbidden(*a):
        raise AssertionError("LINE must not send")

    process(service, model, forbidden, notify=notify)
    receive(service, msg, 2)
    process(service, model, forbidden, notify=notify)
    assert calls == [(HOST, "100", GROUP)]
    assert not model.calls
    assert status(service.store, GROUP)["host_chat_push"] is True
    assert status(service.store, GROUP)["items"][0]["state"] == "host_queued"


def test_host_queue_unknown_does_not_retry(service):
    msg = setup(service, replies=False)
    configure(service.store, GROUP, instructions="架空の試験", host_thread=HOST)
    policy = service.store.get("event_policy", GROUP)
    policy["enabled_at"] -= 1
    service.store.put("event_policy", GROUP, policy, scope=GROUP)
    receive(service, msg)
    calls = []

    def unknown(*args):
        calls.append(args)
        raise TimeoutError()

    process(service, Model(), None, notify=unknown)
    process(service, Model(), None, notify=unknown)
    assert len(calls) == 1
    assert status(service.store, GROUP)["items"][0]["next_check"] == "host_thread_history"


@pytest.mark.parametrize("target,replies", [("bad", False), (HOST, True)])
def test_host_target_is_privileged_and_exclusive(service, target, replies):
    setup(service)
    with pytest.raises(ValueError, match="event_agent_invalid_host_target"):
        configure(
            service.store, GROUP, instructions="架空", host_thread=target, allow_replies=replies
        )


def test_official_queue_uses_fixed_argv_without_body(monkeypatch):
    import subprocess

    from line_agent.event_agent import queue_host_notification

    seen = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")

    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(
            args, 0, f"Queued message {HOST} for thread {HOST}.\n", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert queue_host_notification(HOST, "123", GROUP)["queue_id"] == HOST
    assert seen[0][:5] == ["/usr/bin/codex", "queue", "--thread", HOST, "--message"]
    assert "message_id=123" in seen[0][5]
    assert f"chat={GROUP}" in seen[0][5]


@pytest.mark.parametrize("reason", ["age", "budget"])
def test_host_queue_respects_age_and_budget(service, reason):
    msg = setup(service, replies=False)
    configure(service.store, GROUP, instructions="架空の試験", host_thread=HOST, max_daily=1)
    policy = service.store.get("event_policy", GROUP)
    policy["enabled_at"] -= 1
    service.store.put("event_policy", GROUP, policy, scope=GROUP)
    receive(service, msg)
    if reason == "age":
        row = service.store.records("event_job")[0]
        job = row["value"]
        job["received_at"] -= 601
        service.store.put("event_job", row["id"], job, scope=GROUP)
    else:
        key = f"host:{GROUP}:{time.strftime('%Y-%m-%d', time.gmtime())}"
        service.store.put("event_budget", key, {"count": 1})

    def forbidden(*args):
        raise AssertionError("notification must be limited")

    process(service, Model(), None, notify=forbidden)
    item = status(service.store, GROUP)["items"][0]
    assert item["state"] == "owner_required"
    assert item["reason"] == (
        "event_agent_stale_incoming" if reason == "age" else "event_agent_daily_limit"
    )
