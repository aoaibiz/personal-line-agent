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

## 2026-11-06 persisted-connection/OpenChat follow-up

- Additional desktop session: earlier QR success did not survive a fresh worker (`additional_authentication`, LINE8, HTTP200). After the owner quit native Mac LINE and completed QR again, fresh-worker reuse and repeated real read calls succeeded. Native app coexistence is unverified; root-cause attribution to competing sessions remains a hypothesis. No additional QR is currently pending.
- Active reviewed release: `aa45d907f28dcde7f169d4e02888cfeb0799e1d954aa24728b184a4bfb9dd612`; previous code-only rollback: `5962676e2f3b3727b9b4b6873647b0716355a6c2a172bafc4d4e240ee85a0a45`. Bridge2 unchanged. Account/keys/ledger not rolled back.
- Actual formal MCP after activation:49tools/27typed operations; `additional_connection_check` ok/live_verified; albums2 → first photo page6 → original JPEG1,326,814bytes, SHA256 `e90e386318c24313445b7b830c8fad6b3a7a18ce07407621bc7927a3dcffe3a0`, visually inspected again. Notes authoritative0. OpenChat joined communities54 and first community detail succeeded.
- Source live probes:55 membership-event chat entries, one chat detail,14members, two empty backward event pages with resume cursors. These do not prove nonempty event projection, complete history or pagination with a nonempty continuationToken. Contract fixtures cover distinct sync/page cursors and malformed message payloads.
- Original formal connection check:store integrityOK, identity preserved, E2EEready, connected, pausedfalse, uncertain operations3 untouched. Receiver cycled through starting/stopped; subsequent actual status showed listening and supervisor running the new release with failures0.
- Final source suite416passed/13skipped; Node5passed; Ruffclean. Wheel47entries/sdist112, new module included, forbidden runtime/account/dependency paths and production-ID scan clean. Two independent delta reviews fixed pagination, missing payload handling and the prefixed device-error status flag; no unresolved findings in that delta. Existing live write and Windows limitations remain.
- VOOM/birthday read paths returned application500 and were not shipped as working tools. Broader unintegrated families remain in COVERAGE; this record is not an all-reference-features-complete claim.

### 2026-11-06 authorized tablet connection trial

Owner approved IOSIPAD additional login. Fresh QR completed; same-owner public-key proof and a fresh saved-session check passed. Owner then logged into native Mac LINE; actual AX state showed conversation UI and no login controls. Additional saved-session check still passed, original connection_check returned connected/E2EE ready/receiver listening. Auth-only reviewed runtime `56ba0f022ad5d776d1da217139acbc5234b8ee29ebd63d2e6409621943ea29d3` activated and formal bridge additional_connection_check passed. This is observed coexistence, not a guarantee against future provider session revocation. No existing credentials/DB/ledger were rolled back.

The controlled VOOM/birthday read probes also returned application code500 with IOSIPAD. Device change therefore did not resolve that separate service issue. No failed response was converted into empty success.

### Square operation contract verification

Read-only current-account preflight fetched the chat and community and verified the same joined Square member identity. No live Square write was sent. Synthetic tests cover foreign identities, stale snapshots, own-message provenance, separate explicit target grants, unknown-result non-replay and no goodbye. Independent review caught and corrected the SDK response field `createdSquareMessage` and signed i64 revision serialization; the pinned Thrift serializer was used to verify a large BigInt revision round-trip. These checks do not claim actual sent/leave server acceptance.

### Call-link / LIFF candidate

431 Python tests passed, 13 environment-specific tests skipped; 14 Node contract tests passed. Ruff and worker syntax checks passed. Actual own call-link list returned a successful empty list; no link creation/update/delete or LIFF share was sent as a test. Independent contract/security reviews checked exact generated argument/response forms, owner/target bindings, no automatic LIFF consent, secret filtering, and final-result diagnostics.

## LIFF rich payload delta (2026-11-06)

Flex/template support added with bounded roots, structure, HTTPS URI fields and preparation-time validation. Independent review found non-string URI values and Python/JS Unicode length differences; fixed with explicit string validation and UTF-16 limits across rich and top-level text fields. Targeted Python8/Node7 passed; no live share or permission grant performed. Installed basic release46ed48518e844052a23415a286ee4f2d198937f8c165af6378382e1de851783e formal checks: original connected/E2EEready/receiverlistening, additional live_verified, call-link list0, capabilities50tools43operations. Rich delta pending activation.

## Cover and rich-message release candidate (2026-11-06)

437 Python tests passed/13 skipped;19 Node tests; Ruff clean. Source→sdist→wheel build passed with profile-cover module, preparation validator and BSD license included. Independent cover contract/security reviews passed after adding closed failure propagation, persisted intended upload identity, read-only reconciliation and sdist notice. No actual profile change or external send used as a test. Actual own-cover preflight passed; pending installed-tool verification recorded after activation below.

## Installed final candidate evidence (2026-11-06)

