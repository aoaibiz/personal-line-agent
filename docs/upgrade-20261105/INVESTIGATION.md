# Capability recovery investigation (2026-11-05)

This report separates observed behavior from hypotheses. It contains no chat bodies, account IDs, authentication material or copied private logs.

## What failed previously

The old album candidate indexed album-add notifications in a recent-message page. That is useful discovery evidence, not an authoritative album list. Its channel probe reported HTTP 400 / LINE code 6 and zero originals. That evidence locates a failure at channel issuance; it does not prove a permanent account restriction, nor explain every later OBS 403.

## What this investigation established

Pinned LINEJS source has a dedicated album channel approval method, Moa JSON APIs and original download headers. Our pinned Chrome SDK lacks that approval method in its RPC registry. Calling it by name fails locally; sending the guessed Chrome gateway route returns HTTP 404 / code 10001. Neither is proof that the account cannot access albums.

The direct legacy channel probe failed at transport. An encrypted-LEGY experiment timed out, but source inspection then showed `/CH4` is outside LINEJS's auto-encrypted Talk paths. That experiment is not an upstream-equivalent test and is not used in production. Do not keep changing headers without confirming the request path and protocol first.

The Note channel and Chrome `timeline_home_id` calls succeed. Note content reads timed out; that is unreceived data, not an empty Note list. The returned home ID is an opaque string, not a canonical Talk MID. Treating it as a canonical `u` + 32 hex ID was a new integration mistake caught by an actual schema probe. It was corrected; no identifier is fabricated from a name.

A separate, owner-approved DESKTOPMAC connection is isolated from the existing Chrome session. Same-owner binding uses the existing account's server-returned public keys and the new account's own public keys. Only public-key hashes and key IDs cross that boundary. An independent reviewer caught another wire-contract mistake: LINEJS's `getE2EEPublicKeys()` returns numeric raw-Thrift fields (2/4), not named JSON fields. Raw binary may be a Uint8Array or lossless UTF-8 string, not base64. Contract fixtures now reject mismatched owner/key/representation.

The default regional endpoint timed out. The Japan endpoint progressed through QR creation to phone verification waiting. This is a different observed stage, not proof of completed login. The actual login and album retrieval result must be recorded separately after owner authentication.

The new media API reaches an old encrypted video but decryption fails with a safe ValueError category; this is not a successfully obtained video. Generic support and fixture tests do not establish that historical key availability is sufficient.

## Why the earlier approach did not complete the feature

Confirmed: the implemented path was notification indexing, the required channel method was absent from the chosen adapter, and original retrieval had not succeeded. The available historical record does not establish why work stopped at that point or the exact server-side reason behind code 6/403. Do not invent that history.

The correct response to a missing tool is an adapter-capability investigation, not a conclusion that the application cannot do it. The work must continue through protocol/library comparison, one actual read, schema evidence, and an explicit connection/permission decision where necessary. A method with a similar name or a passing mock is insufficient.

## General method (not LINE-specific)

1. Define the actual deliverable: authoritative list, original content, usable artifact. A notification, preview or filename is not the content.
2. Separate layers: exposed tool, adapter implementation, auth/device scope, request protocol, response schema, download, decryption, rendering.
3. Pin the working reference version and inspect its exact call chain and license. Compare host, verb, headers, body, identity namespace and parser. Avoid raw-API escape hatches.
4. Run one bounded authorized real read. Record safe stage/status/error category; do not capture secrets or full external responses. Failed/unavailable/partial/empty-success are distinct states.
5. If two equivalent attempts fail, change the hypothesis or design rather than retry the same request. Investigate an alternate supported adapter or native application path. Authentication/design changes need owner authorization; ordinary diagnosis does not.
6. Test API-shaped fixtures, cross-account/cross-chat references, wrong schemas, pagination, cancellation, partial saves, timeouts and restart/unknown outcomes. Independently review the actual parser and call site, not only tests written by the implementer.
7. Preserve the known-good connection and state. Deploy code separately with rollback; never roll back keys, receive cursors or send ledger.
8. State implementation, configuration and live evidence separately. An authentication timeout is not 'waiting for an incoming message'. Do not call a feature working until its actual deliverable has been obtained.

## Reuse boundary

These steps are applicable to capability gaps in other integrations. Specific LINE channels, protocols and identity fields are not general rules. The evidence supports adding the investigation checklist and regression cases; it does not prove that any unsupported external API can always be made available.

## Real album original, 2026-11-05

Additional QR/PIN authentication completed; saved credentials reloaded successfully without repeating authentication. Owner E2EE public-key proof matched across connections. Original Chrome connection remained connected, receiver listening, E2EE ready, and uncertain-operation count unchanged at 3.

