"""Agent-operated MCP client for a fresh packaged connection, never direct LINE."""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def request(root, account, request_id, kind=None, payload=None, *, status_only=False):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "line_agent", "--root", str(root), "--account", account, "mcp",
              "--confirmation", "owner-delegation"])
    stage = "mcp_start"
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write,
                read_timeout_seconds=timedelta(seconds=180)) as client:
            stage = "mcp_initialize"
            await client.initialize()
            # No elicitation callback, fake context, or direct SDK fallback.
            name = "operation_status" if status_only else "operation_run"
            args = {"request_id": request_id}
            if not status_only:
                args.update(kind=kind, payload=payload)
            stage = "mcp_call"
            result = await client.call_tool(name, args)
            if not result.structuredContent:
                raise ValueError("missing structured result")
            return result.structuredContent
    except Exception:
        # The logical request ID is kept by the caller; query it, never
        # issue a replacement ID after an interrupted external operation.
        return {"ok": False, "error": "mcp_result_unavailable", "executed": None,
                "stage": stage, "request_id": request_id, "retry_safe": False,
                "next_action": "operation_status"}


def run_request(root, account, request_id, kind=None, payload=None, *, status_only=False):
    return asyncio.run(request(root, account, request_id, kind, payload, status_only=status_only))
