"""Pinned OkLine adapter. Credentials stay in the encrypted account store."""

from __future__ import annotations

import logging
import os
import time

from .counts import contact_counts, group_counts, nonnegative_integer
from .identifiers import id_kind
from .platform_security import find_executable
from .store import Store


class LineAdapter:
    def __init__(self, store: Store):
        self.store = store

    def _client(self, session=None):
        # OkLine has an environment-controlled raw response debug path.
        os.environ.pop("OKLINE_DEBUG", None)
        import okline.transport
        from okline import OkLine
        from okline.transport import LineConfig

        okline.transport._DEBUG = False
        logging.getLogger("okline").setLevel(logging.CRITICAL + 1)
        node = find_executable(os.environ.get("LINE_NODE") or "node")
        if node is None:
            from .diagnostics import DiagnosticError
            raise DiagnosticError("sdk_init", "dependency_missing")
        data = session or {}
        api = OkLine(
            access_token=data.get("accessToken"),
            refresh_token=data.get("refreshToken"),
            certificate=data.get("certificate"),
            mid=data.get("mid"),
            record=False,
            config=LineConfig(max_retries=0, timeout=30, node_path=node),
        )
        from .e2ee_state import restore

        try:
            if session is not None:
                restore(api, data, self.store)
        except Exception:
            api.close()
            raise

        # Allocate before each actual write. The enclosing account lock serializes callers.
        def next_sequence():
            counter = self.store.get("setting", "sequence") or {"next": 1}
            value = counter["next"]
            if value >= 2**31 - 1:
                raise RuntimeError("リクエスト連番の上限です。接続を停止して調査してください")
            self.store.put("setting", "sequence", {"next": value + 1})
            return value

        api.next_req_seq = next_sequence
        from .group_encryption import install
        install(api)
        return api

    def _save(self, api):
        from .e2ee_state import health, session_for_save

        previous = self.store.get("credential", "session")
        session, failure = session_for_save(api, previous)
        self.store.put("credential", "session", session)
        if failure:
            health(self.store, "error", reason=failure.reason)
            raise failure
        keys = (session.get("e2ee") or {}).get("keys") or {}
        health(self.store, "ready" if keys else "missing", count=len(keys),
               reason=None if keys else "e2ee_keys_missing")

    def login(self, on_qr, on_pin, *, repair_e2ee=False, confirmation_gate=None):
        """Owner-only login, with safe stages and a durable authenticated checkpoint."""
        from uuid import uuid4

        from .diagnostics import DiagnosticError, diagnostic_error
        from .e2ee_state import login_poll_timeout, transport_kind

        stages = {
            "createSession": "qr_session_create",
            "createQrCode": "qr_code_create",
            "checkQrCodeVerified": "qr_scan_wait",
            "verifyCertificate": "qr_certificate",
            "createPinCode": "qr_pin_create",
            "checkPinCodeVerified": "qr_pin_wait",
            "qrCodeLoginV2": "qr_issue",
            "getProfile": "profile_check",
        }
        with self.store.exclusive():
            stage, api = "login_state", None
            session, previous = None, None
            trace_id = uuid4().hex[:16]
            trace = {"operation_id": trace_id, "started_at": time.time(), "state": "starting",
                     "repair_e2ee": repair_e2ee, "events": []}

            def record_trace(**fields):
                trace.update(fields)
                try:
                    self.store.put("login_trace", trace_id, trace)
                except Exception:
                    pass  # A diagnostic write must not hide the original failure.

            record_trace()
            try:
                session = self.store.get("credential", "session")
                previous = self.store.get("setting", "identity")
                if session and not session.get("accessToken"):
                    raise DiagnosticError(stage, "invalid_saved_session")
                if session and previous and session.get("mid") != previous["mid"]:
                    raise DiagnosticError(stage, "identity_mismatch")
                stage = "sdk_init"
                # Repair is an explicit owner QR flow, never an automatic retry.
                # Preserve the prior encrypted checkpoint before replacing a session.
                if repair_e2ee and session:
                    self.store.put("credential_recovery", uuid4().hex, session)
                api = self._client(None if repair_e2ee else session)
                original_call = api.transport.call
                issuing_started = False

                def observe(endpoint, *args, **kwargs):
                    nonlocal stage, issuing_started
                    phase = stages.get(endpoint.rsplit(".", 1)[-1])
                    if phase:
                        stage = phase
                    if confirmation_gate and not issuing_started:
                        confirmation_gate(wait=phase in {"qr_certificate", "qr_pin_create", "qr_issue"})
                    if phase == "qr_issue":
                        # Once issuance starts, finish the authenticated checkpoint
                        # even if the host disconnects; never discard rotated tokens.
                        issuing_started = True
                    began = time.monotonic()
                    record_trace(state="waiting", stage=stage, checked_at=time.time())
                    try:
                        value = original_call(endpoint, *args, **kwargs)
                    except Exception as error:
                        safe = diagnostic_error(stage, error)
                        trace["events"].append({"stage": stage, "outcome": "error",
                            "kind": transport_kind(error), "http_status": safe.http_status,
                            "line_code": safe.line_code,
                            "elapsed_seconds": round(time.monotonic()-began, 3)})
                        record_trace()
                        if confirmation_gate and not issuing_started:
                            confirmation_gate(wait=False)
                        # Preserve the SDK's certificate/PIN and polling fallback.
                        raise
                    trace["events"].append({"stage": stage, "outcome": "ok",
                        "elapsed_seconds": round(time.monotonic()-began, 3)})
                    record_trace()
                    if confirmation_gate and not issuing_started:
                        confirmation_gate(wait=False)
                    return value

                api.transport.call = observe
                original_send = getattr(api.transport, "_send", None)
                if original_send:
                    def login_send(method, url, **kwargs):
                        if stage in {"qr_scan_wait", "qr_pin_wait"}:
                            timeout = login_poll_timeout(kwargs.get("headers"))
                            if timeout:
                                kwargs["timeout"] = timeout
                        return original_send(method, url, **kwargs)
                    api.transport._send = login_send
                if not session or repair_e2ee:

                    def render(qr):
                        nonlocal stage
                        stage = "qr_render"
                        on_qr(qr)
                        stage = "qr_scan_wait"

                    stage = "qr_session_create"
                    if repair_e2ee:
                        result = api.qr_login(on_qr=render, on_pin=on_pin,
                            certificate=(session or {}).get("certificate"))
                    else:
                        result = api.qr_login(on_qr=render, on_pin=on_pin)
                    if confirmation_gate and not issuing_started:
                        confirmation_gate(wait=True)
                    if not result.success or not api.tokens.access_token:
                        raise DiagnosticError("qr_issue", "invalid_login_result")
                    if previous and api.tokens.mid != previous["mid"]:
                        raise DiagnosticError("qr_issue", "identity_mismatch")
                    if repair_e2ee and not api.e2ee.is_ready():
                        from .e2ee_state import health

                        reason = ("e2ee_login_unwrap_failed" if
                            getattr(getattr(api, "auth", None), "last_e2ee_login", None)
                            else "e2ee_login_metadata_missing")
                        # Preserve newly issued/rotated tokens even when E2EE failed.
                        # Any old key blobs remain encrypted in the same checkpoint.
                        try:
                            self._save(api)
                        except DiagnosticError as error:
                            # _save checkpointed the rotated tokens and retained
                            # the old keys; keep the actual QR failure as cause.
                            if error.stage != "e2ee_save" or error.reason != "e2ee_restore_failed":
                                raise
                        health(self.store, "error", reason=reason)
                        raise DiagnosticError("e2ee_login", reason)
                    # Do not discard valid tokens if a later profile read fails.
                    if not previous or api.tokens.mid == previous["mid"]:
                        stage = "session_save"
                        self._save(api)
                stage = "profile_check"
                profile = api.get_profile()
                mid = profile.get("mid") if isinstance(profile, dict) else None
                if not mid:
                    raise DiagnosticError(stage, "unsupported_schema")
                if previous and previous["mid"] != mid:
                    raise DiagnosticError(stage, "identity_mismatch")
                api.tokens.mid = mid
                stage = "session_save"
                self._save(api)
                stage = "identity_save"
                self.store.put("setting", "identity", {"mid": mid, "verified_at": time.time()})
                from .delegation import initialize_default
                from .service import AgentService
                initialize_default(AgentService(self.store, self))
                record_trace(state="complete", stage="identity_save", finished_at=time.time(),
                             e2ee_ready=api.e2ee.is_ready())
                return {"connected": True, "resumed_saved_login": bool(session) and not repair_e2ee,
                        "e2ee_ready": api.e2ee.is_ready(),
                        "readiness": "encrypted_messages" if api.e2ee.is_ready() else "metadata_only"}
            except Exception as error:
                failure = diagnostic_error(stage, error)
                if stage in {"qr_scan_wait", "qr_pin_wait"} and failure.http_status == 410:
                    failure = DiagnosticError(stage, "authentication_phone_confirmation_timeout",
                                              http_status=410, line_code=failure.line_code)
                failure.login_trace_id = trace_id
                record_trace(state="error", stage=failure.stage, reason=failure.reason,
                    failure_kind=transport_kind(error), finished_at=time.time())
                # A successful refresh can rotate a single-use token before the retried
                # profile request fails. Persist it without crossing the saved account.
                if (
                    session
                    and api is not None
                    and stage == "profile_check"
                    and failure.reason != "identity_mismatch"
                    and api.tokens.access_token
                    and api.tokens.mid == session.get("mid")
                    and (not previous or api.tokens.mid == previous["mid"])
                ):
                    try:
                        self._save(api)
                    except Exception as save_error:
                        save_failure = diagnostic_error("session_save", save_error)
                        save_failure.login_trace_id = trace_id
                        record_trace(state="error", stage=save_failure.stage,
                                     reason=save_failure.reason, finished_at=time.time())
                        raise save_failure from None
                raise failure from None
            finally:
                if api is not None:
                    try:
                        api.close()
                    except Exception:
                        pass

    def call(self, operation):
        from .delegation import DelegationDenied

        with self.store.exclusive():
            session = self.store.get("credential", "session")
            identity = self.store.get("setting", "identity")
            if not session or not identity or session.get("mid") != identity["mid"]:
                raise ValueError("LINE未接続です。ローカル端末で login を実行してください")
            api = self._client(session)
            try:
                return operation(api)
            except DelegationDenied:
                # Closed internal authorization reason, not upstream text.
                raise
            except Exception as error:
                # No raw upstream exception, URL, or response body crosses the boundary.
                from .diagnostics import diagnostic_error
                raise diagnostic_error("operation", error) from None
            finally:
                try:
                    self._save(api)
                finally:
                    api.close()

    def profile(self):
        return self.call(lambda api: api.get_profile())

    def receive_events(self, accept, seconds=20):
        """One bounded, SDK-serialized SSE connection with no background token threads."""
        from .realtime import ReceiveFailure, revision

        if find_executable(os.environ.get("LINE_NODE") or "node") is None:
            raise ReceiveFailure("dependency_missing")
        identity_rejected = False

        def consume(api):
            nonlocal identity_rejected
            # SDK group decryption can otherwise create a missing E2EE group key.
            # Receive-only must never acquire external write authority from a message.
            def no_key_registration(*args, **kwargs):
                raise RuntimeError("receive_only_key_registration_blocked")

            api.e2ee.register_group_key = no_key_registration
            api.register_e2ee_group_key = no_key_registration
            # Profile read can refresh an expired token before SSE's raw GET.
            profile = api.get_profile()
            if not isinstance(profile, dict) or profile.get("mid") != identity["mid"]:
                identity_rejected = True
                raise ReceiveFailure("identity_mismatch")
            saved = self.store.get("setting", "event_cursor")
            if not saved:
                initial = revision(api.ops._ensure_local_rev())
                saved = {"revision": initial, "initialized_at": time.time(),
                    "coverage": "events_since_initial_seed"}
                self.store.put("setting", "event_cursor", saved)
            api.ops.local_rev = revision(saved["revision"])
            deadline = time.monotonic() + seconds
            original = api.transport._send

            def observe(method, url, **kwargs):
                response = original(method, url, **kwargs)
                if kwargs.get("stream"):
                    if response.status_code != 200:
                        response.close()
                        raise ReceiveFailure("sse_open_rejected")
                    accept({"event": "opened"})
                    lines = response.iter_lines

                    def bounded_lines(*args, **kwargs):
                        # The SDK swallows SSE comments. Check before its parser,
                        # so comments cannot keep the account lock indefinitely.
                        # Event streams are UTF-8. requests defaults text/* to
                        # Latin-1 and str.splitlines() can then split a Japanese
                        # UTF-8 continuation byte 0x85 *inside* a JSON string.
                        # Frame on the wire LF byte first, then decode UTF-8.
                        for raw in lines(decode_unicode=False, delimiter=b"\n"):
                            if time.monotonic() >= deadline:
                                break
                            if (self.store.get("setting", "runtime") or {}).get("paused"):
                                raise ReceiveFailure("paused")
                            try:
                                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                            except UnicodeDecodeError:
                                raise ReceiveFailure("event_utf8_invalid") from None
                            yield line

                    response.iter_lines = bounded_lines
                return response

            api.transport._send = observe
            stream = api.ops._open_sse(read_timeout=min(30.0, max(0.2, seconds)))
            try:
                for event in stream:
                    data = event.data
                    if event.event == "message":
                        ops = data.get("operations") if isinstance(data, dict) else data
                        if isinstance(data, dict) and "type" in data:
                            ops = [data]
                        if not isinstance(ops, list):
                            raise ReceiveFailure("invalid_operation_batch")
                        clean = []
                        for op in ops:
                            if not isinstance(op, dict):
                                raise ReceiveFailure("invalid_operation_batch")
                            item = {key: op[key] for key in ("revision", "type") if key in op}
                            if op.get("message") is not None:
                                if not isinstance(op["message"], dict):
                                    raise ReceiveFailure("invalid_message_event")
                                item["message"] = self._messages(api, [op["message"]])[0]
                            clean.append(item)
                        data = {"operations": clean}
                    accept({"event": event.event, "data": data})
                    if event.event == "reconnect" or time.monotonic() >= deadline:
                        break
            except ReceiveFailure:
                raise
            except Exception as error:
                # requests wraps read timeouts inside ConnectionError.args.
                from requests.exceptions import ReadTimeout
                from urllib3.exceptions import ReadTimeoutError

                pending, seen, timeout = [error], set(), False
                while pending and len(seen) < 12:
                    current = pending.pop()
                    if id(current) in seen:
                        continue
                    seen.add(id(current))
                    timeout |= isinstance(current, (ReadTimeout, ReadTimeoutError))
                    pending.extend(x for x in [current.__cause__, current.__context__,
                        *current.args] if isinstance(x, BaseException))
                if not timeout:
                    raise
            finally:
                stream.close()
                api.transport._send = original

        # Unlike call(), preserve safe protocol codes for receiver diagnostics.
        with self.store.exclusive():
            session = self.store.get("credential", "session")
            identity = self.store.get("setting", "identity")
            if not session or not identity or session.get("mid") != identity["mid"]:
                raise ReceiveFailure("login_required")
            api = self._client(session)
            try:
                return consume(api)
            finally:
                try:
                    if not identity_rejected:
                        self._save(api)
                finally:
                    api.close()

    def contact_ids(self):
        return self.call(lambda api: api.get_all_contact_ids())

    def contacts(self, ids):
        return self.call(lambda api: self._contacts(api, ids))

    @staticmethod
    def _contacts(api, ids):
        rows = []
        # Validate each batch: the SDK's multi-batch merge can hide a missing response.
        for offset in range(0, len(ids), 100):
            batch = ids[offset : offset + 100]
            result = api.get_contacts(batch)
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("contacts"), dict)
                or set(result["contacts"]) != set(batch)
            ):
                raise ValueError("unexpected contact shape or missing contacts")
            for mid, item in result["contacts"].items():
                contact = item.get("contact", item)
                rows.append(
                    {
                        "mid": mid,
                        "displayName": contact.get("displayName", ""),
                        "displayNameOverridden": contact.get("displayNameOverridden", ""),
                        "statusMessage": contact.get("statusMessage", ""),
                        "isOfficial": contact.get("capableBuddy"),
                        "isHidden": bool(nonnegative_integer(contact["settings"]) & 4)
                        if "settings" in contact
                        else None,
                        "contactStatus": nonnegative_integer(contact["status"])
                        if "status" in contact
                        else None,
                    }
                )
        return rows

    @staticmethod
    def _messages(api, raw):
        if not isinstance(raw, list):
            raise ValueError("unexpected message shape")
        result = []
        for msg in raw:
            decrypted = True
            failure = None
            if msg.get("chunks"):
                originals = []
                def deny_registration(*args, **kwargs):
                    raise RuntimeError("read_only_key_registration_blocked")

                try:
                    # Decrypting a history read must not create group keys either.
                    for target, name in ((getattr(api, "e2ee", None), "register_group_key"),
                                         (api, "register_e2ee_group_key")):
                        if target is not None and hasattr(target, name):
                            originals.append((target, name, getattr(target, name)))
                            setattr(target, name, deny_registration)
                    msg = api.decrypt_message(msg)
                    if msg.get("chunks"):
                        from .diagnostics import DiagnosticError

                        raise DiagnosticError("message_decrypt", "message_decryption_failed")
                except Exception as error:
                    from .e2ee_state import decrypt_failure

                    decrypted = False
                    failure = decrypt_failure(error)
                finally:
                    for target, name, original in originals:
                        setattr(target, name, original)
            # Neither encrypted chunks nor credential-like metadata are exposed to the LLM.
            clean = {
                    key: msg[key]
                    for key in (
                        "id",
                        "from",
                        "to",
                        "text",
                        "contentType",
                        "createdTime",
                        "deliveredTime",
                    )
                    if key in msg
                }
            if failure:
                clean.pop("text", None)
                clean["decryption_error"] = failure
            result.append(clean | {"decrypted": decrypted})
        return result

    def recent_messages(self, chat, count=50):
        return self.call(lambda api: self._messages(api, api.get_recent_messages(chat, count)))

    def previous_messages(self, chat, message_id, delivered_time, count):
        return self.call(
            lambda api: self._messages(
                api, api.get_previous_messages(chat, message_id, delivered_time, count)
            )
        )

    def unread(self, cursor=None, limit=100):
        return self.call(lambda api: self._unread(api, cursor, limit))

    @staticmethod
    def _unread(api, cursor=None, limit=100, *, include_zero=False):
        raw = api.get_message_boxes(
            min_chat_id=cursor, unread_only=True, limit=limit, last_messages_per_box=1
        )
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("messageBoxes"), list)
            or type(raw.get("hasNext")) is not bool
        ):
            raise ValueError("unexpected message boxes")
        boxes = raw["messageBoxes"]
        items = {}
        for box in boxes:
            mid = box["id"]
            id_kind(mid)
            count = nonnegative_integer(box.get("unreadCount"))
            items[mid] = {"id": mid, "unreadCount": count}
        if raw["hasNext"] and (not boxes or boxes[-1]["id"] == cursor):
            raise ValueError("empty or stalled next page")
        return {
            "items": [box for box in items.values() if include_zero or box["unreadCount"] > 0],
            # Advance on the raw page, including zero-count boxes filtered out above.
            "next_cursor": boxes[-1]["id"] if raw["hasNext"] else None,
        }

    def account_counts(self, max_unread_pages=100):
        if type(max_unread_pages) is not int or not 1 <= max_unread_pages <= 100:
            raise ValueError("max_unread_pagesは1〜100です")

        def fetch(api):
            started = time.time()
            ids = api.get_all_contact_ids()
            if not isinstance(ids, list) or len(ids) > 100000:
                raise ValueError("unexpected contact ids")
            ids = list(dict.fromkeys(ids))
            if any(id_kind(mid) != "u" for mid in ids):
                raise ValueError("unexpected contact id")
            contacts = contact_counts(self._contacts(api, ids))
            groups = group_counts(api.get_all_chat_mids())
            cursor, cursors, unread = None, set(), {}
            for _page in range(max_unread_pages):
                result = self._unread(api, cursor, 100, include_zero=True)
                for box in result["items"]:
                    unread[box["id"]] = box["unreadCount"]
                cursor = result["next_cursor"]
                if cursor is None:
                    break
                if cursor in cursors:
                    raise ValueError("unread pagination cycle")
                cursors.add(cursor)
            complete = cursor is None
            unread = {mid: count for mid, count in unread.items() if count > 0}
            return {
                "contacts": contacts,
                "groups": groups,
                "unread": {
                    "complete": complete,
                    "chats": len(unread) if complete else None,
                    "messages": sum(unread.values()) if complete else None,
                    "observed_chats": len(unread),
                    "observed_messages": sum(unread.values()),
                    "pages": _page + 1,
                },
                "started_at": started,
                "checked_at": time.time(),
                "scope": "通常LINE。オープンチャット対象外。取得中の新着・既読によって変動します。",
            }

        return self.call(fetch)

    def groups(self):
        return self.call(lambda api: api.get_all_chat_mids())

    def group(self, mid):
        def fetch(api):
            chats = api.get_chats([mid], with_members=True, with_invitees=True)["chats"]
            if len(chats) != 1 or chats[0].get("chatMid") != mid:
                raise ValueError("group identity mismatch")
            return self._public_group(chats[0])

        return self.call(fetch)

    @staticmethod
    def _public_group(raw):
        from okline.entities import Group

        group = Group.from_dict(raw)
        return {
            "chatMid": group.chat_mid,
            "name": group.name,
            "members": group.member_mids,
            "invitees": group.invitee_mids,
        }

    def execute(self, kind, payload, *, preflight):
        def perform(api):
            media_data = None
            if kind == "send_media":
                from .media import load_bytes

                media_data, meta = load_bytes(self.store, payload["media_id"])
                if meta != payload["media"]:
                    raise ValueError("添付が変更されています")
            from .service import action_context

            fresh_context = action_context(
                kind,
                payload,
                lambda chat, count: self._messages(api, api.get_recent_messages(chat, count)),
                lambda ids: [
                    {
                        "displayNameOverridden": item.get("contact", item).get(
                            "displayNameOverridden", ""
                        )
                    }
                    for item in api.get_contacts(ids)["contacts"].values()
                ],
                lambda mid: self._public_group(
                    api.get_chats([mid], with_members=True, with_invitees=True)["chats"][0]
                ),
            )
            preflight(fresh_context)
            if kind == "send_text":
                from .identifiers import is_group
                if is_group(payload["to"]):
                    # A fresh group can reject a plain send with 99, whereas
                    # the SDK's plain-send fallback only handles 82. Negotiate
                    # and seal before the first send, as the extension does.
                    from okline import Message
                    from okline.enums import MessageRelationType
                    related = ({"related_message_id": payload["in_reply_to"],
                                "message_relation_type": int(MessageRelationType.REPLY)}
                               if payload.get("in_reply_to") else {})
                    return api.send_message(Message.text(payload["to"], payload["text"], **related),
                                            encrypt=True)
                if payload.get("in_reply_to"):
                    return api.reply_text(payload["to"], payload["text"], payload["in_reply_to"])
                return api.send_text(payload["to"], payload["text"])
            if kind == "send_media":
                sender = {
                    "image": api.send_image,
                    "video": api.send_video,
                    "audio": api.send_audio,
                    "file": api.send_file,
                }[payload["media_type"]]
                return sender(payload["to"], media_data, name=payload["media"]["name"])
            if kind == "send_mention":
                import json

                from okline import Message

                message = Message.text(
                    payload["to"],
                    payload["text"],
                    content_metadata={
                        "MENTION": json.dumps(payload["mention_metadata"], ensure_ascii=False)
                    },
                )
                from .identifiers import is_group
                return api.send_message(message, encrypt=is_group(payload["to"]))
            if kind == "rename_contact":
                return api.update_contact_setting(payload["mid"], 2, payload["name"])
            if kind == "create_group":
                return api.create_chat(payload["name"], payload["members"])
            if kind == "invite":
                return api.invite_into_chat(payload["group"], payload["members"])
            if kind == "rename_group":
                return api.rename_chat(payload["group"], payload["name"])
            if kind == "add_friend":
                return api.add_friend_by_mid(payload["mid"])
            raise ValueError("unsupported action")

        return self.call(perform)
