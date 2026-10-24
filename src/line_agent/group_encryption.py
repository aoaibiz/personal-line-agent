"""Bounded extension-compatible group-key negotiation, never message replay."""

from .diagnostics import DiagnosticError, diagnostic_error


def install(api):
    """The pinned SDK retries send rejection 99, but misses registration's 99.

    Its bundled ltsmSandbox.js registerGroupKey recursively regenerates on that
    exact server rejection. Bound that recursion to four registration attempts.
    Transport/other failures are never retried. No message is sent here.
    Receive/history paths still replace this method with their write blocker.
    """
    from okline.exceptions import LineApiError

    original = api.e2ee.register_group_key

    def register(group):
        for attempt in range(4):
            try:
                return original(group)
            except LineApiError as error:
                if error.code == 99 and attempt < 3:
                    continue
                safe = diagnostic_error("group_key_register", error)
                # Preserve the exact failing phase and prevent an outer SDK
                # send retry from multiplying an exhausted registration budget.
                raise DiagnosticError("group_key_register", safe.reason,
                    http_status=safe.http_status, line_code=safe.line_code) from None

    api.e2ee.register_group_key = register
