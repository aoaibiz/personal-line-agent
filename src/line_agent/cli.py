"""Local owner controls are deliberately absent from the MCP tool surface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .diagnostics import DiagnosticError, diagnostic_error, format_diagnostic
from .line import LineAdapter
from .service import AgentService
from .store import open_store


def parser():
    app = argparse.ArgumentParser(prog="line-agent")
    app.add_argument("--version", action="version", version=f"line-agent {__version__}")
    app.add_argument("--root", type=Path, default=Path.home() / ".local/share/personal-line-agent")
    app.add_argument("--account", default="personal")
    commands = app.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="アカウント専用保存先とOSキーチェーンの暗号鍵を用意")
    login = commands.add_parser("login", help="本人が端末でQRとPINを確認してLINEへ接続")
    login.add_argument("--repair-e2ee", action="store_true",
                       help="本人のQR確認で暗号化メッセージ用の鍵を取得し直す")
    commands.add_parser("status")
    commands.add_parser("doctor", help="実接続・保存の整合性を診断。秘密や本文は出さない")
    commands.add_parser("configure-operations", help="所有者/実行エージェント専用：通常操作の設定JSONを標準入力から反映")
    assist_config = commands.add_parser("assist-config", help="本人指定の履歴・分類を返信案へ接続")
    assist_config.add_argument("--corpus", type=Path, required=True)
    assist_config.add_argument("--snapshot", type=Path, required=True)
    assist_config.add_argument("--business-name", action="append", default=[])
    assist_config.add_argument("--allow-codex", action="store_true")
    assist = commands.add_parser("assist", help="取得済み新着を分類し返信案を保存（送信しない）")
    assist.add_argument("--watch", action="store_true")
    assist.add_argument("--interval", type=int, default=15)
    assist.add_argument("--limit", type=int, default=3)
    assist.add_argument("--retry-errors", action="store_true", help="本人操作で失敗処理を再試行。監査記録は保持")
    assist.add_argument("--refresh-stale", action="store_true", help="設定変更で古くなった案を本人操作で再生成")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mcp.add_argument("--public-url", help="HTTP利用時のHTTPS公開オリジン。公開接続は自動作成しない")
    mcp.add_argument("--port", type=int, default=8765)
    mcp.add_argument("--confirmation", choices=["mcp-host", "native-mac", "owner-delegation"],
                     help="本人確認の画面。native-macは操作中のMac画面があるローカルstdio専用")
    sync = commands.add_parser("sync")
    sync.add_argument("--job-id")
    worker = commands.add_parser("worker")
    worker.add_argument("--interval", type=int, default=15)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--events", action="store_true", help="未読状態に依存しない新着イベント受信")
    worker.add_argument("--duration", type=float, help="イベント受信の確認時間（秒）。省略時は継続")
    worker.add_argument(
        "--auto-templates", action="store_true", help="事前承認した相手・本文だけ自動返信"
    )
    approve = commands.add_parser("approve")
    approve.add_argument("action_id")
    approve.add_argument("--execute", action="store_true")
    commands.add_parser("pause")
    commands.add_parser("resume")
    export = commands.add_parser("export")
    export.add_argument("path", type=Path)
    pair = commands.add_parser("pair", help="手動Bearer認証対応クライアント用設定をローカル保存")
    pair.add_argument("--public-url", required=True)
    commands.add_parser("revoke-remote")
    media = commands.add_parser("stage-media", help="本人が選んだ添付を暗号化して登録")
    media.add_argument("path", type=Path)
    correction = commands.add_parser("correct-memory", help="相手の分類を本人が訂正・固定")
    correction.add_argument("mid")
    correction.add_argument("--label", action="append", default=[])
    correction.add_argument("--summary", required=True)
    policy = commands.add_parser(
        "allow-template", help="指定した相手と完全一致する本文だけ自動送信を許可"
    )
    policy.add_argument("mid")
    policy.add_argument("--text", action="append", required=True)
    policy.add_argument("--max-daily", type=int, default=5)
    policy.add_argument("--hours", type=int, default=24)
    revoke = commands.add_parser("revoke-template")
    revoke.add_argument("mid")
    delegation = commands.add_parser("setup-delegation", help="初回の委任範囲を本人画面で登録。通常操作の追加画面は不要")
    delegation.add_argument("--target", action="append", required=True)
    delegation.add_argument("--kind", action="append", required=True)
    delegation.add_argument("--hours", type=int, default=24)
    delegation.add_argument("--max-daily", type=int, default=50)
    delegation.add_argument("--max-total", type=int, default=100)
    delegation.add_argument("--max-groups", type=int, default=3)
    request = commands.add_parser("request", help="エージェント内部用。正式MCP経由で通常操作を実行/照会")
    request.add_argument("--request-id", required=True)
    request.add_argument("--kind")
    request.add_argument("--status", action="store_true")
    return app


def write_private_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())


def run(args):
    if args.command == "request":
        from .owner_client import run_request
        if not args.status and not args.kind:
            raise ValueError("操作の種類が必要です")
        payload = None if args.status else json.load(sys.stdin)
        return run_request(args.root, args.account, args.request_id, args.kind, payload, status_only=args.status)
    try:
        store = open_store(args.root, args.account, create=args.command == "init")
    except Exception as error:
        raise diagnostic_error("store_open", error) from None
    service = AgentService(store, LineAdapter(store))
    if args.command == "init":
        return {"initialized": True, "account": args.account, "connected": False}
    if args.command == "login":
        from okline.qrterm import print_qr

        if not sys.stdin.isatty():
            raise DiagnosticError("login_state", "interactive_terminal_required")
        print("個人LINEの非公式接続です。表示された接続先を確認してログインしてください。")
        return service.line.login(
            on_qr=print_qr, on_pin=lambda pin: print(f"LINEで確認するPIN: {pin}"),
            repair_e2ee=args.repair_e2ee,
        )
    if args.command == "doctor":
        from .lifecycle import diagnose
        return diagnose(service, probe=True)
    if args.command == "status":
        return service.status()
    if args.command == "configure-operations":
        from .delegation import configure_owner
        return configure_owner(service, json.load(sys.stdin))
    if args.command == "setup-delegation":
        from .delegation import enroll_native
        return enroll_native(service, {"targets": args.target, "kinds": args.kind,
            "hours": args.hours, "max_daily": args.max_daily, "max_total": args.max_total,
            "max_groups": args.max_groups, "allow_created_groups": True})
    if args.command == "assist-config":
        from .assistance_context import configure_source

        if any(not name.strip() or len(name) > 100 for name in args.business_name):
            raise ValueError("business-nameが不正です")
        try:
            result = configure_source(store, args.corpus, args.snapshot)
        except ValueError as error:
            from .assistance import SAFE_CONTEXT_ERRORS

            reason = str(error) if str(error) in SAFE_CONTEXT_ERRORS else "unsupported_schema"
            raise DiagnosticError("assistance_context", reason) from None
        store.put("setting", "assistance_businesses", [{"name": name} for name in args.business_name])
        if args.allow_codex:
            from .assistance_context import digest

            store.put("setting", "assistance_consent", {"provider": "OpenAI Codex CLI",
                "scope": "incoming_classification_drafts", "recorded_at": time.time(),
                "source_digest": digest(store.get("setting", "assistance_source")),
                "identity_index": store._id("consent_owner", store.get("setting", "identity")["mid"])})
        return result
    if args.command == "assist":
        from .assistance import _consent, refresh_stale, retry_failed, run_assistance
        from .assistance_model import CodexAssistance

        _consent(store)
        try:
            provider = CodexAssistance(store.path / "assistance-inference")
        except DiagnosticError as error:
            store.put("setting", "assistance_runtime", {"state": "error", "stage": "model_bootstrap",
                "reason": error.reason, "details": getattr(error, "details", {}),
                "failed_at": time.time()})
            raise
        if args.retry_errors:
            retry_failed(service, provider)
        if args.refresh_stale:
            refresh_stale(service)
        result = run_assistance(service, provider, args.watch, args.interval, args.limit)
        if result["failed"]:
            raise DiagnosticError("assistance_model", "assistance_partial_failure")
        return result
    if args.command == "stage-media":
        from .media import stage_file

        return stage_file(store, args.path)
    if args.command == "allow-template":
        if not sys.stdin.isatty():
            raise ValueError("本人のローカル端末で設定してください")
        print(
            json.dumps(
                {
                    "recipient": args.mid,
                    "texts": args.text,
                    "max_daily": args.max_daily,
                    "hours": args.hours,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        if input("この範囲の自動送信を許可する場合は相手IDを入力: ") != args.mid:
            raise ValueError("設定を中止しました")
        return service.set_policy_locally(args.mid, args.text, args.max_daily, args.hours)
    if args.command == "revoke-template":
        from .service import validate_mid

        validate_mid(args.mid)
        store.put("policy", args.mid, {"expires_at": 0})
        return {"revoked": True}
    if args.command == "correct-memory":
        from .service import validate_mid

        validate_mid(args.mid)
        if (
            not store.get("contact", args.mid)
            or len(args.summary) > 5000
            or len(args.label) > 20
            or any(len(label) > 50 for label in args.label)
        ):
            raise ValueError("対象または入力が不正です")
        old = store.get("memory", args.mid) or {"mid": args.mid, "evidence": []}
        old.update(
            labels=args.label,
            summary=args.summary,
            confidence="owner_corrected",
            updated_at=time.time(),
        )
        store.put("memory", args.mid, old)
        return {"corrected": True}
    if args.command == "sync":
        return service.sync_contacts(args.job_id)
    if args.command == "mcp":
        from .server import create_server

        if args.transport != "stdio" and not args.public_url:
            raise ValueError("HTTP接続にはpublic-urlと認証設定が必要です")
        if args.public_url and not store.get("setting", "remote_access"):
            raise ValueError("先にpairで接続先を固定してください")
        if args.confirmation == "native-mac" and args.transport != "stdio":
            raise ValueError("Mac画面での本人確認はローカルstdio接続専用です")
        confirmation = args.confirmation or ("owner-delegation" if args.transport == "stdio" else "mcp-host")
        server = create_server(service, args.public_url, args.port, confirmation)
        server.run(transport=args.transport)
        return None
    if args.command == "worker":
        if args.events:
            if args.once or args.auto_templates:
                raise ValueError("eventsは受信専用です。once/auto-templatesとは併用できません")
            from .realtime import run_events

            return run_events(service, args.duration)
        if args.duration is not None:
            raise ValueError("durationはeventsと併用してください")
        from .worker import receive_once, run_worker

        if args.once:
            with store.exclusive("worker", blocking=False):
                result = receive_once(service)
                if args.auto_templates:
                    from .worker import reply_with_owner_templates

                    reply_with_owner_templates(service)
                return result
        return run_worker(service, args.interval, args.auto_templates)
    if args.command == "approve":
        if not sys.stdin.isatty():
            raise ValueError("本人が操作するローカル端末で承認してください")
        action = service.action(args.action_id)
        # JSON escapes untrusted control characters, including terminal escape sequences.
        print(json.dumps(action, ensure_ascii=True, indent=2))
        answer = input(f"この宛先・内容を承認する場合は {args.action_id} を入力: ")
        if answer != args.action_id:
            raise ValueError("承認を中止しました")
        result = service.approve_locally(args.action_id)
        return service.execute(args.action_id) if args.execute else result
    if args.command == "pause":
        return service.pause()
    if args.command == "resume":
        service.resume_locally()
        return {"paused": False}
    if args.command == "export":
        result, offset = [], 0
        while True:
            page = service.contacts(offset=offset, limit=200)
            if not page["complete_snapshot"]:
                raise ValueError("全件取得を完了してから出力してください")
            result.extend(page["items"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        write_private_new(args.path, {"total": len(result), "items": result})
        return {"exported": len(result), "path": str(args.path)}
    if args.command == "pair":
        origin = args.public_url.rstrip("/")
        parsed = urlparse(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("HTTPSの公開オリジンを指定してください")
        token = secrets.token_urlsafe(48)
        resource = f"{origin}/mcp"
        expires = time.time() + 30 * 86400
        # Save to a new private file; never print the credential into stdout/chat.
        target = store.path / f"pairing-{secrets.token_hex(4)}.json"
        write_private_new(
            target,
            {
                "url": resource,
                "headers": {"Authorization": f"Bearer {token}"},
                "expires_at": expires,
            },
        )
        store.put(
            "setting",
            "remote_access",
            {
                "sha256": hashlib.sha256(token.encode()).hexdigest(),
                "expires_at": expires,
                "resource": resource,
            },
        )
        return {
            "pairing_file": str(target),
            "expires_at": expires,
            "note": "このファイルは秘密です。GitHub・チャットへ添付しないでください",
        }
    if args.command == "revoke-remote":
        store.put(
            "setting", "remote_access", {"sha256": "revoked", "expires_at": 0, "resource": ""}
        )
        return {"remote_revoked": True}
    raise ValueError("不明なコマンドです")


def main():
    args = parser().parse_args()
    if (args.command == "worker" and args.events) or args.command == "assist":
        def stop_receiver(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop_receiver)
    try:
        result = run(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=True, indent=2))
    except KeyboardInterrupt:
        return
    except Exception as error:
        print(format_diagnostic(args.command, error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
