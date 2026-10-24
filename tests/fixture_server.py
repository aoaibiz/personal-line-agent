"""Explicitly synthetic stdio transport fixture; never used by the shipped CLI."""

import os
from pathlib import Path

from test_service import FakeLine

from line_agent.server import create_server
from line_agent.service import AgentService
from line_agent.store import Store

store = Store(Path(os.environ["TEST_STORE"]), os.environ["TEST_KEY"].encode())
create_server(AgentService(store, FakeLine(int(os.environ.get("TEST_CONTACT_COUNT", "4000")))),
              confirmation=os.environ.get("TEST_CONFIRMATION", "mcp-host")).run()
