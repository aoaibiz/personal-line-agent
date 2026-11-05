# 0.6.0 verification — 2026-11-05 / 06

## Real owner path

- Existing Chrome connection remained connected, E2EE ready, store integrity OK, receiver listening. Three pre-existing uncertain operations remained unchanged; none retried.
- Owner completed a separate desktop QR/PIN flow. E2EE public-key identity proof matched; encrypted state saved and reloaded. No existing connection or receiver replaced.
- Requested group mapped across ID namespaces by two distinct message IDs and exact destination, not group name alone.
- Through the formal MCP stable bridge after activation: album list returned two albums; first photo page returned six photos and `hasMore=false`; original JPEG saved, 1,326,814 bytes. SHA-256 matched the pre-activation original, which was displayed and visually checked.
- Initial formal `capabilities_list`: backend0.6.0,44 tools,18 typed operations. Later source adds profile/note reads and27 typed operations; final activation evidence is recorded below. Discovery is not authorization.

## Scoped limitations

- Earlier Note500 failure is fixed in source: real current endpoint returned code0/homeInfo.postCount0. Nonempty Note text and write behavior remain unverified.
- Earlier historical video failure is fixed in source:767210-byte MP4 recovered using authenticated chunk-hash format, full decode passed and frame visually inspected. Backward pagination retrieves the same original.
- No unsolicited live write was used to test reactions, unsend, kick, leave, contact changes or other new write kinds. Schema, SDK dispatch, recipient context and operation-ledger behavior were tested with fixtures. Actual account-specific availability remains unverified.
- Separate-connection QR UI was tested on macOS only. Windows and other hosts were not tested for that UI.
- Initial authentication remains an owner action. Agent performs internal installation and diagnostics; no human terminal work is requested.
- The broader optional families in COVERAGE.md are not silently advertised as integrated.

## Verification checkpoints

- Python regression: 391 passed, 13 skipped after the diagnostic/discovery additions.
- Node identity/message-proof tests: 2 passed. Ruff passed. Wheel and sdist content checks passed; rebuilt after license inclusion.
- npm advisory audit: zero known findings with locked Thrift 0.23.0 override. Python registry audit: zero known findings with Thrift 0.24.0; pinned Git OkLine is outside the registry advisory scan.
- Independent perspectives: additional-auth/credential preservation; typed operations/media; content mapping/runtime. Reviewed findings fixed; no confirmed Critical/High remained in those reviewed scopes. Private review artifacts are kept outside the public repository; no account content or secrets included here.

## Runtime and rollback

The owner agent stages an immutable candidate and activates only after same-account connection/integrity checks. The stable bridge does not change. On rollback select the previous immutable backend using the documented runtime management command; preserve account database, additional credentials, receive cursor and UUID ledger. No account-data rollback.

## Final core-fix activation (2026-11-06)

Immutable release `5962676e2f3b3727b9b4b6873647b0716355a6c2a172bafc4d4e240ee85a0a45` activated after same-account integrity/connection check. Previous release retained.
- Stable formal MCP: Note list code0/oktrue, authoritative total_count0.
- Stable formal MCP: historical video saved767210bytes, SHA256 `a2bca8dc4d98588989f7905bb039a70a6d6b27a850853feb3b5257e51106e9c2`, matching decoded working-source original.
- Stable formal MCP:2albums→6photos→1326814-byte original JPEG, same hash as visual verification.
- Stable formal MCP: own profile read ok; capabilities46tools/27typed operations.
- Stable connection_check: store_integrityok, identity_savedtrue, connection_verifiedtrue, E2EEready, receiverlistening, pausedfalse. Uncertain operations remain3, none retried.
- Final regression:408passed,13skipped. Node3passed, Ruff clean. Independent delta review fixed Note attachment loss, stale-note preflight and contact-card participant authorization; auth/security review also passed. No live write/delete tests or newly expanded operation grants.

The core failures are resolved and the installed tool verified. The separately listed broader feature families remain work in progress; this activation does not claim the full reference feature set.
