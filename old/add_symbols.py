#!/usr/bin/env python3
import json, sys; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()

for sym in ["XAUUSD", "EURUSD"]:
    try:
        print(f"Adding {sym}...")
        r = c.call_tool("add_marketwatch_symbol", {"symbol": sym})
        print(f"  {json.dumps(r, indent=2)}")
    except Exception as e:
        print(f"  ERROR: {e}")