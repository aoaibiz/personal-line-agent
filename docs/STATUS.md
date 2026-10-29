# Verification status — 2026-10-25

Current source: **0.4.1 / executable lookup security fix**. This is an engineering verification record, not a guarantee for all accounts. The existing macOS real-account results remain recorded below; Windows results are scoped separately. Phone-only forced reauthentication remains unverified. Human terminal use is allowed during initial installation, configuration and authentication; after setup, ordinary operations and backend updates are performed by the owner agent. See [FINAL-REVIEW.md](FINAL-REVIEW.md).

## 0.4.1 security verification

- [Three independent review sets](SECURITY-REVIEW-2026-10-25.md) covered storage/authentication, authorization/untrusted input, and runtime/supply chain. One High executable-search issue was safely reproduced and fixed across its matching application callers. Two independent reviewers rechecked the patch and synthetic resolution probes; no demonstrated Critical/High remains in that scope.
- Native Windows / Python 3.11: repository suite **331 passed, 5 skipped**. Three additional private authorization probes passed after correcting their HTTP test harness initialization order; the initial combined run's sole failure was that harness error, before any HTTP request. Counts are separate, not repeated totals. OS/privilege/opt-in checks remain scoped by their skip conditions.
- New regression coverage includes uv/Node/Codex CWD and relative-PATH rejection, the real harmless scheduler executable substitution, explicit SDK Node selection, missing-Node failure and pinned Codex executable reuse. Ruff passed. Pinned-backend wheel and sdist build/content checks passed (34 and 80 entries at the build checkpoint).
- The associated PR's macOS/Linux/Windows matrix supplies final cross-platform checks. Live-account evidence for this security update is read-only connection/receiver continuity after replacing the backend and registered callers; no new send or owner reauthentication is required. Earlier real send/reply evidence remains separately described below.

## 0.4.0 Windows verification

