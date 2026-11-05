# Reference adoption coverage

Pinned reference: evex-dev/linejs c0f4a21, package 3.6.4 (MIT). Implementation is not the same as account availability. Existing operation grants are not expanded by adding types.

| Family | This upgrade / decision |
|---|---|
| Albums | Dedicated list, photo page and bounded original download, scoped references; separate encrypted QR connection adapter. Real group list → photo list → original JPEG → visual verification passed, including observed original resource sid=o. |
| Notes | Dedicated list/get and typed create/update/delete. Legacy route returned application code500; fixed `/ext/note/nt` route returns code0 and authoritative postCount0 for the tested group. Text updates retain attachments/styles; fresh content/version participates in preflight. No arbitrary API exposed. Nonempty Note reads and live writes remain unverified. |
| Reactions, read, unsend | Ten total additional write kinds include reaction/cancel, read marker and own-message unsend, with existing UUID ledger and context preflight. |
| Contacts / group membership | Favorite/mute/hide/block/unblock, kick and leave use existing typed operation pipeline. Existing target/kind grants remain unchanged. |
| Media | General received image/video/audio/file retrieval with private output, bounded size and historical-ID lookup. Historical pagination and authenticated chunk-hash video decryption fixed; the previously failing video was downloaded, fully decoded and visually inspected. Existing outbound media remains. |
| Normal sends / profile | Typed sticker, location, contact-card sends and owner profile name/status edits; own-profile read. Same UUID ledger, recipient grants, contact-card participant scope and fresh preflight. |
| Discovery / JSON | `capabilities_list` and CLI `capabilities` (without account initialization) expose current schemas and operation types; no arbitrary RPC or shell passthrough. |
| Events | Preserve existing durable event queue, filtered group routing and body-free host notices. Do not replace these with the reference's volatile process-local delivery. |
| Storage / auth | Preserve encrypted account store/keychain. Additional connection has its own record/lock; no token-export or unrestricted storage interface. |
| Existing normal functions | Contact/group reads, message/history sync, send/reply/mention/media, memory, corrections, drafts already exist; do not duplicate them. |
| OpenChat reads | Joined communities, event-derived chat entries, details, bounded member pages and bounded history-event pages implemented. Separate namespace and 15-minute owner-bound references; no send/join/listener added. Actual community/details/member reads passed; nonempty message projection remains unverified. |
| VOOM/birthday | Actual pinned read routes returned application code500; experimental compatibility changes failed and were removed from the active tools. Unresolved upstream/account compatibility, not an empty result or successful adoption. |
| OpenChat writes, calls, LIFF, pin/BGM, profile images/background, additional Note interactions | Not yet integrated in this candidate. Separate identity/recipient/write contracts and tests are required; normal LINE group authority cannot be assumed for Square identities or social publication. These remain outstanding against the broader request, not silently counted as adopted. |
| Raw API / credential token / storage export | Intentionally not exposed. These bypass the owner's typed authority and secret boundary and are not a safe improvement. |
| Signed external webhook | No destination configured or delivery added by this candidate. Existing local host queue remains; a generic external webhook is still outstanding if required. |

No claim is made that this candidate fully satisfies every feature in the reference CLI.
