"""Synthetic downstream; exercises real upstream MCP elicitation transport only."""

import asyncio
import sys
from pathlib import Path

from mcp import types

from line_agent import bridge

bridge.selected = lambda _: {"current": "a" * 64}


async def fake_backend(*args, elicitation_callback=None):
    if args[4] == "list_tools":
        return types.ListToolsResult(
            tools=[types.Tool(name="authentication_repair", inputSchema={"type": "object"})]
        )
    if elicitation_callback is None:
        return types.CallToolResult(content=[], structuredContent={"state": "unsupported"})
    reply = await elicitation_callback(
        None,
        types.ElicitRequestURLParams(
            mode="url",
            message="Synthetic fixture only",
            url=sys.argv[1] if len(sys.argv) > 1 else "line://au/q/SYNTHETIC",
            elicitationId="11111111-1111-4111-8111-111111111111",
        ),
    )
    return types.CallToolResult(
        content=[],
        structuredContent={
            "state": "complete" if reply.action == "accept" else "cancelled",
            "request_id": "11111111-1111-4111-8111-111111111111",
        },
    )


bridge.invoke = fake_backend
asyncio.run(bridge.serve(Path("/unused"), Path("/unused"), "test"))
