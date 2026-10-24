"""Synthetic two-process authentication transport; never opens a LINE account."""

import asyncio
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from mcp import StdioServerParameters
from mcp.server.fastmcp import Context, FastMCP

from line_agent import authentication, bridge, runtime


class MemoryStore:
    def __init__(self):
        self.rows = {("setting", "identity"): {"mid": "SYNTHETIC"}}

    def get(self, kind, key):
        return self.rows.get((kind, key))

    def put(self, kind, key, value):
        self.rows[kind, key] = value

    def exclusive(self, *args, **kwargs):
        return nullcontext()


def login(on_qr, on_pin, **kwargs):
    on_qr("https://line.me/R/au/lgn/sq/SYNTHETIC?secret=SYNTHETIC&e2eeVersion=1")
    kwargs["confirmation_gate"](wait=True)
    on_pin("123456")
    kwargs["confirmation_gate"](wait=True)
    return {"connected": True, "e2ee_ready": True}


if sys.argv[1] in {"backend", "backend-timeout"}:
    if sys.argv[1] == "backend-timeout":
        original_wait_for = asyncio.wait_for

        async def shortened_wait_for(awaitable, timeout):
            return await original_wait_for(awaitable, 0.05 if timeout == 90 else timeout)

        asyncio.wait_for = shortened_wait_for
    server = FastMCP("synthetic-auth-only")
    service = SimpleNamespace(store=MemoryStore(), line=SimpleNamespace(login=login))

    @server.tool()
    async def authentication_repair(request_id: str, ctx: Context) -> dict[str, object]:
        return await authentication.repair(service, request_id, ctx)

    server.run(transport="stdio")
else:
    # Replace only release lookup and executable destination. invoke(), both
    # stdio sessions, relay(), and repair() are the production implementations.
    bridge.selected = lambda _: {"current": "a" * 64}
    runtime.release_path = lambda *_: Path("/unused")
    runtime.StdioServerParameters = lambda **_: StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).absolute()), "backend"],
        env=dict(os.environ),
    )
    asyncio.run(bridge.serve(Path("/unused"), Path("/unused"), "test"))
