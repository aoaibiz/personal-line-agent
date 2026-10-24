# Development

Use synthetic accounts and fake network adapters for tests. The default tests must not log into LINE or send anything to a real recipient.

```sh
uv sync --frozen
uv run --frozen pytest
uv run --frozen ruff check src tests
uv build --offline
uv run --frozen pip-audit --progress-spinner off
```

These are internal developer/agent commands, not end-user onboarding instructions. Review the actual source and dependencies before connecting a real account. Real writes require the owner's instruction and a confirmed target. Never put real account data into tests, screenshots, issues, commits or release assets.

Before changing action execution, add regressions for duplicate requests, unknown results, pause/revocation and account/target binding. Keep public errors safe and diagnosable. Document unverified behavior rather than replacing failure with an empty result.

Use a small reversible commit. Verify the built artifact contains only intended source/docs/licenses and no local runtime, credentials, databases, caches or logs.