- Native Windows / Python 3.11 x64: full suite **317 passed, 3 skipped**. Skips cover POSIX-only delegation compatibility and unavailable Windows symlink creation privilege; actual Windows junction rejection was tested. A subsequent first-use lock race fix passed 18 focused store/runtime tests (2 symlink skips), including concurrent first creation. An independent, controlled two-process probe confirmed the precise create-before-lock race was removed.
- Final Linux / Python 3.12 regression suite: **313 passed, 8 skipped**, including the existing POSIX delegation digest contract. Windows-only OS tests were skipped there. This is Linux regression evidence, not a native macOS run.
- The final scheduler change passed **18 Windows tests, 1 opt-in probe skipped** and **16 Linux tests, 3 OS/probe skips**. The opt-in real Windows scheduler probe was run separately and passed; these counts overlap earlier suites and are not added together.
- Private Windows ACLs, binary file integrity, cross-process exclusion/release and worker-descendant cleanup were verified. A harmless real Task Scheduler task was registered, started, observed writing its marker, stopped and removed. Later, the account-scoped receiver task reached actual LINE `listening`.
- Owner QR/PIN authentication completed with Windows Credential Manager and encrypted storage. The immutable bridge sent one owner-authorized personal text through `operation_run`; `operation_status` reported sent and remote history contained exactly one matching external message. The owner replied from the other account, and the event receiver decrypted and stored that reply with its stream delivery record. Read-back and real event reception are separate evidence.
- The same live bridge session selected a second immutable release and rolled back to the first. Both real connection checks passed. Repeating the already-completed logical request with the same UUID returned the existing external ID with `already_executed=true`; it did not produce a second send. These Windows candidates contain the same application behavior with documentation differences; this is not a claim that the old macOS-only release runs on Windows.
- Normal receiver stop/start returned to actual listening without owner reauthentication. The same database file identity, authority digest, existing reply and integrity result were preserved. Supported Codex CLI registration and actual tool calls from both Codex CLI and the current desktop chat verified the fixed entry and saved LINE connection.
- On this Windows host, `RestartOnFailure` alone did not restart a manually started interactive task after abnormal process exit; a harmless generated-XML probe reproduced that result. The receiver now also uses a one-minute time trigger with `IgnoreNew`. After installing the reviewed correction, terminating the actual account supervisor caused a new PID to return to LINE listening automatically. Intentional stop then kept the task disabled with zero running instances for more than 70 seconds, spanning the timer interval; explicit start resumed it. This is process recovery, not a Windows reboot/logoff test.
- An immediate stop/start check later exposed Task Scheduler reporting zero instances while the actual supervisor was still running. Stop completion now also requires simultaneous nonblocking acquisition of both account execution locks. Start refuses to clear the stop request while old execution remains; unreadable store/lock state is not reported as success. This correction received focused regression tests and independent review; real process exit and restart are checked separately from Scheduler's instance count.
- Independent correctness/security remediation review found no remaining demonstrated Critical/High findings in the reviewed source. It also verified POSIX integer-UID delegation binding compatibility, preserved runtime directory permissions and account-directory privacy. The existing macOS live account was not accessed during this work.
- Ruff, wheel/sdist builds and distribution-content checks passed (34 wheel entries, 77 sdist entries). The advisory scan reported no known vulnerabilities in auditable installed registry packages; the pinned VCS protocol dependency and this application itself were excluded from that database scan. This is not a cryptographic or comprehensive dependency-source audit.
- Source/history screening used positive controls. Current candidate matches were dependency metadata and synthetic test identifiers; no actual credential was identified. Private account data and operational evidence remain outside the public repository.
- The first remote matrix passed on macOS and Linux (317 tests passed, 9 skipped on each). Windows CI exposed an elevated-token ownership difference: new objects defaulted to Administrators while strict reopening required the actual user. Creation now names the user owner, and explicit initialization normalizes ownership without weakening strict checks on existing objects. The freshly installed uv launcher is normalized before use; ready releases remain strictly checked. Real creation/reopening and a full uv stage/reuse regression cover this path. The final PR checks are the authority for the corrected matrix result.
- Native macOS real-account retesting, Windows reboot/logoff, and fresh-OS-user installation remain unmeasured for this release. See [Windows instructions](WINDOWS.md).

## Earlier 0.2.2 checks

- Fresh isolated project environment: 238 synthetic tests passed on macOS / Python 3.12. The tests cover real MCP stdio protocol with fake LINE adapters, storage, authorization, duplicate requests, unknown results, owner restrictions, history boundaries, media staging, receiver state and inference validation.
- Ruff source/test lint passed. Source distribution and wheel build passed; the wheel contains the 22 application modules and its own license, with no bundled third-party package roots.
- Independent correctness and security source reviews were performed. No unresolved critical/high finding was identified in that reviewed application scope. This is not an audit of every dependency's implementation or cryptography.
- The dependency advisory scan initially flagged certifi 2026.10.18 (PYSEC-2026-4320). The lock now uses 2026.10.23 and metadata requires at least 2026.10.19. The rescan reported no known vulnerabilities in the auditable packages. Of 69 installed packages, the VCS protocol dependency and this project itself were not covered by that advisory database.
- A conditional local-staging issue was fixed: this product's `pairing-*.json` credential files can no longer be staged as attachments. Two regressions failed before the fix and passed afterward. A privileged local owner can still read their own files; filename protection is not a general secret-content detector.
- Secret scanning used a positive control before the actual source scan. The two detected candidates were confirmed synthetic test strings. No actual credential was identified. Private histories, account stores, personal test receipts and private Git history are excluded from this source export.

## 0.3.0 candidate: post-setup backend maintenance

