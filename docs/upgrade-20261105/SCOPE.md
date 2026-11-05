# LINE capability upgrade — 2026-11-05

- Completion: compare pinned LINEJS source against current backend, implement applicable missing capabilities through existing owner authority and operation ledger, activate reviewed runtime, publish GitHub source and document operational learning.
- Evidence: actual authorized album/note read flow first, scoped SDK/API contract tests, existing regression suite, independent correctness/security review, post-activation connection/runtime checks. Record unavailable upstream functionality explicitly.
- Exclusions: no credential export, raw arbitrary API exposure, device identity impersonation, data/ledger rollback, unsolicited writes, or retry of uncertain operations. Feature implementation does not authorize real-account destructive/write tests.
- Exception owner: Ao.
- Rollback: reactivate previous immutable backend; retain account DB, keys, receiver cursor and operation ledger.

## Sources
- Our baseline: 6081561a7a1c71281f10402be7c91bc734ac47a5 (0.5.1).
- Existing album candidate: 1ea30ea56114a66f6c2980ede9e729f79bd9d015 (PR 4).
- LINEJS: c0f4a21a374af677b1f7d1955a1f228e80779643, MIT. https://github.com/evex-dev/linejs
- Comparison supplied by owner is a hypothesis, not execution evidence. Remote comparison file unavailable: connected CoS endpoint returned connection failure.

## Before change
Formal connection_check: integrity OK, identity saved, connected, E2EE ready, receiver listening, paused false, uncertain_operations 3. Existing uncertain operations must not be retried.
