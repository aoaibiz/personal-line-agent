# Security policy

## Reporting

Use this repository's **Security → Report a vulnerability** for private reports. If private reporting is unavailable, open an issue containing only a request for a private reporting channel. Do not post exploit details, message contents, LINE identifiers, QR/PIN information, tokens, backup files or encryption keys publicly. No response-time guarantee is currently offered.

## Trust boundaries

- The account owner, the local OS user and the chosen local MCP host are trusted. This is a personal account tool, not a multi-tenant SaaS isolation boundary. A malicious program running as the same OS user is outside this boundary.
- The local stdio owner client can use all eight implemented write actions by default. Owner restrictions override that capability. Granting this host access is significant: it can read private conversations and carry out owner-directed actions.
- Incoming LINE content and model output are untrusted. The optional background inference process is tool-disabled and produces drafts; it does not inherit the interactive owner's execution authority.
- Remote HTTP uses expiring account-bound credentials and origin/resource checks. Local owner delegation is not transferable to HTTP. A production remote deployment is not part of this release's verified setup.
- Owner-delegation restrictions/revocation control that authority. Separately approved legacy actions and template policies have separate lifetimes; revoke them too when ending all authorization for a target. `agent_pause` is the common stop. Do not describe delegation revocation alone as a global account shutdown.
- Action execution checks identity, pause/revocation, restrictions and conversation freshness before an atomic state transition. Unknown results require reconciliation, not blind retry. Group membership checks and the remote write are not a distributed atomic transaction.

## Storage and disclosure

Account records and staged media use encrypted local storage. OS keychain access protects the encryption key. Back up both data and the required recovery material through an owner-controlled process; key migration is not provided by this source preview.

The tools intentionally return private data to the owner's selected AI host when requested. Encryption at rest does not prevent that host/provider from receiving tool results. The optional background Codex integration separately requires recorded consent for the configured source. Do not connect an untrusted AI host.

Diagnostics avoid full upstream responses and private exception contents. Public reports should use operation stage/reason, package version and redacted metadata. Never attach the account directory.

## Verification scope

Version 0.4.1 fixes executable search-path hijacking (CWE-426): an executable
planted in the owner's working directory could be selected by Windows command
lookup. OS utilities now use the OS-reported system directory; uv, Node and Codex
are resolved from absolute owner-configured PATH directories and passed as absolute
paths. Empty and relative PATH entries are ignored. Owner-selected absolute paths
and their installed binaries remain trusted configuration. Updating only the selected
backend leaves an older fixed entry or supervisor installed; update those callers too,
as described in [Windows setup](docs/WINDOWS.md).

See [docs/STATUS.md](docs/STATUS.md) for dated checks and exclusions. Automated checks and source review do not establish immunity to unknown vulnerabilities or guarantee availability of the external LINE service. The pinned protocol dependency is not covered by the PyPI advisory database and has not received a full independent cryptographic audit here.
