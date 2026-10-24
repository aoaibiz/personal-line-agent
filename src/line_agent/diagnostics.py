"""Closed-vocabulary diagnostics: never stringify an upstream exception or response."""

from __future__ import annotations

STAGES = {
    "group_key_register",
    "e2ee_login", "e2ee_restore", "e2ee_save", "message_decrypt",
    "event_receive",
    "assistance_model",
    "assistance_context",
    "store_open",
    "keyring_select",
    "keyring_read",
    "keyring_write",
    "login_state",
    "sdk_init",
    "qr_session_create",
    "qr_code_create",
    "qr_render",
    "qr_scan_wait",
    "qr_certificate",
    "qr_pin_create",
    "qr_pin_wait",
    "qr_issue",
    "profile_check",
    "session_save",
    "identity_save",
    "operation",
}
REASONS = {
    "e2ee_keys_missing": "復号用の鍵が読み込まれていません。暗号化本文は未取得です。",
    "e2ee_login_incomplete": "QRログインで復号用の鍵を取得できませんでした。接続完了とは扱っていません。",
    "e2ee_login_metadata_missing": "QRの確認後、復号鍵を取り込むための情報を受け取れませんでした。",
    "e2ee_login_unwrap_failed": "QRで受け取った復号鍵をこの接続で利用可能にできませんでした。",
    "e2ee_identity_mismatch": "復号用の鍵と接続アカウントが一致しません。保存済み鍵を保持しました。",
    "e2ee_restore_failed": "保存済みの復号鍵を全て復元できませんでした。元の鍵を保持しました。",
    "e2ee_export_failed": "復号用の鍵を完全に保存できませんでした。以前の鍵と更新済み接続を保持しました。",
    "e2ee_group_registration_required": "グループの復号鍵が取得できず、新しい鍵の登録は実行していません。",
    "message_decryption_failed": "暗号化本文を復号できません。返信要否を判定せず、原因を確認してください。",
    "line_api_rejected": "LINE側でこの工程が拒否されました。",
    "line_transport_failed": "LINEとの通信がこの工程で完了しませんでした。",
    "keyring_failed": "OSキーチェーンへアクセスできませんでした。",
    "secure_keyring_required": "OSの安全なキーチェーンが必要です。",
    "missing_encryption_key": "暗号鍵が見つかりません。既存データは上書きしていません。",
    "permission_denied": "この工程でOSのアクセス許可が得られませんでした。",
    "file_missing": "必要なファイルが見つかりません。",
    "storage_failed": "ローカル保存が完了しませんでした。",
    "invalid_input_or_state": "入力または保存状態がこの操作の条件を満たしていません。",
    "invalid_login_result": "LINEからログイン完了を確認できませんでした。",
    "invalid_saved_session": "保存済み接続を確認できません。自動では上書きしません。",
    "identity_mismatch": "接続先がこの保存先のLINEアカウントと一致しません。",
    "interactive_terminal_required": "本人が操作するMacのターミナルで実行してください。",
    "dependency_missing": "必要なプログラムまたはライブラリを読み込めませんでした。",
    "unsupported_schema": "この工程の応答形式が実装の想定と一致しませんでした。",
    "unexpected_error": "この工程で想定外のエラーが起きました。診断コードをお知らせください。",
}
# Closed receiver codes: values never contain upstream content.
REASONS.update({
    "event_utf8_invalid": "受信ストリームがUTF-8として読めません。受信位置を保持して停止しました。",
    "invalid_revision": "受信位置の形式が不正です。保存済み位置を保持して停止しました。",
    "resync_required": "LINE側が受信位置の再同期を要求しました。欠落の可能性があるため停止しました。",
    "sync_cursor_unverified": "再同期の位置を検証できません。保存済み位置を保持しました。",
    "server_event_exception": "LINE側から受信エラーが返りました。接続状態を確認してください。",
    "unsupported_event": "未対応の受信イベントです。対応形式の調査が必要です。",
    "invalid_operation_batch": "受信イベントの形式が想定と異なります。保存位置は進めていません。",
    "duplicate_operation_revision": "同じ受信位置のイベントが重複しています。保存位置を保持しました。",
    "missing_account_identity": "受信するアカウントの本人確認情報がありません。",
    "missing_resume_cursor": "受信の再開位置がありません。初期接続を確認してください。",
    "invalid_message_event": "受信メッセージの形式が想定と異なります。",
    "invalid_message_id": "受信メッセージの識別子が不正です。",
    "invalid_recipient": "受信先の識別子を検証できません。",
    "invalid_sender": "送信者の識別子を検証できません。",
    "unexpected_direct_recipient": "保存先と異なるアカウント宛の受信を拒否しました。",
    "sse_open_rejected": "LINE側が新着接続を受け付けませんでした。認証状態を確認してください。",
    "stream_transport_failed": "新着接続が3回続けて失敗しました。通信状態を確認し受信を再開してください。",
    "login_required": "保存済み接続とアカウントが一致しません。ローカルのloginで確認してください。",
    "paused": "本人の一時停止設定に従って受信を停止しました。",
})
REASONS.update({
    "external_ai_not_authorized": "このアカウント・履歴ソースの外部AI利用が許可されていません。",
    "model_timeout": "AI応答が制限時間内に完了しませんでした。送信は行っていません。",
    "model_cli_failed": "AI実行が失敗しました。処理記録のexit_code・provider_signalを確認してください。",
    "model_cli_unavailable": "Codex CLIを起動できません。インストールと既存ログインを確認してください。",
    "assistance_partial_failure": "分類・返信案の一部が失敗しました。処理状態と原因コードを確認してください。",
    "assistance_interrupted": "前回の生成が中断されました。受信済みデータを保持し、復旧後に再試行できます。",
})
for _reason in ("model_cli_identity", "model_evidence_outside_context", "model_group_person_confusion",
    "model_prior_evidence_invalid", "model_name_evidence_invalid",
    "model_evidence_missing", "model_draft_incomplete", "model_draft_status_mismatch",
    "model_turn_failed", "model_diagnostic_error", "model_tool_call_rejected",
    "model_completion_missing", "model_output_invalid"):
    REASONS[_reason] = "AI出力が契約を満たしていません。入力世代とモデル検証の記録を確認してください。"
