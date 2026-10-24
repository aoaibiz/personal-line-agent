"""Confirmation via the trusted MCP host; no approval claims in tool arguments."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = Field(title="表示された宛先・内容で実行する", description="承認する場合だけ選択")


def supported(ctx):
    params = ctx.session.client_params
    cap = params.capabilities.elicitation if params else None
    if cap is None:
        return False
    if cap.form is not None:
        return True
    # In the first elicitation protocol an empty capability meant form support.
    return cap.url is None and params.protocolVersion == "2025-06-18"


def display_json(value):
    # Escape controls and bidi formatting, preserving readable Japanese.
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return "".join(f"\\u{ord(c):04x}" if ord(c) in (
        0x061c, 0x200e, 0x200f, *range(0x202a, 0x202f), *range(0x2066, 0x206a)
    ) else c for c in text)


def preview(service, action):
    payload = action["payload"]
    ids = set(payload.get("members", []))
    ids.update(m["mid"] for m in payload.get("mentions", []))
    ids.update(payload[k] for k in ("to", "mid", "group") if k in payload)
    from .identifiers import is_group

    people = sorted(mid for mid in ids if not is_group(mid))
    rows = service.line.contacts(people) if people else []
    names = {r["mid"]: r.get("displayNameOverridden") or r.get("displayName")
             or "名称未取得" for r in rows}
    for mid in sorted(ids):
        if is_group(mid):
            row = service.line.group(mid)
            names[mid] = row.get("name") or "名称未取得"

    def target(mid):
        return f"{display_json(names.get(mid, '名称未取得'))}（ID: {mid}）"

    labels = {"send_text": "メッセージ送信", "send_mention": "メンション送信",
              "send_media": "添付送信", "create_group": "グループ作成",
              "rename_contact": "友だちの表示名変更", "rename_group": "グループ名変更",
              "invite": "グループへの招待", "add_friend": "友だち追加"}
    lines = [f"LINE：{labels[action['kind']]}"]
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    lines.append(f"利用アカウント：{owner or 'この接続のアカウント'}")
    for field, label in (("to", "宛先"), ("mid", "対象"), ("group", "グループ")):
        if field in payload:
            lines.append(f"{label}：{target(payload[field])}")
    if "text" in payload:
        lines.extend(["本文：", display_json(payload["text"])])
    if "in_reply_to" in payload and payload["in_reply_to"]:
        lines.append(f"返信先メッセージID：{display_json(payload['in_reply_to'])}")
    if "name" in payload:
        if action["kind"] == "rename_contact":
            lines.append("変更前：" + display_json(names.get(payload["mid"], "名称未取得")))
        if action["kind"] == "rename_group":
            lines.append("変更前：" + display_json(names.get(payload["group"], "名称未取得")))
        lines.append("新しい名前：" + display_json(payload["name"]))
    if "members" in payload:
        lines.extend(["参加者：", *(target(mid) for mid in payload["members"])])
    if "mentions" in payload:
        lines.extend(["メンション対象：", *(f"{target(m['mid'])}：{display_json(payload['text'][m['start']:m['end']])}"
                     for m in payload["mentions"])])
    if "media" in payload:
        lines.extend(["添付の送信形式：" + display_json(payload["media_type"]),
                      "添付：", display_json(payload["media"])])
    lines.append("この内容で実行します。拒否・キャンセルすると実行しません。")
    return "\n".join(lines)


def record(service, action_id, reason, *, stage="mcp_host_confirmation", host_action=None,
           dialog_button_code=None, process_exit_code=None):
    service.store.put("approval_event", str(uuid.uuid4()), {
        "action_id": action_id, "stage": stage, "reason": reason, "at": time.time(),
        **({"host_action": host_action, "decision_origin": "unknown",
            "presentation_state": "unknown"} if host_action is not None else {}),
        **({"dialog_button_code": dialog_button_code} if dialog_button_code in (1000, 1001) else {}),
        **({"process_exit_code": process_exit_code} if type(process_exit_code) is int else {}),
    })


def stopped(service, action_id, reason, message, *, stage="mcp_host_confirmation", host_action=None):
    record(service, action_id, reason, stage=stage, host_action=host_action)
    return {"ok": False, "action_id": action_id, "stage": stage,
            "error": reason, "message": message, "executed": False}


async def confirm_and_execute(service, action_id, ctx, mode="mcp-host"):
    if mode not in ("mcp-host", "native-mac"):
        raise ValueError("不明な本人確認方式です")
    action = service.action(action_id)
    if action["state"] != "prepared":
        # Existing local approvals and explicit owner policies remain compatible.
        return await asyncio.to_thread(service.execute, action_id)
    if action["expires_at"] <= time.time() or service.paused():
        return stopped(service, action_id, "confirmation_invalidated", "期限切れまたは停止中です。実行していません")
    if mode == "native-mac":
        from .native_approval import confirm_and_execute_native
        return await asyncio.to_thread(confirm_and_execute_native, service, action)
    if not supported(ctx):
        return stopped(service, action_id, "chat_confirmation_unavailable",
                       "この接続ではチャット内の実行確認を表示できません。実行していません。対応ホストへの接続が必要です")
    binding = service.approval_binding(action)
    message = await asyncio.to_thread(preview, service, action)
    remaining = min(300, action["expires_at"] - time.time())
    if remaining <= 0:
        return stopped(service, action_id, "confirmation_invalidated", "確認表示前に期限切れになりました。実行していません")
    try:
        async with asyncio.timeout(remaining):
            response = await ctx.elicit(message, Confirmation)
    except TimeoutError:
        return stopped(service, action_id, "confirmation_timeout", "確認が時間切れになりました。実行していません")
    except Exception:
        return stopped(service, action_id, "confirmation_transport_failed", "チャット側の確認を受け取れませんでした。実行していません")
    if response.action != "accept" or response.data is None or response.data.confirm is not True:
        reason = "confirmation_cancelled" if response.action == "cancel" else "confirmation_declined"
        host_action = response.action if response.action in ("accept", "decline", "cancel") else "unknown"
        return stopped(service, action_id, reason,
                       "接続先から実行承認が返らず未実行です。本人の拒否か表示前の制限かは、この応答だけでは判別できません",
                       host_action=host_action)
    try:
        service.approve_from_client(action_id, binding)
    except ValueError:
        return stopped(service, action_id, "confirmation_invalidated", "確認中に対象・状態・期限が変わりました。実行していません")
    record(service, action_id, "confirmation_accepted")
    return await asyncio.to_thread(service.execute, action_id)
