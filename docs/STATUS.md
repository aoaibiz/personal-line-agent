# Verification status — 2026-10-24

Release candidate: **0.3.1 candidate / source preview**. This is an engineering verification record, not a guarantee for all accounts. Clarified requirement: human terminal use is allowed during initial installation, configuration and authentication. After setup, operation, updates, reconnection and recovery must not require human PC interaction. An initial-setup GUI is not a completion requirement.

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

- Smartphone-only forced LINE reauthentication and E2EE repair, fixed-entry process death/upgrade, receiver-service supervision, and OS login/keychain interaction. The verified backend update path does not establish these paths. Initial terminal-based installation/authentication is allowed.
- A fresh user's complete authentication and smartphone-to-host setup for every supported host.
- All LINE message/media types, every account configuration and future upstream behavior.
- Full smartphone history retrieval, expired media restoration, OpenChat and calendar integration.
- Unattended free-form replies; optional background inference currently produces drafts and separate explicit template policies are a different authority.
- A production public HTTP deployment and cryptographic audit of the external protocol implementation.

## Release protection

Public source is a fresh export, not the private development repository's commit history. Installed libraries remain separate dependencies. The default action authority is intentionally broad for a trusted local owner host; see SECURITY.md. A release can be stopped or replaced, but already downloaded public information cannot be recalled.

## Intentional recovery tests

[Recovery test results](RECOVERY-TESTS.md) record actual process/network/update failures. Receiver auto-restart and the nonterminal reauthentication entry did not meet the post-setup requirement. Existing data and permissions were preserved and the receiver was restored by the agent. These are product-completion blockers, not passing tests.

## 0.3.1 candidate: receiver supervision and owner authentication UI

- macOS launchd owns an account-scoped supervisor; only its own read-only worker is restarted. Real SIGTERM/SIGKILL of the worker recovered in 3.66/3.78 seconds; SIGTERM/SIGKILL of the supervisor recovered in 4.17/10.29 seconds. Each reached actual LINE `listening` without owner PC operation.
- Same database inode, contacts, integrity, authority hash and uncertain operations preserved. Message count did not decrease. A subsequent incoming test from the owner's second account was decrypted and stored once, 0.243 seconds after its server timestamp. This is live reception, not a stop-window backfill result.
- Fatal identity/cursor errors persist as blocked; pause is preserved. Store/keychain bootstrap failures wait in a blocked process instead of repeating OS prompts. Transient network errors back off. No outgoing action replay is performed by the supervisor.
- Added owner authentication capability/repair/status tools. Form and URL elicitation are relayed through the fixed bridge, including completion notification. No QR/PIN appears in tool results. Unsupported hosts stop before starting authentication. Cancellation and duplicate requests are covered by synthetic tests.
- Actual smartphone UI display, LINE-side forced expiry and completed phone-only reauthentication remain **unverified**. A synthetic MCP protocol test is not proof that a specific host supports the UI. Process death/host timeout during a real authentication flow remains unverified.
- OS reboot/login was subsequently checked on 2026-10-24; see RECOVERY-TESTS.md for the legacy-startup conflict and migration scope. A new OS user and Windows remain unverified. This is still a source preview, not a completed all-environments release.

### Final verification details

- Current source and an isolated non-editable 0.3.1 wheel passed the regression suite (265 tests). Ruff and distribution-content checks passed (wheel 32 entries, sdist 67 entries).
- Final macOS supervisor replacement completed after fixing asynchronous `launchctl bootout` teardown. The final supervisor was then killed and automatically returned to actual receiving in 11.26 seconds.
- A real stdio bridge exposed 35 tools (33 backend + 2 bridge), verified the actual LINE profile and database integrity, and refused authentication before starting it when the client lacked form/URL support.
- Independent correctness and security reviews found no unresolved critical/high code findings in this change. This does not waive the unverified smartphone authentication requirement.
- Dependency audit reported no known vulnerabilities for auditable registry packages. The pinned Git dependency and this unpublished application version were not in the registry advisory scan; their absence is not an audit pass for their source code.
- Reusable lesson: macOS service `bootout` may acknowledge before teardown finishes. Wait for the scoped service to disappear before bootstrapping its replacement; preserve the prior plist and account data for rollback. Verified 2026-10-24; applies to this launchd receiver deployment, not a claim about other platforms.
