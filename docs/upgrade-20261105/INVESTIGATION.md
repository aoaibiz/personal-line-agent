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