The requested group's authoritative album API returned two albums. The first album returned six photos. Its `obsResourceId` was `svc=album, sid=o`, with `resourceType=IMAGE_ORIGINAL`. The pinned reference helper only allowed `a` and `v`; therefore copying its helper unchanged would still fail for this real case. Our bounded adapter now accepts only the observed additional `o` path, keeping endpoint, chat, album and object validation. Download returned 1,326,814-byte JPEG and actual image display succeeded. No customer image, object IDs, messages or credentials are committed.

Additional discovered implementation defects were ours: the initial subprocess exited before large stdout finished flushing; group IDs differed between Chrome and desktop transports; and result-page cursor is `cursor` for album listing. These were fixed with flushed private output, matching two distinct message IDs plus destination across both connections, and real cursor pagination. Neither matching names alone nor notification indexing is used as proof of album ownership.

General lesson: use the reference as a hypothesis, check actual wire shapes and media variants, and prove the end-to-end requested artifact. Preserve existing working connections; separate authentication with explicit owner approval; fail by stage instead of returning an empty successful list. Token rotation is subscribed before login readiness, and verified same-owner state is saved on content failures after clean subprocess exit.


## 2026-11-06: errors resolved with real API evidence

Earlier failure records above are historical, not the current diagnosis.
- Note code500 was an endpoint contract error: pinned reference `listPost` used legacy `/mh/api/v57/post/list.json`. The same reference's interaction methods showed the `/ext/note/nt` namespace. A bounded A/B read proved the latter + POST/x-lhm GET returns code0. The tested home explicitly reports postCount0; omitted posts is accepted only with that authoritative zero. This is not evidence of a nonempty Note read.
- The historical video envelope decrypted correctly. Blob verification failed because the SDK only checked direct-ciphertext HMAC; the actual upload uses HMAC of SHA256 hashes of 128KiB chunks. Public Chrome extension3.7.2 independently confirms the format. Added authentication of either verified layout before AES-CTR decryption; corrupt/tag-mismatched bytes never reach decryption. Recovered767210-byte MP4: full ffmpeg decode passed, ffprobe H264/AAC and7.442222s, actual frame inspected.
- SDK `get_messages_by_ids` describes its arguments as inferred/unverified and failed on the account. Known `getPreviousMessagesV2WithRequest` contract works. Added bounded backwards pages and resumable timestamp/message-ID cursor, including actual decimal-string timestamp decoding. An inaccessible page remains explicit failure, not “no media”.
- Independent review caught SDK `updatePost` replacing attached content with empty defaults. Preserve existing content/style and bind a fresh content/version snapshot before execution. Only replacement text and obsolete text-offset metadata change. The server exposes no verified conditional write primitive, so the small race after preflight is not claimed atomic.

## Reusable integration method

1. Separate capability absence, wrong device/authentication, wrong route/headers, wrong response schema and crypto format. Name the failing phase using sanitized codes; do not label all failures “unsupported”.
2. Read the implementation, not just method names or a comparison answer. Track declared/inferred vs actual protocol contracts. Follow adjacent working operations and official/public client behavior when a wrapper is stale.
3. Keep a bounded hypothesis matrix. Compare one changed contract at a time against the authorized real read path. After two same-class failures change the investigation, not just retry.
4. Prove the actual requested output (original bytes, media decode/visual inspection, authoritative list), then preserve that wire shape in synthetic fixtures. Do not commit real conversation payloads, credentials or images.
5. Trace data preservation as well as HTTP success. Third-party convenience defaults can erase content. Scope reads/writes, retain the UUID ledger and treat unknown results as unknown; never auto-resend.
6. Deploy the reviewed immutable build and repeat through the user's stable interface. A local helper succeeding is not proof that the installed tool was upgraded.

These steps apply to other integrations with opaque IDs, session-specific authority and stale SDKs; the exact LINE endpoints and chunk size are specific to this implementation.


## 2026-11-06 additional-device recovery and API adoption

Observed: saved DESKTOPMAC additional state began failing at `ready/getProfile` with LINE code8 (`NOT_AUTHORIZED_DEVICE`). A completed QR exchange was followed by the same failure in a fresh worker. One attempted token refresh did not recover that state; the code8 refresh fallback was removed because the pinned SDK refresh contract applies to MUST_REFRESH_V3_TOKEN, not arbitrary device rejection. There is no proven credential serialization defect.

The owner then quit the native Mac LINE app, without logging out on the phone. A subsequent owner QR flow, fresh-process restore, album read and repeated OpenChat reads succeeded. This is evidence of recovery under that condition; competing desktop sessions are a hypothesis, not a proven LINE policy or a controlled causality result. Reopening native LINE concurrently has not been tested. The original Chrome receiver remained separate.

