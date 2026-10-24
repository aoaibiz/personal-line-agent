import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize(
    "url,accepted",
    [
        ("line://au/q/SYNTHETIC", True),
        ("https://line.me/R/au/lgn/sq/SYNTHETIC?secret=SYNTHETIC%2BKEY%3D&e2eeVersion=1", True),
        ("https://line.me.evil.invalid/R/au/lgn/sq/SYNTHETIC", False),
    ],
)
def test_bridge_real_protocol_relays_host_ui_and_completion(url, accepted):
    async def scenario():
        prompts, notices = [], []

        async def elicitation(ctx, prompt):
            prompts.append(prompt)
            return types.ElicitResult(action="accept")

        async def message(item):
            if isinstance(item, types.ServerNotification) and isinstance(
                item.root, types.ElicitCompleteNotification
            ):
                notices.append(item.root.params.elicitationId)

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_auth_bridge.py")), url],
            env=dict(os.environ),
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(
                read, write, elicitation_callback=elicitation, message_handler=message
            ) as client,
        ):
            await client.initialize()
            result = await client.call_tool(
                "authentication_repair", {"request_id": "11111111-1111-4111-8111-111111111111"}
            )
            assert result.structuredContent
            assert result.structuredContent.get("state") == (
                "complete" if accepted else "cancelled"
            )
            if accepted:
                assert len(prompts) == 1 and prompts[0].url == url
                assert notices == ["11111111-1111-4111-8111-111111111111"]
            else:
                assert not prompts and not notices
            assert url not in str(result) and "SYNTHETIC" not in str(result)

    asyncio.run(scenario())
