# Verification status — 2026-10-24

Release candidate: **0.2.2 / source preview**. This is an engineering verification record, not a guarantee for all accounts. Clarified requirement: human terminal use is allowed during initial installation, configuration and authentication. After setup, operation, updates, reconnection and recovery must not require human PC interaction. An initial-setup GUI is not a completion requirement.

## Verified

- Fresh isolated project environment: 238 synthetic tests passed on macOS / Python 3.12. The tests cover real MCP stdio protocol with fake LINE adapters, storage, authorization, duplicate requests, unknown results, owner restrictions, history boundaries, media staging, receiver state and inference validation.
- Ruff source/test lint passed. Source distribution and wheel build passed; the wheel contains the 22 application modules and its own license, with no bundled third-party package roots.
- Independent correctness and security source reviews were performed. No unresolved critical/high finding was identified in the reviewed application scope. This is not an audit of every dependency's implementation or cryptography.
- The dependency advisory scan initially flagged certifi 2026.10.18 (PYSEC-2026-4320). The lock now uses 2026.10.23 and metadata requires at least 2026.10.19. The rescan reported no known vulnerabilities in the auditable packages. Of 69 installed packages, the VCS protocol dependency and this project itself were not covered by that advisory database.
- A conditional local-staging issue was fixed: this product's `pairing-*.json` credential files can no longer be staged as attachments. Two regressions failed before the fix and passed afterward. A privileged local owner can still read their own files; filename protection is not a general secret-content detector.
- Secret scanning used a positive control before the actual source scan. The two detected candidates were confirmed synthetic test strings. No actual credential was identified. Private histories, account stores, personal test receipts and private Git history are excluded from this source export.

## Earlier real-account tests

The application was used through its MCP path to send personal/group text, create and rename a group, change and restore a contact display name, send a mention, and send images/video. Results were read back and matched. The account owner confirmed mention display/notification and video operation. A separate owner-directed reply in an existing group was also sent and read back.

These tests establish the tested paths and account conditions only. API presence alone is not evidence of every attachment type, friend-add/invitation condition or unsupported conversation type working. The release test suite does not send to real accounts.

## Unverified / incomplete

- All post-setup update, reconnection, reauthentication and recovery paths without human PC interaction. Initial terminal-based installation/authentication is allowed and does not itself make the product incomplete.
- A fresh user's complete authentication and smartphone-to-host setup for every supported host.
- All LINE message/media types, every account configuration and future upstream behavior.
- Full smartphone history retrieval, expired media restoration, OpenChat and calendar integration.
- Unattended free-form replies; optional background inference currently produces drafts and separate explicit template policies are a different authority.
- A production public HTTP deployment and cryptographic audit of the external protocol implementation.

## Release protection

Public source is a fresh export, not the private development repository's commit history. Installed libraries remain separate dependencies. The default action authority is intentionally broad for a trusted local owner host; see SECURITY.md. A release can be stopped or replaced, but already downloaded public information cannot be recalled.