The repair now checks saved-state reuse in a fresh worker before `additional_connection_start` reports connected. `additional_connection_check` verifies that separate lane without QR/relogin, writes or retrying sends. Safe diagnostics retain stage, closed error kind, numeric LINE/HTTP codes and time; raw errors/credentials never leave the private adapter. A device-rejected code8 reports that owner authentication is required instead of trying repeated token refreshes.

OpenChat reference adoption: actual getJoinedSquares worked; the tested getJoinedSquareChats route did not. The pinned CLI itself derives chat entries from membership events, so the supported read tool labels those entries as event-derived and non-exhaustive. Details/members are read only for recent owner-bound references. Message history keeps syncToken and continuationToken separately behind a scoped opaque cursor; independent review caught the first implementation's loss of the page token, and contract tests now cover that case and malformed targeted event payloads. Actual empty event pages do not prove nonempty projection or complete history.

VOOM/birthday: the pinned status/template implementations returned application code500. Two transport/routing compatibility attempts did not fix them. Those experimental routes/tools were removed from the runtime candidate rather than exposed as working. No social writes were attempted; account/device compatibility versus changed upstream contract remains unresolved. Do not perform a third identical routing retry or infer an outage from these responses.

Reusable method: separate QR completion, persisted connection reuse, API contract success and content completeness. Test each boundary in that order. Compare request fields against the actual pinned response/request schemas; a cursor with a plausible name is not interchangeable with another cursor. Preserve negative evidence, and do not turn absent/malformed payloads into a successful empty page.

## 2026-11-06: coexistence and end-to-end contract lessons

Following explicit owner approval, a fresh IOSIPAD additional session was authenticated and proved against the existing account. Saved-state reuse succeeded. The owner then logged into native Mac LINE, its actual conversation UI was observed, and both additional connection and original Chrome receiver still passed live checks. This establishes observed coexistence for this setup; it does not establish a universal provider policy. Reauthentication now preserves the saved device method unless the owner explicitly selects another. Earlier desktop-session recovery statements above are historical.

Independent review of newly added Square writes caught two mock assumptions: the real successful send response is createdSquareMessage, and the pinned i64 serializer needs a number/BigInt, not a decimal string. Corrected both and exercised the actual serializer with a large revision. Passing hand-authored mocks alone had missed them. Call-link update likewise requires targetAttribute:{title}, not the upstream CLI string cast.

Error preservation was verified through the final operation result, not just the adapter: AdditionalFailure/ContentFailure previously lost their reason at the service boundary. Closed reason mapping now preserves LIFF consent-needed and target-changed diagnostics while stripping arbitrary exception content. Failure after execution begins remains unknown, never safe-to-retry by inference.

Use these lessons for other integrations: test the pinned serializer and actual response envelope, preserve identity namespaces and participant authority, verify diagnostics across every wrapper, and confirm successful saved-state reuse after another ordinary client logs in. Generalize the procedure, not this service's endpoint/device constants.

## Cover contract and unresolved VOOM (2026-11-06)

Completion for the cover delta: own-bound staged image through upload/association/readback, unknown-result reconciliation and no story publication. Evidence: actual own-cover read, exact API-shaped regression, independent contract/security review and installed-tool read. Do not ship false applied success, authority bypass or lost intended upload identity. Exception owner: Ao. Rollback: previous immutable code only; preserve action/credential/store data. Actual profile restoration is a separate owner-approved mutation and is not claimed tested.

Primary contract: https://github.com/DeachSword/CHRLINE/blob/3ee320668e2da6fc04b50e2054b880e86d23434d/CHRLINE/timeline.py#L175 and object.py#L111 (BSD-3-Clause notice shipped). Exact own read POST /hm/api/v1/home/cover.json with x-lhm GET and existing working Note headers returned HTTP200/code0/coverObsInfo namespace c. Implemented module preflight also passed. This establishes the current read and namespace, not live mutation. Reference LINEJS myhome/h upload alone was insufficient. Write uses myhome/c with existing timeline channel, storySharefalse, verifies reread equality. Intended upload ID is persisted in the encrypted operation before sending; an unknown outcome can be compared to old/new state without another write.

VOOM status remained HTTP200/application500 even after one evidence-driven check using the working Note host/header family, correct9950 channel, IOSIPAD and physicalGET. Prior variants are recorded separately. No experimental VOOM command is installed and no response is replaced with empty success. More blind routes/reauthentication are not justified; current upstream contract/account eligibility evidence remains needed. Birthday reads likewise not established.