for _reason in ("context_source_changed", "context_snapshot_duplicate", "context_identity_missing",
    "context_identity_ambiguous", "context_kind_mismatch", "context_prior_missing",
    "context_source_count_mismatch", "context_incoming_missing", "context_message_too_large",
    "context_budget_exceeded", "context_uncheckpointed_wal", "context_owner_not_corroborated",
    "context_owner_binding_changed"):
    REASONS[_reason] = "履歴ソースの本人対応・整合性・入力範囲を検証できません。原因コードを確認してください。"
KNOWN_LINE_CODES = {0, 1, 5, 7, 8, 10, 20, 64, 82, 84, 85, 86, 87, 88, 90, 99, 100, 111, 116, 119, 10051, 10201, 10202}


class DiagnosticError(Exception):
    def __init__(self, stage, reason, *, http_status=None, line_code=None):
        self.stage = stage if stage in STAGES else "operation"
        self.reason = reason if reason in REASONS else "unexpected_error"
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        self.line_code = (
            line_code if type(line_code) is int and line_code in KNOWN_LINE_CODES else None
        )
        super().__init__(REASONS[self.reason])


def diagnostic_error(stage, error):
    if isinstance(error, DiagnosticError):
        return error
    try:
        import sqlite3

        from keyring.errors import KeyringError
        from okline.exceptions import LineApiError, LineTransportError

        if isinstance(error, LineApiError):
            return DiagnosticError(
                stage, "line_api_rejected", http_status=error.status, line_code=error.code
            )
        if isinstance(error, LineTransportError):
            return DiagnosticError(stage, "line_transport_failed", http_status=error.status)
        reason = "unexpected_error"
        for category, value in (
            (KeyringError, "keyring_failed"),
            (PermissionError, "permission_denied"),
            (FileNotFoundError, "file_missing"),
            (ImportError, "dependency_missing"),
            (sqlite3.Error, "storage_failed"),
            (ValueError, "invalid_input_or_state"),
            ((KeyError, TypeError, AttributeError), "unsupported_schema"),
        ):
            if isinstance(error, category):
                reason = value
                break
        return DiagnosticError(stage, reason)
    except Exception:
        return DiagnosticError(stage, "unexpected_error")


def format_diagnostic(operation, error):
    safe = diagnostic_error("operation", error)
    allowed_operations = {
        "init",
        "login",
        "status",
        "mcp",
        "sync",
        "worker",
        "approve",
        "pause",
        "resume",
        "export",
        "pair",
        "revoke-remote",
        "stage-media",
        "correct-memory",
        "allow-template",
        "revoke-template",
        "assist-config",
        "assist",
    }
    operation = operation if operation in allowed_operations else "other"
    parts = [f"operation={operation}", f"stage={safe.stage}", f"reason={safe.reason}"]
    if safe.http_status is not None:
        parts.append(f"http={safe.http_status}")
    if safe.line_code is not None:
        parts.append(f"line_code={safe.line_code}")
    return f"エラー [{' '.join(parts)}] {REASONS[safe.reason]}"
