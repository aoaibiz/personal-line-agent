# OpenChat completion — 2026-11-06

Reference: LINEJS CLI square.ts at c0f4a21a374af677b1f7d1955a1f228e80779643. This is a mapping of all22 commands in that file, not a claim that every LINE feature or every server-side write has been exercised.

| Reference command | Installed typed equivalent | Evidence / boundary |
|---|---|---|
| list | openchat_list communities | Actual joined list54 |
| get | openchat_read community | Actual detail read |
| chats | openchat_list chats | Membership-event projection, bounded pagination; not complete historical membership |
| chat-get | openchat_read chat | Actual detail read |
| chat-members | openchat_read members | Actual member read;100/page |
| chat-send | square_send_text | Typed SDK contract/ledger tests; live writes await owner instruction |
| chat-media | square_send_media | Private staged image/video/audio;25MiB; actual isolated Python→Node frame test |
| chat-rename | square_rename_chat | Fresh ADMIN/CO_ADMIN role |
| chat-invite | square_invite |1–20 unique normal canonical user IDs; separate invitee scope; partial is not complete |
| chat-leave | square_leave_chat | Revision preflight; no goodbye message |
| join | square_join | Canonical community ID, display name, optional passcode/message; pending is not joined |
| leave | square_leave | Fresh joined community proof |
| create | square_create | Saved owner plus separate allow_square_create grant; searchable false by default |
| search | openchat_search | Official public page read40 results; first page only; failure is not empty success |
| events | openchat_read messages |100-event backward page with distinct sync/continuation tokens; not full chat history |
| message-data | openchat_media_get | Recent100-event message proof, fixed OBS download,25MiB, private0600 output, quota200files/256MiB |
| react | square_react | Typed reaction enum and exact SDK request sequence |
| rename | square_rename | Community ADMIN/CO_ADMIN proof |
| message-reply | square_send_text.reply_to | Recent message provenance |
| message-read | square_mark_read | Recent message proof |
| message-unsend | square_unsend | Only own recent message |
| message-destroy | square_destroy_message | ADMIN/CO_ADMIN and recent message proof |

## Search → identity → operation

Authenticated searchSquares returned Square501; findSquareByEmid with public emid returned Square400. Neither response is converted into an empty result. Official https://openchat.line.me/jp/search supplies bounded public results and cover links. Public emid is not a canonical s/m MID.

openchat_resolve_public requires a fresh owner-bound search reference. It fetches only that official cover URL (no redirect, account headers or cookies;4MiB limit), requires the same invitation ticket in the official LINE href and structured Nuxt payload, then invokes the pinned findSquareByInvitationTicketV2 request. Actual public search→cover→canonical Square response succeeded. Ticket is not returned/logged/stored as a new public field. Only allowlisted Square identity fields are exposed. No join or authority grant occurs during resolution. A later operation still requires explicit owner authority and fresh account/membership context.

## Activation versus live mutation

Implementation, schemas and runtime registration are completed by this candidate. Existing operation grants are not expanded. The operator uses the existing approval/delegation mechanism when the owner asks for a particular operation. No unsolicited invitations, joins, sends, removals, profile changes or deletions were performed as a test. Their server-side acceptance remains unverified until the corresponding authorized use. Unknown outcomes keep the original request UUID and are never retried under a new UUID.

The additional IOSIPAD connection and original Chrome receiver are preserved. Code rollback selects the previous immutable runtime; never roll back account DB, keys, receive position or send ledger.
