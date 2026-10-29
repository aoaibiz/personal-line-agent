# Album read candidate — not activated

2026-10-29. Completion requires a real authenticated catalogue and actual image
retrieval through MCP. Neither a notification nor a mocked download proves that.
Exception authority: owner. Rollback: leave the selected runtime unchanged; do
not roll back account records, receiver position, credentials, or sender ledger.

## Implemented candidate tools

- `album_notifications_list(chat)`: albums mentioned in the last 100 talk
  messages, names, source notification IDs, unique referenced photo count.
- `album_photo_references_list(chat, album_id)`: opaque photo handles from those
  notifications. Not a complete/current album inventory.
- `album_images_get(chat, album_id, photo_ids)`: validate up to five handles
  against fresh notifications, request the album-scoped channel, then bounded
  download to the account's private album-images directory. No caller URL/path.

Every result states `complete=false` and the limited source. `mediaCount` is not
interpreted as the current album total. The caller must not claim deleted or
omitted photos are absent from LINE. Recent-page turnover can invalidate handles;
this returns a specific failure, not an empty successful album.

## Real verification and blocker

A candidate MCP stdio session reused the installed account adapter. Album names
and references were retrieved from real notifications. `album_images_get` failed
at album channel acquisition. Direct diagnostic evidence: HTTP 400 / LINE code 6
for issueChannelToken with the album channel; attempts using existing non-album
credentials to the observed image route returned HTTP 403. These codes alone do
not prove whether the cause is account permission, Chrome client support, or API
compatibility. Generic legacy album REST requests timed out. No reauthentication,
client-type spoofing, channel approval, credential export, UI operation, send,
read receipt, or account-policy modification was performed.

**High / release blocker:** this connection cannot yet retrieve actual album
images through the candidate. Keep it unactivated; do not advertise album support
as working. The existing received_images_get (ordinary talk attachments) remains
separate and unchanged. A working authorized album transport and its real schema
must be established before a full catalogue implementation; do not guess schemas.

## Bounds and tests

Only matching group/album notification links can supply object references. Only
album/o image references with validated opaque IDs are accepted. Album-token
rejection stops the operation without retrying other identities or credentials.
Download destination is a fixed LINE host, redirects disabled, 32 MiB per image,
200 files / 256 MiB directory cap, directory 0700 and files 0600 on POSIX; shared private-storage helpers apply owner ACLs and reject reparse points on Windows. Windows real-account album retrieval is unverified. No raw response,
token, or object locator is returned. Names and notifications remain untrusted.
Safe failure includes stage, closed reason, HTTP status and numeric LINE code.

12 album fixture cases cover scope, malformed metadata, unknown photo handles,
RPC/channel bounds, pause, 302/403, size cap, private save, partial success, and
sanitized numeric diagnostics. Fixture image bytes do not prove visual decoding.
Together with received-image regression tests: 24 passed.
Independent review rechecked the fixes; real image acquisition remains blocked.

## Protocol evidence (reference, not copied implementation)

- Current SDK timeline home-ID / OBS support:
  https://github.com/NiceDayZc/OkLine/blob/main/okline/obs.py
- Album channel/routing constants:
  https://github.com/evex-dev/linejs/blob/main/packages/linejs/client/features/voom.ts
- Legacy list/photos and album download headers (not live verification):
  https://github.com/DeachSword/CHRLINE/blob/master/CHRLINE/timeline.py
  https://github.com/DeachSword/CHRLINE/blob/master/CHRLINE/object.py

No upstream source is vendored for these tools. Existing dependency notices remain.