Sourcead7921a deployed as immutable releasea658dc54bf5e360227d41196eb5bf1b1a2a1f98f714cb8e7ef314189025744db. Previous46ed48518e844052a23415a286ee4f2d198937f8c165af6378382e1de851783e retained for code-only rollback. Formal bridge2:52tools/44typedoperations; set_profile_background present and absent from prior grants as expected.

Formal installed-tool checks: own background statusok/configured/namespacec; additional connectionok/live_verified; original storeintegrityok,connected,identitysaved,E2EEready,receiverlistening,pausedfalse. Three existing uncertain operations unchanged. Owner-confirmed native Mac LINE login and actual conversation UI were observed before these checks; no additional authentication occurred afterward.

Installed albums_list→album_photos_list→album_media_get returned2albums,6photos,original1326814bytes with SHA256e90e386318c24313445b7b830c8fad6b3a7a18ce07407621bc7927a3dcffe3a0, identical to the visually inspected original. A second authorized group returned authoritative0albums. No message/profile writes were performed during deployment verification. These real reads do not establish live write availability for all new operations.

## OpenChat completion candidate (2026-11-06)

Owner narrowed the remaining scope: OpenChat completion; birthday best effort; VOOM may be omitted. Current official LINE notice confirms VOOM/Birthday Cards retirement; own extended-profile birthday status read succeeds. Date/year are not returned and friend birthday retrieval is not claimed.

- Source actual read: joined communities54; community-leave preflight proved joined membership without leaving. Canonical SDK owner is bound by the existing saved public-key proof, not string equality with Chrome opaque MID.
- Source actual read: public search40 results → official cover href/Nuxt ticket equality → findSquareByInvitationTicketV2 → canonical Square identity succeeded; join_performed false. Failed authenticated search501/emid400 paths are not used as working fallbacks.
- Regression447passed/13environment-specific skipped; Node26passed; Ruff and diff checks passed.
- Actual pinned offline Thrift serialization: createSquare175bytes, joinSquare110, inviteIntoSquareChat99, destroyMessage62 with synthetic input, network forbidden. Independent review separately serialized V2 ticket lookup. Actual25MiB Python→Node frame test passed.
- Build source→sdist→wheel passed;55wheel entries/130sdist entries. Required new modules/docs present, no node_modules/account database/env/auth files packaged.
- Two independent delta perspectives passed after fixing create-only authorization, scoped media lock and stage-specific search diagnostics. Final resolver verified fixed host/no redirects/bounds, matching link/structured ticket, exact V2 request, owner-bound reference and no grant expansion. Private artifacts exclude account data and are not included in the OSS.
- Live writes intentionally not performed: sends, invites, joins, creation, deletion, rename and departure still require an actual owner-requested use. Configuration completion is not a claim of live server acceptance for these operations.

## Installed OpenChat completion evidence (2026-11-06)

Source2b5587d activated as immutable release32bc85ed34257f6a5c946332caea1fd30bc73e7e76c6a704d0e09d72400808ea. Previousa658dc54bf5e360227d41196eb5bf1b1a2a1f98f714cb8e7ef314189025744db retained. Stable bridge2 reports56tools/51operations. Formal openchat_search returned40items; openchat_resolve_public returnedok/canonical namespace/join_performedfalse; openchat_list returned54joined communities. birthday_profile_status returnedok/own_extended_profile/friend_birthdays_supportedfalse, no actual date/year output. Additional connectionok/live_verified; original integrityok,identitysaved,connected,E2EEready,receiverlistening,pausedfalse. Existing3uncertain operations unchanged. No authentication/grant changes or external writes performed during this activation.

## Windows transport correction

GitHub run42152069930 (Windows) exposed real WinError10038 in subprocess-pipe select, a POSIX mode assertion on Windows and an unsupported runner Node bootstrap. Replaced Windows-only pipe waiting with a bounded8-chunk background reader; POSIX selector behavior retained. Tests preserve POSIX0600 assertions and use actual strict Windows ACL checks there. CI now explicitly installs the already-required/tested Node24.22.1 using pinned official setup-node action instead of relying on runner defaults.

Local full regression450passed/13skipped. Actual synthetic25MiB framing tested through both readers;2MiB output drained through EOF, bounded queue/thread shutdown and safe failed-read diagnostic tested. Two independent delta reviews passed after restoring the POSIX permission assertion. No credential/authority changes. Windows result after the correction is recorded separately; source correction is not claimed as actual Windows LINE authentication.

Follow-up Windows run42152987693 confirmed select/ACL/staging errors were resolved, but exposed startup-without-output and large-input EINVAL. Windows uppercase SYSTEMROOT was missing from the prior case-sensitive environment filter; the allowlist now retains that OS bootstrap variable while excluding loader variables. Input frames use64KiB chunks with partial-write handling. A dedicated real Node bootstrap test checks the sanitized environment without printing it. Windows CI executes these targeted tests before the full suite to locate failures promptly.

Independent review additionally reproduced an unbounded write when a synthetic child stopped reading stdin. Input now has a30-second deadline; timeout kills only that dedicated child, joins the writer and emits closed pipe_write_timeout. A2MiB blocked-input test with0.2-second deadline passed. Both delta reviewers verified the correction. No account state is saved on input failure and no write is retried.
