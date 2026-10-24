import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_real_two_hop_owner_form_then_url_then_pin(action):
    async def scenario():
        stages = []

        async def owner_ui(ctx, prompt):
            if isinstance(prompt, types.ElicitRequestURLParams):
                stages.append("url")
                assert prompt.url == (
                    "https://line.me/R/au/lgn/sq/SYNTHETIC?secret=SYNTHETIC&e2eeVersion=1"
                )
                return types.ElicitResult(action="accept")
            if "confirm" in prompt.requestedSchema["properties"]:
                stages.append("owner_start")
                return types.ElicitResult(
                    action=action, content={"confirm": True} if action == "accept" else None
                )
            stages.append("pin")
            assert "123456" in prompt.message
            return types.ElicitResult(action="accept", content={})

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_auth_roundtrip.py")), "bridge"],
            env=dict(os.environ),
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write, elicitation_callback=owner_ui) as client,
        ):
            await client.initialize()
            result = await asyncio.wait_for(
                client.call_tool(
                    "authentication_repair", {"request_id": "22222222-2222-4222-8222-222222222222"}
                ),
                10,
            )
            data = result.structuredContent
            assert data and not result.isError, result
            assert (
                data["state"]
                == {"accept": "complete", "decline": "declined", "cancel": "cancelled"}[action]
            )
            assert data["login_invoked"] is (action == "accept")
            assert stages == (
                ["owner_start", "url", "pin"] if action == "accept" else ["owner_start"]
            )
            assert "123456" not in str(result) and "SYNTHETIC" not in str(result)

    asyncio.run(scenario())