- Added a fixed stdio entry and separate versioned backends. A single live MCP client session successfully selected backend A, switched to B, and rolled back to A; each actual profile/integrity check passed. The same account database inode, contact count and integrity result were preserved. Existing unknown operations were retained and never retried.
- Using the installed non-editable entry, sent one owner-authorized test message, switched to the previous backend, repeated the **same request UUID**, and received the existing receipt rather than another send. After switching forward, real LINE history contained exactly one matching message with the same external ID. No terminal interaction or per-message confirmation was requested from the owner.
- Fresh isolated wheel installation: **249 tests passed**. Current source: **249 tests passed**; changed-source targeted regressions: **11 passed**. Ruff passed; release wheel has 30 entries and sdist has 60 entries at the recorded build. Dependencies remain external. Secret scan of 60 source files returned only the same two previously verified synthetic test strings.
- Independent correctness/security review found and fixed: failed-stage retry requiring cleanup (medium), inability to recover when the previously selected backend is damaged (medium), and inherited working-directory/module-path shadowing of the selected Python package (high). The latter requires attacker-controlled code in the agent working directory; it could execute instead of the selected package. Child Python now uses isolated mode, with a hostile-directory regression. Account data and remote LINE text cannot choose a release or supply a shell command.
- Backend installation, activation and rollback are trusted local owner-agent management, not MCP tools. The normal MCP does not grant arbitrary filesystem/code-installation authority. See [operations](OPERATIONS.md).

## Earlier real-account tests

The application was used through its MCP path to send personal/group text, create and rename a group, change and restore a contact display name, send a mention, and send images/video. Results were read back and matched. The account owner confirmed mention display/notification and video operation. A separate owner-directed reply in an existing group was also sent and read back.

These tests establish the tested paths and account conditions only. API presence alone is not evidence of every attachment type, friend-add/invitation condition or unsupported conversation type working. The release test suite does not send to real accounts.

## Unverified / incomplete

- Smartphone-only forced LINE reauthentication and E2EE repair, fixed-entry process death/upgrade, and a new OS user/keychain setup. Receiver-service supervision and the observed OS restart are covered by the later dated records below. Initial terminal-based installation/authentication is allowed.
- A fresh user's complete authentication and smartphone-to-host setup for every supported host.
- All LINE message/media types, every account configuration and future upstream behavior.
- Full smartphone history retrieval, expired media restoration, OpenChat and calendar integration.
- Unattended free-form replies; optional background inference currently produces drafts and separate explicit template policies are a different authority.
- A production public HTTP deployment and cryptographic audit of the external protocol implementation.

## Release protection

Public source is a fresh export, not the private development repository's commit history. Installed libraries remain separate dependencies. The default action authority is intentionally broad for a trusted local owner host; see SECURITY.md. A release can be stopped or replaced, but already downloaded public information cannot be recalled.

## Intentional recovery tests

[Recovery test results](RECOVERY-TESTS.md) record actual process/network/update failures. Receiver auto-restart and the nonterminal reauthentication entry did not meet the post-setup requirement. Existing data and permissions were preserved and the receiver was restored by the agent. These were failures in that earlier version, not passing tests. Later receiver-supervision fixes are recorded below; smartphone-only reauthentication remains excluded and unverified in the owner-approved release scope.

## 0.3.1 candidate: receiver supervision and owner authentication UI

- macOS launchd owns an account-scoped supervisor; only its own read-only worker is restarted. Real SIGTERM/SIGKILL of the worker recovered in 3.66/3.78 seconds; SIGTERM/SIGKILL of the supervisor recovered in 4.17/10.29 seconds. Each reached actual LINE `listening` without owner PC operation.
- Same database inode, contacts, integrity, authority hash and uncertain operations preserved. Message count did not decrease. A subsequent incoming test from the owner's second account was decrypted and stored once, 0.243 seconds after its server timestamp. This is live reception, not a stop-window backfill result.
- Fatal identity/cursor errors persist as blocked; pause is preserved. Store/keychain bootstrap failures wait in a blocked process instead of repeating OS prompts. Transient network errors back off. No outgoing action replay is performed by the supervisor.
- Added owner authentication capability/repair/status tools. Form and URL elicitation are relayed through the fixed bridge, including completion notification. No QR/PIN appears in tool results. Unsupported hosts stop before starting authentication. Cancellation and duplicate requests are covered by synthetic tests.
- Actual smartphone UI display, LINE-side forced expiry and completed phone-only reauthentication remain **unverified**. A synthetic MCP protocol test is not proof that a specific host supports the UI. Process death/host timeout during a real authentication flow remains unverified.
- OS reboot/login was subsequently checked on 2026-10-24; see RECOVERY-TESTS.md for the legacy-startup conflict and migration scope. A new OS user and Windows remain unverified. This does not establish a completed all-environments release.

