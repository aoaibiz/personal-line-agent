"""Bounded delegation to a trusted owner MCP client, never per-action consent."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import Field

from .identifiers import is_group, is_user
from .service import ACTION_MODELS, Mid, Strict, digest


class GroupRule(Strict):
    participants: list[Mid] = Field(min_length=2, max_length=500)
    kinds: list[str] = Field(min_length=1, max_length=20)


class Scope(Strict):
    targets: list[Mid] = Field(default_factory=list, max_length=5000)
    all_targets: bool = False
    kinds: list[str] = Field(min_length=1, max_length=20)
    hours: int | None = Field(ge=1, le=24 * 365)
    max_daily: int | None = Field(ge=1, le=500)
    max_total: int | None = Field(ge=1, le=10000)
    max_groups: int | None = Field(ge=0, le=100)
    allow_created_groups: bool = True
    group_rules: dict[Mid, GroupRule] = Field(default_factory=dict, max_length=5000)
    blocked_targets: list[Mid] = Field(default_factory=list, max_length=5000)
    blocked_kinds: list[str] = Field(default_factory=list, max_length=20)


DENIALS = {
    "owner_identity_unavailable": "接続アカウントを確認できません。接続状態を確認します。",
    "delegation_connection_unsupported": "この接続には通常操作の委任が登録されていません。",
    "delegation_setup_required": "初回の操作権限設定が必要です。通常操作の確認窓は出していません。",
    "delegation_account_or_connection_changed": "委任したアカウントまたは接続と一致しません。",
    "delegation_revoked": "操作権限は失効しています。実行していません。",
    "delegation_expired": "操作権限の期限が切れています。実行していません。",
    "action_already_started_or_authorized": "この操作は既に開始または承認されています。再実行せず結果を確認します。",
    "action_expired": "操作候補の期限が切れました。候補を作り直す必要があります。",
    "agent_paused": "操作は一時停止中です。実行していません。",
    "delegation_kind_out_of_scope": "この種類の操作は委任されていません。",
    "delegation_target_blocked": "本人がこの対象への操作を禁止しています。",
    "delegation_kind_blocked": "本人がこの種類の操作を禁止しています。",
    "delegation_target_type_unsupported": "この種類のトークは委任に対応していません。",
    "delegation_target_out_of_scope": "この相手・グループは委任の対象外です。",
    "delegation_members_out_of_scope": "参加者に委任対象外の人がいます。",
    "delegation_mentions_out_of_scope": "メンション対象に委任対象外の人がいます。",
    "delegation_group_members_changed": "グループの参加・招待者を委任範囲内と確認できません。",
    "group_waiting_for_members": "グループは作成済みですが、相手の参加待ちです。本文は送信開始していません。",
    "delegation_group_limit": "作成できるグループ数の上限です。",
    "delegation_limit_reached": "操作回数の上限です。追加実行していません。",
    "delegation_changed": "操作権限が変更されました。古い権限では実行しません。",
    "action_or_conversation_changed": "操作内容または会話が更新されました。候補を確認し直します。",
    "delegation_invalid_state": "操作権限の状態を確認できません。実行していません。",
}


class DelegationDenied(ValueError):
    def __init__(self, reason):
        self.reason = reason if reason in DENIALS else "delegation_invalid_state"
        super().__init__(self.reason)


def account_binding(service):
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if not owner or not is_user(owner):
        raise DelegationDenied("owner_identity_unavailable")
    return digest({"path": str(service.store.path), "owner": owner, "uid": os.geteuid()})


def local_principal(service):
    return "local_stdio:" + account_binding(service)


def candidate(service, data):
    scope = Scope.model_validate(data).model_dump()
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if ((not scope["all_targets"] and not scope["targets"])
            or len(set(scope["targets"])) != len(scope["targets"])
            or len(set(scope["kinds"])) != len(scope["kinds"])
            or any(not (is_user(mid) or is_group(mid)) for mid in scope["targets"] + scope["blocked_targets"])
            or not set(scope["kinds"]) <= set(ACTION_MODELS)
            or not set(scope["blocked_kinds"]) <= set(ACTION_MODELS)):
        raise ValueError("委任の対象・操作が不正です")
    for group, rule in scope["group_rules"].items():
        people = rule["participants"]
        if (not is_group(group) or (not scope["all_targets"] and group not in scope["targets"])
                or len(set(people)) != len(people) or owner not in people
                or any(not is_user(mid) for mid in people)
                or len(set(rule["kinds"])) != len(rule["kinds"])
                or not set(rule["kinds"]) <= set(scope["kinds"])
                or not set(rule["kinds"]) <= {"send_text", "send_mention", "send_media", "invite", "rename_group"}):
            raise ValueError("グループ内だけの委任条件が不正です")
    return {"id": str(uuid.uuid4()), "account_binding": account_binding(service),
            "principal": local_principal(service), "scope": scope,
            "created_at": time.time(),
            "expires_at": time.time() + scope["hours"] * 3600 if scope["hours"] else None,
            "owner": service.store.get("setting", "identity")["mid"],
            "mode": "owner_directed", "version": 1}


def default_scope():
    """All implemented account operations; execution still follows owner instructions."""
    return {"all_targets": True, "targets": [], "kinds": list(ACTION_MODELS),
            "hours": None, "max_daily": None, "max_total": None, "max_groups": None,
            "allow_created_groups": True}


def _persist(service, proposed, source, *, if_missing=False):
    with service.store.atomic() as tx:
        if if_missing and tx.get("setting", "owner_client_delegation") is not None:
            return {"initialized": False, "reason": "existing_configuration_preserved"}
        if account_binding(service) != proposed["account_binding"]:
            raise ValueError("アカウントが変わりました")
        proposed.update(source=source, revoked_at=None, used_total=0, daily={}, groups_started=0)
        tx.put("delegation", proposed["id"], proposed)
        tx.put("setting", "owner_client_delegation", {"id": proposed["id"]})
    return {"enrolled": True, "grant_id": proposed["id"], "scope": proposed["scope"],
            "expires_at": proposed["expires_at"], "normal_operation_ui": "none"}


def configure_owner(service, data):
    """Privileged local configuration, deliberately NOT an MCP tool or consent proof.

    Used by account setup/the owner's explicitly instructed local agent. This
    trusts the same OS owner as other admin controls; it cannot attest human
    utterances and must never be represented as per-action UI approval.
    """
    return _persist(service, candidate(service, data), "local_owner_configuration")


def initialize_default(service):
    """New authenticated accounts only. Re-login never erases an existing restriction/revocation."""
    # Login holds sdk, while prepare_once holds requests before sdk. Never
    # acquire requests here. Conditional installation is serialized by SQLite.
    result = _persist(service, candidate(service, default_scope()),
                      "authenticated_account_default", if_missing=True)
    return {"initialized": bool(result.get("enrolled")), **result}


def enroll_native(service, data):
    """Local settings UI only. Never exported as a grant-making MCP tool."""
    from .chat_approval import display_json
    from .native_approval import show_dialog
    proposed = candidate(service, data)
    labels = {"send_text": "テキスト送信", "send_mention": "メンション", "send_media": "添付送信",
              "create_group": "グループ作成", "invite": "招待", "rename_group": "グループ名変更",
              "rename_contact": "友だち表示名変更", "add_friend": "友だち追加"}
    targets = []
    scope = proposed["scope"]
    users = sorted(({mid for mid in scope["targets"] if is_user(mid)} |
                    {mid for rule in scope["group_rules"].values() for mid in rule["participants"]})
                   - {proposed["owner"]})
    names = {r["mid"]: r.get("displayNameOverridden") or r.get("displayName") for r in service.line.contacts(users)} if users else {}
    for mid in proposed["scope"]["targets"]:
        name = service.line.group(mid).get("name") if is_group(mid) else names.get(mid)
        targets.append(f"{display_json(name or '名称未取得')}（{mid}）")
        if mid in scope["group_rules"]:
            rule = scope["group_rules"][mid]
            targets.append("  このグループ内だけの操作：" + "、".join(labels[k] for k in rule["kinds"]))
            targets.append("  許可する参加・招待者：" + "、".join(
                "本人" if person == proposed["owner"] else
                f"{display_json(names.get(person) or '名称未取得')}（{person}）"
                for person in rule["participants"]))
            targets.append("  参加者であることだけでは、個人トークへの操作権限は付与しません。")
    message = "\n".join([
        "初回設定：所有者AIクライアントへの操作委任", "この設定後、範囲内の通常操作では確認窓を出しません。",
        "アカウント：" + proposed["owner"], "接続：このOS利用者のローカルMCP（stdio）",
        "操作対象（グループ別の制限は各対象の下に記載）：", *targets,
        "上限の操作種類（各グループには上記の個別制限を適用）：" + "、".join(labels[k] for k in scope["kinds"]),
        "本文はAIが作成できます。今回の各本文を本人が承認したという意味ではありません。",
        "対象範囲：" + ("本人アカウント内の全員・全通常グループ" if scope["all_targets"] else "上記の対象のみ"),
        "禁止対象：" + display_json(scope["blocked_targets"]),
        "禁止操作：" + "、".join(labels[k] for k in scope["blocked_kinds"]),
        "期限：" + (f"登録候補の作成から{scope['hours']}時間" if scope["hours"] else "本人が解除するまで"),
        f"回数：日次{scope['max_daily'] if scope['max_daily'] is not None else '制限なし'}・総計{scope['max_total'] if scope['max_total'] is not None else '制限なし'}。",
        f"新規グループ：最大{scope['max_groups']}組。個人トークも許可した人だけを参加/招待するものに限定。",
        "この委任で作成したグループの操作：" + ("許可" if scope["allow_created_groups"] else "禁止"),
        "常駐AIへの自動返信権限は含みません。停止・失効はチャットのツールから可能です。",
        "承認すると設定を保存します。LINEへの送信はこの設定操作では行いません。",
    ])
    expected = digest(proposed)
    result = show_dialog(message, 300, purpose="delegation")
    if result.decision != "accepted" or result.button_code != 1001 or result.exit_code != 0:
        return {"ok": False, "error": "delegation_not_enrolled", "executed": False}
    if (digest(proposed) != expected or account_binding(service) != proposed["account_binding"]
            or (proposed["expires_at"] is not None and proposed["expires_at"] <= time.time())):
        raise ValueError("設定中にアカウントまたは期限が変わりました")
    return _persist(service, proposed, "local_owner_settings_ui")


def active(service):
    pointer = service.store.get("setting", "owner_client_delegation") or {}
    return service.store.get("delegation", pointer.get("id", ""))


def status(service):
    grant = active(service)
    if not grant:
        return {"state": "not_enrolled", "normal_operation_ui": "none"}
    state = ("revoked" if grant["revoked_at"] else "expired" if
             grant["expires_at"] is not None and grant["expires_at"] <= time.time() else "active")
    return {"state": state, "grant_id": grant["id"], "scope": grant["scope"],
            "expires_at": grant["expires_at"], "used_total": grant["used_total"],
            "normal_operation_ui": "none", "mode": grant["mode"]}


def revoke(service):
    with service.store.atomic() as tx:
        pointer = tx.get("setting", "owner_client_delegation") or {}
        grant = tx.get("delegation", pointer.get("id", ""))
        if grant:
            grant["revoked_at"] = time.time()
            tx.put("delegation", grant["id"], grant)
    return {"revoked": True}


def check(service, grant, action, principal, group_record=None):
    if not principal:
        raise DelegationDenied("delegation_connection_unsupported")
    if not grant:
        raise DelegationDenied("delegation_setup_required")
    if (grant["account_binding"] != account_binding(service) or grant["principal"] != principal
            or principal != local_principal(service)):
        raise DelegationDenied("delegation_account_or_connection_changed")
    if grant["revoked_at"]:
        raise DelegationDenied("delegation_revoked")
    if grant["expires_at"] is not None and grant["expires_at"] <= time.time():
        raise DelegationDenied("delegation_expired")
    if action["state"] != "prepared":
        raise DelegationDenied("action_already_started_or_authorized")
    if action["expires_at"] <= time.time():
        raise DelegationDenied("action_expired")
    if service.paused():
        raise DelegationDenied("agent_paused")
    scope, kind, payload = grant["scope"], action["kind"], action["payload"]
    if kind not in scope["kinds"]:
        raise DelegationDenied("delegation_kind_out_of_scope")
    if kind in scope.get("blocked_kinds", []):
        raise DelegationDenied("delegation_kind_blocked")
    allowed = set(scope["targets"])
    all_targets = scope.get("all_targets", False)
    blocked = set(scope.get("blocked_targets", []))
    target = payload.get("to") or payload.get("group") or ""
    rule = scope.get("group_rules", {}).get(target)
    if rule and kind not in rule["kinds"]:
        raise DelegationDenied("delegation_kind_out_of_scope")
    for key in ("to", "mid", "group"):
        if key not in payload:
            continue
        mid = payload[key]
        if not (is_user(mid) or is_group(mid)):
            raise DelegationDenied("delegation_target_type_unsupported")
        if mid in blocked:
            raise DelegationDenied("delegation_target_blocked")
        if not all_targets and mid not in allowed and not (is_group(mid) and scope["allow_created_groups"]
                and group_record and group_record.get("grant_id") == grant["id"]):
            raise DelegationDenied("delegation_target_out_of_scope")
    participant_scope = set(rule["participants"]) if rule else allowed
    if blocked & (set(payload.get("members", [])) | {m["mid"] for m in payload.get("mentions", [])}):
        raise DelegationDenied("delegation_target_blocked")
    if not (all_targets and not rule) and not set(payload.get("members", [])) <= participant_scope:
        raise DelegationDenied("delegation_members_out_of_scope")
    if not (all_targets and not rule) and not {m["mid"] for m in payload.get("mentions", [])} <= participant_scope:
        raise DelegationDenied("delegation_mentions_out_of_scope")
    if "group_members" in action["context"]:
        if kind.startswith("send_") and len(action["context"]["group_members"]) < 2:
            raise DelegationDenied("group_waiting_for_members")
        participants = set(action["context"]["group_members"] + action["context"]["group_invitees"])
        if participants & blocked:
            raise DelegationDenied("delegation_target_blocked")
        if (grant["owner"] not in participants or (not participants <= participant_scope if rule
                else not all_targets and not participants <= allowed | {grant["owner"]})):
            raise DelegationDenied("delegation_group_members_changed")
    if kind == "create_group" and scope["max_groups"] is not None and grant["groups_started"] >= scope["max_groups"]:
        raise DelegationDenied("delegation_group_limit")
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    if ((scope["max_total"] is not None and grant["used_total"] >= scope["max_total"])
            or (scope["max_daily"] is not None and grant["daily"].get(today, 0) >= scope["max_daily"])):
        raise DelegationDenied("delegation_limit_reached")


def for_action(service, action, principal):
    grant = active(service)
    target = action["payload"].get("to") or action["payload"].get("group") or ""
    group_record = service.store.get("delegation_group", target)
    check(service, grant, action, principal, group_record)
    return grant["id"]


def claim(service, action, principal, grant_id, binding, fresh_context):
    with service.store.atomic() as tx:
        grant = tx.get("delegation", grant_id)
        pointer = tx.get("setting", "owner_client_delegation") or {}
        current = tx.get("action", action["id"])
        if pointer.get("id") != grant_id or not current:
            raise DelegationDenied("delegation_changed")
        target = current["payload"].get("to") or current["payload"].get("group") or ""
        group_record = tx.get("delegation_group", target)
        check(service, grant, current, principal, group_record)
        if service.approval_binding(current) != binding or fresh_context != current["context"]:
            raise DelegationDenied("action_or_conversation_changed")
        today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
        grant["used_total"] += 1
        grant["daily"][today] = grant["daily"].get(today, 0) + 1
        grant["daily"] = {today: grant["daily"][today]}
        if current["kind"] == "create_group":
            grant["groups_started"] += 1
        current.update(state="sending", started_at=time.time(), authority="owner_client_delegation",
                       delegation_id=grant_id, approval_binding=binding)
        tx.put("delegation", grant_id, grant)
        tx.put("action", current["id"], current)


def remember_group(service, action):
    if action["state"] != "sent" or action["kind"] != "create_group" or not action.get("delegation_id"):
        return
    result = action.get("result") or {}
    mid = (result.get("chat") or {}).get("chatMid")
    if mid and is_group(mid):
        service.store.put("delegation_group", mid, {"grant_id": action["delegation_id"],
                         "action_id": action["id"]})
