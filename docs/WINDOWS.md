# Windowsへの導入

Windowsのデスクトップ利用者として動かす手順です。WSLのPythonではなく、Windows版Python・Windows資格情報マネージャー・Windowsの受信サービスを使います。既存のLINEアプリの接続情報を取り出す機能はありません。初回は本人のスマホで専用のQR認証を行います。

## 導入担当者の手順

Python 3.11以上の64bit版、Git、uv、Node.js、本人のWindowsログイン環境を用意します。個人データはソースやリリースと別のフォルダーに保存します。保存先を変更すると別の暗号鍵・アカウントとして扱われるため、導入後は同じ絶対パスを使います。

PowerShellで取得済みリポジトリを開きます。

```powershell
uv sync --frozen --python 3.11
.\.venv\Scripts\line-agent.exe init
.\.venv\Scripts\line-agent.exe login
```

最後のコマンドは本人が見られる対話端末で実行します。QRとPINはその画面だけで確認し、AIチャット・スクリーンショット・ログ・GitHubへ転記しません。ログイン中のLINEアプリから認証情報をコピーしません。スマホの本人確認を代理で承認しません。

既定のデータ保存先は`%USERPROFILE%\.local\share\personal-line-agent\personal`です。別名を選ぶ場合は、初回から全コマンドで`--account 名前`を指定します。新しいログインでは既定の操作権限が設定されますが、利用者の指示なしに自動返信を開始する設定ではありません。

## Codexへの固定入口の登録

実接続を確認した後、[運用手順](OPERATIONS.md)の`stage`と`activate`で、データとは別の管理フォルダーへ不変のリリースを作ります。WindowsのPythonパスは`FIRST_RELEASE\.venv\Scripts\python.exe`です。バックエンド更新のたびにMCP設定を書き換えません。

管理フォルダーは深く長いパスを避けます。リリースIDと依存ライブラリの階層が加わるため、Windowsのパス長制限で初期化に失敗する場合があります。

```powershell
codex mcp add personal-line -- C:\absolute\MANAGED_HOME\releases\FIRST_RELEASE\.venv\Scripts\python.exe -I -B -m line_agent.bridge --home C:\absolute\MANAGED_HOME --root C:\absolute\ACCOUNT_ROOT --account personal
```

パスとリリース名は導入担当が実際の値に置き換えます。初回登録時のホスト接続で`runtime_status`と`connection_check`を確認します。設定ファイルに記載されたことだけでは接続成功と判断しません。Codexの設定方法は[公式MCPドキュメント](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)を参照してください。

## Windowsの受信サービス

導入担当は、固定入口と同じ不変リリースのPythonを使い、専用のタスクを登録します。実在するNode.jsの絶対パスを指定してください。

```powershell
& 'C:\absolute\MANAGED_HOME\releases\FIRST_RELEASE\.venv\Scripts\python.exe' -I -B -m line_agent.supervisor --home C:\absolute\MANAGED_HOME --root C:\absolute\ACCOUNT_ROOT --account personal --node C:\absolute\node.exe install
& 'C:\absolute\MANAGED_HOME\releases\FIRST_RELEASE\.venv\Scripts\python.exe' -I -B -m line_agent.supervisor --home C:\absolute\MANAGED_HOME --root C:\absolute\ACCOUNT_ROOT --account personal start
```

Windowsタスクスケジューラにアカウント専用のタスクを登録します。本人のWindowsログイン後に動き、パスワードを保存せず、管理者権限での実行を要求しません。PC電源OFF・本人のWindowsログイン前の受信は保証しません。他のLINEアプリや既存の起動項目は変更しません。

タスクは不変リリース内の`pythonw.exe`を使い、受信のたびに端末を開きません。ログイン時と1分間隔の起動確認を設定し、実行中は重複起動しません。監督プロセスの終了時は配下の処理も終了します。同名タスクが既にある場合は上書きしません。監督自体を更新する担当者は既存定義を退避し、専用タスクだけを停止・置換してから実接続を確認します。

同じCLIの`status`でタスク状態を、正式MCPの`agent_status`で受信監督・受信処理の状態を、`connection_check`で実通信を確認します。タスクが実行中というだけでは接続成功と判断しません。停止には同じCLIの`stop`を使います。既存の停止・制限・結果不明の操作を勝手に解除せず、診断後の受信再試行は`retry`を使います。

`stop`は専用タスクを無効化し、実際の実行インスタンスが終了するまで確認します。停止後に定期起動やログインで勝手に復活しません。再開は`start`で行います。管理コマンドを経ずプロセスだけを終了した場合は、定期起動による復帰の対象です。再起動時刻はOSの負荷・電源・ログイン状態にも依存します。

## 保存と復旧

鍵はWindows資格情報マネージャーに保存し、アカウントのレコードは暗号化します。OS利用者を変えたり鍵を削除したりすると、以前のデータを開けません。復旧目的でDB・鍵・受信位置・結果不明の送信記録を削除しません。

`doctor`で実接続・DB整合性・結果不明の操作を確認します。接続が正常なら再認証は不要です。コードを戻す場合もアカウントのデータは戻しません。失敗した送信は同じ依頼UUIDで状態を確認します。

WindowsではNTFSのアクセス権で本人・SYSTEM・管理者だけに書込みを許可し、データや実行環境をジャンクション経由に置きません。既存の共有実行環境は受け入れず、本人専用の新しい管理フォルダーへ導入します。別プロセスの同時送信・同時受信はWindowsのファイルロックで調停します。

Windows実機で本人QR認証、正式MCPからのテキスト送信と読戻し、返信のイベント受信、通常の停止・再起動、同じMCP接続を保持した版の切替・切戻しを確認しています。新しいOS利用者、PC本体の再起動、スマホだけでの強制再認証は別の未検証範囲です。異常終了からの自動復帰については[STATUS.md](STATUS.md)の最終検証結果を確認してください。