### Final verification details

- Current source and an isolated non-editable 0.3.1 wheel passed the regression suite (265 tests). Ruff and distribution-content checks passed (wheel 32 entries, sdist 67 entries).
- Final macOS supervisor replacement completed after fixing asynchronous `launchctl bootout` teardown. The final supervisor was then killed and automatically returned to actual receiving in 11.26 seconds.
- A real stdio bridge exposed 35 tools (33 backend + 2 bridge), verified the actual LINE profile and database integrity, and refused authentication before starting it when the client lacked form/URL support.
- Independent correctness and security reviews found no unresolved critical/high code findings in this change. This does not waive the unverified smartphone authentication requirement.
- Dependency audit reported no known vulnerabilities for auditable registry packages. The pinned Git dependency and this unpublished application version were not in the registry advisory scan; their absence is not an audit pass for their source code.
- Reusable lesson: macOS service `bootout` may acknowledge before teardown finishes. Wait for the scoped service to disappear before bootstrapping its replacement; preserve the prior plist and account data for rollback. Verified 2026-10-24; applies to this launchd receiver deployment, not a claim about other platforms.

### Authentication diagnostic follow-up

- An actual host advertised form/URL support but returned an immediate non-accept response to the start confirmation. The owner reported that no confirmation screen appeared. LINE login did not begin; the existing connection, E2EE and receiver remained working. The earlier implementation collapsed decline/cancel/missing confirmation into one cancelled result, so the exact original host action cannot be recovered from that result.
- Diagnostics now separate those outcomes and record only closed stage/action values, login invocation, and per-stage response time. No credential, QR/PIN or arbitrary host metadata is stored. This change does not relax authentication or host approval policy, and does not itself fix host UI availability.
- Current source regression suite: 269 tests passed. Independent correctness/security delta reviews: 13 targeted tests passed, with no unresolved critical/high finding in this delta. The review caught and fixed response timing being carried into a later unanswered stage.
- Smartphone-only reauthentication remains incomplete. Host policy preventing UI is a supported explanation from public source and the current execution policy, not a traced proof of the installed host's exact internal branch.

### Current QR route compatibility

- After the owner selected an interactive confirmation mode, a real MCP request passed the initial confirmation and reached LINE authentication. It stopped before displaying the QR link because the validator omitted LINE's current `https://line.me/R/au/lgn/sq/<opaque>` route.
- A separate unauthenticated SDK session confirmed that route without reading an account credential, printing a link, approving a login, or rotating the existing session. The validator now accepts only the specified route and a URL-safe identifier on the exact HTTPS host. Userinfo, explicit ports, fragments, other hosts, extra path components and encoded path traversal remain rejected. The SDK query is forwarded unchanged only to the host's authentication UI.
- Successful route validation is not completed LINE reauthentication; the real owner flow remains the acceptance test.
- Both the selected backend and the fixed bridge validate authentication URLs. Bridge version 2 identifies the corrected entry; switching only the backend cannot fix a version 1 entry retained by the host. An existing entry must be reconnected through the host's supported lifecycle; no policy bypass or in-place mutation of an immutable release is used.
- Current source passed 284 regression tests; independent correctness/security delta checks passed 28 targeted tests, including actual stdio relay of the current synthetic URL and rejection of a lookalike host before any owner UI callback. Actual phone-only completion remains unverified.
