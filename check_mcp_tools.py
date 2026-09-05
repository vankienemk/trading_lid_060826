#!/usr/bin/env python3
import json, sys; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()
for t in c.list_tools():
    print(f"  {t['name']}")